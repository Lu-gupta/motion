"""Workflow retries & bounded loops (spec §20). Conditions/input are
mocked; the engine, validation, safety bounds and serialization are real.
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
    def __init__(self, tmp_path, name="wr"):
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

    def make(self, steps):
        self._n += 1
        return self.workflows.create(f"W{self._n}", steps)

    def start(self, wid):
        self._evt.clear()
        return self.engine.start(wid)

    def run(self, steps, timeout=6.0):
        wid = self.make(steps)
        started, reason = self.start(wid)
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


def retry(steps, attempts=3, delay_ms=0, on_fail="fail", until=None,
          fallback=None):
    s = {"type": "retry", "attempts": attempts, "delay_ms": delay_ms,
         "on_fail": on_fail, "steps": steps}
    if until is not None:
        s["until"] = until
    if fallback is not None:
        s["fallback"] = fallback
    return s


def repeat(steps, count):
    return {"type": "repeat", "count": count, "steps": steps}


def repeat_until(steps, conds=None, max_it=10, delay_ms=0,
                 timeout_ms=5_000, mode="all"):
    return {"type": "repeat_until", "conditions": conds or [cond()],
            "mode": mode, "max_iterations": max_it, "delay_ms": delay_ms,
            "timeout_ms": timeout_ms, "steps": steps}


class FlakyKey:
    """key_press stand-in that raises for the first `fail_times` calls of
    one specific key — a failing attempt, exactly like a real input
    error; every other key succeeds."""

    def __init__(self, fail_times, key="a"):
        self.fail_times = fail_times
        self.key = key
        self.calls = []

    def __call__(self, k):
        self.calls.append(k)
        if k == self.key and self.calls.count(k) <= self.fail_times:
            raise ValueError(f"transient failure for {k}")


# -- 1-5: retry ---------------------------------------------------------------
def test_retry_succeeds_first_attempt(h):
    fk = FlakyKey(0)
    with patch.object(iw, "key_press", fk):
        _, status, detail = h.run([retry([h.astep("a")]), h.astep("z")])
    assert status == "completed", detail
    assert fk.calls == ["a", "z"]            # exactly one attempt


def test_retry_succeeds_second_attempt(h):
    fk = FlakyKey(1)
    with patch.object(iw, "key_press", fk):
        _, status, detail = h.run([retry([h.astep("a")]), h.astep("z")])
    assert status == "completed", detail
    assert fk.calls == ["a", "a", "z"]


def test_retry_succeeds_final_attempt(h):
    fk = FlakyKey(2)
    with patch.object(iw, "key_press", fk):
        _, status, detail = h.run([retry([h.astep("a")], attempts=3)])
    assert status == "completed", detail
    assert fk.calls.count("a") == 3


def test_retry_exhausted(h):
    fk = FlakyKey(99)
    with patch.object(iw, "key_press", fk):
        _, status, detail = h.run([retry([h.astep("a")], attempts=3),
                                   h.astep("z")])
    assert status == "failed"
    assert "RETRY EXHAUSTED" in detail
    assert fk.calls.count("a") == 3          # bounded — never a 4th
    assert "z" not in fk.calls               # later steps never ran


def test_retry_delay_between_attempts(h):
    fk = FlakyKey(2)
    t0 = time.monotonic()
    with patch.object(iw, "key_press", fk):
        _, status, _ = h.run([retry([h.astep("a")], attempts=3,
                                    delay_ms=120)])
    elapsed = time.monotonic() - t0
    assert status == "completed"
    assert elapsed >= 0.24                   # two inter-attempt delays


def test_retry_failure_scoped_to_block(h):
    """Spec §2: only the retry block reruns — earlier steps never do."""
    fk = FlakyKey(1, key="find")
    with patch.object(iw, "key_press", fk):
        _, status, _ = h.run([h.astep("launch"),
                              retry([h.astep("find")], attempts=3)])
    assert status == "completed"
    assert fk.calls.count("launch") == 1     # never relaunched
    assert fk.calls.count("find") == 2


def test_retry_on_fail_continue(h):
    fk = FlakyKey(99)
    with patch.object(iw, "key_press", fk):
        _, status, detail = h.run([
            retry([h.astep("a")], attempts=2, on_fail="continue"),
            h.astep("z")])
    assert status == "completed", detail
    assert "z" in fk.calls                   # workflow went on


def test_retry_on_fail_fallback(h):
    fk = FlakyKey(99)
    with patch.object(iw, "key_press", fk):
        _, status, detail = h.run([
            retry([h.astep("a")], attempts=2, on_fail="fallback",
                  fallback=[h.astep("f")]),
            h.astep("z")])
    assert status == "completed", detail
    assert fk.calls == ["a", "a", "f", "z"]  # fallback ran, then continued


# -- 6-9: repeat --------------------------------------------------------------
def test_repeat_once(h):
    fk = FlakyKey(0)
    with patch.object(iw, "key_press", fk):
        _, status, _ = h.run([repeat([h.astep("a")], 1)])
    assert status == "completed"
    assert fk.calls == ["a"]


def test_repeat_three_times(h):
    fk = FlakyKey(0)
    with patch.object(iw, "key_press", fk):
        _, status, _ = h.run([repeat([h.astep("a"), h.astep("b")], 3)])
    assert status == "completed"
    assert fk.calls == ["a", "b"] * 3        # order preserved per pass


def test_repeat_maximum_100(h):
    fk = FlakyKey(0)
    with patch.object(iw, "key_press", fk):
        _, status, _ = h.run([repeat([h.astep("a")], 100)], timeout=15)
    assert status == "completed"
    assert fk.calls.count("a") == 100


def test_invalid_repeat_counts(h):
    for bad in (0, -1, 101, "3", None):
        err = h.workflows.validate([repeat([h.astep("a")], bad)])
        assert err is not None, f"count {bad!r} must be rejected"
    # settings can LOWER the bound
    h.workflows.max_repeat = 10
    assert h.workflows.validate([repeat([h.astep("a")], 11)]) is not None
    assert h.workflows.validate([repeat([h.astep("a")], 10)]) is None


# -- 10-12: repeat-until ------------------------------------------------------
def test_repeat_until_immediately_true(h):
    fk = FlakyKey(0)
    with patch("app.context.conditions.check", lambda c, v=None: True), \
         patch.object(iw, "key_press", fk):
        _, status, _ = h.run([repeat_until([h.astep("a")]), h.astep("z")])
    assert status == "completed"
    assert fk.calls == ["z"]                 # zero passes of the body


def test_repeat_until_becomes_true(h):
    fk = FlakyKey(0)
    hits = []

    def check(c, v=None):
        hits.append(1)
        return len(hits) >= 3                # true on the third check
    with patch("app.context.conditions.check", check), \
         patch.object(iw, "key_press", fk):
        _, status, detail = h.run([repeat_until([h.astep("a")])])
    assert status == "completed", detail
    assert fk.calls == ["a", "a"]            # two passes, then true


def test_repeat_until_iteration_bound(h):
    fk = FlakyKey(0)
    with patch("app.context.conditions.check", lambda c, v=None: False), \
         patch.object(iw, "key_press", fk):
        _, status, detail = h.run([repeat_until([h.astep("a")], max_it=3)])
    assert status == "failed"
    assert "TIMEOUT" in detail               # explicit, never silent
    assert fk.calls.count("a") == 3


def test_repeat_until_time_limit(h):
    with patch("app.context.conditions.check", lambda c, v=None: False):
        _, status, detail = h.run([repeat_until(
            [{"type": "delay", "ms": 60}], max_it=100, timeout_ms=200)])
    assert status == "failed"
    assert "TIMEOUT" in detail


# -- 13-15: retry-until conditions (existing condition engine) ---------------
def test_retry_until_ui_condition(h):
    checks = []

    def check(c, v=None):
        checks.append(c.get("condition"))
        return len(checks) >= 2
    with patch("app.context.conditions.check", check), \
         patch.object(iw, "key_press", lambda k: None):
        _, status, detail = h.run([retry(
            [h.astep("a")], attempts=3,
            until=[cond("ui_element", name="Search")])])
    assert status == "completed", detail
    assert checks == ["ui_element", "ui_element"]   # 2 attempts needed


def test_retry_until_app_condition(h):
    with patch("app.context.conditions.check", lambda c, v=None: True), \
         patch.object(iw, "key_press", lambda k: None):
        _, status, _ = h.run([retry([h.astep("a")],
                                    until=[cond("app_running")])])
    assert status == "completed"


def test_retry_until_variable_condition(h):
    clips = iter(["not yet", "done"])
    with patch("app.actions.system_actions.get_clipboard_text",
               lambda: next(clips)):
        _, status, detail = h.run([retry(
            [{"type": "set_var", "name": "state", "source": "clipboard",
              "value_type": "text"}],
            attempts=3,
            until=[{"condition": "variable", "var": "state",
                    "op": "equals", "value": "done"}])])
    assert status == "completed", detail     # 2nd attempt read "done"


# -- 16-18: nesting -----------------------------------------------------------
def test_nested_retry(h):
    fk = FlakyKey(1)
    with patch.object(iw, "key_press", fk):
        _, status, _ = h.run([retry([retry([h.astep("a")], attempts=2)],
                                    attempts=2)])
    assert status == "completed"
    assert fk.calls.count("a") == 2          # inner retry absorbed it


def test_nested_repeat(h):
    fk = FlakyKey(0)
    with patch.object(iw, "key_press", fk):
        _, status, _ = h.run([repeat([repeat([h.astep("a")], 3)], 2)])
    assert status == "completed"
    assert fk.calls.count("a") == 6


def test_if_retry_repeat_mix_and_depth_rejection(h):
    inner = [h.astep("a")]
    mixed = [{"type": "if", "conditions": [cond()], "mode": "all",
              "wait_ms": 0, "on_timeout": "else",
              "then": [retry(inner, attempts=2)],
              "else": [repeat(inner, 2)]}]
    assert h.workflows.validate(mixed) is None    # sane nesting is fine
    six = inner
    for _ in range(6):
        six = [repeat(six, 2)]
    err = h.workflows.validate(six)
    assert err and "nested deeper" in err


# -- 19-23: cancellation + guards --------------------------------------------
def cancel_mid(h, steps, trigger="cancel_all"):
    fk = FlakyKey(0)
    with patch.object(iw, "key_press", fk):
        wid = h.make(steps)
        started, reason = h.start(wid)
        assert started, reason
        time.sleep(0.15)
        if trigger == "cancel_all":
            h.engine.cancel_all("emergency stop")
        else:
            h.bus.publish("control.enabled", False)
        assert h._evt.wait(3), "did not finish after cancel"
    return h.done[-1], fk


def test_estop_during_retry(h):
    (_, status, _), fk = cancel_mid(
        h, [retry([h.astep("a"), {"type": "delay", "ms": 5_000}],
                  attempts=5, delay_ms=5_000)])
    assert status == "cancelled"
    assert fk.calls.count("a") == 1          # no next attempt started


def test_estop_during_repeat(h):
    (_, status, _), fk = cancel_mid(
        h, [repeat([h.astep("a"), {"type": "delay", "ms": 5_000}], 50)])
    assert status == "cancelled"
    assert fk.calls.count("a") == 1          # no next iteration started


def test_estop_during_repeat_until(h):
    with patch("app.context.conditions.check", lambda c, v=None: False):
        (_, status, _), fk = cancel_mid(
            h, [repeat_until([h.astep("a")], max_it=100, delay_ms=5_000,
                             timeout_ms=120_000)])
    assert status == "cancelled"
    assert fk.calls.count("a") == 1


def test_motion_off_cancels_loop(h):
    """control.enabled False (motion off / shutdown path) behaves
    exactly like Emergency Stop for loops."""
    (_, status, _), fk = cancel_mid(
        h, [repeat([h.astep("a"), {"type": "delay", "ms": 5_000}], 50)],
        trigger="control")
    assert status == "cancelled"
    assert fk.calls.count("a") == 1


def test_duplicate_guard_while_looping(h):
    with patch.object(iw, "key_press", lambda k: None):
        wid = h.make([repeat([{"type": "delay", "ms": 200}], 20)])
        started, reason = h.start(wid)
        assert started, reason
        time.sleep(0.1)
        again, reason = h.engine.start(wid)
        assert not again and "already running" in reason
        h.engine.cancel_all()
        assert h._evt.wait(3)


# -- 24-25: variables ---------------------------------------------------------
def test_variables_persist_across_iterations(h):
    clips = []
    with patch("app.actions.system_actions.set_clipboard_text",
               clips.append):
        _, status, detail = h.run([
            {"type": "set_var", "name": "greeting", "source": "literal",
             "value_type": "text", "value": "hi"},
            repeat([{"type": "set_clipboard", "value": "{greeting}"}], 3)])
    assert status == "completed", detail
    assert clips == ["hi", "hi", "hi"]       # nothing reset per pass


def test_concurrent_loop_isolation(tmp_path):
    a, b = Harness(tmp_path, "iso_a"), Harness(tmp_path, "iso_b")
    clips = []
    with patch("app.actions.system_actions.set_clipboard_text",
               clips.append):
        wa = a.make([
            {"type": "set_var", "name": "v", "source": "literal",
             "value_type": "text", "value": "aaa"},
            repeat([{"type": "set_clipboard", "value": "{v}"},
                    {"type": "delay", "ms": 30}], 3)])
        wb = b.make([
            {"type": "set_var", "name": "v", "source": "literal",
             "value_type": "text", "value": "bbb"},
            repeat([{"type": "set_clipboard", "value": "{v}"},
                    {"type": "delay", "ms": 30}], 3)])
        assert a.start(wa)[0] and b.start(wb)[0]
        assert a._evt.wait(5) and b._evt.wait(5)
    assert sorted(clips) == ["aaa"] * 3 + ["bbb"] * 3


# -- 26-27: serialization + compatibility ------------------------------------
def test_export_import_round_trip(tmp_path, monkeypatch):
    monkeypatch.setenv("MGA_DATA_DIR", str(tmp_path))
    from app.profiles.manager import ProfileManager
    pm = ProfileManager(Database(tmp_path / "p.db"))
    pm.seed_defaults()
    ka = pm.actions.create("kx", "key_press", {"key": "x"})
    steps = [
        {"type": "retry", "attempts": 3, "delay_ms": 500,
         "on_fail": "fallback",
         "steps": [{"type": "action", "action_id": ka}],
         "until": [cond("app_running", process="chrome.exe")],
         "fallback": [{"type": "delay", "ms": 100}]},
        {"type": "repeat", "count": 2,
         "steps": [{"type": "action", "action_id": ka}]},
        {"type": "repeat_until",
         "conditions": [cond("window_title", process="",
                             title="*YouTube*")],
         "mode": "all", "max_iterations": 10, "delay_ms": 500,
         "timeout_ms": 10_000,
         "steps": [{"type": "delay", "ms": 50}]}]
    wid = pm.workflows.create("Loops WF", steps)
    a = pm.actions.create("Loops WF", "workflow", {"workflow_id": wid})
    pid = pm.profiles.create("X", "application", [{"process_name": "x.exe"}])
    pm.rules.create(pid, "circle", a)
    f = tmp_path / "x.json"
    pm.export_profile(pid, f)
    for r in pm.rules.for_profile(pid):
        pm.rules.delete(r.id)
    pm.profiles.delete(pid)
    pm.actions.delete(a)
    pm.actions.delete(ka)
    pm.workflows.delete(wid)
    nid = pm.import_profile(f)
    nwf = pm.workflows.get(pm.actions.get(
        pm.rules.for_profile(nid)[0].action_id).params["workflow_id"])
    assert [s["type"] for s in nwf.steps] == ["retry", "repeat",
                                              "repeat_until"]
    r0 = nwf.steps[0]
    assert (r0["attempts"], r0["delay_ms"], r0["on_fail"]) == (3, 500,
                                                               "fallback")
    assert r0["until"][0]["condition"] == "app_running"
    assert r0["fallback"][0]["type"] == "delay"
    assert r0["steps"][0]["type"] == "action"      # name-ref restored
    assert nwf.steps[1]["count"] == 2
    assert nwf.steps[2]["timeout_ms"] == 10_000
    assert pm.workflows.validate(nwf.steps) is None


def test_legacy_workflows_unchanged(h):
    legacy = [h.astep("a"), {"type": "delay", "ms": 100},
              {"type": "wait", "condition": "process_exists",
               "process": "x.exe", "title": "", "timeout_ms": 1_000},
              {"type": "if", "conditions": [cond()], "mode": "all",
               "wait_ms": 0, "on_timeout": "else",
               "then": [h.astep("b")], "else": []}]
    assert h.workflows.validate(legacy) is None
    with patch("app.context.conditions.check", lambda c, v=None: True), \
         patch.object(iw, "key_press", lambda k: None):
        _, status, _ = h.run(legacy)
    assert status == "completed"


# -- 28-29: progress + malformed validation ----------------------------------
def test_progress_events_show_attempts_not_floods(h):
    fk = FlakyKey(1)
    with patch.object(iw, "key_press", fk):
        _, status, _ = h.run([retry([h.astep("a")], attempts=3)])
    labels = [p[3] for p in h.progress]
    assert "RETRY attempt 1/3" in labels
    assert "RETRY attempt 2/3" in labels
    assert "RETRY attempt 3/3" not in labels      # stopped on success
    checks = 0
    with patch("app.context.conditions.check",
               lambda c, v=None: False):
        h.progress.clear()
        h.run([repeat_until([{"type": "delay", "ms": 10}], max_it=5)])
    # one event per iteration + terminal — never one per poll
    assert len(h.progress) <= 12


def test_malformed_loops_rejected(h):
    v = h.workflows.validate
    a = h.astep("a")
    # retry
    assert v([retry([a], attempts=21)])            # > MAX_RETRY_ATTEMPTS
    assert v([retry([a], attempts="3")])
    assert v([retry([], attempts=2)])              # empty body
    assert v([retry([a], delay_ms=-1)])
    assert v([{"type": "retry", "attempts": 2, "on_fail": "explode",
               "steps": [a]}])
    assert v([retry([a], on_fail="fallback")])     # fallback missing
    assert v([retry([a], until=[{"condition": "variable", "var": "ghost",
                                 "op": "equals", "value": "x"}])])
    # repeat_until
    bad = dict(repeat_until([a]))
    bad.pop("timeout_ms")
    assert v([bad])                                # timeout is mandatory
    assert v([repeat_until([a], max_it=101)])
    assert v([repeat_until([a], max_it=0)])
    assert v([{**repeat_until([a]), "conditions": []}])
    assert v([repeat_until([], max_it=3)])         # empty body
    assert v([{**repeat_until([a]), "timeout_ms": 121_000}])
    assert v([{**repeat_until([a]), "mode": "sometimes"}])
    # good shapes still pass
    assert v([retry([a], attempts=20, delay_ms=0)]) is None
    assert v([repeat_until([a], max_it=100, timeout_ms=120_000)]) is None
