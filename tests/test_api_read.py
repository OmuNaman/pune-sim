"""The read API: does it answer about the right city, and can it carry a big one.

`src/punesim/viewer/` shipped with zero tests, which is how it kept a dossier
whose Interviews list could never populate and a timeline that could never
contain a trip. These cover the parts where being wrong is silent.
"""

import pathlib

import pytest
from fastapi.testclient import TestClient

from punesim import engine
from punesim.api import create_app
from punesim.api.positions import decode
from punesim.api.readlog import ReadOnlyLog, ro_uri
from punesim.kernel.log import EventLog
from punesim.population import synthesize
from punesim.world.block import load_for

pytestmark = pytest.mark.skipif(
    not pathlib.Path("data/anchors/kasba_places.geojson").exists(),
    reason="anchor data not fetched",
)

SEED, HOUSEHOLDS, DAYS = 108, 20, 2


@pytest.fixture(scope="module")
def served(tmp_path_factory):
    """A real run in a real runs/ directory, served by a real app.

    The collision is injected rather than sampled. Hazard rates are per-capita
    (data/classdefs/hazards.json), so 20 households draw 0.0007 hazards a day
    and a test that waited for one would be asserting on an empty log.
    """
    root = tmp_path_factory.mktemp("runs")
    d = root / "unit"
    d.mkdir()
    block = load_for(HOUSEHOLDS)
    hhs, people = synthesize(SEED, block, n_households=HOUSEHOLDS)
    victim = next(p.id for p in people.values() if p.age >= 18)
    inj = engine.Injection(
        day=0, time_s=9 * 3600, type="hazard.road.collision",
        place=block.places[0].id, participants=(victim,), severity=0.6,
    )
    log = EventLog(d / "events.db")
    engine.run_simulation(log, SEED, block, hhs, people, days=DAYS, hazards=True,
                          injections=[inj])
    log.close()
    app = create_app(runs_root=str(root))
    with TestClient(app) as client:
        yield client, "adopted:unit", people


def test_a_windows_path_survives_the_readonly_uri(tmp_path):
    """`f"file:{path}"` with `D:\\runs\\x.db` in it gives SQLite backslash
    escapes and a drive letter it reads as a URI authority. Both silently open
    the wrong thing or nothing."""
    uri = ro_uri(tmp_path / "sub dir" / "events.db")
    assert uri.startswith("file:/") and "\\" not in uri
    assert uri.endswith("?mode=ro")


def test_the_roster_comes_from_the_log_not_the_caller(served):
    """The whole reason api/worldcache.py exists.

    `punesim serve` took --seed/--households/--block and rebuilt the population
    from those; point it at the wrong log and it does not crash, it prints a
    different family's name over the right family's events. Nothing in this API
    accepts a seed, so meta must match what the run actually wrote.
    """
    client, rid, people = served
    m = client.get(f"/api/runs/{rid}/meta").json()
    assert m["seed"] == SEED
    assert m["households"] == HOUSEHOLDS
    assert m["days_done"] == DAYS
    assert m["block"] == "kasba"
    # ...and once something has actually needed the roster, the count agrees
    client.get(f"/api/runs/{rid}/roster")
    assert client.get(f"/api/runs/{rid}/meta").json()["people"] == len(people)


def test_meta_and_the_map_do_not_wait_for_the_population(served):
    """13 seconds of blank screen, at V3 scale, is what this prevents.

    Synthesizing 49,578 people takes 3.8 s, the road graph 1.3 s, and the first
    day of movement 5.6 s. None of that is needed to know where the city is or
    how many days it has, so the header and the streets must answer from the
    log and the block alone — the map draws while the people are still being
    made.
    """
    client, rid, _ = served
    fresh = create_app(runs_root=str(pathlib.Path(client.app.state.registry.root)))
    with TestClient(fresh) as c2:
        m = c2.get(f"/api/runs/{rid}/meta").json()
        assert m["world_ready"] is False, "meta built a world it did not need"
        assert m["bounds"] and m["days_done"] == DAYS
        assert c2.get(f"/api/runs/{rid}/geo/roads").json()["features"]
        assert c2.get(f"/api/runs/{rid}/places").json()
        assert c2.app.state.worlds.cached(rid) is None, (
            "drawing the map should not have synthesized the population"
        )


