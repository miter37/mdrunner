"""Per-agent usage-quota probes (weekly / rolling window limits + reset time).

None of the coding CLIs expose a stable ``usage``/``quota`` subcommand, so
each vendor needs its own token-free trick:

- **codex** — its ``codex app-server`` speaks JSON-RPC over stdio and answers
  ``account/rateLimits/read`` (the same call the TUI's ``/status`` uses). This
  is an account read, not an inference call, so it consumes no model tokens.
- **claude / grok / agy** — no token-free machine-readable source has been
  found yet; their probes return ``available=False`` with a short note. The
  dispatch table below is the single place to slot one in.

Everything here is headless (no Qt) and best-effort: a probe never raises,
it returns a :class:`QuotaResult` with ``available=False`` and an ``error``.
"""

from __future__ import annotations

import json
import os
import queue
import re
import subprocess
import threading
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from typing import Optional

from .agents import resolve_binary

# The 4 agents the quota panel tracks (subscription coding CLIs).
QUOTA_AGENTS = ("claude", "codex", "agy", "grok")


@dataclass
class QuotaWindow:
    """One rate-limit window (e.g. the 5-hour or the weekly bucket)."""

    label: str  # "5h" | "weekly" | "12h window" ...
    used_percent: Optional[float] = None
    resets_at: Optional[float] = None  # unix seconds
    window_minutes: Optional[int] = None

    @property
    def seconds_until_reset(self) -> Optional[float]:
        if self.resets_at is None:
            return None
        return max(0.0, self.resets_at - time.time())


@dataclass
class QuotaResult:
    agent: str
    available: bool
    # authoritative = read from a structured source (codex app-server)
    # estimated     = derived (e.g. PTY /usage scrape, user-entered reset time)
    # unavailable   = no source wired up
    confidence: str = "unavailable"
    checked_at: float = field(default_factory=time.time)
    plan: Optional[str] = None
    windows: list[QuotaWindow] = field(default_factory=list)
    note: Optional[str] = None  # extra human context (e.g. reset credits)
    error: Optional[str] = None  # set when available is False

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _window_label(minutes: Optional[int]) -> str:
    if not minutes:
        return "window"
    if 240 <= minutes <= 360:
        return "5h"
    if 9000 <= minutes <= 11000:
        return "weekly"
    if minutes % 1440 == 0:
        return f"{minutes // 1440}d"
    if minutes % 60 == 0:
        return f"{minutes // 60}h"
    return f"{minutes}m"


_DUR_RE = re.compile(r"(?:(\d+)\s*d)?\s*(?:(\d+)\s*h)?\s*(?:(\d+)\s*m)?", re.I)


def _duration_to_resets_at(text: str, *, now: Optional[float] = None) -> Optional[float]:
    """'115h 39m' / '2d 3h' / '39m' -> absolute unix ts (now + delta)."""
    m = _DUR_RE.search(text.strip())
    if not m or not any(m.groups()):
        return None
    d, h, mi = (int(x) if x else 0 for x in m.groups())
    if d == h == mi == 0:
        return None
    base = now if now is not None else time.time()
    return base + timedelta(days=d, hours=h, minutes=mi).total_seconds()


def _clock_to_resets_at(when: str, tz: str, *, now_dt: Optional[datetime] = None) -> Optional[float]:
    """Claude's reset strings: '2:10pm' (next occurrence) or
    'Sep 12, 11pm' (that date). ``tz`` is an IANA name shown in the screen."""
    try:
        from zoneinfo import ZoneInfo

        zone = ZoneInfo(tz)
    except Exception:  # noqa: BLE001 — unknown tz name
        return None
    now_local = now_dt.astimezone(zone) if now_dt else datetime.now(zone)
    w = when.strip().replace(".", "")
    for fmt in ("%I:%M%p", "%I%p"):
        try:
            t = datetime.strptime(w.replace(" ", ""), fmt)
            cand = now_local.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)
            if cand <= now_local:
                cand += timedelta(days=1)
            return cand.timestamp()
        except ValueError:
            pass
    for fmt in ("%b %d, %I:%M%p", "%b %d, %I%p", "%b %d %I:%M%p", "%b %d %I%p"):
        try:
            # anchor a year so strptime doesn't warn / mis-handle leap day
            t = datetime.strptime(f"{now_local.year} {w}", f"%Y {fmt}")
            cand = now_local.replace(
                month=t.month, day=t.day, hour=t.hour, minute=t.minute,
                second=0, microsecond=0,
            )
            if cand <= now_local:
                cand = cand.replace(year=cand.year + 1)
            return cand.timestamp()
        except ValueError:
            pass
    return None


