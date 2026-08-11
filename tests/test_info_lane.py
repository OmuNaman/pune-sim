"""V1 INFO lane: propagation, mutation, belief, actions, hazards — all
mechanical, all offline, all deterministic (the V1 exit trace lives here)."""

import pytest

from punesim import engine
from punesim.kernel.log import EventLog
from punesim.kernel.rng import keyed_rng
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


def test_a_hazard_always_lands_where_someone_can_perceive_it(tmp_path, world):
    """The soak's day-9 water cut hit a school whose catchment held one home
    with nobody in it: zero percepts, zero conversation, a non-event that still
    consumed a hazard draw."""
    block, hhs, people = world
    log = EventLog(tmp_path / "haz.db")
    engine.run_simulation(log, SEED, block, hhs, people, days=30, hazards=True)
    hazards = [e for e in log.events() if e.type.startswith("hazard.")]
    assert hazards, "30 days and not one hazard — nothing was tested"
    for h in hazards:
        percepts = [e for e in log.events(type="info.heard") if e.caused_by == h.seq]
        assert percepts, f"{h.type} at day {h.sim_time // 86400} was perceived by nobody"


def test_only_a_casualty_gets_an_ambulance(tmp_path, world):
    """Reactions keyed on the presence of a place, not the kind of trouble —
    so the soak dispatched an ambulance to a water cut and to a power cut."""
    block, hhs, people = world
    log = EventLog(tmp_path / "amb.db")
    engine.run_simulation(log, SEED, block, hhs, people, days=30, hazards=True)
    by_seq = {e.seq: e for e in log.events()}
    for amb in log.events(type="ambulance.dispatched"):
        cause = by_seq.get(amb.caused_by)
        assert cause is not None and cause.type.startswith(engine.CASUALTY_PREFIXES), (
            f"ambulance sent for {cause.type if cause else '?'}"
        )
    utility = [
        e for e in log.events()
        if e.type in ("complaint.registered", "utility.tanker_arrived", "utility.restored")
    ]
    outages = [e for e in log.events() if e.type in
               ("hazard.water.supply_cut", "hazard.power.outage")]
    if outages:
        assert utility, "a utility failed and no institution anywhere noticed"
        for e in utility:
            assert e.caused_by is not None


def test_the_lights_coming_back_is_news(tmp_path, world):
    """soak3's only continuity failure: the power was restored at 23:54 and
    nobody was ever told, because utility.restored carries {org, place,
    utility} and every lane that carries news is person-scoped. The scenes
    reasonably concluded the blackout was still running — two days by day 16,
    a week by day 20."""
    block, hhs, people = world
    spot = max(
        (p for p in block.places if p.name),
        key=lambda p: sum(1 for q in people.values() if q.home_id and
                          hazards.haversine_m(p.lat, p.lon, block[q.home_id].lat,
                                              block[q.home_id].lon) <= hazards.AREA_M),
    )
    inj = engine.Injection(day=0, time_s=20 * 3600, type="hazard.power.outage",
                           place=spot.id, severity=0.4)
    log = EventLog(tmp_path / "restore.db")
    _, state = engine.run_simulation(log, SEED, block, hhs, people, days=2, injections=[inj])

    restored = list(log.events(type="utility.restored"))
    assert restored, "the power never came back at all"
    told = [
        e for e in log.events(type="info.heard")
        if e.payload["claim"]["predicate"] == "restored"
    ]
    assert told, "the world fixed the power and told nobody"
    seen_it = [e for e in told if e.payload["channel"] == "witness"]
    assert seen_it and all(e.caused_by == restored[0].seq for e in seen_it)
    # good news travels too — relayed hearings point at the hop before them
    assert all(e.caused_by is not None for e in told)

    # ...and the trouble it resolves stops being live for the people who saw it
    outage_key = next(
        e.payload["claim_key"] for e in log.events(type="info.heard")
        if e.payload["claim"]["predicate"] == "outage"
    )
    knew = {e.payload["person"] for e in told}
    still_live = [
        pid for pid in knew
        if (h := state.info.holdings.get(pid, {}).get(outage_key)) and not h.stifled
    ]
    assert not still_live, f"{len(still_live)} people saw the lights come back and kept spreading the outage"


