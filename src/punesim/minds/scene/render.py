import re

from ...kernel.log import EventLog
from ...kernel.timebase import SECONDS_PER_DAY, to_datetime
from ...population.synth import Person
from ...world.block import Block


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


# A memory is read for weeks. "Last night" is true for one day and wrong
# forever after, and the 30-day re-soak caught exactly that: a power cut on
# Thursday night was still "kal raatri" in scenes on Friday, Saturday, Sunday
# and Monday, because each morning re-read a memory that said so. Relative time
# is rewritten to the day it actually means, at the moment the memory is
# written, in the languages the scenes are written in.
_RELATIVE_TIME = (
    # possessives first, or "yesterday's tiffin" becomes "on Thu 01 Jan's tiffin"
    (re.compile(r"\byesterday'?s\b", re.IGNORECASE), "{prev}'s"),
    (re.compile(r"\blast night'?s\b", re.IGNORECASE), "{prev} night's"),
    (re.compile(r"\btoday'?s\b", re.IGNORECASE), "{today}'s"),
    (re.compile(r"\blast night\b", re.IGNORECASE), "on {prev} night"),
    (re.compile(r"\byesterday\b", re.IGNORECASE), "on {prev}"),
    (re.compile(r"\bkal ratri\b|\bkal raatri\b|\bkalchi raat\b", re.IGNORECASE), "{prev} chya ratri"),
    # possessive before bare, or "last week's workbook" reads
    # "in the week before Tue 06 Jan's workbook"
    (re.compile(r"\blast week'?s\b", re.IGNORECASE), "the earlier week's"),
    (re.compile(r"\blast week\b", re.IGNORECASE), "in the week before {today}"),
    (re.compile(r"\btomorrow'?s\b", re.IGNORECASE), "{next}'s"),
    (re.compile(r"\btomorrow\b", re.IGNORECASE), "on {next}"),
    (re.compile(r"\btonight\b", re.IGNORECASE), "on {today} night"),
    (re.compile(r"\bthis morning\b", re.IGNORECASE), "on {today} morning"),
    (re.compile(r"\btoday\b", re.IGNORECASE), "on {today}"),
    (re.compile(r"\baaj\b", re.IGNORECASE), "{today} la"),
)


def _flatten(s: str) -> str:
    return " ".join((s or "").lower().split())


def absolutize(summary: str, sim_time: int) -> str:
    """Pin a memory's relative time words to the day they were written."""
    if not summary:
        return summary
    today = to_datetime(sim_time)
    prev = to_datetime(max(0, sim_time - SECONDS_PER_DAY))
    nxt = to_datetime(sim_time + SECONDS_PER_DAY)
    for rx, tmpl in _RELATIVE_TIME:
        summary = rx.sub(
            tmpl.format(
                prev=f"{prev:%a %d %b}", today=f"{today:%a %d %b}", next=f"{nxt:%a %d %b}"
            ),
            summary,
        )
    return summary


def held_memories(log: EventLog, member_ids: set[str], until: int | None = None) -> set[tuple[str, str]]:
    """(person, normalized summary) of everything they already remember."""
    # Key on what the model WROTE, not on what was stored. absolutize()
    # rewrites relative time at write time, so comparing against the stored
    # text would let a re-emitted sentence through the gate — and it would then
    # be committed pinned to the wrong day, which is worse than the drift the
    # rewrite exists to prevent.
    return {
        (
            e.payload.get("person", ""),
            e.payload.get("summary_key") or _flatten(e.payload.get("summary", "")),
        )
        for e in log.events(type="memory.formed", until_time=until)
        if e.payload.get("person") in member_ids
    }


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
        # Some payloads carry an unprefixed ref (plan.step_dropped writes the
        # bare OSM id), and an unresolved bare id in a prompt is exactly the
        # invitation _who exists to remove.
        for cand in (pid, f"place:{pid}", f"home:{pid}"):
            p = block.get(cand) if cand else None
            if p is not None and p.name:
                return f"{p.name} [{cand}]"
        return pid or "?"

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
    if e_type == "fact.established":
        return f"it is now known that {who(payload.get('subject', ''))} {payload.get('predicate', '')}: {payload.get('value', '')}"
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
        text = (payload.get("claim") or {}).get("text") or ""
        told = ", ".join(who(p) for p in payload.get("participants", []) or [])
        head = f'word is going round: "{text}"' if text else "word is going round"
        return head + (f" — {told} heard it first" if told else "")
    if e_type.startswith("hazard."):
        kind = e_type.split(".", 1)[1].replace(".", " ").replace("_", " ")
        hurt = ", ".join(who(p) for p in payload.get("participants", []) or [])
        return f"{kind} at {pname(payload.get('place', ''))}" + (f" — {hurt}" if hurt else "")
    return ""  # unknown to the humanizer = not shown; never dump a raw payload
