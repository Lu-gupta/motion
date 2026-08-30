"""Gesture Studio 2.1 — motion-gesture recording quality & template
management. Deterministic; no real camera, no real app launches.

Template management is layered on the EXISTING recognition path: samples
are the normalized trajectories the recognizer already uses, and every
edit rebuilds the merged template through the existing build function —
no second algorithm, no second store. These tests prove the management
layer preserves recognition and never regresses swipes.
"""
import time

import pytest

from app.core.config import Config
from app.core.events import EventBus
from app.core.types import GestureEvent, HandFrame
from app.data.db import Database
from app.gestures.engine import GestureEngine
from app.gestures.trajectory import (build_motion_template,
                                     evaluate_motion_sample, motion_samples,
                                     rebuild_template, sample_spread,
                                     template_diagnostics)
from app.runtime.controller import MotionController

from tests.test_motion_gestures import (FakeRow, engine_with, feed,
                                        hand_frames, noisy, timed,
                                        triangle_shape, z_shape, z_template)


# -- data model: samples stored, previewable, rebuildable --------------------
def test_template_stores_raw_samples_and_revision():
    t = build_motion_template([z_shape(), z_shape(cx=0.4), z_shape(s=0.18)])
    assert t["samples"] == 3
    assert len(t["raw_samples"]) == 3
    assert all(len(s) == 32 for s in t["raw_samples"])
    assert t["revision"] == 1


def test_motion_samples_legacy_fallback():
    t = build_motion_template([z_shape(), z_shape(cx=0.4)])
    legacy = {"points": t["points"], "allow_reverse": True, "samples": 2}
    assert len(motion_samples(legacy)) == 1          # merged → 1 sample
    assert len(motion_samples(t)) == 2               # real per-sample data


def test_rebuild_bumps_revision_and_still_recognizes():
    t = build_motion_template([z_shape(), z_shape(cx=0.4), z_shape(s=0.18)])
    t2 = rebuild_template(motion_samples(t)[:2],
                          revision=t["revision"] + 1)
    assert t2["revision"] == 2 and t2["samples"] == 2
    assert [f[0] for f in feed(engine_with(template=t2),
                               timed(z_shape()))] == ["my_z"]


def test_sample_spread_and_consistency():
    same = build_motion_template([z_shape(), z_shape(cx=0.4, cy=0.6)])
    d = template_diagnostics(same)
    assert d["samples"] == 2 and d["points"] == 32
    assert d["consistency"] in ("consistent", "varied", "inconsistent")
    mixed = build_motion_template([z_shape(), triangle_shape()])
    assert template_diagnostics(mixed)["spread_max"] > d["spread_max"]
    assert sample_spread([z_shape()]) == {"mean": 0.0, "max": 0.0}


# -- recording quality (only real gates, no invented metrics) ----------------
def test_evaluate_motion_sample_reasons():
    good = evaluate_motion_sample(z_shape())
    assert good["ok"] and good["reasons"] == []
    small = evaluate_motion_sample([(0.5, 0.5), (0.505, 0.5)] * 6)
    assert not small["ok"] and "Movement too small" in small["reasons"]
    few = evaluate_motion_sample([(0.5, 0.5), (0.9, 0.9)])
    assert not few["ok"]
    assert "Insufficient trajectory data" in few["reasons"]


# -- recognition preserved through management --------------------------------
def test_rebuilt_template_size_position_speed_invariant():
    t = build_motion_template([z_shape(), z_shape(cx=0.4),
                               noisy(z_shape(s=0.18))])
    t2 = rebuild_template(motion_samples(t))       # delete/rebuild round-trip

    def eng():
        return engine_with(template=t2)
    assert feed(eng(), timed(z_shape(cx=0.35, cy=0.6, s=0.09)))   # pos+size
    assert feed(eng(), timed(z_shape(), duration=0.6))           # fast
    assert feed(eng(), timed(z_shape(), duration=2.0))           # slow


def test_rebuilt_template_false_matches():
    t = build_motion_template([z_shape(), z_shape(cx=0.4)])

    def eng():
        return engine_with(template=t)
    assert all(f[0] != "my_z" for f in feed(eng(), timed(triangle_shape())))
    line = [(0.2 + 0.5 * k / 30, 0.5) for k in range(31)]        # swipe-like
    assert feed(eng(), timed(line)) == []
    assert feed(eng(), timed(z_shape()[:22], duration=0.8)) == []  # partial
    assert feed(eng(), timed(z_shape(s=0.03))) == []              # too small


