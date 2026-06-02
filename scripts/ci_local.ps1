# Local CI parity with .github/workflows/ci.yml
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location -LiteralPath $Root

Write-Host "Secrets scan..."
$tracked = git ls-files
if ($tracked -match '(?m)^config/secrets\.yaml$') {
    throw "config/secrets.yaml is tracked in git"
}
if ($tracked -match '(?m)(^|/)\.env(\..+)?$') {
    throw ".env file is tracked in git"
}
if ($tracked -match '(?m)\.(pem|key)$') {
    throw "Private key material is tracked in git"
}

Write-Host "Installing dependencies..."
python -m pip install -r requirements.txt
python -m pip install -r requirements-dev.txt

Write-Host "Running ruff..."
python -m ruff check .

Write-Host "Compiling Python sources..."
$compileDirs = @(
    "kernel",
    "cortex",
    "nervous",
    "muscle",
    "consciousness",
    "ops",
    "research",
    "rd",
    "sensory",
    "immune",
    "swarm",
    "introspect",
    "memory",
    "telemetry",
    "scripts",
    "tests"
)
foreach ($dir in $compileDirs) {
    if (Test-Path -LiteralPath $dir) {
        python -m compileall -q $dir
    }
}
foreach ($rootPy in @("data_lake.py", "trading_profile.py", "runtime_safety.py", "runtime_controls.py", "paths.py")) {
    if (Test-Path -LiteralPath $rootPy) {
        python -m py_compile $rootPy
    }
}

Write-Host "Running pytest..."
python -m pytest tests/ -q

Write-Host "ci_local.ps1 finished successfully."
