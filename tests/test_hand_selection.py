"""Hand selection / control routing — PHYSICAL handedness contract.

The eligibility layer sits at the GestureEngine ingestion boundary and
selects which physical hand may drive gestures. It never changes a
recognition algorithm; "both" is a strict no-op.

SEMANTIC CONTRACT (what these tests pin):

    the user's physical LEFT  hand  ->  "left"
    the user's physical RIGHT hand  ->  "right"

The tracker labels below are declared here as INDEPENDENT literals —
deliberately not imported from the production table — so that if the
production mapping is ever flipped again these tests fail loudly.

Verified against the real camera pipeline: CameraWorker publishes the RAW
(non-mirrored) frame, MediaPipe Tasks HandLandmarker classifies it, and
its label already denotes the user's PHYSICAL hand. The mirrored preview
in the UI is a display-only transform and never feeds back into control.
"""
import pytest

from app.core.config import Config
from app.core.events import EventBus
from app.core.hand_select import (filter_hand_frame, hand_eligible,
                                   normalize_hand_control, user_perspective)
from app.core.types import HandFrame
from app.data.db import Database
from app.gestures.engine import GestureEngine
from app.runtime.controller import MotionController

from tests.conftest import make_hand

# tracker handedness label produced by each PHYSICAL hand (see docstring)
PHYSICAL_LEFT_LABEL = "Left"
PHYSICAL_RIGHT_LABEL = "Right"


# ============================ pure logic ===================================
def test_normalize_fallback():
    assert normalize_hand_control("left") == "left"
    assert normalize_hand_control("RIGHT") == "right"
    assert normalize_hand_control("both") == "both"
    for bad in (None, "", "middle", 3, "lefty"):
        assert normalize_hand_control(bad) == "both"


def test_physical_left_maps_to_left():
    assert user_perspective(PHYSICAL_LEFT_LABEL) == "left"


def test_physical_right_maps_to_right():
    assert user_perspective(PHYSICAL_RIGHT_LABEL) == "right"


def test_unknown_handedness():
    assert user_perspective("Unknown") == "unknown"
    assert user_perspective("") == "unknown"


def test_eligibility_physical_contract():
    # both accepts either physical hand
    assert hand_eligible(PHYSICAL_LEFT_LABEL, "both")
    assert hand_eligible(PHYSICAL_RIGHT_LABEL, "both")
    # selecting Left accepts physical-left, rejects physical-right
    assert hand_eligible(PHYSICAL_LEFT_LABEL, "left")
    assert not hand_eligible(PHYSICAL_RIGHT_LABEL, "left")
    # selecting Right accepts physical-right, rejects physical-left
    assert hand_eligible(PHYSICAL_RIGHT_LABEL, "right")
    assert not hand_eligible(PHYSICAL_LEFT_LABEL, "right")


def _frame(handeds, t=1.0):
    hands = [make_hand(handedness=h) for h in handeds]
    return HandFrame(hands=hands, timestamp=t, frame_width=640,
                     frame_height=480)


def test_filter_frame_both_is_same_object():
    hf = _frame([PHYSICAL_LEFT_LABEL, PHYSICAL_RIGHT_LABEL])
    assert filter_hand_frame(hf, "both") is hf          # no-op, no alloc


def test_filter_frame_single_mode_keeps_only_selected_physical_hand():
    hf = _frame([PHYSICAL_LEFT_LABEL, PHYSICAL_RIGHT_LABEL])
    left = filter_hand_frame(hf, "left")
    assert [h.handedness for h in left.hands] == [PHYSICAL_LEFT_LABEL]
    right = filter_hand_frame(hf, "right")
    assert [h.handedness for h in right.hands] == [PHYSICAL_RIGHT_LABEL]
    # all-eligible frame returns the same object
    only_left = _frame([PHYSICAL_LEFT_LABEL])
    assert filter_hand_frame(only_left, "left") is only_left


def test_no_fallback_when_selected_hand_absent():
    """Only the non-selected hand visible -> NO eligible hand (never falls
    back to the other hand)."""
    hf = _frame([PHYSICAL_RIGHT_LABEL])
    assert filter_hand_frame(hf, "left").hands == []
    hf = _frame([PHYSICAL_LEFT_LABEL])
    assert filter_hand_frame(hf, "right").hands == []


# ============================ engine ingestion =============================
def _swipe_path(t0=14.0, n=16, step=0.02):
    return [(t0 + 0.02 * k, 0.8 - step * k, 0.5) for k in range(n)]


def _frames(timed_pts, handed, extra=None, pose="point"):
    """HandFrames driving the wrist along timed_pts, hand labelled `handed`.
    `extra` optionally adds a second, stationary hand each frame."""
    base = make_hand(pose=pose)
    out = []
    for (t, x, y) in timed_pts:
        dx = x - base.landmarks[8].x
        dy = y - base.landmarks[8].y
        pts = [(lm.x + dx, lm.y + dy) for lm in base.landmarks]
        hands = [make_hand(points=pts, handedness=handed)]
        if extra is not None:
            still = [(lm.x, lm.y) for lm in base.landmarks]
            hands.append(make_hand(points=still, handedness=extra))
        out.append(HandFrame(hands=hands, timestamp=t, frame_width=640,
                             frame_height=480))
    return out


