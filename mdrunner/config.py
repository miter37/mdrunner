"""Configuration schemas and loaders for mdrunner.

Two YAML files live in the config directory:

- tasks.yaml — list of task definitions
- settings.yaml — per-agent CLI config + global defaults

Both are loaded into typed dataclasses for ergonomic access, but kept
trivially serializable so the GUI can round-trip them through PyYAML.
"""

from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


class ConfigError(ValueError):
    """Raised on a malformed tasks.yaml or settings.yaml."""


# ---------------------------------------------------------------------------
# Task schema
# ---------------------------------------------------------------------------

VALID_WEEKDAYS = {"mon", "tue", "wed", "thu", "fri", "sat", "sun"}
# "quota" = no time trigger; the quota-tick poller evaluates the condition.
VALID_SCHEDULE_MODES = {"once", "daily", "weekly", "interval", "quota"}
QUOTA_CAPABLE_AGENTS = {"claude", "codex", "agy", "grok"}
# Agents that report a rolling 5-hour window (grok only reports weekly).
FIVE_HOUR_AGENTS = {"claude", "codex", "agy"}
VALID_ON_UNKNOWN = {"skip", "run"}
_TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})(?::(\d{2}))?$")


def _schedule_time_from_yaml(value: Any) -> str:
    """Normalize PyYAML's legacy sexagesimal parsing for unquoted HH:MM.

    Unquoted ``14:14`` becomes int 854; unquoted ``0:30`` becomes int 30.
    ``07:25:00`` (string or datetime.time) is folded to ``07:25``.
    """
    if isinstance(value, _dt.time):
        return f"{value.hour:02d}:{value.minute:02d}"
    if isinstance(value, _dt.timedelta):
        value = int(value.total_seconds()) // 60
    if isinstance(value, float) and value == int(value):
        value = int(value)
    if isinstance(value, int) and 0 <= value < 24 * 60:
        return f"{value // 60:02d}:{value % 60:02d}"
    text = str(value).strip()
    m = _TIME_RE.match(text)
    if not m:
        return text
    hour, minute = int(m.group(1)), int(m.group(2))
    if hour > 23 or minute > 59:
        return text
    return f"{hour:02d}:{minute:02d}"


@dataclass
class Schedule:
    mode: str = "weekly"
    days: list[str] = field(default_factory=lambda: ["mon", "tue", "wed", "thu", "fri"])
    time: str = "07:00"
    timezone: str = "Asia/Seoul"
    # interval mode: every N minutes
    interval_minutes: int = 60


@dataclass
class OnFailure:
    notify: bool = False


@dataclass
class MinRerunInterval:
    """Floor on how often a task may actually run, across every trigger
    (time schedule and quota trigger). Manual "Run now" bypasses it."""

    enabled: bool = True
    hours: float = 6.0


@dataclass
class QuotaClause:
    """One cell of the quota-condition grid.

    ``value`` is a percent for the ``*_used`` clauses and a number of hours
    for the ``*_reset`` clauses.

    ``op`` picks the comparison for the ``*_used`` clauses: ``"gte"`` runs
    when ``used_percent >= value`` (the default), ``"lte"`` when
    ``used_percent <= value``. It is ignored for the ``*_reset`` clauses,
    which always compare ``<=`` a number of hours.
    """

    enabled: bool = False
    value: float = 0.0
    op: str = "gte"


VALID_QUOTA_OPS = ("gte", "lte")

# how each op renders in labels / describe()
_QUOTA_OP_SYMBOL = {"gte": "≥", "lte": "≤"}


# (attr, window label, kind, short label) for every grid cell, in UI order.
QUOTA_CLAUSES: tuple[tuple[str, str, str, str], ...] = (
    ("weekly_used", "weekly", "used", "wk used"),
    ("weekly_reset", "weekly", "reset", "wk resets"),
    ("fivehour_used", "5h", "used", "5h used"),
    ("fivehour_reset", "5h", "reset", "5h resets"),
)


