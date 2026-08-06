"""V0 morning household scene (T1).

The LLM is the camera and the judge, not the physics: the scene receives the
household card + recent notable events, returns a WorldDelta (law 3 — the one
schema), and everything it says enters the world only through commit() and
assert_facts(). A scene can revise members' day plans; the plan compiler turns
those into ordinary clockwork trips.
"""

from dataclasses import dataclass

from ..kernel.facts import Canon, PredicateRegistry, assert_facts
from ..kernel.log import EventIn, EventLog
from ..kernel.timebase import SECONDS_PER_DAY, to_datetime
from ..kernel.worlddelta import PlanStep, WorldDelta
from ..llm.gateway import Gateway
from ..population.synth import Household, Person
from ..world.block import Block

SCENE_HOUR_S = int(6.5 * 3600)  # scenes render at 06:30, before the day moves

SYSTEM = """You write the morning scene of one household in a life simulation of Pune's old city.
Ground everything in the HOUSEHOLD CARD and RECENT EVENTS; never contradict them; invent only
small daily texture (chai, tiffin, water timing, school bags). Characters speak naturally —
Marathi/Hindi/English code-mix is welcome. The narration voice is neutral and never attributes
behavior or traits to any community; no slurs anywhere. If RECENT EVENTS contains something
serious, the family responds like a real family — worry, phone calls, changed plans.
Output ONLY one JSON object; all fields optional, no extra fields:
{"narration": "2-4 sentences",
 "transcript": "Name: line\\nName: line  (4-12 lines; speaker labels are given names like 'Madhura:', never ids)",
 "memory_writes": [{"person_id": "...", "salience": 0.0-1.0, "summary": "..."}],
 "mood_deltas": [{"person_id": "...", "dim": "mood" or "stress", "delta": -1.0..1.0}],
 "messages": [{"sender": "...", "recipients": ["..."], "channel": "phone" or "talk", "text": "..."}],
 "canon_facts": [{"subject": "person id", "predicate": "pers.trait", "value": "short trait"}],
 "day_plan": [{"person_id": "...", "steps": [{"t": seconds-since-midnight (28800 = 08:00),
               "place_ref": "place id from the card", "activity": "...", "mode": "walk"}]}]}
Only include day_plan when today should differ from routine (someone stays home, a hospital
visit, an errand). Use exactly the person ids and place ids given in the card."""

REACTION_TASK = """It is {now} — the household has JUST learned of the most recent events above.
Write their immediate reaction — who calls whom, who rushes where, what they decide right now.
day_plan here means THE REST OF TODAY only (steps with t >= now, seconds since midnight): a parent
rushing to the hospital, a shop left shut, a child collected early. Keep it real and specific."""

_ROUTINE_TYPES = {"trip.start", "trip.end", "activity.start"}


@dataclass(frozen=True)
class SceneResult:
    household_id: str
    delta: WorldDelta
    scene_seq: int


def _humanize(e_type: str, payload: dict, block: Block) -> str:
    def pname(pid: str) -> str:
        p = block.get(pid) if pid else None
        return p.name if p and p.name else (pid or "?")

    if e_type == "message.sent":
        return f"{payload.get('sender', '?')} -> {','.join(payload.get('recipients', []))}: {payload.get('text', '')}"
    if e_type == "hazard.road.collision":
        return f"road accident at {pname(payload.get('place', ''))} involving {', '.join(payload.get('participants', []))}"
    if e_type == "hospital.admitted":
        return f"{payload.get('person', '?')} admitted at {pname(payload.get('place', ''))}"
    if e_type == "ambulance.dispatched":
        return f"ambulance reached {pname(payload.get('place', ''))}"
    if e_type == "condition.set":
        return f"{payload.get('entity_id', '?')}: {payload.get('kind', '?')} (severity {payload.get('intensity', '?')})"
    return f"{e_type}: { {k: v for k, v in payload.items() if k != 'wall'} }"


def recent_notable_events(
    log: EventLog,
    member_ids: set[str],
    day: int,
    block: Block,
    limit: int = 12,
    until: int | None = None,
) -> list[str]:
    """Non-routine events from yesterday onward that touch any member.
    `until` bounds the scene's knowledge to its own sim-time — a 06:30 scene
    must never see a 07:20 event that is already committed to the log."""
    since = max(0, (day - 1) * SECONDS_PER_DAY)
    out: list[str] = []
    for e in log.events():
        if e.sim_time < since or e.type in _ROUTINE_TYPES or e.type == "llm.response":
            continue
        if until is not None and e.sim_time >= until:
            continue
        touched = set()
        p = e.payload
        for key in ("person", "sender"):
            if p.get(key):
                touched.add(p[key])
        touched.update(p.get("recipients", []) or [])
        touched.update(p.get("participants", []) or [])
        if p.get("entity_id"):
            touched.add(p["entity_id"])
        if touched & member_ids:
            when = to_datetime(e.sim_time).strftime("%a %H:%M")
            out.append(f"- {when}: {_humanize(e.type, p, block)}")
    return out[-limit:]


def _card_lines(
    block: Block, household: Household, people: dict[str, Person], day: int
) -> list[str]:
    home = block.get(household.home_id)
    date = to_datetime(day * SECONDS_PER_DAY).strftime("%A, %d %B %Y")
    lines = [f"HOUSEHOLD CARD — {household.surname} family ({household.template}), {date}"]
    lines.append(f"home: {household.home_id}" + (f" ({home.name})" if home and home.name else ""))
    for pid in household.member_ids:
        p = people[pid]
        work = ""
        if p.work_id:
            wp = block.get(p.work_id)
            work = f", goes to {wp.name if wp and wp.name else p.work_id} [{p.work_id}]"
        lines.append(f"- {p.id}  {p.name}, {p.age}, {p.occupation}{work}")
    lines.append("")
    return lines


