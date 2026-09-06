"""Unit tests for the run gate (min re-run interval + quota condition)."""

from __future__ import annotations

import time

from mdrunner.config import MinRerunInterval, QuotaClause, QuotaCondition, Schedule, Task
from mdrunner.quota_gate import available_windows, check_task_gate


def _task(**kw) -> Task:
    base = dict(
        id="t1",
        name="T1",
        agent="codex",
        prompt_file="/tmp/x.md",
        schedule=Schedule(mode="daily", time="02:00"),
    )
    base.update(kw)
    return Task(**base)


def _cond(**clauses) -> QuotaCondition:
    """_cond(weekly_used=(True, 90), fivehour_reset=(True, 3), on_unknown="skip").

    A clause spec may be ``(enabled, value)`` or ``(enabled, value, op)``.
    """
    on_unknown = clauses.pop("on_unknown", "skip")
    kw = {}
    for attr, spec in clauses.items():
        enabled, value, *rest = spec
        op = rest[0] if rest else "gte"
        kw[attr] = QuotaClause(enabled=enabled, value=value, op=op)
    return QuotaCondition(on_unknown=on_unknown, **kw)


def _snapshot(agent: str, windows: list[dict], *, available: bool = True) -> dict:
    return {
        "generated_at": time.time(),
        "agents": {
            agent: {
                "available": available,
                "observed_at": time.time(),
                "windows": windows,
            }
        },
    }


# --------------------------------------------------------------- available_windows


def test_available_windows_static_fallback():
    assert available_windows("codex") == ("5h", "weekly")
    assert available_windows("grok") == ("weekly",)
    assert available_windows("opencode") == ()


def test_available_windows_from_snapshot():
    snap = _snapshot("codex", [{"label": "weekly", "used_percent": 10}])
    assert available_windows("codex", snap) == ("weekly",)


# ------------------------------------------------------------------ min re-run gate


def test_min_rerun_blocks_recent_run():
    t = _task(min_rerun_interval=MinRerunInterval(enabled=True, hours=6))
    d = check_task_gate(t, last_run_at=time.time() - 3600, snapshot=None)
    assert not d.allowed
    assert "min re-run interval" in d.reason


def test_min_rerun_allows_after_window():
    t = _task(min_rerun_interval=MinRerunInterval(enabled=True, hours=6))
    d = check_task_gate(t, last_run_at=time.time() - 7 * 3600, snapshot=None)
    assert d.allowed


def test_min_rerun_disabled_is_ignored():
    t = _task(min_rerun_interval=MinRerunInterval(enabled=False, hours=6))
    d = check_task_gate(t, last_run_at=time.time() - 60, snapshot=None)
    assert d.allowed


def test_manual_bypasses_all_gates():
    t = _task(
        min_rerun_interval=MinRerunInterval(enabled=True, hours=6),
        quota_condition=_cond(weekly_used=(True, 90)),
    )
    d = check_task_gate(t, last_run_at=time.time() - 60, snapshot=None, is_manual=True)
    assert d.allowed


# ------------------------------------------------------------------- quota gate: used %


def test_used_clause_met_allows():
    t = _task(
        min_rerun_interval=MinRerunInterval(enabled=False),
        quota_condition=_cond(weekly_used=(True, 90)),
    )
    snap = _snapshot("codex", [{"label": "weekly", "used_percent": 95}])
    assert check_task_gate(t, last_run_at=None, snapshot=snap).allowed


def test_used_clause_below_threshold_skips():
    t = _task(
        min_rerun_interval=MinRerunInterval(enabled=False),
        quota_condition=_cond(weekly_used=(True, 90)),
    )
    snap = _snapshot("codex", [{"label": "weekly", "used_percent": 40}])
    assert not check_task_gate(t, last_run_at=None, snapshot=snap).allowed


