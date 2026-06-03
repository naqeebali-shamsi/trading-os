@echo off
setlocal
set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"

net session >nul 2>&1
if errorlevel 1 (
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

powershell -NoProfile -ExecutionPolicy Bypass -STA -WindowStyle Normal ^
  -File "%ROOT%\installer\install_wizard.ps1" -InstallRoot "%ROOT%" -SetupBridge -Mandatory
exit /b %ERRORLEVEL%
