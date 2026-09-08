"""Quota-alert add/edit dialog.

Name + enabled + one quota-capable engine + the shared 2x2 condition
grid. Deliberately one screen: alerts have no schedule, no cooldown,
no on_unknown — they fire once on the false→true edge and re-arm.
"""

from __future__ import annotations

import uuid

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..alerts import QuotaAlert
from ..config import QUOTA_CAPABLE_AGENTS
from .quota_condition_widget import QuotaConditionWidget
from .theme import style_form


class AlertDialog(QDialog):
    """Edit (or create) a single quota alert. Returns ``self.alert``."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        alert: QuotaAlert | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Quota alert" if alert else "New quota alert")
        self.setMinimumWidth(430)
        self._editing_id = alert.id if alert else uuid.uuid4().hex[:8]

        outer = QVBoxLayout(self)
        form = QWidget(self)
        fq = QFormLayout(form)
        style_form(fq)

        self.in_name = QLineEdit(form)
        self.in_name.setPlaceholderText("e.g. claude weekly almost full")
        self.in_name.setText(alert.name if alert else "")
        fq.addRow("Name", self.in_name)

        self.in_enabled = QCheckBox("Enabled", form)
        self.in_enabled.setChecked(alert.enabled if alert else True)
        fq.addRow("", self.in_enabled)

        self.in_agent = QComboBox(form)
        self.in_agent.addItems(sorted(QUOTA_CAPABLE_AGENTS))
        self.in_agent.setCurrentText(alert.agent if alert else "claude")
        fq.addRow("Engine", self.in_agent)

        self.in_message = QPlainTextEdit(form)
        self.in_message.setPlaceholderText(
            "알림 발송 시 보낼 메시지 (한/영) — e.g. codex 주간 사용량이 한도에 근접했습니다."
        )
        self.in_message.setPlainText(alert.message if alert else "")
        self.in_message.setFixedHeight(64)
        fq.addRow("Message", self.in_message)

        self.cond = QuotaConditionWidget(form)
        fq.addRow("Fire when ALL checked hold", self.cond)
        outer.addWidget(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

        self.alert: QuotaAlert | None = alert
        self.cond.set_condition(alert.condition if alert else None)
        self.cond.set_agent(self.in_agent.currentText())
        self.cond.set_master_enabled(self.in_enabled.isChecked())
        self.in_agent.currentTextChanged.connect(self._refresh_grid)
        self.in_enabled.toggled.connect(self._refresh_grid)

    def _refresh_grid(self, *_args) -> None:
        self.cond.set_agent(self.in_agent.currentText())
        self.cond.set_master_enabled(self.in_enabled.isChecked())

    def _on_accept(self) -> None:
        name = self.in_name.text().strip()
        if not name:
            QMessageBox.warning(self, "Missing name", "Give this alert a name.")
            return
        condition = self.cond.condition()
        if condition is None:
            QMessageBox.warning(
                self,
                "No clause checked",
                "Check at least one clause (weekly / 5-hour · used % or resets within).",
            )
            return
        self.alert = QuotaAlert(
            id=self._editing_id,
            name=name,
            agent=self.in_agent.currentText(),
            enabled=self.in_enabled.isChecked(),
            condition=condition,
            message=self.in_message.toPlainText().strip(),
        )
        self.accept()
