"""Gesture arbitration — primitive vs compound conflict resolution.

Sits between the compound engine and action dispatch:

    Primitive Engine → Compound Engine → **Arbiter** → Rules → Actions

Rules of arbitration (documented behavior):

1. A primitive whose gesture is NOT a possible prefix/continuation of a
   context-relevant enabled compound executes immediately — zero added
   latency, zero overhead beyond one dict lookup.
2. A primitive that could begin or continue a relevant compound has its
   resolved action HELD (the rule is resolved at gesture time, in the
   context where the gesture happened).
3. If a compound completes, all held primitive actions are cancelled and
   exactly one compound action runs. The event that completed a compound
   never also runs its primitive mapping.
4. If no continuation arrives, the held primitive action executes when
   every possibility has expired (deadline = the latest applicable
   step-timeout / hold window + a small slack). Deterministic.
5. Longest match: when a compound completes while a longer compound that
   extends it is still viable, the shorter compound's action is held
   until the longer one completes (shorter cancelled) or expires
   (shorter executes). Equal-length compounds fire as the compound
   engine detects them.
6. Wrong-gesture policy (lenient compounds): an unrelated primitive
   executes immediately and does not disturb held primitives — they
   still release at their own deadlines.
7. Cancellation: motion-off / emergency stop and camera disconnect drop
   ALL held actions (nothing stale ever executes); a held action also
   re-checks motion state at release time.
8. Early release: when a pending primitive's gesture ENDS and no active
   partial sequence can still consume it (e.g. an aborted hold), it
   releases immediately instead of waiting for the full window.

Context awareness: a compound is "relevant" only if the rule engine
resolves its name in the context captured at gesture time — a
Chrome-only compound never delays gestures on the Desktop.

Everything is event-driven; timers are single-shot and only exist while
something is actually pending.
"""
from __future__ import annotations

import logging
import threading
import time

from ..core.events import EventBus
from ..core.types import CameraStatus, Context, GestureEvent, RuleMatch

log = logging.getLogger(__name__)

SLACK_S = 0.06  # timer slack over engine deadlines (lazy-expiry race)


class _Pending:
    __slots__ = ("ev", "ctx", "match", "deadline", "timer", "cancelled",
                 "holders")

    def __init__(self, ev, ctx, match, deadline, holders=frozenset()):
        self.ev = ev
        self.ctx = ctx
        self.match = match
        self.deadline = deadline
        self.timer: threading.Timer | None = None
        self.cancelled = False
        self.holders = holders   # compound def ids justifying the hold


