"""Law 4: one RNG doctrine — counter-based Philox keyed by the six-tuple
(run_seed, domain, entity_id, tick_or_day, purpose, draw_index).

Every random draw anywhere in the sim is a pure function of its key, so
injecting or removing an event never perturbs unrelated entities' draws —
this is what makes clean what-if branches possible. No subsystem may keep
private RNG state; `PCG64`/`default_rng` are banned imports (ruling 17).
"""

from hashlib import blake2b

import numpy as np

_SEP = b"\x1f"


def _key128(
    run_seed: int, domain: str, entity_id: str, tick_or_day: int, purpose: str, draw_index: int
) -> int:
    h = blake2b(digest_size=16)
    for part in (str(run_seed), domain, entity_id, str(tick_or_day), purpose, str(draw_index)):
        h.update(part.encode("utf-8"))
        h.update(_SEP)
    return int.from_bytes(h.digest(), "little")


def keyed_rng(
    run_seed: int,
    domain: str,
    entity_id: str | int,
    tick_or_day: int,
    purpose: str,
    draw_index: int = 0,
) -> np.random.Generator:
    """A fresh, order-independent generator for exactly one keyed decision."""
    key = _key128(int(run_seed), str(domain), str(entity_id), int(tick_or_day), str(purpose), int(draw_index))
    return np.random.Generator(np.random.Philox(key=key))
