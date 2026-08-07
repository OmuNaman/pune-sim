# 30-Day Soak Report - runs/soak/events.db

Run: `uv run punesim run --days 30 --scenes --inject data/scenarios/soak_30d.json --db runs/soak/events.db`
Seed 108, 80 households, 306 people, random hazards on. Analyzed read-only on 2026-08-07.

**Restart note:** the original soak died at sim day ~7 with a `RefusalError` - both LLM lanes
returned *empty* text for one scene (provider blip misclassified as refusal). Fixed on master in
commit `6513624` (empty responses retry once under a distinct cassette slot; failing scenes now
commit a loud `scene.skipped` event instead of killing the run). The run was restarted fresh into
the same db path; days 0-7 fast-forwarded via cassette replay, so early scenes are identical.
This report covers the completed restarted run: **day 0-29, 41,546 events** in the event table
(the CLI reported "events committed" counts a smaller subset; the table is truth).

## Verdict

| Criterion | Result |
|---|---|
| Continuity on followed family hh:000 (V1 exit) | **FAIL - 4 contradictions (2 moderate, 2 minor), listed below** |
| Cost < $1/sim-day | **PASS - $0.0017/sim-day ($0.052 total)** |
| Rumor lifecycles rise-then-die, no >90% saturation | **PASS - all 5 claims died; max reach 73%** |
| Run health (30 days, scenes rendered, injections landed) | **PASS with caveats - 3 scene.skipped (1.9%); spotlight ossifies after d15** |
| Hazards produce percepts + institution reactions | **PASS with caveats - d9 supply_cut had zero percepts; d7 reaction scene lost to a skip** |
| Pressure hysteresis (no thrash) | **MARGINAL - bounded, but financial integrator ratchets; 13 people double-fired, 1 triple-fired** |

## Health

- Events/day: steady 1,311-1,747 (mean 1,385); no drift, no gaps. Total 41,546.
- Scenes: 157 scene.morning + 1 scene.reaction rendered; 161 attempted; 3 scene.skipped
  (provenance=system), all "SchemaError: failed after repair: no JSON object in output":
  - seq11323 d7 14:19 hh:070 - **the reaction lane** for that day's severity-0.73 collision (see Hazards)
  - seq14678 d10 06:30 hh:037 (morning), seq25554 d18 06:30 hh:030 (morning)
- Gate behavior: d0-d5 spotlight on hh:000-004 (attention seeds); d5 adds the hh:002 reaction;
  d6-d14 rotates through injured/pressured households (hh:019/030/034/037/059/070/032/025/014/035/073/078...).
  **d15 rendered 14 scenes** - exactly the 13 households gated by d14 power-outage belief.actions
  plus fill; the routine-bypass gate works as designed.
- **Spotlight ossification:** from d19 to d29 the spotlit set is frozen at
  {hh:008, hh:068, hh:069, hh:073, hh:078} every single day - households whose daily-wage adults
  repeatedly fire the financial-pressure gate (see Pressures) accumulate attention and crowd out
  everyone else. hh:000 was last seen d6 and never returned. Worth an attention-decay look in V2.

## Rumors

| claim_key | hearings | uniq hearers | max hop | variants | belief.actions | lifecycle |
|---|---|---|---|---|---|---|
| cl:fire.small:...3681735096:d1 | 463 | 224 (73%) | 8 | 16 | 0 | d1:387 -> d4:1, dead |
| cl:road.collision:...11430153883:d5 (injected) | 320 | 163 (53%) | 9 | 12 | 0 | d5:229 -> d10:1, dead |
| cl:road.collision:...4632237474:d7 | 368 | 209 (68%) | 7 | 13 | 0 | d7:284 -> d10:2, re-spike d11:20, dead d12+ |
| cl:power.outage:...282961094:d14 | 62 | 43 (14%) | 6 | 6 | 14 | d14:28 -> d18:1, dead |
| cl:mandal_funds (injected d12) | 16 | 10 (3%) | 4 | 3 | 5 | d12:14, d13:2, dead |

