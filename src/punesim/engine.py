"""V0 engine: clockwork days, scene integration, injections, stub institutions.

The day pipeline: (1) morning scenes for spotlit households (optional, LLM),
(2) compile everyone's day — routine templates, or scene-revised plans for
people whose family changed today — merged with injected events and the stub
institutions' timed reactions, (3) commit in sim-time order. Two runs from the
same seed + same cassettes are hash-identical.
"""

from dataclasses import dataclass, field

from .kernel.attention import AttentionField
from .kernel.facts import Canon, PredicateRegistry, core_registry
from .kernel.log import EventIn, EventLog
from .kernel.timebase import SECONDS_PER_DAY
from .kernel.worlddelta import PlanStep
from .llm.gateway import Gateway
from .population.synth import Household, Person
from .world.block import Block
from .world.schedule import TimedEvent, day_events


@dataclass(frozen=True)
class Injection:
    """A user-injected event (provenance='user'), V0-structured."""

    day: int
    time_s: int  # seconds since midnight
    type: str
    place: str | None = None
    participants: tuple[str, ...] = ()
    severity: float | None = None
    payload: dict = field(default_factory=dict)

    @classmethod
    def parse(cls, obj: dict) -> "Injection":
        hh, mm = obj["time"].split(":")
        return cls(
            day=int(obj["day"]),
            time_s=int(hh) * 3600 + int(mm) * 60,
            type=obj["type"],
            place=obj.get("place"),
            participants=tuple(obj.get("participants", [])),
            severity=obj.get("severity"),
            payload=obj.get("payload", {}),
        )


def stub_institution_reactions(
    inj: Injection, t_abs: int, block: Block, people: dict[str, Person]
) -> list[TimedEvent]:
    """Hand-rule subscribers (07-interface red-team fixture): scripted
    ambulance/hospital/school reactions until INSTITUTIONS exists (V2)."""
    out: list[TimedEvent] = []
    if not inj.type.startswith("hazard."):
        return out
    if inj.place:
        out.append(
            TimedEvent(t_abs + 8 * 60, "ambulance.dispatched", {"place": inj.place, "for": list(inj.participants)})
        )
    for pid in inj.participants:
        person = people.get(pid)
        if person is None:
            continue
        hospital = block.nearest(inj.place or person.home_id, "hospital", "clinic")
        if hospital:
            out.append(
                TimedEvent(t_abs + 25 * 60, "hospital.admitted", {"person": pid, "place": hospital.id})
            )
        out.append(
            TimedEvent(
                t_abs + 5 * 60,
                "condition.set",
                {"entity_id": pid, "kind": "injury", "intensity": inj.severity or 0.5, "stage": "er"},
            )
        )
        # the school (or workplace) calls home
        family = [
            q.id
            for q in people.values()
            if q.household_id == person.household_id and q.id != pid and q.age >= 18
        ]
        if family:
            caller = person.work_id or "org:unknown"
            wp = block.get(caller)
            who = wp.name if wp and wp.name else "the school"
            out.append(
                TimedEvent(
                    t_abs + 20 * 60,
                    "message.sent",
                    {
                        "sender": caller,
                        "recipients": family,
                        "channel": "phone",
                        "text": f"Call from {who}: {person.given} has been in an accident; taken to hospital.",
                    },
                )
            )
    return out


def _compile_override(
    person: Person, steps: list[PlanStep], block: Block
) -> list[TimedEvent]:
    """A scene-revised day: walk trips between consecutive planned places."""
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
    """Commit one clockwork day. Overridden people follow their revised plan."""
    overrides = plan_overrides or {}
    timed: list[tuple[int, str, str, TimedEvent, str]] = []
    order = {"trip.end": 0, "activity.start": 1, "trip.start": 2}

    for pid in sorted(people):
        evs = (
            _compile_override(people[pid], overrides[pid], block)
            if pid in overrides
            else day_events(run_seed, people[pid], block, day)
        )
        for te in evs:
            timed.append((te.sim_time, pid, te.type, te, "clockwork"))
    for te in extra or []:
        timed.append((te.sim_time, "~injected", te.type, te, extra_provenance))

    timed.sort(key=lambda x: (x[0], x[1], order.get(x[2], 9), x[2]))
    batch = [
        EventIn(type=te.type, sim_time=t, payload=te.payload, provenance=prov)
        for (t, _pid, _ty, te, prov) in timed
    ]
    log.commit(batch)
    return len(batch)


@dataclass
class SimState:
    canon: Canon
    registry: PredicateRegistry
    attention: AttentionField


def run_simulation(
    log: EventLog,
    run_seed: int,
    block: Block,
    households: list[Household],
    people: dict[str, Person],
    *,
    days: int = 1,
    start_day: int = 0,
    gateway: Gateway | None = None,
    scenes_k: int = 5,
    scene_gate_mode: str = "spotlight",
    injections: list[Injection] | None = None,
) -> tuple[int, SimState]:
    """The V0 day pipeline. Returns (total events, final state)."""
    from .minds.scene import compile_plan_overrides, run_morning_scenes

    state = SimState(canon=Canon(), registry=core_registry(), attention=AttentionField())
    total = 0
    hh_of_person = {p.id: p.household_id for p in people.values()}

    for day in range(start_day, start_day + days):
        overrides: dict[str, list[PlanStep]] = {}
        if gateway is not None and scenes_k > 0:
            all_ids = [h.id for h in households]
            chosen = (
                all_ids
                if scene_gate_mode == "all"
                else state.attention.top_k(all_ids, scenes_k, tick=day * 288)
            )
            results = run_morning_scenes(
                log, gateway, state.canon, state.registry, block, households, people, day,
                chosen_ids=chosen,
            )
            overrides = compile_plan_overrides(results, people, day)
            total += len(results)

        extra: list[TimedEvent] = []
        for inj in injections or []:
            if inj.day != day:
                continue
            t_abs = day * SECONDS_PER_DAY + inj.time_s
            extra.append(
                TimedEvent(
                    t_abs,
                    inj.type,
                    {
                        "place": inj.place,
                        "participants": list(inj.participants),
                        "severity": inj.severity,
                        **inj.payload,
                    },
                )
            )
            extra.extend(stub_institution_reactions(inj, t_abs, block, people))
            for pid in inj.participants:
                if pid in hh_of_person:
                    state.attention.bump(hh_of_person[pid], 5.0, tick=day * 288)

        total += run_day(
            log, run_seed, block, people, day, plan_overrides=overrides, extra=extra
        )
    return total, state


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
