# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q127：所见即所得编辑用的光标几何。

画布上双击文本进入编辑后，文字仍然由 textfx 正常渲染（描边 / 阴影 / 渐变 /
自动换行一个不少），只是叠一根光标和选区。光标落在哪，得问真正的排版
结果 —— 不是问 QTextEdit 自己排的那份（它不认识我们的换行规则、字距、
点字号，差半行是常事）。

这里把 ``layout_for_overlay`` 排出来的 run 逐字量宽，对齐回源文本的每个
字符下标，给出：

* ``caret(i)``    第 i 个字符前面的光标线（视频坐标），i ∈ [0, len(text)]
* ``hit(x, y)``   视频坐标上的一个点应该落到哪个下标
* ``line_rects(a, b)`` 选区 [a, b) 覆盖的矩形（按行合并）

对不上的字符（换行时吞掉的空格 / ``\\n``）拿前一个字的右边缘做零宽框，
这样光标在自动折行处停在上一行末尾，和所有编辑器一样。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from PySide6.QtCore import QPointF, QRectF
from PySide6.QtGui import QFont, QFontMetricsF, QTransform


@dataclass
class CharBox:
    x: float
    y: float
    w: float
    h: float
    line: int

    @property
    def right(self) -> float:
        return self.x + self.w


@dataclass
class LineInfo:
    x: float
    y: float
    w: float
    h: float
    first: int   # 本行第一个源字符下标
    last: int    # 本行最后一个源字符下标（含）；空行时 == first - 1


@dataclass
class CaretMap:
    text: str
    boxes: list[CharBox] = field(default_factory=list)   # len == len(text)
    lines: list[LineInfo] = field(default_factory=list)
    vertical: bool = False
    box: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    rotation: float = 0.0
    scale: float = 1.0
    em: float = 40.0

    # ── 几何 ────────────────────────────────────────────────────────────
    def caret(self, i: int) -> QRectF:
        """第 i 个字符前面的光标（视频坐标，未旋转）。"""
        n = len(self.text)
        i = max(0, min(int(i), n))
        if not self.boxes:
            return self._empty_caret()
        if i < n:
            b = self.boxes[i]
            if self.vertical:
                return QRectF(b.x, b.y, b.w, 0.0)
            return QRectF(b.x, b.y, 0.0, b.h)
        b = self.boxes[-1]
        if self.vertical:
            return QRectF(b.x, b.y + b.h, b.w, 0.0)
        return QRectF(b.x + b.w, b.y, 0.0, b.h)

    def _empty_caret(self) -> QRectF:
        x, y, w, h = self.box
        if self.lines:
            ln = self.lines[0]
            if self.vertical:
                return QRectF(ln.x, ln.y, ln.w, 0.0)
            return QRectF(ln.x, ln.y, 0.0, ln.h)
        em = float(self.em or 40.0)
        if self.vertical:
            return QRectF(x + w * 0.5 - em * 0.5, y, em, 0.0)
        return QRectF(x + w * 0.5, y, 0.0, em * 1.2)

    def line_of(self, i: int) -> int:
        n = len(self.text)
        if not self.boxes:
            return 0
        if i >= n:
            return self.boxes[-1].line
        return self.boxes[max(0, i)].line

    def hit(self, x: float, y: float) -> int:
        """视频坐标（未旋转）→ 光标下标。点在行外取最近一行，行内按字符中线。"""
        n = len(self.text)
        if not self.boxes or not self.lines:
            return n
        main, cross = (x, y) if self.vertical else (y, x)
        best = None
        best_d = None
        for li, ln in enumerate(self.lines):
            lo = ln.x if self.vertical else ln.y
            size = ln.w if self.vertical else ln.h
            if lo <= main < lo + size:
                best = li
                break
            d = min(abs(main - lo), abs(main - (lo + size)))
            if best_d is None or d < best_d:
                best_d = d
                best = li
        ln = self.lines[best]
        if ln.last < ln.first:
            return ln.first
        for i in range(ln.first, ln.last + 1):
            b = self.boxes[i]
            if b.w <= 0 and b.h <= 0:
                continue
            mid = (b.y + b.h * 0.5) if self.vertical else (b.x + b.w * 0.5)
            start = b.y if self.vertical else b.x
            if cross < start:
                return i
            if cross < mid:
                return i
        return ln.last + 1

    def line_rects(self, a: int, b: int) -> list[QRectF]:
        """选区 [a, b) 的矩形，按行合并（视频坐标，未旋转）。"""
        a, b = sorted((int(a), int(b)))
        n = len(self.text)
        a = max(0, min(a, n))
        b = max(0, min(b, n))
        out: list[QRectF] = []
        if b <= a or not self.boxes:
            return out
        cur: QRectF | None = None
        cur_line = -1
        for i in range(a, b):
            bx = self.boxes[i]
            r = QRectF(bx.x, bx.y, bx.w, bx.h)
            if bx.line != cur_line:
                if cur is not None and cur.width() > 0 and cur.height() > 0:
                    out.append(cur)
                cur = r
                cur_line = bx.line
            else:
                cur = cur.united(r) if cur is not None else r
        if cur is not None and cur.width() > 0 and cur.height() > 0:
            out.append(cur)
        return out

    def transform(self) -> QTransform:
        """框旋转 + 整体缩放（和 paint_text_overlay 一样绕框中心，顺序也一样）。"""
        xf = QTransform()
        if self.rotation or abs(self.scale - 1.0) > 1e-6:
            x, y, w, h = self.box
            cx, cy = x + w * 0.5, y + h * 0.5
            xf.translate(cx, cy)
            if self.rotation:
                xf.rotate(self.rotation)
            if abs(self.scale - 1.0) > 1e-6:
                xf.scale(self.scale, self.scale)
            xf.translate(-cx, -cy)
        return xf

    def unrotate(self, x: float, y: float) -> tuple[float, float]:
        inv, ok = self.transform().inverted()
        if not ok:
            return x, y
        p = inv.map(QPointF(x, y))
        return p.x(), p.y()


