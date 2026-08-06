import pytest
from pydantic import ValidationError

from punesim.kernel import WorldDelta


def test_empty_delta_is_valid():
    d = WorldDelta()
    assert d.events == [] and d.canon_facts == [] and d.narration == ""


def test_full_union_round_trip():
    d = WorldDelta.model_validate(
        {
            "narration": "The bus clipped a handcart at the chowk.",
            "transcript": "A: aai ga! B: thamb, thamb.",
            "events": [
                {
                    "type": "hazard.road.collision",
                    "delay_s": 0,
                    "severity": 0.4,
                    "participants": [{"entity_id": "person:p12", "role": "patient"}],
                    "payload": {"mechanism": "sideswipe"},
                }
            ],
            "conditions": [{"entity_id": "person:p12", "kind": "injury", "intensity": 0.3}],
            "canon_facts": [{"subject": "person:p12", "predicate": "state.injury", "value": "slight"}],
            "relationship_deltas": [{"a": "person:p1", "b": "person:p2", "dim": "trust", "delta": -0.1}],
            "memory_writes": [{"person_id": "person:p1", "salience": 0.8, "summary": "Saw the crash."}],
            "process_ops": [{"op": "create", "kind": "police_case", "template_id": "fir_bnss"}],
            "world_ops": [{"op": "entity_modify", "payload": {"entity": "edge:9", "closed": True}}],
            "day_plan": [
                {
                    "person_id": "person:p1",
                    "steps": [{"t": 28800, "place_ref": "place:school", "activity": "drop_child", "mode": "walk"}],
                }
            ],
            "messages": [{"sender": "person:p1", "recipients": ["person:p2"], "channel": "phone", "text": "Bus la ushir hoil."}],
            "commitments": [{"owner": "hh:h3", "kind": "obligation", "vars": {"emi": 3200}}],
            "mood_deltas": [{"person_id": "person:p1", "dim": "stress", "delta": 0.2}],
        }
    )
    assert d == WorldDelta.model_validate(d.model_dump())


def test_unknown_field_is_rejected():
    with pytest.raises(ValidationError):
        WorldDelta.model_validate({"naration_typo": "x"})


def test_bounds_enforced():
    with pytest.raises(ValidationError):
        WorldDelta.model_validate({"conditions": [{"entity_id": "e", "kind": "k", "intensity": 1.5}]})
