"""Law 3: one WorldDelta superset schema (as unioned by §9.1 ruling 1).

Every LLM result — T1 household plan, T2 scene, T3 focal turn, micro update,
adjudication — validates against this one model. Call classes use subsets
(every field defaults empty), so a plan without conditions or a scene without
world_ops is simply a WorldDelta with those lists empty. `extra="forbid"`
everywhere: an unknown field is a schema error, never silently dropped.
"""

from typing import Any, Literal

from pydantic import BaseModel, Field

_CFG = {"extra": "forbid"}


class Participant(BaseModel):
    model_config = _CFG
    entity_id: str
    role: str = "agent"


class EmittedEvent(BaseModel):
    """A child event the LLM proposes; the kernel validates and budgets it."""

    model_config = _CFG
    type: str
    delay_s: int = 0
    severity: float | None = None
    participants: list[Participant] = Field(default_factory=list)
    payload: dict = Field(default_factory=dict)


class ConditionDelta(BaseModel):
    model_config = _CFG
    entity_id: str
    kind: str
    intensity: float = Field(ge=0.0, le=1.0, default=0.5)
    stage: str | None = None
    expected_duration_days: float | None = None
    effects: dict = Field(default_factory=dict)


class FactAssertion(BaseModel):
    model_config = _CFG
    subject: str
    predicate: str
    value: Any


class RelationshipDelta(BaseModel):
    model_config = _CFG
    a: str
    b: str
    dim: str  # trust | warmth | obligation | ...
    delta: float = Field(ge=-1.0, le=1.0)


class MemoryWrite(BaseModel):
    model_config = _CFG
    person_id: str
    salience: float = Field(ge=0.0, le=1.0)
    summary: str


class ProcessOp(BaseModel):
    model_config = _CFG
    op: Literal["create", "advance", "cancel"]
    kind: str
    template_id: str | None = None
    vars: dict = Field(default_factory=dict)


class WorldOp(BaseModel):
    """Structural change: births, new shops, collapsed wadas, metro stops."""

    model_config = _CFG
    op: Literal["entity_create", "entity_modify", "topology_change"]
    payload: dict = Field(default_factory=dict)


class PlanStep(BaseModel):
    model_config = _CFG
    t: int  # sim seconds
    place_ref: str
    activity: str
    mode: str | None = None  # walk | bus | ...


class DayPlan(BaseModel):
    model_config = _CFG
    person_id: str
    steps: list[PlanStep] = Field(default_factory=list)


class Message(BaseModel):
    model_config = _CFG
    sender: str
    recipients: list[str]
    channel: str = "talk"  # talk | phone | whatsapp | ...
    text: str


class Commitment(BaseModel):
    model_config = _CFG
    owner: str  # person or household ref
    kind: str  # obligation | project | promise | ...
    vars: dict = Field(default_factory=dict)


class MoodDelta(BaseModel):
    model_config = _CFG
    person_id: str
    dim: str  # mood | stress | ...
    delta: float = Field(ge=-1.0, le=1.0)


class WorldDelta(BaseModel):
    model_config = _CFG

    narration: str = ""
    transcript: str | None = None
    events: list[EmittedEvent] = Field(default_factory=list)
    conditions: list[ConditionDelta] = Field(default_factory=list)
    canon_facts: list[FactAssertion] = Field(default_factory=list)
    relationship_deltas: list[RelationshipDelta] = Field(default_factory=list)
    memory_writes: list[MemoryWrite] = Field(default_factory=list)
    process_ops: list[ProcessOp] = Field(default_factory=list)
    world_ops: list[WorldOp] = Field(default_factory=list)
    day_plan: list[DayPlan] = Field(default_factory=list)
    messages: list[Message] = Field(default_factory=list)
    commitments: list[Commitment] = Field(default_factory=list)
    mood_deltas: list[MoodDelta] = Field(default_factory=list)
