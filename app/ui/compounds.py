"""Compound gesture UI: list section, visual builder, recorder, safe test."""
from __future__ import annotations

import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog,
                               QDialogButtonBox, QFormLayout, QGroupBox,
                               QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMessageBox, QPushButton,
                               QSpinBox, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from ..gestures.engine import all_gesture_names
from ..runtime.controller import MotionController
from .bridge import QtBridge

STEP_TYPES = [("gesture", "Gesture"), ("hold", "Hold"),
              ("release", "Release")]
HANDS = [("any", "Any hand"), ("left", "Left hand"),
         ("right", "Right hand"), ("same", "Same hand throughout")]


def steps_summary(steps: list[dict]) -> str:
    parts = []
    for s in steps:
        t = s.get("type")
        if t == "gesture":
            parts.append(s.get("gesture", "?"))
        elif t == "hold":
            parts.append(f"hold {s.get('gesture', '?')} "
                         f"{s.get('hold_ms', 0)}ms")
        elif t == "release":
            parts.append(f"release {s.get('gesture') or '(previous)'}")
    return "  →  ".join(parts)


class CompoundBuilderDialog(QDialog):
    """Visual step-by-step builder — no JSON editing."""

    def __init__(self, ctl: MotionController, row=None,
                 proposed_steps: list[dict] | None = None,
                 parent=None) -> None:
        super().__init__(parent)
        self.ctl = ctl
        self.row = row
        self.setWindowTitle("Compound gesture builder")
        self.resize(640, 520)
        self._primitives = all_gesture_names(ctl.custom_gestures.all())

        self.name_edit = QLineEdit(row.name if row else "")
        self.name_edit.setPlaceholderText("e.g. quick_confirm")

        # steps table: type | gesture | hold ms
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Step type", "Gesture",
                                              "Hold duration"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(0, 130)
        self.table.setColumnWidth(1, 200)

        add_btn = QPushButton("+ Add step")
        add_btn.clicked.connect(lambda: self._add_step())
        del_btn = QPushButton("Remove step")
        del_btn.clicked.connect(self._remove_step)
        up_btn = QPushButton("↑")
        up_btn.clicked.connect(lambda: self._move(-1))
        down_btn = QPushButton("↓")
        down_btn.clicked.connect(lambda: self._move(1))
        srow = QHBoxLayout()
        for b in (add_btn, del_btn, up_btn, down_btn):
            srow.addWidget(b)
        srow.addStretch(1)

        # timing / hand
        self.max_dur = QSpinBox()
        self.max_dur.setRange(300, 10000)
        self.max_dur.setSuffix(" ms")
        self.max_dur.setValue(row.max_duration_ms if row else 2000)
        self.gap = QSpinBox()
        self.gap.setRange(100, 5000)
        self.gap.setSuffix(" ms")
        self.gap.setValue(row.step_timeout_ms if row else 700)
        self.min_gap = QSpinBox()
        self.min_gap.setRange(0, 2000)
        self.min_gap.setSuffix(" ms")
        self.min_gap.setValue(row.min_gap_ms if row else 100)
        self.cooldown = QSpinBox()
        self.cooldown.setRange(0, 10000)
        self.cooldown.setSuffix(" ms")
        self.cooldown.setValue(row.cooldown_ms if row else 800)
        self.hand_combo = QComboBox()
        for key, label in HANDS:
            self.hand_combo.addItem(label, key)
        if row:
            i = self.hand_combo.findData(row.hand)
            self.hand_combo.setCurrentIndex(max(0, i))
        self.strict_check = QCheckBox(
            "Strict: any unexpected gesture cancels the sequence")
        self.strict_check.setChecked(bool(row.strict) if row else False)

        tbox = QGroupBox("Timing and conditions")
        tform = QFormLayout(tbox)
        tform.addRow("Maximum sequence duration:", self.max_dur)
        tform.addRow("Maximum gap between steps:", self.gap)
        tform.addRow("Minimum gap between steps:", self.min_gap)
        tform.addRow("Cooldown after firing:", self.cooldown)
        tform.addRow("Hand:", self.hand_combo)
        tform.addRow("", self.strict_check)

        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._save)
        bb.rejected.connect(self.reject)

        lay = QVBoxLayout(self)
        form = QFormLayout()
        form.addRow("Name:", self.name_edit)
        lay.addLayout(form)
        lay.addWidget(QLabel("Steps (performed in order):"))
        lay.addLayout(srow)
        lay.addWidget(self.table)
        lay.addWidget(tbox)
        lay.addWidget(bb)

        for s in (row.steps if row else (proposed_steps or [])):
            self._add_step(s)
        if self.table.rowCount() == 0:
            self._add_step()

    # -- steps table --------------------------------------------------------
    def _add_step(self, step: dict | None = None) -> None:
        r = self.table.rowCount()
        self.table.insertRow(r)
        type_combo = QComboBox()
        for key, label in STEP_TYPES:
            type_combo.addItem(label, key)
        gesture_combo = QComboBox()
        gesture_combo.addItem("(previous step's gesture)", "")
        for g in self._primitives:
            gesture_combo.addItem(g, g)
        hold_spin = QSpinBox()
        hold_spin.setRange(100, 5000)
        hold_spin.setSuffix(" ms")
        hold_spin.setValue(500)
        if step:
            i = type_combo.findData(step.get("type", "gesture"))
            type_combo.setCurrentIndex(max(0, i))
            gi = gesture_combo.findData(step.get("gesture", ""))
            gesture_combo.setCurrentIndex(max(0, gi))
            hold_spin.setValue(int(step.get("hold_ms", 500)))
        else:
            gesture_combo.setCurrentIndex(1 if self._primitives else 0)

        def sync_enabled() -> None:
            t = type_combo.currentData()
            hold_spin.setEnabled(t == "hold")
            gesture_combo.setEnabled(True)

        type_combo.currentIndexChanged.connect(sync_enabled)
        sync_enabled()
        self.table.setCellWidget(r, 0, type_combo)
        self.table.setCellWidget(r, 1, gesture_combo)
        self.table.setCellWidget(r, 2, hold_spin)

    def _remove_step(self) -> None:
        r = self.table.currentRow()
        if r >= 0:
            self.table.removeRow(r)

    def _move(self, delta: int) -> None:
        r = self.table.currentRow()
        n = self.table.rowCount()
        if r < 0 or not (0 <= r + delta < n):
            return
        steps = self._collect_steps()
        steps[r], steps[r + delta] = steps[r + delta], steps[r]
        self.table.setRowCount(0)
        for s in steps:
            self._add_step(s)
        self.table.selectRow(r + delta)

    def _collect_steps(self) -> list[dict]:
        steps = []
        for r in range(self.table.rowCount()):
            t = self.table.cellWidget(r, 0).currentData()
            g = self.table.cellWidget(r, 1).currentData()
            s: dict = {"type": t, "gesture": g}
            if t == "hold":
                s["hold_ms"] = self.table.cellWidget(r, 2).value()
            steps.append(s)
        return steps

    # -- save ---------------------------------------------------------------
    def _save(self) -> None:
        name = self.name_edit.text().strip().lower().replace(" ", "_")
        if not name:
            QMessageBox.warning(self, "Invalid", "Name is required.")
            return
        steps = self._collect_steps()
        repo = self.ctl.compound_gestures
        err = repo.validate(steps, self.hand_combo.currentData())
        if err:
            QMessageBox.warning(self, "Invalid", err)
            return
        taken = set(self._primitives) | {
            c.name for c in repo.all() if not self.row or c.id != self.row.id}
        if name in taken:
            QMessageBox.warning(self, "Duplicate",
                                f"Name {name!r} is already used.")
            return
        timing = dict(max_duration_ms=self.max_dur.value(),
                      step_timeout_ms=self.gap.value(),
                      min_gap_ms=self.min_gap.value(),
                      cooldown_ms=self.cooldown.value(),
                      hand=self.hand_combo.currentData(),
                      strict=self.strict_check.isChecked())
        if self.row:
            repo.update(self.row.id, name=name, steps=steps, **timing)
        else:
            repo.create(name, steps, **timing)
        self.ctl.reload_rules()
        self.saved_name = name
        self.accept()


