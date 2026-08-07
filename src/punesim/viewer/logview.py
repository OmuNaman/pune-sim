"""The viewer's window onto a log, without holding the log.

The first version read every event into a list and precomputed every person's
movement for the whole run. At 80 households that is a few megabytes. At V3
scale — 12,000 households, 30 days, 6.8M events — it is about 7.6 GB of parsed
payloads plus nine million movement segments, so `punesim serve` could not open
the very runs V3 exists to produce.

Almost all of that is routine: 6.77M of those 6.83M events are somebody walking
somewhere. Nothing needs them all at once. The map shows one moment, so
movement is built one day at a time and a couple of days are kept; everything
else — the ticker, the scenes, the rumour families — is what remains once
routine is excluded, and that is small enough to read on demand.
"""

import sqlite3
from collections import OrderedDict
from dataclasses import dataclass

import orjson

from ..kernel.timebase import SECONDS_PER_DAY
from ..world.block import Block

ROUTINE = ("trip.start", "trip.end", "activity.start")
# Excluded from the ticker: movement (its own layer), gossip (the Rumors tab),
# raw model calls, and the log's note to itself.
_NOT_NOTABLE = (*ROUTINE, "llm.response", "fact.established", "fact.superseded",
                "info.heard", "run.meta")
DAYS_CACHED = 3


@dataclass
class Seg:
    t0: int
    t1: int
    kind: str  # 'at' | 'transit'
    a: str
    b: str | None
    activity: str | None


@dataclass
class Row:
    """One event, as the viewer needs it — the log's Event without the import."""

    seq: int
    sim_time: int
    type: str
    payload: dict
    caused_by: int | None
    provenance: str


