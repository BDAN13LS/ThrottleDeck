# Install one desktop shortcut that opens the ThrottleDeck control window.
#
# Run this as the signed-in user. No elevation is needed: the shortcut belongs
# to this account, and the hidden launcher starts the per-user Governor service.
# This does not create a scheduled task, does not register a second instance,
# and does not touch the existing ThrottleDeck Broker shared-task process.
$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
$launcher = Join-Path $root 'scripts\start-governor-hidden.vbs'
if (-not (Test-Path -LiteralPath $launcher)) {
    throw "launcher missing: $launcher"
}

# The public product name is intentionally short so the desktop label stays tidy.
$shortcutName = 'ThrottleDeck'

$desktop = [Environment]::GetFolderPath('Desktop')
$shortcutPath = Join-Path $desktop ($shortcutName + '.lnk')

$wscript = Join-Path $env:SystemRoot 'System32\wscript.exe'
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $wscript
$shortcut.Arguments = '//B "' + $launcher + '"'
$shortcut.WorkingDirectory = $root
$shortcut.WindowStyle = 7
$shortcut.Description = $shortcutName
$shortcut.Save()

$legacyName = 'Governor ' + [char]0x2014 + ' Bot Control.lnk'
$legacyPath = Join-Path $desktop $legacyName
if (Test-Path -LiteralPath $legacyPath) {
    Remove-Item -LiteralPath $legacyPath -Force
}

$governorRoot = Join-Path $env:LOCALAPPDATA 'ThrottleDeck'
$appsConfig = Join-Path $governorRoot 'apps.toml'
$appsTemplate = Join-Path $root 'governor\default-apps.toml'
if (-not (Test-Path -LiteralPath $appsConfig)) {
    New-Item -ItemType Directory -Path $governorRoot -Force | Out-Null
    Copy-Item -LiteralPath $appsTemplate -Destination $appsConfig
}

Write-Host ("installed shortcut: {0}" -f $shortcutPath)
Write-Host ("per-user app config: {0}" -f $appsConfig)
Write-Host "Open it once to start ThrottleDeck and show its window."
