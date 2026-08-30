"""Workflow engine — runs multi-step workflows off the GUI/camera threads.

A workflow is an ordered list of steps stored in the `workflows` table:

    {type: "action", action_id: int}   → execute an existing named action
    {type: "delay",  ms: int}          → cancellable wait
    {type: "wait", condition, process, title, timeout_ms}
                                       → poll a desktop condition
                                         (app/context/conditions.py) until
                                         true or timeout; timeout = FAILED

UI-aware steps (Windows UI Automation, app/context/uia.py — used ONLY
while a workflow explicitly runs them; nothing scans the desktop in the
background):

    {type: "ui_find"|"ui_wait", process, title, control_type, name,
     automation_id, store, timeout_ms}
        → poll for an accessible element; stores a workflow-local
          reference under `store`; timeout = FAILED
    {type: "ui_click", ref}   → invoke via the element's native pattern
    {type: "ui_focus", ref}   → focus the element
    {type: "ui_type", ref, text}
        → set text via the UIA Value pattern, else focus + unicode
          keystrokes (explicit fallback)

References are re-validated before every interaction (element must
still exist, still match its criteria and process) — a stale or
swapped element fails the step; nothing is ever clicked blind.

Bounded control flow (never unbounded, never scripted):

    {type: "retry", attempts, delay_ms, steps, until?, on_fail, fallback?}
        → run the block; a failing step fails only the ATTEMPT, and the
          block reruns after a cancellable delay. `until` (optional
          condition list, ALL) must also become true for an attempt to
          count as success. Exhausting every attempt is RETRY
          EXHAUSTED; on_fail decides: fail the workflow (default),
          continue, or run the fallback steps then continue.
    {type: "repeat", count, steps}
        → run the block exactly `count` times (hard cap 100); a
          failing step fails the workflow as usual.
    {type: "repeat_until", conditions, mode, max_iterations, delay_ms,
     timeout_ms, steps}
        → check the condition group BEFORE each pass (already true →
          zero passes); otherwise run the block, wait `delay_ms`, and
          re-check. Both an iteration bound and a time limit are
          mandatory; hitting either without the condition turning true
          is an explicit TIMEOUT failure — never a silent continue.

Loop iterations share the run's variables and element refs (nothing
resets between iterations); separate runs stay fully isolated.

Execution model:

- `start(workflow_id)` spawns a daemon thread and returns immediately —
  the gesture pipeline, UI and Emergency Stop are never blocked.
- Delays use `threading.Event.wait`, so cancellation interrupts them
  instantly (no `time.sleep`).
- One instance per workflow: starting a workflow that is already running
  fails with "already running" instead of stacking instances.
- First failing step stops the workflow (state FAILED); later steps do
  not run.
- `control.enabled False` (motion off / Emergency Stop) cancels every
  running workflow immediately.

Bus events:

    workflow.progress (name, step_index 1-based, total, label, state)
    workflow.done     (name, status "completed"|"failed"|"cancelled", detail)
"""
from __future__ import annotations

import logging
import threading
import time

from ..context import conditions
from ..core.events import EventBus
from ..core.types import ActionSpec
from ..data.repository import ActionRepo, WorkflowRepo, WorkflowRow

log = logging.getLogger(__name__)


