"""Gesture Command Center — a UX/orchestration layer over the existing
rule engine, profiles, actions and workflows.

Everything here observes or edits existing data: gesture→action/workflow
mappings ARE rows in the `rules` table resolved by `RuleEngine`. The
Command Center never adds a second mapping or execution system, never
touches the camera/gesture threads, and never changes arbitration — it
visualizes state, explains precedence (via rules/analyzer.py) and offers
one-screen assign/edit/enable/duplicate/delete/test.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox,
                               QFormLayout, QGroupBox, QHBoxLayout, QLabel,
                               QMessageBox, QPushButton, QRadioButton,
                               QTableWidget, QTableWidgetItem, QTextEdit,
                               QVBoxLayout, QWidget, QHeaderView, QListWidget,
                               QCheckBox)

from ..gestures.engine import all_gesture_names
from ..rules import analyzer
from ..runtime.controller import MotionController
from .actions_page import WorkflowBuilderDialog, step_label

_KIND_ICON = {"static": "✋", "swipe": "➡", "circle": "◯",
              "motion": "✍", "compound": "⛓", "custom": "★",
              "unknown": "•"}


def gesture_kind(ctl, name: str) -> str:
    from ..gestures import static as sg
    from ..gestures.motion import SWIPE_GESTURES
    from ..gestures.trajectory import TRAJECTORY_GESTURES
    if name in sg.STATIC_GESTURES:
        return "static"
    if name in SWIPE_GESTURES:
        return "swipe"
    if name in TRAJECTORY_GESTURES:
        return "circle"
    if any(m.name == name for m in ctl.motion_gestures.all()):
        return "motion"
    if any(c.name == name for c in ctl.custom_gestures.all()):
        return "custom"
    if any(c.name == name for c in ctl.compound_gestures.all()):
        return "compound"
    return "unknown"


def _profile_context(ctl, rule) -> tuple[str, str]:
    """(profile label, context detail) for a rule."""
    prof = ctl.profile_manager.profiles.get(rule.profile_id)
    if prof is None:
        return "?", ""
    if prof.kind == "global":
        base = "Global"
    else:
        apps = ", ".join(a.get("process_name", "?") for a in prof.apps)
        base = f"{prof.name} [{apps}]"
    bits = []
    if rule.window_pattern:
        bits.append(f"window {rule.window_pattern}")
    if rule.zone:
        bits.append(f"zone {rule.zone}")
    return base, "  ".join(bits)


class QuickAssignDialog(QDialog):
    """One screen: gesture → profile → action OR workflow → save. Edits
    the existing rule when `rule` is given."""

    def __init__(self, ctl: MotionController, rule=None, parent=None) -> None:
        super().__init__(parent)
        self.ctl = ctl
        self.rule = rule
        self.setWindowTitle("Edit mapping" if rule else "Assign gesture")
        self.resize(460, 300)
        pm = ctl.profile_manager

        self.gesture_combo = QComboBox()
        for g in all_gesture_names(ctl.custom_gestures.all(),
                                   ctl.motion_gestures.all()):
            self.gesture_combo.addItem(f"{_KIND_ICON[gesture_kind(ctl, g)]} "
                                       f"{g}", g)
        for cg in ctl.compound_gestures.all():
            self.gesture_combo.addItem(f"⛓ {cg.name}", cg.name)

        self.profile_combo = QComboBox()
        for p in pm.profiles.all():
            self.profile_combo.addItem(p.name, p.id)

        self.rb_action = QRadioButton("Action")
        self.rb_workflow = QRadioButton("Workflow")
        self.rb_action.setChecked(True)
        self.rb_action.toggled.connect(self._refresh_targets)

        self.target_combo = QComboBox()
        self.window_edit = QComboBox()
        self.window_edit.setEditable(True)
        self.window_edit.addItem("")
        self.window_edit.setEditText("")

        self.rb_action.toggled.connect(self._refresh_targets)
        self._refresh_targets()

        if rule is not None:
            gi = self.gesture_combo.findData(rule.gesture)
            self.gesture_combo.setCurrentIndex(max(0, gi))
            pi = self.profile_combo.findData(rule.profile_id)
            self.profile_combo.setCurrentIndex(max(0, pi))
            self.window_edit.setEditText(rule.window_pattern or "")
            a = pm.actions.get(rule.action_id)
            if a and a.type == "workflow":
                self.rb_workflow.setChecked(True)
                self._refresh_targets()
            ti = self.target_combo.findData(rule.action_id)
            if ti >= 0:
                self.target_combo.setCurrentIndex(ti)

        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._save)
        bb.rejected.connect(self.reject)

        typ = QHBoxLayout()
        typ.addWidget(self.rb_action)
        typ.addWidget(self.rb_workflow)
        typ.addStretch(1)
        form = QFormLayout()
        form.addRow("Gesture:", self.gesture_combo)
        form.addRow("Profile:", self.profile_combo)
        form.addRow("Window pattern:", self.window_edit)
        form.addRow("Type:", typ)
        form.addRow("Target:", self.target_combo)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(QLabel("Saved as a normal rule — precedence, "
                             "context and arbitration are unchanged."))
        lay.addWidget(bb)

    def _refresh_targets(self) -> None:
        pm = self.ctl.profile_manager
        want_wf = self.rb_workflow.isChecked()
        self.target_combo.clear()
        for a in pm.actions.all():
            is_wf = a.type == "workflow"
            if is_wf == want_wf:
                self.target_combo.addItem(f"{a.name}  ({a.type})", a.id)

    def _save(self) -> None:
        gesture = self.gesture_combo.currentData()
        pid = self.profile_combo.currentData()
        aid = self.target_combo.currentData()
        if aid is None:
            QMessageBox.warning(self, "Assign",
                                "Create an action or workflow first.")
            return
        win = self.window_edit.currentText().strip()
        rules = self.ctl.profile_manager.rules
        if self.rule is not None:
            rules.update(self.rule.id, gesture=gesture, profile_id=pid,
                         action_id=aid, window_pattern=win)
        else:
            rules.create(pid, gesture, aid, window_pattern=win)
        self.ctl.reload_rules()
        self.accept()


class TestGestureDialog(QDialog):
    """Safe gesture test: observe recognition, resolve the mapping, show
    what WOULD run. Never executes unless the user clicks 'Test full
    action' (with confirmation)."""

    def __init__(self, ctl: MotionController, bridge, parent=None) -> None:
        super().__init__(parent)
        self.ctl = ctl
        self.bridge = bridge
        self.setWindowTitle("Test Gesture")
        self.resize(420, 340)
        self._last_match = None
        self._last_ev = None

        self.info = QLabel("Perform a gesture in front of the camera…\n\n"
                           "(motion control does not need to be enabled — "
                           "this is a read-only test)")
        self.info.setWordWrap(True)
        self.info.setStyleSheet("font-size: 13px;")
        self.run_btn = QPushButton("Test full action (runs it!)")
        self.run_btn.setEnabled(False)
        self.run_btn.clicked.connect(self._run)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        row = QHBoxLayout()
        row.addWidget(self.run_btn)
        row.addStretch(1)
        row.addWidget(close_btn)
        lay = QVBoxLayout(self)
        lay.addWidget(self.info)
        lay.addWidget(row)

        bridge.gesture.connect(self._on_gesture)
        self.destroyed.connect(self._disconnect)

    def _disconnect(self) -> None:
        try:
            self.bridge.gesture.disconnect(self._on_gesture)
        except Exception:
            pass

    def _on_gesture(self, ev) -> None:
        if ev.phase != "start":
            return
        ctx = self.ctl.context.current
        match = self.ctl.rules.resolve(ev.gesture, ctx,
                                       self.ctl._screen_size())
        self._last_match, self._last_ev = match, ev
        lines = [f"Detected:  {ev.gesture}",
                 f"Confidence:  {ev.confidence:.2f}",
                 f"Active app:  {ctx.application}"]
        if match is None:
            lines.append("\nResolved mapping:  (none for this context)")
            self.run_btn.setEnabled(False)
        else:
            lines.append(f"\nResolved profile:  {match.profile_name}")
            lines.append(f"Resolved mapping:  {match.action.name or match.action.type}")
            if match.action.type == "workflow":
                lines.append("Workflow:  " + (match.action.name or ""))
            lines.append("\nResult:  READY")
            self.run_btn.setEnabled(True)
        self.info.setText("\n".join(lines))

    def _run(self) -> None:
        if self._last_match is None:
            return
        name = (self._last_match.action.name
                or self._last_match.action.type)
        if QMessageBox.question(
                self, "Run action",
                f"Run '{name}' for real now?") != QMessageBox.Yes:
            return
        self.ctl.executor.execute(self._last_match.action)


