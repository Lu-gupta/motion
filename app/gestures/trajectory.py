"""Trajectory / shape gesture detection from fingertip paths.

Layered exactly like the swipe detector: consumes the already-tracked
normalized index-fingertip positions (no extra vision processing) and
fires instantaneous gesture events.

Extensible by design: `TrajectoryEngine` owns the shared fingertip
history and runs a list of `TrajectoryDetector` strategies over it.
Adding triangle/zigzag/... later = one new detector class registered in
`DETECTORS`; the engine, settings plumbing, UI listing and event flow
stay untouched. The history is the same (t, x, y) representation used
by the custom-gesture path recorder, so a future "record motion
gesture" feature can reuse it.

Circle detection (approximate by design — hand-drawn circles are ovals,
tilted and wobbly):

- multi-anchor scan over the history window (like swipes): for each
  suffix of the path, compute centroid, radii, and unwrapped angular
  progression around the centroid
- accept when ALL hold:
    size        mean radius >= min_diameter / 2
    duration    min_duration_s <= dt <= max_duration_s
    roundness   std(radii) / mean(radii) <= radius_tolerance
    closure     |end - start| <= closure_frac * mean radius
    sweep       |total angle| >= min_sweep_deg (works CW and CCW)
    direction   >= direction_consistency of angular steps rotate the
                dominant way (rejects zig-zags)
    path sanity path length ≈ circumference (rejects scribbles that
                merely end near their start)
- confidence blends roundness, closure, sweep and direction quality;
  a user threshold (recognition sensitivity) gates the fire
- one event per circle: history cleared + cooldown after a fire
- candidate state (>= ~40% of a consistent turn) gates swipe detection
  so a circle's arcs are never misread as swipes, and feeds the UI
  "drawing motion…" hint + fingertip-trail overlay
"""
from __future__ import annotations

import logging
import math
from collections import deque

log = logging.getLogger(__name__)

TRAJECTORY_GESTURES = ["circle"]


