"""Gesture arbitration tests (spec §14, cases A–O).

Real MotionController + real CompoundEngine + real GestureArbiter with
real timers (short windows). Only the input-injection layer is mocked.
"""
import time

import pytest

from app.core.config import Config
from app.core.events import EventBus
from app.core.types import CameraStatus, Context, GestureEvent
from app.data.db import Database
from app.runtime.controller import MotionController

GAP_MS = 250          # compound step timeout used in tests
WAIT = GAP_MS / 1000 + 0.25   # long enough for any release timer


@pytest.fixture
def ctl(tmp_path, monkeypatch):
    monkeypatch.setenv("MGA_DATA_DIR", str(tmp_path))
    cfg = Config()
    cfg.default_cooldown_ms = 0
    cfg.action_global_cooldown_ms = 0
    db = Database(tmp_path / "arb.db")
    bus = EventBus()
    c = MotionController(cfg, db, bus)
    fired = []
    monkeypatch.setattr(c.executor, "_dispatch",
                        lambda t, p: fired.append((t, p)))
    pm = c.profile_manager
    g = pm.profiles.by_name("Global")
    for r in pm.rules.for_profile(g.id):
        pm.rules.delete(r.id)          # start from a clean mapping slate
    c.reload_rules()
    c.set_motion_enabled(True)
    return c, bus, fired


def key_action(c, name, key):
    return c.profile_manager.actions.create(name, "key_press", {"key": key})


def map_global(c, gesture, action_id):
    g = c.profile_manager.profiles.by_name("Global")
    c.profile_manager.rules.create(g.id, gesture, action_id)


def add_compound(c, name, gestures, **kw):
    kw.setdefault("step_timeout_ms", GAP_MS)
    kw.setdefault("max_duration_ms", 2000)
    kw.setdefault("cooldown_ms", 0)
    c.compound_gestures.create(
        name, [{"type": "gesture", "gesture": g} for g in gestures], **kw)


def ev(gesture, phase, source="primitive", hand="Right"):
    return GestureEvent(gesture=gesture, phase=phase, confidence=0.9,
                        handedness=hand, hand_x=0.5, hand_y=0.5,
                        timestamp=time.monotonic(), source=source)


def keys(fired):
    return [p["key"] for _, p in fired if "key" in p]


def set_ctx(c, process, title=""):
    app = process[:-4] if process.endswith(".exe") else process
    c.context.current = Context(application=app, process=process,
                                window_title=title)


# -- A: no compound → immediate --------------------------------------------
def test_a_primitive_immediate_when_no_compound(ctl):
    c, bus, fired = ctl
    map_global(c, "pinch", key_action(c, "P", "p"))
    c.reload_rules()
    set_ctx(c, "notepad.exe")
    bus.publish("gesture.event", ev("pinch", "start"))
    assert keys(fired) == ["p"]  # instantly, no waiting


# -- B/C/M: double pinch — compound only, exactly once ----------------------
def test_bcm_double_pinch_compound_only_once(ctl):
    c, bus, fired = ctl
    map_global(c, "pinch", key_action(c, "P", "p"))
    add_compound(c, "double_pinch", ["pinch", "pinch"])
    map_global(c, "double_pinch", key_action(c, "D", "d"))
    c.reload_rules()
    set_ctx(c, "notepad.exe")

    bus.publish("gesture.event", ev("pinch", "start"))
    assert keys(fired) == []            # B: first pinch held, not executed
    bus.publish("gesture.event", ev("pinch", "end"))
    bus.publish("gesture.event", ev("pinch", "start"))
    assert keys(fired) == ["d"]         # compound fired
    bus.publish("gesture.event", ev("pinch", "end"))
    time.sleep(WAIT)                    # M: no late primitive release
    assert keys(fired) == ["d"]


# -- D: single pinch falls back after timeout -------------------------------
def test_d_single_pinch_timeout_fallback(ctl):
    c, bus, fired = ctl
    map_global(c, "pinch", key_action(c, "P", "p"))
    add_compound(c, "double_pinch", ["pinch", "pinch"])
    map_global(c, "double_pinch", key_action(c, "D", "d"))
    c.reload_rules()
    set_ctx(c, "notepad.exe")

    bus.publish("gesture.event", ev("pinch", "start"))
    bus.publish("gesture.event", ev("pinch", "end"))
    assert keys(fired) == []
    time.sleep(WAIT)
    assert keys(fired) == ["p"]         # deterministic fallback


