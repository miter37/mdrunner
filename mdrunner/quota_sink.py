"""statusLine sink — capture a CLI's own rate-limit / quota JSON.

Claude Code and Antigravity both support a ``statusLine.command`` hook that
is fed a JSON object on stdin on every turn. Claude's carries
``rate_limits.{five_hour,seven_day}`` (Pro/Max), Antigravity's carries
``quota.<bucket>.{remaining_fraction,reset_time,reset_in_seconds}``.

``mdrunner quota-sink <agent>`` is meant to BE that hook: it reads stdin,
writes the useful fields to ``<state>/quota-sink/<agent>.json`` (atomically),
then — so it doesn't clobber an existing status line — replays stdin to the
previously-configured command (passed base64 in ``--chain``) and prints its
output. It must never fail loudly: a broken status line spams the user.
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import time


def _sink_file(agent: str):
    from .utils.paths import state_dir

    d = state_dir() / "quota-sink"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{agent}.json"


def _atomic_write(path, text: str) -> None:
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)


def _win(label, used, resets_at, window_minutes=None):
    return {
        "label": label,
        "used_percent": used,
        "resets_at": resets_at,
        "window_minutes": window_minutes,
    }


def _parse_iso(s):
    from datetime import datetime

    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError):
        return None


def _acct(value):
    import hashlib

    return hashlib.sha256(str(value).encode("utf-8", "replace")).hexdigest()[:12]


def _extract_claude(payload: dict) -> dict:
    rl = payload.get("rate_limits") or {}
    windows = []
    for key, label, mins in (
        ("five_hour", "5h", 300),
        ("seven_day", "weekly", 10080),
    ):
        w = rl.get(key)
        if isinstance(w, dict) and w.get("used_percentage") is not None:
            windows.append(_win(label, round(float(w["used_percentage"]), 2),
                                w.get("resets_at"), mins))
    acct = None
    for k in ("account_uuid", "account", "user_id", "email"):
        if payload.get(k):
            acct = _acct(payload[k])
            break
    plan = payload.get("subscription") or (payload.get("model") or {}).get("plan")
    return {"windows": windows, "plan": plan, "account": acct}


def _extract_agy(payload: dict) -> dict:
    q = payload.get("quota") or {}
    windows = []
    for bucket, spec in q.items():
        if not isinstance(spec, dict):
            continue
        rf = spec.get("remaining_fraction")
        used = round((1.0 - float(rf)) * 100.0, 2) if isinstance(rf, (int, float)) else None
        resets = _parse_iso(spec.get("reset_time"))
        if resets is None and isinstance(spec.get("reset_in_seconds"), (int, float)):
            resets = time.time() + float(spec["reset_in_seconds"])
        low = bucket.lower()
        label = "weekly" if "week" in low else ("5h" if ("five" in low or "hour" in low) else bucket)
        mins = 10080 if label == "weekly" else (300 if label == "5h" else None)
        windows.append(_win(label, used, resets, mins))
    acct = _acct(payload["email"]) if payload.get("email") else None
    return {"windows": windows, "plan": payload.get("plan_tier"), "account": acct}


_EXTRACT = {"claude": _extract_claude, "agy": _extract_agy}


def run(argv) -> int:
    import argparse

    ap = argparse.ArgumentParser(prog="mdrunner quota-sink", add_help=True)
    ap.add_argument("agent", choices=sorted(_EXTRACT))
    ap.add_argument("--chain", default="", help="base64 of the status-line command to run next")
    args = ap.parse_args(argv)

    raw = sys.stdin.buffer.read()
    try:
        payload = json.loads(raw.decode("utf-8", "replace") or "{}")
        data = _EXTRACT[args.agent](payload)
        if data["windows"]:
            data["observed_at"] = time.time()
            _atomic_write(_sink_file(args.agent), json.dumps(data))
    except Exception:  # noqa: BLE001 — a status line hook must never break
        pass

    # Replay to whatever status line was configured before, so it still works.
    if args.chain:
        try:
            cmd = base64.b64decode(args.chain).decode("utf-8")
            r = subprocess.run(["sh", "-c", cmd], input=raw, capture_output=True, timeout=10)
            sys.stdout.buffer.write(r.stdout)
            return r.returncode
        except Exception:  # noqa: BLE001
            return 0
    return 0
