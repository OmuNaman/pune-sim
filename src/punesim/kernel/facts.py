"""Law 2: one fact gate.

`assert_facts()` is the single semantic gate for generated facts. It never
writes tables — it emits fact.established / fact.superseded events into the
log, and `Canon` is a deterministic projection of those events (rebuildable by
folding the log from seq 0). Enforcement encoded here:

- unregistered predicates are rejected (the registry is the generality engine);
- `clockwork_only` predicates (quantitative state) reject any llm* provenance;
- `scene_gated` predicates (att.stance — 08-identity §6) reject llm provenance
  below disclosure tier 1: tier-0 scenes cannot create prejudice;
- cardinality "one" supersedes-not-deletes (canon history is never erased).
"""

from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel

from .log import FACT_ESTABLISHED, FACT_SUPERSEDED, Event, EventIn, EventLog
from .worlddelta import FactAssertion


class PredicateSpec(BaseModel):
    model_config = {"extra": "forbid"}

    name: str
    cardinality: Literal["one", "multi"] = "one"
    mutability: Literal["event_only", "clockwork_only", "scene_gated"] = "event_only"
    sensitivity: Literal["public", "contextual", "latent", "private"] = "public"


class PredicateRegistry:
    def __init__(self, specs: list[PredicateSpec] | None = None):
        self._specs: dict[str, PredicateSpec] = {}
        for s in specs or []:
            self.register(s)

    def register(self, spec: PredicateSpec) -> None:
        self._specs[spec.name] = spec

    def get(self, name: str) -> PredicateSpec | None:
        return self._specs.get(name)


def core_registry() -> PredicateRegistry:
    """The V0 seed registry; grows as data, never as code."""
    return PredicateRegistry(
        [
            PredicateSpec(name="person.name", cardinality="one", mutability="event_only"),
            PredicateSpec(name="person.occupation", cardinality="one", mutability="event_only"),
            PredicateSpec(name="person.age_years", cardinality="one", mutability="clockwork_only"),
            PredicateSpec(name="hh.member", cardinality="multi", mutability="clockwork_only"),
            PredicateSpec(name="fin.balance_band", mutability="clockwork_only", sensitivity="private"),
            PredicateSpec(name="state.injury", mutability="event_only"),
            PredicateSpec(name="pers.trait", cardinality="multi", mutability="event_only"),
            PredicateSpec(name="id.religion", mutability="clockwork_only", sensitivity="contextual"),
            PredicateSpec(name="id.jati_cluster", mutability="clockwork_only", sensitivity="latent"),
            PredicateSpec(name="id.observance", cardinality="multi", mutability="event_only"),
            PredicateSpec(
                name="att.stance",
                cardinality="multi",
                mutability="scene_gated",
                sensitivity="contextual",
            ),
        ]
    )


@dataclass(frozen=True)
class FactRow:
    seq: int
    subject: str
    predicate: str
    value: Any
    provenance: str


class Canon:
    """Projection of fact events; identical whether maintained live or rebuilt
    by folding the log (test_facts asserts this)."""

    def __init__(self) -> None:
        self._rows: dict[tuple[str, str], list[FactRow]] = {}
        self._live: dict[int, bool] = {}

    def apply(self, e: Event) -> None:
        if e.type == FACT_ESTABLISHED:
            p = e.payload
            row = FactRow(e.seq, p["subject"], p["predicate"], p["value"], p["provenance"])
            self._rows.setdefault((row.subject, row.predicate), []).append(row)
            self._live[e.seq] = True
        elif e.type == FACT_SUPERSEDED:
            self._live[e.payload["target_seq"]] = False

    def live(self, subject: str, predicate: str) -> list[FactRow]:
        return [
            r
            for r in self._rows.get((subject, predicate), [])
            if self._live.get(r.seq, False)
        ]

    @classmethod
    def from_log(cls, log: EventLog, *, branch_id: int = 0) -> "Canon":
        c = cls()
        log.fold(c.apply, branch_id=branch_id)
        return c


@dataclass
class AssertResult:
    accepted: list[int] = field(default_factory=list)  # seqs of established facts
    rejected: list[tuple[FactAssertion, str]] = field(default_factory=list)


def assert_facts(
    log: EventLog,
    canon: Canon,
    registry: PredicateRegistry,
    facts: list[FactAssertion],
    *,
    provenance: str,
    sim_time: int,
    disclosure_tier: int = 0,
    caused_by: int | None = None,
) -> AssertResult:
    result = AssertResult()
    is_llm = provenance.startswith("llm")
    for f in facts:
        spec = registry.get(f.predicate)
        if spec is None:
            result.rejected.append((f, "unregistered_predicate"))
            continue
        if spec.mutability == "clockwork_only" and is_llm:
            result.rejected.append((f, "clockwork_only"))
            continue
        if spec.mutability == "scene_gated" and is_llm and disclosure_tier < 1:
            result.rejected.append((f, "requires_disclosure_tier_1"))
            continue

        batch: list[EventIn] = []
        if spec.cardinality == "one":
            for prior in canon.live(f.subject, f.predicate):
                batch.append(
                    EventIn(
                        type=FACT_SUPERSEDED,
                        sim_time=sim_time,
                        payload={"target_seq": prior.seq},
                        caused_by=caused_by,
                        provenance=provenance,
                    )
                )
        batch.append(
            EventIn(
                type=FACT_ESTABLISHED,
                sim_time=sim_time,
                payload={
                    "subject": f.subject,
                    "predicate": f.predicate,
                    "value": f.value,
                    "provenance": provenance,
                },
                caused_by=caused_by,
                provenance=provenance,
            )
        )
        seqs = log.commit(batch)
        for e_in, seq in zip(batch, seqs):
            canon.apply(
                Event(
                    seq=seq,
                    branch_id=0,
                    sim_time=e_in.sim_time,
                    tick=0,
                    type=e_in.type,
                    payload=e_in.payload,
                    caused_by=e_in.caused_by,
                    provenance=e_in.provenance,
                    actor_ref=None,
                )
            )
        result.accepted.append(seqs[-1])
    return result
