"""Hermes Agent (Nous Research) CLI adapter.

Default invocation:

    hermes chat -q <prompt-text> [bypass flags] [--model <model>] [--add-dir <cwd>] [-Q]

`hermes chat` is normally an interactive TUI. `-q` makes it a one-shot query.
`-Q` (capital) suppresses the banner/spinner so output is clean for piping.

Bypass flag: `--yolo` auto-approves all tool calls. Inferred default if user
leaves bypass empty.
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from .base import AgentAdapter, ArgvResult, read_prompt_text


class HermesAdapter(AgentAdapter):
    id = "hermes"
    binary = "hermes"
    display_name = "Hermes Agent"

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
            list(bypass_flags) if bypass_flags else ["--yolo"]
        )
        argv: list[str] = ["hermes", "chat", "-q", read_prompt_text(prompt_file), "-Q"]
        argv.extend(effective_bypass)
        if model:
            argv += ["--model", model]
        if working_dir is not None:
            argv += ["--add-dir", str(working_dir)]
        argv.extend(extra_args)
        return ArgvResult(argv=argv, cwd=working_dir)
