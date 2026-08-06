"""V0 engine: clockwork days, scenes, injections, stub institutions.

Day pipeline:
  1. T1 morning scenes for spotlit households (06:30, may revise routines);
  2. compile the day: routine templates or scene-revised plans, merged with
     injected events and stub-institution reactions; hospital admissions
     mechanically invalidate the patient's remaining day (plan invalidation);
  3. if an injection touches a household and minds are on, the day SPLITS at
     the moment the family learns: phase A commits, the T2 reaction scene runs
     right there (it can rewrite the rest of the family's day), then phase B
     commits. This is the same-day lane the morning gate cannot provide
     (09-collective-dynamics break B9, V0-thin).

Two runs from the same seed + same cassettes are hash-identical.
"""

from dataclasses import dataclass, field, replace

from .institutions import procedures as proc_mod
from .kernel.attention import AttentionField
from .kernel.facts import Canon, PredicateRegistry, core_registry
from .kernel.log import EventIn, EventLog
from .kernel.timebase import SECONDS_PER_DAY
from .kernel.worlddelta import PlanStep
from .llm.gateway import CassetteMiss, Gateway
from .minds import info as info_mod
from .population.synth import Household, Person
from .world import hazards as hazards_mod
from .world import unrest as unrest_mod
from .world.block import Block
from .world.schedule import TimedEvent, day_events, roaming_worksites

REACTION_DELAY_S = 35 * 60  # the family reacts ~35 min after the event (post phone call)

# --- V1 pressure integrators (03-cognition §1.2, two of six) ----------------
DAILY_WAGE = {"rickshaw_driver", "domestic_worker", "shop_assistant", "tailor", "cook"}
NO_WORK = {"homemaker", "retired", "infant"}
P_THRESHOLD = 0.6
HYSTERESIS = 0.15
HYSTERESIS_DAYS = 20
_ROUTINE_TYPES = ("trip.start", "trip.end", "activity.start")

# (sim_time, person_key, type, event, provenance)
_Timed = tuple[int, str, str, TimedEvent, str]
_ORDER = {"trip.end": 0, "activity.start": 1, "trip.start": 2}


@dataclass(frozen=True)
class Injection:
    """A user-injected event (provenance='user'), V0-structured."""

    day: int
    time_s: int  # seconds since midnight
    type: str
    place: str | None = None
    participants: tuple[str, ...] = ()
    severity: float | None = None
    payload: dict = field(default_factory=dict)

    @classmethod
    def parse(cls, obj: dict) -> "Injection":
        hh, mm = obj["time"].split(":")
        return cls(
            day=int(obj["day"]),
            time_s=int(hh) * 3600 + int(mm) * 60,
            type=obj["type"],
            place=obj.get("place"),
            participants=tuple(obj.get("participants", [])),
            severity=obj.get("severity"),
            payload=obj.get("payload", {}),
        )


# Which hazard classes put a body in an ambulance — and which have an
# institution on the other end that is simply not a medical emergency. Keying
# on the presence of a place instead sent an ambulance to a water cut.
CASUALTY_PREFIXES = ("hazard.road.collision", "hazard.fire.small", "hazard.violence")


