import numpy as np

from punesim.kernel import keyed_rng


def test_same_key_same_sequence():
    a = keyed_rng(1, "hazard", "edge:42", 100, "crash").integers(0, 2**32, size=8)
    b = keyed_rng(1, "hazard", "edge:42", 100, "crash").integers(0, 2**32, size=8)
    assert np.array_equal(a, b)


def test_key_components_independent():
    base = keyed_rng(1, "d", "e", 0, "p").integers(0, 2**32)
    assert base != keyed_rng(2, "d", "e", 0, "p").integers(0, 2**32)
    assert base != keyed_rng(1, "d2", "e", 0, "p").integers(0, 2**32)
    assert base != keyed_rng(1, "d", "e2", 0, "p").integers(0, 2**32)
    assert base != keyed_rng(1, "d", "e", 1, "p").integers(0, 2**32)
    assert base != keyed_rng(1, "d", "e", 0, "p2").integers(0, 2**32)
    assert base != keyed_rng(1, "d", "e", 0, "p", draw_index=1).integers(0, 2**32)


def test_draws_are_order_independent():
    """Drawing for entity B must not perturb entity A — the branch guarantee."""
    a_alone = keyed_rng(7, "life", "person:A", 5, "job").random()
    _ = keyed_rng(7, "life", "person:B", 5, "job").random()
    a_after_b = keyed_rng(7, "life", "person:A", 5, "job").random()
    assert a_alone == a_after_b


def test_int_entity_id_normalized():
    assert (
        keyed_rng(1, "d", 42, 0, "p").integers(0, 2**32)
        == keyed_rng(1, "d", "42", 0, "p").integers(0, 2**32)
    )
