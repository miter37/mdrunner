"""The 2x2 quota-condition grid as a reusable widget.

Extracted from TaskDialog so the quota-alert dialog can offer the exact
same {weekly, 5-hour} × {used %, resets within} editor — one verdict
implementation (`quota_gate`), one editor.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..config import QUOTA_CLAUSES, QuotaClause, QuotaCondition
from ..quota_gate import available_windows

_QC_DEFAULTS = {
    "weekly_used": 90,
    "weekly_reset": 24,
    "fivehour_used": 90,
    "fivehour_reset": 3,
}


class QuotaConditionWidget(QWidget):
    """2x2 AND grid: weekly/5h rows × used/resets columns.

    ``set_agent`` dims the 5h row for agents that only report weekly
    (e.g. grok, via ``quota_gate.available_windows``). ``condition()``
    returns the edited ``QuotaCondition`` (with ``on_unknown="skip"`` —
    alerts never fire on unreadable quota).
    """

    def __init__(self, parent: QWidget | None = None, *, show_hint: bool = True) -> None:
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        grid_w = QWidget(self)
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
        self.cells: dict[str, tuple[QCheckBox, QSpinBox]] = {}
        self.ops: dict[str, QComboBox] = {}
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
            sb.setValue(_QC_DEFAULTS[attr])
            cel = QWidget(grid_w)
            ch = QHBoxLayout(cel)
            ch.setContentsMargins(0, 0, 0, 0)
            ch.setSpacing(4)
            ch.addWidget(cb)
            if op_combo is not None:
                ch.addWidget(op_combo)
                self.ops[attr] = op_combo
            ch.addWidget(sb)
            ch.addStretch(1)
            grid.addWidget(cel, _row[window], _col[kind])
            self.cells[attr] = (cb, sb)
        grid.setColumnStretch(3, 1)
        lay.addWidget(grid_w)

        self.show_hint = show_hint
        self.hint = QLabel(self)
        self.hint.setWordWrap(True)
        self.hint.setObjectName("hint")
        self.hint.setVisible(show_hint)
        lay.addWidget(self.hint)

        self._agent = ""
        self._master_enabled = True
        for cb, _sb in self.cells.values():
            cb.toggled.connect(self.refresh)
        self.refresh()

    # ------------------------------------------------------------ public API

    def set_condition(self, qc: QuotaCondition | None) -> None:
        for attr, (_cb, _sb) in self.cells.items():
            clause = getattr(qc, attr) if qc else None
            _cb.blockSignals(True)
            _cb.setChecked(bool(clause and clause.enabled))
            _cb.blockSignals(False)
            _sb.setValue(int(clause.value) if clause else _QC_DEFAULTS[attr])
            op_combo = self.ops.get(attr)
            if op_combo is not None:
                op_combo.setCurrentIndex(max(0, op_combo.findData(clause.op if clause else "gte")))
        self.refresh()

    def set_agent(self, agent: str) -> None:
        self._agent = agent
        self.refresh()

    def set_master_enabled(self, on: bool) -> None:
        self._master_enabled = on
        self.refresh()

    def condition(self) -> QuotaCondition | None:
        """The edited condition, or None when no clause is checked."""
        clauses: dict[str, QuotaClause] = {}
        any_checked = False
        for attr, _w, _k, _l in QUOTA_CLAUSES:
            cb, sb = self.cells[attr]
            enabled = cb.isChecked() and cb.isEnabled()
            any_checked = any_checked or enabled
            op_combo = self.ops.get(attr)
            op = op_combo.currentData() if op_combo is not None else "gte"
            clauses[attr] = QuotaClause(enabled=enabled, value=float(sb.value()), op=op or "gte")
        if not any_checked:
            return None
        return QuotaCondition(on_unknown="skip", **clauses)

    def refresh(self, *_args) -> None:
        capable = bool(self._agent)
        has_5h = ("5h" in available_windows(self._agent)) if self._agent else False
        on = self._master_enabled and capable
        for attr, window, _kind, _label in QUOTA_CLAUSES:
            cb, sb = self.cells[attr]
            row_ok = on and (window != "5h" or has_5h)
            if window == "5h" and not has_5h and cb.isChecked():
                cb.blockSignals(True)
                cb.setChecked(False)
                cb.blockSignals(False)
            cb.setEnabled(row_ok)
            sb.setEnabled(row_ok and cb.isChecked())
            op_combo = self.ops.get(attr)
            if op_combo is not None:
                op_combo.setEnabled(row_ok and cb.isChecked())
        if not self.show_hint:
            return
        if self._master_enabled and not self._agent:
            self.hint.setText("Pick an engine first.")
        elif not has_5h and self._agent:
            self.hint.setText(
                f"{self._agent} reports the weekly window only — 5-hour clauses are unavailable."
            )
        else:
            self.hint.setText(
                "Fires once when every checked clause holds; "
                "re-arms when the condition turns false."
            )
