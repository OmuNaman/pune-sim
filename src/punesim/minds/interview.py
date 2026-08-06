"""Interview = time bubble (07-interface §4, journalist mode).

The clock pauses; the person answers a stranger's questions grounded in their
card, memories, and logged timeline. The conversation itself becomes canon
(ConversationHeld + a memory), so people remember being interviewed.
"""

from ..kernel.log import EventIn, EventLog
from ..kernel.timebase import to_datetime
from ..llm.gateway import Gateway
from ..minds.scene import _ROUTINE_TYPES, _humanize
from ..population.synth import Person
from ..world.block import Block

SYSTEM = """You are roleplaying one fictional resident of Pune's old city in a life simulation.
Answer AS the person, first person, in their natural voice (Marathi/Hindi/English code-mix
welcome; a 10-year-old sounds like a child, an 80-year-old like an elder). Ground every answer
in the PERSON CARD, MEMORIES, and TIMELINE — if you don't know something, the person doesn't
know it either. A polite stranger (a journalist) is asking; be natural: warm, guarded, chatty,
or brief as fits the person. Reply with the person's spoken words only — no JSON, no narration."""


def interview(
    log: EventLog,
    gateway: Gateway,
    block: Block,
    people: dict[str, Person],
    person_id: str,
    question: str,
    *,
    ghost: bool = False,
) -> str:
    person = people[person_id]

    def place_name(pid: str) -> str:
        p = block.get(pid)
        return p.name if p and p.name else pid

    memories: list[str] = []
    timeline: list[str] = []
    last_t = 0
    for e in log.events():
        last_t = max(last_t, e.sim_time)
        p = e.payload
        if e.type == "memory.formed" and p.get("person") == person_id:
            memories.append(f"- {p.get('summary', '')}")
            continue
        touched = {p.get("person"), p.get("sender"), *(p.get("recipients") or []), *(p.get("participants") or [])}
        if person_id not in touched:
            continue
        when = to_datetime(e.sim_time).strftime("%a %H:%M")
        if e.type in _ROUTINE_TYPES:
            if e.type == "activity.start":
                timeline.append(f"- {when}: {p.get('activity')} at {place_name(p.get('at', ''))}")
        else:
            line = _humanize(e.type, p, block, people)
            if line:
                timeline.append(f"- {when}: {line}")

    card = [
        f"PERSON CARD: {person.name}, {person.age}, {person.occupation}; "
        f"household {person.household_id}; home {place_name(person.home_id)}"
        + (f"; goes to {place_name(person.work_id)}" if person.work_id else ""),
        "",
        "MEMORIES:" if memories else "MEMORIES: (none recorded)",
        *memories[-10:],
        "",
        "TIMELINE (recent):",
        *timeline[-20:],
        "",
        f'The stranger asks: "{question}"',
    ]
    msgs = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": "\n".join(card)}]
    res = gateway.call("focal_turn", msgs, None, temperature=0.7, max_tokens=900, sim_time=last_t)

    if not ghost:
        seqs = log.commit(
            [
                EventIn(
                    type="conversation.held",
                    sim_time=last_t,
                    payload={"person": person_id, "with": "journalist", "question": question, "answer": res.raw},
                    provenance="user",
                )
            ]
        )
        log.commit(
            [
                EventIn(
                    type="memory.formed",
                    sim_time=last_t,
                    payload={
                        "person": person_id,
                        "salience": 0.3,
                        "summary": "A polite stranger asked me about my life and recent days.",
                    },
                    caused_by=seqs[0],
                    provenance="user",
                )
            ]
        )
    return res.raw
