@echo off
setlocal EnableExtensions
rem SPEC65_SHORTCUT
rem Desktop + Start Menu shortcuts. PURE ASCII.
rem Target = .venv\Scripts\pythonw.exe -m orante   (not a bat, not an exe)
rem Icon   = ui\assets\orante-me.ico  (made from "Orante me.png")
rem The work is done by Python (orante\shortcut.py, ctypes + IShellLink).
rem No shell one-liners here on purpose: a long inline -Command line is one of
rem the patterns antivirus flags hardest, and Python does the same job natively.
pushd "%~dp0.."
set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
"%PY%" -m orante.shortcut %*
set "EC=%ERRORLEVEL%"
popd
exit /b %EC%
