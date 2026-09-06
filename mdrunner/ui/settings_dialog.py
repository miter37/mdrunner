"""Settings dialog — Agent CLIs / Defaults / Notifications tabs."""

from __future__ import annotations

import copy
import shlex
from typing import Optional


from PySide6.QtWidgets import (
    QAbstractItemView,
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
from .theme import style_form


class SettingsDialog(QDialog):
    def __init__(self, *, parent, settings: Settings) -> None:
        super().__init__(parent)
        self.settings = copy.deepcopy(settings)
        self.setWindowTitle("Preferences")
        self.resize(800, 640)
        self.setMinimumSize(700, 520)

        self.tabs = QTabWidget(self)
        self.tabs.addTab(self._build_agents_tab(), "Agent CLIs")
        self.tabs.addTab(self._build_defaults_tab(), "Defaults")
        self.tabs.addTab(self._build_notifications_tab(), "Notifications")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 12)
        outer.setSpacing(12)
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
        layout.setContentsMargins(6, 10, 6, 6)
        layout.setSpacing(14)

        # Left: list of agents
        left = QVBoxLayout()
        left_w = QWidget(w)
        left_w.setLayout(left)
        self.agent_list = QListWidget(left_w)
        self.agent_list.currentItemChanged.connect(self._on_agent_selected)
        self.agent_list.setDragEnabled(True)
        self.agent_list.setAcceptDrops(True)
        self.agent_list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        for agent_id in self.settings.agents.keys():
            self.agent_list.addItem(QListWidgetItem(agent_id))
        left.addWidget(self.agent_list, 1)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)
        self.btn_add_agent = QPushButton("Add…", left_w)
        self.btn_add_agent.clicked.connect(self._on_add_agent)
        btn_row.addWidget(self.btn_add_agent)
        self.btn_remove_agent = QPushButton("Remove", left_w)
        self.btn_remove_agent.clicked.connect(self._on_remove_agent)
        btn_row.addWidget(self.btn_remove_agent)

        self.btn_up_agent = QPushButton("▲", left_w)
        self.btn_up_agent.setFixedWidth(34)
        self.btn_up_agent.clicked.connect(self._on_move_agent_up)
        btn_row.addWidget(self.btn_up_agent)
        self.btn_down_agent = QPushButton("▼", left_w)
        self.btn_down_agent.setFixedWidth(34)
        self.btn_down_agent.clicked.connect(self._on_move_agent_down)
        btn_row.addWidget(self.btn_down_agent)

        left.addLayout(btn_row)
        self.btn_health = QPushButton("Run health check", left_w)
        self.btn_health.clicked.connect(self._on_health_button)
        left.addWidget(self.btn_health)

        layout.addWidget(left_w, 1)

        # Right: edit form for the selected agent
        right = QVBoxLayout()
        right_w = QWidget(w)
        right_w.setLayout(right)

        # Binary + default model + health cmd
        gb1 = QGroupBox("Identity", right_w)
        f1 = QFormLayout(gb1)
        style_form(f1)
        self.in_binary = QLineEdit(gb1)
        btn_detect = QPushButton("Detect", gb1)
        btn_detect.clicked.connect(self._on_detect_binary)
        row = QWidget(gb1)
        row_l = QHBoxLayout(row)
        row_l.setContentsMargins(0, 0, 0, 0)
        row_l.addWidget(self.in_binary, 1)
        row_l.addWidget(btn_detect)
        f1.addRow("Binary", row)
        from PySide6.QtWidgets import QComboBox

        self.in_default_model = QComboBox(gb1)
        self.in_default_model.setEditable(True)
        f1.addRow("Default model", self.in_default_model)
        self.in_health_cmd = QLineEdit(gb1)
        f1.addRow("Health check cmd", self.in_health_cmd)
        right.addWidget(gb1)

        # Bypass
        gb2 = QGroupBox("Bypass flags (auto-approve on scheduled run)", right_w)
        f2 = QFormLayout(gb2)
        style_form(f2)
        self.in_bypass_sched = QLineEdit(gb2)
        f2.addRow("Scheduled (cron)", self.in_bypass_sched)
        self.in_bypass_manual = QLineEdit(gb2)
        f2.addRow("Manual (Run Now)", self.in_bypass_manual)
        right.addWidget(gb2)

        # Presets
        gb3 = QGroupBox("Extra args presets", right_w)
        gb3.setMinimumHeight(170)
        v3 = QVBoxLayout(gb3)
        v3.setSpacing(8)
        self.preset_table = QTableWidget(0, 2, gb3)
        self.preset_table.setHorizontalHeaderLabels(["Name", "Args"])
        self.preset_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )
        self.preset_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.preset_table.verticalHeader().setVisible(False)
        self.preset_table.setMinimumHeight(96)
        v3.addWidget(self.preset_table)
        preset_btn_row = QHBoxLayout()
        preset_btn_row.setSpacing(6)
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
        self.health_result.setFixedHeight(88)
        self.health_result.setPlaceholderText("(health check result will appear here)")
        right.addWidget(self.health_result)

        if self.agent_list.count() > 0:
            self.agent_list.setCurrentRow(0)

        layout.addWidget(right_w, 2)
        return w

    def _on_agent_selected(self, _current, _previous) -> None:
        if _previous is not None:
            prev_aid = _previous.text()
            prev_cfg = self.settings.agents.get(prev_aid)
            if prev_cfg is not None:
                prev_cfg.binary = self.in_binary.text().strip() or prev_cfg.binary
                # Only update default_model if the combo box is enabled and the text isn't the loading placeholder
                model_text = self.in_default_model.currentText().strip()
                if self.in_default_model.isEnabled() and model_text != "(loading models...)":
                    prev_cfg.default_model = model_text or None
                prev_cfg.health_cmd = (
                    shlex.split(self.in_health_cmd.text())
                    if self.in_health_cmd.text().strip()
                    else [prev_cfg.binary, "--version"]
                )
                prev_cfg.bypass = Bypass(
                    scheduled=shlex.split(self.in_bypass_sched.text()),
                    manual=shlex.split(self.in_bypass_manual.text()),
                )

        cfg = self._current_agent_cfg()
        if cfg is None:
            return
        aid = self._current_agent_id()
        self.in_binary.setText(cfg.binary)

        self.in_default_model.clear()
        if cfg.default_model:
            self.in_default_model.setCurrentText(cfg.default_model)

        self.in_default_model.setEnabled(False)
        self.in_default_model.addItem("(loading models...)")
        self.in_default_model.setCurrentIndex(0)

        from .workers import ModelFetchWorker

        if not hasattr(self, "_active_model_workers"):
            self._active_model_workers = []

        worker = ModelFetchWorker(aid, cfg.binary)
        self._model_worker = worker
        self._active_model_workers.append(worker)

        def on_models_loaded(models):
            if worker in self._active_model_workers:
                self._active_model_workers.remove(worker)
            if self._model_worker is not worker:
                return
            self.in_default_model.clear()
            if models:
                self.in_default_model.addItems(models)
            if cfg.default_model:
                self.in_default_model.setCurrentText(cfg.default_model)
            else:
                self.in_default_model.setCurrentIndex(-1)
            self.in_default_model.setEnabled(True)

        def on_models_error(err):
            if worker in self._active_model_workers:
                self._active_model_workers.remove(worker)
            if self._model_worker is not worker:
                return
            self.in_default_model.clear()
            if cfg.default_model:
                self.in_default_model.setCurrentText(cfg.default_model)
            self.in_default_model.setEnabled(True)

        worker.models_ready.connect(on_models_loaded)
        worker.error.connect(on_models_error)
        worker.start()

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
        name, ok = QInputDialog.getText(
            self, "Add agent", "Agent id (e.g. opencode, codex, openclaw):"
        )
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
            self,
            "Remove agent",
            f"Remove agent config for {aid!r}? Tasks using it will fail until you re-add the entry.",
        )
        if ans != QMessageBox.StandardButton.Yes:
            return
        del self.settings.agents[aid]
        self.agent_list.takeItem(self.agent_list.currentRow())

    def _on_move_agent_up(self) -> None:
        row = self.agent_list.currentRow()
        if row <= 0:
            return
        self.agent_list.blockSignals(True)
        item = self.agent_list.takeItem(row)
        self.agent_list.insertItem(row - 1, item)
        self.agent_list.setCurrentRow(row - 1)
        self.agent_list.blockSignals(False)

    def _on_move_agent_down(self) -> None:
        row = self.agent_list.currentRow()
        if row < 0 or row >= self.agent_list.count() - 1:
            return
        self.agent_list.blockSignals(True)
        item = self.agent_list.takeItem(row)
        self.agent_list.insertItem(row + 1, item)
        self.agent_list.setCurrentRow(row + 1)
        self.agent_list.blockSignals(False)

    def _on_detect_binary(self) -> None:
        import shutil

        aid = self._current_agent_id()
        query = self.in_binary.text().strip() or aid
        if not query:
            return
        path = shutil.which(query)
        if path:
            self.in_binary.setText(path)
            QMessageBox.information(self, "Detected", f"Found: {path}")
        else:
            QMessageBox.warning(self, "Not found", f"{query!r} not found on PATH")

    def _on_health_button(self) -> None:
        aid = self._current_agent_id()
        if not aid:
            return
        binary = self.in_binary.text().strip()
        model = self.in_default_model.currentText().strip() or None

        self.health_result.clear()
        self.btn_health.setEnabled(False)

        from .workers import SingleAgentHealthWorker

        self._health_worker = SingleAgentHealthWorker(aid, binary, model)

        def on_progress(msg):
            self.health_result.appendPlainText(msg)

        def on_finished(result):
            self.btn_health.setEnabled(True)
            text = []
            text.append("\n==================================")
            if result.ok:
                text.append("✓ 헬스체크 최종 판정: 정상 (PASS)")
                if result.version:
                    text.append(f"  버전: {result.version}")
            else:
                text.append("✗ 헬스체크 최종 판정: 실패 (FAIL)")
                if result.error:
                    text.append(f"  오류 내용: {result.error}")

            if result.stdout:
                text.append(f"\n--- [Stdout Output] ---\n{result.stdout[:500]}")
            if result.stderr:
                text.append(f"\n--- [Stderr Output] ---\n{result.stderr[:500]}")

            self.health_result.appendPlainText("\n".join(text))

        self._health_worker.progress.connect(on_progress)
        self._health_worker.finished.connect(on_finished)
        self._health_worker.start()

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
        args, ok2 = QInputDialog.getText(self, "Edit preset", "Args:", text=" ".join(old.args))
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
        style_form(f)
        self.in_default_timeout = QSpinBox(w)
        self.in_default_timeout.setRange(1, 24 * 60)
        self.in_default_timeout.setSuffix(" min")
        self.in_default_timeout.setValue(self.settings.defaults.timeout_minutes)
        f.addRow("Default task timeout", self.in_default_timeout)

        # 신규 필드 추가
        self.in_artifact_markers = QPlainTextEdit(w)
        self.in_artifact_markers.setPlaceholderText("한 줄에 하나씩 마커 입력")
        self.in_artifact_markers.setMaximumHeight(80)
        self.in_artifact_markers.setPlainText("\n".join(self.settings.defaults.artifact_markers))
        f.addRow("Result Log Markers", self.in_artifact_markers)

        self.in_artifact_window = QSpinBox(w)
        self.in_artifact_window.setRange(1, 3600)
        self.in_artifact_window.setSuffix(" sec")
        self.in_artifact_window.setValue(self.settings.defaults.artifact_time_window_seconds)
        f.addRow("Result Time Window", self.in_artifact_window)

        # --- Quota polling (systemd timer that runs `mdrunner quota-tick`) ---
        qp = getattr(self.settings, "quota_poll", None)
        self.in_quota_enabled = QCheckBox("Poll agent quota on a timer", w)
        self.in_quota_enabled.setChecked(bool(qp and qp.enabled))
        f.addRow("Quota polling", self.in_quota_enabled)

        self.in_quota_interval = QSpinBox(w)
        self.in_quota_interval.setRange(5, 24 * 60)
        self.in_quota_interval.setSuffix(" min")
        self.in_quota_interval.setValue(qp.interval_minutes if qp else 10)
        f.addRow("Poll every", self.in_quota_interval)

        return w

    # =================================================== Notifications tab

    def _build_notifications_tab(self) -> QWidget:
        w = QWidget(self)
        f = QFormLayout(w)
        style_form(f)
        self.in_tg_token = QLineEdit(w)
        self.in_tg_token.setEchoMode(QLineEdit.EchoMode.Password)
        f.addRow("Telegram bot token", self.in_tg_token)
        self.in_tg_chat = QLineEdit(w)
        f.addRow("Chat ID", self.in_tg_chat)
        self.cb_tg_failure = QCheckBox("Notify on failure", w)
        self.cb_tg_failure.setChecked(True)
        f.addRow("", self.cb_tg_failure)

        from . import _telegram_settings
        cfg = _telegram_settings.load()
        self.in_tg_token.setText(cfg.get("bot_token", ""))
        self.in_tg_chat.setText(cfg.get("chat_id", ""))
        self.cb_tg_failure.setChecked(cfg.get("notify_on_failure", True))

        note = QLabel(
            "Telegram notifications are configured at the mdrunner level (global). "
            "Each task can additionally set on_failure.notify=true in tasks.yaml.",
            w,
        )
        note.setWordWrap(True)
        f.addRow(note)
        return w

    def _apply_quota_timer(self, qp) -> None:
        """Install / remove the periodic quota-poll OS timer (systemd on
        Linux, launchd on macOS; unsupported elsewhere)."""
        try:
            from ..cli import _mdrunner_executable_for_scheduler
            from ..scheduler.base import current

            sched = current()
            if not hasattr(sched, "install_quota_poll"):
                return
            if qp.enabled:
                sched.install_quota_poll(
                    qp.interval_minutes, _mdrunner_executable_for_scheduler()
                )
            else:
                sched.uninstall_quota_poll()
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(
                self, "Quota timer",
                f"Saved the setting, but the OS timer step failed:\n\n{exc}",
            )

    # =================================================== Save

    def _on_accept(self) -> None:
        # Apply agent edits into self.settings
        cfg = self._current_agent_cfg()
        if cfg is not None:
            cfg.binary = self.in_binary.text().strip() or cfg.binary
            # Only update default_model if the combo box is enabled and the text isn't the loading placeholder
            model_text = self.in_default_model.currentText().strip()
            if self.in_default_model.isEnabled() and model_text != "(loading models...)":
                cfg.default_model = model_text or None
            cfg.health_cmd = (
                shlex.split(self.in_health_cmd.text())
                if self.in_health_cmd.text().strip()
                else [cfg.binary, "--version"]
            )
            cfg.bypass = Bypass(
                scheduled=shlex.split(self.in_bypass_sched.text()),
                manual=shlex.split(self.in_bypass_manual.text()),
            )

        # Reorder self.settings.agents to match the visual order in self.agent_list
        new_agents = {}
        for i in range(self.agent_list.count()):
            aid = self.agent_list.item(i).text()
            if aid in self.settings.agents:
                new_agents[aid] = self.settings.agents[aid]
        self.settings.agents = new_agents
        # Defaults
        markers = [
            line.strip()
            for line in self.in_artifact_markers.toPlainText().splitlines()
            if line.strip()
        ]
        if not markers:
            markers = ["Saved:", "저장 완료:"]
        self.settings.defaults = Defaults(
            timeout_minutes=self.in_default_timeout.value(),
            artifact_markers=markers,
            artifact_time_window_seconds=self.in_artifact_window.value(),
        )

        # Quota polling — persist the setting and (un)install the systemd timer.
        from ..config import QuotaPoll

        qp_prev = getattr(self.settings, "quota_poll", QuotaPoll())
        self.settings.quota_poll = QuotaPoll(
            enabled=self.in_quota_enabled.isChecked(),
            interval_minutes=self.in_quota_interval.value(),
            agents=list(qp_prev.agents),
        )
        self._apply_quota_timer(self.settings.quota_poll)

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
