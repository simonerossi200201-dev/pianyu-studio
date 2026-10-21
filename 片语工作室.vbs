Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")
root = fso.GetParentFolderName(WScript.ScriptFullName)
shell.CurrentDirectory = root
py = root & "\runtime\python\pythonw.exe"
venvPyw = root & "\runtime\venv\Scripts\pythonw.exe"
If fso.FileExists(venvPyw) Then
    shell.Run """" & venvPyw & """ """ & root & "\studio_launcher.py""", 1, False
ElseIf fso.FileExists(py) Then
    shell.Run """" & py & """ """ & root & "\studio_launcher.py""", 1, False
Else
    shell.Run "pythonw.exe """ & root & "\studio_launcher.py""", 1, False
End If
