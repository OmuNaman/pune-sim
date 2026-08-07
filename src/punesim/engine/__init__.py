"""V0 engine: clockwork days, scenes, injections, stub institutions.

Day pipeline:
  1. T1 morning scenes for spotlit households (06:30, may revise routines);
  2. compile the day: routine templates or scene-revised plans, merged with
     injected events and stub-institution reactions; hospital admissions
     mechanically invalidate the patient's remaining day (plan invalidation);
  3. if an injection touches a household and minds are on, the day SPLITS at
     the moment the family learns: phase A commits, the T2 reaction scene runs
     right there (it can rewrite the rest of the family's day), then phase B
     commits. This is the same-day lane the morning gate cannot provide
     (09-collective-dynamics break B9, V0-thin).

Two runs from the same seed + same cassettes are hash-identical.
"""

from ..world import hazards as hazards_mod
from .day import run_day, run_days
from .injection import Injection
from .loop import run_simulation
from .reactions import (
    CASUALTY_PREFIXES,
    RESOLUTION_PREDICATES,
    stub_institution_reactions,
)
from .state import (
    DAILY_WAGE,
    GATE_BURST,
    HYSTERESIS,
    HYSTERESIS_DAYS,
    NO_WORK,
    P_THRESHOLD,
    REACTION_DELAY_S,
    SimState,
)

# Everything that was reachable as `engine.X` when this was one module stays
# reachable. The split's gates could not have caught a narrowing here — nothing
# in tests/ imports `SimState`, yet `run_simulation` *returns* one, so dropping
# it would have left callers unable to name their own result.
__all__ = [
    "CASUALTY_PREFIXES",
    "DAILY_WAGE",
    "GATE_BURST",
    "HYSTERESIS",
    "HYSTERESIS_DAYS",
    "NO_WORK",
    "P_THRESHOLD",
    "REACTION_DELAY_S",
    "RESOLUTION_PREDICATES",
    "Injection",
    "SimState",
    "hazards_mod",
    "run_day",
    "run_days",
    "run_simulation",
    "stub_institution_reactions",
]
