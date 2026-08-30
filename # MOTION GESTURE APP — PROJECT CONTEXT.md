# MOTION GESTURE APP — PROJECT CONTEXT

## 1. Project Identity

**Project Name:** Motion Gesture App

**Product Type:** Standalone desktop application

**Primary Platform:** Windows

**Core Purpose:**

Motion Gesture App is a programmable, context-aware computer-control application that allows users to control their entire Windows PC using hand gestures and physical hand motions captured through a camera.

The application must transform:

**Camera Input → Hand Tracking → Gesture Recognition → Context Detection → Rule Evaluation → Action Execution**

The product is not merely a gesture-recognition demo.

It is a **general-purpose gesture-to-action platform**.

---

# 2. Product Vision

The final application should allow a user to interact with their computer naturally using hand gestures.

The same gesture may perform different actions depending on:

* Active application
* Active window
* User-selected profile
* Cursor position
* Gesture type
* Gesture duration
* Gesture sequence
* Other configurable conditions

Example:

```text
PINCH

Desktop
    → Right Click

Microsoft Excel
    → New Worksheet

Google Chrome
    → New Tab

Video Player
    → Play/Pause
```

These are examples only.

The actual application must allow the user to configure the mappings themselves.

---

# 3. Core Product Principle

Do NOT build a hard-coded collection of gestures.

Build a programmable system:

```text
Gesture
    +
Context
    +
Conditions
    ↓
Action
```

Users must eventually be able to create, modify, delete, enable, disable, and organize gesture mappings without modifying source code.

---

# 4. Primary Functional Requirements

## 4.1 Camera Input

The application must:

* Detect available cameras
* Allow the user to select a camera
* Start/stop camera processing
* Handle camera disconnection
* Recover from temporary camera failures
* Display camera status
* Provide configurable camera settings where appropriate

---

# 5. Hand Tracking

Use **MediaPipe Tasks Vision** as the primary hand-tracking technology unless a technical reason requires an alternative.

The vision layer should provide stable hand landmark data to the rest of the system.

The vision system must be isolated from:

* Gesture rules
* Application profiles
* Action execution
* UI
* Database logic

The rest of the application must not directly depend on raw camera frames.

---

# 6. Gesture Engine

The gesture engine must support multiple categories.

## Static gestures

Examples:

* Open palm
* Closed fist
* Point
* Thumb up
* Pinch
* Victory
* Custom hand poses

## Dynamic gestures

Examples:

* Swipe left
* Swipe right
* Swipe up
* Swipe down
* Circular motion
* Hand rotation
* Directional movement

## Temporal gestures

Examples:

* Single pinch
* Double pinch
* Pinch and hold
* Quick pinch
* Long hold

## Compound gestures

Examples:

```text
Pinch + Swipe Left
Open Palm + Move Right
Fist → Open Palm
Pinch → Hold → Release
```

The architecture must allow new gesture recognizers to be added without rewriting the rest of the system.

---

# 7. Gesture Recognition Requirements

Gesture recognition should produce normalized gesture events rather than directly executing computer actions.

For example:

```text
GestureDetected(
    gesture="pinch",
    confidence=0.94,
    hand="right",
    timestamp=...
)
```

The gesture engine must NOT directly call:

* Mouse APIs
* Keyboard APIs
* Windows APIs
* Application-specific commands

Instead:

```text
Gesture Engine
      ↓
Gesture Event
      ↓
Rule Engine
```

This separation is mandatory.

---

# 8. Context Engine

The Context Engine determines the current computer environment.

At minimum it should be capable of identifying:

* Active application
* Process name
* Active window
* Window title
* Cursor position
* Screen
* Current profile

Example:

```text
Application:
Microsoft Excel

Process:
EXCEL.EXE

Window:
Book1.xlsx - Excel

Profile:
Microsoft Excel
```

The Context Engine must be independently testable.

---

# 9. Profile System

Profiles are a core product feature.

The system must support:

```text
Global Profile

Application Profiles
    ├── Microsoft Excel
    ├── Google Chrome
    ├── Microsoft Word
    ├── VS Code
    ├── Figma
    └── Custom Application
```

Profiles must support:

* Create
* Edit
* Delete
* Duplicate
* Enable/disable
* Import
* Export
* Reset
* Application association

---

# 10. Rule Engine

The Rule Engine is responsible for determining which action should execute.

Conceptually:

```text
IF

gesture == PINCH

AND

application == EXCEL

THEN

execute action
```

Rules should be data-driven.

