"""Diff two timelines (branch-lite V2).

Because a branch shares a deterministic prefix with its source, the diff has
a precise anatomy: bit-identical events up to the FIRST DIVERGENCE, then two
honestly different worlds. Pairwise comparison is only meaningful up to that
point — after it, everything shifts, so the report switches to aggregate
lenses: whose days changed, what spread further, who paid what, who got
hurt in one world and not the other.
"""

from dataclasses import dataclass, field

import orjson

from .log import EventLog
from .timebase import SECONDS_PER_DAY, to_datetime

_ROUTINE = {"trip.start", "trip.end", "activity.start"}
_NOISE = {"llm.response", "run.meta"}


def _sig(e) -> bytes:
    return orjson.dumps(
        [e.sim_time, e.type, e.payload, e.provenance], option=orjson.OPT_SORT_KEYS
    )


def _hm(t: int) -> str:
    return to_datetime(t).strftime("%a %H:%M")


@dataclass
class DiffReport:
    identical: bool
    a_events: int
    b_events: int
    branch_point: dict | None = None  # the injected what-if (expected, skipped)
    first_divergence: dict | None = None  # first KNOCK-ON difference {day, hm, a, b}
    people_changed: dict = field(default_factory=dict)  # pid -> [days]
    by_day_changed: dict = field(default_factory=dict)  # day -> changed person-days (decoherence curve)
    reconverged_day: int | None = None  # worlds identical again from this day on (None = never)
    type_deltas: dict = field(default_factory=dict)  # type -> b_count - a_count
    rumor_deltas: list = field(default_factory=list)  # {key, reach_a, reach_b}
    only_in_b: list = field(default_factory=list)  # notable one-world events (b side)
    only_in_a: list = field(default_factory=list)
    new_llm_calls: int = 0  # branch calls not replayed from the shared prefix
    headline: list = field(default_factory=list)


_NOTABLE_SOLO = (
    "hospital.admitted", "hospital.discharged", "loan.taken", "police.fir.registered",
    "belief.action", "plan.avoided", "pressure.crossed", "scene.reaction",
)


