"""V0 engine: clockwork days, scenes, injections, stub institutions.

Day pipeline:
  1. T1 morning scenes for spotlit households (06:30, may revise routines);
  2. compile the day: routine templates or scene-revised plans, merged with
     injected events and stub-institution reactions; hospital admissions
     mechanically invalidate the patient's remaining day (plan invalidation);
  3. if an injection touches a household and minds are on, the day SPLITS at
     the moment the family learns: phase A commits, the T2 reaction scene runs
     right there (it can rewrite the rest of the family's day), then phase B
     commits. This is the same-day lane the morning gate cannot provide
     (09-collective-dynamics break B9, V0-thin).

Two runs from the same seed + same cassettes are hash-identical.
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

REACTION_DELAY_S = 35 * 60  # the family reacts ~35 min after the event (post phone call)

# (sim_time, person_key, type, event, provenance)
_Timed = tuple[int, str, str, TimedEvent, str]
_ORDER = {"trip.end": 0, "activity.start": 1, "trip.start": 2}


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
) -> list[_Timed]:
    overrides = plan_overrides or {}
    timed: list[_Timed] = []
    for pid in sorted(people):
        evs = (
            _compile_override(people[pid], overrides[pid], block)
            if pid in overrides
            else day_events(run_seed, people[pid], block, day)
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
            EventIn(type=ty, sim_time=t, payload=te.payload, provenance=prov)
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
    from .minds.scene import compile_plan_overrides, run_morning_scenes, run_reaction_scene

    state = SimState(canon=Canon(), registry=core_registry(), attention=AttentionField())
    total = 0
    hh_of_person = {p.id: p.household_id for p in people.values()}
    hh_by_id = {h.id: h for h in households}

    for day in range(start_day, start_day + days):
        # 1. T1 morning scenes
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

        # 2. injections + stub reactions + reaction-scene triggers
        extra: list[TimedEvent] = []
        reactions: dict[str, int] = {}  # household -> t_react (abs)
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
                hid = hh_of_person.get(pid)
                if hid is None:
                    continue
                state.attention.bump(hid, 5.0, tick=day * 288)
                if gateway is not None:
                    reactions[hid] = max(reactions.get(hid, 0), t_abs + REACTION_DELAY_S)

        # 3. compile + invalidate + commit (split when a family reacts mid-day)
        timed = _compile_day(run_seed, block, people, day, overrides)
        timed = _apply_admissions(timed, extra)
        for te in extra:
            timed.append((te.sim_time, "~injected", te.type, te, "user"))
        timed = _sorted(timed)

        if reactions:
            t_split = min(reactions.values())
            total += _commit(log, [x for x in timed if x[0] < t_split])
            rest = [x for x in timed if x[0] >= t_split]
            reaction_results = []
            for hid in sorted(reactions):
                reaction_results.append(
                    run_reaction_scene(
                        log, gateway, state.canon, state.registry, block,
                        hh_by_id[hid], people, day, now_abs=reactions[hid],
                    )
                )
            total += len(reaction_results)
            rest_over = compile_plan_overrides(reaction_results, people, day)
            for pid, steps in rest_over.items():
                person = people.get(pid)
                if person is None:
                    continue
                rest = [x for x in rest if not (x[1] == pid and x[4] == "clockwork")]
                clamped = [
                    PlanStep(t=max(s.t, t_split), place_ref=s.place_ref, activity=s.activity, mode=s.mode)
                    for s in steps
                ]
                for te in _compile_override(person, clamped, block):
                    rest.append((max(te.sim_time, t_split), pid, te.type, te, "clockwork"))
            total += _commit(log, _sorted(rest))
        else:
            total += _commit(log, timed)
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
