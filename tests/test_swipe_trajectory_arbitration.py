"""Swipe vs trajectory-gesture arbitration regression tests (cases A–N).

Regression: candidate-based pre-suppression starved the swipe detector
and templates could fire on swipe-like paths. Now: swipes are always
fed; straight swipes emit instantly; only a provably-curving path holds
a fired swipe (dropped only when it loops or a shape completes);
templates never claim paths with swipe-like straightness.
"""
import math

import pytest

from app.core.events import EventBus
from app.core.types import HandFrame
from app.gestures.engine import GestureEngine
from app.gestures.trajectory import TemplateDetector, build_motion_template

from tests.conftest import make_hand
from tests.test_motion_gestures import FakeRow, timed, triangle_shape, z_shape
from tests.test_trajectory import circle_path, hand_frames


def run_engine(paths, templates=(), settle_empty=6):
    """Feed one or more timed paths; returns emitted gesture names
    (start phase only). Trailing empty frames let held swipes flush."""
    bus = EventBus()
    eng = GestureEngine(bus)
    if templates:
        eng.trajectories.set_templates(list(templates))
    events = []
    bus.subscribe("gesture.event",
                  lambda ev: events.append(ev.gesture)
                  if ev.phase == "start" else None)
    last_ts = 0.0
    for path in paths:
        for hf in hand_frames(path):
            eng.on_hands(hf)
            last_ts = hf.timestamp
    for k in range(settle_empty):
        eng.on_hands(HandFrame(hands=[], timestamp=last_ts + 0.05 * (k + 1),
                               frame_width=640, frame_height=480))
    return eng, events


def swipe_path(direction, t0=10.0, duration=0.3, n=9):
    """Straight swipe in USER direction (mirror-corrected)."""
    dx, dy = {"right": (-0.45, 0), "left": (0.45, 0),
              "up": (0, -0.4), "down": (0, 0.4)}[direction]
    x0, y0 = 0.5 - dx / 2, 0.5 - dy / 2
    return [(t0 + duration * k / n, x0 + dx * k / n, y0 + dy * k / n)
            for k in range(n + 1)]


def curved_swipe(t0=10.0):
    """Rightward (user) swipe with a natural sag — moderate curve."""
    return [(t0 + 0.35 * k / 8, 0.75 - 0.45 * k / 8,
             0.5 + 0.05 * math.sin(math.pi * k / 8)) for k in range(9)]


Z_TMPL = FakeRow("my_z", build_motion_template([z_shape()]))
HOOK_PTS = ([(0.3 + 0.35 * k / 12, 0.5) for k in range(13)]
            + [(0.65 + 0.06 * math.sin(t), 0.5 - 0.06 * (1 - math.cos(t)))
               for t in [math.pi * k / 8 for k in range(1, 9)]])
HOOK_TMPL = FakeRow("hook", build_motion_template([HOOK_PTS]))


# -- A-D: pure swipes ---------------------------------------------------------
@pytest.mark.parametrize("direction", ["left", "right", "up", "down"])
def test_pure_swipe_each_direction(direction):
    _, events = run_engine([swipe_path(direction)])
    swipes = [g for g in events if g.startswith("swipe")]
    assert swipes == [f"swipe_{direction}"]
    assert "circle" not in events


# -- E/F: swipes with templates loaded ---------------------------------------
def test_swipe_with_templates_loaded():
    for d in ("left", "right", "up", "down"):
        _, events = run_engine([swipe_path(d)], templates=[Z_TMPL, HOOK_TMPL])
        swipes = [g for g in events if g.startswith("swipe")]
        assert swipes == [f"swipe_{d}"], d
        assert "my_z" not in events and "hook" not in events


def test_curved_swipe_near_hook_template_still_fires():
    """A swipe resembling a recorded hook: swipe wins (satisfies swipe
    criteria); the template must not fire on a swipe-like path."""
    _, events = run_engine([curved_swipe()], templates=[HOOK_TMPL])
    swipes = [g for g in events if g.startswith("swipe")]
    assert swipes == ["swipe_right"]
    assert "hook" not in events


