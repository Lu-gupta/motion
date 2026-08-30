"""Gestures page — built-in gesture reference + custom gesture manager
with live recorder."""
from __future__ import annotations

import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QDoubleSpinBox,
                               QFormLayout, QHBoxLayout, QInputDialog, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem,
                               QMessageBox, QProgressBar, QPushButton,
                               QVBoxLayout, QWidget)

from ..core.types import Hand, HandFrame
from ..gestures.custom import build_template, build_template_multi
from ..gestures.motion import SWIPE_GESTURES
from ..gestures.static import STATIC_GESTURES
from ..gestures.trajectory import TRAJECTORY_GESTURES
from ..runtime.controller import MotionController
from .bridge import QtBridge

BUILTIN_DESCRIPTIONS = {
    "pinch": "Thumb and index fingertips touch",
    "open_palm": "All five fingers extended",
    "fist": "All fingers curled",
    "point": "Index finger extended only",
    "thumb_up": "Thumb up, fingers curled",
    "swipe_left": "Fast hand movement left",
    "swipe_right": "Fast hand movement right",
    "swipe_up": "Fast hand movement up",
    "swipe_down": "Fast hand movement down",
    "circle": "Draw a circular motion with your index fingertip "
              "(clockwise or counter-clockwise; imperfect circles OK)",
}

RECORD_SECONDS = 2.0
COUNTDOWN_SECONDS = 3


class RecorderDialog(QDialog):
    """Guided recording: countdown → progress-bar capture → preview →
    add more samples or finish. Produces a merged template."""

    def __init__(self, bridge: QtBridge, gesture_name: str,
                 parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Record gesture — {gesture_name}")
        self.resize(460, 300)
        self.bridge = bridge
        self.gesture_name = gesture_name
        self.template: dict | None = None
        self.samples: list[tuple[list[Hand], list[tuple[float, float]]]] = []
        self._hands: list[Hand] = []
        self._traj: list[tuple[float, float]] = []
        self._recording = False
        self._t_start = 0.0
        self._t_end = 0.0

        self.status = QLabel(
            "Show your hand to the camera, then press Record.\n"
            f"After a {COUNTDOWN_SECONDS}s countdown, hold or perform the "
            f"gesture for {RECORD_SECONDS:.0f}s.\n"
            "Recording 2–3 samples makes matching more robust.")
        self.status.setWordWrap(True)
        self.status.setAlignment(Qt.AlignCenter)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)

        self.preview = QLabel("No sample yet")
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setMinimumHeight(110)
        self.preview.setStyleSheet(
            "background:#101418; color:#8899aa; border-radius:6px;")

        self.record_btn = QPushButton("Record sample")
        self.record_btn.clicked.connect(self._start)
        self.done_btn = QPushButton("Finish (0 samples)")
        self.done_btn.setEnabled(False)
        self.done_btn.clicked.connect(self._finish)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)

        btns = QHBoxLayout()
        btns.addWidget(self.record_btn)
        btns.addWidget(self.done_btn)
        btns.addWidget(cancel_btn)

        lay = QVBoxLayout(self)
        lay.addWidget(self.status)
        lay.addWidget(self.progress)
        lay.addWidget(self.preview)
        lay.addLayout(btns)

        bridge.hands.connect(self._on_hands)
        self.record_seconds = RECORD_SECONDS
        self._countdown = COUNTDOWN_SECONDS
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._prog_timer = QTimer(self)
        self._prog_timer.timeout.connect(self._update_progress)

    # -- capture ------------------------------------------------------------
    def _start(self) -> None:
        self.record_btn.setEnabled(False)
        self.done_btn.setEnabled(False)
        self._countdown = COUNTDOWN_SECONDS
        self.status.setText(f"Get ready… {self._countdown}")
        self._timer.start(1000)

    def _tick(self) -> None:
        self._countdown -= 1
        if self._countdown > 0:
            self.status.setText(f"Get ready… {self._countdown}")
            return
        self._timer.stop()
        self._hands = []
        self._traj = []
        self._recording = True
        self._t_start = time.monotonic()
        self._t_end = self._t_start + self.record_seconds
        self.status.setText("RECORDING — perform the gesture now")
        self._prog_timer.start(80)

    def _update_progress(self) -> None:
        if not self._recording:
            return
        frac = (time.monotonic() - self._t_start) / self.record_seconds
        self.progress.setValue(min(100, int(frac * 100)))

    def _on_hands(self, hf: HandFrame) -> None:
        if not self._recording:
            return
        if hf.hands:
            hand = hf.hands[0]
            self._hands.append(hand)
            w = hand.landmarks[Hand.WRIST]
            self._traj.append((w.x, w.y))
        if time.monotonic() >= self._t_end:
            self._recording = False
            self._prog_timer.stop()
            self.progress.setValue(0)
            self.record_btn.setEnabled(True)
            if len(self._hands) < 10:
                self.status.setText(
                    "Not enough hand data captured — keep your hand visible "
                    "to the camera and record again.")
                self.done_btn.setEnabled(bool(self.samples))
                return
            self.samples.append((self._hands, self._traj))
            self._show_preview(self._traj)
            n = len(self.samples)
            self.status.setText(
                f"Sample {n} captured. Record another for robustness, "
                "or finish.")
            self.record_btn.setText("Record another sample")
            self.done_btn.setText(f"Finish ({n} sample{'s' if n > 1 else ''})")
            self.done_btn.setEnabled(True)

    # -- preview ------------------------------------------------------------
    def _show_preview(self, traj: list[tuple[float, float]]) -> None:
        from PySide6.QtGui import QPainter, QPen, QColor, QPixmap
        w, h = 220, 100
        pix = QPixmap(w, h)
        pix.fill(QColor("#101418"))
        p = QPainter(pix)
        p.setRenderHint(QPainter.Antialiasing)
        import numpy as np
        pts = np.array(traj)
        movement = float(np.linalg.norm(np.diff(pts, axis=0), axis=1).sum()
                         ) if len(pts) > 1 else 0.0
        if movement < 0.12:
            p.setPen(QPen(QColor("#8899aa")))
            p.drawText(pix.rect(), Qt.AlignCenter,
                       "Static pose (no movement path)")
        else:
            span = pts.max(axis=0) - pts.min(axis=0)
            scale = max(span.max(), 1e-6)
            norm = (pts - pts.min(axis=0)) / scale
            margin = 12
            xs = margin + (1.0 - norm[:, 0]) * (w - 2 * margin)  # mirrored
            ys = margin + norm[:, 1] * (h - 2 * margin)
            p.setPen(QPen(QColor("#1a9e4b"), 2))
            for i in range(1, len(xs)):
                p.drawLine(int(xs[i - 1]), int(ys[i - 1]),
                           int(xs[i]), int(ys[i]))
            p.setPen(QPen(QColor("#35d698"), 6))
            p.drawPoint(int(xs[-1]), int(ys[-1]))  # end point emphasized
        p.end()
        self.preview.setPixmap(pix)

    def _finish(self) -> None:
        if not self.samples:
            return
        self.template = build_template_multi(self.gesture_name, self.samples)
        self.accept()

    def done(self, r: int) -> None:  # disconnect on close
        self._timer.stop()
        self._prog_timer.stop()
        try:
            self.bridge.hands.disconnect(self._on_hands)
        except (RuntimeError, TypeError):
            pass
        super().done(r)


