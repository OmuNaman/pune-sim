"""T2 street talk: the day's one exchange that carried news between families.

A 30-day run contained zero conversations between two households — everyone
talked to their own family in morning scenes and the rest of the block moved
information in silence. This lane renders the transmission that already
happened; it never creates one.
"""

import orjson
import pytest

from punesim import engine
from punesim.config import Config
from punesim.kernel.log import EventLog
from punesim.llm import Cassette, Gateway
from punesim.minds import talk
from punesim.population import synthesize
from punesim.world.block import Block

pytestmark = pytest.mark.skipif(
    not __import__("pathlib").Path("data/anchors/kasba_places.geojson").exists(),
    reason="anchor data not fetched",
)

SEED = 33


@pytest.fixture(scope="module")
def world():
    block = Block.load()
    hhs, people = synthesize(SEED, block, n_households=25)
    return block, hhs, people


def _cfg(tmp_path):
    return Config(
        run_seed=SEED, llm_mode="record", scene_gate_mode="spotlight",
        openrouter_api_key="test", openrouter_base_url="x",
        model_workhorse="fake/flash", model_flash="fake/flash",
        model_premium="fake/premium", runs_dir=tmp_path,
    )


class Echo:
    """Always answers; records every prompt for inspection."""

    def __init__(self, reply=None):
        self.reply = reply or orjson.dumps(
            {"narration": "They meet by the shutters.",
             "transcript": "A: aikla ka?\nB: kaay zala?"}
        ).decode()
        self.prompts: list[str] = []

    def __call__(self, model, messages, temperature, max_tokens):
        self.prompts.append(messages[-1]["content"])
        return self.reply, {"total_tokens": 40}


def _rumor(block, people):
    market = next(p for p in block.places if p.kind in ("market", "shop") and p.name)
    seeds = [p.id for p in people.values() if p.age >= 25][:2]
    return engine.Injection(
        day=0, time_s=9 * 3600, type="info.rumor", place=market.id,
        participants=tuple(seeds),
        payload={"credence": 0.9, "claim": {
            "key": "cl:talk_test", "subject": market.id, "predicate": "contaminated",
            "topics": ["water"], "charge": 0.9, "specificity": 0.5, "veracity": "false",
        }},
    )


def test_the_days_exchange_crosses_a_household_line(tmp_path, world):
    block, hhs, people = world
    log = EventLog(tmp_path / "talk.db")
    t = Echo()
    gw = Gateway(_cfg(tmp_path), Cassette(tmp_path / "c.db"), transport=t, log=log)
    engine.run_simulation(
        log, SEED, block, hhs, people, days=3, gateway=gw, scenes_k=1,
        injections=[_rumor(block, people)],
    )
    talks = list(log.events(type="conversation.held"))
    assert talks, "three days of gossip and nobody said a word out loud"
    assert len(talks) <= 3, "the lane is one exchange a day, not a chorus"
    for e in talks:
        a, b = e.payload["participants"]
        assert people[a].household_id != people[b].household_id
        assert people[a].age >= 16 and people[b].age >= 16
        assert e.caused_by is not None, "an exchange with no lineage to the hop it renders"
        assert e.payload["transcript"]


def test_the_prompt_carries_both_identities_and_the_belief(tmp_path, world):
    """Nothing in this lane may be invented: both people arrive named and aged,
    and how much the listener believed it is given, not guessed."""
    block, hhs, people = world
    log = EventLog(tmp_path / "p.db")
    t = Echo()
    gw = Gateway(_cfg(tmp_path), Cassette(tmp_path / "c.db"), transport=t, log=log)
    engine.run_simulation(
        log, SEED, block, hhs, people, days=2, gateway=gw, scenes_k=0,
        injections=[_rumor(block, people)],
    )
    talks = list(log.events(type="conversation.held"))
    assert talks
    prompt = next(p for p in t.prompts if "WHAT WAS PASSED ON" in p)
    a, b = talks[0].payload["participants"]
    assert people[a].name in prompt and people[b].name in prompt
    assert f"({people[a].age}, {people[a].occupation})" in prompt
    assert "THE LISTENER ENDS UP BELIEVING IT:" in prompt


