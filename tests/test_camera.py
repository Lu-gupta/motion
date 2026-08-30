"""Camera worker lifecycle with a fake capture source (no hardware)."""
import threading
import time

import numpy as np

from app.camera.capture import CameraWorker
from app.core.events import EventBus
from app.core.types import CameraStatus


class FakeCap:
    def __init__(self, frames=30, fail_after=None):
        self.frames = frames
        self.count = 0
        self.fail_after = fail_after
        self.released = False

    def isOpened(self):
        return True

    def read(self):
        self.count += 1
        if self.fail_after is not None and self.count > self.fail_after:
            return False, None
        if self.count > self.frames:
            time.sleep(0.01)
        return True, np.zeros((48, 64, 3), dtype=np.uint8)

    def release(self):
        self.released = True


class DeadCap:
    def isOpened(self):
        return False

    def release(self):
        pass


def test_worker_delivers_frames_and_stops():
    bus = EventBus()
    got = []
    bus.subscribe("camera.frame", lambda f, ts: got.append(ts))
    cap = FakeCap()
    w = CameraWorker(bus, opener=lambda i, wd, h: cap, target_fps=120)
    w.start()
    time.sleep(0.3)
    w.stop()
    assert len(got) > 5
    assert cap.released
    assert w.status == CameraStatus.STOPPED


def test_worker_reports_disconnect_and_recovers():
    bus = EventBus()
    statuses = []
    bus.subscribe("camera.status", lambda s, d: statuses.append(s))
    caps = [FakeCap(fail_after=3), FakeCap()]
    idx = {"i": 0}

    def opener(i, wd, h):
        c = caps[min(idx["i"], 1)]
        idx["i"] += 1
        return c

    w = CameraWorker(bus, opener=opener, target_fps=120)
    w.RECONNECT_DELAYS = [0.05]
    w.start()
    time.sleep(0.6)
    w.stop()
    assert CameraStatus.DISCONNECTED in statuses
    # recovered: connected published at least twice
    assert statuses.count(CameraStatus.CONNECTED) >= 2


def test_worker_survives_unopenable_camera():
    bus = EventBus()
    statuses = []
    bus.subscribe("camera.status", lambda s, d: statuses.append(s))
    w = CameraWorker(bus, opener=lambda i, wd, h: DeadCap(), target_fps=30)
    w.RECONNECT_DELAYS = [0.05]
    w.start()
    time.sleep(0.3)
    w.stop()
    assert CameraStatus.DISCONNECTED in statuses
    # thread exited cleanly
    assert w._thread is None
