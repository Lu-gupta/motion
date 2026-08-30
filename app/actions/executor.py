"""Action executor.

Executes ActionSpec objects. Independent of gesture recognition —
callable from tests, from the UI "Test" button, and from the rule engine.

Action type registry (params schema is used by the UI to build forms):

mouse_move        {mode: "relative"|"absolute", dx, dy, x, y}
mouse_click       {button: left|right|middle, count: 1|2}
mouse_down        {button}
mouse_up          {button}
mouse_scroll      {amount: int (+up/-down), horizontal: bool}
key_press         {key: str}
hotkey            {keys: "ctrl+shift+t"}
window            {op: minimize|maximize|restore|close|switch}
volume            {op: up|down|mute, steps: int}
media             {op: play_pause|next|prev}
launch_app        {path: str, args: str}
open_url          {url: str}
open_folder       {path: str}
sequence          {steps: [{type, params, delay_ms_after}]}
workflow          {workflow_id: int} — starts a stored multi-step workflow
cursor_control    {}   — continuous: hand position drives the cursor
"""
from __future__ import annotations

import logging
import threading
import time

from ..core.events import EventBus
from ..core.types import ActionSpec
from . import input_win, system_actions, windows_ctl

log = logging.getLogger(__name__)

MAX_SEQUENCE_STEPS = 50
MAX_STEP_DELAY_MS = 10_000

ACTION_TYPES: dict[str, dict] = {
    "mouse_click": {"label": "Mouse click",
                    "params": {"button": ["left", "right", "middle"],
                               "count": "int"}},
    "mouse_scroll": {"label": "Mouse scroll",
                     "params": {"amount": "int", "horizontal": "bool"}},
    "mouse_move": {"label": "Mouse move",
                   "params": {"mode": ["relative", "absolute"], "dx": "int",
                              "dy": "int", "x": "int", "y": "int"}},
    "mouse_down": {"label": "Mouse button down", "params": {"button": ["left", "right", "middle"]}},
    "mouse_up": {"label": "Mouse button up", "params": {"button": ["left", "right", "middle"]}},
    "key_press": {"label": "Key press", "params": {"key": "str"}},
    "hotkey": {"label": "Keyboard shortcut", "params": {"keys": "str"}},
    "window": {"label": "Window control",
               "params": {"op": ["minimize", "maximize", "restore", "close",
                                 "switch"]}},
    "volume": {"label": "Volume",
               "params": {"op": ["up", "down", "mute"], "steps": "int"}},
    "media": {"label": "Media",
              "params": {"op": ["play_pause", "next", "prev"]}},
    "launch_app": {"label": "Launch application",
                   "params": {"path": "str", "args": "str", "cwd": "str",
                              "if_running": ["new", "focus"]}},
    "open_url": {"label": "Open URL", "params": {"url": "str"}},
    "open_folder": {"label": "Open folder", "params": {"path": "str"}},
    "sequence": {"label": "Action sequence", "params": {"steps": "list"}},
    "workflow": {"label": "Workflow (multi-step)",
                 "params": {"workflow_id": "int"}},
    "cursor_control": {"label": "Cursor follows hand (continuous)",
                       "params": {}},
}


