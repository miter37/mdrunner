import stat
import time
from pathlib import Path

import pytest

from mdrunner import quota
from mdrunner.config import settings_from_dict, settings_to_dict
from mdrunner.quota import (
    QuotaResult,
    QuotaWindow,
    _window_label,
    fmt_reset,
    load_snapshot,
    probe_quota,
    quota_summary,
    save_snapshot,
)


@pytest.fixture(autouse=True)
def _no_real_probes(monkeypatch, tmp_path):
    """Never touch a real agent CLI / ~/.grok log / statusLine sink."""
    import mdrunner._ptyusage as pty

    monkeypatch.setattr(pty, "capture_screen", lambda *a, **k: "")
    monkeypatch.setenv("GROK_HOME", str(tmp_path / "no-grok"))  # empty -> unavailable
    monkeypatch.setenv("RUNCHER_STATE_DIR", str(tmp_path / "state"))  # no quota-sink files


def test_window_label():
    assert _window_label(300) == "5h"
    assert _window_label(10080) == "weekly"
    assert _window_label(720) == "12h"
    assert _window_label(2880) == "2d"
    assert _window_label(None) == "window"


def test_fmt_reset():
    assert fmt_reset(None) == "—"
    assert fmt_reset(0) == "0m"
    assert fmt_reset(90) == "1m"
    assert fmt_reset(3 * 3600 + 20 * 60) == "3h 20m"
    assert fmt_reset(2 * 86400 + 5 * 3600) == "2d 5h"


def test_seconds_until_reset():
    w = QuotaWindow("weekly", used_percent=40, resets_at=time.time() + 3600)
    assert 3500 < w.seconds_until_reset <= 3600
    assert QuotaWindow("x").seconds_until_reset is None


def test_unavailable_probes_have_confidence():
    # claude/agy fall back to unavailable when the PTY scrape yields nothing;
    # grok is unavailable when there's no ~/.grok billing log (GROK_HOME stub).
    for agent in ("claude", "grok", "agy"):
        r = probe_quota(agent)
        assert r.agent == agent
        assert r.available is False
        assert r.confidence == "unavailable"
        assert r.error


