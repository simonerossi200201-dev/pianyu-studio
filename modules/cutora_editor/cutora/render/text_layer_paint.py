# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Paint a text overlay through the shared textfx raster (bg + fill/stroke/shadow)."""
from __future__ import annotations

import math

from dataclasses import replace

from PySide6.QtCore import QPoint, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QTransform

from cutora.core.log import get_logger
from cutora.render.fx_bg import raster_bg
from cutora.render.textfx.layout import LayoutResult, WordRun, layout_event
from cutora.render.textfx.raster import raster_text
from cutora.render.textfx.spec import from_style
from cutora.subtitle.text_layer import (
    normalize_runs,
    overlay_event,
    overlay_text_layout,
    overlay_to_style,
)

logger = get_logger("cutora.render.text_layer_paint")


def apply_run_overrides(layout: LayoutResult, source: str, runs: list) -> LayoutResult:
    """Split line.runs on {start,end,overrides} and apply color/size/weight."""
    specs = []
    for item in normalize_runs(runs, source):
        specs.append((int(item["start"]), int(item["end"]), item["overrides"]))
    if not specs or layout is None:
        return layout
    cursor = 0
    nsrc = len(source or "")
    for line in layout.lines:
        while cursor < nsrc and source[cursor] == "\n":
            cursor += 1
        rebuilt: list[WordRun] = []
        x = line.runs[0].x if line.runs else line.x
        for wr in line.runs:
            piece_runs = _slice_wordrun(wr, cursor, cursor + len(wr.text), specs)
            for piece in piece_runs:
                piece.x = x
                rebuilt.append(piece)
                x += piece.w
            cursor += len(wr.text)
        line.runs = rebuilt
        if rebuilt:
            line.x = rebuilt[0].x
            line.w = (rebuilt[-1].x + rebuilt[-1].w) - line.x
            line.y = min(r.y for r in rebuilt)
            line.h = max((r.y + r.h) for r in rebuilt) - line.y
            line.baseline = rebuilt[0].baseline
    return layout


def _slice_wordrun(wr: WordRun, s0: int, s1: int, specs: list) -> list[WordRun]:
    text = wr.text or ""
    n = len(text)
    if n <= 0:
        return [wr]
    cuts = {0, n}
    covering = []
    for a, b, ov in specs:
        if b <= s0 or a >= s1:
            continue
        cuts.add(max(0, a - s0))
        cuts.add(min(n, b - s0))
        covering.append((a, b, ov))
    cuts = sorted(c for c in cuts if 0 <= c <= n)
    if len(cuts) <= 2 and not covering:
        return [wr]
    out = []
    for i in range(len(cuts) - 1):
        lo, hi = cuts[i], cuts[i + 1]
        if hi <= lo:
            continue
        piece = text[lo:hi]
        ov = {}
        for a, b, o in covering:
            if a <= s0 + lo and s0 + hi <= b:
                ov.update(o)
        out.append(_restyle_run(wr, piece, ov))
    return out or [wr]


def _restyle_run(wr: WordRun, piece: str, ov: dict) -> WordRun:
    fill = wr.fill_override
    size = wr.fontsize_px
    fam = wr.font_family
    font = QFont(wr.qfont) if wr.qfont is not None else None
    if ov.get("color"):
        fill = str(ov["color"])
    if ov.get("fontsize"):
        try:
            size = float(ov["fontsize"])
        except (TypeError, ValueError):
            pass
        if font is not None:
            font.setPixelSize(max(8, int(round(size))))
    if "bold" in ov and font is not None:
        font.setBold(bool(ov["bold"]))
    if "italic" in ov and font is not None:
        font.setItalic(bool(ov["italic"]))
    if ov.get("font_family"):
        fam = str(ov["font_family"])
        try:
            from cutora.ui.panels.fonts import get_font_manager
            font = get_font_manager().get_font(
                fam, max(8, int(round(size))),
                bold=bool(font.bold()) if font is not None else False,
                italic=bool(font.italic()) if font is not None else False,
            )
        except Exception:
            logger.exception("run font override failed")
    if font is not None:
        w = float(QFontMetricsF(font).horizontalAdvance(piece))
        h = float(QFontMetricsF(font).height())
        asc = float(QFontMetricsF(font).ascent())
    else:
        frac = len(piece) / max(1, len(wr.text))
        w = wr.w * frac
        h = wr.h
        asc = wr.baseline - wr.y
    under = bool(ov["underline"]) if "underline" in ov else bool(wr.underline)
    run = replace(
        wr, text=piece, w=w, h=h, fill_override=fill,
        fontsize_px=size, font_family=fam, qfont=font,
        y=wr.baseline - asc if font is not None else wr.y,
        underline=under,
    )
    return run