class MotionRecorderDialog(RecorderDialog):
    """Records fingertip trajectories for drawn motion gestures. Same
    guided flow as the pose recorder; captures the index fingertip and
    builds a normalized shape template."""

    def __init__(self, bridge: QtBridge, gesture_name: str,
                 parent=None) -> None:
        super().__init__(bridge, gesture_name, parent)
        self.setWindowTitle(f"Record motion gesture — {gesture_name}")
        self.record_seconds = 2.5
        self.status.setText(
            "Show your hand, press Record, and after the countdown draw "
            "the shape in the air with your index fingertip "
            f"({self.record_seconds:.1f}s per sample).\n"
            "Record 3 or more samples for the most reliable matching.")

    def _on_hands(self, hf: HandFrame) -> None:
        if not self._recording:
            return
        if hf.hands:
            hand = hf.hands[0]
            self._hands.append(hand)
            tip = hand.landmarks[Hand.INDEX_TIP]
            self._traj.append((tip.x, tip.y))
        if time.monotonic() >= self._t_end:
            self._recording = False
            self._prog_timer.stop()
            self.progress.setValue(0)
            self.record_btn.setEnabled(True)
            from ..gestures.trajectory import evaluate_motion_sample
            verdict = evaluate_motion_sample(self._traj)
            if not verdict["ok"]:
                self.status.setText(
                    "REJECTED — " + "; ".join(verdict["reasons"])
                    + ".\nKeep your hand visible and draw the shape larger, "
                    "then record again.")
                self.done_btn.setEnabled(bool(self.samples))
                return
            self.samples.append((self._hands, self._traj))
            self._show_preview(self._traj)
            n = len(self.samples)
            self.status.setText(
                f"GOOD SAMPLE ✓  ({verdict['points']} points, movement "
                f"{verdict['movement']}).  Sample {n} captured — record "
                "another for robustness (3+ recommended), or finish.")
            self.record_btn.setText("Record another sample")
            self.done_btn.setText(f"Finish ({n} sample{'s' if n > 1 else ''})")
            self.done_btn.setEnabled(True)

    def _finish(self) -> None:
        if not self.samples:
            return
        from ..gestures.trajectory import build_motion_template
        try:
            self.template = build_motion_template(
                [traj for _, traj in self.samples])
        except ValueError as e:
            QMessageBox.warning(self, "Recording", str(e))
            return
        self.accept()


def _draw_trajectory(points, w=200, h=90, ok=True):
    """Render a normalized trajectory into a pixmap (mirrored like the
    preview, direction shown by an end dot). Uses only stored normalized
    points — never a camera frame."""
    from PySide6.QtGui import QColor, QPainter, QPen, QPixmap
    import numpy as np
    pix = QPixmap(w, h)
    pix.fill(QColor("#101418"))
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    pts = np.array(points, dtype=np.float64) if points else np.zeros((0, 2))
    if len(pts) >= 2:
        span = pts.max(axis=0) - pts.min(axis=0)
        scale = max(float(span.max()), 1e-6)
        norm = (pts - pts.min(axis=0)) / scale
        margin = 10
        xs = margin + (1.0 - norm[:, 0]) * (w - 2 * margin)  # mirrored
        ys = margin + norm[:, 1] * (h - 2 * margin)
        colour = "#1a9e4b" if ok else "#aa7733"
        p.setPen(QPen(QColor(colour), 2))
        for i in range(1, len(xs)):
            p.drawLine(int(xs[i - 1]), int(ys[i - 1]),
                       int(xs[i]), int(ys[i]))
        p.setPen(QPen(QColor("#35d698"), 6))
        p.drawPoint(int(xs[-1]), int(ys[-1]))
    else:
        p.setPen(QPen(QColor("#8899aa")))
        p.drawText(pix.rect(), Qt.AlignCenter, "no path")
    p.end()
    return pix


def _describe_direction(points) -> str:
    """Net displacement direction of a normalized sample (mirrored to the
    user's perspective, matching the preview)."""
    import numpy as np
    pts = np.array(points, dtype=np.float64) if points else np.zeros((0, 2))
    if len(pts) < 2:
        return "—"
    dx = float(pts[-1][0] - pts[0][0]) * -1  # mirror x
    dy = float(pts[-1][1] - pts[0][1])
    horiz = "right" if dx > 0 else "left"
    vert = "down" if dy > 0 else "up"
    return f"{vert}-{horiz}" if abs(dx) > 1e-6 or abs(dy) > 1e-6 else "closed"


