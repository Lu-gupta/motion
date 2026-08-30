"""Manual verification of the Action Engine (spec §9, §21).

Runs each action category interactively so a human can confirm real
Windows behavior. Only safe actions are included by default.

Usage:
    .venv\\Scripts\\python.exe scripts\\manual_action_test.py            # list
    .venv\\Scripts\\python.exe scripts\\manual_action_test.py mouse     # group
    .venv\\Scripts\\python.exe scripts\\manual_action_test.py all
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.actions.executor import ActionExecutor  # noqa: E402
from app.core.types import ActionSpec  # noqa: E402

GROUPS: dict[str, list[tuple[str, ActionSpec]]] = {
    "mouse": [
        ("move relative +100,+100",
         ActionSpec("mouse_move", {"mode": "relative", "dx": 100, "dy": 100})),
        ("scroll down 2", ActionSpec("mouse_scroll", {"amount": -2})),
        ("scroll up 2", ActionSpec("mouse_scroll", {"amount": 2})),
        ("middle click", ActionSpec("mouse_click", {"button": "middle"})),
    ],
    "keyboard": [
        ("press key 'a' (focus a text field!)",
         ActionSpec("key_press", {"key": "a"})),
        ("hotkey ctrl+a then ctrl+c",
         ActionSpec("sequence", {"steps": [
             {"type": "hotkey", "params": {"keys": "ctrl+a"},
              "delay_ms_after": 150},
             {"type": "hotkey", "params": {"keys": "ctrl+c"}}]})),
    ],
    "window": [
        ("minimize foreground window", ActionSpec("window", {"op": "minimize"})),
        ("switch window (alt+tab)", ActionSpec("window", {"op": "switch"})),
    ],
    "system": [
        ("volume up", ActionSpec("volume", {"op": "up", "steps": 1})),
        ("volume down", ActionSpec("volume", {"op": "down", "steps": 1})),
        ("media play/pause", ActionSpec("media", {"op": "play_pause"})),
        ("open folder C:\\", ActionSpec("open_folder", {"path": "C:\\"})),
    ],
}


def main() -> None:
    if len(sys.argv) < 2:
        print("groups:", ", ".join(GROUPS), "or 'all'")
        return
    sel = sys.argv[1].lower()
    groups = list(GROUPS) if sel == "all" else [sel]
    ex = ActionExecutor()
    for g in groups:
        for label, spec in GROUPS[g]:
            print(f"[{g}] in 3s: {label}")
            time.sleep(3)
            ok = ex.execute(spec)
            print("   ->", "OK" if ok else "FAILED")
    print("done")


if __name__ == "__main__":
    main()
