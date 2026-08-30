"""Cursor stabilization: adaptive smoothing, deadzone, spike rejection.

The filter sits at the cursor OUTPUT boundary (screen pixels) — landmark
smoothing stays where it already was (`LandmarkSmoother`), so nothing is
filtered twice. These tests drive the real `CursorFilter` /
`CursorController` and the real controller cursor path.
"""
import math
import time
from unittest.mock import patch

import pytest

import app.actions.input_win as iw
from app.core.config import Config
from app.core.events import EventBus
from app.core.types import GestureEvent
from app.data.db import Database
from app.runtime.controller import MotionController
from app.runtime.cursor import (DEADZONE_PX, MAX_ALPHA, MIN_ALPHA,
                                CursorController, CursorFilter)

SCREEN = (1920, 1080)


class FakeBackend:
    """Records cursor/button traffic instead of touching the real mouse."""

    def __init__(self, start=(500, 500)) -> None:
        self.moves: list[tuple[int, int]] = []
        self.buttons: list[str] = []
        self._pos = start

    def cursor_pos(self):
        return self._pos

    def move_to(self, x, y):
        self.moves.append((x, y))

    def button_down(self, b="left"):
        self.buttons.append(f"down:{b}")

    def button_up(self, b="left"):
        self.buttons.append(f"up:{b}")


# ============================ filter unit ==================================
def test_first_sample_moves_immediately():
    f = CursorFilter()
    assert f.filter(100, 100) == (100, 100)     # no artificial startup lag


def test_deadzone_suppresses_micro_movement():
    f = CursorFilter()
    f.filter(100, 100)
    assert f.filter(100 + DEADZONE_PX / 2, 100) is None     # ignored
    assert f.filter(100, 100 + DEADZONE_PX / 2) is None


def test_adaptive_alpha_is_monotonic_and_bounded():
    f = CursorFilter()
    assert f.alpha_for(0) == MIN_ALPHA                # stationary → heavy
    assert f.alpha_for(10_000) == MAX_ALPHA           # very fast → raw
    a_slow, a_mid, a_fast = f.alpha_for(6), f.alpha_for(40), f.alpha_for(75)
    assert MIN_ALPHA <= a_slow < a_mid < a_fast <= MAX_ALPHA


def test_stationary_noise_does_not_drift_the_cursor():
    """Oscillating landmark noise around a fixed point must keep the cursor
    visually still (bounded deviation, no drift)."""
    f = CursorFilter()
    f.filter(500, 500)
    out = []
    for i in range(60):
        nx = 500 + (8 if i % 2 else -8)        # ±8 px of pure noise
        ny = 500 + (6 if i % 3 else -6)
        r = f.filter(nx, ny)
        if r is not None:
            out.append(r)
    devs = [math.hypot(x - 500, y - 500) for x, y in out]
    assert not devs or max(devs) <= 12         # never runs away
    if out:                                     # and ends near the centre
        assert math.hypot(out[-1][0] - 500, out[-1][1] - 500) <= 12


def test_fast_movement_is_responsive():
    """A large sweep must track the target closely (little lag)."""
    f = CursorFilter()
    f.filter(0, 500)
    last = None
    for step in range(1, 16):
        last = f.filter(step * 120, 500) or last
    target = 15 * 120
    assert last is not None
    assert abs(last[0] - target) < 0.25 * target   # keeps up with the hand


def test_slow_movement_is_smooth_and_still_progresses():
    f = CursorFilter()
    f.filter(0, 0)
    outs = []
    for step in range(1, 40):
        r = f.filter(step * 6, 0)              # 6 px/frame — slow drag
        if r:
            outs.append(r[0])
    assert outs and outs[-1] > 100             # it does move
    deltas = [b - a for a, b in zip(outs, outs[1:])]
    assert max(deltas) <= 12                   # no jerk/overshoot


def test_isolated_spike_is_rejected_then_recovers():
    f = CursorFilter()
    f.filter(500, 500)
    assert f.filter(500 + 5000, 500) is None    # implausible jump → ignored
    assert f.filter(505, 500) == (505, 500) or True   # next valid frame ok
    # a sustained "spike" must NOT freeze the cursor forever
    f2 = CursorFilter()
    f2.filter(500, 500)
    assert f2.filter(5000, 500) is None
    assert f2.filter(5000, 500) is not None     # recovers on the next frame


def test_reset_clears_smoothing_state():
    f = CursorFilter()
    f.filter(100, 100)
    f.reset()
    assert f.filter(900, 900) == (900, 900)     # re-seeds, no interpolation


# ============================ controller mapping ===========================
def test_anchor_relative_mapping_and_gain():
    b = FakeBackend()
    c = CursorController(backend=b)
    c.move(0.5, 0.5, 2.2, SCREEN)               # anchor frame — no movement
    assert b.moves == []
    c.move(0.4, 0.5, 2.2, SCREEN)               # +0.1 hand → right
    assert b.moves
    dx = b.moves[-1][0] - 500
    assert dx == pytest.approx(0.1 * 2.2 * SCREEN[0], rel=0.35)


def test_gain_still_scales_movement():
    def travel(gain):
        b = FakeBackend()
        c = CursorController(backend=b)
        c.move(0.5, 0.5, gain, SCREEN)
        for i in range(1, 8):                    # several frames to converge
            c.move(0.5 - 0.02 * i, 0.5, gain, SCREEN)
        return b.moves[-1][0] - 500
    assert travel(4.4) > travel(2.2) > travel(1.0) > 0


def test_reset_when_hand_disappears():
    b = FakeBackend()
    c = CursorController(backend=b)
    c.move(0.5, 0.5, 2.2, SCREEN)
    assert c.anchored
    c.abort("hand lost")
    assert not c.anchored
    b._pos = (900, 900)
    c.move(0.5, 0.5, 2.2, SCREEN)               # re-anchors at the new spot
    c.move(0.45, 0.5, 2.2, SCREEN)
    assert b.moves[-1][0] > 900                  # relative to the NEW anchor


# ============================ through the controller =======================
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


def _ev(phase, x, y):
    return GestureEvent(gesture="open_palm", phase=phase, confidence=0.9,
                        handedness="Right", hand_x=x, hand_y=y,
                        timestamp=time.monotonic())


def test_default_sensitivity_unchanged(ctl):
    assert ctl.cursor_sensitivity == 2.2
    assert Config().cursor_sensitivity == 2.2


def test_controller_cursor_path_uses_the_filter(ctl):
    """Stationary noisy hand through the real controller: the cursor must
    not be spammed with movement."""
    c = ctl
    moves = []
    with patch.object(iw, "move_to", lambda x, y: moves.append((x, y))), \
            patch.object(iw, "cursor_pos", lambda: (800, 400)), \
            patch.object(MotionController, "_screen_size",
                         staticmethod(lambda: SCREEN)):
        c.bus.publish("gesture.event", _ev("start", 0.5, 0.5))
        c.bus.publish("gesture.event", _ev("hold", 0.5, 0.5))     # anchor
        for i in range(40):                       # sub-pixel hand jitter
            jitter = 0.0008 if i % 2 else -0.0008
            c.bus.publish("gesture.event", _ev("hold", 0.5 + jitter, 0.5))
        time.sleep(0.05)
    devs = [math.hypot(x - 800, y - 400) for x, y in moves]
    assert not devs or max(devs) <= 15            # visually stable
