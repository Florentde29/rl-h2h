@echo off
REM One-click installer wrapper. Double-click this file.
REM All real work happens in install.ps1 (kept here so users can read it).
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1"
echo.
pause
