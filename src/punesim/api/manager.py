"""Who is computing right now, and how to tell them to stop.

One subprocess per playing run — see `worker.py` for why a thread cannot do it.
There is deliberately NO limit on how many may run at once: the owner's rule is
that every run and branch has its own play/pause and any combination is allowed.
What the API does instead is report the cost honestly (`live` in /api/health,
and a per-run status), because each worker holds a world: ~750 MB at V3 scale.

Messages come back over a pipe, are read by one daemon thread per worker, and
land in a per-run deque plus a bump counter. The HTTP layer polls that; there is
no async plumbing, because a control channel that carries one message per
sim-day does not need any.
"""

from collections import deque
from dataclasses import asdict
import multiprocessing as mp
import threading
import time

from .worker import (
    CMD_INJECT, CMD_PAUSE, CMD_PLAY, CMD_STEP, CMD_STOP, WorkerParams, worker_main,
)

HISTORY = 200  # messages kept per run for the client to catch up on


class LiveRun:
    def __init__(self, params: WorkerParams):
        self.params = params
        self.status = "starting"
        self.day: int | None = None
        self.detail = ""
        self.error = ""
        self.events = 0
        self.last_seq = 0
        self.last_wall: float | None = None
        self.started = time.time()
        self.messages: deque[dict] = deque(maxlen=HISTORY)
        self.seq = 0  # bumped per message, so a client can poll "anything new?"
        self.proc: mp.Process | None = None
        self.conn = None
        self._lock = threading.Lock()

    def snapshot(self) -> dict:
        return {
            "run_id": self.params.run_id,
            "status": self.status,
            "day": self.day,
            "days": self.params.days,
            "detail": self.detail,
            "error": self.error,
            "events": self.events,
            "last_seq": self.last_seq,
            "last_day_wall": self.last_wall,
            "alive": bool(self.proc and self.proc.is_alive()),
            "seq": self.seq,
        }


