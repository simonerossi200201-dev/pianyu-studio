# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""
cli.py — CUTORA 无头节点入口

模块级禁止 import config / torch / whisperx。
--describe / --status 必须在几十毫秒内返回，且 stdout 只有 JSON。
真正干活的 op 在 late_dispatch 里直接 import cutora 模块。
"""
from __future__ import annotations

import atexit
import copy
import io
import json
import os
import sys
import time
import uuid
import traceback
from datetime import datetime, timezone, timedelta

# ---------------------------------------------------------------------------
#  常量
# ---------------------------------------------------------------------------

APP_NAME = "CUTORA"
APP_VERSION = "7.0.0"
from cutora.core.paths import FFMPEG_DIR, REPO_ROOT, resolve_user_data, settings_file
from cutora.core.log import get_logger as _get_cli_logger
from cutora.core.proc import popen as _no_window_popen, run as _no_window_run
_log = _get_cli_logger("cutora.batch.cli")

def _user_data():
    return resolve_user_data()

def _lock_path():
    return os.path.join(_user_data(), "cutora.lock")

def _jobs_dir():
    return os.path.join(_user_data(), "jobs")

def _export_state_path():
    return os.path.join(_user_data(), "export_state.json")

def _queue_path():
    return settings_file("project_queue.json")

def _refresh_cli_paths():
    global USER_DATA, LOCK_PATH, JOBS_DIR, EXPORT_STATE_PATH, QUEUE_PATH
    USER_DATA = _user_data()
    LOCK_PATH = _lock_path()
    JOBS_DIR = _jobs_dir()
    EXPORT_STATE_PATH = _export_state_path()
    QUEUE_PATH = _queue_path()

_refresh_cli_paths()
QUEUE_VERSION = 1
TZ8 = timezone(timedelta(hours=8))

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_USAGE = 2
EXIT_GATE = 3
EXIT_LOCKED = 4

# --json 时把真正的 stdout 藏在这里，其余 print 全部进 stderr
_REAL_STDOUT = None
_STDOUT_REDIRECTED = False
_LOCK_OWNED = False
_LOCK_PID = None

VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v"}

class CliError(Exception):
    def __init__(self, message, code=EXIT_FAIL, extra=None):
        super().__init__(message)
        self.message = str(message)
        self.code = int(code)
        self.extra = extra or {}

class GateError(CliError):
    def __init__(self, message, extra=None):
        extra = dict(extra or {})
        extra.setdefault("ok", False)
        extra.setdefault("human_needed", True)
        super().__init__(message, EXIT_GATE, extra)

# ===========================================================================
#  时间 / 路径 / JSON
# ===========================================================================

def now_iso():
    return datetime.now(TZ8).isoformat(timespec="seconds")

def abs_path(p):
    if p is None or p == "":
        return p
    return os.path.abspath(os.path.expanduser(str(p)))

def eprint(*args, **kwargs):
    kwargs.setdefault("file", sys.stderr)
    kwargs.setdefault("flush", True)
    try:
        print(*args, **kwargs)
    except Exception:
        pass

def atomic_write_text(path, text, encoding="utf-8"):
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with io.open(tmp, "w", encoding=encoding, newline="\n") as f:
        f.write(text)
    os.replace(tmp, path)

def atomic_write_json(path, obj):
    atomic_write_text(path, json.dumps(obj, ensure_ascii=False, indent=2))

def load_json_file(path):
    with io.open(path, "r", encoding="utf-8") as f:
        return json.load(f)

# ===========================================================================
#  stdout 隔离 + QuickEdit
# ===========================================================================

def _disable_quickedit():
    try:
        from cutora.render import export_guard as export_guard
        export_guard.disable_console_quickedit()
    except Exception:
        pass

def begin_json_stdio():
    """把 stdout 指到 stderr，保住真正的 stdout 给最终 JSON。"""
    global _REAL_STDOUT, _STDOUT_REDIRECTED
    if _STDOUT_REDIRECTED:
        return
    _REAL_STDOUT = sys.stdout
    sys.stdout = sys.stderr
    _STDOUT_REDIRECTED = True

def json_stdout():
    return _REAL_STDOUT if _REAL_STDOUT is not None else sys.stdout

def emit_json(obj):
    text = json.dumps(obj, ensure_ascii=False, indent=2)
    out = json_stdout()
    try:
        out.write(text)
        if not text.endswith("\n"):
            out.write("\n")
        out.flush()
    except Exception:
        eprint(text)

# ===========================================================================
#  单实例锁
# ===========================================================================

def _pid_alive(pid):
    try:
        from cutora.render import export_guard as export_guard
        return bool(export_guard.pid_alive(int(pid)))
    except Exception:
        try:
            pid = int(pid)
            if pid <= 0:
                return False
            if os.name == "nt":
                import ctypes
                h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
                if not h:
                    return False
                code = ctypes.c_ulong()
                ok = ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(code))
                ctypes.windll.kernel32.CloseHandle(h)
                return bool(ok) and code.value == 259
            os.kill(pid, 0)
            return True
        except Exception:
            return False

def read_lock():
    try:
        if not os.path.isfile(LOCK_PATH):
            return None
        with io.open(LOCK_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except Exception:
        return None
    return None

def _clear_lock_file():
    try:
        if os.path.isfile(LOCK_PATH):
            os.remove(LOCK_PATH)
    except Exception:
        pass

def acquire_lock(op):
    """成功返回 None；失败返回占用者 dict。"""
    global _LOCK_OWNED, _LOCK_PID
    _refresh_cli_paths()
    os.makedirs(USER_DATA, exist_ok=True)
    payload = {
        "pid": os.getpid(),
        "started_at": now_iso(),
        "op": str(op or "unknown"),
    }
    for _ in range(3):
        try:
            fd = os.open(LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            try:
                os.write(fd, json.dumps(payload, ensure_ascii=False).encode("utf-8"))
            finally:
                os.close(fd)
            _LOCK_OWNED = True
            _LOCK_PID = os.getpid()
            atexit.register(release_lock_if_owner)
            return None
        except FileExistsError:
            cur = read_lock()
            if cur and _pid_alive(cur.get("pid")):
                return cur
            _clear_lock_file()
        except Exception as e:
            raise CliError("无法创建单实例锁: %s" % e, EXIT_FAIL)
    cur = read_lock()
    return cur or {"pid": "?", "op": "unknown", "started_at": ""}

def release_lock_if_owner():
    global _LOCK_OWNED
    if not _LOCK_OWNED:
        return
    cur = read_lock()
    try:
        if cur and int(cur.get("pid") or 0) == os.getpid():
            _clear_lock_file()
    except Exception:
        try:
            _clear_lock_file()
        except Exception:
            pass
    _LOCK_OWNED = False

def lock_blocked_message(holder):
    pid = holder.get("pid")
    op = holder.get("op") or "?"
    started = holder.get("started_at") or "?"
    who = "图形界面" if str(op) == "gui" else ("无头 op=%s" % op)
    return ("已被占用：%s PID=%s，启动于 %s。等它结束，或确认该进程已死"
            "（锁文件 %s 会在 PID 死后自动清理）。" % (who, pid, started, LOCK_PATH))

# ===========================================================================
#  工程队列（图形界面重启 / 无头 queue_run 共用同一份 JSON）
# ===========================================================================

def split_plan(n, chunk):
    """n 条视频按 chunk 切开。返回 [{index, start, end, count}, ...]，start/end 从 1 计。"""
    try:
        n = int(n)
        chunk = int(chunk)
    except Exception:
        raise CliError("chunk 必须是整数", EXIT_USAGE)
    if chunk <= 0:
        raise CliError("chunk 必须 > 0", EXIT_USAGE)
    if n <= 0:
        return []
    parts = []
    i = 0
    index = 1
    while i < n:
        j = min(n, i + chunk)
        parts.append({"index": index, "start": i + 1, "end": j, "count": j - i})
        i = j
        index += 1
    return parts

def split_stem_from_path(src):
    stem = os.path.splitext(os.path.basename(src or ""))[0]
    if stem.endswith(".asproj"):
        stem = stem[:-7]
    return stem or "split"

def split_part_filename(stem, start, end, index, name_pattern=None):
    name = "%s_%d-%d.asproj.json" % (stem, start, end)
    if name_pattern:
        try:
            name = str(name_pattern).format(stem=stem, start=start, end=end, index=index)
        except Exception:
            pass
    return name

def split_tasks_to_projects(host, tasks, chunk, outdir, stem, name_pattern=None):
    """按列表顺序把 tasks 切成多个工程文件。返回 (artifacts, plan)。"""
    n = len(tasks or [])
    if n == 0:
        raise CliError("工程里没有视频", EXIT_FAIL)
    plan = split_plan(n, chunk)
    outdir = abs_path(outdir)
    os.makedirs(outdir, exist_ok=True)
    artifacts = []
    for part in plan:
        i0 = part["start"] - 1
        i1 = part["end"]
        name = split_part_filename(stem, part["start"], part["end"], part["index"], name_pattern)
        out = os.path.join(outdir, name)
        write_project_file(host, out, tasks[i0:i1], curr_idx=0 if i1 > i0 else -1)
        artifacts.append(abs_path(out))
    return artifacts, plan

def load_queue():
    _refresh_cli_paths()
    if not os.path.isfile(QUEUE_PATH):
        return None
    try:
        data = load_json_file(QUEUE_PATH)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    return data

def save_queue(data):
    data = dict(data or {})
    data["version"] = QUEUE_VERSION
    data["updated_at"] = now_iso()
    _refresh_cli_paths()
    os.makedirs(USER_DATA, exist_ok=True)
    atomic_write_json(QUEUE_PATH, data)
    return data

def empty_queue():
    return {
        "version": QUEUE_VERSION,
        "active": False,
        "paused": False,
        "pause_reason": "",
        "resume_on_launch": False,
        "started_at": None,
        "updated_at": now_iso(),
        "current_index": 0,
        "options": {"restart_between": True, "auto_subtitle": True, "auto_credit": False, "credit_preset_id": ""},
        "items": [],
    }

def make_queue(projects, restart_between=True, auto_subtitle=True,
               auto_credit=False, credit_preset_id=""):
    items = []
    for p in projects or []:
        items.append({
            "project": abs_path(p),
            "status": "pending",
            "started_at": None,
            "finished_at": None,
            "error": None,
            "counts": {},
        })
    q = empty_queue()
    q["active"] = True
    q["paused"] = False
    q["started_at"] = now_iso()
    q["options"] = {
        "restart_between": bool(restart_between),
        "auto_subtitle": bool(auto_subtitle),
        "auto_credit": bool(auto_credit),
        "credit_preset_id": str(credit_preset_id or ""),
    }
    q["items"] = items
    q["current_index"] = 0
    return save_queue(q)

def pause_queue(reason=""):
    q = load_queue()
    if not q:
        return None
    q["paused"] = True
    q["pause_reason"] = str(reason or "")
    q["resume_on_launch"] = False
    return save_queue(q)

def unpause_queue():
    q = load_queue()
    if not q:
        return None
    q["paused"] = False
    q["pause_reason"] = ""
    return save_queue(q)

def deactivate_queue():
    q = load_queue()
    if not q:
        return None
    q["active"] = False
    q["paused"] = False
    q["resume_on_launch"] = False
    return save_queue(q)

def mark_queue_item(index, status, error=None, counts=None):
    q = load_queue()
    if not q:
        return None
    items = q.get("items") or []
    if not (0 <= int(index) < len(items)):
        return q
    index = int(index)
    items[index]["status"] = status
    if status == "running":
        items[index]["started_at"] = now_iso()
        q["current_index"] = index
    elif status in ("done", "failed", "skipped"):
        items[index]["finished_at"] = now_iso()
    if error is not None:
        items[index]["error"] = error
    if counts is not None:
        items[index]["counts"] = counts
    q["items"] = items
    return save_queue(q)

def next_pending_index(q, after=None):
    items = (q or {}).get("items") or []
    start = 0 if after is None else int(after) + 1
    for i in range(max(0, start), len(items)):
        if (items[i].get("status") or "pending") == "pending":
            return i
    return None

def queue_summary(q=None):
    q = q if q is not None else load_queue()
    if not q:
        return {
            "present": False, "active": False, "paused": False,
            "current": 0, "total": 0, "items": [],
            "path": abs_path(QUEUE_PATH),
        }
    items = q.get("items") or []
    counts = {"pending": 0, "running": 0, "done": 0, "failed": 0, "skipped": 0}
    slim = []
    for it in items:
        st = it.get("status") or "pending"
        counts[st] = counts.get(st, 0) + 1
        slim.append({
            "project": it.get("project"),
            "name": os.path.basename(it.get("project") or ""),
            "status": st,
            "error": it.get("error"),
        })
    cur = int(q.get("current_index") or 0)
    return {
        "present": True,
        "active": bool(q.get("active")),
        "paused": bool(q.get("paused")),
        "pause_reason": q.get("pause_reason") or "",
        "resume_on_launch": bool(q.get("resume_on_launch")),
        "current": (cur + 1) if items else 0,
        "current_index": cur,
        "total": len(items),
        "counts": counts,
        "items": slim,
        "path": abs_path(QUEUE_PATH),
        "restart_between": bool((q.get("options") or {}).get("restart_between", True)),
        "auto_subtitle": bool((q.get("options") or {}).get("auto_subtitle", True)),
    }

def spawn_gui_relaunch(extra_args=None):
    """当前进程退出后再拉起图形界面。由 queue_relaunch.py 等 PID 结束后启动。

    等候进程必须脱离父进程的作业对象，否则主程序一关，等候的那个也一起死，
    队列就停在「下次手动打开才继续」。
    """
    extra_args = list(extra_args or [])
    waiter = os.path.join(os.path.dirname(os.path.abspath(__file__)), "queue_relaunch.py")
    argv = [sys.executable, "-u", waiter, str(os.getpid()), sys.executable, "-m", "cutora"] + extra_args
    import subprocess
    env = dict(os.environ)
    repo = os.path.abspath(REPO_ROOT)
    env["PYTHONPATH"] = repo + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONUNBUFFERED"] = "1"
    base = 0
    breakaway = 0
    if os.name == "nt":
        base = (
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        )
        breakaway = 0x01000000  # CREATE_BREAKAWAY_FROM_JOB
    attempts = [base | breakaway, base] if breakaway else [base]
    last = None
    for flags in attempts:
        try:
            _no_window_popen(
                argv,
                cwd=REPO_ROOT,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=flags,
                start_new_session=(os.name != "nt"),
            )
            eprint("[Queue] 已安排重启 pid=%s args=%s" % (os.getpid(), extra_args))
            return
        except OSError as e:
            last = e
    raise last or OSError("无法安排队列重启")

# ===========================================================================
#  能力清单（对外契约）
# ===========================================================================

OPS_SPEC = [
    {
        "name": "stitch",
        "summary": "把一个目录里的片段按文案智能拼接成整条视频，并写出可打开的工程文件",
        "in": "dir",
        "out": "project",
        "implemented": True,
        "params": {
            "dir": {
                "type": "path", "required": True,
                "desc": "素材目录。里面是按组命名的短视频，例如 01.mp4、01 (2).mp4、01a.mp4",
            },
            "captions": {
                "type": "text|path", "required": True,
                "desc": ("文案。三种写法：1) 直接把全文放进这个字段；"
                         "2) @文件路径 读取文本文件；3) 字符串 \"-\" 表示从 stdin 读。"
                         "文本格式二选一：每段以「01|文案」或「01:文案」开头；"
                         "或按空行分段，段数必须和素材组数一致。"),
            },
            "language": {"type": "string", "default": "en",
                         "desc": "识别/对齐语言。常用 en（英语）或 it（意大利语）"},
            "out_dir": {"type": "path", "default": "<dir>\\\\_stitched",
                        "desc": "成品目录。默认是素材目录下的 _stitched"},
            "allow_blocking": {"type": "bool", "default": False,
                               "desc": "素材体检发现断号/无音轨/组内分辨率不一致时，默认停下。true 才继续"},
            "quarantine": {"type": "bool", "default": True,
                           "desc": "把根本不是视频的坏文件挪到 <dir>/_invalid/。只移动不删除"},
            "write_project": {"type": "bool", "default": True,
                              "desc": "成功后写出 stitched.asproj.json，供后续 auto_subtitle / 导出使用"},
            "allow_missing": {"type": "bool", "default": False,
                              "desc": "有缺失文案时默认 exit 3 不写时间线。true 仍写出时间线工程"},
            "analyze": {"type": "bool", "default": True,
                        "desc": "false = 只跑素材体检，不调 whisper、不写时间线。给「仅检查」用"},
            "encode": {"type": "bool", "default": True,
                       "desc": "false = 分析到 cutlist 为止。true 写出时间线工程（不再拼中间 mp4）"},
            "ignore_scripture_refs": {
                "type": "bool", "default": True,
                "desc": "括号里的经文出处（如 (John 8:34-35)）不列入缺失清单。配音不会念这些。默认 true"},
            "group_by_folder": {"type": "bool", "default": False,
                "desc": "true 时一个文件夹算一组，忽略文件名与前缀规则。文件随便命名也能拼"},
        },
        "exit_codes": {
            "0": "成功，成品 mp4 和工程文件已写出",
            "3": "闸门未过：有缺失文案，或体检需要人工确认。未编码",
            "1": "失败",
            "4": "被单实例锁挡住",
        },
        "idempotent": True,
        "note": ("有缺失文案时默认不写时间线，会导出「缺失文案清单.txt」并以 3 退出。"
                 "补齐缺失片段后用同一命令重跑即可；未改动的组会复用上次转录缓存。"
                 "成功时写出 stitched.asproj.json，不生成 _stitched 中间 mp4。"),
    },
    {
        "name": "import_media",
        "summary": "把一组成品视频（通常是 _stitched 里的 mp4）写成片语工程文件",
        "in": "dir",
        "out": "project",
        "implemented": True,
        "params": {
            "videos": {"type": "path|list", "required": True,
                       "desc": "视频目录，或视频路径数组。目录下按文件名排序"},
            "captions": {"type": "text|path", "required": False,
                         "desc": "可选文案，格式同 stitch。按文件名中的组号对应"},
            "out": {"type": "path", "required": False,
                    "desc": "工程输出路径。默认 <目录>/imported.asproj.json"},
        },
        "exit_codes": {"0": "成功", "1": "失败", "2": "参数错误"},
        "idempotent": True,
        "note": "stitch 成功时已经自动写过工程。只有手里只有 mp4+文案、没有工程文件时才需要这个。",
    },
    {
        "name": "auto_subtitle",
        "summary": "按每条视频的文案强制对齐，生成字幕时间轴（对应界面「一键全自动」）",
        "in": "project",
        "out": "project",
        "implemented": True,
        "params": {
            "project": {"type": "path", "required": True, "desc": "工程文件 .asproj.json"},
            "out": {"type": "path", "required": False,
                    "desc": "写出路径。默认覆盖原工程"},
            "force_relayout": {"type": "bool", "default": False,
                               "desc": "当所有视频已经有字幕时，默认不重打轴。true 才会覆盖已有时间轴"},
        },
        "exit_codes": {"0": "成功", "1": "失败"},
        "idempotent": False,
        "note": ("静默覆盖已调好的时间轴是不可逆的，所以 force_relayout 默认 false。"
                 "只处理「有文案但还没有字幕分段」的条目。"),
    },
    {
        "name": "save_project",
        "summary": "读取工程并重新规范化后写回（另存为）",
        "in": "project",
        "out": "project",
        "implemented": True,
        "params": {
            "project": {"type": "path", "required": True, "desc": "源工程"},
            "out": {"type": "path", "required": False, "desc": "目标路径，默认覆盖源文件"},
        },
        "exit_codes": {"0": "成功", "1": "失败"},
        "idempotent": True,
        "note": "不弹窗、不改用户的最近项目列表。",
    },
    {
        "name": "split_project",
        "summary": "按列表顺序把一个工程切成多个较小的工程文件",
        "in": "project",
        "out": "project[]",
        "implemented": True,
        "params": {
            "project": {"type": "path", "required": True, "desc": "源工程"},
            "chunk": {"type": "int", "default": 80, "desc": "每个工程最多几条视频。225 条 chunk=80 → 80/80/65"},
            "outdir": {"type": "path", "required": False, "desc": "输出目录，默认与源工程同目录"},
            "name_pattern": {"type": "string", "required": False,
                             "desc": "默认 <原名>_<start>-<end>.asproj.json"},
        },
        "exit_codes": {"0": "成功", "1": "失败"},
        "idempotent": True,
        "note": "不修改原工程。产出的每个文件都可以单独用界面打开或交给 auto_subtitle / batch_export。图形界面：文件 → 自动拆分工程。",
    },
    {
        "name": "queue_run",
        "summary": "按工程队列依次导出；每个工程都在全新进程里跑完再进入下一个（等价于软件重启）",
        "in": "project[]",
        "out": "video[]",
        "implemented": True,
        "params": {
            "projects": {"type": "path|list", "required": False,
                         "desc": "工程文件、目录、或数组。目录下按给定顺序收取 .asproj.json"},
            "project": {"type": "path", "required": False,
                        "desc": "单个工程。和 chunk 一起用时先拆分再排队"},
            "chunk": {"type": "int", "required": False,
                      "desc": "与 project 联用：先按条数切开，再按切片排队。例 100 条 chunk=40 → 3 个工程"},
            "outdir": {"type": "path", "required": False,
                       "desc": "拆分输出目录，默认与源工程同目录"},
            "name_pattern": {"type": "string", "required": False,
                             "desc": "拆分文件名，默认 <原名>_<start>-<end>.asproj.json"},
            "sort": {"type": "string", "default": "given",
                     "desc": "projects 排序：given（保持传入顺序）/ name / mtime"},
            "stop_on_error": {"type": "bool", "default": True,
                              "desc": "某一个工程失败时是否停下。false 则继续后面的"},
            "workers": {"type": "int|null", "default": None,
                        "desc": "传给每个工程的 batch_export.workers"},
            "gpu": {"type": "bool|null", "default": None,
                    "desc": "传给每个工程的 batch_export.gpu"},
            "mode": {"type": "string", "default": "quality",
                     "desc": "传给每个工程的 batch_export.mode"},
            "auto_subtitle": {"type": "bool", "default": True,
                              "desc": "每个工程导出前先跑 auto_subtitle（一键全自动）并写回工程。拆分后还没有字幕时必须开。已有字幕的条目默认不重打轴"},
            "auto_credit": {"type": "bool", "default": False,
                            "desc": "导出前按文本预设幂等加署名（source_preset_id）。重复跑不叠加"},
            "credit_preset_id": {"type": "string", "default": "credit.default",
                                 "desc": "自动署名用的 text_preset id"},
        },
        "exit_codes": {"0": "全部成功", "1": "有工程失败", "2": "参数错误", "4": "被锁挡住"},
        "idempotent": True,
        "note": ("每个工程默认：auto_subtitle（有缺字幕才跑）→ 写回工程 → 再 spawn batch_export。"
                 "batch_export 子进程退出后再开下一个，用来释放显存/内存，对应图形界面「工程队列」的重启策略。"
                 "拆分后的工程没有字幕，不要把 auto_subtitle 设成 false。"
                 "耗时长请加 --async，再用 --status 看当前工程进度和队列剩余。"
                 "图形界面：文件 → 自动拆分工程 / 工程队列。"),
    },
    {
        "name": "batch_export",
        "summary": "按工程里的字幕把每条视频渲染成带字幕的成品，写到「字幕成品」目录",
        "in": "project",
        "out": "video[]",
        "implemented": True,
        "params": {
            "project": {"type": "path", "required": True, "desc": "工程文件"},
            "workers": {"type": "int|null", "default": None,
                        "desc": "并行进程数。null 表示按当时内存/显存/CPU 自动算"},
            "gpu": {"type": "bool|null", "default": None,
                    "desc": "是否用 NVENC。null 表示有 NVIDIA 显卡就尝试，不够再降级"},
            "mode": {"type": "string", "default": "quality",
                     "desc": "渲染质量。quality 或 fast，与界面一致"},
        },
        "exit_codes": {"0": "成功", "1": "失败", "4": "被锁挡住"},
        "idempotent": True,
        "note": ("耗时长时请加 --async，立刻返回 job id，再用 --status 查进度。"
                 "导出期间右下角会弹出独立的监视器小窗，这是正常的。"
                 "成品在每条原视频旁边的「字幕成品」文件夹。"),
    },
    {
        "name": "add_text_overlay",
        "summary": "给工程里每条视频加上一条文字覆层（署名 / Generated with AI #XY）",
        "in": "project",
        "out": "project",
        "implemented": True,
        "params": {
            "project": {"type": "path", "required": True, "desc": "工程文件 .asproj.json"},
            "preset": {"type": "string", "default": "credit.default",
                       "desc": "文本预设 id（或显示名）。读 UserData/presets/text_presets.json。默认 credit.default（署名）"},
            "content": {"type": "string", "required": False,
                        "desc": "覆盖预设文案。默认用预设里的 Generated with AI #XY"},
            "replace": {"type": "bool", "default": True,
                        "desc": "true 时先删掉同名预设的旧覆层，再写一条新的"},
            "out": {"type": "path", "required": False, "desc": "写出路径，默认覆盖原工程"},
        },
        "exit_codes": {"0": "成功", "1": "失败", "2": "参数错误"},
        "idempotent": True,
        "note": "只改工程，不渲染。要出片再跑 batch_export。需要加插图时先这一步再打开界面。",
    },
    {
        "name": "add_bgm",
        "summary": "从音频文件夹给工程里每条视频配一首够长的背景音乐（对齐界面「全部配乐」）",
        "in": "project",
        "out": "project",
        "implemented": True,
        "params": {
            "project": {"type": "path", "required": True, "desc": "工程文件 .asproj.json"},
            "dir": {"type": "path", "required": True, "desc": "音频文件夹（mp3/wav/m4a/flac/ogg/aac）"},
            "volume": {"type": "number", "default": 0.10, "desc": "线性音量，默认 0.10，与界面批量配乐一致。短于视频的音频会循环"},
            "out": {"type": "path", "required": False, "desc": "写出路径，默认覆盖原工程"},
        },
        "exit_codes": {"0": "成功", "1": "失败", "2": "参数错误"},
        "idempotent": True,
        "note": "按视频时长挑一首更长的音频，尽量不重复。只改工程，不渲染。",
    },
    {
        "name": "merge_projects",
        "summary": "把多个工程文件的视频条目合并成一个工程，每条视频自己的字幕、署名、配乐都保留",
        "in": "project[]",
        "out": "project",
        "implemented": True,
        "params": {
            "projects": {
                "type": "path|list", "required": True,
                "desc": ("工程文件、目录、或数组。"
                         "目录下收取全部 .asproj.json / .asproj。"
                         "也可写 params.dir 当目录用。"),
            },
            "out": {
                "type": "path", "required": False,
                "desc": "写出路径。默认放在第一份工程旁边，名叫 merged.asproj.json",
            },
            "sort": {
                "type": "string", "default": "name",
                "desc": "name 按文件名自然序（Tik001 在 Tik002 前）；mtime 按修改时间；given 按传入顺序",
            },
            "dedupe": {
                "type": "bool", "default": True,
                "desc": "同一视频路径只留第一次出现的那条",
            },
        },
        "exit_codes": {"0": "成功", "1": "失败", "2": "参数错误"},
        "idempotent": True,
        "note": ("不会重新打轴、不会重跑 whisper。任务里已有的 segments / text / 覆层原样并进新工程。"
                 "用界面打开即可继续加插图或导出。"),
    },
    {
        "name": "scatter_broll",
        "summary": "把短视频按固定时长切开，静音后循环铺到工程每条长视频的 Video2（或指定轨道）",
        "in": "project + dir",
        "out": "project",
        "implemented": True,
        "params": {
            "project": {"type": "path", "required": True,
                        "desc": "工程文件 .asproj.json，例如 八段视频.asproj.json"},
            "clips_dir": {"type": "path", "required": True,
                          "desc": "待散落的源视频目录。只读该目录下一层，切片写到其中 _scatter/"},
            "clips": {"type": "list", "required": False,
                      "desc": "目录和/或单个视频文件混着给，例 [\"D:/观众席\", \"D:/a/b.mp4\"]。目录只收下一层，重复路径去重"},
            "clip_sec": {"type": "number", "default": 4,
                         "desc": "每段裁剪时长（秒）。源视频最后一段不足此时长也保留"},
            "interval_sec": {"type": "number", "default": 20,
                             "desc": "工程时间线上相邻两段落点的间隔（秒）"},
            "start_sec": {"type": "number", "default": None,
                          "desc": "第一条落点。默认等于 interval_sec（开头不放，从第 20 秒起）"},
            "mute": {"type": "bool", "default": True,
                     "desc": "true 时切片去掉音频，也不写入音轨"},
            "video_track": {"type": "int", "default": 2,
                            "desc": "目标 Video 轨道编号。1 是主视频，禁止写入。默认 2"},
            "replace": {"type": "bool", "default": True,
                        "desc": "true 时先清空目标轨道再铺，并拿掉该目录素材残留音轨"},
            "out_dir": {"type": "path", "required": False,
                        "desc": "切片输出目录。默认 <clips_dir>/_scatter"},
            "out": {"type": "path", "required": False,
                    "desc": "写出工程路径。默认覆盖原工程"},
            "indices": {"type": "string|list", "required": False,
                        "desc": "只处理这些序号（1 起）。例 \"1-3,5\" 或 [1,2,5]。默认全部"},
            "videos": {"type": "list", "required": False,
                       "desc": "只处理这些文件名，如 [\"Tik001.mp4\",\"Tik003.mp4\"]"},
        },
        "exit_codes": {"0": "成功", "1": "失败", "2": "参数错误"},
        "idempotent": True,
        "note": ("先按 clip_sec 切开（末段不足也保留），再从 start_sec 起每隔 interval_sec "
                 "在每条长视频的 Video 轨道上循环铺切片。默认静音、不进音轨、不碰主视频。"
                 "只改工程，不渲染。"),
    },
    {
        "name": "insert_image",
        "summary": "把同一张图批量铺到工程每条视频的 Images 轨道，默认 1 分钟后出现、铺到片尾、铺满 9:16 画面",
        "in": "project + image",
        "out": "project",
        "implemented": True,
        "params": {
            "project": {"type": "path", "required": True,
                        "desc": "工程文件 .asproj.json"},
            "image": {"type": "path", "required": True,
                      "desc": "图片路径，如 C:\\\\Users\\\\Timco\\\\Downloads\\\\Bible_study.png"},
            "start_sec": {"type": "number|string", "default": 60,
                          "desc": "起始空余。秒数或 m:ss。此前不出现。默认 60（1 分钟）"},
            "end_pad_sec": {"type": "number|string", "default": 0,
                            "desc": "结束空余：距片尾多少秒消失。默认 0=铺到视频结尾"},
            "end_sec": {"type": "number|string", "required": False,
                        "desc": "绝对结束时间（秒或 m:ss）。若提供则覆盖 end_pad_sec"},
            "image_track": {"type": "int", "default": 1,
                            "desc": "Images 轨道编号，默认 1"},
            "fit": {"type": "string", "default": "fill",
                    "desc": "fill 铺满画面；fit 完整放下；stretch 拉伸"},
            "scale": {"type": "number", "default": 1.0,
                      "desc": "额外缩放，1=适配画面"},
            "x": {"type": "number", "default": 0, "desc": "水平偏移像素"},
            "y": {"type": "number", "default": 0, "desc": "垂直偏移像素"},
            "replace": {"type": "bool", "default": True,
                        "desc": "true 时先去掉本脚本上次插的同一张图"},
            "platform_copies": {"type": "object", "required": False,
                        "desc": "多平台副本。例 {\"youtube\":\"a.png\",\"instagram\":\"b.png\",\"tiktok\":\"c.png\"}。默认同一次渲染：字幕烧一遍再叠图。可加 \"render_mode\":\"each\" 做多次单独渲染（字幕盖住图片）。文件名前加 Youtube_ / Instagram_ / TikTok_"},
            "render_mode": {"type": "string", "default": "once",
                        "desc": "once=一次性渲染（默认，更快）；each=多次单独渲染（字幕盖住图片）。写入 platform_copies"},
            "out": {"type": "path", "required": False,
                    "desc": "写出工程路径。默认覆盖原工程"},
            "indices": {"type": "string|list", "required": False,
                        "desc": "只处理这些序号（1 起）。例 \"1-3,5\"。默认全部"},
            "videos": {"type": "list", "required": False,
                       "desc": "只处理这些文件名，如 [\"Tik001.mp4\"]"},
        },
        "exit_codes": {"0": "成功", "1": "失败", "2": "参数错误"},
        "idempotent": True,
        "note": "只改工程 image_overlays，不渲染。默认铺满 9:16 画面、从第 60 秒到片尾、Images 1。带 platform_copies 时预览仍只插一张图，导出才按平台分份。render_mode 默认 once（一次渲染叠图）；each 为每平台单独渲染。",
    },
]

RECIPES_SPEC = [
    {
        "name": "batch_subtitles",
        "summary": "一批口播素材：拼接 → 按 80 切开工程 → 打轴 → 导出字幕成品",
        "steps": ["stitch", "split_project", "auto_subtitle", "batch_export"],
        "note": "stitch 若以退出码 3 停下，先按「缺失文案清单.txt」补片段，再重跑 stitch，不要继续导出。多份工程要连续导出请改走 split_and_queue_export。",
    },
    {
        "name": "split_and_queue_export",
        "summary": "大工程按条数切开，再按工程队列逐份导出；每份结束都起一个新进程（释放显存）",
        "steps": ["split_project", "queue_run"],
        "note": "一条命令也可以：--do queue_run --params {\"project\":\"...\",\"chunk\":40}。100 条 chunk=40 → 3 个工程；每份先 auto_subtitle 再导出。",
    },
    {
        "name": "longtik_finish",
        "summary": "长文案成片：拼接 → 打轴 → 署名 → 配乐 →（可选）导出。要加插图就停在工程，不要导出",
        "steps": ["stitch", "auto_subtitle", "add_text_overlay", "add_bgm", "batch_export"],
        "note": ("署名默认预设 credit.default（署名）= Generated with AI。"
                 "最后一步 batch_export 可省略：只保存工程，再用图形界面加插图后导出。"),
    },
    {
        "name": "merge_then_edit",
        "summary": "多组成片工程合成一份，再打开界面加插图 / 导出",
        "steps": ["merge_projects"],
        "note": "merge_projects 之后用片语 Studio 打开 artifacts 里的 merged 工程。字幕已经在每条视频上。",
    },
]

def describe_payload():
    payload = {
        "app": APP_NAME,
        "version": APP_VERSION,
        "ops": OPS_SPEC,
        "recipes": RECIPES_SPEC,
    }
    from cutora.core.commands import commands_public_list
    payload["commands"] = commands_public_list()
    return payload

# ===========================================================================
#  参数解析
# ===========================================================================

def _argv_has(argv, flag):
    return flag in argv

def _argv_value(argv, flag):
    if flag not in argv:
        return None
    i = argv.index(flag)
    if i + 1 >= len(argv):
        raise CliError("缺少 %s 的参数" % flag, EXIT_USAGE)
    return argv[i + 1]

def parse_cli(argv):
    """argv 含 sys.argv[0]。返回 dict。"""
    args = list(argv[1:])
    cmd = {
        "describe": False,
        "do": None,
        "params": {},
        "params_raw": None,
        "run": None,
        "status": False,
        "status_id": None,
        "json": False,
        "async": False,
        "job_id": None,
        "help": False,
        "queue_child": False,
    }
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("-h", "--help"):
            cmd["help"] = True
            i += 1
        elif a == "--describe":
            cmd["describe"] = True
            i += 1
        elif a == "--json":
            cmd["json"] = True
            i += 1
        elif a == "--async":
            cmd["async"] = True
            i += 1
        elif a == "--do":
            if i + 1 >= len(args):
                raise CliError("--do 后面需要操作名，例如 --do stitch", EXIT_USAGE)
            cmd["do"] = args[i + 1]
            i += 2
        elif a == "--params":
            if i + 1 >= len(args):
                raise CliError("--params 后面需要 JSON 或 @文件", EXIT_USAGE)
            cmd["params_raw"] = args[i + 1]
            i += 2
        elif a == "--run":
            if i + 1 >= len(args):
                raise CliError("--run 后面需要 graph.json 路径", EXIT_USAGE)
            cmd["run"] = args[i + 1]
            i += 2
        elif a == "--status":
            cmd["status"] = True
            if i + 1 < len(args) and not args[i + 1].startswith("-"):
                cmd["status_id"] = args[i + 1]
                i += 2
            else:
                i += 1
        elif a == "--job-id":
            if i + 1 >= len(args):
                raise CliError("--job-id 后面需要 id", EXIT_USAGE)
            cmd["job_id"] = args[i + 1]
            i += 2
        elif a == "--queue-child":
            cmd["queue_child"] = True
            i += 1
        elif a == "--queue-continue":
            # 图形界面重启标志，无头解析时忽略
            i += 1
        else:
            raise CliError("未知参数: %s\n%s" % (a, usage_text()), EXIT_USAGE)
    return cmd

def usage_text():
    names = ", ".join(op["name"] for op in OPS_SPEC)
    return (
        "用法:\n"
        "  python -m cutora --describe\n"
        "  python -m cutora --do <op> --params <json|@file> [--json] [--async]\n"
        "  python -m cutora --run <graph.json> [--json]\n"
        "  python -m cutora --status [job_id]\n"
        "可用 op: %s\n" % names
    )

def parse_params_value(raw):
    if raw is None or raw == "":
        return {}
    text = raw
    if raw == "-":
        text = sys.stdin.read()
    elif raw.startswith("@"):
        path = abs_path(raw[1:])
        if not os.path.isfile(path):
            raise CliError("找不到参数文件: %s" % path, EXIT_USAGE)
        with io.open(path, "r", encoding="utf-8") as f:
            text = f.read()
    try:
        obj = json.loads(text)
    except Exception as e:
        raise CliError("--params 不是合法 JSON: %s" % e, EXIT_USAGE)
    if not isinstance(obj, dict):
        raise CliError("--params 必须是 JSON 对象", EXIT_USAGE)
    return obj

def resolve_text_arg(value):
    """captions 等字段：字面文本 / @路径 / '-'。"""
    if value is None:
        return ""
    if not isinstance(value, str):
        return str(value)
    if value == "-":
        return sys.stdin.read()
    if value.startswith("@"):
        path = abs_path(value[1:])
        if not os.path.isfile(path):
            raise CliError("找不到文本文件: %s" % path, EXIT_USAGE)
        with io.open(path, "r", encoding="utf-8") as f:
            return f.read()
    # 看起来像已存在的文件路径时也读文件（代理经常直接塞路径）
    if os.path.isfile(value) and ("\n" not in value):
        with io.open(value, "r", encoding="utf-8") as f:
            return f.read()
    return value

# ===========================================================================
#  结果 JSON
# ===========================================================================

def _state_dir(op, params):
    params = params or {}
    base = None
    if op == "stitch":
        d = params.get("out_dir") or params.get("dir")
        if d:
            d = abs_path(d)
            if params.get("out_dir"):
                base = d
            else:
                base = os.path.join(d, "_stitched")
    else:
        p = params.get("out") or params.get("project") or params.get("outdir")
        if p:
            p = abs_path(p)
            base = p if os.path.isdir(p) else os.path.dirname(p)
    if not base:
        base = os.path.join(USER_DATA)
    path = os.path.join(base, "_state")
    try:
        os.makedirs(path, exist_ok=True)
    except Exception:
        path = os.path.join(USER_DATA, "_state")
        os.makedirs(path, exist_ok=True)
    return path

def next_state_path(op, params):
    folder = _state_dir(op, params)
    existing = []
    try:
        existing = [n for n in os.listdir(folder) if n.endswith(".json")]
    except Exception:
        pass
    seq = 1
    nums = []
    for n in existing:
        try:
            nums.append(int(n.split("_", 1)[0]))
        except Exception:
            pass
    if nums:
        seq = max(nums) + 1
    return os.path.join(folder, "%02d_%s.json" % (seq, op))

def write_result(op, params, payload):
    path = next_state_path(op, params)
    try:
        atomic_write_json(path, payload)
        payload.setdefault("result_path", abs_path(path))
        # 再写一次带 result_path 的
        atomic_write_json(path, payload)
    except Exception as e:
        eprint("[CLI] 结果 JSON 写入失败: %s" % e)
    return path

# ===========================================================================
#  轻量入口（主模块加载之前）
# ===========================================================================

def early_dispatch(argv):
    """
    从 python -m cutora 入口调用。
    返回 int 退出码表示已经处理完；返回 None 表示继续加载主模块。
    """
    if "--render-job" in argv:
        return None
    from cutora.core.command_cli import dispatch_early, is_commands_argv
    if is_commands_argv(argv):
        code = dispatch_early(argv)
        if code is not None:
            return code
        return None
    # 先把 QuickEdit 关掉，避免终端一点就卡住
    _disable_quickedit()

    light = ("--describe" in argv) or (
        "--status" in argv and "--do" not in argv and "--run" not in argv
    )
    wants_json = "--json" in argv or "--describe" in argv or (
        "--status" in argv and "--do" not in argv
    )
    if wants_json or "--do" in argv or "--run" in argv:
        if "--describe" in argv or "--status" in argv or "--json" in argv:
            begin_json_stdio()

    if "--do" in argv or "--run" in argv:
        # 重量命令：先把 stdout 隔离（模块级 print 一律进 stderr），再继续加载
        begin_json_stdio()
        try:
            cmd = parse_cli(argv)
        except CliError as e:
            eprint(e.message)
            if e.code == EXIT_USAGE:
                eprint(usage_text())
            return e.code
        if cmd["do"] and cmd["do"] not in OP_HANDLERS:
            names = ", ".join(op["name"] for op in OPS_SPEC)
            eprint("未知操作 %r。可用: %s" % (cmd["do"], names))
            eprint(usage_text())
            return EXIT_USAGE
        if cmd["params_raw"] is not None:
            try:
                parse_params_value(cmd["params_raw"])
            except CliError as e:
                eprint(e.message)
                return e.code
        return None

    if not light:
        return None

    try:
        cmd = parse_cli(argv)
    except CliError as e:
        eprint(e.message)
        if e.code == EXIT_USAGE:
            eprint(usage_text())
        return e.code
    except Exception as e:
        eprint("[CLI] 参数解析失败: %s" % e)
        return EXIT_USAGE

    if cmd["help"] and not cmd["describe"] and not cmd["status"]:
        eprint(usage_text())
        return EXIT_USAGE

    if cmd["describe"]:
        emit_json(describe_payload())
        return EXIT_OK

    if cmd["status"]:
        emit_json(cmd_status(cmd["status_id"]))
        return EXIT_OK

    return None

# ===========================================================================
#  重量入口（主模块加载完毕）
# ===========================================================================

def late_dispatch(argv):
    started = now_iso()
    cmd = None
    op_name = None
    params = {}
    try:
        _disable_quickedit()
        from cutora.core.runtime import init_torch_runtime
        init_torch_runtime(render_child=False)
        cmd = parse_cli(argv)
        if cmd["json"]:
            begin_json_stdio()
        if cmd["help"] and not cmd["do"] and not cmd["run"]:
            eprint(usage_text())
            return EXIT_USAGE
        if cmd["run"]:
            return run_graph(cmd)
        if not cmd["do"]:
            raise CliError("缺少 --do 或 --run\n" + usage_text(), EXIT_USAGE)
        op_name = cmd["do"]
        params = parse_params_value(cmd["params_raw"])
        if op_name not in OP_HANDLERS:
            names = ", ".join(op["name"] for op in OPS_SPEC)
            raise CliError("未知操作 %r。可用: %s" % (op_name, names), EXIT_USAGE)

        if cmd["async"]:
            if op_name not in ("batch_export", "queue_run"):
                raise CliError("--async 目前只支持 batch_export / queue_run", EXIT_USAGE)
            return spawn_async(op_name, params, cmd)

        skip_lock = bool(cmd.get("queue_child"))
        if not skip_lock:
            holder = acquire_lock(op_name)
            if holder is not None:
                msg = lock_blocked_message(holder)
                eprint(msg)
                payload = {
                    "op": op_name, "ok": False, "started_at": started,
                    "finished_at": now_iso(), "counts": {}, "artifacts": [],
                    "next_hint": "等占用进程结束后再跑", "human_needed": False,
                    "error": msg,
                }
                if cmd["json"]:
                    emit_json(payload)
                return EXIT_LOCKED

        try:
            ensure_qapp()
            _cli_autosave_begin(params)
            result = OP_HANDLERS[op_name](params, cmd)
            if result.get("ok", True):
                _cli_autosave_end(params)
        finally:
            if not skip_lock:
                release_lock_if_owner()

        result.setdefault("op", op_name)
        result.setdefault("started_at", started)
        result.setdefault("finished_at", now_iso())
        result.setdefault("ok", True)
        result.setdefault("counts", {})
        result.setdefault("artifacts", [])
        result.setdefault("human_needed", False)
        result.setdefault("error", None)
        write_result(op_name, params, result)
        if cmd["json"]:
            emit_json(result)
        else:
            _print_human(result)
        return EXIT_OK if result.get("ok", True) else EXIT_FAIL

    except GateError as e:
        release_lock_if_owner()
        payload = {
            "op": op_name or (cmd or {}).get("do") or "unknown",
            "ok": False,
            "started_at": started,
            "finished_at": now_iso(),
            "counts": e.extra.get("counts") or {},
            "artifacts": e.extra.get("artifacts") or [],
            "next_hint": e.extra.get("next_hint") or "处理闸门提示后重跑",
            "human_needed": True,
            "error": e.message,
        }
        for k, v in e.extra.items():
            if k not in payload:
                payload[k] = v
        try:
            write_result(payload["op"], params, payload)
        except Exception:
            pass
        eprint(e.message)
        if (cmd or {}).get("json") or _STDOUT_REDIRECTED:
            emit_json(payload)
        return EXIT_GATE
    except CliError as e:
        release_lock_if_owner()
        eprint(e.message)
        if e.code == EXIT_USAGE:
            eprint(usage_text())
        payload = {
            "op": op_name or "unknown",
            "ok": False,
            "started_at": started,
            "finished_at": now_iso(),
            "counts": {},
            "artifacts": [],
            "next_hint": None,
            "human_needed": False,
            "error": e.message,
        }
        payload.update(e.extra)
        if (cmd or {}).get("json") or _STDOUT_REDIRECTED:
            emit_json(payload)
        return e.code
    except Exception as e:
        release_lock_if_owner()
        traceback.print_exc(file=sys.stderr)
        payload = {
            "op": op_name or "unknown",
            "ok": False,
            "started_at": started,
            "finished_at": now_iso(),
            "counts": {},
            "artifacts": [],
            "next_hint": None,
            "human_needed": False,
            "error": str(e),
        }
        if (cmd or {}).get("json") or _STDOUT_REDIRECTED:
            emit_json(payload)
        return EXIT_FAIL

def _print_human(result):
    ok = "OK" if result.get("ok") else "FAIL"
    eprint("[%s] %s" % (ok, result.get("op")))
    if result.get("error"):
        eprint("  error: %s" % result["error"])
    counts = result.get("counts") or {}
    if counts:
        eprint("  counts: %s" % counts)
    for p in result.get("artifacts") or []:
        eprint("  artifact: %s" % p)
    if result.get("next_hint"):
        eprint("  next: %s" % result["next_hint"])

def run_graph(cmd):
    path = abs_path(cmd["run"])
    if not os.path.isfile(path):
        raise CliError("找不到 graph 文件: %s" % path, EXIT_USAGE)
    data = load_json_file(path)
    if isinstance(data, list):
        steps = data
    elif isinstance(data, dict):
        steps = data.get("ops") or data.get("steps") or []
    else:
        raise CliError("graph.json 必须是对象或数组", EXIT_USAGE)
    if not steps:
        raise CliError("graph.json 里没有 ops", EXIT_USAGE)

    holder = acquire_lock("run")
    if holder is not None:
        eprint(lock_blocked_message(holder))
        return EXIT_LOCKED
    started = now_iso()
    results = []
    code = EXIT_OK
    try:
        ensure_qapp()
        for i, step in enumerate(steps):
            if not isinstance(step, dict):
                raise CliError("第 %d 步不是对象" % (i + 1), EXIT_USAGE)
            op_name = step.get("op") or step.get("name")
            params = step.get("params") or {}
            if op_name not in OP_HANDLERS:
                raise CliError("第 %d 步未知操作 %r" % (i + 1, op_name), EXIT_USAGE)
            eprint("[run] (%d/%d) %s" % (i + 1, len(steps), op_name))
            try:
                result = OP_HANDLERS[op_name](params, cmd)
                result.setdefault("op", op_name)
                result.setdefault("ok", True)
                write_result(op_name, params, result)
                results.append(result)
                if not result.get("ok", True):
                    code = EXIT_FAIL
                    break
            except GateError as e:
                payload = {
                    "op": op_name, "ok": False, "error": e.message,
                    "human_needed": True,
                }
                payload.update(e.extra)
                write_result(op_name, params, payload)
                results.append(payload)
                eprint(e.message)
                code = EXIT_GATE
                break
    finally:
        release_lock_if_owner()

    summary = {
        "op": "run",
        "ok": code == EXIT_OK,
        "started_at": started,
        "finished_at": now_iso(),
        "counts": {"steps": len(results)},
        "artifacts": [],
        "next_hint": None,
        "human_needed": any(r.get("human_needed") for r in results),
        "error": None if code == EXIT_OK else "graph 在第 %d 步停下" % len(results),
        "steps": results,
    }
    if cmd.get("json"):
        emit_json(summary)
    else:
        _print_human(summary)
    return code

# ===========================================================================
#  宿主 / Qt
# ===========================================================================

_APP = None

def prepare_runtime():
    """工作目录和 PATH 都要能找到 runtime/ffmpeg。

    AlignmentWorker 走 whisperx.load_audio()，它直接 spawn「ffmpeg」，
    不读 config.FFMPEG_PATH。cwd / PATH 不对时就是 WinError 2。
    """
    try:
        os.chdir(REPO_ROOT)
    except Exception:
        pass
    path = os.environ.get("PATH") or ""
    parts = path.split(os.pathsep)
    if os.path.normcase(FFMPEG_DIR) not in [os.path.normcase(p) for p in parts]:
        os.environ["PATH"] = FFMPEG_DIR + os.pathsep + path

def ensure_qapp():
    global _APP
    prepare_runtime()
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance()
    if app is None:
        _APP = QApplication([])
        app = _APP
    from cutora.ui import main_window as host
    if not os.path.isdir(host.FONT_DIR) or not host._iter_font_files():
        raise CliError("字体目录不可用: %s" % getattr(host, "FONT_DIR", "?"), EXIT_FAIL)
    if not host.load_embedded_fonts():
        raise CliError("字体加载失败", EXIT_FAIL)
    try:
        app.setFont(host.get_font_manager().get_font(host.DEFAULT_FONT_FAMILY, 10))
    except Exception:
        pass
    return app

def _connect_once(signal, bucket):
    signal.connect(lambda *a: bucket.append(a if len(a) != 1 else a[0]))

def _as_bool(v, default=False):
    if v is None:
        return default
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    s = str(v).strip().lower()
    if s in ("1", "true", "yes", "y", "on"):
        return True
    if s in ("0", "false", "no", "n", "off", ""):
        return False
    return default

# ===========================================================================
#  工程读写（对齐 serialize_render_task，不弹窗）
# ===========================================================================

def load_default_style(host):
    try:
        from cutora.subtitle.stylepack import default_pack_style
        loaded = default_pack_style()
        if loaded:
            return host.force_allowed_font(__import__("copy").deepcopy(loaded))
    except Exception as e:
        eprint("[CLI] 读取默认字幕预设失败，用内置默认: %s" % e)
    return host.force_allowed_font({
        "pos_x": 540, "pos_y": 1450, "fontsize": 60,
        "font_family": getattr(host, "DEFAULT_FONT_FAMILY", "Arimo"),
        "bold": True, "italic": False,
        "color_text": "#000000", "color_high": "#FF0000",
        "color_bg": "#e0c3fc", "color_shadow": "#000000",
        "opacity_bg": 191, "use_box": True,
        "box_padding_x": 20, "box_padding_y": 20, "box_radius": 20,
        "wrap_lines": 2,
    })

def make_task_from_video(host, video_path, text=""):
    """对齐 MainWindow.on_drop 的核心字段，不碰 file_list / 波形 / 播放器。"""
    import cv2
    from cutora.model.timeline import Timeline as _TL
    from cutora.model.timeline_model import MediaSource, Clip, Track

    path = abs_path(video_path)
    tpl_w, tpl_h = 1080, 1920
    fps = float(getattr(host, "DEFAULT_TIMELINE_FPS", 30.0) or 30.0)
    dims = (tpl_w, tpl_h)
    try:
        cap = host._open_video_capture(path) or cv2.VideoCapture(path)
        if cap is not None and cap.isOpened():
            w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            if w > 0 and h > 0:
                dims = (w, h)
            cap.release()
    except Exception:
        pass

    style = copy.deepcopy(load_default_style(host))
    style["pos_x"] = dims[0] // 2
    style["pos_y"] = int(dims[1] * 0.755)
    scale = (dims[0] / float(tpl_w) + dims[1] / float(tpl_h)) / 2.0
    if abs(scale - 1.0) > 0.01:
        for fk in ("fontsize", "border_width", "box_padding_x", "box_padding_y",
                   "box_radius", "high_padding", "shadow_blur",
                   "adv_stroke_width", "adv_bg_radius"):
            if fk in style and isinstance(style[fk], (int, float)):
                style[fk] = max(1, round(float(style[fk]) * scale))

    timeline = _TL(dims[0], dims[1], fps)
    source = MediaSource(path)
    try:
        source.has_audio = bool(host.has_audio_stream(path))
    except Exception:
        source.has_audio = True
    timeline.sources[source.id] = source
    shared_link_id = uuid.uuid4().hex
    fit_mode = getattr(host, "DEFAULT_VIDEO_FIT_MODE", "fill")

    video_track = host.get_or_create_track(timeline, "video")
    video_track.is_main_video = True
    video_clip = Clip(source.id, 0.0, float(source.duration or 0.0), 0.0)
    video_clip.link_group_id = shared_link_id
    video_clip.fit_mode = fit_mode
    video_clip.x = 0.0
    video_clip.y = 0.0
    video_clip.scale = 1.0
    video_clip.rotation = 0.0
    video_track.clips.append(video_clip)
    host.get_or_create_track(timeline, "subs")

    audio_track1 = host.get_or_create_track(timeline, "audio")
    audio_track1.audio_role = "main"
    audio_track1.name = "原声"
    if source.has_audio:
        audio_clip = Clip(source.id, 0.0, float(source.duration or 0.0), 0.0)
        audio_clip.link_group_id = shared_link_id
        audio_clip.is_main = True
        audio_clip.audio_role = "main"
        audio_track1.clips.append(audio_clip)

    duration = float(source.duration or 0.0)
    task = {
        "video": path,
        "path": path,
        "text": text or "",
        "segments": [],
        "overlays": [],
        "text_overlays": [],
        "image_overlays": [],
        "audio_clips": [],
        "dims": dims,
        "style": style,
        "src_volume": 1.0,
        "intro_media": None,
        "timeline": timeline,
        "video_overlay": {},
        "audio_tracks": [],
        "duration": duration,
        "trim_end_sec": duration,
        "subtitle_roles": {
            "default": {
                "name": "默认",
                "style": copy.deepcopy(style),
                "color": "#CCCCCC",
            }
        },
    }
    from cutora.subtitle.stylepack import ensure_default_pack
    ensure_default_pack(task)
    return task

def serialize_project_data(host, tasks, curr_idx=-1):
    from cutora.core.history import EXPORT_VIDEO_MODE
    return {
        "version": getattr(host, "PROJECT_VERSION", 2),
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "app": "CUTORA",
        "export_video_mode": EXPORT_VIDEO_MODE,
        "tasks": [host.serialize_render_task(t) for t in (tasks or [])],
        "curr_idx": int(curr_idx if curr_idx is not None else -1),
        "ui": {"timeline_zoom": 20, "timeline_scroll_x": 0, "timeline_scroll_y": 0},
        "headless": True,
    }

def write_project_file(host, path, tasks, curr_idx=-1):
    path = abs_path(path)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    data = serialize_project_data(host, tasks, curr_idx)
    atomic_write_json(path, data)
    return path

def _cli_project_param(params: dict) -> str | None:
    if not isinstance(params, dict):
        return None
    for key in ("project", "src", "path"):
        p = params.get(key)
        if isinstance(p, str) and p and os.path.isfile(p):
            return abs_path(p)
    return None


def _cli_autosave_begin(params: dict) -> None:
    path = _cli_project_param(params)
    if not path:
        return
    try:
        from cutora.core.autosave import write_snapshot
        data = load_json_file(path)
        if isinstance(data, dict):
            write_snapshot(path, data)
    except Exception:
        pass


def _cli_autosave_end(params: dict) -> None:
    path = _cli_project_param(params)
    if not path:
        return
    try:
        from cutora.core.autosave import clear_snapshot
        clear_snapshot(path)
    except Exception:
        pass


def load_project_file(host, path):
    path = abs_path(path)
    if not os.path.isfile(path):
        raise CliError("找不到工程文件: %s" % path, EXIT_FAIL)
    data = load_json_file(path)
    if not isinstance(data, dict):
        raise CliError("工程文件格式不对: %s" % path, EXIT_FAIL)
    file_ver = data.get("version", 1)
    vmin = getattr(host, "PROJECT_VERSION_MIN", 1)
    vmax = getattr(host, "PROJECT_VERSION", 2)
    if not (vmin <= file_ver <= vmax):
        raise CliError("项目版本 v%s 不受支持（需要 v%s~v%s）" % (file_ver, vmin, vmax), EXIT_FAIL)
    try:
        host._normalize_project_data(data)
    except Exception:
        pass
    tasks = []
    for t in data.get("tasks") or []:
        task = host.deserialize_render_task(t)
        if not task.get("video"):
            task["video"] = task.get("path") or ""
        if not task.get("path"):
            task["path"] = task.get("video") or ""
        if task.get("video"):
            task["video"] = abs_path(task["video"])
            task["path"] = abs_path(task.get("path") or task["video"])
        try:
            from cutora.render.visual_stack import migrate_old_order
            migrate_old_order(task)          # Q163：老工程打开后画面和以前一样
        except Exception:
            pass
        tasks.append(task)
    from cutora.model.offline import inspect_task, list_offline
    for task in tasks:
        inspect_task(task)
    missing = list_offline(tasks)
    if missing:
        for p in missing:
            _log.error("offline media: %s", p)
        raise CliError(
            "工程有 %d 个离线素材:\n%s" % (len(missing), "\n".join(missing)),
            EXIT_FAIL,
        )
    return data, tasks

def list_videos_in(spec):
    if spec is None:
        raise CliError("缺少 videos / dir", EXIT_USAGE)
    paths = []
    if isinstance(spec, (list, tuple)):
        for p in spec:
            ap = abs_path(p)
            if os.path.isdir(ap):
                paths.extend(_list_dir_videos(ap))
            elif os.path.isfile(ap):
                paths.append(ap)
            else:
                raise CliError("找不到视频: %s" % ap, EXIT_FAIL)
    else:
        ap = abs_path(spec)
        if os.path.isdir(ap):
            paths.extend(_list_dir_videos(ap))
        elif os.path.isfile(ap):
            paths.append(ap)
        else:
            raise CliError("找不到视频或目录: %s" % ap, EXIT_FAIL)
    # 去重保序
    seen = set()
    out = []
    for p in paths:
        k = os.path.normcase(os.path.abspath(p))
        if k in seen:
            continue
        seen.add(k)
        out.append(os.path.abspath(p))
    if not out:
        raise CliError("没有找到视频文件", EXIT_FAIL)
    return out

def _list_dir_videos(folder):
    names = []
    for n in os.listdir(folder):
        ext = os.path.splitext(n)[1].lower()
        if ext in VIDEO_EXTS:
            names.append(n)

    def key(n):
        stem = os.path.splitext(n)[0]
        try:
            return (0, int(stem), n.lower())
        except Exception:
            return (1, n.lower())

    names.sort(key=key)
    return [os.path.join(folder, n) for n in names]

def group_key_from_stem(stem):
    import re
    m = re.match(r"^0*(\d+)", stem.strip())
    if m:
        return m.group(1)
    return stem

# ===========================================================================
#  op: stitch
# ===========================================================================

def op_stitch(params, cmd):
    from cutora.ui import main_window as host
    from cutora.batch import script_stitcher as script_stitcher

    folder = params.get("dir")
    if not folder:
        raise CliError("stitch 需要 params.dir", EXIT_USAGE)
    folder = abs_path(folder)
    if not os.path.isdir(folder):
        raise CliError("不是一个目录: %s" % folder, EXIT_FAIL)
    if "captions" not in params or not params.get("captions"):
        try:
            if r"D:\0_LTX" not in sys.path:
                sys.path.insert(0, r"D:\0_LTX")
            from pathlib import Path as _Path
            from core.table_sync import discover_orante_batch_tasks
            _orante_tasks = discover_orante_batch_tasks(_Path(folder))
            if _orante_tasks:
                params["captions"] = "\n".join(
                    f"{t.name}|{t.copy_text}" for t in _orante_tasks if t.copy_text
                )
                eprint("[stitch] ⚡ 已自动从 Orante 任务表直读 %d 条任务文案" % len(_orante_tasks))
        except Exception:
            pass
    if "captions" not in params:
        raise CliError("stitch 需要 params.captions（且未能从目录自动识别 Orante 任务表）", EXIT_USAGE)
    captions = resolve_text_arg(params.get("captions"))
    language = params.get("language") or "en"
    out_dir = abs_path(params["out_dir"]) if params.get("out_dir") else os.path.join(folder, "_stitched")
    allow_blocking = _as_bool(params.get("allow_blocking"), False)
    quarantine = _as_bool(params.get("quarantine"), True)
    do_write_project = _as_bool(params.get("write_project"), True)
    allow_missing = _as_bool(params.get("allow_missing"), False)
    do_analyze = _as_bool(params.get("analyze"), True)
    do_encode = _as_bool(params.get("encode"), True)
    ignore_scripture_refs = _as_bool(params.get("ignore_scripture_refs"), True)
    if do_encode:
        do_analyze = True

    os.makedirs(out_dir, exist_ok=True)
    # 命名规则可选: params.group_mark / params.order_mark, 例如 "Tik00x" + "v0x"。
    # 都不传 = 自动识别, 行为与补丁前一致。
    # group_by_folder：一个文件夹算一组, 文件名和前缀规则都不看。
    _gmark = str(params.get("group_mark") or "")
    _omark = str(params.get("order_mark") or "")
    _by_folder = _as_bool(params.get("group_by_folder"), False)

    def _scan_input():
        if _by_folder:
            grouped, _fo, _bo, scan_notes = script_stitcher.scan_groups_multi(
                [folder], _gmark, _omark, group_by_folder=True)
            for line in scan_notes:
                eprint("[stitch] " + line)
            return grouped
        return script_stitcher.scan_groups(folder, _gmark, _omark)

    groups = _scan_input()
    if not groups:
        if _by_folder:
            raise CliError(
                "目录下一层没有视频。以文件夹为一组时，视频要直接放在这个目录里"
                "（_stitched / _scatter 里的不算）: %s" % folder,
                EXIT_FAIL)
        raise CliError("目录里没有按组命名的视频（例如 01.mp4 / 01 (2).mp4 / Tik001_v01.mp4）；"
                       "命名特殊时可传 params.group_mark / params.order_mark: %s" % folder,
                       EXIT_FAIL)

    scripts, err = script_stitcher.parse_scripts(captions, list(groups.keys()))
    if err:
        raise CliError("文案解析失败: %s" % err, EXIT_FAIL)
    for _l in getattr(script_stitcher, "_WENAN_LAST", []):
        eprint("[stitch] " + _l)

    eprint("[stitch] 素材 %d 组 / %d 个片段，开始体检" % (
        len(groups), sum(len(v) for v in groups.values())))

    pf_bucket = []
    pf = script_stitcher.PreflightWorker(folder, groups, quarantine_bad=quarantine)
    pf.log.connect(lambda s: eprint(s))
    pf.done.connect(lambda r: pf_bucket.append(r))
    pf.run()
    if not pf_bucket:
        raise CliError("素材体检没有返回结果", EXIT_FAIL)
    rep = pf_bucket[0]
    eprint(rep.text())

    artifacts = []
    inv_txt = os.path.join(folder, "_invalid", "preflight_report.txt")
    if os.path.isfile(inv_txt):
        artifacts.append(abs_path(inv_txt))
    for src, dst in (getattr(rep, "quarantined", None) or []):
        artifacts.append(abs_path(dst))

    if getattr(rep, "fatal", None):
        extra = {
            "counts": {
                "groups": len(groups),
                "clips": sum(len(v) for v in groups.values()),
                "fatal": len(rep.fatal),
                "blocking": len(rep.blocking or []),
            },
            "artifacts": artifacts,
            "next_hint": "按 _invalid/preflight_report.txt 修好坏文件后再跑 stitch",
        }
        raise CliError("素材体检未通过（致命）:\n%s" % rep.text(), EXIT_FAIL, extra)

    if (rep.blocking or []) and not allow_blocking:
        extra = {
            "counts": {
                "groups": len(groups),
                "clips": sum(len(v) for v in groups.values()),
                "blocking": len(rep.blocking),
            },
            "artifacts": artifacts,
            "next_hint": "确认这些都是有意为之后，加 \"allow_blocking\": true 重跑",
        }
        raise GateError("素材体检需要确认（断号/无音轨/组内规格不一致）。"
                        "默认不继续。params.allow_blocking=true 可放行。\n" + rep.text(), extra)

    if getattr(rep, "quarantined", None):
        groups = _scan_input()
        if not groups:
            raise CliError("隔离坏文件之后，目录里没有可用素材了", EXIT_FAIL)
        scripts, err = script_stitcher.parse_scripts(captions, list(groups.keys()))
        if err:
            raise CliError("隔离后文案与剩余组对不上: %s" % err, EXIT_FAIL)

    if not do_analyze:
        eprint("[stitch] analyze=false，只做体检，不分析不编码")
        return {
            "ok": True,
            "counts": {
                "groups": len(groups),
                "clips": sum(len(v) for v in groups.values()),
                "blocking": len(rep.blocking or []),
            },
            "artifacts": [a for a in artifacts if a],
            "next_hint": "体检通过。下一步 --do stitch（会分析并编码）",
            "human_needed": False,
            "error": None,
            "preflight_only": True,
            "out_dir": abs_path(out_dir),
        }

    cuts = {}
    err_list = []
    aw = script_stitcher.AnalyzeWorker(
        groups, scripts, host.get_whisperx_model, host.get_align_model,
        language=language, out_dir=out_dir,
        ignore_scripture_refs=ignore_scripture_refs)
    aw.log.connect(lambda s: eprint(s))
    aw.progress.connect(lambda p: eprint("[progress] analyze %d" % int(p)))
    aw.analyzed.connect(lambda d: cuts.update(d or {}))
    aw.failed.connect(lambda m: err_list.append(m))
    aw.run()
    if err_list:
        raise CliError("分析失败: %s" % err_list[0], EXIT_FAIL)

    missing_map = getattr(aw, "missing_map", None) or {}
    missing_path = os.path.join(out_dir, "缺失文案清单.txt")
    if missing_map:
        try:
            if r"D:\0_LTX" not in sys.path:
                sys.path.insert(0, r"D:\0_LTX")
            from pathlib import Path as _Path
            from core.spine_db import SpineDB
            _spine = SpineDB()
            for _gk, _mv in missing_map.items():
                _ents = (_mv.get("missing") if isinstance(_mv, dict) else _mv) or []
                _spine.report_stitch_gaps_to_orante(
                    task_id=str(_gk).split("-")[0],
                    task_dir=_Path(folder),
                    missing_entries=[e if isinstance(e, dict) else {"text": str(e)} for e in _ents],
                )
            eprint("[stitch] ⚡ 已通过片语工作室信号总线将缺片清单实时推送至 Orante 制片台")
        except Exception as _ge:
            eprint("[stitch] 缺片信号推送提示: %s" % _ge)
    counts = {
        "groups": len(groups),
        "clips": sum(len(v) for v in groups.values()),
        "missing_groups": len(missing_map),
        "missing_spans": sum(len(v.get("missing") or []) for v in missing_map.values()),
    }
    if missing_map and not allow_missing:
        arts = list(artifacts)
        if os.path.isfile(missing_path):
            arts.append(abs_path(missing_path))
        extra = {
            "counts": counts,
            "artifacts": arts,
            "next_hint": "按缺失文案清单补片段到素材目录后，用同一条命令重跑 stitch",
            "missing_groups": sorted(
                missing_map.keys(),
                key=lambda x: int(x) if str(x).isdigit() else str(x)),
        }
        raise GateError("有 %d 组文案现有片段没讲到，已写出清单，未编码。"
                        % len(missing_map), extra)
    if missing_map and allow_missing:
        eprint("[stitch] 有 %d 组缺失文案，allow_missing=true，仍编码（与 GUI 一致）"
               % len(missing_map))

    if not do_encode:
        eprint("[stitch] encode=false，分析完成，不编码")
        arts = list(artifacts)
        if os.path.isfile(missing_path):
            arts.append(abs_path(missing_path))
        return {
            "ok": True,
            "counts": counts,
            "artifacts": [a for a in arts if a],
            "next_hint": "分析完成未编码。确认后重跑 stitch（encode 默认 true）",
            "human_needed": bool(missing_map),
            "error": None,
            "analyze_only": True,
            "out_dir": abs_path(out_dir),
        }

    from cutora.batch.timeline_stitch import (
        placements_from_cutlist, project_from_placements, write_placements,
    )
    placements = placements_from_cutlist(cuts)
    write_placements(out_dir, placements)
    text = "\n\n".join(str(scripts.get(g, "") or "") for g in cuts)
    project_path = os.path.join(out_dir, "stitched.asproj.json")
    project_from_placements(placements, project_path, text=text)
    artifacts.append(abs_path(project_path))
    eprint("[stitch] 时间线 %d 个片段（未生成中间 mp4）: %s"
           % (len(placements), project_path))

    return {
        "ok": True,
        "counts": {
            "groups": len(groups),
            "clips": sum(len(v) for v in groups.values()),
            "encoded": len(placements),
            "missing_groups": 0,
        },
        "artifacts": [a for a in artifacts if a],
        "next_hint": "下一步可 auto_subtitle 或打开工程。拼接结果是时间线，没有中间 mp4。",
        "human_needed": False,
        "error": None,
        "project": abs_path(project_path),
        "mp4": None,
        "out_dir": abs_path(out_dir),
    }

# ===========================================================================
#  op: import_media / save_project / split_project
# ===========================================================================

def op_import_media(params, cmd):
    from cutora.ui import main_window as host
    spec = params.get("videos") or params.get("dir")
    videos = list_videos_in(spec)
    captions_text = resolve_text_arg(params["captions"]) if params.get("captions") else ""
    scripts = {}
    if captions_text:
        from cutora.batch import script_stitcher as script_stitcher
        keys = [group_key_from_stem(os.path.splitext(os.path.basename(p))[0]) for p in videos]
        # parse_scripts 要的是组号列表（唯一、顺序）
        uniq = []
        seen = set()
        for k in keys:
            if k not in seen:
                uniq.append(k)
                seen.add(k)
        parsed, err = script_stitcher.parse_scripts(captions_text, uniq)
        if err:
            raise CliError("文案解析失败: %s" % err, EXIT_FAIL)
        scripts = parsed or {}

    tasks = []
    for p in videos:
        key = group_key_from_stem(os.path.splitext(os.path.basename(p))[0])
        txt = scripts.get(key, "")
        tasks.append(make_task_from_video(host, p, text=txt))

    if params.get("out"):
        out = abs_path(params["out"])
    else:
        root = os.path.dirname(videos[0])
        out = os.path.join(root, "imported.asproj.json")
    write_project_file(host, out, tasks, curr_idx=0 if tasks else -1)
    return {
        "ok": True,
        "counts": {"videos": len(videos), "with_text": sum(1 for t in tasks if t.get("text"))},
        "artifacts": [abs_path(out)],
        "next_hint": "下一步 auto_subtitle --params {\"project\": \"%s\"}" % abs_path(out).replace("\\", "\\\\"),
        "project": abs_path(out),
    }

def op_save_project(params, cmd):
    from cutora.ui import main_window as host
    src = params.get("project")
    if not src:
        raise CliError("save_project 需要 params.project", EXIT_USAGE)
    data, tasks = load_project_file(host, src)
    out = abs_path(params["out"]) if params.get("out") else abs_path(src)
    write_project_file(host, out, tasks, curr_idx=data.get("curr_idx", -1))
    return {
        "ok": True,
        "counts": {"tasks": len(tasks)},
        "artifacts": [abs_path(out)],
        "project": abs_path(out),
    }

def op_split_project(params, cmd):
    from cutora.ui import main_window as host
    src = params.get("project")
    if not src:
        raise CliError("split_project 需要 params.project", EXIT_USAGE)
    data, tasks = load_project_file(host, src)
    try:
        chunk = int(params.get("chunk") or 80)
    except Exception:
        raise CliError("chunk 必须是整数", EXIT_USAGE)
    src_abs = abs_path(src)
    outdir = abs_path(params["outdir"]) if params.get("outdir") else os.path.dirname(src_abs)
    stem = split_stem_from_path(src_abs)
    artifacts, plan = split_tasks_to_projects(
        host, tasks, chunk, outdir, stem, params.get("name_pattern"))
    return {
        "ok": True,
        "counts": {
            "source_tasks": len(tasks),
            "chunk": chunk,
            "parts": len(plan),
            "sizes": [p["count"] for p in plan],
        },
        "artifacts": artifacts,
        "plan": plan,
        "next_hint": "对每个切片工程分别跑 auto_subtitle，或 queue_run 按队列连续导出",
    }

def _parse_json_from_stdout(text):
    text = (text or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        pass
    start = text.rfind("{")
    if start >= 0:
        try:
            return json.loads(text[start:])
        except Exception:
            return None
    return None

def project_needs_subtitle(path):
    """工程里是否还有「有文案但还没有字幕分段」的条目。"""
    path = abs_path(path)
    if not path or not os.path.isfile(path):
        return False
    try:
        data = load_json_file(path)
    except Exception:
        return False
    for t in data.get("tasks") or []:
        if not isinstance(t, dict):
            continue
        text = str(t.get("text") or "").strip()
        if text and not t.get("segments"):
            return True
    return False

def _run_do_child(op_name, project, extra_params=None):
    """新进程跑一次 --do <op>，等它退出。"""
    child_params = {"project": abs_path(project)}
    if extra_params:
        for k, v in extra_params.items():
            if v is not None:
                child_params[k] = v
    os.makedirs(JOBS_DIR, exist_ok=True)
    params_path = os.path.join(JOBS_DIR, "queue-child-%s.params.json" % uuid.uuid4().hex[:8])
    atomic_write_json(params_path, child_params)
    argv = [
        sys.executable, "-u", "-m", "cutora",
        "--do", op_name,
        "--params", "@" + params_path,
        "--json",
        "--queue-child",
    ]
    import subprocess
    eprint("[Queue] 开始 %s %s" % (op_name, project))
    proc = _no_window_popen(
        argv,
        cwd=REPO_ROOT,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=sys.stderr,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    out, _ = proc.communicate()
    payload = _parse_json_from_stdout(out)
    try:
        os.remove(params_path)
    except Exception:
        pass
    return int(proc.returncode or 0), payload

def _run_batch_export_child(project, params):
    extra = {}
    for k in ("workers", "gpu", "mode"):
        if k in params and params.get(k) is not None:
            extra[k] = params[k]
    return _run_do_child("batch_export", project, extra)

def op_queue_run(params, cmd):
    projects = []
    artifacts = []
    chunk = params.get("chunk")
    if params.get("project") and chunk:
        split_res = op_split_project({
            "project": params["project"],
            "chunk": chunk,
            "outdir": params.get("outdir"),
            "name_pattern": params.get("name_pattern"),
        }, cmd)
        artifacts = list(split_res.get("artifacts") or [])
        projects = list(artifacts)
    elif params.get("projects"):
        sort = params.get("sort") or "given"
        projects = collect_asproj_paths(params["projects"], sort=sort)
    elif params.get("project"):
        p = abs_path(params["project"])
        if not os.path.isfile(p):
            raise CliError("找不到工程文件: %s" % p, EXIT_FAIL)
        projects = [p]
    else:
        raise CliError(
            "queue_run 需要 params.projects，或 params.project（可加 chunk 先拆分）",
            EXIT_USAGE)

    if not projects:
        raise CliError("队列里没有工程", EXIT_FAIL)

    auto_sub = True if params.get("auto_subtitle") is None else _as_bool(params.get("auto_subtitle"), True)
    auto_credit = _as_bool(params.get("auto_credit"), False)
    credit_id = str(params.get("credit_preset_id") or params.get("preset") or "credit.default").strip()
    make_queue(
        projects, restart_between=True, auto_subtitle=auto_sub,
        auto_credit=auto_credit, credit_preset_id=credit_id if auto_credit else "")
    stop_on_error = True if params.get("stop_on_error") is None else bool(params.get("stop_on_error"))

    results = []
    failed_n = 0
    subtitled_n = 0
    for i, proj in enumerate(projects):
        mark_queue_item(i, "running")
        counts = {}
        if auto_sub and project_needs_subtitle(proj):
            code, payload = _run_do_child("auto_subtitle", proj)
            ok = (code == 0) and (not payload or payload.get("ok", True))
            counts = dict((payload or {}).get("counts") or {})
            if not ok:
                err = (payload or {}).get("error") or ("auto_subtitle 退出码 %s" % code)
                mark_queue_item(i, "failed", error=err, counts=counts)
                results.append({
                    "project": abs_path(proj),
                    "ok": False,
                    "exit": code,
                    "counts": counts,
                    "error": err,
                    "artifacts": [],
                    "auto_subtitle": True,
                })
                failed_n += 1
                if stop_on_error:
                    for j in range(i + 1, len(projects)):
                        mark_queue_item(j, "skipped", error="前一个工程失败，已停止")
                    break
                continue
            subtitled_n += 1

        if auto_credit and credit_id:
            from cutora.subtitle.text_preset import apply_to_project_file
            try:
                apply_to_project_file(proj, credit_id, idempotent=True)
            except Exception as exc:
                err = "自动署名失败: %s" % exc
                mark_queue_item(i, "failed", error=err, counts=counts)
                results.append({
                    "project": abs_path(proj),
                    "ok": False,
                    "exit": EXIT_FAIL,
                    "counts": counts,
                    "error": err,
                    "artifacts": [],
                    "auto_subtitle": bool(auto_sub),
                })
                failed_n += 1
                if stop_on_error:
                    for j in range(i + 1, len(projects)):
                        mark_queue_item(j, "skipped", error="前一个工程失败，已停止")
                    break
                continue

        code, payload = _run_batch_export_child(proj, params)
        ok = (code == 0) and (not payload or payload.get("ok", True))
        exp_counts = (payload or {}).get("counts") or {}
        counts = dict(counts)
        counts.update(exp_counts)
        err = None if ok else ((payload or {}).get("error") or ("退出码 %s" % code))
        mark_queue_item(i, "done" if ok else "failed", error=err, counts=counts)
        results.append({
            "project": abs_path(proj),
            "ok": ok,
            "exit": code,
            "counts": counts,
            "error": err,
            "artifacts": (payload or {}).get("artifacts") or [],
            "auto_subtitle": bool(auto_sub),
        })
        if not ok:
            failed_n += 1
            if stop_on_error:
                for j in range(i + 1, len(projects)):
                    mark_queue_item(j, "skipped", error="前一个工程失败，已停止")
                break

    deactivate_queue()

    produced = []
    for r in results:
        produced.extend(r.get("artifacts") or [])
    all_ok = failed_n == 0 and len(results) == len(projects)
    return {
        "ok": all_ok,
        "counts": {
            "projects": len(projects),
            "finished": len(results),
            "failed": failed_n,
            "split_parts": len(artifacts),
            "auto_subtitled": subtitled_n,
        },
        "artifacts": produced,
        "projects": [abs_path(p) for p in projects],
        "split_artifacts": artifacts,
        "per_project": results,
        "next_hint": None if all_ok else "失败的工程修完后，用 queue_run 只传入未完成的工程",
        "human_needed": (not all_ok),
        "error": None if all_ok else ("%d 个工程导出失败" % failed_n),
        "queue": queue_summary(),
    }

# ===========================================================================
#  op: auto_subtitle
# ===========================================================================

def _align_one(host, task):
    video = task.get("video") or task.get("path")
    text = (task.get("text") or "").strip()
    if not video or not os.path.isfile(video):
        raise CliError("视频不存在: %s" % video, EXIT_FAIL)
    if not text:
        return None
    style = task.get("style") or {}
    video_w = None
    dims = task.get("dims")
    if isinstance(dims, (list, tuple)) and dims:
        try:
            video_w = int(dims[0])
        except Exception:
            video_w = None
    if not video_w:
        try:
            probed = host.probe_bitrates(video)
            if probed:
                video_w = probed.get("width")
        except Exception:
            video_w = None

    segs_holder = []
    err_holder = []
    worker = host.AlignmentWorker(video, text, style=style, video_w=video_w)
    worker.finished.connect(lambda s: segs_holder.append(s))
    worker.error.connect(lambda m: err_holder.append(m))
    worker.run()
    if err_holder:
        raise CliError("对齐失败 %s: %s" % (os.path.basename(video), err_holder[0]), EXIT_FAIL)
    if not segs_holder:
        raise CliError("对齐没有返回结果: %s" % os.path.basename(video), EXIT_FAIL)
    segs = segs_holder[0] or []
    for seg in segs:
        if isinstance(seg, dict) and "role" not in seg:
            seg["role"] = "default"
    # 与界面默认一致：enable_time_postprocess = False，只做时长兜底
    segs = host.fix_segment_durations(
        segs, min_dur=0.6, next_gap_pad=0.05, rebuild_words_first_only=True)
    try:
        host.trim_gpu_cache("CLI auto_subtitle")
    except Exception:
        pass
    return segs

def op_auto_subtitle(params, cmd):
    from cutora.ui import main_window as host
    src = params.get("project")
    if not src:
        raise CliError("auto_subtitle 需要 params.project", EXIT_USAGE)
    force = _as_bool(params.get("force_relayout"), False)
    data, tasks = load_project_file(host, src)
    out = abs_path(params["out"]) if params.get("out") else abs_path(src)

    unaligned = [i for i, t in enumerate(tasks)
                 if not t.get("segments") and str(t.get("text") or "").strip()]
    has_text = [i for i, t in enumerate(tasks) if str(t.get("text") or "").strip()]

    if unaligned:
        targets = unaligned
        mode = "A"
    elif has_text:
        if not force:
            return {
                "ok": True,
                "counts": {"tasks": len(tasks), "processed": 0, "skipped_relayout": len(has_text)},
                "artifacts": [abs_path(src)],
                "next_hint": "所有条目已有字幕。若要按最新样式重打轴，传 force_relayout: true",
                "project": abs_path(src),
                "mode": "B-skip",
            }
        targets = has_text
        mode = "B"
    else:
        raise CliError("工程里没有带文案的视频，无法打轴", EXIT_FAIL)

    eprint("[auto_subtitle] mode=%s, %d / %d 条" % (mode, len(targets), len(tasks)))
    done = 0
    for i in targets:
        t = tasks[i]
        name = os.path.basename(t.get("video") or t.get("path") or "?")
        eprint("[auto_subtitle] (%d/%d) %s" % (done + 1, len(targets), name))
        eprint("[progress] subtitle %d" % int((done + 1) * 100 / max(1, len(targets))))
        segs = _align_one(host, t)
        if segs is not None:
            from cutora.subtitle.track_source import set_segments
            set_segments(t, segs)
        done += 1

    write_project_file(host, out, tasks, curr_idx=data.get("curr_idx", 0))
    return {
        "ok": True,
        "counts": {"tasks": len(tasks), "processed": done, "mode": mode},
        "artifacts": [abs_path(out)],
        "next_hint": "下一步 batch_export",
        "project": abs_path(out),
        "mode": mode,
    }

# ===========================================================================
#  op: batch_export
# ===========================================================================

def _output_path_for(task):
    from cutora.render.export_params import batch_output_path
    return batch_output_path(task)

def _serial_render(host, jobs, failed_indices, mode, use_gpu):
    still_failed = []
    for j in failed_indices:
        task, out_path = jobs[j]
        eprint("[export] 串行渲染 %s → %s" % (os.path.basename(task.get("video") or ""), out_path))
        err = []
        worker = host.RenderWorker(task, out_path, mode, bool(use_gpu), export_logs=False)
        worker.error.connect(lambda m: err.append(m))
        worker.run()
        if err or not os.path.isfile(out_path):
            still_failed.append(j)
            eprint("[export] 重试仍失败: %s" % (err[0] if err else "没有产出文件"))
    return still_failed

def op_batch_export(params, cmd):
    from cutora.ui import main_window as host
    src = params.get("project")
    if not src:
        raise CliError("batch_export 需要 params.project", EXIT_USAGE)
    data, tasks = load_project_file(host, src)
    if not tasks:
        raise CliError("工程里没有视频", EXIT_FAIL)

    jobs = []
    artifacts = []
    for t in tasks:
        video = t.get("video") or t.get("path")
        if not video or not os.path.isfile(video):
            raise CliError("工程里的视频不存在: %s" % video, EXIT_FAIL)
        outp = _output_path_for(t)
        jobs.append((t, outp))
        artifacts.append(abs_path(outp))
        try:
            from cutora.batch import script_insert_image as _sii
            copies = _sii.active_copies(t)
            for dest in _sii.platform_dest_paths(outp, copies):
                ad = abs_path(dest)
                if ad not in artifacts:
                    artifacts.append(ad)
        except Exception:
            pass

    mode = params.get("mode") or "quality"
    want_gpu = params.get("gpu")
    if want_gpu is None:
        want_gpu = str(getattr(host, "DEVICE", "cpu")).lower() == "cuda"
    else:
        want_gpu = _as_bool(want_gpu, False)

    workers_param = params.get("workers")
    n = len(jobs)
    workers = None
    if workers_param not in (None, "", "null"):
        try:
            workers = max(1, int(workers_param))
        except Exception:
            raise CliError("workers 必须是整数或 null", EXIT_USAGE)

    gpu_workers = None
    enc_threads = 0
    if host._GUARD is not None and n > 1:
        try:
            if want_gpu:
                try:
                    host.release_gpu_models()
                except Exception:
                    pass
            plan_workers = workers if workers else host.PARALLEL_EXPORT_WORKERS
            plan = host._GUARD.plan_parallel_export(n, plan_workers, want_gpu)
            if workers is None:
                workers = plan["workers"]
            gpu_workers = plan["gpu_workers"] if want_gpu else 0
            enc_threads = plan.get("enc_threads") or 0
            eprint("[Guard] 体检: workers=%s gpu_workers=%s enc_threads=%s" % (
                workers, gpu_workers, enc_threads))
            for note in (plan.get("notes") or []):
                eprint("[Guard]   · %s" % note)
        except Exception as e:
            eprint("[Guard] 体检失败，用默认值: %s" % e)

    if workers is None:
        workers = max(1, min(n, int(getattr(host, "PARALLEL_EXPORT_WORKERS", 2) or 2)))
    workers = max(1, min(int(workers), n))

    failed = []
    if n == 1:
        failed = _serial_render(host, jobs, [0], mode, want_gpu)
    else:
        from PySide6.QtCore import QEventLoop
        mgr = host.ParallelExportManager(
            jobs,
            mode=mode,
            use_gpu=bool(want_gpu),
            export_mode=getattr(host, "EXPORT_VIDEO_MODE", "compressed"),
            workers=workers,
            parent=None,
            gpu_workers=gpu_workers,
            enc_threads=enc_threads,
        )
        done_holder = []

        def _on_finished(idxs):
            done_holder.append(list(idxs or []))
            loop.quit()

        mgr.finished.connect(_on_finished)
        mgr.degraded.connect(lambda s: eprint("[Guard] 自动降级: %s" % s))
        loop = QEventLoop()
        mgr.start()
        if not done_holder:
            loop.exec()
        failed = done_holder[-1] if done_holder else list(range(n))
        if failed:
            eprint("[export] %d 个并行未完成，改串行补做" % len(failed))
            failed = _serial_render(host, jobs, failed, mode, want_gpu)

    produced = [p for p in artifacts if os.path.isfile(p)]
    if produced:
        try:
            if r"D:\0_LTX" not in sys.path:
                sys.path.insert(0, r"D:\0_LTX")
            from pathlib import Path as _Path
            from core.spine_db import SpineDB
            _spine = SpineDB()
            for _vp in produced:
                _p = _Path(_vp)
                _spine.on_cutora_render_completed(
                    task_id=_p.stem.split("_")[0],
                    exported_video_path=_p,
                )
        except Exception:
            pass
    ok = len(failed) == 0
    return {
        "ok": ok,
        "counts": {
            "total": n,
            "produced": len(produced),
            "failed": len(failed),
            "workers": workers,
        },
        "artifacts": produced,
        "next_hint": None if ok else "查看失败下标对应的原视频，修后再导",
        "human_needed": (not ok),
        "error": None if ok else ("%d 条导出失败" % len(failed)),
        "failed_indices": failed,
    }

def spawn_async(op_name, params, cmd):
    holder = read_lock()
    if holder and _pid_alive(holder.get("pid")):
        eprint(lock_blocked_message(holder))
        payload = {"op": op_name, "ok": False, "error": lock_blocked_message(holder),
                   "human_needed": False}
        if cmd.get("json"):
            emit_json(payload)
        return EXIT_LOCKED

    os.makedirs(JOBS_DIR, exist_ok=True)
    job_id = cmd.get("job_id") or time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
    params_path = os.path.join(JOBS_DIR, job_id + ".params.json")
    log_path = os.path.join(JOBS_DIR, job_id + ".log")
    job_path = os.path.join(JOBS_DIR, job_id + ".json")
    atomic_write_json(params_path, params)

    argv = [
        sys.executable, "-u", "-m", "cutora",
        "--do", op_name,
        "--params", "@" + params_path,
        "--json",
        "--job-id", job_id,
    ]
    import subprocess
    flags = 0
    if os.name == "nt":
        flags = (getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                 | getattr(subprocess, "DETACHED_PROCESS", 0))
    logf = open(log_path, "w", encoding="utf-8", errors="ignore")
    try:
        proc = _no_window_popen(
            argv,
            cwd=REPO_ROOT,
            stdin=subprocess.DEVNULL,
            stdout=logf,
            stderr=subprocess.STDOUT,
            creationflags=flags,
            start_new_session=(os.name != "nt"),
        )
    except Exception as e:
        logf.close()
        raise CliError("无法启动后台导出: %s" % e, EXIT_FAIL)

    info = {
        "job_id": job_id,
        "op": op_name,
        "pid": proc.pid,
        "started_at": now_iso(),
        "state_path": abs_path(EXPORT_STATE_PATH),
        "log": abs_path(log_path),
        "params": abs_path(params_path),
        "status": "starting",
    }
    atomic_write_json(job_path, info)
    payload = {
        "job": job_id,
        "state": abs_path(EXPORT_STATE_PATH),
        "log": abs_path(log_path),
        "pid": proc.pid,
        "op": op_name,
    }
    emit_json(payload)
    eprint("[CLI] 已在后台启动 %s job=%s pid=%s" % (op_name, job_id, proc.pid))
    return EXIT_OK

# ===========================================================================
#  --status
# ===========================================================================

def cmd_status(job_id):
    info = None
    if job_id:
        jp = os.path.join(JOBS_DIR, str(job_id) + ".json")
        if os.path.isfile(jp):
            try:
                info = load_json_file(jp)
            except Exception:
                info = None
        else:
            return {
                "op": "status",
                "ok": False,
                "running": False,
                "error": "找不到 job: %s" % job_id,
                "human_needed": False,
            }

    state_path = (info or {}).get("state_path") or EXPORT_STATE_PATH
    state = {}
    if os.path.isfile(state_path):
        try:
            state = load_json_file(state_path)
        except Exception as e:
            state = {"error": "读取 state 失败: %s" % e}

    pid = (info or {}).get("pid") or state.get("parent_pid")
    status = state.get("status") or ""
    running = (status == "running") and (pid is None or _pid_alive(pid))
    failed = int(state.get("failed") or 0)
    return {
        "op": (info or {}).get("op") or "batch_export",
        "job": job_id,
        "running": bool(running),
        "done": int(state.get("done") or 0),
        "total": int(state.get("total") or 0),
        "latest": state.get("latest") or "",
        "degraded": state.get("degraded") or "",
        "human_needed": bool(failed > 0 and not running),
        "pid": pid,
        "failed": failed,
        "status": status,
        "state_path": abs_path(state_path) if state_path else None,
        "log": (info or {}).get("log"),
        "project_queue": queue_summary(),
    }

# ===========================================================================
#  op: add_text_overlay / add_bgm
# ===========================================================================

AUDIO_EXTS = {".mp3", ".wav", ".aac", ".m4a", ".flac", ".ogg"}

def _task_duration(host, task):
    d = 0.0
    try:
        d = float(task.get("duration") or task.get("trim_end_sec") or 0)
    except Exception:
        d = 0.0
    if d > 0:
        return d
    video = task.get("video") or task.get("path") or ""
    try:
        probed = host.probe_duration_sec(video)
        if probed:
            return float(probed)
    except Exception:
        pass
    return 0.0

def _task_dims(task):
    dims = task.get("dims") or []
    if isinstance(dims, (list, tuple)) and len(dims) >= 2:
        try:
            w, h = int(dims[0]), int(dims[1])
            if w > 0 and h > 0:
                return w, h
        except Exception:
            pass
    return 1080, 1920

def _scale_text_overlay(ov, dims):
    """把预设从它的画布尺寸缩到当前视频画布。署名预设是整幅右对齐。"""
    out = copy.deepcopy(ov)
    tw, th = dims
    cw = float(out.get("_canvas_w") or out.get("w") or tw or 1080)
    ch = float(out.get("_canvas_h") or out.get("h") or th or 1920)
    if cw <= 0:
        cw = float(tw)
    if ch <= 0:
        ch = float(th)
    sx, sy = tw / cw, th / ch
    avg = (sx + sy) / 2.0
    for k, scale in (
        ("x", sx), ("y", sy), ("w", sx), ("h", sy),
        ("fontsize", avg), ("stroke_width", avg),
        ("bg_padding", avg), ("bg_padding_x", sx), ("bg_padding_y", sy),
        ("bg_radius", avg), ("bg_expand_w", sx), ("bg_expand_h", sy),
    ):
        if k in out and isinstance(out[k], (int, float)):
            out[k] = max(1, int(round(float(out[k]) * scale))) if k != "y" else int(round(float(out[k]) * scale))
    # 整幅右对齐署名：铺满画布，靠 text_layout.align=right
    layout = out.get("text_layout") or {}
    if str(layout.get("align") or "").lower() == "right":
        out["x"] = 0
        out["w"] = int(tw)
        out["h"] = int(th)
        if "y" not in ov:
            out["y"] = 0
    out["_canvas_w"] = int(tw)
    out["_canvas_h"] = int(th)
    return out

def _load_text_preset_raw(host, name):
    from cutora.subtitle import text_preset as TP
    row = TP.resolve_preset(name)
    if row is None:
        return {}
    return TP.template_from_preset(row)

def op_add_text_overlay(params, cmd):
    from cutora.subtitle import text_preset as TP
    src = params.get("project")
    if not src:
        raise CliError("add_text_overlay 需要 params.project", EXIT_USAGE)
    src = abs_path(src)
    if not os.path.isfile(src):
        raise CliError("找不到工程文件: %s" % src, EXIT_FAIL)
    preset_ref = (params.get("preset") or TP.DEFAULT_PRESET_ID).strip() or TP.DEFAULT_PRESET_ID
    preset = TP.resolve_preset(preset_ref)
    if preset is None:
        raise CliError("没有文本预设 %r。先在片语 Studio 里存一个，或改 params.preset" % preset_ref, EXIT_FAIL)
    content = params.get("content")
    replace = _as_bool(params.get("replace"), True)
    try:
        with open(src, encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception as exc:
        raise CliError("工程文件格式不对: %s" % exc, EXIT_FAIL) from exc
    if not isinstance(data, dict):
        raise CliError("工程文件不是 JSON 对象", EXIT_FAIL)
    tasks = list(data.get("tasks") or [])
    if not tasks:
        raise CliError("工程里没有视频", EXIT_FAIL)

    n = 0
    for t in tasks:
        if not isinstance(t, dict):
            continue
        info = TP.apply_to_task(t, preset, idempotent=replace)
        oid = info.get("overlay_id")
        shown = preset.get("text") or ""
        if content is not None and str(content).strip() != "":
            shown = str(content)
            for ov in t.get("text_overlays") or []:
                if isinstance(ov, dict) and ov.get("id") == oid:
                    ov["content"] = shown
                    break
        n += 1
        eprint("[add_text_overlay] %s  ← %s  %s"
               % (os.path.basename(t.get("video") or "?"), preset.get("id") or preset_ref, shown))

    out = abs_path(params["out"]) if params.get("out") else src
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    return {
        "ok": True,
        "counts": {"tasks": len(tasks), "overlays": n},
        "artifacts": [abs_path(out)],
        "next_hint": "署名已写入工程。要加配乐就 add_bgm；要出片就 batch_export；要加插图就用界面打开工程",
        "project": abs_path(out),
        "preset": preset.get("id") or preset_ref,
    }

def _list_audio_files(folder):
    if not folder or not os.path.isdir(folder):
        return []
    out = []
    for n in os.listdir(folder):
        if os.path.splitext(n)[1].lower() in AUDIO_EXTS:
            out.append(os.path.join(folder, n))
    out.sort(key=lambda p: p.lower())
    return out

def op_add_bgm(params, cmd):
    import math
    import random
    from cutora.ui import main_window as host
    from cutora.model.timeline_model import MediaSource, Clip

    src = params.get("project")
    if not src:
        raise CliError("add_bgm 需要 params.project", EXIT_USAGE)
    folder = abs_path(params.get("dir") or params.get("folder") or "")
    if not folder or not os.path.isdir(folder):
        raise CliError("add_bgm 需要一个音频文件夹 params.dir", EXIT_USAGE)
    try:
        volume = float(params.get("volume") if params.get("volume") is not None else 0.22)
    except (TypeError, ValueError):
        volume = 0.22
    volume = max(1e-6, min(2.0, volume))
    gain_db = 20.0 * math.log10(volume)

    data, tasks = load_project_file(host, src)
    if not tasks:
        raise CliError("工程里没有视频", EXIT_FAIL)
    pool = _list_audio_files(folder)
    if not pool:
        raise CliError("文件夹里没有音频（mp3/wav/m4a/flac/ogg/aac）: %s" % folder, EXIT_FAIL)

    durs = {}

    def _dur(path):
        if path not in durs:
            durs[path] = host.probe_duration_sec(path)
        return durs[path]

    video_durs = []
    for t in tasks:
        vd = _task_duration(host, t)
        if not vd:
            raise CliError("无法获取视频时长: %s" % (t.get("video") or "?"), EXIT_FAIL)
        video_durs.append(vd)

    unused = pool[:]
    random.shuffle(unused)
    plan = []
    pending = []
    for i, vd in enumerate(video_durs):
        # 优先选比视频长的，短的也能用（导出会循环）
        longer = [a for a in unused if (_dur(a) or 0) > vd]
        cand = longer or unused
        if cand:
            pick = random.choice(cand)
            plan.append(pick)
            unused.remove(pick)
        else:
            pending.append(i)
            plan.append(None)
    for i in pending:
        plan[i] = random.choice(pool)

    from cutora.audio.sfx_library import (
        apply_new_bgm_defaults, clip_audio_role, ensure_role_track, log_bgm_assign,
    )

    assigned = []
    for t, apath, vd in zip(tasks, plan, video_durs):
        tl = host._tl_of(t) if hasattr(host, "_tl_of") else t.get("timeline")
        if tl is None:
            raise CliError("工程任务没有时间线: %s" % (t.get("video") or "?"), EXIT_FAIL)
        bgm_source = MediaSource(apath)
        bgm_source.has_video = False
        bgm_source.has_audio = True
        src_dur = float(bgm_source.duration or 0.0)
        tl.sources[bgm_source.id] = bgm_source
        audio_track = ensure_role_track(tl, "bgm")
        keep = []
        for c in audio_track.clips:
            role = clip_audio_role(c)
            if getattr(c, "is_main", False) or role in ("main", "sfx"):
                keep.append(c)
        audio_track.clips = keep
        bgm_clip = Clip(bgm_source.id, 0.0, float(vd), 0.0)
        apply_new_bgm_defaults(bgm_clip)
        if params.get("volume") is not None:
            bgm_clip.gain_db = gain_db
        audio_track.clips.append(bgm_clip)
        log_bgm_assign(audio_track, 1, volume, 0)
        if hasattr(host, "_audio_clips_from_track"):
            t["audio_clips"] = host._audio_clips_from_track(t)
            for c in t["audio_clips"]:
                c.pop("_clip_obj", None)
        host.remap_bgm_to_full_duration(t)
        assigned.append(os.path.basename(apath))
        eprint("[add_bgm] %s  ← %s  (%.1fs, vol=%.2f)"
               % (os.path.basename(t.get("video") or "?"), os.path.basename(apath), vd, volume))

    out = abs_path(params["out"]) if params.get("out") else abs_path(src)
    write_project_file(host, out, tasks, curr_idx=data.get("curr_idx", 0))
    return {
        "ok": True,
        "counts": {"tasks": len(tasks), "assigned": len(assigned)},
        "artifacts": [abs_path(out)],
        "files": assigned,
        "next_hint": "配乐已写入工程。要出片就 batch_export；要加插图就用界面打开工程",
        "project": abs_path(out),
        "dir": folder,
    }

# ===========================================================================
#  op: merge_projects
# ===========================================================================

def is_asproj_path(path):
    from cutora.core.brand import is_project_path
    return is_project_path(path)

def _natural_key(s):
    import re
    return [int(t) if t.isdigit() else t.lower()
            for t in re.split(r"(\d+)", os.path.basename(s or ""))]

def collect_asproj_paths(spec, sort="name"):
    """文件、目录或数组 → 绝对路径列表。sort: name | mtime | given。"""
    if spec is None or spec == "":
        raise CliError("merge_projects 需要 params.projects 或 params.dir", EXIT_USAGE)
    items = list(spec) if isinstance(spec, (list, tuple)) else [spec]
    out = []
    seen = set()

    def add(p):
        ap = abs_path(p)
        k = os.path.normcase(ap)
        if k in seen:
            return
        if os.path.isfile(ap) and is_asproj_path(ap):
            seen.add(k)
            out.append(ap)

    for it in items:
        ap = abs_path(it)
        if os.path.isdir(ap):
            try:
                names = os.listdir(ap)
            except Exception as e:
                raise CliError("读不了目录 %s: %s" % (ap, e), EXIT_FAIL)
            for n in names:
                add(os.path.join(ap, n))
        elif os.path.isfile(ap):
            if not is_asproj_path(ap):
                raise CliError("不是工程文件: %s" % ap, EXIT_USAGE)
            add(ap)
        else:
            raise CliError("找不到工程或目录: %s" % ap, EXIT_FAIL)
    if not out:
        raise CliError("没有找到 .asproj.json / .asproj", EXIT_FAIL)
    if sort == "mtime":
        out.sort(key=lambda p: os.path.getmtime(p))
    elif sort == "given":
        pass
    else:
        out.sort(key=_natural_key)
    return out

def op_merge_projects(params, cmd):
    from cutora.ui import main_window as host
    spec = params.get("projects")
    if spec is None:
        spec = params.get("dir")
    sort = str(params.get("sort") or "name").strip().lower()
    if sort not in ("name", "mtime", "given"):
        sort = "name"
    dedupe = _as_bool(params.get("dedupe"), True)
    paths = collect_asproj_paths(spec, sort=sort)

    all_tasks = []
    sources = []
    missing = []
    seen_video = set()
    skipped_dup = 0
    n_with_segs = 0
    n_with_text = 0
    for p in paths:
        eprint("[merge_projects] 读 %s" % os.path.basename(p))
        _data, tasks = load_project_file(host, p)
        kept = 0
        for t in tasks:
            vid = t.get("video") or t.get("path") or ""
            if vid and not os.path.isfile(vid):
                missing.append(vid)
            key = ""
            if vid:
                try:
                    key = os.path.normcase(os.path.abspath(vid))
                except Exception:
                    key = os.path.normcase(vid)
            if dedupe and key and key in seen_video:
                skipped_dup += 1
                continue
            if key:
                seen_video.add(key)
            all_tasks.append(t)
            kept += 1
            if t.get("segments"):
                n_with_segs += 1
            if str(t.get("text") or "").strip():
                n_with_text += 1
        sources.append({"file": abs_path(p), "tasks": kept})
        eprint("[progress] merge %d" % int(len(sources) * 100 / max(1, len(paths))))

    if not all_tasks:
        raise CliError("合并后没有视频条目", EXIT_FAIL)
    if params.get("out"):
        out = abs_path(params["out"])
    else:
        out = os.path.join(os.path.dirname(paths[0]), "merged.asproj.json")
    write_project_file(host, out, all_tasks, curr_idx=0)
    eprint("[merge_projects] %d 个工程 → %d 条视频，字幕已保留 %d 条"
           % (len(paths), len(all_tasks), n_with_segs))
    return {
        "ok": True,
        "counts": {
            "projects": len(paths),
            "tasks": len(all_tasks),
            "with_segments": n_with_segs,
            "with_text": n_with_text,
            "deduped": skipped_dup,
            "missing_videos": len(missing),
        },
        "artifacts": [abs_path(out)],
        "project": abs_path(out),
        "missing": missing,
        "sources": [
            {"file": os.path.basename(s["file"]), "tasks": s["tasks"]}
            for s in sources
        ],
        "next_hint": "用界面打开该工程即可。字幕已在每条视频上，不必再粘贴。",
        "human_needed": bool(missing),
        "error": None,
    }

def op_scatter_broll(params, cmd):
    """切开短视频，静音循环铺到每条长视频的 Video2。"""
    from cutora.ui import main_window as host
    from cutora.batch import script_broll_scatter as sbs

    src = params.get("project")
    if not src:
        raise CliError("scatter_broll 需要 params.project", EXIT_USAGE)
    clips_dir = abs_path(params.get("clips_dir") or params.get("dir") or "")
    if not clips_dir or not os.path.isdir(clips_dir):
        raise CliError("scatter_broll 需要片段目录 params.clips_dir", EXIT_USAGE)

    data, all_tasks = load_project_file(host, src)
    if not all_tasks:
        raise CliError("工程里没有视频", EXIT_FAIL)
    from cutora.batch.script_ui import select_tasks
    tasks = select_tasks(all_tasks, params.get("indices"), params.get("videos"))
    if not tasks:
        raise CliError("indices / videos 没有匹配到任何视频", EXIT_USAGE)

    p = dict(params)
    p["clips_dir"] = clips_dir
    p["mute"] = _as_bool(params.get("mute"), True)
    p["replace"] = _as_bool(params.get("replace"), True)
    if params.get("clip_sec") is not None:
        p["clip_sec"] = float(params.get("clip_sec"))
    if params.get("interval_sec") is not None:
        p["interval_sec"] = float(params.get("interval_sec"))
    if params.get("start_sec") is not None and params.get("start_sec") != "":
        p["start_sec"] = float(params.get("start_sec"))
    if params.get("video_track") is not None:
        p["video_track"] = int(params.get("video_track"))
    if params.get("out_dir"):
        p["out_dir"] = abs_path(params.get("out_dir"))

    eprint("[scatter_broll] 切开 %s" % clips_dir)
    prep = sbs.run_scatter(p, host=host, log=eprint)
    eprint("[scatter_broll] 撒播到 %d 条长视频，轨道 Video%d"
           % (len(tasks), prep["video_track"]))
    stats = sbs.scatter_into_tasks(
        tasks,
        prep["pieces"],
        interval_sec=prep["interval_sec"],
        start_sec=prep["start_sec"],
        video_track=prep["video_track"],
        mute=prep["mute"],
        replace=prep["replace"],
        host=host,
        log=eprint,
        clips_dir=clips_dir,
        out_dir=prep["out_dir"],
    )
    out = abs_path(params["out"]) if params.get("out") else abs_path(src)
    write_project_file(host, out, all_tasks, curr_idx=data.get("curr_idx", 0))
    return {
        "ok": True,
        "counts": {
            "tasks": stats["tasks"],
            "pieces": stats["pieces"],
            "placed": stats["placed"],
        },
        "artifacts": [abs_path(out), prep["out_dir"]],
        "per_task": stats["per_task"],
        "project": abs_path(out),
        "out_dir": prep["out_dir"],
        "next_hint": "切片已铺进工程 Video 轨道。用界面打开检查，要出片就 batch_export。",
    }

def op_insert_image(params, cmd):
    """同一张图批量铺到每条视频的 Images 轨道。"""
    from cutora.ui import main_window as host
    from cutora.batch import script_insert_image as sii

    src = params.get("project")
    if not src:
        raise CliError("insert_image 需要 params.project", EXIT_USAGE)
    image = abs_path(params.get("image") or params.get("path") or "")
    if not image or not os.path.isfile(image):
        raw = params.get("platform_copies") or params.get("platforms") or {}
        if isinstance(raw, dict):
            for key in ("youtube", "Youtube", "instagram", "Instagram", "tiktok", "TikTok"):
                cand = raw.get(key)
                if cand and os.path.isfile(abs_path(cand)):
                    image = abs_path(cand)
                    break
            if (not image or not os.path.isfile(image)) and isinstance(raw.get("platforms"), list):
                for p in raw.get("platforms") or []:
                    cand = (p or {}).get("image") or (p or {}).get("path")
                    if cand and os.path.isfile(abs_path(cand)):
                        image = abs_path(cand)
                        break
    if not image or not os.path.isfile(image):
        raise CliError("insert_image 需要有效图片 params.image（或多平台副本里至少一张图）", EXIT_USAGE)

    data, all_tasks = load_project_file(host, src)
    if not all_tasks:
        raise CliError("工程里没有视频", EXIT_FAIL)
    from cutora.batch.script_ui import select_tasks
    tasks = select_tasks(all_tasks, params.get("indices"), params.get("videos"))
    if not tasks:
        raise CliError("indices / videos 没有匹配到任何视频", EXIT_USAGE)

    p = dict(params)
    p["image"] = image
    p["replace"] = _as_bool(params.get("replace"), True)
    cfg = sii.run_insert(p, host=host, log=eprint)
    eprint("[insert_image] %s  → %d 条视频  Images%d  start=%.1fs"
           % (os.path.basename(image), len(tasks), cfg["image_track"], cfg["start_sec"]))
    stats = sii.insert_into_tasks(
        tasks,
        cfg["image"],
        start_sec=cfg["start_sec"],
        end_pad_sec=cfg["end_pad_sec"],
        end_sec=cfg["end_sec"],
        image_track=cfg["image_track"],
        fit=cfg["fit"],
        scale=cfg["scale"],
        x=cfg["x"],
        y=cfg["y"],
        replace=cfg["replace"],
        host=host,
        log=eprint,
        platform_copies=cfg.get("platform_copies"),
    )
    out = abs_path(params["out"]) if params.get("out") else abs_path(src)
    write_project_file(host, out, all_tasks, curr_idx=data.get("curr_idx", 0))
    copies = stats.get("platform_copies")
    hint = "图片已写入工程 Images 轨道。用界面打开检查，要出片就 batch_export。"
    if copies:
        names = ", ".join(p["prefix"] for p in copies.get("platforms") or [])
        mode = (copies or {}).get("render_mode") or "once"
        if str(mode).lower() in ("each", "multi", "multiple", "per_platform", "full"):
            how = "导出时每个平台单独渲染，字幕会盖在图片上。"
        else:
            how = "导出时字幕只烧一遍再叠图，成品带平台前缀。"
        hint = ("图片已写入工程。已开多平台副本：%s。%s" % (names, how))
    return {
        "ok": True,
        "counts": {"tasks": stats["tasks"], "placed": stats["placed"]},
        "artifacts": [abs_path(out)],
        "per_task": stats["per_task"],
        "project": abs_path(out),
        "image": image,
        "platform_copies": copies,
        "next_hint": hint,
    }

# ===========================================================================
#  GUI 锁（由 MainWindow 调用）
# ===========================================================================

def gui_try_acquire():
    """主窗口启动时调用。返回 (owned: bool, warning: str|None)。"""
    holder = acquire_lock("gui")
    if holder is None:
        return True, None
    return False, lock_blocked_message(holder)

OP_HANDLERS = {
    "stitch": op_stitch,
    "import_media": op_import_media,
    "auto_subtitle": op_auto_subtitle,
    "save_project": op_save_project,
    "split_project": op_split_project,
    "queue_run": op_queue_run,
    "batch_export": op_batch_export,
    "add_text_overlay": op_add_text_overlay,
    "add_bgm": op_add_bgm,
    "merge_projects": op_merge_projects,
    "scatter_broll": op_scatter_broll,
    "insert_image": op_insert_image,
}

def main(argv=None):
    argv = list(argv or sys.argv)
    code = early_dispatch(argv)
    if code is not None:
        return code
    if "--do" in argv or "--run" in argv:
        begin_json_stdio() if "--json" in argv else None
        os.chdir(REPO_ROOT)
        return late_dispatch(argv)
    from cutora.core.command_cli import is_commands_argv, dispatch_late
    if is_commands_argv(argv):
        os.chdir(REPO_ROOT)
        return dispatch_late(argv)
    eprint(usage_text())
    return EXIT_USAGE

if __name__ == "__main__":
    sys.exit(main(sys.argv))
