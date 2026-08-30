"""Rule engine.

resolve(gesture, context) → RuleMatch | None

Precedence (deterministic):
  1. Application-profile rule with a window_pattern matching the title
  2. Application-profile rule without window_pattern
  3. Global-profile rule with window_pattern match
  4. Global-profile rule without window_pattern
  5. No match

Within the same tier, higher profile priority wins, then lower rule id
(stable). All matching data comes from the DB — nothing hard-coded.
"""
from __future__ import annotations

import fnmatch
import logging
import threading

from ..core.types import ActionSpec, Context, RuleMatch
from ..data.repository import (ActionRepo, ProfileRepo, RuleRepo, ProfileRow,
                               RuleRow)

log = logging.getLogger(__name__)


def _title_matches(pattern: str, title: str) -> bool:
    if not pattern:
        return True
    t = title.lower()
    p = pattern.lower()
    if any(ch in p for ch in "*?["):
        return fnmatch.fnmatch(t, p)
    return p in t


def _zone_matches(zone: dict | None, ctx: Context,
                  screen_size: tuple[int, int]) -> bool:
    if zone is None:
        return True
    w, h = screen_size
    if w <= 0 or h <= 0:
        return False
    nx, ny = ctx.cursor_x / w, ctx.cursor_y / h
    return (zone["x0"] <= nx <= zone["x1"]
            and zone["y0"] <= ny <= zone["y1"])


class RuleEngine:
    """Caches profiles/rules/actions; call reload() after edits."""

    def __init__(self, profiles: ProfileRepo, rules: RuleRepo,
                 actions: ActionRepo, zones_provider=None) -> None:
        self._profiles_repo = profiles
        self._rules_repo = rules
        self._actions_repo = actions
        self._zones_provider = zones_provider  # callable -> {name: zone dict}
        self._lock = threading.Lock()
        self._profiles: list[ProfileRow] = []
        self._rules_by_profile: dict[int, list[RuleRow]] = {}
        self._actions: dict[int, ActionSpec] = {}
        self._zones: dict[str, dict] = {}
        self.reload()

    def reload(self) -> None:
        with self._lock:
            self._profiles = [p for p in self._profiles_repo.all() if p.enabled]
            self._rules_by_profile = {}
            for p in self._profiles:
                self._rules_by_profile[p.id] = [
                    r for r in self._rules_repo.for_profile(p.id) if r.enabled]
            self._actions = {}
            for a in self._actions_repo.all():
                self._actions[a.id] = ActionSpec(type=a.type, params=a.params,
                                                 name=a.name)
            self._zones = dict(self._zones_provider() ) if self._zones_provider else {}
        log.info("Rule engine reloaded: %d profiles, %d rules, %d actions",
                 len(self._profiles),
                 sum(len(v) for v in self._rules_by_profile.values()),
                 len(self._actions))

    # -- resolution ---------------------------------------------------------
    def resolve(self, gesture: str, ctx: Context,
                screen_size: tuple[int, int] = (0, 0)) -> RuleMatch | None:
        with self._lock:
            profiles = list(self._profiles)
            rules_by_profile = {k: list(v)
                                for k, v in self._rules_by_profile.items()}
            actions = dict(self._actions)
            zones = dict(self._zones)

        candidates: list[tuple[int, int, int, ProfileRow, RuleRow]] = []
        for p in profiles:
            app_match = self._profile_matches_app(p, ctx)
            if p.kind == "global":
                tier_base = 2  # tiers 3/4
            elif app_match:
                tier_base = 0  # tiers 1/2
            else:
                continue
            for r in rules_by_profile.get(p.id, []):
                if r.gesture != gesture:
                    continue
                if r.window_pattern:
                    if not _title_matches(r.window_pattern, ctx.window_title):
                        continue
                    tier = tier_base  # window-specific
                else:
                    tier = tier_base + 1
                if r.zone:
                    z = zones.get(r.zone)
                    if z is None:  # rule references a deleted zone
                        continue
                    if not _zone_matches(z, ctx, screen_size):
                        continue
                candidates.append((tier, -p.priority, r.id, p, r))

        if not candidates:
            return None
        candidates.sort(key=lambda c: (c[0], c[1], c[2]))
        _, _, _, prof, rule = candidates[0]
        action = actions.get(rule.action_id)
        if action is None:
            log.warning("Rule %d references missing action %d", rule.id,
                        rule.action_id)
            return None
        return RuleMatch(rule_id=rule.id, profile_name=prof.name,
                         action=action, continuous=rule.continuous,
                         cooldown_ms=rule.cooldown_ms)

    @staticmethod
    def _profile_matches_app(p: ProfileRow, ctx: Context) -> bool:
        if p.kind == "global":
            return False
        for a in p.apps:
            if a["process_name"] == ctx.process.lower():
                wp = a.get("window_pattern", "")
                if _title_matches(wp, ctx.window_title):
                    return True
        return False