Do NOT hard-code application-specific gesture behavior into Python modules.

Example conceptual rule:

```json
{
  "gesture": "pinch",
  "context": {
    "application": "excel"
  },
  "action": {
    "type": "keyboard_shortcut",
    "keys": ["SHIFT", "F11"]
  }
}
```

---

# 11. Action Engine

The Action Engine converts resolved rules into actual computer actions.

It should be modular.

## Mouse Actions

Support:

* Move cursor
* Left click
* Right click
* Middle click
* Double click
* Mouse down
* Mouse up
* Drag
* Scroll
* Horizontal scroll

## Keyboard Actions

Support:

* Key press
* Key release
* Keyboard shortcut
* Text input where appropriate

## Window Actions

Support:

* Minimize
* Maximize
* Restore
* Close
* Switch window
* Snap window where practical

## Media Actions

Support:

* Play/pause
* Next
* Previous
* Volume up
* Volume down
* Mute

## System Actions

Support appropriate Windows-level actions such as:

* Volume
* Brightness where supported
* Lock workstation
* Launch application
* Open file/folder
* Open URL

## Macro / Sequence Actions

Support sequences such as:

```text
Action 1
↓
Wait
↓
Action 2
↓
Wait
↓
Action 3
```

The action system must be extensible.

---

# 12. Application-Specific Behavior

The same gesture must be capable of producing different actions in different applications.

Example:

```text
GLOBAL
Pinch → Right Click

EXCEL
Pinch → New Worksheet

CHROME
Pinch → New Tab

CUSTOM APP
Pinch → User-defined action
```

Application-specific mappings override global mappings when configured to do so.

The precedence system must be explicit and deterministic.

Recommended precedence:

```text
Specific Window Rule
        ↓
Application Rule
        ↓
Global Rule
        ↓
No Action
```

---

# 13. User Interface

The desktop UI should be professional and production-oriented.

Primary sections:

```text
Dashboard
Gestures
Profiles
Actions
Gesture Studio
Settings
```

## Dashboard

Display:

* Camera status
* Motion control status
* Current active application
* Current profile
* Current detected gesture
* Recognition confidence
* Recent action
* System health

## Gestures

Display:

* Existing gestures
* Gesture type
* Description
* Assigned actions
* Enable/disable state

## Profiles

Display:

* Global profile
* Application profiles
* Custom profiles
* Profile status

## Gesture Studio

Allow users to:

1. Select/create a gesture
2. Preview the camera
3. Record custom motions where supported
4. Name the gesture
5. Configure sensitivity/tolerance
6. Select target applications
7. Select an action
8. Test the action
9. Save the configuration

---

# 14. Custom Gesture System

The long-term product must support user-defined gestures.

A custom gesture may be represented using:

* Hand landmark trajectory
* Finger states
* Direction
* Velocity
* Duration
* Relative movement
* Temporal sequence

Custom gesture recognition must use tolerances rather than exact coordinate matching.

Users should be able to configure sensitivity where practical.

---

# 15. Cursor / Spatial Context

The architecture should support spatial context.

Possible future conditions:

```text
Cursor over tab
Cursor over button
Cursor over application area
Cursor inside user-defined zone
```

Do not attempt to implement advanced UI semantic recognition before the foundational system is stable.

Design the architecture so it can be added later.

---

# 16. Configuration

User configuration must never require source-code changes.

Configuration should cover:

* Gesture mappings
* Profiles
* Actions
* Sensitivity
* Camera
* Performance
* Startup behavior
* Visual feedback
* Safety settings

Use a proper persistent data layer.

SQLite is the preferred initial database.

---

# 17. Technology Direction

Preferred initial stack:

### Language

Python

### Computer Vision

MediaPipe Tasks Vision

### Camera

OpenCV

### Desktop UI

PySide6 / Qt

### Database

SQLite

### Windows Integration

Use reliable Windows-native APIs/libraries where appropriate.

Avoid unnecessary dependencies.

Every dependency must have a clear purpose.

---

# 18. Architectural Rules

The system should follow clear separation of concerns.

Recommended architecture:

```text
Presentation Layer
        ↓
Application Layer
        ↓
Domain Layer
        ↓
Infrastructure Layer
```

Core runtime flow:

```text
Camera
 ↓
Vision
 ↓
Hand Tracking
 ↓
Gesture Engine
 ↓
Context Engine
 ↓
Rule Engine
 ↓
Action Engine
 ↓
Windows
```

Do not create a monolithic file.

Do not put all functionality into `main.py`.

