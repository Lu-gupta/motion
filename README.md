# Motion Gesture App

Control your Windows PC with programmable hand gestures detected through a
camera.

```text
Camera → Hand Tracking → Gesture Recognition → Context Detection
       → Rule Resolution → Action Execution → Windows PC
```

Mappings are context-aware and fully user-configurable: the same pinch can
right-click on the Desktop, run one shortcut in Excel and another in Chrome —
with a global fallback for everything else.

## Requirements

- Windows 10/11
- Python 3.11
- A webcam

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

The MediaPipe hand-landmark model (~8 MB) is downloaded automatically on
first run and cached under `%APPDATA%\MotionGestureApp\models`.

## Run

```powershell
.\.venv\Scripts\python.exe run.py
```

Headless sanity check (no GUI):

```powershell
.\.venv\Scripts\python.exe run.py --selftest
```

## First steps

1. Open the **Dashboard** — camera preview with live hand skeleton, plus the
   real runtime state (camera status, active app, current gesture,
   confidence, last action).
2. Motion control starts **OFF** for safety. Click **Enable Motion Control**
   to arm it.
3. The seeded **Global** profile maps: pinch → left click, fist → right
   click, swipes → volume/media, thumb up → play/pause. Change everything in
   **Gesture Studio**.
4. Create per-application profiles in **Profiles** (e.g. bind `excel.exe`),
   then add app-specific mappings in **Gesture Studio**. App rules override
   global rules; window-title conditions override both.
5. Record your own gestures in **Gestures → Record new custom gesture**.
6. **EMERGENCY STOP** on the Dashboard (or the tray menu) disarms everything
   instantly.
7. Closing the window minimizes to the tray by default (gestures keep
   running); change this under **Settings → When I close the window**.
   Tray → **Quit Motion Gesture App** always fully exits.

## Built-in gestures

pinch, open_palm, fist, point, thumb_up, swipe_left, swipe_right, swipe_up,
swipe_down, **circle** — plus recorded custom gestures (static poses or
motion paths).

### Trajectory gestures

**Record your own shapes**: Gestures → "Create motion gesture…" — name
it, then draw the shape in the air with your index fingertip (guided
countdown + progress; 3+ samples recommended, each previewed). Samples
are normalized (position/size/speed don't matter — the shape does) and
merged into one template; matching tolerates natural wobble, slight
rotation, and drawing the shape in reverse. The saved gesture is
first-class: map it in Gesture Studio to any action or workflow, per
application, window, or zone — exactly like built-ins. Test it safely
via "Test recognition (safe)"; tune matching tolerance / confidence /
cooldown per gesture. Only the normalized trajectory is stored — never
camera frames.

**Manage samples & templates** (Studio 2.1): Gestures → select a motion
gesture → "Manage samples…" (or double-click). The library row shows
sample count · Ready · Mapped/Unmapped · Enabled/Disabled. Inside the
manager you can review each sample's trajectory preview (point count,
direction), see the merged template the recognizer actually uses, and
the template diagnostics (sample count, resampled points, inter-sample
spread → consistent/varied/inconsistent, revision). Edit the sample set —
**+ Record another sample**, **Replace sample** (the original is kept
until a new one is captured and validated), **Delete sample** (the last
remaining sample is protected — the gesture is never silently emptied) —
and the template is rebuilt through the same merge path only on edit,
never per frame. **Rename** validates the name, blocks duplicates across
every gesture kind, and cascades the new name to all Command Center
mappings and compound steps, so nothing breaks. **Delete** shows exactly
which profiles/actions and compounds depend on the gesture first, and
never deletes an action or workflow. **Disable** keeps the samples,
template and mappings but stops recognition; **Delete** removes only the
gesture. Recording quality is reported from the recorder's real gates
only — a rejected sample says why (movement too small / insufficient
trajectory data), no invented scores.

**Circle**: draw a circular motion in the air with your index fingertip —
clockwise or counter-clockwise, any reasonable size. Recognition is
deliberately approximate: slightly oval, tilted or wobbly circles count;
straight lines, swipes, jitter and incomplete arcs do not. While you draw,
the preview shows a fingertip trail and the dashboard hints
"Drawing motion…"; one drawn circle fires exactly one `circle` event,
mappable in Gesture Studio to any action or workflow like every other
gesture. Tune it in Gestures → circle → Sensitivity (recognition
sensitivity, minimum size, maximum duration, cooldown).

