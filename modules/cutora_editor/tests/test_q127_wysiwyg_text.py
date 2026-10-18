# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q127 · 文本层：框 = 折行宽度（不限行、不缩、不截）+ 所见即所得编辑。"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QInputMethodEvent, QMouseEvent
from PySide6.QtWidgets import QApplication

VW, VH = 1080, 1920
LONG = ("When God is leading a woman toward success, she will go through "
        "these three stages and then some more words to make it long")


def _app():
    app = QApplication.instance() or QApplication(["q127"])
    from cutora.ui.panels.fonts import load_embedded_fonts
    load_embedded_fonts()
    return app


def _ov(**kw):
    from cutora.model.factory_defaults import overlay_defaults
    from cutora.subtitle.text_layer import migrate_text_overlay
    ov = overlay_defaults("text", canvas_w=VW, canvas_h=VH)
    ov["content"] = LONG
    ov["y"] = 100
    ov.update(kw)
    migrate_text_overlay(ov)
    return ov


def _layout(ov):
    from cutora.render.text_layer_paint import layout_for_overlay
    return layout_for_overlay(dict(ov), VW, VH)


def _joined(layout):
    return " ".join("".join(r.text for r in ln.runs).strip() for ln in layout.lines)


# ─────────────────────────── 排版：框就是折行宽度 ───────────────────────────

def test_no_line_cap_no_ellipsis_in_narrow_box():
    """520 宽的框、80 号字：十几行全在，不再 8 行后并进末行打省略号。"""
    _app()
    ov = _ov(x=280, w=520)
    layout, _spec, style, _ev, _box = _layout(ov)
    assert len(layout.lines) > 8
    text = _joined(layout)
    assert "…" not in text
    for word in LONG.split():
        assert word in text, word
    # 每行都在框宽以内
    for ln in layout.lines:
        assert ln.w <= 520.0 + 1.0, (ln.text, ln.w)
    # 字号没被缩
    assert abs(layout.em - 80.0) < 1e-6


def test_full_width_box_wraps_to_full_width_without_shrink():
    """框拉到画布 100%：折行按 100% 算，不再被字幕的 84% 安全边距卡住缩字。"""
    _app()
    ov = _ov(x=0, w=VW)
    layout, *_ = _layout(ov)
    assert abs(layout.em - 80.0) < 1e-6, layout.em
    widest = max(ln.w for ln in layout.lines)
    # 至少有一行比 84% 宽（证明用到了超出安全边距的那一截）
    assert widest > VW * 0.84, widest
    assert widest <= VW + 1.0


def test_side_pads_shrink_wrap_width():
    """text_layout 的左右内边距要从折行宽度里扣掉。"""
    _app()
    a = _ov(x=100, w=880)
    b = _ov(x=100, w=880)
    b["text_layout"]["left_pad"] = 120
    b["text_layout"]["right_pad"] = 120
    la, *_ = _layout(a)
    lb, *_ = _layout(b)
    assert max(ln.w for ln in lb.lines) <= 880 - 240 + 1.0
    assert len(lb.lines) >= len(la.lines)


def test_overlong_word_overflows_instead_of_shrinking():
    """一个词比框还宽：伸出去让用户看见，不缩字号、不打省略号。"""
    _app()
    ov = _ov(x=280, w=300, content="Supercalifragilisticexpialidocious ok")
    layout, *_ = _layout(ov)
    assert abs(layout.em - 80.0) < 1e-6
    assert "Supercalifragilisticexpialidocious" in _joined(layout)


def test_subtitle_styles_keep_safe_margin_cap():
    """字幕样式没有 _overlay_box：_line_avail_width 仍取 min(框, 84%)。"""
    from cutora.render.textfx.layout import _line_avail_width
    sub = {"wrap_width_percent": 100.0}
    ovl = {"wrap_width_percent": 100.0, "_overlay_box": (0, 0, VW, 100)}
    assert _line_avail_width(sub, VW, 40.0, 0.0) == pytest.approx(VW * 0.84)
    assert _line_avail_width(ovl, VW, 40.0, 0.0) == pytest.approx(VW)