class WorkflowEngine:
    # condition polling cadence — modest, event-driven otherwise; a check
    # is two Win32 snapshot calls, so CPU cost at 4 Hz is negligible
    POLL_S = 0.25
    def __init__(self, bus: EventBus, executor, actions: ActionRepo,
                 workflows: WorkflowRepo) -> None:
        self.bus = bus
        self.executor = executor
        self.actions = actions
        self.repo = workflows
        self._lock = threading.Lock()
        self._running: dict[int, threading.Event] = {}  # wid → cancel event
        bus.subscribe("control.enabled", self._on_control)

    # -- public --------------------------------------------------------------
    def start(self, workflow_id: int) -> tuple[bool, str]:
        """Start a workflow. Returns (started, reason)."""
        wf = self.repo.get(int(workflow_id))
        if wf is None:
            return False, f"workflow {workflow_id} does not exist"
        if not wf.enabled:
            return False, f"workflow {wf.name!r} is disabled"
        with self._lock:
            if wf.id in self._running:
                return False, f"workflow {wf.name!r} is already running"
            cancel = threading.Event()
            self._running[wf.id] = cancel
        t = threading.Thread(target=self._run, args=(wf, cancel),
                             name=f"workflow-{wf.id}", daemon=True)
        t.start()
        return True, ""

    def cancel_all(self, reason: str = "cancelled") -> None:
        with self._lock:
            events = list(self._running.values())
        for e in events:
            e.set()
        if events:
            log.info("Cancelled %d workflow(s): %s", len(events), reason)

    def running_ids(self) -> list[int]:
        with self._lock:
            return list(self._running)

    # -- internals -----------------------------------------------------------
    def _on_control(self, enabled: bool) -> None:
        if not enabled:
            self.cancel_all("motion control disabled")

    def _step_label(self, step: dict) -> str:
        t = step.get("type")
        if t == "delay":
            return f"Wait {int(step.get('ms', 0))} ms"
        if t == "wait":
            tmo = int(step.get("timeout_ms", 0)) / 1000
            return (f"Waiting for {conditions.describe(step)} "
                    f"(up to {tmo:g} s)")
        if t in ("ui_find", "ui_wait"):
            from ..context.uia import describe_criteria
            verb = "Find" if t == "ui_find" else "Wait for"
            return f"{verb} UI {describe_criteria(step)}"
        if t == "ui_click":
            return f"Click UI element '{step.get('ref', '')}'"
        if t == "ui_focus":
            return f"Focus UI element '{step.get('ref', '')}'"
        if t == "ui_type":
            return f"Type into UI element '{step.get('ref', '')}'"
        if t == "if":
            desc = " / ".join(conditions.describe(c)
                              for c in step.get("conditions", []))
            return f"IF {step.get('mode', 'all').upper()}: {desc}"
        if t == "set_var":
            src = step.get("source", "literal")
            pretty = {"literal": "value", "variable": "another variable",
                      "clipboard": "clipboard",
                      "active_app": "active application",
                      "active_window": "active window title"}
            return (f"Set {{{step.get('name', '')}}} from "
                    f"{pretty.get(src, src)}")
        if t == "ui_read":
            return (f"Read UI element '{step.get('ref', '')}' → "
                    f"{{{step.get('store_var', '')}}}")
        if t == "set_clipboard":
            return "Set clipboard from workflow"
        if t == "retry":
            return (f"RETRY up to {int(step.get('attempts', 1))} attempts "
                    f"({len(step.get('steps', []))} steps)")
        if t == "repeat":
            return (f"REPEAT {int(step.get('count', 1))} times "
                    f"({len(step.get('steps', []))} steps)")
        if t == "repeat_until":
            desc = " / ".join(conditions.describe(c)
                              for c in step.get("conditions", []))
            return f"REPEAT UNTIL {step.get('mode', 'all').upper()}: {desc}"
        a = self.actions.get(step.get("action_id", 0))
        return a.name if a else "(missing action)"

    def _run_ui_find(self, wf_name: str, i: int, step: dict,
                     cancel: threading.Event, refs: dict) -> str:
        """Poll for an accessible element (ui_find / ui_wait). Stores the
        resolved element under step['store'] when given. Returns
        'ok' | 'timeout' | 'cancelled'."""
        from ..context import uia
        desc = uia.describe_criteria(step)
        deadline = (time.monotonic()
                    + int(step.get("timeout_ms", 10_000)) / 1000.0)
        log.info("Workflow %r step %s: waiting for UI %s", wf_name, i, desc)
        while True:
            hit = uia.find_element(step)
            if hit is not None:
                control, info = hit
                store = (step.get("store") or "").strip()
                if store:
                    refs[store] = (control, dict(step), info)
                log.info("Workflow %r step %s: UI element found (%s / %s)",
                         wf_name, i, info.control_type,
                         info.name[:40] or info.automation_id)
                return "ok"
            if time.monotonic() >= deadline:
                return "timeout"
            if cancel.wait(self.POLL_S):
                return "cancelled"

    def _run_ui_action(self, step: dict, refs: dict,
                       variables: dict | None = None) -> str | None:
        """ui_click / ui_focus / ui_type on a stored reference.
        Returns an error string or None on success. The element is
        re-validated first — stale/changed targets fail, never act."""
        from ..actions import input_win
        from ..context import uia
        ref = (step.get("ref") or "").strip()
        entry = refs.get(ref)
        if entry is None:
            return f"no stored element named {ref!r} (add a Find step)"
        control, criteria, _ = entry
        info = uia.refresh_info(control, criteria)
        if info is None:
            return (f"element {ref!r} no longer exists or changed — "
                    "not acting on a stale target")
        t = step.get("type")
        if t in ("ui_click", "ui_type") and not info.enabled:
            return f"element {ref!r} is disabled"
        try:
            if t == "ui_click":
                uia.invoke(control)
            elif t == "ui_focus":
                uia.focus(control)
            elif t == "ui_type":
                text = conditions.substitute(str(step.get("text", "")),
                                             variables or {})
                if not uia.set_text(control, text):
                    # explicit fallback: focus the verified target, then
                    # type with the single existing input implementation
                    uia.focus(control)
                    time.sleep(0.05)
                    input_win.type_text(text)
                log.info("Typed %d characters into %r", len(text), ref)
        except ValueError as e:
            return str(e)
        return None

    def _run_wait(self, wf_name: str, i: int, step: dict,
                  cancel: threading.Event) -> str:
        """Poll one condition. Returns 'ok' | 'timeout' | 'cancelled'."""
        desc = conditions.describe(step)
        deadline = (time.monotonic()
                    + int(step.get("timeout_ms", 0)) / 1000.0)
        log.info("Workflow %r step %s: waiting for %s", wf_name, i, desc)
        while True:
            if conditions.check(step):
                log.info("Workflow %r step %s: condition satisfied (%s)",
                         wf_name, i, desc)
                return "ok"
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return "timeout"
            if cancel.wait(min(self.POLL_S, remaining)):
                return "cancelled"

    def _eval_condition_group(self, step: dict,
                              variables: dict) -> bool:
        """ALL/ANY over the step's condition list. Raises ValueError on a
        malformed condition or an undefined variable (a FAILURE,
        distinct from FALSE)."""
        conds = step.get("conditions", [])
        if not conds:
            raise ValueError("condition step has no conditions")
        mode = step.get("mode", "all")
        results = []
        for c in conds:
            if c.get("condition") not in conditions.CONDITION_KINDS:
                raise ValueError(
                    f"unknown condition kind {c.get('condition')!r}")
            results.append(conditions.check(c, variables))
        return all(results) if mode == "all" else any(results)

    def _publish_vars(self, wf_name: str, variables: dict) -> None:
        """Variable snapshot for the live inspector panel (values shown
        only there — logs carry names, never contents)."""
        self.bus.publish("workflow.vars", wf_name, dict(variables))

    def _run_set_var(self, step: dict, variables: dict) -> str | None:
        """SET VARIABLE. Returns an error string or None."""
        name = (step.get("name") or "").strip()
        source = step.get("source", "literal")
        vtype = step.get("value_type", "text")
        if source == "literal":
            raw = step.get("value", "")
            if vtype == "number":
                try:
                    value = float(raw)
                except (TypeError, ValueError):
                    return f"{raw!r} is not a number"
            elif vtype == "boolean":
                value = (raw is True
                         or str(raw).strip().lower() in ("true", "yes", "1"))
            else:
                value = conditions.substitute(str(raw), variables)
        elif source == "variable":
            src = (step.get("source_var") or "").strip()
            if src not in variables:
                return f"Variable {src!r} has not been defined."
            value = variables[src]
        elif source == "clipboard":
            from ..actions import system_actions
            value = system_actions.get_clipboard_text()
        elif source in ("active_app", "active_window"):
            from ..context.detector import snapshot
            ctx = snapshot()
            value = (ctx.process if source == "active_app"
                     else ctx.window_title)
        else:
            return f"unknown variable source {source!r}"
        variables[name] = value
        log.info("Set variable %r (%s)", name, source)
        return None

    def _run_data_step(self, step: dict, refs: dict,
                       variables: dict) -> str | None:
        """set_var / ui_read / set_clipboard. Error string or None."""
        t = step.get("type")
        if t == "set_var":
            return self._run_set_var(step, variables)
        if t == "ui_read":
            from ..context import uia
            ref = (step.get("ref") or "").strip()
            entry = refs.get(ref)
            if entry is None:
                return f"no stored element named {ref!r} (add a Find step)"
            control, criteria, _ = entry
            if uia.refresh_info(control, criteria) is None:
                return (f"element {ref!r} no longer exists or changed — "
                        "not reading a stale target")
            text = uia.get_text(control)
            if text is None:
                return (f"element {ref!r} exposes no readable text via "
                        "UI Automation")
            variables[(step.get("store_var") or "").strip()] = text
            log.info("Read UI element %r into variable %r (%d chars)",
                     ref, step.get("store_var"), len(text))
            return None
        if t == "set_clipboard":
            from ..actions import system_actions
            value = conditions.substitute(str(step.get("value", "")),
                                          variables)
            system_actions.set_clipboard_text(value)
            log.info("Clipboard set from workflow (%d chars)", len(value))
            return None
        return f"unknown data step {t!r}"

    def _run_if(self, ctx: dict, i: str, step: dict) -> tuple[str, str]:
        """Conditional step: evaluate (optionally waiting), publish the
        verdict, then run exactly one branch through the SAME executor.
        FALSE is a branch choice, never a failure."""
        wf_name, total, cancel, refs, depth = (
            ctx["name"], ctx["total"], ctx["cancel"], ctx["refs"],
            ctx["depth"])
        desc = " / ".join(conditions.describe(c)
                          for c in step.get("conditions", []))
        mode = step.get("mode", "all").upper()
        try:
            wait_ms = int(step.get("wait_ms", 0) or 0)
            if wait_ms > 0:
                deadline = time.monotonic() + wait_ms / 1000.0
                verdict = False
                while True:
                    if self._eval_condition_group(step, ctx["vars"]):
                        verdict = True
                        break
                    if time.monotonic() >= deadline:
                        if step.get("on_timeout", "else") == "fail":
                            return ("failed",
                                    f"step {i}: condition not true before "
                                    f"timeout ({mode}: {desc})")
                        break   # explicit: timeout → ELSE branch
                    if cancel.wait(self.POLL_S):
                        return "cancelled", "cancelled"
            else:
                verdict = self._eval_condition_group(step, ctx["vars"])
        except ValueError as e:
            return "failed", f"step {i}: condition error — {e}"
        branch = "then" if verdict else "else"
        self.bus.publish(
            "workflow.progress", wf_name, ctx["top_i"], total,
            f"IF {mode}: {desc} → {'TRUE' if verdict else 'FALSE'} "
            f"({branch.upper()} branch)", "condition")
        log.info("Workflow %r step %s: IF %s: %s → %s", wf_name, i, mode,
                 desc, "TRUE" if verdict else "FALSE")
        steps = step.get(branch, [])
        if not steps:
            return "completed", ""
        return self._exec_steps(ctx, steps, depth + 1, prefix=f"{i}.")

    # hard engine-side loop caps — validation enforces the same numbers,
    # but a hand-imported or hand-edited row must still never spin
    MAX_ATTEMPTS = 20
    MAX_ITERATIONS = 100

    def _loop_progress(self, ctx: dict, label: str) -> None:
        self.bus.publish("workflow.progress", ctx["name"], ctx["top_i"],
                         ctx["total"], label, "condition")

    def _run_retry(self, ctx: dict, i: str, step: dict) -> tuple[str, str]:
        """RETRY block: a failing step fails only the attempt. One
        progress event per attempt — the condition polls inside the
        block never flood the log."""
        cancel = ctx["cancel"]
        attempts = max(1, min(int(step.get("attempts", 3)),
                              self.MAX_ATTEMPTS))
        delay_s = max(0, int(step.get("delay_ms", 0))) / 1000.0
        depth = ctx["depth"]
        last = ""
        for k in range(1, attempts + 1):
            if cancel.is_set():
                return "cancelled", "cancelled"
            self._loop_progress(ctx, f"RETRY attempt {k}/{attempts}")
            log.info("Workflow %r step %s: RETRY attempt %d/%d",
                     ctx["name"], i, k, attempts)
            status, detail = self._exec_steps(ctx, step.get("steps", []),
                                              depth + 1, prefix=f"{i}.")
            if status == "cancelled":
                return status, detail
            if status == "completed":
                until = step.get("until") or []
                if not until:
                    return "completed", ""
                try:
                    if self._eval_condition_group(
                            {"conditions": until,
                             "mode": step.get("until_mode", "all")},
                            ctx["vars"]):
                        return "completed", ""
                except ValueError as e:
                    return "failed", f"step {i}: retry condition error — {e}"
                detail = (f"step {i}: retry condition still false "
                          f"({' / '.join(conditions.describe(c) for c in until)})")
            last = detail
            log.info("Workflow %r step %s: attempt %d/%d failed (%s)",
                     ctx["name"], i, k, attempts, detail)
            if k < attempts and delay_s > 0:
                if cancel.wait(delay_s):
                    return "cancelled", "cancelled"
        self._loop_progress(ctx, f"RETRY EXHAUSTED after {attempts} attempts")
        on_fail = step.get("on_fail", "fail")
        if on_fail == "continue":
            log.info("Workflow %r step %s: RETRY EXHAUSTED — continuing "
                     "(configured)", ctx["name"], i)
            return "completed", ""
        if on_fail == "fallback":
            log.info("Workflow %r step %s: RETRY EXHAUSTED — running "
                     "fallback steps", ctx["name"], i)
            return self._exec_steps(ctx, step.get("fallback", []),
                                    depth + 1, prefix=f"{i}.f")
        return "failed", (f"step {i}: RETRY EXHAUSTED — {attempts} "
                          f"attempt(s) failed (last: {last})")

    def _run_repeat(self, ctx: dict, i: str, step: dict) -> tuple[str, str]:
        """REPEAT N TIMES: fixed, bounded. A failing step fails the
        workflow exactly as it would outside the loop."""
        cancel = ctx["cancel"]
        count = max(1, min(int(step.get("count", 1)), self.MAX_ITERATIONS))
        for k in range(1, count + 1):
            if cancel.is_set():
                return "cancelled", "cancelled"
            self._loop_progress(ctx, f"REPEAT iteration {k}/{count}")
            log.info("Workflow %r step %s: REPEAT iteration %d/%d",
                     ctx["name"], i, k, count)
            status, detail = self._exec_steps(ctx, step.get("steps", []),
                                              ctx["depth"] + 1,
                                              prefix=f"{i}.")
            if status != "completed":
                return status, detail
        return "completed", ""

    def _run_repeat_until(self, ctx: dict, i: str,
                          step: dict) -> tuple[str, str]:
        """REPEAT UNTIL CONDITION: pre-checked (already true → zero
        passes), bounded by BOTH max_iterations and timeout_ms. Hitting
        either bound with the condition still false is an explicit
        TIMEOUT failure. A condition error is a FAILURE, never FALSE."""
        cancel = ctx["cancel"]
        group = {"conditions": step.get("conditions", []),
                 "mode": step.get("mode", "all")}
        desc = " / ".join(conditions.describe(c)
                          for c in group["conditions"])
        mode = group["mode"].upper()
        max_it = max(1, min(int(step.get("max_iterations", 10)),
                            self.MAX_ITERATIONS))
        delay_s = max(0, int(step.get("delay_ms", 0))) / 1000.0
        start = time.monotonic()
        deadline = start + int(step.get("timeout_ms", 10_000)) / 1000.0
        done = 0
        while True:
            if cancel.is_set():
                return "cancelled", "cancelled"
            try:
                if self._eval_condition_group(group, ctx["vars"]):
                    self._loop_progress(
                        ctx, f"REPEAT UNTIL {mode}: {desc} → TRUE "
                             f"after {done} iteration(s)")
                    log.info("Workflow %r step %s: REPEAT UNTIL true "
                             "after %d iteration(s)", ctx["name"], i, done)
                    return "completed", ""
            except ValueError as e:
                return "failed", f"step {i}: condition error — {e}"
            if done >= max_it or time.monotonic() >= deadline:
                return "failed", (
                    f"step {i}: TIMEOUT — condition not true after "
                    f"{done} iteration(s) / "
                    f"{time.monotonic() - start:.1f} s "
                    f"({mode}: {desc})")
            done += 1
            self._loop_progress(
                ctx, f"REPEAT UNTIL iteration {done}/{max_it}: {desc}")
            log.info("Workflow %r step %s: REPEAT UNTIL iteration %d/%d",
                     ctx["name"], i, done, max_it)
            status, detail = self._exec_steps(ctx, step.get("steps", []),
                                              ctx["depth"] + 1,
                                              prefix=f"{i}.")
            if status != "completed":
                return status, detail
            if delay_s > 0 and cancel.wait(delay_s):
                return "cancelled", "cancelled"

    def _exec_steps(self, ctx: dict, steps: list[dict], depth: int,
                    prefix: str = "") -> tuple[str, str]:
        """Execute a step list (top level or a branch) — ONE executor for
        every nesting level. Returns (status, detail)."""
        wf_name = ctx["name"]
        total = ctx["total"]
        cancel = ctx["cancel"]
        refs = ctx["refs"]
        for n, step in enumerate(steps, 1):
            i = f"{prefix}{n}"
            if not prefix:
                ctx["top_i"] = n
            if cancel.is_set():
                return "cancelled", "cancelled"
            label = self._step_label(step)
            t = step.get("type")
            if t == "if":
                ctx["depth"] = depth
                status, detail = self._run_if(ctx, i, step)
                if status != "completed":
                    return status, detail
                continue
            if t in ("retry", "repeat", "repeat_until"):
                ctx["depth"] = depth
                run = {"retry": self._run_retry, "repeat": self._run_repeat,
                       "repeat_until": self._run_repeat_until}[t]
                status, detail = run(ctx, i, step)
                if status != "completed":
                    return status, detail
                continue
            self.bus.publish("workflow.progress", wf_name, ctx["top_i"],
                             total, label, "running")
            if t == "delay":
                if cancel.wait(int(step.get("ms", 0)) / 1000.0):
                    return "cancelled", "cancelled"
                continue
            if t == "wait":
                outcome = self._run_wait(wf_name, i, step, cancel)
                if outcome == "cancelled":
                    return "cancelled", "cancelled"
                if outcome == "timeout":
                    return ("failed",
                            f"step {i}: condition not satisfied before "
                            f"timeout ({conditions.describe(step)})")
                continue
            if t in ("ui_find", "ui_wait"):
                outcome = self._run_ui_find(wf_name, i, step, cancel, refs)
                if outcome == "cancelled":
                    return "cancelled", "cancelled"
                if outcome == "timeout":
                    from ..context.uia import describe_criteria
                    return ("failed",
                            f"step {i}: UI element not found "
                            f"({describe_criteria(step)}) — the "
                            "application may not expose UI Automation "
                            "information")
                continue
            if t in ("ui_click", "ui_focus", "ui_type"):
                err = self._run_ui_action(step, refs, ctx["vars"])
                if cancel.is_set():
                    return "cancelled", "cancelled"
                if err:
                    return "failed", f"step {i}: {err}"
                continue
            if t in ("set_var", "ui_read", "set_clipboard"):
                try:
                    err = self._run_data_step(step, refs, ctx["vars"])
                except ValueError as e:
                    err = str(e)
                if err:
                    return "failed", f"step {i}: {err}"
                if t in ("set_var", "ui_read"):
                    self._publish_vars(wf_name, ctx["vars"])
                continue
            # action step — resolve fresh from the repo (hot reload)
            a = self.actions.get(step.get("action_id", 0))
            if a is None:
                return "failed", f"step {i}: action no longer exists"
            if a.type == "workflow":
                return "failed", f"step {i}: workflows cannot nest workflows"
            params = a.params
            if a.type == "open_url" and "{" in str(params.get("url", "")):
                # substitution is data-only and limited to the URL field;
                # the URL is still validated by the executor afterwards
                try:
                    params = {**params,
                              "url": conditions.substitute(
                                  str(params.get("url", "")), ctx["vars"])}
                except ValueError as e:
                    return "failed", f"step {i}: {e}"
            ok = self.executor.execute(
                ActionSpec(type=a.type, params=params, name=a.name))
            if cancel.is_set():
                return "cancelled", "cancelled"
            if not ok:
                return "failed", f"step {i}: {label} failed"
        return "completed", ""

    def _run(self, wf: WorkflowRow, cancel: threading.Event) -> None:
        status, detail = "completed", ""
        log.info("Workflow %r started (%d steps)", wf.name, len(wf.steps))
        try:
            # refs + vars are RUN-LOCAL: created here, garbage-collected
            # with the thread — never shared between runs or workflows
            ctx = {"name": wf.name, "total": len(wf.steps),
                   "cancel": cancel, "refs": {}, "vars": {},
                   "depth": 0, "top_i": 1}
            status, detail = self._exec_steps(ctx, wf.steps, depth=0)
        except Exception as e:  # noqa: BLE001 — must not kill the thread pool
            status, detail = "failed", str(e)
            log.exception("Workflow %r crashed", wf.name)
        finally:
            with self._lock:
                self._running.pop(wf.id, None)
            self.bus.publish("workflow.done", wf.name, status, detail)
            log.info("Workflow %r %s%s", wf.name, status,
                     f" ({detail})" if detail else "")
