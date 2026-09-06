"""Windows scheduler — Task Scheduler via schtasks.exe.

Each mdrunner task becomes a single scheduled task:
    Name: mdrunner-<id>
    Action: <mdrunner.exe> run <id> --mode scheduled
    Trigger: weekly / daily / one-time depending on schedule

We use schtasks.exe (present on all modern Windows). For deeper integration
the win32com.client (TaskScheduler COM API) would be richer, but it requires
pywin32 — schtasks is the lower-friction choice for Phase 3.
"""

from __future__ import annotations

import datetime as _dt
import re
import subprocess
from pathlib import Path
from typing import Optional

from ..config import Task


def _task_name(task_id: str) -> str:
    # Task Scheduler name restrictions: no \, /, :, *, ?, <, >, |, &, ^, $
    safe = re.sub(r"[^A-Za-z0-9_]", "_", task_id)
    return f"mdrunner-{safe}"


def _build_trigger(task: Task) -> tuple[list[str], str]:
    """Return (schtasks /create args for the trigger, human-readable summary)."""
    s = task.schedule
    if s.mode == "quota":
        raise ValueError(
            "quota-triggered tasks have no OS calendar; "
            "install the quota poll timer instead"
        )
    common = ["/SC"]
    if s.mode == "once":
        today = _dt.date.today().strftime("%m/%d/%Y")
        return common + ["ONCE", "/SD", today, "/ST", s.time], f"once @ {s.time}"
    if s.mode == "interval":
        minutes = max(1, int(s.interval_minutes))
        # schtasks supports minute-level repetition via /MO 0:minute:0
        return (
            common + ["MINUTE", "/MO", str(minutes)],
            f"every {minutes} min",
        )
    if s.mode == "daily":
        return common + ["DAILY", "/ST", s.time], f"daily @ {s.time}"
    # weekly
    day_map = {"mon": "MON", "tue": "TUE", "wed": "WED", "thu": "THU",
               "fri": "FRI", "sat": "SAT", "sun": "SUN"}
    days = ",".join(day_map.get(d, "") for d in s.days if day_map.get(d))
    if not days:
        days = "MON"
    return common + ["WEEKLY", "/D", days, "/ST", s.time], f"{days} @ {s.time}"


def _query_state(task_id: str) -> tuple[Optional[int], Optional[_dt.datetime], Optional[_dt.datetime]]:
    """Parse `schtasks /query /xml` for a given task id. Returns (exit, last, next)."""
    return _query_named(_task_name(task_id))


def _query_named(name: str) -> tuple[Optional[int], Optional[_dt.datetime], Optional[_dt.datetime]]:
    """Parse `schtasks /query /xml` for a scheduled-task name."""
    try:
        r = subprocess.run(
            ["schtasks", "/query", "/tn", name, "/xml", "/fo", "LIST"],
            capture_output=True, text=True, check=False,
        )
    except FileNotFoundError:
        return None, None, None
    if r.returncode != 0 or "<Task>" not in r.stdout:
        return None, None, None
    text = r.stdout
    # Pull a few fields; schtasks XML is a verbose blob.
    def get(pattern: str) -> Optional[str]:
        m = re.search(pattern, text)
        return m.group(1) if m else None

    last = get(r"<LastRunTime>([^<]*)</LastRunTime>")
    nxt = get(r"<NextRunTime>([^<]*)</NextRunTime>")
    status = get(r"<Status>([^<]*)</Status>")
    exit_code = 0 if status == "Ready" else None
    last_dt = _parse_schtasks_dt(last) if last else None
    next_dt = _parse_schtasks_dt(nxt) if nxt else None
    return exit_code, last_dt, next_dt


_SCHTASKS_DT_FORMATS = (
    "%m/%d/%Y %I:%M:%S %p",
    "%m/%d/%Y %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
)


def _parse_schtasks_dt(s: str) -> Optional[_dt.datetime]:
    s = s.strip()
    if not s or s in {"N/A", "Disabled"}:
        return None
    for fmt in _SCHTASKS_DT_FORMATS:
        try:
            return _dt.datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


