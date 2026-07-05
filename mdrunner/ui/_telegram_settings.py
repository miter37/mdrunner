"""Telegram settings sidecar — separate from main settings.yaml.

Stored in the same config directory as a small JSON file so the user can
manage their bot token without editing YAML. Currently informational —
Phase 4.1 wires the actual send.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..utils.paths import config_dir


_FILE_NAME = "telegram.json"


def path() -> Path:
    return config_dir() / _FILE_NAME


def load() -> dict[str, Any]:
    p = path()
    if not p.exists():
        return {"bot_token": "", "chat_id": "", "notify_on_failure": True}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"bot_token": "", "chat_id": "", "notify_on_failure": True}


def save(data: dict[str, Any]) -> None:
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")