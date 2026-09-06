# mdrunner

`mdrunner` schedules and runs md-driven CLI agent tasks across Linux and Windows.

Each task points at a single instruction md file and is executed by a configured CLI
agent (`opencode`, `codex`, `openclaw`, `claude`, `agy`, …) at a scheduled time. The CLI
receives the md content as its prompt, performs whatever the md describes, and writes
its own output to disk. `mdrunner` orchestrates the invocation, captures logs, parses
the saved-file marker, and reports the result.

Three delivery modes:

- **Headless CLI** — `mdrunner list | preview | run | validate | health | quota | init | schedule-install …`
- **GUI** — `mdrunner` (launches a PySide6 desktop window; light / dark / system
  theme under **View → Theme**)
- **Frozen binary** — `dist/mdrunner` (or `dist\mdrunner.exe`), single file, no Python required

## Install

```bash
git clone <repo>
cd mdrunner
uv sync --extra gui --extra build
```

Or use the central Python 3.14 venv (recommended for multi-app setups):

```bash
# Central venv once (e.g. /home/doyoonkim/APPs/Python314/venv)
uv python install 3.14
uv venv --python 3.14 /home/doyoonkim/APPs/Python314/venv
/home/doyoonkim/APPs/Python314/venv/bin/pip install -e /path/to/mdrunner[gui,build,dev]
```

## Launchers

`bin/` ships with two platform-specific launchers that resolve a runnable
interpreter in the same order:

1. **frozen binary** — `dist/mdrunner` (Linux/macOS) or `dist\mdrunner.exe` (Windows)
2. **central venv** — `bin/mdrunner.sh` defaults to `/home/doyoonkim/APPs/Python314/venv/`
   (override with `MDRUNNER_PYTHON=...` env var); `bin/mdrunner.bat` defaults to
   `C:\Users\default\AppData\Local\Programs\Python\Python314\venv\Scripts\python.exe`
3. **PATH python3 / python** as last-resort fallback

```bash
# Linux / macOS
bin/mdrunner.sh list
bin/mdrunner.sh run smoke_test --mode scheduled
RUNCHER_CONFIG_DIR=/some/path bin/mdrunner.sh list

# Windows (PowerShell or cmd)
bin\mdrunner.bat list
bin\mdrunner.bat run smoke_test --mode scheduled
set MDRUNNER_CONFIG_DIR=C:\some\path && bin\mdrunner.bat list
```

## Quick start

```bash
# 1) Seed default config
uv run mdrunner init

# 2) Edit tasks at ~/.config/mdrunner/tasks.yaml
#    (or set RUNCHER_CONFIG_DIR=/some/path for testing)
$EDITOR ~/.config/mdrunner/tasks.yaml

# 3) Smoke test
uv run mdrunner validate
uv run mdrunner health

# 4) Run a task once
uv run mdrunner run <task-id>

# 5) Schedule it
uv run mdrunner schedule-install <task-id>

# 6) Launch the GUI
uv run mdrunner --gui       # or just: ./run_gui.sh
```

`mdrunner` / `run.sh` with no arguments prints CLI help (so it never hangs
on a headless box). `./run_gui.sh` always starts the desktop window.

## Project layout

```
mdrunner/
├── pyproject.toml
├── README.md
├── smoke_test.md                     # tiny task: read 5 finviz headlines
├── mdrunner/
│   ├── __init__.py
│   ├── __main__.py                   # python -m mdrunner (CLI or GUI)
│   ├── cli.py                        # all CLI subcommands
│   ├── config.py                     # tasks.yaml + settings.yaml
│   ├── runner.py                     # subprocess + lock + timeout + log capture
│   ├── health.py                     # binary + version probe
│   ├── prompts.py                    # save GUI-authored instructions as md
│   ├── quota.py                      # per-agent usage-quota adapters
│   ├── telegram.py                   # optional failure notifications
│   ├── agents/
│   │   ├── base.py
│   │   ├── opencode.py
│   │   ├── codex.py
│   │   ├── openclaw.py
│   │   ├── claude.py
│   │   ├── agy.py
│   │   ├── hermes.py
│   │   └── grok.py
│   ├── scheduler/
│   │   ├── base.py
│   │   ├── linux.py                  # systemd user timer
│   │   ├── macos.py                  # launchd user agent
│   │   └── windows.py                # Task Scheduler (schtasks)
│   ├── ui/                           # PySide6 GUI
│   │   ├── theme.py                  # Fusion + QSS design system (light/dark)
│   │   ├── icons.py                  # painter-drawn line icons (no icon font)
│   │   ├── delegates.py              # status-pill + two-line name painting
│   │   ├── main_window.py
│   │   ├── task_dialog.py
│   │   ├── settings_dialog.py
│   │   ├── log_view.py
│   │   ├── workers.py
│   │   └── _telegram_settings.py
│   └── utils/
│       └── paths.py
├── installer/
│   ├── mdrunner.spec                  # PyInstaller spec
│   ├── mdrunner_launcher.py           # entry-point shim for frozen binary
│   ├── build.sh                      # Linux / macOS
│   └── build.ps1                     # Windows
└── tests/
    ├── test_agents.py
    ├── test_config.py
    ├── test_runner.py
    └── test_ui_smoke.py
```

