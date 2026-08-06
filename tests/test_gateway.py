from pathlib import Path

import pytest
from pydantic import BaseModel

from punesim.config import Config
from punesim.llm import Cassette, CassetteMiss, Gateway, RefusalError, detect_refusal


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


def test_detect_refusal_heuristics():
    assert detect_refusal("I'm sorry, but I can't assist with that.")
    assert detect_refusal("")
    assert detect_refusal("No.")
    assert not detect_refusal('{"outcome": "the family argues about the match", "mood": -0.2}')
    assert not detect_refusal(
        "The uncle raises his voice about the proposal. " * 5 + "They part without agreement."
    )
