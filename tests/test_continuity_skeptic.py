"""The skeptic that refuted a true finding with a date it made up.

The 30-day soak found a real contradiction — a hospitalised child written into
his own front room on three consecutive mornings — and the continuity read
reported PASS, because the independent skeptic threw all three findings out. One
of its refutations read:

    "Day 7 is Monday 12 Jan 2026, well after Suhas's discharge on Fri 9 Jan;
     he is at home recovering, making the scene compatible."

Day 0 of that run is Thursday 1 January, so day 7 is Thursday the 8th and the
boy was still admitted. The sentence is confident, specific, and false.

The skeptic is told "when unsure, refute — a false alarm is worse than a miss",
which is right for judgement calls and is not a licence to invent facts. Two
defences: it is now given the date so it never has to derive one, and any
refutation that ties a weekday to the scene's day is checked against the
calendar before it is allowed to kill a finding.
"""

import importlib.util
import pathlib
import sys

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "continuity_read",
    pathlib.Path(__file__).resolve().parents[1] / "scripts" / "continuity_read.py",
)
continuity_read = importlib.util.module_from_spec(_SPEC)
sys.modules["continuity_read"] = continuity_read
_SPEC.loader.exec_module(continuity_read)

_bad_weekday = continuity_read._bad_weekday

# Day 0 of a run is Thursday 01 January 2026, so day 7 is Thursday the 8th.
THURSDAY_DAY = 7


def test_it_catches_the_sentence_that_actually_got_through():
    why = ("Day 7 is Monday 12 Jan 2026, well after Suhas's discharge on Fri 9 Jan; "
           "he is at home recovering, making the scene compatible.")
    complaint = _bad_weekday(why, THURSDAY_DAY)
    assert complaint is not None
    assert "Monday" in complaint and "Thursday" in complaint


@pytest.mark.parametrize("why", [
    "Day 7 was Sunday, so the school reference is fine.",
    "day 7 falls on Saturday and canon is silent about weekends.",
    "The scene is fine because day 7, a Tuesday, has no canon entry.",
])
def test_it_catches_the_other_ways_of_saying_it(why):
    assert _bad_weekday(why, THURSDAY_DAY) is not None


def test_it_accepts_the_right_weekday():
    assert _bad_weekday("Day 7 is Thursday, and canon is silent on lunch.", THURSDAY_DAY) is None


@pytest.mark.parametrize("why", [
    # A refutation may legitimately name OTHER days — the discharge really was
    # on a Friday. Flagging these would make the guard fire constantly and it
    # would be turned off, which is worse than not having it.
    "Canon shows the discharge on Friday, so resting at home on Saturday is compatible.",
    "The injury was cleared on Tuesday 13 Jan; a residual limp is not ruled out.",
    "Canon is silent on whether the boys attended school that day.",
    "",
])
def test_it_stays_quiet_on_refutations_that_are_not_about_this_day(why):
    assert _bad_weekday(why, THURSDAY_DAY) is None


def test_the_guard_is_about_this_day_not_any_day():
    """'day 12 is Monday' in a finding about day 7 is not this guard's business —
    it may well be true, and the guard must not become a general fact-checker it
    is not equipped to be."""
    assert _bad_weekday("Day 12 is Monday 12 Jan.", THURSDAY_DAY) is None
