"""mdrunner command-line entry point.

Phase 1 commands:
    mdrunner list               — list configured tasks
    mdrunner preview <task-id>  — show the exact argv that would be invoked
    mdrunner run   <task-id>    — execute one task now
    mdrunner validate           — load config + health-check all agents
    mdrunner health             — health-check all configured agents
    mdrunner init               — write default tasks.yaml + settings.yaml if missing
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence


from .agents import default_settings
from .config import (
    ConfigError,
    Task,
    find_task,
    load_settings,
    load_tasks,
    save_settings,
    save_tasks,
    settings_from_dict,
)
from .runner import (
    LockBusyError,
    health_summary,
    preview_task,
    run_task,
)
from .utils.paths import settings_file, tasks_file


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------


def cmd_list(args: argparse.Namespace) -> int:
    tasks = load_tasks(tasks_file())
    settings = load_settings(settings_file())
    if not tasks:
        print("no tasks defined. run `mdrunner init` to seed defaults, then edit.")
        return 0
    print(f"{'ID':<28} {'AGENT':<12} {'ENABLED':<8} {'SCHEDULE':<28} NAME")
    print("-" * 110)
    for t in tasks:
        if t.schedule.mode == "once":
            sched = "once"
        elif t.schedule.mode == "weekly":
            sched = f"weekly:{','.join(t.schedule.days)}@{t.schedule.time}"
        elif t.schedule.mode == "interval":
            sched = f"every {t.schedule.interval_minutes}m"
        elif t.schedule.mode == "quota":
            sched = "quota"
        else:
            sched = f"daily@{t.schedule.time}"
        enabled = "yes" if t.enabled else "no"
        agent_ok = t.agent in settings.agents
        agent_mark = t.agent if agent_ok else f"{t.agent}(!)"
        print(f"{t.id:<28} {agent_mark:<12} {enabled:<8} {sched:<28} {t.name}")
    return 0


def cmd_preview(args: argparse.Namespace) -> int:
    settings = load_settings(settings_file())
    try:
        info = preview_task(args.task_id, mode=args.mode, settings=settings)
    except KeyError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"task_id  : {info['task_id']}")
    print(f"mode     : {info['mode']}")
    print(f"agent    : {info['agent']}")
    print(f"binary   : {info['binary']} -> {info['binary_path']}")
    print(f"model    : {info['model']}")
    print(f"bypass   : {' '.join(info['bypass_flags']) or '(none)'}")
    print(f"extra    : {' '.join(info['extra_args']) or '(none)'}")
    print(f"cwd      : {info['cwd'] or '(inherit)'}")
    print(f"timeout  : {info['timeout_minutes']}m")
    print("command  :")
    print(f"  $ {' '.join(info['argv_quoted'])}")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    settings = load_settings(settings_file())
    try:
        tasks = load_tasks(tasks_file())
        task = find_task(tasks, args.task_id)
        result = run_task(
            args.task_id,
            mode=args.mode,
            settings=settings,
            timeout_override=args.timeout,
            force_run=getattr(args, "force", False),
        )
    except LockBusyError as exc:
        print(f"locked: {exc}", file=sys.stderr)
        return 4
    except KeyError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if result.skipped:
        print(f"\n[skip] {result.task_id}: {result.skip_reason}")
        return 0

    duration = result.duration_seconds
    print()
    print(f"task_id      : {result.task_id}")
    print(f"started      : {format_ts(result.started_at)}")
    print(f"finished     : {format_ts(result.finished_at)}")
    print(f"duration     : {duration:.1f}s")
    print(f"exit_code    : {result.exit_code}")
    print(f"timed_out    : {result.timed_out}")
    if result.saved_file:
        print(f"saved_file   : {result.saved_file}")
    if result.log_file:
        print(f"log_file     : {result.log_file}")
    if result.error:
        print(f"error        : {result.error}")
    print()
    if result.ok:
        print("[ok] task succeeded")
        return 0
    print("[fail] task did not succeed")
    _maybe_notify_telegram(task, result)
    return 1


def _maybe_notify_telegram(task: Task, result) -> None:
    """Send a Telegram message on failure if the task asks for it."""
    if not task.on_failure.notify:
        return
    from . import telegram
    from .ui import _telegram_settings

    cfg = _telegram_settings.load()
    if not cfg.get("bot_token") or not cfg.get("chat_id"):
        return
    if not cfg.get("notify_on_failure", True):
        return
    body = telegram.format_failure(
        task.name,
        {
            "duration": result.duration_seconds,
            "exit_code": result.exit_code,
            "error": result.error,
            "saved_file": result.saved_file,
        },
    )
    ok, resp = telegram.send(
        bot_token=cfg["bot_token"],
        chat_id=cfg["chat_id"],
        text=body,
    )
    if not ok:
        print(f"telegram notify failed: {resp}", file=sys.stderr)


def cmd_validate(args: argparse.Namespace) -> int:
    try:
        tasks = load_tasks(tasks_file())
        settings = load_settings(settings_file())
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2
    print(f"loaded {len(tasks)} task(s) from {tasks_file()}")
    print(f"loaded {len(settings.agents)} agent(s) from {settings_file()}")
    warnings = 0
    for t in tasks:
        if t.agent not in settings.agents:
            print(f"  warn: task {t.id!r} uses agent {t.agent!r} which has no settings")
            warnings += 1
        if not t.enabled:
            print(f"  note: task {t.id!r} is disabled")
        if not t.prompt_file:
            print(f"  warn: task {t.id!r} has empty prompt_file")
            warnings += 1
        if t.quota_condition is not None:
            print(
                f"  note: task {t.id!r} has a quota condition "
                f"({t.quota_condition.describe(t.agent)})"
            )
        if t.schedule.mode == "quota" and t.enabled:
            try:
                from .scheduler.base import current as _sched

                sched = _sched()
                installed = (
                    hasattr(sched, "quota_poll_installed") and sched.quota_poll_installed()
                )
                every = (
                    getattr(sched, "quota_poll_interval_minutes", lambda: None)()
                    if installed
                    else None
                )
            except Exception as exc:  # noqa: BLE001 — introspection must not fail validate
                installed, every = None, None
                print(f"  note: task {t.id!r} is quota-triggered (scheduler check skipped: {exc})")
            if installed:
                print(
                    f"  note: task {t.id!r} is quota-triggered; poll timer is installed"
                    + (f" (every ~{every}m)" if every else "")
                )
            elif installed is False:
                print(
                    f"  warn: task {t.id!r} is quota-triggered but the quota-poll timer "
                    f"is NOT installed — it will never run. Install it with:  "
                    f"mdrunner quota-schedule install"
                )
                warnings += 1
    rows = health_summary(settings)
    print()
    print(f"{'AGENT':<12} {'OK':<5} {'PATH':<35} VERSION")
    print("-" * 90)
    for r in rows:
        ok = "yes" if r["ok"] else "NO"
        path = r["binary_path"] or "(missing)"
        ver = (r["version"] or r["error"] or "")[:40]
        print(f"{r['agent']:<12} {ok:<5} {path:<35} {ver}")
    return 0 if warnings == 0 else 0  # warnings don't fail validation


def cmd_health(args: argparse.Namespace) -> int:
    settings = load_settings(settings_file())
    rows = health_summary(settings)
    print(json.dumps(rows, indent=2, ensure_ascii=False))
    bad = [r for r in rows if not r["ok"]]
    return 1 if bad else 0


def cmd_quota(args: argparse.Namespace) -> int:
    from .quota import fmt_reset, quota_summary, save_snapshot

    settings = load_settings(settings_file())
    agents = args.agents.split(",") if args.agents else settings.quota_poll.agents
    results = quota_summary([a.strip() for a in agents if a.strip()])

    if args.write:
        p = save_snapshot(results)
        print(f"wrote {p}", file=sys.stderr)

    if args.json:
        print(json.dumps({r.agent: r.to_dict() for r in results}, indent=2, ensure_ascii=False))
        return 0

    import time as _t

    def _age(r):
        if not r.observed_at:
            return ""
        s = int(max(0, _t.time() - r.observed_at))
        return f"{s // 60}m ago" if s >= 60 else f"{s}s ago"

    print(f"{'AGENT':<8} {'SOURCE':<13} {'WINDOW':<9} {'USED':>6} {'RESETS IN':>10}  AGE")
    print("-" * 62)
    for r in results:
        if not r.available:
            print(f"{r.agent:<8} {r.source:<13} {'—':<9} {'—':>6} {'—':>10}      ({r.error})")
            continue
        for i, w in enumerate(r.windows):
            agent_c = r.agent if i == 0 else ""
            src_c = r.source if i == 0 else ""
            age_c = _age(r) if i == 0 else ""
            used = f"{w.used_percent:.0f}%" if w.used_percent is not None else "—"
            print(
                f"{agent_c:<8} {src_c:<13} {w.label:<9} {used:>6} "
                f"{fmt_reset(w.seconds_until_reset):>10}  {age_c}"
            )
        tags = " · ".join(x for x in (r.plan, r.note) if x)
        if tags:
            print(f"{'':<8} {'':<13} · {tags}")
    return 0


def cmd_quota_sink(args: argparse.Namespace) -> int:
    from .quota_sink import run

    return run([args.agent] + (["--chain", args.chain] if args.chain else []))


def cmd_quota_sink_setup(args: argparse.Namespace) -> int:
    """Show (or, with --write, apply) the statusLine hook wiring for an agent."""
    import base64
    import json as _json
    from pathlib import Path

    cfgs = {
        "claude": Path.home() / ".claude" / "settings.json",
        "agy": Path.home() / ".gemini" / "antigravity-cli" / "settings.json",
    }
    path = cfgs[args.agent]
    exe = _mdrunner_executable_for_scheduler()
    try:
        data = _json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, ValueError) as exc:
        print(f"could not read {path}: {exc}", file=sys.stderr)
        return 2
    prev = ((data.get("statusLine") or {}).get("command") or "").strip()

    if args.remove:
        # unwrap: pull the chained command back out
        if "quota-sink" in prev and "--chain" in prev:
            b64 = prev.split("--chain", 1)[1].strip().strip("'\"").split()[0]
            try:
                prev = base64.b64decode(b64).decode("utf-8")
            except Exception:  # noqa: BLE001
                prev = ""
        new_cmd = prev
    else:
        chain = f" --chain {base64.b64encode(prev.encode()).decode()}" if prev else ""
        new_cmd = f'"{exe}" quota-sink {args.agent}{chain}'

    print(f"settings file : {path}")
    print(f"current       : {prev or '(none)'}")
    print(f"new statusLine : {new_cmd or '(cleared)'}")
    if not args.write:
        print("\n(dry run — re-run with --write to apply; a .bak is kept)")
        return 0
    if path.exists():
        path.with_suffix(".json.bak").write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    data.setdefault("statusLine", {})
    data["statusLine"]["type"] = "command"
    data["statusLine"]["command"] = new_cmd
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_json.dumps(data, indent=2), encoding="utf-8")
    print(f"\napplied. backup: {path.with_suffix('.json.bak')}")
    return 0


def cmd_quota_tick(args: argparse.Namespace) -> int:
    """Refresh the quota snapshot, then run any quota-triggered task whose
    condition is now met (and whose min-rerun interval has elapsed)."""
    from .quota import load_snapshot, quota_summary, save_snapshot
    from .quota_gate import check_task_gate
    from .runner import last_real_run_at, run_task

    settings = load_settings(settings_file())

    try:
        tasks = load_tasks(tasks_file())
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2

    conditional = [
        t for t in tasks if t.schedule.mode == "quota" and t.enabled and t.quota_condition
    ]

    # Refresh the configured poll set, but always include the agents that
    # quota-triggered tasks actually depend on so their gate reads fresh data.
    if args.agents:
        base = [a.strip() for a in args.agents.split(",") if a.strip()]
    else:
        base = list(settings.quota_poll.agents)
    probe = list(dict.fromkeys(base + [t.agent for t in conditional]))
    results = quota_summary(probe)
    save_snapshot(results)
    snap = load_snapshot()
    if not conditional:
        print("quota-tick: snapshot refreshed; no quota-triggered tasks", file=sys.stderr)
        return 0

    ran = skipped = 0
    for t in conditional:
        d = check_task_gate(t, last_run_at=last_real_run_at(t.id), snapshot=snap)
        if not d.allowed:
            skipped += 1
            print(f"  skip  {t.id}  ({d.reason})")
            continue
        if args.dry_run:
            print(f"  WOULD RUN  {t.id}  ({d.reason})")
            ran += 1
            continue
        print(f"  run   {t.id}  ({d.reason})")
        try:
            res = run_task(t.id, mode="scheduled", settings=settings)
            state = "ok" if res.ok else ("skip" if res.skipped else "fail")
            print(f"        → {state}"
                  + (f" ({res.skip_reason or res.error})" if state != "ok" else ""))
            ran += 1
        except Exception as exc:  # noqa: BLE001
            print(f"        → error: {exc}")
    print(f"quota-tick: {ran} run, {skipped} skipped", file=sys.stderr)
    return 0


def cmd_quota_schedule(args: argparse.Namespace) -> int:
    from .scheduler.base import current

    sched = current()
    if not hasattr(sched, "install_quota_poll"):
        print("quota poll timer is not supported on this platform", file=sys.stderr)
        return 2
    settings = load_settings(settings_file())

    if args.action == "status":
        if not sched.quota_poll_installed():
            print("quota poll: not installed")
            return 1
        nxt = sched.quota_poll_next_run()
        interval = sched.quota_poll_interval_minutes() or settings.quota_poll.interval_minutes
        print("quota poll: installed")
        print(f"  interval : {interval} min")
        if nxt:
            print(f"  next run : {nxt.isoformat(sep=' ', timespec='seconds')}")
        return 0

    if args.action == "uninstall":
        sched.uninstall_quota_poll()
        print("quota poll: uninstalled")
        return 0

    # install
    interval = args.interval or settings.quota_poll.interval_minutes
    executable = _mdrunner_executable_for_scheduler()
    try:
        sched.install_quota_poll(interval, executable)
    except Exception as exc:  # noqa: BLE001
        print(f"install failed: {exc}", file=sys.stderr)
        return 2
    print(f"quota poll: installed (every {interval} min, exec={executable})")
    nxt = sched.quota_poll_next_run()
    if nxt:
        print(f"  next run: {nxt.isoformat(sep=' ', timespec='seconds')}")
    return 0


def cmd_init(args: argparse.Namespace) -> int:
    settings_p = settings_file()
    tasks_p = tasks_file()
    if not settings_p.exists():
        save_settings(settings_p, settings_from_dict(default_settings()))
        print(f"wrote {settings_p}")
    else:
        # Top up agents shipped since this config was created (e.g. a new
        # adapter added in an update) without touching existing entries.
        import yaml

        from .config import load_yaml

        raw = load_yaml(settings_p)
        raw.setdefault("agents", {})
        added = [a for a in default_settings()["agents"] if a not in raw["agents"]]
        if added:
            for a in added:
                raw["agents"][a] = default_settings()["agents"][a]
            with settings_p.open("w", encoding="utf-8") as fh:
                yaml.safe_dump(raw, fh, sort_keys=False, allow_unicode=True)
            print(f"update: {settings_p} (added agents: {', '.join(added)})")
        else:
            print(f"keep:  {settings_p} (already exists, all agents present)")
    if not tasks_p.exists():
        save_tasks(tasks_p, [])
        print(f"wrote {tasks_p}")
    else:
        print(f"keep:  {tasks_p} (already exists)")
    return 0


def _mdrunner_executable_for_scheduler() -> Path:
    """Resolve the path that the OS scheduler should invoke.

    Preference order:
      1. RUNCHER_EXECUTABLE env var (set by packaged builds).
      2. The path of the current `mdrunner` binary on PATH.
      3. The current Python interpreter (dev fallback).
    """
    import os
    import shutil
    import sys

    env = os.environ.get("RUNCHER_EXECUTABLE")
    if env:
        return Path(env)
    on_path = shutil.which("mdrunner")
    if on_path:
        return Path(on_path)
    return Path(sys.executable)


def cmd_schedule_install(args: argparse.Namespace) -> int:
    from .scheduler.base import current

    tasks = load_tasks(tasks_file())
    task = find_task(tasks, args.task_id)
    if task.schedule.mode == "quota":
        print(
            f"task {task.id!r} is quota-triggered (no time schedule) — it runs via "
            f"the quota poll timer.\nEnable it with:  mdrunner quota-schedule install",
            file=sys.stderr,
        )
        return 2
    if not task.enabled:
        ans = input(f"task {task.id!r} is disabled — install anyway? [y/N] ")
        if ans.strip().lower() != "y":
            print("aborted")
            return 1
    executable = _mdrunner_executable_for_scheduler()
    print(f"using executable: {executable}")
    sched = current()
    try:
        sched.install(task, executable)
    except Exception as exc:  # noqa: BLE001
        print(f"install failed: {exc}", file=sys.stderr)
        return 2
    print(f"installed schedule for {task.id!r}")
    nxt = sched.next_run(task)
    if nxt:
        print(f"next run: {nxt.isoformat(sep=' ', timespec='seconds')}")
    return 0


def cmd_schedule_uninstall(args: argparse.Namespace) -> int:
    from .scheduler.base import current

    tasks = load_tasks(tasks_file())
    try:
        task = find_task(tasks, args.task_id)
    except KeyError:
        # task file may not exist if user is cleaning up after a deletion
        from .config import Task

        task = Task(id=args.task_id, name=args.task_id, prompt_file="")
    sched = current()
    sched.uninstall(task)
    print(f"uninstalled schedule for {args.task_id!r}")
    return 0


def cmd_schedule_status(args: argparse.Namespace) -> int:
    from .scheduler.base import current

    tasks = load_tasks(tasks_file())
    try:
        task = find_task(tasks, args.task_id)
    except KeyError:
        print(f"error: no task with id={args.task_id!r}", file=sys.stderr)
        return 2
    sched = current()
    if not sched.is_installed(task):
        print(f"{task.id}: not installed in OS scheduler")
        return 1
    nxt = sched.next_run(task)
    last_code, last_dt = sched.last_status(task)
    print(f"{task.id}: installed")
    if nxt:
        print(f"  next run : {nxt.isoformat(sep=' ', timespec='seconds')}")
    if last_dt:
        print(f"  last run : {last_dt.isoformat(sep=' ', timespec='seconds')}")
        if last_code is not None:
            print(f"  exit code: {last_code}")
    return 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def format_ts(t: float) -> str:
    import datetime as _dt

    return _dt.datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M:%S")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="mdrunner", description="Schedule md-driven CLI agent tasks.")
    sub = p.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="list configured tasks")
    p_list.set_defaults(func=cmd_list)

    p_preview = sub.add_parser("preview", help="show argv that would be invoked")
    p_preview.add_argument("task_id")
    p_preview.add_argument("--mode", choices=("manual", "scheduled"), default="manual")
    p_preview.set_defaults(func=cmd_preview)

    p_run = sub.add_parser("run", help="execute one task now")
    p_run.add_argument("task_id")
    p_run.add_argument("--mode", choices=("manual", "scheduled"), default="manual")
    p_run.add_argument(
        "--timeout",
        type=int,
        default=None,
        help="override task timeout in minutes (0 = no limit)",
    )
    p_run.add_argument(
        "--force", action="store_true",
        help="run even if the min-rerun-interval / quota-condition gate says skip",
    )
    p_run.set_defaults(func=cmd_run)

    p_validate = sub.add_parser("validate", help="validate config + run health check")
    p_validate.set_defaults(func=cmd_validate)

    p_health = sub.add_parser("health", help="health check all agents (JSON output)")
    p_health.set_defaults(func=cmd_health)

    p_quota = sub.add_parser("quota", help="show per-agent usage quota (5h / weekly / reset)")
    p_quota.add_argument("--json", action="store_true", help="machine-readable output")
    p_quota.add_argument("--write", action="store_true", help="also save a snapshot for the GUI")
    p_quota.add_argument(
        "--agents", default="", help="comma-separated subset (default: settings.quota_poll.agents)"
    )
    p_quota.set_defaults(func=cmd_quota)

    p_qsink = sub.add_parser("quota-sink", help="statusLine hook: capture an agent's rate-limit JSON")
    p_qsink.add_argument("agent", choices=("claude", "agy"))
    p_qsink.add_argument("--chain", default="", help="base64 of the status line command to run next")
    p_qsink.set_defaults(func=cmd_quota_sink)

    p_qsetup = sub.add_parser("quota-sink-setup", help="wire quota-sink into an agent's settings.json")
    p_qsetup.add_argument("agent", choices=("claude", "agy"))
    p_qsetup.add_argument("--write", action="store_true", help="apply (default: dry run)")
    p_qsetup.add_argument("--remove", action="store_true", help="unwire it")
    p_qsetup.set_defaults(func=cmd_quota_sink_setup)

    p_qtick = sub.add_parser(
        "quota-tick", help="refresh quota + fire quota-triggered tasks (the poll timer runs this)"
    )
    p_qtick.add_argument("--dry-run", action="store_true", help="evaluate only, run nothing")
    p_qtick.add_argument("--agents", default="", help="comma-separated subset to refresh")
    p_qtick.set_defaults(func=cmd_quota_tick)

    p_qsched = sub.add_parser(
        "quota-schedule", help="install/remove the periodic quota-poll OS timer"
    )
    p_qsched.add_argument(
        "action", choices=("install", "uninstall", "status"), help="what to do"
    )
    p_qsched.add_argument(
        "--interval", type=int, default=None, help="minutes between polls (default: settings.yaml)"
    )
    p_qsched.set_defaults(func=cmd_quota_schedule)

    p_init = sub.add_parser("init", help="seed default config files if missing")
    p_init.set_defaults(func=cmd_init)

    p_sched_install = sub.add_parser("schedule-install", help="install task in OS scheduler")
    p_sched_install.add_argument("task_id")
    p_sched_install.set_defaults(func=cmd_schedule_install)

    p_sched_uninstall = sub.add_parser("schedule-uninstall", help="remove task from OS scheduler")
    p_sched_uninstall.add_argument("task_id")
    p_sched_uninstall.set_defaults(func=cmd_schedule_uninstall)

    p_sched_status = sub.add_parser("schedule-status", help="show OS scheduler status for task")
    p_sched_status.add_argument("task_id")
    p_sched_status.set_defaults(func=cmd_schedule_status)

    return p


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2
    except FileNotFoundError as exc:
        print(f"file not found: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())