"""Structured per-run log headers/footers.

The agent CLIs often inline the whole prompt on argv (``agy -p '...'``),
which used to dump thousands of prompt lines into the task log and bury
the session id and the actual failure line. Headers stay short; the
prompt lives in ``prompt_file``.
"""

from __future__ import annotations

import re
import shlex
import time
import uuid
from pathlib import Path

_SESSION_LINE = re.compile(
    r"(?i)\bsession[ _-]?id\s*[:=]\s*([A-Za-z0-9._/-]{8,})"
)
_ERR_LINE = re.compile(
    r"(?i)^\s*(?:error|failed|fatal|exception)\s*:\s*(.+?)\s*$"
)


def new_run_id(when: float | None = None) -> str:
    ts = time.strftime("%Y%m%d_%H%M%S", time.localtime(when or time.time()))
    return f"{ts}_{uuid.uuid4().hex[:6]}"


def argv_for_log(
    argv: list[str],
    prompt_file: Path | None,
    prompt_text: str | None,
) -> str:
    name = prompt_file.name if prompt_file is not None else "prompt"
    out: list[str] = []
    for tok in argv:
        if prompt_text and tok == prompt_text:
            out.append(f"<prompt:{name} {len(tok)}c>")
        elif len(tok) > 240:
            out.append(shlex.quote(tok[:80] + f"...<{len(tok)}c>"))
        else:
            out.append(shlex.quote(tok))
    return " ".join(out)


def parse_agent_session(line: str) -> str | None:
    m = _SESSION_LINE.search(line or "")
    if not m:
        return None
    val = m.group(1).rstrip(".,;)]}")
    if val.lower() in {"used", "week", "reset", "resets"}:
        return None
    return val


def infer_failure_reason(
    stdout: str,
    *,
    exit_code: int | None,
    timed_out: bool,
    error: str | None,
) -> str | None:
    if error:
        return error
    if timed_out:
        return "mdrunner timeout"
    last_err = None
    last_any = None
    for raw in (stdout or "").splitlines():
        s = raw.strip()
        if not s:
            continue
        last_any = s
        m = _ERR_LINE.match(s)
        if m:
            last_err = m.group(1).strip()
    if last_err:
        return last_err[:400]
    if exit_code not in (0, None) and last_any and len(last_any) <= 400:
        return last_any
    if exit_code not in (0, None):
        return f"exit_code={exit_code}"
    return None


def format_start(
    *,
    when: float,
    run_id: str,
    task_id: str,
    agent: str,
    model: str | None,
    mode: str,
    cwd: str | None,
    timeout_minutes: int,
    prompt_file: Path | None,
    prompt_chars: int,
    argv_line: str,
) -> str:
    ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(when))
    timeout = "none" if timeout_minutes <= 0 else f"{timeout_minutes}m"
    prompt = str(prompt_file) if prompt_file is not None else "-"
    lines = [
        f"===== mdrunner start {ts} =====",
        f"run={run_id}",
        f"task={task_id}",
        f"agent={agent}",
        f"model={model or '-'}",
        f"mode={mode}",
        f"cwd={cwd or '-'}",
        f"timeout={timeout}",
        f"prompt={prompt} ({prompt_chars} chars)",
        f"argv={argv_line}",
        "=====",
        "",
    ]
    return "\n".join(lines)


def format_end(
    *,
    when: float,
    run_id: str,
    task_id: str,
    agent: str,
    agent_session: str | None,
    ok: bool,
    exit_code: int | None,
    timed_out: bool,
    duration_s: float,
    error: str | None,
) -> str:
    ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(when))
    status = "ok" if ok else "fail"
    if timed_out:
        status = "timeout"
    err = error or "-"
    lines = [
        "",
        f"===== mdrunner end {ts} =====",
        f"run={run_id}",
        f"task={task_id}",
        f"agent={agent}",
        f"agent_session={agent_session or '-'}",
        f"status={status}",
        f"exit_code={exit_code if exit_code is not None else '-'}",
        f"timed_out={'true' if timed_out else 'false'}",
        f"duration={duration_s:.1f}s",
        f"error={err}",
        "=====",
        "",
    ]
    return "\n".join(lines)
