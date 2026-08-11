"""The registry and, from Phase 4, the lifecycle.

Read-only for now: list the runs, describe one, draw the save-tree. The play /
pause / step / branch endpoints arrive with the worker.
"""

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ...kernel.timebase import SECONDS_PER_DAY
from ..manager import params_from
from ..readlog import ReadOnlyLog
from ..registry import RunParams

router = APIRouter()


class NewRun(BaseModel):
    name: str = ""
    block: str = "kasba"
    households: int = Field(80, ge=1, le=60_000)
    days: int = Field(3, ge=1, le=365)
    seed: int | None = None
    hazards: bool = True
    scenes: bool = False
    k: int = Field(5, ge=0, le=50)
    follow: list[str] = []
    autostart: bool = True


class InjectBody(BaseModel):
    """The scenario-file shape, one entry. See engine/injection.py:17."""

    day: int
    time: str = "12:00"
    type: str
    place: str | None = None
    participants: list[str] = []
    severity: float | None = None
    payload: dict = {}

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


@router.post("")
def create_run(request: Request, body: NewRun):
    """Make a new world and, unless told otherwise, start computing it."""
    from ...config import from_env
    from ...world.block import BLOCKS

    if body.block not in BLOCKS:
        raise HTTPException(400, f"unknown block {body.block!r}; "
                                 f"known: {', '.join(sorted(BLOCKS))}")
    seed = body.seed if body.seed is not None else from_env().run_seed
    reg = request.app.state.registry
    rec = reg.create(body.name or f"{body.block} {body.households}hh",
                     RunParams(seed=seed, block=body.block,
                               households=body.households, days=body.days,
                               hazards=body.hazards, scenes=body.scenes,
                               k=body.k, follow=list(body.follow)))
    live = request.app.state.manager.start(
        params_from(rec, start_paused=not body.autostart))
    return {"id": rec.id, "live": live}


@router.get("/{run_id}")
def run_detail(request: Request, run_id: str):
    reg = request.app.state.registry
    rec = reg.get(run_id)
    if rec is None:
        raise HTTPException(404, f"no run {run_id!r}")
    out = _summary(request, rec)
    out["children"] = [c.id for c in reg.children(run_id)]
    return out


def _managed(request: Request, run_id: str):
    rec = request.app.state.registry.get(run_id)
    if rec is None:
        raise HTTPException(404, f"no run {run_id!r}")
    if not rec.managed:
        raise HTTPException(
            409, f"{rec.name!r} is a run this UI did not create — it can be read "
                 "but not driven. Branch it to get one that can.")
    return rec


@router.post("/{run_id}/play")
def play(request: Request, run_id: str):
    """Compute days until told to stop.

    Every run has its own play; several may compute at once. Each holds a world
    (~750 MB at V3 scale), which is why the count of live runs is reported
    rather than capped.
    """
    rec = _managed(request, run_id)
    mgr = request.app.state.manager
    if mgr.play(run_id):
        return mgr.status(run_id)
    # Nothing alive to talk to: spawn one, resuming if there is a checkpoint.
    live = mgr.start(params_from(rec, resume=rec.checkpoint.exists(),
                                 start_paused=False))
    return live


@router.post("/{run_id}/pause")
def pause(request: Request, run_id: str):
    """Stop at the next day boundary — the only consistent moment there is."""
    _managed(request, run_id)
    mgr = request.app.state.manager
    if not mgr.pause(run_id):
        raise HTTPException(409, "this run is not computing")
    return mgr.status(run_id)


@router.post("/{run_id}/step")
def step(request: Request, run_id: str, days: int = 1):
    rec = _managed(request, run_id)
    mgr = request.app.state.manager
    if not mgr.step(run_id, days):
        mgr.start(params_from(rec, resume=rec.checkpoint.exists(), start_paused=True))
        mgr.step(run_id, days)
    return mgr.status(run_id)


@router.post("/{run_id}/stop")
def stop(request: Request, run_id: str, force: bool = False):
    """Graceful by default: finish the day in flight, then exit.

    `force` terminates mid-day, which leaves a partial day in the log. That is
    recoverable — `checkpoint.rewind` drops rows after the last complete day —
    but it is a large delete at V3 scale, so prefer waiting.
    """
    _managed(request, run_id)
    mgr = request.app.state.manager
    if not mgr.stop(run_id, force=force):
        raise HTTPException(409, "this run is not computing")
    return mgr.status(run_id)