## Adding an agent CLI

1. Create `mdrunner/agents/<name>.py`:

   ```python
   from .base import AgentAdapter, ArgvResult, read_prompt_text

   class MyAgent(AgentAdapter):
       id = "myagent"
       binary = "myagent"

       def build_argv(self, prompt_file, *, model, working_dir, extra_args, bypass_flags):
           argv = ["myagent", "run", "--prompt-file", str(prompt_file)]
           argv.extend(bypass_flags)
           if model:
               argv += ["--model", model]
           argv.extend(extra_args)
           return ArgvResult(argv=argv, cwd=working_dir)
   ```

2. Register in `mdrunner/agents/__init__.py`:

   ```python
   from .myagent import MyAgent
   ADAPTERS[MyAgent.id] = MyAgent()
   ```

3. Add a default settings block in `default_settings()` so it shows up after
   `mdrunner init`.

## Supported agent CLIs

| Agent CLI | binary on PATH | Default model | Non-interactive flag | Bypass flag (default) | File flag |
|-----------|----------------|---------------|----------------------|------------------------|------------|
| `opencode` | `opencode` | `minimax-coding-plan/MiniMax-M3` | `run` | `--auto` | (inline only) |
| `codex` | `codex` | `gpt-5.4` | `exec` | `--yolo` | (inline only) |
| `openclaw` | (install separately) | (none configured) | `agent --local` | `--local` | `--message-file` ✓ |
| `claude` | `claude` | `glm-5.2` | `-p` | `--dangerously-skip-permissions` | (inline only) |
| `agy` | `agy` | `Gemini 3.5 Flash (Medium)` | `-p` | `--dangerously-skip-permissions` | (inline only) |
| `hermes` | `hermes` | (CLI default) | `chat -q` | `--yolo` | (inline only) |
| `grok` | `grok` | (CLI default) | `--prompt-file` / `-p` | `--permission-mode bypassPermissions` | `--prompt-file` ✓ |

**Verify available models:** `agy models`, `claude --models`, `opencode models`, `codex --models`, `grok models`.

Adding an agent CLI in a later update? Run `mdrunner init` again — it leaves
your existing `settings.yaml` untouched but appends any newly-shipped agent
blocks (reported as `update: … (added agents: …)`).
Use `mdrunner preview <task-id>` to see the exact argv that will be invoked.

## Build the frozen binary

```bash
# Linux / macOS
./installer/build.sh
./dist/mdrunner list

# Windows (run from PowerShell in repo root)
.\installer\build.ps1
.\dist\mdrunner.exe list
```

Output: single ~67 MB executable in `dist/`. No Python install required on the target.

> Windows binaries must be built on Windows. PyInstaller does not cross-compile.

## Configuration

### `~/.config/mdrunner/settings.yaml`

Auto-created by `mdrunner init`. Each agent has a `default_model`, `health_cmd`,
`bypass.scheduled`/`bypass.manual` (auto-approve flags), and a list of named
`presets` that show up in the GUI's "Extra args preset" dropdown.

Override location: `RUNCHER_CONFIG_DIR=/some/path`.
Log directory override: `RUNCHER_LOG_DIR=/some/path`.
User-data directory override: `RUNCHER_DATA_DIR=/some/path`.

### Inline prompts (`<data>/mdrunner/prompts/`)

In the GUI's Add/Edit task dialog, **Prompt source** can be set to
**Write inline** instead of pointing at an existing file. What you type is
saved as a Markdown file in the app's user-data folder and registered as
that task's `prompt_file`:

