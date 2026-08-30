"""Active-context detection (Windows).

Polls foreground window + cursor; publishes context.changed (Context)
whenever the application/window changes. Win32 calls stop here — the
rest of the app consumes the normalized Context type.
"""
from __future__ import annotations

import logging
import os
import threading

import win32api
import win32gui
import win32process

from ..core.events import EventBus
from ..core.types import Context

log = logging.getLogger(__name__)

# Shell/desktop process names treated as "desktop" context
_DESKTOP_CLASSES = {"Progman", "WorkerW"}

# Locked-workstation dampening: GetCursorPos etc. fail on the secure
# desktop; log one WARNING per failure streak, the rest at DEBUG.
_snapshot_failing = False


def _process_name(hwnd: int) -> str:
    try:
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        if pid <= 0:
            return ""
        import ctypes
        h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # LIMITED_INFO
        if not h:
            return ""
        try:
            buf = ctypes.create_unicode_buffer(1024)
            size = ctypes.c_ulong(1024)
            if ctypes.windll.kernel32.QueryFullProcessImageNameW(
                    h, 0, buf, ctypes.byref(size)):
                return os.path.basename(buf.value).lower()
            return ""
        finally:
            ctypes.windll.kernel32.CloseHandle(h)
    except Exception:
        return ""


def snapshot() -> Context:
    """Take one context snapshot. Safe to call from any thread."""
    global _snapshot_failing
    try:
        hwnd = win32gui.GetForegroundWindow()
        title = win32gui.GetWindowText(hwnd) if hwnd else ""
        cls = win32gui.GetClassName(hwnd) if hwnd else ""
        proc = _process_name(hwnd) if hwnd else ""
        cx, cy = win32api.GetCursorPos()

        screen = 0
        try:
            monitors = win32api.EnumDisplayMonitors()
            for i, (_, _, rect) in enumerate(monitors):
                if rect[0] <= cx < rect[2] and rect[1] <= cy < rect[3]:
                    screen = i
                    break
        except Exception:
            pass

        _snapshot_failing = False
        if cls in _DESKTOP_CLASSES or (proc == "explorer.exe" and not title):
            return Context(application="desktop", process="explorer.exe",
                           window_title="Desktop", cursor_x=cx, cursor_y=cy,
                           screen=screen)
        app = proc[:-4] if proc.endswith(".exe") else proc
        return Context(application=app or "unknown", process=proc,
                       window_title=title, cursor_x=cx, cursor_y=cy,
                       screen=screen)
    except Exception:
        # Expected while the workstation is locked (secure desktop denies
        # cursor/window queries) — don't flood the log at ERROR level.
        if not _snapshot_failing:
            _snapshot_failing = True
            log.warning("Context snapshot unavailable (workstation "
                        "locked?) — repeats logged at DEBUG until it "
                        "recovers", exc_info=True)
        else:
            log.debug("Context snapshot still failing")
        return Context.desktop()


class ContextDetector:
    """Background poller. Publishes context.changed on app/window change."""

    def __init__(self, bus: EventBus, poll_ms: int = 200) -> None:
        self.bus = bus
        self.poll_s = poll_ms / 1000.0
        self.current = Context.desktop()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="context",
                                        daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None

    def _run(self) -> None:
        while not self._stop.wait(self.poll_s):
            ctx = snapshot()
            prev = self.current
            self.current = ctx
            if (ctx.process != prev.process
                    or ctx.window_title != prev.window_title):
                self.bus.publish("context.changed", ctx)
