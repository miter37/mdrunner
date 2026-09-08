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
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

from .agents import get_adapter, resolve_binary, wrap_for_windows
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

# On Windows, TerminateProcess (SIGKILL) does not guarantee child processes
# are reaped immediately; give the process tree a moment to actually die.
TIMEOUT_GRACE_SECONDS = 3.0


def _wait_for_exit(proc: subprocess.Popen, timeout: float) -> None:
    """Wait up to ``timeout`` seconds for ``proc`` to exit, polling gently."""
    deadline = time.time() + timeout
    while proc.poll() is None and time.time() < deadline:
        time.sleep(0.25)


# ---------------------------------------------------------------------------
# Lock
# ---------------------------------------------------------------------------


# Locks older than this are eligible for stale-lock cleanup, but only when
# the recorded PID no longer exists (or the file is unparseable legacy data).
# A live PID always counts as busy, no matter how old the lock is.
STALE_LOCK_SECONDS = 30 * 60


def _pid_alive(pid: int) -> bool:
    """True if a process with ``pid`` still exists (best-effort, no deps)."""
    if pid == os.getpid():
        return True
    if pid <= 0:
        return False
    if sys.platform == "win32":
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
            if not handle:
                return False
            kernel32.CloseHandle(handle)
            return True
        except Exception:  # noqa: BLE001 — conservative: assume alive
            return True
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except (PermissionError, OSError):
        return True  # exists but owned by someone else


def _remove_if_stale(lf: Path) -> bool:
    """Unlink ``lf`` when it is a stale lock. Returns True if removed."""
    now = time.time()
    try:
        raw = lf.read_text(encoding="utf-8", errors="replace").split()
    except OSError:
        return False
    pid: int | None = None
    stamp: float | None = None
    if raw:
        try:
            pid = int(raw[0])
        except ValueError:
            pid = None
    if len(raw) >= 2:
        try:
            stamp = float(raw[1])
        except ValueError:
            stamp = None
    alive = _pid_alive(pid) if pid is not None else False
    if alive:
        # A live owner is busy, even if the timestamp looks old (a legit
        # run with no timeout can hold the lock for a long time).
        return False
    if pid is None:
        # No PID recorded: could be a half-written lock from a process that
        # crashed mid-create at startup — leave it alone (conservative).
        return False
    if stamp is None:
        # Legacy/unparseable lock with a dead/missing PID: fall back to mtime.
        try:
            stamp = lf.stat().st_mtime
        except OSError:
            return False
    if now - stamp < STALE_LOCK_SECONDS:
        return False
    try:
        lf.unlink()
        return True
    except FileNotFoundError:
        return True  # someone else already cleaned it
    except OSError:
        return False


@contextmanager
def task_lock(task_id: str) -> Iterator[Path]:
    """Acquire an exclusive lock for the given task id.

    Raises LockBusyError if another mdrunner process already holds the lock.
    A leftover lock from a crashed run (dead PID + old timestamp) is
    reclaimed automatically instead of blocking forever.
    """
    lf = lock_file(task_id)
    lf.parent.mkdir(parents=True, exist_ok=True)
    fd = None
    for attempt in range(2):
        try:
            fd = os.open(str(lf), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except OSError as exc:
            if exc.errno != errno.EEXIST:
                raise
            if attempt == 0 and _remove_if_stale(lf):
                continue
            raise LockBusyError(f"task {task_id!r} is already running (lock={lf})")
    assert fd is not None
    try:
        os.write(fd, f"{os.getpid()}\n{time.time():.0f}\n".encode())
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
    skipped: bool = False          # a gate (min-interval / quota) blocked the run
    skip_reason: str | None = None
    final_message: str | None = None  # best-effort last user-facing reply
    run_id: str | None = None
    agent_session: str | None = None

    @property
    def duration_seconds(self) -> float:
        return max(0.0, self.finished_at - self.started_at)

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out and self.error is None


@dataclass
class ArtifactDeliveryResult:
    ok: bool
    attempted_files: list[str] = field(default_factory=list)
    sent_files: list[str] = field(default_factory=list)
    error: str | None = None


ARTIFACT_PROMPT_INSTRUCTION = """

--- mdrunner artifact delivery requirement ---
If you create a result file intended for delivery, verify that the file really
exists before finishing. In your final response, print one separate line using
this exact format, replacing the example with the actual absolute path:
Saved: /absolute/path/to/the_actual_file
Do not print a Saved: line for a file that does not exist, and do not invent a
path. If there is no result file, do not print a Saved: line.
--- end mdrunner artifact delivery requirement ---
""".strip()


@contextmanager
def _prompt_file_for_task(task: Task, prompt_file: Path) -> Iterator[Path]:
    """Yield the prompt path, augmenting it only for artifact delivery tasks."""
    if not task.notify_artifact:
        yield prompt_file
        return

    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", suffix=".md", prefix="mdrunner-prompt-", delete=False
        ) as fh:
            temporary_path = Path(fh.name)
            original = prompt_file.read_text(encoding="utf-8")
            fh.write(original)
            if original and not original.endswith("\n"):
                fh.write("\n")
            fh.write("\n" + ARTIFACT_PROMPT_INSTRUCTION + "\n")
        yield temporary_path
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass


