# Motion Gesture App — Roadmap

Status legend: DONE / PARTIAL / NOT IMPLEMENTED / IN PROGRESS

## Phase 0 — Repository & Architecture — **DONE**
- `app/` package, venv, `requirements.txt`, `run.py`, config
  (`app/core/config.py`), rotating logging, thread-safe event bus, pytest,
  SQLite foundation.
- Acceptance met: `run.py --selftest` passes; suite green.

## Phase 1 — Camera Pipeline — **DONE**
- Enumeration (`app/camera/enumerate.py`), capture worker with status
  events, disconnect detection, automatic reconnect with backoff
  (`app/camera/capture.py`). Frames passed by reference (no copies).
- Tests: lifecycle, disconnect/recover, unopenable device (fake sources).
- Verified live: 2 physical cameras detected; connects and streams.

## Phase 2 — Hand Tracking — **DONE**
- MediaPipe Tasks HandLandmarker (VIDEO mode) wrapped in
  `app/vision/hand_tracker.py`; model auto-download; EMA smoothing;
  internal `HandFrame` type — MediaPipe types do not leak.
- Verified live: landmarks stream, skeleton overlay in UI.

## Phase 3 — Gesture Engine — **DONE**
- 5 static gestures + 4 swipes; debounce, cooldown, confidence threshold,
  hold/release state machine, transition handling. Emits events only.
- 9 state-machine/classifier test groups green.

## Phase 4 — Windows Input Control — **DONE**
- SendInput mouse/keyboard (`app/actions/input_win.py`), window ops
  (win32), volume/media via VK keys, launch/open actions.
- Unit tests for parsing/validation; SendInput verified live;
  `scripts/manual_action_test.py` for full manual pass.

## Phase 5 — Context Engine — **DONE**
- Foreground process/app/window title/cursor/screen polling → normalized
  `Context`; desktop detection. Verified live.

## Phase 6 — Rule Engine — **DONE**
- Deterministic precedence window > app > global > none; data-driven from
  SQLite; zone condition support. Full precedence test matrix green.

## Phase 7 — Profiles — **DONE**
- Global/application/custom kinds; create/edit/delete/duplicate/
  enable/disable/import/export; process-name association (lowercased,
  optional window pattern). Round-trip tests green.

## Phase 8 — Action Engine data layer — **DONE**
- 21 seeded reusable actions; named user actions; repository layer; rules
  reference stored actions.

## Phase 9 — Desktop UI — **DONE**
- PySide6: Dashboard (live preview + skeleton + real runtime state),
  Gestures, Profiles, Actions, Gesture Studio, Settings; tray with
  arm/disarm; EMERGENCY STOP. All dashboard values bound to live events —
  nothing fake. Offscreen smoke test green.

## Phase 10 — Gesture Studio — **DONE**
- Profile → gesture → action → window condition → continuous/cooldown →
  save/update/test/enable/disable/delete. No file editing needed.

## Phase 11 — Custom Gestures — **DONE**
- In-app recorder (countdown + 2 s capture), normalized pose+path
  templates with tolerance and min-confidence, enable/disable, mappable in
  Studio like built-ins. Matcher tests green.

## Phase 12 — Action Sequences — **DONE**
- `sequence` action type: ordered steps + per-step delays, nesting
  forbidden, length capped, editable in Actions page, reusable in rules.

## Phase 13 — Advanced Context — **DONE**
- Window-title conditions; cursor/screen in context; named screen zones:
  `ZoneRepo`, rule-engine zone matching (deleted zones fail closed), zone
  manager UI + per-rule zone condition in Gesture Studio.

## Phase 14 — Testing & Reliability — **DONE**
- 73 automated tests; spec §22 scenarios S1–S7 covered (see TESTING.md);
  camera failure recovery verified.

## Phase 15 — Performance Optimization — **DONE**
- Measured with `scripts/perf_probe.py` (live camera, 640×480):
  camera ~20 FPS (device-limited, not processing-limited), vision mean
  8.2 ms / p95 8.8 ms, gesture engine 0.05 ms, rule resolution 2 µs,
  SendInput 31 µs, ~30% of one core, 120 MB working set headless.
- End-to-end added latency after a frame arrives: ≈9 ms — no bottleneck to
  optimize; camera exposure dominates.

## Phase 16 — Packaging & Installer — **DONE** (installer script: PARTIAL)
- PyInstaller one-dir build (`MotionGestureApp.spec`) → standalone
  `dist\MotionGestureApp\MotionGestureApp.exe` (349 MB, no Python needed).
- Verified: packaged exe passes `--selftest` (exit 0, cameras found,
  context works) and runs the full GUI (model download, tracker start,
  camera connect, stable 22 s).
- Installer: `installer\MotionGestureApp.iss` ready for Inno Setup 6;
  not compiled here (Inno Setup not installed on the build machine).

## Milestone — Context-aware application-specific mappings — **DONE**
- Verified live with real applications (2026-08-12): context engine
  correctly identified Notepad, Google Chrome, and Microsoft Excel by
  process; the same pinch resolved to Global / Chrome-profile /
  Excel-profile actions respectively (`scripts/context_live_check.py`,
  ALL PASS).
- Precedence confirmed: window-specific > application > global > no
  action; disabled profiles/rules fall back correctly.
- Hot reload confirmed: Gesture Studio / Profiles saves call
  `MotionController.reload_rules()` — mappings apply immediately, camera
  untouched.
- 14 new automated tests (`tests/test_context_aware.py`).
- Known limitation (deferred): swipe gestures are functional but may
  occasionally miss natural movements at approximately 20 FPS; further
  reliability tuning is deferred to a later gesture-quality pass.

## Milestone — Gesture Quality & Gesture Studio UX — **DONE**
- Per-gesture sensitivity: `gesture_settings` table + repo; engine-level
  per-gesture confidence and cooldown overrides; swipe group thresholds
  (distance / speed / min-max duration / cooldown) — all hot-applied via
  the same reload path as mappings, no restart, camera untouched.
