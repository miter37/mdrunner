"""`mdrunner quota-tick` — refresh snapshot, evaluate quota-triggered tasks."""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import pytest
import yaml

from mdrunner import cli, quota
from mdrunner.quota import QuotaResult, QuotaWindow


@pytest.fixture()
def paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    monkeypatch.setenv("RUNCHER_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("RUNCHER_LOG_DIR", str(tmp_path / "log"))
    monkeypatch.setenv("RUNCHER_STATE_DIR", str(tmp_path / "state"))
    (tmp_path / "cfg").mkdir()
    (tmp_path / "log").mkdir()
    return (tmp_path / "cfg" / "tasks.yaml", tmp_path / "cfg" / "settings.yaml")


def _write_settings(p: Path) -> None:
    p.write_text(
        yaml.safe_dump(
            {
                "agents": {
                    "codex": {
                        "enabled": True,
                        "binary": "codex",
                        "health_cmd": ["codex", "--version"],
                        "bypass": {"scheduled": [], "manual": []},
                        "presets": [],
                    }
                },
                "defaults": {"timeout_minutes": 5},
                "quota_poll": {"enabled": True, "interval_minutes": 10, "agents": ["codex"]},
            }
        ),
        encoding="utf-8",
    )


def _write_quota_task(p: Path, prompt: Path, *, percent: float = 90.0) -> None:
    p.write_text(
        yaml.safe_dump(
            {
                "tasks": [
                    {
                        "id": "qt",
                        "name": "Quota task",
                        "enabled": True,
                        "agent": "codex",
                        "prompt_file": str(prompt),
                        "schedule": {"mode": "quota"},
                        "min_rerun_interval": {"enabled": True, "hours": 6},
                        "quota_condition": {
                            "weekly_used": {"enabled": True, "value": percent},
                            "on_unknown": "skip",
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )


def _fake_summary(used_percent: float):
    def _inner(agents=None):
        return [
            QuotaResult(
                "codex",
                available=True,
                confidence="authoritative",
                plan="plus",
                observed_at=time.time(),
                windows=[QuotaWindow("weekly", used_percent, time.time() + 3600, 10080)],
            )
        ]

    return _inner


def _args(**kw) -> argparse.Namespace:
    base = {"dry_run": True, "agents": ""}
    base.update(kw)
    return argparse.Namespace(**base)


def test_quota_tick_dry_run_would_fire_when_condition_met(
    paths, monkeypatch, capsys
) -> None:
    tasks_p, settings_p = paths
    prompt = tasks_p.parent / "p.md"
    prompt.write_text("hi", encoding="utf-8")
    _write_settings(settings_p)
    _write_quota_task(tasks_p, prompt, percent=90)
    monkeypatch.setattr(quota, "quota_summary", _fake_summary(96.0))

    rc = cli.cmd_quota_tick(_args(dry_run=True))
    out = capsys.readouterr().out
    assert rc == 0
    assert "WOULD RUN  qt" in out


def test_quota_tick_skips_when_below_threshold(paths, monkeypatch, capsys) -> None:
    tasks_p, settings_p = paths
    prompt = tasks_p.parent / "p.md"
    prompt.write_text("hi", encoding="utf-8")
    _write_settings(settings_p)
    _write_quota_task(tasks_p, prompt, percent=90)
    monkeypatch.setattr(quota, "quota_summary", _fake_summary(12.0))

    rc = cli.cmd_quota_tick(_args(dry_run=True))
    out = capsys.readouterr().out
    assert rc == 0
    assert "skip  qt" in out
    assert "WOULD RUN" not in out


def test_cmd_list_shows_quota_mode_not_daily(paths, capsys) -> None:
    tasks_p, settings_p = paths
    prompt = tasks_p.parent / "p.md"
    prompt.write_text("hi", encoding="utf-8")
    _write_settings(settings_p)
    _write_quota_task(tasks_p, prompt)
    rc = cli.cmd_list(argparse.Namespace())
    out = capsys.readouterr().out
    assert rc == 0
    assert "quota" in out
    assert "daily@" not in out


def test_quota_tick_no_quota_tasks_is_noop(paths, monkeypatch, capsys) -> None:
    tasks_p, settings_p = paths
    _write_settings(settings_p)
    tasks_p.write_text(yaml.safe_dump({"tasks": []}), encoding="utf-8")
    monkeypatch.setattr(quota, "quota_summary", _fake_summary(50.0))

    rc = cli.cmd_quota_tick(_args(dry_run=True))
    err = capsys.readouterr().err
    assert rc == 0
    assert "no quota-triggered tasks" in err
