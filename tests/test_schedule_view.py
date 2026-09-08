"""schedule-overview: the 7-day projection over time-triggered tasks."""

import datetime as _dt

from mdrunner.config import (
    MinRerunInterval,
    QuotaClause,
    QuotaCondition,
    Schedule,
    Task,
)
from mdrunner.schedule_view import project_week, render_text


def _t(**kw) -> Task:
    kw.setdefault("id", "t")
    kw.setdefault("name", "T")
    kw.setdefault("prompt_file", "p.md")
    return Task(**kw)


def test_daily_and_weekly_land_on_right_days():
    now = _dt.datetime(2026, 9, 7, 12, 0, tzinfo=_dt.UTC)  # a Monday
    tasks = [
        _t(id="d", name="Daily", schedule=Schedule(mode="daily", time="07:00")),
        _t(
            id="w",
            name="Weekend",
            schedule=Schedule(mode="weekly", days=["sat", "sun"], time="09:00"),
        ),
        _t(id="off", name="Off", enabled=False, schedule=Schedule(mode="daily", time="07:00")),
    ]
    proj = project_week(tasks, now=now)
    assert len(proj.days) == 7
    monday = proj.days[0].isoformat()
    assert [e.name for e in proj.events[monday]] == ["Daily"]
    sat = next(d.isoformat() for d in proj.days if d.weekday() == 5)
    assert {e.name for e in proj.events[sat]} == {"Daily", "Weekend"}
    assert "Off" not in render_text(proj)


def test_quota_and_once_become_trailers():
    now = _dt.datetime(2026, 9, 7, 12, 0, tzinfo=_dt.UTC)
    tasks = [
        _t(
            id="q",
            name="Qt",
            schedule=Schedule(mode="quota"),
            quota_condition=QuotaCondition(weekly_used=QuotaClause(True, 90.0)),
            min_rerun_interval=MinRerunInterval(True, 6.0),
        ),
        _t(id="o", name="Once", schedule=Schedule(mode="once", time="15:30")),
    ]
    proj = project_week(tasks, now=now)
    assert proj.quota == ["Qt"]
    assert [e.name for _l, e in proj.once] == ["Once"]
    text = render_text(proj)
    assert "quota-triggered" in text and "once:" in text


def test_interval_expands_and_timezone_spill_is_safe():
    now = _dt.datetime(2026, 9, 7, 12, 0, tzinfo=_dt.UTC)
    tasks = [
        _t(
            id="i",
            name="Hourly",
            schedule=Schedule(mode="interval", interval_minutes=60),
        ),
        _t(
            id="z",
            name="Far",
            schedule=Schedule(mode="daily", time="02:00", timezone="Pacific/Kiritimati"),
        ),
    ]
    proj = project_week(tasks, now=now)
    assert len(proj.events[proj.days[0].isoformat()]) >= 24  # hourly covers the day
    # the Kiritimati 02:00 lands on *some* column, never dropped
    assert any(e.name == "Far" for evs in proj.events.values() for e in evs)
