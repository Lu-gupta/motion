"""Workflow variables & data flow (spec §16). Clipboard/UIA/context are
mocked; the engine, conditions, validation and serialization are real."""
import threading
import time
from unittest.mock import patch

import pytest

from app.context import conditions
from app.core.events import EventBus
from app.core.types import Context
from app.data.db import Database
from app.data.repository import ActionRepo, WorkflowRepo
from app.runtime.workflows import WorkflowEngine


# -- substitution + comparisons (pure) ----------------------------------------
def test_substitution():
    v = {"name": "Lucky", "n": 3.0, "ok": True}
    assert conditions.substitute("Hello {name}", v) == "Hello Lucky"
    assert conditions.substitute("q={n} b={ok}", v) == "q=3 b=true"
    assert conditions.substitute("no vars", v) == "no vars"
    with pytest.raises(ValueError, match="has not been defined"):
        conditions.substitute("{missing}", v)
    assert conditions.substitution_names("a {x} b {y_z}") == ["x", "y_z"]


def test_variable_comparisons():
    v = {"t": "Success: 12 results", "n": 5.0, "b": True, "e": ""}
    c = conditions.check_variable
    assert c({"var": "t", "op": "contains", "value": "Success"}, v)
    assert c({"var": "t", "op": "starts_with", "value": "Success"}, v)
    assert c({"var": "t", "op": "ends_with", "value": "results"}, v)
    assert not c({"var": "t", "op": "equals", "value": "nope"}, v)
    assert c({"var": "t", "op": "not_equals", "value": "nope"}, v)
    assert c({"var": "e", "op": "is_empty", "value": ""}, v)
    assert c({"var": "t", "op": "is_not_empty", "value": ""}, v)
    assert c({"var": "n", "op": "gt", "value": "4"}, v)
    assert c({"var": "n", "op": "le", "value": "5"}, v)
    assert not c({"var": "n", "op": "lt", "value": "5"}, v)
    assert c({"var": "b", "op": "is_true", "value": ""}, v)
    assert not c({"var": "b", "op": "is_false", "value": ""}, v)
    with pytest.raises(ValueError, match="has not been defined"):
        c({"var": "ghost", "op": "equals", "value": ""}, v)
    with pytest.raises(ValueError, match="not comparable as a number"):
        c({"var": "t", "op": "gt", "value": "1"}, v)
    with pytest.raises(ValueError, match="not a yes/no"):
        c({"var": "t", "op": "is_true", "value": ""}, v)


def test_variable_condition_validation():
    v = conditions.validate
    assert v({"condition": "variable", "var": "x",
              "op": "equals", "value": "a"}) is None
    assert "invalid variable name" in v({"condition": "variable",
                                         "var": "9bad", "op": "equals"})
    assert "unknown comparison" in v({"condition": "variable", "var": "x",
                                      "op": "sorta"})
    assert "numeric" in v({"condition": "variable", "var": "x",
                           "op": "gt", "value": "many"})


# -- engine -------------------------------------------------------------------
class Harness:
    def __init__(self, tmp_path, name="wv"):
        self.db = Database(tmp_path / f"{name}.db")
        self.bus = EventBus()
        self.actions = ActionRepo(self.db)
        self.workflows = WorkflowRepo(self.db)
        from app.actions.executor import ActionExecutor
        self.executor = ActionExecutor(self.bus)
        self.engine = WorkflowEngine(self.bus, self.executor, self.actions,
                                     self.workflows)
        self.engine.POLL_S = 0.03
        self.executor.workflows = self.engine
        self.done = []
        self.var_events = []
        self._evt = threading.Event()
        self.bus.subscribe("workflow.done",
                           lambda *a: (self.done.append(a),
                                       self._evt.set()))
        self.bus.subscribe("workflow.vars",
                           lambda *a: self.var_events.append(a))
        self._n = 0

    def run(self, steps, timeout=6.0):
        self._n += 1
        wid = self.workflows.create(f"W{self._n}", steps)
        self._evt.clear()
        started, reason = self.engine.start(wid)
        assert started, reason
        assert self._evt.wait(timeout), "did not finish"
        return self.done[-1]


@pytest.fixture
def h(tmp_path):
    return Harness(tmp_path)


def sv(name, value, vtype="text", **kw):
    s = {"type": "set_var", "name": name, "source": "literal",
         "value_type": vtype, "value": value}
    s.update(kw)
    return s


def var_if(var, op, value, then, else_=None):
    return {"type": "if",
            "conditions": [{"condition": "variable", "var": var,
                            "op": op, "value": value}],
            "mode": "all", "wait_ms": 0, "on_timeout": "else",
            "then": then, "else": else_ or []}


def find_step(store="el"):
    return {"type": "ui_find", "process": "", "title": "",
            "control_type": "Edit", "name": "S", "automation_id": "",
            "store": store, "timeout_ms": 2000}