def _person_day_sigs(events) -> dict:
    out: dict[tuple[str, int], int] = {}
    for e in events:
        if e.type not in _ROUTINE:
            continue
        pid = e.payload.get("person")
        if not pid:
            continue
        key = (pid, e.sim_time // SECONDS_PER_DAY)
        h = hash((out.get(key, 0), e.sim_time, e.type,
                  e.payload.get("to"), e.payload.get("at"), e.payload.get("activity")))
        out[key] = h
    return out


def _solo_keys(events) -> dict:
    """Identity keys for events that matter even alone in one world."""
    out = {}
    for e in events:
        if e.type in _NOTABLE_SOLO:
            p = e.payload
            key = (e.type, p.get("person") or p.get("victim") or p.get("household"),
                   e.sim_time // SECONDS_PER_DAY)
            out.setdefault(key, e)
    return out


def diff_logs(log_a: EventLog, log_b: EventLog, names: dict[str, str] | None = None) -> DiffReport:
    nm = names or {}

    def label(x):
        return nm.get(x, x or "?")

    raw_a = list(log_a.events())
    raw_b = list(log_b.events())
    ev_a = [e for e in raw_a if e.type not in _NOISE]
    ev_b = [e for e in raw_b if e.type not in _NOISE]
    rep = DiffReport(identical=False, a_events=len(ev_a), b_events=len(ev_b))

    # 1. walk the shared prefix; the injected what-if is the EXPECTED first
    #    difference (the branch point) — the report's "first divergence" is
    #    the first KNOCK-ON event after skipping user-provenance insertions
    i = j = 0
    while i < len(ev_a) and j < len(ev_b):
        if _sig(ev_a[i]) == _sig(ev_b[j]):
            i += 1
            j += 1
            continue
        if ev_b[j].provenance == "user" and rep.branch_point is None:
            e = ev_b[j]
            rep.branch_point = {
                "day": e.sim_time // SECONDS_PER_DAY, "hm": _hm(e.sim_time),
                "what": f"{e.type}: {orjson.dumps(e.payload).decode()[:140]}",
            }
            j += 1  # cancel the insertion, keep walking the twin prefix
            continue
        break
    if i >= len(ev_a) and j >= len(ev_b):
        if rep.branch_point is None:
            rep.identical = True
            rep.headline = ["The two worlds are identical."]
        else:
            rep.headline = ["Only the injected event itself differs — no knock-on effects (yet)."]
        return rep
    ea = ev_a[i] if i < len(ev_a) else None
    eb = ev_b[j] if j < len(ev_b) else None
    at = (eb or ea).sim_time
    rep.first_divergence = {
        "day": at // SECONDS_PER_DAY, "hm": _hm(at),
        "a": f"{ea.type}: {orjson.dumps(ea.payload).decode()[:140]}" if ea else "(nothing)",
        "b": f"{eb.type}: {orjson.dumps(eb.payload).decode()[:140]}" if eb else "(nothing)",
    }

    # cost line: branch LLM calls that were not replays of the shared prefix
    rids_a = {e.payload.get("request_id") for e in raw_a if e.type == "llm.response"}
    rep.new_llm_calls = sum(
        1 for e in raw_b
        if e.type == "llm.response" and e.payload.get("request_id") not in rids_a
    )

    # 2. whose days changed (routine signatures per person-day) + the
    #    decoherence curve and a re-convergence check. Comparison stops at the
    #    SHARED horizon — days only one world lived are new life, not change.
    last_common_day = min(
        max((e.sim_time for e in ev_a), default=0),
        max((e.sim_time for e in ev_b), default=0),
    ) // SECONDS_PER_DAY
    sa, sb = _person_day_sigs(ev_a), _person_day_sigs(ev_b)
    changed: dict[str, list[int]] = {}
    by_day: dict[int, int] = {}
    for key in sorted(set(sa) | set(sb)):
        if key[1] > last_common_day:
            continue
        if sa.get(key) != sb.get(key):
            changed.setdefault(key[0], []).append(key[1])
            by_day[key[1]] = by_day.get(key[1], 0) + 1
    rep.people_changed = changed
    rep.by_day_changed = by_day
    if by_day and max(by_day) < last_common_day:
        rep.reconverged_day = max(by_day) + 1

    # 3. aggregate deltas by type (non-routine)
    def counts(evs):
        c: dict[str, int] = {}
        for e in evs:
            if e.type not in _ROUTINE:
                c[e.type] = c.get(e.type, 0) + 1
        return c

    ca, cb = counts(ev_a), counts(ev_b)
    rep.type_deltas = {
        t: cb.get(t, 0) - ca.get(t, 0)
        for t in sorted(set(ca) | set(cb))
        if cb.get(t, 0) != ca.get(t, 0)
    }

    # 4. rumor reach per claim family
    def reach(evs):
        r: dict[str, set] = {}
        for e in evs:
            if e.type == "info.heard":
                r.setdefault(e.payload.get("claim_key", "?"), set()).add(e.payload.get("person"))
        return r

    ra, rb = reach(ev_a), reach(ev_b)
    for key in sorted(set(ra) | set(rb)):
        la, lb = len(ra.get(key, ())), len(rb.get(key, ()))
        if la != lb or key not in ra or key not in rb:
            rep.rumor_deltas.append({"key": key, "reach_a": la, "reach_b": lb})

    # 5. notable events that exist in only one world
    ka, kb = _solo_keys(ev_a), _solo_keys(ev_b)
    for key in sorted(set(kb) - set(ka)):
        e = kb[key]
        rep.only_in_b.append({"day": e.sim_time // SECONDS_PER_DAY, "hm": _hm(e.sim_time),
                              "type": e.type, "who": label(key[1])})
    for key in sorted(set(ka) - set(kb)):
        e = ka[key]
        rep.only_in_a.append({"day": e.sim_time // SECONDS_PER_DAY, "hm": _hm(e.sim_time),
                              "type": e.type, "who": label(key[1])})

    # 6. the ten-second headline (divergence-first ordering)
    h = rep.headline
    if rep.branch_point is not None:
        h.append(f"Branch point: day {rep.branch_point['day']} {rep.branch_point['hm']} — {rep.branch_point['what'][:90]}.")
    fd = rep.first_divergence
    h.append(f"First knock-on divergence: day {fd['day']} at {fd['hm']}.")
    if changed:
        pd = sum(len(v) for v in changed.values())
        top = sorted(changed.items(), key=lambda kv: (-len(kv[1]), kv[0]))[:3]
        who = ", ".join(f"{label(pid)} ×{len(days)}" for pid, days in top)
        h.append(f"{len(changed)} people lived a different day at least once ({pd} person-days: {who}…).")
    if rep.reconverged_day is not None:
        h.append(f"The timelines re-converged by day {rep.reconverged_day}.")
    elif by_day:
        h.append("The timelines never re-converge — the worlds stay different.")
    horizon_a = max((e.sim_time for e in ev_a), default=0) // SECONDS_PER_DAY
    horizon_b = max((e.sim_time for e in ev_b), default=0) // SECONDS_PER_DAY
    if horizon_b > horizon_a:
        h.append(f"B lives {horizon_b - horizon_a} day(s) beyond A's horizon — deltas past day {horizon_a} are new life, not divergence.")
    for rd in rep.rumor_deltas[:3]:
        arrow = "+" if rd["reach_b"] >= rd["reach_a"] else "−"
        h.append(f"Rumor {rd['key']}: reached {rd['reach_b']} people in B vs {rd['reach_a']} in A ({arrow}{abs(rd['reach_b'] - rd['reach_a'])}).")
    label_of = {
        "hospital.admitted": "hospital admission", "hospital.discharged": "discharge",
        "loan.taken": "moneylender loan", "police.fir.registered": "FIR",
        "belief.action": "belief-driven action", "plan.avoided": "avoided outing",
        "pressure.crossed": "pressure crossing", "scene.reaction": "reaction scene",
    }
    for side, items, word in (("B", rep.only_in_b, "only in the branch"),
                              ("A", rep.only_in_a, "only in the source")):
        by_t: dict[str, int] = {}
        for it in items:
            by_t[it["type"]] = by_t.get(it["type"], 0) + 1
        for t, n in sorted(by_t.items()):
            h.append(f"{n} {label_of.get(t, t)}{'s' if n > 1 else ''} {word} ({side}).")
    if rep.new_llm_calls:
        h.append(f"{rep.new_llm_calls} fresh LLM calls in the branch; everything before the split replayed free.")
    return rep
