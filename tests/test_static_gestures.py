from app.gestures import static
from tests.conftest import make_hand


def test_open_palm():
    name, conf = static.classify(make_hand(pose="open"))
    assert name == "open_palm"
    assert conf >= 0.8


def test_fist():
    name, conf = static.classify(make_hand(pose="fist"))
    assert name == "fist"
    assert conf >= 0.6


def test_pinch():
    name, conf = static.classify(make_hand(pose="pinch"))
    assert name == "pinch"
    assert conf >= 0.7


def test_point():
    name, conf = static.classify(make_hand(pose="point"))
    assert name == "point"
    assert conf >= 0.8


def test_thumb_up():
    name, conf = static.classify(make_hand(pose="thumb_up"))
    assert name == "thumb_up"
    assert conf >= 0.8


def test_pinch_beats_point():
    """Pinch pose keeps middle/ring/pinky curled — must not read as point."""
    hand = make_hand(pose="pinch")
    assert static.pinch_confidence(hand) > 0.5
    name, _ = static.classify(hand)
    assert name == "pinch"


def test_scale_invariance():
    """Same pose, half scale → same classification."""
    base = make_hand(pose="pinch")
    from app.core.types import Hand, Landmark
    shrunk = Hand(
        landmarks=[Landmark(0.5 + (lm.x - 0.5) * 0.5,
                            0.8 + (lm.y - 0.8) * 0.5, 0.0)
                   for lm in base.landmarks],
        handedness="Right", confidence=1.0)
    name, _ = static.classify(shrunk)
    assert name == "pinch"
