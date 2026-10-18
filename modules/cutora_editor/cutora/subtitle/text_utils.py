# SPDX-License-Identifier: GPL-3.0-or-later
# Extracted from the T01 monolith by tools/split_monolith.py.
# Original lines: 1015-1016, 1017-1017, 1018-1018, 1019-1019, 1020-1020, 1021-1021, 1022-1022, 1023-1023, 1024-1024, 1025-1025, 9456-9478, 9480-9487, 9489-9517, 9519-9565, 9567-9577, 9579-9582, 9584-9593, 9595-9599, 9601-9609, 9611-9617, 9619-9642, 9644-9649, 9651-9723, 9725-9802, 9804-9823, 9825-9846, 9848-9864, 9866-9877, 9879-9891, 9893-9897, 9899-9967, 9969-9978, 9980-10078, 10188-10210, 10212-10247, 10249-10313, 10315-10453

import json
import re
from PySide6.QtCore import (
    Qt,
    QRectF,
    QPointF,
)
from PySide6.QtGui import (
    QColor,
    QFont,
    QPen,
    QBrush,
    QFontMetrics,
    QPainterPath,
    QLinearGradient,
    QTextFormat,
    QTextOption,
    QTextDocument,
    QRadialGradient,
)
from PySide6.QtCore import Qt
from cutora.ui.panels.fonts import get_font_manager

DEFAULT_FONT_FAMILY = "Arimo"

# === Rich text custom properties (QTextCharFormat) ===
_RT_PROP_STROKE_COLOR = int(QTextFormat.Property.UserProperty) + 1

_RT_PROP_STROKE_WIDTH = int(QTextFormat.Property.UserProperty) + 2

_RT_PROP_SHADOW_COLOR = int(QTextFormat.Property.UserProperty) + 3

_RT_PROP_SHADOW_OPACITY = int(QTextFormat.Property.UserProperty) + 4

_RT_PROP_SHADOW_BLUR = int(QTextFormat.Property.UserProperty) + 5

_RT_PROP_SHADOW_OFF_X = int(QTextFormat.Property.UserProperty) + 6

_RT_PROP_SHADOW_OFF_Y = int(QTextFormat.Property.UserProperty) + 7

ENABLE_RICH_TEXT = False

_RICH_PARSE_FAST_LIMIT = 4000

_RICH_PARSE_BLOCK_LIMIT = 300

def tokenize_for_wrap(text: str):

    if not text:
        return [], ""
    
    # 修复：防止Windows换行符 \r\n 和单独的 \r 导致分词数组指针索引错乱。
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    has_space = " " in text
    
    import re
    if has_space:
        # 终极修复：绝对不要依赖 .split(" ")！
        # 使用正则提取：任何非空格字符连续块，或者独立的 \n 字符！
        # 这样无论有没有物理空格，只要有 \n，它都会被极其干净利落地切成独立的 token ('ORE', '\n', 'Caro')
        tokens = re.findall(r'[^\s\n]+|\n', text)
        return tokens, " "
    
    # 无空格的场景（如纯中文，或用户强行输入的无空格连续字符）
    # 退化为按字符切割，同时也单独保护 \n 不被粘连
    tokens = []
    for char in text:
        tokens.append(char)
    return tokens, ""

def split_token_by_width(token: str, fm: QFontMetrics, max_width_px: int):
    """已弃用：为了保证“单词完整”，不再按字符拆分单词。

    以前为了极窄字幕框会把超长单词按字符拆段，但这会在渲染/换行时引入
    视觉上的“空格拆词”（比如 preoccupazi o n e）。用户要求必须保持单词完整，
    所以这里保留函数签名但不再拆分。
    """
    return [token]

def wrap_tokens_by_pixel_width(tokens, fm: QFontMetrics, max_width_px: int, joiner: str):
    lines = []
    curr = ""
    for token in tokens:
        if "\n" in token:
            parts = token.split("\n")
            for i, part in enumerate(parts):
                if i > 0:
                    lines.append(curr)
                    curr = ""
                if not part:
                    continue
                cand = part if not curr else (curr + joiner + part)
                if (not curr) or fm.horizontalAdvance(cand) <= max_width_px:
                    curr = cand
                else:
                    if curr: lines.append(curr)
                    curr = part
            continue

        cand = token if not curr else (curr + joiner + token)
        if (not curr) or fm.horizontalAdvance(cand) <= max_width_px:
            curr = cand
        else:
            lines.append(curr)
            curr = token
    if curr or (tokens and tokens[-1].endswith("\n")):
        lines.append(curr)
    return lines

