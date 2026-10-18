# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Sidecar autosave next to the project file. Crash leaves it; clean save/exit deletes it."""
from __future__ import annotations

import json
import os
import time
import uuid
from datetime import datetime, timezone

from cutora.core.history import _load_app_settings
from cutora.core.log import get_logger
from cutora.core.paths import user_autosave_dir

logger = get_logger("cutora.core.autosave")

DEFAULT_MINUTES = 3
THROTTLE_SEC = 15.0
SETTING_MINUTES = "autosave_minutes"
_STAMP_NAME = "last_autosave.json"
_SKIP_COMMANDS = frozenset({
    "project.save", "project.save_as", "project.close", "project.new",
    "project.open", "project.info",
    "project.autosave.status", "project.autosave.restore",
})

_last_write = 0.0

# 这一次运行的身份。快照里记着是哪一次运行写的：别的会话留下的快照（崩了 / 没存就
# 关了）在第一次被覆盖之前先复制进备份 —— Q145 事故：Luca 加了一下午的标题只在
# 自动保存里，软件没了，重开工程后第一次自动保存就把它盖掉了。
SESSION_ID = uuid.uuid4().hex
BACKUP_KEEP = 50
_checked_dest: set = set()
_backed_up_dest: dict[str, str] = {}


def backup_dir() -> str:
    return os.path.join(user_autosave_dir(), "backups")


def _snapshot_session(dest: str):
    """快照是哪次运行写的（读不出来 / 老格式返回 None）。"""
    try:
        with open(dest, encoding="utf-8") as fh:
            data = json.load(fh)
        meta = (data or {}).get("_autosave") if isinstance(data, dict) else None
        return str((meta or {}).get("session") or "") or None
    except Exception:
        return None


def _stem_of(dest: str) -> str:
    stem = os.path.basename(dest)
    if stem.lower().endswith(".autosave.json"):
        stem = stem[: -len(".autosave.json")]
    return stem


def preserve_foreign_snapshot(dest: str) -> str | None:
    """dest 是上一次（别的）会话留下的快照 → 先复制一份进备份目录（并在工程同目录保留一份带时间戳的增量备份，绝不覆盖）。返回备份路径。

    同一个会话里重复调用会返回已生成的备份路径（不会第二次返回 None）；这次运行自己写的不备份。"""
    key = os.path.normcase(os.path.abspath(dest))
    if key in _checked_dest:
        cached = _backed_up_dest.get(key)
        return cached if (cached and os.path.isfile(cached)) else None
    if not os.path.isfile(dest):
        return None
    if _snapshot_session(dest) == SESSION_ID:
        _checked_dest.add(key)
        return None
    _checked_dest.add(key)
    import shutil
    stem = _stem_of(dest)
    folder = backup_dir()
    try:
        os.makedirs(folder, exist_ok=True)
        stamp = datetime.fromtimestamp(os.path.getmtime(dest)).strftime("%Y%m%d-%H%M%S")
        out = os.path.join(folder, "%s.%s.autosave.json" % (stem, stamp))
        n = 1
        while os.path.exists(out):
            n += 1
            out = os.path.join(folder, "%s.%s-%d.autosave.json" % (stem, stamp, n))
        shutil.copy2(dest, out)
        _backed_up_dest[key] = out
        logger.info("autosave: 上次会话留下的快照已备份 %s", out)
    except OSError:
        logger.exception("autosave backup failed: %s", dest)
        return None
    # 同时在工程所在目录保留一份带时间戳的崩溃备份（新增而不覆盖），防止原 .autosave.json 被新会话覆盖后找不到
    try:
        dest_dir = os.path.dirname(os.path.abspath(dest))
        if dest_dir and os.path.isdir(dest_dir) and os.path.normcase(dest_dir) != os.path.normcase(os.path.abspath(user_autosave_dir())):
            local_bak = os.path.join(dest_dir, "%s.autosave.%s.json" % (stem, stamp))
            ln = 1
            while os.path.exists(local_bak):
                # 若已存在同时间戳且内容大小一致的备份则无需重复复制
                try:
                    if os.path.getsize(local_bak) == os.path.getsize(dest):
                        local_bak = None
                        break
                except OSError:
                    pass
                ln += 1
                local_bak = os.path.join(dest_dir, "%s.autosave.%s-%d.json" % (stem, stamp, ln))
            if local_bak:
                shutil.copy2(dest, local_bak)
    except OSError:
        pass
    try:
        mine = sorted(f for f in os.listdir(folder)
                      if f.startswith(stem + ".") and f.endswith(".autosave.json"))
        for old in mine[:-BACKUP_KEEP]:
            os.remove(os.path.join(folder, old))
    except OSError:
        pass
    return out


