from app.core.events import EventBus
from app.core.types import HandFrame
from app.gestures.engine import GestureEngine
from app.gestures.motion import SwipeDetector
from tests.conftest import make_hand


def make_engine(**kw):
    bus = EventBus()
    events = []
    eng = GestureEngine(bus, confidence_threshold=0.6, debounce_frames=3,
                        release_frames=2, cooldown_ms=500, **kw)
    bus.subscribe("gesture.event", events.append)
    return eng, events


def feed(eng, pose, ts, n=1, dt=0.033):
    for i in range(n):
        hands = [make_hand(pose=pose)] if pose else []
        eng.on_hands(HandFrame(hands=hands, timestamp=ts + i * dt,
                               frame_width=640, frame_height=480))
    return ts + n * dt


def test_debounce_requires_consecutive_frames():
    eng, events = make_engine()
    feed(eng, "pinch", 0.0, n=2)
    assert [e for e in events if e.phase == "start"] == []
    feed(eng, "pinch", 0.1, n=1)
    starts = [e for e in events if e.phase == "start"]
    assert len(starts) == 1
    assert starts[0].gesture == "pinch"


def test_hold_then_release():
    eng, events = make_engine()
    t = feed(eng, "pinch", 0.0, n=5)
    holds = [e for e in events if e.phase == "hold"]
    assert len(holds) == 2  # frames after activation
    feed(eng, None, t, n=2)
    ends = [e for e in events if e.phase == "end"]
    assert len(ends) == 1
    assert ends[0].gesture == "pinch"


def test_no_repeat_start_while_held():
    eng, events = make_engine()
    feed(eng, "pinch", 0.0, n=30)
    starts = [e for e in events if e.phase == "start"]
    assert len(starts) == 1


def test_cooldown_blocks_restart():
    eng, events = make_engine()
    t = feed(eng, "pinch", 0.0, n=4)       # start
    t = feed(eng, None, t, n=3)            # end
    feed(eng, "pinch", t, n=4)             # within 500ms cooldown
    starts = [e for e in events if e.phase == "start"]
    assert len(starts) == 1
    # after cooldown expires it can start again
    feed(eng, "pinch", t + 1.0, n=4)
    starts = [e for e in events if e.phase == "start"]
    assert len(starts) == 2


def test_gesture_transition_ends_previous():
    eng, events = make_engine()
    t = feed(eng, "pinch", 0.0, n=4)
    feed(eng, "open", t, n=6)
    ends = [e for e in events if e.phase == "end" and e.gesture == "pinch"]
    starts = [e for e in events
              if e.phase == "start" and e.gesture == "open_palm"]
    assert len(ends) == 1
    assert len(starts) == 1


def test_low_confidence_ignored():
    eng, events = make_engine()
    eng.confidence_threshold = 1.1  # nothing passes
    feed(eng, "pinch", 0.0, n=10)
    assert events == []


# swipe coverage lives in tests/test_swipes.py
