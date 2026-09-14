Option Explicit

' Starts the Governor window without a console window and returns immediately.
' Window style 0 is hidden, so the shortcut never flashes PowerShell. Governor
' itself is started hidden by the PowerShell launcher.
Dim shell, fso, root, command
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
root = fso.GetParentFolderName(fso.GetParentFolderName(WScript.ScriptFullName))
command = "powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File """ & _
    root & "\scripts\start-governor-hidden.ps1"""
shell.Run command, 0, False