class CommandCenterPage(QWidget):
    """The Gesture Command Center page."""

    COLS = ["Gesture", "Type", "Profile / Context", "Assigned",
            "Enabled", "Conflict", "Last"]

    def __init__(self, ctl: MotionController, bridge) -> None:
        super().__init__()
        self.ctl = ctl
        self.bridge = bridge

        title = QLabel("Gesture Command Center")
        title.setStyleSheet("font-size: 18px; font-weight: 700;")
        # safety-state indicator (read-only mirror of the arming gate;
        # configuration lives in Gesture Studio, not here)
        self.safety_lbl = QLabel()
        self.safety_lbl.setStyleSheet("font-size: 13px; font-weight: 700;")
        assign_btn = QPushButton("＋ Assign gesture")
        assign_btn.clicked.connect(self._assign)
        test_btn = QPushButton("Test Gesture")
        test_btn.clicked.connect(self._test_gesture)
        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self.refresh)
        top = QHBoxLayout()
        top.addWidget(title)
        top.addWidget(self.safety_lbl)
        top.addStretch(1)
        top.addWidget(assign_btn)
        top.addWidget(test_btn)
        top.addWidget(refresh_btn)

        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.Stretch)
        self.table.itemSelectionChanged.connect(self._on_select)
        self.table.itemDoubleClicked.connect(lambda _: self._edit_mapping())

        edit_btn = QPushButton("Edit mapping")
        edit_btn.clicked.connect(self._edit_mapping)
        editwf_btn = QPushButton("Edit workflow")
        editwf_btn.clicked.connect(self._edit_workflow)
        dup_btn = QPushButton("Duplicate")
        dup_btn.clicked.connect(self._duplicate)
        toggle_btn = QPushButton("Enable / Disable")
        toggle_btn.clicked.connect(self._toggle)
        del_btn = QPushButton("Delete mapping")
        del_btn.clicked.connect(self._delete)
        rowbtns = QHBoxLayout()
        for b in (edit_btn, editwf_btn, dup_btn, toggle_btn, del_btn):
            rowbtns.addWidget(b)
        rowbtns.addStretch(1)

        # preview (execution chain) + live feedback
        self.preview = QTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setStyleSheet("font-family: Consolas, monospace;")
        prev_box = QGroupBox("Execution chain (preview only)")
        pv = QVBoxLayout(prev_box)
        pv.addWidget(self.preview)

        self.feedback = QLabel("—")
        self.feedback.setWordWrap(True)
        self.feedback.setStyleSheet("font-size: 13px; padding: 6px;")
        fb_box = QGroupBox("Live gesture feedback")
        fv = QVBoxLayout(fb_box)
        fv.addWidget(self.feedback)

        self.activity = QListWidget()
        act_box = QGroupBox("Recent gesture activity")
        av = QVBoxLayout(act_box)
        av.addWidget(self.activity)

        left = QVBoxLayout()
        left.addWidget(prev_box, 2)
        left.addWidget(fb_box, 1)
        bottom = QHBoxLayout()
        bottom.addLayout(left, 2)
        bottom.addWidget(act_box, 1)

        lay = QVBoxLayout(self)
        lay.addLayout(top)
        lay.addWidget(self.table, 3)
        lay.addLayout(rowbtns)
        lay.addLayout(bottom, 2)

        bridge.rule_matched.connect(self._on_rule_matched)
        bridge.workflow_progress.connect(self._on_wf_progress)
        bridge.workflow_done.connect(self._on_wf_done)
        bridge.activity_changed.connect(self._refresh_activity)
        bridge.arming_state.connect(self._on_arming_state)
        self._rules_cache = []
        self._on_arming_state(self.ctl.arming.state.value)
        self.refresh()

    def _on_arming_state(self, state: str) -> None:
        if not self.ctl.arming.enabled:
            self.safety_lbl.setText("")
            return
        text, colour = {
            "DISARMED": ("🔒 DISARMED", "#aa3333"),
            "ARMING": ("⏳ ARMING…", "#d9a44a"),
            "ARMED": ("🟢 ARMED", "#1a9e4b"),
            "DISARMING": ("⏳ DISARMING…", "#d9a44a"),
        }.get(state, (state, "#71847a"))
        self.safety_lbl.setText(text)
        self.safety_lbl.setStyleSheet(
            f"font-size: 13px; font-weight: 700; color: {colour};")

    # -- data ---------------------------------------------------------------
    def refresh(self) -> None:
        pm = self.ctl.profile_manager
        profiles = pm.profiles.all()
        rules = pm.rules.all()
        self._rules_cache = rules
        findings = analyzer.analyze(profiles, rules)
        self.table.setRowCount(0)
        for r in sorted(rules, key=lambda x: (x.gesture, x.profile_id)):
            a = pm.actions.get(r.action_id)
            kind = gesture_kind(self.ctl, r.gesture)
            prof, ctxdetail = _profile_context(self.ctl, r)
            assigned = "(missing action)"
            if a is not None:
                assigned = (f"Workflow: {a.name}" if a.type == "workflow"
                            else a.name)
            fnd = findings.get(r.id)
            conflict = ""
            if fnd and fnd.level == "conflict":
                conflict = "⚠ CONFLICT"
            elif fnd and fnd.level == "info":
                conflict = "ⓘ precedence"
            row = self.table.rowCount()
            self.table.insertRow(row)
            cells = [f"{_KIND_ICON[kind]} {r.gesture}", kind,
                     prof + (f"\n{ctxdetail}" if ctxdetail else ""),
                     assigned, "on" if r.enabled else "OFF",
                     conflict, self._last_for(r, a)]
            for c, text in enumerate(cells):
                it = QTableWidgetItem(text)
                if c == 0:
                    it.setData(Qt.UserRole, r.id)
                if not r.enabled:
                    it.setForeground(Qt.gray)
                if c == 5 and conflict.startswith("⚠"):
                    it.setForeground(Qt.red)
                it.setToolTip(fnd.message if fnd else "")
                self.table.setItem(row, c, it)
        self._refresh_activity()

    def _last_for(self, rule, action) -> str:
        target = None
        if action is not None:
            target = action.name or action.type
        import time as _t
        for e in self.ctl.activity.entries():
            if e.gesture == rule.gesture and (target is None
                                              or e.target == target):
                lt = _t.localtime(e.ts)
                return f"{lt.tm_hour:02d}:{lt.tm_min:02d}:{lt.tm_sec:02d}"
        return "—"

    def _selected_rule(self):
        row = self.table.currentRow()
        if row < 0:
            return None
        it = self.table.item(row, 0)
        rid = it.data(Qt.UserRole) if it else None
        return self.ctl.profile_manager.rules.get(rid) if rid else None

    # -- row actions --------------------------------------------------------
    def _assign(self) -> None:
        if QuickAssignDialog(self.ctl, parent=self).exec():
            self.refresh()

    def _edit_mapping(self) -> None:
        r = self._selected_rule()
        if r and QuickAssignDialog(self.ctl, r, parent=self).exec():
            self.refresh()

    def _edit_workflow(self) -> None:
        r = self._selected_rule()
        if r is None:
            return
        a = self.ctl.profile_manager.actions.get(r.action_id)
        if a is None or a.type != "workflow":
            QMessageBox.information(self, "Edit workflow",
                                    "This mapping runs a plain action, not "
                                    "a workflow.")
            return
        wid = int(a.params.get("workflow_id", 0) or 0)
        if WorkflowBuilderDialog(self.ctl, wid, parent=self).exec():
            self.refresh()

    def _duplicate(self) -> None:
        r = self._selected_rule()
        if r is None:
            return
        # duplicate the MAPPING only (a new independent rule to the same
        # target) — never copies the workflow/action/gesture
        self.ctl.profile_manager.rules.create(
            r.profile_id, r.gesture, r.action_id,
            window_pattern=r.window_pattern, zone=r.zone,
            continuous=r.continuous, cooldown_ms=r.cooldown_ms,
            enabled=r.enabled)
        self.ctl.reload_rules()
        self.refresh()

    def _toggle(self) -> None:
        r = self._selected_rule()
        if r is None:
            return
        self.ctl.profile_manager.rules.update(r.id, enabled=not r.enabled)
        self.ctl.reload_rules()
        self.refresh()

    def _delete(self) -> None:
        r = self._selected_rule()
        if r is None:
            return
        a = self.ctl.profile_manager.actions.get(r.action_id)
        target = (a.name if a else "?")
        if QMessageBox.question(
                self, "Remove mapping",
                f"Remove {r.gesture} → {target} mapping?\n\n"
                "Only the association is removed — the workflow, action, "
                "gesture and profile are kept.") != QMessageBox.Yes:
            return
        self.ctl.profile_manager.rules.delete(r.id)
        self.ctl.reload_rules()
        self.refresh()

    def _test_gesture(self) -> None:
        TestGestureDialog(self.ctl, self.bridge, self).exec()

    # -- selection / preview ------------------------------------------------
    def _on_select(self) -> None:
        r = self._selected_rule()
        if r is None:
            self.preview.setPlainText("")
            return
        pm = self.ctl.profile_manager
        a = pm.actions.get(r.action_id)
        prof, ctxdetail = _profile_context(self.ctl, r)
        chain = [f"{_KIND_ICON[gesture_kind(self.ctl, r.gesture)]} "
                 f"{r.gesture.upper()}", "     |", "  Recognized", "     |",
                 f"  {prof}" + (f"  ({ctxdetail})" if ctxdetail else ""),
                 "     |"]
        if a is None:
            chain.append("  (missing action)")
        elif a.type == "workflow":
            wid = int(a.params.get("workflow_id", 0) or 0)
            wf = pm.workflows.get(wid)
            chain.append(f"  Workflow: {a.name}")
            if wf and wf.requires_confirmation:
                chain += ["     |", "  ⚠ requires confirmation"]
            if wf:
                for s in wf.steps:
                    chain += ["     |", f"  {step_label(self.ctl, s)}"]
        else:
            chain.append(f"  Action: {a.name}  ({a.type})")
        chain += ["     |", "  ✓ COMPLETE"]
        fnd = analyzer.analyze(pm.profiles.all(),
                               pm.rules.all()).get(r.id)
        if fnd and fnd.level != "ok":
            chain += ["", f"[{fnd.level.upper()}] {fnd.message}"]
        self.preview.setPlainText("\n".join(chain))

    # -- live feedback / activity ------------------------------------------
    def _on_rule_matched(self, ev, ctx, match) -> None:
        self.feedback.setText(
            f"✓ {ev.gesture.upper()} DETECTED\n"
            f"{'Workflow' if match.action.type == 'workflow' else 'Action'}"
            f": {match.action.name or match.action.type}\n"
            f"Profile: {match.profile_name}")
        self.feedback.setStyleSheet(
            "font-size: 13px; padding: 6px; color: #1a9e4b; "
            "font-weight: 600;")

    def _on_wf_progress(self, name, i, total, label, state) -> None:
        self.feedback.setText(f"▶ {name} — {i}/{total}\n{label}")
        self.feedback.setStyleSheet(
            "font-size: 13px; padding: 6px; color: #d9a44a;")

    def _on_wf_done(self, name, status, detail) -> None:
        mark = {"completed": "✓", "failed": "✕", "cancelled": "■"}.get(
            status, "?")
        txt = f"{mark} {name} {status}"
        if detail and status != "completed":
            txt += f"\n{detail}"
        color = {"completed": "#1a9e4b", "failed": "#aa3333",
                 "cancelled": "#d9a44a"}.get(status, "#888")
        self.feedback.setText(txt)
        self.feedback.setStyleSheet(
            f"font-size: 13px; padding: 6px; color: {color}; "
            "font-weight: 600;")
        if self.isVisible():
            self.refresh()

    def _refresh_activity(self) -> None:
        import time as _t
        self.activity.clear()
        for e in self.ctl.activity.entries():
            lt = _t.localtime(e.ts)
            stamp = f"{lt.tm_hour:02d}:{lt.tm_min:02d}:{lt.tm_sec:02d}"
            mark = {"completed": "✓", "failed": "✕", "cancelled": "■",
                    "running": "▶"}.get(e.status, "·")
            line = (f"{stamp}  {e.gesture} → {e.target}  "
                    f"[{e.profile}]  {mark} {e.status}")
            if e.detail and e.status == "failed":
                line += f" ({e.detail})"
            self.activity.addItem(line)
