$ErrorActionPreference = 'Stop'
$statePath = Join-Path $PSScriptRoot '.data\process.json'
if (Test-Path -LiteralPath $statePath) {
    $state = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
    $healthUrl = "http://127.0.0.1:$($state.port)/api/health"
    try { $health = Invoke-RestMethod $healthUrl -TimeoutSec 3 } catch { exit 0 }
    if ($health.app -eq 'TFSC Compliance' -and $health.app_path -eq $PSScriptRoot -and $health.version -eq '4.0.0') {
        @{api_pid=$state.api_pid} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $PSScriptRoot '.data\stop-request.json')
    }
}