def list_backups(project_path: str | None = None) -> list[str]:
    """备份目录里的快照，新的在前。给了工程就只列这个工程的。"""
    folder = backup_dir()
    if not os.path.isdir(folder):
        return []
    stem = _stem_of(sidecar_path(project_path)) if project_path else None
    out = []
    for f in os.listdir(folder):
        if not f.endswith(".autosave.json"):
            continue
        if stem and not f.startswith(stem + "."):
            continue
        out.append(os.path.join(folder, f))
    out.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return out


def interval_minutes() -> int:
    s = _load_app_settings() or {}
    try:
        n = int(s.get(SETTING_MINUTES) if s.get(SETTING_MINUTES) not in (None, "") else DEFAULT_MINUTES)
    except (TypeError, ValueError):
        n = DEFAULT_MINUTES
    return max(1, min(60, n))


def sidecar_path(project_path: str) -> str:
    path = os.path.abspath(str(project_path or ""))
    lower = path.lower()
    for ext in (".asproj.json", ".asproj", ".cutora"):
        if lower.endswith(ext):
            return path[: -len(ext)] + ".autosave.json"
    root, _ext = os.path.splitext(path)
    return root + ".autosave.json"


def _pointer_path() -> str:
    return os.path.join(user_autosave_dir(), _STAMP_NAME)


# Q154：还没保存过（没有工程路径）的工程也要自动保存 —— 快照写到用户数据目录下的
# untitled.autosave.json。以前「未自动保存」就真的一个字都没落盘，监视器里改了几十条再崩全没了。
UNTITLED_NAME = "untitled.asproj.json"


def untitled_project_path() -> str:
    return os.path.join(user_autosave_dir(), UNTITLED_NAME)


def is_untitled(project_path) -> bool:
    try:
        return bool(project_path) and os.path.normcase(os.path.abspath(str(project_path))) == \
            os.path.normcase(os.path.abspath(untitled_project_path()))
    except Exception:
        return False


def effective_project_path(project_path) -> str:
    """有工程路径用它，没有就用未命名工程那一份。"""
    return str(project_path) if project_path else untitled_project_path()


