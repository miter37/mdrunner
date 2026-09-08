"""Next-7-day schedule projection — CLI text + GUI dialog share one core.

Everything is computed from tasks.yaml (what *would* fire), not the OS
scheduler (what *is* installed). Disabled tasks are excluded; `once` tasks
and quota-triggered tasks appear in their own trailer rows.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field

from .config import Schedule, Task

_WDAY = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


@dataclass
class DayEvent:
    """One projected firing: task name + local wall-clock time."""

    time: _dt.time
    name: str
    task_id: str


@dataclass
class WeekProjection:
    """7 day columns (dates) + per-day events + out-of-grid trailers."""

    days: list[_dt.date] = field(default_factory=list)
    events: dict[str, list[DayEvent]] = field(default_factory=dict)  # iso date -> events
    once: list[tuple[str, DayEvent]] = field(default_factory=list)  # (date-label, event)
    quota: list[str] = field(default_factory=list)  # task names, time-triggerless


def _parse_hhmm(s: str) -> _dt.time | None:
    try:
        h, m = s.split(":")[:2]
        return _dt.time(int(h), int(m))
    except (ValueError, AttributeError):
        return None


def _local_hhmm(ev_date: _dt.date, t: _dt.time, tz: str) -> tuple[str, _dt.time] | None:
    """Map a task-local (date, time) firing to (iso-date, time) on the host.

    The projection columns live in host-local dates; a task in another zone
    can spill onto the neighbouring column.
    """
    try:
        from zoneinfo import ZoneInfo

        zone = ZoneInfo(tz)
    except Exception:  # noqa: BLE001
        return ev_date.isoformat(), t
    local = _dt.datetime(ev_date.year, ev_date.month, ev_date.day, t.hour, t.minute, tzinfo=zone)
    host = local.astimezone()
    return host.date().isoformat(), host.time().replace(second=0, microsecond=0)


def project_week(
    tasks: list[Task], *, days: int = 7, now: _dt.datetime | None = None
) -> WeekProjection:
    """Project time-triggered firings over the next ``days`` host-local days."""
    now_utc = now or _dt.datetime.now(_dt.UTC)
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=_dt.UTC)
    days = max(1, min(int(days), 14))
    proj = WeekProjection()
    host_today = now_utc.astimezone().date()
    iso_days = [(host_today + _dt.timedelta(days=i)).isoformat() for i in range(days)]
    proj.days = [host_today + _dt.timedelta(days=i) for i in range(days)]
    proj.events = {d: [] for d in iso_days}

    def add(ev_date: _dt.date, t: _dt.time, task: Task) -> None:
        if not task.enabled or t is None:
            return
        mapped = _local_hhmm(ev_date, t, task.schedule.timezone or "Asia/Seoul")
        if mapped is None:
            return
        iso, lt = mapped
        if iso in proj.events:
            proj.events[iso].append(DayEvent(lt, task.name, task.id))

    for task in tasks:
        if not task.enabled:
            continue
        s: Schedule = task.schedule
        t = _parse_hhmm(s.time)
        if s.mode == "quota":
            proj.quota.append(task.name)
        elif s.mode == "once":
            if t is not None:
                proj.once.append((host_today.isoformat(), DayEvent(t, task.name, task.id)))
        elif s.mode == "interval":
            step = max(1, int(s.interval_minutes or 60))
            cursor = host_today
            # Anchor at local midnight; a step that divides the day (every
            # N min) covers the same wall-clock slots each day, so enumerate
            # one day and stamp it on every column.
            seen: set[str] = set()
            while (cursor - host_today).days < days:
                key = cursor.isoformat()
                if key not in seen:
                    seen.add(key)
                    for k in range(0, 24 * 60, step):
                        hh, mm = divmod(k, 60)
                        add(cursor, _dt.time(hh, mm), task)
                        if len(proj.events.get(key, [])) > 200:
                            break
                cursor += _dt.timedelta(days=1)
        elif s.mode == "daily":
            if t is None:
                continue
            for d in proj.days:
                add(d, t, task)
        elif s.mode == "weekly":
            if t is None:
                continue
            want = {d.lower() for d in (s.days or [])}
            for d in proj.days:
                if _WDAY[d.weekday()] in want:
                    add(d, t, task)
    for evs in proj.events.values():
        evs.sort(key=lambda e: (e.time.hour, e.time.minute, e.name))
    proj.once.sort(key=lambda pair: (pair[0], pair[1].time.hour, pair[1].time.minute))
    proj.quota.sort()
    return proj


def render_text(proj: WeekProjection) -> str:
    """Plain-text table for `mdrunner schedule-overview`. No Qt needed."""
    lines = ["7-day schedule projection (tasks.yaml — what would fire):", ""]
    any_event = False
    for d in proj.days:
        evs = proj.events.get(d.isoformat(), [])
        label = d.strftime("%a %m-%d")
        if not evs:
            lines.append(f"{label}:  —")
            continue
        any_event = True
        lines.append(f"{label}:")
        for e in evs:
            lines.append(f"  {e.time.strftime('%H:%M')}  {e.name}")
    if proj.once:
        any_event = True
        lines.append("")
        lines.append("once:")
        for label, e in proj.once:
            lines.append(f"  {label} {e.time.strftime('%H:%M')}  {e.name}")
    if proj.quota:
        lines.append("")
        lines.append("quota-triggered (no time — fires on quota-tick):")
        for name in proj.quota:
            lines.append(f"  {name}")
    if not any_event and not proj.quota:
        lines.append("(no scheduled tasks — add one or enable the quota poll)")
    return "\n".join(lines)
