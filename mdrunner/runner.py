"""Subprocess runner — executes one task end-to-end.

Responsibilities:
    1. Acquire an exclusive lock (sentinel file) so the same task can't
       run twice in parallel.
    2. Resolve the agent adapter + build the argv.
    3. Spawn the agent subprocess, capture stdout/stderr, tee to a log file.
    4. Enforce timeout (kill the process tree on expiry).
    5. Return a RunResult that the CLI and (later) the GUI consume.

Cross-platform lock: we use an atomic create (O_CREAT|O_EXCL) sentinel
file. It's not as robust as fcntl.flock on Linux but it works on Windows
too without extra dependencies.
"""

from __future__ import annotations

import errno
import os
import shlex
import signal
import subprocess
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

from .agents import get_adapter, resolve_binary
from .config import (
    Defaults,
    Settings,
    Task,
    find_task,
    load_settings,
    load_tasks,
)
from .health import probe_health
from .utils.paths import lock_file, task_log_file


# ---------------------------------------------------------------------------
# Lock
# ---------------------------------------------------------------------------


@contextmanager
def task_lock(task_id: str) -> Iterator[Path]:
    """Acquire an exclusive lock for the given task id.

    Raises LockBusyError if another mdrunner process already holds the lock.
    """
    lf = lock_file(task_id)
    lf.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(lf), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except OSError as exc:
        if exc.errno == errno.EEXIST:
            raise LockBusyError(f"task {task_id!r} is already running (lock={lf})")
        raise
    try:
        os.write(fd, f"{os.getpid()}\n".encode())
        os.fsync(fd)
        yield lf
    finally:
        os.close(fd)
        try:
            lf.unlink()
        except FileNotFoundError:
            pass


class LockBusyError(RuntimeError):
    """Raised when another mdrunner instance already holds the task lock."""


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class RunResult:
    task_id: str
    started_at: float
    finished_at: float
    exit_code: int | None
    timed_out: bool
    binary_path: str | None
    argv: list[str] = field(default_factory=list)
    log_file: str | None = None
    saved_file: str | None = None  # parsed from "저장 완료:" line if present
    error: str | None = None

    @property
    def duration_seconds(self) -> float:
        return max(0.0, self.finished_at - self.started_at)

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out and self.error is None


# ---------------------------------------------------------------------------
# Saved-file detection
# ---------------------------------------------------------------------------


_SAVED_MARKER_KO = "저장 완료:"
_SAVED_MARKER_EN = "Saved:"


def detect_saved_file(text: str) -> str | None:
    """Return the LAST saved-file path found in the agent output, if any.

    The most recent save marker is almost always the real artifact, since
    agents that emit multiple saves along the way produce the final one last.
    """
    last: str | None = None
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        # Common forms observed across md-driven skills:
        #   "저장 완료: `/path/to/file.md`"
        #   "> 저장 완료: `/path/to/file.md`"
        #   "Saved: /path/to/file.md"
        for marker in (_SAVED_MARKER_KO, _SAVED_MARKER_EN):
            if marker in s:
                tail = s.split(marker, 1)[1].strip()
                # strip surrounding quotes / angle brackets / backticks
                tail = tail.strip("`\"'<> ")
                # extract first plausible absolute path
                for token in tail.split():
                    if token.startswith("/") or (len(token) > 2 and token[1] == ":" and token[2] == "\\"):
                        last = token
                        break
                break  # one marker per line
    return last


# ---------------------------------------------------------------------------
# Process termination (cross-platform)
# ---------------------------------------------------------------------------


def _terminate_process_tree(proc: subprocess.Popen) -> None:
    """Best-effort termination of the subprocess + descendants."""
    if proc.poll() is not None:
        return
    if sys.platform == "win32":
        try:
            proc.terminate()
        except OSError:
            pass
    else:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                proc.terminate()
            except OSError:
                pass


