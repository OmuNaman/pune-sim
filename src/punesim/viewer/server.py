"""V2 map viewer backend: FastAPI over the event log.

Read side: everything is a projection — people and places re-synthesized from
the seed (D0 is a pure function), positions inferred from trip/activity
events, panels folded from the log. The operator sees full canon.

Write side (V2): two carefully-scoped doors. /api/interview runs the premium
time-bubble against the SAME db (the conversation becomes canon, like the
CLI), and /api/compile turns free operator text into a grounded, validated
injection saved as a runnable scenario — it never mutates the current log,
because injections belong to runs, not to finished histories. After any
write, the snapshot is rebuilt so the UI sees it immediately; /api/reload
does the same for a db that is still being written by a live run.
"""

import threading
from dataclasses import dataclass
from pathlib import Path

import orjson
from fastapi import FastAPI
from fastapi.responses import FileResponse, ORJSONResponse
from pydantic import BaseModel

from ..kernel.log import EventLog
from ..kernel.timebase import SECONDS_PER_DAY, to_datetime
from ..population import synthesize
from ..world.block import Block

STATIC = Path(__file__).parent / "static"

_ROUTINE = {"trip.start", "trip.end", "activity.start"}


@dataclass
class _Seg:
    t0: int
    t1: int  # exclusive; last segment open-ended
    kind: str  # 'at' | 'transit'
    a: str  # place id (at) or origin (transit)
    b: str | None  # destination (transit)
    activity: str | None


def _humanize(e, names: dict[str, str], places: dict[str, str]) -> str:
    p = e.payload

    def nm(x):
        return names.get(x, places.get(x, x or "?"))

    t = e.type
    if t == "trip.start":
        return f"{nm(p.get('person'))} sets off from {nm(p.get('from'))} to {nm(p.get('to'))} ({p.get('purpose', '')})"
    if t == "trip.end":
        return f"{nm(p.get('person'))} arrives at {nm(p.get('at'))}"
    if t == "activity.start":
        return f"{nm(p.get('person'))}: {p.get('activity')} at {nm(p.get('at'))}"
    if t == "message.sent":
        return f"{nm(p.get('sender'))} → {', '.join(nm(r) for r in p.get('recipients', []))}: {p.get('text', '')}"
    if t == "memory.formed":
        return f"{nm(p.get('person'))} will remember: {p.get('summary', '')}"
    if t == "mood.delta":
        d = p.get("delta", 0)
        return f"{nm(p.get('person'))}: {p.get('dim')} {'+' if d >= 0 else ''}{d}"
    if t == "hazard.road.collision":
        who = ", ".join(nm(x) for x in p.get("participants", []))
        return f"Road accident at {nm(p.get('place'))} — {who}"
    if t == "ambulance.dispatched":
        return f"Ambulance reaches {nm(p.get('place'))}"
    if t == "hospital.admitted":
        return f"{nm(p.get('person'))} admitted at {nm(p.get('place'))}"
    if t == "condition.set":
        return f"{nm(p.get('entity_id'))}: {p.get('kind')} (intensity {p.get('intensity')})"
    if t == "scene.morning":
        return f"Morning scene — {p.get('household')}"
    if t == "scene.reaction":
        return f"The {p.get('household')} household reacts"
    if t == "conversation.held":
        return f"{nm(p.get('person'))} spoke with a journalist"
    if t == "plan.revised":
        return f"{p.get('household')} changes today's plans"
    if t == "info.heard":
        claim = p.get("claim", {})
        how = {"witness": "saw it", "household": "heard at home", "f2f": "heard"}.get(p.get("channel"), "heard")
        src = "" if p.get("source") in ("witness", "origin") else f" from {nm(p.get('source'))}"
        return f"{nm(p.get('person'))} {how}{src}: “{claim.get('text', '')}”"
    if t == "belief.action":
        verb = {"avoid_place": "will avoid", "store_water": "is storing water because of",
                "stop_patronage": "is done with"}.get(p.get("action"), p.get("action"))
        return f"{nm(p.get('person'))} believes the rumor and {verb} {nm(p.get('place'))}"
    if t == "plan.avoided":
        return f"{nm(p.get('person'))} stays home today, avoiding {nm(p.get('place'))}"
    if t == "pressure.crossed":
        dim = {"p_health": "health", "p_financial": "money"}.get(p.get("pressure"), p.get("pressure"))
        return f"{nm(p.get('person'))}'s {dim} worries are mounting"
    if t == "hospital.discharged":
        return f"{nm(p.get('person'))} discharged from {nm(p.get('place'))} — bill ₹{int(p.get('bill') or 0)}"
    if t == "money.paid":
        return f"{p.get('household', '?')} pays ₹{int(p.get('amount') or 0)} ({p.get('reason', '')})"
    if t == "loan.taken":
        return f"{p.get('household', '?')} borrows ₹{int(p.get('principal') or 0)} from the moneylender"
    if t == "loan.interest":
        return f"{p.get('household', '?')}: interest ₹{int(p.get('amount') or 0)} added, ₹{int(p.get('outstanding') or 0)} outstanding"
    if t == "police.fir.registered":
        return f"{nm(p.get('complainant'))} registers an FIR at {nm(p.get('station'))}: “{p.get('statement', '')}”"
    if t == "fir.update":
        return f"Police: {p.get('status', '')} ({nm(p.get('victim'))}'s case)"
    if t == "scene.skipped":
        return f"(scene skipped for {p.get('household')}: {p.get('reason', '')[:60]})"
    if t == "hazard.water.supply_cut":
        return f"Water supply cut around {nm(p.get('place'))}"
    if t == "hazard.power.outage":
        return f"Power outage around {nm(p.get('place'))}"
    if t == "hazard.fire.small":
        return f"Small fire at {nm(p.get('place'))}"
    if t.startswith("hazard."):
        who = ", ".join(nm(x) for x in p.get("participants", []))
        return f"{t.split('.', 1)[1].replace('.', ' ')} at {nm(p.get('place'))}{' — ' + who if who else ''}"
    if t == "info.rumor":
        claim = p.get("claim", {})
        return f"A rumor starts: “{claim.get('text', '')}”"
    return f"{t}: {orjson.dumps(p).decode()[:120]}"


