"""Workflow Logic 3.0 — conditional branches (spec §17).

Condition/UIA checks are mocked; the engine, validation, export/import
and safety paths are real.
"""
import threading
import time
from unittest.mock import patch

import pytest

import app.actions.input_win as iw
from app.core.events import EventBus
from app.data.db import Database
from app.data.repository import ActionRepo, WorkflowRepo
from app.runtime.workflows import WorkflowEngine


class Harness:
    def __init__(self, tmp_path):
        self.db = Database(tmp_path / "wl.db")
        self.bus = EventBus()
        self.actions = ActionRepo(self.db)
        self.workflows = WorkflowRepo(self.db)
        from app.actions.executor import ActionExecutor
        self.executor = ActionExecutor(self.bus)
        self.engine = WorkflowEngine(self.bus, self.executor, self.actions,
                                     self.workflows)
        self.engine.POLL_S = 0.03
        self.executor.workflows = self.engine
        self.progress = []
        self.done = []
        self._evt = threading.Event()
        self.bus.subscribe("workflow.progress",
                           lambda *a: self.progress.append(a))
        self.bus.subscribe("workflow.done",
                           lambda *a: (self.done.append(a),
                                       self._evt.set()))
        self._n = 0

    def key(self, k):
        self._n += 1
        return self.actions.create(f"key {k}{self._n}", "key_press",
                                   {"key": k})

    def astep(self, k):
        return {"type": "action", "action_id": self.key(k)}

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


def cond(kind="process_exists", process="x.exe", **kw):
    c = {"condition": kind, "process": process, "title": ""}
    c.update(kw)
    return c


def if_step(then, else_=None, conds=None, mode="all", wait_ms=0,
            on_timeout="else"):
    return {"type": "if", "conditions": conds or [cond()], "mode": mode,
            "wait_ms": wait_ms, "on_timeout": on_timeout,
            "then": then, "else": else_ or []}


# -- 1-8: branch selection ----------------------------------------------------
def run_branching(h, verdicts, steps):
    """verdicts: map condition process → bool."""
    keys = []
    with patch("app.context.conditions.check",
               lambda c, v=None: verdicts.get(c.get("process"), False)), \
         patch.object(iw, "key_press", lambda k: keys.append(k)):
        result = h.run(steps)
    return result, keys


def test_true_takes_then_branch(h):
    (_, status, _), keys = run_branching(
        h, {"x.exe": True},
        [if_step([h.astep("t")], [h.astep("e")]), h.astep("z")])
    assert status == "completed"
    assert keys == ["t", "z"]                # ELSE never ran


def test_false_takes_else_branch_not_failure(h):
    (_, status, detail), keys = run_branching(
        h, {"x.exe": False},
        [if_step([h.astep("t")], [h.astep("e")]), h.astep("z")])
    assert status == "completed", detail      # FALSE ≠ FAILED
    assert keys == ["e", "z"]


def test_false_with_empty_else_continues(h):
    (_, status, _), keys = run_branching(
        h, {"x.exe": False},
        [if_step([h.astep("t")]), h.astep("z")])
    assert status == "completed"
    assert keys == ["z"]


def test_else_if_chain(h):
    """ELSE-IF = nested if in the else branch."""
    steps = [if_step(
        [h.astep("a")],
        [if_step([h.astep("b")], [h.astep("c")],
                 conds=[cond(process="edge.exe")])],
    )]
    (_, s1, _), k1 = run_branching(h, {"x.exe": True}, steps)
    assert k1 == ["a"]
    (_, s2, _), k2 = run_branching(h, {"edge.exe": True}, steps)
    assert k2 == ["b"]
    (_, s3, _), k3 = run_branching(h, {}, steps)
    assert k3 == ["c"]
    assert s1 == s2 == s3 == "completed"


def test_all_and_any_groups(h):
    two = [cond(process="a.exe"), cond(process="b.exe")]
    steps_all = [if_step([h.astep("y")], [h.astep("n")], conds=two,
                         mode="all")]
    steps_any = [if_step([h.astep("y")], [h.astep("n")], conds=two,
                         mode="any")]
    _, k = run_branching(h, {"a.exe": True, "b.exe": False}, steps_all)
    assert k == ["n"]                        # ALL: one false → else
    _, k = run_branching(h, {"a.exe": True, "b.exe": True}, steps_all)
    assert k == ["y"]
    _, k = run_branching(h, {"a.exe": True, "b.exe": False}, steps_any)
    assert k == ["y"]                        # ANY: one true → then
    _, k = run_branching(h, {}, steps_any)
    assert k == ["n"]


