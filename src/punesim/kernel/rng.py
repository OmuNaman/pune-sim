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


def keyed_uniform(
    run_seed: int,
    domain: str,
    entity_id: str | int,
    tick_or_day: int,
    purpose: str,
    draw_index: int = 0,
) -> float:
    """One uniform in [0, 1), from the same six-tuple key. Use for a *single*
    coin-flip; use `keyed_rng` for anything that needs a real generator.

    A deliberate, narrow deviation from "numpy Philox everywhere", made after
    measuring: `keyed_rng(...).random()` costs 21.5us, of which 19.3us is
    constructing the Generator and Philox objects — to produce one float. The
    info lane makes one of these per contact per held claim, so at V3 scale it
    was the single most expensive thing the simulation did.

    What law 4 is actually for is preserved exactly: the draw is a pure
    function of its key, no state is kept anywhere, and injecting or removing
    an event cannot perturb an unrelated entity's draws. Only the bit-generator
    is different, and for one Bernoulli decision a keyed hash is as sound a
    source of a uniform as a counter-based PRNG seeded from that same hash.
    """
    h = blake2b(digest_size=8)
    for part in (str(run_seed), domain, str(entity_id), str(tick_or_day), purpose, str(draw_index)):
        h.update(part.encode("utf-8"))
        h.update(_SEP)
    return int.from_bytes(h.digest(), "little") / 18446744073709551616.0
