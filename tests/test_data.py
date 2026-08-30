import pytest

from app.data.db import Database
from app.data.repository import (ActionRepo, CustomGestureRepo, ProfileRepo,
                                 RuleRepo, SettingsRepo)


@pytest.fixture
def db(tmp_path):
    return Database(tmp_path / "t.db")


def test_schema_and_profile_crud(db):
    profiles = ProfileRepo(db)
    pid = profiles.create("Chrome", "application",
                          [{"process_name": "Chrome.EXE"}])
    p = profiles.get(pid)
    assert p.name == "Chrome"
    assert p.apps[0]["process_name"] == "chrome.exe"  # lowercased

    profiles.update(pid, name="Chrome2", enabled=False)
    p = profiles.get(pid)
    assert p.name == "Chrome2"
    assert not p.enabled

    profiles.delete(pid)
    assert profiles.get(pid) is None


def test_action_rule_crud_and_cascade(db):
    profiles, actions, rules = ProfileRepo(db), ActionRepo(db), RuleRepo(db)
    pid = profiles.create("G", "global")
    aid = actions.create("Click", "mouse_click", {"button": "left"})
    rid = rules.create(pid, "pinch", aid, cooldown_ms=250)

    r = rules.get(rid)
    assert r.gesture == "pinch"
    assert r.cooldown_ms == 250

    rules.update(rid, enabled=False, window_pattern="*docs*")
    r = rules.get(rid)
    assert not r.enabled
    assert r.window_pattern == "*docs*"

    profiles.delete(pid)  # cascade removes rule
    assert rules.get(rid) is None


def test_profile_duplicate_copies_rules(db):
    profiles, actions, rules = ProfileRepo(db), ActionRepo(db), RuleRepo(db)
    pid = profiles.create("Src", "application",
                          [{"process_name": "x.exe"}])
    aid = actions.create("A", "key_press", {"key": "a"})
    rules.create(pid, "fist", aid)
    new_id = profiles.duplicate(pid, "Copy")
    assert len(rules.for_profile(new_id)) == 1
    assert profiles.get(new_id).apps[0]["process_name"] == "x.exe"


def test_custom_gesture_repo(db):
    cg = CustomGestureRepo(db)
    gid = cg.create("wave", {"pose": [0.1], "path": {}}, tolerance=0.4)
    rows = cg.all()
    assert rows[0].name == "wave"
    assert rows[0].tolerance == 0.4
    cg.update(gid, enabled=False)
    assert not cg.all()[0].enabled
    cg.delete(gid)
    assert cg.all() == []


def test_settings_repo(db):
    s = SettingsRepo(db)
    assert s.get("missing", "dflt") == "dflt"
    s.set("k", "v1")
    s.set("k", "v2")
    assert s.get("k") == "v2"
