"""Windows UI Automation access for UI-aware workflows.

Win32/COM stays inside this module — the workflow engine and UI consume
only the normalized `UIElementInfo` model and the functions below. No
OCR, no vision, no screen scraping: everything goes through the
accessibility (UIA) tree that applications expose voluntarily.

Design rules (see spec):

- Nothing here runs continuously. Every function is a one-shot snapshot
  invoked only while a workflow step, condition poll, safe test or the
  UI Inspector explicitly asks. The camera/gesture pipeline never
  touches this module.
- Element identity is structural (process, window, control type, name,
  automation id) — never bare screen coordinates.
- Applications without a useful accessibility tree simply yield "not
  found"; callers report that honestly.
- COM is initialized per call (`_uia()` context) so calls are safe from
  any worker thread and never block shutdown.
"""
from __future__ import annotations

import contextlib
import ctypes
import logging
import os
from dataclasses import dataclass, field

from ..rules.engine import _title_matches as title_matches

log = logging.getLogger(__name__)

# node/depth budgets keep searches bounded on huge trees (browsers)
SEARCH_MAX_NODES = 3000
SEARCH_MAX_DEPTH = 30

CONTROL_TYPES = [
    "Any", "Button", "CheckBox", "ComboBox", "Document", "Edit",
    "Hyperlink", "Image", "List", "ListItem", "MenuItem", "Pane",
    "RadioButton", "Slider", "TabItem", "Text", "ToolBar", "TreeItem",
    "Window",
]


@dataclass
class UIElementInfo:
    """Normalized accessible-element snapshot. Fields an application
    does not expose stay at their defaults."""
    name: str = ""
    control_type: str = ""       # "Edit", "Button", ... (no "Control" suffix)
    automation_id: str = ""
    class_name: str = ""
    process: str = ""            # e.g. "chrome.exe" (lowercase)
    window_title: str = ""
    enabled: bool = True
    visible: bool = True
    rect: tuple[int, int, int, int] = (0, 0, 0, 0)  # l, t, r, b


def matches(info: UIElementInfo, *, control_type: str = "",
            name: str = "", automation_id: str = "",
            require_enabled: bool = False,
            require_visible: bool = False) -> bool:
    """Pure matching logic (unit-testable without COM).

    - control_type: exact, "" or "Any" match everything
    - name: case-insensitive; wildcards/substring via the same matcher
      window rules use; exact names match exactly first
    - automation_id: exact (case-sensitive — IDs are identifiers)
    """
    if control_type and control_type != "Any" \
            and info.control_type != control_type:
        return False
    if automation_id and info.automation_id != automation_id:
        return False
    if name:
        if info.name.lower() != name.lower() \
                and not title_matches(name, info.name):
            return False
        if not info.name:
            return False
    if require_enabled and not info.enabled:
        return False
    if require_visible and not info.visible:
        return False
    return True


# -- COM plumbing ------------------------------------------------------------
@contextlib.contextmanager
def _uia():
    """Per-call COM/UIA context, safe from any thread."""
    import uiautomation as auto
    with auto.UIAutomationInitializerInThread(debug=False):
        yield auto


def _process_name_of_pid(pid: int) -> str:
    try:
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
                return os.path.basename(buf.value).lower()
            return ""
        finally:
            ctypes.windll.kernel32.CloseHandle(h)
    except Exception:
        return ""


def _info_of(control, window_title: str = "",
             process: str = "") -> UIElementInfo:
    r = (0, 0, 0, 0)
    try:
        br = control.BoundingRectangle
        r = (br.left, br.top, br.right, br.bottom)
    except Exception:
        pass
    ct = control.ControlTypeName or ""
    if ct.endswith("Control"):
        ct = ct[:-len("Control")]
    return UIElementInfo(
        name=control.Name or "",
        control_type=ct,
        automation_id=control.AutomationId or "",
        class_name=control.ClassName or "",
        process=process or _process_name_of_pid(control.ProcessId),
        window_title=window_title,
        enabled=bool(control.IsEnabled),
        visible=not bool(control.IsOffscreen),
        rect=r,
    )


def _iter_windows(auto, process: str = "", title: str = ""):
    """Top-level windows filtered by process name and/or title pattern."""
    target = process.strip().lower()
    if target and not target.endswith(".exe"):
        target += ".exe"
    for w in auto.GetRootControl().GetChildren():
        wt = w.Name or ""
        if title and not title_matches(title, wt):
            continue
        proc = _process_name_of_pid(w.ProcessId)
        if target and proc != target:
            continue
        yield w, wt, proc


def _search(root, criteria: dict, window_title: str, process: str):
    """Bounded BFS over the subtree; returns (control, info) or None."""
    from collections import deque
    q = deque([(root, 0)])
    seen = 0
    while q:
        node, depth = q.popleft()
        seen += 1
        if seen > SEARCH_MAX_NODES:
            return None
        try:
            children = node.GetChildren()
        except Exception:
            continue
        for ch in children:
            try:
                info = _info_of(ch, window_title, process)
            except Exception:
                continue
            if matches(info,
                       control_type=criteria.get("control_type", ""),
                       name=criteria.get("name", ""),
                       automation_id=criteria.get("automation_id", ""),
                       require_enabled=criteria.get("require_enabled",
                                                    False),
                       require_visible=criteria.get("require_visible",
                                                    False)):
                return ch, info
            if depth + 1 <= SEARCH_MAX_DEPTH:
                q.append((ch, depth + 1))
    return None