def _engine(mode):
    bus = EventBus()
    eng = GestureEngine(bus)
    eng.hand_control = mode
    evs = []
    bus.subscribe("gesture.event", lambda e: evs.append((e.gesture, e.phase)))
    return eng, evs


def _run(eng, frames):
    for hf in frames:
        eng.on_hands(hf)


def _fired(evs):
    return any(p == "start" and g.startswith("swipe") for g, p in evs)


@pytest.mark.parametrize("physical", [PHYSICAL_LEFT_LABEL,
                                      PHYSICAL_RIGHT_LABEL])
def test_both_mode_accepts_either_physical_hand(physical):
    eng, evs = _engine("both")
    _run(eng, _frames(_swipe_path(), physical))
    assert _fired(evs)


def test_left_mode_accepts_physical_left():
    eng, evs = _engine("left")
    _run(eng, _frames(_swipe_path(), PHYSICAL_LEFT_LABEL))
    assert _fired(evs)


def test_left_mode_rejects_physical_right():
    eng, evs = _engine("left")
    _run(eng, _frames(_swipe_path(), PHYSICAL_RIGHT_LABEL))
    assert not _fired(evs)


def test_right_mode_accepts_physical_right():
    eng, evs = _engine("right")
    _run(eng, _frames(_swipe_path(), PHYSICAL_RIGHT_LABEL))
    assert _fired(evs)


def test_right_mode_rejects_physical_left():
    eng, evs = _engine("right")
    _run(eng, _frames(_swipe_path(), PHYSICAL_LEFT_LABEL))
    assert not _fired(evs)


def test_both_hands_present_only_selected_contributes():
    # the MOVING hand is physical-left; a stationary physical-right decoy
    eng, evs = _engine("left")
    _run(eng, _frames(_swipe_path(), PHYSICAL_LEFT_LABEL,
                      extra=PHYSICAL_RIGHT_LABEL))
    assert _fired(evs)                       # selected hand moved -> fires
    # right-only: the eligible hand is the stationary decoy -> no swipe,
    # and the moving (non-selected) hand must NOT be used instead
    eng, evs = _engine("right")
    _run(eng, _frames(_swipe_path(), PHYSICAL_LEFT_LABEL,
                      extra=PHYSICAL_RIGHT_LABEL))
    assert not _fired(evs)


def test_setting_change_takes_effect_without_restart():
    eng, evs = _engine("both")
    eng.hand_control = "right"
    _run(eng, _frames(_swipe_path(), PHYSICAL_LEFT_LABEL))   # ineligible
    assert not _fired(evs)
    eng.hand_control = "left"
    evs.clear()
    _run(eng, _frames(_swipe_path(), PHYSICAL_LEFT_LABEL))   # now eligible
    assert _fired(evs)


def test_current_handedness_readout_is_physical():
    eng, _ = _engine("both")
    _run(eng, _frames(_swipe_path()[:2], PHYSICAL_RIGHT_LABEL))
    assert eng.current_handedness == "right"
    eng.on_hands(HandFrame(hands=[], timestamp=99.0, frame_width=640,
                           frame_height=480))
    assert eng.current_handedness == ""


# ============================ controller / lifecycle =======================
@pytest.fixture
def ctl(tmp_path, monkeypatch):
    monkeypatch.setenv("MGA_DATA_DIR", str(tmp_path))
    db = Database(tmp_path / "rt.db")
    c = MotionController(Config(), db, EventBus())
    yield c
    c.shutdown()
    db.close()


def test_default_is_both_backward_compatible():
    assert Config().hand_control == "both"


def test_invalid_config_falls_back(tmp_path, monkeypatch):
    monkeypatch.setenv("MGA_DATA_DIR", str(tmp_path))
    cfg = Config()
    cfg.hand_control = "nonsense"
    db = Database(tmp_path / "b.db")
    c = MotionController(cfg, db, EventBus())
    assert c.gestures.hand_control == "both"             # safe fallback
    c.shutdown()
    db.close()


def test_set_hand_control_persists_and_survives_reset(ctl):
    c = ctl
    assert c.set_hand_control("left") == "left"
    assert c.gestures.hand_control == "left"
    assert c.cfg.hand_control == "left"
    # motion off / engine reset must NOT change the routing preference
    c.set_motion_enabled(False)
    c.gestures.reset()
    assert c.gestures.hand_control == "left"
    # camera disconnect/reconnect must preserve it too
    from app.core.types import CameraStatus
    c.bus.publish("camera.status", CameraStatus.DISCONNECTED, "")
    c.bus.publish("camera.status", CameraStatus.CONNECTED, "")
    assert c.gestures.hand_control == "left"
    assert c.set_hand_control("bogus") == "both"         # normalized


def test_reload_config_preserves_selection(tmp_path, monkeypatch):
    monkeypatch.setenv("MGA_DATA_DIR", str(tmp_path))
    cfg = Config()
    cfg.hand_control = "right"
    db = Database(tmp_path / "s.db")
    c = MotionController(cfg, db, EventBus())
    c.cfg.save()
    reloaded = Config.load()
    assert reloaded.hand_control == "right"              # persisted on disk
    c.shutdown()
    db.close()
