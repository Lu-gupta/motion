"""Hand landmarker model management (downloaded once, cached locally)."""
from __future__ import annotations

import logging
import urllib.request
from pathlib import Path

from ..core.config import data_dir

log = logging.getLogger(__name__)

MODEL_URL = ("https://storage.googleapis.com/mediapipe-models/hand_landmarker/"
             "hand_landmarker/float16/latest/hand_landmarker.task")
MODEL_NAME = "hand_landmarker.task"
MIN_VALID_SIZE = 1_000_000  # real model is ~7.8 MB


def model_path() -> Path:
    return data_dir() / "models" / MODEL_NAME


def ensure_model() -> Path:
    p = model_path()
    if p.exists() and p.stat().st_size >= MIN_VALID_SIZE:
        return p
    p.parent.mkdir(parents=True, exist_ok=True)
    log.info("Downloading hand landmarker model to %s", p)
    tmp = p.with_suffix(".tmp")
    urllib.request.urlretrieve(MODEL_URL, tmp)
    if tmp.stat().st_size < MIN_VALID_SIZE:
        tmp.unlink(missing_ok=True)
        raise RuntimeError("Downloaded model file is too small; aborting")
    tmp.replace(p)
    return p
