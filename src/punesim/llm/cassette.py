"""The VCR: every LLM call is recorded under a deterministic request id;
replay mode serves from here and a miss is a hard error (tests never call the
network). This is what makes LLM-touching code free to iterate forever after
first recording.
"""

import sqlite3
from dataclasses import dataclass
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS cassette (
  request_id TEXT PRIMARY KEY,
  model      TEXT NOT NULL,
  request    BLOB NOT NULL,
  response   TEXT NOT NULL,
  usage      BLOB NOT NULL
);
"""


class CassetteMiss(LookupError):
    """Replay mode asked for a call that was never recorded."""


@dataclass(frozen=True)
class RecordedCall:
    request_id: str
    model: str
    response: str
    usage: dict


class Cassette:
    def __init__(self, path: str | Path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path))
        self._conn.executescript(_SCHEMA)

    def get(self, request_id: str) -> RecordedCall | None:
        import orjson

        row = self._conn.execute(
            "SELECT model, response, usage FROM cassette WHERE request_id=?", (request_id,)
        ).fetchone()
        if row is None:
            return None
        return RecordedCall(request_id, row[0], row[1], orjson.loads(row[2]))

    def put(self, request_id: str, *, model: str, request: bytes, response: str, usage: dict) -> None:
        import orjson

        with self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO cassette (request_id, model, request, response, usage)"
                " VALUES (?,?,?,?,?)",
                (request_id, model, request, response, orjson.dumps(usage)),
            )

    def close(self) -> None:
        self._conn.close()