# ---------------------------------------------------------------------------
# Saved-file detection
# ---------------------------------------------------------------------------


def detect_saved_file(text: str, markers: list[str]) -> str | None:
    """Return the LAST saved-file path found in the agent output, if any.

    The most recent save marker is almost always the real artifact, since
    agents that emit multiple saves along the way produce the final one last.
    """
    last: str | None = None
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        for marker in markers:
            if marker in s:
                tail = s.split(marker, 1)[1].strip()
                # strip surrounding quotes / angle brackets / backticks
                tail = tail.strip("`\"'<> ")
                # extract first plausible absolute path
                for token in tail.split():
                    if token.startswith("/") or (
                        len(token) > 2 and token[1] == ":" and token[2] in ("/", "\\")
                    ):
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


def _task_state() -> dict:
    import json

    from .utils.paths import task_state_file

    try:
        return json.loads(task_state_file().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def last_real_run_at(task_id: str) -> float | None:
    v = _task_state().get(task_id, {}).get("last_run_at")
    return float(v) if v else None


def _record_real_run(task_id: str) -> None:
    import json

    from .utils.paths import task_state_file

    st = _task_state()
    st.setdefault(task_id, {})["last_run_at"] = time.time()
    p = task_state_file()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(st, indent=2), encoding="utf-8")


def _skipped_result(task_id: str, reason: str, log_file: str | None = None) -> RunResult:
    now = time.time()
    if log_file:
        try:
            from .runlog import prune_log

            prune_log(Path(log_file))
            with Path(log_file).open("a", encoding="utf-8") as fh:
                fh.write(
                    f"\n===== mdrunner skip {time.strftime('%Y-%m-%d %H:%M:%S')} "
                    f"task={task_id} — {reason} =====\n"
                )
        except OSError:
            pass
    return RunResult(
        task_id=task_id, started_at=now, finished_at=now, exit_code=None,
        timed_out=False, binary_path=None, argv=[], log_file=log_file,
        skipped=True, skip_reason=reason,
    )


