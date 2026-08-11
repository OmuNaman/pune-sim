"""Read endpoints: who lives here, where everything is, where everyone is now."""

from fastapi import APIRouter, HTTPException, Request, Response

from ...kernel.timebase import SECONDS_PER_DAY, to_datetime
from .. import positions as pos_codec
from ..geo import layers_for
from ..humanize import humanize

router = APIRouter()

_ROUTINE = {"trip.start", "trip.end", "activity.start"}

# What a person's own page should carry. The old dossier asked for seven types
# and then tried to build the "their day" timeline out of the same seven, so a
# person's timeline could never contain a trip, a hospital admission, an FIR or
# a rupee — and asked for `interview.answered`, which nothing in this repo has
# ever emitted, while the branch that reads interviews matches
# `conversation.held`, which was never fetched. Both bugs are fixed by asking
# for what the panel actually shows.
_DOSSIER_TYPES = (
    "info.heard", "memory.formed", "mood.delta", "message.sent", "belief.action",
    "pressure.crossed", "conversation.held", "plan.avoided",
    "hospital.admitted", "hospital.discharged", "condition.set",
    "police.fir.registered", "fir.update",
    "hazard.road.collision", "hazard.fire.small",
)
_HOUSEHOLD_TYPES = ("money.paid", "loan.taken", "loan.interest")


def _world(request: Request, run_id: str, *, roads: bool = False):
    rec = request.app.state.registry.get(run_id)
    if rec is None:
        raise HTTPException(404, f"no run {run_id!r}")
    return rec, request.app.state.worlds.get(run_id, rec.db, roads=roads)


@router.get("/{run_id}/meta")
def meta(request: Request, run_id: str):
    """The header line — and deliberately NOT a reason to build a world.

    Synthesizing 49,578 people takes 13 seconds. The page cannot draw anything
    until it knows the bounds and the day count, so making meta wait on the
    roster meant thirteen seconds of blank screen before the first pixel. The
    log's own run.meta answers all of this, and the block is cheap to load
    without the road graph.
    """
    from ...world.block import load_for
    from ..readlog import ReadOnlyLog

    rec = request.app.state.registry.get(run_id)
    if rec is None:
        raise HTTPException(404, f"no run {run_id!r}")
    cache = request.app.state.worlds
    n_events, max_t, max_seq = ReadOnlyLog(rec.db).summary()
    m = cache.meta_only(run_id, rec.db)
    block_name = m.get("block", "kasba")
    households = m.get("households", rec.params.households)
    built = cache.cached(run_id)
    block = built.block if built else load_for(households, block_name, roads=False)
    lats = [p.lat for p in block.places]
    lons = [p.lon for p in block.places]
    live = request.app.state.manager.status(run_id)
    return {
        "id": rec.id,
        "name": rec.name,
        "seed": m.get("seed", rec.params.seed),
        "block": block_name,
        # The population is a pure function of the roster, so its SIZE is known
        # from the log without building it — but only once it is built do we
        # know it exactly, so an unbuilt world reports the households it has.
        "people": len(built.people) if built else 0,
        "households": households,
        "world_ready": built is not None,
        "world_building": cache.building(run_id),
        "events": n_events,
        "last_seq": max_seq,
        # `days` in run.meta is what was ASKED for; days_done is what exists.
        "days_planned": m.get("days"),
        "days_done": max_t // SECONDS_PER_DAY + 1 if n_events else 0,
        "max_t": max_t,
        "routed": bool(built and built.routed),
        "status": live.get("status", rec.status),
        "parent_id": rec.parent_id,
        "parent_day": rec.parent_day,
        "what_if": rec.what_if,
        "managed": rec.managed,
        "bounds": [[min(lats), min(lons)], [max(lats), max(lons)]] if lats else None,
        "epoch": to_datetime(0).isoformat(),
    }


