"""Gesture engine.

Consumes vision.hands (HandFrame); publishes gesture.event (GestureEvent).
Executes nothing — pure event emitter.

State machine per static/custom gesture:

    IDLE --(seen debounce_frames in a row)--> ACTIVE   → emit "start"
    ACTIVE --(each frame while held)-------->          → emit "hold"
    ACTIVE --(missing release_frames)-------> IDLE     → emit "end"

Swipes are instantaneous: emit "start" then "end" immediately.
Cooldown: a gesture that just ended cannot restart within cooldown_ms.
"""
from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass, field

from ..core.events import EventBus
from ..core.hand_select import filter_hand_frame, user_perspective
from ..core.types import GestureEvent, Hand, HandFrame
from . import static as static_gestures
from .custom import CustomGestureMatcher
from .motion import SwipeDetector
from .trajectory import TrajectoryEngine

log = logging.getLogger(__name__)

TRAJECTORY_SECONDS = 1.5


@dataclass
class _GestureState:
    consecutive: int = 0
    missing: int = 0
    active: bool = False
    last_end: float = float("-inf")
    confidence: float = 0.0


class GestureEngine:
    # frames of lost tracking tolerated before swipe history is discarded
    # (fast swipes blur the hand and MediaPipe briefly drops it)
    TRACKING_GAP_FRAMES = 4
    SWIPE_DISPLAY_HOLD_S = 0.8  # keep swipe visible for the UI poll

    # swipe-vs-trajectory arbitration (post-fire, deterministic):
    # a fired swipe whose recent fingertip path has turned less than
    # HOLD_TURN emits immediately (pure swipes: zero added latency);
    # above that it is held up to HOLD_S — dropped if a shape completes
    # or the turn proves a loop/reversal (DROP_TURN), emitted otherwise.
    SWIPE_HOLD_TURN = 0.6    # rad (~34°)
    SWIPE_DROP_TURN = 1.9    # rad (~109°)
    SWIPE_HOLD_S = 0.25

    def __init__(self, bus: EventBus, *,
                 confidence_threshold: float = 0.7,
                 debounce_frames: int = 3,
                 release_frames: int = 4,
                 cooldown_ms: int = 600,
                 swipe_min_distance: float = 0.15,
                 swipe_min_speed: float = 0.6,
                 swipe_window_s: float = 0.5,
                 swipe_mirror_x: bool = True) -> None:
        self.bus = bus
        self.confidence_threshold = confidence_threshold
        self.debounce_frames = debounce_frames
        self.release_frames = release_frames
        self.cooldown_s = cooldown_ms / 1000.0
        self.swipes = SwipeDetector(swipe_min_distance, swipe_min_speed,
                                    swipe_window_s, mirror_x=swipe_mirror_x)
        self.trajectories = TrajectoryEngine()
        # snapshot detector defaults so clearing an override RESTORES them
        # (Reset must return tuning to defaults, not leave the last preset)
        self._swipe_defaults = {
            "min_distance": self.swipes.min_distance,
            "min_speed": self.swipes.min_speed,
            "min_duration_s": getattr(self.swipes, "min_duration_s", None),
            "max_duration_s": getattr(self.swipes, "max_duration_s", None),
            "cooldown_s": getattr(self.swipes, "cooldown_s", None)}
        _c = self.trajectories.detector("circle")
        self._circle_defaults = ({
            "min_confidence": _c.min_confidence,
            "min_diameter": _c.min_diameter,
            "max_duration_s": _c.max_duration_s,
            "cooldown_s": _c.cooldown_s} if _c is not None else {})
        self._traj_candidate_shown = False
        self._pending_swipe: tuple[str, float] | None = None  # (name, t0)
        self.custom = CustomGestureMatcher()
        self.gesture_overrides: dict[str, dict] = {}
        self._states: dict[str, _GestureState] = {}
        self._trajectory: deque[tuple[float, float, float]] = deque()
        self._empty_streak = 0
        self._swipe_display_until = float("-inf")
        self.current_gesture = "none"
        self.current_confidence = 0.0
        self.enabled = True
        self._tracking = False          # a hand was seen on the last frame
        # neutral-before-retrigger (opt-in via the controller): after a
        # discrete shape/swipe fires, block re-emission of the SAME
        # gesture until one neutral frame is observed
        self.require_neutral = False
        self._neutral_block: set[str] = set()
        self._last_reject: tuple[str, str] | None = None   # (gesture, reason)
        # hand selection / control routing (see core/hand_select.py). Set
        # from config by the controller; "both" is a strict no-op. This is
        # an INGESTION filter — it selects which hands enter recognition,
        # never changes a recognition algorithm.
        self.hand_control = "both"
        self.current_handedness = ""    # user-perspective, for the UI
        bus.subscribe("vision.hands", self.on_hands)

    def _state(self, name: str) -> _GestureState:
        if name not in self._states:
            self._states[name] = _GestureState()
        return self._states[name]

    # -- per-gesture tuning -------------------------------------------------
    def _threshold_for(self, gesture: str) -> float:
        return float(self.gesture_overrides.get(gesture, {}).get(
            "confidence", self.confidence_threshold))

    def _cooldown_for(self, gesture: str) -> float:
        ms = self.gesture_overrides.get(gesture, {}).get("cooldown_ms")
        return (ms / 1000.0) if ms is not None else self.cooldown_s

    def _threshold_for_shape(self, gesture: str) -> float:
        """Trajectory shapes gate on their detector's sensitivity unless a
        per-gesture override exists."""
        ov = self.gesture_overrides.get(gesture, {})
        if "confidence" in ov:
            return float(ov["confidence"])
        det = self.trajectories.detector(gesture)
        return det.min_confidence if det else self.confidence_threshold

    def apply_gesture_settings(self, overrides: dict[str, dict]) -> None:
        """Apply per-gesture overrides (hot; no restart needed).

        Static/custom gestures: {confidence, cooldown_ms}.
        Group key 'swipe': {min_distance, min_speed, min_duration_ms,
        max_duration_ms, cooldown_ms} — applies to all four directions.
        Key 'circle': {confidence, min_size, max_duration_ms, cooldown_ms}
        — maps onto the circle trajectory detector.
        """
        self.gesture_overrides = dict(overrides or {})
        # ALWAYS restore detector defaults first, then apply the override
        # if present — so clearing an override truly resets the detector.
        sw = self.gesture_overrides.get("swipe", {})
        d = self.swipes
        dd = self._swipe_defaults
        d.min_distance = float(sw.get("min_distance", dd["min_distance"]))
        d.min_speed = float(sw.get("min_speed", dd["min_speed"]))
        if dd["min_duration_s"] is not None:
            d.min_duration_s = (sw["min_duration_ms"] / 1000.0
                                if "min_duration_ms" in sw
                                else dd["min_duration_s"])
        if dd["max_duration_s"] is not None:
            d.max_duration_s = (sw["max_duration_ms"] / 1000.0
                                if "max_duration_ms" in sw
                                else dd["max_duration_s"])
        if dd["cooldown_s"] is not None:
            d.cooldown_s = (sw["cooldown_ms"] / 1000.0
                            if "cooldown_ms" in sw else dd["cooldown_s"])
        ci = self.gesture_overrides.get("circle", {})
        c = self.trajectories.detector("circle")
        cd = self._circle_defaults
        if c is not None and cd:
            c.min_confidence = float(ci.get("confidence",
                                            cd["min_confidence"]))
            c.min_diameter = float(ci.get("min_size", cd["min_diameter"]))
            c.max_duration_s = (ci["max_duration_ms"] / 1000.0
                                if "max_duration_ms" in ci
                                else cd["max_duration_s"])
            c.cooldown_s = (ci["cooldown_ms"] / 1000.0
                            if "cooldown_ms" in ci else cd["cooldown_s"])

    def _publish_traj_state(self, ts: float) -> None:
        """Lightweight candidate feedback: publish the fingertip trail
        while a shape candidate is forming, and one clearing event when
        it stops. Nothing is published in the idle steady state."""
        active = self.trajectories.candidate
        if active:
            self.bus.publish("trajectory.candidate",
                             self.trajectories.points(), ts)
            self._traj_candidate_shown = True
        elif self._traj_candidate_shown:
            self._traj_candidate_shown = False
            self.bus.publish("trajectory.candidate", [], ts)

    # -- main entry ---------------------------------------------------------
    def on_hands(self, hf: HandFrame) -> None:
        if not self.enabled:
            return
        # hand-selection boundary: drop ineligible hands BEFORE any
        # recognition. "both" returns the same frame (no-op). A dropped
        # hand is indistinguishable from an absent hand downstream.
        hf = filter_hand_frame(hf, self.hand_control)
        self.current_handedness = (user_perspective(hf.hands[0].handedness)
                                   if hf.hands else "")
        ts = hf.timestamp
        if not hf.hands:
            self._tracking = False
            self._neutral_block.clear()   # hand gone = neutral reached
            # a held swipe flushes when the hand leaves — the motion is
            # over, so it cannot be a shape any more
            if self._pending_swipe is not None:
                self._flush_pending_swipe(None, ts, force=True)
            # tolerate brief tracking loss (motion blur during fast swipes);
            # only discard motion history after a sustained gap
            self._empty_streak += 1
            if self._empty_streak > self.TRACKING_GAP_FRAMES:
                self._trajectory.clear()
                self.swipes.reset()
                self.trajectories.reset()
                self._publish_traj_state(ts)
            self._handle_frame(None, "", 0.0, 0.0, ts)
            if ts >= self._swipe_display_until:
                self.current_gesture = "none"
                self.current_confidence = 0.0
            return

        self._empty_streak = 0
        self._tracking = True
        hand = hf.hands[0]
        wrist = hand.landmarks[Hand.WRIST]
        self._trajectory.append((ts, wrist.x, wrist.y))
        cutoff = ts - TRAJECTORY_SECONDS
        while self._trajectory and self._trajectory[0][0] < cutoff:
            self._trajectory.popleft()

        # 1. trajectory shapes (index fingertip). Completed shapes win —
        #    a shape only completes when its own full criteria hold, so
        #    it may cancel a held (ambiguous) swipe; it can never cancel
        #    an already-emitted one.
        tip = hand.landmarks[Hand.INDEX_TIP]
        shape = self.trajectories.update(tip.x, tip.y, ts)
        self._publish_traj_state(ts)
        if shape:
            sname, sconf = shape
            if sconf >= self._threshold_for_shape(sname):
                self._pending_swipe = None   # the motion was the shape
                if self.require_neutral and sname in self._neutral_block:
                    # completed shape suppressed: hand has not returned to
                    # neutral since the last fire of this shape
                    self.swipes.reset()
                    self.current_gesture = sname
                    self.current_confidence = sconf
                    self._swipe_display_until = (ts
                                                 + self.SWIPE_DISPLAY_HOLD_S)
                    self._last_reject = (sname, "awaiting neutral state")
                    return
                self._emit(sname, "start", sconf, hand, ts)
                self._emit(sname, "end", sconf, hand, ts)
                self.current_gesture = sname
                self.current_confidence = sconf
                self._swipe_display_until = ts + self.SWIPE_DISPLAY_HOLD_S
                if self.require_neutral:
                    self._neutral_block.add(sname)
                self.swipes.reset()   # the same motion is not also a swipe
                return

        # 2. swipe — ALWAYS fed (a candidate shape never starves it).
        #    Straight swipes emit instantly; a swipe fired on a curving
        #    path is held briefly (see SWIPE_HOLD_TURN above).
        swipe = self.swipes.update(hand, ts)
        if swipe:
            if self.trajectories.net_turn(ts) < self.SWIPE_HOLD_TURN:
                self._emit_swipe(swipe, hand, ts)
                return
            self._pending_swipe = (swipe, ts)
        elif self._pending_swipe is not None:
            if self._flush_pending_swipe(hand, ts):
                return

        # 2. static gestures
        name, conf = static_gestures.classify(hand)

        # a relaxed/neutral pose clears the neutral-retrigger block so the
        # next drawn shape may fire again
        if name in ("none", "open_palm"):
            self._neutral_block.clear()

        # 3. custom gestures can override when static is weak/none
        traj = [(x, y) for (_, x, y) in self._trajectory]
        cm = self.custom.match(hand, traj)
        if cm and (name == "none" or cm[1] > conf):
            name, conf = cm

        if name != "none" and conf < self._threshold_for(name):
            name = "none"
            conf = 0.0
        if ts >= self._swipe_display_until:
            # display only — detection events below are unaffected by this
            self.current_gesture = name
            self.current_confidence = conf if name != "none" else 0.0
        self._handle_frame(name if name != "none" else None, name, conf,
                           0.0, ts, hand)

    def _emit_swipe(self, swipe: str, hand: Hand | None, ts: float) -> None:
        self._emit(swipe, "start", 1.0, hand, ts)
        self._emit(swipe, "end", 1.0, hand, ts)
        self.current_gesture = swipe
        self.current_confidence = 1.0
        self._swipe_display_until = ts + self.SWIPE_DISPLAY_HOLD_S
        # a swipe interrupts any held static gesture state
        for name, st in self._states.items():
            if st.active:
                st.active = False
                st.last_end = ts
                self._emit(name, "end", st.confidence, hand, ts)
            st.consecutive = 0

    def _flush_pending_swipe(self, hand: Hand | None, ts: float,
                             force: bool = False) -> bool:
        """Resolve a held swipe. Returns True when it emitted."""
        name, t0 = self._pending_swipe
        turn = self.trajectories.net_turn(ts)
        if turn >= self.SWIPE_DROP_TURN:
            self._pending_swipe = None   # the path looped/reversed —
            return False                 # not a swipe (shape may follow)
        if (force or ts - t0 >= self.SWIPE_HOLD_S
                or self.trajectories.tip_stopped()):
            self._pending_swipe = None
            self._emit_swipe(name, hand, ts)
            return True
        return False

    # -- state machine ------------------------------------------------------
    def _handle_frame(self, detected: str | None, name: str, conf: float,
                      _unused: float, ts: float,
                      hand: Hand | None = None) -> None:
        for gname, st in self._states.items():
            if gname == detected:
                continue
            if st.active:
                st.missing += 1
                if st.missing >= self.release_frames:
                    st.active = False
                    st.consecutive = 0
                    st.last_end = ts
                    self._emit(gname, "end", st.confidence, hand, ts)
            else:
                st.consecutive = 0

        if detected is None:
            return
        st = self._state(detected)
        if st.active:
            st.missing = 0
            st.confidence = conf
            self._emit(detected, "hold", conf, hand, ts)
        else:
            if ts - st.last_end < self._cooldown_for(detected):
                return
            st.consecutive += 1
            if st.consecutive >= self.debounce_frames:
                st.active = True
                st.missing = 0
                st.confidence = conf
                self._emit(detected, "start", conf, hand, ts)

    def _emit(self, gesture: str, phase: str, conf: float,
              hand: Hand | None, ts: float) -> None:
        wx = wy = 0.0
        handed = ""
        if hand is not None:
            wrist = hand.landmarks[Hand.WRIST]
            wx, wy = wrist.x, wrist.y
            handed = hand.handedness
        ev = GestureEvent(gesture=gesture, phase=phase, confidence=conf,
                          handedness=handed, hand_x=wx, hand_y=wy,
                          timestamp=ts)
        if phase != "hold":
            log.debug("Gesture %s %s conf=%.2f", gesture, phase, conf)
        self.bus.publish("gesture.event", ev)

    # -- read-only diagnostics (no new state machine) -----------------------
    def threshold_for(self, gesture: str) -> float:
        """Public: the confidence threshold currently applied to a
        gesture (per-gesture override, shape detector, or global)."""
        from .motion import SWIPE_GESTURES
        from .trajectory import TRAJECTORY_GESTURES
        if gesture in SWIPE_GESTURES:
            return 1.0   # swipes are threshold/rule-based, not confidence
        if gesture in TRAJECTORY_GESTURES \
                or self.trajectories.detector(gesture) is not None:
            return self._threshold_for_shape(gesture)
        return self._threshold_for(gesture)

    def cooldown_remaining(self, gesture: str, now: float | None = None
                           ) -> float:
        """Seconds of cooldown left for a static/custom gesture (0 when
        ready). Shapes/swipes manage cooldown in their own detectors."""
        now = time.monotonic() if now is None else now
        st = self._states.get(gesture)
        if st is None:
            return 0.0
        return max(0.0, self._cooldown_for(gesture) - (now - st.last_end))

    def diagnostics(self, gesture: str, now: float | None = None) -> dict:
        """A read-only snapshot of EXISTING engine state for the Studio
        diagnostic panel. Never mutates anything, never fabricates a
        confidence — fields a detector does not expose stay None."""
        now = time.monotonic() if now is None else now
        matched = self.current_gesture == gesture
        conf = self.current_confidence if matched else None
        threshold = self.threshold_for(gesture)
        remaining = self.cooldown_remaining(gesture, now)
        st = self._states.get(gesture)
        active = bool(st and st.active)
        candidate = self.trajectories.candidate
        if not self._tracking:
            state = "NO TRACKING"
        elif remaining > 0:
            state = "COOLDOWN"
        elif matched:
            state = "MATCH"
        elif active:
            state = "TRACKING"
        elif candidate:
            state = "CANDIDATE"
        else:
            state = "WAITING"
        reason = None
        if self._last_reject and self._last_reject[0] == gesture:
            reason = self._last_reject[1]
        out = {"state": state, "confidence": conf, "threshold": threshold,
               "cooldown_remaining_ms": int(remaining * 1000),
               "tracking": self._tracking, "reason": reason}
        det = self.trajectories.detector(gesture)
        if det is not None and getattr(det, "last", None):
            out["shape"] = dict(det.last)
        return out

    # -- misc ---------------------------------------------------------------
    def trajectory(self) -> list[tuple[float, float]]:
        return [(x, y) for (_, x, y) in self._trajectory]

    def reset(self) -> None:
        self._states.clear()
        self._trajectory.clear()
        self.swipes.reset()
        self.trajectories.reset()
        self._traj_candidate_shown = False
        self._pending_swipe = None
        self._empty_streak = 0
        self._swipe_display_until = float("-inf")
        self.current_gesture = "none"
        self.current_confidence = 0.0
        self._neutral_block.clear()
        self._last_reject = None


def all_gesture_names(custom_rows=(), motion_rows=()) -> list[str]:
    names = list(static_gestures.STATIC_GESTURES)
    from .motion import SWIPE_GESTURES
    from .trajectory import TRAJECTORY_GESTURES
    names += SWIPE_GESTURES
    names += TRAJECTORY_GESTURES
    names += [r.name for r in custom_rows]
    names += [r.name for r in motion_rows]
    return names