def test_grok_reads_billing_log(monkeypatch, tmp_path):
    log = tmp_path / "gh" / "logs" / "unified.jsonl"
    log.parent.mkdir(parents=True)
    fresh = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())
    log.write_text(
        '{"ts":"2026-01-01T00:00:00Z","msg":"other"}\n'
        '{"ts":"' + fresh + '","msg":"billing: fetched credits config","ctx":{'
        '"config":{"creditUsagePercent":64.2,"currentPeriod":{'
        '"type":"USAGE_PERIOD_TYPE_WEEKLY","start":"2026-09-01T03:00:00Z",'
        '"end":"2099-09-08T03:00:00Z"}},"subscriptionTier":"SuperGrok Heavy"}}\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("GROK_HOME", str(tmp_path / "gh"))
    r = quota._probe_grok("grok")
    assert r.available and r.confidence == "authoritative"
    assert r.plan == "SuperGrok Heavy"
    w = r.windows[0]
    assert w.label == "weekly" and w.used_percent == 64.2
    assert w.resets_at and w.resets_at > time.time()


def test_grok_missing_log_is_unavailable(monkeypatch, tmp_path):
    monkeypatch.setenv("GROK_HOME", str(tmp_path / "nope"))
    r = quota._probe_grok("grok")
    assert not r.available and "billing snapshot" in r.error


def test_grok_missing_usage_percent_is_none_not_zero(monkeypatch, tmp_path):
    log = tmp_path / "gh" / "logs" / "unified.jsonl"
    log.parent.mkdir(parents=True)
    fresh = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())
    log.write_text(
        '{"ts":"' + fresh + '","msg":"billing: fetched credits config","ctx":{"config":{'
        '"currentPeriod":{"type":"USAGE_PERIOD_TYPE_WEEKLY","end":"2099-01-01T00:00:00Z"},'
        '"isUnifiedBillingUser":true},"subscriptionTier":"SuperGrok"}}\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("GROK_HOME", str(tmp_path / "gh"))
    r = quota._probe_grok("grok")
    assert r.available  # we still have a reset time
    assert r.windows[0].used_percent is None  # NOT 0.0
    assert r.confidence == "estimated"
    assert "not in snapshot" in r.note


def test_quota_sink_extracts_claude_and_agy():
    from mdrunner import quota_sink

    c = quota_sink._extract_claude(
        {
            "rate_limits": {
                "five_hour": {"used_percentage": 20, "resets_at": 111},
                "seven_day": {"used_percentage": 55, "resets_at": 222},
            },
            "subscription": "Max",
        }
    )
    assert c["plan"] == "Max"
    assert {(w["label"], w["used_percent"]) for w in c["windows"]} == {
        ("5h", 20.0),
        ("weekly", 55.0),
    }

    a = quota_sink._extract_agy(
        {
            "quota": {
                "gemini-weekly": {"remaining_fraction": 0.75, "reset_time": "2099-01-01T00:00:00Z"},
                "gemini-five-hour": {"remaining_fraction": 1.0, "reset_in_seconds": 3600},
            },
            "plan_tier": "Pro",
            "email": "x@y.z",
        }
    )
    aw = {w["label"]: w for w in a["windows"]}
    assert aw["weekly"]["used_percent"] == 25.0
    assert aw["5h"]["used_percent"] == 0.0 and aw["5h"]["resets_at"] is not None
    assert a["plan"] == "Pro" and a["account"]


def test_pty_unsupported_marks_sink_setup(monkeypatch):
    """On platforms without a PTY (e.g. Windows), claude/agy degrade with a
    flag the GUI turns into sink-setup guidance — PTY probing itself is
    untouched on Linux/macOS."""
    from mdrunner import quota

    monkeypatch.setattr("mdrunner._ptyusage.supported", lambda: False)
    for agent, probe in (("claude", quota._probe_claude), ("agy", quota._probe_agy)):
        monkeypatch.setattr(quota, "_resolve_cli", lambda _b, _a=agent: f"/usr/bin/{_a}")
        r = probe(agent)
        assert r.available is False
        assert r.needs_sink is True
        d = r.to_dict()
        assert d["needs_sink"] is True  # survives the snapshot round-trip


def test_quota_sink_reads_back_via_probe(monkeypatch, tmp_path):
    import io
    import sys as _sys

    from mdrunner import quota_sink

    monkeypatch.setenv("RUNCHER_STATE_DIR", str(tmp_path / "st"))
    monkeypatch.setattr(
        _sys,
        "stdin",
        type(
            "S",
            (),
            {
                "buffer": io.BytesIO(
                    b'{"rate_limits":{"seven_day":{"used_percentage":88,"resets_at":9}}}'
                )
            },
        )(),
    )
    assert quota_sink.run(["claude"]) == 0  # acts as the statusLine hook
    r = quota._probe_claude("claude")  # now reads the file, no PTY
    assert r.available and r.source == "statusline"
    assert r.windows[0].used_percent == 88.0


def test_claude_agy_parsers_on_sample_text():
    from mdrunner.quota import _parse_agy_usage, _parse_claude_usage

    claude = (
        "Current session██ 24%usedResets 2:10pm (Asia/Seoul)"
        "Current week (all models)█ 2%usedResets Sep 12, 11pm (Asia/Seoul)"
    )
    cw = {w.label: w for w in _parse_claude_usage(claude)}
    assert cw["5h"].used_percent == 24.0
    assert cw["weekly"].used_percent == 2.0
    assert cw["5h"].resets_at and cw["weekly"].resets_at

    agy = (
        "GEMINI MODELS\n Weekly Limit Remaining\n [###] 88.71%\n"
        " 89% remaining · Refreshes in 115h 39m\n Five Hour Limit Remaining\n"
        " [###] 89.58%\n 90% remaining · Refreshes in 39m\n"
        "CLAUDE AND GPT MODELS\n Weekly Limit Remaining\n [###] 100.00%\n Quota available\n"
    )
    aw = {w.label: w for w in _parse_agy_usage(agy)}
    assert aw["weekly"].used_percent == pytest.approx(11.29)
    assert aw["5h"].used_percent == pytest.approx(10.42)
    assert aw["5h"].resets_at


def test_probe_unknown_agent():
    r = probe_quota("nope")
    assert not r.available and "no quota probe" in r.error


def test_snapshot_roundtrip(tmp_path: Path):
    p = tmp_path / "quota.json"
    results = [
        QuotaResult(
            "codex",
            available=True,
            confidence="authoritative",
            plan="plus",
            windows=[QuotaWindow("weekly", 40, time.time() + 100, 10080)],
        ),
        QuotaResult("claude", available=False, error="x"),
    ]
    save_snapshot(results, p)
    snap = load_snapshot(p)
    assert set(snap["agents"]) == {"codex", "claude"}
    assert snap["agents"]["codex"]["windows"][0]["label"] == "weekly"
    assert "generated_at" in snap


def test_load_snapshot_missing(tmp_path: Path):
    assert load_snapshot(tmp_path / "nope.json") == {}


def test_quota_poll_config_roundtrip():
    raw = {
        "agents": {},
        "quota_poll": {"enabled": True, "interval_minutes": 45, "agents": ["codex"]},
    }
    s = settings_from_dict(raw)
    assert s.quota_poll.enabled is True
    assert s.quota_poll.interval_minutes == 45
    assert s.quota_poll.agents == ["codex"]
    # default when absent
    s2 = settings_from_dict({"agents": {}})
    assert s2.quota_poll.enabled is False
    assert s2.quota_poll.interval_minutes == 10
    # survives to_dict -> from_dict
    assert settings_from_dict(settings_to_dict(s)).quota_poll.interval_minutes == 45


# --- codex probe against a fake app-server -----------------------------------

FAKE_APP_SERVER = r"""#!/usr/bin/env python3
import sys, json
def readline():
    return sys.stdin.readline()
while True:
    line = readline()
    if not line:
        break
    line = line.strip()
    if not line:
        continue
    try:
        msg = json.loads(line)
    except Exception:
        continue
    mid = msg.get("id")
    method = msg.get("method")
    if method == "initialize":
        print(json.dumps({"id": mid, "result": {"codexHome": "/x"}}), flush=True)
    elif method == "account/rateLimits/read":
        print(json.dumps({"id": mid, "result": {"rateLimits": {
            "planType": "plus",
            "primary": {"usedPercent": 12, "windowDurationMins": 300, "resetsAt": 4102444800},
            "secondary": {"usedPercent": 63, "windowDurationMins": 10080, "resetsAt": 4102444800}
        }, "rateLimitResetCredits": {"availableCount": 1}}}), flush=True)
"""


@pytest.fixture()
def fake_codex(tmp_path: Path) -> str:
    p = tmp_path / "fakecodex"
    p.write_text(FAKE_APP_SERVER, encoding="utf-8")
    p.chmod(p.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return str(p)


def test_probe_codex_parses_windows(fake_codex: str):
    r = quota._probe_codex(fake_codex, timeout=10)
    assert r.available is True
    assert r.confidence == "authoritative"
    assert r.plan == "plus"
    labels = {w.label: w for w in r.windows}
    assert labels["5h"].used_percent == 12
    assert labels["weekly"].used_percent == 63
    assert labels["weekly"].window_minutes == 10080
    assert "reset credit" in (r.note or "")


def test_probe_codex_binary_missing(tmp_path: Path):
    r = quota._probe_codex(str(tmp_path / "does-not-exist"))
    assert r.available is False
    assert r.confidence == "unavailable"


def test_quota_summary_default_agents(monkeypatch):
    monkeypatch.setattr(
        quota,
        "_probe_codex",
        lambda b, timeout=20.0: QuotaResult(
            "codex",
            available=True,
            confidence="authoritative",
            windows=[QuotaWindow("weekly", 10, None, 10080)],
        ),
    )
    rows = quota_summary()
    assert [r.agent for r in rows] == list(quota.QUOTA_AGENTS)
    codex_row = next(r for r in rows if r.agent == "codex")
    assert codex_row.available and codex_row.confidence == "authoritative"
