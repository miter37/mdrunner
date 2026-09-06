"""Unit tests for the run gate (min re-run interval + quota condition)."""

from __future__ import annotations

import time

from mdrunner.config import MinRerunInterval, QuotaCondition, Schedule, Task
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
        quota_condition=QuotaCondition(window="weekly", comparator=">=", percent=90),
    )
    d = check_task_gate(t, last_run_at=time.time() - 60, snapshot=None, is_manual=True)
    assert d.allowed


# ------------------------------------------------------------------- quota gate


def test_quota_condition_met_allows():
    t = _task(
        min_rerun_interval=MinRerunInterval(enabled=False),
        quota_condition=QuotaCondition(window="weekly", comparator=">=", percent=90, metric="used"),
    )
    snap = _snapshot("codex", [{"label": "weekly", "used_percent": 95}])
    d = check_task_gate(t, last_run_at=None, snapshot=snap)
    assert d.allowed


def test_quota_condition_below_threshold_skips():
    t = _task(
        min_rerun_interval=MinRerunInterval(enabled=False),
        quota_condition=QuotaCondition(window="weekly", comparator=">=", percent=90, metric="used"),
    )
    snap = _snapshot("codex", [{"label": "weekly", "used_percent": 40}])
    d = check_task_gate(t, last_run_at=None, snapshot=snap)
    assert not d.allowed


def test_quota_condition_remaining_metric():
    t = _task(
        min_rerun_interval=MinRerunInterval(enabled=False),
        quota_condition=QuotaCondition(window="weekly", comparator="<=", percent=10, metric="remaining"),
    )
    snap = _snapshot("codex", [{"label": "weekly", "used_percent": 95}])  # remaining = 5
    d = check_task_gate(t, last_run_at=None, snapshot=snap)
    assert d.allowed


def test_quota_window_all_requires_both():
    t = _task(
        min_rerun_interval=MinRerunInterval(enabled=False),
        quota_condition=QuotaCondition(window="all", comparator=">=", percent=90),
    )
    both_hot = _snapshot(
        "codex",
        [{"label": "5h", "used_percent": 92}, {"label": "weekly", "used_percent": 91}],
    )
    assert check_task_gate(t, last_run_at=None, snapshot=both_hot).allowed
    one_cold = _snapshot(
        "codex",
        [{"label": "5h", "used_percent": 92}, {"label": "weekly", "used_percent": 10}],
    )
    assert not check_task_gate(t, last_run_at=None, snapshot=one_cold).allowed


def test_quota_window_any_needs_one():
    t = _task(
        min_rerun_interval=MinRerunInterval(enabled=False),
        quota_condition=QuotaCondition(window="any", comparator=">=", percent=90),
    )
    snap = _snapshot(
        "codex",
        [{"label": "5h", "used_percent": 10}, {"label": "weekly", "used_percent": 99}],
    )
    assert check_task_gate(t, last_run_at=None, snapshot=snap).allowed


def test_on_unknown_skip_vs_run(monkeypatch):
    import mdrunner.quota_gate as qg

    monkeypatch.setattr(
        qg, "_agent_windows_now", lambda a, s: ([], False, "no data")
    )
    skip = _task(
        min_rerun_interval=MinRerunInterval(enabled=False),
        quota_condition=QuotaCondition(window="weekly", on_unknown="skip"),
    )
    run = _task(
        min_rerun_interval=MinRerunInterval(enabled=False),
        quota_condition=QuotaCondition(window="weekly", on_unknown="run"),
    )
    assert not check_task_gate(skip, last_run_at=None, snapshot=None).allowed
    assert check_task_gate(run, last_run_at=None, snapshot=None).allowed


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
        quota_condition=QuotaCondition(window="weekly", comparator=">=", percent=1),
    )
    d = check_task_gate(t, last_run_at=time.time() - 600, snapshot=None)
    assert not d.allowed
    assert "min re-run interval" in d.reason


def test_future_last_run_does_not_wedge_the_task():
    """A corrupt/future last_run_at must not lock the task out forever."""
    t = _task(min_rerun_interval=MinRerunInterval(enabled=True, hours=6))
    d = check_task_gate(t, last_run_at=time.time() + 3600, snapshot=None)
    assert d.allowed


def test_probe_crash_is_caught(monkeypatch):
    def _boom(_agent):
        raise RuntimeError("pty exploded")

    monkeypatch.setattr("mdrunner.quota.probe_quota", _boom)
    skip = _task(
        min_rerun_interval=MinRerunInterval(enabled=False),
        quota_condition=QuotaCondition(window="weekly", on_unknown="skip"),
    )
    run = _task(
        min_rerun_interval=MinRerunInterval(enabled=False),
        quota_condition=QuotaCondition(window="weekly", on_unknown="run"),
    )
    assert not check_task_gate(skip, last_run_at=None, snapshot=None).allowed
    assert check_task_gate(run, last_run_at=None, snapshot=None).allowed


def test_min_rerun_checked_before_quota():
    """A too-recent run is blocked even if the quota condition would pass."""
    t = _task(
        min_rerun_interval=MinRerunInterval(enabled=True, hours=6),
        quota_condition=QuotaCondition(window="weekly", comparator=">=", percent=1),
    )
    snap = _snapshot("codex", [{"label": "weekly", "used_percent": 99}])
    d = check_task_gate(t, last_run_at=time.time() - 600, snapshot=snap)
    assert not d.allowed
    assert "min re-run interval" in d.reason
