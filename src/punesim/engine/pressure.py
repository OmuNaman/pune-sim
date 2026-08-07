from ..institutions import procedures as proc_mod
from ..kernel.log import EventIn
from ..kernel.timebase import SECONDS_PER_DAY
from ..population.synth import Person
from .state import DAILY_WAGE, HYSTERESIS, HYSTERESIS_DAYS, NO_WORK, P_THRESHOLD, SimState


def _pressure_tick(
    state: SimState,
    people: dict[str, Person],
    hh_of_person: dict[str, str],
    hh_members: dict[str, tuple[str, ...]],
    day: int,
    today: list,
    p_fin_override: dict[str, float] | None = None,
) -> tuple[list[EventIn], dict[str, str]]:
    """E2 lane: two integrators, vectorless V1 arithmetic. Injury raises
    p_health; a missed work day (or a household admission's bills) raises
    p_financial — daily-wage occupations feel it hardest. Upward crossings of
    the hysteresis threshold emit pressure.crossed and gate tomorrow's scene."""
    absent, admitted, injured = set(), set(), {}
    for e in today:
        pl = e.payload
        if e.type == "activity.start" and pl.get("activity") in proc_mod.ABSENT_ACTIVITIES:
            absent.add(pl.get("person"))  # absence is the observable thing, not work
        elif e.type == "hospital.admitted":
            admitted.add(pl.get("person"))
        elif e.type == "condition.set" and pl.get("kind") == "injury":
            injured[pl.get("entity_id")] = float(pl.get("intensity") or 0.5)
    absent |= admitted
    events: list[EventIn] = []
    marks: dict[str, str] = {}
    t_tick = (day + 1) * SECONDS_PER_DAY - 300
    for pid in sorted(people):
        p = people[pid]
        pr = state.pressures.setdefault(pid, {"p_health": 0.1, "p_financial": 0.2})
        before = dict(pr)
        if pid in injured:
            pr["p_health"] = min(1.0, max(pr["p_health"], 0.3 + 0.6 * injured[pid]))
        else:
            pr["p_health"] = 0.1 + (pr["p_health"] - 0.1) * 0.96
        if p_fin_override is not None:
            if pid in p_fin_override:  # V2: the ledger is the truth
                pr["p_financial"] = p_fin_override[pid]
        elif p.occupation not in NO_WORK and p.occupation != "student" and p.age >= 18:
            hh_admitted = any(
                q in admitted for q in hh_members.get(hh_of_person.get(pid, ""), ())
            )
            bump = 0.0
            if pid in absent:
                bump += 0.09 if p.occupation in DAILY_WAGE else 0.04
            if hh_admitted:
                bump += 0.05  # hospital bills land on the household
            pr["p_financial"] = min(1.0, 0.2 + (pr["p_financial"] - 0.2) * 0.985 + bump)
        for dim in ("p_health", "p_financial"):
            fired_day = state.fired.get((pid, dim))
            th = P_THRESHOLD + (
                HYSTERESIS if fired_day is not None and day - fired_day < HYSTERESIS_DAYS else 0.0
            )
            if before[dim] < th <= pr[dim]:
                state.fired[(pid, dim)] = day
                events.append(
                    EventIn(type="pressure.crossed", sim_time=t_tick,
                            payload={"person": pid, "pressure": dim, "value": round(pr[dim], 3)},
                            provenance="clockwork")
                )
                hid = hh_of_person.get(pid)
                if hid:
                    marks[hid] = "pressure"
    return events, marks