def test_every_action_says_whether_it_means_staying_away():
    """The recurring bug in this lane is an action defaulting to avoidance.

    First it was `outage`: a power cut had no action mapped, fell through to a
    default of "avoid the place", and 255 of 306 people stopped going somewhere
    because the lights had been off. That default is gone — an unmapped topic
    now changes nobody's route — and test_an_unmapped_topic_changes_nobody's_day
    below keeps it gone. Then it was `store_water`, which *is* mapped — but the
    engine recorded an avoidance for every belief action regardless of which one
    it was, so 1,138 people who filled a drum were also marked as shunning the
    pumping station.

    Both are the same mistake at different layers: something that is not
    avoidance being treated as avoidance because nothing forced the question.
    So every action this lane can emit must be classified, one way or the other.
    """
    emitted = {action for action, _threshold in info.ACTION_THRESHOLDS.values()}
    non_avoiding = {"store_water"}
    unclassified = emitted - info.AVOIDING_ACTIONS - non_avoiding
    assert not unclassified, (
        f"{sorted(unclassified)} can be emitted by the belief lane but nothing says "
        "whether it means the believer stops going to the place. Add it to "
        "info.AVOIDING_ACTIONS or give it a handler in engine/info_pass.py — do not "
        "let it default."
    )
    assert "store_water" not in info.AVOIDING_ACTIONS, (
        "filling a drum because the supply was cut is not a reason to stay away "
        "from where it was cut"
    )


def test_an_invented_detail_comes_from_the_same_lanes(world):
    """`_op_specify`'s variable was called `near`, its docstring promised
    "nearby", and it drew uniformly from every named place in the block — 437
    of them on oldcity, with no distance term anywhere. That is how a rumour
    about the water at Tulshibaug Mandir acquired "people are blaming
    Blackberrys", a menswear shop most of the old city away."""
    block, _, _ = world
    subject = next(p for p in block.places if p.name and block.nearby(p.id, info.NEARBY_WALK_S))
    claim = _claim(subject=subject.id, specificity=0.2, blame=None)
    picks = set()
    for i in range(60):
        rng = keyed_rng(SEED, "info", f"person:spec.{i}", 0, "mutate")
        out = info._op_specify(claim, rng, block)
        assert out.blame, "SPECIFY invented no detail at all"
        picks.add(out.blame)
        assert block.walk_seconds(subject.id, out.blame) <= info.NEARBY_WALK_S, (
            f"{block[out.blame].name} is "
            f"{block.walk_seconds(subject.id, out.blame) // 60} minutes' walk from "
            f"{subject.name} — that is not a detail anyone standing there reaches for"
        )
    named = [p for p in block.places if p.name]
    assert len(picks) < len(named), (
        "every named place in the block is still reachable as a 'nearby' detail"
    )
    assert any(block.walk_seconds(subject.id, p.id) > info.NEARBY_WALK_S for p in named), (
        "this block is too small for the test to mean anything — everything is nearby"
    )


def test_blame_lands_on_somebody_who_could_be_responsible(world):
    """`_op_reattribute` used to pick uniformly from 205 'prominent' places
    with nothing connecting the choice to the claim, so a water-contamination
    rumour blamed a bank. Who gets blamed now has to have something to do with
    what is being alleged — and for a utility that is an organisation, because
    nobody blames a pumping station for a dry tap."""
    block, _, _ = world
    subject = block.of_kind("temple", "shop", "market")[0]

    def blamed(**kw):
        claim = _claim(subject=subject.id, blame=None, **kw)
        rng = keyed_rng(SEED, "info", "person:blame", 0, "mutate")
        return info._op_reattribute(claim, rng, block)

    water = blamed(predicate="contaminated", topics=("water", "health"))
    assert water.blame == "org:pmc_water"
    assert "municipal water" in info.render_text(water, block), (
        "an org id leaked into a rumour's words instead of a name for it"
    )
    crime = blamed(predicate="dangerous", topics=("crime",))
    assert block[crime.blame].kind == "police"

    # ...and a topic nothing maps names nobody, exactly as an unmapped topic
    # changes nobody's route: the sim does not invent a villain it was never
    # told about.
    assert blamed(topics=("astrology",)).blame is None
    assert set(info.BLAMED_ORG.values()) <= set(info.ORG_NAMES), (
        "a claim can be blamed on an org the renderer has no words for"
    )


