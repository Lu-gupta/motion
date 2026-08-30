"""Workflow / action-sequence engine tests (spec §24).

No real applications, URLs or input are executed — process/input layers
are patched. Timing uses events, not fixed sleeps, wherever possible.
"""
import threading
import time
from unittest.mock import patch

import pytest

import app.actions.input_win as iw
from app.actions import system_actions
from app.actions.executor import ActionExecutor
from app.core.config import Config
from app.core.events import EventBus
from app.core.types import ActionSpec, Context, GestureEvent
from app.data.db import Database
from app.data.repository import ActionRepo, WorkflowRepo
from app.runtime.controller import MotionController
from app.runtime.workflows import WorkflowEngine


# -- harness -----------------------------------------------------------------
class Harness:
    """Bare engine: bus + executor + repos, no camera/UI."""

    def __init__(self, tmp_path):
        self.db = Database(tmp_path / "wf.db")
        self.bus = EventBus()
        self.actions = ActionRepo(self.db)
        self.workflows = WorkflowRepo(self.db)
        self.executor = ActionExecutor(self.bus)
        self.engine = WorkflowEngine(self.bus, self.executor, self.actions,
                                     self.workflows)
        self.executor.workflows = self.engine
        self.progress = []
        self.done = []
        self._done_evt = threading.Event()
        self.bus.subscribe("workflow.progress",
                           lambda *a: self.progress.append(a))
        self.bus.subscribe("workflow.done",
                           lambda *a: (self.done.append(a),
                                       self._done_evt.set()))

    def key_action(self, name="Press K", key="k"):
        return self.actions.create(name, "key_press", {"key": key})

    def wait_done(self, timeout=3.0):
        assert self._done_evt.wait(timeout), "workflow did not finish"
        self._done_evt.clear()
        return self.done[-1]


@pytest.fixture
def h(tmp_path):
    return Harness(tmp_path)


# -- 1-3: CRUD / ordering / validation ---------------------------------------
def test_workflow_crud(h):
    aid = h.key_action()
    wid = h.workflows.create("W", [{"type": "action", "action_id": aid},
                                   {"type": "delay", "ms": 100}])
    wf = h.workflows.get(wid)
    assert wf.name == "W" and len(wf.steps) == 2 and wf.enabled
    h.workflows.update(wid, name="W2", enabled=False)
    assert h.workflows.by_name("W2").enabled is False
    h.workflows.update(wid, steps=[{"type": "delay", "ms": 5}])
    assert h.workflows.get(wid).steps == [{"type": "delay", "ms": 5}]
    h.workflows.delete(wid)
    assert h.workflows.get(wid) is None


def test_step_reorder_persists(h):
    a1, a2 = h.key_action("A1", "a"), h.key_action("A2", "b")
    wid = h.workflows.create("W", [{"type": "action", "action_id": a1},
                                   {"type": "action", "action_id": a2}])
    steps = h.workflows.get(wid).steps
    h.workflows.update(wid, steps=list(reversed(steps)))
    assert [s["action_id"] for s in h.workflows.get(wid).steps] == [a2, a1]


def test_validation(h):
    aid = h.key_action()
    v = h.workflows.validate
    assert "at least one" in v([])
    assert "too many" in v([{"type": "delay", "ms": 1}] * 31)
    assert "bad step type" in v([{"type": "teleport"}])
    assert "does not exist" in v([{"type": "action", "action_id": 9999}])
    assert "positive" in v([{"type": "delay", "ms": 0}])
    assert "too long" in v([{"type": "delay", "ms": 120_000}])
    assert v([{"type": "action", "action_id": aid}]) is None
    with pytest.raises(ValueError):
        h.workflows.create("bad", [])


def test_workflows_cannot_nest(h):
    aid = h.key_action()
    wid = h.workflows.create("Inner", [{"type": "action", "action_id": aid}])
    wf_action = h.actions.create("Inner (wf)", "workflow",
                                 {"workflow_id": wid})
    assert "nest" in h.workflows.validate(
        [{"type": "action", "action_id": wf_action}])


def test_sequence_cannot_contain_workflow(h):
    assert "workflow" in h.executor.validate(
        "sequence", {"steps": [{"type": "workflow", "params": {}}]})
    ok = h.executor.execute(ActionSpec(type="sequence", params={
        "steps": [{"type": "workflow", "params": {"workflow_id": 1}}]}))
    assert not ok


