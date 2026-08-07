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
    _n, state = engine.run_simulation(
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
    # mechanical plan invalidation: the admitted student's ROUTINE day ends at
    # the hospital (INFO-lane events — hearing gossip there — are allowed)
    adm_t = next(e.sim_time for e in log.events(type="hospital.admitted"))
    student_after = [
        e for e in log.events()
        if e.payload.get("person") == student.id and e.sim_time > adm_t
        and e.type in ("trip.start", "trip.end", "activity.start")
    ]
    assert all(e.type == "activity.start" and e.payload.get("activity") == "admitted"
               for e in student_after)
    assert any(e.payload.get("activity") == "admitted"
               for e in log.events() if e.payload.get("person") == student.id)
    # consequence-cone lineage: every stub reaction points at the injection
    inj_seq = next(e.seq for e in log.events(type="hazard.road.collision"))
    for t in ("ambulance.dispatched", "hospital.admitted"):
        assert all(e.caused_by == inj_seq for e in log.events(type=t))


def test_reaction_scene_same_day_and_next_morning(tmp_path, world):
    block, hhs, people = world
    student = _find_student(people, hhs)
    mother = next(
        p for p in people.values()
        if p.household_id == student.household_id and p.age >= 18
    )
    hospital = block.nearest(block.places[0].id, "hospital", "clinic")
    inj = engine.Injection(
        day=0, time_s=8 * 3600, type="hazard.road.collision",
        place=block.places[0].id, participants=(student.id,), severity=0.6,
    )
    t_react = 8 * 3600 + engine.REACTION_DELAY_S

    # call order: d0 morning hh:000, hh:001 -> d0 REACTION (student hh) -> d1 morning student hh, hh:000
    reaction = {
        "day_plan": [
            {
                "person_id": mother.id,
                "steps": [{"t": 9 * 3600, "place_ref": hospital.id, "activity": "at_hospital"}],
            }
        ]
    }
    rest_plan = {
        "day_plan": [
            {
                "person_id": student.id,
                "steps": [{"t": 8 * 3600, "place_ref": student.home_id, "activity": "rest_at_home"}],
            }
        ]
    }
    transport = ScriptedTransport([_delta(), _delta(), _delta(**reaction), _delta(**rest_plan), _delta()])
    log = EventLog(tmp_path / "sc.db")
    gw = Gateway(_cfg(tmp_path), Cassette(tmp_path / "c.db"), transport=transport, log=log)
    engine.run_simulation(
        log, SEED, block, hhs, people, days=2, gateway=gw, scenes_k=2, injections=[inj]
    )

    # T2 reaction scene fired SAME DAY at t_react, for the right household
    reactions = [e for e in log.events(type="scene.reaction")]
    assert len(reactions) == 1
    assert reactions[0].sim_time == t_react
    assert reactions[0].payload["household"] == student.household_id
    # its context contained the day's events (the school call / admission)
    assert any("accident" in p or "admitted" in p for p in [transport.prompts[2]])
    # the mother's rest-of-day was rewritten: she goes to the hospital day 0
    day0_mother = [
        e for e in log.events()
        if e.sim_time < 86400 and e.payload.get("person") == mother.id
    ]
    assert any(
        e.type == "trip.end" and e.payload.get("at") == hospital.id for e in day0_mother
    )
    # the admitted student's routine day was cancelled mechanically
    day0_student_after_adm = [
        e for e in log.events()
        if e.sim_time < 86400 and e.payload.get("person") == student.id
        and e.sim_time > 8 * 3600 + 25 * 60
        and e.type in ("trip.start", "trip.end", "activity.start")
    ]
    assert all(e.payload.get("activity") == "admitted" for e in day0_student_after_adm)

    # next morning the spotlight followed the family and the boy stays home
    day1 = [e for e in log.events() if e.sim_time >= 86400]
    scene_hhs = [e.payload["household"] for e in day1 if e.type == "scene.morning"]
    assert scene_hhs[0] == student.household_id
    student_day1 = [e for e in day1 if e.payload.get("person") == student.id]
    assert not any(
        e.type == "trip.start" and e.payload.get("to") == student.work_id for e in student_day1
    )
    assert any(
        e.type == "activity.start" and e.payload.get("activity") == "rest_at_home"
        for e in student_day1
    )
    # all 5 llm responses were committed as input-events (law 1)
    assert sum(1 for e in log.events(type="llm.response")) == 5


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


def test_a_scene_is_never_shown_its_own_previous_output(tmp_path, world):
    """The soak's central defect: 64% of every RECENT EVENTS block was the
    household's own prior LLM output, and the model copied it forward — a
    Sunday memory got re-formed word for word on Monday."""
    block, hhs, people = world
    hh = hhs[0]
    t = ScriptedTransport([
        _delta(
            narration="Tuesday narration that must not reappear.",
            memory_writes=[{"person_id": hh.member_ids[0], "salience": 0.9,
                            "summary": "A very memorable Tuesday thing."}],
            mood_deltas=[{"person_id": hh.member_ids[0], "dim": "mood", "delta": -0.2}],
            messages=[{"sender": hh.member_ids[0], "recipients": [hh.member_ids[-1]],
                       "channel": "talk", "text": "An utterance from our own scene."}],
        ),
        _delta(),
    ])
    log = EventLog(tmp_path / "echo.db")
    gw = Gateway(_cfg(tmp_path), Cassette(tmp_path / "c.db"), transport=t, log=log)
    engine.run_simulation(
        log, SEED, block, hhs, people, days=2, gateway=gw, scenes_k=len(hhs), scene_gate_mode="all"
    )
    day2 = t.prompts[len(hhs)]  # first prompt of day 2 is hh:000 again under "all"
    assert "Tuesday narration that must not reappear" not in day2
    assert "An utterance from our own scene" not in day2
    assert "memory.formed" not in day2 and "mood.delta" not in day2
    assert "scene.morning" not in day2 and "plan.revised" not in day2
    # ...but it IS carried deliberately, as dated background
    assert "A very memorable Tuesday thing." in day2
    assert "EARLIER MORNINGS" in day2


def test_no_prompt_line_ever_dumps_a_raw_payload(tmp_path, world):
    """The raw-dict fallback was the leak's delivery mechanism; an unknown type
    must now render nothing rather than a Python dict."""
    import re

    from punesim.minds.scene import _humanize

    block, hhs, people = world
    assert _humanize("some.future.type", {"person": "person:000.0"}, block, people) == ""
    log = EventLog(tmp_path / "raw.db")
    engine.run_simulation(log, SEED, block, hhs, people, days=4, hazards=True)
    for hh in hhs[:6]:
        lines = recent_notable_events(
            log, set(hh.member_ids), 3, block, household_id=hh.id, people=people
        )
        assert not [ln for ln in lines if re.search(r": \{.*\}$", ln)], lines


def test_every_id_in_a_prompt_arrives_with_a_name(tmp_path, world):
    """A bare person id is an invitation to invent one: the soak's model met
    `person:022.4` and produced "Shobha tai", an adult colleague, for a
    six-year-old pupil — then kept her for four days."""
    import re

    block, hhs, people = world
    log = EventLog(tmp_path / "ids.db")
    engine.run_simulation(log, SEED, block, hhs, people, days=5, hazards=True)
    seen = 0
    for hh in hhs:
        for line in recent_notable_events(
            log, set(hh.member_ids), 4, block, household_id=hh.id, people=people
        ):
            for m in re.finditer(r"person:\d+\.\d+", line):
                seen += 1
                assert line[m.start() - 1] == "[", f"bare id in: {line}"
    assert seen, "no person ids appeared at all — the assertion proved nothing"


def test_events_carry_the_date_and_how_long_ago():
    from punesim.minds.scene import _when

    assert _when(86400 * 1 + 14 * 3600 + 5 * 60, 6).endswith("14:05 (5 days ago)")
    assert "(yesterday)" in _when(86400 * 5, 6)
    assert "(today)" in _when(86400 * 6 + 3600, 6)


def test_a_witnessed_event_keeps_its_hour_forever(tmp_path, world):
    """Contradiction 1 of the soak: a fire four family members watched at 14:05
    was narrated days later as having "broken out at night"."""
    from punesim.minds.scene import witnessed_facts

    block, hhs, people = world
    counts: dict[str, int] = {}
    for p in people.values():
        if p.work_id:
            counts[p.work_id] = counts.get(p.work_id, 0) + 1
    busiest = max(counts, key=lambda k: (counts[k], k))
    inj = engine.Injection(day=0, time_s=14 * 3600 + 5 * 60, type="hazard.fire.small",
                           place=busiest, severity=0.5)
    log = EventLog(tmp_path / "w.db")
    engine.run_simulation(log, SEED, block, hhs, people, days=6, injections=[inj])
    watched = {
        e.payload["person"] for e in log.events(type="info.heard")
        if e.payload.get("channel") == "witness"
    }
    assert watched, "nobody saw the fire — nothing to anchor"
    hh = next(h for h in hhs if set(h.member_ids) & watched)
    facts = witnessed_facts(log, set(hh.member_ids), 5, block, people=people)
    assert facts, "a witnessed fire vanished from the context after two days"
    assert any("14:" in f and "days ago" in f for f in facts)


def test_a_scene_cannot_invent_a_person(tmp_path, world):
    """The registry is canon and a scene does not get to extend it. The soak
    quietly accumulated messages addressed to person:colleague_yogita,
    person:Vinayak Mane and person:neighbor — people who do not exist."""
    block, hhs, people = world
    hh = hhs[0]
    real = hh.member_ids[0]
    t = ScriptedTransport([
        _delta(
            memory_writes=[
                {"person_id": real, "salience": 0.5, "summary": "a real memory"},
                {"person_id": "person:ghost_auntie", "salience": 0.9, "summary": "invented"},
            ],
            messages=[
                {"sender": real, "recipients": ["person:neighbor"],
                 "channel": "phone", "text": "to nobody"},
                {"sender": real, "recipients": [hh.member_ids[-1]],
                 "channel": "talk", "text": "to someone real"},
            ],
        ),
    ])
    log = EventLog(tmp_path / "ghost.db")
    gw = Gateway(_cfg(tmp_path), Cassette(tmp_path / "c.db"), transport=t, log=log)
    engine.run_simulation(
        log, SEED, block, hhs, people, days=1, gateway=gw, scenes_k=1,
    )
    mems = [e.payload["person"] for e in log.events(type="memory.formed")]
    assert real in mems and "person:ghost_auntie" not in mems
    msgs = [e.payload for e in log.events(type="message.sent")]
    assert len(msgs) == 1 and msgs[0]["text"] == "to someone real"
    rejected = list(log.events(type="scene.invalid_ref"))
    assert rejected, "invented ids were dropped silently"
    assert set(rejected[0].payload["ids"]) == {"person:ghost_auntie", "person:neighbor"}


def test_a_memory_pins_the_day_it_means(tmp_path, world):
    """Relative time is true for one day and wrong forever after. The 30-day
    re-soak caught a Thursday-night power cut still being "kal raatri" in
    scenes on Friday, Saturday, Sunday and Monday, because each morning
    re-read a memory that said so."""
    from punesim.minds.scene import absolutize

    t = 15 * 86400 + 6 * 3600  # Fri 16 Jan 2026, 06:00
    out = absolutize("Lost his eraser doing homework by candlelight last night.", t)
    assert "last night" not in out.lower() and "Thu 15 Jan" in out
    assert "kal raatri" not in absolutize("Kal raatri light gela hota.", t).lower()
    assert absolutize("", t) == ""

    block, hhs, people = world
    hh = hhs[0]
    tr = ScriptedTransport([_delta(memory_writes=[
        {"person_id": hh.member_ids[0], "salience": 0.8,
         "summary": "The power went out last night and stayed out."},
    ])])
    log = EventLog(tmp_path / "abs.db")
    gw = Gateway(_cfg(tmp_path), Cassette(tmp_path / "c.db"), transport=tr, log=log)
    engine.run_simulation(log, SEED, block, hhs, people, days=1, gateway=gw, scenes_k=1)
    summaries = [e.payload["summary"] for e in log.events(type="memory.formed")]
    assert summaries and all("last night" not in s.lower() for s in summaries)


def test_a_mass_event_costs_more_scenes_but_not_unbounded_scenes(tmp_path, world):
    """One power cut gate-marked 78 of 80 households, and the third soak spent
    67 scenes on a single day — thirteen normal days — because nothing capped
    the routine-bypass gate. A mass event should mean MORE scenes, not all of
    them, and the households dropped should be named rather than vanish."""
    block, hhs, people = world
    spot = max(
        (p for p in block.places if p.name),
        key=lambda p: sum(
            1 for q in people.values()
            if engine.hazards_mod.haversine_m(
                p.lat, p.lon, block[q.home_id].lat, block[q.home_id].lon
            ) <= engine.hazards_mod.AREA_M
        ),
    )
    inj = engine.Injection(day=0, time_s=18 * 3600, type="info.rumor", place=spot.id,
                           participants=tuple(q.id for q in people.values() if q.age >= 16),
                           payload={"credence": 0.95, "claim": {
                               "key": "cl:mass", "subject": spot.id,
                               "predicate": "contaminated", "topics": ["water"],
                               "charge": 0.9, "specificity": 0.6, "veracity": "false"}})
    t = ScriptedTransport([_delta()] * 200)
    log = EventLog(tmp_path / "cap.db")
    gw = Gateway(_cfg(tmp_path), Cassette(tmp_path / "c.db"), transport=t, log=log)
    k = 3
    engine.run_simulation(
        log, SEED, block, hhs, people, days=3, gateway=gw, scenes_k=k, injections=[inj],
    )
    per_day: dict[int, int] = {}
    for e in log.events(type="scene.morning"):
        per_day[e.sim_time // 86400] = per_day.get(e.sim_time // 86400, 0) + 1
    assert per_day, "no scenes rendered at all"
    worst = max(per_day.values())
    assert worst <= k * engine.GATE_BURST, f"a single day rendered {worst} scenes with k={k}"
    assert worst > k, "the mass event bought no extra scenes at all"
    capped = list(log.events(type="scene.gate_capped"))
    if capped:
        p = capped[0].payload
        assert p["marked"] > p["rendered"] and p["dropped"], "truncation went unrecorded"