# -- E: sequence suppresses its component ----------------------------------
def test_e_pinch_swipe_suppresses_pinch(ctl):
    c, bus, fired = ctl
    map_global(c, "pinch", key_action(c, "P", "p"))
    add_compound(c, "pinch_swipe", ["pinch", "swipe_right"])
    map_global(c, "pinch_swipe", key_action(c, "N", "n"))
    c.reload_rules()
    set_ctx(c, "notepad.exe")

    bus.publish("gesture.event", ev("pinch", "start"))
    bus.publish("gesture.event", ev("pinch", "end"))
    bus.publish("gesture.event", ev("swipe_right", "start"))
    bus.publish("gesture.event", ev("swipe_right", "end"))
    time.sleep(WAIT)
    assert keys(fired) == ["n"]         # compound only, no right-click


# -- F: wrong gesture policy ------------------------------------------------
def test_f_wrong_gesture_executes_own_mapping_pending_releases(ctl):
    c, bus, fired = ctl
    map_global(c, "pinch", key_action(c, "P", "p"))
    map_global(c, "fist", key_action(c, "F", "f"))
    add_compound(c, "pinch_swipe", ["pinch", "swipe_right"])
    map_global(c, "pinch_swipe", key_action(c, "N", "n"))
    c.reload_rules()
    set_ctx(c, "notepad.exe")

    bus.publish("gesture.event", ev("pinch", "start"))
    bus.publish("gesture.event", ev("pinch", "end"))
    bus.publish("gesture.event", ev("fist", "start"))   # unrelated
    assert keys(fired) == ["f"]         # fist immediate (not a prefix)
    time.sleep(WAIT)
    assert keys(fired) == ["f", "p"]    # pinch released at its deadline


# -- G: longest match -------------------------------------------------------
def test_g_longer_compound_beats_shorter(ctl):
    c, bus, fired = ctl
    add_compound(c, "double_pinch", ["pinch", "pinch"])
    add_compound(c, "triple_pinch", ["pinch", "pinch", "pinch"])
    map_global(c, "double_pinch", key_action(c, "D", "d"))
    map_global(c, "triple_pinch", key_action(c, "T", "t"))
    c.reload_rules()
    set_ctx(c, "notepad.exe")

    for _ in range(3):
        bus.publish("gesture.event", ev("pinch", "start"))
        bus.publish("gesture.event", ev("pinch", "end"))
    time.sleep(WAIT)
    assert keys(fired) == ["t"]         # triple only — double suppressed


def test_g2_shorter_fires_when_longer_expires(ctl):
    c, bus, fired = ctl
    add_compound(c, "double_pinch", ["pinch", "pinch"])
    add_compound(c, "triple_pinch", ["pinch", "pinch", "pinch"])
    map_global(c, "double_pinch", key_action(c, "D", "d"))
    map_global(c, "triple_pinch", key_action(c, "T", "t"))
    c.reload_rules()
    set_ctx(c, "notepad.exe")

    for _ in range(2):
        bus.publish("gesture.event", ev("pinch", "start"))
        bus.publish("gesture.event", ev("pinch", "end"))
    assert keys(fired) == []            # double held while triple possible
    time.sleep(WAIT)
    assert keys(fired) == ["d"]         # triple expired → double executes


# -- H/I: context-aware arbitration ----------------------------------------
def test_hi_app_specific_compound_only_delays_in_that_app(ctl):
    c, bus, fired = ctl
    pm = c.profile_manager
    map_global(c, "pinch", key_action(c, "P", "p"))
    add_compound(c, "double_pinch", ["pinch", "pinch"])
    chrome = pm.profiles.create("Chrome", "application",
                                [{"process_name": "chrome.exe"}])
    pm.rules.create(chrome, "double_pinch", key_action(c, "C", "c"))
    c.reload_rules()

    # I: in notepad the compound resolves nowhere → pinch is immediate
    set_ctx(c, "notepad.exe")
    bus.publish("gesture.event", ev("pinch", "start"))
    assert keys(fired) == ["p"]
    bus.publish("gesture.event", ev("pinch", "end"))
    time.sleep(WAIT)
    assert keys(fired) == ["p"]

    # H: in chrome the compound is relevant → pinch held, compound wins
    fired.clear()
    c.compounds.reset()
    set_ctx(c, "chrome.exe", "Tab")
    bus.publish("gesture.event", ev("pinch", "start"))
    assert keys(fired) == []
    bus.publish("gesture.event", ev("pinch", "end"))
    bus.publish("gesture.event", ev("pinch", "start"))
    bus.publish("gesture.event", ev("pinch", "end"))
    time.sleep(WAIT)
    assert keys(fired) == ["c"]


