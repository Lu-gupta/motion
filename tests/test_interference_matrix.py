"""Gesture interference / priority matrix — through the REAL GestureEngine.

No faked detector internals: real SwipeDetector + TrajectoryEngine (circle
+ recorded templates) + static classifier over synthetic hand frames. This
locks the documented "Known gesture interactions" (ARCHITECTURE.md) so the
swipe-vs-shape precedence and the straight-first-stroke interaction cannot
silently regress. Behavioral audit coverage — asserts existing behavior,
changes none of it.
"""
import math

import pytest

from app.core.events import EventBus
from app.core.types import HandFrame
from app.gestures.engine import GestureEngine
from app.gestures.trajectory import build_motion_template

import tests.test_motion_gestures as tm


def _engine(templates=None, neutral=False):
    bus = EventBus()
    eng = GestureEngine(bus)
    eng.require_neutral = neutral
    if templates:
        eng.trajectories.set_templates(templates)
    evs = []
    bus.subscribe("gesture.event", lambda e: evs.append((e.gesture, e.phase)))
    return eng, evs


def _run(eng, timed_pts, pose="point"):
    for hf in tm.hand_frames(timed_pts, pose=pose):
        eng.on_hands(hf)


def _starts(evs, g=None):
    return [n for n, p in evs if p == "start" and (g is None or n == g)]


def _zrow():
    return tm.FakeRow("zed", tm.z_template())


def _swipe(dirn, t0=14.0, n=16, step=0.02):
    out = []
    for k in range(n):
        t = t0 + k * 0.02
        if dirn == "right":
            x, y = 0.8 - step * k, 0.5
        elif dirn == "left":
            x, y = 0.2 + step * k, 0.5
        elif dirn == "up":
            x, y = 0.5, 0.8 - step * k
        else:
            x, y = 0.5, 0.2 + step * k
        out.append((t, x, y))
    return out


def _circle(t0=10.0, dur=1.0, turns=1.05, r=0.14, n=31):
    return [(t0 + dur * k / (n - 1),
             0.5 + r * math.cos(2 * math.pi * turns * k / (n - 1)),
             0.5 + r * math.sin(2 * math.pi * turns * k / (n - 1)))
            for k in range(n)]


# 1. static (point pose) then swipe → swipe fires
def test_static_then_swipe():
    e, ev = _engine()
    _run(e, [(9.0 + k * 0.05, 0.5, 0.5) for k in range(6)])
    _run(e, _swipe("right"))
    assert "swipe_right" in _starts(ev)


# 2. swipe with a motion template loaded → swipe fires, template does not
def test_swipe_with_template_loaded():
    e, ev = _engine([_zrow()])
    _run(e, _swipe("left"))
    assert "swipe_left" in _starts(ev) and "zed" not in _starts(ev)


# 3. circle is not misread as a swipe
def test_circle_not_a_swipe():
    e, ev = _engine()
    _run(e, _circle())
    assert _starts(ev, "circle") == ["circle"]
    assert not any(s.startswith("swipe") for s in _starts(ev))


# 4. circle vs recorded template: whichever shape is drawn wins
def test_circle_vs_template_circle_wins_when_circle_drawn():
    e, ev = _engine([_zrow()])
    _run(e, _circle())
    assert "circle" in _starts(ev) and "zed" not in _starts(ev)


def test_circle_vs_template_template_wins_when_shape_drawn():
    e, ev = _engine([_zrow()])
    _run(e, tm.timed(tm.z_shape()))
    assert "zed" in _starts(ev) and "circle" not in _starts(ev)


# 5. drawing the recorded shape resolves the template
def test_template_vs_static():
    e, ev = _engine([_zrow()])
    _run(e, tm.timed(tm.z_shape()))
    assert "zed" in _starts(ev)


# 7. partial/abandoned motion then swipe → swipe still fires
def test_partial_motion_then_swipe():
    e, ev = _engine([_zrow()])
    _run(e, tm.timed(tm.z_shape()[:18], t0=10.0, duration=0.7))
    assert "zed" not in _starts(ev)
    _run(e, _swipe("right", t0=14.0))
    assert "swipe_right" in _starts(ev)


# 8. curved/sloppy swipe still fires
def test_sloppy_swipe_fires():
    e, ev = _engine()
    sloppy = [(14.0 + k * 0.02, 0.8 - 0.02 * k, 0.5 + 0.03 * math.sin(k / 4))
              for k in range(16)]
    _run(e, sloppy)
    assert any(s.startswith("swipe") for s in _starts(ev))


# 9/10. fast + normal swipes fire
def test_fast_swipe_fires():
    e, ev = _engine()
    _run(e, _swipe("right", n=10, step=0.05))
    assert "swipe_right" in _starts(ev)


def test_normal_swipe_fires():
    e, ev = _engine()
    _run(e, _swipe("right", n=16, step=0.02))
    assert "swipe_right" in _starts(ev)


# 11. repeated swipes stay reliable
def test_repeated_swipes():
    e, ev = _engine()
    for t0 in (14.0, 16.0, 18.0):
        _run(e, _swipe("right", t0=t0))
    assert len(_starts(ev, "swipe_right")) == 3


# 12. motion repeated WITHOUT neutral (gate on) → one fire
def test_neutral_gate_blocks_repeat():
    e, ev = _engine([_zrow()], neutral=True)
    _run(e, tm.timed(tm.z_shape(), t0=10.0))
    _run(e, tm.timed(tm.z_shape(), t0=13.5))
    assert len(_starts(ev, "zed")) == 1


# 13. neutral → motion → neutral → motion → two fires
def test_neutral_cycle_two_fires():
    e, ev = _engine([_zrow()], neutral=True)
    _run(e, tm.timed(tm.z_shape(), t0=10.0))
    e.on_hands(HandFrame(hands=[], timestamp=15.0, frame_width=640,
                         frame_height=480))
    _run(e, tm.timed(tm.z_shape(), t0=16.0))
    assert len(_starts(ev, "zed")) == 2


# swipe-like template cannot hijack a directional swipe
def test_swipe_like_template_cannot_hijack_swipe():
    straight = [(0.2 + 0.5 * k / 30, 0.5 + 0.001 * k) for k in range(31)]
    row = tm.FakeRow("liney", build_motion_template([straight]))
    e, ev = _engine([row])
    _run(e, _swipe("right"))
    assert "swipe_right" in _starts(ev)


# straight-first-stroke interaction (documented): a hard synthetic Z emits a
# swipe from its straight top stroke AND completes the template — proving the
# swipe is never starved and the completed shape never cancels an emitted one
def test_straight_first_stroke_documented_interaction():
    e, ev = _engine([_zrow()])
    _run(e, tm.timed(tm.z_shape()))
    s = _starts(ev)
    assert any(x.startswith("swipe") for x in s)   # opening stroke = swipe
    assert "zed" in s                              # shape still completes
