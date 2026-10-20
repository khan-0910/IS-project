@echo off
rem EXOVET - one-time Windows setup (wraps win_setup.ps1)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0win_setup.ps1"
pause
