from punesim.kernel import Canon, EventLog, FactAssertion, assert_facts, core_registry


def _gate(log, canon, facts, provenance, tier=0):
    return assert_facts(
        log, canon, core_registry(), facts, provenance=provenance, sim_time=600, disclosure_tier=tier
    )


def test_establish_and_supersede(tmp_path):
    log, canon = EventLog(tmp_path / "e.db"), Canon()
    f = FactAssertion(subject="person:p1", predicate="person.name", value="Sunita Jagtap")
    r1 = _gate(log, canon, [f], "synthesis")
    assert r1.accepted and not r1.rejected
    f2 = FactAssertion(subject="person:p1", predicate="person.name", value="Sunita Pawar")
    _gate(log, canon, [f2], "llm_scene")
    live = canon.live("person:p1", "person.name")
    assert [r.value for r in live] == ["Sunita Pawar"]  # superseded, not deleted


def test_canon_is_a_projection_of_the_log(tmp_path):
    log, canon = EventLog(tmp_path / "e.db"), Canon()
    _gate(log, canon, [FactAssertion(subject="s", predicate="person.name", value="A")], "synthesis")
    _gate(log, canon, [FactAssertion(subject="s", predicate="person.name", value="B")], "synthesis")
    rebuilt = Canon.from_log(log)
    assert [r.value for r in rebuilt.live("s", "person.name")] == [
        r.value for r in canon.live("s", "person.name")
    ]


def test_clockwork_only_rejects_llm(tmp_path):
    log, canon = EventLog(tmp_path / "e.db"), Canon()
    f = FactAssertion(subject="person:p1", predicate="person.age_years", value=44)
    r = _gate(log, canon, [f], "llm_scene")
    assert r.rejected and r.rejected[0][1] == "clockwork_only"
    assert _gate(log, canon, [f], "clockwork").accepted


def test_scene_gated_needs_disclosure_tier(tmp_path):
    log, canon = EventLog(tmp_path / "e.db"), Canon()
    f = FactAssertion(
        subject="person:p7",
        predicate="att.stance",
        value={"target": "union:p8xp9", "stance": -0.8, "basis": ["religious", "family_honor"]},
    )
    r0 = _gate(log, canon, [f], "llm_scene", tier=0)
    assert r0.rejected and r0.rejected[0][1] == "requires_disclosure_tier_1"
    r1 = _gate(log, canon, [f], "llm_scene", tier=1)
    assert r1.accepted


def test_unregistered_predicate_rejected(tmp_path):
    log, canon = EventLog(tmp_path / "e.db"), Canon()
    r = _gate(log, canon, [FactAssertion(subject="s", predicate="made.up", value=1)], "llm_scene")
    assert r.rejected[0][1] == "unregistered_predicate"
