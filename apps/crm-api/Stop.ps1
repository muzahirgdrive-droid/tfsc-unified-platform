$ErrorActionPreference = 'Stop'
$crmState = Join-Path $PSScriptRoot '.data'
if (Test-Path -LiteralPath (Join-Path $crmState 'process.json')) {
    Set-Content -LiteralPath (Join-Path $crmState 'stop-request') -Value 'stop' -Encoding ascii
    Write-Output 'CRM will finish current requests and stop.'
} else { Write-Output 'No CRM process started by this launcher was found.' }
