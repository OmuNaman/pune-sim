"""Law 1: the event log is truth.

One append-only, event-sourced log; world state and canon are deterministic
projections of it. Every LLM response is committed as an input-event
("recorded nondeterminism"), so replay is bit-exact without re-calling any
model. The determinism hash covers every committed field except `wall_meta`
(wall-clock noise) — two runs of the same seed must produce identical hashes.
"""

import sqlite3
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from hashlib import blake2s
from pathlib import Path

import orjson
from pydantic import BaseModel, Field

from .timebase import tick_of

LLM_RESPONSE = "llm.response"
FACT_ESTABLISHED = "fact.established"
FACT_SUPERSEDED = "fact.superseded"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS event (
  seq        INTEGER PRIMARY KEY,
  branch_id  INTEGER NOT NULL DEFAULT 0,
  sim_time   INTEGER NOT NULL,
  tick       INTEGER NOT NULL,
  type       TEXT    NOT NULL,
  payload    BLOB    NOT NULL,
  caused_by  INTEGER,
  provenance TEXT    NOT NULL,
  actor_ref  TEXT,
  wall_meta  BLOB
);
CREATE INDEX IF NOT EXISTS ev_time ON event(branch_id, sim_time);
CREATE INDEX IF NOT EXISTS ev_type ON event(type, sim_time);
"""


class EventIn(BaseModel):
    """An event to commit. `tick` is derived from sim_time at commit."""

    model_config = {"extra": "forbid"}

    type: str
    sim_time: int
    payload: dict = Field(default_factory=dict)
    caused_by: int | None = None
    provenance: str = "clockwork"
    actor_ref: str | None = None


@dataclass(frozen=True)
class Event:
    seq: int
    branch_id: int
    sim_time: int
    tick: int
    type: str
    payload: dict
    caused_by: int | None
    provenance: str
    actor_ref: str | None


class EventLog:
    """Sole writer of the event table (law 1 / ruling 3). Everyone else emits
    EventIn intents; only kernel code holds an EventLog and calls commit()."""

    def __init__(self, path: str | Path):
        self._conn = sqlite3.connect(str(path))
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)

    def close(self) -> None:
        self._conn.close()

    def commit(
        self,
        events: Sequence[EventIn],
        *,
        branch_id: int = 0,
        wall_meta: dict | None = None,
    ) -> list[int]:
        """Append events in one transaction; returns their seqs in order."""
        wm = orjson.dumps(wall_meta, option=orjson.OPT_SORT_KEYS) if wall_meta else None
        seqs: list[int] = []
        with self._conn:
            for e in events:
                cur = self._conn.execute(
                    "INSERT INTO event (branch_id, sim_time, tick, type, payload,"
                    " caused_by, provenance, actor_ref, wall_meta)"
                    " VALUES (?,?,?,?,?,?,?,?,?)",
                    (
                        branch_id,
                        e.sim_time,
                        tick_of(e.sim_time),
                        e.type,
                        orjson.dumps(e.payload, option=orjson.OPT_SORT_KEYS),
                        e.caused_by,
                        e.provenance,
                        e.actor_ref,
                        wm,
                    ),
                )
                seqs.append(cur.lastrowid)
        return seqs

    def record_llm_response(
        self,
        *,
        request_id: str,
        model: str,
        response_text: str,
        usage: dict,
        sim_time: int,
        caused_by: int | None = None,
    ) -> int:
        """Recorded nondeterminism: the model's reply becomes an input-event."""
        return self.commit(
            [
                EventIn(
                    type=LLM_RESPONSE,
                    sim_time=sim_time,
                    payload={
                        "request_id": request_id,
                        "model": model,
                        "response": response_text,
                        "usage": usage,
                    },
                    caused_by=caused_by,
                    provenance="llm",
                )
            ]
        )[0]

    def events(
        self,
        *,
        branch_id: int = 0,
        type: str | None = None,
        since_seq: int = 0,
        since_time: int | None = None,
        until_time: int | None = None,
    ) -> Iterator[Event]:
        """Ordered replay. The time bounds exist because scene context is built
        several times per household per day: without them a 30-day run
        deserializes the entire log on every scene, which is quadratic in the
        thing that grows fastest."""
        q = (
            "SELECT seq, branch_id, sim_time, tick, type, payload, caused_by,"
            " provenance, actor_ref FROM event WHERE branch_id=? AND seq>?"
        )
        args: list = [branch_id, since_seq]
        if type is not None:
            q += " AND type=?"
            args.append(type)
        if since_time is not None:
            q += " AND sim_time>=?"
            args.append(since_time)
        if until_time is not None:
            q += " AND sim_time<?"
            args.append(until_time)
        q += " ORDER BY seq"
        for row in self._conn.execute(q, args):
            yield Event(
                seq=row[0],
                branch_id=row[1],
                sim_time=row[2],
                tick=row[3],
                type=row[4],
                payload=orjson.loads(row[5]),
                caused_by=row[6],
                provenance=row[7],
                actor_ref=row[8],
            )

    def fold(self, fn: Callable[[Event], None], *, branch_id: int = 0) -> None:
        """Replay the log through a projector — canon and world state are folds."""
        for e in self.events(branch_id=branch_id):
            fn(e)

    def determinism_hash(self, *, branch_id: int = 0) -> str:
        """Chained blake2s over every committed field except wall_meta and seq
        (ordering is the chain). Two runs from the same seed must match."""
        h = blake2s()
        for row in self._conn.execute(
            "SELECT sim_time, tick, type, payload, caused_by, provenance, actor_ref"
            " FROM event WHERE branch_id=? ORDER BY seq",
            (branch_id,),
        ):
            h.update(
                orjson.dumps(
                    [row[0], row[1], row[2], row[3].decode("utf-8"), row[4], row[5], row[6]]
                )
            )
        return h.hexdigest()
