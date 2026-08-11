from typing import TYPE_CHECKING

from ...institutions.catalog import DISCHARGE_HOUR_S
from ...kernel.log import EventLog
from ...kernel.timebase import SECONDS_PER_DAY, to_datetime
from ...population.synth import Household, Person
from ...world.block import Block
from .prompt import (
    _ROUTINE_TYPES,
    _SELF_OUTPUT_TYPES,
    PHYSICAL_HEADER,
    REACTION_TASK,
    SYSTEM,
)
from .render import _humanize, _when, _who

if TYPE_CHECKING:  # the scene lane reads institution state; it never writes it
    from ...institutions.procedures import ProcState


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
    # Scan back far enough to see our own earlier scenes (whose seqs the
    # lineage filter needs) without walking the whole month every render.
    for e in log.events(since_time=max(0, since - 3 * SECONDS_PER_DAY), until_time=until):
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
    contradicting an event four of the family had personally witnessed.

    The unbounded read looks like the quadratic-in-run-length bug this repo has
    fixed three times, and it is not — measured, because it is cheap to check
    and I nearly "fixed" it on the strength of the shape alone. A 30-day run at
    12,000 households holds **75,042** info.heard events in total, so a full
    scan costs 0.1s and the ~420 calls a whole run makes add ~42 seconds. The
    info lane emits per *transmission*, not per co-presence window, and 16% of
    those are witness rows. If a future change makes hearings grow with
    population the way windows do, filter `$.channel` in SQL and this becomes
    0.1s again; until then the bound would buy nothing and cost the semantics
    above."""
    # One line per thing seen, not per witness: four family members watching the
    # same fire produced four near-identical lines, and a prompt full of near-
    # identical lines is exactly what teaches a model to repeat itself.
    seen: dict[str, tuple[int, str, list[str]]] = {}
    for e in log.events(type="info.heard", until_time=until):
        p = e.payload
        if p.get("channel") != "witness" or p.get("person") not in member_ids:
            continue
        key = p.get("claim_key") or p.get("claim", {}).get("key", "")
        who = _who(p.get("person", ""), people, block)
        if key in seen:
            seen[key][2].append(who)
        else:
            seen[key] = (e.sim_time, p.get("claim", {}).get("text", ""), [who])
    out = [
        f"- {', '.join(names)} {'were' if len(names) > 1 else 'was'} there and saw it:"
        f' "{text}" — {_when(t, day)}'
        for t, text, names in sorted(seen.values())
    ]
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
    for e in log.events(type="memory.formed", until_time=until):
        pid = e.payload.get("person")
        if pid not in member_ids:
            continue
        age = max(0, day - e.sim_time // SECONDS_PER_DAY)
        weight = float(e.payload.get("salience") or 0) * (0.93**age)
        per.setdefault(pid, []).append((weight, e.sim_time, e.payload.get("summary", "")))
    lines: list[str] = []
    cutoff = (day - 1) * SECONDS_PER_DAY
    for pid in sorted(per):
        seen: set[str] = set()
        keep: list[tuple[float, int, str]] = []
        fresh = 0
        for w, t, s in sorted(per[pid], key=lambda x: (-x[0], x[1])):
            if not s or s in seen:
                continue
            # At most one memory from the last two days: what just happened is
            # already in RECENT EVENTS, and stacking it here is what made a
            # household re-live the same small incident three mornings running.
            if t >= cutoff:
                if fresh:
                    continue
                fresh += 1
            seen.add(s)
            keep.append((w, t, s))
            if len(keep) >= per_person:
                break
        name = _who(pid, people, block)
        for _w, t, s in sorted(keep, key=lambda x: x[1]):
            lines.append(f"- {name} — {_when(t, day)}: {s}")
    return lines


def _place_name(block: Block, place_id: str) -> str:
    p = block.get(place_id) if place_id else None
    return f"{p.name} [{place_id}]" if p is not None and p.name else (place_id or "the hospital")


def physical_state(
    proc: "ProcState",
    member_ids: set[str],
    day: int,
    block: Block,
    *,
    now_abs: int,
    people: dict[str, Person] | None = None,
) -> list[str]:
    """Where these bodies actually are today — the constraint no scene may write
    around.

    The 30-day soak at 12,000 households admitted a 10-year-old to a ward on day
    5 and kept him there until day 8, and on days 6, 7 and 8 his household's
    morning scene put him on the divan at home, in his mother's own words
    ("to aaj ghari visram karat ahe"). Those scenes then wrote day_plan
    overrides that the engine committed as real `activity.start` events, so the
    log itself said an admitted patient was at home.

    Nothing was wrong with the engine's bookkeeping: `_apply_stays` bends the
    clockwork off `proc.in_hospital` correctly, and scene-revised plans beat it
    by design. What was wrong is that the ward existed only in `ProcState` and
    never in the prompt — `RECENT EVENTS` reaches back one day, so the admission
    fell out of view on day 7 and the model, told nothing, wrote the plausible
    domestic morning. The fix is not a veto in the compiler; it is telling the
    actors the fact, so they write the true thing themselves.

    The conditions here mirror `engine.bend._apply_stays` deliberately, including
    its precedence — hospital first, convalescence only for someone not in a ward
    — because a prompt that disagreed with the compiler would just be a second,
    politer contradiction. The one thing it adds is the discharge morning: at
    06:30 on the day of a 10:00 discharge the patient is still in the ward, which
    is the case that produced "Suhas is still resting his leg on the divan" on
    the very morning he was in a hospital bed.
    """
    lines: list[str] = []
    for pid in sorted(member_ids):
        who = _who(pid, people, block)
        # Membership, not a (0, "") default: `day == until` is the discharge
        # morning, and a default of 0 makes day 0 the discharge morning for
        # every healthy person alive — which is exactly what it did the first
        # time this was written.
        stay = proc.in_hospital.get(pid)
        if stay is not None:
            until, place = stay
            if day < until:
                lines.append(
                    f"- {who} is in a hospital bed at {_place_name(block, place)}. Not at"
                    f" home, not at work or school: admitted until"
                    f" {to_datetime(until * SECONDS_PER_DAY):%A %d %B}."
                )
                continue
            if day == until and now_abs < day * SECONDS_PER_DAY + DISCHARGE_HOUR_S:
                lines.append(
                    f"- {who} is still in a hospital bed at {_place_name(block, place)}"
                    f" this morning — the discharge is at"
                    f" {to_datetime(day * SECONDS_PER_DAY + DISCHARGE_HOUR_S):%H:%M} today."
                )
                continue
        rest_until = proc.rest.get(pid, 0)
        if day < rest_until and day >= (stay[0] if stay is not None else 0):
            lines.append(
                f"- {who} is at home hurt, convalescing — not fit to go out or to work"
                f" until {to_datetime(rest_until * SECONDS_PER_DAY):%A %d %B}."
            )
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
    physical: list[str] | None = None,
) -> list[dict]:
    lines = _card_lines(block, household, people, day)
    if physical:
        lines.append(PHYSICAL_HEADER)
        lines.extend(physical)
        lines.append("")
    if witnessed:
        lines.append("WHAT THEY SAW THEMSELVES (fixed facts — the times below are exact):")
        lines.extend(witnessed)
        lines.append("")
    if memories:
        lines.append(
            "EARLIER MORNINGS (already written — today must be a DIFFERENT morning; "
            "do not replay these beats or restate them as new):"
        )
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
    physical: list[str] | None = None,
) -> list[dict]:
    lines = _card_lines(block, household, people, day)
    if physical:
        lines.append(PHYSICAL_HEADER)
        lines.extend(physical)
        lines.append("")
    if witnessed:
        lines.append("WHAT THEY SAW THEMSELVES (fixed facts — the times below are exact):")
        lines.extend(witnessed)
        lines.append("")
    if memories:
        lines.append(
            "EARLIER MORNINGS (already written — today must be a DIFFERENT morning; "
            "do not replay these beats or restate them as new):"
        )
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
