"""Swipe detection tests — detector, engine integration, rule reach,
action execution, mirroring, robustness."""
import time

import pytest

from app.core.events import EventBus
from app.core.types import Hand, HandFrame, Landmark
from app.gestures.engine import GestureEngine
from app.gestures.motion import SwipeDetector
from tests.conftest import make_hand

FPS_DT = 0.05  # 20 FPS, matches the measured live camera rate


def shifted_hand(dx: float = 0.0, dy: float = 0.0) -> Hand:
    base = make_hand(pose="open")
    return Hand(
        landmarks=[Landmark(lm.x + dx, lm.y + dy, 0.0)
                   for lm in base.landmarks],
        handedness="Right", confidence=1.0)


def run_motion(det: SwipeDetector, positions: list[tuple[float, float]],
               dt: float = FPS_DT, t0: float = 0.0):
    """Feed a list of (dx, dy) wrist offsets; return all fires."""
    fires = []
    for i, (dx, dy) in enumerate(positions):
        r = det.update(shifted_hand(dx, dy), t0 + i * dt)
        if r:
            fires.append(r)
    return fires


def linear(n: int, dx_total: float, dy_total: float):
    return [(dx_total * i / (n - 1), dy_total * i / (n - 1))
            for i in range(n)]


# -- detector: four directions (image coords, mirror off) -------------------
@pytest.mark.parametrize("dx,dy,expected", [
    (0.30, 0.0, "swipe_right"),
    (-0.30, 0.0, "swipe_left"),
    (0.0, -0.30, "swipe_up"),
    (0.0, 0.30, "swipe_down"),
])
def test_directions_image_coords(dx, dy, expected):
    det = SwipeDetector(mirror_x=False)
    assert run_motion(det, linear(8, dx, dy)) == [expected]


# -- detector: mirroring (user perspective) ---------------------------------
def test_mirrored_horizontal_directions():
    """User moves hand to THEIR right → image x decreases → swipe_right."""
    det = SwipeDetector(mirror_x=True)
    assert run_motion(det, linear(8, -0.30, 0.0)) == ["swipe_right"]
    det2 = SwipeDetector(mirror_x=True)
    assert run_motion(det2, linear(8, 0.30, 0.0)) == ["swipe_left"]


def test_mirror_does_not_affect_vertical():
    det = SwipeDetector(mirror_x=True)
    assert run_motion(det, linear(8, 0.0, -0.30)) == ["swipe_up"]


# -- robustness -------------------------------------------------------------
def test_small_jitter_no_fire():
    """Random small wobble must not trigger."""
    import random
    rng = random.Random(42)
    det = SwipeDetector(mirror_x=False)
    positions = [(rng.uniform(-0.03, 0.03), rng.uniform(-0.03, 0.03))
                 for _ in range(60)]
    assert run_motion(det, positions) == []


def test_slow_movement_no_fire():
    """Covers distance but far too slowly (drift, not a swipe)."""
    det = SwipeDetector(mirror_x=False)
    # 0.3 units over 3 seconds → 0.1 u/s, below min_speed
    assert run_motion(det, linear(60, 0.30, 0.0)) == []


def test_diagonal_rejected_deterministically():
    det = SwipeDetector(mirror_x=False)
    for _ in range(3):
        det2 = SwipeDetector(mirror_x=False)
        assert run_motion(det2, linear(8, 0.25, 0.25)) == []


def test_curved_motion_rejected_by_straightness():
    """Out-and-back: large path, small net displacement."""
    det = SwipeDetector(mirror_x=False)
    out = linear(6, 0.25, 0.0)
    back = [(0.25 - 0.25 * i / 5, 0.0) for i in range(1, 6)]
    fires = run_motion(det, out + back)
    # the outbound leg alone may legitimately fire once; the return
    # inside cooldown must not produce a second event
    assert len(fires) <= 1


def test_cooldown_one_fire_per_motion():
    det = SwipeDetector(mirror_x=False, cooldown_s=0.6)
    fires = run_motion(det, linear(8, 0.30, 0.0))
    assert fires == ["swipe_right"]
    # immediately continue moving — still inside cooldown
    more = run_motion(det, linear(8, 0.30, 0.0), t0=8 * FPS_DT)
    assert more == []
    # after cooldown expires a new swipe fires
    later = run_motion(det, linear(8, 0.30, 0.0), t0=2.0)
    assert later == ["swipe_right"]


