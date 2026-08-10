"""The audit's own probes, tested on events built for the purpose.

A probe is only worth having if it fires when it should and stays quiet when it
should. Both halves need checking, and waiting for a soak to happen to contain
the right shape of data is not checking.
"""

import importlib.util
import pathlib
import sys
from dataclasses import dataclass

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "audit_run", pathlib.Path(__file__).resolve().parents[1] / "scripts" / "audit_run.py"
)
audit_run = importlib.util.module_from_spec(_SPEC)
sys.modules["audit_run"] = audit_run
_SPEC.loader.exec_module(audit_run)


@dataclass
class _P:
    id: str
    household_id: str


@dataclass
class _H:
    id: str


def _world(n_households: int = 40):
    hhs = [_H(f"hh:{i:03d}") for i in range(n_households)]
    people = {f"person:{i:03d}.0": _P(f"person:{i:03d}.0", f"hh:{i:03d}")
              for i in range(n_households)}
    return hhs, people


def _ev(seq: int, type_: str, payload: dict):
    return audit_run.Event(seq=seq, sim_time=seq * 60, type=type_, payload=payload,
                           caused_by=None, provenance="clockwork")


def _run(events, hhs, people, days=10):
    a = audit_run.Audit(events, people, hhs, days)
    a.probe_observer_effect()
    return a.results[-1]


def test_observer_effect_reports_equal_rates_as_equal():
    hhs, people = _world()
    events, seq = [], 0
    watched = [h.id for h in hhs[:20]]
    for h in hhs:  # every household, watched or not, has the same bad luck
        seq += 1
        events.append(_ev(seq, "pressure.crossed", {"person": f"person:{h.id[3:]}.0"}))
    for h in watched:
        seq += 1
        events.append(_ev(seq, "scene.morning", {"household": h}))
    r = _run(events, hhs, people)
    assert r.status == "INFO", r.headline
    assert "x1.00" in " ".join(r.hits)


def test_observer_effect_never_returns_a_verdict():
    """The gap this builds is threefold, which an earlier version of this probe
    called a WARN. It cannot be one. `scene.reaction` fires *on* the events
    being counted, so a household earns its way on camera by having exactly the
    misfortune the watched column then attributes to being on camera. The
    number is worth printing and is not evidence of anything by itself; the
    paired run in scripts/observer_effect.py is what answers the question."""
    hhs, people = _world()
    events, seq = [], 0
    watched = [h.id for h in hhs[:20]]
    for h in hhs:
        for _ in range(3 if h.id in watched else 1):
            seq += 1
            events.append(_ev(seq, "pressure.crossed", {"person": f"person:{h.id[3:]}.0"}))
    for h in watched:
        seq += 1
        events.append(_ev(seq, "scene.morning", {"household": h}))
    r = _run(events, hhs, people)
    assert r.status == "INFO", r.headline
    assert r.status not in ("PASS", "WARN", "FAIL")
    assert "x3.00" in " ".join(r.hits)


def test_observer_effect_prints_what_the_unwatched_rate_predicted():
    """A ratio with no denominator is how a 2x on one event reads as a finding.
    14 households on camera out of 12,000 is the real V3 shape, so the expected
    count has to sit next to the ratio."""
    hhs, people = _world(n_households=200)
    events, seq = [], 0
    watched = [h.id for h in hhs[:10]]
    for h in hhs[10:]:  # 190 unwatched households, one event each
        seq += 1
        events.append(_ev(seq, "pressure.crossed", {"person": f"person:{h.id[3:]}.0"}))
    seq += 1  # and two among the watched, against an expectation of ten
    events.append(_ev(seq, "pressure.crossed", {"person": f"person:{watched[0][3:]}.0"}))
    seq += 1
    events.append(_ev(seq, "pressure.crossed", {"person": f"person:{watched[1][3:]}.0"}))
    for h in watched:
        seq += 1
        events.append(_ev(seq, "scene.morning", {"household": h}))
    r = _run(events, hhs, people)
    assert r.status == "INFO"
    row = next(h for h in r.hits if "pressure.crossed" in h)
    assert "2 ev" in row and "10.0 expected" in row


def test_observer_effect_refuses_to_pass_on_an_empty_table():
    """Nothing happening to anybody is not evidence that watching is harmless."""
    hhs, people = _world()
    events = [_ev(i, "scene.morning", {"household": h.id})
              for i, h in enumerate(hhs[:20], start=1)]
    events.append(_ev(999, "pressure.crossed", {"person": "person:000.0"}))
    r = _run(events, hhs, people)
    assert r.status == "SKIP"
    assert "nothing to compare" in r.headline


def test_observer_effect_skips_when_nobody_is_watched():
    hhs, people = _world()
    events = [_ev(i, "pressure.crossed", {"person": f"person:{h.id[3:]}.0"})
              for i, h in enumerate(hhs, start=1)]
    r = _run(events, hhs, people)
    assert r.status == "SKIP"
    assert "no scenes" in r.headline


@pytest.mark.parametrize("watched_n", [1, 4])
def test_observer_effect_skips_on_too_few_households(watched_n):
    hhs, people = _world()
    events, seq = [], 0
    for h in hhs:
        for _ in range(2):
            seq += 1
            events.append(_ev(seq, "pressure.crossed", {"person": f"person:{h.id[3:]}.0"}))
    for h in hhs[:watched_n]:
        seq += 1
        events.append(_ev(seq, "scene.morning", {"household": h.id}))
    r = _run(events, hhs, people)
    assert r.status == "SKIP"
    assert "no control group" in r.headline