def _confirm_delete_motion(parent, ctl, m) -> bool:
    """Dependency-aware delete confirmation. Shows the rules/compounds that
    reference the gesture; never deletes the referenced actions/workflows."""
    deps = ctl.motion_gesture_dependents(m.name)
    lines = [f"Delete motion gesture {m.name!r}?", ""]
    if deps["rules"] or deps["compounds"]:
        lines.append("Used by:")
        for r in deps["rules"]:
            lines.append(f"  {r['profile']} → {r['action']!r}")
        for c in deps["compounds"]:
            lines.append(f"  compound → {c!r}")
        lines += ["", "Those mappings will stop matching (no action or "
                  "workflow is deleted)."]
    else:
        lines.append("Nothing maps to it.")
    return QMessageBox.question(
        parent, "Delete motion gesture", "\n".join(lines),
        QMessageBox.Cancel | QMessageBox.Yes,
        QMessageBox.Cancel) == QMessageBox.Yes


class MotionGestureManagerDialog(QDialog):
    """Review, manage and rebuild a recorded motion gesture's samples.

    Samples are the normalized trajectories the recognizer already uses
    (never raw camera frames). Editing the sample set rebuilds the merged
    template through the existing build path — no second algorithm — and
    only on add / replace / delete, never per-frame."""

    def __init__(self, ctl: MotionController, bridge: QtBridge, gid: int,
                 parent=None) -> None:
        super().__init__(parent)
        self.ctl = ctl
        self.bridge = bridge
        self.gid = gid
        self.setWindowTitle("Manage motion gesture")
        self.resize(560, 560)

        self.title = QLabel()
        self.title.setStyleSheet("font-size:15px; font-weight:700;")
        self.diag = QLabel()
        self.diag.setWordWrap(True)

        self.samples_list = QListWidget()
        self.samples_list.setMaximumHeight(120)
        self.samples_list.currentRowChanged.connect(self._show_sample)

        self.sample_preview = QLabel()
        self.sample_preview.setAlignment(Qt.AlignCenter)
        self.sample_preview.setMinimumHeight(96)
        self.sample_info = QLabel("—")
        self.template_preview = QLabel()
        self.template_preview.setAlignment(Qt.AlignCenter)
        self.template_preview.setMinimumHeight(96)

        add_btn = QPushButton("+ Record another sample")
        add_btn.clicked.connect(self._add_sample)
        repl_btn = QPushButton("Replace sample")
        repl_btn.clicked.connect(self._replace_sample)
        del_btn = QPushButton("Delete sample")
        del_btn.clicked.connect(self._delete_sample)
        rename_btn = QPushButton("Rename gesture")
        rename_btn.clicked.connect(self._rename)
        toggle_btn = QPushButton("Enable/Disable")
        toggle_btn.clicked.connect(self._toggle)
        sbtns = QHBoxLayout()
        for b in (add_btn, repl_btn, del_btn):
            sbtns.addWidget(b)
        sbtns.addStretch(1)
        gbtns = QHBoxLayout()
        for b in (rename_btn, toggle_btn):
            gbtns.addWidget(b)
        gbtns.addStretch(1)

        close = QDialogButtonBox(QDialogButtonBox.Close)
        close.rejected.connect(self.reject)
        close.clicked.connect(self.accept)

        lay = QVBoxLayout(self)
        lay.addWidget(self.title)
        lay.addWidget(self.diag)
        lay.addLayout(gbtns)
        lay.addWidget(QLabel("Samples (select to preview):"))
        lay.addWidget(self.samples_list)
        lay.addLayout(sbtns)
        row = QHBoxLayout()
        scol = QVBoxLayout()
        scol.addWidget(QLabel("Selected sample:"))
        scol.addWidget(self.sample_preview)
        scol.addWidget(self.sample_info)
        tcol = QVBoxLayout()
        tcol.addWidget(QLabel("Merged template (what the recognizer uses):"))
        tcol.addWidget(self.template_preview)
        row.addLayout(scol)
        row.addLayout(tcol)
        lay.addLayout(row)
        lay.addWidget(close)
        self._refresh()

    # -- state --------------------------------------------------------------
    def _row(self):
        return self.ctl.motion_gestures.get(self.gid)

    def _samples(self):
        from ..gestures.trajectory import motion_samples
        m = self._row()
        return motion_samples(m.template) if m else []

    def _refresh(self) -> None:
        from ..gestures.trajectory import template_diagnostics
        m = self._row()
        if m is None:
            self.reject()
            return
        d = template_diagnostics(m.template)
        self.title.setText(
            f"{m.name}   ({'Enabled' if m.enabled else 'Disabled'})")
        self.diag.setText(
            f"Samples: {d['samples']}   ·   resampled points: {d['points']}   "
            f"·   spread mean {d['spread_mean']} / max {d['spread_max']}   "
            f"·   {d['consistency']}   ·   revision {d['revision']}")
        cur = self.samples_list.currentRow()
        self.samples_list.clear()
        samples = self._samples()
        for i in range(len(samples)):
            self.samples_list.addItem(f"Sample {i + 1}  ✓")
        if samples:
            self.samples_list.setCurrentRow(min(max(cur, 0),
                                                len(samples) - 1))
        self.template_preview.setPixmap(
            _draw_trajectory(m.template.get("points", [])))
        self._show_sample(self.samples_list.currentRow())

    def _show_sample(self, idx: int) -> None:
        samples = self._samples()
        if not (0 <= idx < len(samples)):
            self.sample_preview.setText("no sample")
            self.sample_info.setText("—")
            return
        s = samples[idx]
        self.sample_preview.setPixmap(_draw_trajectory(s))
        self.sample_info.setText(
            f"{len(s)} points   ·   direction {_describe_direction(s)}")

    # -- sample edits (rebuild only here) -----------------------------------
    def _capture_one(self):
        """Run the guided recorder for a single new sample; return the raw
        trajectory or None if cancelled/rejected."""
        if not self.ctl.running:
            QMessageBox.warning(self, "Camera off",
                                "Start the camera first (check the "
                                "Dashboard).")
            return None
        m = self._row()
        dlg = MotionRecorderDialog(self.bridge, m.name, self)
        if not dlg.exec() or not dlg.samples:
            return None
        return dlg.samples[-1][1]        # last captured trajectory (x,y)

    def _rebuild(self, samples) -> None:
        from ..gestures.trajectory import rebuild_template
        m = self._row()
        rev = int(m.template.get("revision", 1)) + 1
        tmpl = rebuild_template(samples, revision=rev)
        self.ctl.motion_gestures.update(self.gid, template=tmpl)
        self.ctl.reload_rules()
        self._refresh()

    def _add_sample(self) -> None:
        traj = self._capture_one()
        if traj is None:
            return
        self._rebuild(self._samples() + [traj])

    def _replace_sample(self) -> None:
        idx = self.samples_list.currentRow()
        samples = self._samples()
        if not (0 <= idx < len(samples)):
            QMessageBox.information(self, "Replace", "Select a sample first.")
            return
        traj = self._capture_one()   # original kept until this succeeds
        if traj is None:
            return
        new = list(samples)
        new[idx] = traj
        self._rebuild(new)

    def _delete_sample(self) -> None:
        idx = self.samples_list.currentRow()
        samples = self._samples()
        if not (0 <= idx < len(samples)):
            return
        if len(samples) <= 1:
            QMessageBox.warning(
                self, "Cannot delete",
                "This is the only sample — a gesture needs at least one. "
                "Record another sample first, or delete the whole gesture.")
            return
        if QMessageBox.question(
                self, "Delete sample", f"Delete sample {idx + 1}?",
                QMessageBox.Cancel | QMessageBox.Yes,
                QMessageBox.Cancel) != QMessageBox.Yes:
            return
        self._rebuild([s for i, s in enumerate(samples) if i != idx])

    # -- gesture-level ops --------------------------------------------------
    def _rename(self) -> None:
        m = self._row()
        name, ok = QInputDialog.getText(self, "Rename gesture",
                                        "New name:", text=m.name)
        if not ok:
            return
        good, msg = self.ctl.rename_motion_gesture(self.gid, name)
        if not good:
            QMessageBox.warning(self, "Rename", msg)
            return
        self._refresh()

    def _toggle(self) -> None:
        m = self._row()
        self.ctl.motion_gestures.update(self.gid, enabled=not m.enabled)
        self.ctl.reload_rules()
        self._refresh()


