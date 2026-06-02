# Trading OS - multi-step install wizard (Windows, ASCII-only for PowerShell 5.1)
param(
    [string]$InstallRoot = "",
    [switch]$Silent,
    [switch]$Mandatory,
    [switch]$ObserveOnly,
    [string]$Mode = "SIMULATION",
    [string]$OpenRouterKey = "",
    [switch]$SetupBridge
)

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $ScriptDir
$script:InstallSucceeded = $false
$script:CurrentStep = 0
$script:StepCount = 5

function Write-WizardLog {
    param([string]$Message)
    $logRoot = if ($InstallRoot) { $InstallRoot } else { $RepoRoot }
    $logDir = Join-Path $logRoot "logs"
    try {
        New-Item -ItemType Directory -Force -Path $logDir | Out-Null
        $line = "{0}  {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
        Add-Content -LiteralPath (Join-Path $logDir "install_wizard.log") -Value $line -Encoding UTF8
    } catch { }
}

Write-WizardLog "Wizard started (InstallRoot=$InstallRoot Mandatory=$Mandatory)"

function Find-Python {
    param([string]$Root = "")
    if ($Root) {
        $bundled = Join-Path $Root "runtime\python\python.exe"
        if (Test-Path -LiteralPath $bundled) { return $bundled }
    }
    $candidates = @(
        (Get-Command python -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source),
        "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe"
    ) | Where-Object { $_ -and (Test-Path -LiteralPath $_) }
    return $candidates | Select-Object -First 1
}

function Test-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $p = New-Object Security.Principal.WindowsPrincipal($id)
    return $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Invoke-InstallConfig {
    param(
        [string]$Root,
        [string]$TradingMode,
        [string]$Key,
        [bool]$ObserveOnlyFlag,
        [bool]$Bridge,
        [string]$ProgressFile,
        [scriptblock]$OnProgress
    )
    $py = Find-Python -Root $Root
    if (-not $py) { throw "Bundled Python not found under $Root\runtime\python." }

    $configScript = Join-Path $ScriptDir "install_config.py"
    if (-not (Test-Path -LiteralPath $configScript)) {
        $configScript = Join-Path $RepoRoot "installer\install_config.py"
    }

    $resultFile = [IO.Path]::GetTempFileName()
    $keyFile = [IO.Path]::GetTempFileName()
    try {
        if ($Key) {
            $utf8NoBom = New-Object System.Text.UTF8Encoding $false
            [System.IO.File]::WriteAllText($keyFile, $Key, $utf8NoBom)
        }
        $configArgs = @(
            $configScript,
            "--install-root", $Root,
            "--mode", $TradingMode,
            "--llm-decision-mode", "ADVISORY",
            "--key-file", $keyFile,
            "--result-file", $resultFile,
            "--progress-file", $ProgressFile
        )
        if ($ObserveOnlyFlag) { $configArgs += "--observe-only" }
        if ($Bridge) { $configArgs += "--setup-bridge" }

        $savedHome = $env:PYTHONHOME
        $env:PYTHONHOME = $null
        $prevEap = $ErrorActionPreference
        $ErrorActionPreference = "Continue"

        $psi = New-Object System.Diagnostics.ProcessStartInfo
        $psi.FileName = $py
        $psi.Arguments = ($configArgs | ForEach-Object {
            if ($_ -match '\s') { '"' + ($_ -replace '"', '""') + '"' } else { $_ }
        }) -join ' '
        $psi.WorkingDirectory = $Root
        $psi.UseShellExecute = $false
        $psi.RedirectStandardOutput = $true
        $psi.RedirectStandardError = $true
        $psi.CreateNoWindow = $true
        $proc = [System.Diagnostics.Process]::Start($psi)

        while (-not $proc.HasExited) {
            if ($OnProgress -and (Test-Path -LiteralPath $ProgressFile)) {
                try {
                    $state = Get-Content -LiteralPath $ProgressFile -Raw | ConvertFrom-Json
                    & $OnProgress $state.step $state.detail
                } catch { }
            }
            [System.Windows.Forms.Application]::DoEvents()
            Start-Sleep -Milliseconds 150
        }
        $installOutput = $proc.StandardOutput.ReadToEnd() + $proc.StandardError.ReadToEnd()
        $installExit = $proc.ExitCode
        $ErrorActionPreference = $prevEap
        if ($savedHome) { $env:PYTHONHOME = $savedHome } else { Remove-Item Env:PYTHONHOME -ErrorAction SilentlyContinue }

        if (-not (Test-Path -LiteralPath $resultFile)) {
            $detail = ($installOutput -replace '\s+', ' ').Trim()
            if ($detail.Length -gt 400) { $detail = $detail.Substring(0, 400) + "..." }
            if ($detail) { throw "Setup failed (exit $installExit): $detail" }
            throw "Setup produced no result (exit $installExit)."
        }
        $payload = Get-Content -LiteralPath $resultFile -Raw | ConvertFrom-Json
        if ($installExit -ne 0 -or -not $payload.install_ok) {
            $msg = $payload.error
            if (-not $msg) { $msg = $payload.traceback }
            if (-not $msg) { $msg = "Install configuration failed." }
            throw $msg.Trim()
        }
        return $payload
    } finally {
        if (Test-Path -LiteralPath $keyFile) { Remove-Item -LiteralPath $keyFile -Force }
        if (Test-Path -LiteralPath $resultFile) { Remove-Item -LiteralPath $resultFile -Force }
        if ($ProgressFile -and (Test-Path -LiteralPath $ProgressFile)) { Remove-Item -LiteralPath $ProgressFile -Force }
    }
}

