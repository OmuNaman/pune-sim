"""Fixtures shared across the suite."""

from dataclasses import replace

import pytest

from punesim.world import classdefs, hazards


@pytest.fixture
def hazard_density(monkeypatch):
    """Let a 20-household test world draw hazards at a city's absolute rate.

    Rates are per-capita now (`data/classdefs/hazards.json`), which is correct
    and makes a small block quiet: 160 people draw 0.0007 hazards a day, so a
    30-day test asserting "a hazard rippled" would be asserting on an empty log
    and would pass or fail on nothing.

    Scaling by reference-population / this-population gives the small world the
    daily rate the four-peth block has — which is exactly what the old absolute
    p_per_day meant. Every test that uses this is testing the ripple machinery,
    never the rate; the rate has its own test in test_hazard_rates.py.
    """
    def _scale(people: dict) -> None:
        factor = classdefs.REFERENCE_POPULATION / max(1, len(people))
        monkeypatch.setattr(hazards, "CLASSES", [
            replace(c, rate_per_1k_per_year=c.rate_per_1k_per_year * factor)
            for c in hazards.CLASSES
        ])
    return _scale
