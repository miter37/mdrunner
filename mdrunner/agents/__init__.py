"""Adapter registry — instantiates adapters by id.

Adding a new agent:

1. Create `mdrunner/agents/<name>.py` with a class inheriting `AgentAdapter`.
2. Import it here and add it to `ADAPTERS`.
3. (Optional) Add a default block to `default_settings()` if you want it
   pre-seeded when settings.yaml is missing.
"""

from __future__ import annotations

from .agy import AgyAdapter
from .base import (
    AgentAdapter,
    ArgvResult,
    resolve_binary,
    wrap_for_windows,  # re-export
)
from .claude import ClaudeAdapter
from .codex import CodexAdapter
from .grok import GrokAdapter
from .hermes import HermesAdapter
from .openclaw import OpenClawAdapter
from .opencode import OpenCodeAdapter

__all__ = [
    "AgentAdapter",
    "ArgvResult",
    "ADAPTERS",
    "get_adapter",
    "resolve_binary",
    "wrap_for_windows",
]


ADAPTERS: dict[str, AgentAdapter] = {
    OpenCodeAdapter.id: OpenCodeAdapter(),
    CodexAdapter.id: CodexAdapter(),
    OpenClawAdapter.id: OpenClawAdapter(),
    ClaudeAdapter.id: ClaudeAdapter(),
    AgyAdapter.id: AgyAdapter(),
    HermesAdapter.id: HermesAdapter(),
    GrokAdapter.id: GrokAdapter(),
}


def get_adapter(agent_id: str) -> AgentAdapter:
    if agent_id not in ADAPTERS:
        raise KeyError(
            f"unknown agent {agent_id!r}; registered: {sorted(ADAPTERS)}"
        )
    return ADAPTERS[agent_id]


def default_settings() -> dict:
    """Return the seed settings.yaml body when no file exists yet."""
    return {
        "agents": {
            "opencode": {
                "enabled": True,
                "binary": "opencode",
                "default_model": "minimax-coding-plan/MiniMax-M3",
                "health_cmd": ["opencode", "--version"],
                "bypass": {
                    "scheduled": ["--auto"],
                    "manual": [],
                },
                "presets": [
                    {"name": "auto-approve", "args": ["--auto"]},
                    {"name": "read-only", "args": []},
                ],
            },
            "codex": {
                "enabled": True,
                "binary": "codex",
                "default_model": "gpt-5.4",
                "health_cmd": ["codex", "--version"],
                "bypass": {
                    "scheduled": ["--yolo"],
                    "manual": [],
                },
                "presets": [
                    {"name": "workspace-write", "args": ["--sandbox", "workspace-write"]},
                    {
                        "name": "danger-full",
                        "args": ["--sandbox", "danger-full-access", "--yolo"],
                    },
                ],
            },
            "openclaw": {
                "enabled": True,
                "binary": "openclaw",
                "default_model": "minimax-coding-plan/MiniMax-M3",
                "health_cmd": ["openclaw", "--version"],
                "bypass": {
                    "scheduled": ["--local"],
                    "manual": [],
                },
                "presets": [
                    {"name": "local-only", "args": ["--local"]},
                ],
            },
            "claude": {
                "enabled": True,
                "binary": "claude",
                "default_model": None,
                "health_cmd": ["claude", "--version"],
                "bypass": {
                    "scheduled": ["--dangerously-skip-permissions"],
                    "manual": [],
                },
                "presets": [
                    {"name": "auto-approve", "args": ["--dangerously-skip-permissions"]},
                    {"name": "read-only", "args": []},
                ],
            },
            "agy": {
                "enabled": True,
                "binary": "agy",
                "default_model": "Gemini 3.5 Flash (Medium)",
                "health_cmd": ["agy", "--version"],
                "bypass": {
                    "scheduled": ["--dangerously-skip-permissions"],
                    "manual": [],
                },
                "presets": [
                    {"name": "auto-approve", "args": ["--dangerously-skip-permissions"]},
                    {"name": "read-only", "args": []},
                ],
            },
            "hermes": {
                "enabled": True,
                "binary": "hermes",
                "default_model": None,
                "health_cmd": ["hermes", "--version"],
                "bypass": {
                    "scheduled": ["--yolo"],
                    "manual": [],
                },
                "presets": [
                    {"name": "auto-approve", "args": ["--yolo"]},
                    {"name": "read-only", "args": []},
                ],
            },
            "grok": {
                "enabled": True,
                "binary": "grok",
                "default_model": None,
                "health_cmd": ["grok", "--version"],
                "bypass": {
                    "scheduled": ["--permission-mode", "bypassPermissions"],
                    "manual": [],
                },
                "presets": [
                    {
                        "name": "auto-approve",
                        "args": ["--permission-mode", "bypassPermissions"],
                    },
                    {"name": "plan-only", "args": ["--permission-mode", "plan"]},
                    {"name": "read-only", "args": []},
                ],
            },
        },
        "defaults": {"timeout_minutes": 10},
    }