function Invoke-ReadinessCheck {
    param(
        [string]$Root,
        [switch]$Wizard
    )
    $py = Find-Python -Root $Root
    if (-not $py) { return $null }

    $checkScript = Join-Path $ScriptDir "readiness_check.py"
    if (-not (Test-Path -LiteralPath $checkScript)) {
        $checkScript = Join-Path $RepoRoot "installer\readiness_check.py"
    }
    if (-not (Test-Path -LiteralPath $checkScript)) { return $null }

    $checkArgs = @($checkScript, "--install-root", $Root)
    if ($Wizard) { $checkArgs += "--wizard" }

    $savedHome = $env:PYTHONHOME
    $env:PYTHONHOME = $null
    try {
        $output = & $py @checkArgs 2>&1 | Out-String
        $output = $output.Trim()
        if (-not $output) { return $null }
        return ($output | ConvertFrom-Json)
    } catch {
        Write-WizardLog "Readiness check failed: $($_.Exception.Message)"
        return $null
    } finally {
        if ($savedHome) { $env:PYTHONHOME = $savedHome } else { Remove-Item Env:PYTHONHOME -ErrorAction SilentlyContinue }
    }
}

function New-DesktopShortcut {
    param([string]$TargetPath, [string]$ShortcutName)
    $shell = New-Object -ComObject WScript.Shell
    $desktop = [Environment]::GetFolderPath('Desktop')
    $lnk = $shell.CreateShortcut((Join-Path $desktop "$ShortcutName.lnk"))
    $lnk.TargetPath = $TargetPath
    $lnk.WorkingDirectory = Split-Path -Parent $TargetPath
    $lnk.Description = "Start Trading OS"
    $lnk.Save()
}

