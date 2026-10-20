@echo off
cd /d "%~dp0"
".venv\Scripts\python.exe" embedded_auto.py crawl-test --new-run
pause
