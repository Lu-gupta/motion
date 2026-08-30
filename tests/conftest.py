from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# isolate app data (config/db/logs) per test session
_tmp_data = None


def pytest_configure(config):
    global _tmp_data
    import tempfile
    _tmp_data = tempfile.mkdtemp(prefix="mga_test_")
    os.environ["MGA_DATA_DIR"] = _tmp_data


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.types import Hand, Landmark  # noqa: E402


def make_hand(points: list[tuple[float, float]] | None = None,
              handedness: str = "Right", confidence: float = 1.0,
              pose: str = "open") -> Hand:
    """Synthetic 21-landmark hand in a canonical layout.

    pose: open | fist | pinch | point | thumb_up
    Wrist at (0.5, 0.8); fingers extend upward (decreasing y).
    """
    if points is not None:
        lms = [Landmark(x, y, 0.0) for x, y in points]
        return Hand(landmarks=lms, handedness=handedness,
                    confidence=confidence)

    wx, wy = 0.5, 0.8
    palm = 0.18  # wrist→middle MCP distance

    # x offsets per finger chain (thumb, index, middle, ring, pinky)
    finger_x = {"thumb": 0.40, "index": 0.46, "middle": 0.50,
                "ring": 0.54, "pinky": 0.58}
    mcp_y = wy - palm

    def chain(x, extended: bool):
        """(mcp, pip, dip, tip) y-positions."""
        if extended:
            return [(x, mcp_y), (x, mcp_y - 0.07), (x, mcp_y - 0.12),
                    (x, mcp_y - 0.16)]
        # curled: tip folds back toward palm
        return [(x, mcp_y), (x, mcp_y - 0.04), (x, mcp_y - 0.01),
                (x, mcp_y + 0.03)]

    ext = {
        "open": {"thumb": True, "index": True, "middle": True,
                 "ring": True, "pinky": True},
        "fist": {"thumb": False, "index": False, "middle": False,
                 "ring": False, "pinky": False},
        "pinch": {"thumb": True, "index": True, "middle": False,
                  "ring": False, "pinky": False},
        "point": {"thumb": False, "index": True, "middle": False,
                  "ring": False, "pinky": False},
        "thumb_up": {"thumb": True, "index": False, "middle": False,
                     "ring": False, "pinky": False},
    }[pose]

    pts: list[tuple[float, float]] = [(wx, wy)]  # 0 wrist

    # thumb chain: 1 CMC, 2 MCP, 3 IP, 4 TIP
    if pose == "thumb_up":
        # thumb extended straight up, tip well above the knuckle row
        pts += [(0.44, wy - 0.02), (0.42, wy - 0.06), (0.42, wy - 0.18),
                (0.43, wy - 0.30)]
    elif ext["thumb"]:
        # extended sideways/diagonal, tip away from index MCP
        pts += [(0.44, wy - 0.02), (0.40, wy - 0.06), (0.36, wy - 0.10),
                (0.33, wy - 0.13)]
    else:
        # tucked against palm, tip near index MCP
        pts += [(0.46, wy - 0.03), (0.45, wy - 0.07), (0.455, wy - 0.10),
                (0.46, wy - 0.12)]

    for f in ("index", "middle", "ring", "pinky"):
        pts += chain(finger_x[f], ext[f])

    if pose == "pinch":
        # bring thumb tip and index tip together
        tip = (0.47, mcp_y - 0.10)
        pts[4] = (tip[0] - 0.008, tip[1])
        pts[8] = (tip[0] + 0.008, tip[1])
        # keep index still "extended" (tip far from wrist vs pip)
        pts[6] = (0.46, mcp_y - 0.05)

    lms = [Landmark(x, y, 0.0) for x, y in pts]
    return Hand(landmarks=lms, handedness=handedness, confidence=confidence)


@pytest.fixture
def hand_factory():
    return make_hand
