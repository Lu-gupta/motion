"""Low-level desktop capture for the workflow recorder (Windows).

Win32 stays inside this module. It installs low-level mouse/keyboard
hooks and a light foreground poll ONLY while a recording is active, and
emits normalized raw event dicts (see `app/runtime/recorder.py`) to a
callback. Nothing here runs unless `start()` was called; `stop()`
removes every hook and joins its threads.

Design constraints:

- Dormant by default — no hooks, no threads, no polling until start().
- The Motion Gesture App's own process is never captured (own PID is
  filtered on every event).
- UI targets are resolved through `app/context/uia.py` (the existing
  accessibility reference model) — never bare coordinates. Secure
  (password) inputs are flagged and their characters are NOT recorded.
- Best-effort: any hook/UIA failure degrades to "no event", never a
  crash — the recorder reports capture-start failure cleanly.
"""
from __future__ import annotations

import ctypes
import logging
import os
import threading
import time
from ctypes import wintypes

from . import uia

log = logging.getLogger(__name__)

WH_KEYBOARD_LL = 13
WH_MOUSE_LL = 14
WM_KEYDOWN = 0x0100
WM_SYSKEYDOWN = 0x0104
WM_LBUTTONDOWN = 0x0201
WM_RBUTTONDOWN = 0x0204
WM_LBUTTONDBLCLK = 0x0203
PM_REMOVE = 0x0001
LLKHF_INJECTED = 0x10

# virtual-key → workflow key name (specials that flush a typing run)
_SPECIAL_KEYS = {
    0x0D: "enter", 0x09: "tab", 0x1B: "esc", 0x08: "backspace",
    0x2E: "delete", 0x25: "left", 0x26: "up", 0x27: "right",
    0x28: "down", 0x24: "home", 0x23: "end", 0x21: "pageup",
    0x22: "pagedown",
}


class _KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD),
                ("flags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.POINTER(wintypes.ULONG))]


class _POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class _MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("pt", _POINT), ("mouseData", wintypes.DWORD),
                ("flags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.POINTER(wintypes.ULONG))]


_HOOKPROC = ctypes.CFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int,
                             wintypes.WPARAM, wintypes.LPARAM)


def _configure_signatures() -> None:
    """Pin ctypes arg/return types. Win32 handles are pointers — without
    this, ctypes' default 32-bit int return truncates them on x64 and
    SetWindowsHookExW / CallNextHookEx silently fail."""
    u = ctypes.windll.user32
    k = ctypes.windll.kernel32
    u.SetWindowsHookExW.restype = ctypes.c_void_p
    u.SetWindowsHookExW.argtypes = [ctypes.c_int, _HOOKPROC,
                                    ctypes.c_void_p, wintypes.DWORD]
    u.CallNextHookEx.restype = ctypes.c_ssize_t
    u.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                 wintypes.WPARAM, wintypes.LPARAM]
    u.UnhookWindowsHookEx.restype = wintypes.BOOL
    u.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
    k.GetModuleHandleW.restype = ctypes.c_void_p
    k.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]


def _running_process_names() -> set[str]:
    """Lowercased process names currently running (Toolhelp snapshot)."""
    names: set[str] = set()
    TH32CS_SNAPPROCESS = 0x2
    k = ctypes.windll.kernel32

    class PROCESSENTRY32(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD),
                    ("cntUsage", wintypes.DWORD),
                    ("th32ProcessID", wintypes.DWORD),
                    ("th32DefaultHeapID", ctypes.POINTER(wintypes.ULONG)),
                    ("th32ModuleID", wintypes.DWORD),
                    ("cntThreads", wintypes.DWORD),
                    ("th32ParentProcessID", wintypes.DWORD),
                    ("pcPriClassBase", ctypes.c_long),
                    ("dwFlags", wintypes.DWORD),
                    ("szExeFile", ctypes.c_char * 260)]

    snap = k.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snap == -1:
        return names
    try:
        entry = PROCESSENTRY32()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32)
        if k.Process32First(snap, ctypes.byref(entry)):
            while True:
                try:
                    names.add(entry.szExeFile.decode(errors="ignore").lower())
                except Exception:
                    pass
                if not k.Process32Next(snap, ctypes.byref(entry)):
                    break
    finally:
        k.CloseHandle(snap)
    return names


