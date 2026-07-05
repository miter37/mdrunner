"""Main application window — task list + run-now + live log.

Headless engine (Phase 1) handles execution. This module is purely
UI orchestration on top of it.
"""

from __future__ import annotations

import datetime as _dt
from typing import Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QColor, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QHeaderView,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QSplitter,
    QStatusBar,
    QStyle,
    QTableWidget,
    QTableWidgetItem,
    QToolBar,
)

from .. import __version__
from ..config import (
    Settings,
    Task,
    find_task,
    load_settings,
    load_tasks,
    save_settings,
    save_tasks,
)
from ..scheduler.base import current as current_scheduler
from ..utils.paths import settings_file, tasks_file
from .log_view import LogView
from .settings_dialog import SettingsDialog
from .task_dialog import TaskDialog
from .workers import HealthCheckWorker, TaskRunWorker


COL_NAME = 0
COL_AGENT = 1
COL_SCHEDULE = 2
COL_STATUS = 3
COL_NEXT_RUN = 4
COL_LAST_RUN = 5
COL_SAVED = 6


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(f"mdrunner {__version__}")
        self.resize(1200, 700)

        self.settings: Settings = load_settings(settings_file())
        self.tasks: list[Task] = self._load_tasks()

        self._build_ui()
        self._build_menu()
        self._build_toolbar()
        self._build_statusbar()

        self.refresh_table()
        self.refresh_health()

        # Auto health check on startup
        QTimer.singleShot(0, self._start_health_check)

        # Periodic refresh of "next run" column
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(60_000)
        self._refresh_timer.timeout.connect(self._refresh_next_run_column)
        self._refresh_timer.start()

    # ------------------------------------------------------------------ UI

    def _build_ui(self) -> None:
        splitter = QSplitter(Qt.Orientation.Vertical, self)

        # Task table
        self.table = QTableWidget(0, 7, self)
        self.table.setHorizontalHeaderLabels(
            ["Name", "Agent", "Schedule", "Status", "Next run", "Last run", "Saved"]
        )
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(COL_NAME, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(COL_AGENT, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(COL_SCHEDULE, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(COL_STATUS, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(COL_NEXT_RUN, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(COL_LAST_RUN, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(COL_SAVED, QHeaderView.ResizeMode.Stretch)
        self.table.doubleClicked.connect(self._on_row_double_clicked)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._on_context_menu)
        splitter.addWidget(self.table)

        # Log view
        self.log_view = LogView(self)
        splitter.addWidget(self.log_view)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)

        self.setCentralWidget(splitter)

    def _build_menu(self) -> None:
        menubar = self.menuBar()
        m_file = menubar.addMenu("&File")
        a_refresh = QAction("&Refresh", self)
        a_refresh.setShortcut(QKeySequence("F5"))
        a_refresh.triggered.connect(self.refresh_all)
        m_file.addAction(a_refresh)

        m_task = menubar.addMenu("&Task")
        a_add = QAction("&Add…", self)
        a_add.setShortcut(QKeySequence("Ctrl+N"))
        a_add.triggered.connect(self._on_add)
        m_task.addAction(a_add)
        a_edit = QAction("&Edit…", self)
        a_edit.setShortcut(QKeySequence("Ctrl+E"))
        a_edit.triggered.connect(self._on_edit)
        m_task.addAction(a_edit)
        a_delete = QAction("&Delete", self)
        a_delete.setShortcut(QKeySequence("Delete"))
        a_delete.triggered.connect(self._on_delete)
        m_task.addAction(a_delete)
        m_task.addSeparator()
        a_toggle = QAction("Toggle &Enabled", self)
        a_toggle.setShortcut(QKeySequence("Space"))
        a_toggle.triggered.connect(self._on_toggle)
        m_task.addAction(a_toggle)
        a_run = QAction("&Run Now", self)
        a_run.setShortcut(QKeySequence("Ctrl+R"))
        a_run.triggered.connect(self._on_run_now)
        m_task.addAction(a_run)

        m_settings = menubar.addMenu("&Settings")
        a_settings = QAction("&Preferences…", self)
        a_settings.setShortcut(QKeySequence("Ctrl+,"))
        a_settings.triggered.connect(self._on_settings)
        m_settings.addAction(a_settings)
        a_health = QAction("Run &Health Check", self)
        a_health.triggered.connect(self._start_health_check)
        m_settings.addAction(a_health)

    def _build_toolbar(self) -> None:
        tb = QToolBar("Main", self)
        tb.setMovable(False)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, tb)
        style = self.style()

        a_add = QAction(style.standardIcon(QStyle.StandardPixmap.SP_FileDialogNewFolder), "&Add…", self)
        a_add.triggered.connect(self._on_add)
        tb.addAction(a_add)

        a_edit = QAction(style.standardIcon(QStyle.StandardPixmap.SP_FileDialogDetailedView), "&Edit…", self)
        a_edit.triggered.connect(self._on_edit)
        tb.addAction(a_edit)

        a_delete = QAction(style.standardIcon(QStyle.StandardPixmap.SP_TrashIcon), "&Delete", self)
        a_delete.triggered.connect(self._on_delete)
        tb.addAction(a_delete)

        tb.addSeparator()

        a_run = QAction(style.standardIcon(QStyle.StandardPixmap.SP_MediaPlay), "&Run Now", self)
        a_run.triggered.connect(self._on_run_now)
        tb.addAction(a_run)

        a_toggle = QAction(style.standardIcon(QStyle.StandardPixmap.SP_DialogApplyButton), "Toggle", self)
        a_toggle.triggered.connect(self._on_toggle)
        tb.addAction(a_toggle)

        tb.addSeparator()

        a_settings = QAction(style.standardIcon(QStyle.StandardPixmap.SP_FileDialogContentsView), "&Settings", self)
        a_settings.triggered.connect(self._on_settings)
        tb.addAction(a_settings)

    def _build_statusbar(self) -> None:
        sb = QStatusBar(self)
        self.setStatusBar(sb)
        self.status_health = QLabel("health: …", self)
        self.status_health.setMinimumWidth(200)
        sb.addPermanentWidget(self.status_health)
        self.status_msg = QLabel("", self)
        sb.addWidget(self.status_msg, 1)

    # -------------------------------------------------------------- Data

    def _load_tasks(self) -> list[Task]:
        try:
            return load_tasks(tasks_file())
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "tasks.yaml error", f"failed to load tasks: {exc}")
            return []

    def refresh_table(self) -> None:
        # Preserve selection by task id
        prev_id = self._selected_task_id()
        self.table.setRowCount(0)
        for t in self.tasks:
            self._append_row(t)
        if prev_id is not None:
            for row in range(self.table.rowCount()):
                if self.table.item(row, COL_NAME).data(Qt.ItemDataRole.UserRole) == prev_id:
                    self.table.selectRow(row)
                    break
        self._refresh_next_run_column()

    def refresh_health(self) -> None:
        # Render last-known health into the status bar; real probe happens via worker
        parts: list[str] = []
        for agent_id, cfg in self.settings.agents.items():
            mark = "?" if not cfg.enabled else "✓"
            parts.append(f"{agent_id} {mark}")
        self.status_health.setText("health: " + " | ".join(parts) + "  (run Settings → Health to refresh)")

    def refresh_all(self) -> None:
        self.settings = load_settings(settings_file())
        self.tasks = self._load_tasks()
        self.refresh_table()
        self.refresh_health()
        self.status_msg.setText("refreshed")

    def _append_row(self, t: Task) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)

        item_name = QTableWidgetItem(t.name)
        item_name.setData(Qt.ItemDataRole.UserRole, t.id)
        if not t.enabled:
            item_name.setForeground(QColor("#888"))
        self.table.setItem(row, COL_NAME, item_name)
        self.table.setItem(row, COL_AGENT, QTableWidgetItem(t.agent))
        self.table.setItem(row, COL_SCHEDULE, QTableWidgetItem(_format_schedule(t)))
        status_item = QTableWidgetItem("—")
        status_item.setData(Qt.ItemDataRole.UserRole, t.id)
        self.table.setItem(row, COL_STATUS, status_item)
        self.table.setItem(row, COL_NEXT_RUN, QTableWidgetItem("—"))
        self.table.setItem(row, COL_LAST_RUN, QTableWidgetItem(_format_last_run(t)))
        self.table.setItem(row, COL_SAVED, QTableWidgetItem("—"))

    def _refresh_next_run_column(self) -> None:
        # Cheap local computation; real scheduler-derived next run shown in
        # Phase 3 once the scheduler integration is wired in.
        for row, t in enumerate(self.tasks):
            nxt = _estimate_next_run(t)
            self.table.item(row, COL_NEXT_RUN).setText(nxt or "—")

    def _selected_task_id(self) -> Optional[str]:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return None
        item = self.table.item(rows[0].row(), COL_NAME)
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _selected_task(self) -> Optional[Task]:
        tid = self._selected_task_id()
        if not tid:
            return None
        try:
            return find_task(self.tasks, tid)
        except KeyError:
            return None

    # -------------------------------------------------------------- Actions

    def _on_add(self) -> None:
        dlg = TaskDialog(parent=self, settings=self.settings, task=None)
        if dlg.exec() == TaskDialog.DialogCode.Accepted:
            self.tasks = self._load_tasks()
            self._install_schedule_for(dlg.task)
            self.refresh_table()
            self.status_msg.setText(f"added '{dlg.task.name}'")

    def _on_edit(self) -> None:
        task = self._selected_task()
        if task is None:
            self.status_msg.setText("no task selected")
            return
        dlg = TaskDialog(parent=self, settings=self.settings, task=task)
        if dlg.exec() == TaskDialog.DialogCode.Accepted:
            self.tasks = self._load_tasks()
            self._install_schedule_for(dlg.task)
            self.refresh_table()
            self.status_msg.setText(f"saved '{dlg.task.name}'")

    def _on_delete(self) -> None:
        task = self._selected_task()
        if task is None:
            self.status_msg.setText("no task selected")
            return
        ans = QMessageBox.question(
            self,
            "Delete task",
            f"Delete task '{task.name}'? This will also remove the OS scheduler entry.",
        )
        if ans != QMessageBox.StandardButton.Yes:
            return
        # Best-effort uninstall the OS scheduler entry first
        try:
            current_scheduler().uninstall(task)
        except Exception:  # noqa: BLE001
            pass
        self.tasks = [t for t in self.tasks if t.id != task.id]
        save_tasks(tasks_file(), self.tasks)
        self.refresh_table()
        self.status_msg.setText(f"deleted '{task.name}'")

    def _on_toggle(self) -> None:
        task = self._selected_task()
        if task is None:
            self.status_msg.setText("no task selected")
            return
        task.enabled = not task.enabled
        save_tasks(tasks_file(), self.tasks)
        self.refresh_table()
        self.status_msg.setText(f"{'enabled' if task.enabled else 'disabled'} '{task.name}'")

    def _on_run_now(self) -> None:
        task = self._selected_task()
        if task is None:
            self.status_msg.setText("no task selected")
            return
        self._start_task_run(task, mode="manual")

    def _install_schedule_for(self, task: Task) -> None:
        """Best-effort install of the OS scheduler entry for a task.

        Silent on errors — the user can always re-run from the CLI.
        """
        if not task.enabled:
            try:
                current_scheduler().uninstall(task)
            except Exception:  # noqa: BLE001
                pass
            return
        try:
            from ..cli import _mdrunner_executable_for_scheduler

            executable = _mdrunner_executable_for_scheduler()
            current_scheduler().install(task, executable)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(
                self,
                "Scheduler install failed",
                f"Could not install OS schedule for {task.id!r}: {exc}\n\n"
                f"You can still run the task manually. Use the CLI:\n"
                f"  mdrunner schedule-install {task.id}",
            )

    def _on_row_double_clicked(self, _index) -> None:
        self._on_edit()

    def _on_context_menu(self, pos) -> None:
        row = self.table.rowAt(pos.y())
        if row < 0:
            return
        self.table.selectRow(row)
        menu = QMenu(self)
        menu.addAction("Edit…", self._on_edit)
        menu.addAction("Run Now", self._on_run_now)
        menu.addAction("Toggle Enabled", self._on_toggle)
        menu.addSeparator()
        menu.addAction("Delete", self._on_delete)
        menu.exec(self.table.viewport().mapToGlobal(pos))

    def _on_settings(self) -> None:
        dlg = SettingsDialog(parent=self, settings=self.settings)
        if dlg.exec() == SettingsDialog.DialogCode.Accepted:
            self.settings = dlg.settings
            save_settings(settings_file(), self.settings)
            self.refresh_health()
            self._start_health_check()
            self.status_msg.setText("settings saved")

    # ------------------------------------------------------- Background work

    def _start_health_check(self) -> None:
        if hasattr(self, "_health_worker") and self._health_worker is not None:
            self._health_worker.cancel()
        self._health_worker = HealthCheckWorker(self.settings)
        self._health_worker.results_ready.connect(self._on_health_results)
        self._health_worker.start()
        self.status_msg.setText("health check running…")

    def _on_health_results(self, rows: list[dict]) -> None:
        parts: list[str] = []
        for r in rows:
            mark = "✓" if r["ok"] else "✗"
            version = r.get("version") or r.get("error") or ""
            parts.append(f"{r['agent']} {mark} {version[:24]}")
        self.status_health.setText("health: " + " | ".join(parts))
        self.status_msg.setText("health check complete")

    def _start_task_run(self, task: Task, *, mode: str) -> None:
        self.log_view.set_task(task, mode=mode)
        if hasattr(self, "_run_worker") and self._run_worker is not None:
            QMessageBox.information(self, "Busy", "Another task is currently running.")
            return
        worker = TaskRunWorker(task, settings=self.settings, mode=mode)
        worker.line.connect(self.log_view.append_line)
        worker.finished_with_result.connect(self._on_task_finished)
        worker.start()
        self._run_worker = worker
        self.status_msg.setText(f"running '{task.name}' ({mode})…")

    def _on_task_finished(self, result_payload: dict) -> None:
        self._run_worker = None
        tid = result_payload["task_id"]
        ok = result_payload["ok"]
        self.status_msg.setText(
            f"{'✓' if ok else '✗'} '{tid}' done in {result_payload['duration']:.1f}s"
        )
        for row, t in enumerate(self.tasks):
            if t.id == tid:
                item = self.table.item(row, COL_STATUS)
                item.setText("✓ ok" if ok else "✗ fail")
                if not ok:
                    item.setForeground(QColor("#c33"))
                else:
                    item.setForeground(QColor("#393"))
                last_item = self.table.item(row, COL_LAST_RUN)
                last_item.setText(_dt.datetime.now().strftime("%Y-%m-%d %H:%M KST"))
                saved_item = self.table.item(row, COL_SAVED)
                saved_item.setText(result_payload.get("saved_file") or "—")
                break
        if result_payload.get("error"):
            QMessageBox.warning(self, "Task failed", str(result_payload["error"]))


# ---------------------------------------------------------------------- helpers


def _format_schedule(t: Task) -> str:
    s = t.schedule
    if s.mode == "once":
        return f"once @ {s.time}"
    if s.mode == "daily":
        return f"daily @ {s.time} ({s.timezone})"
    if s.mode == "weekly":
        days = ",".join(s.days) if s.days else "?"
        return f"{days} @ {s.time} ({s.timezone})"
    if s.mode == "interval":
        return f"every {s.interval_minutes}m"
    return s.mode


def _format_last_run(t: Task) -> str:
    # Heuristic: task id -> log file timestamp if present
    from ..utils.paths import task_log_file

    p = task_log_file(t.id)
    if not p.exists():
        return "—"
    mtime = _dt.datetime.fromtimestamp(p.stat().st_mtime)
    return mtime.strftime("%Y-%m-%d %H:%M")


def _estimate_next_run(t: Task) -> Optional[str]:
    """Cheap local estimate from the schedule spec; no I/O."""
    if not t.enabled:
        return "disabled"
    s = t.schedule
    if s.mode == "once":
        return "—"
    if s.mode == "interval":
        return f"~ every {s.interval_minutes}m"
    if s.mode == "daily":
        return f"daily {s.time}"
    if s.mode == "weekly":
        days = ",".join(s.days) if s.days else "?"
        return f"{days} {s.time}"
    return None


def main() -> int:
    import sys

    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("mdrunner")
    app.setOrganizationName("mdrunner")
    win = MainWindow()
    win.show()
    return app.exec()