| OS | Folder |
|----|--------|
| Linux | `~/.local/share/mdrunner/prompts/` |
| macOS | `~/Library/Application Support/mdrunner/prompts/` |
| Windows | `%LOCALAPPDATA%\mdrunner\prompts\` |

Files are named `<task-name-slug>-<YYYYMMDD-HHMMSS>.md`. Re-opening such a
task loads its text back into the editor and **saves over the same file**
on accept, so the path stored in `tasks.yaml` stays stable.

### `~/.config/mdrunner/tasks.yaml`

One entry per task. Key fields:

| Field | Notes |
|-------|-------|
| `id` | Stable identifier; used in logs, OS scheduler names, URLs |
| `name` | Human-readable name shown in the GUI |
| `enabled` | When false, the scheduler entry (if installed) is removed |
| `agent` | Must match a key in `settings.yaml` |
| `model` | Optional; falls back to the agent's `default_model` |
| `prompt_file` | The md file the agent will read |
| `working_dir` | Optional; the agent process cwd |
| `schedule.mode` | `once` \| `daily` \| `weekly` \| `interval` |
| `schedule.days` | For weekly: `mon`, `tue`, ... `sun` |
| `schedule.time` | `HH:MM` (24h) |
| `schedule.timezone` | IANA tz name, e.g. `Asia/Seoul` |
| `schedule.interval_minutes` | For `interval` mode only |
| `extra_args` | Free-form list passed to the agent CLI |
| `timeout_minutes` | `0` = no timeout |
| `on_failure.notify` | Send a Telegram message on failure (bot must be configured) |

### Agent quota (`mdrunner quota`)

Reports each subscription CLI's usage limits — rolling 5-hour window, weekly
window, % used, and time until reset.

```bash
mdrunner quota                    # table
mdrunner quota --json --write     # machine-readable + save snapshot for the GUI
mdrunner quota-schedule install   # systemd user timer (interval from settings.yaml)
mdrunner quota-schedule status
mdrunner quota-schedule uninstall
```

There is no universal source, so each vendor has its own token-free adapter
(none of these consume model tokens):

| Vendor | Source | State |
|--------|--------|-------|
| `codex` | `codex app-server` → `account/rateLimits/read` | ✅ authoritative |
| `claude` | PTY-scrape the interactive `/usage` screen (session + week % + reset) | ✅ estimated |
| `agy` | PTY-scrape `/usage` (GEMINI MODELS group: weekly + 5-hour) | ✅ estimated |
| `grok` | read `~/.grok/logs/unified.jsonl` billing snapshot; `/usage` on a PTY to refresh | ✅ authoritative |

The PTY scrapers spawn the real CLI for ~30 s and read its `/usage` panel
(an account read — no model tokens). They are best-effort: a screen-format
change makes the probe return `unavailable`, never wrong data. Linux/macOS
only.

See `docs/quota-adapters.md`. The GUI's **Agent Quota** dock shows the latest
snapshot and re-probes on demand. Configure polling in `settings.yaml`:

```yaml
quota_poll:
  enabled: false
  interval_minutes: 180
  agents: [claude, codex, agy, grok]
```

### `~/.config/mdrunner/telegram.json`

Sidecar for Telegram bot settings (kept separate so the bot token isn't in
the main YAML). Created/updated by the GUI's Settings → Notifications tab.

## How it works (under the hood)

1. **Adapter** builds the argv for the configured CLI agent, injecting the
   prompt text (or `--message-file` for openclaw), the `--model` flag, the
   bypass flags, and the task's `extra_args`.
2. **Runner** acquires an exclusive sentinel-file lock (works on both Linux
   and Windows), spawns the agent subprocess, tees stdout/stderr to a log
   file, scans each line for the latest `Saved:` / `저장 완료:` marker to
   capture the artifact path, and enforces a per-task timeout.
3. **Scheduler** registers the task with the OS scheduler
   (systemd user timer on Linux, Task Scheduler on Windows) so it runs
   `mdrunner run <id> --mode scheduled` at the configured time.
4. **Notification** (optional) — on failure, posts a short summary to a
   Telegram chat via the Bot API.

## Smoke test

`smoke_test.md` is a tiny task used to verify the end-to-end pipeline:

> Read the top 5 headlines from `https://finviz.com/news` and print each one
> as a bullet point. End with the line `Saved: /tmp/mdrunner_smoke_<UTC>.md`.

Use it to verify any agent after a config change:

```bash
# Write a one-task config pointing at smoke_test.md, then:
mdrunner run smoke_test --mode scheduled
# expect: exit 0, ~15-30s, /tmp/mdrunner_smoke_*.md contains 5 finviz headlines
```

## Tests

```bash
uv run pytest                 # headless engine + GUI smoke
uv run --extra dev ruff check .   # lint
```

24 tests cover adapters, config roundtrip, runner end-to-end, and a headless
GUI smoke (instantiates MainWindow off-screen and exercises key paths).

## Cross-platform notes

| Area | Linux | macOS | Windows |
|------|:-----:|:-----:|:-------:|
| CLI + PySide6 GUI | ✅ | ✅ | ✅ |
| Task scheduling | systemd user timer | launchd user agent | Task Scheduler (`schtasks`) |
| Quota poll timer | ✅ | ✅ | — |
| quota: codex / grok | ✅ | ✅ | ✅ |
| quota: claude / agy (PTY `/usage`) | ✅ | ✅ | — (`pty` is Unix-only) |
| Telegram, inline prompts, model list | ✅ | ✅ | ✅ |
| GUI launcher | `./run_gui.sh` | `./run_gui.sh` | `run_gui.bat` |

- **Linux** — `~/.config/systemd/user/`. Requires `systemctl` on PATH.
  `loginctl enable-linger <user>` lets tasks run with no interactive session.
- **macOS** — `~/Library/LaunchAgents/com.mdrunner.<id>.plist`, loaded with
  `launchctl`. The plist pins a `PATH` (Homebrew + nvm + `~/.local/bin`) so
  scheduled runs find the agent CLIs. launchd uses local time and has no
  per-job timezone, so a task's `schedule.timezone` is advisory only.
- **Windows** — Task Scheduler via `schtasks.exe`. Set `RUNCHER_EXECUTABLE`
  to control the invoked path (packaged builds do this). The interactive
  claude/agy quota scrape needs a Unix pty and is disabled here; codex
  (JSON-RPC) and grok (billing log) still work.

## License

MIT (or whatever you want — no license file is included yet).
