"""Launch Application action tests (spec §18).

Process creation is mocked — no real applications are launched.
Validation runs against real temp files.
"""
import time
from unittest.mock import patch

import pytest

from app.actions import system_actions
from app.actions.executor import ActionExecutor
from app.core.config import Config
from app.core.events import EventBus
from app.core.types import ActionSpec, Context, GestureEvent
from app.data.db import Database
from app.runtime.controller import MotionController


@pytest.fixture
def fake_exe(tmp_path):
    p = tmp_path / "FakeApp.exe"
    p.write_bytes(b"MZ fake")
    return str(p)


# -- 1-5: validation --------------------------------------------------------
def test_valid_executable(fake_exe, tmp_path):
    assert system_actions.validate_launch(fake_exe) is None
    assert system_actions.validate_launch(fake_exe,
                                          cwd=str(tmp_path)) is None


def test_missing_and_invalid_paths(tmp_path):
    assert "required" in system_actions.validate_launch("")
    assert "not found" in system_actions.validate_launch(
        str(tmp_path / "nope.exe"))
    txt = tmp_path / "not_an_exe.txt"
    txt.write_text("x")
    assert ".exe" in system_actions.validate_launch(str(txt))


def test_bad_working_directory(fake_exe, tmp_path):
    err = system_actions.validate_launch(fake_exe,
                                         cwd=str(tmp_path / "missing"))
    assert "working directory" in err


def test_bad_if_running(fake_exe):
    assert "if_running" in system_actions.validate_launch(
        fake_exe, if_running="maybe")


def test_argument_parsing_safe():
    assert system_actions.parse_launch_args("") == []
    assert system_actions.parse_launch_args("-a -b") == ["-a", "-b"]
    assert system_actions.parse_launch_args(
        '--file "C:\\My Documents\\x.txt" -v') == \
        ["--file", "C:\\My Documents\\x.txt", "-v"]


def test_launch_uses_direct_process_no_shell(fake_exe, tmp_path):
    with patch("app.actions.system_actions.subprocess.Popen") as popen:
        system_actions.launch_app(fake_exe, '--flag "a b"', str(tmp_path))
        popen.assert_called_once()
        args, kwargs = popen.call_args
        assert args[0] == [fake_exe, "--flag", "a b"]  # list, not string
        assert kwargs["shell"] is False
        assert kwargs["cwd"] == str(tmp_path)


def test_focus_existing_skips_launch(fake_exe):
    with patch("app.actions.windows_ctl.focus_process",
               return_value=True) as focus, \
         patch("app.actions.system_actions.subprocess.Popen") as popen:
        system_actions.launch_app(fake_exe, if_running="focus")
        focus.assert_called_once_with("fakeapp.exe")
        popen.assert_not_called()


def test_focus_falls_back_to_launch(fake_exe):
    with patch("app.actions.windows_ctl.focus_process",
               return_value=False), \
         patch("app.actions.system_actions.subprocess.Popen") as popen:
        system_actions.launch_app(fake_exe, if_running="focus")
        popen.assert_called_once()


# -- executor integration ---------------------------------------------------
def test_executor_validate_and_failure_detail(fake_exe, tmp_path):
    ex = ActionExecutor()
    assert ex.validate("launch_app", {"path": fake_exe}) is None
    assert "not found" in ex.validate(
        "launch_app", {"path": str(tmp_path / "ghost.exe")})
    # runtime failure returns False and publishes a reason, no crash
    bus = EventBus()
    results = []
    bus.subscribe("action.executed", lambda *a: results.append(a))
    ex2 = ActionExecutor(bus)
    ok = ex2.execute(ActionSpec(type="launch_app",
                                params={"path": str(tmp_path / "ghost.exe")},
                                name="Ghost"))
    assert not ok
    assert "not found" in results[0][2]


def test_launch_inside_sequence(fake_exe):
    ex = ActionExecutor()
    assert ex.validate("sequence", {"steps": [
        {"type": "launch_app", "params": {"path": fake_exe},
         "delay_ms_after": 100},
        {"type": "launch_app", "params": {"path": fake_exe}},
    ]}) is None
    with patch("app.actions.system_actions.subprocess.Popen") as popen, \
         patch("app.actions.executor.time.sleep"):
        ok = ex.execute(ActionSpec(type="sequence", params={"steps": [
            {"type": "launch_app", "params": {"path": fake_exe}},
            {"type": "launch_app", "params": {"path": fake_exe}}]}))
    assert ok
    assert popen.call_count == 2


