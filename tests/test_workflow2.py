"""Workflow 2.0 — builder, triggers, validation, list, feedback.

Engine semantics (success/failure/cancel/E-stop/duplicate guard) are
covered by test_workflows.py / test_workflow_conditions.py /
test_shutdown.py; this file covers the 2.0 additions.
"""
import os
import time
from unittest.mock import patch

import pytest

import app.actions.input_win as iw
from app.core.config import Config
from app.core.events import EventBus
from app.core.types import Context, GestureEvent
from app.data.db import Database
from app.runtime.controller import MotionController

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture
def ctl(tmp_path, monkeypatch):
    monkeypatch.setenv("MGA_DATA_DIR", str(tmp_path))
    cfg = Config()
    cfg.default_cooldown_ms = 0
    cfg.action_global_cooldown_ms = 0
    db = Database(tmp_path / "w2.db")
    bus = EventBus()
    c = MotionController(cfg, db, bus)
    c.workflows.POLL_S = 0.03
    pm = c.profile_manager
    g = pm.profiles.by_name("Global")
    for r in pm.rules.for_profile(g.id):
        pm.rules.delete(r.id)
    c.reload_rules()
    c.set_motion_enabled(True)
    return c, bus


def key_wf(c, name, key="k", extra_steps=()):
    pm = c.profile_manager
    aid = pm.actions.create(f"{name} key", "key_press", {"key": key})
    steps = [{"type": "action", "action_id": aid}] + list(extra_steps)
    wid = c.workflow_repo.create(name, steps)
    a = pm.actions.create(name, "workflow", {"workflow_id": wid})
    return wid, a


def ev(gesture, phase):
    return GestureEvent(gesture=gesture, phase=phase, confidence=0.9,
                        handedness="Right", hand_x=0.5, hand_y=0.5,
                        timestamp=time.monotonic())


def wait_for(pred, timeout=3.0):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        if pred():
            return True
        time.sleep(0.01)
    return False


# -- data layer ---------------------------------------------------------------
def test_description_and_migration(tmp_path, ctl):
    c, _ = ctl
    wid = c.workflow_repo.create("Described", [{"type": "delay", "ms": 10}],
                                 description="opens things")
    assert c.workflow_repo.get(wid).description == "opens things"
    c.workflow_repo.update(wid, description="new text")
    assert c.workflow_repo.get(wid).description == "new text"
    # migration: a DB created without the column gains it on open
    import sqlite3
    legacy = tmp_path / "legacy.db"
    con = sqlite3.connect(legacy)
    con.execute("CREATE TABLE workflows (id INTEGER PRIMARY KEY, "
                "name TEXT NOT NULL UNIQUE, steps_json TEXT NOT NULL, "
                "enabled INTEGER NOT NULL DEFAULT 1)")
    con.execute("INSERT INTO workflows(name, steps_json) "
                "VALUES('old', '[{\"type\":\"delay\",\"ms\":50}]')")
    con.commit()
    con.close()
    from app.data.repository import WorkflowRepo
    db2 = Database(legacy)
    repo = WorkflowRepo(db2)
    row = repo.by_name("old")
    assert row is not None and row.description == ""
    db2.close()


def test_export_import_description(tmp_path, monkeypatch):
    monkeypatch.setenv("MGA_DATA_DIR", str(tmp_path))
    from app.profiles.manager import ProfileManager
    pm = ProfileManager(Database(tmp_path / "p.db"))
    pm.seed_defaults()
    step = pm.actions.create("S", "key_press", {"key": "a"})
    wid = pm.workflows.create("WF", [{"type": "action", "action_id": step}],
                              description="does a thing")
    a = pm.actions.create("WF", "workflow", {"workflow_id": wid})
    pid = pm.profiles.create("X", "application", [{"process_name": "x.exe"}])
    pm.rules.create(pid, "circle", a)
    f = tmp_path / "x.json"
    pm.export_profile(pid, f)
    for r in pm.rules.for_profile(pid):
        pm.rules.delete(r.id)
    pm.profiles.delete(pid)
    pm.actions.delete(a)
    pm.actions.delete(step)
    pm.workflows.delete(wid)
    nid = pm.import_profile(f)
    rule = pm.rules.for_profile(nid)[0]
    na = pm.actions.get(rule.action_id)
    nwf = pm.workflows.get(na.params["workflow_id"])
    assert nwf.description == "does a thing"
    assert rule.gesture == "circle"


# -- triggers through the existing rule engine --------------------------------
@pytest.mark.parametrize("gesture", ["circle", "my_zigzag", "swipe_left",
                                     "pinch"])
def test_any_gesture_triggers_workflow(ctl, gesture):
    c, bus = ctl
    keys = []
    _, a = key_wf(c, f"WF {gesture}", key="w")
    g = c.profile_manager.profiles.by_name("Global")
    c.profile_manager.rules.create(g.id, gesture, a)
    c.reload_rules()
    c.context.current = Context(application="notepad",
                                process="notepad.exe", window_title="x")
    with patch.object(iw, "key_press", lambda k: keys.append(k)):
        bus.publish("gesture.event", ev(gesture, "start"))
        bus.publish("gesture.event", ev(gesture, "end"))
        assert wait_for(lambda: keys == ["w"]), gesture