def _translate_layout(layout: LayoutResult, dx: float, dy: float) -> None:
    if dx == 0.0 and dy == 0.0:
        return
    for ln in layout.lines:
        ln.x += dx
        ln.y += dy
        ln.baseline += dy
        for run in ln.runs:
            run.x += dx
            run.y += dy
            run.baseline += dy
    layout.text_x += dx
    layout.text_y += dy
    layout.cx += dx
    layout.cy += dy
    layout.hit_x += dx
    layout.hit_y += dy
    ox, oy = layout.origin
    layout.origin = (int(round(ox + dx)), int(round(oy + dy)))


def _place_in_box(layout: LayoutResult, box, align: str, pads: dict) -> None:
    """把排好的文字块放进框里。水平看 align，垂直看 pads["valign"]。

    Q124 之前垂直永远是顶对齐（dy 是常量），想把两行字居中在下三分之一
    只能手动算 Y。老工程没存 valign → top，画出来和以前一模一样。
    """
    x, y, w, h = box
    left = float(pads.get("left_pad") or 0)
    right = float(pads.get("right_pad") or 0)
    top = float(pads.get("top_pad") or 0)
    bottom = float(pads.get("bottom_pad") or 0)
    tw, th = layout.text_w, layout.text_h
    inner_w = max(1.0, w - left - right)
    inner_h = max(1.0, h - top - bottom)
    if align == "left":
        dx = (x + left) - layout.text_x
    elif align == "right":
        dx = (x + w - right - tw) - layout.text_x
    elif align == "justify":
        dx = (x + left) - layout.text_x
        _justify_lines(layout, inner_w)
    else:
        dx = (x + left + (inner_w - tw) * 0.5) - layout.text_x
    valign = str(pads.get("valign") or "top")
    if valign == "middle":
        dy = (y + top + (inner_h - th) * 0.5) - layout.text_y
    elif valign == "bottom":
        dy = (y + h - bottom - th) - layout.text_y
    else:
        dy = (y + top) - layout.text_y
    _translate_layout(layout, dx, dy)


def _justify_lines(layout: LayoutResult, inner_w: float) -> None:
    for ln in layout.lines:
        if len(ln.runs) < 2:
            continue
        extra = inner_w - ln.w
        if extra <= 0.5:
            continue
        gap = extra / float(len(ln.runs) - 1)
        x = ln.runs[0].x
        for i, run in enumerate(ln.runs):
            run.x = x
            x += run.w + (gap if i < len(ln.runs) - 1 else 0.0)
        ln.w = inner_w


def _apply_first_indent(layout: LayoutResult, indent: float) -> None:
    if indent == 0.0 or not layout.lines:
        return
    ln = layout.lines[0]
    for run in ln.runs:
        run.x += indent
    ln.x += indent
    ln.w += indent


