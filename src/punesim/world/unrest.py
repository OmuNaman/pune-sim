"""V2-minimal collective dynamics (09-collective-dynamics, build note).

One instance to de-risk the tier-2 scene quality early: a fear zone, a
Granovetter threshold mobilization, and a scripted police response. No riot
special-case engine — an unrest injection is data like everything else:

- the event seeds claims through the ordinary percept lane (witnesses talk);
- a SMALL crowd mobilizes by threshold cascade: each adult has a keyed
  personal threshold; people join when enough others already have (classic
  Granovetter 1978) — most people's thresholds are never reached, which IS
  the differentiated mass behavior: a handful participate, the block
  shelters;
- police deploy on crowd size; a curfew zone follows severity, and while a
  zone is active, households inside it stay home (essential occupations
  exempt) — the shelter side of the same coin.

Identity note (08-identity): mobilization here is mechanically NEUTRAL —
thresholds come from keyed draws and traits, never from religion or caste.
Identity conditions structure (who lives where, who believes what); the
scene layer renders the humanity; the mechanical layer never selects
participants by community.
"""

from dataclasses import dataclass, field

from ..kernel.rng import keyed_rng
from ..minds.info import traits
from ..population.synth import Person
from .block import Block, haversine_m

ZONE_RADIUS_M = 260.0  # a fear zone covers a couple of lanes
CROWD_FOR_POLICE = 3  # a knot this size at a tense flashpoint brings a patrol
CURFEW_SEVERITY = 0.6  # zones at/above this get a curfew and shelter days
ESSENTIAL = {"police_constable", "doctor", "nurse"}
MOBILIZE_ROUNDS = 8
VOLATILE_FRACTION = 0.08  # Granovetter's instigator tail: the near-zero few


@dataclass(frozen=True)
class Zone:
    place: str
    until_day: int  # exclusive
    level: float  # severity of the standing fear


@dataclass
class UnrestState:
    zones: list = field(default_factory=list)  # active fear/curfew zones

    def active(self, day: int) -> list:
        return [z for z in self.zones if day < z.until_day]


def personal_threshold(run_seed: int, pid: str) -> float:
    """Granovetter threshold: the fraction of others already in the street
    this person needs to see before joining. A MIXTURE, per the 1978 model's
    core insight: cascades exist because a volatile few have near-zero
    thresholds — everyone else needs a crowd that usually never comes."""
    rng = keyed_rng(run_seed, "unrest", pid, 0, "threshold")
    u1, u2 = (float(x) for x in rng.random(2))
    tr = traits(run_seed, pid)
    if u1 < VOLATILE_FRACTION:
        return round(0.02 + 0.12 * u2, 4)  # the instigator tail
    return round(min(1.0, 0.35 + 0.65 * (0.5 * u2 + 0.5 * tr.conscientiousness)), 4)


def mobilize(
    run_seed: int,
    place_id: str,
    t_abs: int,
    severity: float,
    block: Block,
    people: dict[str, Person],
    intervals: dict[str, list[tuple[str, int, int]]],
) -> list[str]:
    """Threshold cascade among adults near the flashpoint. Returns the crowd
    (usually a small minority; often nobody at low severity)."""
    src = block.get(place_id)
    if src is None:
        return []
    candidates: list[str] = []
    for pid in sorted(intervals):
        p = people.get(pid)
        if p is None or p.age < 16 or p.occupation in ESSENTIAL:
            continue
        for pl, t0, t1 in intervals[pid]:
            if t1 < t_abs - 3600 or t0 > t_abs + 3600:
                continue
            here = block.get(pl)
            if here and haversine_m(src.lat, src.lon, here.lat, here.lon) <= ZONE_RADIUS_M:
                candidates.append(pid)
                break
    if not candidates:
        return []
    thresholds = {pid: personal_threshold(run_seed, pid) for pid in candidates}
    frac = 0.18 * severity  # the agitation seed: what the flashpoint itself supplies
    joined: set[str] = set()
    for _ in range(MOBILIZE_ROUNDS):
        joined = {pid for pid in candidates if thresholds[pid] <= frac}
        new_frac = len(joined) / len(candidates)
        if new_frac <= frac:
            break
        frac = new_frac
    return sorted(joined)


def zone_shelters(
    state: UnrestState,
    day: int,
    block: Block,
    people: dict[str, Person],
) -> dict[str, str]:
    """Who stays home today because a zone is active near their home or work.
    Returns {person: zone_place}. Essential occupations keep moving."""
    out: dict[str, str] = {}
    zones = state.active(day)
    if not zones:
        return out
    for pid in sorted(people):
        p = people[pid]
        if p.occupation in ESSENTIAL:
            continue
        for z in zones:
            src = block.get(z.place)
            if src is None:
                continue
            for ref in (p.home_id, p.work_id):
                if ref is None:
                    continue
                here = block.get(ref)
                if here and haversine_m(src.lat, src.lon, here.lat, here.lon) <= ZONE_RADIUS_M:
                    out[pid] = z.place
                    break
            if pid in out:
                break
    return out
