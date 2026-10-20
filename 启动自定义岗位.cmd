@echo off
setlocal
cd /d "%~dp0"
if not exist "custom_profile.json" (
  copy /y "custom_profile_template.json" "custom_profile.json" >nul
  start "" notepad "custom_profile.json"
  echo Edit and save custom_profile.json, then run this launcher again.
  pause
  exit /b
)
call "%~dp0START_BOSS_AUTO.cmd" --config "custom_profile.json" --skip-crawl
endlocal
