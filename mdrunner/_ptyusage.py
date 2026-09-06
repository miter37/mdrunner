"""Drive an interactive agent CLI on a pseudo-terminal to scrape its
``/usage`` screen.

Linux/macOS only (uses :mod:`pty`). Best-effort: any failure returns an
empty string and the caller falls back to "unavailable". Nothing here runs
model inference — ``/usage`` is a local account read.

Steps are event-driven: a step waits for a marker to appear in the output
(with a time cap) before sending its keystrokes, so a slow-starting TUI on
a busy machine doesn't get typed at before it's ready. An optional
``stop_when`` marker ends the capture as soon as the data is on screen.
"""

from __future__ import annotations

import os
import re
import select
import signal
import time
from typing import Optional, Union

_ANSI_OSC = re.compile(r"\x1b\][0-9].*?(?:\x07|\x1b\\)", re.S)
_ANSI_CSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_ANSI_OTHER = re.compile(r"\x1b[=>@-Z\\-_]")

# A step's trigger: a float = "this many seconds after the previous step",
# or ("await", regex, max_seconds) = "wait for regex, but no longer than
# max_seconds after the previous step".
Trigger = Union[float, tuple]
Step = tuple[Trigger, str]


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
    steps: list[Step],
    total_seconds: float = 40.0,
    stop_when: Optional[str] = None,
    cols: int = 200,
    rows: int = 50,
) -> str:
    """Spawn ``argv`` on a PTY, replay ``steps``, return the ANSI-stripped
    transcript ("" on failure)."""
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

    stop_re = re.compile(stop_when) if stop_when else None
    raw = b""
    text = ""
    start = time.time()
    idx = 0
    step_since = start  # when the current step became "pending"

    def _send(s: str) -> None:
        for ch in s:
            try:
                os.write(fd, ch.encode())
            except OSError:
                return
            time.sleep(0.04)

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
            text = strip_ansi(raw.decode("utf-8", "replace"))
            if stop_re and stop_re.search(text):
                # give the screen a beat to finish painting, then done
                time.sleep(0.6)
                try:
                    raw += os.read(fd, 65536)
                except OSError:
                    pass
                break

        if idx < len(steps):
            trig, payload = steps[idx]
            waited = time.time() - step_since
            fire = False
            if isinstance(trig, (int, float)):
                fire = waited >= trig
            else:  # ("await", pattern, cap)
                _, pattern, cap = trig
                fire = bool(re.search(pattern, text)) or waited >= cap
            if fire:
                _send(payload)
                idx += 1
                step_since = time.time()

    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.kill(pid, sig)
        except ProcessLookupError:
            break
        time.sleep(0.2)
    try:
        os.close(fd)
    except OSError:
        pass
    try:
        os.waitpid(pid, 0)
    except ChildProcessError:
        pass

    return strip_ansi(raw.decode("utf-8", "replace"))