def fmt_reset(seconds: Optional[float]) -> str:
    """'2d 3h' / '4h 12m' / '9m' / '—' — compact time-until-reset."""
    if seconds is None:
        return "—"
    s = int(max(0, seconds))
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m, _ = divmod(s, 60)
    if d:
        return f"{d}d {h}h"
    if h:
        return f"{h}h {m}m"
    return f"{m}m"


# ---------------------------------------------------------------------------
# codex — JSON-RPC over `codex app-server`
# ---------------------------------------------------------------------------


def _resolve_cli(binary: str) -> Optional[str]:
    """resolve_binary(), plus well-known install dirs that a GUI/desktop
    launch misses (nvm's node bins hold `codex`, `~/.local/bin` holds most)."""
    path = resolve_binary(binary)
    if path:
        return path
    import glob
    import os as _os

    home = _os.path.expanduser("~")
    candidates = [
        *sorted(glob.glob(f"{home}/.nvm/versions/node/*/bin/{binary}")),
        f"{home}/.local/bin/{binary}",
        f"/usr/local/bin/{binary}",
        f"/opt/homebrew/bin/{binary}",
    ]
    for c in candidates:
        if _os.path.isfile(c) and _os.access(c, _os.X_OK):
            return c
    return None


def _probe_codex(binary: str, timeout: float = 20.0) -> QuotaResult:
    path = _resolve_cli(binary)
    if path is None:
        return QuotaResult("codex", False, error=f"binary {binary!r} not on PATH")

    try:
        proc = subprocess.Popen(
            [path, "app-server"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )
    except OSError as exc:
        return QuotaResult("codex", False, error=f"could not start app-server: {exc}")

    q: "queue.Queue[Optional[str]]" = queue.Queue()

    def _reader() -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            q.put(line)
        q.put(None)

    threading.Thread(target=_reader, daemon=True).start()

    def _send(obj: dict) -> None:
        assert proc.stdin is not None
        proc.stdin.write(json.dumps(obj) + "\n")
        proc.stdin.flush()

    deadline = time.time() + timeout
    result_payload: Optional[dict] = None
    try:
        _send(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "clientInfo": {"name": "mdrunner", "version": "0.1.0"},
                    "capabilities": {"experimentalApi": True},
                },
            }
        )
        _send({"jsonrpc": "2.0", "method": "initialized", "params": {}})
        _send(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "account/rateLimits/read",
                "params": {},
            }
        )
        while time.time() < deadline:
            try:
                line = q.get(timeout=max(0.1, deadline - time.time()))
            except queue.Empty:
                break
            if line is None:
                break
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if msg.get("id") == 2:
                if "error" in msg:
                    return QuotaResult(
                        "codex", False, error=str(msg["error"].get("message", msg["error"]))
                    )
                result_payload = msg.get("result")
                break
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()

    if result_payload is None:
        return QuotaResult("codex", False, error="no response from app-server (not logged in?)")

    rl = result_payload.get("rateLimits") or {}
    windows: list[QuotaWindow] = []
    for key in ("primary", "secondary"):
        w = rl.get(key)
        if not w:
            continue
        mins = w.get("windowDurationMins")
        windows.append(
            QuotaWindow(
                label=_window_label(mins),
                used_percent=w.get("usedPercent"),
                resets_at=w.get("resetsAt"),
                window_minutes=mins,
            )
        )
    note = None
    credits = result_payload.get("rateLimitResetCredits") or {}
    if credits.get("availableCount"):
        note = f"{credits['availableCount']} reset credit(s) available"

    return QuotaResult(
        agent="codex",
        available=bool(windows),
        confidence="authoritative" if windows else "unavailable",
        plan=rl.get("planType"),
        windows=windows,
        note=note,
        error=None if windows else "app-server returned no rate-limit windows",
    )