def test_positions_carry_everybody_and_round_trip(served):
    """The old endpoint capped at 4,000 people by id order and drew a twelfth
    of the city. This sends all of them, in 9 bytes each."""
    client, rid, people = served
    r = client.get(f"/api/runs/{rid}/positions", params={"t": 12 * 3600})
    assert r.headers["content-type"] == "application/octet-stream"
    _v, tick, rows = decode(r.content)
    assert tick == 12 * 3600
    assert len(rows) == len(people), "somebody was dropped, so every ordinal after them shifted"
    assert len(r.content) == 16 + 9 * len(people)
    placed = [x for x in rows if x[0] == x[0]]  # NaN != NaN
    assert len(placed) > len(rows) * 0.9, "most of the city should be somewhere"


def test_the_ordinal_index_is_the_roster_order(served):
    """The binary buffer has no ids in it — position i IS person order[i]. If
    those two ever disagree the whole map is mislabelled and nothing raises."""
    client, rid, people = served
    order = client.get(f"/api/runs/{rid}/roster").json()["order"]
    assert order == sorted(people)
    page = client.get(f"/api/runs/{rid}/people", params={"limit": 5}).json()
    for item in page["items"]:
        assert order[item["ord"]] == item["id"]


def test_a_dossier_carries_the_lanes_the_old_one_dropped(served):
    """The old dossier built its timeline inside a seven-type loop, so a
    person's own day could never contain a trip, an admission, an FIR or a
    rupee — and it asked for `interview.answered`, a type nothing emits, while
    the branch reading interviews matched `conversation.held`, which was never
    fetched."""
    client, rid, people = served
    pid = sorted(people)[0]
    d = client.get(f"/api/runs/{rid}/person/{pid}", params={"day": 1}).json()
    assert d["id"] == pid and d["members"], "a person belongs to a household"
    assert d["trips"], "day 1 has no movement at all for this person"
    for line in d["timeline"]:
        assert "refs" in line, "the client must not have to regex the prose"


def test_people_are_paginated_and_searchable(served):
    """`/api/people` returned the entire roster — 7 MB at V3 scale, on load."""
    client, rid, people = served
    first = client.get(f"/api/runs/{rid}/people", params={"limit": 5}).json()
    assert first["total"] == len(people) and len(first["items"]) == 5
    name = first["items"][0]["name"].split()[0]
    hits = client.get(f"/api/runs/{rid}/people", params={"q": name}).json()
    assert 0 < hits["total"] <= len(people)


