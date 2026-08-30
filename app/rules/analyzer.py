"""Mapping conflict / precedence analyzer.

Read-only companion to the rule engine for the Gesture Command Center.
It does NOT change arbitration or resolution — it only *explains* what
the existing `RuleEngine` will already do when several rules match the
same gesture.

The engine's precedence (see rules/engine.py) is, per gesture:

    tier 0: app-profile rule WITH a window pattern
    tier 1: app-profile rule WITHOUT a window pattern
    tier 2: global rule WITH a window pattern
    tier 3: global rule WITHOUT a window pattern
  then higher profile priority, then lower rule id.

Two enabled rules for the same gesture are a genuine CONFLICT only when
they can match in the *same* context at the *same* tier with the same
window/zone constraints (the engine then falls back to the lowest rule
id — ambiguous to a human). Rules at different tiers are not a conflict:
one deterministically wins; the analyzer reports that as precedence
info ("the Chrome rule wins over the Global rule").
"""
from __future__ import annotations

from dataclasses import dataclass

from ..data.repository import ProfileRow, RuleRow


@dataclass(frozen=True)
class Finding:
    level: str      # "ok" | "conflict" | "info"
    message: str


def _tier(profile: ProfileRow, rule: RuleRow) -> int:
    # READ-ONLY explanatory mirror of RuleEngine's precedence tiers
    # (see rules/engine.py resolve(): app+window=0, app=1, global+window=2,
    # global=3). This function ONLY labels/explains conflicts for the
    # Command Center; it never resolves a mapping. If engine.py's tier
    # scheme ever changes, update this shadow to match — it must stay a
    # description of the engine, never a second resolution implementation.
    if profile.kind == "global":
        base = 2
    else:
        base = 0
    return base if rule.window_pattern else base + 1


def _profile_label(profile: ProfileRow) -> str:
    if profile.kind == "global":
        return "Global"
    apps = ", ".join(a.get("process_name", "?") for a in profile.apps)
    return f"{profile.name} ({apps})" if apps else profile.name


def analyze(profiles: list[ProfileRow],
            rules: list[RuleRow]) -> dict[int, Finding]:
    """Return {rule_id: Finding} for every enabled rule. Disabled rules
    never conflict (they never execute) and are reported as 'ok'."""
    prof_by_id = {p.id: p for p in profiles}
    out: dict[int, Finding] = {}

    # group enabled rules by gesture
    by_gesture: dict[str, list[RuleRow]] = {}
    for r in rules:
        if not r.enabled:
            out[r.id] = Finding("ok", "Disabled — never executes.")
            continue
        prof = prof_by_id.get(r.profile_id)
        if prof is None or not prof.enabled:
            out[r.id] = Finding("info", "Its profile is disabled.")
            continue
        by_gesture.setdefault(r.gesture, []).append(r)

    for gesture, group in by_gesture.items():
        for r in group:
            prof = prof_by_id[r.profile_id]
            rivals = [o for o in group if o.id != r.id]
            # true conflict: same profile + same window + same zone (the
            # engine can only tie-break by rule id)
            dupes = [o for o in rivals
                     if o.profile_id == r.profile_id
                     and (o.window_pattern or "") == (r.window_pattern or "")
                     and (o.zone or "") == (r.zone or "")]
            if dupes:
                winner = min([r] + dupes, key=lambda x: x.id)
                who = "this rule" if winner.id == r.id else \
                    f"the rule created first (id {winner.id})"
                out[r.id] = Finding(
                    "conflict",
                    f"CONFLICT — {gesture} has {len(dupes) + 1} identical "
                    f"mappings in {_profile_label(prof)}; the engine runs "
                    f"{who}.")
                continue
            # precedence info: a lower-tier rival would win over this one
            my_tier = _tier(prof, r)
            higher = []
            for o in rivals:
                op = prof_by_id[o.profile_id]
                ot = _tier(op, o)
                # only meaningful when both could apply together: an app
                # rule and a global rule (global always active)
                cross = {prof.kind, op.kind} == {"global", "application"}
                if cross and ot < my_tier:
                    higher.append((o, op))
            if higher:
                o, op = min(higher, key=lambda x: _tier(x[1], x[0]))
                out[r.id] = Finding(
                    "info",
                    f"When both apply, the {_profile_label(op)} mapping "
                    f"wins over this one (higher precedence).")
                continue
            lower = []
            for o in rivals:
                op = prof_by_id[o.profile_id]
                ot = _tier(op, o)
                cross = {prof.kind, op.kind} == {"global", "application"}
                if cross and ot > my_tier:
                    lower.append((o, op))
            if lower:
                o, op = lower[0]
                out[r.id] = Finding(
                    "info",
                    f"This mapping wins over the {_profile_label(op)} "
                    f"mapping when both apply.")
                continue
            out[r.id] = Finding("ok", "No conflicts.")
    return out
