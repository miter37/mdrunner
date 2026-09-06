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
    monkeypatch.setenv("RUNCHER_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("RUNCHER_STATE_DIR", str(tmp_path / "state"))
    (tmp_path / "cfg").mkdir()
    (tmp_path / "log").mkdir()
    # never shell out during GUI tests
    import mdrunner.utils.models as _models

    monkeypatch.setattr(_models, "fetch_agent_models", lambda *a, **k: [])
    from PySide6.QtWidgets import QApplication
    from mdrunner.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    win = MainWindow()
    try:
        yield app, win
    finally:
        win.close()
        app.processEvents()
        win.deleteLater()
        app.processEvents()


@pytest.mark.gui
def test_main_window_constructs_empty(app_and_window) -> None:
    _app, win = app_and_window
    assert win.windowTitle().startswith("mdrunner")
    assert win.table.columnCount() == 6


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
def test_quota_panel_renders_snapshot(app_and_window) -> None:
    _app, win = app_and_window
    assert win.quota_table.columnCount() == 4

    rows = [
        {
            "agent": "codex",
            "plan": "plus",
            "windows": [
                {"label": "5h", "used_percent": 12, "resets_at": None, "window_minutes": 300},
                {"label": "weekly", "used_percent": 95, "resets_at": None, "window_minutes": 10080},
            ],
        },
        {"agent": "claude", "plan": None, "windows": [], "error": "no structured source"},
    ]
    win._render_quota_rows(rows)
    # 2 codex windows + 1 claude "unavailable" row
    assert win.quota_table.rowCount() == 3
    assert win.quota_table.item(0, 0).text() == "codex"
    assert win.quota_table.item(0, 1).text() == "5h"
    assert win.quota_table.item(1, 2).text() == "95%"
    assert win.quota_table.item(2, 0).text() == "claude"
    assert "no structured source" in win.quota_table.item(2, 1).text()


@pytest.mark.gui
def test_quota_panel_results_handler(app_and_window) -> None:
    _app, win = app_and_window

    win._quota_worker = object()  # simulate an in-flight probe
    # the worker now emits plain dicts (from `mdrunner quota --json`)
    win._on_quota_results(
        [
            {
                "agent": "codex",
                "available": True,
                "confidence": "authoritative",
                "plan": "plus",
                "windows": [
                    {"label": "weekly", "used_percent": 40, "resets_at": None,
                     "window_minutes": 10080}
                ],
            }
        ]
    )
    assert win._quota_worker is None
    assert win.quota_refresh_btn.isEnabled()
    assert win.quota_table.rowCount() == 1


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
def test_task_dialog_inline_prompt_saves_md_and_registers(app_and_window) -> None:
    _app, win = app_and_window
    from mdrunner.ui.task_dialog import TaskDialog
    from mdrunner.config import load_tasks
    from mdrunner.utils.paths import prompts_dir, tasks_file

    _seed_opencode_settings()
    win.refresh_all()

    dlg = TaskDialog(parent=win, settings=win.settings, task=None)
    dlg.in_name.setText("Inline Demo")
    idx = dlg.in_prompt_source.findData("inline")
    dlg.in_prompt_source.setCurrentIndex(idx)
    assert not dlg.in_prompt_editor.isHidden()
    assert dlg.row_prompt_file.isHidden()
    dlg.in_prompt_editor.setPlainText("Read 5 headlines and print them.")
    dlg._on_accept()

    md_files = list(prompts_dir().glob("*.md"))
    assert len(md_files) == 1
    assert md_files[0].name.startswith("inline-demo-")
    assert "5 headlines" in md_files[0].read_text(encoding="utf-8")

    saved = next(t for t in load_tasks(tasks_file()) if t.name == "Inline Demo")
    assert saved.prompt_file == str(md_files[0])

    # Re-open for edit → inline mode is auto-selected and text is loaded back
    dlg2 = TaskDialog(parent=win, settings=win.settings, task=saved)
    assert dlg2._prompt_source() == "inline"
    assert "5 headlines" in dlg2.in_prompt_editor.toPlainText()
    dlg2.in_prompt_editor.setPlainText("Updated instruction.")
    dlg2._on_accept()

    md_files_after = list(prompts_dir().glob("*.md"))
    assert len(md_files_after) == 1  # edited in place, no new file
    assert "Updated instruction." in md_files_after[0].read_text(encoding="utf-8")
    dlg.deleteLater()
    dlg2.deleteLater()


def _seed_opencode_settings() -> None:
    import yaml

    from mdrunner.utils.paths import settings_file

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


@pytest.mark.gui
def test_settings_dialog_notifications_loading(app_and_window) -> None:
    _app, win = app_and_window
    from mdrunner.ui.settings_dialog import SettingsDialog
    from mdrunner.config import Settings
    from mdrunner.ui import _telegram_settings

    # Save mock telegram settings first
    mock_data = {
        "bot_token": "12345:mock_token",
        "chat_id": "-987654321",
        "notify_on_failure": False,
    }
    _telegram_settings.save(mock_data)

    settings = Settings()
    dlg = SettingsDialog(parent=win, settings=settings)

    # Check that UI loaded values correctly
    assert dlg.in_tg_token.text() == "12345:mock_token"
    assert dlg.in_tg_chat.text() == "-987654321"
    assert dlg.cb_tg_failure.isChecked() is False

    # Modify values
    dlg.in_tg_token.setText("new_token")
    dlg.in_tg_chat.setText("new_chat_id")
    dlg.cb_tg_failure.setChecked(True)

    # Accept changes
    dlg._on_accept()

    # Verify settings are saved back
    saved_cfg = _telegram_settings.load()
    assert saved_cfg["bot_token"] == "new_token"
    assert saved_cfg["chat_id"] == "new_chat_id"
    assert saved_cfg["notify_on_failure"] is True

    dlg.deleteLater()


@pytest.mark.gui
def test_settings_dialog_agent_switch_persistence(app_and_window) -> None:
    _app, win = app_and_window
    from mdrunner.ui.settings_dialog import SettingsDialog
    from mdrunner.config import Settings, AgentConfig

    agents = {
        "alpha": AgentConfig(binary="alpha", default_model="model-a"),
        "bravo": AgentConfig(binary="bravo", default_model="model-b"),
    }
    settings = Settings(agents=agents)
    dlg = SettingsDialog(parent=win, settings=settings)

    # 1. Modify alpha settings in the UI
    assert dlg.agent_list.currentRow() == 0
    dlg.in_binary.setText("alpha-modified")
    dlg.in_health_cmd.setText("alpha-modified-health")

    # 2. Switch to bravo
    dlg.agent_list.setCurrentRow(1)
    # Check that UI loaded bravo settings
    assert dlg.in_binary.text() == "bravo"

    # 3. Switch back to alpha
    dlg.agent_list.setCurrentRow(0)
    # Check that UI restored the modified alpha settings
    assert dlg.in_binary.text() == "alpha-modified"
    assert dlg.in_health_cmd.text() == "alpha-modified-health"

    dlg.deleteLater()


@pytest.mark.gui
def test_settings_dialog_cancel_integrity(app_and_window) -> None:
    _app, win = app_and_window
    from mdrunner.ui.settings_dialog import SettingsDialog
    from mdrunner.config import Settings, AgentConfig

    agents = {
        "alpha": AgentConfig(binary="alpha", default_model="model-a"),
    }
    settings = Settings(agents=agents)
    dlg = SettingsDialog(parent=win, settings=settings)

    # 1. Modify the UI inputs
    dlg.in_binary.setText("alpha-modified")
    dlg.in_default_timeout.setValue(99)

    # Simulate rejection / Cancel click
    dlg.reject()

    assert settings.agents["alpha"].binary == "alpha"
    assert settings.defaults.timeout_minutes == 10  # default is 10

    # Verify that the dialog's settings object, even if modified, did not leak back to the original
    assert settings is not dlg.settings

    dlg.deleteLater()


@pytest.mark.gui
def test_settings_dialog_detect_binary_fallback(app_and_window, monkeypatch) -> None:
    _app, win = app_and_window
    from mdrunner.ui.settings_dialog import SettingsDialog
    from mdrunner.config import Settings, AgentConfig
    import shutil

    agents = {
        "my-special-agent": AgentConfig(binary=""),
    }
    settings = Settings(agents=agents)
    dlg = SettingsDialog(parent=win, settings=settings)

    # Mock shutil.which to return a dummy path for "my-special-agent"
    monkeypatch.setattr(shutil, "which", lambda cmd: "/usr/bin/my-special-agent" if cmd == "my-special-agent" else None)

    # in_binary is empty initially (as binary is "")
    assert dlg.in_binary.text() == ""

    # Mock QMessageBox.information to do nothing
    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setattr(QMessageBox, "information", lambda *args, **kwargs: None)

    # Trigger detect binary
    dlg._on_detect_binary()

    # The fallback should use "my-special-agent" as query and find /usr/bin/my-special-agent
    assert dlg.in_binary.text() == "/usr/bin/my-special-agent"

    dlg.deleteLater()



def _seed_quota_settings() -> None:
    """opencode (no quota) + codex (quota-capable) so the quota-condition UI
    can be exercised both ways."""
    import yaml

    from mdrunner.utils.paths import settings_file

    settings_file().parent.mkdir(parents=True, exist_ok=True)
    settings_file().write_text(
        yaml.safe_dump(
            {
                "agents": {
                    "codex": {
                        "enabled": True,
                        "binary": "codex",
                        "default_model": "",
                        "health_cmd": ["codex", "--version"],
                        "bypass": {"scheduled": [], "manual": []},
                        "presets": [],
                    },
                    "opencode": {
                        "enabled": True,
                        "binary": "opencode",
                        "default_model": "minimax-coding-plan/MiniMax-M3",
                        "health_cmd": ["opencode", "--version"],
                        "bypass": {"scheduled": ["--auto"], "manual": []},
                        "presets": [],
                    },
                },
                "defaults": {"timeout_minutes": 5},
            }
        ),
        encoding="utf-8",
    )


@pytest.mark.gui
def test_task_dialog_quota_mode_locks_run_limits(app_and_window) -> None:
    _app, win = app_and_window
    from mdrunner.ui.task_dialog import TaskDialog

    _seed_quota_settings()
    win.refresh_all()

    dlg = TaskDialog(parent=win, settings=win.settings, task=None)
    dlg.in_agent.setCurrentText("codex")
    dlg.in_mode.setCurrentIndex(dlg.in_mode.findData("quota"))

    # both gates forced on + locked
    assert dlg.in_qc_enabled.isChecked() and not dlg.in_qc_enabled.isEnabled()
    assert dlg.in_mrr_enabled.isChecked() and not dlg.in_mrr_enabled.isEnabled()
    # time / days disabled in quota mode
    assert not dlg.in_time.isEnabled()
    assert all(not cb.isEnabled() for cb in dlg.day_checks.values())
    # codex reports both windows → all 4 grid cells are available
    for attr, (cb, _sb) in dlg.qc_cells.items():
        assert cb.isEnabled(), attr

    dlg.deleteLater()


@pytest.mark.gui
def test_task_dialog_quota_condition_saves_and_reloads(app_and_window) -> None:
    _app, win = app_and_window
    from mdrunner.config import load_tasks
    from mdrunner.ui.task_dialog import TaskDialog
    from mdrunner.utils.paths import tasks_file

    _seed_quota_settings()
    win.refresh_all()

    dlg = TaskDialog(parent=win, settings=win.settings, task=None)
    dlg.in_name.setText("Quota Trigger Demo")
    dlg.in_agent.setCurrentText("codex")
    dlg.in_mode.setCurrentIndex(dlg.in_mode.findData("quota"))
    wk_used_cb, wk_used_sb = dlg.qc_cells["weekly_used"]
    wk_reset_cb, wk_reset_sb = dlg.qc_cells["weekly_reset"]
    wk_used_cb.setChecked(True)
    wk_used_sb.setValue(85)
    wk_reset_cb.setChecked(True)
    wk_reset_sb.setValue(12)
    dlg.in_prompt_source.setCurrentIndex(dlg.in_prompt_source.findData("inline"))
    dlg.in_prompt_editor.setPlainText("Do the thing when weekly quota is high.")
    dlg._on_accept()

    saved = next(t for t in load_tasks(tasks_file()) if t.name == "Quota Trigger Demo")
    assert saved.schedule.mode == "quota"
    c = saved.quota_condition
    assert c is not None
    assert c.weekly_used.enabled and c.weekly_used.value == 85.0
    assert c.weekly_reset.enabled and c.weekly_reset.value == 12.0
    assert not c.fivehour_used.enabled
    assert saved.min_rerun_interval.enabled is True

    dlg2 = TaskDialog(parent=win, settings=win.settings, task=saved)
    assert dlg2.in_qc_enabled.isChecked()
    cb2, sb2 = dlg2.qc_cells["weekly_used"]
    assert cb2.isChecked() and sb2.value() == 85
    dlg.deleteLater()
    dlg2.deleteLater()


@pytest.mark.gui
def test_task_dialog_quota_condition_needs_a_checked_clause(app_and_window, monkeypatch) -> None:
    _app, win = app_and_window
    from PySide6.QtWidgets import QMessageBox

    from mdrunner.config import load_tasks
    from mdrunner.ui.task_dialog import TaskDialog
    from mdrunner.utils.paths import tasks_file

    _seed_quota_settings()
    win.refresh_all()
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: None)

    dlg = TaskDialog(parent=win, settings=win.settings, task=None)
    dlg.in_name.setText("No Clause Task")
    dlg.in_agent.setCurrentText("codex")
    dlg.in_qc_enabled.setChecked(True)  # enabled, but no cell checked
    dlg.in_prompt_source.setCurrentIndex(dlg.in_prompt_source.findData("inline"))
    dlg.in_prompt_editor.setPlainText("body")
    dlg._on_accept()
    assert not any(t.name == "No Clause Task" for t in load_tasks(tasks_file()))
    dlg.deleteLater()


