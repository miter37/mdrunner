@echo off
REM ==============================================================
REM  mdrunner - launch the desktop GUI directly (Windows).
REM  Same interpreter resolution as bin\mdrunner.bat, always --gui.
REM      run_gui.bat
REM ==============================================================
setlocal
set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"

where uv >nul 2>&1
if %errorlevel% equ 0 (
    uv run --extra gui python -m mdrunner --gui %*
    exit /b %errorlevel%
)
if exist "%SCRIPT_DIR%.venv\Scripts\python.exe" (
    "%SCRIPT_DIR%.venv\Scripts\python.exe" -m mdrunner --gui %*
    exit /b %errorlevel%
)
if exist "%SCRIPT_DIR%bin\mdrunner.bat" (
    call "%SCRIPT_DIR%bin\mdrunner.bat" --gui %*
    exit /b %errorlevel%
)
where python >nul 2>&1
if %errorlevel% equ 0 (
    set "PYTHONPATH=%SCRIPT_DIR%"
    python -m mdrunner --gui %*
    exit /b %errorlevel%
)
echo Error: could not find uv, a local .venv, bin\mdrunner.bat, or python. >&2
echo Install deps with:  uv sync --extra gui >&2
exit /b 1
