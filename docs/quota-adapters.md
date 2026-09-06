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

| Vendor | Quota source | Confidence | Implemented |
|--------|--------------|------------|-------------|
| **codex** | `codex app-server` JSON-RPC → `account/rateLimits/read` (same call the TUI `/status` makes; an account read, **not** an inference call). | authoritative | ✅ |
| **claude** | No `claude usage --json`. `mdrunner/_ptyusage.py` spawns `claude` on a PTY, answers the trust prompt, sends `/usage`, strips ANSI, and regexes the *Current session* / *Current week* blocks (`_CLAUDE_BLOCK_RE`). Reset strings (`2:10pm` / `Sep 12, 11pm` + tz) → timestamp via `zoneinfo`. | estimated | ✅ (PTY) |
| **agy** (Antigravity) | `/usage` (per model group: weekly + five-hour, % remaining + "Refreshes in Xh Ym") is TUI-only, backed by `RetrieveUserQuotaSummary`. PTY-scrape of the **GEMINI MODELS** group (`_AGY_LIMIT_RE`); `used = 100 − remaining`. A `RetrieveUserQuotaSummary` helper would upgrade this to authoritative. | estimated | ✅ (PTY) |
| **grok** | Open source. `/usage` calls an internal billing handler (`/billing?format=credits` → `BillingConfigResponse` with `creditUsagePercent` + `currentPeriod{type:WEEKLY,start,end}`). Calling it over `grok agent stdio` has returned *Method not found*; the clean path is a small `grok usage-json` helper in a fork that reuses `handle_get_billing()`. Grok has no `/usage` TUI screen to scrape. | authoritative (via helper) | ⏳ stub |

To add one, implement `_probe_<agent>(binary) -> QuotaResult` and register it
in `_DISPATCH`. Nothing else changes — the CLI, the poller, and the GUI panel
all read `QuotaResult`.

## Scheduled polling

```
mdrunner quota                     # table
mdrunner quota --json --write      # machine-readable + save snapshot for the GUI
mdrunner quota-schedule install    # systemd user timer, interval from settings.yaml
mdrunner quota-schedule status
mdrunner quota-schedule uninstall
```

`settings.yaml`:

```yaml
quota_poll:
  enabled: false
  interval_minutes: 180
  agents: [claude, codex, agy, grok]
```

The timer runs `mdrunner quota --write`, which refreshes
`~/.local/state/mdrunner/quota.json`. The GUI's **Agent Quota** dock loads
that snapshot on start and re-probes on **Refresh**.
