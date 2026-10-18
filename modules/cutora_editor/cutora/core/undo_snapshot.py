# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q159 · 撤销快照按视频存，不再每一步深拷整个工程。

以前每条命令记撤销 = ``copy.deepcopy(全部 tasks)`` 两遍（之前 / 之后），撤销再拷两遍 + 整条重载。
媒体库里一百条视频（各带字幕词级时间、覆层、时间线）一遍就是一两秒 —— 随手一个操作、一次 Ctrl+Z
都要卡好几秒，内存里还攒着 80 份整工程。

这里的快照只深拷「这次操作会动到的那几条视频」，其余只记引用（谁、什么顺序）。撤销 / 重做按时间顺序
一步步走：某条视频如果这一步没动过，它此刻的样子就是这一步之前的样子（动过它的后面那些步已经先撤回去了），
所以不拷它也是对的。恢复时**原地**写回（``task.clear(); task.update(副本)``），任务字典的身份不变，
后面的快照里记的引用照样有效。

* ``scope="all"``：整工程（增删 / 重排视频、批量操作、不知道会动到谁的老入口）—— 和以前一样安全。
* ``scope="task"``：只有当前这条；``scope=int`` / ``[int, …]``：指定下标。
"""
from __future__ import annotations

import copy

# 这些命令族只改「当前这条视频」（media_id 指定的话就是那一条）：时间线 / 字幕 / 关键帧 / 效果 / 轨道 /
# 标记 / 预合成 / 覆层 / 分屏 / 调色。样式预设（style.*）、音量总控（audio.volume_master 刷全部）不在这里。
TASK_SCOPED_NAMESPACES = frozenset({
    "subtitle", "timeline", "keyframe", "fx", "track", "marker", "group",
    "overlay", "split", "grade",
})
# 这些参数名出现就说明可能跨视频 → 整工程
_MULTI_PARAM_KEYS = ("media_ids", "task_ids", "task_indices", "indices", "all_tasks", "apply_all", "targets")


def scope_for_command(command, params) -> object:
    """命令总线：按命令族 / 参数判断这一步要拷谁。拿不准 → "all"。"""
    if not command:
        return "all"
    ns = str(command).split(".", 1)[0]
    if ns not in TASK_SCOPED_NAMESPACES:
        return "all"
    params = params if isinstance(params, dict) else {}
    if any(k in params and params[k] not in (None, False, "") for k in _MULTI_PARAM_KEYS):
        return "all"
    if params.get("media"):                 # 按路径指定视频（无头 / 脚本用法）：不猜下标，整工程
        return "all"
    mid = params.get("media_id")
    if mid not in (None, ""):
        try:
            return int(mid)
        except (TypeError, ValueError):
            return "all"
    return "task"


def _indices(scope, n: int, curr_idx: int):
    if scope in (None, "all"):
        return list(range(n))
    if scope == "task":
        return [curr_idx] if 0 <= curr_idx < n else []
    if isinstance(scope, int):
        return [scope] if 0 <= scope < n else []
    out = []
    for s in scope or []:
        try:
            i = int(s)
        except (TypeError, ValueError):
            continue
        if 0 <= i < n and i not in out:
            out.append(i)
    return out


class UndoSnapshot:
    """一步操作「之前」或「之后」的样子：任务顺序（引用）+ 动到的那几条的深拷贝。"""

    __slots__ = ("refs", "copies", "curr_idx", "file_names", "scope", "approx_bytes")

    def __init__(self, tasks, curr_idx, file_names, scope="all"):
        tasks = list(tasks or [])
        self.refs = tasks
        self.curr_idx = int(curr_idx if curr_idx is not None else -1)
        self.file_names = list(file_names or [])
        self.scope = scope
        self.copies = {}
        n_obj = 0
        for i in _indices(scope, len(tasks), self.curr_idx):
            memo = {}
            self.copies[i] = copy.deepcopy(tasks[i], memo)
            n_obj += len(memo)
        # 粗略的内存估计（每个被拷的对象约 100 字节），给历史记录的预算用
        self.approx_bytes = n_obj * 100 + 64

    def same_as(self, other) -> bool:
        """两份快照是不是同一个状态（没改任何东西的那一步要丢掉）。"""
        if not isinstance(other, UndoSnapshot):
            return False
        if self.curr_idx != other.curr_idx or self.file_names != other.file_names:
            return False
        if len(self.refs) != len(other.refs) or any(a is not b for a, b in zip(self.refs, other.refs)):
            return False
        if set(self.copies) != set(other.copies):
            return False
        if abs(int(self.approx_bytes) - int(other.approx_bytes)) > 0:
            return False                      # 对象数都不一样，肯定改了（快速路径）
        try:
            return all(struct_equal(self.copies[i], other.copies[i]) for i in self.copies)
        except Exception:
            return False


    # 老代码 / 测试按字典取
    def __getitem__(self, key):
        if key == "tasks":
            # 快照当时的样子：拷了的那几条给副本，其余给引用（老代码 / 测试按字典读）
            return [self.copies.get(i, t) for i, t in enumerate(self.refs)]
        if key == "curr_idx":
            return self.curr_idx
        if key == "file_names":
            return self.file_names
        raise KeyError(key)

    def get(self, key, default=None):
        try:
            return self[key]
        except KeyError:
            return default

    def __contains__(self, key):
        return key in ("tasks", "curr_idx", "file_names")

    def __bool__(self):
        return True

    def covers_all(self) -> bool:
        return len(self.copies) == len(self.refs)

    def restore_into(self, tasks_list):
        """把快照写回：顺序照 refs，动过的那几条原地覆盖。返回恢复后的任务列表（尽量复用传入的列表对象）。"""
        for i, task in enumerate(self.refs):
            snap = self.copies.get(i)
            if snap is None or not isinstance(task, dict):
                continue
            fresh = copy.deepcopy(snap)
            task.clear()
            task.update(fresh)
        if isinstance(tasks_list, list):
            tasks_list[:] = list(self.refs)
            return tasks_list
        return list(self.refs)

    def describe(self) -> str:
        return "快照：%d/%d 条视频" % (len(self.copies), len(self.refs))

def struct_equal(a, b, _depth=0) -> bool:
    """结构相等：dict / list / tuple 逐项比，普通对象比 __dict__（Timeline / Track / Clip 没定义 __eq__，
    直接 == 会按身份比，两份深拷贝永远不等）。"""
    if a is b:
        return True
    if _depth > 64:
        return a == b
    ta = type(a)
    if ta is not type(b):
        return False
    if ta in (int, float, str, bool, bytes, type(None)):
        return a == b
    if ta is dict:
        if len(a) != len(b):
            return False
        for k, v in a.items():
            if k not in b or not struct_equal(v, b[k], _depth + 1):
                return False
        return True
    if ta in (list, tuple):
        if len(a) != len(b):
            return False
        return all(struct_equal(x, y, _depth + 1) for x, y in zip(a, b))
    if ta in (set, frozenset):
        return a == b
    da = getattr(a, "__dict__", None)
    db = getattr(b, "__dict__", None)
    if da is not None and db is not None:
        return struct_equal(da, db, _depth + 1)
    slots = getattr(ta, "__slots__", None)
    if slots:
        return all(struct_equal(getattr(a, n, None), getattr(b, n, None), _depth + 1) for n in slots)
    try:
        return bool(a == b)
    except Exception:
        return False
