from typing import TYPE_CHECKING

from ...kernel.facts import Canon, PredicateRegistry
from ...kernel.log import EventIn, EventLog
from ...kernel.timebase import SECONDS_PER_DAY
from ...kernel.worlddelta import PlanStep, WorldDelta
from ...llm.gateway import Gateway
from ...population.synth import Household, Person
from ...world.block import Block
from .apply import apply_delta
from .context import (
    build_messages,
    build_reaction_messages,
    memory_digest,
    physical_state,
    recent_notable_events,
    witnessed_facts,
)
from .prompt import SCENE_HOUR_S, SceneResult
from .render import held_memories

if TYPE_CHECKING:  # the scene lane reads institution state; it never writes it
    from ...institutions.procedures import ProcState


def compile_plan_overrides(
    deltas: list[SceneResult], people: dict[str, Person], day: int
) -> dict[str, list[PlanStep]]:
    """Scene day_plans -> per-person step lists (t normalized to absolute sim s)."""
    base = day * SECONDS_PER_DAY
    overrides: dict[str, list[PlanStep]] = {}
    for r in deltas:
        for dp in r.delta.day_plan:
            if dp.person_id not in people or not dp.steps:
                continue
            steps = []
            for s in dp.steps:
                t = s.t if s.t >= SECONDS_PER_DAY else base + s.t
                steps.append(PlanStep(t=t, place_ref=s.place_ref, activity=s.activity, mode=s.mode))
            overrides[dp.person_id] = sorted(steps, key=lambda s: s.t)
    return overrides


def run_morning_scenes(
    log: EventLog,
    gateway: Gateway,
    canon: Canon,
    registry: PredicateRegistry,
    block: Block,
    households: list[Household],
    people: dict[str, Person],
    day: int,
    *,
    chosen_ids: list[str],
    proc: "ProcState",
) -> list[SceneResult]:
    from ...llm.gateway import CassetteMiss

    results: list[SceneResult] = []
    by_id = {h.id: h for h in households}
    sim_time = day * SECONDS_PER_DAY + SCENE_HOUR_S
    for hid in chosen_ids:
        hh = by_id[hid]
        members = set(hh.member_ids)
        recent = recent_notable_events(
            log, members, day, block, until=sim_time, household_id=hid, people=people
        )
        memories = memory_digest(log, members, day, block, until=sim_time, people=people)
        witnessed = witnessed_facts(log, members, day, block, until=sim_time, people=people)
        physical = physical_state(proc, members, day, block, now_abs=sim_time, people=people)
        msgs = build_messages(block, hh, people, day, recent, memories, witnessed, physical)
        try:
            res = gateway.call("scene", msgs, WorldDelta, temperature=0.6, max_tokens=2000, sim_time=sim_time)
        except CassetteMiss:
            raise  # replay integrity is law 1 — never soften it
        except Exception as err:  # noqa: BLE001 — refusal/schema/transport: skip LOUDLY, day goes on
            log.commit([EventIn(
                type="scene.skipped", sim_time=sim_time,
                payload={"household": hid, "reason": f"{type(err).__name__}: {err}"[:200]},
                provenance="system",
            )])
            continue
        seq = apply_delta(
            log, canon, registry, res.parsed, household_id=hid, sim_time=sim_time,
            people=people, prior_memories=held_memories(log, members, until=sim_time),
        )
        results.append(SceneResult(household_id=hid, delta=res.parsed, scene_seq=seq))
    return results


def run_reaction_scene(
    log: EventLog,
    gateway: Gateway,
    canon: Canon,
    registry: PredicateRegistry,
    block: Block,
    household: Household,
    people: dict[str, Person],
    day: int,
    now_abs: int,
    proc: "ProcState",
) -> SceneResult:
    """T2 event-driven scene: the household reacts the moment it learns —
    the mid-day lane the morning gate cannot provide (09 break B9, V0-thin)."""
    members = set(household.member_ids)
    recent = recent_notable_events(
        log, members, day, block, until=now_abs, household_id=household.id, people=people
    )
    memories = memory_digest(log, members, day, block, until=now_abs, people=people)
    witnessed = witnessed_facts(log, members, day, block, until=now_abs, people=people)
    physical = physical_state(proc, members, day, block, now_abs=now_abs, people=people)
    msgs = build_reaction_messages(
        block, household, people, day, recent, now_abs, memories, witnessed, physical
    )
    res = gateway.call("scene", msgs, WorldDelta, temperature=0.6, max_tokens=2000, sim_time=now_abs)
    seq = apply_delta(
        log, canon, registry, res.parsed,
        household_id=household.id, sim_time=now_abs, event_type="scene.reaction",
        people=people, prior_memories=held_memories(log, members, until=now_abs),
    )
    return SceneResult(household_id=household.id, delta=res.parsed, scene_seq=seq)
