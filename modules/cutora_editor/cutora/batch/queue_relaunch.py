# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Wait until the old GUI PID dies, then start a new GUI process.

Used by the project queue: after one project finishes exporting, the running
app schedules this waiter and quits. The waiter relaunches ``python -m cutora``
with --queue-continue so the next project opens and export resumes.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time


def _proc_module():
    """按文件路径载入 cutora/core/proc.py（它只依赖标准库）。

    本文件是按脚本路径拉起的，启动时不能 ``import cutora``：``sys.path[0]`` 是
    ``cutora/batch``，不是仓库根，一 import 等候进程就会在写出日志之前退出，
    队列停在「下次手动打开才继续」。按路径载入同一个封装，既不 import 包，
    又守住「子进程一律走 cutora.core.proc、不弹黑框」的规矩（test_q110）。
    """
    import importlib.util
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(os.path.dirname(here), "core", "proc.py")
    spec = importlib.util.spec_from_file_location("_cutora_proc_standalone", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _hidden_popen(cmd, **kwargs):
    """不开控制台、不许 shell —— 就是 cutora.core.proc.popen。"""
    return _proc_module().popen(cmd, **kwargs)


def wait_pid(pid, timeout_ms=180000):
    pid = int(pid)
    if pid <= 0:
        return
    if os.name == "nt":
        try:
            import ctypes
            k32 = ctypes.windll.kernel32
            SYNCHRONIZE = 0x00100000
            handle = k32.OpenProcess(SYNCHRONIZE, False, pid)
            if handle:
                try:
                    k32.WaitForSingleObject(handle, int(timeout_ms))
                finally:
                    k32.CloseHandle(handle)
                return
        except Exception:
            pass
        # fallback poll
        deadline = time.time() + (timeout_ms / 1000.0)
        while time.time() < deadline:
            try:
                import ctypes
                k32 = ctypes.windll.kernel32
                handle = k32.OpenProcess(0x1000, False, pid)
                if not handle:
                    return
                code = ctypes.c_ulong()
                ok = k32.GetExitCodeProcess(handle, ctypes.byref(code))
                k32.CloseHandle(handle)
                if not ok or code.value != 259:
                    return
            except Exception:
                return
            time.sleep(0.4)
        return
    deadline = time.time() + (timeout_ms / 1000.0)
    while time.time() < deadline:
        try:
            os.kill(pid, 0)
        except OSError:
            return
        time.sleep(0.4)


def repo_root_from_here(script_file=None):
    """本文件在 cutora/batch/ 下。仓库根是再往上两级。不 import cutora。"""
    here = os.path.abspath(script_file or __file__)
    return os.path.dirname(os.path.dirname(os.path.dirname(here)))


def plan_relaunch(argv, repo):
    """等旧进程退出之后要拉起的命令。路径只从参数来，避免脚本目录 import 失败。"""
    argv = list(argv or [])
    if len(argv) < 4:
        raise ValueError("usage: queue_relaunch.py <old_pid> <python> -m cutora [args...]")
    python = argv[2]
    rest = argv[3:]
    repo = os.path.abspath(repo)
    env = dict(os.environ)
    env["PYTHONPATH"] = repo + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONUNBUFFERED"] = "1"
    if rest[:2] == ["-m", "cutora"]:
        cmd = [python, "-u", "-m", "cutora"] + list(rest[2:])
        cwd = repo
    else:
        script = rest[0]
        cmd = [python, "-u", script] + list(rest[1:])
        cwd = os.path.dirname(os.path.abspath(script)) or repo
    return cmd, cwd, env


def _log(msg):
    line = time.strftime("%Y-%m-%d %H:%M:%S ") + str(msg)
    try:
        sys.stderr.write(line + "\n")
    except Exception:
        pass
    try:
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        folder = os.path.join(base, "CUTORA", "logs")
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "queue_relaunch.log"), "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:
        pass


def main(argv=None):
    argv = list(argv or sys.argv)
    if len(argv) < 4:
        sys.stderr.write("usage: queue_relaunch.py <old_pid> <python> -m cutora [args...]\n")
        return 2
    old_pid = argv[1]
    try:
        cmd, cwd, env = plan_relaunch(argv, repo_root_from_here())
    except Exception as e:
        _log("plan failed: %s" % e)
        return 2
    wait_pid(old_pid)
    time.sleep(1.2)
    try:
        os.chdir(cwd)
    except Exception:
        pass
    flags = 0
    if os.name == "nt":
        flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    _log("start %s cwd=%s" % (cmd, cwd))
    try:
        _hidden_popen(
            cmd,
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            creationflags=flags,
            start_new_session=(os.name != "nt"),
        )
    except Exception as e:
        _log("failed to start cutora: %s" % e)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
