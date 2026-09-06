"""Telegram bot notifications.

Phase 4.1: only failure notifications wired up. Optional/quiet by default
so this doesn't surprise users with surprise messages.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from urllib.error import HTTPError
from typing import Any


def _decode_api_response(body: str) -> tuple[bool, str]:
    """Validate Telegram's JSON-level success flag and return useful detail."""
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return False, f"invalid Telegram API response: {body[:300]}"
    if payload.get("ok") is True:
        return True, body[:300]
    description = payload.get("description") or "Telegram API returned ok=false"
    error_code = payload.get("error_code")
    suffix = f" (HTTP/API error {error_code})" if error_code is not None else ""
    return False, f"{description}{suffix}"


def _http_error_detail(exc: HTTPError) -> str:
    try:
        body = exc.read().decode("utf-8", errors="replace")
        ok, detail = _decode_api_response(body)
        return detail if not ok else f"HTTP {exc.code}: {detail}"
    except Exception:  # noqa: BLE001
        return f"HTTP {exc.code}: {exc.reason}"


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
            return _decode_api_response(body)
    except HTTPError as exc:
        return False, _http_error_detail(exc)
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


def send_document(
    *,
    bot_token: str,
    chat_id: str,
    file_path: str,
    caption: str | None = None,
    timeout: float = 10.0,
) -> tuple[bool, str]:
    """Send a document file via Telegram Bot API.

    Returns (ok, error_or_response). No-ops gracefully if bot_token or chat_id
    is missing, or if the file does not exist.
    """
    import uuid
    import mimetypes
    from pathlib import Path
    import urllib.request

    if not bot_token or not chat_id:
        return False, "telegram bot_token / chat_id not configured"

    path = Path(file_path)
    if not path.exists():
        return False, f"file not found: {file_path}"

    url = f"https://api.telegram.org/bot{bot_token}/sendDocument"
    boundary = f"----WebKitFormBoundary{uuid.uuid4().hex}"

    mime_type, _ = mimetypes.guess_type(str(path))
    mime_type = mime_type or "application/octet-stream"

    parts = []
    # chat_id
    parts.append(f"--{boundary}")
    parts.append('Content-Disposition: form-data; name="chat_id"')
    parts.append('')
    parts.append(chat_id)

    # caption
    if caption:
        parts.append(f"--{boundary}")
        parts.append('Content-Disposition: form-data; name="caption"')
        parts.append('')
        parts.append(caption)

    # document file
    parts.append(f"--{boundary}")
    parts.append(f'Content-Disposition: form-data; name="document"; filename="{path.name}"')
    parts.append(f'Content-Type: {mime_type}')
    parts.append('')

    header_bytes = "\r\n".join(parts).encode("utf-8") + b"\r\n"
    with path.open("rb") as f:
        file_bytes = f.read()
    footer_bytes = f"\r\n--{boundary}--\r\n".encode("utf-8")

    body = header_bytes + file_bytes + footer_bytes

    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    req.add_header("Content-Length", str(len(body)))

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            res_body = resp.read().decode("utf-8", errors="replace")
            return _decode_api_response(res_body)
    except HTTPError as exc:
        return False, _http_error_detail(exc)
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)
