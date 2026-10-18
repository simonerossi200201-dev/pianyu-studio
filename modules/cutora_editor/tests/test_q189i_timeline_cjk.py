# SPDX-License-Identifier: GPL-3.0-or-later
"""Q189i：时间线上的中文文件名 / 字幕 / 标记不再画成小方块（西文仍是 Arimo）。"""
from __future__ import annotations

import os
import sys
import threading
import time

if "PySide6" not in sys.modules:
    from cutora.core.runtime import init_torch_runtime
    init_torch_runtime(render_child=False)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(["cutora-q189h"])


def _glyph(font, ch):
    from PySide6.QtGui import QColor, QImage, QPainter
    img = QImage(40, 40, QImage.Format.Format_ARGB32)
    img.fill(QColor(0, 0, 0))
    p = QPainter(img)
    p.setFont(font)
    p.setPen(QColor(255, 255, 255))
    p.drawText(4, 30, ch)
    p.end()
    return bytes(img.constBits())


def test_timeline_label_font_draws_cjk_not_tofu():
    """中 / 文 / 片 画出来必须各不相同（缺字时三个都是同一个方块）。"""
    _qapp()
    from cutora.ui.theme.fonts_ui import register_ui_fonts
    register_ui_fonts()
    from cutora.ui.timeline.widget import timeline_label_font
    from cutora.ui.panels.fonts import DEFAULT_FONT_FAMILY, get_font_manager
    f = timeline_label_font()
    a, b, c = _glyph(f, "中"), _glyph(f, "文"), _glyph(f, "片")
    assert len({a, b, c}) == 3, "时间线字体画中文还是方块"
    # 西文仍是 Arimo（观感不变）
    base = get_font_manager().get_font(DEFAULT_FONT_FAMILY, 8)
    assert f.families()[0] == base.family()
    assert _glyph(f, "A") == _glyph(base, "A")
    # 对照：旧做法（Arimo + 不合并字体）画中文就是同一个方块 —— 证明本测试抓得住
    assert _glyph(base, "中") == _glyph(base, "文")


def test_timeline_paint_uses_label_font():
    import inspect
    from cutora.ui.timeline import widget as W
    src = inspect.getsource(W.TimelineWidget.paintEvent)
    assert "timeline_label_font()" in src
    assert "get_font(DEFAULT_FONT_FAMILY, 8)" not in src


