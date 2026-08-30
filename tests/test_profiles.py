import pytest

from app.data.db import Database
from app.profiles.manager import ProfileManager


@pytest.fixture
def pm(tmp_path):
    db = Database(tmp_path / "p.db")
    m = ProfileManager(db)
    m.seed_defaults()
    return m


def test_seed_idempotent(pm):
    n_actions = len(pm.actions.all())
    n_profiles = len(pm.profiles.all())
    pm.seed_defaults()
    assert len(pm.actions.all()) == n_actions
    assert len(pm.profiles.all()) == n_profiles
    assert pm.profiles.by_name("Global") is not None


def test_seeded_global_has_rules(pm):
    g = pm.profiles.by_name("Global")
    rules = pm.rules.for_profile(g.id)
    assert len(rules) >= 5
    gestures = {r.gesture for r in rules}
    assert "pinch" in gestures


def test_export_import_round_trip(pm, tmp_path):
    pid = pm.profiles.create("Excel", "application",
                             [{"process_name": "excel.exe"}])
    a = pm.actions.by_name("Copy")
    pm.rules.create(pid, "fist", a.id, window_pattern="*Book1*",
                    cooldown_ms=300)

    f = tmp_path / "excel.json"
    pm.export_profile(pid, f)
    assert f.exists()

    new_id = pm.import_profile(f)
    imported = pm.profiles.get(new_id)
    assert imported.name.startswith("Excel")
    assert imported.apps[0]["process_name"] == "excel.exe"
    rules = pm.rules.for_profile(new_id)
    assert len(rules) == 1
    assert rules[0].window_pattern == "*Book1*"
    assert rules[0].cooldown_ms == 300


def test_import_rejects_garbage(pm, tmp_path):
    f = tmp_path / "bad.json"
    f.write_text('{"format": "other"}')
    with pytest.raises(ValueError):
        pm.import_profile(f)


def test_import_global_becomes_custom(pm, tmp_path):
    g = pm.profiles.by_name("Global")
    f = tmp_path / "g.json"
    pm.export_profile(g.id, f)
    nid = pm.import_profile(f)
    assert pm.profiles.get(nid).kind == "custom"
