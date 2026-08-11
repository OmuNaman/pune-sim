"""What did the change actually do?

`kernel/diff.py` already answers this properly — branch point, first knock-on,
how many people had a different day, when the two worlds re-converged. What it
cannot do is answer it at V3 scale: `diff_logs` materialises BOTH logs in memory
(diff.py:89-90), and two 6.8M-event logs is several gigabytes. So this refuses
loudly above a bound rather than taking the machine down.

That refusal is the honest state of the tool, not a stopgap to be hidden: a
ranged diff would be engine work, and pretending otherwise would mean a UI that
sometimes silently kills the server.
"""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ...kernel.log import EventLog
from ..readlog import ReadOnlyLog

router = APIRouter()

# Two logs, fully parsed, in one process. 1.5M events is roughly a gigabyte of
# Python objects — comfortable; 6.8M each is not.
MAX_EVENTS = 1_500_000


class DiffBody(BaseModel):
    a: str
    b: str


@router.post("/diff")
def diff(request: Request, body: DiffBody):
    from ...kernel.diff import diff_logs

    reg = request.app.state.registry
    ra, rb = reg.get(body.a), reg.get(body.b)
    if ra is None or rb is None:
        raise HTTPException(404, "unknown run")

    na = ReadOnlyLog(ra.db).summary()[0]
    nb = ReadOnlyLog(rb.db).summary()[0]
    if na + nb > MAX_EVENTS:
        raise HTTPException(
            413,
            f"these two runs hold {na + nb:,} events between them and the differ "
            f"reads both into memory at once (limit {MAX_EVENTS:,}). Diffing "
            f"runs this size needs a ranged differ that does not exist yet.")

    # Names make the report readable: "1,138 people had a different day" is a
    # number, "Asha Kulkarni" is a person.
    world = request.app.state.worlds.get(body.a, ra.db)
    names = {p.id: p.name for p in world.people.values()}

    la, lb = EventLog(ra.db), EventLog(rb.db)
    try:
        rep = diff_logs(la, lb, names)
    finally:
        la.close()
        lb.close()

    return {
        "identical": rep.identical,
        "a": {"id": ra.id, "name": ra.name, "events": rep.a_events},
        "b": {"id": rb.id, "name": rb.name, "events": rep.b_events},
        "branch_point": rep.branch_point,
        "first_divergence": rep.first_divergence,
        "people_changed": len(rep.people_changed),
        # The decoherence curve: how far apart the two worlds drift, by day.
        "by_day_changed": [{"day": d, "n": n}
                           for d, n in sorted(rep.by_day_changed.items())],
        "reconverged_day": rep.reconverged_day,
        "type_deltas": {k: v for k, v in sorted(
            rep.type_deltas.items(), key=lambda kv: -abs(kv[1])) if v},
        "rumor_deltas": rep.rumor_deltas,
        "only_in_b": rep.only_in_b[:40],
        "only_in_a": rep.only_in_a[:40],
        "headline": rep.headline,
    }