- Sensitivity UI: Gestures page → select gesture → "Sensitivity…"
  (double-click works too). Only meaningful parameters exposed per
  gesture type; one grouped dialog covers all four swipes; reset to
  defaults supported.
- Dashboard feedback: detection flash panel — gesture + confidence on
  every start event, plus resolved action on rule match; reverts after
  1.5 s. No animation overhead (one QLabel + single-shot timer).
- Gesture Studio guided flow: 1·Choose gesture → 2·Choose action →
  3·Choose where (profile / window / zone / continuous / cooldown) →
  4·Test → 5·Save. All previous capabilities retained.
- "Test recognition (safe)": live camera + skeleton in a dialog,
  detection indicator, confidence bar, and the action that WOULD run in
  the current context. Motion control force-disabled while open and
  restored on close; running the action requires an explicit button.
  Distinct from "Test action (runs for real)".
- Custom gesture creation flow: name first → guided recorder with
  countdown + progress bar → multi-sample recording (merged template,
  pose/path averaged) → trajectory preview sketch → save → optional
  jump to Gesture Studio with the gesture preselected.
- Tests: `tests/test_gesture_settings.py` (repo CRUD, per-gesture
  confidence/cooldown overrides, swipe threshold config + effect,
  controller hot apply, multi-sample templates) + UI feature assertions
  in the smoke test. Suite: 111 green, zero regressions.
- Swipe limitation unchanged and still documented (~20 FPS misses).

## Milestone — Compound & Temporal Gesture Engine — **DONE**
- New `CompoundEngine` (`app/gestures/compound.py`) layered after the
  primitive engine: consumes the normalized gesture.event stream (zero
  extra vision processing), one explicit state machine per definition.
- Data-driven model: `compound_gestures` table (steps JSON, max
  duration, step timeout, min gap, cooldown, hand, strict, enabled) +
  `CompoundGestureRepo` with validation. Nothing hard-coded.
- Step types: gesture (START), hold (START + still active after
  hold_ms; early release cancels), release (END; defaults to previous
  step's gesture). Doubles = repeated gesture steps + min/max gap.
  Simultaneous combos deliberately deferred; the step schema's `type`
  field leaves room without reshaping stored data.
- Hand identity: any / left / right / same (locked to first step).
- Cancellation: lazy expiry (stale partials can never fire), motion-off
  / emergency stop, camera disconnect. Held pinch can never double-fire
  (primitive layer emits one START per physical gesture). Optional
  strict mode aborts on unexpected gestures.
- First-class in rules: compound events publish on gesture.event with
  source="compound" — same profile/window precedence, Studio mapping,
  dashboard flash, logs. No nesting.
- UI (Gestures → Compound gestures): list with steps/timing/assigned
  actions; visual step builder (no JSON); record-a-sequence flow that
  proposes steps for confirmation; safe test dialog with per-step live
  progress (motion control force-disabled while open); map-now jump to
  Studio.
- Tests: `tests/test_compound.py` — 25 cases covering spec §19 plus
  repo validation and end-to-end compound → context precedence →
  action. Suite: 136 green, zero regressions.
- ~~Known limitation: primitives inside a compound still fire their own
  mappings~~ — RESOLVED by the Gesture Arbitration milestone below.

## Milestone — Gesture Arbitration / Conflict Resolution — **DONE**
- Resolves the prior limitation: "Primitive gestures inside compounds
  previously fired their own mappings."
- `GestureArbiter` (`app/runtime/arbiter.py`) between gesture events and
  action dispatch: non-prefix primitives run immediately (dict lookup,
  zero latency); prefix primitives hold their resolved action; compound
  completion cancels held components and runs exactly one action;
  expiry releases the held primitive deterministically (+60 ms slack).
- Longest-match: shorter completed compound held while a longer
  extension is viable; cancelled if the longer fires, released if it
  expires.
- Context-aware: relevance = rule engine resolves the compound name in
  the gesture-time context; Chrome-only compounds never delay Desktop.
- Cancellation: motion-off/emergency stop/camera disconnect drop all
  held actions; motion re-checked at release; early release when an
  aborted hold kills every justifying track. Continuous/cursor rules
  never held.
- Dashboard shows an amber "waiting for next gesture…" hint while held.
- Tests: `tests/test_arbitration.py` (spec §14 A–O, 15 cases). Suite:
  150 green, zero regressions. Compound-engine fix found during
  testing: completions now emit after all parallel tracks advance, so
  longer rivals are visible to arbitration.
- Trade-off documented: a lone opening primitive lags by its compound's
  gap window (default 700 ms).

## Milestone — Launch Application Action — **DONE**
- `launch_app` extended into a full first-class action: validated
  executable path, safe quote-aware arguments, optional working
  directory, `if_running` new/focus behavior (focus via the existing
  win32 window layer). Direct process creation only — no shell, ever.
- Application discovery (App Paths registry + System32 basics, zero
  hard-coded install paths) feeding a dedicated no-JSON action form
  with picker/Browse; auto display name.
- Test-action confirmation everywhere a launch could fire from the UI;
  safe Test Recognition shows "Would run: Launch X" without launching.
- Works through every gesture kind (one code path), full rule
  precedence, compounds + arbitration (launch exactly once, component
  primitives suppressed), sequences, import/export, hot reload.
- Verified real launch through the executor (Notepad spawned + closed);
  discovery found 37 real apps incl. Excel/Chrome/Calculator.
- Tests: `tests/test_launch_action.py` (19 cases). Suite: 169 green.
- Limitation: UWP/MSIX apps without an App Paths `.exe` are not
  supported (documented).

## Milestone — Workflow / Action Sequence Engine — **DONE**
- Generic reusable workflow system: `workflows` table (typed steps JSON,
  extensible for future step kinds/variables) + `WorkflowRepo`;
  steps reference existing named actions by id (no duplicated
  implementations) plus delay steps.
- Workflow-as-action: action type `workflow` `{workflow_id}` — rule
  engine/profiles/precedence/arbitration/Studio unchanged; mappable to
  every gesture kind including compounds.
- `WorkflowEngine` (`app/runtime/workflows.py`): daemon thread per run,
  event-driven (no polling, zero per-frame cost), cancellable
  `Event.wait` delays (UI/camera/E-stop never blocked), first failure
  stops the run with the failing step named, motion-off/emergency-stop/
  shutdown cancel instantly, one instance per workflow ("already
  running"), hot-reload (steps + referenced actions resolved fresh each
  run).
- `open_url` hardened: http/https-only validation, default browser via
  `webbrowser`, never a shell. Nesting banned both ways
  (workflow↛workflow, sequence↛workflow).
- UI: Workflows section on the Actions page — visual builder (add/edit/
  delete/reorder/duplicate steps, no JSON), confirm-before-run Test
  Workflow, auto-synced mappable action on save/rename/delete; Dashboard
  live progress row ("Open YouTube — 2/3: Wait 1500 ms", ✓/✕ result);
  workflow confirmations in Actions/Studio/Test Recognition run paths;
  safe Test Recognition still only displays the resolved workflow.
- Import/export: workflow actions serialize their step list with
  referenced action definitions by name; ids never exported;
  find-or-create on import. Backward compatible.
- Tests: `tests/test_workflows.py` (27). Suite: 196 green, zero
  regressions.
- Live TEST A PASS: real Chrome + YouTube through the production chain
  (see TESTING.md).

## Milestone — Smart Workflow Conditions & Waits — **DONE**
- New `wait` workflow step: poll a desktop condition until true or
  timeout. Conditions: application running, process exists, window
  exists (process and/or title), window title matches — same title
  matching semantics as window rules. Fixed Delay preserved.
- Condition engine `app/context/conditions.py` (Win32 confined to the
  context layer, strategy-style `check/validate/describe` API): Toolhelp
  process snapshots + EnumWindows, reusing the detector's process-name
  helper — no duplicate detection systems, no shell.
- Engine: checked immediately (already-true = zero latency), 250 ms
  cancellable polling, timeout → FAILED at that step, Emergency Stop /
  motion off cancel mid-poll instantly, one log line per wait (no
  polling spam). Non-blocking as before.
- Builder UI: "Wait for condition" step with condition picker, installed-
  app picker, process/title fields shown per condition, timeout in
  seconds. Dashboard shows "Waiting for application chrome.exe (up to
  10 s)" via the existing progress row.
