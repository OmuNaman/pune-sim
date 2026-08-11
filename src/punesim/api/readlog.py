"""A log opened for reading only, and opened freshly every time.

Two traps this exists to avoid, both already paid for elsewhere in the repo:

`EventLog(path)` is a WRITE. Its `__init__` runs `executescript(_SCHEMA)`, which
creates the `ev_branch_type` index on an older log and takes the write lock. A
server that opens a run's log to answer `GET /meta` would be a second writer on
a database a worker process is committing to.

And a read-only connection HELD OPEN across queries on a WAL database reads the
snapshot it first saw. That is the exact trap in `pune-sim-v3-scale`: it fooled
me three times into diagnosing a live run as stalled. `LogView` avoids it by
opening a connection per query and closing it, which is why watching a running
sim works at all — this class does the same, and adds the `.events()` shape that
`world_for_log`, `reconstruct_injections` and `diff_logs` all duck-type against.
"""

from dataclasses import dataclass
from pathlib import Path
import sqlite3

import orjson


@dataclass(frozen=True)
class RoEvent:
    """`kernel.log.Event` minus `wall_meta`, which no reader has ever wanted."""

    seq: int
    branch_id: int
    sim_time: int
    tick: int
    type: str
    payload: dict
    caused_by: int | None
    provenance: str
    actor_ref: str | None


# path -> ((size, mtime_ns), (n_events, max_t, max_seq)). See `summary`.
_SUMMARY: dict[str, tuple[tuple[int, int], tuple[int, int, int]]] = {}
# path -> ((size, mtime_ns, exclude), rows). See `counts_by_day_and_type`.
_DAY_COUNTS: dict[str, tuple[tuple, list]] = {}


class _Sidecar:
    """Expensive aggregates, kept in a file beside the log.

    `COUNT(*)` and the per-day `GROUP BY` are half a second and thirty seconds
    respectively on a 6.8M-row log, and both are pure functions of a file that
    for a finished run never changes again. In-memory caching alone means
    paying it once per server start, which is once per time you restart the UI
    while working on it — i.e. constantly.

    Keyed on the log's own (size, mtime_ns), so a live run's sidecar is simply
    stale and ignored rather than wrong. Deleting the file is always safe.
    """

    def __init__(self, db_path: Path):
        self.path = Path(str(db_path) + ".stats.json")

    def _key(self, db_path: Path):
        try:
            st = db_path.stat()
            return [st.st_size, st.st_mtime_ns]
        except OSError:
            return None

    def read(self, db_path: Path, field: str):
        key = self._key(db_path)
        if key is None or not self.path.exists():
            return None
        try:
            blob = orjson.loads(self.path.read_bytes())
        except Exception:  # noqa: BLE001 — a corrupt cache is just a cache miss
            return None
        return blob.get(field) if blob.get("key") == key else None

    def write(self, db_path: Path, field: str, value) -> None:
        key = self._key(db_path)
        if key is None:
            return
        blob = {}
        if self.path.exists():
            try:
                existing = orjson.loads(self.path.read_bytes())
                if existing.get("key") == key:
                    blob = existing
            except Exception:  # noqa: BLE001
                blob = {}
        blob["key"] = key
        blob[field] = value
        try:
            tmp = self.path.with_suffix(".json.partial")
            tmp.write_bytes(orjson.dumps(blob))
            tmp.replace(self.path)
        except OSError:
            pass  # a read-only runs/ directory is a reason to be slow, not to fail


def ro_uri(path: str | Path) -> str:
    """`file:...?mode=ro`, built so a Windows path survives it.

    `f"file:{path}"` with a `D:\\runs\\x.db` in it produces backslashes SQLite
    reads as escapes and a drive letter it reads as a URI authority. Forward
    slashes and an absolute path fix both. Every read connection in this package
    goes through here.
    """
    return f"file:/{Path(path).resolve().as_posix().lstrip('/')}?mode=ro"


