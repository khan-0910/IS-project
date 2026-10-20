@echo off
rem EXOVET - start backend + frontend (wraps win_run.ps1). Pass backend|frontend to start one.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0win_run.ps1" %1
pause
