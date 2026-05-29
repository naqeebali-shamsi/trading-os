# Download and stage a private Python 3.12 runtime for Trading OS installer packaging.
param(
    [string]$StagingRoot = "",
    [string]$PythonVersion = "3.12.10",
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$InstallerDir = $PSScriptRoot
if (-not $StagingRoot) {
    $StagingRoot = [System.IO.Path]::Combine($InstallerDir, "staging", "runtime", "python")
}

[void][System.IO.Directory]::CreateDirectory([System.IO.Path]::GetDirectoryName($StagingRoot))

$pythonExe = [System.IO.Path]::Combine($StagingRoot, "python.exe")
if ((Test-Path -LiteralPath $pythonExe) -and -not $Force) {
    Write-Host "  -> runtime already staged at $StagingRoot"
    exit 0
}

$buildRoot = "C:\TradingOS-Python312"
$buildPython = [System.IO.Path]::Combine($buildRoot, "python.exe")
$local312 = [System.IO.Path]::Combine($env:LOCALAPPDATA, "Programs", "Python", "Python312")

if (-not (Test-Path -LiteralPath $buildPython)) {
    if (Test-Path -LiteralPath ([System.IO.Path]::Combine($local312, "python.exe"))) {
        Write-Host "  Seeding build cache from $local312 ..."
        if (Test-Path -LiteralPath $buildRoot) { Remove-Item -LiteralPath $buildRoot -Recurse -Force }
        & robocopy $local312 $buildRoot /E /NFL /NDL /NJH /NJS /NC /NS | Out-Null
    } else {
        $cacheDir = [System.IO.Path]::Combine($InstallerDir, "cache")
        [void][System.IO.Directory]::CreateDirectory($cacheDir)
        $installerName = "python-$PythonVersion-amd64.exe"
        $installerUrl = "https://www.python.org/ftp/python/$PythonVersion/$installerName"
        $installerPath = [System.IO.Path]::Combine($cacheDir, $installerName)
        if (-not (Test-Path -LiteralPath $installerPath)) {
            Write-Host "  Downloading $installerUrl ..."
            (New-Object System.Net.WebClient).DownloadFile($installerUrl, $installerPath)
        }
        if (Test-Path -LiteralPath $buildRoot) { Remove-Item -LiteralPath $buildRoot -Recurse -Force }
        [void][System.IO.Directory]::CreateDirectory($buildRoot)
        Write-Host "  Installing Python $PythonVersion to $buildRoot (may prompt for UAC)..."
        $argString = "/passive InstallAllUsers=1 PrependPath=0 Include_test=0 Include_pip=1 TargetDir=`"$buildRoot`""
        $pinfo = New-Object System.Diagnostics.ProcessStartInfo
        $pinfo.FileName = $installerPath
        $pinfo.Arguments = $argString
        $pinfo.UseShellExecute = $true
        $proc = [System.Diagnostics.Process]::Start($pinfo)
        $proc.WaitForExit()
    }
}

if (-not (Test-Path -LiteralPath $buildPython)) {
    throw "Bundled Python cache missing at $buildPython. Install Python 3.12 locally or run as Administrator."
}

if (Test-Path -LiteralPath $StagingRoot) {
    Remove-Item -LiteralPath $StagingRoot -Recurse -Force
}
[void][System.IO.Directory]::CreateDirectory($StagingRoot)

Write-Host "  Copying runtime into installer staging..."
& robocopy $buildRoot $StagingRoot /E /NFL /NDL /NJH /NJS /NC /NS | Out-Null
if ($LASTEXITCODE -ge 8) {
    throw "robocopy failed copying Python runtime (exit $LASTEXITCODE)"
}

Write-Host "  Stripping runtime bloat (pyc, docs, tests, dev site-packages)..."
$removeDirs = @(
    (Join-Path $StagingRoot "Doc"),
    (Join-Path $StagingRoot "tcl"),
    (Join-Path $StagingRoot "Lib\test"),
    (Join-Path $StagingRoot "Lib\idlelib")
)
foreach ($dir in $removeDirs) {
    if (Test-Path -LiteralPath $dir) { Remove-Item -LiteralPath $dir -Recurse -Force }
}
Get-ChildItem -LiteralPath $StagingRoot -Recurse -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue |
    ForEach-Object { Remove-Item -LiteralPath $_.FullName -Recurse -Force -ErrorAction SilentlyContinue }
foreach ($pattern in @("*.pyc", "*.pyo")) {
    Get-ChildItem -LiteralPath $StagingRoot -Recurse -File -Filter $pattern -ErrorAction SilentlyContinue |
        ForEach-Object { Remove-Item -LiteralPath $_.FullName -Force -ErrorAction SilentlyContinue }
}

$sitePackages = Join-Path $StagingRoot "Lib\site-packages"
if (Test-Path -LiteralPath $sitePackages) {
    Get-ChildItem -LiteralPath $sitePackages -ErrorAction SilentlyContinue | ForEach-Object {
        $name = $_.Name
        $keep = $name -match '^(pip|setuptools|wheel|_distutils_hack)([-.]|$)'
        if (-not $keep) {
            Remove-Item -LiteralPath $_.FullName -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
    Get-ChildItem -LiteralPath $sitePackages -Filter "*.pth" -File -ErrorAction SilentlyContinue |
        ForEach-Object { Remove-Item -LiteralPath $_.FullName -Force -ErrorAction SilentlyContinue }
}

if (-not (Test-Path -LiteralPath $pythonExe)) {
    throw "python.exe not found after copy: $pythonExe"
}

$encodings = Join-Path $StagingRoot "Lib\encodings\__init__.py"
if (-not (Test-Path -LiteralPath $encodings)) {
    throw "Invalid Python runtime staging: missing Lib\encodings in $StagingRoot"
}

Write-Host "  Verifying bundled Python runtime..."
$prevEap = $ErrorActionPreference
$ErrorActionPreference = "Continue"
try {
    $verifyOutput = & $pythonExe -c "import encodings; import venv; print('ok')" 2>&1 | Out-String
    $verifyExit = $LASTEXITCODE
} finally {
    $ErrorActionPreference = $prevEap
}
if ($verifyExit -ne 0) {
    throw "Bundled Python verification failed: $verifyOutput"
}

Write-Host "  -> $pythonExe"
exit 0