- Reliability fix (spec §27): locked-workstation context snapshot
  failures now log one WARNING per streak, repeats at DEBUG, reset on
  recovery (was: hundreds of ERROR tracebacks).
- Tests: `tests/test_workflow_conditions.py` (18). Suite: 214 green.
- Live §22–§24 PASS: smart Chrome→YouTube with no fixed delay (0.19 s);
  mid-wait process appearance detected in 0.14 s; 3 s timeout fails
  cleanly; E-stop cancels a 60 s wait in <1 ms (see TESTING.md).
- Perf: window checks ~0.25 ms, process check ~9 ms at 4 Hz — only
  while a wait is active; camera/gesture pipeline untouched.

## Milestone — Trajectory Gestures: Circle + extensible engine — **DONE**
- `TrajectoryEngine` + `CircleDetector` (`app/gestures/trajectory.py`):
  runs on the already-tracked normalized index fingertip inside the
  gesture-engine frame step — no extra vision processing. Strategy list
  (`DETECTORS`) makes future shapes (triangle/zigzag/custom) plug-in
  additions; path representation matches the custom recorder for a
  future record-motion feature.
- Circle = approximate by design: CW/CCW, ovals, tilted, wobbly all
  accepted; gated on size, duration, roundness, closure, ≥300°
  consistent sweep and path-length sanity; confidence vs user
  sensitivity; one event per circle (history clear + cooldown).
- False-positive protection: displacement gate, tracking-gap reset,
  straight line/swipe/jitter/zigzag/incomplete-arc/random-walk all
  rejected (tested); candidate rotation evidence suppresses swipe fires
  during a circle while leaving straight swipes untouched.
- First-class event: works through arbitration, compounds
  (pinch→circle), profiles/window rules/zones, actions and workflows
  with zero rule-engine changes. Studio/Gestures UI list it; per-gesture
  sensitivity dialog (sensitivity, min size, max duration, cooldown);
  dashboard "Drawing motion…" hint + fingertip-trail preview overlay.
- Tests: `tests/test_trajectory.py` (24, deterministic synthetic
  trajectories). Suite: 238 green, zero regressions.
- Perf: 0.011 ms/frame worst case (~0.1% of vision step).

## Milestone — Custom Recorded Trajectory Gestures — **DONE**
- Users record their own drawn shapes: Gestures → "Create motion
  gesture…" — guided countdown/progress recorder captures the index
  fingertip (~2.5 s/sample, multi-sample, per-sample preview, min
  movement check); only normalized trajectories stored, never frames.
- Template pipeline in the existing trajectory engine
  (`build_motion_template`/`TemplateDetector`): resample to 32 points,
  centroid-centered, unit-RMS scaled, samples averaged; matching via
  point-wise distance minimized over ±25° rotation search + optional
  direction flip. Deterministic — no ML.
- Measured separation: same shape ≥0.95 conf across position/scale/
  speed/direction/noise; wrong shape ≤0.34; cursor line 0.0; 60%-drawn
  0.29 (default threshold 0.55 has wide margins both sides).
- Per-gesture tolerance/confidence/cooldown dialog; enable/disable/
  delete; safe Test Recognition; map-now flow into Studio; names join
  the global gesture namespace (dup-checked); hot reload preserves
  tuned built-in detectors.
