# Build Trading OS release artifacts (launcher exe + optional installer)
param(
    [switch]$SkipInstaller,
    [switch]$SkipPyInstaller,
    [switch]$SkipEACompile
)

$ErrorActionPreference = "Stop"
$InstallerDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $InstallerDir
Set-Location -LiteralPath $RepoRoot

$issPath = Join-Path $InstallerDir "TradingOSSetup.iss"
if (-not (Test-Path -LiteralPath $issPath)) {
    throw "TradingOSSetup.iss not found: $issPath"
}
$issText = Get-Content -LiteralPath $issPath -Raw
if ($issText -notmatch '(?m)^#define MyAppVersion "([^"]+)"') {
    throw "Could not read #define MyAppVersion from $issPath"
}
$appVersion = $Matches[1]

Write-Host "== Trading OS release build (v$appVersion) ==" -ForegroundColor Cyan
Write-Host "Repo: $RepoRoot"

$py = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $py) { throw "Python not found on PATH." }

if (-not $SkipEACompile) {
    Write-Host "`n[1/6] Compiling MT5 bridge EA..." -ForegroundColor Yellow
    & (Join-Path $RepoRoot "bridge\compile_bridge.ps1") -RepoRoot $RepoRoot -Required
    if ($LASTEXITCODE -ne 0) { throw "EA compile step failed" }
    $ex5 = Join-Path $RepoRoot "bridge\FileBridgeEA_Windows.ex5"
    if (-not (Test-Path -LiteralPath $ex5)) {
        throw "bridge\FileBridgeEA_Windows.ex5 missing - required for installer packaging."
    }
} else {
    Write-Host "`n[1/6] Skipped EA compile ( -SkipEACompile )" -ForegroundColor DarkGray
}

Write-Host "`n[2/6] Staging bundled Python runtime..." -ForegroundColor Yellow
& (Join-Path $InstallerDir "fetch_python_runtime.ps1") -Force
if ($LASTEXITCODE -ne 0) { throw "Python runtime staging failed" }

Write-Host "`n[3/6] Downloading offline wheelhouse..." -ForegroundColor Yellow
$stagedPython = Join-Path $InstallerDir "staging\runtime\python\python.exe"
& (Join-Path $InstallerDir "download_wheelhouse.ps1") -RepoRoot $RepoRoot -PythonExe $stagedPython
if ($LASTEXITCODE -ne 0) { throw "Wheelhouse download failed" }

if (-not $SkipPyInstaller) {
    Write-Host "`n[4/6] Building TradingOS.exe launcher..." -ForegroundColor Yellow
    & $py -m pip install --quiet pyinstaller
    Set-Location -LiteralPath $InstallerDir
    & $py -m PyInstaller --noconfirm --clean launcher.spec
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed for launcher.spec" }

    & $py -m PyInstaller --noconfirm --onefile --name TradingOS-Stop --console stop.py
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed for stop.py" }

    Copy-Item -Force "$InstallerDir\dist\TradingOS.exe" "$RepoRoot\TradingOS.exe"
    Copy-Item -Force "$InstallerDir\dist\TradingOS-Stop.exe" "$RepoRoot\TradingOS-Stop.exe"
    Set-Location -LiteralPath $RepoRoot
    Write-Host "  -> $RepoRoot\TradingOS.exe"
} else {
    Write-Host "`n[4/6] Skipped PyInstaller ( -SkipPyInstaller )" -ForegroundColor DarkGray
}

Write-Host "`n[5/6] Verifying launcher + R&D modules..." -ForegroundColor Yellow
& $py -m py_compile "$RepoRoot\installer\launcher.py" "$RepoRoot\installer\install_config.py" "$RepoRoot\installer\stop.py" "$RepoRoot\installer\secrets_store.py" "$RepoRoot\installer\readiness_check.py" "$RepoRoot\bridge\setup_bridge.py"
if ($LASTEXITCODE -ne 0) { throw "Python compile check failed" }

$compileDirs = @(
    "kernel", "cortex", "nervous", "muscle", "consciousness", "ops",
    "research", "rd", "sensory", "immune", "swarm", "introspect",
    "memory", "telemetry", "scripts"
)
foreach ($dir in $compileDirs) {
    $full = Join-Path $RepoRoot $dir
    if (Test-Path -LiteralPath $full) {
        & $py -m compileall -q $full
        if ($LASTEXITCODE -ne 0) { throw "compileall failed: $dir" }
    }
}
foreach ($rootPy in @("data_lake.py", "trading_profile.py", "runtime_safety.py", "runtime_controls.py", "paths.py")) {
    $f = Join-Path $RepoRoot $rootPy
    if (Test-Path -LiteralPath $f) {
        & $py -m py_compile $f
        if ($LASTEXITCODE -ne 0) { throw "compile failed: $rootPy" }
    }
}

Write-Host "  Running strategy search + Dream Lab smoke tests..."
Push-Location -LiteralPath $RepoRoot
& $py -m pytest "tests/test_strategy_search.py" "tests/test_dream_lab_agents.py" -q --rootdir="$RepoRoot"
$pytestExit = $LASTEXITCODE
Pop-Location
if ($pytestExit -ne 0) { throw "R&D module tests failed" }

if (-not $SkipInstaller) {
    $ex5 = Join-Path $RepoRoot "bridge\FileBridgeEA_Windows.ex5"
    if (-not (Test-Path -LiteralPath $ex5)) {
        throw "bridge\FileBridgeEA_Windows.ex5 missing - run without -SkipEACompile on a machine with MetaTrader 5."
    }
    Write-Host "`n[6/6] Building TradingOS-Setup.exe (Inno Setup)..." -ForegroundColor Yellow
    $iscc = @(
        "${env:ProgramFiles}\Inno Setup 7\ISCC.exe",
        "${env:ProgramFiles(x86)}\Inno Setup 7\ISCC.exe",
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
    ) | Where-Object { Test-Path $_ } | Select-Object -First 1
    if (-not $iscc) {
        Write-Warning "Inno Setup not found. Install from https://jrsoftware.org/isinfo.php"
        Write-Warning "Launcher exe is ready; run ISCC manually: iscc installer\TradingOSSetup.iss"
    } else {
        & $iscc "$InstallerDir\TradingOSSetup.iss"
        if ($LASTEXITCODE -ne 0) { throw "Inno Setup compile failed" }
        Write-Host "  -> $InstallerDir\output\TradingOS-Setup-$appVersion.exe"
    }
} else {
    Write-Host "`n[6/6] Skipped installer ( -SkipInstaller )" -ForegroundColor DarkGray
}

Write-Host "`nDone. For dev: run .\installer\install_wizard.ps1 or double-click TradingOS.exe" -ForegroundColor Green
