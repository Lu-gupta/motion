"""Workflow recorder / action capture (spec §16). The Win32 capture
backend is replaced by a deterministic FakeCapture; the recorder,
the pure event→step converter, materialization, execution, export/import
and safety paths are all real.
"""
import threading
import time
from unittest.mock import patch

import pytest

import app.actions.input_win as iw
from app.core.events import EventBus
from app.data.db import Database
from app.actions.executor import ActionExecutor
from app.profiles.manager import ProfileManager
from app.runtime.recorder import (WorkflowRecorder, build_steps,
                                   SECURE_PLACEHOLDER)
from app.runtime.workflows import WorkflowEngine


# -- fake capture backend -----------------------------------------------------
class FakeCapture:
    def __init__(self, callback):
        self.callback = callback
        self.started = False
        self.stopped = False

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def feed(self, event):
        self.callback(event)


def make_recorder():
    bus = EventBus()
    holder = {}

    def factory(cb):
        cap = FakeCapture(cb)
        holder["cap"] = cap
        return cap
    rec = WorkflowRecorder(bus, capture_factory=factory)
    return rec, holder, bus


def resolver(process):
    table = {"chrome.exe": ("Google Chrome", r"C:\c\chrome.exe"),
             "notepad.exe": ("Notepad", r"C:\Windows\notepad.exe")}
    return table.get(process)


def crit(name="Search", control_type="Edit", process="chrome.exe",
         title="YouTube - Google Chrome", automation_id=""):
    return {"process": process, "title": title,
            "control_type": control_type, "name": name,
            "automation_id": automation_id}


def click(criteria=None, double=False, secure=False):
    c = criteria or crit()
    return {"kind": "click", "process": c["process"], "title": c["title"],
            "double": double, "secure": secure, "criteria": c}


def text(value, criteria=None, secure=False):
    c = criteria or crit()
    return {"kind": "text", "process": c["process"], "secure": secure,
            "text": value, "criteria": c}


def key(vk):
    return {"kind": "key", "vk": vk}


def bs(events, **kw):
    kw.setdefault("resolve", resolver)
    return build_steps(events, **kw)


# -- 1-4, 19, 27-29: recorder lifecycle --------------------------------------
def test_recorder_starts():
    rec, holder, _ = make_recorder()
    ok, reason = rec.start()
    assert ok, reason
    assert rec.recording and holder["cap"].started


def test_recorder_stops_returns_events():
    rec, holder, _ = make_recorder()
    rec.start()
    holder["cap"].feed(click())
    events = rec.stop()
    assert not rec.recording and holder["cap"].stopped
    assert len(events) == 1


def test_recorder_pause_blocks_capture():
    rec, holder, _ = make_recorder()
    rec.start()
    rec.pause()
    holder["cap"].feed(click())          # ignored while paused
    assert rec.stop() == []


def test_recorder_resume_continues():
    rec, holder, _ = make_recorder()
    rec.start()
    rec.pause()
    holder["cap"].feed(click())
    rec.resume()
    holder["cap"].feed(key("enter"))
    events = rec.stop()
    assert [e["kind"] for e in events] == ["key"]


def test_recorder_cancel_clears_events():
    rec, holder, _ = make_recorder()
    rec.start()
    holder["cap"].feed(click())
    rec.cancel()
    assert not rec.recording
    assert rec.stop() == []


def test_emergency_stop_cancels_recording():
    rec, holder, bus = make_recorder()
    rec.start()
    holder["cap"].feed(click())
    bus.publish("control.enabled", False)   # E-stop / motion off
    assert not rec.recording
    assert holder["cap"].stopped


def test_shutdown_during_recording():
    rec, holder, _ = make_recorder()
    rec.start()
    holder["cap"].feed(text("hi"))
    rec.cancel()                            # shutdown path calls cancel()
    assert not rec.recording and holder["cap"].stopped


