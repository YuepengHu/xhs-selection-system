@echo off
chcp 65001 >nul
if not defined XHS_PORT set XHS_PORT=8765
set FOUND=
for /f "tokens=5" %%p in ('netstat -ano ^| findstr /C:":%XHS_PORT% " ^| findstr LISTENING') do (
  taskkill /PID %%p /F >nul 2>nul
  set FOUND=1
)
if defined FOUND (echo 已停止。) else (echo 没有正在运行的监控服务。)
pause
