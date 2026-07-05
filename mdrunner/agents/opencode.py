"""opencode CLI adapter.

Default invocation:

    opencode run <prompt-text> [bypass flags] [--model <model>] [extra args]

The prompt is the full md text (read by mdrunner). opencode has no
`--prompt-file` flag, so the text must be inlined.

Bypass flags: `["--auto"]` auto-approves any permission not explicitly denied.
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from .base import AgentAdapter, ArgvResult, read_prompt_text


class OpenCodeAdapter(AgentAdapter):
    id = "opencode"
    binary = "opencode"
    display_name = "OpenCode"

    def build_argv(
        self,
        prompt_file: Path,
        *,
        model: str | None,
        working_dir: Path | None,
        extra_args: Sequence[str],
        bypass_flags: Sequence[str],
    ) -> ArgvResult:
        argv: list[str] = ["opencode", "run", read_prompt_text(prompt_file)]
        argv.extend(bypass_flags)
        if model:
            argv += ["--model", model]
        argv.extend(extra_args)
        return ArgvResult(argv=argv, cwd=working_dir)