class RunManager:
    def __init__(self) -> None:
        self._live: dict[str, LiveRun] = {}
        self._lock = threading.Lock()

    # -- queries ---------------------------------------------------------- #

    def status(self, run_id: str) -> dict:
        """`{}` for a run nobody is computing — callers fall back to the
        record's durable status."""
        lr = self._live.get(run_id)
        return lr.snapshot() if lr else {}

    def any_live(self) -> list[str]:
        return [k for k, v in self._live.items()
                if v.proc and v.proc.is_alive()]

    def messages(self, run_id: str, since: int = 0) -> list[dict]:
        lr = self._live.get(run_id)
        if not lr:
            return []
        return [m for m in list(lr.messages) if m.get("_seq", 0) > since]

    # -- lifecycle -------------------------------------------------------- #

    def start(self, params: WorkerParams) -> dict:
        """Spawn a worker. Idempotent for a run already alive."""
        with self._lock:
            existing = self._live.get(params.run_id)
            if existing and existing.proc and existing.proc.is_alive():
                return existing.snapshot()
            lr = LiveRun(params)
            parent, child = mp.Pipe(duplex=True)
            # 'spawn' explicitly: it is the only start method on Windows, and
            # naming it means the same code path is exercised everywhere.
            ctx = mp.get_context("spawn")
            lr.proc = ctx.Process(
                target=worker_main, args=(child, params),
                name=f"punesim-{params.run_id}", daemon=True,
            )
            lr.conn = parent
            lr.proc.start()
            child.close()  # the parent's copy of the child end must go, or the
                           # pipe never reports EOF when the child dies
            self._live[params.run_id] = lr
            threading.Thread(target=self._pump, args=(lr,), daemon=True,
                             name=f"pump-{params.run_id}").start()
            return lr.snapshot()

    def _pump(self, lr: LiveRun) -> None:
        """Read this worker's messages until it dies."""
        conn = lr.conn
        while True:
            try:
                if not conn.poll(0.5):
                    if lr.proc and not lr.proc.is_alive():
                        break
                    continue
                msg = conn.recv()
            except (EOFError, OSError):
                break
            self._absorb(lr, msg)
        # The process is gone. Say how, so a crash is not silently a "stop".
        if lr.status not in ("finished", "stopped", "error"):
            code = lr.proc.exitcode if lr.proc else None
            if code:
                lr.status, lr.error = "error", f"worker exited with code {code}"
            else:
                lr.status = "stopped"
        lr.seq += 1

    def _absorb(self, lr: LiveRun, msg: dict) -> None:
        kind = msg.get("kind")
        lr.seq += 1
        msg["_seq"] = lr.seq
        lr.messages.append(msg)
        if kind == "day":
            lr.day = msg.get("day")
            lr.events = msg.get("events", lr.events)
            lr.last_seq = msg.get("last_seq", lr.last_seq)
            lr.last_wall = msg.get("wall")
            lr.status = "running"
        elif kind == "status":
            lr.status = msg.get("status", lr.status)
            lr.detail = msg.get("detail", "")
            if msg.get("day") is not None:
                lr.day = msg["day"] - 1  # they report the day about to run
        elif kind == "finished":
            lr.status, lr.day = "finished", msg.get("day", lr.day)
        elif kind == "stopped":
            lr.status = "stopped"
        elif kind == "error":
            lr.status, lr.error = "error", msg.get("message", "unknown")

    def _send(self, run_id: str, payload: dict) -> bool:
        lr = self._live.get(run_id)
        if not lr or not lr.conn or not (lr.proc and lr.proc.is_alive()):
            return False
        try:
            lr.conn.send(payload)
            return True
        except (BrokenPipeError, OSError):
            return False

    def play(self, run_id: str) -> bool:
        lr = self._live.get(run_id)
        if lr:
            lr.status = "running"
        return self._send(run_id, {"cmd": CMD_PLAY})

    def pause(self, run_id: str) -> bool:
        # The worker pauses at the next day BOUNDARY — the only consistent
        # moment — so at V3 scale this can take up to 86 seconds to take effect.
        # Saying "pausing" rather than "paused" is the difference between a UI
        # that looks broken and one that is telling the truth.
        lr = self._live.get(run_id)
        if lr and lr.status == "running":
            lr.status = "pausing"
        return self._send(run_id, {"cmd": CMD_PAUSE})

    def step(self, run_id: str, days: int = 1) -> bool:
        lr = self._live.get(run_id)
        if lr:
            lr.status = "running"
        return self._send(run_id, {"cmd": CMD_STEP, "days": days})

    def inject(self, run_id: str, injection: dict) -> bool:
        return self._send(run_id, {"cmd": CMD_INJECT, "injection": injection})

    def stop(self, run_id: str, *, force: bool = False) -> bool:
        lr = self._live.get(run_id)
        if not lr:
            return False
        if force and lr.proc and lr.proc.is_alive():
            # Kills mid-day, which leaves a partial day in the log. That is what
            # `checkpoint.rewind` exists for and it is bounded to rows after the
            # last complete day — but it is a large delete at V3 scale, so the
            # next resume says so before it starts.
            lr.proc.terminate()
            lr.status = "stopped"
            return True
        lr.status = "stopping"
        return self._send(run_id, {"cmd": CMD_STOP})

    def forget(self, run_id: str) -> None:
        """Drop a finished run's live record so its status comes from disk."""
        lr = self._live.pop(run_id, None)
        if lr and lr.proc and lr.proc.is_alive():
            lr.proc.terminate()

    def stop_all(self) -> None:
        for rid in list(self._live):
            self.stop(rid, force=True)
        deadline = time.time() + 5
        for lr in self._live.values():
            if lr.proc:
                lr.proc.join(max(0.1, deadline - time.time()))
        self._live.clear()


def params_from(rec, *, resume: bool = False, start_paused: bool = True) -> WorkerParams:
    """A registry record as worker arguments."""
    p = rec.params
    return WorkerParams(
        run_id=rec.id, db=rec.db, checkpoint=str(rec.checkpoint),
        seed=p.seed, block=p.block, households=p.households, days=p.days,
        hazards=p.hazards, scenes=p.scenes, scenes_k=p.k,
        follow=tuple(p.follow), start_paused=start_paused, resume=resume,
        pending=tuple(getattr(rec, "pending_injections", ()) or ()),
    )


__all__ = ["RunManager", "WorkerParams", "params_from", "asdict"]
