"""Safety/sensitivity presets for Gesture Studio 2.0.

Presets are just bundles of the EXISTING per-gesture tuning parameters
(the same keys `GestureSettingsRepo` already stores and the engine
already applies). No new detector parameters are introduced — a preset
only chooses values for parameters that already exist.

    SAFE      — stricter recognition, longer cooldowns (fewer accidental
                fires)
    BALANCED  — the engine defaults (an empty override = defaults)
    FAST      — looser recognition, shorter cooldowns (more responsive)

`preset_for(key, name)` returns the params dict to store for a given
gesture key ("swipe", "circle", or a static/custom gesture name). The
BALANCED preset returns {} (revert to defaults).
"""
from __future__ import annotations

PRESET_NAMES = ("SAFE", "BALANCED", "FAST")

# per gesture-kind → preset → params (only existing keys)
_STATIC = {
    "SAFE": {"confidence": 0.85, "cooldown_ms": 900},
    "BALANCED": {},
    "FAST": {"confidence": 0.60, "cooldown_ms": 300},
}
_SWIPE = {
    "SAFE": {"min_distance": 0.22, "min_speed": 0.9, "min_duration_ms": 80,
             "max_duration_ms": 600, "cooldown_ms": 900},
    "BALANCED": {},
    "FAST": {"min_distance": 0.12, "min_speed": 0.5, "min_duration_ms": 40,
             "max_duration_ms": 700, "cooldown_ms": 350},
}
_CIRCLE = {
    "SAFE": {"confidence": 0.75, "min_size": 0.16, "max_duration_ms": 2000,
             "cooldown_ms": 1500},
    "BALANCED": {},
    "FAST": {"confidence": 0.50, "min_size": 0.10, "max_duration_ms": 3000,
             "cooldown_ms": 700},
}


def kind_of(key: str) -> str:
    if key == "swipe" or key.startswith("swipe"):
        return "swipe"
    if key == "circle":
        return "circle"
    return "static"


def preset_for(key: str, name: str) -> dict:
    """Params dict to store for gesture `key` under preset `name`."""
    if name not in PRESET_NAMES:
        raise ValueError(f"unknown preset {name!r}")
    table = {"swipe": _SWIPE, "circle": _CIRCLE, "static": _STATIC}[
        kind_of(key)]
    return dict(table[name])


def preview(key: str, name: str) -> list[tuple[str, str]]:
    """Human-readable (label, value) rows for a preset preview. Empty
    params (BALANCED) render as 'default'."""
    params = preset_for(key, name)
    if not params:
        return [("All parameters", "default")]
    pretty = {"confidence": "Confidence", "cooldown_ms": "Cooldown",
              "min_distance": "Min distance", "min_speed": "Min speed",
              "min_duration_ms": "Min duration",
              "max_duration_ms": "Max duration", "min_size": "Min size"}
    rows = []
    for k, v in params.items():
        label = pretty.get(k, k)
        rows.append((label, f"{v} ms" if k.endswith("_ms") else str(v)))
    return rows
