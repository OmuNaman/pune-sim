"""The procedures V2 wrote by hand, as data.

Two so far. The point of the pair is that they no longer share a shape by
coincidence — a third (a court hearing, a school admission, a PMC complaint) is
a `Procedure` and a binder, not another seventy lines of the same thing.
"""

from ..kernel.rng import keyed_rng
from .interpreter import Procedure, Step

FIR_SEVERITY_MIN = 0.4

# The hour a ward bed is given up. Named because the scene lane has to tell a
# family, on the discharge morning itself, that the patient is still in the
# ward at 06:30 — a fact it can only state truthfully if it reads the same
# number this procedure schedules the discharge at.
DISCHARGE_HOUR_S = 10 * 3600


def _hospital_bind(e, ctx) -> dict | None:
    """How long they stay, what it costs, and when they are well again."""
    pid = e.payload["person"]
    day, people = ctx["day"], ctx["people"]
    sev = ctx["intensity_today"].get(pid, 0.5)
    rng = keyed_rng(ctx["run_seed"], "hospital", pid, day, "stay")
    stay = 1 + int(sev * 3 + rng.random() * 2)
    d_dis = day + stay
    heal = d_dis + max(1, int(sev * 8))
    return {
        "pid": pid,
        "place": e.payload.get("place"),
        "household": people[pid].household_id if pid in people else None,
        "bill": float(round(3000 + sev * 30000 + rng.random() * 4000, -2)),
        "recovering": round(sev * 0.5, 2),
        "d_dis": d_dis,
        "heal": heal,
        # a ward bed, then convalescence at home for half as long again
        "rest_until": d_dis + max(1, (heal - d_dis) // 2),
    }


def _hospital_commit(b, state) -> None:
    state.in_hospital[b["pid"]] = (b["d_dis"], b["place"] or "")
    state.rest[b["pid"]] = max(state.rest.get(b["pid"], 0), b["rest_until"])


HOSPITAL_STAY = Procedure(
    name="hospital_stay",
    dedup="billed",
    match=lambda e: e.type == "hospital.admitted",
    bind=_hospital_bind,
    commit=_hospital_commit,
    steps=(
        Step("d_dis", DISCHARGE_HOUR_S, "hospital.discharged",
             {"person": "$pid", "place": "$place", "bill": "$bill",
              "household": "$household"}),
        Step("d_dis", DISCHARGE_HOUR_S + 60, "condition.set",
             {"entity_id": "$pid", "kind": "injury", "intensity": "$recovering",
              "stage": "recovering"}),
        Step("heal", 9 * 3600, "condition.set",
             {"entity_id": "$pid", "kind": "injury", "intensity": 0.0,
              "stage": "healed"}),
    ),
)


def _fir_match(e) -> bool:
    return (
        e.type.startswith("hazard.")
        and bool(e.payload.get("participants"))
        and float(e.payload.get("severity") or 0) >= FIR_SEVERITY_MIN
    )


def _fir_bind(e, ctx) -> dict | None:
    """Who complains, at which station, and in whose words.

    The statement is the victim's *own held account* of what happened, drift and
    all — not the log's ground truth. A complaint is testimony.
    """
    day, people, block, info = ctx["day"], ctx["people"], ctx["block"], ctx["info"]
    victim = e.payload["participants"][0]
    person = people.get(victim)
    if person is None:
        return None
    adults = [
        q.id for q in people.values()
        if q.household_id == person.household_id and q.age >= 18 and q.id != victim
    ]
    station = block.nearest(e.payload.get("place") or person.home_id, "police")
    claim_key = f"cl:{e.type.split('.', 1)[1]}:{e.payload.get('place')}:d{day}"
    holding = info.holdings.get(victim, {}).get(claim_key)
    return {
        "victim": victim,
        "complainant": min(adults) if adults else victim,
        "station": station.id if station else None,
        "about_seq": e.seq,
        "statement": (
            holding.claim.text if holding
            else f"{person.given} was hurt in a {e.type.split('.')[-1].replace('_', ' ')}."
        ),
        "d_fir": day + 1,
        "d_update": day + 8,
    }


POLICE_FIR = Procedure(
    name="police_fir",
    dedup="fir_filed",
    match=_fir_match,
    bind=_fir_bind,
    steps=(
        Step("d_fir", 11 * 3600, "police.fir.registered",
             {"complainant": "$complainant", "victim": "$victim", "station": "$station",
              "about_seq": "$about_seq", "statement": "$statement"}),
        Step("d_update", 12 * 3600, "fir.update",
             {"about_seq": "$about_seq", "victim": "$victim",
              "status": "investigation pending"}),
    ),
)

# Order is load-bearing: it fixes the sequence futures enter the pending queue,
# and so the seq of everything downstream.
CATALOG = [HOSPITAL_STAY, POLICE_FIR]
