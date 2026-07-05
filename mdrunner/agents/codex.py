"""OpenAI Codex CLI adapter.

Default invocation:

    codex exec <prompt-text> [--cd <cwd>] [bypass flags] [--model <model>] [extra args]

The prompt is the full md text (codex exec reads from argv).

Bypass flags: `["--dangerously-bypass-approvals-and-sandbox"]` (alias `--yolo`)
or `["--sandbox", "workspace-write", "--ask-for-approval", "never"]` for
a less aggressive default. Both are configured per-agent in settings.yaml.
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from .base import AgentAdapter, ArgvResult, read_prompt_text


class CodexAdapter(AgentAdapter):
    id = "codex"
    binary = "codex"
    display_name = "Codex"

    def build_argv(
        self,
        prompt_file: Path,
        *,
        model: str | None,
        working_dir: Path | None,
        extra_args: Sequence[str],
        bypass_flags: Sequence[str],
    ) -> ArgvResult:
        argv: list[str] = ["codex", "exec", read_prompt_text(prompt_file)]
        if working_dir is not None:
            argv += ["--cd", str(working_dir)]
        argv.extend(bypass_flags)
        if model:
            argv += ["--model", model]
        argv.extend(extra_args)
        return ArgvResult(argv=argv, cwd=working_dir)