#!/usr/bin/env bash
# Build mdrunner for the current OS.
# Output: dist/mdrunner (Linux/macOS) or dist/mdrunner.exe (Windows).
set -euo pipefail

cd "$(dirname "$0")/.."

echo "==> Cleaning previous build artifacts"
rm -rf build dist

echo "==> Syncing dependencies (with build extras)"
uv sync --extra gui --extra build

echo "==> Running PyInstaller"
uv run --extra build pyinstaller --noconfirm --clean installer/mdrunner.spec

echo
echo "==> Build complete"
ls -la dist/
echo
echo "Test it:"
echo "  ./dist/mdrunner --help"
echo "  ./dist/mdrunner list"
