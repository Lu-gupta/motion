"""Gesture Command Center / advanced gesture-to-workflow UX.

Mappings ARE rules resolved by the existing RuleEngine — these tests
exercise the mapping model, the read-only conflict analyzer, the bounded
activity log, the dangerous-workflow confirmation gate and a UI smoke of
the Command Center page. No arbitration/recognition behavior is changed.
"""
import threading
import time
from unittest.mock import patch

import numpy as np
import pytest

import app.actions.input_win as iw
from app.camera.capture import CameraWorker
from app.core.config import Config
from app.core.events import EventBus
from app.core.types import (ActionSpec, Context, GestureEvent, RuleMatch)
from app.data.db import Database
from app.profiles.manager import ProfileManager
from app.rules import analyzer
from app.rules.engine import RuleEngine
from app.runtime.activity import ActivityLog
from app.runtime.controller import MotionController


# ---------------------------------------------------------------- helpers ----
def make_pm(tmp_path, name="cc"):
    db = Database(tmp_path / f"{name}.db")
    pm = ProfileManager(db)
    pm.seed_defaults()
    return pm, db


def make_engine(pm):
    return RuleEngine(pm.profiles, pm.rules, pm.actions)


def ctx(process="chrome.exe", title="YouTube - Chrome"):
    return Context(application=process.split(".")[0], process=process,
                   window_title=title, cursor_x=1, cursor_y=1, screen=0)


# ---------------------------------------------------- 1-5 mapping model -------
def test_create_edit_duplicate_delete_mapping(tmp_path):
    pm, db = make_pm(tmp_path)
    a = pm.actions.create("K", "key_press", {"key": "k"})
    gid = pm.profiles.by_name("Global").id
    rid = pm.rules.create(gid, "circle", a)                 # 1 create
    assert pm.rules.get(rid).gesture == "circle"
    pm.rules.update(rid, gesture="fist")                    # 2 edit
    assert pm.rules.get(rid).gesture == "fist"
    r = pm.rules.get(rid)                                    # 3 duplicate
    rid2 = pm.rules.create(r.profile_id, r.gesture, r.action_id,
                           window_pattern=r.window_pattern, zone=r.zone,
                           enabled=r.enabled)
    assert rid2 != rid
    pm.rules.update(rid2, gesture="pinch")                  # independent
    assert pm.rules.get(rid).gesture == "fist"
    pm.rules.delete(rid2)                                   # 4 delete
    assert pm.rules.get(rid2) is None
    db.close()


def test_enable_disable_mapping(tmp_path):
    pm, db = make_pm(tmp_path)
    a = pm.actions.create("K", "key_press", {"key": "k"})
    gid = pm.profiles.by_name("Global").id
    rid = pm.rules.create(gid, "circle", a)
    eng = make_engine(pm)
    assert eng.resolve("circle", ctx()) is not None
    pm.rules.update(rid, enabled=False)
    eng.reload()
    assert eng.resolve("circle", ctx()) is None            # 15 disabled → no
    pm.rules.update(rid, enabled=True)
    eng.reload()
    assert eng.resolve("circle", ctx()) is not None
    db.close()


# ---------------------------------------------------- 6-14 resolution ---------
def test_gesture_to_action_and_workflow(tmp_path):
    pm, db = make_pm(tmp_path)
    act = pm.actions.create("Click", "key_press", {"key": "k"})
    wid = pm.workflows.create("WF", [{"type": "delay", "ms": 10}])
    wfa = pm.actions.create("WF", "workflow", {"workflow_id": wid})
    gid = pm.profiles.by_name("Global").id
    # use gestures not already mapped by the seeded Global starter rules
    pm.rules.create(gid, "circle", act)
    pm.rules.create(gid, "point", wfa)
    eng = make_engine(pm)
    assert eng.resolve("circle", ctx()).action.type == "key_press"   # 6
    assert eng.resolve("point", ctx()).action.type == "workflow"     # 7
    db.close()


def test_global_vs_app_precedence(tmp_path):
    pm, db = make_pm(tmp_path)
    g = pm.actions.create("G", "key_press", {"key": "g"})
    c = pm.actions.create("C", "key_press", {"key": "c"})
    gid = pm.profiles.by_name("Global").id
    cid = pm.profiles.create("Chrome", "application",
                             [{"process_name": "chrome.exe"}])
    pm.rules.create(gid, "circle", g)                        # 8 global
    pm.rules.create(cid, "circle", c)                        # 9 app-specific
    eng = make_engine(pm)
    # 10 precedence: in Chrome the app rule wins; elsewhere the global one
    assert eng.resolve("circle", ctx("chrome.exe")).action.name == "C"
    assert eng.resolve("circle", ctx("notepad.exe")).action.name == "G"
    db.close()


