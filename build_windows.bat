@echo off
REM Builds SerialMonitor (folder + portable zip) and, if Inno Setup 6 is installed, the installer.
setlocal
cd /d %~dp0
set /p VER=<VERSION
python -m venv .venv || goto :err
call .venv\Scripts\activate.bat
python -m pip install -q --upgrade pip
pip install -q -r requirements-desktop.txt || goto :err
if not exist assets\icon.ico python tools\make_icon.py
pyinstaller --noconfirm serial_monitor.spec || goto :err
echo Running self-test of the packaged app...
start /wait "" dist\SerialMonitor\SerialMonitor.exe --selftest --data-dir "%TEMP%\sm_selftest"
if errorlevel 1 ( echo SELFTEST FAILED - see %TEMP%\sm_selftest\selftest.txt & goto :err )
type "%TEMP%\sm_selftest\selftest.txt"
powershell -NoProfile -Command "Compress-Archive -Force dist\SerialMonitor dist\SerialMonitor-%VER%-portable.zip"
set ISCC=
for %%P in ("%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" "%ProgramFiles%\Inno Setup 6\ISCC.exe" "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe") do if exist %%P set ISCC=%%~P
if defined ISCC (
  if not exist redist\MicrosoftEdgeWebview2Setup.exe (
    mkdir redist 2>nul
    powershell -NoProfile -Command "try { Invoke-WebRequest https://go.microsoft.com/fwlink/p/?LinkId=2124703 -OutFile redist\MicrosoftEdgeWebview2Setup.exe } catch { Write-Host 'WebView2 bootstrapper download skipped' }"
  )
  "%ISCC%" /DAppVersion=%VER% installer.iss || goto :err
  echo.& echo DONE: dist\SerialMonitor-Setup-%VER%.exe
) else (
  echo.& echo Inno Setup 6 not found - installer skipped. Get it from https://jrsoftware.org/isinfo.php
  echo Portable build: dist\SerialMonitor\SerialMonitor.exe  and  dist\SerialMonitor-%VER%-portable.zip
)
exit /b 0
:err
echo Build failed.
exit /b 1
