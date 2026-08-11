"""Who is computing right now.

Phase 4 fills this in: one subprocess per playing run, the control loop living
in `run_simulation`'s `on_day_end` hook, and an SSE stream per run. The owner's
rule is that every run and branch has its own play/pause and any combination may
run at once — which is exactly why each is its own process rather than a thread
or a slot in a queue. They never touch each other, and an 86-second sim-day in
one of them cannot freeze the API or anybody else's map.

Until then this is the empty registry of live state, so the read endpoints can
ask "is this run computing?" without knowing whether the answer can ever be yes.
"""


class RunManager:
    def __init__(self) -> None:
        self._live: dict[str, dict] = {}

    def status(self, run_id: str) -> dict:
        """`{}` for a run nobody is computing — callers fall back to the
        record's durable status."""
        return self._live.get(run_id, {})

    def any_live(self) -> list[str]:
        return list(self._live)

    def stop_all(self) -> None:
        """Called on shutdown. No-op until there are workers to stop."""
        self._live.clear()