class ActionExecutor:
    def __init__(self, bus: EventBus | None = None) -> None:
        self.bus = bus
        self._lock = threading.Lock()
        # set by the runtime controller; workflow actions delegate here
        self.workflows = None

    # -- public -------------------------------------------------------------
    def execute(self, spec: ActionSpec) -> bool:
        """Execute an action. Returns True on success. Never raises."""
        ok, detail = True, ""
        try:
            with self._lock:
                self._dispatch(spec.type, spec.params or {})
        except Exception as e:  # noqa: BLE001 — must not kill pipeline
            ok, detail = False, str(e)
            log.exception("Action failed: %s %s", spec.type, spec.params)
        if self.bus:
            self.bus.publish("action.executed", spec, ok, detail)
        return ok

    def validate(self, type_: str, params: dict) -> str | None:
        """Return an error message or None if the spec looks executable."""
        if type_ not in ACTION_TYPES:
            return f"unknown action type: {type_}"
        try:
            if type_ == "hotkey":
                input_win.parse_shortcut(params.get("keys", ""))
            elif type_ == "key_press":
                input_win.parse_key(params.get("key", ""))
            elif type_ == "launch_app":
                return system_actions.validate_launch(
                    params.get("path", ""), params.get("cwd", ""),
                    params.get("if_running", "new"))
            elif type_ == "open_url":
                return system_actions.validate_url(params.get("url", ""))
            elif type_ == "workflow":
                if self.workflows is not None:
                    wf = self.workflows.repo.get(
                        int(params.get("workflow_id", 0) or 0))
                    if wf is None:
                        return "workflow does not exist"
            elif type_ == "sequence":
                steps = params.get("steps", [])
                if not isinstance(steps, list) or not steps:
                    return "sequence needs at least one step"
                if len(steps) > MAX_SEQUENCE_STEPS:
                    return f"sequence too long (max {MAX_SEQUENCE_STEPS})"
                for s in steps:
                    if s.get("type") in ("sequence", "workflow"):
                        return ("sequences cannot contain "
                                f"{s.get('type')} steps")
                    err = self.validate(s.get("type", ""),
                                        s.get("params", {}))
                    if err:
                        return f"step error: {err}"
        except ValueError as e:
            return str(e)
        return None

    # -- dispatch -----------------------------------------------------------
    def _dispatch(self, type_: str, p: dict) -> None:
        if type_ == "mouse_click":
            input_win.click(p.get("button", "left"),
                            int(p.get("count", 1)))
        elif type_ == "mouse_scroll":
            input_win.scroll(int(p.get("amount", -2)),
                             bool(p.get("horizontal", False)))
        elif type_ == "mouse_move":
            if p.get("mode", "relative") == "absolute":
                input_win.move_to(int(p.get("x", 0)), int(p.get("y", 0)))
            else:
                input_win.move_rel(int(p.get("dx", 0)), int(p.get("dy", 0)))
        elif type_ == "mouse_down":
            input_win.button_down(p.get("button", "left"))
        elif type_ == "mouse_up":
            input_win.button_up(p.get("button", "left"))
        elif type_ == "key_press":
            input_win.key_press(p["key"])
        elif type_ == "hotkey":
            input_win.hotkey(*input_win.parse_shortcut(p["keys"]))
        elif type_ == "window":
            op = p.get("op", "")
            {"minimize": windows_ctl.minimize,
             "maximize": windows_ctl.maximize,
             "restore": windows_ctl.restore,
             "close": windows_ctl.close,
             "switch": windows_ctl.switch_window}[op]()
        elif type_ == "volume":
            op = p.get("op", "up")
            steps = int(p.get("steps", 2))
            {"up": lambda: system_actions.volume_up(steps),
             "down": lambda: system_actions.volume_down(steps),
             "mute": system_actions.volume_mute}[op]()
        elif type_ == "media":
            {"play_pause": system_actions.media_play_pause,
             "next": system_actions.media_next,
             "prev": system_actions.media_prev}[p.get("op", "play_pause")]()
        elif type_ == "launch_app":
            system_actions.launch_app(p.get("path", ""), p.get("args", ""),
                                      p.get("cwd", ""),
                                      p.get("if_running", "new"))
        elif type_ == "open_url":
            system_actions.open_url(p.get("url", ""))
        elif type_ == "open_folder":
            system_actions.open_folder(p.get("path", ""))
        elif type_ == "sequence":
            self._run_sequence(p.get("steps", []))
        elif type_ == "workflow":
            if self.workflows is None:
                raise ValueError("workflow engine is not available")
            started, reason = self.workflows.start(
                int(p.get("workflow_id", 0) or 0))
            if not started:
                raise ValueError(reason)
        elif type_ == "cursor_control":
            pass  # continuous action, handled by the runtime controller
        else:
            raise ValueError(f"unknown action type: {type_}")

    def _run_sequence(self, steps: list[dict]) -> None:
        if len(steps) > MAX_SEQUENCE_STEPS:
            raise ValueError("sequence too long")
        for s in steps:
            stype = s.get("type", "")
            if stype in ("sequence", "workflow"):
                raise ValueError(f"sequences cannot contain {stype} steps")
            self._dispatch(stype, s.get("params", {}))
            delay = min(int(s.get("delay_ms_after", 0)), MAX_STEP_DELAY_MS)
            if delay > 0:
                time.sleep(delay / 1000.0)
