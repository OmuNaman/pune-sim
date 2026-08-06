"""T2 street talk: the day's one exchange that carried news between families.

The INFO lane already computes every face-to-face transmission mechanically —
who stood near whom for long enough, what they said, how much the listener
believed it. What it never did was let anyone *speak*. Households talked to
themselves in morning scenes and the rest of the block moved information in
silence, which is why a 30-day run contained no conversation at all between two
families.

So this renders exactly one: the day's most consequential hop that crossed a
household line. Not a new simulation lane — the transmission already happened
and is already committed; this is the camera turning to look at it. One LLM call
per day, chosen deterministically, and if the call fails the world is unchanged
because the information moved without it.

Choosing "most consequential" mechanically: charge x credence x whether the
listener was hearing it for the first time. That naturally selects the moment a
story jumped to a family that did not know it — which is the interesting
sentence in any day of gossip.
"""

from dataclasses import dataclass

from ..kernel.log import EventIn, EventLog
from ..kernel.timebase import to_datetime
from ..kernel.worlddelta import WorldDelta
from ..llm.gateway import CassetteMiss, Gateway
from ..population.synth import Person
from ..world.block import Block
from .info import Heard

SYSTEM = """You write ONE short exchange between two people who met in a lane, a shop, or a
courtyard in Pune's old city, and one of them passed on a piece of news.

You are given both people (name, age, occupation), where they are, the time, what was said, and
how much the listener ended up believing it. Write what that sounded like.

Rules:
- 4 to 8 lines, speaker labels are the given names you were given, never ids.
- Marathi/Hindi/English code-mix is natural and welcome.
- The listener's belief level is given: a 30% believer waves it off, a 90% believer is alarmed
  and asks for details. Show it in how they answer, not by stating a percentage.
- Never invent a third person with a name or an id, and never contradict either person's age or
  occupation. Two people met; that is all that happened.
- Neutral voice; never attribute behaviour or traits to any community; no slurs.
- Small texture is welcome (what they were carrying, the heat, a shop shutter) but nothing that
  changes the world: no new events, no plans, no injuries.

Output ONLY one JSON object, no extra fields:
{"narration": "one sentence setting the meeting",
 "transcript": "Name: line\\nName: line"}"""


@dataclass(frozen=True)
class Exchange:
    heard: Heard
    speaker: str
    listener: str
    place: str


def pick_exchange(
    heard: list[Heard],
    people: dict[str, Person],
    hh_of_person: dict[str, str],
    intervals: dict[str, list[tuple[str, int, int]]],
) -> Exchange | None:
    """The day's one worth watching: a face-to-face hop between two adults of
    different households, ranked by how much it mattered. Deterministic."""
    best: tuple[float, str, Exchange] | None = None
    for h in heard:
        if h.channel != "f2f":
            continue
        a, b = people.get(h.source), people.get(h.person)
        if a is None or b is None or a.age < 16 or b.age < 16:
            continue
        if hh_of_person.get(a.id) == hh_of_person.get(b.id):
            continue
        # where were they? the listener's span covering the moment
        place = ""
        for pl, t0, t1 in intervals.get(h.person, ()):
            if t0 <= h.sim_time <= t1:
                place = pl
                break
        if not place:
            continue
        weight = h.claim.charge * h.credence * (1.0 + 0.5 * (h.claim.hop <= 1))
        key = (weight, f"{h.sim_time}|{h.person}")
        if best is None or key > (best[0], best[1]):
            best = (weight, key[1], Exchange(h, a.id, b.id, place))
    return best[2] if best else None


def build_messages(
    ex: Exchange, people: dict[str, Person], block: Block
) -> list[dict]:
    a, b = people[ex.speaker], people[ex.listener]
    where = block.get(ex.place)
    lines = [
        f"WHERE: {where.name if where and where.name else 'a lane near their homes'}",
        f"WHEN: {to_datetime(ex.heard.sim_time):%A %d %B %Y, %H:%M}",
        "",
        f"SPEAKER : {a.name} ({a.age}, {a.occupation}) [{a.id}]",
        f"LISTENER: {b.name} ({b.age}, {b.occupation}) [{b.id}]",
        "",
        f'WHAT WAS PASSED ON: "{ex.heard.claim.text}"',
        f"THE LISTENER ENDS UP BELIEVING IT: {int(ex.heard.credence * 100)}%",
    ]
    if ex.heard.claim.ops:
        lines.append(
            f"(this version has already been through {len(ex.heard.claim.ops)} retelling(s) —"
            f" the speaker believes it as stated)"
        )
    lines += ["", "Write the exchange."]
    return [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": "\n".join(lines)},
    ]


def render_exchange(
    log: EventLog,
    gateway: Gateway,
    block: Block,
    people: dict[str, Person],
    ex: Exchange,
) -> int | None:
    """Commit the day's street talk. Returns the seq, or None if the call
    failed — the information moved with or without the camera."""
    msgs = build_messages(ex, people, block)
    try:
        res = gateway.call(
            "scene", msgs, WorldDelta, temperature=0.7, max_tokens=900,
            sim_time=ex.heard.sim_time,
        )
    except CassetteMiss:
        raise  # replay integrity is law 1
    except Exception as err:  # noqa: BLE001 — a missed conversation is not a broken day
        log.commit([EventIn(
            type="scene.skipped", sim_time=ex.heard.sim_time,
            payload={"lane": "talk", "people": [ex.speaker, ex.listener],
                     "reason": f"{type(err).__name__}: {err}"[:200]},
            provenance="system",
        )])
        return None
    return log.commit([EventIn(
        type="conversation.held",
        sim_time=ex.heard.sim_time,
        payload={
            "participants": [ex.speaker, ex.listener],
            "person": ex.listener,  # so it reaches the listener's own timeline
            "place": ex.place,
            "claim_key": ex.heard.claim.key,
            "credence": ex.heard.credence,
            "narration": res.parsed.narration,
            "transcript": res.parsed.transcript or "",
        },
        caused_by=ex.heard.caused_by,
        provenance="llm_scene",
    )])[0]
