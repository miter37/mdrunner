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