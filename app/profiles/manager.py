"""Profile management: CRUD wrappers + import/export + seeding."""
from __future__ import annotations

import json
import logging
from pathlib import Path

from ..data.db import Database
from ..data.repository import (ActionRepo, ProfileRepo, RuleRepo,
                               WorkflowRepo)

log = logging.getLogger(__name__)

GLOBAL_PROFILE = "Global"

DEFAULT_ACTIONS: list[tuple[str, str, dict]] = [
    ("Left click", "mouse_click", {"button": "left", "count": 1}),
    ("Right click", "mouse_click", {"button": "right", "count": 1}),
    ("Double click", "mouse_click", {"button": "left", "count": 2}),
    ("Middle click", "mouse_click", {"button": "middle", "count": 1}),
    ("Scroll up", "mouse_scroll", {"amount": 2, "horizontal": False}),
    ("Scroll down", "mouse_scroll", {"amount": -2, "horizontal": False}),
    ("Minimize window", "window", {"op": "minimize"}),
    ("Maximize window", "window", {"op": "maximize"}),
    ("Restore window", "window", {"op": "restore"}),
    ("Close window", "window", {"op": "close"}),
    ("Switch window", "window", {"op": "switch"}),
    ("Volume up", "volume", {"op": "up", "steps": 2}),
    ("Volume down", "volume", {"op": "down", "steps": 2}),
    ("Mute", "volume", {"op": "mute"}),
    ("Play / Pause", "media", {"op": "play_pause"}),
    ("Next track", "media", {"op": "next"}),
    ("Previous track", "media", {"op": "prev"}),
    ("Copy", "hotkey", {"keys": "ctrl+c"}),
    ("Paste", "hotkey", {"keys": "ctrl+v"}),
    ("Undo", "hotkey", {"keys": "ctrl+z"}),
    ("Cursor follows hand", "cursor_control", {}),
]