def _recompute_roi(layout: LayoutResult, spec, video_w: int, video_h: int) -> None:
    from cutora.render.textfx.layout import fixed_bg_bounds
    from cutora.render.textfx.spec import expansion_px
    em = layout.em
    expand = expansion_px(spec, em)
    tx, ty, tw, th = layout.text_x, layout.text_y, layout.text_w, layout.text_h
    pl, pt, pr, pb = spec.max_transform_bounds()
    # fit_text: same text-union ROI as before. fixed: include the pixel box
    # so a panel larger than the glyphs is not clipped after _place_in_box.
    left, top, right, bot = tx, ty, tx + tw, ty + th
    bounds = fixed_bg_bounds(layout.lines, tx, ty, tw, th, spec, em)
    if bounds is not None:
        left = min(left, bounds[0])
        top = min(top, bounds[1])
        right = max(right, bounds[2])
        bot = max(bot, bounds[3])
        layout.hit_x, layout.hit_y = bounds[0], bounds[1]
        layout.hit_w = bounds[2] - bounds[0]
        layout.hit_h = bounds[3] - bounds[1]
    else:
        layout.hit_x, layout.hit_y = tx, ty
        layout.hit_w, layout.hit_h = tw, th
    # Q142：以前 ROI 夹在画布 [0, 画布宽/高] 里 —— 文本层一旦伸出画布边，
    # 伸出去的那截在光栅里就没了；再一缩放 / 旋转把它转回画面里，就是一刀
    # 硬切（Luca：「在边界经常被莫名的硬裁切掉」）。现在只防离谱尺寸：
    # 允许伸出画布一整个画布那么远，画面外的部分最终由画布 / 导出帧自己裁。
    mx = int(max(video_w, video_h))
    ox = max(-mx, int(math.floor(left - expand - pl * em)))
    oy = max(-mx, int(math.floor(top - expand - pt * em)))
    x1 = min(video_w + mx, int(right + expand + pr * em + 1))
    y1 = min(video_h + mx, int(bot + expand + pb * em + 1))
    layout.origin = (ox, oy)
    layout.roi_w = max(1, x1 - ox)
    layout.roi_h = max(1, y1 - oy)


def auto_height_for(layout: LayoutResult, pads: dict) -> float:
    """框高 = 文字高 + 上下边距。至少一行的高度，空文本也有个可点的框。"""
    top = float(pads.get("top_pad") or 0)
    bottom = float(pads.get("bottom_pad") or 0)
    th = float(layout.text_h or 0.0)
    if th < 1.0:
        th = float(layout.em or 40.0) * 1.3
    return max(8.0, th + top + bottom)


def measure_text_block(ov: dict, video_w: int, video_h: int) -> tuple[float, float]:
    """这条文本按当前框宽排完是多宽多高（不画，只量）。新建时用来定框高。"""
    vw = int(video_w or ov.get("_canvas_w") or 1080)
    vh = int(video_h or ov.get("_canvas_h") or 1920)
    ov = _layout_view(ov)
    style = overlay_to_style(ov, vw, vh)
    event = overlay_event(ov)
    spec = from_style(style)
    layout = layout_event(event, style, spec, vw, vh)
    pads = overlay_text_layout(ov)
    w = float(layout.text_w) + float(pads.get("left_pad") or 0) + float(pads.get("right_pad") or 0)
    return max(1.0, w), auto_height_for(layout, pads)


FIT_SCALE_MIN = 0.15


def _shrunk_style(style: dict, s: float) -> dict:
    """「缩小适配」：整块字按 s 等比缩小 —— 字号、折行宽度、行距、段距、首行缩进一起乘 s，
    所以**折行不变**（和 Q142 矢量缩放同一个前提）。"""
    out = dict(style)
    out["fontsize"] = max(8.0, float(style.get("fontsize") or 40) * s)
    try:
        out["wrap_width_percent"] = max(0.5, float(style.get("wrap_width_percent") or 86.0) * s)
    except (TypeError, ValueError):
        pass
    for key in ("line_spacing", "_para_spacing", "_first_indent"):
        try:
            if style.get(key):
                out[key] = float(style.get(key) or 0) * s
        except (TypeError, ValueError):
            pass
    out["_fit_scale"] = s
    return out


