"""Windows trigger builder must not treat quota mode as weekly.

The quota poller is a separate schtasks entry (`mdrunner-quota-poll`) that
runs `quota-tick` on a minute interval — same role as the systemd/launchd
poll timer on the other platforms.
"""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from mdrunner.config import Schedule, Task
from mdrunner.scheduler import windows
from mdrunner.scheduler.windows import WindowsScheduler, _build_trigger


def test_quota_mode_is_not_a_weekly_schtask():
    task = Task(id="t", name="t", schedule=Schedule(mode="quota"))
    with pytest.raises(ValueError, match="quota"):
        _build_trigger(task)


def test_parse_schtasks_repetition_minutes():
    xml = """
    <Task>
      <Triggers>
        <TimeTrigger>
          <Repetition>
            <Interval>PT10M</Interval>
          </Repetition>
        </TimeTrigger>
      </Triggers>
    </Task>
    """
    assert windows._parse_repetition_minutes(xml) == 10
    assert windows._parse_repetition_minutes("<Interval>PT1H</Interval>") == 60
    assert windows._parse_repetition_minutes("<Interval>PT2H30M</Interval>") == 150
    assert windows._parse_repetition_minutes("<Interval>PT600S</Interval>") == 10
    assert windows._parse_repetition_minutes("<Task></Task>") is None


def test_install_quota_poll_creates_minute_schtask(monkeypatch):
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        r = MagicMock()
        r.returncode = 0
        r.stdout = ""
        r.stderr = ""
        return r

    monkeypatch.setattr(windows.subprocess, "run", fake_run)
    WindowsScheduler().install_quota_poll(10, Path(r"C:\mdrunner.exe"))
    cmd = captured["cmd"]
    assert cmd[:2] == ["schtasks", "/create"]
    assert "mdrunner-quota-poll" in cmd
    assert "MINUTE" in cmd
    assert cmd[cmd.index("/MO") + 1] == "10"
    tr = cmd[cmd.index("/TR") + 1]
    assert "quota-tick" in tr
    assert "mdrunner.exe" in tr


def test_quota_poll_installed_and_next_run(monkeypatch):
    xml = """
    <Task>
      <LastRunTime>N/A</LastRunTime>
      <NextRunTime>09/07/2026 07:12:00 AM</NextRunTime>
      <Status>Ready</Status>
      <Triggers><TimeTrigger><Repetition><Interval>PT15M</Interval></Repetition></TimeTrigger></Triggers>
    </Task>
    """

    def fake_run(cmd, **kwargs):
        r = MagicMock()
        r.returncode = 0
        r.stdout = xml if "/xml" in cmd or any(a.lower() == "/xml" for a in cmd) else "ok"
        r.stderr = ""
        return r

    monkeypatch.setattr(windows.subprocess, "run", fake_run)
    sched = WindowsScheduler()
    assert sched.quota_poll_installed()
    assert sched.quota_poll_interval_minutes() == 15
    nxt = sched.quota_poll_next_run()
    assert nxt is not None
    assert (nxt.year, nxt.month, nxt.day, nxt.hour, nxt.minute) == (2026, 9, 7, 7, 12)


def test_uninstall_quota_poll_deletes_schtask(monkeypatch):
    captured = []

    def fake_run(cmd, **kwargs):
        captured.append(cmd)
        r = MagicMock()
        r.returncode = 0
        r.stdout = ""
        r.stderr = ""
        return r

    monkeypatch.setattr(windows.subprocess, "run", fake_run)
    WindowsScheduler().uninstall_quota_poll()
    assert captured
    assert captured[0][:2] == ["schtasks", "/delete"]
    assert "mdrunner-quota-poll" in captured[0]
