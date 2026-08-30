"""Workflow recorder — turn real desktop actions into an editable
workflow.

This is a CONVENIENCE layer, not a second engine. Captured desktop
events are converted into ordinary workflow step dicts (the exact
portable shape `ProfileManager` already imports), materialized into
normal named actions, and handed to the existing workflow builder for
review, editing, testing and saving. Nothing is ever executed or saved
automatically.

Two clean halves:

- Capture (`app/context/capture.py`) — thin Win32 hook layer, dormant
  until `start()`, emits raw event dicts. Injected here so the engine
  and the whole conversion path are testable without real hooks.
- Conversion (`build_steps`, pure) — filters noise, consolidates
  redundant events (click → focus → type ⇒ Find/Focus/Type), turns
  application transitions into SEMANTIC waits (never raw millisecond
  timing), captures typed text as an editable variable + `{name}`
  substitution, and records secure inputs as a placeholder the user
  must fill in. UI targets reuse the existing UI Automation reference
  model (process/window/control type/name/automation id) — never bare
  coordinates.

Raw event dicts (produced by capture, consumed by `build_steps`):

    {kind:"launch",     ts, process}
    {kind:"foreground", ts, process, title}
    {kind:"click",      ts, process, title, double, secure, criteria}
    {kind:"text",       ts, process, secure, text, criteria}
    {kind:"key",        ts, process, vk}
    {kind:"url",        ts, url}
"""
from __future__ import annotations

import logging
import re
import threading

from ..core.events import EventBus

log = logging.getLogger(__name__)

SECURE_PLACEHOLDER = "[SECURE INPUT]"
DEFAULT_WAIT_MS = 10_000

# browsers whose address bar turns a typed URL into an Open URL step
_BROWSERS = ("chrome.exe", "msedge.exe", "firefox.exe", "brave.exe",
             "opera.exe")
_ADDRESS_HINTS = ("address and search bar", "address bar", "search or "
                  "enter address", "location", "url")
_URL_RE = re.compile(
    r"^\s*(https?://|www\.)?"
    r"([a-z0-9-]+\.)+[a-z]{2,}(/\S*)?\s*$", re.I)


# -- pure helpers -------------------------------------------------------------
def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_")
    return s[:24]


def _unique(base: str, used: set[str], fallback: str) -> str:
    name = base or fallback
    if not re.match(r"^[a-z_]", name):
        name = fallback
    candidate, i = name, 2
    while candidate in used:
        candidate = f"{name}_{i}"
        i += 1
    used.add(candidate)
    return candidate


def _looks_like_url(text: str) -> bool:
    return bool(_URL_RE.match(text or ""))


def _normalize_url(text: str) -> str:
    t = (text or "").strip()
    if not re.match(r"^https?://", t, re.I):
        t = "https://" + t.lstrip("/")
    return t


def _is_addressbar(criteria: dict) -> bool:
    proc = (criteria.get("process") or "").lower()
    if proc not in _BROWSERS:
        return False
    name = (criteria.get("name") or "").lower()
    aid = (criteria.get("automation_id") or "").lower()
    return (any(h in name for h in _ADDRESS_HINTS)
            or aid in ("urlbar-input", "view_1022"))


def _title_pattern(title: str) -> str:
    """A distinctive window-title wildcard from a real title, e.g.
    'YouTube - Google Chrome' → '*YouTube*'. Empty → '' (caller falls
    back to window_exists)."""
    t = (title or "").strip()
    if not t:
        return ""
    head = t.split(" - ")[0].strip()
    head = head or t
    return f"*{head}*"


def _same_target(a: dict, b: dict) -> bool:
    a, b = a or {}, b or {}
    keys = ("process", "control_type", "name", "automation_id")
    return all((a.get(k) or "") == (b.get(k) or "") for k in keys)


def _find_criteria(criteria: dict) -> dict:
    """Only the fields needed to rediscover the element (never coords)."""
    return {"process": criteria.get("process", ""),
            "title": criteria.get("title", ""),
            "control_type": criteria.get("control_type", ""),
            "name": criteria.get("name", ""),
            "automation_id": criteria.get("automation_id", "")}


def _findable(criteria: dict) -> bool:
    c = criteria or {}
    ct = (c.get("control_type") or "").strip()
    return bool((c.get("name") or "").strip()
                or (c.get("automation_id") or "").strip()
                or (ct and ct != "Any"))


