"""Procedures as data: what an institution does, and when it does it.

V2 wrote two of these by hand, inline in `procedures.step` — a hospital stay and
a police FIR. They are 70 lines of near-identical shape: notice an event, refuse
to notice it twice, work out a few facts about the world, and put some events on
the calendar. Writing the third one that way (a court case, a school admission,
a PMC complaint) would be writing that shape a third time.

So the shape is the engine and the specifics are data. A Procedure is a trigger,
a binder, and a schedule; the schedule is declarative, which is where new
procedures actually differ. The binder stays Python on purpose: `min(adults)`,
`block.nearest(place, "police")` and "what does the victim's own account say" are
world queries, and inventing an expression language to spell them in JSON would
buy nothing but a worse Python.

The architecture asks for a closed effect vocabulary, and this is it: a
procedure may schedule events and may mark someone as in hospital or resting.
It cannot write canon, move money, or touch anyone's beliefs.
"""

from collections.abc import Callable
from dataclasses import dataclass, field

from ..kernel.timebase import SECONDS_PER_DAY
from ..world.schedule import TimedEvent


@dataclass(frozen=True)
class Step:
    """One event a procedure puts on the calendar.

    `day` and the `$name` values in `payload` are binding names, filled from
    whatever the procedure's binder returned.
    """

    day: str  # binding holding the absolute sim day
    at_s: int  # seconds into that day
    type: str
    payload: dict  # literal values, or "$binding" to substitute


@dataclass(frozen=True)
class Procedure:
    name: str
    dedup: str  # the ProcState set that remembers which events were handled
    match: Callable  # (event) -> bool
    bind: Callable  # (event, ctx) -> dict of bindings, or None to decline
    steps: tuple[Step, ...] = ()
    commit: Callable | None = None  # (bindings, state) -> None; the two state effects
    scratch: dict = field(default_factory=dict)


def _fill(payload: dict, bindings: dict) -> dict:
    out = {}
    for k, v in payload.items():
        out[k] = bindings.get(v[1:]) if isinstance(v, str) and v.startswith("$") else v
    return out


def run(
    procedures: list[Procedure],
    log_events_today: list,
    state,
    ctx: dict,
) -> dict[int, list[TimedEvent]]:
    """Every procedure against every event of the day, in declaration order.

    Order is load-bearing: it decides the sequence in which futures land in the
    pending queue, and therefore the seq numbers of everything downstream. The
    determinism hash notices.
    """
    pending: dict[int, list[TimedEvent]] = {}
    for e in log_events_today:
        for proc in procedures:
            seen = getattr(state, proc.dedup)
            if e.seq in seen or not proc.match(e):
                continue
            # Marked before binding, deliberately: an event this procedure has
            # looked at is done with, even if the world turned out not to
            # support it (a hazard whose victim is not in the roster). The
            # hand-written version did the same, and retrying such an event
            # every day for the rest of the run would be worse.
            seen.add(e.seq)
            bindings = proc.bind(e, ctx)
            if bindings is None:
                continue
            for step in proc.steps:
                d = bindings[step.day]
                pending.setdefault(d, []).append(TimedEvent(
                    d * SECONDS_PER_DAY + step.at_s,
                    step.type,
                    _fill(step.payload, bindings),
                    e.seq,
                ))
            if proc.commit is not None:
                proc.commit(bindings, state)
    return pending