# -- 4-6: serialization / reference resolution -------------------------------
def test_action_reference_resolved_fresh_each_run(h):
    """Hot reload: editing a referenced action affects the next run."""
    keys = []
    aid = h.key_action("A", "a")
    wid = h.workflows.create("W", [{"type": "action", "action_id": aid}])
    with patch.object(iw, "key_press", lambda k: keys.append(k)):
        h.engine.start(wid)
        h.wait_done()
        h.actions.update(aid, params={"key": "z"})
        h.engine.start(wid)
        h.wait_done()
    assert keys == ["a", "z"]


def test_workflow_action_type_through_executor(h):
    aid = h.key_action()
    wid = h.workflows.create("W", [{"type": "action", "action_id": aid}])
    assert h.executor.validate("workflow", {"workflow_id": wid}) is None
    assert "does not exist" in h.executor.validate(
        "workflow", {"workflow_id": 999})
    keys = []
    with patch.object(iw, "key_press", lambda k: keys.append(k)):
        ok = h.executor.execute(ActionSpec(type="workflow",
                                           params={"workflow_id": wid},
                                           name="W"))
        assert ok
        assert h.wait_done()[1] == "completed"
    assert keys == ["k"]


def test_unknown_and_disabled_workflow_fail_cleanly(h):
    ok = h.executor.execute(ActionSpec(type="workflow",
                                       params={"workflow_id": 424242}))
    assert not ok
    aid = h.key_action()
    wid = h.workflows.create("W", [{"type": "action", "action_id": aid}])
    h.workflows.update(wid, enabled=False)
    started, reason = h.engine.start(wid)
    assert not started and "disabled" in reason


# -- 7-9: execution ----------------------------------------------------------
def test_sequential_execution_with_delay(h):
    order = []
    a1, a2 = h.key_action("A1", "a"), h.key_action("A2", "b")
    wid = h.workflows.create("W", [
        {"type": "action", "action_id": a1},
        {"type": "delay", "ms": 60},
        {"type": "action", "action_id": a2}])
    with patch.object(iw, "key_press",
                      lambda k: order.append((k, time.monotonic()))):
        h.engine.start(wid)
        name, status, _ = h.wait_done()
    assert name == "W" and status == "completed"
    assert [k for k, _ in order] == ["a", "b"]
    # the 60 ms delay actually paused between steps. Lower bound is a
    # fraction of the delay so scheduling/GC jitter on either mocked-
    # callback timestamp cannot flake it, while a skipped delay (~0 ms)
    # still fails loudly. (Event.wait(0.06) is the real, correct wait.)
    assert order[1][1] - order[0][1] >= 0.04
    # progress events: step 1, 2, 3 in order
    assert [p[1] for p in h.progress] == [1, 2, 3]
    assert h.progress[1][3].startswith("Wait")


def test_execution_is_non_blocking(h):
    aid = h.key_action()
    wid = h.workflows.create("W", [{"type": "delay", "ms": 400},
                                   {"type": "action", "action_id": aid}])
    keys = []
    with patch.object(iw, "key_press", lambda k: keys.append(k)):
        t0 = time.monotonic()
        ok = h.executor.execute(ActionSpec(type="workflow",
                                           params={"workflow_id": wid}))
        elapsed = time.monotonic() - t0
        assert ok and elapsed < 0.3               # returned during the delay
        assert keys == []                         # step 2 not yet executed
        assert h.engine.running_ids() == [wid]
        h.wait_done()
    assert keys == ["k"]


def test_failure_stops_workflow(h, tmp_path):
    keys = []
    ghost = h.actions.create("Ghost", "launch_app",
                             {"path": str(tmp_path / "ghost.exe")})
    after = h.key_action("After", "x")
    wid = h.workflows.create("W", [
        {"type": "action", "action_id": ghost},
        {"type": "action", "action_id": after}])
    with patch.object(iw, "key_press", lambda k: keys.append(k)):
        h.engine.start(wid)
        name, status, detail = h.wait_done()
    assert status == "failed"
    assert "step 1" in detail and "Ghost" in detail
    assert keys == []                             # later steps never ran


def test_deleted_step_action_fails(h):
    aid = h.key_action()
    wid = h.workflows.create("W", [{"type": "action", "action_id": aid}])
    h.actions.delete(aid)
    h.engine.start(wid)
    assert "no longer exists" in h.wait_done()[2]


