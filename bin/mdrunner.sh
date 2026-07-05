#!/usr/bin/env bash
# ==============================================================
#  mdrunner launcher (Linux / macOS)
#
#  Usage:
#    bin/mdrunner.sh list
#    bin/mdrunner.sh run smoke_test --mode scheduled
#    bin/mdrunner.sh init
#
#  Resolution order:
#    1. frozen binary at ../dist/mdrunner
#    2. central venv at $MDRUNNER_PYTHON or default /home/doyoonkim/APPs/Python314/venv/bin/python3.14
#    3. PATH python3 + PYTHONPATH=..
# ==============================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
MD_RUNNER_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# 1) Frozen binary
if [ -x "$MD_RUNNER_DIR/dist/mdrunner" ]; then
    exec "$MD_RUNNER_DIR/dist/mdrunner" "$@"
fi

# 2) Central venv (overridable)
VENV_PYTHON="${MDRUNNER_PYTHON:-/home/doyoonkim/APPs/Python314/venv/bin/python3.14}"
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
