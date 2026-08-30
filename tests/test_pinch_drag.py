"""Pinch-and-hold drag.

Drag is a stateful CURSOR interaction, not a mapped gesture action: it
reuses the existing pinch detector (`gestures.static.pinch_confidence`),
the existing hand-selection filter and every existing safety gate. These
tests drive real `vision.hands` frames through the real controller, so the
gesture engine runs exactly as in production.

The invariant under test everywhere: **every mouse button_down has exactly
one matching button_up** — no orphaned OS button state, ever.
"""
import time
from unittest.mock import patch

import pytest

import app.actions.input_win as iw
from app.core.config import Config
from app.core.events import EventBus
from app.core.types import CameraStatus, GestureEvent, HandFrame
from app.data.db import Database
from app.gestures.static import pinch_confidence
from app.runtime.arming import ArmState
from app.runtime.controller import MotionController
from app.runtime.cursor import DragMachine, DragState

from tests.conftest import make_hand
from tests.test_hand_selection import (PHYSICAL_LEFT_LABEL,
                                       PHYSICAL_RIGHT_LABEL)

SCREEN = (1920, 1080)


class FakeBackend:
    def __init__(self):
        self.events: list[str] = []

    def button_down(self, b="left"):
        self.events.append(f"down:{b}")

    def button_up(self, b="left"):
        self.events.append(f"up:{b}")


def _balanced(events):
    """True when every down has exactly one following up."""
    depth = 0
    for e in events:
        depth += 1 if e.startswith("down") else -1
        if depth not in (0, 1):
            return False
    return depth == 0


# ============================ state machine unit ===========================
def test_pose_confidences_are_unambiguous():
    assert pinch_confidence(make_hand(pose="pinch")) >= DragMachine.START_CONF
    assert pinch_confidence(make_hand(pose="open")) == 0.0


def _machine(**kw):
    b = FakeBackend()
    return DragMachine(backend=b, **kw), b


def test_stable_pinch_starts_exactly_one_drag():
    m, b = _machine(start_delay_s=0.15)
    assert m.update(1.0, 0.00, True) == ""          # candidate
    assert m.state is DragState.CANDIDATE
    assert m.update(1.0, 0.10, True) == ""          # still too short
    assert m.update(1.0, 0.20, True) == "start"     # held long enough
    assert m.update(1.0, 0.30, True) == ""          # no repeat
    assert m.update(1.0, 0.40, True) == ""
    assert b.events == ["down:left"]


def test_release_produces_exactly_one_mouse_up():
    m, b = _machine(start_delay_s=0.0, release_frames=2)
    m.update(1.0, 0.0, True)
    m.update(1.0, 0.1, True)
    assert m.dragging
    assert m.update(0.0, 0.2, True) == ""           # one frame below → wait
    assert m.update(0.0, 0.3, True) == "end"
    assert m.update(0.0, 0.4, True) == ""           # no second up
    assert b.events == ["down:left", "up:left"]
    assert _balanced(b.events)


def test_pinch_noise_does_not_start_or_flap_a_drag():
    m, b = _machine(start_delay_s=0.15)
    t = 0.0
    for _ in range(20):                             # alternating noise
        m.update(1.0, t, True)
        t += 0.03
        m.update(0.0, t, True)
        t += 0.03
    assert b.events == []                           # never started
    assert m.state is DragState.IDLE


def test_hysteresis_keeps_drag_through_dips():
    m, b = _machine(start_delay_s=0.0, release_conf=0.35, release_frames=2)
    m.update(1.0, 0.0, True)
    m.update(1.0, 0.1, True)
    assert m.dragging
    for i in range(10):                             # noisy but above relaxed
        m.update(0.45, 0.2 + i * 0.03, True)
    assert m.dragging
    assert b.events == ["down:left"]                # no down/up flapping


def test_not_allowed_always_releases():
    m, b = _machine(start_delay_s=0.0)
    m.update(1.0, 0.0, True)
    m.update(1.0, 0.1, True)
    assert m.dragging
    assert m.update(1.0, 0.2, False) == "end"       # safety revoked
    assert not m.button_held
    assert _balanced(b.events)
    assert m.update(1.0, 0.3, False) == ""          # stays released


def test_abort_is_idempotent():
    m, b = _machine(start_delay_s=0.0)
    m.update(1.0, 0.0, True)
    m.update(1.0, 0.1, True)
    assert m.abort("x") is True
    assert m.abort("x") is False                    # nothing left to release
    assert b.events == ["down:left", "up:left"]


# ============================ real controller path =========================
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
    c.set_drag_settings(enabled=True, start_ms=100, release=0.35)
    yield c
    c.shutdown()
    db.close()


