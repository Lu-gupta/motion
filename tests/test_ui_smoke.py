"""UI smoke test — instantiate the full window offscreen.

Catches broken imports, signal wiring, and page construction without
needing a display or camera.
"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


def test_main_window_constructs(qapp, tmp_path_factory, monkeypatch):
    tmp = tmp_path_factory.mktemp("uidata")
    monkeypatch.setenv("MGA_DATA_DIR", str(tmp))
    from app.core.config import Config
    from app.core.events import EventBus
    from app.data.db import Database
    from app.runtime.controller import MotionController
    from app.ui.main_window import MainWindow

    cfg = Config()
    db = Database(tmp / "ui.db")
    bus = EventBus()
    ctl = MotionController(cfg, db, bus)
    win = MainWindow(ctl, bus)

    assert win.pages.count() == 7
    # nav switches pages and triggers refresh without error
    for i in range(win.nav.count()):
        win.nav.setCurrentRow(i)
        assert win.pages.currentIndex() == i

    # dashboard reflects controller state changes
    ctl.set_motion_enabled(True)
    qapp.processEvents()
    assert win.dashboard.v_motion.text() == "ON"
    ctl.emergency_disable()
    qapp.processEvents()
    assert win.dashboard.v_motion.text() == "OFF"

    # studio has seeded profiles/gestures/actions loaded
    s = win.studio_page
    assert s.profile_combo.count() >= 1
    assert s.gesture_combo.count() >= 9
    assert s.action_combo.count() >= 20
    assert s.table.rowCount() >= 7  # seeded global rules

    # test-recognition dialog: SAFE — disables motion while open, restores
    ctl.set_motion_enabled(True)
    from app.ui.studio import TestRecognitionDialog
    dlg = TestRecognitionDialog(ctl, win.bridge, "pinch", win.studio_page)
    assert ctl.motion_enabled is False          # nothing can execute
    dlg._tick()                                  # resolves without error
    assert "Left click" in dlg.resolved.text() or "no rule" in dlg.resolved.text()
    dlg.done(0)
    assert ctl.motion_enabled is True            # restored

    # recorder 'map it now' flow preselects the gesture in Studio
    win._open_studio_for("pinch")
    assert win.pages.currentIndex() == win.PAGES.index("Gesture Studio")
    assert win.studio_page.gesture_combo.currentText() == "pinch"

    # per-gesture tune dialog persists + hot-applies
    from app.ui.gestures_page import GestureTuneDialog
    tune = GestureTuneDialog(ctl, "pinch", win.gestures_page)
    tune.widgets["confidence"].setValue(0.9)
    tune._save()
    assert ctl.gesture_settings.get("pinch")["confidence"] == 0.9
    assert ctl.gestures._threshold_for("pinch") == 0.9
    swipe_tune = GestureTuneDialog(ctl, "swipe_left", win.gestures_page)
    assert swipe_tune.key == "swipe"             # group settings
    swipe_tune.widgets["min_distance"].setValue(0.25)
    swipe_tune._save()
    assert ctl.gestures.swipes.min_distance == 0.25

    # dashboard flash reacts to gesture events
    from app.core.types import GestureEvent
    ctl.bus_publish = None
    win.bridge.gesture.emit(GestureEvent(
        gesture="swipe_right", phase="start", confidence=0.91,
        handedness="Right", hand_x=0.5, hand_y=0.5))
    qapp.processEvents()
    assert "SWIPE RIGHT" in win.dashboard.flash.text()

    # compound gestures: builder saves through UI path; Studio lists name;
    # safe compound test dialog disables motion and restores it
    from app.ui.compounds import (CompoundBuilderDialog, CompoundTestDialog,
                                  steps_summary)
    builder = CompoundBuilderDialog(ctl, parent=win.gestures_page)
    builder.name_edit.setText("double pinch")
    builder.table.setRowCount(0)
    builder._add_step({"type": "gesture", "gesture": "pinch"})
    builder._add_step({"type": "gesture", "gesture": "pinch"})
    builder._save()
    row = ctl.compound_gestures.by_name("double_pinch")
    assert row is not None
    assert steps_summary(row.steps) == "pinch  →  pinch"
    assert len(ctl.compounds.defs) == 1          # hot-loaded into the engine
    win.studio_page.refresh()
    assert win.studio_page.gesture_combo.findText("double_pinch") >= 0

    ctl.set_motion_enabled(True)
    tdlg = CompoundTestDialog(ctl, win.bridge, row, win.gestures_page)
    assert ctl.motion_enabled is False
    tdlg._tick()                                  # renders step progress
    assert "pinch" in tdlg.steps_label.text()
    tdlg.done(0)
    assert ctl.motion_enabled is True
    ctl.set_motion_enabled(False)

    # duplicate name rejected against compound names too
    import app.ui.compounds as compounds_mod
    monkeypatch.setattr(compounds_mod.QMessageBox, "warning",
                        staticmethod(lambda *a, **k: None))
    builder2 = CompoundBuilderDialog(ctl, parent=win.gestures_page)
    builder2.name_edit.setText("double_pinch")
    builder2.table.setRowCount(0)
    builder2._add_step({"type": "gesture", "gesture": "fist"})
    builder2._save()                              # warning path, no accept
    assert len(ctl.compound_gestures.all()) == 1

    # workflows: builder saves through the UI path; the mappable action
    # appears; dashboard workflow row binds to live events
    from app.ui.actions_page import WorkflowBuilderDialog
    step_action = ctl.profile_manager.actions.by_name("Left click")
    wb = WorkflowBuilderDialog(ctl, parent=win.actions_page)
    wb.name_edit.setText("Open YouTube")
    wb._steps = [{"type": "action", "action_id": step_action.id},
                 {"type": "delay", "ms": 1500}]
    wb._save()
    wf = ctl.workflow_repo.by_name("Open YouTube")
    assert wf is not None and len(wf.steps) == 2
    wa = ctl.profile_manager.actions.by_name("Open YouTube")
    assert wa is not None and wa.type == "workflow"
    assert wa.params["workflow_id"] == wf.id
    win.actions_page.workflows.refresh()
    assert win.actions_page.workflows.listw.count() == 1
    win.studio_page.refresh()
    assert win.studio_page.action_combo.findText(
        "Open YouTube", ) >= 0 or any(
        "Open YouTube" in win.studio_page.action_combo.itemText(i)
        for i in range(win.studio_page.action_combo.count()))

    win.bridge.workflow_progress.emit("Open YouTube", 2, 3, "Wait 1500 ms",
                                      "running")
    qapp.processEvents()
    assert "2/3" in win.dashboard.v_workflow.text()
    win.bridge.workflow_done.emit("Open YouTube", "completed", "")
    qapp.processEvents()
    assert "✓" in win.dashboard.v_workflow.text()

    # smart wait step: dialog builds a valid condition step; builder
    # renders its label; repo accepts it
    from app.ui.actions_page import WorkflowStepDialog
    sd = WorkflowStepDialog(ctl.profile_manager.actions.all(),
                            parent=win.actions_page)
    sd.kind_combo.setCurrentIndex(2)             # wait for condition
    sd.cond_combo.setCurrentIndex(0)             # application running
    sd.process_edit.setText("chrome.exe")
    sd.timeout_spin.setValue(10)
    step = sd.step()
    assert step == {"type": "wait", "condition": "app_running",
                    "process": "chrome.exe", "title": "",
                    "timeout_ms": 10_000}
    sd.done(0)
    assert ctl.workflow_repo.validate([step]) is None
    wb2 = WorkflowBuilderDialog(ctl, parent=win.actions_page)
    assert "chrome.exe" in wb2._label(step)
    wb2.done(0)

    # trajectory gesture: listed, tunable, mappable in Studio
    assert win.studio_page.gesture_combo.findText("circle") >= 0
    listed = [win.gestures_page.builtin_list.item(i).text()
              for i in range(win.gestures_page.builtin_list.count())]
    assert any(t.startswith("circle") for t in listed)
    ctune = GestureTuneDialog(ctl, "circle", win.gestures_page)
    assert ctune.key == "circle" and "min_size" in ctune.widgets
    ctune.widgets["min_size"].setValue(0.2)
    ctune._save()
    assert ctl.gestures.trajectories.detector("circle").min_diameter == 0.2
    ctune2 = GestureTuneDialog(ctl, "circle", win.gestures_page)
    ctune2._reset()
    # dashboard trail signal wired
    win.bridge.trajectory_candidate.emit([(0.4, 0.4), (0.5, 0.5)], 0.0)
    qapp.processEvents()
    assert win.dashboard.video._trail == [(0.4, 0.4), (0.5, 0.5)]

    # motion gestures: repo-created shape shows in the list and in Studio
    from tests.test_motion_gestures import z_template
    ctl.motion_gestures.create("my_z", z_template())
    ctl.reload_rules()
    win.gestures_page.refresh()
    assert win.gestures_page.motion_list.count() == 1
    assert "my_z" in win.gestures_page.motion_list.item(0).text()
    win.studio_page.refresh()
    assert win.studio_page.gesture_combo.findText("my_z") >= 0
    assert any(d.name == "my_z"
               for d in ctl.gestures.trajectories.detectors)
    # duplicate name blocked for new motion gestures
    assert win.gestures_page._name_taken("my_z")
    assert win.gestures_page._name_taken("circle")

    # workflow type is not offered in the generic action editor
    from app.ui.actions_page import ActionEditDialog
    aed = ActionEditDialog(ctl.executor, parent=win.actions_page)
    assert all(aed.type_combo.itemData(i) != "workflow"
               for i in range(aed.type_combo.count()))
    aed.done(0)

    win.tray.hide()
    win.close()
    db.close()
