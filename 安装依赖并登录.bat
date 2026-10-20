@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  if exist "C:\Users\stalker\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" (
    "C:\Users\stalker\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m venv .venv
  ) else (
    python -m venv .venv
  )
)
if not exist ".venv\Scripts\python.exe" (
  echo 无法创建 Python 环境，请先安装 Python 3。
  goto :end
)
call ".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto :end
call ".venv\Scripts\python.exe" embedded_auto.py login
:end
echo.
pause
