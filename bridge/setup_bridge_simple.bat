@echo off
REM Simplified MT5 bridge setup — uses TRADING_OS_ROOT and global Common Files junction.
setlocal EnableDelayedExpansion

if not defined TRADING_OS_ROOT (
    for %%I in ("%~dp0..") do set "TRADING_OS_ROOT=%%~fI"
)

set "SRC_EX5=%TRADING_OS_ROOT%\bridge\FileBridgeEA_Windows.ex5"
set "IPC_WIN=%TRADING_OS_ROOT%\ipc"
set "JUNCTION=%APPDATA%\MetaQuotes\Terminal\Common\Files\trading-os"
set "MT5_EDITOR=C:\Program Files\MetaTrader 5\MetaEditor64.exe"

if not exist "%SRC_EX5%" (
    echo [!] Missing %SRC_EX5%
    echo     Run bridge\compile_bridge.ps1 or compile_on_windows.bat first.
    pause
    exit /b 1
)

set "NEWEST_AGE=0"
set "ACTIVE_DIR="
for /d %%D in ("%APPDATA%\MetaQuotes\Terminal\*") do (
    if exist "%%D\logs" (
        for /f %%F in ('powershell -NoProfile -Command "(Get-ChildItem -LiteralPath '%%D\logs\*.log' -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1).LastWriteTime.ToString('yyyyMMddHHmmss')"') do (
            if %%F gtr !NEWEST_AGE! (
                set "NEWEST_AGE=%%F"
                set "ACTIVE_DIR=%%D"
            )
        )
    )
)

if not defined ACTIVE_DIR (
    echo [!] No MT5 terminal found under %APPDATA%\MetaQuotes\Terminal\
    pause
    exit /b 1
)

echo [ACTIVE] %ACTIVE_DIR%
set "EXPERTS_DIR=%ACTIVE_DIR%\MQL5\Experts"

echo [COPY] %SRC_EX5% --^> %EXPERTS_DIR%
if not exist "%EXPERTS_DIR%" mkdir "%EXPERTS_DIR%"
copy /Y "%SRC_EX5%" "%EXPERTS_DIR%\FileBridgeEA_Windows.ex5"
if %errorlevel% neq 0 (
    echo [!] Copy failed. Run as Administrator.
    pause
    exit /b 1
)

echo [JUNCTION] %JUNCTION% ^<--^> %IPC_WIN%
if exist "%JUNCTION%" rmdir "%JUNCTION%" 2>nul
mklink /J "%JUNCTION%" "%IPC_WIN%"
if %errorlevel% neq 0 (
    echo [!] Junction failed. Run as Administrator.
    pause
    exit /b 1
)

echo.
echo DONE — restart MT5, attach FileBridgeEA_Windows, enable Algo Trading.
echo InpIpcDir must stay trading-os/ (default).
pause
