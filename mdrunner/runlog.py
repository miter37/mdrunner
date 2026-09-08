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

_SESSION_LINE = re.compile(r"(?i)\bsession[ _-]?id\s*[:=]\s*([A-Za-z0-9._/-]{8,})")
_START_LINE = re.compile(r"^===== mdrunner start (\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")
_SKIP_LINE = re.compile(r"^===== mdrunner skip (\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")


def prune_log(path: Path, *, keep_hours: float = 24.0) -> int:
    """Drop run blocks older than ``keep_hours`` from a task log file.

    A block starts at a ``===== mdrunner start <ts> =====`` line (or the
    ``===== mdrunner skip <ts>`` one-liner, which is also dated) and runs to
    the next such line. Anything without a parseable timestamp is kept.
    Returns the number of blocks removed. Never raises.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return 0
    cutoff = time.time() - keep_hours * 3600.0
    lines = text.splitlines(keepends=True)
    # (start_index, start_epoch or None) for every dated block boundary
    bounds: list[tuple[int, float | None]] = []
    for i, line in enumerate(lines):
        m = _START_LINE.match(line.strip()) or _SKIP_LINE.match(line.strip())
        if not m:
            continue
        try:
            ts = time.mktime(time.strptime(m.group(1), "%Y-%m-%d %H:%M:%S"))
        except (ValueError, OverflowError):
            ts = None
        bounds.append((i, ts))
    if not bounds:
        return 0
    keep_from = len(lines)
    dropped = 0
    for idx, (start, ts) in enumerate(bounds):
        if ts is not None and ts < cutoff:
            dropped += 1
        else:
            keep_from = min(keep_from, start)
            break
    if keep_from <= 0 or dropped == 0:
        return 0
    try:
        path.write_text("".join(lines[keep_from:]), encoding="utf-8")
    except OSError:
        return 0
    return dropped


_ERR_LINE = re.compile(r"(?i)^\s*(?:error|failed|fatal|exception)\s*:\s*(.+?)\s*$")


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
