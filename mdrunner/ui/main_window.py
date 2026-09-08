"""Main application window — the operations console: what's scheduled,
what ran, what's next, and how much quota is left. Then act on it.

The headless engine handles execution; this module is UI orchestration.
"""

from __future__ import annotations

import datetime as _dt
from typing import Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QActionGroup, QColor, QFont, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QDockWidget,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QStatusBar,
    QTableWidget,
    QTableWidgetItem,
    QToolBar,
    QVBoxLayout,
    QWidget,
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
from . import icons, theme
from .delegates import STATUS_ROLE, SUBTITLE_ROLE, StatusPillDelegate, TwoLineDelegate
from .log_view import LogView
from .settings_dialog import SettingsDialog
from .task_dialog import TaskDialog
from .workers import AlertSendWorker, HealthCheckWorker, QuotaFetchWorker, TaskRunWorker

COL_NAME = 0
COL_AGENT = 1
COL_SCHEDULE = 2
COL_STATUS = 3
COL_NEXT_RUN = 4
COL_LAST_RUN = 5

_MONO_COLS = (COL_SCHEDULE, COL_NEXT_RUN, COL_LAST_RUN)
_COLUMNS = ["Task", "Agent", "Schedule", "Status", "Next run", "Last run"]


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(f"mdrunner {__version__}")
        self.resize(1180, 720)
        self.setMinimumSize(860, 480)

        app = QApplication.instance()
        self._theme_mode = theme.saved_mode()
        self._pal = theme.PALETTES[theme.apply_theme(app, self._theme_mode) if app else "light"]

        self.settings: Settings = load_settings(settings_file())
        self.tasks: list[Task] = self._load_tasks()

        self._name_delegate = TwoLineDelegate(self)
        self._status_delegate = StatusPillDelegate(self)

        self._build_ui()
        self._build_quota_panel()
        self._build_menu()
        self._build_toolbar()
        self._build_statusbar()
        self._retint()

        self.refresh_table()
        self.refresh_health()
        self._load_quota_from_snapshot()
        self._did_autostart = False

        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(60_000)
        self._refresh_timer.timeout.connect(self._refresh_next_run_column)
        self._refresh_timer.start()

    # ------------------------------------------------------------------ UI

    def _build_ui(self) -> None:
        splitter = QSplitter(Qt.Orientation.Vertical, self)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(1)

        self.table = QTableWidget(0, len(_COLUMNS), self)
        self.table.setHorizontalHeaderLabels(_COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(46)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(False)
        self.table.setItemDelegateForColumn(COL_NAME, self._name_delegate)
        self.table.setItemDelegateForColumn(COL_STATUS, self._status_delegate)
        header = self.table.horizontalHeader()
        header.setHighlightSections(False)
        header.setMinimumSectionSize(64)
        Mode = QHeaderView.ResizeMode
        header.setSectionResizeMode(COL_NAME, Mode.Stretch)
        header.setSectionResizeMode(COL_AGENT, Mode.ResizeToContents)
        header.setSectionResizeMode(COL_SCHEDULE, Mode.ResizeToContents)
        header.setSectionResizeMode(COL_STATUS, Mode.Fixed)
        header.setSectionResizeMode(COL_NEXT_RUN, Mode.ResizeToContents)
        header.setSectionResizeMode(COL_LAST_RUN, Mode.ResizeToContents)
        self.table.setColumnWidth(COL_STATUS, 112)
        self.table.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.table.doubleClicked.connect(self._on_row_double_clicked)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._on_context_menu)

        # Empty state
        empty = QWidget(self)
        el = QVBoxLayout(empty)
        el.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title = QLabel("No tasks scheduled yet", empty)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tf = QFont()
        tf.setPointSize(15)
        tf.setWeight(QFont.Weight.DemiBold)
        title.setFont(tf)
        sub = QLabel(
            "Add a task to run a Markdown instruction with a CLI agent on a schedule.",
            empty,
        )
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub.setObjectName("dim")
        btn = QPushButton("Add task", empty)
        btn.setDefault(True)
        btn.clicked.connect(self._on_add)
        el.addWidget(title)
        el.addSpacing(4)
        el.addWidget(sub)
        el.addSpacing(16)
        el.addWidget(btn, 0, Qt.AlignmentFlag.AlignCenter)

        self.center_stack = QStackedWidget(self)
        self.center_stack.addWidget(self.table)  # 0
        self.center_stack.addWidget(empty)  # 1
        splitter.addWidget(self.center_stack)

        self.log_view = LogView(self)
        splitter.addWidget(self.log_view)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)

        container = QWidget(self)
        cl = QVBoxLayout(container)
        cl.setContentsMargins(12, 10, 12, 10)
        cl.addWidget(splitter)
        self.setCentralWidget(container)

    def _build_quota_panel(self) -> None:
        self.quota_dock = QDockWidget("Agent Quota", self)
        self.quota_dock.setObjectName("quota_dock")
        self.quota_dock.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetMovable
            | QDockWidget.DockWidgetFeature.DockWidgetClosable
        )
        body = QWidget(self.quota_dock)
        lay = QVBoxLayout(body)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(8)

        self.quota_table = QTableWidget(0, 4, body)
        self.quota_table.setObjectName("quotaTable")
        self.quota_table.setHorizontalHeaderLabels(["Agent", "Window", "Used", "Reset"])
        self.quota_table.verticalHeader().setVisible(False)
        self.quota_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.quota_table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.quota_table.setShowGrid(False)
        self.quota_table.setFrameShape(QAbstractItemView.Shape.NoFrame)
        self.quota_table.verticalHeader().setDefaultSectionSize(30)
        self.quota_table.setTextElideMode(Qt.TextElideMode.ElideRight)
        qh = self.quota_table.horizontalHeader()
        qh.setHighlightSections(False)
        qh.setMinimumSectionSize(38)
        qh.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        qh.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        qh.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        qh.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        lay.addWidget(self.quota_table, 1)

        row = QHBoxLayout()
        self.quota_status = QLabel("Checking…", body)
        self.quota_status.setObjectName("dim")
        row.addWidget(self.quota_status, 1)
        self.quota_refresh_btn = QPushButton("Refresh", body)
        self.quota_refresh_btn.clicked.connect(self._start_quota_refresh)
        row.addWidget(self.quota_refresh_btn)
        lay.addLayout(row)

        # --- Quota alerts (Telegram on engine quota states) ---
        from PySide6.QtWidgets import QFrame

        sep = QFrame(body)
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setObjectName("alertSep")
        lay.addWidget(sep)

        al_title = QLabel("Usage alerts", body)
        al_title.setObjectName("logHeader")
        lay.addWidget(al_title)

        self.alert_list = QListWidget(body)
        self.alert_list.setObjectName("alertList")
        self.alert_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.alert_list.setMaximumHeight(112)
        self.alert_list.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.alert_list.itemDoubleClicked.connect(self._on_alert_edit)
        lay.addWidget(self.alert_list, 0)

        self.alert_hint = QLabel(body)
        self.alert_hint.setObjectName("dim")
        self.alert_hint.setWordWrap(True)
        self.alert_hint.setVisible(False)
        lay.addWidget(self.alert_hint)

        al_row = QHBoxLayout()
        self.alert_add_btn = QPushButton("Add…", body)
        self.alert_add_btn.clicked.connect(self._on_alert_add)
        al_row.addWidget(self.alert_add_btn)
        self.alert_edit_btn = QPushButton("Edit", body)
        self.alert_edit_btn.clicked.connect(self._on_alert_edit)
        al_row.addWidget(self.alert_edit_btn)
        self.alert_del_btn = QPushButton("Delete", body)
        self.alert_del_btn.clicked.connect(self._on_alert_delete)
        al_row.addWidget(self.alert_del_btn)
        al_row.addStretch(1)
        lay.addLayout(al_row)

        self._alerts: list = self._load_alerts()
        self._refresh_alert_list()

        self.quota_dock.setWidget(body)
        self.quota_dock.setMinimumWidth(280)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.quota_dock)
        self.resizeDocks([self.quota_dock], [330], Qt.Orientation.Horizontal)

    def _build_menu(self) -> None:
        menubar = self.menuBar()
        m_file = menubar.addMenu("&File")
        a_refresh = QAction("&Reload from disk", self)
        a_refresh.setShortcut(QKeySequence("F5"))
        a_refresh.triggered.connect(self.refresh_all)
        m_file.addAction(a_refresh)

        m_task = menubar.addMenu("&Task")
        for text, seq, slot in (
            ("&Add…", "Ctrl+N", self._on_add),
            ("&Edit…", "Ctrl+E", self._on_edit),
            ("&Delete", "Delete", self._on_delete),
        ):
            a = QAction(text, self)
            a.setShortcut(QKeySequence(seq))
            a.triggered.connect(slot)
            m_task.addAction(a)
        m_task.addSeparator()
        a_toggle = QAction("Enable / &disable", self)
        a_toggle.setShortcut(QKeySequence("Space"))
        a_toggle.triggered.connect(self._on_toggle)
        m_task.addAction(a_toggle)
        a_run = QAction("&Run now", self)
        a_run.setShortcut(QKeySequence("Ctrl+R"))
        a_run.triggered.connect(self._on_run_now)
        m_task.addAction(a_run)

        m_view = menubar.addMenu("&View")
        m_theme = m_view.addMenu("&Theme")
        self._theme_group = QActionGroup(self)
        for label, mode in (("System", "system"), ("Light", "light"), ("Dark", "dark")):
            a = QAction(label, self, checkable=True)
            a.setChecked(mode == self._theme_mode)
            a.triggered.connect(lambda _c, m=mode: self._set_theme(m))
            self._theme_group.addAction(a)
            m_theme.addAction(a)
        m_view.addSeparator()
        a_quota = self.quota_dock.toggleViewAction()
        a_quota.setText("Agent &Quota panel")
        m_view.addAction(a_quota)
        a_qr = QAction("Refresh q&uota", self)
        a_qr.triggered.connect(self._start_quota_refresh)
        m_view.addAction(a_qr)
        m_view.addSeparator()
        a_week = QAction("Schedule &week view…", self)
        a_week.triggered.connect(self._on_schedule_week)
        m_view.addAction(a_week)

        m_settings = menubar.addMenu("&Settings")
        a_settings = QAction("&Preferences…", self)
        a_settings.setShortcut(QKeySequence("Ctrl+,"))
        a_settings.triggered.connect(self._on_settings)
        m_settings.addAction(a_settings)
        a_health = QAction("Run &health check", self)
        a_health.triggered.connect(self._start_health_check)
        m_settings.addAction(a_health)

    def _build_toolbar(self) -> None:
        tb = QToolBar("Main", self)
        tb.setMovable(False)
        tb.setIconSize(_qsize(17))
        tb.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, tb)
        self._toolbar = tb

        self._acts: dict[str, QAction] = {}

        def add(key, name, text, tip, slot, primary=False):
            a = QAction(text, self)
            a.setToolTip(tip)
            a.triggered.connect(slot)
            a.setProperty("iconName", name)
            a.setProperty("primary", primary)
            tb.addAction(a)
            self._acts[key] = a
            return a

        add("add", "add", "Add", "Add a task  (Ctrl+N)", self._on_add, primary=True)
        add("edit", "edit", "Edit", "Edit selected task  (Ctrl+E)", self._on_edit)
        add("delete", "delete", "Delete", "Delete selected task  (Del)", self._on_delete)
        tb.addSeparator()
        add(
            "run",
            "run",
            "Run now",
            "Run selected task now  (Ctrl+R)",
            self._on_run_now,
            primary=True,
        )
        add("toggle", "toggle", "Enable/disable", "Toggle selected task  (Space)", self._on_toggle)

        spacer = QWidget(tb)
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        tb.addWidget(spacer)

        add("quota", "refresh", "Quota", "Re-check agent quota", self._start_quota_refresh)
        add("settings", "settings", "Preferences", "Preferences  (Ctrl+,)", self._on_settings)

    def _build_statusbar(self) -> None:
        sb = QStatusBar(self)
        self.setStatusBar(sb)
        self.status_msg = QLabel("", self)
        sb.addWidget(self.status_msg, 1)
        self.status_health = QLabel("", self)
        self.status_health.setTextFormat(Qt.TextFormat.RichText)
        sb.addPermanentWidget(self.status_health)

    # ------------------------------------------------------- theme plumbing

    def _retint(self) -> None:
        p = self._pal
        self._name_delegate.set_palette(p)
        self._status_delegate.set_palette(p)
        accent = p["text_dim"]
        for _key, a in getattr(self, "_acts", {}).items():
            name = a.property("iconName")
            col = p["accent"] if a.property("primary") else accent
            a.setIcon(icons.icon(name, col))
        self.table.viewport().update()

    def _set_theme(self, mode: str) -> None:
        app = QApplication.instance()
        if app is None:
            return
        icons.clear_cache()
        concrete = theme.apply_theme(app, mode)
        theme.set_saved_mode(mode)
        self._theme_mode = mode
        self._pal = theme.PALETTES[concrete]
        self._retint()
        self.refresh_table()
        self.refresh_health()
        self.status_msg.setText(f"Theme: {mode}")

    # -------------------------------------------------------------- Data

    def _load_tasks(self) -> list[Task]:
        try:
            return load_tasks(tasks_file())
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(
                self,
                "Couldn't read tasks.yaml",
                f"tasks.yaml has a problem and no tasks were loaded:\n\n{exc}",
            )
            return []

    def refresh_table(self) -> None:
        prev_id = self._selected_task_id()
        self.table.setRowCount(0)
        for t in self.tasks:
            self._append_row(t)
        if prev_id is not None:
            for row in range(self.table.rowCount()):
                if self.table.item(row, COL_NAME).data(Qt.ItemDataRole.UserRole) == prev_id:
                    self.table.selectRow(row)
                    break
        self.center_stack.setCurrentIndex(0 if self.tasks else 1)
        self._refresh_next_run_column()

    def refresh_health(self) -> None:
        self._render_health(
            [
                {"agent": a, "ok": None, "disabled": not c.enabled}
                for a, c in self.settings.agents.items()
            ],
            note="press Settings → health check",
        )

    def refresh_all(self) -> None:
        self.settings = load_settings(settings_file())
        self.tasks = self._load_tasks()
        self.refresh_table()
        self.refresh_health()
        self.status_msg.setText("Reloaded from disk")

    def _append_row(self, t: Task) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        p = self._pal

        item_name = QTableWidgetItem(t.name)
        item_name.setData(Qt.ItemDataRole.UserRole, t.id)
        from pathlib import Path as _P

        src = _P(t.prompt_file).name if t.prompt_file else "—"
        item_name.setData(SUBTITLE_ROLE, f"{t.id} · {src}")
        if not t.enabled:
            item_name.setFlags(item_name.flags() & ~Qt.ItemFlag.ItemIsEnabled)
        self.table.setItem(row, COL_NAME, item_name)

        agent_item = QTableWidgetItem(t.agent)
        if t.agent not in self.settings.agents:
            agent_item.setText(f"{t.agent}  (!)")
            agent_item.setForeground(QColor(p["warn"]))
        self.table.setItem(row, COL_AGENT, agent_item)

        self.table.setItem(row, COL_SCHEDULE, QTableWidgetItem(_format_schedule(t)))

        status_item = QTableWidgetItem()
        status_item.setData(Qt.ItemDataRole.UserRole, t.id)
        status_item.setData(
            STATUS_ROLE,
            ("idle", "disabled") if not t.enabled else ("idle", "idle"),
        )
        self.table.setItem(row, COL_STATUS, status_item)

        self.table.setItem(row, COL_NEXT_RUN, QTableWidgetItem("—"))
        self.table.setItem(row, COL_LAST_RUN, QTableWidgetItem(_format_last_run(t)))

        tip = f"{t.id}\n{t.prompt_file or '(no prompt file)'}"
        if t.working_dir:
            tip += f"\ncwd: {t.working_dir}"
        for c in range(len(_COLUMNS)):
            self.table.item(row, c).setToolTip(tip)

        mono = theme.mono_font(10)
        for c in _MONO_COLS:
            it = self.table.item(row, c)
            it.setFont(mono)
            it.setForeground(QColor(p["text_dim"]))

    def _refresh_next_run_column(self) -> None:
        sched = current_scheduler()
        for row, t in enumerate(self.tasks):
            item = self.table.item(row, COL_NEXT_RUN)
            if not item:
                continue
            text = _estimate_next_run(t) or "—"
            if t.enabled:
                try:
                    nxt = sched.next_run(t) if sched.is_installed(t) else None
                except Exception:  # noqa: BLE001
                    nxt = None
                if nxt is not None:
                    text = nxt.strftime("%m-%d %H:%M")
            item.setText(text)

    def _selected_task_id(self) -> Optional[str]:
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
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
            self.status_msg.setText(f"Added '{dlg.task.name}'")

    def _on_edit(self) -> None:
        task = self._selected_task()
        if task is None:
            self.status_msg.setText("Select a task first")
            return
        dlg = TaskDialog(parent=self, settings=self.settings, task=task)
        if dlg.exec() == TaskDialog.DialogCode.Accepted:
            self.tasks = self._load_tasks()
            self._install_schedule_for(dlg.task)
            self.refresh_table()
            self.status_msg.setText(f"Saved '{dlg.task.name}'")

    def _on_delete(self) -> None:
        task = self._selected_task()
        if task is None:
            self.status_msg.setText("Select a task first")
            return
        ans = QMessageBox.question(
            self,
            "Delete task",
            f"Delete '{task.name}' and remove its OS scheduler entry?",
        )
        if ans != QMessageBox.StandardButton.Yes:
            return
        try:
            current_scheduler().uninstall(task)
        except Exception:  # noqa: BLE001
            pass
        self.tasks = [t for t in self.tasks if t.id != task.id]
        save_tasks(tasks_file(), self.tasks)
        self.refresh_table()
        self.status_msg.setText(f"Deleted '{task.name}'")

    def _on_toggle(self) -> None:
        task = self._selected_task()
        if task is None:
            self.status_msg.setText("Select a task first")
            return
        task.enabled = not task.enabled
        save_tasks(tasks_file(), self.tasks)
        self._install_schedule_for(task)
        self.refresh_table()
        self.status_msg.setText(f"{'Enabled' if task.enabled else 'Disabled'} '{task.name}'")

    def _on_run_now(self) -> None:
        task = self._selected_task()
        if task is None:
            self.status_msg.setText("Select a task first")
            return
        self._start_task_run(task, mode="manual")

    def _install_schedule_for(self, task: Task) -> None:
        from ..cli import _mdrunner_executable_for_scheduler

        sched = current_scheduler()
        # quota-triggered tasks have no OS timer — they run via the quota poll.
        if task.schedule.mode == "quota":
            try:
                sched.uninstall(task)  # remove a stale time-based timer if any
            except Exception:  # noqa: BLE001
                pass
            if (
                task.enabled
                and hasattr(sched, "install_quota_poll")
                and not sched.quota_poll_installed()
            ):
                try:
                    sched.install_quota_poll(
                        self.settings.quota_poll.interval_minutes,
                        _mdrunner_executable_for_scheduler(),
                    )
                    self.status_msg.setText("Quota poll timer installed (10 min)")
                except Exception as exc:  # noqa: BLE001
                    QMessageBox.warning(
                        self,
                        "Quota poll timer",
                        f"Task saved, but the quota poll timer failed to install:\n\n{exc}\n\n"
                        f"Install it with:  mdrunner quota-schedule install",
                    )
            return
        if not task.enabled:
            try:
                sched.uninstall(task)
            except Exception:  # noqa: BLE001
                pass
            return
        try:
            sched.install(task, _mdrunner_executable_for_scheduler())
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(
                self,
                "Couldn't schedule the task",
                f"The task is saved, but its OS schedule failed to install:\n\n{exc}\n\n"
                f"Install it later with:  mdrunner schedule-install {task.id}",
            )

    def showEvent(self, event) -> None:  # noqa: N802 (Qt override)
        super().showEvent(event)
        if not self._did_autostart:
            self._did_autostart = True
            QTimer.singleShot(0, self._start_health_check)
            QTimer.singleShot(250, self._start_quota_refresh)

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt override)
        for attr in ("_health_worker", "_quota_worker", "_alert_worker"):
            w = getattr(self, attr, None)
            if w is not None:
                try:
                    if hasattr(w, "cancel"):
                        w.cancel()
                    w.quit()
                    w.wait(1500)
                except RuntimeError:
                    pass
        for w in (getattr(self, "_run_workers", None) or {}).values():
            try:
                w.quit()
                w.wait(1500)
            except RuntimeError:
                pass
        super().closeEvent(event)

    def _on_row_double_clicked(self, _index) -> None:
        self._on_edit()

    def _on_schedule_week(self) -> None:
        from .schedule_week_dialog import ScheduleWeekDialog

        dlg = ScheduleWeekDialog(parent=self, tasks=self.tasks)
        dlg.exec()
        dlg.deleteLater()

    def _on_context_menu(self, pos) -> None:
        row = self.table.rowAt(pos.y())
        if row < 0:
            return
        self.table.selectRow(row)
        menu = QMenu(self)
        menu.addAction("Edit…", self._on_edit)
        menu.addAction("Run now", self._on_run_now)
        menu.addAction("Enable / disable", self._on_toggle)
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
            self.status_msg.setText("Preferences saved")

    # ---- quota panel ----

    def _quota_agents(self) -> list[str]:
        try:
            return list(self.settings.quota_poll.agents)
        except AttributeError:
            from ..quota import QUOTA_AGENTS

            return list(QUOTA_AGENTS)

    def _load_quota_from_snapshot(self) -> None:
        from ..quota import load_snapshot

        snap = load_snapshot()
        agents = snap.get("agents") or {}
        if not agents:
            self.quota_status.setText("No quota data yet. Refresh to check.")
            return
        self._render_quota_rows(list(agents.values()))
        gen = snap.get("generated_at")
        when = _dt.datetime.fromtimestamp(gen).strftime("%H:%M") if gen else "?"
        self.quota_status.setText(f"Snapshot from {when}")

    def _start_quota_refresh(self) -> None:
        if getattr(self, "_quota_worker", None) is not None:
            return
        self.quota_refresh_btn.setEnabled(False)
        self.quota_status.setText("Checking… (up to ~1 min)")
        self._quota_worker = QuotaFetchWorker(self._quota_agents())
        self._quota_worker.results_ready.connect(self._on_quota_results)
        self._quota_worker.start()

    def _on_quota_results(self, results: list) -> None:
        # results: list of plain agent dicts from `mdrunner quota --json`
        # (the child process already wrote the snapshot).
        self._quota_worker = None
        self.quota_refresh_btn.setEnabled(True)
        if not results:
            self.quota_status.setText("Couldn't read quota — try again")
            return
        self._render_quota_rows(results)
        self.quota_status.setText("Updated " + _dt.datetime.now().strftime("%H:%M:%S"))
        self._evaluate_alerts_from_snapshot()

    # ---- quota alerts ----

    def _load_alerts(self) -> list:
        from ..alerts import load_alerts

        try:
            return load_alerts()
        except Exception:  # noqa: BLE001 — a corrupt file must not wedge the UI
            return []

    def _refresh_alert_list(self) -> None:
        from ..alerts import load_alert_state

        p = self._pal
        self.alert_list.clear()
        state = load_alert_state()
        for a in self._alerts:
            short = a.condition.describe(a.agent).split(": ", 1)[-1]
            armed = state.get(f"armed:{a.id}", True)
            if not a.enabled:
                dot, col = "○", p["idle"]
            elif armed:
                dot, col = "●", p["ok"]
            else:
                dot, col = "●", p["warn"]  # fired, waiting to re-arm
            item = QListWidgetItem(f"{dot}  {a.name}  —  {a.agent}: {short}")
            item.setData(Qt.ItemDataRole.UserRole, a.id)
            item.setForeground(QColor(p["text_dim"] if not a.enabled else col))
            tip = (
                "disabled"
                if not a.enabled
                else ("armed" if armed else "fired — re-arms when the condition turns false")
            )
            item.setToolTip(tip)
            self.alert_list.addItem(item)
        self._update_alert_hint()

    def _update_alert_hint(self) -> None:
        from . import _telegram_settings

        if self._alerts:
            self.alert_hint.setVisible(False)
            return
        self.alert_hint.setVisible(True)
        cfg = _telegram_settings.load()
        if not cfg.get("bot_token") or not cfg.get("chat_id"):
            self.alert_hint.setText(
                "No alerts yet. Add one — and set up Telegram under Settings → Notifications."
            )
        else:
            self.alert_hint.setText("No alerts yet. Add one with Add… below.")

    def _save_alerts(self) -> None:
        from ..alerts import save_alerts

        save_alerts(self._alerts)
        self._refresh_alert_list()

    def _selected_alert(self):
        row = self.alert_list.currentRow()
        if row < 0 or row >= len(self._alerts):
            return None
        return self._alerts[row]

    def _on_alert_add(self) -> None:
        from .alert_dialog import AlertDialog

        dlg = AlertDialog(parent=self)
        if dlg.exec() == AlertDialog.DialogCode.Accepted and dlg.alert is not None:
            self._alerts.append(dlg.alert)
            self._save_alerts()
            self.status_msg.setText(f"Alert added: '{dlg.alert.name}'")

    def _on_alert_edit(self) -> None:
        from .alert_dialog import AlertDialog

        cur = self._selected_alert()
        if cur is None:
            return
        dlg = AlertDialog(parent=self, alert=cur)
        if dlg.exec() == AlertDialog.DialogCode.Accepted and dlg.alert is not None:
            self._alerts = [dlg.alert if a.id == cur.id else a for a in self._alerts]
            self._save_alerts()
            self.status_msg.setText(f"Alert updated: '{dlg.alert.name}'")

    def _on_alert_delete(self) -> None:
        cur = self._selected_alert()
        if cur is None:
            return
        if (
            QMessageBox.question(
                self,
                "Delete alert",
                f"Delete quota alert '{cur.name}'?",
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        self._alerts = [a for a in self._alerts if a.id != cur.id]
        self._save_alerts()
        self.status_msg.setText(f"Alert deleted: '{cur.name}'")

    def _evaluate_alerts_from_snapshot(self) -> None:
        """Edge-evaluate alerts against the fresh snapshot; send via worker."""
        if not self._alerts:
            return
        try:
            from ..alerts import evaluate_alerts
            from ..quota import load_snapshot

            fires, _ = evaluate_alerts(self._alerts, load_snapshot())
        except Exception as exc:  # noqa: BLE001
            self.quota_status.setText(f"Alert check skipped ({exc})")
            return
        self._refresh_alert_list()  # fired rules flip to the re-arm dot now
        if not fires:
            return
        if getattr(self, "_alert_worker", None) is not None:
            return
        self._alert_worker = AlertSendWorker(fires)
        self._alert_worker.sent.connect(self._on_alerts_sent)
        self._alert_worker.start()

    def _on_alerts_sent(self, results: list) -> None:
        self._alert_worker = None
        sent = [aid for aid, ok, _d in results if ok]
        failed = [(aid, d) for aid, ok, d in results if not ok]
        if sent:
            self.status_msg.setText(f"Quota alert sent ({len(sent)})")
        if failed:
            self.status_msg.setText(
                f"Quota alert send failed: {'; '.join(f'{a}: {d}' for a, d in failed)[:160]}"
            )

    def _render_quota_rows(self, agent_dicts: list[dict]) -> None:
        from ..quota import fmt_reset

        p = self._pal
        mono = theme.mono_font(10)
        self.quota_table.setRowCount(0)

        def cell(text, *, dim=False, color=None, m=False):
            it = QTableWidgetItem(text)
            if m:
                it.setFont(mono)
            if color:
                it.setForeground(QColor(color))
            elif dim:
                it.setForeground(QColor(p["text_dim"]))
            return it

        for a in agent_dicts:
            agent = a.get("agent", "?")
            plan = a.get("plan")
            windows = a.get("windows") or []
            stale = bool(a.get("stale"))

            def agent_cell(first: bool, _agent=agent, _plan=plan, _stale=stale, _a=a):
                c = cell((_agent + (" *" if _stale else "")) if first else "")
                if first:
                    tip = f"plan: {_plan}" if _plan else ""
                    if _stale:
                        tip = (tip + "\n" if tip else "") + (
                            _a.get("note") or "cached — last probe failed"
                        )
                    if tip:
                        c.setToolTip(tip)
                    if _stale:
                        c.setForeground(QColor(p["text_dim"]))
                return c

            if not windows:
                r = self.quota_table.rowCount()
                self.quota_table.insertRow(r)
                self.quota_table.setItem(r, 0, agent_cell(True))
                if a.get("needs_sink"):
                    sink_cell = cell("needs setup", dim=True)
                    sink_cell.setToolTip(
                        f"{agent}: usage reporting needs the statusLine hook.\n"
                        f"Run:  mdrunner quota-sink-setup {agent} --write\n"
                        "then use the agent once — the panel reads the export."
                    )
                    self.quota_table.setItem(r, 1, sink_cell)
                else:
                    err_cell = cell(a.get("error") or "unavailable", dim=True)
                    if a.get("error"):
                        err_cell.setToolTip(str(a.get("error")))
                    self.quota_table.setItem(r, 1, err_cell)
                self.quota_table.setItem(r, 2, cell("—", dim=True))
                self.quota_table.setItem(r, 3, cell("—", dim=True))
                continue
            for i, w in enumerate(windows):
                r = self.quota_table.rowCount()
                self.quota_table.insertRow(r)
                self.quota_table.setItem(r, 0, agent_cell(i == 0))
                self.quota_table.setItem(r, 1, cell(w.get("label", "window"), dim=True))
                up = w.get("used_percent")
                col = None
                if up is not None and up >= 90:
                    col = p["err"]
                elif up is not None and up >= 75:
                    col = p["warn"]
                self.quota_table.setItem(
                    r, 2, cell(f"{up:.0f}%" if up is not None else "—", m=True, color=col)
                )
                secs = None
                ra = w.get("resets_at")
                if ra is not None:
                    secs = max(0.0, ra - _dt.datetime.now().timestamp())
                self.quota_table.setItem(r, 3, cell(fmt_reset(secs), m=True, dim=True))

    # ------------------------------------------------------- Background work

    def _start_health_check(self) -> None:
        if getattr(self, "_health_worker", None) is not None:
            self._health_worker.cancel()
        self._health_worker = HealthCheckWorker(self.settings)
        self._health_worker.results_ready.connect(self._on_health_results)
        self._health_worker.start()
        self._render_health(
            [{"agent": a, "ok": None} for a in self.settings.agents], note="checking…"
        )

    def _on_health_results(self, rows: list[dict]) -> None:
        self._health_worker = None
        self._render_health(rows)
        self.status_msg.setText("Health check complete")

    def _render_health(self, rows: list[dict], *, note: str | None = None) -> None:
        p = self._pal
        chips = []
        for r in rows:
            ok = r.get("ok")
            if r.get("disabled"):
                col, sym = p["idle"], "○"
            elif ok is None:
                col, sym = p["text_faint"], "·"
            elif ok:
                col, sym = p["ok"], "●"
            else:
                col, sym = p["err"], "●"
            chips.append(
                f'<span style="color:{col}">{sym}</span>'
                f'<span style="color:{p["text_dim"]}">&nbsp;{r["agent"]}</span>'
            )
        html = "&nbsp;&nbsp;".join(chips)
        if note:
            html += f'&nbsp;&nbsp;<span style="color:{p["text_faint"]}">{note}</span>'
        self.status_health.setText(html)

    def _start_task_run(self, task: Task, *, mode: str) -> None:
        self.log_view.set_task(task, mode=mode)
        running = getattr(self, "_run_workers", None) or {}
        if task.id in running:
            QMessageBox.information(
                self,
                "Task is already running",
                f"'{task.name}' is already running — wait for it to finish.",
            )
            return
        self._set_row_status(task.id, ("running", "running"))
        worker = TaskRunWorker(task, settings=self.settings, mode=mode)
        worker.line.connect(lambda line, tid=task.id: self._route_log_line(tid, line))
        worker.finished_with_result.connect(self._on_task_finished)
        worker.start()
        running[task.id] = worker
        self._run_workers = running
        self._active_log_task = task.id
        n = len(running)
        self.status_msg.setText(f"Running '{task.name}'…" if n == 1 else f"Running {n} tasks…")

    def _route_log_line(self, task_id: str, line: str) -> None:
        if task_id != getattr(self, "_active_log_task", None):
            return
        self.log_view.append_line(line)

    def _set_row_status(self, task_id: str, status: tuple[str, str]) -> None:
        for row in range(self.table.rowCount()):
            item = self.table.item(row, COL_STATUS)
            if item and item.data(Qt.ItemDataRole.UserRole) == task_id:
                item.setData(STATUS_ROLE, status)
                self.table.viewport().update()
                return

    def _on_task_finished(self, result_payload: dict) -> None:
        tid = result_payload["task_id"]
        running = getattr(self, "_run_workers", None) or {}
        running.pop(tid, None)
        self._run_workers = running
        if getattr(self, "_active_log_task", None) == tid and running:
            self._active_log_task = next(iter(running))
        ok = result_payload["ok"]
        skipped = result_payload.get("skipped")
        if skipped:
            reason = result_payload.get("skip_reason") or "gate not satisfied"
            self._set_row_status(tid, ("warn", "skipped"))
            self.status_msg.setText(f"Skipped: '{tid}' — {reason}")
            for row, t in enumerate(self.tasks):
                if t.id == tid:
                    self.table.item(row, COL_LAST_RUN).setText(
                        _dt.datetime.now().strftime("%m-%d %H:%M")
                    )
                    break
            return
        self._set_row_status(tid, ("ok", "ok") if ok else ("err", "failed"))
        self.status_msg.setText(
            f"{'Done' if ok else 'Failed'}: '{tid}' in {result_payload['duration']:.1f}s"
        )
        for row, t in enumerate(self.tasks):
            if t.id == tid:
                self.table.item(row, COL_LAST_RUN).setText(
                    _dt.datetime.now().strftime("%m-%d %H:%M")
                )
                break
        saved = result_payload.get("saved_file")
        if ok and saved:
            from pathlib import Path as _P

            self.status_msg.setText(
                f"Done: '{tid}' in {result_payload['duration']:.1f}s → {_P(saved).name}"
            )
        if result_payload.get("error"):
            QMessageBox.warning(
                self,
                "Task failed",
                str(result_payload["error"]),
            )


# ---------------------------------------------------------------------- helpers


def _qsize(n: int):
    from PySide6.QtCore import QSize

    return QSize(n, n)


_DAY_ABBR = {"mon": "M", "tue": "Tu", "wed": "W", "thu": "Th", "fri": "F", "sat": "Sa", "sun": "Su"}
_WEEKDAYS = {"mon", "tue", "wed", "thu", "fri"}


def _fmt_days(days: list[str]) -> str:
    ds = set(days)
    if ds == set(_DAY_ABBR):
        return "daily"
    if ds == _WEEKDAYS:
        return "weekdays"
    return ",".join(_DAY_ABBR.get(d, d) for d in days) if days else "?"


def _format_schedule(t: Task) -> str:
    s = t.schedule
    if s.mode == "quota":
        base = _quota_condition_tag(t) or "quota"
        mrr = t.min_rerun_interval
        return f"{base} · ≥{mrr.hours:g}h apart" if mrr.enabled else base
    if s.mode == "once":
        base = f"once · {s.time}"
    elif s.mode == "daily":
        base = f"daily · {s.time}"
    elif s.mode == "weekly":
        base = f"{_fmt_days(s.days)} · {s.time}"
    elif s.mode == "interval":
        base = f"every {s.interval_minutes}m"
    else:
        base = s.mode
    tag = _quota_condition_tag(t)
    return f"{base}  +  {tag}" if tag else base


def _quota_condition_tag(t: Task) -> str:
    """Compact gate/trigger label, e.g. ``codex: wk used≥90% & 5h resets≤3h``."""
    c = t.quota_condition
    if c is None or not c.active():
        return ""
    return c.describe(t.agent)


def _format_last_run(t: Task) -> str:
    from ..utils.paths import task_log_file

    p = task_log_file(t.id)
    if not p.exists():
        return "—"
    return _dt.datetime.fromtimestamp(p.stat().st_mtime).strftime("%m-%d %H:%M")


def _estimate_next_run(t: Task) -> Optional[str]:
    if not t.enabled:
        return "—"
    s = t.schedule
    if s.mode == "once":
        return "—"
    if s.mode == "quota":
        return "on quota"
    if s.mode == "interval":
        return f"~{s.interval_minutes}m"
    if s.mode == "daily":
        return f"daily {s.time}"
    if s.mode == "weekly":
        return f"{_fmt_days(s.days)} {s.time}"
    return None


def main() -> int:
    import sys

    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("mdrunner")
    app.setOrganizationName("mdrunner")
    theme.apply_theme(app, theme.saved_mode())
    win = MainWindow()
    win.show()
    return app.exec()