class ReadOnlyLog:
    """Enough of `EventLog` for the read paths, and nothing that writes."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def _con(self) -> sqlite3.Connection:
        return sqlite3.connect(ro_uri(self.path), uri=True)

    def exists(self) -> bool:
        return self.path.exists()

    def events(
        self,
        *,
        branch_id: int = 0,
        type: str | None = None,  # noqa: A002 — matches EventLog's signature
        since_seq: int = 0,
        since_time: int | None = None,
        until_time: int | None = None,
        limit: int | None = None,
    ):
        """Same filters as `EventLog.events`, plus an optional `limit`.

        `limit` is pushed into SQL rather than applied to the result, because
        the callers that want a tail — the live ticker — are asking a 6.8M-row
        table for the last few hundred rows.
        """
        where, args = ["seq > ?"], [since_seq]
        if type is not None:
            where.append("type = ?")
            args.append(type)
        if since_time is not None:
            where.append("sim_time >= ?")
            args.append(since_time)
        if until_time is not None:
            where.append("sim_time < ?")
            args.append(until_time)
        sql = (
            "SELECT seq, branch_id, sim_time, tick, type, payload, caused_by, "
            f"provenance, actor_ref FROM event WHERE {' AND '.join(where)} ORDER BY seq"
        )
        if limit is not None:
            sql += f" LIMIT {int(limit)}"
        con = self._con()
        try:
            rows = con.execute(sql, tuple(args)).fetchall()
        finally:
            con.close()
        for r in rows:
            # branch_id is filtered here rather than in the WHERE clause for the
            # reason logview.py:68 documents: naming it makes SQLite prefer the
            # (branch_id, sim_time) index and scan, 0.89s against 0.00s at 6.8M.
            if r[1] == branch_id:
                yield RoEvent(r[0], r[1], r[2], r[3], r[4], orjson.loads(r[5]),
                              r[6], r[7], r[8])

    def summary(self) -> tuple[int, int, int]:
        """(n_events, max_sim_time, max_seq) — the run's header line.

        Cached against the file's own (size, mtime), because `COUNT(*)` over a
        6.8M-row table is 0.5–2 s and the header is asked for on every page. A
        finished log never changes, so it is computed once; a live run's log
        does change, and the cache misses exactly when it should. That beats a
        TTL, which would be both stale and slow.
        """
        try:
            st = self.path.stat()
            key = (st.st_size, st.st_mtime_ns)
        except OSError:
            key = None
        hit = _SUMMARY.get(str(self.path))
        if hit is not None and key is not None and hit[0] == key:
            return hit[1]
        disk = _Sidecar(self.path).read(self.path, "summary")
        if disk is not None:
            out = (disk[0], disk[1], disk[2])
            if key is not None:
                _SUMMARY[str(self.path)] = (key, out)
            return out
        con = self._con()
        try:
            n, max_t, max_seq = con.execute(
                "SELECT COUNT(*), COALESCE(MAX(sim_time), 0), COALESCE(MAX(seq), 0) "
                "FROM event WHERE branch_id = 0"
            ).fetchone()
        finally:
            con.close()
        out = (n, max_t, max_seq)
        if key is not None:
            _SUMMARY[str(self.path)] = (key, out)
            _Sidecar(self.path).write(self.path, "summary", list(out))
        return out

    def counts_by_day_and_type(self, exclude: tuple[str, ...] = ()) -> list[tuple[int, str, int]]:
        """(day, type, n) for the timeline ribbon, aggregated in SQL.

        The alternative is shipping several million rows to Python to count
        them, which is the shape of mistake `logview` was written to undo.

        Even in SQL this is a full aggregate — 26 s on a 6.8M-row log — because
        `sim_time / 86400` is not indexable. Cached on (size, mtime) like
        `summary`, so a finished run pays once and a live run recomputes only
        when it has actually grown.
        """
        try:
            st = self.path.stat()
            key = (st.st_size, st.st_mtime_ns, exclude)
        except OSError:
            key = None
        ck = str(self.path)
        hit = _DAY_COUNTS.get(ck)
        if hit is not None and key is not None and hit[0] == key:
            return hit[1]
        if not exclude:
            disk = _Sidecar(self.path).read(self.path, "day_counts")
            if disk is not None:
                rows = [(d, t, n) for d, t, n in disk]
                if key is not None:
                    _DAY_COUNTS[ck] = (key, rows)
                return rows
        sql = ("SELECT sim_time / 86400, type, COUNT(*) FROM event "
               "WHERE branch_id = 0")
        args: tuple = ()
        if exclude:
            sql += f" AND type NOT IN ({','.join('?' * len(exclude))})"
            args = exclude
        sql += " GROUP BY 1, 2"
        con = self._con()
        try:
            rows = con.execute(sql, args).fetchall()
        finally:
            con.close()
        if key is not None:
            _DAY_COUNTS[ck] = (key, rows)
            if not exclude:
                _Sidecar(self.path).write(self.path, "day_counts", rows)
        return rows