def _fit_to_box(layout: LayoutResult, box, style: dict, spec, event, video_w, video_h):
    """fit_mode="shrink"：放不下就整块等比缩小。

    Q145：以前只把字号缩小、折行宽度不变 —— 字一小，一行能多放几个词，换行位置
    全变了（Luca：「调行距的时候排版全乱了」—— 行距一加高度放不下就触发缩小，
    结果连折行都变了）。现在折行按用户设的字号排好就定下来，放不下只等比缩小。"""
    mode = str(style.get("fit_mode") or "none")
    if mode != "shrink":
        return layout, spec, style
    _x, _y, bw, bh = box
    if layout.text_w <= bw and layout.text_h <= bh:
        return layout, spec, style
    base = style
    s = 1.0
    cur_layout, cur_spec, cur_style = layout, spec, style
    for i in range(4):
        sx = bw / max(1.0, cur_layout.text_w)
        sy = bh / max(1.0, cur_layout.text_h)
        step = min(sx, sy)
        if step >= 1.0 - 1e-4 and i > 0:
            break
        s = max(FIT_SCALE_MIN, min(1.0, s * step * (1.0 if i == 0 else 0.995)))
        cur_style = _shrunk_style(base, s)
        cur_spec = from_style(cur_style)
        cur_layout = layout_event(event, cur_style, cur_spec, video_w, video_h)
        if s <= FIT_SCALE_MIN:
            break
        if cur_layout.text_w <= bw + 0.5 and cur_layout.text_h <= bh + 0.5:
            break
    return cur_layout, cur_spec, cur_style


TEXT_SCALE_MIN = 0.1
TEXT_SCALE_MAX = 5.0


def overlay_scale(ov: dict) -> float:
    """文本层的整体缩放（默认 1）。坏值 / 缺省 → 1，夹在 0.1–5。"""
    try:
        sc = float(ov.get("scale", 1.0) or 1.0)
    except (TypeError, ValueError):
        return 1.0
    if sc != sc:  # NaN
        return 1.0
    return max(TEXT_SCALE_MIN, min(TEXT_SCALE_MAX, sc))


_TL_PX_KEYS = ("left_pad", "right_pad", "top_pad", "bottom_pad",
               "line_spacing", "letter_spacing", "first_indent", "para_spacing")


def _vector_scaled(ov: dict) -> bool:
    """这条文本层的缩放是不是已经按矢量画进了图里（见 _scaled_layers）。"""
    fx = ov.get("fx") if isinstance(ov.get("fx"), dict) else {}
    bg = fx.get("bg") if isinstance(fx.get("bg"), dict) else {}
    kind = str(bg.get("type") or "none")
    return kind != "glass"


def scaled_overlay(ov: dict, s: float) -> dict:
    """Q142：整个文本层按 s 倍放大 / 缩小的「矢量」副本（坐标系也乘 s）。

    所有像素量（框、字号、内边距、行距、段落间距、run 的字号）乘 s；fx 本来就
    按 em 记，跟着字号走。fx 从原层取（老的平铺键在这里就折算成 em，免得
    px/em 比例被乘歪）。排版宽度 = 框宽 / 画布宽，两边都乘 s，折行不变。
    """
    import copy
    from cutora.subtitle.text_layer import overlay_fx_from_legacy
    out = copy.deepcopy(ov)
    k = float(s)
    for key in ("x", "y", "w", "h"):
        out[key] = float(ov.get(key) or 0) * k
    try:
        out["fontsize"] = float(ov.get("fontsize") or 40) * k
    except (TypeError, ValueError):
        out["fontsize"] = 40.0 * k
    lay = ov.get("text_layout") if isinstance(ov.get("text_layout"), dict) else None
    if lay is not None:
        lay2 = dict(lay)
        for key in _TL_PX_KEYS:
            if key in lay2:
                try:
                    lay2[key] = float(lay2[key] or 0) * k
                except (TypeError, ValueError):
                    pass
        out["text_layout"] = lay2
    runs = ov.get("runs")
    if isinstance(runs, list):
        new_runs = []
        for r in runs:
            r2 = copy.deepcopy(r)
            o = r2.get("overrides") if isinstance(r2, dict) else None
            if isinstance(o, dict) and o.get("fontsize"):
                try:
                    o["fontsize"] = float(o["fontsize"]) * k
                except (TypeError, ValueError):
                    pass
            new_runs.append(r2)
        out["runs"] = new_runs
    if not isinstance(ov.get("fx"), dict):
        out["fx"] = overlay_fx_from_legacy(ov)
    out.pop("scale", None)
    out.pop("keyframes", None)
    return out