def test_concurrent_recorder_prevented():
    rec, holder, _ = make_recorder()
    assert rec.start()[0]
    ok, reason = rec.start()
    assert not ok and "already in progress" in reason


# -- 5-10, 16: capture → semantic steps --------------------------------------
def test_application_launch_captured():
    steps = bs([{"kind": "launch", "process": "chrome.exe"}])
    assert steps[0]["type"] == "action"
    assert steps[0]["action"]["type"] == "launch_app"
    assert steps[1] == {"type": "wait", "condition": "app_running",
                        "process": "chrome.exe", "title": "",
                        "timeout_ms": 10_000}


def test_launch_without_known_path_still_waits():
    steps = bs([{"kind": "launch", "process": "mystery.exe"}])
    assert [s.get("condition") for s in steps] == ["app_running"]  # no action


def test_window_transition_captured_as_semantic_wait():
    steps = bs([{"kind": "launch", "process": "chrome.exe"},
                {"kind": "foreground", "process": "chrome.exe",
                 "title": "YouTube - Google Chrome"}])
    waits = [s for s in steps if s["type"] == "wait"]
    assert waits[-1]["condition"] == "window_title"
    assert waits[-1]["title"] == "*YouTube*"       # distinctive, not raw ms
    assert not any(s["type"] == "delay" for s in steps)


def test_transition_without_title_uses_window_exists():
    steps = bs([{"kind": "launch", "process": "notepad.exe"},
                {"kind": "foreground", "process": "notepad.exe",
                 "title": ""}])
    assert steps[-1]["condition"] == "window_exists"


def test_click_captured_as_ui_find_and_click():
    steps = bs([click(crit(name="Submit", control_type="Button"))])
    assert steps[0]["type"] == "ui_find"
    assert steps[0]["control_type"] == "Button"
    assert steps[0]["name"] == "Submit"
    assert steps[1] == {"type": "ui_click", "ref": steps[0]["store"]}


def test_ui_automation_target_carried_into_find():
    c = crit(name="Search", automation_id="sb-1", process="chrome.exe")
    steps = bs([click(c)])
    f = steps[0]
    assert (f["name"], f["automation_id"], f["process"]) == (
        "Search", "sb-1", "chrome.exe")
    assert "x" not in f and "y" not in f            # never coordinates


def test_typing_captured_as_variable_and_type():
    steps = bs([text("AI automation", crit(name="Search"))])
    kinds = [s["type"] for s in steps]
    assert kinds == ["ui_find", "ui_focus", "set_var", "ui_type"]
    sv = steps[2]
    assert sv["value"] == "AI automation" and sv["value_type"] == "text"
    assert steps[3]["text"] == "{" + sv["name"] + "}"


def test_key_press_captured():
    steps = bs([key("enter")])
    assert steps[0]["action"]["type"] == "key_press"
    assert steps[0]["action"]["params"]["key"] == "enter"


# -- 12-15: filtering + consolidation ----------------------------------------
def test_click_then_type_consolidated():
    c = crit(name="Search")
    steps = bs([click(c), text("hello", c)])
    # click folds into the focus of the following type — one find, no click
    assert [s["type"] for s in steps] == [
        "ui_find", "ui_focus", "set_var", "ui_type"]
    assert not any(s["type"] == "ui_click" for s in steps)


def test_irrelevant_movement_and_unknown_ignored():
    steps = bs([{"kind": "move", "x": 5, "y": 5},
                {"not_an_event": True},
                key("tab")])
    assert [s["action"]["params"]["key"] for s in steps
            if s["type"] == "action"] == ["tab"]


def test_idle_time_produces_no_delays():
    steps = bs([click(crit(name="A", control_type="Button")),
                dict(click(crit(name="B", control_type="Button")),
                     ts=9999.0)])
    assert not any(s["type"] == "delay" for s in steps)


