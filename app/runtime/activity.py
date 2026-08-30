"""Bounded recent-gesture-activity log for the Gesture Command Center.

Observes existing bus events (rule matches + workflow outcomes) and keeps
only the last N entries in memory — no database, no unbounded log, no
extra work on the camera/gesture threads. Publishes `activity.changed`
so the UI can refresh through the normal bridge.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass

from ..core.events import EventBus


@dataclass
class ActivityEntry:
    ts: float               # wall-clock epoch seconds (for display)
    gesture: str
    profile: str
    target: str             # action / workflow name
    status: str             # "matched" | "completed" | "failed" | "cancelled"
    detail: str = ""


class ActivityLog:
    MAX = 50

    def __init__(self, bus: EventBus, clock=time.time) -> None:
        self.bus = bus
        self._clock = clock
        self._lock = threading.Lock()
        self._entries: deque[ActivityEntry] = deque(maxlen=self.MAX)
        # name → the gesture/profile that most recently launched it, so a
        # workflow.done can be attributed to the gesture that started it
        self._pending: dict[str, tuple[str, str]] = {}
        bus.subscribe("rule.matched", self._on_matched)
        bus.subscribe("workflow.done", self._on_workflow_done)

    # -- event handlers (worker threads) ------------------------------------
    def _on_matched(self, ev, ctx, match) -> None:
        target = match.action.name or match.action.type
        is_workflow = match.action.type == "workflow"
        with self._lock:
            if is_workflow:
                # a workflow.done will finalize this; remember its origin
                self._pending[target] = (ev.gesture, match.profile_name)
            self._entries.appendleft(ActivityEntry(
                ts=self._clock(), gesture=ev.gesture,
                profile=match.profile_name, target=target,
                status="running" if is_workflow else "completed"))
        self.bus.publish("activity.changed")

    def _on_workflow_done(self, name: str, status: str, detail: str) -> None:
        with self._lock:
            origin = self._pending.pop(name, None)
            # update the most recent running entry for this workflow
            for e in self._entries:
                if e.target == name and e.status == "running":
                    e.status = status
                    e.detail = detail
                    break
            else:
                # a Test-run or gesture we didn't record the start of
                g, p = origin or ("workflow", "—")
                self._entries.appendleft(ActivityEntry(
                    ts=self._clock(), gesture=g, profile=p, target=name,
                    status=status, detail=detail))
        self.bus.publish("activity.changed")

    # -- read (GUI thread) --------------------------------------------------
    def entries(self) -> list[ActivityEntry]:
        with self._lock:
            return list(self._entries)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._pending.clear()
        self.bus.publish("activity.changed")
