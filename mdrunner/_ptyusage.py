"""Drive an interactive agent CLI on a pseudo-terminal to scrape its
``/usage`` screen.

Linux/macOS only (uses :mod:`pty`). Best-effort: any failure returns an
empty string and the caller falls back to "unavailable". Nothing here runs
model inference, so it consumes no usage quota — ``/usage`` is a local
account read.
"""

from __future__ import annotations

import os
import re
import select
import signal
import time

_ANSI_OSC = re.compile(r"\x1b\][0-9].*?(?:\x07|\x1b\\)", re.S)
_ANSI_CSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_ANSI_OTHER = re.compile(r"\x1b[=>@-Z\\-_]")


def strip_ansi(s: str) -> str:
    s = _ANSI_OSC.sub("", s)
    s = _ANSI_CSI.sub("", s)
    s = _ANSI_OTHER.sub("", s)
    return s.replace("\r", "")


def supported() -> bool:
    return hasattr(os, "openpty") and hasattr(os, "fork")


def capture_screen(
    argv: list[str],
    *,
    cwd: str,
    script: list[tuple[float, str]],
    total_seconds: float = 30.0,
    cols: int = 200,
    rows: int = 50,
) -> str:
    """Spawn ``argv`` on a PTY, replay ``script`` (list of ``(delay, text)``,
    text typed a keystroke at a time), collect output for ``total_seconds``,
    then SIGTERM it. Returns the ANSI-stripped transcript ("" on failure).
    """
    if not supported():
        return ""
    try:
        import fcntl
        import pty
        import struct
        import termios
    except ImportError:
        return ""

    try:
        pid, fd = pty.fork()
    except OSError:
        return ""
    if pid == 0:  # child
        try:
            os.chdir(cwd)
            os.environ["TERM"] = "xterm-256color"
            os.environ["COLUMNS"] = str(cols)
            os.environ["LINES"] = str(rows)
            os.environ.pop("CI", None)
            os.execvp(argv[0], argv)
        except Exception:  # noqa: BLE001
            os._exit(127)

    try:
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
    except OSError:
        pass

    raw = b""
    start = time.time()
    idx = 0
    pending = sorted(script, key=lambda x: x[0])
    while time.time() - start < total_seconds:
        try:
            r, _, _ = select.select([fd], [], [], 0.2)
        except (OSError, ValueError):
            break
        if r:
            try:
                chunk = os.read(fd, 65536)
            except OSError:
                break
            if not chunk:
                break
            raw += chunk
        elapsed = time.time() - start
        while idx < len(pending) and elapsed >= pending[idx][0]:
            for ch in pending[idx][1]:
                try:
                    os.write(fd, ch.encode())
                except OSError:
                    break
                time.sleep(0.04)
            idx += 1

    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    time.sleep(0.2)
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        os.close(fd)
    except OSError:
        pass
    try:
        os.waitpid(pid, 0)
    except ChildProcessError:
        pass

    return strip_ansi(raw.decode("utf-8", "replace"))
