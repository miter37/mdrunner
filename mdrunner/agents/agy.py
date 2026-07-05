"""Google Antigravity CLI (agy) adapter.

Default invocation:

    agy -p <prompt-text> [bypass flags] [--model <model>] [--add-dir <cwd>] [extra args]

Antigravity is Gemini-native. Default model in the bundled config is
"Gemini 3.5 Flash (Medium)" (verified via `agy models`).

Bypass flag: `--dangerously-skip-permissions` auto-approves all tool calls.
Inferred default if user leaves bypass empty.
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from .base import AgentAdapter, ArgvResult, read_prompt_text


class AgyAdapter(AgentAdapter):
    id = "agy"
    binary = "agy"
    display_name = "Antigravity (Google)"

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
            list(bypass_flags) if bypass_flags else ["--dangerously-skip-permissions"]
        )
        argv: list[str] = ["agy", "-p", read_prompt_text(prompt_file)]
        argv.extend(effective_bypass)
        if model:
            argv += ["--model", model]
        if working_dir is not None:
            argv += ["--add-dir", str(working_dir)]
        argv.extend(extra_args)
        return ArgvResult(argv=argv, cwd=working_dir)
