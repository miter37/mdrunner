"""Grok Build CLI adapter (xAI).

Default invocation:

    grok --prompt-file <prompt_file> [bypass flags] [--model <model>]
         [--cwd <cwd>] [extra args]

`grok` is normally an interactive TUI; passing `--prompt-file` (or `-p`)
puts it in single-turn headless mode: it prints the response to stdout
and exits. Grok accepts the prompt as a file, so — like openclaw — the
md file is passed by path rather than inlined.

Bypass flags: `["--permission-mode", "bypassPermissions"]` auto-approves
every tool call. Inferred default when the user leaves bypass empty
(headless mode otherwise stalls on the first permission prompt).
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from .base import AgentAdapter, ArgvResult


class GrokAdapter(AgentAdapter):
    id = "grok"
    binary = "grok"
    display_name = "Grok Build (xAI)"

    def build_argv(
        self,
        prompt_file: Path,
        *,
        model: str | None,
        working_dir: Path | None,
        extra_args: Sequence[str],
        bypass_flags: Sequence[str],
    ) -> ArgvResult:
        # Safe default: auto-approve all permissions for non-interactive runs.
        effective_bypass: Sequence[str] = (
            list(bypass_flags)
            if bypass_flags
            else ["--permission-mode", "bypassPermissions"]
        )
        argv: list[str] = ["grok", "--prompt-file", str(prompt_file)]
        argv.extend(effective_bypass)
        if model:
            argv += ["--model", model]
        if working_dir is not None:
            argv += ["--cwd", str(working_dir)]
        argv.extend(extra_args)
        return ArgvResult(argv=argv, cwd=working_dir)