# -- 6-10: repository / serialization ---------------------------------------
def test_named_action_crud_and_serialization(tmp_path, fake_exe):
    from app.data.repository import ActionRepo
    repo = ActionRepo(Database(tmp_path / "a.db"))
    params = {"path": fake_exe, "args": "-x", "cwd": "",
              "if_running": "new"}
    aid = repo.create("Open FakeApp", "launch_app", params)
    row = repo.get(aid)
    assert row.type == "launch_app"
    assert row.params == params            # round-trips through JSON
    repo.update(aid, params={**params, "args": "-y"})
    assert repo.get(aid).params["args"] == "-y"
    repo.delete(aid)
    assert repo.get(aid) is None


def test_profile_export_import_with_launch_action(tmp_path, fake_exe,
                                                  monkeypatch):
    monkeypatch.setenv("MGA_DATA_DIR", str(tmp_path))
    from app.profiles.manager import ProfileManager
    pm = ProfileManager(Database(tmp_path / "p.db"))
    pm.seed_defaults()
    aid = pm.actions.create("Open FakeApp", "launch_app",
                            {"path": fake_exe, "args": "", "cwd": "",
                             "if_running": "focus"})
    pid = pm.profiles.create("X", "application",
                             [{"process_name": "x.exe"}])
    pm.rules.create(pid, "pinch", aid)
    f = tmp_path / "x.json"
    pm.export_profile(pid, f)
    nid = pm.import_profile(f)
    rules = pm.rules.for_profile(nid)
    a = pm.actions.get(rules[0].action_id)
    assert a.type == "launch_app"
    assert a.params["if_running"] == "focus"


# -- 11-22: runtime integration ---------------------------------------------
@pytest.fixture
def ctl(tmp_path, monkeypatch, fake_exe):
    monkeypatch.setenv("MGA_DATA_DIR", str(tmp_path))
    cfg = Config()
    cfg.default_cooldown_ms = 0
    cfg.action_global_cooldown_ms = 0
    db = Database(tmp_path / "rt.db")
    bus = EventBus()
    c = MotionController(cfg, db, bus)
    launches = []
    monkeypatch.setattr(
        "app.actions.system_actions.subprocess.Popen",
        lambda cmd, **kw: launches.append(tuple(cmd)))
    pm = c.profile_manager
    g = pm.profiles.by_name("Global")
    for r in pm.rules.for_profile(g.id):
        pm.rules.delete(r.id)
    c.reload_rules()
    c.set_motion_enabled(True)
    return c, bus, launches, fake_exe


def ev(gesture, phase, source="primitive"):
    return GestureEvent(gesture=gesture, phase=phase, confidence=0.9,
                        handedness="Right", hand_x=0.5, hand_y=0.5,
                        timestamp=time.monotonic(), source=source)


def set_ctx(c, process, title=""):
    app = process[:-4] if process.endswith(".exe") else process
    c.context.current = Context(application=app, process=process,
                                window_title=title)


def launch_action(c, name, exe):
    return c.profile_manager.actions.create(
        name, "launch_app", {"path": exe, "args": "", "cwd": "",
                             "if_running": "new"})


def test_global_launch_mapping_any_gesture_kind(ctl):
    """Primitive, swipe (dynamic) and custom names share one code path."""
    c, bus, launches, exe = ctl
    aid = launch_action(c, "Open FakeApp", exe)
    g = c.profile_manager.profiles.by_name("Global")
    for gesture in ("pinch", "swipe_right", "victory"):
        c.profile_manager.rules.create(g.id, gesture, aid)
    c.reload_rules()
    set_ctx(c, "notepad.exe")
    for gesture in ("pinch", "swipe_right", "victory"):
        bus.publish("gesture.event", ev(gesture, "start"))
        bus.publish("gesture.event", ev(gesture, "end"))
        time.sleep(0.02)
    time.sleep(0.1)
    assert len(launches) == 3