def test_nested_conditions_two_deep(h):
    steps = [if_step(
        [if_step([h.astep("aa")], [h.astep("ab")],
                 conds=[cond(process="inner.exe")])],
        [h.astep("b")])]
    _, k = run_branching(h, {"x.exe": True, "inner.exe": True}, steps)
    assert k == ["aa"]
    _, k = run_branching(h, {"x.exe": True}, steps)
    assert k == ["ab"]
    _, k = run_branching(h, {}, steps)
    assert k == ["b"]


def test_condition_verdict_published(h):
    run_branching(h, {"x.exe": True}, [if_step([h.astep("t")])])
    verdicts = [p for p in h.progress if p[4] == "condition"]
    assert verdicts and "TRUE" in verdicts[-1][3]


# -- 9-13: validation ---------------------------------------------------------
def test_validation_rules(h):
    v = h.workflows.validate
    a = h.astep("k")
    assert "no conditions" in v([{"type": "if", "conditions": [],
                                  "then": [a], "else": []}])
    assert "THEN branch" in v([if_step([])])
    assert "mode" in v([{**if_step([a]), "mode": "sometimes"}])
    assert "on_timeout" in v([{**if_step([a]), "on_timeout": "explode"}])
    bad_cond = if_step([a], conds=[{"condition": "ocr"}])
    assert "unknown condition" in v([bad_cond])
    # invalid action inside a branch
    assert "does not exist" in v(
        [if_step([{"type": "action", "action_id": 99999}])])
    # invalid UI ref inside a branch
    assert "not stored" in v(
        [if_step([{"type": "ui_click", "ref": "ghost"}])])
    # branch-stored refs usable after the if
    find = {"type": "ui_find", "control_type": "Edit", "name": "S",
            "automation_id": "", "process": "", "title": "",
            "store": "el", "timeout_ms": 2000}
    assert v([if_step([find]),
              {"type": "ui_click", "ref": "el"}]) is None


def test_max_nesting_depth(h):
    a = h.astep("k")
    node = if_step([a])
    for _ in range(5):
        node = if_step([node])
    err = h.workflows.validate([node])       # depth 6 > 5
    assert err and "nested deeper" in err
    node = if_step([a])
    for _ in range(4):
        node = if_step([node])
    assert h.workflows.validate([node]) is None   # depth 5 OK


# -- 14-18: timeout / cancellation / safety -----------------------------------
def test_condition_wait_becomes_true(h):
    flip_at = time.monotonic() + 0.2
    keys = []
    with patch("app.context.conditions.check",
               lambda c, v=None: time.monotonic() >= flip_at), \
         patch.object(iw, "key_press", lambda k: keys.append(k)):
        _, status, _ = h.run([if_step([h.astep("t")], [h.astep("e")],
                                      wait_ms=5000)])
    assert status == "completed" and keys == ["t"]


def test_condition_timeout_to_else_vs_fail(h):
    keys = []
    with patch("app.context.conditions.check", lambda c, v=None: False), \
         patch.object(iw, "key_press", lambda k: keys.append(k)):
        _, s1, _ = h.run([if_step([h.astep("t")], [h.astep("e")],
                                  wait_ms=200, on_timeout="else")])
        assert s1 == "completed" and keys == ["e"]
        keys.clear()
        _, s2, d2 = h.run([if_step([h.astep("t")], [h.astep("e")],
                                   wait_ms=200, on_timeout="fail")])
    assert s2 == "failed" and "timeout" in d2
    assert keys == []                        # neither branch ran


def test_cancel_during_condition_wait(h):
    with patch("app.context.conditions.check", lambda c, v=None: False):
        wid = h.workflows.create("Long", [if_step([h.astep("t")],
                                                  wait_ms=60_000)])
        h._evt.clear()
        h.engine.start(wid)
        time.sleep(0.1)
        h.bus.publish("control.enabled", False)
        assert h._evt.wait(2.0)
    assert h.done[-1][1] == "cancelled"


