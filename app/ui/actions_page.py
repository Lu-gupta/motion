"""Actions page — named reusable actions, sequences and workflows."""
from __future__ import annotations

import json

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox,
                               QFileDialog, QFormLayout, QGroupBox,
                               QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMenu, QMessageBox,
                               QPushButton, QSpinBox, QCheckBox,
                               QStackedWidget, QTextEdit, QVBoxLayout,
                               QWidget)

from ..actions.executor import ACTION_TYPES, ActionExecutor
from ..core.types import ActionSpec
from ..runtime.controller import MotionController

SIMPLE_PARAM_HELP = {
    "mouse_click": 'e.g. {"button": "right", "count": 1}',
    "mouse_scroll": 'e.g. {"amount": -2, "horizontal": false}',
    "mouse_move": 'e.g. {"mode": "relative", "dx": 50, "dy": 0}',
    "mouse_down": '{"button": "left"}',
    "mouse_up": '{"button": "left"}',
    "key_press": '{"key": "enter"}',
    "hotkey": '{"keys": "ctrl+shift+t"}',
    "window": '{"op": "minimize"}  (minimize/maximize/restore/close/switch)',
    "volume": '{"op": "up", "steps": 2}',
    "media": '{"op": "play_pause"}',
    "launch_app": '{"path": "C:/Windows/notepad.exe", "args": ""}',
    "open_url": '{"url": "https://example.com"}',
    "open_folder": '{"path": "C:/Users"}',
    "sequence": ('{"steps": [{"type": "hotkey", "params": {"keys": "ctrl+c"},'
                 ' "delay_ms_after": 200}, {"type": "key_press", "params":'
                 ' {"key": "enter"}}]}'),
    "cursor_control": "{}  (cursor follows hand while gesture held)",
}


