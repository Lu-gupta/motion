"""Runtime controller — wires the full pipeline.

Camera → HandTracker → GestureEngine → (Context + RuleEngine) → Executor

Owns lifecycle, motion-control arming, safety (global cooldown,
continuous-rule gating, emergency disable), and the cursor-control mode.
"""
from __future__ import annotations

import logging
import threading
import time

from ..actions.executor import ActionExecutor
from ..actions import input_win
from ..camera.capture import CameraWorker
from ..context.detector import ContextDetector
from ..core.config import Config
from ..core.events import EventBus
from ..core.types import (ActionSpec, CameraStatus, Context, GestureEvent,
                          Hand)
from ..data.db import Database
from ..data.repository import (CompoundGestureRepo, CustomGestureRepo,
                               GestureSettingsRepo, MotionGestureRepo,
                               SettingsRepo, WorkflowRepo, ZoneRepo)
from ..gestures.compound import CompoundEngine
from .activity import ActivityLog
from .arbiter import GestureArbiter
from .arming import ArmingController
from .cursor import CursorController
from .recorder import WorkflowRecorder
from .workflows import WorkflowEngine
from ..gestures.engine import GestureEngine
from ..profiles.manager import ProfileManager
from ..rules.engine import RuleEngine
from ..vision.hand_tracker import HandTracker

log = logging.getLogger(__name__)

# the existing static gesture reused for pinch-and-hold drag (thumb+index)
DRAG_GESTURE = "pinch"


