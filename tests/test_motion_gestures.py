"""Custom recorded trajectory (motion) gestures — deterministic
synthetic paths only."""
import math
import time
from unittest.mock import patch

import pytest

import app.actions.input_win as iw
from app.core.config import Config
from app.core.events import EventBus
from app.core.types import Context, GestureEvent, HandFrame
from app.data.db import Database
from app.data.repository import MotionGestureRepo
from app.gestures.engine import GestureEngine, all_gesture_names
from app.gestures.trajectory import (TemplateDetector, TrajectoryEngine,
                                     build_motion_template,
                                     normalize_template, template_distance)
from app.runtime.controller import MotionController

from tests.conftest import make_hand

FPS = 30.0


# -- synthetic shapes ---------------------------------------------------------
def timed(points, t0=10.0, duration=1.2):
    n = len(points) - 1
    return [(t0 + duration * i / n, x, y) for i, (x, y) in enumerate(points)]


def z_shape(cx=0.5, cy=0.5, s=0.15, n_per=12):
    """Z: top stroke → diagonal → bottom stroke."""
    strokes = [((cx - s, cy - s), (cx + s, cy - s)),
               ((cx + s, cy - s), (cx - s, cy + s)),
               ((cx - s, cy + s), (cx + s, cy + s))]
    pts = []
    for (x0, y0), (x1, y1) in strokes:
        for k in range(n_per):
            f = k / n_per
            pts.append((x0 + (x1 - x0) * f, y0 + (y1 - y0) * f))
    pts.append(strokes[-1][1])
    return pts


def triangle_shape(cx=0.5, cy=0.5, s=0.15, n_per=12):
    v = [(cx, cy - s), (cx + s, cy + s), (cx - s, cy + s), (cx, cy - s)]
    pts = []
    for (x0, y0), (x1, y1) in zip(v, v[1:]):
        for k in range(n_per):
            f = k / n_per
            pts.append((x0 + (x1 - x0) * f, y0 + (y1 - y0) * f))
    pts.append(v[-1])
    return pts


def noisy(points, amp=0.006, seed=42):
    s = seed
    out = []
    for (x, y) in points:
        s = (1103515245 * s + 12345) % (2 ** 31)
        nx = ((s / 2 ** 31) - 0.5) * 2 * amp
        s = (1103515245 * s + 12345) % (2 ** 31)
        ny = ((s / 2 ** 31) - 0.5) * 2 * amp
        out.append((x + nx, y + ny))
    return out


def z_template():
    return build_motion_template([
        z_shape(), z_shape(cx=0.4, s=0.12), noisy(z_shape(s=0.18))])


def feed(eng, timed_pts):
    fires = []
    for (t, x, y) in timed_pts:
        r = eng.update(x, y, t)
        if r:
            fires.append(r)
    return fires


def engine_with(template_name="my_z", template=None, **kw):
    class Row:
        name = template_name
        tolerance = kw.get("tolerance", 0.35)
        min_confidence = kw.get("min_confidence", 0.55)
        cooldown_ms = kw.get("cooldown_ms", 1000)
        enabled = kw.get("enabled", True)
    Row.template = template or z_template()
    eng = TrajectoryEngine()
    eng.set_templates([Row])
    return eng


# -- template building --------------------------------------------------------
def test_build_template_multi_sample():
    t = z_template()
    assert len(t["points"]) == 32
    assert t["samples"] == 3
    with pytest.raises(ValueError):
        build_motion_template([])
    with pytest.raises(ValueError):
        build_motion_template([[(0.5, 0.5)]])   # no movement


def test_normalization_scale_translation_invariant():
    a = normalize_template(z_shape())
    b = normalize_template(z_shape(cx=0.3, cy=0.7, s=0.08))
    assert template_distance(a, b, allow_reverse=False) < 0.1


def test_template_distance_discriminates():
    z = normalize_template(z_shape())
    tri = normalize_template(triangle_shape())
    assert template_distance(z, tri) > 0.35


# -- matching -----------------------------------------------------------------
def test_same_shape_recognized():
    fires = feed(engine_with(), timed(z_shape()))
    assert [f[0] for f in fires] == ["my_z"]
    assert fires[0][1] >= 0.55


def test_scaled_and_translated_recognized():
    assert feed(engine_with(), timed(z_shape(cx=0.35, cy=0.6, s=0.09)))
    assert feed(engine_with(), timed(z_shape(cx=0.6, cy=0.4, s=0.25)))


def test_noisy_recognized():
    assert feed(engine_with(), timed(noisy(z_shape(), amp=0.008)))


def test_speed_variation_recognized():
    assert feed(engine_with(), timed(z_shape(), duration=0.6))
    assert feed(engine_with(), timed(z_shape(), duration=2.0))


