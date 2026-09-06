# Agent quota adapters

`mdrunner quota` reports each subscription coding CLI's usage limits
(rolling 5-hour window, weekly window, and time until reset). There is **no
single method** — each vendor needs its own token-free trick, so every
adapter is isolated behind one interface (`mdrunner/quota.py :: QuotaResult`):

```
{ agent, available, confidence, plan, windows[], note, error, checked_at }
confidence = "authoritative" | "estimated" | "unavailable"
window     = { label ("5h" | "weekly" | …), used_percent, resets_at, window_minutes }
```

Windows are identified by **duration**, never position: `window_minutes == 10080`
is the weekly bucket, `~300` is the 5-hour bucket. (Some accounts return only
the weekly window as `primary`.)

## Status per vendor (2026-09)

`QuotaResult` carries **`source`** (how authoritative the data is: `api` /
`statusline` / `billing-log` / `screen-scrape` / `none`) separately from
**`observed_at`** (how fresh) and `confidence`. Raw `window_minutes` is kept;
the `weekly` / `5h` label is only a hint. `account` is a short hash of the
signed-in account so a stale value is never carried across an account switch.

| Vendor | Primary source | Fallback | `source` |
|--------|----------------|----------|----------|
| **codex** | `codex app-server` JSON-RPC -> `account/rateLimits/read`, preferring `rateLimitsByLimitId["codex"]` then `rateLimits`. Account read, not inference. `rateLimitResetCredits.availableCount` is authoritative for the credit count. | - | `api` |
| **claude** | `mdrunner quota-sink claude` - a `statusLine.command` hook that captures Claude Code's own `rate_limits.{five_hour,seven_day}` JSON to `<state>/quota-sink/claude.json`. Instant, no PTY, no tokens. Wire it with `mdrunner quota-sink-setup claude --write` (chains any existing status line). | PTY `/usage` scrape when the sink file is missing/stale | `statusline` -> `screen-scrape` |
| **agy** (Antigravity) | `mdrunner quota-sink agy` - captures `quota.<bucket>.{remaining_fraction,reset_time}` (`used = (1 - remaining_fraction) * 100`, `reset_time` verbatim). | PTY `/usage` scrape of the GEMINI MODELS group | `statusline` -> `screen-scrape` |
| **grok** | Reverse-scan `$GROK_HOME/logs/unified.jsonl` for the last `billing: fetched credits config`. `creditUsagePercent` is Optional - fall back to legacy `used/monthlyLimit`, else `used_percent = None` (never 0). If the entry is > 5 min old, send `/usage` on a PTY and only accept the refresh if a newer billing line appears (read the log, not the screen). | last known snapshot, marked stale | `billing-log` |

To add one, implement `_probe_<agent>(binary) -> QuotaResult` and register it
in `_DISPATCH`. Nothing else changes - the CLI, the poller, and the GUI panel
all read `QuotaResult`.

## Scheduled polling

```
mdrunner quota                     # table
mdrunner quota --json --write      # machine-readable + save snapshot for the GUI
mdrunner quota-schedule install    # OS timer (systemd / launchd / Task Scheduler)
mdrunner quota-schedule status
mdrunner quota-schedule uninstall
```

`settings.yaml`:

```yaml
quota_poll:
  enabled: false
  interval_minutes: 10
  agents: [claude, codex, agy, grok]
```

The timer runs `mdrunner quota --write`, which refreshes
`~/.local/state/mdrunner/quota.json`. The GUI's **Agent Quota** dock loads
that snapshot on start and re-probes on **Refresh**.

## Quota-conditional task execution

`quota_gate.check_task_gate()` decides whether a task may run right now, from
two gates checked cheap-first:

1. `min_rerun_interval` — enough time since the task last *actually* ran?
   Skips (min-interval, quota, lock) never advance `last_run_at`, so repeated
   skips can't push the deadline forward and starve the task.
2. `quota_condition` — a 2×2 grid, `{weekly, 5-hour} × {used %, resets within
   N hours}`, read from the latest snapshot (one live `probe_quota()` for that
   single agent if its entry is missing or older than 15 min). Only the
   `enabled` clauses are AND-ed, and there must be at least one. `used`
   compares `used_percent` against `value` percent — `>=` by default, or
   `<=` when the clause's `op` is `"lte"`; `reset` always compares
   `seconds_until_reset` (or `resets_at - now`) `<=` `value` hours. If any
   enabled clause can't be evaluated, `on_unknown` picks `skip`/`run`.

`mdrunner run <id> --mode scheduled` applies both gates (bypass with
`--force`); `--mode manual` / GUI **Run now** bypass them. For
`schedule.mode: quota` tasks there is no OS timer for the task itself — the
`mdrunner quota-tick` poll timer (installed by `mdrunner quota-schedule
install`) refreshes the snapshot and fires them. See the README's
"Quota-conditional execution" section for the `quota_condition` fields.
