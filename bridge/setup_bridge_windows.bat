@echo off
REM Trading OS — Setup FileBridgeEA on Windows MT5
REM Uses TRADING_OS_ROOT when set; otherwise script directory parent.

setlocal EnableDelayedExpansion

if defined TRADING_OS_ROOT (
    set "REPO=%TRADING_OS_ROOT%"
) else (
    set "REPO=%~dp0.."
)
for %%I in ("%REPO%") do set "REPO=%%~fI"

set "SRC_EX5=%REPO%\bridge\FileBridgeEA_Windows.ex5"
set "IPC_WIN=%REPO%\ipc"
set "GLOBAL_COMMON=%USERPROFILE%\AppData\Roaming\MetaQuotes\Terminal\Common\Files"

echo [ROOT] %REPO%
echo [IPC ] %IPC_WIN%

if not exist "%SRC_EX5%" (
    echo [!] Compiled EA not found: %SRC_EX5%
    echo     Compile bridge\FileBridgeEA_Windows.mq5 in MetaEditor first.
    pause
    exit /b 1
)

echo [1/3] Detecting active Terminal...
set "ACTIVE_DIR="
for /f "delims=" %%F in ('powershell -NoProfile -c "gci -Path '%USERPROFILE%\AppData\Roaming\MetaQuotes\Terminal\*\logs\*.log' -EA SilentlyContinue ^| Sort LastWriteTime -Descending ^| Select -First 1 ^| %% { $_.Directory.Parent.FullName }"') do (
    set "ACTIVE_DIR=%%F"
)
if not defined ACTIVE_DIR (
    for /d %%D in ("%USERPROFILE%\AppData\Roaming\MetaQuotes\Terminal\*") do (
        if not defined ACTIVE_DIR (
            if exist "%%D\config\common.ini" set "ACTIVE_DIR=%%D"
        )
    )
)
if not defined ACTIVE_DIR (
    echo [!] No Terminal found.
    pause
    exit /b 1
)
echo     Active: %ACTIVE_DIR%
set "EXPERTS_DIR=%ACTIVE_DIR%\MQL5\Experts"

echo [2/3] Copying .ex5...
if not exist "%EXPERTS_DIR%" mkdir "%EXPERTS_DIR%"
copy /Y "%SRC_EX5%" "%EXPERTS_DIR%\FileBridgeEA_Windows.ex5"
if %errorlevel% neq 0 (
    echo [!] Copy failed. Run as Administrator.
    pause
    exit /b 1
)

echo [3/3] Creating junction at GLOBAL Common\Files...
if not exist "%GLOBAL_COMMON%" mkdir "%GLOBAL_COMMON%"
rmdir "%GLOBAL_COMMON%\trading-os" 2>nul
mklink /J "%GLOBAL_COMMON%\trading-os" "%IPC_WIN%"
if %errorlevel% neq 0 (
    echo [!] Junction failed. Run as Administrator.
    pause
    exit /b 1
)

echo.
echo DONE. Restart MT5, attach EA, enable algo trading.
pause
