"""Which world does this log belong to? Ask the log, not the caller.

A log is a stream of ids — `person:1160.3`, `hh:1160`, `place:node/3681735096` —
and every one of them means something only against the population that wrote
them. That population is a pure function of (seed, block, households), so it can
always be rebuilt; the failure mode is rebuilding the *wrong* one and never
finding out. `hh:000` and `person:001.1` exist in every world this repo can
synthesize, so a tool pointed at the wrong roster does not crash. It prints a
different family's name over the right family's events and looks fine.

That has already cost this repo a soak: a windowed audit dropped the `run.meta`
row, fell back to kasba, regenerated 46,671 people for a log of 49,578, and
passed nineteen probes against a world nobody had run
(docs/soaks/v3-scale-soak.md). `scripts/audit_run.py` was fixed to read the meta
and refuse on a mismatch. This is that fix, in one place, for the three callers
that still had the bug — `punesim interview`, `punesim follow`, and
`scripts/continuity_read.py`, the last of which is the instrument that decides
V1's exit.

Explicit arguments still win when they agree, and are refused when they do not:
a caller who passes `--households 80` at an oldcity log has made a mistake worth
a non-zero exit rather than a confident answer.
"""

from ..population.synth import Household, Person, synthesize
from .block import DEFAULT_BLOCK, Block, load_for


class RosterMismatch(RuntimeError):
    """The log says one world and the caller asked for another."""


def _first(*values):
    """The first value that is not None. The log wins, then the caller's
    explicit flag, then whatever default the command carries."""
    return next((v for v in values if v is not None), None)


def read_meta(log) -> dict | None:
    """The run.meta payload, or None for a log written before it existed."""
    for e in log.events(type="run.meta"):
        return dict(e.payload)
    return None


def world_for_log(
    log,
    seed: int | None = None,
    households: int | None = None,
    block: str | None = None,
    *,
    fallback_seed: int | None = None,
    fallback_households: int = 80,
) -> tuple[Block, list[Household], dict[str, Person], dict]:
    """Rebuild the population a log was written against.

    Returns (block, households, people, meta). `meta` is empty for a log written
    before run.meta existed — in which case the caller's arguments are all there
    is, and the roster is uncorroborated rather than wrong.

    The `fallback_*` arguments are kept separate from the positional ones on
    purpose: only values the caller *explicitly asked for* are worth refusing
    over. A default that happens to disagree with the log is the log's business.
    """
    meta = read_meta(log) or {}
    for name, given, key in (
        ("seed", seed, "seed"),
        ("households", households, "households"),
        ("block", block, "block"),
    ):
        recorded = meta.get(key)
        if key == "block" and recorded is None and meta:
            recorded = DEFAULT_BLOCK  # loop.py records `block` only when non-default
        if given is not None and recorded is not None and given != recorded:
            raise RosterMismatch(
                f"this log was written with {name}={recorded!r} and you asked for "
                f"{name}={given!r}. Refusing to rebuild the wrong roster — every id "
                f"in the log would resolve to a different person."
            )

    run_seed = _first(meta.get("seed"), seed, fallback_seed)
    n_households = _first(meta.get("households"), households, fallback_households)
    block_name = _first(meta.get("block"), block, DEFAULT_BLOCK)
    if run_seed is None or n_households is None:
        raise RosterMismatch(
            "this log has no run.meta and you did not pass --seed/--households, so "
            "there is no way to know which population wrote it."
        )

    blk = load_for(n_households, block_name)
    hhs, people = synthesize(run_seed, blk, n_households=n_households)
    return blk, hhs, people, meta
