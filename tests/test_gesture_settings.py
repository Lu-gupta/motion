"""Per-gesture sensitivity settings — repo, engine overrides, hot apply."""
import time

import pytest

from app.core.config import Config
from app.core.events import EventBus
from app.core.types import HandFrame
from app.data.db import Database
from app.data.repository import GestureSettingsRepo
from app.gestures.engine import GestureEngine
from app.runtime.controller import MotionController
from tests.conftest import make_hand
from tests.test_swipes import linear, run_motion
from app.gestures.motion import SwipeDetector


# -- repo -------------------------------------------------------------------
def test_repo_crud(tmp_path):
    r = GestureSettingsRepo(Database(tmp_path / "gs.db"))
    assert r.all() == {}
    r.set("pinch", {"confidence": 0.9})
    r.set("swipe", {"min_distance": 0.2})
    assert r.get("pinch") == {"confidence": 0.9}
    assert r.all()["swipe"]["min_distance"] == 0.2
    r.set("pinch", {"confidence": 0.5, "cooldown_ms": 200})
    assert r.get("pinch")["cooldown_ms"] == 200
    r.set("pinch", {})  # empty = revert to defaults
    assert r.get("pinch") == {}


# -- engine confidence override ---------------------------------------------
def feed(eng, pose, t0, n, dt=0.033):
    for i in range(n):
        hands = [make_hand(pose=pose)] if pose else []
        eng.on_hands(HandFrame(hands=hands, timestamp=t0 + i * dt,
                               frame_width=640, frame_height=480))
    return t0 + n * dt


def test_per_gesture_confidence_override():
    bus = EventBus()
    events = []
    eng = GestureEngine(bus, confidence_threshold=0.6, debounce_frames=2,
                        release_frames=2, cooldown_ms=0)
    bus.subscribe("gesture.event", events.append)

    eng.apply_gesture_settings({"pinch": {"confidence": 1.01}})  # impossible
    feed(eng, "pinch", 0.0, 6)
    assert [e for e in events if e.gesture == "pinch"] == []
    # other gestures unaffected by the pinch override
    feed(eng, "open", 1.0, 6)
    assert any(e.gesture == "open_palm" for e in events)

    eng.apply_gesture_settings({})  # revert
    feed(eng, None, 2.0, 3)
    feed(eng, "pinch", 3.0, 6)
    assert any(e.gesture == "pinch" for e in events)


def test_per_gesture_cooldown_override():
    bus = EventBus()
    events = []
    eng = GestureEngine(bus, confidence_threshold=0.6, debounce_frames=2,
                        release_frames=1, cooldown_ms=10_000)
    bus.subscribe("gesture.event", events.append)
    eng.apply_gesture_settings({"fist": {"cooldown_ms": 50}})

    t = feed(eng, "fist", 0.0, 4)
    t = feed(eng, None, t, 2)      # release
    t = feed(eng, "fist", t + 0.1, 4)  # 100ms later: over 50ms override
    starts = [e for e in events if e.gesture == "fist" and e.phase == "start"]
    assert len(starts) == 2  # default 10s cooldown would have blocked this


# -- swipe threshold configuration ------------------------------------------
def test_swipe_settings_applied_to_detector():
    bus = EventBus()
    eng = GestureEngine(bus)
    eng.apply_gesture_settings({"swipe": {
        "min_distance": 0.30, "min_speed": 1.5, "min_duration_ms": 100,
        "max_duration_ms": 400, "cooldown_ms": 900}})
    d = eng.swipes
    assert d.min_distance == 0.30
    assert d.min_speed == 1.5
    assert d.min_duration_s == pytest.approx(0.1)
    assert d.max_duration_s == pytest.approx(0.4)
    assert d.cooldown_s == pytest.approx(0.9)


def test_swipe_threshold_change_affects_detection():
    bus = EventBus()
    events = []
    eng = GestureEngine(bus)
    bus.subscribe("gesture.event", events.append)
    # strict: 0.30 units required — a 0.2-unit swipe must not fire
    eng.apply_gesture_settings({"swipe": {"min_distance": 0.30}})
    from app.core.types import Hand, Landmark
    for i, (dx, dy) in enumerate(linear(8, -0.20, 0.0)):
        base = make_hand(pose="open")
        h = Hand([Landmark(lm.x + dx, lm.y + dy, 0.0)
                  for lm in base.landmarks], "Right", 1.0)
        eng.on_hands(HandFrame(hands=[h], timestamp=i * 0.05,
                               frame_width=640, frame_height=480))
    assert not any(e.gesture.startswith("swipe") for e in events)
    # relaxed: same movement now fires
    eng.reset()
    eng.apply_gesture_settings({"swipe": {"min_distance": 0.12,
                                          "min_speed": 0.5}})
    for i, (dx, dy) in enumerate(linear(8, -0.20, 0.0)):
        base = make_hand(pose="open")
        h = Hand([Landmark(lm.x + dx, lm.y + dy, 0.0)
                  for lm in base.landmarks], "Right", 1.0)
        eng.on_hands(HandFrame(hands=[h], timestamp=5 + i * 0.05,
                               frame_width=640, frame_height=480))
    assert any(e.gesture == "swipe_right" for e in events)


# -- controller hot apply ---------------------------------------------------
def test_controller_applies_settings_on_reload(tmp_path, monkeypatch):
    monkeypatch.setenv("MGA_DATA_DIR", str(tmp_path))
    cfg = Config()
    db = Database(tmp_path / "c.db")
    bus = EventBus()
    c = MotionController(cfg, db, bus)
    assert c.gestures.swipes.min_distance == cfg.swipe_min_distance
    c.gesture_settings.set("swipe", {"min_distance": 0.25})
    c.gesture_settings.set("pinch", {"confidence": 0.95})
    c.reload_rules()  # what the UI calls on save
    assert c.gestures.swipes.min_distance == 0.25
    assert c.gestures._threshold_for("pinch") == 0.95
    assert c.gestures._threshold_for("fist") == cfg.gesture_confidence_threshold
    db.close()


# -- multi-sample custom templates ------------------------------------------
def test_build_template_multi_static():
    from app.gestures.custom import build_template_multi, pose_distance
    s1 = ([make_hand(pose="point") for _ in range(5)], [(0.5, 0.8)] * 5)
    s2 = ([make_hand(pose="point") for _ in range(5)], [(0.5, 0.8)] * 5)
    tmpl = build_template_multi("p", [s1, s2])
    assert tmpl["samples"] == 2
    assert pose_distance(make_hand(pose="point"), tmpl) < 0.2


def test_build_template_multi_motion_average():
    from app.gestures.custom import build_template_multi
    down1 = [(0.5, 0.3 + 0.05 * i) for i in range(10)]
    down2 = [(0.52, 0.28 + 0.055 * i) for i in range(10)]
    s1 = ([make_hand(pose="point") for _ in range(10)], down1)
    s2 = ([make_hand(pose="point") for _ in range(10)], down2)
    tmpl = build_template_multi("drag", [s1, s2])
    assert tmpl["path"]["movement"] > 0.12  # classified as motion
    assert len(tmpl["path"]["points"]) > 0
