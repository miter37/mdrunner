"""Linux scheduler — systemd user timer.

Each mdrunner task becomes a pair of files in `~/.config/systemd/user/`:

    mdrunner-<id>.service   # Type=oneshot, ExecStart=mdrunner run <id> --mode scheduled
    mdrunner-<id>.timer     # OnCalendar spec, Persistent=true

We don't bundle a single .service and dispatch on argv because:
  - the OS scheduler can show the user exactly which task is in the journal
  - failures and logs are scoped per-task
  - `systemctl --user list-timers mdrunner-*.timer` gives a clean at-a-glance view
"""

from __future__ import annotations

import datetime as _dt
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from ..config import Task


SERVICE_TEMPLATE = """\
[Unit]
Description=mdrunner task: {task_name} ({task_id})

[Service]
Type=oneshot
ExecStart={executable} run {task_id} --mode scheduled
"""

TIMER_TEMPLATE = """\
[Unit]
Description=Run {task_name} ({task_id}) on schedule

[Timer]
{oncalendar}
{timezone_line}Persistent=true
Unit=mdrunner-{task_id}.service

[Install]
WantedBy=timers.target
"""


def _user_dir() -> Path:
    return Path.home() / ".config" / "systemd" / "user"


def _service_path(task_id: str) -> Path:
    return _user_dir() / f"mdrunner-{task_id}.service"


def _timer_path(task_id: str) -> Path:
    return _user_dir() / f"mdrunner-{task_id}.timer"


def _format_oncalendar(task: Task) -> tuple[str, str]:
    """Return (OnCalendar_value, Timezone_line) for a task."""
    s = task.schedule
    if s.mode == "once":
        # systemd doesn't have a "once" mode; schedule for the next occurrence
        # of the time today, but that's racy. Best-effort: schedule a one-shot
        # using OnCalendar=YYYY-MM-DD HH:MM.
        today = _dt.datetime.now().strftime("%Y-%m-%d")
        return (f"OnCalendar={today} {s.time}", "")
    if s.mode == "daily":
        return (f"OnCalendar=*-*-* {s.time}", "")
    if s.mode == "interval":
        minutes = max(1, int(s.interval_minutes))
        return (f"OnCalendar=*:0/{minutes}:0", "")
    # weekly
    days = ",".join(s.days) if s.days else "mon"
    return (f"OnCalendar={days} *-*-* {s.time}", "")


class LinuxScheduler:
    def install(self, task: Task, mdrunner_executable: Path) -> None:
        if not shutil.which("systemctl"):
            raise RuntimeError("systemctl not found on PATH")
        service_text = SERVICE_TEMPLATE.format(
            task_name=task.name,
            task_id=task.id,
            executable=mdrunner_executable,
        )
        oncalendar, _tz_line = _format_oncalendar(task)
        timer_text = TIMER_TEMPLATE.format(
            task_name=task.name,
            task_id=task.id,
            oncalendar=oncalendar,
            timezone_line="",  # systemd >= 250 supports Timezone= but it's optional
        )
        _user_dir().mkdir(parents=True, exist_ok=True)
        _service_path(task.id).write_text(service_text, encoding="utf-8")
        _timer_path(task.id).write_text(timer_text, encoding="utf-8")
        # Reload + enable
        subprocess.run(
            ["systemctl", "--user", "daemon-reload"], check=True, capture_output=True
        )
        subprocess.run(
            ["systemctl", "--user", "enable", "--now", f"mdrunner-{task.id}.timer"],
            check=True,
            capture_output=True,
        )

    def uninstall(self, task: Task) -> None:
        # Best-effort: ignore errors so GUI flow stays smooth
        subprocess.run(
            ["systemctl", "--user", "disable", "--now", f"mdrunner-{task.id}.timer"],
            check=False, capture_output=True,
        )
        subprocess.run(
            ["systemctl", "--user", "daemon-reload"], check=False, capture_output=True,
        )
        for p in (_service_path(task.id), _timer_path(task.id)):
            try:
                p.unlink()
            except FileNotFoundError:
                pass

    def is_installed(self, task: Task) -> bool:
        return _service_path(task.id).exists() and _timer_path(task.id).exists()

    def next_run(self, task: Task) -> Optional[_dt.datetime]:
        try:
            r = subprocess.run(
                ["systemctl", "--user", "show", f"mdrunner-{task.id}.timer",
                 "--property=NextElapseUSecRealtime", "--value"],
                capture_output=True, text=True, check=True,
            )
        except (subprocess.CalledProcessError, FileNotFoundError):
            return None
        text = r.stdout.strip()
        if not text or text.lower() == "n/a":
            return None
        # Format: "Day YYYY-MM-DD HH:MM:SS TZ" or just "YYYY-MM-DD HH:MM:SS TZ"
        try:
            parts = text.split(maxsplit=1)
            ts = parts[1] if len(parts) == 2 else parts[0]
            return _dt.datetime.fromisoformat(ts.replace(" ", "T").split("+")[0].split("-")[-1] if "+" in ts else ts.replace(" ", "T").split("-")[0])
        except (ValueError, IndexError):
            return None

    def last_status(self, task: Task) -> tuple[Optional[int], Optional[_dt.datetime]]:
        # Inspect journal for the most recent run of the service
        try:
            r = subprocess.run(
                ["journalctl", "--user", "-u", f"mdrunner-{task.id}.service", "-n", "1",
                 "--output=json", "--no-pager"],
                capture_output=True, text=True, check=True,
            )
        except (subprocess.CalledProcessError, FileNotFoundError):
            return None, None
        import json

        for line in r.stdout.splitlines():
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = rec.get("__REALTIME_TIMESTAMP")
            if not ts:
                continue
            try:
                last_dt = _dt.datetime.fromtimestamp(int(ts) / 1_000_000)
            except (ValueError, TypeError):
                continue
            exit_code_str = rec.get("EXIT_CODE") or rec.get("EXIT_STATUS")
            try:
                return int(exit_code_str) if exit_code_str is not None else None, last_dt
            except ValueError:
                return None, last_dt
        return None, None
