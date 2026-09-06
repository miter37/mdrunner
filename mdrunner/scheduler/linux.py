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


def _parse_systemd_timestamp(text: str) -> Optional[_dt.datetime]:
    """Parse a systemd human timestamp like ``Mon 2026-09-07 07:12:00 KST``.

    systemd prints an optional leading weekday, a ``YYYY-MM-DD`` date, a
    ``HH:MM:SS`` time, and a trailing timezone abbreviation that
    ``datetime.fromisoformat`` cannot handle. We pick out the date and time
    tokens and return a naive local datetime (matching ``last_status``).
    """
    text = (text or "").strip()
    if not text or text.lower() in {"n/a", "0"}:
        return None
    date_tok = time_tok = None
    for tok in text.split():
        if len(tok) == 10 and tok[4] == "-" and tok[7] == "-":
            date_tok = tok
        elif len(tok) == 8 and tok[2] == ":" and tok[5] == ":":
            time_tok = tok
    if not date_tok or not time_tok:
        return None
    try:
        return _dt.datetime.strptime(f"{date_tok} {time_tok}", "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def _user_dir() -> Path:
    return Path.home() / ".config" / "systemd" / "user"


def _service_path(task_id: str) -> Path:
    return _user_dir() / f"mdrunner-{task_id}.service"


def _timer_path(task_id: str) -> Path:
    return _user_dir() / f"mdrunner-{task_id}.timer"


def _tz_line(task: Task) -> str:
    """``Timezone=<IANA>`` line for the [Timer] section (systemd >= 250).

    Without this the OnCalendar time is read in the host's local zone, so a
    task whose ``schedule.timezone`` differs fires at the wrong wall-clock
    time. Only emitted for a real IANA name (contains a '/').
    """
    tz = (task.schedule.timezone or "").strip()
    return f"Timezone={tz}\n" if "/" in tz else ""


def _format_oncalendar(task: Task) -> tuple[str, str]:
    """Return (timer trigger line(s), Timezone_line) for a task.

    Interval mode uses a *relative* repeat (``OnUnitActiveSec``) rather than
    ``OnCalendar=*:0/N:0`` — the latter is rejected by systemd for N >= 60
    because the minute field only spans 0-59, which silently broke every
    interval task of an hour or more.
    """
    s = task.schedule
    if s.mode == "interval":
        minutes = max(1, int(s.interval_minutes))
        return (
            f"OnBootSec={min(minutes, 5)}min\nOnUnitActiveSec={minutes}min",
            "",  # relative timer — timezone is irrelevant
        )
    if s.mode == "once":
        # systemd has no "once"; pin an absolute date-time. Racy if installed
        # after the time has already passed today (it then never fires).
        today = _dt.datetime.now().strftime("%Y-%m-%d")
        return (f"OnCalendar={today} {s.time}", _tz_line(task))
    if s.mode == "daily":
        return (f"OnCalendar=*-*-* {s.time}", _tz_line(task))
    # weekly — systemd normalises lowercase day names itself
    days = ",".join(s.days) if s.days else "mon"
    return (f"OnCalendar={days} *-*-* {s.time}", _tz_line(task))


class LinuxScheduler:
    def install(self, task: Task, mdrunner_executable: Path) -> None:
        if not shutil.which("systemctl"):
            raise RuntimeError("systemctl not found on PATH")
        service_text = SERVICE_TEMPLATE.format(
            task_name=task.name,
            task_id=task.id,
            executable=mdrunner_executable,
        )
        oncalendar, tz_line = _format_oncalendar(task)
        timer_text = TIMER_TEMPLATE.format(
            task_name=task.name,
            task_id=task.id,
            oncalendar=oncalendar,
            timezone_line=tz_line,
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
        # Format: "Day YYYY-MM-DD HH:MM:SS TZ" or just "YYYY-MM-DD HH:MM:SS TZ"
        return _parse_systemd_timestamp(r.stdout)

    # --- quota poller (a single non-task timer) ---------------------------

    QUOTA_UNIT = "mdrunner-quota-poll"

    def install_quota_poll(self, interval_minutes: int, mdrunner_executable: Path) -> None:
        if not shutil.which("systemctl"):
            raise RuntimeError("systemctl not found on PATH")
        interval = max(1, int(interval_minutes))
        service_text = (
            "[Unit]\nDescription=mdrunner agent-quota poll\n\n"
            "[Service]\nType=oneshot\n"
            f"ExecStart={mdrunner_executable} quota --write\n"
        )
        timer_text = (
            "[Unit]\nDescription=Poll agent quota every "
            f"{interval} min\n\n"
            "[Timer]\n"
            f"OnBootSec={min(interval, 5)}min\n"
            f"OnUnitActiveSec={interval}min\n"
            "Persistent=true\n"
            f"Unit={self.QUOTA_UNIT}.service\n\n"
            "[Install]\nWantedBy=timers.target\n"
        )
        _user_dir().mkdir(parents=True, exist_ok=True)
        (_user_dir() / f"{self.QUOTA_UNIT}.service").write_text(service_text, encoding="utf-8")
        (_user_dir() / f"{self.QUOTA_UNIT}.timer").write_text(timer_text, encoding="utf-8")
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=True, capture_output=True)
        subprocess.run(
            ["systemctl", "--user", "enable", "--now", f"{self.QUOTA_UNIT}.timer"],
            check=True,
            capture_output=True,
        )

    def uninstall_quota_poll(self) -> None:
        subprocess.run(
            ["systemctl", "--user", "disable", "--now", f"{self.QUOTA_UNIT}.timer"],
            check=False,
            capture_output=True,
        )
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=False, capture_output=True)
        for name in (f"{self.QUOTA_UNIT}.service", f"{self.QUOTA_UNIT}.timer"):
            try:
                (_user_dir() / name).unlink()
            except FileNotFoundError:
                pass

    def quota_poll_installed(self) -> bool:
        return (_user_dir() / f"{self.QUOTA_UNIT}.timer").exists()

    def quota_poll_interval_minutes(self) -> Optional[int]:
        """Interval baked into the installed timer, or None if not installed."""
        try:
            text = (_user_dir() / f"{self.QUOTA_UNIT}.timer").read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        for line in text.splitlines():
            if line.startswith("OnUnitActiveSec="):
                val = line.split("=", 1)[1].strip()
                if val.endswith("min"):
                    try:
                        return int(val[:-3])
                    except ValueError:
                        return None
        return None

    def quota_poll_next_run(self) -> Optional[_dt.datetime]:
        try:
            r = subprocess.run(
                ["systemctl", "--user", "show", f"{self.QUOTA_UNIT}.timer",
                 "--property=NextElapseUSecRealtime", "--value"],
                capture_output=True, text=True, check=True,
            )
        except (subprocess.CalledProcessError, FileNotFoundError):
            return None
        return _parse_systemd_timestamp(r.stdout)

    def last_status(self, task: Task) -> tuple[Optional[int], Optional[_dt.datetime]]:
        # Inspect journal for the most recent run of the service. We ask for
        # several lines because the record carrying the exit status is not
        # necessarily the very last line emitted for a run.
        try:
            r = subprocess.run(
                ["journalctl", "--user", "-u", f"mdrunner-{task.id}.service", "-n", "20",
                 "--output=json", "--no-pager"],
                capture_output=True, text=True, check=True,
            )
        except (subprocess.CalledProcessError, FileNotFoundError):
            return None, None
        import json

        last_dt: Optional[_dt.datetime] = None
        exit_code: Optional[int] = None
        for line in r.stdout.splitlines():  # journalctl emits oldest -> newest
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = rec.get("__REALTIME_TIMESTAMP")
            if ts:
                try:
                    last_dt = _dt.datetime.fromtimestamp(int(ts) / 1_000_000)
                except (ValueError, TypeError):
                    pass
            # EXIT_STATUS is the numeric code; EXIT_CODE is the kind
            # ("exited" / "killed"). Prefer the former, fall back to a
            # numeric-looking EXIT_CODE. Last record with one wins.
            for key in ("EXIT_STATUS", "EXIT_CODE"):
                val = rec.get(key)
                if val is None:
                    continue
                try:
                    exit_code = int(val)
                except (ValueError, TypeError):
                    continue
                break
        return exit_code, last_dt
