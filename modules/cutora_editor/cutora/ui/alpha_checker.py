# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q165 · 预览里透明的地方画棋盘格。

抠像 / AI 抠像 / 遮罩算出来的透明是真的（alpha），但画布以前在画面底下先铺一层黑，抠掉的地方
看着还是黑的 ——「抠了但不透明」。现在：画面里有这类效果时底下铺棋盘格（只影响预览）。
导出照旧：透明处露出下面的轨道，下面什么都没有就是黑（mp4 没有透明通道）。"""
from __future__ import annotations

from PySide6.QtCore import QRectF
from PySide6.QtGui import QBrush, QColor, QPixmap, QPainter

_TILE = None


def _tile(px: int = 8) -> QPixmap:
    global _TILE
    if _TILE is None:
        pm = QPixmap(px * 2, px * 2)
        pm.fill(QColor(58, 58, 62))
        p = QPainter(pm)
        try:
            c = QColor(84, 84, 90)
            p.fillRect(0, 0, px, px, c)
            p.fillRect(px, px, px, px, c)
        finally:
            p.end()
        _TILE = pm
    return _TILE


def paint_alpha_checker(painter, rect: QRectF) -> None:
    """在 rect 里铺棋盘格（屏幕像素大小固定，不随画面缩放）。"""
    painter.save()
    try:
        brush = QBrush(_tile())
        painter.setBrushOrigin(rect.topLeft())
        painter.fillRect(rect, brush)
    finally:
        painter.restore()


_FX_ATTRS = ("keyer", "masks", "ai_matte", "fx_graph")


def _scan(task) -> bool:
    from cutora.fx.layer_fx import layer_has_fx
    tl = task.get("timeline") if isinstance(task, dict) else None
    for tr in list(getattr(tl, "tracks", None) or []):
        if getattr(tr, "domain", "visual") == "audio" or not getattr(tr, "visible", True):
            continue
        if getattr(tr, "type", None) not in ("video", "image"):
            continue
        if any(getattr(tr, a, None) for a in _FX_ATTRS):
            try:
                if layer_has_fx(tr):
                    return True
            except Exception:
                pass
        for c in list(getattr(tr, "clips", None) or []):
            if not any(getattr(c, a, None) for a in _FX_ATTRS):
                continue                 # 快速路径：一个效果键都没有
            try:
                if layer_has_fx(c):
                    return True
            except Exception:
                continue
    for key in ("image_overlays", "overlays"):
        for ov in task.get(key) or [] if isinstance(task, dict) else []:
            if isinstance(ov, dict) and ov.get("type") != "adjust" and (
                    ov.get("keyer") or ov.get("masks") or ov.get("ai_matte") or ov.get("fx_graph")):
                try:
                    if layer_has_fx(ov):
                        return True
                except Exception:
                    continue
    return False


def task_has_alpha_fx(task) -> bool:
    """这条视频里有没有会挖透明的效果（抠像 / AI 抠像 / 遮罩 / 节点图）。每次画都现查（快速路径只看有没有效果键）。"""
    if not isinstance(task, dict):
        return False
    try:
        return _scan(task)
    except Exception:
        return False
