"""Cursor-control movement sensitivity.

Exposes the gain cursor control ALREADY used (a hard-coded 2.2) as a
user setting. Deliberately separate from Open-Palm RECOGNITION confidence:
these tests assert that changing sensitivity moves the cursor further per
unit of hand movement while leaving recognition, hand selection and every
safety gate untouched.

Cursor movement is exercised through the real controller with
`input_win.move_to` / `cursor_pos` patched — no real mouse is moved.
"""
import time
from unittest.mock import patch

import pytest

import app.actions.input_win as iw
from app.core.config import (CURSOR_SENSITIVITY_DEFAULT, Config,
                             normalize_cursor_sensitivity)
from app.core.events import EventBus
from app.core.types import GestureEvent, HandFrame
from app.data.db import Database
from app.runtime.arming import ArmState
from app.runtime.controller import MotionController

from tests.conftest import make_hand
from tests.test_hand_selection import (PHYSICAL_LEFT_LABEL,
                                       PHYSICAL_RIGHT_LABEL)

SCREEN = (1920, 1080)


# ============================ config =======================================
def test_default_preserves_current_behavior():
    assert Config().cursor_sensitivity == CURSOR_SENSITIVITY_DEFAULT == 2.2


def test_normalize_fallback_and_clamp():
    assert normalize_cursor_sensitivity(None) == 2.2       # missing
    assert normalize_cursor_sensitivity("nonsense") == 2.2  # invalid
    assert normalize_cursor_sensitivity(float("nan")) == 2.2
    assert normalize_cursor_sensitivity(0.01) == 0.5       # clamped low
    assert normalize_cursor_sensitivity(999) == 6.0        # clamped high
    assert normalize_cursor_sensitivity("3.5") == 3.5      # numeric string
    assert normalize_cursor_sensitivity(1.0) == 1.0


def test_missing_field_in_old_config_file(tmp_path, monkeypatch):
    """An existing config.json written before this feature must load and
    behave exactly as before."""
    monkeypatch.setenv("MGA_DATA_DIR", str(tmp_path))
    (tmp_path / "config.json").write_text('{"camera_index": 0}',
                                          encoding="utf-8")
    cfg = Config.load()
    assert cfg.cursor_sensitivity == 2.2


def test_persistence_round_trip(tmp_path, monkeypatch):
    monkeypatch.setenv("MGA_DATA_DIR", str(tmp_path))
    cfg = Config()
    cfg.cursor_sensitivity = 4.0
    cfg.save()
    assert Config.load().cursor_sensitivity == 4.0


# ============================ movement =====================================
@pytest.fixture
def ctl(tmp_path, monkeypatch):
    monkeypatch.setenv("MGA_DATA_DIR", str(tmp_path))
    cfg = Config()
    cfg.default_cooldown_ms = 0
    cfg.action_global_cooldown_ms = 0
    db = Database(tmp_path / "rt.db")
    c = MotionController(cfg, db, EventBus())
    pm = c.profile_manager
    g = pm.profiles.by_name("Global")
    for r in pm.rules.for_profile(g.id):
        pm.rules.delete(r.id)
    a = pm.actions.create("Cursor", "cursor_control", {})
    pm.rules.create(g.id, "open_palm", a, continuous=True)
    c.reload_rules()
    c.set_motion_enabled(True)
    yield c
    c.shutdown()
    db.close()


def _ev(phase, x, y, ts=None, handed=PHYSICAL_RIGHT_LABEL):
    return GestureEvent(gesture="open_palm", phase=phase, confidence=0.9,
                        handedness=handed, hand_x=x, hand_y=y,
                        timestamp=ts if ts is not None else time.monotonic())


def _drag(c, dx=0.10):
    """Anchor, then move the hand by dx; returns the cursor x delta."""
    moves = []
    with patch.object(iw, "move_to", lambda x, y: moves.append((x, y))), \
            patch.object(iw, "cursor_pos", lambda: (500, 500)), \
            patch.object(MotionController, "_screen_size",
                         staticmethod(lambda: SCREEN)):
        c.bus.publish("gesture.event", _ev("start", 0.5, 0.5))
        time.sleep(0.05)
        c.bus.publish("gesture.event", _ev("hold", 0.5, 0.5))   # anchor
        time.sleep(0.05)
        c.bus.publish("gesture.event", _ev("hold", 0.5 - dx, 0.5))
        time.sleep(0.1)
    return (moves[-1][0] - 500) if moves else 0


def test_default_gain_matches_historical_behavior(ctl):
    dx = 0.10
    delta = _drag(ctl, dx)
    assert delta == int(dx * 2.2 * SCREEN[0])          # unchanged: 2.2 gain


