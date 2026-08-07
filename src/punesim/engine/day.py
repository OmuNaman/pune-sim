from ..kernel.log import EventIn, EventLog
from ..kernel.worlddelta import PlanStep
from ..population.synth import Person
from ..world.block import Block
from ..world.schedule import TimedEvent, day_events
from .state import _ORDER, _Timed


def _compile_override(person: Person, steps: list[PlanStep], block: Block) -> list[TimedEvent]:
    """A scene-revised (part of a) day: walk trips between planned places.
    V0 simplification: the walk starts from home."""
    ev: list[TimedEvent] = []
    current = person.home_id
    for s in steps:
        place = s.place_ref if block.get(s.place_ref) else None
        if place is None:
            ev.append(
                TimedEvent(s.t, "plan.step_dropped", {"person": person.id, "place_ref": s.place_ref})
            )
            continue
        if place != current:
            dur = block.walk_seconds(current, place)
            depart = max(s.t - dur, 0)
            ev.append(
                TimedEvent(depart, "trip.start", {"person": person.id, "from": current, "to": place, "mode": s.mode or "walk", "purpose": s.activity})
            )
            ev.append(TimedEvent(depart + dur, "trip.end", {"person": person.id, "at": place, "purpose": s.activity}))
            current = place
        ev.append(TimedEvent(max(s.t, ev[-1].sim_time if ev else s.t), "activity.start", {"person": person.id, "at": place, "activity": s.activity}))
    return ev


def _compile_day(
    run_seed: int,
    block: Block,
    people: dict[str, Person],
    day: int,
    plan_overrides: dict[str, list[PlanStep]] | None,
    worksites: dict[str, tuple[str, ...]] | None = None,
) -> list[_Timed]:
    overrides = plan_overrides or {}
    sites = worksites or {}
    timed: list[_Timed] = []
    for pid in sorted(people):
        evs = (
            _compile_override(people[pid], overrides[pid], block)
            if pid in overrides
            else day_events(run_seed, people[pid], block, day, sites.get(pid, ()))
        )
        for te in evs:
            timed.append((te.sim_time, pid, te.type, te, "clockwork"))
    return timed


def _apply_admissions(timed: list[_Timed], extra: list[TimedEvent]) -> list[_Timed]:
    """Mechanical plan invalidation: an admitted person's remaining day is
    cancelled and replaced by being at the hospital."""
    admissions = [
        (te.payload["person"], te.sim_time, te.payload["place"])
        for te in extra
        if te.type == "hospital.admitted"
    ]
    for pid, t_adm, place in admissions:
        timed = [x for x in timed if not (x[1] == pid and x[0] > t_adm)]
        te = TimedEvent(t_adm, "activity.start", {"person": pid, "at": place, "activity": "admitted"})
        timed.append((te.sim_time, pid, te.type, te, "clockwork"))
    return timed


def _sorted(timed: list[_Timed]) -> list[_Timed]:
    return sorted(timed, key=lambda x: (x[0], x[1], _ORDER.get(x[2], 9), x[2]))


def _commit(log: EventLog, timed: list[_Timed]) -> int:
    if not timed:
        return 0
    log.commit(
        [
            EventIn(type=ty, sim_time=t, payload=te.payload, provenance=prov, caused_by=te.caused_by)
            for (t, _pid, ty, te, prov) in timed
        ]
    )
    return len(timed)


def run_day(
    log: EventLog,
    run_seed: int,
    block: Block,
    people: dict[str, Person],
    day: int,
    *,
    plan_overrides: dict[str, list[PlanStep]] | None = None,
    extra: list[TimedEvent] | None = None,
    extra_provenance: str = "user",
) -> int:
    """One clockwork day, committed whole (zero-LLM path)."""
    timed = _compile_day(run_seed, block, people, day, plan_overrides)
    timed = _apply_admissions(timed, extra or [])
    for te in extra or []:
        timed.append((te.sim_time, "~injected", te.type, te, extra_provenance))
    return _commit(log, _sorted(timed))


def run_days(
    log: EventLog,
    run_seed: int,
    block: Block,
    people: dict[str, Person],
    *,
    days: int = 1,
    start_day: int = 0,
) -> int:
    """Zero-LLM compatibility path (tests, `punesim run` without --scenes)."""
    total = 0
    for day in range(start_day, start_day + days):
        total += run_day(log, run_seed, block, people, day)
    return total
