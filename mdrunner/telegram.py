"""Telegram bot notifications.

Phase 4.1: only failure notifications wired up. Optional/quiet by default
so this doesn't surprise users with surprise messages.
"""

from __future__ import annotations

import urllib.parse
import urllib.request
from typing import Any


def send(
    *,
    bot_token: str,
    chat_id: str,
    text: str,
    timeout: float = 5.0,
) -> tuple[bool, str]:
    """Send a text message via Telegram Bot API.

    Returns (ok, error_or_response). No-ops gracefully if bot_token or chat_id
    is missing.
    """
    if not bot_token or not chat_id:
        return False, "telegram bot_token / chat_id not configured"
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    data = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return True, body[:300]
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def format_failure(task_name: str, result: dict[str, Any]) -> str:
    """Standardize the failure message body sent to Telegram."""
    duration = result.get("duration", 0.0)
    exit_code = result.get("exit_code")
    error = result.get("error")
    saved = result.get("saved_file")
    parts = [f"mdrunner: '{task_name}' failed", f"duration: {duration:.1f}s"]
    if exit_code is not None:
        parts.append(f"exit_code: {exit_code}")
    if error:
        parts.append(f"error: {error}")
    if saved:
        parts.append(f"last_save: {saved}")
    return "\n".join(parts)