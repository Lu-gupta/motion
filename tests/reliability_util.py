"""Resource-observability helpers for soak/reliability tests.

Test/debug instrumentation only — nothing here is imported by production
code or runs continuously in the app. It samples thread counts, event-bus
subscriptions, live workflow/arbiter/recorder state and a memory proxy so
soak tests can assert resources return to baseline (no monotonic growth).
"""
from __future__ import annotations

import gc
import threading
import time
from dataclasses import dataclass


def thread_names() -> list[str]:
    return [t.name for t in threading.enumerate() if t.is_alive()]


def count_threads(*prefixes: str) -> int:
    names = thread_names()
    return sum(1 for n in names
               if any(n.startswith(p) for p in prefixes))


def wait_threads_drained(prefixes, timeout: float = 3.0) -> bool:
    """Poll until no live thread name starts with any prefix. Uses a real
    wait on thread objects, not a blind sleep."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        live = [t for t in threading.enumerate()
                if t.is_alive() and any(t.name.startswith(p)
                                        for p in prefixes)]
        if not live:
            return True
        for t in live:
            t.join(timeout=max(0.0, deadline - time.monotonic()))
    return count_threads(*prefixes) == 0


def bus_subscription_count(bus, topic: str | None = None) -> int:
    subs = getattr(bus, "_subs", {})
    if topic is not None:
        return len(subs.get(topic, []))
    return sum(len(v) for v in subs.values())


def gc_objects() -> int:
    gc.collect()
    return len(gc.get_objects())


def rss_mb() -> float | None:
    """Resident set size in MB, or None if psutil is unavailable."""
    try:
        import psutil
        return psutil.Process().memory_info().rss / (1024 * 1024)
    except Exception:
        return None


@dataclass
class ResourceProbe:
    threads_total: int
    camera_threads: int
    context_threads: int
    workflow_threads: int
    recorder_threads: int
    bus_subs: int
    gc_objects: int
    rss_mb: float | None

    @classmethod
    def take(cls, bus=None) -> "ResourceProbe":
        return cls(
            threads_total=len([t for t in threading.enumerate()
                               if t.is_alive()]),
            camera_threads=count_threads("camera"),
            context_threads=count_threads("context"),
            workflow_threads=count_threads("workflow"),
            recorder_threads=count_threads("recorder-"),
            bus_subs=bus_subscription_count(bus) if bus is not None else 0,
            gc_objects=gc_objects(),
            rss_mb=rss_mb(),
        )


def assert_no_monotonic_growth(samples: list[int], slack: float = 0.15,
                               label: str = "resource") -> None:
    """Assert the last sample is not meaningfully larger than the median
    of the first few — tolerates fluctuation, catches steady climb."""
    if len(samples) < 3:
        return
    baseline = sorted(samples[:max(3, len(samples) // 3)])
    med = baseline[len(baseline) // 2]
    allowed = med * (1.0 + slack) + 200
    assert samples[-1] <= allowed, (
        f"{label} grew monotonically: baseline~{med}, final={samples[-1]}, "
        f"allowed<={allowed:.0f}; samples={samples}")
