"""Main window: navigation, pages, system tray."""
from __future__ import annotations

import logging

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (QApplication, QHBoxLayout, QListWidget,
                               QMainWindow, QMenu, QStackedWidget,
                               QSystemTrayIcon, QVBoxLayout, QWidget)

from ..core.events import EventBus
from ..runtime.controller import MotionController
from .actions_page import ActionsPage
from .bridge import QtBridge
from .command_center import CommandCenterPage
from .dashboard import Dashboard
from .gestures_page import GesturesPage
from .profiles_page import ProfilesPage
from .settings_page import SettingsPage
from .studio import StudioPage

log = logging.getLogger(__name__)


def _make_icon(active: bool) -> QIcon:
    pix = QPixmap(64, 64)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QColor("#1a9e4b" if active else "#555f6a"))
    p.setPen(Qt.NoPen)
    p.drawEllipse(6, 6, 52, 52)
    p.setBrush(QColor("white"))
    # stylized hand: palm + three fingers
    p.drawRoundedRect(22, 28, 20, 18, 4, 4)
    p.drawRoundedRect(23, 14, 5, 16, 2, 2)
    p.drawRoundedRect(30, 11, 5, 19, 2, 2)
    p.drawRoundedRect(37, 14, 5, 16, 2, 2)
    p.end()
    return QIcon(pix)


class MainWindow(QMainWindow):
    PAGES = ["Dashboard", "Command Center", "Gestures", "Profiles",
             "Actions", "Gesture Studio", "Settings"]

    def __init__(self, ctl: MotionController, bus: EventBus) -> None:
        super().__init__()
        self.ctl = ctl
        self.setWindowTitle("Motion Gesture App")
        self.resize(1100, 700)
        self.bridge = QtBridge(bus)

        self.nav = QListWidget()
        self.nav.addItems(self.PAGES)
        self.nav.setMaximumWidth(180)
        self.nav.setStyleSheet(
            "QListWidget {font-size: 14px;} "
            "QListWidget::item {padding: 10px;}")

        self.pages = QStackedWidget()
        self.dashboard = Dashboard(ctl, self.bridge)
        self.command_center = CommandCenterPage(ctl, self.bridge)
        self.gestures_page = GesturesPage(ctl, self.bridge,
                                          on_mapped=self._open_studio_for)
        self.profiles_page = ProfilesPage(ctl)
        self.actions_page = ActionsPage(ctl, self.bridge)
        self.studio_page = StudioPage(ctl, self.bridge)
        self.settings_page = SettingsPage(ctl)
        for w in (self.dashboard, self.command_center, self.gestures_page,
                  self.profiles_page, self.actions_page, self.studio_page,
                  self.settings_page):
            self.pages.addWidget(w)

        self.nav.currentRowChanged.connect(self._on_nav)
        self.nav.setCurrentRow(0)

        central = QWidget()
        lay = QHBoxLayout(central)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.nav)
        lay.addWidget(self.pages, 1)
        self.setCentralWidget(central)

        self._setup_tray()
        self.bridge.control_enabled.connect(self._update_tray_icon)
        self._update_tray_icon(ctl.motion_enabled)
        # dangerous-workflow confirmation prompt (fired from the gesture
        # thread via the bus → bridge; answered on the GUI thread)
        self.bridge.confirm_request.connect(self._on_confirm_request)

    def _on_confirm_request(self, token: int, workflow: str, gesture: str,
                            profile: str) -> None:
        from PySide6.QtWidgets import QMessageBox
        accepted = QMessageBox.question(
            self, "Confirm workflow",
            f"{gesture} → run workflow \"{workflow}\" ({profile})?\n\n"
            "This workflow is flagged as requiring confirmation.",
            QMessageBox.Cancel | QMessageBox.Yes,
            QMessageBox.Cancel) == QMessageBox.Yes
        self.ctl.resolve_confirmation(token, accepted)

    def _open_studio_for(self, gesture: str) -> None:
        """Jump to Gesture Studio with a gesture preselected."""
        self.nav.setCurrentRow(self.PAGES.index("Gesture Studio"))
        self.studio_page.preselect_gesture(gesture)

    def _on_nav(self, row: int) -> None:
        # refresh cross-page data when switching
        page = self.pages.widget(row)
        if hasattr(page, "refresh"):
            page.refresh()
        self.pages.setCurrentIndex(row)

    # -- tray ---------------------------------------------------------------
    def _setup_tray(self) -> None:
        self.tray = QSystemTrayIcon(_make_icon(False), self)
        menu = QMenu()
        self.tray_toggle = QAction("Enable motion control")
        self.tray_toggle.triggered.connect(
            lambda: self.ctl.set_motion_enabled(not self.ctl.motion_enabled))
        show_action = QAction("Open Motion Gesture App")
        show_action.triggered.connect(self._show_window)
        quit_action = QAction("Quit Motion Gesture App")
        quit_action.triggered.connect(self._quit)
        menu.addAction(show_action)
        menu.addAction(self.tray_toggle)
        menu.addSeparator()
        menu.addAction(quit_action)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(
            lambda r: self._show_window()
            if r == QSystemTrayIcon.DoubleClick else None)
        self.tray.show()

    def _update_tray_icon(self, enabled: bool) -> None:
        icon = _make_icon(enabled)
        self.tray.setIcon(icon)
        self.setWindowIcon(icon)
        self.tray.setToolTip(
            "Motion Gesture App — motion control "
            + ("ON" if enabled else "OFF"))
        self.tray_toggle.setText("Disable motion control" if enabled
                                 else "Enable motion control")

    def _show_window(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _quit(self) -> None:
        """TRUE application shutdown: disarm, cancel workflows/holds,
        join worker threads, release MediaPipe, remove the tray icon,
        then quit the Qt loop. Idempotent — repeated activations (tray
        double-fire, close during quit) are no-ops."""
        if getattr(self, "_quitting", False):
            return
        self._quitting = True
        self.tray.hide()
        self.ctl.shutdown()
        QApplication.quit()

    def closeEvent(self, event) -> None:
        if getattr(self, "_quitting", False):
            event.accept()
            return
        if self.ctl.cfg.minimize_to_tray and self.tray.isVisible():
            # close-to-tray: gesture control keeps running, no teardown
            self.hide()
            self.tray.showMessage(
                "Motion Gesture App",
                "Still running in the tray. Right-click the icon to quit.",
                QSystemTrayIcon.Information, 2500)
            event.ignore()
        else:
            self._quit()
            event.accept()
