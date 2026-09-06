"""Agent adapter protocol + shared utilities.

Each adapter encapsulates the quirks of one CLI agent. Adapters are
plain Python classes — adding a new agent means writing one class and
registering it in `mdrunner/agents/__init__.py`.
"""

from __future__ import annotations

import shutil
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


@dataclass
class ArgvResult:
    argv: list[str]
    cwd: Path | None
    stdin_text: str | None = None


class AgentAdapter(ABC):
    """Base class for CLI agent adapters.

    Concrete subclasses declare `id` and `binary` and implement
    `build_argv` to assemble the argv that should be executed.
    """

    id: str = ""
    binary: str = ""
    display_name: str = ""

    @abstractmethod
    def build_argv(
        self,
        prompt_file: Path,
        *,
        model: str | None,
        working_dir: Path | None,
        extra_args: Sequence[str],
        bypass_flags: Sequence[str],
    ) -> ArgvResult:
        """Build the argv that should be invoked.

        Returns a list ready to pass to subprocess and the directory
        the process should start in.
        """


def resolve_binary(binary: str) -> str | None:
    """Resolve a binary name or absolute path to an executable path.

    Absolute paths are returned as-is if the file exists. Bare names are
    looked up via PATH. Returns None when the binary cannot be found.
    """
    p = Path(binary)
    if p.is_absolute():
        return str(p) if p.exists() else None
    return shutil.which(binary)


def read_prompt_text(prompt_file: Path) -> str:
    return prompt_file.read_text(encoding="utf-8")
