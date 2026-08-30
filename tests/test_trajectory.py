"""Trajectory gesture tests — circle detector + engine integration
(spec §22). All trajectories are deterministic synthetic paths."""
import math
import time
from unittest.mock import patch

import pytest

import app.actions.input_win as iw
from app.core.config import Config
from app.core.events import EventBus
from app.core.types import Context, GestureEvent, HandFrame
from app.data.db import Database
from app.gestures.engine import GestureEngine, all_gesture_names
from app.gestures.trajectory import CircleDetector, TrajectoryEngine
from app.runtime.controller import MotionController

from tests.conftest import make_hand

FPS = 30.0
DT = 1.0 / FPS


# -- synthetic paths ----------------------------------------------------------
def circle_path(cx=0.5, cy=0.5, rx=0.12, ry=None, turns=1.05, n=None,
                cw=True, rot=0.0, noise=0.0, t0=10.0, duration=1.0):
    """Deterministic parametric (t, x, y) circle/oval path."""
    ry = rx if ry is None else ry
    n = n or max(12, int(duration * FPS))
    pts = []
    rng_state = 12345
    for k in range(n + 1):
        frac = k / n
        a = 2 * math.pi * turns * frac * (1 if cw else -1)
        x = rx * math.cos(a)
        y = ry * math.sin(a)
        if rot:
            xr = x * math.cos(rot) - y * math.sin(rot)
            y = x * math.sin(rot) + y * math.cos(rot)
            x = xr
        if noise:
            rng_state = (1103515245 * rng_state + 12345) % (2 ** 31)
            nx = ((rng_state / 2 ** 31) - 0.5) * 2 * noise
            rng_state = (1103515245 * rng_state + 12345) % (2 ** 31)
            ny = ((rng_state / 2 ** 31) - 0.5) * 2 * noise
            x, y = x + nx, y + ny
        pts.append((t0 + frac * duration, cx + x, cy + y))
    return pts


def line_path(x0=0.2, y0=0.5, x1=0.8, y1=0.5, duration=0.6, t0=10.0):
    n = max(6, int(duration * FPS))
    return [(t0 + k / n * duration,
             x0 + (x1 - x0) * k / n, y0 + (y1 - y0) * k / n)
            for k in range(n + 1)]


def feed(eng: TrajectoryEngine, pts):
    fires = []
    for (t, x, y) in pts:
        r = eng.update(x, y, t)
        if r:
            fires.append(r)
    return fires


@pytest.fixture
def traj():
    return TrajectoryEngine()


# -- 1-7: valid circles -------------------------------------------------------
def test_clockwise_circle(traj):
    fires = feed(traj, circle_path(cw=True))
    assert [f[0] for f in fires] == ["circle"]
    assert fires[0][1] >= 0.6


def test_counter_clockwise_circle(traj):
    assert [f[0] for f in feed(traj, circle_path(cw=False))] == ["circle"]


def test_small_and_large_circles(traj):
    assert feed(traj, circle_path(rx=0.075))          # just above min size
    traj2 = TrajectoryEngine()
    assert feed(traj2, circle_path(rx=0.3, duration=1.4))


def test_oval(traj):
    assert feed(traj, circle_path(rx=0.15, ry=0.10))


def test_imperfect_noisy_circle(traj):
    assert feed(traj, circle_path(rx=0.13, noise=0.008))


def test_tilted_oval(traj):
    assert feed(traj, circle_path(rx=0.15, ry=0.10, rot=math.radians(35)))


# -- 8-13: rejections ---------------------------------------------------------
def test_incomplete_circle_rejected(traj):
    assert feed(traj, circle_path(turns=0.7)) == []


def test_straight_line_rejected(traj):
    assert feed(traj, line_path()) == []


def test_fast_swipe_shape_rejected(traj):
    assert feed(traj, line_path(duration=0.15)) == []


def test_jitter_rejected(traj):
    pts = []
    for k in range(40):
        pts.append((10.0 + k * DT,
                    0.5 + 0.01 * ((k * 7919) % 13 - 6) / 6,
                    0.5 + 0.01 * ((k * 104729) % 11 - 5) / 5))
    assert feed(traj, pts) == []


def test_too_small_circle_rejected(traj):
    assert feed(traj, circle_path(rx=0.04)) == []


