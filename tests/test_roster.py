"""Which world does a log belong to?

The bug this guards against does not raise. `hh:000` and `person:001.1` exist in
every world this repo can synthesize, so a tool pointed at the wrong roster
prints a different family's name over the right family's events and looks
entirely fine. It has already cost one soak (nineteen probes passed against a
world nobody had run) and it was still live in `punesim interview`, `punesim
follow` and `scripts/continuity_read.py` — the last of which decides V1's exit.
"""

import pytest

from punesim.world.roster import RosterMismatch, read_meta, world_for_log


class _Ev:
    def __init__(self, payload):
        self.payload = payload
        self.type = "run.meta"


class _Log:
    """Just enough EventLog to answer `events(type="run.meta")`."""

    def __init__(self, meta: dict | None):
        self._meta = meta

    def events(self, *, type=None, **_kw):
        if type == "run.meta" and self._meta is not None:
            yield _Ev(dict(self._meta))


KASBA = {"seed": 108, "households": 80, "days": 30}
OLDCITY = {"seed": 108, "households": 12000, "days": 30, "block": "oldcity"}


def test_the_log_decides_when_the_caller_says_nothing():
    block, hhs, people, meta = world_for_log(_Log(OLDCITY))
    assert block.name == "oldcity"
    assert len(hhs) == 12000 and len(people) == 49578
    assert meta["households"] == 12000


def test_a_log_without_a_block_key_means_the_default_one():
    """`loop.py` records `block` only when it is not the default, so its absence
    is a positive statement that the run was on kasba — not missing data."""
    block, _hhs, people, _meta = world_for_log(_Log(KASBA))
    assert block.name == "kasba" and len(people) == 306


@pytest.mark.parametrize("field, value", [
    ("seed", 999), ("households", 80), ("block", "kasba"),
])
def test_an_explicit_disagreement_is_refused(field, value):
    kwargs = {"seed": None, "households": None, "block": None, field: value}
    with pytest.raises(RosterMismatch) as exc:
        world_for_log(_Log(OLDCITY), **kwargs)
    assert field in str(exc.value)


def test_agreeing_explicitly_is_fine():
    block, _hhs, people, _meta = world_for_log(
        _Log(OLDCITY), seed=108, households=12000, block="oldcity"
    )
    assert block.name == "oldcity" and len(people) == 49578


def test_a_default_that_disagrees_is_not_a_refusal():
    """Only what the caller ASKED for is worth refusing over. A command whose
    --households default happens to be 80 must not reject a 12,000-household
    log it was pointed at deliberately."""
    block, _hhs, people, _meta = world_for_log(
        _Log(OLDCITY), fallback_seed=108, fallback_households=80
    )
    assert block.name == "oldcity" and len(people) == 49578


def test_a_log_with_no_meta_falls_back_rather_than_guessing_wrong():
    block, _hhs, people, meta = world_for_log(
        _Log(None), fallback_seed=108, fallback_households=80
    )
    assert meta == {} and block.name == "kasba" and len(people) == 306


def test_no_meta_and_no_seed_is_an_error_not_a_default():
    with pytest.raises(RosterMismatch) as exc:
        world_for_log(_Log(None))
    assert "no run.meta" in str(exc.value)


def test_read_meta_returns_none_for_a_log_without_one():
    assert read_meta(_Log(None)) is None
    assert read_meta(_Log(KASBA))["seed"] == 108
