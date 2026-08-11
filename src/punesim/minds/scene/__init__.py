"""V0 morning household scene (T1).

The LLM is the camera and the judge, not the physics: the scene receives the
household card + recent notable events, returns a WorldDelta (law 3 — the one
schema), and everything it says enters the world only through commit() and
assert_facts(). A scene can revise members' day plans; the plan compiler turns
those into ordinary clockwork trips.
"""

from .apply import apply_delta
from .context import (
    _card_lines,
    build_messages,
    build_reaction_messages,
    memory_digest,
    physical_state,
    recent_notable_events,
    witnessed_facts,
)
from .prompt import (
    _ROUTINE_TYPES,
    _SELF_OUTPUT_TYPES,
    PHYSICAL_HEADER,
    REACTION_TASK,
    SCENE_HOUR_S,
    SYSTEM,
    SceneResult,
)
from .render import (
    _RELATIVE_TIME,
    _flatten,
    _humanize,
    _when,
    _who,
    absolutize,
    held_memories,
)
from .run import compile_plan_overrides, run_morning_scenes, run_reaction_scene

__all__ = [
    "PHYSICAL_HEADER",
    "REACTION_TASK",
    "SCENE_HOUR_S",
    "SYSTEM",
    "_RELATIVE_TIME",
    "_ROUTINE_TYPES",
    "_SELF_OUTPUT_TYPES",
    "SceneResult",
    "_card_lines",
    "_flatten",
    "_humanize",
    "_when",
    "_who",
    "absolutize",
    "apply_delta",
    "build_messages",
    "build_reaction_messages",
    "compile_plan_overrides",
    "held_memories",
    "memory_digest",
    "physical_state",
    "recent_notable_events",
    "run_morning_scenes",
    "run_reaction_scene",
    "witnessed_facts",
]