@dataclass
class QuotaCondition:
    """Run only when the task's own agent's quota meets ALL checked clauses.

    A 2x2 grid — {weekly, 5-hour} x {used %, resets within N hours}. Only
    the enabled clauses are AND-ed. The agent is always ``task.agent``.
    Each ``used`` clause carries its own operator (``>=`` or ``<=`` the
    percent, per ``QuotaClause.op``); ``reset`` is always ``<=`` a number
    of hours until the window refreshes.
    """

    weekly_used: QuotaClause = field(default_factory=lambda: QuotaClause(False, 90.0))
    weekly_reset: QuotaClause = field(default_factory=lambda: QuotaClause(False, 24.0))
    fivehour_used: QuotaClause = field(default_factory=lambda: QuotaClause(False, 90.0))
    fivehour_reset: QuotaClause = field(default_factory=lambda: QuotaClause(False, 3.0))
    on_unknown: str = "skip"     # skip | run  (when quota can't be read)

    def active(self) -> list[tuple[str, str, str, float, str]]:
        """[(short label, window, kind, value, op), ...] for the enabled clauses.

        ``op`` is ``"gte"``/``"lte"`` for ``used`` clauses and always
        ``"lte"`` for ``reset`` clauses (whose comparison is fixed).
        """
        out = []
        for attr, window, kind, label in QUOTA_CLAUSES:
            clause: QuotaClause = getattr(self, attr)
            if clause.enabled:
                op = clause.op if kind == "used" else "lte"
                out.append((label, window, kind, clause.value, op))
        return out

    def uses_five_hour(self) -> bool:
        return self.fivehour_used.enabled or self.fivehour_reset.enabled

    def describe(self, agent: str) -> str:
        parts = [
            f"{label}{_QUOTA_OP_SYMBOL.get(op, '≥')}{value:g}{'%' if kind == 'used' else 'h'}"
            for label, _w, kind, value, op in self.active()
        ]
        return f"{agent}: " + (" & ".join(parts) if parts else "(no clause)")


@dataclass
class Task:
    id: str
    name: str
    enabled: bool = True
    agent: str = "opencode"
    model: str | None = None
    prompt_file: str = ""
    working_dir: str | None = None
    schedule: Schedule = field(default_factory=Schedule)
    extra_args: list[str] = field(default_factory=list)
    timeout_minutes: int = 10
    on_failure: OnFailure = field(default_factory=OnFailure)
    notify_artifact: bool = False
    notify_final_message: bool = False
    notify_start: bool = False
    artifact_dir: str | None = None
    artifact_extensions: list[str] = field(default_factory=lambda: [".md"])
    min_rerun_interval: MinRerunInterval = field(default_factory=MinRerunInterval)
    quota_condition: QuotaCondition | None = None


def _clause_from_dict(raw: Any, task_id: str, attr: str, kind: str) -> QuotaClause:
    if not raw:
        default = 90.0 if kind == "used" else (24.0 if attr == "weekly_reset" else 3.0)
        return QuotaClause(False, default)
    if not isinstance(raw, dict):
        raise ConfigError(f"task '{task_id}': quota_condition.{attr} must be a mapping")
    try:
        value = float(raw.get("value", 0.0))
    except (TypeError, ValueError):
        raise ConfigError(
            f"task '{task_id}': quota_condition.{attr}.value must be a number"
        ) from None
    enabled = bool(raw.get("enabled", False))
    op = str(raw.get("op", "gte")).lower()
    if op not in VALID_QUOTA_OPS:
        raise ConfigError(
            f"task '{task_id}': quota_condition.{attr}.op must be one of "
            f"{', '.join(VALID_QUOTA_OPS)} (got {raw.get('op')!r})"
        )
    if kind == "reset":
        op = "gte"  # not applicable — reset always compares <= hours
    if enabled and kind == "used" and not 0.0 <= value <= 100.0:
        raise ConfigError(
            f"task '{task_id}': quota_condition.{attr}.value {value} out of range 0–100"
        )
    if enabled and kind == "reset" and value <= 0:
        raise ConfigError(
            f"task '{task_id}': quota_condition.{attr}.value must be > 0 hours (got {value})"
        )
    return QuotaClause(enabled, value, op)


