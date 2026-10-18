#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
右键表格工具 —— 可视化批量重命名 / 上传

界面是一个真正的电子表格：
  · 双击单元格编辑、方向键导航、Shift 选区、拖拽填充柄
  · 支持 Ctrl+V 粘贴多行多列（制表符分隔），自动识别
  · 插入 / 删除 / 移动 / 重命名 列与行，拖拽列宽
  · 单元格默认固定宽度、长文件名截断；可选换行，可批量切换
  · Ctrl+Z / Ctrl+Y 撤销重做
  · 所有旧功能（查找替换、前后缀、统一命名、序号）收进顶部菜单栏

工程文件：改名后自动在目录内保存 .renamer_project.json，
         随时可通过「文件 → 还原到最初的文件名」一键还原。

用法： pythonw 表格工具.py "目标文件夹"
"""

import os
import re
import sys
import hmac
import json
import time
import socket
import shutil
import secrets
import threading
import webbrowser
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

APP_NAME = "右键表格工具"
PROJECT_FILE = ".renamer_project.json"
ILLEGAL_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
VIDEO_EXTS = {
    ".mp4", ".mov", ".mkv", ".avi", ".m4v", ".wmv",
    ".webm", ".mpg", ".mpeg", ".mpe", ".flv", ".ts", ".m2ts", ".mts",
}
RCLONE_REMOTE = "gdrive"
CREATE_NO_WINDOW = 0x08000000
RCLONE_CANDIDATES = [
    os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "runtime", "bin", "rclone.exe")),
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "rclone.exe"),
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "RightClickTableTool", "rclone.exe"),
]

MAX_BODY = 32 * 1024 * 1024


def sanitize(name):
    name = ILLEGAL_CHARS.sub('', name)
    return name.strip().rstrip('.')


def entry_name_ok(name):
    """只允许当前文件夹里的一个名字。拒绝绝对路径、盘符、分隔符和 .. 。"""
    if not isinstance(name, str):
        return False
    if not name or name != name.strip() or name in (".", ".."):
        return False
    if os.path.isabs(name):
        return False
    if any(c in name for c in ("/", "\\", "\x00", ":")):
        return False
    return True


def natural_key(s):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r'(\d+)', s)]


# ============================================================ 工程文件
class Project:
    """就地保存的工程文件：记录表格状态、改名历史、以及每个文件的最初名字。"""

    def __init__(self, directory):
        self.dir = directory
        self.path = os.path.join(directory, PROJECT_FILE)
        self._last_mtime = 0.0
        self.data = {
            "version": 3,
            "dir": directory,
            "updated": "",
            "grid": None,          # 表格状态（列定义 + 行数据）
            "origin": {},          # 当前文件名 -> 最初的文件名
            "file_meta": {},       # 当前文件名 -> {original, size, link}
            "history": [],         # [{time, pairs:[[old,new],...]}] 每轮成功改名一条
            "snap_index": 0,       # 当前对应第几个时间点：0=最初，n=第 n 轮改名之后
        }
        self.load()

    def load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d, dict):
                self.data.update(d)
                self.data["dir"] = self.dir
                if self.data.get("snap_index") is None:
                    self.data["snap_index"] = len(self.data.get("history") or [])
            try:
                self._last_mtime = os.path.getmtime(self.path)
            except Exception:
                pass
        except Exception:
            pass

    def reload_if_newer(self):
        """若 Robota 在外部更新了 .renamer_project.json，自动热重载最新表格与下载清单。"""
        try:
            if os.path.exists(self.path):
                mt = os.path.getmtime(self.path)
                if mt > (self._last_mtime or 0.0) + 0.05:
                    self.load()
                    return True
        except Exception:
            pass
        return False

    def save(self):
        self.data["updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
        tmp = self.path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=1)
            self._attr(128)          # 先取消隐藏(NORMAL)，否则 Windows 上覆盖会被拒绝
            os.replace(tmp, self.path)
            self._attr(2)            # FILE_ATTRIBUTE_HIDDEN
            try:
                self._last_mtime = os.path.getmtime(self.path)
            except Exception:
                pass
            return True
        except Exception:
            try:
                os.remove(tmp)
            except Exception:
                pass
            return False

    def _attr(self, flags):
        if os.name != "nt" or not os.path.exists(self.path):
            return
        try:
            import ctypes
            ctypes.windll.kernel32.SetFileAttributesW(str(self.path), flags)
        except Exception:
            pass

    def snap_index(self):
        hist = self.data.get("history") or []
        i = self.data.get("snap_index")
        if i is None:
            return len(hist)
        try:
            i = int(i)
        except Exception:
            i = len(hist)
        return max(0, min(i, len(hist)))

    def snapshot_list(self):
        hist = self.data.get("history") or []
        cur = self.snap_index()
        out = [{"index": 0, "time": "", "label": "最初的文件名", "count": 0, "current": cur == 0}]
        for i, h in enumerate(hist):
            n = len(h.get("pairs") or [])
            out.append({
                "index": i + 1,
                "time": h.get("time") or "",
                "label": "第 %d 轮改名" % (i + 1),
                "count": n,
                "current": cur == i + 1,
            })
        return out

    def _name_after(self, original, index):
        """从最初名字出发，应用到第 index 轮改名之后的名字。index=0 即最初。"""
        name = original
        hist = self.data.get("history") or []
        for item in hist[: max(0, index)]:
            for pair in item.get("pairs") or []:
                if len(pair) >= 2 and pair[0] == name:
                    name = pair[1]
                    break
        return name

    def _rebuild_origin(self):
        """按 history[:snap_index] 重放，得到 当前名 -> 最初名。"""
        current_to_orig = {}
        for item in (self.data.get("history") or [])[: self.snap_index()]:
            for pair in item.get("pairs") or []:
                if len(pair) < 2:
                    continue
                old, new = pair[0], pair[1]
                orig = current_to_orig.get(old, old)
                current_to_orig.pop(old, None)
                current_to_orig[new] = orig
        self.data["origin"] = {c: o for c, o in current_to_orig.items() if c != o}

    def _remap_meta(self, pairs, links=None):
        meta = dict(self.data.get("file_meta") or {})
        links = links or {}
        saved = {}
        for old, _ in pairs:
            saved[old] = meta.pop(old, {}) or {}
        for old, new in pairs:
            info = dict(saved.get(old) or {})
            orig = info.get("original") or _origin_name((self.data.get("origin") or {}).get(new, old))
            info["original"] = orig or old
            path = os.path.join(self.dir, new)
            if os.path.isdir(path):
                info["kind"] = "dir"
            elif os.path.isfile(path):
                info["kind"] = "file"
                try:
                    info["size"] = os.path.getsize(path)
                except Exception:
                    pass
            link = links.get(new) or links.get(old) or info.get("link")
            if link:
                info["link"] = link
            meta[new] = info
        self.data["file_meta"] = meta

    def record(self, pairs, links=None, inventory=None):
        """一次成功改名：丢掉当前时间点之后的未来快照，再追加一轮。"""
        if not pairs:
            return
        hist = list(self.data.get("history") or [])
        idx = self.snap_index()
        if idx < len(hist):
            hist = hist[:idx]
        item = {
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "pairs": [[a, b] for a, b in pairs],
            "count": len(pairs),
        }
        if inventory:
            item["before"] = inventory          # 改名前文件夹里有什么，出了事可以对账
        hist.append(item)
        self.data["history"] = hist[-100:]
        self.data["snap_index"] = len(self.data["history"])
        self._remap_meta(pairs, links)
        self._rebuild_origin()
        self.save()

    def plan_to_snapshot(self, index):
        """把磁盘上的文件改到指定时间点的名字。index=0 最初，n=第 n 轮之后。"""
        hist = self.data.get("history") or []
        try:
            index = int(index)
        except Exception:
            index = 0
        index = max(0, min(index, len(hist)))
        origin = self.data.get("origin") or {}
        meta = self.data.get("file_meta") or {}
        files = list_all_names(self.dir)
        plan = []
        used = set()
        for f in files:
            orig = _origin_name(origin.get(f, f)) or f
            target = self._name_after(orig, index)
            if not target or target == f or f.lower() in used:
                continue
            plan.append((f, target))
            used.add(f.lower())
        return plan, index

    def finish_restore_to(self, index, renamed_pairs):
        hist = self.data.get("history") or []
        try:
            index = int(index)
        except Exception:
            index = 0
        self.data["snap_index"] = max(0, min(index, len(hist)))
        if renamed_pairs:
            self._remap_meta(renamed_pairs)
        self._rebuild_origin()
        self.save()

    def restore_plan(self):
        """回到最初名字，等价于时间点 0。"""
        plan, _ = self.plan_to_snapshot(0)
        return plan

    def export_payload(self):
        d = dict(self.data)
        d["version"] = 3
        d["dir"] = self.dir
        d["exported"] = time.strftime("%Y-%m-%d %H:%M:%S")
        return d


# ============================================================ 改名引擎
RNM_TMP = "__rnm"          # 两阶段改名用的临时前缀，正常情况下一瞬间就消失


def _tmp_name(directory, i, old):
    """临时名：__rnm<pid>_<序号>__<原名>。保证目录里当前不存在同名。"""
    k = 0
    while True:
        tag = "%s%d_%d%s__" % (RNM_TMP, os.getpid(), i, ("_%d" % k) if k else "")
        name = (tag + old)[:200]
        if not os.path.exists(os.path.join(directory, name)):
            return name
        k += 1


def recover_temp_files(directory):
    """上次改名如果被强杀/断电，可能残留临时文件。这里把它们改回原名。
    原名已经被占用时保持不动，只把名字报出来，绝不覆盖任何东西。"""
    recovered, stuck = [], []
    try:
        names = os.listdir(directory)
    except Exception:
        return recovered, stuck
    for name in names:
        if not name.startswith(RNM_TMP):
            continue
        parts = name.split("__", 2)          # ['', 'rnm<pid>_<i>', '原名']
        orig = parts[2] if len(parts) == 3 else ""
        if not orig:
            stuck.append(name)
            continue
        if os.path.exists(os.path.join(directory, orig)):
            stuck.append(name)
            continue
        try:
            os.rename(os.path.join(directory, name), os.path.join(directory, orig))
            recovered.append([name, orig])
        except Exception:
            stuck.append(name)
    return recovered, stuck


def folder_inventory(directory):
    """改名前的文件夹快照：[[名字, 字节数], ...]。只用来事后对账，不参与任何判断。"""
    out = []
    try:
        for name in sorted(os.listdir(directory)):
            if name.startswith(RNM_TMP):
                continue
            try:
                st = os.stat(os.path.join(directory, name))
                out.append([name, st.st_size if os.path.isfile(os.path.join(directory, name)) else -1])
            except Exception:
                out.append([name, -1])
    except Exception:
        return []
    return out[:5000]


def do_rename(directory, plan):
    """两阶段改名，可安全处理互换 / 循环 / 仅大小写变化。plan=[(old,new),...]

    铁律：只改名。绝不覆盖、绝不删除任何文件。
    执行前先体检，有一条不过就整批不动；执行中出任何错，整批回滚。
    """
    def fail(msg):
        return {"ok": False, "message": msg, "renamed": [], "errors": [], "skipped": []}

    plan = [(o, n) for o, n in plan if o != n]
    if not plan:
        return {"ok": True, "renamed": [], "errors": [], "skipped": []}

    bad = ["%s → %s" % (o, n) for o, n in plan if not entry_name_ok(o) or not entry_name_ok(n)]
    if bad:
        return fail("文件名不能带路径或盘符，这次一个都没有改：\n" + "\n".join(bad[:12]))

    # 体检 0：把已经不在文件夹里的源文件剔掉（被移走/删掉的，不是危险，跳过即可）
    skipped = [o for o, _ in plan if not os.path.exists(os.path.join(directory, o))]
    if skipped:
        gone = {x.lower() for x in skipped}
        plan = [(o, n) for o, n in plan if o.lower() not in gone]
    if not plan:
        return {"ok": True, "renamed": [], "errors": [], "skipped": skipped}

    # 体检 1：新名字之间不能重复，否则必然有文件被盖掉
    targets = [n.lower() for _, n in plan]
    dups = sorted({t for t in targets if targets.count(t) > 1})
    if dups:
        return fail("以下新文件名重复，会互相覆盖，本次一个都没有改：\n" + "\n".join(dups[:12]))

    # 体检 2：新名字不能落在「本次不参与改名的文件」头上
    sources = {o.lower() for o, _ in plan}
    taken = [(o, n) for o, n in plan
             if n.lower() not in sources and os.path.exists(os.path.join(directory, n))]
    if taken:
        return fail("这些新文件名已经被文件夹里别的文件占用了。"
                    "为了不覆盖你的文件，本次一个都没有改：\n"
                    + "\n".join("%s → %s" % (o, n) for o, n in taken[:12]))

    before = set(os.listdir(directory))
    staged = []                      # [(old, tmp, new)]

    def back_to_original():
        for old, tmp, _new in reversed(staged):
            try:
                if (os.path.exists(os.path.join(directory, tmp))
                        and not os.path.exists(os.path.join(directory, old))):
                    os.rename(os.path.join(directory, tmp), os.path.join(directory, old))
            except Exception:
                pass

    # 阶段一：原名 -> 临时名（腾空所有目标位置）
    try:
        for i, (old, new) in enumerate(plan):
            tmp = _tmp_name(directory, i, old)
            os.rename(os.path.join(directory, old), os.path.join(directory, tmp))
            staged.append((old, tmp, new))
    except Exception as e:
        back_to_original()
        return fail("改名中断，已经全部还原，你的文件没有变动。\n原因：%s" % e)

    # 阶段二：临时名 -> 新名。目标只要还存在就立刻停手，绝不覆盖
    renamed, done = [], []
    try:
        for old, tmp, new in staged:
            dst = os.path.join(directory, new)
            if os.path.exists(dst):
                raise OSError("「%s」这个名字已经被占用，不能覆盖它" % new)
            os.rename(os.path.join(directory, tmp), dst)
            renamed.append([old, new])
            done.append((old, tmp, new))
    except Exception as e:
        for old, tmp, new in reversed(done):          # 已经落地的先退回临时名
            try:
                os.rename(os.path.join(directory, new), os.path.join(directory, tmp))
            except Exception:
                pass
        back_to_original()
        return fail("改名中断，已经全部还原，你的文件没有变动。\n原因：%s" % e)

    # 体检 3：改完之后，文件一个都不能少
    warn = []
    try:
        after = set(os.listdir(directory))
        if len(after) < len(before):
            warn.append("⚠ 改名前 %d 个条目，改名后 %d 个，少了 %d 个，请立刻检查这个文件夹。"
                        % (len(before), len(after), len(before) - len(after)))
        left = sorted(f for f in after if f.startswith(RNM_TMP))
        if left:
            warn.append("⚠ 还有临时文件没有恢复：" + "、".join(left[:6]))
    except Exception:
        pass
    if skipped:
        warn.append("有 %d 个文件已经不在文件夹里，跳过了。" % len(skipped))

    return {"ok": True, "renamed": renamed, "errors": warn, "skipped": skipped}


def prefs_path():
    base = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "RightClickTableTool")
    try:
        os.makedirs(base, exist_ok=True)
    except Exception:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, "prefs.json")


def default_prefs():
    return {
        "width": 1320, "height": 860,
        "showLink": True, "showPrev": True, "showStat": True,
        "includeDirs": False, "videosOnly": True,
        "specialW": {"porig": 180, "pnew": 180, "link": 180, "stat": 150},
        "dataColW": 160,
    }


def load_prefs():
    p = default_prefs()
    try:
        with open(prefs_path(), "r", encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict):
            p.update(d)
            if isinstance(d.get("specialW"), dict):
                sw = default_prefs()["specialW"]
                sw.update(d["specialW"])
                p["specialW"] = sw
    except Exception:
        pass
    return p


def save_prefs(p):
    cur = load_prefs()
    cur.update(p or {})
    try:
        with open(prefs_path(), "w", encoding="utf-8") as f:
            json.dump(cur, f, ensure_ascii=False, indent=1)
        return True
    except Exception:
        return False


def is_video_name(name):
    return os.path.splitext(name)[1].lower() in VIDEO_EXTS


def skip_entry_name(name):
    if name == PROJECT_FILE:
        return True
    if name.endswith(".renamer.json"):
        return True
    if name.startswith("上传日志_") and name.endswith(".txt"):
        return True
    if name.startswith(RNM_TMP):
        return True
    return False


def list_entries(directory, include_dirs=False, videos_only=False):
    """当前目录的条目。include_dirs=False 时只有文件；True 时含子文件夹（仅一层）。
    videos_only=True 时文件只留视频，文件夹仍按 include_dirs 决定。"""
    recover_temp_files(directory)          # 上次异常残留的临时文件，先改回原名
    out = []
    try:
        names = os.listdir(directory)
    except Exception:
        return []
    for name in names:
        if skip_entry_name(name):
            continue
        path = os.path.join(directory, name)
        if os.path.isfile(path):
            if videos_only and not (is_video_name(name) or not os.path.splitext(name)[1]):
                continue
            out.append({"name": name, "kind": "file"})
        elif include_dirs and os.path.isdir(path):
            out.append({"name": name, "kind": "dir"})
    out.sort(key=lambda e: (0 if e["kind"] == "dir" else 1, natural_key(e["name"])))
    return out


def list_files(directory):
    return [e["name"] for e in list_entries(directory, False)]


def list_all_names(directory):
    """文件 + 文件夹，供还原匹配用，不受「包含文件夹」开关影响。"""
    return [e["name"] for e in list_entries(directory, True)]


def is_plain_file(path):
    """上传硬规则：必须是普通文件。文件夹、断掉的链接一律 False。"""
    try:
        return os.path.isfile(path) and not os.path.isdir(path)
    except Exception:
        return False


def _origin_name(v):
    if isinstance(v, dict):
        return v.get("original") or ""
    return v or ""


def _match_file(current, original, meta, by_lower, sizes, used):
    """在目标文件夹里找这个记录对应的实际文件：先当前名，再原名，再唯一大小。"""
    for name in (current, original):
        if not name:
            continue
        hit = by_lower.get(name.lower())
        if hit and hit.lower() not in used:
            return hit
    size = None
    if isinstance(meta, dict):
        size = meta.get("size")
    if size:
        cands = [f for f, s in sizes.items() if s == size and f.lower() not in used]
        if len(cands) == 1:
            return cands[0]
    return None


def remap_grid_files(grid, directory, origin, file_meta):
    """导入工程后，按当前文件夹把 grid.rows[].file 对到实际文件。"""
    if not grid or not isinstance(grid, dict):
        return grid, []
    files = list_all_names(directory)
    by_lower = {f.lower(): f for f in files}
    sizes = {}
    for f in files:
        try:
            sizes[f] = os.path.getsize(os.path.join(directory, f))
        except Exception:
            pass
    used, missing = set(), []
    origin = origin or {}
    file_meta = file_meta or {}
    for row in grid.get("rows") or []:
        old = row.get("file")
        if not old:
            continue
        first = _origin_name(origin.get(old, old))
        meta = file_meta.get(old) or {}
        found = _match_file(old, first, meta, by_lower, sizes, used)
        if found:
            row["file"] = found
            used.add(found.lower())
        else:
            missing.append(old)
            row["file"] = None
    return grid, missing


# ============================================================ 系统：资源管理器 / 剪贴板 / 对话框 / rclone
def _hidden_kw(timeout=45, env=None, text=True):
    """所有子进程都隐藏控制台，避免闪出黑色命令行窗口。"""
    kw = dict(timeout=timeout, env=env)
    if text:
        kw.update(capture_output=True, encoding="utf-8", errors="replace")
    else:
        kw["capture_output"] = True
    if os.name == "nt":
        kw["creationflags"] = CREATE_NO_WINDOW
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        kw["startupinfo"] = si
    return kw


def _ps(script, timeout=45):
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-STA", "-WindowStyle", "Hidden", "-Command", script],
            **_hidden_kw(timeout=timeout),
        )
        return (r.stdout or "").strip()
    except Exception:
        return ""


def clipboard_text():
    """用 Win32 API 读剪贴板，不启动 PowerShell，因此不会闪控制台。"""
    if os.name != "nt":
        return ""
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        CF_UNICODETEXT = 13
        if not user32.OpenClipboard(None):
            return ""
        try:
            handle = user32.GetClipboardData(CF_UNICODETEXT)
            if not handle:
                return ""
            kernel32.GlobalLock.restype = ctypes.c_void_p
            ptr = kernel32.GlobalLock(handle)
            if not ptr:
                return ""
            try:
                return ctypes.wstring_at(ptr)
            finally:
                kernel32.GlobalUnlock(handle)
        finally:
            user32.CloseClipboard()
    except Exception:
        return ""


def clipboard_path():
    text = (clipboard_text() or "").strip().strip('"').strip()
    if not text:
        return None
    text = text.splitlines()[0].strip().strip('"')
    if os.path.isdir(text):
        return text
    if os.path.isfile(text):
        return os.path.dirname(text)
    return None


_explorer_cache = ([], 0.0)
_explorer_lock = threading.Lock()


def explorer_folders():
    """读当前资源管理器打开的文件夹。结果缓存 2 秒，避免连打 PowerShell。"""
    global _explorer_cache
    now = time.time()
    cached, ts = _explorer_cache
    if now - ts < 2.0:
        return list(cached)
    if not _explorer_lock.acquire(blocking=False):
        return list(cached)
    try:
        cached, ts = _explorer_cache
        if time.time() - ts < 2.0:
            return list(cached)
        script = r"""
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false
$shell = New-Object -ComObject Shell.Application
$seen = @{}
foreach ($w in @($shell.Windows())) {
  try {
    $p = $w.Document.Folder.Self.Path
    if ($p -and -not $seen.ContainsKey($p) -and (Test-Path -LiteralPath $p -PathType Container)) {
      $seen[$p] = $true
      Write-Output $p
    }
  } catch {}
}
"""
        out = _ps(script, timeout=8)
        paths = []
        for line in (out or "").splitlines():
            p = line.strip()
            if p and os.path.isdir(p) and p not in paths:
                paths.append(p)
        _explorer_cache = (paths, time.time())
        return list(paths)
    finally:
        _explorer_lock.release()


def win_pick(kind, initial="", title="", filename=""):
    """kind: folder | open | save。用 WinForms 对话框，避免和 pywebview 抢 tk。"""
    initial = initial or os.getcwd()
    title = title or ""
    filename = filename or ""
    payload = json.dumps({
        "kind": kind, "initial": initial, "title": title, "filename": filename,
    }, ensure_ascii=False)
    tmp = os.path.join(os.environ.get("TEMP", "."), "renamer_dlg_%d.json" % os.getpid())
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(payload)
        script = r"""
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false
Add-Type -AssemblyName System.Windows.Forms
$cfg = Get-Content -LiteralPath $env:RENAMER_DLG -Encoding UTF8 | ConvertFrom-Json
if ($cfg.kind -eq 'folder') {
  $d = New-Object System.Windows.Forms.FolderBrowserDialog
  $d.Description = $cfg.title
  if ($cfg.initial) { $d.SelectedPath = $cfg.initial }
  $d.ShowNewFolderButton = $true
  if ($d.ShowDialog() -eq 'OK') { Write-Output $d.SelectedPath }
} elseif ($cfg.kind -eq 'open') {
  $d = New-Object System.Windows.Forms.OpenFileDialog
  $d.Title = $cfg.title
  $d.Filter = '重命名工程 (*.renamer.json;*.json)|*.renamer.json;*.json|所有文件 (*.*)|*.*'
  if ($cfg.initial) { $d.InitialDirectory = $cfg.initial }
  $d.CheckFileExists = $true
  if ($d.ShowDialog() -eq 'OK') { Write-Output $d.FileName }
} else {
  $d = New-Object System.Windows.Forms.SaveFileDialog
  $d.Title = $cfg.title
  $d.Filter = '重命名工程 (*.renamer.json)|*.renamer.json|JSON (*.json)|*.json|所有文件 (*.*)|*.*'
  $d.AddExtension = $true
  $d.DefaultExt = 'renamer.json'
  if ($cfg.initial) { $d.InitialDirectory = $cfg.initial }
  if ($cfg.filename) { $d.FileName = $cfg.filename }
  $d.OverwritePrompt = $true
  if ($d.ShowDialog() -eq 'OK') { Write-Output $d.FileName }
}
"""
        env = os.environ.copy()
        env["RENAMER_DLG"] = tmp
        r = subprocess.run(
            ["powershell", "-NoProfile", "-STA", "-WindowStyle", "Hidden", "-Command", script],
            **_hidden_kw(timeout=300, env=env),
        )
        path = (r.stdout or "").strip()
        return path if path else None
    except Exception:
        return None
    finally:
        try:
            os.remove(tmp)
        except Exception:
            pass


def find_rclone():
    for p in RCLONE_CANDIDATES:
        if p and os.path.isfile(p):
            return p
    return shutil.which("rclone")


def drive_folder_id(url):
    if not url:
        return None
    u = str(url).strip().replace("&amp;", "&")
    m = re.search(r"[?&]q=([^&]+)", u)
    if "google.com/url" in u and m:
        try:
            from urllib.parse import unquote
            u = unquote(m.group(1))
        except Exception:
            pass
    m = re.search(r"(?:id=|/folders/)([A-Za-z0-9_-]{10,})", u)
    return m.group(1) if m else None


def rclone_has_remote(rclone, name):
    try:
        r = subprocess.run([rclone, "listremotes"], **_hidden_kw(timeout=20))
        text = (r.stdout or "") + (r.stderr or "")
        return any(line.strip() == (name + ":") for line in text.splitlines())
    except Exception:
        return False


def verify_upload(rclone, remote, folder_id, filename, local_size):
    try:
        r = subprocess.run(
            [rclone, "lsjson", remote + ":",
             "--drive-root-folder-id", folder_id,
             "--files-only", "--no-modtime"],
            **_hidden_kw(timeout=90, text=False),
        )
        if r.returncode != 0:
            return False, "读回目标文件夹失败 (rclone lsjson 返回码 %s)" % r.returncode
        raw = (r.stdout or b"").decode("utf-8", "replace").strip()
        if not raw:
            return False, "读回目标文件夹为空（权限问题或文件夹 ID 不对）"
        try:
            items = json.loads(raw)
        except Exception as e:
            return False, "读回结果不是合法 JSON：%s" % e
        same = [it for it in items if it.get("Name") == filename]
        if not same:
            return False, "目标文件夹里没有找到同名文件：" + filename
        if len(same) > 1:
            return False, "目标文件夹里有 %d 个同名文件，数量异常：%s" % (len(same), filename)
        remote_item = same[0]
        size = remote_item.get("Size")
        if size is None or int(size) != int(local_size):
            return False, "大小不一致：本地 %s 字节，远端 %s 字节" % (local_size, size)
        rid = remote_item.get("ID") or remote_item.get("Id")
        if not rid:
            return False, "读回结果里没有文件 ID"
        return True, rid
    except Exception as e:
        return False, "校验出错：%s" % e


class Uploader:
    def __init__(self):
        self.lock = threading.Lock()
        self.state = {
            "running": False, "done": False, "log": [],
            "ok": 0, "fail": 0, "plan": 0, "current": "", "message": "",
            "results": [],
        }

    def snapshot(self):
        with self.lock:
            s = dict(self.state)
            s["log"] = list(self.state["log"])
            s["results"] = [dict(x) for x in (self.state.get("results") or [])]
            return s

    def _log(self, line):
        with self.lock:
            self.state["log"].append(line)
            if len(self.state["log"]) > 800:
                self.state["log"] = self.state["log"][-800:]

    def _result(self, name, status, **kw):
        rec = {
            "file": name, "status": status,
            "id": kw.get("id") or "", "reason": kw.get("reason") or "",
        }
        with self.lock:
            found = False
            results = self.state.setdefault("results", [])
            for i, x in enumerate(results):
                if x.get("file") == name:
                    results[i] = rec
                    found = True
                    break
            if not found:
                results.append(rec)
            if status == "uploading":
                self.state["current"] = name

    def start(self, directory, items):
        with self.lock:
            if self.state.get("running"):
                return False, "已有上传任务在进行。"
            self.state = {
                "running": True, "done": False, "log": [],
                "ok": 0, "fail": 0, "plan": len(items), "current": "", "message": "",
                "results": [{"file": it["file"], "status": "queued", "id": "", "reason": ""} for it in items],
            }
        t = threading.Thread(target=self._run, args=(directory, items), daemon=True)
        t.start()
        return True, ""

    def _run(self, directory, items):
        rclone = find_rclone()
        log_path = os.path.join(directory, "上传日志_" + time.strftime("%Y%m%d_%H%M%S") + ".txt")
        fails = []
        try:
            if not rclone:
                self._log("未找到 rclone.exe。请把它放到工具目录，或放到 D:\\0_Dev\\下载图片\\Upload\\")
                for it in items:
                    self._result(it["file"], "fail", reason="未找到 rclone.exe")
                with self.lock:
                    self.state["message"] = "未找到 rclone.exe"
                    self.state["fail"] = len(items)
                return
            if not rclone_has_remote(rclone, RCLONE_REMOTE):
                self._log("rclone 还没有配置名为 %s 的 Google Drive 远程。请先运行 rclone config。" % RCLONE_REMOTE)
                for it in items:
                    self._result(it["file"], "fail", reason="rclone 未配置 gdrive")
                with self.lock:
                    self.state["message"] = "rclone 未配置 gdrive 远程"
                    self.state["fail"] = len(items)
                return
            self._log("rclone: " + rclone)
            ok_count = 0
            for i, it in enumerate(items, 1):
                name, fid = it["file"], it["id"]
                src = os.path.join(directory, name)
                self._result(name, "uploading")
                self._log("[%d/%d] 上传 %s" % (i, len(items), name))
                if not is_plain_file(src):
                    fails.append(name)
                    self._result(name, "fail", reason="不是文件（文件夹不上传）")
                    self._log("  跳过：不是文件（文件夹不会上传）")
                    continue
                local_size = os.path.getsize(src)
                cmd = [
                    rclone, "copy", src, RCLONE_REMOTE + ":",
                    "--drive-root-folder-id", fid,
                    "--local-encoding", "None",
                    "--drive-chunk-size", "64M",
                    "--stats", "10s",
                    "--stats-one-line",
                ]
                try:
                    r = subprocess.run(cmd, **_hidden_kw(timeout=3600 * 6, text=False))
                    code = r.returncode
                except Exception as e:
                    fails.append(name)
                    self._result(name, "fail", reason=str(e))
                    self._log("  失败：%s" % e)
                    try:
                        with open(log_path, "a", encoding="utf-8") as f:
                            f.write("%s  失败  %s  原因: %s\n" % (time.strftime("%H:%M:%S"), name, e))
                    except Exception:
                        pass
                    continue
                if code != 0:
                    fails.append(name)
                    self._result(name, "fail", reason="rclone 返回码 %s" % code)
                    self._log("  失败：rclone 返回码 %s" % code)
                    try:
                        with open(log_path, "a", encoding="utf-8") as f:
                            f.write("%s  失败  %s  原因: rclone 返回码 %s\n" % (time.strftime("%H:%M:%S"), name, code))
                    except Exception:
                        pass
                    continue
                self._log("  校验：正在读回目标文件夹…")
                good, detail = verify_upload(rclone, RCLONE_REMOTE, fid, name, local_size)
                if good:
                    ok_count += 1
                    self._result(name, "ok", id=str(detail))
                    self._log("  完成：文件ID %s，%s 字节" % (detail, local_size))
                    try:
                        with open(log_path, "a", encoding="utf-8") as f:
                            f.write("%s  成功  %s  文件ID: %s  大小: %s\n" % (
                                time.strftime("%H:%M:%S"), name, detail, local_size))
                    except Exception:
                        pass
                else:
                    fails.append(name)
                    self._result(name, "fail", reason=str(detail))
                    self._log("  失败：校验未通过 — %s" % detail)
                    try:
                        with open(log_path, "a", encoding="utf-8") as f:
                            f.write("%s  失败  %s  原因: 校验未通过 - %s\n" % (
                                time.strftime("%H:%M:%S"), name, detail))
                    except Exception:
                        pass
                with self.lock:
                    self.state["ok"] = ok_count
                    self.state["fail"] = len(fails)
            self._log("完成：成功 %d / %d。日志：%s" % (ok_count, len(items), log_path))
            with self.lock:
                self.state["ok"] = ok_count
                self.state["fail"] = len(fails)
                self.state["message"] = log_path
        finally:
            with self.lock:
                self.state["running"] = False
                self.state["done"] = True
                self.state["current"] = ""


DRIVE_ID = r"[A-Za-z0-9_-]{15,}"
LINK_PATTERNS = [
    (r"/file/d/(%s)" % DRIVE_ID, "file"),
    (r"/document/d/(%s)" % DRIVE_ID, "file"),
    (r"/spreadsheets/d/(%s)" % DRIVE_ID, "file"),
    (r"/presentation/d/(%s)" % DRIVE_ID, "file"),
    (r"/folders/(%s)" % DRIVE_ID, "folder"),
    (r"/drive/folders/(%s)" % DRIVE_ID, "folder"),
    (r"[?&]id=(%s)" % DRIVE_ID, "unknown"),
]


def drive_link_info(url):
    """把用户粘贴的各种网盘网址解析成 {kind, id}。
    kind: file / folder / unknown（unknown 需要再用 rclone 探一下）。
    认得：/file/d/ID、/folders/ID、open?id=ID、uc?export=download&id=ID、
         google.com/url?q=… 包装、以及直接粘一个裸 ID。"""
    u = str(url or "").strip().replace("&amp;", "&").strip('"').strip()
    if not u:
        return {"kind": "", "id": ""}
    m = re.search(r"[?&]q=([^&]+)", u)
    if "google.com/url" in u and m:
        try:
            from urllib.parse import unquote
            u = unquote(m.group(1))
        except Exception:
            pass
    for pat, kind in LINK_PATTERNS:
        hit = re.search(pat, u)
        if hit:
            if kind == "unknown" and ("/uc" in u or "export=download" in u):
                kind = "file"
            return {"kind": kind, "id": hit.group(1)}
    if re.fullmatch(DRIVE_ID, u):
        return {"kind": "unknown", "id": u}
    return {"kind": "", "id": ""}


def rclone_list_folder(rclone, fid, timeout=120):
    """列出一个网盘文件夹。返回 (ok, items, message)。"""
    try:
        r = subprocess.run(
            [rclone, "lsjson", RCLONE_REMOTE + ":",
             "--drive-root-folder-id", fid, "--no-modtime"],
            **_hidden_kw(timeout=timeout, text=False),
        )
    except Exception as e:
        return False, [], str(e)
    out = (r.stdout or b"").decode("utf-8", "replace").strip()
    err = (r.stderr or b"").decode("utf-8", "replace").strip()
    if r.returncode != 0:
        return False, [], (err.splitlines() or ["rclone 返回码 %s" % r.returncode])[-1]
    if not out:
        return True, [], ""
    try:
        items = json.loads(out)
    except Exception as e:
        return False, [], "读回结果不是合法 JSON：%s" % e
    return True, items, ""


def fmt_bytes(n):
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return ("%.0f %s" % (n, unit)) if unit == "B" else ("%.1f %s" % (n, unit))
        n /= 1024.0
    return "%.1f TB" % n


def dedupe_path(directory, name):
    """本地重名时自动加序号，绝不覆盖已有文件。名字必须落在 directory 里面。"""
    name = sanitize(name or "")
    if not entry_name_ok(name):
        name = "下载文件"
    target = os.path.join(directory, name)
    if not os.path.exists(target):
        return target
    root, ext = os.path.splitext(name)
    i = 2
    while True:
        cand = os.path.join(directory, "%s (%d)%s" % (root, i, ext))
        if not os.path.exists(cand):
            return cand
        i += 1


_REAL_EXT_RE = re.compile(r"^\.[A-Za-z0-9]{1,7}$")


def _split_real_ext(filename):
    """只把纯英文数字后缀（如 .mp4/.jpg/.png）当成真正的扩展名；
    像『34.测试子涵-8』『小夏41已使用_2.已使用_1』这种点号后面带中文或下划线的，属于主文件名而非扩展名！"""
    base, own = os.path.splitext(filename or "")
    if own and not _REAL_EXT_RE.match(own):
        return filename or "", ""
    return base, own


def _sniff_media_ext(file_path):
    """当网盘原文件没有带后缀名时，通过文件头魔数自动识别真实媒体格式（.mp4 / .jpg / .png / .webp / .mkv）。"""
    if not file_path or not os.path.isfile(file_path):
        return ""
    try:
        with open(file_path, "rb") as f:
            hdr = f.read(32)
        if len(hdr) >= 12 and hdr[4:8] in (b"ftyp", b"moov", b"wide", b"mdat"):
            return ".mp4"
        if hdr.startswith(b"\xff\xd8\xff"):
            return ".jpg"
        if hdr.startswith(b"\x89PNG\r\n\x1a\n"):
            return ".png"
        if len(hdr) >= 12 and hdr[:4] == b"RIFF" and hdr[8:12] == b"WEBP":
            return ".webp"
        if hdr.startswith(b"\x1a\x45\xdf\xa3"):
            return ".mkv"
    except Exception:
        pass
    return ""


def final_download_name(stem, remote_name, keep_ext=True, ext="", ext_fill=True, src_path=None):
    """和改名表同一套规则：表格拼出来的名字（不含扩展名）+ 扩展名。
    ext 非空    = 统一指定扩展名（比如网盘上传的人没带后缀，下载时补 .mp4）
    ext_fill    = True 只补给本来就没有扩展名的文件；False 一律换成 ext
    表格里这一行全空 -> 用网盘里的原名（必要时给它补上扩展名）。"""
    remote_name = sanitize(remote_name or "") or "下载文件"
    base, own = _split_real_ext(remote_name)
    if not own and src_path:
        own = _sniff_media_ext(src_path)
    want = sanitize(stem or "")
    if not want:
        if keep_ext and ext and not (own and ext_fill):
            return base + ext
        if keep_ext and own and not remote_name.lower().endswith(own.lower()):
            return base + own
        return remote_name
    if not keep_ext:
        return want
    if ext and not (own and ext_fill):
        return want + ext
    return want + own


class Downloader:
    """网盘下载引擎。
    · 用 rclone 的 gdrive 远程（和上传同一个已授权的远程），私有文件夹也能下。
    · 每一行 = 一个网盘文件 ID，各自独立的临时目录，可以并发下载。
    · 每个文件独立命名、独立重试、独立报状态；本地重名自动加序号，绝不覆盖。
    """

    def __init__(self):
        self.lock = threading.Lock()
        self.name_lock = threading.Lock()
        self.cancel = threading.Event()
        self.state = self._blank()

    def _blank(self):
        return {
            "running": False, "done": False, "log": [],
            "ok": 0, "fail": 0, "plan": 0, "current": "", "message": "",
            "results": [],
        }

    def snapshot(self):
        with self.lock:
            s = dict(self.state)
            s["log"] = list(self.state["log"])
            s["results"] = [dict(x) for x in (self.state.get("results") or [])]
            return s

    def _log(self, line):
        with self.lock:
            self.state["log"].append(line)
            if len(self.state["log"]) > 800:
                self.state["log"] = self.state["log"][-800:]

    def _set(self, key, status=None, **kw):
        with self.lock:
            results = self.state.setdefault("results", [])
            for i, x in enumerate(results):
                if x.get("key") == key:
                    rec = dict(x)
                    break
            else:
                rec = {"key": key, "status": "queued", "saved": "", "reason": "", "pct": 0}
                results.append(rec)
                i = len(results) - 1
            if status:
                rec["status"] = status
            for k, v in kw.items():
                rec[k] = v
            results[i] = rec

    def _tick_current(self, active):
        with self.lock:
            self.state["current"] = "　".join(active) if active else ""

    def start(self, dest, items, workers=3):
        with self.lock:
            if self.state.get("running"):
                return False, "已有下载任务在进行。"
            self.cancel.clear()
            self.state = self._blank()
            self.state.update({
                "running": True, "plan": len(items),
                "results": [{"key": it["key"], "status": "queued", "saved": "",
                             "reason": "", "pct": 0} for it in items],
            })
        t = threading.Thread(target=self._run, args=(dest, items, workers), daemon=True)
        t.start()
        return True, ""

    def stop(self):
        self.cancel.set()

    # ---------------------------------------------------------------- 单个文件
    def _one(self, rclone, dest, tmp_root, it, idx, total, active):
        key = it["key"]
        fid = it.get("id") or ""
        want = it.get("stem") or ""
        label = want or it.get("name") or fid
        tmp_dir = os.path.join(tmp_root, str(idx))
        last_err = "未知错误"
        for attempt in range(1, 4):                      # 最多试 3 次，网络抖动不至于整行失败
            if self.cancel.is_set():
                self._set(key, "fail", reason="已取消")
                return False
            self._set(key, "downloading", pct=0,
                      reason=("第 %d 次重试…" % (attempt - 1)) if attempt > 1 else "")
            with self.lock:
                active[key] = label
            self._tick_current(list(active.values()))
            try:
                shutil.rmtree(tmp_dir, ignore_errors=True)
                os.makedirs(tmp_dir, exist_ok=True)
            except Exception as e:
                last_err = "建不了临时文件夹：%s" % e
                break
            cmd = [
                rclone, "backend", "copyid", RCLONE_REMOTE + ":", fid,
                tmp_dir.replace("\\", "/") + "/",
                "--drive-acknowledge-abuse",
                "--transfers", "1", "--checkers", "2",
                "--low-level-retries", "10", "--retries", "1",
                "--stats", "1s", "--stats-one-line",
            ]
            err_tail = []
            code = -1
            try:
                kw = dict(stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          universal_newlines=True, encoding="utf-8", errors="replace")
                if os.name == "nt":
                    kw["creationflags"] = CREATE_NO_WINDOW
                    si = subprocess.STARTUPINFO()
                    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                    kw["startupinfo"] = si
                proc = subprocess.Popen(cmd, **kw)
                for line in proc.stdout:                  # 边下边读，进度实时回传界面
                    line = (line or "").strip()
                    if not line:
                        continue
                    err_tail.append(line)
                    if len(err_tail) > 12:
                        err_tail.pop(0)
                    m = re.search(r"(\d{1,3})%", line)
                    if m:
                        self._set(key, "downloading", pct=min(100, int(m.group(1))))
                    if self.cancel.is_set():
                        try:
                            proc.kill()
                        except Exception:
                            pass
                        break
                code = proc.wait(timeout=30)
            except Exception as e:
                last_err = str(e)
                code = -1
            if self.cancel.is_set():
                self._set(key, "fail", reason="已取消")
                return False
            got = []
            try:
                got = [n for n in os.listdir(tmp_dir)
                       if os.path.isfile(os.path.join(tmp_dir, n))]
            except Exception:
                got = []
            if code != 0 or not got:
                if code != 0:
                    real = [x for x in err_tail if "ERROR" in x or "Failed" in x or "error" in x]
                    last_err = (real or err_tail or ["rclone 返回码 %s" % code])[-1][:200]
                else:
                    last_err = "没有下载到文件（Google 文档这类在线格式需要先导出成 mp4/pdf 再下）"
                    break                                 # 这种重试也没用
                self._log("  第 %d 次失败：%s" % (attempt, last_err))
                time.sleep(2 * attempt)
                continue
            remote_name = got[0]
            src = os.path.join(tmp_dir, remote_name)
            try:
                size = os.path.getsize(src)
            except Exception:
                size = 0
            want_size = it.get("size") or 0
            if want_size and size and int(size) != int(want_size):
                last_err = "大小不一致：网盘 %s 字节，下载到 %s 字节" % (want_size, size)
                self._log("  第 %d 次失败：%s" % (attempt, last_err))
                time.sleep(2 * attempt)
                continue
            final = final_download_name(want, remote_name, it.get("keepExt", True),
                                        it.get("ext") or "", it.get("extFill") is not False, src_path=src)
            with self.name_lock:                          # 并发时也不会两行抢同一个名字
                target = dedupe_path(dest, final)
                try:
                    os.replace(src, target)
                except Exception as e:
                    last_err = "保存失败：%s" % e
                    target = None
            if not target:
                break
            saved = os.path.basename(target)
            self._set(key, "ok", saved=saved, pct=100, reason="", remote=remote_name, size=size)
            self._log("[%d/%d] 完成 %s（%s，网盘原名 %s）" % (idx + 1, total, saved, fmt_bytes(size), remote_name))
            self._write_log_line(dest, "成功", saved, "网盘原名: %s  大小: %s" % (remote_name, size))
            shutil.rmtree(tmp_dir, ignore_errors=True)
            with self.lock:
                active.pop(key, None)
                self.state["ok"] = self.state.get("ok", 0) + 1
            self._tick_current(list(active.values()))
            return True
        self._set(key, "fail", reason=last_err, pct=0)
        self._log("[%d/%d] 失败 %s：%s" % (idx + 1, total, label, last_err))
        self._write_log_line(dest, "失败", label, "原因: " + last_err)
        shutil.rmtree(tmp_dir, ignore_errors=True)
        with self.lock:
            active.pop(key, None)
            self.state["fail"] = self.state.get("fail", 0) + 1
        self._tick_current(list(active.values()))
        return False

    def _write_log_line(self, dest, kind, name, extra):
        try:
            with open(os.path.join(dest, self.log_name), "a", encoding="utf-8") as f:
                f.write("%s  %s  %s  %s\n" % (time.strftime("%H:%M:%S"), kind, name, extra))
        except Exception:
            pass

    # ---------------------------------------------------------------- 整批
    def _run(self, dest, items, workers):
        rclone = find_rclone()
        self.log_name = "下载日志_" + time.strftime("%Y%m%d_%H%M%S") + ".txt"
        tmp_root = os.path.join(dest, ".rcdl_tmp")
        active = {}
        try:
            if not rclone:
                for it in items:
                    self._set(it["key"], "fail", reason="未找到 rclone.exe")
                with self.lock:
                    self.state["message"] = "未找到 rclone.exe"
                    self.state["fail"] = len(items)
                return
            if not rclone_has_remote(rclone, RCLONE_REMOTE):
                for it in items:
                    self._set(it["key"], "fail", reason="rclone 未配置 gdrive 远程")
                with self.lock:
                    self.state["message"] = "rclone 未配置 gdrive 远程"
                    self.state["fail"] = len(items)
                return
            self._log("rclone: " + rclone)
            self._log("保存到: " + dest)
            self._log("同时下载 %d 个，共 %d 个文件" % (workers, len(items)))
            shutil.rmtree(tmp_root, ignore_errors=True)
            os.makedirs(tmp_root, exist_ok=True)
            total = len(items)
            try:
                from concurrent.futures import ThreadPoolExecutor
                with ThreadPoolExecutor(max_workers=max(1, min(8, int(workers or 3)))) as pool:
                    list(pool.map(lambda p: self._one(rclone, dest, tmp_root, p[1], p[0], total, active),
                                  list(enumerate(items))))
            except Exception as e:
                self._log("下载过程出错：%s" % e)
            with self.lock:
                ok_n, fail_n = self.state.get("ok", 0), self.state.get("fail", 0)
            self._log("完成：成功 %d / %d。" % (ok_n, total))
            with self.lock:
                self.state["message"] = os.path.join(dest, self.log_name) if (ok_n or fail_n) else ""
        finally:
            shutil.rmtree(tmp_root, ignore_errors=True)
            with self.lock:
                self.state["running"] = False
                self.state["done"] = True
                self.state["current"] = ""

# ============================================================ 批量新建文件夹
WIN_RESERVED = {"CON", "PRN", "AUX", "NUL"} | {"COM%d" % i for i in range(1, 10)} | {"LPT%d" % i for i in range(1, 10)}


def check_folder_name(name):
    """返回 (合法的名字, 错误原因)。和改名用同一个 sanitize，再加上 Windows 的保留名检查。"""
    n = sanitize(name or "")
    if not n:
        return None, "名字是空的"
    if n in (".", ".."):
        return None, "名字不合法"
    if n.split(".")[0].strip().upper() in WIN_RESERVED:
        return None, "「%s」是 Windows 的保留名，不能当文件夹名" % n
    if len(n) > 240:
        return None, "名字太长（超过 240 个字）"
    return n, ""


def make_folders(dest, items):
    """逐个创建。已存在的文件夹跳过；同名文件、非法名字报失败；绝不覆盖、绝不删除任何东西。"""
    out = []
    for it in items:
        key = it.get("key") or ""
        n, why = check_folder_name(it.get("name"))
        if not n:
            out.append({"key": key, "status": "fail", "name": it.get("name") or "", "reason": why})
            continue
        target = os.path.join(dest, n)
        if os.path.isdir(target):
            out.append({"key": key, "status": "exists", "name": n, "reason": ""})
            continue
        if os.path.exists(target):
            out.append({"key": key, "status": "fail", "name": n, "reason": "这里已经有一个同名的文件"})
            continue
        try:
            os.mkdir(target)
            out.append({"key": key, "status": "ok", "name": n, "reason": ""})
        except OSError as e:
            out.append({"key": key, "status": "fail", "name": n, "reason": str(e.strerror or e)})
    return out


def remove_empty_folders(dest, names):
    """撤销刚才的创建：只删除仍然是空的文件夹；里面放了东西的一律保留。"""
    removed, kept = [], []
    for n in names or []:
        n, _ = check_folder_name(n)
        if not n:
            continue
        p = os.path.join(dest, n)
        if not os.path.isdir(p):
            continue
        try:
            os.rmdir(p)                      # rmdir 只能删空文件夹，天然安全
            removed.append(n)
        except OSError:
            kept.append(n)
    return removed, kept


# ============================================================ 一键连接 Google Drive
def _rclone_run(rclone, args, timeout=60):
    try:
        r = subprocess.run([rclone] + args, **_hidden_kw(timeout=timeout))
        return r.returncode, (r.stdout or ""), (r.stderr or "")
    except Exception as e:
        return -1, "", str(e)


def _install_drive_token(rclone, token):
    """把令牌写进 rclone 自己的配置文件。令牌不进命令行，避免出现在进程列表里。"""
    if not token or any(c in token for c in ("\n", "\r")):
        return False
    path = rclone_config_file(rclone)
    if not path:
        return False
    if rclone_has_remote(rclone, RCLONE_REMOTE):
        _rclone_run(rclone, ["config", "delete", RCLONE_REMOTE], 30)
    folder = os.path.dirname(path)
    try:
        if folder:
            os.makedirs(folder, exist_ok=True)
        with open(path, "a", encoding="utf-8", newline="\n") as f:
            f.write(
                "\n[%s]\ntype = drive\nscope = drive\ntoken = %s\n"
                "config_refresh_token = false\n" % (RCLONE_REMOTE, token)
            )
    except OSError:
        return False
    return rclone_has_remote(rclone, RCLONE_REMOTE)


def rclone_config_file(rclone):
    code, out, err = _rclone_run(rclone, ["config", "file"], 20)
    lines = [x.strip() for x in (out + "\n" + err).splitlines() if x.strip()]
    for x in reversed(lines):
        if x.lower().endswith(".conf") or os.path.sep in x or "/" in x:
            return x
    return ""


def rclone_drive_about(rclone, remote=None):
    """读一次网盘容量，顺便证明连接真的可用。返回 (ok, info_or_message)。"""
    remote = remote or RCLONE_REMOTE
    code, out, err = _rclone_run(rclone, ["about", remote + ":", "--json"], 60)
    if code == 0:
        try:
            d = json.loads(out or "{}")
            return True, {"total": d.get("total") or 0, "used": d.get("used") or 0,
                          "free": d.get("free") or 0, "trashed": d.get("trashed") or 0}
        except Exception:
            return True, {}
    code2, out2, err2 = _rclone_run(rclone, ["lsjson", remote + ":", "--max-depth", "1", "--dirs-only"], 60)
    if code2 == 0:
        return True, {}
    tail = [x for x in (err or err2 or "").splitlines() if x.strip()]
    return False, (tail[-1] if tail else "rclone 返回码 %s" % code)[:240]


def _extract_token(text):
    """从 rclone authorize 的输出里取出令牌（兼容 JSON 和新版的 base64 包装）。"""
    m = re.search(r"--->\s*(.+?)\s*<---", text, re.S)
    blob = (m.group(1) if m else "").strip()
    if not blob:
        return None
    cands = [blob]
    try:
        import base64
        pad = "=" * (-len(blob) % 4)
        cands.append(base64.urlsafe_b64decode(blob + pad).decode("utf-8", "replace"))
    except Exception:
        pass
    for c in cands:
        try:
            d = json.loads(c)
        except Exception:
            continue
        if isinstance(d, dict):
            if d.get("access_token") or d.get("refresh_token"):
                return json.dumps(d, separators=(",", ":"))
            inner = d.get("token")
            if isinstance(inner, str) and ("access_token" in inner or "refresh_token" in inner):
                return inner
            if isinstance(inner, dict):
                return json.dumps(inner, separators=(",", ":"))
    return None


class DriveSetup:
    """后台完成全部配置：找到 rclone → 浏览器里授权 → 写入 gdrive 远程 → 读一次容量验证。
    令牌只交给 rclone 写进它自己的配置文件，不写日志、不回传给界面。"""

    STEPS = ["准备", "浏览器授权", "保存连接", "测试连接"]

    def __init__(self):
        self.lock = threading.Lock()
        self.proc = None
        self.cancelled = False
        self.state = {"running": False, "done": False, "ok": False, "step": 0,
                      "message": "", "error": "", "authUrl": "", "about": {}}

    def snapshot(self):
        with self.lock:
            return dict(self.state)

    def _set(self, **kw):
        with self.lock:
            self.state.update(kw)

    def start(self):
        with self.lock:
            if self.state.get("running"):
                return False
            self.cancelled = False
            self.state = {"running": True, "done": False, "ok": False, "step": 1,
                          "message": "正在准备…", "error": "", "authUrl": "", "about": {}}
        threading.Thread(target=self._run, daemon=True).start()
        return True

    def cancel(self):
        self.cancelled = True
        p = self.proc
        if p:
            try:
                p.kill()
            except Exception:
                pass

    def _fail(self, msg):
        self._set(running=False, done=True, ok=False, error=msg, message="")

    def _run(self):
        try:
            rclone = find_rclone()
            if not rclone:
                return self._fail("没有找到 rclone.exe。请重新运行「安装.bat」，它会把 rclone 一起装好。")
            # ---- 第 2 步：浏览器授权（rclone 会自己打开浏览器）
            self._set(step=2, message="已经打开浏览器：登录你的 Google 账号，然后点「允许」。")
            kw = dict(stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                      universal_newlines=True, encoding="utf-8", errors="replace")
            if os.name == "nt":
                kw["creationflags"] = CREATE_NO_WINDOW
                si = subprocess.STARTUPINFO()
                si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                kw["startupinfo"] = si
            self.proc = subprocess.Popen([rclone, "authorize", "drive"], **kw)
            buf = []
            t0 = time.time()
            watchdog = threading.Timer(600, self.cancel)      # 10 分钟没完成就放弃
            watchdog.daemon = True
            watchdog.start()
            try:
                for line in self.proc.stdout:
                    buf.append(line)
                    m = re.search(r"(https?://127\.0\.0\.1:\d+/auth\S*)", line)
                    if m:
                        self._set(authUrl=m.group(1))
                    if "Got code" in line:
                        self._set(message="浏览器那边已经授权，正在收尾…")
                self.proc.wait(timeout=30)
            finally:
                watchdog.cancel()
            code = self.proc.returncode
            self.proc = None
            if self.cancelled:
                return self._fail("已取消。随时可以再点一次「一键连接」。" if time.time() - t0 < 590
                                  else "等了 10 分钟还没在浏览器里完成授权，已自动取消。")
            text = "".join(buf)
            token = _extract_token(text)
            if code != 0 or not token:
                low = text.lower()
                if "bind" in low or "address already in use" in low or "53682" in low and "fail" in low:
                    return self._fail("授权用的本机端口被占用了（可能上次的授权窗口还开着）。关掉多余的 rclone 窗口后再试一次。")
                return self._fail("浏览器授权没有完成。请关掉多余的授权窗口后再试一次。")
            # ---- 第 3 步：写入配置（先授权成功，才动旧配置，取消不会弄坏原来的连接）
            self._set(step=3, message="正在保存连接…")
            if not _install_drive_token(rclone, token):
                return self._fail("保存连接失败。rclone 的配置文件没有写成。")
            # ---- 第 4 步：测试
            self._set(step=4, message="正在测试连接…")
            ok, info = rclone_drive_about(rclone)
            if not ok:
                return self._fail("连接已保存，但测试没通过：%s" % info)
            self._set(running=False, done=True, ok=True, step=4, about=info or {},
                      message="连接成功", error="")
        except Exception as e:
            self._fail("出错了：%s" % e)


# ============================================================ HTTP 服务
class App:
    def __init__(self, directory):
        self.dir = directory
        self.project = Project(directory)
        self.undo_stack = []
        self.lock = threading.Lock()
        self.uploader = Uploader()
        self.downloader = Downloader()
        self.drive = DriveSetup()

    def _flag(self, payload, key, default=False):
        if payload and key in payload:
            return bool(payload.get(key))
        prefs = load_prefs()
        if key in prefs:
            return bool(prefs.get(key))
        g = self.project.data.get("grid") or {}
        if key in g:
            return bool(g.get(key))
        return bool(default)

    def _include_dirs_flag(self, payload=None):
        return self._flag(payload, "includeDirs", False)

    def _videos_only_flag(self, payload=None):
        return self._flag(payload, "videosOnly", True)

    def _listing(self, payload=None):
        inc = self._include_dirs_flag(payload)
        vid = self._videos_only_flag(payload)
        entries = list_entries(self.dir, inc, vid)
        return {
            "files": [e["name"] for e in entries],
            "entries": entries,
            "includeDirs": inc,
            "videosOnly": vid,
            # 即使当前不显示文件夹，也带回目录名，方便关掉开关时清掉残留空行
            "dirNames": [e["name"] for e in list_entries(self.dir, True, False) if e["kind"] == "dir"],
        }

    def _init_payload(self, payload=None):
        self.project.reload_if_newer()
        listing = self._listing(payload)
        return {
            "dir": self.dir,
            "files": listing["files"],
            "entries": listing["entries"],
            "includeDirs": listing["includeDirs"],
            "videosOnly": listing["videosOnly"],
            "prefs": load_prefs(),
            "grid": self.project.data.get("grid"),
            "restorable": len(self.project.restore_plan()),
            "updated": self.project.data.get("updated", ""),
            "history": len(self.project.data.get("history") or []),
            "snap_index": self.project.snap_index(),
            "snapshots": self.project.snapshot_list(),
            "rclone": bool(find_rclone()),
            "gdrive": bool(find_rclone()) and rclone_has_remote(find_rclone(), RCLONE_REMOTE),
        }

    def _switch_dir(self, new_dir, payload=None):
        new_dir = os.path.abspath(new_dir)
        if not os.path.isdir(new_dir):
            return {"ok": False, "message": "文件夹不存在：%s" % new_dir}
        try:
            self.project.save()
        except Exception:
            pass
        self.dir = new_dir
        self.project = Project(new_dir)
        self.undo_stack = []
        data = self._init_payload(payload)
        data["ok"] = True
        return data

    def api(self, path, payload):
        if path == "/api/uploadstatus":
            return self.uploader.snapshot()
        if path == "/api/downloadstatus":
            return self.downloader.snapshot()
        if path == "/api/mklist":
            dest = (payload.get("dest") or self.dir).strip().strip('"')
            if not os.path.isdir(dest):
                return {"ok": False, "message": "文件夹不存在：%s" % dest, "names": []}
            try:
                names = os.listdir(dest)
            except Exception as e:
                return {"ok": False, "message": str(e), "names": []}
            return {"ok": True, "names": names}
        if path == "/api/mkdirs":
            dest = (payload.get("dest") or self.dir).strip().strip('"')
            if not os.path.isdir(dest):
                return {"ok": False, "message": "要建在哪里的那个文件夹不存在：%s" % dest}
            items = payload.get("items") or []
            if not items:
                return {"ok": False, "message": "没有要创建的文件夹。"}
            res = make_folders(dest, items)
            try:
                made = [r["name"] for r in res if r["status"] == "ok"]
                if made:
                    with open(os.path.join(dest, "新建文件夹日志.txt"), "a", encoding="utf-8") as f:
                        f.write("%s  新建 %d 个：%s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), len(made), "、".join(made)))
            except Exception:
                pass
            return {"ok": True, "results": res}
        if path == "/api/mkundo":
            dest = (payload.get("dest") or self.dir).strip().strip('"')
            if not os.path.isdir(dest):
                return {"ok": False, "message": "文件夹不存在：%s" % dest}
            removed, kept = remove_empty_folders(dest, payload.get("names") or [])
            return {"ok": True, "removed": removed, "kept": kept}
        if path == "/api/openpath":
            pth = (payload.get("path") or "").strip().strip('"')
            if not os.path.isdir(pth):
                return {"ok": False, "message": "文件夹不存在"}
            try:
                if os.name == "nt":
                    os.startfile(pth)  # noqa: PTH
                else:
                    subprocess.Popen(["xdg-open", pth])
                return {"ok": True}
            except Exception as e:
                return {"ok": False, "message": str(e)}
        if path == "/api/drivestatus":
            rclone = find_rclone()
            st = self.drive.snapshot()
            configured = bool(rclone) and rclone_has_remote(rclone, RCLONE_REMOTE)
            st.update({"rclone": bool(rclone), "configured": configured,
                       "remote": RCLONE_REMOTE})
            if payload.get("withFile") and rclone:
                st["configFile"] = rclone_config_file(rclone)
            return st
        if path == "/api/driveconnect":
            if not find_rclone():
                return {"ok": False, "message": "没有找到 rclone.exe。请重新运行「安装.bat」。"}
            started = self.drive.start()
            return {"ok": True, "started": started}
        if path == "/api/drivecancel":
            self.drive.cancel()
            return {"ok": True}
        if path == "/api/drivetest":
            rclone = find_rclone()
            if not rclone:
                return {"ok": False, "message": "没有找到 rclone.exe"}
            if not rclone_has_remote(rclone, RCLONE_REMOTE):
                return {"ok": False, "message": "还没有连接 Google Drive"}
            ok, info = rclone_drive_about(rclone)
            return {"ok": ok, "about": info if ok else {}, "message": "" if ok else info}
        if path == "/api/drivedisconnect":
            rclone = find_rclone()
            if rclone and rclone_has_remote(rclone, RCLONE_REMOTE):
                _rclone_run(rclone, ["config", "delete", RCLONE_REMOTE], 30)
            return {"ok": True}
        if path == "/api/downloadstop":
            self.downloader.stop()
            return {"ok": True}
        if path == "/api/resolvelink":
            # 解析一条网盘链接：是文件就返回 ID，是文件夹就把里面的文件列出来。
            # 放在大锁外面：rclone 调用可能要几秒，不能卡住自动保存。
            info = drive_link_info(payload.get("link"))
            if not info["id"]:
                return {"ok": False, "message": "看不出这是一个网盘链接"}
            rclone = find_rclone()
            if not rclone:
                return {"ok": False, "message": "未找到 rclone.exe"}
            if info["kind"] == "file":
                return {"ok": True, "kind": "file", "id": info["id"]}
            ok, items, msg = rclone_list_folder(rclone, info["id"])
            if ok:
                files = [{"id": x.get("ID") or "", "name": x.get("Name") or "",
                          "size": x.get("Size") or 0}
                         for x in items if not x.get("IsDir")]
                subdirs = len([x for x in items if x.get("IsDir")])
                if files or subdirs or info["kind"] == "folder":
                    return {"ok": True, "kind": "folder", "id": info["id"],
                            "files": files, "subdirs": subdirs}
                return {"ok": True, "kind": "file", "id": info["id"]}
            if info["kind"] == "folder":
                return {"ok": False, "message": msg or "打不开这个文件夹"}
            return {"ok": True, "kind": "file", "id": info["id"]}
        if path == "/api/pickdlfolder":
            picked = win_pick("folder", payload.get("initial") or self.dir, "选择下载保存到哪个文件夹")
            if not picked:
                return {"ok": False, "cancelled": True}
            return {"ok": True, "dir": picked}
        if path == "/api/paths":
            clip = clipboard_path()
            return {"explorer": explorer_folders(), "clipboard": clip}

        with self.lock:
            if path == "/api/init":
                return self._init_payload(payload)
            if path == "/api/files":
                reloaded = self.project.reload_if_newer()
                listing = self._listing(payload)
                listing["restorable"] = len(self.project.restore_plan())
                if reloaded:
                    listing["grid"] = self.project.data.get("grid")
                listing["ok"] = True
                return listing
            if path == "/api/savegrid":
                reloaded = self.project.reload_if_newer()
                incoming_grid = payload.get("grid")
                if reloaded and isinstance(incoming_grid, dict) and isinstance(self.project.data.get("grid"), dict):
                    disk_dl = (self.project.data["grid"].get("dl") or {})
                    disk_rows = (disk_dl.get("doc") or {}).get("rows") or []
                    in_rows = ((incoming_grid.get("dl") or {}).get("doc") or {}).get("rows") or []
                    if disk_rows and not in_rows:
                        incoming_grid["dl"] = disk_dl
                self.project.data["grid"] = incoming_grid
                ok = self.project.save()
                if payload.get("prefs"):
                    save_prefs(payload.get("prefs"))
                return {"ok": ok, "updated": self.project.data.get("updated", "")}
            if path == "/api/saveprefs":
                ok = save_prefs(payload or {})
                return {"ok": ok, "prefs": load_prefs()}
            if path == "/api/droppath":
                raw = (payload.get("path") or "").strip().strip('"')
                if not raw:
                    return {"ok": False, "message": "没有路径"}
                if os.path.isfile(raw):
                    raw = os.path.dirname(raw)
                if not os.path.isdir(raw):
                    return {"ok": False, "message": "不是文件夹：%s" % raw}
                return self._switch_dir(raw, payload)
            if path == "/api/rename":
                plan = [(p[0], p[1]) for p in payload.get("plan", [])]
                inventory = folder_inventory(self.dir)      # 改名前的完整清单，存档备查
                res = do_rename(self.dir, plan)
                links = payload.get("links") or {}
                if res.get("renamed"):
                    self.undo_stack.append(res["renamed"])
                    self.project.record(res["renamed"], links, inventory)
                if payload.get("grid") is not None:
                    self.project.data["grid"] = payload["grid"]
                    self.project.save()
                res.update(self._listing(payload))
                res["undo"] = len(self.undo_stack)
                res["restorable"] = len(self.project.restore_plan())
                res["snap_index"] = self.project.snap_index()
                res["snapshots"] = self.project.snapshot_list()
                return res
            if path == "/api/undo":
                if not self.undo_stack:
                    return {"ok": False, "message": "没有可撤销的操作。"}
                batch = self.undo_stack.pop()
                res = do_rename(self.dir, [(n, o) for o, n in reversed(batch)])
                if res.get("renamed"):
                    prev = self.project.snap_index() - 1
                    if prev >= 0:
                        self.project.finish_restore_to(prev, res["renamed"])
                    else:
                        self.project.record(res["renamed"])
                res.update(self._listing(payload))
                res["undo"] = len(self.undo_stack)
                res["restorable"] = len(self.project.restore_plan())
                res["snap_index"] = self.project.snap_index()
                res["snapshots"] = self.project.snapshot_list()
                return res
            if path == "/api/snapshots":
                return {
                    "ok": True,
                    "index": self.project.snap_index(),
                    "list": self.project.snapshot_list(),
                }
            if path == "/api/snapplan":
                idx = payload.get("index")
                if idx is None:
                    idx = self.project.snap_index() - 1
                plan, index = self.project.plan_to_snapshot(idx)
                return {
                    "ok": True,
                    "index": index,
                    "plan": plan,
                    "current": self.project.snap_index(),
                    "list": self.project.snapshot_list(),
                }
            if path == "/api/restoresnap":
                idx = payload.get("index")
                if idx is None:
                    idx = self.project.snap_index() - 1
                plan, index = self.project.plan_to_snapshot(idx)
                if index == self.project.snap_index() and not plan:
                    return {"ok": False, "message": "已经在这个时间点，不用改。"}
                if not plan:
                    self.project.finish_restore_to(index, [])
                    out = {"ok": True, "renamed": [], "errors": [], "skipped": []}
                    out.update(self._listing(payload))
                    out["restorable"] = len(self.project.restore_plan())
                    out["snap_index"] = self.project.snap_index()
                    out["snapshots"] = self.project.snapshot_list()
                    out["undo"] = len(self.undo_stack)
                    return out
                res = do_rename(self.dir, plan)
                if res.get("renamed") or not res.get("errors"):
                    self.project.finish_restore_to(index, res.get("renamed") or [])
                    self.undo_stack = []
                res.update(self._listing(payload))
                res["restorable"] = len(self.project.restore_plan())
                res["snap_index"] = self.project.snap_index()
                res["snapshots"] = self.project.snapshot_list()
                res["undo"] = len(self.undo_stack)
                return res
            if path == "/api/restore":
                plan, index = self.project.plan_to_snapshot(0)
                if not plan:
                    return {"ok": False, "message": "工程文件里没有需要还原的记录。"}
                res = do_rename(self.dir, plan)
                if res.get("renamed") or not res.get("errors"):
                    self.project.finish_restore_to(0, res.get("renamed") or [])
                    self.undo_stack = []
                res.update(self._listing(payload))
                res["restorable"] = len(self.project.restore_plan())
                res["snap_index"] = self.project.snap_index()
                res["snapshots"] = self.project.snapshot_list()
                return res
            if path == "/api/restoreinfo":
                return {
                    "plan": self.project.restore_plan(),
                    "dir": self.dir,
                    "updated": self.project.data.get("updated", ""),
                    "history": len(self.project.data.get("history") or []),
                    "snap_index": self.project.snap_index(),
                    "list": self.project.snapshot_list(),
                }
            if path == "/api/opendir":
                target = (payload.get("dir") or "").strip().strip('"')
                if not target:
                    return {"ok": False, "message": "没有提供文件夹路径。"}
                return self._switch_dir(target, payload)
            if path == "/api/browsefolder":
                picked = win_pick("folder", payload.get("initial") or self.dir, "选择视频所在文件夹")
                if not picked:
                    return {"ok": False, "cancelled": True}
                return self._switch_dir(picked, payload)
            if path == "/api/export":
                default_name = "重命名工程_%s.renamer.json" % time.strftime("%Y%m%d_%H%M%S")
                picked = payload.get("path")
                if not picked:
                    picked = win_pick("save", self.dir, "导出工程文件", default_name)
                if not picked:
                    return {"ok": False, "cancelled": True}
                if payload.get("grid") is not None:
                    self.project.data["grid"] = payload["grid"]
                blob = self.project.export_payload()
                try:
                    with open(picked, "w", encoding="utf-8") as f:
                        json.dump(blob, f, ensure_ascii=False, indent=1)
                except Exception as e:
                    return {"ok": False, "message": "导出失败：%s" % e}
                return {"ok": True, "path": picked}
            if path == "/api/import":
                picked = payload.get("path")
                if not picked:
                    picked = win_pick("open", self.dir, "导入工程文件")
                if not picked:
                    return {"ok": False, "cancelled": True}
                try:
                    with open(picked, "r", encoding="utf-8") as f:
                        data = json.load(f)
                except Exception as e:
                    return {"ok": False, "message": "读不了这个工程文件：%s" % e}
                if not isinstance(data, dict):
                    return {"ok": False, "message": "工程文件格式不对。"}
                video_dir = (payload.get("dir") or "").strip().strip('"')
                recorded = data.get("dir") or ""
                if not video_dir:
                    if recorded and os.path.isdir(recorded):
                        video_dir = recorded
                    else:
                        return {
                            "ok": False, "need_folder": True,
                            "recorded": recorded,
                            "path": picked,
                            "message": "工程文件里记录的原文件夹已经不在了，请指定视频现在所在的文件夹。",
                        }
                if not os.path.isdir(video_dir):
                    return {"ok": False, "message": "文件夹不存在：%s" % video_dir}
                try:
                    self.project.save()
                except Exception:
                    pass
                self.dir = os.path.abspath(video_dir)
                self.project = Project(self.dir)
                grid, missing = remap_grid_files(
                    data.get("grid"), self.dir,
                    data.get("origin") or {}, data.get("file_meta") or {})
                self.project.data["origin"] = data.get("origin") or {}
                self.project.data["file_meta"] = data.get("file_meta") or {}
                self.project.data["history"] = data.get("history") or []
                if data.get("snap_index") is None:
                    self.project.data["snap_index"] = len(self.project.data["history"])
                else:
                    self.project.data["snap_index"] = data.get("snap_index")
                self.project.data["grid"] = grid
                self.project.data["imported_from"] = picked
                self.project.save()
                self.undo_stack = []
                out = self._init_payload()
                out["ok"] = True
                out["missing"] = missing
                out["imported"] = picked
                return out
            if path == "/api/reveal":
                name = payload.get("file") or ""
                if name and not entry_name_ok(name):
                    return {"ok": False, "message": "文件名不合法"}
                path_arg = os.path.join(self.dir, name) if name else self.dir
                try:
                    if name and os.path.isfile(path_arg):
                        subprocess.Popen(["explorer", "/select,", os.path.normpath(path_arg)],
                                         creationflags=CREATE_NO_WINDOW if os.name == "nt" else 0)
                    else:
                        subprocess.Popen(["explorer", os.path.normpath(self.dir)],
                                         creationflags=CREATE_NO_WINDOW if os.name == "nt" else 0)
                    return {"ok": True}
                except Exception as e:
                    return {"ok": False, "message": str(e)}
            if path == "/api/openfile":
                name = payload.get("file") or ""
                if not entry_name_ok(name):
                    return {"ok": False, "message": "文件名不合法"}
                path_arg = os.path.join(self.dir, name)
                if not os.path.exists(path_arg):
                    return {"ok": False, "message": "文件不存在"}
                try:
                    os.startfile(path_arg)  # noqa: PTH
                    return {"ok": True}
                except Exception as e:
                    return {"ok": False, "message": str(e)}
            if path == "/api/openurl":
                url = str(payload.get("url") or "").strip()
                if not re.match(r"^https?://", url, re.I):
                    return {"ok": False, "message": "不是网址"}
                try:
                    if os.name == "nt":
                        os.startfile(url)  # noqa: PTH
                    else:
                        webbrowser.open(url)
                    return {"ok": True}
                except Exception as e:
                    try:
                        webbrowser.open(url)
                        return {"ok": True}
                    except Exception:
                        return {"ok": False, "message": str(e)}
            if path == "/api/pastelog":
                try:
                    base = os.path.dirname(prefs_path())
                    with open(os.path.join(base, "last_paste.html"), "w", encoding="utf-8") as f:
                        f.write(str(payload.get("html") or ""))
                    with open(os.path.join(base, "last_paste.txt"), "w", encoding="utf-8") as f:
                        f.write(str(payload.get("text") or ""))
                        f.write("\n\n==== 解析结果 ====\n")
                        f.write(str(payload.get("parsed") or ""))
                except Exception:
                    pass
                return {"ok": True}
            if path == "/api/upload":
                items = []
                for it in payload.get("items") or []:
                    name = it.get("file") or ""
                    url = it.get("url") or ""
                    fid = drive_folder_id(url)
                    if not entry_name_ok(name) or not fid:
                        continue
                    src = os.path.join(self.dir, name)
                    # 硬规则：文件夹永远不进上传队列，即使勾选、即使填了网盘链接。
                    if not is_plain_file(src):
                        continue
                    items.append({"file": name, "id": fid, "url": url})
                if not items:
                    return {"ok": False, "message": "没有可上传的文件。文件夹不会被上传；需要勾选、本地是文件、并且网盘链接能解析出文件夹 ID。"}
                ok, msg = self.uploader.start(self.dir, items)
                if not ok:
                    return {"ok": False, "message": msg}
                return {"ok": True, "plan": len(items)}
            if path == "/api/dlrename":
                dest = (payload.get("dest") or "").strip().strip('"')
                if not dest or not os.path.isdir(dest):
                    return {"ok": False, "message": "下载文件夹不存在：%s" % dest}
                plan = []
                for p in payload.get("plan") or []:
                    if not isinstance(p, (list, tuple)) or len(p) != 2:
                        continue
                    old, new = p[0], p[1]
                    if not (entry_name_ok(old) and entry_name_ok(new)):
                        return {"ok": False, "message": "文件名不合法：%s → %s" % (old, new)}
                    plan.append((old, new))
                if not plan:
                    return {"ok": False, "message": "没有需要改的文件"}
                return do_rename(dest, plan)
            if path == "/api/download":
                dest = (payload.get("dest") or self.dir).strip().strip('"')
                if not os.path.isdir(dest):
                    return {"ok": False, "message": "保存位置不存在：%s" % dest}
                items = []
                for it in payload.get("items") or []:
                    fid = (it.get("id") or "").strip()
                    if not fid or fid[:1] == "-" or any(c in fid for c in " \t\r\n"):
                        continue
                    items.append({
                        "key": it.get("key") or fid,
                        "id": fid,
                        "name": it.get("name") or "",
                        "stem": it.get("stem") or "",
                        "keepExt": it.get("keepExt") is not False,
                        "ext": (it.get("ext") or "").strip(),
                        "extFill": it.get("extFill") is not False,
                        "size": it.get("size") or 0,
                    })
                if not items:
                    return {"ok": False, "message": "没有可下载的行。先粘贴网盘链接并解析出文件。"}
                need = sum(int(it.get("size") or 0) for it in items)
                if need:
                    try:
                        free = shutil.disk_usage(dest).free
                    except Exception:
                        free = 0
                    if free and need + 300 * 1024 * 1024 > free:
                        return {"ok": False,
                                "message": "磁盘空间可能不够：这批一共 %s，%s 只剩 %s。"
                                           "请清一些空间，或把保存位置换到别的盘。"
                                           % (fmt_bytes(need), os.path.splitdrive(dest)[0] or dest,
                                              fmt_bytes(free))}
                workers = int(payload.get("workers") or 3)
                ok, msg = self.downloader.start(dest, items, workers)
                if not ok:
                    return {"ok": False, "message": msg}
                return {"ok": True, "plan": len(items)}
            if path == "/api/quit":
                threading.Timer(0.3, lambda: os._exit(0)).start()
                return {"ok": True}
        return {"ok": False, "message": "未知接口"}


class Handler(BaseHTTPRequestHandler):
    app = None
    token = ""
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _send(self, body, ctype, status=200):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _deny(self):
        self._send('{"ok":false,"message":"拒绝访问"}', "application/json; charset=utf-8", 403)

    def _host_ok(self):
        host = (self.headers.get("Host") or "").strip().lower()
        if host.startswith("["):
            return False
        return host.split(":")[0] in ("127.0.0.1", "localhost")

    def _origin_ok(self):
        origin = (self.headers.get("Origin") or "").strip().lower()
        if not origin:
            return True
        if origin == "null":
            return False
        port = int(self.server.server_address[1])
        allowed = ("http://127.0.0.1:%d" % port, "http://localhost:%d" % port)
        return origin in allowed

    def _token_ok(self):
        got = self.headers.get("X-Rct-Token") or ""
        exp = Handler.token or ""
        if not got or not exp:
            return False
        try:
            return hmac.compare_digest(got, exp)
        except Exception:
            return False

    def do_GET(self):
        if not self._host_ok() or not self._origin_ok():
            self._deny()
            return
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            page = HTML.replace("___RCT_SESSION_TOKEN___", Handler.token)
            self._send(page, "text/html; charset=utf-8")
        else:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()

    def do_POST(self):
        if not self._host_ok() or not self._origin_ok() or not self._token_ok():
            self._deny()
            return
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = -1
        if n < 0 or n > MAX_BODY:
            self._send('{"ok":false,"message":"请求太大或格式不对"}',
                       "application/json; charset=utf-8", 400)
            return
        raw = self.rfile.read(n) if n else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        try:
            result = Handler.app.api(self.path.split("?", 1)[0], payload)
        except Exception:
            result = {"ok": False, "message": "服务器错误"}
        self._send(json.dumps(result, ensure_ascii=False), "application/json; charset=utf-8")


# ============================================================ 前端
HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>右键表格工具</title>
<style>
:root{
  --bd:#e3e5e8; --bd2:#d0d3d7; --hd:#f6f7f9; --blue:#1a73e8; --blue-bg:rgba(26,115,232,.10);
  --txt:#1f2328; --mut:#8a9099; --err:#d93025; --err-bg:#fce8e6; --ok:#137333;
  --pv:#fbf8f1; --pvhd:#f3ecdd; --pvbd:#e6dcc6;
}
*{box-sizing:border-box}
html,body{height:100%;margin:0}
body{font:13px/1.45 "Segoe UI","Microsoft YaHei","微软雅黑",sans-serif;color:var(--txt);
     background:#fff;display:flex;flex-direction:column;overflow:hidden;user-select:none}

/* ---------- 菜单栏 ---------- */
#menubar{display:flex;align-items:center;height:34px;padding:0 6px;border-bottom:1px solid var(--bd);
         background:#fff;flex:none;position:relative;z-index:60}
#menubar .brand{font-weight:600;margin:0 12px 0 6px;color:var(--blue);letter-spacing:.3px}
.menu{position:relative}
.menu>.mtitle{padding:5px 11px;border-radius:6px;cursor:default}
.menu:hover>.mtitle,.menu.open>.mtitle{background:#eef1f4}
.dropdown{display:none;position:absolute;top:32px;left:0;min-width:264px;background:#fff;
          border:1px solid var(--bd2);border-radius:8px;box-shadow:0 8px 26px rgba(0,0,0,.14);padding:5px 0;z-index:70}
.menu.open .dropdown{display:block}
.mi{display:flex;justify-content:space-between;gap:26px;padding:7px 14px;cursor:default;white-space:nowrap}
.mi:hover{background:var(--blue-bg)}
.mi .key{color:var(--mut);font-size:11.5px}
.mi.disabled{color:#bbb;pointer-events:none}
.msep{height:1px;background:var(--bd);margin:5px 0}

/* ---------- 工具条（可换行，窄窗口不挤字） ---------- */
#actionbar{display:flex;flex-wrap:wrap;align-items:center;gap:8px 10px;padding:7px 10px;
         background:var(--hd);border-bottom:1px solid var(--bd);flex:none}
.ab-left{display:flex;flex-wrap:wrap;align-items:center;gap:8px 10px;flex:1;min-width:0}
.ab-right{display:flex;flex-wrap:wrap;align-items:center;gap:8px;margin-left:auto}
.grp{display:flex;align-items:center;gap:6px;padding-right:10px;border-right:1px solid var(--bd2)}
.grp:last-child{border-right:none;padding-right:0}
button{font:inherit;border:1px solid var(--bd2);background:#fff;border-radius:6px;padding:5px 10px;
       cursor:pointer;color:var(--txt);white-space:nowrap;line-height:1.2}
button:hover{background:#f0f2f5}
button:disabled{color:#bbb;cursor:default;background:#f7f7f7}
button.primary{background:var(--blue);border-color:var(--blue);color:#fff;font-weight:600}
button.primary:hover{background:#1765cc}
button.upload{background:#0f7b6c;border-color:#0f7b6c;color:#fff;font-weight:600}
button.upload:hover{background:#0c675b}
.sepv{width:1px;height:20px;background:var(--bd2)}
.seg{background:#fff;border:1px solid var(--bd2);border-radius:7px;padding:2px;gap:0}
.seg button{border:0;background:transparent;border-radius:5px;padding:4px 10px}
.seg button:hover{background:#eef1f4}
.toggle{display:flex;align-items:center;gap:6px;padding:4px 9px;border:1px solid var(--bd2);
        border-radius:7px;background:#fff;cursor:pointer;white-space:nowrap;user-select:none;font:inherit}
.toggle:hover{background:#f0f2f5}
.toggle input{accent-color:var(--blue);margin:0}
.toggle.on{background:#e8f0fe;border-color:#c5d8f8;color:#174ea6}
.morewrap{position:relative}
#moremenu{display:none;position:absolute;top:34px;left:0;min-width:200px;background:#fff;
          border:1px solid var(--bd2);border-radius:8px;box-shadow:0 8px 26px rgba(0,0,0,.14);padding:5px 0;z-index:80}
#moremenu .mi{cursor:pointer}
#pathbar{display:flex;align-items:center;gap:8px;padding:5px 12px;border-bottom:1px solid var(--bd);
         background:#fff;flex:none;min-height:28px;font-size:12px;color:var(--mut)}
#dirlabel{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;
          direction:rtl;text-align:left;cursor:pointer;color:var(--mut)}
#dirlabel:hover{color:var(--blue);text-decoration:underline}
#pathmeta{flex:none;color:var(--mut)}
@media (max-width: 920px){
  .ab-right{width:100%;justify-content:flex-end;margin-left:0;padding-top:4px;
            border-top:1px dashed var(--bd)}
  .grp{border-right:none;padding-right:0}
}

/* ---------- 表格 ---------- */
#wrap{flex:1;overflow:auto;position:relative;outline:none}
table{border-collapse:separate;border-spacing:0;table-layout:fixed;width:max-content;min-width:100%}
th,td{border-right:1px solid var(--bd);border-bottom:1px solid var(--bd);height:29px;padding:0 8px;
      overflow:hidden;white-space:nowrap;text-overflow:ellipsis;font-weight:400;text-align:left;background:#fff;
      max-width:0;box-sizing:border-box;vertical-align:middle}
td.wrap,th.wrap{white-space:pre-wrap;word-break:break-word;overflow-wrap:anywhere;text-overflow:clip;
      height:auto;min-height:29px;line-height:1.35;padding:4px 8px;vertical-align:top;overflow:hidden}
thead th{position:sticky;top:0;z-index:20;background:var(--hd);font-weight:600;text-align:center;
         border-bottom:1px solid var(--bd2);color:#444}
thead th.corner{left:0;z-index:31;width:34px}
thead th.corner2{position:sticky;left:34px;z-index:30;width:46px;background:var(--hd)}
th.chkhd,td.chk{position:sticky;left:0;z-index:12;width:34px;text-align:center;padding:0;
        background:var(--hd);cursor:pointer;user-select:none}
td.chk{background:#fff;z-index:11}
th.rownum{position:sticky;left:34px;z-index:10;background:var(--hd);text-align:center;color:var(--mut);
          font-size:11.5px;width:46px;cursor:default}
th.rownum:hover{background:#e9ecef}
tr.rsel th.rownum,tr.rsel td.chk,th.hdr.hl{background:#dbe6f7;color:#333}
td.cell{cursor:cell}
/* 斑马纹：偶数行轻微着色；用 background-image 叠在各种底色上，不打架 */
tbody tr:nth-child(even) td,tbody tr:nth-child(even) th.rownum{
     background-image:linear-gradient(rgba(37,64,110,.045),rgba(37,64,110,.045))}
/* 悬停：整行淡蓝高亮，行号加深，一眼看出在第几行 */
tbody tr:hover td,tbody tr:hover th.rownum{
     background-image:linear-gradient(rgba(26,115,232,.085),rgba(26,115,232,.085))}
tbody tr:hover th.rownum{color:var(--blue);font-weight:600}
tbody tr:hover td.pv{background-image:linear-gradient(rgba(26,115,232,.06),rgba(26,115,232,.06))}
tr.skip td.cell{opacity:.45}
tr.skip td.pnew{color:var(--mut)!important;font-weight:400}
.box{display:inline-block;width:17px;height:17px;border:1.5px solid #b3b9c4;border-radius:5px;
     background:#fff;vertical-align:middle;position:relative;box-sizing:border-box;
     transition:background .12s,border-color .12s,box-shadow .12s,transform .08s}
td.chk:hover .box,th.chkhd:hover .box{border-color:var(--blue);box-shadow:0 0 0 3px rgba(26,115,232,.14)}
td.chk:active .box{transform:scale(.92)}
.box.on{background:linear-gradient(180deg,#3d8bff,#1a73e8);border-color:#1a6fe0;
     box-shadow:0 1px 2px rgba(26,115,232,.35)}
.box.on:after{content:"";position:absolute;left:5px;top:1.5px;width:4.5px;height:8.5px;
     border:solid #fff;border-width:0 2px 2px 0;border-radius:1px;transform:rotate(45deg)}
.box.some{background:#fff;border-color:var(--blue)}
.box.some:after{content:"";position:absolute;left:3px;top:6px;width:8px;height:2.5px;border-radius:2px;background:var(--blue)}
tr.skip td.chk .box{border-color:#c9ced4}
td.linkcell{color:#1a73e8}
td.linkcell .ph{color:#c2c7ce;font-style:italic}
td.linkcell .lkh{color:#9aa3ad;font-size:11.5px}
td.linkcell .lkid{font-family:Consolas,"Cascadia Mono",monospace;font-size:12px;color:#1a73e8;letter-spacing:.2px}
td.linkcell .lkbad{color:var(--err);font-weight:600}
body.ctrl-down td.linkcell.haslink{cursor:pointer}
body.ctrl-down td.linkcell.haslink:hover .lkid{text-decoration:underline;text-underline-offset:2px}
body.ctrl-down td.linkcell.haslink:hover{box-shadow:inset 0 -2px 0 var(--blue)}
.dupb{display:inline-block;margin-right:6px;padding:0 5px;border-radius:9px;font-size:10.5px;font-weight:700;
      line-height:16px;vertical-align:1px;background:#fff1cc;color:#8a5a00;border:1px solid #f0c36d}
button.redo{background:#fff7e6;border-color:#e0a72e;color:#7a5a10;font-weight:600;display:none}
button.redo:hover{background:#ffe9b3}
button.redo.show{display:inline-block}
.dupwarn{border:1px solid #f0c36d;background:#fff8e6;border-radius:8px;padding:10px 12px;margin:10px 0;color:#7a5a10;font-size:12.5px;line-height:1.6}
.dupwarn b{color:#8a3b00}
.dupwarn label{display:flex;gap:7px;align-items:center;margin-top:8px;font-weight:600;cursor:pointer}
.redowarn{border:1px solid #f5c2be;background:#fdeceb;border-radius:8px;padding:10px 12px;margin:10px 0;color:#8a2a22;font-size:12.5px;line-height:1.6}
td.stat{font-size:12px;color:#5a6069}
td.stat.ok{color:var(--ok);font-weight:600;background:#eaf6ec}
td.stat.fail{color:var(--err);font-weight:600;background:var(--err-bg)}
td.stat.run{color:var(--blue);font-weight:600}
td.stat.skip{color:var(--mut)}
th.stathd{background:#eef6f1;color:#3d7a45}
td.sel{background:var(--blue-bg)}
td.active{outline:2px solid var(--blue);outline-offset:-2px;z-index:5;position:relative}
tr.blank th.rownum{color:#c9ced4}
tr.blank td{background:#fafafa;color:#c9ced4;font-style:italic}
tr.dir td.cell{background:#f7fafc}
.dirmark{display:inline-block;margin-right:3px;padding:0 3px;border-radius:3px;background:#dbeafe;
         color:#1e40af;font-size:10px;font-weight:700;vertical-align:middle}
.ph{color:#c2c7ce}

/* 预览列（只读） */
th.pv{background:var(--pvhd);color:#7a6a45;border-left:2px solid var(--pvbd)}
td.pv{background:var(--pv);color:#6b6b6b;cursor:default}
td.pv.first{border-left:2px solid var(--pvbd)}
td.pv.sel{background:#f0e9da}
td.pnew{font-weight:500}
td.pnew.chg{color:var(--ok)}
td.pnew.err{background:var(--err-bg)!important;color:var(--err);font-weight:600}
.ext{color:#b9bec5}

.hdrlabel{display:block;overflow:hidden;text-overflow:ellipsis;pointer-events:none}
.resizer{position:absolute;top:0;right:0;width:6px;height:100%;cursor:col-resize;z-index:25}
.resizer:hover{background:var(--blue)}

#fill{position:absolute;width:8px;height:8px;background:var(--blue);border:1px solid #fff;
      border-radius:1px;cursor:crosshair;display:none;z-index:40}
#vline{position:absolute;width:2px;background:var(--blue);display:none;z-index:45;top:0}
#hline{position:absolute;height:2px;background:var(--blue);display:none;z-index:45;left:0}
#editor{position:absolute;display:none;z-index:50;border:2px solid var(--blue);padding:2px 6px;
        font:inherit;outline:none;background:#fff;border-radius:2px;resize:none;overflow:auto;
        white-space:pre-wrap;word-break:break-word;line-height:1.35;box-sizing:border-box}

/* ---------- 状态栏 ---------- */
#dropmask{display:none;position:fixed;inset:0;z-index:400;background:rgba(26,115,232,.14);
          border:3px dashed var(--blue);align-items:center;justify-content:center;
          font-size:22px;font-weight:700;color:var(--blue);pointer-events:none}
#dropmask.on{display:flex}
#stUndo{display:none;align-items:center;font-weight:700;border:1px solid #e0a72e;background:#fff7e6;
        color:#7a5a10;padding:2px 12px;height:22px;border-radius:6px;cursor:pointer;flex:none}
#stUndo.show{display:inline-flex}
#stUndo:hover{background:#ffe9b3}
#status{display:flex;align-items:center;gap:18px;height:28px;padding:0 14px;border-top:1px solid var(--bd);
        background:var(--hd);color:#5a6069;font-size:12px;flex:none}
#status .warn{color:var(--err);font-weight:600}
#status .sv{margin-left:auto;color:var(--mut)}
#formula{color:#5a6069;max-width:44%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
#formula b{color:var(--ok)}

/* ---------- 右键菜单 / 弹窗 ---------- */
#ctx{position:fixed;display:none;background:#fff;border:1px solid var(--bd2);border-radius:8px;
     box-shadow:0 8px 26px rgba(0,0,0,.16);padding:5px 0;z-index:200;min-width:214px}
#mask{position:fixed;inset:0;background:rgba(0,0,0,.28);display:none;z-index:300;
      align-items:center;justify-content:center}
.dlg{background:#fff;border-radius:12px;box-shadow:0 20px 60px rgba(0,0,0,.3);min-width:440px;max-width:720px;
     max-height:82vh;overflow:auto;user-select:text}
.sum{display:grid;grid-template-columns:1fr auto;gap:5px 18px;margin:4px 0 12px;font-size:13px}
.sum .k{color:#4a5058}
.sum .v{font-weight:700;text-align:right;font-variant-numeric:tabular-nums}
.sum .ok .v{color:var(--ok)}
.sum .warn .v,.sum .warn .k{color:var(--err)}
.sum .mut .v,.sum .mut .k{color:var(--mut)}
details.sumd{margin:8px 0 4px}
details.sumd summary{cursor:pointer;color:var(--blue);margin-bottom:6px}
.snap{display:flex;align-items:center;gap:10px;padding:8px 10px;border:1px solid var(--bd);
      border-radius:8px;margin-bottom:6px;cursor:pointer;background:#fff}
.snap:hover{background:#eef6ff;border-color:#c5daf7}
.snap.cur{background:#eaf6ec;border-color:#b7e0c4;cursor:default}
.snap .t{flex:1}
.snap .tm{color:var(--mut);font-size:12px;flex:none}
.dlg h3{margin:0;padding:16px 20px;border-bottom:1px solid var(--bd);font-size:15px}
.dlg .body{padding:16px 20px}
.dlg .foot{padding:12px 20px;border-top:1px solid var(--bd);display:flex;justify-content:flex-end;gap:8px}
.row{display:flex;align-items:center;gap:10px;margin-bottom:11px}
.row label{width:120px;color:#4a5058;flex:none}
.row input[type=text],.row input[type=number],.row select{flex:1;font:inherit;padding:6px 9px;
      border:1px solid var(--bd2);border-radius:6px;outline:none}
.row input:focus,.row select:focus{border-color:var(--blue)}
.hint{color:var(--mut);font-size:12px;margin-top:-4px;margin-bottom:12px;line-height:1.65}
/* 批量设置扩展名：单选项 */
label.extopt{display:flex;align-items:flex-start;gap:10px;padding:10px 12px;margin-bottom:8px;
      border:1px solid var(--bd);border-radius:8px;cursor:pointer;transition:background .12s,border-color .12s}
label.extopt:hover{background:var(--hd);border-color:var(--bd2)}
label.extopt input{margin:2px 0 0;accent-color:var(--blue);flex:none}
label.extopt span{display:flex;flex-direction:column;gap:3px;line-height:1.5}
label.extopt b{font-weight:600}
label.extopt em{font-style:normal;color:var(--mut);font-size:12px}
label.extopt:has(input:checked){border-color:var(--blue);background:var(--blue-bg)}
.extdocs{display:flex;align-items:center;gap:8px;margin:0 0 14px;flex-wrap:wrap}
.extdocs .lbl{color:var(--mut);font-size:12px;margin-right:2px}
label.extdoc{display:flex;align-items:center;cursor:pointer}
label.extdoc input{position:absolute;opacity:0;pointer-events:none}
label.extdoc span{display:flex;align-items:baseline;gap:8px;padding:7px 14px;border:1px solid var(--bd2);
      border-radius:999px;font-weight:600;transition:background .12s,border-color .12s,color .12s}
label.extdoc span em{font-style:normal;font-weight:400;font-size:12px;color:var(--mut)}
label.extdoc:hover span{background:var(--hd)}
label.extdoc input:checked+span{border-color:var(--blue);background:var(--blue-bg);color:var(--blue)}
label.extdoc input:checked+span em{color:var(--blue);opacity:.8}
label.extdoc input:focus-visible+span{outline:2px solid var(--blue);outline-offset:2px}
.extdisk{margin-top:10px;padding:8px 12px;border-left:3px solid var(--blue);background:var(--blue-bg);
      border-radius:0 6px 6px 0;color:var(--txt)}
label.extfill{display:flex;align-items:center;gap:8px;margin:0 0 10px 26px;cursor:pointer;
      color:var(--txt);line-height:1.5;transition:opacity .12s}
label.extfill input{margin:0;accent-color:var(--blue);flex:none}
#d_extval{max-width:170px}
pre.log{background:#f6f7f9;border:1px solid var(--bd);border-radius:6px;padding:10px;max-height:300px;
        overflow:auto;font:12px/1.6 Consolas,monospace;white-space:pre-wrap;user-select:text}
div.chk{display:flex;align-items:center;gap:7px;margin-bottom:10px}

/* ---------- 列字母 / 长度上限徽标 ---------- */
.collet{display:inline-block;min-width:15px;padding:0 4px;margin-right:5px;border-radius:3px;
        background:#e4e7ec;color:#5a6069;font-size:10.5px;font-weight:700;pointer-events:none}
th.hdr.hl .collet{background:#b9cdf0;color:#22345a}
.climit{margin-left:6px;padding:0 4px;border-radius:3px;background:#eaf3ea;color:#3d7a45;
        font-size:10.5px;font-weight:700;pointer-events:none}

/* ---------- 统一单元格（整列覆盖层）---------- */
thead tr.mrow th{top:29px;z-index:19;background:#fff7e6;border-bottom:2px solid #f0c36d;padding:0 4px}
thead tr.mrow th.mcorner{left:34px;z-index:29;background:#fdf0d5;text-align:center;font-size:10.5px;
        color:#a07b28;font-weight:700;padding:0}
thead tr.mrow th.mchk{left:0;z-index:29;background:#fdf0d5;width:34px}
thead tr.mrow th.pv{background:#f7f1e4}
.minput{width:100%;height:22px;border:1px solid #e2c68a;border-radius:4px;background:#fffdf7;
        font:inherit;font-size:12px;padding:0 6px;outline:none;color:#7a5a10}
.minput:focus{border-color:#e0a72e;background:#fff;box-shadow:0 0 0 2px rgba(224,167,46,.25)}
.minput.on{background:#ffeec2;border-color:#e0a72e;font-weight:600}
.minput.bad{border-color:var(--err);background:#fdeceb;color:var(--err)}
.minput::placeholder{color:#cdbb92;font-weight:400}
td.mov{color:#8a6d22;background:#fffdf5}
td.mov .under{color:#cfd3d8;text-decoration:line-through;margin-left:7px;font-size:11px}

/* ---------- 超长标记 ---------- */
td.over{background:var(--err-bg);color:var(--err)}
td.over .cut{color:#f09b95}
td.pnew.toolong{background:#fff4d6!important;color:#8a5a00}
#status .warn2{color:#a86b00;font-weight:600}

/* ---------- 查找命中高亮 ---------- */
td.hit{box-shadow:inset 0 0 0 2px #e6c200}
td.hitcur{background:#ffd54a!important;box-shadow:inset 0 0 0 2px #c98f00}

/* ---------- 查找面板 ---------- */
#findbar{position:fixed;top:48px;right:22px;z-index:120;background:#fff;border:1px solid var(--bd2);
         border-radius:10px;box-shadow:0 12px 34px rgba(0,0,0,.2);padding:12px 14px;display:none;width:452px}
#findbar .fr{display:flex;align-items:center;gap:6px;margin-bottom:7px}
#findbar input[type=text]{flex:1;font:inherit;padding:5px 8px;border:1px solid var(--bd2);
         border-radius:6px;outline:none;min-width:0}
#findbar input[type=text]:focus{border-color:var(--blue)}
#findbar .lb{width:50px;color:#4a5058;flex:none;font-size:12px}
#findbar button{padding:4px 9px;font-size:12px}
#findbar .cnt{font-size:12px;color:var(--mut);min-width:70px;text-align:right;flex:none}
#findbar .opts{display:flex;gap:15px;align-items:center;font-size:12px;color:#4a5058;margin:2px 0 8px 56px}
#findbar .opts label{display:flex;align-items:center;gap:4px;cursor:pointer}
#findbar .scope{border-top:1px dashed var(--bd);padding-top:9px;margin-top:2px}
#findbar .sch{font-size:11.5px;color:var(--mut);margin:0 0 6px 56px;line-height:1.6}
#findbar .x{position:absolute;top:7px;right:10px;cursor:pointer;color:var(--mut);font-size:16px;line-height:1}
#findbar .x:hover{color:var(--txt)}

#jumpbar{display:none;align-items:center;gap:10px;padding:7px 12px;background:#eef6ff;
         border-bottom:1px solid #c5daf7;flex:none;font-size:12.5px}
#jumpbar b{color:var(--blue)}
#jumpbar .jp{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
#upbar{display:none;align-items:center;gap:10px;padding:7px 12px;background:#eef8f4;
         border-bottom:1px solid #b7e0d0;flex:none;font-size:12.5px}
#upbar.fail{background:#fdeceb;border-bottom-color:#f5c2be}
#upbar .jp{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
#uplog{display:none;margin:0;max-height:140px;flex:none;border:0;border-bottom:1px solid #b7e0d0;
       background:#0f1720;color:#d7e2ec;padding:10px;overflow:auto;
       font:12px/1.55 Consolas,monospace;white-space:pre-wrap}
/* ---------- 下载分屏面板（青绿色系，和上半部分改名表格区分开）---------- */
#splitter{display:none;flex:none;height:7px;cursor:row-resize;background:var(--hd);
     border-top:1px solid var(--bd);border-bottom:1px solid #b7e0d0;position:relative}
#splitter:after{content:"";position:absolute;left:50%;top:2px;width:46px;height:3px;margin-left:-23px;
     border-radius:2px;background:#9ccfbd}
#splitter:hover{background:#dff1ea}
#splitter:hover:after{background:#0f7b6c}
#dlpane{display:none;flex:none;height:46%;min-height:140px;flex-direction:column;
     background:#f7fcfa;border-top:1px solid #b7e0d0}
#dlbar{display:flex;flex-wrap:wrap;align-items:center;gap:8px;padding:7px 10px;flex:none;
     background:#e9f6f2;border-bottom:1px solid #b7e0d0}
#dlbar .dltitle{font-weight:700;color:#0f7b6c;letter-spacing:.3px}
#dlbar .lb{color:#4a7268;font-size:12px;display:flex;align-items:center;gap:4px}
#dlbar select{font:inherit;font-size:12px;padding:3px 5px;border:1px solid #bfdfd4;border-radius:6px;
     background:#fff;color:#2f6f5e;outline:none}
#dldest{max-width:38%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;direction:rtl;
     text-align:left;font-size:12px;color:#2f6f5e;background:#fff;border:1px solid #bfdfd4;
     border-radius:6px;padding:3px 8px;cursor:pointer}
#dldest:hover{border-color:#0f7b6c}
#dlbar .grow{margin-left:auto}
button.dlgo{background:#0f7b6c;border-color:#0f7b6c;color:#fff;font-weight:600}
button.dlgo:hover{background:#0c675b}
button.dlbtn.on{background:#e9f6f2;border-color:#0f7b6c;color:#0f7b6c;font-weight:600}
#dlhint{flex:none;padding:5px 12px;font-size:11.5px;color:#5c7d74;background:#f2faf7;
     border-bottom:1px solid #dcefe8}
#dlwrap{flex:1;overflow:auto;position:relative;outline:none;background:#fff}
/* 下载表：同一套表格，换青绿色皮肤 */
#dlwrap thead th{background:#eaf6f1;color:#2f6f5e;border-bottom-color:#b7e0d0}
#dlwrap thead th.corner,#dlwrap thead th.corner2,#dlwrap th.chkhd,#dlwrap th.rownum{background:#eef7f3}
#dlwrap th.rownum:hover{background:#dcefe7}
#dlwrap th.hdr.hl,#dlwrap tr.rsel th.rownum,#dlwrap tr.rsel td.chk{background:#cfe9df;color:#1f4f43}
#dlwrap th.pv{background:#e3f1ec;color:#3d6b60;border-left-color:#b7e0d0}
#dlwrap td.pv{background:#f5fbf9;color:#5f6b66}
#dlwrap td.pv.first{border-left-color:#b7e0d0}
#dlwrap td.pv.sel{background:#e2f1eb}
#dlwrap th.stathd{background:#e3f1ec;color:#2f6f5e}
#dlwrap tbody tr:hover td,#dlwrap tbody tr:hover th.rownum{
     background-image:linear-gradient(rgba(15,123,108,.085),rgba(15,123,108,.085))}
#dlwrap tbody tr:hover th.rownum{color:#0f7b6c}
#dlwrap td.sel{background:rgba(15,123,108,.11)}
#dlwrap td.active{outline-color:#0f7b6c}
#dlwrap #fill{background:#0f7b6c}
#dlwrap #editor{border-color:#0f7b6c}
#dlwrap .box.on{background:linear-gradient(180deg,#1fa38d,#0f7b6c);border-color:#0f7b6c}
#dlwrap .box.some{border-color:#0f7b6c}
#dlwrap .box.some:after{background:#0f7b6c}
#dlwrap td.chk:hover .box,#dlwrap th.chkhd:hover .box{border-color:#0f7b6c;box-shadow:0 0 0 3px rgba(15,123,108,.15)}
#dlwrap .collet{background:#d7ede5;color:#2f6f5e}
#dlwrap th.hdr.hl .collet{background:#9fd3c2;color:#123c33}
#dlwrap .dirmark{background:#d7ede5;color:#2f6f5e}
#dlwrap td.linkcell .lkid{color:#0f7b6c}
#dlwrap td.stat.ready{color:#2f6f5e}
#dlwrap td.stat.wait{color:#8a9099}
#dlwrap td.pv .szt{margin-left:6px;color:#a9b5b0;font-size:11px}
/* 下载表：多素材文件夹同组配色与可折叠子行 */
#dlwrap tr.fgrp-0{--fg-bg:#fff8e8;--fg-pv:#f9f1dc;--fg-bar:#d99416;--fg-bd:#fde4ab;--fg-tx:#8a5300}
#dlwrap tr.fgrp-1{--fg-bg:#eefaf3;--fg-pv:#e3f4ea;--fg-bar:#1b9a64;--fg-bd:#bcecd5;--fg-tx:#0d5938}
#dlwrap tr.fgrp-2{--fg-bg:#eef6ff;--fg-pv:#e3eefc;--fg-bar:#2473e0;--fg-bd:#c2dcff;--fg-tx:#12468f}
#dlwrap tr.fgrp-3{--fg-bg:#f6f1ff;--fg-pv:#ece4fa;--fg-bar:#7b48e3;--fg-bd:#dccbff;--fg-tx:#472096}
#dlwrap tr.fgrp-4{--fg-bg:#fff3ee;--fg-pv:#fae8e0;--fg-bar:#de5a35;--fg-bd:#ffd2c4;--fg-tx:#8f2c11}
#dlwrap tr.fgrp-5{--fg-bg:#eefafb;--fg-pv:#e2f3f5;--fg-bar:#0d879c;--fg-bd:#bdebf2;--fg-tx:#07505d}
#dlwrap tr.fgrp td.cell{background-color:var(--fg-bg)}
#dlwrap tr.fgrp td.pv{background-color:var(--fg-pv)}
#dlwrap tr.fgrp th.rownum{background-color:var(--fg-pv);border-left:4px solid var(--fg-bar);color:var(--fg-tx);font-weight:700}
#dlwrap tr.fgrp td.chk{background-color:var(--fg-pv)}
#dlwrap tr.fparent td.cell{font-weight:600}
#dlwrap tr.fsub th.rownum{font-weight:500;padding-left:6px;opacity:.92}
#dlwrap tr.fgrp-end td,#dlwrap tr.fgrp-end th{border-bottom:2px solid var(--fg-bar)!important}
.ftoggle{display:inline-flex;align-items:center;gap:2px;margin-right:4px;padding:1px 6px;border-radius:9px;
     background:var(--fg-bd,#d7ede5);color:var(--fg-tx,#2f6f5e);font-size:10.5px;font-weight:700;cursor:pointer;
     border:1px solid var(--fg-bar,#8bc7b5);user-select:none;transition:transform .1s,filter .12s}
.ftoggle:hover{filter:brightness(.95);transform:scale(1.03)}
.fsub-tag{display:inline-block;margin-right:4px;padding:0 5px;border-radius:4px;background:var(--fg-bd,#d7ede5);
     color:var(--fg-tx,#2f6f5e);font-size:10px;font-weight:700}
td.stat .rty{margin-left:6px;color:#a86b00;font-weight:400;font-size:11px}
td.stat .pbar{display:inline-block;width:52px;height:5px;border-radius:3px;background:#dbe6e2;
     margin-right:6px;vertical-align:middle;overflow:hidden}
td.stat .pbar i{display:block;height:100%;background:var(--blue);border-radius:3px;transition:width .3s}
#dlfoot #dlsum{color:#2f6f5e;font-weight:600}
#dlfoot #dlsum .warn{color:var(--err)} #dlfoot #dlsum .warn2{color:#a86b00}
/* 当前正在编辑的表：顶部一条细色带 */
#docTag{margin-left:auto;margin-right:6px;font-size:11.5px;padding:2px 10px;border-radius:10px;
     background:#e8f0fe;color:#174ea6;white-space:nowrap;cursor:default}
body.doc-dl #docTag{background:#e9f6f2;color:#0f7b6c}
body.doc-dl #dlbar{box-shadow:inset 0 3px 0 #0f7b6c}
body.doc-dl #wrap td.sel{background:rgba(26,115,232,.05)}
body.doc-dl #wrap td.active{outline-color:rgba(26,115,232,.35)}
body.doc-main #dlwrap td.sel{background:rgba(15,123,108,.05)}
body.doc-main #dlwrap td.active{outline-color:rgba(15,123,108,.35)}
#dlfoot{flex:none;display:flex;align-items:center;gap:12px;padding:5px 12px;font-size:12px;
     color:#3d6b60;background:#eaf6f1;border-top:1px solid #dcefe8}
#dlfoot .jp{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:#6b8a82}
#dllog{display:none;margin:0;max-height:120px;flex:none;background:#0f1720;color:#d7e2ec;padding:9px;
     overflow:auto;font:11.5px/1.5 Consolas,monospace;white-space:pre-wrap}

/* ---------- 下方面板的标签切换 ---------- */
.ptabs{display:inline-flex;gap:2px;padding:2px;border-radius:9px;background:rgba(0,0,0,.05);margin-right:4px}
.ptabs .ptab{border:0;background:transparent;border-radius:7px;padding:4px 11px;font-size:12.5px;font-weight:600;color:#6b7580}
.ptabs .ptab:hover{background:rgba(255,255,255,.6)}
.ptabs .ptab.on{cursor:default;background:#fff;box-shadow:0 1px 3px rgba(0,0,0,.12)}
.ptabs .ptab.on.dl{color:#0f7b6c}
.ptabs .ptab.on.mk{color:#6a3fd1}
/* ---------- 批量新建文件夹面板 ---------- */
button.mkbtn.on{background:#f3efff;border-color:#8b6cf0;color:#5b34c9;font-weight:600}
#mkpane{display:none;flex:none;height:46%;min-height:140px;flex-direction:column;background:#fbfaff;border-top:1px solid #d9cffb}
#mkbar{display:flex;flex-wrap:wrap;align-items:center;gap:8px;padding:7px 10px;flex:none;background:#f3efff;border-bottom:1px solid #d9cffb}
#mkbar .lb{color:#6a5a96;font-size:12px}
#mkbar .grow{margin-left:auto}
#mkdest{max-width:36%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;direction:rtl;text-align:left;
     font-size:12px;color:#5b34c9;background:#fff;border:1px solid #d9cffb;border-radius:6px;padding:3px 8px;cursor:pointer}
#mkdest:hover{border-color:#8b6cf0}
button.mkgo{background:linear-gradient(180deg,#8a63f5,#6a3fd1);border-color:#6a3fd1;color:#fff;font-weight:600}
button.mkgo:hover{background:linear-gradient(180deg,#936ff7,#5f35c4)}
#mkhint{flex:none;padding:6px 12px;font-size:11.5px;line-height:1.6;color:#6f6590;background:#f8f5ff;border-bottom:1px solid #ebe4fd}
#mkhint i{font-style:normal;color:#5b34c9;background:#efe9ff;border-radius:4px;padding:0 4px}
#mkwrap{flex:1;overflow:auto;position:relative;outline:none;background:#fff}
#mkwrap thead th{background:#f3efff;color:#4f3a8a;border-bottom-color:#d9cffb}
#mkwrap thead th.corner,#mkwrap thead th.corner2,#mkwrap th.chkhd,#mkwrap th.rownum{background:#f6f3ff}
#mkwrap th.rownum:hover{background:#ebe4fd}
#mkwrap th.hdr.hl,#mkwrap tr.rsel th.rownum,#mkwrap tr.rsel td.chk{background:#e4dafd;color:#3a2380}
#mkwrap th.pv{background:#efe9ff;color:#5f4a9e;border-left-color:#d9cffb}
#mkwrap td.pv{background:#fbf9ff;color:#5f6070}
#mkwrap td.pv.first{border-left-color:#d9cffb}
#mkwrap th.stathd{background:#efe9ff;color:#5f4a9e}
#mkwrap tbody tr:hover td,#mkwrap tbody tr:hover th.rownum{
     background-image:linear-gradient(rgba(106,63,209,.07),rgba(106,63,209,.07))}
#mkwrap tbody tr:hover th.rownum{color:#6a3fd1}
#mkwrap td.sel{background:rgba(106,63,209,.10)}
#mkwrap td.active{outline-color:#6a3fd1}
#mkwrap #fill{background:#6a3fd1}
#mkwrap #editor{border-color:#6a3fd1}
#mkwrap .box.on{background:linear-gradient(180deg,#8a63f5,#6a3fd1);border-color:#6a3fd1}
#mkwrap .box.some{border-color:#6a3fd1}
#mkwrap .box.some:after{background:#6a3fd1}
#mkwrap td.chk:hover .box,#mkwrap th.chkhd:hover .box{border-color:#6a3fd1;box-shadow:0 0 0 3px rgba(106,63,209,.15)}
#mkwrap .collet{background:#e9e1fd;color:#5b34c9}
#mkwrap th.hdr.hl .collet{background:#c6b3f7;color:#2c1570}
#mkwrap td.pnew.chg{color:#3d2a7a;font-weight:600}
#mkwrap td.stat.ready{color:#6a3fd1}
#mkwrap td.stat.wait{color:#9a93ad}
#mkwrap td.stat.ok{color:var(--ok);background:#eaf6ec}
/* 小文件夹图标（纯 CSS 画的，避免依赖字体里的 emoji） */
.fic{display:inline-block;position:relative;width:15px;height:11px;margin:0 7px -1px 0;border-radius:2px;
     background:linear-gradient(180deg,#ffd46b,#f4b33c);box-shadow:inset 0 -1px 0 rgba(0,0,0,.08)}
.fic:before{content:"";position:absolute;left:0;top:-3px;width:7px;height:4px;border-radius:2px 2px 0 0;background:#f4b33c}
.fic.big{width:30px;height:22px;border-radius:4px;margin:0}
.fic.big:before{top:-5px;width:13px;height:6px;border-radius:3px 3px 0 0}
#mkfoot{flex:none;display:flex;align-items:center;gap:12px;padding:5px 12px;font-size:12px;color:#5f4a9e;
     background:#f3efff;border-top:1px solid #ebe4fd}
#mkfoot .grow{margin-left:auto}
#mkfoot #mksum b{color:#3d2a7a}
#mkfoot .warn,#mkstat .warn{color:var(--err);font-weight:600}
body.doc-mk #docTag{background:#f3efff;color:#5b34c9}
body.doc-mk #mkbar{box-shadow:inset 0 3px 0 #6a3fd1}
body.doc-mk #wrap td.sel{background:rgba(26,115,232,.05)}
body.doc-mk #wrap td.active{outline-color:rgba(26,115,232,.35)}
/* 新建确认框 */
.mk-dest{display:flex;align-items:center;gap:12px;background:#f8f5ff;border:1px solid #ebe4fd;border-radius:12px;padding:10px 14px;margin-bottom:12px}
.mk-dest .k{font-size:11.5px;color:#8a7fb0}
.mk-dest .v{font-size:12.5px;color:#3d2a7a;font-weight:600;word-break:break-all}
.mk-chips{display:flex;flex-wrap:wrap;gap:6px;margin:10px 0 6px;max-height:180px;overflow:auto}
.mk-chip{display:inline-flex;align-items:center;padding:4px 10px 4px 8px;border-radius:8px;background:#f6f3ff;
     border:1px solid #e6defc;font-size:12.5px;color:#3d2a7a}
.mk-more{font-size:12px;color:#8a7fb0;align-self:center}

/* ---------- 连接 Google Drive ---------- */
.drivepill{display:inline-flex;align-items:center;gap:7px;border-radius:999px;padding:3px 12px 3px 10px;
     font-size:12px;color:#5a6069;margin-right:4px;height:24px;background:#fff}
.drivepill i{width:8px;height:8px;border-radius:50%;background:#f4b400;box-shadow:0 0 0 3px rgba(244,180,0,.18);flex:none}
.drivepill.on{color:#1e6b3a}
.drivepill.on i{background:#34a853;box-shadow:0 0 0 3px rgba(52,168,83,.18)}
.drivepill.busy i{background:#1a73e8;box-shadow:0 0 0 3px rgba(26,115,232,.2);animation:gdpulse 1.1s ease-in-out infinite}
@keyframes gdpulse{0%,100%{opacity:1}50%{opacity:.35}}
@keyframes gdspin{to{transform:rotate(360deg)}}
@keyframes gdpop{0%{transform:scale(.6);opacity:0}70%{transform:scale(1.06);opacity:1}100%{transform:scale(1)}}
@keyframes gdfade{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}
@keyframes gddraw{to{stroke-dashoffset:0}}
#dlg.gdwrap{min-width:0;max-width:94vw;width:440px;border-radius:20px;overflow:hidden;user-select:none}
.gd{animation:gdfade .22s ease-out}
.gd-hero{position:relative;padding:30px 30px 20px;text-align:center;overflow:hidden;
     background:radial-gradient(120% 90% at 0% 0%,#e8f0ff 0%,rgba(232,240,255,0) 60%),
                radial-gradient(120% 90% at 100% 0%,#e3f6ee 0%,rgba(227,246,238,0) 55%),#fbfcfe}
.gd-hero:before,.gd-hero:after{content:"";position:absolute;border-radius:50%;pointer-events:none}
.gd-hero:before{width:160px;height:160px;right:-60px;top:-70px;background:rgba(52,168,83,.07)}
.gd-hero:after{width:120px;height:120px;left:-50px;bottom:-70px;background:rgba(26,115,232,.06)}
.gd-icon{position:relative;z-index:1;width:68px;height:68px;margin:0 auto 16px;border-radius:20px;background:#fff;
     display:flex;align-items:center;justify-content:center;
     box-shadow:0 10px 28px rgba(26,115,232,.16),0 2px 6px rgba(0,0,0,.05)}
.gd-icon.ok{background:linear-gradient(180deg,#3cc36a,#1e9e4f);box-shadow:0 10px 28px rgba(30,158,79,.3);animation:gdpop .45s ease-out}
.gd-icon.bad{background:linear-gradient(180deg,#ff8a7a,#e5534b);box-shadow:0 10px 28px rgba(229,83,75,.28)}
.gd-icon.spin:after{content:"";position:absolute;inset:-6px;border-radius:24px;border:2px solid transparent;
     border-top-color:#1a73e8;border-right-color:#34a853;animation:gdspin 1.1s linear infinite}
.gd-title{position:relative;z-index:1;font-size:19px;font-weight:700;color:#1f2328;letter-spacing:.3px}
.gd-sub{position:relative;z-index:1;margin-top:7px;color:#5f6b7a;font-size:13px;line-height:1.7}
.gd-body{padding:6px 30px 4px}
.gd-steps{list-style:none;margin:0;padding:6px 0 0;position:relative}
.gd-step{display:flex;gap:13px;align-items:flex-start;padding:8px 0;position:relative}
.gd-step:not(:last-child):before{content:"";position:absolute;left:11px;top:34px;bottom:-6px;width:2px;
     background:#e6eaef;border-radius:2px}
.gd-step.done:not(:last-child):before{background:#b9e4c8}
.gd-dot{flex:none;width:24px;height:24px;border-radius:50%;border:2px solid #dfe3e8;background:#fff;box-sizing:border-box;
     display:flex;align-items:center;justify-content:center;font-size:11.5px;font-weight:700;color:#a3abb5;
     position:relative;transition:all .25s}
.gd-step.active .gd-dot{border-color:#d6e4fb;color:#1a73e8}
.gd-step.active .gd-dot:after{content:"";position:absolute;inset:-2px;border-radius:50%;border:2px solid transparent;
     border-top-color:#1a73e8;animation:gdspin .8s linear infinite}
.gd-step.done .gd-dot{background:#34a853;border-color:#34a853;color:#fff}
.gd-step.fail .gd-dot{background:#e5534b;border-color:#e5534b;color:#fff}
.gd-step .t{font-weight:600;color:#9aa3ad;font-size:13.5px;line-height:24px;transition:color .25s}
.gd-step.active .t,.gd-step.done .t{color:#1f2328}
.gd-step.fail .t{color:#b3261e}
.gd-step .d{font-size:12px;color:#8a939d;margin-top:1px;line-height:1.55}
.gd-step:not(.active):not(.fail) .d{display:none}
.gd-step.idle .d{display:block;color:#a3abb5}
.gd-open{display:inline-block;margin-top:6px;font-size:12px;color:#1a73e8;cursor:pointer;text-decoration:underline;text-underline-offset:2px}
.gd-actions{display:flex;flex-direction:column;align-items:stretch;gap:6px;padding:16px 30px 8px}
.gd-row{display:flex;gap:8px;justify-content:center;flex-wrap:wrap}
button.gd-btn{height:46px;border-radius:13px;font-size:15px;font-weight:700;letter-spacing:.5px;border:0;color:#fff;cursor:pointer;
     background:linear-gradient(180deg,#3b8bff,#1a73e8);box-shadow:0 8px 20px rgba(26,115,232,.28);
     transition:transform .12s,box-shadow .12s,filter .12s}
button.gd-btn:hover{background:linear-gradient(180deg,#3b8bff,#1a73e8);transform:translateY(-1px);box-shadow:0 12px 26px rgba(26,115,232,.34);filter:brightness(1.03)}
button.gd-btn:active{transform:translateY(0);box-shadow:0 5px 14px rgba(26,115,232,.28)}
button.gd-btn.green{background:linear-gradient(180deg,#3cc36a,#1e9e4f);box-shadow:0 8px 20px rgba(30,158,79,.28)}
button.gd-btn.green:hover{background:linear-gradient(180deg,#3cc36a,#1e9e4f)}
button.gd-soft{height:34px;border-radius:10px;padding:0 14px;font-size:12.5px;background:#f2f4f7;border:1px solid transparent;color:#3c4550}
button.gd-soft:hover{background:#e8ecf1}
button.gd-soft.warn{color:#b3261e}
button.gd-soft.warn:hover{background:#fdecea}
button.gd-link{background:none;border:0;color:#7a8490;font-size:12.5px;padding:6px;height:auto}
button.gd-link:hover{background:none;color:#1f2328}
.gd-note{font-size:11.5px;color:#9aa3ad;text-align:center;padding:4px 34px 20px;line-height:1.65}
.gd-quota{margin:14px 0 2px;background:#f5f7fa;border-radius:14px;padding:13px 15px;text-align:left}
.gd-qrow{display:flex;justify-content:space-between;align-items:baseline;font-size:12.5px;color:#5f6b7a}
.gd-qrow b{color:#1f2328;font-size:13px}
.gd-bar{height:8px;border-radius:6px;background:#e3e8ee;overflow:hidden;margin-top:9px}
.gd-bar i{display:block;height:100%;border-radius:6px;background:linear-gradient(90deg,#34a853,#1a73e8);transition:width .6s ease}
.gd-qfree{font-size:11.5px;color:#8a939d;margin-top:7px}
.gd-err{margin-top:12px;background:#fdecea;color:#8c1d18;border-radius:12px;padding:11px 14px;font-size:12.5px;line-height:1.65;text-align:left}
.gd-confirm{font-size:12.5px;color:#8c1d18;text-align:center;margin-bottom:2px}
.gd-skel{height:88px;border-radius:14px;margin:10px 0 18px;
     background:linear-gradient(90deg,#f1f3f6 0%,#f8f9fb 50%,#f1f3f6 100%);background-size:200% 100%;animation:gdsk 1.2s linear infinite}
@keyframes gdsk{to{background-position:-200% 0}}

/* =====================================================================
   暗色主题（html.dark）。配色：深灰蓝底 + 柔和的蓝色强调，预览列用暖灰做区分。
   ===================================================================== */
html.dark{color-scheme:dark;
  --bd:#262d3a; --bd2:#333c4b; --hd:#161b23; --blue:#5b9bff; --blue-bg:rgba(91,155,255,.17);
  --txt:#d7dce4; --mut:#7f8896; --err:#ff7b72; --err-bg:rgba(255,123,114,.13); --ok:#5ec98b;
  --pv:#1a1a19; --pvhd:#22211c; --pvbd:#3a372c;
  --bg:#0f1319; --bg1:#161b23; --bg2:#1c222c; --bg3:#232a36;
}
html.dark body{background:var(--bg);color:var(--txt)}
html.dark #menubar{background:var(--bg1);border-bottom-color:var(--bd)}
html.dark .menu:hover>.mtitle,html.dark .menu.open>.mtitle{background:var(--bg3)}
html.dark .dropdown,html.dark #moremenu,html.dark #ctx{background:#1b212b;border-color:var(--bd2);
  box-shadow:0 10px 30px rgba(0,0,0,.55)}
html.dark .mi.disabled{color:#4d5563}
html.dark .msep{background:var(--bd)}
html.dark #actionbar{background:var(--bg1);border-bottom-color:var(--bd)}
html.dark .grp{border-right-color:var(--bd2)}
html.dark button{background:var(--bg3);border-color:var(--bd2);color:var(--txt)}
html.dark button:hover{background:#2b3341}
html.dark button:disabled{background:#1a1f28;color:#4d5563;border-color:var(--bd)}
html.dark button.primary{background:#2f6fe0;border-color:#2f6fe0;color:#fff}
html.dark button.primary:hover{background:#3d7cf0}
html.dark button.upload{background:#158a76;border-color:#158a76;color:#fff}
html.dark button.upload:hover{background:#1a9c86}
html.dark .sepv{background:var(--bd2)}
html.dark .seg{background:var(--bg2);border-color:var(--bd2)}
html.dark .seg button{background:transparent}
html.dark .seg button:hover{background:#2b3341}
html.dark .toggle{background:var(--bg3);border-color:var(--bd2);color:var(--txt)}
html.dark .toggle:hover{background:#2b3341}
html.dark .toggle.on{background:rgba(91,155,255,.16);border-color:rgba(91,155,255,.45);color:#9cc2ff}
html.dark #pathbar{background:var(--bg);border-bottom-color:var(--bd)}
html.dark #jumpbar{background:#17233a;border-bottom-color:#2b4370;color:#c9d6ee}
html.dark #upbar{background:#14261f;border-bottom-color:#25503f;color:#c9e6d6}
html.dark #upbar.fail{background:#2c1a1a;border-bottom-color:#5a2c2c}
html.dark #uplog{background:#090c11;color:#b8c6d6;border-bottom-color:#25503f}

/* 表格 */
html.dark th,html.dark td{background:var(--bg);border-color:var(--bd)}
html.dark thead th{background:var(--hd);color:#aeb6c2;border-bottom-color:var(--bd2)}
html.dark thead th.corner2{background:var(--hd)}
html.dark th.chkhd,html.dark td.chk{background:var(--hd)}
html.dark td.chk{background:var(--bg)}
html.dark th.rownum{background:var(--hd);color:var(--mut)}
html.dark th.rownum:hover{background:var(--bg3)}
html.dark tr.rsel th.rownum,html.dark tr.rsel td.chk,html.dark th.hdr.hl{background:#243652;color:#dbe6ff}
html.dark tbody tr:nth-child(even) td,html.dark tbody tr:nth-child(even) th.rownum{
  background-image:linear-gradient(rgba(255,255,255,.035),rgba(255,255,255,.035))}
html.dark tbody tr:hover td,html.dark tbody tr:hover th.rownum{
  background-image:linear-gradient(rgba(91,155,255,.13),rgba(91,155,255,.13))}
html.dark tbody tr:hover td.pv{background-image:linear-gradient(rgba(91,155,255,.09),rgba(91,155,255,.09))}
html.dark tbody tr:hover th.rownum{color:var(--blue)}
html.dark .box{background:var(--bg2);border-color:#4a5566}
html.dark .box.on{background:linear-gradient(180deg,#5b9bff,#2f6fe0);border-color:#2f6fe0;box-shadow:0 1px 3px rgba(0,0,0,.5)}
html.dark .box.some{background:var(--bg2);border-color:var(--blue)}
html.dark tr.skip td.chk .box{border-color:#3a4351}
html.dark td.linkcell{color:#7fb3ff}
html.dark td.linkcell .ph,html.dark .ph{color:#4d5563}
html.dark td.stat{color:#9aa4b2}
html.dark td.stat.ok{color:var(--ok);background:rgba(94,201,139,.10)}
html.dark td.stat.fail{color:var(--err);background:var(--err-bg)}
html.dark th.stathd{background:#15211b;color:#7fc99a}
html.dark td.sel{background:var(--blue-bg)}
html.dark tr.blank th.rownum{color:#3f4754}
html.dark tr.blank td{background:#121620;color:#3f4754}
html.dark tr.dir td.cell{background:#131a22}
html.dark .dirmark{background:#1e3358;color:#9cc2ff}
html.dark th.pv{background:var(--pvhd);color:#b3a887;border-left-color:var(--pvbd)}
html.dark td.pv{background:var(--pv);color:#9a958a}
html.dark td.pv.first{border-left-color:var(--pvbd)}
html.dark td.pv.sel{background:#2a2a22}
html.dark td.pnew{color:#c9c3b3}
html.dark td.pnew.chg{color:var(--ok)}
html.dark td.pnew.err{background:var(--err-bg)!important;color:var(--err)}
html.dark .ext{color:#5a5f68}
html.dark .resizer:hover{background:var(--blue)}
html.dark #fill{background:var(--blue);border-color:var(--bg)}
html.dark #editor{background:#1b212b;color:var(--txt);border-color:var(--blue)}
html.dark .collet{background:#2b3341;color:#aeb6c2}
html.dark th.hdr.hl .collet{background:#3a5a8f;color:#e3ecff}
html.dark .climit{background:#173222;color:#7fc99a}
html.dark thead tr.mrow th{background:#231e12;border-bottom-color:#7a5c1e}
html.dark thead tr.mrow th.mcorner,html.dark thead tr.mrow th.mchk{background:#2a2314;color:#d3a94a}
html.dark thead tr.mrow th.pv{background:#1f1c15}
html.dark .minput{background:#1a1712;border-color:#5a4a22;color:#e2c37a}
html.dark .minput:focus{border-color:#d3a94a;background:#20190e;box-shadow:0 0 0 2px rgba(211,169,74,.25)}
html.dark .minput.on{background:#332a12;border-color:#d3a94a}
html.dark .minput.bad{border-color:var(--err);background:#2c1a1a;color:var(--err)}
html.dark .minput::placeholder{color:#6a5a36}
html.dark td.mov{color:#d3b264;background:#191610}
html.dark td.mov .under{color:#5a5f68}
html.dark td.over{background:var(--err-bg);color:var(--err)}
html.dark td.over .cut{color:#b0564f}
html.dark td.pnew.toolong{background:#2c2410!important;color:#e2b25a}
html.dark #status .warn2{color:#e2b25a}
html.dark td.hit{box-shadow:inset 0 0 0 2px #b89a1e}
html.dark td.hitcur{background:#6b5410!important;box-shadow:inset 0 0 0 2px #e2b25a;color:#fff}
html.dark #dropmask{background:rgba(91,155,255,.16)}

/* 状态栏 / 弹窗 / 查找 */
html.dark #status{background:var(--bg1);border-top-color:var(--bd);color:#9aa4b2}
html.dark #formula{color:#9aa4b2}
html.dark #stUndo{background:#2e2410;border-color:#8a6a1e;color:#e2b25a}
html.dark #stUndo:hover{background:#3d3015}
html.dark #mask{background:rgba(0,0,0,.55)}
html.dark .dlg{background:#1b212b;color:var(--txt);box-shadow:0 24px 70px rgba(0,0,0,.6)}
html.dark .dlg h3,html.dark .dlg .foot{border-color:var(--bd)}
html.dark .sum .k,html.dark .row label{color:#aeb6c2}
html.dark .row input[type=text],html.dark .row input[type=number],html.dark .row select,
html.dark #findbar input[type=text]{background:#12161d;border-color:var(--bd2);color:var(--txt)}
html.dark .row input:focus,html.dark .row select:focus,html.dark #findbar input[type=text]:focus{border-color:var(--blue)}
html.dark pre.log{background:#0f1319;border-color:var(--bd);color:#c2ccd8}
html.dark .snap{background:var(--bg2);border-color:var(--bd)}
html.dark .snap:hover{background:#1f2c40;border-color:#345a8f}
html.dark .snap.cur{background:#15261d;border-color:#2c6a45}
html.dark details.sumd summary{color:var(--blue)}
html.dark #findbar{background:#1b212b;border-color:var(--bd2);box-shadow:0 14px 40px rgba(0,0,0,.6)}
html.dark #findbar .lb,html.dark #findbar .opts{color:#aeb6c2}
html.dark #findbar .scope{border-top-color:var(--bd)}
html.dark #findbar .x:hover{color:var(--txt)}
html.dark ::-webkit-scrollbar{width:11px;height:11px}
html.dark ::-webkit-scrollbar-track{background:var(--bg)}
html.dark ::-webkit-scrollbar-thumb{background:#2f3846;border:2px solid var(--bg);border-radius:8px}
html.dark ::-webkit-scrollbar-thumb:hover{background:#3d4756}

html.dark td.linkcell .lkh{color:#5f6875}
html.dark td.linkcell .lkid{color:#7fb3ff}
html.dark .dupb{background:#3a2e10;color:#e2b25a;border-color:#7a5c1e}
html.dark button.redo{background:#2e2410;border-color:#8a6a1e;color:#e2b25a}
html.dark button.redo:hover{background:#3d3015}
html.dark .dupwarn{background:#2a2312;border-color:#7a5c1e;color:#e2c37a}
html.dark .dupwarn b{color:#ffcf73}
html.dark .redowarn{background:#2c1a1a;border-color:#5a2c2c;color:#ffb3ad}
html.dark #splitter{background:var(--bg1);border-top-color:var(--bd);border-bottom-color:#25503f}
html.dark #splitter:after{background:#2c6a5c}
html.dark #splitter:hover{background:#17241f}
html.dark #splitter:hover:after{background:#3fb99f}
html.dark #dlpane{background:#0e1613;border-top-color:#25503f}
html.dark #dlbar{background:#14241f;border-bottom-color:#25503f}
html.dark #dlbar .dltitle{color:#5ecbaa}
html.dark #dlbar .lb{color:#8fb3a8}
html.dark #dlbar select{background:#0f1a17;border-color:#2c6a5c;color:#9ed8c6}

html.dark #dldest{background:#0f1a17;border-color:#2c6a5c;color:#9ed8c6}
html.dark #dldest:hover{border-color:#3fb99f}
html.dark button.dlgo{background:#158a76;border-color:#158a76;color:#fff}
html.dark button.dlgo:hover{background:#1a9c86}
html.dark button.dlbtn.on{background:#14241f;border-color:#2c6a5c;color:#5ecbaa}
html.dark #dlhint{background:#101b18;color:#7fa093;border-bottom-color:#1e3b33}
html.dark #dlwrap{background:#0e1613}
html.dark #dlwrap th,html.dark #dlwrap td{background-color:#0e1613;border-right-color:#1e3b33;border-bottom-color:#1e3b33}
html.dark #dlwrap thead th{background:#15251f;color:#7fc9b0;border-bottom-color:#25503f}
html.dark #dlwrap thead th.corner,html.dark #dlwrap thead th.corner2,html.dark #dlwrap th.chkhd,html.dark #dlwrap th.rownum{background:#12201b}
html.dark #dlwrap th.hdr.hl,html.dark #dlwrap tr.rsel th.rownum,html.dark #dlwrap tr.rsel td.chk{background:#1d3d34;color:#c9efe3}
html.dark #dlwrap th.pv{background:#14231e;color:#8fbfb0;border-left-color:#25503f}
html.dark #dlwrap td.pv{background-color:#101a17;color:#93a39d}
html.dark #dlwrap td.pv.first{border-left-color:#25503f}
html.dark #dlwrap th.stathd{background:#14231e;color:#7fc9b0}
html.dark #dlwrap tbody tr:nth-child(even) td,html.dark #dlwrap tbody tr:nth-child(even) th.rownum{
  background-image:linear-gradient(rgba(255,255,255,.03),rgba(255,255,255,.03))}
html.dark #dlwrap tbody tr:hover td,html.dark #dlwrap tbody tr:hover th.rownum{
  background-image:linear-gradient(rgba(63,185,159,.13),rgba(63,185,159,.13))}
html.dark #dlwrap tbody tr:hover th.rownum{color:#5ecbaa}
html.dark #dlwrap td.sel{background-color:rgba(63,185,159,.16)}
html.dark #dlwrap td.active{outline-color:#3fb99f}
html.dark #dlwrap #fill{background:#3fb99f;border-color:#0e1613}
html.dark #dlwrap #editor{border-color:#3fb99f}
html.dark #dlwrap .box.on{background:linear-gradient(180deg,#3fb99f,#158a76);border-color:#158a76}
html.dark #dlwrap .collet{background:#1e3b33;color:#7fc9b0}
html.dark #dlwrap .dirmark{background:#1e3b33;color:#7fc9b0}
html.dark #dlwrap td.linkcell .lkid{color:#5ecbaa}
html.dark #dlwrap td.stat.ready{color:#5ecbaa}
html.dark #dlwrap tr.fgrp-0{--fg-bg:#221c10;--fg-pv:#1b160d;--fg-bar:#e0a82e;--fg-bd:#3a2d12;--fg-tx:#f3c969}
html.dark #dlwrap tr.fgrp-1{--fg-bg:#11231b;--fg-pv:#0d1c15;--fg-bar:#34c78a;--fg-bd:#18382b;--fg-tx:#7ce2b6}
html.dark #dlwrap tr.fgrp-2{--fg-bg:#121d2d;--fg-pv:#0e1724;--fg-bar:#5299ff;--fg-bd:#1b3152;--fg-tx:#95c2ff}
html.dark #dlwrap tr.fgrp-3{--fg-bg:#1d152c;--fg-pv:#161022;--fg-bar:#a375ff;--fg-bd:#2d1f47;--fg-tx:#c8aeff}
html.dark #dlwrap tr.fgrp-4{--fg-bg:#261612;--fg-pv:#1e110e;--fg-bar:#f27b58;--fg-bd:#3d2018;--fg-tx:#ffab91}
html.dark #dlwrap tr.fgrp-5{--fg-bg:#102226;--fg-pv:#0c1a1d;--fg-bar:#29b8cf;--fg-bd:#16363d;--fg-tx:#7de3f2}
html.dark td.stat .pbar{background:#22332e}
html.dark #dlfoot #dlsum{color:#7fc9b0}
html.dark #docTag{background:#17233a;color:#9cc2ff}
html.dark body.doc-dl #docTag,html.dark.doc-dl #docTag{background:#14241f;color:#5ecbaa}
html.dark body.doc-dl #dlbar{box-shadow:inset 0 3px 0 #3fb99f}
html.dark #dlfoot{background:#14241f;border-top-color:#1e3b33;color:#8fc9b8}
html.dark #dlfoot .jp{color:#6f8f86}
html.dark .drivepill{color:#aeb6c2;background:#1b212b;border-color:#333c4b}
html.dark button.gd-btn,html.dark button.gd-btn:hover{background:linear-gradient(180deg,#4a93ff,#2a6fe0);color:#fff;border:0;
     box-shadow:0 8px 22px rgba(42,111,224,.35)}
html.dark button.gd-btn.green,html.dark button.gd-btn.green:hover{background:linear-gradient(180deg,#3cc36a,#1e9e4f);
     box-shadow:0 8px 22px rgba(30,158,79,.32)}
html.dark button.gd-link,html.dark button.gd-link:hover{background:none;border:0;color:#8a94a2}
html.dark button.gd-link:hover{color:#e6e9ee}
html.dark .drivepill.on{color:#7fd6a0}
html.dark .gd-hero{background:radial-gradient(120% 90% at 0% 0%,#1a2742 0%,rgba(26,39,66,0) 60%),
     radial-gradient(120% 90% at 100% 0%,#15291f 0%,rgba(21,41,31,0) 55%),#1b212b}
html.dark .gd-icon{background:#232a36;box-shadow:0 10px 28px rgba(0,0,0,.45)}
html.dark .gd-title,html.dark .gd-step.active .t,html.dark .gd-step.done .t,html.dark .gd-qrow b{color:#e6e9ee}
html.dark .gd-sub{color:#9aa4b2}
html.dark .gd-dot{background:#1b212b;border-color:#333c4b;color:#6b7482}
html.dark .gd-step.active .gd-dot{border-color:#2b3a55;color:#7fb0ff}
html.dark .gd-step:not(:last-child):before{background:#2a313d}
html.dark .gd-step.done:not(:last-child):before{background:#24553a}
html.dark .gd-step .t{color:#6b7482}
html.dark .gd-quota{background:#151a22}
html.dark .gd-qrow{color:#9aa4b2}
html.dark .gd-bar{background:#2a313d}
html.dark .gd-err{background:#2c1a1a;color:#ffb3ad}
html.dark .gd-confirm{color:#ffb3ad}
html.dark button.gd-soft{background:#232a36;color:#c9d1db}
html.dark button.gd-soft:hover{background:#2b3341}
html.dark button.gd-soft.warn{color:#ff8a80}
html.dark button.gd-link:hover{color:#e6e9ee}
html.dark .gd-skel{background:linear-gradient(90deg,#1f252f 0%,#262d38 50%,#1f252f 100%);background-size:200% 100%}
html.dark .ptabs{background:rgba(255,255,255,.06)}
html.dark .ptabs .ptab{color:#8a94a2;background:transparent}
html.dark .ptabs .ptab:hover{background:rgba(255,255,255,.06)}
html.dark .ptabs .ptab.on{background:#232a36;box-shadow:0 1px 3px rgba(0,0,0,.5)}
html.dark .ptabs .ptab.on.dl{color:#5ecbaa}
html.dark .ptabs .ptab.on.mk{color:#b39cff}
html.dark button.mkbtn.on{background:#221a3a;border-color:#5b44a8;color:#c3b0ff}
html.dark #mkpane{background:#12101a;border-top-color:#3a2f63}
html.dark #mkbar{background:#1a1628;border-bottom-color:#3a2f63}
html.dark #mkbar .lb{color:#a497c9}
html.dark #mkdest{background:#141120;border-color:#3a2f63;color:#c3b0ff}
html.dark button.mkgo,html.dark button.mkgo:hover{background:linear-gradient(180deg,#8a63f5,#6a3fd1);border-color:#6a3fd1;color:#fff}
html.dark #mkhint{background:#16131f;color:#9d93bd;border-bottom-color:#2a2442}
html.dark #mkhint i{background:#2a2345;color:#c3b0ff}
html.dark #mkwrap{background:#12101a}
html.dark #mkwrap th,html.dark #mkwrap td{background-color:#12101a;border-right-color:#2a2442;border-bottom-color:#2a2442}
html.dark #mkwrap thead th{background:#1c1730;color:#b7a6ef;border-bottom-color:#3a2f63}
html.dark #mkwrap thead th.corner,html.dark #mkwrap thead th.corner2,html.dark #mkwrap th.chkhd,html.dark #mkwrap th.rownum{background:#171325}
html.dark #mkwrap th.hdr.hl,html.dark #mkwrap tr.rsel th.rownum,html.dark #mkwrap tr.rsel td.chk{background:#2e2455;color:#e2d9ff}
html.dark #mkwrap th.pv,html.dark #mkwrap th.stathd{background:#1a1530;color:#a897e0;border-left-color:#3a2f63}
html.dark #mkwrap td.pv{background-color:#15121f;color:#a8a3bb}
html.dark #mkwrap tbody tr:nth-child(even) td,html.dark #mkwrap tbody tr:nth-child(even) th.rownum{
  background-image:linear-gradient(rgba(255,255,255,.03),rgba(255,255,255,.03))}
html.dark #mkwrap tbody tr:hover td,html.dark #mkwrap tbody tr:hover th.rownum{
  background-image:linear-gradient(rgba(139,108,240,.14),rgba(139,108,240,.14))}
html.dark #mkwrap td.sel{background-color:rgba(139,108,240,.18)}
html.dark #mkwrap td.active{outline-color:#9d80ff}
html.dark #mkwrap #fill{background:#9d80ff;border-color:#12101a}
html.dark #mkwrap #editor{border-color:#9d80ff}
html.dark #mkwrap .box.on{background:linear-gradient(180deg,#9d80ff,#6a3fd1);border-color:#6a3fd1}
html.dark #mkwrap .collet{background:#2a2345;color:#b7a6ef}
html.dark #mkwrap td.pnew.chg{color:#d9ccff}
html.dark #mkwrap td.stat.ready{color:#b39cff}
html.dark #mkwrap td.stat.ok{color:var(--ok);background:rgba(94,201,139,.10)}
html.dark #mkfoot{background:#1a1628;border-top-color:#2a2442;color:#a897e0}
html.dark #mkfoot #mksum b{color:#e2d9ff}
html.dark body.doc-mk #docTag,html.dark.doc-mk #docTag{background:#221a3a;color:#c3b0ff}
html.dark body.doc-mk #mkbar{box-shadow:inset 0 3px 0 #8a63f5}
html.dark .mk-dest{background:#1a1628;border-color:#2e2650}
html.dark .mk-dest .v{color:#e2d9ff}
html.dark .mk-chip{background:#1d1832;border-color:#2e2650;color:#d9ccff}
/* 主题切换按钮 */
#btnTheme{width:32px;padding:5px 0;text-align:center;font-size:14px;line-height:1}
</style>
<script>try{if(localStorage.getItem('theme')==='dark')document.documentElement.classList.add('dark');}catch(e){}</script>
</head>
<body>

<div id="menubar">
  <div class="brand">右键表格工具</div>
  <div class="menu" data-menu="file"><div class="mtitle">文件</div><div class="dropdown"></div></div>
  <div class="menu" data-menu="edit"><div class="mtitle">编辑</div><div class="dropdown"></div></div>
  <div class="menu" data-menu="insert"><div class="mtitle">插入</div><div class="dropdown"></div></div>
  <div class="menu" data-menu="tools"><div class="mtitle">命名规则</div><div class="dropdown"></div></div>
  <div class="menu" data-menu="upload"><div class="mtitle">上传</div><div class="dropdown"></div></div>
  <div class="menu" data-menu="view"><div class="mtitle">视图</div><div class="dropdown"></div></div>
  <div class="menu" data-menu="settings"><div class="mtitle">设置</div><div class="dropdown"></div></div>
  <div class="menu" data-menu="help"><div class="mtitle">帮助</div><div class="dropdown"></div></div>
  <span id="docTag" title="菜单里的编辑、插入、命名规则、视图，作用在这张表上。点一下另一张表就切换过去">✎ 正在编辑：改名表格</span>
  <button id="btnDrive" class="drivepill" onclick="openDriveSetup()" title="连接你自己的 Google Drive（上传、下载都用它）"><i></i>连接网盘</button>
</div>

<div id="jumpbar">
  <span>检测到资源管理器：</span>
  <span class="jp" id="jumpPath"></span>
  <button class="primary" onclick="jumpToDetected()">转到这个文件夹</button>
  <button onclick="hideJump()">忽略</button>
</div>

<div id="actionbar">
  <div class="ab-left">
    <div class="grp">
      <button onclick="openFolderDlg()" title="打开另一个文件夹">打开</button>
      <button onclick="reloadFiles()" title="重新扫描当前目录">重载</button>
      <label class="toggle" id="incDirsLab" title="打开后，当前目录下的子文件夹也会出现在表格里，可以一起改名。上传永远只传文件，不会上传文件夹。">
        <input type="checkbox" id="incDirs" onchange="toggleIncludeDirs(this.checked)">包含文件夹
      </label>
      <label class="toggle" id="vidOnlyLab" title="默认只显示视频。关掉后可以看到封面、日志等其它文件。">
        <input type="checkbox" id="vidOnly" onchange="toggleVideosOnly(this.checked)" checked>只显示视频
      </label>
    </div>
    <div class="grp">
      <button onclick="tool_number()" title="给勾选的行填序号">序号</button>
      <button onclick="copyFileNames()" title="Ctrl+Shift+C">复制文件名</button>
      <button onclick="copyFilePaths()" title="Ctrl+Shift+P">复制路径</button>
    </div>
    <div class="grp seg" title="只作用于当前选中的单元格">
      <button onclick="setWrapRange(false)">截断</button>
      <button onclick="setWrapRange(true)">换行</button>
    </div>
    <div class="grp">
      <button id="btnDl" class="dlbtn" onclick="toggleDownload(undefined,true)" title="打开 / 收起网盘下载面板（Ctrl+Shift+D）">⬇ 下载</button>
      <button id="btnMk" class="mkbtn" onclick="toggleMk(undefined,true)" title="批量新建文件夹：每行一个文件夹名（Ctrl+Shift+N）">📁 建文件夹</button>
    </div>
    <div class="grp morewrap">
      <button onclick="toggleMore(event)">更多 ▾</button>
      <div id="moremenu">
        <div class="mi" onclick="hideMore();insertCol(selRange().c1)">左侧插入列</div>
        <div class="mi" onclick="hideMore();insertCol(dataEnd(selRange().c2)+1)">右侧插入列</div>
        <div class="msep"></div>
        <div class="mi" onclick="hideMore();setWrapAll(false)">全部截断</div>
        <div class="mi" onclick="hideMore();setWrapAll(true)">全部换行</div>
        <div class="mi" onclick="hideMore();resetColWidths()">恢复默认列宽</div>
      </div>
    </div>
  </div>
  <div class="ab-right">
    <button id="btnUndo" onclick="undoRename()" disabled>撤销</button>
    <button class="primary" onclick="execRename()" title="Ctrl+Enter">执行重命名</button>
    <button class="upload" onclick="startUpload(false)" title="只上传勾选的文件；已传的会跳过">上传勾选</button>
    <button id="btnRetry" onclick="startUpload(true)" disabled style="display:none" title="有上传失败的行时出现：只重试失败的行">只重试失败</button>
    <button id="btnRedo" class="redo" onclick="startUpload('redo')" title="勾选里有「已传」的行时出现：把它们按当前填写的网盘链接重新上传一次">重新上传</button>
    <button id="btnTheme" onclick="toggleTheme()" title="切换亮色 / 暗色主题（Ctrl+Shift+L）">🌙</button>
  </div>
</div>
<div id="upbar">
  <span id="upstat">准备上传…</span>
  <span class="jp" id="upcur"></span>
  <button onclick="toggleUpLog()">日志</button>
  <button onclick="hideUpbar()">收起</button>
</div>
<pre class="uplog" id="uplog"></pre>
<div id="pathbar">
  <span>📁</span>
  <span id="dirlabel" title="点击更换文件夹"></span>
  <span id="pathmeta"></span>
</div>

<div id="wrap" tabindex="0">
  <table id="grid"></table>
  <div id="fill"></div><div id="vline"></div><div id="hline"></div>
  <textarea id="editor" spellcheck="false" rows="1"></textarea>
</div>

<div id="splitter" title="拖动可以调整下载面板的高度"></div>
<div id="dlpane">
  <div id="dlbar">
    <span class="ptabs"><button class="ptab on dl">⬇ 网盘下载</button><button class="ptab" onclick="toggleMk(true,true)">📁 新建文件夹</button></span>
    <span class="lb">保存到</span>
    <span id="dldest" onclick="pickDlFolder()" title="点击更换保存位置"></span>
    <button onclick="dlUseCurrentDir()" title="保存到当前这个文件夹">用当前文件夹</button>
    <span class="grow"></span>
    <button onclick="dlAddRows(5)" title="在下载表末尾加 5 行（也可以右键插入行/列）">＋5 行</button>
    <button onclick="dlToggleAllFolders(false)" title="将所有网盘文件夹的子素材折叠起来，主表只显示文件夹行，方便直接填前缀（如 v01）">📁 折叠子行</button>
    <button onclick="dlToggleAllFolders(true)" title="展开所有网盘文件夹内的子素材行（默认编号 (1)(2)(3)，也可单独改名）">📂 展开子行</button>
    <button onclick="dlResolveButton()" title="把粘贴的链接解析成具体文件；文件夹会收納为可折叠同色分组，子素材自动配 (1)(2)(3)">解析链接</button>
    <label class="lb" title="同时下载几个文件">同时
      <select id="dlWorkers" onchange="dlSaveWorkers()">
        <option value="1">1 个</option><option value="2">2 个</option>
        <option value="3" selected>3 个</option><option value="5">5 个</option>
      </select>
    </label>
    <button class="dlgo" onclick="dlStart()" title="下载勾选的行（折叠的文件夹会自动下载全部子素材）">开始下载</button>
    <button id="dlStopBtn" onclick="dlStop()" style="display:none">停止</button>
    <button onclick="dlClear()">清空</button>
    <button onclick="toggleDownload(false,true)">收起 ▾</button>
  </div>
  <div id="dlhint">把网盘链接粘到右边<b>「网盘链接」</b>列；<b>文件夹链接默认折叠为同色分组主行</b>，只需在文件夹主行填前缀（如 <b>v01</b>），里面多个素材会自动命名为 <b>v01(1)、v01(2)、v01(3)</b>！点击行号旁的 <b>▶ N项</b> 可展开/折叠子行单独修改后缀。</div>
  <div id="dlwrap" tabindex="0"><table id="dlgrid"></table></div>
  <div id="dlfoot">
    <span id="dlsum"></span>
    <span id="dlstat">还没有开始下载</span>
    <span class="jp" id="dlcur"></span>
    <button onclick="toggleDlLog()">日志</button>
  </div>
  <pre id="dllog"></pre>
</div>

<div id="mkpane">
  <div id="mkbar">
    <span class="ptabs"><button class="ptab" onclick="toggleDownload(true,true)">⬇ 网盘下载</button><button class="ptab on mk">📁 新建文件夹</button></span>
    <span class="lb">建在</span>
    <span id="mkdest" onclick="pickMkFolder()" title="点击更换位置"></span>
    <button onclick="mkUseCurrentDir()" title="建在当前这个文件夹">用当前文件夹</button>
    <span class="grow"></span>
    <button onclick="mkAddRows(5)" title="在表格末尾加 5 行（也可以右键插入行 / 列）">＋5 行</button>
    <button id="mkUndoBtn" onclick="mkUndo()" style="display:none" title="删除刚才新建、并且仍然是空的文件夹">撤销刚才的创建</button>
    <button id="mkGo" class="mkgo" onclick="mkStart()" title="新建勾选的文件夹（Ctrl+Enter）">创建文件夹</button>
    <button onclick="mkClear()">清空</button>
    <button onclick="toggleMk(false,true)">收起 ▾</button>
  </div>
  <div id="mkhint"><b>每一行 = 一个文件夹。</b>左边的列从左到右拼成文件夹名，右边实时预览；可以拖右下角填充编号（<i>第01集</i> 往下拖 → <i>第02集</i>…）、插入列、用顶部「统一」格给整列加同样的前缀或后缀，命名规则和查找替换也都能用。已经存在的文件夹会自动跳过，不会覆盖任何东西。</div>
  <div id="mkwrap" tabindex="0"><table id="mkgrid"></table></div>
  <div id="mkfoot">
    <span id="mksum"></span>
    <span id="mkstat"></span>
    <span class="grow"></span>
    <button onclick="mkOpenDest()" title="在资源管理器里打开「建在」的文件夹">打开文件夹</button>
  </div>
</div>

<div id="status">
  <button id="stUndo" onclick="undoRename()">撤销刚才的改名</button>
  <span id="stFiles"></span><span id="stChanged"></span><span id="stErr"></span>
  <span id="formula"></span><span class="sv" id="stSaved"></span>
</div>
<div id="dropmask">松开鼠标，打开这个文件夹</div>

<div id="findbar">
  <div class="x" onclick="closeFind()">×</div>
  <div class="fr"><span class="lb">查找</span><input type="text" id="f_find" placeholder="要找的内容">
    <span class="cnt" id="f_cnt"></span></div>
  <div class="opts">
    <label><input type="checkbox" id="f_case">区分大小写</label>
    <label><input type="checkbox" id="f_re">正则表达式</label>
    <label><input type="checkbox" id="f_whole">整格完全匹配</label>
    <button onclick="findStep(-1)">↑ 上一个</button><button onclick="findStep(1)">↓ 下一个</button>
  </div>
  <div class="fr"><span class="lb">替换为</span><input type="text" id="f_rep" placeholder="留空 = 删除找到的内容">
    <button onclick="replaceCur()">替换当前</button>
    <button class="primary" onclick="replaceAll()">全部替换</button></div>
  <div class="scope">
    <div class="fr"><span class="lb">限定列</span><input type="text" id="f_cols" placeholder="留空=全部；例 A 或 A,C 或 B-D"></div>
    <div class="fr"><span class="lb">限定行</span><input type="text" id="f_rows" placeholder="留空=全部；例 3 或 1-20 或 1-5,9"></div>
    <div class="sch">用表头的列字母、左侧的行号来圈定范围，两个都留空就是整张表。
      勾「只在选中区域内」则忽略上面两栏。</div>
    <div class="opts" style="margin-bottom:0">
      <label><input type="checkbox" id="f_sel">只在当前选中区域内</label>
      <label><input type="checkbox" id="f_master">同时替换统一单元格</label>
    </div>
  </div>
</div>

<div id="ctx"></div>
<div id="mask"><div class="dlg" id="dlg"></div></div>

<script>
/* =======================================================================
   模型：一行 = 一个文件名。
   所有「数据列」从左到右直接拼接 = 新文件名（扩展名自动沿用原文件的）。
   末尾两列是只读的预览对照列：原文件名 → 新文件名。
   ======================================================================= */
const APP_NAME='右键表格工具';
const P_ORIG='__porig__', P_NEW='__pnew__', P_LINK='__link__', P_STAT='__stat__';
const CHK_W=34, NUM_W=46, HEAD_COLS=2;
let cols=[];              // 只含可编辑的数据列（会被保存进工程文件）
let vcols=[];             // 实际渲染的列 = cols (+ 预览列 + 网盘链接)
let rows=[], DIR='', files=[];
let showPrev=true, showLink=true, showStat=true, keepExt=true, joinSep='', includeDirs=false, videosOnly=true;
/* 扩展名策略：keep=沿用原后缀  set=统一用 extSet  none=不要后缀
   extFill=true 时 set 只补给本来没有后缀的文件，已有后缀的保持原样 */
let extMode='keep', extSet='.mp4', extFill=true;
let maxName=180;          // 最终文件名（不含扩展名）总长上限，0 = 不限
let specialW={porig:180,pnew:180,link:180,stat:150};  // 预览列 / 链接列 / 状态列宽
let sel={r1:0,c1:0,r2:0,c2:0}, active={r:0,c:0};
let undoStack=[], redoStack=[], saveTimer=null, restorable=0, undoCount=0;
let hasRclone=false;
let theme=document.documentElement.classList.contains('dark')?'dark':'light';
function applyTheme(t){
  theme=(t==='dark')?'dark':'light';
  document.documentElement.classList.toggle('dark',theme==='dark');
  const b=$('btnTheme'); if(b){ b.textContent=theme==='dark'?'☀':'🌙'; }
  try{ localStorage.setItem('theme',theme); }catch(e){}
}
function toggleTheme(){ applyTheme(theme==='dark'?'light':'dark'); buildMenus(); scheduleSave(); }

const $=id=>document.getElementById(id);
let wrap=$('wrap'), gridEl=$('grid');
const editor=$('editor');
/* =======================================================================
   两张表共用同一套表格引擎：
     main = 上面的改名表，dl = 下面的下载表。
   每张表有自己的一份「文档」（列、行、选区、撤销栈、连接符……）。
   鼠标点到哪张表，引擎就切到那张表的文档上工作，所以两张表的功能完全一样。
   ======================================================================= */
const DOC_EL={main:{wrap:$('wrap'),grid:$('grid')}, dl:{wrap:$('dlwrap'),grid:$('dlgrid')}, mk:{wrap:$('mkwrap'),grid:$('mkgrid')}};
const DOC_NAMES=['main','dl','mk'];
function makeDocFor(n){ return n==='dl'?makeDlDoc():(n==='mk'?makeMkDoc():captureDoc()); }
let DOC='main';
const DOCS={main:null, dl:null, mk:null};
let docQuiet=false;      // 后台刷新另一张表时为 true：不动光标、填充柄、公式栏、菜单
let docLockN=0;          // 改名表的异步操作（执行改名、上传、换文件夹…）进行中，禁止切表
function captureDoc(){
  return {cols,vcols,rows,sel,active,undoStack,redoStack,joinSep,keepExt,maxName,
          extMode,extSet,extFill,
          showPrev,showLink,showStat,specialW,files,hits,hitIdx};
}
function loadDoc(d){
  ({cols,vcols,rows,sel,active,undoStack,redoStack,joinSep,keepExt,maxName,
    showPrev,showLink,showStat,specialW,files,hits,hitIdx}=d);
  extMode=d.extMode||(d.keepExt===false?'none':'keep');
  extSet=(d.extSet===undefined)?'.mp4':d.extSet;
  extFill=(d.extFill===undefined)?true:!!d.extFill;
}
function docOf(name){
  if(name===DOC) return captureDoc();
  if(!DOCS[name]) DOCS[name]=makeDocFor(name);
  return DOCS[name];
}
function setDocDom(name){
  DOC=name; wrap=DOC_EL[name].wrap; gridEl=DOC_EL[name].grid;
}
function switchDoc(to){
  if(to===DOC||docQuiet) return;
  if(docLockN>0 && to!=='main') return;
  if(editing) closeEditor(true);
  hideCtx();
  fillDrag=null; dragging=false; chkPaint=null;
  DOCS[DOC]=captureDoc();
  if(!DOCS[to]) DOCS[to]=makeDocFor(to);
  loadDoc(DOCS[to]);
  setDocDom(to);
  ['fill','vline','hline','editor'].forEach(id=>{ const el=$(id); if(el&&el.parentNode!==wrap) wrap.appendChild(el); });
  $('fill').style.display='none';
  document.body.classList.toggle('doc-dl',to==='dl');
  document.body.classList.toggle('doc-mk',to==='mk');
  document.body.classList.toggle('doc-main',to==='main');
  const tag=$('docTag');
  if(tag) tag.textContent={dl:'⬇ 正在编辑：下载表格',mk:'📁 正在编辑：新建文件夹',main:'✎ 正在编辑：改名表格'}[to];
  rebuildV(); buildMenus(); paintSel();
}
/* 在另一张表上悄悄执行一段同步代码（比如后台进度刷新），执行完原样切回来 */
function withDoc(name, fn){
  if(name===DOC) return fn();
  const cur=DOC, saved=captureDoc(), savedEditing=editing, q=docQuiet;
  if(!DOCS[name]) DOCS[name]=makeDocFor(name);
  loadDoc(DOCS[name]); setDocDom(name);
  docQuiet=true; editing=null;
  try{ return fn(); }
  finally{
    DOCS[name]=captureDoc();
    loadDoc(saved); setDocDom(cur);
    docQuiet=q; editing=savedEditing;
  }
}
function otherDocs(){ return DOC_NAMES.filter(d=>d!==DOC); }
/* 同一个处理函数绑到两张表上；sw=false 表示不因为这个事件切表（比如鼠标划过） */
function bindGrid(type,fn,sw){
  DOC_NAMES.forEach(d=>DOC_EL[d].grid.addEventListener(type,e=>{
    if(sw===false){ if(d!==DOC) return; }
    else { switchDoc(d); if(d!==DOC) return; }
    fn(e);
  }));
}
function bindWrap(type,fn){
  DOC_NAMES.forEach(d=>DOC_EL[d].wrap.addEventListener(type,e=>{
    switchDoc(d); if(d!==DOC) return; fn(e);
  }));
}
DOC_NAMES.forEach(d=>DOC_EL[d].wrap.addEventListener('mousedown',()=>switchDoc(d)));
const esc=s=>String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
const escA=s=>esc(s).replace(/"/g,'&quot;');
/* 0->A, 25->Z, 26->AA —— 表头的列字母，查找替换用它来圈定范围 */
function colLetter(i){ let s='',n=i+1; while(n>0){ const r=(n-1)%26; s=String.fromCharCode(65+r)+s; n=Math.floor((n-1)/26); } return s; }
function letterToIdx(s){
  s=String(s).trim().toUpperCase(); if(!/^[A-Z]+$/.test(s)) return -1;
  let n=0; for(const ch of s) n=n*26+(ch.charCodeAt(0)-64);
  return n-1;
}
const clamp=(v,a,b)=>Math.max(a,Math.min(b,v));
let nextId=1; const uid=p=>p+(nextId++)+'_'+Math.random().toString(36).slice(2,6);

function rebuildV(){
  const DL=DOC==='dl';
  vcols = cols.slice();
  if(DOC==='mk'){
    if(showStat) vcols.push({id:P_STAT,name:'状态',w:specialW.stat||160,type:'stat'});
    if(showPrev) vcols.push({id:P_NEW ,name:'预览：文件夹名',w:specialW.pnew||260,type:'pnew'});
    return;
  }
  if(showPrev) vcols.push({id:P_ORIG,name:DL?'网盘里的文件':'预览：原文件名',w:specialW.porig||180,type:'porig'});
  if(showLink) vcols.push({id:P_LINK,name:'网盘链接',w:specialW.link||180,type:'link'});
  if(showStat) vcols.push({id:P_STAT,name:DL?'下载状态':'上传状态',w:specialW.stat||150,type:'stat'});
  if(showPrev) vcols.push({id:P_NEW ,name:DL?'预览：下载后的名字':'预览：新文件名',w:specialW.pnew||180,type:'pnew'});
}
function rememberW(ci){
  const c=vcols[ci]; if(!c) return;
  if(c.type==='porig') specialW.porig=c.w;
  else if(c.type==='pnew') specialW.pnew=c.w;
  else if(c.type==='link') specialW.link=c.w;
  else if(c.type==='stat') specialW.stat=c.w;
}
function isWrap(ri,ci){
  const r=rows[ri], c=vcols[ci];
  return !!(r && c && r.wrap && r.wrap[c.id]);
}
function setWrapRange(on){
  const s=selRange();
  pushUndo();
  for(let r=s.r1;r<=s.r2;r++){
    if(!rows[r].wrap) rows[r].wrap={};
    for(let c=s.c1;c<=s.c2;c++){
      const id=vcols[c]&&vcols[c].id; if(!id) continue;
      if(on) rows[r].wrap[id]=true; else delete rows[r].wrap[id];
    }
  }
  commit();
  toast(on?'已把选中单元格设为换行（列宽不变，行高随内容变）':'已把选中单元格设为截断显示');
}
function setWrapAll(on){
  pushUndo();
  rows.forEach(r=>{
    r.wrap=r.wrap||{};
    vcols.forEach(c=>{ if(on) r.wrap[c.id]=true; else delete r.wrap[c.id]; });
  });
  commit();
  toast(on?'整张表已换行':'整张表已截断显示');
}
function resetColWidths(){
  pushUndo();
  cols.forEach(c=>{ c.w=160; });
  specialW={porig:180,pnew:180,link:180,stat:150};
  rebuildV(); commit();
  toast('列宽已恢复为默认固定宽度，可再拖列边缘调整');
}
function isData(ci){ return vcols[ci] && vcols[ci].type==='data'; }
function isLink(ci){ return vcols[ci] && vcols[ci].type==='link'; }
function isEditable(ci){ return isData(ci)||isLink(ci); }
function dataEnd(ci){ return clamp(ci,0,cols.length-1); }   // 把索引夹回数据列区域
function rowOn(r){ return r && r.checked!==false; }
function isDirRow(r){ return !!(r && r.kind==='dir'); }
function statLabel(r){
  if(DOC==='dl') return dlStatText(r);
  if(DOC==='mk') return mkStatText(r);
  if(!r||!r.file) return '';
  if(isDirRow(r)) return '不上传';
  const u=r.up||{};
  if(u.status==='ok') return u.id ? ('已传（'+String(u.id).slice(0,12)+'）') : '已传';
  if(u.status==='fail') return u.reason ? ('失败：'+u.reason) : '失败';
  if(u.status==='uploading'||u.status==='queued') return '上传中';
  return '未传';
}

function newRow(file, kind){
  kind = kind || 'file';
  const r={id:uid('r'),file:file||null,kind:file?kind:null,cells:{},checked:true,link:'',wrap:{},up:{status:'none'}};
  if(file && cols.length) r.cells[cols[0].id]=stemOf(file, kind);
  return r;
}
function getVal(ri,ci){
  const c=vcols[ci], r=rows[ri];
  if(!c||!r) return '';
  if(c.type==='porig') return DOC==='dl' ? (r.rname||'') : (r.file||'');
  if(c.type==='pnew')  return finalName(r)||'';
  if(c.type==='link')  return r.link||'';
  if(c.type==='stat')  return statLabel(r);
  return r.cells[c.id]||'';
}
function setVal(ri,ci,v){
  const c=vcols[ci], r=rows[ri];
  if(!c||!r) return;
  if(c.type==='link'){
    if(DOC==='dl' && String(v||'').trim()!==String(r.link||'').trim()) dlResetRow(r);
    r.link=v||''; return;
  }
  if(c.type!=='data') return;
  if(v==='') delete r.cells[c.id]; else r.cells[c.id]=v;
}

/* -------- 文件名计算 -------- */
const ILLEGAL=/[<>:"/\\|?*\x00-\x1f]/g;
function sanitize(s){ return String(s).replace(ILLEGAL,'').trim().replace(/\.+$/,''); }
const REAL_EXT_RX=/^\.[A-Za-z0-9]{1,7}$/;
function extOf(n, kind){
  if(kind==='dir') return '';
  const i=n.lastIndexOf('.');
  if(i<=0) return '';
  const cand=n.slice(i);
  return REAL_EXT_RX.test(cand) ? cand : '';
}
function stemOf(n, kind){
  if(kind==='dir') return n;
  const i=n.lastIndexOf('.');
  if(i<=0) return n;
  const cand=n.slice(i);
  return REAL_EXT_RX.test(cand) ? n.slice(0,i) : n;
}
/* "mp4" / ".MP4" / " .mp4 " 一律整理成 ".mp4"；空值返回 '' */
function normExt(s){
  const v=String(s||'').replace(ILLEGAL,'').replace(/\s+/g,'').replace(/^\.+/,'').replace(/\.+$/,'');
  return v ? '.'+v : '';
}
/* 这一行最终该用什么扩展名 */
function extFor(r){
  if(isDirRow(r)) return '';
  const own=extOf(r.file||'', r.kind);
  if(extMode==='none') return '';
  if(extMode==='set'){
    const e=normExt(extSet);
    if(!e) return own;
    return (extFill && own) ? own : e;
  }
  return own;
}
/* 这一行的扩展名是不是会被改动（用来做预览统计） */
function extChanged(r){
  if(isDirRow(r)) return false;
  return extFor(r)!==extOf(r.file||'', r.kind);
}

/* -------- 统一单元格：某列填了统一值，整列就都用它（原单元格内容不动）-------- */
function hasMaster(c){ return !!(c && c.type==='data' && c.master); }
/* 这一列这一行实际生效的值（统一单元格优先） */
function liveVal(r,c){ return hasMaster(c) ? c.master : (r.cells[c.id]||''); }

/* -------- 长度上限 --------
   每列可以设 c.max（字符数上限），maxName 是拼出来的文件名总长上限。
   表格里始终显示完整原文，只有算最终文件名时才截断，所以随时改上限都能重算。 */
function tooLong(v,max){ return max>0 && String(v).trim().length>max; }

/* 一行按列拿到各段文字；limit=true 时套用每列的字符上限 */
function rowParts(r,limit){
  return cols.map(c=>{
    let v=String(liveVal(r,c)).trim();
    if(limit && c.max>0 && v.length>c.max) v=v.slice(0,c.max);
    return v;
  });
}
const joinParts=ps=>ps.filter(x=>x!=='').join(joinSep);
function joinRow(r){ return joinParts(rowParts(r,false)); }   // 未截断的完整拼接

/* 总长超标时，每次从「当前最长的那一列」削一个字，
   这样结尾的编号列（-XY-v240 这种短列）永远不会被砍掉。 */
function cappedStem(r){
  const parts=rowParts(r,true);
  if(maxName>0){
    let guard=0;
    while(joinParts(parts).length>maxName && guard++<20000){
      let bi=-1,bl=1;
      parts.forEach((p,i)=>{ if(p.length>bl){ bl=p.length; bi=i; } });
      if(bi<0) break;
      parts[bi]=parts[bi].slice(0,bl-1);
    }
  }
  return joinParts(parts);
}
function isRowTrimmed(r){
  return sanitize(joinRow(r)) !== sanitize(cappedStem(r));
}
function dlParentOf(r){
  if(!r||!r.parentId) return null;
  const list=(typeof dlRowsRef==='function')?dlRowsRef():rows;
  return list.find(x=>x.id===r.parentId)||null;
}
function dlChildStem(parentRow, childRow){
  const pStem=parentRow?sanitize(cappedStem(parentRow)):'';
  const cRaw=sanitize(cappedStem(childRow));
  const idx=childRow.subIndex||1;
  if(!cRaw || /^\(\d+\)$/.test(cRaw) || /^[_-]/.test(cRaw)){
    const suf=cRaw||('('+idx+')');
    return pStem ? sanitize(pStem+suf) : '';
  }
  return cRaw;
}
function dlEffectiveStem(r){
  if(DOC==='dl' && r && r.isSubRow){
    return dlChildStem(dlParentOf(r), r);
  }
  return sanitize(cappedStem(r));
}
function finalName(r){
  if(!r.file) return null;
  if(!rowOn(r)) return r.file;                // 未勾选 -> 不改名
  if(DOC==='dl' && r.isFolderParent){
    const v=sanitize(cappedStem(r));
    return v || r.file;
  }
  const v=dlEffectiveStem(r);
  if(!v) return r.file;                       // 整行都空 -> 保持原名
  if(isDirRow(r)) return v;                   // 文件夹不加扩展名
  return v+extFor(r);
}

function computeIssues(){
  const finals=rows.map(finalName);
  const count={}, issues=new Set();
  const skip=r=>(DOC==='dl' && (r.isFolderParent || (!r.rname && !dlEffectiveStem(r))));
  rows.forEach((r,i)=>{
    if(!r.file||!rowOn(r)||!finals[i]||skip(r)) return;
    const k=finals[i].toLowerCase(); count[k]=(count[k]||0)+1;
  });
  const moving=new Set(rows.filter((r,i)=>r.file&&rowOn(r)&&finals[i]!==r.file).map(r=>r.file.toLowerCase()));
  rows.forEach((r,i)=>{
    if(!r.file||!rowOn(r)||!finals[i]||skip(r)) return;
    const n=finals[i], k=n.toLowerCase();
    if(count[k]>1) issues.add(i);
    else if(n!==r.file && !moving.has(k) && files.some(f=>f.toLowerCase()===k)) issues.add(i);
  });
  return {finals,issues};
}

/* =======================================================================
   渲染
   ======================================================================= */
function headerCheckClass(){
  const withFile=rows.filter(r=>r.file);
  const n=withFile.filter(rowOn).length;
  if(!withFile.length || n===0) return '';
  if(n===withFile.length) return ' on';
  return ' some';
}
/* -------- 列宽自适应：c.w 是"期望宽"，按窗口可用宽度等比缩放成显示宽 c.dw --------
   窗口宽 → 等比拉宽填满；窗口窄 → 等比收窄，但每列不小于 COL_MIN，再窄才出横向滚动条 */
const COL_MIN=72;
let fitF=1;
function fitCols(){
  const free=Math.max(0,wrap.clientWidth-CHK_W-NUM_W-1);
  const base=vcols.reduce((s,c)=>s+(c.w||160),0);
  fitF=(base>0&&free>0)?free/base:1;
  let sum=0;
  vcols.forEach(c=>{ c.dw=Math.max(COL_MIN,Math.floor((c.w||160)*fitF)); sum+=c.dw; });
  if(vcols.length && sum<free) vcols[vcols.length-1].dw+=free-sum;   // 取整余数补到最后一列
  return CHK_W+NUM_W+vcols.reduce((s,c)=>s+c.dw,0);
}
function refitAll(){
  applyColWidths();
  otherDocs().forEach(d=>withDoc(d, applyColWidths));
  placeFill();
}
function applyColWidths(){
  const tableW=fitCols();
  gridEl.style.width=tableW+'px';
  const cs=gridEl.querySelectorAll('colgroup col');
  vcols.forEach((c,i)=>{ const col=cs[i+HEAD_COLS]; if(col) col.style.width=c.dw+'px'; });
}
let fitTimer=null;
window.addEventListener('resize',()=>{
  clearTimeout(fitTimer);
  fitTimer=setTimeout(()=>{ if(editing) closeEditor(true); refitAll(); },40);
});
/* 从网盘网址里取文件夹 ID（和后端 drive_folder_id 同规则） */
function folderIdOf(url){
  let u=String(url||'').trim().replace(/&amp;/g,'&');
  if(!u) return '';
  const q=u.match(/[?&]q=([^&]+)/);
  if(/google\.com\/url/.test(u)&&q){ try{ u=decodeURIComponent(q[1]); }catch(e){} }
  const m=u.match(/(?:id=|\/folders\/)([A-Za-z0-9_-]{10,})/);
  return m?m[1]:'';
}
function driveLinkInfo(url){
  let u=String(url||'').trim().replace(/&amp;/g,'&').replace(/^"|"$/g,'');
  if(!u) return {kind:'',id:''};
  const q=u.match(/[?&]q=([^&]+)/);
  if(/google\.com\/url/.test(u)&&q){ try{ u=decodeURIComponent(q[1]); }catch(e){} }
  const ID='([A-Za-z0-9_-]{15,})';
  const pats=[[`/file/d/${ID}`,'file'],[`/(?:document|spreadsheets|presentation)/d/${ID}`,'file'],
              [`/folders/${ID}`,'folder'],[`[?&]id=${ID}`,'unknown']];
  for(const [p,k] of pats){
    const m=u.match(new RegExp(p));
    if(m) return {kind:(k==='unknown'&&(/\/uc\b|export=download/.test(u)))?'file':k,id:m[1]};
  }
  if(/^[A-Za-z0-9_-]{15,}$/.test(u)) return {kind:'unknown',id:u};
  return {kind:'',id:''};
}
function dlLinkHtml(r){
  const link=r.link;
  if(!link) return '<span class="ph">粘贴网盘文件 / 文件夹链接</span>';
  const info=driveLinkInfo(link);
  if(!info.id) return `<span class="lkbad">⚠ 看不出是网盘链接：</span>${esc(link)}`;
  const short=info.id.length>16?(info.id.slice(0,8)+'…'+info.id.slice(-6)):info.id;
  const head=info.kind==='folder'?'folders/':(info.kind==='file'?'file/':'id/');
  return `<span class="lkh">${head}</span><span class="lkid">${esc(short)}</span>`;
}
function linkHtml(link,dupCount){
  if(!link) return '<span class="ph">粘贴网盘文件夹网址</span>';
  const id=folderIdOf(link);
  if(!id) return `<span class="lkbad">⚠ 解析不出文件夹：</span>${esc(link)}`;
  const n=dupCount[id]||0;
  const badge=n>1?`<span class="dupb" title="有 ${n} 行填的是同一个文件夹">×${n}</span>`:'';
  const short=id.length>16?(id.slice(0,8)+'…'+id.slice(-6)):id;
  return `${badge}<span class="lkh">folders/</span><span class="lkid">${esc(short)}</span>`;
}
function render(){
  const DL=DOC==='dl', MK=DOC==='mk';
  if(DL) rows.forEach(dlSyncFile);
  if(MK) rows.forEach(mkSyncFile);
  rebuildV();
  const {finals,issues}=computeIssues();
  const dupCount={};
  if(!DL) rows.forEach(r=>{ if(r.file&&r.link){ const id=folderIdOf(r.link); if(id) dupCount[id]=(dupCount[id]||0)+1; } });
  const tableW=fitCols();
  gridEl.style.width=tableW+'px';
  let h='<colgroup><col style="width:'+CHK_W+'px"><col style="width:'+NUM_W+'px">'+vcols.map(c=>`<col style="width:${c.dw}px">`).join('')+'</colgroup>';
  h+='<thead><tr><th class="corner chkhd" title="全选 / 全不选（只作用于有文件的行）"><span class="box'+headerCheckClass()+'" id="hdrBox"></span></th><th class="corner2">#</th>';
  vcols.forEach((c,i)=>{
    const pv=c.type!=='data' && c.type!=='link';
    const letter=(c.type==='data')?`<span class="collet">${colLetter(i)}</span>`:'';
    const lim=(c.type==='data'&&c.max>0)?`<span class="climit">≤${c.max}</span>`:'';
    h+=`<th data-c="${i}" class="hdr${pv?' pv':''}${c.type==='link'?' linkhd':''}${c.type==='stat'?' stathd':''}"><span class="hdrlabel">${letter}${esc(c.name)}${lim}</span>`+
       `<span class="resizer" data-rz="${i}"></span></th>`;
  });
  h+='</tr>';
  /* 统一单元格行：独立在文件之外，填一次覆盖整列 */
  h+='<tr class="mrow"><th class="mchk"></th><th class="mcorner" title="统一单元格：在这里填一次，整列都用它；清空即恢复各行原内容">统一</th>';
  vcols.forEach((c,i)=>{
    if(c.type!=='data'){ h+='<th class="pv"></th>'; return; }
    const v=c.master||'';
    const bad=tooLong(v,c.max)?' bad':'';
    h+=`<th><input class="minput${v?' on':''}${bad}" data-m="${i}" value="${escA(v)}" `+
       `placeholder="统一 ${colLetter(i)} 列…" spellcheck="false"></th>`;
  });
  h+='</tr></thead><tbody>';
  rows.forEach((r,ri)=>{
    const skip=r.file && !rowOn(r);
    const isd=isDirRow(r);
    let fcls='';
    if(DL && r.folderGroup!==undefined && r.folderGroup!==null && r.folderGroup>=0){
      fcls+=` fgrp fgrp-${r.folderGroup%6}`;
      if(r.isFolderParent) fcls+=' fparent';
      else if(r.isSubRow) fcls+=' fsub';
    }
    let rnumHtml='';
    if(DL && r.isFolderParent){
      const chN=(r.children||[]).length;
      const arr=r.folderCollapsed?'▶':'▼';
      rnumHtml=`<span class="ftoggle" data-fid="${escA(r.id)}" title="点击${r.folderCollapsed?'展开':'折叠'}该文件夹内的 ${chN} 个素材子行">${arr} ${chN}项</span>${ri+1}`;
    } else if(DL && r.isSubRow){
      rnumHtml=`<span class="fsub-tag" title="所属文件夹第 ${r.subIndex||1} 个子素材">↳(${r.subIndex||1})</span>`;
    } else {
      rnumHtml=`${isd?'<span class="dirmark" title="文件夹，可改名，不上传">夹</span>':''}${(DL&&r.fromFolder)?'<span class="dirmark" title="从网盘文件夹链接展开出来的文件">夹</span>':''}${ri+1}`;
    }
    h+=`<tr data-r="${ri}" class="${r.file?'':'blank '}${skip?'skip ':''}${isd?'dir':''}${fcls}"><td class="chk" data-r="${ri}"><span class="box${rowOn(r)?' on':''}"></span></td><th class="rownum" data-r="${ri}">${rnumHtml}</th>`;
    vcols.forEach((c,ci)=>{
      let cls='cell', txt='';
      if(c.type==='data'){
        const raw=r.cells[c.id]||'';
        if(hasMaster(c)){
          cls+=' mov';
          txt=esc(c.master)+(raw&&raw!==c.master?`<span class="under">${esc(raw)}</span>`:'');
        } else {
          txt=esc(raw);
          if(tooLong(raw,c.max)){
            cls+=' over';
            txt=esc(raw.slice(0,c.max))+`<span class="cut">${esc(raw.slice(c.max))}</span>`;
          }
        }
      } else if(c.type==='porig'){
        cls+=' pv first';
        if(DL){
          if(r.isFolderParent){
            const chN=(r.children||[]).length;
            txt=`<b>📁 文件夹（共 ${chN} 个素材）</b>`+(r.size?`<span class="szt">${esc(fmtSize(r.size))}</span>`:'');
          } else if(r.rname) txt=`${esc(stemOf(r.rname,'file'))}<span class="ext">${esc(extOf(r.rname,'file'))}</span>`+
                          (r.size?`<span class="szt">${esc(fmtSize(r.size))}</span>`:'');
          else if(r.file) txt=`<span class="ph">${r.fid?'下载时取网盘原名':'待解析'}</span>`;
        } else
        txt=r.file?(isd?esc(r.file):`${esc(stemOf(r.file,r.kind))}<span class="ext">${esc(extOf(r.file,r.kind))}</span>`):'';
      } else if(c.type==='link'){
        cls+=' cell linkcell';
        txt=DL?dlLinkHtml(r):linkHtml(r.link, dupCount);
      } else if(c.type==='stat' && MK){
        cls+=' pv stat';
        const k=mkState(r);
        if(k==='ok') cls+=' ok'; else if(k==='fail'||k==='bad') cls+=' fail';
        else if(k==='exists') cls+=' wait'; else if(k==='new') cls+=' ready';
        txt=esc(mkStatText(r));
      } else if(c.type==='stat' && DL){
        cls+=' pv stat';
        const ds=r.ds||{}, st=ds.status||'none';
        if(!r.file){ txt=''; }
        else if(st==='ok'){ cls+=' ok'; txt=esc(dlStatText(r)); }
        else if(st==='fail'){ cls+=' fail'; txt=esc(dlStatText(r)); }
        else if(st==='downloading'){
          const p=Math.max(0,Math.min(100,+ds.pct||0));
          cls+=' run'; txt=`<span class="pbar"><i style="width:${p}%"></i></span>下载中 ${p}%`+(ds.msg?`<span class="rty">${esc(ds.msg)}</span>`:'');
        }
        else if(st==='queued'||st==='resolving'){ cls+=' wait'; txt=esc(dlStatText(r)); }
        else { if(r.fid) cls+=' ready'; txt=esc(dlStatText(r)); }
      } else if(c.type==='stat'){
        const st=(r.up&&r.up.status)||'none';
        cls+=' pv stat';
        if(isd) cls+=' skip';
        else if(st==='ok') cls+=' ok';
        else if(st==='fail') cls+=' fail';
        else if(st==='uploading'||st==='queued') cls+=' run';
        txt=esc(statLabel(r));
      } else {
        cls+=' pv pnew';
        const n=finals[ri];
        if(n&&MK){
          if(!rowOn(r)) txt='（不创建）';
          else {
            if(issues.has(ri)||mkState(r)==='bad') cls+=' err';
            else if(isRowTrimmed(r)) cls+=' toolong';
            else cls+=' chg';
            txt='<span class="fic"></span>'+esc(n);
          }
        }
        else if(n&&DL&&!rowOn(r)) txt='（不下载）';
        else if(DL&&r.isFolderParent){
          const chN=(r.children||[]).length;
          const pStem=sanitize(cappedStem(r));
          if(pStem){
            cls+=' chg';
            txt=`📁 <b>${esc(pStem)}(1) ～ ${esc(pStem)}(${chN})</b> <span class="ext">（共 ${chN} 项）</span>`;
          } else {
            txt=`<span class="ph">填前缀（如 v01）→ 自动编为 v01(1)～v01(${chN})</span>`;
          }
        }
        else if(n&&DL&&!r.rname){
          const stem=dlEffectiveStem(r);
          if(issues.has(ri)) cls+=' err';
          else if(stem) cls+=' chg';
          txt=stem ? `${esc(stem)}<span class="ext">${esc(extPreview(r))}</span>`
                   : `<span class="ph">${esc(dlRemoteShown(r))}</span>`;
        }
        else if(n){
          if(!rowOn(r)) txt='（不改名）';
          else {
            if(issues.has(ri)) cls+=' err';
            else if(isRowTrimmed(r)) cls+=' toolong';
            else if(n!==r.file) cls+=' chg';
            txt=isd?esc(n):`${esc(stemOf(n,r.kind))}<span class="ext">${esc(extOf(n,r.kind))}</span>`;
          }
        }
      }
      if(isWrap(ri,ci)) cls+=' wrap';
      let tip=getVal(ri,ci);
      if(c.type==='link' && r.link && looksLikeUrl(r.link)){
        cls+=' haslink';
        tip='按住 Ctrl 点击，在浏览器中打开\n'+r.link;
      }
      h+=`<td class="${cls}" data-r="${ri}" data-c="${ci}" title="${escA(tip)}">${txt}</td>`;
    });
    h+='</tr>';
  });
  h+='</tbody>';
  gridEl.innerHTML=h;
  bindMasters();
  paintSel();
  updateStatus(finals,issues);
  paintHits();
}

/* -------- 统一单元格的输入框 -------- */
function bindMasters(){
  const myDoc=DOC;
  gridEl.querySelectorAll('input.minput').forEach(inp=>{
    const ci=+inp.dataset.m;
    inp.addEventListener('mousedown',e=>{ e.stopPropagation(); switchDoc(myDoc); });
    inp.addEventListener('focus',()=>switchDoc(myDoc));
    inp.addEventListener('keydown',e=>{
      e.stopPropagation();
      if(e.key==='Enter'||e.key==='Escape') inp.blur();
    });
    inp.addEventListener('change',()=>{
      switchDoc(myDoc); if(DOC!==myDoc) return;
      const c=vcols[ci]; if(!c||c.type!=='data') return;
      const v=inp.value;
      if((c.master||'')===v) return;
      pushUndo();
      if(v==='') delete c.master; else c.master=v;
      commit();
      toast(v?`${colLetter(ci)} 列已被统一为「${v}」，清空这一格即可恢复各行原内容`
             :`${colLetter(ci)} 列的统一值已清除，恢复各行原内容`);
    });
  });
}
function tdAt(r,c){ return gridEl.querySelector(`td[data-r="${r}"][data-c="${c}"]`); }

function paintSel(){
  gridEl.querySelectorAll('td.sel,td.active').forEach(e=>e.classList.remove('sel','active'));
  gridEl.querySelectorAll('tr.rsel').forEach(e=>e.classList.remove('rsel'));
  gridEl.querySelectorAll('th.hdr.hl').forEach(e=>e.classList.remove('hl'));
  const s=selRange();
  for(let r=s.r1;r<=s.r2;r++){
    const tr=gridEl.querySelector(`tr[data-r="${r}"]`); if(tr) tr.classList.add('rsel');
    for(let c=s.c1;c<=s.c2;c++){ const td=tdAt(r,c); if(td) td.classList.add('sel'); }
  }
  for(let c=s.c1;c<=s.c2;c++){ const th=gridEl.querySelector(`th.hdr[data-c="${c}"]`); if(th) th.classList.add('hl'); }
  const a=tdAt(active.r,active.c); if(a) a.classList.add('active');
  placeFill(); showFormula();
}
function showFormula(){
  if(docQuiet) return;
  if(DOC==='mk') rows.forEach(mkSyncFile);
  const r=rows[active.r];
  if(!r||!r.file){ $('formula').textContent=''; return; }
  const parts=rowParts(r,false).filter(x=>x!=='');
  const n=finalName(r);
  const cut=isRowTrimmed(r)?`（已按上限截断，原长 ${sanitize(joinRow(r)).length} 字）`:'';
  if(DOC==='mk'){
    const nm=finalName(r);
    $('formula').innerHTML=`新建文件夹 第 ${active.r+1} 行： ${esc(parts.join(' + '))} → <b>📁 ${esc(nm||'')}</b>${cut}`;
    return;
  }
  const pre=DOC==='dl'?'下载表 ':'';
  const shown=(DOC==='dl'&&!r.rname)?(n===DL_PENDING?dlRemoteShown(r):n+extPreview(r)):n;
  $('formula').innerHTML= parts.length
    ? `${pre}第 ${active.r+1} 行： ${esc(parts.join(' + '))} → <b>${esc(shown)}</b>${cut}`
    : (DOC==='dl' ? `${pre}第 ${active.r+1} 行：名字留空 → 用网盘原名 ${esc(r.rname||'')}`
                  : `第 ${active.r+1} 行：整行为空 → 保持原名 ${esc(r.file)}`);
}
function placeFill(){
  if(docQuiet) return;
  const f=$('fill'), s=selRange(), td=tdAt(s.r2,s.c2);
  if(!td || editor.style.display==='block'){ f.style.display='none'; return; }
  const a=td.getBoundingClientRect(), w=wrap.getBoundingClientRect();
  f.style.left=(a.right-w.left+wrap.scrollLeft-5)+'px';
  f.style.top =(a.bottom-w.top +wrap.scrollTop -5)+'px';
  f.style.display='block';
}
DOC_NAMES.forEach(d=>DOC_EL[d].wrap.addEventListener('scroll',()=>{ if(d===DOC){ placeFill(); hideCtx(); } }));

function updateStatus(finals,issues){
  if(DOC==='dl'){ updateDlStatus(finals,issues); return; }
  if(DOC==='mk'){ updateMkStatus(finals,issues); return; }
  const bound=rows.filter(r=>r.file).length;
  const on=rows.filter(r=>r.file&&rowOn(r)).length;
  let chg=0; rows.forEach((r,i)=>{ if(r.file&&rowOn(r)&&finals[i]!==r.file) chg++; });
  const nDir=rows.filter(r=>r.file&&isDirRow(r)).length;
  const nFile=bound-nDir;
  $('stFiles').textContent=`${nFile} 个文件${nDir?` · ${nDir} 个文件夹`:''} · 勾选 ${on} · ${cols.length} 列`;
  const pm=$('pathmeta');
  if(pm) pm.textContent=includeDirs?(nDir?`含文件夹`:`含文件夹（当前没有子文件夹）`):'仅文件';
  $('stChanged').textContent=`将改名 ${chg} 个`;
  let over=0; rows.forEach(r=>cols.forEach(c=>{ if(!hasMaster(c)&&tooLong(r.cells[c.id]||'',c.max)) over++; }));
  const trimmed=rows.filter(r=>r.file&&rowOn(r)&&isRowTrimmed(r)).length;
  const bits=[];
  if(issues.size) bits.push(`<span class="warn">⚠ ${issues.size} 处命名冲突</span>`);
  if(over)    bits.push(`<span class="warn">${over} 格超列上限</span>`);
  if(trimmed) bits.push(`<span class="warn2">${trimmed} 行改名时会截断</span>`);
  $('stErr').innerHTML=bits.join(' &nbsp;·&nbsp; ');
  $('btnUndo').disabled=undoCount===0;
  const ub=$('stUndo');
  if(ub && !undoCount){ ub.classList.remove('show'); ub.disabled=true; }
  const failN=rows.filter(r=>r.file&&r.up&&r.up.status==='fail').length;
  const br=$('btnRetry');
  if(br){ br.disabled=failN===0; br.textContent=failN?('只重试失败 '+failN):'只重试失败'; br.style.display=failN?'':'none'; }
  const redoN=rows.filter(r=>r.file&&!isDirRow(r)&&rowOn(r)&&r.up&&r.up.status==='ok').length;
  const rb=$('btnRedo');
  if(rb){ rb.classList.toggle('show',redoN>0); rb.textContent='重新上传 '+redoN; }
}

/* =======================================================================
   撤销 / 重做（表格编辑）
   ======================================================================= */
function snapshot(){ return JSON.stringify({cols,rows,joinSep,maxName,showLink,showPrev,showStat,keepExt,extMode,extSet,extFill,specialW,includeDirs,videosOnly}); }
function pushUndo(){ undoStack.push(snapshot()); if(undoStack.length>80) undoStack.shift(); redoStack=[]; }
function applySnap(s){
  const d=JSON.parse(s);
  cols=d.cols; rows=d.rows; joinSep=d.joinSep||'';
  if(d.maxName!==undefined) maxName=d.maxName;
  if(d.showLink!==undefined) showLink=d.showLink;
  if(d.showPrev!==undefined) showPrev=d.showPrev;
  if(d.showStat!==undefined) showStat=d.showStat;
  if(d.keepExt!==undefined) keepExt=d.keepExt;
  if(d.extMode!==undefined) extMode=d.extMode;
  if(d.extSet!==undefined) extSet=d.extSet;
  if(d.extFill!==undefined) extFill=!!d.extFill;
  if(d.specialW) specialW=d.specialW;
  if(d.includeDirs!==undefined) includeDirs=!!d.includeDirs;
  if(d.videosOnly!==undefined) videosOnly=!!d.videosOnly;
  active.c=clamp(active.c,0,Math.max(0,vcols.length-1));
  rebuildV();
  buildMenus(); render(); scheduleSave();
  syncDirToggle();
}
function gridUndo(){ if(!undoStack.length) return; redoStack.push(snapshot()); applySnap(undoStack.pop()); }
function gridRedo(){ if(!redoStack.length) return; undoStack.push(snapshot()); applySnap(redoStack.pop()); }
function commit(){ render(); scheduleSave(); if(DOC==='dl') dlScheduleResolve(); }

/* =======================================================================
   选区与键盘
   ======================================================================= */
function selRange(){
  return {r1:Math.min(sel.r1,sel.r2),r2:Math.max(sel.r1,sel.r2),
          c1:Math.min(sel.c1,sel.c2),c2:Math.max(sel.c1,sel.c2)};
}
function setActive(r,c,extend){
  r=clamp(r,0,rows.length-1); c=clamp(c,0,vcols.length-1);
  if(extend){ sel.r2=r; sel.c2=c; } else sel={r1:r,c1:c,r2:r,c2:c};
  active={r,c};
  paintSel(); scrollIntoView(r,c);
}
function scrollIntoView(r,c){
  if(docQuiet) return;
  const td=tdAt(r,c); if(!td) return;
  const a=td.getBoundingClientRect(), w=wrap.getBoundingClientRect();
  const headH=60;   // 列标题行 + 统一单元格行
  if(a.top<w.top+headH) wrap.scrollTop-=(w.top+headH-a.top);
  if(a.bottom>w.bottom) wrap.scrollTop+=(a.bottom-w.bottom);
  if(a.left<w.left+CHK_W+NUM_W) wrap.scrollLeft-=(w.left+CHK_W+NUM_W-a.left);
  if(a.right>w.right) wrap.scrollLeft+=(a.right-w.right);
}

let dragging=false, chkPaint=null;
function openLinkOfRow(ri){
  const r=rows[ri]; const url=r&&r.link;
  if(!url||!looksLikeUrl(url)){ toast('这一格不是有效网址'); return; }
  post('/api/openurl',{url:url.trim()}).then(d=>{
    toast(d&&d.ok!==false?('已在浏览器中打开：'+url):('打不开这个网址：'+((d&&d.message)||'')));
  });
}
/* 按住 Ctrl 时给 body 加标记，链接格显示成可点击的样子 */
function setCtrlDown(on){ document.body.classList.toggle('ctrl-down',!!on); }
document.addEventListener('keydown',e=>{ if(e.key==='Control'||e.key==='Meta') setCtrlDown(true); });
document.addEventListener('keyup',e=>{ if(e.key==='Control'||e.key==='Meta') setCtrlDown(false); });
window.addEventListener('blur',()=>setCtrlDown(false));
document.addEventListener('mousemove',e=>{ if(!!(e.ctrlKey||e.metaKey)!==document.body.classList.contains('ctrl-down')) setCtrlDown(e.ctrlKey||e.metaKey); });
function toggleAllChecks(){
  const withFile=rows.filter(r=>r.file);
  const allOn=withFile.length && withFile.every(rowOn);
  pushUndo();
  rows.forEach(r=>{ if(r.file) r.checked=!allOn; });
  commit();
}
function setCheck(ri,on){
  const r=rows[ri]; if(!r) return;
  r.checked=!!on;
  if(DOC==='dl' && r.isFolderParent){
    (r.children||[]).forEach(ch=>{ ch.checked=!!on; });
    rows.forEach(x=>{ if(x.parentId===r.id) x.checked=!!on; });
  }
}
bindGrid('mousedown',e=>{
  hideCtx();
  const ft=e.target.closest('.ftoggle');
  if(ft){ e.preventDefault(); e.stopPropagation(); toggleFolderRow(ft.dataset.fid); return; }
  const rz=e.target.closest('.resizer');
  if(rz){ startResize(e,+rz.dataset.rz); return; }
  if(e.target.closest('th.corner')||e.target.closest('#hdrBox')){
    toggleAllChecks(); return;
  }
  const hdr=e.target.closest('th.hdr');
  if(hdr){ startHeaderDrag(e,+hdr.dataset.c); return; }
  const rn=e.target.closest('th.rownum');
  if(rn){ startRowDrag(e,+rn.dataset.r); return; }
  const box=e.target.closest('td.chk');
  if(box){
    const ri=+box.dataset.r;
    const on=!rowOn(rows[ri]);
    pushUndo();
    setCheck(ri,on);
    chkPaint={value:on};
    render();
    return;
  }
  const td=e.target.closest('td.cell');
  if(!td) return;
  if(e.button===0 && (e.ctrlKey||e.metaKey) && td.classList.contains('haslink')){
    e.preventDefault();
    openLinkOfRow(+td.dataset.r);
    return;
  }
  closeEditor(true);
  const r=+td.dataset.r, c=+td.dataset.c;
  if(e.shiftKey){ sel.r2=r; sel.c2=c; paintSel(); }
  else{ active={r,c}; sel={r1:r,c1:c,r2:r,c2:c}; paintSel(); dragging=true; }
  wrap.focus();
});
bindGrid('mousemove',e=>{
  if(chkPaint){
    const box=e.target.closest('td.chk')||(e.target.closest('tr[data-r]')&&e.target.closest('tr[data-r]').querySelector('td.chk'));
    if(!box) return;
    const ri=+box.dataset.r;
    if(rowOn(rows[ri])!==chkPaint.value){ setCheck(ri,chkPaint.value); const el=box.querySelector('.box'); if(el) el.classList.toggle('on',chkPaint.value); const tr=gridEl.querySelector(`tr[data-r="${ri}"]`); if(tr) tr.classList.toggle('skip', !!(rows[ri].file && !chkPaint.value)); }
    return;
  }
  if(!dragging) return;
  const td=e.target.closest('td.cell'); if(!td) return;
  sel.r2=+td.dataset.r; sel.c2=+td.dataset.c; paintSel();
},false);
document.addEventListener('mouseup',()=>{
  if(chkPaint){ chkPaint=null; commit(); }
  dragging=false;
});
bindGrid('dblclick',e=>{
  const hdr=e.target.closest('th.hdr');
  if(hdr){ renameCol(+hdr.dataset.c); return; }
  const td=e.target.closest('td.cell') || e.target.closest('th.rownum');
  if(!td) return;
  const r=+td.dataset.r, c=td.dataset.c!==undefined?+td.dataset.c:active.c;
  const row=rows[r];
  if(DOC==='mk' && vcols[c] && (vcols[c].type==='stat'||vcols[c].type==='pnew')){
    setActive(r,c,false); toast('完整内容：'+getVal(r,c)); return;
  }
  if(DOC==='dl' && vcols[c] && (vcols[c].type==='porig'||vcols[c].type==='stat'||vcols[c].type==='pnew')){
    setActive(r,c,false);
    toast(vcols[c].type==='porig'?(row&&row.rname?('网盘原名：'+row.rname):'这一行还没解析出网盘原名')
                                 :('完整内容：'+getVal(r,c)));
    return;
  }
  if(row && row.file && isDirRow(row)){
    setActive(r, isNaN(c)?0:c, false);
    post('/api/openfile',{file:row.file});
    return;
  }
  const col=vcols[c];
  if(row && row.file && col && col.type==='porig'){
    setActive(r,c,false);
    post('/api/openfile',{file:row.file});
    return;
  }
  if(td.classList.contains('cell')) openEditor(r,c);
});

bindWrap('keydown',e=>{
  if(editor.style.display==='block') return;
  const k=e.key, ctrl=e.ctrlKey||e.metaKey;
  if(ctrl&&k.toLowerCase()==='z'&&!e.shiftKey){ e.preventDefault(); gridUndo(); return; }
  if(ctrl&&(k.toLowerCase()==='y'||(k.toLowerCase()==='z'&&e.shiftKey))){ e.preventDefault(); gridRedo(); return; }
  if(ctrl&&k.toLowerCase()==='a'){ e.preventDefault(); sel={r1:0,c1:0,r2:rows.length-1,c2:vcols.length-1}; paintSel(); return; }
  if(ctrl&&k.toLowerCase()==='d'){ e.preventDefault(); fillDown(); return; }
  if(ctrl&&(k.toLowerCase()==='f'||k.toLowerCase()==='h')){ e.preventDefault(); openFind(); return; }
  if(ctrl&&k==='Enter'){ e.preventDefault(); if(DOC==='dl') dlStart(); else if(DOC==='mk') mkStart(); else execRename(); return; }
  if(ctrl&&e.shiftKey&&k.toLowerCase()==='c'){ e.preventDefault(); copyFileNames(); return; }
  if(ctrl&&e.shiftKey&&k.toLowerCase()==='l'){ e.preventDefault(); toggleTheme(); return; }
  if(ctrl&&e.shiftKey&&k.toLowerCase()==='d'){ e.preventDefault(); toggleDownload(undefined,true); return; }
  if(ctrl&&e.shiftKey&&k.toLowerCase()==='n'){ e.preventDefault(); toggleMk(undefined,true); return; }
  if(ctrl&&e.shiftKey&&k.toLowerCase()==='p'){ e.preventDefault(); copyFilePaths(); return; }
  if(k===' '){ e.preventDefault(); const s=selRange(); const anyOff=[]; for(let r=s.r1;r<=s.r2;r++) if(rows[r]&&!rowOn(rows[r])) anyOff.push(r); checkSel(anyOff.length>0); return; }
  const nav={ArrowUp:[-1,0],ArrowDown:[1,0],ArrowLeft:[0,-1],ArrowRight:[0,1]}[k];
  if(nav){
    e.preventDefault();
    if(ctrl) setActive(nav[0]<0?0:(nav[0]>0?rows.length-1:active.r),
                       nav[1]<0?0:(nav[1]>0?vcols.length-1:active.c), e.shiftKey);
    else setActive(active.r+nav[0],active.c+nav[1],e.shiftKey);
    return;
  }
  if(k==='Tab'){ e.preventDefault(); setActive(active.r,active.c+(e.shiftKey?-1:1),false); return; }
  if(k==='Enter'||k==='F2'){ e.preventDefault(); openEditor(active.r,active.c); return; }
  if(k==='Delete'||k==='Backspace'){ e.preventDefault(); clearSel(); return; }
  if(k==='Escape') return;
  if(!ctrl && !e.altKey && k.length===1){ openEditor(active.r,active.c,k); e.preventDefault(); }
});

function clearSel(){
  const s=selRange();
  if(!anyData(s)) return;
  pushUndo();
  for(let r=s.r1;r<=s.r2;r++) for(let c=s.c1;c<=s.c2;c++) setVal(r,c,'');
  commit();
}
function anyData(s){ for(let c=s.c1;c<=s.c2;c++) if(isEditable(c)) return true; return false; }

/* =======================================================================
   单元格编辑
   ======================================================================= */
let editing=null;
function openEditor(r,c,seed){
  if(!isEditable(c)){
    const full=getVal(r,c);
    if(full) toast('完整内容：'+full);
    else toast('预览列是只读的。双击可看完整文件名，或把该格设为换行 / 拖宽列。');
    return;
  }
  if(hasMaster(vcols[c]))
    toast(`注意：${colLetter(c)} 列被顶部统一单元格覆盖着，改这里暂时看不到效果（清空统一格即可恢复）`);
  const td=tdAt(r,c); if(!td) return;
  const a=td.getBoundingClientRect(), w=wrap.getBoundingClientRect();
  editor.value = seed!==undefined ? seed : getVal(r,c);
  editor.style.left=(a.left-w.left+wrap.scrollLeft)+'px';
  editor.style.top =(a.top -w.top +wrap.scrollTop )+'px';
  editor.style.width=Math.max(a.width,160)+'px';
  const tall=isWrap(r,c) || (editor.value||'').length>36;
  editor.style.height=(tall?Math.max(a.height,88):Math.max(a.height,29))+'px';
  editor.style.display='block';
  editing={r,c};
  $('fill').style.display='none';
  setTimeout(()=>{ editor.focus();
    if(seed===undefined) editor.select();
    else editor.setSelectionRange(editor.value.length,editor.value.length); },0);
}
function closeEditor(save){
  if(!editing) return;
  const {r,c}=editing; editing=null;
  const v=editor.value;
  editor.style.display='none';
  if(save && v!==getVal(r,c)){ pushUndo(); setVal(r,c,v); commit(); }
  else paintSel();
  wrap.focus();
}
editor.addEventListener('keydown',e=>{
  e.stopPropagation();
  if(e.key==='Enter'&&!e.shiftKey){ e.preventDefault(); const {r,c}=editing; closeEditor(true); setActive(r+1,c,false); }
  else if(e.key==='Escape'){ e.preventDefault(); editing=null; editor.style.display='none'; paintSel(); wrap.focus(); }
  else if(e.key==='Tab'){ e.preventDefault(); const {r,c}=editing; closeEditor(true); setActive(r,c+(e.shiftKey?-1:1),false); }
});
editor.addEventListener('blur',()=>closeEditor(true));
editor.addEventListener('paste',e=>{
  if(!editing||!e.clipboardData) return;
  const text=e.clipboardData.getData('text/plain');
  const html=e.clipboardData.getData('text/html');
  const {r,c}=editing;
  const res=clipToGrid(text,html,c);
  const data=res.data;
  const multi=data.length>1||(data[0]&&data[0].length>1)||res.multi;
  if(multi){
    /* 复制的是多格：不能塞进一个格子。退出编辑，从这一格开始一格对一格粘贴 */
    e.preventDefault();
    logPaste(text,html,res);
    editing=null; editor.style.display='none';
    active={r,c}; sel={r1:r,c1:c,r2:r,c2:c};
    wrap.focus();
    pasteLinked=res.linked;
    pasteAt(r,c,data);
    return;
  }
  if(!isLink(c)||!html) return;
  const v=data[0]&&data[0][0];
  if(!v||!looksLikeUrl(v)) return;
  e.preventDefault();
  editor.value=v;
  try{ editor.setSelectionRange(v.length,v.length); }catch(err){}
});

/* =======================================================================
   剪贴板
   ======================================================================= */
function parseTSV(text){
  const out=[]; let row=[],cur='',i=0,inQ=false;
  while(i<text.length){
    const ch=text[i];
    if(inQ){
      if(ch==='"'){ if(text[i+1]==='"'){cur+='"';i+=2;continue;} inQ=false;i++;continue; }
      cur+=ch;i++;continue;
    }
    if(ch==='"'&&cur===''){ inQ=true;i++;continue; }
    if(ch==='\t'){ row.push(cur);cur='';i++;continue; }
    if(ch==='\r'){ i++;continue; }
    if(ch==='\n'){ row.push(cur);out.push(row);row=[];cur='';i++;continue; }
    cur+=ch;i++;
  }
  row.push(cur); out.push(row);
  while(out.length&&out[out.length-1].every(x=>x==='')) out.pop();
  return out;
}
function tsvOfSel(){
  const s=selRange(), lines=[];
  for(let r=s.r1;r<=s.r2;r++){
    const cells=[];
    for(let c=s.c1;c<=s.c2;c++){
      let v=getVal(r,c);
      if(/[\t\n"]/.test(v)) v='"'+v.replace(/"/g,'""')+'"';
      cells.push(v);
    }
    lines.push(cells.join('\t'));
  }
  return lines.join('\n');
}
document.addEventListener('copy',e=>{
  if(editing||document.activeElement!==wrap) return;
  e.preventDefault(); e.clipboardData.setData('text/plain',tsvOfSel());
});
document.addEventListener('cut',e=>{
  if(editing||document.activeElement!==wrap) return;
  e.preventDefault(); e.clipboardData.setData('text/plain',tsvOfSel()); clearSel();
});
/* -------- 从剪贴板 HTML 里抠超链接（Google Sheet 的网址只在 text/html 里）--------
   三条都认：data-sheets-hyperlink → td 里的 a[href] → data-sheets-formula 的 =HYPERLINK("url")
   Sheet 常把真实地址包成 google.com/url?q=…，再把 q 解出来。 */
function decodeEntities(t){
  return String(t==null?'':t).replace(/&nbsp;/gi,' ').replace(/&amp;/g,'&').replace(/&lt;/g,'<')
    .replace(/&gt;/g,'>').replace(/&quot;/g,'"').replace(/&#39;/g,"'").replace(/&#x27;/gi,"'");
}
function hrefFromFormula(t){
  const raw=String(t==null?'':t);
  const m=raw.match(/^\s*=\s*HYPERLINK\s*\(\s*"([^"]*)"/i)||raw.match(/^\s*=\s*HYPERLINK\s*\(\s*'([^']*)'/i);
  return m?m[1]:'';
}
function unwrapGoogleUrl(href){
  const raw=String(href==null?'':href).trim();
  if(!raw) return '';
  try{ const u=new URL(raw);
       if(u.hostname==='www.google.com'&&u.pathname==='/url'){ const q=u.searchParams.get('q'); if(q) return q; }
  }catch(e){}
  return raw;
}
function cellFromTd(td){
  const text=String(td.textContent||'').replace(/\u00a0/g,' ').replace(/\s+/g,' ').trim();
  let href=td.getAttribute('data-sheets-hyperlink')||'';
  if(!href){ const a=td.querySelector('a[href]'); if(a) href=a.getAttribute('href')||''; }
  if(!href) href=hrefFromFormula(td.getAttribute('data-sheets-formula')||'');
  href=href?unwrapGoogleUrl(decodeEntities(href)):'';
  return {text,href:href||null};
}
function expandSpanRows(rawRows){
  const occ=[],out=[];
  function mark(r,c){ while(out.length<=r){out.push([]);occ.push([]);} while(out[r].length<=c){out[r].push(null);occ[r].push(false);} }
  for(let r=0;r<rawRows.length;r++){
    let c=0; const cells=rawRows[r]||[];
    for(let k=0;k<cells.length;k++){
      mark(r,c); while(occ[r][c]){ c+=1; mark(r,c); }
      const cell=cells[k]||{}, cs=Math.max(1,cell.colspan|0), rs=Math.max(1,cell.rowspan|0);
      const packed={text:cell.text||'',href:cell.href||null};
      for(let i=0;i<rs;i++) for(let j=0;j<cs;j++){ mark(r+i,c+j); occ[r+i][c+j]=true; out[r+i][c+j]=packed; }
      c+=cs;
    }
  }
  const width=out.reduce((m,row)=>Math.max(m,row.length),0);
  out.forEach(row=>{ while(row.length<width) row.push({text:'',href:null}); for(let j=0;j<row.length;j++) if(!row[j]) row[j]={text:'',href:null}; });
  return out;
}
function parseHtmlGridLinks(html){
  html=String(html==null?'':html);
  if(!html.trim()) return null;
  try{
    const doc=new DOMParser().parseFromString(html,'text/html');
    const table=doc.querySelector('table'); if(!table) return null;
    const raw=[];
    table.querySelectorAll('tr').forEach(tr=>{
      const row=[];
      Array.from(tr.children).forEach(td=>{
        const tag=td.tagName.toLowerCase(); if(tag!=='td'&&tag!=='th') return;
        const p=cellFromTd(td);
        p.colspan=parseInt(td.getAttribute('colspan')||'1',10)||1;
        p.rowspan=parseInt(td.getAttribute('rowspan')||'1',10)||1;
        row.push(p);
      });
      raw.push(row);
    });
    const grid=expandSpanRows(raw);
    return grid.length?grid:null;
  }catch(e){ return null; }
}
function firstHrefFromHtml(html){
  const grid=parseHtmlGridLinks(html);
  if(grid) for(const row of grid) for(const cell of row) if(cell&&cell.href) return cell.href;
  const a=String(html||'').match(/<a\b[^>]*href\s*=\s*(?:"([^"]*)"|'([^']*)')/i);
  if(a) return unwrapGoogleUrl(decodeEntities(a[1]||a[2]||''));
  const hy=String(html||'').match(/data-sheets-hyperlink\s*=\s*(?:"([^"]*)"|'([^']*)')/i);
  if(hy) return unwrapGoogleUrl(decodeEntities(hy[1]||hy[2]||''));
  return null;
}
/* 纯文本网格和 HTML 网格行数必须一致；HTML 可以更宽（尾列空格），不能更窄 */
function linksAlign(textGrid,linkGrid){
  if(!textGrid||!linkGrid||textGrid.length!==linkGrid.length) return false;
  const tC=textGrid.reduce((m,r)=>Math.max(m,r.length),0), lC=linkGrid.reduce((m,r)=>Math.max(m,r.length),0);
  if(lC<tC) return false;
  if(lC>tC) for(const row of linkGrid) for(let j=tC;j<row.length;j++) if(row[j]&&String(row[j].text||'').trim()) return false;
  return true;
}
/* 把 HTML 里的超链接合并进文本网格：目标列是网盘链接列（或非编辑列时自动路由到链接列）就用网址替换显示名。
   返回 {data, linked}：linked = 替换了几格 */
function mergeLinksIntoGrid(data,links,preferLink){
  if(!links||!preferLink) return {data,linked:0};
  let linked=0;
  const out=data.map((line,i)=>line.map((v,j)=>{
    const cell=links[i]&&links[i][j];
    if(cell&&cell.href&&!looksLikeUrl(v)){ linked++; return cell.href; }
    return v;
  }));
  return {data:out,linked};
}
let pasteLinked=0, pasteMulti=false;
/* 把剪贴板（纯文本 + HTML）解析成要粘贴的网格。
   规则：
   · HTML 里有表格时，行列数以 HTML 表格为准（一格对一格、顺序不变）；
     纯文本行列对得上就用纯文本的字，对不上就用 HTML 单元格里的字。
   · 每一格的超链接只属于这一格，绝不拿第一格的链接去填别的格。
   · 只有剪贴板里真的只有一格时，才允许"一格铺满选区"。 */
function clipToGrid(text,html,c0,forceLink){
  let data=parseTSV(text||'');
  let links=null;
  const lg=html?parseHtmlGridLinks(html):null;
  if(lg&&lg.length){
    const lgCells=lg.length*lg[0].length;
    if(data.length&&linksAlign(data,lg)){ links=lg; }
    else if(lgCells>1||!data.length){
      data=lg.map(r=>r.map(c=>c.text));
      links=lg;
    }
  } else if(!data.length && html){
    /* 没有表格结构的 HTML：只有一个链接才用它，多个链接宁可不贴也不乱贴 */
    const all=String(html).match(/<a\b[^>]*href\s*=/gi)||[];
    if(all.length<=2){ const h=firstHrefFromHtml(html); if(h) data=[[h]]; }
  }
  let linked=0;
  if(links&&data.length){
    const allLinked=links.every(r=>r.every(c=>!String(c.text||'').trim()||c.href));
    const preferLink=!!forceLink||isLink(c0)||(!isEditable(c0)&&allLinked)||(data[0].length===1&&allLinked&&!isData(c0));
    const m=mergeLinksIntoGrid(data,links,preferLink);
    data=m.data; linked=m.linked;
  }
  const cells=data.length?data.length*data[0].length:0;
  return {data,linked,multi:cells>1||(lg&&lg.length*lg[0].length>1),links};
}
function logPaste(text,html,res){
  if(!html) return;
  try{
    const parsed=(res.data||[]).map((row,i)=>(i+1)+': '+row.join(' | ')).join('\n');
    post('/api/pastelog',{text:text||'',html:html||'',parsed});
  }catch(e){}
}
document.addEventListener('paste',e=>{
  if(editing||document.activeElement!==wrap) return;
  e.preventDefault();
  const text=e.clipboardData.getData('text/plain');
  const html=e.clipboardData.getData('text/html');
  const res=clipToGrid(text,html,active.c);
  logPaste(text,html,res);
  let data=res.data;
  if(!data.length) return;
  pasteLinked=res.linked; pasteMulti=!!res.multi;
  /* 选中了一片区域时：在选区范围内平铺重复，一格内容就铺满整个选区 */
  const s=selRange();
  const selH=s.r2-s.r1+1, selW=s.c2-s.c1+1;
  const dH=data.length, dW=data[0].length;
  /* 只有剪贴板真的是 1 格时才铺满选区；多格一律从选区左上角按原样一格对一格贴 */
  if(!pasteMulti && dH===1 && dW===1 && (selH>1||selW>1)) pasteTiled(s,data);
  else pasteAt(s.r1,s.c1,data);
  pasteMulti=false;
});
/* 批量粘贴：把剪贴板内容按选区大小平铺（选区比内容大时重复，比内容小时只填选区） */
function pasteTiled(s,data){
  let writable=false;
  for(let c=s.c1;c<=s.c2;c++) if(isEditable(c)) writable=true;
  if(!writable){ toast('预览列不能粘贴。请选中左边的编辑列，或「网盘链接」列。'); return; }
  pushUndo();
  const dH=data.length, dW=data[0].length;
  let n=0;
  for(let r=s.r1;r<=s.r2;r++){
    if(r>=rows.length) break;
    for(let c=s.c1;c<=s.c2;c++){
      if(!isEditable(c)) continue;
      setVal(r,c,data[(r-s.r1)%dH][(c-s.c1)%dW]);
      n++;
    }
  }
  sel={r1:s.r1,c1:s.c1,r2:Math.min(s.r2,rows.length-1),c2:s.c2};
  active={r:s.r1,c:s.c1};
  commit();
  const one=(dH===1&&dW===1);
  toast(one?`已把内容填入选中的 ${n} 个单元格`
           :`已在选区内平铺粘贴 ${dH}×${dW} 的内容，共填 ${n} 个单元格`);
}
function pasteAt(r0,c0,data){
  if(!isEditable(c0) && data.length && data.every(line=>looksLikeUrl(line[0]))){
    const li=vcols.findIndex(c=>c.type==='link');
    if(li>=0){ pasteAt(r0,li,data); return; }
  }
  if(isLink(c0)){
    pushUndo();
    const needRows=r0+data.length-rows.length;
    for(let i=0;i<needRows;i++) rows.push({id:uid('r'),file:null,cells:{},checked:true,link:'',wrap:{}});
    data.forEach((line,i)=>setVal(r0+i,c0,line[0]||''));
    sel={r1:r0,c1:c0,r2:r0+data.length-1,c2:c0};
    active={r:r0,c:c0};
    commit();
    const ids=new Set(data.map(l=>folderIdOf(l[0])).filter(Boolean));
    toast(`已把 ${data.length} 条网盘链接按顺序粘贴到第 ${r0+1}–${r0+data.length} 行，共 ${ids.size} 个不同文件夹`+(pasteLinked?`（${pasteLinked} 条从 Google Sheet 超链接/芯片里解析）`:''));
    pasteLinked=0;
    return;
  }
  if(!isData(c0)){ toast('预览列不能粘贴。点一下「网盘链接」列再粘贴网址。'); return; }
  pushUndo();
  const needCols=c0+data[0].length-cols.length;
  for(let i=0;i<needCols;i++) cols.push({id:uid('c'),name:nextColName(cols.length),w:190,type:'data'});
  const needRows=r0+data.length-rows.length;
  for(let i=0;i<needRows;i++) rows.push({id:uid('r'),file:null,cells:{},checked:true,link:'',wrap:{}});
  rebuildV();
  data.forEach((line,i)=>line.forEach((v,j)=>{
    if(isEditable(c0+j)) setVal(r0+i,c0+j,v);
  }));
  sel={r1:r0,c1:c0,r2:r0+data.length-1,c2:c0+data[0].length-1};
  active={r:r0,c:c0};
  commit();
  if(needRows>0) toast(`已粘贴 ${data.length} 行（新增 ${needRows} 个空行，空行没有对应文件、不参与改名）`);
  else if(needCols>0) toast(`已粘贴 ${data.length} 行 × ${data[0].length} 列（新增 ${needCols} 列）`);
}
function nextColName(i){ return String.fromCharCode(65+(i%26))+(i>=26?Math.floor(i/26):'')+' 列'; }

/* =======================================================================
   填充柄
   ======================================================================= */
let fillDrag=null;
$('fill').addEventListener('mousedown',e=>{
  e.preventDefault(); e.stopPropagation();
  fillDrag={base:selRange()};
  fillDrag.to=fillDrag.base.r2;
});
document.addEventListener('mousemove',e=>{
  if(!fillDrag) return;
  const el=document.elementFromPoint(e.clientX,e.clientY);
  const cell=el&&el.closest&&el.closest('td.cell');
  if(cell&&gridEl.contains(cell)){ fillDrag.to=+cell.dataset.r; sel.r1=fillDrag.base.r1; sel.r2=Math.max(fillDrag.base.r2,fillDrag.to); paintSel(); }
});
document.addEventListener('mouseup',e=>{
  if(!fillDrag) return;
  const b=fillDrag.base, to=fillDrag.to; fillDrag=null;
  if(to<=b.r2){ paintSel(); return; }
  /* 按住 Ctrl（或 Alt）拖 = 原样复制，不递增数字；不按 = 智能递增 */
  doFill(b,to,e.ctrlKey||e.metaKey||e.altKey);
});
function doFill(b,toRow,copyOnly){
  if(!anyData(b)) return;
  pushUndo();
  const n=b.r2-b.r1+1;
  for(let c=b.c1;c<=b.c2;c++){
    if(!isEditable(c)) continue;
    const src=[]; for(let r=b.r1;r<=b.r2;r++) src.push(getVal(r,c));
    const m=src.map(v=>/^(.*?)(\d+)(\D*)$/.exec(v));
    const series=!copyOnly && m.every(x=>x) &&
                 (n===1 || m.every((x,i)=>i===0||(x[1]===m[0][1]&&x[3]===m[0][3])));
    let step=1;
    if(series&&n>1) step=(+m[n-1][2]-+m[0][2])/(n-1)||1;
    for(let r=b.r2+1;r<=toRow;r++){
      if(series){
        const last=m[n-1], val=Math.round(+last[2]+step*(r-b.r2));
        setVal(r,c,last[1]+String(val).padStart(last[2].length,'0')+last[3]);
      } else setVal(r,c,src[(r-b.r1)%n]);
    }
  }
  sel={r1:b.r1,c1:b.c1,r2:toRow,c2:b.c2};
  commit();
  toast(copyOnly?'已原样复制填充（按住 Ctrl 拖拽 = 不递增）'
                :'已智能填充（结尾数字自动递增；按住 Ctrl 拖拽则原样复制）');
}
function fillDown(){
  const s=selRange();
  if(s.r2<=s.r1||!anyData(s)) return;
  pushUndo();
  for(let c=s.c1;c<=s.c2;c++){ if(!isEditable(c)) continue;
    const v=getVal(s.r1,c); for(let r=s.r1+1;r<=s.r2;r++) setVal(r,c,v); }
  commit();
}

/* =======================================================================
   列宽 / 列拖动 / 行拖动
   ======================================================================= */
function startResize(e,ci){
  e.preventDefault();
  /* 拖的是显示宽；换算回期望宽后整表重新等比适配，所以表始终贴合窗口 */
  const x0=e.clientX, d0=vcols[ci].dw||vcols[ci].w;
  const mv=ev=>{ const dw=Math.max(60,d0+ev.clientX-x0);
                 vcols[ci].w=Math.max(60,Math.round(dw/(fitF||1)));
                 applyColWidths(); placeFill(); };
  const up=()=>{ rememberW(ci); document.removeEventListener('mousemove',mv); document.removeEventListener('mouseup',up); scheduleSave(); };
  document.addEventListener('mousemove',mv); document.addEventListener('mouseup',up);
}
function startHeaderDrag(e,ci){
  sel={r1:0,c1:ci,r2:rows.length-1,c2:ci}; active={r:0,c:ci}; paintSel();
  if(!isData(ci)) return;                       // 预览列 / 链接列不可移动
  const x0=e.clientX; let moved=false, target=ci;
  const line=$('vline');
  const mv=ev=>{
    if(!moved && Math.abs(ev.clientX-x0)<6) return;
    moved=true;
    const el=document.elementFromPoint(ev.clientX,ev.clientY);
    const h=el&&el.closest&&el.closest('th.hdr');
    if(h){
      const j=+h.dataset.c, rc=h.getBoundingClientRect(), w=wrap.getBoundingClientRect();
      target=clamp((ev.clientX>rc.left+rc.width/2)?j+1:j,0,cols.length);
      const ref=gridEl.querySelector(`th.hdr[data-c="${clamp(target,0,vcols.length-1)}"]`);
      const rr=ref.getBoundingClientRect();
      line.style.left=((target>=vcols.length?rr.right:rr.left)-w.left+wrap.scrollLeft)+'px';
      line.style.height=gridEl.offsetHeight+'px'; line.style.display='block';
    }
  };
  const up=()=>{
    document.removeEventListener('mousemove',mv); document.removeEventListener('mouseup',up);
    line.style.display='none';
    if(moved && target!==ci && target!==ci+1){
      pushUndo();
      const [c]=cols.splice(ci,1);
      cols.splice(target>ci?target-1:target,0,c);
      commit();
    }
  };
  document.addEventListener('mousemove',mv); document.addEventListener('mouseup',up);
}
function startRowDrag(e,ri){
  if(e.shiftKey){ sel.r2=ri; sel.c1=0; sel.c2=vcols.length-1; paintSel(); return; }
  sel={r1:ri,c1:0,r2:ri,c2:vcols.length-1}; active={r:ri,c:0}; paintSel();
  const y0=e.clientY; let moved=false, target=ri;
  const line=$('hline');
  const mv=ev=>{
    if(!moved && Math.abs(ev.clientY-y0)<6) return;
    moved=true;
    const el=document.elementFromPoint(ev.clientX,ev.clientY);
    const tr=el&&el.closest&&el.closest('tr[data-r]');
    if(tr){
      const j=+tr.dataset.r, rc=tr.getBoundingClientRect(), w=wrap.getBoundingClientRect();
      target=(ev.clientY>rc.top+rc.height/2)?j+1:j;
      line.style.top=((target>j?rc.bottom:rc.top)-w.top+wrap.scrollTop)+'px';
      line.style.width=gridEl.offsetWidth+'px'; line.style.display='block';
    }
  };
  const up=()=>{
    document.removeEventListener('mousemove',mv); document.removeEventListener('mouseup',up);
    line.style.display='none';
    if(moved && target!==ri && target!==ri+1){
      pushUndo();
      const [r]=rows.splice(ri,1);
      rows.splice(target>ri?target-1:target,0,r);
      commit();
    }
  };
  document.addEventListener('mousemove',mv); document.addEventListener('mouseup',up);
}

/* =======================================================================
   行列操作
   ======================================================================= */
function insertCol(at,fill){
  pushUndo();
  at=clamp(at,0,cols.length);
  const c={id:uid('c'),name:nextColName(cols.length),w:160,type:'data'};
  cols.splice(at,0,c);
  if(fill==='stem') rows.forEach(r=>{ const src=DOC==='dl'?r.rname:(DOC==='mk'?'':r.file); if(src) r.cells[c.id]=stemOf(src, DOC==='dl'?'file':r.kind); });
  sel={r1:0,c1:at,r2:rows.length-1,c2:at}; active={r:0,c:at};
  commit();
}
function deleteCols(){
  const s=selRange(), del=[];
  for(let c=s.c1;c<=s.c2;c++) if(isData(c)) del.push(vcols[c].id);
  if(!del.length){ toast('预览列不能删除（可以在「视图」里关掉它们）。'); return; }
  if(del.length>=cols.length){ toast('至少要保留一个编辑列。'); return; }
  pushUndo();
  cols=cols.filter(c=>!del.includes(c.id));
  rows.forEach(r=>del.forEach(id=>delete r.cells[id]));
  rebuildV();
  active.c=clamp(active.c,0,vcols.length-1); sel.c1=sel.c2=active.c;
  commit();
}
function renameCol(ci){
  if(!isData(ci)){ toast('预览列的名字不能修改。'); return; }
  const c=vcols[ci];
  dialog('重命名列',`<div class="row"><label>列名</label><input type="text" id="d_name" value="${escA(c.name)}"></div>`,
    ()=>{ pushUndo(); c.name=$('d_name').value||c.name; commit(); });
}
function insertRow(at){ pushUndo(); rows.splice(clamp(at,0,rows.length),0,{id:uid('r'),file:null,cells:{},checked:true,link:'',wrap:{}}); commit(); }
function deleteRows(){
  const s=selRange(); pushUndo();
  rows.splice(s.r1,s.r2-s.r1+1);
  if(!rows.length) rows.push({id:uid('r'),file:null,cells:{},checked:true,link:'',wrap:{}});
  active.r=clamp(s.r1,0,rows.length-1); sel.r1=sel.r2=active.r;
  commit();
}
function removeBlank(){
  pushUndo();
  rows=rows.filter(r=>r.file||Object.values(r.cells).some(v=>v));
  if(!rows.length) rows.push({id:uid('r'),file:null,cells:{},checked:true,link:'',wrap:{}});
  commit();
}
function sortByCol(ci,asc){
  pushUndo();
  const key=v=>String(v).split(/(\d+)/).map(t=>/^\d+$/.test(t)?t.padStart(12,'0'):t.toLowerCase()).join('');
  rows=rows.map((r,i)=>({r,i,k:key(getVal(i,ci))}))
           .sort((a,b)=>a.k<b.k?(asc?-1:1):a.k>b.k?(asc?1:-1):a.i-b.i)
           .map(x=>x.r);
  commit();
}

/* =======================================================================
   右键菜单
   ======================================================================= */
function hideCtx(){ $('ctx').style.display='none'; }
document.addEventListener('mousedown',e=>{ if(!e.target.closest('#ctx')) hideCtx(); },true);
function showCtx(x,y,items){
  if(DOC==='mk'){
    items=items.filter(it=>it==='-'||!/文件路径|资源管理器|打开此文件/.test(it[1]));
    items=items.concat(['-',
      ['mkx1','填入序号…','',()=>tool_number()],
      ['mkx2','创建勾选的文件夹','Ctrl+Enter',()=>mkStart()],
      ['mkx3','打开「建在」的文件夹','',()=>mkOpenDest()]]);
    items=items.filter((it,i,a)=>!(it==='-'&&(i===0||a[i-1]==='-'||i===a.length-1)));
  }
  if(DOC==='dl'){
    items=items.filter(it=>it==='-'||!/文件路径|资源管理器|打开此文件/.test(it[1]));
    items=items.concat(['-',
      ['dlx1','重新解析选中行的链接','',()=>dlResolveSelected()],
      ['dlx2','在浏览器中打开这一行的链接','Ctrl+点击',()=>openLinkOfRow(active.r)],
      ['dlx3','下载勾选的行','Ctrl+Enter',()=>dlStart()]]);
    items=items.filter((it,i,a)=>!(it==='-'&&(i===0||a[i-1]==='-'||i===a.length-1)));
  }
  const c=$('ctx');
  c.innerHTML=items.map(it=>it==='-'?'<div class="msep"></div>'
    :`<div class="mi" data-a="${it[0]}">${esc(it[1])}${it[2]?`<span class="key">${esc(it[2])}</span>`:''}</div>`).join('');
  c.style.display='block';
  c.style.left=Math.min(x,innerWidth-c.offsetWidth-6)+'px';
  c.style.top=Math.min(y,innerHeight-c.offsetHeight-6)+'px';
  c.querySelectorAll('.mi').forEach(el=>el.onclick=()=>{ hideCtx(); items.find(i=>i[0]===el.dataset.a)[3](); });
}
bindGrid('contextmenu',e=>{
  e.preventDefault();
  const hdr=e.target.closest('th.hdr');
  if(hdr){
    const ci=+hdr.dataset.c;
    sel={r1:0,c1:ci,r2:rows.length-1,c2:ci}; active={r:0,c:ci}; paintSel();
    const items=[];
    if(isData(ci)){
      items.push(['a','在左侧插入 1 列','',()=>insertCol(ci)],
                 ['b','在右侧插入 1 列','',()=>insertCol(ci+1)],
                 ['c','删除此列','',deleteCols],
                 '-',
                 ['d','重命名此列','双击',()=>renameCol(ci)],
                 ['h','设置此列字符上限…',cols[ci]&&cols[ci].max?('当前 ≤'+cols[ci].max):'未设',()=>tool_colmax(ci)],
                 ['i',(vcols[ci]&&vcols[ci].master?'清除':'设置')+'此列统一值…',
                      vcols[ci]&&vcols[ci].master?('「'+vcols[ci].master+'」'):'',()=>tool_master(ci)],
                 ['g','清空此列内容','',()=>{ sel={r1:0,c1:ci,r2:rows.length-1,c2:ci}; clearSel(); }],
                 '-');
    }
    items.push(['e','按此列升序排列 ↑','',()=>sortByCol(ci,true)],
               ['f','按此列降序排列 ↓','',()=>sortByCol(ci,false)]);
    showCtx(e.clientX,e.clientY,items); return;
  }
  const rn=e.target.closest('th.rownum');
  if(rn){
    const ri=+rn.dataset.r, s=selRange();
    if(ri<s.r1||ri>s.r2){ sel={r1:ri,c1:0,r2:ri,c2:vcols.length-1}; active={r:ri,c:0}; paintSel(); }
    showCtx(e.clientX,e.clientY,[
      ['a','在上方插入 1 行','',()=>insertRow(ri)],
      ['b','在下方插入 1 行','',()=>insertRow(ri+1)],
      ['c','删除选中行','Delete',deleteRows],
      '-',
      ['k','勾选选中行','',()=>checkSel(true)],
      ['u','取消勾选选中行','',()=>checkSel(false)],
      ['n','复制选中文件名','Ctrl+Shift+C',()=>copyFileNames()],
      ['p','复制选中文件路径','Ctrl+Shift+P',()=>copyFilePaths()],
      ['o','在资源管理器中显示','',()=>revealSel()],
      '-',
      ['d','删除所有空行','',removeBlank],
    ]); return;
  }
  const td=e.target.closest('td.cell');
  if(td){
    const r=+td.dataset.r,c=+td.dataset.c;
    let s=selRange();
    if(r<s.r1||r>s.r2||c<s.c1||c>s.c2){ active={r,c}; sel={r1:r,c1:c,r2:r,c2:c}; paintSel(); s=selRange(); }
    showCtx(e.clientX,e.clientY,[
      ['x','剪切','Ctrl+X',cutSel],
      ['c','复制','Ctrl+C',copySel],
      ['e','清除内容','Delete',clearSel],
      ['d','向下填充','Ctrl+D',fillDown],
      '-',
      ['n','复制选中文件名','Ctrl+Shift+C',()=>copyFileNames()],
      ['p','复制选中文件路径','Ctrl+Shift+P',()=>copyFilePaths()],
      ['w','选中单元格换行','',()=>setWrapRange(true)],
      ['t','选中单元格截断','',()=>setWrapRange(false)],
      ['o','在资源管理器中显示','',()=>revealSel()],
      ['f','打开此文件','',()=>openSelFile()],
      '-',
      ['ra','在上方插入行','',()=>insertRow(s.r1)],
      ['rb','在下方插入行','',()=>insertRow(s.r2+1)],
      ['rd','删除选中行','',deleteRows],
      '-',
      ['ca','在左侧插入列','',()=>insertCol(s.c1)],
      ['cb','在右侧插入列','',()=>insertCol(dataEnd(s.c2)+1)],
      ['cd','删除选中列','',deleteCols],
    ]);
  }
});

/* =======================================================================
   弹窗
   ======================================================================= */
function dialog(title,bodyHtml,onOk,okText){
  $('dlg').classList.remove('gdwrap');
  $('dlg').innerHTML=`<h3>${esc(title)}</h3><div class="body">${bodyHtml}</div>
    <div class="foot"><button onclick="closeDlg()">取消</button>
    ${onOk?`<button class="primary" id="d_ok">${esc(okText||'确定')}</button>`:''}</div>`;
  $('mask').style.display='flex';
  if(onOk) $('d_ok').onclick=()=>{ if(onOk()!==false) closeDlg(); };
  const f=$('dlg').querySelector('input,select'); if(f){ f.focus(); if(f.select)f.select(); }
  $('dlg').querySelectorAll('input').forEach(i=>i.addEventListener('keydown',e=>{
    if(e.key==='Enter'&&onOk){ e.preventDefault(); $('d_ok').click(); }}));
}
function closeDlg(){ $('mask').style.display='none'; $('dlg').innerHTML=''; $('dlg').classList.remove('gdwrap'); wrap.focus(); }
$('mask').addEventListener('mousedown',e=>{ if(e.target===$('mask')) closeDlg(); });
document.addEventListener('keydown',e=>{ if(e.key==='Escape'&&$('mask').style.display==='flex') closeDlg(); });
function info(title,html){ dialog(title,html,null); }
/* 兜底：任何没接住的脚本错误都弹出来，避免出现「点了按钮却毫无反应」 */
let errShown=0;
function reportErr(where,err){
  if(errShown++>3) return;
  const msg=(err&&(err.stack||err.message))||String(err);
  try{
    info('出错了 · '+where,
      '<p>这一步没能完成。可以先按 Ctrl+S 保存，再把下面这段信息发给开发者。</p>'+
      '<pre class="log">'+esc(msg)+'</pre>');
  }catch(_){ try{ toast('出错了：'+msg); }catch(__){} }
}
window.addEventListener('error',e=>reportErr('脚本错误', e.error||e.message));
window.addEventListener('unhandledrejection',e=>reportErr('操作未完成', e.reason));
let toastT=null;
function toast(msg){ $('stSaved').textContent=msg; clearTimeout(toastT); toastT=setTimeout(()=>markSaved(),4500); }

/* =======================================================================
   命名规则（都作用在「当前选中的编辑列 / 选区」上）
   ======================================================================= */
function curDataCol(){
  if(isData(active.c)) return active.c;
  const s=selRange();
  for(let c=s.c1;c<=s.c2;c++) if(isData(c)) return c;
  return 0;
}
function colOptions(selIdx){
  return cols.map((c,i)=>`<option value="${i}"${i===selIdx?' selected':''}>${esc(c.name)}</option>`).join('');
}
const selChk='<div class="chk"><input type="checkbox" id="d_only"><label for="d_only">仅应用于当前选中的行</label></div>';
function targetRows(only){
  const base=only ? (()=>{ const s=selRange(), out=[]; for(let r=s.r1;r<=s.r2;r++) out.push(r); return out; })()
                  : rows.map((r,i)=>i);
  return base.filter(i=>rowOn(rows[i]));
}

function tool_number(){
  dialog('填入序号',`
    <div class="row"><label>写到哪一列</label><select id="d_col">${colOptions(curDataCol())}<option value="new">＋ 新建一列</option></select></div>
    <div class="row"><label>前缀文字</label><input type="text" id="d_pre" placeholder="例如 v，留空则只有数字"></div>
    <div class="row"><label>起始数字</label><input type="number" id="d_start" value="1"></div>
    <div class="row"><label>位数补零</label><input type="number" id="d_pad" value="2" min="1"></div>
    <div class="hint">序号按当前表格顺序生成，会覆盖该列原有内容。未勾选的行不会填、也不占号。空行跳过但仍占一个号。</div>`+selChk,
  ()=>{
    const pick=$('d_col').value, pre=$('d_pre').value;
    const st=+$('d_start').value||1, pad=+$('d_pad').value||1, only=$('d_only').checked;
    pushUndo();
    let ci;
    if(pick==='new'){ ci=cols.length; cols.push({id:uid('c'),name:'序号',w:120,type:'data'}); rebuildV(); }
    else ci=+pick;
    const id=cols[ci].id;
    let n=st;
    targetRows(only).forEach(i=>{
      const r=rows[i]; if(!r.file) return;
      r.cells[id]=pre+String(n++).padStart(pad,'0');
    });
    commit();
  });
}
function tool_replace(){
  dialog('查找和替换',`
    <div class="row"><label>查找</label><input type="text" id="d_find"></div>
    <div class="row"><label>替换为</label><input type="text" id="d_rep"></div>
    <div class="chk"><input type="checkbox" id="d_re"><label for="d_re">使用正则表达式</label></div>
    <div class="row"><label>作用范围</label><select id="d_scope">
      <option value="sel">当前选中的单元格</option>
      <option value="col">某一整列</option>
      <option value="all">全部编辑列</option></select></div>
    <div class="row"><label>　哪一列</label><select id="d_col">${colOptions(curDataCol())}</select></div>`,
  ()=>{
    const f=$('d_find').value; if(!f) return false;
    const rep=$('d_rep').value, scope=$('d_scope').value;
    let re; try{ re=$('d_re').checked ? new RegExp(f,'g')
                                      : new RegExp(f.replace(/[.*+?^${}()|[\]\\]/g,'\\$&'),'g'); }
    catch(err){ toast('正则表达式有误：'+err.message); return false; }
    pushUndo();
    const apply=id=>rows.forEach(r=>{ if(r.cells[id]) r.cells[id]=r.cells[id].replace(re,rep); });
    if(scope==='sel'){
      const s=selRange();
      for(let r=s.r1;r<=s.r2;r++) for(let c=s.c1;c<=s.c2;c++)
        if(isData(c)) setVal(r,c,getVal(r,c).replace(re,rep));
    } else if(scope==='col') apply(cols[+$('d_col').value].id);
    else cols.forEach(c=>apply(c.id));
    commit();
  });
}
/* =======================================================================
   查找 / 替换面板（Ctrl+F）
   —— 支持用列字母（A、A,C、B-D）和行号（3、1-20、1-5,9）圈定范围
   ======================================================================= */
let hits=[], hitIdx=-1;

/* "A,C-E" -> [0,2,3,4]；留空返回 null 表示全部 */
function parseColSpec(s){
  s=String(s||'').trim(); if(!s) return null;
  const out=new Set();
  for(const part of s.split(/[,，\s]+/)){
    if(!part) continue;
    const m=/^([A-Za-z]+)\s*[-~—]\s*([A-Za-z]+)$/.exec(part);
    if(m){
      let a=letterToIdx(m[1]), b=letterToIdx(m[2]);
      if(a<0||b<0) return 'bad';
      if(a>b) [a,b]=[b,a];
      for(let i=a;i<=b;i++) out.add(i);
    } else {
      const i=letterToIdx(part);
      if(i<0) return 'bad';
      out.add(i);
    }
  }
  return [...out].filter(i=>i>=0&&i<cols.length).sort((a,b)=>a-b);
}
/* "1-20,25" -> [0..19,24]（界面上是 1 起，内部 0 起）；留空返回 null */
function parseRowSpec(s){
  s=String(s||'').trim(); if(!s) return null;
  const out=new Set();
  for(const part of s.split(/[,，\s]+/)){
    if(!part) continue;
    const m=/^(\d+)\s*[-~—]\s*(\d+)$/.exec(part);
    if(m){
      let a=+m[1], b=+m[2]; if(a>b) [a,b]=[b,a];
      for(let i=a;i<=b;i++) out.add(i-1);
    } else if(/^\d+$/.test(part)) out.add(+part-1);
    else return 'bad';
  }
  return [...out].filter(i=>i>=0&&i<rows.length).sort((a,b)=>a-b);
}

function findScope(){
  if($('f_sel').checked){
    const s=selRange(), rs=[], cs=[];
    for(let r=s.r1;r<=s.r2;r++) rs.push(r);
    for(let c=s.c1;c<=s.c2;c++) if(isData(c)) cs.push(c);
    return {rows:rs, cols:cs, err:cs.length?'':'选区里没有可编辑的列'};
  }
  const cs=parseColSpec($('f_cols').value), rs=parseRowSpec($('f_rows').value);
  if(cs==='bad') return {err:'「限定列」写法不对，应该是列字母，例如 A 或 A,C 或 B-D'};
  if(rs==='bad') return {err:'「限定行」写法不对，应该是行号，例如 3 或 1-20 或 1-5,9'};
  return {
    rows: rs || rows.map((_,i)=>i),
    cols: cs || cols.map((_,i)=>i),
    err:''
  };
}
function findRegex(){
  const f=$('f_find').value; if(!f) return null;
  const flags='g'+($('f_case').checked?'':'i');
  try{
    if($('f_re').checked) return new RegExp(f,flags);
    const lit=f.replace(/[.*+?^${}()|[\]\\]/g,'\\$&');
    return new RegExp($('f_whole').checked?('^'+lit+'$'):lit,flags);
  }catch(err){ toast('正则表达式有误：'+err.message); return null; }
}
function runFind(keepIdx){
  const sc=findScope();
  hits=[];
  if(sc.err){ $('f_cnt').textContent=''; paintHits(); toast(sc.err); return; }
  const re=findRegex();
  if(!re){ hitIdx=-1; $('f_cnt').textContent=''; paintHits(); return; }
  sc.rows.forEach(r=>sc.cols.forEach(c=>{
    if(!isData(c)) return;
    const v=rows[r].cells[vcols[c].id]||'';
    re.lastIndex=0;
    if(v && re.test(v)) hits.push({r,c});
  }));
  if(!keepIdx||hitIdx>=hits.length) hitIdx=hits.length?0:-1;
  $('f_cnt').textContent=hits.length?`${hitIdx+1} / ${hits.length}`:'无匹配';
  paintHits();
}
function paintHits(){
  gridEl.querySelectorAll('td.hit,td.hitcur').forEach(e=>e.classList.remove('hit','hitcur'));
  if($('findbar').style.display!=='block') return;
  hits.forEach((h,i)=>{ const td=tdAt(h.r,h.c); if(td) td.classList.add(i===hitIdx?'hitcur':'hit'); });
}
function findStep(d){
  if(!hits.length){ runFind(); if(!hits.length) return; }
  else hitIdx=(hitIdx+d+hits.length)%hits.length;
  const h=hits[hitIdx];
  active={r:h.r,c:h.c}; sel={r1:h.r,c1:h.c,r2:h.r,c2:h.c};
  paintSel(); scrollIntoView(h.r,h.c); paintHits();
  $('f_cnt').textContent=`${hitIdx+1} / ${hits.length}`;
}
function replaceCur(){
  if(hitIdx<0||!hits[hitIdx]){ runFind(); if(hitIdx<0) return; }
  const re=findRegex(); if(!re) return;
  const h=hits[hitIdx], rep=$('f_rep').value;
  pushUndo();
  setVal(h.r,h.c,getVal(h.r,h.c).replace(re,rep));
  commit();
  const keep=hitIdx; runFind(true);
  hitIdx=hits.length?Math.min(keep,hits.length-1):-1;
  paintHits();
  $('f_cnt').textContent=hits.length?`${hitIdx+1} / ${hits.length}`:'无匹配';
}
function replaceAll(){
  const sc=findScope();
  if(sc.err){ toast(sc.err); return; }
  const re=findRegex(); if(!re) return;
  const rep=$('f_rep').value;
  let n=0, mcount=0;
  pushUndo();
  sc.rows.forEach(r=>sc.cols.forEach(c=>{
    if(!isData(c)) return;
    const v=getVal(r,c); if(!v) return;
    re.lastIndex=0; if(!re.test(v)) return;
    re.lastIndex=0; setVal(r,c,v.replace(re,rep)); n++;
  }));
  if($('f_master').checked){
    sc.cols.forEach(c=>{
      const col=vcols[c]; if(!col||col.type!=='data'||!col.master) return;
      re.lastIndex=0; if(!re.test(col.master)) return;
      re.lastIndex=0;
      const nv=col.master.replace(re,rep);
      if(nv==='') delete col.master; else col.master=nv;
      mcount++;
    });
  }
  commit(); runFind();
  toast(n||mcount ? `已替换 ${n} 个单元格${mcount?`、${mcount} 个统一单元格`:''}` : '没有找到匹配内容');
}
function openFind(){
  $('findbar').style.display='block';
  const f=$('f_find'); f.focus(); f.select();
  runFind();
}
function closeFind(){
  $('findbar').style.display='none';
  hits=[]; hitIdx=-1; paintHits(); wrap.focus();
}
['f_find','f_cols','f_rows'].forEach(id=>
  $(id).addEventListener('input',()=>runFind()));
['f_case','f_re','f_whole','f_sel'].forEach(id=>
  $(id).addEventListener('change',()=>runFind()));
$('f_find').addEventListener('keydown',e=>{
  if(e.key==='Enter'){ e.preventDefault(); findStep(e.shiftKey?-1:1); }
  if(e.key==='Escape'){ e.preventDefault(); closeFind(); }
});
$('findbar').addEventListener('keydown',e=>{ if(e.key==='Escape') closeFind(); });

/* =======================================================================
   长度上限设置
   ======================================================================= */
/* 下载表：这一行的后缀在预览里显示成什么 */
function extPreview(r){
  const own=r&&r.rname?extOf(r.rname,'file'):null;      // null = 网盘原名还没解析出来
  if(extMode==='none') return '';
  if(extMode==='set'){
    const e=normExt(extSet);
    if(!e) return own===null?'.原后缀':own;
    if(!extFill) return e;
    return own===null ? e : (own||e);
  }
  return own===null?'.原后缀':own;
}
/* 下载表：这一行名字留空时，最终会落成什么名字 */
function dlRemoteShown(r){
  const rn=(r&&r.rname)||'';
  if(!rn) return '网盘原名';
  if(extMode==='set'){
    const e=normExt(extSet), own=extOf(rn,'file');
    if(e && !(own && extFill)) return stemOf(rn,'file')+e;
  }
  return rn;
}

/* =======================================================================
   批量设置扩展名
   · 明确选「作用于哪张表」：改名表 / 下载表，各自独立
   · 不依赖「当前正在编辑哪张表」，用 withDoc 直接作用到选中的那张
   · 下载表里已经下载好的文件，应用后可以一键在磁盘上补后缀
   ======================================================================= */
const EXT_DOC_NAME={main:'改名表',dl:'下载表'};
/* 按当前的扩展名规则，把任意一个文件名换成新后缀（只动后缀） */
function applyExtTo(name){
  const own=extOf(name,'file'), stem=stemOf(name,'file');
  if(extMode==='none') return stem;
  if(extMode==='set'){
    const e=normExt(extSet);
    if(!e) return name;
    return (extFill&&own) ? name : stem+e;
  }
  return name;
}
/* 某张表里：勾选的文件行 */
function extTargetRows(){ return rows.filter(r=>r.file&&rowOn(r)&&!isDirRow(r)&&r.file!==DL_PENDING); }
/* 某张表里：已经下载到磁盘、按新规则名字会变的行（只有下载表有） */
function extDiskFixes(){
  if(DOC!=='dl') return [];
  return rows.filter(r=>r.file&&rowOn(r)&&r.ds&&r.ds.status==='ok'&&r.ds.saved)
             .map(r=>({r,old:r.ds.saved,neu:applyExtTo(r.ds.saved)}))
             .filter(x=>x.neu!==x.old);
}
function extCountOn(doc){ return withDoc(doc,()=>extTargetRows().length); }
function extReadState(doc){ return withDoc(doc,()=>({mode:extMode,val:extSet,fill:extFill})); }
function extDlgVals(){
  return {
    doc:(document.querySelector('input[name=d_extdoc]:checked')||{}).value||DOC,
    mode:(document.querySelector('input[name=d_extmode]:checked')||{}).value||'keep',
    val:$('d_extval')?$('d_extval').value:'.mp4',
    fill:$('d_extfill')?$('d_extfill').checked:true,
  };
}
/* 预览：在目标表上临时套用弹窗里的选项，算完原样还回去 */
function extPreviewHtml(v){
  return withDoc(v.doc,()=>{
    const old=[extMode,extSet,extFill];
    extMode=v.mode; extSet=v.val; extFill=v.fill;
    try{
      const name=EXT_DOC_NAME[v.doc];
      const list=extTargetRows();
      if(!list.length) return `<span class="ph">${name}里现在没有勾选的文件。先在${name}里勾选要改的行。</span>`;
      const chg=list.filter(extChanged);
      const noExt=list.filter(r=>!extOf(r.file,r.kind)).length;
      const show=r=> DOC==='dl'
        ? esc(r.rname||r.file)+'  →  '+esc(finalName(r))
        : esc(r.file)+'  →  '+esc(finalName(r));
      let t=`${name}勾选的 <b>${list.length}</b> 个文件里，<b>${chg.length}</b> 个的后缀会变`
          + (noExt?`（其中本来没有后缀的 ${noExt} 个）`:'')+'。';
      if(chg.length) t+=`<pre class="log" style="margin-top:8px">${chg.slice(0,4).map(show).join('\n')}${chg.length>4?'\n…':''}</pre>`;
      const fx=extDiskFixes();
      if(fx.length) t+=`<div class="extdisk">其中 <b>${fx.length}</b> 个<b>已经下载到磁盘</b>了（比如 ${esc(fx[0].old)}）。
        点「应用」之后会问你要不要把磁盘上的这些文件也一起补上后缀。</div>`;
      return t;
    } finally { [extMode,extSet,extFill]=old; }
  });
}
function extDlgRefresh(){
  const v=extDlgVals();
  const inp=$('d_extval'), fr=$('d_extfillrow');
  if(inp) inp.disabled=(v.mode!=='set');
  if(fr) fr.style.opacity=(v.mode==='set')?'1':'.4';
  const tip=$('d_exttip');
  if(tip) tip.innerHTML = v.doc==='dl'
    ? '只改后缀，<b>文件名部分一个字都不动</b>。影响<b>之后的下载</b>；已经下载好的文件，应用后会问你要不要一起补上。'
    : '只改后缀，<b>文件名部分一个字都不动</b>。应用后先看预览列，确认没问题再点「执行重命名」。';
  const box=$('d_extprev'); if(box) box.innerHTML=extPreviewHtml(v);
}
/* 切换目标表时，把单选/输入框换成那张表自己的设置 */
function extDlgLoadDoc(doc){
  const st=extReadState(doc);
  document.querySelectorAll('input[name=d_extmode]').forEach(el=>{ el.checked=(el.value===st.mode); });
  if($('d_extval')) $('d_extval').value=normExt(st.val)||'.mp4';
  if($('d_extfill')) $('d_extfill').checked=!!st.fill;
  extDlgRefresh();
}
function openExtDlg(){
  /* 能选的表：改名表永远在；下载面板开着（或用过）才给下载表 */
  const docs=['main'].concat((dlOpen||DOCS.dl||DOC==='dl')?['dl']:[]);
  const cnt={}; docs.forEach(d=>cnt[d]=extCountOn(d));
  /* 默认目标：当前在编辑的那张；如果它一个勾选都没有、而另一张有，就选另一张 */
  let target=docs.includes(DOC)?DOC:'main';
  if(!cnt[target]){ const o=docs.find(d=>d!==target&&cnt[d]>0); if(o) target=o; }
  const st=extReadState(target);

  const T=d=>`<label class="extdoc">
      <input type="radio" name="d_extdoc" value="${d}"${d===target?' checked':''}>
      <span>${d==='dl'?'⬇':'✎'} ${EXT_DOC_NAME[d]}<em>勾选 ${cnt[d]} 个文件</em></span></label>`;
  const R=(val,label,note)=>`<label class="extopt">
      <input type="radio" name="d_extmode" value="${val}"${st.mode===val?' checked':''}>
      <span><b>${label}</b><em>${note}</em></span></label>`;
  dialog('批量设置扩展名', `
    <div class="extdocs"><span class="lbl">作用于</span>${docs.map(T).join('')}</div>
    <div class="hint" id="d_exttip" style="margin-top:0"></div>
    ${R('keep','沿用原来的扩展名','默认。a022.mp4 还是 .mp4，本来没后缀的还是没有')}
    ${R('set','统一用这个后缀','别人上传时没带后缀，下载下来是光秃秃的 a022，用这个补上')}
    <div class="row" style="margin-left:26px">
      <label style="width:auto">新后缀</label>
      <input type="text" id="d_extval" value="${escA(normExt(st.val)||'.mp4')}" placeholder=".mp4">
    </div>
    <label class="extfill" id="d_extfillrow">
      <input type="checkbox" id="d_extfill"${st.fill?' checked':''}>
      <span>只补给<b>本来没有后缀</b>的文件，已经有后缀的保持原样</span>
    </label>
    ${R('none','去掉扩展名','新文件名里不带任何后缀')}
    <div id="d_extprev" class="hint" style="border-top:1px dashed var(--bd);padding-top:12px;margin-top:12px;margin-bottom:0"></div>`,
  ()=>{
    const v=extDlgVals();
    if(v.mode==='set' && !normExt(v.val)){ toast('后缀不能是空的，比如填 .mp4'); return false; }
    /* 直接作用到选中的那张表，跟「当前正在编辑哪张」无关 */
    const fixes=withDoc(v.doc,()=>{
      pushUndo();
      extMode=v.mode; extSet=normExt(v.val)||extSet; extFill=v.fill;
      keepExt=(extMode!=='none');          // 老工程文件/后端沿用这个开关
      return extDiskFixes();
    });
    if(v.doc==='dl') renderDlDoc(); else withDoc('main',()=>render());
    if(v.doc===DOC) render();
    buildMenus(); scheduleSave();
    const what=v.mode==='set'?('统一为 '+normExt(v.val)+(v.fill?'（只补没有后缀的）':'（全部替换）'))
              :v.mode==='none'?'不带扩展名':'沿用原来的扩展名';
    toast(EXT_DOC_NAME[v.doc]+'：扩展名'+what);
    if(fixes.length) setTimeout(()=>offerDiskExtFix(fixes),60);
  },'应用');
  document.querySelectorAll('input[name=d_extdoc]').forEach(el=>el.addEventListener('change',()=>extDlgLoadDoc(el.value)));
  document.querySelectorAll('input[name=d_extmode]').forEach(el=>el.addEventListener('change',extDlgRefresh));
  ['d_extval','d_extfill'].forEach(id=>{
    const el=$(id); if(el){ el.addEventListener('input',extDlgRefresh); el.addEventListener('change',extDlgRefresh); }
  });
  extDlgRefresh();
}
/* 下载表：已经下载到磁盘的文件，一键把后缀补上（只改名，绝不覆盖） */
function offerDiskExtFix(fixes){
  const lines=fixes.map(x=>x.old+'  →  '+x.neu);
  dialog('已下载的文件也补上后缀？', `
    <p>下载表的设置已经改好，<b>之后的下载</b>都会按新后缀保存。</p>
    <p>另外有 <b>${fixes.length}</b> 个文件<b>已经下载到磁盘</b>了，名字还是旧的：</p>
    <pre class="log">${esc(lines.slice(0,40).join('\n'))}${lines.length>40?'\n…':''}</pre>
    <div class="hint">位置：${esc(dlDest||'')}<br>只改后缀，不动文件内容；新名字已被别的文件占用时，整批都不改，不会覆盖任何东西。</div>`,
  ()=>{
    closeDlg();
    const plan=fixes.map(x=>[x.old,x.neu]);
    post('/api/dlrename',{dest:dlDest,plan}).then(d=>{
      if(!d||d.ok===false){ info('没有改动','<pre class="log">'+esc((d&&d.message)||'未知错误')+'</pre>'); return; }
      const done=new Map((d.renamed||[]).map(p=>[p[0],p[1]]));
      withDoc('dl',()=>{ rows.forEach(r=>{ if(r.ds&&r.ds.saved&&done.has(r.ds.saved)) r.ds.saved=done.get(r.ds.saved); }); });
      renderDlDoc(); scheduleSave();
      let html=`<p>✅ 已给 <b>${done.size}</b> 个已下载的文件补上后缀。</p>`;
      if(d.skipped&&d.skipped.length) html+=`<p>⚠ ${d.skipped.length} 个文件已经不在下载文件夹里，跳过了：</p><pre class="log">${esc(d.skipped.join('\n'))}</pre>`;
      if(d.errors&&d.errors.length) html+=`<pre class="log">${esc(d.errors.join('\n'))}</pre>`;
      info('完成',html);
    });
    return false;
  },'把这 '+fixes.length+' 个也补上');
}

function tool_maxlen(){
  const ci=curDataCol();
  const list=cols.map((c,i)=>
    `<div class="row"><label>${colLetter(i)} · ${esc(c.name)}</label>
     <input type="number" id="d_max${i}" min="0" value="${c.max||0}" placeholder="0"></div>`).join('');
  dialog('字符长度上限',`
    <div class="row"><label>文件名总长上限</label>
      <input type="number" id="d_total" min="0" value="${maxName}"></div>
    <div class="hint">拼出来的文件名（不含扩展名）最多多少个字。超了会<b>从当前最长的那一列</b>往回削，
      所以结尾的编号列（例如 -XY-v240）不会被砍掉。填 0 = 不限。
      Windows 单个文件名最多 255 个字，路径深的话建议留到 180 以内。</div>
    <div style="border-top:1px dashed #ddd;padding-top:12px;margin-top:4px"></div>
    <div class="hint" style="margin-top:0">下面给每一列单独设上限，0 = 这一列不限：</div>
    ${list}`,
  ()=>{
    pushUndo();
    maxName=Math.max(0,+$('d_total').value||0);
    cols.forEach((c,i)=>{
      const v=Math.max(0,+$('d_max'+i).value||0);
      if(v) c.max=v; else delete c.max;
    });
    commit();
    toast('长度上限已更新，表格里超限的格子会标红，改名时按上限截断');
  },'保存');
  setTimeout(()=>{ const el=$('d_max'+ci); if(el){ el.focus(); el.select(); } },30);
}

/* 单列上限 / 单列统一值（右键菜单用） */
function tool_colmax(ci){
  const c=cols[ci]; if(!c) return;
  dialog(`${colLetter(ci)} 列的字符上限`,`
    <div class="row"><label>最多几个字</label><input type="number" id="d_m" min="0" value="${c.max||0}"></div>
    <div class="hint">0 = 不限。超出的部分在表格里会标红显示，执行重命名时才真正截断，原文不会丢。</div>`,
  ()=>{ pushUndo(); const v=Math.max(0,+$('d_m').value||0);
        if(v) c.max=v; else delete c.max; buildMenus(); commit(); });
}
function tool_master(ci){
  const c=cols[ci]; if(!c) return;
  dialog(`${colLetter(ci)} 列的统一单元格`,`
    <div class="row"><label>统一内容</label><input type="text" id="d_mv" value="${escA(c.master||'')}"></div>
    <div class="hint">填了内容，这一列每一行都用它；留空则清除，各行恢复自己原来的内容。
      各单元格原有的字不会被破坏，只是被盖住。</div>`,
  ()=>{ pushUndo(); const v=$('d_mv').value;
        if(v) c.master=v; else delete c.master; commit(); });
}

function tool_affix(){
  dialog('给某一列加前缀 / 后缀',`
    <div class="row"><label>哪一列</label><select id="d_col">${colOptions(curDataCol())}</select></div>
    <div class="row"><label>前缀</label><input type="text" id="d_pre"></div>
    <div class="row"><label>后缀</label><input type="text" id="d_suf"></div>
    <div class="hint">只加在该列现有文字的两侧。如果只是想在文件名前后加固定内容，
    更推荐直接插入一个新列然后整列填同一个值。</div>`+selChk,
  ()=>{
    const id=cols[+$('d_col').value].id, pre=$('d_pre').value, suf=$('d_suf').value;
    pushUndo();
    targetRows($('d_only').checked).forEach(i=>{
      const r=rows[i]; if(!r.file) return;
      r.cells[id]=pre+(r.cells[id]||'')+suf;
    });
    commit();
  });
}
function tool_fillText(){
  dialog('整列填充固定文字',`
    <div class="row"><label>哪一列</label><select id="d_col">${colOptions(curDataCol())}</select></div>
    <div class="row"><label>填入</label><input type="text" id="d_txt" placeholder="例如：美-5"></div>
    <div class="hint">配合「填入序号」就能做出「统一名称 + 序号」的效果。</div>`+selChk,
  ()=>{
    const id=cols[+$('d_col').value].id, v=$('d_txt').value;
    pushUndo();
    targetRows($('d_only').checked).forEach(i=>{ if(rows[i].file) rows[i].cells[id]=v; });
    commit();
  });
}
function tool_case(){
  dialog('大小写与空白',`
    <div class="row"><label>哪一列</label><select id="d_col">${colOptions(curDataCol())}</select></div>
    <div class="row"><label>操作</label><select id="d_op">
      <option value="lower">全部小写</option><option value="upper">全部大写</option>
      <option value="title">每个单词首字母大写</option>
      <option value="trim">去掉首尾空格</option>
      <option value="squeeze">多个空格合并为一个</option>
      <option value="dash">空格换成短横线 -</option>
      <option value="undash">短横线/下划线换成空格</option></select></div>`+selChk,
  ()=>{
    const id=cols[+$('d_col').value].id, op=$('d_op').value;
    const f={lower:x=>x.toLowerCase(),upper:x=>x.toUpperCase(),
      title:x=>x.replace(/\b\w/g,m=>m.toUpperCase()),trim:x=>x.trim(),
      squeeze:x=>x.replace(/\s+/g,' '),dash:x=>x.replace(/\s+/g,'-'),
      undash:x=>x.replace(/[-_]+/g,' ')}[op];
    pushUndo();
    targetRows($('d_only').checked).forEach(i=>{
      const r=rows[i]; if(r.cells[id]) r.cells[id]=f(r.cells[id]); });
    commit();
  });
}
function tool_joinSep(){
  dialog('列与列之间的连接符',`
    <div class="row"><label>连接符</label><input type="text" id="d_js" value="${escA(joinSep)}" placeholder="留空 = 直接拼接"></div>
    <div class="hint">默认留空，也就是各列内容原样首尾相接。
    填 <b>-</b> 就会变成 <b>美-5-v01-rename01</b>。空白的列会被自动跳过，不会出现连着两个连接符。</div>`,
  ()=>{ pushUndo(); joinSep=$('d_js').value; commit(); });
}

/* =======================================================================
   与后端交互
   ======================================================================= */
const API_TOKEN="___RCT_SESSION_TOKEN___";
async function post(url,body){
  const r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json','X-Rct-Token':API_TOKEN},body:JSON.stringify(body||{})});
  return r.json();
}
function gridState(){
  /* cols 里的 max（列字符上限）和 master（统一单元格）会随对象一起存进工程文件。
     改名表和下载表各存各的，和当前正在编辑哪张表无关。 */
  const m=docOf('main');
  return {v:3,cols:m.cols,joinSep:m.joinSep,showPrev:m.showPrev,showLink:m.showLink,showStat:m.showStat,
          keepExt:m.keepExt,extMode:m.extMode,extSet:m.extSet,extFill:m.extFill,
          maxName:m.maxName,specialW:m.specialW,includeDirs,videosOnly,
          dl:dlState(),
          mk:mkStateSave(),
          rows:m.rows.map(r=>({id:r.id,file:r.file,kind:r.kind||(r.file?'file':null),cells:r.cells,
                             checked:r.checked!==false,link:r.link||'',wrap:r.wrap||{},
                             up:r.up||{status:'none'}}))};
}
function scheduleSave(){
  clearTimeout(saveTimer);
  $('stSaved').textContent='正在保存…';
  saveTimer=setTimeout(async()=>{
    markSaved((await post('/api/savegrid',{grid:gridState(),prefs:chromeState()})).updated);
  },700);
}
let lastSaved='';
function markSaved(t){ if(t) lastSaved=t; $('stSaved').textContent=lastSaved?('工程已自动保存 '+lastSaved):''; }

/* 旧版工程文件（原文件名/新文件名两列）迁移到新模型 */
function migrate(g){
  if(!g||!g.cols||!g.cols.length) return null;
  if(!g.cols.some(c=>c.type==='orig'||c.type==='new')) return g;
  const nc={id:'c_migrated',name:'文件名',w:340,type:'data'};
  const keep=g.cols.filter(c=>c.type==='data').map(c=>Object.assign({},c,{type:'data'}));
  return {
    v:2, joinSep:'', showPrev:true, keepExt:true,
    cols:[nc].concat(keep),
    rows:(g.rows||[]).map(r=>{
      const cells=Object.assign({},r.cells||{});
      const v=(cells['__new__']||'').trim();
      delete cells['__orig__']; delete cells['__new__'];
      cells[nc.id]= v || (r.file?stemOf(r.file):'');
      return {id:r.id,file:r.file,cells,checked:r.checked!==false,link:r.link||''};
    })
  };
}
/* 合并后端文件/文件夹列表：保留已有行顺序与内容，补上新条目，消失的变空行 */
function mergeFiles(list, entries, dirNames){
  const kinds={};
  if(entries && entries.length){
    entries.forEach(e=>{ kinds[e.name]=e.kind||'file'; });
    list=entries.map(e=>e.name);
  } else {
    (list||[]).forEach(n=>{ kinds[n]='file'; });
  }
  files=list.slice();
  const have=new Set(rows.filter(r=>r.file).map(r=>r.file));
  const set=new Set(list);
  const drop=new Set();
  rows.forEach(r=>{
    if(r.file&&!set.has(r.file)){
      /* 文件夹行、或被「只显示视频」滤掉的文件：整行删掉，避免留下空行。 */
      if(r.kind==='dir' || videosOnly) drop.add(r.id);
      else { r.file=null; r.kind=null; }
    } else if(r.file && kinds[r.file]) r.kind=kinds[r.file];
  });
  if(drop.size) rows=rows.filter(r=>!drop.has(r.id));
  list.forEach(f=>{ if(!have.has(f)) rows.push(newRow(f, kinds[f]||'file')); });
  if(!includeDirs) dropFolderRows(dirNames);
  if(!rows.length) rows.push(newRow(null));
}
function dropFolderRows(dirNames){
  /* 关掉「包含文件夹」后：删掉文件夹行，以及上次残留的、只装着文件夹名的空行。 */
  const dirs=new Set(dirNames||[]);
  rows=rows.filter(r=>{
    if(r.kind==='dir') return false;
    if(r.file && dirs.has(r.file)) return false;
    if(!r.file){
      const vals=Object.values(r.cells||{}).filter(v=>v);
      if(!r.link && vals.length<=1 && vals[0] && dirs.has(vals[0])) return false;
    }
    return true;
  });
  if(!rows.length) rows.push(newRow(null));
}
function syncDirToggle(){
  const el=$('incDirs'), lab=$('incDirsLab');
  if(el) el.checked=!!includeDirs;
  if(lab) lab.classList.toggle('on', !!includeDirs);
}
function syncVidToggle(){
  const el=$('vidOnly'), lab=$('vidOnlyLab');
  if(el) el.checked=!!videosOnly;
  if(lab) lab.classList.toggle('on', !!videosOnly);
}
function chromeState(){
  const m=docOf('main');
  return {showLink:m.showLink,showPrev:m.showPrev,showStat:m.showStat,includeDirs,videosOnly,
          specialW:m.specialW,theme,dlOpen,mkOpen,dlH,dlWorkers,
          dataColW:(m.cols[0]&&m.cols[0].w)||160};
}
function applyPrefs(p){
  if(!p) return;
  if(p.showLink!==undefined) showLink=!!p.showLink;
  if(p.showPrev!==undefined) showPrev=!!p.showPrev;
  if(p.showStat!==undefined) showStat=!!p.showStat;
  if(p.includeDirs!==undefined) includeDirs=!!p.includeDirs;
  if(p.videosOnly!==undefined) videosOnly=!!p.videosOnly;
  if(p.theme==='dark'||p.theme==='light') applyTheme(p.theme);
  if(p.dlH) { dlH=+p.dlH||0; if(dlH>0) $('dlpane').style.height=dlH+'px'; }
  if(p.dlWorkers){ dlWorkers=+p.dlWorkers||3; const w=$('dlWorkers'); if(w) w.value=String(dlWorkers); }
  if(p.dlOpen!==undefined && !!p.dlOpen!==dlOpen) toggleDownload(!!p.dlOpen);
  if(p.mkOpen && !dlOpen && !mkOpen) toggleMk(true);
  if(p.specialW) specialW=Object.assign({porig:180,pnew:180,link:180,stat:150}, specialW||{}, p.specialW);
}

function applyInit(d){
  DIR=d.dir; files=d.files.slice(); restorable=d.restorable; hasRclone=!!d.rclone; hasGdrive=!!d.gdrive; updateDrivePill();
  if(!dlDest||!dlDestManual) dlDest=d.dir;
  if(!mkDest||!mkDestManual) mkDest=d.dir;
  if(d.includeDirs!==undefined) includeDirs=!!d.includeDirs;
  if(d.videosOnly!==undefined) videosOnly=!!d.videosOnly;
  if(d.prefs) applyPrefs(d.prefs);
  undoCount=0;
  $('dirlabel').textContent=DIR;
  document.title=APP_NAME+' — '+DIR;
  if($('dirlabel')) $('dirlabel').onclick=()=>openFolderDlg();
  syncDirToggle();
  syncVidToggle();
}
async function init(){
  const d=await post('/api/init');
  applyInit(d);
  const g=migrate(d.grid);
  if(g&&g.cols.length){
    cols=g.cols; joinSep=g.joinSep||''; applyDlState(g); applyMkState(g);
    if(g.keepExt!==undefined) keepExt=g.keepExt;
    extMode=g.extMode||(g.keepExt===false?'none':'keep');
    if(g.extSet!==undefined) extSet=g.extSet;
    if(g.extFill!==undefined) extFill=!!g.extFill;
    if(g.maxName!==undefined) maxName=g.maxName;
    rows=(g.rows||[]).map(r=>({id:r.id||uid('r'),file:r.file,kind:r.kind||(r.file?'file':null),cells:r.cells||{},
                               checked:r.checked!==false,link:r.link||'',wrap:r.wrap||{},
                               up:r.up||{status:'none'}}));
    if(g.specialW) specialW=g.specialW;
    else {
      cols.forEach(c=>{ if((c.w||0)>=260) c.w=160; });
      specialW={porig:180,pnew:180,link:180,stat:150};
    }
    if(d.prefs) applyPrefs(d.prefs);
    mergeFiles(d.files, d.entries, d.dirNames);
  } else {
    cols=[{id:uid('c'),name:'文件名',w:(d.prefs&&d.prefs.dataColW)||160,type:'data'}];
    rows=(d.entries||d.files.map(n=>({name:n,kind:'file'}))).map(e=>newRow(e.name||e, e.kind||'file'));
    if(!rows.length) rows.push({id:uid('r'),file:null,cells:{},checked:true,link:'',wrap:{}});
  }
  lastSaved=d.updated||'';
  rebuildV(); buildMenus(); render(); markSaved();
  wrap.focus(); setActive(0,0,false);
  $('dirlabel').onclick=()=>openFolderDlg();
  syncDirToggle();
  try{
    const gs=await post('/api/drivestatus',{});
    if(gs&&gs.running) gdWatch();
  }catch(e){}
  try{
    const ds=await post('/api/downloadstatus');
    if(ds&&ds.running){ if(!dlOpen) toggleDownload(true); watchDownload(); }
    else if(ds&&ds.done&&(ds.results||[]).length) applyDlResults(ds.results);
  }catch(e){}
}
async function reloadFiles(){
  const d=await post('/api/files',{includeDirs,videosOnly});
  restorable=d.restorable; mergeFiles(d.files, d.entries, d.dirNames);
  buildMenus(); commit(); toast('列表已刷新');
}
async function toggleIncludeDirs(on){
  includeDirs=!!on;
  syncDirToggle();
  await post('/api/savegrid',{grid:gridState(),prefs:chromeState()});
  const d=await post('/api/files',{includeDirs,videosOnly});
  restorable=d.restorable;
  if(!includeDirs) rows=rows.filter(r=>r.kind!=='dir');
  mergeFiles(d.files, d.entries, d.dirNames);
  if(!includeDirs) dropFolderRows(d.dirNames);
  buildMenus(); commit();
  toast(includeDirs
    ? '已包含当前目录下的文件夹。可以给文件夹改名；上传仍然只传文件。'
    : '只显示文件。文件夹行已从表格里去掉。');
}
async function toggleVideosOnly(on){
  videosOnly=!!on;
  syncVidToggle();
  await post('/api/savegrid',{grid:gridState(),prefs:chromeState()});
  const d=await post('/api/files',{includeDirs,videosOnly});
  restorable=d.restorable;
  mergeFiles(d.files, d.entries, d.dirNames);
  buildMenus(); commit();
  toast(videosOnly?'只显示视频（封面、日志、工程文件已隐藏）':'显示当前目录下的全部文件');
}
async function undoRename(){
  const d=await post('/api/undo');
  if(d.ok===false){ toast(d.message); return; }
  undoCount=d.undo; restorable=d.restorable; afterRename(d,'撤销');
}
function sumRow(cls,k,v){ return `<div class="${cls||''}"><span class="k">${k}</span><span class="v">${v}</span></div>`; }
function listBlock(title, lines, maxN){
  if(!lines||!lines.length) return '';
  const body=lines.slice(0, maxN||80).join('\n')+(lines.length>(maxN||80)?'\n…': '');
  return `<details class="sumd"><summary>${title}（${lines.length}）</summary><pre class="log">${esc(body)}</pre></details>`;
}
function renameChecklist(){
  const {finals,issues}=computeIssues();
  const will=[], same=[], off=[], folders=[], noLink=[], canUp=[];
  let empty=0;
  rows.forEach((r,i)=>{
    if(!r.file){ empty++; return; }
    if(!rowOn(r)){ off.push(r.file); return; }
    if(isDirRow(r)) folders.push(r.file);
    else if(r.link) canUp.push(r.file);
    else noLink.push(r.file);
    if(finals[i] && finals[i]!==r.file) will.push([r.file, finals[i], isDirRow(r)]);
    else same.push(r.file);
  });
  const trimmed=rows.filter(r=>r.file&&rowOn(r)&&isRowTrimmed(r)).length;
  return {finals,issues,will,same,off,folders,noLink,canUp,empty,trimmed};
}
async function execRename(){
  const c=renameChecklist();
  const plan=c.will.map(p=>[p[0],p[1]]);
  const conflict=[...c.issues].map(i=>{
    const r=rows[i]; return (r&&r.file?r.file:'第 '+(i+1)+' 行')+'  →  '+(c.finals[i]||'');
  });
  let html='<div class="sum">';
  html+=sumRow(c.will.length?'ok':'mut','将改名',c.will.length);
  html+=sumRow(c.issues.size?'warn':'','命名冲突（标红，不能执行）',c.issues.size);
  html+=sumRow('mut','勾选但新名相同，不改',c.same.length);
  html+=sumRow('mut','未勾选，不改',c.off.length);
  html+=sumRow('','其中文件夹（会改名，不会上传）',c.folders.length);
  html+=sumRow(c.noLink.length?'warn':'','勾选文件没有网盘链接',c.noLink.length);
  html+=sumRow(c.canUp.length?'ok':'','勾选且有链接，改完可上传',c.canUp.length);
  html+='</div>';
  if(c.trimmed) html+=`<div class="hint" style="color:#a86b00">其中 ${c.trimmed} 个会按字符上限截断。命名规则 → 字符长度上限。</div>`;
  html+=listBlock('将改名', c.will.map(p=>p[0]+'\n   → '+p[1]+(p[2]?'  〔文件夹〕':'')), 80);
  html+=listBlock('命名冲突', conflict, 40);
  html+=listBlock('没有网盘链接的勾选文件', c.noLink, 40);
  html+=listBlock('将改名的文件夹', c.folders, 40);
  html+='<div class="hint">只改勾选的行。文件夹可以改名，但上传永远只传文件。执行后会写入工程文件，可还原。</div>';
  if(c.issues.size){
    info('无法执行：有命名冲突', html+'<p style="color:var(--err)">请先改掉预览列里标红的行。</p>');
    return;
  }
  if(!plan.length){
    info('没有需要改名的行', html+'<p>勾选的行里，新文件名和原文件名完全一致。</p>');
    return;
  }
  dialog('改名前请确认', html,
    ()=>{
      closeDlg();
      const links={};
      rows.forEach(r=>{ if(r.file&&r.link) links[r.file]=r.link; });
      post('/api/rename',{plan,grid:gridState(),links}).then(d=>{
        if(d.ok===false){ info('执行失败','<pre class="log">'+esc(d.message)+'</pre>'); return; }
        undoCount=d.undo; restorable=d.restorable; afterRename(d,'重命名',true);
      });
      return false;
    },'确认改名 '+plan.length+' 个');
}
function afterRename(d,verb,offerUp){
  const map=new Map((d.renamed||[]).map(p=>[p[0],p[1]]));
  rows.forEach(r=>{ if(r.file&&map.has(r.file)) r.file=map.get(r.file); });
  mergeFiles(d.files, d.entries, d.dirNames);
  buildMenus(); commit();
  const n=(d.renamed||[]).length;
  const ub=$('stUndo');
  if(ub){
    if(n>0 && undoCount){
      ub.classList.add('show');
      ub.disabled=false;
      ub.textContent='撤销刚才 '+n+' 个';
    } else {
      ub.classList.remove('show');
      ub.disabled=true;
    }
  }
  let html=`<p>✅ 成功${verb} <b>${n}</b> 个项目。</p>`;
  if(d.skipped&&d.skipped.length) html+=`<p>⚠ ${d.skipped.length} 个文件已经不在文件夹里，跳过了：</p><pre class="log">${esc(d.skipped.join('\n'))}</pre>`;
  if(d.errors&&d.errors.length) html+=`<p style="color:var(--err)">请注意：</p><pre class="log">${esc(d.errors.join('\n'))}</pre>`;
  html+='<div class="hint">表格内容保持不变，预览列已显示为「无需改动」。工程文件已就地更新。</div>';
  const up=uploadableItems();
  if(offerUp && up.length){
    html+=`<p>其中 <b>${up.length}</b> 个已勾选且填了网盘链接，可以直接上传。</p>
      <div class="foot" style="padding:0;border:0;justify-content:flex-start">
        <button class="primary" onclick="closeDlg();startUpload()">☁ 立即上传这 ${up.length} 个</button>
      </div>`;
  }
  info(verb+'完成',html);
}
function snapLabel(s){
  if(!s) return '';
  const n=s.count?(' · '+s.count+' 个'):'';
  const t=s.time?('  '+s.time):'';
  return s.label+n+t+(s.current?'  （当前）':'');
}
async function restoreOriginal(){ confirmRestoreSnap(0, '还原到最初的文件名'); }
async function restoreLastSnap(){
  const d=await post('/api/snapshots');
  const idx=(d.index||0)-1;
  if(idx<0){ info('没有上一轮','<p>现在已经是最初的文件名，或者还没有成功改过名。</p>'); return; }
  confirmRestoreSnap(idx, '回到上一次成功的名字');
}
async function confirmRestoreSnap(index, title){
  const d=await post('/api/snapplan',{index});
  if(d.ok===false){ info('无法还原','<p>'+esc(d.message||'')+'</p>'); return; }
  const item=(d.list||[]).find(s=>s.index===d.index) || {};
  if(d.current===d.index && !(d.plan||[]).length){
    info('已经在这个时间点','<p>'+esc(snapLabel(item))+'</p>'); return;
  }
  const plan=d.plan||[];
  dialog(title||'回到这个时间点',
    `<p>目标：<b>${esc(snapLabel(item))}</b></p>
     <p>将改名 <b>${plan.length}</b> 个：</p>
     <pre class="log">${esc(plan.slice(0,300).map(p=>p[0]+'\n   → '+p[1]).join('\n')||'（文件名已是这个时间点，只移动光标）')}</pre>
     <div class="hint">这不会删掉更晚的时间点。之后如果再执行一次新的改名，比当前更晚的快照才会丢掉（和新分支一样）。</div>`,
    ()=>{
      closeDlg();
      post('/api/restoresnap',{index:d.index}).then(r=>{
        if(r.ok===false){ info('还原失败','<p>'+esc(r.message)+'</p>'); return; }
        restorable=r.restorable; undoCount=r.undo||0; afterRename(r,'回到时间点');
      });
      return false;
    }, plan.length?('确认改回 '+plan.length+' 个'):'就停在这里');
}
async function showSnapshots(){
  const d=await post('/api/snapshots');
  const list=d.list||[];
  if(!list.length){ info('还没有时间点','<p>成功改名一次之后，这里就会出现快照。</p>'); return; }
  let html='<div class="hint">每次成功改名会留下一个时间点。点下面一行即可回到那个时候的文件名。当前这一行是绿色的。</div>';
  list.forEach(s=>{
    const cls=s.current?'snap cur':'snap';
    const go=s.current?'':'onclick="closeDlg();confirmRestoreSnap('+s.index+')"';
    html+=`<div class="${cls}" ${go}><div class="t">${esc(s.label)}${s.count?(' · '+s.count+' 个'):''}${s.current?' · 当前':''}</div><div class="tm">${esc(s.time||'')}</div></div>`;
  });
  info('改名时间点', html);
}
async function showProject(){
  const d=await post('/api/restoreinfo');
  const snaps=(d.list||[]).map(s=>snapLabel(s)).join('\n');
  info('工程文件信息',
    `<div class="hint">位置：${esc(DIR)}\\.renamer_project.json（隐藏文件）<br>
     每次成功改名都会留下时间点，可以回到上一轮，不必只能回到最开始。<br>
     可用「文件 → 导出工程文件」另存一份可见备份。</div>
     <p>时间点（当前第 ${d.snap_index||0} 个）：</p>
     <pre class="log">${esc(snaps||'（还没有改名记录）')}</pre>
     <p><b>${(d.plan||[]).length}</b> 个文件可还原到最初名字：</p>
     <pre class="log">${esc((d.plan||[]).map(p=>p[0]+'\n   ← 最初：'+p[1]).join('\n')||'（无）')}</pre>`);
}

/* =======================================================================
   复制文件名 / 路径、勾选、上传、文件夹跳转、工程导入导出
   ======================================================================= */
function selectedFileRows(){
  const s=selRange(), out=[];
  for(let r=s.r1;r<=s.r2;r++) if(rows[r]&&rows[r].file) out.push(rows[r]);
  if(!out.length) rows.forEach(r=>{ if(r.file&&rowOn(r)) out.push(r); });
  return out;
}
function copyText(s){
  const ta=document.createElement('textarea');
  ta.value=s; document.body.appendChild(ta); ta.select();
  try{ document.execCommand('copy'); }catch(e){}
  document.body.removeChild(ta);
  wrap.focus();
}
function copyFileNames(){
  const list=selectedFileRows().map(r=>DOC==='dl'?(r.ds&&r.ds.saved||finalName(r)):(DOC==='mk'?finalName(r):r.file)).filter(n=>n&&n!==DL_PENDING&&n!==MK_PENDING);
  if(!list.length){ toast('没有可复制的文件名'); return; }
  copyText(list.join('\n'));
  toast('已复制 '+list.length+' 个文件名');
}
function copyFilePaths(){
  const list=selectedFileRows().map(r=>DIR.replace(/[\\/]+$/,'')+'\\'+r.file);
  if(!list.length){ toast('没有可复制的路径'); return; }
  copyText(list.join('\n'));
  toast('已复制 '+list.length+' 条完整路径');
}
function checkSel(on){
  const s=selRange();
  pushUndo();
  for(let r=s.r1;r<=s.r2;r++) if(rows[r]) rows[r].checked=!!on;
  commit();
}
function revealSel(){
  const r=rows[active.r];
  post('/api/reveal',{file:r&&r.file||''});
}
function openSelFile(){
  const r=rows[active.r];
  if(!r||!r.file){ toast('这一行没有对应文件'); return; }
  post('/api/openfile',{file:r.file});
}

function looksLikeUrl(s){ return /^https?:\/\//i.test(String(s||'').trim()); }
function uploadableItems(onlyFail){
  const out=[];
  rows.forEach(r=>{
    if(!r.file||isDirRow(r)||!r.link) return;
    if(onlyFail==='redo'){
      if(!rowOn(r)||!(r.up && r.up.status==='ok')) return;
    } else if(onlyFail){
      if(!(r.up && r.up.status==='fail')) return;
    } else {
      if(!rowOn(r)) return;
      if(r.up && r.up.status==='ok') return;   // 已传的不再进队列，表格里看得见
    }
    out.push({file:r.file,url:r.link});
  });
  return out;
}
function applyUploadResults(results){ return withDoc('main',()=>applyUploadResultsMain(results)); }
function applyUploadResultsMain(results){
  if(!results||!results.length) return;
  const map={};
  results.forEach(x=>{ if(x&&x.file) map[x.file]=x; });
  let changed=false, cur=-1;
  rows.forEach((r,i)=>{
    if(!r.file||!map[r.file]) return;
    const x=map[r.file];
    const next={status:x.status||'none', id:x.id||'', reason:x.reason||'', url:(r.up&&r.up.url)||''};
    const prev=JSON.stringify(r.up||{});
    r.up=next;
    if(next.status==='ok') r.checked=false;
    if(next.status==='fail'||next.status==='uploading'||next.status==='queued') r.checked=true;
    if(next.status==='uploading') cur=i;
    if(JSON.stringify(r.up)!==prev) changed=true;
  });
  if(changed){
    render();
    scheduleSave();
    if(cur>=0){
      const tr=gridEl.querySelector('tr[data-r="'+cur+'"]');
      if(tr) tr.scrollIntoView({block:'nearest'});
    }
  }
}
function dupFolderBlock(items){
  const groups={};
  items.forEach(it=>{ const id=folderIdOf(it.url); if(!id) return; (groups[id]=groups[id]||[]).push(it.file); });
  const dups=Object.keys(groups).filter(k=>groups[k].length>1);
  if(!dups.length) return '';
  let h='<div class="dupwarn">⚠ <b>注意：有多个文件会传到同一个文件夹。</b>如果每个视频本该去不同的文件夹，说明链接填错了，请先取消并检查「网盘链接」列（带 ×N 标记的格子）。';
  dups.forEach(id=>{ h+=`<div style="margin-top:6px"><b>${groups[id].length} 个文件</b> → folders/${esc(id)}</div>`; });
  h+='<label><input type="checkbox" id="d_dupok">我确认这些文件本来就该传到同一个文件夹</label></div>';
  return h;
}
function startUpload(onlyFail){
  if(upTimer){ toast('已有上传任务在进行，请等它结束。'); return; }
  if(!hasGdrive){ openDriveSetup(onlyFail==='redo'?'redo':(onlyFail?'retry':'upload')); return; }
  if(onlyFail==='redo') return startRedoUpload();
  const items=uploadableItems(!!onlyFail);
  const folders=[], noLink=[], off=[], already=[];
  rows.forEach(r=>{
    if(!r.file) return;
    if(isDirRow(r)){ if(rowOn(r)||onlyFail) folders.push(r.file); return; }
    if(onlyFail) return;
    if(!rowOn(r)){ off.push(r.file); return; }
    if(!r.link) noLink.push(r.file);
    else if(r.up && r.up.status==='ok') already.push(r.file);
  });
  const failN=rows.filter(r=>r.up&&r.up.status==='fail').length;
  let html='<div class="sum">';
  html+=sumRow(items.length?'ok':'mut', onlyFail?'将重试失败项':'将上传文件', items.length);
  html+=sumRow('mut','文件夹（永不上传）', folders.length);
  html+=sumRow(noLink.length?'warn':'','勾选但没有网盘链接', noLink.length);
  html+=sumRow('mut','未勾选', off.length);
  html+=sumRow('','勾选但已传过（本次跳过，需要可点「重新上传」）', already.length);
  html+=sumRow(failN?'warn':'','当前标记为失败', failN);
  html+='</div>';
  html+=dupFolderBlock(items);
  html+=listBlock('将上传', items.map(it=>it.file+'\n   → '+it.url), 60);
  html+=listBlock('没有链接，跳过', noLink, 40);
  html+=listBlock('文件夹，跳过', folders, 40);
  html+=`<div class="hint">${hasRclone?'已找到 rclone。':'还没找到 rclone.exe，上传可能会失败。'}</div>
         <div class="hint">传到哪个文件夹，只看本表这一行填的「网盘链接」。对应关系和结果会写入「上传状态」列和工程文件。</div>
         <div class="hint">文件夹即使勾选也不会上传。成功的行会取消勾选并写「已传」；失败的行保持勾选，方便重试。</div>`;
  if(!items.length){
    info(onlyFail?'没有失败项可重试':'没有可上传的文件', html);
    return;
  }
  dialog(onlyFail?'重试失败的上传':'上传前请确认', html,
    ()=>{
      const dk=$('d_dupok');
      if(dk&&!dk.checked){ dk.parentElement.style.outline='2px solid var(--err)'; dk.parentElement.style.borderRadius='4px'; return false; }
      closeDlg();
      items.forEach(it=>{
        const r=rows.find(x=>x.file===it.file);
        if(r){ r.up={status:'queued',id:'',reason:'',url:it.url}; r.checked=true; }
      });
      render();
      post('/api/upload',{items}).then(d=>{
        if(d.ok===false){ info('无法开始上传','<p>'+esc(d.message)+'</p>'); return; }
        watchUpload();
      });
      return false;
    }, onlyFail?('重试 '+items.length+' 个'):('开始上传 '+items.length+' 个'));
}
function startRedoUpload(){
  const items=uploadableItems('redo');
  if(!items.length){
    info('没有可重新上传的文件','<p>先在左边勾选状态为「已传」的行，并确认它们的「网盘链接」已经改成正确的文件夹。</p>');
    return;
  }
  let same=0;
  const lines=items.map(it=>{
    const r=rows.find(x=>x.file===it.file);
    const prev=r&&r.up&&r.up.url?folderIdOf(r.up.url):'';
    const now=folderIdOf(it.url);
    if(prev&&prev===now) same++;
    return it.file+'\n   上次 → '+(prev?('folders/'+prev):'（未记录）')+'\n   这次 → folders/'+now+(prev&&prev===now?'   ← 和上次相同':'');
  });
  let html='<div class="sum">'+sumRow('ok','将重新上传', items.length)+sumRow(same?'warn':'','目标文件夹和上次相同', same)+'</div>';
  html+='<div class="redowarn">这些文件之前已经上传成功过。重新上传会按<b>现在</b>每一行填写的网盘链接再传一次。<br>'+
        '之前传错位置的那份不会被自动删除，需要你自己去网盘里删掉。<br>'+
        '如果目标文件夹和上次相同、且里面已有同名同大小的文件，rclone 会跳过，不会传出第二份。</div>';
  html+=dupFolderBlock(items);
  html+=`<details class="sumd" open><summary>对照清单（${items.length}）</summary><pre class="log">${esc(lines.join('\n'))}</pre></details>`;
  dialog('重新上传已传过的文件', html, ()=>{
    const dk=$('d_dupok');
    if(dk&&!dk.checked){ dk.parentElement.style.outline='2px solid var(--err)'; dk.parentElement.style.borderRadius='4px'; return false; }
    closeDlg();
    items.forEach(it=>{
      const r=rows.find(x=>x.file===it.file);
      if(r){ r.up={status:'queued',id:'',reason:'',url:it.url}; r.checked=true; }
    });
    render();
    post('/api/upload',{items}).then(d=>{
      if(d.ok===false){ info('无法开始上传','<p>'+esc(d.message)+'</p>'); return; }
      watchUpload();
    });
    return false;
  }, '重新上传 '+items.length+' 个');
}
/* =======================================================================
   下载表：和改名表是同一套表格引擎（见 switchDoc），这里只放「下载」特有的部分。
   · 左边的编辑列从左到右拼成下载后的名字（全空 = 用网盘原名）
   · 右边：网盘里的文件 / 网盘链接 / 下载状态 / 预览：下载后的名字
   ======================================================================= */
const DL_PENDING='（网盘原名）';
let dlOpen=false, dlDest='', dlDestManual=false, dlTimer=null, dlH=0, dlWorkers=3;
let dlResolving=false, dlResolveAgain=false, dlResolveT=null;
let dlFolderGroupSeq=0;
function dlNextFolderGroup(){
  const list=dlRowsRef();
  const used=list.filter(r=>r.isFolderParent && r.folderGroup!==undefined && r.folderGroup>=0).length;
  return (used + (dlFolderGroupSeq++)) % 6;
}
function dlBlankRow(){
  return {id:uid('r'),file:null,kind:null,cells:{},checked:true,link:'',wrap:{},up:{status:'none'},
          fid:'',rname:'',size:0,fromFolder:false,isFolderParent:false,folderCollapsed:true,
          folderGroup:-1,children:[],isSubRow:false,parentId:'',subIndex:0,subTotal:0,
          ds:{status:'none',pct:0,saved:'',msg:''}};
}
function makeDlDoc(){
  const rs=[]; for(let i=0;i<12;i++) rs.push(dlBlankRow());
  return {cols:[{id:uid('c'),name:'新文件名',w:200,type:'data'}],vcols:[],rows:rs,
          sel:{r1:0,c1:0,r2:0,c2:0},active:{r:0,c:0},undoStack:[],redoStack:[],
          joinSep:'',keepExt:true,extMode:'keep',extSet:'.mp4',extFill:true,
          maxName:180,showPrev:true,showLink:true,showStat:true,
          specialW:{porig:190,pnew:200,link:230,stat:160},files:[],hits:[],hitIdx:-1};
}
function dlNormDs(ds){
  ds=ds||{};
  const st=(ds.status==='ok'||ds.status==='fail')?ds.status:'none';
  return {status:st,pct:st==='ok'?100:0,saved:ds.saved||'',msg:st==='fail'?(ds.msg||''):''};
}
function dlSyncFile(r){
  const has=!!String(r.link||'').trim();
  r.file=has?(r.rname||DL_PENDING):null;
  r.kind=has?(r.isFolderParent?'dir':'file'):null;
  if(!r.ds) r.ds={status:'none',pct:0,saved:'',msg:''};
  if(r.fid===undefined) r.fid='';
  if(r.rname===undefined) r.rname='';
  if(r.isFolderParent && Array.isArray(r.children)){
    r.children.forEach(ch=>{
      ch.file=ch.rname||DL_PENDING;
      ch.kind='file';
      if(!ch.ds) ch.ds={status:'none',pct:0,saved:'',msg:''};
    });
  }
}
function dlResetRow(r){
  if(r.isFolderParent && !r.folderCollapsed){
    const list=dlRowsRef();
    for(let i=list.length-1;i>=0;i--){ if(list[i].parentId===r.id) list.splice(i,1); }
  }
  r.fid=''; r.rname=''; r.size=0; r.fromFolder=false;
  r.isFolderParent=false; r.folderCollapsed=true; r.folderGroup=-1; r.children=[];
  r.isSubRow=false; r.parentId=''; r.subIndex=0; r.subTotal=0;
  r.ds={status:'none',pct:0,saved:'',msg:''};
}
function dlStatText(r){
  if(!r||!String(r.link||'').trim()) return '';
  const ds=r.ds||{}, st=ds.status||'none';
  if(r.isFolderParent){
    const chs=r.children||[];
    const okC=chs.filter(c=>c.ds&&c.ds.status==='ok').length;
    const failC=chs.filter(c=>c.ds&&c.ds.status==='fail').length;
    if(st==='ok'||(chs.length&&okC===chs.length)) return `已全部下载（${okC}/${chs.length}）`;
    if(st==='downloading') return `文件夹下载中 ${+ds.pct||0}%（${okC}/${chs.length}）`;
    if(st==='queued') return `排队中（共 ${chs.length} 项）`;
    if(failC) return `已完成 ${okC}/${chs.length}（失败 ${failC}）`;
    return `📁 已收纳 ${chs.length} 个素材`+(r.folderCollapsed?'（已折叠）':'（已展开）');
  }
  if(st==='ok') return '已下载：'+(ds.saved||'');
  if(st==='fail') return '失败：'+(ds.msg||'');
  if(st==='downloading') return '下载中 '+(+ds.pct||0)+'%';
  if(st==='queued') return '排队中';
  if(st==='resolving') return '解析中…';
  return r.fid?'已就绪':'待解析';
}
/* 展开 / 折叠某一个网盘文件夹主行的子行 */
function toggleFolderRow(rowId, forceExpand){
  switchDoc('dl'); if(DOC!=='dl') return;
  if(editing) closeEditor(true);
  const idx=rows.findIndex(r=>r.id===rowId && r.isFolderParent);
  if(idx<0) return;
  const p=rows[idx];
  const wantExpand=(forceExpand===undefined)?!!p.folderCollapsed:!!forceExpand;
  if(wantExpand && p.folderCollapsed){
    p.folderCollapsed=false;
    const chs=(p.children||[]).map((ch,k)=>Object.assign(dlBlankRow(), ch, {
      isSubRow:true, parentId:p.id, subIndex:ch.subIndex||(k+1), subTotal:p.children.length,
      folderGroup:p.folderGroup, checked:p.checked!==false
    }));
    p.children=chs;
    rows.splice(idx+1, 0, ...chs);
  } else if(!wantExpand && !p.folderCollapsed){
    const liveChs=rows.filter(x=>x.parentId===p.id);
    if(liveChs.length) p.children=liveChs;
    for(let i=rows.length-1;i>=0;i--){
      if(rows[i].parentId===p.id) rows.splice(i,1);
    }
    p.folderCollapsed=true;
  }
  active.r=clamp(active.r,0,Math.max(0,rows.length-1));
  sel={r1:active.r,c1:active.c,r2:active.r,c2:active.c};
  render(); dlSave();
}
/* 一键展开或折叠所有网盘文件夹子行 */
function dlToggleAllFolders(expand){
  switchDoc('dl'); if(DOC!=='dl') return;
  if(editing) closeEditor(true);
  const parents=rows.filter(r=>r.isFolderParent).map(r=>r.id);
  if(!parents.length){ toast('当前下载表里还没有已解析的网盘文件夹'); return; }
  parents.forEach(pid=>toggleFolderRow(pid, expand));
  toast(expand?'已展开所有文件夹内的子素材行（默认后缀 (1)(2)(3)，可单独修改）':'已折叠所有文件夹子行，只需在文件夹主行填前缀（如 v01）即可！');
}
function fmtSize(n){
  n=+n||0; if(!n) return '';
  if(n<1024) return n+' B';
  if(n<1024*1024) return (n/1024).toFixed(0)+' KB';
  if(n<1024*1024*1024) return (n/1024/1024).toFixed(1)+' MB';
  return (n/1024/1024/1024).toFixed(2)+' GB';
}
/* 下载表的行 / 列，不管当前正在编辑哪张表，都拿到「活的」那一份 */
function dlRowsRef(){ return DOC==='dl'?rows:docOf('dl').rows; }
function renderDlDoc(){ withDoc('dl',()=>render()); }
function dlSave(){ scheduleSave(); }
function dlSerializeRow(r){
  return {
    id:r.id,link:r.link||'',cells:r.cells||{},checked:r.checked!==false,
    wrap:r.wrap||{},fid:r.fid||'',rname:r.rname||'',size:r.size||0,
    fromFolder:!!r.fromFolder,isFolderParent:!!r.isFolderParent,
    folderCollapsed:r.folderCollapsed!==false,folderGroup:r.folderGroup!==undefined?r.folderGroup:-1,
    isSubRow:!!r.isSubRow,parentId:r.parentId||'',subIndex:r.subIndex||0,subTotal:r.subTotal||0,
    children:Array.isArray(r.children)?r.children.map(ch=>({
      id:ch.id,link:ch.link||'',cells:ch.cells||{},checked:ch.checked!==false,
      wrap:ch.wrap||{},fid:ch.fid||'',rname:ch.rname||'',size:ch.size||0,
      fromFolder:true,isSubRow:true,parentId:r.id,subIndex:ch.subIndex||0,subTotal:ch.subTotal||0,
      folderGroup:r.folderGroup!==undefined?r.folderGroup:-1,ds:dlNormDs(ch.ds)
    })):[],
    ds:dlNormDs(r.ds)
  };
}
function dlState(){
  const d=docOf('dl');
  return {dest:dlDest,doc:{cols:d.cols,joinSep:d.joinSep,keepExt:d.keepExt,
          extMode:d.extMode,extSet:d.extSet,extFill:d.extFill,maxName:d.maxName,
          specialW:d.specialW,showPrev:d.showPrev,showLink:d.showLink,showStat:d.showStat,
          rows:d.rows.map(dlSerializeRow)}};
}
function applyDlState(g){
  const d=g&&g.dl, doc=makeDlDoc();
  if(d&&d.doc&&Array.isArray(d.doc.cols)&&d.doc.cols.length){
    const x=d.doc;
    doc.cols=x.cols; doc.joinSep=x.joinSep||''; doc.keepExt=x.keepExt!==false;
    doc.extMode=x.extMode||(x.keepExt===false?'none':'keep');
    if(x.extSet!==undefined) doc.extSet=x.extSet;
    if(x.extFill!==undefined) doc.extFill=!!x.extFill;
    doc.maxName=(x.maxName===undefined)?180:x.maxName;
    doc.specialW=Object.assign({},doc.specialW,x.specialW||{});
    ['showPrev','showLink','showStat'].forEach(k=>{ if(x[k]!==undefined) doc[k]=!!x[k]; });
    doc.rows=(x.rows||[]).map(r=>{
      const row=Object.assign(dlBlankRow(),r,{id:r.id||uid('r'),ds:dlNormDs(r.ds)});
      if(Array.isArray(r.children)){
        row.children=r.children.map(ch=>Object.assign(dlBlankRow(),ch,{id:ch.id||uid('r'),ds:dlNormDs(ch.ds)}));
      }
      return row;
    });
  } else if(d&&Array.isArray(d.rows)&&d.rows.some(r=>r.link||r.out)){
    /* 上一版下载面板的格式：一列链接 + 一列新名字 */
    doc.rows=d.rows.filter(r=>r.link||r.out).map(r=>{
      const n=dlBlankRow();
      n.link=r.link||''; n.fid=r.fid||''; n.rname=r.name||''; n.size=r.size||0;
      n.fromFolder=!!r.fromFolder; n.checked=r.on!==false;
      if(r.out) n.cells[doc.cols[0].id]=r.out;
      if(r.status==='ok') n.ds={status:'ok',pct:100,saved:r.saved||'',msg:''};
      return n;
    });
  }
  while(doc.rows.length<12) doc.rows.push(dlBlankRow());
  dlDest=(d&&d.dest)||'';
  dlDestManual=!!(dlDest&&DIR&&dlDest.toLowerCase()!==DIR.toLowerCase());
  if(!dlDest) dlDest=DIR;
  if(DOC==='dl'){ loadDoc(doc); render(); } else DOCS.dl=doc;
  dlSyncDest();
  if(dlOpen) renderDlDoc();
}
function updateDlStatus(finals,issues){
  const linked=rows.filter(r=>r.file);
  const folderParents=linked.filter(r=>r.isFolderParent);
  const on=linked.filter(rowOn);
  const ready=on.filter(r=>r.fid).length;
  const done=linked.filter(r=>r.ds&&r.ds.status==='ok').length;
  const bad=linked.filter(r=>r.ds&&r.ds.status==='fail').length;
  const bits=[`${linked.length} 行`+(folderParents.length?`（含 ${folderParents.length} 个同色折叠文件夹）`:''),`勾选 ${on.length}`,`已就绪 ${ready}`];
  if(done) bits.push(`已下载 ${done}`);
  if(bad) bits.push(`<span class="warn">失败 ${bad}</span>`);
  if(issues&&issues.size) bits.push(`<span class="warn2">${issues.size} 行重名（下载时自动加序号）</span>`);
  const el=$('dlsum'); if(el) el.innerHTML=bits.join(' · ');
}
function dlSyncDest(){
  const dd=$('dldest'); if(!dd) return;
  const dest=dlDest||DIR;
  dd.textContent=dest; dd.title='保存到：'+dest+'（点击更换）';
}
function dlUseCurrentDir(){ dlDest=DIR; dlDestManual=false; dlSyncDest(); dlSave(); }
async function pickDlFolder(){
  const d=await post('/api/pickdlfolder',{initial:dlDest||DIR});
  if(d&&d.ok&&d.dir){ dlDest=d.dir; dlDestManual=true; dlSyncDest(); dlSave(); }
}
function dlSaveWorkers(){ dlWorkers=+($('dlWorkers')||{}).value||3; scheduleSave(); }
function dlStop(){ post('/api/downloadstop',{}); toast('正在停止下载…（下到一半的文件不会留下半截）'); }
function dlAddRows(n){
  switchDoc('dl'); if(DOC!=='dl') return;
  pushUndo();
  for(let i=0;i<(n||5);i++) rows.push(dlBlankRow());
  commit();
}
function dlClear(){
  if(dlTimer){ toast('下载还在进行，先等它结束'); return; }
  switchDoc('dl'); if(DOC!=='dl') return;
  pushUndo();
  rows=[]; for(let i=0;i<12;i++) rows.push(dlBlankRow());
  sel={r1:0,c1:0,r2:0,c2:0}; active={r:0,c:0};
  commit(); toast('下载表格已清空（Ctrl+Z 可以撤销）');
}
/* 打开 / 收起下载面板。fromUser=true 表示是用户点的：打开时切到下载表并把光标放到第一个空链接格 */
function toggleDownload(force, fromUser){
  dlOpen=(force===undefined)?!dlOpen:!!force;
  if(dlOpen&&mkOpen) mkHide();
  $('dlpane').style.display=dlOpen?'flex':'none';
  $('splitter').style.display=(dlOpen||mkOpen)?'block':'none';
  const b=$('btnDl'); if(b) b.classList.toggle('on',dlOpen);
  if(dlOpen){
    if(!dlDest) dlDest=DIR;
    if(dlH>0) $('dlpane').style.height=dlH+'px';
    dlSyncDest();
    renderDlDoc();
  }
  if(fromUser){
    if(dlOpen){
      switchDoc('dl');
      if(DOC==='dl'){
        const li=vcols.findIndex(c=>c.type==='link');
        let r=rows.findIndex(x=>!String(x.link||'').trim()); if(r<0) r=0;
        wrap.focus(); if(li>=0) setActive(r,li,false);
      }
    } else if(DOC==='dl') switchDoc('main');
  }
  refitAll(); scheduleSave();
}
$('splitter').addEventListener('mousedown',e=>{
  e.preventDefault();
  const pane=mkOpen?$('mkpane'):$('dlpane');
  const y0=e.clientY, h0=pane.offsetHeight;
  const mv=ev=>{
    const h=Math.max(150,Math.min(window.innerHeight-260,h0-(ev.clientY-y0)));
    dlH=h; $('dlpane').style.height=h+'px'; $('mkpane').style.height=h+'px'; refitAll();
  };
  const up=()=>{ document.removeEventListener('mousemove',mv); document.removeEventListener('mouseup',up);
                 placeFill(); scheduleSave(); };
  document.addEventListener('mousemove',mv); document.addEventListener('mouseup',up);
});
/* 粘贴或改了链接之后，自动去解析（稍等一下，免得连续粘贴时反复解析） */
function dlScheduleResolve(){
  clearTimeout(dlResolveT);
  dlResolveT=setTimeout(()=>dlResolveAll(),350);
}
function dlResolveSelected(){
  switchDoc('dl'); if(DOC!=='dl') return;
  const s=selRange(), ids=[];
  for(let r=s.r1;r<=s.r2;r++){ const x=rows[r]; if(x&&String(x.link||'').trim()){ dlResetRow(x); ids.push(x.id); } }
  if(!ids.length){ toast('选中的行里没有链接'); return; }
  render(); dlResolveAll(ids);
}
function dlResolveButton(){
  dlRowsRef().forEach(r=>{ if(String(r.link||'').trim()&&!r.fid&&r.ds&&r.ds.status==='fail') r.ds={status:'none',pct:0,saved:'',msg:''}; });
  renderDlDoc(); dlResolveAll();
}
/* 解析：文件链接确认 ID；文件夹链接默认收纳为可折叠的同色分组父行，子行默认带 (1)(2)(3) 后缀 */
async function dlResolveAll(onlyIds){
  if(dlResolving){ dlResolveAgain=true; return 0; }
  dlResolving=true;
  let okN=0, failN=0, folderN=0;
  try{
    for(let guard=0; guard<2000; guard++){
      const list=dlRowsRef();
      const row=list.find(r=>String(r.link||'').trim() && !r.fid && !r.isSubRow &&
        !['resolving','fail','ok','downloading','queued'].includes((r.ds||{}).status) &&
        (!onlyIds||onlyIds.includes(r.id)));
      if(!row) break;
      const id=row.id, link=String(row.link).trim();
      row.ds={status:'resolving',pct:0,saved:'',msg:''};
      renderDlDoc();
      let d=null;
      try{ d=await post('/api/resolvelink',{link}); }catch(e){ d=null; }
      const list2=dlRowsRef(), i=list2.findIndex(r=>r.id===id);
      if(i<0) continue;
      const r=list2[i];
      if(String(r.link||'').trim()!==link){ dlResetRow(r); continue; }     // 解析期间链接被改了
      if(!d||d.ok===false){
        r.ds={status:'fail',pct:0,saved:'',msg:(d&&d.message)||'解析失败'}; failN++; renderDlDoc(); continue;
      }
      if(d.kind==='file'){
        r.fid=d.id; r.ds={status:'none',pct:0,saved:'',msg:''}; okN++; renderDlDoc(); continue;
      }
      const fl=d.files||[];
      if(!fl.length){
        r.ds={status:'fail',pct:0,saved:'',msg:d.subdirs?'这个文件夹里只有子文件夹，没有文件':'这个文件夹是空的'};
        failN++; renderDlDoc(); continue;
      }
      const dcols=docOf('dl').cols;
      const firstColId=(dcols[0]&&dcols[0].id)||'';
      const grp=dlNextFolderGroup();
      r.isFolderParent=true;
      r.folderCollapsed=true;
      r.folderGroup=grp;
      r.fid='FOLDER:'+(d.id||uid('f'));
      r.rname=`📁 文件夹（共 ${fl.length} 个素材）`;
      r.size=fl.reduce((s,f)=>s+(+f.size||0),0);
      r.ds={status:'none',pct:0,saved:'',msg:''};
      r.children=fl.map((f,k)=>{
        const n=dlBlankRow();
        n.link=r.link; n.fid=f.id||''; n.rname=f.name||''; n.size=+f.size||0;
        n.fromFolder=true; n.isSubRow=true; n.parentId=r.id;
        n.subIndex=k+1; n.subTotal=fl.length; n.folderGroup=grp;
        n.checked=r.checked!==false;
        n.cells={};
        if(firstColId) n.cells[firstColId]='('+(k+1)+')';
        if(!n.fid) n.ds={status:'fail',pct:0,saved:'',msg:'这个文件没有 ID'};
        return n;
      });
      folderN++; okN+=fl.length;
      renderDlDoc();
    }
  } finally { dlResolving=false; }
  dlSave();
  if(okN||failN) toast(`链接解析完成：${okN} 个素材就绪`+(folderN?`（${folderN} 个文件夹已按同色折叠，填前缀如 v01 即自动编为 v01(1)(2)…）`:'')+(failN?`，${failN} 条没认出来`:''));
  if(dlResolveAgain){ dlResolveAgain=false; dlScheduleResolve(); }
  return okN;
}
/* 开始下载：支持折叠的文件夹父行（自动按 v01(1)、v01(2) 下载所有子素材）及展开的子行 */
async function dlStart(){
  if(dlTimer){ toast('已有下载任务在进行，请等它结束。'); return; }
  if(!hasGdrive){ openDriveSetup('download'); return; }
  if(!dlOpen) toggleDownload(true);
  switchDoc('dl'); if(DOC!=='dl'){ toast('改名表正在执行操作，稍后再试'); return; }
  if(editing) closeEditor(true);
  rows.forEach(dlSyncFile);
  const picked=rows.filter(r=>r.file&&rowOn(r));
  if(!picked.length){ info('没有要下载的行','<p>先在右边的「网盘链接」列粘贴链接，并勾选左边的复选框。</p>'); return; }
  const pend=picked.filter(r=>!r.fid);
  if(pend.length){
    pend.forEach(r=>{ if(r.ds&&r.ds.status==='fail') r.ds={status:'none',pct:0,saved:'',msg:''}; });
    toast('正在解析链接…'); await dlResolveAll(pend.map(r=>r.id));
  }
  const plan=withDoc('dl',()=>{
    rows.forEach(dlSyncFile);
    const items=[], bad=[], done=[], lines=[];
    const pushChildItem=(parentRow, ch)=>{
      if(!rowOn(ch)) return;
      if(!ch.fid){ bad.push(ch); return; }
      if(ch.ds&&ch.ds.status==='downloading') return;
      if(ch.ds&&ch.ds.status==='ok') done.push(ch);
      const stem=dlChildStem(parentRow, ch);
      const shown=stem?(stem+extPreview(ch)):dlRemoteShown(ch);
      items.push({key:ch.id,id:ch.fid,name:ch.rname||'',stem,
                  keepExt:extMode!=='none', ext:(extMode==='set'?normExt(extSet):''), extFill:!!extFill,
                  size:ch.size||0});
      lines.push(shown+'\n   ← '+(ch.rname||('网盘文件 '+ch.fid)));
    };
    rows.forEach(r=>{
      if(!r.file||!rowOn(r)) return;
      if(r.isFolderParent){
        if(r.folderCollapsed){
          (r.children||[]).forEach(ch=>pushChildItem(r, ch));
        }
        return;
      }
      if(r.isSubRow){
        pushChildItem(dlParentOf(r), r);
        return;
      }
      if(!r.fid){ bad.push(r); return; }
      if(r.ds&&r.ds.status==='downloading') return;
      if(r.ds&&r.ds.status==='ok') done.push(r);
      const stem=sanitize(cappedStem(r));
      const shown=stem?(stem+extPreview(r)):dlRemoteShown(r);
      items.push({key:r.id,id:r.fid,name:r.rname||'',stem,
                  keepExt:extMode!=='none', ext:(extMode==='set'?normExt(extSet):''), extFill:!!extFill,
                  size:r.size||0});
      lines.push(shown+'\n   ← '+(r.rname||('网盘文件 '+r.fid)));
    });
    return {items,bad,done,lines,issues:computeIssues().issues.size};
  });
  const {items,bad,done,lines}=plan;
  if(!items.length){
    info('没有可下载的文件','<p>勾选的行里没有解析成功的网盘文件。看一下「下载状态」列里写的原因。</p>'
      +listBlock('没认出来的链接', bad.map(r=>r.link+'\n   → '+((r.ds&&r.ds.msg)||'未解析')), 40));
    return;
  }
  let html='<div class="sum">';
  html+=sumRow('ok','将下载', items.length);
  html+=sumRow('','其中按表格重新命名', items.filter(it=>it.stem).length);
  html+=sumRow(bad.length?'warn':'mut','勾了但链接没认出来（跳过）', bad.length);
  html+=sumRow(done.length?'warn':'mut','之前已经下载过（会再下一次）', done.length);
  const known=items.filter(it=>+it.size>0), total=known.reduce((n,it)=>n+(+it.size||0),0);
  if(total) html+=sumRow('','合计大小'+(known.length<items.length?`（另有 ${items.length-known.length} 个大小未知）`:''), fmtSize(total));
  html+='</div>';
  html+=`<div class="hint">保存到：<b>${esc(dlDest||DIR)}</b></div>`;
  if(plan.issues) html+=`<div class="hint" style="color:#a86b00">有 ${plan.issues} 行名字重复，下载时会自动加 (2)(3)，不会互相覆盖。</div>`;
  html+=listBlock('下载后的名字 ← 网盘里的文件', lines, 80);
  html+=listBlock('跳过（链接没认出来）', bad.map(r=>r.link+'\n   → '+((r.ds&&r.ds.msg)||'未解析')), 30);
  html+=`<div class="hint">${hasRclone?'已找到 rclone。':'还没找到 rclone.exe，下载会失败。'}
         文件夹主行填前缀（如 v01），里面的多个文件会自动命名为 v01(1)、v01(2)、v01(3) 并补上原扩展名。</div>`;
  dialog('下载前请确认', html, ()=>{
    closeDlg();
    const list=dlRowsRef();
    const keySet=new Set(items.map(it=>it.key));
    list.forEach(r=>{
      if(keySet.has(r.id)) r.ds={status:'queued',pct:0,saved:'',msg:''};
      if(r.isFolderParent && Array.isArray(r.children)){
        let anyQ=false;
        r.children.forEach(ch=>{ if(keySet.has(ch.id)){ ch.ds={status:'queued',pct:0,saved:'',msg:''}; anyQ=true; } });
        if(anyQ) r.ds={status:'queued',pct:0,saved:'',msg:''};
      }
    });
    renderDlDoc();
    post('/api/download',{dest:dlDest||DIR,items,workers:dlWorkers}).then(d=>{
      if(d.ok===false){
        const l2=dlRowsRef();
        l2.forEach(r=>{
          if(keySet.has(r.id)&&r.ds.status==='queued') r.ds={status:'none',pct:0,saved:'',msg:''};
          if(r.isFolderParent&&Array.isArray(r.children)){
            r.children.forEach(ch=>{ if(keySet.has(ch.id)&&ch.ds.status==='queued') ch.ds={status:'none',pct:0,saved:'',msg:''}; });
            if(r.ds.status==='queued') r.ds={status:'none',pct:0,saved:'',msg:''};
          }
        });
        renderDlDoc();
        info('无法开始下载','<p>'+esc(d.message)+'</p>'); return;
      }
      watchDownload();
    });
    return false;
  }, '开始下载 '+items.length+' 个');
}
function toggleDlLog(){
  const el=$('dllog');
  el.style.display=el.style.display==='block'?'none':'block';
}
function applyDlResults(results){
  if(!results||!results.length) return;
  const map={}; results.forEach(x=>{ if(x&&x.key) map[x.key]=x; });
  let changed=false;
  const updateOne=(r,x)=>{
    const st=x.status||'none', pct=+x.pct||0;
    if(st==='ok'&&x.remote&&!r.rname){ r.rname=x.remote; if(x.size&&!r.size) r.size=+x.size; changed=true; }
    const next={status:st,pct,saved:x.saved||'',msg:x.reason||((st==='fail')?((r.ds||{}).msg||''):'')};
    if(st==='downloading'&&x.reason) next.msg=x.reason;
    if(JSON.stringify(next)!==JSON.stringify(r.ds||{})){ r.ds=next; changed=true; }
  };
  const list=dlRowsRef();
  list.forEach(r=>{
    if(map[r.id]) updateOne(r, map[r.id]);
    if(r.isFolderParent && Array.isArray(r.children) && r.children.length){
      r.children.forEach(ch=>{
        if(map[ch.id]) updateOne(ch, map[ch.id]);
        const live=list.find(z=>z.id===ch.id);
        if(live && map[ch.id]) updateOne(live, map[ch.id]);
      });
      const chs=r.children;
      const tot=chs.length;
      const okC=chs.filter(c=>c.ds&&c.ds.status==='ok').length;
      const failC=chs.filter(c=>c.ds&&c.ds.status==='fail').length;
      const dlC=chs.filter(c=>c.ds&&c.ds.status==='downloading').length;
      const qC=chs.filter(c=>c.ds&&c.ds.status==='queued').length;
      const avgPct=Math.round(chs.reduce((s,c)=>s+((c.ds&&c.ds.status==='ok')?100:(+(c.ds&&c.ds.pct)||0)),0)/tot);
      let pst='none';
      if(okC===tot) pst='ok';
      else if(dlC>0 || (okC>0 && qC>0)) pst='downloading';
      else if(qC>0) pst='queued';
      else if(failC>0) pst='fail';
      const pnext={status:pst,pct:avgPct,saved:`${okC}/${tot} 个文件`,msg:failC?`${failC} 项失败`:''};
      if(JSON.stringify(pnext)!==JSON.stringify(r.ds||{})){ r.ds=pnext; changed=true; }
    }
  });
  if(changed){ renderDlDoc(); dlSave(); }
}
/* 下载完刷新改名表的文件列表（悄悄刷新，不打断正在下载表里编辑的人） */
async function refreshMainQuiet(){
  const d=await post('/api/files',{includeDirs,videosOnly});
  if(!d||d.ok===false) return;
  withDoc('main',()=>{ restorable=d.restorable; mergeFiles(d.files,d.entries,d.dirNames); render(); });
  scheduleSave();
}
function watchDownload(){
  $('dlstat').textContent='正在下载…';
  $('dlStopBtn').style.display='inline-block';
  const tick=async()=>{
    const s=await post('/api/downloadstatus');
    applyDlResults(s.results||[]);
    const logEl=$('dllog');
    if(logEl){ logEl.textContent=(s.log||[]).join('\n'); logEl.scrollTop=logEl.scrollHeight; }
    if(s.running){
      $('dlstat').innerHTML=`下载中　成功 ${s.ok} / ${s.plan}　失败 ${s.fail}`;
      $('dlcur').textContent=s.current||'';
    } else if(s.done){
      clearInterval(dlTimer); dlTimer=null;
      applyDlResults(s.results||[]);
      $('dlStopBtn').style.display='none';
      const failN=+(s.fail||0);
      $('dlstat').innerHTML=`完成：成功 <b>${s.ok}</b> / ${s.plan}　失败 <b>${failN}</b>`;
      $('dlcur').textContent=s.message||'';
      dlSave();
      toast(failN?('下载结束，失败 '+failN+' 个，原因写在「下载状态」列'):('下载完成，成功 '+s.ok+' 个'));
      if((dlDest||DIR).toLowerCase()===DIR.toLowerCase() && s.ok) refreshMainQuiet();
    }
  };
  dlTimer=setInterval(tick,800); tick();
}

/* =======================================================================
   一键连接 Google Drive
   用户只需要：点「一键连接」→ 在自动打开的浏览器里点「允许」。
   其余（授权、写 rclone 配置、测试）全部由后台脚本完成。
   ======================================================================= */
let hasGdrive=false, gdTimer=null, gdReason='', gdLastKey='';
const GD_CLOUD=`<svg width="38" height="38" viewBox="0 0 48 48" fill="none">
  <defs><linearGradient id="gdg" x1="6" y1="10" x2="42" y2="40" gradientUnits="userSpaceOnUse">
    <stop stop-color="#3b8bff"/><stop offset="1" stop-color="#34a853"/></linearGradient></defs>
  <path d="M14.5 37h20a8.5 8.5 0 0 0 1.3-16.9A11.5 11.5 0 0 0 13.6 18 9.5 9.5 0 0 0 14.5 37Z" stroke="url(#gdg)" stroke-width="3" stroke-linejoin="round"/>
  <path d="M20 26.5l4-4 4 4M24 22.8V32" stroke="url(#gdg)" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
const GD_CHECK=`<svg width="34" height="34" viewBox="0 0 34 34" fill="none"><path d="M9 17.5l5.5 5.5L25.5 12" stroke="#fff" stroke-width="3.4"
  stroke-linecap="round" stroke-linejoin="round" stroke-dasharray="30" stroke-dashoffset="30" style="animation:gddraw .45s .15s ease-out forwards"/></svg>`;
const GD_WARN=`<svg width="32" height="32" viewBox="0 0 32 32" fill="none"><path d="M16 9v9" stroke="#fff" stroke-width="3.2" stroke-linecap="round"/>
  <circle cx="16" cy="23.5" r="2" fill="#fff"/></svg>`;
const GD_NOTE=`<div class="gd-note">🔒 授权只保存在这台电脑的 rclone 配置文件里。<br>工具不会上传、记录或分享它；别人拿到这个工具，连的是他自己的网盘。</div>`;
const GD_STEPS=[['准备','检查这台电脑上的 rclone'],
  ['在浏览器里点「允许」','浏览器会自动打开 Google 登录页：选择你的账号，然后点「允许」（页面上的应用名是 rclone，这是正常的）。完成后回到这里就行'],
  ['保存连接','自动写好 gdrive 配置，不用你填任何东西'],
  ['测试连接','读一次网盘空间，确认真的能用']];
function updateDrivePill(busy){
  const b=$('btnDrive'); if(!b) return;
  b.classList.toggle('on',!!hasGdrive&&!busy);
  b.classList.toggle('busy',!!busy);
  b.innerHTML='<i></i>'+(busy?'正在连接…':(hasGdrive?'网盘已连接':'连接网盘'));
  b.title=hasGdrive?'Google Drive 已连接，点这里查看或更换账号':'还没有连接 Google Drive：点这里一键连接';
  buildMenus();
}
function gdIsOpen(){ return $('mask').style.display==='flex' && $('dlg').classList.contains('gdwrap'); }
function gdShow(html){
  const d=$('dlg');
  d.classList.add('gdwrap');
  d.innerHTML=`<div class="gd">${html}</div>`;
  $('mask').style.display='flex';
}
function gdHero(title,sub,icon,cls){
  return `<div class="gd-hero"><div class="gd-icon ${cls||''}">${icon||GD_CLOUD}</div>
    <div class="gd-title">${title}</div><div class="gd-sub">${sub}</div></div>`;
}
function gdStepsHtml(cur,failed,idle){
  return '<ol class="gd-steps">'+GD_STEPS.map((x,i)=>{
    const n=i+1;
    let cls=idle?'idle':(n<cur?'done':(n===cur?(failed?'fail':'active'):''));
    const mark=(!idle&&n<cur)?'✓':((!idle&&n===cur&&failed)?'!':n);
    return `<li class="gd-step ${cls}"><span class="gd-dot">${mark}</span><div><div class="t">${x[0]}</div><div class="d">${x[1]}</div></div></li>`;
  }).join('')+'</ol>';
}
function gdQuotaHtml(a){
  if(!a||!(+a.total||+a.used)) return '';
  if(!+a.total) return `<div class="gd-quota"><div class="gd-qrow"><span>网盘已用</span><b>${fmtSize(a.used)}</b></div></div>`;
  const p=Math.max(1,Math.min(100,Math.round(a.used/a.total*100)));
  const free=+a.free||Math.max(0,a.total-a.used);
  return `<div class="gd-quota"><div class="gd-qrow"><span>网盘空间</span><b>${fmtSize(a.used)} / ${fmtSize(a.total)}</b></div>
    <div class="gd-bar"><i style="width:0%" data-w="${p}"></i></div><div class="gd-qfree">还剩 ${fmtSize(free)}</div></div>`;
}
function gdAnimateBars(){ setTimeout(()=>document.querySelectorAll('.gd-bar i[data-w]').forEach(i=>i.style.width=i.dataset.w+'%'),60); }
function gdReasonText(){
  return {upload:'上传之前，先连上你自己的 Google Drive。',retry:'重试上传之前，先连上你的 Google Drive。',
          redo:'重新上传之前，先连上你的 Google Drive。',download:'下载之前，先连上你自己的 Google Drive。'}[gdReason]
         ||'上传和下载都会用你自己的网盘账号。';
}
async function openDriveSetup(reason){
  gdReason=reason||'';
  gdShow(gdHero('Google Drive','正在检查连接…')+'<div class="gd-body"><div class="gd-skel"></div></div>');
  let st=null;
  try{ st=await post('/api/drivestatus',{}); }catch(e){ st=null; }
  if(!gdIsOpen()) return;
  if(!st) return gdRenderError('没能读取连接状态，请稍后再试。',0);
  if(st.running){ gdWatch(); return gdRenderRunning(st); }
  if(!st.rclone) return gdRenderNoRclone();
  if(st.configured){ hasGdrive=true; updateDrivePill(); return gdRenderConnected(); }
  hasGdrive=false; updateDrivePill();
  gdRenderIntro();
}
function gdRenderIntro(){
  gdLastKey='';
  gdShow(gdHero('连接 Google Drive',gdReasonText()+'<br>只需要在浏览器里点一次「允许」，其余全部自动完成。')+
    `<div class="gd-body">${gdStepsHtml(0,false,true)}</div>
     <div class="gd-actions"><button class="gd-btn" onclick="gdConnect()">一键连接</button>
       <button class="gd-link" onclick="closeDlg()">以后再说</button></div>`+GD_NOTE);
}
async function gdConnect(){
  gdRenderRunning({step:1,message:'正在准备…'});
  updateDrivePill(true);
  let d=null;
  try{ d=await post('/api/driveconnect',{}); }catch(e){ d=null; }
  if(!d||d.ok===false){ updateDrivePill(false); return gdRenderError((d&&d.message)||'没能开始连接。',1); }
  gdWatch();
}
function gdRenderRunning(st){
  const key=[st.step,st.message,st.authUrl].join('|');
  if(key===gdLastKey && gdIsOpen()) return;
  gdLastKey=key;
  const step=st.step||1;
  let body=gdStepsHtml(step,false,false);
  if(step===2&&st.authUrl) body=body.replace('完成后回到这里就行</div>',
    `完成后回到这里就行</div><span class="gd-open" onclick="gdOpenAuth()">浏览器没有自动打开？点这里打开授权页</span>`);
  window._gdAuthUrl=st.authUrl||'';
  gdShow(gdHero(step===2?'去浏览器里点「允许」':'正在连接…',esc(st.message||''),GD_CLOUD,'spin')+
    `<div class="gd-body">${body}</div>
     <div class="gd-actions"><div class="gd-row">
       <button class="gd-soft" onclick="gdCancel()">取消</button>
       <button class="gd-soft" onclick="closeDlg()" title="连接会在后台继续，完成后会提示你">在后台继续</button></div></div>`+GD_NOTE);
}
function gdOpenAuth(){ if(window._gdAuthUrl) post('/api/openurl',{url:window._gdAuthUrl}); }
async function gdCancel(){ await post('/api/drivecancel',{}); }
function gdWatch(){
  clearInterval(gdTimer);
  updateDrivePill(true);
  const tick=async()=>{
    let st=null;
    try{ st=await post('/api/drivestatus',{}); }catch(e){ return; }
    if(st.running){ updateDrivePill(true); if(gdIsOpen()) gdRenderRunning(st); return; }
    clearInterval(gdTimer); gdTimer=null; gdLastKey='';
    if(st.done&&st.ok){
      hasGdrive=true; hasRclone=true; updateDrivePill(false);
      if(gdIsOpen()) gdRenderSuccess(st.about);
      else toast('Google Drive 已连接好，上传和下载可以直接用了');
    } else {
      hasGdrive=!!st.configured; updateDrivePill(false);
      if(st.done&&st.error&&/^已取消/.test(st.error)){ if(gdIsOpen()) gdRenderIntro(); toast('已取消连接'); }
      else if(st.done&&st.error){ if(gdIsOpen()) gdRenderError(st.error,st.step); else toast('Google Drive 没有连上：'+st.error); }
      else if(gdIsOpen()) gdRenderIntro();
    }
  };
  gdTimer=setInterval(tick,700); tick();
}
function gdContinueLabel(){
  return {upload:'继续上传',retry:'继续重试上传',redo:'继续重新上传',download:'继续下载'}[gdReason]||'完成';
}
function gdContinue(){
  const r=gdReason; gdReason=''; closeDlg();
  if(r==='upload') startUpload(false);
  else if(r==='retry') startUpload(true);
  else if(r==='redo') startUpload('redo');
  else if(r==='download') dlStart();
}
function gdRenderSuccess(about){
  gdShow(gdHero('连接成功','以后上传和下载都会自动使用这个网盘，不用再设置。',GD_CHECK,'ok')+
    `<div class="gd-body">${gdQuotaHtml(about)}</div>
     <div class="gd-actions"><button class="gd-btn green" onclick="gdContinue()">${gdContinueLabel()}</button></div>`+GD_NOTE);
  gdAnimateBars();
}
async function gdRenderConnected(){
  gdShow(gdHero('Google Drive 已连接','上传和下载都在使用这个连接。',GD_CHECK,'ok')+
    `<div class="gd-body" id="gdQuota"><div class="gd-skel"></div></div>
     <div class="gd-actions" id="gdAct">
       <button class="gd-btn green" onclick="gdContinue()">${gdContinueLabel()}</button>
       <div class="gd-row">
         <button class="gd-soft" onclick="gdTest()">测试连接</button>
         <button class="gd-soft" onclick="gdSwitch()">换一个账号</button>
         <button class="gd-soft warn" onclick="gdAskDisconnect()">断开连接</button></div></div>`+GD_NOTE);
  gdTest(true);
}
async function gdTest(silent){
  const q=$('gdQuota'); if(q&&!silent) q.innerHTML='<div class="gd-skel"></div>';
  let d=null;
  try{ d=await post('/api/drivetest',{}); }catch(e){ d=null; }
  const q2=$('gdQuota'); if(!q2) return;
  if(d&&d.ok){
    q2.innerHTML=gdQuotaHtml(d.about)||'<div class="gd-quota"><div class="gd-qrow"><span>连接正常</span><b>✓</b></div></div>';
    gdAnimateBars();
    if(!silent) toast('连接正常');
  } else {
    q2.innerHTML=`<div class="gd-err">连接好像失效了：${esc((d&&d.message)||'读不到网盘')}<br>点「换一个账号」重新连接一次就好（可以选同一个账号）。</div>`;
  }
}
function gdSwitch(){
  gdReason=gdReason||'';
  gdShow(gdHero('换一个账号','会打开浏览器让你重新选择账号。<br>新账号连接成功之后才会替换，中途取消不会影响现在的连接。')+
    `<div class="gd-body">${gdStepsHtml(0,false,true)}</div>
     <div class="gd-actions"><button class="gd-btn" onclick="gdConnect()">开始</button>
       <button class="gd-link" onclick="gdRenderConnected()">返回</button></div>`+GD_NOTE);
}
function gdAskDisconnect(){
  const a=$('gdAct'); if(!a) return;
  a.innerHTML=`<div class="gd-confirm">断开后，上传和下载都要重新连接才能用。确定吗？</div>
    <div class="gd-row"><button class="gd-soft warn" onclick="gdDisconnect()">确定断开</button>
    <button class="gd-soft" onclick="gdRenderConnected()">不断开</button></div>`;
}
async function gdDisconnect(){
  await post('/api/drivedisconnect',{});
  hasGdrive=false; updateDrivePill(false);
  toast('已断开 Google Drive');
  gdRenderIntro();
}
function gdRenderError(msg,step){
  gdLastKey='';
  gdShow(gdHero('没有连上',gdReasonText(),GD_WARN,'bad')+
    `<div class="gd-body">${step?gdStepsHtml(step,true,false):''}<div class="gd-err">${esc(msg)}</div></div>
     <div class="gd-actions"><button class="gd-btn" onclick="gdConnect()">再试一次</button>
       <button class="gd-link" onclick="closeDlg()">关闭</button></div>`+GD_NOTE);
}
function gdRenderNoRclone(){
  gdShow(gdHero('还差一个小组件','连接网盘需要 rclone（开源的网盘同步工具），安装包里已经带了它。',GD_WARN,'bad')+
    `<div class="gd-body"><div class="gd-err">没有找到 rclone.exe。<br>请重新双击运行工具文件夹里的「安装.bat」，它会把 rclone 一起装好；装完再打开这里点「一键连接」。</div></div>
     <div class="gd-actions"><button class="gd-link" onclick="closeDlg()">知道了</button></div>`);
}

/* =======================================================================
   批量新建文件夹：和改名表同一套表格。每一行 = 一个文件夹，
   左边各列从左到右拼成文件夹名（连接符、统一单元格、长度上限、命名规则全部通用）。
   ======================================================================= */
const MK_PENDING='（新文件夹）';
let mkOpen=false, mkDest='', mkDestManual=false, mkExisting=new Set(), mkLast=null, mkBusy=false;
function mkBlankRow(){
  return {id:uid('r'),file:null,kind:null,cells:{},checked:true,link:'',wrap:{},up:{status:'none'},
          ms:{status:'none',made:'',msg:''}};
}
function makeMkDoc(){
  const rs=[]; for(let i=0;i<12;i++) rs.push(mkBlankRow());
  return {cols:[{id:uid('c'),name:'文件夹名',w:240,type:'data'}],vcols:[],rows:rs,
          sel:{r1:0,c1:0,r2:0,c2:0},active:{r:0,c:0},undoStack:[],redoStack:[],
          joinSep:'',keepExt:false,extMode:'none',extSet:'',extFill:true,
          maxName:180,showPrev:true,showLink:false,showStat:true,
          specialW:{porig:180,pnew:280,link:180,stat:170},files:[],hits:[],hitIdx:-1};
}
/* 一行里自己填了内容才算一个文件夹（只有统一单元格的空行不算，免得每个空行都建一个） */
function mkSyncFile(r){
  const has=Object.values(r.cells||{}).some(v=>String(v==null?'':v).trim());
  r.file=has?MK_PENDING:null; r.kind=has?'file':null;
  if(!r.ms) r.ms={status:'none',made:'',msg:''};
}
function mkNameOf(r){ return r.file?sanitize(cappedStem(r)):''; }
const MK_RESERVED=new Set(['CON','PRN','AUX','NUL','COM1','COM2','COM3','COM4','COM5','COM6','COM7','COM8','COM9',
                           'LPT1','LPT2','LPT3','LPT4','LPT5','LPT6','LPT7','LPT8','LPT9']);
function mkBadReason(n){
  if(MK_RESERVED.has(n.split('.')[0].trim().toUpperCase())) return 'Windows 保留名，不能当文件夹名';
  if(n.length>240) return '名字太长（超过 240 个字）';
  return '';
}
function mkState(r){
  if(!r||!r.file) return '';
  const n=mkNameOf(r), ms=r.ms||{};
  if(!n) return 'empty';
  if(ms.made&&ms.made.toLowerCase()===n.toLowerCase()){
    if(ms.status==='ok') return 'ok';
    if(ms.status==='fail') return 'fail';
  }
  if(mkBadReason(n)) return 'bad';
  if(mkExisting.has(n.toLowerCase())) return 'exists';
  return 'new';
}
function mkStatText(r){
  const k=mkState(r);
  if(k==='ok') return '✓ 已创建';
  if(k==='fail') return '失败：'+((r.ms&&r.ms.msg)||'');
  if(k==='exists') return '已存在（会跳过）';
  if(k==='bad') return '⚠ '+mkBadReason(mkNameOf(r));
  if(k==='new') return sanitize(joinRow(r))!==joinRow(r).trim()?'待创建（去掉了不能用的符号）':'待创建';
  if(k==='empty') return '名字是空的';
  return '';
}
function mkRowsRef(){ return DOC==='mk'?rows:docOf('mk').rows; }
function renderMkDoc(){ withDoc('mk',()=>render()); }
function mkStateSave(){
  const d=docOf('mk');
  return {dest:mkDest,doc:{cols:d.cols,joinSep:d.joinSep,maxName:d.maxName,specialW:d.specialW,
          showPrev:d.showPrev,showStat:d.showStat,
          rows:d.rows.map(r=>({id:r.id,cells:r.cells||{},checked:r.checked!==false,wrap:r.wrap||{},
               ms:(r.ms&&(r.ms.status==='ok'||r.ms.status==='fail'))?r.ms:{status:'none',made:'',msg:''}}))}};
}
function applyMkState(g){
  const d=g&&g.mk, doc=makeMkDoc();
  if(d&&d.doc&&Array.isArray(d.doc.cols)&&d.doc.cols.length){
    const x=d.doc;
    doc.cols=x.cols; doc.joinSep=x.joinSep||'';
    doc.maxName=(x.maxName===undefined)?180:x.maxName;
    doc.specialW=Object.assign({},doc.specialW,x.specialW||{});
    ['showPrev','showStat'].forEach(k=>{ if(x[k]!==undefined) doc[k]=!!x[k]; });
    doc.rows=(x.rows||[]).map(r=>Object.assign(mkBlankRow(),r,{id:r.id||uid('r')}));
  }
  while(doc.rows.length<12) doc.rows.push(mkBlankRow());
  mkDest=(d&&d.dest)||'';
  mkDestManual=!!(mkDest&&DIR&&mkDest.toLowerCase()!==DIR.toLowerCase());
  if(!mkDest) mkDest=DIR;
  if(DOC==='mk'){ loadDoc(doc); render(); } else DOCS.mk=doc;
  mkSyncDest();
}
function updateMkStatus(finals,issues){
  const named=rows.filter(r=>r.file);
  const on=named.filter(rowOn);
  let nNew=0,nEx=0,nOk=0;
  on.forEach(r=>{ const k=mkState(r); if(k==='new') nNew++; else if(k==='exists') nEx++; else if(k==='ok') nOk++; });
  const bits=[`<b>${on.length}</b> 个文件夹名`,`待创建 <b>${nNew}</b>`];
  if(nEx) bits.push(`已存在 ${nEx}`);
  if(nOk) bits.push(`已创建 ${nOk}`);
  if(issues&&issues.size) bits.push(`<span class="warn">${issues.size} 行重名（只建一次）</span>`);
  const el=$('mksum'); if(el) el.innerHTML=bits.join(' · ');
  const go=$('mkGo'); if(go){ go.textContent=nNew?`创建 ${nNew} 个文件夹`:'创建文件夹'; }
}
function mkSyncDest(){
  const dd=$('mkdest'); if(!dd) return;
  const dest=mkDest||DIR;
  dd.textContent=dest; dd.title='建在：'+dest+'（点击更换）';
}
async function mkRefreshExisting(){
  const dest=mkDest||DIR; if(!dest) return;
  let d=null; try{ d=await post('/api/mklist',{dest}); }catch(e){ d=null; }
  mkExisting=new Set(((d&&d.names)||[]).map(n=>String(n).toLowerCase()));
  renderMkDoc();
}
function mkUseCurrentDir(){ mkDest=DIR; mkDestManual=false; mkSyncDest(); mkRefreshExisting(); scheduleSave(); }
async function pickMkFolder(){
  const d=await post('/api/pickdlfolder',{initial:mkDest||DIR});
  if(d&&d.ok&&d.dir){ mkDest=d.dir; mkDestManual=true; mkSyncDest(); mkRefreshExisting(); scheduleSave(); }
}
function mkOpenDest(){ post('/api/openpath',{path:mkDest||DIR}); }
function mkAddRows(n){
  switchDoc('mk'); if(DOC!=='mk') return;
  pushUndo(); for(let i=0;i<(n||5);i++) rows.push(mkBlankRow()); commit();
}
function mkClear(){
  switchDoc('mk'); if(DOC!=='mk') return;
  pushUndo();
  rows=[]; for(let i=0;i<12;i++) rows.push(mkBlankRow());
  sel={r1:0,c1:0,r2:0,c2:0}; active={r:0,c:0};
  commit(); toast('新建文件夹表格已清空（Ctrl+Z 可以撤销）');
}
function mkHide(){
  mkOpen=false;
  $('mkpane').style.display='none';
  const b=$('btnMk'); if(b) b.classList.remove('on');
  if(DOC==='mk') switchDoc('main');
}
function toggleMk(force, fromUser){
  mkOpen=(force===undefined)?!mkOpen:!!force;
  if(mkOpen&&dlOpen){
    dlOpen=false; $('dlpane').style.display='none';
    const bd=$('btnDl'); if(bd) bd.classList.remove('on');
    if(DOC==='dl') switchDoc('main');
  }
  $('mkpane').style.display=mkOpen?'flex':'none';
  $('splitter').style.display=(mkOpen||dlOpen)?'block':'none';
  const b=$('btnMk'); if(b) b.classList.toggle('on',mkOpen);
  if(mkOpen){
    if(!mkDest) mkDest=DIR;
    if(dlH>0) $('mkpane').style.height=dlH+'px';
    mkSyncDest(); renderMkDoc(); mkRefreshExisting();
  }
  if(fromUser){
    if(mkOpen){
      switchDoc('mk');
      if(DOC==='mk'){
        let r=rows.findIndex(x=>!Object.values(x.cells||{}).some(v=>String(v).trim())); if(r<0) r=0;
        wrap.focus(); setActive(r,0,false);
      }
    } else if(DOC==='mk') switchDoc('main');
  }
  refitAll(); scheduleSave();
}
async function mkStart(){
  if(mkBusy) return;
  if(!mkOpen) toggleMk(true);
  switchDoc('mk'); if(DOC!=='mk'){ toast('改名表正在执行操作，稍后再试'); return; }
  if(editing) closeEditor(true);
  await mkRefreshExisting();
  switchDoc('mk'); if(DOC!=='mk') return;
  rows.forEach(mkSyncFile);
  const items=[], exists=[], dups=[], empty=[], bad=[], seen={};
  rows.forEach((r,i)=>{
    if(!r.file||!rowOn(r)) return;
    const n=mkNameOf(r);
    if(!n){ empty.push(i+1); return; }
    if(mkBadReason(n)){ bad.push(`第 ${i+1} 行「${n}」：${mkBadReason(n)}`); return; }
    const k=n.toLowerCase();
    if(seen[k]){ dups.push(`第 ${i+1} 行「${n}」和第 ${seen[k]} 行重名`); return; }
    seen[k]=i+1;
    if(mkExisting.has(k)){ exists.push(n); return; }
    items.push({key:r.id,name:n,row:i+1});
  });
  const dest=mkDest||DIR;
  if(!items.length){
    info('没有要新建的文件夹',
      (exists.length?`<p>勾选的 ${exists.length} 个名字在「${esc(dest)}」里都已经存在了。</p>`:'<p>先在左边的列里写上文件夹名（每行一个），并勾选左边的复选框。</p>')
      +listBlock('已存在（跳过）', exists, 60)+listBlock('表格里重名', dups, 40)+listBlock('名字不能用', bad, 40));
    return;
  }
  let html='<div class="mk-dlg">';
  html+=`<div class="mk-dest"><span class="fic big"></span><div><div class="k">建在</div><div class="v">${esc(dest)}</div></div></div>`;
  html+='<div class="sum">';
  html+=sumRow('ok','将新建', items.length);
  html+=sumRow(exists.length?'':'mut','已存在（跳过，不会动它）', exists.length);
  html+=sumRow(dups.length?'warn':'mut','表格里重名（只建一次）', dups.length);
  if(bad.length) html+=sumRow('warn','名字不能用（跳过）', bad.length);
  html+='</div>';
  html+='<div class="mk-chips">'+items.slice(0,24).map(it=>`<span class="mk-chip"><span class="fic"></span>${esc(it.name)}</span>`).join('')+
        (items.length>24?`<span class="mk-more">…还有 ${items.length-24} 个</span>`:'')+'</div>';
  html+=listBlock('已存在（跳过）', exists, 60)+listBlock('表格里重名', dups, 40)+listBlock('名字不能用（跳过）', bad, 40);
  html+='<div class="hint">只会新建文件夹，不会移动、覆盖或删除任何已有的东西。建完如果发现不对，可以点「撤销刚才的创建」（只删还是空的文件夹）。</div></div>';
  dialog('新建文件夹', html, ()=>{ mkDo(dest,items); }, `创建 ${items.length} 个文件夹`);
}
async function mkDo(dest,items){
  mkBusy=true;
  let d=null;
  try{ d=await post('/api/mkdirs',{dest,items:items.map(it=>({key:it.key,name:it.name}))}); }catch(e){ d=null; }
  mkBusy=false;
  if(!d||d.ok===false){ info('没能新建文件夹','<p>'+esc((d&&d.message)||'出错了')+'</p>'); return; }
  const map={}; (d.results||[]).forEach(x=>map[x.key]=x);
  let ok=0, ex=0, fail=0; const made=[];
  mkRowsRef().forEach(r=>{
    const x=map[r.id]; if(!x) return;
    if(x.status==='ok'){ ok++; made.push(x.name); r.ms={status:'ok',made:x.name,msg:''}; }
    else if(x.status==='exists'){ ex++; r.ms={status:'none',made:'',msg:''}; }
    else { fail++; r.ms={status:'fail',made:x.name||'',msg:x.reason||''}; }
  });
  mkLast=made.length?{dest,names:made}:null;
  const ub=$('mkUndoBtn'); if(ub) ub.style.display=mkLast?'inline-block':'none';
  $('mkstat').innerHTML=`刚才：新建 <b>${ok}</b> 个`+(ex?` · 已存在 ${ex}`:'')+(fail?` · <span class="warn">失败 ${fail}</span>`:'');
  await mkRefreshExisting();
  scheduleSave();
  toast(fail?`新建了 ${ok} 个文件夹，${fail} 个失败（原因写在「状态」列）`:`已新建 ${ok} 个文件夹`);
  if(ok && (dest||'').toLowerCase()===(DIR||'').toLowerCase() && includeDirs) refreshMainQuiet();
}
function mkUndo(){
  if(!mkLast||!mkLast.names.length) return;
  const L=mkLast;
  dialog('撤销刚才的创建',
    `<p>删除刚才在「${esc(L.dest)}」里新建的 <b>${L.names.length}</b> 个文件夹？</p>
     <div class="hint">只会删除<b>仍然是空的</b>文件夹；如果你已经往里面放了东西，那个文件夹会保留。</div>`
     +listBlock('刚才新建的', L.names, 60),
    async ()=>{
      const d=await post('/api/mkundo',{dest:L.dest,names:L.names});
      if(!d||d.ok===false){ info('撤销失败','<p>'+esc((d&&d.message)||'出错了')+'</p>'); return; }
      const gone=new Set((d.removed||[]).map(n=>n.toLowerCase()));
      mkRowsRef().forEach(r=>{ if(r.ms&&r.ms.status==='ok'&&gone.has(String(r.ms.made).toLowerCase())) r.ms={status:'none',made:'',msg:''}; });
      mkLast=(d.kept&&d.kept.length)?{dest:L.dest,names:d.kept}:null;
      const ub=$('mkUndoBtn'); if(ub) ub.style.display=mkLast?'inline-block':'none';
      $('mkstat').textContent=`已撤销：删除 ${d.removed.length} 个空文件夹`+(d.kept.length?`，${d.kept.length} 个里面有东西已保留`:'');
      await mkRefreshExisting(); scheduleSave();
      toast(`已删除 ${d.removed.length} 个刚建的空文件夹`+(d.kept.length?`，${d.kept.length} 个不是空的，保留了`:''));
    }, '撤销');
}
window.addEventListener('focus',()=>{ if(mkOpen) mkRefreshExisting(); });
let upTimer=null;
function hideUpbar(){
  $('upbar').style.display='none';
  $('uplog').style.display='none';
}
function toggleUpLog(){
  const el=$('uplog');
  el.style.display = el.style.display==='block'?'none':'block';
}
function watchUpload(){
  closeDlg();
  $('upbar').style.display='flex';
  $('upbar').classList.remove('fail');
  $('upstat').textContent='正在上传…';
  $('upcur').textContent='';
  const tick=async()=>{
    const s=await post('/api/uploadstatus');
    applyUploadResults(s.results||[]);
    const logEl=$('uplog'), st=$('upstat'), cur=$('upcur');
    if(!st){ clearInterval(upTimer); return; }
    if(logEl){ logEl.textContent=(s.log||[]).join('\n'); logEl.scrollTop=logEl.scrollHeight; }
    if(s.running){
      st.innerHTML=`上传中　成功 ${s.ok} / ${s.plan}　失败 ${s.fail}`;
      if(cur) cur.textContent=s.current||'';
    } else if(s.done){
      clearInterval(upTimer); upTimer=null;
      applyUploadResults(s.results||[]);
      const failN=+(s.fail||0);
      st.innerHTML=`完成：成功 <b>${s.ok}</b> / ${s.plan}　失败 <b>${failN}</b>`;
      if(cur) cur.textContent=s.message||'';
      $('upbar').classList.toggle('fail', failN>0);
      toast(failN?('上传结束，失败 '+failN+' 个已保持勾选，可点「只重试失败」'):('上传完成，成功 '+s.ok+' 个'));
    }
  };
  upTimer=setInterval(tick,800); tick();
}

let lastJump='', jumpIgnore='', pollBusy=false, pollLast=0;
function hideJump(){ jumpIgnore=$('jumpPath').textContent||''; $('jumpbar').style.display='none'; }
async function pollPaths(){
  /* 只在「切回本窗口」时探测路径。双击编辑单元格不会触发 visibilitychange，
     避免再去拉资源管理器、弹出命令行窗口。 */
  if(editor && editor.style.display==='block') return;
  if(pollBusy) return;
  const now=Date.now();
  if(now-pollLast<1500) return;
  pollBusy=true; pollLast=now;
  try{
    const d=await post('/api/paths');
    const list=d.explorer||[];
    let p=list.filter(x=>x && x.toLowerCase()!==DIR.toLowerCase()).pop() || '';
    if(!p && d.clipboard && d.clipboard.toLowerCase()!==DIR.toLowerCase()) p=d.clipboard;
    if(p && p!==lastJump && p!==jumpIgnore){
      lastJump=p;
      $('jumpPath').textContent=p;
      $('jumpbar').style.display='flex';
    }
  }catch(e){}
  finally{ pollBusy=false; }
}
function jumpToDetected(){
  const p=$('jumpPath').textContent; if(!p) return;
  switchToDir(p);
}
async function switchToDir(p){
  await post('/api/savegrid',{grid:gridState()});
  const d=await post('/api/opendir',{dir:p,includeDirs,videosOnly});
  if(d.ok===false){ info('打不开这个文件夹','<p>'+esc(d.message)+'</p>'); return; }
  loadFromPayload(d);
  $('jumpbar').style.display='none';
  toast('已切换到 '+DIR);
}
function loadFromPayload(d){
  applyInit(d);
  const g=migrate(d.grid);
  if(g&&g.cols.length){
    cols=g.cols; joinSep=g.joinSep||''; applyDlState(g); applyMkState(g);
    if(g.keepExt!==undefined) keepExt=g.keepExt;
    extMode=g.extMode||(g.keepExt===false?'none':'keep');
    if(g.extSet!==undefined) extSet=g.extSet;
    if(g.extFill!==undefined) extFill=!!g.extFill;
    if(g.maxName!==undefined) maxName=g.maxName;
    rows=(g.rows||[]).map(r=>({id:r.id||uid('r'),file:r.file,kind:r.kind||(r.file?'file':null),cells:r.cells||{},
                               checked:r.checked!==false,link:r.link||'',wrap:r.wrap||{},
                               up:r.up||{status:'none'}}));
    if(g.specialW) specialW=g.specialW;
    else {
      cols.forEach(c=>{ if((c.w||0)>=260) c.w=160; });
      specialW={porig:180,pnew:180,link:180,stat:150};
    }
    if(d.prefs) applyPrefs(d.prefs);
    mergeFiles(d.files, d.entries, d.dirNames);
  } else {
    cols=[{id:uid('c'),name:'文件名',w:(d.prefs&&d.prefs.dataColW)||160,type:'data'}];
    rows=(d.entries||d.files.map(n=>({name:n,kind:'file'}))).map(e=>newRow(e.name||e, e.kind||'file'));
    if(!rows.length) rows.push({id:uid('r'),file:null,cells:{},checked:true,link:'',wrap:{}});
  }
  lastSaved=d.updated||'';
  rebuildV(); buildMenus(); render(); markSaved();
  wrap.focus(); setActive(0,0,false);
  syncDirToggle();
}
function hideMore(){ const m=$('moremenu'); if(m) m.style.display='none'; }
function toggleMore(e){
  if(e) e.stopPropagation();
  const m=$('moremenu'); if(!m) return;
  m.style.display = m.style.display==='block'?'none':'block';
}
function openFolderDlg(){
  dialog('打开文件夹',`
    <p>去资源管理器点开目标文件夹，再点回本窗口，路径会自动出现在下面。也可以直接粘贴或浏览。</p>
    <div class="row"><label>文件夹</label><input type="text" id="d_dir" value="${escA(DIR)}"></div>
    <div class="hint" id="d_exp">正在检测资源管理器…</div>
    <div id="d_exlist"></div>`,
    ()=>{ const p=$('d_dir').value.trim().replace(/^"|"$/g,''); if(!p) return false; switchToDir(p); },
    '打开');
  const refresh=async()=>{
    const d=await post('/api/paths');
    const box=$('d_exlist'); if(!box) return;
    const items=[];
    (d.explorer||[]).forEach(p=>items.push(p));
    if(d.clipboard && items.indexOf(d.clipboard)<0) items.unshift(d.clipboard);
    $('d_exp').textContent=items.length?'点下面一行即可填入（来自当前资源管理器 / 剪贴板）：':'没有检测到打开的资源管理器，也可以直接粘贴路径。';
    box.innerHTML=items.map(p=>`<div class="mi" style="padding:6px 8px;cursor:pointer" onclick="document.getElementById('d_dir').value=this.dataset.p" data-p="${escA(p)}">${esc(p)}</div>`).join('')
      + `<div class="mi" style="padding:6px 8px;cursor:pointer;color:var(--blue)" onclick="browseNativeFolder()">… 浏览电脑上的文件夹</div>`;
    if(items.length && items[items.length-1].toLowerCase()!==DIR.toLowerCase())
      $('d_dir').value=items[items.length-1];
  };
  refresh();
  const t=setInterval(()=>{ if($('mask').style.display!=='flex'){ clearInterval(t); return; } refresh(); },900);
}
async function browseNativeFolder(){
  await post('/api/savegrid',{grid:gridState()});
  const d=await post('/api/browsefolder',{initial:DIR,includeDirs,videosOnly});
  if(d.cancelled) return;
  if(d.ok===false){ info('打不开','<p>'+esc(d.message)+'</p>'); return; }
  closeDlg();
  loadFromPayload(d);
  toast('已切换到 '+DIR);
}
async function exportProject(){
  const d=await post('/api/export',{grid:gridState()});
  if(d.cancelled) return;
  if(d.ok===false){ info('导出失败','<p>'+esc(d.message)+'</p>'); return; }
  toast('工程已导出：'+d.path);
  info('工程已导出',`<p>已保存到：</p><pre class="log">${esc(d.path)}</pre>
    <div class="hint">以后视频挪了位置，用「文件 → 导入工程文件」，再指定视频现在所在的文件夹，就能按这份记录还原文件名。</div>`);
}
let pendingImportPath='';
async function importProject(dirHint){
  const d=await post('/api/import',{dir:dirHint||'',path:pendingImportPath||''});
  if(d.cancelled) return;
  if(d.path) pendingImportPath=d.path;
  if(d.need_folder){
    dialog('工程文件里的原文件夹已经不在了',
      `<p>${esc(d.message||'')}</p>
       <p>请指定视频<b>现在</b>所在的文件夹。工具会按文件名、原名或大小自动对上。</p>
       ${d.recorded?`<div class="hint">工程里记录的原位置：${esc(d.recorded)}</div>`:''}
       <div class="row"><label>当前文件夹</label><input type="text" id="d_dir" value=""></div>
       <div class="hint">也可以先去资源管理器打开那个文件夹，再点回这里，路径会自动填。</div>`,
      ()=>{
        const p=$('d_dir').value.trim(); if(!p) return false;
        importProjectWithDir(p);
      },'用这个文件夹导入');
    const t=setInterval(async()=>{
      if($('mask').style.display!=='flex'){ clearInterval(t); return; }
      const pth=await post('/api/paths');
      const list=pth.explorer||[];
      const pick=list[list.length-1]||pth.clipboard;
      if(pick && $('d_dir') && !$('d_dir').value) $('d_dir').value=pick;
    },900);
    return;
  }
  if(d.ok===false){ info('导入失败','<p>'+esc(d.message)+'</p>'); return; }
  finishImport(d);
}
async function importProjectWithDir(dir){
  closeDlg();
  const d=await post('/api/import',{dir:dir,path:pendingImportPath||''});
  if(d.path) pendingImportPath=d.path;
  if(d.need_folder){ importProject(dir); return; }
  if(d.ok===false){ info('导入失败','<p>'+esc(d.message)+'</p>'); return; }
  pendingImportPath='';
  finishImport(d);
}
function finishImport(d){
  loadFromPayload(d);
  let html=`<p>已导入工程。当前文件夹：<b>${esc(DIR)}</b></p>`;
  if(d.missing&&d.missing.length)
    html+=`<p>有 <b>${d.missing.length}</b> 条在这个文件夹里没对上文件（会显示为空行）：</p>
           <pre class="log">${esc(d.missing.join('\n'))}</pre>
           <div class="hint">确认视频都在这个文件夹后再点「重新载入」。对上之后可用「还原到最初的文件名」。</div>`;
  else html+=`<div class="hint">文件已按工程记录对上。需要的话点「文件 → 还原到最初的文件名」。</div>`;
  info('导入完成',html);
}
document.addEventListener('visibilitychange',()=>{
  if(document.visibilityState==='visible') pollPaths();
});

/* =======================================================================
   菜单栏
   ======================================================================= */
const copySel=()=>{ wrap.focus(); document.execCommand('copy'); };
const cutSel =()=>{ wrap.focus(); document.execCommand('cut'); };
const MENUS={
 file:[
  ['📂 打开文件夹…','',()=>openFolderDlg()],
  ['📁 批量新建文件夹…','Ctrl+Shift+N',()=>toggleMk(true,true)],
  ['⟳ 重新载入文件列表','',()=>reloadFiles()],
  '-',
  ['📤 导出工程文件…','',()=>exportProject()],
  ['📥 导入工程文件…','',()=>{ pendingImportPath=''; importProject(); }],
  '-',
  ['🚀 执行重命名','Ctrl+Enter',()=>execRename()],
  ['↩ 撤销上次重命名','',()=>undoRename(),'undo'],
  '-',
  ['⏮ 回到上一次成功的名字','',()=>restoreLastSnap()],
  ['🕘 改名时间点…','',()=>showSnapshots()],
  ['还原到最初的文件名','',()=>restoreOriginal(),'restore'],
  ['📄 查看工程文件信息','',()=>showProject()],
  '-',
  ['✖ 退出','',()=>post('/api/quit').then(()=>{
      document.body.innerHTML='<div style="margin:auto;color:#8a9099;font-size:15px">已退出，工程文件已保存。可以关闭此窗口了。</div>';
      document.body.style.alignItems='center'; document.body.style.justifyContent='center';
      window.close(); })],
 ],
 edit:[
  ['撤销','Ctrl+Z',()=>gridUndo()],
  ['重做','Ctrl+Y',()=>gridRedo()],
  '-',
  ['复制','Ctrl+C',copySel],
  ['剪切','Ctrl+X',cutSel],
  ['复制选中文件名','Ctrl+Shift+C',()=>copyFileNames()],
  ['复制选中文件路径','Ctrl+Shift+P',()=>copyFilePaths()],
  ['清除选中内容','Delete',()=>clearSel()],
  ['全选','Ctrl+A',()=>{ sel={r1:0,c1:0,r2:rows.length-1,c2:vcols.length-1}; paintSel(); }],
  '-',
  ['勾选选中行','',()=>checkSel(true)],
  ['取消勾选选中行','',()=>checkSel(false)],
  ['向下填充','Ctrl+D',()=>fillDown()],
 ],
 insert:[
  ['在选中列左侧插入列','',()=>insertCol(selRange().c1)],
  ['在选中列右侧插入列','',()=>insertCol(dataEnd(selRange().c2)+1)],
  ['插入一列并填入原文件名','',()=>insertCol(dataEnd(selRange().c2)+1,'stem')],
  ['删除选中列','',()=>deleteCols()],
  '-',
  ['在选中行上方插入行','',()=>insertRow(selRange().r1)],
  ['在选中行下方插入行','',()=>insertRow(selRange().r2+1)],
  ['删除选中行','Delete',()=>deleteRows()],
  ['删除所有空行','',()=>removeBlank()],
 ],
 tools:[
  ['🔢 填入序号…','',()=>tool_number()],
  ['📛 整列填充固定文字…','',()=>tool_fillText()],
  ['🔍 查找和替换…','Ctrl+F',()=>openFind()],
  ['📏 字符长度上限…','',()=>tool_maxlen(),'maxlen'],
  ['🔍 查找替换（旧版弹窗）…','',()=>tool_replace()],
  ['➕ 给某列加前缀 / 后缀…','',()=>tool_affix()],
  ['🏷 批量设置扩展名…','',()=>openExtDlg()],
  ['Aa 大小写与空白…','',()=>tool_case()],
  '-',
  ['⚙ 列之间的连接符…','',()=>tool_joinSep(),'sep'],
 ],
 upload:[
  ['☁ 上传勾选且有链接的文件…','',()=>startUpload(false)],
  ['↻ 只重试失败的上传…','',()=>startUpload(true)],
  ['⟲ 重新上传勾选里已传过的文件…','',()=>startUpload('redo')],
  '-',
  ['⬇ 网盘下载面板','Ctrl+Shift+D',()=>toggleDownload(undefined,true)],
  ['📁 批量新建文件夹面板','Ctrl+Shift+N',()=>toggleMk(undefined,true)],
  ['📂 在资源管理器中显示当前文件','',()=>revealSel()],
  ['▶ 打开当前文件','',()=>openSelFile()],
 ],
 view:[
  ['#PREV#','',()=>{ showPrev=!showPrev; rebuildV();
      active.c=clamp(active.c,0,vcols.length-1); sel.c1=clamp(sel.c1,0,vcols.length-1);
      sel.c2=clamp(sel.c2,0,vcols.length-1); buildMenus(); commit(); }],
  ['#LINK#','',()=>{ showLink=!showLink; rebuildV();
      active.c=clamp(active.c,0,vcols.length-1); sel.c1=clamp(sel.c1,0,vcols.length-1);
      sel.c2=clamp(sel.c2,0,vcols.length-1); buildMenus(); commit(); }],
  ['#STAT#','',()=>{ showStat=!showStat; rebuildV();
      active.c=clamp(active.c,0,vcols.length-1); sel.c1=clamp(sel.c1,0,vcols.length-1);
      sel.c2=clamp(sel.c2,0,vcols.length-1); buildMenus(); commit(); }],
  ['#EXT#','',()=>openExtDlg()],
  ['#DIRS#','',()=>toggleIncludeDirs(!includeDirs)],
  ['#VID#','',()=>toggleVideosOnly(!videosOnly)],
  ['#THEME#','Ctrl+Shift+L',()=>toggleTheme()],
  '-',
  ['选中单元格截断显示','',()=>setWrapRange(false)],
  ['选中单元格自动换行','',()=>setWrapRange(true)],
  ['全部截断','',()=>setWrapAll(false)],
  ['全部换行','',()=>setWrapAll(true)],
  ['恢复默认列宽','',()=>resetColWidths()],
 ],
 settings:[
  ['#DRIVE#','',()=>openDriveSetup()],
  ['#THEME#','Ctrl+Shift+L',()=>toggleTheme()],
 ],
 help:[
  ['⌨ 快捷键与用法','',()=>showHelp()],
 ],
};
/* 这些操作只属于改名表（本地文件）：从哪里调用都先切回改名表，异步期间不许切走 */
function mainOnly(names){
  names.forEach(n=>{
    const f=window[n]; if(typeof f!=='function'||f._mainOnly) return;
    const g=function(...a){
      switchDoc('main');
      docLockN++;
      let r;
      try{ r=f.apply(this,a); }
      catch(e){ docLockN--; throw e; }
      if(r&&typeof r.then==='function') return r.finally(()=>{ docLockN=Math.max(0,docLockN-1); });
      docLockN=Math.max(0,docLockN-1);
      return r;
    };
    g._mainOnly=true; window[n]=g;
  });
}
function buildMenus(){
  if(docQuiet) return;
  document.querySelectorAll('.menu').forEach(m=>{
    const dd=m.querySelector('.dropdown'); dd.innerHTML='';
    (MENUS[m.dataset.menu]||[]).forEach(it=>{
      if(it==='-'){ const s=document.createElement('div'); s.className='msep'; dd.appendChild(s); return; }
      let label=it[0], key=it[1]||'', off=false;
      if(label==='#PREV#') label=(showPrev?'☑':'☐')+' 显示预览对照列';
      if(label==='#LINK#') label=(showLink?'☑':'☐')+' 显示网盘链接列';
      if(label==='#STAT#') label=(showStat?'☑':'☐')+' 显示上传状态列';
      if(label==='#EXT#')  label='🏷 扩展名：'+(extMode==='set'?('统一用 '+(normExt(extSet)||'?')+(extFill?'（只补缺）':'（全部换）'))
                                   :extMode==='none'?'不带扩展名':'沿用原后缀')+' …';
      if(label==='#DIRS#') label=(includeDirs?'☑':'☐')+' 包含文件夹（仅改名，不上传）';
      if(label==='#VID#') label=(videosOnly?'☑':'☐')+' 只显示视频';
      if(label==='#THEME#') label=(theme==='dark'?'☀ 切换到亮色主题':'🌙 切换到暗色主题');
      if(label==='#DRIVE#'){ label='☁ Google Drive 网盘连接…'; key=hasGdrive?'已连接':'未连接'; }
      if(it[3]==='restore'){ key=restorable?(restorable+' 个可还原'):'无记录'; off=!restorable; }
      if(it[3]==='undo') off=!undoCount;
      if(it[3]==='sep') key=joinSep?`当前「${joinSep}」`:'当前：直接拼接';
      if(it[3]==='maxlen'){
        const nc=cols.filter(c=>c.max>0).length;
        key=(maxName?`总长 ${maxName}`:'总长不限')+(nc?` · ${nc} 列有上限`:'');
      }
      const el=document.createElement('div');
      el.className='mi'+(off?' disabled':'');
      el.innerHTML=esc(label)+'<span class="key">'+esc(key)+'</span>';
      el.onclick=()=>{ closeMenus(); it[2](); };
      dd.appendChild(el);
    });
  });
}
function closeMenus(){ document.querySelectorAll('.menu.open').forEach(m=>m.classList.remove('open')); }
document.querySelectorAll('.menu').forEach(m=>{
  m.querySelector('.mtitle').addEventListener('mousedown',e=>{
    e.preventDefault(); const was=m.classList.contains('open'); closeMenus(); if(!was) m.classList.add('open');
  });
  m.addEventListener('mouseenter',()=>{ if(document.querySelector('.menu.open')){ closeMenus(); m.classList.add('open'); } });
});
document.addEventListener('mousedown',e=>{
  if(!e.target.closest('.menu')) closeMenus();
  if(!e.target.closest('.morewrap')) hideMore();
});

function showHelp(){
  info('快捷键与用法',`
  <pre class="log">【核心概念】
  一行 = 一个文件名。所有白色「编辑列」从左到右直接拼接，就是新文件名。
  最左边的复选框：勾选的行才会被改名 / 填序号 / 上传，没勾选的完全不动。
  「包含文件夹」打开后，当前目录下的子文件夹会出现在表格里，可以一起改名。
  上传永远只传文件，文件夹即使勾选、即使填了链接也不会上传。
  点列顶的复选框 = 全选或全不选；按住左键在复选框列上拖 = 连续勾选或取消。
  空格键 = 勾选/取消当前选中的行。
  右侧「预览：原文件名」后面是「网盘链接」列，不参与拼接文件名。
  每一行自己填写（或粘贴）该视频要传到的文件夹网址。
  上传只认本表：这一行的文件 → 这一行的链接。软件会把对应关系写进工程文件和「上传状态」列。

  例：  [美-5] [v01] [rename01]   →   美-5v01rename01.jpeg

【表格操作】
  双击 / F2 / Enter            编辑单元格
  直接打字                      覆盖输入
  方向键 / Tab                  移动
  Shift + 方向键 / 点击          扩展选区
  Ctrl + 方向键                 跳到边缘
  Ctrl + C / X / V              复制 / 剪切 / 粘贴（支持多行多列）
  Ctrl + 鼠标左键点网盘链接      在浏览器中打开这个链接
  Ctrl + Shift + D              打开 / 收起网盘下载面板
  Ctrl + Shift + N              打开 / 收起批量新建文件夹面板（每行 = 一个文件夹）
  设置 → Google Drive 网盘连接   一键连接你自己的网盘（上传、下载都用它）
  Ctrl + Shift + C              复制选中行的文件名
  Ctrl + Shift + P              复制选中行的完整路径
  Ctrl + Z / Y                  撤销 / 重做表格编辑
  Ctrl + D                      向下填充
  Ctrl + F / Ctrl + H           打开查找替换面板
  Delete                        清除选中内容
  空格                          勾选 / 取消勾选选中行
  拖选区右下角小方块             智能填充（结尾数字自动递增）
  Ctrl + 拖那个小方块           原样复制填充，数字不递增
  拖列标题                      移动整列
  拖列标题右边缘                调整列宽（打开时列宽固定，长文件名默认截断）
  工具栏「截断 / 换行」         把选中单元格在截断与换行之间切换
  「全部截断 / 全部换行」       一键切换整张表
  鼠标悬停单元格                显示完整内容；双击进入编辑看全文
  拖左侧行号                    移动整行
  右键                          完整的行 / 列 / 单元格菜单
  Ctrl + Enter                  执行重命名（只改勾选的行）

【打开文件夹 · 自动跳转】
  把文件夹拖进窗口即可打开。也可以去资源管理器点开目标文件夹，
  再点回本窗口，顶部蓝条一点就能跳转。
  双击「预览：原文件名」会播放/打开该文件；双击文件夹行会打开那个文件夹。
  默认只显示视频，工具栏「只显示视频」可关掉以看到封面和日志。
  窗口大小、列宽、包含文件夹、链接列、只显示视频会记住，换文件夹不用重设。
  改名成功后，左下角会出现「撤销刚才 N 个」。

【改名前预检】
  点「执行重命名」会先列出：将改几条、冲突几条、未勾选、文件夹、
  没有网盘链接、改完能上传几条。有冲突时不能执行。

【上传】
  在「网盘链接」列填入该行视频要传到的文件夹网址，勾选后点「上传勾选」。
  传到哪里只由本表这一行决定，不读取任何外部表格。
  「上传状态」列会写回：未传 / 上传中 / 已传（文件ID）/ 失败原因，并保存在工程文件里。
  成功的行会取消勾选；失败的行保持勾选，可「只重试失败」。
  文件夹永远不上传。rclone 仍用本机已配置的 gdrive 远程。

【工程文件】
  每次成功改名都会在 .renamer_project.json 里留下一个时间点。
  「文件 → 回到上一次成功的名字」回到上一轮，不必回到最开始。
  「文件 → 改名时间点」可以点选任意一轮。
  导出 / 导入工程文件后，视频挪位置也能按记录还原。</pre>`);
}

let dropDepth=0;
function showDrop(on){ const m=$('dropmask'); if(m) m.classList.toggle('on', !!on); }
document.addEventListener('dragenter',e=>{ e.preventDefault(); dropDepth++; showDrop(true); });
document.addEventListener('dragover',e=>{ e.preventDefault(); try{ e.dataTransfer.dropEffect='copy'; }catch(err){} });
document.addEventListener('dragleave',()=>{ dropDepth=Math.max(0,dropDepth-1); if(!dropDepth) showDrop(false); });
document.addEventListener('drop',e=>{
  e.preventDefault(); dropDepth=0; showDrop(false);
  let p='';
  try{
    if(e.dataTransfer.files && e.dataTransfer.files.length)
      p=e.dataTransfer.files[0].path || '';
  }catch(err){}
  if(!p){
    try{ p=(e.dataTransfer.getData('text/plain')||'').trim().replace(/^"|"$/g,''); }catch(err){}
  }
  if(!p){ toast('没有读到本地路径。请把文件夹拖进这个窗口（不要拖到浏览器标签上）。'); return; }
  post('/api/droppath',{path:p,includeDirs,videosOnly}).then(d=>{
    if(d.ok===false){ info('打不开','<p>'+esc(d.message)+'</p>'); return; }
    loadFromPayload(d);
    toast('已打开 '+DIR);
  });
});

mainOnly(['execRename','undoRename','reloadFiles','toggleIncludeDirs','toggleVideosOnly',
          'restoreOriginal','restoreLastSnap','confirmRestoreSnap','showSnapshots','showProject',
          'copyFilePaths','revealSel','openSelFile','startUpload','startRedoUpload',
          'exportProject','importProject','importProjectWithDir','openFolderDlg','browseNativeFolder',
          'switchToDir','jumpToDetected','loadFromPayload','renameChecklist']);
init();
</script>
</body>
</html>
"""


# ============================================================ 启动
def main():
    target = sys.argv[1] if len(sys.argv) > 1 else os.getcwd()
    target = os.path.abspath(target)
    if not os.path.isdir(target):
        target = os.getcwd()

    Handler.token = secrets.token_urlsafe(32)
    Handler.app = App(target)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = "http://127.0.0.1:%d/" % port

    try:
        import webview  # pywebview：原生窗口，体验最好
        prefs = load_prefs()
        win = webview.create_window(
            "%s — %s" % (APP_NAME, target), url,
            width=int(prefs.get("width") or 1320),
            height=int(prefs.get("height") or 860),
            min_size=(900, 560),
        )
        def _save_win():
            try:
                save_prefs({"width": int(win.width or 1320), "height": int(win.height or 860)})
            except Exception:
                pass
        try:
            win.events.closing += _save_win
        except Exception:
            pass
        webview.start()
        os._exit(0)
    except ImportError:
        webbrowser.open(url)
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