def test_nobody_is_available_to_talk_in_their_sleep():
    """`presence_intervals` is right that you are at home all night — a 03:00
    fire has to be able to find you there, and `witness_tiers` reads the same
    intervals to do it. It was `_copresence_windows` that read eight hours of
    sleep as eight hours of opportunity, so a 30-day soak has Mahavir Bafna
    telling two people something at 03:31."""
    for day in (0, 5):
        base = day * 86400
        intervals = {
            "person:sleep.a": [("home:1", base, base + 86400)],
            "person:sleep.b": [("home:1", base, base + 86400)],
        }
        windows = info._copresence_windows(intervals, SEED, day)
        assert windows, "two people home all day must still find a moment to talk"
        wake = max(info.awake_window(SEED, p)[0] for p in intervals)
        bed = min(info.awake_window(SEED, p)[1] for p in intervals)
        for lo, hi, _place, _a, _b in windows:
            assert base + wake <= lo < hi <= base + bed, (
                f"a contact window at {(lo % 86400) // 3600:02d}:{(lo % 3600) // 60:02d}, "
                "when both of them are asleep"
            )
    # the earliest anyone in the block leaves the house is a schoolchild at
    # 07:10, so a chronotype may never wake somebody after their own front door
    assert info.WAKE_S[1] <= 7 * 3600


def test_the_block_stops_gossiping_at_night(tmp_path, world):
    """The same thing end to end: no hop of any real run lands in the dark."""
    block, hhs, people = world
    log = EventLog(tmp_path / "night.db")
    engine.run_simulation(log, SEED, block, hhs, people, days=6, hazards=True)
    small_hours = [
        e for e in log.events(type="info.heard")
        if e.payload["channel"] == "f2f" and e.payload["source"] != "origin"
        and not info.WAKE_S[0] <= e.sim_time % 86400 < info.BED_S[1]
    ]
    assert not small_hours, (
        f"{len(small_hours)} conversations happened while everyone was asleep, "
        f"the first at {small_hours[0].sim_time % 86400 // 3600:02d}:"
        f"{small_hours[0].sim_time % 3600 // 60:02d}"
    )


def test_resuming_a_run_without_its_state_is_refused(tmp_path):
    """`start_day` without `state` would quietly start a second world."""
    block = Block.load()
    hhs, people = synthesize(SEED, block, n_households=10)
    log = EventLog(tmp_path / "resume.db")
    with pytest.raises(ValueError, match="start_day"):
        engine.run_simulation(log, SEED, block, hhs, people, days=1, start_day=4)


def test_an_unmapped_topic_moves_nobody(tmp_path):
    """A belief with no designed behaviour spreads without rerouting anyone.

    It is still heard, still remembered, still something a scene can be about.
    What it cannot do is silently acquire a mechanical consequence nobody chose
    for it — which is how a power cut once emptied a market.
    """
    block = Block.load()
    hhs, people = synthesize(SEED, block, n_households=40)
    place = block.of_kind("temple", "shop", "market")[0].id
    inj = engine.Injection(
        day=0, time_s=19 * 3600, type="info.rumor", place=place,
        participants=[next(iter(people))],
        payload={"credence": 0.95, "claim": {
            "key": "cl:unmapped", "subject": place, "predicate": "dangerous",
            "topics": ["astrology"],  # nothing in ACTION_THRESHOLDS
            "charge": 0.9, "specificity": 0.6, "veracity": "false", "valence": -0.8,
        }},
    )
    log = EventLog(tmp_path / "unmapped.db")
    engine.run_simulation(log, SEED, block, hhs, people, days=3, injections=[inj])
    heard = [e for e in log.events(type="info.heard")
             if e.payload.get("claim_key") == "cl:unmapped"]
    acted = [e for e in log.events(type="belief.action")
             if e.payload.get("claim_key") == "cl:unmapped"]
    log.close()
    assert heard, "the claim should still spread — this is not a gag order"
    assert not acted, (
        f"{len(acted)} people acted on a topic nothing maps; the sim guessed a "
        "behaviour for them instead of admitting it has none"
    )
