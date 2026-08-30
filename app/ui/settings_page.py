"""Settings page — camera + engine tuning, persisted to config.json."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox,
                               QFormLayout, QGroupBox, QHBoxLayout, QLabel,
                               QMessageBox, QPushButton, QSlider, QSpinBox,
                               QVBoxLayout, QWidget)

from ..camera.enumerate import list_cameras
from ..core.config import (CURSOR_SENSITIVITY_MAX, CURSOR_SENSITIVITY_MIN,
                           DRAG_RELEASE_MAX, DRAG_RELEASE_MIN,
                           DRAG_START_MS_MAX, DRAG_START_MS_MIN,
                           normalize_cursor_sensitivity,
                           normalize_drag_release, normalize_drag_start_ms)
from ..runtime.controller import MotionController


class SettingsPage(QWidget):
    def __init__(self, ctl: MotionController) -> None:
        super().__init__()
        self.ctl = ctl
        cfg = ctl.cfg

        cam_box = QGroupBox("Camera")
        cam_form = QFormLayout(cam_box)
        self.camera_combo = QComboBox()
        self.camera_combo.addItem(f"Camera {cfg.camera_index} (current)",
                                  cfg.camera_index)
        scan_btn = QPushButton("Scan for cameras")
        scan_btn.clicked.connect(self._scan)
        self.mirror_check = QCheckBox("Mirror preview")
        self.mirror_check.setChecked(cfg.mirror_preview)
        cam_form.addRow("Device:", self.camera_combo)
        cam_form.addRow("", scan_btn)
        cam_form.addRow("", self.mirror_check)

        eng_box = QGroupBox("Gesture engine")
        eng_form = QFormLayout(eng_box)
        self.conf_spin = QDoubleSpinBox()
        self.conf_spin.setRange(0.3, 1.0)
        self.conf_spin.setSingleStep(0.05)
        self.conf_spin.setValue(cfg.gesture_confidence_threshold)
        self.debounce_spin = QSpinBox()
        self.debounce_spin.setRange(1, 15)
        self.debounce_spin.setValue(cfg.debounce_frames)
        self.cooldown_spin = QSpinBox()
        self.cooldown_spin.setRange(0, 5000)
        self.cooldown_spin.setSuffix(" ms")
        self.cooldown_spin.setValue(cfg.default_cooldown_ms)
        self.smooth_spin = QDoubleSpinBox()
        self.smooth_spin.setRange(0.0, 0.9)
        self.smooth_spin.setSingleStep(0.1)
        self.smooth_spin.setValue(cfg.landmark_smoothing)
        eng_form.addRow("Confidence threshold:", self.conf_spin)
        eng_form.addRow("Debounce frames:", self.debounce_spin)
        eng_form.addRow("Default cooldown:", self.cooldown_spin)
        eng_form.addRow("Landmark smoothing:", self.smooth_spin)

        # -- cursor control (movement sensitivity only) ---------------------
        cur_box = QGroupBox("Cursor control")
        cur_form = QFormLayout(cur_box)
        self.cursor_slider = QSlider(Qt.Horizontal)
        self.cursor_slider.setMinimum(int(CURSOR_SENSITIVITY_MIN * 100))
        self.cursor_slider.setMaximum(int(CURSOR_SENSITIVITY_MAX * 100))
        self.cursor_slider.setSingleStep(10)
        self.cursor_slider.setPageStep(20)
        self.cursor_slider.setValue(
            int(normalize_cursor_sensitivity(cfg.cursor_sensitivity) * 100))
        self.cursor_value = QLabel()
        self.cursor_slider.valueChanged.connect(self._cursor_label)
        self._cursor_label(self.cursor_slider.value())
        srow = QHBoxLayout()
        srow.addWidget(self.cursor_slider, 1)
        srow.addWidget(self.cursor_value)
        cur_form.addRow("Sensitivity:", srow)
        cur_form.addRow("", QLabel(
            "How far the cursor travels for a given hand movement. Lower = "
            "finer, higher = faster. This does not change how the cursor "
            "gesture itself is recognized."))

        # -- drag control (separate from sensitivity) -----------------------
        drag_box = QGroupBox("Drag control")
        drag_form = QFormLayout(drag_box)
        self.drag_check = QCheckBox("Enable pinch drag")
        self.drag_check.setChecked(bool(cfg.cursor_drag_enabled))
        self.drag_check.setToolTip(
            "Pinch and hold to press and hold the left mouse button, move to "
            "drag, release the pinch to drop. While enabled the pinch "
            "gesture is dedicated to dragging and does not run its mapping.")
        gesture_lbl = QLabel("Thumb + index")
        self.drag_start = QSpinBox()
        self.drag_start.setRange(DRAG_START_MS_MIN, DRAG_START_MS_MAX)
        self.drag_start.setSingleStep(10)
        self.drag_start.setSuffix(" ms")
        self.drag_start.setValue(
            normalize_drag_start_ms(cfg.cursor_drag_start_ms))
        self.drag_start.setToolTip(
            "How long the pinch must be held before the drag starts.")
        self.drag_release = QDoubleSpinBox()
        self.drag_release.setRange(DRAG_RELEASE_MIN, DRAG_RELEASE_MAX)
        self.drag_release.setSingleStep(0.05)
        self.drag_release.setValue(
            normalize_drag_release(cfg.cursor_drag_release))
        self.drag_release.setToolTip(
            "Pinch strength below which an active drag lets go. Lower = the "
            "drag survives more hand-tracking noise before releasing.")
        drag_form.addRow("", self.drag_check)
        drag_form.addRow("Pinch gesture:", gesture_lbl)
        drag_form.addRow("Start delay:", self.drag_start)
        drag_form.addRow("Release tolerance:", self.drag_release)
        cur_form.addRow(drag_box)

        ui_box = QGroupBox("Application")
        ui_form = QFormLayout(ui_box)
        self.close_combo = QComboBox()
        self.close_combo.addItem(
            "Minimize to tray (keep gestures running)", True)
        self.close_combo.addItem("Quit the application", False)
        self.close_combo.setCurrentIndex(0 if cfg.minimize_to_tray else 1)
        self.armed_check = QCheckBox("Motion control armed at startup")
        self.armed_check.setChecked(cfg.motion_control_enabled)
        self.repeat_spin = QSpinBox()
        self.repeat_spin.setRange(1, 100)
        self.repeat_spin.setValue(
            max(1, min(int(cfg.workflow_max_repeat), 100)))
        self.repeat_spin.setToolTip(
            "Highest count a workflow REPEAT step may use. The absolute "
            "ceiling is 100 — this setting can only lower it.")
        self.confirm_check = QCheckBox(
            "Require confirmation for workflows flagged \"requires "
            "confirmation\"")
        self.confirm_check.setChecked(cfg.confirm_dangerous_workflows)
        self.neutral_check = QCheckBox(
            "Require neutral hand state before a discrete gesture "
            "re-triggers")
        self.neutral_check.setChecked(cfg.require_neutral_before_retrigger)
        ui_form.addRow("When I close the window:", self.close_combo)
        ui_form.addRow("", self.armed_check)
        ui_form.addRow("Max workflow repeat iterations:", self.repeat_spin)
        ui_form.addRow("", self.confirm_check)
        ui_form.addRow("", self.neutral_check)

        apply_btn = QPushButton("Apply and save")
        apply_btn.clicked.connect(self._apply)

        lay = QVBoxLayout(self)
        lay.addWidget(cam_box)
        lay.addWidget(eng_box)
        lay.addWidget(cur_box)
        lay.addWidget(ui_box)
        lay.addWidget(apply_btn)
        lay.addWidget(QLabel("Camera/engine changes apply immediately; "
                             "no restart needed."))
        lay.addStretch(1)

    def _cursor_label(self, raw: int) -> None:
        v = raw / 100.0
        band = "Low" if v < 1.6 else ("Medium" if v <= 3.2 else "High")
        self.cursor_value.setText(f"{v:.1f}×  ({band})")

    def _scan(self) -> None:
        was_running = self.ctl.running
        if was_running:
            self.ctl.camera.stop()  # release device for probing
        try:
            cams = list_cameras()
        finally:
            if was_running:
                self.ctl.camera.start()
        self.camera_combo.clear()
        if not cams:
            QMessageBox.information(self, "Cameras", "No cameras found.")
            self.camera_combo.addItem(
                f"Camera {self.ctl.cfg.camera_index} (current)",
                self.ctl.cfg.camera_index)
            return
        for c in cams:
            self.camera_combo.addItem(
                f"{c.name} ({c.width}x{c.height})", c.index)

    def _apply(self) -> None:
        cfg = self.ctl.cfg
        cfg.camera_index = self.camera_combo.currentData()
        cfg.mirror_preview = self.mirror_check.isChecked()
        cfg.gesture_confidence_threshold = self.conf_spin.value()
        cfg.debounce_frames = self.debounce_spin.value()
        cfg.default_cooldown_ms = self.cooldown_spin.value()
        cfg.landmark_smoothing = self.smooth_spin.value()
        cfg.minimize_to_tray = bool(self.close_combo.currentData())
        cfg.motion_control_enabled = self.armed_check.isChecked()
        cfg.workflow_max_repeat = self.repeat_spin.value()
        cfg.confirm_dangerous_workflows = self.confirm_check.isChecked()
        cfg.require_neutral_before_retrigger = self.neutral_check.isChecked()
        cfg.save()
        self.ctl.workflow_repo.max_repeat = cfg.workflow_max_repeat
        self.ctl.set_require_neutral(cfg.require_neutral_before_retrigger)
        # cursor movement gain — live, persisted, cursor-only
        self.ctl.set_cursor_sensitivity(self.cursor_slider.value() / 100.0)
        # drag control — live; disabling always releases a held button
        self.ctl.set_drag_settings(enabled=self.drag_check.isChecked(),
                                   start_ms=self.drag_start.value(),
                                   release=self.drag_release.value())

        # live-apply
        eng = self.ctl.gestures
        eng.confidence_threshold = cfg.gesture_confidence_threshold
        eng.debounce_frames = cfg.debounce_frames
        eng.cooldown_s = cfg.default_cooldown_ms / 1000.0
        self.ctl.tracker.smoother.alpha = cfg.landmark_smoothing
        if self.ctl.camera.index != cfg.camera_index:
            self.ctl.camera.set_camera(cfg.camera_index)
        QMessageBox.information(self, "Settings", "Saved and applied.")
