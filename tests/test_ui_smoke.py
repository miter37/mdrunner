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
    assert (
        "(set prompt file to see preview)" in dlg.preview_text.toPlainText()
        or "preview" in dlg.preview_text.toPlainText().lower()
    )
    dlg.deleteLater()


@pytest.mark.gui
def test_task_dialog_notifications_integration(app_and_window) -> None:
    _app, win = app_and_window
    from mdrunner.ui.task_dialog import TaskDialog
    from mdrunner.config import Task, OnFailure, Schedule

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
        artifact_extensions=[".png", ".pdf"],
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
            artifact_time_window_seconds=120,
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
    settings = dlg.settings

    assert settings.defaults.timeout_minutes == 20
    assert settings.defaults.artifact_markers == ["NewMarker1", "NewMarker2"]
    assert settings.defaults.artifact_time_window_seconds == 300

    dlg.deleteLater()


@pytest.mark.gui
def test_settings_dialog_agent_reordering(app_and_window) -> None:
    _app, win = app_and_window
    from mdrunner.ui.settings_dialog import SettingsDialog
    from mdrunner.config import Settings, AgentConfig
    from PySide6.QtWidgets import QAbstractItemView

    # Set up agents in a specific, non-alphabetical order
    agents = {
        "charlie": AgentConfig(binary="charlie"),
        "alpha": AgentConfig(binary="alpha"),
        "bravo": AgentConfig(binary="bravo"),
    }
    settings = Settings(agents=agents)

    dlg = SettingsDialog(parent=win, settings=settings)

    # 1. Check loading order preservation
    items = [dlg.agent_list.item(i).text() for i in range(dlg.agent_list.count())]
    assert items == ["charlie", "alpha", "bravo"]

    # 2. Check drag-and-drop properties
    assert dlg.agent_list.dragEnabled() is True
    assert dlg.agent_list.acceptDrops() is True
    assert dlg.agent_list.dragDropMode() == QAbstractItemView.DragDropMode.InternalMove

    # 3. Check Up/Down buttons and move handlers
    assert hasattr(dlg, "btn_up_agent")
    assert hasattr(dlg, "btn_down_agent")

    # Select "alpha" (index 1) and move it up -> should become ["alpha", "charlie", "bravo"]
    dlg.agent_list.setCurrentRow(1)
    dlg._on_move_agent_up()
    items = [dlg.agent_list.item(i).text() for i in range(dlg.agent_list.count())]
    assert items == ["alpha", "charlie", "bravo"]

    # Select "bravo" (index 2) and move it down -> no effect
    dlg.agent_list.setCurrentRow(2)
    dlg._on_move_agent_down()
    items = [dlg.agent_list.item(i).text() for i in range(dlg.agent_list.count())]
    assert items == ["alpha", "charlie", "bravo"]

    # Select "alpha" (index 0) and move it down -> should become ["charlie", "alpha", "bravo"]
    dlg.agent_list.setCurrentRow(0)
    dlg._on_move_agent_down()
    items = [dlg.agent_list.item(i).text() for i in range(dlg.agent_list.count())]
    assert items == ["charlie", "alpha", "bravo"]

    # 4. Check saving the new order upon accept
    # Let's move "bravo" (index 2) up -> ["charlie", "bravo", "alpha"]
    dlg.agent_list.setCurrentRow(2)
    dlg._on_move_agent_up()

    dlg._on_accept()
    settings = dlg.settings

    # The settings.agents keys should be in the new order: charlie, bravo, alpha
    new_keys = list(settings.agents.keys())
    assert new_keys == ["charlie", "bravo", "alpha"]

    dlg.deleteLater()


@pytest.mark.gui
def test_settings_dialog_deepcopy_and_persistence(app_and_window) -> None:
    _app, win = app_and_window
    from mdrunner.ui.settings_dialog import SettingsDialog
    from mdrunner.config import Settings, AgentConfig, Bypass

    agents = {
        "alpha": AgentConfig(
            binary="alpha",
            default_model="model-a",
            health_cmd=["alpha", "--version"],
            bypass=Bypass(scheduled=["--sched-a"], manual=["--man-a"]),
        ),
        "bravo": AgentConfig(
            binary="bravo",
            default_model="model-b",
            health_cmd=["bravo", "--version"],
            bypass=Bypass(scheduled=["--sched-b"], manual=["--man-b"]),
        ),
    }
    settings = Settings(agents=agents)

    dlg = SettingsDialog(parent=win, settings=settings)

    # 1. Verify deepcopy is applied
    assert dlg.settings is not settings
    assert dlg.settings.agents["alpha"] is not settings.agents["alpha"]

    # 2. Verify settings are persisted on agent switch
    # Select alpha (index 0)
    assert dlg.agent_list.currentRow() == 0

    # Modify the UI inputs
    dlg.in_binary.setText("alpha-modified")
    dlg.in_default_model.setEnabled(True)
    dlg.in_default_model.setCurrentText("model-a-modified")
    dlg.in_health_cmd.setText("alpha-modified --health")
    dlg.in_bypass_sched.setText("--sched-a-modified")
    dlg.in_bypass_manual.setText("--man-a-modified")

    # Change current row to 1 ("bravo")
    dlg.agent_list.setCurrentRow(1)

    # Verify that the modifications to "alpha" are saved in dlg.settings.agents["alpha"]
    alpha_cfg = dlg.settings.agents["alpha"]
    assert alpha_cfg.binary == "alpha-modified"
    assert alpha_cfg.default_model == "model-a-modified"
    assert alpha_cfg.health_cmd == ["alpha-modified", "--health"]
    assert alpha_cfg.bypass.scheduled == ["--sched-a-modified"]
    assert alpha_cfg.bypass.manual == ["--man-a-modified"]

    # Verify that the original settings passed in are completely untouched (thanks to deepcopy)
    orig_alpha_cfg = settings.agents["alpha"]
    assert orig_alpha_cfg.binary == "alpha"
    assert orig_alpha_cfg.default_model == "model-a"
    assert orig_alpha_cfg.health_cmd == ["alpha", "--version"]
    assert orig_alpha_cfg.bypass.scheduled == ["--sched-a"]
    assert orig_alpha_cfg.bypass.manual == ["--man-a"]

    dlg.deleteLater()
