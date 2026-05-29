# Admin-only repair for Trading OS installed under Program Files.
# Fixes directory permissions and reports readiness. Does not remove API keys.
# Pass -SetupBridge to re-run MT5 bridge setup via install_config.py.
param(
    [string]$InstallRoot = "",
    [switch]$SetupBridge
)

$ErrorActionPreference = "Stop"

function Test-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($id)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Find-Python {
    param([string]$Root)
    $bundled = Join-Path $Root "runtime\python\python.exe"
    if (Test-Path -LiteralPath $bundled) { return $bundled }
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    return $null
}

function Read-InstallSettings {
    param([string]$Root)
    $mode = "SIMULATION"
    $observeOnly = $false
    $candidates = @(
        (Join-Path $env:ProgramData "TradingOS\config.env"),
        (Join-Path $Root "config.env")
    )
    foreach ($path in $candidates) {
        if (-not (Test-Path -LiteralPath $path)) { continue }
        Get-Content -LiteralPath $path | ForEach-Object {
            if ($_ -match '^\s*TRADING_OS_MODE=(.+)$') {
                $mode = $Matches[1].Trim().ToUpper()
            }
            if ($_ -match '^\s*TRADING_OS_LLM_DISABLED=1\s*$') {
                $observeOnly = $true
            }
        }
        break
    }
    return @{ Mode = $mode; ObserveOnly = $observeOnly }
}

if (-not (Test-Admin)) {
    throw "repair_install.ps1 must be run as Administrator."
}

if ($InstallRoot) {
    $InstallRoot = (Resolve-Path -LiteralPath $InstallRoot).Path
} else {
    $InstallRoot = "C:\Program Files\TradingOS"
}

if (-not (Test-Path -LiteralPath $InstallRoot)) {
    throw "Install root not found: $InstallRoot"
}

Write-Host "Repairing Trading OS at $InstallRoot ..."

icacls $InstallRoot /grant "Users:(OI)(CI)M" /T | Out-Null

$py = Find-Python -Root $InstallRoot
if (-not $py) { throw "No Python available for repair." }

$env:PYTHONHOME = $null
Remove-Item Env:PYTHONHOME -ErrorAction SilentlyContinue

$installerDir = Join-Path $InstallRoot "installer"
$configScript = Join-Path $installerDir "install_config.py"
$readinessScript = Join-Path $installerDir "readiness_check.py"

if ($SetupBridge) {
    if (-not (Test-Path -LiteralPath $configScript)) {
        throw "install_config.py not found under $installerDir"
    }
    $settings = Read-InstallSettings -Root $InstallRoot
    $configArgs = @(
        $configScript,
        "--install-root", $InstallRoot,
        "--mode", $settings.Mode,
        "--json"
    )
    if ($settings.ObserveOnly) { $configArgs += "--observe-only" }
    $configArgs += "--setup-bridge"

    Write-Host "Re-running bridge setup (mode=$($settings.Mode)) ..."
    & $py @configArgs
    if ($LASTEXITCODE -ne 0) { throw "install_config failed with exit $LASTEXITCODE" }
    Write-Host "Bridge setup complete."
} else {
    if (-not (Test-Path -LiteralPath $readinessScript)) {
        throw "readiness_check.py not found under $installerDir"
    }
    Write-Host "Running readiness check ..."
    & $py $readinessScript --install-root $InstallRoot
    $checkExit = $LASTEXITCODE
    if ($checkExit -ne 0) {
        Write-Host "Readiness check reported issues (exit $checkExit). Review output above."
        exit $checkExit
    }
    Write-Host "Readiness check passed."
}

Write-Host "Repair complete."
