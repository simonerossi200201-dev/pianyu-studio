# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""图片连线：记下哪张图对应哪条视频。

连线本身不写时间线。大小、位置、时间留在批量加载主窗口里，
点「插入到所选视频」时才按当时的设置真正叠上去。同一张图可以连多条视频。

这个模块不碰界面。界面在 `cutora/ui/link_image_dialog.py`。
"""
from __future__ import annotations

import os

from cutora.batch.batch_load import IMAGE_EXTS, list_image_paths
from cutora.batch.link_import import plan_from_links

__all__ = ["resolve_images", "plan_from_links", "apply_plan"]


def _natural_key(path: str):
    import re
    base = os.path.basename(path or "")
    return [int(t) if t.isdigit() else t.lower()
            for t in re.split(r"(\d+)", base)]


def resolve_images(entries) -> list[str]:
    """文件夹 / 单张图混着给，摊平、去重。文件夹内部按自然序，条目之间保持先后。"""
    out, seen = [], set()

    def _add(path):
        if not path or not os.path.isfile(path):
            return
        if os.path.splitext(path)[1].lower() not in IMAGE_EXTS:
            return
        key = os.path.normcase(os.path.abspath(path))
        if key in seen:
            return
        seen.add(key)
        out.append(os.path.abspath(path))

    for raw in entries or []:
        item = str(raw or "").strip().strip('"')
        if not item:
            continue
        if os.path.isdir(item):
            names = list_image_paths(item)
            names.sort(key=_natural_key)
            for path in names:
                _add(path)
        else:
            _add(item)
    return out


def _stamp(task, blend, anim_in, anim_out) -> None:
    from cutora.batch.script_insert_image import is_insert_overlay
    for ov in task.get("image_overlays") or []:
        if not is_insert_overlay(ov):
            continue
        ov["blend_mode"] = blend
        from cutora.batch.script_insert_image import stamp_transition
        stamp_transition(ov, anim_in, anim_out)
        for el in ov.get("elements") or []:
            if isinstance(el, dict):
                el["blend_mode"] = blend


def apply_plan(plan, tasks, *, replace=True, host=None, log=None,
               anim_in="fade", anim_out="fade", blend="normal",
               on_image=None, **insert_kw) -> dict:
    """按计划把图插进各条视频。同一条视频上的第一张才按 replace 清掉旧的插入。

    ``on_image(task, path)`` 在每一张成功插上之后调用（记配方用）。
    返回 {"tasks": n, "images": n}。
    """
    from cutora.batch.script_insert_image import insert_into_tasks

    n_tasks = n_images = 0
    for row in plan or []:
        task = tasks[row["task_index"]]
        paths = [p for p in (row.get("paths") or []) if p]
        if not paths:
            continue
        first = True
        for path in paths:
            insert_into_tasks(
                [task], path,
                replace=bool(replace) and first,
                host=host,
                log=log,
                **insert_kw,
            )
            first = False
            n_images += 1
            if on_image is not None:
                on_image(task, path)
        _stamp(task, blend or "normal", anim_in or "fade", anim_out or "fade")
        n_tasks += 1
    return {"tasks": n_tasks, "images": n_images}