class LaunchAppForm(QWidget):
    """Friendly editor for launch_app actions — no JSON exposed."""

    def __init__(self, name_edit: QLineEdit, parent=None) -> None:
        super().__init__(parent)
        self._name_edit = name_edit

        self.app_combo = QComboBox()
        self.app_combo.addItem("Choose an installed application…", "")
        self._discovered = []
        try:
            from ..actions.discovery import discover_applications
            self._discovered = discover_applications()
        except Exception:
            pass
        for app in self._discovered:
            self.app_combo.addItem(app.name, app.path)
        self.app_combo.currentIndexChanged.connect(self._on_pick)

        self.exe_edit = QLineEdit()
        self.exe_edit.setPlaceholderText(r"C:\path\to\application.exe")
        browse_btn = QPushButton("Browse…")
        browse_btn.clicked.connect(self._browse_exe)
        exe_row = QHBoxLayout()
        exe_row.addWidget(self.exe_edit, 1)
        exe_row.addWidget(browse_btn)

        self.args_edit = QLineEdit()
        self.args_edit.setPlaceholderText(
            'optional, e.g. --new-window "https://example.com"')
        self.cwd_edit = QLineEdit()
        self.cwd_edit.setPlaceholderText("optional working directory")
        cwd_btn = QPushButton("Browse…")
        cwd_btn.clicked.connect(self._browse_cwd)
        cwd_row = QHBoxLayout()
        cwd_row.addWidget(self.cwd_edit, 1)
        cwd_row.addWidget(cwd_btn)

        self.running_combo = QComboBox()
        self.running_combo.addItem("Launch a new instance", "new")
        self.running_combo.addItem("Focus the existing window if open",
                                   "focus")

        form = QFormLayout(self)
        form.setContentsMargins(0, 0, 0, 0)
        form.addRow("Application:", self.app_combo)
        form.addRow("Executable:", exe_row)
        form.addRow("Arguments:", self.args_edit)
        form.addRow("Working directory:", cwd_row)
        form.addRow("If already running:", self.running_combo)

    def _on_pick(self) -> None:
        path = self.app_combo.currentData()
        if path:
            self.exe_edit.setText(path)
            if not self._name_edit.text().strip():
                self._name_edit.setText(
                    f"Open {self.app_combo.currentText()}")

    def _browse_exe(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Select application", "", "Applications (*.exe)")
        if path:
            self.exe_edit.setText(path)

    def _browse_cwd(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Working directory")
        if path:
            self.cwd_edit.setText(path)

    def set_params(self, p: dict) -> None:
        self.exe_edit.setText(p.get("path", ""))
        self.args_edit.setText(p.get("args", ""))
        self.cwd_edit.setText(p.get("cwd", ""))
        i = self.running_combo.findData(p.get("if_running", "new"))
        self.running_combo.setCurrentIndex(max(0, i))

    def params(self) -> dict:
        return {"path": self.exe_edit.text().strip(),
                "args": self.args_edit.text().strip(),
                "cwd": self.cwd_edit.text().strip(),
                "if_running": self.running_combo.currentData()}


class ActionEditDialog(QDialog):
    def __init__(self, executor: ActionExecutor, name: str = "",
                 type_: str = "mouse_click", params: dict | None = None,
                 parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Edit Action")
        self.executor = executor
        self.resize(560, 420)

        self.name_edit = QLineEdit(name)
        self.type_combo = QComboBox()
        for t, meta in ACTION_TYPES.items():
            if t == "workflow":
                continue  # workflows are managed in the Workflows section
            self.type_combo.addItem(f"{meta['label']} ({t})", t)
        idx = self.type_combo.findData(type_)
        self.type_combo.setCurrentIndex(max(0, idx))

        # page 0: generic JSON editor · page 1: friendly launch form
        self.params_edit = QTextEdit()
        self.params_edit.setPlainText(json.dumps(params or {}, indent=2))
        self.help_label = QLabel()
        self.help_label.setWordWrap(True)
        self.help_label.setStyleSheet("color: #667788;")
        json_page = QWidget()
        jl = QVBoxLayout(json_page)
        jl.setContentsMargins(0, 0, 0, 0)
        jl.addWidget(QLabel("Parameters (JSON):"))
        jl.addWidget(self.params_edit)
        jl.addWidget(self.help_label)

        self.launch_form = LaunchAppForm(self.name_edit)
        if type_ == "launch_app" and params:
            self.launch_form.set_params(params)

        self.stack = QStackedWidget()
        self.stack.addWidget(json_page)
        self.stack.addWidget(self.launch_form)

        self.type_combo.currentIndexChanged.connect(self._on_type_change)
        self._on_type_change()

        form = QFormLayout()
        form.addRow("Name:", self.name_edit)
        form.addRow("Type:", self.type_combo)

        btns = QDialogButtonBox(QDialogButtonBox.Save
                                | QDialogButtonBox.Cancel)
        btns.accepted.connect(self._validate_and_accept)
        btns.rejected.connect(self.reject)

        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(self.stack, 1)
        lay.addWidget(btns)

    def _on_type_change(self) -> None:
        t = self.type_combo.currentData()
        self.stack.setCurrentIndex(1 if t == "launch_app" else 0)
        self.help_label.setText(SIMPLE_PARAM_HELP.get(t, ""))

    def _validate_and_accept(self) -> None:
        if not self.name_edit.text().strip():
            QMessageBox.warning(self, "Invalid", "Name is required.")
            return
        t = self.type_combo.currentData()
        if t == "launch_app":
            params = self.launch_form.params()
        else:
            try:
                params = json.loads(self.params_edit.toPlainText() or "{}")
            except json.JSONDecodeError as e:
                QMessageBox.warning(self, "Invalid JSON", str(e))
                return
        err = self.executor.validate(t, params)
        if err:
            QMessageBox.warning(self, "Invalid action", err)
            return
        self.result_data = (self.name_edit.text().strip(), t, params)
        self.accept()


class WorkflowStepDialog(QDialog):
    """One workflow step: action / delay / condition wait / UI
    Automation (find, click, focus, type)."""

    # ui_element conditions are configured via the dedicated
    # "UI: wait for element" step kind (richer form); the condition
    # kind itself remains available to the engine and import files.
    CONDITIONS = [
        ("Application is running", "app_running"),
        ("Process exists", "process_exists"),
        ("A window of the application exists", "window_exists"),
        ("Window title matches", "window_title"),
    ]

    # populated by the UI Inspector's "Use this element in workflow"
    LAST_INSPECTED: dict | None = None

    def __init__(self, actions, step: dict | None = None, parent=None,
                 store_names: list[str] | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Workflow step")
        self._store_names = store_names or []

        self.kind_combo = QComboBox()
        self.kind_combo.addItem("Run an action", "action")
        self.kind_combo.addItem("Wait (fixed delay)", "delay")
        self.kind_combo.addItem("Wait for condition", "wait")
        self.kind_combo.addItem("UI: find element (store it)", "ui_find")
        self.kind_combo.addItem("UI: wait for element", "ui_wait")
        self.kind_combo.addItem("UI: click element", "ui_click")
        self.kind_combo.addItem("UI: focus element", "ui_focus")
        self.kind_combo.addItem("UI: type text into element", "ui_type")
        self.kind_combo.addItem("Data: set variable", "set_var")
        self.kind_combo.addItem("Data: read UI element text", "ui_read")
        self.kind_combo.addItem("Data: set clipboard", "set_clipboard")

        self.action_combo = QComboBox()
        for a in actions:
            if a.type == "workflow":
                continue  # workflows cannot nest workflows
            self.action_combo.addItem(f"{a.name}  ({a.type})", a.id)

        self.delay_spin = QSpinBox()
        self.delay_spin.setRange(1, 60_000)
        self.delay_spin.setValue(1000)
        self.delay_spin.setSuffix(" ms")

        # condition page
        self.cond_combo = QComboBox()
        for label, key in self.CONDITIONS:
            self.cond_combo.addItem(label, key)
        self.app_pick = QComboBox()
        self.app_pick.addItem("Pick an installed application…", "")
        try:
            from ..actions.discovery import discover_applications
            import os
            for app in discover_applications():
                self.app_pick.addItem(
                    app.name, os.path.basename(app.path).lower())
        except Exception:
            pass
        self.app_pick.currentIndexChanged.connect(self._on_app_pick)
        self.process_edit = QLineEdit()
        self.process_edit.setPlaceholderText("e.g. chrome.exe")
        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText("e.g. *YouTube*")
        self.timeout_spin = QSpinBox()
        self.timeout_spin.setRange(1, 120)
        self.timeout_spin.setValue(10)
        self.timeout_spin.setSuffix(" s")

        # UI-automation find/wait page
        from ..context.uia import CONTROL_TYPES
        self.ui_process = QLineEdit()
        self.ui_process.setPlaceholderText("e.g. chrome.exe (optional)")
        self.ui_title = QLineEdit()
        self.ui_title.setPlaceholderText("window title, e.g. *YouTube* "
                                         "(optional)")
        self.ui_ct = QComboBox()
        for ct in CONTROL_TYPES:
            self.ui_ct.addItem(ct, ct)
        self.ui_name = QLineEdit()
        self.ui_name.setPlaceholderText("element name, e.g. Search")
        self.ui_autoid = QLineEdit()
        self.ui_autoid.setPlaceholderText("automation id (optional)")
        self.ui_store = QLineEdit()
        self.ui_store.setPlaceholderText("store as, e.g. search_box")
        self.ui_timeout = QSpinBox()
        self.ui_timeout.setRange(1, 120)
        self.ui_timeout.setValue(10)
        self.ui_timeout.setSuffix(" s")
        use_btn = QPushButton("Use inspected element")
        use_btn.clicked.connect(self._use_inspected)
        test_el_btn = QPushButton("Test Element (read-only)")
        test_el_btn.clicked.connect(self._test_element)

        # UI click/focus/type page
        self.ui_ref = QComboBox()
        self.ui_ref.setEditable(True)
        for s in self._store_names:
            self.ui_ref.addItem(s)
        self.ui_text = QLineEdit()
        self.ui_text.setPlaceholderText(
            "text to type — {variable_name} is replaced at run time")

        # data pages: set variable / read element / set clipboard
        self.var_name = QLineEdit()
        self.var_name.setPlaceholderText("e.g. search_text")
        self.var_source = QComboBox()
        self.var_source.addItem("Text value", ("literal", "text"))
        self.var_source.addItem("Number value", ("literal", "number"))
        self.var_source.addItem("Yes/no value", ("literal", "boolean"))
        self.var_source.addItem("Another variable", ("variable", "text"))
        self.var_source.addItem("Clipboard text", ("clipboard", "text"))
        self.var_source.addItem("Active application",
                                ("active_app", "text"))
        self.var_source.addItem("Active window title",
                                ("active_window", "text"))
        self.var_value = QLineEdit()
        self.var_value.setPlaceholderText(
            "value — {variable_name} is replaced in text values")
        self.var_source.currentIndexChanged.connect(
            lambda: self.var_value.setVisible(
                self.var_source.currentData()[0] in ("literal",
                                                     "variable")))
        self.read_ref = QComboBox()
        self.read_ref.setEditable(True)
        for s in self._store_names:
            self.read_ref.addItem(s)
        self.read_var = QLineEdit()
        self.read_var.setPlaceholderText("store text as, e.g. result_text")
        self.clip_value = QLineEdit()
        self.clip_value.setPlaceholderText(
            "clipboard text — {variable_name} supported")

        self.stack = QStackedWidget()
        pa = QWidget()
        la = QFormLayout(pa)
        la.setContentsMargins(0, 0, 0, 0)
        la.addRow("Action:", self.action_combo)
        pd = QWidget()
        ld = QFormLayout(pd)
        ld.setContentsMargins(0, 0, 0, 0)
        ld.addRow("Duration:", self.delay_spin)
        pw = QWidget()
        lw = QFormLayout(pw)
        lw.setContentsMargins(0, 0, 0, 0)
        lw.addRow("Condition:", self.cond_combo)
        lw.addRow("Application:", self.app_pick)
        self._process_row = QLabel("Process:")
        lw.addRow(self._process_row, self.process_edit)
        self._title_row = QLabel("Window title:")
        lw.addRow(self._title_row, self.title_edit)
        lw.addRow("Timeout:", self.timeout_spin)
        pf = QWidget()
        lf = QFormLayout(pf)
        lf.setContentsMargins(0, 0, 0, 0)
        lf.addRow("Application (process):", self.ui_process)
        lf.addRow("Window title:", self.ui_title)
        lf.addRow("Control type:", self.ui_ct)
        lf.addRow("Name:", self.ui_name)
        lf.addRow("Automation ID:", self.ui_autoid)
        lf.addRow("Store as:", self.ui_store)
        lf.addRow("Timeout:", self.ui_timeout)
        brow = QHBoxLayout()
        brow.addWidget(use_btn)
        brow.addWidget(test_el_btn)
        lf.addRow(brow)
        pr = QWidget()
        lr = QFormLayout(pr)
        lr.setContentsMargins(0, 0, 0, 0)
        lr.addRow("Stored element:", self.ui_ref)
        self._text_row = QLabel("Text:")
        lr.addRow(self._text_row, self.ui_text)
        pv = QWidget()
        lv = QFormLayout(pv)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.addRow("Variable name:", self.var_name)
        lv.addRow("Set from:", self.var_source)
        lv.addRow("Value:", self.var_value)
        pg = QWidget()
        lg = QFormLayout(pg)
        lg.setContentsMargins(0, 0, 0, 0)
        lg.addRow("Stored element:", self.read_ref)
        lg.addRow("Store text as:", self.read_var)
        pc = QWidget()
        lc = QFormLayout(pc)
        lc.setContentsMargins(0, 0, 0, 0)
        lc.addRow("Clipboard text:", self.clip_value)
        self.stack.addWidget(pa)   # 0 action
        self.stack.addWidget(pd)   # 1 delay
        self.stack.addWidget(pw)   # 2 wait condition
        self.stack.addWidget(pf)   # 3 ui find/wait
        self.stack.addWidget(pr)   # 4 ui click/focus/type
        self.stack.addWidget(pv)   # 5 set variable
        self.stack.addWidget(pg)   # 6 read ui element
        self.stack.addWidget(pc)   # 7 set clipboard
        self.kind_combo.currentIndexChanged.connect(self._on_kind_change)
        self.cond_combo.currentIndexChanged.connect(self._on_cond_change)
        self._on_cond_change()

        if step:
            t = step.get("type")
            if t == "delay":
                self.kind_combo.setCurrentIndex(1)
                self.delay_spin.setValue(int(step.get("ms", 1000)))
            elif t == "wait":
                self.kind_combo.setCurrentIndex(2)
                ci = self.cond_combo.findData(step.get("condition"))
                self.cond_combo.setCurrentIndex(max(0, ci))
                self.process_edit.setText(step.get("process", ""))
                self.title_edit.setText(step.get("title", ""))
                self.timeout_spin.setValue(
                    max(1, int(step.get("timeout_ms", 10_000)) // 1000))
            elif t in ("ui_find", "ui_wait"):
                ki = self.kind_combo.findData(t)
                self.kind_combo.setCurrentIndex(ki)
                self.ui_process.setText(step.get("process", ""))
                self.ui_title.setText(step.get("title", ""))
                ci = self.ui_ct.findData(step.get("control_type") or "Any")
                self.ui_ct.setCurrentIndex(max(0, ci))
                self.ui_name.setText(step.get("name", ""))
                self.ui_autoid.setText(step.get("automation_id", ""))
                self.ui_store.setText(step.get("store", ""))
                self.ui_timeout.setValue(
                    max(1, int(step.get("timeout_ms", 10_000)) // 1000))
            elif t in ("ui_click", "ui_focus", "ui_type"):
                ki = self.kind_combo.findData(t)
                self.kind_combo.setCurrentIndex(ki)
                self.ui_ref.setCurrentText(step.get("ref", ""))
                self.ui_text.setText(step.get("text", ""))
            elif t == "set_var":
                self.kind_combo.setCurrentIndex(
                    self.kind_combo.findData(t))
                self.var_name.setText(step.get("name", ""))
                want = (step.get("source", "literal"),
                        step.get("value_type", "text"))
                for k in range(self.var_source.count()):
                    if self.var_source.itemData(k) == want:
                        self.var_source.setCurrentIndex(k)
                        break
                v = step.get("value", step.get("source_var", ""))
                self.var_value.setText(str(v))
            elif t == "ui_read":
                self.kind_combo.setCurrentIndex(
                    self.kind_combo.findData(t))
                self.read_ref.setCurrentText(step.get("ref", ""))
                self.read_var.setText(step.get("store_var", ""))
            elif t == "set_clipboard":
                self.kind_combo.setCurrentIndex(
                    self.kind_combo.findData(t))
                self.clip_value.setText(step.get("value", ""))
            else:
                i = self.action_combo.findData(step.get("action_id"))
                self.action_combo.setCurrentIndex(max(0, i))
        self._on_kind_change()

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self._accept)
        btns.rejected.connect(self.reject)

        form = QFormLayout(self)
        form.addRow("Step type:", self.kind_combo)
        form.addRow(self.stack)
        form.addRow(btns)

    def _on_kind_change(self) -> None:
        kind = self.kind_combo.currentData()
        page = {"action": 0, "delay": 1, "wait": 2, "ui_find": 3,
                "ui_wait": 3, "ui_click": 4, "ui_focus": 4,
                "ui_type": 4, "set_var": 5, "ui_read": 6,
                "set_clipboard": 7}[kind]
        self.stack.setCurrentIndex(page)
        is_type = kind == "ui_type"
        self.ui_text.setVisible(is_type)
        self._text_row.setVisible(is_type)

    def _use_inspected(self) -> None:
        info = WorkflowStepDialog.LAST_INSPECTED
        if not info:
            QMessageBox.information(
                self, "No inspected element",
                "Open the UI Inspector first, hover the element you "
                "want and press 'Use this element in workflow'.")
            return
        self.ui_process.setText(info.get("process", ""))
        self.ui_title.setText(info.get("window_title", ""))
        ci = self.ui_ct.findData(info.get("control_type") or "Any")
        self.ui_ct.setCurrentIndex(max(0, ci))
        self.ui_name.setText(info.get("name", ""))
        self.ui_autoid.setText(info.get("automation_id", ""))

    def _test_element(self) -> None:
        """Read-only element test: search now, show what was found.
        Never clicks or modifies anything."""
        from ..context import uia
        crit = self._ui_criteria()
        err = None
        if not (crit["name"] or crit["automation_id"]
                or (crit["control_type"] and
                    crit["control_type"] != "Any")):
            err = "Set a name, automation id or control type first."
        if err:
            QMessageBox.warning(self, "Test element", err)
            return
        hit = uia.find_element(crit)
        if hit is None:
            QMessageBox.warning(
                self, "Test element",
                "✕ Element not found.\n\nThe application may not be "
                "running, the filters may not match, or the application "
                "does not expose UI Automation information.")
            return
        _, info = hit
        QMessageBox.information(
            self, "Test element",
            "✓ ELEMENT FOUND\n\n"
            f"Application: {info.process}\n"
            f"Window: {info.window_title}\n"
            f"Control: {info.control_type}\n"
            f"Name: {info.name}\n"
            f"Automation ID: {info.automation_id or '—'}\n"
            f"Class: {info.class_name or '—'}\n"
            f"Enabled: {info.enabled}   Visible: {info.visible}\n"
            f"Rectangle: {info.rect}")

    def _ui_criteria(self) -> dict:
        ct = self.ui_ct.currentData()
        return {"process": self.ui_process.text().strip(),
                "title": self.ui_title.text().strip(),
                "control_type": "" if ct == "Any" else ct,
                "name": self.ui_name.text().strip(),
                "automation_id": self.ui_autoid.text().strip()}

    def _on_app_pick(self) -> None:
        proc = self.app_pick.currentData()
        if proc:
            self.process_edit.setText(proc)

    def _on_cond_change(self) -> None:
        kind = self.cond_combo.currentData()
        needs_title = kind in ("window_exists", "window_title")
        needs_process = kind != "window_title"
        self.title_edit.setVisible(needs_title)
        self._title_row.setVisible(needs_title)
        self.process_edit.setVisible(needs_process)
        self._process_row.setVisible(needs_process)
        self.app_pick.setVisible(needs_process)

    def _accept(self) -> None:
        kind = self.kind_combo.currentData()
        if kind == "action" and self.action_combo.currentData() is None:
            QMessageBox.warning(self, "No action",
                                "Create a named action first.")
            return
        if kind == "wait":
            from ..context.conditions import validate as validate_cond
            err = validate_cond(self._condition_step())
            if err:
                QMessageBox.warning(self, "Invalid condition", err)
                return
        if kind in ("ui_find", "ui_wait"):
            c = self._ui_criteria()
            if not (c["name"] or c["automation_id"] or c["control_type"]):
                QMessageBox.warning(
                    self, "Invalid UI step",
                    "Set a name, automation id or control type.")
                return
        if kind in ("ui_click", "ui_focus", "ui_type") \
                and not self.ui_ref.currentText().strip():
            QMessageBox.warning(self, "Invalid UI step",
                                "Pick the stored element name from an "
                                "earlier Find step.")
            return
        if kind in ("set_var", "ui_read"):
            from ..context.conditions import VAR_NAME_RE
            name = (self.var_name.text() if kind == "set_var"
                    else self.read_var.text()).strip()
            if not VAR_NAME_RE.match(name):
                QMessageBox.warning(
                    self, "Invalid variable name",
                    "Use lowercase letters, digits and underscores, "
                    "starting with a letter (e.g. search_text).")
                return
            if kind == "ui_read" and not self.read_ref.currentText().strip():
                QMessageBox.warning(self, "Invalid step",
                                    "Pick the stored element to read.")
                return
        self.accept()

    def _condition_step(self) -> dict:
        return {"type": "wait",
                "condition": self.cond_combo.currentData(),
                "process": self.process_edit.text().strip(),
                "title": self.title_edit.text().strip(),
                "timeout_ms": self.timeout_spin.value() * 1000}

    def step(self) -> dict:
        kind = self.kind_combo.currentData()
        if kind == "delay":
            return {"type": "delay", "ms": self.delay_spin.value()}
        if kind == "wait":
            return self._condition_step()
        if kind in ("ui_find", "ui_wait"):
            c = self._ui_criteria()
            return {"type": kind, **c,
                    "store": self.ui_store.text().strip(),
                    "timeout_ms": self.ui_timeout.value() * 1000}
        if kind in ("ui_click", "ui_focus", "ui_type"):
            s = {"type": kind, "ref": self.ui_ref.currentText().strip()}
            if kind == "ui_type":
                s["text"] = self.ui_text.text()
            return s
        if kind == "set_var":
            source, vtype = self.var_source.currentData()
            s = {"type": "set_var", "name": self.var_name.text().strip(),
                 "source": source, "value_type": vtype}
            if source == "variable":
                s["source_var"] = self.var_value.text().strip()
            elif source == "literal":
                s["value"] = self.var_value.text()
            return s
        if kind == "ui_read":
            return {"type": "ui_read",
                    "ref": self.read_ref.currentText().strip(),
                    "store_var": self.read_var.text().strip()}
        if kind == "set_clipboard":
            return {"type": "set_clipboard",
                    "value": self.clip_value.text()}
        return {"type": "action", "action_id": self.action_combo.currentData()}


class ConditionDialog(QDialog):
    """Edits ONE condition (used inside If/Else steps)."""

    KINDS = [
        ("Application is running", "app_running"),
        ("Process exists", "process_exists"),
        ("A window of the application exists", "window_exists"),
        ("Window title matches", "window_title"),
        ("UI element exists", "ui_element"),
        ("Variable comparison", "variable"),
    ]

    OPS = [
        ("text equals", "equals"), ("text is not", "not_equals"),
        ("text contains", "contains"), ("starts with", "starts_with"),
        ("ends with", "ends_with"), ("is empty", "is_empty"),
        ("is not empty", "is_not_empty"),
        ("number =", "eq"), ("number ≠", "ne"), ("number >", "gt"),
        ("number <", "lt"), ("number ≥", "ge"), ("number ≤", "le"),
        ("is true (yes)", "is_true"), ("is false (no)", "is_false"),
    ]

    def __init__(self, cond: dict | None = None, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Condition")
        from ..context.uia import CONTROL_TYPES

        self.kind_combo = QComboBox()
        for label, key in self.KINDS:
            self.kind_combo.addItem(label, key)
        self.process_edit = QLineEdit()
        self.process_edit.setPlaceholderText("e.g. chrome.exe")
        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText("e.g. *YouTube*")
        self.ct_combo = QComboBox()
        for ct in CONTROL_TYPES:
            self.ct_combo.addItem(ct, ct)
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("element name, e.g. Search")
        self.autoid_edit = QLineEdit()
        self.enabled_check = QCheckBox("element must be enabled")
        self.visible_check = QCheckBox("element must be visible")
        self.var_edit = QLineEdit()
        self.var_edit.setPlaceholderText("variable name, e.g. result_text")
        self.op_combo = QComboBox()
        for label, key in self.OPS:
            self.op_combo.addItem(label, key)
        self.value_edit = QLineEdit()
        self.value_edit.setPlaceholderText("comparison value")

        form = QFormLayout(self)
        form.addRow("Condition:", self.kind_combo)
        self._rows = {}
        for key, label, w in (
                ("process", "Process:", self.process_edit),
                ("title", "Window title:", self.title_edit),
                ("ct", "Control type:", self.ct_combo),
                ("name", "Element name:", self.name_edit),
                ("autoid", "Automation ID:", self.autoid_edit),
                ("en", "", self.enabled_check),
                ("vis", "", self.visible_check),
                ("var", "Variable:", self.var_edit),
                ("op", "Comparison:", self.op_combo),
                ("val", "Value:", self.value_edit)):
            lbl = QLabel(label)
            form.addRow(lbl, w)
            self._rows[key] = (lbl, w)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)
        form.addRow(bb)
        self.kind_combo.currentIndexChanged.connect(self._on_kind)

        if cond:
            ki = self.kind_combo.findData(cond.get("condition"))
            self.kind_combo.setCurrentIndex(max(0, ki))
            self.process_edit.setText(cond.get("process", ""))
            self.title_edit.setText(cond.get("title", ""))
            ci = self.ct_combo.findData(cond.get("control_type") or "Any")
            self.ct_combo.setCurrentIndex(max(0, ci))
            self.name_edit.setText(cond.get("name", ""))
            self.autoid_edit.setText(cond.get("automation_id", ""))
            self.enabled_check.setChecked(bool(cond.get("require_enabled")))
            self.visible_check.setChecked(bool(cond.get("require_visible")))
            self.var_edit.setText(cond.get("var", ""))
            oi = self.op_combo.findData(cond.get("op"))
            self.op_combo.setCurrentIndex(max(0, oi))
            self.value_edit.setText(str(cond.get("value", "")))
        self._on_kind()

    def _on_kind(self) -> None:
        kind = self.kind_combo.currentData()
        show = {
            "app_running": ("process",),
            "process_exists": ("process",),
            "window_exists": ("process", "title"),
            "window_title": ("title",),
            "ui_element": ("process", "title", "ct", "name", "autoid",
                           "en", "vis"),
            "variable": ("var", "op", "val"),
        }[kind]
        for key, (lbl, w) in self._rows.items():
            vis = key in show
            lbl.setVisible(vis)
            w.setVisible(vis)

    def condition(self) -> dict:
        kind = self.kind_combo.currentData()
        ct = self.ct_combo.currentData()
        c = {"condition": kind,
             "process": self.process_edit.text().strip(),
             "title": self.title_edit.text().strip()}
        if kind == "ui_element":
            c.update({"control_type": "" if ct == "Any" else ct,
                      "name": self.name_edit.text().strip(),
                      "automation_id": self.autoid_edit.text().strip(),
                      "require_enabled": self.enabled_check.isChecked(),
                      "require_visible": self.visible_check.isChecked()})
        if kind == "variable":
            c.update({"var": self.var_edit.text().strip(),
                      "op": self.op_combo.currentData(),
                      "value": self.value_edit.text()})
        return c

    def _accept(self) -> None:
        from ..context.conditions import validate as validate_cond
        err = validate_cond(self.condition())
        if err:
            QMessageBox.warning(self, "Invalid condition", err)
            return
        self.accept()


class BranchList(QWidget):
    """Step list for one If/Else branch — same step editors as the main
    builder, including nested If/Else."""

    def __init__(self, ctl, title: str, steps: list[dict],
                 store_names, parent=None) -> None:
        super().__init__(parent)
        self.ctl = ctl
        self.steps = list(steps)
        self._store_names = store_names   # callable → current store names
        self.listw = QListWidget()
        self.listw.setMinimumHeight(90)
        self.listw.itemDoubleClicked.connect(lambda _: self._edit())
        add_btn = QPushButton("Add step")
        add_btn.clicked.connect(self._add)
        ctrl_btn = QPushButton("Control flow ▾")
        ctrl_btn.clicked.connect(lambda: control_flow_menu(
            self, ctrl_btn, self._append_step))
        edit_btn = QPushButton("Edit")
        edit_btn.clicked.connect(self._edit)
        del_btn = QPushButton("Delete")
        del_btn.clicked.connect(self._del)
        up_btn = QPushButton("↑")
        up_btn.clicked.connect(lambda: self._move(-1))
        down_btn = QPushButton("↓")
        down_btn.clicked.connect(lambda: self._move(1))
        row = QHBoxLayout()
        row.addWidget(QLabel(title))
        row.addStretch(1)
        for b in (add_btn, ctrl_btn, edit_btn, del_btn, up_btn, down_btn):
            row.addWidget(b)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 0, 0, 0)   # indent = visible nesting
        lay.addLayout(row)
        lay.addWidget(self.listw)
        self.refresh()

    def refresh(self) -> None:
        self.listw.clear()
        for i, s in enumerate(self.steps, 1):
            self.listw.addItem(f"{i}.  {step_label(self.ctl, s)}")

    def _add(self) -> None:
        dlg = WorkflowStepDialog(self.ctl.profile_manager.actions.all(),
                                 parent=self,
                                 store_names=self._store_names())
        if dlg.exec():
            self.steps.append(dlg.step())
            self.refresh()

    def _append_step(self, kind: str) -> None:
        dlg = control_flow_dialog(self.ctl, kind, None, self,
                                  self._store_names)
        if dlg.exec():
            self.steps.append(dlg.step())
            self.refresh()

    def _edit(self) -> None:
        i = self.listw.currentRow()
        if i < 0:
            return
        s = self.steps[i]
        if s.get("type") in CONTROL_FLOW_TYPES:
            dlg = control_flow_dialog(self.ctl, s["type"], s, self,
                                      self._store_names)
        else:
            dlg = WorkflowStepDialog(
                self.ctl.profile_manager.actions.all(), s, parent=self,
                store_names=self._store_names())
        if dlg.exec():
            self.steps[i] = dlg.step()
            self.refresh()

    def _del(self) -> None:
        i = self.listw.currentRow()
        if i >= 0:
            self.steps.pop(i)
            self.refresh()

    def _move(self, d: int) -> None:
        i = self.listw.currentRow()
        j = i + d
        if i < 0 or not (0 <= j < len(self.steps)):
            return
        self.steps[i], self.steps[j] = self.steps[j], self.steps[i]
        self.refresh()
        self.listw.setCurrentRow(j)


class IfStepDialog(QDialog):
    """Visual If/Else editor: condition group (ALL/ANY), optional wait,
    THEN and ELSE branches. Declarative only — no expressions."""

    def __init__(self, ctl, step: dict | None = None, parent=None,
                 store_names=None) -> None:
        super().__init__(parent)
        self.ctl = ctl
        self.setWindowTitle("If / Else step")
        self.resize(640, 620)
        self._store_names = store_names or (lambda: [])
        step = step or {}

        self.mode_combo = QComboBox()
        self.mode_combo.addItem("ALL conditions are true", "all")
        self.mode_combo.addItem("ANY condition is true", "any")
        mi = self.mode_combo.findData(step.get("mode", "all"))
        self.mode_combo.setCurrentIndex(max(0, mi))

        self.conds: list[dict] = list(step.get("conditions", []))
        self.cond_list = QListWidget()
        self.cond_list.setMaximumHeight(80)
        self.cond_list.itemDoubleClicked.connect(lambda _: self._edit_cond())
        cadd = QPushButton("Add condition")
        cadd.clicked.connect(self._add_cond)
        cedit = QPushButton("Edit")
        cedit.clicked.connect(self._edit_cond)
        cdel = QPushButton("Remove")
        cdel.clicked.connect(self._del_cond)
        ctest = QPushButton("Test condition (read-only)")
        ctest.clicked.connect(self._test_cond)
        crow = QHBoxLayout()
        for b in (cadd, cedit, cdel, ctest):
            crow.addWidget(b)
        crow.addStretch(1)

        self.wait_spin = QSpinBox()
        self.wait_spin.setRange(0, 120)
        self.wait_spin.setSuffix(" s")
        self.wait_spin.setValue(int(step.get("wait_ms", 0)) // 1000)
        self.wait_spin.setSpecialValueText("check immediately")
        self.timeout_combo = QComboBox()
        self.timeout_combo.addItem("use the ELSE branch", "else")
        self.timeout_combo.addItem("fail the workflow", "fail")
        ti = self.timeout_combo.findData(step.get("on_timeout", "else"))
        self.timeout_combo.setCurrentIndex(max(0, ti))

        self.then_list = BranchList(ctl, "THEN — when true:",
                                    step.get("then", []),
                                    self._store_names, self)
        self.else_list = BranchList(ctl, "ELSE — when false:",
                                    step.get("else", []),
                                    self._store_names, self)

        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)

        form = QFormLayout()
        form.addRow("Branch when:", self.mode_combo)
        form.addRow("Wait up to:", self.wait_spin)
        form.addRow("If still false after waiting:", self.timeout_combo)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(QLabel("Conditions:"))
        lay.addLayout(crow)
        lay.addWidget(self.cond_list)
        lay.addWidget(self.then_list, 1)
        lay.addWidget(self.else_list, 1)
        lay.addWidget(bb)
        self._refresh_conds()

    def _refresh_conds(self) -> None:
        from ..context.conditions import describe
        self.cond_list.clear()
        for c in self.conds:
            self.cond_list.addItem(describe(c))

    def _add_cond(self) -> None:
        dlg = ConditionDialog(parent=self)
        if dlg.exec():
            self.conds.append(dlg.condition())
            self._refresh_conds()

    def _edit_cond(self) -> None:
        i = self.cond_list.currentRow()
        if i < 0:
            return
        dlg = ConditionDialog(self.conds[i], parent=self)
        if dlg.exec():
            self.conds[i] = dlg.condition()
            self._refresh_conds()

    def _del_cond(self) -> None:
        i = self.cond_list.currentRow()
        if i >= 0:
            self.conds.pop(i)
            self._refresh_conds()

    def _test_cond(self) -> None:
        """Read-only evaluation of the current condition group. Nothing
        executes."""
        if not self.conds:
            QMessageBox.information(self, "Test condition",
                                    "Add a condition first.")
            return
        from ..context.conditions import check, describe
        lines = []
        results = []
        unknown = False
        for c in self.conds:
            try:
                ok = check(c)
                results.append(ok)
                lines.append(f"{'✓' if ok else '✕'}  {describe(c)}")
            except ValueError:
                unknown = True
                lines.append(f"?  {describe(c)}  (depends on a run-time "
                             "variable)")
        mode = self.mode_combo.currentData()
        if unknown:
            header = "? CANNOT EVALUATE NOW"
        else:
            verdict = all(results) if mode == "all" else any(results)
            header = ("✓ CONDITION TRUE" if verdict
                      else "✕ CONDITION FALSE")
        QMessageBox.information(
            self, "Test condition",
            f"{header}  ({mode.upper()})\n\n" + "\n".join(lines))

    def _accept(self) -> None:
        if not self.conds:
            QMessageBox.warning(self, "Invalid", "Add at least one "
                                "condition.")
            return
        if not self.then_list.steps:
            QMessageBox.warning(self, "Invalid", "The THEN branch needs "
                                "at least one step.")
            return
        self.accept()

    def step(self) -> dict:
        return {"type": "if",
                "conditions": list(self.conds),
                "mode": self.mode_combo.currentData(),
                "wait_ms": self.wait_spin.value() * 1000,
                "on_timeout": self.timeout_combo.currentData(),
                "then": list(self.then_list.steps),
                "else": list(self.else_list.steps)}


# control-flow step kinds share one menu (builder + nested branches) and
# one editor dispatch — visually consistent, never a programming IDE
CONTROL_FLOW_TYPES = ("if", "retry", "repeat", "repeat_until")
CONTROL_FLOW_MENU = [("If / Else…", "if"),
                     ("Retry…", "retry"),
                     ("Repeat N times…", "repeat"),
                     ("Repeat until condition…", "repeat_until")]


def control_flow_menu(parent, button, on_pick) -> None:
    menu = QMenu(parent)
    for label, kind in CONTROL_FLOW_MENU:
        menu.addAction(label, lambda k=kind: on_pick(k))
    menu.exec(button.mapToGlobal(button.rect().bottomLeft()))


def control_flow_dialog(ctl, kind: str, step: dict | None, parent,
                        store_names):
    cls = {"if": IfStepDialog, "retry": RetryStepDialog,
           "repeat": RepeatStepDialog,
           "repeat_until": RepeatUntilStepDialog}[kind]
    return cls(ctl, step, parent=parent, store_names=store_names)


class ConditionListWidget(QWidget):
    """Compact add/edit/remove editor for a condition list — reuses
    ConditionDialog, i.e. the ONE existing condition system."""

    def __init__(self, title: str, conds: list[dict], parent=None) -> None:
        super().__init__(parent)
        self.conds: list[dict] = list(conds)
        self.listw = QListWidget()
        self.listw.setMaximumHeight(70)
        self.listw.itemDoubleClicked.connect(lambda _: self._edit())
        add_b = QPushButton("Add condition")
        add_b.clicked.connect(self._add)
        edit_b = QPushButton("Edit")
        edit_b.clicked.connect(self._edit)
        del_b = QPushButton("Remove")
        del_b.clicked.connect(self._del)
        row = QHBoxLayout()
        row.addWidget(QLabel(title))
        row.addStretch(1)
        for b in (add_b, edit_b, del_b):
            row.addWidget(b)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addLayout(row)
        lay.addWidget(self.listw)
        self._refresh()

    def _refresh(self) -> None:
        from ..context.conditions import describe
        self.listw.clear()
        for c in self.conds:
            self.listw.addItem(describe(c))

    def _add(self) -> None:
        dlg = ConditionDialog(parent=self)
        if dlg.exec():
            self.conds.append(dlg.condition())
            self._refresh()

    def _edit(self) -> None:
        i = self.listw.currentRow()
        if i < 0:
            return
        dlg = ConditionDialog(self.conds[i], parent=self)
        if dlg.exec():
            self.conds[i] = dlg.condition()
            self._refresh()

    def _del(self) -> None:
        i = self.listw.currentRow()
        if i >= 0:
            self.conds.pop(i)
            self._refresh()


class RetryStepDialog(QDialog):
    """RETRY block editor: bounded attempts, delay between attempts, the
    steps to try, an optional success condition, and explicit failure
    behavior. Declarative only — never a loop the user can unbound."""

    def __init__(self, ctl, step: dict | None = None, parent=None,
                 store_names=None) -> None:
        super().__init__(parent)
        self.ctl = ctl
        self.setWindowTitle("Retry step")
        self.resize(620, 560)
        self._store_names = store_names or (lambda: [])
        step = step or {}

        self.attempts_spin = QSpinBox()
        self.attempts_spin.setRange(1, 20)
        self.attempts_spin.setValue(int(step.get("attempts", 3)))
        self.delay_spin = QSpinBox()
        self.delay_spin.setRange(0, 60_000)
        self.delay_spin.setSuffix(" ms")
        self.delay_spin.setValue(int(step.get("delay_ms", 500)))
        self.fail_combo = QComboBox()
        self.fail_combo.addItem("Fail the workflow", "fail")
        self.fail_combo.addItem("Continue with the next step", "continue")
        self.fail_combo.addItem("Run fallback steps, then continue",
                                "fallback")
        fi = self.fail_combo.findData(step.get("on_fail", "fail"))
        self.fail_combo.setCurrentIndex(max(0, fi))
        self.fail_combo.currentIndexChanged.connect(self._on_fail_change)

        self.steps_list = BranchList(ctl, "Steps to try:",
                                     step.get("steps", []),
                                     self._store_names, self)
        self.until_list = ConditionListWidget(
            "Succeed only when (optional):", step.get("until", []), self)
        self.fallback_list = BranchList(ctl, "On failure, run instead:",
                                        step.get("fallback", []),
                                        self._store_names, self)

        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)

        form = QFormLayout()
        form.addRow("Attempts (max 20):", self.attempts_spin)
        form.addRow("Delay between attempts:", self.delay_spin)
        form.addRow("If every attempt fails:", self.fail_combo)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(self.steps_list, 1)
        lay.addWidget(self.until_list)
        lay.addWidget(self.fallback_list, 1)
        lay.addWidget(bb)
        self._on_fail_change()

    def _on_fail_change(self) -> None:
        self.fallback_list.setVisible(
            self.fail_combo.currentData() == "fallback")

    def _accept(self) -> None:
        if not self.steps_list.steps:
            QMessageBox.warning(self, "Invalid", "Add at least one step "
                                "to try.")
            return
        if self.fail_combo.currentData() == "fallback" \
                and not self.fallback_list.steps:
            QMessageBox.warning(self, "Invalid", "Add at least one "
                                "fallback step (or pick another failure "
                                "behavior).")
            return
        self.accept()

    def step(self) -> dict:
        s = {"type": "retry",
             "attempts": self.attempts_spin.value(),
             "delay_ms": self.delay_spin.value(),
             "on_fail": self.fail_combo.currentData(),
             "steps": list(self.steps_list.steps)}
        if self.until_list.conds:
            s["until"] = list(self.until_list.conds)
        if s["on_fail"] == "fallback":
            s["fallback"] = list(self.fallback_list.steps)
        return s


class RepeatStepDialog(QDialog):
    """REPEAT N TIMES editor — fixed, bounded repetition."""

    def __init__(self, ctl, step: dict | None = None, parent=None,
                 store_names=None) -> None:
        super().__init__(parent)
        self.ctl = ctl
        self.setWindowTitle("Repeat step")
        self.resize(560, 420)
        self._store_names = store_names or (lambda: [])
        step = step or {}

        limit = getattr(ctl.workflow_repo, "max_repeat", 100)
        self.count_spin = QSpinBox()
        self.count_spin.setRange(1, max(1, min(int(limit), 100)))
        self.count_spin.setValue(int(step.get("count", 3)))

        self.steps_list = BranchList(ctl, "Steps to repeat:",
                                     step.get("steps", []),
                                     self._store_names, self)

        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)

        form = QFormLayout()
        form.addRow(f"Repeat count (max {self.count_spin.maximum()}):",
                    self.count_spin)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(self.steps_list, 1)
        lay.addWidget(bb)

    def _accept(self) -> None:
        if not self.steps_list.steps:
            QMessageBox.warning(self, "Invalid", "Add at least one step "
                                "to repeat.")
            return
        self.accept()

    def step(self) -> dict:
        return {"type": "repeat",
                "count": self.count_spin.value(),
                "steps": list(self.steps_list.steps)}


class RepeatUntilStepDialog(QDialog):
    """REPEAT UNTIL CONDITION editor — the condition group is checked
    BEFORE each pass; both an iteration bound and a time limit are
    mandatory, and hitting either is an explicit TIMEOUT."""

    def __init__(self, ctl, step: dict | None = None, parent=None,
                 store_names=None) -> None:
        super().__init__(parent)
        self.ctl = ctl
        self.setWindowTitle("Repeat until condition")
        self.resize(620, 520)
        self._store_names = store_names or (lambda: [])
        step = step or {}

        self.mode_combo = QComboBox()
        self.mode_combo.addItem("ALL conditions are true", "all")
        self.mode_combo.addItem("ANY condition is true", "any")
        mi = self.mode_combo.findData(step.get("mode", "all"))
        self.mode_combo.setCurrentIndex(max(0, mi))
        self.iter_spin = QSpinBox()
        self.iter_spin.setRange(1, 100)
        self.iter_spin.setValue(int(step.get("max_iterations", 10)))
        self.delay_spin = QSpinBox()
        self.delay_spin.setRange(0, 60_000)
        self.delay_spin.setSuffix(" ms")
        self.delay_spin.setValue(int(step.get("delay_ms", 500)))
        self.timeout_spin = QSpinBox()
        self.timeout_spin.setRange(1, 120)
        self.timeout_spin.setSuffix(" s")
        self.timeout_spin.setValue(
            max(1, int(step.get("timeout_ms", 10_000)) // 1000))

        self.cond_list = ConditionListWidget(
            "Stop repeating when:", step.get("conditions", []), self)
        self.steps_list = BranchList(ctl, "Steps each iteration:",
                                     step.get("steps", []),
                                     self._store_names, self)

        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)

        form = QFormLayout()
        form.addRow("Stop when:", self.mode_combo)
        form.addRow("Maximum iterations (max 100):", self.iter_spin)
        form.addRow("Delay between iterations:", self.delay_spin)
        form.addRow("Time limit (required):", self.timeout_spin)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(self.cond_list)
        lay.addWidget(self.steps_list, 1)
        lay.addWidget(bb)

    def _accept(self) -> None:
        if not self.cond_list.conds:
            QMessageBox.warning(self, "Invalid", "Add at least one "
                                "condition to stop on.")
            return
        if not self.steps_list.steps:
            QMessageBox.warning(self, "Invalid", "Add at least one step "
                                "to run each iteration.")
            return
        self.accept()

    def step(self) -> dict:
        return {"type": "repeat_until",
                "conditions": list(self.cond_list.conds),
                "mode": self.mode_combo.currentData(),
                "max_iterations": self.iter_spin.value(),
                "delay_ms": self.delay_spin.value(),
                "timeout_ms": self.timeout_spin.value() * 1000,
                "steps": list(self.steps_list.steps)}


def step_label(ctl, s: dict) -> str:
    """Shared step description used by the builder and branch lists."""
    t = s.get("type")
    if t == "if":
        from ..context.conditions import describe
        desc = " / ".join(describe(c) for c in s.get("conditions", []))
        return (f"IF {s.get('mode', 'all').upper()}: {desc}   "
                f"({len(s.get('then', []))} then / "
                f"{len(s.get('else', []))} else)")
    if t == "delay":
        return f"Wait {int(s.get('ms', 0))} ms"
    if t == "wait":
        from ..context.conditions import describe
        return (f"Wait for {describe(s)} "
                f"(timeout {int(s.get('timeout_ms', 0)) // 1000} s)")
    if t in ("ui_find", "ui_wait"):
        from ..context.uia import describe_criteria
        verb = "Find" if t == "ui_find" else "Wait for"
        store = s.get("store")
        return (f"{verb} UI {describe_criteria(s)}"
                + (f"  → {store}" if store else ""))
    if t == "ui_click":
        return f"Click UI element '{s.get('ref', '')}'"
    if t == "ui_focus":
        return f"Focus UI element '{s.get('ref', '')}'"
    if t == "ui_type":
        return f"Type into UI element '{s.get('ref', '')}'"
    if t == "set_var":
        src = s.get("source", "literal")
        pretty = {"literal": "value", "variable": "another variable",
                  "clipboard": "clipboard",
                  "active_app": "active application",
                  "active_window": "active window title"}
        return f"Set {{{s.get('name', '')}}} from {pretty.get(src, src)}"
    if t == "ui_read":
        return (f"Read UI element '{s.get('ref', '')}' → "
                f"{{{s.get('store_var', '')}}}")
    if t == "set_clipboard":
        return "Set clipboard from workflow"
    if t == "retry":
        extra = ""
        if s.get("on_fail") == "continue":
            extra = ", then continue"
        elif s.get("on_fail") == "fallback":
            extra = f", else {len(s.get('fallback', []))} fallback steps"
        return (f"RETRY up to {int(s.get('attempts', 1))} attempts   "
                f"({len(s.get('steps', []))} steps{extra})")
    if t == "repeat":
        return (f"REPEAT {int(s.get('count', 1))} times   "
                f"({len(s.get('steps', []))} steps)")
    if t == "repeat_until":
        from ..context.conditions import describe
        desc = " / ".join(describe(c) for c in s.get("conditions", []))
        return (f"REPEAT UNTIL {s.get('mode', 'all').upper()}: {desc}   "
                f"(≤{int(s.get('max_iterations', 0))} iterations, "
                f"≤{int(s.get('timeout_ms', 0)) // 1000} s)")
    a = ctl.profile_manager.actions.get(s.get("action_id", 0))
    return a.name if a else "(missing action)"


class WorkflowBuilderDialog(QDialog):
    """Visual multi-step workflow editor — no JSON exposed.

    One-stop editing: name, description, enabled state, ordered steps,
    and an optional trigger (gesture + profile). The trigger is stored
    as a normal rule through the existing rule engine — precedence,
    context and arbitration are untouched.
    """

    NO_TRIGGER = "(no trigger — assign in Gesture Studio)"

    def __init__(self, ctl: MotionController, wid: int | None = None,
                 parent=None, initial: dict | None = None) -> None:
        super().__init__(parent)
        self.ctl = ctl
        self.repo = ctl.workflow_repo
        self.actions = ctl.profile_manager.actions
        self.wid = wid
        self._trigger_rule_id: int | None = None
        self.setWindowTitle("Edit Workflow" if wid else "New Workflow")
        self.resize(600, 560)

        self.name_edit = QLineEdit()
        self.desc_edit = QLineEdit()
        self.desc_edit.setPlaceholderText("optional description")
        self.enabled_check = QCheckBox("Enabled")
        self.enabled_check.setChecked(True)
        self.confirm_check = QCheckBox(
            "Requires confirmation before running (when the global "
            "\"confirm dangerous workflows\" setting is on)")

        # trigger: any first-class gesture, through the normal rule system
        from ..gestures.engine import all_gesture_names
        self.trigger_combo = QComboBox()
        self.trigger_combo.addItem(self.NO_TRIGGER, "")
        for g in all_gesture_names(ctl.custom_gestures.all(),
                                   ctl.motion_gestures.all()):
            self.trigger_combo.addItem(g, g)
        for cg in ctl.compound_gestures.all():
            self.trigger_combo.addItem(cg.name, cg.name)
        self.profile_combo = QComboBox()
        for p in ctl.profile_manager.profiles.all():
            self.profile_combo.addItem(p.name, p.id)

        self.steps_list = QListWidget()
        self.steps_list.itemDoubleClicked.connect(lambda _: self._edit_step())
        self._steps: list[dict] = []

        if wid is not None:
            wf = self.repo.get(wid)
            if wf:
                self.name_edit.setText(wf.name)
                self.desc_edit.setText(wf.description)
                self.enabled_check.setChecked(wf.enabled)
                self.confirm_check.setChecked(wf.requires_confirmation)
                self._steps = list(wf.steps)
                self._load_trigger(wf)
        elif initial:
            # prefill for a NEW workflow (e.g. recorded steps)
            self.name_edit.setText(initial.get("name", ""))
            self.desc_edit.setText(initial.get("description", ""))
            self._steps = list(initial.get("steps", []))

        add_btn = QPushButton("Add Step")
        add_btn.clicked.connect(self._add_step)
        ctrl_btn = QPushButton("Control flow ▾")
        ctrl_btn.clicked.connect(lambda: control_flow_menu(
            self, ctrl_btn, self._add_ctrl))
        edit_btn = QPushButton("Edit")
        edit_btn.clicked.connect(self._edit_step)
        del_btn = QPushButton("Delete")
        del_btn.clicked.connect(self._del_step)
        up_btn = QPushButton("Move Up")
        up_btn.clicked.connect(lambda: self._move(-1))
        down_btn = QPushButton("Move Down")
        down_btn.clicked.connect(lambda: self._move(1))
        dup_btn = QPushButton("Duplicate")
        dup_btn.clicked.connect(self._dup_step)
        row = QHBoxLayout()
        for b in (add_btn, ctrl_btn, edit_btn, del_btn, up_btn, down_btn,
                  dup_btn):
            row.addWidget(b)

        test_btn = QPushButton("Test Workflow")
        test_btn.clicked.connect(self._test)
        btns = QDialogButtonBox(QDialogButtonBox.Save
                                | QDialogButtonBox.Cancel)
        btns.accepted.connect(self._save)
        btns.rejected.connect(self.reject)
        bottom = QHBoxLayout()
        bottom.addWidget(test_btn)
        bottom.addStretch(1)
        bottom.addWidget(btns)

        form = QFormLayout()
        form.addRow("Workflow name:", self.name_edit)
        form.addRow("Description:", self.desc_edit)
        form.addRow("Trigger gesture:", self.trigger_combo)
        form.addRow("In profile:", self.profile_combo)
        form.addRow("", self.enabled_check)
        form.addRow("", self.confirm_check)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(QLabel("Steps run in order. Delays never freeze the "
                             "app and are cancelled by Emergency Stop."))
        lay.addWidget(self.steps_list, 1)
        lay.addLayout(row)
        lay.addLayout(bottom)
        self._refresh()

    def _load_trigger(self, wf) -> None:
        """Preselect the existing rule that triggers this workflow."""
        pm = self.ctl.profile_manager
        a = pm.actions.by_name(wf.name)
        if not (a and a.type == "workflow"):
            return
        for r in pm.rules.all():
            if r.action_id == a.id:
                gi = self.trigger_combo.findData(r.gesture)
                if gi >= 0:
                    self.trigger_combo.setCurrentIndex(gi)
                pi = self.profile_combo.findData(r.profile_id)
                if pi >= 0:
                    self.profile_combo.setCurrentIndex(pi)
                self._trigger_rule_id = r.id
                return

    def _label(self, s: dict) -> str:
        return step_label(self.ctl, s)

    def _stores(self, steps=None) -> list[str]:
        out = []
        for s in (self._steps if steps is None else steps):
            if s.get("type") in ("ui_find", "ui_wait") and s.get("store"):
                out.append(s.get("store"))
            elif s.get("type") == "if":
                out += self._stores(s.get("then", []))
                out += self._stores(s.get("else", []))
            elif s.get("type") in ("retry", "repeat", "repeat_until"):
                out += self._stores(s.get("steps", []))
                out += self._stores(s.get("fallback", []))
        return out

    def _refresh(self) -> None:
        self.steps_list.clear()
        for i, s in enumerate(self._steps, 1):
            self.steps_list.addItem(f"{i}.  {self._label(s)}")

    def _add_step(self) -> None:
        dlg = WorkflowStepDialog(self.actions.all(), parent=self,
                                 store_names=self._stores())
        if dlg.exec():
            self._steps.append(dlg.step())
            self._refresh()

    def _add_ctrl(self, kind: str) -> None:
        dlg = control_flow_dialog(self.ctl, kind, None, self, self._stores)
        if dlg.exec():
            self._steps.append(dlg.step())
            self._refresh()

    def _edit_step(self) -> None:
        i = self.steps_list.currentRow()
        if i < 0:
            return
        s = self._steps[i]
        if s.get("type") in CONTROL_FLOW_TYPES:
            dlg = control_flow_dialog(self.ctl, s["type"], s, self,
                                      self._stores)
        else:
            dlg = WorkflowStepDialog(self.actions.all(), s, parent=self,
                                     store_names=self._stores())
        if dlg.exec():
            self._steps[i] = dlg.step()
            self._refresh()

    def _del_step(self) -> None:
        i = self.steps_list.currentRow()
        if i >= 0:
            self._steps.pop(i)
            self._refresh()

    def _dup_step(self) -> None:
        i = self.steps_list.currentRow()
        if i >= 0:
            self._steps.insert(i + 1, dict(self._steps[i]))
            self._refresh()

    def _move(self, delta: int) -> None:
        i = self.steps_list.currentRow()
        j = i + delta
        if i < 0 or not (0 <= j < len(self._steps)):
            return
        self._steps[i], self._steps[j] = self._steps[j], self._steps[i]
        self._refresh()
        self.steps_list.setCurrentRow(j)

    def _validate_deep(self) -> list[str]:
        """Structural validation plus per-step configuration checks:
        referenced actions must exist and pass the executor's validator
        (recursively through If/Else branches), conditions must be
        well-formed."""
        problems = []
        err = self.repo.validate(self._steps)
        if err:
            problems.append(err)
            return problems
        self._validate_actions(self._steps, "", problems)
        return problems

    def _validate_actions(self, steps: list[dict], prefix: str,
                          problems: list[str]) -> None:
        for n, s in enumerate(steps, 1):
            i = f"{prefix}{n}"
            if s.get("type") == "action":
                a = self.actions.get(s.get("action_id", 0))
                if a is None:
                    problems.append(f"step {i}: action no longer exists")
                    continue
                aerr = self.ctl.executor.validate(a.type, a.params)
                if aerr:
                    problems.append(f"step {i} ({a.name}): {aerr}")
            elif s.get("type") == "if":
                self._validate_actions(s.get("then", []), f"{i}.", problems)
                self._validate_actions(s.get("else", []), f"{i}.", problems)
            elif s.get("type") in ("retry", "repeat", "repeat_until"):
                self._validate_actions(s.get("steps", []), f"{i}.",
                                       problems)
                self._validate_actions(s.get("fallback", []), f"{i}.f",
                                       problems)

    def _test(self) -> None:
        """Safe test: validate everything and show the plan; running for
        real requires an explicit second confirmation."""
        problems = self._validate_deep()
        plan = "\n".join(f"{i}.  {self._label(s)}"
                         for i, s in enumerate(self._steps, 1))
        if problems:
            QMessageBox.warning(
                self, "Workflow has problems",
                "This workflow is not runnable:\n\n"
                + "\n".join(f"• {p}" for p in problems))
            return
        if QMessageBox.question(
                self, "Test workflow",
                "Validation OK. Steps in order:\n\n" + plan
                + "\n\nRun this workflow for real now? It will launch "
                "applications, open URLs and press keys.",
                QMessageBox.Cancel | QMessageBox.Yes,
                QMessageBox.Cancel) != QMessageBox.Yes:
            return
        if self.wid is None or self.repo.get(self.wid) is None:
            QMessageBox.information(self, "Test",
                                    "Save the workflow first, then test.")
            return
        # persist current steps so the run matches what is on screen
        self.repo.update(self.wid, steps=self._steps)
        started, reason = self.ctl.workflows.start(self.wid)
        if not started:
            QMessageBox.warning(self, "Workflow", reason)

    def _save(self) -> None:
        name = self.name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "Invalid", "A name is required.")
            return
        problems = self._validate_deep()
        if problems:
            QMessageBox.warning(self, "Invalid workflow",
                                "\n".join(f"• {p}" for p in problems))
            return
        existing = self.repo.by_name(name)
        if existing and existing.id != self.wid:
            QMessageBox.warning(self, "Duplicate",
                                f"Workflow {name!r} already exists.")
            return
        clash = self.actions.by_name(name)
        enabled = self.enabled_check.isChecked()
        confirm = self.confirm_check.isChecked()
        desc = self.desc_edit.text().strip()
        if self.wid is None:
            if clash:
                QMessageBox.warning(
                    self, "Duplicate",
                    f"An action named {name!r} already exists — the "
                    "workflow needs that name for its mappable action.")
                return
            self.wid = self.repo.create(name, self._steps, enabled, desc,
                                        requires_confirmation=confirm)
            self.actions.create(name, "workflow", {"workflow_id": self.wid})
        else:
            old = self.repo.get(self.wid)
            if clash and old and clash.name != old.name:
                QMessageBox.warning(self, "Duplicate",
                                    f"An action named {name!r} exists.")
                return
            self.repo.update(self.wid, name=name, steps=self._steps,
                             enabled=enabled, description=desc,
                             requires_confirmation=confirm)
            # keep the mappable action in sync (create it if missing)
            if old:
                a = self.actions.by_name(old.name)
                if a and a.type == "workflow":
                    self.actions.update(a.id, name=name,
                                        params={"workflow_id": self.wid})
                elif not clash:
                    self.actions.create(name, "workflow",
                                        {"workflow_id": self.wid})
        self._save_trigger(name)
        self.ctl.reload_rules()
        self.accept()

    def _save_trigger(self, name: str) -> None:
        """Upsert the trigger rule through the existing rule system.
        '(no trigger)' leaves any Studio-managed rules untouched."""
        gesture = self.trigger_combo.currentData()
        if not gesture:
            return
        pm = self.ctl.profile_manager
        a = pm.actions.by_name(name)
        if a is None:
            return
        pid = self.profile_combo.currentData()
        if self._trigger_rule_id is not None and pm.rules.get(
                self._trigger_rule_id):
            pm.rules.update(self._trigger_rule_id, gesture=gesture,
                            profile_id=pid, action_id=a.id)
        else:
            self._trigger_rule_id = pm.rules.create(pid, gesture, a.id)


class VariableWatchDialog(QDialog):
    """Read-only live variable panel shown while testing a workflow.
    Values are displayed here only — normal logs never carry variable
    contents."""

    def __init__(self, bridge, wf_name: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Variables — {wf_name}")
        self.resize(360, 260)
        self.setModal(False)
        self._wf = wf_name
        self.listw = QListWidget()
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Workflow variables (read-only, live):"))
        lay.addWidget(self.listw)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.reject)
        lay.addWidget(close_btn)
        bridge.workflow_vars.connect(self._on_vars)
        bridge.workflow_done.connect(self._on_done)

    def _on_vars(self, name: str, variables: dict) -> None:
        if name != self._wf:
            return
        self.listw.clear()
        for k, v in variables.items():
            if isinstance(v, bool):
                shown = "yes" if v else "no"
            elif isinstance(v, float) and v.is_integer():
                shown = str(int(v))
            else:
                shown = str(v)
                if len(shown) > 120:
                    shown = shown[:120] + "…"
            self.listw.addItem(f"{k}  →  {shown}")

    def _on_done(self, name: str, status: str, detail: str) -> None:
        if name == self._wf:
            self.setWindowTitle(f"Variables — {self._wf} ({status})")


class UIInspectorDialog(QDialog):
    """Hover-based accessible-element inspector. Polls only while this
    dialog is open — nothing scans the desktop in the background."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("UI Inspector")
        self.resize(460, 320)
        self._last_info: dict | None = None

        self.info_label = QLabel(
            "Hover the mouse over any UI element in another window.\n"
            "Its accessible properties appear here (read-only).")
        self.info_label.setWordWrap(True)
        self.info_label.setStyleSheet(
            "font-family: Consolas, monospace; background:#101418; "
            "color:#c8d4e0; padding:10px; border-radius:6px;")
        self.info_label.setMinimumHeight(200)

        use_btn = QPushButton("Use this element in workflow")
        use_btn.clicked.connect(self._use)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.reject)
        row = QHBoxLayout()
        row.addWidget(use_btn)
        row.addStretch(1)
        row.addWidget(close_btn)

        lay = QVBoxLayout(self)
        lay.addWidget(self.info_label, 1)
        lay.addLayout(row)

        from PySide6.QtCore import QTimer
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._poll)
        self._timer.start(500)

    def _poll(self) -> None:
        import ctypes

        class _PT(ctypes.Structure):
            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]
        pt = _PT()
        if not ctypes.windll.user32.GetCursorPos(ctypes.byref(pt)):
            return
        # skip our own window
        if self.geometry().contains(pt.x, pt.y):
            return
        from ..context import uia
        info = uia.element_from_point(pt.x, pt.y)
        if info is None:
            self.info_label.setText(
                "UI Automation information unavailable for this "
                "application/element.")
            return
        self._last_info = {
            "process": info.process, "window_title": info.window_title,
            "control_type": info.control_type, "name": info.name,
            "automation_id": info.automation_id,
            "class_name": info.class_name}
        self.info_label.setText(
            f"Application:   {info.process}\n"
            f"Window:        {info.window_title[:60]}\n"
            f"Control type:  {info.control_type}\n"
            f"Name:          {info.name[:60]}\n"
            f"Automation ID: {info.automation_id or '—'}\n"
            f"Class:         {info.class_name or '—'}\n"
            f"Enabled:       {info.enabled}\n"
            f"Visible:       {info.visible}\n"
            f"Rectangle:     {info.rect}")

    def _use(self) -> None:
        if not self._last_info:
            QMessageBox.information(self, "UI Inspector",
                                    "Hover an element first.")
            return
        WorkflowStepDialog.LAST_INSPECTED = dict(self._last_info)
        QMessageBox.information(
            self, "UI Inspector",
            "Element captured. In a workflow step, choose "
            "'UI: find element' and press 'Use inspected element'.")

    def done(self, r: int) -> None:
        self._timer.stop()
        super().done(r)


def _recorded_step_label(ctl, s: dict) -> str:
    """Label for a still-portable recorded step (action steps carry an
    inline spec, not yet an id) for the live recorder preview."""
    if s.get("type") == "action" and isinstance(s.get("action"), dict):
        a = s["action"]
        if a.get("type") == "open_url":
            return f"Open URL {a.get('params', {}).get('url', '')}"
        if a.get("type") == "launch_app":
            return a.get("name", "Launch application")
        if a.get("type") == "key_press":
            return a.get("name", "Press key")
        return a.get("name", a.get("type", "action"))
    return step_label(ctl, s)


class RecorderDialog(QDialog):
    """Workflow recorder: capture real desktop actions and convert them
    into an editable workflow. Never executes or saves automatically —
    Stop opens the normal workflow builder for review, editing, testing
    and saving (where the gesture trigger is assigned as always)."""

    def __init__(self, ctl: MotionController, on_saved=None,
                 parent=None) -> None:
        super().__init__(parent)
        self.ctl = ctl
        self.rec = ctl.recorder
        self._on_saved = on_saved
        self.setWindowTitle("Workflow Recorder")
        self.resize(560, 520)

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("e.g. Open YouTube Search")
        self.desc_edit = QLineEdit()
        self.desc_edit.setPlaceholderText("optional description")

        self.status = QLabel("Ready. Nothing is captured until you press "
                             "Start Recording.")
        self.status.setWordWrap(True)
        self.steps_list = QListWidget()

        self.start_btn = QPushButton("● Start Recording")
        self.start_btn.clicked.connect(self._start)
        self.pause_btn = QPushButton("Pause")
        self.pause_btn.clicked.connect(self._toggle_pause)
        self.pause_btn.setEnabled(False)
        self.stop_btn = QPushButton("Stop && Review")
        self.stop_btn.clicked.connect(self._stop)
        self.stop_btn.setEnabled(False)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self._cancel)
        row = QHBoxLayout()
        for b in (self.start_btn, self.pause_btn, self.stop_btn):
            row.addWidget(b)
        row.addStretch(1)
        row.addWidget(cancel_btn)

        form = QFormLayout()
        form.addRow("Workflow name:", self.name_edit)
        form.addRow("Description:", self.desc_edit)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(self.status)
        lay.addWidget(QLabel("Captured steps (edit after Stop):"))
        lay.addWidget(self.steps_list, 1)
        lay.addWidget(QLabel("Tip: perform real actions in other apps. "
                             "Mouse movement, idle time and this app are "
                             "ignored; typed text becomes an editable "
                             "variable; passwords are never recorded."))
        lay.addLayout(row)

        # Poll on the GUI thread — capture events arrive on worker
        # threads, so we never touch widgets from a bus handler.
        self._timer = QTimer(self)
        self._timer.setInterval(300)
        self._timer.timeout.connect(self._tick)

    def _tick(self) -> None:
        if not self.rec.recording and self.stop_btn.isEnabled():
            # cancelled out from under us (Emergency Stop / motion off)
            self.status.setText("Recording cancelled (Emergency Stop / "
                                "motion control off).")
            self._reset_buttons()
            self._timer.stop()
            return
        if self.rec.recording and not self.rec.paused:
            self._refresh_preview()

    # -- lifecycle ----------------------------------------------------------
    def _exclude(self) -> tuple:
        import sys
        import os
        if getattr(sys, "frozen", False):
            return (os.path.basename(sys.executable).lower(),)
        return ()

    def _start(self) -> None:
        ok, reason = self.rec.start()
        if not ok:
            QMessageBox.warning(self, "Recorder", reason)
            return
        self.start_btn.setEnabled(False)
        self.pause_btn.setEnabled(True)
        self.stop_btn.setEnabled(True)
        self.status.setText("● Recording — perform your actions now.")
        self._timer.start()
        self._refresh_preview()

    def _toggle_pause(self) -> None:
        if self.rec.paused:
            self.rec.resume()
            self.pause_btn.setText("Pause")
            self.status.setText("● Recording — perform your actions now.")
        else:
            self.rec.pause()
            self.pause_btn.setText("Resume")
            self.status.setText("Paused — nothing is being captured.")

    def _refresh_preview(self) -> None:
        steps = self.rec.build(exclude_processes=self._exclude())
        self.steps_list.clear()
        for i, s in enumerate(steps, 1):
            self.steps_list.addItem(
                f"{i}.  {_recorded_step_label(self.ctl, s)}")

    def _reset_buttons(self) -> None:
        self.start_btn.setEnabled(True)
        self.pause_btn.setEnabled(False)
        self.pause_btn.setText("Pause")
        self.stop_btn.setEnabled(False)

    def _cancel(self) -> None:
        self._timer.stop()
        if self.rec.recording:
            self.rec.cancel()
        self.reject()

    def _stop(self) -> None:
        self._timer.stop()
        self.rec.stop()
        portable = self.rec.build(exclude_processes=self._exclude())
        if not portable:
            QMessageBox.information(
                self, "Nothing recorded",
                "No meaningful actions were captured. Try again and "
                "perform clicks, typing or application launches.")
            self._reset_buttons()
            return
        # materialize recorded steps into real named actions (find-or-
        # create), exactly like an imported workflow
        try:
            steps = self.ctl.profile_manager.materialize_steps(portable)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "Recorder",
                                f"Could not prepare recorded steps: {e}")
            self._reset_buttons()
            return
        secure = _has_secure(portable)
        if secure:
            QMessageBox.information(
                self, "Secure input recorded",
                "A password/secure field was recorded as "
                "\"[SECURE INPUT]\". Replace it with the real value in "
                "the review window before running the workflow.")
        dlg = WorkflowBuilderDialog(
            self.ctl, parent=self,
            initial={"name": self.name_edit.text().strip(),
                     "description": self.desc_edit.text().strip(),
                     "steps": steps})
        self.accept()
        if dlg.exec() and self._on_saved:
            self._on_saved()


def _has_secure(steps: list[dict]) -> bool:
    for s in steps:
        if s.get("type") == "set_var" and s.get("secure"):
            return True
        for key in ("steps", "then", "else", "fallback"):
            if isinstance(s.get(key), list) and _has_secure(s[key]):
                return True
    return False


class WorkflowsSection(QGroupBox):
    """List + lifecycle for workflows. A saved workflow is mappable to any
    gesture like a normal named action; the builder can also assign a
    trigger directly."""

    def __init__(self, ctl: MotionController, on_change,
                 bridge=None) -> None:
        super().__init__("Workflows (multi-step)")
        self.ctl = ctl
        self.repo = ctl.workflow_repo
        self.on_change = on_change
        self._bridge = bridge
        self._last_result: dict[str, str] = {}   # name → status text

        self.listw = QListWidget()
        self.listw.itemDoubleClicked.connect(lambda _: self._edit())

        new_btn = QPushButton("New Workflow")
        new_btn.clicked.connect(self._new)
        record_btn = QPushButton("Record Workflow")
        record_btn.clicked.connect(self._record)
        edit_btn = QPushButton("Edit")
        edit_btn.clicked.connect(self._edit)
        dup_btn = QPushButton("Duplicate")
        dup_btn.clicked.connect(self._duplicate)
        toggle_btn = QPushButton("Enable/Disable")
        toggle_btn.clicked.connect(self._toggle)
        del_btn = QPushButton("Delete")
        del_btn.clicked.connect(self._delete)
        test_btn = QPushButton("Test Workflow")
        test_btn.clicked.connect(self._test)
        inspect_btn = QPushButton("UI Inspector…")
        inspect_btn.clicked.connect(
            lambda: UIInspectorDialog(self).exec())
        row = QHBoxLayout()
        for b in (new_btn, record_btn, edit_btn, dup_btn, toggle_btn,
                  del_btn, test_btn, inspect_btn):
            row.addWidget(b)
        row.addStretch(1)

        lay = QVBoxLayout(self)
        lay.addLayout(row)
        lay.addWidget(self.listw)
        if bridge is not None:
            bridge.workflow_progress.connect(
                lambda *a: self.refresh() if self.isVisible() else None)
            bridge.workflow_done.connect(self._on_done)
        self.refresh()

    def _on_done(self, name: str, status: str, detail: str) -> None:
        mark = {"completed": "✓ completed", "failed": "✕ failed",
                "cancelled": "■ cancelled"}.get(status, status)
        self._last_result[name] = mark + (f" — {detail}" if status ==
                                          "failed" and detail else "")
        if self.isVisible():
            self.refresh()

    def _trigger_of(self, wf) -> str:
        pm = self.ctl.profile_manager
        a = pm.actions.by_name(wf.name)
        if a is None or a.type != "workflow":
            return "no trigger"
        rules = [r for r in pm.rules.all() if r.action_id == a.id]
        if not rules:
            return "no trigger"
        profs = {p.id: p.name for p in pm.profiles.all()}
        r = rules[0]
        extra = f" (+{len(rules) - 1})" if len(rules) > 1 else ""
        return f"{r.gesture} • {profs.get(r.profile_id, '?')}{extra}"

    def refresh(self) -> None:
        self.listw.clear()
        running = set(self.ctl.workflows.running_ids())
        for wf in self.repo.all():
            n = len(wf.steps)
            state = ("● running" if wf.id in running
                     else ("✓ enabled" if wf.enabled else "disabled"))
            label = (f"{wf.name}  —  {self._trigger_of(wf)} • "
                     f"{n} step{'s' if n != 1 else ''} • {state}")
            last = self._last_result.get(wf.name)
            if last and wf.id not in running:
                label += f" • {last}"
            if wf.description:
                label += f"\n    {wf.description}"
            item = QListWidgetItem(label)
            item.setData(32, wf.id)
            self.listw.addItem(item)

    def _duplicate(self) -> None:
        wid = self._selected()
        if wid is None:
            return
        wf = self.repo.get(wid)
        base, i = f"{wf.name} (copy)", 2
        name = base
        actions = self.ctl.profile_manager.actions
        while self.repo.by_name(name) or actions.by_name(name):
            name = f"{wf.name} (copy {i})"
            i += 1
        try:
            nid = self.repo.create(name, list(wf.steps), wf.enabled,
                                   wf.description)
        except ValueError as e:
            QMessageBox.warning(self, "Duplicate workflow",
                                f"Cannot duplicate: {e}")
            return
        actions.create(name, "workflow", {"workflow_id": nid})
        self.ctl.reload_rules()
        self.refresh()
        self.on_change()

    def _toggle(self) -> None:
        wid = self._selected()
        if wid is None:
            return
        wf = self.repo.get(wid)
        self.repo.update(wid, enabled=not wf.enabled)
        self.ctl.reload_rules()
        self.refresh()

    def _selected(self) -> int | None:
        it = self.listw.currentItem()
        return it.data(32) if it else None

    def _new(self) -> None:
        if WorkflowBuilderDialog(self.ctl, parent=self).exec():
            self.refresh()
            self.on_change()

    def _record(self) -> None:
        def saved():
            self.refresh()
            self.on_change()
        RecorderDialog(self.ctl, on_saved=saved, parent=self).exec()

    def _edit(self) -> None:
        wid = self._selected()
        if wid is not None and WorkflowBuilderDialog(
                self.ctl, wid, parent=self).exec():
            self.refresh()
            self.on_change()

    def _delete(self) -> None:
        wid = self._selected()
        if wid is None:
            return
        wf = self.repo.get(wid)
        if QMessageBox.question(
                self, "Delete workflow",
                f"Delete workflow {wf.name!r}? Its mappable action and any "
                "rules using it are removed too.") != QMessageBox.Yes:
            return
        a = self.ctl.profile_manager.actions.by_name(wf.name)
        if a and a.type == "workflow":
            self.ctl.profile_manager.actions.delete(a.id)
        self.repo.delete(wid)
        self.ctl.reload_rules()
        self.refresh()
        self.on_change()

    def _test(self) -> None:
        """Safe test: validates first, shows the plan, and only runs on
        an explicit second confirmation. Opens the live variable panel
        when the workflow uses variables."""
        wid = self._selected()
        if wid is None:
            return
        wf = self.repo.get(wid)
        if wf and self._bridge is not None and self._uses_vars(wf.steps):
            VariableWatchDialog(self._bridge, wf.name, self).show()
        dlg = WorkflowBuilderDialog(self.ctl, wid, parent=self)
        dlg._test()
        dlg.done(0)

    @staticmethod
    def _uses_vars(steps: list[dict]) -> bool:
        for s in steps:
            if s.get("type") in ("set_var", "ui_read"):
                return True
            if s.get("type") == "if" and (
                    WorkflowsSection._uses_vars(s.get("then", []))
                    or WorkflowsSection._uses_vars(s.get("else", []))):
                return True
            if s.get("type") in ("retry", "repeat", "repeat_until") and (
                    WorkflowsSection._uses_vars(s.get("steps", []))
                    or WorkflowsSection._uses_vars(s.get("fallback", []))):
                return True
        return False


class ActionsPage(QWidget):
    def __init__(self, ctl: MotionController, bridge=None) -> None:
        super().__init__()
        self.ctl = ctl
        self.repo = ctl.profile_manager.actions
        self._bridge = bridge

        self.listw = QListWidget()
        self.listw.itemDoubleClicked.connect(lambda _: self._edit())

        add_btn = QPushButton("New Action")
        add_btn.clicked.connect(self._add)
        edit_btn = QPushButton("Edit")
        edit_btn.clicked.connect(self._edit)
        del_btn = QPushButton("Delete")
        del_btn.clicked.connect(self._delete)
        test_btn = QPushButton("Test (runs for real)")
        test_btn.clicked.connect(self._test)

        btns = QHBoxLayout()
        for b in (add_btn, edit_btn, del_btn, test_btn):
            btns.addWidget(b)
        btns.addStretch(1)

        self.workflows = WorkflowsSection(ctl, on_change=self.refresh,
                                          bridge=bridge)

        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Named actions are reusable across rules. "
                             "Sequences run steps in order with delays."))
        lay.addLayout(btns)
        lay.addWidget(self.listw, 3)
        lay.addWidget(self.workflows, 2)
        self.refresh()

    def refresh(self) -> None:
        self.listw.clear()
        for a in self.repo.all():
            label = f"{a.name}  —  {a.type}"
            if a.is_builtin:
                label += "  [built-in]"
            item = QListWidgetItem(label)
            item.setData(32, a.id)
            self.listw.addItem(item)

    def _selected_id(self) -> int | None:
        it = self.listw.currentItem()
        return it.data(32) if it else None

    def _add(self) -> None:
        dlg = ActionEditDialog(self.ctl.executor, parent=self)
        if dlg.exec():
            name, type_, params = dlg.result_data
            if self.repo.by_name(name):
                QMessageBox.warning(self, "Duplicate",
                                    f"Action {name!r} already exists.")
                return
            self.repo.create(name, type_, params)
            self.ctl.reload_rules()
            self.refresh()

    def _edit(self) -> None:
        aid = self._selected_id()
        if aid is None:
            return
        a = self.repo.get(aid)
        if a.type == "workflow":
            QMessageBox.information(
                self, "Workflow",
                f"{a.name!r} is a workflow — edit it in the Workflows "
                "section below.")
            return
        dlg = ActionEditDialog(self.ctl.executor, a.name, a.type, a.params,
                               parent=self)
        if dlg.exec():
            name, type_, params = dlg.result_data
            self.repo.update(aid, name=name, type_=type_, params=params)
            self.ctl.reload_rules()
            self.refresh()

    def _delete(self) -> None:
        aid = self._selected_id()
        if aid is None:
            return
        a = self.repo.get(aid)
        if QMessageBox.question(
                self, "Delete",
                f"Delete action {a.name!r}? Rules using it are removed too."
                ) == QMessageBox.Yes:
            self.repo.delete(aid)
            self.ctl.reload_rules()
            self.refresh()

    def _test(self) -> None:
        aid = self._selected_id()
        if aid is None:
            return
        a = self.repo.get(aid)
        if a.type == "cursor_control":
            QMessageBox.information(
                self, "Test", "Cursor control is continuous — assign it to "
                "a gesture and hold the gesture.")
            return
        if a.type == "launch_app":
            exe = a.params.get("path", "?")
            if QMessageBox.question(
                    self, "Launch application",
                    f"Launch {a.name!r} now?\n\n{exe}",
                    QMessageBox.Cancel | QMessageBox.Yes,
                    QMessageBox.Cancel) != QMessageBox.Yes:
                return
        if a.type == "workflow":
            if QMessageBox.question(
                    self, "Run workflow",
                    f"This workflow will run real actions.\n\n"
                    f"Run {a.name!r} now?",
                    QMessageBox.Cancel | QMessageBox.Yes,
                    QMessageBox.Cancel) != QMessageBox.Yes:
                return
        ok = self.ctl.executor.execute(
            ActionSpec(type=a.type, params=a.params, name=a.name))
        if not ok:
            QMessageBox.warning(self, "Test", "Action failed — see log.")
