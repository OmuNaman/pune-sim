from ..kernel.log import EventLog
from ..kernel.timebase import SECONDS_PER_DAY
from ..population.synth import Person
from ..world import unrest as unrest_mod
from ..world.block import Block
from ..world.schedule import TimedEvent
from .injection import Injection
from .state import SimState

# Which hazard classes put a body in an ambulance — and which have an
# institution on the other end that is simply not a medical emergency. Keying
# on the presence of a place instead sent an ambulance to a water cut.
CASUALTY_PREFIXES = ("hazard.road.collision", "hazard.fire.small", "hazard.violence")

# Events that END a trouble. They are place-scoped, so without their own
# percept lane they reach nobody at all -> (predicate, topics) for the claim
# the neighbourhood forms when it notices relief arriving.
RESOLUTION_PREDICATES = {
    "utility.restored": ("restored", ("power",)),
    "utility.tanker_arrived": ("water_tanker", ("water",)),
}


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