# -- 10-13: cancellation / duplicates ----------------------------------------
def test_cancel_during_delay(h):
    keys = []
    aid = h.key_action()
    wid = h.workflows.create("W", [{"type": "delay", "ms": 2000},
                                   {"type": "action", "action_id": aid}])
    with patch.object(iw, "key_press", lambda k: keys.append(k)):
        h.engine.start(wid)
        time.sleep(0.05)
        t0 = time.monotonic()
        h.engine.cancel_all("test")
        _, status, _ = h.wait_done()
        assert time.monotonic() - t0 < 0.5        # did not wait out the delay
    assert status == "cancelled" and keys == []


def test_motion_off_event_cancels(h):
    keys = []
    aid = h.key_action()
    wid = h.workflows.create("W", [{"type": "delay", "ms": 2000},
                                   {"type": "action", "action_id": aid}])
    with patch.object(iw, "key_press", lambda k: keys.append(k)):
        h.engine.start(wid)
        time.sleep(0.05)
        h.bus.publish("control.enabled", False)   # emergency stop path
        _, status, _ = h.wait_done()
    assert status == "cancelled" and keys == []


def test_duplicate_instance_prevented(h):
    aid = h.key_action()
    wid = h.workflows.create("W", [{"type": "delay", "ms": 300},
                                   {"type": "action", "action_id": aid}])
    with patch.object(iw, "key_press", lambda k: None):
        started, _ = h.engine.start(wid)
        assert started
        again, reason = h.engine.start(wid)
        assert not again and "already running" in reason
        # surfaced as a failed action through the executor too
        results = []
        h.bus.subscribe("action.executed", lambda *a: results.append(a))
        ok = h.executor.execute(ActionSpec(type="workflow",
                                           params={"workflow_id": wid}))
        assert not ok and "already running" in results[-1][2]
        h.wait_done()
        # finished → may start again
        started2, _ = h.engine.start(wid)
        assert started2
        h.wait_done()


def test_two_different_workflows_may_run_concurrently(h):
    a = h.key_action()
    w1 = h.workflows.create("W1", [{"type": "delay", "ms": 200},
                                   {"type": "action", "action_id": a}])
    w2 = h.workflows.create("W2", [{"type": "delay", "ms": 200},
                                   {"type": "action", "action_id": a}])
    with patch.object(iw, "key_press", lambda k: None):
        assert h.engine.start(w1)[0] and h.engine.start(w2)[0]
        assert sorted(h.engine.running_ids()) == sorted([w1, w2])
        h.wait_done()
        if h.engine.running_ids():
            h.wait_done()


# -- open_url validation ------------------------------------------------------
def test_open_url_validation():
    assert system_actions.validate_url("https://youtube.com") is None
    assert system_actions.validate_url("http://x.test/path?q=1") is None
    assert "required" in system_actions.validate_url("")
    assert "http" in system_actions.validate_url("youtube.com")
    assert "http" in system_actions.validate_url("file:///C:/x")
    assert "http" in system_actions.validate_url('cmd /c "evil"')
    ex = ActionExecutor()
    assert ex.validate("open_url", {"url": "notaurl"})
    assert ex.validate("open_url", {"url": "https://youtube.com"}) is None


def test_open_url_uses_default_browser_not_shell(h):
    url_action = h.actions.create("YouTube", "open_url",
                                  {"url": "https://youtube.com"})
    wid = h.workflows.create("W", [{"type": "action",
                                    "action_id": url_action}])
    opened = []
    with patch("app.actions.system_actions.webbrowser.open",
               lambda u: opened.append(u)):
        h.engine.start(wid)
        assert h.wait_done()[1] == "completed"
    assert opened == ["https://youtube.com"]


# -- runtime integration ------------------------------------------------------
@pytest.fixture
def ctl(tmp_path, monkeypatch):
    monkeypatch.setenv("MGA_DATA_DIR", str(tmp_path))
    cfg = Config()
    cfg.default_cooldown_ms = 0
    cfg.action_global_cooldown_ms = 0
    db = Database(tmp_path / "rt.db")
    bus = EventBus()
    c = MotionController(cfg, db, bus)
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


def set_ctx(c, process, title=""):
    app = process[:-4] if process.endswith(".exe") else process
    c.context.current = Context(application=app, process=process,
                                window_title=title)


def make_workflow_action(c, name, keys=("k",)):
    """Workflow of key_press steps + matching named workflow action."""
    pm = c.profile_manager
    steps = []
    for i, k in enumerate(keys):
        aid = pm.actions.create(f"{name} step{i}", "key_press", {"key": k})
        steps.append({"type": "action", "action_id": aid})
    wid = c.workflow_repo.create(name, steps)
    return pm.actions.create(name, "workflow", {"workflow_id": wid}), wid


def wait_for(pred, timeout=2.0):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        if pred():
            return True
        time.sleep(0.01)
    return False


