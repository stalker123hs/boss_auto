@echo off
cd /d "%~dp0"
".venv\Scripts\python.exe" embedded_auto.py test --skip-crawl --new-run
set "RC=%ERRORLEVEL%"
echo.
echo Exit code: %RC%
pause
exit /b %RC%
