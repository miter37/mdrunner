"""Headless tests for standalone quota alerts (edge trigger + persistence)."""

from __future__ import annotations

import json
import time

from mdrunner.alerts import (
    QuotaAlert,
    evaluate_alerts,
    load_alerts,
    save_alerts,
)
from mdrunner.config import QuotaClause, QuotaCondition


def _cond(**kw) -> QuotaCondition:
    base = {
        "weekly_used": QuotaClause(False, 90.0),
        "weekly_reset": QuotaClause(False, 24.0),
        "fivehour_used": QuotaClause(False, 90.0),
        "fivehour_reset": QuotaClause(False, 3.0),
    }
    base.update(kw)
    return QuotaCondition(**base)


def _snap(agent: str, used: float, when: float) -> dict:
    return {
        "agents": {
            agent: {
                "agent": agent,
                "available": True,
                "observed_at": when,
                "windows": [
                    {
                        "label": "weekly",
                        "used_percent": used,
                        "resets_at": when + 3600,
                        "window_minutes": 10080,
                    }
                ],
            }
        }
    }


def _alert(**kw) -> QuotaAlert:
    kw.setdefault("id", "a1")
    kw.setdefault("name", "weekly high")
    kw.setdefault("agent", "claude")
    kw.setdefault("condition", _cond(weekly_used=QuotaClause(True, 90.0)))
    return QuotaAlert(**kw)


def test_edge_fires_once_then_rearms(tmp_path) -> None:
    state_p = tmp_path / "alert-state.json"
    a = _alert()

    fires, _st = evaluate_alerts([a], _snap("claude", 95.0, time.time()), state_path_=state_p)
    assert [f.alert.id for f in fires] == ["a1"]

    # still true → no second fire
    fires, _st = evaluate_alerts([a], _snap("claude", 96.0, time.time()), state_path_=state_p)
    assert fires == []

    # false → re-arm, and no fire
    fires, st = evaluate_alerts([a], _snap("claude", 10.0, time.time()), state_path_=state_p)
    assert fires == [] and st.get("armed:a1") is True

    # true again → fires again
    fires, _st = evaluate_alerts([a], _snap("claude", 95.0, time.time()), state_path_=state_p)
    assert [f.alert.id for f in fires] == ["a1"]


def test_unknown_never_fires_and_keeps_state(tmp_path) -> None:
    state_p = tmp_path / "alert-state.json"
    state_p.write_text(json.dumps({"armed:a1": True}), encoding="utf-8")
    a = _alert()
    fires, st = evaluate_alerts([a], {"agents": {}}, state_path_=state_p)
    assert fires == []
    assert st.get("armed:a1") is True  # untouched


def test_disabled_alert_ignored(tmp_path) -> None:
    state_p = tmp_path / "alert-state.json"
    fires, _ = evaluate_alerts(
        [_alert(enabled=False)], _snap("claude", 95.0, time.time()), state_path_=state_p
    )
    assert fires == []


def test_roundtrip_and_skip_corrupt(tmp_path) -> None:
    p = tmp_path / "alerts.json"
    save_alerts(
        [
            _alert(message="codex 주간 사용량이 한도에 근접했습니다."),
            _alert(id="a2", agent="grok"),
        ],
        p,
    )
    loaded = load_alerts(p)
    assert [(a.id, a.agent) for a in loaded] == [("a1", "claude"), ("a2", "grok")]
    assert loaded[0].message == "codex 주간 사용량이 한도에 근접했습니다."
    # legacy entries without a message load fine (empty string)
    legacy = _alert(id="a3")
    save_alerts([legacy], p)
    raw = json.loads(p.read_text(encoding="utf-8"))
    del raw["alerts"][0]["message"]
    p.write_text(json.dumps(raw), encoding="utf-8")
    assert load_alerts(p)[0].message == ""
    # corrupt entries are skipped, healthy ones survive
    save_alerts([_alert(), _alert(id="a2", agent="grok")], p)
    raw = json.loads(p.read_text(encoding="utf-8"))
    raw["alerts"].append({"id": "bad", "agent": "nope"})
    raw["alerts"].append({"id": "bad2", "agent": "claude"})  # no clauses
    p.write_text(json.dumps(raw), encoding="utf-8")
    assert [a.id for a in load_alerts(p)] == ["a1", "a2"]


def test_lte_operator(tmp_path) -> None:
    state_p = tmp_path / "alert-state.json"
    a = _alert(condition=_cond(weekly_used=QuotaClause(True, 20.0, "lte")))
    fires, _ = evaluate_alerts([a], _snap("claude", 10.0, time.time()), state_path_=state_p)
    assert [f.alert.id for f in fires] == ["a1"]


def test_format_quota_alert_prefers_registered_message() -> None:
    from mdrunner.telegram import format_quota_alert

    body = format_quota_alert("codex wkly", "codex", "used 5% (need ≤90%)", "한도 근접!")
    assert body.splitlines()[:2] == ["mdrunner: quota alert 'codex wkly' fired", "agent: codex"]
    assert body.splitlines()[2] == "한도 근접!"
    assert "used 5%" not in body
    # no message registered → raw verdict kept as before
    legacy = format_quota_alert("codex wkly", "codex", "used 5% (need ≤90%)", "")
    assert legacy.endswith("used 5% (need ≤90%)")
