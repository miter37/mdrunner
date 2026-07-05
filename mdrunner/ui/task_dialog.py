"""Task add/edit dialog."""

from __future__ import annotations

import datetime as _dt
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QTime
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from ..config import (
    OnFailure,
    Schedule,
    Settings,
    Task,
    save_tasks,
)
from ..utils.paths import tasks_file


WEEKDAYS = [
    ("mon", "Mon"),
    ("tue", "Tue"),
    ("wed", "Wed"),
    ("thu", "Thu"),
    ("fri", "Fri"),
    ("sat", "Sat"),
    ("sun", "Sun"),
]

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
        self.resize(720, 640)

        self._build_ui(task)
        self._connect_signals()
        self._refresh_agent_dependent_fields()
        self._refresh_preview()

    # --------------------------------------------------------------- helpers

    def _suggest_id(self) -> str:
        return f"task_{_dt.datetime.now().strftime('%Y%m%d_%H%M%S')}"

    def _build_ui(self, task: Optional[Task]) -> None:
        outer = QVBoxLayout(self)

        # --- Identity ---
        gb_id = QGroupBox("Identity", self)
        form = QFormLayout(gb_id)
        self.in_name = QLineEdit(gb_id)
        if task:
            self.in_name.setText(task.name)
        form.addRow("Name", self.in_name)
        outer.addWidget(gb_id)

        # --- Agent ---
        gb_agent = QGroupBox("Agent", self)
        fa = QFormLayout(gb_agent)
        self.in_agent = QComboBox(gb_agent)
        for agent_id in sorted(self.settings.agents.keys()):
            self.in_agent.addItem(agent_id)
        if task:
            idx = self.in_agent.findText(task.agent)
            if idx >= 0:
                self.in_agent.setCurrentIndex(idx)
        fa.addRow("Agent", self.in_agent)

        self.in_model = QLineEdit(gb_agent)
        self.in_model.setPlaceholderText(
            "e.g. minimax-coding-plan/MiniMax-M3 (blank = use default)"
        )
        if task and task.model:
            self.in_model.setText(task.model)
        fa.addRow("Model", self.in_model)

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
        outer.addWidget(gb_agent)

        # --- Prompt + working dir ---
        gb_paths = QGroupBox("Files", self)
        fp = QFormLayout(gb_paths)
        self.in_prompt = QLineEdit(gb_paths)
        if task:
            self.in_prompt.setText(task.prompt_file)
        btn_prompt = QPushButton("Browse…", gb_paths)
        btn_prompt.clicked.connect(self._pick_prompt)
        rowp = QWidget(gb_paths)
        rowp_lay = QHBoxLayout(rowp)
        rowp_lay.setContentsMargins(0, 0, 0, 0)
        rowp_lay.addWidget(self.in_prompt, 1)
        rowp_lay.addWidget(btn_prompt)
        fp.addRow("Prompt file", rowp)

        self.in_cwd = QLineEdit(gb_paths)
        if task and task.working_dir:
            self.in_cwd.setText(task.working_dir)
        btn_cwd = QPushButton("Browse…", gb_paths)
        btn_cwd.clicked.connect(self._pick_cwd)
        rowc = QWidget(gb_paths)
        rowc_lay = QHBoxLayout(rowc)
        rowc_lay.setContentsMargins(0, 0, 0, 0)
        rowc_lay.addWidget(self.in_cwd, 1)
        rowc_lay.addWidget(btn_cwd)
        fp.addRow("Working dir (optional)", rowc)
        outer.addWidget(gb_paths)

        # --- Schedule ---
        gb_sched = QGroupBox("Schedule", self)
        fs = QFormLayout(gb_sched)
        self.in_mode = QComboBox(gb_sched)
        for m in ("once", "daily", "weekly", "interval"):
            self.in_mode.addItem(m)
        if task:
            idx = self.in_mode.findText(task.schedule.mode)
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
                hh, mm = task.schedule.time.split(":")
                self.in_time.setTime(QTime(int(hh), int(mm)))
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

        outer.addWidget(gb_sched)

        # --- 알림 설정 (Telegram Notification) ---
        gb_notify = QGroupBox("Telegram Notification", self)
        fn = QFormLayout(gb_notify)

        self.in_notify_fail = QCheckBox("실패 시 텔레그램 알림 전송 (On Failure)", gb_notify)
        if task:
            self.in_notify_fail.setChecked(task.on_failure.notify)
        fn.addRow("", self.in_notify_fail)

        self.in_notify_artifact = QCheckBox("성공 시 결과물 파일 전송 (Send Artifact)", gb_notify)
        if task:
            self.in_notify_artifact.setChecked(task.notify_artifact)
        fn.addRow("", self.in_notify_artifact)

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

        outer.addWidget(gb_notify)

        # --- Preview ---
        gb_prev = QGroupBox("Preview command", self)
        fp2 = QVBoxLayout(gb_prev)
        self.preview_text = QPlainTextEdit(gb_prev)
        self.preview_text.setReadOnly(True)
        self.preview_text.setMaximumHeight(120)
        fp2.addWidget(self.preview_text)
        outer.addWidget(gb_prev, 1)

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
        self.in_model.textChanged.connect(self._refresh_preview)
        self.in_extra.textChanged.connect(self._refresh_preview)
        self.in_prompt.textChanged.connect(self._refresh_preview)
        self.in_cwd.textChanged.connect(self._refresh_preview)
        self.in_timeout.valueChanged.connect(self._refresh_preview)
        self.in_preset.currentIndexChanged.connect(self._on_preset_changed)
        self.in_mode.currentTextChanged.connect(self._refresh_preview)
        self.in_time.timeChanged.connect(self._refresh_preview)
        self.in_tz.currentTextChanged.connect(self._refresh_preview)
        self.in_interval.valueChanged.connect(self._refresh_preview)

        self.in_notify_artifact.toggled.connect(self._toggle_artifact_fields)
        self._toggle_artifact_fields(self.in_notify_artifact.isChecked())

    def _toggle_artifact_fields(self, checked: bool) -> None:
        self.in_artifact_dir.setEnabled(checked)
        self.in_artifact_ext.setEnabled(checked)

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
            # Set default model placeholder
            if cfg.default_model and not self.in_model.text():
                self.in_model.setText(cfg.default_model)
        self.in_preset.blockSignals(False)
        self._refresh_preview()

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
        for code, _ in WEEKDAYS:
            self.in_preset.currentTextChanged.disconnect()  # type: ignore[attr-defined]
        self.in_model.clear()
        self.in_extra.clear()
        for code, cb in self.day_checks.items():
            cb.setChecked(code in ("mon", "tue", "wed", "thu", "fri"))
        self.in_notify_fail.setChecked(False)
        self.in_notify_artifact.setChecked(False)
        self.in_artifact_dir.clear()
        self.in_artifact_ext.setText(".md")
        self._refresh_preview()

    def _refresh_preview(self) -> None:
        if not self.in_prompt.text().strip():
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
        model = self.in_model.text().strip() or cfg.default_model
        cwd = Path(self.in_cwd.text()).expanduser() if self.in_cwd.text().strip() else None
        extra = shlex_split(self.in_extra.text())
        prompt_file = Path(self.in_prompt.text()).expanduser()
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

    def _on_accept(self) -> None:
        name = self.in_name.text().strip()
        prompt_file = self.in_prompt.text().strip()
        if not name:
            QMessageBox.warning(self, "Missing name", "Task name is required.")
            return
        if not prompt_file:
            QMessageBox.warning(self, "Missing prompt file", "Prompt file is required.")
            return
        if not Path(prompt_file).expanduser().exists():
            QMessageBox.warning(self, "Prompt file missing", f"File does not exist:\n{prompt_file}")
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
            agent=self.in_agent.currentText(),
            model=self.in_model.text().strip() or None,
            prompt_file=prompt_file,
            working_dir=self.in_cwd.text().strip() or None,
            schedule=self._build_schedule(),
            extra_args=shlex_split(self.in_extra.text()),
            timeout_minutes=self.in_timeout.value(),
            on_failure=OnFailure(notify=self.in_notify_fail.isChecked()),
            notify_artifact=self.in_notify_artifact.isChecked(),
            artifact_dir=self.in_artifact_dir.text().strip() or None,
            artifact_extensions=ext_list,
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
            mode=self.in_mode.currentText(),
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