class CompoundRecorderDialog(QDialog):
    """Perform the sequence once; primitives are captured and proposed."""

    CAPTURE_SECONDS = 5.0

    def __init__(self, ctl: MotionController, bridge: QtBridge,
                 parent=None) -> None:
        super().__init__(parent)
        self.ctl = ctl
        self.bridge = bridge
        self.setWindowTitle("Record compound gesture")
        self.resize(440, 220)
        self.captured: list[str] = []
        self._capturing = False
        self._t_end = 0.0

        self.status = QLabel(
            "Press Start, then perform your sequence of gestures "
            f"within {self.CAPTURE_SECONDS:.0f} seconds.\n"
            "Detected primitives will be proposed as steps — nothing is "
            "saved without your confirmation.")
        self.status.setWordWrap(True)
        self.status.setAlignment(Qt.AlignCenter)
        self.seq_label = QLabel("—")
        self.seq_label.setAlignment(Qt.AlignCenter)
        self.seq_label.setStyleSheet("font-weight:700; font-size:14px;")
        self.start_btn = QPushButton("Start recording")
        self.start_btn.clicked.connect(self._start)
        self.use_btn = QPushButton("Use this sequence…")
        self.use_btn.setEnabled(False)
        self.use_btn.clicked.connect(self.accept)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        row = QHBoxLayout()
        row.addWidget(self.start_btn)
        row.addWidget(self.use_btn)
        row.addWidget(cancel)

        lay = QVBoxLayout(self)
        lay.addWidget(self.status)
        lay.addWidget(self.seq_label)
        lay.addLayout(row)

        bridge.gesture.connect(self._on_gesture)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)

    def _start(self) -> None:
        self.captured.clear()
        self.seq_label.setText("recording…")
        self._capturing = True
        self._t_end = time.monotonic() + self.CAPTURE_SECONDS
        self.start_btn.setEnabled(False)
        self._timer.start(200)

    def _tick(self) -> None:
        if time.monotonic() >= self._t_end:
            self._timer.stop()
            self._capturing = False
            self.start_btn.setEnabled(True)
            self.start_btn.setText("Record again")
            if self.captured:
                self.use_btn.setEnabled(True)
                self.status.setText("Sequence captured — use it, or record "
                                    "again.")
            else:
                self.status.setText("No gestures detected — try again with "
                                    "your hand visible to the camera.")

    def _on_gesture(self, ev) -> None:
        if not self._capturing or ev.phase != "start":
            return
        if ev.source == "compound":
            return
        if self.captured and self.captured[-1] == ev.gesture:
            return  # dedupe immediate repeats from jitter
        self.captured.append(ev.gesture)
        self.seq_label.setText("  →  ".join(self.captured))

    def proposed_steps(self) -> list[dict]:
        return [{"type": "gesture", "gesture": g} for g in self.captured]

    def done(self, r: int) -> None:
        self._timer.stop()
        try:
            self.bridge.gesture.disconnect(self._on_gesture)
        except (RuntimeError, TypeError):
            pass
        super().done(r)


