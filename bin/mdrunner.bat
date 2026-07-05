@echo off
REM ==============================================================
REM  mdrunner launcher (Windows)
REM
REM  Usage:
REM    bin\mdrunner.bat list
REM    bin\mdrunner.bat run smoke_test --mode scheduled
REM    bin\mdrunner.bat init
REM
REM  Resolution order:
REM    1. frozen binary at ..\dist\mdrunner.exe
REM    2. central venv at C:\Users\default\AppData\Local\Programs\Python\Python314\venv\Scripts\python.exe
REM       (override with MDRUNNER_PYTHON env var)
REM    3. PATH python + PYTHONPATH=..
REM ==============================================================

setlocal

set "SCRIPT_DIR=%~dp0"
pushd "%SCRIPT_DIR%.." >nul
set "MD_RUNNER_DIR=%CD%"
popd >nul

REM 1) Frozen binary
if exist "%MD_RUNNER_DIR%\dist\mdrunner.exe" (
    "%MD_RUNNER_DIR%\dist\mdrunner.exe" %*
    exit /b %errorlevel%
)

REM 2) Central venv (overridable)
if not defined MDRUNNER_PYTHON (
    if exist "C:\Users\default\AppData\Local\Programs\Python\Python314\venv\Scripts\python.exe" (
        set "MDRUNNER_PYTHON=C:\Users\default\AppData\Local\Programs\Python\Python314\venv\Scripts\python.exe"
    )
)
if defined MDRUNNER_PYTHON if exist "%MDRUNNER_PYTHON%" (
    set "PYTHONPATH=%MD_RUNNER_DIR%"
    "%MDRUNNER_PYTHON%" -m mdrunner %*
    exit /b %errorlevel%
)

REM 3) PATH python fallback
where python >nul 2>&1
if %errorlevel% equ 0 (
    set "PYTHONPATH=%MD_RUNNER_DIR%"
    python -m mdrunner %*
    exit /b %errorlevel%
)

echo Error: mdrunner launcher could not find a runnable interpreter. >&2
echo Tried: >&2
echo   - %MD_RUNNER_DIR%\dist\mdrunner.exe >&2
echo   - %MDRUNNER_PYTHON% >&2
echo   - python on PATH >&2
echo Run installer\build.ps1 to build the frozen binary, or install Python 3.14 + run 'pip install -e .' in this project. >&2
exit /b 1
