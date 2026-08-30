"""Hand selection / control routing — the single authoritative eligibility
layer between the tracker's handedness output and the gesture-control path.

Pure and dependency-light (no MediaPipe, no Qt). It NEVER recognizes a
gesture and NEVER alters `Hand.handedness`; it only decides which hands
may enter the existing GestureEngine. `mode == "both"` is a strict no-op
(the frame is returned unchanged) so default behavior is byte-for-byte the
same as before this feature existed.

Handedness convention — the semantic contract is:

    the user's physical LEFT  hand  ->  "left"
    the user's physical RIGHT hand  ->  "right"

Verified end to end against the real pipeline:

- `CameraWorker` publishes the RAW frame (no flip) and `HandTracker` feeds
  exactly that to MediaPipe Tasks HandLandmarker, passing
  `category_name` through verbatim. There is NO mirroring anywhere before
  or inside classification.
- The mirrored/selfie view the user sees is a DISPLAY-only transform in
  `ui/video_widget.py` (`frame[:, ::-1, ::-1]` plus a landmark-x flip for
  drawing). It never feeds back into control, so it must not influence
  this mapping.
- On this pipeline the tracker's handedness label ALREADY denotes the
  user's physical hand, so the mapping here is the IDENTITY.

Why this is not a coordinate flip: image mirroring inverts the x AXIS, and
the codebase already handles that separately and correctly where it truly
applies (`SwipeDetector.mirror_x`, the cursor's x inversion, the preview).
Handedness is not a coordinate — it is an anatomical classification the
model resolves from the hand's own structure, so it needs no x-flip.
Conflating the two is what previously inverted this table (it applied the
"input is assumed mirrored, swap otherwise" note a priori, which does not
match the observed behavior of MediaPipe Tasks here).

This table is the ONE place handedness semantics are decided. Recognition
still receives the original `Hand.handedness`, so custom-gesture
handedness folding and compound hand-locking are unaffected.
"""
from __future__ import annotations

VALID_MODES = ("left", "right", "both")

# tracker handedness label -> user's physical hand (see module docstring:
# empirically verified as the identity for our non-mirrored feed)
_TO_USER = {"Left": "left", "Right": "right"}


def normalize_hand_control(value) -> str:
    """Coerce any stored/config value to a valid mode; anything missing or
    invalid falls back to 'both' (backward compatible)."""
    if isinstance(value, str) and value.lower() in VALID_MODES:
        return value.lower()
    return "both"


def user_perspective(mp_handedness: str) -> str:
    """MediaPipe handedness label -> 'left' | 'right' | 'unknown' from the
    USER's physical perspective (see module docstring for why we swap)."""
    return _TO_USER.get(mp_handedness, "unknown")


def hand_eligible(mp_handedness: str, mode: str) -> bool:
    """True if a hand with this tracker handedness may generate control
    events under `mode`. 'both' accepts everything; a single-hand mode
    accepts ONLY the selected physical hand (never falls back)."""
    if mode == "both":
        return True
    return user_perspective(mp_handedness) == mode


def filter_hand_frame(hf, mode: str):
    """Return a HandFrame containing only the eligible hands. 'both' (and
    any frame where every hand is already eligible) returns the SAME frame
    object — no allocation, identical downstream behavior."""
    if mode == "both" or not getattr(hf, "hands", None):
        return hf
    eligible = [h for h in hf.hands if hand_eligible(h.handedness, mode)]
    if len(eligible) == len(hf.hands):
        return hf
    from .types import HandFrame
    return HandFrame(hands=eligible, timestamp=hf.timestamp,
                     frame_width=hf.frame_width, frame_height=hf.frame_height)