def test_motion_gesture_app_excluded():
    own = click(crit(name="Enable", process="motiongestureapp.exe"))
    steps = bs([own, key("enter")], exclude_processes=("motiongestureapp.exe",))
    assert all(s["type"] != "ui_find" for s in steps)   # own UI dropped
    assert steps[0]["action"]["params"]["key"] == "enter"


def test_redundant_launch_events_consolidated():
    steps = bs([{"kind": "launch", "process": "chrome.exe"},
                {"kind": "foreground", "process": "chrome.exe",
                 "title": "New Tab - Google Chrome"},
                {"kind": "foreground", "process": "chrome.exe",
                 "title": "New Tab - Google Chrome"}])
    # one launch action + one app_running wait; duplicate identical wait
    # collapsed
    assert sum(1 for s in steps
               if s.get("action", {}).get("type") == "launch_app") == 1


# -- 17-18: variables + secure -----------------------------------------------
def test_variable_names_unique():
    steps = bs([text("one", crit(name="Search")),
                text("two", crit(name="Search"))])
    names = [s["name"] for s in steps if s["type"] == "set_var"]
    assert len(names) == 2 and names[0] != names[1]


def test_secure_input_placeholder_not_value():
    steps = bs([text("hunter2secret", crit(name="Password",
                                          control_type="Edit"), secure=True)])
    sv = [s for s in steps if s["type"] == "set_var"][0]
    assert sv["value"] == SECURE_PLACEHOLDER and sv.get("secure") is True
    # the real characters never appear anywhere in the output
    assert "hunter2secret" not in repr(steps)


def test_address_bar_url_becomes_open_url():
    c = crit(name="Address and search bar", process="chrome.exe", title="")
    steps = bs([text("youtube.com", c)])
    assert len(steps) == 1
    assert steps[0]["action"]["type"] == "open_url"
    assert steps[0]["action"]["params"]["url"] == "https://youtube.com"


# -- end-to-end harness ------------------------------------------------------
class Harness:
    def __init__(self, tmp_path, name="rec"):
        self.db = Database(tmp_path / f"{name}.db")
        self.bus = EventBus()
        self.pm = ProfileManager(self.db)
        self.pm.seed_defaults()
        self.executor = ActionExecutor(self.bus)
        self.engine = WorkflowEngine(self.bus, self.executor,
                                     self.pm.actions, self.pm.workflows)
        self.engine.POLL_S = 0.03
        self.executor.workflows = self.engine
        self.done = []
        self._evt = threading.Event()
        self.bus.subscribe("workflow.done",
                           lambda *a: (self.done.append(a),
                                       self._evt.set()))

    def run(self, steps, timeout=6.0):
        wid = self.pm.workflows.create("Recorded WF", steps)
        self._evt.clear()
        ok, why = self.engine.start(wid)
        assert ok, why
        assert self._evt.wait(timeout), "did not finish"
        return self.done[-1]


@pytest.fixture
def h(tmp_path):
    return Harness(tmp_path)


def test_review_before_save_no_autosave():
    """The recorder never creates a workflow — only the review/save UI
    does. stop() + build touch no repository."""
    rec, holder, _ = make_recorder()
    rec.start()
    holder["cap"].feed(text("AI automation", crit(name="Search")))
    rec.stop()
    steps = rec.build(resolve=resolver)
    assert steps and all(s.get("type") for s in steps)
    # (no DB handle exists here at all — proves conversion is pure)


def test_malformed_capture_rejected_but_valid_survive(h):
    steps = bs([{"garbage": 1}, None, key("enter")])
    materialized = h.pm.materialize_steps(steps)
    assert h.pm.workflows.validate(materialized) is None


def test_recorded_workflow_materializes_and_validates(h):
    portable = bs([{"kind": "launch", "process": "chrome.exe"},
                   click(crit(name="Search")),
                   text("AI automation", crit(name="Search")),
                   key("enter")])
    steps = h.pm.materialize_steps(portable)
    # action steps now reference real ids
    assert steps[0]["type"] == "action" and "action_id" in steps[0]
    assert h.pm.workflows.validate(steps) is None


