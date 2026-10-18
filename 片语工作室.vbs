Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")
root = fso.GetParentFolderName(WScript.ScriptFullName)
shell.CurrentDirectory = root
py = root & "\runtime\python\pythonw.exe"
venvPyw = root & "\runtime\venv\Scripts\pythonw.exe"
If fso.FileExists(py) Then
    shell.Run """" & py & """ -c ""import sys; sys.path.insert(0, r'" & root & "'); from core.portable import rewrite_pyvenv_cfg; rewrite_pyvenv_cfg(r'" & root & "')""", 0, True
End If
If fso.FileExists(venvPyw) Then
    shell.Run """" & venvPyw & """ """ & root & "\studio_launcher.py""", 1, False
ElseIf fso.FileExists(py) Then
    shell.Run """" & py & """ """ & root & "\studio_launcher.py""", 1, False
Else
    shell.Run """" & root & "\片语工作室.bat""", 0, False
End If
