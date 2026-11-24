@echo off
chcp 65001 >nul
title CS2 WORKBENCH
cd /d "%~dp0"

rem 优先启动打包好的 exe；没有 exe 时回退到源码运行
if exist "%~dp0CS2Workbench.exe" (
  start "" "%~dp0CS2Workbench.exe"
  exit /b 0
)

set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"

set "PY="
where python >nul 2>nul && set "PY=python"
if not defined PY (
  where py >nul 2>nul && set "PY=py -3"
)
if not defined PY (
  echo.
  echo   [x] 没有找到 CS2Workbench.exe，也没有 Python 3。
  echo       直接双击 CS2Workbench.exe 即可，或安装 Python 3.10+ 后重试。
  echo.
  pause
  exit /b 1
)

%PY% server.py
echo.
echo   Workbench stopped.
pause
