# scripts/emergency_stop.ps1 — Circuit breaker halt (Windows / PowerShell)
# Usage: .\scripts\emergency_stop.ps1 [reason]
param(
    [string]$Reason = "manual_emergency"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Python = if ($env:TRADING_OS_PYTHON) { $env:TRADING_OS_PYTHON } else { "python" }

& $Python "$Root\scripts\emergency_stop.py" $Reason
exit $LASTEXITCODE