class CompoundTestDialog(QDialog):
    """SAFE compound test: motion disabled while open; shows per-step
    progress live and flashes on completion."""

    def __init__(self, ctl: MotionController, bridge: QtBridge, row,
                 parent=None) -> None:
        super().__init__(parent)
        self.ctl = ctl
        self.row = row
        self.setWindowTitle(f"Test compound — {row.name}")
        self.resize(460, 300)
        self._was_enabled = ctl.motion_enabled
        ctl.set_motion_enabled(False)  # SAFE — nothing executes
        self._bridge = bridge
        self._detected_at = 0.0

        self.target = QLabel("Target:  " + steps_summary(row.steps))
        self.target.setWordWrap(True)
        self.target.setStyleSheet("font-weight:600;")
        self.steps_label = QLabel("")
        self.steps_label.setStyleSheet(
            "font-family: Consolas, monospace; font-size: 14px;")
        self.result = QLabel("Perform the sequence…")
        self.result.setAlignment(Qt.AlignCenter)
        self.result.setMinimumHeight(48)
        self.result.setStyleSheet("font-size:16px; font-weight:700;")
        self.hint = QLabel("Actions will NOT run in this mode. Motion "
                           "control is re-enabled when you close this "
                           "window (if it was on).")
        self.hint.setWordWrap(True)
        close = QDialogButtonBox(QDialogButtonBox.Close)
        close.rejected.connect(self.reject)
        close.clicked.connect(self.accept)

        lay = QVBoxLayout(self)
        lay.addWidget(self.target)
        lay.addWidget(self.steps_label)
        lay.addWidget(self.result)
        lay.addWidget(self.hint)
        lay.addWidget(close)

        bridge.gesture.connect(self._on_gesture)
        self._poll = QTimer(self)
        self._poll.timeout.connect(self._tick)
        self._poll.start(100)

    def _tick(self) -> None:
        done_n, total = self.ctl.compounds.progress(self.row.name)
        marks = []
        for i, s in enumerate(self.row.steps):
            label = steps_summary([s])
            if i < done_n:
                marks.append(f"✓ {label}")
            elif i == done_n:
                marks.append(f"… waiting for {label}")
            else:
                marks.append(f"  {label}")
        self.steps_label.setText("\n".join(marks))
        if self._detected_at and time.monotonic() - self._detected_at > 2.0:
            self.result.setText("Perform the sequence…")
            self.result.setStyleSheet("font-size:16px; font-weight:700;")
            self._detected_at = 0.0

    def _on_gesture(self, ev) -> None:
        if (ev.source == "compound" and ev.gesture == self.row.name
                and ev.phase == "start"):
            self.result.setText(f"✓ COMPOUND DETECTED — {self.row.name}  "
                                f"({ev.confidence * 100:.0f}%)")
            self.result.setStyleSheet(
                "font-size:16px; font-weight:800; color:#1a9e4b;")
            self._detected_at = time.monotonic()

    def done(self, r: int) -> None:
        self._poll.stop()
        try:
            self._bridge.gesture.disconnect(self._on_gesture)
        except (RuntimeError, TypeError):
            pass
        self.ctl.set_motion_enabled(self._was_enabled)
        super().done(r)


