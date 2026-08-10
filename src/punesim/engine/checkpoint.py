"""Save a running world at a day boundary so a long soak can survive being killed.

A 30-day run at 12,000 households is ~2.5 hours and, until now, all-or-nothing:
`run_simulation` accepts a `start_day` but only together with the in-memory
`SimState`, and refuses it otherwise because resuming from a bare log would
begin a *new* world with everyone's opening pressures re-fired and nobody
remembering anything they had heard. Four attempts at V3's last exit clause were
killed at days 22, 8, 2 and 2, and each one started over from nothing. V4 opens
with a ninety-day soak.

So: after each day, write the state beside the log. On `--resume`, read it back
and carry on.

**The one uncomfortable part, stated plainly.** A process killed mid-day has
already committed part of that day. The log is append-only (law 1), so those
rows cannot simply be un-written — and re-running the day on top of them would
duplicate it. What resume does instead is what a database does after a crash:
the checkpoint records the last seq of the last *complete* day, and rows beyond
it are an incomplete transaction that gets rolled back before the day is re-run.

That is narrow and it is the only place in this repo that removes committed
rows. It only ever touches `seq > checkpoint.seq`, only on an explicit resume,
and every row it drops is regenerated identically by the re-run because every
draw is keyed rather than sequential. If the seqs do not line up, it refuses
rather than guessing.

The guards are the same shape as `world/roster.py`'s, and for the same reason: a
checkpoint restored onto the wrong world would not raise. Every id in it would
resolve to somebody.
"""

import pickle
from dataclasses import fields
from pathlib import Path

from .state import SimState

FORMAT = 1


class CheckpointMismatch(RuntimeError):
    """This checkpoint does not belong to this run."""


def _fingerprint() -> tuple[str, ...]:
    """SimState's shape. A checkpoint written before a field was added or
    removed cannot be resumed into the new engine, and unpickling it would
    half-work rather than fail — the missing field would just be its default,
    which for `acted` or `fired` means a whole lane quietly re-firing."""
    return tuple(f.name for f in fields(SimState))


def save(
    path: str | Path, state: SimState, *, day: int, seq: int,
    run_seed: int, households: int, block: str, days: int | None = None,
) -> None:
    """Write the world as of the end of `day`. `seq` is the log's last seq."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    blob = pickle.dumps(
        {"format": FORMAT, "fingerprint": _fingerprint(), "day": day, "seq": seq,
         "run_seed": run_seed, "households": households, "block": block,
         "days": days, "state": state},
        protocol=pickle.HIGHEST_PROTOCOL,
    )
    # Written beside the target and renamed, so a checkpoint is never half a
    # file: being killed during the write is exactly the case this exists for.
    tmp = p.with_suffix(p.suffix + ".partial")
    tmp.write_bytes(blob)
    tmp.replace(p)


def load(
    path: str | Path, *, run_seed: int, households: int, block: str,
    days: int | None = None,
) -> tuple[SimState, int, int]:
    """Return (state, next_day, seq). Raises CheckpointMismatch if it belongs
    to a different world or a different engine."""
    data = pickle.loads(Path(path).read_bytes())
    if data.get("format") != FORMAT:
        raise CheckpointMismatch(
            f"checkpoint format {data.get('format')}, this engine writes {FORMAT}"
        )
    if tuple(data.get("fingerprint") or ()) != _fingerprint():
        missing = set(_fingerprint()) ^ set(data.get("fingerprint") or ())
        raise CheckpointMismatch(
            f"SimState has changed shape since this checkpoint was written "
            f"(differing fields: {sorted(missing)}). Resuming would give those fields "
            f"their defaults, which for `acted` or `fired` means a whole lane re-firing "
            f"silently. Start the run again."
        )
    for name, want, got in (("seed", run_seed, data["run_seed"]),
                            ("households", households, data["households"]),
                            ("block", block, data["block"])):
        if want != got:
            raise CheckpointMismatch(
                f"this checkpoint is from a run with {name}={got!r}, you are running "
                f"{name}={want!r}. Every id in it would resolve to a different person."
            )
    # Resume CONTINUES a run; it does not extend one. run.meta records the days
    # that were asked for, so `--days 3` finished and then resumed as `--days 5`
    # is a genuinely different run from an uninterrupted `--days 5` and hashes
    # differently — measured, not supposed. Refusing here is the difference
    # between that being an error and being a mystery.
    if days is not None and data.get("days") is not None and days != data["days"]:
        raise CheckpointMismatch(
            f"this checkpoint belongs to a --days {data['days']} run and you asked for "
            f"--days {days}. Resume continues a run, it does not extend one: run.meta "
            f"already records {data['days']}, so the result would not match an "
            f"uninterrupted --days {days} run. Re-run from scratch to change the horizon."
        )
    return data["state"], data["day"] + 1, data["seq"]


def rewind(log, seq: int) -> int:
    """Drop rows a killed run committed after the last complete day.

    Returns how many were dropped. See the module docstring: this is the only
    place committed rows are removed, it is bounded to `seq > checkpoint.seq`,
    and the re-run regenerates them identically."""
    con = log._conn  # noqa: SLF001 — the kernel owns writes; this is crash recovery
    n = con.execute("SELECT count(*) FROM event WHERE seq > ?", (seq,)).fetchone()[0]
    if n:
        con.execute("DELETE FROM event WHERE seq > ?", (seq,))
        con.commit()
    return n


def resume_point(
    path: str | Path, log, *, run_seed: int, households: int, block: str,
    days: int | None = None,
) -> tuple[SimState, int, int]:
    """Load a checkpoint and make the log agree with it.

    Returns (state, next_day, rows_dropped)."""
    state, next_day, seq = load(
        path, run_seed=run_seed, households=households, block=block, days=days
    )
    con = log._conn  # noqa: SLF001
    top = con.execute("SELECT coalesce(max(seq), 0) FROM event").fetchone()[0]
    if top < seq:
        raise CheckpointMismatch(
            f"the checkpoint is ahead of the log: it ends at seq {seq} and the log stops "
            f"at {top}. This is not the log that checkpoint was written for."
        )
    return state, next_day, rewind(log, seq)
