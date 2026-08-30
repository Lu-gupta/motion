"""Gesture arming / disarming safety gate.

A small, explicit CONTROL-layer state machine that sits at the execution
boundary of the EXISTING pipeline. It observes the final recognized
gesture events (the same `gesture.event` the controller already handles)
and decides whether execution may proceed. It is NOT a recognizer: it
never touches the camera/vision threads, never re-detects a gesture,
never duplicates arbitration or mapping. Pure O(1) per event.

States:

    DISARMED   recognition still happens (diagnostics work); nothing maps
               to an action / cursor / workflow.
    ARMING     the arming gesture is being held toward `arm_hold_ms`.
    ARMED      existing behavior — gestures execute exactly as before.
    DISARMING  transient step while a disarm is being applied (pending
               execution is cancelled here via the on_disarm callback).

Safety-state relationship (kept centralized, no conflicting states):

    EMERGENCY STOP  >  LOCK / DISARMED  >  ARMED

- Emergency Stop (motion off) ALWAYS disarms (the controller calls
  force_disarm on the E-stop path, independent of config).
- The Gesture Studio LOCK and this DISARMED gate are peers: both let
  recognition continue while blocking execution. ARMED restores normal
  execution but LOCK / motion-off still win.

Critical safety rule: the configured arming/disarm gestures are CONSUMED
by this gate — while the arming feature is ON they never execute their
own mapped action (no duplicate recognition path; the controller simply
does not forward a consumed event to arbitration/execution).
"""
from __future__ import annotations

import logging
import time
from enum import Enum

from ..core.types import CameraStatus, GestureEvent

log = logging.getLogger(__name__)


class ArmState(str, Enum):
    DISARMED = "DISARMED"
    ARMING = "ARMING"
    ARMED = "ARMED"
    DISARMING = "DISARMING"


class ArmingController:
    def __init__(self, bus, cfg, *, on_disarm=None,
                 clock=time.monotonic) -> None:
        """on_disarm(): called once when LEAVING the ARMED state so the
        controller can cancel anything pending (arbiter holds, continuous
        actions, confirmations) — nothing armed must survive a disarm."""
        self.bus = bus
        self.cfg = cfg
        self._on_disarm = on_disarm
        self._clock = clock
        self.state = ArmState.DISARMED
        self._arming_since = 0.0
        bus.subscribe("control.enabled", self._on_control)
        bus.subscribe("camera.status", self._on_camera)

    # -- config snapshot ----------------------------------------------------
    @property
    def enabled(self) -> bool:
        return bool(self.cfg.arming_enabled)

    @property
    def armed(self) -> bool:
        return self.state == ArmState.ARMED

    def _control_set(self) -> set:
        s = set()
        if self.cfg.arming_gesture:
            s.add(self.cfg.arming_gesture)
        if self.cfg.disarm_gesture:
            s.add(self.cfg.disarm_gesture)
        return s

    # -- the gate -----------------------------------------------------------
    def allow(self, ev: GestureEvent) -> bool:
        """True if this recognized gesture event may proceed to
        arbitration/execution. Consumes the arming/disarm control gesture
        (returns False), and blocks all execution unless ARMED.

        Fail-open when the feature is off OR enabled without an arming
        gesture chosen — so a misconfiguration can never lock a user out.
        """
        if not self.enabled or not self.cfg.arming_gesture:
            return True
        if ev.gesture in self._control_set():
            self._handle_control(ev)
            return False                     # control gesture never executes
        return self.state == ArmState.ARMED

    def _handle_control(self, ev: GestureEvent) -> None:
        g, ph, ts = ev.gesture, ev.phase, ev.timestamp
        arm_g = self.cfg.arming_gesture
        dis_g = self.cfg.disarm_gesture
        hold = max(0, int(self.cfg.arm_hold_ms)) / 1000.0

        # disarm: ARMED + disarm gesture completes
        if (self.state == ArmState.ARMED and dis_g and g == dis_g
                and ph == "start"):
            self._begin_disarm("disarm gesture")
            return

        # arm: DISARMED/ARMING + arming gesture
        if g == arm_g and self.state in (ArmState.DISARMED, ArmState.ARMING):
            if ph == "start":
                if hold <= 0:
                    self._set(ArmState.ARMED)
                else:
                    self._arming_since = ts
                    self._set(ArmState.ARMING)
            elif ph == "hold" and self.state == ArmState.ARMING:
                if ts - self._arming_since >= hold:
                    self._set(ArmState.ARMED)
            elif ph == "end" and self.state == ArmState.ARMING:
                # an instantaneous gesture (swipe/shape/compound: start==end
                # timestamp, no hold phase) completes the hold; a static
                # released before the hold elapsed cancels back to DISARMED
                if ts <= self._arming_since or ts - self._arming_since >= hold:
                    self._set(ArmState.ARMED)
                else:
                    self._set(ArmState.DISARMED)
        # any other control-gesture case (e.g. arming gesture while ARMED,
        # disarm gesture while DISARMED) is simply consumed — no state
        # change, and critically no mapped action executes.

    # -- transitions --------------------------------------------------------
    def _set(self, state: ArmState) -> None:
        if state == self.state:
            return
        prev = self.state
        self.state = state
        log.info("Arming %s -> %s", prev.value, state.value)
        self.bus.publish("arming.state", state.value)
        if prev == ArmState.ARMED and state != ArmState.ARMED and self._on_disarm:
            self._on_disarm()   # cancel anything pending when leaving ARMED

    def _begin_disarm(self, reason: str) -> None:
        log.info("Disarming (%s)", reason)
        self._set(ArmState.DISARMING)   # fires on_disarm (was ARMED)
        self._set(ArmState.DISARMED)

    def force_disarm(self, reason: str = "forced") -> None:
        """Unconditional disarm (Emergency Stop, config change)."""
        if self.state == ArmState.ARMED:
            self._begin_disarm(reason)
        elif self.state != ArmState.DISARMED:
            self._set(ArmState.DISARMED)

    def publish_state(self) -> None:
        self.bus.publish("arming.state", self.state.value)

    def reset(self, publish: bool = True) -> None:
        """Return to the safe default. Used at startup and whenever the
        arming configuration changes."""
        if self.state != ArmState.DISARMED:
            self.force_disarm("reset")
        if publish:
            self.publish_state()

    # -- external safety signals -------------------------------------------
    def _on_control(self, enabled: bool) -> None:
        # motion off (E-stop routes here too, and also calls force_disarm
        # directly so it disarms regardless of this setting)
        if not enabled and self.enabled and self.cfg.disarm_on_motion_off:
            self.force_disarm("motion disabled")

    def _on_camera(self, status, _detail: str = "") -> None:
        if not self.enabled:
            return
        if (status in (CameraStatus.DISCONNECTED, CameraStatus.STOPPED,
                       CameraStatus.ERROR)
                and self.cfg.disarm_on_camera_disconnect):
            self.force_disarm("camera lost")
        # a reconnect (CONNECTED) intentionally does NOT auto-arm