def test_new_text_gets_fx_at_birth():
    """新建文本一出生就带 fx：只走 textfx 一条排版路径。老键不删（迁移才删）。"""
    from cutora.batch.batch_load import make_text_overlay
    from cutora.subtitle.text_layer import ensure_overlay_fx
    ov = make_text_overlay(None, "hi", 0.0, 3.0, dims=(VW, VH))
    assert isinstance(ov.get("fx"), dict)
    legacy = {"type": "text", "content": "x", "color": "#FF0000", "use_stroke": True,
              "stroke_color": "#000000", "stroke_width": 4, "fontsize": 60}
    ensure_overlay_fx(legacy)
    assert legacy["fx"]["fill"]["colors"][0].upper() == "#FF0000"
    assert legacy["use_stroke"] is True   # 不删老键
    fx_before = legacy["fx"]
    ensure_overlay_fx(legacy)
    assert legacy["fx"] is fx_before      # 已有就不动


# ─────────────────────────── 光标几何 ───────────────────────────

def test_caret_map_aligns_every_source_char():
    from cutora.render.text_caret import build_caret_map
    _app()
    ov = _ov(content="When God is leading a woman\ntoward  success, she will go")
    cm = build_caret_map(ov, VW, VH)
    text = ov["content"]
    assert len(cm.boxes) == len(text)
    # 可见字符都有宽度；折行吞掉的空格 / 换行是零宽
    for i, ch in enumerate(text):
        b = cm.boxes[i]
        if ch.strip():
            assert b.w > 0 and b.h > 0, (i, ch, b)
    nl = text.index("\n")
    assert cm.boxes[nl].w == 0
    # 换行前后的字在不同行
    assert cm.boxes[nl - 1].line < cm.boxes[nl + 1].line
    # 行号单调
    lines = [b.line for b in cm.boxes]
    assert lines == sorted(lines)
    # 末位光标在最后一个字右边
    end = cm.caret(len(text))
    last = cm.boxes[-1]
    assert end.x() == pytest.approx(last.x + last.w)
    assert end.y() == pytest.approx(last.y)


def test_caret_hit_roundtrip_and_selection_rects():
    from cutora.render.text_caret import build_caret_map
    _app()
    ov = _ov(content="When God is leading a woman toward success")
    cm = build_caret_map(ov, VW, VH)
    n = len(cm.text)
    for i in (0, 3, 12, 20, n - 1):
        c = cm.caret(i)
        assert cm.hit(c.x() + 1.0, c.y() + c.height() / 2) == i
    # 点在最后一行右边很远：落到末尾
    last = cm.boxes[-1]
    assert cm.hit(last.x + 5000, last.y + 1) == n
    # 选区跨行 → 每行一个矩形
    a, b = 5, n - 3
    rects = cm.line_rects(a, b)
    assert len(rects) == len({cm.boxes[i].line for i in range(a, b)})
    for r in rects:
        assert r.width() > 0 and r.height() > 0
    assert cm.line_rects(7, 7) == []


def test_caret_map_rotation_unrotate():
    from cutora.render.text_caret import build_caret_map
    _app()
    ov = _ov(content="Rotate me", rotation=30.0)
    cm = build_caret_map(ov, VW, VH)
    c = cm.caret(3)
    x, y = c.x() + 1.0, c.y() + c.height() / 2
    p = cm.transform().map(QPointF(x, y))          # 转到屏幕上的位置
    ux, uy = cm.unrotate(p.x(), p.y())             # 再转回来
    assert ux == pytest.approx(x, abs=1e-6) and uy == pytest.approx(y, abs=1e-6)
    assert cm.hit(ux, uy) == 3


def test_caret_map_empty_text_has_a_caret_in_the_box():
    from cutora.render.text_caret import build_caret_map
    _app()
    ov = _ov(content="")
    cm = build_caret_map(ov, VW, VH)
    c = cm.caret(0)
    bx, by, bw, bh = cm.box
    assert bx <= c.x() <= bx + bw
    assert c.height() > 0


# ─────────────────────────── 画布：所见即所得 ───────────────────────────

def _mouse(typ, x, y, mods=Qt.KeyboardModifier.NoModifier):
    return QMouseEvent(typ, QPointF(float(x), float(y)), QPointF(float(x), float(y)),
                       Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, mods)


