"""Central logging: rotating file + console."""
from __future__ import annotations

import logging
import logging.handlers
import sys

from .config import data_dir


def setup_logging(level: int = logging.INFO) -> None:
    root = logging.getLogger()
    if root.handlers:  # already configured
        return
    root.setLevel(level)
    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-7s %(name)s: %(message)s")

    fh = logging.handlers.RotatingFileHandler(
        data_dir() / "app.log", maxBytes=2_000_000, backupCount=3,
        encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(fh)

    ch = logging.StreamHandler(sys.stderr)
    ch.setFormatter(fmt)
    root.addHandler(ch)

    # third-party noise stays at WARNING even in verbose mode
    for noisy in ("matplotlib", "PIL", "absl"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
