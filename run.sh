#!/usr/bin/env bash
# ==============================================================
#  mdrunner Launcher Script
#  
#  Usage:
#    ./run.sh [args]
#    ./run.sh --gui
# ==============================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Ensure we run from the project root directory
cd "$SCRIPT_DIR"

# 1. Try running with uv (recommended)
if command -v uv >/dev/null 2>&1; then
    exec uv run python -m mdrunner "$@"
# 2. Try running with the local virtual environment (.venv)
elif [ -x "$SCRIPT_DIR/.venv/bin/python" ]; then
    exec "$SCRIPT_DIR/.venv/bin/python" -m mdrunner "$@"
# 3. Try running via the bin/mdrunner.sh launcher
elif [ -x "$SCRIPT_DIR/bin/mdrunner.sh" ]; then
    exec "$SCRIPT_DIR/bin/mdrunner.sh" "$@"
# 4. Fallback to system python3
elif command -v python3 >/dev/null 2>&1; then
    export PYTHONPATH="$SCRIPT_DIR"
    exec python3 -m mdrunner "$@"
else
    echo "Error: Could not find uv, local .venv, bin/mdrunner.sh, or python3 on PATH." >&2
    exit 1
fi