class ProfileManager:
    def __init__(self, db: Database) -> None:
        self.db = db
        self.profiles = ProfileRepo(db)
        self.rules = RuleRepo(db)
        self.actions = ActionRepo(db)
        self.workflows = WorkflowRepo(db)

    # -- seeding ------------------------------------------------------------
    def seed_defaults(self) -> None:
        """Idempotent: create built-in actions + Global profile w/ starter
        mappings on first run."""
        for name, type_, params in DEFAULT_ACTIONS:
            if not self.actions.by_name(name):
                self.actions.create(name, type_, params, is_builtin=True)

        if not self.profiles.by_name(GLOBAL_PROFILE):
            pid = self.profiles.create(GLOBAL_PROFILE, "global", priority=0)
            starter = [
                ("pinch", "Left click"),
                ("fist", "Right click"),
                ("swipe_up", "Volume up"),
                ("swipe_down", "Volume down"),
                ("swipe_left", "Previous track"),
                ("swipe_right", "Next track"),
                ("thumb_up", "Play / Pause"),
            ]
            for gesture, action_name in starter:
                a = self.actions.by_name(action_name)
                if a:
                    self.rules.create(pid, gesture, a.id)
            log.info("Seeded Global profile with starter mappings")

    # -- import / export ----------------------------------------------------
    def export_profile(self, pid: int, path: Path) -> None:
        p = self.profiles.get(pid)
        if not p:
            raise ValueError(f"profile {pid} not found")
        rules = []
        for r in self.rules.for_profile(pid):
            a = self.actions.get(r.action_id)
            rules.append({
                "gesture": r.gesture,
                "action": self._export_action(a) if a else None,
                "window_pattern": r.window_pattern,
                "zone": r.zone,
                "continuous": r.continuous,
                "cooldown_ms": r.cooldown_ms,
                "enabled": r.enabled,
            })
        doc = {"format": "mga-profile", "version": 1,
               "profile": {"name": p.name, "kind": p.kind,
                           "priority": p.priority, "apps": p.apps},
               "rules": rules}
        path.write_text(json.dumps(doc, indent=2), encoding="utf-8")

    def import_profile(self, path: Path) -> int:
        doc = json.loads(path.read_text(encoding="utf-8"))
        if doc.get("format") != "mga-profile":
            raise ValueError("not a Motion Gesture App profile file")
        prof = doc["profile"]
        name = prof["name"]
        base, i = name, 2
        while self.profiles.by_name(name):
            name = f"{base} ({i})"
            i += 1
        kind = prof.get("kind", "custom")
        if kind == "global":
            kind = "custom"  # never import a second global
        pid = self.profiles.create(name, kind, prof.get("apps", []),
                                   priority=prof.get("priority", 0))
        for r in doc.get("rules", []):
            adoc = r.get("action")
            if not adoc:
                continue
            aid = self._import_action(adoc)
            self.rules.create(pid, r["gesture"], aid,
                              window_pattern=r.get("window_pattern", ""),
                              zone=r.get("zone", ""),
                              continuous=r.get("continuous", False),
                              cooldown_ms=r.get("cooldown_ms", 0),
                              enabled=r.get("enabled", True))
        return pid

    # -- action serialization (workflow-aware) -------------------------------
    def _export_action(self, a) -> dict:
        """Serialize an action. Workflow actions embed their step list with
        referenced action definitions by name (workflow ids are
        machine-local and never exported)."""
        doc = {"name": a.name, "type": a.type, "params": a.params}
        if a.type != "workflow":
            return doc
        doc["params"] = {}
        wf = self.workflows.get(int(a.params.get("workflow_id", 0) or 0))
        if wf is None:
            return doc
        doc["workflow"] = {"steps": self._export_steps(wf.steps),
                           "description": wf.description,
                           "requires_confirmation": wf.requires_confirmation}
        return doc

    def _export_steps(self, steps: list[dict]) -> list[dict]:
        """Machine-local action ids → portable name refs, recursively
        through conditional branches; other steps are self-contained."""
        out = []
        for s in steps:
            if s.get("type") == "action":
                sa = self.actions.get(s.get("action_id", 0))
                if sa and sa.type != "workflow":
                    out.append({"type": "action",
                                "action": {"name": sa.name,
                                           "type": sa.type,
                                           "params": sa.params}})
            elif s.get("type") == "if":
                node = dict(s)
                node["then"] = self._export_steps(s.get("then", []))
                node["else"] = self._export_steps(s.get("else", []))
                out.append(node)
            elif s.get("type") in ("retry", "repeat", "repeat_until"):
                node = dict(s)
                node["steps"] = self._export_steps(s.get("steps", []))
                if s.get("fallback"):
                    node["fallback"] = self._export_steps(
                        s.get("fallback", []))
                out.append(node)
            else:
                out.append(dict(s))
        return out

    def materialize_steps(self, docs: list[dict]) -> list[dict]:
        """Turn portable steps (e.g. from the workflow recorder — action
        steps carry an inline `action` spec) into concrete steps with
        real `action_id`s, find-or-creating named actions. Reuses the
        exact importer path so recorded and imported workflows are
        identical."""
        return self._import_steps(docs)

    def _import_steps(self, docs: list[dict]) -> list[dict]:
        """Portable name refs → local action ids, recursively through
        conditional branches."""
        steps = []
        for s in docs:
            if s.get("type") == "action" and s.get("action"):
                steps.append({"type": "action",
                              "action_id": self._import_action(
                                  s["action"])})
            elif s.get("type") == "if":
                node = dict(s)
                node["then"] = self._import_steps(s.get("then", []))
                node["else"] = self._import_steps(s.get("else", []))
                steps.append(node)
            elif s.get("type") in ("retry", "repeat", "repeat_until"):
                node = dict(s)
                node["steps"] = self._import_steps(s.get("steps", []))
                if s.get("fallback"):
                    node["fallback"] = self._import_steps(
                        s.get("fallback", []))
                steps.append(node)
            elif s.get("type"):
                steps.append(dict(s))   # delay / wait / ui_* verbatim
        return steps

    def _import_action(self, adoc: dict) -> int:
        """Find-or-create an action from its exported form. Workflow
        actions recreate (or reuse by name) the workflow definition and
        every referenced step action."""
        existing = self.actions.by_name(adoc["name"])
        if existing:
            return existing.id
        params = adoc.get("params", {})
        if adoc.get("type") == "workflow":
            steps = self._import_steps(
                (adoc.get("workflow") or {}).get("steps", []))
            wf = self.workflows.by_name(adoc["name"])
            if wf is not None:
                params = {"workflow_id": wf.id}
            elif steps:
                wdoc = adoc.get("workflow") or {}
                params = {"workflow_id": self.workflows.create(
                    adoc["name"], steps,
                    description=wdoc.get("description", ""),
                    requires_confirmation=bool(
                        wdoc.get("requires_confirmation", False)))}
            else:
                params = {}  # unusable but import must not abort
        return self.actions.create(adoc["name"], adoc["type"], params)