def wrap_tokens_with_indices(tokens, fm: QFontMetrics, max_width_px: int, joiner: str):
    lines = []
    indices = []
    curr = ""
    curr_idx_list = []
    
    for idx, token in enumerate(tokens):
        if "\n" in token:
            parts = token.split("\n")
            for i, part in enumerate(parts):
                if i > 0:
                    lines.append(curr)
                    indices.append(curr_idx_list)
                    curr = ""
                    curr_idx_list = []
                if not part:
                    # Only append the newline token to the first line segment it touches
                    if i == 0:
                        curr_idx_list.append(idx)
                    continue
                cand = part if not curr else (curr + joiner + part)
                if (not curr) or fm.horizontalAdvance(cand) <= max_width_px:
                    curr = cand
                    curr_idx_list.append(idx)
                else:
                    if curr: 
                        lines.append(curr)
                        indices.append(curr_idx_list)
                    curr = part
                    curr_idx_list = [idx]
            continue

        cand = token if not curr else (curr + joiner + token)
        if (not curr) or fm.horizontalAdvance(cand) <= max_width_px:
            curr = cand
            curr_idx_list.append(idx)
        else:
            lines.append(curr)
            indices.append(curr_idx_list)
            curr = token
            curr_idx_list = [idx]
            
    if curr or (tokens and tokens[-1].endswith("\n")):
        lines.append(curr)
        indices.append(curr_idx_list)
        
    return lines, indices

def wrap_text_by_pixel_width(text: str, fm: QFontMetrics, max_width_px: int):
    if text is None:
        return []
    lines = []
    for raw in str(text).split("\n"):
        tokens, joiner = tokenize_for_wrap(raw)
        if tokens:
            lines.extend(wrap_tokens_by_pixel_width(tokens, fm, max_width_px, joiner))
        else:
            lines.append(raw)
    return lines

def _rich_plain_text(spans):
    if not isinstance(spans, list):
        return ""
    return "".join(str(s.get("text", "")) for s in spans if isinstance(s, dict))

def _rich_default_layout(layout):
    """text_layout 补齐默认值。键表跟 style_schema.TEXT_LAYOUT_DEFAULT 走。

    以前这里手写了一份只有七个键的表，valign / first_indent / para_spacing
    一进属性面板就被丢掉：面板显示成默认值，再一保存就把用户设过的值
    冲成默认 —— 「垂直居中」选了存了重开就没了。
    """
    from cutora.subtitle.style_schema import TEXT_LAYOUT_DEFAULT
    base = dict(TEXT_LAYOUT_DEFAULT)
    if isinstance(layout, dict):
        base.update({k: layout.get(k, base[k]) for k in base})
    return base

def _normalize_text_align(align_name):
    val = str(align_name or "left").strip().lower()
    if val in ("left", "right", "center", "justify"):
        return val
    return "left"

def _qt_align_flag_for_text(align_name):
    a = _normalize_text_align(align_name)
    if a == "right":
        return Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop
    if a == "center":
        return Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop
    if a == "justify":
        return Qt.AlignmentFlag.AlignJustify | Qt.AlignmentFlag.AlignTop
    return Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop

def _align_x_for_block(box_x, box_w, line_w, align_name):
    a = _normalize_text_align(align_name)
    if a == "right":
        return box_x + max(0.0, box_w - line_w)
    if a == "center":
        return box_x + max(0.0, (box_w - line_w) / 2.0)
    return box_x

def _iter_line_segments_for_draw(line_text, fm, box_x, box_w, align_name, is_last_line):
    text = str(line_text or "")
    if text == "":
        return
    a = _normalize_text_align(align_name)
    if a == "justify" and (not is_last_line):
        tokens = re.findall(r"\S+|\s+", text)
        if tokens:
            base_w = sum(float(fm.horizontalAdvance(tok)) for tok in tokens)
            space_count = sum(1 for tok in tokens if tok.isspace())
            extra_total = max(0.0, float(box_w) - base_w)
            extra_space = (extra_total / space_count) if space_count > 0 else 0.0
            x = float(box_x)
            for tok in tokens:
                w = float(fm.horizontalAdvance(tok))
                if tok.isspace():
                    x += w + extra_space
                else:
                    yield tok, x
                    x += w
            return
    line_w = float(fm.horizontalAdvance(text))
    x0 = _align_x_for_block(float(box_x), float(box_w), line_w, a)
    yield text, x0

