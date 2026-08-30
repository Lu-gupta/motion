"""Thread-safe publish/subscribe event bus.

Subsystems communicate through topics instead of direct references:

    camera.frame        (ndarray frame, float ts)
    camera.status       (CameraStatus, str detail)
    vision.hands        (HandFrame)
    gesture.event       (GestureEvent)
    context.changed     (Context)
    rule.matched        (GestureEvent, Context, RuleMatch)
    rule.unmatched      (GestureEvent, Context)
    action.executed     (ActionSpec, bool ok, str detail)
    control.enabled     (bool)
"""
from __future__ import annotations

import logging
import threading
from collections import defaultdict
from typing import Callable

log = logging.getLogger(__name__)


class EventBus:
    def __init__(self) -> None:
        self._subs: dict[str, list[Callable]] = defaultdict(list)
        self._lock = threading.RLock()

    def subscribe(self, topic: str, handler: Callable) -> None:
        with self._lock:
            self._subs[topic].append(handler)

    def unsubscribe(self, topic: str, handler: Callable) -> None:
        with self._lock:
            if handler in self._subs.get(topic, []):
                self._subs[topic].remove(handler)

    def publish(self, topic: str, *args, **kwargs) -> None:
        with self._lock:
            handlers = list(self._subs.get(topic, []))
        for h in handlers:
            try:
                h(*args, **kwargs)
            except Exception:
                log.exception("Event handler %s failed for topic %s", h, topic)
