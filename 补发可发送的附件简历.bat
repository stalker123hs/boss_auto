@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo 请先双击“安装依赖并登录.bat”
  pause
  exit /b 1
)
call ".venv\Scripts\python.exe" scripts\repair_pending_attachments.py
echo.
pause