## Compound gestures

Combine any gestures (built-in or custom) into higher-level temporal
gestures in **Gestures → Compound gestures**: sequences
(`fist → open_palm`), double taps (`pinch → pinch` with min/max gap),
holds (`pinch held 1000 ms`) and releases — built visually (or recorded:
perform the sequence once and the app proposes the steps), tested safely,
and mapped to actions exactly like any other gesture, including
per-application overrides.

Arbitration is automatic: if `pinch` and `double pinch` are both mapped,
a double pinch runs only the double-pinch action (the single-pinch action
waits out the gap window and only fires for a lone pinch).

## Built-in action types

Mouse (move/click/double/middle/drag/scroll), keyboard (key press,
shortcut), window (minimize/maximize/restore/close/switch), system (volume,
media keys, open URL, open folder), **launch application**, action
sequences with delays, **multi-step workflows**, and continuous cursor
control ("Cursor follows hand").

### Launch Application

Map any gesture (primitive, swipe, custom, or compound) to launch a
Windows program. In **Actions → New Action → Launch application** pick an
installed app from the discovered list (App Paths registry + system
basics — nothing hard-coded), or browse to any `.exe`; optional
arguments and working directory; choose "Launch a new instance" or
"Focus the existing window if open". Paths are validated, processes are
created directly (never through a shell), and testing an action always
asks before actually launching. UWP/MSIX-only apps without a real
`.exe` path are not supported.

### Workflows (multi-step)

Map one gesture to a whole flow. Example:

```text
DOUBLE PINCH  →  Workflow "Open YouTube"
                 1. Launch Google Chrome
                 2. Wait for condition: application Chrome is running
                 3. Open https://youtube.com
```

Build it in **Actions → Workflows → New Workflow**: name + optional
description, pick a **trigger gesture and profile right in the editor**
(any gesture — statics, swipes, circle, recorded shapes, compounds; the
trigger is a normal rule, so Studio and precedence work as always), add
steps that run your existing named actions (launch, open URL, keys,
mouse — anything), insert fixed delays or **smart waits** between them,
reorder/duplicate freely. **Test Workflow** validates everything
(missing actions, bad parameters, malformed conditions), shows the
ordered plan, and only runs after an explicit confirmation. The list
shows each workflow's trigger, profile, step count, enabled/running
state and last result; duplicate a workflow with one click. The
Dashboard shows a live step checklist while a workflow runs
(✓ done · ● current · ○ pending), and ✓/✕/■ on
completion/failure/cancellation.

Smart wait conditions (Add Step → Wait for condition):

- **Application is running** — pick an installed app or type a process
  name (`chrome.exe`)
- **Process exists** — exact process name
- **A window of the application exists** — process and/or title pattern
- **Window title matches** — pattern like `*YouTube*` (same matching as
  window rules: wildcards, or plain substring)

