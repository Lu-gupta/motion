"""Application shutdown / tray lifecycle tests.

Fake camera source + stub tracker — no hardware, no MediaPipe model.
"""
import threading
import time
from unittest.mock import patch

import numpy as np
import pytest

import app.actions.input_win as iw
from app.camera.capture import CameraWorker
from app.core.config import Config
from app.core.events import EventBus
from app.core.types import CameraStatus
from app.data.db import Database
from app.runtime.controller import MotionController


class FakeCap:
    def __init__(self):
        self.released = False

    def isOpened(self):
        return True

    def read(self):
        time.sleep(0.005)
        return True, np.zeros((48, 64, 3), dtype=np.uint8)

    def release(self):
        self.released = True


class StubTracker:
    def __init__(self):
        self.started = False

    def start(self):
        self.started = True

    def stop(self):
        self.started = False


@pytest.fixture
def ctl(tmp_path, monkeypatch):
    monkeypatch.setenv("MGA_DATA_DIR", str(tmp_path))
    cfg = Config()
    cfg.default_cooldown_ms = 0
    cfg.action_global_cooldown_ms = 0
    db = Database(tmp_path / "sd.db")
    bus = EventBus()
    c = MotionController(cfg, db, bus)
    c.workflows.POLL_S = 0.03
    fake = FakeCap()
    c.camera = CameraWorker(bus, opener=lambda *a: fake, target_fps=120)
    c.tracker = StubTracker()
    c._fake_cap = fake
    return c, bus


def alive_threads(prefixes=("camera", "context", "workflow")):
    return [t for t in threading.enumerate()
            if any(t.name.startswith(p) for p in prefixes) and t.is_alive()]


def test_shutdown_idempotent(ctl):
    c, _ = ctl
    c.shutdown()
    c.shutdown()
    c.shutdown()            # must never raise
    assert c._shutdown


def test_shutdown_during_camera_capture(ctl):
    c, bus = ctl
    c.start()
    time.sleep(0.2)
    assert c.camera.status == CameraStatus.CONNECTED
    assert alive_threads(("camera", "context"))
    c.shutdown()
    time.sleep(0.1)
    assert c.camera.status == CameraStatus.STOPPED
    assert c._fake_cap.released
    assert not c.running
    assert not c.tracker.started
    assert alive_threads(("camera", "context")) == []


def test_shutdown_disarms_and_clears_arbiter(ctl):
    c, _ = ctl
    c.set_motion_enabled(True)
    c.shutdown()
    assert c.motion_enabled is False
    assert c.arbiter.pending_count() == 0


def test_shutdown_cancels_workflow_delay(ctl):
    c, bus = ctl
    keys = []
    pm = c.profile_manager
    a1 = pm.actions.create("K1", "key_press", {"key": "1"})
    a2 = pm.actions.create("K2", "key_press", {"key": "2"})
    wid = c.workflow_repo.create("W", [
        {"type": "action", "action_id": a1},
        {"type": "delay", "ms": 5000},
        {"type": "action", "action_id": a2}])
    done = []
    evt = threading.Event()
    bus.subscribe("workflow.done", lambda *a: (done.append(a), evt.set()))
    with patch.object(iw, "key_press", lambda k: keys.append(k)):
        c.workflows.start(wid)
        t0 = time.monotonic()
        while keys != ["1"] and time.monotonic() - t0 < 2:
            time.sleep(0.01)
        c.shutdown()
        assert evt.wait(2.0)
        time.sleep(0.1)
    assert done[-1][1] == "cancelled"
    assert keys == ["1"]                    # step 3 never ran
    assert alive_threads(("workflow",)) == []


def test_shutdown_cancels_condition_wait(ctl):
    c, bus = ctl
    keys = []
    pm = c.profile_manager
    a = pm.actions.create("K", "key_press", {"key": "k"})
    wid = c.workflow_repo.create("W", [
        {"type": "wait", "condition": "app_running",
         "process": "fake_app_never.exe", "title": "", "timeout_ms": 60_000},
        {"type": "action", "action_id": a}])
    done = []
    evt = threading.Event()
    bus.subscribe("workflow.done", lambda *a: (done.append(a), evt.set()))
    with patch.object(iw, "key_press", lambda k: keys.append(k)):
        c.workflows.start(wid)
        time.sleep(0.15)
        t0 = time.monotonic()
        c.shutdown()
        assert evt.wait(2.0)
        assert time.monotonic() - t0 < 1.5   # immediate, not the 60 s
    assert done[-1][1] == "cancelled" and keys == []


def test_stop_then_shutdown_and_double_stop(ctl):
    c, _ = ctl
    c.start()
    time.sleep(0.1)
    c.stop()
    c.stop()                # double stop is a no-op
    c.shutdown()            # after a manual stop, still clean
    assert not c.running


# -- window / tray (offscreen) ------------------------------------------------
@pytest.fixture(scope="module")
def qapp():
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _make_window(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("MGA_DATA_DIR", str(tmp_path))
    from app.ui.main_window import MainWindow
    cfg = Config()
    db = Database(tmp_path / "ui.db")
    bus = EventBus()
    c = MotionController(cfg, db, bus)
    fake = FakeCap()
    c.camera = CameraWorker(bus, opener=lambda *a: fake, target_fps=120)
    c.tracker = StubTracker()
    win = MainWindow(c, bus)
    return win, c, db


def test_close_to_tray_keeps_running(qapp, tmp_path, monkeypatch):
    win, c, db = _make_window(qapp, tmp_path, monkeypatch)
    c.cfg.minimize_to_tray = True
    c.start()
    time.sleep(0.1)
    win.show()
    win.close()                              # X → hide, not quit
    qapp.processEvents()
    assert not win.isVisible()
    assert win.tray.isVisible()              # tray icon stays
    assert c.running                         # gestures keep working
    assert not c._shutdown
    win._quit()
    win.close()
    db.close()


def test_tray_quit_full_shutdown(qapp, tmp_path, monkeypatch):
    win, c, db = _make_window(qapp, tmp_path, monkeypatch)
    quits = []
    from PySide6.QtWidgets import QApplication
    monkeypatch.setattr(QApplication, "quit",
                        staticmethod(lambda: quits.append(1)))
    c.cfg.minimize_to_tray = True            # quit must bypass tray-minimize
    c.start()
    time.sleep(0.1)
    win._quit()                              # tray "Quit Motion Gesture App"
    assert c._shutdown and not c.running
    assert not win.tray.isVisible()          # tray icon removed
    assert quits == [1]
    win._quit()                              # idempotent, no double teardown
    assert quits == [1]
    win.close()                              # close after quit: accepted
    assert alive_threads(("camera", "context", "workflow")) == []
    db.close()


def test_close_quits_when_tray_disabled(qapp, tmp_path, monkeypatch):
    win, c, db = _make_window(qapp, tmp_path, monkeypatch)
    quits = []
    from PySide6.QtWidgets import QApplication
    monkeypatch.setattr(QApplication, "quit",
                        staticmethod(lambda: quits.append(1)))
    c.cfg.minimize_to_tray = False
    c.start()
    time.sleep(0.1)
    win.show()
    win.close()                              # X → full quit
    qapp.processEvents()
    assert c._shutdown and not c.running
    assert not win.tray.isVisible()
    assert quits == [1]
    db.close()