if ($Silent) {
    if (-not $InstallRoot) { $InstallRoot = "C:\TradingOS" }
    $pf = [IO.Path]::GetTempFileName()
    try {
        $result = Invoke-InstallConfig -Root $InstallRoot -TradingMode $Mode -Key $OpenRouterKey `
            -ObserveOnlyFlag:$ObserveOnly -Bridge:$SetupBridge -ProgressFile $pf -OnProgress $null
        Write-Output ($result | ConvertTo-Json -Depth 5)
        exit 0
    } catch {
        Write-Error $_
        exit 1
    }
}

$resolvedRoot = if ($InstallRoot) { $InstallRoot } else { $RepoRoot }
$alreadyConfigured = (Test-Path -LiteralPath (Join-Path $resolvedRoot ".install-complete"))

$form = New-Object System.Windows.Forms.Form
$form.Text = "Trading OS Setup - QTPi"
$form.Size = New-Object System.Drawing.Size(620, 520)
$form.StartPosition = "CenterScreen"
$form.FormBorderStyle = "FixedDialog"
$form.MaximizeBox = $false
$form.TopMost = $true
$form.Add_Shown({ $form.Activate(); $form.TopMost = $false })

$form.Add_FormClosing({
    param($sender, $e)
    if ($Mandatory -and -not $script:InstallSucceeded -and $script:CurrentStep -lt ($script:StepCount - 1)) {
        $ans = [System.Windows.Forms.MessageBox]::Show(
            "Setup is not finished. Trading OS will not run until configuration completes.`n`nExit anyway?",
            "Trading OS Setup",
            [System.Windows.Forms.MessageBoxButtons]::YesNo,
            [System.Windows.Forms.MessageBoxIcon]::Warning)
        if ($ans -ne [System.Windows.Forms.DialogResult]::Yes) { $e.Cancel = $true }
    }
})

function New-Panel {
    param([int]$Top = 56)
    $p = New-Object System.Windows.Forms.Panel
    $p.Location = New-Object System.Drawing.Point(0, $Top)
    $p.Size = New-Object System.Drawing.Size(620, 380)
    $p.Visible = $false
    return $p
}

$banner = New-Object System.Windows.Forms.Label
$banner.Location = New-Object System.Drawing.Point(16, 12)
$banner.Size = New-Object System.Drawing.Size(580, 36)
$banner.Font = New-Object System.Drawing.Font("Segoe UI", 9, [System.Drawing.FontStyle]::Bold)
if (Test-Admin) {
    $banner.ForeColor = [System.Drawing.Color]::DarkGreen
    $banner.Text = "Administrator: full install including MT5 bridge junction is available."
} else {
    $banner.ForeColor = [System.Drawing.Color]::DarkOrange
    $banner.Text = "Not running as Administrator. Close and use Configure Trading OS (elevates automatically)."
}
$form.Controls.Add($banner)

$lblStep = New-Object System.Windows.Forms.Label
$lblStep.Location = New-Object System.Drawing.Point(16, 44)
$lblStep.Size = New-Object System.Drawing.Size(580, 20)
$lblStep.ForeColor = [System.Drawing.Color]::Gray
$form.Controls.Add($lblStep)

$panelWelcome = New-Panel
$lblW = New-Object System.Windows.Forms.Label
$lblW.Location = New-Object System.Drawing.Point(24, 16)
$lblW.Size = New-Object System.Drawing.Size(560, 200)
$lblW.Text = @"
Welcome to Trading OS by QTPi.

This wizard will:
  1. Save your OpenRouter API key (or enable observe-only mode)
  2. Create the Python runtime (about 1-2 minutes)
  3. Connect MetaTrader 5 via the file bridge (LIVE mode)

Install folder:
  $resolvedRoot

You log in to MT5 inside the MetaTrader app - not here.
"@
$panelWelcome.Controls.Add($lblW)
if ($alreadyConfigured) {
    $lblDone = New-Object System.Windows.Forms.Label
    $lblDone.ForeColor = [System.Drawing.Color]::DarkGreen
    $lblDone.Location = New-Object System.Drawing.Point(24, 220)
    $lblDone.Size = New-Object System.Drawing.Size(560, 40)
    $lblDone.Text = "This copy is already configured. You can re-run setup or launch Trading OS."
    $panelWelcome.Controls.Add($lblDone)
}
$form.Controls.Add($panelWelcome)

$panelBrain = New-Panel
$lblKey = New-Object System.Windows.Forms.Label
$lblKey.Text = "OpenRouter API key (from openrouter.ai)"
$lblKey.Location = New-Object System.Drawing.Point(24, 16)
$lblKey.Size = New-Object System.Drawing.Size(560, 20)
$panelBrain.Controls.Add($lblKey)
$txtKey = New-Object System.Windows.Forms.TextBox
$txtKey.UseSystemPasswordChar = $true
$txtKey.Location = New-Object System.Drawing.Point(24, 40)
$txtKey.Size = New-Object System.Drawing.Size(560, 24)
$panelBrain.Controls.Add($txtKey)
$chkObserve = New-Object System.Windows.Forms.CheckBox
$chkObserve.Text = "Observe-only (SIMULATION without LLM - no API key needed)"
$chkObserve.Location = New-Object System.Drawing.Point(24, 76)
$chkObserve.Size = New-Object System.Drawing.Size(560, 24)
$chkObserve.Add_CheckedChanged({
    $txtKey.Enabled = -not $chkObserve.Checked
})
$panelBrain.Controls.Add($chkObserve)
$lblBrainHelp = New-Object System.Windows.Forms.Label
$lblBrainHelp.Location = New-Object System.Drawing.Point(24, 108)
$lblBrainHelp.Size = New-Object System.Drawing.Size(560, 60)
$lblBrainHelp.Text = "The API key powers the trading brain. It is stored with Windows DPAPI encryption under ProgramData\TradingOS."
$panelBrain.Controls.Add($lblBrainHelp)
$form.Controls.Add($panelBrain)

$panelTrading = New-Panel
$lblMode = New-Object System.Windows.Forms.Label
$lblMode.Text = "Trading mode"
$lblMode.Location = New-Object System.Drawing.Point(24, 16)
$panelTrading.Controls.Add($lblMode)
$cmbMode = New-Object System.Windows.Forms.ComboBox
$cmbMode.Items.AddRange(@("SIMULATION", "LIVE"))
$cmbMode.SelectedIndex = 0
$cmbMode.DropDownStyle = "DropDownList"
$cmbMode.Location = New-Object System.Drawing.Point(24, 40)
$cmbMode.Size = New-Object System.Drawing.Size(220, 24)
$panelTrading.Controls.Add($cmbMode)
$lblModeHelp = New-Object System.Windows.Forms.Label
$lblModeHelp.Location = New-Object System.Drawing.Point(24, 72)
$lblModeHelp.Size = New-Object System.Drawing.Size(560, 40)
$lblModeHelp.Text = "First run: use SIMULATION. Switch to LIVE when MT5 is connected and you are ready to trade."
$panelTrading.Controls.Add($lblModeHelp)
$chkBridge = New-Object System.Windows.Forms.CheckBox
$chkBridge.Text = "Set up MT5 file bridge (copy EA to MetaTrader + IPC junction)"
$chkBridge.Checked = $true
if ($PSBoundParameters.ContainsKey('SetupBridge')) { $chkBridge.Checked = [bool]$SetupBridge }
$chkBridge.Location = New-Object System.Drawing.Point(24, 120)
$chkBridge.Size = New-Object System.Drawing.Size(560, 24)
$panelTrading.Controls.Add($chkBridge)
$lblMt5Help = New-Object System.Windows.Forms.Label
$lblMt5Help.Location = New-Object System.Drawing.Point(24, 152)
$lblMt5Help.Size = New-Object System.Drawing.Size(560, 120)
$lblMt5Help.Text = @"
Required for LIVE quotes and orders:
  - MetaTrader 5 installed and logged in (your broker account)
  - FileBridgeEA_Windows attached to a chart with Algo Trading ON

This wizard copies the EA and links the IPC folder. It does not store your MT5 password.
"@
$panelTrading.Controls.Add($lblMt5Help)
$cmbMode.Add_SelectedIndexChanged({
    if ($cmbMode.SelectedItem -eq "LIVE") { $chkBridge.Checked = $true }
})
$chkObserve.Add_CheckedChanged({
    if ($chkObserve.Checked) {
        if ($cmbMode.SelectedItem -eq "LIVE") { $cmbMode.SelectedIndex = 0 }
        $cmbMode.Enabled = $false
    } else { $cmbMode.Enabled = $true }
})
$form.Controls.Add($panelTrading)

$panelProgress = New-Panel
$lblProgressTitle = New-Object System.Windows.Forms.Label
$lblProgressTitle.Font = New-Object System.Drawing.Font("Segoe UI", 11, [System.Drawing.FontStyle]::Bold)
$lblProgressTitle.Text = "Installing..."
$lblProgressTitle.Location = New-Object System.Drawing.Point(24, 16)
$lblProgressTitle.Size = New-Object System.Drawing.Size(560, 28)
$panelProgress.Controls.Add($lblProgressTitle)
$lblProgressStep = New-Object System.Windows.Forms.Label
$lblProgressStep.Location = New-Object System.Drawing.Point(24, 48)
$lblProgressStep.Size = New-Object System.Drawing.Size(560, 24)
$lblProgressStep.Text = "Preparing..."
$panelProgress.Controls.Add($lblProgressStep)
$progressBar = New-Object System.Windows.Forms.ProgressBar
$progressBar.Style = "Marquee"
$progressBar.MarqueeAnimationSpeed = 30
$progressBar.Location = New-Object System.Drawing.Point(24, 80)
$progressBar.Size = New-Object System.Drawing.Size(560, 22)
$panelProgress.Controls.Add($progressBar)
$txtLog = New-Object System.Windows.Forms.TextBox
$txtLog.Multiline = $true
$txtLog.ReadOnly = $true
$txtLog.ScrollBars = "Vertical"
$txtLog.Location = New-Object System.Drawing.Point(24, 112)
$txtLog.Size = New-Object System.Drawing.Size(560, 200)
$panelProgress.Controls.Add($txtLog)
$form.Controls.Add($panelProgress)

$panelDone = New-Panel
$lblDoneTitle = New-Object System.Windows.Forms.Label
$lblDoneTitle.Font = New-Object System.Drawing.Font("Segoe UI", 12, [System.Drawing.FontStyle]::Bold)
$lblDoneTitle.ForeColor = [System.Drawing.Color]::DarkGreen
$lblDoneTitle.Text = "Trading OS is ready"
$lblDoneTitle.Location = New-Object System.Drawing.Point(24, 16)
$lblDoneTitle.Size = New-Object System.Drawing.Size(560, 28)
$panelDone.Controls.Add($lblDoneTitle)
$lblDoneBody = New-Object System.Windows.Forms.Label
$lblDoneBody.Location = New-Object System.Drawing.Point(24, 52)
$lblDoneBody.Size = New-Object System.Drawing.Size(560, 180)
$script:DoneBodyDefault = @"
Next steps for LIVE trading:
  1. Restart MetaTrader 5
  2. Drag FileBridgeEA_Windows onto a chart
  3. Enable Algo Trading
  4. Launch Trading OS (opens dashboard at http://127.0.0.1:8765/ui)
"@
$lblDoneBody.Text = $script:DoneBodyDefault
$panelDone.Controls.Add($lblDoneBody)
$btnLaunch = New-Object System.Windows.Forms.Button
$btnLaunch.Text = "Launch Trading OS"
$btnLaunch.Location = New-Object System.Drawing.Point(24, 250)
$btnLaunch.Size = New-Object System.Drawing.Size(160, 32)
$btnLaunch.Add_Click({
    $launcher = Join-Path $resolvedRoot "TradingOS.exe"
    if (Test-Path -LiteralPath $launcher) { Start-Process -FilePath $launcher }
})
$panelDone.Controls.Add($btnLaunch)
$form.Controls.Add($panelDone)

$panels = @($panelWelcome, $panelBrain, $panelTrading, $panelProgress, $panelDone)
$stepTitles = @("Welcome", "Brain / API key", "Trading and MT5", "Installing", "Complete")

function Set-Step {
    param([int]$Index)
    $script:CurrentStep = $Index
    for ($i = 0; $i -lt $panels.Count; $i++) {
        $panels[$i].Visible = ($i -eq $Index)
    }
    $lblStep.Text = "Step $($Index + 1) of $($panels.Count): $($stepTitles[$Index])"
    $btnBack.Enabled = ($Index -gt 0 -and $Index -lt 3)
    $btnNext.Visible = ($Index -lt 2)
    $btnNext.Enabled = ($Index -lt 2)
    $btnInstall.Visible = ($Index -eq 2)
    $btnClose.Text = if ($Index -eq 4) { "Close" } else { "Cancel" }
}

$btnBack = New-Object System.Windows.Forms.Button
$btnBack.Text = "Back"
$btnBack.Location = New-Object System.Drawing.Point(320, 440)
$btnBack.Add_Click({ Set-Step ($script:CurrentStep - 1) })
$form.Controls.Add($btnBack)

$btnNext = New-Object System.Windows.Forms.Button
$btnNext.Text = "Next"
$btnNext.Location = New-Object System.Drawing.Point(420, 440)
$btnNext.Add_Click({
    if ($script:CurrentStep -eq 0) {
        Set-Step 1
        return
    }
    if ($script:CurrentStep -eq 1) {
        if (-not $chkObserve.Checked) {
            $k = $txtKey.Text.Trim()
            if (-not $k) {
                [System.Windows.Forms.MessageBox]::Show("Enter your OpenRouter API key or enable observe-only.", "Trading OS Setup", "OK", "Warning")
                return
            }
            if ($k.Length -lt 20) {
                [System.Windows.Forms.MessageBox]::Show("API key looks too short.", "Trading OS Setup", "OK", "Warning")
                return
            }
        }
        Set-Step 2
        return
    }
})
$form.Controls.Add($btnNext)

$btnInstall = New-Object System.Windows.Forms.Button
$btnInstall.Text = "Install"
$btnInstall.Location = New-Object System.Drawing.Point(420, 440)
$btnInstall.Visible = $false
$btnInstall.Add_Click({
    if ($cmbMode.SelectedItem -eq "LIVE" -and -not $chkBridge.Checked) {
        $w = [System.Windows.Forms.MessageBox]::Show("LIVE mode needs the MT5 bridge. Continue without bridge setup?", "Trading OS Setup", "YesNo", "Warning")
        if ($w -ne [System.Windows.Forms.DialogResult]::Yes) { return }
    }
    if ($chkBridge.Checked -and -not (Test-Admin)) {
        $w = [System.Windows.Forms.MessageBox]::Show("MT5 bridge setup requires Administrator.`n`nContinue anyway (bridge step may fail)?", "Trading OS Setup", "YesNo", "Warning")
        if ($w -ne [System.Windows.Forms.DialogResult]::Yes) { return }
    }
    $btnInstall.Enabled = $false
    $btnNext.Enabled = $false
    $btnBack.Enabled = $false
    Set-Step 3
    $progressFile = Join-Path $env:TEMP ("tradingos-progress-{0}.json" -f [Guid]::NewGuid().ToString("N"))
    try {
        $onProgress = {
            param($step, $detail)
            $lblProgressStep.Text = if ($detail) { "$step - $detail" } else { $step }
            [System.Windows.Forms.Application]::DoEvents()
        }
        $result = Invoke-InstallConfig -Root $resolvedRoot -TradingMode $cmbMode.SelectedItem -Key $txtKey.Text.Trim() `
            -ObserveOnlyFlag:$chkObserve.Checked -Bridge:$chkBridge.Checked -ProgressFile $progressFile -OnProgress $onProgress
        if ($cmbMode.SelectedItem -eq "LIVE" -and $chkBridge.Checked -and $result.bridge_error) {
            throw ("MT5 bridge setup failed: " + [string]$result.bridge_error)
        }
        $txtLog.Text = ($result | ConvertTo-Json -Depth 5)
        $launcher = Join-Path $resolvedRoot "TradingOS.exe"
        if (Test-Path -LiteralPath $launcher) { New-DesktopShortcut -TargetPath $launcher -ShortcutName "Trading OS" }
        $readiness = Invoke-ReadinessCheck -Root $resolvedRoot -Wizard
        $script:InstallSucceeded = $true
        Write-WizardLog "Wizard completed successfully."
        $lblDoneTitle.ForeColor = [System.Drawing.Color]::DarkGreen
        $lblDoneTitle.Text = "Trading OS is ready"
        $doneBody = $script:DoneBodyDefault
        if ($readiness -and -not $readiness.ok) {
            $failed = @($readiness.checks | Where-Object { -not $_.ok })
            $warnLines = @($failed | ForEach-Object {
                $detail = if ($_.detail) { $_.detail } else { "failed" }
                "  - $($_.name): $detail"
            })
            $lblDoneTitle.ForeColor = [System.Drawing.Color]::DarkOrange
            $lblDoneTitle.Text = "Trading OS installed with warnings"
            $doneBody = "Readiness warnings:`n" + ($warnLines -join "`n") + "`n`n" + $doneBody
            Write-WizardLog ("Readiness warnings: " + ($failed.name -join ", "))
        }
        $lblDoneBody.Text = $doneBody
        Set-Step 4
    } catch {
        $progressBar.Style = "Continuous"
        $progressBar.Value = 0
        $lblProgressTitle.Text = "Install failed"
        $lblProgressTitle.ForeColor = [System.Drawing.Color]::DarkRed
        $lblProgressStep.Text = $_.Exception.Message
        $txtLog.Text = $_.Exception.Message
        Write-WizardLog "Wizard failed: $($_.Exception.Message)"
        $btnBack.Enabled = $true
        $btnInstall.Enabled = $true
        [System.Windows.Forms.MessageBox]::Show($_.Exception.Message, "Install failed", "OK", "Error")
    }
})
$form.Controls.Add($btnInstall)

$btnClose = New-Object System.Windows.Forms.Button
$btnClose.Text = "Cancel"
$btnClose.Location = New-Object System.Drawing.Point(510, 440)
$btnClose.Add_Click({ $form.Close() })
$form.Controls.Add($btnClose)

Set-Step 0
[void]$form.ShowDialog()
if ($script:InstallSucceeded) { exit 0 }
Write-WizardLog "Wizard exited without completing install."
exit 1
