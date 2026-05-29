@echo off
REM Trading OS — Compile EA on Windows MT5 (dynamic paths via script location)
setlocal

for %%I in ("%~dp0..") do set "REPO_ROOT=%%~fI"
set "MT5=%ProgramFiles%\MetaTrader 5\MetaEditor64.exe"
set "EA=%REPO_ROOT%\bridge\FileBridgeEA_Windows.mq5"
set "LOG=%REPO_ROOT%\bridge\compile.log"
set "EX5=%REPO_ROOT%\bridge\FileBridgeEA_Windows.ex5"

if not exist "%MT5%" (
    echo MetaEditor64.exe not found. Install MetaTrader 5 first.
    pause
    exit /b 1
)

echo [1/3] Compiling %EA% ...
"%MT5%" /compile:"%EA%" /log:"%LOG%"

powershell -NoProfile -Command "$text = Get-Content -LiteralPath '%LOG%' -Encoding Unicode -Raw; if ($text -match '0 error') { exit 0 } else { exit 1 }"
if %errorlevel% neq 0 (
    echo Compilation FAILED. See %LOG%
    type "%LOG%"
    pause
    exit /b 1
)

if not exist "%EX5%" (
    echo Compilation reported success but %EX5% was not created.
    pause
    exit /b 1
)

echo [2/3] Compilation SUCCESS

for /f "delims=" %%a in ('powershell -NoProfile -Command "(Get-ChildItem -Path $env:APPDATA\MetaQuotes\Terminal -Directory | Sort-Object { (Get-ChildItem $_.FullName\logs\*.log -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1).LastWriteTime } -Descending | Select-Object -First 1 -ExpandProperty FullName)"') do set "TERM_DIR=%%a"

if not defined TERM_DIR (
    echo Could not detect MT5 terminal. Copy manually:
    echo   %EX5%
    echo to %%USERPROFILE%%\AppData\Roaming\MetaQuotes\Terminal\[UUID]\MQL5\Experts\
    pause
    exit /b 0
)

set "EXPERTS_DIR=%TERM_DIR%\MQL5\Experts"
if not exist "%EXPERTS_DIR%" mkdir "%EXPERTS_DIR%"
copy /Y "%EX5%" "%EXPERTS_DIR%\FileBridgeEA_Windows.ex5"

echo [3/3] Copied to %EXPERTS_DIR%
echo.
echo NEXT: Restart MT5, attach FileBridgeEA_Windows, InpIpcDir=trading-os/, enable Algo Trading.
pause