def test_set_variables_and_types(h):
    _, status, detail = h.run([
        sv("t", "hello"), sv("n", "42", "number"),
        sv("b", "yes", "boolean"),
        var_if("n", "eq", "42", [var_if("b", "is_true", "", [
            var_if("t", "equals", "hello", [sv("okv", "1")],
                   [sv("bad", "1")])])])])
    assert status == "completed", detail
    last_vars = h.var_events[-1][1]
    assert last_vars["t"] == "hello" and last_vars["n"] == 42.0
    assert last_vars["b"] is True
    assert "okv" in last_vars and "bad" not in last_vars


def test_set_from_variable_clipboard_and_context(h):
    with patch("app.actions.system_actions.get_clipboard_text",
               return_value="from clip"), \
         patch("app.context.detector.snapshot",
               return_value=Context(application="chrome",
                                    process="chrome.exe",
                                    window_title="YouTube - Chrome")):
        _, status, _ = h.run([
            sv("a", "one"),
            {"type": "set_var", "name": "copy", "source": "variable",
             "source_var": "a", "value_type": "text"},
            {"type": "set_var", "name": "clip", "source": "clipboard",
             "value_type": "text"},
            {"type": "set_var", "name": "app", "source": "active_app",
             "value_type": "text"},
            {"type": "set_var", "name": "title",
             "source": "active_window", "value_type": "text"}])
    assert status == "completed"
    v = h.var_events[-1][1]
    assert v["copy"] == "one" and v["clip"] == "from clip"
    assert v["app"] == "chrome.exe" and "YouTube" in v["title"]


def test_ui_read_and_typed_substitution(h):
    from tests.test_uia_workflows import info
    typed = []
    with patch("app.context.uia.find_element",
               lambda c: ("ctrl", info())), \
         patch("app.context.uia.refresh_info", lambda c, cr: info()), \
         patch("app.context.uia.get_text",
               lambda c: "Success: 12 results"), \
         patch("app.context.uia.set_text",
               lambda c, t: typed.append(t) or True):
        _, status, detail = h.run([
            find_step(),
            {"type": "ui_read", "ref": "el", "store_var": "result_text"},
            var_if("result_text", "contains", "Success",
                   [sv("verdict", "good")], [sv("verdict", "bad")]),
            {"type": "ui_type", "ref": "el",
             "text": "found: {result_text}"}])
    assert status == "completed", detail
    assert typed == ["found: Success: 12 results"]
    assert h.var_events[-1][1]["verdict"] == "good"


def test_clipboard_write_with_substitution(h):
    written = []
    with patch("app.actions.system_actions.set_clipboard_text",
               lambda t: written.append(t)):
        _, status, _ = h.run([
            sv("name", "Lucky"),
            {"type": "set_clipboard", "value": "Hello {name}"}])
    assert status == "completed"
    assert written == ["Hello Lucky"]


def test_variable_in_url(h):
    opened = []
    aid = h.actions.create("Search", "open_url",
                           {"url": "https://x.test/?q={search_text}"})
    with patch("app.actions.system_actions.webbrowser.open",
               lambda u: opened.append(u)):
        _, status, detail = h.run([
            sv("search_text", "AI news"),
            {"type": "action", "action_id": aid}])
    assert status == "completed", detail
    assert opened == ["https://x.test/?q=AI news"]


def never_branch(*defines):
    """An if-step whose THEN never runs at runtime but statically
    defines variables (validation is conservative across branches) —
    the tool for constructing runtime-only undefined-variable cases."""
    return {"type": "if",
            "conditions": [{"condition": "process_exists",
                            "process": "never_running_app.exe",
                            "title": ""}],
            "mode": "all", "wait_ms": 0, "on_timeout": "else",
            "then": [sv(d, "x") for d in defines], "else": []}


def test_undefined_variable_fails_clearly(h):
    from tests.test_uia_workflows import info
    with patch("app.context.uia.find_element",
               lambda c: ("ctrl", info())), \
         patch("app.context.uia.refresh_info", lambda c, cr: info()):
        _, status, detail = h.run([
            never_branch("ghost"), find_step(),
            {"type": "ui_type", "ref": "el", "text": "{ghost}"}])
    assert status == "failed"
    assert "'ghost' has not been defined" in detail


def test_undefined_variable_condition_fails(h):
    _, status, detail = h.run([
        never_branch("ghost"),
        var_if("ghost", "equals", "x", [sv("a", "1")])])
    assert status == "failed" and "has not been defined" in detail


def test_lifetime_no_leak_between_runs(h):
    _, s1, _ = h.run([sv("persist", "value")])
    assert s1 == "completed"
    # a second run reading the same variable must fail — fresh context
    _, s2, d2 = h.run([
        never_branch("persist"),
        var_if("persist", "equals", "value", [sv("x", "1")])])
    assert s2 == "failed" and "has not been defined" in d2


