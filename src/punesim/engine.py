"""V0 clockwork day: compile every person's schedule into events and commit
them in sim-time order. Zero LLM calls. The acid test: two runs from the same
seed produce byte-identical determinism hashes.
"""

from .kernel.log import EventIn, EventLog
from .population.synth import Person
from .world.block import Block
from .world.schedule import day_events


def run_days(
    log: EventLog,
    run_seed: int,
    block: Block,
    people: dict[str, Person],
    *,
    days: int = 1,
    start_day: int = 0,
) -> int:
    """Returns total events committed. Deterministic given (run_seed, block)."""
    total = 0
    for day in range(start_day, start_day + days):
        timed = []
        for pid in sorted(people):
            for te in day_events(run_seed, people[pid], block, day):
                timed.append((te.sim_time, pid, te))
        order = {"trip.end": 0, "activity.start": 1, "trip.start": 2}
        timed.sort(key=lambda x: (x[0], x[1], order.get(x[2].type, 9), x[2].type))
        batch = [
            EventIn(type=te.type, sim_time=t, payload=te.payload, provenance="clockwork")
            for (t, _pid, te) in timed
        ]
        log.commit(batch)
        total += len(batch)
    return total
