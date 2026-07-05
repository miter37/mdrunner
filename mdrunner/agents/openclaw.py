"""openclaw agent adapter.

Default invocation:

    openclaw agent --local --message-file <prompt_file> [bypass flags]
                   [--model <model>] [extra args]

openclaw is the only one of the three that takes the prompt as a file
via `--message-file` (UTF-8). It also strips an optional BOM and rejects
non-UTF-8 input, which is convenient for Windows-saved md files.

Bypass flags: `["--local"]` switches to embedded execution (skips Gateway
round-trip). For scheduled cron runs this avoids depending on a long-lived
Gateway daemon.
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from .base import AgentAdapter, ArgvResult


class OpenClawAdapter(AgentAdapter):
    id = "openclaw"
    binary = "openclaw"
    display_name = "OpenClaw"

    def build_argv(
        self,
        prompt_file: Path,
        *,
        model: str | None,
        working_dir: Path | None,
        extra_args: Sequence[str],
        bypass_flags: Sequence[str],
    ) -> ArgvResult:
        argv: list[str] = [
            "openclaw",
            "agent",
            "--local",
            "--message-file",
            str(prompt_file),
        ]
        argv.extend(bypass_flags)
        if model:
            argv += ["--model", model]
        argv.extend(extra_args)
        return ArgvResult(argv=argv, cwd=working_dir)