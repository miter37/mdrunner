"""Live log view — shows streaming stdout from a running task."""

from __future__ import annotations

from datetime import datetime

from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QLabel, QPlainTextEdit, QVBoxLayout, QWidget

from ..config import Task
from .theme import mono_font


class LogView(QWidget):
    """Header label + read-only text view that streams agent output."""

    MAX_LINES = 5000

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 6, 0, 0)
        layout.setSpacing(2)

        self.header = QLabel("Log · no task running", self)
        self.header.setObjectName("logHeader")
        layout.addWidget(self.header)

        self.text = QPlainTextEdit(self)
        self.text.setReadOnly(True)
        self.text.setFont(mono_font(10))
        self.text.setMaximumBlockCount(self.MAX_LINES)
        self.text.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        layout.addWidget(self.text, 1)

        self._task: Task | None = None
        self._line_count = 0

    def set_task(self, task: Task, *, mode: str) -> None:
        self._task = task
        self.text.clear()
        self._line_count = 0
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.header.setText(
            f"Log: {task.name}  ({task.agent}, mode={mode})  started {ts}"
        )
        self.append_line(f"--- mdrunner {ts} task={task.id} mode={mode} agent={task.agent} ---")

    def append_line(self, line: str) -> None:
        if not line.endswith("\n"):
            line = line + "\n"
        self.text.appendPlainText(line.rstrip("\n"))
        self._line_count += 1
        # Auto-scroll
        cursor = self.text.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.text.setTextCursor(cursor)