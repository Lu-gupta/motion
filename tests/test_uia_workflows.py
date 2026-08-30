"""UI-aware workflows (spec §20). Windows UI Automation is mocked —
no real applications or COM required."""
import threading
import time
from unittest.mock import patch

import pytest

from app.context import conditions
from app.context.uia import UIElementInfo, describe_criteria, matches
from app.core.events import EventBus
from app.data.db import Database
from app.data.repository import ActionRepo, WorkflowRepo
from app.runtime.workflows import WorkflowEngine


# -- 1-9: element model + matching (pure) -------------------------------------
def info(**kw):
    base = dict(name="Search", control_type="Edit",
                automation_id="searchbox", class_name="X",
                process="chrome.exe", window_title="YouTube",
                enabled=True, visible=True, rect=(1, 2, 3, 4))
    base.update(kw)
    return UIElementInfo(**base)


def test_element_model_defaults():
    e = UIElementInfo()
    assert e.enabled and e.visible and e.rect == (0, 0, 0, 0)
    assert e.name == "" and e.automation_id == ""


def test_matching():
    e = info()
    assert matches(e, name="Search")                    # exact
    assert matches(e, name="search")                    # case-insensitive
    assert matches(e, name="*earch*")                   # wildcard
    assert matches(e, control_type="Edit", name="Search")
    assert matches(e, control_type="Any")
    assert matches(e, automation_id="searchbox")
    assert not matches(e, name="Submit")
    assert not matches(e, control_type="Button")
    assert not matches(e, automation_id="SEARCHBOX")    # ids case-sensitive
    assert not matches(info(name=""), name="Search")
    assert not matches(info(enabled=False), name="Search",
                       require_enabled=True)
    assert not matches(info(visible=False), name="Search",
                       require_visible=True)
    assert matches(info(enabled=False), name="Search")  # filter opt-in


def test_describe_criteria():
    s = {"control_type": "Edit", "name": "Search", "process": "chrome.exe"}
    d = describe_criteria(s)
    assert "Edit" in d and "Search" in d and "chrome.exe" in d


# -- condition integration ----------------------------------------------------
def test_ui_condition_validate_and_check():
    assert "needs" in conditions.validate({"condition": "ui_element"})
    ok = {"condition": "ui_element", "name": "Search"}
    assert conditions.validate(ok) is None
    with patch("app.context.uia.find_element",
               return_value=("ctrl", info())):
        assert conditions.check({**ok, "process": "chrome"})
    with patch("app.context.uia.find_element", return_value=None):
        assert not conditions.check(ok)


# -- repo validation ----------------------------------------------------------
@pytest.fixture
def repos(tmp_path):
    db = Database(tmp_path / "u.db")
    return ActionRepo(db), WorkflowRepo(db)


def ui_find(store="el", **kw):
    s = {"type": "ui_find", "process": "", "title": "",
         "control_type": "Edit", "name": "Search", "automation_id": "",
         "store": store, "timeout_ms": 2000}
    s.update(kw)
    return s


def test_malformed_ui_steps_rejected(repos):
    _, wr = repos
    v = wr.validate
    assert "name, automation id or control type" in v(
        [ui_find(control_type="", name="")])
    assert "timeout" in v([ui_find(timeout_ms=1)])
    assert "stored element name" in v([{"type": "ui_click", "ref": ""}])
    assert "not stored" in v([{"type": "ui_click", "ref": "ghost"}])
    assert "not stored" in v([{"type": "ui_click", "ref": "el"},
                              ui_find()])   # ref BEFORE the find
    assert v([ui_find(), {"type": "ui_click", "ref": "el"},
              {"type": "ui_focus", "ref": "el"},
              {"type": "ui_type", "ref": "el", "text": "hi"}]) is None


# -- engine -------------------------------------------------------------------
class Harness:
    def __init__(self, tmp_path):
        self.db = Database(tmp_path / "wf.db")
        self.bus = EventBus()
        self.actions = ActionRepo(self.db)
        self.workflows = WorkflowRepo(self.db)

        class _Ex:
            def execute(self, spec):
                return True
        self.engine = WorkflowEngine(self.bus, _Ex(), self.actions,
                                     self.workflows)
        self.engine.POLL_S = 0.03
        self.done = []
        self._evt = threading.Event()
        self.bus.subscribe("workflow.done",
                           lambda *a: (self.done.append(a),
                                       self._evt.set()))

    def run(self, steps, timeout=6.0):
        wid = self.workflows.create("W%d" % len(self.done), steps)
        self._evt.clear()
        started, reason = self.engine.start(wid)
        assert started, reason
        assert self._evt.wait(timeout), "did not finish"
        return self.done[-1]


@pytest.fixture
def h(tmp_path):
    return Harness(tmp_path)


def fake_uia(found=True, refreshed=True, clicks=None, focuses=None,
             texts=None, set_text_ok=True):
    el = ("ctrl", info())
    patches = {
        "find_element": lambda c: el if found else None,
        "refresh_info": lambda c, cr: info() if refreshed else None,
        "invoke": lambda c: (clicks.append(1) if clicks is not None
                             else None),
        "focus": lambda c: (focuses.append(1) if focuses is not None
                            else None),
        "set_text": lambda c, t: (texts.append(t) if texts is not None
                                  else None) or set_text_ok,
    }
    return patch.multiple("app.context.uia", **patches)


def test_ui_find_stores_and_workflow_succeeds(h):
    clicks, focuses, texts = [], [], []
    with fake_uia(clicks=clicks, focuses=focuses, texts=texts):
        name, status, detail = h.run([
            ui_find(),
            {"type": "ui_focus", "ref": "el"},
            {"type": "ui_type", "ref": "el", "text": "AI news"},
            {"type": "ui_click", "ref": "el"}])
    assert status == "completed", detail
    assert clicks == [1] and focuses == [1] and texts == ["AI news"]


