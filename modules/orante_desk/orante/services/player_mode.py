# -*- coding: utf-8 -*-
"""「5 收片」外部播放器模式（External Player Mode）：
- 支持直接拖入任意外部视频文件夹（无需先走 1~4 步），自动扫描视频片段、提取首帧缩略图、生成临时播放清单
- 严格视图隔离：未点「保存为项目」前，外部播放器任务仅在 `player_mode=1` / `include_external=1` 下可见，
  不污染「1 任务表」「2 排镜头」「4 生成」中的正常生产任务
- 点击「保存为项目」(`save_as_project`) 后，正式转为可编辑 Orante 项目并落盘 `project.orproj.json`，
  支持去「2 排镜头」改文案/提示词、去「1 任务表」绑定账号或账号组，并在「5 收片」中直接点「重生」替换不满意的镜头
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import asdict
from pathlib import Path

from .. import db, events, paths, tasks as taskmod
from ..errors import OranteError
from ..models import Image, Manifest, Segment

VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm"}
_SKIP_DIRS = {"_弃用", "_错件", "_历史", "images", ".git", "__pycache__"}
_SEG_NUM_RE = re.compile(r"(?:^|[_\-\s])s?(\d{1,4})(?:[_\-\s\.]|$)", re.I)


def _natural_sort_key(p: Path) -> tuple:
    m = _SEG_NUM_RE.search(p.stem)
    num = int(m.group(1)) if m else 999999
    return (num, p.name.lower())


def _find_video_files(folder: Path) -> list[Path]:
    """扫描目录本身及 `clips/` 子目录中的视频文件。"""
    vids: list[Path] = []
    seen: set[Path] = set()
    search_dirs = [folder]
    clips_dir = folder / "clips"
    if clips_dir.is_dir():
        search_dirs.insert(0, clips_dir)
    for d in search_dirs:
        for child in sorted(d.iterdir(), key=lambda x: x.name.lower()):
            if child.is_file() and child.suffix.lower() in VIDEO_EXTS and child.stat().st_size > 0:
                rp = child.resolve()
                if rp not in seen:
                    seen.add(rp)
                    vids.append(child)
    vids.sort(key=_natural_sort_key)
    return vids


def _probe_duration(video_path: Path) -> int:
    try:
        r = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(video_path),
            ],
            capture_output=True, text=True, timeout=6,
        )
        if r.returncode == 0 and (r.stdout or "").strip():
            return max(1, int(round(float(r.stdout.strip()))))
    except Exception:
        pass
    return 6


def _extract_first_frame(video_path: Path, dst_jpg: Path) -> bool:
    dst_jpg.parent.mkdir(parents=True, exist_ok=True)
    if dst_jpg.is_file() and dst_jpg.stat().st_size > 0:
        return True
    try:
        r = subprocess.run(
            [
                "ffmpeg", "-y", "-ss", "0.05",
                "-i", str(video_path),
                "-frames:v", "1", "-q:v", "3",
                str(dst_jpg),
            ],
            capture_output=True, timeout=8,
        )
        if r.returncode == 0 and dst_jpg.is_file() and dst_jpg.stat().st_size > 0:
            return True
    except Exception:
        pass
    # 兜底：若环境无 ffmpeg 或测试中用假 mp4 文件，生成一张干净的缩略占位图
    try:
        from PIL import Image as PILImage
        im = PILImage.new("RGB", (540, 960), (36, 44, 58))
        im.save(dst_jpg, format="JPEG", quality=85)
        return True
    except Exception:
        return False


def _expand_candidate_folders(raw_folders: list[str]) -> list[Path]:
    """如果传入的是总目录（里面每个子目录是一个任务），自动展开子目录；如果目录本身就有视频则直接当任务。"""
    out: list[Path] = []
    seen: set[Path] = set()
    for raw in (raw_folders or []):
        s = (raw or "").strip().strip('"').strip("'")
        if not s:
            continue
        p = Path(s)
        if not p.exists():
            continue
        if p.is_file() and p.suffix.lower() in VIDEO_EXTS:
            p = p.parent
        if not p.is_dir():
            continue
        direct_vids = _find_video_files(p)
        if direct_vids:
            rp = p.resolve()
            if rp not in seen:
                seen.add(rp)
                out.append(p)
            continue
        # 检查一层子目录
        for sub in sorted(p.iterdir(), key=lambda x: x.name.lower()):
            if not sub.is_dir() or sub.name in _SKIP_DIRS or sub.name.startswith("."):
                continue
            if _find_video_files(sub):
                rp = sub.resolve()
                if rp not in seen:
                    seen.add(rp)
                    out.append(sub)
    return out


def import_folders(folders: list[str]) -> dict:
    """将本地视频文件夹导入「5 收片」外部播放器模式。"""
    target_dirs = _expand_candidate_folders(folders)
    if not target_dirs:
        raise OranteError("所选文件夹中没有找到视频文件（支持 .mp4 / .mov / .mkv / .webm）")

    imported_tasks: list[dict] = []
    now = db.now()

    for folder in target_dirs:
        vids = _find_video_files(folder)
        if not vids:
            continue
        # 若该目录已有 manifest.json，优先复用其中的文案和提示词
        manifest_path = folder / "manifest.json"
        existing_m: Manifest | None = None
        if manifest_path.is_file():
            try:
                existing_m = Manifest.load(folder)
            except Exception:
                existing_m = None

        existing_row = db.one("SELECT * FROM tasks WHERE dir=?", (str(folder),))
        tid = existing_row["id"] if existing_row else db.next_id("task", "T")
        t_name = (existing_row or {}).get("name") or folder.name

        images_dir = folder / "images"
        images_dir.mkdir(parents=True, exist_ok=True)

        m = existing_m or Manifest.new(tid, t_name, "flow", "", "long", "")
        m.task_id = tid
        m.name = t_name

        new_images: list[dict] = []
        new_segments: list[dict] = []
        old_segs_by_s = {int(sg.get("s") or 0): sg for sg in (m.segments or []) if isinstance(sg, dict)}
        old_imgs_by_p = {im.get("p"): im for im in (m.images or []) if isinstance(im, dict) and im.get("p")}

        for idx, vpath in enumerate(vids, 1):
            p_code = f"p{idx:02d}"
            rel_img = f"images/{p_code}.jpg"
            _extract_first_frame(vpath, folder / rel_img)
            old_im = old_imgs_by_p.get(p_code) or {}
            img_obj = asdict(Image(
                p=p_code,
                file=rel_img,
                role=old_im.get("role") or "主体",
                desc=old_im.get("desc") or "",
                src=vpath.name,
            ))
            new_images.append(img_obj)

            try:
                rel_clip = vpath.relative_to(folder).as_posix()
            except ValueError:
                rel_clip = vpath.name

            old_sg = old_segs_by_s.get(idx) or {}
            dur = int(old_sg.get("duration") or _probe_duration(vpath))
            seg_obj = asdict(Segment(
                s=idx,
                text=old_sg.get("text") or "",
                duration=dur,
                image=p_code,
                role=old_sg.get("role") or "主体",
                shot=old_sg.get("shot") or "",
                prompt_final=old_sg.get("prompt_final") or "",
                status="collected",
                takes=[{
                    "t": 1,
                    "file": rel_clip,
                    "picked": True,
                    "engine": "external",
                    "at": now,
                }],
                flow_mode=old_sg.get("flow_mode") or "auto",
                preset_id=old_sg.get("preset_id") or "",
                custom_prompt=old_sg.get("custom_prompt") or "",
                extra_prompts=old_sg.get("extra_prompts") or [],
            ))
            new_segments.append(seg_obj)

        m.images = new_images
        m.segments = new_segments
        m.save(folder)

        meta = db.unj((existing_row or {}).get("meta"), {}) or {}
        meta["source_mode"] = "external_player"
        meta["player_saved"] = bool(meta.get("player_saved", False))
        meta["imported_at"] = now
        meta["seg_counts"] = taskmod.seg_counts(m)

        if existing_row:
            db.run(
                "UPDATE tasks SET name=?, status=?, dir=?, meta=?, updated=? WHERE id=?",
                (t_name, "generated", str(folder), db.j(meta), now, tid),
            )
        else:
            max_order = (db.one("SELECT COALESCE(MAX(row_order), 0) n FROM tasks") or {}).get("n") or 0
            db.run(
                """INSERT INTO tasks(
                     id, name, engine, account_id, type, copy, image_source,
                     duration_pref, expand_account_id, status, dir, batch_id,
                     row_order, created, updated, meta, gen_mode, flow_mode, preset_id
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    tid, t_name, "flow", "", "long", "", str(folder),
                    "8", "", "generated", str(folder), "",
                    int(max_order) + 1, now, now, db.j(meta), "first_frame", "auto", "",
                ),
            )
        imported_tasks.append(taskmod.get(tid))

    events.emit("tasks.changed")
    return {
        "ok": True,
        "imported_count": len(imported_tasks),
        "count": len(imported_tasks),
        "tasks": imported_tasks,
        "imported": imported_tasks,
    }