def test_reversed_direction_recognized():
    assert feed(engine_with(), timed(list(reversed(z_shape()))))


def test_wrong_shape_rejected():
    """A triangle must never match the Z template. (The approximate
    circle detector may read a clean triangle as a rough circle — that
    is its documented tolerance and not the template's concern.)"""
    fires = feed(engine_with(), timed(triangle_shape()))
    assert all(f[0] != "my_z" for f in fires)


def test_incomplete_shape_rejected():
    partial = z_shape()[:22]        # ~60% of the Z
    assert feed(engine_with(), timed(partial, duration=0.8)) == []


def test_cursor_movement_rejected():
    line = [(0.2 + 0.5 * k / 30, 0.5 + 0.02 * math.sin(k / 3))
            for k in range(31)]
    assert feed(engine_with(), timed(line)) == []


def test_too_small_rejected():
    assert feed(engine_with(), timed(z_shape(s=0.03))) == []


def test_cooldown_and_single_fire():
    eng = engine_with()
    f1 = feed(eng, timed(z_shape(), t0=10.0))
    assert len(f1) == 1
    f2 = feed(eng, timed(z_shape(), t0=11.3, duration=0.5))  # inside cooldown
    assert f2 == []
    f3 = feed(eng, timed(z_shape(), t0=13.5))
    assert len(f3) == 1


def test_confidence_threshold():
    eng = engine_with(min_confidence=0.95)   # nearly impossible
    assert feed(eng, timed(noisy(z_shape(), amp=0.01))) == []


def test_disabled_template_not_matched():
    eng = engine_with(enabled=False)
    assert feed(eng, timed(z_shape())) == []
    assert len(eng.detectors) == 1           # circle only


def test_circle_detector_preserved():
    eng = engine_with()
    circle = [(0.5 + 0.14 * math.cos(2 * math.pi * 1.05 * k / 30),
               0.5 + 0.14 * math.sin(2 * math.pi * 1.05 * k / 30))
              for k in range(31)]
    fires = feed(eng, timed(circle, duration=1.0))
    assert [f[0] for f in fires] == ["circle"]


# -- gesture engine level -----------------------------------------------------
def hand_frames(timed_pts, pose="point"):
    base = make_hand(pose=pose)
    frames = []
    for (t, x, y) in timed_pts:
        dx = x - base.landmarks[8].x
        dy = y - base.landmarks[8].y
        pts = [(lm.x + dx, lm.y + dy) for lm in base.landmarks]
        frames.append(HandFrame(hands=[make_hand(points=pts)], timestamp=t,
                                frame_width=640, frame_height=480))
    return frames


class FakeRow:
    def __init__(self, name, template):
        self.name = name
        self.template = template
        self.tolerance = 0.35
        self.min_confidence = 0.55
        self.cooldown_ms = 1000
        self.enabled = True


def test_engine_emits_one_event_and_swipes_unaffected():
    bus = EventBus()
    eng = GestureEngine(bus)
    eng.trajectories.set_templates([FakeRow("my_z", z_template())])
    events = []
    bus.subscribe("gesture.event", lambda ev: events.append(ev))
    for hf in hand_frames(timed(z_shape())):
        eng.on_hands(hf)
    zs = [e for e in events if e.gesture == "my_z"]
    assert [e.phase for e in zs] == ["start", "end"]
    # plain swipe still recognized with templates loaded (timestamps
    # continue forward — the clock is monotonic in production)
    events.clear()
    swipe = [(14.0 + k * 0.02, 0.8 - 0.02 * k, 0.5) for k in range(16)]
    for hf in hand_frames(swipe):
        eng.on_hands(hf)
    assert any(e.gesture == "swipe_right" for e in events)


def test_hot_reload_templates():
    bus = EventBus()
    eng = GestureEngine(bus)
    eng.trajectories.set_templates([FakeRow("my_z", z_template())])
    assert {d.name for d in eng.trajectories.detectors} == {"circle", "my_z"}
    eng.apply_gesture_settings({"circle": {"min_size": 0.2}})
    eng.trajectories.set_templates([FakeRow("tri", build_motion_template(
        [triangle_shape()]))])
    names = {d.name for d in eng.trajectories.detectors}
    assert names == {"circle", "tri"}
    # circle instance kept its tuned setting through the reload
    assert eng.trajectories.detector("circle").min_diameter == 0.2