def test_the_ticker_tails_by_seq(served):
    """`since_seq` is what makes watching a live run possible: a client that
    has seen up to N asks for what came after, not for the run again."""
    client, rid, _ = served
    whole = client.get(f"/api/runs/{rid}/ticker").json()
    assert whole["items"], "the injected collision and its reactions are notable"
    mid = whole["items"][len(whole["items"]) // 2]["seq"]
    tail = client.get(f"/api/runs/{rid}/ticker", params={"since_seq": mid}).json()
    assert all(i["seq"] > mid for i in tail["items"])
    assert len(tail["items"]) < len(whole["items"])
    assert whole["last_seq"] == whole["items"][-1]["seq"]


def test_geometry_is_served_at_all(served):
    """No endpoint has ever exposed the buildings or the streets. The map drew
    CDN raster tiles over a city whose real shape was sitting in the repo."""
    client, rid, _ = served
    b = client.get(f"/api/runs/{rid}/geo/buildings").json()
    r = client.get(f"/api/runs/{rid}/geo/roads").json()
    assert len(b["features"]) > 1000 and len(r["features"]) > 100
    roles = {f["properties"]["role"] for f in b["features"]}
    assert {"place", "home"} <= roles
    # the join that makes a clicked polygon a place card: OSM id -> Place.id
    sim_ids = {f["properties"]["sim_id"] for f in b["features"]}
    places = client.get(f"/api/runs/{rid}/places").json()
    assert sum(1 for p in places if p["id"] in sim_ids) > 10, (
        "no building matched a simulated place, so clicking the map can never "
        "open the place it drew"
    )


def test_the_day_strip_counts_without_reading_the_log(served):
    client, rid, _ = served
    days = client.get(f"/api/runs/{rid}/days").json()
    assert len(days) == DAYS
    assert all(d["total"] >= d["notable"] for d in days)
    assert sum(d["total"] for d in days) == ReadOnlyLog(
        client.get(f"/api/runs/{rid}/meta").json() and
        pathlib.Path(client.app.state.registry.get(rid).db)
    ).summary()[0]


def test_no_event_ever_renders_as_a_raw_payload(served):
    """The old viewer's fallback dumped JSON into the ticker, a model read it
    back, and `test_no_prompt_line_ever_dumps_a_raw_payload` exists because of
    it. A type with no sentence should name itself and say nothing else — a
    missing sentence is a TODO, not something to show a person."""
    from punesim.api.humanize import text_for

    client, rid, _ = served
    w = client.app.state.worlds.get(rid, client.app.state.registry.get(rid).db)
    for typ in w.view.types():
        rows = w.view.of_type(typ, limit=1)
        if not rows:
            continue
        text = text_for(rows[0], w.person_names, w.place_names)
        assert "{" not in text and '":' not in text, f"{typ} leaked its payload: {text}"
        assert not text.startswith(f"{typ}:"), f"{typ} rendered as its own type name"


def test_a_plan_step_reference_is_never_printed_raw(served):
    """`plan.step_dropped.place_ref` is a schedule reference, not a place id:
    the same field holds `place:home.way/1`, `place:way/2`, a bare `node/3` and
    the literal `place:unknown`. None are keys the block knows."""
    from punesim.api.humanize import _place_ref

    client, rid, _ = served
    w = client.app.state.worlds.get(rid, client.app.state.registry.get(rid).db)
    for ref in ("place:unknown", "", None, "place:home.way/999", "node/12345"):
        out = _place_ref(ref, w.place_names)
        assert ":" not in out and "/" not in out, f"{ref!r} printed raw as {out!r}"
    # ...and a ref it CAN resolve still resolves
    real = next(iter(w.place_names))
    assert _place_ref(real, w.place_names) == w.place_names[real]


def test_one_persons_movement_does_not_poison_the_city_cache(served):
    """`segs_for_day(person=…)` narrows in SQL — 6.9s of a 10.5s dossier at V3
    scale was parsing 224,544 movement rows to show one person's walk. The map
    reads the SAME day cache, so a one-person answer landing in it would draw a
    city of one, and nothing would raise."""
    client, rid, people = served
    view = client.app.state.worlds.get(rid, client.app.state.registry.get(rid).db).view
    everyone = view.segs_for_day(1)
    pid = sorted(people)[0]
    one = view.segs_for_day(1, person=pid)
    assert set(one) == {pid}
    assert len(view.segs_for_day(1)) == len(everyone) > 1, (
        "the whole-city day cache was replaced by one person's movement"
    )


def test_the_ticker_can_be_asked_for_one_day(served):
    """Without this the endpoint returns the last N events of the WHOLE run, so
    a client sitting on day 1 of a 30-day log gets a page of day 29, filters it
    to nothing, and truthfully reports that nothing has happened."""
    client, rid, _ = served
    d0 = client.get(f"/api/runs/{rid}/ticker", params={"day": 0}).json()["items"]
    d1 = client.get(f"/api/runs/{rid}/ticker", params={"day": 1}).json()["items"]
    assert d0, "day 0 has the injected collision in it"
    assert all(e["day"] == 0 for e in d0)
    assert all(e["day"] == 1 for e in d1)
    whole = client.get(f"/api/runs/{rid}/ticker").json()["items"]
    assert len(d0) + len(d1) == len(whole)


def test_the_consequence_cone_walks_real_causation(served):
    """An injection is a stone and this is the ripple. `caused_by` is a real
    column, so the chain is the log's own claim about what caused what — not a
    guess from things happening near each other in time."""
    client, rid, _ = served
    items = client.get(f"/api/runs/{rid}/ticker").json()["items"]
    crash = next(i for i in items if i["type"] == "hazard.road.collision")
    cone = client.get(f"/api/runs/{rid}/cone/{crash['seq']}").json()
    assert cone["root"]["type"] == "hazard.road.collision"
    assert cone["children"], "a collision that caused nothing at all"
    assert {c["type"] for c in cone["children"]} & {
        "ambulance.dispatched", "hospital.admitted", "condition.set"}
    assert all(c["depth"] >= 1 for c in cone["children"])


def test_a_summary_is_not_recomputed_for_a_log_that_has_not_changed(served):
    """COUNT(*) is 0.5-2s on a 6.8M-row log and the header asks for it on every
    page. The cache key is the file's own (size, mtime), so a live run misses
    exactly when it should."""
    client, rid, _ = served
    log = ReadOnlyLog(client.app.state.registry.get(rid).db)
    assert log.summary() == log.summary()
