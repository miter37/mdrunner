"""Task add/edit dialog."""

from __future__ import annotations

import datetime as _dt
import os
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QTime
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from .theme import mono_font, style_form

from ..config import (
    QUOTA_CAPABLE_AGENTS,
    QUOTA_CLAUSES,
    MinRerunInterval,
    OnFailure,
    QuotaClause,
    QuotaCondition,
    Schedule,
    Settings,
    Task,
    save_tasks,
)
from ..prompts import is_managed_prompt, save_inline_prompt
from ..quota_gate import available_windows
from ..utils.paths import prompts_dir, tasks_file


WEEKDAYS = [
    ("mon", "Mon"),
    ("tue", "Tue"),
    ("wed", "Wed"),
    ("thu", "Thu"),
    ("fri", "Fri"),
    ("sat", "Sat"),
    ("sun", "Sun"),
]

_QC_DEFAULTS = {
    "weekly_used": 90,
    "weekly_reset": 24,
    "fivehour_used": 90,
    "fivehour_reset": 3,
}

COMMON_TZ = [
    "Asia/Seoul",
    "Asia/Tokyo",
    "Asia/Shanghai",
    "Asia/Hong_Kong",
    "America/New_York",
    "America/Chicago",
    "America/Los_Angeles",
    "Europe/London",
    "Europe/Berlin",
    "UTC",
]


