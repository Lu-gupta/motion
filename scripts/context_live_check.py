"""Live verification: context detection + rule precedence with REAL apps.

Launches/uses real Windows applications, brings each to the foreground,
and verifies:
  1. the context engine identifies process/application/title correctly
  2. the rule engine resolves the right profile for a pinch in each app

Uses a temporary data dir with demo profiles (Global/Excel/Chrome) so the
user's real configuration is untouched. Actions are NOT executed — the
resolver is exercised, the executor is not.

Usage:
    .venv\\Scripts\\python.exe scripts\\context_live_check.py
"""
from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["MGA_DATA_DIR"] = tempfile.mkdtemp(prefix="mga_ctx_check_")

import win32con  # noqa: E402
import win32gui  # noqa: E402
import win32process  # noqa: E402

from app.context.detector import snapshot  # noqa: E402
from app.core.types import Context  # noqa: E402
from app.data.db import Database  # noqa: E402
from app.profiles.manager import ProfileManager  # noqa: E402
from app.rules.engine import RuleEngine  # noqa: E402

TARGETS = {
    "notepad.exe": r"C:\Windows\System32\notepad.exe",
    "chrome.exe": r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    "excel.exe": (r"C:\Program Files\Microsoft Office\root\Office16"
                  r"\EXCEL.EXE"),
}


def find_window_for(process_name: str) -> int:
    """Top-level visible window belonging to the given process."""
    result = []

    def cb(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd) or not win32gui.GetWindowText(hwnd):
            return
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        try:
            import ctypes as ct
            h = ct.windll.kernel32.OpenProcess(0x1000, False, pid)
            if h:
                buf = ct.create_unicode_buffer(1024)
                size = ct.c_ulong(1024)
                if ct.windll.kernel32.QueryFullProcessImageNameW(
                        h, 0, buf, ct.byref(size)):
                    if os.path.basename(buf.value).lower() == process_name:
                        result.append(hwnd)
                ct.windll.kernel32.CloseHandle(h)
        except Exception:
            pass

    win32gui.EnumWindows(cb, None)
    return result[0] if result else 0


def force_foreground(hwnd: int) -> None:
    # simulate an Alt press to satisfy SetForegroundWindow rules
    ALT = 0x12
    ctypes.windll.user32.keybd_event(ALT, 0, 0, 0)
    try:
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        win32gui.SetForegroundWindow(hwnd)
    finally:
        ctypes.windll.user32.keybd_event(ALT, 0, 2, 0)  # KEYEVENTF_KEYUP
    time.sleep(0.6)


def main() -> None:
    # -- demo rules: same gesture, different action per app ---------------
    db = Database()
    pm = ProfileManager(db)
    pm.seed_defaults()
    a_ws = pm.actions.create("Demo Excel action", "hotkey",
                             {"keys": "shift+f11"})
    a_tab = pm.actions.create("Demo Chrome action", "hotkey",
                              {"keys": "ctrl+t"})
    ex = pm.profiles.create("Excel", "application",
                            [{"process_name": "excel.exe"}])
    ch = pm.profiles.create("Chrome", "application",
                            [{"process_name": "chrome.exe"}])
    pm.rules.create(ex, "pinch", a_ws)
    pm.rules.create(ch, "pinch", a_tab)
    engine = RuleEngine(pm.profiles, pm.rules, pm.actions)

    launched: list[subprocess.Popen] = []
    checks: list[tuple[str, bool, str]] = []

    for proc, exe in TARGETS.items():
        if not os.path.exists(exe):
            checks.append((proc, False, "not installed — skipped"))
            continue
        hwnd = find_window_for(proc)
        if not hwnd:
            launched.append(subprocess.Popen([exe]))
            deadline = time.time() + 25
            while time.time() < deadline and not hwnd:
                time.sleep(1.0)
                hwnd = find_window_for(proc)
        if not hwnd:
            checks.append((proc, False, "no window appeared"))
            continue
        force_foreground(hwnd)
        ctx = snapshot()

        det_ok = ctx.process == proc
        match = engine.resolve("pinch", ctx, (1920, 1080))
        expected_profile = {"notepad.exe": "Global", "chrome.exe": "Chrome",
                            "excel.exe": "Excel"}[proc]
        res_ok = match is not None and match.profile_name == expected_profile
        checks.append((
            proc, det_ok and res_ok,
            f"detected process={ctx.process!r} app={ctx.application!r} "
            f"title={ctx.window_title[:40]!r} → profile="
            f"{match.profile_name if match else 'NO MATCH'} action="
            f"{match.action.name if match else '-'}"))

    print("\n=== Live context + precedence check ===")
    ok_all = True
    for proc, ok, detail in checks:
        print(f"[{'PASS' if ok else 'FAIL'}] {proc}: {detail}")
        ok_all = ok_all and (ok or "skipped" in detail)

    # desktop / self check
    ctx = snapshot()
    print(f"[info] final foreground: {ctx.process} {ctx.window_title[:40]!r}")
    db.close()
    print("RESULT:", "ALL PASS" if ok_all else "FAILURES PRESENT")
    sys.exit(0 if ok_all else 1)


if __name__ == "__main__":
    main()