@router.post("/{run_id}/inject")
def inject(request: Request, run_id: str, body: InjectBody):
    """Put an event into a run that has not reached that day yet.

    A live injection is committed with provenance "user" and no cause, which is
    exactly what `branch.reconstruct_injections` recovers — so a run steered
    from the browser is still reproducible from its own log alone.
    """
    from ...engine import Injection

    rec = _managed(request, run_id)
    obj = body.model_dump()
    try:
        Injection.parse(obj)   # fail here, not in the worker
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"not a valid injection: {exc}") from None

    mgr = request.app.state.manager
    live = mgr.status(run_id)
    if live and live.get("day") is not None and body.day <= live["day"]:
        raise HTTPException(
            409, f"day {body.day} has already been computed (this run is on day "
                 f"{live['day']}). Inject into a later day, or branch from "
                 f"day {body.day} to change what happened.")
    if not mgr.inject(run_id, obj):
        # Not running: hold it for the next play, which is the same door the
        # CLI's --inject uses.
        pend = list(getattr(rec, "pending_injections", []) or [])
        pend.append(obj)
        rec.pending_injections = pend
        request.app.state.registry.save(rec)
        return {"queued": True, "live": False, "day": body.day}
    return {"queued": True, "live": True, "day": body.day}


class BranchBody(BaseModel):
    name: str = ""
    what_if: str = ""
    """Where the two worlds part. Days before this are replayed identically."""
    from_day: int | None = None
    add_days: int = Field(0, ge=0, le=365)
    injections: list[InjectBody] = []


@router.post("/{run_id}/branch")
def branch(request: Request, run_id: str, body: BranchBody):
    """Fork a world at a day and change one thing.

    A branch is not a copy of the database — it is the SAME world re-run with
    one more injection in it. That is what makes the diff meaningful: the sim is
    deterministic, so replaying days 0..N gives byte-identical results, and
    every difference after the fork is caused by the thing you changed. Nothing
    else could have caused it.

    The cost is that the shared past is re-simulated: branching at day 12 of a
    30-day V3 run recomputes twelve days. The response says how many, so the UI
    can warn before the user commits to it.

    Two carve-outs the UI must not hide:
      - interviews (`conversation.held`) are user-provenance but deliberately
        NOT reconstructed (branch.py:18), so a branch will not replay a
        conversation you had with somebody in the parent.
      - the horizon cannot be extended in place (checkpoint.py:105), which is
        precisely why "play past the end" sends you here with add_days.
    """
    from ...branch import reconstruct_injections
    from ..readlog import ReadOnlyLog

    reg = request.app.state.registry
    src = reg.get(run_id)
    if src is None:
        raise HTTPException(404, f"no run {run_id!r}")
    log = ReadOnlyLog(src.db)
    if not log.exists():
        raise HTTPException(409, "this run has no log to branch from")

    meta = request.app.state.worlds.meta_only(run_id, src.db)
    seed = meta.get("seed", src.params.seed)
    block = meta.get("block", "kasba")
    households = meta.get("households", src.params.households)
    n_events, max_t, _seq = log.summary()
    days_done = (max_t // SECONDS_PER_DAY + 1) if n_events else 0

    fork_day = body.from_day if body.from_day is not None else days_done
    if fork_day < 0 or fork_day > days_done:
        raise HTTPException(
            400, f"cannot branch at day {fork_day}: this run has {days_done} days")

    # Everything the parent was told to do, plus the what-if. Injections after
    # the fork day are dropped — that is what forking THERE means.
    inherited = [
        {"day": i.day,
         "time": f"{i.time_s // 3600:02d}:{i.time_s % 3600 // 60:02d}",
         "type": i.type, "place": i.place, "participants": list(i.participants),
         "severity": i.severity, "payload": i.payload}
        for i in reconstruct_injections(log) if i.day < fork_day
    ]
    extra = [b.model_dump() for b in body.injections]
    for e in extra:
        if e["day"] < fork_day:
            raise HTTPException(
                400, f"an injection on day {e['day']} is before the fork at day "
                     f"{fork_day} — it would be part of the shared past, not the "
                     f"what-if")

    days = max(days_done, fork_day) + body.add_days
    rec = reg.create(
        body.name or f"{src.name} · what if",
        RunParams(seed=seed, block=block, households=households, days=days,
                  hazards=src.params.hazards, scenes=src.params.scenes,
                  k=src.params.k, follow=list(src.params.follow)),
        parent_id=run_id, parent_day=fork_day,
        what_if=body.what_if or (extra[0]["type"] if extra else "same again"),
    )
    rec.pending_injections = [*inherited, *extra]
    reg.save(rec)
    live = request.app.state.manager.start(params_from(rec, start_paused=False))
    return {
        "id": rec.id,
        "fork_day": fork_day,
        "replays_days": fork_day,
        "days": days,
        "inherited": len(inherited),
        "added": len(extra),
        "live": live,
        # Said plainly, because the UI shows it before the user commits.
        "note": (f"days 0–{fork_day - 1} are re-simulated identically; "
                 f"everything after is the consequence of the change"
                 if fork_day else "branched from the very beginning"),
    }


@router.get("/{run_id}/events")
def worker_events(request: Request, run_id: str, since: int = 0):
    """What the worker has said since message `since`.

    Polling rather than SSE on purpose: this channel carries about one message
    per sim-day — 0.04s apart at kasba, 86s apart at V3 — and a poll is a great
    deal less machinery than a streaming response for that.
    """
    mgr = request.app.state.manager
    return {"status": mgr.status(run_id),
            "messages": mgr.messages(run_id, since)}
