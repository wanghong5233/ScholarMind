Option Explicit

Dim fso, shell, scriptDir, psScript, cmd, arg
Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")

scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
psScript = scriptDir & "\tunnel_watchdog.ps1"

cmd = "powershell.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File """ & psScript & """"

If WScript.Arguments.Count > 0 Then
  arg = WScript.Arguments(0)
  cmd = cmd & " -PublicHealthUrl """ & arg & """"
End If

' 0 = hidden window, False = do not wait
shell.Run cmd, 0, False
