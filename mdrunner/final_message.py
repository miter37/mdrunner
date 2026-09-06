"""Pull the agent's last user-facing reply out of a run's stdout.

CLI agents mix progress chatter, tool traces, and the actual answer on one
stream. This is best-effort: prefer structured JSON/JSONL when present,
otherwise drop leading status lines and trailing ``Saved:`` / mdrunner
bookkeeping, and keep the remaining block.
"""

from __future__ import annotations

import json
import re

_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_TRAILING = re.compile(
    r"^(?:Saved:\s+\S+|->{0,1}\s*Saved\b.*|\[mdrunner\].*)$",
    re.I,
)
_PROGRESS = re.compile(
    r"^(?:"
    r"I (?:will|have|am|'ll|'m|just)|"
    r"I'll |I'm |Let me |"
    r"The pipeline|"
    r"Waiting |Running |Thinking |"
    r"Working on|Tool call|Calling |"
    r"✔|✓|●|[⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏]"
    r")",
    re.I,
)
_CJK = re.compile(r"[\u3131-\uD7A3\u4e00-\u9fff]")
TELEGRAM_LIMIT = 3900  # sendMessage cap is 4096; leave room for a header


def extract_final_message(text: str) -> str | None:
    if not text or not str(text).strip():
        return None
    text = _ANSI.sub("", text)
    from_json = _last_jsonl_text(text)
    if from_json:
        return from_json
    lines = text.splitlines()
    while lines and (not lines[-1].strip() or _TRAILING.match(lines[-1].strip())):
        lines.pop()
    i = 0
    while i < len(lines) and (not lines[i].strip() or _is_progress(lines[i])):
        i += 1
    body = "\n".join(lines[i:]).strip()
    return body or None


def split_telegram_chunks(body: str, limit: int = TELEGRAM_LIMIT) -> list[str]:
    body = (body or "").strip()
    if not body:
        return []
    if len(body) <= limit:
        return [body]
    chunks: list[str] = []
    rest = body
    while rest:
        if len(rest) <= limit:
            chunks.append(rest)
            break
        cut = rest.rfind("\n", 0, limit)
        if cut < limit // 3:
            cut = limit
        chunks.append(rest[:cut].rstrip())
        rest = rest[cut:].lstrip()
    return [c for c in chunks if c]


def format_final_message(task_name: str, body: str, *, part: int = 1, parts: int = 1) -> str:
    head = f"mdrunner: '{task_name}' 완료"
    if parts > 1:
        head += f" ({part}/{parts})"
    return head + "\n\n" + body


def _is_progress(line: str) -> bool:
    s = line.strip()
    if not s or _TRAILING.match(s):
        return True
    if _PROGRESS.match(s):
        return True
    if (
        len(s) < 140
        and s.endswith((".", "..."))
        and not _CJK.search(s)
        and not s.startswith(("#", "-", "*", "["))
        and s[0].isupper()
    ):
        return True
    return False


def _json_text(obj: object) -> str | None:
    if not isinstance(obj, dict):
        return None
    for key in ("result", "message", "text"):
        val = obj.get(key)
        if isinstance(val, str) and val.strip() and key != "type":
            return val.strip()
    item = obj.get("item")
    if isinstance(item, dict):
        inner = _json_text(item)
        if inner:
            return inner
    content = obj.get("content")
    if isinstance(content, str) and content.strip():
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for chunk in content:
            if isinstance(chunk, str) and chunk.strip():
                parts.append(chunk.strip())
            elif isinstance(chunk, dict):
                t = chunk.get("text") or chunk.get("content")
                if isinstance(t, str) and t.strip():
                    parts.append(t.strip())
        if parts:
            return "\n".join(parts)
    return None


def _last_jsonl_text(text: str) -> str | None:
    last: str | None = None
    json_lines = 0
    other = 0
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if not line.startswith("{"):
            other += 1
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            other += 1
            continue
        json_lines += 1
        extracted = _json_text(obj)
        if extracted:
            last = extracted
    if last and json_lines >= 1 and json_lines >= other:
        return last
    return None
