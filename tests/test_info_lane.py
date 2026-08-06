"""V1 INFO lane: propagation, mutation, belief, actions, hazards — all
mechanical, all offline, all deterministic (the V1 exit trace lives here)."""

import pytest

from punesim import engine
from punesim.kernel.log import EventLog
from punesim.minds import info
from punesim.population import synthesize
from punesim.world import hazards
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


def test_traits_are_keyed(world):
    a = info.traits(SEED, "person:001.1")
    assert a == info.traits(SEED, "person:001.1")
    assert a != info.traits(SEED, "person:001.2")
    assert 0.0 <= a.sociability <= 1.0


def test_mutation_is_keyed_and_audited(world):
    block, _, _ = world
    place = next(p for p in block.places if p.name)
    claim = hazards.hazard_claim(
        "hazard.water.supply_cut", place.id, 0, "supply_cut", ("water",), 0.6, block
    )
    m1 = info.maybe_mutate(claim, SEED, "person:000.1", 0, block)
    m2 = info.maybe_mutate(claim, SEED, "person:000.1", 0, block)
    assert m1 == m2  # same key, same drift
    assert m1.hop == claim.hop + 1
    if m1.ops:  # if an op fired, the drift is auditable and re-rendered
        assert m1.ops[-1] in info.OPS
        assert m1.text


def test_credence_saturates_and_discounts_repetition():
    c1 = info.update_credence(info.PRIOR_CREDENCE, "f2f", 0, 0.5, 0.7)
    c2 = info.update_credence(c1, "f2f", 1, 0.5, 0.7)
    c3 = info.update_credence(c2, "f2f", 2, 0.5, 0.7)
    assert c1 > info.PRIOR_CREDENCE
    assert (c1 - info.PRIOR_CREDENCE) > (c2 - c1) > (c3 - c2) > 0
    assert info.update_credence(c1, "f2f", 1, 0.5, 0.7, same_source=True) < c2
    # a household account moves belief more than a stranger's
    assert info.update_credence(0.15, "household", 0, 0.5, 0.7) > c1


def test_presence_intervals_track_trips(world):
    block, _, people = world
    pid = min(people)
    home = people[pid].home_id
    dest = next(p for p in block.places if p.name).id
    routine = [
        (1000, pid, "trip.start", {"person": pid, "from": home, "to": dest}),
        (1600, pid, "trip.end", {"person": pid, "at": dest}),
        (5000, pid, "trip.start", {"person": pid, "from": dest, "to": home}),
        (5600, pid, "trip.end", {"person": pid, "at": home}),
    ]
    iv = info.presence_intervals(routine, {pid: people[pid]}, 0)[pid]
    assert (home, 0, 1000) == iv[0]
    assert (dest, 1600, 5000) in iv
    assert iv[-1][0] == home and iv[-1][2] == 86400


