# SPDX-License-Identifier: GPL-3.0-or-later
# Extracted from the T01 monolith by tools/split_monolith.py.
# Original lines: 2617-2624, 2625-2625, 2627-2628, 2631-2650, 2653-2670, 2673-2674, 2675-2687, 2690-2691, 2692-2692, 2694-2694, 2696-2765, 2767-2775, 2778-2780, 2781-2782, 2785-2810, 2813-5877

import contextvars
import os
import math
import cv2
from cutora.render.tls import ThreadLocalMapping
from PySide6.QtCore import (
    Qt,
    QRectF,
    QPoint,
    QPointF,
)
from PySide6.QtGui import (
    QColor,
    QPainter,
    QPen,
    QBrush,
    QFontMetrics,
    QFontMetricsF,
    QPainterPath,
    QImage,
    QLinearGradient,
    QRadialGradient,
)
from cutora.render import anim_engine as anim_engine
from cutora.subtitle.text_utils import (
    ENABLE_RICH_TEXT,
    tokenize_for_wrap,
    wrap_tokens_with_indices,
    wrap_text_by_pixel_width,
    _rich_default_layout,
    _normalize_text_align,
    _align_x_for_block,
    _iter_line_segments_for_draw,
    _visible_lines,
    _build_text_bg_brush,
    _rich_cache_key,
    _rich_layout_lines,
    _rich_measure_lines,
    _draw_rich_lines,
    _plain_layout_lines,
)
from PySide6.QtCore import Qt
from cutora.render.readers import (
    _cv2_frame_to_qimage,
    _open_video_capture,
)
from cutora.render.readers import _cv2_frame_to_qimage, _open_video_capture
from cutora.subtitle.text_utils import ENABLE_RICH_TEXT, _align_x_for_block, _build_text_bg_brush, _draw_rich_lines, _iter_line_segments_for_draw, _normalize_text_align, _plain_layout_lines, _rich_cache_key, _rich_default_layout, _rich_layout_lines, _rich_measure_lines, _visible_lines, tokenize_for_wrap, wrap_text_by_pixel_width, wrap_tokens_with_indices
from cutora.core import preview_perf as _preview_perf

def _tl_of(*a, **k):
    """Call-time import: this module is loaded by --render-job before MainWindow."""
    from cutora.ui.main_window import _tl_of as _impl
    return _impl(*a, **k)

from cutora.ui.panels.fonts import get_font_manager, sanitize_font_family


def _overlay_hit(ov, idx, rect, current_time, list_name):
    """Preview hit record with a 1/4 alpha mask. Not used by export."""
    from cutora.render.hitmask import attach_mask
    rec = {"type": "overlay", "list": list_name, "idx": idx, "rect": rect}
    if ov.get("locked"):
        rec["locked"] = True          # Q152-G：锁定的层画布上点不中
    w = max(1, int(round(float(ov.get("w") or 1))))
    h = max(1, int(round(float(ov.get("h") or 1))))
    img = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(0)
    local = dict(ov)
    local["x"] = 0
    local["y"] = 0
    local["rotation"] = 0
    local["_no_mask"] = True            # 命中按整个框算，遮罩不参与
    # Q152 修：位置 / 旋转打了关键帧时，draw 里的 bake_host 会把 x/y 又烘回画布坐标，
    # 字画到这张局部小图外面去 —— 命中遮罩全空，画布上点不中这条文字。
    kfs = local.get("keyframes")
    if isinstance(kfs, dict) and any(k in kfs for k in ("x", "y", "rotation")):
        local["keyframes"] = {k: v for k, v in kfs.items() if k not in ("x", "y", "rotation")}
    p = QPainter(img)
    try:
        FrameRenderer.draw_single_overlay(p, local, current_time)
    finally:
        p.end()
    attach_mask(
        rec, img,
        rotation=float(ov.get("rotation") or 0),
        flip_x=bool(ov.get("flip_x")),
        flip_y=bool(ov.get("flip_y")),
        opacity=float(ov.get("opacity", 255) or 255) / 255.0,
    )
    return rec

# --- 混合模式映射（唯一事实源）---
# 亮度蒙版不在这张表里。未知名字仍然落到普通覆盖，旧的五档混合一个像素都不能变。
BLEND_MODES = {
    "normal":     QPainter.CompositionMode.CompositionMode_SourceOver,
    "multiply":   QPainter.CompositionMode.CompositionMode_Multiply,
    "screen":     QPainter.CompositionMode.CompositionMode_Screen,
    "overlay":    QPainter.CompositionMode.CompositionMode_Overlay,
    "additive":   QPainter.CompositionMode.CompositionMode_Plus,
}

_BLEND_NORMAL = BLEND_MODES["normal"]

LUMA_MATTE = "luma_matte"
LUMA_MATTE_INV = "luma_matte_inv"

_DEFER_LUMA = contextvars.ContextVar("cutora_defer_luma", default=False)
_LUMA_NOTE = contextvars.ContextVar("cutora_luma_note", default=None)


def is_luma_matte(name) -> bool:
    return str(name or "") in (LUMA_MATTE, LUMA_MATTE_INV)


def luma_invert(name) -> bool:
    return str(name or "") == LUMA_MATTE_INV


def luma_deferred() -> bool:
    """正在把这一层画进稍后才混合的透明图。这时只留下蒙版的画面，挖空留给外层。"""
    return bool(_DEFER_LUMA.get())


class defer_luma_matte:
    def __enter__(self):
        self._tok = _DEFER_LUMA.set(True)
        return self

    def __exit__(self, *_exc):
        _DEFER_LUMA.reset(self._tok)


def note_luma_matte(mode) -> None:
    if is_luma_matte(mode):
        _LUMA_NOTE.set(str(mode))


def take_luma_note():
    mode = _LUMA_NOTE.get()
    _LUMA_NOTE.set(None)
    return mode