# ── 构建 ────────────────────────────────────────────────────────────────

def _run_font(run) -> QFont | None:
    """raster._run_path 用的那个字体（含字距），量宽必须用同一个。"""
    if run.qfont is None:
        return None
    font = QFont(run.qfont)
    track = float(run.tracking_em or 0.0)
    px = float(run.fontsize_px or 0.0)
    if track:
        font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, track * px)
    else:
        font.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 100.0)
    return font


def _fold(ch: str, upper: bool) -> str:
    return ch.upper() if upper else ch


def _run_char_boxes(run, line_idx: int, vertical: bool) -> list[tuple[str, CharBox]]:
    """一个 run 里每个字符的框（视频坐标）。"""
    text = str(run.text or "")
    out: list[tuple[str, CharBox]] = []
    if not text:
        return out
    if vertical:
        # 竖排一个 run 就是一个字格
        out.append((text, CharBox(run.x, run.y, run.w, run.h, line_idx)))
        return out
    font = _run_font(run)
    x0 = float(run.x) + float(run.pad_px or 0.0) + float(getattr(run, "glyph_ox", 0.0) or 0.0)
    if font is None:
        # 没字体只能均分
        step = float(run.w) / max(1, len(text))
        for i, ch in enumerate(text):
            out.append((ch, CharBox(x0 + i * step, run.y, step, run.h, line_idx)))
        return out
    fm = QFontMetricsF(font)
    prev = 0.0
    for i, ch in enumerate(text):
        adv = float(fm.horizontalAdvance(text[: i + 1]))
        out.append((ch, CharBox(x0 + prev, float(run.y), max(0.0, adv - prev), float(run.h), line_idx)))
        prev = adv
    return out


def build_caret_map(ov: dict, video_w: int, video_h: int) -> CaretMap:
    """按当前样式把 ov 排一遍（不画），给出每个源字符的框。"""
    from cutora.render.text_layer_paint import layout_for_overlay, overlay_scale
    text = str(ov.get("content") or "")
    vertical = str(ov.get("writing_mode") or "horizontal") != "horizontal"
    work = dict(ov)
    layout, spec, style, _event, box = layout_for_overlay(work, int(video_w), int(video_h))
    upper = bool(getattr(spec, "letter_case", "") == "upper")
    cmap = CaretMap(
        text=text, vertical=vertical, box=tuple(float(v) for v in box),
        rotation=float(ov.get("rotation") or 0.0), scale=overlay_scale(ov),
        em=float(layout.em or 40.0),
    )
    n = len(text)
    boxes: list[CharBox | None] = [None] * n
    lines: list[LineInfo] = []
    si = 0
    last_box: CharBox | None = None
    for li, ln in enumerate(layout.lines):
        info = LineInfo(float(ln.x), float(ln.y), float(ln.w), float(ln.h), first=si, last=si - 1)
        line_started = False
        for run in ln.runs:
            for piece, cb in _run_char_boxes(run, li, vertical):
                if vertical and len(piece) > 1:
                    # 一个字格里放了多个字（极少见）：均分
                    step = cb.h / max(1, len(piece))
                    subs = [(c, CharBox(cb.x, cb.y + k * step, cb.w, step, li)) for k, c in enumerate(piece)]
                else:
                    subs = [(piece, cb)] if len(piece) == 1 else [(c, cb) for c in piece]
                for ch, box_ in subs:
                    # 源文本里被排版吞掉的空白（折行点的空格 / 换行）→ 零宽
                    while si < n and text[si] in " \n\r\t" and _fold(text[si], upper) != _fold(ch, upper):
                        boxes[si] = _zero_after(last_box, box_, li, vertical)
                        si += 1
                    if si < n and _fold(text[si], upper) == _fold(ch, upper):
                        boxes[si] = box_
                        if not line_started:
                            info.first = si
                            line_started = True
                        info.last = si
                        last_box = box_
                        si += 1
                    else:
                        # 排版自己加的字符（不在源里）：不占下标
                        last_box = box_
        if not line_started:
            info.first = si
            info.last = si - 1
        lines.append(info)
    # 尾巴上没对上的（末尾换行 / 空格）
    while si < n:
        boxes[si] = _zero_after(last_box, None, len(lines) - 1 if lines else 0, vertical)
        si += 1
    cmap.boxes = [b if b is not None else CharBox(cmap.box[0], cmap.box[1], 0.0, 0.0, 0) for b in boxes]
    cmap.lines = lines
    _fix_zero_lines(cmap)
    return cmap


def _zero_after(prev: CharBox | None, nxt: CharBox | None, line: int, vertical: bool) -> CharBox:
    if prev is not None:
        if vertical:
            return CharBox(prev.x, prev.y + prev.h, prev.w, 0.0, prev.line)
        return CharBox(prev.x + prev.w, prev.y, 0.0, prev.h, prev.line)
    if nxt is not None:
        if vertical:
            return CharBox(nxt.x, nxt.y, nxt.w, 0.0, line)
        return CharBox(nxt.x, nxt.y, 0.0, nxt.h, line)
    return CharBox(0.0, 0.0, 0.0, 0.0, line)


def _fix_zero_lines(cmap: CaretMap) -> None:
    """零宽框的 line 号跟着它前面那个字走，保证 line_of 单调。"""
    last_line = 0
    for b in cmap.boxes:
        if b.w <= 0 and b.h <= 0:
            b.line = last_line
        else:
            last_line = b.line
