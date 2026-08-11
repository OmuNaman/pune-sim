"""The reading panels: the ticker, the scenes, the rumour board, the day strip."""

from fastapi import APIRouter, HTTPException, Request

from ...kernel.timebase import SECONDS_PER_DAY, to_datetime
from ..humanize import humanize
from ..readlog import ReadOnlyLog

router = APIRouter()

BELIEVER_CREDENCE = 0.55  # the threshold the V1 exit used for "believes it"
SPREAD_CAP = 400  # hearings sent per rumour; the fold's own numbers stay exact


def _world(request: Request, run_id: str):
    rec = request.app.state.registry.get(run_id)
    if rec is None:
        raise HTTPException(404, f"no run {run_id!r}")
    return rec, request.app.state.worlds.get(run_id, rec.db)


@router.get("/{run_id}/ticker")
def ticker(request: Request, run_id: str, since_seq: int = 0, limit: int = 500,
           day: int | None = None):
    """The log minus movement, gossip and bookkeeping.

    `since_seq` is what makes this the live tail: a client that has seen up to
    seq N asks for what came after, instead of re-reading the run.

    `day` scopes it to one sim-day, and matters more than it looks: without it
    the endpoint returns the LAST n events of the whole run, so a client sitting
    on day 1 of a 30-day log filters a page full of day 29 down to nothing and
    truthfully reports that nothing has happened.
    """
    _rec, w = _world(request, run_id)
    rows = w.view.notable(limit=200_000, day=day)
    if since_seq:
        rows = [e for e in rows if e.seq > since_seq]
    rows = rows[-max(1, min(limit, 5000)):]
    out = []
    for e in rows:
        h = humanize(e, w.person_names, w.place_names)
        out.append({
            "seq": e.seq, "t": e.sim_time, "day": e.sim_time // SECONDS_PER_DAY,
            "hm": to_datetime(e.sim_time).strftime("%a %H:%M"),
            "type": e.type, "provenance": e.provenance, "caused_by": e.caused_by,
            "place": e.payload.get("place"),
            "text": h["text"], "refs": h["refs"],
        })
    out.sort(key=lambda x: (x["t"], x["seq"]))
    return {"items": out, "last_seq": out[-1]["seq"] if out else since_seq}


@router.get("/{run_id}/cone/{seq}")
def cone(request: Request, run_id: str, seq: int, limit: int = 200):
    """Everything one event caused, breadth-first.

    The best idea in the old UI: an injection is a stone, and this is the
    ripple. `caused_by` is a real column, so the chain is the log's own claim
    about causation rather than a guess from timing.
    """
    _rec, w = _world(request, run_id)
    rows = w.view.notable(limit=200_000)
    by_cause: dict[int, list] = {}
    for e in rows:
        if e.caused_by is not None:
            by_cause.setdefault(e.caused_by, []).append(e)
    root = next((e for e in rows if e.seq == seq), None)
    out, frontier, depth = [], [seq], 0
    seen = {seq}
    while frontier and len(out) < limit and depth < 12:
        nxt = []
        for s in frontier:
            for child in by_cause.get(s, ()):
                if child.seq in seen:
                    continue
                seen.add(child.seq)
                h = humanize(child, w.person_names, w.place_names)
                out.append({
                    "seq": child.seq, "t": child.sim_time, "depth": depth + 1,
                    "day": child.sim_time // SECONDS_PER_DAY,
                    "hm": to_datetime(child.sim_time).strftime("%a %H:%M"),
                    "type": child.type, "caused_by": child.caused_by,
                    "text": h["text"], "refs": h["refs"],
                })
                nxt.append(child.seq)
        frontier, depth = nxt, depth + 1
    root_out = None
    if root is not None:
        h = humanize(root, w.person_names, w.place_names)
        root_out = {"seq": root.seq, "t": root.sim_time, "type": root.type,
                    "text": h["text"], "refs": h["refs"]}
    return {"root": root_out, "children": out, "truncated": len(out) >= limit}