class DesktopCapture:
    """Injectable capture backend. `callback(event_dict)` is invoked for
    each meaningful raw event."""

    def __init__(self, callback) -> None:
        self.callback = callback
        self.own_pid = os.getpid()
        self._hook_thread: threading.Thread | None = None
        self._fg_thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._hook_tid = 0
        self._mouse_hook = None
        self._kbd_hook = None
        self._mouse_proc = None
        self._kbd_proc = None
        # typing session state
        self._text_lock = threading.Lock()
        self._buf: list[str] = []
        self._text_target: dict | None = None
        self._text_secure = False
        # click double-detect
        self._last_click = (0.0, 0, 0)
        # foreground / launch tracking
        self._initial_procs: set[str] = set()
        self._seen: set[str] = set()
        self._last_fg = ("", "")

    # -- lifecycle ----------------------------------------------------------
    def start(self) -> None:
        self._stop.clear()
        self._initial_procs = _running_process_names()
        self._hook_thread = threading.Thread(target=self._hook_loop,
                                             name="recorder-hooks",
                                             daemon=True)
        self._hook_thread.start()
        self._fg_thread = threading.Thread(target=self._fg_loop,
                                           name="recorder-fg", daemon=True)
        self._fg_thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._flush_text()
        # the hook thread pumps with PeekMessage and re-checks _stop every
        # tick, so setting the event is enough — no PostThreadMessage race
        # (a message posted before the thread owns a queue would be lost).
        for t in (self._hook_thread, self._fg_thread):
            if t and t is not threading.current_thread():
                t.join(timeout=2.0)
        self._hook_thread = self._fg_thread = None

    # -- foreground / launch poll ------------------------------------------
    def _fg_loop(self) -> None:
        import win32gui
        from .detector import _process_name
        while not self._stop.wait(0.2):
            try:
                hwnd = win32gui.GetForegroundWindow()
                if not hwnd:
                    continue
                import win32process
                _, pid = win32process.GetWindowThreadProcessId(hwnd)
                if pid == self.own_pid:
                    continue
                proc = _process_name(hwnd)
                title = win32gui.GetWindowText(hwnd)
                if not proc:
                    continue
                if (proc, title) == self._last_fg:
                    continue
                self._last_fg = (proc, title)
                self._flush_text()          # focus moved → end typing run
                if proc not in self._seen:
                    self._seen.add(proc)
                    if proc not in self._initial_procs:
                        self._emit({"kind": "launch", "process": proc})
                        continue
                self._emit({"kind": "foreground", "process": proc,
                            "title": title})
            except Exception:
                log.debug("foreground poll failed", exc_info=True)

    # -- hook thread --------------------------------------------------------
    def _hook_loop(self) -> None:
        _configure_signatures()
        user32 = ctypes.windll.user32
        self._hook_tid = ctypes.windll.kernel32.GetCurrentThreadId()
        self._mouse_proc = _HOOKPROC(self._on_mouse)
        self._kbd_proc = _HOOKPROC(self._on_key)
        h_mod = ctypes.windll.kernel32.GetModuleHandleW(None)
        self._mouse_hook = user32.SetWindowsHookExW(
            WH_MOUSE_LL, self._mouse_proc, h_mod, 0)
        self._kbd_hook = user32.SetWindowsHookExW(
            WH_KEYBOARD_LL, self._kbd_proc, h_mod, 0)
        if not self._mouse_hook or not self._kbd_hook:
            log.error("recorder: could not install input hooks")
            self._uninstall(user32)
            return
        # Pump with PeekMessage and re-check _stop every tick. LL-hook
        # callbacks are serviced while the queue is pumped; polling (vs a
        # blocking GetMessage woken by PostThreadMessage) makes teardown
        # race-free and bounded — stop() just sets the event.
        msg = wintypes.MSG()
        while not self._stop.is_set():
            while user32.PeekMessageW(ctypes.byref(msg), 0, 0, 0,
                                      PM_REMOVE):
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
            self._stop.wait(0.01)
        self._uninstall(user32)

    def _uninstall(self, user32) -> None:
        for hook in (self._mouse_hook, self._kbd_hook):
            if hook:
                try:
                    user32.UnhookWindowsHookEx(hook)
                except Exception:
                    pass
        self._mouse_hook = self._kbd_hook = None

    # -- mouse --------------------------------------------------------------
    def _on_mouse(self, ncode, wparam, lparam):
        try:
            if ncode >= 0 and wparam in (WM_LBUTTONDOWN, WM_RBUTTONDOWN,
                                         WM_LBUTTONDBLCLK):
                ms = ctypes.cast(lparam,
                                 ctypes.POINTER(_MSLLHOOKSTRUCT)).contents
                if not (ms.flags & LLKHF_INJECTED):
                    self._handle_click(ms.pt.x, ms.pt.y,
                                       wparam == WM_LBUTTONDBLCLK)
        except Exception:
            log.debug("mouse hook error", exc_info=True)
        return ctypes.windll.user32.CallNextHookEx(
            self._mouse_hook, ncode, wparam, lparam)

    def _handle_click(self, x, y, dbl) -> None:
        self._flush_text()                 # a click ends any typing run
        hit = uia.target_at_point(x, y)
        if hit is None:
            return
        criteria, secure, pid = hit
        if pid == self.own_pid:
            return                          # never capture our own UI
        now = time.monotonic()
        lt, lx, ly = self._last_click
        if not dbl and now - lt < 0.4 and abs(x - lx) < 6 and abs(y - ly) < 6:
            dbl = True
        self._last_click = (now, x, y)
        self._emit({"kind": "click", "process": criteria.get("process", ""),
                    "title": criteria.get("title", ""), "double": dbl,
                    "secure": secure, "criteria": criteria})

    # -- keyboard -----------------------------------------------------------
    def _on_key(self, ncode, wparam, lparam):
        try:
            if ncode >= 0 and wparam in (WM_KEYDOWN, WM_SYSKEYDOWN):
                kb = ctypes.cast(lparam,
                                 ctypes.POINTER(_KBDLLHOOKSTRUCT)).contents
                if not (kb.flags & LLKHF_INJECTED):
                    self._handle_key(kb.vkCode)
        except Exception:
            log.debug("key hook error", exc_info=True)
        return ctypes.windll.user32.CallNextHookEx(
            self._kbd_hook, ncode, wparam, lparam)

    def _handle_key(self, vk) -> None:
        if vk in _SPECIAL_KEYS:
            self._flush_text()
            name = _SPECIAL_KEYS[vk]
            # Enter/Tab end a field; emit them as explicit key presses
            self._emit({"kind": "key", "vk": name})
            return
        ch = self._vk_to_char(vk)
        if ch is None:
            return
        with self._text_lock:
            if self._text_target is None:
                tgt = uia.focused_target()
                if tgt is None:
                    return
                criteria, secure, pid = tgt
                if pid == self.own_pid:
                    return
                self._text_target = criteria
                self._text_secure = secure
            if not self._text_secure:
                self._buf.append(ch)

    def _vk_to_char(self, vk) -> str | None:
        try:
            user32 = ctypes.windll.user32
            state = (ctypes.c_ubyte * 256)()
            user32.GetKeyboardState(ctypes.byref(state))
            buf = (ctypes.c_wchar * 8)()
            layout = user32.GetKeyboardLayout(0)
            n = user32.ToUnicodeEx(vk, 0, ctypes.byref(state), buf, 8, 0,
                                   layout)
            if n > 0:
                c = buf[0]
                if c and c.isprintable():
                    return c
            return None
        except Exception:
            return None

    def _flush_text(self) -> None:
        with self._text_lock:
            target, secure = self._text_target, self._text_secure
            text = "".join(self._buf)
            self._buf = []
            self._text_target = None
            self._text_secure = False
        if target is None:
            return
        if not text and not secure:
            return
        self._emit({"kind": "text", "process": target.get("process", ""),
                    "secure": secure, "text": text, "criteria": target})

    # -- emit ---------------------------------------------------------------
    def _emit(self, event: dict) -> None:
        event.setdefault("ts", time.monotonic())
        try:
            self.callback(event)
        except Exception:
            log.debug("recorder callback error", exc_info=True)