Every condition has a timeout (default 10 s). The wait continues the
instant the condition becomes true; if the timeout passes first the
workflow **fails** at that step ("condition not satisfied before
timeout") and later steps never run. Fixed Delay remains available for
intentional pauses. Saving creates a
mappable action with the workflow's name, so it works everywhere a
normal action works — global, per-application and window-specific rules,
any gesture kind including compounds.

Workflow behavior:

- Runs in the background — the camera, gestures, UI and EMERGENCY STOP
  are never blocked, even during long waits.
- A failing step stops the workflow and reports which step failed.
- EMERGENCY STOP / disabling motion control cancels a running workflow
  instantly (pending steps never fire).
- The same workflow cannot start twice concurrently ("already running").
- "Test Workflow" always asks for confirmation first; safe Test
  Recognition only shows "Would run: <workflow>" without running it.
- The Dashboard shows live progress ("Open YouTube — 2/3: Wait 1500 ms").

The **Open URL** action validates its URL (http/https only) and opens it
with the default browser — never through a shell.

### Conditional workflows (If / Else)

Workflows can branch — declaratively, no scripting: **Add If/Else** in
the builder, add one or more conditions (application running, process,
window, window title, UI element exists/enabled/visible), choose
**ALL/ANY**, optionally wait up to N seconds for the conditions to
become true (with explicit timeout behavior: use the ELSE branch, or
fail the workflow), then fill the THEN and ELSE branches with normal
steps — actions, delays, waits, UI automation, even nested If/Else
(up to 5 levels). FALSE is a branch choice, never a failure.
"Test condition" evaluates read-only with a per-condition ✓/✕
breakdown; the Dashboard shows each verdict live
("IF … → TRUE (THEN branch)"). Conditions are evaluated only when the
workflow reaches them.

Example — one gesture, adaptive behavior:

```text
DOUBLE PINCH → IF chrome.exe is running
                 THEN  focus it and search
                 ELSE  launch Chrome, wait for it, then search
```

## Gesture Studio 2.0 — calibration, diagnostics & safety

The Gestures page carries a **Studio safety & calibration** bar and a
richer **Test recognition (safe)** diagnostic mode — all built on the
existing recognition engine (no second detector, no new state machine).

- **Live recognition diagnostics** — Safe Test Recognition shows the
  gesture's state (WAITING / TRACKING / CANDIDATE / MATCH / COOLDOWN /
  NO TRACKING), current confidence vs the required threshold (with a
  bar), cooldown remaining, tracking quality, the resolved mapping, the
  active app/window context, and — for the circle detector — direction
  (CW/CCW), movement, closure and sweep. It **never executes** the
  mapping; "Test full action" remains the only way to run it. A bounded
  in-memory **detection history** (latest 50, clearable) records
  MATCH/COOLDOWN transitions — no database, no per-frame logging. Values
  a detector doesn't expose are shown as "—", never fabricated.
- **Presets** — SAFE / BALANCED / FAST, each built only from the
  existing per-gesture tuning parameters; a preview shows the values
  before you apply. **Reset selected gesture** and **Reset ALL tuning**
  restore defaults (tuning only — never a gesture, mapping, workflow,
  action or profile). Reset now genuinely returns the swipe/circle
  detectors to their built-in defaults.
- **Gesture Lock** — a global **● GESTURES ARMED / 🔒 GESTURES LOCKED**
  toggle: while locked, the camera keeps tracking and recognition keeps
  running (diagnostics still update) but **no mapping executes**. It
  reuses the controller's state; Emergency Stop / motion-off always
  overrides.
- **Require neutral state before re-trigger** (Settings, or the Studio
  bar) — after a drawn shape (circle / recorded motion) fires, the same
  shape will not fire again until the hand returns to a neutral state
  (open palm or out of view). Prevents a lingering pose re-triggering a
  workflow repeatedly. Off by default; it never affects swipes.

## Control hand (left / right / both)

Choose which physical hand drives gestures — **Control hand** in the
Gesture Studio safety bar (Both / Left / Right; **Both** by default, so
existing setups are unchanged). It is a single eligibility filter at the
tracker→engine boundary, not a second recognizer.

- **Both** — current behavior (either hand).
- **Left / Right** — only the selected physical hand can produce gesture
  events, candidates, swipes, motion-template matches, compound inputs or
  executions. The other hand may be visible to the camera but contributes
  nothing, and there is **never** an automatic fall-back to it — if the
  selected hand isn't present, there is simply no control gesture.
- **Left / Right mean YOUR physical hand.** It uses the tracker's
  **handedness classification**, never screen X position, and the mirrored
  preview you see is display-only — it does not affect which hand is
  selected. A live **detected:** readout next to the setting shows which
  physical hand the tracker currently sees, so you can confirm at a glance.
- Applies **live** (no engine restart) and the preference **survives**
  camera disconnect/reconnect and lifecycle resets. Safety is unaffected —
  arming/disarming and Emergency Stop still gate everything.

## Cursor stability & pinch drag

**Stability.** Cursor movement passes through a stabilizer at the output
boundary, so a resting hand gives a resting cursor:

- **Adaptive smoothing** — the further the cursor target moved this frame,
  the less it is smoothed. Stationary/slow hand → heavy smoothing; fast
  hand → almost raw responsiveness, so there is no added lag when you
  sweep across the screen.
