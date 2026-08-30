"""Runtime controller scenario tests (spec §22) with mocked execution.

The executor's dispatch is patched so no real input is injected;
we assert which actions WOULD fire.
"""
import time
from unittest.mock import patch

import pytest

from app.core.config import Config
from app.core.events import EventBus
from app.core.types import Context, GestureEvent
from app.data.db import Database
from app.runtime.controller import MotionController


@pytest.fixture
def ctl(tmp_path, monkeypatch):
    monkeypatch.setenv("MGA_DATA_DIR", str(tmp_path))
    cfg = Config()
    cfg.default_cooldown_ms = 100
    cfg.action_global_cooldown_ms = 0
    db = Database(tmp_path / "rt.db")
    bus = EventBus()
    c = MotionController(cfg, db, bus)
    # add Chrome profile with pinch → hotkey
    pm = c.profile_manager
    aid = pm.actions.by_name("Copy").id
    pid = pm.profiles.create("Chrome", "application",
                             [{"process_name": "chrome.exe"}])
    pm.rules.create(pid, "pinch", aid)
    c.reload_rules()
    fired = []
    monkeypatch.setattr(c.executor, "_dispatch",
                        lambda t, p: fired.append((t, p)))
    return c, bus, fired


def gesture(name, phase="start", x=0.5, y=0.5):
    return GestureEvent(gesture=name, phase=phase, confidence=0.9,
                        handedness="Right", hand_x=x, hand_y=y,
                        timestamp=time.monotonic())


def set_ctx(c, process, title=""):
    app = process[:-4] if process.endswith(".exe") else process
    c.context.current = Context(application=app, process=process,
                                window_title=title)


def test_s1_desktop_pinch_fires_global_action(ctl):
    c, bus, fired = ctl
    c.set_motion_enabled(True)
    set_ctx(c, "explorer.exe", "Desktop")
    bus.publish("gesture.event", gesture("pinch"))
    assert fired == [("mouse_click", {"button": "left", "count": 1})]
    assert c.last_profile == "Global"


def test_s3_chrome_pinch_fires_chrome_action(ctl):
    c, bus, fired = ctl
    c.set_motion_enabled(True)
    set_ctx(c, "chrome.exe", "New Tab")
    bus.publish("gesture.event", gesture("pinch"))
    assert fired == [("hotkey", {"keys": "ctrl+c"})]
    assert c.last_profile == "Chrome"


def test_s4_global_fallback_for_unprofiled_app(ctl):
    c, bus, fired = ctl
    c.set_motion_enabled(True)
    set_ctx(c, "notepad.exe", "Untitled")
    bus.publish("gesture.event", gesture("pinch"))
    assert fired[0][0] == "mouse_click"
    assert c.last_profile == "Global"


def test_s5_app_override_beats_global(ctl):
    c, bus, fired = ctl
    c.set_motion_enabled(True)
    set_ctx(c, "chrome.exe")
    bus.publish("gesture.event", gesture("pinch"))
    set_ctx(c, "notepad.exe")
    bus.publish("gesture.event", gesture("pinch", phase="end"))
    bus.publish("gesture.event", gesture("pinch"))
    assert fired[0][0] == "hotkey"      # chrome-specific
    assert fired[1][0] == "mouse_click"  # global fallback


def test_s6_motion_off_no_actions(ctl):
    c, bus, fired = ctl
    c.set_motion_enabled(False)
    set_ctx(c, "chrome.exe")
    bus.publish("gesture.event", gesture("pinch"))
    bus.publish("gesture.event", gesture("swipe_up"))
    assert fired == []


def test_hold_does_not_refire_discrete_action(ctl):
    c, bus, fired = ctl
    c.set_motion_enabled(True)
    set_ctx(c, "chrome.exe")
    bus.publish("gesture.event", gesture("pinch", "start"))
    for _ in range(20):
        bus.publish("gesture.event", gesture("pinch", "hold"))
    assert len(fired) == 1


def test_rule_cooldown_blocks_rapid_restart(ctl):
    c, bus, fired = ctl
    c.set_motion_enabled(True)
    set_ctx(c, "chrome.exe")
    bus.publish("gesture.event", gesture("pinch", "start"))
    bus.publish("gesture.event", gesture("pinch", "end"))
    bus.publish("gesture.event", gesture("pinch", "start"))  # within 100ms
    assert len(fired) == 1
    time.sleep(0.12)
    bus.publish("gesture.event", gesture("pinch", "start"))
    assert len(fired) == 2


def test_continuous_rule_refires_on_hold(ctl):
    c, bus, fired = ctl
    pm = c.profile_manager
    scroll = pm.actions.by_name("Scroll down")
    g = pm.profiles.by_name("Global")
    pm.rules.create(g.id, "point", scroll.id, continuous=True,
                    cooldown_ms=10)
    c.reload_rules()
    c.set_motion_enabled(True)
    set_ctx(c, "notepad.exe")
    bus.publish("gesture.event", gesture("point", "start"))
    time.sleep(0.03)
    bus.publish("gesture.event", gesture("point", "hold"))
    time.sleep(0.03)
    bus.publish("gesture.event", gesture("point", "hold"))
    assert len(fired) >= 3
    bus.publish("gesture.event", gesture("point", "end"))
    n = len(fired)
    bus.publish("gesture.event", gesture("point", "hold"))  # after end
    assert len(fired) == n


def test_emergency_disable_releases_continuous(ctl):
    c, bus, fired = ctl
    pm = c.profile_manager
    cursor = pm.actions.by_name("Cursor follows hand")
    g = pm.profiles.by_name("Global")
    pm.rules.create(g.id, "point", cursor.id)
    c.reload_rules()
    c.set_motion_enabled(True)
    set_ctx(c, "notepad.exe")
    bus.publish("gesture.event", gesture("point", "start"))
    assert "point" in c._active_continuous
    c.emergency_disable()
    assert c._active_continuous == {}
    assert not c.motion_enabled


def test_swipe_reaches_rule_engine_and_executes(ctl):
    """Seeded Global profile: swipe_up → Volume up. The event must
    resolve through the rule engine and the mapped action must execute."""
    c, bus, fired = ctl
    matched = []
    bus.subscribe("rule.matched", lambda ev, ctx, m: matched.append(m))
    c.set_motion_enabled(True)
    set_ctx(c, "notepad.exe", "Untitled")
    bus.publish("gesture.event", gesture("swipe_up", "start"))
    bus.publish("gesture.event", gesture("swipe_up", "end"))
    assert len(matched) == 1
    assert matched[0].action.name == "Volume up"
    assert fired == [("volume", {"op": "up", "steps": 2})]


def test_all_four_swipes_resolve(ctl):
    c, bus, fired = ctl
    c.set_motion_enabled(True)
    set_ctx(c, "notepad.exe")
    for g in ("swipe_up", "swipe_down", "swipe_left", "swipe_right"):
        bus.publish("gesture.event", gesture(g, "start"))
        bus.publish("gesture.event", gesture(g, "end"))
        time.sleep(0.01)
    assert len(fired) == 4  # every direction mapped and executed


def test_unmatched_gesture_publishes_event(ctl):
    c, bus, fired = ctl
    unmatched = []
    bus.subscribe("rule.unmatched", lambda ev, ctx: unmatched.append(ev))
    c.set_motion_enabled(True)
    set_ctx(c, "notepad.exe")
    bus.publish("gesture.event", gesture("open_palm"))
    assert fired == []
    assert len(unmatched) == 1
