# -*- coding: utf-8 -*-
"""kind=prepare：任务从「草稿」到「已准备」：开目录 → 下图编号 → 拆段 → 写 manifest。

0923：准备阶段不再判空镜（不调 Gemini）。图全部收下当主体，空镜由用户自己标。
"""
from __future__ import annotations

from pathlib import Path

from .. import db, tasks as taskmod, workspace, events
from ..jobs import register
from ..models import Manifest
from ..errors import OranteError


@register("prepare")
def prepare(job, batch_dir: str = "", batch_id: str = ""):
    t = taskmod.get(job.task_id)
    if not batch_dir:
        raise OranteError("没有告诉我这批存到哪", "任务面板会先弹「保存位置」。")
    d = workspace.task_dir(batch_dir, t["id"], t["name"])
    (d / "copy.txt").write_text(t.get("copy") or "", encoding="utf-8")
    job.log(f"任务目录 {d}")
    m = Manifest.new(t["id"], t["name"], t["engine"], t["account_id"], t["type"], t["copy"])
    from . import pipeline
    # 用任务表 gen_mode 列解析，不再读全局 generate.mode
    m.mode = pipeline.resolve_gen_mode(t)

    from . import images, split, broll
    job.log(f"下图：{t['image_source']}")
    m.images = images.fetch(job, t, d)
    job.check()
    # 0923：不再叫 Gemini 判空镜——太慢，还常把横屏主体图判成空镜把任务卡死。
    # 图全部收下，一律先当主体、连号 p01…；哪张是空镜由用户自己在面板上标。
    job.log("图全部收下，不判空镜（哪张是空镜自己标）")
    for im in m.images:
        if not isinstance(im, dict):
            continue
        im["role"] = "主体"
        im.pop("broll_check", None)
        im.pop("broll_reason", None)
        if im.get("eye") == "空镜":
            im.pop("eye", None)
    m.images = broll.renumber(d, m.images)
    job.check()
    job.log(f"拆段（{t['engine']}）")
    old_segs = list(getattr(m, "segments", None) or [])
    # 若任务目录已有旧 manifest，保留 duration_manual 段
    try:
        from ..models import Manifest as _M
        from pathlib import Path as _P
        if t.get("dir") and _P(t["dir"]).is_dir():
            old_segs = list(_M.load(_P(t["dir"])).segments or []) or old_segs
    except Exception:
        pass
    m.split, m.segments = split.split_task(job, t, m)
    from . import duration_edit as _duredit
    m.segments = _duredit.preserve_manual_durations(old_segs, m.segments)
    m.save(d)
    taskmod.set_dir(t["id"], d, batch_id)
    taskmod.set_status(t["id"], "prepared")
    # 规格 86：仅没文案 → 自动配图并推到 arranged（有文案单图不短路）
    from . import simple86 as _simple86
    arranged = _simple86.auto_arrange_if_simple(job, t, m, d)
    if arranged:
        taskmod.set_status(t["id"], "arranged")
    n_subj = sum(1 for im in m.images if im.get("role") != "空镜")
    n_broll = sum(1 for im in m.images if im.get("role") == "空镜")
    # 单图短视频任务：准备阶段即预先把唯一主体图 p01 挂到所有分段上，方便后续一键跳过编排
    if n_subj == 1 and m.segments:
        first_p = next((im.get("p") for im in m.images if im.get("role") != "空镜" and im.get("p")), "p01")
        for sg in m.segments:
            if not (sg.get("image") or "").strip():
                sg["image"] = first_p
        m.save(d)
    meta = dict(t.get("meta") or {}) if isinstance(t.get("meta"), dict) else {}
    meta["subjects"] = n_subj
    meta["brolls"] = n_broll
    db.run("UPDATE tasks SET meta=?, updated=? WHERE id=?",
           (db.j(meta), db.now(), t["id"]))
    events.emit("tasks.changed")
    job.log(f"✓ 主体 {n_subj} 张，空镜 {n_broll} 张，{len(m.segments)} 段")
    return {
        "dir": str(d),
        "images": len(m.images),
        "subjects": n_subj,
        "brolls": n_broll,
        "segments": len(m.segments),
    }