class Recorder:
    """Captures every button/cursor/key effect for one scenario."""

    def __init__(self):
        self.buttons: list[str] = []
        self.moves: list[tuple[int, int]] = []
        self.keys: list[str] = []

    def __enter__(self):
        self._p = [
            patch.object(iw, "button_down",
                         lambda b="left": self.buttons.append(f"down:{b}")),
            patch.object(iw, "button_up",
                         lambda b="left": self.buttons.append(f"up:{b}")),
            patch.object(iw, "move_to",
                         lambda x, y: self.moves.append((x, y))),
            patch.object(iw, "cursor_pos", lambda: (500, 500)),
            patch.object(iw, "key_press", lambda k: self.keys.append(k)),
            patch.object(MotionController, "_screen_size",
                         staticmethod(lambda: SCREEN)),
        ]
        for p in self._p:
            p.start()
        return self

    def __exit__(self, *a):
        for p in self._p:
            p.stop()
        return False


def _frame(pose, t, handed=PHYSICAL_RIGHT_LABEL, dx=0.0):
    base = make_hand(pose=pose)
    pts = [(lm.x + dx, lm.y) for lm in base.landmarks]
    return HandFrame(hands=[make_hand(points=pts, handedness=handed)],
                     timestamp=t, frame_width=640, frame_height=480)


def _pinch_hold(c, frames=8, t0=100.0, handed=PHYSICAL_RIGHT_LABEL,
                move=False):
    """Publish a sustained pinch (optionally moving) and return the end ts."""
    t = t0
    for i in range(frames):
        dx = -0.02 * i if move else 0.0
        c.bus.publish("vision.hands", _frame("pinch", t, handed, dx))
        t += 0.05
    return t


def test_pinch_starts_one_drag_and_release_ends_it(ctl):
    c = ctl
    with Recorder() as rec:
        t = _pinch_hold(c)
        assert c.drag_active
        assert rec.buttons == ["down:left"]
        c.bus.publish("vision.hands", _frame("open", t))
        c.bus.publish("vision.hands", _frame("open", t + 0.05))
        assert not c.drag_active
    assert rec.buttons == ["down:left", "up:left"]
    assert _balanced(rec.buttons)


def test_movement_while_pinched_keeps_button_held(ctl):
    c = ctl
    with Recorder() as rec:
        _pinch_hold(c, frames=12, move=True)
        assert c.drag_active
    assert rec.buttons == ["down:left"]              # exactly one press
    assert rec.moves                                  # and the cursor moved


def test_pinch_noise_does_not_spam_clicks(ctl):
    c = ctl
    with Recorder() as rec:
        t = 100.0
        for _ in range(15):
            c.bus.publish("vision.hands", _frame("pinch", t))
            t += 0.03
            c.bus.publish("vision.hands", _frame("open", t))
            t += 0.03
    assert rec.buttons == []                          # no down/up spam


def test_hand_disappearance_releases_button(ctl):
    c = ctl
    with Recorder() as rec:
        t = _pinch_hold(c)
        assert c.drag_active
        c.bus.publish("vision.hands", HandFrame(hands=[], timestamp=t,
                                                frame_width=640,
                                                frame_height=480))
    assert rec.buttons == ["down:left", "up:left"]
    assert not c.drag_active


def test_camera_disconnect_releases_button(ctl):
    c = ctl
    with Recorder() as rec:
        _pinch_hold(c)
        assert c.drag_active
        c.bus.publish("camera.status", CameraStatus.DISCONNECTED, "")
    assert rec.buttons == ["down:left", "up:left"]
    assert not c.drag_active


def test_motion_off_releases_button(ctl):
    c = ctl
    with Recorder() as rec:
        _pinch_hold(c)
        c.set_motion_enabled(False)
    assert rec.buttons == ["down:left", "up:left"]


def test_estop_releases_button(ctl):
    c = ctl
    with Recorder() as rec:
        _pinch_hold(c)
        c.emergency_disable()
    assert rec.buttons == ["down:left", "up:left"]


def test_shutdown_releases_button(ctl):
    c = ctl
    with Recorder() as rec:
        _pinch_hold(c)
        c.shutdown()
    assert rec.buttons == ["down:left", "up:left"]


def test_gesture_lock_releases_and_blocks(ctl):
    c = ctl
    with Recorder() as rec:
        _pinch_hold(c)
        c.set_gestures_locked(True)
        assert rec.buttons == ["down:left", "up:left"]
        _pinch_hold(c, t0=200.0)
        assert not c.drag_active                      # locked → no new drag
    assert rec.buttons == ["down:left", "up:left"]


def test_control_hand_change_releases_button(ctl):
    c = ctl
    with Recorder() as rec:
        _pinch_hold(c)
        c.set_hand_control("left")
    assert rec.buttons == ["down:left", "up:left"]