- **Micro-movement deadzone** — tiny hand-tracking noise never reaches the
  OS cursor. With typical jitter the cursor does not move *at all*; with
  heavy jitter a ~17 px raw wobble becomes ~2 px.
- **Spike rejection** — a single implausible tracking jump is ignored, and
  the cursor recovers on the next frame (it can never freeze).

Nothing here changes gesture recognition, and landmark smoothing is
unchanged — the filter deliberately works on the cursor target, not the
landmarks, so nothing is smoothed twice.

**Pinch drag** (Settings → Cursor control → Drag control, **off by
default**). Enable it to drag files, windows and selections:

```
move hand (open palm)  →  cursor moves
pinch thumb + index    →  left button pressed and HELD
move while pinched     →  the item follows
release the pinch      →  left button released
```

- Reuses the existing pinch detector — no new gesture, no new detector.
- **Start delay** (default 150 ms) and **release tolerance** (default
  0.35) give hysteresis: a noisy frame can neither start a drag nor drop
  one mid-move, so you never get click-spam.
- Obeys the **selected control hand**, the **arming gate**, the Studio
  **lock**, motion-off and **Emergency Stop** — all of which also release
  the button immediately if a drag is in progress.
- The button is **always released** on hand loss, camera disconnect,
  disarm, lock, motion-off, E-stop, control-hand change, disabling the
  setting, and shutdown. A press never outlives its release.
- While enabled, the pinch gesture is **dedicated to dragging** — its
  mapped action does not run, so a drag can't also fire a click.

## Cursor control sensitivity

Settings → **Cursor control → Sensitivity** — how far the cursor travels
for a given hand movement while a cursor-control gesture (e.g. Open Palm)
is held. Slider from 0.5× to 6.0×, **default 2.2×** (exactly the previous
built-in behavior); the label shows Low / Medium / High. It applies live
on Apply and is saved to your config.

It scales **only** cursor movement. It does not change how Open Palm (or
any gesture) is recognized, does not touch swipe/circle/motion/compound
recognition, hand selection, arming/disarming or Emergency Stop — and
cursor control still respects all of those gates.

## Gesture arming / disarming safety

An optional **Arming & safety** layer (Gestures page) adds an explicit
arm/disarm gate over the whole pipeline. It is a control-layer state
machine (DISARMED → ARMING → ARMED → DISARMING) at the execution
boundary — **not** a second recognizer: gestures are recognized exactly
as before, but nothing executes unless the system is **ARMED**.

- **OFF by default** — existing behavior is unchanged until you turn it
  on. When ON, the app starts **DISARMED**: gestures are still recognized
  (diagnostics work) but no mapping, cursor or workflow runs.
- **Arm** by performing the configured **arming gesture** (any existing
  gesture — static, swipe, circle, recorded motion or compound). An
  optional **arm hold** requires holding it briefly. Optionally set a
  separate **disarm gesture**.
- **Control gestures are consumed** — while arming is ON, the arming /
  disarm gesture never runs its own mapped action (so a Fist used to arm
  never also triggers Fist → Play/Pause), and repeating it never
  double-executes. No duplicate recognition path.
- **Automatic disarm** — **Emergency Stop always disarms** (highest
  priority, also cancels an in-progress arming); motion-control-off and
  camera-disconnect disarm too (each configurable). A camera **reconnect
  never auto-arms**, and every app start is **DISARMED**.
- Safety order is centralized: **Emergency Stop > Lock / Disarmed >
  Armed**. The Command Center shows the live state (🔒 DISARMED / ⏳
  ARMING… / 🟢 ARMED); configuration lives only in Gesture Studio.

## Gesture Command Center

A single screen (nav → **Command Center**) that shows every
gesture → trigger → context → action/workflow → status at a glance and
lets you manage them without hopping between pages. It is a UX layer over
the existing rule engine — mappings are ordinary rules, resolved and
arbitrated exactly as before.

- **Mapping table** — icon + gesture, type (static / swipe / circle /
  recorded motion / compound), profile & context, the assigned action or
  workflow, enabled state, a **conflict indicator**, and last-triggered
  time.
