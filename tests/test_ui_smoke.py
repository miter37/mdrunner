"""Headless smoke test for the GUI — instantiate MainWindow off-screen and
walk a few key code paths so the GUI module doesn't bitrot in CI.
"""

from __future__ import annotations

import os

import pytest

# Off-screen Qt platform for headless CI
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6", reason="PySide6 not installed; install with `uv sync --extra gui`")


@pytest.fixture()
def app_and_window(tmp_path, monkeypatch):
    monkeypatch.setenv("RUNCHER_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("RUNCHER_LOG_DIR", str(tmp_path / "log"))
    (tmp_path / "cfg").mkdir()
    (tmp_path / "log").mkdir()
    from PySide6.QtWidgets import QApplication
    from mdrunner.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    win = MainWindow()
    yield app, win


@pytest.mark.gui
def test_main_window_constructs_empty(app_and_window) -> None:
    _app, win = app_and_window
    assert win.windowTitle().startswith("mdrunner")
    assert win.table.columnCount() == 7


@pytest.mark.gui
def test_main_window_with_task(app_and_window) -> None:
    _app, win = app_and_window
    # Add a task via direct file write
    import yaml

    from mdrunner.utils.paths import settings_file, tasks_file

    settings_file().parent.mkdir(parents=True, exist_ok=True)
    settings_file().write_text(
        yaml.safe_dump(
            {
                "agents": {
                    "opencode": {
                        "enabled": True,
                        "binary": "opencode",
                        "default_model": "minimax-coding-plan/MiniMax-M3",
                        "health_cmd": ["opencode", "--version"],
                        "bypass": {"scheduled": ["--auto"], "manual": []},
                        "presets": [],
                    }
                },
                "defaults": {"timeout_minutes": 5},
            }
        ),
        encoding="utf-8",
    )
    tasks_file().write_text(
        yaml.safe_dump(
            {
                "tasks": [
                    {
                        "id": "demo",
                        "name": "Demo Task",
                        "agent": "opencode",
                        "prompt_file": "/tmp/does_not_exist.md",
                        "schedule": {"mode": "daily", "time": "07:00", "timezone": "UTC"},
                        "timeout_minutes": 5,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    win.refresh_all()
    assert win.table.rowCount() == 1
    assert win.table.item(0, 0).text() == "Demo Task"


@pytest.mark.gui
def test_task_dialog_preview(app_and_window) -> None:
    _app, win = app_and_window
    from mdrunner.ui.task_dialog import TaskDialog

    dlg = TaskDialog(parent=win, settings=win.settings, task=None)
    # No prompt file yet → preview is empty
    assert "(set prompt file to see preview)" in dlg.preview_text.toPlainText() or "preview" in dlg.preview_text.toPlainText().lower()
    dlg.deleteLater()