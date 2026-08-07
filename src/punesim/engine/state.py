from dataclasses import dataclass, field

from ..institutions import procedures as proc_mod
from ..kernel.attention import AttentionField
from ..kernel.facts import Canon, PredicateRegistry
from ..minds import info as info_mod
from ..world import unrest as unrest_mod
from ..world.schedule import TimedEvent

REACTION_DELAY_S = 35 * 60  # the family reacts ~35 min after the event (post phone call)

# --- V1 pressure integrators (03-cognition §1.2, two of six) ----------------
DAILY_WAGE = {"rickshaw_driver", "domestic_worker", "shop_assistant", "tailor", "cook"}
NO_WORK = {"homemaker", "retired", "infant"}
P_THRESHOLD = 0.6
HYSTERESIS = 0.15
HYSTERESIS_DAYS = 20
GATE_BURST = 3  # a big day may render 3x k scenes; beyond that it is a budget hole
_ROUTINE_TYPES = ("trip.start", "trip.end", "activity.start")

# (sim_time, person_key, type, event, provenance)
_Timed = tuple[int, str, str, TimedEvent, str]
_ORDER = {"trip.end": 0, "activity.start": 1, "trip.start": 2}


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
    sheltered: set = field(default_factory=set)  # today's curfew-bound, pre-scene
