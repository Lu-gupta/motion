"""Compound / temporal gesture engine.

Sits AFTER the primitive gesture engine in the pipeline:

    vision → GestureEngine (primitives) → gesture.event
                                             │
                                             ▼
                                       CompoundEngine ── gesture.event
                                                          (source="compound")

Consumes the normalized primitive event stream only — no vision
processing, no MediaPipe access. Each enabled compound definition runs
an explicit per-definition state machine:

    IDLE ──step 0 satisfied──▶ WAITING(step 1) ── … ──▶ MATCHED → emit
      ▲                                │
      └────── timeout / cancel ◀───────┘

Step types:
    gesture  — satisfied by the primitive's START transition
    hold     — gesture START, then still ACTIVE after hold_ms
               (early END cancels the whole partial sequence)
    release  — satisfied by the gesture's END transition; empty gesture
               means "the previous step's gesture"

Timing: step_timeout_ms bounds the gap between step completions,
max_duration_ms bounds the whole sequence, min_gap_ms enforces a
minimum gap (double-tap debounce), cooldown_ms gates re-fire.

Hand identity: any | left | right | same (locked to the first step's
hand). False-positive protection relies on the primitive layer emitting
exactly one START per physical gesture (its own debounce/state machine),
plus the timing bounds here. Partial sequences expire lazily — a stale
partial can never complete because expiry is checked before matching.

Simultaneous combinations (PINCH + SWIPE) are intentionally not modeled
yet; the step schema carries a `type` field so a "combo" step can be
added later without reshaping stored data.

Compound events are emitted on the same gesture.event topic with
source="compound" (start immediately followed by end), so the rule
engine, dashboard and logs treat compounds as first-class gestures.
Events with source="compound" are ignored on input — compounds cannot
nest.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from ..core.events import EventBus
from ..core.types import CameraStatus, GestureEvent

log = logging.getLogger(__name__)


@dataclass
class _Track:
    """Runtime state for one compound definition."""
    step_index: int = 0
    started_at: float = 0.0
    last_step_at: float = 0.0
    hand_lock: str = ""
    hold_started: float | None = None
    confidences: list[float] = field(default_factory=list)

    def reset(self) -> None:
        self.step_index = 0
        self.started_at = 0.0
        self.last_step_at = 0.0
        self.hand_lock = ""
        self.hold_started = None
        self.confidences.clear()


class CompoundEngine:
    def __init__(self, bus: EventBus) -> None:
        self.bus = bus
        self.defs = []                       # list[CompoundRow]
        self.prefix: dict[str, list] = {}    # first-step gesture → defs
        self._tracks: dict[int, _Track] = {}
        self._last_fire: dict[int, float] = {}  # survives resets (cooldown)
        self.last_detected = ""              # for UI/status
        # per-primitive-event instrumentation for the arbitration layer
        self.last_advanced: list = []        # defs advanced by last event
        self.last_completed = None           # def completed by last event
        bus.subscribe("gesture.event", self.on_event)
        bus.subscribe("control.enabled", self._on_control)
        bus.subscribe("camera.status", self._on_camera_status)

    # -- configuration ------------------------------------------------------
    def set_definitions(self, rows) -> None:
        self.defs = [r for r in rows if r.enabled and r.steps]
        self.prefix = {}
        for d in self.defs:
            g = d.steps[0].get("gesture", "")
            if g:
                self.prefix.setdefault(g, []).append(d)
        self.reset()
        log.info("Compound engine loaded %d definition(s)", len(self.defs))

    def reset(self) -> None:
        for t in self._tracks.values():
            t.reset()

    def progress(self, name: str) -> tuple[int, int]:
        """(completed steps, total steps) — for the safe test UI."""
        for d in self.defs:
            if d.name == name:
                return self._track(d.id).step_index, len(d.steps)
        return 0, 0

    # -- cancellation sources -----------------------------------------------
    def _on_control(self, enabled: bool) -> None:
        if not enabled:  # motion off / emergency stop
            self.reset()

    def _on_camera_status(self, status: CameraStatus, _detail: str) -> None:
        if status in (CameraStatus.DISCONNECTED, CameraStatus.STOPPED,
                      CameraStatus.ERROR):
            self.reset()

    # -- event stream -------------------------------------------------------
    def _track(self, cid: int) -> _Track:
        if cid not in self._tracks:
            self._tracks[cid] = _Track()
        return self._tracks[cid]

    def on_event(self, ev: GestureEvent) -> None:
        if ev.source == "compound":
            return  # no nesting / no self-feedback
        self.last_advanced = []
        self.last_completed = None
        self._fires = []   # (def, confidence) — emitted after the loop so
        for d in self.defs:  # every parallel track advances first
            try:
                self._advance(d, ev)
            except Exception:
                log.exception("Compound %r state machine failed", d.name)
                self._track(d.id).reset()
        for d, conf in self._fires:
            self._fire(d, conf, ev, ev.timestamp)
        self._fires = []

    # -- arbitration queries ------------------------------------------------
    def _track_deadline(self, d, tr: _Track) -> float:
        return min(tr.last_step_at + d.step_timeout_ms / 1000.0,
                   tr.started_at + d.max_duration_ms / 1000.0)

    def continuation_deadline_for(self, gesture: str, relevant) -> float | None:
        """Latest moment an ACTIVE partial sequence could still consume
        this gesture (None if no active track expects it)."""
        deadline = None
        for d in self.defs:
            tr = self._tracks.get(d.id)
            if not tr or tr.step_index == 0 or tr.step_index >= len(d.steps):
                continue
            step = d.steps[tr.step_index]
            target = (step.get("gesture", "")
                      or d.steps[tr.step_index - 1].get("gesture", ""))
            if target != gesture or not relevant(d):
                continue
            dl = self._track_deadline(d, tr)
            if step.get("type") == "hold":
                anchor = tr.hold_started or tr.last_step_at
                dl = max(dl, anchor + int(step.get("hold_ms", 500)) / 1000.0)
            deadline = dl if deadline is None else max(deadline, dl)
        return deadline

    def defer_deadline_for(self, gesture: str, now: float,
                           relevant) -> float | None:
        """Latest moment ANY relevant compound could still turn this
        gesture into (part of) a compound. None = execute immediately."""
        deadline = self.continuation_deadline_for(gesture, relevant)
        # tracks this very event just advanced: the event was consumed as
        # a step, so its primitive action must wait for the track's window
        for d in self.last_advanced:
            if d is self.last_completed:
                continue
            tr = self._tracks.get(d.id)
            if not tr or tr.step_index == 0:
                continue
            if not relevant(d):
                continue
            dl = self._track_deadline(d, tr)
            deadline = dl if deadline is None else max(deadline, dl)
        for d in self.prefix.get(gesture, []):
            tr = self._tracks.get(d.id)
            if tr and tr.step_index > 0:
                continue  # active track handled above
            if (now - self._last_fire.get(d.id, float("-inf"))
                    < d.cooldown_ms / 1000.0):
                continue  # cannot start during its cooldown
            if not relevant(d):
                continue
            step0 = d.steps[0]
            if step0.get("type") == "hold":
                dl = now + int(step0.get("hold_ms", 500)) / 1000.0
            else:
                dl = now + d.step_timeout_ms / 1000.0
            deadline = dl if deadline is None else max(deadline, dl)
        return deadline

    def holders_for(self, gesture: str, relevant) -> set[int]:
        """Def ids whose state justifies holding this gesture's primitive
        action: tracks the event advanced, plus armed hold-step openers."""
        ids: set[int] = set()
        for d in self.last_advanced:
            if d is self.last_completed:
                continue
            tr = self._tracks.get(d.id)
            if tr and tr.step_index > 0 and relevant(d):
                ids.add(d.id)
        for d in self.prefix.get(gesture, []):
            tr = self._tracks.get(d.id)
            if (d.steps[0].get("type") == "hold" and tr
                    and tr.step_index == 0 and tr.hold_started is not None
                    and relevant(d)):
                ids.add(d.id)
        return ids

    def any_holder_alive(self, ids: set[int]) -> bool:
        for cid in ids:
            tr = self._tracks.get(cid)
            if tr and (tr.step_index > 0 or tr.hold_started is not None):
                return True
        return False

    def def_by_name(self, name: str):
        for d in self.defs:
            if d.name == name:
                return d
        return None

    def longer_rivals(self, completed, relevant) -> list[tuple]:
        """Active, still-viable compounds that extend `completed`'s step
        list — used for longest-match arbitration. Returns
        [(def, deadline)]."""
        out = []
        n = len(completed.steps)
        for d in self.defs:
            if d.id == completed.id or len(d.steps) <= n:
                continue
            if d.steps[:n] != completed.steps:
                continue
            tr = self._tracks.get(d.id)
            if not tr or tr.step_index < n:
                continue
            if not relevant(d):
                continue
            out.append((d, self._track_deadline(d, tr)))
        return out

    # -- state machine ------------------------------------------------------
    def _advance(self, d, ev: GestureEvent) -> None:
        tr = self._track(d.id)
        now = ev.timestamp

        # lazy expiry: stale partials can never complete
        if tr.step_index > 0:
            if (now - tr.last_step_at > d.step_timeout_ms / 1000.0
                    or now - tr.started_at > d.max_duration_ms / 1000.0):
                tr.reset()

        step = d.steps[tr.step_index]
        prev_gesture = (d.steps[tr.step_index - 1].get("gesture", "")
                        if tr.step_index > 0 else "")

        if not self._hand_ok(d, tr, ev):
            return

        matched = False
        stype = step.get("type", "gesture")
        target = step.get("gesture", "") or prev_gesture

        if stype == "gesture":
            matched = ev.phase == "start" and ev.gesture == target
        elif stype == "hold":
            if ev.gesture == target:
                if ev.phase == "start":
                    tr.hold_started = now
                elif ev.phase == "hold":
                    if tr.hold_started is None and prev_gesture == target:
                        # already holding since the previous step matched
                        tr.hold_started = tr.last_step_at
                    if tr.hold_started is not None:
                        matched = (now - tr.hold_started
                                   >= int(step.get("hold_ms", 500)) / 1000.0)
                elif ev.phase == "end":
                    # released before the threshold: cancel the sequence
                    if tr.hold_started is not None:
                        tr.reset()
                    return
        elif stype == "release":
            matched = ev.phase == "end" and ev.gesture == target

        # minimum gap between step completions (double-tap debounce)
        if (matched and tr.step_index > 0 and d.min_gap_ms
                and now - tr.last_step_at < d.min_gap_ms / 1000.0):
            return  # too fast — ignore, keep waiting

        if matched:
            if tr.step_index == 0:
                if (now - self._last_fire.get(d.id, float("-inf"))
                        < d.cooldown_ms / 1000.0):
                    return
                tr.started_at = now
                if d.hand == "same":
                    tr.hand_lock = ev.handedness
            tr.step_index += 1
            tr.last_step_at = now
            tr.hold_started = None
            tr.confidences.append(ev.confidence)
            self.last_advanced.append(d)
            if tr.step_index >= len(d.steps):
                self.last_completed = d
                conf = min(tr.confidences) if tr.confidences else 1.0
                self._fires.append((d, conf))
                self._last_fire[d.id] = now  # gate parallel restarts now
                tr.reset()
            return

        # strict mode: an unexpected different gesture START aborts
        if (d.strict and tr.step_index > 0 and ev.phase == "start"
                and ev.gesture not in (target, prev_gesture)):
            tr.reset()

    def _hand_ok(self, d, tr: _Track, ev: GestureEvent) -> bool:
        if d.hand == "any":
            return True
        if d.hand in ("left", "right"):
            return ev.handedness.lower() == d.hand
        if d.hand == "same":
            return not tr.hand_lock or ev.handedness == tr.hand_lock
        return True

    def _fire(self, d, conf: float, ev: GestureEvent, now: float) -> None:
        self.last_detected = d.name
        log.info("Compound gesture detected: %s (conf %.2f)", d.name, conf)
        for phase in ("start", "end"):
            self.bus.publish("gesture.event", GestureEvent(
                gesture=d.name, phase=phase, confidence=conf,
                handedness=ev.handedness, hand_x=ev.hand_x,
                hand_y=ev.hand_y, timestamp=now, source="compound"))