def run_task(
    task_id: str,
    *,
    mode: str = "manual",
    settings: Settings | None = None,
    tasks_file: Path | None = None,
    settings_file: Path | None = None,
    timeout_override: int | None = None,
    force_run: bool = False,
) -> RunResult:
    """Execute one task by id. Returns a RunResult.

    mode: "manual" | "scheduled" — selects which bypass flag set from settings.
    A "scheduled" run passes through the min-rerun-interval and quota-condition
    gates unless ``force_run`` is set; "manual" always bypasses them.
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
        # A leftover OS timer must not look like a failed run (exit 1 + telegram).
        return _skipped_result(task_id, f"task {task_id!r} is disabled")

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

    # Gates (min re-run interval + quota condition). Scheduled runs only.
    if mode == "scheduled" and not force_run and (
        task.min_rerun_interval.enabled or task.quota_condition is not None
    ):
        from .quota import load_snapshot
        from .quota_gate import check_task_gate
        from .utils.paths import task_log_file

        decision = check_task_gate(
            task, last_run_at=last_real_run_at(task_id), snapshot=load_snapshot()
        )
        if not decision.allowed:
            return _skipped_result(task_id, decision.reason, str(task_log_file(task_id)))

    result = _execute(task, agent_cfg, settings.defaults, mode, timeout_override)
    if not result.skipped:
        _record_real_run(task_id)
    if task.notify_start:
        _send_start_via_telegram(task, result)
    if result.ok and task.notify_final_message:
        _send_final_message_via_telegram(task, result)
    if result.ok and task.notify_artifact:
        delivery = _send_result_artifacts_via_telegram(task, settings, result)
        if delivery.ok:
            if result.log_file:
                with Path(result.log_file).open("a", encoding="utf-8") as log_fh:
                    log_fh.write(
                        "\n[mdrunner] telegram artifact delivery succeeded: "
                        f"{', '.join(delivery.sent_files)}\n"
                    )
        else:
            result.error = delivery.error or "Telegram artifact delivery failed"
            if result.log_file:
                with Path(result.log_file).open("a", encoding="utf-8") as log_fh:
                    log_fh.write(
                        f"\n[mdrunner] telegram artifact delivery failed: {result.error}\n"
                    )
    return result


def _send_start_via_telegram(task: Task, result: RunResult) -> None:
    """Best-effort: never fails the run if Telegram is unconfigured."""
    from . import telegram
    from .ui import _telegram_settings

    cfg = _telegram_settings.load()
    bot_token = cfg.get("bot_token")
    chat_id = cfg.get("chat_id")
    if not bot_token or not chat_id:
        return
    try:
        telegram.send(
            bot_token=bot_token,
            chat_id=chat_id,
            text=telegram.format_start(task.name),
            timeout=15.0,
        )
    except Exception:  # noqa: BLE001
        return
    finally:
        if result.log_file:
            try:
                with Path(result.log_file).open("a", encoding="utf-8") as log_fh:
                    log_fh.write("\n[mdrunner] telegram start message sent\n")
            except OSError:
                pass


def _send_final_message_via_telegram(task: Task, result: RunResult) -> None:
    """Best-effort: never fails the run if Telegram or extraction misses."""
    from . import telegram
    from .final_message import format_final_message, split_telegram_chunks
    from .ui import _telegram_settings

    cfg = _telegram_settings.load()
    bot_token = cfg.get("bot_token")
    chat_id = cfg.get("chat_id")
    log_note = ""
    if not bot_token or not chat_id:
        log_note = "telegram bot_token / chat_id not configured"
    elif not result.final_message:
        log_note = "no final message extracted from agent stdout"
    else:
        chunks = split_telegram_chunks(result.final_message)
        n = len(chunks)
        sent = 0
        last_err = ""
        for i, chunk in enumerate(chunks, 1):
            text = format_final_message(task.name, chunk, part=i, parts=n)
            ok, detail = telegram.send(
                bot_token=bot_token, chat_id=chat_id, text=text, timeout=15.0
            )
            if ok:
                sent += 1
            else:
                last_err = detail
                break
        if sent == n:
            log_note = f"telegram final message sent ({sent} part{'s' if sent != 1 else ''})"
        else:
            log_note = f"telegram final message failed: {last_err or 'send failed'}"

    if result.log_file:
        try:
            with Path(result.log_file).open("a", encoding="utf-8") as log_fh:
                log_fh.write(f"\n[mdrunner] {log_note}\n")
        except OSError:
            pass


def _send_result_artifacts_via_telegram(
    task: Task, settings: Settings, result: RunResult
) -> ArtifactDeliveryResult:
    from .ui import _telegram_settings
    from . import telegram

    cfg = _telegram_settings.load()
    bot_token = cfg.get("bot_token")
    chat_id = cfg.get("chat_id")
    if not bot_token or not chat_id:
        return ArtifactDeliveryResult(
            ok=False,
            error="Telegram bot_token / chat_id not configured",
        )

    # 결과물 수집 리스트
    files_to_send = []

    # 1순위: 로그에서 파싱된 파일이 유효한 경우
    if result.saved_file:
        p = Path(result.saved_file).expanduser()
        if p.exists() and p.is_file():
            files_to_send.append(p)

    # 2순위: 1순위 검출 실패 시 디렉터리 타임스탬프 관측 스캔
    if not files_to_send and task.artifact_dir:
        dir_path = Path(task.artifact_dir).expanduser()
        if dir_path.exists() and dir_path.is_dir():
            window = settings.defaults.artifact_time_window_seconds
            # finished_at 기준 최근 N초 내의 범위
            start_limit = result.finished_at - window

            # 재귀적으로 탐색
            for root, _, files in os.walk(str(dir_path)):
                for file in files:
                    fp = Path(root) / file
                    # 확장자 검사
                    if fp.suffix.lower() in [ext.lower() for ext in task.artifact_extensions]:
                        try:
                            mtime = fp.stat().st_mtime
                            ctime = fp.stat().st_ctime
                            # 최근 생성/수정 시간 조건 매칭
                            if (start_limit <= mtime <= result.finished_at + 2) or (
                                start_limit <= ctime <= result.finished_at + 2
                            ):
                                files_to_send.append(fp)
                        except OSError:
                            pass

    if not files_to_send:
        return ArtifactDeliveryResult(
            ok=False,
            error=(
                f"no artifact file found for delivery in {task.artifact_dir or '(no artifact_dir)'}"
            ),
        )

    attempted_files: list[str] = []
    sent_files: list[str] = []
    errors: list[str] = []

    # 파일 전송 실행
    model_name = task.model or "기본 모델"
    if "--model" in result.argv:
        try:
            idx = result.argv.index("--model")
            if idx + 1 < len(result.argv):
                model_name = result.argv[idx + 1]
        except ValueError:
            pass

    for fp in files_to_send:
        caption = (
            f"✅ [mdrunner] '{task.name}' 결과물 전송\n"
            f"- 실행 모델: {model_name}\n"
            f"- 소요 시간: {result.duration_seconds:.1f}초"
        )
        attempted_files.append(str(fp))
        try:
            ok, detail = telegram.send_document(
                bot_token=bot_token, chat_id=chat_id, file_path=str(fp), caption=caption
            )
        except Exception as exc:  # noqa: BLE001
            ok, detail = False, str(exc)
        if ok:
            sent_files.append(str(fp))
        else:
            errors.append(f"{fp}: {detail}")

    return ArtifactDeliveryResult(
        ok=not errors and len(sent_files) == len(attempted_files),
        attempted_files=attempted_files,
        sent_files=sent_files,
        error="; ".join(errors) if errors else None,
    )


def _preflight_fail(
    task: Task,
    *,
    mode: str,
    error: str,
    binary_path: str | None = None,
    argv: list[str] | None = None,
    model: str | None = None,
) -> RunResult:
    from .runlog import format_end, format_start, new_run_id

    started = time.time()
    run_id = new_run_id(started)
    log_path = task_log_file(task.id)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        from .runlog import prune_log

        prune_log(log_path)
    except Exception:  # noqa: BLE001 — pruning must never block a run
        pass
    prompt = Path(task.prompt_file).expanduser() if task.prompt_file else None
    header = format_start(
        when=started,
        run_id=run_id,
        task_id=task.id,
        agent=task.agent,
        model=model or task.model,
        mode=mode,
        cwd=task.working_dir,
        timeout_minutes=task.timeout_minutes,
        prompt_file=prompt if prompt and prompt.exists() else None,
        prompt_chars=0,
        argv_line="(not started)",
    )
    footer = format_end(
        when=time.time(),
        run_id=run_id,
        task_id=task.id,
        agent=task.agent,
        agent_session=None,
        ok=False,
        exit_code=None,
        timed_out=False,
        duration_s=0.0,
        error=error,
    )
    try:
        with log_path.open("a", encoding="utf-8") as log_fh:
            log_fh.write("\n" + header + footer)
    except OSError:
        pass
    return RunResult(
        task_id=task.id,
        started_at=started,
        finished_at=time.time(),
        exit_code=None,
        timed_out=False,
        binary_path=binary_path,
        argv=argv or [],
        log_file=str(log_path),
        error=error,
        run_id=run_id,
    )


def _execute(
    task: Task,
    agent_cfg,
    defaults: Defaults,
    mode: str,
    timeout_override: int | None,
) -> RunResult:
    binary_path = resolve_binary(agent_cfg.binary)
    if binary_path is None:
        return _preflight_fail(
            task,
            mode=mode,
            error=f"agent binary {agent_cfg.binary!r} not found on PATH",
        )

    prompt_file = Path(task.prompt_file).expanduser()
    if not prompt_file.exists():
        return _preflight_fail(
            task,
            mode=mode,
            error=f"prompt file not found: {prompt_file}",
            binary_path=binary_path,
            model=task.model or agent_cfg.default_model,
        )

    adapter = get_adapter(task.agent)
    bypass = agent_cfg.bypass.scheduled if mode == "scheduled" else agent_cfg.bypass.manual
    model = task.model or agent_cfg.default_model

    working_dir = Path(task.working_dir).expanduser() if task.working_dir else None
    if working_dir and not working_dir.exists():
        return _preflight_fail(
            task,
            mode=mode,
            error=f"working_dir not found: {working_dir}",
            binary_path=binary_path,
            model=task.model or agent_cfg.default_model,
        )

    with _prompt_file_for_task(task, prompt_file) as effective_prompt_file:
        argv_result = adapter.build_argv(
            effective_prompt_file,
            model=model,
            working_dir=working_dir,
            extra_args=task.extra_args,
            bypass_flags=bypass,
        )
        argv = argv_result.argv
        cwd = argv_result.cwd or working_dir
        stdin_text = argv_result.stdin_text

        if binary_path and argv:
            argv = [binary_path, *argv[1:]]

        timeout_minutes = timeout_override if timeout_override is not None else task.timeout_minutes
        if timeout_minutes <= 0:
            timeout_seconds = None  # no limit
        else:
            timeout_seconds = timeout_minutes * 60

        log_path = task_log_file(task.id)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        started = time.time()
        from .runlog import (
            argv_for_log,
            format_end,
            format_start,
            infer_failure_reason,
            new_run_id,
            parse_agent_session,
            prune_log,
        )

        prune_log(log_path)

        run_id = new_run_id(started)
        prompt_text = stdin_text
        if prompt_text is None:
            try:
                prompt_text = effective_prompt_file.read_text(encoding="utf-8")
            except OSError:
                prompt_text = None
        prompt_chars = len(prompt_text) if prompt_text is not None else 0
        accumulated_text: list[str] = []
        saved_file: str | None = None
        timed_out = False
        exit_code: int | None = None
        error: str | None = None
        agent_session: str | None = None

        try:
            with task_lock(task.id):
                with log_path.open("a", encoding="utf-8") as log_fh:
                    log_fh.write(
                        "\n"
                        + format_start(
                            when=started,
                            run_id=run_id,
                            task_id=task.id,
                            agent=task.agent,
                            model=model,
                            mode=mode,
                            cwd=str(cwd) if cwd else None,
                            timeout_minutes=(
                                timeout_override
                                if timeout_override is not None
                                else task.timeout_minutes
                            ),
                            prompt_file=effective_prompt_file,
                            prompt_chars=prompt_chars,
                            argv_line=argv_for_log(argv, effective_prompt_file, prompt_text),
                        )
                    )
                    log_fh.flush()

                    proc = subprocess.Popen(
                        wrap_for_windows(argv),
                        cwd=str(cwd) if cwd else None,
                        stdin=subprocess.PIPE if stdin_text is not None else None,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        bufsize=1,
                        env=os.environ.copy(),
                        start_new_session=(sys.platform != "win32"),
                    )

                    deadline = None if timeout_seconds is None else started + timeout_seconds
                    try:
                        if stdin_text is not None and proc.stdin is not None:
                            proc.stdin.write(stdin_text)
                            proc.stdin.close()
                        assert proc.stdout is not None
                        for line in proc.stdout:
                            log_fh.write(line)
                            log_fh.flush()
                            accumulated_text.append(line)
                            if agent_session is None:
                                sid = parse_agent_session(line)
                                if sid:
                                    agent_session = sid
                                    log_fh.write(f"[mdrunner] agent_session={sid}\n")
                                    log_fh.flush()
                            # Streaming save-marker scan: cheap, but we only look at
                            # the last ~4 KB so the cost stays bounded for long runs.
                            # After the run finishes we do a full scan below.
                            recent_window = "".join(accumulated_text[-200:])
                            saved_file = detect_saved_file(recent_window, defaults.artifact_markers)
                            if deadline is not None and time.time() > deadline:
                                timed_out = True
                                _terminate_process_tree(proc)
                                try:
                                    proc.wait(timeout=5)
                                except subprocess.TimeoutExpired:
                                    _kill_process_tree(proc)
                                    # Grace period: on Windows SIGKILL may not
                                    # immediately reap child processes, so wait
                                    # briefly for the tree to actually die.
                                    _wait_for_exit(proc, TIMEOUT_GRACE_SECONDS)
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

                    finished = time.time()
                    stdout_text = "".join(accumulated_text)
                    reason = infer_failure_reason(
                        stdout_text,
                        exit_code=exit_code,
                        timed_out=timed_out,
                        error=error,
                    )
                    if error is None and reason and (
                        timed_out or exit_code not in (0, None)
                    ):
                        error = reason
                    ok = exit_code == 0 and not timed_out and error is None
                    log_fh.write(
                        format_end(
                            when=finished,
                            run_id=run_id,
                            task_id=task.id,
                            agent=task.agent,
                            agent_session=agent_session,
                            ok=ok,
                            exit_code=exit_code,
                            timed_out=timed_out,
                            duration_s=max(0.0, finished - started),
                            error=error,
                        )
                    )
        except LockBusyError as exc:
            return _preflight_fail(
                task,
                mode=mode,
                error=str(exc),
                binary_path=binary_path,
                argv=argv,
                model=model,
            )

        finished = time.time()
        stdout_text = "".join(accumulated_text)
        if saved_file is None:
            saved_file = detect_saved_file(stdout_text, defaults.artifact_markers)
        from .final_message import extract_final_message

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
            final_message=extract_final_message(stdout_text),
            run_id=run_id,
            agent_session=agent_session,
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