# -- step emitters ------------------------------------------------------------
def _action(atype: str, name: str, params: dict) -> dict:
    # portable action-step shape (== export format) → materialized later
    return {"type": "action",
            "action": {"name": name, "type": atype, "params": params}}


def _launch_steps(process: str, resolve, launched: set) -> list[dict]:
    if process in launched:
        return []
    launched.add(process)
    out = []
    hit = resolve(process)
    if hit:
        friendly, path = hit
        out.append(_action("launch_app", f"Launch {friendly}",
                           {"path": path, "args": "", "if_running": "focus"}))
    out.append({"type": "wait", "condition": "app_running",
                "process": process, "title": "",
                "timeout_ms": DEFAULT_WAIT_MS})
    return out


def _transition_wait(process: str, title: str) -> dict:
    pat = _title_pattern(title)
    if pat:
        return {"type": "wait", "condition": "window_title",
                "process": "", "title": pat, "timeout_ms": DEFAULT_WAIT_MS}
    return {"type": "wait", "condition": "window_exists",
            "process": process, "title": "", "timeout_ms": DEFAULT_WAIT_MS}


def _wait_sig(step: dict) -> tuple:
    return (step.get("condition"), step.get("process"), step.get("title"))


# -- excluded processes -------------------------------------------------------
def _keep(e: dict, exclude: tuple) -> bool:
    if not isinstance(e, dict) or "kind" not in e:
        return False
    proc = (e.get("process") or "").lower()
    if proc and proc in exclude:
        return False
    crit = e.get("criteria")
    if crit and (crit.get("process") or "").lower() in exclude:
        return False
    return True


def build_steps(events: list[dict], resolve=None,
                exclude_processes: tuple = ()) -> list[dict]:
    """Pure converter: raw capture events → editable workflow steps
    (portable action shape). No I/O, no execution. Deterministic."""
    resolve = resolve or _discovery_resolver()
    exclude = tuple(p.lower() for p in exclude_processes)
    ev = [e for e in events if _keep(e, exclude)]

    steps: list[dict] = []
    launched: set[str] = set()
    last_wait: tuple | None = None
    store_names: set[str] = set()
    var_names: set[str] = set()

    def emit(step):
        nonlocal last_wait
        steps.append(step)
        last_wait = _wait_sig(step) if step.get("type") == "wait" else None

    def emit_wait(step):
        nonlocal last_wait
        if _wait_sig(step) == last_wait:
            return                      # never duplicate an identical wait
        emit(step)

    def store_for(criteria):
        base = _slug(criteria.get("name", "")) or _slug(
            criteria.get("control_type", ""))
        suffix = "_box" if base else ""
        return _unique(f"{base}{suffix}", store_names, "element")

    def var_for(criteria):
        base = _slug(criteria.get("name", ""))
        return _unique(f"{base}_text" if base else "", var_names, "text")

    def find_focus(criteria):
        store = store_for(criteria)
        emit({"type": "ui_find", **_find_criteria(criteria),
              "store": store, "timeout_ms": DEFAULT_WAIT_MS})
        emit({"type": "ui_focus", "ref": store})
        return store

    n = len(ev)
    i = 0
    while i < n:
        e = ev[i]
        k = e.get("kind")
        if k == "launch":
            for s in _launch_steps(e.get("process", ""), resolve, launched):
                emit_wait(s) if s.get("type") == "wait" else emit(s)
        elif k == "foreground":
            proc, title = e.get("process", ""), e.get("title", "")
            if proc and proc not in launched and resolve(proc):
                for s in _launch_steps(proc, resolve, launched):
                    emit_wait(s) if s.get("type") == "wait" else emit(s)
            elif proc:
                emit_wait(_transition_wait(proc, title))
        elif k == "url":
            emit(_action("open_url", "Open URL",
                        {"url": _normalize_url(e.get("url", ""))}))
        elif k == "click":
            crit = e.get("criteria") or {}
            if not _findable(crit):
                log.debug("recorder: dropping click with no stable UI ref")
                i += 1
                continue
            nxt = ev[i + 1] if i + 1 < n else None
            if nxt and nxt.get("kind") == "text" \
                    and _same_target(crit, nxt.get("criteria")):
                pass                    # fold: the text step does find+focus
            else:
                store = store_for(crit)
                emit({"type": "ui_find", **_find_criteria(crit),
                      "store": store, "timeout_ms": DEFAULT_WAIT_MS})
                emit({"type": "ui_click", "ref": store})
        elif k == "text":
            crit = e.get("criteria") or {}
            secure = bool(e.get("secure"))
            text = e.get("text", "")
            if _is_addressbar(crit) and not secure and _looks_like_url(text):
                emit(_action("open_url", "Open URL",
                            {"url": _normalize_url(text)}))
            elif _findable(crit):
                store = find_focus(crit)
                if secure:
                    var = var_for(crit)
                    emit({"type": "set_var", "name": var,
                          "source": "literal", "value_type": "text",
                          "value": SECURE_PLACEHOLDER, "secure": True})
                    emit({"type": "ui_type", "ref": store,
                          "text": "{" + var + "}"})
                else:
                    var = var_for(crit)
                    emit({"type": "set_var", "name": var,
                          "source": "literal", "value_type": "text",
                          "value": text})
                    emit({"type": "ui_type", "ref": store,
                          "text": "{" + var + "}"})
        elif k == "key":
            vk = e.get("vk", "")
            if vk:
                emit(_action("key_press", f"Press {vk.upper()}",
                            {"key": vk}))
        i += 1
    return steps


