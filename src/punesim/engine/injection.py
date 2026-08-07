from dataclasses import dataclass, field


@dataclass(frozen=True)
class Injection:
    """A user-injected event (provenance='user'), V0-structured."""

    day: int
    time_s: int  # seconds since midnight
    type: str
    place: str | None = None
    participants: tuple[str, ...] = ()
    severity: float | None = None
    payload: dict = field(default_factory=dict)

    @classmethod
    def parse(cls, obj: dict) -> "Injection":
        hh, mm = obj["time"].split(":")
        return cls(
            day=int(obj["day"]),
            time_s=int(hh) * 3600 + int(mm) * 60,
            type=obj["type"],
            place=obj.get("place"),
            participants=tuple(obj.get("participants", [])),
            severity=obj.get("severity"),
            payload=obj.get("payload", {}),
        )
