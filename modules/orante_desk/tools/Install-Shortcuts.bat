@echo off
setlocal EnableExtensions
rem Old filename. Forwards to the Chinese-named sibling. PURE ASCII.
rem Looks for SPEC65_SHORTCUT so the name itself never has to sit in this file.
cd /d "%~dp0"
for %%F in ("*.bat") do (
  findstr /c:"SPEC65_SHORTCUT" "%%F" >nul 2>nul
  if not errorlevel 1 (
    if /i not "%%~nxF"=="Install-Shortcuts.bat" (
      call "%%F" %*
      goto :done
    )
  )
)
echo Shortcut script not found.
exit /b 1
:done
exit /b %ERRORLEVEL%
