"""V0 daily schedules: per-person routines, jittered per (person, day) with
keyed draws, compiled to timed events. This is the cached-template half of the
routine-bypass gate — the whole city breathes at zero LLM cost; scenes only
*modify* these routines (V0 step 8+).
"""

from dataclasses import dataclass

from ..kernel.rng import keyed_rng
from ..kernel.timebase import SECONDS_PER_DAY
from ..population.synth import Person
from .block import Block, haversine_m

_H = 3600

# Trades that work a full day but have no fixed workplace in the block. Before
# V1.1 these people simply stayed home: they earned nothing (finances-lite pays
# daily wages only for a worked day), so their p_financial ratcheted forever,
# and they never met anyone outside their own house.
ROAMING_WORK = {"domestic_worker", "rickshaw_driver", "cook", "shop_assistant", "tailor"}
CLIENTS_PER_DOMESTIC = 2
NEAR_CLIENT_POOL = 12  # you work in your own lanes, not across the city
WORK_ACTIVITIES = ("work", "driving_rounds", "school")  # what counts as a worked day


def roaming_worksites(
    run_seed: int, block: Block, people: dict[str, Person]
) -> dict[str, tuple[str, ...]]:
    """Where the no-fixed-workplace trades actually spend their day.

    A domestic worker's day is *other people's houses* — which is exactly why
    they are a block's best-connected carriers of news: the information graph
    is built by who shares a room, not who shares a surname. Rickshaw drivers
    wait at the market; the rest fall back to the nearest plausible venue.
    Keyed per person, so adding this never perturbs anyone else's draws.
    """
    occupied = sorted({p.home_id for p in people.values()})
    out: dict[str, tuple[str, ...]] = {}
    for pid in sorted(people):
        p = people[pid]
        if p.work_id is not None or p.occupation not in ROAMING_WORK or not 18 <= p.age < 62:
            continue
        rng = keyed_rng(run_seed, "worksite", pid, 0, "assign")
        if p.occupation == "domestic_worker":
            src = block.get(p.home_id)
            pool = [h for h in occupied if h != p.home_id and block.get(h)]
            if src is not None:
                pool.sort(
                    key=lambda h: (haversine_m(src.lat, src.lon, block[h].lat, block[h].lon), h)
                )
            near = pool[:NEAR_CLIENT_POOL]
            picks: list[str] = []
            while near and len(picks) < CLIENTS_PER_DOMESTIC:
                picks.append(near.pop(int(rng.integers(0, len(near)))))
            out[pid] = tuple(picks)
        else:
            dest = block.nearest(p.home_id, "market", "shop", "restaurant", "bus_stop")
            out[pid] = (dest.id,) if dest else ()
    return out


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


def day_events(
    run_seed: int,
    person: Person,
    block: Block,
    day: int,
    worksites: tuple[str, ...] = (),
) -> list[TimedEvent]:
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

    if worksites:
        # a roaming trade's day: out to the client houses / the stand and back
        activity = "driving_rounds" if person.occupation == "rickshaw_driver" else "work"
        t = base + int(8.5 * _H) + int(rng.integers(-30, 31)) * 60
        current = home
        for site in worksites:
            if block.get(site) is None:
                continue
            t = _trip(ev, block, person, t, current, site, "work")
            ev.append(
                TimedEvent(t, "activity.start", {"person": person.id, "at": site, "activity": activity})
            )
            current = site
            t += int(rng.integers(110, 205)) * 60  # a couple of hours per stop
        if current != home:
            _trip(ev, block, person, t, current, home, "return_home")
        return ev

    # homemaker / retired: morning errand or worship visit
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