Do not allow UI code to directly manipulate Windows input.

Do not allow gesture recognition code to execute actions directly.

Do not hard-code application profiles.

---

# 19. Event-Driven Design

Use an internal event architecture where appropriate.

Examples:

```text
CameraStarted
CameraStopped
HandDetected
GestureDetected
ContextChanged
ProfileChanged
RuleMatched
ActionStarted
ActionCompleted
ActionFailed
```

This makes the application easier to extend and debug.

---

# 20. Safety Requirements

Because this application can control the user's computer, accidental actions must be minimized.

Implement:

* Confidence thresholds
* Gesture debounce
* Cooldowns
* Minimum gesture duration where appropriate
* Action confirmation for potentially destructive operations
* Emergency disable mechanism
* Global enable/disable
* Clear visual state indicating when gesture control is active

The application must never continuously trigger the same action because a gesture remains visible unless the configured gesture specifically supports continuous behavior.

---

# 21. Performance Requirements

The application should be designed for continuous operation.

Priorities:

1. Low latency
2. Stable tracking
3. Low CPU usage
4. Reasonable memory usage
5. No unnecessary frame processing
6. Graceful degradation on weaker hardware

Do not optimize prematurely.

First establish correctness and measurable performance.

---

# 22. Testing Requirements

Testing is mandatory.

Create tests for:

* Gesture detection
* Gesture state transitions
* Gesture debounce
* Gesture confidence
* Context detection
* Profile resolution
* Rule precedence
* Action resolution
* Configuration persistence
* Import/export
* Error handling

The system should include integration tests for critical flows.

Manual testing must also be performed for:

```text
Camera
→ Hand
→ Gesture
→ Context
→ Rule
→ Action
→ Windows
```

---

# 23. Logging

Implement structured logging.

Logs should make it possible to determine:

```text
What gesture was detected?
With what confidence?
What application was active?
Which profile was selected?
Which rule matched?
Which action executed?
Did the action succeed?
```

Do not log unnecessary sensitive user information.

---

# 24. Error Handling

The application must gracefully handle:

* Camera unavailable
* Camera disconnected
* MediaPipe initialization failure
* Invalid profile
* Invalid gesture configuration
* Invalid action
* Application disappearing
* Windows API failure
* Database failure

Errors should be surfaced to the UI without crashing the entire application.

---

# 25. Development Philosophy

Build incrementally.

At every stage:

```text
Implement
→ Test
→ Run
→ Inspect
→ Fix
→ Refactor
→ Test Again
```

Never assume that code works simply because it compiles.

The application must be executed and tested continuously.

---

# 26. Definition of Done

A feature is NOT complete merely because source code exists.

A feature is complete only when:

* Implementation exists
* Tests exist where appropriate
* The application runs
* The feature works in the real desktop environment
* Errors are handled
* UI behavior is usable
* Existing functionality remains intact
* Documentation is updated
* No known critical regression remains

---

# 27. Final Product Goal

The final Motion Gesture App should feel like a polished Windows application rather than a developer prototype.

A user should be able to:

1. Install the application
2. Launch it
3. Connect/select a camera
4. Enable motion control
5. Use predefined gestures
6. Create application-specific mappings
7. Create custom gestures
8. Configure actions
9. Build action sequences
10. Save profiles
11. Switch profiles
12. Export/import configurations
13. Run the application reliably in the background

The final system must be modular enough to continue expanding without rewriting its core architecture.

---

# 28. Non-Goals

Do not introduce unrelated functionality.

Do not turn the project into:

* An AI assistant
* A chatbot
* A voice assistant
* A general automation platform unrelated to gestures
* A browser-only extension
* A simple gesture demo

The central product is:

> **A programmable, context-aware gesture control system for Windows PCs.**

---

# 29. Engineering Standard

Prefer:

* Clean architecture
* Strong typing where useful
* Small modules
* Clear interfaces
* Testable components
* Explicit state machines
* Data-driven configuration
* Structured logging
* Defensive error handling
* Maintainable code

Avoid:

* Giant files
* Global state everywhere
* Hard-coded mappings
* Magic numbers
* Duplicate logic
* Unnecessary abstractions
* Temporary hacks becoming permanent architecture

Always choose maintainability over the shortest implementation.

---

# 30. Project Rule

When working on this repository, treat Motion Gesture App as a standalone product.

All architecture, code, documentation, testing, and product decisions must remain focused on Motion Gesture App and its purpose of controlling a Windows PC through programmable hand and motion gestures.
