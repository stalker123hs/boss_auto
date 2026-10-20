@echo off
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" "scripts\review_missed_jobs.py" %*
) else (
  py -3 "scripts\review_missed_jobs.py" %*
)
pause