def _visible_lines(lines, fm, box_h):
    line_h = max(1, int(fm.height()))
    max_lines = max(0, int(float(box_h) // line_h))
    if max_lines <= 0:
        return []
    return list(lines[:max_lines])

def _build_text_bg_brush(obj, rect):
    fill_type = str(obj.get("bg_fill_type", "solid") or "solid").lower()
    grad_dir = str(obj.get("bg_grad_dir", "top_bottom") or "top_bottom").lower()
    opacity = int(obj.get("bg_opacity", 191))

    def _mk_color(key, fallback):
        c = QColor(obj.get(key, fallback))
        c.setAlpha(opacity)
        return c

    c1 = _mk_color("bg_color", "#000000")
    c2 = _mk_color("bg_color2", obj.get("bg_color", "#000000"))
    c3 = _mk_color("bg_color3", obj.get("bg_color2", obj.get("bg_color", "#000000")))
    c4 = _mk_color("bg_color4", obj.get("bg_color3", obj.get("bg_color2", obj.get("bg_color", "#000000"))))

    def _linear_points(r, d):
        if d == "left_right":
            return r.topLeft(), r.topRight()
        if d == "right_left":
            return r.topRight(), r.topLeft()
        if d == "bottom_top":
            return r.bottomLeft(), r.topLeft()
        if d == "tl_br":
            return r.topLeft(), r.bottomRight()
        if d == "br_tl":
            return r.bottomRight(), r.topLeft()
        if d == "tr_bl":
            return r.topRight(), r.bottomLeft()
        if d == "bl_tr":
            return r.bottomLeft(), r.topRight()
        return r.topLeft(), r.bottomLeft()

    def _radial_center(r, d):
        if d == "top":
            return QPointF(r.center().x(), r.top())
        if d == "bottom":
            return QPointF(r.center().x(), r.bottom())
        if d == "left":
            return QPointF(r.left(), r.center().y())
        if d == "right":
            return QPointF(r.right(), r.center().y())
        if d == "tl":
            return r.topLeft()
        if d == "tr":
            return r.topRight()
        if d == "bl":
            return r.bottomLeft()
        if d == "br":
            return r.bottomRight()
        return r.center()

    if fill_type.startswith("radial"):
        g = QRadialGradient(_radial_center(rect, grad_dir), max(rect.width(), rect.height()) * 0.6)
        if fill_type == "radial4":
            g.setColorAt(0.0, c1); g.setColorAt(0.33, c2); g.setColorAt(0.66, c3); g.setColorAt(1.0, c4)
        elif fill_type == "radial3":
            g.setColorAt(0.0, c1); g.setColorAt(0.5, c2); g.setColorAt(1.0, c3)
        else:
            g.setColorAt(0.0, c1); g.setColorAt(1.0, c2)
        return QBrush(g)

    if fill_type.startswith("linear"):
        p1, p2 = _linear_points(rect, grad_dir)
        g = QLinearGradient(p1, p2)
        if fill_type == "linear4":
            g.setColorAt(0.0, c1); g.setColorAt(0.33, c2); g.setColorAt(0.66, c3); g.setColorAt(1.0, c4)
        elif fill_type == "linear3":
            g.setColorAt(0.0, c1); g.setColorAt(0.5, c2); g.setColorAt(1.0, c3)
        else:
            g.setColorAt(0.0, c1); g.setColorAt(1.0, c2)
        return QBrush(g)

    return QBrush(c1)

def _draw_thumbnail_text_on_rect(painter, text_meta, target_rect):
    if not isinstance(text_meta, dict):
        return
    content = str(text_meta.get("content", "") or "")
    if not content.strip():
        return
    tw = float(target_rect.width())
    th = float(target_rect.height())
    if tw <= 1 or th <= 1:
        return
    x = float(text_meta.get("x", 0.0))
    y = float(text_meta.get("y", 0.0))
    w = float(text_meta.get("w", tw))
    h = float(text_meta.get("h", th))
    # 强制文本框在画布范围内
    x = max(0.0, min(x, tw - 1.0))
    y = max(0.0, min(y, th - 1.0))
    w = max(1.0, min(w, tw - x))
    h = max(1.0, min(h, th - y))
    font = get_font_manager().get_font(
        text_meta.get("font_family", DEFAULT_FONT_FAMILY),
        int(text_meta.get("fontsize", 42)),
        bold=bool(text_meta.get("bold", True)),
        italic=bool(text_meta.get("italic", False))
    )
    painter.save()
    try:
        painter.translate(target_rect.left(), target_rect.top())
        layout = _rich_default_layout(text_meta.get("text_layout"))
        box_x = x + float(layout.get("left_pad", 0))
        box_y = y + float(layout.get("top_pad", 0))
        box_w = max(1.0, w - float(layout.get("left_pad", 0)) - float(layout.get("right_pad", 0)))
        box_h = max(1.0, h - float(layout.get("top_pad", 0)) - float(layout.get("bottom_pad", 0)))
        align_name = _normalize_text_align(layout.get("align", "left"))

        painter.setFont(font)
        lines, max_line_w, _total_h, fm = _plain_layout_lines(content, font, box_w)
        visible_lines = _visible_lines(lines, fm, box_h)
        visible_h = len(visible_lines) * fm.height()
        visible_max_w = min(box_w, max((ln[1] for ln in visible_lines), default=0.0))

        if text_meta.get("bg_enabled", True) and visible_h > 0:
            pad_x = int(text_meta.get("bg_padding_x", text_meta.get("bg_padding", 12)))
            pad_y = int(text_meta.get("bg_padding_y", text_meta.get("bg_padding", 12)))
            bx = _align_x_for_block(box_x, box_w, visible_max_w, align_name) - pad_x
            by = box_y - pad_y
            bw = visible_max_w + pad_x * 2
            bh = visible_h + pad_y * 2
            bg_rect = QRectF(bx, by, bw, bh)
            rad = int(text_meta.get("bg_radius", 12))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(_build_text_bg_brush(text_meta, bg_rect))
            painter.drawRoundedRect(bg_rect, rad, rad)

        painter.save()
        painter.setClipRect(QRectF(box_x, box_y, box_w, box_h))
        text_c = QColor(text_meta.get("color", "#FFFFFF"))
        path = QPainterPath()
        y_cursor = box_y
        for i, line in enumerate(visible_lines):
            txt = line[0]
            if not txt:
                y_cursor += fm.height()
                continue
            for seg_text, seg_x in _iter_line_segments_for_draw(txt, fm, box_x, box_w, align_name, i == len(visible_lines) - 1):
                path.addText(seg_x, y_cursor + fm.ascent(), font, seg_text)
            y_cursor += fm.height()
        if text_meta.get("use_stroke", True):
            pen = QPen(QColor(text_meta.get("stroke_color", "#000000")), int(text_meta.get("stroke_width", 3)))
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(path)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(text_c)
        painter.drawPath(path)
        painter.restore()
    finally:
        painter.restore()

def _rich_span_with_fallback(span, base):
    out = dict(span) if isinstance(span, dict) else {}
    out.setdefault("text", "")
    out.setdefault("font_family", base.get("font_family", DEFAULT_FONT_FAMILY))
    out.setdefault("fontsize", base.get("fontsize", 40))
    out.setdefault("bold", base.get("bold", False))
    out.setdefault("italic", base.get("italic", False))
    out.setdefault("color", base.get("color", "#FFFFFF"))
    out.setdefault("bg_color", None)
    out.setdefault("bg_opacity", 255)
    out.setdefault("stroke_color", base.get("stroke_color", "#000000"))
    out.setdefault("stroke_width", base.get("stroke_width", 0))
    out.setdefault("use_stroke", base.get("use_stroke", False))
    out.setdefault("shadow_color", base.get("shadow_color", "#000000"))
    out.setdefault("shadow_opacity", base.get("shadow_opacity", 120))
    out.setdefault("shadow_blur", base.get("shadow_blur", 0))
    out.setdefault("shadow_offset_x", base.get("shadow_offset_x", 4))
    out.setdefault("shadow_offset_y", base.get("shadow_offset_y", 4))
    out.setdefault("shadow_enabled", base.get("shadow_enabled", False))
    return out

def _rich_apply_base_to_spans(spans, base):
    if not isinstance(spans, list):
        return
    for sp in spans:
        if not isinstance(sp, dict):
            continue
        if sp.get("text") == "\n":
            continue
        sp["font_family"] = base.get("font_family", sp.get("font_family", DEFAULT_FONT_FAMILY))
        sp["fontsize"] = int(base.get("fontsize", sp.get("fontsize", 40)))
        sp["bold"] = bool(base.get("bold", sp.get("bold", False)))
        sp["italic"] = bool(base.get("italic", sp.get("italic", False)))
        sp["color"] = base.get("color", sp.get("color", "#FFFFFF"))
        sp["use_stroke"] = bool(base.get("use_stroke", sp.get("use_stroke", False)))
        sp["stroke_color"] = base.get("stroke_color", sp.get("stroke_color", "#000000"))
        sp["stroke_width"] = int(base.get("stroke_width", sp.get("stroke_width", 0)))
        sp["shadow_enabled"] = bool(base.get("shadow_enabled", sp.get("shadow_enabled", False)))
        sp["shadow_color"] = base.get("shadow_color", sp.get("shadow_color", "#000000"))
        sp["shadow_opacity"] = int(base.get("shadow_opacity", sp.get("shadow_opacity", 120)))
        sp["shadow_blur"] = int(base.get("shadow_blur", sp.get("shadow_blur", 0)))
        sp["shadow_offset_x"] = int(base.get("shadow_offset_x", sp.get("shadow_offset_x", 4)))
        sp["shadow_offset_y"] = int(base.get("shadow_offset_y", sp.get("shadow_offset_y", 4)))

def _rich_apply_effects_to_spans(spans, base):
    if not isinstance(spans, list):
        return
    for sp in spans:
        if not isinstance(sp, dict):
            continue
        if sp.get("text") == "\n":
            continue
        sp["use_stroke"] = bool(base.get("use_stroke", sp.get("use_stroke", False)))
        sp["stroke_color"] = base.get("stroke_color", sp.get("stroke_color", "#000000"))
        sp["stroke_width"] = int(base.get("stroke_width", sp.get("stroke_width", 0)))
        sp["shadow_enabled"] = bool(base.get("shadow_enabled", sp.get("shadow_enabled", False)))
        sp["shadow_color"] = base.get("shadow_color", sp.get("shadow_color", "#000000"))
        sp["shadow_opacity"] = int(base.get("shadow_opacity", sp.get("shadow_opacity", 120)))
        sp["shadow_blur"] = int(base.get("shadow_blur", sp.get("shadow_blur", 0)))
        sp["shadow_offset_x"] = int(base.get("shadow_offset_x", sp.get("shadow_offset_x", 4)))
        sp["shadow_offset_y"] = int(base.get("shadow_offset_y", sp.get("shadow_offset_y", 4)))

def _rich_cache_key(spans, base_style, max_width, layout, letter_spacing):
    try:
        payload = {
            "spans": spans,
            "base": base_style,
            "max_width": float(max_width),
            "layout": layout,
            "letter_spacing": float(letter_spacing or 0)
        }
        return hash(json.dumps(payload, sort_keys=True, ensure_ascii=True))
    except Exception:
        return None

def _clear_rich_cache_in_task(task):
    if not isinstance(task, dict):
        return
    for ov in task.get("text_overlays", []) or []:
        if isinstance(ov, dict):
            ov.pop("_rich_cache", None)
    for ov in task.get("image_overlays", []) or []:
        if not isinstance(ov, dict):
            continue
        ov.pop("_rich_cache", None)
        for el in ov.get("elements", []) or []:
            if isinstance(el, dict):
                el.pop("_rich_cache", None)

def _rich_tokenize(text):
    if not text:
        return []
    # keep spaces and newlines as tokens
    return re.findall(r"\n|\s+|[^\s]+", str(text))

def _rich_layout_lines(spans, base_style, max_width, letter_spacing):
    lines = []
    curr = []
    curr_w = 0.0
    curr_ascent = 0.0
    curr_descent = 0.0

    def _finalize():
        nonlocal curr, curr_w, curr_ascent, curr_descent
        if not curr:
            return
        # trim trailing spaces
        while curr and curr[-1].get("is_space"):
            curr_w -= curr[-1].get("width", 0.0)
            curr.pop()
        if curr:
            lines.append({
                "segments": curr,
                "width": max(0.0, curr_w),
                "ascent": curr_ascent,
                "descent": curr_descent
            })
        curr = []
        curr_w = 0.0
        curr_ascent = 0.0
        curr_descent = 0.0

    for raw_span in spans or []:
        span = _rich_span_with_fallback(raw_span, base_style)
        text = span.get("text", "")
        if text == "":
            continue
        tokens = _rich_tokenize(text)
        font = get_font_manager().get_font(
            span.get("font_family"),
            int(span.get("fontsize", 40)),
            bold=bool(span.get("bold", False)),
            italic=bool(span.get("italic", False))
        )
        font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, float(letter_spacing or 0))
        fm = QFontMetrics(font)

        for token in tokens:
            if token == "\n":
                _finalize()
                continue
            is_space = token.isspace()
            if is_space and not curr:
                continue
            token_w = fm.horizontalAdvance(token)
            if curr and not is_space and (curr_w + token_w) > max_width:
                _finalize()
            if is_space and not curr:
                continue
            seg = {
                "text": token,
                "style": span,
                "font": font,
                "fm": fm,
                "width": token_w,
                "is_space": is_space
            }
            curr.append(seg)
            curr_w += token_w
            curr_ascent = max(curr_ascent, fm.ascent())
            curr_descent = max(curr_descent, fm.descent())

    _finalize()
    return lines

def _rich_measure_lines(lines, line_spacing):
    if not lines:
        return 0.0, 0.0
    max_w = max(l.get("width", 0.0) for l in lines)
    total_h = 0.0
    for i, l in enumerate(lines):
        total_h += l.get("ascent", 0.0) + l.get("descent", 0.0)
        if i < len(lines) - 1:
            total_h += float(line_spacing or 0)
    return max_w, total_h

def _draw_rich_lines(painter, lines, start_x, start_y, line_spacing):
    y = start_y
    for li, line in enumerate(lines):
        ascent = line.get("ascent", 0.0)
        baseline = y + ascent
        x = start_x
        for seg in line.get("segments", []):
            text = seg.get("text", "")
            if not text or seg.get("is_space"):
                x += seg.get("width", 0.0)
                continue
            style = seg.get("style", {})
            font = seg.get("font")
            if font is not None:
                painter.setFont(font)
            fm = seg.get("fm") or QFontMetrics(painter.font())

            # Per-span background
            bg_color = style.get("bg_color")
            if bg_color:
                bg_c = QColor(bg_color)
                bg_opacity = int(style.get("bg_opacity", 255))
                bg_c.setAlpha(bg_opacity)
                rect = QRectF(x, baseline - fm.ascent(), seg.get("width", 0.0), fm.height())
                painter.save()
                try:
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.setBrush(bg_c)
                    painter.drawRect(rect)
                finally:
                    painter.restore()

            # Shadow
            if style.get("shadow_enabled", False):
                sh_c = QColor(style.get("shadow_color", "#000000"))
                sh_c.setAlpha(int(style.get("shadow_opacity", 120)))
                sx = float(style.get("shadow_offset_x", 4))
                sy = float(style.get("shadow_offset_y", 4))
                blur = int(style.get("shadow_blur", 0))
                shadow_path = QPainterPath()
                shadow_path.addText(x + sx, baseline + sy, painter.font(), text)
                if blur > 0:
                    steps = max(3, blur)
                    for i in range(steps, 0, -1):
                        frac = i / steps
                        layer_alpha = int(sh_c.alpha() * (1.0 - frac) * 0.15)
                        if layer_alpha < 1:
                            continue
                        sc = QColor(sh_c)
                        sc.setAlpha(layer_alpha)
                        offset = frac * blur * 0.6
                        painter.save()
                        try:
                            painter.setPen(QPen(sc, offset * 2, Qt.PenStyle.SolidLine,
                                                Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
                            painter.setBrush(Qt.BrushStyle.NoBrush)
                            painter.drawPath(shadow_path)
                        finally:
                            painter.restore()
                painter.save()
                try:
                    painter.setPen(QPen(sh_c, 1, Qt.PenStyle.SolidLine,
                                        Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
                    painter.setBrush(Qt.BrushStyle.NoBrush)
                    painter.drawPath(shadow_path)
                finally:
                    painter.restore()

            # Stroke
            if style.get("use_stroke", False) and int(style.get("stroke_width", 0)) > 0:
                stroke_c = QColor(style.get("stroke_color", "#000000"))
                stroke_w = int(style.get("stroke_width", 1))
                path = QPainterPath()
                path.addText(x, baseline, painter.font(), text)
                painter.save()
                try:
                    painter.setPen(QPen(stroke_c, stroke_w, Qt.PenStyle.SolidLine,
                                        Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
                    painter.setBrush(Qt.BrushStyle.NoBrush)
                    painter.drawPath(path)
                finally:
                    painter.restore()

            # Fill
            fill_c = QColor(style.get("color", "#FFFFFF"))
            painter.save()
            try:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(fill_c)
                fill_path = QPainterPath()
                fill_path.addText(x, baseline, painter.font(), text)
                painter.drawPath(fill_path)
            finally:
                painter.restore()

            x += seg.get("width", 0.0)
        y += line.get("ascent", 0.0) + line.get("descent", 0.0)
        if li < len(lines) - 1:
            y += float(line_spacing or 0)

def _fit_font_size_to_rect(text, font_family, base_size, bold, italic, max_w, max_h, min_size=6):
    if not text:
        return int(base_size or 12)
    try:
        max_w = int(max_w)
        max_h = int(max_h)
    except Exception:
        return int(base_size or 12)
    if max_w <= 0 or max_h <= 0:
        return int(base_size or 12)
    size = max(min_size, int(base_size or 12))
    while size > min_size:
        font = get_font_manager().get_font(
            font_family,
            size,
            bold=bool(bold),
            italic=bool(italic)
        )
        lines, max_line_w, total_h, _ = _plain_layout_lines(text, font, max_w)
        if max_line_w <= max_w and total_h <= max_h:
            return size
        size -= 1
    return max(min_size, size)

def _plain_layout_lines(text, font, max_w):
    fm = QFontMetrics(font)
    tokens = _rich_tokenize(text)
    lines = []
    curr = ""
    curr_w = 0.0

    def finalize():
        nonlocal curr, curr_w
        if curr == "" and not lines:
            curr = ""
            curr_w = 0.0
            return
        line_w = fm.horizontalAdvance(curr) if curr else 0.0
        lines.append((curr, line_w))
        curr = ""
        curr_w = 0.0

    for token in tokens:
        if token == "\n":
            finalize()
            continue
        is_space = token.isspace()
        if is_space and curr == "":
            continue
        token_w = fm.horizontalAdvance(token)
        if curr and curr_w + token_w > max_w:
            finalize()
            if is_space:
                continue
        curr += token
        curr_w += token_w
    finalize()
    max_line_w = max((w for _, w in lines), default=0.0)
    total_h = len(lines) * fm.height()
    return lines, max_line_w, total_h, fm

def _doc_layout_lines(text, font, max_w, align=Qt.AlignmentFlag.AlignLeft):
    try:
        doc = QTextDocument()
        doc.setDocumentMargin(0)
        opt = QTextOption()
        opt.setWrapMode(QTextOption.WrapMode.WordWrap)
        opt.setAlignment(align)
        doc.setDefaultTextOption(opt)
        doc.setDefaultFont(font)
        doc.setTextWidth(max_w)
        doc.setPlainText(text or "")

        lines = []
        max_line_w = 0.0
        block = doc.begin()
        while block.isValid():
            layout = block.layout()
            if layout is not None:
                for i in range(layout.lineCount()):
                    line = layout.lineAt(i)
                    text_start = line.textStart()
                    text_len = line.textLength()
                    line_text = block.text()[text_start:text_start + text_len]
                    line_w = line.naturalTextWidth()
                    max_line_w = max(max_line_w, line_w)
                    lines.append({
                        "text": line_text,
                        "x": line.position().x(),
                        "y": line.position().y(),
                        "width": line_w,
                        "ascent": line.ascent(),
                        "height": line.height(),
                    })
            block = block.next()
        total_h = doc.size().height()
        # 兜底：QTextDocument 布局偶发返回空行时，回退到稳定的纯文本分行，保证“必显示”
        if not lines and (text or "") != "":
            lines_raw, max_line_w, total_h, fm = _plain_layout_lines(text or "", font, max_w)
            y = 0.0
            for line_text, line_w in lines_raw:
                lines.append({
                    "text": line_text,
                    "x": 0.0,
                    "y": y,
                    "width": line_w,
                    "ascent": fm.ascent(),
                    "height": fm.height(),
                })
                y += fm.height()
        return lines, max_line_w, total_h
    except Exception:
        lines_raw, max_line_w, total_h, fm = _plain_layout_lines(text or "", font, max_w)
        lines = []
        y = 0.0
        for line_text, line_w in lines_raw:
            lines.append({
                "text": line_text,
                "x": 0.0,
                "y": y,
                "width": line_w,
                "ascent": fm.ascent(),
                "height": fm.height(),
            })
            y += fm.height()
        return lines, max_line_w, total_h

def resegment_words_by_style(all_words, style, video_w):
    if not all_words:
        return []
    try:
        font_size = int(style.get('fontsize', 60))
    except Exception:
        font_size = 60
    max_lines = max(1, int(style.get('wrap_lines', 2)))
    if not isinstance(video_w, (int, float)) or video_w <= 0:
        video_w = 1920
    from cutora.subtitle.stylepack import line_max_width_px
    try:
        fs = float(style.get("fontsize") or font_size)
    except (TypeError, ValueError):
        fs = float(font_size)
    max_width_px = int(line_max_width_px(style, video_w, em=fs))

    if bool(style.get('advanced_textbox_enabled', False)):
        max_width_px = max(80, int(style.get('advanced_textbox_w', max_width_px) or max_width_px))

    # 用像素宽度真实分段，避免渲染阶段出现“省略号截断”
    font = get_font_manager().get_font(
        style.get("font_family"),
        int(font_size),
        bold=bool(style.get('bold', True)),
        italic=bool(style.get('italic', False))
    )
    fm = QFontMetrics(font)

    def pack_event_words(words):
        if not words:
            return None
        text = " ".join([w.get('word', '') for w in words if w.get('word')]).strip()
        return {
            "start": words[0].get("start"),
            "end": words[-1].get("end"),
            "words": words,
            "text": text
        }

    def _split_word_by_px(word_obj):
        """保持单词完整：不再拆分单词。"""
        token = (word_obj.get('word') or '')
        if not token:
            return []
        return [dict(word=token, start=word_obj.get('start'), end=word_obj.get('end'), wid=word_obj.get('wid'))]

    events = []
    current_words = []
    gap_threshold = float(style.get('segment_gap_sec', 0.45))

    def _is_hard_break_token(token: str) -> bool:
        if not token:
            return False
        if re.search(r"[。！？!?；;：:]+$", token):
            return True
        if re.search(r"[\"'”’）)】\]》》】」]$", token):
            return True
        return False

    def _would_fit(next_token: str):
        tokens = [w.get('word', '') for w in current_words if w.get('word')]
        if next_token:
            tokens.append(next_token)
        lines = wrap_tokens_by_pixel_width(tokens, fm, max_width_px, " ") if tokens else []
        return len(lines) <= max_lines
        
    def _deduplicate_words(w_list):
        if not w_list: return []
        w_list.sort(key=lambda x: x.get("start", 0))
        res = [w_list[0]]
        for w in w_list[1:]:
            prev = res[-1]
            t_cur = w.get("start", 0)
            t_prev_end = prev.get("end", 0)
            if t_cur < t_prev_end - 0.05:
                s1 = str(prev.get("word", "")).strip().lower()
                s2 = str(w.get("word", "")).strip().lower()
                import string
                s1_c = s1.translate(str.maketrans('', '', string.punctuation))
                s2_c = s2.translate(str.maketrans('', '', string.punctuation))
                if s1_c and s2_c:
                    if s1_c == s2_c:
                        if len(str(w.get("word", ""))) > len(str(prev.get("word", ""))):
                            res[-1] = w
                        continue
                    if s1_c in s2_c:
                        res[-1] = w
                        continue
                    if s2_c in s1_c:
                        continue
            res.append(w)
        return res

    prev_end = None
    prev_token = None
    for w in _deduplicate_words(all_words):
        for part in _split_word_by_px(w):
            token = part.get('word', '')
            if not token:
                continue

            if current_words and prev_token and _is_hard_break_token(prev_token):
                packed = pack_event_words(current_words)
                if packed:
                    events.append(packed)
                current_words = []

            if current_words and isinstance(prev_end, (int, float)) and isinstance(part.get('start'), (int, float)):
                if part['start'] - prev_end >= gap_threshold:
                    packed = pack_event_words(current_words)
                    if packed:
                        events.append(packed)
                    current_words = []

            if current_words and (not _would_fit(token)):
                packed = pack_event_words(current_words)
                if packed:
                    events.append(packed)
                current_words = []

            current_words.append(part)
            prev_end = part.get('end')
            prev_token = token

    packed = pack_event_words(current_words)
    if packed:
        events.append(packed)
    return events
