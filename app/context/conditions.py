"""Desktop condition checks for smart workflow waits.

Win32 stays inside app/context/ — the workflow engine consumes only the
generic `check(cond) / validate(cond) / describe(cond)` API, so new
condition kinds (and future non-Windows backends) plug in here without
touching the engine.

Condition dict shape (stored inside workflow steps):

    {condition: "app_running" | "process_exists" | "window_exists"
                | "window_title",
     process: "chrome.exe",          # target process name where relevant
     title:   "*YouTube*"}           # window-title pattern where relevant

Checks are snapshot-based (Toolhelp process list / EnumWindows), safe to
poll at a modest interval, and never raise — a failing probe reads as
"condition not (yet) true".
"""
from __future__ import annotations

import ctypes
import logging

from ..rules.engine import _title_matches as title_matches

log = logging.getLogger(__name__)

CONDITION_KINDS = ("app_running", "process_exists", "window_exists",
                   "window_title", "ui_element", "variable")

_KIND_LABELS = {
    "app_running": "application",
    "process_exists": "process",
    "window_exists": "window of",
    "window_title": "window title",
    "ui_element": "UI element",
    "variable": "variable",
}

# workflow-variable comparisons — a fixed operator set, never expressions
TEXT_OPS = ("equals", "not_equals", "contains", "starts_with",
            "ends_with", "is_empty", "is_not_empty")
NUMBER_OPS = ("eq", "ne", "gt", "lt", "ge", "le")
BOOL_OPS = ("is_true", "is_false")
VARIABLE_OPS = TEXT_OPS + NUMBER_OPS + BOOL_OPS

VAR_NAME_RE = __import__("re").compile(r"^[a-z_][a-z0-9_]*$")
_SUBST_RE = __import__("re").compile(r"\{([a-z_][a-z0-9_]*)\}")


def substitute(text: str, variables: dict) -> str:
    """Replace {name} placeholders with variable values. Data-only —
    the result is always a plain string; undefined names raise."""
    def repl(m):
        name = m.group(1)
        if name not in variables:
            raise ValueError(
                f"Variable {name!r} has not been defined.")
        v = variables[name]
        if isinstance(v, bool):
            return "true" if v else "false"
        if isinstance(v, float) and v.is_integer():
            return str(int(v))
        return str(v)
    return _SUBST_RE.sub(repl, text)


def substitution_names(text: str) -> list[str]:
    return _SUBST_RE.findall(text or "")


def check_variable(cond: dict, variables: dict) -> bool:
    """Evaluate one variable comparison. Raises ValueError on an
    undefined variable or a type/operator mismatch (a FAILURE, distinct
    from FALSE)."""
    name = cond.get("var", "")
    if name not in variables:
        raise ValueError(f"Variable {name!r} has not been defined.")
    v = variables[name]
    op = cond.get("op", "")
    ref = cond.get("value", "")
    if op in BOOL_OPS:
        if not isinstance(v, bool):
            raise ValueError(f"variable {name!r} is not a yes/no value")
        return v if op == "is_true" else not v
    if op in NUMBER_OPS:
        try:
            a = float(v) if not isinstance(v, bool) else None
            b = float(ref)
        except (TypeError, ValueError):
            a = None
            b = 0.0
        if a is None:
            raise ValueError(
                f"variable {name!r} is not comparable as a number")
        return {"eq": a == b, "ne": a != b, "gt": a > b, "lt": a < b,
                "ge": a >= b, "le": a <= b}[op]
    if op in TEXT_OPS:
        s = ("true" if v else "false") if isinstance(v, bool) else str(v)
        r = str(ref)
        return {"equals": s == r, "not_equals": s != r,
                "contains": r in s, "starts_with": s.startswith(r),
                "ends_with": s.endswith(r), "is_empty": s == "",
                "is_not_empty": s != ""}[op]
    raise ValueError(f"unknown comparison {op!r}")


def _normalize_process(name: str) -> str:
    """'Chrome', 'chrome.exe', a full path → 'chrome.exe' (lowercase)."""
    import os
    n = os.path.basename(name.strip().strip('"')).lower()
    if n and not n.endswith(".exe"):
        n += ".exe"
    return n


# -- probes ------------------------------------------------------------------
def _iter_process_names():
    """Yield running process image names (lowercase) via Toolhelp — direct
    Win32 snapshot, no shell."""
    TH32CS_SNAPPROCESS = 0x2
    INVALID_HANDLE = ctypes.c_void_p(-1).value

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", ctypes.c_ulong),
            ("cntUsage", ctypes.c_ulong),
            ("th32ProcessID", ctypes.c_ulong),
            ("th32DefaultHeapID", ctypes.c_void_p),
            ("th32ModuleID", ctypes.c_ulong),
            ("cntThreads", ctypes.c_ulong),
            ("th32ParentProcessID", ctypes.c_ulong),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", ctypes.c_ulong),
            ("szExeFile", ctypes.c_wchar * 260),
        ]

    k32 = ctypes.windll.kernel32
    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snap == INVALID_HANDLE or not snap:
        return
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        if not k32.Process32FirstW(snap, ctypes.byref(entry)):
            return
        while True:
            yield entry.szExeFile.lower()
            if not k32.Process32NextW(snap, ctypes.byref(entry)):
                return
    finally:
        k32.CloseHandle(snap)


