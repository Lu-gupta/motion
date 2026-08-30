"""Repositories — the only place SQL touches application objects."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Optional

from .db import Database

log = logging.getLogger(__name__)


@dataclass
class ProfileRow:
    id: int
    name: str
    kind: str            # global | application | custom
    enabled: bool
    priority: int
    apps: list[dict]     # [{process_name, window_pattern}]


@dataclass
class ActionRow:
    id: int
    name: str
    type: str
    params: dict
    is_builtin: bool


@dataclass
class RuleRow:
    id: int
    profile_id: int
    gesture: str
    action_id: int
    window_pattern: str
    zone: str
    continuous: bool
    cooldown_ms: int
    enabled: bool


@dataclass
class CustomGestureRow:
    id: int
    name: str
    template: dict
    tolerance: float
    min_confidence: float
    enabled: bool


class ProfileRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    def _row(self, r) -> ProfileRow:
        con = self.db.connect()
        apps = [dict(a) for a in con.execute(
            "SELECT process_name, window_pattern FROM profile_apps "
            "WHERE profile_id=?", (r["id"],))]
        return ProfileRow(r["id"], r["name"], r["kind"], bool(r["enabled"]),
                          r["priority"], apps)

    def all(self) -> list[ProfileRow]:
        con = self.db.connect()
        return [self._row(r) for r in con.execute(
            "SELECT * FROM profiles ORDER BY priority DESC, name")]

    def get(self, pid: int) -> Optional[ProfileRow]:
        con = self.db.connect()
        r = con.execute("SELECT * FROM profiles WHERE id=?", (pid,)).fetchone()
        return self._row(r) if r else None

    def by_name(self, name: str) -> Optional[ProfileRow]:
        con = self.db.connect()
        r = con.execute("SELECT * FROM profiles WHERE name=?",
                        (name,)).fetchone()
        return self._row(r) if r else None

    def create(self, name: str, kind: str, apps: list[dict] | None = None,
               enabled: bool = True, priority: int = 0) -> int:
        con = self.db.connect()
        with con:
            cur = con.execute(
                "INSERT INTO profiles(name, kind, enabled, priority) "
                "VALUES(?,?,?,?)", (name, kind, int(enabled), priority))
            pid = cur.lastrowid
            for a in apps or []:
                con.execute(
                    "INSERT INTO profile_apps(profile_id, process_name, "
                    "window_pattern) VALUES(?,?,?)",
                    (pid, a["process_name"].lower(),
                     a.get("window_pattern", "")))
        return pid

    def update(self, pid: int, *, name: str | None = None,
               enabled: bool | None = None, priority: int | None = None,
               apps: list[dict] | None = None) -> None:
        con = self.db.connect()
        with con:
            if name is not None:
                con.execute("UPDATE profiles SET name=? WHERE id=?",
                            (name, pid))
            if enabled is not None:
                con.execute("UPDATE profiles SET enabled=? WHERE id=?",
                            (int(enabled), pid))
            if priority is not None:
                con.execute("UPDATE profiles SET priority=? WHERE id=?",
                            (priority, pid))
            if apps is not None:
                con.execute("DELETE FROM profile_apps WHERE profile_id=?",
                            (pid,))
                for a in apps:
                    con.execute(
                        "INSERT INTO profile_apps(profile_id, process_name, "
                        "window_pattern) VALUES(?,?,?)",
                        (pid, a["process_name"].lower(),
                         a.get("window_pattern", "")))

    def delete(self, pid: int) -> None:
        con = self.db.connect()
        with con:
            con.execute("DELETE FROM profiles WHERE id=?", (pid,))

    def duplicate(self, pid: int, new_name: str) -> int:
        src = self.get(pid)
        if not src:
            raise ValueError(f"profile {pid} not found")
        kind = "custom" if src.kind == "global" else src.kind
        new_id = self.create(new_name, kind, src.apps, src.enabled,
                             src.priority)
        con = self.db.connect()
        with con:
            for r in con.execute("SELECT * FROM rules WHERE profile_id=?",
                                 (pid,)):
                con.execute(
                    "INSERT INTO rules(profile_id, gesture, action_id, "
                    "window_pattern, zone, continuous, cooldown_ms, enabled) "
                    "VALUES(?,?,?,?,?,?,?,?)",
                    (new_id, r["gesture"], r["action_id"],
                     r["window_pattern"], r["zone"], r["continuous"],
                     r["cooldown_ms"], r["enabled"]))
        return new_id


class ActionRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    @staticmethod
    def _row(r) -> ActionRow:
        return ActionRow(r["id"], r["name"], r["type"],
                         json.loads(r["params_json"]), bool(r["is_builtin"]))

    def all(self) -> list[ActionRow]:
        con = self.db.connect()
        return [self._row(r) for r in
                con.execute("SELECT * FROM actions ORDER BY name")]

    def get(self, aid: int) -> Optional[ActionRow]:
        con = self.db.connect()
        r = con.execute("SELECT * FROM actions WHERE id=?", (aid,)).fetchone()
        return self._row(r) if r else None

    def by_name(self, name: str) -> Optional[ActionRow]:
        con = self.db.connect()
        r = con.execute("SELECT * FROM actions WHERE name=?",
                        (name,)).fetchone()
        return self._row(r) if r else None

    def create(self, name: str, type_: str, params: dict,
               is_builtin: bool = False) -> int:
        con = self.db.connect()
        with con:
            cur = con.execute(
                "INSERT INTO actions(name, type, params_json, is_builtin) "
                "VALUES(?,?,?,?)",
                (name, type_, json.dumps(params), int(is_builtin)))
        return cur.lastrowid

    def update(self, aid: int, *, name: str | None = None,
               type_: str | None = None, params: dict | None = None) -> None:
        con = self.db.connect()
        with con:
            if name is not None:
                con.execute("UPDATE actions SET name=? WHERE id=?",
                            (name, aid))
            if type_ is not None:
                con.execute("UPDATE actions SET type=? WHERE id=?",
                            (type_, aid))
            if params is not None:
                con.execute("UPDATE actions SET params_json=? WHERE id=?",
                            (json.dumps(params), aid))

    def delete(self, aid: int) -> None:
        con = self.db.connect()
        with con:
            con.execute("DELETE FROM actions WHERE id=?", (aid,))


class RuleRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    @staticmethod
    def _row(r) -> RuleRow:
        return RuleRow(r["id"], r["profile_id"], r["gesture"], r["action_id"],
                       r["window_pattern"], r["zone"], bool(r["continuous"]),
                       r["cooldown_ms"], bool(r["enabled"]))

    def all(self) -> list[RuleRow]:
        con = self.db.connect()
        return [self._row(r) for r in con.execute("SELECT * FROM rules")]

    def for_profile(self, pid: int) -> list[RuleRow]:
        con = self.db.connect()
        return [self._row(r) for r in con.execute(
            "SELECT * FROM rules WHERE profile_id=?", (pid,))]

    def get(self, rid: int) -> Optional[RuleRow]:
        con = self.db.connect()
        r = con.execute("SELECT * FROM rules WHERE id=?", (rid,)).fetchone()
        return self._row(r) if r else None

    def create(self, profile_id: int, gesture: str, action_id: int, *,
               window_pattern: str = "", zone: str = "",
               continuous: bool = False, cooldown_ms: int = 0,
               enabled: bool = True) -> int:
        con = self.db.connect()
        with con:
            cur = con.execute(
                "INSERT INTO rules(profile_id, gesture, action_id, "
                "window_pattern, zone, continuous, cooldown_ms, enabled) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (profile_id, gesture, action_id, window_pattern, zone,
                 int(continuous), cooldown_ms, int(enabled)))
        return cur.lastrowid

    def update(self, rid: int, **fields) -> None:
        allowed = {"gesture", "action_id", "window_pattern", "zone",
                   "continuous", "cooldown_ms", "enabled", "profile_id"}
        con = self.db.connect()
        with con:
            for k, v in fields.items():
                if k not in allowed:
                    raise ValueError(f"bad field {k}")
                if isinstance(v, bool):
                    v = int(v)
                con.execute(f"UPDATE rules SET {k}=? WHERE id=?", (v, rid))

    def delete(self, rid: int) -> None:
        con = self.db.connect()
        with con:
            con.execute("DELETE FROM rules WHERE id=?", (rid,))


class CustomGestureRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    @staticmethod
    def _row(r) -> CustomGestureRow:
        return CustomGestureRow(r["id"], r["name"],
                                json.loads(r["template_json"]),
                                r["tolerance"], r["min_confidence"],
                                bool(r["enabled"]))

    def all(self) -> list[CustomGestureRow]:
        con = self.db.connect()
        return [self._row(r) for r in
                con.execute("SELECT * FROM custom_gestures ORDER BY name")]

    def create(self, name: str, template: dict, tolerance: float = 0.35,
               min_confidence: float = 0.7) -> int:
        con = self.db.connect()
        with con:
            cur = con.execute(
                "INSERT INTO custom_gestures(name, template_json, tolerance, "
                "min_confidence) VALUES(?,?,?,?)",
                (name, json.dumps(template), tolerance, min_confidence))
        return cur.lastrowid

    def update(self, gid: int, *, tolerance: float | None = None,
               min_confidence: float | None = None,
               enabled: bool | None = None) -> None:
        con = self.db.connect()
        with con:
            if tolerance is not None:
                con.execute("UPDATE custom_gestures SET tolerance=? "
                            "WHERE id=?", (tolerance, gid))
            if min_confidence is not None:
                con.execute("UPDATE custom_gestures SET min_confidence=? "
                            "WHERE id=?", (min_confidence, gid))
            if enabled is not None:
                con.execute("UPDATE custom_gestures SET enabled=? WHERE id=?",
                            (int(enabled), gid))

    def delete(self, gid: int) -> None:
        con = self.db.connect()
        with con:
            con.execute("DELETE FROM custom_gestures WHERE id=?", (gid,))


@dataclass
class ZoneRow:
    id: int
    name: str
    x0: float
    y0: float
    x1: float
    y1: float


class ZoneRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    def all(self) -> list[ZoneRow]:
        con = self.db.connect()
        return [ZoneRow(r["id"], r["name"], r["x0"], r["y0"], r["x1"],
                        r["y1"])
                for r in con.execute("SELECT * FROM zones ORDER BY name")]

    def as_dict(self) -> dict[str, dict]:
        """name → {x0,y0,x1,y1} for the rule engine."""
        return {z.name: {"x0": z.x0, "y0": z.y0, "x1": z.x1, "y1": z.y1}
                for z in self.all()}

    def create(self, name: str, x0: float, y0: float, x1: float,
               y1: float) -> int:
        if x1 < x0:
            x0, x1 = x1, x0
        if y1 < y0:
            y0, y1 = y1, y0
        con = self.db.connect()
        with con:
            cur = con.execute(
                "INSERT INTO zones(name, x0, y0, x1, y1) VALUES(?,?,?,?,?)",
                (name, x0, y0, x1, y1))
        return cur.lastrowid

    def update(self, zid: int, name: str, x0: float, y0: float, x1: float,
               y1: float) -> None:
        if x1 < x0:
            x0, x1 = x1, x0
        if y1 < y0:
            y0, y1 = y1, y0
        con = self.db.connect()
        with con:
            con.execute(
                "UPDATE zones SET name=?, x0=?, y0=?, x1=?, y1=? WHERE id=?",
                (name, x0, y0, x1, y1, zid))

    def delete(self, zid: int) -> None:
        con = self.db.connect()
        with con:
            con.execute("DELETE FROM zones WHERE id=?", (zid,))


@dataclass
class CompoundRow:
    id: int
    name: str
    steps: list[dict]        # [{type: gesture|hold|release, gesture, hold_ms}]
    max_duration_ms: int
    step_timeout_ms: int
    min_gap_ms: int
    cooldown_ms: int
    hand: str                # any | left | right | same
    strict: bool
    enabled: bool


class CompoundGestureRepo:
    VALID_STEP_TYPES = ("gesture", "hold", "release")
    VALID_HANDS = ("any", "left", "right", "same")

    def __init__(self, db: Database) -> None:
        self.db = db

    @staticmethod
    def _row(r) -> CompoundRow:
        return CompoundRow(r["id"], r["name"], json.loads(r["steps_json"]),
                           r["max_duration_ms"], r["step_timeout_ms"],
                           r["min_gap_ms"], r["cooldown_ms"], r["hand"],
                           bool(r["strict"]), bool(r["enabled"]))

    def all(self) -> list[CompoundRow]:
        con = self.db.connect()
        return [self._row(r) for r in
                con.execute("SELECT * FROM compound_gestures ORDER BY name")]

    def get(self, cid: int) -> CompoundRow | None:
        con = self.db.connect()
        r = con.execute("SELECT * FROM compound_gestures WHERE id=?",
                        (cid,)).fetchone()
        return self._row(r) if r else None

    def by_name(self, name: str) -> CompoundRow | None:
        con = self.db.connect()
        r = con.execute("SELECT * FROM compound_gestures WHERE name=?",
                        (name,)).fetchone()
        return self._row(r) if r else None

    def validate(self, steps: list[dict], hand: str) -> str | None:
        if not steps or len(steps) < 1:
            return "a compound needs at least one step"
        if len(steps) > 8:
            return "too many steps (max 8)"
        for s in steps:
            if s.get("type") not in self.VALID_STEP_TYPES:
                return f"bad step type: {s.get('type')!r}"
            if s["type"] in ("gesture", "hold") and not s.get("gesture"):
                return "gesture/hold steps need a gesture"
            if s["type"] == "hold" and not int(s.get("hold_ms", 0)) > 0:
                return "hold steps need a positive hold duration"
        if hand not in self.VALID_HANDS:
            return f"bad hand: {hand!r}"
        return None

    def create(self, name: str, steps: list[dict], *,
               max_duration_ms: int = 2000, step_timeout_ms: int = 700,
               min_gap_ms: int = 0, cooldown_ms: int = 800,
               hand: str = "any", strict: bool = False) -> int:
        err = self.validate(steps, hand)
        if err:
            raise ValueError(err)
        con = self.db.connect()
        with con:
            cur = con.execute(
                "INSERT INTO compound_gestures(name, steps_json, "
                "max_duration_ms, step_timeout_ms, min_gap_ms, cooldown_ms, "
                "hand, strict) VALUES(?,?,?,?,?,?,?,?)",
                (name, json.dumps(steps), max_duration_ms, step_timeout_ms,
                 min_gap_ms, cooldown_ms, hand, int(strict)))
        return cur.lastrowid

    def update(self, cid: int, *, name: str | None = None,
               steps: list[dict] | None = None, enabled: bool | None = None,
               **timing) -> None:
        con = self.db.connect()
        with con:
            if name is not None:
                con.execute("UPDATE compound_gestures SET name=? WHERE id=?",
                            (name, cid))
            if steps is not None:
                err = self.validate(steps, timing.get("hand", "any"))
                if err:
                    raise ValueError(err)
                con.execute("UPDATE compound_gestures SET steps_json=? "
                            "WHERE id=?", (json.dumps(steps), cid))
            if enabled is not None:
                con.execute("UPDATE compound_gestures SET enabled=? "
                            "WHERE id=?", (int(enabled), cid))
            for k in ("max_duration_ms", "step_timeout_ms", "min_gap_ms",
                      "cooldown_ms", "hand", "strict"):
                if k in timing:
                    v = timing[k]
                    if isinstance(v, bool):
                        v = int(v)
                    con.execute(
                        f"UPDATE compound_gestures SET {k}=? WHERE id=?",
                        (v, cid))

    def delete(self, cid: int) -> None:
        con = self.db.connect()
        with con:
            con.execute("DELETE FROM compound_gestures WHERE id=?", (cid,))


@dataclass
class MotionGestureRow:
    id: int
    name: str
    template: dict           # {points: [[x,y]*32], allow_reverse, samples}
    tolerance: float
    min_confidence: float
    cooldown_ms: int
    enabled: bool


class MotionGestureRepo:
    """Recorded fingertip trajectory gestures (drawn shapes)."""

    def __init__(self, db: Database) -> None:
        self.db = db

    @staticmethod
    def _row(r) -> MotionGestureRow:
        return MotionGestureRow(r["id"], r["name"],
                                json.loads(r["template_json"]),
                                r["tolerance"], r["min_confidence"],
                                r["cooldown_ms"], bool(r["enabled"]))

    def all(self) -> list[MotionGestureRow]:
        con = self.db.connect()
        return [self._row(r) for r in
                con.execute("SELECT * FROM motion_gestures ORDER BY name")]

    def get(self, gid: int) -> MotionGestureRow | None:
        con = self.db.connect()
        r = con.execute("SELECT * FROM motion_gestures WHERE id=?",
                        (gid,)).fetchone()
        return self._row(r) if r else None

    def by_name(self, name: str) -> MotionGestureRow | None:
        con = self.db.connect()
        r = con.execute("SELECT * FROM motion_gestures WHERE name=?",
                        (name,)).fetchone()
        return self._row(r) if r else None

    @staticmethod
    def validate(name: str, template: dict) -> str | None:
        if not name or not name.strip():
            return "a name is required"
        pts = (template or {}).get("points", [])
        if not isinstance(pts, list) or len(pts) < 8:
            return "template needs a recorded trajectory"
        return None

    def create(self, name: str, template: dict, *,
               tolerance: float = 0.35, min_confidence: float = 0.55,
               cooldown_ms: int = 1000) -> int:
        err = self.validate(name, template)
        if err:
            raise ValueError(err)
        con = self.db.connect()
        with con:
            cur = con.execute(
                "INSERT INTO motion_gestures(name, template_json, "
                "tolerance, min_confidence, cooldown_ms) VALUES(?,?,?,?,?)",
                (name.strip(), json.dumps(template), tolerance,
                 min_confidence, cooldown_ms))
        return cur.lastrowid

    def update(self, gid: int, **fields) -> None:
        con = self.db.connect()
        with con:
            for k in ("name", "tolerance", "min_confidence", "cooldown_ms"):
                if k in fields:
                    con.execute(
                        f"UPDATE motion_gestures SET {k}=? WHERE id=?",
                        (fields[k], gid))
            if "template" in fields:
                con.execute(
                    "UPDATE motion_gestures SET template_json=? WHERE id=?",
                    (json.dumps(fields["template"]), gid))
            if "enabled" in fields:
                con.execute(
                    "UPDATE motion_gestures SET enabled=? WHERE id=?",
                    (int(fields["enabled"]), gid))

    def delete(self, gid: int) -> None:
        con = self.db.connect()
        with con:
            con.execute("DELETE FROM motion_gestures WHERE id=?", (gid,))


@dataclass
class WorkflowRow:
    id: int
    name: str
    steps: list[dict]        # [{type:'action',action_id}|{type:'delay',ms}]
    enabled: bool
    description: str = ""
    requires_confirmation: bool = False


class WorkflowRepo:
    """Multi-step workflows. Steps reference existing named actions by id
    (no duplicated action definitions) plus control steps (delay, wait).

    The step schema is a typed dict list so future step kinds
    (conditional, variables) extend it without a migration.

    Wait steps: {type:"wait", condition, process, title, timeout_ms} —
    condition semantics live in app/context/conditions.py.
    """

    VALID_STEP_TYPES = ("action", "delay", "wait",
                        "ui_find", "ui_wait", "ui_click", "ui_focus",
                        "ui_type", "if",
                        "set_var", "ui_read", "set_clipboard",
                        "retry", "repeat", "repeat_until")
    VAR_SOURCES = ("literal", "variable", "clipboard", "active_app",
                   "active_window")
    VAR_TYPES = ("text", "number", "boolean")
    MAX_STEPS = 30
    MAX_DELAY_MS = 60_000
    MIN_WAIT_TIMEOUT_MS = 100
    MAX_WAIT_TIMEOUT_MS = 120_000
    # one shared nesting budget for every block kind (if/retry/repeat/
    # repeat_until) — bounded loops must never become unbounded recursion
    MAX_NESTING_DEPTH = 5
    MAX_IF_DEPTH = MAX_NESTING_DEPTH   # historical name, same budget
    MAX_RETRY_ATTEMPTS = 20
    MAX_REPEAT_ITERATIONS = 100        # absolute ceiling — never higher
    ON_FAIL = ("fail", "continue", "fallback")

    def __init__(self, db: Database) -> None:
        self.db = db
        # settings may LOWER the repeat bound; the ceiling is absolute
        self.max_repeat = self.MAX_REPEAT_ITERATIONS

    @staticmethod
    def _row(r) -> WorkflowRow:
        keys = r.keys()
        rc = bool(r["requires_confirmation"]) \
            if "requires_confirmation" in keys else False
        return WorkflowRow(r["id"], r["name"], json.loads(r["steps_json"]),
                           bool(r["enabled"]), r["description"], rc)

    def all(self) -> list[WorkflowRow]:
        con = self.db.connect()
        return [self._row(r) for r in
                con.execute("SELECT * FROM workflows ORDER BY name")]

    def get(self, wid: int) -> WorkflowRow | None:
        con = self.db.connect()
        r = con.execute("SELECT * FROM workflows WHERE id=?",
                        (wid,)).fetchone()
        return self._row(r) if r else None

    def by_name(self, name: str) -> WorkflowRow | None:
        con = self.db.connect()
        r = con.execute("SELECT * FROM workflows WHERE name=?",
                        (name,)).fetchone()
        return self._row(r) if r else None

    def validate(self, steps: list[dict]) -> str | None:
        if not isinstance(steps, list) or not steps:
            return "a workflow needs at least one step"
        stores: set[str] = set()   # element refs defined by earlier steps
        vars_: set[str] = set()    # variables defined by earlier steps
        return self._validate_steps(steps, stores, vars_, depth=0,
                                    prefix="")

    def _validate_steps(self, steps: list[dict], stores: set[str],
                        vars_: set[str], depth: int,
                        prefix: str) -> str | None:
        if not isinstance(steps, list):
            return "malformed branch (not a step list)"
        if len(steps) > self.MAX_STEPS:
            return f"too many steps (max {self.MAX_STEPS})"
        con = self.db.connect()
        for n, s in enumerate(steps, 1):
            i = f"{prefix}{n}"
            t = s.get("type")
            if t not in self.VALID_STEP_TYPES:
                return f"step {i}: bad step type {t!r}"
            if t == "action":
                a = con.execute("SELECT type FROM actions WHERE id=?",
                                (s.get("action_id"),)).fetchone()
                if a is None:
                    return f"step {i}: action does not exist"
                if a["type"] == "workflow":
                    return f"step {i}: workflows cannot nest workflows"
            elif t == "delay":
                ms = s.get("ms", 0)
                if not isinstance(ms, int) or ms < 1:
                    return f"step {i}: delay needs a positive duration"
                if ms > self.MAX_DELAY_MS:
                    return (f"step {i}: delay too long "
                            f"(max {self.MAX_DELAY_MS} ms)")
            elif t == "wait":
                from ..context.conditions import validate as validate_cond
                err = validate_cond(s)
                if err:
                    return f"step {i}: {err}"
                err = self._validate_timeout(i, s)
                if err:
                    return err
            elif t in ("ui_find", "ui_wait"):
                ct = (s.get("control_type") or "").strip()
                if not ((s.get("name") or "").strip()
                        or (s.get("automation_id") or "").strip()
                        or (ct and ct != "Any")):
                    return (f"step {i}: UI find needs a name, automation "
                            "id or control type")
                err = self._validate_timeout(i, s)
                if err:
                    return err
                store = (s.get("store") or "").strip()
                if store:
                    stores.add(store)
            elif t in ("ui_click", "ui_focus", "ui_type"):
                ref = (s.get("ref") or "").strip()
                if not ref:
                    return f"step {i}: UI step needs a stored element name"
                if ref not in stores:
                    return (f"step {i}: element {ref!r} is not stored by "
                            "an earlier Find/Wait UI step")
                if t == "ui_type":
                    if not isinstance(s.get("text", ""), str):
                        return f"step {i}: type step needs text"
                    err = self._check_subst(i, s.get("text", ""), vars_)
                    if err:
                        return err
            elif t == "set_var":
                from ..context.conditions import VAR_NAME_RE
                name = (s.get("name") or "").strip()
                if not VAR_NAME_RE.match(name):
                    return (f"step {i}: invalid variable name {name!r} "
                            "(lowercase letters, digits, underscores)")
                src = s.get("source", "literal")
                if src not in self.VAR_SOURCES:
                    return f"step {i}: unknown variable source {src!r}"
                vtype = s.get("value_type", "text")
                if vtype not in self.VAR_TYPES:
                    return f"step {i}: unknown variable type {vtype!r}"
                if src == "literal" and vtype == "number":
                    try:
                        float(s.get("value", ""))
                    except (TypeError, ValueError):
                        return (f"step {i}: {s.get('value')!r} is not "
                                "a number")
                if src == "literal" and vtype == "text":
                    err = self._check_subst(i, str(s.get("value", "")),
                                            vars_)
                    if err:
                        return err
                if src == "variable":
                    sv = (s.get("source_var") or "").strip()
                    if sv not in vars_:
                        return (f"step {i}: variable {sv!r} has not been "
                                "defined by an earlier step")
                vars_.add(name)
            elif t == "ui_read":
                from ..context.conditions import VAR_NAME_RE
                ref = (s.get("ref") or "").strip()
                if ref not in stores:
                    return (f"step {i}: element {ref!r} is not stored by "
                            "an earlier Find/Wait UI step")
                sv = (s.get("store_var") or "").strip()
                if not VAR_NAME_RE.match(sv):
                    return f"step {i}: invalid variable name {sv!r}"
                vars_.add(sv)
            elif t == "set_clipboard":
                err = self._check_subst(i, str(s.get("value", "")), vars_)
                if err:
                    return err
            elif t == "if":
                if depth + 1 > self.MAX_IF_DEPTH:
                    return (f"step {i}: conditions nested deeper than "
                            f"{self.MAX_IF_DEPTH} levels")
                conds = s.get("conditions")
                if not isinstance(conds, list) or not conds:
                    return f"step {i}: condition step has no conditions"
                from ..context.conditions import validate as validate_cond
                for cn, c in enumerate(conds, 1):
                    cerr = validate_cond(c)
                    if cerr:
                        return f"step {i} condition {cn}: {cerr}"
                    if c.get("condition") == "variable" \
                            and c.get("var") not in vars_:
                        return (f"step {i} condition {cn}: variable "
                                f"{c.get('var')!r} has not been defined "
                                "by an earlier step")
                if s.get("mode", "all") not in ("all", "any"):
                    return f"step {i}: mode must be 'all' or 'any'"
                if s.get("on_timeout", "else") not in ("else", "fail"):
                    return f"step {i}: on_timeout must be 'else' or 'fail'"
                wait_ms = s.get("wait_ms", 0)
                if not isinstance(wait_ms, int) or wait_ms < 0 \
                        or wait_ms > self.MAX_WAIT_TIMEOUT_MS:
                    return f"step {i}: bad condition wait time"
                then_steps = s.get("then")
                if not isinstance(then_steps, list) or not then_steps:
                    return f"step {i}: the THEN branch needs at least one step"
                else_steps = s.get("else", [])
                # branches: THEN and ELSE each see the refs stored so far;
                # refs stored inside either branch are usable afterwards
                # (missing ones fail cleanly at run time)
                t_stores, t_vars = set(stores), set(vars_)
                err = self._validate_steps(then_steps, t_stores, t_vars,
                                           depth + 1, f"{i}.")
                if err:
                    return err
                e_stores, e_vars = set(stores), set(vars_)
                if else_steps:
                    err = self._validate_steps(else_steps, e_stores,
                                               e_vars, depth + 1, f"{i}.")
                    if err:
                        return err
                stores |= t_stores | e_stores
                vars_ |= t_vars | e_vars
            elif t == "retry":
                if depth + 1 > self.MAX_NESTING_DEPTH:
                    return (f"step {i}: blocks nested deeper than "
                            f"{self.MAX_NESTING_DEPTH} levels")
                attempts = s.get("attempts")
                if not isinstance(attempts, int) or attempts < 1:
                    return (f"step {i}: retry needs a positive attempt "
                            "count")
                if attempts > self.MAX_RETRY_ATTEMPTS:
                    return (f"step {i}: too many retry attempts (max "
                            f"{self.MAX_RETRY_ATTEMPTS})")
                err = self._validate_loop_delay(i, s)
                if err:
                    return err
                if s.get("on_fail", "fail") not in self.ON_FAIL:
                    return (f"step {i}: on_fail must be one of "
                            f"{'/'.join(self.ON_FAIL)}")
                body = s.get("steps")
                if not isinstance(body, list) or not body:
                    return f"step {i}: retry needs at least one step"
                b_stores, b_vars = set(stores), set(vars_)
                err = self._validate_steps(body, b_stores, b_vars,
                                           depth + 1, f"{i}.")
                if err:
                    return err
                # `until` runs AFTER the block → sees the block's vars
                err = self._validate_conditions(i, s.get("until"), b_vars,
                                                required=False)
                if err:
                    return err
                if s.get("on_fail") == "fallback":
                    fb = s.get("fallback")
                    if not isinstance(fb, list) or not fb:
                        return (f"step {i}: fallback needs at least one "
                                "step")
                    f_stores, f_vars = set(stores), set(vars_)
                    err = self._validate_steps(fb, f_stores, f_vars,
                                               depth + 1, f"{i}.f")
                    if err:
                        return err
                    stores |= f_stores
                    vars_ |= f_vars
                stores |= b_stores
                vars_ |= b_vars
            elif t == "repeat":
                if depth + 1 > self.MAX_NESTING_DEPTH:
                    return (f"step {i}: blocks nested deeper than "
                            f"{self.MAX_NESTING_DEPTH} levels")
                count = s.get("count")
                if not isinstance(count, int) or count < 1:
                    return (f"step {i}: repeat needs a positive count")
                limit = min(self.max_repeat, self.MAX_REPEAT_ITERATIONS)
                if count > limit:
                    return (f"step {i}: repeat count too high "
                            f"(max {limit})")
                body = s.get("steps")
                if not isinstance(body, list) or not body:
                    return f"step {i}: repeat needs at least one step"
                b_stores, b_vars = set(stores), set(vars_)
                err = self._validate_steps(body, b_stores, b_vars,
                                           depth + 1, f"{i}.")
                if err:
                    return err
                stores |= b_stores
                vars_ |= b_vars
            elif t == "repeat_until":
                if depth + 1 > self.MAX_NESTING_DEPTH:
                    return (f"step {i}: blocks nested deeper than "
                            f"{self.MAX_NESTING_DEPTH} levels")
                err = self._validate_conditions(i, s.get("conditions"),
                                                vars_, required=True)
                if err:
                    return err
                if s.get("mode", "all") not in ("all", "any"):
                    return f"step {i}: mode must be 'all' or 'any'"
                it = s.get("max_iterations")
                if not isinstance(it, int) or it < 1:
                    return (f"step {i}: repeat-until needs a positive "
                            "iteration bound")
                if it > self.MAX_REPEAT_ITERATIONS:
                    return (f"step {i}: too many iterations (max "
                            f"{self.MAX_REPEAT_ITERATIONS})")
                err = self._validate_loop_delay(i, s)
                if err:
                    return err
                # a time limit is MANDATORY — repeat-until can never
                # rely on the iteration bound alone
                err = self._validate_timeout(i, s)
                if err:
                    return err
                body = s.get("steps")
                if not isinstance(body, list) or not body:
                    return f"step {i}: repeat-until needs at least one step"
                b_stores, b_vars = set(stores), set(vars_)
                err = self._validate_steps(body, b_stores, b_vars,
                                           depth + 1, f"{i}.")
                if err:
                    return err
                stores |= b_stores
                vars_ |= b_vars
        return None

    def _validate_conditions(self, i, conds, vars_: set[str], *,
                             required: bool) -> str | None:
        """Shared condition-list validation (repeat_until, retry.until)
        — the ONE existing condition engine, no duplicate kinds."""
        from ..context.conditions import validate as validate_cond
        if conds in (None, []) and not required:
            return None
        if not isinstance(conds, list) or not conds:
            return f"step {i}: needs at least one condition"
        for cn, c in enumerate(conds, 1):
            cerr = validate_cond(c)
            if cerr:
                return f"step {i} condition {cn}: {cerr}"
            if c.get("condition") == "variable" and c.get("var") not in vars_:
                return (f"step {i} condition {cn}: variable "
                        f"{c.get('var')!r} has not been defined by an "
                        "earlier step")
        return None

    def _validate_loop_delay(self, i, s: dict) -> str | None:
        d = s.get("delay_ms", 0)
        if not isinstance(d, int) or d < 0 or d > self.MAX_DELAY_MS:
            return (f"step {i}: delay must be 0–{self.MAX_DELAY_MS} ms")
        return None

    @staticmethod
    def _check_subst(i, text: str, vars_: set[str]) -> str | None:
        from ..context.conditions import substitution_names
        for name in substitution_names(text):
            if name not in vars_:
                return (f"step {i}: variable {name!r} has not been "
                        "defined by an earlier step")
        return None

    def _validate_timeout(self, i: int, s: dict) -> str | None:
        tmo = s.get("timeout_ms", 0)
        if not isinstance(tmo, int) or tmo < self.MIN_WAIT_TIMEOUT_MS:
            return (f"step {i}: wait timeout must be at least "
                    f"{self.MIN_WAIT_TIMEOUT_MS} ms")
        if tmo > self.MAX_WAIT_TIMEOUT_MS:
            return (f"step {i}: wait timeout too long (max "
                    f"{self.MAX_WAIT_TIMEOUT_MS} ms)")
        return None

    def create(self, name: str, steps: list[dict],
               enabled: bool = True, description: str = "",
               requires_confirmation: bool = False) -> int:
        err = self.validate(steps)
        if err:
            raise ValueError(err)
        con = self.db.connect()
        with con:
            cur = con.execute(
                "INSERT INTO workflows(name, description, steps_json, "
                "enabled, requires_confirmation) VALUES(?,?,?,?,?)",
                (name, description, json.dumps(steps), int(enabled),
                 int(requires_confirmation)))
        return cur.lastrowid

    def update(self, wid: int, *, name: str | None = None,
               steps: list[dict] | None = None,
               enabled: bool | None = None,
               description: str | None = None,
               requires_confirmation: bool | None = None) -> None:
        con = self.db.connect()
        with con:
            if requires_confirmation is not None:
                con.execute("UPDATE workflows SET requires_confirmation=? "
                            "WHERE id=?", (int(requires_confirmation), wid))
            if name is not None:
                con.execute("UPDATE workflows SET name=? WHERE id=?",
                            (name, wid))
            if description is not None:
                con.execute("UPDATE workflows SET description=? WHERE id=?",
                            (description, wid))
            if steps is not None:
                err = self.validate(steps)
                if err:
                    raise ValueError(err)
                con.execute("UPDATE workflows SET steps_json=? WHERE id=?",
                            (json.dumps(steps), wid))
            if enabled is not None:
                con.execute("UPDATE workflows SET enabled=? WHERE id=?",
                            (int(enabled), wid))

    def delete(self, wid: int) -> None:
        con = self.db.connect()
        with con:
            con.execute("DELETE FROM workflows WHERE id=?", (wid,))


class GestureSettingsRepo:
    """Per-gesture tuning overrides.

    Key is the gesture name ('pinch', 'point', …) or the group key
    'swipe' which applies to all four swipe directions. Params are a
    small dict; absent keys fall back to engine defaults.
    """

    def __init__(self, db: Database) -> None:
        self.db = db

    def all(self) -> dict[str, dict]:
        con = self.db.connect()
        return {r["gesture"]: json.loads(r["params_json"])
                for r in con.execute("SELECT * FROM gesture_settings")}

    def get(self, gesture: str) -> dict:
        con = self.db.connect()
        r = con.execute("SELECT params_json FROM gesture_settings "
                        "WHERE gesture=?", (gesture,)).fetchone()
        return json.loads(r["params_json"]) if r else {}

    def set(self, gesture: str, params: dict) -> None:
        con = self.db.connect()
        with con:
            if params:
                con.execute(
                    "INSERT INTO gesture_settings(gesture, params_json) "
                    "VALUES(?,?) ON CONFLICT(gesture) DO UPDATE SET "
                    "params_json=excluded.params_json",
                    (gesture, json.dumps(params)))
            else:  # empty params = revert to defaults
                con.execute("DELETE FROM gesture_settings WHERE gesture=?",
                            (gesture,))


class SettingsRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    def get(self, key: str, default: str = "") -> str:
        con = self.db.connect()
        r = con.execute("SELECT value FROM settings WHERE key=?",
                        (key,)).fetchone()
        return r["value"] if r else default

    def set(self, key: str, value: str) -> None:
        con = self.db.connect()
        with con:
            con.execute(
                "INSERT INTO settings(key, value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value))