- First-class events: actions, workflows, app profiles, window rules,
  zones, compound steps, arbitration, emergency stop — all verified via
  the standard pipeline with zero rule-engine changes.
- Tests: `tests/test_motion_gestures.py` (21) + UI smoke assertions.
  Suite: 259 green, zero regressions.

## Milestone — Shutdown / tray lifecycle — **DONE**
- `MotionController.shutdown()`: idempotent full teardown — disarm
  (cancels workflows mid-delay/mid-wait, drops arbiter holds, releases
  continuous actions), then stop (joins camera thread, closes the
  MediaPipe landmarker, joins the context poller). No force-kill
  anywhere.
- Tray "Quit Motion Gesture App" performs the TRUE shutdown (bypasses
  tray-minimize, removes the tray icon, quits the Qt loop); window X
  honors the new Settings → "When I close the window" choice
  (Minimize to tray — default, unchanged — or Quit the application).
  Quit guard makes repeated activations safe; main() exit path calls
  shutdown() again harmlessly and closes the DB.
- Config loader tolerates a BOM in hand-edited config.json (was:
  silent fallback to defaults).
- Tests: `tests/test_shutdown.py` (9, fake camera + stub tracker +
  offscreen window). Suite: 268 green.
- Real packaged-exe shutdown verified: WM_CLOSE with quit-on-close →
  "shutdown requested … complete" in 2.5 s, exit code 0, process gone.

## Fix — Swipe vs trajectory-gesture arbitration regression — **DONE**
- Root cause: candidate/near-match pre-suppression skipped swipe-
  detector updates on ordinary (wobbly/curved) swipes, and template
  detectors could fire on swipe-like paths (a hook-shaped template
  matched a curled swipe), resetting swipe history.
- Fix: swipe detector always fed; post-fire arbitration on accumulated
  tangent turn (<34° = instant emit, held ≤250 ms otherwise, dropped
  only on a proven loop ≥109° or a completed shape); templates barred
  from paths with net/path straightness ≥ 0.72. Deterministic, no ML,
  ~zero added cost.
- Tests: `tests/test_swipe_trajectory_arbitration.py` (17, cases A–N).
  Suite: 285 green — circle, custom shapes, compounds, statics all
  unchanged.

## Milestone — Workflow 2.0 (visual builder + triggers) — **DONE**
- One-stop builder: name, description, enabled, ordered steps
  (add/edit/delete/duplicate/reorder), and an in-editor trigger
  (any gesture incl. circle/recorded shapes/compounds + profile) stored
  as a normal rule — precedence and arbitration untouched, Studio still
  works.
- Deep validation on save and in the safe Test mode: structure,
  referenced actions exist + pass the executor validator, conditions
  well-formed; validated plan shown before an explicit run
  confirmation; broken workflows never saved silently.
- Workflow list 2.0: trigger • profile • step count • enabled/●running
  • last result (✓/✕/■), plus Duplicate and Enable/Disable; duplicating
  a broken workflow warns instead of crashing.
- Dashboard live step checklist (✓ done · ● current · ○ pending, capped
  window for long workflows) and distinct ■ cancelled state with
  failure reasons inline.
- Data: workflows.description column with additive migration for
  existing databases; description carried through profile
  export/import.
- Engine untouched — same non-blocking execution, cancellation,
  Emergency Stop, duplicate guard, smart waits.
- Tests: `tests/test_workflow2.py` (9). Suite: 294 green.

## Milestone — UI-aware workflows (Windows UI Automation) — **DONE**
- `app/context/uia.py`: normalized `UIElementInfo` model + bounded
  UIA-tree search (process/title/control-type/name/automation-id
  filters, node+depth budgets), native-pattern invoke (never blind
  coordinates), focus, Value-pattern typing, element-from-point.
  Per-call COM init — worker-thread safe, shutdown never blocked.
- New workflow steps: UI find / wait (poll @250 ms, timeout = FAILED
  with an honest "may not expose UI Automation" reason), click, focus,
  type — operating on run-local stored references that are re-validated
  before every interaction (stale/changed/disabled targets fail safely).
- Declarative refs only (store → ref), validated in order; no scripting
  language. `ui_element` condition kind added to the condition system.
- Builder: five UI step forms with app/window/type/name/id fields,
  read-only "Test Element", "Use inspected element"; **UI Inspector**
  (hover → accessible properties → send into a workflow step). Dashboard
  checklist labels cover UI steps.
- Export/import passes non-action steps through verbatim — also fixes a
  pre-existing bug where wait steps were silently dropped on export.
- `input_win.type_text`: unicode SendInput typing (single typing
  implementation, reused as the explicit UIA fallback).
- Tests: `tests/test_uia_workflows.py` (15, UIA fully mocked). Suite:
  309 green. Live: real Notepad launch → UIA find (Document/
  RichEditD2DPT) → focus → type, verified by UIA readback, 1.17 s.
- No background UI scanning: UIA runs only inside explicit workflow
  steps/tests/Inspector; camera and gesture pipelines untouched.

## Milestone — Workflow Logic 3.0 (safe conditional workflows) — **DONE**
- Declarative `if` step: condition group (ALL/ANY) over the single
  existing condition engine (app/process/window/title/ui_element incl.
  enabled/visible), optional wait-until-true with explicit timeout
  behavior (ELSE branch or fail), THEN/ELSE branches of normal steps,
  ELSE-IF via nesting (cap: 5 levels). No scripting, no expressions.
- One recursive executor (`_exec_steps`) — identical cancellation,
  Emergency Stop, duplicate-guard, UI-ref and shutdown semantics at
  every nesting level. FALSE = branch choice, never failure; verdicts
  published as single condition progress events.
