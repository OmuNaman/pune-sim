"""Events as English, plus the ids that English is about.

The sentences are ported from `viewer/server.py` almost unchanged — forty-odd
event types of carefully written prose, and a revamp has no business rewriting
them. What changes is the return type.

The old version returned a bare string, so the client had to get structure back
out of it: `app.js` recovered "who is injured" by running `/^(.+?) admitted/`
over the rendered sentence, and matched a scene's speaker to a person with
`p.name.startsWith(firstWord)` — first-name roulette across 49,578 people. The
server had the ids all along and threw them away.

So `humanize()` returns the sentence AND the ids it named. Clicking a name in
the ticker is then a lookup, not a guess.
"""

import orjson

# Which payload keys hold a person, and which hold a place. Written once here
# instead of being re-derived per event type — every lane agrees on these names.
_PERSON_KEYS = ("person", "sender", "entity_id", "complainant", "victim", "source")
_PERSON_LISTS = ("recipients", "participants", "lineage")
_PLACE_KEYS = ("place", "at", "from", "to", "home", "station", "subject")

_ORGS = {"org:pmc_water": "the PMC water office", "org:mseb": "the electricity board"}


def refs_in(payload: dict) -> dict[str, list[str]]:
    """The person and place ids this payload mentions, deduped, in order."""
    people, places = [], []
    for k in _PERSON_KEYS:
        v = payload.get(k)
        if isinstance(v, str) and v.startswith("person:"):
            people.append(v)
    for k in _PERSON_LISTS:
        for v in payload.get(k) or ():
            if isinstance(v, str) and v.startswith("person:"):
                people.append(v)
    for k in _PLACE_KEYS:
        v = payload.get(k)
        if isinstance(v, str) and v.startswith(("place:", "home:")):
            places.append(v)
    claim = payload.get("claim")
    if isinstance(claim, dict):
        s = claim.get("subject")
        if isinstance(s, str) and s.startswith(("place:", "home:")):
            places.append(s)
    return {
        "person_ids": list(dict.fromkeys(people)),
        "place_ids": list(dict.fromkeys(places)),
    }


