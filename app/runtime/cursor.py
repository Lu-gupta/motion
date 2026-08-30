"""Cursor output boundary: stabilization + pinch-and-hold drag.

This is the ONE place that moves the OS cursor for cursor control and the
ONE place that holds the left mouse button for dragging. It is not a
gesture engine and not a detector: it consumes positions and an existing
pinch confidence (`gestures.static.pinch_confidence`) that the controller
feeds it, after the existing hand-selection and safety gates have already
run.

Three small pieces:

- `CursorFilter` — adaptive low-pass on the OUTPUT (screen pixels), plus a
  micro-movement deadzone and one-frame spike rejection. It deliberately
  filters the cursor target, NOT landmarks: the tracker already runs an
  EMA over landmarks (`LandmarkSmoother`), and filtering twice in the same
  domain would add lag for no gain.
- `DragMachine` — IDLE → CANDIDATE → DRAGGING state machine with
  hysteresis, guaranteeing every `button_down` has exactly one matching
  `button_up`.
- `CursorController` — anchor-relative hand→screen mapping (the mapping
  cursor control has always used) wired through the filter, plus the drag
  machine.

Adaptive smoothing, in one sentence: the further the cursor target moved
this frame, the less it is smoothed — a stationary hand gets heavy
smoothing (jitter disappears), a fast hand gets almost none (no lag).
"""
from __future__ import annotations

import logging
import math
from enum import Enum

from ..actions import input_win as iw

log = logging.getLogger(__name__)

# -- stabilization tuning (internal; deliberately not user-facing) ----------
DEADZONE_PX = 4.0        # target moves below this do not move the cursor
MIN_ALPHA = 0.15         # stationary/slow → heavy smoothing
MAX_ALPHA = 0.90         # fast → nearly raw responsiveness
SLOW_PX = 4.0            # at/below this distance, MIN_ALPHA applies
FAST_PX = 80.0           # at/above this distance, MAX_ALPHA applies
SPIKE_PX = 700.0         # one-frame jump treated as a tracking spike


class CursorFilter:
    """Adaptive EMA + deadzone + single-frame spike rejection.

    `filter()` returns the pixel the cursor should move to, or None when it
    should not move at all this frame (deadzone / rejected spike).
    """

    def __init__(self, deadzone_px: float = DEADZONE_PX,
                 min_alpha: float = MIN_ALPHA, max_alpha: float = MAX_ALPHA,
                 slow_px: float = SLOW_PX, fast_px: float = FAST_PX,
                 spike_px: float = SPIKE_PX) -> None:
        self.deadzone_px = deadzone_px
        self.min_alpha = min_alpha
        self.max_alpha = max_alpha
        self.slow_px = slow_px
        self.fast_px = fast_px
        self.spike_px = spike_px
        self._x: float | None = None
        self._y: float | None = None
        self._spike_held = False

    def reset(self) -> None:
        self._x = self._y = None
        self._spike_held = False

    def alpha_for(self, dist: float) -> float:
        """Smoothing factor for a per-frame target distance (1.0 = raw)."""
        if dist <= self.slow_px:
            return self.min_alpha
        if dist >= self.fast_px:
            return self.max_alpha
        t = (dist - self.slow_px) / max(1e-9, self.fast_px - self.slow_px)
        return self.min_alpha + t * (self.max_alpha - self.min_alpha)

    def filter(self, tx: float, ty: float) -> tuple[int, int] | None:
        if self._x is None:
            self._x, self._y = float(tx), float(ty)
            return int(round(tx)), int(round(ty))
        dx, dy = tx - self._x, ty - self._y
        dist = math.hypot(dx, dy)

        # isolated tracking spike: skip ONE frame, then accept so the
        # cursor can never be permanently frozen by a bad estimate
        if dist > self.spike_px:
            if not self._spike_held:
                self._spike_held = True
                return None
            self._x, self._y = float(tx), float(ty)
            self._spike_held = False
            return int(round(tx)), int(round(ty))
        self._spike_held = False

        if dist < self.deadzone_px:
            return None                      # landmark noise — stay put

        a = self.alpha_for(dist)
        nx, ny = self._x + a * dx, self._y + a * dy
        if math.hypot(nx - self._x, ny - self._y) < 1.0:
            return None                      # sub-pixel step
        self._x, self._y = nx, ny
        return int(round(nx)), int(round(ny))


class DragState(str, Enum):
    IDLE = "IDLE"
    CANDIDATE = "CANDIDATE"
    DRAGGING = "DRAGGING"