def _utility_reactions(
    inj: Injection, t_abs: int, caused_by: int | None = None
) -> list[TimedEvent]:
    """A dry tap or a load-shed is not silent: somebody complains and somebody
    eventually turns it back on. Deterministic in severity — no draws, so law 4
    is untouched — and clamped inside the day so commit order stays honest."""
    out: list[TimedEvent] = []
    sev = float(inj.severity or 0.5)
    day_end = (t_abs // SECONDS_PER_DAY + 1) * SECONDS_PER_DAY - 60
    if inj.type == "hazard.water.supply_cut":
        out.append(TimedEvent(t_abs + 40 * 60, "complaint.registered",
                              {"org": "org:pmc_water", "place": inj.place,
                               "about": inj.type, "severity": sev}, caused_by))
        out.append(TimedEvent(min(t_abs + 3 * 3600, day_end), "utility.tanker_arrived",
                              {"org": "org:pmc_water", "place": inj.place,
                               "loads": 1 + int(sev * 2)}, caused_by))
    elif inj.type == "hazard.power.outage":
        out.append(TimedEvent(t_abs + 15 * 60, "complaint.registered",
                              {"org": "org:mseb", "place": inj.place,
                               "about": inj.type, "severity": sev}, caused_by))
        out.append(TimedEvent(min(t_abs + int(1800 + sev * 4 * 3600), day_end),
                              "utility.restored",
                              {"org": "org:mseb", "place": inj.place,
                               "utility": "power"}, caused_by))
    return out


def stub_institution_reactions(
    inj: Injection, t_abs: int, block: Block, people: dict[str, Person], caused_by: int | None = None
) -> list[TimedEvent]:
    """Hand-rule subscribers (07-interface red-team fixture): scripted
    ambulance/hospital/school reactions until INSTITUTIONS exists (V2).
    Every reaction carries `caused_by` — the consequence cone's lineage."""
    out: list[TimedEvent] = []
    if not inj.type.startswith("hazard."):
        return out
    if not inj.type.startswith(CASUALTY_PREFIXES):
        return _utility_reactions(inj, t_abs, caused_by)
    if inj.place:
        out.append(
            TimedEvent(t_abs + 8 * 60, "ambulance.dispatched",
                       {"place": inj.place, "for": list(inj.participants)}, caused_by)
        )
    for pid in inj.participants:
        person = people.get(pid)
        if person is None:
            continue
        hospital = block.nearest(inj.place or person.home_id, "hospital", "clinic")
        if hospital:
            out.append(
                TimedEvent(t_abs + 25 * 60, "hospital.admitted",
                           {"person": pid, "place": hospital.id}, caused_by)
            )
        out.append(
            TimedEvent(
                t_abs + 5 * 60,
                "condition.set",
                {"entity_id": pid, "kind": "injury", "intensity": inj.severity or 0.5, "stage": "er"},
                caused_by,
            )
        )
        # the school (or workplace) calls home
        family = [
            q.id
            for q in people.values()
            if q.household_id == person.household_id and q.id != pid and q.age >= 18
        ]
        if family:
            caller = person.work_id or "org:unknown"
            wp = block.get(caller)
            who = wp.name if wp and wp.name else "the school"
            out.append(
                TimedEvent(
                    t_abs + 20 * 60,
                    "message.sent",
                    {
                        "sender": caller,
                        "recipients": family,
                        "channel": "phone",
                        "text": f"Call from {who}: {person.given} has been in an accident; taken to hospital.",
                    },
                    caused_by,
                )
            )
    return out


def _compile_override(person: Person, steps: list[PlanStep], block: Block) -> list[TimedEvent]:
    """A scene-revised (part of a) day: walk trips between planned places.
    V0 simplification: the walk starts from home."""
    ev: list[TimedEvent] = []
    current = person.home_id
    for s in steps:
        place = s.place_ref if block.get(s.place_ref) else None
        if place is None:
            ev.append(
                TimedEvent(s.t, "plan.step_dropped", {"person": person.id, "place_ref": s.place_ref})
            )
            continue
        if place != current:
            dur = block.walk_seconds(current, place)
            depart = max(s.t - dur, 0)
            ev.append(
                TimedEvent(depart, "trip.start", {"person": person.id, "from": current, "to": place, "mode": s.mode or "walk", "purpose": s.activity})
            )
            ev.append(TimedEvent(depart + dur, "trip.end", {"person": person.id, "at": place, "purpose": s.activity}))
            current = place
        ev.append(TimedEvent(max(s.t, ev[-1].sim_time if ev else s.t), "activity.start", {"person": person.id, "at": place, "activity": s.activity}))
    return ev


def _compile_day(
    run_seed: int,
    block: Block,
    people: dict[str, Person],
    day: int,
    plan_overrides: dict[str, list[PlanStep]] | None,
    worksites: dict[str, tuple[str, ...]] | None = None,
) -> list[_Timed]:
    overrides = plan_overrides or {}
    sites = worksites or {}
    timed: list[_Timed] = []
    for pid in sorted(people):
        evs = (
            _compile_override(people[pid], overrides[pid], block)
            if pid in overrides
            else day_events(run_seed, people[pid], block, day, sites.get(pid, ()))
        )
        for te in evs:
            timed.append((te.sim_time, pid, te.type, te, "clockwork"))
    return timed


def _apply_admissions(timed: list[_Timed], extra: list[TimedEvent]) -> list[_Timed]:
    """Mechanical plan invalidation: an admitted person's remaining day is
    cancelled and replaced by being at the hospital."""
    admissions = [
        (te.payload["person"], te.sim_time, te.payload["place"])
        for te in extra
        if te.type == "hospital.admitted"
    ]
    for pid, t_adm, place in admissions:
        timed = [x for x in timed if not (x[1] == pid and x[0] > t_adm)]
        te = TimedEvent(t_adm, "activity.start", {"person": pid, "at": place, "activity": "admitted"})
        timed.append((te.sim_time, pid, te.type, te, "clockwork"))
    return timed


def _sorted(timed: list[_Timed]) -> list[_Timed]:
    return sorted(timed, key=lambda x: (x[0], x[1], _ORDER.get(x[2], 9), x[2]))


def _commit(log: EventLog, timed: list[_Timed]) -> int:
    if not timed:
        return 0
    log.commit(
        [
            EventIn(type=ty, sim_time=t, payload=te.payload, provenance=prov, caused_by=te.caused_by)
            for (t, _pid, ty, te, prov) in timed
        ]
    )
    return len(timed)


def run_day(
    log: EventLog,
    run_seed: int,
    block: Block,
    people: dict[str, Person],
    day: int,
    *,
    plan_overrides: dict[str, list[PlanStep]] | None = None,
    extra: list[TimedEvent] | None = None,
    extra_provenance: str = "user",
) -> int:
    """One clockwork day, committed whole (zero-LLM path)."""
    timed = _compile_day(run_seed, block, people, day, plan_overrides)
    timed = _apply_admissions(timed, extra or [])
    for te in extra or []:
        timed.append((te.sim_time, "~injected", te.type, te, extra_provenance))
    return _commit(log, _sorted(timed))


@dataclass
class SimState:
    canon: Canon
    registry: PredicateRegistry
    attention: AttentionField
    info: info_mod.InfoState = field(default_factory=info_mod.InfoState)
    acted: set = field(default_factory=set)  # (person, claim_key) that already fired E5
    avoid: dict = field(default_factory=dict)  # person -> {place: (claim_key, action_seq)}
    morning_acts: dict = field(default_factory=dict)  # person -> [(activity, claim_key, seq)], one-shot
    pressures: dict = field(default_factory=dict)  # person -> {p_health, p_financial}
    fired: dict = field(default_factory=dict)  # (person, pressure) -> day (hysteresis)
    gate_marks: dict = field(default_factory=dict)  # household -> reason for tomorrow's scene
    proc: proc_mod.ProcState = field(default_factory=proc_mod.ProcState)  # V2 institutions
    pending: dict = field(default_factory=dict)  # future_day -> [TimedEvent] (procedure futures)
    unrest: unrest_mod.UnrestState = field(default_factory=unrest_mod.UnrestState)


def _apply_zones(
    block: Block, timed: list[_Timed], people: dict[str, Person], state: SimState,
    day: int, skip: set[str],
) -> list[_Timed]:
    """Active fear/curfew zones keep households home — the shelter side of
    collective dynamics. Essential occupations keep moving; scene plans win."""
    shelters = unrest_mod.zone_shelters(state.unrest, day, block, people)
    shelters = {pid: z for pid, z in shelters.items() if pid not in skip}
    if not shelters:
        return timed
    base = day * SECONDS_PER_DAY
    timed = [x for x in timed if not (x[1] in shelters and x[4] == "clockwork")]
    for pid in sorted(shelters):
        te = TimedEvent(base + 8 * 3600, "activity.start",
                        {"person": pid, "at": people[pid].home_id,
                         "activity": "shelters_at_home", "zone": shelters[pid]})
        timed.append((te.sim_time, pid, te.type, te, "clockwork"))
    return timed


def _unrest_response(
    log: EventLog,
    state: SimState,
    run_seed: int,
    block: Block,
    people: dict[str, Person],
    hh_of_person: dict[str, str],
    inj: Injection,
    day: int,
    t_abs: int,
    inj_seq: int,
    intervals: dict,
    extra: list[TimedEvent],
) -> int:
    """The minimal collective-dynamics instance: threshold mobilization,
    scripted police, a curfew zone. Percepts and gossip about the flashpoint
    flow through the ordinary INFO lane — no riot-specific spread code."""
    severity = float(inj.severity or 0.5)
    crowd = unrest_mod.mobilize(
        run_seed, inj.place or "", t_abs, severity, block, people, intervals
    ) if inj.place else []
    n = 0
    if crowd:
        extra.append(TimedEvent(
            t_abs + 45 * 60, "crowd.gathered",
            {"place": inj.place, "participants": crowd, "size": len(crowd)}, inj_seq,
        ))
        for pid in crowd:
            hid = hh_of_person.get(pid)
            if hid:
                state.gate_marks[hid] = "unrest"
                state.attention.bump(hid, 2.0, tick=day * 288)
    if inj.place and (
        len(crowd) >= unrest_mod.CROWD_FOR_POLICE or severity >= unrest_mod.CURFEW_SEVERITY
    ):  # a curfew without police is a suggestion
        extra.append(TimedEvent(
            t_abs + 75 * 60, "police.deployed",
            {"place": inj.place, "crowd_size": len(crowd)}, inj_seq,
        ))
    if severity >= unrest_mod.CURFEW_SEVERITY and inj.place:
        shelter_days = 1 + int(severity * 2)
        until = day + 1 + shelter_days
        state.unrest.zones.append(unrest_mod.Zone(inj.place, until, severity))
        extra.append(TimedEvent(
            t_abs + 3 * 3600, "curfew.imposed",
            {"place": inj.place, "from_day": day + 1, "until_day": until,
             "radius_m": unrest_mod.ZONE_RADIUS_M}, inj_seq,
        ))
    return n


def _apply_stays(
    timed: list[_Timed], people: dict[str, Person], state: SimState, day: int, skip: set[str]
) -> list[_Timed]:
    """Hospital stays and convalescence bend the clockwork: an admitted person
    spends the day in the ward; a discharged one rests at home until fit.
    Scene-revised plans (skip) win, as everywhere."""
    base = day * SECONDS_PER_DAY
    replaced: dict[str, TimedEvent] = {}
    for pid in sorted(state.proc.in_hospital):
        until, place = state.proc.in_hospital[pid]
        if day < until and pid in people and pid not in skip:
            replaced[pid] = TimedEvent(
                base + 8 * 3600, "activity.start",
                {"person": pid, "at": place or people[pid].home_id, "activity": "admitted"},
            )
    for pid in sorted(state.proc.rest):
        if pid in replaced or pid in skip or pid not in people:
            continue
        if day < state.proc.rest[pid] and day >= state.proc.in_hospital.get(pid, (0, ""))[0]:
            replaced[pid] = TimedEvent(
                base + 8 * 3600, "activity.start",
                {"person": pid, "at": people[pid].home_id, "activity": "rest_at_home"},
            )
    if not replaced:
        return timed
    timed = [x for x in timed if not (x[1] in replaced and x[4] == "clockwork")]
    for pid in sorted(replaced):
        te = replaced[pid]
        timed.append((te.sim_time, pid, te.type, te, "clockwork"))
    return timed


def _apply_beliefs(
    timed: list[_Timed], people: dict[str, Person], state: SimState, day: int, skip: set[str]
) -> list[_Timed]:
    """Mechanical E5 behavior for un-spotlit people: a believer whose routine
    touches an avoided place stays home instead (V1 ruling: the whole clockwork
    day drops — errand days ARE the visit, and a feared workplace keeps you
    home), plus one-shot morning acts (store_water). Scene-revised plans win —
    persons in `skip` are untouched."""
    base = day * SECONDS_PER_DAY
    dropped: dict[str, tuple[str, str, int, int]] = {}  # pid -> (place, claim_key, seq, t_dep)
    for pid in sorted(state.avoid):
        if pid in skip or pid not in people:
            continue
        places = state.avoid[pid]
        for t, epid, _ty, te, prov in timed:
            if epid != pid or prov != "clockwork":
                continue
            hit = next((te.payload[k] for k in ("to", "at") if te.payload.get(k) in places), None)
            if hit is not None:
                claim_key, seq = places[hit]
                dropped[pid] = (hit, claim_key, seq, t)
                break
    if dropped:
        timed = [x for x in timed if not (x[1] in dropped and x[4] == "clockwork")]
        for pid in sorted(dropped):
            place, claim_key, seq, t_dep = dropped[pid]
            for te in (
                TimedEvent(t_dep, "plan.avoided",
                           {"person": pid, "place": place, "claim_key": claim_key,
                            "activity": "stays_home"}, seq),
                TimedEvent(t_dep, "activity.start",
                           {"person": pid, "at": people[pid].home_id, "activity": "stays_home"}, seq),
            ):
                timed.append((te.sim_time, pid, te.type, te, "clockwork"))
    for pid in sorted(state.morning_acts):
        if pid in skip or pid not in people:
            continue
        for activity, _claim_key, seq in state.morning_acts[pid]:
            te = TimedEvent(base + int(6.75 * 3600), "activity.start",
                            {"person": pid, "at": people[pid].home_id, "activity": activity}, seq)
            timed.append((te.sim_time, pid, te.type, te, "clockwork"))
    state.morning_acts.clear()
    return timed


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


def _info_pass(
    log: EventLog,
    state: SimState,
    run_seed: int,
    block: Block,
    people: dict[str, Person],
    hh_members: dict[str, tuple[str, ...]],
    hh_of_person: dict[str, str],
    day: int,
) -> tuple[dict[str, str], int]:
    """Post-commit INFO lane (E5 + E7 percepts): witnesses of today's hazards
    receive tiered percepts, the day's co-presence propagates every held claim
    mechanically, and credence crossings become belief.action events that
    change tomorrow's plans. Runs on COMMITTED movements, so reaction-scene
    rewrites are already reflected. Returns (gate marks, heard count)."""
    day0, day1 = day * SECONDS_PER_DAY, (day + 1) * SECONDS_PER_DAY
    today = [e for e in log.events() if day0 <= e.sim_time < day1]
    routine = [
        (e.sim_time, e.payload.get("person"), e.type, e.payload)
        for e in today
        if e.type in _ROUTINE_TYPES
    ]
    intervals = info_mod.presence_intervals(routine, people, day)

    def commit_heard(h: info_mod.Heard) -> int:
        return log.commit(
            [
                EventIn(
                    type="info.heard", sim_time=h.sim_time,
                    payload={
                        "person": h.person, "claim_key": h.claim.key,
                        "claim": h.claim.to_payload(), "source": h.source,
                        "channel": h.channel, "credence": h.credence,
                        "lineage": list(h.lineage),  # the mouths it came through
                    },
                    caused_by=h.caused_by, provenance="clockwork",
                )
            ]
        )[0]

    by_type = {c[0]: c for c in hazards_mod.CLASSES}
    for e in today:
        # percept sources: any hazard, plus any user-injected PLACED event that
        # is not itself information — a public assassination, a procession, a
        # collapse all ripple through the same witness->gossip lane with zero
        # event-specific code (novelty ladder, architecture §9.4). Roots only:
        # consequence events (ambulance, admission) carry caused_by and must
        # not seed their own claims.
        is_hazard = e.type.startswith("hazard.")
        is_public = e.provenance == "user" and e.caused_by is None and not e.type.startswith("info.")
        if not (is_hazard or is_public) or not e.payload.get("place"):
            continue
        cls = by_type.get(e.type)
        shape = cls[3] if cls else "point"
        predicate = cls[4] if cls else e.type.rsplit(".", 1)[-1]
        topics = cls[5] if cls else ("safety",)
        if cls:
            charge = cls[6]
        else:
            sev = e.payload.get("severity")
            charge = min(1.0, 0.45 + 0.5 * float(sev)) if sev is not None else 0.7
        participants = tuple(e.payload.get("participants") or [])
        qty = float(len(participants)) if participants else None
        claim = hazards_mod.hazard_claim(
            e.type, e.payload["place"], day, predicate, topics, charge, block, qty
        )
        holders = [(pid, 0.9) for pid in participants if pid in people]
        holders += hazards_mod.witness_tiers(
            e.payload["place"], e.sim_time, shape, block, people, intervals, exclude=participants
        )
        for pid, spec in sorted(holders):
            if claim.key in state.info.holdings.get(pid, {}):
                continue
            variant = replace(claim, specificity=spec)
            variant = replace(variant, text=info_mod.render_text(variant, block))
            credence = hazards_mod.witness_credence(spec)
            heard = info_mod.Heard(e.sim_time + 300, pid, variant, "witness", "witness", credence, e.seq)
            seq = commit_heard(heard)
            state.info.hear(
                pid, variant, credence, day, seq, source="witness",
                t_abs=heard.sim_time, channel="witness",
            )

    heard = info_mod.propagate_day(
        state.info, run_seed, day, block, people, intervals, hh_members, commit_heard
    )

    marks: dict[str, str] = {}
    for act in info_mod.crossed_actions(state.info, state.acted):
        state.acted.add((act.person, act.claim_key))
        holding = state.info.holdings[act.person][act.claim_key]
        seq = log.commit(
            [
                EventIn(
                    type="belief.action", sim_time=day1 - 7200,
                    payload={
                        "person": act.person, "claim_key": act.claim_key,
                        "action": act.action, "place": act.place,
                        "credence": round(holding.credence, 3),
                    },
                    caused_by=act.caused_by, provenance="clockwork",
                )
            ]
        )[0]
        state.avoid.setdefault(act.person, {})[act.place] = (act.claim_key, seq)
        if act.action == "store_water":
            state.morning_acts.setdefault(act.person, []).append(("store_water", act.claim_key, seq))
        hid = hh_of_person.get(act.person)
        if hid:
            marks[hid] = "info"
    return marks, len(heard)


def run_simulation(
    log: EventLog,
    run_seed: int,
    block: Block,
    households: list[Household],
    people: dict[str, Person],
    *,
    days: int = 1,
    start_day: int = 0,
    gateway: Gateway | None = None,
    scenes_k: int = 5,
    scene_gate_mode: str = "spotlight",
    injections: list[Injection] | None = None,
    hazards: bool = False,
    follow: tuple[str, ...] = (),
) -> tuple[int, SimState]:
    """The V1 day pipeline: gated scenes -> compile (belief-bent) -> injections
    + sampled hazards -> split-commit with reaction scenes -> INFO propagation
    -> pressure tick. Returns (total events, final state)."""
    from .minds.scene import compile_plan_overrides, run_morning_scenes, run_reaction_scene

    state = SimState(canon=Canon(), registry=core_registry(), attention=AttentionField())
    state.proc.finances = proc_mod.init_finances(run_seed, households, people)
    for p in people.values():  # a poor family STARTS worried — being born poor
        f = state.proc.finances.get(p.household_id)  # is not an E2 event
        if f is not None and p.age >= 18:
            state.pressures[p.id] = {"p_health": 0.1, "p_financial": proc_mod.p_financial(f)}
    total = 0
    if start_day == 0:  # self-describing log: a db alone is enough to branch it
        log.commit([EventIn(
            type="run.meta", sim_time=0,
            payload={"seed": run_seed, "households": len(households), "days": days},
            provenance="system",
        )])
        total += 1
    hh_of_person = {p.id: p.household_id for p in people.values()}
    hh_by_id = {h.id: h for h in households}
    hh_members = {h.id: h.member_ids for h in households}
    worksites = roaming_worksites(run_seed, block, people)
    for ref in follow:  # a followed family renders every day, whatever else happens
        hid = ref if ref in hh_by_id else hh_of_person.get(ref)
        if hid is None:
            raise ValueError(f"--follow: no such household or person: {ref}")
        state.attention.set_focus(hid, 10.0)

    for day in range(start_day, start_day + days):
        # 1. T1 morning scenes — routine-bypass gate: households marked by
        #    yesterday's E2/E5/E7 lanes always render; attention fills to k
        overrides: dict[str, list[PlanStep]] = {}
        if gateway is not None and scenes_k > 0:
            all_ids = [h.id for h in households]
            if scene_gate_mode == "all":
                chosen = all_ids
            else:
                focused = [h for h in state.attention.focused() if h in hh_by_id]
                gated = [
                    h for h in sorted(state.gate_marks)
                    if h in hh_by_id and h not in focused
                ]
                fill = [
                    h for h in state.attention.top_k(all_ids, scenes_k, tick=day * 288, day=day)
                    if h not in gated and h not in focused
                ]
                chosen = (focused + gated + fill)[: max(scenes_k, len(focused) + len(gated))]
            results = run_morning_scenes(
                log, gateway, state.canon, state.registry, block, households, people, day,
                chosen_ids=chosen,
            )
            for hid in chosen:  # a spent slot counts, skipped scenes included
                state.attention.mark_rendered(hid, day)
            overrides = compile_plan_overrides(results, people, day)
            total += len(results)
        state.gate_marks.clear()

        # 2. compile the day (scene plans win; beliefs, bodies and fear zones
        #    bend the rest)
        timed = _compile_day(run_seed, block, people, day, overrides, worksites)
        timed = _apply_beliefs(timed, people, state, day, skip=set(overrides))
        timed = _apply_stays(timed, people, state, day, skip=set(overrides))
        timed = _apply_zones(block, timed, people, state, day, skip=set(overrides))
        pre_routine = [
            (t, pid, ty, te.payload)
            for (t, pid, ty, te, prov) in timed
            if prov == "clockwork" and ty in _ROUTINE_TYPES
        ]
        pre_intervals = info_mod.presence_intervals(pre_routine, people, day)

        # 3. injections (committed now, AFTER morning scenes — a 06:30 scene
        #    must not see a 07:20 event) + stub reactions + reaction triggers
        extra: list[TimedEvent] = []  # user-injection consequences
        extra_cw: list[TimedEvent] = []  # clockwork-hazard consequences
        reactions: dict[str, int] = {}  # household -> t_react (abs)
        for inj in injections or []:
            if inj.day != day:
                continue
            t_abs = day * SECONDS_PER_DAY + inj.time_s
            inj_seq = log.commit(
                [
                    EventIn(
                        type=inj.type,
                        sim_time=t_abs,
                        payload={
                            "place": inj.place,
                            "participants": list(inj.participants),
                            "severity": inj.severity,
                            **inj.payload,
                        },
                        provenance="user",
                    )
                ]
            )[0]
            total += 1
            if inj.type.startswith("info."):
                total += _seed_rumor(log, state, run_seed, block, inj, day, t_abs, inj_seq)
                continue  # a rumor propagates through the INFO lane, not sirens
            if inj.type.startswith("unrest."):
                total += _unrest_response(
                    log, state, run_seed, block, people, hh_of_person,
                    inj, day, t_abs, inj_seq, pre_intervals, extra,
                )
                continue  # collective dynamics, not an ambulance
            extra.extend(stub_institution_reactions(inj, t_abs, block, people, caused_by=inj_seq))
            for pid in inj.participants:
                hid = hh_of_person.get(pid)
                if hid is None:
                    continue
                state.attention.bump(hid, 5.0, tick=day * 288)
                state.gate_marks[hid] = "hazard"  # a bump alone decays overnight
                if gateway is not None:
                    reactions[hid] = max(reactions.get(hid, 0), t_abs + REACTION_DELAY_S)

        # 4. un-injected trouble: sampled hazards ride the same machinery (E7)
        if hazards:
            for hz in hazards_mod.sample_day(run_seed, day, block, people, pre_intervals):
                hz_seq = log.commit(
                    [
                        EventIn(
                            type=hz.type, sim_time=hz.t_abs,
                            payload={
                                "place": hz.place,
                                "participants": list(hz.participants),
                                "severity": hz.severity,
                            },
                            provenance="clockwork",
                        )
                    ]
                )[0]
                total += 1
                hz_inj = Injection(
                    day=day, time_s=hz.t_abs - day * SECONDS_PER_DAY, type=hz.type,
                    place=hz.place, participants=hz.participants, severity=hz.severity,
                )
                extra_cw.extend(
                    stub_institution_reactions(hz_inj, hz.t_abs, block, people, caused_by=hz_seq)
                )
                for pid in hz.participants:
                    hid = hh_of_person.get(pid)
                    if hid is None:
                        continue
                    state.attention.bump(hid, 3.0, tick=day * 288)
                    state.gate_marks[hid] = "hazard"  # tomorrow's scene, guaranteed
                    if gateway is not None:
                        reactions[hid] = max(reactions.get(hid, 0), hz.t_abs + REACTION_DELAY_S)

        # 5. the institutions' scheduled futures land today (discharges, FIRs,
        #    healing stages), then invalidate + commit (split on reactions)
        extra_cw.extend(state.pending.pop(day, []))
        timed = _apply_admissions(timed, extra + extra_cw)
        for te in extra:
            timed.append((te.sim_time, "~injected", te.type, te, "user"))
        for te in extra_cw:
            timed.append((te.sim_time, "~hazard", te.type, te, "clockwork"))
        timed = _sorted(timed)

        if reactions:
            t_split = min(reactions.values())
            total += _commit(log, [x for x in timed if x[0] < t_split])
            rest = [x for x in timed if x[0] >= t_split]
            reaction_results = []
            for hid in sorted(reactions):
                try:
                    reaction_results.append(
                        run_reaction_scene(
                            log, gateway, state.canon, state.registry, block,
                            hh_by_id[hid], people, day, now_abs=reactions[hid],
                        )
                    )
                except CassetteMiss:
                    raise  # replay integrity is law 1
                except Exception as err:  # noqa: BLE001 — skip loudly, the day goes on
                    log.commit([EventIn(
                        type="scene.skipped", sim_time=reactions[hid],
                        payload={"household": hid, "reason": f"{type(err).__name__}: {err}"[:200]},
                        provenance="system",
                    )])
            total += len(reaction_results)
            rest_over = compile_plan_overrides(reaction_results, people, day)
            for pid, steps in rest_over.items():
                person = people.get(pid)
                if person is None:
                    continue
                rest = [x for x in rest if not (x[1] == pid and x[4] == "clockwork")]
                clamped = [
                    PlanStep(t=max(s.t, t_split), place_ref=s.place_ref, activity=s.activity, mode=s.mode)
                    for s in steps
                ]
                for te in _compile_override(person, clamped, block):
                    rest.append((max(te.sim_time, t_split), pid, te.type, te, "clockwork"))
            total += _commit(log, _sorted(rest))
        else:
            total += _commit(log, timed)

        # 6. INFO lane (E5/E7): witness percepts, mechanical propagation over
        #    the day's COMMITTED movements, belief-action crossings
        info_marks, n_heard = _info_pass(
            log, state, run_seed, block, people, hh_members, hh_of_person, day
        )
        total += n_heard
        state.gate_marks.update(info_marks)

        # 7. V2 institutions: procedures schedule their futures, money moves
        day0, day1 = day * SECONDS_PER_DAY, (day + 1) * SECONDS_PER_DAY
        today = [e for e in log.events() if day0 <= e.sim_time < day1]
        new_pending = proc_mod.step(today, state.proc, run_seed, day, block, people, state.info)
        for d, tes in new_pending.items():
            state.pending.setdefault(d, []).extend(tes)
        fin_events, p_fin = proc_mod.daily_finance_tick(state.proc, day, people, today)
        for te, caused_by in fin_events:
            log.commit([EventIn(type=te.type, sim_time=te.sim_time, payload=te.payload,
                                caused_by=caused_by, provenance="clockwork")])
            total += 1

        # 8. E2: nightly pressure tick with hysteresis (p_financial now reads
        #    the real ledger instead of bump arithmetic)
        p_events, p_marks = _pressure_tick(
            state, people, hh_of_person, hh_members, day, today, p_fin_override=p_fin
        )
        if p_events:
            total += len(log.commit(p_events))
        state.gate_marks.update(p_marks)
        for hid in {**info_marks, **p_marks}:
            state.attention.bump(hid, 1.5, tick=day * 288 + 287)
    return total, state


def _seed_rumor(
    log: EventLog,
    state: SimState,
    run_seed: int,
    block: Block,
    inj: Injection,
    day: int,
    t_abs: int,
    inj_seq: int,
) -> int:
    """An injected rumor (type info.*): participants are the first hearers;
    everything after that is the mechanical INFO lane. The claim is pure data —
    a novel rumor needs zero new engine code (novelty ladder, §9.4)."""
    p = dict(inj.payload.get("claim") or {})
    p.setdefault("key", f"cl:injected:d{day}")
    p.setdefault("subject", inj.place or "")
    p.setdefault("predicate", "dangerous")
    p.setdefault("text", "")
    claim = info_mod.Claim.from_payload(p)
    if not claim.text:
        claim = replace(claim, text=info_mod.render_text(claim, block))
    n = 0
    for pid in inj.participants:
        credence = float(
            inj.payload.get("credence")
            or info_mod.update_credence(
                info_mod.PRIOR_CREDENCE, "f2f", 0,
                info_mod.traits(run_seed, pid).credulity, claim.charge,
            )
        )
        seq = log.commit(
            [
                EventIn(
                    type="info.heard", sim_time=t_abs,
                    payload={
                        "person": pid, "claim_key": claim.key,
                        "claim": claim.to_payload(), "source": "origin",
                        "channel": "f2f", "credence": round(credence, 3),
                        "lineage": [],
                    },
                    caused_by=inj_seq, provenance="clockwork",
                )
            ]
        )[0]
        state.info.hear(pid, claim, credence, day, seq, source="origin", t_abs=t_abs, channel="f2f")
        n += 1
    return n


def run_days(
    log: EventLog,
    run_seed: int,
    block: Block,
    people: dict[str, Person],
    *,
    days: int = 1,
    start_day: int = 0,
) -> int:
    """Zero-LLM compatibility path (tests, `punesim run` without --scenes)."""
    total = 0
    for day in range(start_day, start_day + days):
        total += run_day(log, run_seed, block, people, day)
    return total
