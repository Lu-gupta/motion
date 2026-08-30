"""Gesture arming / disarming safety gate.

Two layers of coverage:
- unit: the ArmingController state machine in isolation (deterministic,
  injected clock via event timestamps).
- integration: the gate inside the real MotionController — gesture events
  published on the bus, execution observed through a patched key_press.

The gate is control-layer only: it never recognizes gestures, so these
tests publish already-recognized gesture.event values (exactly what the
GestureEngine emits) and assert execution is gated.
"""
import time
from unittest.mock import patch

import pytest

import app.actions.input_win as iw
from app.core.config import Config
from app.core.events import EventBus
from app.core.types import CameraStatus, GestureEvent
from app.data.db import Database
from app.runtime.arming import ArmingController, ArmState
from app.runtime.controller import MotionController


def ev(g, phase="start", ts=0.0, source=""):
    return GestureEvent(gesture=g, phase=phase, confidence=0.9,
                        handedness="Right", hand_x=0.5, hand_y=0.5,
                        timestamp=ts, source=source)


# ============================ unit: state machine ==========================
def _ac(**cfgkw):
    cfg = Config()
    cfg.arming_enabled = True
    cfg.arming_gesture = "fist"
    for k, v in cfgkw.items():
        setattr(cfg, k, v)
    bus = EventBus()
    states, disarms = [], []
    bus.subscribe("arming.state", states.append)
    ac = ArmingController(bus, cfg, on_disarm=lambda: disarms.append(1))
    return ac, states, disarms


def test_feature_off_is_pass_through():
    ac, _, _ = _ac(arming_enabled=False)
    assert ac.allow(ev("circle")) is True
    assert ac.allow(ev("fist")) is True
    assert ac.state == ArmState.DISARMED


def test_enabled_without_gesture_fails_open():
    ac, _, _ = _ac(arming_gesture="")
    assert ac.allow(ev("circle")) is True     # never lock the user out


def test_disarmed_blocks_and_arming_gesture_consumed():
    ac, _, _ = _ac()
    assert ac.allow(ev("point")) is False     # blocked while disarmed
    assert ac.allow(ev("fist")) is False      # control gesture consumed
    assert ac.state == ArmState.ARMED         # ...and it armed


def test_armed_allows_normal_and_control_consumed():
    ac, _, _ = _ac()
    ac.allow(ev("fist"))                       # arm
    assert ac.state == ArmState.ARMED
    assert ac.allow(ev("circle")) is True      # normal gesture passes
    # arming gesture again while ARMED is consumed, no state change
    assert ac.allow(ev("fist")) is False
    assert ac.state == ArmState.ARMED


def test_disarm_gesture_disarms_and_cancels_pending():
    ac, _, disarms = _ac(disarm_gesture="open_palm")
    ac.allow(ev("fist"))
    assert ac.state == ArmState.ARMED
    assert ac.allow(ev("open_palm")) is False  # disarm gesture consumed
    assert ac.state == ArmState.DISARMED
    assert disarms == [1]                       # on_disarm fired once


def test_hold_to_arm():
    ac, _, _ = _ac(arm_hold_ms=300)
    assert ac.allow(ev("fist", "start", ts=1.0)) is False
    assert ac.state == ArmState.ARMING
    ac.allow(ev("fist", "hold", ts=1.2))        # not yet
    assert ac.state == ArmState.ARMING
    ac.allow(ev("fist", "hold", ts=1.35))       # held long enough
    assert ac.state == ArmState.ARMED


def test_hold_instantaneous_gesture_arms_on_completion():
    ac, _, _ = _ac(arming_gesture="swipe_right", arm_hold_ms=300)
    ac.allow(ev("swipe_right", "start", ts=1.0))
    ac.allow(ev("swipe_right", "end", ts=1.0))  # start==end → instantaneous
    assert ac.state == ArmState.ARMED


def test_hold_released_early_cancels():
    ac, _, _ = _ac(arm_hold_ms=300)
    ac.allow(ev("fist", "start", ts=1.0))
    ac.allow(ev("fist", "end", ts=1.1))         # < 300 ms → cancel
    assert ac.state == ArmState.DISARMED


def test_estop_while_arming_cancels():
    ac, _, _ = _ac(arm_hold_ms=500)
    ac.allow(ev("fist", "start", ts=1.0))
    assert ac.state == ArmState.ARMING
    ac.force_disarm("emergency stop")
    assert ac.state == ArmState.DISARMED