def test_compound_circle_motion_mappings_resolve(tmp_path):
    pm, db = make_pm(tmp_path)
    a = pm.actions.create("A", "key_press", {"key": "a"})
    gid = pm.profiles.by_name("Global").id
    # compound (12), circle (13), a recorded-motion name (14) all map the
    # same way — no special path
    for gesture in ("double_pinch", "circle", "my_zigzag"):
        pm.rules.create(gid, gesture, a)
    eng = make_engine(pm)
    for gesture in ("double_pinch", "circle", "my_zigzag"):
        assert eng.resolve(gesture, ctx()).action.name == "A"
    db.close()


# ---------------------------------------------------- 11 conflict analyzer ----
def test_conflict_detection(tmp_path):
    pm, db = make_pm(tmp_path)
    a = pm.actions.create("A", "key_press", {"key": "a"})
    b = pm.actions.create("B", "key_press", {"key": "b"})
    gid = pm.profiles.by_name("Global").id
    r1 = pm.rules.create(gid, "circle", a)          # identical context
    r2 = pm.rules.create(gid, "circle", b)          # → CONFLICT
    findings = analyzer.analyze(pm.profiles.all(), pm.rules.all())
    assert findings[r1].level == "conflict"
    assert findings[r2].level == "conflict"
    assert "CONFLICT" in findings[r1].message
    db.close()


def test_precedence_info_not_conflict(tmp_path):
    pm, db = make_pm(tmp_path)
    g = pm.actions.create("G", "key_press", {"key": "g"})
    c = pm.actions.create("C", "key_press", {"key": "c"})
    gid = pm.profiles.by_name("Global").id
    cid = pm.profiles.create("Chrome", "application",
                             [{"process_name": "chrome.exe"}])
    rg = pm.rules.create(gid, "circle", g)
    rc = pm.rules.create(cid, "circle", c)
    findings = analyzer.analyze(pm.profiles.all(), pm.rules.all())
    # different tiers → not a conflict, but explained precedence
    assert findings[rg].level == "info"
    assert findings[rc].level == "info"
    assert "wins" in findings[rg].message or "wins" in findings[rc].message
    db.close()


def test_disabled_rule_never_conflicts(tmp_path):
    pm, db = make_pm(tmp_path)
    a = pm.actions.create("A", "key_press", {"key": "a"})
    b = pm.actions.create("B", "key_press", {"key": "b"})
    gid = pm.profiles.by_name("Global").id
    r1 = pm.rules.create(gid, "circle", a)
    r2 = pm.rules.create(gid, "circle", b, enabled=False)
    findings = analyzer.analyze(pm.profiles.all(), pm.rules.all())
    assert findings[r1].level == "ok"           # the other is disabled
    assert findings[r2].level == "ok"
    db.close()


# ---------------------------------------------------- 16-17 safe delete/edit --
def test_delete_mapping_keeps_workflow(tmp_path):
    pm, db = make_pm(tmp_path)
    wid = pm.workflows.create("Keep", [{"type": "delay", "ms": 10}])
    wfa = pm.actions.create("Keep", "workflow", {"workflow_id": wid})
    gid = pm.profiles.by_name("Global").id
    rid = pm.rules.create(gid, "circle", wfa)
    pm.rules.delete(rid)                                     # 16
    assert pm.workflows.get(wid) is not None                # workflow stays
    assert pm.actions.by_name("Keep") is not None           # action stays
    db.close()


def test_edit_workflow_preserves_mapping(tmp_path):
    pm, db = make_pm(tmp_path)
    wid = pm.workflows.create("Edit", [{"type": "delay", "ms": 10}])
    wfa = pm.actions.create("Edit", "workflow", {"workflow_id": wid})
    gid = pm.profiles.by_name("Global").id
    rid = pm.rules.create(gid, "circle", wfa)
    pm.workflows.update(wid, steps=[{"type": "delay", "ms": 20},
                                    {"type": "delay", "ms": 30}])  # 17
    r = pm.rules.get(rid)
    assert r is not None and r.action_id == wfa                 # mapping intact
    assert len(pm.workflows.get(wid).steps) == 2
    db.close()


