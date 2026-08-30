"""Application configuration.

Runtime tuning values live here (JSON file); user mappings live in SQLite.
"""
from __future__ import annotations

import dataclasses
import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

APP_NAME = "MotionGestureApp"

# Cursor-control movement sensitivity = the hand-delta -> screen-delta GAIN
# that cursor control has always used (it was a hard-coded 2.2). Exposing
# that existing parameter — NOT a second sensitivity system, and unrelated
# to Open-Palm recognition confidence.
CURSOR_SENSITIVITY_MIN = 0.5
CURSOR_SENSITIVITY_MAX = 6.0
CURSOR_SENSITIVITY_DEFAULT = 2.2     # historical gain → identical behavior


# Pinch-and-hold drag (cursor interaction, not a mapped gesture action).
# OFF by default: it holds a REAL mouse button and dedicates the pinch
# gesture to dragging, so existing installs must opt in explicitly.
DRAG_START_MS_MIN = 60
DRAG_START_MS_MAX = 1000
DRAG_START_MS_DEFAULT = 150
DRAG_RELEASE_MIN = 0.10
DRAG_RELEASE_MAX = 0.55
DRAG_RELEASE_DEFAULT = 0.35     # < the 0.6 start threshold → hysteresis


def _normalize_number(value, lo, hi, default):
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    if v != v:                        # NaN
        return default
    return max(lo, min(hi, v))


def normalize_drag_start_ms(value) -> int:
    """How long a pinch must be held before a drag starts."""
    return int(_normalize_number(value, DRAG_START_MS_MIN, DRAG_START_MS_MAX,
                                 DRAG_START_MS_DEFAULT))


def normalize_drag_release(value) -> float:
    """Relaxed pinch confidence below which an ACTIVE drag releases (lower
    = the drag survives more landmark noise before letting go)."""
    return _normalize_number(value, DRAG_RELEASE_MIN, DRAG_RELEASE_MAX,
                             DRAG_RELEASE_DEFAULT)


def normalize_cursor_sensitivity(value) -> float:
    """Clamp to the supported range. Missing/invalid (or NaN) falls back to
    the historical default, so old config files behave exactly as before."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return CURSOR_SENSITIVITY_DEFAULT
    if v != v:                        # NaN
        return CURSOR_SENSITIVITY_DEFAULT
    return max(CURSOR_SENSITIVITY_MIN, min(CURSOR_SENSITIVITY_MAX, v))


def data_dir() -> Path:
    base = os.environ.get("MGA_DATA_DIR")
    if base:
        p = Path(base)
    else:
        p = Path(os.environ.get("APPDATA", str(Path.home()))) / APP_NAME
    p.mkdir(parents=True, exist_ok=True)
    return p


@dataclass
class Config:
    camera_index: int = 0
    frame_width: int = 640
    frame_height: int = 480
    target_fps: int = 30
    mirror_preview: bool = True

    # vision
    max_hands: int = 1
    min_detection_confidence: float = 0.6
    min_tracking_confidence: float = 0.5
    landmark_smoothing: float = 0.5  # EMA alpha for smoothed value (0=off)

    # gesture engine
    gesture_confidence_threshold: float = 0.7
    debounce_frames: int = 3          # consecutive frames before "start"
    release_frames: int = 4           # frames without gesture before "end"
    default_cooldown_ms: int = 600
    swipe_min_speed: float = 0.6      # normalized units per second
    swipe_min_distance: float = 0.15  # normalized frame fraction
    swipe_window_s: float = 0.5
    swipe_mirror_x: bool = True       # report directions from user's view
    # control routing: which physical hand may drive gestures.
    # "left" | "right" | "both". Default "both" = existing behavior; any
    # missing/invalid value falls back to "both" (see core/hand_select.py).
    hand_control: str = "both"
    # cursor control: hand-movement -> cursor-movement gain (the parameter
    # cursor control already used). Higher = small hand motion moves the
    # cursor further. Applies ONLY to cursor-control movement.
    cursor_sensitivity: float = CURSOR_SENSITIVITY_DEFAULT
    # pinch-and-hold drag (thumb + index). Reuses the existing pinch
    # detector; while enabled the pinch gesture is dedicated to dragging.
    cursor_drag_enabled: bool = False
    cursor_drag_start_ms: int = DRAG_START_MS_DEFAULT
    cursor_drag_release: float = DRAG_RELEASE_DEFAULT

    # context
    context_poll_ms: int = 200

    # safety
    motion_control_enabled: bool = False   # start disarmed
    emergency_gesture: str = "open_palm_hold"
    action_global_cooldown_ms: int = 150
    # workflow loops: user-facing repeat bound (hard ceiling is 100 —
    # the setting can only lower it, never raise it)
    workflow_max_repeat: int = 100
    # when on, a gesture that resolves to a workflow flagged
    # "requires confirmation" prompts before running (Emergency Stop
    # always overrides). Off = flagged workflows still run normally.
    confirm_dangerous_workflows: bool = False
    # Gesture Studio safety: require the hand to return to a neutral
    # state before the SAME discrete gesture (circle / swipe / recorded
    # motion) can fire again — prevents a held pose re-triggering a
    # workflow repeatedly. Off = existing cooldown-only behavior.
    require_neutral_before_retrigger: bool = False

    # arming / disarming safety gate (control-layer, over the existing
    # pipeline — never a second recognizer). OFF by default so existing
    # users are not unexpectedly locked out; when ON the app starts
    # DISARMED and the user must perform the arming gesture. The arming/
    # disarm gestures are CONSUMED by the gate and never run their mapping.
    arming_enabled: bool = False
    arming_gesture: str = ""            # existing gesture name (any kind)
    disarm_gesture: str = ""            # optional; empty = no gesture disarm
    arm_hold_ms: int = 0               # 0 = arm on completion; >0 = hold
    disarm_on_motion_off: bool = True   # motion disabled → disarm
    disarm_on_camera_disconnect: bool = True  # camera lost → disarm
    # (Emergency Stop ALWAYS disarms — not configurable.)

    # ui
    start_minimized: bool = False
    minimize_to_tray: bool = True

    @classmethod
    def path(cls) -> Path:
        return data_dir() / "config.json"

    @classmethod
    def load(cls) -> "Config":
        p = cls.path()
        cfg = cls()
        if p.exists():
            try:
                # utf-8-sig: tolerate a BOM in hand-edited config files
                raw = json.loads(p.read_text(encoding="utf-8-sig"))
                known = {f.name for f in dataclasses.fields(cls)}
                for k, v in raw.items():
                    if k in known:
                        setattr(cfg, k, v)
            except Exception:
                log.exception("Failed to load config; using defaults")
        return cfg

    def save(self) -> None:
        self.path().write_text(
            json.dumps(dataclasses.asdict(self), indent=2), encoding="utf-8")
