from dataclasses import dataclass

from app.gestures.custom import (CustomGestureMatcher, build_template,
                                 path_features, pose_distance)
from tests.conftest import make_hand


@dataclass
class Row:
    name: str
    template: dict
    tolerance: float
    min_confidence: float
    enabled: bool


def test_static_template_matches_same_pose():
    hands = [make_hand(pose="point") for _ in range(5)]
    tmpl = build_template("mypoint", hands, [(0.5, 0.8)] * 5)
    assert pose_distance(make_hand(pose="point"), tmpl) < 0.2
    assert pose_distance(make_hand(pose="fist"), tmpl) > 0.3


def test_matcher_static():
    hands = [make_hand(pose="thumb_up") for _ in range(5)]
    tmpl = build_template("ok", hands, [(0.5, 0.8)] * 5)
    m = CustomGestureMatcher()
    m.set_templates([Row("ok", tmpl, 0.35, 0.5, True)])
    hit = m.match(make_hand(pose="thumb_up"), [])
    assert hit is not None
    assert hit[0] == "ok"
    assert m.match(make_hand(pose="open"), []) is None


def test_matcher_respects_enabled_flag():
    hands = [make_hand(pose="fist") for _ in range(3)]
    tmpl = build_template("f", hands, [(0.5, 0.8)] * 3)
    m = CustomGestureMatcher()
    m.set_templates([Row("f", tmpl, 0.35, 0.5, False)])
    assert m.match(make_hand(pose="fist"), []) is None


def test_path_template_needs_matching_motion():
    hands = [make_hand(pose="point") for _ in range(10)]
    circle_ish = [(0.5 + 0.1 * (i % 3), 0.5 + 0.05 * i) for i in range(10)]
    tmpl = build_template("drag_down", hands, circle_ish)
    m = CustomGestureMatcher()
    m.set_templates([Row("drag_down", tmpl, 0.35, 0.4, True)])
    # same-ish trajectory matches
    hit = m.match(make_hand(pose="point"), circle_ish)
    assert hit is not None
    # opposite trajectory does not
    reverse = [(x, 1.0 - y) for x, y in circle_ish]
    assert m.match(make_hand(pose="point"), reverse) is None


def test_handedness_folding():
    """Left-hand recording should match left-hand live input."""
    left = [make_hand(pose="point", handedness="Left") for _ in range(3)]
    tmpl = build_template("lp", left, [(0.5, 0.8)] * 3)
    m = CustomGestureMatcher()
    m.set_templates([Row("lp", tmpl, 0.35, 0.5, True)])
    assert m.match(make_hand(pose="point", handedness="Left"), []) is not None


def test_path_features_static():
    pf = path_features([(0.5, 0.5), (0.501, 0.5)])
    assert pf["movement"] < 0.01
