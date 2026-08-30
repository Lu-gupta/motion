"""Action engine tests — validation + safe dispatch paths only.

Input-injecting actions are NOT fired in unit tests; SendInput calls are
covered by scripts/manual_action_test.py (manual verification).
"""
from unittest.mock import patch

from app.actions import input_win
from app.actions.executor import ACTION_TYPES, ActionExecutor
from app.core.events import EventBus
from app.core.types import ActionSpec


def test_parse_key_known():
    assert input_win.parse_key("a") == ord("A")
    assert input_win.parse_key("Enter") == 0x0D
    assert input_win.parse_key("volumeup") == 0xAF


def test_parse_key_unknown():
    import pytest
    with pytest.raises(ValueError):
        input_win.parse_key("notakey")


def test_parse_shortcut():
    assert input_win.parse_shortcut("Ctrl+Shift+T") == ["ctrl", "shift", "t"]


def test_validate_unknown_type():
    ex = ActionExecutor()
    assert ex.validate("teleport", {}) is not None


def test_validate_bad_hotkey():
    ex = ActionExecutor()
    assert ex.validate("hotkey", {"keys": "ctrl+nope"}) is not None
    assert ex.validate("hotkey", {"keys": "ctrl+c"}) is None


def test_validate_sequence_rules():
    ex = ActionExecutor()
    assert ex.validate("sequence", {"steps": []}) is not None
    assert ex.validate("sequence", {"steps": [
        {"type": "sequence", "params": {}}]}) is not None
    ok = ex.validate("sequence", {"steps": [
        {"type": "key_press", "params": {"key": "a"}, "delay_ms_after": 100},
        {"type": "hotkey", "params": {"keys": "ctrl+v"}},
    ]})
    assert ok is None


def test_execute_failure_returns_false_and_publishes():
    bus = EventBus()
    results = []
    bus.subscribe("action.executed", lambda *a: results.append(a))
    ex = ActionExecutor(bus)
    ok = ex.execute(ActionSpec(type="open_folder",
                               params={"path": "Z:/definitely/missing"}))
    assert not ok
    assert results[0][1] is False


def test_sequence_dispatch_order_and_delay():
    ex = ActionExecutor()
    calls = []
    with patch.object(ex, "_dispatch", wraps=None) as _:
        pass  # can't wrap easily; instead patch input funcs

    with patch("app.actions.executor.input_win.key_press",
               side_effect=lambda k: calls.append(("key", k))), \
         patch("app.actions.executor.time.sleep",
               side_effect=lambda s: calls.append(("sleep", s))):
        ok = ex.execute(ActionSpec(type="sequence", params={"steps": [
            {"type": "key_press", "params": {"key": "a"},
             "delay_ms_after": 200},
            {"type": "key_press", "params": {"key": "b"}},
        ]}))
    assert ok
    assert calls == [("key", "a"), ("sleep", 0.2), ("key", "b")]


def test_cursor_control_is_noop_in_executor():
    ex = ActionExecutor()
    assert ex.execute(ActionSpec(type="cursor_control", params={}))


def test_registry_covers_dispatch():
    """Every registered type must validate cleanly with defaults or be
    param-validated."""
    ex = ActionExecutor()
    for t in ACTION_TYPES:
        if t in ("hotkey", "key_press", "sequence", "launch_app",
                 "open_url"):
            continue  # these require validated params
        assert ex.validate(t, {}) is None, t