# -- repository ---------------------------------------------------------------
def test_repo_crud(tmp_path):
    repo = MotionGestureRepo(Database(tmp_path / "m.db"))
    with pytest.raises(ValueError):
        repo.create("", z_template())
    with pytest.raises(ValueError):
        repo.create("x", {"points": []})
    gid = repo.create("my_z", z_template())
    row = repo.get(gid)
    assert row.name == "my_z" and len(row.template["points"]) == 32
    assert repo.by_name("my_z").id == gid
    repo.update(gid, tolerance=0.5, min_confidence=0.7, cooldown_ms=2000,
                enabled=False)
    row = repo.get(gid)
    assert (row.tolerance, row.min_confidence,
            row.cooldown_ms, row.enabled) == (0.5, 0.7, 2000, False)
    repo.delete(gid)
    assert repo.get(gid) is None


# -- pipeline integration -----------------------------------------------------
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


def mev(phase, name="my_z"):
    return GestureEvent(gesture=name, phase=phase, confidence=0.9,
                        handedness="Right", hand_x=0.5, hand_y=0.5,
                        timestamp=time.monotonic())


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


def test_motion_gesture_full_pipeline(ctl):
    """Recorded shape loads into the live engine and its event resolves
    action / workflow / app profile / zone; catalog lists it."""
    c, bus = ctl
    keys = []
    pm = c.profile_manager
    c.motion_gestures.create("my_z", z_template())
    c.reload_rules()
    assert any(d.name == "my_z"
               for d in c.gestures.trajectories.detectors)
    assert "my_z" in all_gesture_names(c.custom_gestures.all(),
                                       c.motion_gestures.all())
    a_g = pm.actions.create("G", "key_press", {"key": "g"})
    a_app = pm.actions.create("A", "key_press", {"key": "a"})
    a_zone = pm.actions.create("Z", "key_press", {"key": "z"})
    step = pm.actions.create("S", "key_press", {"key": "w"})
    wid = c.workflow_repo.create("WF", [{"type": "action",
                                         "action_id": step}])
    a_wf = pm.actions.create("WF", "workflow", {"workflow_id": wid})
    g = pm.profiles.by_name("Global")
    pm.rules.create(g.id, "my_z", a_g)
    chrome = pm.profiles.create("Chrome", "application",
                                [{"process_name": "chrome.exe"}])
    c.zones.create("left", 0.0, 0.0, 0.4, 1.0)
    pm.rules.create(chrome, "my_z", a_zone, zone="left")
    pm.rules.create(chrome, "my_z", a_app)
    excel = pm.profiles.create("Excel", "application",
                               [{"process_name": "excel.exe"}])
    pm.rules.create(excel, "my_z", a_wf)
    c.reload_rules()
    with patch.object(iw, "key_press", lambda k: keys.append(k)), \
            patch.object(MotionController, "_screen_size",
                         staticmethod(lambda: (1920, 1080))):
        set_ctx(c, "notepad.exe")
        bus.publish("gesture.event", mev("start"))
        bus.publish("gesture.event", mev("end"))
        assert wait_for(lambda: keys == ["g"])          # global action
        set_ctx(c, "chrome.exe", cx=1500)
        bus.publish("gesture.event", mev("start"))
        bus.publish("gesture.event", mev("end"))
        assert wait_for(lambda: keys == ["g", "a"])     # app profile
        set_ctx(c, "chrome.exe", cx=100)
        bus.publish("gesture.event", mev("start"))
        bus.publish("gesture.event", mev("end"))
        assert wait_for(lambda: keys == ["g", "a", "z"])  # zone
        set_ctx(c, "excel.exe")
        bus.publish("gesture.event", mev("start"))
        bus.publish("gesture.event", mev("end"))
        assert wait_for(lambda: keys == ["g", "a", "z", "w"])  # workflow


def test_motion_gesture_compound_and_blocking(ctl):
    c, bus = ctl
    keys = []
    pm = c.profile_manager
    c.motion_gestures.create("my_z", z_template())
    a = pm.actions.create("C", "key_press", {"key": "c"})
    c.compound_gestures.create(
        "pinch_z", [{"type": "gesture", "gesture": "pinch"},
                    {"type": "gesture", "gesture": "my_z"}],
        step_timeout_ms=1500, cooldown_ms=0)
    g = pm.profiles.by_name("Global")
    pm.rules.create(g.id, "pinch_z", a)
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
        bus.publish("gesture.event", mev("start"))
        bus.publish("gesture.event", mev("end"))
        assert wait_for(lambda: keys == ["c"])          # compound fired
        # motion off / emergency stop block the gesture entirely
        pm.rules.create(g.id, "my_z", pm.actions.create(
            "K", "key_press", {"key": "k"}))
        c.reload_rules()
        c.emergency_disable()
        bus.publish("gesture.event", mev("start"))
        time.sleep(0.1)
    assert keys == ["c"]