- Recursive validation (branch actions/UI refs/conditions/depth) and
  recursive export/import (nested action steps → portable name refs);
  old linear workflows unchanged.
- Builder: "Add If/Else" with visual condition list, ALL/ANY, wait +
  timeout behavior, indented THEN/ELSE branch editors (nested If/Else
  supported), read-only "Test condition" with per-condition ✓/✕.
- Tests: `tests/test_workflow_logic.py` (16). Suite: 325 green.
- Live: one workflow ran both branches for real (Notepad closed →
  ELSE launches+types; running → THEN types into it), verified via UIA.

## Milestone — Workflow Variables & Data Flow — **DONE**
- Run-local variables (text/number/boolean) in the execution context —
  destroyed on completion/cancel/E-stop, isolated between concurrent
  runs. No scripting, no expressions, no eval.
- Steps: Set variable (literal / another variable / clipboard / active
  application / active window title), Read UI element text (accessible
  text of a re-validated stored element), Set clipboard. Clipboard
  touched only when a step runs.
- `{name}` substitution — data-only, limited to ui_type text,
  set_clipboard value, and open_url URLs (still executor-validated).
  Undefined variables fail with "Variable 'x' has not been defined."
- Conditions: `variable` kind with fixed typed operator set; type
  mismatch/undefined = FAILURE (distinct from FALSE).
- Validation: name syntax, sources, types, numeric literals,
  defined-before-use tracked statically through branches.
- UI: three "Data:" step forms, Variable-comparison condition editor,
  live read-only Variables panel during Test Workflow (values never in
  normal logs); dashboard checklist labels.
- Tests: `tests/test_workflow_variables.py` (15). Suite: 340 green.
- Live: type {var} into real Notepad via UIA → read back → IF contains
  → real clipboard set and verified.

## Milestone — Workflow Retries & Bounded Loops — **DONE**
- Three block steps, same recursive executor as If/Else: RETRY
  (attempts ≤ 20, delay, optional until-condition, explicit RETRY
  EXHAUSTED with on_fail fail/continue/fallback — only the block
  reruns, never earlier steps), REPEAT N TIMES (ceiling 100, settings
  can lower via `workflow_max_repeat`), REPEAT UNTIL CONDITION
  (pre-checked, iteration bound AND time limit both mandatory —
  explicit TIMEOUT, never silent).
- Safety: shared 5-level nesting cap across all block kinds, engine
  re-clamps every bound at run time, no while-true/unbounded/recursive
  workflows possible; loops share run-local vars/refs across
  iterations, runs stay isolated; Event-based waits (no CPU spinning),
  one progress event per attempt/iteration.
- Builder: "Control flow ▾" menu (If/Else, Retry, Repeat, Repeat
  until) at top level and inside every branch; also fixed a latent
  Workflow-2.0 regression where the builder's step list/layout was
  only constructed on one edit path (new-workflow dialog rendered
  empty).
- Recursive validation/export/import through steps + fallback; legacy
  workflows unchanged. Dashboard shows live attempt/iteration lines.
- Tests: `tests/test_workflow_retries.py` (33). Suite: 373 green.
- Live "Reliable YouTube Search": Chrome closed → launch → retry-wait
  → repeat-until YouTube window → UIA find/focus/type {search_text} →
  Enter → repeat-until results → variable IF → clipboard verified;
  plus already-open, fallback-path, and E-stop-in-retry/repeat-until
  scenarios (<1 ms cancel).

## Milestone — Workflow Recorder / Action Capture — **DONE**
- Convenience capture layer on top of the existing workflow/action/UIA
  systems — NO second execution engine. Actions → Workflows → Record
  Workflow; nothing is executed or saved automatically (Record → Review
  in the normal builder → Test → Save → assign gesture).
- Two halves: `app/context/capture.py` (`DesktopCapture` — dormant
  Win32 LL mouse/keyboard hooks + light foreground poll, own-PID
  filtered, injected input ignored, UIA target + secure detection,
  x64 handle signatures pinned) and `app/runtime/recorder.py`
  (`build_steps` — a pure events→steps converter, plus
  `WorkflowRecorder` orchestration cancelled by E-stop/motion-off/
  shutdown).
- Captures launches/transitions as SEMANTIC waits (app_running /
  window_title / window_exists — never raw ms), clicks/typing as UIA
  Find/Focus/Type (reference model reused, never coordinates), typed
  text as an editable variable + `{name}` substitution, browser
  address-bar URLs as Open URL, and password fields as `[SECURE INPUT]`
  (characters never buffered). Ignores movement/idle/own UI/background;
  consolidates click→focus→type and open→activate→appear.
- Recorded steps are the exact portable import shape → materialized via
  `ProfileManager.materialize_steps` (find-or-create actions) → opened
  in the existing `WorkflowBuilderDialog`; recorded workflows are
  identical to hand-built ones (edit/reorder/If-Else/Retry/Repeat/
  variables/test/export-import/trigger unchanged).
- Tests: `tests/test_workflow_recorder.py` (33). Suite: 406 green.
- Live: recorded workflow typed into real Notepad (verified by
  readback), variable changed to "computer vision" without re-recording
  and re-run, circle gesture assigned; real LL hooks install on x64,
  capture click+text+foreground events against live UIA, tear down
  cleanly.

## Milestone — Reliability / Soak / Concurrency Hardening — **DONE**
- No new features, no recognition/arbitration/semantics changes. Added
  soak/concurrency/lifecycle coverage and fixed reliability defects
  minimally.
- Root cause fixed: the recorder's Win32 hook thread blocked in
  `GetMessageW` and relied on `PostThreadMessageW` to wake — a message
  posted before the thread owned a queue could be lost, leaking the hook
  thread and installed hooks on rapid start/stop. Now the thread pumps
  with `PeekMessage` and re-checks a stop event each tick; `stop()` just
  sets the event (race-free, bounded teardown). `stop()` also never
  self-joins.