def test_low_and_high_sensitivity_scale_movement(ctl):
    c = ctl
    c.set_cursor_sensitivity(1.0)
    low = _drag(c)
    c.set_cursor_sensitivity(5.0)
    high = _drag(c)
    assert 0 < low < high
    assert high == pytest.approx(low * 5.0, rel=0.02)   # linear in the gain


def test_live_update_without_restart(ctl):
    c = ctl
    before = _drag(c)
    assert c.set_cursor_sensitivity(4.4) == 4.4         # 2× the default
    after = _drag(c)
    assert after == pytest.approx(before * 2.0, rel=0.02)
    assert c.cfg.cursor_sensitivity == 4.4              # persisted to config


def test_invalid_sensitivity_falls_back(ctl):
    assert ctl.set_cursor_sensitivity("bogus") == 2.2
    assert ctl.cursor_sensitivity == 2.2


# ============ hand selection interaction (cursor + routing) ================
def _palm_frames(handed, xs, t0=50.0):
    """Open-palm frames whose wrist walks through xs (real recognition)."""
    base = make_hand(pose="open")
    out = []
    for i, x in enumerate(xs):
        dx = x - base.landmarks[0].x
        pts = [(lm.x + dx, lm.y) for lm in base.landmarks]
        out.append(HandFrame(hands=[make_hand(points=pts, handedness=handed)],
                             timestamp=t0 + i * 0.05, frame_width=640,
                             frame_height=480))
    return out


def _cursor_moves_for(c, mode, handed):
    """Feed real open-palm frames through the engine; count cursor moves."""
    c.set_hand_control(mode)
    c.gestures.reset()
    moves = []
    xs = [0.5] * 5 + [0.45, 0.40, 0.35]
    with patch.object(iw, "move_to", lambda x, y: moves.append((x, y))), \
            patch.object(iw, "cursor_pos", lambda: (500, 500)), \
            patch.object(MotionController, "_screen_size",
                         staticmethod(lambda: SCREEN)):
        for hf in _palm_frames(handed, xs):
            c.gestures.on_hands(hf)
        time.sleep(0.15)
    return moves


def test_selected_physical_hand_drives_cursor(ctl):
    assert _cursor_moves_for(ctl, "left", PHYSICAL_LEFT_LABEL)
    assert _cursor_moves_for(ctl, "right", PHYSICAL_RIGHT_LABEL)
    assert _cursor_moves_for(ctl, "both", PHYSICAL_LEFT_LABEL)


def test_non_selected_hand_cannot_move_cursor(ctl):
    assert _cursor_moves_for(ctl, "left", PHYSICAL_RIGHT_LABEL) == []
    assert _cursor_moves_for(ctl, "right", PHYSICAL_LEFT_LABEL) == []


def test_sensitivity_does_not_change_eligibility(ctl):
    c = ctl
    c.set_cursor_sensitivity(6.0)
    assert c.gestures.hand_control in ("left", "right", "both")
    assert _cursor_moves_for(c, "left", PHYSICAL_RIGHT_LABEL) == []
    assert _cursor_moves_for(c, "left", PHYSICAL_LEFT_LABEL)


def test_hand_selection_does_not_change_sensitivity(ctl):
    c = ctl
    c.set_cursor_sensitivity(3.3)
    c.set_hand_control("left")
    assert c.cursor_sensitivity == 3.3
    c.set_hand_control("both")
    assert c.cursor_sensitivity == 3.3


def test_open_palm_recognition_threshold_untouched(ctl):
    """Changing cursor sensitivity must not alter recognition confidence."""
    c = ctl
    before = (c.gestures.confidence_threshold,
              c.gestures.threshold_for("open_palm"))
    c.set_cursor_sensitivity(5.5)
    assert (c.gestures.confidence_threshold,
            c.gestures.threshold_for("open_palm")) == before


# ============================ safety gates =================================
def test_gesture_lock_blocks_cursor(ctl):
    c = ctl
    c.set_gestures_locked(True)
    assert _drag(c) == 0
    c.set_gestures_locked(False)
    assert _drag(c) != 0


def test_estop_blocks_cursor(ctl):
    c = ctl
    c.emergency_disable()
    assert _drag(c) == 0


def test_motion_off_blocks_cursor(ctl):
    c = ctl
    c.set_motion_enabled(False)
    assert _drag(c) == 0


def test_arming_gate_blocks_cursor(ctl):
    c = ctl
    c.set_arming_config(arming_enabled=True, arming_gesture="fist")
    assert c.arming.state == ArmState.DISARMED
    assert _drag(c) == 0                                  # disarmed: no cursor
    c.bus.publish("gesture.event",
                  GestureEvent(gesture="fist", phase="start", confidence=0.9,
                               handedness=PHYSICAL_RIGHT_LABEL, hand_x=0.5,
                               hand_y=0.5, timestamp=time.monotonic()))
    time.sleep(0.05)
    assert c.arming.state == ArmState.ARMED
    assert _drag(c) != 0                                  # armed: cursor works
