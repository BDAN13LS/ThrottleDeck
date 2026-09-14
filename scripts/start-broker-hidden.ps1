# Serve the shared venue request broker, hidden, for the life of the machine.
#
# Every bot on this box calls the venues through here, so it must be up before
# any of them and stay up. It binds 127.0.0.1 only and holds no credentials:
# callers pass their own auth headers straight through.
#
# python.exe with its output redirected, NOT pythonw.exe. pythonw has no
# stdout at all and uvicorn writes its startup lines there, so the process dies
# on its first log call and leaves nothing behind to explain why.
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root '.venv\Scripts\python.exe'
$logDir = Join-Path $root 'logs'
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir | Out-Null }

# Already listening? Do not stand up a second one. Exactly one process owns the
# budget, and two would recreate the problem this service exists to solve.
$listening = $null
try {
    $listening = Get-NetTCPConnection -State Listen -LocalPort 8777 -ErrorAction Stop
} catch { $listening = $null }
if ($listening) { exit 0 }

# Account-specific Kalshi prediction and perps rates are intentionally not
# embedded here. Set the documented environment variables for the scheduled
# task only after checking the authenticated account's own limits response.
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
Start-Process -FilePath $python `
    -ArgumentList '-m', 'venue_broker' `
    -WorkingDirectory $root `
    -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $logDir 'broker.out.log') `
    -RedirectStandardError (Join-Path $logDir 'broker.err.log')
