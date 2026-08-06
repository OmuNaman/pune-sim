from pathlib import Path

import pytest
from pydantic import BaseModel

from punesim.config import Config
from punesim.llm import Cassette, CassetteMiss, Gateway, RefusalError, detect_refusal
from punesim.llm.gateway import SchemaError


class SceneOut(BaseModel):
    model_config = {"extra": "forbid"}
    outcome: str
    mood: float


def _cfg(tmp_path: Path, mode: str) -> Config:
    return Config(
        run_seed=1,
        llm_mode=mode,
        scene_gate_mode="spotlight",
        openrouter_api_key="test-key",
        openrouter_base_url="https://openrouter.ai/api/v1",
        model_workhorse="fake/workhorse",
        model_flash="fake/flash",
        model_premium="fake/premium",
        runs_dir=tmp_path,
    )


class FakeTransport:
    """Scriptable transport: pops replies per model; counts network calls."""

    def __init__(self, replies: dict[str, list[str]]):
        self.replies = {m: list(r) for m, r in replies.items()}
        self.calls: list[str] = []

    def __call__(self, model, messages, temperature, max_tokens):
        self.calls.append(model)
        return self.replies[model].pop(0), {"total_tokens": 10}


MSGS = [{"role": "user", "content": "resolve the scene"}]


def test_record_then_replay_without_network(tmp_path):
    t = FakeTransport({"fake/workhorse": ['{"outcome": "resolved", "mood": 0.2}']})
    g = Gateway(_cfg(tmp_path, "record"), Cassette(tmp_path / "c.db"), transport=t)
    r1 = g.call("scene", MSGS, SceneOut)
    assert r1.parsed.outcome == "resolved" and len(t.calls) == 1

    t2 = FakeTransport({})  # would raise on any call
    g2 = Gateway(_cfg(tmp_path, "replay"), Cassette(tmp_path / "c.db"), transport=t2)
    r2 = g2.call("scene", MSGS, SceneOut)
    assert r2.parsed == r1.parsed and t2.calls == []


def test_replay_miss_is_hard_error(tmp_path):
    g = Gateway(_cfg(tmp_path, "replay"), Cassette(tmp_path / "c.db"), transport=FakeTransport({}))
    with pytest.raises(CassetteMiss):
        g.call("scene", MSGS, SceneOut)


def test_refusal_reroutes_to_premium_once(tmp_path):
    t = FakeTransport(
        {
            "fake/workhorse": ["I'm sorry, but I can't assist with that request."],
            "fake/premium": ['{"outcome": "father objects, talks continue", "mood": -0.4}'],
        }
    )
    g = Gateway(_cfg(tmp_path, "record"), Cassette(tmp_path / "c.db"), transport=t)
    r = g.call("scene", MSGS, SceneOut)
    assert r.status == "rerouted_premium" and r.model == "fake/premium"
    assert t.calls == ["fake/workhorse", "fake/premium"]


def test_double_refusal_raises_never_templates(tmp_path):
    t = FakeTransport(
        {
            "fake/workhorse": ["I cannot help with this."],
            "fake/premium": ["I must decline."],
        }
    )
    g = Gateway(_cfg(tmp_path, "record"), Cassette(tmp_path / "c.db"), transport=t)
    with pytest.raises(RefusalError):
        g.call("scene", MSGS, SceneOut)


def test_identity_class_routes_premium_directly(tmp_path):
    t = FakeTransport({"fake/premium": ['{"outcome": "ok", "mood": 0.0}']})
    g = Gateway(_cfg(tmp_path, "record"), Cassette(tmp_path / "c.db"), transport=t)
    r = g.call("scene", MSGS, SceneOut, identity_class=1)
    assert r.model == "fake/premium" and t.calls == ["fake/premium"]


def test_repair_round_fixes_bad_json(tmp_path):
    t = FakeTransport(
        {
            "fake/workhorse": [
                'Sure! Here is the scene: {"outcome": "resolved", "mood": "high"}',  # bad type
                '{"outcome": "resolved", "mood": 0.4}',
            ]
        }
    )
    g = Gateway(_cfg(tmp_path, "record"), Cassette(tmp_path / "c.db"), transport=t)
    r = g.call("scene", MSGS, SceneOut)
    assert r.status == "repaired" and r.parsed.mood == 0.4 and len(t.calls) == 2


_PROSE = (
    "The morning light falls across the courtyard and the family gathers for chai, "
    "speaking quietly about the day ahead and the work that waits for each of them."
)


def test_schema_failure_resamples_before_giving_up(tmp_path):
    """A model stuck in prose keeps apologizing in prose through the repair
    round; one clean resample of the ORIGINAL prompt rescues the scene."""
    t = FakeTransport(
        {"fake/workhorse": [_PROSE, _PROSE, '{"outcome": "resolved", "mood": 0.1}']}
    )
    g = Gateway(_cfg(tmp_path, "record"), Cassette(tmp_path / "c.db"), transport=t)
    r = g.call("scene", MSGS, SceneOut)
    assert r.status == "resampled" and r.parsed.mood == 0.1
    assert len(t.calls) == 3


def test_schema_failure_still_raises_when_resample_fails(tmp_path):
    t = FakeTransport({"fake/workhorse": [_PROSE, _PROSE, _PROSE]})
    g = Gateway(_cfg(tmp_path, "record"), Cassette(tmp_path / "c.db"), transport=t)
    with pytest.raises(SchemaError):
        g.call("scene", MSGS, SceneOut)


def test_resample_never_softens_replay_integrity(tmp_path):
    """Law 1: a replay miss inside the resample slot is still a hard error."""
    t = FakeTransport({"fake/workhorse": [_PROSE, _PROSE]})
    g = Gateway(_cfg(tmp_path, "record"), Cassette(tmp_path / "c.db"), transport=t)
    with pytest.raises(IndexError):  # transport exhausted rather than templating
        g.call("scene", MSGS, SceneOut)
    g2 = Gateway(_cfg(tmp_path, "replay"), Cassette(tmp_path / "c2.db"), transport=FakeTransport({}))
    with pytest.raises(CassetteMiss):
        g2.call("scene", MSGS, SceneOut)


def test_detect_refusal_heuristics():
    assert detect_refusal("I'm sorry, but I can't assist with that.")
    assert detect_refusal("")
    assert detect_refusal("No.")
    assert not detect_refusal('{"outcome": "the family argues about the match", "mood": -0.2}')
    assert not detect_refusal(
        "The uncle raises his voice about the proposal. " * 5 + "They part without agreement."
    )


def test_a_literal_newline_inside_a_narration_is_not_a_failure(tmp_path):
    """The soak's most severe hazard lost its reaction scene to this: orjson is
    strict about control characters, and the model had written a transcript
    with a real newline inside the string. Parsed locally, no retry, no new
    cassette slot — old recordings replay unchanged."""
    t = FakeTransport({"fake/workhorse": ['{"outcome": "he came\nhome late", "mood": -0.3}']})
    g = Gateway(_cfg(tmp_path, "record"), Cassette(tmp_path / "c.db"), transport=t)
    r = g.call("scene", MSGS, SceneOut)
    assert r.status == "ok" and len(t.calls) == 1
    assert r.parsed.outcome == "he came\nhome late"