class DragMachine:
    """Pinch-and-hold drag. Stateful by design — never a repeating one-shot.

    Hysteresis: starting needs `start_conf` held for `start_delay_s`;
    releasing uses a lower `release_conf` sustained for `release_frames`,
    so landmark noise can neither start nor cancel a drag. The `_down`
    flag makes press/release strictly paired.
    """

    START_CONF = 0.6

    def __init__(self, button: str = "left", start_conf: float = START_CONF,
                 release_conf: float = 0.35, start_delay_s: float = 0.15,
                 release_frames: int = 2, backend=None) -> None:
        self.button = button
        self.start_conf = start_conf
        self.release_conf = release_conf
        self.start_delay_s = start_delay_s
        self.release_frames = release_frames
        self.backend = backend or iw
        self.state = DragState.IDLE
        self._down = False
        self._t0 = 0.0
        self._below = 0

    @property
    def dragging(self) -> bool:
        return self.state == DragState.DRAGGING

    @property
    def button_held(self) -> bool:
        return self._down

    def update(self, pinch_conf: float, ts: float, allowed: bool) -> str:
        """Feed one frame. Returns "start", "end" or "" (no transition)."""
        if not allowed:
            return "end" if self.abort("not allowed") else ""
        if self.state is DragState.IDLE:
            if pinch_conf >= self.start_conf:
                self.state = DragState.CANDIDATE
                self._t0 = ts
        elif self.state is DragState.CANDIDATE:
            if pinch_conf < self.start_conf:
                self.state = DragState.IDLE
            elif ts - self._t0 >= self.start_delay_s:
                self._press()
                return "start"
        elif self.state is DragState.DRAGGING:
            if pinch_conf < self.release_conf:
                self._below += 1
                if self._below >= self.release_frames:
                    self._release()
                    return "end"
            else:
                self._below = 0
        return ""

    def abort(self, reason: str = "") -> bool:
        """Cancel any drag and ALWAYS release a held button. Idempotent —
        returns True only when a real drag was interrupted."""
        was = self._down
        if was:
            log.info("Drag aborted (%s) — releasing mouse button", reason)
        self._release()
        self.state = DragState.IDLE
        self._below = 0
        return was

    # -- button pairing -----------------------------------------------------
    def _press(self) -> None:
        if not self._down:
            self.backend.button_down(self.button)
            self._down = True
            self.state = DragState.DRAGGING
            self._below = 0
            log.info("Drag started (mouse %s down)", self.button)

    def _release(self) -> None:
        if self._down:
            self.backend.button_up(self.button)
            self._down = False
            log.info("Drag ended (mouse %s up)", self.button)
        self.state = DragState.IDLE


class CursorController:
    """Anchor-relative hand→cursor mapping + stabilization + drag.

    The mapping is the one cursor control has always used: the first frame
    anchors (hand position, current mouse position) and later frames apply
    the hand delta scaled by the sensitivity gain. Only the OUTPUT changed
    — it now goes through `CursorFilter`.
    """

    def __init__(self, backend=None, drag: DragMachine | None = None) -> None:
        self.backend = backend or iw
        self.filter = CursorFilter()
        self.drag = drag or DragMachine(backend=self.backend)
        self._anchor: tuple[float, float] | None = None
        self._pos_anchor: tuple[int, int] | None = None

    @property
    def anchored(self) -> bool:
        return self._anchor is not None

    def reset(self) -> None:
        """Drop the movement session (anchor + smoothing). Does NOT touch
        the drag button state — use `abort()` for that."""
        self._anchor = None
        self._pos_anchor = None
        self.filter.reset()

    def abort(self, reason: str = "") -> bool:
        """Full stop: release a held drag button and drop the session."""
        released = self.drag.abort(reason)
        self.reset()
        return released

    def move(self, hx: float, hy: float, gain: float,
             screen: tuple[int, int]) -> None:
        """Feed one hand position (normalized wrist coords)."""
        if self._anchor is None:
            self._anchor = (hx, hy)
            self._pos_anchor = self.backend.cursor_pos()
            self.filter.reset()
            return
        ax, ay = self._anchor
        px, py = self._pos_anchor
        w, h = screen
        # camera image is mirrored for the user: invert x (unchanged)
        tx = px + (ax - hx) * gain * w
        ty = py + (hy - ay) * gain * h
        out = self.filter.filter(tx, ty)
        if out is not None:
            self.backend.move_to(out[0], out[1])
