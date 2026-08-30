"""Compound / temporal gesture engine tests (spec §19)."""
import time
from dataclasses import dataclass, field

import pytest

from app.core.events import EventBus
from app.core.types import CameraStatus, Context, GestureEvent
from app.gestures.compound import CompoundEngine


@dataclass
class Def:
    """Stand-in for CompoundRow (same attribute contract)."""
    id: int
    name: str
    steps: list
    max_duration_ms: int = 2000
    step_timeout_ms: int = 700
    min_gap_ms: int = 0
    cooldown_ms: int = 800
    hand: str = "any"
    strict: bool = False
    enabled: bool = True


def make_engine(*defs):
    bus = EventBus()
    out = []
    eng = CompoundEngine(bus)
    eng.set_definitions(list(defs))
    bus.subscribe("gesture.event",
                  lambda ev: out.append(ev) if ev.source == "compound" else None)
    return eng, bus, out


def ev(gesture, phase, t, hand="Right", conf=0.9):
    return GestureEvent(gesture=gesture, phase=phase, confidence=conf,
                        handedness=hand, hand_x=0.5, hand_y=0.5, timestamp=t)


def send(bus, *events):
    for e in events:
        bus.publish("gesture.event", e)


DOUBLE_PINCH = Def(1, "double_pinch",
                   [{"type": "gesture", "gesture": "pinch"},
                    {"type": "gesture", "gesture": "pinch"}],
                   step_timeout_ms=900, min_gap_ms=100)

PINCH_SWIPE = Def(2, "pinch_swipe_right",
                  [{"type": "gesture", "gesture": "pinch"},
                   {"type": "gesture", "gesture": "swipe_right"}])

PINCH_HOLD = Def(3, "pinch_hold",
                 [{"type": "hold", "gesture": "pinch", "hold_ms": 500}])

PINCH_RELEASE = Def(4, "pinch_release",
                    [{"type": "gesture", "gesture": "pinch"},
                     {"type": "release"}])

FIST_PALM = Def(5, "fist_open_palm",
                [{"type": "gesture", "gesture": "fist"},
                 {"type": "gesture", "gesture": "open_palm"}])


# -- double gestures --------------------------------------------------------
def test_double_pinch_detected():
    eng, bus, out = make_engine(DOUBLE_PINCH)
    send(bus,
         ev("pinch", "start", 1.0), ev("pinch", "end", 1.2),
         ev("pinch", "start", 1.5), ev("pinch", "end", 1.7))
    assert [e.gesture for e in out] == ["double_pinch", "double_pinch"]
    assert [e.phase for e in out] == ["start", "end"]


def test_single_pinch_not_double():
    eng, bus, out = make_engine(DOUBLE_PINCH)
    send(bus, ev("pinch", "start", 1.0), ev("pinch", "end", 1.2))
    assert out == []


def test_long_held_pinch_not_double():
    """One physical pinch = one START; holds must never count twice."""
    eng, bus, out = make_engine(DOUBLE_PINCH)
    events = [ev("pinch", "start", 1.0)]
    events += [ev("pinch", "hold", 1.0 + i * 0.05) for i in range(1, 30)]
    events.append(ev("pinch", "end", 2.5))
    send(bus, *events)
    assert out == []


def test_min_gap_rejects_bounce():
    eng, bus, out = make_engine(DOUBLE_PINCH)  # min_gap 100ms
    send(bus,
         ev("pinch", "start", 1.0), ev("pinch", "end", 1.02),
         ev("pinch", "start", 1.05))  # 50ms after first start — bounce
    assert out == []
    # a properly spaced second pinch still completes
    send(bus, ev("pinch", "start", 1.4))
    assert [e.gesture for e in out] == ["double_pinch", "double_pinch"]


# -- sequences --------------------------------------------------------------
def test_pinch_swipe_right_detected():
    eng, bus, out = make_engine(PINCH_SWIPE)
    send(bus,
         ev("pinch", "start", 1.0), ev("pinch", "end", 1.1),
         ev("swipe_right", "start", 1.4), ev("swipe_right", "end", 1.4))
    assert [e.gesture for e in out][:1] == ["pinch_swipe_right"]