- Added `tests/reliability_util.py` (test/debug-only observability:
  thread counts, bus subscriptions, gc/RSS proxy, thread-drain waiter,
  no-monotonic-growth assert) and `tests/test_reliability_soak.py` (26
  tests): launch/shutdown ×20, camera reconnect ×20, 10k gesture frames
  + 10k arbiter events, workflow duplicate-guard/isolation/cancel,
  representative-workflow ×40, recorder soak ×100 + real hook teardown
  ×16, UI-Automation stale-target soak, 500 context switches, Emergency
  Stop and shutdown stress, DB integrity across 10 restarts.
- Verified: resources return to baseline after every cycle; no thread/
  subscription/hook/timer/state leaks; duplicate-run guard holds under
  stress; variables/UI refs never leak between runs; camera disconnect
  clears the arbiter but never cancels a running workflow.
- Flaky `test_sequential_execution_with_delay` hardened: its assertion
  compared two mocked-callback timestamps around a 60 ms delay and was
  sensitive to GC/scheduling jitter on either capture (production
  `Event.wait` is correct); bound widened to a fraction of the delay.
  25× isolated + 3× full-suite runs now clean.
- Full suite: 432 green (26 new). Packaged exe: 6 launch/shutdown cycles,
  all selftests exit 0, RSS flat (~264 MB steady state), threads steady
  (~80/process, MediaPipe+Qt+OpenCV pools), zero orphaned processes,
  clean exit each cycle.

## Milestone — Gesture Command Center / advanced gesture-to-workflow UX — **DONE**
- UX/orchestration layer over the existing rule engine — no second
  mapping or execution system, no arbitration/recognition change.
  New nav page "Command Center".
- Mapping table (icon/type/profile+context/assigned/enabled/conflict/
  last), one-dialog quick-assign (gesture → profile → action|workflow →
  save), execution-chain preview, safe Test Gesture (read-only resolve;
  runs only on explicit "Test full action"), live feedback + bounded
  activity list, and enable/disable/duplicate/edit-mapping/edit-workflow/
  safe-delete (delete removes only the association).
- Conflict analyzer (`app/rules/analyzer.py`, pure/read-only): flags
  same-context duplicates as CONFLICT and explains cross-tier precedence
  ("the Chrome mapping wins over the Global one") by mirroring the
  engine's tiers — it never changes resolution.
- Activity log (`app/runtime/activity.py`): in-memory ring (last 50) on
  rule.matched + workflow.done only (no camera-thread work, no DB log);
  publishes activity.changed for the bridge.
- Dangerous-workflow confirmation: additive `workflows.
  requires_confirmation` column (migrated) + global
  `confirm_dangerous_workflows` setting; the controller defers a flagged
  gesture-triggered workflow via workflow.confirm_request → GUI modal →
  resolve_confirmation, re-checking motion state. Emergency Stop /
  motion-off voids pending confirmations. Export/import carry the flag.
- Tests: `tests/test_command_center.py` (18). Suite: 450 green.
- Live: Circle → Global → "Open YouTube" resolved and ran for real
  (Chrome → retry-wait → YouTube, completed), activity logged; disabled
  mapping ignored; Chrome-specific vs Global precedence correct; analyzer
  explained it.

## Milestone — Gesture Studio 2.0: calibration, diagnostics & safety — **DONE**
- Diagnostics/safety layered on the EXISTING recognition engine — no
  second detector, no parallel state machine, no new mapping/execution.
- Read-only `GestureEngine.diagnostics()/threshold_for()/
  cooldown_remaining()` project existing state (states WAITING/TRACKING/
  CANDIDATE/MATCH/COOLDOWN/NO-TRACKING); confidence never fabricated.
  `CircleDetector.last` exposes direction/movement/closure/sweep.
- Studio "Test recognition (safe)" enriched: state, confidence vs
  threshold bar, cooldown, tracking, reject reason, context, circle
  diagnostics, bounded 50-entry in-memory detection history (no DB).
  Never executes; "Test full action" stays the only execution path.
- Presets (SAFE/BALANCED/FAST, `app/gestures/presets.py`) built only
  from existing tuning keys, with preview; Reset gesture / Reset ALL
  restore tuning defaults (no gesture/mapping/workflow deleted).
- Gesture Lock (`set_gestures_locked` + `control.locked`): recognition
  continues, execution suppressed (`_fire`/`_drive_cursor` gated,
  `gesture.blocked` published); Emergency Stop overrides.
- Neutral-before-retrigger (`require_neutral_before_retrigger`): a
  boolean gate on the existing lifecycle suppressing a repeated drawn
  shape until a neutral frame — swipes/statics untouched.
- Reliability fix found in live validation: `apply_gesture_settings`
  now restores swipe/circle detector defaults before applying an
  override, so Reset truly resets in-place-mutated detectors.
- Tests: `tests/test_studio2.py` (17). Suite: 467 green (swipe/compound/
  circle/motion regressions all still pass).
- Live: lock suppresses execution then arms; E-stop overrides;
  read-only diagnostics snapshot; presets apply + reset; neutral flag —
  all through the production controller.

## Milestone — Gesture Studio 2.1: motion-gesture recording quality & template management — **DONE**
- Layered on the EXISTING recognition/normalization path — no second
  engine, matcher, store or schema change. The template JSON now keeps
  the normalized per-sample trajectories (`raw_samples`) + a `revision`
  counter beside the merged `points`; legacy templates fall back to the
  merged shape as one sample.
- Pure `trajectory.py` helpers: `motion_samples`, `rebuild_template`
  (re-merges an edited set through the same `build_motion_template`),
  `sample_spread`/`template_diagnostics` (read-only spread + consistency
  label), and `evaluate_motion_sample` (only the recorder's real gates —
  no invented quality metrics; recorder now shares this verdict).
