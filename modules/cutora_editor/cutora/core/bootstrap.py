# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Launch-time environment check and the desktop shortcut.

The ready marker is one line in ``runtime/venv/.cutora_ready``:

    <sha256 of requirements.lock> <UTC timestamp>

Startup compares that hash to the lock file and does not import torch.
A thick pack already has both; install runs only when they disagree.
"""
from __future__ import annotations

import hashlib
import os
import subprocess
from datetime import datetime, timezone

from cutora.core.brand import APP_DISPLAY_NAME, TAGLINE_ZH

MARKER_NAME = ".cutora_ready"
STAMP_NAME = "desktop_shortcut.created"
SHORTCUT_NAME = APP_DISPLAY_NAME
SHORTCUT_DESCRIPTION = "%s — %s" % (APP_DISPLAY_NAME, TAGLINE_ZH)


def _root(root: str | None) -> str:
    if root:
        return os.path.abspath(root)
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def ready_marker_path(root: str | None = None) -> str:
    return os.path.join(_root(root), "runtime", "venv", MARKER_NAME)


def lock_path(root: str | None) -> str:
    return os.path.join(_root(root), "requirements.lock")


def lock_digest(root: str | None = None) -> str:
    """SHA-256 of ``requirements.lock`` as lowercase hex. Raw file bytes."""
    h = hashlib.sha256()
    with open(lock_path(root), "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def write_ready_marker(root: str | None = None, now: datetime | None = None) -> str:
    """Write the marker. Returns its path. Caller has finished installing."""
    digest = lock_digest(root)
    when = now or datetime.now(timezone.utc)
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    stamp = when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    path = ready_marker_path(root)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="ascii", newline="\n") as fh:
        fh.write("%s %s\n" % (digest, stamp))
    return path


def is_env_ready(root: str | None = None) -> bool:
    """True only when the marker's hash equals ``requirements.lock``."""
    path = ready_marker_path(root)
    try:
        with open(path, "r", encoding="ascii") as fh:
            text = fh.read().strip()
    except OSError:
        return False
    if not text:
        return False
    got = text.split()[0]
    try:
        want = lock_digest(root)
    except OSError:
        return False
    return got.lower() == want.lower()


def fast_path_ready(root: str | None = None) -> bool:
    """Hash check used by ``CUTORA.vbs``.

    When the bundled interpreter is present, rewrite ``pyvenv.cfg`` first.
    A relocated thick pack's venv still points at the pack machine's path;
    without the rewrite, ``pythonw -m cutora`` cannot start, and the silent
    path would have to give up. The hash compare stays the readiness decision.
    """
    root = _root(root)
    bundled = os.path.join(root, "runtime", "python", "python.exe")
    if os.path.isfile(bundled):
        try:
            from cutora.core.portable import rewrite_pyvenv_cfg
            rewrite_pyvenv_cfg(root)
        except Exception:
            return False
    return is_env_ready(root)


def shortcut_spec(root: str, system_root: str | None = None) -> dict:
    """Arguments for the desktop ``.lnk``. One place, used by the bats."""
    root = os.path.abspath(root)
    sysroot = system_root or os.environ.get("SystemRoot") or r"C:\Windows"
    vbs = os.path.join(root, "CUTORA.vbs")
    return {
        "Name": SHORTCUT_NAME,
        "TargetPath": os.path.join(sysroot, "System32", "wscript.exe"),
        "Arguments": '"%s"' % vbs,
        "WorkingDirectory": root,
        "IconLocation": os.path.join(root, "resources", "brand", "app.ico"),
        "Description": SHORTCUT_DESCRIPTION,
    }


def shortcut_stamp_path(user_data: str | None = None) -> str:
    if not user_data:
        from cutora.core.paths import resolve_user_data
        user_data = resolve_user_data()
    return os.path.join(os.path.abspath(user_data), STAMP_NAME)


