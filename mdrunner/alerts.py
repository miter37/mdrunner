"""Standalone quota alerts — Telegram on engine quota states.

Independent of tasks: a rule watches one quota-capable agent's 2x2
quota condition and fires a single Telegram message on the
false→true transition (edge trigger). While the condition stays true
nothing more fires; when it turns false the rule re-arms.

Evaluation happens inside `mdrunner quota-tick` (the periodic poller)
and from the GUI's quota refresh, against the same quota snapshot the
run gates use. Edge state lives in ``alert-state.json`` so the CLI
poller and the GUI share it — whichever fires first disarms, and the
other side skips.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import QuotaClause, QuotaCondition


@dataclass
class QuotaAlert:
    """One quota-watch rule.

    The 2x2 AND grid is the same schema tasks use
    (``QuotaCondition``), minus ``on_unknown``: alerts never fire on
    unreadable quota, regardless. ``message`` is the user-authored body
    sent on fire (Korean/English free text); ``name`` stays the short
    list label.
    """

    id: str
    name: str
    agent: str
    enabled: bool = True
    condition: QuotaCondition = field(default_factory=QuotaCondition)
    message: str = ""

    def describe(self) -> str:
        return self.condition.describe(self.agent)


# ---------------------------------------------------------------------------
# Persistence: alerts.json (user rules, config dir)
# ---------------------------------------------------------------------------


def alerts_path() -> Path:
    from .utils.paths import alerts_file

    return alerts_file()


def _alert_to_dict(a: QuotaAlert) -> dict[str, Any]:
    from .config import _quota_condition_to_dict

    return {
        "id": a.id,
        "name": a.name,
        "agent": a.agent,
        "enabled": a.enabled,
        "condition": _quota_condition_to_dict(a.condition),
        "message": a.message,
    }


def _clause_from_dict(raw: Any) -> QuotaClause:
    if not isinstance(raw, dict):
        return QuotaClause(False, 0.0)
    try:
        value = float(raw.get("value", 0.0))
    except (TypeError, ValueError):
        value = 0.0
    op = str(raw.get("op", "gte")).lower()
    if op not in ("gte", "lte"):
        op = "gte"
    return QuotaClause(bool(raw.get("enabled", False)), value, op)


def _alert_from_dict(data: dict[str, Any]) -> QuotaAlert:
    from .config import FIVE_HOUR_AGENTS, QUOTA_CAPABLE_AGENTS, QUOTA_CLAUSES

    agent = str(data.get("agent", ""))
    if agent not in QUOTA_CAPABLE_AGENTS:
        raise ValueError(f"unknown quota agent {agent!r}")
    raw = data.get("condition") or {}
    if not isinstance(raw, dict):
        raise TypeError("alert condition must be a mapping")
    cond = QuotaCondition(
        **{attr: _clause_from_dict(raw.get(attr)) for attr, _w, _k, _l in QUOTA_CLAUSES}
    )
    if not cond.active():
        raise ValueError("alert needs at least one enabled clause")
    if cond.uses_five_hour() and agent not in FIVE_HOUR_AGENTS:
        raise ValueError(f"{agent!r} only reports a weekly window")
    return QuotaAlert(
        id=str(data.get("id") or uuid.uuid4().hex[:8]),
        name=str(data.get("name") or "alert"),
        agent=agent,
        enabled=bool(data.get("enabled", True)),
        condition=cond,
        message=str(data.get("message") or ""),
    )


def load_alerts(path: Path | None = None) -> list[QuotaAlert]:
    p = Path(path) if path is not None else alerts_path()
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    items = raw.get("alerts") if isinstance(raw, dict) else raw
    if not isinstance(items, list):
        return []
    out: list[QuotaAlert] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            out.append(_alert_from_dict(item))
        except ValueError:
            continue  # skip corrupt entries, keep the healthy ones
    return out


def save_alerts(alerts: list[QuotaAlert], path: Path | None = None) -> Path:
    p = Path(path) if path is not None else alerts_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(
            {"alerts": [_alert_to_dict(a) for a in alerts]},
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return p


# ---------------------------------------------------------------------------
# Edge state: alert-state.json (state dir, shared CLI/GUI)
# ---------------------------------------------------------------------------


def state_path() -> Path:
    from .utils.paths import alert_state_file

    return alert_state_file()


def load_alert_state(path: Path | None = None) -> dict[str, Any]:
    p = Path(path) if path is not None else state_path()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_alert_state(state: dict[str, Any], path: Path | None = None) -> None:
    p = Path(path) if path is not None else state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(f".{__import__('os').getpid()}.tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    tmp.replace(p)


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------


@dataclass
class AlertFire:
    alert: QuotaAlert
    detail: str  # human-readable verdict detail for the message


def evaluate_alerts(
    alerts: list[QuotaAlert],
    snapshot: dict | None = None,
    *,
    state: dict[str, Any] | None = None,
    state_path_: Path | None = None,
) -> tuple[list[AlertFire], dict[str, Any]]:
    """Decide which alerts fire now. Returns (fires, updated state).

    Each enabled alert is evaluated; a fire happens only when the
    condition holds, the quota data was actually readable, and the
    alert is currently armed (False→True edge). Firing disarms; a
    False verdict re-arms; unknown data leaves the state untouched.
    """
    from .quota_gate import quota_condition_met

    st = dict(state) if state is not None else load_alert_state(state_path_)
    fires: list[AlertFire] = []
    for a in alerts:
        if not a.enabled:
            continue
        d = quota_condition_met(a.agent, a.condition, snapshot)
        key = f"armed:{a.id}"
        if d.unknown:
            continue  # unknown data: never fire, never toggle
        if d.allowed:
            if st.get(key, True):
                fires.append(AlertFire(a, d.reason))
                st[key] = False
        else:
            st[key] = True
    _save_alert_state(st, state_path_)
    return fires, st


# ---------------------------------------------------------------------------
# Delivery
# ---------------------------------------------------------------------------


def send_alert_fires(fires: list[AlertFire]) -> list[tuple[str, bool, str]]:
    """Send Telegram messages for fired alerts. Returns [(id, ok, detail)]."""
    from . import telegram
    from .ui import _telegram_settings

    cfg = _telegram_settings.load()
    bot_token = cfg.get("bot_token")
    chat_id = cfg.get("chat_id")
    out: list[tuple[str, bool, str]] = []
    for f in fires:
        if not bot_token or not chat_id:
            out.append((f.alert.id, False, "telegram bot_token / chat_id not configured"))
            continue
        text = telegram.format_quota_alert(f.alert.name, f.alert.agent, f.detail, f.alert.message)
        ok, detail = telegram.send(bot_token=bot_token, chat_id=chat_id, text=text, timeout=15.0)
        out.append((f.alert.id, ok, detail))
    return out
