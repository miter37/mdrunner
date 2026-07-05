"""Entry-point script for the PyInstaller-frozen binary.

Imports mdrunner as a package first, then dispatches to the CLI's main().
Relative imports inside the mdrunner package work because mdrunner.* is
on sys.path as a real package (not just flat modules).
"""

import sys

from mdrunner.cli import main


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