# -- public API ---------------------------------------------------------------
def find_element(criteria: dict):
    """One snapshot search. criteria: {process, title, control_type,
    name, automation_id, require_enabled, require_visible}. Returns
    (control, UIElementInfo) or None. Never raises."""
    try:
        with _uia() as auto:
            for w, wt, proc in _iter_windows(
                    auto, criteria.get("process", ""),
                    criteria.get("title", "")):
                hit = _search(w, criteria, wt, proc)
                if hit:
                    return hit
        return None
    except Exception:
        log.debug("UIA search failed", exc_info=True)
        return None


def element_exists(criteria: dict) -> bool:
    return find_element(criteria) is not None


def refresh_info(control, criteria: dict) -> UIElementInfo | None:
    """Re-validate a previously found element: it must still exist and
    still match its criteria (identity check — never act on a stale or
    swapped element). Returns fresh info or None."""
    try:
        with _uia():
            info = _info_of(control)
            if not matches(info,
                           control_type=criteria.get("control_type", ""),
                           name=criteria.get("name", ""),
                           automation_id=criteria.get("automation_id", "")):
                return None
            proc = criteria.get("process", "").strip().lower()
            if proc and not proc.endswith(".exe"):
                proc += ".exe"
            if proc and info.process != proc:
                return None
            return info
    except Exception:
        return None


def invoke(control) -> None:
    """Click via the element's native accessibility pattern. Raises
    ValueError when the element supports no invoke-style pattern —
    never falls back to blind coordinates."""
    with _uia():
        for getter in ("GetInvokePattern", "GetTogglePattern",
                       "GetSelectionItemPattern",
                       "GetLegacyIAccessiblePattern"):
            try:
                p = getattr(control, getter)()
            except Exception:
                continue
            if not p:
                continue
            try:
                if getter == "GetInvokePattern":
                    p.Invoke()
                elif getter == "GetTogglePattern":
                    p.Toggle()
                elif getter == "GetSelectionItemPattern":
                    p.Select()
                else:
                    p.DoDefaultAction()
                return
            except Exception:
                continue
        raise ValueError("element exposes no clickable pattern")


def focus(control) -> None:
    with _uia():
        try:
            control.SetFocus()
        except Exception as e:
            raise ValueError(f"could not focus element: {e}") from e


def set_text(control, text: str) -> bool:
    """Set text via the UIA Value pattern. Returns False when the
    element does not support it (caller may use the explicit focused-
    keystroke fallback)."""
    with _uia():
        try:
            p = control.GetValuePattern()
        except Exception:
            return False
        if not p:
            return False
        try:
            p.SetValue(text)
            return True
        except Exception:
            return False


def get_text(control) -> str | None:
    """Read an element's accessible text: Value pattern → Text pattern →
    Name. Returns None when nothing is exposed. Read-only."""
    with _uia():
        try:
            p = control.GetValuePattern()
            if p:
                v = p.Value
                if v:
                    return str(v)
        except Exception:
            pass
        try:
            tp = control.GetTextPattern()
            if tp:
                v = tp.DocumentRange.GetText(-1)
                if v:
                    return str(v)
        except Exception:
            pass
        try:
            return control.Name or None
        except Exception:
            return None


def element_from_point(x: int, y: int) -> UIElementInfo | None:
    """Inspector helper: accessible element under a screen point."""
    try:
        with _uia() as auto:
            c = auto.ControlFromPoint(x, y)
            if c is None:
                return None
            top = c.GetTopLevelControl()
            return _info_of(c, top.Name if top else "")
    except Exception:
        return None


def _target_of(control, auto) -> tuple[dict, bool, int]:
    top = None
    try:
        top = control.GetTopLevelControl()
    except Exception:
        pass
    info = _info_of(control, top.Name if top else "")
    secure = False
    try:
        secure = bool(control.IsPassword)
    except Exception:
        pass
    pid = 0
    try:
        pid = int(control.ProcessId)
    except Exception:
        pass
    criteria = {"process": info.process, "title": info.window_title,
                "control_type": info.control_type, "name": info.name,
                "automation_id": info.automation_id,
                "class_name": info.class_name}
    return criteria, secure, pid


def target_at_point(x: int, y: int):
    """Recorder helper: accessible criteria under a screen point, plus
    whether it is a secure (password) input and its owning pid. Returns
    (criteria, secure, pid) or None. Never raises."""
    try:
        with _uia() as auto:
            c = auto.ControlFromPoint(x, y)
            if c is None:
                return None
            return _target_of(c, auto)
    except Exception:
        return None


def focused_target():
    """Recorder helper: accessible criteria of the currently focused
    element (typing target) + secure flag + pid, or None."""
    try:
        with _uia() as auto:
            c = auto.GetFocusedControl()
            if c is None:
                return None
            return _target_of(c, auto)
    except Exception:
        return None


def describe_criteria(c: dict) -> str:
    parts = []
    if c.get("control_type") and c["control_type"] != "Any":
        parts.append(c["control_type"])
    if c.get("name"):
        parts.append(f"'{c['name']}'")
    if c.get("automation_id"):
        parts.append(f"#{c['automation_id']}")
    where = []
    if c.get("process"):
        where.append(c["process"])
    if c.get("title"):
        where.append(c["title"])
    s = " ".join(parts) or "element"
    return s + (f" in {' '.join(where)}" if where else "")