def test_pose_independent():
    """Swipe with a fist — must still fire (no pose requirement)."""
    det = SwipeDetector(mirror_x=False)
    fires = []
    for i, (dx, dy) in enumerate(linear(8, 0.30, 0.0)):
        base = make_hand(pose="fist")
        h = Hand(landmarks=[Landmark(lm.x + dx, lm.y + dy, 0.0)
                            for lm in base.landmarks],
                 handedness="Right", confidence=1.0)
        r = det.update(h, i * FPS_DT)
        if r:
            fires.append(r)
    assert fires == ["swipe_right"]


# -- engine integration -----------------------------------------------------
def make_engine(**kw):
    bus = EventBus()
    events = []
    eng = GestureEngine(bus, confidence_threshold=0.6, debounce_frames=3,
                        release_frames=2, cooldown_ms=500, **kw)
    bus.subscribe("gesture.event", events.append)
    return eng, events, bus


def feed_motion(eng, positions, dt=FPS_DT, t0=0.0, gaps=()):
    for i, (dx, dy) in enumerate(positions):
        hands = [] if i in gaps else [shifted_hand(dx, dy)]
        eng.on_hands(HandFrame(hands=hands, timestamp=t0 + i * dt,
                               frame_width=640, frame_height=480))


def test_engine_emits_swipe_start_end_mirrored():
    eng, events, _ = make_engine()  # default mirror_x=True
    feed_motion(eng, linear(8, -0.30, 0.0))  # image-left = user-right
    swipes = [e for e in events if e.gesture == "swipe_right"]
    assert [e.phase for e in swipes] == ["start", "end"]


def test_engine_survives_tracking_gap_mid_swipe():
    """MediaPipe dropping 1-2 frames during fast motion must not kill
    the swipe."""
    eng, events, _ = make_engine()
    feed_motion(eng, linear(10, -0.35, 0.0), gaps={4, 5})
    swipes = [e for e in events if e.gesture == "swipe_right"]
    assert len(swipes) >= 1


def test_engine_long_gap_clears_history():
    eng, events, _ = make_engine()
    # first half of a swipe, then a long tracking loss, then hand
    # reappears far away — must NOT fire from the stale history
    feed_motion(eng, linear(4, -0.12, 0.0))
    feed_motion(eng, [(-0.12, 0.0)] * 6, t0=4 * FPS_DT, gaps=set(range(6)))
    eng.on_hands(HandFrame(hands=[shifted_hand(-0.35, 0.0)],
                           timestamp=1.0, frame_width=640, frame_height=480))
    assert [e for e in events if e.gesture.startswith("swipe")] == []


def test_swipe_display_sticky_for_ui():
    eng, events, _ = make_engine()
    feed_motion(eng, linear(8, -0.30, 0.0))
    assert eng.current_gesture == "swipe_right"
    # a few more neutral frames within the display hold — still shown
    feed_motion(eng, [(-0.30, 0.0)] * 3, t0=8 * FPS_DT)
    assert eng.current_gesture == "swipe_right"
    # after the hold expires it clears
    eng.on_hands(HandFrame(hands=[shifted_hand(-0.30, 0.0)], timestamp=5.0,
                           frame_width=640, frame_height=480))
    assert eng.current_gesture != "swipe_right"


def test_swipe_does_not_break_static_gesture_flow():
    """Static pinch before and after a swipe still works."""
    eng, events, _ = make_engine()
    for i in range(4):  # pinch activates
        eng.on_hands(HandFrame(hands=[make_hand(pose="pinch")],
                               timestamp=i * FPS_DT,
                               frame_width=640, frame_height=480))
    assert any(e.gesture == "pinch" and e.phase == "start" for e in events)
    feed_motion(eng, linear(8, -0.30, 0.0), t0=1.0)   # swipe
    assert any(e.gesture == "swipe_right" for e in events)
    for i in range(8):  # pinch again after swipe + cooldown
        eng.on_hands(HandFrame(hands=[make_hand(pose="pinch")],
                               timestamp=3.0 + i * FPS_DT,
                               frame_width=640, frame_height=480))
    starts = [e for e in events
              if e.gesture == "pinch" and e.phase == "start"]
    assert len(starts) == 2
