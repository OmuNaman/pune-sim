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
from . import interpreter
from .catalog import CATALOG

# ₹/month, rough Kasba texture (finances-lite; replaced by real ledgers at V3)
MONTHLY_INCOME = {
    "shopkeeper": 32000, "office_clerk": 28000, "shop_assistant": 14000,
    "tailor": 16000, "rickshaw_driver": 18000, "teacher": 30000,
    "domestic_worker": 10000, "cook": 15000, "nurse": 26000,
    "bank_clerk": 32000, "police_constable": 30000, "doctor": 70000,
    "priest": 12000, "student": 0, "homemaker": 0, "retired": 8000, "infant": 0,
}
WORK_DAYS_PER_MONTH = 26

# Household spending. Anchored on HCES 2023-24 (MoSPI): all-India URBAN MPCE
# ₹6,996/person/month, Maharashtra above the national average; carried to 2026
# at ~8%/yr nominal and adjusted DOWN for the old city, which is poorer than
# Pune's average. The fixed term is the part that does not shrink with family
# size (rent, power, water) — which is why living alone is dear per head.
# provenance=estimate; see docs/subsystems for the anchor discipline.
COST_FIXED = 4500.0
COST_PER_HEAD = 5200.0
# A PG/hostel student is not destitute — their money comes from home. Without
# this, every student household ran a pure deficit and starved on schedule.
PG_REMITTANCE = (9000.0, 14000.0)
LOAN_MONTHLY_RATE = 0.03  # the moneylender's rate; banks come with V3

# Evidence that a workday was LOST. Everything else counts as worked.
#
# The inverse rule (a whitelist of "worked" activity strings) was a one-way
# ratchet: half the block has no fixed workplace, and a scene that narrates
# someone going to work writes free text ("leave for two housecleaning jobs"),
# which no whitelist matches. Absence is the rare, observable thing — so
# absence is what we detect.
ABSENT_ACTIVITIES = {"admitted", "rest_at_home", "shelters_at_home", "stays_home"}


def absent_today(
    state: "ProcState",
    day: int,
    log_events_today: list,
    extra: set[str] | None = None,
    people: dict[str, Person] | None = None,
) -> set[str]:
    """Who demonstrably did not work today: in hospital, convalescing at home,
    sheltering under a curfew, or keeping away from somewhere they fear.

    `extra` carries absences the log cannot show. A household whose scene
    re-planned the day writes its own free-text activities, so a spotlit person
    under curfew never emits `shelters_at_home` — they lost the wage all the
    same, and the engine passes that set in directly."""
    out = {
        e.payload.get("person")
        for e in log_events_today
        if (e.type == "activity.start" and e.payload.get("activity") in ABSENT_ACTIVITIES)
        or e.type == "hospital.admitted"
    }
    out |= {pid for pid, (until, _) in state.in_hospital.items() if day < until}
    out |= {pid for pid, until in state.rest.items() if day < until}
    out |= extra or set()
    # And the one signal no wording can hide: a person with a workplace who
    # never left the house. A scene that narrates someone staying in writes
    # free text ("stays home, calls the school") that no list will ever match,
    # but it cannot fake a trip that did not happen.
    if people is not None:
        went_out = {
            e.payload.get("person") for e in log_events_today if e.type == "trip.start"
        }
        for p in people.values():
            if p.work_id and p.age >= 18 and p.id not in went_out:
                out.add(p.id)
    out.discard(None)
    return out


@dataclass
class Finances:
    liquid: float
    monthly_income: float  # earned wages
    monthly_costs: float
    loans: float = 0.0  # outstanding informal principal
    monthly_support: float = 0.0  # remittances — money that arrives without work

    @property
    def monthly_inflow(self) -> float:
        return self.monthly_income + self.monthly_support


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
        costs = COST_FIXED + COST_PER_HEAD * len(hh.member_ids)
        rng = keyed_rng(run_seed, "finance", hh.id, 0, "init")
        support = 0.0
        if hh.template == "pg_students":
            lo, hi = PG_REMITTANCE
            support = float(sum(lo + rng.random() * (hi - lo) for _ in hh.member_ids))
        liquid = max(1500.0, (income + support) * (0.5 + rng.random() * 1.8))
        out[hh.id] = Finances(
            liquid=round(liquid, -2), monthly_income=income, monthly_costs=costs,
            monthly_support=round(support, -2),
        )
    return out


def p_financial(f: Finances) -> float:
    """Closed-form worry: thin cousin of 03-cognition §1.3's sigmoid.
    The runway term has gradient up to 2.5 months of costs saved — a ₹24k
    hospital bill is invisible to the comfortable and a cliff to the poor."""
    runway = 1.0 - f.liquid / (2.5 * f.monthly_costs)
    debt = f.loans / max(f.monthly_inflow, 4000.0)
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
    Returns {future_day: [TimedEvent]} for the engine's pending queue.

    The two procedures this used to spell out by hand now live in
    `catalog.py` as data; see `interpreter.py` for why.
    """
    return interpreter.run(
        CATALOG,
        log_events_today,
        state,
        {
            "run_seed": run_seed, "day": day, "block": block,
            "people": people, "info": info,
            # today's injuries, so a stay's length can answer to how badly hurt
            # somebody is rather than to a coin
            "intensity_today": {
                e.payload.get("entity_id"): float(e.payload.get("intensity") or 0.5)
                for e in log_events_today
                if e.type == "condition.set" and e.payload.get("kind") == "injury"
            },
        },
    )


def daily_finance_tick(
    state: ProcState,
    day: int,
    people: dict[str, Person],
    log_events_today: list,
    extra_absent: set[str] | None = None,
) -> tuple[list[tuple[TimedEvent, int | None]], dict[str, float]]:
    """Earn, spend, pay bills, borrow when short, accrue interest.
    Returns ([(event, caused_by)] to commit now, {person: p_financial})."""
    out: list[tuple[TimedEvent, int | None]] = []
    absent = absent_today(state, day, log_events_today, extra_absent, people)
    t_night = (day + 1) * SECONDS_PER_DAY - 600

    by_hh: dict[str, list[Person]] = {}
    for p in people.values():
        by_hh.setdefault(p.household_id, []).append(p)

    # Today's discharges, indexed by household. This used to be a linear scan of
    # the whole day's log *inside* the per-household loop — 1280 households x
    # 23k events is 29M comparisons a day to find the two or three that matter.
    discharges: dict[str, list] = {}
    for e in log_events_today:
        if e.type == "hospital.discharged":
            discharges.setdefault(e.payload.get("household"), []).append(e)

    from ..engine import DAILY_WAGE  # single source for the wage split

    for hid in sorted(state.finances):
        f = state.finances[hid]
        earned = 0.0
        for p in by_hh.get(hid, []):
            income = MONTHLY_INCOME.get(p.occupation, 0)
            if income <= 0:
                continue
            daily = income / WORK_DAYS_PER_MONTH
            if p.occupation in DAILY_WAGE:
                earned += 0.0 if p.id in absent else daily  # no work, no wage
            else:
                earned += daily  # a salaried month survives a few sick days
        f.liquid += earned + f.monthly_support / 30.0 - f.monthly_costs / 30.0

        for e in discharges.get(hid, ()):
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
