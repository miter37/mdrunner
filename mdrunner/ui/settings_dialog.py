"""Settings dialog — Agent CLIs / Defaults / Notifications tabs."""

from __future__ import annotations

import shlex
from typing import Optional

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..config import (
    AgentConfig,
    Bypass,
    Defaults,
    Preset,
    Settings,
)
from ..health import bypass_risk_level, probe_health


class SettingsDialog(QDialog):
    def __init__(self, *, parent, settings: Settings) -> None:
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("mdrunner — Settings")
        self.resize(820, 600)

        self.tabs = QTabWidget(self)
        self.tabs.addTab(self._build_agents_tab(), "Agent CLIs")
        self.tabs.addTab(self._build_defaults_tab(), "Defaults")
        self.tabs.addTab(self._build_notifications_tab(), "Notifications")

        outer = QVBoxLayout(self)
        outer.addWidget(self.tabs)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

    # =================================================== Agent CLIs tab

    def _build_agents_tab(self) -> QWidget:
        w = QWidget(self)
        layout = QHBoxLayout(w)

        # Left: list of agents
        left = QVBoxLayout()
        left_w = QWidget(w)
        left_w.setLayout(left)
        self.agent_list = QListWidget(left_w)
        self.agent_list.currentItemChanged.connect(self._on_agent_selected)
        for agent_id in sorted(self.settings.agents.keys()):
            self.agent_list.addItem(QListWidgetItem(agent_id))
        if self.agent_list.count() > 0:
            self.agent_list.setCurrentRow(0)
        left.addWidget(self.agent_list, 1)

        btn_row = QHBoxLayout()
        self.btn_add_agent = QPushButton("Add…", left_w)
        self.btn_add_agent.clicked.connect(self._on_add_agent)
        btn_row.addWidget(self.btn_add_agent)
        self.btn_remove_agent = QPushButton("Remove", left_w)
        self.btn_remove_agent.clicked.connect(self._on_remove_agent)
        btn_row.addWidget(self.btn_remove_agent)
        self.btn_health = QPushButton("Run Health Check", left_w)
        self.btn_health.clicked.connect(self._on_health_button)
        btn_row.addWidget(self.btn_health)
        left.addLayout(btn_row)

        layout.addWidget(left_w, 1)

        # Right: edit form for the selected agent
        right = QVBoxLayout()
        right_w = QWidget(w)
        right_w.setLayout(right)

        # Binary + default model + health cmd
        gb1 = QGroupBox("Identity", right_w)
        f1 = QFormLayout(gb1)
        self.in_binary = QLineEdit(gb1)
        btn_detect = QPushButton("Detect", gb1)
        btn_detect.clicked.connect(self._on_detect_binary)
        row = QWidget(gb1)
        row_l = QHBoxLayout(row)
        row_l.setContentsMargins(0, 0, 0, 0)
        row_l.addWidget(self.in_binary, 1)
        row_l.addWidget(btn_detect)
        f1.addRow("Binary", row)
        self.in_default_model = QLineEdit(gb1)
        f1.addRow("Default model", self.in_default_model)
        self.in_health_cmd = QLineEdit(gb1)
        f1.addRow("Health check cmd", self.in_health_cmd)
        right.addWidget(gb1)

        # Bypass
        gb2 = QGroupBox("Bypass flags (auto-approve on scheduled run)", right_w)
        f2 = QFormLayout(gb2)
        self.in_bypass_sched = QLineEdit(gb2)
        f2.addRow("Scheduled (cron)", self.in_bypass_sched)
        self.in_bypass_manual = QLineEdit(gb2)
        f2.addRow("Manual (Run Now)", self.in_bypass_manual)
        right.addWidget(gb2)

        # Presets
        gb3 = QGroupBox("Extra args presets", right_w)
        v3 = QVBoxLayout(gb3)
        self.preset_table = QTableWidget(0, 2, gb3)
        self.preset_table.setHorizontalHeaderLabels(["Name", "Args"])
        self.preset_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.preset_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.preset_table.verticalHeader().setVisible(False)
        v3.addWidget(self.preset_table)
        preset_btn_row = QHBoxLayout()
        self.btn_add_preset = QPushButton("Add", gb3)
        self.btn_add_preset.clicked.connect(self._on_add_preset)
        self.btn_edit_preset = QPushButton("Edit", gb3)
        self.btn_edit_preset.clicked.connect(self._on_edit_preset)
        self.btn_del_preset = QPushButton("Delete", gb3)
        self.btn_del_preset.clicked.connect(self._on_del_preset)
        preset_btn_row.addWidget(self.btn_add_preset)
        preset_btn_row.addWidget(self.btn_edit_preset)
        preset_btn_row.addWidget(self.btn_del_preset)
        preset_btn_row.addStretch(1)
        v3.addLayout(preset_btn_row)
        right.addWidget(gb3, 1)

        # Health result panel
        self.health_result = QPlainTextEdit(right_w)
        self.health_result.setReadOnly(True)
        self.health_result.setMaximumHeight(120)
        self.health_result.setPlaceholderText("(health check result will appear here)")
        right.addWidget(self.health_result)

        layout.addWidget(right_w, 2)
        return w

    def _on_agent_selected(self, _current, _previous) -> None:
        cfg = self._current_agent_cfg()
        if cfg is None:
            return
        self.in_binary.setText(cfg.binary)
        self.in_default_model.setText(cfg.default_model or "")
        self.in_health_cmd.setText(" ".join(cfg.health_cmd))
        self.in_bypass_sched.setText(" ".join(cfg.bypass.scheduled))
        self.in_bypass_manual.setText(" ".join(cfg.bypass.manual))
        self._refresh_preset_table(cfg)
        self.health_result.clear()

    def _current_agent_id(self) -> Optional[str]:
        item = self.agent_list.currentItem()
        return item.text() if item else None

    def _current_agent_cfg(self) -> Optional[AgentConfig]:
        aid = self._current_agent_id()
        if aid is None:
            return None
        return self.settings.agents.get(aid)

    def _refresh_preset_table(self, cfg: AgentConfig) -> None:
        self.preset_table.setRowCount(0)
        for p in cfg.presets:
            row = self.preset_table.rowCount()
            self.preset_table.insertRow(row)
            self.preset_table.setItem(row, 0, QTableWidgetItem(p.name))
            self.preset_table.setItem(row, 1, QTableWidgetItem(" ".join(p.args)))

    def _on_add_agent(self) -> None:
        name, ok = QInputDialog.getText(self, "Add agent", "Agent id (e.g. opencode, codex, openclaw):")
        if not ok or not name.strip():
            return
        name = name.strip()
        if name in self.settings.agents:
            QMessageBox.warning(self, "Duplicate", f"Agent {name!r} already exists.")
            return
        self.settings.agents[name] = AgentConfig(binary=name, health_cmd=[name, "--version"])
        self.agent_list.addItem(QListWidgetItem(name))
        # Select the new one
        for i in range(self.agent_list.count()):
            if self.agent_list.item(i).text() == name:
                self.agent_list.setCurrentRow(i)
                break

    def _on_remove_agent(self) -> None:
        aid = self._current_agent_id()
        if aid is None:
            return
        ans = QMessageBox.question(
            self, "Remove agent", f"Remove agent config for {aid!r}? Tasks using it will fail until you re-add the entry."
        )
        if ans != QMessageBox.StandardButton.Yes:
            return
        del self.settings.agents[aid]
        self.agent_list.takeItem(self.agent_list.currentRow())

    def _on_detect_binary(self) -> None:
        import shutil

        path = shutil.which(self.in_binary.text().strip())
        if path:
            self.in_binary.setText(path)
            QMessageBox.information(self, "Detected", f"Found: {path}")
        else:
            QMessageBox.warning(self, "Not found", f"{self.in_binary.text()!r} not found on PATH")

    def _on_health_button(self) -> None:
        aid = self._current_agent_id()
        if not aid:
            return
        binary = self.in_binary.text().strip()
        cmd_text = self.in_health_cmd.text().strip()
        cmd = shlex.split(cmd_text) if cmd_text else [binary, "--version"]
        r = probe_health(binary, cmd)
        risk = bypass_risk_level(
            shlex.split(self.in_bypass_sched.text()) + shlex.split(self.in_bypass_manual.text())
        )
        text = []
        if r.ok:
            text.append(f"✓ Found: {r.binary_path}")
            if r.version:
                text.append(f"  Version: {r.version}")
        else:
            text.append(f"✗ Error: {r.error}")
            if r.stdout:
                text.append(f"  stdout: {r.stdout[:300]}")
            if r.stderr:
                text.append(f"  stderr: {r.stderr[:300]}")
        text.append(f"  bypass risk: {risk}")
        self.health_result.setPlainText("\n".join(text))

    def _on_add_preset(self) -> None:
        cfg = self._current_agent_cfg()
        if cfg is None:
            return
        name, ok = QInputDialog.getText(self, "Add preset", "Preset name:")
        if not ok or not name.strip():
            return
        args, ok2 = QInputDialog.getText(self, "Add preset", "Args (space-separated):")
        if not ok2:
            return
        cfg.presets.append(Preset(name=name.strip(), args=shlex.split(args)))
        self._refresh_preset_table(cfg)

    def _on_edit_preset(self) -> None:
        cfg = self._current_agent_cfg()
        if cfg is None:
            return
        row = self.preset_table.currentRow()
        if row < 0:
            return
        old = cfg.presets[row]
        name, ok = QInputDialog.getText(self, "Edit preset", "Name:", text=old.name)
        if not ok or not name.strip():
            return
        args, ok2 = QInputDialog.getText(
            self, "Edit preset", "Args:", text=" ".join(old.args)
        )
        if not ok2:
            return
        cfg.presets[row] = Preset(name=name.strip(), args=shlex.split(args))
        self._refresh_preset_table(cfg)

    def _on_del_preset(self) -> None:
        cfg = self._current_agent_cfg()
        if cfg is None:
            return
        row = self.preset_table.currentRow()
        if row < 0:
            return
        del cfg.presets[row]
        self._refresh_preset_table(cfg)

    # =================================================== Defaults tab

    def _build_defaults_tab(self) -> QWidget:
        w = QWidget(self)
        f = QFormLayout(w)
        self.in_default_timeout = QSpinBox(w)
        self.in_default_timeout.setRange(1, 24 * 60)
        self.in_default_timeout.setSuffix(" min")
        self.in_default_timeout.setValue(self.settings.defaults.timeout_minutes)
        f.addRow("Default task timeout", self.in_default_timeout)
        return w

    # =================================================== Notifications tab

    def _build_notifications_tab(self) -> QWidget:
        w = QWidget(self)
        f = QFormLayout(w)
        self.in_tg_token = QLineEdit(w)
        self.in_tg_token.setEchoMode(QLineEdit.EchoMode.Password)
        f.addRow("Telegram bot token", self.in_tg_token)
        self.in_tg_chat = QLineEdit(w)
        f.addRow("Chat ID", self.in_tg_chat)
        self.cb_tg_failure = QCheckBox("Notify on failure", w)
        self.cb_tg_failure.setChecked(True)
        f.addRow("", self.cb_tg_failure)
        note = QLabel(
            "Telegram notifications are configured at the mdrunner level (global). "
            "Each task can additionally set on_failure.notify=true in tasks.yaml.",
            w,
        )
        note.setWordWrap(True)
        f.addRow(note)
        return w

    # =================================================== Save

    def _on_accept(self) -> None:
        # Apply agent edits into self.settings
        cfg = self._current_agent_cfg()
        if cfg is not None:
            cfg.binary = self.in_binary.text().strip() or cfg.binary
            cfg.default_model = self.in_default_model.text().strip() or None
            cfg.health_cmd = shlex.split(self.in_health_cmd.text()) if self.in_health_cmd.text().strip() else [cfg.binary, "--version"]
            cfg.bypass = Bypass(
                scheduled=shlex.split(self.in_bypass_sched.text()),
                manual=shlex.split(self.in_bypass_manual.text()),
            )
        # Defaults
        self.settings.defaults = Defaults(timeout_minutes=self.in_default_timeout.value())
        # Telegram settings stored in a small sidecar file (Phase 4)
        from . import _telegram_settings

        _telegram_settings.save(
            {
                "bot_token": self.in_tg_token.text().strip(),
                "chat_id": self.in_tg_chat.text().strip(),
                "notify_on_failure": self.cb_tg_failure.isChecked(),
            }
        )
        self.accept()