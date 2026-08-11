"""The one door to a run's world, and it always asks the log.

The old viewer took `--seed`, `--households` and `--block` from the command line
and rebuilt the population from those. Point it at an oldcity log with the
default flags and it does not crash: `hh:000` and `person:001.1` exist in every
world this repo can synthesize, so it prints a different family's name over the
right family's events and looks completely fine. That mistake has already cost
this repo a soak.

`world/roster.py` exists to prevent exactly that, and this module is the only
place in the API allowed to build a world. Nothing here accepts a seed.

Building a world is not free — 12,000 households is a few seconds of synthesis,
and the oldcity road graph is 438 Dijkstras on top. So worlds are cached by run,
and the road graph is left out until something actually asks for a route.
"""

from collections import OrderedDict
from dataclasses import dataclass
import threading

from ..population.synth import Household, Person
from ..viewer.logview import LogView
from ..world.block import Block, load_for
from ..world.roster import read_meta, world_for_log
from .readlog import ReadOnlyLog

CACHED_WORLDS = 2  # each is ~750 MB at V3 scale; two is a working set, not a store


@dataclass
class RunWorld:
    """Everything a read endpoint needs about one run."""

    block: Block
    households: list[Household]
    people: dict[str, Person]
    meta: dict
    view: LogView
    order: list[str]  # person ids, sorted — the ordinal index the binary positions use

    def __post_init__(self) -> None:
        self.person_names = {p.id: p.name for p in self.people.values()}
        self.place_names = {p.id: (p.name or p.kind)
                            for p in [*self.block.places, *self.block.homes]}
        self.hh_members = {h.id: list(h.member_ids) for h in self.households}
        self.ordinal = {pid: i for i, pid in enumerate(self.order)}

    @property
    def routed(self) -> bool:
        return self.block.roads is not None


class WorldCache:
    def __init__(self, max_worlds: int = CACHED_WORLDS):
        self._worlds: OrderedDict[str, RunWorld] = OrderedDict()
        self._meta: dict[str, dict] = {}
        self._building: set[str] = set()
        self._lock = threading.Lock()
        self.max_worlds = max_worlds

    def meta_only(self, run_id: str, db_path: str) -> dict:
        """The log's run.meta, without building a world.

        Worth having separately because a world is 13 seconds at V3 scale —
        3.8 s to synthesize 49,578 people, 1.3 s for the road graph, and a first
        day of movement on top — and the header line needs none of it. The map
        can draw the city and say how many people are in it while they are
        still being made.
        """
        hit = self._meta.get(run_id)
        if hit is None:
            hit = self._meta[run_id] = read_meta(ReadOnlyLog(db_path)) or {}
        return hit

    def cached(self, run_id: str) -> RunWorld | None:
        """The world if it is already built, else None. Never blocks."""
        return self._worlds.get(run_id)

    def building(self, run_id: str) -> bool:
        return run_id in self._building

    def get(self, run_id: str, db_path: str, *, roads: bool = False) -> RunWorld:
        """The world this log was written against.

        `roads=False` by default: the walking graph costs seconds to prepare and
        only the route endpoint needs it. Asking for roads on a cached roadless
        world rebuilds it once and keeps the routed one.
        """
        hit = self._worlds.get(run_id)
        if hit is not None and (not roads or hit.routed):
            self._worlds.move_to_end(run_id)
            return hit

        # One build per run, however many requests arrive at once. Without this
        # a page that fetches meta, people and positions together starts three
        # 13-second synthesises of the same 49,578 people.
        with self._lock:
            hit = self._worlds.get(run_id)
            if hit is not None and (not roads or hit.routed):
                self._worlds.move_to_end(run_id)
                return hit
            self._building.add(run_id)
            try:
                log = ReadOnlyLog(db_path)
                block, hhs, people, meta = world_for_log(log)
                if roads and block.roads is None:
                    # world_for_log builds the block through load_for, which
                    # routes only where the block is configured to; asking again
                    # with roads=True is the documented way (block.py:239).
                    block = load_for(len(hhs), meta.get("block", block.name), roads=True)
                world = RunWorld(
                    block=block, households=hhs, people=people, meta=meta,
                    view=LogView(db_path, block, people), order=sorted(people),
                )
            finally:
                self._building.discard(run_id)
            self._worlds[run_id] = world
            self._worlds.move_to_end(run_id)
            while len(self._worlds) > self.max_worlds:
                self._worlds.popitem(last=False)
            return world

    def drop(self, run_id: str) -> None:
        """Forget a world — after a run is deleted, or its log has grown."""
        self._worlds.pop(run_id, None)

    def refresh(self, run_id: str) -> None:
        """A live run has committed more; re-read the cheap summary.

        The world itself never changes during a run — the population is fixed at
        day 0 — so only the log view needs telling.
        """
        w = self._worlds.get(run_id)
        if w is not None:
            w.view.refresh()
