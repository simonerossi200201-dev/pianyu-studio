# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""把 Q119 那个 bug 存坏的工程救回来。

症状：拖进 v01…v22 存盘重开，媒体库每一行都变成最后那个视频。原因是
apply_project_data 的主循环用了上一个循环留下的 path 变量（见 Q119），
于是每条 task 的 "video" / "path" 都被写成了最后一个文件。

好消息是**时间线没坏**：每条 task 的 timeline.sources 里存的还是它自己
那个视频的真实路径。这个脚本就是照着主视频轨的片段把 video/path 反推回去。

    python tools/fix_task_video_paths.py "D:\\1_PROJECT\\2026-09-24\\A\\2026-09-24A.cutora"

默认只看不改（dry-run），确认没问题再加 --write。改之前会把原文件复制成
<名字>.bak-YYYYmmdd-HHMMSS。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time


def _sources_of(tl):
    if isinstance(tl, dict):
        return tl.get("sources") or {}
    return {}


def _tracks_of(tl):
    if isinstance(tl, dict):
        return tl.get("tracks") or []
    return []


def _src_path(src):
    if isinstance(src, dict):
        return str(src.get("path") or "")
    return str(getattr(src, "path", "") or "")


def main_video_path(task) -> str:
    """这条 task 真正的主视频：主视频轨第一个片段指向的源。

    没有 is_main_video 标记就退而取第一条视频轨；再不行就取时长最长的源。
    """
    tl = (task or {}).get("timeline")
    sources = _sources_of(tl)
    tracks = [t for t in _tracks_of(tl)
              if (t.get("type") if isinstance(t, dict) else None) == "video"]
    ordered = [t for t in tracks if (t.get("is_main_video") if isinstance(t, dict) else False)]
    ordered += [t for t in tracks if t not in ordered]
    for tr in ordered:
        clips = (tr.get("clips") if isinstance(tr, dict) else None) or []
        for c in clips:
            sid = c.get("source_id") if isinstance(c, dict) else None
            path = _src_path(sources.get(sid)) if sid else ""
            if path:
                return path
    best, best_dur = "", -1.0
    for src in sources.values():
        path = _src_path(src)
        try:
            dur = float(src.get("duration") or 0.0) if isinstance(src, dict) else 0.0
        except (TypeError, ValueError):
            dur = 0.0
        if path and dur > best_dur:
            best, best_dur = path, dur
    return best


def fix(path: str, write: bool = False) -> int:
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    tasks = data.get("tasks") or []
    if not tasks:
        print("这个工程里没有条目。")
        return 0
    changed = 0
    for i, task in enumerate(tasks):
        old = str(task.get("video") or task.get("path") or "")
        real = main_video_path(task)
        if not real:
            print("  [%2d] 时间线里找不到视频源，跳过（原值 %s）"
                  % (i + 1, os.path.basename(old) or "空"))
            continue
        if os.path.normcase(os.path.abspath(real)) == os.path.normcase(os.path.abspath(old or real)):
            print("  [%2d] %s  已经是对的" % (i + 1, os.path.basename(real)))
            continue
        print("  [%2d] %s  →  %s"
              % (i + 1, os.path.basename(old) or "空", os.path.basename(real)))
        task["video"] = real
        task["path"] = real
        changed += 1
    if not changed:
        print("\n没有需要改的。")
        return 0
    if not write:
        print("\n共 %d 条要改。确认无误后加 --write 真正写回。" % changed)
        return changed
    backup = "%s.bak-%s" % (path, time.strftime("%Y%m%d-%H%M%S"))
    shutil.copy2(path, backup)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False)
    os.replace(tmp, path)
    print("\n已改 %d 条。原文件备份在：\n  %s" % (changed, backup))
    return changed


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("project")
    ap.add_argument("--write", action="store_true", help="真正写回（默认只看不改）")
    args = ap.parse_args()
    if not os.path.isfile(args.project):
        print("找不到工程文件：%s" % args.project)
        sys.exit(2)
    print("工程：%s" % args.project)
    fix(args.project, write=args.write)
