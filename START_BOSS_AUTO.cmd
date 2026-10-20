@echo off
setlocal
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
title BOSS Auto Apply
cd /d "%~dp0"
set "START_LOG=%~dp0launcher.log"
echo [%date% %time%] launcher started > "%START_LOG%"
echo Starting BOSS auto apply...
echo Folder: %~dp0
echo.

if not exist ".venv\Scripts\python.exe" (
  echo [1/4] Creating Python environment...
  if exist "C:\Users\stalker\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" (
    "C:\Users\stalker\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m venv .venv
  ) else (
    python -m venv .venv
  )
)

if not exist ".venv\Scripts\python.exe" (
  echo ERROR: Python 3 is required.
  echo Python environment creation failed >> "%START_LOG%"
  goto :end
)

if not exist ".venv\.deps_installed" (
  echo [2/4] Installing dependencies. This may take several minutes...
  call ".venv\Scripts\python.exe" -m pip install -r requirements.txt
  if errorlevel 1 (
    echo Dependency installation failed >> "%START_LOG%"
    goto :end
  )
  type nul > ".venv\.deps_installed"
)

if /i "%BOSS_AUTO_CHECK_ONLY%"=="1" (
  call ".venv\Scripts\python.exe" embedded_auto.py check
  if errorlevel 1 echo Program check failed >> "%START_LOG%"
  if not errorlevel 1 echo Program check passed >> "%START_LOG%"
  goto :end
)

echo [3/4] Checking BOSS login...
call ".venv\Scripts\python.exe" embedded_auto.py login
if errorlevel 1 (
  echo Login step failed >> "%START_LOG%"
  goto :end
)

echo [4/4] Login OK. Resuming the current task or applying collected jobs...
call ".venv\Scripts\python.exe" embedded_auto.py production --skip-crawl %*
if errorlevel 1 echo Auto apply ended with an error >> "%START_LOG%"
if not errorlevel 1 echo Auto apply finished normally >> "%START_LOG%"

:end
echo.
echo Finished. See launcher.log and assets\embedded_runs for details.
if /i not "%BOSS_AUTO_CHECK_ONLY%"=="1" pause
endlocal
