Option Explicit

' Starts the broker without a console window and returns immediately. The
' broker is a long-lived service, so unlike the scheduled observers this must
' NOT wait for it to exit.
Dim shell, fso, root, command
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
root = fso.GetParentFolderName(fso.GetParentFolderName(WScript.ScriptFullName))
command = "powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File """ & _
    root & "\scripts\start-broker-hidden.ps1"""
shell.Run command, 0, False
