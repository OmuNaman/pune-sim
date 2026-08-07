"""Procedures as data — including one that did not exist before this test."""

from dataclasses import dataclass

import pytest

from punesim.institutions.catalog import CATALOG, HOSPITAL_STAY, POLICE_FIR
from punesim.institutions.interpreter import Procedure, Step, run
from punesim.kernel.timebase import SECONDS_PER_DAY


@dataclass
class _Event:
    seq: int
    type: str
    payload: dict


class _State:
    """Only what the interpreter is allowed to touch."""

    def __init__(self):
        self.billed, self.fir_filed, self.summons = set(), set(), set()
        self.in_hospital, self.rest = {}, {}


def test_a_new_procedure_is_data_and_a_binder():
    """The whole claim of the interpreter, stated as a test.

    A court summons — an institution the sim has never had — is a trigger, five
    lines of binder, and two scheduled events. No engine change, no new branch
    in `step`, nothing added to the effect vocabulary.
    """
    summons = Procedure(
        name="court_summons",
        dedup="summons",
        match=lambda e: e.type == "police.fir.registered",
        bind=lambda e, ctx: {
            "who": e.payload["complainant"],
            "about": e.seq,
            "d_hearing": ctx["day"] + 21,
            "d_reminder": ctx["day"] + 14,
        },
        steps=(
            Step("d_reminder", 9 * 3600, "court.notice_served",
                 {"person": "$who", "about_seq": "$about"}),
            Step("d_hearing", 11 * 3600, "court.hearing_listed",
                 {"person": "$who", "about_seq": "$about", "stage": "first_hearing"}),
        ),
    )
    state = _State()
    fir = _Event(seq=41, type="police.fir.registered",
                 payload={"complainant": "person:007.0", "victim": "person:007.2"})

    pending = run([summons], [fir], state, {"day": 3})

    assert sorted(pending) == [17, 24]
    notice = pending[17][0]
    assert notice.type == "court.notice_served"
    assert notice.payload == {"person": "person:007.0", "about_seq": 41}
    assert notice.sim_time == 17 * SECONDS_PER_DAY + 9 * 3600
    assert notice.caused_by == 41, "a scheduled future points back at what caused it"
    assert pending[24][0].payload["stage"] == "first_hearing"

    # and it refuses to fire twice on the same event, without saying so anywhere
    assert run([summons], [fir], state, {"day": 3}) == {}


def test_declining_still_counts_as_handled():
    """A binder that returns None schedules nothing and is never retried.

    The hand-written police procedure marked a hazard filed before discovering
    its victim was not in the roster, and that is deliberate: retrying such an
    event every day for the rest of a 30-day run would be worse than dropping
    it.
    """
    picky = Procedure(
        name="picky", dedup="summons",
        match=lambda e: True, bind=lambda e, ctx: None,
        steps=(Step("d", 0, "never.happens", {}),),
    )
    state = _State()
    e = _Event(seq=9, type="anything", payload={})
    assert run([picky], [e], state, {}) == {}
    assert 9 in state.summons


def test_the_shipped_catalog_is_what_v2_wrote_by_hand():
    assert CATALOG == [HOSPITAL_STAY, POLICE_FIR], (
        "order fixes the sequence futures enter the pending queue, and so the "
        "seq of everything downstream — the determinism hash notices"
    )
    assert HOSPITAL_STAY.commit is not None, "a stay marks someone in hospital"
    assert POLICE_FIR.commit is None, "filing a complaint changes nobody's day"
    for proc in CATALOG:
        assert proc.steps, f"{proc.name} schedules nothing"


@pytest.mark.parametrize("proc", CATALOG, ids=lambda p: p.name)
def test_every_step_references_a_binding_the_binder_could_supply(proc):
    """A `$typo` would surface as a silent None in a committed payload."""
    for step in proc.steps:
        refs = {v[1:] for v in step.payload.values() if isinstance(v, str) and v.startswith("$")}
        assert step.day.isidentifier()
        for r in refs:
            assert r.isidentifier(), f"{proc.name}: bad reference ${r}"
