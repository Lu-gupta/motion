import json

from app.core.config import Config
from app.core.events import EventBus


def test_config_round_trip(tmp_path, monkeypatch):
    monkeypatch.setenv("MGA_DATA_DIR", str(tmp_path))
    cfg = Config()
    cfg.camera_index = 2
    cfg.gesture_confidence_threshold = 0.9
    cfg.save()
    loaded = Config.load()
    assert loaded.camera_index == 2
    assert loaded.gesture_confidence_threshold == 0.9


def test_config_ignores_unknown_keys(tmp_path, monkeypatch):
    monkeypatch.setenv("MGA_DATA_DIR", str(tmp_path))
    p = Config.path()
    p.write_text(json.dumps({"camera_index": 1, "bogus_key": 42}))
    cfg = Config.load()
    assert cfg.camera_index == 1
    assert not hasattr(cfg, "bogus_key") or cfg.__dict__.get("bogus_key") is None


def test_event_bus_basic():
    bus = EventBus()
    got = []
    bus.subscribe("t", lambda x: got.append(x))
    bus.publish("t", 1)
    bus.publish("other", 2)
    assert got == [1]


def test_event_bus_handler_exception_isolated():
    bus = EventBus()
    got = []

    def bad(_):
        raise RuntimeError("boom")

    bus.subscribe("t", bad)
    bus.subscribe("t", lambda x: got.append(x))
    bus.publish("t", 5)
    assert got == [5]


def test_event_bus_unsubscribe():
    bus = EventBus()
    got = []
    h = lambda x: got.append(x)  # noqa: E731
    bus.subscribe("t", h)
    bus.unsubscribe("t", h)
    bus.publish("t", 1)
    assert got == []
