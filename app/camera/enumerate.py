"""Camera discovery."""
from __future__ import annotations

import logging
from dataclasses import dataclass

import cv2

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class CameraInfo:
    index: int
    name: str
    width: int
    height: int


def list_cameras(max_probe: int = 5) -> list[CameraInfo]:
    """Probe camera indices. DirectShow backend on Windows (fast open)."""
    found: list[CameraInfo] = []
    for i in range(max_probe):
        cap = cv2.VideoCapture(i, cv2.CAP_DSHOW)
        try:
            if cap.isOpened():
                ok, _ = cap.read()
                if ok:
                    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                    found.append(CameraInfo(i, f"Camera {i}", w, h))
        finally:
            cap.release()
    log.info("Camera probe found %d device(s)", len(found))
    return found
