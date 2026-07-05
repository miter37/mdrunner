"""Allow `python -m mdrunner` to invoke either the CLI or the GUI.

Default: GUI if PySide6 is importable and no extra args, else CLI.
Use `python -m mdrunner run <id>` (or any other subcommand) to force CLI.
Use `python -m mdrunner --cli <subcommand>` to force CLI explicitly.
"""

from __future__ import annotations

import sys
from typing import Sequence


def main(argv: Sequence[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    # If a known CLI subcommand is first, hand off to the CLI.
    if args and args[0] in {
        "list",
        "preview",
        "run",
        "validate",
        "health",
        "init",
        "schedule-install",
        "schedule-uninstall",
        "schedule-status",
    }:
        from .cli import main as cli_main

        return cli_main(args)
    # If --cli passed, strip it and forward.
    if args and args[0] == "--cli":
        from .cli import main as cli_main

        return cli_main(args[1:])
    # Standard help / version flags → also CLI (so `mdrunner --help` doesn't
    # try to launch the GUI without a display).
    if args and args[0] in {"-h", "--help", "--version"}:
        from .cli import main as cli_main

        return cli_main(args)
    # Otherwise, launch the GUI.
    try:
        from .ui.main_window import main as gui_main

        return gui_main()
    except ImportError as exc:
        print(
            f"PySide6 not available ({exc}). Install with: uv sync --extra gui",
            file=sys.stderr,
        )
        print("Or run a CLI command: mdrunner list | preview | run | validate | health | init", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())