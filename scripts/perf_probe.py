"""Performance probe (spec §19) — measures the real pipeline headless.

Runs camera + hand tracking + gesture engine for N seconds and reports:
camera FPS, vision processing latency, gesture engine latency, rule
resolution latency, action injection latency, CPU time, working-set memory.

Usage:
    .venv\\Scripts\\python.exe scripts\\perf_probe.py [seconds]
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import Config  # noqa: E402
from app.core.events import EventBus  # noqa: E402
from app.core.logging_setup import setup_logging  # noqa: E402
from app.camera.capture import CameraWorker  # noqa: E402
from app.vision.hand_tracker import HandTracker  # noqa: E402
from app.gestures.engine import GestureEngine  # noqa: E402


def working_set_mb() -> float:
    class PMC(ctypes.Structure):
        _fields_ = [("cb", wt.DWORD), ("PageFaultCount", wt.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t)]
    pmc = PMC()
    pmc.cb = ctypes.sizeof(PMC)
    k32 = ctypes.windll.kernel32
    handle = wt.HANDLE(k32.GetCurrentProcess())
    ok = k32.K32GetProcessMemoryInfo(handle, ctypes.byref(pmc), pmc.cb)
    if not ok:
        return 0.0
    return pmc.WorkingSetSize / (1024 * 1024)


def pct(vals, p):
    if not vals:
        return 0.0
    s = sorted(vals)
    return s[min(len(s) - 1, int(len(s) * p))]


def main() -> None:
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 10.0
    setup_logging()
    cfg = Config.load()
    bus = EventBus()

    frame_ts: list[float] = []
    vision_ms: list[float] = []
    gesture_ms: list[float] = []

    tracker = HandTracker(bus, cfg.max_hands, cfg.min_detection_confidence,
                          cfg.min_tracking_confidence,
                          cfg.landmark_smoothing)
    engine = GestureEngine(bus)

    orig_on_hands = engine.on_hands

    def timed_on_hands(hf):
        t0 = time.perf_counter()
        orig_on_hands(hf)
        gesture_ms.append((time.perf_counter() - t0) * 1000)
        vision_ms.append(tracker.process_ms)

    bus.unsubscribe("vision.hands", orig_on_hands)
    bus.subscribe("vision.hands", timed_on_hands)
    bus.subscribe("camera.frame", lambda f, ts: frame_ts.append(ts))

    cam = CameraWorker(bus, cfg.camera_index, cfg.frame_width,
                       cfg.frame_height, cfg.target_fps)
    cpu0 = time.process_time()
    tracker.start()
    cam.start()
    print(f"measuring {seconds:.0f}s …")
    time.sleep(seconds)
    cam.stop()
    tracker.stop()
    cpu_used = time.process_time() - cpu0

    # rule resolution microbench (seeded DB)
    from app.data.db import Database
    from app.profiles.manager import ProfileManager
    from app.rules.engine import RuleEngine
    from app.core.types import Context
    db = Database()
    pm = ProfileManager(db)
    pm.seed_defaults()
    rules = RuleEngine(pm.profiles, pm.rules, pm.actions)
    ctx = Context(application="chrome", process="chrome.exe",
                  window_title="New Tab")
    t0 = time.perf_counter()
    n = 5000
    for _ in range(n):
        rules.resolve("pinch", ctx, (1920, 1080))
    rule_us = (time.perf_counter() - t0) / n * 1e6

    # action injection microbench (harmless 0-px relative move)
    from app.actions import input_win
    t0 = time.perf_counter()
    m = 200
    for _ in range(m):
        input_win.move_rel(0, 0)
    action_us = (time.perf_counter() - t0) / m * 1e6

    if len(frame_ts) >= 2:
        fps = (len(frame_ts) - 1) / (frame_ts[-1] - frame_ts[0])
    else:
        fps = 0.0

    print("\n=== Performance report ===")
    print(f"camera frames:            {len(frame_ts)}  ({fps:.1f} FPS)")
    print(f"vision latency:           mean {statistics.mean(vision_ms):.1f} ms"
          f"   p95 {pct(vision_ms, 0.95):.1f} ms" if vision_ms else
          "vision latency:           no data")
    print(f"gesture engine latency:   mean {statistics.mean(gesture_ms):.2f} ms"
          f"  p95 {pct(gesture_ms, 0.95):.2f} ms" if gesture_ms else
          "gesture engine latency:   no data")
    print(f"rule resolution:          {rule_us:.0f} µs / resolve")
    print(f"action injection:         {action_us:.0f} µs / SendInput")
    print(f"process CPU time:         {cpu_used:.1f} s over {seconds:.0f} s "
          f"wall ({cpu_used / seconds * 100:.0f}% of one core)")
    print(f"working set:              {working_set_mb():.0f} MB")
    db.close()


if __name__ == "__main__":
    main()
