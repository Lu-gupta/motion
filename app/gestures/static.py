"""Static hand-pose classifiers.

Pure geometry on the internal Hand type. Each classifier returns a
confidence in [0..1]; classify() returns the best (name, confidence).

Distances are normalized by palm size so classification is
scale-invariant (works near or far from the camera).
"""
from __future__ import annotations

import math

from ..core.types import Hand, Landmark

STATIC_GESTURES = ["pinch", "open_palm", "fist", "point", "thumb_up"]

FINGER_JOINTS = {
    "index": (Hand.INDEX_MCP, Hand.INDEX_PIP, Hand.INDEX_TIP),
    "middle": (Hand.MIDDLE_MCP, Hand.MIDDLE_PIP, Hand.MIDDLE_TIP),
    "ring": (Hand.RING_MCP, Hand.RING_PIP, Hand.RING_TIP),
    "pinky": (Hand.PINKY_MCP, Hand.PINKY_PIP, Hand.PINKY_TIP),
}


def _dist(a: Landmark, b: Landmark) -> float:
    return math.hypot(a.x - b.x, a.y - b.y)


def finger_extended(hand: Hand, finger: str) -> bool:
    """A finger is extended when tip is farther from wrist than PIP."""
    mcp_i, pip_i, tip_i = FINGER_JOINTS[finger]
    wrist = hand.landmarks[Hand.WRIST]
    tip = hand.landmarks[tip_i]
    pip = hand.landmarks[pip_i]
    return _dist(wrist, tip) > _dist(wrist, pip) * 1.08


def thumb_extended(hand: Hand) -> bool:
    """Thumb extended when tip is far from index MCP (relative to palm)."""
    tip = hand.landmarks[Hand.THUMB_TIP]
    index_mcp = hand.landmarks[Hand.INDEX_MCP]
    return _dist(tip, index_mcp) / hand.palm_size() > 0.55


def extension_state(hand: Hand) -> dict[str, bool]:
    st = {f: finger_extended(hand, f) for f in FINGER_JOINTS}
    st["thumb"] = thumb_extended(hand)
    return st


def pinch_confidence(hand: Hand) -> float:
    """Thumb tip ↔ index tip proximity while the index reaches forward.

    The reach requirement separates pinch from fist/thumb-up, where a
    curled index tip also sits near the thumb.
    """
    wrist = hand.landmarks[Hand.WRIST]
    index_tip = hand.landmarks[Hand.INDEX_TIP]
    if _dist(wrist, index_tip) / hand.palm_size() < 1.2:
        return 0.0
    d = _dist(hand.landmarks[Hand.THUMB_TIP], index_tip) / hand.palm_size()
    # d < 0.25 → strong pinch; d > 0.6 → not a pinch
    if d >= 0.6:
        return 0.0
    return max(0.0, min(1.0, (0.6 - d) / 0.35))


def open_palm_confidence(hand: Hand) -> float:
    st = extension_state(hand)
    n = sum(st.values())  # 0..5
    if not all(st[f] for f in FINGER_JOINTS):
        return 0.0
    return 0.85 if n == 4 else 1.0


def fist_confidence(hand: Hand) -> float:
    st = extension_state(hand)
    if any(st[f] for f in FINGER_JOINTS):
        return 0.0
    if st["thumb"]:
        # extended thumb with curled fingers reads as thumb-up territory
        return 0.4
    # tips close to palm center
    wrist = hand.landmarks[Hand.WRIST]
    mid_mcp = hand.landmarks[Hand.MIDDLE_MCP]
    cx, cy = (wrist.x + mid_mcp.x) / 2, (wrist.y + mid_mcp.y) / 2
    avg = sum(math.hypot(hand.landmarks[t].x - cx, hand.landmarks[t].y - cy)
              for _, _, t in FINGER_JOINTS.values()) / 4 / hand.palm_size()
    return 1.0 if avg < 0.9 else 0.6


def point_confidence(hand: Hand) -> float:
    st = extension_state(hand)
    if st["index"] and not st["middle"] and not st["ring"] and not st["pinky"]:
        return 0.9 if st["thumb"] else 1.0
    return 0.0


def thumb_up_confidence(hand: Hand) -> float:
    st = extension_state(hand)
    if not st["thumb"] or any(st[f] for f in FINGER_JOINTS):
        return 0.0
    tip = hand.landmarks[Hand.THUMB_TIP]
    mcp = hand.landmarks[Hand.THUMB_MCP]
    dx, dy = tip.x - mcp.x, tip.y - mcp.y
    if -dy > abs(dx) * 1.2:  # image y grows downward → up is negative
        return 1.0
    return 0.0


CLASSIFIERS = {
    "pinch": pinch_confidence,
    "open_palm": open_palm_confidence,
    "fist": fist_confidence,
    "point": point_confidence,
    "thumb_up": thumb_up_confidence,
}


def classify(hand: Hand) -> tuple[str, float]:
    """Best static gesture for a hand; ("none", 0.0) when nothing fits.

    Pinch wins over point when both fire (pinch pose often keeps other
    fingers curled like point does).
    """
    scores = {name: fn(hand) for name, fn in CLASSIFIERS.items()}
    if scores["pinch"] > 0.5:
        scores["point"] = 0.0
        scores["fist"] = 0.0
    best = max(scores, key=scores.get)
    if scores[best] <= 0.0:
        return "none", 0.0
    return best, scores[best]