- **Assign** — one dialog: gesture → profile → Action *or* Workflow →
  Save (optionally a window pattern). No multi-screen detour.
- **Execution-chain preview** — select a mapping to see its flow
  (Gesture → Recognized → Profile → Workflow → each step → Complete).
  Visualization only; nothing runs.
- **Conflict / precedence analyzer** — flags genuine conflicts (the same
  gesture mapped twice in the same context) and *explains* precedence for
  overlaps ("in Chrome the Chrome mapping wins over the Global one") —
  purely descriptive, the arbitration engine is untouched.
- **Test Gesture** — perform a gesture and see Detected / Confidence /
  Resolved profile / Resolved mapping, read-only; it only runs the action
  if you explicitly click **Test full action** (with a confirm).
- **Live feedback + recent activity** — the detected gesture and live
  workflow progress/outcome show inline; a bounded (in-memory, last 50)
  activity list shows recent `gesture → target → completed/failed/
  cancelled`. No database log, no camera-thread work.
- **Enable/disable, duplicate, edit mapping, edit workflow, delete** —
  disable keeps a mapping stored but idle; duplicate clones only the
  mapping (never the workflow/action); delete removes only the
  association (the workflow, action, gesture and profile are kept);
  "Edit workflow" opens the normal builder.

**Dangerous-workflow confirmation** — a workflow can be flagged *requires
confirmation* (in the workflow builder). When the global **Settings →
"Require confirmation for … workflows"** is on, a gesture that resolves to
a flagged workflow prompts before running; off, it runs normally.
Emergency Stop always overrides — a pending confirmation is voided the
instant motion control goes off.

### Record a workflow (action capture)

Instead of building a workflow step by step, **record one**: Actions →
Workflows → **Record Workflow**, name it, press **Start Recording**, then
perform the actions on your desktop — launch an app, click a field, type,
press Enter. Press **Stop && Review** and the recorder converts what you
did into an ordinary, fully-editable workflow, then opens it in the normal
builder for review, testing, trigger assignment and saving. Nothing is
ever executed or saved automatically.

It captures *meaning*, not mouse coordinates:

- **Application launches / window transitions** become semantic waits
  ("wait for Chrome", "wait for the *YouTube* window") — never raw
  millisecond timing.
- **Clicks and typing** on an app's accessible controls become UI
  Automation steps (Find → Focus → Type) using the same reference model
  as hand-built UI steps — re-validated before every run, never blind
  coordinates.
- **Typed text** becomes an editable **variable** (`search_text = "AI
  automation"`) referenced as `{search_text}` — so you can change the
  text later without re-recording.
- A typed **URL in a browser address bar** becomes an **Open URL** step.
- **Password / secure fields** are recorded as `[SECURE INPUT]` — the
  characters are never captured; you fill in the real value in review.

It deliberately ignores mouse movement, idle time, background noise and
the Motion Gesture App's own window, and consolidates redundant events
(click + focus + type ⇒ one Find/Focus/Type). **Pause/Resume** stops and
resumes capture; Emergency Stop / motion-off cancels the recording
instantly. A recorded workflow is identical to a hand-built one — edit,
reorder, add If/Else, Retry, Repeat, variables, test, export/import, and
assign any gesture, all as usual.

### Retries & bounded loops (control flow)

Workflows survive normal desktop timing without scripting. Under
**Control flow ▾** in the builder (next to If/Else, also inside any
branch):

- **Retry** — try a block of steps up to N attempts (max 20) with a
  delay between attempts. A failing step fails only the *attempt*, and
  only the retry block reruns — steps before it never re-execute.
  Optionally require a condition ("succeed only when …") for an attempt
  to count. When every attempt fails the result is an explicit
  **RETRY EXHAUSTED**, and you choose what happens: fail the workflow
  (default), continue, or run fallback steps and then continue.
- **Repeat N times** — run a block a fixed number of times. Hard
  ceiling 100; Settings → "Max workflow repeat iterations" can lower
  it further. A failing step fails the workflow exactly as usual.