def default_desktop_dir() -> str:
    """File-system desktop, including an OneDrive-redirected one."""
    if os.name == "nt":
        try:
            import ctypes
            buf = ctypes.create_unicode_buffer(32768)
            # CSIDL_DESKTOPDIRECTORY = 0x0010, SHGFP_TYPE_CURRENT = 0
            hr = ctypes.windll.shell32.SHGetFolderPathW(None, 0x0010, None, 0, buf)
            if hr == 0 and buf.value:
                return buf.value
        except Exception:
            pass
    return os.path.join(os.path.expanduser("~"), "Desktop")


def desktop_shortcut_path(desktop: str | None = None) -> str:
    folder = desktop or default_desktop_dir()
    return os.path.join(folder, SHORTCUT_NAME + ".lnk")


def write_shortcut(spec: dict, lnk_path: str) -> None:
    """Create the ``.lnk`` via WScript.Shell. Values travel in env vars."""
    folder = os.path.dirname(os.path.abspath(lnk_path))
    if folder:
        os.makedirs(folder, exist_ok=True)
    env = os.environ.copy()
    env["CUTORA_LNK"] = os.path.abspath(lnk_path)
    env["CUTORA_TARGET"] = spec["TargetPath"]
    env["CUTORA_ARGS"] = spec["Arguments"]
    env["CUTORA_WORKDIR"] = spec["WorkingDirectory"]
    env["CUTORA_ICON"] = spec["IconLocation"]
    env["CUTORA_DESC"] = spec["Description"]
    script = (
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:CUTORA_LNK); "
        "$s.TargetPath = $env:CUTORA_TARGET; "
        "$s.Arguments = $env:CUTORA_ARGS; "
        "$s.WorkingDirectory = $env:CUTORA_WORKDIR; "
        "$s.IconLocation = $env:CUTORA_ICON; "
        "$s.Description = $env:CUTORA_DESC; "
        "$s.Save()"
    )
    # Q106: 这里原来是裸 subprocess.run。父进程是 pythonw（无控制台），
    # powershell 是控制台程序，Windows 会为它现开一个控制台窗口 ——
    # 那就是用户看到的「点一下闪一个黑框」。走 core.proc.run 带上
    # CREATE_NO_WINDOW 就没有了。
    from cutora.core.proc import run as _run_no_window
    _run_no_window(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        env=env,
        check=True,
        capture_output=True,
    )


def _write_stamp(path: str, reason: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    when = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with open(path, "w", encoding="ascii", newline="\n") as fh:
        fh.write("%s %s\n" % (reason, when))


def _require_shortcut_files(spec: dict, root: str) -> None:
    vbs = os.path.join(root, "CUTORA.vbs")
    if not os.path.isfile(vbs):
        raise FileNotFoundError(vbs)
    if not os.path.isfile(spec["IconLocation"]):
        raise FileNotFoundError(spec["IconLocation"])


def maybe_create_desktop_shortcut(
    root: str | None = None,
    *,
    desktop: str | None = None,
    user_data: str | None = None,
    system_root: str | None = None,
    write_lnk=None,
) -> str:
    """Create the desktop icon at most once.

    Returns ``created``, ``exists``, or ``skipped``.
    ``skipped`` means the stamp is already there: a shortcut the user
    deleted is not put back.
    """
    root = _root(root)
    stamp = shortcut_stamp_path(user_data)
    if os.path.isfile(stamp):
        return "skipped"
    lnk = desktop_shortcut_path(desktop)
    if os.path.isfile(lnk):
        _write_stamp(stamp, "existed")
        return "exists"
    spec = shortcut_spec(root, system_root=system_root)
    _require_shortcut_files(spec, root)
    writer = write_lnk or write_shortcut
    writer(spec, lnk)
    _write_stamp(stamp, "created")
    return "created"


def create_desktop_shortcut(
    root: str | None = None,
    *,
    desktop: str | None = None,
    user_data: str | None = None,
    system_root: str | None = None,
    write_lnk=None,
) -> str:
    """Always (re)write the desktop shortcut and record the stamp."""
    root = _root(root)
    spec = shortcut_spec(root, system_root=system_root)
    _require_shortcut_files(spec, root)
    lnk = desktop_shortcut_path(desktop)
    writer = write_lnk or write_shortcut
    writer(spec, lnk)
    _write_stamp(shortcut_stamp_path(user_data), "created")
    return lnk