def _quota_condition_from_dict(
    qc_data: Any, task_id: str, agent: str
) -> QuotaCondition | None:
    if not qc_data:
        return None
    if not isinstance(qc_data, dict):
        raise ConfigError(f"task '{task_id}': quota_condition must be a mapping")
    if agent not in QUOTA_CAPABLE_AGENTS:
        raise ConfigError(
            f"task '{task_id}': quota_condition needs a quota-capable agent "
            f"({', '.join(sorted(QUOTA_CAPABLE_AGENTS))}), not {agent!r}"
        )

    on_unknown = str(qc_data.get("on_unknown", "skip"))
    if on_unknown not in VALID_ON_UNKNOWN:
        raise ConfigError(
            f"task '{task_id}': invalid quota_condition.on_unknown {on_unknown!r} "
            f"(use one of {', '.join(sorted(VALID_ON_UNKNOWN))})"
        )

    # Back-compat: the first cut used a flat {window, percent, ...} shape.
    if "value" not in qc_data and ("window" in qc_data or "percent" in qc_data):
        pct = float(qc_data.get("percent", 90.0))
        win = str(qc_data.get("window", "weekly"))
        legacy = {"on_unknown": on_unknown}
        if win in ("weekly", "any", "all"):
            legacy["weekly_used"] = {"enabled": True, "value": pct}
        if win in ("5h", "any", "all"):
            legacy["fivehour_used"] = {"enabled": True, "value": pct}
        qc_data = legacy

    cond = QuotaCondition(
        weekly_used=_clause_from_dict(qc_data.get("weekly_used"), task_id, "weekly_used", "used"),
        weekly_reset=_clause_from_dict(
            qc_data.get("weekly_reset"), task_id, "weekly_reset", "reset"
        ),
        fivehour_used=_clause_from_dict(
            qc_data.get("fivehour_used"), task_id, "fivehour_used", "used"
        ),
        fivehour_reset=_clause_from_dict(
            qc_data.get("fivehour_reset"), task_id, "fivehour_reset", "reset"
        ),
        on_unknown=on_unknown,
    )
    if not cond.active():
        raise ConfigError(
            f"task '{task_id}': quota_condition needs at least one enabled clause "
            f"(weekly_used, weekly_reset, fivehour_used, fivehour_reset)"
        )
    if cond.uses_five_hour() and agent not in FIVE_HOUR_AGENTS:
        raise ConfigError(
            f"task '{task_id}': {agent!r} only reports a weekly window — "
            f"remove the 5-hour clause(s)"
        )
    return cond


def task_from_dict(data: dict[str, Any]) -> Task:
    if not isinstance(data, dict):
        raise ConfigError(f"task must be a mapping, got {type(data).__name__}")
    if "id" not in data or "name" not in data:
        raise ConfigError("each task needs 'id' and 'name'")
    sched_data = data.get("schedule") or {}
    if not isinstance(sched_data, dict):
        raise ConfigError(f"task.schedule must be a mapping (got {type(sched_data).__name__})")
    if "days" in sched_data:
        days = [str(d).lower() for d in (sched_data.get("days") or [])]
    else:
        days = ["mon", "tue", "wed", "thu", "fri"]
    schedule = Schedule(
        mode=str(sched_data.get("mode", "weekly")),
        days=days,
        time=_schedule_time_from_yaml(sched_data.get("time", "07:00")),
        timezone=str(sched_data.get("timezone", "Asia/Seoul")),
        interval_minutes=int(sched_data.get("interval_minutes", 60)),
    )
    if schedule.mode not in VALID_SCHEDULE_MODES:
        raise ConfigError(
            f"task '{data['id']}' has invalid schedule.mode={schedule.mode!r}"
        )
    # After normalization a valid time is always HH:MM. Anything else is junk
    # (e.g. YAML ``24:00`` → 1440, or a bare ``25:00``).
    if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", schedule.time):
        raise ConfigError(
            f"task '{data['id']}' has invalid schedule.time {schedule.time!r} "
            f"(use HH:MM, 00:00–23:59)"
        )
    if schedule.mode == "weekly":
        if not schedule.days:
            raise ConfigError(
                f"task '{data['id']}' weekly schedule needs at least one weekday"
            )
        for d in schedule.days:
            if d not in VALID_WEEKDAYS:
                raise ConfigError(
                    f"task '{data['id']}' has invalid weekday {d!r}"
                )
    if schedule.mode == "interval" and schedule.interval_minutes < 1:
        raise ConfigError(
            f"task '{data['id']}' schedule.interval_minutes must be >= 1 "
            f"(got {schedule.interval_minutes})"
        )
    on_fail_data = data.get("on_failure") or {}
    on_failure = OnFailure(
        notify=bool(on_fail_data.get("notify", False)),
    )

    mri_data = data.get("min_rerun_interval") or {}
    try:
        mri_hours = float(mri_data.get("hours", 6.0))
    except (TypeError, ValueError):
        raise ConfigError(
            f"task '{data['id']}': min_rerun_interval.hours must be a number"
        ) from None
    if mri_hours <= 0:
        raise ConfigError(
            f"task '{data['id']}': min_rerun_interval.hours must be > 0 (got {mri_hours})"
        )
    min_rerun = MinRerunInterval(
        enabled=bool(mri_data.get("enabled", True)),
        hours=mri_hours,
    )

    agent = str(data.get("agent", "opencode"))
    quota_condition = _quota_condition_from_dict(data.get("quota_condition"), data["id"], agent)

    if schedule.mode == "quota":
        if quota_condition is None:
            raise ConfigError(
                f"task '{data['id']}': schedule.mode 'quota' needs a quota_condition"
            )
        if not min_rerun.enabled:
            raise ConfigError(
                f"task '{data['id']}': a quota-triggered task must set "
                f"min_rerun_interval.enabled = true"
            )

    return Task(
        id=str(data["id"]),
        name=str(data["name"]),
        enabled=bool(data.get("enabled", True)),
        agent=str(data.get("agent", "opencode")),
        model=(str(data["model"]) if data.get("model") else None),
        prompt_file=str(data.get("prompt_file", "")),
        working_dir=(str(data["working_dir"]) if data.get("working_dir") else None),
        schedule=schedule,
        extra_args=[str(x) for x in data.get("extra_args", [])],
        timeout_minutes=int(data.get("timeout_minutes", 10)),
        on_failure=on_failure,
        notify_artifact=bool(data.get("notify_artifact", False)),
        notify_final_message=bool(data.get("notify_final_message", False)),
        notify_start=bool(data.get("notify_start", False)),
        artifact_dir=(str(data["artifact_dir"]) if data.get("artifact_dir") else None),
        artifact_extensions=[str(x) for x in data.get("artifact_extensions", [".md"])],
        min_rerun_interval=min_rerun,
        quota_condition=quota_condition,
    )