class MotionController:
    def __init__(self, cfg: Config, db: Database, bus: EventBus) -> None:
        self.cfg = cfg
        self.db = db
        self.bus = bus

        self.profile_manager = ProfileManager(db)
        self.profile_manager.seed_defaults()
        self.custom_gestures = CustomGestureRepo(db)
        self.settings = SettingsRepo(db)
        self.zones = ZoneRepo(db)
        self.gesture_settings = GestureSettingsRepo(db)
        self.compound_gestures = CompoundGestureRepo(db)
        self.motion_gestures = MotionGestureRepo(db)

        self.camera = CameraWorker(bus, cfg.camera_index, cfg.frame_width,
                                   cfg.frame_height, cfg.target_fps)
        self.tracker = HandTracker(
            bus, cfg.max_hands, cfg.min_detection_confidence,
            cfg.min_tracking_confidence, cfg.landmark_smoothing)
        self.gestures = GestureEngine(
            bus,
            confidence_threshold=cfg.gesture_confidence_threshold,
            debounce_frames=cfg.debounce_frames,
            release_frames=cfg.release_frames,
            cooldown_ms=cfg.default_cooldown_ms,
            swipe_min_distance=cfg.swipe_min_distance,
            swipe_min_speed=cfg.swipe_min_speed,
            swipe_window_s=cfg.swipe_window_s,
            swipe_mirror_x=cfg.swipe_mirror_x)
        self.context = ContextDetector(bus, cfg.context_poll_ms)
        self.compounds = CompoundEngine(bus)
        self.executor = ActionExecutor(bus)
        self.workflow_repo = WorkflowRepo(db)
        # settings may lower the repeat bound; the 100 ceiling is absolute
        self.workflow_repo.max_repeat = max(
            1, min(int(cfg.workflow_max_repeat),
                   WorkflowRepo.MAX_REPEAT_ITERATIONS))
        self.workflows = WorkflowEngine(bus, self.executor,
                                        self.profile_manager.actions,
                                        self.workflow_repo)
        self.executor.workflows = self.workflows
        # workflow recorder — dormant until the UI starts a session;
        # cancels itself on control.enabled False (E-stop / motion off)
        self.recorder = WorkflowRecorder(bus)
        # bounded recent-activity log for the Command Center (observes
        # existing bus events; no camera-thread work, no DB log)
        self.activity = ActivityLog(bus)
        # pending workflow confirmations (token → (action, name))
        self._confirm_lock = threading.Lock()
        self._pending_confirms: dict[int, tuple] = {}
        self._confirm_seq = 0
        self.rules = RuleEngine(self.profile_manager.profiles,
                                self.profile_manager.rules,
                                self.profile_manager.actions,
                                zones_provider=self.zones.as_dict)
        self.arbiter = GestureArbiter(
            bus, self.compounds,
            resolver=lambda g, c: self.rules.resolve(g, c,
                                                     self._screen_size()),
            executor=self._execute_arbitrated)

        self._motion_enabled = cfg.motion_control_enabled
        # Gesture Studio lock: recognition/tracking keep running, but no
        # mapping executes. Distinct from motion-off; Emergency Stop
        # (motion off) still overrides everything.
        self._gestures_locked = False
        # hand selection / control routing (which physical hand may drive
        # gestures). Config-driven; "both" = existing behavior. Applied at
        # the GestureEngine ingestion boundary — no recognition change.
        from ..core.hand_select import normalize_hand_control
        self.gestures.hand_control = normalize_hand_control(cfg.hand_control)
        # cursor-control movement sensitivity (the existing gain, now
        # user-adjustable). Independent of gesture recognition confidence.
        from ..core.config import normalize_cursor_sensitivity
        self.cursor_sensitivity = normalize_cursor_sensitivity(
            cfg.cursor_sensitivity)
        # single cursor-output boundary: stabilization + pinch-drag. The
        # only place the OS cursor is moved for cursor control and the only
        # place the left mouse button is held.
        self.cursor = CursorController()
        self.apply_drag_settings()
        # drag is fed by the SAME vision.hands frames the engine consumes
        # (subscribed after it, so recognition always runs first)
        bus.subscribe("vision.hands", self._on_hands_for_drag)
        bus.subscribe("camera.status", self._on_camera_for_drag)
        # arming/disarming safety gate — a control-layer state machine at
        # the execution boundary (NOT a recognizer). Starts DISARMED.
        self.arming = ArmingController(bus, cfg,
                                       on_disarm=self._on_arming_disarm)
        self.gestures.require_neutral = cfg.require_neutral_before_retrigger
        self._last_action_ts = 0.0
        self._rule_last_fire: dict[int, float] = {}
        self._active_continuous: dict[str, ActionSpec] = {}  # gesture → action
        # cursor anchoring lives entirely in self.cursor (single owner)
        self._lock = threading.Lock()

        self.last_action_desc = ""
        self.last_profile = ""
        self.running = False
        self._shutdown = False

        self._reload_custom_gestures()
        self.gestures.apply_gesture_settings(self.gesture_settings.all())
        self.compounds.set_definitions(self.compound_gestures.all())
        bus.subscribe("gesture.event", self._on_gesture)

    # -- lifecycle ----------------------------------------------------------
    def start(self) -> None:
        if self.running:
            return
        log.info("Starting runtime pipeline")
        self.tracker.start()
        self.camera.start()
        self.context.start()
        self.running = True

    def stop(self) -> None:
        if not self.running:
            return
        log.info("Stopping runtime pipeline")
        self.workflows.cancel_all("application stopping")
        self.camera.stop()
        self.tracker.stop()
        self.context.stop()
        self.gestures.reset()
        self.running = False

    def shutdown(self) -> None:
        """Full application teardown. Idempotent — safe to call any
        number of times, from the tray quit, window close and the main()
        exit path.

        Order matters: disarming first publishes control.enabled False,
        which cancels running workflows mid-delay/mid-wait, drops every
        arbiter hold (their timers are cancelled) and releases continuous
        actions; stop() then joins the camera thread, closes the
        MediaPipe landmarker and joins the context poller.
        """
        if self._shutdown:
            return
        self._shutdown = True
        log.info("Application shutdown requested")
        self.cursor.abort("application shutdown")
        self.set_motion_enabled(False)
        self.workflows.cancel_all("application shutdown")
        try:
            self.recorder.cancel()
        except Exception:
            log.debug("recorder cancel on shutdown failed", exc_info=True)
        self.stop()
        log.info("Application shutdown complete")

    # -- motion control arming ---------------------------------------------
    @property
    def motion_enabled(self) -> bool:
        return self._motion_enabled

    def set_motion_enabled(self, enabled: bool) -> None:
        self._motion_enabled = enabled
        if not enabled:
            self._release_all_continuous()
            # pending confirmations are void once motion is off (E-stop
            # authoritative — nothing waits to run behind a prompt)
            with self._confirm_lock:
                self._pending_confirms.clear()
        self.bus.publish("control.enabled", enabled)
        log.info("Motion control %s", "ENABLED" if enabled else "DISABLED")

    def emergency_disable(self) -> None:
        log.warning("EMERGENCY DISABLE triggered")
        # E-stop ALWAYS disarms (independent of disarm-on-motion-off), and
        # cancels an in-progress ARMING — highest-priority safety action.
        self.arming.force_disarm("emergency stop")
        self.set_motion_enabled(False)

    # -- arming / disarming safety gate ------------------------------------
    def _on_arming_disarm(self) -> None:
        """Leaving ARMED must not let queued/held execution slip through."""
        self.cursor.abort("disarmed")
        self.arbiter.reset()
        self._release_all_continuous()
        with self._confirm_lock:
            self._pending_confirms.clear()

    def set_arming_config(self, **fields) -> None:
        """Update arming configuration, persist it, and return to the safe
        DISARMED default (any config change re-arms from scratch)."""
        allowed = {"arming_enabled", "arming_gesture", "disarm_gesture",
                   "arm_hold_ms", "disarm_on_motion_off",
                   "disarm_on_camera_disconnect"}
        for k, v in fields.items():
            if k not in allowed:
                raise ValueError(f"bad arming field {k}")
            setattr(self.cfg, k, v)
        try:
            self.cfg.save()
        except Exception:
            log.debug("config save failed after arming change", exc_info=True)
        self.arming.reset()

    # -- gesture lock (recognition continues, execution suppressed) ---------
    @property
    def gestures_locked(self) -> bool:
        return self._gestures_locked

    def set_gestures_locked(self, locked: bool) -> None:
        self._gestures_locked = bool(locked)
        if self._gestures_locked:
            self.cursor.abort("gestures locked")
        self.bus.publish("control.locked", self._gestures_locked)
        log.info("Gestures %s", "LOCKED" if locked else "ARMED")

    def set_require_neutral(self, on: bool) -> None:
        self.cfg.require_neutral_before_retrigger = bool(on)
        self.gestures.require_neutral = bool(on)

    def set_cursor_sensitivity(self, value) -> float:
        """Cursor-control movement gain. Applied live (the next cursor frame
        uses it); persisted; invalid values fall back to the default.
        Affects ONLY cursor movement — not gesture recognition, hand
        selection, arming or any other action."""
        from ..core.config import normalize_cursor_sensitivity
        v = normalize_cursor_sensitivity(value)
        self.cursor_sensitivity = v
        self.cfg.cursor_sensitivity = v
        try:
            self.cfg.save()
        except Exception:
            log.debug("config save failed after cursor sensitivity change",
                      exc_info=True)
        log.info("Cursor sensitivity set to %.2f", v)
        return v

    # -- pinch-and-hold drag ------------------------------------------------
    def apply_drag_settings(self) -> None:
        """Push the drag tuning from config onto the drag machine (live)."""
        from ..core.config import (normalize_drag_release,
                                   normalize_drag_start_ms)
        d = self.cursor.drag
        d.start_delay_s = normalize_drag_start_ms(
            self.cfg.cursor_drag_start_ms) / 1000.0
        d.release_conf = normalize_drag_release(self.cfg.cursor_drag_release)

    def set_drag_settings(self, *, enabled: bool | None = None,
                          start_ms=None, release=None) -> None:
        """Update + persist drag configuration. Disabling always releases a
        held button first, so the setting can never strand mouse state."""
        if enabled is not None:
            self.cfg.cursor_drag_enabled = bool(enabled)
        if start_ms is not None:
            self.cfg.cursor_drag_start_ms = int(start_ms)
        if release is not None:
            self.cfg.cursor_drag_release = float(release)
        self.apply_drag_settings()
        if not self.cfg.cursor_drag_enabled:
            self.cursor.abort("pinch drag disabled")
        try:
            self.cfg.save()
        except Exception:
            log.debug("config save failed after drag change", exc_info=True)

    @property
    def drag_active(self) -> bool:
        return self.cursor.drag.dragging

    def _drag_allowed(self) -> bool:
        """Drag obeys EVERY existing execution gate — it never bypasses
        them (E-stop/motion-off, Studio lock, and the arming gate)."""
        if not self._motion_enabled or self._gestures_locked:
            return False
        if self.arming.enabled and not self.arming.armed:
            return False
        return True

    def _drag_consumes(self, ev: GestureEvent) -> bool:
        """While pinch drag is enabled the pinch gesture is DEDICATED to
        dragging: its mapped action never executes, so a drag can never
        also fire a click/workflow (and never double-fires)."""
        return (self.cfg.cursor_drag_enabled
                and ev.source != "compound"
                and ev.gesture == DRAG_GESTURE)

    def _on_hands_for_drag(self, hf) -> None:
        """Per-frame drag update. Runs after the gesture engine on the same
        synchronous vision.hands event. O(1); costs nothing when disabled."""
        if not self.cfg.cursor_drag_enabled:
            return
        from ..core.hand_select import filter_hand_frame
        from ..gestures.static import pinch_confidence
        hands = filter_hand_frame(hf, self.gestures.hand_control).hands
        if not hands:
            # selected hand gone — never leave the button held
            self.cursor.abort("hand lost")
            return
        allowed = self._drag_allowed()
        hand = hands[0]
        conf = pinch_confidence(hand)
        event = self.cursor.drag.update(conf, hf.timestamp, allowed)
        if event == "start":
            self.cursor.reset()          # anchor the drag where it began
        if self.cursor.drag.dragging:
            wrist = hand.landmarks[Hand.WRIST]
            self.cursor.move(wrist.x, wrist.y, self.cursor_sensitivity,
                             self._screen_size())

    def _on_camera_for_drag(self, status, _detail: str = "") -> None:
        if status in (CameraStatus.DISCONNECTED, CameraStatus.STOPPED,
                      CameraStatus.ERROR):
            self.cursor.abort("camera lost")

    def set_hand_control(self, mode: str) -> str:
        """Choose which physical hand drives gestures ('left'|'right'|
        'both'). Applied live (no recognition restart); persisted; survives
        camera reconnect and lifecycle resets. Returns the normalized mode."""
        from ..core.hand_select import normalize_hand_control
        m = normalize_hand_control(mode)
        self.cfg.hand_control = m
        try:
            self.cfg.save()
        except Exception:
            log.debug("config save failed after hand_control change",
                      exc_info=True)
        if m != self.gestures.hand_control:
            self.cursor.abort("control hand changed")
        self.gestures.hand_control = m
        self.bus.publish("hand.control", m)
        log.info("Control hand set to %s", m)
        return m

    # -- configuration reload ----------------------------------------------
    def reload_rules(self) -> None:
        self.rules.reload()
        self._reload_custom_gestures()
        self.gestures.apply_gesture_settings(self.gesture_settings.all())
        self.compounds.set_definitions(self.compound_gestures.all())

    def _reload_custom_gestures(self) -> None:
        self.gestures.custom.set_templates(self.custom_gestures.all())
        self.gestures.trajectories.set_templates(self.motion_gestures.all())

    # -- motion-gesture template management ---------------------------------
    def _gesture_name_taken(self, name: str) -> bool:
        """True if a gesture name is already used by a built-in, custom,
        compound or motion gesture (the whole gesture namespace)."""
        from ..gestures.motion import SWIPE_GESTURES
        from ..gestures.static import STATIC_GESTURES
        from ..gestures.trajectory import TRAJECTORY_GESTURES
        if name in set(STATIC_GESTURES) | set(SWIPE_GESTURES) \
                | set(TRAJECTORY_GESTURES):
            return True
        return (any(g.name == name for g in self.custom_gestures.all())
                or any(c.name == name for c in self.compound_gestures.all())
                or any(m.name == name for m in self.motion_gestures.all()))

    def motion_gesture_dependents(self, name: str) -> dict:
        """What references a motion gesture by name: mapping rules (with
        their profile + action) and compound gestures. Read-only — used to
        warn before delete and to show a Mapped/Unmapped badge. Reuses the
        existing name-based reference model (rules/compounds key on name)."""
        pm = self.profile_manager
        prof = {p.id: p for p in pm.profiles.all()}
        rules = []
        for r in pm.rules.all():
            if r.gesture == name:
                a = pm.actions.get(r.action_id)
                rules.append({
                    "profile": prof[r.profile_id].name
                    if r.profile_id in prof else "?",
                    "action": a.name if a else "?", "rule_id": r.id})
        comps = [c.name for c in self.compound_gestures.all()
                 if any(s.get("gesture") == name for s in c.steps)]
        return {"rules": rules, "compounds": comps}

    def rename_motion_gesture(self, gid: int, new_name: str) -> tuple[bool, str]:
        """Rename a recorded motion gesture and cascade the new name to
        every reference (mapping rules + compound steps) so Command Center
        mappings are preserved, never broken. Deterministic; no data loss."""
        new = new_name.strip().lower().replace(" ", "_")
        if not new:
            return False, "a name is required"
        row = self.motion_gestures.get(gid)
        if row is None:
            return False, "gesture not found"
        if new == row.name:
            return True, ""
        if self._gesture_name_taken(new):
            return False, f"the name {new!r} is already in use"
        old = row.name
        self.motion_gestures.update(gid, name=new)
        for r in self.profile_manager.rules.all():
            if r.gesture == old:
                self.profile_manager.rules.update(r.id, gesture=new)
        for c in self.compound_gestures.all():
            if any(s.get("gesture") == old for s in c.steps):
                steps = [{**s, "gesture": new} if s.get("gesture") == old
                         else s for s in c.steps]
                self.compound_gestures.update(c.id, steps=steps, hand=c.hand)
        self.reload_rules()
        return True, ""

    # -- gesture handling ---------------------------------------------------
    def _on_gesture(self, ev: GestureEvent) -> None:
        if not self._motion_enabled:
            return
        # safety gate: consumes the arming/disarm control gesture and blocks
        # all execution unless ARMED (recognition already happened upstream,
        # so diagnostics still see the gesture). O(1); feature-off = pass.
        if not self.arming.allow(ev):
            return
        # pinch drag consumes the pinch gesture (cursor interaction, never a
        # RuleEngine action) so dragging can't also fire its mapping
        if self._drag_consumes(ev):
            return
        ctx = self.context.current
        screen = self._screen_size()

        if ev.phase == "start":
            # arbitration: primitives that could become a compound are
            # held; compound events may wait for a longer match
            if ev.source == "compound":
                if self.arbiter.on_compound_start(ev, ctx) == "deferred":
                    return
            else:
                if self.arbiter.on_primitive_start(ev, ctx) != "immediate":
                    return
            self._start_action(ev, ctx, screen)

        elif ev.phase == "hold":
            with self._lock:
                action = self._active_continuous.get(ev.gesture)
            if action is None:
                return
            if action.type == "cursor_control":
                self._drive_cursor(ev)
            else:
                # continuous rule: re-fire respecting its own cooldown
                match = self.rules.resolve(ev.gesture, ctx, screen)
                if match and match.continuous:
                    now = time.monotonic()
                    cooldown = (match.cooldown_ms
                                or self.cfg.default_cooldown_ms) / 1000
                    if now - self._rule_last_fire.get(match.rule_id, 0.0) >= cooldown:
                        self._rule_last_fire[match.rule_id] = now
                        self._fire(match, ev)

        elif ev.phase == "end":
            if ev.source != "compound":
                self.arbiter.on_primitive_end(ev)
            with self._lock:
                self._active_continuous.pop(ev.gesture, None)
            if not self.cursor.drag.dragging:
                self.cursor.reset()   # end of a cursor session (not a drag)

    # -- action paths -------------------------------------------------------
    def _start_action(self, ev: GestureEvent, ctx: Context,
                      screen: tuple[int, int]) -> None:
        match = self.rules.resolve(ev.gesture, ctx, screen)
        if match is None:
            self.bus.publish("rule.unmatched", ev, ctx)
            return
        self.last_profile = match.profile_name
        if match.continuous or match.action.type == "cursor_control":
            with self._lock:
                self._active_continuous[ev.gesture] = match.action
            if not self.cursor.drag.dragging:
                self.cursor.reset()      # a new cursor session anchors fresh
            self.bus.publish("rule.matched", ev, ctx, match)
            if match.action.type != "cursor_control":
                self._fire(match, ev)
            return
        self._execute_discrete(match, ev, ctx)

    def _execute_discrete(self, match, ev: GestureEvent,
                          ctx: Context) -> None:
        """Discrete action with per-rule + global cooldown gating."""
        now = time.monotonic()
        cooldown = (match.cooldown_ms or self.cfg.default_cooldown_ms) / 1000
        if now - self._rule_last_fire.get(match.rule_id, 0.0) < cooldown:
            return
        if now - self._last_action_ts < self.cfg.action_global_cooldown_ms / 1000:
            return
        self._rule_last_fire[match.rule_id] = now
        self._last_action_ts = now
        self.bus.publish("rule.matched", ev, ctx, match)
        self._fire(match, ev)

    def _execute_arbitrated(self, match, ev: GestureEvent,
                            ctx: Context) -> None:
        """Deferred release from the arbiter (timer thread)."""
        if not self._motion_enabled:
            return
        self.last_profile = match.profile_name
        self._execute_discrete(match, ev, ctx)

    def _fire(self, match, ev: GestureEvent) -> None:
        if self._gestures_locked:
            # locked: recognition already happened (diagnostics can show
            # it), but the mapping must not execute
            self.bus.publish("gesture.blocked", ev.gesture, "gestures locked")
            log.info("Gesture %s matched but gestures are LOCKED — not "
                     "executing", ev.gesture)
            return
        if self._needs_confirmation(match.action):
            self._request_confirmation(match, ev)
            return
        ok = self.executor.execute(match.action)
        self.last_action_desc = match.action.name or match.action.type
        log.info("Gesture %s → action %r (%s) [%s]", ev.gesture,
                 self.last_action_desc, match.profile_name,
                 "ok" if ok else "FAILED")

    # -- dangerous-workflow confirmation ------------------------------------
    def _needs_confirmation(self, action: ActionSpec) -> bool:
        """A gesture-triggered workflow flagged 'requires confirmation'
        prompts first — but only when the global setting is on. One
        indexed lookup, only at fire time (never per frame)."""
        if not self.cfg.confirm_dangerous_workflows:
            return False
        if action.type != "workflow":
            return False
        wid = int(action.params.get("workflow_id", 0) or 0)
        wf = self.workflow_repo.get(wid)
        return bool(wf and wf.requires_confirmation)

    def _request_confirmation(self, match, ev: GestureEvent) -> None:
        name = match.action.name or match.action.type
        with self._confirm_lock:
            self._confirm_seq += 1
            token = self._confirm_seq
            self._pending_confirms[token] = (match.action, name)
        self.bus.publish("workflow.confirm_request", token, name,
                         ev.gesture, match.profile_name)
        log.info("Gesture %s → workflow %r awaiting confirmation",
                 ev.gesture, name)

    def resolve_confirmation(self, token: int, accept: bool) -> None:
        """Called from the GUI after the user answers the prompt.
        Emergency Stop / motion-off in the meantime always wins."""
        with self._confirm_lock:
            entry = self._pending_confirms.pop(token, None)
        if entry is None:
            return
        action, name = entry
        if not accept or not self._motion_enabled:
            # reflect the decline/abort in the activity log + dashboard
            self.bus.publish("workflow.done", name, "cancelled",
                             "confirmation declined" if not accept
                             else "motion control off")
            return
        self.executor.execute(action)
        self.last_action_desc = name

    # -- cursor-control mode ------------------------------------------------
    def _drive_cursor(self, ev: GestureEvent) -> None:
        """Relative cursor control: hand delta from anchor drives cursor."""
        if self._gestures_locked:
            return   # locked: continuous cursor movement is suppressed too
        if self.cursor.drag.dragging:
            return   # the drag feed owns cursor movement while dragging
        # the SAME anchor-relative mapping cursor control always used, now
        # routed through the shared stabilizing cursor boundary. Movement
        # gain = the user-adjustable sensitivity (never confidence).
        self.cursor.move(ev.hand_x, ev.hand_y, self.cursor_sensitivity,
                         self._screen_size())

    def _release_all_continuous(self) -> None:
        # motion off / E-stop: a held drag button MUST be released here
        self.cursor.abort("continuous actions released")
        with self._lock:
            self._active_continuous.clear()

    @staticmethod
    def _screen_size() -> tuple[int, int]:
        import ctypes
        u = ctypes.windll.user32
        return u.GetSystemMetrics(0), u.GetSystemMetrics(1)

    # -- status for UI ------------------------------------------------------
    def status(self) -> dict:
        return {
            "motion_enabled": self._motion_enabled,
            "camera_status": self.camera.status,
            "camera_fps": self.camera.fps,
            "vision_ms": self.tracker.process_ms,
            "context": self.context.current,
            "gesture": self.gestures.current_gesture,
            "confidence": self.gestures.current_confidence,
            "last_action": self.last_action_desc,
            "last_profile": self.last_profile,
            "arming_enabled": self.arming.enabled,
            "arming_state": self.arming.state.value,
            "hand_control": self.gestures.hand_control,
            "detected_hand": self.gestures.current_handedness,
        }