def test_concurrent_isolation(tmp_path):
    """Two engines/workflows running simultaneously never share values."""
    h = Harness(tmp_path, "iso")
    typed = {}
    from tests.test_uia_workflows import info

    def fake_set_text(c, t):
        wid = t.split(":")[0]
        typed.setdefault(wid, []).append(t)
        return True
    done = []
    evt = threading.Event()
    h.bus.subscribe("workflow.done",
                    lambda *a: (done.append(a),
                                evt.set() if len(done) >= 2 else None))
    with patch("app.context.uia.find_element",
               lambda c: ("ctrl", info())), \
         patch("app.context.uia.refresh_info", lambda c, cr: info()), \
         patch("app.context.uia.set_text", fake_set_text):
        w1 = h.workflows.create("iso A", [
            sv("name", "A"), find_step(), {"type": "delay", "ms": 150},
            {"type": "ui_type", "ref": "el", "text": "A:{name}"}])
        w2 = h.workflows.create("iso B", [
            sv("name", "B"), find_step(), {"type": "delay", "ms": 150},
            {"type": "ui_type", "ref": "el", "text": "B:{name}"}])
        assert h.engine.start(w1)[0] and h.engine.start(w2)[0]
        assert evt.wait(6.0)
    assert typed["A"] == ["A:A"] and typed["B"] == ["B:B"]


def test_cancel_and_estop_destroy_context(h):
    with patch("app.context.conditions.check", lambda c, v=None: False):
        wid = h.workflows.create("C", [
            sv("secret", "x"),
            {"type": "if", "conditions": [{"condition": "process_exists",
                                           "process": "never.exe",
                                           "title": ""}],
             "mode": "all", "wait_ms": 60_000, "on_timeout": "else",
             "then": [sv("y", "1")], "else": []}])
        h._evt.clear()
        h.engine.start(wid)
        time.sleep(0.15)
        h.bus.publish("control.enabled", False)   # emergency stop
        assert h._evt.wait(2.0)
    assert h.done[-1][1] == "cancelled"
    # run again: context is fresh, 'secret' not defined
    _, s2, d2 = h.run([
        never_branch("secret"),
        var_if("secret", "equals", "x", [sv("z", "1")])])
    assert s2 == "failed" and "has not been defined" in d2


# -- validation ---------------------------------------------------------------
def test_validation(h):
    v = h.workflows.validate
    assert "invalid variable name" in v([sv("Bad Name", "x")])
    assert "unknown variable source" in v(
        [{"type": "set_var", "name": "a", "source": "telepathy",
          "value_type": "text"}])
    assert "unknown variable type" in v(
        [{"type": "set_var", "name": "a", "source": "literal",
          "value_type": "blob"}])
    assert "not a number" in v([sv("n", "many", "number")])
    assert "has not been defined" in v(
        [{"type": "set_var", "name": "a", "source": "variable",
          "source_var": "ghost", "value_type": "text"}])
    assert "has not been defined" in v(
        [find_step(), {"type": "ui_type", "ref": "el",
                       "text": "hi {ghost}"}])
    assert "has not been defined" in v(
        [{"type": "set_clipboard", "value": "{ghost}"}])
    assert "has not been defined" in v(
        [var_if("ghost", "equals", "x", [sv("a", "1")])])
    assert "is not stored" in v(
        [{"type": "ui_read", "ref": "nope", "store_var": "t"}])
    assert "invalid variable name" in v(
        [find_step(), {"type": "ui_read", "ref": "el",
                       "store_var": "Bad!"}])
    # good end-to-end shape validates
    ok = [sv("q", "AI"), find_step(),
          {"type": "ui_read", "ref": "el", "store_var": "r"},
          var_if("r", "contains", "AI",
                 [{"type": "ui_type", "ref": "el", "text": "{q}/{r}"}]),
          {"type": "set_clipboard", "value": "{r}"}]
    assert v(ok) is None


def test_export_import_round_trip(tmp_path, monkeypatch):
    monkeypatch.setenv("MGA_DATA_DIR", str(tmp_path))
    from app.profiles.manager import ProfileManager
    pm = ProfileManager(Database(tmp_path / "p.db"))
    pm.seed_defaults()
    steps = [sv("q", "AI"), find_step(),
             {"type": "ui_read", "ref": "el", "store_var": "r"},
             var_if("r", "contains", "AI",
                    [{"type": "set_clipboard", "value": "{r}"}])]
    wid = pm.workflows.create("Vars WF", steps)
    a = pm.actions.create("Vars WF", "workflow", {"workflow_id": wid})
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
    nwf = pm.workflows.get(pm.actions.get(
        pm.rules.for_profile(nid)[0].action_id).params["workflow_id"])
    assert [s["type"] for s in nwf.steps] == [
        "set_var", "ui_find", "ui_read", "if"]
    assert nwf.steps[0]["name"] == "q"
    assert nwf.steps[3]["conditions"][0]["var"] == "r"
    assert nwf.steps[3]["then"][0]["value"] == "{r}"
    assert pm.workflows.validate(nwf.steps) is None
    # legacy workflows without variables still validate
    assert pm.workflows.validate([{"type": "delay", "ms": 100}]) is None