# ---------------------------------------------------- 18-19 activity log ------
def _match(gesture, name, atype="workflow"):
    return (GestureEvent(gesture, "start", 0.9, "Right", 0.5, 0.5, 0.0),
            ctx(),
            RuleMatch(rule_id=1, profile_name="Global",
                      action=ActionSpec(type=atype, params={}, name=name),
                      continuous=False, cooldown_ms=0))


def test_activity_reflects_workflow_outcomes(tmp_path):
    bus = EventBus()
    clock = [1000.0]
    log = ActivityLog(bus, clock=lambda: clock[0])
    # a workflow start then failure (18) and another cancellation (19)
    bus.publish("rule.matched", *_match("circle", "Open YouTube"))
    bus.publish("workflow.done", "Open YouTube", "failed", "Chrome down")
    bus.publish("rule.matched", *_match("pinch", "Do Thing"))
    bus.publish("workflow.done", "Do Thing", "cancelled", "Emergency Stop")
    entries = log.entries()
    by_target = {e.target: e for e in entries}
    assert by_target["Open YouTube"].status == "failed"
    assert by_target["Open YouTube"].detail == "Chrome down"
    assert by_target["Do Thing"].status == "cancelled"


def test_activity_is_bounded(tmp_path):
    bus = EventBus()
    log = ActivityLog(bus, clock=lambda: 0.0)
    for i in range(120):
        bus.publish("rule.matched", *_match("circle", f"A{i}", "key_press"))
    assert len(log.entries()) == ActivityLog.MAX      # bounded ring


def test_activity_only_subscribes_lightweight_events():
    """23: the activity log must never do work on camera/vision events."""
    bus = EventBus()
    ActivityLog(bus)
    for hot in ("camera.frame", "vision.hands", "gesture.event"):
        assert bus._subs.get(hot, []) == []
    assert bus._subs.get("rule.matched")
    assert bus._subs.get("workflow.done")


# --------------------------------------- 20-22 confirmation / E-stop gate -----
class FakeCap:
    def isOpened(self): return True
    def read(self):
        time.sleep(0.003)
        return True, np.zeros((48, 64, 3), dtype=np.uint8)
    def release(self): pass


class StubTracker:
    def start(self): self.started = True
    def stop(self): self.started = False


@pytest.fixture
def ctl(tmp_path, monkeypatch):
    monkeypatch.setenv("MGA_DATA_DIR", str(tmp_path))
    cfg = Config()
    cfg.default_cooldown_ms = 0
    cfg.action_global_cooldown_ms = 0
    cfg.confirm_dangerous_workflows = True
    db = Database(tmp_path / "gate.db")
    bus = EventBus()
    c = MotionController(cfg, db, bus)
    c.workflows.POLL_S = 0.02
    c.camera = CameraWorker(bus, opener=lambda *a: FakeCap(), target_fps=120)
    c.tracker = StubTracker()
    return c, bus, db


def _map_confirm_workflow(c):
    pm = c.profile_manager
    a = pm.actions.create("K", "key_press", {"key": "k"})
    wid = pm.workflows.create("Dangerous", [
        {"type": "action", "action_id": a}], requires_confirmation=True)
    wfa = pm.actions.create("Dangerous", "workflow", {"workflow_id": wid})
    gid = pm.profiles.by_name("Global").id
    pm.rules.create(gid, "circle", wfa)
    c.reload_rules()
    return wid


def test_confirmation_gate_defers_then_runs(ctl):
    c, bus, db = ctl
    wid = _map_confirm_workflow(c)
    c.set_motion_enabled(True)
    reqs = []
    done = threading.Event()
    bus.subscribe("workflow.confirm_request",
                  lambda *a: reqs.append(a))
    bus.subscribe("workflow.done", lambda *a: done.set())
    keys = []
    with patch.object(iw, "key_press", lambda k: keys.append(k)):
        c._on_gesture(GestureEvent("circle", "start", 0.9, "Right",
                                   0.5, 0.5, time.monotonic()))
        time.sleep(0.1)
        assert reqs, "no confirmation requested"
        assert keys == []                       # 22 not executed yet
        token = reqs[0][0]
        c.resolve_confirmation(token, True)     # user accepts
        assert done.wait(3.0)
    assert keys == ["k"]                         # ran exactly once
    c.shutdown()
    db.close()


