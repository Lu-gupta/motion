"""Hand tracking abstraction.

Wraps MediaPipe Tasks HandLandmarker. MediaPipe types stop here:
output is the internal `HandFrame` published on `vision.hands`.
"""
from __future__ import annotations

import logging
import threading
import time

import cv2
import numpy as np

from ..core.events import EventBus
from ..core.types import Hand, HandFrame, Landmark
from .model_download import ensure_model

log = logging.getLogger(__name__)


class LandmarkSmoother:
    """Exponential moving average over landmark positions."""

    def __init__(self, alpha: float = 0.5) -> None:
        self.alpha = alpha
        self._prev: dict[str, np.ndarray] = {}

    def smooth(self, key: str, pts: np.ndarray) -> np.ndarray:
        if self.alpha <= 0:
            return pts
        prev = self._prev.get(key)
        if prev is None or prev.shape != pts.shape:
            self._prev[key] = pts
            return pts
        out = self.alpha * prev + (1.0 - self.alpha) * pts
        self._prev[key] = out
        return out

    def reset(self) -> None:
        self._prev.clear()


class HandTracker:
    """Consumes camera.frame events; publishes vision.hands (HandFrame)."""

    def __init__(self, bus: EventBus, max_hands: int = 1,
                 min_detection_confidence: float = 0.6,
                 min_tracking_confidence: float = 0.5,
                 smoothing_alpha: float = 0.5) -> None:
        self.bus = bus
        self.max_hands = max_hands
        self.min_det = min_detection_confidence
        self.min_track = min_tracking_confidence
        self.smoother = LandmarkSmoother(smoothing_alpha)
        self._landmarker = None
        self._lock = threading.Lock()
        self._t0 = time.monotonic()
        self._last_ts_ms = -1
        self.process_ms = 0.0  # last processing latency, for perf page
        self.started = False

    def start(self) -> None:
        if self.started:
            return
        import mediapipe as mp
        from mediapipe.tasks import python as mp_python
        from mediapipe.tasks.python import vision as mp_vision

        model = ensure_model()
        options = mp_vision.HandLandmarkerOptions(
            base_options=mp_python.BaseOptions(model_asset_path=str(model)),
            running_mode=mp_vision.RunningMode.VIDEO,
            num_hands=self.max_hands,
            min_hand_detection_confidence=self.min_det,
            min_tracking_confidence=self.min_track,
        )
        self._landmarker = mp_vision.HandLandmarker.create_from_options(
            options)
        self._mp = mp
        self.bus.subscribe("camera.frame", self._on_frame)
        self.started = True
        log.info("Hand tracker started")

    def stop(self) -> None:
        if not self.started:
            return
        self.bus.unsubscribe("camera.frame", self._on_frame)
        with self._lock:
            if self._landmarker:
                self._landmarker.close()
                self._landmarker = None
        self.smoother.reset()
        self.started = False
        log.info("Hand tracker stopped")

    # -- internals ----------------------------------------------------------
    def _on_frame(self, frame: np.ndarray, ts: float) -> None:
        with self._lock:
            if self._landmarker is None:
                return
            t_start = time.perf_counter()
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            mp_image = self._mp.Image(
                image_format=self._mp.ImageFormat.SRGB, data=rgb)
            ts_ms = int((ts - self._t0) * 1000)
            if ts_ms <= self._last_ts_ms:  # VIDEO mode needs monotonic ts
                ts_ms = self._last_ts_ms + 1
            self._last_ts_ms = ts_ms
            try:
                result = self._landmarker.detect_for_video(mp_image, ts_ms)
            except Exception:
                log.exception("Hand landmark detection failed")
                return
            self.process_ms = (time.perf_counter() - t_start) * 1000

        hands: list[Hand] = []
        h, w = frame.shape[:2]
        for i, lms in enumerate(result.hand_landmarks):
            pts = np.array([[lm.x, lm.y, lm.z] for lm in lms],
                           dtype=np.float32)
            handedness = "Unknown"
            confidence = 1.0
            if result.handedness and i < len(result.handedness):
                cat = result.handedness[i][0]
                handedness = cat.category_name or "Unknown"
                confidence = float(cat.score)
            pts = self.smoother.smooth(f"{handedness}_{i}", pts)
            hands.append(Hand(
                landmarks=[Landmark(float(p[0]), float(p[1]), float(p[2]))
                           for p in pts],
                handedness=handedness,
                confidence=confidence,
            ))
        if not hands:
            self.smoother.reset()
        self.bus.publish("vision.hands",
                         HandFrame(hands=hands, timestamp=ts,
                                   frame_width=w, frame_height=h))
