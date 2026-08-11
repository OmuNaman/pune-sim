"""One run, in its own process, driven a day at a time.

A sim-day at 12,000 households is 62-86 seconds of pure-Python compute with no
GIL release of any duration. In a thread inside uvicorn that freezes every
endpoint for a minute at a time — the map, the panels, the health check, all of
it. So each run gets a process.

That also happens to be exactly what the owner asked for: every run and branch
has its own play/pause and any combination may compute at once. Independent
processes never touch each other, and a run that wedges can be terminated.

`run_simulation` is called ONCE, with the full day count, because `run.meta`
records the `days` argument of the first call (loop.py:68-80) — driving it a day
at a time with `days=1` would record `days: 1` and produce a different log from
an uninterrupted run of the same length. All control happens inside the
`on_day_end` hook (loop.py:325), which is the only moment the world is
consistent: every lane has committed and `state` is a whole world.

Spawn-safe on Windows: the entry point is a module-level function, the arguments
are all picklable, and the world is rebuilt here from (seed, block, households)
rather than shipped across the pipe.
"""

from dataclasses import dataclass
import queue
import time
import traceback


@dataclass
class WorkerParams:
    """Everything the child needs. Must stay picklable — no Block, no SimState."""

    run_id: str
    db: str
    checkpoint: str
    seed: int
    block: str
    households: int
    days: int
    hazards: bool = True
    scenes: bool = False
    scenes_k: int = 5
    follow: tuple[str, ...] = ()
    start_paused: bool = True
    resume: bool = False
    # Injections accepted while nothing was computing. Plain dicts, because
    # WorkerParams crosses a process boundary and Injection is not the shape
    # you want to pickle across one.
    pending: tuple[dict, ...] = ()


# Messages the parent sends: {"cmd": ...}
CMD_PLAY, CMD_PAUSE, CMD_STEP, CMD_STOP, CMD_INJECT = "play", "pause", "step", "stop", "inject"


def worker_main(conn, params: WorkerParams) -> None:
    """Child entry point. Everything heavy is imported here, after the fork."""
    from ..engine import Injection, run_simulation
    from ..engine import checkpoint as ckpt_mod
    from ..kernel.log import EventLog
    from ..population import synthesize
    from ..world.block import load_for

    def send(kind: str, **body) -> None:
        try:
            conn.send({"kind": kind, "run_id": params.run_id, **body})
        except (BrokenPipeError, EOFError):
            pass  # the parent is gone; the day loop will notice on the next poll

    try:
        send("status", status="building", detail="synthesising the population")
        block = load_for(params.households, params.block)
        hhs, people = synthesize(params.seed, block, n_households=params.households)
        log = EventLog(params.db)

        state, start_day = None, 0
        if params.resume:
            send("status", status="resuming", detail="rolling back the interrupted day")
            state, start_day, dropped = ckpt_mod.resume_point(
                params.checkpoint, log, run_seed=params.seed,
                households=params.households, block=params.block, days=params.days,
            )
            send("status", status="resumed", day=start_day, dropped=dropped)
            if start_day >= params.days:
                send("finished", day=params.days - 1, reason="already complete")
                log.close()
                return

        gateway = None
        if params.scenes:
            from .. import config
            from ..llm import Cassette, Gateway

            cfg = config.from_env()
            gateway = Gateway(cfg, Cassette(cfg.cassette_path), log=log)

        # The loop iterates THIS list every day (loop.py:159), so appending to it
        # from inside on_day_end is how a live injection lands. No engine change.
        injections: list = []
        for obj in params.pending:
            try:
                injections.append(Injection.parse(obj))
            except Exception as exc:  # noqa: BLE001
                send("inject", accepted=False, reason=f"queued: {exc}")
        paused = {"now": params.start_paused}
        steps = {"left": 0}
        stopping = {"now": False}

        def last_seq() -> int:
            return log._conn.execute(  # noqa: SLF001 — reading our own writer
                "SELECT coalesce(max(seq),0) FROM event").fetchone()[0]

        def handle(msg: dict) -> None:
            cmd = msg.get("cmd")
            if cmd == CMD_PLAY:
                paused["now"] = False
                steps["left"] = 0
            elif cmd == CMD_PAUSE:
                paused["now"] = True
            elif cmd == CMD_STEP:
                paused["now"] = False
                steps["left"] = int(msg.get("days", 1))
            elif cmd == CMD_STOP:
                stopping["now"] = True
                paused["now"] = False
            elif cmd == CMD_INJECT:
                try:
                    inj = Injection.parse(msg["injection"])
                except Exception as exc:  # noqa: BLE001 — reported, not fatal
                    send("inject", accepted=False, reason=str(exc))
                    return
                injections.append(inj)
                send("inject", accepted=True, day=inj.day, type=inj.type)

        def drain(block_until_ready: bool) -> None:
            """Service commands. Blocks while paused, which is what pause IS."""
            while True:
                while conn.poll():
                    handle(conn.recv())
                if not block_until_ready or not paused["now"] or stopping["now"]:
                    return
                conn.poll(0.2)  # a pause costs a tenth of a core, not a spin

        class _Stop(RuntimeError):
            """Asked to stop at a day boundary."""

        def on_day_end(day: int, st) -> None:
            ckpt_mod.save(
                params.checkpoint, st, day=day, seq=last_seq(),
                run_seed=params.seed, households=params.households,
                block=params.block, days=params.days,
            )
            send("day", day=day, last_seq=last_seq(),
                 events=log._conn.execute("SELECT count(*) FROM event").fetchone()[0],  # noqa: SLF001
                 wall=round(time.time() - clock["t0"], 2))
            clock["t0"] = time.time()
            if steps["left"] > 0:
                steps["left"] -= 1
                if steps["left"] == 0:
                    paused["now"] = True
            drain(block_until_ready=True)
            if stopping["now"]:
                raise _Stop

        clock = {"t0": time.time()}
        send("status", status="paused" if params.start_paused else "running",
             day=start_day)
        drain(block_until_ready=True)   # honour start_paused before day 0
        if stopping["now"]:
            send("stopped", day=start_day - 1)
            log.close()
            return

        try:
            run_simulation(
                log, params.seed, block, hhs, people,
                days=params.days - start_day, start_day=start_day, state=state,
                gateway=gateway, scenes_k=params.scenes_k if params.scenes else 0,
                injections=injections, hazards=params.hazards,
                follow=params.follow, block_name=params.block,
                on_day_end=on_day_end,
            )
        except _Stop:
            send("stopped", day=None)
            log.close()
            return
        send("finished", day=params.days - 1)
        log.close()
    except Exception as exc:  # noqa: BLE001 — the parent must hear about it
        send("error", message=f"{type(exc).__name__}: {exc}",
             traceback=traceback.format_exc()[-2000:])
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass
