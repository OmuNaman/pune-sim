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


def test_focus_beats_any_amount_of_drama_elsewhere():
    """A followed family must not be pushed off screen by a big enough
    accident somewhere else — focus was only additive before, so a hazard
    bump of 5.0 outranked a focus level of 3.0."""
    a = AttentionField()
    a.set_focus("hh:followed", 3.0)
    a.bump("hh:crash", 5.0, tick=0)
    assert a.top_k(["hh:crash", "hh:followed"], 1, tick=0) == ["hh:followed"]


def test_never_rendered_outranks_recently_rendered():
    a = AttentionField()
    a.mark_rendered("hh:seen", 9)
    assert a.score("hh:unseen", tick=0, day=10) > a.score("hh:seen", tick=0, day=10)


def test_urgency_still_outranks_maximum_boredom():
    """The inequality that keeps this rotation and not chaos: a household
    something happened to yesterday beats one nothing has happened to in
    twenty days."""
    a = AttentionField()
    a.bump("hh:hurt", 1.5, tick=30 * 288 + 287)
    a.mark_rendered("hh:hurt", 30)
    assert a.score("hh:hurt", tick=31 * 288, day=31) > a.score("hh:bored", tick=31 * 288, day=31)


def test_a_quiescent_block_rotates_instead_of_freezing():
    """The soak's failure: uniform exponential decay is order-preserving, so
    once the bumps stop the top-k set is mathematically frozen forever. Eleven
    days ran with the same five households while a family that had simply
    never been bumped sat at exactly 0.0, locked out permanently."""
    a = AttentionField()
    ids = [f"hh:{i:03d}" for i in range(80)]
    a.bump("hh:005", 3.0, tick=0)  # one early flurry, then silence
    a.bump("hh:007", 2.0, tick=288)
    seen: set[str] = set()
    sets: list[tuple[str, ...]] = []
    for day in range(30):
        chosen = a.top_k(ids, 5, tick=day * 288, day=day)
        sets.append(tuple(chosen))
        seen.update(chosen)
        for hid in chosen:
            a.mark_rendered(hid, day)
    assert len(seen) == len(ids), f"only {len(seen)} of 80 households ever reached the camera"
    assert len(set(sets[:16])) == 16, "the same five households came back within 16 days"
