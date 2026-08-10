"""A run that is killed and resumed must be the same run.

Until now a long soak was all-or-nothing: `run_simulation` takes a `start_day`
only together with the in-memory `SimState` and refuses it otherwise, so a
killed process lost everything. Four attempts at V3's last exit clause died at
days 22, 8, 2 and 2 of 30, each starting over from nothing.

The only test that matters here is hash equality. A resumed run is worth having
only if it is indistinguishable from one that was never interrupted — same
events, same order, same payloads — because everything downstream (the audit,
the continuity judge, the determinism pin) treats the log as the run.
"""

import pytest

from punesim import engine
from punesim.engine import checkpoint as ckpt_mod
from punesim.kernel.log import EventLog
from punesim.population import synthesize
from punesim.world.block import Block

pytestmark = pytest.mark.skipif(
    not __import__("pathlib").Path("data/anchors/kasba_places.geojson").exists(),
    reason="anchor data not fetched",
)

SEED, HOUSEHOLDS, DAYS, KILL_AFTER = 108, 60, 5, 2


@pytest.fixture(scope="module")
def world():
    block = Block.load()
    hhs, people = synthesize(SEED, block, n_households=HOUSEHOLDS)
    return block, hhs, people


class _Killed(RuntimeError):
    """Stands in for the process dying between days."""


def _uninterrupted(tmp_path, world):
    block, hhs, people = world
    tmp_path.mkdir(parents=True, exist_ok=True)
    log = EventLog(tmp_path / "whole.db")
    engine.run_simulation(log, SEED, block, hhs, people, days=DAYS, hazards=True)
    h = log.determinism_hash()
    log.close()
    return h


def _killed_and_resumed(tmp_path, world):
    block, hhs, people = world
    tmp_path.mkdir(parents=True, exist_ok=True)
    db, ckpt = tmp_path / "part.db", tmp_path / "part.state"

    # First life: asks for the SAME number of days, dies partway. Asking for
    # DAYS here is load-bearing — run.meta records the days requested, so a run
    # launched as `--days 3` and later extended to 5 is a genuinely different
    # run and hashes differently. Resume continues a run; it does not extend one.
    log = EventLog(db)

    def save(day, st):
        ckpt_mod.save(
            ckpt, st, day=day,
            seq=log._conn.execute("SELECT coalesce(max(seq),0) FROM event").fetchone()[0],
            run_seed=SEED, households=HOUSEHOLDS, block="kasba",
        )
        if day == KILL_AFTER:
            raise _Killed

    with pytest.raises(_Killed):
        engine.run_simulation(log, SEED, block, hhs, people, days=DAYS,
                              hazards=True, on_day_end=save)
    log.close()

    # Second life: a fresh process would open the log afresh, so this does too.
    log = EventLog(db)
    state, next_day, dropped = ckpt_mod.resume_point(
        ckpt, log, run_seed=SEED, households=HOUSEHOLDS, block="kasba"
    )
    engine.run_simulation(log, SEED, block, hhs, people, days=DAYS - next_day,
                          start_day=next_day, state=state, hazards=True)
    h = log.determinism_hash()
    log.close()
    return h, next_day, dropped


def test_a_resumed_run_is_byte_identical_to_one_that_never_stopped(tmp_path, world):
    whole = _uninterrupted(tmp_path / "a", world)
    resumed, next_day, _dropped = _killed_and_resumed(tmp_path / "b", world)
    assert next_day == KILL_AFTER + 1
    assert resumed == whole, (
        "a resumed run diverged from an uninterrupted one — the checkpoint does not "
        "capture the whole world, and everything downstream treats the log as the run"
    )


def test_the_checkpoint_refuses_another_world(tmp_path, world):
    """The failure this guards against does not raise on its own: every id in a
    kasba checkpoint resolves to somebody in an oldcity run."""
    block, hhs, people = world
    log = EventLog(tmp_path / "x.db")
    _n, state = engine.run_simulation(log, SEED, block, hhs, people, days=1)
    ckpt_mod.save(tmp_path / "x.state", state, day=0, seq=1,
                  run_seed=SEED, households=HOUSEHOLDS, block="kasba")
    log.close()
    for field, kw in (("seed", {"run_seed": 999}), ("households", {"households": 12000}),
                      ("block", {"block": "oldcity"})):
        args = {"run_seed": SEED, "households": HOUSEHOLDS, "block": "kasba", **kw}
        with pytest.raises(ckpt_mod.CheckpointMismatch) as exc:
            ckpt_mod.load(tmp_path / "x.state", **args)
        assert field in str(exc.value)


def test_a_checkpoint_from_a_different_engine_is_refused(tmp_path):
    """SimState gaining or losing a field must not half-load: an absent `acted`
    or `fired` would silently re-fire a whole lane rather than fail."""
    import pickle

    blob = {"format": ckpt_mod.FORMAT, "fingerprint": ("canon", "registry"), "day": 3,
            "seq": 100, "run_seed": SEED, "households": HOUSEHOLDS, "block": "kasba",
            "state": None}
    (tmp_path / "old.state").write_bytes(pickle.dumps(blob))
    with pytest.raises(ckpt_mod.CheckpointMismatch) as exc:
        ckpt_mod.load(tmp_path / "old.state", run_seed=SEED,
                      households=HOUSEHOLDS, block="kasba")
    assert "changed shape" in str(exc.value)


def test_resume_will_not_extend_a_run(tmp_path, world):
    """`--days 3` finished and resumed as `--days 5` is a genuinely different
    run from an uninterrupted `--days 5`: run.meta records the days asked for,
    so the hashes differ. Measured that way before this guard existed, and the
    difference is far easier to hit than to diagnose."""
    block, hhs, people = world
    log = EventLog(tmp_path / "y.db")
    _n, state = engine.run_simulation(log, SEED, block, hhs, people, days=3)
    ckpt_mod.save(tmp_path / "y.state", state, day=2, seq=10,
                  run_seed=SEED, households=HOUSEHOLDS, block="kasba", days=3)
    log.close()
    with pytest.raises(ckpt_mod.CheckpointMismatch) as exc:
        ckpt_mod.load(tmp_path / "y.state", run_seed=SEED, households=HOUSEHOLDS,
                      block="kasba", days=5)
    assert "does not extend one" in str(exc.value)
