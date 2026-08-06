"""Scene lane, injections, and replay — all offline via scripted transport."""

import orjson
import pytest

from punesim import engine
from punesim.config import Config
from punesim.kernel.log import EventLog
from punesim.llm import Cassette, Gateway
from punesim.minds.scene import recent_notable_events
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


def _cfg(tmp_path, mode="record"):
    return Config(
        run_seed=SEED,
        llm_mode=mode,
        scene_gate_mode="spotlight",
        openrouter_api_key="test",
        openrouter_base_url="x",
        model_workhorse="fake/flash",
        model_flash="fake/flash",
        model_premium="fake/premium",
        runs_dir=tmp_path,
    )


class ScriptedTransport:
    """Returns queued responses in call order; records prompts for inspection."""

    def __init__(self, replies: list[str]):
        self.replies = list(replies)
        self.prompts: list[str] = []

    def __call__(self, model, messages, temperature, max_tokens):
        self.prompts.append(messages[-1]["content"])
        return self.replies.pop(0), {"total_tokens": 50}


def _delta(**kw) -> str:
    return orjson.dumps(
        {"narration": "An ordinary morning.", "transcript": "A: chala.\nB: ho.", **kw}
    ).decode()


def _find_student(people, hhs):
    """A student from a household outside the default top-k tie-break picks."""
    for p in people.values():
        if p.occupation == "student" and p.work_id and p.household_id not in ("hh:000", "hh:001"):
            return p
    pytest.fail("no suitable student")


def test_injection_stub_reactions_and_attention(tmp_path, world):
    block, hhs, people = world
    student = _find_student(people, hhs)
    inj = engine.Injection(
        day=0, time_s=8 * 3600 + 10 * 60, type="hazard.road.collision",
        place=block.places[0].id, participants=(student.id,), severity=0.7,
    )
    log = EventLog(tmp_path / "inj.db")
    n, state = engine.run_simulation(
        log, SEED, block, hhs, people, days=1, injections=[inj]
    )
    types = [e.type for e in log.events() if e.provenance == "user"]
    assert "hazard.road.collision" in types
    assert "ambulance.dispatched" in types
    assert "hospital.admitted" in types
    assert "message.sent" in types  # the school called home
    # attention bumped the household for tomorrow's spotlight
    top = state.attention.top_k([h.id for h in hhs], 1, tick=288)
    assert top == [student.household_id]
    # the family's scene context would mention the hospital
    hh = next(h for h in hhs if h.id == student.household_id)
    recent = recent_notable_events(log, set(hh.member_ids), 1, block)
    assert any("admitted" in line for line in recent)


def test_scene_day_plan_override_changes_the_day(tmp_path, world):
    block, hhs, people = world
    student = _find_student(people, hhs)
    inj = engine.Injection(
        day=0, time_s=8 * 3600, type="hazard.road.collision",
        place=block.places[0].id, participants=(student.id,), severity=0.6,
    )
    # day 0: hh:000, hh:001 (zero attention, id tie-break); day 1: student's hh first, hh:000 second
    rest_plan = {
        "day_plan": [
            {
                "person_id": student.id,
                "steps": [{"t": 8 * 3600, "place_ref": student.home_id, "activity": "rest_at_home"}],
            }
        ]
    }
    transport = ScriptedTransport([_delta(), _delta(), _delta(**rest_plan), _delta()])
    log = EventLog(tmp_path / "sc.db")
    gw = Gateway(_cfg(tmp_path), Cassette(tmp_path / "c.db"), transport=transport, log=log)
    engine.run_simulation(
        log, SEED, block, hhs, people, days=2, gateway=gw, scenes_k=2, injections=[inj]
    )

    day1 = [e for e in log.events() if e.sim_time >= 86400]
    scene_hhs = [e.payload["household"] for e in day1 if e.type == "scene.morning"]
    assert scene_hhs[0] == student.household_id  # spotlight followed the injection
    # the injured student stayed home: no school trip, rest activity present
    student_day1 = [e for e in day1 if e.payload.get("person") == student.id]
    assert not any(
        e.type == "trip.start" and e.payload.get("to") == student.work_id for e in student_day1
    )
    assert any(
        e.type == "activity.start" and e.payload.get("activity") == "rest_at_home"
        for e in student_day1
    )
    # scene context for the family mentioned the accident
    assert any("accident" in p or "admitted" in p for p in transport.prompts[2:])
    # llm responses were committed as input-events (law 1)
    assert sum(1 for e in log.events(type="llm.response")) == 4


def test_record_then_replay_is_hash_identical(tmp_path, world):
    block, hhs, people = world
    replies = [_delta(), _delta(), _delta(), _delta()]
    cassette_path = tmp_path / "cass.db"

    log1 = EventLog(tmp_path / "r1.db")
    gw1 = Gateway(_cfg(tmp_path, "record"), Cassette(cassette_path), transport=ScriptedTransport(replies), log=log1)
    engine.run_simulation(log1, SEED, block, hhs, people, days=2, gateway=gw1, scenes_k=2)

    class Bomb:
        def __call__(self, *a, **k):
            raise AssertionError("replay must not touch the network")

    log2 = EventLog(tmp_path / "r2.db")
    gw2 = Gateway(_cfg(tmp_path, "replay"), Cassette(cassette_path), transport=Bomb(), log=log2)
    engine.run_simulation(log2, SEED, block, hhs, people, days=2, gateway=gw2, scenes_k=2)

    assert log1.determinism_hash() == log2.determinism_hash()