@pytest.mark.gui
def test_task_dialog_five_hour_row_disabled_for_grok(app_and_window) -> None:
    _app, win = app_and_window
    import yaml

    from mdrunner.ui.task_dialog import TaskDialog
    from mdrunner.utils.paths import settings_file

    settings_file().write_text(
        yaml.safe_dump(
            {
                "agents": {
                    "grok": {"enabled": True, "binary": "grok", "default_model": "",
                             "health_cmd": ["grok", "--version"],
                             "bypass": {"scheduled": [], "manual": []}, "presets": []},
                },
                "defaults": {"timeout_minutes": 5},
            }
        ),
        encoding="utf-8",
    )
    win.refresh_all()

    dlg = TaskDialog(parent=win, settings=win.settings, task=None)
    dlg.in_agent.setCurrentText("grok")
    dlg.in_qc_enabled.setChecked(True)
    assert dlg.qc_cells["weekly_used"][0].isEnabled()
    assert not dlg.qc_cells["fivehour_used"][0].isEnabled()
    assert not dlg.qc_cells["fivehour_reset"][0].isEnabled()
    dlg.deleteLater()


@pytest.mark.gui
def test_task_dialog_quota_condition_hidden_for_non_capable_agent(app_and_window) -> None:
    _app, win = app_and_window
    from mdrunner.ui.task_dialog import TaskDialog

    _seed_quota_settings()
    win.refresh_all()

    dlg = TaskDialog(parent=win, settings=win.settings, task=None)
    dlg.in_agent.setCurrentText("opencode")
    # user tries to enable it -> refresh unchecks + disables (opencode has no quota)
    dlg.in_qc_enabled.setChecked(True)
    assert not dlg.in_qc_enabled.isChecked()
    assert not dlg.in_qc_enabled.isEnabled()

    dlg.deleteLater()


