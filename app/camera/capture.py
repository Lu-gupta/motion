"""Camera capture worker.

Runs a daemon thread; publishes:
    camera.frame  (ndarray BGR frame, float monotonic ts)
    camera.status (CameraStatus, str detail)

Reconnects automatically after disconnection with backoff.
The frame ndarray is passed by reference — consumers must not mutate it.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Optional

import cv2

from ..core.events import EventBus
from ..core.types import CameraStatus

log = logging.getLogger(__name__)

# Opener signature: (index, width, height) -> cv2.VideoCapture-like object.
# Injectable for tests.
def _default_opener(index: int, width: int, height: int):
    cap = cv2.VideoCapture(index, cv2.CAP_DSHOW)
    if cap.isOpened():
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    return cap


class CameraWorker:
    RECONNECT_DELAYS = [0.5, 1.0, 2.0, 3.0, 5.0]

    def __init__(self, bus: EventBus, index: int = 0, width: int = 640,
                 height: int = 480, target_fps: int = 30,
                 opener: Callable = _default_opener) -> None:
        self.bus = bus
        self.index = index
        self.width = width
        self.height = height
        self.frame_interval = 1.0 / max(1, target_fps)
        self._opener = opener
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self.status = CameraStatus.STOPPED
        self.fps = 0.0

    # -- public API ---------------------------------------------------------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="camera",
                                        daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3.0)
            self._thread = None
        self._set_status(CameraStatus.STOPPED, "stopped")

    def set_camera(self, index: int) -> None:
        """Switch device; restarts worker if running."""
        running = self._thread is not None and self._thread.is_alive()
        if running:
            self.stop()
        self.index = index
        if running:
            self.start()

    # -- internals ----------------------------------------------------------
    def _set_status(self, status: CameraStatus, detail: str = "") -> None:
        if status != self.status:
            self.status = status
            log.info("Camera status: %s %s", status.value, detail)
            self.bus.publish("camera.status", status, detail)

    def _run(self) -> None:
        reconnect_i = 0
        while not self._stop.is_set():
            self._set_status(CameraStatus.STARTING, f"opening {self.index}")
            cap = self._opener(self.index, self.width, self.height)
            if not cap or not cap.isOpened():
                if cap:
                    cap.release()
                self._set_status(CameraStatus.DISCONNECTED,
                                 f"cannot open camera {self.index}")
                delay = self.RECONNECT_DELAYS[
                    min(reconnect_i, len(self.RECONNECT_DELAYS) - 1)]
                reconnect_i += 1
                if self._stop.wait(delay):
                    break
                continue

            reconnect_i = 0
            self._set_status(CameraStatus.CONNECTED, f"camera {self.index}")
            fail_count = 0
            frames = 0
            fps_t0 = time.monotonic()
            try:
                while not self._stop.is_set():
                    t0 = time.monotonic()
                    ok, frame = cap.read()
                    if not ok or frame is None:
                        fail_count += 1
                        if fail_count >= 10:
                            self._set_status(CameraStatus.DISCONNECTED,
                                             "read failures")
                            break
                        time.sleep(0.05)
                        continue
                    fail_count = 0
                    ts = time.monotonic()
                    self.bus.publish("camera.frame", frame, ts)
                    frames += 1
                    if ts - fps_t0 >= 1.0:
                        self.fps = frames / (ts - fps_t0)
                        frames = 0
                        fps_t0 = ts
                    # pace to target fps
                    elapsed = time.monotonic() - t0
                    sleep = self.frame_interval - elapsed
                    if sleep > 0.001:
                        self._stop.wait(sleep)
            except Exception:
                log.exception("Camera loop crashed; will attempt recovery")
                self._set_status(CameraStatus.ERROR, "loop exception")
            finally:
                cap.release()
        # exit
        if self._stop.is_set():
            self._set_status(CameraStatus.STOPPED, "stopped")
