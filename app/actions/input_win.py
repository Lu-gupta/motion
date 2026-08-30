"""Low-level Windows input via SendInput (ctypes).

Only this module touches raw Win32 input structures.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import logging

log = logging.getLogger(__name__)

user32 = ctypes.windll.user32

# input types
INPUT_MOUSE = 0
INPUT_KEYBOARD = 1

# mouse flags
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_HWHEEL = 0x1000

# key flags
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004

VK = {
    "backspace": 0x08, "tab": 0x09, "enter": 0x0D, "shift": 0x10,
    "ctrl": 0x11, "alt": 0x12, "pause": 0x13, "capslock": 0x14,
    "esc": 0x1B, "escape": 0x1B, "space": 0x20, "pageup": 0x21,
    "pagedown": 0x22, "end": 0x23, "home": 0x24, "left": 0x25, "up": 0x26,
    "right": 0x27, "down": 0x28, "printscreen": 0x2C, "insert": 0x2D,
    "delete": 0x2E, "win": 0x5B, "lwin": 0x5B, "rwin": 0x5C, "apps": 0x5D,
    "numpad0": 0x60, "numpad1": 0x61, "numpad2": 0x62, "numpad3": 0x63,
    "numpad4": 0x64, "numpad5": 0x65, "numpad6": 0x66, "numpad7": 0x67,
    "numpad8": 0x68, "numpad9": 0x69, "multiply": 0x6A, "add": 0x6B,
    "subtract": 0x6D, "decimal": 0x6E, "divide": 0x6F,
    "f1": 0x70, "f2": 0x71, "f3": 0x72, "f4": 0x73, "f5": 0x74, "f6": 0x75,
    "f7": 0x76, "f8": 0x77, "f9": 0x78, "f10": 0x79, "f11": 0x7A,
    "f12": 0x7B, "numlock": 0x90, "scrolllock": 0x91,
    "volumemute": 0xAD, "volumedown": 0xAE, "volumeup": 0xAF,
    "medianext": 0xB0, "mediaprev": 0xB1, "mediastop": 0xB2,
    "mediaplaypause": 0xB3,
    ";": 0xBA, "=": 0xBB, ",": 0xBC, "-": 0xBD, ".": 0xBE, "/": 0xBF,
    "`": 0xC0, "[": 0xDB, "\\": 0xDC, "]": 0xDD, "'": 0xDE,
}
for c in "abcdefghijklmnopqrstuvwxyz":
    VK[c] = ord(c.upper())
for c in "0123456789":
    VK[c] = ord(c)


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wt.LONG), ("dy", wt.LONG), ("mouseData", wt.DWORD),
                ("dwFlags", wt.DWORD), ("time", wt.DWORD),
                ("dwExtraInfo", ctypes.POINTER(wt.ULONG))]


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wt.WORD), ("wScan", wt.WORD), ("dwFlags", wt.DWORD),
                ("time", wt.DWORD),
                ("dwExtraInfo", ctypes.POINTER(wt.ULONG))]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", _MOUSEINPUT), ("ki", _KEYBDINPUT)]


class _INPUT(ctypes.Structure):
    _fields_ = [("type", wt.DWORD), ("union", _INPUTUNION)]


def _send(inputs: list[_INPUT]) -> None:
    arr = (_INPUT * len(inputs))(*inputs)
    sent = user32.SendInput(len(inputs), arr, ctypes.sizeof(_INPUT))
    if sent != len(inputs):
        log.warning("SendInput sent %d of %d events", sent, len(inputs))


def _mouse_input(flags: int, dx: int = 0, dy: int = 0,
                 data: int = 0) -> _INPUT:
    inp = _INPUT()
    inp.type = INPUT_MOUSE
    inp.union.mi = _MOUSEINPUT(dx, dy, data, flags, 0, None)
    return inp


def _key_input(vk: int, up: bool = False) -> _INPUT:
    inp = _INPUT()
    inp.type = INPUT_KEYBOARD
    flags = KEYEVENTF_KEYUP if up else 0
    inp.union.ki = _KEYBDINPUT(vk, 0, flags, 0, None)
    return inp


# -- public mouse API -------------------------------------------------------
def cursor_pos() -> tuple[int, int]:
    pt = wt.POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y


def move_to(x: int, y: int) -> None:
    """Absolute move in virtual-screen coordinates."""
    vx = user32.GetSystemMetrics(76)   # SM_XVIRTUALSCREEN
    vy = user32.GetSystemMetrics(77)
    vw = user32.GetSystemMetrics(78)   # SM_CXVIRTUALSCREEN
    vh = user32.GetSystemMetrics(79)
    ax = int((x - vx) * 65535 / max(1, vw - 1))
    ay = int((y - vy) * 65535 / max(1, vh - 1))
    _send([_mouse_input(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | 0x4000,
                        ax, ay)])  # 0x4000 = VIRTUALDESK


def move_rel(dx: int, dy: int) -> None:
    _send([_mouse_input(MOUSEEVENTF_MOVE, dx, dy)])


_BUTTONS = {
    "left": (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP),
    "right": (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP),
    "middle": (MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP),
}


def button_down(button: str = "left") -> None:
    _send([_mouse_input(_BUTTONS[button][0])])


def button_up(button: str = "left") -> None:
    _send([_mouse_input(_BUTTONS[button][1])])


def click(button: str = "left", count: int = 1) -> None:
    down, up = _BUTTONS[button]
    inputs = []
    for _ in range(count):
        inputs += [_mouse_input(down), _mouse_input(up)]
    _send(inputs)


def scroll(amount: int, horizontal: bool = False) -> None:
    """amount in notches; positive = up / right."""
    flag = MOUSEEVENTF_HWHEEL if horizontal else MOUSEEVENTF_WHEEL
    _send([_mouse_input(flag, data=ctypes.c_ulong(amount * 120).value)])


# -- public keyboard API ----------------------------------------------------
def parse_key(name: str) -> int:
    k = name.strip().lower()
    if k in VK:
        return VK[k]
    raise ValueError(f"unknown key: {name!r}")


def key_press(key: str) -> None:
    vk = parse_key(key)
    _send([_key_input(vk), _key_input(vk, up=True)])


def hotkey(*keys: str) -> None:
    vks = [parse_key(k) for k in keys]
    downs = [_key_input(v) for v in vks]
    ups = [_key_input(v, up=True) for v in reversed(vks)]
    _send(downs + ups)


def _unicode_input(ch: str, up: bool = False) -> _INPUT:
    inp = _INPUT()
    inp.type = INPUT_KEYBOARD
    flags = KEYEVENTF_UNICODE | (KEYEVENTF_KEYUP if up else 0)
    inp.union.ki = _KEYBDINPUT(0, ord(ch), flags, 0, None)
    return inp


def type_text(text: str) -> None:
    """Type a unicode string into the focused control (KEYEVENTF_UNICODE
    — layout-independent). Single implementation for all typing."""
    inputs: list[_INPUT] = []
    for ch in text:
        if ch == "\n":
            vk = VK["enter"]
            inputs += [_key_input(vk), _key_input(vk, up=True)]
        else:
            inputs += [_unicode_input(ch), _unicode_input(ch, up=True)]
    if inputs:
        _send(inputs)


def parse_shortcut(spec: str) -> list[str]:
    """'ctrl+shift+t' → ['ctrl','shift','t'] (validated)."""
    parts = [p.strip().lower() for p in spec.split("+") if p.strip()]
    if not parts:
        raise ValueError("empty shortcut")
    for p in parts:
        parse_key(p)
    return parts
