@echo off
REM Double-click this with Rocket League running (in a match if you can).
REM It prints a diagnosis and saves it to logs\diag-stats-api.txt.
setlocal
cd /d "%~dp0"

set "PY_EXE="
for /f "delims=" %%i in ('where python.exe 2^>nul') do if not defined PY_EXE set "PY_EXE=%%i"

if not defined PY_EXE (
    where py >nul 2>&1 && set "PY_EXE=py"
)

if not defined PY_EXE (
    echo Python is not on PATH. Install Python 3.10+ and tick "Add Python to PATH".
    pause
    exit /b 1
)

"%PY_EXE%" "%~dp0diag_stats_api.py"
echo.
pause