# -- swipe protection (mandatory) --------------------------------------------
def _swipe_path(dirn, t0=14.0):
    n, step = 16, 0.02
    out = []
    for k in range(n):
        t = t0 + k * 0.02
        if dirn == "right":
            x, y = 0.8 - step * k, 0.5
        elif dirn == "left":
            x, y = 0.2 + step * k, 0.5
        elif dirn == "up":
            x, y = 0.5, 0.8 - step * k
        else:
            x, y = 0.5, 0.2 + step * k
        out.append((t, x, y))
    return out


def _engine_with_template():
    bus = EventBus()
    eng = GestureEngine(bus)
    eng.trajectories.set_templates([FakeRow("my_z", z_template())])
    events = []
    bus.subscribe("gesture.event", lambda ev: events.append(ev))
    return eng, events


@pytest.mark.parametrize("dirn,expect", [
    ("right", "swipe_right"), ("left", "swipe_left"),
    ("up", "swipe_up"), ("down", "swipe_down")])
def test_all_swipes_fire_with_motion_template_loaded(dirn, expect):
    eng, events = _engine_with_template()
    for hf in hand_frames(_swipe_path(dirn)):
        eng.on_hands(hf)
    assert any(e.gesture == expect for e in events), dirn


def test_partial_motion_then_swipe_still_swipes():
    eng, events = _engine_with_template()
    for hf in hand_frames(timed(z_shape()[:18], t0=10.0, duration=0.7)):
        eng.on_hands(hf)
    assert not any(e.gesture == "my_z" for e in events)
    events.clear()
    for hf in hand_frames(_swipe_path("right", t0=14.0)):
        eng.on_hands(hf)
    assert any(e.gesture == "swipe_right" for e in events)


# -- neutral-before-retrigger for motion gestures ----------------------------
def test_motion_neutral_before_retrigger():
    bus = EventBus()
    eng = GestureEngine(bus)
    eng.require_neutral = True
    eng.trajectories.set_templates([FakeRow("my_z", z_template())])
    starts = []
    bus.subscribe("gesture.event",
                  lambda ev: starts.append((ev.gesture, ev.phase)))

    def n_fires():
        return sum(1 for g, p in starts if g == "my_z" and p == "start")

    for hf in hand_frames(timed(z_shape(), t0=10.0)):
        eng.on_hands(hf)
    assert n_fires() == 1
    for hf in hand_frames(timed(z_shape(), t0=13.5)):   # no neutral between
        eng.on_hands(hf)
    assert n_fires() == 1                                # duplicate blocked
    eng.on_hands(HandFrame(hands=[], timestamp=15.0,
                           frame_width=640, frame_height=480))  # neutral
    for hf in hand_frames(timed(z_shape(), t0=16.0)):
        eng.on_hands(hf)
    assert n_fires() == 2                                # fires again


# -- controller: rename cascade, dependents, disable/enable ------------------
@pytest.fixture
def ctl(tmp_path, monkeypatch):
    monkeypatch.setenv("MGA_DATA_DIR", str(tmp_path))
    cfg = Config()
    cfg.default_cooldown_ms = 0
    cfg.action_global_cooldown_ms = 0
    db = Database(tmp_path / "rt.db")
    bus = EventBus()
    c = MotionController(cfg, db, bus)
    g = c.profile_manager.profiles.by_name("Global")
    for r in c.profile_manager.rules.for_profile(g.id):
        c.profile_manager.rules.delete(r.id)
    c.reload_rules()
    yield c
    c.shutdown()
    db.close()


def test_dependents_detection(ctl):
    c = ctl
    pm = c.profile_manager
    c.motion_gestures.create("my_z", z_template())
    assert c.motion_gesture_dependents("my_z") == {"rules": [],
                                                    "compounds": []}
    a = pm.actions.create("A", "key_press", {"key": "a"})
    g = pm.profiles.by_name("Global")
    pm.rules.create(g.id, "my_z", a)
    c.compound_gestures.create(
        "pz", [{"type": "gesture", "gesture": "my_z"}])
    deps = c.motion_gesture_dependents("my_z")
    assert deps["rules"] and deps["rules"][0]["action"] == "A"
    assert deps["compounds"] == ["pz"]


