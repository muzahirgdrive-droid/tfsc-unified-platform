$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
$studioDir = $PSScriptRoot
try {
    $pythonCandidates = @(
        (Join-Path $studioDir 'runtime\python.exe'),
        (Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe')
    )
    $studioPython = $pythonCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
    if (-not $studioPython) {
        $pythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
        if ($pythonCommand) { $studioPython = $pythonCommand.Source }
    }
    if (-not $studioPython) { throw 'Python 3.10 or newer is required. This computer can use the Python runtime included with Codex.' }
    $studioPort = 8765
    $running = $false
    foreach ($candidatePort in 8765..8785) {
        $candidateUrl = "http://127.0.0.1:$candidatePort"
        try {
            $health = Invoke-RestMethod -Uri "$candidateUrl/api/health" -TimeoutSec 1
            if ($health.app -eq 'TFSC Compliance' -and $health.app_path -eq $studioDir -and $health.version -eq '4.0.0') { $studioPort = $candidatePort; $running = $true; break }
        } catch {
            $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $candidatePort)
            try { $listener.Start(); $listener.Stop(); $studioPort = $candidatePort; break } catch { $listener.Stop() }
        }
    }
    $studioUrl = "http://127.0.0.1:$studioPort"
    if (-not $running) {
        $stateDir = Join-Path $studioDir '.data'
        New-Item -ItemType Directory -Path $stateDir -Force | Out-Null
        $arguments = @('"' + (Join-Path $studioDir 'launch.py') + '"', '--port', "$studioPort")
        $studioProcess = Start-Process -FilePath $studioPython -ArgumentList $arguments -WorkingDirectory $studioDir -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $stateDir 'startup.log') -RedirectStandardError (Join-Path $stateDir 'startup-error.log')
        $ready = $false
        foreach ($attempt in 1..40) {
            Start-Sleep -Milliseconds 250
            try { $health = Invoke-RestMethod -Uri "$studioUrl/api/health" -TimeoutSec 1; if ($health.app -eq 'TFSC Compliance') { $ready=$true; break } } catch {}
            if ($studioProcess.HasExited) { break }
        }
        if (-not $ready) { throw ('The local service could not start. See ' + (Join-Path $stateDir 'startup-error.log')) }
    }
    Start-Process $studioUrl
} catch {
    [System.Windows.Forms.MessageBox]::Show($_.Exception.Message, 'TFSC Compliance') | Out-Null
    exit 1
}
