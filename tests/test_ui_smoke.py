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


@pytest.mark.gui
def test_task_dialog_notifications_integration(app_and_window) -> None:
    _app, win = app_and_window
    from mdrunner.ui.task_dialog import TaskDialog
    from mdrunner.config import Task, OnFailure, Schedule
    from pathlib import Path

    # We need a dummy prompt file to avoid acceptance validation warning
    from mdrunner.utils.paths import settings_file
    prompt_file = settings_file().parent / "dummy.md"
    prompt_file.write_text("# Dummy prompt", encoding="utf-8")

    task = Task(
        id="notify_test",
        name="Notify Test Task",
        agent="opencode",
        prompt_file=str(prompt_file),
        schedule=Schedule(),
        on_failure=OnFailure(notify=True),
        notify_artifact=True,
        artifact_dir="/tmp/artifacts",
        artifact_extensions=[".png", ".pdf"]
    )

    dlg = TaskDialog(parent=win, settings=win.settings, task=task)

    # Verify initial population
    assert dlg.in_notify_fail.isChecked() is True
    assert dlg.in_notify_artifact.isChecked() is True
    assert dlg.in_artifact_dir.text() == "/tmp/artifacts"
    assert dlg.in_artifact_ext.text() == ".png, .pdf"

    # Toggle off notify artifact -> should disable fields
    dlg.in_notify_artifact.setChecked(False)
    assert dlg.in_artifact_dir.isEnabled() is False
    assert dlg.in_artifact_ext.isEnabled() is False

    # Change notify fail and accept
    dlg.in_notify_fail.setChecked(False)
    # We also change notify artifact back to True, and fields to check acceptance save
    dlg.in_notify_artifact.setChecked(True)
    dlg.in_artifact_dir.setText("/tmp/new_artifacts")
    dlg.in_artifact_ext.setText(".md, .html")

    # Accept changes
    dlg._on_accept()

    # Retrieve the saved task
    from mdrunner.config import load_tasks
    from mdrunner.utils.paths import tasks_file
    saved_tasks = load_tasks(tasks_file())
    saved_task = next(t for t in saved_tasks if t.id == "notify_test")

    assert saved_task.on_failure.notify is False
    assert saved_task.notify_artifact is True
    assert saved_task.artifact_dir == "/tmp/new_artifacts"
    assert saved_task.artifact_extensions == [".md", ".html"]

    dlg.deleteLater()


@pytest.mark.gui
def test_settings_dialog_defaults_integration(app_and_window) -> None:
    _app, win = app_and_window
    from mdrunner.ui.settings_dialog import SettingsDialog
    from mdrunner.config import Settings, Defaults

    settings = Settings(
        defaults=Defaults(
            timeout_minutes=15,
            artifact_markers=["TestMarker1", "TestMarker2"],
            artifact_time_window_seconds=120
        )
    )

    dlg = SettingsDialog(parent=win, settings=settings)

    # Verify initial population
    assert dlg.in_default_timeout.value() == 15
    assert dlg.in_artifact_markers.toPlainText() == "TestMarker1\nTestMarker2"
    assert dlg.in_artifact_window.value() == 120

    # Modify values
    dlg.in_default_timeout.setValue(20)
    dlg.in_artifact_markers.setPlainText("NewMarker1\nNewMarker2")
    dlg.in_artifact_window.setValue(300)

    # Accept changes (simulate OK click)
    dlg._on_accept()

    assert settings.defaults.timeout_minutes == 20
    assert settings.defaults.artifact_markers == ["NewMarker1", "NewMarker2"]
    assert settings.defaults.artifact_time_window_seconds == 300

    dlg.deleteLater()