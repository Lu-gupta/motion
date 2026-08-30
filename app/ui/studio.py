"""Gesture Studio — the mapping editor.

Profile → Gesture → Action → conditions → save/test/toggle.
No JSON editing needed for normal use.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog,
                               QDialogButtonBox, QDoubleSpinBox, QFormLayout,
                               QGroupBox, QHBoxLayout, QInputDialog, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem,
                               QMessageBox, QPushButton, QSpinBox,
                               QTableWidget, QTableWidgetItem, QVBoxLayout,
                               QWidget)

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QProgressBar

from ..core.types import ActionSpec, Context
from ..gestures.engine import all_gesture_names
from ..runtime.controller import MotionController
from .bridge import QtBridge
from .video_widget import VideoWidget


class TestRecognitionDialog(QDialog):
    """SAFE gesture test: recognition only, actions never execute.

    Motion control is force-disabled while the dialog is open and
    restored on close. Shows live tracking, detection state, confidence
    and the action that WOULD run. Executing that action requires the
    explicit button.
    """

    def __init__(self, ctl: MotionController, bridge: QtBridge,
                 gesture: str, parent=None) -> None:
        super().__init__(parent)
        self.ctl = ctl
        self.gesture = gesture
        self.setWindowTitle(f"Test recognition — {gesture}")
        self.resize(560, 520)

        self._was_enabled = ctl.motion_enabled
        ctl.set_motion_enabled(False)  # SAFE: nothing executes while open

        self.video = VideoWidget(mirror=ctl.cfg.mirror_preview)
        self.video.setMinimumSize(320, 240)
        bridge.frame.connect(self.video.on_frame)
        bridge.hands.connect(self.video.on_hands)
        self._bridge = bridge

        self.hint = QLabel(
            f"Perform  {gesture.replace('_', ' ').upper()}  in front of "
            "the camera. Actions will NOT run in this mode.")
        self.hint.setWordWrap(True)

        self.is_circle = gesture == "circle"
        self.detected = QLabel("WAITING")
        self.detected.setStyleSheet("font-size:18px; font-weight:700;")
        self.conf_bar = QProgressBar()
        self.conf_bar.setRange(0, 100)
        self.conf_bar.setFormat("confidence %p%")
        self.state_lbl = QLabel("WAITING")
        self.conf_lbl = QLabel("—")
        self.cooldown_lbl = QLabel("READY")
        self.tracking_lbl = QLabel("—")
        self.reason_lbl = QLabel("—")
        self.reason_lbl.setWordWrap(True)
        self.context_lbl = QLabel("—")
        self.context_lbl.setWordWrap(True)
        self.resolved = QLabel("—")
        self.resolved.setWordWrap(True)

        form = QFormLayout()
        form.addRow("Detected:", self.detected)
        form.addRow("State:", self.state_lbl)
        form.addRow("Confidence / threshold:", self.conf_lbl)
        form.addRow("", self.conf_bar)
        form.addRow("Cooldown:", self.cooldown_lbl)
        form.addRow("Tracking:", self.tracking_lbl)
        form.addRow("Reason:", self.reason_lbl)
        form.addRow("Context:", self.context_lbl)
        form.addRow("Would run:", self.resolved)

        self.circle_lbl = QLabel("—")
        self.circle_lbl.setWordWrap(True)
        self.circle_box = QGroupBox("Circle diagnostics")
        cb = QVBoxLayout(self.circle_box)
        cb.addWidget(self.circle_lbl)
        self.circle_box.setVisible(self.is_circle)

        # bounded in-memory detection history (last 50) — never a DB
        self.history = QTableWidget(0, 4)
        self.history.setHorizontalHeaderLabels(
            ["Time", "Gesture", "Confidence", "Result"])
        self.history.setEditTriggers(QTableWidget.NoEditTriggers)
        self.history.setMaximumHeight(150)
        self.history.horizontalHeader().setStretchLastSection(True)
        clear_hist = QPushButton("Clear history")
        clear_hist.clicked.connect(lambda: self.history.setRowCount(0))

        self.exec_btn = QPushButton("Test full action (runs it!)")
        self.exec_btn.clicked.connect(self._execute)
        close_btn = QDialogButtonBox(QDialogButtonBox.Close)
        close_btn.rejected.connect(self.reject)
        close_btn.clicked.connect(self.accept)

        lay = QVBoxLayout(self)
        lay.addWidget(self.hint)
        lay.addWidget(self.video, 1)
        lay.addLayout(form)
        lay.addWidget(self.circle_box)
        lay.addWidget(QLabel("Detection history (latest 50):"))
        lay.addWidget(self.history)
        lay.addWidget(clear_hist)
        lay.addWidget(self.exec_btn)
        lay.addWidget(close_btn)

        self._hits = 0
        self._match: object = None
        self._last_state = ""
        self._poll = QTimer(self)
        self._poll.timeout.connect(self._tick)
        self._poll.start(120)

    def _add_history(self, gesture: str, conf, result: str) -> None:
        import time as _t
        lt = _t.localtime()
        stamp = f"{lt.tm_hour:02d}:{lt.tm_min:02d}:{lt.tm_sec:02d}"
        self.history.insertRow(0)
        vals = [stamp, gesture,
                "—" if conf is None else f"{conf:.2f}", result]
        for c, v in enumerate(vals):
            self.history.setItem(0, c, QTableWidgetItem(v))
        while self.history.rowCount() > 50:
            self.history.removeRow(self.history.rowCount() - 1)

    def _tick(self) -> None:
        diag = self.ctl.gestures.diagnostics(self.gesture)
        state = diag["state"]
        conf = diag["confidence"]
        thr = diag["threshold"]
        self.state_lbl.setText(state)
        matched = state == "MATCH"
        self.detected.setText(f"✓ {self.gesture} — MATCH" if matched
                              else (self.gesture if state in (
                                  "TRACKING", "CANDIDATE", "COOLDOWN")
                                  else "WAITING"))
        self.detected.setStyleSheet(
            "font-size:18px; font-weight:700; color:"
            + ("#1a9e4b" if matched else "#71847a") + ";")
        if conf is not None:
            self.conf_bar.setValue(int(conf * 100))
        self.conf_lbl.setText(
            (f"{conf:.2f}" if conf is not None else "—")
            + f"  /  required {thr:.2f}"
            + ("   (swipes are rule-based, not confidence)"
               if thr >= 1.0 else ""))
        rem = diag["cooldown_remaining_ms"]
        self.cooldown_lbl.setText(f"COOLDOWN — {rem} ms" if rem > 0
                                  else "READY")
        self.tracking_lbl.setText("GOOD" if diag["tracking"]
                                  else "NO TRACKING")
        self.reason_lbl.setText(diag["reason"] or "—")
        if self.is_circle and diag.get("shape"):
            s = diag["shape"]
            self.circle_lbl.setText(
                f"Direction: {s.get('direction', '—')}   "
                f"Movement: {s.get('movement', '—')}   "
                f"Closure: {s.get('closure', '—')}   "
                f"Sweep: {s.get('sweep_deg', '—')}°   "
                f"Confidence: {s.get('confidence', '—')}   "
                f"Result: {s.get('result', '—')}")
        # record transitions into MATCH / COOLDOWN into the history
        if state != self._last_state and state in ("MATCH", "COOLDOWN"):
            self._add_history(self.gesture, conf, state)
        self._last_state = state
        ctx = self.ctl.context.current
        self.context_lbl.setText(
            f"App: {ctx.application or '—'}   "
            f"Window: {ctx.window_title or '—'}")
        self._match = self.ctl.rules.resolve(self.gesture, ctx,
                                             self.ctl._screen_size())
        if self._match:
            self.resolved.setText(
                f"{self._match.action.name or self._match.action.type}   "
                f"(profile: {self._match.profile_name})   "
                "— SAFE TEST ONLY, nothing runs")
        else:
            self.resolved.setText(
                f"no rule matches in {ctx.application or 'this context'}")

    def _execute(self) -> None:
        if not self._match:
            QMessageBox.information(self, "No action",
                                    "No rule resolves here — nothing to run.")
            return
        if self._match.action.type == "cursor_control":
            QMessageBox.information(
                self, "Continuous action",
                "Cursor control runs while the gesture is held — enable "
                "motion control and hold the gesture instead.")
            return
        if self._match.action.type == "launch_app":
            name = self._match.action.name or "application"
            if QMessageBox.question(
                    self, "Launch application",
                    f"Launch {name!r} now?",
                    QMessageBox.Cancel | QMessageBox.Yes,
                    QMessageBox.Cancel) != QMessageBox.Yes:
                return
        if self._match.action.type == "workflow":
            name = self._match.action.name or "workflow"
            if QMessageBox.question(
                    self, "Run workflow",
                    f"This workflow will run real actions.\n\n"
                    f"Run {name!r} now?",
                    QMessageBox.Cancel | QMessageBox.Yes,
                    QMessageBox.Cancel) != QMessageBox.Yes:
                return
        self.ctl.executor.execute(self._match.action)

    def done(self, r: int) -> None:
        self._poll.stop()
        try:
            self._bridge.frame.disconnect(self.video.on_frame)
            self._bridge.hands.disconnect(self.video.on_hands)
        except (RuntimeError, TypeError):
            pass
        self.ctl.set_motion_enabled(self._was_enabled)  # restore safety state
        super().done(r)


class ZoneManagerDialog(QDialog):
    """CRUD for named screen zones (normalized 0–100% coordinates)."""

    def __init__(self, ctl: MotionController, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Screen zones")
        self.ctl = ctl
        self.resize(460, 320)

        self.listw = QListWidget()
        add_btn = QPushButton("Add")
        add_btn.clicked.connect(self._add)
        edit_btn = QPushButton("Edit")
        edit_btn.clicked.connect(self._edit)
        del_btn = QPushButton("Delete")
        del_btn.clicked.connect(self._delete)
        btns = QHBoxLayout()
        for b in (add_btn, edit_btn, del_btn):
            btns.addWidget(b)
        btns.addStretch(1)

        close = QDialogButtonBox(QDialogButtonBox.Close)
        close.rejected.connect(self.reject)
        close.clicked.connect(self.accept)

        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Zones restrict a mapping to a screen region "
                             "(cursor position, % of primary screen)."))
        lay.addLayout(btns)
        lay.addWidget(self.listw)
        lay.addWidget(close)
        self.refresh()

    def refresh(self) -> None:
        self.listw.clear()
        for z in self.ctl.zones.all():
            item = QListWidgetItem(
                f"{z.name}   x {z.x0 * 100:.0f}–{z.x1 * 100:.0f}%   "
                f"y {z.y0 * 100:.0f}–{z.y1 * 100:.0f}%")
            item.setData(32, z.id)
            self.listw.addItem(item)

    def _zone_form(self, title: str, z=None):
        dlg = QDialog(self)
        dlg.setWindowTitle(title)
        name = QLineEdit(z.name if z else "")
        spins = {}
        for key, label, val in (("x0", "Left %", z.x0 if z else 0.0),
                                ("x1", "Right %", z.x1 if z else 0.5),
                                ("y0", "Top %", z.y0 if z else 0.0),
                                ("y1", "Bottom %", z.y1 if z else 0.5)):
            s = QDoubleSpinBox()
            s.setRange(0.0, 100.0)
            s.setSuffix(" %")
            s.setValue(val * 100)
            spins[key] = s
        form = QFormLayout(dlg)
        form.addRow("Name:", name)
        for key, label in (("x0", "Left"), ("x1", "Right"), ("y0", "Top"),
                           ("y1", "Bottom")):
            form.addRow(f"{label}:", spins[key])
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        form.addRow(bb)
        if not dlg.exec():
            return None
        n = name.text().strip()
        if not n:
            QMessageBox.warning(self, "Invalid", "Name required.")
            return None
        return (n, spins["x0"].value() / 100, spins["y0"].value() / 100,
                spins["x1"].value() / 100, spins["y1"].value() / 100)

    def _add(self) -> None:
        res = self._zone_form("New zone")
        if res:
            existing = {z.name for z in self.ctl.zones.all()}
            if res[0] in existing:
                QMessageBox.warning(self, "Duplicate", "Zone name exists.")
                return
            self.ctl.zones.create(*res)
            self._done()

    def _selected(self):
        it = self.listw.currentItem()
        if not it:
            return None
        for z in self.ctl.zones.all():
            if z.id == it.data(32):
                return z
        return None

    def _edit(self) -> None:
        z = self._selected()
        if not z:
            return
        res = self._zone_form("Edit zone", z)
        if res:
            self.ctl.zones.update(z.id, *res)
            self._done()

    def _delete(self) -> None:
        z = self._selected()
        if not z:
            return
        if QMessageBox.question(self, "Delete",
                                f"Delete zone {z.name!r}? Rules using it "
                                "will stop matching.") == QMessageBox.Yes:
            self.ctl.zones.delete(z.id)
            self._done()

    def _done(self) -> None:
        self.ctl.reload_rules()
        self.refresh()


class StudioPage(QWidget):
    def __init__(self, ctl: MotionController,
                 bridge: QtBridge | None = None) -> None:
        super().__init__()
        self.ctl = ctl
        self.bridge = bridge
        self.pm = ctl.profile_manager
        self._editing_rule_id: int | None = None

        # -- guided editor: gesture → action → where → test → save ----------
        self.gesture_combo = QComboBox()
        self.action_combo = QComboBox()
        self.profile_combo = QComboBox()
        self.window_edit = QLineEdit()
        self.window_edit.setPlaceholderText(
            "optional window-title condition, e.g. *docs* (empty = any)")
        self.zone_combo = QComboBox()
        self.zone_btn = QPushButton("Manage zones…")
        self.zone_btn.clicked.connect(self._manage_zones)
        zone_row = QHBoxLayout()
        zone_row.addWidget(self.zone_combo, 1)
        zone_row.addWidget(self.zone_btn)
        self.continuous_check = QCheckBox(
            "Continuous (re-fires while gesture is held)")
        self.cooldown_spin = QSpinBox()
        self.cooldown_spin.setRange(0, 10000)
        self.cooldown_spin.setSuffix(" ms")
        self.cooldown_spin.setSpecialValueText("default")

        step1 = QGroupBox("1 · Choose gesture")
        f1 = QFormLayout(step1)
        f1.addRow("When I perform:", self.gesture_combo)

        step2 = QGroupBox("2 · Choose action")
        f2 = QFormLayout(step2)
        f2.addRow("Do this:", self.action_combo)

        step3 = QGroupBox("3 · Choose where it works")
        f3 = QFormLayout(step3)
        f3.addRow("In profile:", self.profile_combo)
        f3.addRow("Window title:", self.window_edit)
        f3.addRow("Screen zone:", zone_row)
        f3.addRow("", self.continuous_check)
        f3.addRow("Cooldown:", self.cooldown_spin)

        steps_row = QHBoxLayout()
        steps_row.addWidget(step1, 1)
        steps_row.addWidget(step2, 1)
        steps_row.addWidget(step3, 2)

        self.test_rec_btn = QPushButton("4 · Test recognition (safe)")
        self.test_rec_btn.clicked.connect(self._test_recognition)
        self.test_btn = QPushButton("Test action (runs for real)")
        self.test_btn.clicked.connect(self._test_action)
        self.save_btn = QPushButton("5 · Save mapping")
        self.save_btn.setStyleSheet("font-weight:700;")
        self.save_btn.clicked.connect(self._save)
        self.new_btn = QPushButton("New mapping")
        self.new_btn.clicked.connect(self._clear_editor)
        btns = QHBoxLayout()
        btns.addWidget(self.test_rec_btn)
        btns.addWidget(self.test_btn)
        btns.addWidget(self.save_btn)
        btns.addWidget(self.new_btn)
        btns.addStretch(1)

        # -- rules table ----------------------------------------------------
        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(
            ["Profile", "Gesture", "Action", "Window", "Zone", "Continuous",
             "Cooldown", "Enabled"])
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.itemSelectionChanged.connect(self._load_selected)
        self.table.horizontalHeader().setStretchLastSection(True)

        self.toggle_btn = QPushButton("Enable/Disable selected")
        self.toggle_btn.clicked.connect(self._toggle_selected)
        self.delete_btn = QPushButton("Delete selected")
        self.delete_btn.clicked.connect(self._delete_selected)
        tbtns = QHBoxLayout()
        tbtns.addWidget(self.toggle_btn)
        tbtns.addWidget(self.delete_btn)
        tbtns.addStretch(1)

        lay = QVBoxLayout(self)
        lay.addLayout(steps_row)
        lay.addLayout(btns)
        lay.addWidget(QLabel("Existing mappings (select to edit):"))
        lay.addLayout(tbtns)
        lay.addWidget(self.table)
        self.refresh()

    # -- external entry points ----------------------------------------------
    def preselect_gesture(self, gesture: str) -> None:
        """Used by the custom-gesture recorder's 'map it now' flow."""
        self.refresh()
        self._clear_editor()
        idx = self.gesture_combo.findText(gesture)
        if idx >= 0:
            self.gesture_combo.setCurrentIndex(idx)

    def _test_recognition(self) -> None:
        if self.bridge is None:
            return
        gesture = self.gesture_combo.currentText()
        if not gesture:
            return
        TestRecognitionDialog(self.ctl, self.bridge, gesture, self).exec()

    # -- data ---------------------------------------------------------------
    def refresh(self) -> None:
        self.profile_combo.clear()
        for p in self.pm.profiles.all():
            self.profile_combo.addItem(p.name, p.id)
        self.gesture_combo.clear()
        for g in all_gesture_names(self.ctl.custom_gestures.all(),
                                   self.ctl.motion_gestures.all()):
            self.gesture_combo.addItem(g)
        for c in self.ctl.compound_gestures.all():  # compounds first-class
            self.gesture_combo.addItem(c.name)
        self.action_combo.clear()
        for a in self.pm.actions.all():
            self.action_combo.addItem(a.name, a.id)
        self.zone_combo.clear()
        self.zone_combo.addItem("(anywhere)", "")
        for z in self.ctl.zones.all():
            self.zone_combo.addItem(z.name, z.name)
        self._fill_table()

    def _fill_table(self) -> None:
        rules = []
        for p in self.pm.profiles.all():
            for r in self.pm.rules.for_profile(p.id):
                rules.append((p, r))
        self.table.setRowCount(len(rules))
        for row, (p, r) in enumerate(rules):
            a = self.pm.actions.get(r.action_id)
            vals = [p.name, r.gesture, a.name if a else "?",
                    r.window_pattern or "any",
                    r.zone or "anywhere",
                    "yes" if r.continuous else "no",
                    f"{r.cooldown_ms} ms" if r.cooldown_ms else "default",
                    "yes" if r.enabled else "no"]
            for col, v in enumerate(vals):
                item = QTableWidgetItem(v)
                if col == 0:
                    item.setData(Qt.UserRole, r.id)
                self.table.setItem(row, col, item)

    # -- editor -------------------------------------------------------------
    def _selected_rule_id(self) -> int | None:
        row = self.table.currentRow()
        if row < 0:
            return None
        return self.table.item(row, 0).data(Qt.UserRole)

    def _load_selected(self) -> None:
        rid = self._selected_rule_id()
        if rid is None:
            return
        r = self.pm.rules.get(rid)
        if not r:
            return
        self._editing_rule_id = rid
        p = self.pm.profiles.get(r.profile_id)
        a = self.pm.actions.get(r.action_id)
        self.profile_combo.setCurrentIndex(
            max(0, self.profile_combo.findData(r.profile_id)))
        self.gesture_combo.setCurrentText(r.gesture)
        if a:
            self.action_combo.setCurrentIndex(
                max(0, self.action_combo.findData(a.id)))
        self.window_edit.setText(r.window_pattern)
        zi = self.zone_combo.findData(r.zone)
        self.zone_combo.setCurrentIndex(max(0, zi))
        self.continuous_check.setChecked(r.continuous)
        self.cooldown_spin.setValue(r.cooldown_ms)
        self.save_btn.setText("Update mapping")

    def _manage_zones(self) -> None:
        ZoneManagerDialog(self.ctl, self).exec()
        current = self.zone_combo.currentData()
        self.zone_combo.clear()
        self.zone_combo.addItem("(anywhere)", "")
        for z in self.ctl.zones.all():
            self.zone_combo.addItem(z.name, z.name)
        idx = self.zone_combo.findData(current)
        self.zone_combo.setCurrentIndex(max(0, idx))

    def _clear_editor(self) -> None:
        self._editing_rule_id = None
        self.window_edit.clear()
        self.zone_combo.setCurrentIndex(0)
        self.continuous_check.setChecked(False)
        self.cooldown_spin.setValue(0)
        self.table.clearSelection()
        self.save_btn.setText("Save mapping")

    def _save(self) -> None:
        pid = self.profile_combo.currentData()
        aid = self.action_combo.currentData()
        gesture = self.gesture_combo.currentText()
        if pid is None or aid is None or not gesture:
            QMessageBox.warning(self, "Incomplete",
                                "Profile, gesture and action are required.")
            return
        kwargs = dict(window_pattern=self.window_edit.text().strip(),
                      zone=self.zone_combo.currentData() or "",
                      continuous=self.continuous_check.isChecked(),
                      cooldown_ms=self.cooldown_spin.value())
        if self._editing_rule_id is not None:
            self.pm.rules.update(self._editing_rule_id, gesture=gesture,
                                 action_id=aid, profile_id=pid, **kwargs)
        else:
            self.pm.rules.create(pid, gesture, aid, **kwargs)
        self.ctl.reload_rules()
        self._clear_editor()
        self._fill_table()

    def _test_action(self) -> None:
        aid = self.action_combo.currentData()
        if aid is None:
            return
        a = self.pm.actions.get(aid)
        if a.type == "cursor_control":
            QMessageBox.information(self, "Test",
                                    "Cursor control runs while the gesture "
                                    "is held — cannot test as one-shot.")
            return
        if a.type == "launch_app":
            if QMessageBox.question(
                    self, "Launch application",
                    f"Launch {a.name!r} now?\n\n{a.params.get('path', '?')}",
                    QMessageBox.Cancel | QMessageBox.Yes,
                    QMessageBox.Cancel) != QMessageBox.Yes:
                return
        if a.type == "workflow":
            if QMessageBox.question(
                    self, "Run workflow",
                    f"This workflow will run real actions.\n\n"
                    f"Run {a.name!r} now?",
                    QMessageBox.Cancel | QMessageBox.Yes,
                    QMessageBox.Cancel) != QMessageBox.Yes:
                return
        self.ctl.executor.execute(
            ActionSpec(type=a.type, params=a.params, name=a.name))

    # -- table ops ----------------------------------------------------------
    def _toggle_selected(self) -> None:
        rid = self._selected_rule_id()
        if rid is None:
            return
        r = self.pm.rules.get(rid)
        self.pm.rules.update(rid, enabled=not r.enabled)
        self.ctl.reload_rules()
        self._fill_table()

    def _delete_selected(self) -> None:
        rid = self._selected_rule_id()
        if rid is None:
            return
        if QMessageBox.question(self, "Delete",
                                "Delete this mapping?") == QMessageBox.Yes:
            self.pm.rules.delete(rid)
            self.ctl.reload_rules()
            self._clear_editor()
            self._fill_table()
