"""Smart workflow conditions & waits (spec §21).

Process/window state is mocked — no real applications are polled except
the explicitly-real python.exe process checks.
"""
import logging
import threading
import time
from unittest.mock import patch

import pytest

import app.actions.input_win as iw
from app.actions.executor import ActionExecutor
from app.context import conditions
from app.core.events import EventBus
from app.core.types import ActionSpec, Context, GestureEvent
from app.data.db import Database
from app.data.repository import ActionRepo, WorkflowRepo
from app.runtime.workflows import WorkflowEngine


class Harness:
    def __init__(self, tmp_path, poll_s=0.03):
        self.db = Database(tmp_path / "wfc.db")
        self.bus = EventBus()
        self.actions = ActionRepo(self.db)
        self.workflows = WorkflowRepo(self.db)
        self.executor = ActionExecutor(self.bus)
        self.engine = WorkflowEngine(self.bus, self.executor, self.actions,
                                     self.workflows)
        self.engine.POLL_S = poll_s
        self.executor.workflows = self.engine
        self.done = []
        self._evt = threading.Event()
        self.bus.subscribe("workflow.done",
                           lambda *a: (self.done.append(a), self._evt.set()))

    def key_action(self, name="Press K", key="k"):
        return self.actions.create(name, "key_press", {"key": key})

    def wait_done(self, timeout=5.0):
        assert self._evt.wait(timeout), "workflow did not finish"
        self._evt.clear()
        return self.done[-1]


@pytest.fixture
def h(tmp_path):
    return Harness(tmp_path)


def wait_step(condition="process_exists", process="x.exe", title="",
              timeout_ms=1000):
    return {"type": "wait", "condition": condition, "process": process,
            "title": title, "timeout_ms": timeout_ms}


# -- condition unit checks ----------------------------------------------------
def test_process_exists_real():
    """python.exe genuinely runs while the test runs."""
    assert conditions.process_exists("python.exe")
    assert conditions.process_exists("python")        # extension optional
    assert conditions.process_exists("PYTHON.EXE")    # case-insensitive
    assert not conditions.process_exists("definitely_not_real_app.exe")
    assert not conditions.process_exists("")


def test_process_name_normalization():
    assert conditions._normalize_process("Chrome") == "chrome.exe"
    assert conditions._normalize_process(
        r"C:\Program Files\Google\Chrome\Application\chrome.exe") == \
        "chrome.exe"
    assert conditions._normalize_process('"notepad.exe"') == "notepad.exe"


def test_window_exists_mocked():
    windows = [(101, "Budget2026.xlsx - Excel", "excel.exe"),
               (102, "YouTube - Google Chrome", "chrome.exe")]

    class FakeGui:
        @staticmethod
        def IsWindowVisible(h):
            return True

        @staticmethod
        def GetWindowText(h):
            return next(t for i, t, p in windows if i == h)

        @staticmethod
        def EnumWindows(cb, _):
            for i, _, _ in windows:
                cb(i, None)

    with patch.dict("sys.modules", {"win32gui": FakeGui}), \
         patch("app.context.detector._process_name",
               lambda h: next(p for i, t, p in windows if i == h)):
        assert conditions.window_exists(process="chrome.exe")
        assert conditions.window_exists(title_pattern="*YouTube*")
        assert conditions.window_exists(process="chrome.exe",
                                        title_pattern="*YouTube*")
        assert not conditions.window_exists(process="chrome.exe",
                                            title_pattern="*Excel*")
        assert not conditions.window_exists(process="firefox.exe")
        assert not conditions.window_exists(title_pattern="*Gmail*")
        # substring (no wildcard) matching, same semantics as rules
        assert conditions.window_exists(title_pattern="youtube")


def test_condition_validate():
    v = conditions.validate
    assert "unknown condition" in v({"condition": "ocr"})
    assert "process name" in v({"condition": "app_running", "process": ""})
    assert "process name" in v({"condition": "process_exists"})
    assert "title pattern" in v({"condition": "window_title", "title": ""})
    assert "process or a title" in v({"condition": "window_exists"})
    assert v({"condition": "app_running", "process": "chrome.exe"}) is None
    assert v({"condition": "window_exists", "title": "*x*"}) is None


def test_condition_check_never_raises():
    with patch("app.context.conditions.process_exists",
               side_effect=RuntimeError("boom")):
        assert conditions.check({"condition": "process_exists",
                                 "process": "x.exe"}) is False
    assert conditions.check({"condition": "nope"}) is False


def test_describe():
    assert "process x.exe" in conditions.describe(wait_step())
    s = wait_step("window_title", process="", title="*YouTube*")
    assert "*YouTube*" in conditions.describe(s)


# -- repo validation ----------------------------------------------------------
def test_wait_step_repo_validation(h):
    ok = [wait_step(timeout_ms=1000)]
    assert h.workflows.validate(ok) is None
    assert "unknown condition" in h.workflows.validate(
        [wait_step(condition="ocr")])
    assert "process name" in h.workflows.validate(
        [wait_step(process="")])
    assert "at least" in h.workflows.validate(
        [wait_step(timeout_ms=10)])
    assert "too long" in h.workflows.validate(
        [wait_step(timeout_ms=999_999)])


