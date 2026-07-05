"""Claude Code CLI adapter (Anthropic).

Default invocation:

    claude -p <prompt-text> [bypass flags] [--model <model>] [extra args]

`claude -p` is the non-interactive "print" mode that exits after one turn.
No `--prompt-file` flag exists; the prompt is inlined as argv[2].

Bypass flag: `--dangerously-skip-permissions` auto-approves all tool calls
without confirmation prompts. Inferred default if user leaves bypass empty.
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from .base import AgentAdapter, ArgvResult, read_prompt_text


class ClaudeAdapter(AgentAdapter):
    id = "claude"
    binary = "claude"
    display_name = "Claude Code"

    def build_argv(
        self,
        prompt_file: Path,
        *,
        model: str | None,
        working_dir: Path | None,
        extra_args: Sequence[str],
        bypass_flags: Sequence[str],
    ) -> ArgvResult:
        # Safe default: if user left bypass empty, auto-approve all permissions.
        # Non-interactive (-p) mode without auto-approve hangs waiting for input.
        effective_bypass: Sequence[str] = (
            list(bypass_flags) if bypass_flags else ["--dangerously-skip-permissions"]
        )
        argv: list[str] = ["claude", "-p", read_prompt_text(prompt_file)]
        argv.extend(effective_bypass)
        if model:
            argv += ["--model", model]
        if working_dir is not None:
            argv += ["--add-dir", str(working_dir)]
        argv.extend(extra_args)
        return ArgvResult(argv=argv, cwd=working_dir)