class CompoundSection(QWidget):
    """Embedded in the Gestures page."""

    def __init__(self, ctl: MotionController, bridge: QtBridge,
                 on_mapped=None) -> None:
        super().__init__()
        self.ctl = ctl
        self.bridge = bridge
        self.on_mapped = on_mapped
        self.repo = ctl.compound_gestures

        self.listw = QListWidget()
        self.listw.itemDoubleClicked.connect(lambda _: self._edit())

        buttons = [("+ Create compound gesture", self._create),
                   ("Record…", self._record), ("Edit", self._edit),
                   ("Test (safe)", self._test),
                   ("Enable/Disable", self._toggle),
                   ("Delete", self._delete)]
        brow = QHBoxLayout()
        for label, fn in buttons:
            b = QPushButton(label)
            b.clicked.connect(fn)
            brow.addWidget(b)
        brow.addStretch(1)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(QLabel("Compound gestures (sequences, double taps, "
                             "holds — map them like any gesture):"))
        lay.addLayout(brow)
        lay.addWidget(self.listw)
        self.refresh()

    def refresh(self) -> None:
        self.listw.clear()
        rules = self.ctl.profile_manager.rules.all()
        actions = {a.id: a.name
                   for a in self.ctl.profile_manager.actions.all()}
        for c in self.repo.all():
            assigned = sorted({actions.get(r.action_id, "?")
                               for r in rules if r.gesture == c.name})
            label = f"{c.name}:   {steps_summary(c.steps)}"
            label += (f"   [gap≤{c.step_timeout_ms}ms, "
                      f"total≤{c.max_duration_ms}ms, {c.hand}]")
            if assigned:
                label += "   →  " + ", ".join(assigned)
            if not c.enabled:
                label += "   (disabled)"
            item = QListWidgetItem(label)
            item.setData(32, c.id)
            self.listw.addItem(item)

    def _selected(self):
        it = self.listw.currentItem()
        return self.repo.get(it.data(32)) if it else None

    def _create(self, proposed: list[dict] | None = None) -> None:
        dlg = CompoundBuilderDialog(self.ctl, proposed_steps=proposed,
                                    parent=self)
        if dlg.exec():
            self.refresh()
            if self.on_mapped and QMessageBox.question(
                    self, "Saved",
                    f"Compound {dlg.saved_name!r} saved.\n\n"
                    "Assign an action to it in Gesture Studio now?",
                    QMessageBox.Yes | QMessageBox.No) == QMessageBox.Yes:
                self.on_mapped(dlg.saved_name)

    def _record(self) -> None:
        if not self.ctl.running:
            QMessageBox.warning(self, "Camera off",
                                "The camera must be running to record.")
            return
        rec = CompoundRecorderDialog(self.ctl, self.bridge, self)
        if rec.exec() and rec.captured:
            self._create(proposed=rec.proposed_steps())

    def _edit(self) -> None:
        row = self._selected()
        if not row:
            return
        dlg = CompoundBuilderDialog(self.ctl, row=row, parent=self)
        if dlg.exec():
            self.refresh()

    def _test(self) -> None:
        row = self._selected()
        if not row:
            return
        if not row.enabled:
            QMessageBox.information(self, "Disabled",
                                    "Enable this compound before testing.")
            return
        CompoundTestDialog(self.ctl, self.bridge, row, self).exec()

    def _toggle(self) -> None:
        row = self._selected()
        if row:
            self.repo.update(row.id, enabled=not row.enabled)
            self.ctl.reload_rules()
            self.refresh()

    def _delete(self) -> None:
        row = self._selected()
        if not row:
            return
        if QMessageBox.question(self, "Delete",
                                f"Delete compound {row.name!r}? Rules "
                                "mapped to it will stop matching."
                                ) == QMessageBox.Yes:
            self.repo.delete(row.id)
            self.ctl.reload_rules()
            self.refresh()
