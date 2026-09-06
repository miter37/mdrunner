#!/usr/bin/env bash
# ==============================================================
#  mdrunner — launch the desktop GUI directly.
#
#  Same interpreter resolution as run.sh, but always in GUI mode.
#  Extra args are forwarded, e.g.:
#      ./run_gui.sh
#      QT_QPA_PLATFORM=xcb ./run_gui.sh      # force a Qt backend
# ==============================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

if command -v uv >/dev/null 2>&1; then
    # --extra gui pulls PySide6 into the project venv on first run.
    exec uv run --extra gui python -m mdrunner --gui "$@"
elif [ -x "$SCRIPT_DIR/.venv/bin/python" ]; then
    exec "$SCRIPT_DIR/.venv/bin/python" -m mdrunner --gui "$@"
elif [ -x "$SCRIPT_DIR/bin/mdrunner.sh" ]; then
    exec "$SCRIPT_DIR/bin/mdrunner.sh" --gui "$@"
elif command -v python3 >/dev/null 2>&1; then
    export PYTHONPATH="$SCRIPT_DIR"
    exec python3 -m mdrunner --gui "$@"
else
    echo "Error: could not find uv, a local .venv, bin/mdrunner.sh, or python3." >&2
    echo "Install deps with:  uv sync --extra gui" >&2
    exit 1
fi