class GestureArbiter:
    def __init__(self, bus: EventBus, compounds, resolver, executor) -> None:
        """resolver(gesture, ctx) -> RuleMatch | None
        executor(match, ev) -> None  (must re-check motion state)"""
        self.bus = bus
        self.compounds = compounds
        self.resolver = resolver
        self.executor = executor
        self._lock = threading.RLock()
        self._pending: dict[int, _Pending] = {}      # primitives
        self._pending_compounds: dict[int, _Pending] = {}
        self._next_id = 0
        bus.subscribe("control.enabled", self._on_control)
        bus.subscribe("camera.status", self._on_camera_status)

    # -- decisions ----------------------------------------------------------
    def on_primitive_start(self, ev: GestureEvent, ctx: Context) -> str:
        """Returns 'suppressed' | 'deferred' | 'immediate'."""
        with self._lock:
            ce = self.compounds
            if ce.last_completed is not None:
                # this very event completed a compound — never double-act
                return "suppressed"
            relevant = self._relevance(ctx)
            deadline = ce.defer_deadline_for(ev.gesture, ev.timestamp,
                                             relevant)
            if deadline is None:
                return "immediate"
            match = self.resolver(ev.gesture, ctx)
            if match is None:
                return "immediate"  # nothing to hold; compound proceeds
            if match.continuous or match.action.type == "cursor_control":
                return "immediate"  # holding a continuous action breaks UX
            holders = ce.holders_for(ev.gesture, relevant)
            self._pend(self._pending, ev, ctx, match, deadline + SLACK_S,
                       holders)
            log.debug("Arbiter holding %s for %.0f ms", ev.gesture,
                      (deadline + SLACK_S - ev.timestamp) * 1000)
            self.bus.publish("arbiter.pending", ev, deadline + SLACK_S)
            return "deferred"

    def on_compound_start(self, ev: GestureEvent, ctx: Context) -> str:
        """Returns 'deferred' | 'immediate'."""
        with self._lock:
            # a compound completed: its component primitives must not act
            self._cancel_all(self._pending)
            cdef = self.compounds.def_by_name(ev.gesture)
            # a longer compound superseded any held shorter one it extends
            if cdef is not None:
                for pid, p in list(self._pending_compounds.items()):
                    shorter = self.compounds.def_by_name(p.ev.gesture)
                    if (shorter is not None
                            and cdef.steps[:len(shorter.steps)]
                            == shorter.steps):
                        self._cancel(self._pending_compounds, pid)
            match = self.resolver(ev.gesture, ctx)
            if match is None or cdef is None:
                return "immediate"  # unmatched → controller logs it
            rivals = self.compounds.longer_rivals(cdef, self._relevance(ctx))
            if not rivals:
                return "immediate"
            deadline = max(dl for _, dl in rivals) + SLACK_S
            self._pend(self._pending_compounds, ev, ctx, match, deadline)
            log.debug("Arbiter holding compound %s (longer match possible)",
                      ev.gesture)
            self.bus.publish("arbiter.pending", ev, deadline)
            return "deferred"

    def on_primitive_end(self, ev: GestureEvent) -> None:
        """Early release: if every compound track that justified the hold
        is dead (e.g. an aborted hold step), don't wait the full window."""
        with self._lock:
            for pid, p in list(self._pending.items()):
                if p.ev.gesture != ev.gesture:
                    continue
                if not self.compounds.any_holder_alive(p.holders):
                    self._release(self._pending, pid)

    # -- internals ----------------------------------------------------------
    def _relevance(self, ctx: Context):
        return lambda d: self.resolver(d.name, ctx) is not None

    def _pend(self, store: dict, ev, ctx, match: RuleMatch,
              deadline: float, holders=frozenset()) -> None:
        self._next_id += 1
        pid = self._next_id
        p = _Pending(ev, ctx, match, deadline, holders)
        store[pid] = p
        delay = max(0.0, deadline - time.monotonic())
        p.timer = threading.Timer(delay,
                                  lambda: self._release(store, pid))
        p.timer.daemon = True
        p.timer.start()

    def _release(self, store: dict, pid: int) -> None:
        with self._lock:
            p = store.pop(pid, None)
            if p is None or p.cancelled:
                return
            if p.timer:
                p.timer.cancel()
        log.debug("Arbiter releasing held %s", p.ev.gesture)
        try:
            self.executor(p.match, p.ev, p.ctx)
        except Exception:
            log.exception("Arbiter release failed for %s", p.ev.gesture)

    def _cancel(self, store: dict, pid: int) -> None:
        p = store.pop(pid, None)
        if p is not None:
            p.cancelled = True
            if p.timer:
                p.timer.cancel()

    def _cancel_all(self, store: dict) -> None:
        for pid in list(store):
            self._cancel(store, pid)

    def reset(self) -> None:
        with self._lock:
            self._cancel_all(self._pending)
            self._cancel_all(self._pending_compounds)

    def pending_count(self) -> int:
        with self._lock:
            return len(self._pending) + len(self._pending_compounds)

    # -- cancellation sources -----------------------------------------------
    def _on_control(self, enabled: bool) -> None:
        if not enabled:
            self.reset()

    def _on_camera_status(self, status: CameraStatus, _detail: str) -> None:
        if status in (CameraStatus.DISCONNECTED, CameraStatus.STOPPED,
                      CameraStatus.ERROR):
            self.reset()
