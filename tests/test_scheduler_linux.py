import datetime as dt

from mdrunner.config import Schedule, Task
from mdrunner.scheduler.linux import _format_oncalendar, _parse_systemd_timestamp


def test_parse_systemd_timestamp_with_weekday_and_tz():
    # This is the exact shape `systemctl show --value` prints.
    assert _parse_systemd_timestamp("Mon 2026-09-07 07:12:00 KST") == dt.datetime(
        2026, 9, 7, 7, 12, 0
    )


def test_parse_systemd_timestamp_without_weekday():
    assert _parse_systemd_timestamp("2026-09-07 20:00:00 UTC") == dt.datetime(
        2026, 9, 7, 20, 0, 0
    )


def test_parse_systemd_timestamp_empty_or_na():
    assert _parse_systemd_timestamp("") is None
    assert _parse_systemd_timestamp("n/a") is None
    assert _parse_systemd_timestamp("0") is None
    assert _parse_systemd_timestamp("   \n") is None


def _task(**kw):
    tz = kw.pop("timezone", "Asia/Seoul")
    return Task(id="t", name="t", schedule=Schedule(timezone=tz, **kw))


def test_format_oncalendar_modes():
    assert _format_oncalendar(_task(mode="daily", time="07:25"))[0] == "OnCalendar=*-*-* 07:25"
    assert (
        _format_oncalendar(_task(mode="weekly", days=["mon", "tue"], time="20:00"))[0]
        == "OnCalendar=mon,tue *-*-* 20:00"
    )


def test_interval_uses_relative_repeat_not_broken_oncalendar():
    # Regression: OnCalendar=*:0/90:0 is invalid (minute field is 0-59), which
    # silently broke every interval task of an hour or more.
    line, tz = _format_oncalendar(_task(mode="interval", interval_minutes=90))
    assert "OnUnitActiveSec=90min" in line
    assert "OnCalendar" not in line
    assert tz == ""  # relative timer — no timezone
    short = _format_oncalendar(_task(mode="interval", interval_minutes=5))[0]
    assert "OnUnitActiveSec=5min" in short


def test_timezone_line_emitted_for_calendar_modes():
    assert _format_oncalendar(_task(mode="daily", time="07:00"))[1] == "Timezone=Asia/Seoul\n"
    assert (
        _format_oncalendar(_task(mode="weekly", days=["mon"], time="09:00", timezone="America/New_York"))[1]
        == "Timezone=America/New_York\n"
    )
    # a bare word (not an IANA name) is not emitted
    assert _format_oncalendar(_task(mode="daily", time="07:00", timezone="local"))[1] == ""
