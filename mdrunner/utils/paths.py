"""Cross-platform filesystem locations for mdrunner config and logs."""

from __future__ import annotations

import os
from pathlib import Path

from platformdirs import user_config_dir, user_log_dir

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


def task_log_file(task_id: str) -> Path:
    return log_dir() / f"{task_id}.log"


def lock_file(task_id: str) -> Path:
    return log_dir() / f"{task_id}.lock"