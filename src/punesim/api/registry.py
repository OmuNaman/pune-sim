"""Which runs exist, and which run is a branch of which.

`runs/` today is whatever anyone happened to name a directory: `soak3`,
`obs2-off`, `rate-check`. That is fine for a CLI and useless for a save-tree,
because nothing records that `obs2-off` and `obs2-on` are the same world with
one thing changed.

So: runs the UI creates live under `runs/managed/<id>/` with a `run.json` beside
the log, and every older directory is *adopted* read-only by scanning for
`events.db`. Adopted runs have no parent and no checkpoint; they are history you
can look at, not history you can extend.

The parameters in `run.json` are a convenience copy, never the truth. Seed,
households and block always come from the log's own `run.meta` through
`world_for_log` — the file is for the things the log cannot know, which is the
run's name and what it was branched from.
"""

from dataclasses import asdict, dataclass, field
from pathlib import Path
import secrets
import time

import orjson

MANAGED = "managed"
RUN_JSON = "run.json"
LOG_NAME = "events.db"


@dataclass
class RunParams:
    seed: int
    block: str = "kasba"
    households: int = 80
    days: int = 1
    hazards: bool = True
    scenes: bool = False
    k: int = 5
    follow: list[str] = field(default_factory=list)


@dataclass
class RunRecord:
    id: str
    name: str
    db: str  # absolute path to events.db
    created_at: float
    params: RunParams
    managed: bool = True  # False for an adopted pre-UI run directory
    parent_id: str | None = None
    parent_day: int | None = None  # the day it forked at
    what_if: str = ""  # the headline for the save-tree edge
    day_done: int = -1  # last completed day; -1 = nothing yet
    status: str = "created"  # created|stopped|finished|error (live states live in the manager)
    error: str = ""
    # Injections accepted while nothing was computing; handed to the worker on
    # the next play, which is the same door the CLI's --inject uses.
    pending_injections: list = field(default_factory=list)

    @property
    def checkpoint(self) -> Path:
        return Path(self.db + ".state")

    def to_json(self) -> dict:
        out = asdict(self)
        out["params"] = asdict(self.params)
        return out

    @classmethod
    def from_json(cls, obj: dict, *, db: str) -> "RunRecord":
        obj = dict(obj)
        obj["params"] = RunParams(**obj.get("params", {"seed": 0}))
        obj["db"] = db  # the path is where we found it, not where it was written
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in obj.items() if k in known})


def new_id() -> str:
    return f"r-{time.strftime('%Y%m%d')}-{secrets.token_hex(2)}"


class RunRegistry:
    def __init__(self, root: str | Path = "runs"):
        self.root = Path(root)
        self.managed_root = self.root / MANAGED
        self.runs: dict[str, RunRecord] = {}

    # -- disk ------------------------------------------------------------- #

    def scan(self) -> None:
        """Rebuild the index from disk. Cheap: one stat per directory."""
        self.runs.clear()
        for rj in sorted(self.managed_root.glob(f"*/{RUN_JSON}")):
            try:
                rec = RunRecord.from_json(
                    orjson.loads(rj.read_bytes()), db=str(rj.parent / LOG_NAME)
                )
            except Exception:  # noqa: BLE001 — one corrupt run must not hide the rest
                continue
            self.runs[rec.id] = rec
        for db in sorted(self.root.glob(f"*/{LOG_NAME}")):
            if db.parent.name == MANAGED or not db.exists():
                continue
            rid = f"adopted:{db.parent.name}"
            self.runs[rid] = RunRecord(
                id=rid, name=db.parent.name, db=str(db), created_at=db.stat().st_mtime,
                # Adopted runs get their params from the log itself at read time;
                # seed 0 here is a placeholder that no read path ever consults.
                params=RunParams(seed=0), managed=False, status="finished",
            )

    def save(self, rec: RunRecord) -> None:
        p = Path(rec.db).parent / RUN_JSON
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(orjson.dumps(rec.to_json(), option=orjson.OPT_INDENT_2))
        self.runs[rec.id] = rec

    def create(self, name: str, params: RunParams, *, parent_id: str | None = None,
               parent_day: int | None = None, what_if: str = "") -> RunRecord:
        rid = new_id()
        d = self.managed_root / rid
        d.mkdir(parents=True, exist_ok=True)
        rec = RunRecord(
            id=rid, name=name or rid, db=str(d / LOG_NAME), created_at=time.time(),
            params=params, parent_id=parent_id, parent_day=parent_day, what_if=what_if,
        )
        self.save(rec)
        return rec

    def get(self, run_id: str) -> RunRecord | None:
        return self.runs.get(run_id)

    def children(self, run_id: str) -> list[RunRecord]:
        return [r for r in self.runs.values() if r.parent_id == run_id]