class LogView:
    def __init__(self, db_path: str, block: Block, people: dict):
        self.db_path = db_path
        self.block = block
        self.people = people
        self._days: OrderedDict[int, dict[str, list[Seg]]] = OrderedDict()
        self.refresh()

    # -- plumbing --------------------------------------------------------- #

    def _con(self) -> sqlite3.Connection:
        return sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)

    def _rows(self, where: str, args: tuple, branch: int = 0) -> list[Row]:
        """Rows for one branch, filtered by `where`.

        `branch_id` is applied in Python rather than in SQL on purpose: naming
        it in the WHERE clause makes SQLite prefer the (branch_id, sim_time)
        index and scan the table, which costs 0.89s per query on a 6.8M-event
        log against 0.00s on the type index. Logs written before
        `ev_branch_type` existed have no index that serves both.
        """
        con = self._con()
        try:
            raw = con.execute(
                "SELECT seq, sim_time, type, payload, caused_by, provenance, branch_id "
                f"FROM event WHERE {where} ORDER BY seq", args,
            ).fetchall()
        finally:
            con.close()
        return [Row(r[0], r[1], r[2], orjson.loads(r[3]), r[4], r[5])
                for r in raw if r[6] == branch]

    def refresh(self) -> None:
        """Re-read the cheap summary; called after anything writes to the log."""
        con = self._con()
        try:
            self.n_events, self.max_t = con.execute(
                "SELECT COUNT(*), COALESCE(MAX(sim_time), 0) FROM event WHERE branch_id = 0"
            ).fetchone()
        finally:
            con.close()
        self._days.clear()
        self._det_hash: str | None = None
        self._types: list[str] | None = None

    def cached_hash(self) -> str:
        """The hash if it has already been folded, else empty — never blocks."""
        return self._det_hash or ""

    def det_hash(self) -> str:
        """Folded lazily: it walks the whole log, which is seconds at V3 scale
        and pointless on every page load."""
        if self._det_hash is None:
            from ..kernel.log import EventLog

            log = EventLog(self.db_path)
            self._det_hash = log.determinism_hash()
            log.close()
        return self._det_hash

    # -- movement, one day at a time -------------------------------------- #

    def segs_for_day(self, day: int) -> dict[str, list[Seg]]:
        """Where everyone was, through one day.

        Safe to build per day because everyone starts and ends the day at home —
        the same invariant `info.presence_intervals` is built on. Without it
        this would have to replay from day zero to know where anyone is.
        """
        hit = self._days.get(day)
        if hit is not None:
            self._days.move_to_end(day)
            return hit
        t0, t1 = day * SECONDS_PER_DAY, (day + 1) * SECONDS_PER_DAY
        events = self._rows(
            "sim_time >= ? AND sim_time < ? AND type IN (?, ?, ?)", (t0, t1, *ROUTINE),
        )
        segs: dict[str, list[Seg]] = {}
        cur: dict[str, tuple[str, str | None]] = {}
        open_t: dict[str, int] = {}
        for e in events:
            pid = e.payload.get("person")
            p = self.people.get(pid)
            if p is None:
                continue
            if pid not in segs:
                segs[pid], cur[pid], open_t[pid] = [], (p.home_id, None), t0
            if e.type == "trip.start":
                at, act = cur[pid]
                segs[pid].append(Seg(open_t[pid], e.sim_time, "at", at, None, act))
                cur[pid] = (e.payload["to"], e.payload.get("purpose"))
                segs[pid].append(Seg(e.sim_time, -1, "transit", e.payload["from"],
                                     e.payload["to"], e.payload.get("purpose")))
                open_t[pid] = e.sim_time
            elif e.type == "trip.end":
                last = segs[pid][-1] if segs[pid] else None
                if last is not None and last.kind == "transit" and last.t1 == -1:
                    last.t1 = e.sim_time
                cur[pid] = (e.payload["at"], cur[pid][1])
                open_t[pid] = e.sim_time
            elif e.type == "activity.start":
                at, act = cur[pid]
                if e.sim_time > open_t[pid]:  # activities never bleed backward
                    segs[pid].append(Seg(open_t[pid], e.sim_time, "at", at, None, act))
                    open_t[pid] = e.sim_time
                cur[pid] = (e.payload.get("at", at), e.payload.get("activity"))
        for pid, (at, act) in cur.items():
            segs[pid].append(Seg(open_t[pid], t1, "at", at, None, act))
        self._days[day] = segs
        while len(self._days) > DAYS_CACHED:
            self._days.popitem(last=False)
        return segs

    def pos(self, pid: str, t: int):
        p = self.people.get(pid)
        if p is None:
            return None
        segs = self.segs_for_day(t // SECONDS_PER_DAY).get(pid)
        best = None
        for s in segs or ():
            if s.t0 <= t and (s.t1 == -1 or t < s.t1):
                best = s
        if best is None:  # nobody moved today: they are home
            pl = self.block.get(p.home_id)
            return (pl.lat, pl.lon, "at", p.home_id, None) if pl else None
        if best.kind == "at" or best.b is None:
            pl = self.block.get(best.a)
            return (pl.lat, pl.lon, "at", best.a, best.activity) if pl else None
        a, b = self.block.get(best.a), self.block.get(best.b)
        if not a or not b:
            return None
        span = (best.t1 if best.t1 > 0 else best.t0 + 600) - best.t0
        frac = min(max((t - best.t0) / max(1, span), 0.0), 1.0)
        return (a.lat + (b.lat - a.lat) * frac, a.lon + (b.lon - a.lon) * frac,
                "transit", best.b, best.activity)

    # -- everything that is not somebody walking --------------------------- #

    def types(self) -> list[str]:
        """Distinct event types. Cached: SELECT DISTINCT walks the whole type
        index, which is 12 seconds on a 6.8M-event log and constant per run."""
        if self._types is None:
            con = self._con()
            try:
                self._types = [r[0] for r in con.execute("SELECT DISTINCT type FROM event")]
            finally:
                con.close()
        return self._types

    def notable(self, limit: int = 4000) -> list[Row]:
        """The ticker's events: the log minus movement, gossip and bookkeeping.

        Asked as "these types" rather than "not those types", because a NOT IN
        can only be answered by reading every row — and movement is 99% of them.
        """
        wanted = [t for t in self.types() if t not in _NOT_NOTABLE]
        if not wanted:
            return []
        return self.of_type(*wanted, limit=limit)

    def of_type(self, *types: str, limit: int = 4000) -> list[Row]:
        holes = ",".join("?" * len(types))
        return self._rows(f"type IN ({holes})", types)[-limit:]

    def for_person(self, pid: str, types: tuple[str, ...], limit: int = 400) -> list[Row]:
        """One person's events of a few types — filtered in Python after a
        type-narrowed query, because the payload's person id is not a column."""
        out = [e for e in self.of_type(*types, limit=1_000_000)
               if e.payload.get("person") == pid
               or pid in (e.payload.get("participants") or ())
               or e.payload.get("to") == pid]
        return out[-limit:]