- **Repeat until condition** — the condition group (ALL/ANY, the same
  conditions as everywhere else: application/process/window/title/UI
  element/variable) is checked *before* each pass — already true means
  zero passes. Both an iteration bound (≤100) **and** a time limit are
  mandatory; hitting either with the condition still false is an
  explicit **TIMEOUT** failure, never a silent continue.

Loops share the run's variables and stored UI elements (nothing resets
between iterations); separate runs stay isolated. Blocks nest with
If/Else up to the same 5-level limit. Emergency Stop / motion-off
cancels a loop instantly — mid-delay, mid-wait, mid-attempt — and no
further iteration ever starts. The Dashboard and Test Workflow show
live loop progress ("RETRY attempt 2/3", "REPEAT UNTIL iteration
3/10 …"); infinite loops, unbounded repeats and workflow recursion are
rejected at validation time.

Example:

```text
DOUBLE PINCH → Reliable YouTube Search
  1. Set {search_text} = "AI automation"
  2. Launch Chrome
  3. RETRY ×3: wait for Chrome
  4. Open youtube.com
  5. REPEAT UNTIL window "*YouTube*" (≤10 iterations, ≤15 s)
  6. RETRY ×3: find + focus the Search box
  7. Type {search_text} · Enter
  8. IF {result_text} contains "AI" → THEN … ELSE …
```

### Workflow variables (data flow)

Workflows can carry data — declaratively, still no scripting. New
"Data:" steps: **Set variable** (text/number/yes-no value, another
variable, clipboard text, active application, or active window title),
**Read UI element text** (accessible text of a stored element → a
variable), **Set clipboard** (with substitution). Reference variables
as `{name}` in UI typing, clipboard text, and Open URL actions
(`https://…?q={search_text}`); everything else is untouched, and
substitution is always data-only. Conditions gained **Variable
comparison** (text equals/contains/starts/ends/empty, number
=/≠/</>/≤/≥, yes-no is true/false) — so a workflow can read a result
and branch on it. Variables are **run-local**: created when the run
starts, destroyed on completion/cancel/Emergency Stop, never shared
between concurrent runs. Validation rejects invalid names, wrong
types, and variables used before any step could define them; a live
read-only **Variables panel** opens during Test Workflow (values are
shown there, never in normal logs).

### UI-aware workflow steps (Windows UI Automation)

Workflows can optionally interact with an application's accessible UI —
no coordinates, no OCR, no vision. Step types under "UI:" in the step
picker:

- **Find / Wait for element** — poll for an element by application,
  window title, control type, name and/or automation id (timeout,
  cancellable); store it under a name like `search_box`
- **Click element** — via the element's native accessibility pattern,
  never blind coordinates
- **Focus element**, **Type text into element** — text set via the UIA
  value pattern, with an explicit focused-keystroke fallback

Stored elements are re-validated before every interaction: if the
element vanished or changed, the step fails safely rather than acting
on the wrong target. UI Automation runs **only** while a workflow step
explicitly asks — nothing scans the desktop in the background, and the
camera/gesture pipeline is untouched. Use the **UI Inspector**
(Actions → Workflows → UI Inspector…) to hover any element, read its
accessible properties, and send them straight into a workflow step
("Use inspected element"); "Test Element" in the step editor is
read-only. Applications that don't expose UI Automation information
are reported honestly ("element not found — the application may not
expose UI Automation information") and everything else keeps working.

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q
```

Manual verification of real input injection:

```powershell
.\.venv\Scripts\python.exe scripts\manual_action_test.py all
```

## Build a standalone app

```powershell
.\.venv\Scripts\python.exe -m pip install pyinstaller
.\.venv\Scripts\pyinstaller.exe MotionGestureApp.spec --noconfirm
```

Result: `dist\MotionGestureApp\MotionGestureApp.exe` — no Python needed on
the target machine (Windows 10/11 x64).

## Data locations

Everything lives under `%APPDATA%\MotionGestureApp\`:
`config.json` (tuning), `app.db` (profiles/rules/actions/custom gestures),
`app.log` (rotating log), `models\` (vision model). Set `MGA_DATA_DIR` to
relocate (used by tests).

See [ARCHITECTURE.md](ARCHITECTURE.md), [TESTING.md](TESTING.md) and
[ROADMAP.md](ROADMAP.md).
