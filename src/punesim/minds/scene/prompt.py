from dataclasses import dataclass

from ...kernel.worlddelta import WorldDelta

SCENE_HOUR_S = int(6.5 * 3600)  # scenes render at 06:30, before the day moves

SYSTEM = """You write the morning scene of one household in a life simulation of Pune's old city.
Ground everything in the HOUSEHOLD CARD and RECENT EVENTS; never contradict them; invent only
small daily texture (chai, tiffin, water timing, school bags). Characters speak naturally —
Marathi/Hindi/English code-mix is welcome. The narration voice is neutral and never attributes
behavior or traits to any community; no slurs anywhere. If RECENT EVENTS contains something
serious, the family responds like a real family — worry, phone calls, changed plans.
If someone "heard" a rumor, they repeat it in their own words at their stated belief level —
a 40% believer is skeptical, a 90% believer acts on it; family members may argue about it.
Output ONLY one JSON object; all fields optional, no extra fields:
{"narration": "2-4 sentences",
 "transcript": "Name: line\\nName: line  (4-12 lines; speaker labels are given names like 'Madhura:', never ids)",
 "memory_writes": [{"person_id": "...", "salience": 0.0-1.0, "summary": "..."}],
 "mood_deltas": [{"person_id": "...", "dim": "mood" or "stress", "delta": -1.0..1.0}],
 "messages": [{"sender": "...", "recipients": ["..."], "channel": "phone" or "talk", "text": "..."}],
 "canon_facts": [{"subject": "person id", "predicate": "pers.trait", "value": "short trait"}],
 "day_plan": [{"person_id": "...", "steps": [{"t": seconds-since-midnight (28800 = 08:00),
               "place_ref": "place id from the card", "activity": "...", "mode": "walk"}]}]}
Only include day_plan when today should differ from routine (someone stays home, a hospital
visit, an errand). Use exactly the person ids and place ids given in the card.
A PHYSICAL STATE block, when present, says where a body already is: write the family visiting,
phoning or worrying about someone in a hospital bed, never that person at home.

THREE RULES THAT OVERRIDE EVERYTHING ELSE:
1. PEOPLE ARE FIXED. Every person is given to you as "Name (age, occupation) [id]". Never invent
   a name, age, relationship or job for anyone, and never rename someone you were given. If a
   line mentions a six-year-old pupil, they are a six-year-old pupil — not a colleague, not an
   aunt. Nobody in the room is anyone but the people on the card: do not have a child call out
   to a grandmother or an uncle who is not listed. If you need someone who was not given to
   you, refer to them vaguely ("a neighbour", "someone at the market") and never give them an id.
2. TIME IS FIXED. Every line carries the exact date, time, and how long ago it was. Say "yesterday"
   only for something marked (yesterday), and never move an event to a different time of day than
   the one shown. If someone SAW IT HAPPEN, they saw it at the stated hour — they cannot
   misremember it as happening at night.
3. EVERY MORNING IS ITS OWN MORNING. The "EARLIER MORNINGS" lines are scenes already written.
   Never restate one as if it happened today, never write a memory_write that repeats one, and
   do not run the same small beat again — if a boy lost his notebook yesterday, today is about
   something else. Real households repeat their routines but not their incidents."""

# Stated as fact, not as an instruction: the family knows where its own people
# are, and a scene that is told so has no reason to invent a boy onto the divan.
PHYSICAL_HEADER = "PHYSICAL STATE (where these people actually are — you cannot move them):"

REACTION_TASK = """It is {now} — the household has JUST learned of the most recent events above.
Write their immediate reaction — who calls whom, who rushes where, what they decide right now.
day_plan here means THE REST OF TODAY only (steps with t >= now, seconds since midnight): a parent
rushing to the hospital, a shop left shut, a child collected early. Keep it real and specific."""

_ROUTINE_TYPES = {"trip.start", "trip.end", "activity.start"}

# A scene must never be shown its OWN prior output as if it were news. These
# types are the scene lane's own bookkeeping: they carry no world state a family
# could independently notice, and re-feeding them makes the model copy them
# forward. In the 30-day soak 64% of every RECENT EVENTS block was the
# household's own previous LLM output, and 53 of 118 prompts were 100%
# self-output with zero world events — which is how a Sunday memory got
# re-formed on Monday, word for word. Memory is read back deliberately by
# memory_digest(); it never leaks in through here.
_SELF_OUTPUT_TYPES = frozenset({
    "scene.morning", "scene.reaction", "scene.skipped", "scene.invalid_ref",
    "scene.gate_capped",
    "memory.formed", "mood.delta", "plan.revised",
    # A street exchange is the camera rendering a transmission that already
    # happened. The information itself reaches the household through the
    # info.heard event; showing the rendered dialogue too would put the model's
    # own words back in front of it, which is the failure this set exists for.
    "conversation.held",
})


@dataclass(frozen=True)
class SceneResult:
    household_id: str
    delta: WorldDelta
    scene_seq: int
