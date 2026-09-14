# Set up ThrottleDeck on one Windows machine.
#
# This is deliberately a small orchestrator around the two maintained installers:
# the broker task owns the shared read budget and the desktop shortcut owns the
# per-user control window. It is safe to run again: an existing virtual
# environment and per-user application configuration stay in place.
[CmdletBinding()]
param(
    [switch]$PreflightOnly
)

$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
$venvDirectory = Join-Path $root '.venv'
$python = Join-Path $venvDirectory 'Scripts\python.exe'
$appsConfig = Join-Path $env:LOCALAPPDATA 'ThrottleDeck\apps.toml'
$edgeCandidates = @(
    (Join-Path $env:ProgramFiles 'Microsoft\Edge\Application\msedge.exe'),
    (Join-Path ${env:ProgramFiles(x86)} 'Microsoft\Edge\Application\msedge.exe')
)

function Test-LocalHealth {
    param([Parameter(Mandatory = $true)][string]$Url)

    try {
        return (Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 2).StatusCode -eq 200
    } catch {
        return $false
    }
}

function Assert-PortAvailableOrHealthy {
    param(
        [Parameter(Mandatory = $true)][int]$Port,
        [Parameter(Mandatory = $true)][string]$HealthUrl,
        [Parameter(Mandatory = $true)][string]$Label
    )

    $listeners = @(Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue)
    if ($listeners.Count -gt 0 -and -not (Test-LocalHealth -Url $HealthUrl)) {
        throw "$Label port $Port is already in use by something other than ThrottleDeck. Stop that service or change its port before setup."
    }
}

function Wait-ForLocalHealth {
    param(
        [Parameter(Mandatory = $true)][string]$Url,
        [Parameter(Mandatory = $true)][string]$Label
    )

    $deadline = (Get-Date).AddSeconds(30)
    while ((Get-Date) -lt $deadline) {
        if (Test-LocalHealth -Url $Url) { return }
        Start-Sleep -Milliseconds 500
    }
    throw "$Label did not answer $Url within 30 seconds. Check the logs folder in the checkout."
}

if ($env:OS -ne 'Windows_NT') {
    throw 'ThrottleDeck setup supports Windows only.'
}

# PowerShell Desktop and PowerShell Core are both supported on Windows; retain
# the edition in the diagnostic so an operator can report the exact host.
Write-Host ("PowerShell edition: {0}" -f $PSVersionTable.PSEdition)

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run this setup from an elevated PowerShell window. The unattended broker task needs administrator rights.'
}

if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
    throw 'Python 3.12 is required. Install it from python.org, then run setup again.'
}
& py -3.12 --version
if ($LASTEXITCODE -ne 0) {
    throw 'Python 3.12 is required. Install it from python.org, then run setup again.'
}

if (-not ($edgeCandidates | Where-Object { Test-Path -LiteralPath $_ })) {
    throw 'Microsoft Edge is required to open the compact control window. Install Edge, then run setup again.'
}

Assert-PortAvailableOrHealthy -Port 8777 -HealthUrl 'http://127.0.0.1:8777/health' -Label 'Broker'
Assert-PortAvailableOrHealthy -Port 8778 -HealthUrl 'http://127.0.0.1:8778/health' -Label 'Governor'

if ($PreflightOnly) {
    Write-Host 'Preflight passed. No files, tasks, shortcuts, or services were changed.'
    return
}

if (-not (Test-Path -LiteralPath $python)) {
    Write-Host 'Creating the local Python environment...'
    & py -3.12 -m venv $venvDirectory
    if ($LASTEXITCODE -ne 0) { throw 'Could not create .venv.' }
}

Write-Host 'Installing ThrottleDeck runtime dependencies...'
& $python -m pip install -e $root
if ($LASTEXITCODE -ne 0) { throw 'Could not install ThrottleDeck runtime dependencies.' }

if (-not (Test-Path -LiteralPath $appsConfig)) {
    Write-Host 'A neutral per-user application configuration will be created.'
} else {
    Write-Host 'Existing %LOCALAPPDATA%\ThrottleDeck\apps.toml will be preserved.'
}

& (Join-Path $root 'scripts\install-broker-task.ps1')
if ($LASTEXITCODE -ne 0) { throw 'Could not install the broker task.' }

& (Join-Path $root 'scripts\install-governor-shortcut.ps1')
if ($LASTEXITCODE -ne 0) { throw 'Could not install the desktop shortcut.' }

$governorLauncher = Join-Path $root 'scripts\start-governor-hidden.vbs'
Start-Process -FilePath (Join-Path $env:SystemRoot 'System32\wscript.exe') `
    -ArgumentList '//B', $governorLauncher `
    -WorkingDirectory $root `
    -WindowStyle Hidden

Wait-ForLocalHealth -Url 'http://127.0.0.1:8777/health' -Label 'Broker'
Wait-ForLocalHealth -Url 'http://127.0.0.1:8778/health' -Label 'Governor'

Write-Host 'ThrottleDeck is ready. The desktop shortcut opens the control window.'
