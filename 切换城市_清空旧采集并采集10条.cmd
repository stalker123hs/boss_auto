@echo off
cd /d "%~dp0"
set /p CITY=City name (for example: Shanghai, Suzhou, Ningbo): 
if "%CITY%"=="" exit /b 1
".venv\Scripts\python.exe" embedded_auto.py crawl-test --new-run --cities "%CITY%"
pause
