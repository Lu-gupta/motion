"""EventBus → Qt signal bridge.

Bus callbacks arrive on worker threads; Qt widgets must only be touched
from the GUI thread. Signals with queued delivery cross that boundary.
"""
from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from ..core.events import EventBus


class QtBridge(QObject):
    frame = Signal(object, float)          # ndarray, ts
    camera_status = Signal(object, str)    # CameraStatus, detail
    hands = Signal(object)                 # HandFrame
    gesture = Signal(object)               # GestureEvent
    context_changed = Signal(object)       # Context
    rule_matched = Signal(object, object, object)
    action_executed = Signal(object, bool, str)
    control_enabled = Signal(bool)
    arbiter_pending = Signal(object, float)   # GestureEvent, deadline
    workflow_progress = Signal(str, int, int, str, str)  # name,i,total,label,state
    workflow_done = Signal(str, str, str)     # name, status, detail
    trajectory_candidate = Signal(object, float)  # points ([] = cleared), ts
    workflow_vars = Signal(str, dict)         # workflow name, variables
    activity_changed = Signal()               # recent-activity log updated
    confirm_request = Signal(int, str, str, str)  # token, wf, gesture, profile
    control_locked = Signal(bool)             # gestures locked / armed
    gesture_blocked = Signal(str, str)        # gesture, reason (locked)
    arming_state = Signal(str)                # DISARMED/ARMING/ARMED/DISARMING

    def __init__(self, bus: EventBus) -> None:
        super().__init__()
        bus.subscribe("camera.frame", self.frame.emit)
        bus.subscribe("camera.status", self.camera_status.emit)
        bus.subscribe("vision.hands", self.hands.emit)
        bus.subscribe("gesture.event", self.gesture.emit)
        bus.subscribe("context.changed", self.context_changed.emit)
        bus.subscribe("rule.matched", self.rule_matched.emit)
        bus.subscribe("action.executed", self.action_executed.emit)
        bus.subscribe("control.enabled", self.control_enabled.emit)
        bus.subscribe("arbiter.pending", self.arbiter_pending.emit)
        bus.subscribe("workflow.progress", self.workflow_progress.emit)
        bus.subscribe("workflow.done", self.workflow_done.emit)
        bus.subscribe("trajectory.candidate", self.trajectory_candidate.emit)
        bus.subscribe("workflow.vars", self.workflow_vars.emit)
        bus.subscribe("activity.changed", self.activity_changed.emit)
        bus.subscribe("workflow.confirm_request", self.confirm_request.emit)
        bus.subscribe("control.locked", self.control_locked.emit)
        bus.subscribe("gesture.blocked", self.gesture_blocked.emit)
        bus.subscribe("arming.state", self.arming_state.emit)
