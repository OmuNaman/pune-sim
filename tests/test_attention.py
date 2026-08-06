from punesim.kernel import AttentionField


def test_focus_dominates_and_topk_is_deterministic():
    a = AttentionField()
    a.set_focus("hh:h1", 3.0)
    a.bump("hh:h2", 1.0, tick=10)
    assert a.top_k(["hh:h3", "hh:h2", "hh:h1"], 2, tick=10) == ["hh:h1", "hh:h2"]
    # ties break by entity id, not dict order
    assert a.top_k(["hh:z", "hh:a"], 1, tick=10) == ["hh:a"]


def test_perturbation_decays():
    a = AttentionField(half_life_ticks=10)
    a.bump("hh:h2", 2.0, tick=0)
    assert abs(a.score("hh:h2", tick=10) - 1.0) < 1e-9  # one half-life
    assert a.score("hh:h2", tick=1000) < 0.01


def test_bumps_accumulate():
    a = AttentionField(half_life_ticks=10)
    a.bump("e", 1.0, tick=0)
    a.bump("e", 1.0, tick=0)
    assert abs(a.score("e", 0) - 2.0) < 1e-9