def _scaled_layers(ov: dict, sc: float, box, vw: int, vh: int, t, backdrop):
    """在 sc 倍坐标系里重新排版 + 光栅，返回画在原画布上的 (bg, text, origin)。

    原画布上的点 p = c + sc·(q − c)，q 是未缩放坐标、c 是框中心。放大后的坐标
    q_s = sc·q，所以 p = q_s + c·(1 − sc)。把 c·(1−sc) 的小数部分先挪进排版里，
    整数部分作为落点 —— 贴图时不重采样，字是按目标大小矢量画出来的。
    """
    x, y, w, h = box
    cx, cy = x + w * 0.5, y + h * 0.5
    dx, dy = cx * (1.0 - sc), cy * (1.0 - sc)
    ix, iy = math.floor(dx), math.floor(dy)
    ovs = scaled_overlay(ov, sc)
    ovs["x"] = float(ovs["x"]) + (dx - ix)
    ovs["y"] = float(ovs["y"]) + (dy - iy)
    vws = max(1, int(round(vw * sc)))
    vhs = max(1, int(round(vh * sc)))
    layout, spec, style, event, _box = layout_for_overlay(ovs, vws, vhs)
    xf = None
    if t is not None:
        from cutora.render.textfx.anim import transform_at
        xf = transform_at(float(t), event, spec, layout)
    bg = raster_bg(layout, spec, None, vws, vhs, skip_blur=False)
    text = raster_text(layout, spec, style, skip_blur=False, t=t, xf=xf)
    if xf is not None and t is not None:
        from cutora.render.textfx.word_fx import apply_geom, apply_word_color
        text = apply_word_color(text, layout, spec, float(t), event)
        text = apply_geom(text, layout, spec, float(t), event, xf, vws, vhs)
        if bg is not None:
            bg_xf = replace(xf, blur_sigma_em=0.0, extra_color=None)
            bg = apply_geom(bg, layout, spec, float(t), event, bg_xf, vws, vhs)
    origin = (int(layout.origin[0]) + ix, int(layout.origin[1]) + iy)
    return bg, text, origin


def layout_for_overlay(ov: dict, vw: int, vh: int):
    """Q127：把一条文本层排好、放进框里，但不画。

    paint_text_overlay 和画布上的光标几何（text_caret）共用这一份，
    保证光标落点和真正画出来的字是同一次排版。grow_box / auto_height
    会像绘制时一样回写 ov["w"] / ov["h"]。

    Returns (layout, spec, style, event, (x, y, w, h)).
    """
    x = float(ov.get("x") or 0)
    y = float(ov.get("y") or 0)
    w = float(ov.get("w") or 100)
    h = float(ov.get("h") or 100)
    src = ov
    ov = _layout_view(ov)
    style = overlay_to_style(ov, vw, vh)
    event = overlay_event(ov)
    spec = from_style(style)
    layout = layout_event(event, style, spec, vw, vh)
    layout, spec, style = _fit_to_box(
        layout, (x, y, w, h), style, spec, event, vw, vh)
    fit = str(style.get("fit_mode") or "")
    if fit == "grow_box":
        pads = overlay_text_layout(ov)
        need_w = layout.text_w + float(pads.get("left_pad") or 0) + float(pads.get("right_pad") or 0)
        need_h = layout.text_h + float(pads.get("top_pad") or 0) + float(pads.get("bottom_pad") or 0)
        if need_w > w:
            src["w"] = ov["w"] = w = need_w
        if need_h > h:
            src["h"] = ov["h"] = h = need_h
    elif fit == "auto_height":
        # Q124：宽度用户定，高度跟着文字走 —— 双向。打到第三行框长高，
        # 删回两行框变矮，用户永远不用管高度。这是新建文本的默认。
        pads = overlay_text_layout(ov)
        need_h = auto_height_for(layout, pads)
        if abs(need_h - h) > 0.5:
            src["h"] = ov["h"] = h = need_h
    lay = overlay_text_layout(ov)
    _apply_first_indent(layout, float(style.get("_first_indent") or 0))
    align = str(lay.get("align") or "left")
    _place_in_box(layout, (x, y, w, h), align, lay)
    runs = event.get("runs") or []
    if runs:
        fit_s = float(style.get("_fit_scale") or 1.0)
        if fit_s < 0.9999:
            runs = _scale_run_sizes(runs, fit_s)
        before = [(ln.x, ln.w) for ln in layout.lines]
        apply_run_overrides(layout, event.get("text") or "", runs)
        _realign_after_runs(layout, align, before)
    _recompute_roi(layout, spec, vw, vh)
    return layout, spec, style, event, (x, y, w, h)


