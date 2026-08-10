"""The paired-run observer-effect differ: its statistic and its guards.

The statistic decides whether a difference between the two arms gets called a
finding, so it is worth pinning against numbers computed by hand rather than
against whatever the implementation happened to return the first time.
"""

import importlib.util
import pathlib
import sys

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "observer_effect",
    pathlib.Path(__file__).resolve().parents[1] / "scripts" / "observer_effect.py",
)
observer_effect = importlib.util.module_from_spec(_SPEC)
sys.modules["observer_effect"] = observer_effect
_SPEC.loader.exec_module(observer_effect)

binom_two_sided = observer_effect.binom_two_sided
pair_problem = observer_effect.pair_problem


@pytest.mark.parametrize("k, n, expected", [
    (5, 10, 1.0),            # dead even
    (0, 10, 2 / 1024),       # every event in one arm: (1 + 1) / 2^10
    (10, 10, 2 / 1024),      # ...and symmetric
    (1, 10, 22 / 1024),      # 2 * (C(10,0) + C(10,1)) / 2^10
    (8, 10, 112 / 1024),     # 2 * (C(10,8) + C(10,9) + C(10,10)) / 2^10
    (3, 6, 1.0),
    (0, 0, 1.0),             # no events at all is not evidence of anything
])
def test_binomial_matches_hand_computed_values(k, n, expected):
    assert binom_two_sided(k, n) == pytest.approx(expected, abs=1e-12)


def test_binomial_is_symmetric():
    for n in (7, 20, 41):
        for k in range(n + 1):
            assert binom_two_sided(k, n) == pytest.approx(binom_two_sided(n - k, n))


def test_a_large_run_needs_a_real_gap_to_register():
    """The point of the statistic. 30 sim-days at 80 households produce a few
    hundred outcome events, and a handful more on one side of a pair is what
    noise looks like — not a soap opera."""
    assert binom_two_sided(260, 500) > 0.05      # 260 vs 240: nothing
    assert binom_two_sided(300, 500) < 0.001     # 300 vs 200: something


def _arm(seed=108, households=80, block="kasba", days=30, scenes=5, reactions=0):
    return {"meta": {"seed": seed, "households": households, "block": block, "days": days},
            "types": {"scene.morning": scenes, "scene.reaction": reactions}}


def test_a_matched_pair_is_accepted():
    assert pair_problem(_arm(), _arm(scenes=0)) is None


@pytest.mark.parametrize("field, value", [
    ("seed", 109), ("households", 12000), ("block", "oldcity"), ("days", 14),
])
def test_a_mismatched_pair_is_refused(field, value):
    """Diffing two different worlds produces a confident number about nothing."""
    problem = pair_problem(_arm(), _arm(scenes=0, **{field: value}))
    assert problem and field in problem


def test_the_off_arm_must_actually_have_the_camera_off():
    assert "has scenes in it" in pair_problem(_arm(), _arm(scenes=0, reactions=3))


def test_the_on_arm_must_actually_have_the_camera_on():
    assert "no scenes" in pair_problem(_arm(scenes=0), _arm(scenes=0))