_ISO_DUR = re.compile(
    r"^P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?$",
    re.I,
)


def _parse_repetition_minutes(xml: str) -> Optional[int]:
    """Read the first ``<Interval>PTxx</Interval>`` out of schtasks XML."""
    m = re.search(r"<Interval>\s*([^<]+?)\s*</Interval>", xml or "", re.I)
    if not m:
        return None
    token = m.group(1).strip().upper().replace(" ", "")
    dm = _ISO_DUR.fullmatch(token)
    if not dm or not any(dm.groups()):
        return None
    days, hours, minutes, seconds = (int(x) if x else 0 for x in dm.groups())
    return days * 1440 + hours * 60 + minutes + seconds // 60


class WindowsScheduler:
    # Full Task Scheduler name (hyphens are allowed; _task_name would
    # turn "quota-poll" into "quota_poll"). Matches Linux unit naming.
    QUOTA_TASK_NAME = "mdrunner-quota-poll"

    def install(self, task: Task, mdrunner_executable: Path) -> None:
        trigger_args, _summary = _build_trigger(task)
        name = _task_name(task.id)
        # /TR takes the full command; schtasks requires surrounding quotes if spaces.
        tr = f'"{mdrunner_executable}" run {task.id} --mode scheduled'
        cmd = [
            "schtasks", "/create",
            "/TN", name,
            "/TR", tr,
            *trigger_args,
            "/F",  # force overwrite if exists
        ]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(f"schtasks failed: {r.stderr.strip() or r.stdout.strip()}")

    def uninstall(self, task: Task) -> None:
        name = _task_name(task.id)
        subprocess.run(
            ["schtasks", "/delete", "/tn", name, "/F"],
            check=False, capture_output=True,
        )

    def is_installed(self, task: Task) -> bool:
        name = _task_name(task.id)
        r = subprocess.run(
            ["schtasks", "/query", "/tn", name],
            capture_output=True, text=True, check=False,
        )
        return r.returncode == 0

    def next_run(self, task: Task) -> Optional[_dt.datetime]:
        _exit, _last, nxt = _query_state(task.id)
        return nxt

    def last_status(self, task: Task) -> tuple[Optional[int], Optional[_dt.datetime]]:
        exit_code, last, _next = _query_state(task.id)
        return exit_code, last

    # --- quota poller (a single non-task scheduled task) ------------------

    def install_quota_poll(self, interval_minutes: int, mdrunner_executable: Path) -> None:
        # schtasks /SC MINUTE accepts 1–1439
        interval = max(1, min(int(interval_minutes), 1439))
        name = self.QUOTA_TASK_NAME
        tr = f'"{mdrunner_executable}" quota-tick'
        cmd = [
            "schtasks", "/create",
            "/TN", name,
            "/TR", tr,
            "/SC", "MINUTE",
            "/MO", str(interval),
            "/F",
        ]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(f"schtasks failed: {r.stderr.strip() or r.stdout.strip()}")

    def uninstall_quota_poll(self) -> None:
        subprocess.run(
            ["schtasks", "/delete", "/tn", self.QUOTA_TASK_NAME, "/F"],
            check=False, capture_output=True,
        )

    def quota_poll_installed(self) -> bool:
        try:
            r = subprocess.run(
                ["schtasks", "/query", "/tn", self.QUOTA_TASK_NAME],
                capture_output=True, text=True, check=False,
            )
        except FileNotFoundError:
            return False
        return r.returncode == 0

    def quota_poll_interval_minutes(self) -> Optional[int]:
        try:
            r = subprocess.run(
                ["schtasks", "/query", "/tn", self.QUOTA_TASK_NAME, "/xml", "/fo", "LIST"],
                capture_output=True, text=True, check=False,
            )
        except FileNotFoundError:
            return None
        if r.returncode != 0:
            return None
        return _parse_repetition_minutes(r.stdout)

    def quota_poll_next_run(self) -> Optional[_dt.datetime]:
        _exit, _last, nxt = _query_named(self.QUOTA_TASK_NAME)
        return nxt