def test_fist_open_palm_detected():
    eng, bus, out = make_engine(FIST_PALM)
    send(bus,
         ev("fist", "start", 1.0), ev("fist", "end", 1.3),
         ev("open_palm", "start", 1.5))
    assert [e.gesture for e in out][:1] == ["fist_open_palm"]


def test_step_timeout_resets():
    eng, bus, out = make_engine(PINCH_SWIPE)  # step_timeout 700ms
    send(bus,
         ev("pinch", "start", 1.0),
         ev("swipe_right", "start", 2.0))  # 1000ms later — expired
    assert out == []
    # expired partial was reset and swipe_right cannot start a pinch
    # sequence — index must be back at 0
    assert eng.progress("pinch_swipe_right") == (0, 2)


def test_max_duration_resets():
    d = Def(9, "triple", [{"type": "gesture", "gesture": "pinch"}] * 3,
            max_duration_ms=1000, step_timeout_ms=900, cooldown_ms=0)
    eng, bus, out = make_engine(d)
    send(bus,
         ev("pinch", "start", 1.0), ev("pinch", "end", 1.05),
         ev("pinch", "start", 1.8), ev("pinch", "end", 1.85),
         ev("pinch", "start", 2.3))  # 1300ms total — over max_duration
    assert out == []


def test_wrong_gesture_strict_resets():
    d = Def(10, "strict_seq",
            [{"type": "gesture", "gesture": "pinch"},
             {"type": "gesture", "gesture": "swipe_right"}], strict=True)
    eng, bus, out = make_engine(d)
    send(bus,
         ev("pinch", "start", 1.0), ev("pinch", "end", 1.1),
         ev("fist", "start", 1.2),          # unexpected → reset
         ev("swipe_right", "start", 1.4))
    assert out == []


def test_wrong_gesture_lenient_ignored():
    eng, bus, out = make_engine(PINCH_SWIPE)  # strict=False
    send(bus,
         ev("pinch", "start", 1.0), ev("pinch", "end", 1.1),
         ev("point", "start", 1.2), ev("point", "end", 1.25),
         ev("swipe_right", "start", 1.5))
    assert [e.gesture for e in out][:1] == ["pinch_swipe_right"]


# -- hold -------------------------------------------------------------------
def test_hold_detected():
    eng, bus, out = make_engine(PINCH_HOLD)
    events = [ev("pinch", "start", 1.0)]
    events += [ev("pinch", "hold", 1.0 + i * 0.1) for i in range(1, 8)]
    send(bus, *events)
    assert [e.gesture for e in out][:1] == ["pinch_hold"]
    assert len([e for e in out if e.phase == "start"]) == 1  # fires once


def test_hold_released_early_cancels():
    eng, bus, out = make_engine(PINCH_HOLD)
    send(bus,
         ev("pinch", "start", 1.0),
         ev("pinch", "hold", 1.2),
         ev("pinch", "end", 1.3))  # released at 300ms < 500ms
    assert out == []


def test_explicit_pinch_then_hold_sequence():
    d = Def(11, "pinch_then_hold",
            [{"type": "gesture", "gesture": "pinch"},
             {"type": "hold", "gesture": "pinch", "hold_ms": 400}],
            step_timeout_ms=1000)
    eng, bus, out = make_engine(d)
    events = [ev("pinch", "start", 1.0)]
    events += [ev("pinch", "hold", 1.0 + i * 0.1) for i in range(1, 6)]
    send(bus, *events)
    assert [e.gesture for e in out][:1] == ["pinch_then_hold"]


# -- release ----------------------------------------------------------------
def test_release_step():
    eng, bus, out = make_engine(PINCH_RELEASE)
    send(bus, ev("pinch", "start", 1.0))
    assert out == []  # not yet
    send(bus, ev("pinch", "end", 1.3))
    assert [e.gesture for e in out][:1] == ["pinch_release"]