# -- J/K: emergency stop & motion off clear pending -------------------------
def test_jk_motion_off_clears_pending(ctl):
    c, bus, fired = ctl
    map_global(c, "pinch", key_action(c, "P", "p"))
    add_compound(c, "double_pinch", ["pinch", "pinch"])
    map_global(c, "double_pinch", key_action(c, "D", "d"))
    c.reload_rules()
    set_ctx(c, "notepad.exe")

    bus.publish("gesture.event", ev("pinch", "start"))
    assert c.arbiter.pending_count() == 1
    c.emergency_disable()               # J (and K: same path)
    assert c.arbiter.pending_count() == 0
    time.sleep(WAIT)
    assert keys(fired) == []            # nothing stale executes


# -- L: camera disconnect clears pending ------------------------------------
def test_l_camera_disconnect_clears_pending(ctl):
    c, bus, fired = ctl
    map_global(c, "pinch", key_action(c, "P", "p"))
    add_compound(c, "double_pinch", ["pinch", "pinch"])
    map_global(c, "double_pinch", key_action(c, "D", "d"))
    c.reload_rules()
    set_ctx(c, "notepad.exe")

    bus.publish("gesture.event", ev("pinch", "start"))
    assert c.arbiter.pending_count() == 1
    bus.publish("camera.status", CameraStatus.DISCONNECTED, "unplugged")
    assert c.arbiter.pending_count() == 0
    time.sleep(WAIT)
    assert keys(fired) == []


# -- N: held pinch → one deferred action, no repeats ------------------------
def test_n_held_pinch_single_action(ctl):
    c, bus, fired = ctl
    map_global(c, "pinch", key_action(c, "P", "p"))
    add_compound(c, "double_pinch", ["pinch", "pinch"])
    map_global(c, "double_pinch", key_action(c, "D", "d"))
    c.reload_rules()
    set_ctx(c, "notepad.exe")

    bus.publish("gesture.event", ev("pinch", "start"))
    for _ in range(20):
        bus.publish("gesture.event", ev("pinch", "hold"))
    bus.publish("gesture.event", ev("pinch", "end"))
    time.sleep(WAIT)
    assert keys(fired) == ["p"]         # exactly one, after fallback


# -- O: unrelated primitives unaffected -------------------------------------
def test_o_non_prefix_gesture_never_delayed(ctl):
    c, bus, fired = ctl
    map_global(c, "fist", key_action(c, "F", "f"))
    add_compound(c, "double_pinch", ["pinch", "pinch"])
    map_global(c, "double_pinch", key_action(c, "D", "d"))
    c.reload_rules()
    set_ctx(c, "notepad.exe")

    bus.publish("gesture.event", ev("fist", "start"))
    assert keys(fired) == ["f"]         # zero arbitration latency


# -- continuous actions are never held --------------------------------------
def test_continuous_rule_immediate_despite_compound(ctl):
    c, bus, fired = ctl
    pm = c.profile_manager
    g = pm.profiles.by_name("Global")
    a = key_action(c, "Scroll", "s")
    pm.rules.create(g.id, "pinch", a, continuous=True, cooldown_ms=10)
    add_compound(c, "double_pinch", ["pinch", "pinch"])
    map_global(c, "double_pinch", key_action(c, "D", "d"))
    c.reload_rules()
    set_ctx(c, "notepad.exe")

    bus.publish("gesture.event", ev("pinch", "start"))
    assert keys(fired) == ["s"]         # continuous fires immediately


# -- early release: aborted hold window -------------------------------------
def test_early_release_on_aborted_hold(ctl):
    c, bus, fired = ctl
    map_global(c, "pinch", key_action(c, "P", "p"))
    c.compound_gestures.create(
        "pinch_hold", [{"type": "hold", "gesture": "pinch",
                        "hold_ms": 1500}], cooldown_ms=0)
    map_global(c, "pinch_hold", key_action(c, "H", "h"))
    c.reload_rules()
    set_ctx(c, "notepad.exe")

    bus.publish("gesture.event", ev("pinch", "start"))
    assert c.arbiter.pending_count() == 1   # held for the 1.5 s hold window
    bus.publish("gesture.event", ev("pinch", "end"))  # aborted early
    time.sleep(0.1)
    assert keys(fired) == ["p"]         # released early, not after 1.5 s