def _discovery_resolver():
    """process name → (friendly, path) using the existing discovery
    (no hard-coded paths). Built once per conversion."""
    table = {}
    try:
        import os
        from ..actions.discovery import discover_applications
        for app in discover_applications():
            table[os.path.basename(app.path).lower()] = (app.name, app.path)
    except Exception:
        log.debug("app discovery unavailable for recorder", exc_info=True)

    def resolve(process: str):
        return table.get((process or "").lower())
    return resolve


class WorkflowRecorder:
    """Orchestrates a recording session on top of an injected capture
    backend. Dormant until `start()`; one session at a time; cancelled
    instantly by Emergency Stop / motion-off / shutdown."""

    def __init__(self, bus: EventBus, capture_factory=None) -> None:
        self.bus = bus
        self._capture_factory = capture_factory or _default_capture_factory
        self._capture = None
        self._events: list[dict] = []
        self._lock = threading.Lock()
        self._recording = False
        self._paused = False
        # cancelled by the same signal that cancels workflows
        bus.subscribe("control.enabled", self._on_control)

    @property
    def recording(self) -> bool:
        return self._recording

    @property
    def paused(self) -> bool:
        return self._paused

    def start(self) -> tuple[bool, str]:
        with self._lock:
            if self._recording:
                return False, "a recording is already in progress"
            self._events = []
            self._paused = False
            self._recording = True
        try:
            self._capture = self._capture_factory(self._on_event)
            self._capture.start()
        except Exception as e:  # noqa: BLE001
            with self._lock:
                self._recording = False
            self._capture = None
            log.exception("recorder capture failed to start")
            return False, f"could not start capture: {e}"
        self.bus.publish("recorder.state", "recording", 0)
        log.info("Workflow recording started")
        return True, ""

    def pause(self) -> None:
        if self._recording:
            self._paused = True
            self.bus.publish("recorder.state", "paused", len(self._events))

    def resume(self) -> None:
        if self._recording:
            self._paused = False
            self.bus.publish("recorder.state", "recording",
                             len(self._events))

    def stop(self) -> list[dict]:
        """Stop capture and return the raw events (conversion is a
        separate, pure step so callers can inspect either)."""
        self._teardown()
        self.bus.publish("recorder.state", "stopped", len(self._events))
        log.info("Workflow recording stopped (%d raw events)",
                 len(self._events))
        return list(self._events)

    def cancel(self) -> None:
        self._teardown()
        self._events = []
        self.bus.publish("recorder.state", "cancelled", 0)
        log.info("Workflow recording cancelled")

    def build(self, resolve=None,
              exclude_processes: tuple = ()) -> list[dict]:
        return build_steps(self._events, resolve, exclude_processes)

    # -- internals ----------------------------------------------------------
    def _on_event(self, event: dict) -> None:
        if not self._recording or self._paused:
            return
        with self._lock:
            self._events.append(event)
        label = event.get("kind", "?")
        self.bus.publish("recorder.event", label, len(self._events))

    def _on_control(self, enabled: bool) -> None:
        if not enabled and self._recording:
            self.cancel()

    def _teardown(self) -> None:
        with self._lock:
            self._recording = False
            self._paused = False
        cap = self._capture
        self._capture = None
        if cap is not None:
            try:
                cap.stop()
            except Exception:
                log.debug("recorder capture stop failed", exc_info=True)


def _default_capture_factory(callback):
    from ..context.capture import DesktopCapture
    return DesktopCapture(callback)
