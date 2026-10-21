Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")
root = fso.GetParentFolderName(WScript.ScriptFullName)
shell.CurrentDirectory = root
py = root & "\runtime\python\pythonw.exe"
venvPyw = root & "\runtime\venv\Scripts\pythonw.exe"
venvCfg = root & "\runtime\venv\pyvenv.cfg"
If fso.FileExists(root & "\runtime\python\python.exe") And fso.FolderExists(root & "\runtime\venv") Then
    On Error Resume Next
    Set stm = CreateObject("ADODB.Stream")
    If Err.Number = 0 Then
        stm.Type = 2
        stm.Charset = "utf-8"
        stm.Open
        stm.WriteText "home = " & root & "\runtime\python" & vbCrLf & "include-system-site-packages = false" & vbCrLf & "version = 3.10.11" & vbCrLf
        stm.SaveToFile venvCfg, 2
        stm.Close
    End If
    On Error GoTo 0
End If
If fso.FileExists(venvPyw) Then
    shell.Run """" & venvPyw & """ """ & root & "\studio_launcher.py""", 1, False
ElseIf fso.FileExists(py) Then
    shell.Run """" & py & """ """ & root & "\studio_launcher.py""", 1, False
Else
    shell.Run "pythonw.exe """ & root & "\studio_launcher.py""", 1, False
End If
