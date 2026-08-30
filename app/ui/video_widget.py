"""Live camera preview with hand-landmark overlay."""
from __future__ import annotations

import numpy as np
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QImage, QPainter, QPen, QColor, QPixmap
from PySide6.QtWidgets import QLabel

from ..core.types import HandFrame

# MediaPipe hand topology connections
_CONNECTIONS = [
    (0, 1), (1, 2), (2, 3), (3, 4),
    (0, 5), (5, 6), (6, 7), (7, 8),
    (5, 9), (9, 10), (10, 11), (11, 12),
    (9, 13), (13, 14), (14, 15), (15, 16),
    (13, 17), (17, 18), (18, 19), (19, 20), (0, 17),
]


class VideoWidget(QLabel):
    def __init__(self, mirror: bool = True) -> None:
        super().__init__()
        self.setMinimumSize(480, 360)
        self.setAlignment(Qt.AlignCenter)
        self.setStyleSheet(
            "background-color: #101418; color: #8899aa; border-radius: 6px;")
        self.setText("Camera preview")
        self.mirror = mirror
        self._frame: np.ndarray | None = None
        self._hands: HandFrame | None = None
        self._trail: list = []
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._render)
        self._timer.start(33)

    def on_frame(self, frame: np.ndarray, _ts: float) -> None:
        self._frame = frame  # reference only; render copies

    def on_hands(self, hf: HandFrame) -> None:
        self._hands = hf

    def on_trajectory(self, points: list, _ts: float) -> None:
        """Fingertip trail while a shape candidate is forming ([] clears)."""
        self._trail = points

    def clear_feed(self) -> None:
        self._frame = None
        self._hands = None
        self._trail = []
        self.setText("Camera disconnected")

    def _render(self) -> None:
        if self._frame is None or not self.isVisible():
            return
        frame = self._frame
        h, w = frame.shape[:2]
        rgb = np.ascontiguousarray(frame[:, ::-1, ::-1] if self.mirror
                                   else frame[:, :, ::-1])
        img = QImage(rgb.data, w, h, 3 * w, QImage.Format_RGB888).copy()

        if self._hands and self._hands.hands:
            p = QPainter(img)
            p.setRenderHint(QPainter.Antialiasing)
            for hand in self._hands.hands:
                pts = []
                for lm in hand.landmarks:
                    x = (1.0 - lm.x) * w if self.mirror else lm.x * w
                    pts.append((x, lm.y * h))
                p.setPen(QPen(QColor(0, 220, 130), 2))
                for a, b in _CONNECTIONS:
                    p.drawLine(int(pts[a][0]), int(pts[a][1]),
                               int(pts[b][0]), int(pts[b][1]))
                p.setPen(QPen(QColor(255, 255, 255), 1))
                for x, y in pts:
                    p.drawEllipse(int(x) - 2, int(y) - 2, 4, 4)
            p.end()

        if self._trail and len(self._trail) >= 2:
            p = QPainter(img)
            p.setRenderHint(QPainter.Antialiasing)
            p.setPen(QPen(QColor(255, 196, 64, 200), 2))
            last = None
            for (tx, ty) in self._trail:
                x = (1.0 - tx) * w if self.mirror else tx * w
                y = ty * h
                if last is not None:
                    p.drawLine(int(last[0]), int(last[1]), int(x), int(y))
                last = (x, y)
            p.end()

        pix = QPixmap.fromImage(img).scaled(
            self.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.setPixmap(pix)