def _kill_process_tree(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    if sys.platform == "win32":
        try:
            proc.kill()
        except OSError:
            pass
    else:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                proc.kill()
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run_task(
    task_id: str,
    *,
    mode: str = "manual",
    settings: Settings | None = None,
    tasks_file: Path | None = None,
    settings_file: Path | None = None,
    timeout_override: int | None = None,
) -> RunResult:
    """Execute one task by id. Returns a RunResult.

    mode: "manual" | "scheduled" — selects which bypass flag set from settings.
    """
    if mode not in ("manual", "scheduled"):
        raise ValueError(f"mode must be 'manual' or 'scheduled', got {mode!r}")

    if settings is None:
        settings = load_settings(None if settings_file is None else str(settings_file))
    if tasks_file is None:
        from .utils.paths import tasks_file as _tasks_file
        tasks_path = _tasks_file()
    else:
        tasks_path = tasks_file
    tasks = load_tasks(tasks_path)
    task = find_task(tasks, task_id)

    if not task.enabled:
        return RunResult(
            task_id=task_id,
            started_at=time.time(),
            finished_at=time.time(),
            exit_code=None,
            timed_out=False,
            binary_path=None,
            argv=[],
            error=f"task {task_id!r} is disabled (enabled=false)",
        )

    agent_cfg = settings.agents.get(task.agent)
    if agent_cfg is None:
        return RunResult(
            task_id=task_id,
            started_at=time.time(),
            finished_at=time.time(),
            exit_code=None,
            timed_out=False,
            binary_path=None,
            argv=[],
            error=f"no settings for agent {task.agent!r} (add it to settings.yaml)",
        )

    return _execute(task, agent_cfg, settings.defaults, mode, timeout_override)


def _execute(
    task: Task,
    agent_cfg,
    defaults: Defaults,
    mode: str,
    timeout_override: int | None,
) -> RunResult:
    binary_path = resolve_binary(agent_cfg.binary)
    if binary_path is None:
        return RunResult(
            task_id=task.id,
            started_at=time.time(),
            finished_at=time.time(),
            exit_code=None,
            timed_out=False,
            binary_path=None,
            argv=[],
            error=f"agent binary {agent_cfg.binary!r} not found on PATH",
        )

    prompt_file = Path(task.prompt_file).expanduser()
    if not prompt_file.exists():
        return RunResult(
            task_id=task.id,
            started_at=time.time(),
            finished_at=time.time(),
            exit_code=None,
            timed_out=False,
            binary_path=binary_path,
            argv=[],
            error=f"prompt file not found: {prompt_file}",
        )

    adapter = get_adapter(task.agent)
    bypass = agent_cfg.bypass.scheduled if mode == "scheduled" else agent_cfg.bypass.manual
    model = task.model or agent_cfg.default_model

    working_dir = Path(task.working_dir).expanduser() if task.working_dir else None
    if working_dir and not working_dir.exists():
        return RunResult(
            task_id=task.id,
            started_at=time.time(),
            finished_at=time.time(),
            exit_code=None,
            timed_out=False,
            binary_path=binary_path,
            argv=[],
            error=f"working_dir not found: {working_dir}",
        )

    argv_result = adapter.build_argv(
        prompt_file,
        model=model,
        working_dir=working_dir,
        extra_args=task.extra_args,
        bypass_flags=bypass,
    )
    argv = argv_result.argv
    cwd = argv_result.cwd or working_dir

    timeout_minutes = timeout_override if timeout_override is not None else task.timeout_minutes
    if timeout_minutes <= 0:
        timeout_seconds = None  # no limit
    else:
        timeout_seconds = timeout_minutes * 60

    log_path = task_log_file(task.id)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    started = time.time()
    accumulated_text: list[str] = []
    saved_file: str | None = None
    timed_out = False
    exit_code: int | None = None
    error: str | None = None

    try:
        with task_lock(task.id):
            with log_path.open("a", encoding="utf-8") as log_fh:
                log_fh.write(
                    f"\n===== mdrunner start {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(started))} "
                    f"task={task.id} mode={mode} agent={task.agent} model={model or '-'} =====\n"
                )
                log_fh.write(f"$ {' '.join(shlex.quote(a) for a in argv)}\n")
                log_fh.flush()

                proc = subprocess.Popen(
                    argv,
                    cwd=str(cwd) if cwd else None,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    env=os.environ.copy(),
                    start_new_session=(sys.platform != "win32"),
                )

                deadline = None if timeout_seconds is None else started + timeout_seconds
                try:
                    assert proc.stdout is not None
                    for line in proc.stdout:
                        log_fh.write(line)
                        log_fh.flush()
                        accumulated_text.append(line)
                        # Streaming save-marker scan: cheap, but we only look at
                        # the last ~4 KB so the cost stays bounded for long runs.
                        # After the run finishes we do a full scan below.
                        recent_window = "".join(accumulated_text[-200:])
                        saved_file = detect_saved_file(recent_window)
                        if deadline is not None and time.time() > deadline:
                            timed_out = True
                            _terminate_process_tree(proc)
                            try:
                                proc.wait(timeout=5)
                            except subprocess.TimeoutExpired:
                                _kill_process_tree(proc)
                                proc.wait(timeout=5)
                            break
                    exit_code = proc.wait()
                except KeyboardInterrupt:
                    _terminate_process_tree(proc)
                    proc.wait(timeout=5)
                    error = "interrupted"
                finally:
                    if proc.poll() is None:
                        _terminate_process_tree(proc)
                        proc.wait(timeout=5)
    except LockBusyError as exc:
        return RunResult(
            task_id=task.id,
            started_at=started,
            finished_at=time.time(),
            exit_code=None,
            timed_out=False,
            binary_path=binary_path,
            argv=argv,
            log_file=str(log_path),
            error=str(exc),
        )

    finished = time.time()
    if saved_file is None:
        saved_file = detect_saved_file("".join(accumulated_text))
    return RunResult(
        task_id=task.id,
        started_at=started,
        finished_at=finished,
        exit_code=exit_code,
        timed_out=timed_out,
        binary_path=binary_path,
        argv=argv,
        log_file=str(log_path),
        saved_file=saved_file,
        error=error,
    )


# ---------------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------------


def preview_task(
    task_id: str,
    *,
    mode: str = "manual",
    settings: Settings | None = None,
    tasks_file: Path | None = None,
    settings_file: Path | None = None,
) -> dict:
    """Return a dict describing how the task would be invoked, without running."""
    if settings is None:
        settings = load_settings(None if settings_file is None else str(settings_file))
    if tasks_file is None:
        from .utils.paths import tasks_file as _tasks_file
        tasks_path = _tasks_file()
    else:
        tasks_path = tasks_file
    tasks = load_tasks(tasks_path)
    task = find_task(tasks, task_id)
    agent_cfg = settings.agents.get(task.agent)
    if agent_cfg is None:
        raise KeyError(f"no settings for agent {task.agent!r}")
    adapter = get_adapter(task.agent)
    bypass = agent_cfg.bypass.scheduled if mode == "scheduled" else agent_cfg.bypass.manual
    model = task.model or agent_cfg.default_model
    working_dir = Path(task.working_dir).expanduser() if task.working_dir else None
    argv_result = adapter.build_argv(
        Path(task.prompt_file).expanduser(),
        model=model,
        working_dir=working_dir,
        extra_args=task.extra_args,
        bypass_flags=bypass,
    )
    return {
        "task_id": task.id,
        "mode": mode,
        "agent": task.agent,
        "binary": agent_cfg.binary,
        "binary_path": resolve_binary(agent_cfg.binary),
        "model": model,
        "bypass_flags": list(bypass),
        "extra_args": list(task.extra_args),
        "cwd": str(argv_result.cwd or working_dir) if (argv_result.cwd or working_dir) else None,
        "argv": argv_result.argv,
        "argv_quoted": [shlex.quote(a) for a in argv_result.argv],
        "timeout_minutes": task.timeout_minutes,
    }


# ---------------------------------------------------------------------------
# Health summary across all configured agents
# ---------------------------------------------------------------------------


def health_summary(settings: Settings) -> list[dict]:
    rows = []
    for agent_id, cfg in settings.agents.items():
        result = probe_health(cfg.binary, cfg.health_cmd)
        rows.append(
            {
                "agent": agent_id,
                "binary": cfg.binary,
                "binary_path": result.binary_path,
                "ok": result.ok,
                "version": result.version,
                "error": result.error,
                "bypass_risk": _bypass_risk(cfg.bypass.scheduled + cfg.bypass.manual),
            }
        )
    return rows


def _bypass_risk(flags) -> str:
    # local import to avoid circular dependency at module load
    from .health import bypass_risk_level

    return bypass_risk_level(flags)