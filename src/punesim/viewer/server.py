"""V0 map viewer backend: a read-only FastAPI over the event log.

Everything is a projection: people and places are re-synthesized from the seed
(D0 is a pure function), positions are inferred from trip/activity events, and
the god's-eye panels (timeline, scenes, memories) are folds over the log. The
operator sees full canon — disclosure tiers govern prompts, not this UI.
"""

from dataclasses import dataclass
from pathlib import Path

import orjson
from fastapi import FastAPI
from fastapi.responses import FileResponse, ORJSONResponse

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
    return f"{t}: {orjson.dumps(p).decode()[:120]}"


def create_app(db_path: str, seed: int, n_households: int = 80) -> FastAPI:
    block = Block.load()
    households, people = synthesize(seed, block, n_households=n_households)
    log = EventLog(db_path)

    place_names = {p.id: (p.name or p.kind) for p in [*block.places, *block.homes]}
    person_names = {p.id: p.name for p in people.values()}
    hh_members = {h.id: list(h.member_ids) for h in households}

    # --- position segments per person (built once; log is immutable) --------
    segs: dict[str, list[_Seg]] = {pid: [] for pid in people}
    cur: dict[str, tuple[str, str | None]] = {pid: (p.home_id, None) for pid, p in people.items()}
    open_t: dict[str, int] = dict.fromkeys(people, 0)
    max_t = 0
    events_cache = list(log.events())
    det_hash = log.determinism_hash()
    log.close()  # immutable snapshot view; no connection crosses threads
    for e in events_cache:
        max_t = max(max_t, e.sim_time)
        pid = e.payload.get("person")
        if pid not in segs or e.type not in _ROUTINE:
            continue
        if e.type == "trip.start":
            at, act = cur[pid]
            segs[pid].append(_Seg(open_t[pid], e.sim_time, "at", at, None, act))
            cur[pid] = (e.payload["to"], e.payload.get("purpose"))
            segs[pid].append(_Seg(e.sim_time, -1, "transit", e.payload["from"], e.payload["to"], e.payload.get("purpose")))
            open_t[pid] = e.sim_time
        elif e.type == "trip.end":
            if segs[pid] and segs[pid][-1].kind == "transit" and segs[pid][-1].t1 == -1:
                segs[pid][-1].t1 = e.sim_time
            cur[pid] = (e.payload["at"], cur[pid][1])
            open_t[pid] = e.sim_time
        elif e.type == "activity.start":
            at, act = cur[pid]
            if e.sim_time > open_t[pid]:  # close the labelled span so activities never bleed backward
                segs[pid].append(_Seg(open_t[pid], e.sim_time, "at", at, None, act))
                open_t[pid] = e.sim_time
            cur[pid] = (e.payload.get("at", at), e.payload.get("activity"))
    for pid, (at, act) in cur.items():
        segs[pid].append(_Seg(open_t[pid], max_t + SECONDS_PER_DAY, "at", at, None, act))

    def _pos(pid: str, t: int):
        p = people[pid]
        best = None
        for s in segs[pid]:
            if s.t0 <= t and (s.t1 == -1 or t < s.t1):
                best = s
        if best is None:
            best = _Seg(0, 0, "at", p.home_id, None, None)
        if best.kind == "at" or best.b is None:
            pl = block.get(best.a)
            return (pl.lat, pl.lon, "at", best.a, best.activity) if pl else None
        a, b = block.get(best.a), block.get(best.b)
        if not a or not b:
            return None
        frac = (t - best.t0) / max(1, (best.t1 if best.t1 > 0 else best.t0 + 600) - best.t0)
        frac = min(max(frac, 0.0), 1.0)
        return (a.lat + (b.lat - a.lat) * frac, a.lon + (b.lon - a.lon) * frac, "transit", best.b, best.activity)

    app = FastAPI(default_response_class=ORJSONResponse)

    @app.get("/api/meta")
    def meta():
        lats = [p.lat for p in block.places]
        lons = [p.lon for p in block.places]
        return {
            "seed": seed,
            "people": len(people),
            "households": len(households),
            "events": len(events_cache),
            "days": max_t // SECONDS_PER_DAY + 1,
            "max_t": max_t,
            "hash": det_hash[:16],
            "bounds": [[min(lats), min(lons)], [max(lats), max(lons)]],
        }

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
        p = people.get(pid)
        if p is None:
            return {"error": "unknown person"}
        memories, moods, lines, interviews = [], [], [], []
        for e in events_cache:
            pl = e.payload
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
                       *(pl.get("recipients") or []), *(pl.get("participants") or [])}
            if pid in touched and e.type != "llm.response":
                lines.append({
                    "seq": e.seq, "t": e.sim_time, "day": e.sim_time // SECONDS_PER_DAY,
                    "hm": to_datetime(e.sim_time).strftime("%H:%M"),
                    "type": e.type, "routine": e.type in _ROUTINE,
                    "text": _humanize(e, person_names, place_names),
                })
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
            "interviews": interviews,
        }

    @app.get("/api/scenes")
    def scenes():
        out = []
        for e in events_cache:
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
        for e in events_cache:
            if e.type in _ROUTINE or e.type in ("llm.response", "fact.established", "fact.superseded"):
                continue
            out.append({
                "seq": e.seq, "t": e.sim_time,
                "hm": to_datetime(e.sim_time).strftime("%a %H:%M"),
                "type": e.type, "provenance": e.provenance,
                "text": _humanize(e, person_names, place_names),
                "place": e.payload.get("place"),
            })
        return out

    @app.get("/api/positions")
    def positions(t: int):
        out = []
        for pid in people:
            r = _pos(pid, t)
            if r is None:
                continue
            lat, lon, state, at, activity = r
            out.append({
                "id": pid, "name": person_names[pid], "lat": lat, "lon": lon,
                "state": state, "at": at, "at_name": place_names.get(at, ""),
                "activity": activity or "",
            })
        return out

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
