param(
    [switch]$Once,
    [switch]$Watch,
    [switch]$Json,
    [switch]$NoWatchlist,
    [switch]$VerboseWatchlist,
    [double]$Interval = 60,
    [double]$MaxHeartbeatAge = 30
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root = Split-Path -Parent $ScriptDir

function Invoke-WslScanner {
    $wsl = Get-Command wsl -ErrorAction SilentlyContinue
    if (-not $wsl) {
        Write-Error "Repo path is not visible to native Windows PowerShell and wsl.exe was not found: $Root"
    }
    $linuxRoot = $null
    if ($Root -match '^([A-Za-z]):\\(.*)$') {
        $drive = $Matches[1].ToLowerInvariant()
        $rest = $Matches[2] -replace '\\', '/'
        $linuxRoot = "/mnt/$drive/$rest"
    }
    if (-not $linuxRoot) {
        $linuxRoot = & $wsl.Source wslpath -a -u "$Root" 2>$null
    }
    if (-not $linuxRoot) {
        Write-Error "Could not translate repo path for WSL: $Root"
    }
    $args = @("--once")
    if ($Watch) { $args = @("--watch") }
    if ($Json) { $args += "--json" }
    if ($NoWatchlist) { $args += "--no-watchlist" }
    if ($VerboseWatchlist) { $args += "--verbose-watchlist" }
    $args += @("--interval", [string]$Interval, "--max-heartbeat-age", [string]$MaxHeartbeatAge)
    $cmd = "cd '$linuxRoot' && PYTHONUNBUFFERED=1 python3 scripts/opportunity_scanner.py $($args -join ' ')"
    Write-Host "Trading OS Opportunity Scanner via WSL" -ForegroundColor Cyan
    Write-Host "Root: $linuxRoot"
    & $wsl.Source bash -lc $cmd
    exit $LASTEXITCODE
}

if (-not (Test-Path $Root)) {
    Invoke-WslScanner
}

Set-Location $Root

$python = Get-Command py -ErrorAction SilentlyContinue
if ($python) {
    $pyArgs = @("-3", "-u", "scripts/opportunity_scanner.py")
} else {
    $python = Get-Command python -ErrorAction SilentlyContinue
    if (-not $python) {
        Write-Error "Python was not found. Install Python or use WSL: wsl -d Ubuntu -- bash -lc 'cd /mnt/e/GROWTH/trading-os && python3 scripts/opportunity_scanner.py --once'"
    }
    $pyArgs = @("-u", "scripts/opportunity_scanner.py")
}

if ($Watch) { $pyArgs += "--watch" } else { $pyArgs += "--once" }
if ($Json) { $pyArgs += "--json" }
if ($NoWatchlist) { $pyArgs += "--no-watchlist" }
if ($VerboseWatchlist) { $pyArgs += "--verbose-watchlist" }
$pyArgs += @("--interval", [string]$Interval, "--max-heartbeat-age", [string]$MaxHeartbeatAge)

Write-Host "Trading OS Opportunity Scanner" -ForegroundColor Cyan
Write-Host "Root: $Root"
Write-Host "Command: $($python.Source) $($pyArgs -join ' ')"
& $python.Source @pyArgs
exit $LASTEXITCODE
