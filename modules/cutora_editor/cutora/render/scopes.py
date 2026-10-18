# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Preview scopes: waveform / RGB parade / vectorscope / histogram.

Computed on a 1/4-resolution copy, every 3rd frame. Skip frames return
immediately so the preview thread is not blocked.
"""
from __future__ import annotations

import numpy as np

from cutora.core.log import get_logger

_LOG = get_logger("cutora.render.scopes")

SCOPE_DOWNSCALE = 4
SCOPE_EVERY = 3
_MODES = ("off", "luma", "parade", "vector", "hist")


def _rgba_u8(img):
    from PySide6.QtGui import QImage
    conv = img.convertToFormat(QImage.Format.Format_RGBA8888)
    w, h = conv.width(), conv.height()
    bpl = conv.bytesPerLine()
    raw = bytes(conv.constBits())
    arr = np.frombuffer(raw, dtype=np.uint8)
    if arr.size < h * bpl:
        arr = np.frombuffer(conv.bits(), dtype=np.uint8)
    return arr.reshape((h, bpl))[:, : w * 4].reshape((h, w, 4)).copy()


def _luma_u8(rgb):
    r = rgb[..., 0].astype(np.float32)
    g = rgb[..., 1].astype(np.float32)
    b = rgb[..., 2].astype(np.float32)
    y = 0.2126 * r + 0.7152 * g + 0.0722 * b
    return np.clip(np.rint(y), 0, 255).astype(np.uint8)


def histogram(rgb_u8):
    rgb = rgb_u8[..., :3]
    bins = np.arange(257)
    hr, _ = np.histogram(rgb[..., 0], bins=bins)
    hg, _ = np.histogram(rgb[..., 1], bins=bins)
    hb, _ = np.histogram(rgb[..., 2], bins=bins)
    hy, _ = np.histogram(_luma_u8(rgb), bins=bins)
    return {"r": hr, "g": hg, "b": hb, "y": hy}


def waveform(rgb_u8, bins=256):
    """Per-column luma histogram. Shape (bins, width)."""
    y = _luma_u8(rgb_u8[..., :3])
    h, w = y.shape
    out = np.zeros((int(bins), w), dtype=np.float32)
    cols = np.broadcast_to(np.arange(w, dtype=np.int32), y.shape)
    np.add.at(out, (y.ravel() * (int(bins) - 1) // 255, cols.ravel()), 1.0)
    peak = float(out.max()) or 1.0
    return (out / peak).astype(np.float32)


def parade(rgb_u8, bins=256):
    rgb = rgb_u8[..., :3]
    h, w, _ = rgb.shape
    out = np.zeros((int(bins), w, 3), dtype=np.float32)
    cols = np.broadcast_to(np.arange(w, dtype=np.int32), (h, w))
    for c in range(3):
        ch = rgb[..., c]
        np.add.at(out[..., c], (ch.ravel() * (int(bins) - 1) // 255, cols.ravel()), 1.0)
        peak = float(out[..., c].max()) or 1.0
        out[..., c] /= peak
    return out.astype(np.float32)


def vectorscope(rgb_u8, size=128):
    rgb = rgb_u8[..., :3].astype(np.float32) / 255.0
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    cb = -0.168736 * r - 0.331264 * g + 0.5 * b
    cr = 0.5 * r - 0.418688 * g - 0.081312 * b
    n = int(size)
    u = np.clip(np.rint((cb + 0.5) * (n - 1)), 0, n - 1).astype(np.int32)
    v = np.clip(np.rint((1.0 - (cr + 0.5)) * (n - 1)), 0, n - 1).astype(np.int32)
    acc = np.zeros((n, n), dtype=np.float32)
    np.add.at(acc, (v.ravel(), u.ravel()), 1.0)
    peak = float(acc.max()) or 1.0
    return (acc / peak).astype(np.float32)


class ScopeSampler:
    def __init__(self):
        self._n = 0
        self.compute_count = 0
        self.last_size = (0, 0)
        self.last_source_size = (0, 0)
        self.last_histogram = None
        self.last_waveform = None
        self.last_parade = None
        self.last_vectorscope = None
        self.mode = "off"

    def set_mode(self, mode: str) -> None:
        m = str(mode or "off")
        if m not in _MODES:
            _LOG.warning("未知示波器模式 %r，已回到 off", mode)
            m = "off"
        self.mode = m

    def feed(self, qimage) -> bool:
        """Return True when the expensive path ran. Skip 2 of every 3 frames."""
        if qimage is None or qimage.isNull():
            return False
        self.last_source_size = (int(qimage.width()), int(qimage.height()))
        self._n += 1
        if self._n % SCOPE_EVERY != 1:
            return False
        return self.compute(qimage)

    def compute(self, qimage) -> bool:
        """Always run the 1/4-res path. Caller decides skip / thread."""
        if qimage is None or qimage.isNull():
            return False
        self.last_source_size = (int(qimage.width()), int(qimage.height()))
        from PySide6.QtCore import Qt
        w = max(1, int(qimage.width()) // SCOPE_DOWNSCALE)
        h = max(1, int(qimage.height()) // SCOPE_DOWNSCALE)
        small = qimage.scaled(w, h, Qt.AspectRatioMode.IgnoreAspectRatio,
                              Qt.TransformationMode.FastTransformation)
        self.last_size = (int(small.width()), int(small.height()))
        try:
            arr = _rgba_u8(small)
            self.last_histogram = histogram(arr)
            self.last_waveform = waveform(arr)
            self.last_parade = parade(arr)
            self.last_vectorscope = vectorscope(arr)
        except Exception:
            _LOG.exception("示波器计算失败 %sx%s", w, h)
            return False
        self.compute_count += 1
        return True

    def overlay_qimage(self, width, height):
        """ARGB overlay for the preview. Empty if mode is off or no sample yet."""
        from PySide6.QtGui import QImage, QColor, QPainter, QPen
        w, h = max(8, int(width)), max(8, int(height))
        img = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(QColor(0, 0, 0, 0))
        mode = self.mode
        if mode == "off":
            return img
        painter = QPainter(img)
        try:
            painter.fillRect(0, 0, w, h, QColor(0, 0, 0, 150))
            if mode == "luma" and self.last_waveform is not None:
                _paint_waveform(painter, self.last_waveform, w, h, QColor(80, 220, 90, 220))
            elif mode == "parade" and self.last_parade is not None:
                _paint_parade(painter, self.last_parade, w, h)
            elif mode == "vector" and self.last_vectorscope is not None:
                _paint_vector(painter, self.last_vectorscope, w, h)
            elif mode == "hist" and self.last_histogram is not None:
                _paint_hist(painter, self.last_histogram, w, h)
        except Exception:
            _LOG.exception("示波器叠加绘制失败 mode=%s", mode)
        finally:
            painter.end()
        return img


def _paint_waveform(painter, wf, w, h, color):
    bins, cols = wf.shape[:2]
    for x in range(w):
        src_x = min(cols - 1, int(x * cols / max(1, w)))
        col = wf[:, src_x]
        peak = int(np.argmax(col))
        y = int((1.0 - peak / max(1, bins - 1)) * (h - 1))
        painter.setPen(color)
        painter.drawLine(x, h - 1, x, y)


def _paint_parade(painter, par, w, h):
    from PySide6.QtGui import QColor
    colors = (QColor(220, 60, 50, 200), QColor(60, 200, 70, 200), QColor(50, 90, 220, 200))
    bins, cols, _ch = par.shape
    third = max(1, w // 3)
    for c, color in enumerate(colors):
        x0 = c * third
        for x in range(third):
            src_x = min(cols - 1, int(x * cols / max(1, third)))
            peak = int(np.argmax(par[:, src_x, c]))
            y = int((1.0 - peak / max(1, bins - 1)) * (h - 1))
            painter.setPen(color)
            painter.drawLine(x0 + x, h - 1, x0 + x, y)


def _paint_vector(painter, acc, w, h):
    from PySide6.QtGui import QColor, QImage
    n = int(acc.shape[0])
    rgb = np.zeros((n, n, 4), dtype=np.uint8)
    a = np.clip(acc * 255.0, 0, 255).astype(np.uint8)
    rgb[..., 1] = a
    rgb[..., 2] = a
    rgb[..., 3] = np.clip(a.astype(np.int16) * 2, 0, 180).astype(np.uint8)
    src = QImage(rgb.data, n, n, n * 4, QImage.Format.Format_RGBA8888).copy()
    side = min(w, h)
    x = (w - side) // 2
    y = (h - side) // 2
    painter.drawImage(x, y, src.scaled(side, side))
    painter.setPen(QColor(180, 180, 180, 160))
    painter.drawEllipse(x, y, side - 1, side - 1)


def _paint_hist(painter, hist, w, h):
    from PySide6.QtGui import QColor
    colors = {
        "r": QColor(220, 60, 50, 180),
        "g": QColor(60, 200, 70, 180),
        "b": QColor(50, 90, 220, 180),
        "y": QColor(230, 230, 230, 200),
    }
    for key, color in colors.items():
        arr = hist.get(key)
        if arr is None or len(arr) == 0:
            continue
        peak = float(np.max(arr)) or 1.0
        n = len(arr)
        painter.setPen(color)
        for i in range(n):
            x = int(i * (w - 1) / max(1, n - 1))
            y = int((1.0 - arr[i] / peak) * (h - 1))
            painter.drawLine(x, h - 1, x, y)