@router.get("/{run_id}/people")
def people(request: Request, run_id: str, q: str = "", offset: int = 0, limit: int = 200):
    """Paginated and searchable. The old one returned the whole roster — 7 MB
    at V3 scale, on every page load, to fill a list nobody scrolls to the end
    of."""
    _rec, w = _world(request, run_id)
    rows = [w.people[pid] for pid in w.order]
    if q:
        needle = q.lower()
        rows = [p for p in rows
                if needle in p.name.lower() or needle in p.occupation.lower()
                or needle in p.id]
    total = len(rows)
    page = rows[offset:offset + max(1, min(limit, 1000))]
    return {
        "total": total, "offset": offset,
        "items": [{
            "id": p.id, "ord": w.ordinal[p.id], "name": p.name, "age": p.age,
            "sex": p.sex, "occupation": p.occupation, "religion": p.religion,
            "household": p.household_id, "home": p.home_id, "work": p.work_id,
            "work_name": w.place_names.get(p.work_id, ""),
        } for p in page],
    }


@router.get("/{run_id}/roster")
def roster(request: Request, run_id: str):
    """Ordinal → id/name only. What the binary positions buffer indexes into;
    fetched once, then every frame is array maths."""
    _rec, w = _world(request, run_id)
    return {"order": w.order, "names": [w.people[p].name for p in w.order]}


@router.get("/{run_id}/person/{pid}")
def person(request: Request, run_id: str, pid: str, day: int | None = None):
    _rec, w = _world(request, run_id)
    p = w.people.get(pid)
    if p is None:
        raise HTTPException(404, f"no person {pid!r}")
    memories, moods, lines, interviews, heard = [], [], [], [], []
    hh = p.household_id
    # Two scopes, on purpose. "Their day" is one day — and fetching every event
    # of eighteen types across a thirty-day run to show a thirtieth of them was
    # 4.6 s a click at V3 scale, slower than the whole map. But what somebody
    # REMEMBERS and what they BELIEVE accumulate over the run; scoping those to
    # today would quietly empty them, and an empty panel reads as "nothing
    # happened to this person" rather than "you are looking at one day".
    day_rows = w.view.of_type(*_DOSSIER_TYPES, *_HOUSEHOLD_TYPES,
                              limit=1_000_000, day=day)
    lifetime_rows = w.view.for_person(
        pid, ("info.heard", "memory.formed", "conversation.held"), limit=400)
    seen_seq: set[int] = set()
    for e in [*lifetime_rows, *day_rows]:
        if e.seq in seen_seq:
            continue
        seen_seq.add(e.seq)
        pl = e.payload
        touched = {pl.get("person"), pl.get("sender"), pl.get("entity_id"),
                   pl.get("complainant"), pl.get("victim"),
                   *(pl.get("recipients") or []), *(pl.get("participants") or [])}
        mine_hh = e.type in _HOUSEHOLD_TYPES and pl.get("household") == hh
        if pid not in touched and not mine_hh:
            continue
        if e.type == "info.heard" and pl.get("person") == pid:
            c = pl.get("claim", {})
            heard.append({
                "t": e.sim_time, "hm": to_datetime(e.sim_time).strftime("%a %H:%M"),
                "text": c.get("text", ""), "key": pl.get("claim_key"),
                "credence": pl.get("credence"), "channel": pl.get("channel"),
                "source_id": pl.get("source"),
                "source": w.person_names.get(pl.get("source"), pl.get("source")),
                "hop": c.get("hop", 0), "ops": c.get("ops", []),
            })
        elif e.type == "memory.formed" and pl.get("person") == pid:
            memories.append({"t": e.sim_time, "summary": pl.get("summary"),
                             "salience": pl.get("salience")})
        elif e.type == "mood.delta" and pl.get("person") == pid:
            moods.append({"t": e.sim_time, "dim": pl.get("dim"), "delta": pl.get("delta")})
        elif e.type == "conversation.held" and pl.get("with") == "journalist" \
                and pl.get("person") == pid:
            interviews.append({
                "t": e.sim_time, "hm": to_datetime(e.sim_time).strftime("%a %H:%M"),
                "question": pl.get("question", ""), "answer": pl.get("answer", ""),
            })
        h = humanize(e, w.person_names, w.place_names)
        lines.append({
            "seq": e.seq, "t": e.sim_time, "day": e.sim_time // SECONDS_PER_DAY,
            "hm": to_datetime(e.sim_time).strftime("%H:%M"),
            "type": e.type, "caused_by": e.caused_by,
            "text": h["text"], "refs": h["refs"],
        })
    lines.sort(key=lambda x: (x["t"], x["seq"]))

    trips = []
    if day is not None:
        for s in w.view.segs_for_day(day, person=pid).get(pid, []):
            trips.append({
                "t0": s.t0, "t1": s.t1, "kind": s.kind,
                "a": s.a, "a_name": w.place_names.get(s.a, ""),
                "b": s.b, "b_name": w.place_names.get(s.b, "") if s.b else "",
                "activity": s.activity,
            })
    return {
        "id": p.id, "ord": w.ordinal[pid], "name": p.name, "age": p.age, "sex": p.sex,
        "occupation": p.occupation, "religion": p.religion, "household": hh,
        "members": [{"id": m, "name": w.people[m].name, "age": w.people[m].age,
                     "occupation": w.people[m].occupation}
                    for m in w.hh_members.get(hh, []) if m in w.people],
        "home": p.home_id, "home_name": w.place_names.get(p.home_id, "home"),
        "work": p.work_id, "work_name": w.place_names.get(p.work_id, ""),
        "memories": memories, "moods": moods, "timeline": lines,
        "interviews": interviews, "heard": heard, "trips": trips,
    }


