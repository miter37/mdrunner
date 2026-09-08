"""Cross-platform filesystem locations for mdrunner config and logs."""

from __future__ import annotations

import os
from pathlib import Path

from platformdirs import user_config_dir, user_data_dir, user_log_dir, user_state_dir

APP_NAME = "mdrunner"


def config_dir() -> Path:
    """Return the mdrunner config directory; create it if missing."""
    override = os.environ.get("RUNCHER_CONFIG_DIR")
    if override:
        p = Path(override).expanduser().resolve()
    else:
        p = Path(user_config_dir(APP_NAME, appauthor=False))
    p.mkdir(parents=True, exist_ok=True)
    return p


def data_dir() -> Path:
    """Return the mdrunner user-data directory; create it if missing.

    This is where the app keeps content it manages on the user's behalf
    (as opposed to `config_dir()`, which holds settings). On Linux that
    is ``~/.local/share/mdrunner`` (XDG), on macOS
    ``~/Library/Application Support/mdrunner``, on Windows
    ``%LOCALAPPDATA%\\mdrunner``. Honors RUNCHER_DATA_DIR for tests.
    """
    override = os.environ.get("RUNCHER_DATA_DIR")
    if override:
        p = Path(override).expanduser().resolve()
    else:
        p = Path(user_data_dir(APP_NAME, appauthor=False))
    p.mkdir(parents=True, exist_ok=True)
    return p


def prompts_dir() -> Path:
    """Return the folder where inline-authored prompt md files are stored."""
    p = data_dir() / "prompts"
    p.mkdir(parents=True, exist_ok=True)
    return p


def tasks_file() -> Path:
    return config_dir() / "tasks.yaml"


def settings_file() -> Path:
    return config_dir() / "settings.yaml"


def log_dir() -> Path:
    """Return the mdrunner log directory; honors RUNCHER_LOG_DIR for tests."""
    override = os.environ.get("RUNCHER_LOG_DIR")
    if override:
        p = Path(override).expanduser().resolve()
    else:
        p = Path(user_log_dir(APP_NAME, appauthor=False))
    p.mkdir(parents=True, exist_ok=True)
    return p


def state_dir() -> Path:
    """Return the mdrunner state directory (small machine-local snapshots).

    Linux: ``~/.local/state/mdrunner``. Honors RUNCHER_STATE_DIR for tests.
    """
    override = os.environ.get("RUNCHER_STATE_DIR")
    if override:
        p = Path(override).expanduser().resolve()
    else:
        p = Path(user_state_dir(APP_NAME, appauthor=False))
    p.mkdir(parents=True, exist_ok=True)
    return p


def task_state_file() -> Path:
    """Per-task runtime state (last real run time), for the re-run interval."""
    return state_dir() / "task-state.json"


def quota_trigger_file() -> Path:
    """Per-task quota-trigger bookkeeping for `mdrunner quota-tick`."""
    return state_dir() / "quota-triggers.json"


def quota_snapshot_file() -> Path:
    """Latest agent-quota snapshot written by `mdrunner quota --write`."""
    return state_dir() / "quota.json"


def task_log_file(task_id: str) -> Path:
    return log_dir() / f"{task_id}.log"


def lock_file(task_id: str) -> Path:
    return log_dir() / f"{task_id}.lock"


def alerts_file() -> Path:
    """User-authored quota-alert rules (Telegram on engine quota states)."""
    return config_dir() / "alerts.json"


def alert_state_file() -> Path:
    """Edge-trigger bookkeeping for quota alerts (armed/disarmed per alert)."""
    return state_dir() / "alert-state.json"