def write_snapshot(project_path: str, data: dict) -> str:
    """Write 工程名.autosave.json beside the project. Returns the sidecar path."""
    global _last_write
    if not project_path:
        raise ValueError("没有工程路径，无法自动保存")
    dest = sidecar_path(project_path)
    preserve_foreign_snapshot(dest)
    payload = dict(data or {})
    payload["_autosave"] = {
        "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "project": "" if is_untitled(project_path) else os.path.abspath(project_path),
        "session": SESSION_ID,
    }
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    tmp = dest + ".tmp"
    try:
        from cutora.model.project_io import _rj_json_safe
        body = _rj_json_safe(dict(data or {}))
        if not isinstance(body, dict):
            body = dict(data or {})
        body["_autosave"] = payload["_autosave"]
        payload = body
    except Exception:
        logger.exception("autosave sanitize failed; writing with ProjectEncoder")
    # Q159：一次性紧凑编码（C 加速），不缩进 —— 带缩进的 json.dump 走纯 Python 逐项编码，
    # 大工程要两三秒；快照文件不需要好看
    from cutora.core.safe_json import ProjectEncoder
    text = json.dumps(payload, ensure_ascii=False, cls=ProjectEncoder)
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
        fh.write("\n")
    os.replace(tmp, dest)
    try:
        os.makedirs(user_autosave_dir(), exist_ok=True)
        ptr = {"sidecar": dest, "project": os.path.abspath(project_path)}
        ptmp = _pointer_path() + ".tmp"
        with open(ptmp, "w", encoding="utf-8") as fh:
            json.dump(ptr, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        os.replace(ptmp, _pointer_path())
    except Exception:
        logger.exception("autosave pointer write failed")
    _last_write = time.monotonic()
    logger.info("autosave %s", dest)
    return dest


def clear_snapshot(project_path: str | None) -> None:
    if not project_path:
        return
    dest = sidecar_path(project_path)
    # 别的会话留下的快照（还没被恢复）不能跟着这次的干净保存一起删掉
    preserve_foreign_snapshot(dest)
    try:
        if os.path.isfile(dest):
            os.remove(dest)
    except OSError:
        logger.warning("could not remove autosave %s", dest)
    try:
        ptr = _pointer_path()
        if os.path.isfile(ptr):
            with open(ptr, encoding="utf-8") as fh:
                info = json.load(fh)
            if os.path.normcase(str((info or {}).get("sidecar") or "")) == os.path.normcase(dest):
                os.remove(ptr)
    except Exception:
        pass


def clear_pointer() -> None:
    """扔掉全局指针文件。

    Q118：退出时只删了当前工程旁边的 sidecar，指针文件却留着，于是下次
    启动（那会儿还没打开任何工程）就照着指针把上一个会话的快照翻出来，
    每次都问一遍「上次未正常退出，是否恢复？」。干净退出就一起清掉。
    """
    try:
        ptr = _pointer_path()
        if os.path.isfile(ptr):
            os.remove(ptr)
    except OSError:
        logger.warning("could not remove autosave pointer")


def leftover_for(project_path: str | None) -> str | None:
    if project_path:
        dest = sidecar_path(project_path)
        if os.path.isfile(dest):
            return dest
    try:
        ptr = _pointer_path()
        if os.path.isfile(ptr):
            with open(ptr, encoding="utf-8") as fh:
                info = json.load(fh)
            side = str((info or {}).get("sidecar") or "")
            if side and os.path.isfile(side):
                return side
    except Exception:
        return None
    return None


def read_snapshot(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError("autosave 不是对象")
    return data


def project_payload(data: dict) -> dict:
    out = dict(data or {})
    out.pop("_autosave", None)
    return out


def due() -> bool:
    """Q159：到没到下一次自动保存的时间。调用方先问这个再去整理工程数据 —— 以前每条命令都先把整个
    工程深拷清洗一遍（一百条视频一秒多），再发现没到 15 秒白做。"""
    return (time.monotonic() - _last_write) >= THROTTLE_SEC


def mark_started() -> None:
    """后台写盘已经领走这一轮：节流从现在算，别人别再排。"""
    global _last_write
    _last_write = time.monotonic()


def maybe_write(project_path: str | None, data: dict, *, force: bool = False) -> str | None:
    if not project_path or not data:
        return None
    if not force and not due():
        return None
    return write_snapshot(project_path, data)


def should_skip_command(name: str) -> bool:
    return str(name or "") in _SKIP_COMMANDS


def status(project_path: str | None = None) -> dict:
    left = leftover_for(project_path)
    meta = None
    if left:
        try:
            raw = read_snapshot(left)
            meta = raw.get("_autosave")
        except Exception:
            meta = None
    return {
        "minutes": interval_minutes(),
        "sidecar": leftover_for(project_path) if project_path else left,
        "leftover": left,
        "meta": meta,
        "throttle_sec": THROTTLE_SEC,
    }


def untitled_leftover() -> str | None:
    """上一次会话留下的未命名工程快照（崩了 / 没存就关了）；这次运行自己写的不算。"""
    dest = sidecar_path(untitled_project_path())
    if not os.path.isfile(dest) or _snapshot_session(dest) == SESSION_ID:
        return None
    return dest


def unsaved_leftover(project_path: str | None) -> str | None:
    """打开工程时：旁边有没有上一次会话留下、比工程文件新、内容又不一样的快照。
    没有工程路径（刚启动 / 未命名）→ 看未命名工程的快照。"""
    if not project_path:
        return untitled_leftover()
    dest = sidecar_path(project_path)
    if not os.path.isfile(dest):
        return None
    if _snapshot_session(dest) == SESSION_ID:
        return None
    try:
        if os.path.isfile(project_path) and os.path.getmtime(dest) <= os.path.getmtime(project_path) + 1.0:
            return None
    except OSError:
        return None
    try:
        snap = project_payload(read_snapshot(dest))
        with open(project_path, encoding="utf-8") as fh:
            proj = json.load(fh)
        if isinstance(proj, dict):
            proj.pop("_autosave", None)
            if (json.dumps(snap, sort_keys=True, default=str)
                    == json.dumps(proj, sort_keys=True, default=str)):
                return None
    except Exception:
        pass
    return dest


def reset_throttle_for_tests() -> None:
    global _last_write
    _last_write = 0.0
    _checked_dest.clear()
    _backed_up_dest.clear()