def test_ui_type_fallback_to_keystrokes(h):
    focuses, typed = [], []
    with fake_uia(focuses=focuses, set_text_ok=False), \
         patch("app.actions.input_win.type_text",
               lambda t: typed.append(t)):
        _, status, _ = h.run([
            ui_find(),
            {"type": "ui_type", "ref": "el", "text": "hello"}])
    assert status == "completed"
    assert typed == ["hello"] and focuses == [1]   # focused before typing


def test_missing_element_times_out_and_blocks_later_steps(h):
    clicks = []
    with fake_uia(found=False, clicks=clicks):
        t0 = time.monotonic()
        _, status, detail = h.run([
            ui_find(timeout_ms=300),
            {"type": "ui_click", "ref": "el"}])
        took = time.monotonic() - t0
    assert status == "failed"
    assert "not found" in detail and "step 1" in detail
    assert clicks == []
    assert 0.25 <= took < 2.0


def test_stale_element_fails_safely(h):
    clicks = []
    with fake_uia(refreshed=False, clicks=clicks):
        _, status, detail = h.run([
            ui_find(),
            {"type": "ui_click", "ref": "el"}])
    assert status == "failed"
    assert "stale" in detail or "no longer exists" in detail
    assert clicks == []                     # never clicked a swapped target


def test_disabled_element_not_clicked(h):
    clicks = []
    with fake_uia(clicks=clicks), \
         patch("app.context.uia.refresh_info",
               lambda c, cr: info(enabled=False)):
        _, status, detail = h.run([
            ui_find(),
            {"type": "ui_click", "ref": "el"}])
    assert status == "failed" and "disabled" in detail
    assert clicks == []


def test_wrong_application_no_match(h):
    """Process filter mismatch = finder returns None = timeout."""
    with patch("app.context.uia.find_element",
               lambda c: None if c.get("process") == "excel.exe"
               else ("ctrl", info())):
        _, status, _ = h.run([ui_find(process="excel.exe",
                                      timeout_ms=300)])
    assert status == "failed"


def test_ui_wait_cancellation(h):
    with fake_uia(found=False):
        wid = h.workflows.create("Long", [ui_find(timeout_ms=60_000)])
        h._evt.clear()
        h.engine.start(wid)
        time.sleep(0.1)
        t0 = time.monotonic()
        h.bus.publish("control.enabled", False)   # emergency stop path
        assert h._evt.wait(2.0)
        assert time.monotonic() - t0 < 1.0
    assert h.done[-1][1] == "cancelled"


def test_shutdown_during_ui_wait(tmp_path, monkeypatch):
    from app.core.config import Config
    from app.runtime.controller import MotionController
    monkeypatch.setenv("MGA_DATA_DIR", str(tmp_path))
    db = Database(tmp_path / "rt.db")
    bus = EventBus()
    c = MotionController(Config(), db, bus)
    c.workflows.POLL_S = 0.03
    done = []
    evt = threading.Event()
    bus.subscribe("workflow.done", lambda *a: (done.append(a), evt.set()))
    with patch("app.context.uia.find_element", lambda cr: None):
        wid = c.workflow_repo.create("UI", [ui_find(timeout_ms=60_000)])
        c.workflows.start(wid)
        time.sleep(0.1)
        c.shutdown()
        assert evt.wait(2.0)
    assert done[-1][1] == "cancelled"


def test_ref_scope_is_per_run(h):
    """References do not leak between runs."""
    seq = {"n": 0}

    def finder(c):
        seq["n"] += 1
        return ("ctrl", info()) if seq["n"] == 1 else None
    with patch("app.context.uia.find_element", finder), \
         patch("app.context.uia.refresh_info", lambda c, cr: info()), \
         patch("app.context.uia.invoke", lambda c: None):
        _, s1, _ = h.run([ui_find()])
        assert s1 == "completed"
        # second run: find fails → click step never sees the old ref
        _, s2, d2 = h.run([ui_find(timeout_ms=300),
                           {"type": "ui_click", "ref": "el"}])
    assert s2 == "failed" and "not found" in d2


def test_export_import_ui_steps(tmp_path, monkeypatch):
    monkeypatch.setenv("MGA_DATA_DIR", str(tmp_path))
    from app.profiles.manager import ProfileManager
    pm = ProfileManager(Database(tmp_path / "p.db"))
    pm.seed_defaults()
    steps = [ui_find(),
             {"type": "wait", "condition": "app_running",
              "process": "chrome.exe", "title": "", "timeout_ms": 5000},
             {"type": "ui_type", "ref": "el", "text": "hi"}]
    wid = pm.workflows.create("UI WF", steps)
    a = pm.actions.create("UI WF", "workflow", {"workflow_id": wid})
    pid = pm.profiles.create("X", "application", [{"process_name": "x.exe"}])
    pm.rules.create(pid, "circle", a)
    f = tmp_path / "x.json"
    pm.export_profile(pid, f)
    for r in pm.rules.for_profile(pid):
        pm.rules.delete(r.id)
    pm.profiles.delete(pid)
    pm.actions.delete(a)
    pm.workflows.delete(wid)
    nid = pm.import_profile(f)
    rule = pm.rules.for_profile(nid)[0]
    na = pm.actions.get(rule.action_id)
    nwf = pm.workflows.get(na.params["workflow_id"])
    assert [s["type"] for s in nwf.steps] == ["ui_find", "wait", "ui_type"]
    assert nwf.steps[0]["name"] == "Search"      # criteria intact
    assert nwf.steps[1]["condition"] == "app_running"  # wait no longer dropped
