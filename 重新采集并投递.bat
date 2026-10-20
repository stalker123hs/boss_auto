@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Python environment is missing. Run the setup/login launcher first.
  pause
  exit /b 1
)
echo Starting a fresh crawl and apply run using embedded_config.json...
call ".venv\Scripts\python.exe" embedded_auto.py production --new-run
set "EXIT_CODE=%ERRORLEVEL%"
echo.
if not "%EXIT_CODE%"=="0" echo The program ended with error code %EXIT_CODE%.
pause
exit /b %EXIT_CODE%
