"""Allow `python -m mdrunner` to invoke either the CLI or the GUI.

Behavior:
    - With a known CLI subcommand (or --help/--version) → CLI
    - With --cli → CLI
    - With --gui → GUI
    - No args → CLI help (avoids headless hang on display-less systems)

Run the GUI explicitly with:
    mdrunner --gui
or
    MDRUNNER_GUI=1 mdrunner
"""

from __future__ import annotations

import os
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
        "quota",
        "quota-schedule",
        "quota-sink",
        "quota-sink-setup",
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
    # Explicit GUI flag or env var.
    if (args and args[0] == "--gui") or os.environ.get("MDRUNNER_GUI") == "1":
        try:
            from .ui.main_window import main as gui_main

            return gui_main()
        except ImportError as exc:
            print(
                f"PySide6 not available ({exc}). Install with: uv sync --extra gui",
                file=sys.stderr,
            )
            return 2
    # No args: show CLI help (instead of trying to launch the GUI, which hangs
    # in headless environments).
    from .cli import main as cli_main

    return cli_main(["--help"])


if __name__ == "__main__":
    sys.exit(main())