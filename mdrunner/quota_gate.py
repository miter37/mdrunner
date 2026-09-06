"""Decide whether a task may run right now.

Two gates, checked in order (cheap first):

1. ``min_rerun_interval`` — has enough time passed since the task last
   *actually* ran? (Skips never advance that clock.)
2. ``quota_condition`` — is the task's own agent's quota at the required
   level? Reads the latest snapshot; if that agent's entry is stale it does
   one live probe of just that agent.

Manual "Run now" bypasses both.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

from .config import QUOTA_CAPABLE_AGENTS, Task

# how old the snapshot may be before the gate re-probes that one agent
_SNAPSHOT_MAX_AGE = 900.0  # 15 min

# fallback when no snapshot exists yet
_STATIC_WINDOWS = {
    "codex": ("5h", "weekly"),
    "claude": ("5h", "weekly"),
    "agy": ("5h", "weekly"),
    "grok": ("weekly",),
}


@dataclass
class GateDecision:
    allowed: bool
    reason: str


def available_windows(agent: str, snapshot: Optional[dict] = None) -> tuple[str, ...]:
    """Which quota windows this agent reports — for the condition UI."""
    if agent not in QUOTA_CAPABLE_AGENTS:
        return ()
    entry = ((snapshot or {}).get("agents") or {}).get(agent) if snapshot else None
    labels = [w.get("label") for w in (entry or {}).get("windows", []) if w.get("label")]
    labels = [x for x in labels if x in ("5h", "weekly")]
    return tuple(dict.fromkeys(labels)) or _STATIC_WINDOWS.get(agent, ("weekly",))


def _as_window_dict(w) -> dict:
    """Coerce a QuotaWindow (dataclass / obj / dict) to a plain dict."""
    if isinstance(w, dict):
        return w
    for attr in ("__dict__", "_asdict"):
        val = getattr(w, attr, None)
        if callable(val):
            return dict(val())
        if isinstance(val, dict):
            return dict(val)
    return {
        "label": getattr(w, "label", None),
        "used_percent": getattr(w, "used_percent", None),
        "resets_at": getattr(w, "resets_at", None),
        "window_minutes": getattr(w, "window_minutes", None),
    }


def _agent_windows_now(agent: str, snapshot: Optional[dict]):
    """(list[window dict], data_ok, note). One live probe if the snapshot
    entry for this agent is missing or stale."""
    entry = ((snapshot or {}).get("agents") or {}).get(agent) if snapshot else None
    observed = (entry or {}).get("observed_at")
    fresh = observed is not None and time.time() - observed <= _SNAPSHOT_MAX_AGE
    if entry and entry.get("available") and fresh:
        return [_as_window_dict(w) for w in (entry.get("windows") or [])], True, "snapshot"
    # stale / missing -> probe just this agent
    try:
        from .quota import probe_quota

        r = probe_quota(agent)
    except Exception as exc:  # noqa: BLE001 — a probe crash must not wedge the gate
        return [], False, f"quota probe failed: {exc}"
    if not r.available:
        return [], False, r.error or "quota unavailable"
    return [_as_window_dict(w) for w in r.windows], True, "live"


def _clause_result(
    clause_kind: str, value: float, op: str, w: Optional[dict], now: float
):
    """(result | None, detail str). None = data missing for this clause.

    ``op`` is ``"gte"``/``"lte"`` for ``used`` clauses; ``reset`` ignores it
    and always compares ``<=`` hours.
    """
    if not w:
        return None, "no data"
    if clause_kind == "used":
        up = w.get("used_percent")
        if up is None:
            return None, "used% not reported"
        if op == "lte":
            return up <= value, f"used {up:g}% (need ≤{value:g}%)"
        return up >= value, f"used {up:g}% (need ≥{value:g}%)"
    # reset: how long until the window refreshes
    secs = w.get("seconds_until_reset")
    if secs is None and w.get("resets_at") is not None:
        secs = w["resets_at"] - now
    if secs is None:
        return None, "reset time not reported"
    # A stamp already well in the past is stale — we don't know the *next*
    # reset, so this clause is unknown rather than "resets in -10h ≤ 3h".
    if secs < -60:
        return None, "reset time is in the past"
    secs = max(0.0, float(secs))
    hrs = secs / 3600.0
    return hrs <= value, f"resets in {hrs:.1f}h (need ≤{value:g}h)"


def _quota_met(task: Task, snapshot: Optional[dict]) -> GateDecision:
    c = task.quota_condition
    assert c is not None
    now = time.time()
    windows, data_ok, note = _agent_windows_now(task.agent, snapshot)
    if not data_ok:
        allow = c.on_unknown == "run"
        return GateDecision(allow, f"quota unknown ({note}) → {'run' if allow else 'skip'}")

    by_label = {w.get("label"): w for w in windows}
    results: list[bool] = []
    details: list[str] = []
    unknown: list[str] = []
    for label, window, kind, value, op in c.active():
        res, detail = _clause_result(kind, value, op, by_label.get(window), now)
        if res is None:
            unknown.append(f"{label} ({detail})")
        else:
            results.append(res)
            details.append(f"{label}: {detail} → {'ok' if res else 'no'}")

    if unknown:
        allow = c.on_unknown == "run"
        return GateDecision(
            allow,
            f"quota data incomplete [{', '.join(unknown)}] → {'run' if allow else 'skip'}",
        )

    met = all(results)
    verb = "all clauses met" if met else "clause(s) not met"
    return GateDecision(met, f"{c.describe(task.agent)} — {verb}: {'; '.join(details)}")


def check_task_gate(
    task: Task,
    *,
    last_run_at: Optional[float],
    snapshot: Optional[dict] = None,
    is_manual: bool = False,
    now: Optional[float] = None,
) -> GateDecision:
    if is_manual:
        return GateDecision(True, "manual run (gates bypassed)")
    now = now if now is not None else time.time()

    mri = task.min_rerun_interval
    # For a pure `interval` task the schedule interval IS the re-run floor, so a
    # separate min-rerun gate would just fight it (and timer jitter could make
    # every other fire skip). Only enforce it there when a quota condition is
    # also present (Case A gate — the user opted into an extra floor).
    enforce_mri = (
        mri.enabled
        and last_run_at is not None
        and last_run_at <= now  # a future stamp = corrupt state -> don't wedge
        and not (task.schedule.mode == "interval" and task.quota_condition is None)
    )
    if enforce_mri:
        elapsed = now - last_run_at
        need = mri.hours * 3600.0
        if elapsed < need:
            ago = _human(elapsed)
            return GateDecision(
                False,
                f"min re-run interval: last run {ago} ago, need {mri.hours:g}h",
            )

    if task.quota_condition is not None:
        return _quota_met(task, snapshot)

    return GateDecision(True, "no gate")


def _human(seconds: float) -> str:
    s = int(max(0, seconds))
    h, s = divmod(s, 3600)
    m = s // 60
    if h:
        return f"{h}h {m}m"
    return f"{m}m"
