"""Custom gesture recording and matching.

Templates store two normalized feature sets, both tolerant to scale
and position:

1. pose  — static hand shape: landmark cloud centered on the wrist and
   scaled by palm size (folded across handedness).
2. path  — optional motion trajectory of the wrist, resampled to a
   fixed number of points, centered and scale-normalized ($1-recognizer
   style, without rotation invariance — direction matters for gestures).

A template with a short path (< min movement) matches as a static pose;
otherwise pose AND path must both match.
"""
from __future__ import annotations

import logging
import math

import numpy as np

from ..core.types import Hand

log = logging.getLogger(__name__)

PATH_POINTS = 32
STATIC_PATH_THRESHOLD = 0.12  # total normalized movement below → static


def pose_features(hand: Hand) -> list[float]:
    """21 landmarks → wrist-centered, palm-scaled, handedness-folded."""
    pts = np.array([[lm.x, lm.y] for lm in hand.landmarks], dtype=np.float64)
    wrist = pts[Hand.WRIST].copy()
    pts -= wrist
    scale = hand.palm_size()
    pts /= scale
    if hand.handedness == "Left":  # fold left hands onto right
        pts[:, 0] = -pts[:, 0]
    return pts.flatten().tolist()


def _resample(points: np.ndarray, n: int) -> np.ndarray:
    """Resample polyline to n evenly spaced points."""
    if len(points) < 2:
        return np.repeat(points[:1], n, axis=0)
    seg = np.linalg.norm(np.diff(points, axis=0), axis=1)
    total = seg.sum()
    if total <= 1e-9:
        return np.repeat(points[:1], n, axis=0)
    dists = np.concatenate([[0.0], np.cumsum(seg)])
    targets = np.linspace(0.0, total, n)
    out = np.empty((n, 2))
    j = 0
    for i, t in enumerate(targets):
        while j < len(seg) - 1 and dists[j + 1] < t:
            j += 1
        denom = max(seg[j], 1e-12)
        alpha = (t - dists[j]) / denom
        out[i] = points[j] + alpha * (points[j + 1] - points[j])
    return out


def path_features(trajectory: list[tuple[float, float]]) -> dict:
    """Wrist trajectory → normalized resampled path + total movement."""
    pts = np.array(trajectory, dtype=np.float64)
    if len(pts) < 2:
        return {"points": [], "movement": 0.0}
    movement = float(np.linalg.norm(np.diff(pts, axis=0), axis=1).sum())
    rs = _resample(pts, PATH_POINTS)
    rs -= rs.mean(axis=0)
    scale = max(rs.max(axis=0) - rs.min(axis=0)) if len(rs) else 1.0
    if scale > 1e-9:
        rs /= scale
    return {"points": rs.flatten().tolist(), "movement": movement}


def build_template(name: str, sample_hands: list[Hand],
                   trajectory: list[tuple[float, float]]) -> dict:
    """Create a template from a recording.

    sample_hands: hand snapshots across the recording (pose averaged).
    trajectory: wrist positions across the recording.
    """
    poses = np.array([pose_features(h) for h in sample_hands])
    pf = path_features(trajectory)
    return {
        "name": name,
        "pose": poses.mean(axis=0).tolist(),
        "pose_std": float(poses.std(axis=0).mean()) if len(poses) > 1 else 0.0,
        "path": pf,
        "version": 1,
    }


# fingertips discriminate poses far more than palm/base joints
_TIP_IDS = (4, 8, 12, 16, 20)
_LM_WEIGHTS = np.array([3.0 if i in _TIP_IDS else 1.0 for i in range(21)])


def build_template_multi(name: str,
                         samples: list[tuple[list[Hand],
                                             list[tuple[float, float]]]]) -> dict:
    """Merge several recordings of the same gesture into one template.

    Poses are averaged across every frame of every sample. Paths are
    averaged point-wise after normalization (all samples resample to
    PATH_POINTS). Movement is the mean so a mix of hesitant/quick takes
    still classifies static vs motion sensibly.
    """
    if not samples:
        raise ValueError("no samples")
    if len(samples) == 1:
        return build_template(name, samples[0][0], samples[0][1])
    all_poses = []
    paths = []
    movements = []
    for hands, traj in samples:
        all_poses.extend(pose_features(h) for h in hands)
        pf = path_features(traj)
        movements.append(pf["movement"])
        if pf["points"]:
            paths.append(np.array(pf["points"]))
    poses = np.array(all_poses)
    mean_movement = float(np.mean(movements)) if movements else 0.0
    if mean_movement >= STATIC_PATH_THRESHOLD and paths:
        mean_path = np.mean(np.stack(paths), axis=0).tolist()
    else:
        mean_path = []
    return {
        "name": name,
        "pose": poses.mean(axis=0).tolist(),
        "pose_std": float(poses.std(axis=0).mean()) if len(poses) > 1 else 0.0,
        "path": {"points": mean_path, "movement": mean_movement},
        "samples": len(samples),
        "version": 1,
    }


def pose_distance(hand: Hand, template: dict) -> float:
    t = np.array(template["pose"])
    f = np.array(pose_features(hand))
    if t.shape != f.shape:
        return math.inf
    per_lm = np.linalg.norm((t - f).reshape(-1, 2), axis=1)
    if per_lm.shape[0] != _LM_WEIGHTS.shape[0]:
        return float(np.sqrt(np.mean((t - f) ** 2)))
    return float(np.sqrt(np.average(per_lm ** 2, weights=_LM_WEIGHTS)))


def path_distance(trajectory: list[tuple[float, float]],
                  template: dict) -> float:
    tp = template.get("path", {}).get("points", [])
    if not tp:
        return 0.0
    cur = path_features(trajectory)
    if not cur["points"]:
        return math.inf
    a = np.array(tp).reshape(-1, 2)
    b = np.array(cur["points"]).reshape(-1, 2)
    return float(np.mean(np.linalg.norm(a - b, axis=1)))


class CustomGestureMatcher:
    """Matches live hands against enabled custom-gesture templates."""

    def __init__(self) -> None:
        self.templates: list[dict] = []  # {name, template, tolerance, min_confidence}

    def set_templates(self, rows) -> None:
        """rows: iterable of CustomGestureRow."""
        self.templates = [
            {"name": r.name, "template": r.template,
             "tolerance": r.tolerance, "min_confidence": r.min_confidence}
            for r in rows if r.enabled]

    def match(self, hand: Hand,
              trajectory: list[tuple[float, float]]) -> tuple[str, float] | None:
        """Best matching custom gesture or None."""
        best: tuple[str, float] | None = None
        for t in self.templates:
            tmpl = t["template"]
            is_static = (tmpl.get("path", {}).get("movement", 0.0)
                         < STATIC_PATH_THRESHOLD)
            pd = pose_distance(hand, tmpl)
            tol = t["tolerance"]
            if pd > tol:
                continue
            conf = max(0.0, 1.0 - pd / max(tol, 1e-9))
            if not is_static:
                dd = path_distance(trajectory, tmpl)
                if dd > tol:
                    continue
                conf = min(conf, max(0.0, 1.0 - dd / max(tol, 1e-9)))
            if conf < t["min_confidence"]:
                continue
            if best is None or conf > best[1]:
                best = (t["name"], conf)
        return best