# -- engine: wait steps -------------------------------------------------------
def test_wait_satisfied_immediately_continues(h):
    keys = []
    aid = h.key_action()
    wid = h.workflows.create("W", [wait_step(timeout_ms=5000),
                                   {"type": "action", "action_id": aid}])
    with patch("app.context.conditions.check", return_value=True), \
         patch.object(iw, "key_press", lambda k: keys.append(k)):
        t0 = time.monotonic()
        h.engine.start(wid)
        _, status, _ = h.wait_done()
        elapsed = time.monotonic() - t0
    assert status == "completed" and keys == ["k"]
    assert elapsed < 1.0            # did not sit out the 5 s timeout


def test_wait_satisfied_later_continues_immediately(h):
    keys = []
    aid = h.key_action()
    wid = h.workflows.create("W", [wait_step(timeout_ms=5000),
                                   {"type": "action", "action_id": aid}])
    flip_at = time.monotonic() + 0.2
    with patch("app.context.conditions.check",
               side_effect=lambda c: time.monotonic() >= flip_at), \
         patch.object(iw, "key_press", lambda k: keys.append(k)):
        t0 = time.monotonic()
        h.engine.start(wid)
        _, status, _ = h.wait_done()
        elapsed = time.monotonic() - t0
    assert status == "completed" and keys == ["k"]
    assert 0.2 <= elapsed < 1.5     # continued right after the flip


def test_wait_timeout_fails_and_stops(h):
    keys = []
    aid = h.key_action()
    wid = h.workflows.create("W", [wait_step(timeout_ms=200),
                                   {"type": "action", "action_id": aid}])
    with patch("app.context.conditions.check", return_value=False), \
         patch.object(iw, "key_press", lambda k: keys.append(k)):
        h.engine.start(wid)
        _, status, detail = h.wait_done()
    assert status == "failed"
    assert "not satisfied before timeout" in detail
    assert keys == []               # step 2 never ran


def test_wait_polling_is_modest(h):
    calls = []
    h.engine.POLL_S = 0.1
    wid = h.workflows.create("W", [wait_step(timeout_ms=500)])
    with patch("app.context.conditions.check",
               side_effect=lambda c: (calls.append(1), False)[1]):
        h.engine.start(wid)
        h.wait_done()
    assert 3 <= len(calls) <= 10    # ~5 Hz polling, not camera-rate


def test_wait_cancel_via_emergency_stop(h):
    keys = []
    aid = h.key_action()
    wid = h.workflows.create("W", [wait_step(timeout_ms=30_000),
                                   {"type": "action", "action_id": aid}])
    with patch("app.context.conditions.check", return_value=False), \
         patch.object(iw, "key_press", lambda k: keys.append(k)):
        h.engine.start(wid)
        time.sleep(0.1)
        t0 = time.monotonic()
        h.bus.publish("control.enabled", False)   # motion off / E-stop
        _, status, _ = h.wait_done()
        assert time.monotonic() - t0 < 1.0        # immediate, not timeout
    assert status == "cancelled" and keys == []


def test_wait_non_blocking_start(h):
    wid = h.workflows.create("W", [wait_step(timeout_ms=2000)])
    with patch("app.context.conditions.check", return_value=False):
        t0 = time.monotonic()
        ok = h.executor.execute(ActionSpec(type="workflow",
                                           params={"workflow_id": wid}))
        assert ok and time.monotonic() - t0 < 0.3
        h.engine.cancel_all("test")
        h.wait_done()


def test_wait_no_log_spam(h, caplog):
    wid = h.workflows.create("W", [wait_step(timeout_ms=500)])
    with patch("app.context.conditions.check", return_value=False), \
         caplog.at_level(logging.INFO, logger="app.runtime.workflows"):
        h.engine.start(wid)
        h.wait_done()
    waiting_lines = [r for r in caplog.records
                     if "waiting for" in r.getMessage().lower()]
    assert len(waiting_lines) == 1  # one line per wait, not per poll


def test_mixed_steps_action_wait_action(h):
    keys = []
    a1, a2 = h.key_action("A1", "a"), h.key_action("A2", "b")
    wid = h.workflows.create("W", [
        {"type": "action", "action_id": a1},
        wait_step(timeout_ms=5000),
        {"type": "delay", "ms": 30},
        {"type": "action", "action_id": a2}])
    with patch("app.context.conditions.check", return_value=True), \
         patch.object(iw, "key_press", lambda k: keys.append(k)):
        h.engine.start(wid)
        _, status, _ = h.wait_done()
    assert status == "completed" and keys == ["a", "b"]