def text_for(e, names: dict[str, str], places: dict[str, str]) -> str:
    """One event as a sentence. Ported from viewer/server.py:35-141."""
    p = e.payload

    def nm(x):
        return names.get(x, places.get(x, x or "?"))

    t = e.type
    if t == "trip.start":
        return (f"{nm(p.get('person'))} sets off from {nm(p.get('from'))} to "
                f"{nm(p.get('to'))} ({p.get('purpose', '')})")
    if t == "trip.end":
        return f"{nm(p.get('person'))} arrives at {nm(p.get('at'))}"
    if t == "activity.start":
        return f"{nm(p.get('person'))}: {p.get('activity')} at {nm(p.get('at'))}"
    if t == "message.sent":
        rec = ", ".join(nm(r) for r in p.get("recipients", []))
        return f"{nm(p.get('sender'))} → {rec}: {p.get('text', '')}"
    if t == "memory.formed":
        return f"{nm(p.get('person'))} will remember: {p.get('summary', '')}"
    if t == "mood.delta":
        d = p.get("delta", 0)
        return f"{nm(p.get('person'))}: {p.get('dim')} {'+' if d >= 0 else ''}{d}"
    if t == "hazard.road.collision":
        who = ", ".join(nm(x) for x in p.get("participants", []))
        return f"Road accident at {nm(p.get('place'))} — {who}"
    if t == "ambulance.dispatched":
        return f"Ambulance reaches {nm(p.get('place'))}"
    if t == "hospital.admitted":
        return f"{nm(p.get('person'))} admitted at {nm(p.get('place'))}"
    if t == "condition.set":
        return f"{nm(p.get('entity_id'))}: {p.get('kind')} (intensity {p.get('intensity')})"
    if t == "scene.morning":
        return f"Morning scene — {p.get('household')}"
    if t == "scene.reaction":
        return f"The {p.get('household')} household reacts"
    if t == "conversation.held":
        if p.get("with") == "journalist":
            return f"{nm(p.get('person'))} spoke with a journalist"
        who = " and ".join(nm(x) for x in p.get("participants", []))
        return f"{who} stop to talk at {nm(p.get('place'))} — the news changes hands"
    if t == "plan.revised":
        return f"{p.get('household')} changes today's plans"
    if t == "info.heard":
        claim = p.get("claim", {})
        how = {"witness": "saw it", "household": "heard at home",
               "f2f": "heard"}.get(p.get("channel"), "heard")
        src = "" if p.get("source") in ("witness", "origin") else f" from {nm(p.get('source'))}"
        return f"{nm(p.get('person'))} {how}{src}: “{claim.get('text', '')}”"
    if t == "belief.action":
        verb = {"avoid_place": "will avoid", "store_water": "is storing water because of",
                "stop_patronage": "is done with"}.get(p.get("action"), p.get("action"))
        return f"{nm(p.get('person'))} believes the rumor and {verb} {nm(p.get('place'))}"
    if t == "plan.avoided":
        return f"{nm(p.get('person'))} stays home today, avoiding {nm(p.get('place'))}"
    if t == "pressure.crossed":
        dim = {"p_health": "health", "p_financial": "money"}.get(p.get("pressure"), p.get("pressure"))
        return f"{nm(p.get('person'))}'s {dim} worries are mounting"
    if t == "hospital.discharged":
        return (f"{nm(p.get('person'))} discharged from {nm(p.get('place'))} — "
                f"bill ₹{int(p.get('bill') or 0)}")
    if t == "money.paid":
        return f"{p.get('household', '?')} pays ₹{int(p.get('amount') or 0)} ({p.get('reason', '')})"
    if t == "loan.taken":
        return f"{p.get('household', '?')} borrows ₹{int(p.get('principal') or 0)} from the moneylender"
    if t == "loan.interest":
        return (f"{p.get('household', '?')}: interest ₹{int(p.get('amount') or 0)} added, "
                f"₹{int(p.get('outstanding') or 0)} outstanding")
    if t == "police.fir.registered":
        return (f"{nm(p.get('complainant'))} registers an FIR at {nm(p.get('station'))}: "
                f"“{p.get('statement', '')}”")
    if t == "fir.update":
        return f"Police: {p.get('status', '')} ({nm(p.get('victim'))}'s case)"
    if t == "scene.skipped":
        return f"(scene skipped for {p.get('household')}: {p.get('reason', '')[:60]})"
    if t == "crowd.gathered":
        return f"A crowd of ~{p.get('size', '?')} gathers at {nm(p.get('place'))}"
    if t == "police.deployed":
        return f"Police deployed at {nm(p.get('place'))} (crowd of {p.get('crowd_size', '?')})"
    if t == "curfew.imposed":
        return f"Curfew imposed around {nm(p.get('place'))} until day {p.get('until_day', '?')}"
    if t.startswith("unrest."):
        return f"Unrest near {nm(p.get('place'))} — {t.split('.', 1)[1].replace('_', ' ')}"
    if t == "hazard.water.supply_cut":
        return f"Water supply cut around {nm(p.get('place'))}"
    if t == "hazard.power.outage":
        return f"Power outage around {nm(p.get('place'))}"
    if t == "hazard.fire.small":
        return f"Small fire at {nm(p.get('place'))}"
    if t.startswith("hazard."):
        who = ", ".join(nm(x) for x in p.get("participants", []))
        return (f"{t.split('.', 1)[1].replace('.', ' ')} at {nm(p.get('place'))}"
                f"{' — ' + who if who else ''}")
    if t == "info.rumor":
        return f"A rumor starts: “{p.get('claim', {}).get('text', '')}”"
    if t == "complaint.registered":
        return (f"A complaint reaches {_ORGS.get(p.get('org'), p.get('org', '?'))} "
                f"about {nm(p.get('place'))}")
    if t == "utility.tanker_arrived":
        return f"A municipal tanker reaches {nm(p.get('place'))} ({p.get('loads', 1)} load(s))"
    if t == "utility.restored":
        return f"{str(p.get('utility', 'supply')).title()} is back around {nm(p.get('place'))}"
    if t == "scene.invalid_ref":
        parts = []
        if p.get("ids"):
            parts.append(f"invented ids dropped: {', '.join(p['ids'])}")
        if p.get("repeat_memories"):
            parts.append(f"repeated memories dropped for {len(p['repeat_memories'])}")
        return f"(canon gate, {p.get('household')}: {'; '.join(parts)})"
    if t == "run.meta":
        return (f"Run begins — seed {p.get('seed')}, {p.get('households')} households, "
                f"{p.get('days')} days")
    if t == "fact.established":
        return f"Canon: {nm(p.get('subject'))} — {p.get('predicate')} = {p.get('value')}"
    if t == "scene.gate_capped":
        return f"(attention gate capped: {p.get('rendered')} rendered, {p.get('dropped') and len(p['dropped'])} dropped)"
    return f"{t}: {orjson.dumps(p).decode()[:120]}"


def humanize(e, names: dict[str, str], places: dict[str, str]) -> dict:
    """`{text, refs}` — the sentence, and the ids it is about."""
    return {"text": text_for(e, names, places), "refs": refs_in(e.payload)}
