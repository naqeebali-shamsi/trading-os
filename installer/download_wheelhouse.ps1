# Download wheels for offline pip install on Windows amd64 (cp312).
param(
    [string]$RepoRoot = "",
    [string]$WheelhouseDir = "",
    [string]$PythonExe = ""
)

$ErrorActionPreference = "Stop"
$InstallerDir = $PSScriptRoot
if (-not $RepoRoot) {
    $RepoRoot = Split-Path -Parent $InstallerDir
}
$RepoRoot = (Resolve-Path -LiteralPath $RepoRoot).Path

if (-not $WheelhouseDir) {
    $WheelhouseDir = [System.IO.Path]::Combine($InstallerDir, "wheelhouse")
}
[void][System.IO.Directory]::CreateDirectory($WheelhouseDir)

if (-not $PythonExe) {
    $PythonExe = [System.IO.Path]::Combine($InstallerDir, "staging", "runtime", "python", "python.exe")
}
if (-not (Test-Path -LiteralPath $PythonExe)) {
    $PythonExe = (Get-Command python -ErrorAction SilentlyContinue).Source
}
if (-not $PythonExe) {
    throw "Python not found for wheelhouse download."
}

if (Test-Path -LiteralPath $WheelhouseDir) {
    Get-ChildItem -LiteralPath $WheelhouseDir -Filter "*cp311*" -File -ErrorAction SilentlyContinue |
        Remove-Item -Force -ErrorAction SilentlyContinue
}

$req = [System.IO.Path]::Combine($RepoRoot, "requirements.txt")
Write-Host "  Downloading cp312 wheels to $WheelhouseDir ..."
& $PythonExe -m pip download -r $req -d $WheelhouseDir --only-binary=:all: --platform win_amd64 --python-version 312 --implementation cp
if ($LASTEXITCODE -ne 0) {
    Write-Warning "Platform-specific download failed; falling back to local download."
    & $PythonExe -m pip download -r $req -d $WheelhouseDir
    if ($LASTEXITCODE -ne 0) { throw "pip download failed" }
}

$count = (Get-ChildItem -LiteralPath $WheelhouseDir -Filter "*.whl").Count
Write-Host "  -> $count wheel(s) in wheelhouse"
exit 0