@router.get("/{run_id}/scenes")
def scenes(request: Request, run_id: str, day: int | None = None, limit: int = 400):
    _rec, w = _world(request, run_id)
    out = []
    for e in w.view.of_type("scene.morning", "scene.reaction", "conversation.held",
                            limit=100_000):
        d = e.sim_time // SECONDS_PER_DAY
        if day is not None and d != day:
            continue
        pl = e.payload
        if e.type in ("scene.morning", "scene.reaction"):
            hid = pl.get("household")
            members = w.hh_members.get(hid, [])
            out.append({
                "seq": e.seq, "t": e.sim_time, "day": d,
                "hm": to_datetime(e.sim_time).strftime("%a %H:%M"),
                "kind": e.type, "household": hid,
                "family": w.people[members[0]].surname if members and members[0] in w.people else hid,
                "cast": [{"id": m, "name": w.person_names.get(m, m)} for m in members],
                "narration": pl.get("narration", ""), "transcript": pl.get("transcript", ""),
            })
        elif e.type == "conversation.held" and pl.get("participants"):
            who = pl["participants"]
            out.append({
                "seq": e.seq, "t": e.sim_time, "day": d,
                "hm": to_datetime(e.sim_time).strftime("%a %H:%M"),
                "kind": "conversation.held",
                "household": w.people[who[0]].household_id if who[0] in w.people else "",
                "family": " & ".join(w.person_names.get(x, x) for x in who),
                "cast": [{"id": x, "name": w.person_names.get(x, x)} for x in who],
                "narration": pl.get("narration", ""), "transcript": pl.get("transcript", ""),
            })
    out.sort(key=lambda x: (x["t"], x["seq"]))
    return out[-limit:]