@pytest.mark.gui
def test_main_table_shows_quota_condition_tag(app_and_window) -> None:
    _app, win = app_and_window
    import yaml

    from mdrunner.utils.paths import tasks_file

    _seed_quota_settings()
    tasks_file().write_text(
        yaml.safe_dump(
            {
                "tasks": [
                    {
                        "id": "gate",
                        "name": "Gated daily",
                        "agent": "codex",
                        "prompt_file": "/tmp/x.md",
                        "schedule": {"mode": "daily", "time": "02:00"},
                        "quota_condition": {"weekly_used": {"enabled": True, "value": 90}},
                    },
                    {
                        "id": "trig",
                        "name": "Pure trigger",
                        "agent": "codex",
                        "prompt_file": "/tmp/y.md",
                        "schedule": {"mode": "quota"},
                        "min_rerun_interval": {"enabled": True, "hours": 6},
                        "quota_condition": {"fivehour_reset": {"enabled": True, "value": 3}},
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    win.refresh_all()

    sched_col = 2
    texts = {win.table.item(r, 0).text(): win.table.item(r, sched_col).text()
             for r in range(win.table.rowCount())}
    assert "codex: wk used≥90%" in texts["Gated daily"]
    assert "daily" in texts["Gated daily"]
    assert texts["Pure trigger"].startswith("codex: 5h resets≤3h")