# -- runtime integration ------------------------------------------------------
@pytest.fixture
def ctl(tmp_path, monkeypatch):
    from app.core.config import Config
    from app.runtime.controller import MotionController
    monkeypatch.setenv("MGA_DATA_DIR", str(tmp_path))
    cfg = Config()
    cfg.default_cooldown_ms = 0
    cfg.action_global_cooldown_ms = 0
    db = Database(tmp_path / "rt.db")
    bus = EventBus()
    c = MotionController(cfg, db, bus)
    c.workflows.POLL_S = 0.03
    pm = c.profile_manager
    g = pm.profiles.by_name("Global")
    for r in pm.rules.for_profile(g.id):
        pm.rules.delete(r.id)
    c.reload_rules()
    c.set_motion_enabled(True)
    return c, bus


def ev(gesture, phase, source="primitive"):
    return GestureEvent(gesture=gesture, phase=phase, confidence=0.9,
                        handedness="Right", hand_x=0.5, hand_y=0.5,
                        timestamp=time.monotonic(), source=source)


def wait_for(pred, timeout=3.0):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        if pred():
            return True
        time.sleep(0.01)
    return False


def test_compound_gesture_smart_workflow(ctl):
    """Double pinch → wait-condition workflow; primitive suppressed."""
    c, bus = ctl
    keys = []
    pm = c.profile_manager
    aid = pm.actions.create("K", "key_press", {"key": "w"})
    wid = c.workflow_repo.create("Smart", [wait_step(timeout_ms=5000),
                                           {"type": "action",
                                            "action_id": aid}])
    a_wf = pm.actions.create("Smart", "workflow", {"workflow_id": wid})
    a_click = pm.actions.create("X", "key_press", {"key": "x"})
    g = pm.profiles.by_name("Global")
    pm.rules.create(g.id, "pinch", a_click)
    c.compound_gestures.create(
        "double_pinch", [{"type": "gesture", "gesture": "pinch"}] * 2,
        step_timeout_ms=250, cooldown_ms=0)
    pm.rules.create(g.id, "double_pinch", a_wf)
    c.reload_rules()
    c.context.current = Context(application="notepad",
                                process="notepad.exe", window_title="x")
    with patch("app.context.conditions.check", return_value=True), \
         patch.object(iw, "key_press", lambda k: keys.append(k)):
        bus.publish("gesture.event", ev("pinch", "start"))
        bus.publish("gesture.event", ev("pinch", "end"))
        time.sleep(0.05)
        bus.publish("gesture.event", ev("pinch", "start"))
        bus.publish("gesture.event", ev("pinch", "end"))
        assert wait_for(lambda: "w" in keys)
        time.sleep(0.5)
    assert keys == ["w"]


def test_context_aware_smart_workflow(ctl):
    c, bus = ctl
    keys = []
    pm = c.profile_manager
    for name, key, proc in (("WF Chrome", "c", "chrome.exe"),
                            ("WF Global", "g", "")):
        aid = pm.actions.create(f"{name} key", "key_press", {"key": key})
        wid = c.workflow_repo.create(name, [wait_step(timeout_ms=5000),
                                            {"type": "action",
                                             "action_id": aid}])
        a = pm.actions.create(name, "workflow", {"workflow_id": wid})
        if proc:
            p = pm.profiles.create("Chrome", "application",
                                   [{"process_name": proc}])
            pm.rules.create(p, "pinch", a)
        else:
            g = pm.profiles.by_name("Global")
            pm.rules.create(g.id, "pinch", a)
    c.reload_rules()
    with patch("app.context.conditions.check", return_value=True), \
         patch.object(iw, "key_press", lambda k: keys.append(k)):
        c.context.current = Context(application="chrome",
                                    process="chrome.exe", window_title="t")
        bus.publish("gesture.event", ev("pinch", "start"))
        bus.publish("gesture.event", ev("pinch", "end"))
        assert wait_for(lambda: keys == ["c"])
        c.context.current = Context(application="notepad",
                                    process="notepad.exe", window_title="t")
        bus.publish("gesture.event", ev("pinch", "start"))
        bus.publish("gesture.event", ev("pinch", "end"))
        assert wait_for(lambda: keys == ["c", "g"])


# -- locked-workstation logging dampening ------------------------------------
def test_snapshot_failure_logs_once(caplog):
    from app.context import detector
    detector._snapshot_failing = False
    with patch.object(detector.win32gui, "GetForegroundWindow",
                      side_effect=OSError("Access is denied")), \
         caplog.at_level(logging.DEBUG, logger="app.context.detector"):
        for _ in range(5):
            ctx = detector.snapshot()
            assert ctx.application == "desktop"   # graceful fallback
    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(warnings) == 1                     # one per failure streak
    # recovery resets the streak → next failure warns again
    caplog.clear()
    detector.snapshot()                           # real call succeeds
    assert detector._snapshot_failing is False
    with patch.object(detector.win32gui, "GetForegroundWindow",
                      side_effect=OSError("Access is denied")), \
         caplog.at_level(logging.DEBUG, logger="app.context.detector"):
        detector.snapshot()
    assert any(r.levelno == logging.WARNING for r in caplog.records)