def test_rename_cascades_and_preserves_mappings(ctl):
    c = ctl
    pm = c.profile_manager
    gid = c.motion_gestures.create("my_z", z_template())
    a = pm.actions.create("A", "key_press", {"key": "a"})
    g = pm.profiles.by_name("Global")
    rid = pm.rules.create(g.id, "my_z", a)
    c.compound_gestures.create(
        "pz", [{"type": "gesture", "gesture": "pinch"},
               {"type": "gesture", "gesture": "my_z"}], step_timeout_ms=1500)
    ok, msg = c.rename_motion_gesture(gid, "My Zed")
    assert ok, msg
    assert c.motion_gestures.get(gid).name == "my_zed"      # normalized
    assert pm.rules.get(rid).gesture == "my_zed"            # rule cascaded
    comp = c.compound_gestures.by_name("pz")
    assert any(s.get("gesture") == "my_zed" for s in comp.steps)
    # engine picks up the renamed template
    assert any(d.name == "my_zed"
               for d in c.gestures.trajectories.detectors)


def test_rename_rejects_duplicate_and_builtin(ctl):
    c = ctl
    gid = c.motion_gestures.create("my_z", z_template())
    c.motion_gestures.create("other", z_template())
    assert c.rename_motion_gesture(gid, "other")[0] is False
    assert c.rename_motion_gesture(gid, "pinch")[0] is False   # built-in
    assert c.rename_motion_gesture(gid, "circle")[0] is False  # trajectory
    assert c.motion_gestures.get(gid).name == "my_z"


def test_disable_stops_recognition_keeps_data(ctl):
    c = ctl
    gid = c.motion_gestures.create("my_z", z_template())
    c.reload_rules()
    assert any(d.name == "my_z" for d in c.gestures.trajectories.detectors)
    c.motion_gestures.update(gid, enabled=False)
    c.reload_rules()
    assert all(d.name != "my_z" for d in c.gestures.trajectories.detectors)
    assert len(motion_samples(c.motion_gestures.get(gid).template)) >= 1
    c.motion_gestures.update(gid, enabled=True)
    c.reload_rules()
    assert any(d.name == "my_z" for d in c.gestures.trajectories.detectors)


# -- UI smoke (offscreen) -----------------------------------------------------
def test_manager_dialog_smoke(tmp_path, monkeypatch):
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    import app.ui.gestures_page as gp
    from app.ui.bridge import QtBridge
    monkeypatch.setenv("MGA_DATA_DIR", str(tmp_path))
    app = QApplication.instance() or QApplication([])
    cfg = Config()
    db = Database(tmp_path / "ui.db")
    bus = EventBus()
    c = MotionController(cfg, db, bus)
    bridge = QtBridge(bus)
    gid = c.motion_gestures.create("my_z", z_template())

    dlg = gp.MotionGestureManagerDialog(c, bridge, gid)
    assert dlg.samples_list.count() == 3
    dlg.samples_list.setCurrentRow(0)
    dlg._show_sample(0)
    assert dlg.sample_info.text() != "—"

    # delete one sample → rebuild keeps recognition data, bumps revision
    rev0 = c.motion_gestures.get(gid).template["revision"]
    dlg._rebuild(dlg._samples()[:2])
    assert dlg.samples_list.count() == 2
    assert c.motion_gestures.get(gid).template["revision"] == rev0 + 1

    # min-sample protection: deleting the last sample warns, never deletes
    monkeypatch.setattr(gp.QMessageBox, "warning", lambda *a, **k: None)
    dlg._rebuild([dlg._samples()[0]])
    assert dlg.samples_list.count() == 1
    dlg.samples_list.setCurrentRow(0)
    before = c.motion_gestures.get(gid).template["revision"]
    dlg._delete_sample()
    assert c.motion_gestures.get(gid).template["revision"] == before
    assert dlg.samples_list.count() == 1                 # still there

    # dependency-aware delete helper renders without launching anything
    a = c.profile_manager.actions.create("A", "key_press", {"key": "a"})
    g = c.profile_manager.profiles.by_name("Global")
    c.profile_manager.rules.create(g.id, "my_z", a)
    monkeypatch.setattr(gp.QMessageBox, "question",
                        lambda *a, **k: gp.QMessageBox.Cancel)
    m = c.motion_gestures.get(gid)
    assert gp._confirm_delete_motion(dlg, c, m) is False   # cancelled
    dlg.done(0)
    c.shutdown()
    db.close()
