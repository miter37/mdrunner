"""Configuration schemas and loaders for mdrunner.

Two YAML files live in the config directory:

- tasks.yaml — list of task definitions
- settings.yaml — per-agent CLI config + global defaults

Both are loaded into typed dataclasses for ergonomic access, but kept
trivially serializable so the GUI can round-trip them through PyYAML.
"""

from __future__ import annotations

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
VALID_SCHEDULE_MODES = {"once", "daily", "weekly", "interval"}


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
    artifact_dir: str | None = None
    artifact_extensions: list[str] = field(default_factory=lambda: [".md"])


def task_from_dict(data: dict[str, Any]) -> Task:
    if not isinstance(data, dict):
        raise ConfigError(f"task must be a mapping, got {type(data).__name__}")
    if "id" not in data or "name" not in data:
        raise ConfigError("each task needs 'id' and 'name'")
    sched_data = data.get("schedule") or {}
    if not isinstance(sched_data, dict):
        raise ConfigError(f"task.schedule must be a mapping (got {type(sched_data).__name__})")
    schedule = Schedule(
        mode=str(sched_data.get("mode", "weekly")),
        days=[str(d).lower() for d in sched_data.get("days", [])],
        time=str(sched_data.get("time", "07:00")),
        timezone=str(sched_data.get("timezone", "Asia/Seoul")),
        interval_minutes=int(sched_data.get("interval_minutes", 60)),
    )
    if schedule.mode not in VALID_SCHEDULE_MODES:
        raise ConfigError(
            f"task '{data['id']}' has invalid schedule.mode={schedule.mode!r}"
        )
    if schedule.mode == "weekly":
        for d in schedule.days:
            if d not in VALID_WEEKDAYS:
                raise ConfigError(
                    f"task '{data['id']}' has invalid weekday {d!r}"
                )
    on_fail_data = data.get("on_failure") or {}
    on_failure = OnFailure(
        notify=bool(on_fail_data.get("notify", False)),
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
        artifact_dir=(str(data["artifact_dir"]) if data.get("artifact_dir") else None),
        artifact_extensions=[str(x) for x in data.get("artifact_extensions", [".md"])],
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
        "artifact_dir": task.artifact_dir,
        "artifact_extensions": task.artifact_extensions,
    }


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
    """Settings for the periodic agent-quota poller (systemd user timer)."""

    enabled: bool = False
    interval_minutes: int = 180
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
        interval_minutes=int(qp_data.get("interval_minutes", 180)),
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