def test_hold_then_release():
    d = Def(12, "hold_release",
            [{"type": "hold", "gesture": "pinch", "hold_ms": 300},
             {"type": "release"}], step_timeout_ms=2000)
    eng, bus, out = make_engine(d)
    events = [ev("pinch", "start", 1.0)]
    events += [ev("pinch", "hold", 1.0 + i * 0.1) for i in range(1, 5)]
    events.append(ev("pinch", "end", 1.6))
    send(bus, *events)
    assert [e.gesture for e in out][:1] == ["hold_release"]


# -- cooldown / once per sequence -------------------------------------------
def test_fires_once_and_cooldown():
    eng, bus, out = make_engine(DOUBLE_PINCH)  # cooldown 800ms
    send(bus,
         ev("pinch", "start", 1.0), ev("pinch", "end", 1.1),
         ev("pinch", "start", 1.3), ev("pinch", "end", 1.4),
         # immediate third+fourth within cooldown
         ev("pinch", "start", 1.6), ev("pinch", "end", 1.7),
         ev("pinch", "start", 1.9), ev("pinch", "end", 2.0))
    starts = [e for e in out if e.phase == "start"]
    assert len(starts) == 1
    # after cooldown expires it can fire again
    send(bus,
         ev("pinch", "start", 3.0), ev("pinch", "end", 3.1),
         ev("pinch", "start", 3.3))
    starts = [e for e in out if e.phase == "start"]
    assert len(starts) == 2


# -- hand identity ----------------------------------------------------------
def test_right_hand_only():
    d = Def(13, "right_double",
            [{"type": "gesture", "gesture": "pinch"}] * 2, hand="right",
            step_timeout_ms=900)
    eng, bus, out = make_engine(d)
    send(bus,
         ev("pinch", "start", 1.0, hand="Left"),
         ev("pinch", "start", 1.3, hand="Left"))
    assert out == []
    send(bus,
         ev("pinch", "start", 2.0, hand="Right"),
         ev("pinch", "start", 2.3, hand="Right"))
    assert [e.gesture for e in out][:1] == ["right_double"]


def test_same_hand_lock():
    d = Def(14, "same_double",
            [{"type": "gesture", "gesture": "pinch"}] * 2, hand="same",
            step_timeout_ms=900)
    eng, bus, out = make_engine(d)
    send(bus,
         ev("pinch", "start", 1.0, hand="Left"),
         ev("pinch", "start", 1.3, hand="Right"))  # other hand — ignored
    assert out == []
    send(bus, ev("pinch", "start", 1.5, hand="Left"))
    assert [e.gesture for e in out][:1] == ["same_double"]


# -- cancellation -----------------------------------------------------------
def test_motion_off_resets_partial():
    eng, bus, out = make_engine(PINCH_SWIPE)
    send(bus, ev("pinch", "start", 1.0))
    assert eng.progress("pinch_swipe_right") == (1, 2)
    bus.publish("control.enabled", False)  # emergency stop / motion off
    assert eng.progress("pinch_swipe_right") == (0, 2)
    send(bus, ev("swipe_right", "start", 1.2))
    assert out == []


def test_camera_disconnect_resets_partial():
    eng, bus, out = make_engine(PINCH_SWIPE)
    send(bus, ev("pinch", "start", 1.0))
    bus.publish("camera.status", CameraStatus.DISCONNECTED, "gone")
    assert eng.progress("pinch_swipe_right") == (0, 2)


def test_compound_events_do_not_feed_back():
    """Compound output must not advance other compounds (no nesting)."""
    d2 = Def(15, "meta",
             [{"type": "gesture", "gesture": "double_pinch"}])
    eng, bus, out = make_engine(DOUBLE_PINCH, d2)
    send(bus,
         ev("pinch", "start", 1.0), ev("pinch", "end", 1.1),
         ev("pinch", "start", 1.4))
    assert [e.gesture for e in out if e.phase == "start"] == ["double_pinch"]


def test_custom_gesture_participates():
    d = Def(16, "victory_swipe",
            [{"type": "gesture", "gesture": "victory"},
             {"type": "gesture", "gesture": "swipe_right"}])
    eng, bus, out = make_engine(d)
    send(bus,
         ev("victory", "start", 1.0), ev("victory", "end", 1.2),
         ev("swipe_right", "start", 1.5))
    assert [e.gesture for e in out][:1] == ["victory_swipe"]