- **No saturation failure** (max 73% of 306) and **no immortal rumor** (nothing spreading at d29).
- The d11 re-spike of the d7 collision claim is mechanically clean: one holder (person:078.2)
  hit a crowded morning cluster and fired 6 shares at ~10:22, cascading hops 4->7 in one day; it
  then died of freshness/stifling. Multi-hop-per-day working as designed.
- Distortion audit works: the fire claim mutated through EXAGGERATE ("6 people affected") and two
  different REATTRIBUTE blames (Tambdi Jogeshwari, then Chavhan Shriram Mandir) - drift visible hop by hop.
- cl:mandal_funds behaved: seeded at 0.8 credence to 2 students, spread only through their two
  households + contacts, produced 5 stop_patronage actions against Tulshibaug Mandir (hh:014,
  hh:035), then died. Low reach explained by seeding two students at 17:45 (day nearly over,
  freshness tau 4d, fraud threshold 0.7).

## Hazards

| seq | day | type | place | outcome |
|---|---|---|---|---|
| 1361 | d1 14:05 | fire.small (clockwork, sev 0.31) | RCM Gujarati High School | 116 percepts; ambulance dispatched |
| 7247 | d5 11:40 | road.collision (**injected**, sev 0.55) | Nilkantheshwar; hits person:002.2 Iqbal Tamboli | 78 percepts; full chain: condition.set(injury 0.55) -> ambulance -> clinic phones family -> admitted at Manish Clinic -> **hh:002 reaction scene at 12:15** (canon-perfect, see Continuity) |
| 10243 | d7 13:44 | road.collision (clockwork, sev 0.73) | Mahalaxmi Store; hits person:070.1 Ketaki Jagtap | 133 percepts; full institution chain; **but the hh:070 reaction scene was skipped (SchemaError)** - the most severe hazard of the run got no narrative reaction |
| 13316 | d9 06:24 | water.supply_cut (clockwork, sev 0.35) | Moula Mohammed Ali Johar Urdu School | **0 percepts** - only 1 home within the 320 m area and zero synthesized residents in it; the "populated core" venue filter admits near-empty catchments |
| 20139 | d14 10:53 | power.outage (clockwork, sev 0.5) | place:way/282961094 | 13 percepts -> 43 hearers -> 14 belief.actions -> 13 households gated d15 |

Stub-institution quirk (known V2 gap): an **ambulance is dispatched for every placed hazard**,
including the water cut and the power outage - stub_institution_reactions keys on place alone,
not hazard type.

## Pressures

32 pressure.crossed events, 17 unique (person, dimension). The two health crossings are correct
(the two collision victims, once each). The 15 financial arcs are the concern:

- 13 people fired **twice** (first at ~0.62 over the 0.6 threshold, again at ~0.77-0.80 over the
  hysteresis-raised 0.75) and person:008.1 (Vandana Kulkarni, domestic_worker) fired **three
  times** (d7 0.634, d14 0.763, d18 0.82) - she oscillates around 0.75.
- Root cause, verified against her activity log: adults with work_id=None (domestic_worker,
  cook, some shop_assistants) rarely emit an activity in the "worked" whitelist
  (work/driving_rounds/school/errand) - and when a scene *does* send them to work, the
  LLM-authored activity strings ("leave for two housecleaning jobs", "domestic work (client
  house)") do not match the whitelist either, so **narrated workdays still count as missed work**.
  Their p_financial ratchets up ~0.09/day against a 0.985 decay and re-crosses forever.
- Verdict: hysteresis itself holds (no rapid flip-flopping; crossings are 2-7 days apart), but
  the integrator is a one-way ratchet for this occupation class - it is why the same 5 households
  monopolize the spotlight from d15 on. Flagging as a V1 bug: match scene activities to the
  worked-whitelist (or classify by presence-at-work-place), and consider widening the hysteresis
  band or capping refires.

## Continuity findings - followed family hh:000 (Chavan)

