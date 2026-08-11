from dataclasses import replace

from ..kernel.log import EventIn, EventLog
from ..kernel.timebase import SECONDS_PER_DAY
from ..llm.gateway import Gateway
from ..minds import info as info_mod
from ..minds import talk as talk_mod
from ..population.synth import Person
from ..world import hazards as hazards_mod
from ..world.block import Block
from .injection import Injection
from .reactions import RESOLUTION_PREDICATES
from .state import _ROUTINE_TYPES, SimState


def _info_pass(
    log: EventLog,
    state: SimState,
    run_seed: int,
    block: Block,
    people: dict[str, Person],
    hh_members: dict[str, tuple[str, ...]],
    hh_of_person: dict[str, str],
    day: int,
    gateway: Gateway | None = None,
) -> tuple[dict[str, str], int]:
    """Post-commit INFO lane (E5 + E7 percepts): witnesses of today's hazards
    receive tiered percepts, the day's co-presence propagates every held claim
    mechanically, and credence crossings become belief.action events that
    change tomorrow's plans. Runs on COMMITTED movements, so reaction-scene
    rewrites are already reflected. Returns (gate marks, heard count)."""
    day0, day1 = day * SECONDS_PER_DAY, (day + 1) * SECONDS_PER_DAY
    # Bounded in SQL, not in Python: filtering a full replay cost one whole-log
    # deserialize per day, so a run's cost grew with the square of its length.
    # Same rows, same seq order — `events` applies the bounds to the same query.
    today = list(log.events(since_time=day0, until_time=day1))
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

    by_type = hazards_mod.BY_TYPE
    by_seq_today = {e.seq: e for e in today}
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
        # A class the world is allowed to count but never to stage seeds no
        # claim and opens no scene, however hard attention is pointed at it
        # (08-identity §5). Nothing today is `numeric`; NCRB calibration will
        # add classes that must be.
        if cls is not None and cls.countable_only:
            continue
        shape = cls.shape if cls else "point"
        predicate = cls.predicate if cls else e.type.rsplit(".", 1)[-1]
        topics = cls.topics if cls else ("safety",)
        if cls:
            charge = cls.charge
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

    # Relief is news too. The third soak's ONLY continuity failure was that the
    # power came back at 23:54 and nobody was ever told: utility.restored
    # carries {org, place, utility}, so it seeded no percept, matched no member
    # in any prompt, and the scenes reasonably concluded the blackout was still
    # running — two days by day 16, a week by day 20. Place-scoped events have
    # to reach the people whose lives they change, or the world silently fixes
    # things behind everyone's back.
    for e in today:
        if e.type not in RESOLUTION_PREDICATES or not e.payload.get("place"):
            continue
        cause = by_seq_today.get(e.caused_by)
        if cause is None or not cause.type.startswith("hazard."):
            continue
        predicate, topics = RESOLUTION_PREDICATES[e.type]
        good = hazards_mod.hazard_claim(
            e.type, e.payload["place"], day, predicate, topics, 0.35, block
        )
        good = replace(good, valence=0.5, veracity="true")
        good = replace(good, text=info_mod.render_text(good, block))
        stale_key = f"cl:{cause.type.split('.', 1)[1]}:{e.payload['place']}:d{cause.sim_time // SECONDS_PER_DAY}"
        for pid, spec in sorted(
            hazards_mod.witness_tiers(e.payload["place"], e.sim_time, "area", block, people, intervals)
        ):
            if good.key in state.info.holdings.get(pid, {}):
                continue
            variant = replace(good, specificity=spec)
            variant = replace(variant, text=info_mod.render_text(variant, block))
            credence = hazards_mod.witness_credence(spec)
            h2 = info_mod.Heard(e.sim_time + 120, pid, variant, "witness", "witness", credence, e.seq)
            seq = commit_heard(h2)
            state.info.hear(
                pid, variant, credence, day, seq, source="witness",
                t_abs=h2.sim_time, channel="witness",
            )
            # ...and the trouble it resolves stops being live for them: they no
            # longer act on it and no longer pass it on.
            old = state.info.holdings.get(pid, {}).get(stale_key)
            if old is not None:
                old.credence = min(old.credence, 0.2)
                old.stifled = True

    heard = info_mod.propagate_day(
        state.info, run_seed, day, block, people, intervals, hh_members, commit_heard
    )

    marks: dict[str, str] = {}
    for act in info_mod.crossed_actions(state.info, state.acted, people, block):
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
        if act.action in info_mod.AVOIDING_ACTIONS:
            state.avoid.setdefault(act.person, {})[act.place] = (act.claim_key, seq)
        elif act.action == "store_water":
            state.morning_acts.setdefault(act.person, []).append(("store_water", act.claim_key, seq))
        hid = hh_of_person.get(act.person)
        if hid:
            marks[hid] = "info"

    # T2 street talk: the day's one exchange that carried news across a
    # household line. The transmission already happened and is committed —
    # this is only the camera turning to look at it.
    if gateway is not None and heard:
        ex = talk_mod.pick_exchange(heard, people, hh_of_person, intervals)
        if ex is not None:
            seq = talk_mod.render_exchange(log, gateway, block, people, ex)
            if seq is not None:
                for pid in (ex.speaker, ex.listener):
                    hid = hh_of_person.get(pid)
                    if hid:
                        state.attention.bump(hid, 0.8, tick=day * 288 + 200)
    return marks, len(heard)


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