def test_zigzag_rejected(traj):
    pts = []
    for k in range(40):
        x = 0.3 + 0.4 * (k / 40)
        y = 0.5 + (0.12 if (k // 5) % 2 else -0.12)
        pts.append((10.0 + k * DT, x, y))
    assert feed(traj, pts) == []


def test_random_walk_rejected(traj):
    s = 987654321
    x, y = 0.5, 0.5
    pts = []
    for k in range(60):
        s = (1103515245 * s + 12345) % (2 ** 31)
        x = min(0.9, max(0.1, x + ((s / 2 ** 31) - 0.5) * 0.08))
        s = (1103515245 * s + 12345) % (2 ** 31)
        y = min(0.9, max(0.1, y + ((s / 2 ** 31) - 0.5) * 0.08))
        pts.append((10.0 + k * DT, x, y))
    assert feed(traj, pts) == []


def test_slow_drift_rejected(traj):
    # a loop drawn far too slowly (10 s) exceeds max duration
    assert feed(traj, circle_path(duration=10.0, n=200)) == []


# -- 14: cooldown -------------------------------------------------------------
def test_cooldown_between_circles(traj):
    f1 = feed(traj, circle_path(t0=10.0))          # completes ~11.0
    assert len(f1) == 1
    # a fast second circle completing inside the 1 s cooldown → suppressed
    f2 = feed(traj, circle_path(t0=11.1, duration=0.5))
    assert f2 == []
    # after the cooldown → fires again
    f3 = feed(traj, circle_path(t0=13.0))
    assert len(f3) == 1


# -- 15: engine level — one circle = one event; pose preserved ---------------
def hand_frames(path, pose="point"):
    """HandFrames of a point-pose hand translated along the path."""
    base = make_hand(pose=pose)
    frames = []
    for (t, x, y) in path:
        dx = x - base.landmarks[8].x     # anchor: index tip follows path
        dy = y - base.landmarks[8].y
        pts = [(lm.x + dx, lm.y + dy) for lm in base.landmarks]
        frames.append(HandFrame(hands=[make_hand(points=pts)], timestamp=t,
                                frame_width=640, frame_height=480))
    return frames


def collect_events(engine_kwargs=None, path=None, pose="point"):
    bus = EventBus()
    eng = GestureEngine(bus, **(engine_kwargs or {}))
    events = []
    bus.subscribe("gesture.event", lambda ev: events.append(ev))
    for hf in hand_frames(path, pose):
        eng.on_hands(hf)
    return eng, events


def test_one_circle_one_event_and_pose_unaffected():
    path = circle_path(rx=0.14)
    _, events = collect_events(path=path)
    circles = [e for e in events if e.gesture == "circle"]
    assert [e.phase for e in circles] == ["start", "end"]
    assert circles[0].confidence >= 0.6
    # point pose still tracked through the motion (static engine intact)
    assert any(e.gesture == "point" for e in events)
    # no swipe fired from the circle's arcs
    assert not any(e.gesture.startswith("swipe") for e in events)


def test_swipe_still_works_and_no_circle():
    path = line_path(x0=0.8, y0=0.5, x1=0.35, y1=0.5, duration=0.3)
    _, events = collect_events(path=path)
    swipes = {e.gesture for e in events if e.gesture.startswith("swipe")}
    assert swipes == {"swipe_right"}   # mirror: image-left = user right
    assert not any(e.gesture == "circle" for e in events)


def test_circle_settings_hot_apply():
    bus = EventBus()
    eng = GestureEngine(bus)
    eng.apply_gesture_settings({"circle": {"confidence": 0.85,
                                           "min_size": 0.3,
                                           "max_duration_ms": 1500,
                                           "cooldown_ms": 2500}})
    det = eng.trajectories.detector("circle")
    assert det.min_confidence == 0.85
    assert det.min_diameter == 0.3
    assert det.max_duration_s == 1.5
    assert det.cooldown_s == 2.5
    # a formerly-valid 0.12-wide circle is now too small
    events = []
    bus.subscribe("gesture.event", lambda ev: events.append(ev))
    for hf in hand_frames(circle_path(rx=0.12)):
        eng.on_hands(hf)
    assert not any(e.gesture == "circle" for e in events)


def test_candidate_event_published():
    bus = EventBus()
    eng = GestureEngine(bus)
    pubs = []
    bus.subscribe("trajectory.candidate", lambda pts, ts: pubs.append(pts))
    for hf in hand_frames(circle_path(rx=0.14)):
        eng.on_hands(hf)
    assert any(len(p) > 2 for p in pubs)     # trail while drawing
    assert pubs[-1] == []                    # cleared after the fire


def test_circle_in_gesture_catalog():
    assert "circle" in all_gesture_names()


# -- 16-23: pipeline integration ----------------------------------------------
@pytest.fixture
def ctl(tmp_path, monkeypatch):
    monkeypatch.setenv("MGA_DATA_DIR", str(tmp_path))
    cfg = Config()
    cfg.default_cooldown_ms = 0
    cfg.action_global_cooldown_ms = 0
    db = Database(tmp_path / "rt.db")
    bus = EventBus()
    c = MotionController(cfg, db, bus)
    pm = c.profile_manager
    g = pm.profiles.by_name("Global")
    for r in pm.rules.for_profile(g.id):
        pm.rules.delete(r.id)
    c.reload_rules()
    c.set_motion_enabled(True)
    return c, bus


def cev(phase, ts=None):
    return GestureEvent(gesture="circle", phase=phase, confidence=0.9,
                        handedness="Right", hand_x=0.5, hand_y=0.5,
                        timestamp=ts or time.monotonic())


def wait_for(pred, timeout=3.0):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        if pred():
            return True
        time.sleep(0.01)
    return False


def set_ctx(c, process, title="", cx=960, cy=540):
    app = process[:-4] if process.endswith(".exe") else process
    c.context.current = Context(application=app, process=process,
                                window_title=title, cursor_x=cx, cursor_y=cy)


def test_circle_maps_to_action_and_profiles_and_zones(ctl):
    c, bus = ctl
    keys = []
    pm = c.profile_manager
    a_g = pm.actions.create("G", "key_press", {"key": "g"})
    a_app = pm.actions.create("A", "key_press", {"key": "a"})
    a_zone = pm.actions.create("Z", "key_press", {"key": "z"})
    g = pm.profiles.by_name("Global")
    pm.rules.create(g.id, "circle", a_g)
    excel = pm.profiles.create("Excel", "application",
                               [{"process_name": "excel.exe"}])
    c.zones.create("left", 0.0, 0.0, 0.4, 1.0)
    # zone rule first (lower id) → wins inside the zone, app rule is the
    # fallback outside it (zones are conditions within the same tier)
    pm.rules.create(excel, "circle", a_zone, zone="left")
    pm.rules.create(excel, "circle", a_app)
    c.reload_rules()
    with patch.object(iw, "click"), patch.object(
            iw, "key_press", lambda k: keys.append(k)), \
            patch.object(MotionController, "_screen_size",
                         staticmethod(lambda: (1920, 1080))):
        set_ctx(c, "notepad.exe")                 # global fallback
        bus.publish("gesture.event", cev("start"))
        bus.publish("gesture.event", cev("end"))
        assert wait_for(lambda: keys == ["g"])
        set_ctx(c, "excel.exe", cx=1500)          # app rule (outside zone)
        bus.publish("gesture.event", cev("start"))
        bus.publish("gesture.event", cev("end"))
        assert wait_for(lambda: keys == ["g", "a"])
        set_ctx(c, "excel.exe", cx=100)           # zone rule wins
        bus.publish("gesture.event", cev("start"))
        bus.publish("gesture.event", cev("end"))
        assert wait_for(lambda: keys == ["g", "a", "z"])


def test_circle_maps_to_workflow(ctl):
    c, bus = ctl
    keys = []
    pm = c.profile_manager
    step = pm.actions.create("S", "key_press", {"key": "w"})
    wid = c.workflow_repo.create("WF", [{"type": "action",
                                         "action_id": step}])
    a_wf = pm.actions.create("WF", "workflow", {"workflow_id": wid})
    g = pm.profiles.by_name("Global")
    pm.rules.create(g.id, "circle", a_wf)
    c.reload_rules()
    set_ctx(c, "notepad.exe")
    with patch.object(iw, "key_press", lambda k: keys.append(k)):
        bus.publish("gesture.event", cev("start"))
        bus.publish("gesture.event", cev("end"))
        assert wait_for(lambda: keys == ["w"])


def test_circle_as_compound_step(ctl):
    """pinch → circle compound fires; lone circle suppressed as component."""
    c, bus = ctl
    keys = []
    pm = c.profile_manager
    a_comp = pm.actions.create("C", "key_press", {"key": "c"})
    c.compound_gestures.create(
        "pinch_circle", [{"type": "gesture", "gesture": "pinch"},
                         {"type": "gesture", "gesture": "circle"}],
        step_timeout_ms=1500, cooldown_ms=0)
    g = pm.profiles.by_name("Global")
    pm.rules.create(g.id, "pinch_circle", a_comp)
    c.reload_rules()
    set_ctx(c, "notepad.exe")
    with patch.object(iw, "key_press", lambda k: keys.append(k)):
        t = time.monotonic()
        bus.publish("gesture.event", GestureEvent(
            gesture="pinch", phase="start", confidence=0.9,
            handedness="Right", hand_x=0.5, hand_y=0.5, timestamp=t))
        bus.publish("gesture.event", GestureEvent(
            gesture="pinch", phase="end", confidence=0.9,
            handedness="Right", hand_x=0.5, hand_y=0.5, timestamp=t))
        time.sleep(0.05)
        bus.publish("gesture.event", cev("start"))
        bus.publish("gesture.event", cev("end"))
        assert wait_for(lambda: keys == ["c"])


def test_motion_off_and_estop_block_circle(ctl):
    c, bus = ctl
    keys = []
    pm = c.profile_manager
    a = pm.actions.create("K", "key_press", {"key": "k"})
    g = pm.profiles.by_name("Global")
    pm.rules.create(g.id, "circle", a)
    c.reload_rules()
    set_ctx(c, "notepad.exe")
    with patch.object(iw, "key_press", lambda k: keys.append(k)):
        c.set_motion_enabled(False)
        bus.publish("gesture.event", cev("start"))
        time.sleep(0.1)
        assert keys == []
        c.set_motion_enabled(True)
        c.emergency_disable()
        bus.publish("gesture.event", cev("start"))
        time.sleep(0.1)
    assert keys == []
