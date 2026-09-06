"""OpenAI Codex CLI adapter.

Default invocation:

    codex exec [--cd <cwd>] [bypass flags] [--model <model>] [extra args] -

The prompt is streamed on stdin instead of argv. This avoids a real Codex CLI
parsing edge case where prompts starting with `-`/`---` are treated as flags.

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
        prompt_text = read_prompt_text(prompt_file)
        # Put all options before the prompt sentinel.  We stream the real
        # prompt over stdin because Codex may parse a leading `---` in argv
        # as another option and exit 2.
        argv: list[str] = ["codex", "exec"]
        if working_dir is not None:
            argv += ["--cd", str(working_dir)]
        # `codex exec` is non-interactive when driven by mdrunner.  Keep
        # manual runs from blocking on an approval prompt when the user has
        # not configured an explicit bypass list.
        argv.extend(bypass_flags or ("--yolo",))
        if model:
            argv += ["--model", model]
        argv.extend(extra_args)
        argv.append("-")
        return ArgvResult(argv=argv, cwd=working_dir, stdin_text=prompt_text)