def task_to_dict(task: Task) -> dict[str, Any]:
    return {
        "id": task.id,
        "name": task.name,
        "enabled": task.enabled,
        "agent": task.agent,
        "model": task.model,
        "prompt_file": task.prompt_file,
        "working_dir": task.working_dir,
        "schedule": {
            "mode": task.schedule.mode,
            "days": task.schedule.days,
            "time": task.schedule.time,
            "timezone": task.schedule.timezone,
            "interval_minutes": task.schedule.interval_minutes,
        },
        "extra_args": task.extra_args,
        "timeout_minutes": task.timeout_minutes,
        "on_failure": {"notify": task.on_failure.notify},
        "notify_artifact": task.notify_artifact,
        "notify_final_message": task.notify_final_message,
        "notify_start": task.notify_start,
        "artifact_dir": task.artifact_dir,
        "artifact_extensions": task.artifact_extensions,
        "min_rerun_interval": {
            "enabled": task.min_rerun_interval.enabled,
            "hours": task.min_rerun_interval.hours,
        },
        "quota_condition": (
            None if task.quota_condition is None else _quota_condition_to_dict(task.quota_condition)
        ),
    }


def _quota_condition_to_dict(c: QuotaCondition) -> dict[str, Any]:
    out: dict[str, Any] = {"on_unknown": c.on_unknown}
    for attr, _window, kind, _label in QUOTA_CLAUSES:
        clause: QuotaClause = getattr(c, attr)
        cell: dict[str, Any] = {"enabled": clause.enabled, "value": clause.value}
        if kind == "used":
            cell["op"] = clause.op
        out[attr] = cell
    return out


# ---------------------------------------------------------------------------
# Settings schema
# ---------------------------------------------------------------------------


@dataclass
class Preset:
    name: str
    args: list[str] = field(default_factory=list)


@dataclass
class Bypass:
    scheduled: list[str] = field(default_factory=list)
    manual: list[str] = field(default_factory=list)


@dataclass
class AgentConfig:
    enabled: bool = True
    binary: str = ""
    default_model: str | None = None
    health_cmd: list[str] = field(default_factory=list)
    bypass: Bypass = field(default_factory=Bypass)
    presets: list[Preset] = field(default_factory=list)


@dataclass
class Defaults:
    timeout_minutes: int = 10
    artifact_markers: list[str] = field(default_factory=lambda: ["Saved:", "저장 완료:"])
    artifact_time_window_seconds: int = 30


@dataclass
class QuotaPoll:
    """The periodic quota poller (`mdrunner quota-tick`): refreshes the quota
    snapshot every ``interval_minutes`` and then fires any quota-triggered
    tasks whose condition is now met."""

    enabled: bool = False
    interval_minutes: int = 10
    agents: list[str] = field(
        default_factory=lambda: ["claude", "codex", "agy", "grok"]
    )


