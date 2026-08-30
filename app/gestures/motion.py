"""Motion gesture detection (swipes) from wrist trajectory.

Design (see also TESTING.md):

- Works on a short temporal history of normalized wrist positions —
  never a single-frame comparison.
- A swipe fires when some anchor point in the window and the newest
  point satisfy ALL of:
    distance   — net displacement >= min_distance (normalized units)
    duration   — min_duration_s <= dt <= max_duration_s
    velocity   — net displacement / dt >= min_speed (units/second)
    straight   — net displacement / path length >= straightness
                 (rejects jitter and curved motion)
    direction  — one axis dominates by `dominance`; diagonals are
                 deterministically rejected
- Pose-independent: the detector never looks at finger state.
- mirror_x: a front-facing camera sees the user's rightward motion as
  image-x decreasing. With mirror_x=True (default) directions are
  reported from the USER's perspective, matching the mirrored preview.
- One fire per motion: cooldown + history clear after a hit.
"""
from __future__ import annotations

import math
from collections import deque

from ..core.types import Hand

SWIPE_GESTURES = ["swipe_left", "swipe_right", "swipe_up", "swipe_down"]


class SwipeDetector:
    def __init__(self, min_distance: float = 0.15, min_speed: float = 0.6,
                 window_s: float = 0.5, cooldown_s: float = 0.6,
                 mirror_x: bool = True, min_duration_s: float = 0.06,
                 max_duration_s: float = 0.6, straightness: float = 0.65,
                 dominance: float = 1.5) -> None:
        self.min_distance = min_distance
        self.min_speed = min_speed
        self.window_s = window_s
        self.cooldown_s = cooldown_s
        self.mirror_x = mirror_x
        self.min_duration_s = min_duration_s
        self.max_duration_s = max_duration_s
        self.straightness = straightness
        self.dominance = dominance
        self._hist: deque[tuple[float, float, float]] = deque()  # (t, x, y)
        self._last_fire = float("-inf")

    def reset(self) -> None:
        self._hist.clear()

    def update(self, hand: Hand, ts: float) -> str | None:
        wrist = hand.landmarks[Hand.WRIST]
        self._hist.append((ts, wrist.x, wrist.y))
        cutoff = ts - self.window_s
        while self._hist and self._hist[0][0] < cutoff:
            self._hist.popleft()
        if ts - self._last_fire < self.cooldown_s:
            return None
        if len(self._hist) < 4:
            return None

        pts = list(self._hist)
        t_now, x_now, y_now = pts[-1]

        # path length from each point to the end (for straightness)
        n = len(pts)
        suffix_path = [0.0] * n
        for i in range(n - 2, -1, -1):
            seg = math.hypot(pts[i + 1][1] - pts[i][1],
                             pts[i + 1][2] - pts[i][2])
            suffix_path[i] = suffix_path[i + 1] + seg

        best: tuple[float, float, float] | None = None  # (dist, dx, dy)
        for i in range(n - 3):
            t0, x0, y0 = pts[i]
            dur = t_now - t0
            if dur < self.min_duration_s or dur > self.max_duration_s:
                continue
            dx, dy = x_now - x0, y_now - y0
            dist = math.hypot(dx, dy)
            if dist < self.min_distance:
                continue
            if dist / dur < self.min_speed:
                continue
            if suffix_path[i] <= 1e-9 or dist / suffix_path[i] < self.straightness:
                continue
            if best is None or dist > best[0]:
                best = (dist, dx, dy)

        if best is None:
            return None
        _, dx, dy = best
        if self.mirror_x:
            dx = -dx  # report from the user's perspective

        if abs(dx) > abs(dy) * self.dominance:
            name = "swipe_right" if dx > 0 else "swipe_left"
        elif abs(dy) > abs(dx) * self.dominance:
            name = "swipe_down" if dy > 0 else "swipe_up"
        else:
            return None  # diagonal — deterministically rejected

        self._last_fire = ts
        self._hist.clear()
        return name
