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
If someone "heard" a rumor, they repeat it in their own words at their stated belief level —
a 40% believer is skeptical, a 90% believer acts on it; family members may argue about it.
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
visit, an errand). Use exactly the person ids and place ids given in the card.

THREE RULES THAT OVERRIDE EVERYTHING ELSE:
1. PEOPLE ARE FIXED. Every person is given to you as "Name (age, occupation) [id]". Never invent
   a name, age, relationship or job for anyone, and never rename someone you were given. If a
   line mentions a six-year-old pupil, they are a six-year-old pupil — not a colleague, not an
   aunt. If you need someone who was not given to you, refer to them vaguely ("a neighbour",
   "someone at the market") and never give them an id.
2. TIME IS FIXED. Every line carries the exact date, time, and how long ago it was. Say "yesterday"
   only for something marked (yesterday), and never move an event to a different time of day than
   the one shown. If someone SAW IT HAPPEN, they saw it at the stated hour — they cannot
   misremember it as happening at night.
3. MEMORY IS BACKGROUND. The "WHAT THEY ALREADY CARRY" lines are what these people already know
   from earlier days. Never restate one as something that happened today, and never write a
   memory_write that repeats one. New memory_writes are for what happens in THIS scene."""

REACTION_TASK = """It is {now} — the household has JUST learned of the most recent events above.
Write their immediate reaction — who calls whom, who rushes where, what they decide right now.
day_plan here means THE REST OF TODAY only (steps with t >= now, seconds since midnight): a parent
rushing to the hospital, a shop left shut, a child collected early. Keep it real and specific."""

_ROUTINE_TYPES = {"trip.start", "trip.end", "activity.start"}

# A scene must never be shown its OWN prior output as if it were news. These
# types are the scene lane's own bookkeeping: they carry no world state a family
# could independently notice, and re-feeding them makes the model copy them
# forward. In the 30-day soak 64% of every RECENT EVENTS block was the
# household's own previous LLM output, and 53 of 118 prompts were 100%
# self-output with zero world events — which is how a Sunday memory got
# re-formed on Monday, word for word. Memory is read back deliberately by
# memory_digest(); it never leaks in through here.
_SELF_OUTPUT_TYPES = frozenset({
    "scene.morning", "scene.reaction", "scene.skipped", "scene.invalid_ref",
    "memory.formed", "mood.delta", "plan.revised",
})


@dataclass(frozen=True)
class SceneResult:
    household_id: str
    delta: WorldDelta
    scene_seq: int


def _who(pid: str, people: dict[str, Person] | None, block: Block) -> str:
    """Every id that reaches a prompt arrives with a name attached.

    A bare `person:022.4` in the context is an invitation: the soak's model met
    one and invented "Shobha tai", an adult colleague, for what canon says is a
    six-year-old pupil — then kept her for four days. Ids are also ambiguous on
    their own (hh:022 holds both Sachin Shelar, 6, and Sachin Shelar, 64)."""
    p = (people or {}).get(pid)
    if p is not None:
        return f"{p.name} ({p.age}, {p.occupation}) [{p.id}]"
    pl = block.get(pid) if pid else None
    if pl is not None and pl.name:
        return f"{pl.name} [{pid}]"
    return pid or "?"


def _when(t: int, day: int) -> str:
    """Absolute date plus how long ago. The soak's d6 scene called a fire from
    five days earlier "yesterday" — which was the literal reading of a context
    whose every line said only "Tue 06:30"."""
    dt = to_datetime(t)
    d = day - t // SECONDS_PER_DAY
    rel = "today" if d <= 0 else "yesterday" if d == 1 else f"{d} days ago"
    return f"{dt.strftime('%a %d %b %Y %H:%M')} ({rel})"


def _humanize(
    e_type: str, payload: dict, block: Block, people: dict[str, Person] | None = None
) -> str:
    """One line of world news for a prompt, or "" for anything a family could
    not have noticed. There is deliberately NO raw-payload fallback: dumping an
    unknown event's dict is how the scene lane's own bookkeeping ended up in
    its own prompts."""

    def who(pid: str) -> str:
        return _who(pid, people, block)

    def pname(pid: str) -> str:
        p = block.get(pid) if pid else None
        return f"{p.name} [{pid}]" if p and p.name else (pid or "?")

    if e_type == "message.sent":
        rec = ", ".join(who(r) for r in payload.get("recipients", []) or [])
        return f"{who(payload.get('sender', ''))} -> {rec}: {payload.get('text', '')}"
    if e_type == "hazard.road.collision":
        hurt = ", ".join(who(p) for p in payload.get("participants", []) or []) or "nobody hurt"
        return f"road accident at {pname(payload.get('place', ''))} — {hurt}"
    if e_type == "hospital.admitted":
        return f"{who(payload.get('person', ''))} admitted at {pname(payload.get('place', ''))}"
    if e_type == "ambulance.dispatched":
        return f"ambulance reached {pname(payload.get('place', ''))}"
    if e_type == "condition.set":
        return f"{who(payload.get('entity_id', ''))}: {payload.get('kind', '?')} (severity {payload.get('intensity', '?')})"
    if e_type == "info.heard":
        claim = payload.get("claim", {})
        src = who(payload.get("source", ""))
        how = {
            "witness": "SAW IT HAPPEN",
            "household": "heard it at home",
            "phone": f"heard by phone from {src}",
            "f2f": f"heard face to face from {src}",
        }.get(payload.get("channel", ""), f"heard from {src}")
        return (
            f"{who(payload.get('person', ''))} {how} — \"{claim.get('text', '')}\""
            f" (believes it {int(float(payload.get('credence', 0)) * 100)}%)"
        )
    if e_type == "belief.action":
        return (
            f"{who(payload.get('person', ''))} now acts on what they heard:"
            f" {payload.get('action', '')} re {pname(payload.get('place', ''))}"
        )
    if e_type == "plan.avoided":
        return f"{who(payload.get('person', ''))} is staying home today, avoiding {pname(payload.get('place', ''))} because of what they heard"
    if e_type == "pressure.crossed":
        return f"{who(payload.get('person', ''))}: {payload.get('pressure', '')} worry has crossed a threshold ({payload.get('value', '')})"
    if e_type == "plan.step_dropped":
        return f"{who(payload.get('person', ''))} could not get to {pname(payload.get('place_ref', ''))} today"
    if e_type == "hospital.discharged":
        return (
            f"{who(payload.get('person', ''))} discharged from {pname(payload.get('place', ''))} — "
            f"hospital bill ₹{int(payload.get('bill') or 0)}"
        )
    if e_type == "money.paid":
        return f"the household paid ₹{int(payload.get('amount') or 0)} ({payload.get('reason', '')})"
    if e_type == "loan.taken":
        return (
            f"the family had to borrow ₹{int(payload.get('principal') or 0)} from a moneylender "
            f"at {int(float(payload.get('monthly_rate') or 0.03) * 100)}% per month"
        )
    if e_type == "loan.interest":
        return f"moneylender interest added ₹{int(payload.get('amount') or 0)} — ₹{int(payload.get('outstanding') or 0)} now outstanding"
    if e_type == "police.fir.registered":
        return (
            f"{who(payload.get('complainant', ''))} registered an FIR at {pname(payload.get('station', ''))}"
            f" — statement: \"{payload.get('statement', '')}\""
        )
    if e_type == "complaint.registered":
        org = {"org:pmc_water": "the municipal water office", "org:mseb": "the electricity board"}
        return f"a complaint went in to {org.get(payload.get('org', ''), payload.get('org', '?'))} about {pname(payload.get('place', ''))}"
    if e_type == "utility.tanker_arrived":
        return f"a municipal water tanker reached {pname(payload.get('place', ''))} ({payload.get('loads', 1)} load(s))"
    if e_type == "utility.restored":
        return f"{payload.get('utility', 'supply')} came back around {pname(payload.get('place', ''))}"
    if e_type == "fir.update":
        return f"police update on the case: {payload.get('status', '')}"
    if e_type == "crowd.gathered":
        return f"a crowd of about {payload.get('size', '?')} gathered at {pname(payload.get('place', ''))}"
    if e_type == "police.deployed":
        return f"police reached {pname(payload.get('place', ''))} to disperse the crowd"
    if e_type == "curfew.imposed":
        return (
            f"a curfew was imposed around {pname(payload.get('place', ''))} from day"
            f" {payload.get('from_day', '?')} — people are told to stay indoors"
        )
    if e_type.startswith("unrest."):
        return f"trouble broke out near {pname(payload.get('place', ''))} — the lanes have gone tense"
    if e_type.startswith("info."):  # an injected rumour reaching the street
        return f"word is going round about {pname(payload.get('place', ''))}"
    if e_type.startswith("hazard."):
        kind = e_type.split(".", 1)[1].replace(".", " ").replace("_", " ")
        hurt = ", ".join(who(p) for p in payload.get("participants", []) or [])
        return f"{kind} at {pname(payload.get('place', ''))}" + (f" — {hurt}" if hurt else "")
    return ""  # unknown to the humanizer = not shown; never dump a raw payload


def recent_notable_events(
    log: EventLog,
    member_ids: set[str],
    day: int,
    block: Block,
    limit: int = 12,
    until: int | None = None,
    household_id: str | None = None,
    people: dict[str, Person] | None = None,
) -> list[str]:
    """Non-routine events from yesterday onward that touch any member.
    `until` bounds the scene's knowledge to its own sim-time — a 06:30 scene
    must never see a 07:20 event that is already committed to the log.
    `household_id` also matches household-addressed events (bills, loans).

    What this deliberately excludes is this household's own scene output. A
    message another family's scene sent *to* us is real inbound news and stays;
    a message our own scene authored is our own words and goes — which a type
    filter cannot tell apart, so we follow the lineage back to the scene that
    wrote it (87 of 92 authored messages in the soak were pure self-echo)."""
    since = max(0, (day - 1) * SECONDS_PER_DAY)
    out: list[str] = []
    own_scenes: set[int] = set()
    for e in log.events():
        p = e.payload
        if e.type in ("scene.morning", "scene.reaction") and p.get("household") == household_id:
            own_scenes.add(e.seq)  # recorded before the self-output skip below
        if e.sim_time < since or e.type in _ROUTINE_TYPES or e.type == "llm.response":
            continue
        if e.type in _SELF_OUTPUT_TYPES:
            continue
        if e.provenance == "llm_scene" and e.caused_by in own_scenes:
            continue  # our own words, coming back at us a day later
        if until is not None and e.sim_time >= until:
            continue
        touched = set()
        for key in ("person", "sender", "complainant", "victim"):
            if p.get(key):
                touched.add(p[key])
        touched.update(p.get("recipients", []) or [])
        touched.update(p.get("participants", []) or [])
        if p.get("entity_id"):
            touched.add(p["entity_id"])
        if household_id is not None and p.get("household") == household_id:
            touched |= member_ids
        if not touched & member_ids:
            continue
        line = _humanize(e.type, p, block, people)
        if line:
            out.append(f"- {_when(e.sim_time, day)}: {line}")
    return out[-limit:]


def witnessed_facts(
    log: EventLog,
    member_ids: set[str],
    day: int,
    block: Block,
    *,
    until: int | None = None,
    people: dict[str, Person] | None = None,
    limit: int = 6,
) -> list[str]:
    """What these people saw with their own eyes, and the hour they saw it.

    Deliberately NOT bounded by the recent-events window. A fire you watched on
    Friday afternoon is still a fixed fact on Wednesday — the soak's model, given
    only a vague memory and no timestamp, decided it had happened "at night",
    contradicting an event four of the family had personally witnessed."""
    out: list[str] = []
    for e in log.events(type="info.heard"):
        if until is not None and e.sim_time >= until:
            continue
        p = e.payload
        if p.get("channel") != "witness" or p.get("person") not in member_ids:
            continue
        out.append(
            f"- {_who(p.get('person', ''), people, block)} was there and saw it:"
            f" \"{p.get('claim', {}).get('text', '')}\" — {_when(e.sim_time, day)}"
        )
    return out[-limit:]


def memory_digest(
    log: EventLog,
    member_ids: set[str],
    day: int,
    block: Block,
    *,
    until: int | None = None,
    per_person: int = 3,
    people: dict[str, Person] | None = None,
) -> list[str]:
    """What this family carries from before — the ONLY way memory.formed
    re-enters a prompt.

    Read back deliberately and dated, rather than leaking in through RECENT
    EVENTS where it read as fresh news and got re-formed verbatim the next
    morning. Salience decays with age so a vivid day-3 memory cannot pin the
    digest for the rest of the month.
    """
    per: dict[str, list[tuple[float, int, str]]] = {}
    for e in log.events(type="memory.formed"):
        if until is not None and e.sim_time >= until:
            continue
        pid = e.payload.get("person")
        if pid not in member_ids:
            continue
        age = max(0, day - e.sim_time // SECONDS_PER_DAY)
        weight = float(e.payload.get("salience") or 0) * (0.93**age)
        per.setdefault(pid, []).append((weight, e.sim_time, e.payload.get("summary", "")))
    lines: list[str] = []
    for pid in sorted(per):
        seen: set[str] = set()
        keep: list[tuple[float, int, str]] = []
        for w, t, s in sorted(per[pid], key=lambda x: (-x[0], x[1])):
            if s and s not in seen:
                seen.add(s)
                keep.append((w, t, s))
            if len(keep) >= per_person:
                break
        name = _who(pid, people, block)
        for _w, t, s in sorted(keep, key=lambda x: x[1]):
            lines.append(f"- {name} — {_when(t, day)}: {s}")
    return lines


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
    memories: list[str] | None = None,
    witnessed: list[str] | None = None,
) -> list[dict]:
    lines = _card_lines(block, household, people, day)
    if witnessed:
        lines.append("WHAT THEY SAW THEMSELVES (fixed facts — the times below are exact):")
        lines.extend(witnessed)
        lines.append("")
    if memories:
        lines.append("WHAT THEY ALREADY CARRY (background — do not re-tell as if it were new):")
        lines.extend(memories)
        lines.append("")
    if recent:
        lines.append("RECENT EVENTS (the only things that have actually happened):")
        lines.extend(recent)
    else:
        lines.append("RECENT EVENTS: nothing new — an ordinary morning.")
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
    memories: list[str] | None = None,
    witnessed: list[str] | None = None,
) -> list[dict]:
    lines = _card_lines(block, household, people, day)
    if witnessed:
        lines.append("WHAT THEY SAW THEMSELVES (fixed facts — the times below are exact):")
        lines.extend(witnessed)
        lines.append("")
    if memories:
        lines.append("WHAT THEY ALREADY CARRY (background — do not re-tell as if it were new):")
        lines.extend(memories)
        lines.append("")
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
    people: dict[str, Person] | None = None,
) -> int:
    """Commit the scene and its consequences; returns the scene event seq."""
    # Referential integrity at the gate. Nothing validated the person ids a
    # scene returned, and the soak quietly accumulated messages addressed to
    # `person:colleague_yogita`, `person:Vinayak Mane` and `person:neighbor` —
    # people who do not exist. A dangling id is dropped and recorded, never
    # committed: the registry is canon, and a scene does not get to extend it.
    dropped: list[str] = []

    def known(pid: str | None) -> bool:
        if people is None or not pid:
            return True
        norm = pid if pid.startswith(("person:", "place:", "home:", "org:")) else f"person:{pid}"
        if norm in people or norm.startswith(("place:", "home:", "org:")):
            return True
        dropped.append(pid)
        return False

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
        if not known(m.person_id):
            continue
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
        if not known(md.person_id):
            continue
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
        recipients = [r for r in msg.recipients if known(r)]
        if not known(msg.sender) or (msg.recipients and not recipients):
            continue
        batch.append(
            EventIn(
                type="message.sent",
                sim_time=sim_time,
                payload={
                    "sender": msg.sender,
                    "recipients": recipients,
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
        if not known(c.entity_id):
            continue
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
    if dropped:
        batch.append(
            EventIn(
                type="scene.invalid_ref",
                sim_time=sim_time,
                payload={"household": household_id, "ids": sorted(set(dropped))},
                caused_by=scene_seq,
                provenance="system",
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
    from ..llm.gateway import CassetteMiss

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
        msgs = build_messages(block, hh, people, day, recent, memories, witnessed)
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
            people=people,
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
    members = set(household.member_ids)
    recent = recent_notable_events(
        log, members, day, block, until=now_abs, household_id=household.id, people=people
    )
    memories = memory_digest(log, members, day, block, until=now_abs, people=people)
    witnessed = witnessed_facts(log, members, day, block, until=now_abs, people=people)
    msgs = build_reaction_messages(
        block, household, people, day, recent, now_abs, memories, witnessed
    )
    res = gateway.call("scene", msgs, WorldDelta, temperature=0.6, max_tokens=2000, sim_time=now_abs)
    seq = apply_delta(
        log, canon, registry, res.parsed,
        household_id=household.id, sim_time=now_abs, event_type="scene.reaction",
        people=people,
    )
    return SceneResult(household_id=household.id, delta=res.parsed, scene_seq=seq)
