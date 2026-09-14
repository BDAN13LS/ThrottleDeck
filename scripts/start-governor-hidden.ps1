# Start Governor hidden when it is not already up, then open its own window.
#
# Governor is a per-user control plane on 127.0.0.1:8778. This launcher never
# starts, stops, or restarts the existing ThrottleDeck Broker shared-task process: that
# service owns 127.0.0.1:8777 and exactly one process owns the venue budget.
# Governor is separate and must not become a second supervised instance.
#
# Nothing here prints the per-install control key or the one-use launch nonce.
# The key is read from the per-user file, sent once in a request header, and
# discarded; the nonce only ever travels inside the Edge command line.
$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root '.venv\Scripts\python.exe'
$logDir = Join-Path $root 'logs'
$baseUrl = 'http://127.0.0.1:8778'
$healthUrl = "$baseUrl/health"
$healthTimeoutSeconds = 25

function Test-GovernorHealthy {
    try {
        $response = Invoke-WebRequest -Uri $healthUrl -UseBasicParsing -TimeoutSec 2
        return ($response.StatusCode -eq 200)
    } catch {
        return $false
    }
}

# Exactly one Governor: if the port already answers health, do not start another.
if (-not (Test-GovernorHealthy)) {
    if (-not (Test-Path -LiteralPath $logDir)) {
        New-Item -ItemType Directory -Path $logDir | Out-Null
    }
    Start-Process -FilePath $python `
        -ArgumentList '-m', 'governor' `
        -WorkingDirectory $root `
        -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $logDir 'governor.out.log') `
        -RedirectStandardError (Join-Path $logDir 'governor.err.log')

    $deadline = (Get-Date).AddSeconds($healthTimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Milliseconds 500
        if (Test-GovernorHealthy) { break }
    }
    if (-not (Test-GovernorHealthy)) {
        Write-Error "Governor did not answer $healthUrl within $healthTimeoutSeconds seconds."
        exit 1
    }
}

# Python reads the binary install key, requests a one-use nonce in memory, and
# opens Edge in app mode.  Neither the key nor the nonce is written to a log.
Start-Process -FilePath $python `
    -ArgumentList '-m', 'governor', '--open' `
    -WorkingDirectory $root `
    -WindowStyle Hidden

exit 0