def build_messages(
    block: Block,
    household: Household,
    people: dict[str, Person],
    day: int,
    recent: list[str],
) -> list[dict]:
    lines = _card_lines(block, household, people, day)
    if recent:
        lines.append("RECENT EVENTS:")
        lines.extend(recent)
    else:
        lines.append("RECENT EVENTS: none — an ordinary morning.")
    lines.append("")
    lines.append("Write this household's morning scene.")
    return [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": "\n".join(lines)},
    ]


def build_reaction_messages(
    block: Block,
    household: Household,
    people: dict[str, Person],
    day: int,
    recent: list[str],
    now_abs: int,
) -> list[dict]:
    lines = _card_lines(block, household, people, day)
    lines.append("EVENTS (yesterday and TODAY so far):")
    lines.extend(recent or ["- (nothing notable)"])
    lines.append("")
    lines.append(REACTION_TASK.format(now=to_datetime(now_abs).strftime("%H:%M")))
    return [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": "\n".join(lines)},
    ]


def apply_delta(
    log: EventLog,
    canon: Canon,
    registry: PredicateRegistry,
    delta: WorldDelta,
    *,
    household_id: str,
    sim_time: int,
    disclosure_tier: int = 0,
    event_type: str = "scene.morning",
) -> int:
    """Commit the scene and its consequences; returns the scene event seq."""
    scene_seq = log.commit(
        [
            EventIn(
                type=event_type,
                sim_time=sim_time,
                payload={
                    "household": household_id,
                    "narration": delta.narration,
                    "transcript": delta.transcript or "",
                },
                provenance="llm_scene",
            )
        ]
    )[0]

    batch: list[EventIn] = []
    for m in delta.memory_writes:
        batch.append(
            EventIn(
                type="memory.formed",
                sim_time=sim_time,
                payload={"person": m.person_id, "salience": m.salience, "summary": m.summary},
                caused_by=scene_seq,
                provenance="llm_scene",
            )
        )
    for md in delta.mood_deltas:
        batch.append(
            EventIn(
                type="mood.delta",
                sim_time=sim_time,
                payload={"person": md.person_id, "dim": md.dim, "delta": md.delta},
                caused_by=scene_seq,
                provenance="llm_scene",
            )
        )
    for msg in delta.messages:
        batch.append(
            EventIn(
                type="message.sent",
                sim_time=sim_time,
                payload={
                    "sender": msg.sender,
                    "recipients": msg.recipients,
                    "channel": msg.channel,
                    "text": msg.text,
                },
                caused_by=scene_seq,
                provenance="llm_scene",
            )
        )
    for ev in delta.events:
        batch.append(
            EventIn(
                type=ev.type,
                sim_time=sim_time + max(0, ev.delay_s),
                payload={
                    **ev.payload,
                    "participants": [p.entity_id for p in ev.participants],
                    "severity": ev.severity,
                },
                caused_by=scene_seq,
                provenance="llm_scene",
            )
        )
    for c in delta.conditions:
        batch.append(
            EventIn(
                type="condition.set",
                sim_time=sim_time,
                payload={
                    "entity_id": c.entity_id,
                    "kind": c.kind,
                    "intensity": c.intensity,
                    "stage": c.stage,
                    "effects": c.effects,
                },
                caused_by=scene_seq,
                provenance="llm_scene",
            )
        )
    if delta.day_plan:
        batch.append(
            EventIn(
                type="plan.revised",
                sim_time=sim_time,
                payload={
                    "household": household_id,
                    "persons": [dp.person_id for dp in delta.day_plan],
                },
                caused_by=scene_seq,
                provenance="llm_scene",
            )
        )
    if batch:
        log.commit(batch)
    if delta.canon_facts:
        assert_facts(
            log,
            canon,
            registry,
            delta.canon_facts,
            provenance="llm_scene",
            sim_time=sim_time,
            disclosure_tier=disclosure_tier,
            caused_by=scene_seq,
        )
    return scene_seq


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
) -> list[SceneResult]:
    results: list[SceneResult] = []
    by_id = {h.id: h for h in households}
    sim_time = day * SECONDS_PER_DAY + SCENE_HOUR_S
    for hid in chosen_ids:
        hh = by_id[hid]
        recent = recent_notable_events(log, set(hh.member_ids), day, block, until=sim_time)
        msgs = build_messages(block, hh, people, day, recent)
        res = gateway.call("scene", msgs, WorldDelta, temperature=0.6, max_tokens=2000, sim_time=sim_time)
        seq = apply_delta(
            log, canon, registry, res.parsed, household_id=hid, sim_time=sim_time
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
) -> SceneResult:
    """T2 event-driven scene: the household reacts the moment it learns —
    the mid-day lane the morning gate cannot provide (09 break B9, V0-thin)."""
    recent = recent_notable_events(log, set(household.member_ids), day, block, until=now_abs)
    msgs = build_reaction_messages(block, household, people, day, recent, now_abs)
    res = gateway.call("scene", msgs, WorldDelta, temperature=0.6, max_tokens=2000, sim_time=now_abs)
    seq = apply_delta(
        log, canon, registry, res.parsed,
        household_id=household.id, sim_time=now_abs, event_type="scene.reaction",
    )
    return SceneResult(household_id=household.id, delta=res.parsed, scene_seq=seq)