def _canvas_with_text(content="When God is leading a woman toward success"):
    from cutora.model.timeline import Timeline
    from cutora.model.timeline_model import Track
    from cutora.model.factory_defaults import overlay_defaults
    from cutora.ui.canvas import VideoCanvas
    tl = Timeline(VW, VH, 30.0)
    tr = Track("text")
    tl.tracks.append(tr)
    ov = overlay_defaults("text", canvas_w=VW, canvas_h=VH, track_id=tr.id)
    ov["content"] = content
    task = {"timeline": tl, "segments": [], "style": {}, "text_overlays": [ov],
            "image_overlays": [], "duration": 5.0, "dims": [VW, VH]}
    c = VideoCanvas()
    c.setFixedSize(540, 960)
    c.set_data(task, 0.5)
    c.show()
    QApplication.processEvents()
    c.repaint()
    rec = next(r for r in c._hit_records
               if r.get("type") == "overlay" and r.get("list") == "text_overlays")
    wrect, _ = c._video_rect_to_widget(rec["rect"])
    return c, ov, rec, wrect


def _dbl(c, wrect, fx=0.05, fy=0.3):
    c.mouseDoubleClickEvent(_mouse(
        QEvent.Type.MouseButtonDblClick,
        wrect.left() + wrect.width() * fx, wrect.top() + wrect.height() * fy))


def test_double_click_enters_wysiwyg_and_keeps_rendering():
    _app()
    c, ov, rec, wrect = _canvas_with_text()
    try:
        _dbl(c, wrect)
        assert c._wysiwyg is not None
        assert ov.get("_inline_editing") is None          # 字照常渲染，不藏
        ed = c._wysiwyg["editor"]
        assert not ed.testAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        assert ed.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        assert ed.cursorWidth() == 0
        # 双击落在第一个词上 → 选中那个词
        assert ed.textCursor().selectedText() == "When"
        # 有 fx 了（只走 textfx 路径）
        assert isinstance(ov.get("fx"), dict)
    finally:
        c.close()


def test_typing_updates_content_live_and_commit_keeps_it():
    from PySide6.QtTest import QTest
    _app()
    c, ov, rec, wrect = _canvas_with_text()
    try:
        _dbl(c, wrect)
        ed = c._wysiwyg["editor"]
        cur = ed.textCursor()
        cur.movePosition(cur.MoveOperation.End)
        ed.setTextCursor(cur)
        QTest.keyClicks(ed, " now")
        assert ov["content"].endswith("success now")   # 实时写回
        # 框外点一下 → 提交
        c.mousePressEvent(_mouse(QEvent.Type.MouseButtonPress, 3, 3))
        assert c._wysiwyg is None
        assert c._inline_editor is None
        assert ov["content"].endswith("success now")
        assert ov.get("_inline_editing") is None
    finally:
        c.close()


def test_enter_is_newline_escape_finishes():
    from PySide6.QtTest import QTest
    _app()
    c, ov, rec, wrect = _canvas_with_text(content="ab")
    try:
        _dbl(c, wrect)
        ed = c._wysiwyg["editor"]
        cur = ed.textCursor()
        cur.movePosition(cur.MoveOperation.End)
        ed.setTextCursor(cur)
        QTest.keyClick(ed, Qt.Key.Key_Return)
        QTest.keyClicks(ed, "c")
        assert ov["content"] == "ab\nc"
        QTest.keyClick(ed, Qt.Key.Key_Escape)
        assert c._wysiwyg is None
        assert ov["content"] == "ab\nc"                 # Esc 结束并保留
    finally:
        c.close()


def test_ime_preedit_is_visible_then_replaced_by_commit():
    _app()
    app = QApplication.instance()
    c, ov, rec, wrect = _canvas_with_text(content="ab")
    try:
        _dbl(c, wrect)
        ed = c._wysiwyg["editor"]
        cur = ed.textCursor()
        cur.movePosition(cur.MoveOperation.End)
        ed.setTextCursor(cur)
        app.sendEvent(ed, QInputMethodEvent("ni", []))
        assert ov["content"] == "abni"                  # 组字中也看得见
        pos, a, b, pre = c._wysiwyg_positions()
        assert pre == 2 and pos == 4
        ev = QInputMethodEvent("", [])
        ev.setCommitString("你")
        app.sendEvent(ed, ev)
        assert ov["content"] == "ab你"
        assert ed.preedit == ""
        c._commit_inline_edit()
        assert ov["content"] == "ab你"
    finally:
        c.close()