class TaskDialog(QDialog):
    """Add or edit one task.

    On accept: writes the entire tasks.yaml back (single source of truth on disk).
    """

    def __init__(self, *, parent, settings: Settings, task: Optional[Task]) -> None:
        super().__init__(parent)
        self.settings = settings
        self.task_id: str = task.id if task else self._suggest_id()
        self.setWindowTitle("Edit task" if task else "Add task")
        self.resize(820, 720)
        self.setMinimumSize(720, 640)

        self._build_ui(task)
        self._connect_signals()
        self._refresh_agent_dependent_fields()
        self._refresh_preview()

    # --------------------------------------------------------------- helpers

    def _suggest_id(self) -> str:
        return f"task_{_dt.datetime.now().strftime('%Y%m%d_%H%M%S')}"

    _style_form = staticmethod(style_form)

    def _build_ui(self, task: Optional[Task]) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 12)
        outer.setSpacing(10)

        tabs = QTabWidget(self)

        def _add_tab(title: str, inner: QWidget, *, fill: bool = False) -> None:
            page = QWidget()
            lay = QVBoxLayout(page)
            lay.setContentsMargins(8, 10, 8, 8)
            lay.addWidget(inner, 1 if fill else 0)
            if not fill:
                lay.addStretch(1)
            tabs.addTab(page, title)

        # --- Name (always visible, above the tabs) ---
        name_row = QWidget(self)
        name_lay = QHBoxLayout(name_row)
        name_lay.setContentsMargins(2, 0, 2, 0)
        name_lay.setSpacing(10)
        name_lbl = QLabel("Task name", name_row)
        self.in_name = QLineEdit(name_row)
        self.in_name.setPlaceholderText("A short, recognizable name")
        if task:
            self.in_name.setText(task.name)
        name_lay.addWidget(name_lbl)
        name_lay.addWidget(self.in_name, 1)
        outer.addWidget(name_row)

        # --- Agent ---
        gb_agent = QWidget()
        fa = QFormLayout(gb_agent)
        self._style_form(fa)
        self.in_agent = QComboBox(gb_agent)
        for agent_id in sorted(self.settings.agents.keys()):
            self.in_agent.addItem(agent_id)
        if task:
            idx = self.in_agent.findText(task.agent)
            if idx >= 0:
                self.in_agent.setCurrentIndex(idx)
        fa.addRow("Agent", self.in_agent)

        self.in_model = QComboBox(gb_agent)
        self.in_model.setEditable(True)
        self.in_model.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.in_model.lineEdit().setPlaceholderText("(blank = agent's default model)")
        if task and task.model:
            self.in_model.setCurrentText(task.model)
        fa.addRow("Model", self.in_model)
        self._model_worker = None

        self.in_preset = QComboBox(gb_agent)
        self.in_preset.addItem("(custom / none)", None)
        if task and task.agent in self.settings.agents:
            for p in self.settings.agents[task.agent].presets:
                self.in_preset.addItem(p.name, p.args)
            if task.extra_args:
                self.in_preset.addItem("(current: custom)", task.extra_args)
                self.in_preset.setCurrentIndex(self.in_preset.count() - 1)
        fa.addRow("Extra args preset", self.in_preset)

        self.in_extra = QLineEdit(gb_agent)
        self.in_extra.setPlaceholderText("(optional, e.g. --sandbox workspace-write)")
        if task:
            self.in_extra.setText(" ".join(task.extra_args))
        fa.addRow("Extra args (free form)", self.in_extra)

        self.in_timeout = QSpinBox(gb_agent)
        self.in_timeout.setRange(0, 24 * 60)
        self.in_timeout.setSuffix(" min")
        self.in_timeout.setValue(task.timeout_minutes if task else 10)
        fa.addRow("Timeout", self.in_timeout)
        _add_tab("Agent", gb_agent)

        # --- Prompt + working dir ---
        gb_paths = QWidget()
        fp = QFormLayout(gb_paths)
        self._style_form(fp)

        # Prompt source: point at an existing md file, or type the instruction
        # right here and let mdrunner save it as an md file in its own folder.
        self.in_prompt_source = QComboBox(gb_paths)
        self.in_prompt_source.addItem("Existing file", "file")
        self.in_prompt_source.addItem("Write inline (saved as .md)", "inline")
        fp.addRow("Prompt source", self.in_prompt_source)

        self.in_prompt = QLineEdit(gb_paths)
        if task:
            self.in_prompt.setText(task.prompt_file)
        self.btn_prompt = QPushButton("Browse…", gb_paths)
        self.btn_prompt.clicked.connect(self._pick_prompt)
        self.row_prompt_file = QWidget(gb_paths)
        rowp_lay = QHBoxLayout(self.row_prompt_file)
        rowp_lay.setContentsMargins(0, 0, 0, 0)
        rowp_lay.setSpacing(6)
        rowp_lay.addWidget(self.in_prompt, 1)
        rowp_lay.addWidget(self.btn_prompt)
        self.lbl_prompt_file = QLabel("Prompt file", gb_paths)
        fp.addRow(self.lbl_prompt_file, self.row_prompt_file)

        self.in_prompt_editor = QPlainTextEdit(gb_paths)
        self.in_prompt_editor.setPlaceholderText(
            "Type the task instruction here. On save it is written as a "
            "Markdown file into:\n" + str(prompts_dir())
        )
        self.in_prompt_editor.setMinimumHeight(200)
        self.lbl_prompt_editor = QLabel("Instruction (md)", gb_paths)
        fp.addRow(self.lbl_prompt_editor, self.in_prompt_editor)

        # When editing a task whose prompt file is one mdrunner manages,
        # default to inline mode and load the current text for editing.
        if task and is_managed_prompt(task.prompt_file):
            self.in_prompt_source.setCurrentIndex(self.in_prompt_source.findData("inline"))
            try:
                self.in_prompt_editor.setPlainText(
                    Path(task.prompt_file).expanduser().read_text(encoding="utf-8")
                )
            except OSError:
                pass

        self.in_cwd = QLineEdit(gb_paths)
        if task and task.working_dir:
            self.in_cwd.setText(task.working_dir)
        btn_cwd = QPushButton("Browse…", gb_paths)
        btn_cwd.clicked.connect(self._pick_cwd)
        rowc = QWidget(gb_paths)
        rowc_lay = QHBoxLayout(rowc)
        rowc_lay.setContentsMargins(0, 0, 0, 0)
        rowc_lay.setSpacing(6)
        rowc_lay.addWidget(self.in_cwd, 1)
        rowc_lay.addWidget(btn_cwd)
        fp.addRow("Working dir (optional)", rowc)
        _add_tab("Files", gb_paths, fill=True)

        # --- Schedule ---
        gb_sched = QWidget()
        fs = QFormLayout(gb_sched)
        self._style_form(fs)
        self.in_mode = QComboBox(gb_sched)
        for m, label in (
            ("once", "once"), ("daily", "daily"), ("weekly", "weekly"),
            ("interval", "interval (every N min)"),
            ("quota", "quota (no time — checked every ~10 min)"),
        ):
            self.in_mode.addItem(label, m)
        if task:
            idx = self.in_mode.findData(task.schedule.mode)
            if idx >= 0:
                self.in_mode.setCurrentIndex(idx)
        fs.addRow("Mode", self.in_mode)

        # Day checkboxes
        self.day_checks: dict[str, QCheckBox] = {}
        days_row = QWidget(gb_sched)
        days_lay = QHBoxLayout(days_row)
        days_lay.setContentsMargins(0, 0, 0, 0)
        for code, label in WEEKDAYS:
            cb = QCheckBox(label, days_row)
            self.day_checks[code] = cb
            days_lay.addWidget(cb)
        days_lay.addStretch(1)
        if task and task.schedule.days:
            for d in task.schedule.days:
                if d in self.day_checks:
                    self.day_checks[d].setChecked(True)
        else:
            for d in ("mon", "tue", "wed", "thu", "fri"):
                self.day_checks[d].setChecked(True)
        fs.addRow("Days (weekly mode)", days_row)

        self.in_time = QTimeEdit(gb_sched)
        self.in_time.setDisplayFormat("HH:mm")
        if task:
            try:
                parts = task.schedule.time.split(":")
                self.in_time.setTime(QTime(int(parts[0]), int(parts[1])))
            except Exception:  # noqa: BLE001
                self.in_time.setTime(QTime(7, 0))
        else:
            self.in_time.setTime(QTime(7, 0))
        fs.addRow("Time", self.in_time)

        self.in_tz = QComboBox(gb_sched)
        self.in_tz.setEditable(True)
        for tz in COMMON_TZ:
            self.in_tz.addItem(tz)
        if task:
            self.in_tz.setCurrentText(task.schedule.timezone)
        else:
            self.in_tz.setCurrentText("Asia/Seoul")
        fs.addRow("Timezone", self.in_tz)

        self.in_interval = QSpinBox(gb_sched)
        self.in_interval.setRange(1, 24 * 60)
        self.in_interval.setSuffix(" min")
        self.in_interval.setValue(task.schedule.interval_minutes if task else 60)
        fs.addRow("Interval (interval mode)", self.in_interval)

        # Min re-run interval — a floor on how often the task actually runs.
        mri = task.min_rerun_interval if task else MinRerunInterval()
        self.in_mrr_enabled = QCheckBox("Enforce a minimum gap between runs", gb_sched)
        self.in_mrr_enabled.setChecked(mri.enabled)
        self.in_mrr_hours = QDoubleSpinBox(gb_sched)
        self.in_mrr_hours.setRange(0.25, 24 * 30)
        self.in_mrr_hours.setSingleStep(0.5)
        self.in_mrr_hours.setDecimals(2)
        self.in_mrr_hours.setSuffix(" h")
        self.in_mrr_hours.setValue(mri.hours)
        mrr_row = QWidget(gb_sched)
        mrr_lay = QHBoxLayout(mrr_row)
        mrr_lay.setContentsMargins(0, 0, 0, 0)
        mrr_lay.setSpacing(8)
        mrr_lay.addWidget(self.in_mrr_enabled)
        mrr_lay.addWidget(self.in_mrr_hours)
        mrr_lay.addStretch(1)
        fs.addRow("Min re-run interval", mrr_row)

        _add_tab("Schedule", gb_sched)

        # --- Quota condition (2x2 AND grid) ---
        self.gb_quota = QWidget()
        fq = QFormLayout(self.gb_quota)
        self._style_form(fq)
        qc = task.quota_condition if task else None
        self.in_qc_enabled = QCheckBox(
            "Run only when ALL checked quota clauses hold", self.gb_quota
        )
        self.in_qc_enabled.setChecked(qc is not None)
        fq.addRow("", self.in_qc_enabled)

        grid_w = QWidget(self.gb_quota)
        grid = QGridLayout(grid_w)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(4)
        grid.addWidget(QLabel("used", grid_w), 0, 1)
        grid.addWidget(QLabel("resets within", grid_w), 0, 2)
        grid.addWidget(QLabel("weekly", grid_w), 1, 0)
        grid.addWidget(QLabel("5-hour", grid_w), 2, 0)
        _row = {"weekly": 1, "5h": 2}
        _col = {"used": 1, "reset": 2}
        self.qc_cells: dict[str, tuple[QCheckBox, QSpinBox]] = {}
        # per-`used`-clause comparison operator (≥ / ≤); reset clauses have none.
        self.qc_ops: dict[str, QComboBox] = {}
        for attr, window, kind, _label in QUOTA_CLAUSES:
            cb = QCheckBox(grid_w)
            sb = QSpinBox(grid_w)
            op_combo: QComboBox | None = None
            if kind == "used":
                sb.setRange(0, 100)
                sb.setSuffix(" %")
                op_combo = QComboBox(grid_w)
                op_combo.addItem("≥", "gte")
                op_combo.addItem("≤", "lte")
            else:
                sb.setRange(1, 24 * 14)  # hours, up to ~2 weeks
                sb.setSuffix(" h")
            if qc:
                clause: QuotaClause = getattr(qc, attr)
                cb.setChecked(clause.enabled)
                sb.setValue(int(clause.value))
                if op_combo is not None:
                    op_combo.setCurrentIndex(max(0, op_combo.findData(clause.op)))
            else:
                sb.setValue(_QC_DEFAULTS[attr])
            cell = QWidget(grid_w)
            ch = QHBoxLayout(cell)
            ch.setContentsMargins(0, 0, 0, 0)
            ch.setSpacing(4)
            ch.addWidget(cb)
            if op_combo is not None:
                ch.addWidget(op_combo)
                self.qc_ops[attr] = op_combo
            ch.addWidget(sb)
            ch.addStretch(1)
            grid.addWidget(cell, _row[window], _col[kind])
            self.qc_cells[attr] = (cb, sb)
        grid.setColumnStretch(3, 1)
        fq.addRow("Clauses", grid_w)

        self.in_qc_on_unknown = QComboBox(self.gb_quota)
        self.in_qc_on_unknown.addItem("skip the run (safe)", "skip")
        self.in_qc_on_unknown.addItem("run anyway", "run")
        if qc:
            self.in_qc_on_unknown.setCurrentIndex(
                max(0, self.in_qc_on_unknown.findData(qc.on_unknown))
            )
        fq.addRow("If quota can't be read", self.in_qc_on_unknown)

        self.lbl_qc_hint = QLabel(self.gb_quota)
        self.lbl_qc_hint.setWordWrap(True)
        self.lbl_qc_hint.setObjectName("hint")
        fq.addRow("", self.lbl_qc_hint)
        _add_tab("Quota", self.gb_quota)

        # --- 알림 설정 (Telegram Notification) ---
        gb_notify = QWidget()
        fn = QFormLayout(gb_notify)
        self._style_form(fn)

        self.in_notify_fail = QCheckBox("실패 시 텔레그램 알림 전송 (On Failure)", gb_notify)
        if task:
            self.in_notify_fail.setChecked(task.on_failure.notify)
        fn.addRow("", self.in_notify_fail)

        self.in_notify_artifact = QCheckBox("성공 시 결과물 파일 전송 (Send Artifact)", gb_notify)
        if task:
            self.in_notify_artifact.setChecked(task.notify_artifact)
        fn.addRow("", self.in_notify_artifact)

        self.in_notify_final = QCheckBox(
            "성공 시 에이전트 최종 메시지 전송 (Final reply)", gb_notify
        )
        if task:
            self.in_notify_final.setChecked(task.notify_final_message)
        fn.addRow("", self.in_notify_final)

        self.in_artifact_dir = QLineEdit(gb_notify)
        self.in_artifact_dir.setPlaceholderText("(선택 사항) 결과물이 저장될 폴더 경로")
        if task and task.artifact_dir:
            self.in_artifact_dir.setText(task.artifact_dir)
        btn_art_dir = QPushButton("Browse…", gb_notify)

        def pick_art_dir():
            path = QFileDialog.getExistingDirectory(
                self,
                "Select artifact output directory",
                self.in_artifact_dir.text() or str(Path.home()),
            )
            if path:
                self.in_artifact_dir.setText(path)

        btn_art_dir.clicked.connect(pick_art_dir)
        row_art = QWidget(gb_notify)
        row_art_lay = QHBoxLayout(row_art)
        row_art_lay.setContentsMargins(0, 0, 0, 0)
        row_art_lay.setSpacing(6)
        row_art_lay.addWidget(self.in_artifact_dir, 1)
        row_art_lay.addWidget(btn_art_dir)
        fn.addRow("Result Directory", row_art)

        self.in_artifact_ext = QLineEdit(gb_notify)
        self.in_artifact_ext.setPlaceholderText("콤마로 구분, 예: .md, .png (기본값: .md)")
        if task and task.artifact_extensions:
            self.in_artifact_ext.setText(", ".join(task.artifact_extensions))
        else:
            self.in_artifact_ext.setText(".md")
        fn.addRow("Extensions filter", self.in_artifact_ext)

        _add_tab("Notifications", gb_notify)
        outer.addWidget(tabs, 1)

        # --- Preview ---
        gb_prev = QGroupBox("Preview command", self)
        fp2 = QVBoxLayout(gb_prev)
        self.preview_text = QPlainTextEdit(gb_prev)
        self.preview_text.setReadOnly(True)
        self.preview_text.setFont(mono_font(9))
        self.preview_text.setFixedHeight(96)
        fp2.addWidget(self.preview_text)
        outer.addWidget(gb_prev)

        # --- Buttons ---
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.RestoreDefaults,
            parent=self,
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.StandardButton.RestoreDefaults).clicked.connect(
            self._restore_defaults
        )
        outer.addWidget(buttons)

    def _connect_signals(self) -> None:
        self.in_agent.currentTextChanged.connect(self._refresh_agent_dependent_fields)
        self.in_model.currentTextChanged.connect(self._refresh_preview)
        self.in_extra.textChanged.connect(self._refresh_preview)
        self.in_prompt_source.currentIndexChanged.connect(self._toggle_prompt_source)
        self.in_prompt.textChanged.connect(self._refresh_preview)
        self.in_prompt_editor.textChanged.connect(self._refresh_preview)
        self.in_cwd.textChanged.connect(self._refresh_preview)
        self.in_timeout.valueChanged.connect(self._refresh_preview)
        self.in_preset.currentIndexChanged.connect(self._on_preset_changed)
        self.in_mode.currentIndexChanged.connect(self._on_mode_changed)
        self.in_time.timeChanged.connect(self._refresh_preview)
        self.in_tz.currentTextChanged.connect(self._refresh_preview)
        self.in_interval.valueChanged.connect(self._refresh_preview)

        self.in_agent.currentTextChanged.connect(self._refresh_quota_condition_ui)
        self.in_qc_enabled.toggled.connect(self._refresh_quota_condition_ui)
        for _cb, _sb in self.qc_cells.values():
            _cb.toggled.connect(self._refresh_quota_condition_ui)
        self.in_mrr_enabled.toggled.connect(
            lambda c: self.in_mrr_hours.setEnabled(c or self.in_mode.currentData() == "quota")
        )

        self.in_notify_artifact.toggled.connect(self._toggle_artifact_fields)
        self._toggle_artifact_fields(self.in_notify_artifact.isChecked())
        self._toggle_prompt_source()
        self._on_mode_changed()

    def _toggle_artifact_fields(self, checked: bool) -> None:
        self.in_artifact_dir.setEnabled(checked)
        self.in_artifact_ext.setEnabled(checked)

    def _prompt_source(self) -> str:
        return self.in_prompt_source.currentData() or "file"

    def _toggle_prompt_source(self) -> None:
        inline = self._prompt_source() == "inline"
        self.lbl_prompt_file.setVisible(not inline)
        self.row_prompt_file.setVisible(not inline)
        self.lbl_prompt_editor.setVisible(inline)
        self.in_prompt_editor.setVisible(inline)
        self._refresh_preview()

    # ---------------------------------------------------------- event handlers

    def _refresh_agent_dependent_fields(self) -> None:
        agent_id = self.in_agent.currentText()
        cfg = self.settings.agents.get(agent_id)
        # Update preset list
        self.in_preset.blockSignals(True)
        self.in_preset.clear()
        self.in_preset.addItem("(custom / none)", None)
        if cfg:
            for p in cfg.presets:
                self.in_preset.addItem(p.name, list(p.args))
        self.in_preset.blockSignals(False)
        self._populate_models(agent_id)
        self._refresh_preview()

    def _populate_models(self, agent_id: str) -> None:
        """Fill the Model dropdown from the agent's known models (async)."""
        cfg = self.settings.agents.get(agent_id)
        cur = self.in_model.currentText().strip()
        seed = [cfg.default_model] if (cfg and cfg.default_model) else []
        self._set_model_items(seed, keep=cur)
        if getattr(self, "_model_worker", None) is not None:
            return
        binp = None
        if cfg:
            from ..agents import resolve_binary

            binp = resolve_binary(cfg.binary)
        from .workers import ModelFetchWorker

        self._model_worker = ModelFetchWorker(agent_id, binp)
        self._model_worker.models_ready.connect(self._on_models_ready)
        self._model_worker.error.connect(lambda _e: setattr(self, "_model_worker", None))
        self._model_worker.start()

    def _on_models_ready(self, models: list) -> None:
        self._model_worker = None
        cfg = self.settings.agents.get(self.in_agent.currentText())
        seed = [cfg.default_model] if (cfg and cfg.default_model) else []
        self._set_model_items(seed + list(models), keep=self.in_model.currentText().strip())

    def _set_model_items(self, items: list, *, keep: str) -> None:
        uniq = list(dict.fromkeys(m for m in items if m))
        self.in_model.blockSignals(True)
        self.in_model.clear()
        self.in_model.addItems(uniq)
        self.in_model.setCurrentText(keep)
        self.in_model.blockSignals(False)

    def _on_mode_changed(self, _idx: int = -1) -> None:
        mode = self.in_mode.currentData()
        weekly = mode == "weekly"
        interval = mode == "interval"
        quota = mode == "quota"
        for cb in self.day_checks.values():
            cb.setEnabled(weekly)
        self.in_interval.setEnabled(interval)
        self.in_time.setEnabled(not interval and not quota)
        self.in_tz.setEnabled(not quota)
        self._refresh_quota_condition_ui()
        self._refresh_preview()

    def _refresh_quota_condition_ui(self, *_args) -> None:
        """Enable / populate the quota-condition widgets for the current agent+mode.

        In ``quota`` mode both the quota condition and the min re-run interval are
        mandatory, so they are forced on and locked.  For a non-quota-capable
        agent the whole condition is unavailable.
        """
        agent = self.in_agent.currentText()
        capable = agent in QUOTA_CAPABLE_AGENTS
        quota_mode = self.in_mode.currentData() == "quota"

        if quota_mode:
            for w, val in ((self.in_qc_enabled, True), (self.in_mrr_enabled, True)):
                w.blockSignals(True)
                w.setChecked(val)
                w.blockSignals(False)
            self.in_qc_enabled.setEnabled(False)
            self.in_mrr_enabled.setEnabled(False)
        else:
            self.in_mrr_enabled.setEnabled(True)
            self.in_qc_enabled.setEnabled(capable)
            if not capable and self.in_qc_enabled.isChecked():
                self.in_qc_enabled.blockSignals(True)
                self.in_qc_enabled.setChecked(False)
                self.in_qc_enabled.blockSignals(False)

        self.in_mrr_hours.setEnabled(self.in_mrr_enabled.isChecked() or quota_mode)

        on = self.in_qc_enabled.isChecked() and capable
        has_5h = ("5h" in available_windows(agent)) if capable else False
        for attr, window, _kind, _label in QUOTA_CLAUSES:
            cb, sb = self.qc_cells[attr]
            row_ok = on and (window != "5h" or has_5h)
            if window == "5h" and not has_5h and cb.isChecked():
                cb.blockSignals(True)
                cb.setChecked(False)
                cb.blockSignals(False)
            cb.setEnabled(row_ok)
            sb.setEnabled(row_ok and cb.isChecked())
            op_combo = self.qc_ops.get(attr)
            if op_combo is not None:
                op_combo.setEnabled(row_ok and cb.isChecked())
        self.in_qc_on_unknown.setEnabled(on)

        if not capable:
            self.lbl_qc_hint.setText(
                f"{agent} does not report quota — condition unavailable "
                "(engines with quota: " + ", ".join(sorted(QUOTA_CAPABLE_AGENTS)) + ")."
            )
        elif quota_mode:
            self.lbl_qc_hint.setText(
                "No time trigger. The quota poll checks this every "
                f"~{self.settings.quota_poll.interval_minutes} min and runs the task "
                "when the condition holds (min re-run interval still applies)."
            )
        elif on:
            extra = "" if has_5h else f"  ({agent} reports the weekly window only.)"
            self.lbl_qc_hint.setText(
                "Runs at the scheduled time only when every checked clause holds; "
                "otherwise it is skipped (not a failure)." + extra
            )
        else:
            self.lbl_qc_hint.setText("")
        self._refresh_preview()

    def done(self, r: int) -> None:  # noqa: D401 — stop the model worker cleanly
        w = getattr(self, "_model_worker", None)
        if w is not None:
            try:
                w.quit()
                w.wait(1000)
            except RuntimeError:
                pass
            self._model_worker = None
        super().done(r)

    def _on_preset_changed(self, _idx: int) -> None:
        data = self.in_preset.currentData()
        if data is None:
            return
        self.in_extra.blockSignals(True)
        self.in_extra.setText(" ".join(data))
        self.in_extra.blockSignals(False)
        self._refresh_preview()

    def _pick_prompt(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select prompt md file",
            self.in_prompt.text() or str(Path.home()),
            "Markdown (*.md);;All files (*)",
        )
        if path:
            self.in_prompt.setText(path)

    def _pick_cwd(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self, "Select working directory", self.in_cwd.text() or str(Path.home())
        )
        if path:
            self.in_cwd.setText(path)

    def _restore_defaults(self) -> None:
        defaults = self.settings.defaults
        self.in_timeout.setValue(defaults.timeout_minutes)
        self.in_tz.setCurrentText("Asia/Seoul")
        self.in_time.setTime(QTime(7, 0))
        self.in_model.setCurrentText("")
        self.in_extra.clear()
        for code, cb in self.day_checks.items():
            cb.setChecked(code in ("mon", "tue", "wed", "thu", "fri"))
        self.in_notify_fail.setChecked(False)
        self.in_notify_artifact.setChecked(False)
        self.in_notify_final.setChecked(False)
        self.in_artifact_dir.clear()
        self.in_artifact_ext.setText(".md")
        self.in_mrr_enabled.setChecked(True)
        self.in_mrr_hours.setValue(6.0)
        self.in_qc_enabled.setChecked(False)
        for attr, (cb, sb) in self.qc_cells.items():
            cb.setChecked(False)
            sb.setValue(_QC_DEFAULTS[attr])
        for op_combo in self.qc_ops.values():
            op_combo.setCurrentIndex(0)  # back to ≥
        self.in_qc_on_unknown.setCurrentIndex(0)
        self._refresh_quota_condition_ui()
        self._refresh_preview()

    def _refresh_preview(self) -> None:
        if self._prompt_source() == "inline":
            if not self.in_prompt_editor.toPlainText().strip():
                self.preview_text.setPlainText("(write the instruction to see preview)")
                return
        elif not self.in_prompt.text().strip():
            self.preview_text.setPlainText("(set prompt file to see preview)")
            return
        try:
            preview = self._build_preview_dict()
        except Exception as exc:  # noqa: BLE001
            self.preview_text.setPlainText(f"(preview error: {exc})")
            return
        quoted = " ".join(preview["argv_quoted"])
        self.preview_text.setPlainText(f"$ {quoted}")

    def _build_preview_dict(self) -> dict:
        from ..agents import get_adapter

        agent_id = self.in_agent.currentText()
        cfg = self.settings.agents.get(agent_id)
        if cfg is None:
            return {"argv_quoted": [f"(no settings for {agent_id!r})"]}
        adapter = get_adapter(agent_id)
        bypass = cfg.bypass.scheduled
        model = self.in_model.currentText().strip() or cfg.default_model
        cwd = Path(self.in_cwd.text()).expanduser() if self.in_cwd.text().strip() else None
        extra = shlex_split(self.in_extra.text())

        tmp_prompt: Optional[Path] = None
        if self._prompt_source() == "inline":
            import tempfile

            fd, tmp_name = tempfile.mkstemp(suffix=".md", text=True)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(self.in_prompt_editor.toPlainText())
            tmp_prompt = Path(tmp_name)
            prompt_file = tmp_prompt
        else:
            prompt_file = Path(self.in_prompt.text()).expanduser()

        try:
            if not prompt_file.exists():
                return {"argv_quoted": [f"(prompt file not found: {prompt_file})"]}
            res = adapter.build_argv(
                prompt_file,
                model=model,
                working_dir=cwd,
                extra_args=extra,
                bypass_flags=bypass,
            )
            return {"argv_quoted": [shlex_quote(a) for a in res.argv]}
        finally:
            if tmp_prompt is not None:
                try:
                    tmp_prompt.unlink()
                except OSError:
                    pass

    def _on_accept(self) -> None:
        name = self.in_name.text().strip()
        if not name:
            QMessageBox.warning(self, "Missing name", "Task name is required.")
            return

        # --- Run limits: validate widgets BEFORE any disk write (no orphan .md) ---
        mode = self.in_mode.currentData()
        agent = self.in_agent.currentText()
        qc_on = self.in_qc_enabled.isChecked()

        if mode == "weekly" and not any(cb.isChecked() for cb in self.day_checks.values()):
            QMessageBox.warning(
                self,
                "No weekdays selected",
                "Weekly mode needs at least one weekday, or pick daily / interval / quota.",
            )
            return

        if qc_on and agent not in QUOTA_CAPABLE_AGENTS:
            QMessageBox.warning(
                self,
                "Quota not available",
                f"{agent!r} does not report quota usage. Turn the quota condition "
                "off, or choose one of: " + ", ".join(sorted(QUOTA_CAPABLE_AGENTS)) + ".",
            )
            return
        if mode == "quota" and not qc_on:
            QMessageBox.warning(
                self,
                "Quota condition required",
                "A quota-triggered task has no time schedule, so it needs a quota "
                "condition to decide when to run. Enable it, or pick a time-based mode.",
            )
            return

        mrr_enabled = self.in_mrr_enabled.isChecked() or mode == "quota"
        if mode == "quota" and not mrr_enabled:
            QMessageBox.warning(
                self,
                "Min re-run interval required",
                "Quota-triggered tasks must keep a minimum re-run interval so the "
                "poller cannot fire them back-to-back. Enable it to continue.",
            )
            return
        min_rerun = MinRerunInterval(
            enabled=mrr_enabled, hours=float(self.in_mrr_hours.value())
        )
        quota_condition = None
        if qc_on and agent in QUOTA_CAPABLE_AGENTS:
            clauses: dict[str, QuotaClause] = {}
            any_checked = False
            for attr, _window, _kind, _label in QUOTA_CLAUSES:
                cb, sb = self.qc_cells[attr]
                enabled = cb.isChecked() and cb.isEnabled()
                any_checked = any_checked or enabled
                op_combo = self.qc_ops.get(attr)
                op = op_combo.currentData() if op_combo is not None else "gte"
                clauses[attr] = QuotaClause(
                    enabled=enabled, value=float(sb.value()), op=op or "gte"
                )
            if not any_checked:
                QMessageBox.warning(
                    self,
                    "No quota clause checked",
                    "Check at least one clause (weekly / 5-hour · used % or resets "
                    "within), or turn the quota condition off.",
                )
                return
            quota_condition = QuotaCondition(
                on_unknown=self.in_qc_on_unknown.currentData() or "skip", **clauses
            )

        if self._prompt_source() == "inline":
            body = self.in_prompt_editor.toPlainText().strip()
            if not body:
                QMessageBox.warning(
                    self,
                    "Missing instruction",
                    "Write the task instruction, or switch Prompt source to "
                    "“Existing file”.",
                )
                return
            existing = self.in_prompt.text().strip() or None
            try:
                saved = save_inline_prompt(body, name=name, existing_path=existing)
            except OSError as exc:
                QMessageBox.critical(self, "Save failed", f"Could not write prompt file:\n{exc}")
                return
            self.in_prompt.setText(str(saved))
            prompt_file = str(saved)
        else:
            prompt_file = self.in_prompt.text().strip()
            if not prompt_file:
                QMessageBox.warning(self, "Missing prompt file", "Prompt file is required.")
                return
            if not Path(prompt_file).expanduser().exists():
                QMessageBox.warning(
                    self, "Prompt file missing", f"File does not exist:\n{prompt_file}"
                )
                return

        # Persist
        from ..cli import load_tasks

        tasks = load_tasks(tasks_file())

        ext_list = [x.strip() for x in self.in_artifact_ext.text().split(",") if x.strip()]
        if not ext_list:
            ext_list = [".md"]

        new_task = Task(
            id=self.task_id,
            name=name,
            enabled=True,
            agent=agent,
            model=self.in_model.currentText().strip() or None,
            prompt_file=prompt_file,
            working_dir=self.in_cwd.text().strip() or None,
            schedule=self._build_schedule(),
            extra_args=shlex_split(self.in_extra.text()),
            timeout_minutes=self.in_timeout.value(),
            on_failure=OnFailure(notify=self.in_notify_fail.isChecked()),
            notify_artifact=self.in_notify_artifact.isChecked(),
            notify_final_message=self.in_notify_final.isChecked(),
            artifact_dir=self.in_artifact_dir.text().strip() or None,
            artifact_extensions=ext_list,
            min_rerun_interval=min_rerun,
            quota_condition=quota_condition,
        )
        # Replace if same id
        tasks = [t for t in tasks if t.id != new_task.id]
        tasks.append(new_task)
        save_tasks(tasks_file(), tasks)
        self.task = new_task
        self.accept()

    def _build_schedule(self) -> Schedule:
        days = [c for c, cb in self.day_checks.items() if cb.isChecked()]
        return Schedule(
            mode=self.in_mode.currentData(),
            days=days,
            time=self.in_time.time().toString("HH:mm"),
            timezone=self.in_tz.currentText().strip() or "Asia/Seoul",
            interval_minutes=self.in_interval.value(),
        )


# ---------------------------------------------------------------------- utils


def shlex_split(text: str) -> list[str]:
    import shlex

    try:
        return shlex.split(text) if text.strip() else []
    except ValueError:
        return text.split()


def shlex_quote(token: str) -> str:
    import shlex

    return shlex.quote(token)
