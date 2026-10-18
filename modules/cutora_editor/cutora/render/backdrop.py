# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q155 · 整幅背景：分屏 / 不分屏都能用的「垫在最底下的一整张」。

三种来源，同一时刻只用一种（设一种就清掉另外两种）：

* **单个视频撑满**（只对分屏）：``split.backdrop.mode = "full"``，整幅垫某一格那条视频的模糊。
  画法在 ``split_screen.paint_split_video_tracks``。
* **外部图片**：``task["bg_image"] = {"path", "blur", "dim"}``，按铺满（cover）画在最底下。
* **外部视频**：时间线最底下一条 ``bg_role="backdrop"`` 的视频轨，片段循环 + 匹配时长，
  按铺满画；不进分屏格子、不出声。不分屏时它本来就是最底下一条轨，照常画。

整幅背景生效时，各片段自己的「背景填充」（每格一块的模糊）这一帧不画 —— 留白处统一露出
整幅背景，左右分屏就不会一边一块、割裂感很重。片段上的设置不动，换回「每格各自」就回来。

预览（canvas）、中央监视器 / 预览引擎（preview/engine）、导出（render_worker）在画视频轨
之前都调 ``begin_frame(task)``，画完调 ``end_frame()``；``fill_bg.fill_spec_at`` 读这里的开关。
状态按线程记（监视器每格一个渲染线程、导出也在自己的线程里）。
"""
from __future__ import annotations

import os
import threading

from cutora.core.log import get_logger

logger = get_logger("cutora.render.backdrop")

BACKDROP_ROLE = "backdrop"
BACKDROP_TRACK_NAME = "整幅背景"
IMAGE_DEFAULTS = {"path": "", "blur": 0.0, "dim": 0.0}

_TLS = threading.local()


# ───────────────────────── 读 ─────────────────────────

def is_backdrop_track(tr) -> bool:
    role = tr.get("bg_role") if isinstance(tr, dict) else getattr(tr, "bg_role", "")
    return str(role or "") == BACKDROP_ROLE


def _tracks(task):
    tl = task.get("timeline") if isinstance(task, dict) else None
    return list(getattr(tl, "tracks", None) or []) if tl is not None else []


def backdrop_tracks(task, *, active_only: bool = True) -> list:
    out = []
    for tr in _tracks(task):
        if not is_backdrop_track(tr):
            continue
        if active_only and (not getattr(tr, "visible", True) or getattr(tr, "mute", False)
                            or not (getattr(tr, "clips", None) or [])):
            continue
        out.append(tr)
    return out


def bg_image_spec(task):
    """task 上的外部背景图（规范化）；没有 / 路径空 → None。"""
    raw = task.get("bg_image") if isinstance(task, dict) else None
    if not isinstance(raw, dict):
        return None
    path = str(raw.get("path") or "").strip()
    if not path:
        return None
    out = dict(IMAGE_DEFAULTS)
    out["path"] = path
    for k, lo, hi in (("blur", 0.0, 100.0), ("dim", 0.0, 1.0)):
        try:
            out[k] = max(lo, min(hi, float(raw.get(k, out[k]) or 0.0)))
        except (TypeError, ValueError):
            pass
    return out


def has_external(task) -> bool:
    return bg_image_spec(task) is not None or bool(backdrop_tracks(task))


def split_full(task) -> bool:
    from cutora.render.split_layout import backdrop_is_full, split_enabled
    return bool(split_enabled(task) and backdrop_is_full((task or {}).get("split")))


def mode_of(task) -> str:
    """给界面打勾用：image / video / full / each。"""
    if bg_image_spec(task) is not None:
        return "image"
    if backdrop_tracks(task, active_only=False):
        return "video"
    if split_full(task):
        return "full"
    return "each"


def suppress_clip_fill(task) -> bool:
    """这一帧片段自己的背景填充要不要让位给整幅背景。"""
    try:
        return has_external(task) or split_full(task)
    except Exception:
        logger.exception("判断整幅背景失败")
        return False


# ───────────────────────── 每帧开关（按线程） ─────────────────────────

def begin_frame(task) -> None:
    _TLS.frame = bool(suppress_clip_fill(task))


def end_frame() -> None:
    _TLS.frame = False


class suppress_fill:
    """with suppress_fill(): 里面画的片段都不画自己的背景填充（可嵌套）。"""

    def __enter__(self):
        _TLS.depth = int(getattr(_TLS, "depth", 0)) + 1
        return self

    def __exit__(self, *_exc):
        _TLS.depth = max(0, int(getattr(_TLS, "depth", 0)) - 1)
        return False


def fill_suppressed() -> bool:
    return bool(getattr(_TLS, "frame", False)) or int(getattr(_TLS, "depth", 0)) > 0


# ───────────────────────── 画外部图片 ─────────────────────────

_IMG_CACHE: dict = {}
_IMG_CACHE_MAX = 24
_IMG_LOCK = threading.Lock()


def _load_image(path: str):
    from PySide6.QtGui import QImage
    try:
        st = os.stat(path)
        key = (os.path.normcase(os.path.abspath(path)), int(st.st_mtime), int(st.st_size))
    except OSError:
        return None
    with _IMG_LOCK:
        hit = _IMG_CACHE.get(key)
    if hit is not None:
        return hit
    img = QImage(path)
    if img.isNull():
        logger.warning("背景图读不出来：%s", path)
        return None
    img = img.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
    with _IMG_LOCK:
        if len(_IMG_CACHE) >= _IMG_CACHE_MAX:
            _IMG_CACHE.pop(next(iter(_IMG_CACHE)))
        _IMG_CACHE[key] = img
    return img


_PLATE_CACHE: dict = {}
_PLATE_MAX = 16


def image_plate(spec, width: int, height: int):
    """外部图按铺满裁成 width×height（+ 模糊 / 压暗）。同尺寸同设置复用。"""
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QColor, QImage, QPainter
    from cutora.render.fill_bg import blur_image, cover_rect

    w, h = max(1, int(width)), max(1, int(height))
    src = _load_image(spec["path"])
    if src is None:
        return None
    key = (id(src), w, h, round(float(spec["blur"]), 2), round(float(spec["dim"]), 3))
    with _IMG_LOCK:
        hit = _PLATE_CACHE.get(key)
    if hit is not None:
        return hit
    out = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    out.fill(QColor(0, 0, 0, 255))
    p = QPainter(out)
    try:
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        r = cover_rect(src.width(), src.height(), w, h)     # 以中心为原点
        p.drawImage(QRectF(r).translated(w / 2.0, h / 2.0), src)
    finally:
        p.end()
    if float(spec["blur"]) > 0.0:
        small = blur_image(out, float(spec["blur"]), "lens")
        if small is not None and not small.isNull() and small is not out:
            big = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
            big.fill(QColor(0, 0, 0, 255))
            bp = QPainter(big)
            try:
                bp.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
                bp.drawImage(QRectF(0.0, 0.0, float(w), float(h)), small)
            finally:
                bp.end()
            out = big
    dim = int(round(float(spec["dim"]) * 255.0))
    if dim > 0:
        dp = QPainter(out)
        try:
            dp.fillRect(0, 0, w, h, QColor(0, 0, 0, dim))
        finally:
            dp.end()
    with _IMG_LOCK:
        if len(_PLATE_CACHE) >= _PLATE_MAX:
            _PLATE_CACHE.pop(next(iter(_PLATE_CACHE)))
        _PLATE_CACHE[key] = out
    return out


def paint_bg_image(painter, task, width, height) -> bool:
    """task 有外部背景图就铺满画上去；返回画没画。"""
    spec = bg_image_spec(task)
    if spec is None:
        return False
    try:
        plate = image_plate(spec, width, height)
    except Exception:
        logger.exception("画外部背景图失败")
        return False
    if plate is None:
        return False
    painter.drawImage(0, 0, plate)
    return True


# ───────────────────────── 改（纯数据，撤销由调用方快照） ─────────────────────────

def clear_external(task) -> int:
    """去掉外部图片 / 外部视频背景。返回去掉了几样。"""
    n = 0
    if isinstance(task, dict) and task.pop("bg_image", None) is not None:
        n += 1
    tl = task.get("timeline") if isinstance(task, dict) else None
    if tl is not None:
        keep = [tr for tr in tl.tracks if not is_backdrop_track(tr)]
        n += len(tl.tracks) - len(keep)
        tl.tracks[:] = keep
    return n


def _set_split_mode(task, mode: str, cell=None) -> bool:
    from cutora.render.split_layout import apply_split_to_task, split_enabled
    if not split_enabled(task):
        return False
    bd = {"mode": mode}
    if cell:
        bd["cell"] = str(cell)
    apply_split_to_task(task, backdrop=bd)
    if mode != "full":
        sp = task.get("split") or {}
        if isinstance(sp.get("backdrop"), dict):
            sp["backdrop"].pop("cell", None)
    return True


def set_each(task) -> bool:
    """回到「每格各自」：去掉整幅背景；分屏的 backdrop 回 each（左右完整 = 每格各自模糊）。"""
    from cutora.render.split_layout import CONTAIN_LAYOUTS, split_enabled
    changed = clear_external(task) > 0
    if split_enabled(task):
        sp = task.get("split") or {}
        raw = sp.get("backdrop")
        if isinstance(raw, dict) or sp.get("layout") in CONTAIN_LAYOUTS:
            changed = _set_split_mode(task, "each") or changed
    return changed


def set_full(task, cell=None) -> bool:
    """分屏：整幅垫一格那条视频的模糊（cell 空 = 原视频那格）。不分屏 → 不改，返回 False。"""
    from cutora.render.split_layout import split_enabled
    if not split_enabled(task):
        return False
    clear_external(task)
    return _set_split_mode(task, "full", cell or main_cell(task))


def set_image(task, path: str, *, blur: float = 0.0, dim: float = 0.0) -> bool:
    path = str(path or "").strip()
    if not path or not os.path.isfile(path):
        raise ValueError("找不到图片：%s" % path)
    clear_external(task)
    task["bg_image"] = {"path": path, "blur": float(blur), "dim": float(dim)}
    return True


def set_video(task, path: str, *, duration=None, host=None):
    """最底下加一条整幅背景视频轨：循环播放，匹配这条视频的时长，铺满，静音。返回新轨道。"""
    from cutora.model.timeline_model import Clip, Track
    from cutora.batch.script_broll_scatter import _add_source, probe_duration, task_duration

    path = str(path or "").strip()
    if not path or not os.path.isfile(path):
        raise ValueError("找不到视频：%s" % path)
    tl = task.get("timeline") if isinstance(task, dict) else None
    if tl is None:
        raise ValueError("这条视频没有时间线")
    dur = float(duration if duration is not None else probe_duration(path) or 0.0)
    if dur <= 0:
        raise ValueError("读不到时长：%s" % os.path.basename(path))
    host_dur = float(task_duration(task, host) or 0.0) or dur
    clear_external(task)
    track = Track("video")
    track.is_main_video = False
    track.bg_role = BACKDROP_ROLE
    track.name = BACKDROP_TRACK_NAME
    sid = _add_source(tl, path, dur, has_audio=False)
    clip = Clip(sid, 0.0, dur, 0.0)
    clip.fit_mode = "fill"
    clip.is_main = False
    clip.loop = True
    clip.match_duration = True
    clip._match_target_dur = host_dur
    try:
        from cutora.model.project_schema import BG_FILL_DEFAULTS
        clip.bg_fill = dict(BG_FILL_DEFAULTS)
        clip.bg_fill["mode"] = "none"
    except Exception:
        pass
    track.clips.append(clip)
    # 视频域最底下 = 列表最前面（add_track 是往视频域顶上插）
    tl.tracks.insert(0, track)
    return track


def main_cell(task):
    """原视频（主轨）在哪一格；没有主轨就第一格。"""
    from cutora.render.split_layout import normalize_split
    first = None
    for tr in _tracks(task):
        cid = getattr(tr, "split_cell", None)
        if not cid or is_backdrop_track(tr):
            continue
        if getattr(tr, "is_main_video", False):
            return str(cid)
        first = first or str(cid)
    if first:
        return first
    cells = normalize_split((task or {}).get("split")).get("cells") or []
    return str(cells[0].get("id")) if cells else None