def test_context_specific_launch_precedence(ctl):
    c, bus, launches, exe = ctl
    pm = c.profile_manager
    a_launch = launch_action(c, "Open FakeApp", exe)
    a_click = pm.actions.by_name("Right click")
    g = pm.profiles.by_name("Global")
    pm.rules.create(g.id, "pinch", a_click.id)
    excel = pm.profiles.create("Excel", "application",
                               [{"process_name": "excel.exe"}])
    pm.rules.create(excel, "pinch", a_launch,
                    window_pattern="*Budget*")   # window-specific launch
    pm.rules.create(excel, "pinch", a_launch)     # app-specific launch
    c.reload_rules()
    clicks = []
    c.executor._dispatch  # keep real dispatch for launch; count clicks via mock
    import app.actions.input_win as iw
    with patch.object(iw, "click", lambda *a, **k: clicks.append(1)):
        set_ctx(c, "excel.exe", "Budget2026.xlsx - Excel")
        bus.publish("gesture.event", ev("pinch", "start"))
        bus.publish("gesture.event", ev("pinch", "end"))
        set_ctx(c, "excel.exe", "Book1 - Excel")
        bus.publish("gesture.event", ev("pinch", "start"))
        bus.publish("gesture.event", ev("pinch", "end"))
        set_ctx(c, "notepad.exe", "Untitled")
        bus.publish("gesture.event", ev("pinch", "start"))
    assert len(launches) == 2     # window-specific + app-specific
    assert len(clicks) == 1       # global fallback elsewhere


def test_compound_launch_with_arbitration_no_side_effect(ctl):
    """Double pinch launches exactly once; primitive click suppressed."""
    c, bus, launches, exe = ctl
    pm = c.profile_manager
    a_launch = launch_action(c, "Open FakeApp", exe)
    a_key = pm.actions.create("K", "key_press", {"key": "k"})
    g = pm.profiles.by_name("Global")
    pm.rules.create(g.id, "pinch", a_key)
    c.compound_gestures.create(
        "double_pinch", [{"type": "gesture", "gesture": "pinch"}] * 2,
        step_timeout_ms=250, cooldown_ms=0)
    pm.rules.create(g.id, "double_pinch", a_launch)
    c.reload_rules()
    keys = []
    import app.actions.input_win as iw
    with patch.object(iw, "key_press", lambda k: keys.append(k)):
        set_ctx(c, "notepad.exe")
        bus.publish("gesture.event", ev("pinch", "start"))
        bus.publish("gesture.event", ev("pinch", "end"))
        bus.publish("gesture.event", ev("pinch", "start"))
        bus.publish("gesture.event", ev("pinch", "end"))
        time.sleep(0.5)
    assert len(launches) == 1     # compound exactly once
    assert keys == []             # primitive suppressed


def test_disabled_rule_no_launch(ctl):
    c, bus, launches, exe = ctl
    pm = c.profile_manager
    aid = launch_action(c, "Open FakeApp", exe)
    g = pm.profiles.by_name("Global")
    rid = pm.rules.create(g.id, "fist", aid)
    pm.rules.update(rid, enabled=False)
    c.reload_rules()
    set_ctx(c, "notepad.exe")
    bus.publish("gesture.event", ev("fist", "start"))
    assert launches == []


def test_motion_off_and_emergency_no_launch(ctl):
    c, bus, launches, exe = ctl
    pm = c.profile_manager
    aid = launch_action(c, "Open FakeApp", exe)
    g = pm.profiles.by_name("Global")
    pm.rules.create(g.id, "fist", aid)
    c.reload_rules()
    set_ctx(c, "notepad.exe")
    c.set_motion_enabled(False)
    bus.publish("gesture.event", ev("fist", "start"))
    assert launches == []
    c.set_motion_enabled(True)
    c.emergency_disable()
    bus.publish("gesture.event", ev("fist", "start"))
    assert launches == []


def test_hot_reload_launch_action(ctl):
    c, bus, launches, exe = ctl
    set_ctx(c, "notepad.exe")
    bus.publish("gesture.event", ev("thumb_up", "start"))
    assert launches == []          # nothing mapped yet
    aid = launch_action(c, "Open FakeApp", exe)
    g = c.profile_manager.profiles.by_name("Global")
    c.profile_manager.rules.create(g.id, "thumb_up", aid)
    c.reload_rules()               # what the UI calls on save
    bus.publish("gesture.event", ev("thumb_up", "end"))
    bus.publish("gesture.event", ev("thumb_up", "start"))
    assert len(launches) == 1


def test_safe_test_recognition_never_launches(ctl, qapp=None):
    """Resolution shows the launch action; nothing is executed."""
    c, bus, launches, exe = ctl
    pm = c.profile_manager
    aid = launch_action(c, "Open FakeApp", exe)
    g = pm.profiles.by_name("Global")
    pm.rules.create(g.id, "pinch", aid)
    c.reload_rules()
    set_ctx(c, "notepad.exe")
    match = c.rules.resolve("pinch", c.context.current, (1920, 1080))
    assert match.action.name == "Open FakeApp"   # "would run" display data
    assert launches == []                        # resolving ≠ launching