@router.get("/{run_id}/rumors")
def rumors(request: Request, run_id: str):
    """Claim families: origin, hop-by-hop drift, reach, believers, actions.

    Ported whole from the old viewer — the analysis was the best thing in it.
    Every number folds from the log; nothing here is a model's opinion.
    """
    _rec, w = _world(request, run_id)
    hearings = w.view.of_type("info.heard", "belief.action", limit=1_000_000)
    by_seq = {e.seq: e for e in hearings}
    fams: dict[str, dict] = {}
    for e in hearings:
        pl = e.payload
        if e.type == "info.heard":
            c = pl.get("claim", {})
            key = pl.get("claim_key") or c.get("key", "?")
            f = fams.setdefault(key, {
                "key": key, "first_t": e.sim_time, "origin_type": None, "origin_prov": "",
                "veracity": c.get("veracity", "unknown"),
                "subject_id": c.get("subject"),
                "subject": w.place_names.get(c.get("subject"), c.get("subject")),
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
            if len(f["spread"]) < SPREAD_CAP:
                f["spread"].append({
                    "t": e.sim_time, "hm": to_datetime(e.sim_time).strftime("%a %H:%M"),
                    "person_id": pl.get("person"),
                    "person": w.person_names.get(pl.get("person"), pl.get("person")),
                    "source_id": pl.get("source"),
                    "source": w.person_names.get(pl.get("source"), pl.get("source")),
                    "channel": pl.get("channel"), "credence": pl.get("credence"),
                    "hop": c.get("hop", 0),
                    "chain": [w.person_names.get(x, x) for x in (pl.get("lineage") or [])],
                })
            if f["origin_type"] is None:
                root, hops = e, 0
                while root.caused_by is not None and root.caused_by in by_seq and hops < 20:
                    root, hops = by_seq[root.caused_by], hops + 1
                f["origin_type"], f["origin_prov"] = root.type, root.provenance
        elif e.type == "belief.action":
            f = fams.get(pl.get("claim_key"))
            if f is not None:
                f["actions"].append({
                    "hm": to_datetime(e.sim_time).strftime("%a %H:%M"),
                    "person_id": pl.get("person"),
                    "person": w.person_names.get(pl.get("person"), pl.get("person")),
                    "action": pl.get("action"),
                    "place_id": pl.get("place"),
                    "place": w.place_names.get(pl.get("place"), pl.get("place")),
                })
    out = []
    for f in sorted(fams.values(), key=lambda x: x["first_t"]):
        hearers = f.pop("hearers")
        f.pop("_vseen")
        f["reach"] = len(hearers)
        f["believers"] = sum(1 for c in hearers.values() if (c or 0) >= BELIEVER_CREDENCE)
        f["first_hm"] = to_datetime(f.pop("first_t")).strftime("%a %H:%M")
        f["by_day"] = [{"day": d, "n": n} for d, n in sorted(f["by_day"].items())]
        f["spread_truncated"] = f["reach"] > SPREAD_CAP
        out.append(f)
    return out


@router.get("/{run_id}/trouble")
def trouble(request: Request, run_id: str, day: int):
    """Where the day went wrong, and where the news of it travelled.

    Two shapes, one call, because the map draws both and asking twice would
    fetch the same day twice. Hazards are points in time and space; hops are
    the arcs between the person who knew and the person who now knows, which is
    the only part of this simulation that is genuinely about the geography of
    talk rather than the geography of walking.
    """
    _rec, w = _world(request, run_id)
    lo, hi = day * SECONDS_PER_DAY, (day + 1) * SECONDS_PER_DAY

    hazards = []
    for e in w.view.of_type(*[t for t in w.view.types()
                              if t.startswith(("hazard.", "unrest.", "crowd.",
                                               "police.", "curfew."))],
                            limit=4000, day=day):
        pl = w.block.get(e.payload.get("place") or "")
        if pl is None:
            continue
        h = humanize(e, w.person_names, w.place_names)
        hazards.append({
            "seq": e.seq, "t": e.sim_time, "type": e.type,
            "hm": to_datetime(e.sim_time).strftime("%H:%M"),
            "lat": pl.lat, "lon": pl.lon, "place": pl.id, "place_name": pl.name,
            "severity": e.payload.get("severity"),
            "text": h["text"],
        })

    # A hop needs both ends placed, and where somebody IS at a given moment is
    # the day's movement — which is already built and cached for the map.
    segs = w.view.segs_for_day(day)

    def where(pid: str, t: int):
        best = None
        for s in segs.get(pid, ()):
            if s.t0 <= t and (s.t1 == -1 or t < s.t1):
                best = s
        pl = w.block.get(best.a if best else (w.people[pid].home_id
                                              if pid in w.people else ""))
        return (pl.lat, pl.lon) if pl else None

    hops = []
    for e in w.view.of_type("info.heard", limit=20_000, day=day):
        pl = e.payload
        src, dst = pl.get("source"), pl.get("person")
        if not src or not dst or src in ("origin", "witness"):
            continue
        a, b = where(src, e.sim_time), where(dst, e.sim_time)
        if not a or not b:
            continue
        # Two people talking face to face are AT THE SAME PLACE — that is what a
        # conversation is. Dropping same-place hops as degenerate threw away
        # every f2f hop in the run and left only the impossible ones. A zero
        # length arc is drawn as a ring instead.
        hops.append({
            "t": e.sim_time, "from": [a[1], a[0]], "to": [b[1], b[0]],
            "same_place": a == b,
            "credence": pl.get("credence"), "key": pl.get("claim_key"),
            "channel": pl.get("channel"),
        })
        if len(hops) >= 2000:   # a busy day at V3 scale is tens of thousands
            break

    return {"day": day, "hazards": hazards, "hops": hops,
            "hops_truncated": len(hops) >= 2000}


@router.get("/{run_id}/days")
def days(request: Request, run_id: str):
    """Per-day event counts by type, for the timeline ribbon.

    Aggregated in SQL. Counting several million rows in Python to draw a
    histogram is the exact shape of mistake logview.py was written to undo.
    """
    rec, _w = _world(request, run_id)
    from ...viewer.logview import _NOT_NOTABLE

    rows = ReadOnlyLog(rec.db).counts_by_day_and_type()
    per_day: dict[int, dict] = {}
    for day, typ, n in rows:
        d = per_day.setdefault(day, {"day": day, "total": 0, "notable": 0, "by_type": {}})
        d["total"] += n
        d["by_type"][typ] = d["by_type"].get(typ, 0) + n
        if typ not in _NOT_NOTABLE:
            d["notable"] += n
    return [per_day[k] for k in sorted(per_day)]
