"""Gesture Studio 2.0 — calibration, diagnostics & safety.

Diagnostics are read-only projections of EXISTING engine state; the lock
and neutral gate reuse the controller/engine lifecycle. No recognition
algorithm is changed — swipe/compound regressions are asserted here too.
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
from app.core.types import GestureEvent, Hand, HandFrame, Landmark
from app.data.db import Database
from app.gestures import presets
from app.gestures.engine import GestureEngine
from app.runtime.controller import MotionController


# ------------------------------------------------------------ presets --------
def test_preset_tables_use_existing_keys():
    for key, allowed in (("pinch", {"confidence", "cooldown_ms"}),
                         ("swipe", {"min_distance", "min_speed",
                                    "min_duration_ms", "max_duration_ms",
                                    "cooldown_ms"}),
                         ("circle", {"confidence", "min_size",
                                     "max_duration_ms", "cooldown_ms"})):
        for name in ("SAFE", "FAST"):
            params = presets.preset_for(key, name)
            assert params and set(params).issubset(allowed)
    assert presets.preset_for("pinch", "BALANCED") == {}   # defaults


def test_preset_safe_stricter_than_fast():
    assert (presets.preset_for("pinch", "SAFE")["confidence"]
            > presets.preset_for("pinch", "FAST")["confidence"])
    assert (presets.preset_for("circle", "SAFE")["cooldown_ms"]
            > presets.preset_for("circle", "FAST")["cooldown_ms"])


def test_reset_restores_detector_defaults():
    """Clearing an override must RESTORE detector defaults, not leave the
    last applied preset (regression found in live validation)."""
    bus = EventBus()
    eng = GestureEngine(bus)
    circle = eng.trajectories.detector("circle")
    default_conf = circle.min_confidence
    default_speed = eng.swipes.min_speed
    eng.apply_gesture_settings({"circle": presets.preset_for("circle",
                                                             "SAFE"),
                                "swipe": presets.preset_for("swipe", "FAST")})
    assert circle.min_confidence != default_conf
    assert eng.swipes.min_speed != default_speed
    eng.apply_gesture_settings({})              # reset ALL
    assert circle.min_confidence == default_conf
    assert eng.swipes.min_speed == default_speed


def test_preset_preview_rows():
    rows = presets.preview("swipe", "SAFE")
    assert any(label == "Cooldown" for label, _ in rows)
    assert presets.preview("pinch", "BALANCED") == [("All parameters",
                                                     "default")]


# --------------------------------------------------- engine diagnostics ------
def _hand(landmarks=None):
    lm = landmarks or [Landmark(0.5, 0.5, 0.0) for _ in range(21)]
    return Hand(landmarks=lm, handedness="Right", confidence=0.9)


def _frame(hands, ts):
    return HandFrame(hands=hands, timestamp=ts, frame_width=640,
                     frame_height=480)


def test_diagnostics_snapshot_shape():
    bus = EventBus()
    eng = GestureEngine(bus)
    d = eng.diagnostics("circle", now=0.0)
    assert set(d) >= {"state", "confidence", "threshold",
                      "cooldown_remaining_ms", "tracking", "reason"}
    assert d["state"] == "NO TRACKING"     # nothing seen yet
    assert d["confidence"] is None         # never fabricated


def test_diagnostics_threshold_by_kind():
    bus = EventBus()
    eng = GestureEngine(bus, confidence_threshold=0.7)
    assert eng.threshold_for("swipe_left") == 1.0     # rule-based
    assert eng.threshold_for("pinch") == 0.7
    assert 0.0 < eng.threshold_for("circle") <= 1.0   # detector sensitivity


def test_cooldown_remaining_counts_down():
    bus = EventBus()
    eng = GestureEngine(bus, cooldown_ms=1000)
    st = eng._state("pinch")
    st.last_end = 10.0
    assert eng.cooldown_remaining("pinch", now=10.4) == pytest.approx(0.6,
                                                                      abs=0.01)
    assert eng.cooldown_remaining("pinch", now=11.5) == 0.0
    # reflected as a COOLDOWN state
    eng.current_gesture = "none"
    eng._tracking = True
    assert eng.diagnostics("pinch", now=10.4)["state"] == "COOLDOWN"


def test_diagnostics_match_state_uses_current():
    bus = EventBus()
    eng = GestureEngine(bus)
    eng._tracking = True
    eng.current_gesture = "circle"
    eng.current_confidence = 0.9
    d = eng.diagnostics("circle", now=0.0)
    assert d["state"] == "MATCH" and d["confidence"] == 0.9


# --------------------------------------------- neutral-before-retrigger ------
def _straight_swipe_frames(t0=0.0):
    """A fast left→right wrist motion that the swipe detector fires on."""
    frames = []
    for i in range(6):
        x = 0.2 + i * 0.12
        lm = [Landmark(x, 0.5, 0.0) for _ in range(21)]
        frames.append(_frame([_hand(lm)], t0 + i * 0.02))
    return frames


def test_neutral_gate_off_does_not_touch_swipes():
    """Regression: the neutral setting must never suppress swipes."""
    bus = EventBus()
    events = []
    bus.subscribe("gesture.event", lambda e: events.append((e.gesture,
                                                             e.phase)))
    eng = GestureEngine(bus)
    eng.require_neutral = True          # ON — swipes still must fire
    for f in _straight_swipe_frames():
        eng.on_hands(f)
    swipes = [g for g, p in events if g.startswith("swipe") and p == "start"]
    assert swipes, "swipe was suppressed by the neutral gate"


def test_neutral_gate_blocks_repeated_shape_until_neutral():
    bus = EventBus()
    eng = GestureEngine(bus)
    eng.require_neutral = True
    fired = []

    # stub the trajectory detector to "complete a circle" on demand
    def fake_update(x, y, ts):
        return ("circle", 0.95) if getattr(eng, "_want_circle", False) \
            else None
    with patch.object(eng.trajectories, "update", side_effect=fake_update), \
         patch.object(eng, "_threshold_for_shape", lambda g: 0.5):
        bus.subscribe("gesture.event",
                      lambda e: fired.append((e.gesture, e.phase)))
        eng._want_circle = True
        eng.on_hands(_frame([_hand()], 0.0))          # circle #1 → fires
        eng.on_hands(_frame([_hand()], 0.05))         # still non-neutral
        n_after_two = sum(1 for g, p in fired
                          if g == "circle" and p == "start")
        assert n_after_two == 1                       # 2nd suppressed
        # neutral frame (no hand) clears the block
        eng._want_circle = False
        eng.on_hands(_frame([], 0.1))
        eng._want_circle = True
        eng.on_hands(_frame([_hand()], 0.15))         # fires again
        n_final = sum(1 for g, p in fired
                      if g == "circle" and p == "start")
    assert n_final == 2


# ---------------------------------------------------- controller lock --------
class FakeCap:
    def isOpened(self): return True
    def read(self):
        time.sleep(0.003)
        return True, np.zeros((48, 64, 3), dtype=np.uint8)
    def release(self): pass


class StubTracker:
    def start(self): pass
    def stop(self): pass


@pytest.fixture
def ctl(tmp_path, monkeypatch):
    monkeypatch.setenv("MGA_DATA_DIR", str(tmp_path))
    cfg = Config()
    cfg.default_cooldown_ms = 0
    cfg.action_global_cooldown_ms = 0
    db = Database(tmp_path / "s2.db")
    bus = EventBus()
    c = MotionController(cfg, db, bus)
    c.camera = CameraWorker(bus, opener=lambda *a: FakeCap(), target_fps=120)
    c.tracker = StubTracker()
    return c, bus, db


def _map(c, gesture="circle", key="k"):
    pm = c.profile_manager
    a = pm.actions.create(f"A_{key}", "key_press", {"key": key})
    gid = pm.profiles.by_name("Global").id
    pm.rules.create(gid, gesture, a)
    c.reload_rules()


def _fire_gesture(c, gesture="circle"):
    c._on_gesture(GestureEvent(gesture, "start", 0.95, "Right", 0.5, 0.5,
                               time.monotonic()))


def test_lock_suppresses_execution_recognition_continues(ctl):
    c, bus, db = ctl
    _map(c)
    c.set_motion_enabled(True)
    blocked = []
    bus.subscribe("gesture.blocked", lambda *a: blocked.append(a))
    keys = []
    with patch.object(iw, "key_press", lambda k: keys.append(k)):
        c.set_gestures_locked(True)
        _fire_gesture(c)                     # recognized but locked
        assert keys == [] and blocked        # nothing executed
        c.set_gestures_locked(False)         # unlock
        _fire_gesture(c)
    assert keys == ["k"]                      # runs after unlock
    c.shutdown()
    db.close()


def test_lock_state_event_published(ctl):
    c, bus, db = ctl
    states = []
    bus.subscribe("control.locked", lambda v: states.append(v))
    c.set_gestures_locked(True)
    c.set_gestures_locked(False)
    assert states == [True, False]
    c.shutdown()
    db.close()


def test_emergency_stop_overrides_lock(ctl):
    c, bus, db = ctl
    _map(c)
    c.set_gestures_locked(False)
    c.set_motion_enabled(False)              # E-stop
    keys = []
    with patch.object(iw, "key_press", lambda k: keys.append(k)):
        _fire_gesture(c)                     # motion off → ignored entirely
    assert keys == []
    c.shutdown()
    db.close()


def test_disabled_mapping_never_executes_under_diagnostics(ctl):
    c, bus, db = ctl
    pm = c.profile_manager
    a = pm.actions.create("A", "key_press", {"key": "k"})
    gid = pm.profiles.by_name("Global").id
    rid = pm.rules.create(gid, "circle", a, enabled=False)
    c.reload_rules()
    c.set_motion_enabled(True)
    keys = []
    with patch.object(iw, "key_press", lambda k: keys.append(k)):
        _fire_gesture(c)
    assert keys == []                         # disabled rule → no run
    c.shutdown()
    db.close()


def test_require_neutral_setter_updates_engine(ctl):
    c, bus, db = ctl
    assert c.gestures.require_neutral is False
    c.set_require_neutral(True)
    assert c.gestures.require_neutral is True
    c.shutdown()
    db.close()


# ------------------------------------------------------ circle diagnostics ---
def test_circle_detector_records_diagnostics():
    from app.gestures.trajectory import CircleDetector
    import math
    det = CircleDetector()
    pts = []
    for i in range(40):
        a = 2 * math.pi * i / 39
        pts.append((i * 0.02, 0.5 + 0.15 * math.cos(a),
                    0.5 + 0.15 * math.sin(a)))
    res = det.analyze(pts)
    assert res is not None and res[0] == "circle"
    assert det.last["result"] == "MATCH"
    assert det.last["direction"] in ("CW", "CCW")
    assert det.last["movement"] == "VALID"


# ------------------------------------------------------ UI smoke -------------
def test_studio_safety_bar_and_diagnostic_dialog(tmp_path, monkeypatch):
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    from app.ui.bridge import QtBridge
    from app.ui.gestures_page import GesturesPage
    from app.ui.studio import TestRecognitionDialog
    monkeypatch.setenv("MGA_DATA_DIR", str(tmp_path))
    app = QApplication.instance() or QApplication([])
    cfg = Config()
    db = Database(tmp_path / "ui.db")
    bus = EventBus()
    c = MotionController(cfg, db, bus)
    bridge = QtBridge(bus)
    page = GesturesPage(c, bridge)
    # lock button toggles controller state + label
    page.lock_btn.setChecked(True)
    page._toggle_lock()
    assert c.gestures_locked is True
    assert "LOCKED" in page.lock_btn.text()
    page.lock_btn.setChecked(False)
    page._toggle_lock()
    assert c.gestures_locked is False
    # neutral checkbox drives the engine flag
    page.neutral_check.setChecked(True)
    assert c.gestures.require_neutral is True
    # diagnostic dialog builds and ticks read-only (nothing executes)
    dlg = TestRecognitionDialog(c, bridge, "circle", page)
    c.gestures.current_gesture = "circle"
    c.gestures.current_confidence = 0.9
    c.gestures._tracking = True
    dlg._tick()
    assert dlg.state_lbl.text() in ("MATCH", "TRACKING", "WAITING",
                                    "CANDIDATE", "COOLDOWN", "NO TRACKING")
    assert not dlg.circle_box.isHidden()      # circle diagnostics shown
    dlg.done(0)
    c.shutdown()
    db.close()
