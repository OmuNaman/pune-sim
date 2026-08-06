"""V2 injection compiler: ground/validate/repair/preview — offline."""

import orjson
import pytest

from punesim import engine
from punesim.config import Config
from punesim.kernel.log import EventLog
from punesim.llm import Cassette, Gateway
from punesim.minds.compiler import CompileError, compile_injection
from punesim.population import synthesize
from punesim.world.block import Block

pytestmark = pytest.mark.skipif(
    not __import__("pathlib").Path("data/anchors/kasba_places.geojson").exists(),
    reason="anchor data not fetched",
)

SEED = 21


@pytest.fixture(scope="module")
def world():
    block = Block.load()
    hhs, people = synthesize(SEED, block, n_households=20)
    return block, hhs, people


class ScriptedTransport:
    def __init__(self, replies):
        self.replies = list(replies)
        self.prompts = []

    def __call__(self, model, messages, temperature, max_tokens):
        self.prompts.append(messages[-1]["content"])
        return self.replies.pop(0), {"total_tokens": 50}


def _gw(tmp_path, transport):
    cfg = Config(
        run_seed=SEED, llm_mode="record", scene_gate_mode="spotlight",
        openrouter_api_key="test", openrouter_base_url="x",
        model_workhorse="fake/flash", model_flash="fake/flash",
        model_premium="fake/premium", runs_dir=tmp_path,
    )
    return Gateway(cfg, Cassette(tmp_path / "c.db"), transport=transport)


def _spec(**kw) -> str:
    base = {
        "day": 1, "time": "12:00", "type": "hazard.road.collision",
        "place_ref": None, "participants": [], "severity": 0.5,
        "claim": None, "narrative": "A test event.", "notes": "",
    }
    return orjson.dumps({**base, **kw}).decode()


def test_valid_compile_first_try(tmp_path, world):
    block, _, people = world
    place = next(p for p in block.places if p.name)
    victim = min(people)
    t = ScriptedTransport([_spec(place_ref=place.id, participants=[victim])])
    out = compile_injection(_gw(tmp_path, t), block, people, "an accident", default_day=1)
    assert out.injection.place == place.id
    assert out.injection.participants == (victim,)
    assert place.name in out.preview
    # the world card grounded the prompt
    assert place.id in t.prompts[0] and victim in t.prompts[0]


def test_invalid_place_gets_repair_round_with_suggestions(tmp_path, world):
    block, _, people = world
    real = next(p for p in block.places if p.name)
    t = ScriptedTransport([
        _spec(place_ref="place:shaniwar_wada_invented"),
        _spec(place_ref=real.id),
    ])
    out = compile_injection(_gw(tmp_path, t), block, people, "an accident near shaniwar wada")
    assert out.injection.place == real.id
    assert "closest real places" in t.prompts[1]
    assert "failed validation" in t.prompts[1]


def test_double_failure_raises(tmp_path, world):
    block, _, people = world
    t = ScriptedTransport([
        _spec(place_ref="place:nope"),
        _spec(place_ref="place:still_nope", participants=["person:ghost"]),
    ])
    with pytest.raises(CompileError) as ei:
        compile_injection(_gw(tmp_path, t), block, people, "an accident")
    assert any("does not exist" in e for e in ei.value.errors)


def test_rumor_compiles_with_rendered_claim(tmp_path, world):
    block, _, people = world
    place = next(p for p in block.places if p.name)
    t = ScriptedTransport([
        _spec(
            type="info.rumor", place_ref=place.id, severity=None,
            claim={
                "key": "cl:test", "subject": place.id, "predicate": "contaminated",
                "topics": ["water"], "quantity": None, "unit": None, "charge": 0.8,
                "specificity": 0.5, "veracity": "false", "valence": -0.7,
            },
        )
    ])
    out = compile_injection(_gw(tmp_path, t), block, people, "rumor about bad water")
    claim = out.injection.payload["claim"]
    assert claim["predicate"] == "contaminated"
    assert place.name in claim["text"]


def test_novel_public_event_ripples_without_new_code(tmp_path, world):
    """The dynamism guarantee: a compiled 'assassination' — a type no engine
    code has ever seen — commits, gets witnessed, and spreads."""
    block, _, people = world
    worker = next(p for p in people.values() if p.work_id and p.occupation != "student")
    t = ScriptedTransport([
        _spec(type="event.assassination", place_ref=worker.work_id,
              participants=[], severity=0.9, time="12:00",
              narrative="A public figure is attacked in daylight."),
    ])
    out = compile_injection(_gw(tmp_path, t), block, people, "the DM is killed in daylight", default_day=0)
    assert out.injection.day == 1  # from _spec default

    block2, hhs, people2 = world[0], world[1], world[2]
    log = EventLog(tmp_path / "assassin.db")
    engine.run_simulation(log, SEED, block2, hhs, people2, days=3, injections=[out.injection])
    inj_ev = next(e for e in log.events(type="event.assassination"))
    heard = [e for e in log.events(type="info.heard")]
    witnessed = [e for e in heard if e.caused_by == inj_ev.seq]
    assert witnessed, "a daylight killing nobody noticed"
    assert any("assassination" in e.payload["claim"]["text"] for e in heard)
    # and it travelled beyond direct witnesses
    hop1 = [e for e in heard if e.payload["claim"]["key"].startswith("cl:assassination") and e.payload["claim"]["hop"] >= 1]
    assert hop1, "the news never left the eyewitnesses"
