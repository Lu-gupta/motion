"""SQLite connection + schema. All SQL lives in the data layer."""
from __future__ import annotations

import logging
import sqlite3
import threading
from pathlib import Path

from ..core.config import data_dir

log = logging.getLogger(__name__)

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL CHECK (kind IN ('global','application','custom')),
    enabled INTEGER NOT NULL DEFAULT 1,
    priority INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS profile_apps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    process_name TEXT NOT NULL,            -- e.g. 'chrome.exe' (lowercase)
    window_pattern TEXT NOT NULL DEFAULT '' -- optional substring/glob on title
);

CREATE TABLE IF NOT EXISTS actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    type TEXT NOT NULL,                    -- mouse_click, hotkey, sequence, ...
    params_json TEXT NOT NULL DEFAULT '{}',
    is_builtin INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    gesture TEXT NOT NULL,
    action_id INTEGER NOT NULL REFERENCES actions(id) ON DELETE CASCADE,
    window_pattern TEXT NOT NULL DEFAULT '',  -- extra title condition
    zone TEXT NOT NULL DEFAULT '',            -- named screen zone or ''
    continuous INTEGER NOT NULL DEFAULT 0,
    cooldown_ms INTEGER NOT NULL DEFAULT 0,   -- 0 = use default
    enabled INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS custom_gestures (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    template_json TEXT NOT NULL,
    tolerance REAL NOT NULL DEFAULT 0.35,
    min_confidence REAL NOT NULL DEFAULT 0.7,
    enabled INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS zones (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    x0 REAL NOT NULL, y0 REAL NOT NULL,   -- normalized [0..1] screen coords
    x1 REAL NOT NULL, y1 REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS gesture_settings (
    gesture TEXT PRIMARY KEY,      -- gesture name, or 'swipe' for the group
    params_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS motion_gestures (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    template_json TEXT NOT NULL,            -- normalized trajectory shape
    tolerance REAL NOT NULL DEFAULT 0.35,
    min_confidence REAL NOT NULL DEFAULT 0.55,
    cooldown_ms INTEGER NOT NULL DEFAULT 1000,
    enabled INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS workflows (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL DEFAULT '',
    steps_json TEXT NOT NULL,               -- [{type:'action',action_id}|{type:'delay',ms}|{type:'wait',...}]
    enabled INTEGER NOT NULL DEFAULT 1,
    requires_confirmation INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS compound_gestures (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    steps_json TEXT NOT NULL,               -- [{type, gesture, hold_ms}]
    max_duration_ms INTEGER NOT NULL DEFAULT 2000,
    step_timeout_ms INTEGER NOT NULL DEFAULT 700,   -- max gap between steps
    min_gap_ms INTEGER NOT NULL DEFAULT 0,          -- min gap (double taps)
    cooldown_ms INTEGER NOT NULL DEFAULT 800,
    hand TEXT NOT NULL DEFAULT 'any',       -- any|left|right|same
    strict INTEGER NOT NULL DEFAULT 0,      -- unexpected gesture resets
    enabled INTEGER NOT NULL DEFAULT 1
);
"""


class Database:
    """One connection per thread; schema applied on first use."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (data_dir() / "app.db")
        self._local = threading.local()
        with self.connect() as con:
            con.executescript(SCHEMA)
            self._migrate(con)

    @staticmethod
    def _migrate(con: sqlite3.Connection) -> None:
        """Additive column migrations for databases created by older
        versions (CREATE TABLE IF NOT EXISTS never alters existing
        tables)."""
        cols = {r["name"] for r in con.execute(
            "PRAGMA table_info(workflows)")}
        if "description" not in cols:
            con.execute("ALTER TABLE workflows ADD COLUMN description "
                        "TEXT NOT NULL DEFAULT ''")
            log.info("Migrated: workflows.description column added")
        if "requires_confirmation" not in cols:
            con.execute("ALTER TABLE workflows ADD COLUMN "
                        "requires_confirmation INTEGER NOT NULL DEFAULT 0")
            log.info("Migrated: workflows.requires_confirmation added")

    def connect(self) -> sqlite3.Connection:
        con = getattr(self._local, "con", None)
        if con is None:
            con = sqlite3.connect(self.path)
            con.row_factory = sqlite3.Row
            con.execute("PRAGMA foreign_keys = ON")
            self._local.con = con
        return con

    def close(self) -> None:
        con = getattr(self._local, "con", None)
        if con is not None:
            con.close()
            self._local.con = None