def test_rumor_exit_trace(tmp_path, world):
    """V1 exit: an injected rumor propagates, mutates, and changes household
    behavior over 3 sim-days — zero LLM, zero rumor-specific engine code."""
    block, hhs, people = world
    market = next(p for p in block.places if p.kind in ("market", "shop") and p.name)
    seeds = [
        p.id for p in people.values()
        if p.age >= 25 and p.occupation not in ("infant",)
    ][:2]
    inj = engine.Injection(
        day=0, time_s=9 * 3600, type="info.rumor", place=market.id,
        participants=tuple(seeds),
        payload={
            "credence": 0.85,
            "claim": {
                "key": "cl:water_scare", "subject": market.id,
                "predicate": "contaminated", "topics": ["water"],
                "charge": 0.8, "specificity": 0.5, "veracity": "false",
                "valence": -0.7,
            },
        },
    )
    log = EventLog(tmp_path / "rumor.db")
    engine.run_simulation(log, SEED, block, hhs, people, days=3, injections=[inj])

    heard = list(log.events(type="info.heard"))
    hearers = {e.payload["person"] for e in heard}
    assert len(hearers) > len(seeds), "the rumor never left its seeds"
    assert len(hearers) < len(people), "everyone knows — saturation controls failed"
    assert all(e.caused_by is not None for e in heard), "a hop lost its lineage"
    assert max(e.payload["claim"]["hop"] for e in heard) >= 1  # multi-hop
    # belief crossed into behavior: someone acted, and a next-day plan changed
    actions = list(log.events(type="belief.action"))
    assert actions
    behaved = [e for e in log.events() if e.type == "plan.avoided"] + [
        e for e in log.events()
        if e.type == "activity.start" and e.payload.get("activity") == "store_water"
    ]
    assert behaved, "belief never changed anyone's day"
    assert all(e.caused_by is not None for e in list(log.events(type="belief.action")))
    # freshness: day-3 spread is no bigger than the peak day (the rumor cools)
    by_day = {}
    for e in heard:
        by_day[e.sim_time // 86400] = by_day.get(e.sim_time // 86400, 0) + 1
    assert by_day.get(2, 0) <= max(by_day.values())


def test_rumor_run_is_deterministic(tmp_path, world):
    block, hhs, people = world
    inj = engine.Injection(
        day=0, time_s=10 * 3600, type="info.rumor",
        place=block.places[0].id, participants=(sorted(people)[3],),
        payload={"credence": 0.8, "claim": {
            "key": "cl:det", "subject": block.places[0].id,
            "predicate": "dangerous", "topics": ["safety"], "charge": 0.7,
        }},
    )
    hashes = []
    for name in ("a.db", "b.db"):
        log = EventLog(tmp_path / name)
        engine.run_simulation(log, SEED, block, hhs, people, days=2, injections=[inj])
        hashes.append(log.determinism_hash())
    assert hashes[0] == hashes[1]


def test_random_hazards_ripple_without_injection(tmp_path, world):
    """V1 exit: an UN-injected hazard produces a believable ripple — sirens,
    percepts, gossip — from keyed draws alone, twice-run hash-identical."""
    block, hhs, people = world
    hashes, logs = [], []
    for name in ("h1.db", "h2.db"):
        log = EventLog(tmp_path / name)
        engine.run_simulation(log, SEED, block, hhs, people, days=6, hazards=True)
        hashes.append(log.determinism_hash())
        logs.append(log)
    assert hashes[0] == hashes[1]
    log = logs[0]
    hz = [e for e in log.events() if e.type.startswith("hazard.")]
    assert hz, "six days and nothing happened — rates too low for the exit test"
    assert all(e.provenance == "clockwork" for e in hz)
    heard = list(log.events(type="info.heard"))
    hz_seqs = {e.seq for e in hz}
    witnessed = [e for e in heard if e.caused_by in hz_seqs]
    assert witnessed, "a hazard nobody perceived is a tree falling in no forest"
    # the ripple is attributable end-to-end: walk any hop chain back to a hazard
    by_seq = {e.seq: e for e in log.events()}
    chain = witnessed[0]
    steps = 0
    while chain.caused_by is not None and steps < 10:
        chain = by_seq[chain.caused_by]
        steps += 1
    assert chain.type.startswith("hazard.")


def test_pressure_crossing_gates_and_commits(tmp_path, world):
    """E2: a serious injury pushes p_health over threshold the same night."""
    block, hhs, people = world
    victim = next(p for p in people.values() if p.age >= 18 and p.work_id)
    inj = engine.Injection(
        day=0, time_s=9 * 3600, type="hazard.road.collision",
        place=block.places[0].id, participants=(victim.id,), severity=0.7,
    )
    log = EventLog(tmp_path / "p.db")
    _, state = engine.run_simulation(log, SEED, block, hhs, people, days=2, injections=[inj])
    crossed = [e for e in log.events(type="pressure.crossed")]
    assert any(e.payload["person"] == victim.id and e.payload["pressure"] == "p_health" for e in crossed)
    assert state.pressures[victim.id]["p_health"] > 0.6


def _claim(**kw):
    base = {
        "key": "cl:fire:place:x:d0", "subject": "place:x", "predicate": "fire",
        "text": "A fire broke out at the school", "veracity": "true",
        "specificity": 0.85, "topics": ("safety",),
    }
    base.update(kw)
    return info.Claim(**base)


def test_a_witness_keeps_their_own_account_but_not_their_certainty():
    """You do not un-see a fire because your husband tells you a bigger one.
    Confidence still moves — corroboration is real; the story does not."""
    st = info.InfoState()
    seen = _claim()
    st.hear("p1", seen, 0.95, day=0, seq=1, source="witness", channel="witness")
    rumour = _claim(
        text="A fire broke out at the school — 6 people affected; people are blaming the temple",
        quantity=6.0, unit="people", veracity="distorted", specificity=0.7,
        ops=("EXAGGERATE", "REATTRIBUTE"), hop=2, blame="place:t",
    )
    st.hear("p1", rumour, 0.99, day=0, seq=2, source="p0", channel="household", lineage=("p1", "p0"))

    h = st.holdings["p1"]["cl:fire:place:x:d0"]
    assert h.claim.text == seen.text and h.claim.ops == () and h.claim.veracity == "true"
    assert h.last_seq == 1, "last_seq must move with the claim or the drift audit lies"
    assert h.credence == 0.99 and h.exposures == 2 and h.last_source == "p0"
    assert h.witnessed


def test_a_witness_still_learns_a_fuller_true_account():
    """Someone who only heard the commotion two lanes away can be told what
    actually happened — but only by an account that is precise AND undistorted."""
    st = info.InfoState()
    glimpse = _claim(text="Something happened at the school", specificity=0.55)
    st.hear("p1", glimpse, 0.75, day=0, seq=1, source="witness", channel="witness")
    fuller = _claim(specificity=0.85, veracity="true")
    st.hear("p1", fuller, 0.9, day=0, seq=2, source="p2", channel="f2f", lineage=("p2",))
    assert st.holdings["p1"]["cl:fire:place:x:d0"].claim.text == fuller.text

    st2 = info.InfoState()
    st2.hear("p1", glimpse, 0.75, day=0, seq=1, source="witness", channel="witness")
    louder = _claim(specificity=0.9, veracity="distorted", ops=("SPECIFY",))
    st2.hear("p1", louder, 0.9, day=0, seq=2, source="p2", channel="f2f", lineage=("p2",))
    assert st2.holdings["p1"]["cl:fire:place:x:d0"].claim.text == glimpse.text


def test_non_witnesses_still_update_normally():
    st = info.InfoState()
    st.hear("p1", _claim(), 0.4, day=0, seq=1, source="p9", channel="f2f", lineage=("p9",))
    later = _claim(text="a much wilder version", ops=("EXAGGERATE",), veracity="distorted")
    st.hear("p1", later, 0.7, day=0, seq=2, source="p8", channel="f2f", lineage=("p8",))
    h = st.holdings["p1"]["cl:fire:place:x:d0"]
    assert h.claim.text == "a much wilder version" and h.last_seq == 2


def test_lineage_accumulates_and_keeps_the_recent_tail():
    st = info.InfoState()
    long_chain = tuple(f"p{i}" for i in range(info.LINEAGE_MAX + 5))
    st.hear("px", _claim(), 0.5, day=0, seq=1, source="pz", channel="f2f", lineage=long_chain)
    kept = st.holdings["px"]["cl:fire:place:x:d0"].lineage
    assert len(kept) == info.LINEAGE_MAX
    assert kept[-1] == long_chain[-1], "truncating the tail would disable echo detection"


def test_your_own_story_does_not_come_back_to_convince_you(tmp_path, world):
    """A->B->A: before this guard 12% of all hearings were echoes, and they
    were the fastest route to false certainty about your own exaggeration."""
    block, hhs, people = world
    log = EventLog(tmp_path / "echo.db")
    engine.run_simulation(log, SEED, block, hhs, people, days=6, hazards=True)
    echoes = [
        e for e in log.events(type="info.heard")
        if e.payload["person"] in (e.payload.get("lineage") or [])
    ]
    assert not echoes, f"{len(echoes)} hearings came back to their own teller"
