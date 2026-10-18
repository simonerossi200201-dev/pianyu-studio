# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Batch-load thumbnail grid. The preview itself is the app VideoCanvas."""
from __future__ import annotations

import os

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QImage, QImageReader, QPainter, QPixmap
from PySide6.QtWidgets import QListWidget, QListWidgetItem

from cutora.batch.batch_load import IMAGE_EXTS, VIDEO_EXTS, list_image_paths
from cutora.core.log import get_logger
from cutora.ui.theme.tokens import SPACE_2, SPACE_6

_LOG = get_logger("cutora.batch.batch_preview")
_THUMB = SPACE_6 * 3  # 96
_CELL_W = SPACE_6 * 4  # 128
_CELL_H = _THUMB + SPACE_6  # 96 thumb + one text line


def read_video_frame(path: str, t: float = 0.0) -> QImage:
    """One decoded frame. Empty image if the file cannot be read."""
    try:
        import cv2
        # Q156：明确软解 —— 缩略图不值得占一个显卡硬解会话（会话多了显卡驱动会被打挂）
        cap = cv2.VideoCapture(path, cv2.CAP_FFMPEG,
                               [cv2.CAP_PROP_HW_ACCELERATION, cv2.VIDEO_ACCELERATION_NONE])
        if not cap.isOpened():
            cap = cv2.VideoCapture(path)
        if not cap.isOpened():
            _LOG.warning("无法打开视频: %s", path)
            return QImage()
        if t and float(t) > 0:
            cap.set(cv2.CAP_PROP_POS_MSEC, float(t) * 1000.0)
        ok, frame = cap.read()
        cap.release()
        if not ok or frame is None:
            _LOG.warning("读不到视频帧: %s t=%.3f", path, float(t or 0))
            return QImage()
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        h, w = rgb.shape[:2]
        img = QImage(rgb.data, w, h, rgb.strides[0], QImage.Format.Format_RGB888)
        return img.copy()
    except Exception:
        _LOG.exception("读取视频帧失败: %s t=%.3f", path, float(t or 0))
        return QImage()