def _layout_view(ov: dict) -> dict:
    """排版用的视图：覆盖全文的 run 格式先提升成本层格式（见 promote_whole_runs）。
    没有 run 的文本层原样返回同一个 dict —— 老工程、golden 一个字节不变。"""
    if not ov.get("runs"):
        return ov
    import copy
    from cutora.subtitle.text_layer import promote_whole_runs
    view = dict(ov)
    view["runs"] = copy.deepcopy(ov.get("runs"))
    if isinstance(ov.get("fx"), dict):
        view["fx"] = copy.deepcopy(ov["fx"])
    if promote_whole_runs(view):
        return view
    return ov


def _scale_run_sizes(runs: list, s: float) -> list:
    """缩小适配时，局部富文本里写死的字号跟着整块一起缩。"""
    out = []
    for item in runs:
        if not isinstance(item, dict):
            continue
        o = item.get("overrides")
        if isinstance(o, dict) and o.get("fontsize"):
            item = dict(item)
            o = dict(o)
            try:
                o["fontsize"] = max(1.0, float(o["fontsize"]) * s)
            except (TypeError, ValueError):
                pass
            item["overrides"] = o
        out.append(item)
    return out


def _realign_after_runs(layout: LayoutResult, align: str, before: list) -> None:
    """局部富文本换了字体 / 字号以后，行宽变了：按对齐方式把**变了宽度的行**重新摆好，
    并更新整块文字的范围 —— 光栅范围按它算，以前没更新，变宽的字就被切掉。
    宽度没变的行（只改颜色 / 下划线）一个像素都不动。"""
    changed = False
    for ln, (old_x, old_w) in zip(layout.lines, before):
        runs = getattr(ln, "runs", None) or []
        if not runs:
            continue
        new_w = (runs[-1].x + runs[-1].w) - runs[0].x
        if abs(new_w - float(old_w)) <= 0.01:
            continue
        changed = True
        cur_x = runs[0].x
        if align in ("center", "centre"):
            want = float(old_x) + float(old_w) * 0.5 - new_w * 0.5
        elif align == "right":
            want = float(old_x) + float(old_w) - new_w
        else:
            want = cur_x
        dx = want - cur_x
        if abs(dx) > 1e-6:
            for r in runs:
                r.x += dx
        ln.x = runs[0].x
        ln.w = new_w
    if not changed:
        return
    live = [ln for ln in layout.lines if getattr(ln, "runs", None)]
    left = min([float(layout.text_x)] + [ln.x for ln in live])
    right = max([float(layout.text_x + layout.text_w)] + [ln.x + ln.w for ln in live])
    top = min([float(layout.text_y)] + [ln.y for ln in live])
    bot = max([float(layout.text_y + layout.text_h)] + [ln.y + ln.h for ln in live])
    layout.text_x, layout.text_w = left, right - left
    layout.text_y, layout.text_h = top, bot - top


# ── Q130：静态文本层的光栅缓存 ──────────────────────────────────────────
# 一条没有动画、没有关键帧、背景不是毛玻璃的文本层，第 1 帧和第 5000 帧画出来
# 一模一样，但以前每一帧都重新排版 + 光栅（实测每条 15–20 ms）。22 条视频
# 里那句「Generated by AI」被重画了 10 万次。现在按「overlay 内容 + 画布尺寸」
# 记住 bg / text 两张图，命中就只剩两次 drawImage。像素与不缓存时逐字节相同。
_STATIC_MAX = 48
_STATIC_CACHE: "OrderedDict[tuple, tuple]" = None
_STATIC_LOCK = None
STATIC_STATS = {"hit": 0, "miss": 0, "skip": 0}


