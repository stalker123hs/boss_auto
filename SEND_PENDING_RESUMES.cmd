@echo off
setlocal
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
title BOSS Send Pending Resumes
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Please run START_BOSS_AUTO.cmd first.
  pause
  exit /b 1
)
call ".venv\Scripts\python.exe" scripts\repair_pending_attachments.py
echo.
pause
endlocal
