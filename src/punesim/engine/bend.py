from ..kernel.timebase import SECONDS_PER_DAY
from ..population.synth import Person
from ..world import unrest as unrest_mod
from ..world.block import Block
from ..world.schedule import TimedEvent
from .state import SimState, _Timed


def _apply_zones(
    block: Block, timed: list[_Timed], people: dict[str, Person], state: SimState,
    day: int, skip: set[str],
) -> list[_Timed]:
    """Active fear/curfew zones keep households home — the shelter side of
    collective dynamics. Essential occupations keep moving; scene plans win."""
    shelters = unrest_mod.zone_shelters(state.unrest, day, block, people)
    # Record everyone the curfew covers, INCLUDING those a scene is about to
    # re-plan: they still lost the day's wage, and the finance lane can only
    # see the activity strings the engine writes, never the scene's free text.
    state.sheltered = set(shelters)
    shelters = {pid: z for pid, z in shelters.items() if pid not in skip}
    if not shelters:
        return timed
    base = day * SECONDS_PER_DAY
    timed = [x for x in timed if not (x[1] in shelters and x[4] == "clockwork")]
    for pid in sorted(shelters):
        te = TimedEvent(base + 8 * 3600, "activity.start",
                        {"person": pid, "at": people[pid].home_id,
                         "activity": "shelters_at_home", "zone": shelters[pid]})
        timed.append((te.sim_time, pid, te.type, te, "clockwork"))
    return timed


def _apply_stays(
    timed: list[_Timed], people: dict[str, Person], state: SimState, day: int, skip: set[str]
) -> list[_Timed]:
    """Hospital stays and convalescence bend the clockwork: an admitted person
    spends the day in the ward; a discharged one rests at home until fit.
    Scene-revised plans (skip) win, as everywhere."""
    base = day * SECONDS_PER_DAY
    replaced: dict[str, TimedEvent] = {}
    for pid in sorted(state.proc.in_hospital):
        until, place = state.proc.in_hospital[pid]
        if day < until and pid in people and pid not in skip:
            replaced[pid] = TimedEvent(
                base + 8 * 3600, "activity.start",
                {"person": pid, "at": place or people[pid].home_id, "activity": "admitted"},
            )
    for pid in sorted(state.proc.rest):
        if pid in replaced or pid in skip or pid not in people:
            continue
        if day < state.proc.rest[pid] and day >= state.proc.in_hospital.get(pid, (0, ""))[0]:
            replaced[pid] = TimedEvent(
                base + 8 * 3600, "activity.start",
                {"person": pid, "at": people[pid].home_id, "activity": "rest_at_home"},
            )
    if not replaced:
        return timed
    timed = [x for x in timed if not (x[1] in replaced and x[4] == "clockwork")]
    for pid in sorted(replaced):
        te = replaced[pid]
        timed.append((te.sim_time, pid, te.type, te, "clockwork"))
    return timed


def _apply_beliefs(
    timed: list[_Timed], people: dict[str, Person], state: SimState, day: int, skip: set[str]
) -> list[_Timed]:
    """Mechanical E5 behavior for un-spotlit people: a believer whose routine
    touches an avoided place stays home instead (V1 ruling: the whole clockwork
    day drops — errand days ARE the visit, and a feared workplace keeps you
    home), plus one-shot morning acts (store_water). Scene-revised plans win —
    persons in `skip` are untouched."""
    base = day * SECONDS_PER_DAY
    dropped: dict[str, tuple[str, str, int, int]] = {}  # pid -> (place, claim_key, seq, t_dep)
    for pid in sorted(state.avoid):
        if pid in skip or pid not in people:
            continue
        places = state.avoid[pid]
        for t, epid, _ty, te, prov in timed:
            if epid != pid or prov != "clockwork":
                continue
            hit = next((te.payload[k] for k in ("to", "at") if te.payload.get(k) in places), None)
            if hit is not None:
                claim_key, seq = places[hit]
                dropped[pid] = (hit, claim_key, seq, t)
                break
    if dropped:
        timed = [x for x in timed if not (x[1] in dropped and x[4] == "clockwork")]
        for pid in sorted(dropped):
            place, claim_key, seq, t_dep = dropped[pid]
            for te in (
                TimedEvent(t_dep, "plan.avoided",
                           {"person": pid, "place": place, "claim_key": claim_key,
                            "activity": "stays_home"}, seq),
                TimedEvent(t_dep, "activity.start",
                           {"person": pid, "at": people[pid].home_id, "activity": "stays_home"}, seq),
            ):
                timed.append((te.sim_time, pid, te.type, te, "clockwork"))
    for pid in sorted(state.morning_acts):
        if pid in skip or pid not in people:
            continue
        for activity, _claim_key, seq in state.morning_acts[pid]:
            te = TimedEvent(base + int(6.75 * 3600), "activity.start",
                            {"person": pid, "at": people[pid].home_id, "activity": activity}, seq)
            timed.append((te.sim_time, pid, te.type, te, "clockwork"))
    state.morning_acts.clear()
    return timed
