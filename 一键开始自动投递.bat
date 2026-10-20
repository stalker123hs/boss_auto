@echo off
chcp 65001 >nul
title BOSS 嵌入式自动投递
cd /d "%~dp0"
set "START_LOG=%~dp0启动日志.txt"
echo [%date% %time%] 启动器已运行 > "%START_LOG%"
echo BOSS 嵌入式自动投递正在启动...
echo 工作目录：%~dp0
echo.

if not exist ".venv\Scripts\python.exe" (
  echo [1/4] 正在创建运行环境...
  if exist "C:\Users\stalker\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" (
    "C:\Users\stalker\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m venv .venv
  ) else (
    python -m venv .venv
  )
)

if not exist ".venv\Scripts\python.exe" (
  echo 无法创建 Python 环境，请先安装 Python 3。
  echo Python 环境创建失败 >> "%START_LOG%"
  goto :end
)

if not exist ".venv\.deps_installed" (
  echo [2/4] 正在安装依赖，首次运行需要几分钟...
  call ".venv\Scripts\python.exe" -m pip install -r requirements.txt
  if errorlevel 1 (
    echo 依赖安装失败 >> "%START_LOG%"
    goto :end
  )
  type nul > ".venv\.deps_installed"
)

if /i "%BOSS_AUTO_CHECK_ONLY%"=="1" (
  call ".venv\Scripts\python.exe" embedded_auto.py check
  if errorlevel 1 echo 程序检查失败 >> "%START_LOG%"
  if not errorlevel 1 echo 程序检查通过 >> "%START_LOG%"
  goto :end
)

echo [3/4] 正在检查 BOSS 登录状态...
call ".venv\Scripts\python.exe" embedded_auto.py login
if errorlevel 1 (
  echo 登录步骤失败 >> "%START_LOG%"
  goto :end
)

echo [4/4] 登录成功，开始采集并自动投递...
call ".venv\Scripts\python.exe" embedded_auto.py production %*
if errorlevel 1 echo 自动投递异常结束 >> "%START_LOG%"
if not errorlevel 1 echo 自动投递正常结束 >> "%START_LOG%"

:end
echo.
echo 运行结束。日志位于 assets\embedded_runs
if /i not "%BOSS_AUTO_CHECK_ONLY%"=="1" pause
