@echo off
REM Re-launch ourselves under "cmd /k" so the window never closes on its own:
REM whatever happens (parse error, missing tool, success) stays on screen.
if /I not "%~1"=="run" (
    start "CUTORA - mimalloc setup" cmd /k ""%~f0" run"
    exit /b 0
)
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"
title CUTORA - mimalloc setup
echo === CUTORA mimalloc setup ===

REM ============================================================================
REM  Q180: swap the Windows C-runtime heap for mimalloc (Microsoft, MIT licence).
REM
REM  Why: every export frame allocates and frees dozens of multi-megabyte buffers
REM  (decoded frames, keyer float planes, Qt layers, canvases). The Windows heap
REM  serves blocks of that size straight from VirtualAlloc, so each one costs a
REM  fresh set of page faults + zeroing and the heap lock is shared by all compose
REM  threads. Measured on the same project: allocator behaviour alone moves one
REM  frame between 76 ms and 175 ms. mimalloc keeps freed pages per thread and
REM  hands them back without touching the kernel.
REM
REM  What this does (idempotent, reversible):
REM    1. downloads the official release archive from GitHub (SHA-256 pinned)
REM    2. puts mimalloc.dll + mimalloc-redirect.dll next to runtime\python\python.exe
REM    3. backs up python.exe / pythonw.exe (*.exe.orig) and patches their import
REM       table with minject.exe so mimalloc.dll is loaded first
REM  Undo: copy runtime\python\python.exe.orig over python.exe (same for pythonw),
REM  or set MIMALLOC_DISABLE_REDIRECT=1 in the environment.
REM  Close CUTORA before running this (the exe cannot be patched while running).
REM ============================================================================

set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"
set "PYDIR=%ROOT%\runtime\python"
set "DL=%ROOT%\runtime\mimalloc"
set "VER=v3.3.2"
set "URL=https://github.com/microsoft/mimalloc/releases/download/%VER%/mimalloc-%VER%-windows-x64.tar.gz"
set "SHA=4fd23187fcc643d0240fb1e3be268e4296184d0e0a24c389569b5d04bbd802ef"
set "TGZ=%DL%\mimalloc-%VER%-windows-x64.tar.gz"

if not exist "%PYDIR%\python.exe" (
    color 0C
    echo [FATAL] runtime\python\python.exe not found. Run CUTORA_Install.bat first.
    pause
    exit /b 1
)

tasklist /FI "IMAGENAME eq python.exe" 2>nul | find /I "python.exe" >nul
if not errorlevel 1 (
    color 0E
    echo [WARN] python.exe is running. Close CUTORA and any export first, then re-run.
    pause
    exit /b 1
)

if not exist "%DL%" mkdir "%DL%"

if exist "%DL%\bin\mimalloc.dll" goto :have_files
echo [1/3] downloading mimalloc %VER% ...
curl.exe -L --fail --retry 3 -o "%TGZ%" "%URL%"
if errorlevel 1 (
    color 0C
    echo [FATAL] download failed. Download manually:
    echo         %URL%
    echo         and save it as %TGZ%
    pause
    exit /b 1
)
for /f "skip=1 tokens=1" %%H in ('certutil -hashfile "%TGZ%" SHA256 ^| findstr /v "CertUtil"') do set "GOT=%%H"
if /I not "%GOT%"=="%SHA%" (
    color 0C
    echo [FATAL] SHA-256 mismatch: got %GOT%
    del /q "%TGZ%"
    pause
    exit /b 1
)
tar -xzf "%TGZ%" -C "%DL%"
if not exist "%DL%\bin\mimalloc.dll" (
    color 0C
    echo [FATAL] archive did not contain bin\mimalloc.dll
    pause
    exit /b 1
)

:have_files
echo [2/3] installing dlls next to python.exe ...
copy /Y "%DL%\bin\mimalloc.dll" "%PYDIR%\mimalloc.dll" >nul
copy /Y "%DL%\bin\mimalloc-redirect.dll" "%PYDIR%\mimalloc-redirect.dll" >nul

echo [3/3] patching import tables (backups: *.exe.orig) ...
for %%E in (python.exe pythonw.exe) do (
    if exist "%PYDIR%\%%E" (
        if not exist "%PYDIR%\%%E.orig" copy /Y "%PYDIR%\%%E" "%PYDIR%\%%E.orig" >nul
        "%DL%\bin\minject.exe" --force --inplace "%PYDIR%\%%E"
        if errorlevel 1 (
            color 0C
            echo [FATAL] minject failed on %%E - restoring backup
            copy /Y "%PYDIR%\%%E.orig" "%PYDIR%\%%E" >nul
            pause
            exit /b 1
        )
    )
)

echo.
echo verifying ...
"%DL%\bin\minject.exe" --list "%PYDIR%\python.exe" | findstr /I "mimalloc"
"%PYDIR%\python.exe" -c "import ctypes,sys; h=ctypes.windll.kernel32.GetModuleHandleW('mimalloc.dll'); print('mimalloc loaded:', bool(h)); sys.exit(0 if h else 1)"
if errorlevel 1 (
    color 0E
    echo [WARN] python started but mimalloc.dll is not loaded. See runtime\mimalloc\bin\readme or
    echo        run with MIMALLOC_VERBOSE=1 for details. Backups are in %PYDIR%\*.exe.orig
    pause
    exit /b 1
)
color 0A
echo.
echo done. CUTORA now logs "[Runtime] mimalloc ..." at startup.
pause
exit /b 0