def save_as_project(task_ids: list[str] | None = None) -> dict:
    """将外部播放器中导入的临时审片任务正式保存为 Orante 项目：
    1. 标记 `meta['player_saved'] = True`，解除视图隔离，使其出现在「1 任务表」「2 排镜头」「4 生成」
    2. 在任务目录就地写出 `project.orproj.json` 工程文件
    """
    from . import project as proj_mod

    if task_ids:
        rows = [db.one("SELECT * FROM tasks WHERE id=?", (str(tid).strip(),)) for tid in task_ids]
        rows = [r for r in rows if r]
    else:
        all_rows = db.rows("SELECT * FROM tasks")
        rows = [
            r for r in all_rows
            if (db.unj(r.get("meta"), {}) or {}).get("source_mode") == "external_player"
        ]

    saved_ids: list[str] = []
    saved_tasks: list[dict] = []
    now = db.now()

    for r in rows:
        tid = r["id"]
        meta = db.unj(r.get("meta"), {}) or {}
        meta["player_saved"] = True
        meta["saved_as_project_at"] = now
        db.run("UPDATE tasks SET archived=0, meta=?, updated=? WHERE id=?", (db.j(meta), now, tid))
        try:
            proj_mod.write_task_orproj(tid)
        except Exception:
            pass
        saved_ids.append(tid)
        saved_tasks.append(taskmod.get(tid))

    events.emit("tasks.changed")
    return {
        "ok": True,
        "saved_count": len(saved_ids),
        "count": len(saved_ids),
        "task_ids": saved_ids,
        "tasks": saved_tasks,
    }
