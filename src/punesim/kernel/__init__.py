"""The constitution kernel: the five laws as code.

1. The event log is truth            -> log.EventLog
2. One fact gate                     -> facts.assert_facts / Canon / registry
3. One WorldDelta schema             -> worlddelta.WorldDelta
4. One RNG doctrine                  -> rng.keyed_rng
5. One attention/budget authority    -> attention.AttentionField
   (+ one time substrate             -> timebase, ruling §9.1-2)
"""

from .attention import AttentionField
from .facts import (
    AssertResult,
    Canon,
    PredicateRegistry,
    PredicateSpec,
    assert_facts,
    core_registry,
)
from .log import Event, EventIn, EventLog
from .rng import keyed_rng
from .worlddelta import FactAssertion, WorldDelta

__all__ = [
    "AssertResult",
    "AttentionField",
    "Canon",
    "Event",
    "EventIn",
    "EventLog",
    "FactAssertion",
    "PredicateRegistry",
    "PredicateSpec",
    "WorldDelta",
    "assert_facts",
    "core_registry",
    "keyed_rng",
]
