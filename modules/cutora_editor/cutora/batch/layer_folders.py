# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""图层面板里的文件夹。和预合成 clip.group 不是一回事。

默认不写 hidden / opacity / blend：渲染看见这种组就当它不存在，画面一个字节不变。
只有关掉眼睛、把不透明度拧离 255、或混合离开「正常」，子层才跟着变。
"""
from __future__ import annotations

import uuid
from contextlib import contextmanager
from contextvars import ContextVar

KEY = "layer_folders"
_TASK: ContextVar = ContextVar("cutora_folder_task", default=None)


def folders(task) -> list:
    raw = task.get(KEY) if isinstance(task, dict) else None
    return [f for f in raw or [] if isinstance(f, dict) and f.get("id")]


def find(task, fid) -> dict | None:
    want = str(fid or "")
    for f in folders(task):
        if str(f.get("id")) == want:
            return f
    return None


def is_identity(folder) -> bool:
    if not isinstance(folder, dict):
        return True
    if folder.get("hidden"):
        return False
    try:
        op = float(folder.get("opacity", 255))
    except (TypeError, ValueError):
        op = 255.0
    if abs(op - 255.0) > 0.01:
        return False
    return str(folder.get("blend") or "normal") == "normal"


def any_live(task) -> bool:
    return any(not is_identity(f) for f in folders(task))


def member_key(obj, locator=None) -> str:
    if isinstance(obj, dict):
        oid = obj.get("id")
        if oid:
            return "o:" + str(oid)
        if isinstance(locator, dict):
            return "l:%s:%s" % (locator.get("list"), locator.get("index"))
        return ""
    uid = getattr(obj, "uid", None) if obj is not None else None
    return ("c:" + str(uid)) if uid else ""


def add_folder(task, keys, name: str = "") -> dict:
    folder = {
        "id": "fd" + uuid.uuid4().hex[:8],
        "name": (name or "").strip() or "分组",
        "members": list(dict.fromkeys(k for k in keys if k)),
        "collapsed": False,
    }
    task.setdefault(KEY, []).append(folder)
    return folder


def ungroup(task, fid) -> bool:
    raw = list(task.get(KEY) or [])
    keep = [f for f in raw if str(f.get("id")) != str(fid)]
    if len(keep) == len(raw):
        return False
    if keep:
        task[KEY] = keep
    else:
        task.pop(KEY, None)
    return True


def toggle_collapsed(task, fid) -> bool:
    f = find(task, fid)
    if f is None:
        return False
    f["collapsed"] = not bool(f.get("collapsed"))
    return True


def set_opacity(task, fid, opacity) -> bool:
    f = find(task, fid)
    if f is None:
        return False
    try:
        op = int(round(float(opacity)))
    except (TypeError, ValueError):
        return False
    op = max(0, min(255, op))
    if op >= 255:
        f.pop("opacity", None)
    else:
        f["opacity"] = op
    return True


def set_blend(task, fid, blend: str) -> bool:
    f = find(task, fid)
    if f is None:
        return False
    name = str(blend or "normal")
    if name == "normal":
        f.pop("blend", None)
    else:
        f["blend"] = name
    return True


def folder_of(task, obj, locator=None) -> dict | None:
    key = member_key(obj, locator)
    if not key or not isinstance(task, dict):
        return None
    for f in folders(task):
        if key in (f.get("members") or []):
            return f
    return None


def hidden_by_folder(task, obj, locator=None) -> bool:
    if not isinstance(task, dict) or not task.get(KEY):
        return False
    f = folder_of(task, obj, locator)
    return bool(f and f.get("hidden"))


def blend_of_folder(task, obj, locator=None):
    """组混合不是「正常」时返回它，否则 None。调用方只在子层自己也是正常时才换上。"""
    if not isinstance(task, dict) or not task.get(KEY):
        return None
    f = folder_of(task, obj, locator)
    if not f:
        return None
    name = str(f.get("blend") or "normal")
    return None if name == "normal" else name


def opacity_factor(task, obj, locator=None) -> float:
    if not isinstance(task, dict) or not task.get(KEY):
        return 1.0
    f = folder_of(task, obj, locator)
    if not f or "opacity" not in f:
        return 1.0
    try:
        return max(0.0, min(1.0, float(f.get("opacity", 255)) / 255.0))
    except (TypeError, ValueError):
        return 1.0


@contextmanager
def folder_task_scope(task):
    tok = _TASK.set(task if isinstance(task, dict) and task.get(KEY) else None)
    try:
        yield
    finally:
        _TASK.reset(tok)


def opacity_mul_current(obj) -> float:
    task = _TASK.get()
    if not task:
        return 1.0
    return opacity_factor(task, obj)


def decorate(task, rows) -> list:
    """没有文件夹就原样返回。有的话在第一个成员前面插一行组标题，成员缩进。"""
    fs = folders(task)
    if not fs:
        return rows
    from cutora.batch.layer_model import resolve

    by_key = {}
    for f in fs:
        for k in f.get("members") or []:
            by_key.setdefault(k, f)
    emitted = set()
    out = []

    def _header(f):
        return {
            "kind": "folder",
            "title": str(f.get("name") or "分组"),
            "label": str(f.get("name") or "分组"),
            "locator": {"where": "folder", "id": f.get("id")},
            "hidden": bool(f.get("hidden")),
            "locked": False,
            "color": "",
            "is_audio": False,
            "opacity": float(f.get("opacity", 255) or 255),
            "blend": str(f.get("blend") or "normal"),
            "start": 0.0,
            "end": 0.0,
            "indent": 0,
            "collapsed": bool(f.get("collapsed")),
        }

    for r in rows:
        loc = r.get("locator")
        obj = resolve(task, loc)
        key = member_key(obj, loc)
        f = by_key.get(key)
        if f is None:
            out.append(r)
            continue
        fid = f.get("id")
        if fid not in emitted:
            emitted.add(fid)
            out.append(_header(f))
        if f.get("collapsed"):
            continue
        child = dict(r)
        child["indent"] = 1
        out.append(child)
    for f in fs:
        if f.get("id") not in emitted:
            out.append(_header(f))
    return out
