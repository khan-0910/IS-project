@echo off
rem EXOVET - double-click to start. First run installs everything (10-20 min).
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\win_run.ps1" -Setup
pause
