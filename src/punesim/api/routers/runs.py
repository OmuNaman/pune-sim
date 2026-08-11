"""The registry and, from Phase 4, the lifecycle.

Read-only for now: list the runs, describe one, draw the save-tree. The play /
pause / step / branch endpoints arrive with the worker.
"""

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from ...kernel.timebase import SECONDS_PER_DAY
from ..readlog import ReadOnlyLog

router = APIRouter()

# A log's run.meta is written once at day 0 and never changes, so a run that has
# been listed before never needs the lookup again.
_META: dict[str, dict] = {}


def _summary(request: Request, rec) -> dict:
    """One row of the run list. Deliberately cheap: a cached count and a stat,
    no roster build — a machine with forty run directories should not
    synthesize forty populations to draw a list."""
    log = ReadOnlyLog(rec.db)
    n_events, max_t, max_seq = (0, 0, 0)
    size = 0
    if log.exists():
        n_events, max_t, max_seq = log.summary()
        size = Path(rec.db).stat().st_size
    meta = _META.get(rec.db, {})
    if n_events and not meta:
        for e in log.events(type="run.meta"):
            meta = _META[rec.db] = dict(e.payload)
            break
    live = request.app.state.manager.status(rec.id)
    return {
        "id": rec.id, "name": rec.name, "managed": rec.managed,
        "status": live.get("status", rec.status),
        "computing_day": live.get("day"),
        "seed": meta.get("seed", rec.params.seed),
        "block": meta.get("block", "kasba"),
        "households": meta.get("households", rec.params.households),
        "days_planned": meta.get("days", rec.params.days),
        "days_done": (max_t // SECONDS_PER_DAY + 1) if n_events else 0,
        "events": n_events, "last_seq": max_seq,
        "created_at": rec.created_at,
        "parent_id": rec.parent_id, "parent_day": rec.parent_day,
        "what_if": rec.what_if,
        "size_bytes": size,
    }


@router.get("")
def list_runs(request: Request):
    """Every run and branch on disk — the save-tree's node list.

    Rescans each time: a run directory can appear from the CLI while the server
    is up, and a list that quietly omits it is worse than a slow one.
    """
    reg = request.app.state.registry
    reg.scan()
    rows = [_summary(request, r) for r in reg.runs.values()]
    rows.sort(key=lambda r: (r["created_at"]), reverse=True)
    return {"runs": rows}


@router.get("/{run_id}")
def run_detail(request: Request, run_id: str):
    reg = request.app.state.registry
    rec = reg.get(run_id)
    if rec is None:
        raise HTTPException(404, f"no run {run_id!r}")
    out = _summary(request, rec)
    out["children"] = [c.id for c in reg.children(run_id)]
    return out
