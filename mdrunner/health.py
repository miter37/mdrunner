"""Agent health check — locate binary and probe version.

Designed to be cheap enough to call from app-startup and settings-save
without any caching layer.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import Sequence

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
    if "yolo" in joined or "dangerously-bypass" in joined or "danger-full" in joined:
        return "high"
    if "auto-approve" in joined or "--auto" in joined or "never" in joined:
        return "medium"
    if not bypass_flags:
        return "none"
    return "low"