"""Application entry point."""
from __future__ import annotations

import argparse
import logging
import sys

log = logging.getLogger(__name__)


def run() -> int:
    parser = argparse.ArgumentParser(prog="motion-gesture-app")
    parser.add_argument("--selftest", action="store_true",
                        help="initialize subsystems headless and exit")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    from .core.logging_setup import setup_logging
    setup_logging(logging.DEBUG if args.verbose else logging.INFO)

    from .core.config import Config
    from .core.events import EventBus
    from .data.db import Database
    from .runtime.controller import MotionController

    cfg = Config.load()
    db = Database()
    bus = EventBus()
    ctl = MotionController(cfg, db, bus)

    if args.selftest:
        log.info("Selftest: subsystems initialized OK")
        n_actions = len(ctl.profile_manager.actions.all())
        n_profiles = len(ctl.profile_manager.profiles.all())
        log.info("Selftest: %d profiles, %d actions", n_profiles, n_actions)
        from .camera.enumerate import list_cameras
        cams = list_cameras(3)
        log.info("Selftest: cameras: %s", cams)
        from .context.detector import snapshot
        log.info("Selftest: context: %s", snapshot())
        try:
            from .context import uia
            with uia._uia() as auto:
                n = len(auto.GetRootControl().GetChildren())
            log.info("Selftest: UI Automation OK (%d top-level windows)", n)
        except Exception:
            log.exception("Selftest: UI Automation unavailable")
            return 1
        ctl.db.close()
        return 0

    from PySide6.QtWidgets import QApplication
    from .ui.main_window import MainWindow

    qapp = QApplication(sys.argv)
    qapp.setApplicationName("Motion Gesture App")
    qapp.setQuitOnLastWindowClosed(False)  # tray keeps us alive

    win = MainWindow(ctl, bus)
    ctl.start()  # camera + tracking + context begin immediately
    if cfg.start_minimized:
        win.hide()
    else:
        win.show()

    rc = qapp.exec()
    ctl.shutdown()   # idempotent — normally already done by the tray quit
    ctl.db.close()
    return rc


if __name__ == "__main__":
    sys.exit(run())
