"""Branch-lite (V2): fork a world, run a what-if, keep both timelines.

A run is a pure function of (seed, injections, cassettes) — so a branch is
not a database surgery, it is a RE-RUN: reconstruct the original injections
from the source log (the log is self-describing), add the what-if, and run
again. The cassette replays every LLM call from the shared prefix for free
and byte-identically, so the two worlds are exact twins until the moment of
divergence — which is precisely what makes the diff meaningful.
"""

from dataclasses import dataclass
from pathlib import Path

from .engine import Injection, run_simulation
from .kernel.log import EventLog
from .kernel.timebase import SECONDS_PER_DAY

_NOT_INJECTIONS = {"conversation.held", "memory.formed"}  # interviews are user-provenance too


def read_meta(log: EventLog) -> dict | None:
    for e in log.events(type="run.meta"):
        return dict(e.payload)
    return None


def reconstruct_injections(log: EventLog) -> list[Injection]:
    """The original scenario, recovered from the log itself."""
    out: list[Injection] = []
    for e in log.events():
        # true injections are user-provenance ROOTS; their stub consequences
        # (ambulance, admission, the school call) all carry caused_by lineage
        if e.provenance != "user" or e.caused_by is not None or e.type in _NOT_INJECTIONS:
            continue
        p = dict(e.payload)
        place = p.pop("place", None)
        participants = tuple(p.pop("participants", None) or [])
        severity = p.pop("severity", None)
        out.append(Injection(
            day=e.sim_time // SECONDS_PER_DAY,
            time_s=e.sim_time % SECONDS_PER_DAY,
            type=e.type, place=place, participants=participants,
            severity=severity, payload=p,
        ))
    return out


@dataclass(frozen=True)
class BranchResult:
    db_path: str
    seed: int
    days: int
    injections: int  # total incl. inherited
    events: int


def branch_run(
    source_db: str | Path,
    out_db: str | Path,
    *,
    block,
    synthesize,
    extra_injections: list[Injection] | None = None,
    add_days: int = 0,
    gateway=None,
    scenes_k: int = 5,
    scene_gate_mode: str = "spotlight",
    hazards: bool | None = None,
) -> BranchResult:
    """Fork `source_db` into `out_db` with `extra_injections` added and the
    horizon extended by `add_days`. Same seed, same world; the only difference
    is the what-if."""
    src = EventLog(source_db)
    meta = read_meta(src)
    if meta is None:
        src.close()
        raise ValueError("source log has no run.meta — re-run it once on this version first")
    inherited = reconstruct_injections(src)
    had_hazards = any(
        e.provenance == "clockwork" and e.type.startswith("hazard.") for e in src.events()
    )
    max_t = max((e.sim_time for e in src.events()), default=0)
    src.close()

    seed = int(meta["seed"])
    n_households = int(meta["households"])
    days = max_t // SECONDS_PER_DAY + 1 + add_days
    injections = inherited + list(extra_injections or [])

    out_path = Path(out_db)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(out_path) + suffix)
        if p.exists():
            p.unlink()

    hhs, people = synthesize(seed, block, n_households=n_households)
    log = EventLog(out_path)
    if gateway is not None:
        gateway.log = log  # llm.response events belong to the branch's log
    n, _state = run_simulation(
        log, seed, block, hhs, people,
        days=days, gateway=gateway, scenes_k=scenes_k,
        scene_gate_mode=scene_gate_mode, injections=injections,
        hazards=had_hazards if hazards is None else hazards,
        # Without this the branch RUNS on `block` correctly and RECORDS the
        # default one, because run.meta omits `block` when it is the default.
        # So a branch of a 12k oldcity run wrote a run.meta implicitly claiming
        # kasba, and every tool downstream — the audit, the continuity read,
        # follow, interview — would faithfully rebuild the wrong 306-person
        # world for it. The same bug as world/roster.py exists to stop, arriving
        # through the one door that writes its own metadata.
        block_name=block.name,
    )
    log.close()
    return BranchResult(
        db_path=str(out_path), seed=seed, days=days,
        injections=len(injections), events=n,
    )
