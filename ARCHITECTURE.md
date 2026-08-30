# Architecture

## Pipeline

```text
CameraWorker (thread)          app/camera/capture.py
      │  publishes camera.frame / camera.status
      ▼
HandTracker (MediaPipe Tasks)  app/vision/hand_tracker.py
      │  publishes vision.hands (internal HandFrame — no MediaPipe types leak)
      ▼
GestureEngine (state machine)  app/gestures/engine.py
      │  publishes gesture.event (GestureEvent: start/hold/end,
      │                           source="primitive")
      ├──────────────────────────────► CompoundEngine
      │                                app/gestures/compound.py
      │              consumes primitive events only; emits compound
      │              gesture.event (source="compound") on the same topic
      ▼
MotionController (orchestrator) app/runtime/controller.py
      │  reads ContextDetector.current, asks RuleEngine.resolve()
      ▼
ActionExecutor                 app/actions/executor.py
      │  SendInput / win32 / shell
      ▼
Windows
```

All cross-subsystem communication goes through a thread-safe `EventBus`
(`app/core/events.py`). The UI attaches via `QtBridge`, which re-emits bus
events as Qt signals so widgets update on the GUI thread.

## Modules

| Package | Responsibility |
|---|---|
| `app/core` | shared types, event bus, config, logging |
| `app/camera` | device enumeration, capture thread, disconnect recovery |
| `app/vision` | MediaPipe wrapper, model download, landmark smoothing |
| `app/gestures` | static pose classifiers, swipe detector, custom-gesture recorder/matcher, gesture state machine |
| `app/context` | foreground process/window/cursor/screen polling → normalized `Context` |
| `app/rules` | data-driven rule resolution with deterministic precedence |
| `app/actions` | independent action engine: SendInput mouse/keyboard, window ops, system ops, sequences |
| `app/profiles` | profile manager, seeding, import/export |
| `app/data` | SQLite schema + repositories (the only SQL in the app) |
| `app/runtime` | `MotionController` — lifecycle, arming, safety, cursor mode |
| `app/ui` | PySide6 windows/pages/tray |

## Key types (`app/core/types.py`)

- `Hand` / `HandFrame` — internal landmark representation (21 points).
- `GestureEvent` — `gesture`, `phase` (`start`/`hold`/`end`), confidence,
  wrist position. The gesture engine only emits events; it never executes.
- `Context` — application, process, window title, cursor, screen.
- `ActionSpec` — type + params, pure data.
- `RuleMatch` — resolved rule with action, continuous flag, cooldown.

## Rule precedence (deterministic)

1. App-profile rule with matching window pattern
2. App-profile rule (no window pattern)
3. Global rule with matching window pattern
4. Global rule
5. No match → `rule.unmatched`

Ties: higher profile priority, then lower rule id. All rules/actions come
from SQLite — no application names are hard-coded.

## Safety model

- Motion control starts disarmed; master toggle + tray toggle + EMERGENCY
  STOP.
- Discrete rules fire once on gesture **start**; holds never re-fire unless
  the rule is marked `continuous` (which then respects its own cooldown).
- Per-rule cooldown + global action cooldown + gesture debounce
  (N consecutive frames) + release hysteresis.
- Disarming clears all held continuous actions.

## Launch Application action

`launch_app` is a normal first-class action type — same repository,
rules, sequences, import/export and arbitration as every other action.
Params: `path`, `args`, `cwd`, `if_running` ("new" | "focus").

- Security: the path is untrusted configuration. It must be an existing
  `.exe`; processes are created with an argument LIST via
  `subprocess.Popen(shell=False)` — never a shell, never string
  concatenation. Arguments are tokenized with quote support.
- `if_running="focus"` reuses the win32 window layer
  (`windows_ctl.focus_process`) to foreground an existing visible window
  of the same executable instead of spawning again; falls back to
  launching when none exists. Default is "new"; repeated-fire storms are
  already prevented by gesture cooldowns + arbitration.
- Discovery (`app/actions/discovery.py`): App Paths registry (HKLM/HKCU,
  both views) + System32 basics. No install paths are hard-coded.
  Limitation: UWP/MSIX apps without an App Paths `.exe` entry are not
  discoverable/launchable.
- UI: dedicated form (no JSON) with app picker, Browse, arguments,
  working directory, run-behavior. "Test action" asks for confirmation
  before actually launching; safe Test Recognition only *shows* the
  resolved launch action.

## Workflows (multi-step actions)

A workflow is an ordered step list stored in the `workflows` table
(`steps_json`): `{type:"action", action_id}` steps reference existing
named actions by id (implementations are never duplicated),
`{type:"delay", ms}` steps pause, and
`{type:"wait", condition, process, title, timeout_ms}` steps poll a
desktop condition. The typed step schema leaves room for future kinds
(conditionals, variables) without a migration.

### Smart wait conditions

Condition semantics live in `app/context/conditions.py` (Win32 stays in
the context layer; the engine only calls the generic
`check/validate/describe` API — a strategy seam for future kinds):

- `app_running` / `process_exists` — Toolhelp process snapshot (direct
  Win32, no shell); names normalized (`Chrome` → `chrome.exe`).
- `window_exists` / `window_title` — EnumWindows over visible titled
  windows, filtered by process name (same helper as the context
  detector) and/or title pattern using the **same matching semantics as
  window rules** (`_title_matches`: wildcards via fnmatch, otherwise
  substring, case-insensitive).

