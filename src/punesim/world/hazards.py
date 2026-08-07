"""V1 thin hazards: un-injected trouble, sampled by keyed draws (04-events).

The world produces its own ripples: a keyed Bernoulli per hazard class per day
decides whether something happens, where, when, and to whom — no scenario
code, no LLM. Each realized hazard reuses the same stub-institution machinery
as user injections, and seeds percepts in tiers (witness / nearby / word of
mouth) so the block *hears about it* through the INFO lane rather than by
narrator fiat.
"""

from dataclasses import dataclass, replace

from ..kernel.rng import keyed_rng
from ..kernel.timebase import SECONDS_PER_DAY
from ..minds.info import WITNESS_CREDENCE, Claim, render_text
from ..population.synth import Person
from .block import Block, haversine_m

# (type, p_per_day, window_s, shape, predicate, topics, charge)
CLASSES: list[tuple[str, float, tuple[int, int], str, str, tuple[str, ...], float]] = [
    ("hazard.road.collision", 0.10, (8 * 3600, 20 * 3600), "point", "collision", ("safety",), 0.85),
    ("hazard.water.supply_cut", 0.06, (6 * 3600, 9 * 3600), "area", "supply_cut", ("water",), 0.6),
    ("hazard.power.outage", 0.07, (10 * 3600, 22 * 3600), "area", "outage", ("power",), 0.35),
    ("hazard.fire.small", 0.02, (9 * 3600, 23 * 3600), "point", "fire", ("safety",), 0.9),
]

NEARBY_M = 220.0  # you notice the commotion from a couple of lanes away
AREA_M = 320.0  # an area hazard (water cut, outage) covers homes within this
WITNESS_PAD_S = 30 * 60  # present within half an hour of it counts as seeing it
MIN_AUDIENCE = 3  # fewer perceivers than this and it is a tree falling in no forest


@dataclass(frozen=True)
class Hazard:
    type: str
    day: int
    t_abs: int
    place: str
    shape: str  # 'point' | 'area'
    predicate: str
    topics: tuple[str, ...]
    charge: float
    severity: float
    participants: tuple[str, ...]  # directly struck (point hazards only)


def sample_day(
    run_seed: int,
    day: int,
    block: Block,
    people: dict[str, Person],
    intervals: dict[str, list[tuple[str, int, int]]],
) -> list[Hazard]:
    """Keyed draws only — adding or removing one hazard class never perturbs
    another's realizations (law 4).

    The hour is drawn BEFORE the venue, because who is out and about depends on
    the time: the 06:00-09:00 water window and the 10:00-22:00 outage window
    have completely different populated cores, and a static count of nearby
    homes cannot tell them apart. A venue with nobody to perceive it is not a
    hazard, it is a tree falling in no forest — the soak's day-9 water cut hit
    a school whose 320 m catchment held one home with nobody in it, and
    produced zero percepts and zero conversation."""
    out: list[Hazard] = []
    named = sorted((p for p in block.places if p.name), key=lambda p: p.id)
    if not named:
        return out
    for cls, rate, (w0, w1), shape, predicate, topics, charge in CLASSES:
        rng = keyed_rng(run_seed, "hazard", cls, day, "realize")
        if rng.random() >= rate:
            continue
        t_abs = day * SECONDS_PER_DAY + w0 + int(rng.integers(0, max(1, (w1 - w0) // 60))) * 60
        live = [
            p for p in named
            if len(witness_tiers(p.id, t_abs, shape, block, people, intervals)) >= MIN_AUDIENCE
        ]
        if not live:
            continue  # nobody would perceive it; do not fabricate a hazard
        place = live[int(rng.integers(0, len(live)))]
        severity = 0.25 + rng.random() * 0.5
        participants: tuple[str, ...] = ()
        if shape == "point" and cls == "hazard.road.collision":
            present = [
                pid for pid in sorted(intervals)
                if people[pid].age >= 6 and any(
                    pl == place.id and t0 - 900 <= t_abs <= t1 + 900
                    for pl, t0, t1 in intervals[pid]
                )
            ]
            if present:
                n = min(len(present), 1 + int(rng.integers(0, 2)))
                participants = tuple(present[:n])
        out.append(
            Hazard(cls, day, t_abs, place.id, shape, predicate, topics, charge,
                   round(severity, 2), participants)
        )
    return out


def hazard_claim(hz_type: str, place: str, day: int, predicate: str,
                 topics: tuple[str, ...], charge: float, block: Block,
                 quantity: float | None = None) -> Claim:
    """The true origin claim a hazard seeds into the INFO lane."""
    c = Claim(
        key=f"cl:{hz_type.split('.', 1)[1]}:{place}:d{day}",
        subject=place, predicate=predicate, text="",
        quantity=quantity, unit="people" if quantity else None,
        valence=-0.6, charge=charge, specificity=0.85, veracity="true",
        topics=topics,
    )
    return replace(c, text=render_text(c, block))


def witness_tiers(
    place_id: str,
    t_abs: int,
    shape: str,
    block: Block,
    people: dict[str, Person],
    intervals: dict[str, list[tuple[str, int, int]]],
    exclude: tuple[str, ...] = (),
) -> list[tuple[str, float]]:
    """(person, specificity) pairs for who perceives it directly.
    Tier 1 — at the spot: full-specificity account.
    Tier 2 — a couple of lanes away (point) or home in the area (area):
    lower-specificity 'heard the commotion / tap ran dry' account."""
    src = block.get(place_id)
    if src is None:
        return []
    lo, hi = t_abs - WITNESS_PAD_S, t_abs + WITNESS_PAD_S
    out: list[tuple[str, float]] = []
    # A block has ~100 places and, at peth scale, tens of thousands of spans.
    # Measuring the same handful of distances once per span cost 870k haversines
    # in a 3-day probe; there are only ever as many answers as there are places.
    dist: dict[str, float | None] = {}
    for pid in sorted(intervals):
        if pid in exclude or people[pid].age < 6:
            continue
        best = 0.0
        for pl, t0, t1 in intervals[pid]:
            if t1 < lo or t0 > hi:
                continue
            if pl not in dist:
                here = block.get(pl)
                dist[pl] = None if here is None else haversine_m(src.lat, src.lon, here.lat, here.lon)
            d = dist[pl]
            if d is None:
                continue
            if pl == place_id:
                best = max(best, 0.85)
            elif shape == "point" and d <= NEARBY_M:
                best = max(best, 0.55)
            elif shape == "area" and d <= AREA_M:
                best = max(best, 0.7)  # they experience it, wherever they are in the area
        if best > 0:
            out.append((pid, best))
    return out


def witness_credence(specificity: float) -> float:
    """Direct experience convinces; a glimpse convinces a bit less."""
    return WITNESS_CREDENCE if specificity >= 0.8 else 0.75
