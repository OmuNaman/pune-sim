"""Event classes as data (architecture §2, EVENTS: "new event types are data, not code").

A hazard class used to be a tuple in a Python list. Adding one meant editing
code, and the tuple had nowhere to say the two things that matter most about a
class and are not mechanics: where its rate came from, and whether a scene may
be opened on it at all.

`narratability` is the second of those, and it is a safety rule rather than a
preference. NCRB calibration will generate classes — suicides, domestic
violence, crimes against children — that the sim must be able to count without
ever staging. `numeric` means exactly that: it happens, it is in the log, and no
scene opens on it however hard attention is pointed at it.
"""

from dataclasses import dataclass
from pathlib import Path

import orjson

DEFAULT_PATH = "data/classdefs/hazards.json"
NARRATABILITY = ("full", "abstract", "numeric")
SHAPES = ("point", "area")
DAYS_PER_YEAR = 365.0

# The population the estimate-only rates are anchored at: the V3 four-peth block
# at 12,000 households. Those classes have no city table behind them, so their
# level is the old absolute setting held at the largest world we have actually
# run — not a measurement, and `provenance` says so. Only the reference matters
# for them; the shape is per-capita for every class alike.
REFERENCE_POPULATION = 49_578


@dataclass(frozen=True)
class ClassDef:
    type: str
    rate_per_1k_per_year: float
    window: tuple[int, int]  # seconds into the day
    shape: str
    predicate: str
    topics: tuple[str, ...]
    charge: float
    narratability: str = "full"
    provenance: str = "estimate"

    def expected_per_day(self, population: int) -> float:
        """Poisson mean for one day in a world of `population` people.

        This is the whole of the per-capita fix: a rate is a property of a
        population, so a world twice the size has twice the trouble. It used to
        be an absolute `p_per_day`, which made the same 0.25 hazards a day fall
        on 306 people and on 49,578 — 298 per 1,000 per year against 1.84."""
        return self.rate_per_1k_per_year * population / 1000.0 / DAYS_PER_YEAR

    @property
    def narratable(self) -> bool:
        """May a scene be opened on this? `abstract` may be mentioned, not staged."""
        return self.narratability == "full"

    @property
    def countable_only(self) -> bool:
        return self.narratability == "numeric"

    @property
    def measured(self) -> bool:
        """Is the rate from a source, or is it a number somebody liked?"""
        return not self.provenance.startswith("estimate")


def _seconds(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 3600 + int(m) * 60


def load(path: str | Path = DEFAULT_PATH) -> list[ClassDef]:
    """Ordered class definitions. Order fixes the sequence of keyed draws in
    `hazards.sample_day`, and therefore the determinism hash — so the file's
    order is part of the world, not a presentation detail."""
    raw = orjson.loads(Path(path).read_bytes())
    out: list[ClassDef] = []
    for c in raw["classes"]:
        if c["shape"] not in SHAPES:
            raise ValueError(f"{c['type']}: shape {c['shape']!r} not in {SHAPES}")
        if c.get("narratability", "full") not in NARRATABILITY:
            raise ValueError(
                f"{c['type']}: narratability {c['narratability']!r} not in {NARRATABILITY}"
            )
        if "p_per_day" in c:
            raise ValueError(
                f"{c['type']}: p_per_day is gone — hazard rates are per-capita now. "
                "Give rate_per_1k_per_year (incidents per 1,000 people per year) and "
                "a provenance saying where it came from."
            )
        if float(c["rate_per_1k_per_year"]) <= 0:
            raise ValueError(f"{c['type']}: rate_per_1k_per_year must be positive")
        w0, w1 = c["window"]
        out.append(ClassDef(
            type=c["type"], rate_per_1k_per_year=float(c["rate_per_1k_per_year"]),
            window=(_seconds(w0), _seconds(w1)), shape=c["shape"],
            predicate=c["predicate"], topics=tuple(c["topics"]),
            charge=float(c["charge"]),
            narratability=c.get("narratability", "full"),
            provenance=c.get("provenance", "estimate"),
        ))
    return out