def test_gesture_to_workflow_global_mapping(ctl):
    c, bus = ctl
    keys = []
    a, _ = make_workflow_action(c, "WF", keys=("a", "b"))
    g = c.profile_manager.profiles.by_name("Global")
    c.profile_manager.rules.create(g.id, "pinch", a)
    c.reload_rules()
    set_ctx(c, "notepad.exe")
    with patch.object(iw, "key_press", lambda k: keys.append(k)):
        bus.publish("gesture.event", ev("pinch", "start"))
        bus.publish("gesture.event", ev("pinch", "end"))
        assert wait_for(lambda: keys == ["a", "b"])


def test_context_specific_workflow_precedence(ctl):
    c, bus = ctl
    keys = []
    pm = c.profile_manager
    a_global, _ = make_workflow_action(c, "Global WF", keys=("g",))
    a_chrome, _ = make_workflow_action(c, "Chrome WF", keys=("c",))
    a_window, _ = make_workflow_action(c, "Window WF", keys=("w",))
    g = pm.profiles.by_name("Global")
    pm.rules.create(g.id, "pinch", a_global)
    chrome = pm.profiles.create("Chrome", "application",
                                [{"process_name": "chrome.exe"}])
    pm.rules.create(chrome, "pinch", a_chrome)
    pm.rules.create(chrome, "pinch", a_window, window_pattern="*Gmail*")
    c.reload_rules()
    with patch.object(iw, "key_press", lambda k: keys.append(k)):
        set_ctx(c, "chrome.exe", "Gmail - Chrome")
        bus.publish("gesture.event", ev("pinch", "start"))
        bus.publish("gesture.event", ev("pinch", "end"))
        assert wait_for(lambda: keys == ["w"])
        set_ctx(c, "chrome.exe", "News - Chrome")
        bus.publish("gesture.event", ev("pinch", "start"))
        bus.publish("gesture.event", ev("pinch", "end"))
        assert wait_for(lambda: keys == ["w", "c"])
        set_ctx(c, "notepad.exe")
        bus.publish("gesture.event", ev("pinch", "start"))
        bus.publish("gesture.event", ev("pinch", "end"))
        assert wait_for(lambda: keys == ["w", "c", "g"])


def test_compound_triggers_workflow_arbitration_suppresses_primitive(ctl):
    """Double pinch → workflow exactly once, single-pinch action suppressed."""
    c, bus = ctl
    keys = []
    pm = c.profile_manager
    a_wf, _ = make_workflow_action(c, "WF", keys=("w",))
    a_click = pm.actions.create("K", "key_press", {"key": "x"})
    g = pm.profiles.by_name("Global")
    pm.rules.create(g.id, "pinch", a_click)
    c.compound_gestures.create(
        "double_pinch", [{"type": "gesture", "gesture": "pinch"}] * 2,
        step_timeout_ms=250, cooldown_ms=0)
    pm.rules.create(g.id, "double_pinch", a_wf)
    c.reload_rules()
    set_ctx(c, "notepad.exe")
    with patch.object(iw, "key_press", lambda k: keys.append(k)):
        bus.publish("gesture.event", ev("pinch", "start"))
        bus.publish("gesture.event", ev("pinch", "end"))
        time.sleep(0.05)
        bus.publish("gesture.event", ev("pinch", "start"))
        bus.publish("gesture.event", ev("pinch", "end"))
        assert wait_for(lambda: "w" in keys)
        time.sleep(0.5)   # wait out arbitration windows
    assert keys == ["w"]                          # no suppressed 'x'


def test_motion_off_blocks_workflow_start(ctl):
    c, bus = ctl
    keys = []
    a, _ = make_workflow_action(c, "WF", keys=("a",))
    g = c.profile_manager.profiles.by_name("Global")
    c.profile_manager.rules.create(g.id, "pinch", a)
    c.reload_rules()
    c.set_motion_enabled(False)
    set_ctx(c, "notepad.exe")
    with patch.object(iw, "key_press", lambda k: keys.append(k)):
        bus.publish("gesture.event", ev("pinch", "start"))
        time.sleep(0.15)
    assert keys == []


