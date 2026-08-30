import pytest

from app.core.types import Context
from app.data.db import Database
from app.data.repository import ActionRepo, ProfileRepo, RuleRepo, ZoneRepo
from app.rules.engine import RuleEngine


@pytest.fixture
def db(tmp_path):
    return Database(tmp_path / "z.db")


def test_zone_repo_crud_and_normalization(db):
    zr = ZoneRepo(db)
    zid = zr.create("left-half", 0.5, 0.8, 0.0, 0.0)  # swapped on purpose
    z = zr.all()[0]
    assert (z.x0, z.y0, z.x1, z.y1) == (0.0, 0.0, 0.5, 0.8)
    zr.update(zid, "top", 0.0, 0.0, 1.0, 0.25)
    assert zr.as_dict()["top"]["y1"] == 0.25
    zr.delete(zid)
    assert zr.all() == []


def test_rule_engine_zone_condition(db):
    profiles, actions, rules, zones = (ProfileRepo(db), ActionRepo(db),
                                       RuleRepo(db), ZoneRepo(db))
    zones.create("top-left", 0.0, 0.0, 0.5, 0.5)
    aid = actions.create("A", "key_press", {"key": "a"})
    bid = actions.create("B", "key_press", {"key": "b"})
    gid = profiles.create("Global", "global")
    rules.create(gid, "pinch", aid, zone="top-left")
    rules.create(gid, "pinch", bid)  # anywhere

    eng = RuleEngine(profiles, rules, actions, zones_provider=zones.as_dict)
    screen = (1000, 1000)

    inside = Context(application="x", process="x.exe", window_title="t",
                     cursor_x=200, cursor_y=200)
    outside = Context(application="x", process="x.exe", window_title="t",
                      cursor_x=900, cursor_y=900)

    # zone rule has lower id → wins inside the zone
    assert eng.resolve("pinch", inside, screen).action.name == "A"
    # outside the zone only the anywhere rule matches
    assert eng.resolve("pinch", outside, screen).action.name == "B"


def test_zone_rule_no_screen_size_never_matches(db):
    profiles, actions, rules, zones = (ProfileRepo(db), ActionRepo(db),
                                       RuleRepo(db), ZoneRepo(db))
    zones.create("z", 0.0, 0.0, 1.0, 1.0)
    aid = actions.create("A", "key_press", {"key": "a"})
    gid = profiles.create("Global", "global")
    rules.create(gid, "pinch", aid, zone="z")
    eng = RuleEngine(profiles, rules, actions, zones_provider=zones.as_dict)
    ctx = Context(application="x", process="x.exe", cursor_x=1, cursor_y=1)
    assert eng.resolve("pinch", ctx, (0, 0)) is None


def test_missing_zone_never_matches(db):
    profiles, actions, rules, zones = (ProfileRepo(db), ActionRepo(db),
                                       RuleRepo(db), ZoneRepo(db))
    aid = actions.create("A", "key_press", {"key": "a"})
    gid = profiles.create("Global", "global")
    rules.create(gid, "pinch", aid, zone="ghost")
    eng = RuleEngine(profiles, rules, actions, zones_provider=zones.as_dict)
    ctx = Context(application="x", process="x.exe", cursor_x=1, cursor_y=1)
    assert eng.resolve("pinch", ctx, (1000, 1000)) is None