def test_no_talk_flag_leaves_the_world_unchanged(tmp_path, world):
    """The information moves either way — the camera is optional, and turning
    it off must not perturb a single mechanical draw."""
    block, hhs, people = world
    inj = _rumor(block, people)
    log_a = EventLog(tmp_path / "a.db")
    engine.run_simulation(log_a, SEED, block, hhs, people, days=3, injections=[inj])
    log_b = EventLog(tmp_path / "b.db")
    t = Echo()
    gw = Gateway(_cfg(tmp_path), Cassette(tmp_path / "c2.db"), transport=t, log=log_b)
    engine.run_simulation(
        log_b, SEED, block, hhs, people, days=3, gateway=gw, scenes_k=0,
        injections=[inj], talk=False,
    )
    assert not list(log_b.events(type="conversation.held"))
    assert t.prompts == []
    heard_a = [(e.sim_time, e.payload["person"], e.payload["claim"]["text"]) for e in log_a.events(type="info.heard")]
    heard_b = [(e.sim_time, e.payload["person"], e.payload["claim"]["text"]) for e in log_b.events(type="info.heard")]
    assert heard_a == heard_b


def test_the_choice_is_deterministic(tmp_path, world):
    block, hhs, people = world
    inj = _rumor(block, people)
    picks = []
    for name in ("d1.db", "d2.db"):
        log = EventLog(tmp_path / name)
        t = Echo()
        gw = Gateway(_cfg(tmp_path), Cassette(tmp_path / f"c-{name}"), transport=t, log=log)
        engine.run_simulation(
            log, SEED, block, hhs, people, days=3, gateway=gw, scenes_k=0, injections=[inj],
        )
        picks.append([
            (e.sim_time, tuple(e.payload["participants"])) for e in log.events(type="conversation.held")
        ])
    assert picks[0] == picks[1]


def test_a_failed_call_costs_a_conversation_not_a_day(tmp_path, world):
    block, hhs, people = world

    class Broken:
        def __init__(self):
            self.prompts: list[str] = []

        def __call__(self, *a, **k):
            raise RuntimeError("provider is down")

    log = EventLog(tmp_path / "broken.db")
    gw = Gateway(_cfg(tmp_path), Cassette(tmp_path / "c3.db"), transport=Broken(), log=log)
    n, _ = engine.run_simulation(
        log, SEED, block, hhs, people, days=2, gateway=gw, scenes_k=0,
        injections=[_rumor(block, people)],
    )
    assert n > 0 and not list(log.events(type="conversation.held"))
    skips = [e for e in log.events(type="scene.skipped") if e.payload.get("lane") == "talk"]
    assert skips, "a broken call should be loud, not silent"
    assert list(log.events(type="info.heard")), "the rumour still moved"


def test_pick_prefers_the_consequential_hop(world):
    """Charge x credence x freshness — the moment a story jumped to a family
    that did not have it is the interesting sentence in a day of gossip."""
    _, _, people = world
    a = next(p for p in people.values() if p.age >= 25)
    b = next(p for p in people.values() if p.age >= 25 and p.household_id != a.household_id)
    from punesim.minds.info import Claim, Heard

    dull = Heard(1000, b.id, Claim(key="k", subject="s", predicate="p", text="t", charge=0.2, hop=5),
                 a.id, "f2f", 0.3, 1)
    vivid = Heard(2000, b.id, Claim(key="k", subject="s", predicate="p", text="t", charge=0.9, hop=0),
                  a.id, "f2f", 0.9, 2)
    intervals = {b.id: [("place:x", 0, 9999)]}
    hh_of = {p.id: p.household_id for p in people.values()}
    ex = talk.pick_exchange([dull, vivid], people, hh_of, intervals)
    assert ex is not None and ex.heard is vivid
