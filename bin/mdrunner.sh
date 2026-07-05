#!/usr/bin/env bash
# ==============================================================
#  mdrunner launcher (Linux / macOS)
#
#  Usage:
#    bin/mdrunner.sh list
#    bin/mdrunner.sh run smoke_test --mode scheduled
#    bin/mdrunner.sh init
#    bin/mdrunner.sh --gui        # launch the PySide6 GUI
#
#  Resolution order:
#    CLI mode (default):
#      1. frozen binary at ../dist/mdrunner
#      2. central venv at $MDRUNNER_PYTHON or default /home/doyoonkim/APPs/Python314/venv/bin/python3.14
#      3. PATH python3 + PYTHONPATH=..
#    GUI mode (--gui or MDRUNNER_GUI=1):
#      Always via the central venv, since the frozen binary is CLI-only.
# ==============================================================

set -euo pipefail

# Resolve the real path of this script — when called as `mdrunner` (via PATH),
# $0 is just "mdrunner", not an absolute path, so `dirname` would yield "."
# instead of the actual script directory. Resolve via readlink/which first.
SCRIPT_PATH="$(readlink -f "$0" 2>/dev/null || true)"
if [ -z "$SCRIPT_PATH" ] || [ ! -e "$SCRIPT_PATH" ]; then
    SCRIPT_PATH="$0"
fi
SCRIPT_DIR="$(cd "$(dirname "$SCRIPT_PATH")" && pwd)"
MD_RUNNER_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# Detect GUI mode: --gui flag or MDRUNNER_GUI=1 env var
GUI_MODE=0
if [ "${1:-}" = "--gui" ] || [ -n "${MDRUNNER_GUI:-}" ]; then
    GUI_MODE=1
fi

# Central venv (overridable)
VENV_PYTHON="${MDRUNNER_PYTHON:-/home/doyoonkim/APPs/Python314/venv/bin/python3.14}"

# GUI mode → always use python (frozen binary is CLI-only)
if [ "$GUI_MODE" -eq 1 ]; then
    if [ -x "$VENV_PYTHON" ]; then
        export PYTHONPATH="$MD_RUNNER_DIR"
        exec "$VENV_PYTHON" -m mdrunner --gui "$@"
    fi
    if command -v python3 >/dev/null 2>&1; then
        export PYTHONPATH="$MD_RUNNER_DIR"
        exec python3 -m mdrunner --gui "$@"
    fi
    echo "Error: GUI mode needs a Python interpreter (frozen binary is CLI-only)." >&2
    echo "Set MDRUNNER_PYTHON or install python3 with the central venv." >&2
    exit 1
fi

# CLI mode
# 1) Frozen binary
if [ -x "$MD_RUNNER_DIR/dist/mdrunner" ]; then
    exec "$MD_RUNNER_DIR/dist/mdrunner" "$@"
fi

# 2) Central venv
if [ -x "$VENV_PYTHON" ]; then
    export PYTHONPATH="$MD_RUNNER_DIR"
    exec "$VENV_PYTHON" -m mdrunner "$@"
fi

# 3) PATH python3 fallback
if command -v python3 >/dev/null 2>&1; then
    export PYTHONPATH="$MD_RUNNER_DIR"
    exec python3 -m mdrunner "$@"
fi

echo "Error: mdrunner launcher could not find a runnable interpreter." >&2
echo "Tried:" >&2
echo "  - $MD_RUNNER_DIR/dist/mdrunner" >&2
echo "  - $VENV_PYTHON  (override with MDRUNNER_PYTHON env var)" >&2
echo "  - python3 on PATH" >&2
echo "Run installer/build.sh to build the frozen binary, or set MDRUNNER_PYTHON." >&2
exit 1
