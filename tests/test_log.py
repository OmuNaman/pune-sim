from punesim.kernel import EventIn, EventLog


def _events():
    return [
        EventIn(type="trip.start", sim_time=300, payload={"person": "p1", "mode": "walk"}),
        EventIn(type="trip.end", sim_time=900, payload={"person": "p1"}, provenance="clockwork"),
        EventIn(type="hazard.road.collision", sim_time=1200, payload={"severity": 0.4}),
    ]


def test_commit_and_iterate(tmp_path):
    log = EventLog(tmp_path / "e.db")
    seqs = log.commit(_events())
    assert seqs == [1, 2, 3]
    evts = list(log.events())
    assert [e.type for e in evts] == ["trip.start", "trip.end", "hazard.road.collision"]
    assert evts[0].payload["mode"] == "walk"
    assert evts[0].tick == 1  # sim_time 300 -> tick 1


def test_determinism_hash_ignores_wall_meta(tmp_path):
    a, b = EventLog(tmp_path / "a.db"), EventLog(tmp_path / "b.db")
    a.commit(_events(), wall_meta={"wall_ms": 111, "host": "x"})
    b.commit(_events(), wall_meta={"wall_ms": 999, "host": "y"})
    assert a.determinism_hash() == b.determinism_hash()


def test_determinism_hash_sees_payload_change(tmp_path):
    a, b = EventLog(tmp_path / "a.db"), EventLog(tmp_path / "b.db")
    a.commit(_events())
    evts = _events()
    evts[2].payload["severity"] = 0.5
    b.commit(evts)
    assert a.determinism_hash() != b.determinism_hash()


def test_llm_response_is_an_input_event(tmp_path):
    log = EventLog(tmp_path / "e.db")
    log.record_llm_response(
        request_id="abc123",
        model="deepseek/deepseek-chat",
        response_text='{"ok": true}',
        usage={"total_tokens": 42},
        sim_time=600,
    )
    [e] = list(log.events(type="llm.response"))
    assert e.provenance == "llm"
    assert e.payload["request_id"] == "abc123"
    assert e.payload["usage"]["total_tokens"] == 42
