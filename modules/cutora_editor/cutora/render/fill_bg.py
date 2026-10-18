# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q117 · 画面没铺满时，背景填什么。

一条竖屏视频放进横幅里，两边就是黑的。这里给四种补法：

- ``blur``   原片放大、镜头模糊之后垫在底下（Luca 最常用的那种景深背景）
- ``mirror`` 把画面沿边镜像翻出去，接缝处像素是连着的
- ``tile``   平铺重复
- ``stretch`` 拉伸铺满（会变形，但边缘一定有像素）

预览（cutora/ui/canvas.py）和导出（cutora/render/render_worker.py）都调
``paint_fill``，**只有这一份实现**：两边各写一遍就是预览好看导出不一样的开始。
"""
from __future__ import annotations

import math
import threading
from collections import OrderedDict

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter

FILL_MODES = ("none", "blur", "mirror", "tile", "stretch")

from cutora.model.project_schema import BG_FILL_DEFAULTS as DEFAULTS  # noqa: E402

_MAX_SMALL = 96         # 模糊用的缩图长边上限：先缩再放，又快又软

# Q151 镜头模糊（Luca：0 不糊，给一点就糊成一片，调不细；要达芬奇 Lens Blur 那种
# 六边形光圈、亮点散成光斑、糊里还看得出东西的质感）。
BLUR_STYLES = ("lens", "legacy")
DEFAULT_HIGHLIGHTS = 0.35   # 达芬奇 Lens Blur 的 Highlights 默认就是 0.35
LENS_MAX_FRAC = 0.05        # 程度 100 → 光圈半径 = 长边 5%（1920 竖屏约 96 px）
LENS_CURVE = 1.6            # 程度 → 半径走幂曲线：前半段细，后半段才快
_LENS_WORK_R = 4.0          # 卷积时的工作半径（像素）：大光圈先缩图再卷
_LENS_WORK_MAX = 640        # 工作图长边上限
_HEX_ROT_DEG = 15.0         # 光圈转一点，光斑不是正放的六边形


def fill_spec(clip) -> dict:
    """读出这个片段的填充设置，缺的补默认值。"""
    raw = None
    if clip is not None:
        raw = (clip.get("bg_fill") if isinstance(clip, dict)
               else getattr(clip, "bg_fill", None))
    out = dict(DEFAULTS)
    if isinstance(raw, dict):
        for k in DEFAULTS:
            if k in raw and raw[k] is not None:
                out[k] = raw[k]
    mode = str(out.get("mode") or "none").lower()
    out["mode"] = mode if mode in FILL_MODES else "none"
    for k in ("blur", "scale", "x", "y", "rotation", "dim"):
        try:
            out[k] = float(out[k])
        except (TypeError, ValueError):
            out[k] = float(DEFAULTS[k])
    # Q151：没写模糊类型的老设置 —— 已经在用模糊背景的保持旧观感，其余（含新片段）用镜头模糊
    style = str(out.get("blur_style") or "").lower()
    if isinstance(raw, dict) and "blur_style" not in raw:
        style = "legacy" if str(raw.get("mode") or "").lower() == "blur" else "lens"
    out["blur_style"] = style if style in BLUR_STYLES else "lens"
    try:
        out["highlights"] = max(0.0, min(1.0, float(out.get("highlights"))))
    except (TypeError, ValueError):
        out["highlights"] = float(DEFAULTS.get("highlights", DEFAULT_HIGHLIGHTS))
    out["blur"] = max(0.0, min(100.0, out["blur"]))
    out["dim"] = max(0.0, min(1.0, out["dim"]))
    out["scale"] = max(0.05, min(8.0, out["scale"]))
    return out


# Q152-A：背景填充里能打关键帧的数（关键帧路径 = "bg_fill.<键>"）
FILL_KEYABLE = ("blur", "dim", "scale", "x", "y", "rotation")


def fill_spec_at(clip, timeline_t) -> dict:
    """fill_spec + 关键帧：预览和导出画背景时都用这个，传时间线时刻。

    Q155：这一帧有整幅背景（外部图片 / 外部视频 / 分屏单个视频撑满）时，片段自己的背景填充让位 → none。"""
    spec = fill_spec(clip)
    try:
        from cutora.render.backdrop import fill_suppressed
        if fill_suppressed():
            spec["mode"] = "none"
            return spec
    except Exception:
        pass
    if clip is None or timeline_t is None:
        return spec
    try:
        from cutora.render.keyframe import local_time, path_enabled, value_at
    except Exception:
        return spec
    t_local = None
    for key in FILL_KEYABLE:
        path = "bg_fill." + key
        if not path_enabled(clip, path):
            continue
        if t_local is None:
            t_local = local_time(clip, timeline_t)
        try:
            spec[key] = float(value_at(clip, path, t_local, spec[key]))
        except (TypeError, ValueError):
            continue
    if t_local is not None:
        spec = fill_spec({"bg_fill": spec})
    return spec


def set_fill(clip, **kw) -> dict:
    """属性面板改一项就调这个；返回写回去的完整设置。"""
    spec = fill_spec(clip)
    spec.update({k: v for k, v in kw.items() if k in DEFAULTS})
    spec = fill_spec({"bg_fill": spec})
    if isinstance(clip, dict):
        clip["bg_fill"] = spec
    else:
        clip.bg_fill = spec
    return spec


def covers(target_rect, dest_w, dest_h, eps: float = 0.5) -> bool:
    """前景已经把这块地方铺满了吗（铺满就不用画背景）。"""
    if target_rect is None:
        return False
    return (target_rect.left() <= eps
            and target_rect.top() <= eps
            and target_rect.right() >= float(dest_w) - eps
            and target_rect.bottom() >= float(dest_h) - eps)


def needs_fill(spec, target_rect, dest_w, dest_h) -> bool:
    if not spec or str(spec.get("mode") or "none") == "none":
        return False
    if dest_w <= 0 or dest_h <= 0:
        return False
    return not covers(target_rect, dest_w, dest_h)


# ----------------------------------------------------------------- 几何

def cover_rect(src_w, src_h, dest_w, dest_h, scale=1.0, rotation=0.0):
    """铺满整块地方的矩形（以中心为基准）。

    转过角度之后，外接框会变大，所以按 |cos|+|sin| 把边放大，
    转任何角度都不会把角露出来。
    """
    if src_w <= 0 or src_h <= 0 or dest_w <= 0 or dest_h <= 0:
        return QRectF(0, 0, float(dest_w), float(dest_h))
    rad = math.radians(float(rotation or 0.0))
    c, s = abs(math.cos(rad)), abs(math.sin(rad))
    need_w = dest_w * c + dest_h * s
    need_h = dest_w * s + dest_h * c
    ratio = max(need_w / float(src_w), need_h / float(src_h))
    w = src_w * ratio * float(scale)
    h = src_h * ratio * float(scale)
    return QRectF(-w / 2.0, -h / 2.0, w, h)


def contain_rect(src_w, src_h, dest_w, dest_h, scale=1.0):
    """完整装进去的矩形（以中心为基准），镜像/平铺以它为一块砖。"""
    if src_w <= 0 or src_h <= 0 or dest_w <= 0 or dest_h <= 0:
        return QRectF(0, 0, float(dest_w), float(dest_h))
    ratio = min(dest_w / float(src_w), dest_h / float(src_h))
    w = src_w * ratio * float(scale)
    h = src_h * ratio * float(scale)
    return QRectF(-w / 2.0, -h / 2.0, w, h)


# ----------------------------------------------------------------- 模糊

_BLUR_MEMO: OrderedDict = OrderedDict()
_BLUR_MEMO_LOCK = threading.Lock()
_BLUR_MEMO_MAX = 64


def blur_image(frame: QImage, amount: float, style: str = "legacy",
               highlights: float = DEFAULT_HIGHLIGHTS) -> QImage:
    """模糊一帧，返回小图（画的时候让 painter 双线性铺满）。

    style="lens"：Q151 镜头模糊（六边形光圈 + 高光散景），程度按 lens_radius_px
    细调；style="legacy"：Q117 的缩图 + 高斯（老工程原样）。
    """
    if frame is None or frame.isNull():
        return frame
    ck = int(frame.cacheKey())
    mkey = (ck, round(float(amount or 0.0), 3), str(style or "legacy"), round(float(highlights or 0.0), 3)) if ck else None
    if mkey is not None:
        with _BLUR_MEMO_LOCK:
            hit = _BLUR_MEMO.get(mkey)
            if hit is not None:
                _BLUR_MEMO.move_to_end(mkey)
                return hit
    if str(style or "legacy") == "lens":
        out = lens_blur(frame, amount, highlights)
    else:
        out = _legacy_blur(frame, amount)
    if mkey is not None and out is not None and out is not frame and not out.isNull():
        with _BLUR_MEMO_LOCK:
            _BLUR_MEMO[mkey] = out
            while len(_BLUR_MEMO) > _BLUR_MEMO_MAX:
                _BLUR_MEMO.popitem(last=False)
    return out


def _legacy_blur(frame: QImage, amount: float) -> QImage:
    """先缩小再放大的廉价模糊（Q117 原实现，老工程 / 老分屏用）。

    amount 0–100。缩到长边最多 96 像素再放回去，几乎不花时间。
    缺点（Q151 Luca）：一过 0 就缩到 96 px，稍微给一点就糊成一片，调不细。
    """
    if frame is None or frame.isNull():
        return frame
    amount = max(0.0, min(100.0, float(amount)))
    if amount <= 0.5:
        return frame
    w, h = frame.width(), frame.height()
    if w <= 0 or h <= 0:
        return frame
    long_side = max(w, h)
    # amount 越大缩得越狠：0 → 原样，100 → 长边 _MAX_SMALL/4
    target = long_side * (1.0 - amount / 100.0 * 0.985)
    target = max(8.0, min(float(_MAX_SMALL), target))
    ratio = target / float(long_side)
    sw = max(2, int(round(w * ratio)))
    sh = max(2, int(round(h * ratio)))
    small = _downscale(frame, sw, sh)
    return _gaussian(small, amount)


def _downscale(frame: QImage, sw: int, sh: int) -> QImage:
    """缩到 sw×sh。有 cv2 就用 INTER_AREA（每帧 <1 ms），没有就退回 Qt 的平滑缩放。

    Q130：Qt 的 SmoothTransformation 把 1080×1920 缩到 96 px 要 4 ms 以上，
    模糊背景每帧都走这一步，换成 cv2 后这一步几乎不占时间。
    """
    try:
        import cv2
        import numpy as np
    except ImportError:
        return frame.scaled(sw, sh, Qt.AspectRatioMode.IgnoreAspectRatio,
                            Qt.TransformationMode.SmoothTransformation)
    try:
        img = frame
        fmt = img.format()
        # 解码器给的是 BGR888（3 通道），覆层是 ARGB32；都直接在原缓冲上缩，
        # 不做整幅格式转换（那一步本身就要 3 ms）。
        if fmt == QImage.Format.Format_BGR888:
            ch = 3
        elif fmt in (QImage.Format.Format_ARGB32_Premultiplied,
                     QImage.Format.Format_ARGB32, QImage.Format.Format_RGB32):
            ch = 4
        else:
            img = img.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
            fmt = img.format()
            ch = 4
        w, h = img.width(), img.height()
        bpl = img.bytesPerLine()
        raw = img.constBits()
        try:
            buf = np.frombuffer(raw, dtype=np.uint8, count=h * bpl)
        except (TypeError, BufferError, ValueError):
            buf = np.frombuffer(bytes(raw), dtype=np.uint8)
        arr = buf.reshape((h, bpl))[:, :w * ch].reshape((h, w, ch))
        # 先按 2 的倍数金字塔降（高斯 + 抽样，本来就是要模糊），最后一步再面积缩放：
        # 1280→96 直接 INTER_AREA 要 2.4 ms，金字塔两级 + AREA 只要 0.9 ms。
        while arr.shape[0] >= sh * 4 and arr.shape[1] >= sw * 4 and min(arr.shape[:2]) > 8:
            arr = cv2.pyrDown(arr)
        out = cv2.resize(arr, (int(sw), int(sh)), interpolation=cv2.INTER_AREA)
        out = np.ascontiguousarray(out)
        return QImage(out.data, int(sw), int(sh), int(sw) * ch, fmt).copy()
    except Exception:
        from cutora.core.log import get_logger
        get_logger("cutora.render.fill_bg").exception("cv2 缩图失败，退回 Qt 缩放")
        return frame.scaled(sw, sh, Qt.AspectRatioMode.IgnoreAspectRatio,
                            Qt.TransformationMode.SmoothTransformation)


def blur_image_full(frame: QImage, amount: float, style: str = "legacy",
                    highlights: float = DEFAULT_HIGHLIGHTS) -> QImage:
    """和 blur_image 一样，但放回原尺寸（给需要整幅图的调用方）。"""
    small = blur_image(frame, amount, style, highlights)
    if small is frame or small is None or small.isNull():
        return frame
    return small.scaled(frame.width(), frame.height(), Qt.AspectRatioMode.IgnoreAspectRatio,
                        Qt.TransformationMode.SmoothTransformation)


def _gaussian(small: QImage, amount: float) -> QImage:
    """缩图上再来一道高斯，把缩放留下的棱角抹平。没有 cv2 就跳过。"""
    try:
        import cv2
        import numpy as np
    except ImportError:
        return small
    try:
        img = small.convertToFormat(QImage.Format.Format_RGBA8888)
        w, h = img.width(), img.height()
        buf = np.frombuffer(bytes(img.constBits()), dtype=np.uint8)
        arr = buf.reshape((h, img.bytesPerLine()))[:, :w * 4].reshape((h, w, 4))
        k = int(max(1, round(amount / 25.0))) * 2 + 1
        blurred = cv2.GaussianBlur(arr, (k, k), 0)
        out = QImage(bytes(blurred.tobytes()), w, h, w * 4,
                     QImage.Format.Format_RGBA8888)
        return out.copy()
    except Exception:
        from cutora.core.log import get_logger
        get_logger("cutora.render.fill_bg").exception("高斯模糊失败，退回缩放模糊")
        return small


# ----------------------------------------------------------------- 镜头模糊

def lens_radius_px(amount: float, long_side: float) -> float:
    """程度 0–100 → 光圈半径（像素，按原图长边）。

    幂曲线：10 → 0.13 %（1920 上 2.4 px，轻微失焦），45 → 1.4 %（27 px），
    100 → 5 %（96 px）。每一格都看得出差别，不会一给就糊死。"""
    a = max(0.0, min(100.0, float(amount or 0.0))) / 100.0
    return float(long_side) * LENS_MAX_FRAC * (a ** LENS_CURVE)


_HEX_CACHE: dict = {}


def hex_kernel(radius: float):
    """六边形光圈卷积核（4× 超采样抗锯齿，和为 1）。按 0.05 px 缓存。"""
    import numpy as np
    r = max(0.5, float(radius))
    key = round(r * 20.0)
    k = _HEX_CACHE.get(key)
    if k is not None:
        return k
    r = key / 20.0
    half = int(math.ceil(r))
    n = 2 * half + 1
    ss = 4
    coords = (np.arange(n * ss, dtype=np.float32) + 0.5) / ss - (n / 2.0)
    xx, yy = np.meshgrid(coords, coords)
    inner = r * math.cos(math.pi / 6.0)       # 六边形内切圆半径
    inside = np.ones_like(xx, dtype=bool)
    for i in range(3):
        ang = math.radians(_HEX_ROT_DEG + 30.0 + 60.0 * i)
        inside &= np.abs(xx * math.cos(ang) + yy * math.sin(ang)) <= inner
    k = inside.reshape(n, ss, n, ss).mean(axis=(1, 3)).astype(np.float32)
    tot = float(k.sum())
    if tot <= 0.0:
        k = np.zeros((1, 1), np.float32)
        k[0, 0] = 1.0
    else:
        k /= tot
    if len(_HEX_CACHE) > 256:
        _HEX_CACHE.clear()
    _HEX_CACHE[key] = k
    return k


_LUT: dict = {}


def _lens_luts(highlights: float):
    """(8 位 → 线性光（γ 2）, 亮度 → 高光权重) 两张查表，按 0.01 缓存。

    在线性光里卷，亮点才会散成光斑。高光不是直接提亮（那样整块亮色会一起变色，
    Q151 实测橙色块被提成了另一种橙），而是做加权平均的权重：平的地方权重处处
    一样、颜色不变；暗处里的一个亮点权重大，卷出去就成了一个亮的六边形光斑。"""
    import numpy as np
    key = int(round(max(0.0, min(1.0, float(highlights or 0.0))) * 100.0))
    pair = _LUT.get(key)
    if pair is None:
        v = np.arange(256, dtype=np.float32) / 255.0
        lin = (v * v).astype(np.float32)
        t = np.clip((v - 0.7) / 0.3, 0.0, 1.0)         # γ 空间 0.7 以上才算高光
        m = t * t * (3.0 - 2.0 * t)
        wt = (1.0 + (key / 100.0) * 40.0 * m * m).astype(np.float32)
        pair = (lin, wt)
        _LUT[key] = pair
    return pair


def _frame_array(frame: QImage):
    """QImage → (h, w, ch) uint8 视图（不复制）。BGR888 3 通道，其余转 ARGB32 系 4 通道。

    两种排布在内存里第 0 个通道都是 B。返回 (arr, ch, fmt, keepalive)。"""
    import numpy as np
    img = frame
    fmt = img.format()
    if fmt == QImage.Format.Format_BGR888:
        ch = 3
    elif fmt in (QImage.Format.Format_ARGB32_Premultiplied,
                 QImage.Format.Format_ARGB32, QImage.Format.Format_RGB32):
        ch = 4
    else:
        img = img.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
        fmt = img.format()
        ch = 4
    w, h = img.width(), img.height()
    bpl = img.bytesPerLine()
    raw = img.constBits()
    try:
        buf = np.frombuffer(raw, dtype=np.uint8, count=h * bpl)
    except (TypeError, BufferError, ValueError):
        buf = np.frombuffer(bytes(raw), dtype=np.uint8)
    arr = buf.reshape((h, bpl))[:, :w * ch].reshape((h, w, ch))
    return arr, ch, fmt, img


def lens_blur(frame: QImage, amount: float, highlights: float = DEFAULT_HIGHLIGHTS,
              radius_long_side=None, as_array: bool = False):
    """镜头模糊：线性光里用六边形光圈卷积，亮点加权，散成六边形光斑。

    返回工作分辨率的小图（≤ 640 长边），和 blur_image 一样交给 painter 铺满。
    没有 cv2 时退回旧的缩图模糊。radius_long_side：光圈按这个长边算（Q174 调整图层只模糊画面的一块，
    光圈要跟整幅画面走，不跟这一块的大小走）；不给 = 按 frame 自己的长边（原来的行为）。"""
    if frame is None or frame.isNull():
        return frame
    w, h = frame.width(), frame.height()
    if w <= 0 or h <= 0:
        return frame
    long_side = float(max(w, h))
    radius = lens_radius_px(amount, float(radius_long_side) if radius_long_side else long_side)
    if radius < 0.35:
        return _frame_array(frame)[0] if as_array else frame
    try:
        import cv2
        import numpy as np
    except ImportError:
        return _legacy_blur(frame, amount)
    try:
        arr, ch, fmt, _keep = _frame_array(frame)
        if as_array and ch != 4:
            arr = cv2.cvtColor(np.ascontiguousarray(arr), cv2.COLOR_BGR2BGRA)
            ch, fmt = 4, QImage.Format.Format_ARGB32_Premultiplied
        full = arr
        s = min(1.0, _LENS_WORK_R / radius, _LENS_WORK_MAX / long_side)
        sw = max(2, int(round(w * s)))
        sh = max(2, int(round(h * s)))
        if (sw, sh) != (w, h):
            if w >= sw * 2 and h >= sh * 2 and w * h > 300000:
                arr_s = cv2.resize(arr, (sw * 2, sh * 2), interpolation=cv2.INTER_LINEAR)
                small = cv2.resize(arr_s, (sw, sh), interpolation=cv2.INTER_AREA)
            else:
                while arr.shape[0] >= sh * 4 and arr.shape[1] >= sw * 4 and min(arr.shape[:2]) > 8:
                    arr = cv2.pyrDown(arr)
                small = cv2.resize(arr, (sw, sh), interpolation=cv2.INTER_AREA)
        else:
            small = np.array(arr)
        rw = radius * (sw / float(w))
        kern = hex_kernel(rw)
        col = np.ascontiguousarray(small[..., :3])
        lin_lut, wt_lut = _lens_luts(highlights)
        lin = cv2.LUT(col, lin_lut)
        if float(highlights or 0.0) > 0.0:
            # 高光从原图取：缩图会把一盏小灯平均成灰点，光斑就没了。原图先按
            # 一个工作像素的大小取最大值（膨胀 + 最近邻），亮点在小图里还是亮点。
            if (sw, sh) != (w, h):
                if w >= sw * 3 and h >= sh * 3 and w * h > 300000:
                    sub_hi = cv2.resize(full, (sw * 2, sh * 2), interpolation=cv2.INTER_NEAREST)
                    hi = cv2.dilate(sub_hi, np.ones((2, 2), np.uint8))
                else:
                    k = max(2, int(math.ceil(w / float(sw))))
                    hi = cv2.dilate(full, np.ones((k, k), np.uint8))   # 4 通道也直接膨胀，不先拆
                hi = cv2.resize(hi, (sw, sh), interpolation=cv2.INTER_NEAREST)
                hi = np.ascontiguousarray(hi[..., :3])
            else:
                hi = col
            extra = cv2.LUT(cv2.cvtColor(hi, cv2.COLOR_BGR2GRAY), wt_lut) - 1.0
            # 加权平均：平的地方 hi == col，权重处处一样，颜色不变；
            # 暗处一个亮点带着大权重和它原本的亮度卷出去，成一个亮的六边形。
            hot = cv2.LUT(hi, lin_lut)
            hot *= extra[..., None]
            hot += lin
            num = cv2.filter2D(hot, -1, kern, borderType=cv2.BORDER_REFLECT)
            extra += 1.0
            den = cv2.filter2D(extra, -1, kern, borderType=cv2.BORDER_REFLECT)
            out = num / den[..., None]
        else:
            out = cv2.filter2D(lin, -1, kern, borderType=cv2.BORDER_REFLECT)
        # 回到 γ 空间（开平方），convertScaleAbs 顺带饱和到 0–255
        bgr_u8 = cv2.convertScaleAbs(cv2.sqrt(out), alpha=255.0)
        if not as_array and (ch == 3 or fmt == QImage.Format.Format_RGB32 or int(small[..., 3].min()) >= 255):
            out_fmt = QImage.Format.Format_RGB32 if ch == 3 else fmt
            qout = QImage(sw, sh, out_fmt)
            dst = np.frombuffer(qout.bits(), dtype=np.uint8).reshape(
                sh, qout.bytesPerLine() // 4, 4)[:, :sw]
            cv2.cvtColor(bgr_u8, cv2.COLOR_BGR2BGRA, dst=dst)
            return qout
        res = np.empty((sh, sw, ch), dtype=np.uint8)
        res[..., :3] = bgr_u8
        if ch == 4 and int(small[..., 3].min()) >= 255:
            res[..., 3] = 255
        elif ch == 4:
            a = cv2.filter2D(small[..., 3].astype(np.float32), -1, kern,
                             borderType=cv2.BORDER_REFLECT)
            a = np.clip(a + 0.5, 0, 255).astype(np.uint8)
            res[..., 3] = a
            if fmt == QImage.Format.Format_ARGB32_Premultiplied:
                np.minimum(res[..., :3], a[..., None], out=res[..., :3])
        res = np.ascontiguousarray(res)
        if as_array:
            return res                      # Q180 调整图层：直接要数组，省掉 QImage 来回拷贝
        return QImage(res.data, sw, sh, sw * ch, fmt).copy()
    except Exception:
        from cutora.core.log import get_logger
        get_logger("cutora.render.fill_bg").exception("镜头模糊失败，退回缩图模糊")
        if as_array:
            return _frame_array(_legacy_blur(frame, amount))[0]
        return _legacy_blur(frame, amount)


# ----------------------------------------------------------------- 绘制

def paint_fill(painter: QPainter, frame: QImage, spec, dest_w, dest_h,
               target_rect=None) -> bool:
    """在 (0,0,dest_w,dest_h) 里画背景。画了返回 True。

    调用方必须在画前景之前调用，并且自己不要提前 save/restore —— 这里管好了。
    """
    if painter is None or frame is None or frame.isNull():
        return False
    spec = fill_spec({"bg_fill": spec})
    if not needs_fill(spec, target_rect, dest_w, dest_h):
        return False
    split_rect = getattr(painter, "_split_layer_rect", None)
    mode = spec["mode"]
    if split_rect is not None and mode == "stretch":
        mode = "blur"
        spec["blur_style"] = "lens"
    if split_rect is not None:
        dest = QRectF(float(split_rect[0]), float(split_rect[1]), float(split_rect[2]), float(split_rect[3]))
    else:
        dest = QRectF(0.0, 0.0, float(dest_w), float(dest_h))
    fill_w, fill_h = float(dest.width()), float(dest.height())
    painter.save()
    try:
        painter.setClipRect(dest)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.save()
        painter.translate(dest.center())
        painter.translate(float(spec["x"]), float(spec["y"]))
        if abs(spec["rotation"]) > 1e-6:
            painter.rotate(float(spec["rotation"]))
        if mode == "stretch":
            r = QRectF(-fill_w / 2.0, -fill_h / 2.0, fill_w, fill_h)
            sc = float(spec["scale"])
            if abs(sc - 1.0) > 1e-6:
                r = QRectF(r.x() * sc, r.y() * sc, r.width() * sc, r.height() * sc)
            painter.drawImage(r, frame)
        elif mode == "blur":
            # Q130：模糊图不再先放大回 1080×1920 再画（那一步每帧 8–9 ms），
            # 直接让 painter 用双线性把小图铺满目标框 —— 同样是双线性放大，
            # 结果肉眼无差，省掉一整张全画布的中间图。
            img = blur_image(frame, spec["blur"], spec["blur_style"], spec["highlights"])
            painter.drawImage(
                cover_rect(frame.width(), frame.height(), fill_w, fill_h,
                           spec["scale"], spec["rotation"]),
                img)
        elif mode in ("mirror", "tile"):
            _paint_tiles(painter, frame, spec, fill_w, fill_h,
                         mirrored=(mode == "mirror"))
        else:
            painter.restore()
            return False
        # 背景画完先把自己的位移转角退掉，压暗那一层要盖的是整块地方，
        # 不是被转过的背景（resetTransform 会连调用方的坐标系一起抹掉，不能用）
        painter.restore()
        dim = float(spec["dim"])
        if dim > 0.001:
            painter.fillRect(dest, QColor(0, 0, 0, int(round(255 * dim))))
    finally:
        painter.restore()
    if split_rect is not None:
        try:
            painter._split_filled = True
        except Exception:
            pass
    return True


def _paint_tiles(painter, frame, spec, dest_w, dest_h, mirrored: bool):
    """以中心那块为基准，一圈圈铺出去，直到盖住整块地方。

    ``mirrored`` 时相邻的一块左右/上下翻过来，接缝两边的像素是连着的，
    看不出拼接线。
    """
    base = contain_rect(frame.width(), frame.height(), dest_w, dest_h,
                        float(spec["scale"]))
    tw, th = base.width(), base.height()
    if tw < 1.0 or th < 1.0:
        return
    # 转了角度就多铺几圈，别把角空出来
    rad = math.radians(float(spec["rotation"] or 0.0))
    reach_w = dest_w * abs(math.cos(rad)) + dest_h * abs(math.sin(rad))
    reach_h = dest_w * abs(math.sin(rad)) + dest_h * abs(math.cos(rad))
    nx = int(math.ceil((reach_w / 2.0 - tw / 2.0) / tw)) + 1
    ny = int(math.ceil((reach_h / 2.0 - th / 2.0) / th)) + 1
    nx = max(0, min(nx, 32))
    ny = max(0, min(ny, 32))
    cache = {}
    for iy in range(-ny, ny + 1):
        for ix in range(-nx, nx + 1):
            fx = mirrored and (ix % 2 != 0)
            fy = mirrored and (iy % 2 != 0)
            key = (bool(fx), bool(fy))
            img = cache.get(key)
            if img is None:
                img = frame.mirrored(bool(fx), bool(fy)) if (fx or fy) else frame
                cache[key] = img
            painter.drawImage(
                QRectF(base.x() + ix * tw, base.y() + iy * th, tw, th), img)
