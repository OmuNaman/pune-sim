import pytest

from punesim import engine
from punesim.kernel.log import EventLog
from punesim.population import synthesize
from punesim.world.block import Block

pytestmark = pytest.mark.skipif(
    not __import__("pathlib").Path("data/anchors/kasba_places.geojson").exists(),
    reason="anchor data not fetched",
)


@pytest.fixture(scope="module")
def world():
    block = Block.load()
    _, people = synthesize(11, block, n_households=40)
    return block, people


def test_day_is_deterministic(tmp_path, world):
    block, people = world
    a, b = EventLog(tmp_path / "a.db"), EventLog(tmp_path / "b.db")
    na = engine.run_days(a, 11, block, people, days=1)
    nb = engine.run_days(b, 11, block, people, days=1)
    assert na == nb > 100
    assert a.determinism_hash() == b.determinism_hash()


def test_different_seed_different_day(tmp_path, world):
    block, people = world
    a, b = EventLog(tmp_path / "a.db"), EventLog(tmp_path / "b.db")
    engine.run_days(a, 11, block, people, days=1)
    engine.run_days(b, 12, block, people, days=1)  # same people, different jitter
    assert a.determinism_hash() != b.determinism_hash()


def test_events_are_time_ordered_and_coherent(tmp_path, world):
    block, people = world
    log = EventLog(tmp_path / "e.db")
    engine.run_days(log, 11, block, people, days=2)
    last = -1
    open_trips: dict[str, str] = {}
    for e in log.events():
        assert e.sim_time >= last
        last = e.sim_time
        pid = e.payload.get("person")
        if e.type == "trip.start":
            assert pid not in open_trips, f"{pid} started a trip while travelling"
            open_trips[pid] = e.payload["to"]
        elif e.type == "trip.end":
            assert open_trips.pop(pid) == e.payload["at"]
    assert not open_trips  # everyone arrived