def test_selected_hand_filtering_respected(ctl):
    c = ctl
    c.set_hand_control("left")
    with Recorder() as rec:
        _pinch_hold(c, handed=PHYSICAL_RIGHT_LABEL)   # non-selected hand
        assert not c.drag_active
        assert rec.buttons == []
        _pinch_hold(c, t0=200.0, handed=PHYSICAL_LEFT_LABEL)
        assert c.drag_active
    assert rec.buttons == ["down:left"]


def test_disarmed_blocks_drag(ctl):
    c = ctl
    c.set_arming_config(arming_enabled=True, arming_gesture="fist")
    assert c.arming.state == ArmState.DISARMED
    with Recorder() as rec:
        _pinch_hold(c)
        assert not c.drag_active                      # gate not bypassed
        assert rec.buttons == []
        c.bus.publish("gesture.event", GestureEvent(
            gesture="fist", phase="start", confidence=0.9,
            handedness=PHYSICAL_RIGHT_LABEL, hand_x=0.5, hand_y=0.5,
            timestamp=time.monotonic()))
        assert c.arming.state == ArmState.ARMED
        _pinch_hold(c, t0=200.0)
        assert c.drag_active
    assert rec.buttons == ["down:left"]


def test_disarm_while_dragging_releases(ctl):
    c = ctl
    c.set_arming_config(arming_enabled=True, arming_gesture="fist")
    c.bus.publish("gesture.event", GestureEvent(
        gesture="fist", phase="start", confidence=0.9,
        handedness=PHYSICAL_RIGHT_LABEL, hand_x=0.5, hand_y=0.5,
        timestamp=time.monotonic()))
    with Recorder() as rec:
        _pinch_hold(c)
        assert c.drag_active
        c.arming.force_disarm("test")
    assert rec.buttons == ["down:left", "up:left"]


def test_drag_does_not_fire_the_pinch_mapping(ctl):
    """Pinch keeps its mapping when drag is OFF, and is consumed when ON —
    so a drag never also runs a click/workflow."""
    c = ctl
    pm = c.profile_manager
    g = pm.profiles.by_name("Global")
    a = pm.actions.create("K", "key_press", {"key": "k"})
    pm.rules.create(g.id, "pinch", a)
    c.reload_rules()

    with Recorder() as rec:                            # drag ENABLED
        _pinch_hold(c, frames=10)
        assert c.drag_active
    assert rec.keys == []                              # mapping suppressed
    assert rec.buttons == ["down:left"]

    c.set_drag_settings(enabled=False)                 # drag OFF
    c.gestures.reset()
    with Recorder() as rec2:
        _pinch_hold(c, t0=300.0, frames=10)
        time.sleep(0.05)
    assert rec2.buttons == []                          # no drag at all
    assert rec2.keys == ["k"]                          # normal mapping runs


def test_disabling_drag_releases_a_held_button(ctl):
    c = ctl
    with Recorder() as rec:
        _pinch_hold(c)
        assert c.drag_active
        c.set_drag_settings(enabled=False)
    assert rec.buttons == ["down:left", "up:left"]


def test_config_defaults_and_backward_compatibility(tmp_path, monkeypatch):
    monkeypatch.setenv("MGA_DATA_DIR", str(tmp_path))
    cfg = Config()
    assert cfg.cursor_drag_enabled is False            # opt-in
    assert cfg.cursor_drag_start_ms == 150
    assert cfg.cursor_drag_release == 0.35
    (tmp_path / "config.json").write_text('{"camera_index": 0}',
                                          encoding="utf-8")
    old = Config.load()                                # pre-feature file
    assert old.cursor_drag_enabled is False
    assert old.cursor_drag_start_ms == 150


def test_invalid_drag_config_falls_back(tmp_path, monkeypatch):
    monkeypatch.setenv("MGA_DATA_DIR", str(tmp_path))
    cfg = Config()
    cfg.cursor_drag_start_ms = "nonsense"
    cfg.cursor_drag_release = None
    db = Database(tmp_path / "x.db")
    c = MotionController(cfg, db, EventBus())
    assert c.cursor.drag.start_delay_s == 0.15
    assert c.cursor.drag.release_conf == 0.35
    c.shutdown()
    db.close()


def test_no_orphaned_button_after_full_lifecycle(ctl):
    """RUN → drag → interrupt → drag → shutdown leaves nothing held."""
    c = ctl
    with Recorder() as rec:
        _pinch_hold(c)
        c.set_motion_enabled(False)
        c.set_motion_enabled(True)
        _pinch_hold(c, t0=200.0)
        c.shutdown()
    assert _balanced(rec.buttons)
    assert not c.cursor.drag.button_held
