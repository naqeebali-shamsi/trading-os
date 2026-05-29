# Compile FileBridgeEA_Windows.mq5 to .ex5 for release packaging.
param(
    [string]$RepoRoot = "",
    [switch]$Required
)

$ErrorActionPreference = "Stop"
if (-not $RepoRoot) {
    $RepoRoot = Split-Path -Parent $PSScriptRoot
}
$RepoRoot = (Resolve-Path -LiteralPath $RepoRoot).Path

$eaMq5 = Join-Path $RepoRoot "bridge\FileBridgeEA_Windows.mq5"
$eaEx5 = Join-Path $RepoRoot "bridge\FileBridgeEA_Windows.ex5"
$logPath = Join-Path $RepoRoot "bridge\compile.log"

if (-not (Test-Path -LiteralPath $eaMq5)) {
    throw "EA source not found: $eaMq5"
}

if (Test-Path -LiteralPath $eaEx5) {
    $ex5Time = (Get-Item -LiteralPath $eaEx5).LastWriteTimeUtc
    $mq5Time = (Get-Item -LiteralPath $eaMq5).LastWriteTimeUtc
    if ($ex5Time -ge $mq5Time) {
        Write-Host "  -> bridge\FileBridgeEA_Windows.ex5 (up to date)"
        exit 0
    }
    Write-Host "  -> .mq5 newer than .ex5 - recompiling..."
}

$editorCandidates = @(
    "${env:ProgramFiles}\MetaTrader 5\MetaEditor64.exe",
    "${env:ProgramFiles(x86)}\MetaTrader 5\MetaEditor64.exe"
) | Where-Object { Test-Path $_ }

$editor = $editorCandidates | Select-Object -First 1
if (-not $editor) {
    $msg = "MetaEditor64.exe not found. Install MetaTrader 5 or place a prebuilt bridge\FileBridgeEA_Windows.ex5 in the repo."
    if ($Required -and -not (Test-Path -LiteralPath $eaEx5)) {
        throw $msg
    }
    if (Test-Path -LiteralPath $eaEx5) {
        Write-Warning "$msg Using existing .ex5."
        exit 0
    }
    Write-Warning $msg
    exit 1
}

Write-Host "  Compiling with $editor"
& $editor "/compile:$eaMq5" "/log:$logPath"
if ($LASTEXITCODE -ne 0) {
    Write-Warning "MetaEditor exit code $LASTEXITCODE (log may still show success)"
}

if (-not (Test-Path -LiteralPath $logPath)) {
    if ($Required -and -not (Test-Path -LiteralPath $eaEx5)) {
        throw "Compile log missing and no .ex5 produced."
    }
    exit $(if (Test-Path -LiteralPath $eaEx5) { 0 } else { 1 })
}

$logText = Get-Content -LiteralPath $logPath -Encoding Unicode -Raw
if ($logText -notmatch "0 error") {
    if ($Required -and -not (Test-Path -LiteralPath $eaEx5)) {
        throw "EA compilation failed. See bridge\compile.log"
    }
    if (Test-Path -LiteralPath $eaEx5) {
        Write-Warning "Compile log reports errors but .ex5 exists - continuing."
        exit 0
    }
    throw "EA compilation failed. See bridge\compile.log"
}

if (-not (Test-Path -LiteralPath $eaEx5)) {
    throw "Compilation reported success but $eaEx5 was not created."
}

Write-Host "  -> $eaEx5"
exit 0