def test_motion_off_disarms_only_when_configured():
    ac, _, _ = _ac(disarm_on_motion_off=True)
    ac.allow(ev("fist"))
    ac.bus.publish("control.enabled", False)
    assert ac.state == ArmState.DISARMED

    ac2, _, _ = _ac(disarm_on_motion_off=False)
    ac2.allow(ev("fist"))
    ac2.bus.publish("control.enabled", False)
    assert ac2.state == ArmState.ARMED          # stays armed


def test_camera_disconnect_disarms_reconnect_does_not_arm():
    ac, _, _ = _ac(disarm_on_camera_disconnect=True)
    ac.allow(ev("fist"))
    ac.bus.publish("camera.status", CameraStatus.DISCONNECTED, "")
    assert ac.state == ArmState.DISARMED
    ac.bus.publish("camera.status", CameraStatus.CONNECTED, "")
    assert ac.state == ArmState.DISARMED        # NEVER auto-arms


def test_disarming_state_is_exercised():
    ac, states, _ = _ac()
    ac.allow(ev("fist"))                         # ARMED
    ac.force_disarm("x")
    assert "DISARMING" in states and states[-1] == "DISARMED"


# ============================ integration: controller ======================
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
    c.set_motion_enabled(True)
    yield c
    c.shutdown()
    db.close()


def _map(c, gesture, key):
    a = c.profile_manager.actions.create(key.upper(), "key_press",
                                         {"key": key})
    g = c.profile_manager.profiles.by_name("Global")
    c.profile_manager.rules.create(g.id, gesture, a)
    c.reload_rules()


def _wait(pred, timeout=2.0):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        if pred():
            return True
        time.sleep(0.01)
    return False


def _fire(c, gesture, source=""):
    c.bus.publish("gesture.event", ev(gesture, "start", ts=time.monotonic(),
                                      source=source))
    c.bus.publish("gesture.event", ev(gesture, "end", ts=time.monotonic(),
                                      source=source))


def test_disarmed_blocks_all_gesture_kinds(ctl):
    c = ctl
    c.motion_gestures.create("my_z", __import__(
        "tests.test_motion_gestures", fromlist=["z_template"]).z_template())
    c.compound_gestures.create("pz", [{"type": "gesture", "gesture": "point"}])
    for gk, key in [("circle", "c"), ("swipe_right", "s"), ("point", "p"),
                    ("my_z", "z"), ("pz", "k")]:
        _map(c, gk, key)
    c.set_arming_config(arming_enabled=True, arming_gesture="fist")
    keys = []
    with patch.object(iw, "key_press", lambda k: keys.append(k)):
        for gk in ("circle", "swipe_right", "point", "my_z"):
            _fire(c, gk)
        _fire(c, "pz", source="compound")
        time.sleep(0.1)
    assert keys == []                            # DISARMED → nothing executes


def test_arm_then_execute_and_arming_gesture_not_run(ctl):
    c = ctl
    _map(c, "point", "p")
    _map(c, "fist", "f")                         # arming gesture ALSO mapped
    c.set_arming_config(arming_enabled=True, arming_gesture="fist")
    keys = []
    with patch.object(iw, "key_press", lambda k: keys.append(k)):
        _fire(c, "point")
        time.sleep(0.05)
        assert keys == []                        # still disarmed
        _fire(c, "fist")                         # arm (must NOT press 'f')
        assert _wait(lambda: c.arming.state == ArmState.ARMED)
        _fire(c, "point")
        assert _wait(lambda: keys == ["p"])      # now executes
        _fire(c, "fist")                         # repeat arming gesture
        _fire(c, "fist")
        time.sleep(0.1)
    assert "f" not in keys                        # arming gesture never runs
    assert keys == ["p"]


def test_disarm_gesture_stops_execution(ctl):
    c = ctl
    _map(c, "point", "p")
    c.set_arming_config(arming_enabled=True, arming_gesture="fist",
                        disarm_gesture="open_palm")
    keys = []
    with patch.object(iw, "key_press", lambda k: keys.append(k)):
        _fire(c, "fist")
        assert _wait(lambda: c.arming.state == ArmState.ARMED)
        _fire(c, "point")
        assert _wait(lambda: keys == ["p"])
        _fire(c, "open_palm")                    # disarm
        assert _wait(lambda: c.arming.state == ArmState.DISARMED)
        _fire(c, "point")
        time.sleep(0.1)
    assert keys == ["p"]                          # no execution after disarm