def test_emergency_stop_cancels_running_workflow(ctl):
    c, bus = ctl
    keys = []
    pm = c.profile_manager
    a1 = pm.actions.create("K1", "key_press", {"key": "1"})
    a2 = pm.actions.create("K2", "key_press", {"key": "2"})
    wid = c.workflow_repo.create("Long WF", [
        {"type": "action", "action_id": a1},
        {"type": "delay", "ms": 2000},
        {"type": "action", "action_id": a2}])
    done = []
    evt = threading.Event()
    bus.subscribe("workflow.done", lambda *a: (done.append(a), evt.set()))
    with patch.object(iw, "key_press", lambda k: keys.append(k)):
        c.workflows.start(wid)
        assert wait_for(lambda: keys == ["1"])
        c.emergency_disable()
        assert evt.wait(2.0)
        time.sleep(0.1)
    assert done[-1][1] == "cancelled"
    assert keys == ["1"]                          # step 3 never ran


def test_safe_recognition_resolves_without_running(ctl):
    c, bus = ctl
    keys = []
    a, _ = make_workflow_action(c, "WF", keys=("a",))
    g = c.profile_manager.profiles.by_name("Global")
    c.profile_manager.rules.create(g.id, "pinch", a)
    c.reload_rules()
    set_ctx(c, "notepad.exe")
    with patch.object(iw, "key_press", lambda k: keys.append(k)):
        match = c.rules.resolve("pinch", c.context.current, (1920, 1080))
        assert match is not None
        assert match.action.type == "workflow"
        assert match.action.name == "WF"
        time.sleep(0.1)
    assert keys == []                             # resolve alone runs nothing


def test_disabled_rule_no_workflow(ctl):
    c, bus = ctl
    keys = []
    a, _ = make_workflow_action(c, "WF", keys=("a",))
    g = c.profile_manager.profiles.by_name("Global")
    rid = c.profile_manager.rules.create(g.id, "pinch", a)
    c.profile_manager.rules.update(rid, enabled=False)
    c.reload_rules()
    set_ctx(c, "notepad.exe")
    with patch.object(iw, "key_press", lambda k: keys.append(k)):
        bus.publish("gesture.event", ev("pinch", "start"))
        time.sleep(0.15)
    assert keys == []


def test_workflow_hot_reload_steps(ctl):
    c, bus = ctl
    keys = []
    pm = c.profile_manager
    a, wid = make_workflow_action(c, "WF", keys=("a",))
    g = pm.profiles.by_name("Global")
    pm.rules.create(g.id, "pinch", a)
    c.reload_rules()
    set_ctx(c, "notepad.exe")
    with patch.object(iw, "key_press", lambda k: keys.append(k)):
        bus.publish("gesture.event", ev("pinch", "start"))
        bus.publish("gesture.event", ev("pinch", "end"))
        assert wait_for(lambda: keys == ["a"])
        nid = pm.actions.create("New step", "key_press", {"key": "n"})
        c.workflow_repo.update(wid, steps=[
            {"type": "action", "action_id": nid}])
        # no restart, no reload call needed — next run reads fresh steps
        bus.publish("gesture.event", ev("pinch", "start"))
        bus.publish("gesture.event", ev("pinch", "end"))
        assert wait_for(lambda: keys == ["a", "n"])


def test_export_import_workflow_round_trip(tmp_path, monkeypatch):
    monkeypatch.setenv("MGA_DATA_DIR", str(tmp_path))
    from app.profiles.manager import ProfileManager
    pm = ProfileManager(Database(tmp_path / "p.db"))
    pm.seed_defaults()
    step_a = pm.actions.create("Step A", "key_press", {"key": "a"})
    wid = pm.workflows.create("My WF", [
        {"type": "action", "action_id": step_a},
        {"type": "delay", "ms": 1500}])
    wf_action = pm.actions.create("My WF", "workflow", {"workflow_id": wid})
    pid = pm.profiles.create("X", "application",
                             [{"process_name": "x.exe"}])
    pm.rules.create(pid, "pinch", wf_action)
    f = tmp_path / "x.json"
    pm.export_profile(pid, f)
    # simulate a fresh machine: wipe actions + workflows
    for r in pm.rules.for_profile(pid):
        pm.rules.delete(r.id)
    pm.profiles.delete(pid)
    pm.actions.delete(wf_action)
    pm.actions.delete(step_a)
    pm.workflows.delete(wid)
    nid = pm.import_profile(f)
    rule = pm.rules.for_profile(nid)[0]
    a = pm.actions.get(rule.action_id)
    assert a.type == "workflow"
    new_wf = pm.workflows.get(a.params["workflow_id"])
    assert new_wf is not None
    assert new_wf.steps[1] == {"type": "delay", "ms": 1500}
    ref = pm.actions.get(new_wf.steps[0]["action_id"])
    assert ref.name == "Step A" and ref.params == {"key": "a"}