def test_used_clause_lte_operator_allows_when_below():
    """op='lte' flips the comparison: run only while usage stays low."""
    t = _task(
        min_rerun_interval=MinRerunInterval(enabled=False),
        quota_condition=_cond(weekly_used=(True, 20, "lte")),
    )
    low = _snapshot("codex", [{"label": "weekly", "used_percent": 12}])
    assert check_task_gate(t, last_run_at=None, snapshot=low).allowed
    high = _snapshot("codex", [{"label": "weekly", "used_percent": 55}])
    assert not check_task_gate(t, last_run_at=None, snapshot=high).allowed


def test_multiple_used_clauses_are_anded():
    t = _task(
        min_rerun_interval=MinRerunInterval(enabled=False),
        quota_condition=_cond(weekly_used=(True, 90), fivehour_used=(True, 90)),
    )
    both_hot = _snapshot(
        "codex",
        [{"label": "5h", "used_percent": 92}, {"label": "weekly", "used_percent": 91}],
    )
    assert check_task_gate(t, last_run_at=None, snapshot=both_hot).allowed
    one_cold = _snapshot(
        "codex",
        [{"label": "5h", "used_percent": 10}, {"label": "weekly", "used_percent": 91}],
    )
    assert not check_task_gate(t, last_run_at=None, snapshot=one_cold).allowed


def test_unchecked_clause_is_ignored():
    """Only the enabled clause matters; the disabled 5h one does not."""
    t = _task(
        min_rerun_interval=MinRerunInterval(enabled=False),
        quota_condition=_cond(weekly_used=(True, 90), fivehour_used=(False, 99)),
    )
    snap = _snapshot(
        "codex",
        [{"label": "5h", "used_percent": 1}, {"label": "weekly", "used_percent": 95}],
    )
    assert check_task_gate(t, last_run_at=None, snapshot=snap).allowed


# ------------------------------------------------------------- quota gate: reset time


def test_reset_clause_met_when_window_refreshes_soon():
    t = _task(
        min_rerun_interval=MinRerunInterval(enabled=False),
        quota_condition=_cond(weekly_reset=(True, 6)),  # resets within 6h
    )
    soon = _snapshot(
        "codex",
        [{"label": "weekly", "used_percent": 20, "seconds_until_reset": 2 * 3600}],
    )
    assert check_task_gate(t, last_run_at=None, snapshot=soon).allowed
    later = _snapshot(
        "codex",
        [{"label": "weekly", "used_percent": 20, "seconds_until_reset": 30 * 3600}],
    )
    assert not check_task_gate(t, last_run_at=None, snapshot=later).allowed


def test_reset_clause_derives_seconds_from_resets_at():
    t = _task(
        min_rerun_interval=MinRerunInterval(enabled=False),
        quota_condition=_cond(weekly_reset=(True, 5)),
    )
    snap = _snapshot(
        "codex",
        [{"label": "weekly", "used_percent": 10, "resets_at": time.time() + 3600}],
    )
    assert check_task_gate(t, last_run_at=None, snapshot=snap).allowed


def test_used_and_reset_clauses_together():
    t = _task(
        min_rerun_interval=MinRerunInterval(enabled=False),
        quota_condition=_cond(weekly_used=(True, 80), weekly_reset=(True, 12)),
    )
    hot_and_soon = _snapshot(
        "codex",
        [{"label": "weekly", "used_percent": 88, "seconds_until_reset": 4 * 3600}],
    )
    assert check_task_gate(t, last_run_at=None, snapshot=hot_and_soon).allowed
    hot_but_far = _snapshot(
        "codex",
        [{"label": "weekly", "used_percent": 88, "seconds_until_reset": 40 * 3600}],
    )
    assert not check_task_gate(t, last_run_at=None, snapshot=hot_but_far).allowed


# --------------------------------------------------------------------- on_unknown


def test_incomplete_data_uses_on_unknown(monkeypatch):
    import mdrunner.quota_gate as qg

    monkeypatch.setattr(qg, "_agent_windows_now", lambda a, s: ([], False, "no data"))
    skip = _task(
        min_rerun_interval=MinRerunInterval(enabled=False),
        quota_condition=_cond(weekly_used=(True, 90), on_unknown="skip"),
    )
    run = _task(
        min_rerun_interval=MinRerunInterval(enabled=False),
        quota_condition=_cond(weekly_used=(True, 90), on_unknown="run"),
    )
    assert not check_task_gate(skip, last_run_at=None, snapshot=None).allowed
    assert check_task_gate(run, last_run_at=None, snapshot=None).allowed


