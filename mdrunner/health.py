"""Agent health check — locate binary and probe version.

Designed to be cheap enough to call from app-startup and settings-save
without any caching layer.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import Callable, Sequence

from .agents import resolve_binary


@dataclass
class HealthResult:
    ok: bool
    binary_path: str | None = None
    version: str | None = None
    stdout: str = ""
    stderr: str = ""
    error: str | None = None


def probe_health(binary: str, health_cmd: Sequence[str], timeout: float = 5.0) -> HealthResult:
    """Return binary path + version output for the configured agent."""
    path = resolve_binary(binary)
    if path is None:
        return HealthResult(
            ok=False,
            error=f"binary '{binary}' not found on PATH",
        )

    cmd = list(health_cmd) if health_cmd else [binary, "--version"]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        return HealthResult(
            ok=False,
            binary_path=path,
            error=f"health command timed out after {timeout}s",
            stdout=(exc.stdout or b"").decode(errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or ""),
        )
    except FileNotFoundError:
        return HealthResult(ok=False, binary_path=path, error=f"command not runnable: {' '.join(cmd)}")
    except OSError as exc:
        return HealthResult(ok=False, binary_path=path, error=str(exc))

    version_output = (proc.stdout or "") + (("\n" + proc.stderr) if proc.stderr else "")
    version_output = version_output.strip()

    if proc.returncode != 0:
        return HealthResult(
            ok=False,
            binary_path=path,
            stdout=proc.stdout or "",
            stderr=proc.stderr or "",
            error=f"health command exit={proc.returncode}",
        )

    return HealthResult(
        ok=True,
        binary_path=path,
        version=version_output.splitlines()[0] if version_output else None,
        stdout=proc.stdout or "",
        stderr=proc.stderr or "",
    )


def bypass_risk_level(bypass_flags: Sequence[str]) -> str:
    """Heuristic warning level for a bypass flag list."""
    joined = " ".join(bypass_flags).lower()
    if (
        "yolo" in joined
        or "dangerously-bypass" in joined
        or "danger-full" in joined
        or "dangerously-skip" in joined
        or "bypasspermissions" in joined
    ):
        return "high"
    if "auto-approve" in joined or "--auto" in joined or "never" in joined:
        return "medium"
    if not bypass_flags:
        return "none"
    return "low"


def probe_health_interactive(
    agent_id: str,
    binary: str,
    model: str | None,
    progress_callback: Callable[[str], None],
    timeout: float = 30.0
) -> HealthResult:
    import tempfile
    import os

    # [1/4] 바이너리 경로 탐색
    progress_callback("[1/4] 바이너리 경로 탐색 중...")
    path = resolve_binary(binary)
    if path is None:
        err = f"바이너리 '{binary}'를 PATH에서 찾을 수 없습니다."
        progress_callback(f"✗ 실패: {err}")
        return HealthResult(ok=False, error=err)
    progress_callback(f"✓ 성공: {path}")

    # [2/4] CLI 버전 정보 확인
    progress_callback("[2/4] CLI 버전 정보 확인 중...")
    ver_cmd = [path, "--version"]
    if agent_id == "hermes":
        ver_cmd = [path, "version"]
    try:
        proc = subprocess.run(ver_cmd, capture_output=True, text=True, timeout=5.0)
        version_output = (proc.stdout or "") + (proc.stderr or "")
        version_output = version_output.strip().splitlines()[0] if version_output.strip() else "Unknown Version"
        progress_callback(f"✓ 성공: {version_output}")
    except Exception as e:
        progress_callback(f"⚠ 경고: 버전 쿼리 실패 ({e}) - 계속 진행합니다.")
        version_output = "Unknown"

    # [3/4] 실시간 인퍼런스 요청
    prompt = "i say ping, you say pong"
    progress_callback(f"[3/4] 실시간 인퍼런스 요청 중 (모델: {model or '기본 모델'})...")

    cmd = []
    stdin_data = None
    temp_prompt_file = None

    try:
        if agent_id == "agy":
            cmd = [path, "-p", prompt, "--dangerously-skip-permissions"]
            if model:
                cmd += ["--model", model]
        elif agent_id == "grok":
            cmd = [path, "-p", prompt, "--permission-mode", "bypassPermissions"]
            if model:
                cmd += ["--model", model]
        elif agent_id == "claude":
            cmd = [path, "-p", "--dangerously-skip-permissions"]
            if model:
                cmd += ["--model", model]
            stdin_data = prompt
        elif agent_id == "codex":
            cmd = [path, "exec", "--dangerously-bypass-approvals-and-sandbox", prompt]
            if model:
                cmd += ["--model", model]
        elif agent_id == "opencode":
            cmd = [path, "run", prompt, "--auto"]
            if model:
                cmd += ["--model", model]
        elif agent_id == "hermes":
            cmd = [path, "chat", "-q", prompt, "-Q", "--yolo"]
            if model:
                cmd += ["--model", model]
        elif agent_id == "openclaw":
            fd, temp_path = tempfile.mkstemp(suffix=".md", text=True)
            temp_prompt_file = temp_path
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(prompt)
            cmd = [path, "agent", "--local", "--message-file", temp_path]
            if model:
                cmd += ["--model", model]
        else:
            cmd = [path, prompt]

        proc = subprocess.run(
            cmd,
            input=stdin_data,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        stdout = proc.stdout or ""
        stderr = proc.stderr or ""
        exit_code = proc.returncode
    except subprocess.TimeoutExpired:
        err = f"인퍼런스 요청이 제한 시간({timeout}초)을 초과했습니다."
        progress_callback(f"✗ 실패: {err}")
        return HealthResult(ok=False, binary_path=path, error=err)
    except Exception as e:
        err = f"인퍼런스 실행 중 오류 발생: {e}"
        progress_callback(f"✗ 실패: {err}")
        return HealthResult(ok=False, binary_path=path, error=err)
    finally:
        if temp_prompt_file and os.path.exists(temp_prompt_file):
            try:
                os.remove(temp_prompt_file)
            except Exception:
                pass

    # [4/4] 응답 결과 검증
    progress_callback("[4/4] 응답 결과 검증 중...")
    combined_output = stdout + "\n" + stderr
    
    if "401" in combined_output or "Unauthorized" in combined_output or "Authentication Failed" in combined_output:
        err = "인증 오류(401 Unauthorized)가 발생했습니다. API 키 또는 로그인 설정을 확인하십시오."
        progress_callback(f"✗ 실패: {err}")
        return HealthResult(ok=False, binary_path=path, stdout=stdout, stderr=stderr, error=err)

    if exit_code != 0:
        err = f"프로세스가 에러 코드({exit_code})로 종료되었습니다."
        progress_callback(f"✗ 실패: {err}")
        return HealthResult(ok=False, binary_path=path, stdout=stdout, stderr=stderr, error=err)

    if "pong" in combined_output.lower():
        progress_callback("✓ 성공: 에이전트가 'pong' 응답을 올바르게 반환했습니다.")
        return HealthResult(ok=True, binary_path=path, version=version_output, stdout=stdout, stderr=stderr)
    else:
        err = "에이전트 응답에 'pong' 단어가 포함되어 있지 않습니다."
        progress_callback(f"✗ 실패: {err}")
        return HealthResult(ok=False, binary_path=path, stdout=stdout, stderr=stderr, error=err)