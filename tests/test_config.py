"""Config load/save roundtrip tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from mdrunner.config import (
    ConfigError,
    Schedule,
    Settings,
    Task,
    find_task,
    load_settings,
    load_tasks,
    save_settings,
    save_tasks,
    settings_from_dict,
    settings_to_dict,
    task_from_dict,
    task_to_dict,
)


def test_task_roundtrip() -> None:
    raw = {
        "id": "demo",
        "name": "Demo task",
        "agent": "opencode",
        "model": "anthropic/claude-sonnet-4-5",
        "prompt_file": "/tmp/demo.md",
        "working_dir": "/tmp",
        "schedule": {"mode": "weekly", "days": ["mon", "wed", "fri"], "time": "08:30", "timezone": "Asia/Seoul"},
        "extra_args": ["--foo", "bar"],
        "timeout_minutes": 15,
        "on_failure": {"notify": True},
    }
    t = task_from_dict(raw)
    assert t.id == "demo"
    assert t.schedule.mode == "weekly"
    assert t.schedule.days == ["mon", "wed", "fri"]
    assert t.on_failure.notify is True
    assert t.timeout_minutes == 15

    # roundtrip
    again = task_from_dict(task_to_dict(t))
    assert again == t


def test_unquoted_yaml_time_sexagesimal_is_normalized() -> None:
    import yaml

    raw = yaml.safe_load(
        """
id: afternoon
name: Afternoon
schedule:
  mode: daily
  time: 14:14
"""
    )

    assert raw["schedule"]["time"] == 854
    task = task_from_dict(raw)
    assert task.schedule.time == "14:14"


def test_settings_roundtrip() -> None:
    raw = {
        "agents": {
            "opencode": {
                "enabled": True,
                "binary": "opencode",
                "default_model": "anthropic/claude-sonnet-4-5",
                "health_cmd": ["opencode", "--version"],
                "bypass": {"scheduled": ["--auto"], "manual": []},
                "presets": [{"name": "auto-approve", "args": ["--auto"]}],
            }
        },
        "defaults": {"timeout_minutes": 10},
    }
    s = settings_from_dict(raw)
    assert s.agents["opencode"].default_model == "anthropic/claude-sonnet-4-5"
    again = settings_from_dict(settings_to_dict(s))
    assert again == s


def test_invalid_weekday_rejected() -> None:
    raw = {
        "id": "x",
        "name": "x",
        "schedule": {"mode": "weekly", "days": ["funday"], "time": "08:00"},
    }
    with pytest.raises(ConfigError):
        task_from_dict(raw)


def test_save_load_tasks(tmp_path: Path) -> None:
    p = tmp_path / "tasks.yaml"
    t = Task(
        id="t1",
        name="T1",
        prompt_file="/tmp/x.md",
        schedule=Schedule(mode="daily", time="07:00"),
    )
    save_tasks(p, [t])
    loaded = load_tasks(p)
    assert len(loaded) == 1
    assert find_task(loaded, "t1") == t


def test_save_load_settings(tmp_path: Path) -> None:
    p = tmp_path / "settings.yaml"
    s = Settings.from_dict = None  # type: ignore[attr-defined]  # noqa: F841
    s = settings_from_dict(
        {
            "agents": {
                "opencode": {
                    "binary": "opencode",
                    "default_model": "anthropic/claude-sonnet-4-5",
                    "health_cmd": ["opencode", "--version"],
                    "bypass": {"scheduled": ["--auto"], "manual": []},
                    "presets": [],
                }
            },
            "defaults": {"timeout_minutes": 10},
        }
    )
    save_settings(p, s)
    again = load_settings(p)
    assert again == s


def test_artifact_config_roundtrip(tmp_path: Path) -> None:
    # 1. Test Task with new fields set explicitly
    task_raw = {
        "id": "artifact-task",
        "name": "Artifact Task",
        "notify_artifact": True,
        "artifact_dir": "/path/to/artifacts",
        "artifact_extensions": [".txt", ".json"],
    }
    t = task_from_dict(task_raw)
    assert t.notify_artifact is True
    assert t.artifact_dir == "/path/to/artifacts"
    assert t.artifact_extensions == [".txt", ".json"]

    # Roundtrip Task
    task_dict = task_to_dict(t)
    assert task_dict["notify_artifact"] is True
    assert task_dict["artifact_dir"] == "/path/to/artifacts"
    assert task_dict["artifact_extensions"] == [".txt", ".json"]
    assert task_from_dict(task_dict) == t

    # 2. Test Task defaults when fields are omitted
    task_default_raw = {
        "id": "default-task",
        "name": "Default Task",
    }
    t_def = task_from_dict(task_default_raw)
    assert t_def.notify_artifact is False
    assert t_def.artifact_dir is None
    assert t_def.artifact_extensions == [".md"]

    # 3. Test Settings with new defaults fields set explicitly
    settings_raw = {
        "agents": {},
        "defaults": {
            "timeout_minutes": 15,
            "artifact_markers": ["Saved to:", "Artifact:"],
            "artifact_time_window_seconds": 60,
        },
    }
    s = settings_from_dict(settings_raw)
    assert s.defaults.artifact_markers == ["Saved to:", "Artifact:"]
    assert s.defaults.artifact_time_window_seconds == 60

    # Roundtrip Settings
    settings_dict = settings_to_dict(s)
    assert settings_dict["defaults"]["artifact_markers"] == ["Saved to:", "Artifact:"]
    assert settings_dict["defaults"]["artifact_time_window_seconds"] == 60
    assert settings_from_dict(settings_dict) == s

    # 4. Test Settings defaults when fields are omitted
    settings_default_raw = {
        "agents": {},
        "defaults": {
            "timeout_minutes": 10,
        },
    }
    s_def = settings_from_dict(settings_default_raw)
    assert s_def.defaults.artifact_markers == ["Saved:", "저장 완료:"]
    assert s_def.defaults.artifact_time_window_seconds == 30