@router.get("/{run_id}/place/{place_id:path}")
def place(request: Request, run_id: str, place_id: str, day: int = 0, t: int | None = None):
    """One place: what it is, who is in it now, what happened here today."""
    _rec, w = _world(request, run_id)
    pl = w.block.get(place_id)
    if pl is None:
        raise HTTPException(404, f"no place {place_id!r}")
    moment = t if t is not None else day * SECONDS_PER_DAY + 12 * 3600
    here = []
    for pid, segs in w.view.segs_for_day(moment // SECONDS_PER_DAY).items():
        for s in segs:
            if s.kind == "at" and s.a == place_id and s.t0 <= moment < (s.t1 if s.t1 > 0 else s.t0):
                here.append({"id": pid, "name": w.person_names.get(pid, pid),
                             "activity": s.activity or ""})
                break
    today = []
    lo, hi = (moment // SECONDS_PER_DAY) * SECONDS_PER_DAY, ((moment // SECONDS_PER_DAY) + 1) * SECONDS_PER_DAY
    for e in w.view.notable(limit=4000):
        if lo <= e.sim_time < hi and (
            e.payload.get("place") == place_id or e.payload.get("at") == place_id
        ):
            h = humanize(e, w.person_names, w.place_names)
            today.append({"seq": e.seq, "t": e.sim_time, "type": e.type,
                          "hm": to_datetime(e.sim_time).strftime("%H:%M"),
                          "text": h["text"], "refs": h["refs"]})
    return {
        "id": pl.id, "name": pl.name, "kind": pl.kind, "lat": pl.lat, "lon": pl.lon,
        "here": sorted(here, key=lambda x: x["name"])[:200], "here_n": len(here),
        "today": today,
    }


@router.get("/{run_id}/geo/{layer}")
def geo(request: Request, run_id: str, layer: str):
    """Buildings and roads as GeoJSON. Immutable per block, so it caches hard.

    Reads the block name from run.meta rather than a built world: the map
    should draw the streets while the population is still being synthesized,
    not after.
    """
    rec = request.app.state.registry.get(run_id)
    if rec is None:
        raise HTTPException(404, f"no run {run_id!r}")
    block_name = request.app.state.worlds.meta_only(run_id, rec.db).get("block", "kasba")
    try:
        body = layers_for(block_name).layer(layer)
    except KeyError:
        raise HTTPException(404, f"no layer {layer!r}; try buildings or roads") from None
    return Response(
        content=body, media_type="application/geo+json",
        headers={"Cache-Control": "public, max-age=86400",
                 "ETag": f'"{block_name}-{layer}-{len(body)}"'},
    )


@router.get("/{run_id}/compare")
def compare(request: Request, run_id: str, a: str, b: str, day: int = 0):
    """Two lives, side by side — and where they touch.

    The interesting column is the middle one. Two people in a city of fifty
    thousand mostly never meet, and when they do it is at a place at a time; and
    when they have both heard the same rumour they have usually heard DIFFERENT
    WORDS for it, at different credences, through different mouths. That drift
    is the thing this simulation is for, and it is invisible until you put two
    people next to each other.
    """
    _rec, w = _world(request, run_id)
    pa, pb = w.people.get(a), w.people.get(b)
    if pa is None or pb is None:
        raise HTTPException(404, "unknown person")

    # Where each was, through this day, from the same movement the map uses.
    segs = w.view.segs_for_day(day)
    sa = [s for s in segs.get(a, []) if s.kind == "at"]
    sb = [s for s in segs.get(b, []) if s.kind == "at"]

    crossings = []
    for x in sa:
        for y in sb:
            if x.a != y.a:
                continue
            lo, hi = max(x.t0, y.t0), min(x.t1 if x.t1 > 0 else x.t0,
                                          y.t1 if y.t1 > 0 else y.t0)
            if hi - lo < 300:   # under a tick together is passing, not meeting
                continue
            crossings.append({
                "place": x.a, "place_name": w.place_names.get(x.a, x.a),
                "t0": lo, "t1": hi, "minutes": (hi - lo) // 60,
                "hm": to_datetime(lo).strftime("%H:%M"),
                "a_doing": x.activity, "b_doing": y.activity,
            })
    crossings.sort(key=lambda c: c["t0"])

    # The same claim, as each of them holds it.
    def heard(pid: str) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for e in w.view.for_person(pid, ("info.heard",), limit=400):
            if e.payload.get("person") != pid:
                continue
            c = e.payload.get("claim", {})
            key = e.payload.get("claim_key") or c.get("key")
            if not key:
                continue
            out[key] = {
                "text": c.get("text", ""), "hop": c.get("hop", 0),
                "credence": e.payload.get("credence"),
                "hm": to_datetime(e.sim_time).strftime("%a %H:%M"),
                "source": w.person_names.get(e.payload.get("source"),
                                             e.payload.get("source")),
                "ops": c.get("ops", []),
            }
        return out

    ha, hb = heard(a), heard(b)
    shared = [
        {"key": k, "a": ha[k], "b": hb[k],
         "same_words": ha[k]["text"] == hb[k]["text"]}
        for k in ha.keys() & hb.keys()
    ]
    shared.sort(key=lambda s: (s["same_words"], s["key"]))

    def card(p) -> dict:
        return {
            "id": p.id, "name": p.name, "age": p.age, "sex": p.sex,
            "occupation": p.occupation, "religion": p.religion,
            "household": p.household_id,
            "home_name": w.place_names.get(p.home_id, "home"),
            "work_name": w.place_names.get(p.work_id, ""),
        }

    return {
        "day": day,
        "a": card(pa), "b": card(pb),
        "same_household": pa.household_id == pb.household_id,
        "crossings": crossings,
        "shared_claims": shared,
        "only_a": len(ha.keys() - hb.keys()),
        "only_b": len(hb.keys() - ha.keys()),
    }


@router.get("/{run_id}/places")
def places_early(request: Request, run_id: str):
    """Named places — also from the block alone, for the same reason as geo."""
    from ...world.block import load_for

    rec = request.app.state.registry.get(run_id)
    if rec is None:
        raise HTTPException(404, f"no run {run_id!r}")
    built = request.app.state.worlds.cached(run_id)
    if built is not None:
        block = built.block
    else:
        m = request.app.state.worlds.meta_only(run_id, rec.db)
        block = load_for(m.get("households", rec.params.households),
                         m.get("block", "kasba"), roads=False)
    return [{"id": p.id, "name": p.name, "kind": p.kind, "lat": p.lat, "lon": p.lon}
            for p in block.places]


@router.get("/{run_id}/positions")
def positions(request: Request, run_id: str, t: int):
    """Everybody, at one moment, as a binary buffer. See api/positions.py."""
    _rec, w = _world(request, run_id)
    buf = pos_codec.snapshot(w, t)
    return Response(content=buf, media_type="application/octet-stream",
                    headers={"Cache-Control": "no-store"})


@router.get("/{run_id}/route")
def route(request: Request, run_id: str, frm: str, to: str):
    """The real walked path between two places, for the follow-camera trail.

    Without this the map lerps a straight line between endpoints and people walk
    through buildings — while the engine, which has an 8k-node graph, routed
    them along the lanes all along.
    """
    from ..routing import walk_path

    _rec, w = _world(request, run_id, roads=True)
    a, b = w.block.get(frm), w.block.get(to)
    if a is None or b is None:
        raise HTTPException(404, "unknown place")
    path = walk_path(w.block, frm, to)
    if path is None:
        return {"straight": True, "polyline": [[a.lat, a.lon], [b.lat, b.lon]]}
    return {"straight": False, "polyline": path}
