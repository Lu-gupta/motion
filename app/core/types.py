"""Shared internal data types.

These types form the boundaries between subsystems. No third-party
(MediaPipe/OpenCV/Qt) types may appear here.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class CameraStatus(Enum):
    STOPPED = "stopped"
    STARTING = "starting"
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    ERROR = "error"


@dataclass(frozen=True)
class Landmark:
    x: float  # normalized [0..1] in image space
    y: float
    z: float  # relative depth


@dataclass
class Hand:
    """Internal hand representation. 21 landmarks, MediaPipe topology."""
    landmarks: list[Landmark]
    handedness: str  # "Left" | "Right" | "Unknown"
    confidence: float

    WRIST = 0
    THUMB_TIP = 4
    INDEX_MCP = 5
    INDEX_PIP = 6
    INDEX_TIP = 8
    MIDDLE_MCP = 9
    MIDDLE_PIP = 10
    MIDDLE_TIP = 12
    RING_MCP = 13
    RING_PIP = 14
    RING_TIP = 16
    PINKY_MCP = 17
    PINKY_PIP = 18
    PINKY_TIP = 20
    THUMB_IP = 3
    THUMB_MCP = 2

    def palm_size(self) -> float:
        """Reference scale: wrist to middle-finger MCP distance."""
        w = self.landmarks[self.WRIST]
        m = self.landmarks[self.MIDDLE_MCP]
        return max(1e-6, ((w.x - m.x) ** 2 + (w.y - m.y) ** 2) ** 0.5)


@dataclass
class HandFrame:
    """Result of vision processing for one camera frame."""
    hands: list[Hand]
    timestamp: float
    frame_width: int
    frame_height: int


@dataclass(frozen=True)
class GestureEvent:
    """Emitted by the gesture engines. Never executes anything itself."""
    gesture: str            # e.g. "pinch", "swipe_left", custom/compound name
    phase: str              # "start" | "hold" | "end"
    confidence: float
    handedness: str
    hand_x: float           # normalized wrist position at detection
    hand_y: float
    timestamp: float = field(default_factory=time.monotonic)
    source: str = "primitive"   # "primitive" | "compound"


@dataclass(frozen=True)
class Context:
    """Normalized active-context snapshot."""
    application: str = ""     # friendly name, e.g. "chrome"
    process: str = ""         # executable name lowercased, e.g. "chrome.exe"
    window_title: str = ""
    cursor_x: int = 0
    cursor_y: int = 0
    screen: int = 0

    @staticmethod
    def desktop() -> "Context":
        return Context(application="desktop", process="explorer.exe",
                       window_title="Desktop")


@dataclass(frozen=True)
class ActionSpec:
    """A resolved, executable action description (data only)."""
    type: str                 # e.g. "mouse_click", "hotkey", "sequence"
    params: dict
    name: str = ""


@dataclass(frozen=True)
class RuleMatch:
    """Result of rule resolution."""
    rule_id: int
    profile_name: str
    action: ActionSpec
    continuous: bool = False
    cooldown_ms: int = 0
