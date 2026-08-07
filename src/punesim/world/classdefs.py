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


@dataclass(frozen=True)
class ClassDef:
    type: str
    p_per_day: float
    window: tuple[int, int]  # seconds into the day
    shape: str
    predicate: str
    topics: tuple[str, ...]
    charge: float
    narratability: str = "full"
    provenance: str = "estimate"

    @property
    def narratable(self) -> bool:
        """May a scene be opened on this? `abstract` may be mentioned, not staged."""
        return self.narratability == "full"

    @property
    def countable_only(self) -> bool:
        return self.narratability == "numeric"


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
        w0, w1 = c["window"]
        out.append(ClassDef(
            type=c["type"], p_per_day=float(c["p_per_day"]),
            window=(_seconds(w0), _seconds(w1)), shape=c["shape"],
            predicate=c["predicate"], topics=tuple(c["topics"]),
            charge=float(c["charge"]),
            narratability=c.get("narratability", "full"),
            provenance=c.get("provenance", "estimate"),
        ))
    return out
