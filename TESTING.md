# Testing

## Automated suite

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q
```

596 tests, all green as of 2026-08-16.

| File | Covers |
|---|---|
| `test_core.py` | config round-trip, event bus isolation |
| `test_data.py` | schema, repository CRUD, cascade deletes |
| `test_static_gestures.py` | all 5 static classifiers on synthetic hands, pinch/fist/thumb-up disambiguation, scale invariance |
| `test_gesture_engine.py` | debounce, hold/release, cooldown, no repeat-fire, transitions, swipe detection through the engine |
| `test_rule_engine.py` | full precedence matrix (window > app > global > none), disabled rules/profiles, tie-breaks |
| `test_actions.py` | key/shortcut parsing, validation, sequence order + delays (input functions mocked) |
| `test_camera.py` | worker lifecycle, disconnect detection + reconnect, unopenable device (fake capture source) |
| `test_custom_gestures.py` | template build/match, tolerance, handedness folding, path matching |
| `test_profiles.py` | seeding idempotence, export/import round-trip |
| `test_runtime.py` | spec §22 scenarios S1–S6 end-to-end with mocked dispatch |
| `test_ui_smoke.py` | full MainWindow offscreen, live dashboard binding |
| `test_zones.py` | zone repo CRUD/normalization, zone rule matching, deleted-zone fail-closed |
| `test_swipes.py` | 4 directions (image + mirrored user coords), jitter/slow/diagonal/curved rejection, cooldown, pose independence, tracking-gap survival, sticky UI display, rule reach + action execution |
| `test_context_aware.py` | live context snapshot, per-app profile selection, override scenarios A–D, global fallback across apps, hot reload (add/modify mapping, no camera restart), app-switch/profile-edit safety, no-match |
| `test_gesture_settings.py` | per-gesture settings repo, confidence/cooldown overrides, swipe threshold configuration + detection effect, controller hot apply, multi-sample custom templates |
| `test_compound.py` | double gestures (held-pinch and bounce protection, min gap), sequences (timeout, max duration, strict vs lenient), hold (early release cancels), release steps, cooldown/once-per-sequence, hand identity (left/right/same), cancellation on motion-off and camera disconnect, no nesting, custom gestures as steps, repo validation, end-to-end compound → rule precedence → action, motion-off blocks compounds |
| `test_launch_action.py` | path/cwd/if_running validation, safe quote-aware argument parsing, direct no-shell process creation, focus-existing behavior (+fallback), executor failure detail, launch inside sequences, action CRUD + profile export/import round-trip, launch via every gesture kind through one code path, window > app > global launch precedence, compound + arbitration (launch exactly once, primitive suppressed), disabled rule / motion-off / emergency stop produce no launch, hot reload, safe-recognition-resolves-without-launching |
| `test_workflows.py` | workflow CRUD, step ordering/reordering, validation (nesting bans, delay bounds, missing actions), fresh action-reference resolution (hot reload), workflow-as-action through the executor, sequential execution with real delays, non-blocking start, failure propagation stops later steps, cancel mid-delay, motion-off/emergency-stop cancellation, duplicate-instance prevention (+ concurrent distinct workflows), open_url validation + default-browser (no shell), gesture→workflow global mapping, window > app > global workflow precedence, compound double-pinch → workflow with primitive suppressed, motion-off/disabled-rule → no start, safe recognition resolves without running, profile export/import round-trip with referenced step actions |
| `test_workflow_conditions.py` | smart waits: real/mocked process-exists, name normalization, mocked window/title conditions (wildcard + substring), condition validation (+ repo step validation, timeout bounds), check-never-raises, immediate continue when already true, continue right after condition flips mid-wait, timeout fails workflow and blocks later steps, modest polling rate, emergency-stop cancel mid-poll, non-blocking start, one-log-line-per-wait, mixed action/wait/delay steps, compound → smart workflow with arbitration, context-aware smart workflows, locked-workstation snapshot logging dampened to one warning per streak |
| `test_trajectory.py` | circle detector on deterministic synthetic paths: CW/CCW, small/large, oval, noisy, tilted; rejections (incomplete arc, straight line, fast swipe shape, jitter, too-small, zigzag, random walk, slow drift); cooldown; engine level: one circle = exactly one start+end event, point pose unaffected, no swipe from circle arcs, swipes still fire on straight motions, settings hot-apply, candidate trail events, gesture catalog; pipeline: circle → action with global/app/zone precedence, circle → workflow, pinch→circle compound, motion-off/emergency-stop blocking |
| `test_motion_gestures.py` | recorded trajectory gestures on deterministic synthetic shapes: multi-sample template build, scale/translation invariance, shape discrimination (Z vs triangle vs line), recognition across position/scale/noise/speed/reverse direction, rejections (wrong shape, 60%-drawn, cursor line, too small), cooldown + single fire, confidence threshold, disabled template, circle preserved alongside templates, engine one-event + swipes unaffected, hot reload keeps tuned built-ins, repo CRUD/validation, full pipeline (action, workflow, app profile, zone through one recorded gesture), compound step + emergency-stop blocking |
| `test_shutdown.py` | application lifecycle: idempotent shutdown (×3), shutdown during active fake-camera capture (threads joined, capture released), disarm + arbiter cleared, cancels a workflow mid-delay and mid-condition-wait (later steps never run), double stop, close→tray keeps running (offscreen), tray quit = full shutdown + tray icon removed + idempotent + bypasses tray-minimize, close quits fully when "Quit application" close behavior is set |
| `test_swipe_trajectory_arbitration.py` | swipe-vs-shape regression (cases A–N): pure swipes all four directions, swipes with templates loaded, curved swipe near a hook-like template still fires (template blocked by the straightness bar), circle-only / custom-only shapes still complete, shapes never fire on swipes, static+swipe interaction, swipe after a failed/abandoned motion candidate, candidate timeout leaves swipes available, no duplicate events, held-swipe emit-after-hold and loop-drop mechanics |
| `test_workflow2.py` | Workflow 2.0: description persistence + legacy-DB column migration, export/import carries description + trigger rule, any-gesture triggers (circle/custom/swipe/pinch parametrized), compound trigger + duplicate-instance guard, builder end-to-end offscreen (create with trigger → rule exists → gesture fires workflow; edit preselects + updates the same rule; deep validation rejects dead action references and blocks saving; duplicating a broken workflow warns instead of crashing), section duplicate/toggle/trigger labels, dashboard step checklist (✓/●/○, ■ cancelled, failure reason) |
| `test_uia_workflows.py` | UI-aware workflows (UIA mocked): element model defaults, pure matching (exact/case-insensitive/wildcard names, control types, case-sensitive automation ids, opt-in enabled/visible), criteria description, ui_element condition validate+check, malformed UI step rejection incl. ref-before-find ordering, find→store→focus→type→click success, Value-pattern fallback to focused keystrokes, missing-element timeout blocks later steps, stale-element and disabled-element safety (never act), wrong-application filter, cancellation during UI wait, shutdown during UI wait, per-run reference scoping, export/import of UI+wait steps (also fixes silent wait-step drop on export) |
| `test_workflow_logic.py` | conditional workflows: TRUE→THEN, FALSE→ELSE (not a failure), empty ELSE continues, ELSE-IF chains, ALL/ANY groups, two-deep nesting, condition verdict progress events, recursive validation (no conditions, empty THEN, bad mode/on_timeout, unknown condition kind, dead action in branch, bad UI ref in branch, branch-stored refs usable after), max nesting depth 5, wait-until-true, timeout→else vs timeout→fail, cancel during condition wait, Emergency Stop inside THEN and ELSE, evaluation error = failure, duplicate guard with branches, nested export/import round trip, old linear compatibility |
| `test_workflow_variables.py` | variables & data flow: substitution (values, types, undefined raises), full comparison-operator matrix (text/number/boolean incl. type-mismatch failures), condition validation, set_var literal/variable/clipboard/active-app/active-window, ui_read + `{var}` typing end-to-end, clipboard write with substitution, `{var}` in open_url, runtime-undefined variables fail with a clear message (branch-defined trick), run-local lifetime (no leak between runs), concurrent-run isolation, cancel/E-stop context destruction, static validation (names, sources, types, numeric literals, defined-before-use through branches, ui_read refs), export/import round trip + legacy compatibility |
| `test_studio2.py` | Gesture Studio 2.0 (calibration/diagnostics/safety; recognition unchanged): presets built only from existing keys (SAFE stricter than FAST, BALANCED = defaults, preview rows), reset restores swipe/circle detector defaults (regression found in live validation), read-only diagnostics snapshot shape (never fabricates confidence), threshold-by-kind (swipe rule-based=1.0, static=global, circle=detector sensitivity), cooldown counts down → COOLDOWN state, MATCH state from current gesture, neutral-before-retrigger blocks a repeated drawn shape until a neutral frame while NEVER suppressing swipes, gesture lock suppresses execution while recognition continues + publishes control.locked + gesture.blocked, Emergency-Stop overrides lock, disabled mapping never executes, require-neutral setter updates the engine, circle detector records direction/movement/closure diagnostics, and an offscreen smoke of the safety bar (lock/neutral) + enriched diagnostic dialog |
| `test_command_center.py` | Gesture Command Center (mappings are rules; no arbitration change): create/edit/duplicate/delete mapping, enable/disable (disabled rule resolves to none), gesture→action and gesture→workflow resolution, global vs app-specific precedence (app wins in-app, global elsewhere), compound/circle/recorded-motion mappings resolve through one path; conflict analyzer (identical-context duplicates → CONFLICT, cross-tier overlap → precedence info that explains the winner, disabled rules never conflict); delete-mapping keeps the workflow/action, edit-workflow preserves the mapping; bounded activity log reflects workflow completed/failed/cancelled, is capped at MAX, and subscribes only to rule.matched/workflow.done (never camera/vision/gesture); dangerous-workflow confirmation gate (defers then runs on accept, never runs on decline, Emergency-Stop voids a pending confirmation, setting-off runs normally, no double execution); Command Center page offscreen smoke (table rows, gesture-kind classification, preview chain, live feedback + activity via bridge, quick-assign dialog) |
| `test_reliability_soak.py` (+ `reliability_util.py`) | reliability / soak / concurrency (production behavior unchanged; drives real subsystems): launch→shutdown ×20 and start/stop ×15 with thread/gc drained to baseline (no monotonic growth); camera reconnect ×20 via a dropping-cap opener (single worker thread, DISCONNECTED/CONNECTED cycle, clean stop), camera disconnect clears the arbiter but never cancels a running workflow; 10k synthetic frames through the real engine (bounded start count, cooldown respected, trajectory deque stays time-pruned) + 10k gesture.events with the arbiter pending count always draining to 0; duplicate-run guard ×50, 30× concurrent distinct workflows with no variable leak, cancelling one workflow leaves the other; representative retry/repeat/if/repeat-until/variable/UI workflow ×40 with no object growth; recorder soak ×100 (every capture backend stopped, no recorder threads), recorder Emergency-Stop/shutdown/concurrent-prevention, **real DesktopCapture hook install+teardown ×16** (PeekMessage teardown, hooks always uninstalled); UI-Automation soak alternating healthy/stale targets (stale fails safely, never acts, never crashes); 500 context transitions (single context thread); Emergency Stop during delay/retry/repeat-until/nested loop (immediate cancel, worker drained); shutdown during delay/wait/retry/repeat (parametrized, clean exit, threads drained); DB integrity across 10 restarts (seed idempotent, workflows validate, no duplicate Global profile) |
| `test_hand_selection.py` | Hand selection / control routing (ingestion filter at the GestureEngine boundary; recognition/arbitration/mapping unchanged). Pins the PHYSICAL contract — physical LEFT → "left", physical RIGHT → "right" — using tracker labels declared as literals **inside the test file** (not imported from the production table), so a re-inversion fails loudly. Pure: normalize fallback (missing/invalid → both), physical-left/physical-right mapping, unknown handedness, eligibility (Both accepts either; Left accepts physical-left + rejects physical-right; Right accepts physical-right + rejects physical-left), `filter_hand_frame` returns the SAME object for "both"/all-eligible, and **no fallback** when only the non-selected hand is visible. Engine: both-mode fires for either physical hand (parametrized), left/right modes accept only their physical hand, two-hands-present only the selected contributes (moving eligible fires; moving non-selected with a stationary eligible decoy does not), a live setting change takes effect with no restart, `current_handedness` readout is physical. Controller/lifecycle: default "both" (backward compatible), invalid config falls back to both, `set_hand_control` persists and survives engine reset + motion-off + camera disconnect/reconnect, and round-trips through `Config.load()` |
| `test_cursor_stability.py` | Cursor stabilization at the output boundary (recognition untouched). Filter: first sample moves immediately (no startup lag), deadzone suppresses micro-movement, adaptive alpha is monotonic and bounded between MIN_ALPHA/MAX_ALPHA, oscillating stationary noise never drifts (bounded deviation, ends near centre), a fast sweep tracks the target within 25% (responsive), slow movement progresses without jerk, an isolated spike is rejected **and recovers on the next frame** (never freezes), reset re-seeds. Controller: anchor-relative mapping + gain scaling still monotonic in sensitivity, abort drops the anchor so the next session re-anchors at the new mouse position. End-to-end: a stationary noisy hand through the real controller keeps the cursor visually stable |
| `test_pinch_drag.py` | Pinch-and-hold drag (stateful cursor interaction reusing the existing pinch detector; every test also asserts down/up pairing). Unit: pose confidences are unambiguous, stable pinch starts **exactly one** drag after the start delay, release produces **exactly one** mouse-up, alternating pinch noise never starts or flaps a drag, hysteresis holds the drag through confidence dips, revoking `allowed` always releases, abort is idempotent. Real controller via `vision.hands`: pinch→drag→release, movement while pinched keeps the button held once and moves the cursor, noise produces no click spam, and the button is released on hand disappearance, camera disconnect, motion-off, E-stop, shutdown, Studio lock (which also blocks new drags), control-hand change, disarm-while-dragging and disabling the setting. Also: selected-hand filtering respected (non-selected hand cannot drag), DISARMED blocks drag until armed, the pinch mapping is suppressed while drag is enabled but runs normally when disabled, config defaults/back-compat (off by default, pre-feature config.json loads), invalid drag config falls back, and a full RUN→drag→interrupt→drag→shutdown lifecycle leaves no orphaned button |
| `test_cursor_sensitivity.py` | Cursor-control movement sensitivity (exposes the gain cursor control already used; no second cursor engine). Config: default is 2.2 (identical to the previous hard-coded behavior), `normalize_cursor_sensitivity` falls back for missing/invalid/NaN and clamps to 0.5–6.0, a pre-feature config.json still loads at 2.2, persistence round-trip. Movement (real controller, `move_to`/`cursor_pos`/screen-size patched): default gain reproduces the historical pixel delta exactly, low vs high scale movement **linearly in the gain**, live update without restart (2× gain → 2× travel, persisted), invalid value falls back. Interaction: the selected physical hand drives the cursor in left/right/both modes while the **non-selected hand produces no cursor movement at all** (driven through real open-palm recognition), sensitivity changes never alter eligibility, hand-selection changes never alter sensitivity, and Open-Palm recognition thresholds are untouched. Safety: gesture lock, Emergency Stop, motion-off and the **arming gate** each block cursor movement, and cursor control resumes once armed/unlocked |
| `test_arming.py` | Gesture arming/disarming safety gate (control-layer over the existing pipeline; recognition/arbitration/mapping unchanged). Unit (ArmingController): feature-off pass-through, enabled-without-gesture fails open, DISARMED blocks + arming gesture consumed + arms, ARMED allows normal + consumes repeated control gesture, disarm gesture disarms and fires the cancel-pending callback once, hold-to-arm (held long enough → ARMED), instantaneous gesture arms on completion, early release cancels, E-stop cancels ARMING, motion-off disarms only when configured, camera disconnect disarms while reconnect never auto-arms, DISARMING state exercised. Integration (real MotionController, events on the bus, execution via patched key_press): DISARMED blocks circle/swipe/static/motion/compound, arm-then-execute while the arming gesture never runs its own mapping (no duplicate execution on repeat), disarm gesture stops execution, Emergency Stop disarms, motion-off disarms and re-enabling motion does NOT auto-arm, a fresh controller starts DISARMED, and the confirmation gate is not bypassed while DISARMED. Offscreen UI smoke: Studio Arming&Safety controls persist config + show state, Command Center mirrors the state via the bus signal |
| `test_interference_matrix.py` | Gesture interference/priority matrix through the REAL GestureEngine (no faked detectors): static→swipe, swipe-with-template-loaded, circle-not-a-swipe, circle-vs-template (whichever shape drawn wins), template-vs-static, partial-motion→swipe, sloppy/fast/normal swipe, repeated swipes, neutral gate blocks repeat, neutral-cycle two fires, swipe-like template can't hijack a directional swipe, and the documented straight-first-stroke interaction (a hard synthetic Z emits a swipe from its straight top stroke AND completes the template — swipe never starved, completed shape never cancels an emitted swipe). Locks the "Known gesture interactions" documented in ARCHITECTURE.md |
| `test_studio21.py` | Gesture Studio 2.1 (motion-gesture template management; recognition/normalization/arbitration unchanged): template stores per-sample normalized trajectories (`raw_samples`) + `revision` with legacy fallback to the merged shape, `rebuild_template` re-merges an edited set through the existing build path (bumps revision, still recognizes a Z), `sample_spread`/`template_diagnostics` report inter-sample distance + consistency label, `evaluate_motion_sample` reports ONLY the recorder's real gates (movement-too-small / insufficient-data), a rebuilt template stays position/size/speed-invariant and still rejects wrong-shape/swipe-like/partial/too-small, **all four swipes still fire with a motion template loaded** (parametrized) and a partial motion draw then swipe still swipes, neutral-before-retrigger blocks a repeated drawn shape until a neutral frame then fires again, controller `motion_gesture_dependents` finds rules+compounds, `rename_motion_gesture` cascades to rules+compound steps and rejects duplicate/built-in names, disable unloads the detector while keeping samples, and an offscreen smoke of the manager dialog (sample list/preview, rebuild bumps revision, last-sample deletion protected, dependency-aware delete confirmation) |
| `test_workflow_recorder.py` | workflow recorder (FakeCapture backend; converter/materialize/execute real): recorder start/stop/pause/resume/cancel, concurrent-recorder prevention, Emergency-Stop and shutdown cancel an active recording; pure conversion — application launch → Launch + app_running wait, window transition → semantic window_title/window_exists wait (never raw ms), click → Find+Click, typing → Set variable + Type {var}, key press, UI Automation target (name/automation id/process, never coordinates) carried into Find; filtering — mouse movement/idle/unknown kinds ignored, Motion Gesture App excluded, redundant launch events consolidated, click→type consolidated to Find/Focus/Type; unique variable names, secure input recorded as [SECURE INPUT] with real characters never present, browser address-bar URL → Open URL; end-to-end — review-before-save (no autosave), malformed capture rejected while valid steps survive, recorded steps materialize + validate + are editable + execute through the real engine ({var} substituted, keys pressed), UI-reference re-validation fails safely on a stale target, export/import round trip, gesture assignment, duplicate-name protection |
| `test_workflow_retries.py` | retries & bounded loops: retry succeeds on first/second/final attempt, RETRY EXHAUSTED bounded + blocks later steps, inter-attempt delay, failure scoped to the block (earlier steps never rerun), on_fail continue/fallback, repeat ×1/×3/×100, invalid counts (0/negative/>100/over lowered settings bound), repeat-until immediately true (zero passes)/becomes true/iteration bound/time limit (both explicit TIMEOUT), retry-until with UI/app/variable conditions, nested retry + nested repeat + if-retry-repeat mix, 6-deep nesting rejected, Emergency Stop during retry/repeat/repeat-until + motion-off (no next iteration starts), duplicate guard while looping, variables persist across iterations, concurrent loop isolation, export/import round trip (until/fallback/bounds preserved), legacy linear/if workflows unchanged, per-attempt progress events without polling floods, malformed-loop validation matrix |
| `test_arbitration.py` | spec cases A–O: immediate primitives without compounds, held first pinch, compound-only double pinch (exactly once), timeout fallback, sequence suppression, wrong-gesture policy, longest-match win + shorter-on-expiry, context-aware delay (Chrome-only compound), emergency-stop/motion-off/camera-disconnect clearing, held-pinch single action, non-prefix zero latency, continuous never held, aborted-hold early release |

Tests isolate app data via `MGA_DATA_DIR`.

## Manual verification

Real input injection is not exercised by the unit suite (it would click and
type on the test machine). Verified via:

```powershell
.\.venv\Scripts\python.exe scripts\manual_action_test.py all
```

Each action prints a 3-second warning, executes for real, and reports OK/
FAILED.

## Scenario checklist (spec §22)

| # | Scenario | Automated | Manual |
|---|---|---|---|
| S1 | Desktop pinch → configured action | ✅ `test_s1…` | ✅ live |
| S2 | Excel pinch → Excel action | ✅ (same path as S3) | requires Excel |
| S3 | Chrome pinch → Chrome action | ✅ `test_s3…` | ✅ live |
| S4 | Global fallback | ✅ `test_s4…` | ✅ live |
| S5 | App override beats global | ✅ `test_s5…` | ✅ live |
| S6 | Motion OFF → no actions | ✅ `test_s6…` | ✅ live |
| S7 | Camera disconnect → app alive, UI shows it, recovery | ✅ `test_camera.py` | ✅ live (worker reconnects with backoff) |

## Performance (spec §19)

`scripts/perf_probe.py 12` with live camera, 2026-08-12, Windows 11:

| Metric | Value |
|---|---|
| Camera FPS | ~20 (device/exposure-limited) |
| Vision latency | mean 8.2 ms, p95 8.8 ms |
| Gesture engine | 0.05 ms |
| Rule resolution | 2 µs |
| SendInput injection | 31 µs |
| CPU | ~30% of one core |
| Working set (headless) | 120 MB |

## Context-aware mapping — live validation (2026-08-12)

`scripts/context_live_check.py` launches real applications, forces each
to the foreground and checks detection + resolution. Result: **ALL PASS**

| Foreground app | Detected process | Resolved profile | Resolved action |
|---|---|---|---|
| Notepad | `notepad.exe` | Global | Left click |
| Google Chrome | `chrome.exe` | Chrome | Demo Chrome action |
| Microsoft Excel | `excel.exe` | Excel | Demo Excel action |

Application identity is matched by **process name** (stable), with
window-title patterns as an optional extra condition — never title-only.
Mapping changes hot-reload on save; the camera is not restarted.

## Workflow engine — live validation (2026-08-13)

Real TEST A executed through the production chain (isolated data dir):
workflow "Open YouTube" = Launch Chrome → wait 1500 ms → open
https://youtube.com, started via the executor's `workflow` action.
Result: **PASS** — executor returned in 0 ms (non-blocking), duplicate
start rejected with "already running" during the delay, all three
progress events in order, status `completed`, real Chrome process
confirmed and YouTube opened.

## Smart workflow conditions — live validation (2026-08-14)

Four real scenarios through the production chain (spec §22–§24):

| Scenario | Result |
|---|---|
| A — Launch Chrome → wait `app_running chrome.exe` → open YouTube (no fixed delay) | PASS, completed in 0.19 s (condition already true → zero added latency) |
| B — Wait for `notepad.exe` while not running; Notepad spawned 2 s later | PASS, workflow continued 0.14 s after the process appeared (2.14 s total) |
| C — Wait for nonexistent app, 3 s timeout | PASS, failed at 3.02 s with "condition not satisfied before timeout"; later step never ran |
| D — Emergency stop during a 60 s wait | PASS, cancelled in <1 ms; later step never ran |

Condition check cost (100-call average): window/title ~0.25 ms,
process snapshot ~9 ms → ~4% of one core at 4 Hz only while a
process-wait is actively polling. Camera/gesture path untouched.

## Trajectory gestures — performance (2026-08-14)

Worst case (actively drawing, full multi-anchor analysis every frame):
0.011 ms/frame ≈ 0.1% of the 8.2 ms vision step. Idle hand (displacement
gate short-circuit): 0.006 ms. No extra vision processing; camera FPS
unaffected.

Real-camera circle checklist (perform after changes to the detector):
draw CW circle, CCW circle, imperfect circle → CIRCLE fires once each;
straight line, swipe, normal cursor motion → no circle, swipes still
fire as swipes.

Recorded motion gestures — synthetic separation (defaults tol 0.35,
threshold 0.55): same shape any position/scale/speed/direction ≥ 0.95
confidence; wrong shape ≤ 0.34; straight cursor motion 0.0; 60%-drawn
shape 0.29. Known approximate-circle trait: a cleanly drawn triangle
can read as a rough circle (radius spread within tolerance) — raise
circle sensitivity if undesired.

Real-camera motion-gesture checklist: record a shape (3 samples,
preview each), safe Test Recognition matches it, map to an action →
perform → fires once, map to a workflow → performs, switch apps for a
context-specific variant, then confirm circle + swipes still work.

## UI-aware workflows — live validation (2026-08-14)

Real Windows run through the production WorkflowEngine with real UI
Automation: Launch Notepad → wait for `notepad.exe` → wait for its
editable UIA element (found: Document / RichEditD2DPT "Text editor")
→ focus → type. Result: **PASS**, completed in 1.17 s; the typed text
"Hello from Motion Gesture App" was verified by reading the document
back through UIA.

## Conditional workflows — live validation (2026-08-14)

One workflow, both branches, real Windows + real UIA: IF
`notepad.exe` running THEN find editor → focus → type ELSE launch
Notepad → wait → find → focus → type. Run 1 (Notepad closed):
verdict "→ FALSE (ELSE branch)", Notepad launched and typed into.
Run 2 (Notepad running): verdict "→ TRUE (THEN branch)", typed into
the existing window (Value pattern replaces content — documented).
Both runs completed. **PASS**.

## Workflow variables — live validation (2026-08-14)

Real chain through the production engine: set `{greeting}` → launch
Notepad → find its editor via UIA → type `{greeting}!` → read the text
back into `{result_text}` → IF `{result_text}` contains "variables"
(verdict: TRUE/THEN) → set the real clipboard to
`copied: {result_text}`. Clipboard verified:
`copied: Hello from variables!`. **PASS**.

## Known limitations

- Swipe gestures are functional but may occasionally miss natural
  movements at approximately 20 FPS; further reliability tuning is
  deferred to a later gesture-quality pass.

## Live smoke procedure

1. `run.py --selftest` — subsystem init, camera probe, context snapshot.
2. `run.py` — GUI: camera connects, hand skeleton overlays, gesture +
   confidence update on the Dashboard.
3. Enable motion control, pinch → left click fires (watch Last Action).
4. EMERGENCY STOP → gestures produce nothing.