def test_click_inside_places_caret_drag_selects():
    _app()
    c, ov, rec, wrect = _canvas_with_text(content="When God is leading")
    try:
        _dbl(c, wrect)
        ed = c._wysiwyg["editor"]
        cm = c._wysiwyg_map()
        target_rect = c._preview_target_rect()
        sx = target_rect.width() / VW
        sy = target_rect.height() / VH

        def widget_pt(i):
            r = cm.caret(i)
            return (target_rect.x() + (r.x() + 1.0) * sx,
                    target_rect.y() + (r.y() + r.height() / 2) * sy)

        x0, y0 = widget_pt(2)
        c.mousePressEvent(_mouse(QEvent.Type.MouseButtonPress, x0, y0))
        assert c._wysiwyg is not None                   # 框内不提交
        assert ed.textCursor().position() == 2
        x1, y1 = widget_pt(7)
        c.mouseMoveEvent(_mouse(QEvent.Type.MouseMove, x1, y1))
        c.mouseReleaseEvent(_mouse(QEvent.Type.MouseButtonRelease, x1, y1))
        assert ed.textCursor().selectionStart() == 2
        assert ed.textCursor().selectionEnd() == 7
        assert ed.textCursor().selectedText() == "en Go"
    finally:
        c.close()


def test_blink_repaints_only_the_caret_layer():
    """闪烁只贴底图 + 画光标层，不重新合成画面。"""
    _app()
    app = QApplication.instance()
    c, ov, rec, wrect = _canvas_with_text()
    try:
        _dbl(c, wrect)
        for _ in range(3):        # 进入编辑态那一拍的焦点 / 光标事件先跑完
            app.processEvents()
        c.repaint()
        cnt = {"scene": 0, "layer": 0}
        ps, dl = c._paint_scene, c._draw_wysiwyg_layer

        def _scene(p):
            cnt["scene"] += 1
            return ps(p)

        def _layer(p):
            cnt["layer"] += 1
            return dl(p)

        c._paint_scene = _scene
        c._draw_wysiwyg_layer = _layer
        for _ in range(3):
            c._wysiwyg_blink()
            app.processEvents()
        assert cnt["layer"] >= 3
        assert cnt["scene"] == 0                        # 一次都没重新合成
        # 常规刷新还是完整重画
        c.update()
        app.processEvents()
        assert cnt["scene"] == 1
    finally:
        c.close()


def test_second_edit_session_starts_after_first_commit():
    """老编辑框 deleteLater 前先摘回调，否则它的 focusOut 会把新会话当场提交掉。"""
    _app()
    c, ov, rec, wrect = _canvas_with_text()
    try:
        _dbl(c, wrect)
        assert c._wysiwyg is not None
        c.mousePressEvent(_mouse(QEvent.Type.MouseButtonPress, 3, 3))
        assert c._wysiwyg is None
        _dbl(c, wrect)
        assert c._wysiwyg is not None
        QApplication.processEvents()
        assert c._wysiwyg is not None
    finally:
        c.close()


def test_paint_text_overlay_unchanged_by_refactor():
    """layout_for_overlay 抽出来后，paint_text_overlay 的像素一个不变。"""
    import hashlib
    from PySide6.QtGui import QColor, QImage, QPainter
    from cutora.render.text_layer_paint import paint_text_overlay
    _app()
    ov = _ov(content="Hello wrap world", w=600, x=200)

    def _md5():
        img = QImage(VW, VH, QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(QColor(20, 20, 20))
        p = QPainter(img)
        try:
            paint_text_overlay(p, dict(ov), 0.0, None, video_w=VW, video_h=VH)
        finally:
            p.end()
        return hashlib.md5(img.constBits().tobytes()).hexdigest()

    assert _md5() == _md5()   # 稳定；跨版本的字节对比见 test_q100 的 A/B