def test_confirmation_decline_does_not_run(ctl):
    c, bus, db = ctl
    _map_confirm_workflow(c)
    c.set_motion_enabled(True)
    reqs = []
    bus.subscribe("workflow.confirm_request", lambda *a: reqs.append(a))
    keys = []
    with patch.object(iw, "key_press", lambda k: keys.append(k)):
        c._on_gesture(GestureEvent("circle", "start", 0.9, "Right",
                                   0.5, 0.5, time.monotonic()))
        time.sleep(0.1)
        assert reqs
        c.resolve_confirmation(reqs[0][0], False)
        time.sleep(0.1)
    assert keys == []                            # declined → never runs
    c.shutdown()
    db.close()


def test_emergency_stop_voids_pending_confirmation(ctl):
    c, bus, db = ctl
    _map_confirm_workflow(c)
    c.set_motion_enabled(True)
    reqs = []
    bus.subscribe("workflow.confirm_request", lambda *a: reqs.append(a))
    keys = []
    with patch.object(iw, "key_press", lambda k: keys.append(k)):
        c._on_gesture(GestureEvent("circle", "start", 0.9, "Right",
                                   0.5, 0.5, time.monotonic()))
        time.sleep(0.1)
        assert reqs
        c.set_motion_enabled(False)              # 20 EMERGENCY STOP
        c.resolve_confirmation(reqs[0][0], True)  # late accept ignored
        time.sleep(0.1)
    assert keys == []                            # E-stop authoritative
    c.shutdown()
    db.close()


def test_confirmation_off_runs_normally(tmp_path, monkeypatch):
    monkeypatch.setenv("MGA_DATA_DIR", str(tmp_path))
    cfg = Config()
    cfg.default_cooldown_ms = 0
    cfg.action_global_cooldown_ms = 0
    cfg.confirm_dangerous_workflows = False       # setting OFF
    db = Database(tmp_path / "off.db")
    bus = EventBus()
    c = MotionController(cfg, db, bus)
    c.workflows.POLL_S = 0.02
    c.camera = CameraWorker(bus, opener=lambda *a: FakeCap(), target_fps=120)
    c.tracker = StubTracker()
    _map_confirm_workflow(c)
    c.set_motion_enabled(True)
    reqs, done = [], threading.Event()
    bus.subscribe("workflow.confirm_request", lambda *a: reqs.append(a))
    bus.subscribe("workflow.done", lambda *a: done.set())
    keys = []
    with patch.object(iw, "key_press", lambda k: keys.append(k)):
        c._on_gesture(GestureEvent("circle", "start", 0.9, "Right",
                                   0.5, 0.5, time.monotonic()))
        assert done.wait(3.0)
    assert reqs == []                            # no prompt when off
    assert keys == ["k"]
    c.shutdown()
    db.close()


# ---------------------------------------------------- UI smoke ----------------
def test_command_center_page_smoke(tmp_path, monkeypatch):
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    from app.ui.bridge import QtBridge
    from app.ui.command_center import (CommandCenterPage, QuickAssignDialog,
                                       gesture_kind)
    monkeypatch.setenv("MGA_DATA_DIR", str(tmp_path))
    app = QApplication.instance() or QApplication([])
    cfg = Config()
    db = Database(tmp_path / "ui.db")
    bus = EventBus()
    c = MotionController(cfg, db, bus)
    # seed one action mapping + one workflow mapping + a conflict
    pm = c.profile_manager
    a = pm.actions.create("K", "key_press", {"key": "k"})
    wid = pm.workflows.create("WF", [{"type": "delay", "ms": 10}])
    wfa = pm.actions.create("WF", "workflow", {"workflow_id": wid})
    gid = pm.profiles.by_name("Global").id
    pm.rules.create(gid, "circle", wfa)
    pm.rules.create(gid, "circle", a)          # conflict row
    pm.rules.create(gid, "swipe_right", a)
    c.reload_rules()
    bridge = QtBridge(bus)
    page = CommandCenterPage(c, bridge)
    page.refresh()
    # seeded Global starter rules + the 3 added here
    assert page.table.rowCount() == len(pm.rules.all())
    assert page.table.rowCount() >= 3
    assert gesture_kind(c, "circle") == "circle"
    assert gesture_kind(c, "swipe_right") == "swipe"
    # select first row → preview builds a chain without error
    page.table.selectRow(0)
    app.processEvents()
    assert "COMPLETE" in page.preview.toPlainText()
    # live feedback + activity update via bus events (GUI thread)
    bus.publish("rule.matched", *_match("circle", "WF"))
    app.processEvents()
    assert "DETECTED" in page.feedback.text()
    assert page.activity.count() >= 1
    # quick-assign dialog builds
    dlg = QuickAssignDialog(c, parent=page)
    assert dlg.gesture_combo.count() > 0
    c.shutdown()
    db.close()