# -- repo -------------------------------------------------------------------
def test_repo_crud_and_validation(tmp_path):
    from app.data.db import Database
    from app.data.repository import CompoundGestureRepo
    repo = CompoundGestureRepo(Database(tmp_path / "c.db"))
    steps = [{"type": "gesture", "gesture": "pinch"},
             {"type": "gesture", "gesture": "swipe_right"}]
    cid = repo.create("quick_confirm", steps, hand="any", cooldown_ms=500)
    row = repo.get(cid)
    assert row.name == "quick_confirm"
    assert row.steps == steps
    assert row.cooldown_ms == 500

    repo.update(cid, enabled=False, step_timeout_ms=900)
    row = repo.get(cid)
    assert not row.enabled
    assert row.step_timeout_ms == 900

    with pytest.raises(ValueError):
        repo.create("bad", [])
    with pytest.raises(ValueError):
        repo.create("bad2", [{"type": "teleport"}])
    with pytest.raises(ValueError):
        repo.create("bad3", [{"type": "hold", "gesture": "pinch"}])

    repo.delete(cid)
    assert repo.get(cid) is None


# -- end-to-end: compound → context → rule → action -------------------------
@pytest.fixture
def ctl(tmp_path, monkeypatch):
    from app.core.config import Config
    from app.data.db import Database
    from app.runtime.controller import MotionController
    monkeypatch.setenv("MGA_DATA_DIR", str(tmp_path))
    cfg = Config()
    cfg.default_cooldown_ms = 0
    cfg.action_global_cooldown_ms = 0
    db = Database(tmp_path / "e2e.db")
    bus = EventBus()
    c = MotionController(cfg, db, bus)
    fired = []
    monkeypatch.setattr(c.executor, "_dispatch",
                        lambda t, p: fired.append((t, p)))
    c.set_motion_enabled(True)
    return c, bus, fired


def test_compound_first_class_in_rules_with_precedence(ctl):
    c, bus, fired = ctl
    pm = c.profile_manager
    c.compound_gestures.create(
        "double_pinch", [{"type": "gesture", "gesture": "pinch"}] * 2,
        step_timeout_ms=900)
    a_g = pm.actions.create("G", "key_press", {"key": "g"})
    a_c = pm.actions.create("C", "key_press", {"key": "c"})
    g = pm.profiles.by_name("Global")
    pm.rules.create(g.id, "double_pinch", a_g)
    ch = pm.profiles.create("Chrome", "application",
                            [{"process_name": "chrome.exe"}])
    pm.rules.create(ch, "double_pinch", a_c)
    c.reload_rules()

    def double_pinch(t0):
        send(bus,
             ev("pinch", "start", t0), ev("pinch", "end", t0 + 0.1),
             ev("pinch", "start", t0 + 0.3), ev("pinch", "end", t0 + 0.4))

    c.context.current = Context(application="chrome", process="chrome.exe",
                                window_title="Tab")
    double_pinch(time.monotonic())
    c.context.current = Context(application="notepad",
                                process="notepad.exe", window_title="x")
    double_pinch(time.monotonic() + 2.0)

    # note: the two primitive pinches may also fire their own global
    # pinch mapping — filter to the compound's actions
    keys = [p["key"] for t, p in fired if p.get("key") in ("c", "g")]
    assert keys == ["c", "g"]  # app override first, then global fallback


def test_motion_off_blocks_compound_action(ctl):
    c, bus, fired = ctl
    pm = c.profile_manager
    c.compound_gestures.create(
        "double_pinch", [{"type": "gesture", "gesture": "pinch"}] * 2,
        step_timeout_ms=900)
    a = pm.actions.create("X", "key_press", {"key": "x"})
    g = pm.profiles.by_name("Global")
    pm.rules.create(g.id, "double_pinch", a)
    c.reload_rules()
    c.set_motion_enabled(False)
    t0 = time.monotonic()
    send(bus,
         ev("pinch", "start", t0), ev("pinch", "end", t0 + 0.1),
         ev("pinch", "start", t0 + 0.3))
    assert fired == []