Engine behavior for wait steps: poll every 250 ms (`POLL_S`) with a
cancellable `Event.wait` — checked immediately first, so an
already-true condition continues with zero added latency; timeout →
workflow FAILED at that step ("condition not satisfied before
timeout"); Emergency Stop / motion off / shutdown cancel mid-poll
instantly. Checks never raise (a failed probe reads "not yet true").
One log line per wait + one on satisfaction — polling never logs.
Measured cost per check: window conditions ~0.25 ms, process snapshot
~9 ms → worst case ~4% of one core only while a process-wait is
actively polling; zero cost otherwise, nothing added to the camera
path.

The workflow itself is exposed as a normal action of type `workflow`
with params `{workflow_id}` — the rule engine, profiles, precedence,
arbitration, Studio and import/export treat it like any other action:

```text
Gesture → Rule → workflow action → WorkflowEngine → ActionExecutor
```

The workflow builder is a one-stop editor: name, description, enabled
state, ordered steps, and an optional trigger (gesture + profile). The
trigger is stored as an ordinary rule through the existing rule engine —
no bypass, normal precedence/arbitration. Saving validates deeply:
structure (repo), referenced actions must exist and pass the executor's
validator, conditions must be well-formed; broken workflows are never
silently saved, and Test Workflow shows the validated plan before an
explicit run confirmation.

### Conditional steps (Workflow Logic 3.0)

`{type:"if", conditions:[…], mode:"all"|"any", wait_ms, on_timeout:
"else"|"fail", then:[steps], else:[steps]}` — declarative only, no
expressions or scripting. Conditions reuse the single condition engine
(`app/context/conditions.py`, including `ui_element`). ELSE-IF chains
are nested `if` nodes in the else branch. Execution is one recursive
`_exec_steps` walker — the same executor at every nesting level, so
cancellation, Emergency Stop, the duplicate guard, UI-reference
re-validation and shutdown behave identically inside branches. FALSE
selects the ELSE branch and is never a failure; failures are
evaluation errors (malformed condition), configured `on_timeout:
"fail"`, action/UI failures, or cancellation. `wait_ms > 0` polls the
group (cancellable, 250 ms) until true or timeout; timeout behavior is
always explicit. Verdicts publish one `workflow.progress` event with
state "condition" ("IF ALL: … → TRUE (THEN branch)") — no log
flooding. Validation is recursive: per-condition checks, ALL/ANY mode,
non-empty THEN, action existence and UI-ref ordering inside branches,
and a nesting cap (`MAX_IF_DEPTH` = 5). Export/import converts nested
action steps to portable name references recursively; old linear
workflows are untouched.

### Workflow variables (data flow)

Run-local variables live in the execution context next to UI refs —
created per run, destroyed with the thread, never shared between
concurrent runs. Types: text / number / boolean only. Steps:
`set_var` (sources: literal — typed —, another variable, clipboard
text, active application/window via a one-shot context snapshot),
`ui_read` (accessible text of a re-validated stored element: Value →
Text pattern → Name), `set_clipboard`. Substitution `{name}` is
data-only and limited to explicit surfaces: `ui_type` text,
`set_clipboard` value, and the `url` of `open_url` actions (still
validated by the executor afterwards) — never launch paths, shell
strings or SQL; there is no expression language and no eval. The
condition system gained the `variable` kind with a fixed operator set
(text/number/boolean); an undefined variable or type mismatch raises —
a FAILURE, distinct from FALSE. Validation statically tracks
defined-before-use through branches (conservative union, missing refs
fail cleanly at run time), name syntax, sources, types and numeric
literals. Variable snapshots publish on `workflow.vars` for the
read-only live panel shown during Test Workflow; logs carry variable
names and lengths, never contents. Clipboard is touched only when a
step runs — never monitored.

### Retries & bounded loops (control flow)

Three block step kinds, executed by the same recursive `_exec_steps`
walker as If/Else (same cancellation, duplicate guard, refs/vars —
loop iterations share the run context, nothing resets between passes):

- `{type:"retry", attempts, delay_ms, steps, until?, until_mode?,
  on_fail:"fail"|"continue"|"fallback", fallback?}` — a failing step
  fails only the attempt; the block reruns after a cancellable delay
  (steps before the block never re-execute). `until` (optional
  condition list, existing engine) must also hold for an attempt to
  succeed; it is evaluated with the run's variables. Exhaustion is an
  explicit RETRY EXHAUSTED; `on_fail` picks fail / continue / run
  fallback then continue.
- `{type:"repeat", count, steps}` — fixed bounded repetition; failures
  behave exactly as outside the loop.
- `{type:"repeat_until", conditions, mode, max_iterations, delay_ms,
  timeout_ms, steps}` — group checked BEFORE each pass (already true →
  zero passes); both the iteration bound and the time limit are
  mandatory, and hitting either while false is an explicit TIMEOUT
  failure. Condition errors (e.g. undefined variable) are failures,
  never FALSE.

Safety: `MAX_RETRY_ATTEMPTS` = 20, `MAX_REPEAT_ITERATIONS` = 100
(config `workflow_max_repeat` may only lower it), one shared
`MAX_NESTING_DEPTH` = 5 across if/retry/repeat/repeat_until, and the
engine re-clamps every bound at run time so a hand-edited row still
cannot spin. There is no while-true, no unbounded repeat, and workflow
recursion stays rejected. Loops publish one `workflow.progress` event
per attempt/iteration (state "condition": "RETRY attempt 2/3",
"REPEAT UNTIL iteration 3/10 …") — polling never floods the log — and
all waits are `Event.wait`-based (no CPU spinning; the camera/gesture
pipeline is untouched). Export/import recurses through `steps` and
`fallback` like If branches; validation checks bounds, on_fail,
condition well-formedness (retry `until` sees the block's variables —
it runs after the block) and the depth cap.

### Gesture Studio 2.0 (calibration / diagnostics / safety)

Diagnostics and safety layered on the existing recognition engine — no
second detector, no parallel recognition state machine.

- **Read-only diagnostics** — `GestureEngine.diagnostics(gesture)`,
  `threshold_for()` and `cooldown_remaining()` project EXISTING engine
  state (current gesture/confidence, per-gesture `_states`, trajectory
  candidate, detector thresholds) into a snapshot dict. They never
  mutate anything and never fabricate a confidence (fields a detector
  doesn't expose stay `None`). The Studio dialog polls these scalars on
  the GUI thread — it never processes camera frames or MediaPipe.
- **Circle diagnostics** — `CircleDetector.last` records direction,
  movement, closure, sweep and result of the most recent evaluated arc.
  Read-only; detection logic is unchanged.
- **Presets** (`app/gestures/presets.py`, pure) — SAFE/BALANCED/FAST are
  bundles of the EXISTING `GestureSettingsRepo` keys; BALANCED is `{}`
  (defaults). Applied through the existing `apply_gesture_settings`
  hot-reload path.
- **Reset fix** — `apply_gesture_settings` now snapshots the swipe/circle
  detector defaults at construction and restores-then-overrides on every
  call, so clearing an override truly resets those in-place-mutated
  detectors (previously a cleared override left the last preset's values).
- **Gesture lock** — `MotionController.set_gestures_locked()` +
  `control.locked` bus event. `_fire` and `_drive_cursor` return early
  when locked (publishing `gesture.blocked` for diagnostics) so
  recognition/arbitration still run but nothing executes. Distinct from
  motion-off; Emergency Stop still gates `_on_gesture` entirely.
- **Neutral-before-retrigger** — `Config.require_neutral_before_retrigger`
  → `GestureEngine.require_neutral`. A single boolean gate on the
  existing lifecycle: a fired drawn-shape name is added to a
  `_neutral_block` set and suppressed until a neutral frame (no hand, or
  a relaxed static pose) clears it. It only gates trajectory shapes —
  swipes and statics are untouched (repeated swipes keep working).

### Hand selection / control routing (`app/core/hand_select.py`)

One authoritative eligibility layer at the boundary between the tracker's
handedness output and recognition — NOT a second recognizer, detector or
matcher. Pure and dependency-light (no MediaPipe, no Qt).

- Flow: `HandTracker → filter_hand_frame → GestureEngine → …`. The filter
  runs as the FIRST line of `GestureEngine.on_hands`: it drops ineligible
  hands before any recognition. `mode == "both"` returns the SAME frame
  object (strict no-op) so default behavior is unchanged and free.
- A dropped hand is indistinguishable from an absent hand downstream, so
  every existing path (swipe/circle/template/static/custom/compound) and
  arbitration is untouched — events simply don't originate from the
  non-selected hand, and there is never a fall-back to it.
- Handedness convention — contract: physical LEFT → "left", physical
  RIGHT → "right". Verified end to end: `CameraWorker` publishes the RAW
  frame (no flip) and the tracker passes MediaPipe's `category_name`
  through verbatim, so **nothing mirrors before or inside classification**
  and the tracker label ALREADY denotes the user's physical hand →
  `user_perspective()` is the IDENTITY. The mirrored/selfie view is a
  DISPLAY-only transform in `ui/video_widget.py` and never feeds back into
  control. Image mirroring inverts the x AXIS — handled separately and
  correctly where it truly applies (`SwipeDetector.mirror_x`, the cursor's
  x inversion, the preview) — but handedness is an anatomical
  classification, not a coordinate, so it needs no flip. Conflating the
  two is what previously inverted this table (an a-priori reading of the
  "input is assumed mirrored" note, contradicted by the observed behavior
  of MediaPipe Tasks here). `Hand.handedness` reaching recognition is
  unchanged, so custom-gesture folding and compound hand-locking are
  unaffected.
- Config `hand_control` ("left"|"right"|"both", default "both");
  `normalize_hand_control` coerces missing/invalid → "both". Applied live
  via `GestureEngine.hand_control` (`MotionController.set_hand_control`, no
  restart); persisted; survives camera reconnect and `gestures.reset()`.
  `GestureEngine.current_handedness` (user-perspective) feeds the Studio
  "detected:" readout via the existing `vision.hands` bridge signal.
- Note: with `num_hands = 1` the tracker returns a single hand; a
  single-hand mode then rejects it when it's the wrong hand (no control),
  and the "both hands present, only selected contributes" case is covered
  once `max_hands ≥ 2`. The filter logic is independent of that config.

### Cursor output boundary (`app/runtime/cursor.py`)

One module owns everything that reaches the OS pointer for cursor control:
`CursorController` (anchor-relative mapping + the filter), `CursorFilter`
(stabilization) and `DragMachine` (pinch-and-hold). It is **not** a
recognizer — it consumes positions and an existing pinch confidence the
controller feeds it, after hand selection and every safety gate have run.

- **Single owner.** `_drive_cursor` delegates to `CursorController.move()`;
  the anchor that used to live on the controller was removed, so there is
  exactly one anchor and one filter (a leftover second anchor would eat a
  frame — that was caught by the previous milestone's tests).
- **Adaptive smoothing** — EMA on the cursor TARGET (screen px), alpha
  interpolated from the per-frame distance: `MIN_ALPHA` (0.15) at/below
  `SLOW_PX` (4 px) → heavy smoothing when stationary; `MAX_ALPHA` (0.90)
  at/above `FAST_PX` (80 px) → near-raw when sweeping. Filtering the
  target (not landmarks) avoids double-filtering the tracker's existing
  `LandmarkSmoother` EMA and keeps fast motion lag-free.
- **Deadzone** `DEADZONE_PX` (4 px) plus a sub-pixel guard: noise below it
  produces no `move_to` call at all.
- **Spike rejection** — a jump over `SPIKE_PX` (700 px) is skipped for ONE
  frame, then accepted, so a bad estimate can never freeze the cursor.
- **Reset points** (anchor + filter): cursor session start/end, hand lost,
  control-hand change, motion off, camera disconnect, lock, disarm,
  shutdown.

**Drag state machine** — `IDLE → CANDIDATE → DRAGGING → IDLE`, fed one
frame at a time with the existing `static.pinch_confidence`:

- CANDIDATE requires `start_conf` 0.6; DRAGGING starts only after the
  pinch is held `cursor_drag_start_ms` (default 150 ms). Release uses the
  lower `cursor_drag_release` (default 0.35) sustained for 2 frames —
  hysteresis in both directions, so noise neither starts nor cancels.
- `_down` makes press/release strictly paired; `abort()` is idempotent and
  is called from every interruption path, so **every `button_down` has
  exactly one `button_up`**.
- Movement while dragging comes from the `vision.hands` feed (the pinch
  pose ends the open-palm continuous action), routed through the SAME
  controller + filter — not a second cursor path.
- `_drag_allowed()` re-checks motion-enabled, Studio lock and the arming
  gate every frame: drag never bypasses a safety gate.
- While `cursor_drag_enabled`, `_drag_consumes()` drops pinch events at
  the controller gate so dragging never also executes the pinch mapping
  (no double-fire). Recognition itself is untouched.
- Cost when disabled (the default): one boolean check per frame.

### Cursor control movement sensitivity

Cursor control is anchor-relative: the first `hold` frame stores the hand
position + current mouse position, and each later frame maps the hand
delta to a screen delta (`MotionController._drive_cursor`). That mapping
has always had a single gain (a hard-coded `2.2`); the setting simply
EXPOSES that existing parameter — there is no second cursor engine and no
second sensitivity system.

- `Config.cursor_sensitivity` (default `2.2` = historical behavior; range
  0.5–6.0 via `normalize_cursor_sensitivity`, which clamps and falls back
  to the default for missing/invalid/NaN values, so old config files keep
  behaving identically).
- Applied ONLY inside `_drive_cursor`, i.e. at the narrowest cursor
  movement boundary — never to raw landmark coordinates, never to any
  other action. `MotionController.set_cursor_sensitivity()` updates it
  live (next cursor frame) and persists it.
- Independent of Open-Palm RECOGNITION confidence
  (`gesture_confidence_threshold` / per-gesture overrides) and of hand
  selection. Existing landmark smoothing (`landmark_smoothing`, tracker
  level) and the anchor/re-anchor behavior are untouched; no deadzone was
  added.
- UI: Settings → **Cursor control → Sensitivity** slider (with a
  Low/Medium/High readout), not the Studio safety bar.
- Safety is unchanged: `_drive_cursor` still returns early on gesture
  lock, and cursor events only reach it through `_on_gesture`, which is
  gated by motion-off/E-stop and the arming gate.

### Gesture arming / disarming (`app/runtime/arming.py`)

A CONTROL-layer state machine at the execution boundary — NOT a
recognizer. `ArmingController` observes the same `gesture.event` the
controller already handles and decides whether execution proceeds; it
never touches the camera/vision threads, never re-detects a gesture, and
never duplicates arbitration or mapping. O(1) per event.

- States: `DISARMED` (recognition + diagnostics run, nothing executes),
  `ARMING` (arming gesture held toward `arm_hold_ms`), `ARMED` (existing
  behavior, unchanged), `DISARMING` (transient; the moment pending
  execution is cancelled). Starts DISARMED; not persisted → every app
  start is DISARMED.
- The gate: `MotionController._on_gesture` calls `self.arming.allow(ev)`
  right after the motion-enabled check. `allow()` returns `False` (event
  dropped, never forwarded to arbiter/execution) for the configured
  arming/disarm **control gestures** — so they never run their own mapped
  action and never double-execute — and returns `False` for every gesture
  while not ARMED. Feature-off (or enabled without an arming gesture
  chosen) is pass-through `True`, so nothing changes for existing users
  and a misconfiguration can't lock anyone out.
- Consuming the control gesture happens purely at this gate — there is no
  second recognition path. The arming gesture is an ordinary recognized
  gesture; the controller simply doesn't forward it.
- Leaving ARMED fires an `on_disarm` callback into the controller that
  cancels anything pending (`arbiter.reset()`, continuous actions,
  confirmations) — nothing armed survives a disarm.
- Automatic disarm: `control.enabled False` (motion off) disarms when
  `disarm_on_motion_off`; camera DISCONNECTED/STOPPED/ERROR disarms when
  `disarm_on_camera_disconnect`; a CONNECTED (reconnect) never auto-arms.
  **Emergency Stop always disarms** — `emergency_disable()` calls
  `arming.force_disarm()` directly (independent of config) before motion
  off, which also cancels an in-progress ARMING.
- Config: `arming_enabled`, `arming_gesture`, `disarm_gesture`,
  `arm_hold_ms`, `disarm_on_motion_off`, `disarm_on_camera_disconnect`
  (JSON config; OFF by default). `arming.state` is published on the bus →
  `QtBridge.arming_state` → Studio "Arming & safety" section (the only
  place it's configured) and the Command Center read-only indicator.

Centralized safety order (no conflicting states):

    EMERGENCY STOP  >  LOCK / DISARMED  >  ARMED

E-Stop (motion off) blocks `_on_gesture` entirely and force-disarms; LOCK
and DISARMED are peers that let recognition run while blocking execution
(`_fire`/`allow`); ARMED restores normal execution but LOCK/motion-off
still win.

### Gesture Command Center

A UX/orchestration layer, not a new engine. Mappings ARE rows in the
`rules` table resolved by `RuleEngine`; the Command Center only
visualizes and edits them.

- **Conflict analyzer** (`app/rules/analyzer.py`) — pure, read-only.
  Mirrors the engine's precedence tiers to classify each enabled rule as
  `conflict` (same gesture + same profile + same window/zone — the engine
  can only tie-break by rule id), `info` (cross-tier overlap, e.g. an app
  rule that wins over a global rule — deterministic, explained), or `ok`.
  It never changes resolution or arbitration; it explains them.
- **Activity log** (`app/runtime/activity.py`) — a bounded in-memory ring
  (last 50) subscribed only to `rule.matched` and `workflow.done` (never
  camera/vision/gesture events, so zero recognition-thread cost). It
  attributes a `workflow.done` back to the gesture that started it and
  publishes `activity.changed` for the UI to refresh via the bridge. No
  database log.
- **Dangerous-workflow confirmation** — `WorkflowRow.requires_confirmation`
  (new additive column, migrated) plus the global `Config.
  confirm_dangerous_workflows`. In `MotionController._fire`, a
  gesture-resolved workflow flagged for confirmation is not executed
  inline: the controller stores it under a token and publishes
  `workflow.confirm_request`; the GUI (bridge → modal, on the GUI thread)
  answers via `resolve_confirmation(token, accept)`, which re-checks
  motion state before running. Motion-off / Emergency Stop clears all
  pending confirmations (authoritative). The lookup is one indexed query
  at fire time only — never per frame.
- **Command Center page** (`app/ui/command_center.py`) — mapping table,
  one-dialog quick-assign, execution-chain preview, safe Test Gesture
  (observes `gesture` events, resolves read-only, runs only on explicit
  confirm), live feedback and the activity list. All updates arrive via
  the existing `QtBridge` signals (`rule_matched`, `workflow_progress/
  done`, `activity_changed`, `confirm_request`) — the page never polls the
  DB per frame and never touches the camera/gesture threads.

### Workflow recorder (action capture)

A convenience layer that turns real desktop actions into an editable
workflow — **not** a second engine. Two clean halves:

- **Capture** (`app/context/capture.py`, `DesktopCapture`) — Win32 stays
  here. Dormant until `start()`, it installs low-level mouse/keyboard
  hooks (`WH_MOUSE_LL`/`WH_KEYBOARD_LL`) on a dedicated thread that pumps
  the message queue with `PeekMessage` while re-checking a stop event
  every tick (so teardown is race-free — `stop()` just sets the event, no
  `PostThreadMessage` that could be lost before the thread owns a queue),
  plus a light foreground poll, and emits normalized raw event dicts to a
  callback. The app's own PID is filtered on every event;
  injected (SendInput) input is ignored; UI targets and secure/password
  detection go through `app/context/uia.py` (`target_at_point`,
  `focused_target`) — never bare coordinates. ctypes handle signatures
  are pinned so 64-bit hook/module pointers are not truncated. Any
  failure degrades to "no event", and `stop()` removes every hook.
- **Conversion** (`app/runtime/recorder.py`, `build_steps`) — a PURE
  function: raw events → workflow step dicts in the exact portable shape
  `ProfileManager` imports. It filters noise (movement/idle/own UI/
  unknown kinds), consolidates redundant events (click→focus→type ⇒ one
  Find/Focus/Type; app opens/activates/window-appears ⇒ Launch + one
  wait), turns transitions into SEMANTIC waits (`app_running`,
  `window_title` via a distinctive title token, else `window_exists`) —
  never raw millisecond delays — captures typed text as a `set_var` +
  `{name}` substitution in a Type step, detects a browser address-bar URL
  as an Open URL step, and records secure inputs as a `[SECURE INPUT]`
  placeholder (characters never buffered).

`WorkflowRecorder` orchestrates a session (start/stop/pause/resume/
cancel, one at a time, injectable capture backend) and cancels itself on
`control.enabled False` (Emergency Stop / motion-off) and app shutdown.
`stop()` returns raw events; `build()` converts; the UI then materializes
the portable steps via `ProfileManager.materialize_steps` (the exact
importer path — find-or-create named actions) and hands them to the
normal `WorkflowBuilderDialog` for review/edit/test/save/trigger. Nothing
is executed or saved automatically. Recorded workflows are byte-identical
in shape to hand-built ones, so every downstream system (validation,
execution, retries/loops, export/import, gesture triggers) is unchanged.

`WorkflowEngine` (`app/runtime/workflows.py`) runs each started workflow
on its own daemon thread:

- **Non-blocking:** starting returns immediately; delays use
  `threading.Event.wait`, never `time.sleep` on the GUI/camera threads.
- **States/feedback:** publishes `workflow.progress`
  (name, step i/total, label) and `workflow.done`
  (completed | failed | cancelled) on the bus; the Dashboard binds to
  both.
- **Failure = stop:** the first failing step ends the workflow as FAILED
  with "step N: <label> failed"; later steps never run.
- **Cancellation:** `control.enabled False` (motion off / EMERGENCY
  STOP) and app shutdown cancel all running workflows mid-delay;
  cancellation wins immediately.
- **Concurrency:** one instance per workflow — a second start reports
  "already running" (different workflows may run in parallel). Gesture
  debounce/cooldowns/arbitration gate the trigger as usual.
- **No nesting:** workflows cannot contain workflow actions, and
  sequences cannot contain workflow steps (validated + enforced at
  runtime).
- **Hot reload:** step actions are resolved fresh from the repository at
  execution time — edits apply to the next run with no restart.
- Step 1 runs only after the gesture legitimately fired (motion on,
  rule enabled, arbitration settled); subsequent steps intentionally do
  not require the gesture to be held.

`open_url` validates its URL (http/https + host only) and opens it via
the OS default-browser mechanism (`webbrowser`) — never a shell.

The context detector dampens locked-workstation noise: when snapshots
fail (secure desktop denies cursor/window queries) it logs one WARNING
per failure streak and the rest at DEBUG, resetting on recovery.

## UI-aware workflow steps (Windows UI Automation)

`app/context/uia.py` confines all UIA/COM access behind a normalized
`UIElementInfo` model (name, control type, automation id, class,
process, window title, enabled, visible, rect) and a small API:
`find_element` (bounded BFS over top-level windows filtered by process/
title, node+depth budgets for huge trees), `refresh_info` (identity
re-validation), `invoke` (Invoke → Toggle → SelectionItem → legacy
DoDefaultAction; raises rather than falling back to coordinates),
`focus`, `set_text` (Value pattern), `element_from_point` (Inspector).
COM is initialized per call, so any worker thread may use it and
nothing blocks shutdown. Matching (`matches`) is a pure function:
control type exact, name case-insensitive with the same
wildcard/substring semantics as window rules, automation id
case-sensitive, opt-in enabled/visible requirements.

Workflow step types `ui_find`/`ui_wait` (poll at the engine's 250 ms
cadence until found or timeout → FAILED), `ui_click`, `ui_focus`,
`ui_type` operate on workflow-local element references (`store:` →
`ref:`) that live only for the run. Before every interaction the
reference is re-validated (element exists, still matches its criteria
and process, enabled for click/type) — a stale or swapped element
fails the step; nothing is ever clicked blind. `ui_type` prefers the
UIA Value pattern and falls back explicitly to focusing the verified
target + unicode keystrokes (`input_win.type_text`, the single typing
implementation). Typed values are logged as lengths, not content.
The condition system also gained a `ui_element` kind for wait steps.

Performance/safety: UIA is touched only while a workflow step,
condition poll, read-only "Test Element", or the (open) UI Inspector
explicitly asks — no background desktop scanning, camera/gesture
pipeline untouched. Apps without an accessibility tree simply time out
with an honest reason. Validation rejects UI steps whose `ref` is not
stored by an earlier find step.

## Database schema

`profiles`, `profile_apps` (process/window association), `actions`
(named, reusable, `params_json`, sequences included), `rules`
(profile+gesture+action+conditions), `custom_gestures` (templates),
`motion_gestures` (recorded trajectory shapes), `zones`, `settings`,
`workflows` (multi-step definitions), `compound_gestures`. Foreign keys
ON, cascading deletes.

## Compound / temporal gestures

`CompoundEngine` (`app/gestures/compound.py`) layers on top of the
primitive engine — it consumes the normalized `gesture.event` stream
(no vision processing) and runs one explicit state machine per enabled
definition:

```text
IDLE ──step 0 satisfied──▶ WAITING(step 1) ── … ──▶ MATCHED → emit
  ▲                              │
  └────── timeout / cancel ◀─────┘
```

- **Step types:** `gesture` (satisfied by START), `hold` (START then
  still ACTIVE after `hold_ms`; early END cancels the sequence),
  `release` (satisfied by END; empty gesture = previous step's gesture).
- **Timing:** `step_timeout_ms` bounds the gap between steps,
  `max_duration_ms` bounds the whole sequence, `min_gap_ms` debounces
  double-taps, `cooldown_ms` gates re-fire. Stale partials expire lazily
  — expiry is checked before any match, so they can never complete late.
- **Hand identity:** `any` / `left` / `right` / `same` (locked to the
  first step's hand).
- **Cancellation:** motion-off / emergency stop (`control.enabled
  False`) and camera disconnect reset all partial sequences.
- **False-positive protection:** the primitive layer emits exactly one
  START per physical gesture, so a held pinch can never count as two;
  `strict` mode optionally aborts on unexpected gesture STARTs.
- Compound events are published on the same `gesture.event` topic with
  `source="compound"` (START then END), making compound names
  first-class in the rule engine — same profile/window precedence, same
  Studio mapping flow. Compound input ignores compound events, so
  compounds cannot nest.
- Definitions live in the `compound_gestures` table (steps as JSON) and
  hot-reload with everything else.

## Gesture arbitration (primitive vs compound conflicts)

`GestureArbiter` (`app/runtime/arbiter.py`) sits between gesture events
and action dispatch, so a primitive that is part of a compound never
fires its own mapping by accident:

- A primitive that cannot begin/continue any **context-relevant**
  enabled compound executes immediately (one prefix-dict lookup — no
  measurable overhead, no added latency).
- Otherwise its resolved action is **held** (rule resolved at gesture
  time, in the context where the gesture happened).
- Compound completes → all held component actions are cancelled and
  exactly one compound action runs; the completing event never
  double-acts.
- No continuation → the held action executes deterministically when
  every possibility has expired (latest applicable step-timeout / hold
  window + 60 ms slack).
- **Longest match:** if a completed compound is a step-prefix of a
  longer, still-viable compound, its action is held; the longer one
  completing cancels it, the longer one expiring releases it.
- Wrong-gesture policy (lenient): unrelated primitives execute
  immediately; held actions still release at their own deadlines.
- Early release: when the pending gesture ENDS and every compound track
  that justified the hold is dead (e.g. aborted hold step), it releases
  immediately.
- Cancellation: motion-off / emergency stop / camera disconnect drop all
  held actions; motion state is re-checked at release time.
- Continuous / cursor-control rules are never held (latency would break
  them).
- Relevance is contextual: a Chrome-only compound never delays gestures
  on the Desktop.

Trade-off: with a compound configured, its opening primitive's action
lags by that compound's gap window (default 700 ms) when performed
alone. That is inherent to disambiguation; tune per-compound timing to
taste.

## Trajectory / shape gestures

`TrajectoryEngine` (`app/gestures/trajectory.py`) runs inside the
gesture engine's frame step — it consumes the already-tracked normalized
index-fingertip position (zero extra vision work) and runs a list of
detector strategies over a shared time-windowed history. Adding future
shapes (triangle, zigzag, …) = one new detector class in `DETECTORS`;
the event flow, settings plumbing and UI listing are shape-agnostic.
The history is the same normalized-path representation the custom
recorder uses, ready for a future "record motion gesture" feature.

`CircleDetector` — approximate by design (hand-drawn circles are ovals):
multi-anchor scan over path suffixes; a fire requires size (mean radius
≥ min_diameter/2), duration bounds, roundness (radii std/mean ≤ 0.35),
closure (end near start relative to radius), ≥ 300° of angular sweep in
a consistent direction (CW and CCW both valid), and path-length ≈
circumference. Confidence blends those scores against the user's
"recognition sensitivity". One fire per circle: history cleared +
cooldown.

False-positive/arbitration design (swipe vs shape):

- A cheap displacement gate stops idle/hover frames before any
  analysis; a tracking gap resets the path (disjoint motions never
  join).
- The swipe detector is ALWAYS fed — no candidate/partial shape match
  may starve it (that pre-suppression was a regression: it skipped
  swipe updates on ordinary wobbly swipes). Arbitration is post-fire
  and deterministic, keyed on the accumulated tangent turn of the
  recent fingertip path:
  - turn < ~34° at fire time → the swipe emits instantly (pure swipes:
    zero added latency);
  - otherwise the fired swipe is held up to 250 ms: dropped when a
    shape completes or the turn proves a loop/reversal (≥ ~109°),
    emitted when the motion ends or the hold expires. A circle's early
    arc therefore never emits a swipe, while a genuinely curved swipe
    is merely delayed a few frames — never lost.
- Template detectors never claim swipe-like paths: a candidate segment
  whose net/path straightness is ≥ 0.72 is skipped (a short
  directional translation is the swipe detector's domain by
  definition). Recording a swipe-like shape as a motion gesture is
  therefore unsupported (documented).
- A completed shape cancels a held (ambiguous) swipe and clears swipe
  history; it can never cancel an already-emitted swipe. One event per
  gesture, no duplicates.
- Static poses keep tracking during the motion; shapes emit as
  instantaneous start+end events (like swipes) and flow through
  arbitration/compounds/rules as ordinary gesture names.

Cost: ~0.011 ms/frame worst case (actively circling), ~0.006 ms idle —
≈0.1% of the 8.2 ms vision step.

### Known gesture interactions

These are intentional, verified consequences of the swipe-never-starved
design above — documented so they are not mistaken for defects. (The
14-case real-engine interference matrix in TESTING covers them.)

**Swipe-vs-shape precedence.** The swipe detector is fed every frame and
is authoritative for *straight* motion; the trajectory detectors (circle
+ recorded templates) are authoritative for *turning/closed* motion. A
straight stroke emits a swipe immediately (zero latency); a turning
stroke's swipe is held up to 250 ms and dropped if a shape completes or
the path loops/reverses (≥ ~109°). A completed shape cancels only a held,
not-yet-emitted swipe — never an already-emitted one. This split is
deterministic and never lets a partial/near shape match consume a swipe.

**Straight-first-stroke interaction (known, out of scope to "fix").** A
recorded motion template whose FIRST stroke is long, fast and straight
(≥ the swipe distance ~0.15 view and speed ~0.6 width/s) can emit a
directional swipe *before* the template completes — because that opening
stroke *is*, at that instant, an authoritative straight swipe. Example: a
hard synthetic "Z" (three straight strokes) emits `swipe_left` from its
top stroke and then `zed` when the shape closes. This is the direct price
of keeping swipe recognition continuously fed; suppressing it would
require look-ahead that re-buffers/second-guesses straight strokes and
would re-introduce the swipe-starvation regression, so it is deliberately
**not** changed.

- Curved/turning templates (circle, loops, zig-zags with reversals) grow
  the accumulated tangent turn and are held/dropped correctly, so they do
  NOT exhibit the interaction — it is specific to a straight opening
  stroke that already satisfies the swipe gate on its own.
- Existing mitigations, no code change required: prefer **curved motion
  templates** (avoid a long straight lead-in); **neutral-before-retrigger**
  and per-gesture/rule **cooldowns** bound repeats; and the swipe and the
  shape are ordinary gesture names, so mapping either (or neither) is a
  normal Command Center choice.

**Video preview cost (technical debt, not a gesture interaction).** The
dashboard/test video widget mirrors and smooth-scales the full camera
frame on the GUI thread at ~30 fps (`app/ui/video_widget.py`). It is
preview-only, throttled, and decoupled from capture (the frame callback
only stores a reference), but it is the heaviest GUI-thread work in the
app. Left as-is intentionally — a rewrite risks preview behavior for no
recognition benefit. Tracked in ROADMAP tech-debt.

### Recorded motion gestures (custom trajectory templates)

`TemplateDetector` instances plug into the same engine, one per row of
the `motion_gestures` table (hot-reloaded via
`TrajectoryEngine.set_templates`; built-in detectors keep their
instances and tuned settings). Deterministic $1-recognizer-style
matching — no ML:

- Recording (Gestures → "Create motion gesture…") captures the index
  fingertip for ~2.5 s per sample, multiple samples; each sample is
  resampled to 32 arc-length-uniform points, centered on its centroid
  and scaled to unit RMS radius (position/scale/speed-invariant), then
  samples are averaged point-wise and re-normalized into one template.
  Only this normalized polyline is stored — no camera frames.
- Matching runs the same multi-anchor suffix scan as circle; the
  candidate segment is normalized identically and compared point-wise,
  minimized over a small rotation search (±25°) and an optional
  direction flip (`allow_reverse`, on by default). Confidence =
  1 − distance/(2·tolerance); fires need the per-gesture confidence
  threshold, plus minimum size, duration bounds, and the shared
  cooldown/one-event/reset behavior.
- Measured separation on synthetic shapes: same shape (any
  position/scale/speed/direction, moderate noise) ≥ 0.95 confidence;
  wrong shape ≤ 0.34; straight cursor motion 0.0; 60%-drawn shape 0.29
  — the 0.55 default threshold sits in a wide margin.
- A near-match (≥ 70% of threshold) sets the candidate flag, joining
  the circle candidate in suppressing swipe fires mid-draw.

**Template management (Studio 2.1).** The stored template keeps the
normalized per-sample trajectories in `raw_samples` (plus a `revision`
counter) alongside the merged `points` — same table, same JSON blob, no
second store, no schema change; legacy templates without `raw_samples`
fall back to the merged shape as one sample. Pure helpers in
`trajectory.py` do all the work: `motion_samples()` reads the sample set,
`rebuild_template()` re-merges an edited set through the **same**
`build_motion_template` path (no second algorithm) and bumps the
revision, `sample_spread()` / `template_diagnostics()` report read-only
inter-sample distance and a consistency label, and
`evaluate_motion_sample()` centralizes the recorder's only real
acceptance gates (point count + total movement) so the recorder and the
UI share one verdict — no fabricated quality metrics. Rebuilding happens
only on add/replace/delete, never per frame; the recorder itself runs on
the existing camera-worker → bridge-signal path (Qt never touches
frames). Rename cascades the new name across every reference —
`MotionController.rename_motion_gesture` updates the `motion_gestures`
row, all `rules.gesture` that equal the old name, and matching compound
steps — because the mapping model keys gestures by name;
`motion_gesture_dependents()` is the read-only inverse used for the
Mapped badge and the dependency-aware delete confirmation (it never
deletes an action/workflow). Disable simply drops the row from
`set_templates` (recognition can't resolve it) while keeping samples and
mappings intact.

## Custom gestures

Recording captures ~2 s of hand frames. Template = wrist-centered,
palm-scaled, handedness-folded landmark cloud (pose, fingertip-weighted
distance) + resampled normalized wrist trajectory (path). Static templates
match pose only; motion templates require pose AND path within tolerance.

## Threads

| Thread | Owner |
|---|---|
| camera | `CameraWorker` |
| context | `ContextDetector` |
| GUI | Qt main loop |

Vision + gesture processing runs synchronously on the camera thread (frame
→ hands → gesture → rule → action), which keeps ordering trivial and
latency minimal at 640×480/30fps.