class _Snapshot:
    """Everything derived from the log at one moment; rebuilt after writes."""

    def __init__(self, db_path: str, block: Block, people: dict):
        self.block = block
        self.people = people
        log = EventLog(db_path)
        self.events = list(log.events())
        self.det_hash = log.determinism_hash()
        log.close()

        self.segs: dict[str, list[_Seg]] = {pid: [] for pid in people}
        cur: dict[str, tuple[str, str | None]] = {pid: (p.home_id, None) for pid, p in people.items()}
        open_t: dict[str, int] = dict.fromkeys(people, 0)
        self.max_t = 0
        for e in self.events:
            self.max_t = max(self.max_t, e.sim_time)
            pid = e.payload.get("person")
            if pid not in self.segs or e.type not in _ROUTINE:
                continue
            if e.type == "trip.start":
                at, act = cur[pid]
                self.segs[pid].append(_Seg(open_t[pid], e.sim_time, "at", at, None, act))
                cur[pid] = (e.payload["to"], e.payload.get("purpose"))
                self.segs[pid].append(_Seg(e.sim_time, -1, "transit", e.payload["from"], e.payload["to"], e.payload.get("purpose")))
                open_t[pid] = e.sim_time
            elif e.type == "trip.end":
                if self.segs[pid] and self.segs[pid][-1].kind == "transit" and self.segs[pid][-1].t1 == -1:
                    self.segs[pid][-1].t1 = e.sim_time
                cur[pid] = (e.payload["at"], cur[pid][1])
                open_t[pid] = e.sim_time
            elif e.type == "activity.start":
                at, act = cur[pid]
                if e.sim_time > open_t[pid]:  # close the labelled span so activities never bleed backward
                    self.segs[pid].append(_Seg(open_t[pid], e.sim_time, "at", at, None, act))
                    open_t[pid] = e.sim_time
                cur[pid] = (e.payload.get("at", at), e.payload.get("activity"))
        for pid, (at, act) in cur.items():
            self.segs[pid].append(_Seg(open_t[pid], self.max_t + SECONDS_PER_DAY, "at", at, None, act))

    def pos(self, pid: str, t: int):
        p = self.people[pid]
        best = None
        for s in self.segs[pid]:
            if s.t0 <= t and (s.t1 == -1 or t < s.t1):
                best = s
        if best is None:
            best = _Seg(0, 0, "at", p.home_id, None, None)
        if best.kind == "at" or best.b is None:
            pl = self.block.get(best.a)
            return (pl.lat, pl.lon, "at", best.a, best.activity) if pl else None
        a, b = self.block.get(best.a), self.block.get(best.b)
        if not a or not b:
            return None
        frac = (t - best.t0) / max(1, (best.t1 if best.t1 > 0 else best.t0 + 600) - best.t0)
        frac = min(max(frac, 0.0), 1.0)
        return (a.lat + (b.lat - a.lat) * frac, a.lon + (b.lon - a.lon) * frac, "transit", best.b, best.activity)