- Studio manager (`MotionGestureManagerDialog`, Gestures → "Manage
  samples…"): per-sample trajectory previews (point count/direction),
  merged-template preview, template diagnostics; add / replace (original
  kept until the new sample validates) / delete (last sample protected)
  → rebuild only on edit, never per frame. Library row shows samples ·
  Ready · Mapped/Unmapped · Enabled/Disabled.
- Rename cascades the new name to every reference —
  `MotionController.rename_motion_gesture` updates the row, all matching
  `rules.gesture`, and compound steps (mappings never break); rejects
  duplicate/built-in names. `motion_gesture_dependents` drives the Mapped
  badge and the dependency-aware delete confirmation (never deletes an
  action/workflow). Disable unloads the detector, keeps samples+mappings.
- Tests: `tests/test_studio21.py` (18). Suite: **485 green** — swipes
  (all four fire with a template loaded), circle, motion, compound and
  arbitration regressions all still pass.
- Live (production controller): load/diagnostics, rename cascade to
  rule+compound, sample delete/rebuild still recognizes, disable unloads
  detector while keeping samples. Packaged exe rebuilt, selftest exit 0
  (UI Automation OK), GUI launches, no orphan after shutdown.

## Milestone — System audit + documentation & non-behavioral perf hardening — **DONE**
- Audit-only outcome: recognition/arbitration/mapping/workflow semantics
  verified unchanged; NO behavioral code changed. Suite 485→ (see below)
  green; 14-case real-engine interference matrix 14/14.
- Docs: ARCHITECTURE.md gains a "Known gesture interactions" section —
  formalizes swipe-vs-shape precedence and the **straight-first-stroke**
  interaction (a template whose first stroke is long/fast/straight can
  emit a directional swipe before the template completes; intentional
  consequence of keeping the swipe detector continuously fed; suppressing
  it would need look-ahead that re-starves swipes → out of scope;
  mitigations: curved templates, neutral-before-retrigger, cooldowns).
- `rules/analyzer._tier` annotated as a READ-ONLY explanatory mirror of
  `RuleEngine` precedence — never a second resolver.
- Perf (non-behavioral): the Gesture Studio safety-bar context label is
  now event-driven (context.changed + rule.matched + showEvent) instead
  of a 700 ms poll — same text, fewer idle wakeups, zero recognition-
  timing impact. Dashboard `_refresh_poll` (150 ms) LEFT AS-IS on
  purpose: it reads camera FPS + live current-gesture/confidence decay,
  which have no per-frame bus signal — converting it would change
  displayed behavior.
- Regression lock: `tests/test_interference_matrix.py` commits the 14-case
  matrix through the real GestureEngine (no faked detectors).

### Technical debt (tracked, non-blocking)
- `app/ui/video_widget.py` mirrors + smooth-scales the full camera frame
  on the GUI thread at ~30 fps (preview-only, throttled, decoupled from
  capture). Heaviest GUI-thread work; left untouched — a rewrite risks
  preview behavior for no recognition benefit.
- `dashboard._refresh_poll` and a couple of `WorkflowEngine` per-step
  action re-reads are mild redundancies, all off the per-frame path.

## Milestone — Gesture arming / disarming safety system — **DONE**
- Closes the audit's one remaining functional limitation ("no arming
  gesture"). A CONTROL-layer state machine (`app/runtime/arming.py`,
  `ArmingController`) at the execution boundary — NOT a second recognizer,
  detector, arbiter, mapping or execution path. O(1) per event; no extra
  camera worker / recognition loop / per-frame SQLite / polling timer /
  MediaPipe call.
- States DISARMED → ARMING → ARMED → DISARMING. Gate lives in
  `_on_gesture` via `arming.allow(ev)`: control gestures (arming/disarm)
  are consumed (never run their mapping, never double-execute); nothing
  executes unless ARMED. Feature OFF by default and fail-open on
  misconfig → existing users unaffected, never locked out.
- Emergency Stop always disarms (and cancels ARMING); motion-off and
  camera-disconnect disarm when configured; reconnect never auto-arms;
  every app start is DISARMED. Leaving ARMED cancels pending arbiter
  holds / continuous actions / confirmations. Safety order centralized:
  E-STOP > LOCK/DISARMED > ARMED.
- Config: arming_enabled/arming_gesture/disarm_gesture/arm_hold_ms/
  disarm_on_motion_off/disarm_on_camera_disconnect (JSON, OFF default).
  `arming.state` → bus → QtBridge → Studio "Arming & safety" section
  (sole config site) + Command Center read-only indicator. No recognition,
  swipe, circle, motion-template, compound, arbitration, RuleEngine or
  workflow semantics changed.
- Tests: `tests/test_arming.py` (20 — unit state machine + real-controller
  integration + offscreen UI smoke). Suite 500→520 green; interference
  matrix 15/15 still green. Packaged exe rebuilt, selftest exit 0, GUI
  launch/exit clean, 0 orphans.

## Milestone — Hand selection / control routing — **DONE**
- One authoritative eligibility layer (`app/core/hand_select.py`, pure) at
  the tracker→engine boundary — NOT a second engine/detector/matcher/
  arbiter/mapping/execution path. Runs as the first line of
  `GestureEngine.on_hands`; `mode == "both"` returns the same frame object
  (strict no-op) → default behavior byte-for-byte unchanged.
- `hand_control` config: "left" | "right" | "both" (default "both");
  missing/invalid → "both" (`normalize_hand_control`). Applied live via
  `MotionController.set_hand_control` (no engine restart); persisted;
  survives camera reconnect and lifecycle/engine resets.
- Uses the tracker's HANDEDNESS classification, never screen X. Verified
  convention: our RAW non-mirrored feed inverts MediaPipe's selfie-based
  label, so `user_perspective()` swaps it once here (tracker "Left"→user
  right, "Right"→user left). `Hand.handedness` reaching recognition is
  unchanged (custom-gesture folding unaffected). Non-selected hand never
  produces events/candidates/swipes/templates/compounds; never falls back.
