# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q152 · 把一个图层的抠像 + 遮罩套到它的像素上。预览和导出都只调这里。

apply_layer_fx(frame, host, *, px_scale=1.0) -> QImage
    frame : 图层这一刻的像素（视频 = 解出来的那一帧，已经裁切 / 翻转 / 调色 / 渐隐；
            图片 = 原图），BGR888 / ARGB32 系都认
    host  : Clip 对象或覆层 dict（读 keyer / masks）
    px_scale : 画布像素 → 图层像素的系数（feather / expand 的单位是画布像素）
没有启用的抠像也没有遮罩 → 原样返回同一个对象（老工程零改动）。
有 → 返回 ARGB32_Premultiplied 的新图。按（帧的 cacheKey, 参数签名）缓存最近几张。
"""
from __future__ import annotations

import threading
from collections import OrderedDict

import numpy as np
from PySide6.QtGui import QImage

from cutora.core.log import get_logger
from cutora.fx import ai_matte as A
from cutora.fx import graph as G
from cutora.fx import keyer as K
from cutora.fx import masks as M

_LOG = get_logger("cutora.fx.layer_fx")
_CACHE: "OrderedDict[tuple, QImage]" = OrderedDict()
_CACHE_MAX = 12
_CACHE_LOCK = threading.Lock()


def layer_has_fx(host) -> bool:
    return G.has_graph(host) or K.is_active(host) or M.has_masks(host) or A.is_active(host)


def _matte_key(host, src):
    """AI 抠像这一帧的缓存键：(设置签名, 用到的源帧号)；没启用 / 没给源时间 → None。"""
    if src is None or not A.is_active(host):
        return None
    sp = A.spec(host)
    fi = A.frame_index_for(sp, float(src.get("src_t") or 0.0))
    if fi is None:
        return None
    from cutora.fx import matte_store as S
    return (A.signature(sp), fi, S.index_version(sp["session"]), tuple(src.get("crop", (0, 0, 0, 0))),
            tuple(src.get("flip", (False, False))), src.get("order", "crop_flip"))


def fx_signature(host, px_scale: float = 1.0, src: dict | None = None, view: str | None = None) -> tuple | None:
    if G.has_graph(host):
        # Q152-F：有节点图就只看图（老键不生效）；AI 抠像节点各自带上这一帧的缓存版本
        g = G.graph_of(host)
        ai = tuple(_matte_key(n["params"], src) for n in G.ai_nodes(host))
        return (G.signature(g), round(float(px_scale), 4), ai,
                None if src is None else (round(float(src.get("src_t") or 0.0), 4),), view)
    k = K.spec(host)
    ms = M.masks_of(host)
    mk = _matte_key(host, src)
    if not k["enabled"] and not ms and mk is None:
        return None
    return (K.signature(k) if k["enabled"] else None, M.signature(ms), round(float(px_scale), 4), mk)


def _as_arrays(frame: QImage):
    """QImage → (rgb (h,w,3) uint8, alpha (h,w) float32 或 None)。

    Q180：直接读 QImage 的内存（不再先 bytes() 整张拷一遍），通道换序交给 cv2（多线程）—— 结果逐位不变。"""
    import cv2
    fmt = frame.format()
    if fmt == QImage.Format.Format_BGR888:
        img = frame
        ch = 3
    elif fmt in (QImage.Format.Format_ARGB32, QImage.Format.Format_ARGB32_Premultiplied,
                 QImage.Format.Format_RGB32):
        img = frame.convertToFormat(QImage.Format.Format_ARGB32) if fmt != QImage.Format.Format_ARGB32 else frame
        ch = 4
    else:
        img = frame.convertToFormat(QImage.Format.Format_ARGB32)
        ch = 4
    w, h = img.width(), img.height()
    bpl = img.bytesPerLine()
    buf = np.frombuffer(img.constBits(), dtype=np.uint8, count=bpl * h)
    arr = buf.reshape((h, bpl))[:, :w * ch].reshape((h, w, ch))
    if ch == 3:
        rgb = cv2.cvtColor(np.ascontiguousarray(arr), cv2.COLOR_BGR2RGB)          # BGR → RGB
        alpha = None
    else:
        rgb = cv2.cvtColor(np.ascontiguousarray(arr), cv2.COLOR_BGRA2RGB)         # BGRA → RGB
        alpha = None
        if not (fmt == QImage.Format.Format_RGB32 or not frame.hasAlphaChannel()):
            alpha = arr[..., 3].astype(np.float32) / 255.0
    del buf, arr
    return rgb, alpha


def _to_qimage(rgb: np.ndarray, alpha) -> QImage:
    import cv2
    h, w = rgb.shape[:2]
    if rgb.dtype != np.uint8 or rgb.ndim != 3 or rgb.shape[2] != 3:
        return _to_qimage_float(rgb, alpha)
    rgb = np.ascontiguousarray(rgb)
    if alpha is None:
        a = np.full((h, w), 255, dtype=np.uint8)
        rgbp = rgb
    else:
        a = cv2.convertScaleAbs(np.ascontiguousarray(alpha, dtype=np.float32), alpha=255.0)
        # 预乘：round(rgb × a / 255)。cv2 的 uint8 乘法带 scale 和以前 float32 那条路 65536 种组合逐个相同
        rgbp = cv2.multiply(rgb, cv2.merge([a, a, a]), scale=1.0 / 255.0)
    out = np.empty((h, w, 4), dtype=np.uint8)
    cv2.mixChannels([rgbp, a], [out], [0, 2, 1, 1, 2, 0, 3, 3])      # RGB + A → BGRA
    return QImage(out.data, w, h, w * 4, QImage.Format.Format_ARGB32_Premultiplied).copy()


def _to_qimage_float(rgb: np.ndarray, alpha) -> QImage:
    import cv2
    h, w = rgb.shape[:2]
    if alpha is None:
        a = np.full((h, w), 255, dtype=np.uint8)
        rgbp = rgb
    else:
        a = cv2.convertScaleAbs(np.ascontiguousarray(alpha, dtype=np.float32), alpha=255.0)
        # 预乘：rgb × a / 255（cv2 在 float32 上做，比 numpy uint16 快一倍）
        af = cv2.merge([a, a, a]).astype(np.float32) * np.float32(1.0 / 255.0)
        rgbp = cv2.convertScaleAbs(cv2.multiply(rgb.astype(np.float32), af))
    out = cv2.merge([np.ascontiguousarray(rgbp[..., 2]), np.ascontiguousarray(rgbp[..., 1]),
                     np.ascontiguousarray(rgbp[..., 0]), a])
    out = np.ascontiguousarray(out)
    return QImage(out.data, w, h, w * 4, QImage.Format.Format_ARGB32_Premultiplied).copy()


def apply_ai_matte(rgb, alpha, host, px_scale: float, src: dict):
    """AI 抠像一步（流水线和节点图共用）：按 view 合成 / 黑白 Matte / 彩色叠加。"""
    h, w = rgb.shape[:2]
    sp = A.spec(host)
    a_ai = A.alpha_for_frame(host, src, w, h, px_scale=px_scale, guide_rgb=rgb if sp["refine"] else None)
    if a_ai is None:
        return rgb, alpha
    view = sp["view"]
    if view == "matte":
        g = np.clip(a_ai * 255.0 + 0.5, 0, 255).astype(np.uint8)
        return np.repeat(g[..., None], 3, axis=-1), None
    if view == "overlay":
        ov = A.overlay_rgba(host, src, w, h, px_scale=px_scale)
        if ov is not None:
            a_ov = ov[..., 3:4].astype(np.float32) / 255.0
            col = ov[..., 2::-1].astype(np.float32)            # BGRA → RGB（预乘）
            rgb = np.clip(rgb.astype(np.float32) * (1.0 - a_ov) + col + 0.5, 0, 255).astype(np.uint8)
        return rgb, alpha
    return rgb, (a_ai if alpha is None else alpha * a_ai)


def apply_arrays(rgb: np.ndarray, alpha, host, px_scale: float = 1.0, src: dict | None = None,
                 view: str | None = None):
    """numpy 版（导出的数组路径 / 测试用）：返回 (rgb, alpha 或 None)。

    有节点图（fx_graph）→ 按图求值；否则老流水线：蓝绿幕 → AI 抠像 → 形状遮罩（三者的 alpha 相乘）。"""
    if G.has_graph(host):
        return G.evaluate(rgb, alpha, host, px_scale, src, view=view)
    k = K.spec(host)
    if k["enabled"]:
        rgb, a_key = K.apply(rgb, k)
        if a_key is not None:
            ak = a_key.astype(np.float32) / 255.0
            alpha = ak if alpha is None else alpha * ak
    if src is not None and A.is_active(host):
        rgb, alpha = apply_ai_matte(rgb, alpha, host, px_scale, src)
    ms = M.masks_of(host)
    if ms:
        h, w = rgb.shape[:2]
        total = M.combine(ms, w, h, px_scale)
        if total is not None:
            alpha = total if alpha is None else alpha * total
    return rgb, alpha


def _gpu_eligible(host, src, view) -> bool:
    """能交给显卡的：老流水线（没有节点图）、没有 AI 抠像、不是在看 matte / 溢色。"""
    if view is not None or G.has_graph(host) or (src is not None and A.is_active(host)):
        return False
    if A.is_active(host):
        return False
    k = K.spec(host)
    return (not k["enabled"]) or k["view"] == "comp"


def _frame_bgr_array(frame: QImage):
    """QImage → (arr (h, w, 3|4) uint8 视图 BGR/BGRA, keepalive)。预乘的先转成直通（和 _as_arrays 一样）。"""
    fmt = frame.format()
    if fmt == QImage.Format.Format_BGR888:
        img, ch = frame, 3
    elif fmt == QImage.Format.Format_ARGB32:
        img, ch = frame, 4
    elif fmt == QImage.Format.Format_RGB32:
        img, ch = frame, 4
    else:
        img, ch = frame.convertToFormat(QImage.Format.Format_ARGB32), 4
    w, h = img.width(), img.height()
    bpl = img.bytesPerLine()
    buf = np.frombuffer(img.constBits(), dtype=np.uint8, count=bpl * h)
    arr = buf.reshape((h, bpl))[:, :w * ch].reshape((h, w, ch))
    if ch == 4 and (fmt == QImage.Format.Format_RGB32 or not frame.hasAlphaChannel()):
        arr = np.ascontiguousarray(arr[..., :3])          # 没有 alpha：当 3 通道
    return arr, img


def _apply_gpu(frame: QImage, host, px_scale: float, be):
    """Q181：抠像 + 遮罩 + 预乘一次在显卡上做完。"""
    arr, keep = _frame_bgr_array(frame)
    h, w = arr.shape[:2]
    k = K.spec(host)
    ms = M.masks_of(host)
    mask = M.combine(ms, w, h, px_scale) if ms else None        # 遮罩光栅化有缓存（CPU）
    out = be.key_layer(arr, k if k["enabled"] else None, mask, bgr=True)
    del arr, keep
    out = np.ascontiguousarray(out)
    return QImage(out.data, w, h, w * 4, QImage.Format.Format_ARGB32_Premultiplied).copy()


def apply_layer_fx(frame: QImage, host, *, px_scale: float = 1.0, src: dict | None = None,
                   view: str | None = None, _force_cpu: bool = False) -> QImage:
    """src（可选）= AI 抠像要的源帧信息 {"src_t", "crop", "flip", "order"}，见 ai_matte.alpha_for_frame。
    view（可选，只给预览）= 节点图查看器：只看到某个节点的输出。
    _force_cpu：测试 / 自检用，跳过显卡。"""
    if frame is None or frame.isNull() or host is None:
        return frame
    sig = fx_signature(host, px_scale, src, view)
    if sig is None:
        return frame
    key = (frame.cacheKey(), sig)
    with _CACHE_LOCK:
        hit = _CACHE.get(key)
        if hit is not None:
            _CACHE.move_to_end(key)
    if hit is not None:
        return hit
    out = None
    if not _force_cpu and _gpu_eligible(host, src, view):
        from cutora.fx import gpu as _gpu
        be = _gpu.backend()
        if be is not None:
            try:
                out = _apply_gpu(frame, host, px_scale, be)
            except Exception as e:
                _gpu.disable("图层抠像 / 遮罩：%s: %s" % (type(e).__name__, e))
                out = None
    if out is None:
        try:
            rgb, alpha = _as_arrays(frame)
            rgb, alpha = apply_arrays(rgb, alpha, host, px_scale, src, view)
            out = _to_qimage(rgb, alpha)
        except Exception:
            _LOG.exception("图层抠像 / 遮罩失败，原样画")
            return frame
    with _CACHE_LOCK:        # Q171：主画布和监视器渲染线程共用这份缓存
        _CACHE[key] = out
        while len(_CACHE) > _CACHE_MAX:
            _CACHE.popitem(last=False)
    return out


def clear_cache() -> None:
    with _CACHE_LOCK:
        _CACHE.clear()
    M.clear_cache()


def source_ctx(clip, src_t, *, order: str = "crop_flip") -> dict:
    """渲染端拼 AI 抠像要的源帧信息：源时间 + 这个图层的裁切 / 翻转（预览和导出的顺序不同）。"""
    def g(k, d=0):
        return (clip.get(k, d) if isinstance(clip, dict) else getattr(clip, k, d)) or d
    try:
        crop = (int(g("crop_left")), int(g("crop_right")), int(g("crop_top")), int(g("crop_bottom")))
    except (TypeError, ValueError):
        crop = (0, 0, 0, 0)
    return {"src_t": float(src_t or 0.0), "crop": crop,
            "flip": (bool(g("flip_x", False)), bool(g("flip_y", False))), "order": order}


def mask_px_scale(frame_w: float, drawn_w: float) -> float:
    """画布像素 → 图层像素：帧宽 / 它在画布上占的宽。"""
    try:
        return float(frame_w) / float(drawn_w) if drawn_w else 1.0
    except (TypeError, ValueError, ZeroDivisionError):
        return 1.0


def mask_canvas_image(painter_size, ov, draw_fn) -> QImage | None:
    """覆层（文字 / 图片）带遮罩时：先画到整幅透明图上，再按覆层的框把遮罩套上去。

    painter_size: (canvas_w, canvas_h)；draw_fn(painter) 把覆层画进去。
    遮罩坐标在覆层自己的框里（x, y, w, h, rotation），所以把遮罩按同样的变换画到画布大小的
    alpha 图上，再乘到覆层层上。"""
    import cv2
    from PySide6.QtGui import QPainter
    cw, ch = int(painter_size[0]), int(painter_size[1])
    if cw <= 0 or ch <= 0:
        return None
    ms = M.masks_of(ov)
    if not ms:
        return None
    layer = QImage(cw, ch, QImage.Format.Format_ARGB32_Premultiplied)
    layer.fill(0)
    p = QPainter(layer)
    try:
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        draw_fn(p)
    finally:
        p.end()
    try:
        x, y = float(ov.get("x", 0) or 0), float(ov.get("y", 0) or 0)
        w, h = max(1.0, float(ov.get("w", 1) or 1)), max(1.0, float(ov.get("h", 1) or 1))
        rot = float(ov.get("rotation", 0) or 0)
        sc = float(ov.get("scale", 1.0) or 1.0)
    except (TypeError, ValueError):
        return layer
    bw, bh = max(1, int(round(w))), max(1, int(round(h)))
    total = M.combine(ms, bw, bh, 1.0)
    if total is None:
        return layer
    # 把框坐标的遮罩变换到画布：先缩放（整体缩放绕中心）再旋转再平移
    import math
    cx, cy = x + w / 2.0, y + h / 2.0
    a = math.radians(rot)
    c, s = math.cos(a), math.sin(a)
    # 源框中心 (bw/2, bh/2) → 画布 (cx, cy)
    mat = np.array([[c * sc, -s * sc, cx - (c * sc * bw / 2.0 - s * sc * bh / 2.0)],
                    [s * sc, c * sc, cy - (s * sc * bw / 2.0 + c * sc * bh / 2.0)]], dtype=np.float32)
    canvas_mask = cv2.warpAffine(total, mat, (cw, ch), flags=cv2.INTER_LINEAR,
                                 borderMode=cv2.BORDER_CONSTANT, borderValue=0.0)
    buf = np.frombuffer(bytes(layer.constBits()), dtype=np.uint8).reshape((ch, layer.bytesPerLine()))
    arr = buf[:, :cw * 4].reshape((ch, cw, 4)).astype(np.float32)
    arr *= canvas_mask[..., None]
    out = np.ascontiguousarray(np.clip(arr + 0.5, 0, 255).astype(np.uint8))
    return QImage(out.data, cw, ch, cw * 4, QImage.Format.Format_ARGB32_Premultiplied).copy()
