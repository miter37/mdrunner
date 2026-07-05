"""Scheduler adapter protocol + cross-platform factory."""

from __future__ import annotations

import datetime as _dt
import sys
from pathlib import Path
from typing import Protocol, runtime_checkable

from ..config import Task
from .linux import LinuxScheduler
from .windows import WindowsScheduler


@runtime_checkable
class SchedulerAdapter(Protocol):
    def install(self, task: Task, mdrunner_executable: Path) -> None: ...
    def uninstall(self, task: Task) -> None: ...
    def is_installed(self, task: Task) -> bool: ...
    def next_run(self, task: Task) -> _dt.datetime | None: ...
    def last_status(self, task: Task) -> tuple[int, _dt.datetime | None]:
        """Returns (exit_code, last_run_time). exit_code is None if no record."""


def current() -> SchedulerAdapter:
    """Return the scheduler adapter for the current OS."""
    if sys.platform.startswith("linux"):
        return LinuxScheduler()
    if sys.platform == "win32":
        return WindowsScheduler()
    raise RuntimeError(f"unsupported platform: {sys.platform}")