class InterviewBody(BaseModel):
    person_id: str
    question: str
    ghost: bool = False


class CompileBody(BaseModel):
    text: str
    day: int = 0


def create_app(db_path: str, seed: int, n_households: int = 80, cfg=None) -> FastAPI:
    block = Block.load()
    households, people = synthesize(seed, block, n_households=n_households)

    place_names = {p.id: (p.name or p.kind) for p in [*block.places, *block.homes]}
    person_names = {p.id: p.name for p in people.values()}
    hh_members = {h.id: list(h.member_ids) for h in households}

    S = {"snap": _Snapshot(db_path, block, people)}
    write_lock = threading.Lock()

    def _gateway(log=None):
        from ..llm import Cassette, Gateway

        return Gateway(cfg, Cassette(cfg.cassette_path), log=log)

    app = FastAPI(default_response_class=ORJSONResponse)

    @app.get("/api/meta")
    def meta():
        snap = S["snap"]
        lats = [p.lat for p in block.places]
        lons = [p.lon for p in block.places]
        return {
            "seed": seed,
            "people": len(people),
            "households": len(households),
            "events": len(snap.events),
            "days": snap.max_t // SECONDS_PER_DAY + 1,
            "max_t": snap.max_t,
            "hash": snap.det_hash[:16],
            "llm": cfg is not None,
            "bounds": [[min(lats), min(lons)], [max(lats), max(lons)]],
        }

    @app.post("/api/reload")
    def reload():
        with write_lock:
            S["snap"] = _Snapshot(db_path, block, people)
        return {"events": len(S["snap"].events)}

    @app.get("/api/places")
    def places():
        return [
            {"id": p.id, "name": p.name, "kind": p.kind, "lat": p.lat, "lon": p.lon}
            for p in block.places
        ]

    @app.get("/api/people")
    def people_list():
        return [
            {
                "id": p.id, "name": p.name, "age": p.age, "sex": p.sex,
                "occupation": p.occupation, "religion": p.religion,
                "household": p.household_id, "home": p.home_id, "work": p.work_id,
                "work_name": place_names.get(p.work_id, ""),
            }
            for p in sorted(people.values(), key=lambda x: x.id)
        ]

    @app.get("/api/person/{pid}")
    def person(pid: str):
        snap = S["snap"]
        p = people.get(pid)
        if p is None:
            return {"error": "unknown person"}
        memories, moods, lines, interviews, heard = [], [], [], [], []
        for e in snap.events:
            pl = e.payload
            if e.type == "info.heard" and pl.get("person") == pid:
                c = pl.get("claim", {})
                heard.append({
                    "t": e.sim_time, "hm": to_datetime(e.sim_time).strftime("%a %H:%M"),
                    "text": c.get("text", ""), "key": pl.get("claim_key"),
                    "credence": pl.get("credence"), "channel": pl.get("channel"),
                    "source": person_names.get(pl.get("source"), pl.get("source")),
                    "hop": c.get("hop", 0), "ops": c.get("ops", []),
                })
            if e.type == "memory.formed" and pl.get("person") == pid:
                memories.append({"t": e.sim_time, "summary": pl.get("summary"), "salience": pl.get("salience")})
            elif e.type == "mood.delta" and pl.get("person") == pid:
                moods.append({"t": e.sim_time, "dim": pl.get("dim"), "delta": pl.get("delta")})
            elif e.type == "conversation.held" and pl.get("person") == pid:
                interviews.append({
                    "t": e.sim_time, "hm": to_datetime(e.sim_time).strftime("%a %H:%M"),
                    "question": pl.get("question", ""), "answer": pl.get("answer", ""),
                })
            touched = {pl.get("person"), pl.get("sender"), pl.get("entity_id"),
                       pl.get("complainant"), pl.get("victim"),
                       *(pl.get("recipients") or []), *(pl.get("participants") or [])}
            if (pid in touched or pl.get("household") == p.household_id and e.type.startswith(("money", "loan"))) and e.type != "llm.response":
                lines.append({
                    "seq": e.seq, "t": e.sim_time, "day": e.sim_time // SECONDS_PER_DAY,
                    "hm": to_datetime(e.sim_time).strftime("%H:%M"),
                    "type": e.type, "routine": e.type in _ROUTINE,
                    "caused_by": e.caused_by,
                    "text": _humanize(e, person_names, place_names),
                })
        lines.sort(key=lambda x: (x["t"], x["seq"]))
        hh = [
            {"id": m, "name": people[m].name, "age": people[m].age, "occupation": people[m].occupation}
            for m in hh_members.get(p.household_id, [])
        ]
        return {
            "id": p.id, "name": p.name, "age": p.age, "sex": p.sex,
            "occupation": p.occupation, "religion": p.religion,
            "household": p.household_id, "members": hh,
            "home": p.home_id, "home_name": place_names.get(p.home_id, "home"),
            "work": p.work_id, "work_name": place_names.get(p.work_id, ""),
            "memories": memories, "moods": moods, "timeline": lines,
            "interviews": interviews, "heard": heard,
        }

    @app.get("/api/scenes")
    def scenes():
        out = []
        for e in S["snap"].events:
            if e.type in ("scene.morning", "scene.reaction"):
                hid = e.payload.get("household")
                out.append({
                    "seq": e.seq, "t": e.sim_time, "day": e.sim_time // SECONDS_PER_DAY,
                    "hm": to_datetime(e.sim_time).strftime("%a %H:%M"),
                    "kind": e.type, "household": hid,
                    "family": people[hh_members[hid][0]].surname if hh_members.get(hid) else hid,
                    "narration": e.payload.get("narration", ""),
                    "transcript": e.payload.get("transcript", ""),
                })
        return out

    @app.get("/api/ticker")
    def ticker():
        out = []
        for e in S["snap"].events:
            # info.heard is high-volume gossip — it lives in the Rumors tab
            if e.type in _ROUTINE or e.type in (
                "llm.response", "fact.established", "fact.superseded", "info.heard",
                "run.meta",
            ):
                continue
            out.append({
                "seq": e.seq, "t": e.sim_time,
                "hm": to_datetime(e.sim_time).strftime("%a %H:%M"),
                "type": e.type, "provenance": e.provenance,
                "caused_by": e.caused_by,
                "text": _humanize(e, person_names, place_names),
                "place": e.payload.get("place"),
            })
        out.sort(key=lambda x: (x["t"], x["seq"]))
        return out

    @app.get("/api/rumors")
    def rumors():
        """Claim families: origin, hop-by-hop drift, reach, believers, actions.
        The auditable telephone game — every number here folds from the log."""
        snap = S["snap"]
        by_seq = {e.seq: e for e in snap.events}
        fams: dict[str, dict] = {}
        for e in snap.events:
            if e.type == "info.heard":
                pl = e.payload
                c = pl.get("claim", {})
                key = pl.get("claim_key") or c.get("key", "?")
                f = fams.setdefault(key, {
                    "key": key, "first_t": e.sim_time, "origin_type": None,
                    "veracity": c.get("veracity", "unknown"),
                    "subject": place_names.get(c.get("subject"), c.get("subject")),
                    "hearers": {}, "variants": [], "_vseen": {}, "by_day": {},
                    "actions": [], "spread": [],
                })
                f["hearers"][pl.get("person")] = pl.get("credence", 0)
                day = e.sim_time // SECONDS_PER_DAY
                f["by_day"][day] = f["by_day"].get(day, 0) + 1
                text = c.get("text", "")
                if text not in f["_vseen"]:
                    f["_vseen"][text] = True
                    f["variants"].append({
                        "text": text, "hop": c.get("hop", 0), "ops": c.get("ops", []),
                        "first_hm": to_datetime(e.sim_time).strftime("%a %H:%M"),
                    })
                if len(f["spread"]) < 400:
                    f["spread"].append({
                        "t": e.sim_time, "hm": to_datetime(e.sim_time).strftime("%a %H:%M"),
                        "person": person_names.get(pl.get("person"), pl.get("person")),
                        "person_id": pl.get("person"),
                        "source": person_names.get(pl.get("source"), pl.get("source")),
                        "channel": pl.get("channel"), "credence": pl.get("credence"),
                        "hop": c.get("hop", 0),
                    })
                if f["origin_type"] is None:
                    root, hops = e, 0
                    while root.caused_by is not None and root.caused_by in by_seq and hops < 20:
                        root, hops = by_seq[root.caused_by], hops + 1
                    f["origin_type"] = root.type
                    f["origin_prov"] = root.provenance
            elif e.type == "belief.action":
                pl = e.payload
                key = pl.get("claim_key")
                if key in fams:
                    fams[key]["actions"].append({
                        "hm": to_datetime(e.sim_time).strftime("%a %H:%M"),
                        "person": person_names.get(pl.get("person"), pl.get("person")),
                        "person_id": pl.get("person"),
                        "action": pl.get("action"),
                        "place": place_names.get(pl.get("place"), pl.get("place")),
                    })
        out = []
        for f in sorted(fams.values(), key=lambda x: x["first_t"]):
            hearers = f.pop("hearers")
            f.pop("_vseen")
            f["reach"] = len(hearers)
            f["believers"] = sum(1 for c in hearers.values() if (c or 0) >= 0.55)
            f["first_hm"] = to_datetime(f.pop("first_t")).strftime("%a %H:%M")
            f["by_day"] = [{"day": d, "n": n} for d, n in sorted(f["by_day"].items())]
            out.append(f)
        return out

    @app.get("/api/positions")
    def positions(t: int):
        snap = S["snap"]
        out = []
        for pid in people:
            r = snap.pos(pid, t)
            if r is None:
                continue
            lat, lon, state, at, activity = r
            out.append({
                "id": pid, "name": person_names[pid], "lat": lat, "lon": lon,
                "state": state, "at": at, "at_name": place_names.get(at, ""),
                "activity": activity or "",
            })
        return out

    @app.post("/api/interview")
    def interview_endpoint(body: InterviewBody):
        """The 'Ask them something' box: premium time-bubble, becomes canon."""
        if cfg is None:
            return {"error": "no LLM configured — restart serve with a .env key"}
        if body.person_id not in people:
            return {"error": "unknown person"}
        from ..minds.interview import interview as _interview

        with write_lock:
            log = EventLog(db_path)
            try:
                answer = _interview(
                    log, _gateway(log), block, people, body.person_id,
                    body.question, ghost=body.ghost,
                )
            except Exception as e:  # noqa: BLE001 — surfaced to the UI, not a 500
                log.close()
                return {"error": f"interview failed: {e}"}
            log.close()
            S["snap"] = _Snapshot(db_path, block, people)
        return {"answer": answer, "person": person_names[body.person_id]}

    @app.post("/api/compile")
    def compile_endpoint(body: CompileBody):
        """The Inject box: free text -> grounded preview + runnable scenario.
        Never mutates this log — injections belong to runs (branching lands
        next; until then the response carries the exact run command)."""
        if cfg is None:
            return {"error": "no LLM configured — restart serve with a .env key"}
        from ..minds.compiler import CompileError, compile_injection

        with write_lock:
            try:
                out = compile_injection(
                    _gateway(), block, people, body.text, default_day=body.day
                )
            except CompileError as e:
                return {"error": "could not compile", "details": e.errors}
            except Exception as e:  # noqa: BLE001
                return {"error": f"compile failed: {e}"}
        inj = out.injection
        obj = {
            "day": inj.day,
            "time": f"{inj.time_s // 3600:02d}:{inj.time_s % 3600 // 60:02d}",
            "type": inj.type,
            "place": inj.place,
            "participants": list(inj.participants),
            "severity": inj.severity,
            "payload": inj.payload,
        }
        save = Path("runs/injections/ui_compiled.json")
        existing = orjson.loads(save.read_bytes()) if save.exists() else []
        existing.append(obj)
        save.parent.mkdir(parents=True, exist_ok=True)
        save.write_bytes(orjson.dumps(existing, option=orjson.OPT_INDENT_2))
        return {
            "preview": out.preview,
            "narrative": out.spec.narrative,
            "notes": out.spec.notes,
            "injection": obj,
            "saved": str(save),
            "count": len(existing),
            "run_cmd": f"uv run punesim run --days {max(inj.day + 2, 3)} --scenes --inject {save} --db runs/dev/events.db",
        }

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/app.js")
    def appjs():
        return FileResponse(STATIC / "app.js")

    @app.get("/style.css")
    def stylecss():
        return FileResponse(STATIC / "style.css")

    return app
