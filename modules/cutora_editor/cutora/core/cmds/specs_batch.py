# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Wrap the 15 existing CLI actions as batch.* commands. Do not hand-copy params."""
from __future__ import annotations

from cutora.core.command_schema import op_params_to_schema
from cutora.core.cmds.specutil import RESULT_SCHEMA, doc
from cutora.core.commands import CommandSpec

H = "cutora.core.cmds.batch_ops:cmd_batch_dispatch"

_ASYNC = {
    "stitch", "auto_subtitle", "queue_run", "batch_export",
    "batch_subtitles", "split_and_queue_export", "longtik_finish",
}
_DANGEROUS = {
    "stitch", "queue_run", "batch_export", "save_project",
    "batch_subtitles", "split_and_queue_export", "longtik_finish",
}

_RECIPE_STEPS = {
    "batch_subtitles": ["stitch", "split_project", "auto_subtitle", "batch_export"],
    "split_and_queue_export": ["split_project", "queue_run"],
    "longtik_finish": ["stitch", "auto_subtitle", "add_text_overlay", "add_bgm", "batch_export"],
}


def _title(name: str) -> str:
    return {
        "stitch": "智能拼接",
        "import_media": "无头导入媒体",
        "auto_subtitle": "无头自动字幕",
        "save_project": "无头保存工程",
        "split_project": "拆分工程",
        "queue_run": "跑工程队列",
        "batch_export": "无头批量导出",
        "add_text_overlay": "批量文字覆层",
        "add_bgm": "无头批量配乐",
        "merge_projects": "合并工程",
        "scatter_broll": "散落 B-roll",
        "insert_image": "批量加载",
        "batch_subtitles": "口播字幕配方",
        "split_and_queue_export": "拆分并排队导出",
        "longtik_finish": "长文案成片配方",
    }.get(name, name)


def _doc_for(item: dict) -> str:
    summary = str(item.get("summary") or "")
    note = str(item.get("note") or "")
    text = "。".join(p for p in (summary, note) if p)
    if len(text) < 40:
        text = text + "。失败常见原因：路径不存在、工程损坏、或被单实例锁占用。"
    return doc(text)


def _from_op(op: dict) -> CommandSpec:
    name = str(op["name"])
    return CommandSpec(
        name="batch." + name,
        title_zh=_title(name),
        doc_zh=_doc_for(op),
        params_schema=op_params_to_schema(op.get("params") or {}),
        returns_schema=RESULT_SCHEMA,
        undoable=False,
        dangerous=name in _DANGEROUS,
        requires_project=False,
        run=H,
        undo=None,
        headless=True,
        async_job=name in _ASYNC,
    )


def _from_recipe(rec: dict) -> CommandSpec | None:
    name = str(rec.get("name") or "")
    if name not in _RECIPE_STEPS:
        return None
    params = {}
    # Recipes reuse the first step's schema plus additionalProperties.
    from cutora.batch.cli import OPS_SPEC
    first = _RECIPE_STEPS[name][0]
    for op in OPS_SPEC:
        if op["name"] == first:
            params = op.get("params") or {}
            break
    return CommandSpec(
        name="batch." + name,
        title_zh=_title(name),
        doc_zh=_doc_for(rec),
        params_schema=op_params_to_schema(params),
        returns_schema=RESULT_SCHEMA,
        undoable=False,
        dangerous=name in _DANGEROUS,
        requires_project=False,
        run=H,
        undo=None,
        headless=True,
        async_job=name in _ASYNC,
    )


def build() -> list:
    from cutora.batch.cli import OPS_SPEC, RECIPES_SPEC
    from cutora.core.cmds.specutil import s, spec
    out = [_from_op(op) for op in OPS_SPEC]
    for rec in RECIPES_SPEC:
        item = _from_recipe(rec)
        if item is not None:
            out.append(item)
    out.append(spec(
        "stitch.to_timeline", "拼接成时间线",
        "读 task_dir 下的 cutlist.json，把保留片段铺成一条视频轨的 ClipPlacement 列表"
        "（source_path / in_t / out_t / start_t / link_audio），fit_mode=fill 铺满画布。"
        "不生成 _stitched 中间 mp4。portable，按目录寻址。"
        "失败常见原因：cutlist.json 不存在。",
        "cutora.core.cmds.batch_ops:cmd_stitch_to_timeline",
        params={"task_dir": s("string", "任务目录，内含 cutlist.json 或 _stitched/cutlist.json")},
        required=["task_dir"],
        headless=True,
        portable=True,
    ))
    return out


def recipe_steps(command_name: str) -> list[str] | None:
    short = command_name.split(".", 1)[-1]
    return _RECIPE_STEPS.get(short)