def _static_store():
    global _STATIC_CACHE, _STATIC_LOCK
    if _STATIC_CACHE is None:
        import threading
        from collections import OrderedDict
        _STATIC_CACHE = OrderedDict()
        _STATIC_LOCK = threading.Lock()
    return _STATIC_CACHE, _STATIC_LOCK


def clear_static_cache() -> None:
    cache, lock = _static_store()
    with lock:
        cache.clear()


def _static_key(ov: dict, vw: int, vh: int):
    import json
    try:
        body = json.dumps(ov, sort_keys=True, default=str, ensure_ascii=False)
    except Exception:
        return None
    return (body, int(vw), int(vh))


def _cacheable(ov: dict, spec) -> bool:
    """这条文本层的光栅原则上和时间无关？（逐帧还要再问 _hold_token）"""
    try:
        from cutora.render.keyframe import keyframes_of
        if keyframes_of(ov):
            return False
    except Exception:
        return False
    if spec.bg.kind() == "glass":          # 毛玻璃读底图，每帧都不同
        return False
    from cutora.render.textfx.anim import _anim_id
    from cutora.render.textfx.word_fx import UNIT_WARP, WORD_COLOR
    anim = spec.anim
    if _anim_id(anim.loop) != "none":       # 循环动画没有静止段
        return False
    for name in (anim.in_name, anim.out_name):
        aid = _anim_id(name)
        if aid in UNIT_WARP or aid in WORD_COLOR:   # 逐词 / 逐行 / 换字色：每帧不同
            return False
    # 普通进 / 出场动画可以：播完到出场之前那一大段是静止的，逐帧由 _hold_token 把关
    return True


def _layer_at_rest(t: float, event: dict, spec, layout) -> bool:
    """这一帧整层静止？= 不在重点词 / 进出场窗口里，且整层变换是恒等。
    transform_at 只做几行算术，比重新排版 + 光栅便宜三个数量级。"""
    from cutora.render.textfx.anim import is_static_hold, transform_at, xf_is_identity
    if not is_static_hold(t, event, spec):
        return False
    if layout is None:
        return True
    return xf_is_identity(transform_at(t, event, spec, layout))


def _hold_token(spec, event, t, layout=None):
    """这一帧能用缓存吗？能就返回一个键片段（重点词划线画完前 / 画完后是两张图），
    不能（正在 pop / 划线 / 进出场的那几帧）返回 None。"""
    if t is None:
        return "static"
    if not _layer_at_rest(float(t), event, spec, layout):
        return None
    from cutora.render.textfx.pipeline import _emph_line_token
    tok = _emph_line_token(spec, float(t), event)
    if isinstance(tok, int):
        return None
    return ("hold", tok)