class GestureTuneDialog(QDialog):
    """Per-gesture sensitivity. Statics/custom: confidence + cooldown.
    Swipes: one shared group of motion thresholds (advanced)."""

    def __init__(self, ctl: MotionController, gesture: str, parent=None
                 ) -> None:
        super().__init__(parent)
        self.ctl = ctl
        self.gesture = gesture
        self.is_swipe = gesture.startswith("swipe")
        self.is_circle = gesture == "circle"
        key = "swipe" if self.is_swipe else gesture
        self.key = key
        saved = ctl.gesture_settings.get(key)
        cfg = ctl.cfg
        self.setWindowTitle(f"Sensitivity — {'all swipes' if self.is_swipe else gesture}")

        form = QFormLayout(self)
        self.widgets: dict[str, QDoubleSpinBox] = {}

        def spin(name, lo, hi, step, default, suffix="", decimals=2):
            s = QDoubleSpinBox()
            s.setRange(lo, hi)
            s.setSingleStep(step)
            s.setDecimals(decimals)
            if suffix:
                s.setSuffix(suffix)
            s.setValue(saved.get(name, default))
            self.widgets[name] = s
            return s

        if self.is_circle:
            form.addRow(QLabel("Circles may be imperfect — clockwise or "
                               "counter-clockwise."))
            form.addRow("Recognition sensitivity (higher = stricter):",
                        spin("confidence", 0.3, 0.9, 0.05, 0.6))
            form.addRow("Minimum size (fraction of view):",
                        spin("min_size", 0.06, 0.5, 0.01, 0.12))
            form.addRow("Maximum duration:",
                        spin("max_duration_ms", 500, 5000, 100, 2000,
                             " ms", 0))
            form.addRow("Cooldown between circles:",
                        spin("cooldown_ms", 200, 5000, 100, 1000, " ms", 0))
        elif self.is_swipe:
            form.addRow(QLabel("Applies to all four swipe directions."))
            form.addRow("Minimum distance (fraction of view):",
                        spin("min_distance", 0.05, 0.6, 0.01, 0.15))
            form.addRow("Minimum speed (view-widths/second):",
                        spin("min_speed", 0.2, 3.0, 0.05, 0.6))
            form.addRow("Minimum duration:",
                        spin("min_duration_ms", 20, 500, 10, 60, " ms", 0))
            form.addRow("Maximum duration:",
                        spin("max_duration_ms", 200, 2000, 50, 600, " ms", 0))
            form.addRow("Cooldown between swipes:",
                        spin("cooldown_ms", 100, 3000, 50, 600, " ms", 0))
        else:
            form.addRow(QLabel("Higher confidence = stricter detection."))
            form.addRow("Confidence threshold:",
                        spin("confidence", 0.3, 1.0, 0.05,
                             cfg.gesture_confidence_threshold))
            form.addRow("Cooldown after release:",
                        spin("cooldown_ms", 0, 5000, 50,
                             cfg.default_cooldown_ms, " ms", 0))

        reset_btn = QPushButton("Reset to defaults")
        reset_btn.clicked.connect(self._reset)
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._save)
        bb.rejected.connect(self.reject)
        row = QHBoxLayout()
        row.addWidget(reset_btn)
        row.addWidget(bb)
        form.addRow(row)

    def _save(self) -> None:
        params = {k: (int(w.value()) if k.endswith("_ms") else w.value())
                  for k, w in self.widgets.items()}
        self.ctl.gesture_settings.set(self.key, params)
        self.ctl.reload_rules()  # hot apply
        self.accept()

    def _reset(self) -> None:
        self.ctl.gesture_settings.set(self.key, {})
        self.ctl.reload_rules()
        self.accept()