# ---------------------------------------------------------------------------
# claude / grok / agy — no structured token-free source yet
# ---------------------------------------------------------------------------
#
# Per-vendor status as of 2026-09 (see docs/quota-adapters.md):
#   claude — no `claude usage --json`; `/usage` (session + week %, reset time)
#            is TUI-only. Path: spawn a PTY, send `/usage`, strip ANSI, regex.
#            Fallback: user-entered weekly reset time + stop on "usage limit
#            reached" error. Result would be confidence="estimated".
#   grok   — open source. Its `/usage` UI calls an internal billing handler
#            (`/billing?format=credits` -> BillingConfigResponse with
#            `creditUsagePercent` + `currentPeriod{type:WEEKLY,start,end}`).
#            Calling it over `grok agent stdio` has historically returned
#            "Method not found"; the clean path is a tiny `grok usage-json`
#            helper in a fork that reuses `handle_get_billing()`.
#   agy    — `/usage` (weekly + five-hour, % remaining + refresh-in) is
#            TUI-only; internally backed by RetrieveUserQuotaSummary
#            (remainingFraction / resetTime). Path: reuse that call via a
#            helper, or PTY-scrape `/usage` for an MVP.


def _probe_unavailable(agent: str, why: str) -> QuotaResult:
    return QuotaResult(agent, available=False, confidence="unavailable", error=why)


# --- claude: scrape the interactive /usage screen over a PTY ----------------

# Claude's TUI paints /usage with cursor moves, not newlines, so the whole
# block often arrives on one line: "Current session███ 24%used Resets 2:10pm
# (Asia/Seoul)Current week (all models)█ 2%used Resets Sep 12, 11pm (...)".
_CLAUDE_BLOCK_RE = re.compile(
    r"Current[ \t]+(session|week).{0,160}?(\d+)[ \t]*%[ \t]*used"
    r".{0,40}?Resets[ \t]+([^(\n]+?)[ \t]*\(([^)\n]+)\)",
    re.I | re.S,
)


def _parse_claude_usage(text: str) -> list[QuotaWindow]:
    windows: list[QuotaWindow] = []
    for kind, pct, when, tz in _CLAUDE_BLOCK_RE.findall(text):
        label = "5h" if kind.lower() == "session" else "weekly"
        windows.append(
            QuotaWindow(
                label=label,
                used_percent=float(pct),
                resets_at=_clock_to_resets_at(when, tz.strip()),
            )
        )
    return windows


def _probe_claude(binary: str, timeout: float = 28.0) -> QuotaResult:
    path = _resolve_cli(binary)
    if path is None:
        return QuotaResult("claude", False, error=f"binary {binary!r} not on PATH")
    from . import _ptyusage

    if not _ptyusage.supported():
        return QuotaResult("claude", False, error="PTY scrape unsupported on this platform")
    # Claude may open a "trust this folder?" prompt on a fresh session; the
    # default choice is "No, exit", so arrow-down to "Yes, I trust" + Enter
    # first. If there is no prompt those keys land harmlessly in the input.
    text = _ptyusage.capture_screen(
        [path],
        cwd=os.path.expanduser("~"),
        script=[
            (3.5, "\x1b[B"), (4.0, "\r"),
            (7.0, "/usage"), (7.8, "\r"), (10.0, "\r"), (13.0, "\r"),
        ],
        total_seconds=timeout,
    )
    windows = _parse_claude_usage(text)
    if not windows:
        return QuotaResult(
            "claude", False, error="could not parse /usage screen (format changed?)"
        )
    return QuotaResult(
        "claude",
        available=True,
        confidence="estimated",
        plan="Claude" + (" Pro" if "Claude Pro" in text else ""),
        windows=windows,
        note="scraped from /usage (approximate)",
    )


# --- agy (Antigravity): scrape /usage; use the GEMINI MODELS group ---------

