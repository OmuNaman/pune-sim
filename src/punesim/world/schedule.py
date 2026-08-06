"""V0 daily schedules: per-person routines, jittered per (person, day) with
keyed draws, compiled to timed events. This is the cached-template half of the
routine-bypass gate — the whole city breathes at zero LLM cost; scenes only
*modify* these routines (V0 step 8+).
"""

from dataclasses import dataclass

from ..kernel.rng import keyed_rng
from ..kernel.timebase import SECONDS_PER_DAY
from ..population.synth import Person
from .block import Block

_H = 3600


@dataclass(frozen=True)
class TimedEvent:
    sim_time: int
    type: str
    payload: dict
    caused_by: int | None = None  # seq of the causing event (injection lineage)


def _trip(events: list[TimedEvent], block: Block, person: Person, t: int, frm: str, to: str, purpose: str) -> int:
    """Append a walk trip; returns arrival time."""
    dur = block.walk_seconds(frm, to)
    events.append(
        TimedEvent(t, "trip.start", {"person": person.id, "from": frm, "to": to, "mode": "walk", "purpose": purpose})
    )
    events.append(TimedEvent(t + dur, "trip.end", {"person": person.id, "at": to, "purpose": purpose}))
    return t + dur


def day_events(run_seed: int, person: Person, block: Block, day: int) -> list[TimedEvent]:
    """One person's clockwork day, in absolute sim seconds."""
    base = day * SECONDS_PER_DAY
    rng = keyed_rng(run_seed, "schedule", person.id, day, "jitter")
    ev: list[TimedEvent] = []
    home = person.home_id

    if person.occupation == "infant":
        return ev

    if person.occupation == "student" and person.work_id:
        depart = base + int(7.25 * _H) + int(rng.integers(-15, 21)) * 60
        arrive = _trip(ev, block, person, depart, home, person.work_id, "school")
        ev.append(TimedEvent(arrive, "activity.start", {"person": person.id, "at": person.work_id, "activity": "school"}))
        back = base + int(13.5 * _H) + int(rng.integers(0, 46)) * 60
        _trip(ev, block, person, back, person.work_id, home, "return_home")
        return ev

    if person.work_id is not None and person.occupation not in ("homemaker", "retired"):
        depart = base + 9 * _H + int(rng.integers(-45, 46)) * 60
        arrive = _trip(ev, block, person, depart, home, person.work_id, "work")
        ev.append(TimedEvent(arrive, "activity.start", {"person": person.id, "at": person.work_id, "activity": "work"}))
        back = base + int(18.5 * _H) + int(rng.integers(-30, 61)) * 60
        _trip(ev, block, person, back, person.work_id, home, "return_home")
        return ev

    # homemaker / retired / roaming work: morning errand or worship visit
    if rng.random() < 0.55:
        worship_kind = {
            "hindu": "temple",
            "muslim": "mosque",
            "christian": "church",
            "jain": "jain_temple",
            "buddhist_navayana": "vihara",
        }.get(person.religion, "temple")
        dest = (
            block.nearest(home, worship_kind, "temple")
            if rng.random() < 0.5
            else block.nearest(home, "shop", "market")
        )
        if dest is not None:
            out = base + 8 * _H + int(rng.integers(0, 90)) * 60
            arrive = _trip(ev, block, person, out, home, dest.id, "errand")
            stay = int(rng.integers(20, 61)) * 60
            ev.append(TimedEvent(arrive, "activity.start", {"person": person.id, "at": dest.id, "activity": "errand"}))
            _trip(ev, block, person, arrive + stay, dest.id, home, "return_home")
    if person.occupation == "rickshaw_driver":
        out = base + 9 * _H + int(rng.integers(0, 60)) * 60
        ev.append(TimedEvent(out, "activity.start", {"person": person.id, "at": home, "activity": "driving_rounds"}))
    return ev
