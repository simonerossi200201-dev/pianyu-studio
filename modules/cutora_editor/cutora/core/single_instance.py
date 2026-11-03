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
    if sys.platform == "darwin":
        try:
            import subprocess
            pid_path = _pid_file_path()
            if os.path.isfile(pid_path):
                with open(pid_path, "r", encoding="utf-8") as fh:
                    raw = fh.read().strip()
                if raw.isdigit():
                    script = (
                        'tell application "System Events" to set frontmost of '
                        'every process whose unix id is %d to true' % int(raw)
                    )
                    subprocess.run(["osascript", "-e", script], capture_output=True, timeout=2)
                    return True
        except Exception:
            pass
        return False
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
    if _MUTEX_HANDLE is not None or _LOCK_FILE_FH is not None:
        return True
    if os.name != "nt":
        pid_path = _pid_file_path()
        if not os.path.isfile(pid_path):
            return False
        try:
            import fcntl
            with open(pid_path, "a", encoding="utf-8") as fh:
                try:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
                    return False
                except OSError:
                    return True
        except Exception:
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


def release_single_instance() -> None:
    """Release the single-instance lock and clean up the PID lockfile on process exit."""
    global _MUTEX_HANDLE, _LOCK_FILE_FH
    if _MUTEX_HANDLE is not None:
        try:
            import ctypes
            ctypes.windll.kernel32.CloseHandle(_MUTEX_HANDLE)
        except Exception:
            pass
        _MUTEX_HANDLE = None
    if _LOCK_FILE_FH is not None:
        try:
            import fcntl
            fcntl.flock(_LOCK_FILE_FH.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass
        try:
            _LOCK_FILE_FH.close()
        except Exception:
            pass
        _LOCK_FILE_FH = None
    try:
        pid_path = _pid_file_path()
        if os.path.isfile(pid_path):
            with open(pid_path, "r", encoding="utf-8") as fh:
                raw = fh.read().strip()
            if raw == str(os.getpid()):
                os.remove(pid_path)
    except Exception:
        pass


def _record_lock_state(stage: str, error: str = "") -> None:
    try:
        ltx_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
        if ltx_root not in sys.path:
            sys.path.insert(0, ltx_root)
        from core.launch_state import write_launch_state
        write_launch_state(stage, app_name="cutora", error=error)
    except Exception:
        pass


def acquire_single_instance_or_exit() -> bool:
    """Acquire the single-instance lock for the CUTORA GUI process.

    If another CUTORA GUI process is already running, focuses the existing
    window and raises ``SystemExit(0)`` immediately.
    Distinguishes real lock contention (EAGAIN/EACCES) from unexpected I/O errors,
    and never truncates the PID file before the lock is held.
    """
    import atexit
    import errno
    global _MUTEX_HANDLE, _LOCK_FILE_FH
    if _MUTEX_HANDLE is not None or _LOCK_FILE_FH is not None:
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
            _record_lock_state("EXISTING_INSTANCE_FOCUSED")
            raise SystemExit(0)
        _MUTEX_HANDLE = handle
        atexit.register(release_single_instance)
        try:
            with open(_pid_file_path(), "w", encoding="utf-8") as fh:
                fh.write(str(os.getpid()))
        except Exception:
            pass
        _record_lock_state("LOCK_ACQUIRED")
        return True

    # Non-Windows (macOS / Linux) using flock on a single persistent file descriptor
    import fcntl
    fh = None
    lock_candidates = [
        _pid_file_path(),
        os.path.join(os.path.expanduser("~"), ".cutora_single_instance.pid"),
    ]
    for cand_path in lock_candidates:
        fh = None
        try:
            fh = open(cand_path, "a+", encoding="utf-8")
        except OSError:
            continue
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            fh.seek(0)
            fh.truncate()
            fh.write(str(os.getpid()))
            fh.flush()
            _LOCK_FILE_FH = fh
            atexit.register(release_single_instance)
            _record_lock_state("LOCK_ACQUIRED")
            return True
        except OSError as exc:
            try:
                fh.close()
            except Exception:
                pass
            if exc.errno in (errno.EAGAIN, errno.EACCES, getattr(errno, "EWOULDBLOCK", errno.EAGAIN)):
                focus_existing_cutora_window()
                _record_lock_state("EXISTING_INSTANCE_FOCUSED")
                raise SystemExit(0)
            # Other I/O error on this path -> try fallback candidate
            continue

    _record_lock_state("LOCK_FAILED", error="Unable to open or lock any single-instance lockfile")
    return True
