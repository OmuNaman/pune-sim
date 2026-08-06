"""V2 procedures: the hospital and the police push back (plain Python).

Two hand-written procedures (architecture §6 V2) replace fire-and-forget
stubs with processes that have DURATION and COST:

- hospital: admission -> a severity-scaled stay -> discharge with a BILL;
  the injury heals through staged conditions and the patient rests at home
  until fit — missed work days and the bill flow into finances-lite, which
  is what finally makes p_financial an honest number instead of a bump.
- police: a serious hazard with victims gets an FIR the next morning,
  registered from the complainant's OWN held account of the event (their
  claim variant, drift included — witnesses can honestly disagree with the
  record), then an investigation-pending update a week later.

Everything is deterministic: keyed draws only, futures scheduled as pending
TimedEvents that the engine merges into later days.
"""

from dataclasses import dataclass, field

from ..kernel.rng import keyed_rng
from ..kernel.timebase import SECONDS_PER_DAY
from ..minds.info import InfoState
from ..population.synth import Household, Person
from ..world.block import Block
from ..world.schedule import TimedEvent

# ₹/month, rough Kasba texture (finances-lite; replaced by real ledgers at V3)
MONTHLY_INCOME = {
    "shopkeeper": 32000, "office_clerk": 28000, "shop_assistant": 14000,
    "tailor": 16000, "rickshaw_driver": 18000, "teacher": 30000,
    "domestic_worker": 10000, "cook": 15000, "nurse": 26000,
    "bank_clerk": 32000, "police_constable": 30000, "doctor": 70000,
    "priest": 12000, "student": 0, "homemaker": 0, "retired": 8000, "infant": 0,
}
WORK_DAYS_PER_MONTH = 26
LOAN_MONTHLY_RATE = 0.03  # the moneylender's rate; banks come with V3
FIR_SEVERITY_MIN = 0.4


@dataclass
class Finances:
    liquid: float
    monthly_income: float
    monthly_costs: float
    loans: float = 0.0  # outstanding informal principal


@dataclass
class ProcState:
    finances: dict[str, Finances] = field(default_factory=dict)  # household ->
    rest: dict[str, int] = field(default_factory=dict)  # person -> rests until day (exclusive)
    in_hospital: dict[str, tuple[int, str]] = field(default_factory=dict)  # person -> (until_day, place)
    fir_filed: set = field(default_factory=set)  # hazard seqs already FIRed
    billed: set = field(default_factory=set)  # admission seqs already processed


def init_finances(
    run_seed: int, households: list[Household], people: dict[str, Person]
) -> dict[str, Finances]:
    """D0 money: income from occupations, costs from household size, savings
    from a keyed draw — a P1-thin stand-in for 03-cognition §1.3."""
    out: dict[str, Finances] = {}
    for hh in households:
        income = float(sum(MONTHLY_INCOME.get(people[m].occupation, 12000) for m in hh.member_ids))
        costs = 6000.0 + 2600.0 * len(hh.member_ids)
        rng = keyed_rng(run_seed, "finance", hh.id, 0, "init")
        liquid = max(1500.0, income * (0.5 + rng.random() * 1.8))
        out[hh.id] = Finances(liquid=round(liquid, -2), monthly_income=income, monthly_costs=costs)
    return out


def p_financial(f: Finances) -> float:
    """Closed-form worry: thin cousin of 03-cognition §1.3's sigmoid.
    The runway term has gradient up to 2.5 months of costs saved — a ₹24k
    hospital bill is invisible to the comfortable and a cliff to the poor."""
    runway = 1.0 - f.liquid / (2.5 * f.monthly_costs)
    debt = f.loans / max(f.monthly_income, 4000.0)
    return round(min(1.0, max(0.0, 0.12 + 0.6 * max(0.0, runway) + min(0.3, 0.3 * debt))), 3)


