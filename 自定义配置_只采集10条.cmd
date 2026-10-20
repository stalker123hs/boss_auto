@echo off
cd /d "%~dp0"
if not exist "custom_profile.json" (
  copy /y "custom_profile_template.json" "custom_profile.json" >nul
  start "" notepad "custom_profile.json"
  exit /b 0
)
".venv\Scripts\python.exe" embedded_auto.py crawl-test --config custom_profile.json --new-run
pause
