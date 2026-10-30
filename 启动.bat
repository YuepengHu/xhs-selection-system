@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
if not defined XHS_PORT set XHS_PORT=8765

where uv >nul 2>nul
if errorlevel 1 (
  echo 还没有安装，请先双击“安装.bat”。
  pause
  exit /b 1
)

netstat -ano | findstr /C:":%XHS_PORT% " | findstr LISTENING >nul
if not errorlevel 1 (
  echo 已经在运行了，直接打开浏览器。
  start "" "http://127.0.0.1:%XHS_PORT%"
  exit /b 0
)

echo == 启动中，请不要关闭这个窗口（关闭窗口会停止监控）==
echo 浏览器地址：http://127.0.0.1:%XHS_PORT%
start "" /b cmd /c "timeout /t 4 /nobreak >nul & start http://127.0.0.1:%XHS_PORT%"
uv run uvicorn xhs_monitor.app:app --host 127.0.0.1 --port %XHS_PORT%
pause