- UI: Gesture Studio "Control hand" combo + live "detected:" readout (via
  the existing `vision.hands` bridge signal). Recognition, swipe/circle/
  motion-template/compound arbitration, RuleEngine precedence, mappings,
  workflow, arming and E-stop semantics all unchanged.
- Tests: `tests/test_hand_selection.py` (16 — pure + engine ingestion +
  controller/lifecycle). Suite 520→536 green; interference matrix 15/15
  still green. Live validation through the production controller confirmed
  both/left/right routing with correct physical-hand mapping. Packaged exe
  rebuilt, selftest exit 0, GUI launch/exit clean, 0 orphans.

## Milestone — Physical handedness fix + cursor sensitivity — **DONE**
- **Part 1 (bug fix).** Reported: selecting Left controlled the physical
  RIGHT hand. Traced the whole pipeline; the ONLY handedness
  transformation anywhere is `hand_select._TO_USER` — camera publishes a
  raw frame, the tracker passes MediaPipe's `category_name` verbatim, and
  the mirrored preview is display-only. Root cause: that table applied the
  "input is assumed mirrored → swap" note a priori (conflating x-axis
  MIRRORING with anatomical HANDEDNESS), which contradicts the observed
  behavior here. Fix: the mapping is the identity, documented, single
  source of truth. No recognition/arbitration/RuleEngine/workflow/arming/
  E-stop change; `Hand.handedness` reaching recognition is untouched, so
  custom-gesture folding and compound hand-locking are unaffected.
  Tests now pin the PHYSICAL contract with literals declared in the test
  file, so a future re-inversion fails loudly (12 tests went red before the
  fix, green after).
- **Part 2 (feature).** Cursor-control movement sensitivity EXPOSES the
  gain `_drive_cursor` already used (hard-coded 2.2) — no second cursor
  engine, no second sensitivity system, and explicitly NOT Open-Palm
  recognition confidence. `Config.cursor_sensitivity` (default 2.2 =
  unchanged behavior, range 0.5–6.0, invalid/missing → default);
  `set_cursor_sensitivity()` applies live + persists; applied only inside
  `_drive_cursor`, never to raw landmark coordinates. UI: Settings →
  Cursor control → Sensitivity slider with a Low/Medium/High readout.
  Existing smoothing/anchor behavior preserved; no deadzone introduced.
- Tests: `tests/test_cursor_sensitivity.py` (17) + rewritten
  `tests/test_hand_selection.py` (21). Suite 536→**558** green;
  interference matrix 15/15; 114 gesture/arbitration/arming regression
  tests green. Packaged exe rebuilt, selftest exit 0, GUI launch/exit
  clean, 0 orphans.
- Remaining real-camera validation: confirm with a live camera that
  selecting Left responds to your physical left hand (and Right to right)
  — the automated tests pin the transformation, not the physical camera.

## Milestone — Cursor stability + pinch-and-hold drag — **DONE**
- New `app/runtime/cursor.py` owns the cursor OUTPUT boundary:
  `CursorController` (the anchor-relative mapping cursor control always
  used) + `CursorFilter` + `DragMachine`. Not a recognizer — it consumes
  positions and the EXISTING `static.pinch_confidence`, after hand
  selection and every safety gate. No second gesture engine/detector/
  arbitration/mapping/input pipeline.
- Stability: adaptive EMA on the cursor target (heavy when stationary,
  near-raw when fast → no added lag), 4 px micro-movement deadzone, and
  one-frame spike rejection that always recovers. Measured: typical
  landmark jitter now produces **zero** cursor movement; heavy jitter goes
  from a ~17 px raw swing to ~2 px. Landmark smoothing untouched (the
  filter works on the target, so nothing is filtered twice).
- Drag: IDLE → CANDIDATE → DRAGGING with hysteresis (start 0.6 held
  150 ms; release at the relaxed 0.35 for 2 frames). Strict press/release
  pairing plus an idempotent `abort()` wired into every interruption —
  hand loss, camera disconnect, motion-off, E-stop, lock, disarm,
  control-hand change, disabling the setting, shutdown. **Every
  button_down has exactly one button_up.**
- Safety: `_drag_allowed()` re-checks motion/lock/arming every frame (no
  bypass), and while enabled the pinch gesture is consumed at the
  controller gate so a drag never also fires its mapping.
- Config (backward compatible, opt-in): `cursor_drag_enabled` (default
  **off** — it holds a real mouse button), `cursor_drag_start_ms` (150),
  `cursor_drag_release` (0.35), each with invalid/missing fallbacks. UI:
  Settings → Cursor control → **Drag control** (keeps Sensitivity above).
- Defect found + fixed during the milestone: delegating `_drive_cursor`
  left the controller's legacy anchor in place, so two anchors each ate a
  frame and the first movement was swallowed. Caught by the previous
  milestone's cursor tests; the duplicate anchor state was removed so the
  cursor module is the single owner. Those tests then passed UNCHANGED,
  proving sensitivity semantics were preserved exactly.
- Tests: `tests/test_cursor_stability.py` (13) + `tests/test_pinch_drag.py`
  (25). Suite 558→**596** green; interference matrix 15/15; 135
  gesture/arbitration/arming/hand-selection regressions green. Real-engine
  validation: open-palm cursor, stable stationary cursor, pinch→drag→move
  →release, E-stop authority, balanced down/up. Packaged exe rebuilt,
  selftest exit 0, GUI launch/exit clean, 0 orphans.

## Phase 17 — Final QA — **DONE**
- Selftest + full 73-test suite + live GUI runs pass on Windows 11 /
  Python 3.11, both from source and from the packaged exe.
- End-to-end acceptance flow (spec §28) demonstrated: camera → hand →
  gesture → context → profile → rule → action → UI + logs.
