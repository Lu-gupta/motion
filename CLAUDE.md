# Motion Gesture App — project guide

Windows desktop app: control the PC with camera-detected hand gestures.
Python 3.11, PySide6, MediaPipe Tasks, SQLite, SendInput/win32.

## Commands

```powershell
.\.venv\Scripts\python.exe run.py              # run GUI
.\.venv\Scripts\python.exe run.py --selftest   # headless init check
.\.venv\Scripts\python.exe -m pytest tests -q  # test suite
.\.venv\Scripts\python.exe scripts\manual_action_test.py all  # real-input manual pass
```

## Architecture (see ARCHITECTURE.md)

Event-bus pipeline: `camera → vision → gestures → runtime controller →
rules → actions`. Subsystem boundaries are hard:

- MediaPipe types stay inside `app/vision/`.
- Win32/SendInput stays inside `app/actions/` and `app/context/`.
- SQL stays inside `app/data/`.
- The gesture engine emits events only — it never executes actions.
- UI binds to runtime state via `app/ui/bridge.py` Qt signals; never poll
  Win32 or the DB from widgets directly.

## Conventions

- Internal types in `app/core/types.py`; keep them dependency-free.
- Rules/actions/profiles are data (SQLite) — never hard-code app-specific
  behavior in logic.
- Motion control must default to disarmed; any new action path must respect
  cooldowns and the `continuous` flag.
- Long-running work (workflow delays etc.) never blocks the GUI/camera
  threads and must cancel on `control.enabled False`.
- Tests isolate data via `MGA_DATA_DIR` env var.
- App data: `%APPDATA%\MotionGestureApp\` (config.json, app.db, app.log,
  models/).