def test_recorded_workflow_editable(h):
    steps = h.pm.materialize_steps(bs([key("enter")]))
    steps.append({"type": "delay", "ms": 500})       # user edits freely
    assert h.pm.workflows.validate(steps) is None


def test_recorded_workflow_executes(h):
    from tests.test_uia_workflows import info
    portable = bs([click(crit(name="Search")),
                   text("AI automation", crit(name="Search")),
                   key("enter")])
    steps = h.pm.materialize_steps(portable)
    typed, keys = [], []
    with patch("app.context.uia.find_element",
               lambda c: ("ctrl", info())), \
         patch("app.context.uia.refresh_info", lambda c, cr: info()), \
         patch("app.context.uia.focus", lambda c: None), \
         patch("app.context.uia.set_text",
               lambda c, t: typed.append(t) or True), \
         patch.object(iw, "key_press", lambda k: keys.append(k)):
        _, status, detail = h.run(steps)
    assert status == "completed", detail
    assert typed == ["AI automation"]                # {var} substituted
    assert keys == ["enter"]


def test_ui_reference_revalidation_on_recorded_steps(h):
    """Recorded UI steps re-validate the target — a vanished element
    fails safely instead of acting blind."""
    steps = h.pm.materialize_steps(bs([click(crit(name="Search")),
                                       text("x", crit(name="Search"))]))
    with patch("app.context.uia.find_element",
               lambda c: ("ctrl", __import__(
                   "tests.test_uia_workflows", fromlist=["info"]).info())), \
         patch("app.context.uia.refresh_info", lambda c, cr: None):
        _, status, detail = h.run(steps)
    assert status == "failed" and "stale" in detail.lower()


def test_recorded_workflow_export_import(tmp_path):
    h = Harness(tmp_path, "eio")
    portable = bs([{"kind": "launch", "process": "chrome.exe"},
                   click(crit(name="Search")),
                   text("AI automation", crit(name="Search")),
                   key("enter")])
    steps = h.pm.materialize_steps(portable)
    wid = h.pm.workflows.create("Rec Export", steps)
    a = h.pm.actions.create("Rec Export", "workflow", {"workflow_id": wid})
    pid = h.pm.profiles.create("P", "application",
                               [{"process_name": "x.exe"}])
    h.pm.rules.create(pid, "circle", a)
    f = tmp_path / "rec.json"
    h.pm.export_profile(pid, f)
    for r in h.pm.rules.for_profile(pid):
        h.pm.rules.delete(r.id)
    h.pm.profiles.delete(pid)
    h.pm.workflows.delete(wid)
    h.pm.actions.delete(a)
    nid = h.pm.import_profile(f)
    nwf = h.pm.workflows.get(h.pm.actions.get(
        h.pm.rules.for_profile(nid)[0].action_id).params["workflow_id"])
    assert [s["type"] for s in nwf.steps] == [
        "action", "wait", "ui_find", "ui_focus", "set_var", "ui_type",
        "action"]
    assert h.pm.workflows.validate(nwf.steps) is None


def test_gesture_assignment(h):
    steps = h.pm.materialize_steps(bs([key("enter")]))
    wid = h.pm.workflows.create("Assign Me", steps)
    a = h.pm.actions.create("Assign Me", "workflow", {"workflow_id": wid})
    gid = h.pm.profiles.by_name("Global").id
    h.pm.rules.create(gid, "circle", a)
    rules = [r for r in h.pm.rules.all() if r.action_id == a]
    assert rules and rules[0].gesture == "circle"


def test_duplicate_workflow_protection(h):
    steps = h.pm.materialize_steps(bs([key("enter")]))
    h.pm.workflows.create("Dup", steps)
    with pytest.raises(Exception):
        # name is unique-constrained at the DB layer
        h.pm.workflows.create("Dup", steps)
