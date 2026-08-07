from ...kernel.facts import Canon, PredicateRegistry, assert_facts
from ...kernel.log import EventIn, EventLog
from ...kernel.worlddelta import WorldDelta
from ...population.synth import Person
from .render import _flatten, absolutize


def apply_delta(
    log: EventLog,
    canon: Canon,
    registry: PredicateRegistry,
    delta: WorldDelta,
    *,
    household_id: str,
    sim_time: int,
    disclosure_tier: int = 0,
    event_type: str = "scene.morning",
    people: dict[str, Person] | None = None,
    prior_memories: set[str] | None = None,
) -> int:
    """Commit the scene and its consequences; returns the scene event seq."""
    # Referential integrity at the gate. Nothing validated the person ids a
    # scene returned, and the soak quietly accumulated messages addressed to
    # `person:colleague_yogita`, `person:Vinayak Mane` and `person:neighbor` —
    # people who do not exist. A dangling id is dropped and recorded, never
    # committed: the registry is canon, and a scene does not get to extend it.
    dropped: list[str] = []
    repeated: list[str] = []

    def canonical(pid: str | None) -> str:
        """The model writes both `person:002.1` and a bare `002.1`. Committing
        the bare form means the receiving household's own prompt never matches
        it, so an inbound message quietly reaches nobody."""
        if not pid:
            return pid or ""
        return pid if ":" in pid else f"person:{pid}"

    def known(pid: str | None) -> bool:
        if people is None or not pid:
            return True
        norm = canonical(pid)
        if norm in people or norm.startswith(("place:", "home:", "org:")):
            return True
        dropped.append(pid)
        return False

    scene_seq = log.commit(
        [
            EventIn(
                type=event_type,
                sim_time=sim_time,
                payload={
                    "household": household_id,
                    "narration": delta.narration,
                    "transcript": delta.transcript or "",
                },
                provenance="llm_scene",
            )
        ]
    )[0]

    batch: list[EventIn] = []
    already = prior_memories or set()
    for m in delta.memory_writes:
        if not known(m.person_id):
            continue
        m = m.model_copy(update={"person_id": canonical(m.person_id)})
        # A memory already held is not a new memory. The prompt asks for this
        # and the model mostly complies, but a life made of the same three
        # remembered incidents is the failure mode worth a hard guarantee.
        if (m.person_id, _flatten(m.summary)) in already:
            repeated.append(m.person_id)
            continue
        batch.append(
            EventIn(
                type="memory.formed",
                sim_time=sim_time,
                payload={"person": m.person_id, "salience": m.salience,
                         "summary": absolutize(m.summary, sim_time),
                         "summary_key": _flatten(m.summary)},
                caused_by=scene_seq,
                provenance="llm_scene",
            )
        )
    for md in delta.mood_deltas:
        if not known(md.person_id):
            continue
        batch.append(
            EventIn(
                type="mood.delta",
                sim_time=sim_time,
                payload={"person": canonical(md.person_id), "dim": md.dim, "delta": md.delta},
                caused_by=scene_seq,
                provenance="llm_scene",
            )
        )
    for msg in delta.messages:
        recipients = [canonical(r) for r in msg.recipients if known(r)]
        if not known(msg.sender) or (msg.recipients and not recipients):
            continue
        batch.append(
            EventIn(
                type="message.sent",
                sim_time=sim_time,
                payload={
                    "sender": canonical(msg.sender),
                    "recipients": recipients,
                    "channel": msg.channel,
                    "text": msg.text,
                },
                caused_by=scene_seq,
                provenance="llm_scene",
            )
        )
    for ev in delta.events:
        batch.append(
            EventIn(
                type=ev.type,
                sim_time=sim_time + max(0, ev.delay_s),
                payload={
                    **ev.payload,
                    "participants": [p.entity_id for p in ev.participants],
                    "severity": ev.severity,
                },
                caused_by=scene_seq,
                provenance="llm_scene",
            )
        )
    for c in delta.conditions:
        if not known(c.entity_id):
            continue
        batch.append(
            EventIn(
                type="condition.set",
                sim_time=sim_time,
                payload={
                    "entity_id": canonical(c.entity_id),
                    "kind": c.kind,
                    "intensity": c.intensity,
                    "stage": c.stage,
                    "effects": c.effects,
                },
                caused_by=scene_seq,
                provenance="llm_scene",
            )
        )
    if delta.day_plan:
        batch.append(
            EventIn(
                type="plan.revised",
                sim_time=sim_time,
                payload={
                    "household": household_id,
                    "persons": [dp.person_id for dp in delta.day_plan],
                },
                caused_by=scene_seq,
                provenance="llm_scene",
            )
        )
    if dropped or repeated:
        batch.append(
            EventIn(
                type="scene.invalid_ref",
                sim_time=sim_time,
                payload={
                    "household": household_id,
                    "ids": sorted(set(dropped)),
                    "repeat_memories": sorted(set(repeated)),
                },
                caused_by=scene_seq,
                provenance="system",
            )
        )
    if batch:
        log.commit(batch)
    if delta.canon_facts:
        assert_facts(
            log,
            canon,
            registry,
            delta.canon_facts,
            provenance="llm_scene",
            sim_time=sim_time,
            disclosure_tier=disclosure_tier,
            caused_by=scene_seq,
        )
    return scene_seq
