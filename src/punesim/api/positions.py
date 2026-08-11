"""Where everybody is, as bytes.

The old endpoint returned JSON objects and therefore could not return everybody:
49,578 people with a name and a place name each is about 8.7 MB per scrub of the
timeline, so it capped at the first 4,000 by id and drew a twelfth of the city.

Nothing about the map needs a name to draw a dot. It needs a position and a
colour, and the browser already has the roster. So this sends three parallel
arrays with a small header and no per-person object at all:

    magic 'PSPO' | u16 version | u16 flags | u32 count | u32 tick
    Float32[count * 2]   lon, lat interleaved
    Uint8[count]         activity code

That is 9 bytes a person against roughly 180 as JSON — 450 KB for the whole city,
which decodes into typed arrays deck.gl can hand straight to the GPU. The index
into these arrays is the person's ordinal in the run's sorted roster, which the
client fetches once; nothing has to be looked up per frame.
"""

import struct

MAGIC = b"PSPO"
VERSION = 1

# What somebody is doing, as one byte. The map colours by this, so the list is
# about visual distinction rather than completeness — every activity the sim
# emits that is not named here is simply "somewhere, doing something".
ACTIVITY_CODES = {
    "": 0, "home": 0, "sleep": 0, "rest": 0,
    "commute": 1, "transit": 1,
    "work": 2,
    "school": 3, "study": 3,
    "market": 4, "shop": 4, "errand": 4,
    "worship": 5, "temple": 5,
    "hospital": 6, "clinic": 6,
    "social": 7, "visit": 7,
}
CODE_TRANSIT = 1
CODE_OTHER = 8


def code_for(state: str, activity: str | None) -> int:
    """One byte for what this person is up to."""
    if state == "transit":
        return CODE_TRANSIT
    return ACTIVITY_CODES.get((activity or "").lower(), CODE_OTHER)


def encode(rows: list[tuple[float, float, int]], tick: int) -> bytes:
    """(lon, lat, code) per person, in roster order, as one buffer.

    A person the log cannot place — no home, unresolvable place — is written as
    a NaN position rather than skipped, because skipping would shift every
    ordinal after them and silently mislabel the rest of the city. The client
    drops NaNs at draw time.
    """
    n = len(rows)
    head = struct.pack("<4sHHII", MAGIC, VERSION, 0, n, tick)
    coords = bytearray(8 * n)
    codes = bytearray(n)
    for i, (lon, lat, c) in enumerate(rows):
        struct.pack_into("<ff", coords, i * 8, lon, lat)
        codes[i] = c
    return head + bytes(coords) + bytes(codes)


def decode(buf: bytes) -> tuple[int, int, list[tuple[float, float, int]]]:
    """Inverse of `encode` — for tests, and for anyone debugging in Python."""
    magic, version, _flags, n, tick = struct.unpack_from("<4sHHII", buf, 0)
    if magic != MAGIC:
        raise ValueError(f"not a positions buffer: {magic!r}")
    if version != VERSION:
        raise ValueError(f"positions version {version}, this reader speaks {VERSION}")
    off = 16
    out = []
    for i in range(n):
        lon, lat = struct.unpack_from("<ff", buf, off + i * 8)
        out.append((lon, lat, buf[off + 8 * n + i]))
    return version, tick, out


def snapshot(world, t: int) -> bytes:
    """The whole city at one moment, encoded.

    `LogView.pos` is the same call the old endpoint made per person; what
    changed is that it is now made for everybody and the result never becomes a
    dict. At 49,578 people this is ~150 ms, which is well inside the 1–2 Hz a
    live map refreshes at, and scrubbing reads cached keyframes on the client.
    """
    view = world.view
    rows: list[tuple[float, float, int]] = []
    nan = float("nan")
    for pid in world.order:
        r = view.pos(pid, t)
        if r is None:
            rows.append((nan, nan, CODE_OTHER))
            continue
        lat, lon, state, _at, activity = r
        rows.append((lon, lat, code_for(state, activity)))
    return encode(rows, t)
