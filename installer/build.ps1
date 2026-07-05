# Build mdrunner for Windows. Run from PowerShell in the repo root.
# Output: dist\mdrunner.exe
$ErrorActionPreference = "Stop"

Set-Location (Join-Path $PSScriptRoot "..")

Write-Host "==> Cleaning previous build artifacts"
Remove-Item -Recurse -Force build, dist -ErrorAction SilentlyContinue

Write-Host "==> Syncing dependencies (with build extras)"
uv sync --extra gui --extra build

Write-Host "==> Running PyInstaller"
uv run --extra build pyinstaller --noconfirm --clean installer/mdrunner.spec

Write-Host ""
Write-Host "==> Build complete"
Get-ChildItem dist
Write-Host ""
Write-Host "Test it:"
Write-Host "  .\dist\mdrunner.exe --help"
Write-Host "  .\dist\mdrunner.exe list"
