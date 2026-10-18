# -*- coding: utf-8 -*-
"""规格 86：简单任务（仅无文案）短路编排与迁移。"""
from __future__ import annotations

from pathlib import Path

from ..errors import OranteError

SILENT_DEFAULT = (
    "画面中的人物全程不说话、没有对白，嘴唇保持闭合；可以有自然的表情、眨眼和细微的动作。\n"
    "No dialogue. The person does not speak; lips stay closed. Natural expressions and subtle movements only."
)


def has_copy(task_or_manifest) -> bool:
    if task_or_manifest is None:
        return False
    if isinstance(task_or_manifest, dict):
        c = task_or_manifest.get("copy")
        if isinstance(c, dict):
            return bool((c.get("text") or "").strip())
        return bool((c or "").strip())
    c = getattr(task_or_manifest, "copy", None)
    if isinstance(c, dict):
        return bool((c.get("text") or "").strip())
    return bool((c or "").strip())


def subject_images(manifest) -> list[dict]:
    imgs = list(getattr(manifest, "images", None) or [])
    out = []
    for im in imgs:
        if not isinstance(im, dict):
            continue
        role = (im.get("role") or "主体").strip()
        if role in ("空镜", "broll", "BROLL"):
            continue
        out.append(im)

    def key(im):
        p = str(im.get("p") or "")
        digits = "".join(ch for ch in p if ch.isdigit())
        return (int(digits) if digits else 0, p)

    out.sort(key=key)
    return out


def is_simple(task, manifest) -> bool:
    """简单任务 = 没有文案（有文案的单图任务保持 86 前行为）。"""
    return (not has_copy(task)) and (not has_copy(manifest))


def auto_arrange_if_simple(job, task, manifest, task_dir) -> bool:
    """仅无文案任务：按每张图（含空镜）填段、加 image_locked、写 pins、标记 simple86。
    有文案（含单图）一律不短路，保持 86 前行为。
    """
    if not is_simple(task, manifest):
        return False
    segs = list(getattr(manifest, "segments", None) or [])
    if not segs:
        return False
    # 没文案时空镜也是一段，不能只认主体图，否则全是空镜头的任务排不进去。
    from . import split as splitmod
    pics = splitmod.clip_images(manifest)
    if not pics:
        return False
    for i, sg in enumerate(segs):
        if i < len(pics):
            sg["image"] = pics[i].get("p") or sg.get("image") or ""
        sg["image_locked"] = True
    pins = {}
    for sg in segs:
        s = sg.get("s")
        im = sg.get("image") or ""
        if s is not None and im:
            pins[str(s)] = im
    tr = dict(getattr(manifest, "track", None) or {})
    tr["pins"] = pins
    tr["simple86"] = True
    manifest.track = tr
    manifest.segments = segs
    manifest.save(task_dir)
    if job is not None:
        fn = getattr(job, "log", None)
        if callable(fn):
            fn("只有一种配图方式，已自动排好（跳过编排）")
    return True


def _has_takes(manifest) -> bool:
    for sg in getattr(manifest, "segments", None) or []:
        if sg.get("takes"):
            return True
        if (sg.get("status") or "") in ("done", "collected", "accepted"):
            return True
    return False


def migrate_copy_to_desc(task_ids: list[str], *, confirm: bool = False) -> dict:
    """H 节：文案 → 每张主体图描述语。仅处理显式传入的 id，且必须 confirm。"""
    if not confirm:
        raise OranteError("需要确认", "请在界面确认后再迁移。")
    if not task_ids:
        raise OranteError("没有选中任务", "先在任务表选中要迁移的任务。")
    from .. import tasks as taskmod
    from ..models import Manifest
    from . import split

    ok, skipped, errors = [], [], []
    for tid in task_ids:
        try:
            t = taskmod.get(tid)
        except Exception as e:
            errors.append({"id": tid, "error": str(e)})
            continue
        st = (t.get("status") or "")
        if st in ("generating", "collected", "finished"):
            skipped.append({"id": tid, "reason": "已有生成进度，不能迁移"})
            continue
        d = t.get("dir")
        if not d or not Path(d).is_dir():
            errors.append({"id": tid, "error": "任务还没准备"})
            continue
        m = Manifest.load(Path(d))
        if _has_takes(m):
            skipped.append({"id": tid, "reason": "已有成片，不能迁移"})
            continue
        if isinstance(t.get("copy"), dict):
            copy = (t["copy"].get("text") or "").strip()
        else:
            copy = (t.get("copy") or "").strip()
        if not copy:
            mc = getattr(m, "copy", None)
            if isinstance(mc, dict):
                copy = (mc.get("text") or "").strip()
            else:
                copy = (mc or "").strip()
        if not copy:
            skipped.append({"id": tid, "reason": "文案已空"})
            continue
        # 先在内存里把新段拆好，全部成功后才落盘、才清库里的文案。
        # 0922 教训：以前先清库再拆段，Flow 时长 15 一抛错，文案就丢在半路了。
        for im in subject_images(m):
            im["desc"] = copy
            im["desc_manual"] = True
        if isinstance(getattr(m, "copy", None), dict):
            m.copy = {"text": "", "sha1": ""}
        else:
            m.copy = ""
        t2 = dict(t)
        t2["copy"] = ""

        class _Job:
            def log(self, msg):
                pass

            def check(self):
                pass

        job = _Job()
        try:
            m.split, m.segments = split.split_task(job, t2, m)
        except Exception as e:  # noqa: BLE001
            msg = getattr(e, "message", None) or str(e)
            hint = getattr(e, "hint", "") or ""
            errors.append({"id": tid, "error": msg, "hint": hint})
            continue
        auto_arrange_if_simple(job, t2, m, Path(d))
        m.save(Path(d))
        from .. import db as _db
        _db.run("UPDATE tasks SET copy=?, updated=? WHERE id=?", ("", _db.now(), tid))
        taskmod.set_status(tid, "arranged")
        ok.append(tid)
    return {"ok": ok, "skipped": skipped, "errors": errors}


def use_current_track(task_id: str, *, start_generate: bool = True) -> dict:
    """D 节：每段都有图 → 推到 arranged，可选立刻开跑。"""
    from .. import tasks as taskmod
    from ..models import Manifest

    t = taskmod.get(task_id)
    d = t.get("dir")
    if not d or not Path(d).is_dir():
        raise OranteError("任务还没准备", "先跑准备。")
    m = Manifest.load(Path(d))
    segs = list(m.segments or [])
    if not segs:
        raise OranteError("没有段", "先准备/拆段。")
    missing = [sg.get("s") for sg in segs if not (sg.get("image") or "").strip()]
    if missing:
        raise OranteError("还有段没配图", f"段 {missing} 没有图片，请先去编排。")
    st = t.get("status") or ""
    if st not in ("arranged", "generating", "collected", "finished"):
        taskmod.set_status(task_id, "arranged")
    result = {"status": "arranged", "started": False}
    if start_generate:
        try:
            from .. import jobs as jobmod
            jobmod.start("generate", task_id)
            result["started"] = True
        except Exception:
            result["started"] = False
            result["need_start"] = True
    return result


def missing_desc_for_no_copy(task, manifest) -> list[str]:
    if has_copy(task) or has_copy(manifest):
        return []
    from . import split as splitmod
    bad = []
    for im in splitmod.clip_images(manifest):
        if not str(im.get("desc") or "").strip():
            bad.append(im.get("p") or "?")
    return bad
