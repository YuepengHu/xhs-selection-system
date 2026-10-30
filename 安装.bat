@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo == 小红书选品系统：安装（Windows）==

where winget >nul 2>nul
if errorlevel 1 (
  echo 没有找到 winget。请先在 Microsoft Store 安装“应用安装程序”，或升级到 Windows 10 1809 以上版本。
  pause
  exit /b 1
)

where uv >nul 2>nul
if errorlevel 1 (
  echo 安装 uv ...
  winget install --id astral-sh.uv -e --accept-source-agreements --accept-package-agreements
)

where adb >nul 2>nul
if errorlevel 1 (
  echo 安装 adb（连接安卓手机用）...
  winget install --id Google.PlatformTools -e --accept-source-agreements --accept-package-agreements
)

set "CHROME="
if exist "%ProgramFiles%\Google\Chrome\Application\chrome.exe" set "CHROME=1"
if exist "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe" set "CHROME=1"
if exist "%LocalAppData%\Google\Chrome\Application\chrome.exe" set "CHROME=1"
if not defined CHROME (
  echo 没有找到 Google Chrome，请先安装：https://www.google.cn/chrome/
  pause
  exit /b 1
)

where uv >nul 2>nul
if errorlevel 1 (
  echo.
  echo 刚装好的软件要重新打开窗口才生效。请关闭本窗口，再双击一次“安装.bat”。
  pause
  exit /b 0
)

echo 安装程序依赖 ...
set PYTHONUTF8=1
uv sync
if errorlevel 1 (
  echo 依赖安装失败，请检查网络后重试。
  pause
  exit /b 1
)

echo.
echo 安装完成。以后双击“启动.bat”即可使用。
pause
