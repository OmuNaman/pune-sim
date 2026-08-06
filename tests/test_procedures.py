"""V2 institutions: hospital stay + bill, police FIR, finances-lite — offline.
The V2 exit trace lives here: the crash yields an FIR and a hospital bill that
raises p_financial and gates a money scene later, all zero-LLM."""

import pytest

from punesim import engine
from punesim.institutions import procedures
from punesim.kernel.log import EventLog
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


def _crash(block, hhs, people, severity=0.7):
    """Hit the most financially vulnerable daily-wage earner — the household
    for whom the V2 exit arc (bill -> loan -> money scene) is a real cliff."""
    fins = procedures.init_finances(SEED, hhs, people)
    victim = min(
        (p for p in people.values() if p.age >= 18 and p.occupation in engine.DAILY_WAGE),
        key=lambda p: (fins[p.household_id].liquid / fins[p.household_id].monthly_costs, p.id),
    )
    return victim, engine.Injection(
        day=0, time_s=9 * 3600, type="hazard.road.collision",
        place=block.places[0].id, participants=(victim.id,), severity=severity,
    )


def test_init_finances_is_keyed_and_sane(world):
    _, hhs, people = world
    f1 = procedures.init_finances(SEED, hhs, people)
    f2 = procedures.init_finances(SEED, hhs, people)
    assert f1 == f2
    assert all(v.liquid > 0 and v.monthly_costs > 0 for v in f1.values())


def test_admission_yields_stay_discharge_bill_and_rest(tmp_path, world):
    block, hhs, people = world
    victim, inj = _crash(block, hhs, people)
    log = EventLog(tmp_path / "h.db")
    _, _state = engine.run_simulation(log, SEED, block, hhs, people, days=10, injections=[inj])

    dis = [e for e in log.events(type="hospital.discharged")]
    assert len(dis) == 1
    assert dis[0].payload["person"] == victim.id
    assert dis[0].payload["bill"] > 3000
    adm_day = 0
    dis_day = dis[0].sim_time // 86400
    assert 1 <= dis_day <= 6
    # in the ward between admission and discharge; convalescing after
    for d in range(adm_day + 1, dis_day):
        day_evs = [
            e for e in log.events()
            if e.payload.get("person") == victim.id
            and d * 86400 <= e.sim_time < (d + 1) * 86400
            and e.type == "activity.start"
        ]
        assert day_evs and all(e.payload["activity"] == "admitted" for e in day_evs)
    rest_evs = [
        e for e in log.events()
        if e.payload.get("person") == victim.id and e.payload.get("activity") == "rest_at_home"
    ]
    assert rest_evs, "no convalescence after discharge"
    # staged healing: recovering then healed
    stages = [e.payload.get("stage") for e in log.events(type="condition.set")
              if e.payload.get("entity_id") == victim.id]
    assert "recovering" in stages and "healed" in stages
    # lineage: everything points back at the admission
    adm_seq = next(e.seq for e in log.events(type="hospital.admitted"))
    assert dis[0].caused_by == adm_seq


def test_fir_from_the_victims_own_account(tmp_path, world):
    block, hhs, people = world
    victim, inj = _crash(block, hhs, people, severity=0.6)
    log = EventLog(tmp_path / "f.db")
    engine.run_simulation(log, SEED, block, hhs, people, days=9, injections=[inj])

    firs = [e for e in log.events(type="police.fir.registered")]
    assert len(firs) == 1
    fir = firs[0]
    assert fir.sim_time // 86400 == 1  # next morning
    assert fir.payload["victim"] == victim.id
    assert fir.payload["statement"], "an FIR needs a statement"
    # the statement is the victim's own held variant of the claim
    held = [
        e.payload["claim"]["text"] for e in log.events(type="info.heard")
        if e.payload["person"] == victim.id
    ]
    assert fir.payload["statement"] in held
    updates = [e for e in log.events(type="fir.update")]
    assert updates and updates[0].sim_time // 86400 == 8


def test_bill_moves_money_and_p_financial(tmp_path, world):
    block, hhs, people = world
    victim, inj = _crash(block, hhs, people)
    hid = victim.household_id

    log0 = EventLog(tmp_path / "base.db")
    _, s0 = engine.run_simulation(log0, SEED, block, hhs, people, days=10)
    log1 = EventLog(tmp_path / "hurt.db")
    _, s1 = engine.run_simulation(log1, SEED, block, hhs, people, days=10, injections=[inj])

    paid = [e for e in log1.events(type="money.paid")]
    assert any(e.payload["household"] == hid for e in paid)
    # the injured world is poorer and more worried than the untouched one
    assert s1.proc.finances[hid].liquid < s0.proc.finances[hid].liquid
    adult = next(p.id for p in people.values() if p.household_id == hid and p.age >= 18)
    assert s1.pressures[adult]["p_financial"] > s0.pressures[adult]["p_financial"]
    # if the family had to borrow, the loan is on the books with lineage
    loans = [e for e in log1.events(type="loan.taken") if e.payload["household"] == hid]
    if loans:
        assert s1.proc.finances[hid].loans > 0
        assert loans[0].caused_by is not None


def test_money_scene_gate_fires_for_a_poor_household(tmp_path, world):
    """The V2 exit beat: financial pressure from the crash gates a scene —
    detectable zero-LLM as a pressure.crossed p_financial for the household."""
    block, hhs, people = world
    victim, inj = _crash(block, hhs, people)
    log = EventLog(tmp_path / "g.db")
    _, _state = engine.run_simulation(log, SEED, block, hhs, people, days=21, injections=[inj])
    crossed = [
        e for e in log.events(type="pressure.crossed")
        if e.payload["pressure"] == "p_financial"
        and people.get(e.payload["person"], victim).household_id == victim.household_id
    ]
    assert crossed, "three weeks of bills and lost wages moved nobody's needle"


def test_procedures_are_deterministic(tmp_path, world):
    block, hhs, people = world
    _, inj = _crash(block, hhs, people)
    hashes = []
    for name in ("d1.db", "d2.db"):
        log = EventLog(tmp_path / name)
        engine.run_simulation(log, SEED, block, hhs, people, days=12, injections=[inj])
        hashes.append(log.determinism_hash())
    assert hashes[0] == hashes[1]
