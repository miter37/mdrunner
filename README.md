# mdrunner

`mdrunner` schedules and runs md-driven CLI agent tasks across Linux and Windows.

Each task points at a single instruction md file and is executed by a configured CLI
agent (`opencode`, `codex`, `openclaw`, `claude`, `agy`, …) at a scheduled time. The CLI
receives the md content as its prompt, performs whatever the md describes, and writes
its own output to disk. `mdrunner` orchestrates the invocation, captures logs, parses
the saved-file marker, and reports the result.

Three delivery modes:

- **Headless CLI** — `mdrunner list | preview | run | validate | health | init | schedule-install …`
- **GUI** — `mdrunner` (launches a PySide6 desktop window)
- **Frozen binary** — `dist/mdrunner` (or `dist\mdrunner.exe`), single file, no Python required

## Install

```bash
git clone <repo>
cd mdrunner
uv sync --extra gui --extra build
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
uv run mdrunner
```

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
│   ├── telegram.py                   # optional failure notifications
│   ├── agents/
│   │   ├── base.py
│   │   ├── opencode.py
│   │   ├── codex.py
│   │   └── openclaw.py
│   ├── scheduler/
│   │   ├── base.py
│   │   ├── linux.py                  # systemd user timer
│   │   └── windows.py                # Task Scheduler (schtasks)
│   ├── ui/                           # PySide6 GUI
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

**Verify available models:** `agy models`, `claude --models`, `opencode models`, `codex --models`.
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

- **Linux** — `~/.config/systemd/user/`. Requires `systemctl` on PATH.
  Tasks run as the logged-in user. `loginctl enable-linger <user>` lets
  them run even when no interactive session is active.
- **Windows** — Task Scheduler via `schtasks.exe`. The mdrunner binary
  must be at a stable absolute path. Set `RUNCHER_EXECUTABLE` to control
  which path the OS scheduler invokes (set by packaged builds).

## License

MIT (or whatever you want — no license file is included yet).