Canon roster (seed 108): Suhas 34 rickshaw_driver; Madhura 30 teacher at RCM school;
Dnyaneshwar 10, Vaishali 13, Aditya 10 - all three at RCM school. hh:000 was spotlit d0-d6
(7 morning scenes, seqs 2, 1327, 3074, 4468, 5828, 7196, 8844) and appears in no event after d7.
No conversation.held events exist anywhere in this run. All names, ages, occupations, and
family relationships stayed stable across all seven scenes; no phantom injuries, no healed
injury reappearing; the d3 "Sunday" is correct (d3 = Sunday 04 Jan 2026). The d1 school fire
was witnessed by Madhura + all three kids (seqs 2686-2689) and the family fire talk on d2
is fully grounded. But four contradictions:

1. **[moderate] d3, seq4468 - the fire moves to nighttime.** Madhura: "Kal school la hoto, pan
   fire raatri la lagla" (I was at school yesterday, but the fire broke out at night). Canon:
   hazard.fire.small seq1361 at **14:05**, witnessed by Madhura and the children at 14:10
   (seqs 2686-2689), and Aditya on d2 (seq3074): "Maine dekha smoke bahut tha." The scene
   rewrites a witnessed afternoon fire into an unwitnessed night fire.
2. **[moderate] d3-d6 - "Shobha tai" the adult colleague is actually a 6-year-old.** The scenes
   (seq4468, seq5828) put Madhura on the phone with colleague "Shobha tai", and four
   message.sent events (seqs 4479, 5839, 7205, 8852) are addressed to person:022.4. Canon:
   person:022.4 is **Sachin Shelar, age 6, student** at her school (the d2 f2f hearing seq4444
   - a pupil telling the teacher - is what the scene dressed up as a colleague phone call).
   Invented identity contradicting the person registry, repeated four days running.
3. **[minor] d4, seqs 5829-5833 + 5839 - copied memories record things that did not happen that
   day.** The entire d4 memory block and the phone message are verbatim duplicates of d3
   (seqs 4469-4473, 4479): the d4 Vaishali memory says "Reminded that it's Sunday" - d4 is
   **Monday**, and no one mentions Sunday in the d4 transcript; the re-sent message says
   "mala kal sangitla" (told me yesterday) - by d4 that was two days prior.
4. **[minor] d6, seq8844 - temporal drift.** Narration: "still uneasy after yesterday's fire
   rumour" - the fire and its rumor ran d1-d4; the d5 talk was the collision. "Yesterday" is
   off by five days.

Observations (not counted as contradictions): d6 Suhas cites "a customer last night" as a
rumor source - no such hearing in the log (arguably allowed texture); the INFO lane lets a
direct witness account be **overwritten by weaker hearsay** (Madhura, witness credence 0.95,
ends d1 holding her husband hop-2 exaggerated variant at 0.99 - seq2958; same for Suhas on
d7, seq11721) and permits an A->B->A echo (Aditya receives back his own exaggeration via Suhas,
seq2960). Both are worth a V2 rule (witness accounts sticky; lineage-aware echo damping).

Contrast: the single reaction scene (hh:002 Tamboli, d5 seq8138) is canon-perfect - all five
named members match the registry (Arman 64, Amina 62, Rukhsana 36 tailor "closing her shop",
Nilofar 5 collected from school, Farzana 4 clinging to her grandmother), and the hospital named
(Manish Clinic) is exactly where Iqbal was admitted.

## Cost

185 llm.response events, all with full token splits (183 x deepseek-v4-flash, 2 x deepseek-v4-pro
from the refusal reroute - priced at flash rates below, negligible):

- prompt: 169,666 tokens x $0.084/M = $0.01425
- completion: 225,469 tokens x $0.168/M = $0.03788
- **total ~ $0.0521 for 30 sim-days -> $0.0017/sim-day** (no 60/40 estimate needed; real splits
  present). Two orders of magnitude under the $1/sim-day exit bar even before cassette reuse.
