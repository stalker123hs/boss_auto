@echo off
setlocal
cd /d "%~dp0"
if not exist "custom_profile.json" (
  copy /y "custom_profile_template.json" "custom_profile.json" >nul
  start "" notepad "custom_profile.json"
  echo 请先保存 custom_profile.json，然后再次双击本文件。
  pause
  exit /b 0
)
call "%~dp0START_BOSS_AUTO.cmd" --config "custom_profile.json" --new-run
endlocal