_AGY_LIMIT_RE = re.compile(
    r"(Weekly|Five\s*Hour)\s+Limit\s+Remaining[^\n]*\n"
    r"[^\n]*?(\d+(?:\.\d+)?)\s*%[^\n]*\n"  # bar line — the precise remaining %
    r"[^\n]*?(?:Refreshes\s+in\s+([0-9dhm \t]+)|Quota\s+available)",
    re.I,
)


def _parse_agy_usage(text: str, group: str = "GEMINI MODELS") -> list[QuotaWindow]:
    # Slice out the requested model group's section.
    up = text
    gi = up.find(group)
    if gi == -1:
        return []
    rest = up[gi + len(group):]
    # stop at the next ALL-CAPS "... MODELS" header
    nxt = re.search(r"\n[A-Z][A-Z /]+MODELS", rest)
    section = rest[: nxt.start()] if nxt else rest

    windows: list[QuotaWindow] = []
    for kind, remaining, refresh in _AGY_LIMIT_RE.findall(section):
        label = "weekly" if kind.lower().startswith("weekly") else "5h"
        used = round(100.0 - float(remaining), 2) if remaining else None
        windows.append(
            QuotaWindow(
                label=label,
                used_percent=used,
                resets_at=_duration_to_resets_at(refresh) if refresh.strip() else None,
            )
        )
    return windows


def _probe_agy(binary: str, timeout: float = 32.0) -> QuotaResult:
    path = _resolve_cli(binary)
    if path is None:
        return QuotaResult("agy", False, error=f"binary {binary!r} not on PATH")
    from . import _ptyusage

    if not _ptyusage.supported():
        return QuotaResult("agy", False, error="PTY scrape unsupported on this platform")
    text = _ptyusage.capture_screen(
        [path],
        cwd=os.path.expanduser("~"),
        script=[(9.0, "/usage"), (10.0, "\r"), (12.0, "\r"), (14.0, "\r")],
        total_seconds=timeout,
    )
    windows = _parse_agy_usage(text)
    if not windows:
        return QuotaResult(
            "agy", False, error="could not parse /usage screen (format changed?)"
        )
    return QuotaResult(
        "agy",
        available=True,
        confidence="estimated",
        plan="Gemini",
        windows=windows,
        note="GEMINI MODELS group, scraped from /usage (approximate)",
    )


_DISPATCH = {
    "codex": lambda binary: _probe_codex(binary),
    "claude": lambda binary: _probe_claude(binary),
    "agy": lambda binary: _probe_agy(binary),
    "grok": lambda binary: _probe_unavailable(
        "grok",
        "needs a `grok usage-json` helper reusing its internal billing handler",
    ),
}


def probe_quota(agent_id: str, binary: str | None = None) -> QuotaResult:
    """Best-effort quota probe for one agent. Never raises."""
    fn = _DISPATCH.get(agent_id)
    if fn is None:
        return QuotaResult(agent_id, False, error=f"no quota probe for agent {agent_id!r}")
    try:
        return fn(binary or agent_id)
    except Exception as exc:  # noqa: BLE001 — a probe must never break the caller
        return QuotaResult(agent_id, False, error=f"probe crashed: {exc}")


def quota_summary(agent_ids: list[str] | tuple[str, ...] | None = None) -> list[QuotaResult]:
    """Probe each agent, one at a time.

    The claude/agy probes drive an interactive TUI on a pseudo-terminal;
    running two of those at once makes the fixed-delay keystroke script
    race the slower-starting UI, so keep it sequential.
    """
    return [probe_quota(a) for a in (agent_ids or QUOTA_AGENTS)]


# ---------------------------------------------------------------------------
# Snapshot persistence (written by the poller, read by the GUI)
# ---------------------------------------------------------------------------


def save_snapshot(results: list[QuotaResult], path=None):
    from .utils.paths import quota_snapshot_file

    p = path or quota_snapshot_file()
    payload = {
        "generated_at": time.time(),
        "agents": {r.agent: r.to_dict() for r in results},
    }
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return p


def load_snapshot(path=None) -> dict:
    from .utils.paths import quota_snapshot_file

    p = path or quota_snapshot_file()
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