def test_emergency_stop_inside_branch(h):
    """E-stop mid-THEN (and mid-ELSE) delay: no later steps anywhere."""
    for verdict in (True, False):
        keys = []
        branch = [h.astep("a"), {"type": "delay", "ms": 3000},
                  h.astep("b")]
        steps = ([if_step(branch, [])] if verdict
                 else [if_step([h.astep("x")], branch)])
        with patch("app.context.conditions.check", lambda c, v=None: verdict), \
             patch.object(iw, "key_press", lambda k: keys.append(k)):
            wid = h.workflows.create(f"E{verdict}", steps)
            h._evt.clear()
            h.engine.start(wid)
            t0 = time.monotonic()
            while "a" not in keys and time.monotonic() - t0 < 2:
                time.sleep(0.01)
            h.engine.cancel_all("estop")
            assert h._evt.wait(2.0)
            time.sleep(0.1)
        assert h.done[-1][1] == "cancelled"
        assert "b" not in keys


def test_condition_evaluation_error_is_failure(h):
    with patch("app.context.conditions.check",
               side_effect=RuntimeError("uia exploded")), \
         patch.object(iw, "key_press", lambda k: None):
        _, status, detail = h.run([if_step([h.astep("t")])])
    assert status == "failed"                # error ≠ FALSE


def test_duplicate_guard_with_branches(h):
    with patch("app.context.conditions.check", lambda c, v=None: True), \
         patch.object(iw, "key_press", lambda k: None):
        wid = h.workflows.create("Dup", [
            if_step([{"type": "delay", "ms": 400}])])
        h._evt.clear()
        assert h.engine.start(wid)[0]
        again, reason = h.engine.start(wid)
        assert not again and "already running" in reason
        assert h._evt.wait(3.0)


# -- 20-21: import/export + compatibility -------------------------------------
def test_export_import_round_trip_with_branches(tmp_path, monkeypatch):
    monkeypatch.setenv("MGA_DATA_DIR", str(tmp_path))
    from app.profiles.manager import ProfileManager
    pm = ProfileManager(Database(tmp_path / "p.db"))
    pm.seed_defaults()
    aid = pm.actions.create("Step A", "key_press", {"key": "a"})
    bid = pm.actions.create("Step B", "key_press", {"key": "b"})
    steps = [if_step(
        then=[{"type": "action", "action_id": aid},
              if_step([{"type": "action", "action_id": bid}],
                      conds=[cond(kind="ui_element", name="Search",
                                  control_type="Edit",
                                  automation_id="")])],
        else_=[{"type": "delay", "ms": 250}],
        conds=[cond(), cond(process="y.exe")], mode="any",
        wait_ms=3000, on_timeout="fail")]
    wid = pm.workflows.create("Branchy", steps, description="d")
    a = pm.actions.create("Branchy", "workflow", {"workflow_id": wid})
    pid = pm.profiles.create("X", "application", [{"process_name": "x.exe"}])
    pm.rules.create(pid, "circle", a)
    f = tmp_path / "x.json"
    pm.export_profile(pid, f)
    for r in pm.rules.for_profile(pid):
        pm.rules.delete(r.id)
    pm.profiles.delete(pid)
    pm.actions.delete(a)
    pm.actions.delete(aid)
    pm.actions.delete(bid)
    pm.workflows.delete(wid)
    nid = pm.import_profile(f)
    rule = pm.rules.for_profile(nid)[0]
    nwf = pm.workflows.get(
        pm.actions.get(rule.action_id).params["workflow_id"])
    top = nwf.steps[0]
    assert top["type"] == "if" and top["mode"] == "any"
    assert top["wait_ms"] == 3000 and top["on_timeout"] == "fail"
    assert pm.actions.get(top["then"][0]["action_id"]).name == "Step A"
    inner = top["then"][1]
    assert inner["type"] == "if"
    assert inner["conditions"][0]["condition"] == "ui_element"
    assert pm.actions.get(
        inner["then"][0]["action_id"]).name == "Step B"
    assert top["else"] == [{"type": "delay", "ms": 250}]
    # old linear workflows still validate/import unchanged
    assert pm.workflows.validate([{"type": "delay", "ms": 100}]) is None
