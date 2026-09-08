"""Week view — the next 7 days of firings as an hour × day grid.

Same projection core as the headless `schedule-overview`; this module is
only the Qt rendering (modal dialog, hour rows × day columns).
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..schedule_view import WeekProjection, project_week
from .theme import mono_font


class ScheduleWeekDialog(QDialog):
    """Modal 7-day grid. ``tasks`` are projected at open time."""

    def __init__(self, parent: QWidget | None = None, *, tasks: list, days: int = 7) -> None:
        super().__init__(parent)
        self.setWindowTitle("Schedule · next 7 days")
        self.resize(760, 520)
        self.setMinimumSize(560, 360)

        self._proj: WeekProjection = project_week(tasks, days=days)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 12, 14, 12)
        outer.setSpacing(8)

        sub = QLabel(
            "From tasks.yaml — what would fire. "
            "“quota…” rows have no time trigger; “once…” fires a single time.",
            self,
        )
        sub.setObjectName("dim")
        sub.setWordWrap(True)
        outer.addWidget(sub)

        hours = self._hour_range()
        self.grid = QTableWidget(len(hours), len(self._proj.days) + 1, self)
        self.grid.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.grid.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.grid.setShowGrid(False)
        self.grid.verticalHeader().setVisible(False)
        headers = [""] + [d.strftime("%a %m-%d") for d in self._proj.days]
        self.grid.setHorizontalHeaderLabels(headers)
        hh = self.grid.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        for c in range(1, len(headers)):
            hh.setSectionResizeMode(c, QHeaderView.ResizeMode.Stretch)
        mono = mono_font(10)
        for r, h in enumerate(hours):
            lab = QTableWidgetItem(f"{h:02d}:00")
            lab.setFont(mono)
            self.grid.setItem(r, 0, lab)
            for c, d in enumerate(self._proj.days, start=1):
                evs = [e for e in self._proj.events.get(d.isoformat(), []) if e.time.hour == h]
                if not evs:
                    continue
                cell = QTableWidgetItem(
                    "\n".join(f"{e.time.strftime('%H:%M')} {e.name}" for e in evs)
                )
                cell.setFont(mono)
                cell.setToolTip("\n".join(f"{e.name} ({e.task_id})" for e in evs))
                self.grid.setItem(r, c, cell)
        outer.addWidget(self.grid, 1)

        if self._proj.once or self._proj.quota:
            extra = []
            for _label, e in self._proj.once:
                extra.append(f"once · {e.time.strftime('%H:%M')} {e.name}")
            for name in self._proj.quota:
                extra.append(f"quota · {name}")
            foot = QLabel("\n".join(extra), self)
            foot.setObjectName("dim")
            foot.setWordWrap(True)
            outer.addWidget(foot)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, parent=self)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

    def _hour_range(self) -> list[int]:
        seen = {e.time.hour for evs in self._proj.events.values() for e in evs}
        if not seen:
            return list(range(6, 24))
        lo = max(0, min(seen) - 1)
        hi = min(23, max(seen) + 1)
        return list(range(lo, hi + 1))