def test_emergency_stop_disarms(ctl):
    c = ctl
    _map(c, "point", "p")
    c.set_arming_config(arming_enabled=True, arming_gesture="fist")
    _fire(c, "fist")
    assert _wait(lambda: c.arming.state == ArmState.ARMED)
    c.emergency_disable()
    assert c.arming.state == ArmState.DISARMED


def test_motion_off_disarms_and_reenable_does_not_arm(ctl):
    c = ctl
    _map(c, "point", "p")
    c.set_arming_config(arming_enabled=True, arming_gesture="fist")
    _fire(c, "fist")
    assert _wait(lambda: c.arming.state == ArmState.ARMED)
    c.set_motion_enabled(False)
    assert c.arming.state == ArmState.DISARMED
    c.set_motion_enabled(True)                   # re-enable motion
    assert c.arming.state == ArmState.DISARMED   # must NOT auto-arm
    keys = []
    with patch.object(iw, "key_press", lambda k: keys.append(k)):
        _fire(c, "point")
        time.sleep(0.1)
    assert keys == []


def test_fresh_controller_starts_disarmed(tmp_path, monkeypatch):
    monkeypatch.setenv("MGA_DATA_DIR", str(tmp_path))
    cfg = Config()
    cfg.arming_enabled = True
    cfg.arming_gesture = "fist"
    db = Database(tmp_path / "s.db")
    c = MotionController(cfg, db, EventBus())
    assert c.arming.state == ArmState.DISARMED   # restart → disarmed
    c.shutdown()
    db.close()


def test_confirmation_gate_not_bypassed_when_disarmed(ctl):
    """A confirm-required workflow mapped to a gesture must not even
    request confirmation while DISARMED (the gate blocks first)."""
    c = ctl
    c.cfg.confirm_dangerous_workflows = True
    step = c.profile_manager.actions.create("S", "key_press", {"key": "w"})
    wid = c.workflow_repo.create("WF", [{"type": "action", "action_id": step}],
                                 requires_confirmation=True)
    a = c.profile_manager.actions.create("WF", "workflow",
                                         {"workflow_id": wid})
    g = c.profile_manager.profiles.by_name("Global")
    c.profile_manager.rules.create(g.id, "point", a)
    c.reload_rules()
    c.set_arming_config(arming_enabled=True, arming_gesture="fist")
    requests = []
    c.bus.subscribe("workflow.confirm_request",
                    lambda *a: requests.append(a))
    _fire(c, "point")
    time.sleep(0.1)
    assert requests == []                         # blocked before confirm


# ============================ UI smoke (offscreen) =========================
def test_arming_ui_smoke(tmp_path, monkeypatch):
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    from app.ui.bridge import QtBridge
    from app.ui.gestures_page import GesturesPage
    from app.ui.command_center import CommandCenterPage
    monkeypatch.setenv("MGA_DATA_DIR", str(tmp_path))
    app = QApplication.instance() or QApplication([])
    cfg = Config()
    db = Database(tmp_path / "ui.db")
    bus = EventBus()
    c = MotionController(cfg, db, bus)
    bridge = QtBridge(bus)
    page = GesturesPage(c, bridge)
    cc = CommandCenterPage(c, bridge)

    # enabling arming through the Studio UI persists config + shows state
    page.arm_enable.setChecked(True)
    page._select_combo(page.arm_gesture, "fist")
    page._apply_arming()
    assert c.cfg.arming_enabled is True
    assert c.cfg.arming_gesture == "fist"
    assert c.arming.state == ArmState.DISARMED
    assert "DISARMED" in page.arm_state_lbl.text()
    assert "DISARMED" in cc.safety_lbl.text()     # Command Center mirrors it

    # a live state change propagates to both UIs via the bus signal
    c.arming.force_disarm("x")                     # already disarmed → no-op
    c.bus.publish("arming.state", "ARMED")
    assert "ARMED" in page.arm_state_lbl.text()
    assert "ARMED" in cc.safety_lbl.text()
    c.shutdown()
    db.close()