def step(
    log_events_today: list,
    state: ProcState,
    run_seed: int,
    day: int,
    block: Block,
    people: dict[str, Person],
    info: InfoState,
) -> dict[int, list[TimedEvent]]:
    """Scan today's committed events; schedule the institutions' futures.
    Returns {future_day: [TimedEvent]} for the engine's pending queue."""
    pending: dict[int, list[TimedEvent]] = {}

    def later(d: int, te: TimedEvent) -> None:
        pending.setdefault(d, []).append(te)

    intensity_today = {
        e.payload.get("entity_id"): float(e.payload.get("intensity") or 0.5)
        for e in log_events_today
        if e.type == "condition.set" and e.payload.get("kind") == "injury"
    }

    for e in log_events_today:
        # --- hospital: stay, staged healing, discharge with a bill ----------
        if e.type == "hospital.admitted" and e.seq not in state.billed:
            state.billed.add(e.seq)
            pid = e.payload["person"]
            sev = intensity_today.get(pid, 0.5)
            rng = keyed_rng(run_seed, "hospital", pid, day, "stay")
            stay = 1 + int(sev * 3 + rng.random() * 2)
            bill = float(round(3000 + sev * 30000 + rng.random() * 4000, -2))
            d_dis = day + stay
            t_dis = d_dis * SECONDS_PER_DAY + 10 * 3600
            later(d_dis, TimedEvent(
                t_dis, "hospital.discharged",
                {"person": pid, "place": e.payload.get("place"), "bill": bill,
                 "household": people[pid].household_id if pid in people else None},
                e.seq,
            ))
            later(d_dis, TimedEvent(
                t_dis + 60, "condition.set",
                {"entity_id": pid, "kind": "injury", "intensity": round(sev * 0.5, 2),
                 "stage": "recovering"}, e.seq,
            ))
            heal = d_dis + max(1, int(sev * 8))
            later(heal, TimedEvent(
                heal * SECONDS_PER_DAY + 9 * 3600, "condition.set",
                {"entity_id": pid, "kind": "injury", "intensity": 0.0, "stage": "healed"},
                e.seq,
            ))
            place = e.payload.get("place") or ""
            state.in_hospital[pid] = (d_dis, place)
            state.rest[pid] = max(state.rest.get(pid, 0), d_dis + max(1, (heal - d_dis) // 2))

        # --- police: FIR next morning, from the complainant's own account ---
        if (
            e.type.startswith("hazard.")
            and e.payload.get("participants")
            and float(e.payload.get("severity") or 0) >= FIR_SEVERITY_MIN
            and e.seq not in state.fir_filed
        ):
            state.fir_filed.add(e.seq)
            victim = e.payload["participants"][0]
            person = people.get(victim)
            if person is None:
                continue
            adults = [
                q.id for q in people.values()
                if q.household_id == person.household_id and q.age >= 18 and q.id != victim
            ]
            complainant = min(adults) if adults else victim
            station = block.nearest(e.payload.get("place") or person.home_id, "police")
            claim_key = f"cl:{e.type.split('.', 1)[1]}:{e.payload.get('place')}:d{day}"
            holding = info.holdings.get(victim, {}).get(claim_key)
            statement = (
                holding.claim.text if holding
                else f"{person.given} was hurt in a {e.type.split('.')[-1].replace('_', ' ')}."
            )
            d_fir = day + 1
            later(d_fir, TimedEvent(
                d_fir * SECONDS_PER_DAY + 11 * 3600, "police.fir.registered",
                {"complainant": complainant, "victim": victim,
                 "station": station.id if station else None,
                 "about_seq": e.seq, "statement": statement},
                e.seq,
            ))
            later(day + 8, TimedEvent(
                (day + 8) * SECONDS_PER_DAY + 12 * 3600, "fir.update",
                {"about_seq": e.seq, "victim": victim, "status": "investigation pending"},
                e.seq,
            ))
    return pending


def daily_finance_tick(
    state: ProcState,
    day: int,
    people: dict[str, Person],
    log_events_today: list,
) -> tuple[list[tuple[TimedEvent, int | None]], dict[str, float]]:
    """Earn, spend, pay bills, borrow when short, accrue interest.
    Returns ([(event, caused_by)] to commit now, {person: p_financial})."""
    out: list[tuple[TimedEvent, int | None]] = []
    worked = {
        e.payload.get("person")
        for e in log_events_today
        if e.type == "activity.start" and e.payload.get("activity") in ("work", "driving_rounds")
    }
    t_night = (day + 1) * SECONDS_PER_DAY - 600

    by_hh: dict[str, list[Person]] = {}
    for p in people.values():
        by_hh.setdefault(p.household_id, []).append(p)

    for hid in sorted(state.finances):
        f = state.finances[hid]
        earned = 0.0
        for p in by_hh.get(hid, []):
            income = MONTHLY_INCOME.get(p.occupation, 0)
            if income <= 0:
                continue
            daily = income / WORK_DAYS_PER_MONTH
            from ..engine import DAILY_WAGE  # single source for the wage split

            if p.occupation in DAILY_WAGE:
                earned += daily if p.id in worked else 0.0
            else:
                earned += daily
        f.liquid += earned - f.monthly_costs / 30.0

        for e in log_events_today:
            if e.type == "hospital.discharged" and e.payload.get("household") == hid:
                bill = float(e.payload.get("bill") or 0)
                f.liquid -= bill
                out.append((TimedEvent(
                    t_night - 60, "money.paid",
                    {"household": hid, "amount": bill, "reason": "hospital bill"}, e.seq,
                ), e.seq))
                if f.liquid < 0:
                    principal = float(round(-f.liquid + 2000, -2))
                    f.liquid += principal
                    f.loans += principal
                    out.append((TimedEvent(
                        t_night - 30, "loan.taken",
                        {"household": hid, "principal": principal,
                         "lender": "org:moneylender", "monthly_rate": LOAN_MONTHLY_RATE},
                        e.seq,
                    ), e.seq))

        if f.loans > 0 and day and day % 30 == 0:
            interest = round(f.loans * LOAN_MONTHLY_RATE, -1)
            f.loans += interest
            out.append((TimedEvent(
                t_night, "loan.interest",
                {"household": hid, "amount": interest, "outstanding": round(f.loans)}, None,
            ), None))

    p_fin: dict[str, float] = {}
    for p in people.values():
        f = state.finances.get(p.household_id)
        if f is not None and p.age >= 18:
            p_fin[p.id] = p_financial(f)
    return out, p_fin
