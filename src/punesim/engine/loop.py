from ..institutions import procedures as proc_mod
from ..kernel.attention import AttentionField
from ..kernel.facts import Canon, core_registry
from ..kernel.log import EventIn, EventLog
from ..kernel.timebase import SECONDS_PER_DAY
from ..kernel.worlddelta import PlanStep
from ..llm.gateway import CassetteMiss, Gateway
from ..minds import info as info_mod
from ..population.synth import Household, Person
from ..world import hazards as hazards_mod
from ..world.block import DEFAULT_BLOCK, Block
from ..world.schedule import TimedEvent, roaming_worksites
from .bend import _apply_beliefs, _apply_stays, _apply_zones
from .day import _apply_admissions, _commit, _compile_day, _compile_override, _sorted
from .info_pass import _info_pass, _seed_rumor
from .injection import Injection
from .pressure import _pressure_tick
from .reactions import _unrest_response, stub_institution_reactions
from .state import _ROUTINE_TYPES, GATE_BURST, REACTION_DELAY_S, SimState


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
    talk: bool = True,
    block_name: str = DEFAULT_BLOCK,
    state: SimState | None = None,
) -> tuple[int, SimState]:
    """The V1 day pipeline: gated scenes -> compile (belief-bent) -> injections
    + sampled hazards -> split-commit with reaction scenes -> INFO propagation
    -> pressure tick. Returns (total events, final state)."""
    from ..minds.scene import compile_plan_overrides, run_morning_scenes, run_reaction_scene

    # A caller may pass state in to advance an existing world one day at a time
    # (scripts/day_cost.py does, to see what grows as a run gets longer). A
    # fresh SimState per day would reset exactly the accumulation worth
    # measuring, and would also re-fire everyone's opening pressures.
    fresh = state is None
    if fresh and start_day:
        raise ValueError(
            f"start_day={start_day} with no state: this would silently begin a *new* world "
            "on day "
            f"{start_day} — no run.meta, everyone's opening pressures re-fired, nobody "
            "remembering anything they have heard. Pass the state returned by the "
            "previous call."
        )
    if fresh:
        state = SimState(canon=Canon(), registry=core_registry(), attention=AttentionField())
        state.proc.finances = proc_mod.init_finances(run_seed, households, people)
        for p in people.values():  # a poor family STARTS worried — being born poor
            f = state.proc.finances.get(p.household_id)  # is not an E2 event
            if f is not None and p.age >= 18:
                state.pressures[p.id] = {"p_health": 0.1, "p_financial": proc_mod.p_financial(f)}
    total = 0
    if start_day == 0:  # self-describing log: a db alone is enough to branch it
        meta = {"seed": run_seed, "households": len(households), "days": days,
                "follow": list(follow)}
        # The block belongs in meta because re-synthesising a 12k-household
        # oldcity run against the default kasba pool would silently produce a
        # different population. Recorded only when it is NOT the default, so
        # every existing log's run.meta payload — and the determinism hash the
        # V0-V2 soaks were measured against — stays byte-identical.
        if block_name != DEFAULT_BLOCK:
            meta["block"] = block_name
        log.commit([EventIn(
            type="run.meta", sim_time=0, payload=meta, provenance="system",
        )])
        total += 1
    hh_of_person = {p.id: p.household_id for p in people.values()}
    hh_by_id = {h.id: h for h in households}
    hh_members = {h.id: h.member_ids for h in households}
    if state.worksites is None:  # constant per run; 10M distance sums at 12k
        state.worksites = roaming_worksites(run_seed, block, people)
    worksites = state.worksites
    for ref in follow if fresh else ():  # focus is set once, not re-set per day
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
                # Gated households come in attention order, not alphabetically:
                # when more are marked than the day can afford, the ones that
                # survive should be the ones something happened to.
                gated = state.attention.top_k(
                    [h for h in state.gate_marks if h in hh_by_id and h not in focused],
                    len(state.gate_marks), tick=day * 288, day=day,
                )
                fill = [
                    h for h in state.attention.top_k(all_ids, scenes_k, tick=day * 288, day=day)
                    if h not in gated and h not in focused
                ]
                # A mass event should mean MORE scenes, not unbounded scenes.
                # One power cut gate-marked 78 of 80 households and made a
                # single day cost 67 scenes — thirteen normal days — with no
                # ceiling anywhere in the pipeline.
                budget = max(scenes_k, min(len(focused) + len(gated), scenes_k * GATE_BURST))
                chosen = (focused + gated + fill)[:budget]
                dropped = [h for h in gated if h not in chosen]
                if dropped:
                    log.commit([EventIn(
                        type="scene.gate_capped", sim_time=day * SECONDS_PER_DAY,
                        payload={"marked": len(gated), "rendered": len(chosen),
                                 "dropped": dropped[:40], "budget": budget},
                        provenance="system",
                    )])
                    total += 1
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
        state.sheltered = set()
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
            payload = {
                "place": inj.place,
                "participants": list(inj.participants),
                "severity": inj.severity,
                **inj.payload,
            }
            if inj.type.startswith("info.") and isinstance(payload.get("claim"), dict):
                # A scenario author writes the claim's SHAPE (subject, predicate,
                # charge); the narrator text is rendered from it. Committing the
                # bare spec left the injection event reading "A rumor starts: ''"
                # everywhere it was shown.
                spec = dict(payload["claim"])
                if not spec.get("text"):
                    c = info_mod.Claim.from_payload(
                        {**spec, "key": spec.get("key", f"cl:injected:d{day}"),
                         "subject": spec.get("subject", inj.place or ""),
                         "predicate": spec.get("predicate", "dangerous"), "text": ""}
                    )
                    spec["text"] = info_mod.render_text(c, block)
                payload["claim"] = spec
            inj_seq = log.commit(
                [EventIn(type=inj.type, sim_time=t_abs, payload=payload, provenance="user")]
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
            log, state, run_seed, block, people, hh_members, hh_of_person, day,
            gateway=gateway if talk else None,
        )
        total += n_heard
        state.gate_marks.update(info_marks)

        # 7. V2 institutions: procedures schedule their futures, money moves
        day0, day1 = day * SECONDS_PER_DAY, (day + 1) * SECONDS_PER_DAY
        today = list(log.events(since_time=day0, until_time=day1))  # bounded in SQL
        new_pending = proc_mod.step(today, state.proc, run_seed, day, block, people, state.info)
        for d, tes in new_pending.items():
            state.pending.setdefault(d, []).extend(tes)
        fin_events, p_fin = proc_mod.daily_finance_tick(
            state.proc, day, people, today, extra_absent=state.sheltered
        )
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
