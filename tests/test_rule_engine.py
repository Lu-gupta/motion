import pytest

from app.core.types import Context
from app.data.db import Database
from app.data.repository import ActionRepo, ProfileRepo, RuleRepo
from app.rules.engine import RuleEngine


@pytest.fixture
def setup(tmp_path):
    db = Database(tmp_path / "r.db")
    profiles, actions, rules = ProfileRepo(db), ActionRepo(db), RuleRepo(db)

    a_global = actions.create("GlobalClick", "mouse_click", {"button": "left"})
    a_chrome = actions.create("ChromeAction", "hotkey", {"keys": "ctrl+t"})
    a_docs = actions.create("DocsAction", "hotkey", {"keys": "ctrl+s"})

    gid = profiles.create("Global", "global")
    rules.create(gid, "pinch", a_global)

    cid = profiles.create("Chrome", "application",
                          [{"process_name": "chrome.exe"}])
    rules.create(cid, "pinch", a_chrome)
    rules.create(cid, "pinch", a_docs, window_pattern="*docs*")

    eng = RuleEngine(profiles, rules, actions)
    return eng, profiles, rules, actions


def ctx(process="chrome.exe", title="New Tab - Google Chrome"):
    app = process[:-4] if process.endswith(".exe") else process
    return Context(application=app, process=process, window_title=title)


def test_app_specific_beats_global(setup):
    eng = setup[0]
    m = eng.resolve("pinch", ctx())
    assert m.action.name == "ChromeAction"
    assert m.profile_name == "Chrome"


def test_window_specific_beats_app(setup):
    eng = setup[0]
    m = eng.resolve("pinch", ctx(title="My Docs - Google Docs"))
    assert m.action.name == "DocsAction"


def test_global_fallback_for_unknown_app(setup):
    eng = setup[0]
    m = eng.resolve("pinch", ctx(process="notepad.exe", title="Untitled"))
    assert m.action.name == "GlobalClick"
    assert m.profile_name == "Global"


def test_no_match(setup):
    eng = setup[0]
    assert eng.resolve("fist", ctx(process="notepad.exe")) is None


def test_disabled_rule_skipped(setup):
    eng, profiles, rules, actions = setup
    for r in rules.all():
        rules.update(r.id, enabled=False)
    eng.reload()
    assert eng.resolve("pinch", ctx()) is None


def test_disabled_profile_skipped(setup):
    eng, profiles, rules, actions = setup
    chrome = profiles.by_name("Chrome")
    profiles.update(chrome.id, enabled=False)
    eng.reload()
    m = eng.resolve("pinch", ctx())
    assert m.action.name == "GlobalClick"  # falls back to global


def test_case_insensitive_process(setup):
    eng = setup[0]
    m = eng.resolve("pinch", Context(application="chrome",
                                     process="chrome.exe",
                                     window_title="x"))
    assert m.action.name == "ChromeAction"


def test_deterministic_tie_break(tmp_path):
    db = Database(tmp_path / "t2.db")
    profiles, actions, rules = ProfileRepo(db), ActionRepo(db), RuleRepo(db)
    a1 = actions.create("A1", "key_press", {"key": "a"})
    a2 = actions.create("A2", "key_press", {"key": "b"})
    gid = profiles.create("Global", "global")
    r1 = rules.create(gid, "fist", a1)
    rules.create(gid, "fist", a2)
    eng = RuleEngine(profiles, rules, actions)
    m = eng.resolve("fist", ctx())
    assert m.rule_id == r1  # lower id wins
