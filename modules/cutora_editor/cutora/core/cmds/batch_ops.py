# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Headless batch.* handlers that call existing CLI op functions."""
from __future__ import annotations

import os

from cutora.core.cmds.specs_batch import recipe_steps
from cutora.core.commands import CommandError, Context


def cmd_batch_dispatch(ctx: Context, params: dict) -> dict:
    """Run one CLI op or a recipe of ops. Same functions as ``--do``."""
    short = (ctx.command or "").split(".", 1)[-1]
    steps = recipe_steps(ctx.command or "")
    if steps:
        return _run_steps(steps, params)
    return _run_one(short, params)


def cmd_batch_queue_add(ctx: Context, params: dict) -> dict:
    from cutora.batch.cli import make_queue, queue_summary
    projects = list(params.get("projects") or [])
    if not projects:
        raise CommandError("projects 不能为空")
    auto = params.get("auto_subtitle")
    auto_subtitle = True if auto is None else bool(auto)
    q = make_queue(projects, restart_between=True, auto_subtitle=auto_subtitle)
    return {"queue": queue_summary(q)}


def _run_one(op_name: str, params: dict) -> dict:
    from cutora.batch.cli import OP_HANDLERS
    fn = OP_HANDLERS.get(op_name)
    if fn is None:
        raise CommandError("未知无头操作 %r" % op_name, code="unknown")
    result = fn(params, {"json": True})
    if result is None:
        return {"ok": True}
    if not isinstance(result, dict):
        return {"ok": True, "result": result}
    return result


def cmd_stitch_to_timeline(ctx: Context, params: dict) -> dict:
    """T20a: cutlist → timeline placements. Never writes a stitched mp4."""
    task_dir = str(params.get("task_dir") or "").strip()
    if not task_dir or not os.path.isdir(task_dir):
        raise CommandError("stitch.to_timeline 需要已存在的 task_dir")
    from cutora.batch.timeline_stitch import (
        load_cutlist, placements_from_cutlist, project_from_placements,
        placements_by_group, projects_from_cutlist, write_placements,
    )
    cuts = load_cutlist(task_dir)
    grouped = placements_by_group(cuts)
    if len(grouped) > 1:
        asprojs = projects_from_cutlist(cuts, task_dir)
        n = sum(len(v) for v in grouped.values())
        return {
            "ok": True,
            "n": n,
            "groups": list(grouped.keys()),
            "asproj": asprojs[0] if asprojs else None,
            "asprojs": asprojs,
            "placements": [p.as_dict() for plist in grouped.values() for p in plist],
            "mp4": None,
        }
    placements = placements_from_cutlist(cuts)
    dest = write_placements(task_dir, placements)
    asproj = os.path.join(os.path.dirname(dest), "stitched.asproj.json")
    project_from_placements(placements, asproj)
    return {
        "ok": True,
        "n": len(placements),
        "placements": [p.as_dict() for p in placements],
        "path": dest,
        "asproj": asproj,
        "mp4": None,
    }


def _run_steps(steps: list, params: dict) -> dict:
    results = []
    for name in steps:
        item = _run_one(name, params)
        results.append({"op": name, "result": item})
        if item.get("ok") is False:
            return {"ok": False, "steps": results}
    return {"ok": True, "steps": results}
