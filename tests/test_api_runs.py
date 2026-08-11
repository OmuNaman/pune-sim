"""Driving the engine from the API: does it produce the same world?

The point of this file is one claim: a run started, paused, injected into and
resumed from a browser is the SAME run as one launched from the command line.
If that is not true then the UI is a different simulator wearing the sim's
clothes, and every number it shows is about a world nobody else can reproduce.
"""

import pathlib
import time

import pytest
from fastapi.testclient import TestClient

from punesim import engine
from punesim.api import create_app
from punesim.kernel.log import EventLog
from punesim.population import synthesize
from punesim.world.block import load_for

pytestmark = pytest.mark.skipif(
    not pathlib.Path("data/anchors/kasba_places.geojson").exists(),
    reason="anchor data not fetched",
)

SEED, HOUSEHOLDS, DAYS = 108, 20, 3
TIMEOUT = 120  # seconds; a 20-household day is ~0.01s, so this is only for hangs


@pytest.fixture
def client(tmp_path):
    app = create_app(runs_root=str(tmp_path / "runs"))
    with TestClient(app) as c:
        yield c
        c.app.state.manager.stop_all()


def _wait(client, run_id: str, *, until, timeout: int = TIMEOUT) -> dict:
    """Poll the worker's status until `until(status)` or we give up."""
    deadline = time.time() + timeout
    last: dict = {}
    while time.time() < deadline:
        last = client.get(f"/api/runs/{run_id}/events").json()["status"]
        if until(last):
            return last
        time.sleep(0.05)
    raise AssertionError(f"timed out waiting; last status was {last}")


def _hash_of(db: pathlib.Path) -> str:
    log = EventLog(db)
    try:
        return log.determinism_hash()
    finally:
        log.close()


def test_a_run_driven_from_the_api_matches_one_from_the_cli(client, tmp_path):
    """The claim the whole UI rests on.

    The worker calls `run_simulation` ONCE with the full day count, because
    run.meta records the days argument of the FIRST call (loop.py:68-80) — a
    worker that drove it a day at a time with days=1 would write `days: 1` and
    hash differently from an uninterrupted run of the same length.
    """
    r = client.post("/api/runs", json={
        "name": "api", "block": "kasba", "households": HOUSEHOLDS,
        "days": DAYS, "seed": SEED, "hazards": True, "autostart": True,
    }).json()
    rid = r["id"]
    _wait(client, rid, until=lambda s: s.get("status") in ("finished", "error"))
    status = client.get(f"/api/runs/{rid}/events").json()["status"]
    assert status["status"] == "finished", status

    api_db = pathlib.Path(client.app.state.registry.get(rid).db)

    # ...and the same run, the way the CLI does it.
    block = load_for(HOUSEHOLDS)
    hhs, people = synthesize(SEED, block, n_households=HOUSEHOLDS)
    cli_db = tmp_path / "cli.db"
    log = EventLog(cli_db)
    engine.run_simulation(log, SEED, block, hhs, people, days=DAYS, hazards=True)
    log.close()

    assert _hash_of(api_db) == _hash_of(cli_db), (
        "a run driven from the API is a different world from the same run "
        "driven from the CLI — every number the UI shows would be about a "
        "simulation nobody else can reproduce"
    )


def test_pause_stops_at_a_day_boundary_and_resume_continues(client):
    """Pause is not a freeze — the worker finishes the day in flight, because
    that is the only moment `state` is a consistent world (loop.py:325)."""
    rid = client.post("/api/runs", json={
        "name": "paused", "households": HOUSEHOLDS, "days": DAYS, "seed": SEED,
        "autostart": False,
    }).json()["id"]
    _wait(client, rid, until=lambda s: s.get("status") == "paused")

    client.post(f"/api/runs/{rid}/step")
    _wait(client, rid, until=lambda s: s.get("day") == 0)
    mid = client.get(f"/api/runs/{rid}/meta").json()
    assert mid["days_done"] == 1, "step ran more than one day"

    client.post(f"/api/runs/{rid}/play")
    _wait(client, rid, until=lambda s: s.get("status") == "finished")
    assert client.get(f"/api/runs/{rid}/meta").json()["days_done"] == DAYS


def test_an_injection_from_the_api_lands_and_is_recoverable(client):
    """A run steered from the browser must still be reproducible from its own
    log: injections commit with provenance 'user' and no cause, which is exactly
    what `branch.reconstruct_injections` recovers (branch.py:27-45)."""
    from punesim.api.readlog import ReadOnlyLog
    from punesim.branch import reconstruct_injections

    rid = client.post("/api/runs", json={
        "name": "injected", "households": HOUSEHOLDS, "days": DAYS, "seed": SEED,
        "autostart": False,
    }).json()["id"]
    _wait(client, rid, until=lambda s: s.get("status") == "paused")

    block = load_for(HOUSEHOLDS)
    place = block.places[0].id
    body = {"day": 1, "time": "09:30", "type": "hazard.water.supply_cut",
            "place": place, "severity": 0.6}
    assert client.post(f"/api/runs/{rid}/inject", json=body).json()["queued"]

    client.post(f"/api/runs/{rid}/play")
    _wait(client, rid, until=lambda s: s.get("status") == "finished")

    db = client.app.state.registry.get(rid).db
    log = ReadOnlyLog(db)
    hits = [e for e in log.events(type="hazard.water.supply_cut")]
    assert hits, "the injected water cut never happened"
    assert hits[0].provenance == "user" and hits[0].caused_by is None
    back = reconstruct_injections(log)
    assert any(i.type == "hazard.water.supply_cut" and i.day == 1 for i in back), (
        "the injection cannot be recovered from the log, so this run cannot be "
        "branched or re-run from its own record"
    )


def test_injecting_into_the_past_is_refused_not_silently_dropped(client):
    """Days already computed are history. Offering to rewrite them and then not
    doing it is worse than saying no."""
    rid = client.post("/api/runs", json={
        "name": "late", "households": HOUSEHOLDS, "days": DAYS, "seed": SEED,
        "autostart": True,
    }).json()["id"]
    _wait(client, rid, until=lambda s: s.get("status") == "finished")
    r = client.post(f"/api/runs/{rid}/inject", json={
        "day": 0, "time": "09:00", "type": "hazard.fire.small"})
    assert r.status_code == 409
    assert "already been computed" in r.json()["detail"]


def test_an_adopted_run_can_be_read_but_not_driven(client, tmp_path):
    """A run this UI did not create has no checkpoint and no recorded horizon;
    play would have to invent both."""
    d = tmp_path / "runs" / "handmade"
    d.mkdir(parents=True)
    block = load_for(HOUSEHOLDS)
    hhs, people = synthesize(SEED, block, n_households=HOUSEHOLDS)
    log = EventLog(d / "events.db")
    engine.run_simulation(log, SEED, block, hhs, people, days=1)
    log.close()
    client.app.state.registry.scan()

    r = client.post("/api/runs/adopted:handmade/play")
    assert r.status_code == 409 and "branch" in r.json()["detail"].lower()
    assert client.get("/api/runs/adopted:handmade/meta").json()["days_done"] == 1
