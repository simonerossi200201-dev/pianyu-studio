Option Explicit

' Zero-window entry. Readiness and install live in CUTORA.bat and
' cutora.core.bootstrap. This file only branches.
Dim fso, shell, root, pythonw, probe, q, rc, code

Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")
q = Chr(34)
root = fso.GetParentFolderName(WScript.ScriptFullName)
shell.CurrentDirectory = root

pythonw = root & "\runtime\venv\Scripts\pythonw.exe"
probe = root & "\runtime\python\pythonw.exe"
If Not fso.FileExists(probe) Then probe = pythonw

rc = 1
If fso.FileExists(pythonw) And fso.FileExists(probe) Then
    code = "import sys; sys.path.insert(0, sys.argv[1]); from cutora.core.bootstrap import fast_path_ready; raise SystemExit(0 if fast_path_ready(sys.argv[1]) else 1)"
    rc = shell.Run(q & probe & q & " -c " & q & code & q & " " & q & root & q, 0, True)
End If

If rc = 0 Then
    shell.Run q & pythonw & q & " -m cutora", 0, False
    ' Normal thick packs never enter the bat, so the once-only shortcut
    ' has to be requested here. The decision is in Python.
    code = "import sys; sys.path.insert(0, sys.argv[1]); from cutora.core.bootstrap import maybe_create_desktop_shortcut; maybe_create_desktop_shortcut(sys.argv[1])"
    shell.Run q & probe & q & " -c " & q & code & q & " " & q & root & q, 0, True
Else
    shell.Run "cmd.exe /d /c " & q & root & "\CUTORA.bat" & q & " bootstrap", 1, False
End If
