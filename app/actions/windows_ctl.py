"""Window management actions (win32)."""
from __future__ import annotations

import logging

import win32con
import win32gui

log = logging.getLogger(__name__)


def _foreground() -> int:
    return win32gui.GetForegroundWindow()


def minimize() -> None:
    hwnd = _foreground()
    if hwnd:
        win32gui.ShowWindow(hwnd, win32con.SW_MINIMIZE)


def maximize() -> None:
    hwnd = _foreground()
    if hwnd:
        win32gui.ShowWindow(hwnd, win32con.SW_MAXIMIZE)


def restore() -> None:
    hwnd = _foreground()
    if hwnd:
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)


def close() -> None:
    hwnd = _foreground()
    if hwnd:
        win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)


def switch_window() -> None:
    """Alt+Tab via keyboard injection (most reliable)."""
    from .input_win import hotkey
    hotkey("alt", "tab")


def _process_name_of(hwnd: int) -> str:
    import ctypes
    import win32process
    try:
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        if pid <= 0:
            return ""
        h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not h:
            return ""
        try:
            buf = ctypes.create_unicode_buffer(1024)
            size = ctypes.c_ulong(1024)
            if ctypes.windll.kernel32.QueryFullProcessImageNameW(
                    h, 0, buf, ctypes.byref(size)):
                import os
                return os.path.basename(buf.value).lower()
            return ""
        finally:
            ctypes.windll.kernel32.CloseHandle(h)
    except Exception:
        return ""


def focus_process(process_name: str) -> bool:
    """Bring a visible top-level window of the given executable to the
    foreground. Returns False when no such window exists."""
    import ctypes
    found: list[int] = []

    def cb(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return
        if not win32gui.GetWindowText(hwnd):
            return
        if _process_name_of(hwnd) == process_name.lower():
            found.append(hwnd)

    try:
        win32gui.EnumWindows(cb, None)
    except Exception:
        log.exception("EnumWindows failed")
        return False
    if not found:
        return False
    hwnd = found[0]
    ALT = 0x12
    ctypes.windll.user32.keybd_event(ALT, 0, 0, 0)  # satisfy SFW rules
    try:
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        win32gui.SetForegroundWindow(hwnd)
    except Exception:
        log.exception("SetForegroundWindow failed")
        return False
    finally:
        ctypes.windll.user32.keybd_event(ALT, 0, 2, 0)
    return True
