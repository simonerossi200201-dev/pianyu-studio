# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Single-instance lock for CUTORA GUI.

Ensures only ONE CUTORA GUI instance can run at any time across the system
(via Windows Named Mutex ``Local\\CUTORA_SingleInstance_Mutex_v1``).
If a second launch is attempted, it brings the existing CUTORA window to the
foreground and exits immediately before loading PyTorch or Qt.
"""
from __future__ import annotations

import os
import sys
import tempfile

MUTEX_NAME = "Local\\CUTORA_SingleInstance_Mutex_v1"
ERROR_ALREADY_EXISTS = 183
SYNCHRONIZE = 0x00100000
SW_RESTORE = 9

_MUTEX_HANDLE = None
_LOCK_FILE_FH = None


def _pid_file_path() -> str:
    return os.path.join(tempfile.gettempdir(), "cutora_single_instance.pid")


def focus_existing_cutora_window() -> bool:
    """Attempt to bring the existing running CUTORA main window to the foreground."""
    if os.name != "nt":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        target_pid = None
        pid_path = _pid_file_path()
        if os.path.isfile(pid_path):
            try:
                with open(pid_path, "r", encoding="utf-8") as fh:
                    raw = fh.read().strip()
                if raw.isdigit():
                    target_pid = int(raw)
            except Exception:
                target_pid = None

        found_hwnds: list[int] = []

        WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def _enum_cb(hwnd, _lparam):
            try:
                if not user32.IsWindowVisible(hwnd):
                    return True
                pid = wintypes.DWORD(0)
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                if target_pid and pid.value == target_pid:
                    length = user32.GetWindowTextLengthW(hwnd)
                    if length > 0:
                        found_hwnds.append(hwnd)
                        return False
                else:
                    length = user32.GetWindowTextLengthW(hwnd)
                    if length > 0:
                        buf = ctypes.create_unicode_buffer(length + 1)
                        user32.GetWindowTextW(hwnd, buf, length + 1)
                        title = buf.value or ""
                        if "CUTORA" in title.upper() or "片语" in title:
                            if "片语工作室" not in title:
                                found_hwnds.append(hwnd)
                                return False
            except Exception:
                pass
            return True

        user32.EnumWindows(WNDENUMPROC(_enum_cb), 0)
        if found_hwnds:
            hwnd = found_hwnds[0]
            if user32.IsIconic(hwnd):
                user32.ShowWindow(hwnd, SW_RESTORE)
            user32.SetForegroundWindow(hwnd)
            return True
    except Exception:
        pass
    return False


def is_cutora_running() -> bool:
    """Return True if a CUTORA GUI instance currently holds the single-instance mutex."""
    if _MUTEX_HANDLE is not None:
        return True
    if os.name != "nt":
        return False
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        h = kernel32.OpenMutexW(SYNCHRONIZE, False, MUTEX_NAME)
        if h:
            kernel32.CloseHandle(h)
            return True
    except Exception:
        pass
    return False


def acquire_single_instance_or_exit() -> bool:
    """Acquire the single-instance lock for the CUTORA GUI process.

    If another CUTORA GUI process is already running, focuses the existing
    window and raises ``SystemExit(0)`` immediately.
    """
    global _MUTEX_HANDLE, _LOCK_FILE_FH
    if _MUTEX_HANDLE is not None:
        return True

    if os.name == "nt":
        import ctypes
        kernel32 = ctypes.windll.kernel32
        kernel32.SetLastError(0)
        handle = kernel32.CreateMutexW(None, True, MUTEX_NAME)
        last_err = kernel32.GetLastError()
        if not handle:
            return True
        if last_err == ERROR_ALREADY_EXISTS:
            kernel32.CloseHandle(handle)
            focus_existing_cutora_window()
            raise SystemExit(0)
        _MUTEX_HANDLE = handle
        try:
            with open(_pid_file_path(), "w", encoding="utf-8") as fh:
                fh.write(str(os.getpid()))
        except Exception:
            pass
        return True

    # Non-Windows fallback using file lock
    try:
        import fcntl
        fh = open(_pid_file_path(), "w", encoding="utf-8")
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        fh.write(str(os.getpid()))
        fh.flush()
        _LOCK_FILE_FH = fh
        return True
    except Exception:
        raise SystemExit(0)