class GesturesPage(QWidget):
    def __init__(self, ctl: MotionController, bridge: QtBridge,
                 on_mapped=None) -> None:
        super().__init__()
        self.ctl = ctl
        self.bridge = bridge
        self.repo = ctl.custom_gestures
        self.on_mapped = on_mapped  # callback(gesture_name) → open Studio

        self.builtin_list = QListWidget()
        for g in STATIC_GESTURES + SWIPE_GESTURES + TRAJECTORY_GESTURES:
            self.builtin_list.addItem(
                f"{g}  —  {BUILTIN_DESCRIPTIONS.get(g, '')}")
        self.builtin_list.setMaximumHeight(200)
        self.builtin_list.itemDoubleClicked.connect(
            lambda _: self._tune_builtin())

        self.custom_list = QListWidget()

        rec_btn = QPushButton("Record new custom gesture…")
        rec_btn.clicked.connect(self._record)
        tol_btn = QPushButton("Adjust sensitivity…")
        tol_btn.clicked.connect(self._adjust)
        toggle_btn = QPushButton("Enable/Disable")
        toggle_btn.clicked.connect(self._toggle)
        del_btn = QPushButton("Delete")
        del_btn.clicked.connect(self._delete)
        btns = QHBoxLayout()
        for b in (rec_btn, tol_btn, toggle_btn, del_btn):
            btns.addWidget(b)
        btns.addStretch(1)

        tune_btn = QPushButton("Sensitivity…")
        tune_btn.clicked.connect(self._tune_builtin)
        builtin_row = QHBoxLayout()
        builtin_row.addWidget(QLabel("Built-in gestures "
                                     "(double-click to tune):"))
        builtin_row.addStretch(1)
        builtin_row.addWidget(tune_btn)

        # motion gestures (drawn fingertip shapes)
        self.motion_list = QListWidget()
        self.motion_list.setMaximumHeight(110)
        m_new = QPushButton("Create motion gesture…")
        m_new.clicked.connect(self._record_motion)
        m_manage = QPushButton("Manage samples…")
        m_manage.clicked.connect(self._manage_motion)
        m_test = QPushButton("Test recognition (safe)")
        m_test.clicked.connect(self._test_motion)
        m_sens = QPushButton("Sensitivity…")
        m_sens.clicked.connect(self._adjust_motion)
        m_toggle = QPushButton("Enable/Disable")
        m_toggle.clicked.connect(self._toggle_motion)
        m_del = QPushButton("Delete")
        m_del.clicked.connect(self._delete_motion)
        mbtns = QHBoxLayout()
        for b in (m_new, m_manage, m_test, m_sens, m_toggle, m_del):
            mbtns.addWidget(b)
        mbtns.addStretch(1)
        self.motion_list.itemDoubleClicked.connect(
            lambda _: self._manage_motion())

        from .compounds import CompoundSection
        self.compound_section = CompoundSection(ctl, bridge,
                                                on_mapped=on_mapped)

        safety = self._build_safety_bar()
        arming = self._build_arming_bar()

        lay = QVBoxLayout(self)
        lay.addWidget(safety)
        lay.addWidget(arming)
        lay.addLayout(builtin_row)
        lay.addWidget(self.builtin_list)
        lay.addWidget(QLabel("Custom gestures:"))
        lay.addLayout(btns)
        lay.addWidget(self.custom_list)
        lay.addWidget(QLabel("Motion gestures — shapes drawn with the "
                             "index fingertip:"))
        lay.addLayout(mbtns)
        lay.addWidget(self.motion_list)
        lay.addWidget(self.compound_section, 1)
        self.refresh()

    # -- Studio 2.0 safety & calibration bar --------------------------------
    def _build_safety_bar(self):
        from PySide6.QtWidgets import (QCheckBox, QComboBox, QGroupBox,
                                       QGridLayout)
        box = QGroupBox("Studio safety & calibration")
        grid = QGridLayout(box)

        self.lock_btn = QPushButton()
        self.lock_btn.setCheckable(True)
        self.lock_btn.setChecked(self.ctl.gestures_locked)
        self.lock_btn.clicked.connect(self._toggle_lock)
        self._sync_lock_btn(self.ctl.gestures_locked)
        if self.bridge is not None:
            self.bridge.control_locked.connect(self._sync_lock_btn)

        self.neutral_check = QCheckBox("Require neutral state before "
                                       "re-trigger")
        self.neutral_check.setChecked(
            self.ctl.cfg.require_neutral_before_retrigger)
        self.neutral_check.toggled.connect(self.ctl.set_require_neutral)

        self.context_lbl = QLabel("Context: —")
        # Event-driven (no polling): this label depends only on the active
        # context and the last resolved profile, both already broadcast on
        # the bus. Refresh on those signals + when the page is shown — no
        # periodic timer, no recognition-timing impact.
        if self.bridge is not None:
            self.bridge.context_changed.connect(
                lambda *_: self._refresh_context())
            self.bridge.rule_matched.connect(
                lambda *_: self._refresh_context())
        self._refresh_context(force=True)

        self.preset_combo = QComboBox()
        from ..gestures.presets import PRESET_NAMES
        for p in PRESET_NAMES:
            self.preset_combo.addItem(p, p)
        preset_btn = QPushButton("Apply preset to selected")
        preset_btn.clicked.connect(self._apply_preset)
        reset_btn = QPushButton("Reset selected gesture")
        reset_btn.clicked.connect(self._reset_selected)
        reset_all_btn = QPushButton("Reset ALL tuning")
        reset_all_btn.clicked.connect(self._reset_all)

        # control-hand routing (which physical hand may drive gestures)
        from ..core.hand_select import normalize_hand_control
        self.hand_combo = QComboBox()
        for label, val in (("Both hands", "both"), ("Left hand", "left"),
                           ("Right hand", "right")):
            self.hand_combo.addItem(label, val)
        self._select_combo(self.hand_combo,
                           normalize_hand_control(self.ctl.cfg.hand_control))
        self.hand_combo.currentIndexChanged.connect(self._apply_hand_control)
        self.hand_detected = QLabel("detected: —")
        if self.bridge is not None:
            self.bridge.hands.connect(self._on_hands_detected)

        grid.addWidget(self.lock_btn, 0, 0)
        grid.addWidget(self.neutral_check, 0, 1)
        grid.addWidget(self.context_lbl, 0, 2, 1, 2)
        grid.addWidget(QLabel("Preset:"), 1, 0)
        grid.addWidget(self.preset_combo, 1, 1)
        grid.addWidget(preset_btn, 1, 2)
        grid.addWidget(reset_btn, 1, 3)
        grid.addWidget(reset_all_btn, 1, 4)
        grid.addWidget(QLabel("Control hand:"), 2, 0)
        grid.addWidget(self.hand_combo, 2, 1)
        grid.addWidget(self.hand_detected, 2, 2, 1, 3)
        return box

    def _apply_hand_control(self, *_):
        self.ctl.set_hand_control(self.hand_combo.currentData())

    def _on_hands_detected(self, hf) -> None:
        if not self.isVisible():
            return
        from ..core.hand_select import user_perspective
        up = user_perspective(hf.hands[0].handedness) if hf.hands else "—"
        self.hand_detected.setText(f"detected: {up}")

    def _sync_lock_btn(self, locked: bool) -> None:
        self.lock_btn.setChecked(locked)
        self.lock_btn.setText("🔒 GESTURES LOCKED" if locked
                              else "● GESTURES ARMED")
        self.lock_btn.setStyleSheet(
            "font-weight:700; color:" + ("#aa3333" if locked
                                         else "#1a9e4b") + ";")

    def _toggle_lock(self) -> None:
        self.ctl.set_gestures_locked(self.lock_btn.isChecked())

    def _refresh_context(self, force: bool = False) -> None:
        if not force and not self.isVisible():
            return
        ctx = self.ctl.context.current
        prof = self.ctl.last_profile or "—"
        self.context_lbl.setText(
            f"Context: {ctx.application or '—'}  ·  "
            f"{ctx.window_title or '—'}  ·  profile {prof}")

    def showEvent(self, ev) -> None:      # keep label fresh when shown
        super().showEvent(ev)
        self._refresh_context(force=True)

    # -- Arming & safety section --------------------------------------------
    def _build_arming_bar(self):
        from PySide6.QtWidgets import (QCheckBox, QComboBox, QGroupBox,
                                       QGridLayout, QSpinBox)
        from ..gestures.engine import all_gesture_names
        cfg = self.ctl.cfg
        box = QGroupBox("Arming & safety")
        grid = QGridLayout(box)
        self._arming_loading = True

        self.arm_state_lbl = QLabel()
        self.arm_state_lbl.setStyleSheet("font-size:14px; font-weight:700;")

        self.arm_enable = QCheckBox("Arming ON")
        self.arm_enable.setChecked(cfg.arming_enabled)
        self.arm_enable.toggled.connect(self._apply_arming)

        gestures = list(all_gesture_names(self.ctl.custom_gestures.all(),
                                          self.ctl.motion_gestures.all()))
        gestures += [c.name for c in self.ctl.compound_gestures.all()]

        self.arm_gesture = QComboBox()
        for g in gestures:
            self.arm_gesture.addItem(g, g)
        self._select_combo(self.arm_gesture, cfg.arming_gesture)
        self.arm_gesture.currentIndexChanged.connect(self._apply_arming)

        self.disarm_gesture = QComboBox()
        self.disarm_gesture.addItem("None", "")
        for g in gestures:
            self.disarm_gesture.addItem(g, g)
        self._select_combo(self.disarm_gesture, cfg.disarm_gesture)
        self.disarm_gesture.currentIndexChanged.connect(self._apply_arming)

        self.arm_hold = QSpinBox()
        self.arm_hold.setRange(0, 5000)
        self.arm_hold.setSingleStep(100)
        self.arm_hold.setSuffix(" ms")
        self.arm_hold.setSpecialValueText("instant")
        self.arm_hold.setValue(int(cfg.arm_hold_ms))
        self.arm_hold.valueChanged.connect(self._apply_arming)

        self.arm_off_motion = QCheckBox("Motion control disabled")
        self.arm_off_motion.setChecked(cfg.disarm_on_motion_off)
        self.arm_off_motion.toggled.connect(self._apply_arming)
        self.arm_off_camera = QCheckBox("Camera disconnected")
        self.arm_off_camera.setChecked(cfg.disarm_on_camera_disconnect)
        self.arm_off_camera.toggled.connect(self._apply_arming)
        estop_chk = QCheckBox("Emergency Stop (always)")
        estop_chk.setChecked(True)
        estop_chk.setEnabled(False)

        grid.addWidget(self.arm_enable, 0, 0)
        grid.addWidget(self.arm_state_lbl, 0, 1, 1, 3)
        grid.addWidget(QLabel("Arming gesture:"), 1, 0)
        grid.addWidget(self.arm_gesture, 1, 1)
        grid.addWidget(QLabel("Disarm gesture:"), 1, 2)
        grid.addWidget(self.disarm_gesture, 1, 3)
        grid.addWidget(QLabel("Arm hold:"), 2, 0)
        grid.addWidget(self.arm_hold, 2, 1)
        grid.addWidget(QLabel("Automatic disarm:"), 3, 0)
        grid.addWidget(self.arm_off_motion, 3, 1)
        grid.addWidget(self.arm_off_camera, 3, 2)
        grid.addWidget(estop_chk, 3, 3)

        if self.bridge is not None:
            self.bridge.arming_state.connect(self._sync_arm_state)
        self._sync_arm_state(self.ctl.arming.state.value)
        self._arming_loading = False
        return box

    @staticmethod
    def _select_combo(combo, value) -> None:
        idx = combo.findData(value)
        combo.setCurrentIndex(idx if idx >= 0 else 0)

    def _apply_arming(self, *_):
        if getattr(self, "_arming_loading", False):
            return
        self.ctl.set_arming_config(
            arming_enabled=self.arm_enable.isChecked(),
            arming_gesture=self.arm_gesture.currentData() or "",
            disarm_gesture=self.disarm_gesture.currentData() or "",
            arm_hold_ms=int(self.arm_hold.value()),
            disarm_on_motion_off=self.arm_off_motion.isChecked(),
            disarm_on_camera_disconnect=self.arm_off_camera.isChecked())

    def _sync_arm_state(self, state: str) -> None:
        if not self.ctl.arming.enabled:
            self.arm_state_lbl.setText("Arming OFF — gestures always active")
            self.arm_state_lbl.setStyleSheet(
                "font-size:14px; font-weight:700; color:#71847a;")
            return
        text, colour = {
            "DISARMED": ("🔒 GESTURES DISARMED", "#aa3333"),
            "ARMING": ("⏳ ARMING…", "#d9a44a"),
            "ARMED": ("🟢 GESTURES ARMED", "#1a9e4b"),
            "DISARMING": ("⏳ DISARMING…", "#d9a44a"),
        }.get(state, (state, "#71847a"))
        self.arm_state_lbl.setText(text)
        self.arm_state_lbl.setStyleSheet(
            f"font-size:14px; font-weight:700; color:{colour};")

    def _selected_builtin_key(self):
        it = self.builtin_list.currentItem()
        if not it:
            return None
        g = it.text().split("  —")[0].strip()
        return "swipe" if g.startswith("swipe") else g

    def _apply_preset(self) -> None:
        from ..gestures import presets
        key = self._selected_builtin_key()
        if key is None:
            QMessageBox.information(self, "Preset",
                                    "Select a built-in gesture first.")
            return
        name = self.preset_combo.currentData()
        rows = presets.preview(key, name)
        body = "\n".join(f"  {k}: {v}" for k, v in rows)
        if QMessageBox.question(
                self, f"Apply {name} preset",
                f"Apply {name} to {key!r}?\n\n{body}",
                QMessageBox.Cancel | QMessageBox.Yes,
                QMessageBox.Cancel) != QMessageBox.Yes:
            return
        self.ctl.gesture_settings.set(key, presets.preset_for(key, name))
        self.ctl.gestures.apply_gesture_settings(
            self.ctl.gesture_settings.all())
        QMessageBox.information(self, "Preset", f"{name} applied to {key}.")

    def _reset_selected(self) -> None:
        key = self._selected_builtin_key()
        if key is None:
            QMessageBox.information(self, "Reset",
                                    "Select a built-in gesture first.")
            return
        self.ctl.gesture_settings.set(key, {})   # {} = defaults
        self.ctl.gestures.apply_gesture_settings(
            self.ctl.gesture_settings.all())
        QMessageBox.information(self, "Reset",
                                f"{key} tuning restored to defaults.")

    def _reset_all(self) -> None:
        if QMessageBox.question(
                self, "Reset ALL tuning",
                "Restore ALL gesture tuning to defaults?\n\n"
                "This changes ONLY tuning values — no gesture, mapping, "
                "workflow, action or profile is deleted.",
                QMessageBox.Cancel | QMessageBox.Yes,
                QMessageBox.Cancel) != QMessageBox.Yes:
            return
        for key in list(self.ctl.gesture_settings.all().keys()):
            self.ctl.gesture_settings.set(key, {})
        self.ctl.gestures.apply_gesture_settings(
            self.ctl.gesture_settings.all())
        QMessageBox.information(self, "Reset", "All tuning restored.")

    def _tune_builtin(self) -> None:
        it = self.builtin_list.currentItem()
        if not it:
            return
        gesture = it.text().split("  —")[0].strip()
        GestureTuneDialog(self.ctl, gesture, self).exec()

    def refresh(self) -> None:
        if hasattr(self, "compound_section"):
            self.compound_section.refresh()
        self.motion_list.clear()
        for m in self.ctl.motion_gestures.all():
            from ..gestures.trajectory import motion_samples
            n = len(motion_samples(m.template)) or m.template.get("samples", 1)
            ready = "✓ Ready" if m.template.get("points") else "no template"
            mapped = ("Mapped"
                      if self.ctl.motion_gesture_dependents(m.name)["rules"]
                      else "Unmapped")
            state = "Enabled" if m.enabled else "Disabled"
            label = (f"{m.name}   ·   {n} sample(s)   ·   {ready}   ·   "
                     f"{mapped}   ·   {state}")
            item = QListWidgetItem(label)
            item.setData(32, m.id)
            self.motion_list.addItem(item)
        self.custom_list.clear()
        for g in self.repo.all():
            kind = ("motion" if g.template.get("path", {}).get("movement", 0)
                    >= 0.12 else "pose")
            label = (f"{g.name}  [{kind}]  tolerance={g.tolerance:.2f}  "
                     f"min-conf={g.min_confidence:.2f}")
            if not g.enabled:
                label += "  (disabled)"
            item = QListWidgetItem(label)
            item.setData(32, g.id)
            self.custom_list.addItem(item)

    def _selected(self):
        it = self.custom_list.currentItem()
        if not it:
            return None
        for g in self.repo.all():
            if g.id == it.data(32):
                return g
        return None

    def _record(self) -> None:
        if not self.ctl.running:
            QMessageBox.warning(self, "Camera off",
                                "Start the camera first (it starts with the "
                                "app; check the Dashboard).")
            return
        # 1. name first
        name, ok = QInputDialog.getText(self, "Create custom gesture",
                                        "Gesture name:")
        name = name.strip().lower().replace(" ", "_") if ok else ""
        if not name:
            return
        if self._name_taken(name):
            QMessageBox.warning(self, "Duplicate",
                                f"Gesture name {name!r} already exists.")
            return
        # 2. record (one or more samples, with preview)
        dlg = RecorderDialog(self.bridge, name, self)
        if not dlg.exec() or dlg.template is None:
            return
        # 3. save with default tolerance; adjustable via Sensitivity…
        self.repo.create(name, dlg.template)
        self.ctl.reload_rules()
        self.refresh()
        # 4. offer to map it right away
        if self.on_mapped and QMessageBox.question(
                self, "Saved",
                f"Custom gesture {name!r} saved "
                f"({dlg.template.get('samples', 1)} sample(s)).\n\n"
                "Assign an action to it in Gesture Studio now?",
                QMessageBox.Yes | QMessageBox.No) == QMessageBox.Yes:
            self.on_mapped(name)

    def _name_taken(self, name: str) -> bool:
        existing = {g.name for g in self.repo.all()}
        existing |= {c.name for c in self.ctl.compound_gestures.all()}
        existing |= {m.name for m in self.ctl.motion_gestures.all()}
        builtin = set(STATIC_GESTURES + SWIPE_GESTURES
                      + TRAJECTORY_GESTURES)
        return name in existing or name in builtin

    # -- motion gestures -----------------------------------------------------
    def _selected_motion(self):
        it = self.motion_list.currentItem()
        if not it:
            return None
        return self.ctl.motion_gestures.get(it.data(32))

    def _record_motion(self) -> None:
        if not self.ctl.running:
            QMessageBox.warning(self, "Camera off",
                                "Start the camera first (it starts with the "
                                "app; check the Dashboard).")
            return
        name, ok = QInputDialog.getText(self, "Create motion gesture",
                                        "Gesture name:")
        name = name.strip().lower().replace(" ", "_") if ok else ""
        if not name:
            return
        if self._name_taken(name):
            QMessageBox.warning(self, "Duplicate",
                                f"Gesture name {name!r} already exists.")
            return
        dlg = MotionRecorderDialog(self.bridge, name, self)
        if not dlg.exec() or dlg.template is None:
            return
        self.ctl.motion_gestures.create(name, dlg.template)
        self.ctl.reload_rules()
        self.refresh()
        if self.on_mapped and QMessageBox.question(
                self, "Saved",
                f"Motion gesture {name!r} saved "
                f"({dlg.template.get('samples', 1)} sample(s)).\n\n"
                "Assign an action to it in Gesture Studio now?",
                QMessageBox.Yes | QMessageBox.No) == QMessageBox.Yes:
            self.on_mapped(name)

    def _test_motion(self) -> None:
        m = self._selected_motion()
        if m is None or self.bridge is None:
            return
        from .studio import TestRecognitionDialog
        TestRecognitionDialog(self.ctl, self.bridge, m.name, self).exec()

    def _adjust_motion(self) -> None:
        m = self._selected_motion()
        if m is None:
            return
        dlg = QDialog(self)
        dlg.setWindowTitle(f"Sensitivity — {m.name}")
        tol = QDoubleSpinBox()
        tol.setRange(0.15, 0.7)
        tol.setSingleStep(0.05)
        tol.setValue(m.tolerance)
        conf = QDoubleSpinBox()
        conf.setRange(0.3, 0.9)
        conf.setSingleStep(0.05)
        conf.setValue(m.min_confidence)
        cool = QDoubleSpinBox()
        cool.setRange(200, 5000)
        cool.setDecimals(0)
        cool.setSingleStep(100)
        cool.setSuffix(" ms")
        cool.setValue(m.cooldown_ms)
        form = QFormLayout(dlg)
        form.addRow(QLabel("Higher tolerance = looser shape matching."))
        form.addRow("Matching tolerance:", tol)
        form.addRow("Confidence threshold:", conf)
        form.addRow("Cooldown:", cool)
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        form.addRow(bb)
        if dlg.exec():
            self.ctl.motion_gestures.update(
                m.id, tolerance=tol.value(), min_confidence=conf.value(),
                cooldown_ms=int(cool.value()))
            self.ctl.reload_rules()
            self.refresh()

    def _toggle_motion(self) -> None:
        m = self._selected_motion()
        if m is None:
            return
        self.ctl.motion_gestures.update(m.id, enabled=not m.enabled)
        self.ctl.reload_rules()
        self.refresh()

    def _manage_motion(self) -> None:
        m = self._selected_motion()
        if m is None:
            QMessageBox.information(self, "Manage",
                                    "Select a motion gesture first.")
            return
        MotionGestureManagerDialog(self.ctl, self.bridge, m.id, self).exec()
        self.ctl.reload_rules()
        self.refresh()

    def _delete_motion(self) -> None:
        m = self._selected_motion()
        if m is None:
            return
        if _confirm_delete_motion(self, self.ctl, m):
            self.ctl.motion_gestures.delete(m.id)
            self.ctl.reload_rules()
            self.refresh()

    def _adjust(self) -> None:
        g = self._selected()
        if not g:
            return
        dlg = QDialog(self)
        dlg.setWindowTitle(f"Sensitivity — {g.name}")
        tol = QDoubleSpinBox()
        tol.setRange(0.05, 1.0)
        tol.setSingleStep(0.05)
        tol.setValue(g.tolerance)
        conf = QDoubleSpinBox()
        conf.setRange(0.1, 1.0)
        conf.setSingleStep(0.05)
        conf.setValue(g.min_confidence)
        form = QFormLayout(dlg)
        form.addRow("Tolerance (higher = looser match):", tol)
        form.addRow("Minimum confidence:", conf)
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        form.addRow(bb)
        if dlg.exec():
            self.repo.update(g.id, tolerance=tol.value(),
                             min_confidence=conf.value())
            self.ctl.reload_rules()
            self.refresh()

    def _toggle(self) -> None:
        g = self._selected()
        if g:
            self.repo.update(g.id, enabled=not g.enabled)
            self.ctl.reload_rules()
            self.refresh()

    def _delete(self) -> None:
        g = self._selected()
        if not g:
            return
        if QMessageBox.question(self, "Delete",
                                f"Delete gesture {g.name!r}?"
                                ) == QMessageBox.Yes:
            self.repo.delete(g.id)
            self.ctl.reload_rules()
            self.refresh()
