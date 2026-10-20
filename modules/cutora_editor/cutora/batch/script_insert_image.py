# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""
script_insert_image.py — 批量加载（图片/视频叠到工程每条视频的 Images 轨道）
================================================================
由顶部菜单「批量 → 批量加载...」调用，也给无头
`--do insert_image` 用。

本模块不 import 主窗口（避免循环依赖）。

多平台副本：默认「一次性渲染」——字幕烧录只做一遍，导出时再用
FFmpeg 把各平台的图叠上去（图在字幕上面，更快）。
可选「多次单独渲染」：每个平台整段单独渲染，字幕按时间线盖在图片上。
成品文件名前加 Youtube_ / Instagram_ / TikTok_。
"""
from __future__ import annotations

import copy
import json
import os
import subprocess
import tempfile
import uuid

from cutora.core.config import FFMPEG_PATH, FFPROBE_PATH
from cutora.core.log import get_logger
from cutora.core.proc import popen as _no_window_popen, run as _no_window_run

_LOG = get_logger("cutora.batch.insert_image")

SCRIPT_TAG = "insert_image"
DEFAULT_IMAGE = r"C:\Users\Timco\Downloads\Bible_study.png"
# 找图对话框的建议起点（用户本机常用目录；不存在则只当占位提示）
SUGGESTED_IMAGE_DIR = r"D:\1_PROJECT\Tiktok_Pic"
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif")
RENDER_ONCE = "once"
RENDER_EACH = "each"
_PREFS_KEY_DIR = "insert_image_default_dir"
_PREFS_KEY_RENDER = "insert_image_render_mode"

# id, 界面名, 文件名前缀
PLATFORMS = (
    ("youtube", "Youtube", "Youtube"),
    ("instagram", "Instagram", "Instagram"),
    ("tiktok", "TikTok", "TikTok"),
)
PLATFORM_BY_ID = {p[0]: p for p in PLATFORMS}


def normalize_render_mode(value):
    """once=字幕烧一遍再叠图（默认，图在字幕上）；each=每平台单独渲染（字幕可盖住图）。"""
    mode = str(value or RENDER_ONCE).strip().lower()
    if mode in (RENDER_EACH, "multi", "multiple", "per_platform", "full"):
        return RENDER_EACH
    return RENDER_ONCE


def _prefs_path():
    from cutora.core.paths import settings_file
    return settings_file("app_settings.json")


def load_insert_prefs():
    out = {"default_dir": "", "render_mode": RENDER_ONCE}
    try:
        p = _prefs_path()
        if not os.path.isfile(p):
            return out
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return out
        d = str(data.get(_PREFS_KEY_DIR) or "").strip()
        if d:
            out["default_dir"] = d
        out["render_mode"] = normalize_render_mode(data.get(_PREFS_KEY_RENDER))
    except Exception:
        _LOG.exception("读取批量加载偏好失败")
    return out


def save_insert_prefs(default_dir=None, render_mode=None):
    try:
        p = _prefs_path()
        data = {}
        if os.path.isfile(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if not isinstance(data, dict):
                    data = {}
            except Exception:
                _LOG.exception("偏好文件损坏，将重写: %s", p)
                data = {}
        if default_dir is not None:
            data[_PREFS_KEY_DIR] = str(default_dir or "").strip()
        if render_mode is not None:
            data[_PREFS_KEY_RENDER] = normalize_render_mode(render_mode)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        _LOG.exception("保存批量加载偏好失败")


def file_dialog_start(current_path, default_dir=""):
    """QFileDialog 起点：已有文件/目录优先，否则用默认找图目录。"""
    cur = (current_path or "").strip().strip('"')
    if cur:
        if os.path.isfile(cur) or os.path.isdir(cur):
            return cur
        parent = os.path.dirname(cur)
        while parent:
            if os.path.isdir(parent):
                return parent
            nxt = os.path.dirname(parent)
            if nxt == parent:
                break
            parent = nxt
    for cand in (default_dir, SUGGESTED_IMAGE_DIR):
        d = (cand or "").strip().strip('"')
        if d and os.path.isdir(d):
            return d
    return ""


def parse_time(value, default=0.0):
    """秒数，或 'm:ss' / 'h:mm:ss'。"""
    if value is None or value == "":
        return float(default)
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip().replace("，", ":").replace(" ", "")
    if ":" in s:
        parts = s.split(":")
        try:
            nums = [float(p) if p else 0.0 for p in parts]
        except ValueError:
            return float(default)
        if len(nums) == 2:
            return nums[0] * 60.0 + nums[1]
        if len(nums) == 3:
            return nums[0] * 3600.0 + nums[1] * 60.0 + nums[2]
        return float(default)
    try:
        return float(s)
    except ValueError:
        return float(default)


def _timeline_of(task, host=None):
    if host is not None:
        fn = getattr(host, "_tl_of", None)
        if callable(fn):
            return fn(task)
    try:
        from cutora.ui.main_window import _tl_of
        return _tl_of(task)
    except Exception:
        return (task or {}).get("timeline")


def task_duration(task, host=None):
    try:
        d = float(task.get("duration") or task.get("trim_end_sec") or 0)
        if d > 0:
            return d
    except Exception:
        pass
    tl = _timeline_of(task, host)
    if tl is not None:
        for tr in getattr(tl, "tracks", []) or []:
            if getattr(tr, "type", "") == "video" and getattr(tr, "is_main_video", False):
                for c in getattr(tr, "clips", []) or []:
                    try:
                        return float(getattr(c, "out_t", 0) or 0) or float(
                            getattr(c, "effective_duration", 0) or 0)
                    except Exception:
                        pass
    video = task.get("video") or task.get("path") or ""
    if host is not None and hasattr(host, "probe_duration_sec") and video:
        try:
            p = host.probe_duration_sec(video)
            if p:
                return float(p)
        except Exception:
            pass
    return 0.0


def task_dims(task):
    dims = task.get("dims") or []
    if isinstance(dims, (list, tuple)) and len(dims) >= 2:
        try:
            w, h = int(dims[0]), int(dims[1])
            if w > 0 and h > 0:
                return w, h
        except Exception:
            pass
    tl = task.get("timeline")
    try:
        w = int(getattr(tl, "width", 0) or 0)
        h = int(getattr(tl, "height", 0) or 0)
        if w > 0 and h > 0:
            return w, h
    except Exception:
        pass
    return 720, 1280


def image_size(path):
    try:
        import cv2
        im = cv2.imread(path, cv2.IMREAD_UNCHANGED)
        if im is not None:
            return int(im.shape[1]), int(im.shape[0])
    except Exception:
        pass
    try:
        from PIL import Image
        with Image.open(path) as im:
            return int(im.width), int(im.height)
    except Exception:
        pass
    return 0, 0


def image_tracks(tl):
    tracks = getattr(tl, "tracks", None) or []
    return [t for t in tracks if getattr(t, "type", "") == "image"]


def ensure_image_track(tl, track_no):
    from cutora.model.timeline_model import Track
    n = max(1, int(track_no))
    its = image_tracks(tl)
    while len(its) < n:
        tr = Track("image")
        tl.add_track(tr)
        its = image_tracks(tl)
    return its[n - 1]


def layout_element(iw, ih, tw, th, fit="fill", scale=1.0, offset_x=0.0, offset_y=0.0):
    """返回图片元素在画布上的 x,y,w,h。"""
    from cutora.batch.batch_load import compute_fit_rect
    iw = max(1, int(iw or tw))
    ih = max(1, int(ih or th))
    tw = max(1, int(tw))
    th = max(1, int(th))
    return compute_fit_rect(iw, ih, tw, th, fit_mode=fit, scale=scale,
                            offset_x=offset_x, offset_y=offset_y)


def is_insert_overlay(ov):
    if not isinstance(ov, dict):
        return False
    if str(ov.get("id") or "").startswith("ov_insert_"):
        return True
    if ov.get("_script") == SCRIPT_TAG:
        return True
    return False


def _is_ours(ov, image_path):
    if is_insert_overlay(ov):
        return True
    if not isinstance(ov, dict):
        return False
    els = ov.get("elements") or []
    if els and isinstance(els[0], dict):
        p = els[0].get("path") or ""
        if p and image_path and os.path.normcase(os.path.abspath(p)) == \
                os.path.normcase(os.path.abspath(image_path)):
            return True
    return False


def _anim_family(anim) -> str:
    a = str(anim or "none").lower().strip()
    if a in ("", "none", "off"):
        return ""
    if a.startswith("slide"):
        d = a.split("+", 1)[1] if "+" in a else "left"
        d = {"left": "left", "right": "right", "up": "up", "top": "up", "down": "down", "bottom": "down"}.get(d, "left")
        return "slide_" + d
    if "pop" in a or "scale" in a or "zoom" in a or "bounce" in a:
        return "pop"
    if "sway" in a or "shake" in a or "swing" in a:
        return "sway"
    return "fade"


def transition_from_anims(anim_in, anim_out, duration=None) -> tuple:
    """Q158：批量加载的「入场 / 出场动画」→ 图片真正会画的 ``transition_preset``。

    渲染图片层只认 ``transition_preset`` / ``transition_duration``（anim_engine.TRANSITION_PRESETS），
    以前批量写的 ``anim_in`` / ``anim_out`` 没人读，开头淡入淡出一直不生效。
    返回 (preset, duration)。"""
    from cutora.render.anim_engine import DEFAULT_TRANSITION_DURATION, TRANSITION_PRESETS
    fi, fo = _anim_family(anim_in), _anim_family(anim_out)
    if not fi and not fo:
        return "none", float(duration or DEFAULT_TRANSITION_DURATION)
    fam = fi or fo
    scope = "both" if (fi and fo) else ("in" if fi else "out")
    preset = "%s_%s" % (fam, scope)
    valid = {v for _l, v in TRANSITION_PRESETS}
    if preset not in valid:
        preset = "fade_%s" % scope
    return preset, float(duration or DEFAULT_TRANSITION_DURATION)


def stamp_transition(ov, anim_in, anim_out, duration=None) -> dict:
    preset, dur = transition_from_anims(anim_in, anim_out, duration)
    ov["anim_in"] = anim_in or "none"
    ov["anim_out"] = anim_out or "none"
    ov["transition_preset"] = preset
    ov["transition_duration"] = dur
    return ov


def make_overlay(path, start, end, tw, th, iw, ih, fit, scale, x, y, track_id, track_no,
                 blend="normal", anim_in="fade", anim_out="fade"):
    ex, ey, ew, eh = layout_element(iw, ih, tw, th, fit=fit, scale=scale,
                                    offset_x=x, offset_y=y)
    blend = blend or "normal"
    return {
        "type": "image",
        "start": float(start),
        "end": float(end),
        "x": 0,
        "y": 0,
        "w": int(tw),
        "h": int(th),
        "rotation": 0,
        "opacity": 255,
        "elements": [{
            "kind": "image",
            "path": path,
            "x": float(ex),
            "y": float(ey),
            "w": float(ew),
            "h": float(eh),
            "rotation": 0.0,
            "opacity": 255.0,
            "blend_mode": blend,
            "lock_aspect": (fit or "fill").lower() != "stretch",
        }],
        "scale": 1.0,
        "flip_x": False,
        "flip_y": False,
        "lock_aspect": (fit or "fill").lower() != "stretch",
        "keep_aspect": (fit or "fill").lower() != "stretch",
        "aspect_ratio": (iw / max(1, ih)) if iw and ih else (tw / max(1, th)),
        "blend_mode": blend,
        "anim_in": anim_in or "fade",
        "anim_out": anim_out or "fade",
        # Q158：开头 / 结尾的淡入淡出等 = 图片层真正会画的转场（见 transition_from_anims）
        "transition_preset": transition_from_anims(anim_in, anim_out)[0],
        "transition_duration": transition_from_anims(anim_in, anim_out)[1],
        "id": "ov_insert_%s" % uuid.uuid4().hex[:12],
        "track_index": int(track_no),
        "_track_id": track_id,
        "_script": SCRIPT_TAG,
    }


# ---------------------------------------------------------------------------
#  多平台副本
# ---------------------------------------------------------------------------

def _si():
    if os.name == "nt":
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        return si
    return None


def _run(cmd, timeout=None):
    return _no_window_run(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        startupinfo=_si(), timeout=timeout,
    )


def prefixed_output_path(path, prefix):
    """给成品文件名加上平台前缀。已有该前缀则不重复加。"""
    if not path or not prefix:
        return path
    d, name = os.path.split(path)
    lead = str(prefix) + "_"
    if name.startswith(lead):
        return path
    return os.path.join(d, lead + name)


def platform_dest_paths(base_path, copies):
    """base_path 可能是未加前缀的，也可能已经是第一份平台成品。"""
    copies = list(copies or [])
    if not copies:
        return []
    d, name = os.path.split(base_path)
    first = (copies[0].get("prefix") or copies[0].get("id") or "") + "_"
    rest = name[len(first):] if first and name.startswith(first) else name
    out = []
    for p in copies:
        prefix = p.get("prefix") or p.get("id") or "Platform"
        out.append(os.path.join(d, "%s_%s" % (prefix, rest)))
    return out


def resolve_export_output_path(task, base_path):
    """批量导出主输出路径：开了多平台副本时用第一份平台成品的路径。"""
    copies = active_copies(task)
    if not copies:
        return base_path
    return prefixed_output_path(base_path, copies[0].get("prefix") or copies[0].get("id"))


def _valid_image(path):
    if not path:
        return False
    p = os.path.abspath(str(path).strip().strip('"'))
    if not os.path.isfile(p):
        return False
    return os.path.splitext(p)[1].lower() in IMAGE_EXTS


def normalize_platform_copies(raw, start_sec=60.0, end_pad_sec=0.0, end_sec=None,
                              fit="fill", scale=1.0, x=0.0, y=0.0, image_track=1,
                              render_mode=None):
    """把界面 / CLI 给的多平台配置收成可落盘的结构。"""
    if not raw:
        return None
    platforms_in = []
    if isinstance(raw, dict):
        if raw.get("enabled") is False:
            return None
        if raw.get("render_mode") not in (None, ""):
            render_mode = raw.get("render_mode")
        inner = raw.get("platforms")
        if isinstance(inner, (list, tuple)):
            platforms_in = list(inner)
        else:
            # 简写: {"youtube": "a.png", "instagram": "b.png"}
            src = inner if isinstance(inner, dict) else raw
            for pid, _label, prefix in PLATFORMS:
                img = src.get(pid) or src.get(prefix) or src.get(_label)
                if img:
                    platforms_in.append({"id": pid, "image": img})
    elif isinstance(raw, (list, tuple)):
        platforms_in = list(raw)
    else:
        return None

    platforms = []
    seen = set()
    for item in platforms_in:
        if not isinstance(item, dict):
            continue
        pid = str(item.get("id") or item.get("prefix") or "").strip().lower()
        spec = PLATFORM_BY_ID.get(pid)
        if spec is None:
            for k, v in PLATFORM_BY_ID.items():
                if pid in (k, v[1].lower(), v[2].lower()):
                    spec = v
                    break
        if spec is None:
            continue
        if spec[0] in seen:
            continue
        img = item.get("image") or item.get("path") or ""
        img = os.path.abspath(str(img).strip().strip('"')) if img else ""
        if not _valid_image(img):
            raise ValueError("平台 %s 的图片无效：%s" % (spec[1], img or "(空)"))
        seen.add(spec[0])
        platforms.append({
            "id": spec[0],
            "name": spec[1],
            "prefix": spec[2],
            "image": img,
        })
    if not platforms:
        return None
    fit = (fit or "fill").lower()
    if fit not in ("fill", "fit", "stretch", "source"):
        fit = "fill"
    return {
        "enabled": True,
        "start_sec": float(start_sec),
        "end_pad_sec": float(end_pad_sec),
        "end_sec": None if end_sec in (None, "") else float(end_sec),
        "fit": fit,
        "scale": float(scale if scale not in (None, "") else 1.0),
        "x": float(x or 0),
        "y": float(y or 0),
        "image_track": max(1, int(image_track)),
        "render_mode": normalize_render_mode(render_mode),
        "platforms": platforms,
    }


def active_copies(task):
    """导出时真正要出的平台列表。没开或没有有效图片则返回空。"""
    cfg = (task or {}).get("platform_copies") if isinstance(task, dict) else None
    if not isinstance(cfg, dict) or not cfg.get("enabled"):
        return []
    out = []
    for p in cfg.get("platforms") or []:
        if not isinstance(p, dict):
            continue
        img = p.get("image") or p.get("path") or ""
        if not _valid_image(img):
            continue
        pid = str(p.get("id") or "").strip().lower()
        spec = PLATFORM_BY_ID.get(pid)
        prefix = p.get("prefix") or (spec[2] if spec else None) or p.get("name") or pid or "Platform"
        name = p.get("name") or (spec[1] if spec else prefix)
        out.append({
            "id": spec[0] if spec else pid,
            "name": name,
            "prefix": prefix,
            "image": os.path.abspath(img),
        })
    return out


def copies_layout(task):
    cfg = (task or {}).get("platform_copies") if isinstance(task, dict) else {}
    if not isinstance(cfg, dict):
        cfg = {}
    return {
        "start_sec": float(cfg.get("start_sec") if cfg.get("start_sec") is not None else 60.0),
        "end_pad_sec": float(cfg.get("end_pad_sec") or 0.0),
        "end_sec": None if cfg.get("end_sec") in (None, "") else float(cfg.get("end_sec")),
        "fit": (cfg.get("fit") or "fill"),
        "scale": float(cfg.get("scale") if cfg.get("scale") is not None else 1.0),
        "x": float(cfg.get("x") or 0),
        "y": float(cfg.get("y") or 0),
    }


def copies_render_mode(task):
    cfg = (task or {}).get("platform_copies") if isinstance(task, dict) else None
    if not isinstance(cfg, dict):
        return RENDER_ONCE
    return normalize_render_mode(cfg.get("render_mode"))


def task_with_platform_overlay(task, copy):
    """浅拷贝 task，把批量插图换成该平台的图，并关掉副本标记。

    给「多次单独渲染」用：时间线按正常层序画（字幕在图上面）。
    """
    t = dict(task or {})
    t.pop("platform_copies", None)
    img = (copy or {}).get("image") or (copy or {}).get("path") or ""
    img = os.path.abspath(str(img).strip().strip('"')) if img else ""
    if not _valid_image(img):
        raise ValueError("平台图片无效：%s" % (img or "(空)"))
    layout = copies_layout(task)
    iw, ih = image_size(img)
    tw, th = task_dims(t)
    ovs = list(t.get("image_overlays") or [])
    new_ovs = []
    replaced = False
    for ov in ovs:
        if not is_insert_overlay(ov):
            new_ovs.append(ov)
            continue
        replaced = True
        start = ov.get("start", layout.get("start_sec") or 0)
        end = ov.get("end")
        if end in (None, ""):
            end = start
        try:
            ov_w = int(ov.get("w") or tw)
            ov_h = int(ov.get("h") or th)
        except Exception:
            ov_w, ov_h = tw, th
        if ov_w <= 0:
            ov_w = tw
        if ov_h <= 0:
            ov_h = th
        if iw <= 0 or ih <= 0:
            iw, ih = ov_w, ov_h
        new_ovs.append(make_overlay(
            img, start, end, ov_w, ov_h, iw, ih,
            layout.get("fit") or "fill",
            layout.get("scale"), layout.get("x"), layout.get("y"),
            ov.get("_track_id"), ov.get("track_index") or 1))
    if not replaced:
        start = float(layout.get("start_sec") or 0)
        abs_end = layout.get("end_sec")
        dur = task_duration(t)
        if abs_end not in (None, ""):
            end = float(abs_end)
        else:
            end = max(0.0, dur - float(layout.get("end_pad_sec") or 0))
        if iw <= 0 or ih <= 0:
            iw, ih = tw, th
        new_ovs.append(make_overlay(
            img, start, end, tw, th, iw, ih,
            layout.get("fit"), layout.get("scale"),
            layout.get("x"), layout.get("y"),
            None, 1))
    t["image_overlays"] = new_ovs
    return t


def task_without_insert_overlays(task):
    """浅拷贝 task，去掉本脚本插入的图，供字幕烧录那一遍使用。"""
    t = dict(task or {})
    t["image_overlays"] = [
        ov for ov in (task.get("image_overlays") or [])
        if not is_insert_overlay(ov)
    ]
    return t


def probe_video_wh(path):
    try:
        res = _run([
            FFPROBE_PATH, "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-of", "csv=p=0", path,
        ])
        txt = (res.stdout or b"").decode("utf-8", errors="ignore").strip()
        parts = txt.replace("x", ",").split(",")
        if len(parts) >= 2:
            w, h = int(float(parts[0])), int(float(parts[1]))
            if w > 0 and h > 0:
                return w, h
    except Exception:
        pass
    return 0, 0


def probe_video_duration(path):
    try:
        res = _run([
            FFPROBE_PATH, "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", path,
        ])
        val = (res.stdout or b"").decode("utf-8", errors="ignore").strip()
        d = float(val) if val else 0.0
        return d if d > 0 else 0.0
    except Exception:
        return 0.0


def _has_audio(path):
    try:
        res = _run([
            FFPROBE_PATH, "-v", "error", "-select_streams", "a:0",
            "-show_entries", "stream=codec_type",
            "-of", "csv=p=0", path,
        ])
        return bool((res.stdout or b"").strip())
    except Exception:
        return False


def _video_encode_args(use_gpu, export_mode):
    if use_gpu:
        if export_mode == "original":
            return ["-c:v", "h264_nvenc", "-preset", "p4",
                    "-rc", "constqp", "-qp", "0", "-pix_fmt", "yuv420p"]
        return ["-c:v", "h264_nvenc", "-preset", "p4",
                "-rc", "vbr", "-cq", "18", "-b:v", "0", "-pix_fmt", "yuv420p"]
    if export_mode == "original":
        return ["-c:v", "libx264", "-preset", "ultrafast",
                "-crf", "0", "-pix_fmt", "yuv420p"]
    return ["-c:v", "libx264", "-preset", "veryfast",
            "-crf", "18", "-pix_fmt", "yuv420p"]


def overlay_image_on_video(
        video_path, image_path, dest_path, layout, use_gpu=False,
        export_mode="compressed", video_wh=None, duration=None):
    """把一张静图叠到已经烧好字幕的视频上。音频尽量 copy。"""
    if not os.path.isfile(video_path):
        return False, "找不到已渲染视频：%s" % video_path
    if not _valid_image(image_path):
        return False, "找不到平台图片：%s" % image_path

    tw, th = video_wh if video_wh else (0, 0)
    if tw <= 0 or th <= 0:
        tw, th = probe_video_wh(video_path)
    if tw <= 0 or th <= 0:
        tw, th = 720, 1280

    iw, ih = image_size(image_path)
    if iw <= 0 or ih <= 0:
        iw, ih = tw, th

    fit = (layout or {}).get("fit") or "fill"
    scale = float((layout or {}).get("scale") or 1.0)
    x = float((layout or {}).get("x") or 0)
    y = float((layout or {}).get("y") or 0)
    ex, ey, ew, eh = layout_element(iw, ih, tw, th, fit=fit, scale=scale,
                                    offset_x=x, offset_y=y)
    ew_i = max(2, int(round(ew)) // 2 * 2)
    eh_i = max(2, int(round(eh)) // 2 * 2)
    ex_i = int(round(ex))
    ey_i = int(round(ey))

    start = max(0.0, float((layout or {}).get("start_sec") or 0))
    trim = float((layout or {}).get("trim_start_sec") or 0)
    start = max(0.0, start - trim)
    dur = duration if duration and duration > 0 else probe_video_duration(video_path)
    abs_end = (layout or {}).get("end_sec")
    end_pad = float((layout or {}).get("end_pad_sec") or 0)
    if abs_end not in (None, ""):
        end = max(start, float(abs_end) - trim)
    else:
        end = (dur - end_pad) if dur > 0 else 0.0
    if dur > 0:
        end = min(end, dur) if end > 0 else dur

    if dur > 0 and start >= dur - 0.05:
        # 图根本不会出现：直接复制一份成品，避免空导出
        try:
            import shutil
            os.makedirs(os.path.dirname(dest_path) or ".", exist_ok=True)
            shutil.copy2(video_path, dest_path)
            return True, None
        except Exception as e:
            return False, str(e)

    if end <= 0 or (dur > 0 and end >= dur - 0.05):
        enable = "gte(t,%.3f)" % start if start > 0.02 else None
    else:
        enable = "between(t,%.3f,%.3f)" % (start, end)

    # scale 后叠到指定位置。format=rgba 保留 PNG 透明。
    img_f = "[1:v]scale=%d:%d:flags=lanczos,format=rgba,setsar=1[img]" % (ew_i, eh_i)
    ov = "[0:v][img]overlay=x=%d:y=%d" % (ex_i, ey_i)
    if enable:
        ov += ":enable='%s'" % enable
    ov += ":format=auto[vout]"
    fc = img_f + ";" + ov

    os.makedirs(os.path.dirname(dest_path) or ".", exist_ok=True)
    has_a = _has_audio(video_path)

    def _build(use_nvenc, audio_copy):
        cmd = [
            FFMPEG_PATH, "-y",
            "-i", video_path,
            "-i", image_path,
            "-filter_complex", fc,
            "-map", "[vout]",
        ]
        if has_a:
            cmd += ["-map", "0:a"]
            if audio_copy:
                cmd += ["-c:a", "copy"]
            else:
                cmd += ["-c:a", "aac", "-b:a", "192k"]
        else:
            cmd += ["-an"]
        cmd += _video_encode_args(use_nvenc, export_mode)
        cmd += ["-movflags", "+faststart", dest_path]
        return cmd

    attempts = []
    if use_gpu:
        attempts.append((True, True))
        attempts.append((True, False))
    attempts.append((False, True))
    attempts.append((False, False))

    last_err = ""
    for nv, acopy in attempts:
        res = _run(_build(nv, acopy))
        if res.returncode == 0 and os.path.isfile(dest_path) and os.path.getsize(dest_path) > 1024:
            return True, None
        last_err = (res.stderr or b"").decode("utf-8", errors="ignore")[-800:]
        try:
            if os.path.isfile(dest_path):
                os.remove(dest_path)
        except Exception:
            pass
        if nv:
            print("[平台副本] NVENC 叠图失败，改 CPU 重试")
    return False, last_err or "FFmpeg 叠图失败"


def export_platform_copies(
        primary_out, task, copies=None, use_gpu=False,
        export_mode="compressed", log=None):
    """字幕成品已经写在 primary_out（无插图）。按平台叠图，写出带前缀的多份。

    返回 (produced_paths, err)。成功时 err 为 None。
    """
    copies = list(copies if copies is not None else active_copies(task))
    if not copies:
        return [primary_out], None
    if not os.path.isfile(primary_out):
        return [], "找不到字幕成品：%s" % primary_out

    dests = platform_dest_paths(primary_out, copies)
    layout = copies_layout(task)
    layout["trim_start_sec"] = float((task or {}).get("trim_start_sec") or 0)

    fd, tmp = tempfile.mkstemp(suffix=".platbase.mp4")
    os.close(fd)
    try:
        os.replace(primary_out, tmp)
    except Exception:
        import shutil
        shutil.copy2(primary_out, tmp)
        try:
            os.remove(primary_out)
        except Exception:
            pass

    video_wh = probe_video_wh(tmp)
    duration = probe_video_duration(tmp)
    produced = []
    try:
        for p, dest in zip(copies, dests):
            if log:
                log("[平台副本] %s  叠图 %s" % (
                    p.get("prefix") or p.get("name"), os.path.basename(p["image"])))
            ok, err = overlay_image_on_video(
                tmp, p["image"], dest, layout,
                use_gpu=use_gpu, export_mode=export_mode,
                video_wh=video_wh, duration=duration)
            if not ok:
                # 尽量把无图半成品还回去，方便重试
                try:
                    if not os.path.isfile(primary_out):
                        import shutil
                        shutil.copy2(tmp, primary_out)
                except Exception:
                    pass
                return produced, err or ("平台 %s 叠图失败" % (p.get("name") or "?"))
            produced.append(dest)
            if log:
                log("[平台副本] → %s" % dest)
    finally:
        try:
            os.remove(tmp)
        except Exception:
            pass

    if primary_out not in produced and produced:
        # 主输出路径如果就是第一份平台成品，上面 overlay 已经写回去了。
        # 否则第一份在 dests[0]，调用方用 produced 即可。
        pass
    if not os.path.isfile(primary_out) and produced:
        # 并行导出以 primary_out 是否存在判断成功。第一份若路径不同，复制一份过去。
        if os.path.normcase(os.path.abspath(produced[0])) != os.path.normcase(os.path.abspath(primary_out)):
            try:
                import shutil
                shutil.copy2(produced[0], primary_out)
            except Exception:
                pass
    return produced, None


def insert_into_tasks(
        tasks,
        image_path,
        start_sec=60.0,
        end_pad_sec=0.0,
        end_sec=None,
        image_track=1,
        fit="fill",
        scale=1.0,
        x=0.0,
        y=0.0,
        replace=True,
        host=None,
        log=None,
        platform_copies=None,
        video_loop=True,
        video_mute=True,
        blend="normal",
        split=None,
        span_of=None,
):
    """把一张图 / 一段视频加到每条任务上。

    Q143：视频（mp4/mov…）不再塞进图片覆层（那样只剩一张静帧，导出时还可能
    是空的），而是放进主画面上面那条「叠加」视频轨：真的会播、可以循环、可以
    静音、可以分屏。``split`` = {"layout", "border", "feather", "main_cell"
    （原视频格子）, "insert_cell"（插入视频格子）} 时顺便开分屏。
    """
    if not tasks:
        raise ValueError("工程里没有视频任务")

    start_sec = parse_time(start_sec, 60.0)
    end_pad_sec = parse_time(end_pad_sec, 0.0)
    abs_end = None if end_sec in (None, "") else parse_time(end_sec, 0.0)
    track_no = max(1, int(image_track))
    fit = (fit or "fill").lower()
    if fit not in ("fill", "fit", "stretch", "source"):
        fit = "fill"
    scale = float(scale if scale not in (None, "") else 1.0)
    x = float(x or 0)
    y = float(y or 0)

    copies_cfg = None
    if platform_copies:
        copies_cfg = normalize_platform_copies(
            platform_copies,
            start_sec=start_sec,
            end_pad_sec=end_pad_sec,
            end_sec=abs_end,
            fit=fit,
            scale=scale,
            x=x,
            y=y,
            image_track=track_no,
        )
        # 开了多平台副本时，时间线必须用用户勾选的平台图，
        # 不能沿用对话框顶部那张默认图（例如 Bible_study.png）。
        if copies_cfg:
            image_path = copies_cfg["platforms"][0]["image"]

    image_path = os.path.abspath(image_path)
    if not os.path.isfile(image_path):
        raise ValueError("找不到素材：%s" % image_path)
    ext = os.path.splitext(image_path)[1].lower()
    from cutora.batch.batch_load import VIDEO_EXTS
    if ext not in IMAGE_EXTS and ext not in VIDEO_EXTS:
        raise ValueError("不是支持的图片/视频格式：%s" % ext)
    if ext in VIDEO_EXTS:
        iw, ih = probe_video_wh(image_path)
        if iw <= 0 or ih <= 0:
            iw, ih = 1080, 1920
    else:
        iw, ih = image_size(image_path)

    placed = 0
    per_task = []
    used_split_history: list[str] = []
    for task in tasks:
        name = os.path.basename(task.get("video") or "?")
        dur = task_duration(task, host)
        if dur <= 0:
            raise ValueError("读不到任务时长：%s" % name)
        followed = None
        if callable(span_of):
            try:
                followed = span_of(task, dur)
            except Exception:
                followed = None
        if followed:
            start = max(0.0, float(followed[0]))
            end = min(float(followed[1]), dur)
        else:
            start = max(0.0, float(start_sec))
            if abs_end is not None and abs_end > 0:
                end = min(float(abs_end), dur)
            else:
                end = max(0.0, dur - float(end_pad_sec))
        if end <= start + 0.05:
            raise ValueError(
                "%s 的可见区间无效：start=%.2f end=%.2f dur=%.2f" % (
                    name, start, end, dur))
        tw, th = task_dims(task)
        tl = _timeline_of(task, host)
        from cutora.media.animated import classify_animated, import_animated_as_clip
        animated = classify_animated(image_path)
        is_video = (not animated) and ext in VIDEO_EXTS
        track_id = None
        if tl is not None:
            tw = int(getattr(tl, "width", tw) or tw)
            th = int(getattr(tl, "height", th) or th)
            if not animated and not is_video:
                tr = ensure_image_track(tl, track_no)
                track_id = getattr(tr, "id", None)
        placed_track = None
        if is_video:
            if tl is None:
                raise ValueError("当前任务没有时间线，无法放入视频")
            placed_track = _place_video_clip(
                task, tl, image_path, start, end, fit=fit, scale=scale, x=x, y=y,
                loop=bool(video_loop), mute=bool(video_mute), blend=blend,
                replace=replace)
            if split:
                from cutora.batch.split_assign import (
                    SPLIT_RANDOM_BY_COUNT,
                    assign_random_split_for_task,
                    assign_split,
                    layout_cells,
                )
                from cutora.batch.link_import import main_video_track
                lay = split.get("layout") or "top_bottom"
                use_fs = bool(split.get("face_safe", True))
                if lay == SPLIT_RANDOM_BY_COUNT or split.get("random_by_count"):
                    res_sp = assign_random_split_for_task(
                        task, None,
                        used_history=used_split_history,
                        border=split.get("border"),
                        feather_px=split.get("feather"),
                        backdrop=split.get("backdrop"),
                        shuffle_cells=True,
                        face_safe=use_fs,
                    )
                    if log:
                        log("    🎲 随机分屏（%d条视频轨）→ %s%s" % (
                            res_sp.get("n_videos", 2), res_sp.get("style_label", lay),
                            " · 🛡️人脸零穿帮已校准" if use_fs else ""))
                else:
                    cells = layout_cells(lay)
                    main_cell = split.get("main_cell") or cells[0]
                    ins_cell = split.get("insert_cell") or (cells[1] if len(cells) > 1 else cells[0])
                    order = [c for c in (main_cell, ins_cell) if c in cells]
                    assign_split(
                        task, lay, [main_video_track(tl), placed_track][:len(order)],
                        border=split.get("border"), feather_px=split.get("feather"),
                        cells=order, backdrop=split.get("backdrop"),
                        used_history=used_split_history,
                        face_safe=use_fs)
        elif animated:
            fps = 30.0
            if tl is not None:
                fps = float(getattr(tl, "fps", 0) or 0) or fps
            info = import_animated_as_clip(image_path, fps=fps)
            if tl is None:
                raise ValueError("当前任务没有时间线，无法放入动画")
            from cutora.assets.user import overlay_video_track
            from cutora.model.timeline_model import Clip, MediaSource
            src = MediaSource(info["path"])
            src.alpha = bool(info.get("alpha", True))
            tl.sources[src.id] = src
            # Image-track clips are not drawn or rendered. Same shelf as the
            # asset library: a non-main video track above the main picture.
            tr = overlay_video_track(tl)
            if replace:
                keep = []
                for c in list(getattr(tr, "clips", []) or []):
                    sid = getattr(c, "source_id", None)
                    old = tl.sources.get(sid) if sid else None
                    op = getattr(old, "path", None) if old is not None else None
                    if op and os.path.normcase(os.path.abspath(op)) == os.path.normcase(
                            os.path.abspath(info["path"])):
                        continue
                    keep.append(c)
                tr.clips = keep
            src_dur = float(getattr(src, "duration", 0) or 0) or max(0.04, end - start)
            clip = Clip(src.id, 0.0, src_dur, start)
            clip.loop = True
            clip.match_duration = True
            clip._match_target_dur = max(0.04, end - start)
            clip.alpha = bool(src.alpha)
            clip.fit_mode = fit
            clip.x = x
            clip.y = y
            clip.scale = scale
            tr.clips.append(clip)
        else:
            ovs = list(task.get("image_overlays") or [])
            if replace:
                ovs = [ov for ov in ovs if not _is_ours(ov, image_path)]
            ov = make_overlay(
                image_path, start, end, tw, th, iw, ih,
                fit, scale, x, y, track_id, track_no)
            ovs.append(ov)
            task["image_overlays"] = ovs
        if copies_cfg:
            task["platform_copies"] = copy.deepcopy(copies_cfg)
        else:
            task.pop("platform_copies", None)
        placed += 1
        per_task.append({
            "video": name,
            "start": start,
            "end": end,
            "duration": dur,
            "track_id": getattr(placed_track, "id", None) if is_video else None,
        })
        if log:
            log("  %s  Images%d  %.1fs → %.1fs  (视频 %.1fs)" % (
                name, track_no, start, end, dur))
            if copies_cfg:
                names = ", ".join(p["prefix"] for p in copies_cfg["platforms"])
                log("    多平台副本：%s" % names)
                log("    时间线预览：%s  %s" % (
                    copies_cfg["platforms"][0]["prefix"],
                    os.path.basename(image_path)))
    return {
        "placed": placed,
        "tasks": len(tasks),
        "image": image_path,
        "per_task": per_task,
        "platform_copies": copies_cfg,
    }


BATCH_VIDEO_TRACK_NAME = "批量视频"


def _place_video_clip(task, tl, path, start, end, *, fit="fill", scale=1.0, x=0.0,
                      y=0.0, loop=True, mute=True, blend="normal", replace=True,
                      track=None, src_dur=None):
    """一段视频放进「批量视频」轨（主画面上面、不是主轨）。返回那条轨道。

    Q144：``track`` 指定就放进那条轨（中央监视器往某个格子里拖视频）；
    ``src_dur`` 给了就不再探测时长。
    """
    from cutora.batch.script_broll_scatter import _add_source
    from cutora.model.timeline_model import Clip, Track
    if track is None:
        for tr in getattr(tl, "tracks", []) or []:
            if (getattr(tr, "type", "") == "video" and not getattr(tr, "is_main_video", False)
                    and getattr(tr, "name", "") == BATCH_VIDEO_TRACK_NAME):
                track = tr
                break
    if track is None:
        track = Track("video")
        track.name = BATCH_VIDEO_TRACK_NAME
        track.is_main_video = False
        tl.add_track(track)
    if not src_dur:
        src_dur = probe_video_duration(path) or 0.0
    if src_dur <= 0:
        raise ValueError("读不到视频时长：%s" % path)
    sid = _add_source(tl, path, src_dur, has_audio=not mute)
    want = os.path.normcase(os.path.abspath(path))
    if replace:
        keep = []
        for c in list(getattr(track, "clips", []) or []):
            src = tl.sources.get(getattr(c, "source_id", None))
            sp = getattr(src, "path", None) if src is not None else None
            if sp and os.path.normcase(os.path.abspath(sp)) == want:
                continue
            keep.append(c)
        track.clips = keep
    span = max(0.04, float(end) - float(start))
    # 片段长度 = min(素材, 可见区间)；素材比区间短时，循环就靠 match_duration
    # 铺到区间结束，不循环就放完为止
    clip = Clip(sid, 0.0, float(min(src_dur, span)), float(start))
    clip.fit_mode = fit or "fill"
    clip.blend_mode = blend or "normal"
    clip.x = float(x)
    clip.y = float(y)
    clip.scale = float(scale)
    clip.rotation = 0.0
    clip.speed = 1.0
    clip.is_main = False
    clip.loop = bool(loop)
    clip.match_duration = bool(loop)
    clip._match_target_dur = span if loop else 0.0
    track.clips.append(clip)
    return track


def run_insert(params, host=None, log=None):
    image = params.get("image") or params.get("path") or ""
    if not image:
        raise ValueError("需要图片路径 image")
    raw_copies = params.get("platform_copies") or params.get("platforms")
    if raw_copies and params.get("render_mode") not in (None, ""):
        if isinstance(raw_copies, dict):
            raw_copies = dict(raw_copies)
            raw_copies.setdefault("render_mode", params.get("render_mode"))
        elif isinstance(raw_copies, (list, tuple)):
            raw_copies = {
                "enabled": True,
                "platforms": list(raw_copies),
                "render_mode": params.get("render_mode"),
            }
    return {
        "image": os.path.abspath(image),
        "start_sec": parse_time(params.get("start_sec"), 60.0),
        "end_pad_sec": parse_time(params.get("end_pad_sec"), 0.0),
        "end_sec": None if params.get("end_sec") in (None, "") else parse_time(params.get("end_sec")),
        "image_track": int(params.get("image_track") if params.get("image_track") is not None else 1),
        "fit": (params.get("fit") or "fill"),
        "scale": float(params.get("scale") if params.get("scale") is not None else 1.0),
        "x": float(params.get("x") if params.get("x") is not None else 0.0),
        "y": float(params.get("y") if params.get("y") is not None else 0.0),
        "replace": True if params.get("replace") is None else bool(params.get("replace")),
        "platform_copies": raw_copies,
    }


# ---------------------------------------------------------------------------
#  GUI
# ---------------------------------------------------------------------------

def open_dialog(parent):
    """批量加载。右栏是 VideoCanvas，参数在 batch_dialog.build_batch_dialog。"""
    from cutora.ui.widgets.busy_bar import finish, start_busy
    start_busy(parent, "正在打开批量加载")
    try:
        from cutora.batch.batch_dialog import build_batch_dialog
        dlg = build_batch_dialog(parent)
    finally:
        finish()
    dlg.exec()
