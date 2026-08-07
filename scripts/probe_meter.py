"""Measurement plumbing for the scale probe: peak RSS, and a counter wrapped
around the one function whose cost is quadratic in how many people share a room.

Kept out of scale_probe.py so that the thing being measured and the thing doing
the measuring stay separable — a probe that is hard to read is a probe nobody
re-runs.
"""

import ctypes
import ctypes.wintypes as wt
import sys
import tracemalloc
from dataclasses import dataclass, field


class _PROCESS_MEMORY_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("cb", wt.DWORD),
        ("PageFaultCount", wt.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
    ]


def peak_rss_mb() -> float:
    """Peak working set of this process, in MB. 0.0 where unavailable.

    Process-wide and monotonic, so a ladder must run each rung in its own
    process for the number to mean anything (scale_probe.py does).
    """
    if sys.platform == "win32":
        try:
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.GetCurrentProcess.restype = wt.HANDLE
            kernel32.GetCurrentProcess.argtypes = []
            # K32GetProcessMemoryInfo is kernel32's forwarder for the psapi
            # entry point; psapi.dll is a stub on modern Windows and resolving
            # it there silently returned 0, which read as "no memory used".
            get_info = getattr(kernel32, "K32GetProcessMemoryInfo", None) or (
                ctypes.WinDLL("psapi").GetProcessMemoryInfo
            )
            get_info.restype = wt.BOOL
            get_info.argtypes = [wt.HANDLE, ctypes.POINTER(_PROCESS_MEMORY_COUNTERS), wt.DWORD]
            counters = _PROCESS_MEMORY_COUNTERS()
            counters.cb = ctypes.sizeof(counters)
            if get_info(kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
                return counters.PeakWorkingSetSize / 1e6
        except Exception:  # noqa: BLE001,S110 — a probe never fails on its own meter
            pass  # fall through to the POSIX / tracemalloc path below
    try:
        import resource

        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1e3
    except Exception:  # noqa: BLE001
        return (tracemalloc.get_traced_memory()[1] / 1e6) if tracemalloc.is_tracing() else 0.0


@dataclass
class Copresence:
    """What the all-pairs co-presence sweep actually did.

    `windows` is the size of the list that gets materialised and sorted every
    day; `worst_place` is the crowd that produced the most of them. At 80
    households nothing shares a room with more than a handful of people, so
    these numbers are the ones that decide whether the current engine can hold
    a peth.
    """

    calls: int = 0
    windows: int = 0
    max_windows_in_a_day: int = 0
    worst_place: str = ""
    worst_place_windows: int = 0
    max_at_one_place: int = 0  # most people co-present in one place-span set
    per_day: list[int] = field(default_factory=list)

    def install(self):
        """Wrap minds.info._copresence_windows; returns an uninstall callable."""
        from punesim.minds import info as info_mod

        original = info_mod._copresence_windows

        def counting(intervals, *a, **kw):
            out = original(intervals, *a, **kw)
            self.calls += 1
            self.windows += len(out)
            self.per_day.append(len(out))
            self.max_windows_in_a_day = max(self.max_windows_in_a_day, len(out))
            by_place: dict[str, int] = {}
            for _lo, _hi, place, _a, _b in out:
                by_place[place] = by_place.get(place, 0) + 1
            for place, n in by_place.items():
                if n > self.worst_place_windows:
                    self.worst_place, self.worst_place_windows = place, n
            crowd: dict[str, int] = {}
            for spans in intervals.values():
                for place, _a0, _a1 in spans:
                    crowd[place] = crowd.get(place, 0) + 1
            if crowd:
                self.max_at_one_place = max(self.max_at_one_place, max(crowd.values()))
            return out

        info_mod._copresence_windows = counting

        def uninstall():
            info_mod._copresence_windows = original

        return uninstall