# -- G/H: shapes still complete ----------------------------------------------
def test_circle_only_no_swipe():
    _, events = run_engine([circle_path(rx=0.14)], templates=[Z_TMPL])
    assert events.count("circle") == 1
    assert not any(g.startswith("swipe") for g in events)


def test_custom_trajectory_only():
    """Drawn at a deliberate pace (below swipe speed) → template only."""
    slow_z = timed(z_shape(), duration=1.9)
    _, events = run_engine([slow_z], templates=[Z_TMPL])
    assert events.count("my_z") == 1
    assert not any(g.startswith("swipe") for g in events)


# -- I/J: shapes must not fire on swipes --------------------------------------
def test_circle_not_fired_by_swipe():
    for d in ("left", "right", "up", "down"):
        _, events = run_engine([swipe_path(d)])
        assert "circle" not in events


def test_template_not_fired_by_swipe():
    _, events = run_engine([curved_swipe()], templates=[Z_TMPL, HOOK_TMPL])
    assert "my_z" not in events and "hook" not in events


# -- K: static + swipe interaction --------------------------------------------
def test_static_gesture_with_swipe():
    hold = [(10.0 + k / 30, 0.5, 0.5) for k in range(12)]
    # swipe continues from the held position (no teleport between paths);
    # image-x increasing = user swiping left (mirrored preview)
    swipe = [(10.45 + 0.3 * k / 9, 0.5 + 0.42 * k / 9, 0.5)
             for k in range(10)]
    _, events = run_engine([hold, swipe])
    assert "point" in events                       # static pose tracked
    assert [g for g in events if g.startswith("swipe")] == ["swipe_left"]


# -- L/M: failed motion candidates leave swipes available --------------------
def test_swipe_after_failed_motion_candidate():
    """Half a circle (abandoned), pause, then a clean swipe."""
    half = circle_path(rx=0.14, turns=0.5, t0=10.0, duration=0.6)
    _, events = run_engine([half, swipe_path("right", t0=12.5)],
                           templates=[Z_TMPL])
    assert "circle" not in events and "my_z" not in events
    assert [g for g in events if g.startswith("swipe")] == ["swipe_right"]


def test_candidate_timeout_keeps_swipes_available():
    """A motion that raises a candidate but never completes must not
    lock out later swipes."""
    wiggle = circle_path(rx=0.13, turns=0.6, t0=10.0, duration=0.7)
    eng, events = run_engine([wiggle, swipe_path("left", t0=13.0)])
    assert [g for g in events if g.startswith("swipe")] == ["swipe_left"]
    assert eng._pending_swipe is None              # nothing stuck


# -- N: no duplicates ---------------------------------------------------------
def test_no_duplicate_events():
    _, events = run_engine([swipe_path("right")], templates=[Z_TMPL])
    swipes = [g for g in events if g.startswith("swipe")]
    assert len(swipes) == 1
    _, events2 = run_engine([circle_path(rx=0.14)])
    assert events2.count("circle") == 1


# -- held-swipe mechanics -----------------------------------------------------
def test_curved_swipe_emits_after_hold():
    """A swipe fired on a curving path is held, then emitted when the
    motion ends without looping — never lost."""
    _, events = run_engine([curved_swipe()])
    assert [g for g in events if g.startswith("swipe")] == ["swipe_right"]


def test_loop_drops_held_swipe_but_circle_fires():
    """During a circle the early arc may satisfy swipe criteria; the
    held swipe must be dropped once the path provably loops."""
    eng, events = run_engine([circle_path(rx=0.12)])
    assert events.count("circle") == 1
    assert not any(g.startswith("swipe") for g in events)
    assert eng._pending_swipe is None


def test_template_straightness_bar():
    """TemplateDetector never evaluates swipe-like paths."""
    det = TemplateDetector("hook", HOOK_TMPL.template)
    pts = [(10.0 + 0.35 * k / 12, x, y)
           for k, (x, y) in enumerate(HOOK_PTS[:13])]  # straight run only
    assert det.analyze(pts) is None