def paint_text_overlay(painter, ov, current_time=None, backdrop=None, video_w=None, video_h=None):
    """Raster fx bg + text into *painter*. Returns the overlay's logical rect."""
    if current_time is not None:
        from cutora.render.keyframe import bake_host, local_time
        ov = bake_host(ov, local_time(ov, current_time))
    x = float(ov.get("x") or 0)
    y = float(ov.get("y") or 0)
    w = float(ov.get("w") or 100)
    h = float(ov.get("h") or 100)
    logical = QRectF(x, y, w, h)
    if painter is None or painter.paintEngine() is None:
        return logical
    if ov.get("_inline_editing"):
        return logical
    vw = int(video_w or ov.get("_canvas_w") or 1080)
    vh = int(video_h or ov.get("_canvas_h") or 1920)

    cache, lock = _static_store()
    key0 = _static_key(ov, vw, vh)
    hit = None
    key = None
    meta = None
    if key0 is not None:
        with lock:
            meta = cache.get(("meta",) + key0)
        if meta is not None and meta["ok"]:
            tok = _hold_token(meta["spec"], meta["event"], current_time, meta["layout"])
            if tok is not None:
                key = key0 + (tok,)
                with lock:
                    hit = cache.get(key)
                    if hit is not None:
                        cache.move_to_end(key)
    if hit is not None:
        STATIC_STATS["hit"] += 1
        bg, text, origin, (x, y, w, h) = hit
        # grow_box / auto_height 当初改过框：同一份数据再画一遍也要落到同样的框上
        if abs(float(ov.get("w") or 0) - w) > 1e-6:
            ov["w"] = w
        if abs(float(ov.get("h") or 0) - h) > 1e-6:
            ov["h"] = h
        logical = QRectF(x, y, w, h)
    else:
        layout, spec, style, event, (x, y, w, h) = layout_for_overlay(ov, vw, vh)
        logical = QRectF(x, y, w, h)

        xf = None
        t = current_time
        if t is not None:
            from cutora.render.textfx.anim import transform_at
            xf = transform_at(float(t), event, spec, layout)
        sc0 = overlay_scale(ov)
        vector_scale = abs(sc0 - 1.0) > 1e-6 and _vector_scaled(ov)
        if vector_scale:
            # Q142：缩放不再是把位图拉大（放大就糊），按目标大小重新排版光栅
            bg, text, origin = _scaled_layers(ov, sc0, (x, y, w, h), vw, vh, t, backdrop)
        else:
            bg = raster_bg(layout, spec, backdrop, vw, vh, skip_blur=False)
            text = raster_text(layout, spec, style, skip_blur=False, t=t, xf=xf)
            if xf is not None and t is not None:
                from cutora.render.textfx.word_fx import apply_geom, apply_word_color
                text = apply_word_color(text, layout, spec, float(t), event)
                text = apply_geom(text, layout, spec, float(t), event, xf, vw, vh)
                if bg is not None:
                    bg_xf = replace(xf, blur_sigma_em=0.0, extra_color=None)
                    bg = apply_geom(bg, layout, spec, float(t), event, bg_xf, vw, vh)
            origin = (int(layout.origin[0]), int(layout.origin[1]))
        if key0 is not None:
            if meta is None:
                meta = {"ok": _cacheable(ov, spec), "spec": spec, "event": event, "layout": layout}
                if meta["ok"]:
                    # 不能缓存的（关键帧每帧一个新 key）不进表，免得把表撑爆
                    with lock:
                        cache[("meta",) + key0] = meta
                        while len(cache) > _STATIC_MAX:
                            cache.popitem(last=False)
            stored = False
            if meta["ok"]:
                from cutora.render.textfx.anim import xf_is_identity
                tok = _hold_token(spec, event, t, layout)
                if tok is not None and (xf is None or xf_is_identity(xf)):
                    with lock:
                        cache[key0 + (tok,)] = (bg, text, origin, (x, y, w, h))
                        while len(cache) > _STATIC_MAX:
                            cache.popitem(last=False)
                    stored = True
            STATIC_STATS["miss" if stored else "skip"] += 1

    opacity = float(ov.get("opacity") or 255) / 255.0
    rotation = float(ov.get("rotation") or 0)
    painter.save()
    try:
        if rotation:
            cx = x + w * 0.5
            cy = y + h * 0.5
            painter.translate(cx, cy)
            painter.rotate(rotation)
            painter.translate(-cx, -cy)
        # Q129：缩放 —— 静态值和关键帧都认（bake_host 已经把关键帧烤进 ov["scale"]）。
        # 像图片一样绕框中心整体放大缩小，排版（折行 / 字号）一个不动。
        sc = overlay_scale(ov)
        if abs(sc - 1.0) > 1e-6 and not _vector_scaled(ov):
            # 只有毛玻璃背景还走位图缩放（它读的是画布底图，坐标不能换）
            cx = x + w * 0.5
            cy = y + h * 0.5
            painter.translate(cx, cy)
            painter.scale(sc, sc)
            painter.translate(-cx, -cy)
        if opacity < 0.999:
            painter.setOpacity(max(0.0, min(1.0, opacity)) * painter.opacity())
        pt = QPoint(int(origin[0]), int(origin[1]))
        if bg is not None:
            painter.drawImage(pt, bg)
        painter.drawImage(pt, text)
    finally:
        painter.restore()
    return logical