def test_one_clause_unreadable_falls_to_on_unknown():
    """weekly used is fine, but the 5h window isn't reported at all."""
    t = _task(
        min_rerun_interval=MinRerunInterval(enabled=False),
        quota_condition=_cond(weekly_used=(True, 90), fivehour_used=(True, 90), on_unknown="skip"),
    )
    snap = _snapshot("codex", [{"label": "weekly", "used_percent": 99}])
    d = check_task_gate(t, last_run_at=None, snapshot=snap)
    assert not d.allowed
    assert "incomplete" in d.reason


def test_probe_crash_is_caught(monkeypatch):
    def _boom(_agent):
        raise RuntimeError("pty exploded")

    monkeypatch.setattr("mdrunner.quota.probe_quota", _boom)
    skip = _task(
        min_rerun_interval=MinRerunInterval(enabled=False),
        quota_condition=_cond(weekly_used=(True, 90), on_unknown="skip"),
    )
    run = _task(
        min_rerun_interval=MinRerunInterval(enabled=False),
        quota_condition=_cond(weekly_used=(True, 90), on_unknown="run"),
    )
    assert not check_task_gate(skip, last_run_at=None, snapshot=None).allowed
    assert check_task_gate(run, last_run_at=None, snapshot=None).allowed


# ----------------------------------------------------------------- ordering / interval


def test_interval_task_not_blocked_by_default_min_rerun():
    """A pure interval task must fire on its interval, not be throttled to 6h."""
    t = _task(
        schedule=Schedule(mode="interval", interval_minutes=30),
        min_rerun_interval=MinRerunInterval(enabled=True, hours=6),
    )
    d = check_task_gate(t, last_run_at=time.time() - 60, snapshot=None)
    assert d.allowed


def test_interval_task_with_quota_condition_still_honors_min_rerun():
    t = _task(
        schedule=Schedule(mode="interval", interval_minutes=30),
        min_rerun_interval=MinRerunInterval(enabled=True, hours=6),
        quota_condition=_cond(weekly_used=(True, 1)),
    )
    d = check_task_gate(t, last_run_at=time.time() - 600, snapshot=None)
    assert not d.allowed
    assert "min re-run interval" in d.reason


def test_future_last_run_does_not_wedge_the_task():
    """A corrupt/future last_run_at must not lock the task out forever."""
    t = _task(min_rerun_interval=MinRerunInterval(enabled=True, hours=6))
    d = check_task_gate(t, last_run_at=time.time() + 3600, snapshot=None)
    assert d.allowed


def test_past_reset_is_not_treated_as_upcoming():
    """A resets_at already in the past is stale — not 'resets within N hours'."""
    t = _task(
        min_rerun_interval=MinRerunInterval(enabled=False),
        quota_condition=_cond(weekly_reset=(True, 3), on_unknown="skip"),
    )
    snap = _snapshot(
        "codex",
        [{"label": "weekly", "used_percent": 20, "resets_at": time.time() - 10 * 3600}],
    )
    d = check_task_gate(t, last_run_at=None, snapshot=snap)
    assert not d.allowed
    assert "incomplete" in d.reason or "unknown" in d.reason or "past" in d.reason


def test_min_rerun_checked_before_quota():
    """A too-recent run is blocked even if the quota condition would pass."""
    t = _task(
        min_rerun_interval=MinRerunInterval(enabled=True, hours=6),
        quota_condition=_cond(weekly_used=(True, 1)),
    )
    snap = _snapshot("codex", [{"label": "weekly", "used_percent": 99}])
    d = check_task_gate(t, last_run_at=time.time() - 600, snapshot=snap)
    assert not d.allowed
    assert "min re-run interval" in d.reason