def luma_coverage_image(image, invert: bool = False, opacity: float = 1.0):
    """把画面收成一张「保留多少」的遮罩。

    Rec.709 亮度。白 = 保留下面的画面，黑 = 让下面变透明。反相交换。
    源像素完全透明（变换后的空白边）不挖空：盖不住的地方系数是 1。
    返回的图只使用 alpha，给 CompositionMode_DestinationIn 用。
    """
    import numpy as np

    if image is None or image.isNull():
        return image
    src = image.convertToFormat(QImage.Format.Format_ARGB32)
    w, h = int(src.width()), int(src.height())
    if w <= 0 or h <= 0:
        return src
    bpl = int(src.bytesPerLine())
    raw = np.frombuffer(bytes(src.constBits()), dtype=np.uint8, count=bpl * h).reshape(h, bpl)
    pix = raw[:, : w * 4].reshape(h, w, 4).astype(np.float32)
    b, g, r, a = pix[..., 0], pix[..., 1], pix[..., 2], pix[..., 3]
    cover = a / 255.0
    y = (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255.0
    keep = (1.0 - y) if invert else y
    op = max(0.0, min(1.0, float(opacity if opacity is not None else 1.0)))
    factor = 1.0 - op * cover * (1.0 - keep)
    out = np.zeros((h, w, 4), dtype=np.uint8)
    out[..., 3] = np.clip(np.round(factor * 255.0), 0, 255).astype(np.uint8)
    qimg = QImage(out.data, w, h, w * 4, QImage.Format.Format_ARGB32)
    return qimg.copy()


def paint_luma_matte(dest, blend_name, draw, width, height) -> None:
    """先把片段画进透明图（含它自己的变换 / 调色 / 关键帧），再按亮度挖已经画好的画面。"""
    w, h = int(width), int(height)
    if dest is None or w <= 0 or h <= 0:
        return
    buf = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    buf.fill(0)
    p = QPainter(buf)
    try:
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        draw(p)
    finally:
        if p.isActive():
            p.end()
    mask = luma_coverage_image(buf, invert=luma_invert(blend_name))
    dest.save()
    try:
        dest.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
        dest.drawImage(0, 0, mask)
    finally:
        dest.restore()


def draw_luma_image(painter, image, mode, rect=None) -> None:
    """把已经画好的蒙版画面按亮度叠到目标上。rect 与 draw_band 的目标矩形一致。"""
    if painter is None or image is None or image.isNull():
        return
    mask = luma_coverage_image(image, invert=luma_invert(mode))
    painter.save()
    try:
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
        if rect is None:
            painter.drawImage(0, 0, mask)
        else:
            painter.drawImage(rect, mask)
    finally:
        painter.restore()


def paint_blended_video_clip(dest, blend_name, into_layer, draw, width, height) -> None:
    """视频片段的混合。普通五档与原来一样：设混合模式再画。

    亮度蒙版：
    - 直接画在已经有画面的目标上：挖空目标。
    - 画进轨道的离屏层：先按普通覆盖留下蒙版画面，由 blit 再挖。
    - 这一层还要被外层按混合模式叠（视频带）：同样只留画面，外层再挖。
    """
    name = str(blend_name or "normal")
    if is_luma_matte(name):
        if into_layer:
            note_luma_matte(name)
            dest.save()
            try:
                draw(dest)
            finally:
                dest.restore()
            return
        if luma_deferred():
            dest.save()
            try:
                draw(dest)
            finally:
                dest.restore()
            return
        paint_luma_matte(dest, name, draw, width, height)
        return
    dest.save()
    dest.setCompositionMode(BLEND_MODES.get(name, _BLEND_NORMAL))
    try:
        draw(dest)
    finally:
        dest.restore()

# --- 渲染顺序（唯一排序来源：Timeline.get_sorted_video_tracks）---
_DEFAULT_RENDER_ORDER = ["image", "text", "subs"]  # 底→顶 fallback

def compute_export_render_order(task_data):
    """Export path. No solo_ids argument — session solo cannot reach the file."""
    return compute_render_order(task_data, solo_ids=None)


def compute_export_audio_tracks(task_data):
    """Export-side twin of compute_export_render_order. No solo_ids."""
    from cutora.model.track_guard import (
        compute_export_audio_tracks as _compute_export_audio_tracks,
    )
    return _compute_export_audio_tracks(task_data)


def compute_render_order(task_data, solo_ids=None):
    """NLE Gen-2: 直接从 timeline.tracks 顺序获取视觉轨道渲染列表（底→顶）。

    solo_ids is preview-only. Export callers must use compute_export_render_order.
    """
    try:
        from cutora.model.track_guard import track_is_muted
        tl = _tl_of(task_data)
        if tl is None:
            return list(_DEFAULT_RENDER_ORDER)
        order = []
        saw_overlay = False
        for t in tl.tracks:
            tt = getattr(t, "type", None)
            if getattr(t, "domain", "visual") != "visual":
                continue
            if tt == "video":
                continue
            if tt:
                saw_overlay = True
            if not getattr(t, "visible", True):
                continue
            if track_is_muted(t, task=task_data, solo_ids=solo_ids):
                continue
            if tt and tt not in order:
                order.append(tt)
        if order:
            return order
        if saw_overlay:
            return []
        return list(_DEFAULT_RENDER_ORDER)
    except Exception:
        return list(_DEFAULT_RENDER_ORDER)

def _resolve_blend_mode(task_data, layer_type):
    """从 timeline 中查找 layer_type 对应 Track 的 blend_mode。
    字幕（subs / text）强制返回 normal——不参与混合模式。
    旧项目 / 无 Track 时返回 normal。"""
    # Step 6.4: 字幕护城河——subs/text 始终 normal
    if layer_type in ("subs",):
        return _BLEND_NORMAL
    try:
        tl = _tl_of(task_data)
        if tl is None:
            return _BLEND_NORMAL
        for t in tl.tracks:
            if hasattr(t, "type") and t.type == layer_type:
                mode_name = getattr(t, "blend_mode", "normal")
                return BLEND_MODES.get(mode_name, _BLEND_NORMAL)
    except Exception:
        pass
    return _BLEND_NORMAL

# --- Cached image loader for dynamic box image attachment ---
# Per-thread: QImage is not safe to paint from two compose workers at once.
_IMAGE_CACHE = ThreadLocalMapping("image_cache", dict)

def _raster_svg(path):
    from PySide6.QtSvg import QSvgRenderer
    rend = QSvgRenderer(path)
    if not rend.isValid():
        return None
    sz = rend.defaultSize()
    w = max(1, int(sz.width()) if sz.width() > 0 else 256)
    h = max(1, int(sz.height()) if sz.height() > 0 else 256)
    img = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(0)
    p = QPainter(img)
    try:
        rend.render(p)
    finally:
        p.end()
    return img


def get_cached_image(path):
    """Load and cache a QImage from path. Returns None if failed."""
    if path in _IMAGE_CACHE:
        return _IMAGE_CACHE[path]
    if not os.path.isfile(path):
        _IMAGE_CACHE[path] = None
        return None
    ext = os.path.splitext(path)[1].lower()
    img = None
    if ext == ".svg":
        try:
            img = _raster_svg(path)
        except Exception:
            img = None
    else:
        img = QImage(path)
        if img is not None and img.isNull():
            img = None
    _IMAGE_CACHE[path] = img
    return img


def _fade_qimage(img, obj):
    """No-op when fade fields are missing or 0. Same apply_edge_fade as subtitle bg."""
    from cutora.render.textfx.fade import (
        apply_edge_fade, fade_offset_tuple, fade_tuple, offsets_active,
    )
    top, bottom, left, right = fade_tuple(obj)
    offs = fade_offset_tuple(obj)
    if max(top, bottom, left, right) <= 0 and not offsets_active(offs):
        return img
    return apply_edge_fade(
        img, top, bottom, left, right, offsets=offs)


# --- 视频帧解码缓存（用于 dyn_box 视频挂载）---
# Per-thread: cv2.VideoCapture is not thread-safe.
_DBI_VIDEO_CAPS = ThreadLocalMapping("dbi_caps", dict)          # path → cv2.VideoCapture

_DBI_VIDEO_FRAME_CACHE = ThreadLocalMapping("dbi_frames", dict)   # path → (time_sec, QImage)

_DBI_VIDEO_EXTS = frozenset({'.mp4', '.mov', '.avi', '.mkv', '.webm', '.wmv', '.flv', '.m4v'})

def get_dbi_video_frame(path, current_time, speed=1.0, loop=True, loop_gap=0.0):
    """从视频文件中解码指定时间的帧，返回 QImage。
    speed: 播放速度倍率（负数=倒放）
    loop:  是否循环播放
    loop_gap: 两次循环之间的间隔（秒），间隔期间返回 None（不显示）
    """
    if not path or not os.path.isfile(path):
        return None
    try:
        cap = _DBI_VIDEO_CAPS.get(path)
        if cap is None or not cap.isOpened():
            cap = _open_video_capture(path) or cv2.VideoCapture(path)
            if not cap.isOpened():
                return None
            _DBI_VIDEO_CAPS[path] = cap
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        total_frames = cap.get(cv2.CAP_PROP_FRAME_COUNT)
        vid_dur = total_frames / max(1, fps)  # 视频原始时长（秒）
        if vid_dur < 0.05:
            return None

        # 应用变速：elapsed = 实际经过的视频内部时间
        elapsed = current_time * abs(speed) if speed != 0 else 0.0

        if loop and vid_dur > 0.05:
            # 循环周期 = 视频时长 + 间隔
            cycle = vid_dur + max(0.0, loop_gap)
            pos_in_cycle = elapsed % cycle if cycle > 0 else 0.0
            if pos_in_cycle >= vid_dur:
                # 处于间隔期间 → 不显示
                return None
            seek_t = pos_in_cycle
        else:
            # 不循环：超出视频长度则停在最后一帧
            seek_t = min(elapsed, vid_dur - 0.01)

        # 倒放：从末尾往前
        if speed < 0:
            seek_t = max(0.0, vid_dur - seek_t)

        seek_t = max(0.0, seek_t)

        # 对齐到视频帧边界（1/fps 精度），避免同一视觉帧因微小时间差反复 seek 导致闪烁
        frame_idx = int(seek_t * fps)
        _cache_key = (path, frame_idx, round(speed, 3))
        cached = _DBI_VIDEO_FRAME_CACHE.get(path)
        if cached and cached[0] == _cache_key and cached[1] is not None:
            return cached[1]

        cap.set(cv2.CAP_PROP_POS_MSEC, seek_t * 1000.0)
        ret, frame = cap.read()
        if not ret or frame is None:
            # 解码失败时返回上一帧有效缓存，避免闪灭
            if cached and cached[1] is not None:
                return cached[1]
            return None
        fh, fw = frame.shape[:2]
        if fw <= 0 or fh <= 0:
            if cached and cached[1] is not None:
                return cached[1]
            return None
        qimg = _cv2_frame_to_qimage(frame)
        _DBI_VIDEO_FRAME_CACHE[path] = (_cache_key, qimg)
        return qimg
    except Exception:
        # 异常时也尝试返回上一帧有效缓存
        cached = _DBI_VIDEO_FRAME_CACHE.get(path)
        if cached and cached[1] is not None:
            return cached[1]
        return None

def cleanup_dbi_video_caps():
    """释放所有 dyn_box 视频挂载的 VideoCapture 资源。"""
    for c in _DBI_VIDEO_CAPS.values():
        try:
            c.release()
        except Exception:
            pass
    _DBI_VIDEO_CAPS.clear()
    _DBI_VIDEO_FRAME_CACHE.clear()

# --- 多层扩展描边 QImage 缓存（避免每帧重复 drawPath）---
# Per-thread. 字幕 SE 缓存：同一字幕段 + 同一样式 → 复用已渲染的 QImage
_SE_SUB_CACHE = ThreadLocalMapping(
    "se_sub", lambda: {"key": None, "img": None})

# 文本覆层 SE 缓存
_SE_OV_CACHE = ThreadLocalMapping(
    "se_ov", lambda: {"key": None, "img": None})

def _render_se_to_painter(p, text_path, se_layers, se_colors, _lw_list,
                          se_layer_feathers, se_feather, border_w):
    """将多层扩展描边绘制到指定 painter 上（复用逻辑，避免代码重复）。"""
    for layer_i in range(se_layers - 1, -1, -1):
        ci = min(layer_i, len(se_colors) - 1) if se_colors else 0
        lc = QColor(se_colors[ci]) if se_colors else QColor('#FF0000')
        layer_f = float(se_layer_feathers[layer_i]) if layer_i < len(se_layer_feathers) else se_feather
        pen_w = border_w
        for k in range(0, layer_i + 1):
            pen_w += _lw_list[k]
        pen_w = max(1.0, pen_w * 2)
        if layer_f > 0.5:
            _f_steps = min(4, max(2, int(layer_f / 3)))
            for fi in range(_f_steps, 0, -1):
                frac = fi / _f_steps
                extra = frac * layer_f * 2
                a = max(8, int(255 * (1.0 - frac * 0.8)))
                sc = QColor(lc); sc.setAlpha(a)
                p.setPen(QPen(sc, pen_w + extra, Qt.PenStyle.SolidLine,
                              Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawPath(text_path)
        p.setPen(QPen(lc, pen_w, Qt.PenStyle.SolidLine,
                       Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(text_path)

# --- 渲染核心（FrameRenderer 不做排序决策）---
def _v1_safe_cap(video_w: int, video_h: int, cx: float, stroke: float, wrap: float) -> float:
    """Safe-zone line cap without importing cutora.render.safe_zones (export AST lock)."""
    w, h = float(max(1, video_w)), float(max(1, video_h))
    ratio = w / h
    if ratio <= 0.75:
        left, right = 0.04, 0.18
    elif ratio >= 1.4:
        left, right = 0.05, 0.05
    else:
        left, right = 0.04, 0.04
    sx = w * left
    sw = w * (1.0 - left - right)
    room = 2.0 * min(max(0.0, cx - sx), max(0.0, sx + sw - cx))
    cap = min(sw, room, float(wrap)) - 2.0 * max(0.0, float(stroke))
    return max(50.0, cap)


def _fit_v1_overwide_lines(
    event, s, lines, fm, font, font_size, font_family, max_width, painter,
    video_w=None, video_h=None,
):
    """v1: reflow by rendered width, shrink ≤15%, ellipsis inside safe zone."""
    from cutora.subtitle.layout import (
        FIT_SCALE_FLOOR, LayoutRules, reflow_and_fit,
    )
    from cutora.subtitle.stylepack import effective_max_lines
    wrap = float(max_width) if max_width else 1.0
    cx = float((s or {}).get("pos_x") or ((video_w or 0) * 0.5) or 540)
    stroke = 0.0
    if isinstance(s, dict) and s.get("use_stroke"):
        try:
            stroke = float(s.get("border_width") or 0)
        except (TypeError, ValueError):
            stroke = 0.0
    if video_w and video_h:
        cap = _v1_safe_cap(int(video_w), int(video_h), cx, stroke, wrap)
    else:
        cap = wrap
    # Constraint, not target width: in-range lines (already ≤ wrap box)
    # must return byte-identical. Safe cap is only used when something
    # already overflowed the wrap (T06-2 revert-rerecord ruling).
    overflow = False
    for ln in lines or []:
        if float(fm.horizontalAdvance(ln)) > wrap + 0.5:
            overflow = True
            break
    if not overflow:
        event.pop("layout_warn", None)
        event.pop("_fit_warn", None)
        event.pop("_fit_visible", None)
        event.pop("_fit_iters", None)
        return lines, fm, font, font_size
    from cutora.ui.panels.fonts import style_weight_args
    bold, italic, style_name = style_weight_args(s)
    em0 = float(font_size)
    family = font_family

    def fm_factory(scale):
        sz = max(8, int(round(em0 * float(scale))))
        fnt = get_font_manager().get_font(
            family, sz, bold=bold, italic=italic, style=style_name)
        return QFontMetricsF(fnt)

    rules = LayoutRules(
        max_chars_per_line=int(s.get("max_chars_per_line") or 42),
        max_lines=max(1, int(effective_max_lines(s))),
        balance_lines=True,
        lang=event.get("lang") or "en",
        line_break=str(s.get("line_break") or "space"),
    )
    src = event.get("text") or " ".join(lines or [])
    new_lines, scale, warn, _iters = reflow_and_fit(
        src, list(lines or []), event.get("lang"), rules, fm_factory, cap,
        floor=FIT_SCALE_FLOOR)
    event["_fit_iters"] = _iters
    event["_fit_visible"] = " ".join(new_lines)
    if warn:
        event["layout_warn"] = warn
        event["_fit_warn"] = warn
    else:
        event.pop("layout_warn", None)
        event.pop("_fit_warn", None)
    font_size = em0 * float(scale)
    font = get_font_manager().get_font(
        family, max(8, int(round(font_size))),
        bold=bold, italic=italic, style=style_name)
    painter.setFont(font)
    fm = QFontMetrics(font)
    return new_lines, fm, font, font_size


def _folder_hides(task, ov) -> bool:
    if not isinstance(task, dict) or not task.get("layer_folders"):
        return False
    from cutora.batch.layer_folders import hidden_by_folder
    return hidden_by_folder(task, ov)


def _apply_image_grade(img, ov, el):
    """图片没有调色键，或色阶 / 调色还是默认时，返回原图。"""
    grades = []
    for host in (el, ov):
        if isinstance(host, dict) and host.get("grade"):
            grades.append(host.get("grade"))
    if not grades or img is None:
        return img
    from cutora.render.grade import apply_grade_to_qimage, grade_changes_picture
    if not any(grade_changes_picture(g) for g in grades):
        return img
    return apply_grade_to_qimage(img, grades)


class FrameRenderer:
    @staticmethod
    def _draw_adv_textbox_bg(painter, s, adv_box_x, adv_box_y, adv_box_w, adv_box_h):
        if not isinstance(s, dict) or not s.get('adv_bg_enabled', False):
            return
        bg_w = float(s.get('adv_bg_w', adv_box_w) or adv_box_w)
        bg_h = float(s.get('adv_bg_h', adv_box_h) or adv_box_h)
        bg_off_x = float(s.get('adv_bg_offset_x', 0) or 0)
        bg_off_y = float(s.get('adv_bg_offset_y', 0) or 0)
        bg_x = (adv_box_x + adv_box_w / 2.0) + bg_off_x - bg_w / 2.0
        bg_y = (adv_box_y + adv_box_h / 2.0) + bg_off_y - bg_h / 2.0
        bg_rect = QRectF(bg_x, bg_y, bg_w, bg_h)
        bg_rad = float(s.get('adv_bg_radius', 20) or 0)
        if s.get('adv_bg_gradient_enabled', False):
            grad_type = s.get('adv_bg_gradient_type', 'linear_h')
            grad_colors_str = s.get('adv_bg_gradient_colors', '#e0c3fc,#8ec5fc')
            grad_colors = [c.strip() for c in grad_colors_str.split(',') if c.strip()]
            bg_alpha = int(s.get('adv_bg_opacity', 191))
            if len(grad_colors) >= 2:
                if grad_type == 'center':
                    g = QRadialGradient(bg_rect.center(), max(bg_rect.width(), bg_rect.height()) / 2)
                    for i, c in enumerate(grad_colors):
                        cc = QColor(c); cc.setAlpha(bg_alpha)
                        g.setColorAt(i / max(1, len(grad_colors) - 1), cc)
                elif grad_type == 'fresnel':
                    g = QLinearGradient(bg_rect.topLeft(), bg_rect.topRight())
                    edge_c = QColor(grad_colors[0]); edge_c.setAlpha(min(255, bg_alpha))
                    mid_c = QColor(grad_colors[-1]); mid_c.setAlpha(min(255, int(bg_alpha * 0.2)))
                    g.setColorAt(0.0, edge_c); g.setColorAt(0.15, mid_c)
                    g.setColorAt(0.85, mid_c); g.setColorAt(1.0, edge_c)
                else:
                    if grad_type == 'linear_v':
                        g = QLinearGradient(bg_rect.topLeft(), bg_rect.bottomLeft())
                    else:
                        g = QLinearGradient(bg_rect.topLeft(), bg_rect.topRight())
                    for i, c in enumerate(grad_colors):
                        cc = QColor(c); cc.setAlpha(bg_alpha)
                        g.setColorAt(i / max(1, len(grad_colors) - 1), cc)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QBrush(g))
                painter.drawRoundedRect(bg_rect, bg_rad, bg_rad)
                if s.get('adv_bg_gradient_highlight', False):
                    painter.save()
                    try:
                        hl = QLinearGradient(bg_rect.topLeft(), QPointF(bg_rect.left(), bg_rect.top() + bg_rect.height() * 0.3))
                        hl.setColorAt(0.0, QColor(255, 255, 255, 60))
                        hl.setColorAt(1.0, QColor(255, 255, 255, 0))
                        path_hl = QPainterPath(); path_hl.addRoundedRect(bg_rect, bg_rad, bg_rad)
                        painter.setPen(Qt.PenStyle.NoPen)
                        painter.setBrush(QBrush(hl))
                        painter.drawPath(path_hl)
                    finally:
                        painter.restore()
                return
        bg = QColor(s.get('adv_bg_color', '#e0c3fc'))
        bg.setAlpha(int(s.get('adv_bg_opacity', 191)))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(bg)
        painter.drawRoundedRect(bg_rect, bg_rad, bg_rad)

    @staticmethod
    def draw_layers(painter, task_data, video_w, video_h, current_time,
                    hit_recorder=None, render_order=None, backdrop=None, only_track=None):
        """按 render_order 指定的顺序绘制各层。没有图层文件夹时，里面的不透明度和显隐与以前相同。"""
        from cutora.batch.layer_folders import folder_task_scope
        with folder_task_scope(task_data):
            return FrameRenderer._draw_layers_body(
                painter, task_data, video_w, video_h, current_time,
                hit_recorder, render_order, backdrop, only_track)

    @staticmethod
    def _draw_layers_body(painter, task_data, video_w, video_h, current_time,
                          hit_recorder=None, render_order=None, backdrop=None, only_track=None):
        """按 render_order 指定的顺序绘制各层。
        render_order: list[str]，底→顶，例如 ["image","text","subs"]。
        FrameRenderer 自身不做任何排序决策——调用方负责传入。
        backdrop: 视频像素尺寸的 QImage，仅 v2 fx.bg.glass 使用。
        only_track: Q163 统一叠压 —— 只画这一条轨（visual_stack 逐层调用）。"""
        if painter is None or painter.paintEngine() is None:
            return
        assert painter.paintEngine() is not None, "Painter dead at draw_layers entry"
        from cutora.subtitle.stylepack import set_paint_task_hint
        hint = ""
        if isinstance(task_data, dict):
            hint = str(task_data.get("video") or task_data.get("path") or "")
        set_paint_task_hint(hint)

        if render_order is None:
            render_order = _DEFAULT_RENDER_ORDER
        sub_time = current_time

        # 预先收集各类型 Track 的 visible 状态（用于隐藏轨道时跳过渲染）
        _layer_visible = {"text": True, "image": True, "subs": True}
        tl_obj = _tl_of(task_data) if isinstance(task_data, dict) else None
        if tl_obj is not None:
            for lt in ("text", "image", "subs"):
                # 如果该类型的所有 Track 都 hidden，则不渲染
                tracks_of_type = [t for t in tl_obj.tracks if getattr(t, "type", "") == lt]
                if tracks_of_type and all(
                    (not getattr(t, "visible", True)) or getattr(t, "mute", False)
                    for t in tracks_of_type
                ):
                    _layer_visible[lt] = False

        if only_track is not None:
            # 单条轨：只看这一条自己显不显示（同类型别的轨隐藏了不影响它）
            _ot = getattr(only_track, "type", "")
            _layer_visible = {"text": True, "image": True, "subs": True}
            if not getattr(only_track, "visible", True):
                return
            render_order = [x for x in render_order if x == _ot]

        def _only(tracks_of_type):
            if only_track is None:
                return tracks_of_type
            return [t for t in tracks_of_type if t is only_track]

        for layer_type in render_order:
            # 轨道隐藏检查
            if not _layer_visible.get(layer_type, True):
                continue
            if layer_type == "text":
                text_ovs = task_data.get('text_overlays', [])
                if tl_obj is not None:
                    text_tracks = [t for t in tl_obj.tracks if getattr(t, "type", "") == "text" and getattr(t, "visible", True)]
                    first_text_tid = getattr(text_tracks[0], "id", None) if text_tracks else None
                    text_tracks = _only(text_tracks)
                    from cutora.render.track_transform import (
                        compose_track_transform, paint_track_layer, track_needs_layer,
                        track_props_of,
                    )
                    for t in text_tracks:
                        t_id = getattr(t, "id", None)
                        track_blend_name = getattr(t, "blend_mode", "normal")
                        layering = track_needs_layer(t)

                        def _paint_text(dest, _t=t, _tid=t_id, _tbn=track_blend_name, _layering=layering):
                            for i, ov in enumerate(text_ovs):
                                if ov.get("hidden") or _folder_hides(task_data, ov):      # Q152-G 图层面板关掉的
                                    continue
                                ov_tid = ov.get('_track_id')
                                if ov_tid != _tid and not (ov_tid is None and _tid == first_text_tid):
                                    continue
                                if ov['start'] <= current_time <= ov['end']:
                                    ov['_canvas_w'] = video_w; ov['_canvas_h'] = video_h
                                    ov_blend_name = ov.get("blend_mode")
                                    if not _layering:
                                        ov_blend_name = ov_blend_name or _tbn
                                    else:
                                        ov_blend_name = ov_blend_name or "normal"
                                    dest.save()
                                    try:
                                        dest.setCompositionMode(BLEND_MODES.get(ov_blend_name, _BLEND_NORMAL))
                                        rect = FrameRenderer.draw_single_overlay(
                                            dest, ov, current_time, backdrop=backdrop)
                                    finally:
                                        dest.restore()
                                    if hit_recorder is not None:
                                        if rect is not None and _layering:
                                            rect = compose_track_transform(
                                                rect, track_props_of(_t), (video_w, video_h))
                                        hit_recorder.append(_overlay_hit(
                                            ov, i, rect, current_time, 'text_overlays'))

                        paint_track_layer(painter, t, video_w, video_h, _paint_text)
                else:
                    for i, ov in enumerate(text_ovs):
                        if ov.get("hidden") or _folder_hides(task_data, ov):
                            continue
                        if ov['start'] <= current_time <= ov['end']:
                            ov['_canvas_w'] = video_w; ov['_canvas_h'] = video_h
                            ov_blend_name = ov.get("blend_mode") or "normal"
                            painter.save()
                            try:
                                painter.setCompositionMode(BLEND_MODES.get(ov_blend_name, _BLEND_NORMAL))
                                rect = FrameRenderer.draw_single_overlay(
                                    painter, ov, current_time, backdrop=backdrop)
                            finally:
                                painter.restore()
                            if hit_recorder is not None:
                                hit_recorder.append(_overlay_hit(
                                    ov, i, rect, current_time, 'text_overlays'))

            elif layer_type == "image":
                image_ovs = task_data.get('image_overlays', [])
                if tl_obj is not None:
                    img_tracks = [t for t in tl_obj.tracks if getattr(t, "type", "") == "image" and getattr(t, "visible", True)]
                    first_img_tid = getattr(img_tracks[0], "id", None) if img_tracks else None
                    img_tracks = _only(img_tracks)
                    from cutora.render.track_transform import (
                        compose_track_transform, paint_track_layer, track_needs_layer,
                        track_props_of,
                    )
                    for t in img_tracks:
                        t_id = getattr(t, "id", None)
                        track_blend_name = getattr(t, "blend_mode", "normal")
                        layering = track_needs_layer(t)

                        def _paint_img(dest, _t=t, _tid=t_id, _tbn=track_blend_name, _layering=layering):
                            for i, ov in enumerate(image_ovs):
                                if ov.get("hidden") or _folder_hides(task_data, ov):
                                    continue
                                ov_tid = ov.get('_track_id')
                                if ov_tid != _tid and not (ov_tid is None and _tid == first_img_tid):
                                    continue
                                if ov['start'] <= current_time <= ov['end']:
                                    ov_blend_name = ov.get("blend_mode")
                                    if not _layering:
                                        ov_blend_name = ov_blend_name or _tbn
                                    else:
                                        ov_blend_name = ov_blend_name or "normal"
                                    if str(ov_blend_name or "normal") == "normal" and isinstance(task_data, dict) and task_data.get("layer_folders"):
                                        from cutora.batch.layer_folders import blend_of_folder
                                        _fb = blend_of_folder(task_data, ov)
                                        if _fb:
                                            ov_blend_name = _fb
                                    dest.save()
                                    try:
                                        dest.setCompositionMode(BLEND_MODES.get(ov_blend_name, _BLEND_NORMAL))
                                        rect = FrameRenderer.draw_single_overlay(dest, ov, current_time)
                                        if hit_recorder is not None and rect is not None:
                                            if _layering:
                                                rect = compose_track_transform(
                                                    rect, track_props_of(_t), (video_w, video_h))
                                            hit_recorder.append(_overlay_hit(
                                                ov, i, rect, current_time, 'image_overlays'))
                                        if ov.get('type') == 'image':
                                            FrameRenderer.draw_stickers(
                                                dest, ov, hit_recorder if hit_recorder is not None else None, i,
                                                default_blend_name=ov_blend_name, current_time=current_time
                                            )
                                    finally:
                                        dest.restore()

                        paint_track_layer(painter, t, video_w, video_h, _paint_img)
                else:
                    for i, ov in enumerate(image_ovs):
                        if ov.get("hidden") or _folder_hides(task_data, ov):
                            continue
                        if ov['start'] <= current_time <= ov['end']:
                            ov_blend_name = ov.get("blend_mode") or "normal"
                            if isinstance(task_data, dict) and task_data.get("layer_folders"):
                                from cutora.batch.layer_folders import blend_of_folder
                                _fb = blend_of_folder(task_data, ov)
                                if _fb and ov_blend_name == "normal":
                                    ov_blend_name = _fb
                            painter.save()
                            try:
                                painter.setCompositionMode(BLEND_MODES.get(ov_blend_name, _BLEND_NORMAL))
                                rect = FrameRenderer.draw_single_overlay(painter, ov, current_time)
                                # [FIX-IMG] 先登记容器命中框，再登记元素命中框（见上方同类修复）
                                if hit_recorder is not None and rect is not None:
                                    hit_recorder.append(_overlay_hit(
                                        ov, i, rect, current_time, 'image_overlays'))
                                if ov.get('type') == 'image':
                                    FrameRenderer.draw_stickers(
                                        painter, ov, hit_recorder if hit_recorder is not None else None, i,
                                        default_blend_name=ov_blend_name, current_time=current_time
                                    )
                            finally:
                                painter.restore()

            elif layer_type == "subs":
                segs = task_data.get("segments", []) or []
                if segs:
                    base_style = task_data.get("style", {})
                    role_ranges = {}
                    for seg in segs:
                        role_id = seg.get("role", "default")
                        s0 = seg.get("start"); e0 = seg.get("end")
                        if not isinstance(s0, (int, float)) or not isinstance(e0, (int, float)):
                            continue
                        r = role_ranges.get(role_id)
                        if r is None:
                            role_ranges[role_id] = [s0, e0]
                        else:
                            r[0] = min(r[0], s0)
                            r[1] = max(r[1], e0)
                    for role_id, (rs, re) in role_ranges.items():
                        if current_time is None or not (rs <= current_time <= re):
                            continue
                        from cutora.subtitle.stylepack import resolve_paint_style
                        style = resolve_paint_style(task_data, role_id)
                        if bool(style.get("advanced_textbox_enabled", False)) and style.get("adv_bg_enabled", False):
                            adv_box_x = float(style.get('advanced_textbox_x', 0) or 0)
                            adv_box_y = float(style.get('advanced_textbox_y', 0) or 0)
                            adv_box_w = float(style.get('advanced_textbox_w', 0) or 0)
                            adv_box_h = float(style.get('advanced_textbox_h', 0) or 0)
                            if adv_box_w < 80.0:
                                adv_box_w = max(80.0, video_w * 0.85)
                                adv_box_h = max(40.0, video_h * 0.16)
                                adv_box_x = (video_w - adv_box_w) / 2.0
                                adv_box_y = (video_h - adv_box_h) / 2.0
                            painter.save()
                            try:
                                FrameRenderer._draw_adv_textbox_bg(painter, style, adv_box_x, adv_box_y, adv_box_w, adv_box_h)
                            finally:
                                painter.restore()
                painter.save()
                try:
                    painter.setCompositionMode(_BLEND_NORMAL)
                    segments = task_data.get('segments', [])
                    base_style = task_data.get('style', {})

                    # ── Check for Scrolling Lyrics Mode ──
                    _lyrics_style = base_style
                    # If current seg has a role, check role style for lyrics flag
                    _lyrics_enabled = bool(_lyrics_style.get('lyrics_scroll_enabled', False))
                    if _lyrics_enabled and segments:
                        _lyrics_rect, _lyrics_seg_idx = FrameRenderer.draw_lyrics_scroll(
                            painter, segments, _lyrics_style, sub_time,
                            video_w, video_h, task_data,
                            hit_recorder=hit_recorder, backdrop=backdrop)
                        if hit_recorder is not None and _lyrics_seg_idx >= 0:
                            hit_recorder.append({'type': 'sub', 'idx': _lyrics_seg_idx, 'rect': _lyrics_rect})
                    else:
                        # ── Original single-segment rendering ──
                        curr_seg_idx = -1
                        curr_seg = None
                        for i, seg in enumerate(segments):
                            if seg['start'] <= sub_time <= seg['end']:
                                curr_seg = seg
                                curr_seg_idx = i
                                break
                        if curr_seg:
                            role_id = curr_seg.get("role", "default")
                            from cutora.subtitle.stylepack import resolve_paint_style
                            style = resolve_paint_style(task_data, role_id)
                            if hit_recorder is not None:
                                player_ms = task_data.get('_player_ms')
                                used_ms = int(current_time * 1000)
                                if player_ms is not None and task_data.get('_last_dbg_seg') != curr_seg_idx:
                                    diff_ms = used_ms - player_ms
                                    print(f"[SYNC] player_ms={player_ms} used_ms={used_ms} diff_ms={diff_ms} seg_start={curr_seg.get('start', 0)} seg_end={curr_seg.get('end', 0)}")
                                    task_data['_last_dbg_seg'] = curr_seg_idx
                            rect = FrameRenderer.draw_subtitle(
                                painter, curr_seg, style, sub_time, video_w, video_h,
                                hit_recorder=hit_recorder, seg_idx=curr_seg_idx,
                                backdrop=backdrop)
                            if hit_recorder is not None:
                                rec = {'type': 'sub', 'idx': curr_seg_idx, 'rect': rect}
                                try:
                                    from cutora.render.textfx.pipeline import word_hits_for
                                    wh = word_hits_for(curr_seg_idx)
                                    if wh:
                                        rec['word_hits'] = wh
                                except Exception:
                                    pass
                                hit_recorder.append(rec)
                finally:
                    painter.restore()

    @staticmethod
    def draw_lyrics_scroll(painter, segments, style, current_time,
                           video_w, video_h, task_data,
                           hit_recorder=None, backdrop=None):
        """
        Scrolling Lyrics Mode  (歌词滚动模式) — v5 Teleprompter

        All segments are laid out on a very long virtual vertical strip.
        The strip scrolls continuously upward as time progresses.
        Only the textbox-sized viewport is visible; everything outside is
        clipped, with smoothstep feathering at the edges.

        Uses draw_subtitle() per-segment so ALL existing style features
        (elastic bar/block, per-word highlight, stroke, etc.) are inherited.
        """
        if not segments:
            return QRectF(), -1

        # ── Textbox geometry ──
        adv_box_x = float(style.get('advanced_textbox_x', 0) or 0)
        adv_box_y = float(style.get('advanced_textbox_y', 0) or 0)
        adv_box_w = float(style.get('advanced_textbox_w', 0) or 0)
        adv_box_h = float(style.get('advanced_textbox_h', 0) or 0)

        if adv_box_w < 80:
            adv_box_w = max(80, video_w * 0.85)
            adv_box_h = max(200, video_h * 0.35)
            adv_box_x = (video_w - adv_box_w) / 2.0
            adv_box_y = (video_h - adv_box_h) / 2.0

        if (adv_box_x == 0 and adv_box_y == 0) or \
           (adv_box_x > video_w or adv_box_x + adv_box_w < 0) or \
           (adv_box_y > video_h or adv_box_y + adv_box_h < 0):
            adv_box_w = max(80, adv_box_w)
            adv_box_x = max(50, (video_w - adv_box_w) / 2.0)
            adv_box_y = max(50, (video_h - adv_box_h) / 2.0)

        adv_box_w = max(80, adv_box_w)
        adv_box_h = max(60, adv_box_h)

        buf_w = int(adv_box_w)
        buf_h = int(adv_box_h)
        if buf_w < 10 or buf_h < 10:
            return QRectF(adv_box_x, adv_box_y, adv_box_w, adv_box_h), -1

        # ── Lyrics params ──
        center_scale = float(style.get('lyrics_center_scale', 1.15))
        feather_px = float(style.get('lyrics_edge_feather', 40))
        # Parse transparency decay (0-100 from UI)
        alpha_decay = float(style.get('lyrics_alpha_decay', 0)) / 100.0
        line_gap_extra = int(style.get('lyrics_line_gap', 20))
        center_offset = float(style.get('lyrics_center_offset', 0))

        # ── Font & metrics ──
        from cutora.ui.panels.fonts import style_weight_args
        font_family = style.get('font_family')
        base_fontsize = int(style.get('fontsize', 60))
        base_bold, base_italic, base_style = style_weight_args(style)
        base_font = get_font_manager().get_font(
            font_family, base_fontsize,
            bold=base_bold, italic=base_italic, style=base_style)
        base_fm = QFontMetrics(base_font)
        base_line_h = base_fm.height()
        line_step = base_line_h + line_gap_extra

        # Center line Y in buffer coords (configurable offset)
        center_line_y = buf_h / 2.0 + center_offset

        # ── Build strip layout (cached) ──
        padding_x = 12
        wrap_width = max(50, buf_w - padding_x * 2)
        seg_gap = max(line_gap_extra, int(base_line_h * 0.5))

        cache_key = (font_family, base_fontsize, base_bold, base_italic,
                     wrap_width, line_gap_extra, seg_gap, len(segments))
        old_cache = task_data.get('_lyrics_strip_cache')

        if old_cache and old_cache.get('key') == cache_key:
            strip = old_cache['strip']
        else:
            strip = []
            y_cursor = 0.0
            for si, seg in enumerate(segments):
                text = seg.get('edited_text') or seg.get('text', '')
                wrapped = wrap_text_by_pixel_width(text, base_fm, wrap_width)
                if not wrapped:
                    wrapped = ['']
                n = len(wrapped)
                block_h = n * base_line_h + max(0, n - 1) * line_gap_extra
                strip.append({'strip_y': y_cursor, 'height': block_h, 'n_lines': n})
                y_cursor += block_h + seg_gap
            task_data['_lyrics_strip_cache'] = {'key': cache_key, 'strip': strip}

        if not strip:
            return QRectF(adv_box_x, adv_box_y, adv_box_w, adv_box_h), -1

        # ── Find active segment ──
        curr_idx = -1
        for i, seg in enumerate(segments):
            s_start = seg.get('start', 0)
            s_end = seg.get('end', 0)
            if isinstance(s_start, (int, float)) and isinstance(s_end, (int, float)):
                if s_start <= current_time <= s_end:
                    curr_idx = i
                    break

        _state_key = '_lyrics_last_idx'
        if curr_idx >= 0:
            task_data[_state_key] = curr_idx
        else:
            last = task_data.get(_state_key, -1)
            if 0 <= last < len(segments):
                curr_idx = last
            else:
                # Find nearest past segment
                best_i, best_e = -1, -1.0
                for i, seg in enumerate(segments):
                    e = seg.get('end', 0)
                    if isinstance(e, (int, float)) and e <= current_time and e > best_e:
                        best_e = e
                        best_i = i
                curr_idx = max(0, best_i)
                task_data[_state_key] = curr_idx

        if curr_idx < 0:
            curr_idx = 0

        # ── Compute read_y on the strip (teleprompter scroll) ──
        seg_data = segments[curr_idx]
        sl = strip[curr_idx]
        seg_start = float(seg_data.get('start', 0))
        seg_end = float(seg_data.get('end', 0))
        seg_dur = max(0.001, seg_end - seg_start)

        if seg_start <= current_time <= seg_end:
            # Inside active segment: scroll from top to bottom of block
            progress = (current_time - seg_start) / seg_dur
            read_y = sl['strip_y'] + progress * sl['height']
        elif current_time > seg_end:
            # Past the active segment — check for gap before next
            read_y = sl['strip_y'] + sl['height']
            if curr_idx + 1 < len(segments):
                next_seg = segments[curr_idx + 1]
                next_start = float(next_seg.get('start', seg_end))
                if current_time < next_start:
                    # In gap: interpolate to next segment
                    gap_dur = max(0.001, next_start - seg_end)
                    gap_prog = (current_time - seg_end) / gap_dur
                    end_y = sl['strip_y'] + sl['height']
                    start_y = strip[curr_idx + 1]['strip_y']
                    read_y = end_y + gap_prog * (start_y - end_y)
        else:
            read_y = sl['strip_y']

        # Viewport: base physical scroll to keep reading point centered
        viewport_scroll = read_y - center_line_y

        # ── Determine visible boundary ──
        vis_top = viewport_scroll - buf_h
        vis_bot = viewport_scroll + buf_h * 2

        # ── Build offscreen buffer ──
        # Fix text clipping by making the buffer wider based on center scale.
        overscan_x = int(buf_w * max(0.0, center_scale - 1.0) / 2.0)
        off_w = buf_w + overscan_x * 2

        offscreen = QImage(off_w, buf_h, QImage.Format.Format_ARGB32_Premultiplied)
        offscreen.fill(Qt.GlobalColor.transparent)
        off_p = QPainter(offscreen)
        off_p.setRenderHint(QPainter.RenderHint.Antialiasing)
        off_p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        stroke_w = float(style.get('stroke_width', 4) or 0)

        try:
            # ── Textbox background ──
            if style.get('adv_bg_enabled', False):
                FrameRenderer._draw_adv_textbox_bg(
                    off_p, style, 0, 0, buf_w, buf_h)

            _all_deferred_dbi = []  # 延迟渲染的图像挂载列表
            for si in range(len(segments)):
                if si >= len(strip):
                    break
                sli = strip[si]

                # Quick visibility cull in base space
                if sli['strip_y'] + sli['height'] < vis_top or sli['strip_y'] > vis_bot:
                    continue

                dynamic_layout_list = []
                min_y = float('inf')
                max_y = float('-inf')

                for li in range(sli['n_lines']):
                    # 1. Base unscaled physical position of line center
                    base_line_cy = sli['strip_y'] + li * line_step + base_line_h / 2.0
                    
                    # 2. Distance from reading point
                    base_dist = base_line_cy - read_y
                    
                    # 3. physical offset from reading point (reverted dynamic bulging)
                    phys_screen_cy = center_line_y + base_dist
                    
                    # 4. Compute soft scale natively for the active reading line
                    scale = 1.0
                    raw_norm = abs(base_dist) / max(1.0, line_step)
                    if raw_norm < 1.0:
                        t = 1.0 - raw_norm
                        # Smoothstep for softer peaking around the audio reading cursor
                        soft_t = t * t * (3.0 - 2.0 * t)
                        scale = 1.0 + (center_scale - 1.0) * soft_t
                    
                    # Asymmetrical alpha: current and future lines remain 100% opaque.
                    # Only apply alpha decay to lines that have already been read.
                    past_amount = -base_dist - (line_step / 2.0)
                    if past_amount <= 0:
                        a = 1.0
                    else:
                        norm_dist = past_amount / max(1.0, line_step)
                        a = anim_engine.compute_lyrics_line_alpha(norm_dist, alpha_decay)

                    # Save properties
                    dynamic_layout_list.append({
                        'screen_cy': phys_screen_cy,
                        'scale': scale,
                        'alpha': a,
                        'color': None
                    })

                    min_y = min(min_y, phys_screen_cy - base_line_h * scale)
                    max_y = max(max_y, phys_screen_cy + base_line_h * scale)

                # Visibility check after scale transformation
                if max_y < 0 or min_y > buf_h:
                    continue

                # ── Build per-segment style for draw_subtitle ──
                seg_style = dict(style)
                seg_style['fontsize'] = base_fontsize
                seg_style['advanced_textbox_enabled'] = False
                seg_style['lyrics_scroll_enabled'] = False
                seg_style['adv_bg_enabled'] = False
                from cutora.subtitle.stylepack import wrap_width_percent_value
                if wrap_width_percent_value(seg_style) is None:
                    usable = max(
                        1.0, float(off_w) - 2.0 * (padding_x + overscan_x))
                    seg_style['wrap_width_percent'] = max(
                        0.0, min(100.0, usable / max(1.0, float(off_w)) * 100.0))
                seg_style['wrap_lines'] = max(sli['n_lines'] + 2, 20)
                seg_style['line_spacing'] = line_gap_extra
                
                # To prevent pre-pass stroke cache from misaligning scaled text:
                seg_style['stroke_expand_enabled'] = False 

                # Sub-buffer: size it to tightly fit the physically pushed lines
                margin = max(int(stroke_w * 2 + 30), int(base_line_h * 1.5))
                sub_h = int(max_y - min_y + margin * 2)
                sub_h = max(sub_h, base_line_h + margin * 2)
                
                dest_y = min_y - margin

                # Map screen Y to sub_buf Y
                mapped_dynamic_layout = []
                for dl in dynamic_layout_list:
                    sub_buf_cy = dl['screen_cy'] - dest_y
                    # draw_subtitle `y` is the baseline coordinate!
                    # center = top + half_height = baseline - ascent + half_height
                    # therefore baseline = center + ascent - half_height
                    sub_buf_baseline = sub_buf_cy + base_fm.ascent() - base_line_h / 2.0
                    mapped_dynamic_layout.append({
                        'y': sub_buf_baseline,
                        'scale': dl['scale'],
                        'alpha': dl['alpha'],
                        'color': dl['color']
                    })

                seg_style['pos_x'] = off_w / 2.0
                # Using dynamic_layout, pos_y logic in draw_subtitle is skipped, but needs a dummy
                seg_style['pos_y'] = 0 

                fake_event = dict(segments[si])
                fake_event.pop('_inline_editing', None)

                # ── Render into sub-buffer ──
                sub_buf = QImage(off_w, sub_h,
                                 QImage.Format.Format_ARGB32_Premultiplied)
                sub_buf.fill(Qt.GlobalColor.transparent)
                sub_p = QPainter(sub_buf)
                sub_p.setRenderHint(QPainter.RenderHint.Antialiasing)
                sub_p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
                try:
                    FrameRenderer.draw_subtitle(
                        sub_p, fake_event, seg_style,
                        current_time, off_w, sub_h,
                        dynamic_layout_list=mapped_dynamic_layout)
                finally:
                    sub_p.end()

                # ── Composite sub-buffer onto offscreen ──
                off_p.drawImage(QPointF(0, dest_y), sub_buf)

                # ── 收集延迟的图像挂载（用于主画布渲染）──
                if fake_event.get('_deferred_dbi'):
                    for _dd in fake_event['_deferred_dbi']:
                        _dd['_dest_y'] = dest_y  # 记录 sub-buffer → offscreen 偏移
                    _all_deferred_dbi.extend(fake_event['_deferred_dbi'])

            # ── Edge feathering ──
            if feather_px > 0.5:
                fade_h = max(4, int(feather_px))
                fade_h = min(fade_h, buf_h // 2 - 1)

                off_p.setCompositionMode(
                    QPainter.CompositionMode.CompositionMode_DestinationIn)

                top_grad = QLinearGradient(0, 0, 0, fade_h)
                for i in range(9):
                    t = i / 8.0
                    ss = t * t * (3.0 - 2.0 * t)
                    top_grad.setColorAt(t, QColor(0, 0, 0, int(255 * ss)))
                off_p.fillRect(QRectF(0, 0, off_w, fade_h), QBrush(top_grad))

                mid_start = fade_h
                mid_end = buf_h - fade_h
                if mid_end > mid_start:
                    off_p.fillRect(QRectF(0, mid_start, off_w, mid_end - mid_start),
                                  QColor(0, 0, 0, 255))

                bot_grad = QLinearGradient(0, buf_h - fade_h, 0, buf_h)
                for i in range(9):
                    t = i / 8.0
                    ss = t * t * (3.0 - 2.0 * t)
                    bot_grad.setColorAt(t, QColor(0, 0, 0, int(255 * (1.0 - ss))))
                off_p.fillRect(QRectF(0, buf_h - fade_h, off_w, fade_h),
                              QBrush(bot_grad))

                off_p.setCompositionMode(
                    QPainter.CompositionMode.CompositionMode_SourceOver)

        finally:
            off_p.end()

        # ── Composite onto main canvas ──
        textbox_rect = QRectF(adv_box_x, adv_box_y, adv_box_w, adv_box_h)
        painter.save()
        try:
            painter.drawImage(QPointF(adv_box_x - overscan_x, adv_box_y), offscreen)
        finally:
            painter.restore()

        # ── 渲染延迟的图像挂载（在主画布上，支持混合模式）──
        if _all_deferred_dbi:
            # 坐标映射：sub-buffer(ix,iy) → offscreen(ix, iy+dest_y) 
            #          → main canvas(ix + adv_box_x - overscan_x, iy + dest_y + adv_box_y)
            _map_x = adv_box_x - overscan_x
            _map_y = adv_box_y
            for _dd in _all_deferred_dbi:
                _main_ix = _dd['ix'] + _map_x
                _main_iy = _dd['iy'] + _dd['_dest_y'] + _map_y
                _dd_img = _dd['image']
                painter.save()
                try:
                    _bm = BLEND_MODES.get(_dd.get('blend', 'normal'), _BLEND_NORMAL)
                    painter.setCompositionMode(_bm)
                    if _dd.get('opacity', 1.0) < 0.999:
                        painter.setOpacity(painter.opacity() * _dd['opacity'])
                    pcx = _main_ix + _dd_img.width() / 2
                    pcy = _main_iy + _dd_img.height() / 2
                    _rot = _dd.get('rot', 0)
                    _dx = _dd.get('dx', 0)
                    _dy = _dd.get('dy', 0)
                    _sx = _dd.get('sx', 1.0)
                    _sy = _dd.get('sy', 1.0)
                    if _rot != 0 or _dx != 0 or _dy != 0 or _sx != 1.0:
                        painter.translate(pcx + _dx, pcy + _dy)
                        if _rot != 0:
                            painter.rotate(_rot)
                        if _sx != 1.0 or _sy != 1.0:
                            painter.scale(_sx, _sy)
                        painter.translate(-pcx, -pcy)
                    painter.drawImage(QPoint(int(_main_ix), int(_main_iy)), _dd_img)
                finally:
                    painter.restore()

        return textbox_rect, curr_idx

    @staticmethod
    def draw_single_overlay(painter, ov, current_time=None, backdrop=None):
        if current_time is not None:
            from cutora.render.keyframe import bake_host, local_time
            ov = bake_host(ov, local_time(ov, current_time))
        # Q152-B：带遮罩的覆层先画到整幅透明层上，再按它的框把遮罩乘上去
        if ov.get("masks") and not ov.get("_no_mask") and not ov.get("fx_graph"):
            from cutora.fx.layer_fx import mask_canvas_image
            from cutora.fx.masks import has_masks
            if has_masks(ov):
                inner = dict(ov)
                inner["_no_mask"] = True
                dev = painter.device()
                cw = int(ov.get("_canvas_w") or (dev.width() if dev is not None else 0) or 0)
                ch = int(ov.get("_canvas_h") or (dev.height() if dev is not None else 0) or 0)
                result = {}

                def _draw(p, _inner=inner):
                    result["rect"] = FrameRenderer.draw_single_overlay(p, _inner, None, backdrop)

                layer = mask_canvas_image((cw, ch), ov, _draw)
                if layer is not None:
                    painter.drawImage(0, 0, layer)
                    return result.get("rect") or QRectF(float(ov.get("x", 0)), float(ov.get("y", 0)),
                                                        float(ov.get("w", 100)), float(ov.get("h", 100)))
        x = float(ov.get("x", 0))
        y = float(ov.get("y", 0))
        w = float(ov.get("w", 100))
        h = float(ov.get("h", 100))
        logical_rect = QRectF(x, y, w, h)
        # 新式 image overlay 使用 elements 结构，没有 content/x/y/w/h 顶层字段
        # 这类 overlay 由 draw_stickers 处理，此处跳过
        if ov.get('type') == 'image' and 'content' not in ov and ov.get('elements'):
            return logical_rect
        rotation = ov.get('rotation', 0)
        opacity = ov.get('opacity', 255)
        content = ov.get('content', '')

        if ov.get('type') == 'text' and (ov.get('fx') or ov.get('runs')):
            from cutora.render.text_layer_paint import paint_text_overlay
            return paint_text_overlay(
                painter, ov, current_time, backdrop,
                video_w=ov.get('_canvas_w'), video_h=ov.get('_canvas_h'))
        
        painter.save()
        try:
            cx = w / 2.0
            cy = h / 2.0
            painter.translate(x + cx, y + cy)
            painter.rotate(rotation)
            painter.translate(-cx, -cy)

            base_opacity = opacity / 255.0
            ov_start = float(ov.get('start', 0.0))
            ov_end = float(ov.get('end', 0.0))
            t_preset = ov.get("transition_preset", "none")
            t_dur = float(ov.get("transition_duration", anim_engine.DEFAULT_TRANSITION_DURATION))
            t_op, t_scale, t_dx, t_dy = anim_engine.compute_transition_params(
                current_time, ov_start, ov_end, t_preset, t_dur
            )
            if t_dx or t_dy:
                painter.translate(t_dx, t_dy)
            if abs(t_scale - 1.0) > 0.0001:
                painter.translate(cx, cy)
                painter.scale(t_scale, t_scale)
                painter.translate(-cx, -cy)
            
            if ov['type'] == 'image':
                from cutora.batch.layer_folders import opacity_mul_current
                painter.setOpacity(base_opacity * t_op * opacity_mul_current(ov))
                img = get_cached_image(content)
                if img and ov.get("fx_graph"):
                    # Q152-F：图片层有节点图 → 整张图按图求值（遮罩坐标就是图片自己的框，和整幅层路径一致）
                    from cutora.fx.layer_fx import apply_layer_fx
                    _w_box = float(ov.get("w", 0) or 0) * float(ov.get("scale", 1.0) or 1.0)
                    _pxs = (img.width() / _w_box) if _w_box > 0 else 1.0
                    from cutora.fx.graph import preview_view
                    _gh = {"fx_graph": ov.get("fx_graph")}
                    img = apply_layer_fx(img, _gh, px_scale=_pxs, view=preview_view(_gh))
                elif img and ov.get("keyer"):
                    # Q152-C：图片层抠像（只抠像；遮罩走上面的整幅层路径）
                    from cutora.fx.layer_fx import apply_layer_fx
                    img = apply_layer_fx(img, {"keyer": ov.get("keyer")})
                if img:
                    scale = float(ov.get('scale', 1.0))
                    flip_x = ov.get('flip_x', False)
                    flip_y = ov.get('flip_y', False)
                    keep_aspect = ov.get('lock_aspect', ov.get('keep_aspect', True))
                    scaled_w = w * scale
                    scaled_h = h * scale
                    painter.save()
                    try:
                        painter.translate(cx, cy)
                        painter.scale(-1 if flip_x else 1, -1 if flip_y else 1)
                        painter.translate(-cx, -cy)
                        scaled_img = img.scaled(int(scaled_w), int(scaled_h), Qt.AspectRatioMode.KeepAspectRatio if keep_aspect else Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
                        scaled_img = _fade_qimage(scaled_img, ov)
                        draw_x = (w - scaled_w) / 2 + (scaled_w - scaled_img.width()) / 2
                        draw_y = (h - scaled_h) / 2 + (scaled_h - scaled_img.height()) / 2
                        painter.drawImage(QPointF(draw_x, draw_y), scaled_img)
                    finally:
                        painter.restore()

            elif ov['type'] == 'text':
                if ov.get("_inline_editing", False):
                    return logical_rect
                # 逻辑命中框始终是未旋转的 world rect（logical_rect）
                _ = QRectF(0.0, 0.0, float(w), float(h))

                # ── Animation: entry / exit for text overlay ──
                anim_in_type = ov.get('anim_in_type', 'none')
                anim_out_type = ov.get('anim_out_type', 'none')
                in_prog, out_prog = 1.0, 1.0
                _anim_opacity_factor = 1.0

                if current_time is not None and (anim_in_type != 'none' or anim_out_type != 'none'):
                    in_dur = float(ov.get('anim_in_duration', 0.3)) if anim_in_type != 'none' else 0.0
                    out_dur = float(ov.get('anim_out_duration', 0.3)) if anim_out_type != 'none' else 0.0
                    in_prog, out_prog = anim_engine.compute_anim_progress(
                        current_time, ov_start, ov_end,
                        in_dur, out_dur,
                        ov.get('anim_in_easing', 'ease_out'),
                        ov.get('anim_out_easing', 'ease_out'))

                    # Fade
                    if anim_in_type == 'fade' or anim_out_type == 'fade':
                        fade_p = 1.0
                        if anim_in_type == 'fade': fade_p = min(fade_p, in_prog)
                        if anim_out_type == 'fade': fade_p = min(fade_p, out_prog)
                        _anim_opacity_factor *= fade_p

                    # Slide
                    for atype, prog in [(anim_in_type, in_prog), (anim_out_type, out_prog)]:
                        if atype in ('slide_up', 'slide_down', 'slide_left', 'slide_right'):
                            dx, dy = anim_engine.compute_slide_offset(prog, atype, distance=60)
                            painter.translate(dx, dy)

                    # Pop
                    if anim_in_type == 'pop' or anim_out_type == 'pop':
                        pop_p = 1.0
                        if anim_in_type == 'pop': pop_p = min(pop_p, anim_engine.compute_pop_scale(in_prog))
                        if anim_out_type == 'pop': pop_p = min(pop_p, anim_engine.compute_pop_scale(out_prog))
                        if pop_p < 0.999:
                            painter.translate(cx, cy)
                            painter.scale(pop_p, pop_p)
                            painter.translate(-cx, -cy)

                    # Floating
                    if anim_in_type == 'floating':
                        fl_amp = float(ov.get('floating_amplitude', 8))
                        fl_per = float(ov.get('floating_period', 2.0))
                        dy_f = anim_engine.compute_floating_offset(current_time, ov_start, fl_amp, fl_per)
                        painter.translate(0, dy_f)

                    # Flash highlight
                    if anim_in_type == 'flash_highlight':
                        fi = anim_engine.compute_flash_highlight(current_time, ov_start, float(ov.get('flash_duration', 0.1)))
                        if fi > 0.01:
                            flash_c = QColor(ov.get('flash_color', '#FFFF00'))
                            flash_c.setAlpha(int(200 * fi))
                            target_r = QRectF(-4.0, -4.0, float(w) + 8.0, float(h) + 8.0)
                            painter.setPen(Qt.PenStyle.NoPen)
                            painter.setBrush(flash_c)
                            painter.drawRoundedRect(target_r, 8, 8)

                from cutora.batch.layer_folders import opacity_mul_current
                painter.setOpacity(base_opacity * _anim_opacity_factor * t_op * opacity_mul_current(ov))

                if ENABLE_RICH_TEXT and ov.get("content_format") == "rich_spans":
                    layout = _rich_default_layout(ov.get("text_layout"))
                    base_style = {
                        "font_family": sanitize_font_family(ov.get("font_family")),
                        "fontsize": ov.get("fontsize", 40),
                        "bold": ov.get("bold", False),
                        "italic": ov.get("italic", False),
                        "color": ov.get("color", "#FFFFFF"),
                        "stroke_color": ov.get("stroke_color", "#000000"),
                        "stroke_width": ov.get("stroke_width", 0),
                        "use_stroke": ov.get("use_stroke", False),
                        "shadow_color": ov.get("shadow_color", "#000000"),
                        "shadow_opacity": ov.get("shadow_opacity", 120),
                        "shadow_blur": ov.get("shadow_blur", 0),
                        "shadow_offset_x": ov.get("shadow_offset_x", 4),
                        "shadow_offset_y": ov.get("shadow_offset_y", 4),
                        "shadow_enabled": ov.get("shadow_enabled", False),
                    }
                    rich_spans = ov.get("rich_content") if isinstance(ov.get("rich_content"), list) else []
                    if not rich_spans:
                        rich_spans = [{"text": content, **base_style}]
                    box_x = layout["left_pad"]
                    box_y = layout["top_pad"]
                    box_w = max(1.0, w - layout["left_pad"] - layout["right_pad"])
                    box_h = max(1.0, h - layout["top_pad"] - layout["bottom_pad"])
                    letter_spacing = layout.get("letter_spacing", 0)
                    cache_key = _rich_cache_key(rich_spans, base_style, box_w, layout, letter_spacing)
                    cache = ov.get("_rich_cache", {})
                    if cache_key is not None and cache.get("key") == cache_key:
                        lines = cache.get("lines", [])
                        max_w = cache.get("max_w", 0.0)
                        total_h = cache.get("total_h", 0.0)
                    else:
                        lines = _rich_layout_lines(rich_spans, base_style, box_w, letter_spacing)
                        max_w, total_h = _rich_measure_lines(lines, layout.get("line_spacing", 0))
                        ov["_rich_cache"] = {"key": cache_key, "lines": lines, "max_w": max_w, "total_h": total_h}

                    if ov.get('bg_enabled', False):
                        pad = ov.get('bg_padding', 12)
                        bg_c = QColor(ov.get('bg_color', '#000000'))
                        bg_c.setAlpha(int(ov.get('bg_opacity', 191)))
                        bx = box_x - pad
                        by = box_y - pad
                        bw = min(box_w, max_w) + pad * 2
                        bh = total_h + pad * 2
                        rad = ov.get('bg_radius', 12)
                        painter.setPen(Qt.PenStyle.NoPen)
                        painter.setBrush(bg_c)
                        painter.drawRoundedRect(QRectF(bx, by, bw, bh), rad, rad)

                    if ov.get('stroke_expand_enabled', False):
                        se_layers = int(ov.get('stroke_expand_layers', 3))
                        se_step = float(ov.get('stroke_expand_step', 4))
                        se_feather = float(ov.get('stroke_expand_feather', 8))
                        se_colors_str = ov.get('stroke_expand_colors', '#FF0000,#00FF00,#0000FF')
                        se_colors = [c.strip() for c in se_colors_str.split(',') if c.strip()]
                        se_layer_widths = ov.get('stroke_expand_layer_widths', [])
                        se_layer_feathers = ov.get('stroke_expand_layer_feathers', [])
                        _lw_list = []
                        for li in range(se_layers):
                            _lw_list.append(float(se_layer_widths[li]) if li < len(se_layer_widths) else se_step)
                        _lf_tuple = tuple(float(se_layer_feathers[i]) if i < len(se_layer_feathers) else se_feather
                                          for i in range(se_layers))
                        all_path = QPainterPath()
                        y_cursor = box_y
                        for line in lines:
                            ascent = line.get("ascent", 0.0)
                            baseline = y_cursor + ascent
                            x_cursor = box_x
                            for seg in line.get("segments", []):
                                txt = seg.get("text", "")
                                if not txt or seg.get("is_space"):
                                    x_cursor += seg.get("width", 0.0)
                                    continue
                                font = seg.get("font")
                                if font is not None:
                                    all_path.addText(x_cursor, baseline, font, txt)
                                x_cursor += seg.get("width", 0.0)
                            y_cursor += ascent + line.get("descent", 0.0) + float(layout.get("line_spacing", 0))
                        _render_se_to_painter(painter, all_path, se_layers, se_colors, _lw_list, _lf_tuple, se_feather, ov.get("stroke_width", 0))

                    _draw_rich_lines(painter, lines, box_x, box_y, layout.get("line_spacing", 0))
                    return logical_rect

                layout = _rich_default_layout(ov.get("text_layout"))
                box_x = layout["left_pad"]
                box_y = layout["top_pad"]
                box_w = max(1.0, w - layout["left_pad"] - layout["right_pad"])
                box_h = max(1.0, h - layout["top_pad"] - layout["bottom_pad"])
                align_name = _normalize_text_align(layout.get("align", "left"))

                font_family = ov.get("font_family")
                font_size = ov.get('fontsize', 40)
                font = get_font_manager().get_font(
                    font_family, int(font_size),
                    bold=ov.get('bold', False),
                    italic=ov.get('italic', False)
                )
                painter.setFont(font)
                lines, max_line_w, total_h, fm = _plain_layout_lines(content, font, box_w)
                visible_lines = _visible_lines(lines, fm, box_h)
                visible_h = len(visible_lines) * fm.height()
                visible_max_w = min(box_w, max((ln[1] for ln in visible_lines), default=0.0))

                text_c = QColor(ov.get('color', '#FFFFFF'))
                painter.setPen(text_c)

                start_x = box_x
                start_y = box_y

                if ov.get('bg_enabled', False) and visible_h > 0:
                    pad_x = int(ov.get('bg_padding_x', ov.get('bg_padding', 12)))
                    pad_y = int(ov.get('bg_padding_y', ov.get('bg_padding', 12)))
                    extra_w = int(ov.get('bg_expand_w', 0))
                    extra_h = int(ov.get('bg_expand_h', 0))
                    bx = _align_x_for_block(box_x, box_w, visible_max_w, align_name) - pad_x - extra_w
                    by = box_y - pad_y - extra_h
                    bw = visible_max_w + pad_x * 2 + extra_w * 2
                    bh = visible_h + pad_y * 2 + extra_h * 2
                    bg_rect = QRectF(bx, by, bw, bh)
                    rad = ov.get('bg_radius', 12)
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.setBrush(_build_text_bg_brush(ov, bg_rect))
                    painter.drawRoundedRect(bg_rect, rad, rad)

                painter.save()
                painter.setClipRect(QRectF(box_x, box_y, box_w, box_h))

                # ── 投影（shadow）：支持羽化 + 粗细 + 透明度 ──
                if ov.get('shadow_enabled', False):
                    shadow_c = QColor(ov.get('shadow_color', '#000000'))
                    shadow_alpha = int(ov.get('shadow_opacity', 120))
                    sx = float(ov.get('shadow_offset_x', 4))
                    sy = float(ov.get('shadow_offset_y', 4))
                    blur = int(ov.get('shadow_blur', 0))
                    spread = int(ov.get('shadow_spread', 0))

                    shadow_path = QPainterPath()
                    y_cursor = start_y
                    for i, line in enumerate(lines):
                        txt = line[0]
                        if not txt:
                            y_cursor += fm.height()
                            continue
                        for seg_text, seg_x in _iter_line_segments_for_draw(
                            txt, fm, start_x, box_w, align_name, i == len(lines) - 1
                        ):
                            y_cursor_draw = y_cursor + fm.ascent()
                            shadow_path.addText(seg_x + sx, y_cursor_draw + sy, font, seg_text)
                        y_cursor += fm.height()

                    if blur > 0:
                        steps = max(3, blur)
                        for i in range(steps, 0, -1):
                            frac = i / steps
                            layer_alpha = int(shadow_alpha * (1.0 - frac) * 0.15)
                            if layer_alpha < 1:
                                continue
                            sc = QColor(shadow_c)
                            sc.setAlpha(layer_alpha)
                            offset = frac * blur * 0.6
                            pen_w = spread + offset * 2
                            painter.setPen(QPen(sc, max(1, pen_w), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
                            painter.setBrush(sc)
                            painter.drawPath(shadow_path)
                        core_c = QColor(shadow_c)
                        core_c.setAlpha(shadow_alpha)
                        if spread > 0:
                            painter.setPen(QPen(core_c, spread, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
                        else:
                            painter.setPen(Qt.PenStyle.NoPen)
                        painter.setBrush(core_c)
                        painter.drawPath(shadow_path)
                    else:
                        shadow_c.setAlpha(shadow_alpha)
                        if spread > 0:
                            painter.setPen(QPen(shadow_c, spread, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
                        else:
                            painter.setPen(Qt.PenStyle.NoPen)
                        painter.setBrush(shadow_c)
                        painter.drawPath(shadow_path)

                # 使用 QPainterPath 确保描边永远在文字下方
                path = QPainterPath()
                y_cursor = start_y
                for i, line in enumerate(lines):
                    txt = line[0]
                    if not txt:
                        y_cursor += fm.height()
                        continue
                    for seg_text, seg_x in _iter_line_segments_for_draw(
                        txt, fm, start_x, box_w, align_name, i == len(lines) - 1
                    ):
                        y_cursor_draw = y_cursor + fm.ascent()
                        path.addText(seg_x, y_cursor_draw, font, seg_text)
                    y_cursor += fm.height()

                # ── 多层扩展描边（QImage 缓存加速）──
                if ov.get('stroke_expand_enabled', False):
                    se_layers = int(ov.get('stroke_expand_layers', 3))
                    se_step = float(ov.get('stroke_expand_step', 4))
                    se_feather = float(ov.get('stroke_expand_feather', 8))
                    se_colors_str = ov.get('stroke_expand_colors', '#FF0000,#00FF00,#0000FF')
                    se_colors = [c.strip() for c in se_colors_str.split(',') if c.strip()]
                    se_layer_widths = ov.get('stroke_expand_layer_widths', [])
                    se_layer_feathers = ov.get('stroke_expand_layer_feathers', [])
                    base_bw = int(ov.get('stroke_width', 2)) if ov.get('use_stroke', False) else 0
                    _lw_list = []
                    for li in range(se_layers):
                        _lw_list.append(float(se_layer_widths[li]) if li < len(se_layer_widths) else se_step)
                    _lf_tuple = tuple(float(se_layer_feathers[i]) if i < len(se_layer_feathers) else se_feather
                                      for i in range(se_layers))

                    # 缓存检查（overlay 内容不随时间变化，仅动画坐标变化）
                    _ov_se_stable = (in_prog >= 0.999 and out_prog >= 0.999
                                     and anim_in_type != 'floating')
                    _ov_se_key = (
                        content, se_layers, se_colors_str, tuple(_lw_list), _lf_tuple,
                        base_bw, font_family, int(font_size),
                        int(start_x * 10), int(start_y * 10), rotation,
                    )
                    _ov_se_hit = (_ov_se_stable
                                  and _SE_OV_CACHE.get("key") == _ov_se_key
                                  and _SE_OV_CACHE.get("img") is not None)

                    if _ov_se_hit:
                        painter.drawImage(0, 0, _SE_OV_CACHE["img"])
                    else:
                        if _ov_se_stable:
                            _vw = int(ov.get('_canvas_w', 1920))
                            _vh = int(ov.get('_canvas_h', 1080))
                            _se_img = QImage(_vw, _vh,
                                             QImage.Format.Format_ARGB32_Premultiplied)
                            _se_img.fill(Qt.GlobalColor.transparent)
                            _se_p = QPainter(_se_img)
                            _se_p.setRenderHint(QPainter.RenderHint.Antialiasing)
                            _render_se_to_painter(_se_p, path, se_layers,
                                                  se_colors, _lw_list, _lf_tuple,
                                                  se_feather, base_bw)
                            _se_p.end()
                            _SE_OV_CACHE["key"] = _ov_se_key
                            _SE_OV_CACHE["img"] = _se_img
                            painter.drawImage(0, 0, _se_img)
                        else:
                            _render_se_to_painter(painter, path, se_layers,
                                                  se_colors, _lw_list, _lf_tuple,
                                                  se_feather, base_bw)

                # ── 普通描边（在多层扩展之上，文字 fill 之下）──
                if ov.get('use_stroke', False):
                    pen = QPen(QColor(ov.get('stroke_color', '#000000')), int(ov.get('stroke_width', 2)))
                    painter.setPen(pen)
                    painter.setBrush(Qt.BrushStyle.NoBrush)
                    painter.drawPath(path)

                # ── 文字 fill（永远在最顶层，不被遮盖）──
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(text_c)
                painter.drawPath(path)
                painter.restore()
        finally:
            painter.restore()
        return logical_rect

    @staticmethod
    def draw_stickers(painter, ov, hit_recorder=None, ov_idx=None, default_blend_name=None, current_time=None):
        if current_time is not None:
            from cutora.render.keyframe import bake_host, local_time
            ov = bake_host(ov, local_time(ov, current_time))
        elements = ov.get("elements", [])
        if not isinstance(elements, list) or not elements:
            return
        elements = sorted(elements, key=lambda e: e.get("z", 0))
        ov_x = float(ov.get("x", 0))
        ov_y = float(ov.get("y", 0))
        ov_w = float(ov.get("w", 0))
        ov_h = float(ov.get("h", 0))
        image_rect = QRectF(ov_x, ov_y, ov_w, ov_h)
        t_preset = ov.get("transition_preset", "none")
        t_dur = float(ov.get("transition_duration", anim_engine.DEFAULT_TRANSITION_DURATION))
        ov_start = float(ov.get('start', 0.0))
        ov_end = float(ov.get('end', 0.0))
        t_op, t_scale, t_dx, t_dy = anim_engine.compute_transition_params(
            current_time, ov_start, ov_end, t_preset, t_dur
        )
        painter.save()
        try:
            if t_dx or t_dy:
                painter.translate(t_dx, t_dy)
            if abs(t_scale - 1.0) > 0.0001:
                ov_cx = ov_x + ov_w / 2.0
                ov_cy = ov_y + ov_h / 2.0
                painter.translate(ov_cx, ov_cy)
                painter.scale(t_scale, t_scale)
                painter.translate(-ov_cx, -ov_cy)
            for el_idx, el in enumerate(elements):
                if not isinstance(el, dict):
                    continue
                if current_time is not None:
                    from cutora.render.keyframe import bake_host, local_time
                    el = bake_host(el, local_time(el, current_time))
                el_op = t_op
                el_scale = 1.0
                if isinstance(el, dict) and el.get("sticker_id"):
                    from cutora.render.stickers import sticker_transform
                    xf = sticker_transform(el, ov, current_time)
                    el_op = float(t_op) * float(xf.alpha)
                    el_scale = float(xf.scale_x)
                rect = FrameRenderer.draw_sticker(
                    painter, ov, el,
                    default_blend_name=default_blend_name,
                    transition_opacity=el_op,
                    extra_scale=el_scale,
                )
                if hit_recorder is not None and rect is not None:
                    # [FIX-IMG] 命中框直接使用元素自身矩形，不再被容器矩形裁剪，
                    # 这样变换框始终贴合图片本身；容器 w/h 为 0 时也仍能选中图片
                    hit_rect = QRectF(rect)
                    if not hit_rect.isNull():
                        rec = {
                            'type': 'sticker',
                            'ov_idx': ov_idx,
                            'el_idx': el_idx,
                            'rect': hit_rect,
                        }
                        img = None
                        sid = str(el.get("sticker_id") or "").strip()
                        if sid:
                            from cutora.render.stickers import raster_sticker
                            px = max(1, int(round(max(
                                float(el.get("w") or 1), float(el.get("h") or 1)))))
                            img = raster_sticker(sid, px)
                        elif el.get("path"):
                            img = get_cached_image(el.get("path"))
                        if img is not None and not img.isNull():
                            from cutora.render.hitmask import attach_mask
                            op = (float(el.get("opacity", 255) or 255) / 255.0) * (
                                float(ov.get("opacity", 255) or 255) / 255.0)
                            trans = float(el_op)
                            if trans > 1.5:
                                trans = trans / 255.0
                            attach_mask(
                                rec, img,
                                rotation=float(el.get("rotation") or 0) + float(ov.get("rotation") or 0),
                                flip_x=bool(ov.get("flip_x")),
                                flip_y=bool(ov.get("flip_y")),
                                opacity=op * trans,
                            )
                        hit_recorder.append(rec)
        finally:
            painter.restore()

    @staticmethod
    def draw_sticker(painter, ov, el, default_blend_name=None, transition_opacity=1.0, extra_scale=1.0):
        result = QRectF()
        ov_x = float(ov.get("x", 0))
        ov_y = float(ov.get("y", 0))
        ov_w = float(ov.get("w", 0))
        ov_h = float(ov.get("h", 0))
        ov_rot = float(ov.get("rotation", 0))

        el_x = float(el.get("x", 0))
        el_y = float(el.get("y", 0))
        el_w = float(el.get("w", 100))
        el_h = float(el.get("h", 100))
        el_rot = float(el.get("rotation", 0))
        el_op = float(el.get("opacity", 255))
        ov_op = float(ov.get("opacity", 255))
        ov_scale = float(ov.get("scale", 1.0))

        abs_x = ov_x + el_x
        abs_y = ov_y + el_y
        # 应用 overlay 级别的缩放（以元素中心为基点）
        if ov_scale != 1.0:
            cx_s = abs_x + el_w / 2
            cy_s = abs_y + el_h / 2
            scaled_w = el_w * ov_scale
            scaled_h = el_h * ov_scale
            abs_x = cx_s - scaled_w / 2
            abs_y = cy_s - scaled_h / 2
            el_w = scaled_w
            el_h = scaled_h
        target = QRectF(abs_x, abs_y, el_w, el_h)

        painter.save()
        try:
            ov_cx = ov_x + ov_w / 2
            ov_cy = ov_y + ov_h / 2
            painter.translate(ov_cx, ov_cy)
            painter.rotate(ov_rot)
            painter.translate(-ov_cx, -ov_cy)

            blend_name = el.get("blend_mode") or default_blend_name or ov.get("blend_mode") or "normal"
            painter.setCompositionMode(BLEND_MODES.get(blend_name, _BLEND_NORMAL))
            from cutora.batch.layer_folders import opacity_mul_current
            painter.setOpacity((el_op / 255.0) * (ov_op / 255.0) * float(transition_opacity) * opacity_mul_current(ov))
            cx = abs_x + el_w / 2
            cy = abs_y + el_h / 2
            painter.translate(cx, cy)
            painter.rotate(el_rot)
            painter.translate(-cx, -cy)

            kind = el.get("kind")
            if kind == "image":
                img_path = el.get("path", "")
                sticker_id = str(el.get("sticker_id") or "").strip()
                cached = None
                if sticker_id:
                    from cutora.render.stickers import raster_sticker
                    px = max(1, int(round(max(float(el_w), float(el_h)))))
                    cached = raster_sticker(sticker_id, px)
                elif img_path:
                    cached = get_cached_image(img_path)
                if abs(float(extra_scale) - 1.0) > 0.0001:
                    painter.translate(cx, cy)
                    painter.scale(float(extra_scale), float(extra_scale))
                    painter.translate(-cx, -cy)
                if cached is not None and not cached.isNull():
                    img_path = img_path or sticker_id
                if img_path and cached is not None:
                    if cached and not cached.isNull():
                        # 翻转支持
                        flip_x = bool(ov.get("flip_x", False))
                        flip_y = bool(ov.get("flip_y", False))
                        if flip_x or flip_y:
                            cached = cached.mirrored(flip_x, flip_y)
                        cached = _apply_image_grade(cached, ov, el)
                        # 边缘羽化
                        feather = int(ov.get("feather", 0))
                        from cutora.render.textfx.fade import fade_active
                        need_fade = fade_active(el)
                        if feather > 0:
                            tw = int(target.width())
                            th = int(target.height())
                            if tw > 0 and th > 0:
                                scaled = cached.scaled(tw, th, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
                                feathered = QImage(tw, th, QImage.Format.Format_ARGB32_Premultiplied)
                                feathered.fill(Qt.GlobalColor.transparent)
                                fp = QPainter(feathered)
                                fp.drawImage(0, 0, scaled)
                                fp.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
                                f = min(feather, tw // 2, th // 2)
                                # 上边渐变
                                _ov_gt = QLinearGradient(0, 0, 0, f)
                                _ov_gt.setColorAt(0.0, QColor(0, 0, 0, 0))
                                _ov_gt.setColorAt(1.0, QColor(0, 0, 0, 255))
                                fp.fillRect(QRectF(0, 0, tw, f), QBrush(_ov_gt))
                                # 中间区域（保持不变）
                                if th - 2 * f > 0:
                                    fp.fillRect(QRectF(0, f, tw, th - 2 * f), QColor(0, 0, 0, 255))
                                # 下边渐变
                                _ov_gb = QLinearGradient(0, th - f, 0, th)
                                _ov_gb.setColorAt(0.0, QColor(0, 0, 0, 255))
                                _ov_gb.setColorAt(1.0, QColor(0, 0, 0, 0))
                                fp.fillRect(QRectF(0, th - f, tw, f), QBrush(_ov_gb))
                                # 左边渐变
                                _ov_gl = QLinearGradient(0, 0, f, 0)
                                _ov_gl.setColorAt(0.0, QColor(0, 0, 0, 0))
                                _ov_gl.setColorAt(1.0, QColor(0, 0, 0, 255))
                                fp.fillRect(QRectF(0, 0, f, th), QBrush(_ov_gl))
                                # 右边渐变
                                _ov_gr = QLinearGradient(tw - f, 0, tw, 0)
                                _ov_gr.setColorAt(0.0, QColor(0, 0, 0, 255))
                                _ov_gr.setColorAt(1.0, QColor(0, 0, 0, 0))
                                fp.fillRect(QRectF(tw - f, 0, f, th), QBrush(_ov_gr))
                                fp.end()
                                painter.drawImage(target, _fade_qimage(feathered, el))
                            else:
                                tw = max(1, int(round(target.width())))
                                th = max(1, int(round(target.height())))
                                scaled = cached.scaled(
                                    tw, th,
                                    Qt.AspectRatioMode.IgnoreAspectRatio,
                                    Qt.TransformationMode.SmoothTransformation)
                                painter.drawImage(
                                    target,
                                    scaled if not need_fade else _fade_qimage(scaled, el))
                        elif need_fade:
                            tw = max(1, int(round(target.width())))
                            th = max(1, int(round(target.height())))
                            scaled = cached.scaled(
                                tw, th,
                                Qt.AspectRatioMode.IgnoreAspectRatio,
                                Qt.TransformationMode.SmoothTransformation)
                            painter.drawImage(target, _fade_qimage(scaled, el))
                        else:
                            painter.drawImage(target, cached)
            result = target
        finally:
            painter.restore()
        return result

    @staticmethod
    def draw_subtitle(painter, event, s, current_time, video_w, video_h,
                       hit_recorder=None, seg_idx=None, dynamic_layout_list=None, _hit_char_offset=0,
                       backdrop=None):
        result = QRectF()
        if event.get("_inline_editing", False):
            return result
        from cutora.subtitle.stylepack import ensure_style_has_fx
        s = ensure_style_has_fx(
            s, seg_idx=seg_idx, cue=str((event or {}).get("text") or ""))
        if current_time is not None:
            from cutora.render.keyframe import bake_host, local_time
            s = bake_host(s, local_time(event if isinstance(event, dict) else s, current_time))
        from cutora.render.textfx import draw as textfx_draw
        return textfx_draw(
            painter, event, s, current_time, video_w, video_h, backdrop,
            hit_recorder=hit_recorder, seg_idx=seg_idx,
            dynamic_layout_list=dynamic_layout_list,
            _hit_char_offset=_hit_char_offset,
        )
