@echo off
chcp 65001 >nul
if /i "%BOSS_COLLECT_CHECK_ONLY%"=="1" (
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\run_formal_collect.ps1" -CheckOnly -NoPause
  exit /b %ERRORLEVEL%
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\run_formal_collect.ps1"
