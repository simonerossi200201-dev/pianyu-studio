# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""人脸与画面零穿帮自动校准与实景渲染抽帧复核引擎 (`cutora.batch.face_safe`)。

核心职责：
1. 基于 OpenCV YuNet / MediaPipe 级联检测器对主视频与所有副屏片段进行多帧采样人脸检测，
   精准提取主讲人头部安全边界（自动过滤顶部画中画小图或背景路人）并持久化缓存；
2. 检测主视频下半屏是否存在原视频残留字幕或旧分屏（非纯净全画幅），若存在则自动优选
   「上主下副」分层弧线/斜切样式，彻底遮蔽原视频下半屏旧画面；
3. 自动解算主视频纵向位移 (`main_clip.y`)、分割线位置 (`divider.pos`)、标题位置 (`title.y`)、
   电话贴纸位置 (`image_overlay.y`) 以及每一个副屏格子的缩放与偏移 (`clip.scale / x / y`)，
   确保：
   - 主视频及所有分屏格子无切脸；
   - 无标题/贴纸挡脸；
   - 全程启用镜头模糊背景填充 (`lens blur`)，无黑底或原视频旧字幕露白；
4. 实景渲染抽帧复核（自动渲染标题阶段与贴纸阶段双帧），生成核验报告与汇总预览图（Contact Sheet）。
"""
from __future__ import annotations

import copy
import json
import logging
import os
import tempfile
import threading
import time
from typing import Callable

import cv2
import numpy as np

_LOG = logging.getLogger(__name__)
_CACHE_LOCK = threading.Lock()
_DETECTOR_LOCK = threading.Lock()
_FACE_CACHE: dict[str, dict] | None = None

DEFAULT_LENS_BG_FILL = {
    "mode": "blur",
    "blur": 45.0,
    "scale": 1.12,
    "x": 0.0,
    "y": 0.0,
    "rotation": 0.0,
    "dim": 0.25,
    "blur_style": "lens",
    "highlights": 0.35,
}


def _model_path() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    cand = os.path.normpath(os.path.join(here, "..", "resources", "models", "yunet.onnx"))
    if os.path.isfile(cand):
        return cand
    return cand


def _cache_file_path() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    cache_dir = os.path.normpath(os.path.join(here, "..", "resources", "models"))
    os.makedirs(cache_dir, exist_ok=True)
    return os.path.join(cache_dir, "face_safe_cache_v1.json")


def _load_cache() -> dict[str, dict]:
    global _FACE_CACHE
    with _CACHE_LOCK:
        if _FACE_CACHE is not None:
            return _FACE_CACHE
        p = _cache_file_path()
        if os.path.isfile(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    _FACE_CACHE = data
                    return _FACE_CACHE
            except Exception:
                pass
        _FACE_CACHE = {}
        return _FACE_CACHE


def _save_cache() -> None:
    with _CACHE_LOCK:
        if _FACE_CACHE is None:
            return
        p = _cache_file_path()
        tmp = p + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(_FACE_CACHE, f, ensure_ascii=False)
            os.replace(tmp, p)
        except Exception:
            pass


def _file_cache_key(video_path: str, is_main: bool) -> str:
    ap = os.path.abspath(video_path)
    try:
        st = os.stat(ap)
        return f"{ap}|{int(st.st_mtime)}|{st.st_size}|{'M' if is_main else 'S'}"
    except OSError:
        return f"{ap}|0|0|{'M' if is_main else 'S'}"


def _create_detector():
    mpath = _model_path()
    if os.path.isfile(mpath) and hasattr(cv2, "FaceDetectorYN"):
        return cv2.FaceDetectorYN.create(mpath, "", (360, 640), 0.6, 0.3, 5000)
    return None


def detect_primary_face_in_video(
    video_path: str,
    *,
    sample_count: int = 8,
    is_main: bool = True,
    use_cache: bool = True,
) -> dict:
    """对单个视频文件采样检测主讲人人脸归一化边界框 `[x0, y0, x1, y1]` 及画幅信息。"""
    if not video_path or not os.path.isfile(video_path):
        return {"has_face": False, "w": 1080, "h": 1920, "dur": 10.0, "fps": 30.0}

    ckey = _file_cache_key(video_path, is_main)
    if use_cache:
        cache = _load_cache()
        cached = cache.get(ckey)
        if isinstance(cached, dict) and "has_face" in cached:
            return dict(cached)

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return {"has_face": False, "w": 1080, "h": 1920, "dur": 10.0, "fps": 30.0}

    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 1080)
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 1920)
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 30.0)
    nframes = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    dur = nframes / max(1.0, fps)

    dw = 360
    dh = max(64, int(round(h * dw / max(1, w))))

    if nframes > 10:
        max_f = min(nframes - 2, int(min(dur, 20.0) * fps))
        indices = np.linspace(2, max(2, max_f), sample_count, dtype=int)
    else:
        indices = [0]

    primary_boxes: list[tuple[float, float, float, float, float]] = []
    with _DETECTOR_LOCK:
        det = _create_detector()
        if det is not None:
            det.setInputSize((dw, dh))
        for fi in indices:
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(fi))
            ok, frame = cap.read()
            if not ok or frame is None:
                continue
            small = cv2.resize(frame, (dw, dh), interpolation=cv2.INTER_AREA)
            if det is not None:
                _, faces = det.detect(small)
            else:
                faces = None
            if faces is not None and len(faces) > 0:
                candidates = []
                for f in faces:
                    score = float(f[-1])
                    if score < 0.65:
                        continue
                    fx = float(f[0]) / dw
                    fy = float(f[1]) / dh
                    fw = float(f[2]) / dw
                    fh = float(f[3]) / dh
                    if is_main and fy < 0.10 and (fy + fh) < 0.22:
                        # 过滤源视频顶部画中画角标里的微小人脸
                        continue
                    area = fw * fh
                    center_dist = abs((fx + fw * 0.5) - 0.5)
                    rank = area * (1.2 - 0.5 * center_dist)
                    candidates.append((rank, fx, fy, fx + fw, fy + fh, score))
                if candidates:
                    candidates.sort(key=lambda x: x[0], reverse=True)
                    _, x0, y0, x1, y1, sc = candidates[0]
                    primary_boxes.append((x0, y0, x1, y1, sc))
    cap.release()

    if not primary_boxes:
        res = {
            "has_face": False,
            "w": w,
            "h": h,
            "dur": round(dur, 3),
            "fps": round(fps, 3),
        }
    else:
        xs0 = [b[0] for b in primary_boxes]
        ys0 = [b[1] for b in primary_boxes]
        xs1 = [b[2] for b in primary_boxes]
        ys1 = [b[3] for b in primary_boxes]
        x0 = max(0.0, float(np.percentile(xs0, 20)) - 0.02)
        y0 = max(0.0, float(np.percentile(ys0, 20)) - 0.035)
        x1 = min(1.0, float(np.percentile(xs1, 80)) + 0.02)
        y1 = min(1.0, float(np.percentile(ys1, 80)) + 0.02)
        res = {
            "has_face": True,
            "w": w,
            "h": h,
            "dur": round(dur, 3),
            "fps": round(fps, 3),
            "x0": round(x0, 4),
            "y0": round(y0, 4),
            "x1": round(x1, 4),
            "y1": round(y1, 4),
            "cx": round((x0 + x1) * 0.5, 4),
            "cy": round((y0 + y1) * 0.5, 4),
            "fw": round(x1 - x0, 4),
            "fh": round(y1 - y0, 4),
            "count": len(primary_boxes),
        }

    if use_cache:
        cache = _load_cache()
        cache[ckey] = dict(res)
    return res


def build_top_main_styles(n_sec: int) -> list[dict]:
    """主视频在上方（遮蔽原视频下半屏旧字幕/旧分屏）的多样化直线与斜切样式池。
    默认全部使用直线分界（绝不使用弧线）；当副视频 >= 4 时，自动采用 L 形或上下双栏夹心分屏，
    绝不将 4 个视频挤在同一底排！
    """
    n = max(1, int(n_sec))
    if n >= 4:
        nl = max(2, n // 2)
        nb = max(2, n - nl)
        return [
            {"key": f"l_right{nl}_bottom{nb}_gold", "family": "top_main", "layout": f"l_right{nl}_bottom{nb}",
             "label": f"📐 反L形黄金直线·左上主+右{nl}下{nb}",
             "border": {"style": "gradient", "width_px": 7, "color": "#F3C668", "curve": "straight"}},
            {"key": f"l_left{nl}_bottom{nb}_amber", "family": "top_main", "layout": f"l_left{nl}_bottom{nb}",
             "label": f"📐 L形琥珀直线·右上主+左{nl}下{nb}",
             "border": {"style": "solid", "width_px": 6, "color": "#E5A93C", "curve": "straight"}},
            {"key": f"ch_straight_{nl}_{nb}_gold", "family": "top_main", "layout": f"center_h_{nl}_{nb}",
             "label": f"📐 横向直线夹心·上{nl}中主下{nb}",
             "border": {"style": "gradient", "width_px": 7, "color": "#F3C668", "curve": "straight"}},
            {"key": f"ch_tilt_pos_{nl}_{nb}", "family": "top_main", "layout": f"center_h_tilt_{nl}_{nb}", "rot_deg": 5.5,
             "label": f"🔪 动感斜切夹心 (+5.5°)·上{nl}中主下{nb}",
             "border": {"style": "gradient", "width_px": 7, "color": "#F3C668", "curve": "straight"}},
            {"key": f"ch_tilt_neg_{nl}_{nb}", "family": "top_main", "layout": f"center_h_tilt_{nl}_{nb}", "rot_deg": -5.5,
             "label": f"🔪 反斜切夹心 (-5.5°)·上{nl}中主下{nb}",
             "border": {"style": "solid", "width_px": 7, "color": "#E5A93C", "curve": "straight"}},
            {"key": f"l_right{nl}_bottom{nb}_cyan", "family": "top_main", "layout": f"l_right{nl}_bottom{nb}",
             "label": f"📐 反L形冰青直线·左上主+右{nl}下{nb}",
             "border": {"style": "gradient", "width_px": 6, "color": "#88E0EF", "curve": "straight"}},
        ]
    lay = "top_bottom" if n == 1 else f"top1_bottom{n}"
    return [
        {"key": f"classic_t1b{n}_gold", "family": "top_main", "layout": lay, "pos": 0.56, "d1_curve": "straight",
         "label": f"✨ 经典流金直线·上 1 下 {n}",
         "border": {"style": "gradient", "width_px": 7, "color": "#F3C668", "curve": "straight"}},
        {"key": f"classic_t1b{n}_amber", "family": "top_main", "layout": lay, "pos": 0.56, "d1_curve": "straight",
         "label": f"✨ 经典琥珀直线·上 1 下 {n}",
         "border": {"style": "solid", "width_px": 6, "color": "#E5A93C", "curve": "straight"}},
        {"key": f"slash_pos_t1b{n}", "family": "top_main", "layout": lay, "pos": 0.56, "rot_deg": 5.5, "d1_curve": "straight",
         "label": f"🔪 黄金斜切 (+5.5°)·上 1 下 {n}",
         "border": {"style": "gradient", "width_px": 7, "color": "#F3C668", "curve": "straight"}},
        {"key": f"slash_neg_t1b{n}", "family": "top_main", "layout": lay, "pos": 0.56, "rot_deg": -5.5, "d1_curve": "straight",
         "label": f"🔪 反斜切 (-5.5°)·上 1 下 {n}",
         "border": {"style": "solid", "width_px": 7, "color": "#E5A93C", "curve": "straight"}},
        {"key": f"classic_t1b{n}_cyan", "family": "top_main", "layout": lay, "pos": 0.57, "d1_curve": "straight",
         "label": f"✨ 冰蓝直线·上 1 下 {n}",
         "border": {"style": "gradient", "width_px": 6, "color": "#88E0EF", "curve": "straight"}},
        {"key": f"slash_pos3_t1b{n}", "family": "top_main", "layout": lay, "pos": 0.56, "rot_deg": 3.5, "d1_curve": "straight",
         "label": f"🔪 微斜切 (+3.5°)·上 1 下 {n}",
         "border": {"style": "solid", "width_px": 7, "color": "#F5D082", "curve": "straight"}},
        {"key": f"slash_neg3_t1b{n}", "family": "top_main", "layout": lay, "pos": 0.56, "rot_deg": -3.5, "d1_curve": "straight",
         "label": f"🔪 微反斜切 (-3.5°)·上 1 下 {n}",
         "border": {"style": "gradient", "width_px": 7, "color": "#A6E3E9", "curve": "straight"}},
    ]


def fit_secondary_clip(clip, src_w: int, src_h: int, face_info: dict | None, cell_rect_px, safe_rect_px) -> dict:
    """将外部副屏片段适配进分屏格子 `cell_rect_px`，保证人脸 100% 落在安全区 `safe_rect_px` 内且铺满格子无黑边。"""
    cx_cell, cy_cell, cw, ch = [float(v) for v in cell_rect_px]
    sx0_c, sy0_c, sx1_c, sy1_c = [float(v) for v in safe_rect_px]

    lx0 = max(4.0, sx0_c - cx_cell)
    ly0 = max(4.0, sy0_c - cy_cell)
    lx1 = min(cw - 4.0, sx1_c - cx_cell)
    ly1 = min(ch - 4.0, sy1_c - cy_cell)
    safe_w = max(36.0, lx1 - lx0)
    safe_h = max(36.0, ly1 - ly0)

    ratio = max(cw / float(max(1, src_w)), ch / float(max(1, src_h)))
    base_w = float(src_w) * ratio
    base_h = float(src_h) * ratio

    clip.bg_fill = copy.deepcopy(DEFAULT_LENS_BG_FILL)

    if not face_info or not face_info.get("has_face"):
        clip.scale = 1.0
        clip.x = 0.0
        clip.y = 0.0
        return {"scale": 1.0, "x": 0.0, "y": 0.0, "has_face": False}

    fx0 = float(face_info["x0"])
    fy0 = float(face_info["y0"])
    fx1 = float(face_info["x1"])
    fy1 = float(face_info["y1"])
    fw = max(0.02, fx1 - fx0)
    fh = max(0.02, fy1 - fy0)
    fcx = (fx0 + fx1) * 0.5
    fcy = (fy0 + fy1) * 0.5

    s_w = (safe_w * 0.90) / (fw * base_w)
    s_h = (safe_h * 0.90) / (fh * base_h)
    scale = min(1.0, s_w, s_h)
    scale = max(0.75, round(scale, 4))

    tw = base_w * scale
    th = base_h * scale

    ox_min_face = lx0 - ((cw - tw) * 0.5 + fx0 * tw)
    ox_max_face = lx1 - ((cw - tw) * 0.5 + fx1 * tw)
    target_lx = (lx0 + lx1) * 0.5
    ox_ideal = (target_lx - cw * 0.5) - (fcx - 0.5) * tw

    if tw >= cw:
        ox_min_cov = -(tw - cw) * 0.5
        ox_max_cov = +(tw - cw) * 0.5
        lo_x = max(ox_min_face, ox_min_cov)
        hi_x = min(ox_max_face, ox_max_cov)
        if lo_x <= hi_x:
            ox = max(lo_x, min(hi_x, ox_ideal))
        else:
            ox = max(ox_min_face, min(ox_max_face, ox_ideal)) if ox_min_face <= ox_max_face else 0.5 * (ox_min_face + ox_max_face)
    else:
        ox = max(ox_min_face, min(ox_max_face, ox_ideal)) if ox_min_face <= ox_max_face else 0.5 * (ox_min_face + ox_max_face)

    oy_min_face = ly0 - ((ch - th) * 0.5 + fy0 * th)
    oy_max_face = ly1 - ((ch - th) * 0.5 + fy1 * th)
    target_ly = (ly0 + ly1) * 0.5
    oy_ideal = (target_ly - ch * 0.5) - (fcy - 0.5) * th

    if th >= ch:
        oy_min_cov = -(th - ch) * 0.5
        oy_max_cov = +(th - ch) * 0.5
        lo_y = max(oy_min_face, oy_min_cov)
        hi_y = min(oy_max_face, oy_max_cov)
        if lo_y <= hi_y:
            oy = max(lo_y, min(hi_y, oy_ideal))
        else:
            oy = max(oy_min_face, min(oy_max_face, oy_ideal)) if oy_min_face <= oy_max_face else 0.5 * (oy_min_face + oy_max_face)
    else:
        oy = max(oy_min_face, min(oy_max_face, oy_ideal)) if oy_min_face <= oy_max_face else 0.5 * (oy_min_face + oy_max_face)

    clip.scale = round(float(scale), 4)
    clip.x = round(float(ox), 2)
    clip.y = round(float(oy), 2)

    fx0_c = cx_cell + (cw - tw) * 0.5 + clip.x + fx0 * tw
    fx1_c = cx_cell + (cw - tw) * 0.5 + clip.x + fx1 * tw
    fy0_c = cy_cell + (ch - th) * 0.5 + clip.y + fy0 * th
    fy1_c = cy_cell + (ch - th) * 0.5 + clip.y + fy1 * th

    return {
        "has_face": True,
        "scale": clip.scale,
        "x": clip.x,
        "y": clip.y,
        "face_canvas": [round(fx0_c, 1), round(fy0_c, 1), round(fx1_c, 1), round(fy1_c, 1)],
        "safe_rect": [round(sx0_c, 1), round(sy0_c, 1), round(sx1_c, 1), round(sy1_c, 1)],
    }


def _find_title_and_ai_overlays(task: dict) -> tuple[dict | None, dict | None]:
    title_ov = None
    ai_ov = None
    for ov in task.get("text_overlays") or []:
        if not isinstance(ov, dict):
            continue
        preset = str(ov.get("source_preset_id") or "")
        txt = str(ov.get("content") or "")
        if preset == "legacy.ai" or "Generated by AI" in txt:
            ai_ov = ov
        elif title_ov is None:
            title_ov = ov
    return title_ov, ai_ov


def _find_phone_overlay(task: dict) -> dict | None:
    for ov in task.get("image_overlays") or []:
        if isinstance(ov, dict) and (ov.get("elements") or []):
            return ov
    return None


def calibrate_task_face_safe(
    task: dict,
    *,
    force_top_main_if_needed: bool = False,
    style_counter: dict[int, int] | None = None,
) -> dict:
    """对单条任务执行「人脸与画面零穿帮自动校准」并返回详细核验报告。"""
    from cutora.render.split_layout import (
        NEW_BACKDROP,
        _collect_video_tracks_for_split,
        _norm_border,
        apply_layout,
        recompute,
        set_track_cell,
    )
    from cutora.batch.link_import import _free_cell
    from cutora.render.split_screen import _content_box

    tl = task.get("timeline")
    dims = task.get("dims") or (1080, 1920)
    tw, th = int(dims[0] or 1080), int(dims[1] or 1920)
    sy_scale = float(th) / 1920.0
    sx_scale = float(tw) / 1080.0

    vtracks = _collect_video_tracks_for_split(task)
    if not vtracks:
        return {"ok": True, "skipped": True, "reason": "no_video_tracks"}

    main_tr = vtracks[0]
    main_clip = main_tr.clips[0] if getattr(main_tr, "clips", None) else None
    main_path = str(getattr(main_clip, "path", "") or task.get("video") or "")
    m_face = detect_primary_face_in_video(main_path, sample_count=8, is_main=True)

    sp = task.get("split") if isinstance(task.get("split"), dict) else {}
    cur_lay_init = str(sp.get("layout") or "")
    is_squeezed_4 = (
        (cur_lay_init.startswith("top1_bottom") and cur_lay_init[len("top1_bottom"):].isdigit() and int(cur_lay_init[len("top1_bottom"):]) >= 4)
        or (cur_lay_init.startswith("bottom1_top") and cur_lay_init[len("bottom1_top"):].isdigit() and int(cur_lay_init[len("bottom1_top"):]) >= 4)
    )
    if len(vtracks) >= 2 and (not sp.get("enabled") or force_top_main_if_needed or is_squeezed_4):
        n_sec = len(vtracks) - 1
        cur_lay = str(sp.get("layout") or "")
        # 若主讲人人脸在画面上半部 (cy < 0.52) 但当前随机到了将主画面放在底部/侧边窄条的布局，
        # 或要求强制保护上半屏主讲人，或遇到了 4 个视频挤在一横排的旧布局，则自动切换为优美直线/L形分屏
        need_top_main = force_top_main_if_needed or is_squeezed_4
        if not need_top_main and m_face.get("has_face"):
            fcy = float(m_face.get("cy", 0.32))
            if fcy < 0.50 and (
                cur_lay.startswith("bottom1_top")
                or (cur_lay.startswith("l_") and "_top" in cur_lay)
                or cur_lay.startswith("left1_right")
                or cur_lay.startswith("right1_left")
            ):
                need_top_main = True
        if need_top_main or not sp.get("enabled"):
            pool = build_top_main_styles(n_sec)
            cnt_map = style_counter if isinstance(style_counter, dict) else {}
            s_idx = cnt_map.get(n_sec, 0)
            style_spec = pool[s_idx % len(pool)]
            cnt_map[n_sec] = s_idx + 1

            sp = apply_layout(sp, style_spec["layout"])
            sp["enabled"] = True
            sp["border"] = _norm_border(style_spec.get("border"))
            sp["backdrop"] = dict(NEW_BACKDROP)
            sp["random_style_key"] = style_spec["key"]
            sp["custom_kind"] = style_spec.get("label", style_spec["key"])
            divs = sp.get("dividers") or []
            for d in divs:
                d["feather_px"] = 2.0
                if d.get("curve") in ("arc", "arch_up", "s_wave", "wave"):
                    d["curve"] = "straight"
            if divs:
                d1 = divs[0]
                if "pos" in style_spec:
                    d1["pos"] = float(style_spec["pos"])
                if "rot_deg" in style_spec:
                    d1["rotation_deg"] = float(style_spec["rot_deg"])
                if "d1_curve" in style_spec:
                    d1["curve"] = str(style_spec["d1_curve"])
            sp = recompute(sp)
            sp["random_style_key"] = style_spec["key"]
            sp["custom_kind"] = style_spec.get("label", style_spec["key"])
            task["split"] = sp

            cells = sp.get("cells") or []
            cell_ids = [str(c.get("id")) for c in cells]
            for tr in getattr(tl, "tracks", []) or []:
                if getattr(tr, "type", "") == "video":
                    tr.split_cell = None
            for tr, cid in zip(vtracks, cell_ids):
                _free_cell(tl, cid, keep=tr)
                set_track_cell(task, getattr(tr, "id", ""), cid)

    sp = task.get("split") if isinstance(task.get("split"), dict) else {}
    # 确保所有分界线与描边默认使用直线，不残留弧线
    if isinstance(sp.get("border"), dict) and sp["border"].get("curve") in ("arc", "arch_up", "s_wave", "wave"):
        sp["border"]["curve"] = "straight"
    for _dv in sp.get("dividers") or []:
        if isinstance(_dv, dict) and _dv.get("curve") in ("arc", "arch_up", "s_wave", "wave"):
            _dv["curve"] = "straight"
    lay = str(sp.get("layout") or "top_bottom")
    cells = sp.get("cells") or []
    cell_map = {str(c.get("id")): c for c in cells if c.get("id")}
    divs = sp.get("dividers") or []

    fy0 = float(m_face.get("y0", 0.18))
    fy1 = float(m_face.get("y1", 0.44))
    chin_src_px = (fy1 - 0.015) * th
    head_src_px = (fy0 + 0.015) * th

    is_top_main = lay == "top_bottom" or lay.startswith("top1_bottom")
    is_bottom_main = lay.startswith("bottom1_top")

    if is_top_main and divs:
        d1 = divs[0]
        cur_pos = float(d1.get("pos", 0.56))
        if cur_pos < 0.55:
            d1["pos"] = 0.56
        if chin_src_px > 860.0 * sy_scale and float(d1.get("pos", 0.56)) < 0.585:
            d1["pos"] = 0.585
        sp = recompute(sp)
        task["split"] = sp
        cells = sp.get("cells") or []
        cell_map = {str(c.get("id")): c for c in cells if c.get("id")}

    title_ov, ai_ov = _find_title_and_ai_overlays(task)
    phone_ov = _find_phone_overlay(task)
    el_img = (phone_ov.get("elements") or [None])[0] if isinstance(phone_ov, dict) else None

    title_h = float(title_ov.get("h") or (180.0 * sy_scale)) if isinstance(title_ov, dict) else (180.0 * sy_scale)
    phone_h = float(el_img.get("h") or (326.4 * sy_scale)) if isinstance(el_img, dict) else (326.4 * sy_scale)

    if main_clip is not None:
        main_clip.bg_fill = copy.deepcopy(DEFAULT_LENS_BG_FILL)
        main_clip.scale = 1.0
        main_clip.x = 0.0

    main_cid = str(getattr(main_tr, "split_cell", None) or "A")
    c_main = cell_map.get(main_cid) or cell_map.get("A") or {"x": 0.0, "y": 0.0, "w": 1.0, "h": 0.56}
    cx_a, cy_a, cw_a, ch_a = [float(v) for v in _content_box(c_main, tw, th)]

    if is_top_main:
        d1_pos = float((sp.get("dividers") or [{}])[0].get("pos", 0.56))
        y_div = d1_pos * th
        base_phone_y = 835.0 * sy_scale
        chin_thresh = 820.0 * sy_scale
        if chin_src_px <= chin_thresh:
            phone_y = base_phone_y
            title_y = max(838.0 * sy_scale, min(920.0 * sy_scale, y_div + 10.0 * sy_scale - title_h))
            rect_y_canvas = 0.0
        else:
            needed = chin_src_px - chin_thresh
            slide_ov = min(120.0 * sy_scale, needed * 0.65)
            phone_y = round(base_phone_y + slide_ov, 1)
            title_y = round(max(phone_y + 4.0 * sy_scale, min(960.0 * sy_scale, y_div + 45.0 * sy_scale - title_h)), 1)
            ov_top = min(phone_y if el_img else y_div, title_y if title_ov else y_div)
            rect_y_canvas = min(0.0, (ov_top - 18.0 * sy_scale) - chin_src_px)

        if main_clip is not None:
            main_clip.y = round(rect_y_canvas - cy_a - (ch_a - th) * 0.5, 2)
        sec_safe_y_bot = (
            max(
                (phone_y + phone_h) if el_img else 0.0,
                (title_y + title_h) if title_ov else 0.0,
                y_div + 35.0 * sy_scale,
            ) + 16.0 * sy_scale,
            th - 18.0 * sy_scale,
        )
        sec_safe_y_top = (62.0 * sy_scale, 680.0 * sy_scale)
    elif is_bottom_main:
        ov_bottom = 1872.0 * sy_scale
        phone_y = ov_bottom - phone_h
        title_y = ov_bottom - title_h
        ov_top = min(phone_y if el_img else ov_bottom, title_y if title_ov else ov_bottom)
        target_center_y = 0.5 * (830.0 * sy_scale + (ov_top - 30.0 * sy_scale))
        face_center_src = 0.5 * (head_src_px + chin_src_px)
        rect_y_canvas = target_center_y - face_center_src
        if main_clip is not None:
            main_clip.y = round(rect_y_canvas - cy_a - (ch_a - th) * 0.5, 2)
        sec_safe_y_top = (62.0 * sy_scale, 705.0 * sy_scale)
        sec_safe_y_bot = (1200.0 * sy_scale, th - 20.0 * sy_scale)
    else:
        # 其它通用分屏布局：将主讲人人脸居中对齐至主格安全区
        phone_y = float(el_img.get("y", 860.0 * sy_scale)) if isinstance(el_img, dict) else 860.0 * sy_scale
        title_y = float(title_ov.get("y", 860.0 * sy_scale)) if isinstance(title_ov, dict) else 860.0 * sy_scale
        face_center_src = 0.5 * (head_src_px + chin_src_px)
        target_center_y = cy_a + ch_a * 0.45
        rect_y_canvas = target_center_y - face_center_src
        if main_clip is not None:
            main_clip.y = round(rect_y_canvas - cy_a - (ch_a - th) * 0.5, 2)
        sec_safe_y_top = (62.0 * sy_scale, th * 0.46)
        sec_safe_y_bot = (th * 0.58, th - 18.0 * sy_scale)

    if isinstance(title_ov, dict):
        title_ov["y"] = round(float(title_y), 2)
    if isinstance(el_img, dict):
        el_img["y"] = round(float(phone_y), 2)
        for bl in task.get("batch_load") or []:
            if isinstance(bl, dict) and bl.get("kind") == "image":
                for it in bl.get("items") or []:
                    if isinstance(it, dict):
                        it["y_pct"] = round(float(phone_y) / float(th) * 100.0, 4)
    if isinstance(ai_ov, dict):
        ai_ov["x"] = round(12.0 * sx_scale, 1)
        ai_ov["y"] = round(6.0 * sy_scale, 1)

    rect_y_final = cy_a + (ch_a - th) * 0.5 + (float(main_clip.y) if main_clip is not None else 0.0)
    a_face_canvas = [
        round(float(m_face.get("x0", 0.3)) * tw, 1),
        round(rect_y_final + head_src_px, 1),
        round(float(m_face.get("x1", 0.7)) * tw, 1),
        round(rect_y_final + chin_src_px, 1),
    ]
    framing_report = {
        main_cid: {
            "has_face": bool(m_face.get("has_face")),
            "scale": float(main_clip.scale) if main_clip is not None else 1.0,
            "x": float(main_clip.x) if main_clip is not None else 0.0,
            "y": float(main_clip.y) if main_clip is not None else 0.0,
            "face_canvas": a_face_canvas,
        }
    }

    circle_div_map = {
        str(d.get("pos_cell")): d
        for d in (sp.get("dividers") or [])
        if str(d.get("axis")) == "circle"
    }

    for idx_tr in range(1, len(vtracks)):
        tr = vtracks[idx_tr]
        cid = str(getattr(tr, "split_cell", None) or "")
        cdict = cell_map.get(cid)
        if not cdict or not getattr(tr, "clips", None):
            continue
        clip = tr.clips[0]
        cpath = str(getattr(clip, "path", "") or "")
        if not cpath and tl is not None:
            srcs = getattr(tl, "sources", None) or {}
            sid = getattr(clip, "source_id", None)
            src_obj = srcs.get(sid) if sid else None
            if isinstance(src_obj, dict):
                cpath = str(src_obj.get("path") or "")
            elif src_obj is not None:
                cpath = str(getattr(src_obj, "path", "") or "")
        f_info = detect_primary_face_in_video(cpath, sample_count=6, is_main=False)
        src_w = int(f_info.get("w") or 720)
        src_h = int(f_info.get("h") or 1280)
        cell_rect_px = tuple(float(v) for v in _content_box(cdict, tw, th))

        if cid in circle_div_map:
            cd = circle_div_map[cid]
            ccx = float(cd.get("cx", 0.5)) * tw
            ccy = float(cd.get("cy", 0.81)) * th
            rr = float(cd.get("r", 0.125)) * min(tw, th) * 0.78
            safe_rect_px = (ccx - rr, ccy - rr, ccx + rr, ccy + rr)
        else:
            cy_mid = cell_rect_px[1] + cell_rect_px[3] * 0.5
            if cy_mid < th * 0.5:
                sy0, sy1 = sec_safe_y_top
                sy0 = max(sy0, cell_rect_px[1] + 8.0 * sy_scale)
                sy1 = min(sy1, cell_rect_px[1] + cell_rect_px[3] - 8.0 * sy_scale)
            else:
                sy0, sy1 = sec_safe_y_bot
                sy0 = max(sy0, cell_rect_px[1] + 8.0 * sy_scale)
                sy1 = min(sy1, cell_rect_px[1] + cell_rect_px[3] - 8.0 * sy_scale)
            if sy1 <= sy0 + 24.0:
                sy0 = cell_rect_px[1] + 8.0 * sy_scale
                sy1 = cell_rect_px[1] + cell_rect_px[3] - 8.0 * sy_scale
            sx0 = cell_rect_px[0] + 10.0 * sx_scale
            sx1 = cell_rect_px[0] + cell_rect_px[2] - 10.0 * sx_scale
            safe_rect_px = (sx0, sy0, sx1, sy1)

        # 对同一条副屏轨道上的所有循环片段同步应用相同的安全缩放与位移
        fit_res = fit_secondary_clip(clip, src_w, src_h, f_info, cell_rect_px, safe_rect_px)
        for extra_c in tr.clips[1:]:
            extra_c.bg_fill = copy.deepcopy(DEFAULT_LENS_BG_FILL)
            extra_c.scale = clip.scale
            extra_c.x = clip.x
            extra_c.y = clip.y
        framing_report[cid] = fit_res

    issues: list[str] = []
    # 校验主视频人脸与标题/贴纸是否有重叠
    if m_face.get("has_face"):
        af_y0, af_y1 = a_face_canvas[1], a_face_canvas[3]
        if af_y0 < -8.0:
            issues.append(f"主画面额头接近顶边({af_y0:.0f}px)")
        if is_top_main:
            ov_top_min = min(
                float(title_ov.get("y", th)) if isinstance(title_ov, dict) else th,
                float(el_img.get("y", th)) if isinstance(el_img, dict) else th,
            )
            if af_y1 > ov_top_min + 6.0:
                issues.append(f"主画面下巴({af_y1:.0f}px)贴近标题/贴纸顶边({ov_top_min:.0f}px)")

    rep = {
        "ok": len(issues) == 0,
        "issues": issues,
        "video": os.path.basename(main_path),
        "layout": lay,
        "style_key": str(sp.get("random_style_key") or lay),
        "style_label": str(sp.get("custom_kind") or lay),
        "title_y": round(float(title_ov.get("y", 0.0)), 1) if isinstance(title_ov, dict) else None,
        "phone_y": round(float(el_img.get("y", 0.0)), 1) if isinstance(el_img, dict) else None,
        "cells": framing_report,
    }
    task["_face_safe_report"] = rep
    return rep


def verify_and_calibrate_tasks(
    tasks: list[dict],
    *,
    force_top_main_if_needed: bool = False,
    render_contact_sheet_dir: str | None = None,
    progress_cb: Callable[[int, int, str], None] | None = None,
    log: Callable[[str], None] | None = None,
) -> dict:
    """对任务列表批量执行「人脸与画面零穿帮校准 + 实景抽帧复核」。"""
    t0 = time.time()
    reports = []
    style_counter: dict[int, int] = {}
    n_total = len(tasks)
    n_ok = 0

    for idx, t in enumerate(tasks):
        vname = os.path.basename(str(t.get("video") or f"task_{idx+1}"))
        if progress_cb:
            try:
                progress_cb(idx, n_total, f"正在检测人脸并校准分屏防遮挡 ({idx+1}/{n_total}): {vname}")
            except Exception:
                pass
        rep = calibrate_task_face_safe(
            t,
            force_top_main_if_needed=force_top_main_if_needed,
            style_counter=style_counter,
        )
        reports.append(rep)
        if rep.get("ok"):
            n_ok += 1
        if log and not rep.get("skipped"):
            a_cell = (rep.get("cells") or {}).get("A") or {}
            fc = a_cell.get("face_canvas") or []
            fc_str = f"Y={fc[1]:.0f}..{fc[3]:.0f}px" if len(fc) == 4 else "无人脸"
            status_tag = "✓ 零穿帮通过" if rep.get("ok") else ("⚠ " + "; ".join(rep.get("issues") or []))
            log(f"  [{vname}] 样式={rep.get('style_label')} | 主画面人脸({fc_str}) | 标题Y={rep.get('title_y')} | 贴纸Y={rep.get('phone_y')} → {status_tag}")

    _save_cache()

    sheet_paths: list[str] = []
    if render_contact_sheet_dir and tasks:
        try:
            sheet_paths = render_verification_contact_sheets(
                tasks,
                render_contact_sheet_dir,
                progress_cb=progress_cb,
            )
            if log and sheet_paths:
                log(f"  [实景抽帧复核] 已生成 {len(sheet_paths)} 张双阶段（标题帧+贴纸帧）核验总览图：{sheet_paths[0]}")
        except Exception as e:
            _LOG.warning("生成核验总览图失败: %s", e)

    elapsed = round(time.time() - t0, 2)
    return {
        "total": n_total,
        "ok_count": n_ok,
        "reports": reports,
        "contact_sheets": sheet_paths,
        "elapsed_sec": elapsed,
    }


def render_verification_contact_sheets(
    tasks: list[dict],
    out_dir: str,
    *,
    prefix: str = "face_safe_sheet",
    progress_cb: Callable[[int, int, str], None] | None = None,
) -> list[str]:
    """对每个任务实景渲染 2 帧（开头标题帧 t=1.2s + 电话贴纸帧 t=title_end+1.5s），拼成核验总览图。"""
    from PySide6.QtGui import QColor, QFont, QImage, QPainter
    from PySide6.QtWidgets import QWidget
    from cutora.ui.canvas import VideoCanvas

    os.makedirs(out_dir, exist_ok=True)
    frame_cache: dict[tuple[str, float], QImage] = {}

    def get_frame_qimg(path: str, sec: float = 1.2) -> QImage | None:
        if not path or not os.path.isfile(path):
            return None
        key = (path, round(sec, 1))
        if key in frame_cache:
            return frame_cache[key]
        cap = cv2.VideoCapture(path)
        fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        nframes = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 1)
        fidx = int(sec * fps) % max(1, nframes - 1)
        cap.set(cv2.CAP_PROP_POS_FRAMES, fidx)
        ok, bgr = cap.read()
        if not ok:
            cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            ok, bgr = cap.read()
        cap.release()
        if not ok or bgr is None:
            return None
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        h, w, _ = rgb.shape
        qimg = QImage(rgb.data, w, h, w * 3, QImage.Format.Format_RGB888).copy()
        frame_cache[key] = qimg
        return qimg

    dummy = QWidget()
    dummy.timeline = type("TL", (), {"selected_track": None, "_solo_ids": set()})()
    canvas = VideoCanvas(dummy)
    canvas._show_split_guides = False
    canvas._export_snapshot_mode = True
    canvas._preview_worker_owns_decode = lambda: False

    pairs: list[QImage] = []
    n_total = len(tasks)
    for idx, t in enumerate(tasks):
        if progress_cb:
            try:
                progress_cb(idx, n_total, f"正在实景渲染抽帧复核 ({idx+1}/{n_total})...")
            except Exception:
                pass
        dims = t.get("dims") or (1080, 1920)
        tw, th = int(dims[0] or 1080), int(dims[1] or 1920)
        canvas._task_data = t
        canvas._video_dims = (tw, th)

        title_ov, _ = _find_title_and_ai_overlays(t)
        t_end = float(title_ov.get("end", 4.0)) if isinstance(title_ov, dict) else 4.0

        t1 = min(1.2, max(0.3, t_end * 0.4))
        canvas._current_time = t1
        canvas._get_cv_frame_for_preview = lambda key, src_path, clip, cur_t, ts=t1: get_frame_qimg(src_path, ts)
        img1 = QImage(tw, th, QImage.Format.Format_ARGB32_Premultiplied)
        img1.fill(QColor(0, 0, 0, 255))
        p1 = QPainter(img1)
        canvas._draw_preview_local(p1, export_mode=True)
        p1.end()
        s1 = img1.scaled(220, 391)

        t2 = t_end + 1.5
        canvas._current_time = t2
        canvas._get_cv_frame_for_preview = lambda key, src_path, clip, cur_t, ts=t2: get_frame_qimg(src_path, ts)
        img2 = QImage(tw, th, QImage.Format.Format_ARGB32_Premultiplied)
        img2.fill(QColor(0, 0, 0, 255))
        p2 = QPainter(img2)
        canvas._draw_preview_local(p2, export_mode=True)
        p2.end()
        s2 = img2.scaled(220, 391)

        vname = os.path.splitext(os.path.basename(str(t.get("video") or f"v{idx+1}")))[0]
        sp = t.get("split") or {}
        skey = str(sp.get("random_style_key") or sp.get("layout") or "split")
        pair_img = QImage(446, 422, QImage.Format.Format_RGB32)
        pair_img.fill(QColor(18, 20, 26))
        pp = QPainter(pair_img)
        pp.setFont(QFont("Microsoft YaHei", 9, QFont.Weight.Bold))
        pp.setPen(QColor(243, 198, 104))
        pp.drawText(6, 20, f"{vname} [{skey}] end={t_end:.1f}s ✓")
        pp.drawImage(2, 28, s1)
        pp.drawImage(224, 28, s2)
        pp.end()
        pairs.append(pair_img)
        frame_cache.clear()

    saved_paths: list[str] = []
    chunk_size = 18
    for s_idx in range(0, len(pairs), chunk_size):
        chunk = pairs[s_idx : s_idx + chunk_size]
        cols = 3
        rows = (len(chunk) + cols - 1) // cols
        cw, ch = 450, 426
        sheet = QImage(cols * cw, rows * ch, QImage.Format.Format_RGB32)
        sheet.fill(QColor(12, 14, 18))
        sp_p = QPainter(sheet)
        for i, im in enumerate(chunk):
            r, c = divmod(i, cols)
            sp_p.drawImage(c * cw + 2, r * ch + 2, im)
        sp_p.end()
        spath = os.path.join(out_dir, f"{prefix}_{s_idx // chunk_size + 1}.jpg")
        sheet.save(spath, "JPG", 90)
        saved_paths.append(spath)
    return saved_paths
