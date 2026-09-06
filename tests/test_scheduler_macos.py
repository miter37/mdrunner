import plistlib

from mdrunner.config import Schedule, Task
from mdrunner.scheduler import macos


def _task(**kw):
    return Task(id="task_1", name="t", prompt_file="/x.md", schedule=Schedule(**kw))


def test_label_is_sanitised():
    assert macos._label("task_2026-07-14 195433") == "com.mdrunner.task_2026-07-14_195433"


def test_calendar_daily():
    assert macos._calendar_intervals(_task(mode="daily", time="07:25")) == {
        "Hour": 7,
        "Minute": 25,
    }


def test_calendar_weekly():
    ci = macos._calendar_intervals(
        _task(mode="weekly", days=["mon", "wed", "sun"], time="20:00")
    )
    assert isinstance(ci, list)
    assert {d["Weekday"] for d in ci} == {1, 3, 0}
    assert all(d["Hour"] == 20 and d["Minute"] == 0 for d in ci)


def test_calendar_weekly_empty_days_falls_back():
    ci = macos._calendar_intervals(_task(mode="weekly", days=[], time="09:00"))
    assert ci == [{"Weekday": 1, "Hour": 9, "Minute": 0}]


def test_plist_interval_uses_startinterval_seconds_min_60():
    from pathlib import Path

    body = macos._build_plist(
        "com.mdrunner.x",
        ["/bin/mdrunner", "run", "x", "--mode", "scheduled"],
        _task(mode="interval", interval_minutes=90),
        Path("/tmp/x.log"),
    )
    assert body["StartInterval"] == 90 * 60
    assert "StartCalendarInterval" not in body
    assert body["RunAtLoad"] is False
    assert body["ProgramArguments"][1:3] == ["run", "x"]
    # sub-minute intervals are clamped
    body2 = macos._build_plist(
        "l", ["a"], _task(mode="interval", interval_minutes=0), Path("/tmp/x.log")
    )
    assert body2["StartInterval"] == 60


def test_plist_daily_roundtrips_and_pins_path():
    from pathlib import Path

    body = macos._build_plist(
        "com.mdrunner.d", ["/bin/mdrunner", "run", "d"],
        _task(mode="daily", time="07:00"), Path("/tmp/d.log"),
    )
    blob = plistlib.dumps(body)
    back = plistlib.loads(blob)
    assert back["StartCalendarInterval"] == {"Hour": 7, "Minute": 0}
    assert "/bin" in back["EnvironmentVariables"]["PATH"]


def test_harvest_path_includes_common_dirs():
    p = macos._harvest_path()
    assert "/opt/homebrew/bin" in p
    assert p.endswith("/sbin") or "/usr/bin" in p


def test_current_returns_mac_on_darwin(monkeypatch):
    import mdrunner.scheduler.base as base

    monkeypatch.setattr(base.sys, "platform", "darwin")
    assert isinstance(base.current(), macos.MacScheduler)


def test_quota_mode_plist_is_not_a_weekly_calendar():
    import pytest
    from pathlib import Path

    with pytest.raises(ValueError, match="quota"):
        macos._build_plist(
            "com.mdrunner.q",
            ["/bin/mdrunner", "run", "q", "--mode", "scheduled"],
            _task(mode="quota"),
            Path("/tmp/q.log"),
        )