def process_exists(name: str) -> bool:
    target = _normalize_process(name)
    if not target:
        return False
    try:
        return any(p == target for p in _iter_process_names())
    except Exception:
        log.debug("process probe failed", exc_info=True)
        return False


def window_exists(process: str = "", title_pattern: str = "") -> bool:
    """True when a visible, titled top-level window matches the given
    process name and/or title pattern (both filters optional but at
    least one must be given by validation)."""
    import win32gui

    from .detector import _process_name

    target = _normalize_process(process) if process else ""
    found: list[int] = []

    def cb(hwnd, _):
        if found:
            return
        if not win32gui.IsWindowVisible(hwnd):
            return
        title = win32gui.GetWindowText(hwnd)
        if not title:
            return
        if title_pattern and not title_matches(title_pattern, title):
            return
        if target and _process_name(hwnd) != target:
            return
        found.append(hwnd)

    try:
        win32gui.EnumWindows(cb, None)
    except Exception:
        # EnumWindows raises if the callback ever returned falsy on some
        # pywin32 versions; treat a failed sweep as "not found yet"
        pass
    return bool(found)


# -- generic API -------------------------------------------------------------
def validate(cond: dict) -> str | None:
    kind = cond.get("condition", "")
    if kind not in CONDITION_KINDS:
        return f"unknown condition: {kind!r}"
    process = (cond.get("process") or "").strip()
    title = (cond.get("title") or "").strip()
    if kind in ("app_running", "process_exists") and not process:
        return "condition needs a process name"
    if kind == "window_title" and not title:
        return "condition needs a window-title pattern"
    if kind == "window_exists" and not (process or title):
        return "window condition needs a process or a title pattern"
    if kind == "ui_element":
        ct = (cond.get("control_type") or "").strip()
        if not ((cond.get("name") or "").strip()
                or (cond.get("automation_id") or "").strip()
                or (ct and ct != "Any")):
            return ("UI element condition needs a name, automation id "
                    "or control type")
    if kind == "variable":
        name = (cond.get("var") or "").strip()
        if not VAR_NAME_RE.match(name):
            return f"invalid variable name {name!r}"
        op = cond.get("op", "")
        if op not in VARIABLE_OPS:
            return f"unknown comparison {op!r}"
        if op in NUMBER_OPS:
            try:
                float(cond.get("value", ""))
            except (TypeError, ValueError):
                return "number comparison needs a numeric value"
    return None


def check(cond: dict, variables: dict | None = None) -> bool:
    """Evaluate a condition snapshot. Never raises — except for
    `variable` conditions, whose undefined-variable / type errors are
    genuine workflow failures and must propagate."""
    kind = cond.get("condition", "")
    if kind == "variable":
        return check_variable(cond, variables or {})
    try:
        process = (cond.get("process") or "").strip()
        title = (cond.get("title") or "").strip()
        if kind in ("app_running", "process_exists"):
            return process_exists(process)
        if kind in ("window_exists", "window_title"):
            return window_exists(process, title)
        if kind == "ui_element":
            from . import uia
            return uia.element_exists({
                "process": process, "title": title,
                "control_type": cond.get("control_type", ""),
                "name": cond.get("name", ""),
                "automation_id": cond.get("automation_id", ""),
                "require_enabled": bool(cond.get("require_enabled")),
                "require_visible": bool(cond.get("require_visible"))})
        return False
    except Exception:
        log.debug("condition check failed", exc_info=True)
        return False


def describe(cond: dict) -> str:
    kind = cond.get("condition", "")
    target = (cond.get("process") or "").strip()
    title = (cond.get("title") or "").strip()
    what = _KIND_LABELS.get(kind, kind)
    parts = [p for p in (_normalize_process(target) if target else "",
                         title) if p]
    if kind == "ui_element":
        for k in ("control_type", "name", "automation_id"):
            v = (cond.get(k) or "").strip()
            if v and v != "Any":
                parts.append(v)
    if kind == "variable":
        pretty = {"equals": "=", "not_equals": "≠", "contains": "contains",
                  "starts_with": "starts with", "ends_with": "ends with",
                  "is_empty": "is empty", "is_not_empty": "is not empty",
                  "eq": "=", "ne": "≠", "gt": ">", "lt": "<", "ge": "≥",
                  "le": "≤", "is_true": "is true", "is_false": "is false"}
        op = cond.get("op", "")
        parts = [f"{{{cond.get('var', '')}}}", pretty.get(op, op)]
        if op not in ("is_empty", "is_not_empty", "is_true", "is_false"):
            parts.append(f"'{cond.get('value', '')}'")
    return f"{what} {' '.join(parts)}".strip()
