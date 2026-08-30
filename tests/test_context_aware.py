"""Context-aware mapping milestone tests.

Covers: application context detection, profile selection per app,
window-specific precedence, global fallback, disabled profile/rule,
runtime hot-reload of mappings, multiple app profiles, no-match, and
safety around app switching / profile edits.
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
    cfg.default_cooldown_ms = 0
    cfg.action_global_cooldown_ms = 0
    db = Database(tmp_path / "ctx.db")
    bus = EventBus()
    c = MotionController(cfg, db, bus)
    fired = []
    monkeypatch.setattr(c.executor, "_dispatch",
                        lambda t, p: fired.append((t, p)))
    c.set_motion_enabled(True)
    return c, bus, fired


def gesture(name, phase="start"):
    return GestureEvent(gesture=name, phase=phase, confidence=0.9,
                        handedness="Right", hand_x=0.5, hand_y=0.5,
                        timestamp=time.monotonic())


def set_ctx(c, process, title=""):
    app = process[:-4] if process.endswith(".exe") else process
    c.context.current = Context(application=app, process=process,
                                window_title=title)


def pinch(c, bus):
    bus.publish("gesture.event", gesture("pinch"))
    bus.publish("gesture.event", gesture("pinch", "end"))


# -- context detection ------------------------------------------------------
def test_live_context_snapshot_fields():
    """Real Win32 snapshot returns a plausible normalized context."""
    from app.context.detector import snapshot
    ctx = snapshot()
    assert ctx.process.endswith(".exe") or ctx.process == ""
    assert ctx.process == ctx.process.lower()
    assert isinstance(ctx.cursor_x, int)
    assert ctx.screen >= 0


# -- per-application profile selection --------------------------------------
@pytest.fixture
def three_profiles(ctl):
    c, bus, fired = ctl
    pm = c.profile_manager
    a_global = pm.actions.create("G-act", "key_press", {"key": "g"})
    a_excel = pm.actions.create("E-act", "key_press", {"key": "e"})
    a_chrome = pm.actions.create("C-act", "key_press", {"key": "c"})
    g = pm.profiles.by_name("Global")
    # replace seeded pinch rule with a known one
    for r in pm.rules.for_profile(g.id):
        if r.gesture == "pinch":
            pm.rules.delete(r.id)
    pm.rules.create(g.id, "pinch", a_global)
    ex = pm.profiles.create("Excel", "application",
                            [{"process_name": "excel.exe"}])
    pm.rules.create(ex, "pinch", a_excel)
    ch = pm.profiles.create("Chrome", "application",
                            [{"process_name": "chrome.exe"}])
    pm.rules.create(ch, "pinch", a_chrome)
    c.reload_rules()
    return c, bus, fired, pm


def test_same_gesture_three_apps_three_actions(three_profiles):
    c, bus, fired, pm = three_profiles
    set_ctx(c, "excel.exe", "Book1 - Excel")
    pinch(c, bus)
    set_ctx(c, "chrome.exe", "New Tab - Google Chrome")
    pinch(c, bus)
    set_ctx(c, "explorer.exe", "Desktop")
    pinch(c, bus)
    assert [p["key"] for _, p in fired] == ["e", "c", "g"]


def test_scenario_b_removed_app_rule_falls_back(three_profiles):
    c, bus, fired, pm = three_profiles
    ex = pm.profiles.by_name("Excel")
    for r in pm.rules.for_profile(ex.id):
        pm.rules.delete(r.id)
    c.reload_rules()
    set_ctx(c, "excel.exe", "Book1")
    pinch(c, bus)
    assert [p["key"] for _, p in fired] == ["g"]  # global fallback


def test_scenario_c_global_disabled_no_action(three_profiles):
    c, bus, fired, pm = three_profiles
    g = pm.profiles.by_name("Global")
    for r in pm.rules.for_profile(g.id):
        if r.gesture == "pinch":
            pm.rules.update(r.id, enabled=False)
    c.reload_rules()
    set_ctx(c, "notepad.exe", "Untitled")  # no app profile
    pinch(c, bus)
    assert fired == []


def test_scenario_d_window_rule_overrides_app_rule(three_profiles):
    c, bus, fired, pm = three_profiles
    a_special = pm.actions.create("Book-special", "key_press", {"key": "s"})
    ex = pm.profiles.by_name("Excel")
    pm.rules.create(ex.id, "pinch", a_special, window_pattern="*Budget*")
    c.reload_rules()
    set_ctx(c, "excel.exe", "Budget2026.xlsx - Excel")
    pinch(c, bus)
    set_ctx(c, "excel.exe", "Book1 - Excel")
    pinch(c, bus)
    assert [p["key"] for _, p in fired] == ["s", "e"]


def test_disabled_profile_falls_back_at_runtime(three_profiles):
    c, bus, fired, pm = three_profiles
    ex = pm.profiles.by_name("Excel")
    pm.profiles.update(ex.id, enabled=False)
    c.reload_rules()
    set_ctx(c, "excel.exe", "Book1")
    pinch(c, bus)
    assert [p["key"] for _, p in fired] == ["g"]


def test_global_fallback_open_palm_across_apps(three_profiles):
    """Spec §8: app rule must not disable global behavior elsewhere."""
    c, bus, fired, pm = three_profiles
    a_a = pm.actions.create("A-palm", "key_press", {"key": "a"})
    a_b = pm.actions.create("B-palm", "key_press", {"key": "b"})
    g = pm.profiles.by_name("Global")
    for r in pm.rules.for_profile(g.id):
        if r.gesture == "open_palm":
            pm.rules.delete(r.id)
    pm.rules.create(g.id, "open_palm", a_a)
    ex = pm.profiles.by_name("Excel")
    pm.rules.create(ex.id, "open_palm", a_b)
    c.reload_rules()

    for proc, expected in (("excel.exe", "b"), ("chrome.exe", "a"),
                           ("explorer.exe", "a")):
        set_ctx(c, proc)
        bus.publish("gesture.event", gesture("open_palm"))
        bus.publish("gesture.event", gesture("open_palm", "end"))
    assert [p["key"] for _, p in fired] == ["b", "a", "a"]


# -- hot reload -------------------------------------------------------------
def test_hot_reload_new_mapping_applies_without_restart(ctl):
    c, bus, fired = ctl
    pm = c.profile_manager
    set_ctx(c, "winword.exe", "Doc1 - Word")
    bus.publish("gesture.event", gesture("point"))  # nothing mapped
    assert fired == []

    a = pm.actions.create("W-act", "key_press", {"key": "w"})
    w = pm.profiles.create("Word", "application",
                           [{"process_name": "winword.exe"}])
    pm.rules.create(w, "point", a)
    c.reload_rules()  # what Gesture Studio calls on Save

    bus.publish("gesture.event", gesture("point", "end"))
    bus.publish("gesture.event", gesture("point"))
    assert [p["key"] for _, p in fired] == ["w"]


def test_hot_reload_modified_mapping_applies(ctl):
    c, bus, fired = ctl
    pm = c.profile_manager
    a1 = pm.actions.create("First", "key_press", {"key": "1"})
    a2 = pm.actions.create("Second", "key_press", {"key": "2"})
    w = pm.profiles.create("Word", "application",
                           [{"process_name": "winword.exe"}])
    rid = pm.rules.create(w, "fist", a1)
    c.reload_rules()
    set_ctx(c, "winword.exe")
    bus.publish("gesture.event", gesture("fist"))
    pm.rules.update(rid, action_id=a2)
    c.reload_rules()
    bus.publish("gesture.event", gesture("fist", "end"))
    bus.publish("gesture.event", gesture("fist"))
    assert [p["key"] for _, p in fired] == ["1", "2"]


def test_hot_reload_does_not_touch_camera(ctl):
    c, _, _ = ctl
    with patch.object(c.camera, "stop") as cam_stop, \
         patch.object(c.camera, "start") as cam_start:
        c.reload_rules()
        cam_stop.assert_not_called()
        cam_start.assert_not_called()


# -- safety -----------------------------------------------------------------
def test_app_switch_alone_fires_nothing(three_profiles):
    c, bus, fired, pm = three_profiles
    for proc in ("excel.exe", "chrome.exe", "explorer.exe", "excel.exe"):
        set_ctx(c, proc)
        bus.publish("context.changed", c.context.current)
    assert fired == []


def test_profile_edit_alone_fires_nothing(three_profiles):
    c, bus, fired, pm = three_profiles
    ex = pm.profiles.by_name("Excel")
    pm.profiles.update(ex.id, priority=5)
    c.reload_rules()
    assert fired == []


def test_motion_off_blocks_app_specific_rules(three_profiles):
    c, bus, fired, pm = three_profiles
    c.set_motion_enabled(False)
    set_ctx(c, "excel.exe")
    pinch(c, bus)
    assert fired == []


def test_no_match_publishes_unmatched(three_profiles):
    c, bus, fired, pm = three_profiles
    unmatched = []
    bus.subscribe("rule.unmatched", lambda ev, ctx: unmatched.append(ev))
    set_ctx(c, "notepad.exe")
    bus.publish("gesture.event", gesture("thumb_up", "start"))
    # seeded global maps thumb_up → Play/Pause; delete it first
    pm = c.profile_manager
    g = pm.profiles.by_name("Global")
    for r in pm.rules.for_profile(g.id):
        pm.rules.delete(r.id)
    c.reload_rules()
    fired.clear()
    bus.publish("gesture.event", gesture("thumb_up", "end"))
    bus.publish("gesture.event", gesture("thumb_up", "start"))
    assert fired == []
    assert len(unmatched) >= 1