@dataclass
class Settings:
    agents: dict[str, AgentConfig] = field(default_factory=dict)
    defaults: Defaults = field(default_factory=Defaults)
    quota_poll: QuotaPoll = field(default_factory=QuotaPoll)


def agent_from_dict(agent_id: str, data: dict[str, Any]) -> AgentConfig:
    bypass_data = data.get("bypass") or {}
    bypass = Bypass(
        scheduled=[str(x) for x in bypass_data.get("scheduled", [])],
        manual=[str(x) for x in bypass_data.get("manual", [])],
    )
    presets = [
        Preset(name=str(p["name"]), args=[str(x) for x in p.get("args", [])])
        for p in data.get("presets", [])
    ]
    return AgentConfig(
        enabled=bool(data.get("enabled", True)),
        binary=str(data.get("binary", agent_id)),
        default_model=(str(data["default_model"]) if data.get("default_model") else None),
        health_cmd=[str(x) for x in data.get("health_cmd", [agent_id, "--version"])],
        bypass=bypass,
        presets=presets,
    )


def agent_to_dict(agent_id: str, agent: AgentConfig) -> dict[str, Any]:
    return {
        "enabled": agent.enabled,
        "binary": agent.binary,
        "default_model": agent.default_model,
        "health_cmd": agent.health_cmd,
        "bypass": {
            "scheduled": agent.bypass.scheduled,
            "manual": agent.bypass.manual,
        },
        "presets": [{"name": p.name, "args": p.args} for p in agent.presets],
    }


def settings_from_dict(data: dict[str, Any]) -> Settings:
    agents_data = data.get("agents") or {}
    if not isinstance(agents_data, dict):
        raise ConfigError("settings.agents must be a mapping")
    agents = {
        agent_id: agent_from_dict(agent_id, cfg)
        for agent_id, cfg in agents_data.items()
    }
    defaults_data = data.get("defaults") or {}
    defaults = Defaults(
        timeout_minutes=int(defaults_data.get("timeout_minutes", 10)),
        artifact_markers=[str(x) for x in defaults_data.get("artifact_markers", ["Saved:", "저장 완료:"])],
        artifact_time_window_seconds=int(defaults_data.get("artifact_time_window_seconds", 30)),
    )
    qp_data = data.get("quota_poll") or {}
    quota_poll = QuotaPoll(
        enabled=bool(qp_data.get("enabled", False)),
        interval_minutes=int(qp_data.get("interval_minutes", 10)),
        agents=[str(x) for x in qp_data.get("agents", ["claude", "codex", "agy", "grok"])],
    )
    return Settings(agents=agents, defaults=defaults, quota_poll=quota_poll)


def settings_to_dict(settings: Settings) -> dict[str, Any]:
    return {
        "agents": {
            agent_id: agent_to_dict(agent_id, cfg)
            for agent_id, cfg in settings.agents.items()
        },
        "defaults": {
            "timeout_minutes": settings.defaults.timeout_minutes,
            "artifact_markers": settings.defaults.artifact_markers,
            "artifact_time_window_seconds": settings.defaults.artifact_time_window_seconds,
        },
        "quota_poll": {
            "enabled": settings.quota_poll.enabled,
            "interval_minutes": settings.quota_poll.interval_minutes,
            "agents": settings.quota_poll.agents,
        },
    }


# ---------------------------------------------------------------------------
# Loaders / savers
# ---------------------------------------------------------------------------


def load_yaml(path: Path | str) -> dict[str, Any]:
    p = Path(path) if path is not None else None
    if p is None or not p.exists():
        return {}
    with p.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ConfigError(f"{p}: top level must be a mapping")
    return data


def save_yaml(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(
            data,
            fh,
            sort_keys=False,
            allow_unicode=True,
            default_flow_style=False,
        )


def load_tasks(path: Path) -> list[Task]:
    data = load_yaml(path)
    raw = data.get("tasks") or []
    if not isinstance(raw, list):
        raise ConfigError(f"{path}: 'tasks' must be a list")
    return [task_from_dict(item) for item in raw]


def save_tasks(path: Path, tasks: list[Task]) -> None:
    save_yaml(path, {"tasks": [task_to_dict(t) for t in tasks]})


def load_settings(path: Path) -> Settings:
    return settings_from_dict(load_yaml(path))


def save_settings(path: Path, settings: Settings) -> None:
    save_yaml(path, settings_to_dict(settings))


def find_task(tasks: list[Task], task_id: str) -> Task:
    for t in tasks:
        if t.id == task_id:
            return t
    raise KeyError(f"no task with id={task_id!r}")
