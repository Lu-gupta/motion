"""Dashboard — live runtime state. Every value is bound to real events."""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QGridLayout, QGroupBox, QHBoxLayout, QLabel,
                               QPushButton, QVBoxLayout, QWidget)

from ..core.types import CameraStatus
from ..runtime.controller import MotionController
from .bridge import QtBridge
from .video_widget import VideoWidget


class StatusValue(QLabel):
    def __init__(self, text: str = "—") -> None:
        super().__init__(text)
        self.setStyleSheet("font-weight: 600;")
        self.setTextInteractionFlags(Qt.TextSelectableByMouse)


class Dashboard(QWidget):
    def __init__(self, ctl: MotionController, bridge: QtBridge) -> None:
        super().__init__()
        self.ctl = ctl

        self.video = VideoWidget(mirror=ctl.cfg.mirror_preview)
        bridge.frame.connect(self.video.on_frame)
        bridge.hands.connect(self.video.on_hands)
        bridge.trajectory_candidate.connect(self.video.on_trajectory)

        self.toggle_btn = QPushButton("Enable Motion Control")
        self.toggle_btn.setCheckable(True)
        self.toggle_btn.setMinimumHeight(44)
        self.toggle_btn.clicked.connect(self._on_toggle)

        self.stop_btn = QPushButton("EMERGENCY STOP")
        self.stop_btn.setMinimumHeight(44)
        self.stop_btn.setStyleSheet(
            "QPushButton {background:#8b1a1a; color:white; font-weight:700;}")
        self.stop_btn.clicked.connect(self._on_emergency)

        # detection flash — lights up when a gesture fires, then fades back
        self.flash = QLabel("Waiting for gestures…")
        self.flash.setAlignment(Qt.AlignCenter)
        self.flash.setMinimumHeight(56)
        self.flash.setWordWrap(True)
        self._flash_idle_style = (
            "QLabel {background: transparent; color: #71847a;"
            " border: 1px dashed #71847a; border-radius: 8px;"
            " font-size: 13px;}")
        self._flash_hit_style = (
            "QLabel {background: #16351f; color: #35d698;"
            " border: 1px solid #1a9e4b; border-radius: 8px;"
            " font-size: 14px; font-weight: 700;}")
        self.flash.setStyleSheet(self._flash_idle_style)
        self._flash_timer = QTimer(self)
        self._flash_timer.setSingleShot(True)
        self._flash_timer.timeout.connect(self._flash_reset)

        grid_box = QGroupBox("Runtime state")
        grid = QGridLayout(grid_box)
        self.v_motion = StatusValue("OFF")
        self.v_camera = StatusValue("Stopped")
        self.v_fps = StatusValue("0")
        self.v_app = StatusValue("—")
        self.v_window = StatusValue("—")
        self.v_profile = StatusValue("—")
        self.v_gesture = StatusValue("none")
        self.v_conf = StatusValue("0%")
        self.v_action = StatusValue("—")
        self.v_workflow = StatusValue("—")
        rows = [
            ("Motion Control", self.v_motion),
            ("Camera", self.v_camera),
            ("Camera FPS", self.v_fps),
            ("Active Application", self.v_app),
            ("Active Window", self.v_window),
            ("Active Profile", self.v_profile),
            ("Current Gesture", self.v_gesture),
            ("Confidence", self.v_conf),
            ("Last Action", self.v_action),
            ("Workflow", self.v_workflow),
        ]
        for i, (label, w) in enumerate(rows):
            grid.addWidget(QLabel(label + ":"), i, 0)
            grid.addWidget(w, i, 1)

        right = QVBoxLayout()
        right.addWidget(self.toggle_btn)
        right.addWidget(self.stop_btn)
        right.addWidget(self.flash)
        right.addWidget(grid_box)
        right.addStretch(1)

        lay = QHBoxLayout(self)
        lay.addWidget(self.video, 3)
        rw = QWidget()
        rw.setLayout(right)
        rw.setMaximumWidth(360)
        lay.addWidget(rw, 2)

        self._flash_wait_style = (
            "QLabel {background: #33290f; color: #d9a44a;"
            " border: 1px solid #a06a12; border-radius: 8px;"
            " font-size: 13px; font-weight: 600;}")

        # live bindings
        bridge.gesture.connect(self._on_gesture_event)
        bridge.arbiter_pending.connect(self._on_pending)
        bridge.camera_status.connect(self._on_camera_status)
        bridge.control_enabled.connect(self._on_control_enabled)
        bridge.context_changed.connect(self._on_context)
        bridge.rule_matched.connect(self._on_rule_matched)
        bridge.action_executed.connect(self._on_action)
        bridge.workflow_progress.connect(self._on_workflow_progress)
        bridge.workflow_done.connect(self._on_workflow_done)
        bridge.trajectory_candidate.connect(self._on_traj_candidate)
        self._wf_timer = QTimer(self)
        self._wf_timer.setSingleShot(True)
        self._wf_timer.timeout.connect(lambda: self.v_workflow.setText("—"))

        self._poll = QTimer(self)
        self._poll.timeout.connect(self._refresh_poll)
        self._poll.start(150)
        self._on_control_enabled(ctl.motion_enabled)

    # -- handlers -----------------------------------------------------------
    def _on_toggle(self, checked: bool) -> None:
        self.ctl.set_motion_enabled(checked)

    def _on_emergency(self) -> None:
        self.ctl.emergency_disable()

    def _on_control_enabled(self, enabled: bool) -> None:
        self.v_motion.setText("ON" if enabled else "OFF")
        self.v_motion.setStyleSheet(
            "font-weight:700; color:%s;" % ("#1a9e4b" if enabled else "#aa3333"))
        self.toggle_btn.setChecked(enabled)
        self.toggle_btn.setText("Disable Motion Control" if enabled
                                else "Enable Motion Control")

    def _on_camera_status(self, status: CameraStatus, detail: str) -> None:
        pretty = {CameraStatus.CONNECTED: "Connected",
                  CameraStatus.DISCONNECTED: "Disconnected",
                  CameraStatus.STARTING: "Starting…",
                  CameraStatus.STOPPED: "Stopped",
                  CameraStatus.ERROR: "Error"}[status]
        self.v_camera.setText(pretty)
        ok = status == CameraStatus.CONNECTED
        self.v_camera.setStyleSheet(
            "font-weight:700; color:%s;" % ("#1a9e4b" if ok else "#aa3333"))
        if not ok:
            self.video.clear_feed()

    def _on_context(self, ctx) -> None:
        self.v_app.setText(ctx.application or "—")
        title = ctx.window_title or "—"
        self.v_window.setText(title[:48] + ("…" if len(title) > 48 else ""))

    def _on_gesture_event(self, ev) -> None:
        if ev.phase != "start":
            return
        pretty = ev.gesture.replace("_", " ").upper()
        self.flash.setText(f"{pretty}   ·   {ev.confidence * 100:.0f}%")
        self.flash.setStyleSheet(self._flash_hit_style)
        self._flash_timer.start(1500)

    def _on_traj_candidate(self, points, _ts: float) -> None:
        # only show the hint while nothing stronger is on the flash panel
        if points and not self._flash_timer.isActive():
            self.flash.setText("Drawing motion…")
            self.flash.setStyleSheet(self._flash_wait_style)
            self._flash_timer.start(600)

    def _on_pending(self, ev, deadline: float) -> None:
        pretty = ev.gesture.replace("_", " ").upper()
        self.flash.setText(f"{pretty}   ·   waiting for next gesture…")
        self.flash.setStyleSheet(self._flash_wait_style)
        self._flash_timer.start(
            max(300, int((deadline - ev.timestamp) * 1000)))

    def _flash_reset(self) -> None:
        self.flash.setText("Waiting for gestures…")
        self.flash.setStyleSheet(self._flash_idle_style)

    def _on_rule_matched(self, ev, ctx, match) -> None:
        self.v_profile.setText(match.profile_name)
        pretty = ev.gesture.replace("_", " ").upper()
        action = match.action.name or match.action.type
        self.flash.setText(
            f"{pretty}   ·   {ev.confidence * 100:.0f}%   →   {action}")
        self.flash.setStyleSheet(self._flash_hit_style)
        self._flash_timer.start(1500)

    def _on_action(self, spec, ok, detail) -> None:
        name = spec.name or spec.type
        self.v_action.setText(name if ok else f"{name} (failed)")

    def _wf_step_lines(self, name: str, current: int) -> str:
        """✓ done · ● current · ○ pending checklist (capped at 8 rows)."""
        wf = self.ctl.workflow_repo.by_name(name)
        if wf is None:
            return ""
        lines = []
        for idx, step in enumerate(wf.steps, 1):
            mark = "✓" if idx < current else ("●" if idx == current else "○")
            t = step.get("type")
            if t == "delay":
                text = f"Wait {int(step.get('ms', 0))} ms"
            elif t == "wait":
                from ..context.conditions import describe
                text = f"Wait for {describe(step)}"
            elif t in ("ui_find", "ui_wait"):
                from ..context.uia import describe_criteria
                text = f"UI {describe_criteria(step)}"
            elif t in ("ui_click", "ui_focus", "ui_type"):
                verb = {"ui_click": "Click", "ui_focus": "Focus",
                        "ui_type": "Type into"}[t]
                text = f"{verb} '{step.get('ref', '')}'"
            elif t == "if":
                from ..context.conditions import describe
                conds = step.get("conditions", [])
                text = ("IF " + (describe(conds[0]) if conds else "?")
                        + ("…" if len(conds) > 1 else ""))
            elif t == "set_var":
                text = f"Set {{{step.get('name', '')}}}"
            elif t == "ui_read":
                text = (f"Read '{step.get('ref', '')}' → "
                        f"{{{step.get('store_var', '')}}}")
            elif t == "set_clipboard":
                text = "Set clipboard"
            elif t == "retry":
                text = f"Retry up to {int(step.get('attempts', 1))}×"
            elif t == "repeat":
                text = f"Repeat {int(step.get('count', 1))}×"
            elif t == "repeat_until":
                from ..context.conditions import describe
                conds = step.get("conditions", [])
                text = ("Repeat until "
                        + (describe(conds[0]) if conds else "?")
                        + ("…" if len(conds) > 1 else ""))
            else:
                a = self.ctl.profile_manager.actions.get(
                    step.get("action_id", 0))
                text = a.name if a else "?"
            lines.append(f"{mark} {text}")
        if len(lines) > 8:
            lines = lines[max(0, current - 4):current + 4]
        return "\n".join(lines)

    def _on_workflow_progress(self, name: str, i: int, total: int,
                              label: str, state: str) -> None:
        self._wf_timer.stop()
        # "condition" states carry live loop/branch info (attempt 2/3,
        # verdicts) — show that line; plain steps just show the checklist
        extra = f"\n{label}" if state == "condition" else ""
        self.v_workflow.setText(
            f"{name} — {i}/{total}{extra}\n{self._wf_step_lines(name, i)}")
        self.v_workflow.setStyleSheet("font-weight:600; color:#d9a44a;")

    def _on_workflow_done(self, name: str, status: str, detail: str) -> None:
        mark = {"completed": "✓", "failed": "✕", "cancelled": "■"}[status]
        text = f"{mark} {name} {status}"
        if status == "failed" and detail:
            text += f"\n{detail}"
        self.v_workflow.setText(text)
        color = {"completed": "#1a9e4b", "failed": "#aa3333",
                 "cancelled": "#d9a44a"}[status]
        self.v_workflow.setStyleSheet(f"font-weight:700; color:{color};")
        self._wf_timer.start(5000)

    def _refresh_poll(self) -> None:
        if not self.isVisible():
            return
        st = self.ctl.status()
        self.v_fps.setText(f"{st['camera_fps']:.0f}")
        self.v_gesture.setText(st["gesture"])
        self.v_conf.setText(f"{st['confidence'] * 100:.0f}%")
        ctx = st["context"]
        self.v_app.setText(ctx.application or "—")
        if st["last_profile"]:
            self.v_profile.setText(st["last_profile"])
