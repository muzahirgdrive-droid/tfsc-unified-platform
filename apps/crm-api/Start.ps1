$ErrorActionPreference = 'Stop'
$crmRoot = $PSScriptRoot
$crmUrl = 'http://127.0.0.1:8766'
try {
    try {
        $health = Invoke-RestMethod "$crmUrl/api/health" -TimeoutSec 2
        if ($health.app -eq 'TFSC Sales CRM') { Start-Process $crmUrl; exit 0 }
    } catch {}
    $crmPython = Join-Path $crmRoot '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $crmPython)) {
        $command = Get-Command python.exe -ErrorAction SilentlyContinue
        if (-not $command) { throw 'Install Python 3.12 or newer, then run Start TFSC Sales CRM.cmd again.' }
        & $command.Source -m venv (Join-Path $crmRoot '.venv')
        if ($LASTEXITCODE -ne 0) { throw 'Could not create the Python environment.' }
        & $crmPython -m pip install -r (Join-Path $crmRoot 'requirements.txt')
        if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed. Check your network and retry.' }
    }
    $crmState = Join-Path $crmRoot '.data'
    New-Item -ItemType Directory -Path $crmState -Force | Out-Null
    $crmProcess = Start-Process -FilePath $crmPython -ArgumentList @('"' + (Join-Path $crmRoot 'launch.py') + '"') -WorkingDirectory $crmRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $crmState 'startup.log') -RedirectStandardError (Join-Path $crmState 'startup-error.log')
    foreach ($attempt in 1..40) {
        Start-Sleep -Milliseconds 500
        try { $health = Invoke-RestMethod "$crmUrl/api/health" -TimeoutSec 1; if ($health.app -eq 'TFSC Sales CRM') { Start-Process $crmUrl; exit 0 } } catch {}
        if ($crmProcess.HasExited) { break }
    }
    throw 'CRM did not start. Check .data\startup-error.log and make sure port 8766 is free.'
} catch { Write-Error $_; exit 1 }