class CircleDetector:
    """Approximate-circle detector. All thresholds are normalized-space
    (fractions of the camera view), never pixels."""

    name = "circle"

    def __init__(self) -> None:
        self.min_diameter = 0.12        # normalized units (fraction of view)
        self.min_duration_s = 0.25
        self.max_duration_s = 2.0
        self.min_sweep_deg = 300.0
        self.radius_tolerance = 0.35    # std/mean of radii
        self.closure_frac = 0.9         # end-start dist vs mean radius
        self.direction_consistency = 0.8
        self.min_confidence = 0.6       # "recognition sensitivity"
        self.cooldown_s = 1.0
        self.candidate = False          # partial-turn evidence (see engine)
        # read-only diagnostics of the most recent evaluated arc (Studio
        # Circle panel). Never affects detection.
        self.last: dict = {}

    def window_s(self) -> float:
        return self.max_duration_s

    def reset(self) -> None:
        self.candidate = False

    # -- analysis ------------------------------------------------------------
    def analyze(self, pts: list[tuple[float, float, float]]
                ) -> tuple[str, float] | None:
        """pts: [(t, x, y)] oldest→newest. Returns (name, confidence) on a
        completed circle; updates self.candidate as a side channel."""
        self.candidate = False
        n = len(pts)
        if n < 8:
            return None
        t_now = pts[-1][0]

        CANDIDATE_MIN_DUR = 0.12   # arcs register as candidates early so
        best: tuple[float] | None = None   # swipes are suppressed in time
        stride = max(1, n // 24)
        for i in range(0, n - 8, stride):
            sub = pts[i:]
            dur = t_now - sub[0][0]
            if dur < CANDIDATE_MIN_DUR or dur > self.max_duration_s:
                continue
            if not self.candidate and self._rotation_evidence(sub):
                self.candidate = True
            if dur < self.min_duration_s:
                continue
            res = self._evaluate(sub)
            if res is None:
                continue
            conf, partial = res
            if partial:
                self.candidate = True
                continue
            if best is None or conf > best[0]:
                best = (conf,)
        if best is not None and best[0] >= self.min_confidence:
            return (self.name, round(best[0], 2))
        return None

    @staticmethod
    def _rotation_evidence(sub) -> bool:
        """Early-arc test for candidacy: smooth, direction-consistent
        bearing sweep of >= ~80° around the sub-path centroid. Straight
        lines can't pass — their only angular step is one ±π
        discontinuity, which is filtered out; zig-zags fail direction
        consistency; jitter fails the engine's displacement gate."""
        m = len(sub)
        if m < 6:
            return False
        cx = sum(p[1] for p in sub) / m
        cy = sum(p[2] for p in sub) / m
        angles = [math.atan2(p[2] - cy, p[1] - cx) for p in sub]
        total = 0.0
        pos = neg = 0
        for a0, a1 in zip(angles, angles[1:]):
            d = a1 - a0
            while d > math.pi:
                d -= 2 * math.pi
            while d < -math.pi:
                d += 2 * math.pi
            if abs(d) > math.pi / 2:
                continue               # bearing discontinuity, not rotation
            total += d
            if d > 1e-6:
                pos += 1
            elif d < -1e-6:
                neg += 1
        steps = pos + neg
        if steps < 4:
            return False
        if max(pos, neg) / steps < 0.8:
            return False
        return abs(total) >= 1.4       # ~80 degrees of genuine rotation

    def _evaluate(self, sub) -> tuple[float, bool] | None:
        """Returns (confidence, is_partial) or None when clearly invalid."""
        m = len(sub)
        cx = sum(p[1] for p in sub) / m
        cy = sum(p[2] for p in sub) / m
        radii = [math.hypot(p[1] - cx, p[2] - cy) for p in sub]
        mean_r = sum(radii) / m
        if mean_r < self.min_diameter / 2:
            return None
        var = sum((r - mean_r) ** 2 for r in radii) / m
        rel_std = math.sqrt(var) / mean_r
        if rel_std > self.radius_tolerance:
            return None

        # angular progression around the centroid
        angles = [math.atan2(p[2] - cy, p[1] - cx) for p in sub]
        total = 0.0
        pos = neg = 0
        for a0, a1 in zip(angles, angles[1:]):
            d = a1 - a0
            while d > math.pi:
                d -= 2 * math.pi
            while d < -math.pi:
                d += 2 * math.pi
            total += d
            if d > 1e-6:
                pos += 1
            elif d < -1e-6:
                neg += 1
        steps = pos + neg
        if steps == 0:
            return None
        dominant = max(pos, neg) / steps
        if dominant < self.direction_consistency:
            return None
        sweep = abs(total)
        min_sweep = math.radians(self.min_sweep_deg)

        if sweep < min_sweep:
            # enough consistent rotation to call it a candidate?
            return (0.0, True) if sweep >= 0.25 * 2 * math.pi else None

        # closure: back near the starting region (relative to size)
        close_d = math.hypot(sub[-1][1] - sub[0][1], sub[-1][2] - sub[0][2])
        if close_d > self.closure_frac * mean_r:
            return (0.0, True)          # still turning, not closed yet

        # path-length sanity: a circle's path ≈ its circumference
        path = sum(math.hypot(b[1] - a[1], b[2] - a[2])
                   for a, b in zip(sub, sub[1:]))
        circumference = 2 * math.pi * mean_r
        ratio = path / circumference if circumference > 0 else 99.0
        if not (0.6 <= ratio <= 2.8):
            return None

        conf = (0.35 * (1.0 - rel_std / self.radius_tolerance)
                + 0.25 * (1.0 - close_d / (self.closure_frac * mean_r))
                + 0.20 * min(1.0, sweep / (2 * math.pi))
                + 0.20 * dominant)
        self.last = {
            "direction": "CW" if total < 0 else "CCW",
            "movement": "VALID" if mean_r >= self.min_diameter / 2
            else "TOO SMALL",
            "closure": "VALID", "sweep_deg": round(math.degrees(sweep)),
            "confidence": round(max(0.0, min(1.0, conf)), 2),
            "result": "MATCH"}
        return (max(0.0, min(1.0, conf)), False)


TEMPLATE_POINTS = 32


def normalize_template(points: list[tuple[float, float]]):
    """Polyline → resampled (TEMPLATE_POINTS, 2) array, centroid at the
    origin, RMS radius 1. Position- and scale-invariant by construction;
    rotation is handled at match time by a small rotation search."""
    import numpy as np

    from .custom import _resample
    pts = np.asarray(points, dtype=np.float64)
    if len(pts) < 2:
        raise ValueError("not enough points")
    rs = _resample(pts, TEMPLATE_POINTS)
    rs = rs - rs.mean(axis=0)
    rms = float(np.sqrt((rs ** 2).sum(axis=1).mean()))
    if rms < 1e-9:
        raise ValueError("no movement")
    return rs / rms


# quality gates shared by the recorder and the sample evaluator — the
# ONLY real acceptance criteria (no invented metrics)
MOTION_MIN_POINTS = 10
MOTION_MIN_MOVEMENT = 0.25


def build_motion_template(samples: list[list[tuple[float, float]]],
                          revision: int = 1) -> dict:
    """Merge one or more drawn samples into a stored template: each
    sample is normalized, then averaged point-wise and re-normalized.

    The normalized per-sample trajectories are kept in `raw_samples` so
    samples can be reviewed, deleted, replaced and the template rebuilt
    without re-recording — never raw camera frames, only the normalized
    shape the recognizer already uses. `revision` bumps on every rebuild.
    """
    import numpy as np
    if not samples:
        raise ValueError("no samples")
    norm = [normalize_template(s) for s in samples]
    mean = np.mean(np.stack(norm), axis=0)
    merged = normalize_template([tuple(p) for p in mean])
    return {"points": [[float(x), float(y)] for x, y in merged],
            "allow_reverse": True, "samples": len(samples),
            "raw_samples": [[[float(x), float(y)] for x, y in s]
                            for s in norm],
            "revision": int(revision), "version": 1}


def motion_samples(template: dict) -> list[list[tuple[float, float]]]:
    """The normalized per-sample trajectories stored in a template.
    Legacy templates (recorded before raw_samples existed) fall back to
    the merged shape as a single sample so they still preview/manage."""
    rs = (template or {}).get("raw_samples")
    if rs:
        return [[tuple(p) for p in s] for s in rs]
    pts = (template or {}).get("points") or []
    return [[tuple(p) for p in pts]] if pts else []


def rebuild_template(samples: list[list[tuple[float, float]]],
                     revision: int = 1) -> dict:
    """Rebuild a template from a (possibly edited) sample set. Same merge
    as recording — no second algorithm."""
    return build_motion_template(samples, revision=revision)


def sample_spread(samples: list[list[tuple[float, float]]]) -> dict:
    """Diagnostic spread between samples: mean/max pairwise normalized
    distance (the same metric the recognizer matches with). Read-only."""
    norm = [normalize_template(s) for s in samples]
    if len(norm) < 2:
        return {"mean": 0.0, "max": 0.0}
    ds = []
    for i in range(len(norm)):
        for j in range(i + 1, len(norm)):
            ds.append(template_distance(norm[i], norm[j], allow_reverse=True))
    return {"mean": sum(ds) / len(ds), "max": max(ds)}


def template_diagnostics(template: dict) -> dict:
    """Read-only template-level diagnostics for the Studio manager: sample
    count, resampled point count, and inter-sample spread → a plain
    consistency label. Never changes the template."""
    samples = motion_samples(template)
    spread = sample_spread(samples) if len(samples) >= 2 \
        else {"mean": 0.0, "max": 0.0}
    m = spread["max"]
    label = ("consistent" if m < 0.25
             else "varied" if m < 0.5 else "inconsistent")
    return {"samples": template.get("samples", len(samples)),
            "spread_mean": round(spread["mean"], 3),
            "spread_max": round(m, 3), "consistency": label,
            "points": len(template.get("points", [])),
            "revision": template.get("revision", 1)}


def evaluate_motion_sample(traj: list[tuple[float, float]]) -> dict:
    """Quality verdict for one freshly drawn trajectory, using ONLY the
    recorder's real gates (point count + total normalized movement). No
    fabricated quality scores."""
    import numpy as np
    pts = np.array(traj, dtype=np.float64) if traj else np.zeros((0, 2))
    n = len(pts)
    movement = (float(np.linalg.norm(np.diff(pts, axis=0), axis=1).sum())
                if n > 1 else 0.0)
    reasons = []
    if n < MOTION_MIN_POINTS:
        reasons.append("Insufficient trajectory data")
    if movement < MOTION_MIN_MOVEMENT:
        reasons.append("Movement too small")
    return {"ok": not reasons, "points": n,
            "movement": round(movement, 3), "reasons": reasons}


def template_distance(tmpl_pts, cand_pts, allow_reverse: bool = True
                      ) -> float:
    """Mean point-wise distance between two normalized shapes, minimized
    over a small rotation search (±25°) and optional direction flip."""
    import numpy as np
    a = np.asarray(tmpl_pts, dtype=np.float64)
    best = math.inf
    variants = [cand_pts, cand_pts[::-1]] if allow_reverse else [cand_pts]
    for cand in variants:
        b = np.asarray(cand, dtype=np.float64)
        for rot in (-0.44, -0.22, 0.0, 0.22, 0.44):
            c, s = math.cos(rot), math.sin(rot)
            br = b @ np.array([[c, s], [-s, c]])
            d = float(np.mean(np.linalg.norm(a - br, axis=1)))
            if d < best:
                best = d
    return best


class TemplateDetector:
    """Matches the fingertip path against one recorded shape template.
    Deterministic $1-recognizer-style matching — no ML."""

    def __init__(self, name: str, template: dict, tolerance: float = 0.35,
                 min_confidence: float = 0.55,
                 cooldown_s: float = 1.0) -> None:
        self.name = name
        self.template = template
        self.tolerance = tolerance
        self.min_confidence = min_confidence
        self.cooldown_s = cooldown_s
        self.min_diameter = 0.10        # engine displacement gate
        self.min_duration_s = 0.3
        self.max_duration_s = 2.5
        self.candidate = False

    def window_s(self) -> float:
        return self.max_duration_s

    def reset(self) -> None:
        self.candidate = False

    def _confidence(self, pts: list[tuple[float, float]]) -> float:
        try:
            cand = normalize_template(pts)
        except ValueError:
            return 0.0
        d = template_distance(self.template["points"], cand,
                              self.template.get("allow_reverse", True))
        return max(0.0, 1.0 - d / (2 * max(self.tolerance, 1e-9)))

    def analyze(self, pts: list[tuple[float, float, float]]
                ) -> tuple[str, float] | None:
        self.candidate = False
        n = len(pts)
        if n < 8:
            return None
        t_now = pts[-1][0]
        best = 0.0
        stride = max(1, n // 16)
        for i in range(0, n - 8, stride):
            sub = pts[i:]
            dur = t_now - sub[0][0]
            if dur < self.min_duration_s or dur > self.max_duration_s:
                continue
            xs = [p[1] for p in sub]
            ys = [p[2] for p in sub]
            if max(max(xs) - min(xs), max(ys) - min(ys)) < self.min_diameter:
                continue
            # a short directional translation is the swipe detector's
            # domain — templates never claim swipe-like paths
            path = sum(math.hypot(b[1] - a[1], b[2] - a[2])
                       for a, b in zip(sub, sub[1:]))
            net = math.hypot(sub[-1][1] - sub[0][1], sub[-1][2] - sub[0][2])
            if path > 1e-9 and net / path >= 0.72:
                continue
            conf = self._confidence([(p[1], p[2]) for p in sub])
            if conf > best:
                best = conf
        if best >= self.min_confidence:
            return (self.name, round(best, 2))
        if best >= 0.7 * self.min_confidence:
            self.candidate = True       # UI trail hint only — never gates
        return None


DETECTORS = [CircleDetector]


class TrajectoryEngine:
    """Owns the fingertip history; runs detector strategies over it."""

    def __init__(self) -> None:
        self.detectors = [cls() for cls in DETECTORS]
        self._hist: deque[tuple[float, float, float]] = deque()
        self._last_fire = float("-inf")

    def set_templates(self, rows) -> None:
        """Hot-reload recorded motion-gesture templates. Built-in shape
        detectors (circle) keep their instances — and their tuned
        settings; template detectors are rebuilt from the repository."""
        builtin = [d for d in self.detectors
                   if not isinstance(d, TemplateDetector)]
        templates = [
            TemplateDetector(r.name, r.template, r.tolerance,
                             r.min_confidence, r.cooldown_ms / 1000.0)
            for r in rows if r.enabled]
        self.detectors = builtin + templates

    def detector(self, name: str):
        for d in self.detectors:
            if d.name == name:
                return d
        return None

    @property
    def candidate(self) -> bool:
        return any(d.candidate for d in self.detectors)

    def points(self) -> list[tuple[float, float]]:
        return [(x, y) for (_, x, y) in self._hist]

    def net_turn(self, ts: float, window_s: float = 0.45) -> float:
        """|accumulated signed tangent-heading change| over the recent
        fingertip path. A straight/mildly-curved swipe stays low; a
        circle or direction reversal grows large. Jitter segments
        (< 0.004) are skipped; opposite curls cancel (signed sum)."""
        pts = [(t, x, y) for (t, x, y) in self._hist if t >= ts - window_s]
        if len(pts) < 3:
            return 0.0
        total = 0.0
        prev_heading = None
        for (t0, x0, y0), (t1, x1, y1) in zip(pts, pts[1:]):
            dx, dy = x1 - x0, y1 - y0
            if math.hypot(dx, dy) < 0.004:
                continue
            h = math.atan2(dy, dx)
            if prev_heading is not None:
                d = h - prev_heading
                while d > math.pi:
                    d -= 2 * math.pi
                while d < -math.pi:
                    d += 2 * math.pi
                total += d
            prev_heading = h
        return abs(total)

    def tip_stopped(self) -> bool:
        """True when the last few fingertip samples barely moved."""
        if len(self._hist) < 4:
            return False
        tail = list(self._hist)[-4:]
        path = sum(math.hypot(b[1] - a[1], b[2] - a[2])
                   for a, b in zip(tail, tail[1:]))
        return path < 0.012

    def reset(self) -> None:
        self._hist.clear()
        for d in self.detectors:
            d.reset()

    def update(self, x: float, y: float, ts: float
               ) -> tuple[str, float] | None:
        """Feed one normalized fingertip position. Returns (gesture,
        confidence) when a shape completes."""
        if self._hist and ts - self._hist[-1][0] > 0.5:
            self.reset()   # tracking gap — never join disjoint motions
        self._hist.append((ts, x, y))
        window = max(d.window_s() for d in self.detectors)
        cutoff = ts - window
        while self._hist and self._hist[0][0] < cutoff:
            self._hist.popleft()

        cooldown = max(d.cooldown_s for d in self.detectors)
        if ts - self._last_fire < cooldown:
            for d in self.detectors:
                d.candidate = False
            return None
        if len(self._hist) < 8:
            return None

        # cheap displacement gate: idle/hovering hands never reach the
        # shape analysis (bounding box must be plausibly shape-sized)
        min_need = min(d.min_diameter for d in self.detectors
                       if hasattr(d, "min_diameter"))
        xs = [p[1] for p in self._hist]
        ys = [p[2] for p in self._hist]
        if max(max(xs) - min(xs), max(ys) - min(ys)) < min_need * 0.8:
            for d in self.detectors:
                d.candidate = False
            return None

        pts = list(self._hist)
        for d in self.detectors:
            fire = d.analyze(pts)
            if fire:
                log.debug("Trajectory %s conf=%.2f", fire[0], fire[1])
                self._last_fire = ts
                self.reset()
                return fire
        return None