def _icon_from_image(img: QImage, tw: int, th: int) -> QIcon:
    pm = QPixmap(int(tw), int(th))
    pm.fill(Qt.GlobalColor.transparent)
    if img is None or img.isNull():
        return QIcon(pm)
    scaled = QPixmap.fromImage(img).scaled(
        int(tw), int(th),
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    painter = QPainter(pm)
    try:
        painter.drawPixmap(
            (int(tw) - scaled.width()) // 2,
            (int(th) - scaled.height()) // 2,
            scaled,
        )
    finally:
        painter.end()
    return QIcon(pm)


def _still_icon(path: str, tw: int, th: int) -> QIcon | None:
    ext = os.path.splitext(path)[1].lower()
    if ext in IMAGE_EXTS and ext != ".gif":
        reader = QImageReader(path)
        reader.setAutoTransform(True)
        sz = reader.size()
        if sz.isValid() and sz.width() > 0 and sz.height() > 0:
            sz.scale(int(tw), int(th), Qt.AspectRatioMode.KeepAspectRatio)
            if sz.width() > 0 and sz.height() > 0:
                reader.setScaledSize(sz)
        img = reader.read()
        if img is not None and not img.isNull():
            return _icon_from_image(img, tw, th)
    frame = read_video_frame(path, 0.0)
    if frame is not None and not frame.isNull():
        return _icon_from_image(frame, tw, th)
    return None


class ImageThumbGrid(QListWidget):
    """Folder grid. Each cell is a fixed thumb with one elided filename."""

    image_chosen = Signal(str)

    def __init__(self, parent=None, exts=None):
        super().__init__(parent)
        self.setObjectName("batchImageGrid")
        self._exts = tuple(exts) if exts else IMAGE_EXTS
        self.setViewMode(QListWidget.ViewMode.IconMode)
        self.setFlow(QListWidget.Flow.LeftToRight)
        self.setWrapping(True)
        self.setIconSize(QSize(_THUMB, _THUMB))
        self.setGridSize(QSize(_CELL_W, _CELL_H))
        self.setUniformItemSizes(True)
        self.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.setMovement(QListWidget.Movement.Static)
        self.setWordWrap(False)
        self.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.setSpacing(SPACE_2)
        self.setMinimumHeight(_CELL_H + SPACE_2)
        self.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.itemClicked.connect(self._on_click)
        self._folder = ""

    def set_folder(self, folder: str):
        from cutora.ui.timeline.thumbs import peek_clip_thumb, request_clip_thumb
        self._folder = (folder or "").strip()
        self.clear()
        paths = list_image_paths(self._folder, exts=self._exts)
        if not paths:
            if self._folder:
                _LOG.info("目录里没有匹配的素材: %s", self._folder)
            return
        tw, th = _THUMB, _THUMB
        for path in paths:
            name = os.path.basename(path)
            item = QListWidgetItem(name)
            item.setData(Qt.ItemDataRole.UserRole, path)
            item.setToolTip(path)
            item.setSizeHint(QSize(_CELL_W, _CELL_H))
            icon = _still_icon(path, tw, th)
            if icon is not None and not icon.isNull():
                item.setIcon(icon)
            else:
                hit = peek_clip_thumb(path, 0.0, tw, th)
                if hit:
                    item.setIcon(QIcon(hit))
                else:
                    def _done(_k="", it=item, p=path, ww=tw, hh=th):
                        dest = peek_clip_thumb(p, 0.0, ww, hh)
                        if dest:
                            it.setIcon(QIcon(dest))
                        self.viewport().update()
                    request_clip_thumb(path, 0.0, tw, th, on_done=_done)
            self.addItem(item)

    def _on_click(self, item):
        path = item.data(Qt.ItemDataRole.UserRole) if item is not None else ""
        if path and os.path.isfile(path):
            self.image_chosen.emit(str(path))
        elif path:
            _LOG.warning("缩略图文件已不存在: %s", path)


def prime_video_still(path: str, t: float = 0.0) -> None:
    """Put one decoded frame in the sticker image cache so a video path paints."""
    if not path or not os.path.isfile(path):
        return
    ext = os.path.splitext(path)[1].lower()
    if ext not in VIDEO_EXTS:
        return
    from cutora.render import frame_renderer as fr
    cache = fr._IMAGE_CACHE
    existing = cache.get(path)
    if existing is not None and not existing.isNull():
        return
    img = read_video_frame(path, t)
    if img is not None and not img.isNull():
        cache[path] = img


def fitted_background(path: str, t: float, cw: int, ch: int, fit: str = "fill") -> QImage | None:
    """Canvas-sized still of `path` at time t, fitted the way a clip would be."""
    if not path or not os.path.isfile(path) or cw <= 0 or ch <= 0:
        return None
    src = read_video_frame(path, t)
    if src is None or src.isNull():
        return None
    from cutora.batch.batch_load import compute_fit_rect
    out = QImage(int(cw), int(ch), QImage.Format.Format_RGB32)
    out.fill(QColor(0, 0, 0))
    x, y, w, h = compute_fit_rect(
        src.width(), src.height(), cw, ch, fit or "fill", 1.0, 0.0, 0.0)
    painter = QPainter(out)
    try:
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.drawImage(QRectF(float(x), float(y), float(w), float(h)), src)
    finally:
        painter.end()
    return out


# ── Q134：批量加载右栏的背景要是「画布上那一帧」 ───────────────────────────
# 以前只拿 task["video"] 这一个文件去读帧。拼接出来的工程 video 是第一段切片
# （十几秒），时间单位选「分钟」、起点 1.0 → 去 15 秒的文件里找第 60 秒，
# 读不到帧 → 右栏全黑，只剩插图。现在按时间线合成视频层（分屏 / 多段 /
# 模糊背景都和画布一致），时间线上没有视频片段时才退回老办法。
_BG_ENGINE = None


def _bg_engine():
    global _BG_ENGINE
    if _BG_ENGINE is None:
        from cutora.preview.engine import PreviewEngine
        _BG_ENGINE = PreviewEngine()
    return _BG_ENGINE


def _timeline_has_video(task) -> bool:
    tl = task.get("timeline") if isinstance(task, dict) else None
    for tr in getattr(tl, "tracks", None) or []:
        if getattr(tr, "type", "") == "video" and (getattr(tr, "clips", None) or []):
            return True
    return False


def _timeline_duration(task) -> float:
    tl = task.get("timeline") if isinstance(task, dict) else None
    end = 0.0
    for tr in getattr(tl, "tracks", None) or []:
        if getattr(tr, "type", "") != "video":
            continue
        for clip in getattr(tr, "clips", None) or []:
            try:
                end = max(end, float(clip.start_t) + float(getattr(clip, "effective_duration", None) or clip.duration))
            except Exception:
                continue
    return end


def task_background(task, t: float, cw: int, ch: int, fit: str = "fill") -> QImage | None:
    """批量加载预览的背景：这个工程在时间 t 的视频层，铺到 cw×ch 的黑底上。"""
    if cw <= 0 or ch <= 0:
        return None
    if isinstance(task, dict) and _timeline_has_video(task):
        try:
            dur = _timeline_duration(task)
            tt = float(t or 0.0)
            if dur > 0 and tt >= dur:
                tt = max(0.0, dur - 0.05)
            layer = _bg_engine().video_frame_at(task, tt)
            if layer is not None and not layer.isNull():
                out = QImage(int(cw), int(ch), QImage.Format.Format_RGB32)
                out.fill(QColor(0, 0, 0))
                painter = QPainter(out)
                try:
                    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
                    painter.drawImage(QRectF(0.0, 0.0, float(cw), float(ch)), layer)
                finally:
                    painter.end()
                return out
        except Exception:
            _LOG.exception("按时间线合成预览背景失败，退回单文件取帧")
    path = ""
    if isinstance(task, dict):
        path = str(task.get("video") or task.get("path") or "")
    return fitted_background(path, t, cw, ch, fit)
