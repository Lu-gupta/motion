"""Profiles page — CRUD, app association, import/export."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QFileDialog,
                               QFormLayout, QHBoxLayout, QInputDialog, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem,
                               QMessageBox, QPushButton, QSpinBox,
                               QVBoxLayout, QWidget, QComboBox)

from ..runtime.controller import MotionController


class ProfileEditDialog(QDialog):
    def __init__(self, name="", kind="application", priority=0,
                 apps: list[dict] | None = None, allow_kind=True,
                 parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Profile")
        self.name_edit = QLineEdit(name)
        self.kind_combo = QComboBox()
        self.kind_combo.addItems(["application", "custom"])
        self.kind_combo.setCurrentText(kind if kind != "global"
                                       else "application")
        self.kind_combo.setEnabled(allow_kind)
        self.priority_spin = QSpinBox()
        self.priority_spin.setRange(-100, 100)
        self.priority_spin.setValue(priority)
        self.apps_edit = QLineEdit(
            ", ".join(a["process_name"] for a in (apps or [])))
        self.apps_edit.setPlaceholderText("chrome.exe, excel.exe")

        form = QFormLayout(self)
        form.addRow("Name:", self.name_edit)
        form.addRow("Kind:", self.kind_combo)
        form.addRow("Priority:", self.priority_spin)
        form.addRow("Processes:", self.apps_edit)
        form.addRow(QLabel("Processes: comma-separated executable names the "
                           "profile applies to."))
        btns = QDialogButtonBox(QDialogButtonBox.Save
                                | QDialogButtonBox.Cancel)
        btns.accepted.connect(self._ok)
        btns.rejected.connect(self.reject)
        form.addRow(btns)

    def _ok(self) -> None:
        if not self.name_edit.text().strip():
            QMessageBox.warning(self, "Invalid", "Name required.")
            return
        apps = [{"process_name": p.strip().lower()}
                for p in self.apps_edit.text().split(",") if p.strip()]
        if self.kind_combo.currentText() == "application" and not apps:
            QMessageBox.warning(self, "Invalid",
                                "Application profiles need at least one "
                                "process name.")
            return
        self.result_data = (self.name_edit.text().strip(),
                            self.kind_combo.currentText(),
                            self.priority_spin.value(), apps)
        self.accept()


class ProfilesPage(QWidget):
    def __init__(self, ctl: MotionController) -> None:
        super().__init__()
        self.ctl = ctl
        self.pm = ctl.profile_manager

        self.listw = QListWidget()
        self.listw.itemDoubleClicked.connect(lambda _: self._edit())

        buttons = [
            ("New", self._add), ("Edit", self._edit),
            ("Duplicate", self._duplicate), ("Delete", self._delete),
            ("Enable/Disable", self._toggle),
            ("Import…", self._import), ("Export…", self._export),
        ]
        btn_lay = QHBoxLayout()
        for label, fn in buttons:
            b = QPushButton(label)
            b.clicked.connect(fn)
            btn_lay.addWidget(b)
        btn_lay.addStretch(1)

        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(
            "Application profiles activate when their process is in the "
            "foreground and override the Global profile."))
        lay.addLayout(btn_lay)
        lay.addWidget(self.listw)
        self.refresh()

    def refresh(self) -> None:
        self.listw.clear()
        for p in self.pm.profiles.all():
            apps = ", ".join(a["process_name"] for a in p.apps)
            label = f"{p.name}  [{p.kind}]"
            if apps:
                label += f"  →  {apps}"
            if not p.enabled:
                label += "  (disabled)"
            item = QListWidgetItem(label)
            item.setData(32, p.id)
            self.listw.addItem(item)

    def _selected(self):
        it = self.listw.currentItem()
        return self.pm.profiles.get(it.data(32)) if it else None

    def _add(self) -> None:
        dlg = ProfileEditDialog(parent=self)
        if dlg.exec():
            name, kind, prio, apps = dlg.result_data
            if self.pm.profiles.by_name(name):
                QMessageBox.warning(self, "Duplicate", "Name exists.")
                return
            self.pm.profiles.create(name, kind, apps, priority=prio)
            self._done()

    def _edit(self) -> None:
        p = self._selected()
        if not p:
            return
        if p.kind == "global":
            QMessageBox.information(self, "Global profile",
                                    "The Global profile itself has no app "
                                    "association; edit its rules in Gesture "
                                    "Studio.")
            return
        dlg = ProfileEditDialog(p.name, p.kind, p.priority, p.apps,
                                parent=self)
        if dlg.exec():
            name, kind, prio, apps = dlg.result_data
            self.pm.profiles.update(p.id, name=name, priority=prio, apps=apps)
            self._done()

    def _duplicate(self) -> None:
        p = self._selected()
        if not p:
            return
        name, ok = QInputDialog.getText(self, "Duplicate", "New name:",
                                        text=f"{p.name} copy")
        if ok and name.strip():
            if self.pm.profiles.by_name(name.strip()):
                QMessageBox.warning(self, "Duplicate", "Name exists.")
                return
            self.pm.profiles.duplicate(p.id, name.strip())
            self._done()

    def _delete(self) -> None:
        p = self._selected()
        if not p:
            return
        if p.kind == "global":
            QMessageBox.warning(self, "Not allowed",
                                "The Global profile cannot be deleted.")
            return
        if QMessageBox.question(self, "Delete",
                                f"Delete profile {p.name!r} and its rules?"
                                ) == QMessageBox.Yes:
            self.pm.profiles.delete(p.id)
            self._done()

    def _toggle(self) -> None:
        p = self._selected()
        if not p:
            return
        self.pm.profiles.update(p.id, enabled=not p.enabled)
        self._done()

    def _import(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Import profile", "",
                                              "Profile (*.json)")
        if path:
            try:
                self.pm.import_profile(Path(path))
                self._done()
            except Exception as e:
                QMessageBox.warning(self, "Import failed", str(e))

    def _export(self) -> None:
        p = self._selected()
        if not p:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export profile",
                                              f"{p.name}.json",
                                              "Profile (*.json)")
        if path:
            try:
                self.pm.export_profile(p.id, Path(path))
            except Exception as e:
                QMessageBox.warning(self, "Export failed", str(e))

    def _done(self) -> None:
        self.ctl.reload_rules()
        self.refresh()
