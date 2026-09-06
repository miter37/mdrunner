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
    # how authoritative the underlying source is (NOT how fresh — see
    # ``observed_at`` for that; a stale API read still has source "api"):
    #   api          — a supported machine API (codex app-server)
    #   statusline   — the CLI's official statusLine JSON export
    #   billing-log  — the vendor's own on-disk billing snapshot (grok)
    #   screen-scrape— PTY read of the interactive /usage screen
    #   none         — nothing wired up
    source: str = "none"
    # authoritative | estimated | unavailable  (kept for back-compat / UI)
    confidence: str = "unavailable"
    checked_at: float = field(default_factory=time.time)  # when mdrunner probed
    observed_at: Optional[float] = None  # when the underlying data was produced
    plan: Optional[str] = None
    account: Optional[str] = None  # short hash of the signed-in account
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

    # Prefer the multi-bucket view keyed by limit id; fall back to the
    # back-compat single-bucket ``rateLimits``.
    by_id = result_payload.get("rateLimitsByLimitId") or {}
    rl = by_id.get("codex") or next(iter(by_id.values()), None) or (
        result_payload.get("rateLimits") or {}
    )
    windows: list[QuotaWindow] = []
    for key in ("primary", "secondary"):
        w = rl.get(key)
        if not w:
            continue
        mins = w.get("windowDurationMins")  # kept raw; label is only a hint
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
    if credits.get("availableCount"):  # availableCount is authoritative
        note = f"{credits['availableCount']} reset credit(s) available"

    acct = result_payload.get("accountId")
    return QuotaResult(
        agent="codex",
        available=bool(windows),
        source="api",
        confidence="authoritative" if windows else "unavailable",
        observed_at=time.time(),
        plan=rl.get("planType"),
        account=_acct_hash(acct) if acct else None,
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


# --- statusLine sink: claude/agy write rate-limit JSON here via a hook ------


def _sink_path(agent: str):
    from .utils.paths import state_dir

    return state_dir() / "quota-sink" / f"{agent}.json"


def _read_quota_sink(agent: str, *, fresh_after: float = 1800.0) -> Optional[QuotaResult]:
    """A `mdrunner quota-sink <agent>` statusLine hook writes the CLI's own
    rate-limit export here — instant, no PTY, no tokens. Returns a
    QuotaResult if the file is present and not older than ``fresh_after``."""
    try:
        raw = json.loads(_sink_path(agent).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    observed = raw.get("observed_at")
    windows = [
        QuotaWindow(
            label=w.get("label", "window"),
            used_percent=w.get("used_percent"),
            resets_at=w.get("resets_at"),
            window_minutes=w.get("window_minutes"),
        )
        for w in raw.get("windows", [])
    ]
    if not windows:
        return None
    stale = observed is not None and time.time() - observed > fresh_after
    return QuotaResult(
        agent,
        available=True,
        source="statusline",
        confidence="estimated" if stale else "authoritative",
        observed_at=observed,
        plan=raw.get("plan"),
        account=raw.get("account"),
        windows=windows,
        note="from statusLine export"
        + (f" · {int((time.time() - observed) // 60)} min old" if stale else ""),
    )


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


def _probe_claude(binary: str, timeout: float = 30.0) -> QuotaResult:
    sink = _read_quota_sink("claude")
    if sink is not None:
        return sink
    path = _resolve_cli(binary)
    if path is None:
        return QuotaResult("claude", False, error=f"binary {binary!r} not on PATH")
    from . import _ptyusage

    if not _ptyusage.supported():
        return QuotaResult("claude", False, error="PTY scrape unsupported on this platform")

    def _once() -> tuple:
        # Claude may open a "trust this folder?" prompt (default "No, exit") —
        # arrow-down to "Yes, I trust" + Enter; harmless keystrokes otherwise.
        # Steps wait for the screen to be ready instead of a fixed sleep.
        text = _ptyusage.capture_screen(
            [path],
            cwd=os.path.expanduser("~"),
            steps=[
                (("await", r"trust this folder|for shortcuts|Try \"|>\s", 14.0), "\x1b[B"),
                (0.4, "\r"),
                (("await", r"for shortcuts|Try \"|\?\s+for", 14.0), "/usage"),
                (0.35, "\r"),
                (1.6, "\r"),
            ],
            stop_when=r"Current\s+week[^\n]*\n[^\n]*?\d+\s*%\s*used",
            total_seconds=timeout,
        )
        return _parse_claude_usage(text), text

    windows: list[QuotaWindow] = []
    text = ""
    for _ in range(2):
        windows, text = _once()
        if windows:
            break
    if not windows:
        return QuotaResult(
            "claude", False, error="could not parse /usage screen (busy? format changed?)"
        )
    return QuotaResult(
        "claude",
        available=True,
        source="screen-scrape",
        confidence="estimated",
        observed_at=time.time(),
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
    sink = _read_quota_sink("agy")
    if sink is not None:
        return sink
    path = _resolve_cli(binary)
    if path is None:
        return QuotaResult("agy", False, error=f"binary {binary!r} not on PATH")
    from . import _ptyusage

    if not _ptyusage.supported():
        return QuotaResult("agy", False, error="PTY scrape unsupported on this platform")
    text = _ptyusage.capture_screen(
        [path],
        cwd=os.path.expanduser("~"),
        steps=[
            (("await", r"Navigate|for shortcuts|\bhelp\b|›|▌|esc to", 16.0), "/usage"),
            (0.4, "\r"),
            (2.0, "\r"),
            (2.0, "\r"),
        ],
        stop_when=r"Five Hour Limit|Weekly Limit",
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
        source="screen-scrape",
        confidence="estimated",
        observed_at=time.time(),
        plan="Gemini",
        windows=windows,
        note="GEMINI MODELS group, scraped from /usage (approximate)",
    )


# --- grok: read the billing snapshot grok writes to its own log ------------
#
# Grok Build logs `msg == "billing: fetched credits config"` to
# $GROK_HOME/logs/unified.jsonl with ctx.config.creditUsagePercent and
# ctx.config.currentPeriod.{type,end}. That is exactly used% + reset time,
# no auth/API needed. `/usage` in the TUI triggers a fresh fetch; we send it
# on a PTY and then re-read the *log* (not the screen), so a layout change
# can't break us.


def _grok_billing_log() -> "os.PathLike | str":
    home = os.environ.get("GROK_HOME") or os.path.expanduser("~/.grok")
    return os.path.join(home, "logs", "unified.jsonl")


def _parse_iso(s) -> Optional[float]:
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError):
        return None


def _acct_hash(value: str) -> str:
    import hashlib

    return hashlib.sha256(value.encode("utf-8", "replace")).hexdigest()[:12]


def _grok_account_id() -> Optional[str]:
    """Best-effort signed-in grok user id/email from ~/.grok/auth.json."""
    home = os.environ.get("GROK_HOME") or os.path.expanduser("~/.grok")
    try:
        auth = json.loads(open(os.path.join(home, "auth.json")).read())
    except (OSError, json.JSONDecodeError):
        return None
    for v in (auth.values() if isinstance(auth, dict) else []):
        if isinstance(v, dict):
            aid = v.get("user_id") or v.get("email") or v.get("principal_id")
            if aid:
                return str(aid)
    return None


def _read_grok_billing() -> Optional[dict]:
    """Newest billing snapshot from unified.jsonl → {ts, config, tier} | None.

    Reverse line scan from EOF so a rotated / huge log and a half-line at a
    read boundary don't matter.
    """
    marker = b"billing: fetched credits config"

    def _pick(raw: bytes):
        if marker not in raw:
            return None
        try:
            rec = json.loads(raw.decode("utf-8", "replace"))
        except ValueError:
            return None
        ctx = rec.get("ctx") or {}
        return {
            "ts": rec.get("ts"),
            "config": ctx.get("config") or {},
            "tier": ctx.get("subscriptionTier"),
        }

    try:
        with open(_grok_billing_log(), "rb") as fh:
            fh.seek(0, 2)
            end = fh.tell()
            buf = b""
            while end > 0:
                step = min(64 * 1024, end)
                end -= step
                fh.seek(end)
                buf = fh.read(step) + buf
                lines = buf.split(b"\n")
                buf = lines.pop(0)  # partial first line — unless end==0 (see below)
                for raw in reversed(lines):
                    hit = _pick(raw)
                    if hit:
                        return hit
            return _pick(buf)  # the very first line of the file
    except OSError:
        return None


def _grok_used_percent(cfg: dict) -> Optional[float]:
    """creditUsagePercent is officially Optional; fall back to the legacy
    used / monthlyLimit shape, and return None (never 0) when truly unknown."""
    v = cfg.get("creditUsagePercent")
    if isinstance(v, (int, float)):
        return round(float(v), 2)
    used, limit = cfg.get("used"), cfg.get("monthlyLimit")
    try:
        if used is not None and limit:
            return round(float(used) / float(limit) * 100.0, 2)
    except (TypeError, ValueError, ZeroDivisionError):
        pass
    return None


def _grok_result(entry: dict, *, fresh_after: float) -> QuotaResult:
    cfg = entry["config"]
    used = _grok_used_percent(cfg)
    period = cfg.get("currentPeriod") or {}
    label = "weekly" if "WEEKLY" in str(period.get("type", "")) else "period"
    observed = _parse_iso(entry.get("ts"))
    age = time.time() - (observed or 0.0)
    stale = age > fresh_after
    note = entry.get("tier") or "SuperGrok"
    if used is None:
        note += " · usage % not in snapshot"
    if stale:
        note += f" · snapshot {int(age // 60)} min old"
    acct = cfg.get("userId") or cfg.get("teamId") or _grok_account_id()
    return QuotaResult(
        "grok",
        available=True,
        source="billing-log",
        confidence=(
            "authoritative" if (used is not None and not stale)
            else "estimated"
        ),
        observed_at=observed,
        plan=entry.get("tier"),
        account=_acct_hash(str(acct)) if acct else None,
        windows=[
            QuotaWindow(
                label=label,
                used_percent=used,
                resets_at=_parse_iso(period.get("end")),
                window_minutes=10080 if label == "weekly" else None,
            )
        ],
        note=note,
    )


def _probe_grok(binary: str, timeout: float = 24.0) -> QuotaResult:
    FRESH = 300.0  # snapshot older than this triggers a refresh
    entry = _read_grok_billing()
    before_ts = _parse_iso(entry.get("ts")) if entry else None
    if before_ts is None or time.time() - before_ts > FRESH:
        start = time.time()
        path = _resolve_cli(binary)
        try:
            from . import _ptyusage

            if path and _ptyusage.supported():
                # `/usage` on a PTY makes grok re-fetch billing and append to
                # the log; we read the *log*, not the screen.
                _ptyusage.capture_screen(
                    [path],
                    cwd=os.path.expanduser("~"),
                    steps=[
                        (("await", r"Navigate|for shortcuts|\bhelp\b|›|▌|esc to", 12.0),
                         "/usage"),
                        (0.4, "\r"),
                    ],
                    stop_when=None,
                    total_seconds=min(timeout, 22.0),
                )
        except Exception:  # noqa: BLE001
            pass
        fresh = _read_grok_billing()
        fresh_ts = _parse_iso(fresh.get("ts")) if fresh else None
        # only accept the refresh if it produced a genuinely newer entry
        if fresh and fresh_ts is not None and fresh_ts >= start - 2:
            entry = fresh
    if not entry:
        return QuotaResult(
            "grok", False, source="none",
            error="no billing snapshot in ~/.grok/logs/unified.jsonl (run grok once)",
        )
    return _grok_result(entry, fresh_after=FRESH)


_DISPATCH = {
    "codex": lambda binary: _probe_codex(binary),
    "claude": lambda binary: _probe_claude(binary),
    "agy": lambda binary: _probe_agy(binary),
    "grok": lambda binary: _probe_grok(binary),
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
    """Write the snapshot, but never regress: if a probe just failed for an
    agent that had good data last time, keep the previous entry (marked
    stale) so a transient scrape miss doesn't blank the panel."""
    from .utils.paths import quota_snapshot_file

    p = path or quota_snapshot_file()
    prev = load_snapshot(p).get("agents", {})
    agents: dict[str, dict] = {}
    for r in results:
        cur = r.to_dict()
        old = prev.get(r.agent)
        # don't carry a previous entry forward across an account switch
        if old and r.account and old.get("account") and r.account != old.get("account"):
            old = None
        if not r.available and old and old.get("available") and old.get("windows"):
            old = dict(old)
            old["note"] = (old.get("note") or "").split(" — last seen")[0]
            when = time.strftime("%m-%d %H:%M", time.localtime(old.get("checked_at", time.time())))
            old["note"] = f"{old['note']} — last seen {when}".strip(" —")
            old["stale"] = True
            agents[r.agent] = old
        else:
            agents[r.agent] = cur
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps({"generated_at": time.time(), "agents": agents}, indent=2),
        encoding="utf-8",
    )
    return p


def load_snapshot(path=None) -> dict:
    from .utils.paths import quota_snapshot_file

    p = path or quota_snapshot_file()
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
