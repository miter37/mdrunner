"""macOS scheduler — launchd user agent.

Each mdrunner task becomes a plist in ``~/Library/LaunchAgents/``:

    com.mdrunner.<id>.plist   ProgramArguments = mdrunner run <id> --mode scheduled

Triggers map to launchd keys:
    daily    -> StartCalendarInterval {Hour, Minute}
    weekly   -> StartCalendarInterval [ {Weekday, Hour, Minute}, ... ]
    interval -> StartInterval <seconds>
    once     -> StartCalendarInterval {Month, Day, Hour, Minute}   (best effort;
               launchd has no true one-shot — it repeats yearly)

launchd agents inherit a bare PATH, so the plist pins a PATH that includes
Homebrew, ~/.local/bin and every nvm node bin (where `codex` lives).
"""

from __future__ import annotations

import datetime as _dt
import os
import plistlib
import re
import subprocess
from pathlib import Path
from typing import Optional

from ..config import Task

_QUOTA_LABEL = "com.mdrunner.quota-poll"
# launchd Weekday: 0/7 = Sunday, 1 = Monday ... 6 = Saturday
_WEEKDAY = {"sun": 0, "mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6}


def _agents_dir() -> Path:
    d = Path.home() / "Library" / "LaunchAgents"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _label(task_id: str) -> str:
    return f"com.mdrunner.{re.sub(r'[^A-Za-z0-9_.-]', '_', task_id)}"


def _plist_path(label: str) -> Path:
    return _agents_dir() / f"{label}.plist"


def _hhmm(t: str) -> tuple[int, int]:
    try:
        h, m = t.split(":")
        return int(h), int(m)
    except (ValueError, AttributeError):
        return 7, 0


def _harvest_path() -> str:
    """A PATH launchd agents can actually use to find the agent CLIs."""
    home = os.path.expanduser("~")
    parts = [
        "/opt/homebrew/bin", "/opt/homebrew/sbin",
        "/usr/local/bin", "/usr/local/sbin",
        f"{home}/.local/bin", f"{home}/bin",
        "/usr/bin", "/bin", "/usr/sbin", "/sbin",
    ]
    node_root = Path(home) / ".nvm" / "versions" / "node"
    if node_root.is_dir():
        parts[0:0] = [str(p / "bin") for p in sorted(node_root.iterdir()) if (p / "bin").is_dir()]
    # de-dupe, keep order
    seen, out = set(), []
    for p in parts:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return ":".join(out)


def _calendar_intervals(task: Task):
    s = task.schedule
    h, m = _hhmm(s.time)
    if s.mode == "daily":
        return {"Hour": h, "Minute": m}
    if s.mode == "once":
        now = _dt.datetime.now()
        return {"Month": now.month, "Day": now.day, "Hour": h, "Minute": m}
    # weekly
    days = [d for d in s.days if d in _WEEKDAY] or ["mon"]
    return [{"Weekday": _WEEKDAY[d], "Hour": h, "Minute": m} for d in days]


def _build_plist(label: str, argv: list[str], task: Task, log_path: Path) -> dict:
    p: dict = {
        "Label": label,
        "ProgramArguments": argv,
        "RunAtLoad": False,
        "EnvironmentVariables": {"PATH": _harvest_path()},
        "StandardOutPath": str(log_path),
        "StandardErrorPath": str(log_path),
        "ProcessType": "Background",
    }
    if task.schedule.mode == "interval":
        p["StartInterval"] = max(60, int(task.schedule.interval_minutes) * 60)
    else:
        p["StartCalendarInterval"] = _calendar_intervals(task)
    return p


def _launchctl(*args: str, check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(["launchctl", *args], capture_output=True, text=True, check=check)


def _domain() -> str:
    return f"gui/{os.getuid()}"


def _load(plist: Path, label: str) -> None:
    # `bootstrap` is the modern verb; fall back to the long-lived `load -w`.
    r = _launchctl("bootstrap", _domain(), str(plist))
    if r.returncode != 0 and "already bootstrapped" not in (r.stderr + r.stdout):
        r2 = _launchctl("load", "-w", str(plist))
        if r2.returncode != 0:
            raise RuntimeError(
                f"launchctl could not load {label}: "
                f"{(r.stderr or r.stdout or r2.stderr or r2.stdout).strip()}"
            )
    _launchctl("enable", f"{_domain()}/{label}")


def _unload(plist: Path, label: str) -> None:
    _launchctl("bootout", f"{_domain()}/{label}")
    if plist.exists():
        _launchctl("unload", "-w", str(plist))


class MacScheduler:
    # --- per-task ---------------------------------------------------------

    def install(self, task: Task, mdrunner_executable: Path) -> None:
        from ..utils.paths import log_dir

        label = _label(task.id)
        plist = _plist_path(label)
        argv = [str(mdrunner_executable), "run", task.id, "--mode", "scheduled"]
        log_path = log_dir() / f"launchd-{task.id}.log"
        if plist.exists():  # replace cleanly
            _unload(plist, label)
        plist.write_bytes(plistlib.dumps(_build_plist(label, argv, task, log_path)))
        _load(plist, label)

    def uninstall(self, task: Task) -> None:
        label = _label(task.id)
        plist = _plist_path(label)
        try:
            _unload(plist, label)
        finally:
            try:
                plist.unlink()
            except FileNotFoundError:
                pass

    def is_installed(self, task: Task) -> bool:
        return _plist_path(_label(task.id)).exists()

    def next_run(self, task: Task) -> Optional[_dt.datetime]:
        # launchd does not expose the next fire time; the UI falls back to
        # its own estimate from the schedule spec.
        return None

    def last_status(self, task: Task) -> tuple[Optional[int], Optional[_dt.datetime]]:
        label = _label(task.id)
        r = _launchctl("print", f"{_domain()}/{label}")
        exit_code = None
        if r.returncode == 0:
            m = re.search(r"last exit code\s*=\s*(-?\d+)", r.stdout)
            if m:
                exit_code = int(m.group(1))
        from ..utils.paths import log_dir

        log_path = log_dir() / f"launchd-{task.id}.log"
        last_dt = None
        try:
            last_dt = _dt.datetime.fromtimestamp(log_path.stat().st_mtime)
        except OSError:
            pass
        return exit_code, last_dt

    # --- quota poll (a single non-task agent) ---------------------------

    def install_quota_poll(self, interval_minutes: int, mdrunner_executable: Path) -> None:
        from ..utils.paths import log_dir

        plist = _plist_path(_QUOTA_LABEL)
        if plist.exists():
            _unload(plist, _QUOTA_LABEL)
        body = {
            "Label": _QUOTA_LABEL,
            "ProgramArguments": [str(mdrunner_executable), "quota", "--write"],
            "RunAtLoad": True,
            "StartInterval": max(60, int(interval_minutes) * 60),
            "EnvironmentVariables": {"PATH": _harvest_path()},
            "StandardOutPath": str(log_dir() / "launchd-quota-poll.log"),
            "StandardErrorPath": str(log_dir() / "launchd-quota-poll.log"),
            "ProcessType": "Background",
        }
        plist.write_bytes(plistlib.dumps(body))
        _load(plist, _QUOTA_LABEL)

    def uninstall_quota_poll(self) -> None:
        plist = _plist_path(_QUOTA_LABEL)
        try:
            _unload(plist, _QUOTA_LABEL)
        finally:
            try:
                plist.unlink()
            except FileNotFoundError:
                pass

    def quota_poll_installed(self) -> bool:
        return _plist_path(_QUOTA_LABEL).exists()

    def quota_poll_interval_minutes(self) -> Optional[int]:
        try:
            data = plistlib.loads(_plist_path(_QUOTA_LABEL).read_bytes())
        except (OSError, plistlib.InvalidFileException):
            return None
        secs = data.get("StartInterval")
        return int(secs) // 60 if secs else None

    def quota_poll_next_run(self) -> Optional[_dt.datetime]:
        return None
