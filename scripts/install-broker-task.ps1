# Install the broker as an S4U scheduled task. Run this once, elevated.
#
# Elevation is needed for one reason: S4U means "run whether operator is logged
# on or not", which grants a batch logon right. Nothing else here needs it.
#
# The task starts at logon AND at boot, and repeats every 5 minutes. The
# launcher exits immediately when the port is already listening, so the repeat
# is a cheap restart-if-dead rather than a second copy: exactly one process
# must own the budget.
#
#   Right-click > Run with PowerShell (as administrator), or:
#   powershell -ExecutionPolicy Bypass -File scripts\install-broker-task.ps1
$ErrorActionPreference = 'Stop'

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'This must run elevated. Registering an S4U task needs administrator rights.'
}

$root = Split-Path -Parent $PSScriptRoot
$launcher = Join-Path $root 'scripts\start-broker-hidden.vbs'
if (-not (Test-Path $launcher)) { throw "launcher missing: $launcher" }

$action = New-ScheduledTaskAction -Execute 'wscript.exe' -Argument "//B `"$launcher`""
$atBoot = New-ScheduledTaskTrigger -AtStartup

# A daily trigger carrying a repetition, rather than a -Once trigger with an
# unbounded duration. Two things were wrong before:
#   * [TimeSpan]::MaxValue serialises to P99999999DT23H59M59S and Task
#     Scheduler refuses the registration: "The task XML contains a value which
#     is incorrectly formatted or out of range."
#   * -Daily does not accept -RepetitionInterval in the same parameter set, so
#     the repetition is built on a -Once trigger and assigned across.
# The daily trigger re-arms every midnight, so a 24-hour window repeating every
# five minutes is continuous. AtStartup covers a reboot before that.
$daily = New-ScheduledTaskTrigger -Daily -At 12:00am
$repeat = New-ScheduledTaskTrigger -Once -At 12:00am `
    -RepetitionInterval (New-TimeSpan -Minutes 5) `
    -RepetitionDuration (New-TimeSpan -Hours 24)
$daily.Repetition = $repeat.Repetition

$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -StartWhenAvailable `
    -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1)
$taskPrincipal = New-ScheduledTaskPrincipal -UserId $env:USERNAME `
    -LogonType S4U -RunLevel Limited

Register-ScheduledTask -TaskName 'ThrottleDeck Broker' -Action $action `
    -Trigger @($atBoot, $daily) -Settings $settings -Principal $taskPrincipal -Force `
    -Description ('Shared request broker for polymarket.us and Kalshi. One ' +
        'process owns the per-IP budget so the bots on this machine stop ' +
        'breaching it together. Loopback only; holds no credentials.') | Out-Null

$task = Get-ScheduledTask -TaskName 'ThrottleDeck Broker'
Write-Host ("installed: {0}, logon type {1}" -f $task.State, $task.Principal.LogonType)
Write-Host "starting it now..."
Start-ScheduledTask -TaskName 'ThrottleDeck Broker'
Start-Sleep -Seconds 6
$up = Get-NetTCPConnection -State Listen -LocalPort 8777 -ErrorAction SilentlyContinue
if ($up) { Write-Host "broker is listening on 127.0.0.1:8777" }
else { Write-Warning "not listening yet; check logs\broker.err.log" }