def test_compound_trigger_and_duplicate_guard(ctl):
    c, bus = ctl
    keys = []
    pm = c.profile_manager
    aid = pm.actions.create("slow key", "key_press", {"key": "w"})
    wid = c.workflow_repo.create("Slow WF", [
        {"type": "action", "action_id": aid},
        {"type": "delay", "ms": 600}])
    a = pm.actions.create("Slow WF", "workflow", {"workflow_id": wid})
    c.compound_gestures.create(
        "double_pinch", [{"type": "gesture", "gesture": "pinch"}] * 2,
        step_timeout_ms=250, cooldown_ms=0)
    g = pm.profiles.by_name("Global")
    pm.rules.create(g.id, "double_pinch", a)
    c.reload_rules()
    c.context.current = Context(application="notepad",
                                process="notepad.exe", window_title="x")
    with patch.object(iw, "key_press", lambda k: keys.append(k)):
        for _ in range(2):
            bus.publish("gesture.event", ev("pinch", "start"))
            bus.publish("gesture.event", ev("pinch", "end"))
            time.sleep(0.05)
        assert wait_for(lambda: keys == ["w"])
        # re-trigger while running → duplicate guard, still one instance
        started, reason = c.workflows.start(wid)
        assert not started and "already running" in reason
    assert keys == ["w"]


# -- builder (offscreen) ------------------------------------------------------
@pytest.fixture(scope="module")
def qapp():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_builder_trigger_validation_duplicate(qapp, ctl, monkeypatch):
    c, bus = ctl
    from app.ui.actions_page import WorkflowBuilderDialog, WorkflowsSection
    pm = c.profile_manager
    aid = pm.actions.create("Press W", "key_press", {"key": "w"})

    # create through the builder with a trigger
    dlg = WorkflowBuilderDialog(c)
    dlg.name_edit.setText("Built WF")
    dlg.desc_edit.setText("built by test")
    dlg._steps = [{"type": "action", "action_id": aid},
                  {"type": "delay", "ms": 20}]
    gi = dlg.trigger_combo.findData("circle")
    assert gi > 0
    dlg.trigger_combo.setCurrentIndex(gi)
    dlg._save()
    wf = c.workflow_repo.by_name("Built WF")
    assert wf is not None and wf.description == "built by test"
    a = pm.actions.by_name("Built WF")
    assert a.type == "workflow"
    rules = [r for r in pm.rules.all() if r.action_id == a.id]
    assert len(rules) == 1 and rules[0].gesture == "circle"

    # the trigger actually fires the workflow end-to-end
    keys = []
    c.context.current = Context(application="notepad",
                                process="notepad.exe", window_title="x")
    import app.actions.input_win as iw2
    with patch.object(iw2, "key_press", lambda k: keys.append(k)):
        bus.publish("gesture.event", ev("circle", "start"))
        bus.publish("gesture.event", ev("circle", "end"))
        assert wait_for(lambda: keys == ["w"])

    # editing preselects the trigger; changing it updates the same rule
    dlg2 = WorkflowBuilderDialog(c, wf.id)
    assert dlg2.trigger_combo.currentData() == "circle"
    gi2 = dlg2.trigger_combo.findData("pinch")
    dlg2.trigger_combo.setCurrentIndex(gi2)
    dlg2._save()
    rules = [r for r in pm.rules.all() if r.action_id == a.id]
    assert len(rules) == 1 and rules[0].gesture == "pinch"

    # section: duplicate + toggle + trigger label (while refs are valid)
    sec = WorkflowsSection(c, on_change=lambda: None)
    sec.refresh()
    sec.listw.setCurrentRow(0)
    n_before = len(c.workflow_repo.all())
    sec._duplicate()
    assert len(c.workflow_repo.all()) == n_before + 1
    copy = c.workflow_repo.by_name("Built WF (copy)")
    assert copy is not None
    assert pm.actions.by_name("Built WF (copy)").type == "workflow"
    assert "pinch" in sec._trigger_of(c.workflow_repo.get(wf.id))
    sec.listw.setCurrentRow(
        [w.id for w in c.workflow_repo.all()].index(wf.id))
    sec._toggle()
    assert c.workflow_repo.get(wf.id).enabled is False

    # deep validation catches a dead action reference
    dlg3 = WorkflowBuilderDialog(c, wf.id)
    pm.actions.delete(aid)
    problems = dlg3._validate_deep()
    assert problems and "action" in problems[0]
    # save with problems is rejected (workflow unchanged)
    monkeypatch.setattr("app.ui.actions_page.QMessageBox.warning",
                        staticmethod(lambda *a, **k: None))
    before = c.workflow_repo.get(wf.id).steps
    dlg3._save()
    assert c.workflow_repo.get(wf.id).steps == before
    # duplicating a broken workflow warns instead of crashing
    sec.refresh()
    sec.listw.setCurrentRow(
        [w.id for w in c.workflow_repo.all()].index(wf.id))
    n = len(c.workflow_repo.all())
    sec._duplicate()
    assert len(c.workflow_repo.all()) == n
    for d in (dlg, dlg2, dlg3):
        d.done(0)


def test_dashboard_checklist(qapp, ctl):
    c, _ = ctl
    from app.ui.bridge import QtBridge
    from app.ui.dashboard import Dashboard
    pm = c.profile_manager
    aid = pm.actions.create("Step A", "key_press", {"key": "a"})
    c.workflow_repo.create("Checklist WF", [
        {"type": "action", "action_id": aid},
        {"type": "delay", "ms": 1500},
        {"type": "action", "action_id": aid}])
    bridge = QtBridge(c.bus)
    dash = Dashboard(c, bridge)
    dash._on_workflow_progress("Checklist WF", 2, 3, "Wait 1500 ms",
                               "running")
    text = dash.v_workflow.text()
    assert "✓ Step A" in text and "● Wait 1500 ms" in text \
        and "○ Step A" in text
    dash._on_workflow_done("Checklist WF", "cancelled", "")
    assert "■" in dash.v_workflow.text()
    dash._on_workflow_done("Checklist WF", "failed",
                           "step 2: condition not satisfied")
    assert "condition not satisfied" in dash.v_workflow.text()
    dash.close()
