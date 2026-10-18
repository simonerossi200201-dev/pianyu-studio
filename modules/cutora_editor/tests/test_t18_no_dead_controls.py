# -*- coding: utf-8 -*-
"""Q18: every visible subtitle-style control must change export pixels.

``test_t18_no_dead_controls`` is allowed to be red in Q18; it measures how
many controls are still dead. T19–T24 must turn it green.
``DEAD_CONTROL_ALLOWLIST`` starts empty. Do not add 'cannot fix yet' entries.
"""
from __future__ import annotations

import copy
import hashlib
import logging
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CANVAS_W, CANVAS_H = 720, 1280
T_S = 0.55
MAGENTA = "#FF00FF"
EVIDENCE_PATH = os.path.join(REPO, "docs", "plan", "reports", "T18_pixel_lock.txt")

# attr list. Empty on purpose (Q18/Q26). A new entry must be explained in the .ok.
DEAD_CONTROL_ALLOWLIST: list[str] = []


def _qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(["t18-dead-controls"])


def _panel():
    import cutora.ui.panels.property  # noqa: F401
    from cutora.ui.panels.subtitle_style import SubtitleStylePanel
    from cutora.ui.panels.fonts import load_embedded_fonts
    load_embedded_fonts()
    return SubtitleStylePanel()


def _fx_section(path: str) -> str:
    if path.startswith("fill."):
        return "配色方案"
    if path.startswith("stroke."):
        return "描边"
    if path.startswith("glow."):
        return "发光"
    if path.startswith("shadow."):
        return "投影"
    if path.startswith("extrude."):
        return "3D 挤出"
    if path.startswith("fonts."):
        return "双字体"
    if path.startswith("emphasis."):
        return "自动着色"
    if path.startswith("follow_word."):
        return "跟随单词"
    if path.startswith("anim."):
        return "动画效果"
    if path.startswith("scatter."):
        return "弹出散落"
    if path in ("tracking_em", "letter_case", "word_mode"):
        return "字幕排版"
    if path.startswith("bg.texture") or path in (
        "bg.texture_tint", "bg.align", "bg.line_gap_em",
    ):
        return "背景纹理"
    if path.startswith("bg."):
        return "背景色块"
    if path.startswith("line_bar."):
        return "跟随句子 · 底条"
    return "其他"


# Visible value widgets that dock_panels places but are not in FX_FIELD_WIDGETS.
# (attr, section, key, kind, evidence)
# kind: fx path / v1 扁平 / DEPRECATED
V1_CONTROLS = (
    ("sub_font", "基本排版", "font_family", "v1 扁平",
     "subtitle_style.py:2186 on_change; spec.py:656 fonts.body 优先, font_family 仅兜底"),
    ("sub_size", "基本排版", "fontsize", "v1 扁平",
     "subtitle_style.py:2118; frame_renderer 读 fontsize, textfx.from_style 不读"),
    ("sub_bold", "基本排版", "bold", "v1 扁平",
     "subtitle_style.py:2201; from_style 不读"),
    ("sub_italic", "基本排版", "italic", "v1 扁平",
     "subtitle_style.py:2202; from_style 不读"),
    ("sub_font_style", "基本排版", "font_style", "v1 扁平",
     "subtitle_style.py:2196; from_style 不读"),
    ("sub_x", "基本排版", "pos_x", "v1 扁平",
     "subtitle_style.py:2119 set_position_px; from_style 不读"),
    ("sub_y", "基本排版", "pos_y", "v1 扁平",
     "subtitle_style.py:2120 set_position_px; from_style 不读"),
    ("sub_rot", "基本排版", "rotation", "v1 扁平",
     "subtitle_style.py:2134; from_style 不读"),
    ("sub_box", "背景色块", "fx.bg.enabled", "fx 路径",
     "subtitle_style.py SectionToggle bg.enabled; raster.py bg.kind()"),
    ("sub_strk", "描边", "fx.stroke.enabled", "fx 路径",
     "subtitle_style.py SectionToggle stroke.enabled; raster.py stroke.live()"),
    ("sub_glow_en", "发光", "fx.glow.enabled", "fx 路径",
     "subtitle_style.py SectionToggle glow.enabled; raster.py glow.live()"),
    ("sub_shadow_en", "投影", "fx.shadow.enabled", "fx 路径",
     "subtitle_style.py SectionToggle shadow.enabled; raster.py enabled and strength > 0"),
    ("sub_dyn", "跟随单词", "fx.follow_word", "fx 路径",
     "subtitle_style.py:2345"),
    ("sub_dyn_tint", "跟随单词", "fx.follow_word.tint.enabled", "fx 路径",
     "subtitle_style.py collect → from_style highlight id"),
    ("sub_dyn_box", "跟随单词", "fx.follow_word.box.enabled", "fx 路径",
     "subtitle_style.py collect → from_style box_highlight id"),
    ("fx_follow_tint_type", "跟随单词", "fx.follow_word.tint.type", "fx 路径",
     "subtitle_style.py mount_follow_word_tint"),
    ("fx_follow_tint_colors", "跟随单词", "fx.follow_word.tint.colors", "fx 路径",
     "subtitle_style.py mount_follow_word_tint"),
    ("fx_follow_tint_angle", "跟随单词", "fx.follow_word.tint.angle", "fx 路径",
     "subtitle_style.py mount_follow_word_tint"),
    ("fx_follow_box_color", "跟随单词", "fx.follow_word.box.color", "fx 路径",
     "subtitle_style.py mount_follow_word_tint"),
    ("sub_dyn_op", "跟随单词", "fx.follow_word.box.alpha", "fx 路径",
     "subtitle_style.py:2357"),
    ("sub_dyn_pad", "跟随单词", "fx.follow_word.box.pad_em", "fx 路径",
     "subtitle_style.py:2358"),
    ("sub_dyn_off_y", "跟随单词", "fx.follow_word.box.offset_y_em", "fx 路径",
     "subtitle_style.py:2359"),
    ("sub_dyn_rad", "跟随单词", "fx.follow_word.box.radius_em", "fx 路径",
     "subtitle_style.py:2360"),
    ("sub_dyn_hollow", "跟随单词", "fx.follow_word.box.stroke_width", "fx 路径",
     "subtitle_style.py:2361"),
    ("sub_dyn_stroke", "跟随单词", "fx.follow_word.box.stroke_width", "fx 路径",
     "subtitle_style.py:2361"),
    ("sub_dyn_glow", "跟随单词", "fx.follow_word.box.glow_radius", "fx 路径",
     "subtitle_style.py:2362"),
    ("sub_adv_bar", "跟随句子 · 底条", "fx.follow_sentence", "fx 路径",
     "subtitle_style.py on_change → fx.follow_sentence"),
    ("sub_wrap_width", "字幕排版", "wrap_width_percent", "v1 扁平",
     "subtitle_style.py wrap_width_percent; line_max_width_px"),
    ("sub_lines", "字幕排版", "wrap_lines", "v1 扁平",
     "subtitle_style.py:2148"),
    ("sub_line_spacing", "字幕排版", "line_spacing", "v1 扁平",
     "subtitle_style.py:2149"),
    ("sub_lyrics_en", "滚动歌词", "lyrics_scroll_enabled", "v1 扁平",
     "subtitle_style.py:2285; 任务书要求不用 lyrics-scroll 包作基准"),
    ("sub_lyrics_damp", "滚动歌词", "lyrics_scroll_damping", "v1 扁平",
     "subtitle_style.py:2286"),
    ("sub_lyrics_trans", "滚动歌词", "lyrics_scroll_transition", "v1 扁平",
     "subtitle_style.py:2287"),
    ("sub_lyrics_scale", "滚动歌词", "lyrics_center_scale", "v1 扁平",
     "subtitle_style.py:2288"),
    ("sub_lyrics_feather", "滚动歌词", "lyrics_edge_feather", "v1 扁平",
     "subtitle_style.py:2290"),
    ("sub_lyrics_gap", "滚动歌词", "lyrics_line_gap", "v1 扁平",
     "subtitle_style.py:2292"),
    ("sub_lyrics_alpha_decay", "滚动歌词", "lyrics_alpha_decay", "v1 扁平",
     "subtitle_style.py:2291"),
    ("sub_lyrics_center_offset", "滚动歌词", "lyrics_center_offset", "v1 扁平",
     "subtitle_style.py:2293"),
    ("sub_lyrics_center_color", "滚动歌词", "lyrics_center_color", "v1 扁平",
     "subtitle_style.py:622 pick_c"),
    ("sub_adv_textbox", "高级文本框", "advanced_textbox_enabled", "v1 扁平",
     "subtitle_style.py:2150"),
    ("sub_adv_x", "高级文本框", "advanced_textbox_x", "v1 扁平",
     "subtitle_style.py:2151"),
    ("sub_adv_y", "高级文本框", "advanced_textbox_y", "v1 扁平",
     "subtitle_style.py:2152"),
    ("sub_adv_w", "高级文本框", "advanced_textbox_w", "v1 扁平",
     "subtitle_style.py:2153"),
    ("sub_adv_h", "高级文本框", "advanced_textbox_h", "v1 扁平",
     "subtitle_style.py:2154"),
    ("sub_adv_align", "高级文本框", "advanced_textbox_align", "v1 扁平",
     "subtitle_style.py:2155"),
    ("sub_adv_line_spacing", "高级文本框", "adv_line_spacing", "v1 扁平",
     "subtitle_style.py:2156"),
    ("sub_adv_text_align", "高级文本框", "adv_text_align", "v1 扁平",
     "subtitle_style.py:2157"),
    ("sub_adv_stroke_en", "高级文本框", "adv_stroke_enabled", "v1 扁平",
     "subtitle_style.py:2162"),
    ("sub_adv_stroke_w", "高级文本框", "adv_stroke_width", "v1 扁平",
     "subtitle_style.py:2163"),
    ("sub_adv_stroke_op", "高级文本框", "adv_stroke_opacity", "v1 扁平",
     "subtitle_style.py:2164"),
    ("sub_adv_stroke_c", "高级文本框", "adv_stroke_color", "v1 扁平",
     "subtitle_style.py:655 pick_c"),
    ("sub_adv_bg_en", "高级文本框", "adv_bg_enabled", "v1 扁平",
     "subtitle_style.py:2165"),
    ("sub_adv_bg_off_x", "高级文本框", "adv_bg_offset_x", "v1 扁平",
     "subtitle_style.py:2166"),
    ("sub_adv_bg_off_y", "高级文本框", "adv_bg_offset_y", "v1 扁平",
     "subtitle_style.py:2167"),
    ("sub_adv_bg_w", "高级文本框", "adv_bg_w", "v1 扁平",
     "subtitle_style.py:2168"),
    ("sub_adv_bg_h", "高级文本框", "adv_bg_h", "v1 扁平",
     "subtitle_style.py:2169"),
    ("sub_adv_bg_rad", "高级文本框", "adv_bg_radius", "v1 扁平",
     "subtitle_style.py:2170"),
    ("sub_adv_bg_op", "高级文本框", "adv_bg_opacity", "v1 扁平",
     "subtitle_style.py:2171"),
    ("sub_adv_bg_color", "高级文本框", "adv_bg_color", "v1 扁平",
     "subtitle_style.py:684 pick_c"),
    ("sub_adv_bg_grad_en", "高级文本框", "adv_bg_gradient_enabled", "v1 扁平",
     "subtitle_style.py:2172"),
    ("sub_adv_bg_grad_type", "高级文本框", "adv_bg_gradient_type", "v1 扁平",
     "subtitle_style.py:2173"),
    ("sub_adv_bg_grad_hl", "高级文本框", "adv_bg_gradient_highlight", "v1 扁平",
     "subtitle_style.py:2174"),
    ("sub_dyn_anim", "跟随单词", "fx.follow_word.box.overshoot", "fx 路径",
     "subtitle_style.py on_change box_highlight.overshoot"),
    ("sub_dyn_anim_overshoot", "跟随单词", "fx.follow_word.box.overshoot", "fx 路径",
     "subtitle_style.py on_change box_highlight.overshoot"),
    ("sub_dyn_anim_dur", "跟随单词", "fx.follow_word.box.duration", "fx 路径",
     "subtitle_style.py on_change box_highlight.duration"),
)


def _catalog():
    from cutora.ui.panels.fx_schema import FX_FIELD_WIDGETS
    rows = []
    seen = set()
    for path, attr in FX_FIELD_WIDGETS.items():
        rows.append((
            attr,
            _fx_section(path),
            "fx." + path,
            "fx 路径",
            "fx_schema.py:FX_FIELD_WIDGETS[%r]; spec.py:642 from_style 读 style['fx']" % path,
        ))
        seen.add(attr)
    for row in V1_CONTROLS:
        if row[0] in seen:
            continue
        rows.append(row)
        seen.add(row[0])
    return rows


def _base_style():
    from cutora.subtitle.stylepack import apply_pack_to_task, builtin_dir, load_pack
    pack = load_pack(os.path.join(builtin_dir(), "glass-white"))
    task = {"style": {}, "segments": []}
    apply_pack_to_task(task, pack, apply_animation=True)
    style = copy.deepcopy(task["style"])
    style["font_family"] = "Arimo"
    style["fontsize"] = 48
    style["pos_x"] = 360
    style["pos_y"] = 900
    style["bold"] = True
    fx = style.setdefault("fx", {})
    if not isinstance(fx, dict):
        fx = {}
        style["fx"] = fx
    fx.setdefault("fill", {})["type"] = "solid"
    fx.setdefault("fill", {})["colors"] = ["#F4F7FB", "#22D3EE"]
    stroke = fx.setdefault("stroke", {})
    if float(stroke.get("width_em") or 0) <= 0:
        stroke["width_em"] = 0.08
        stroke.setdefault("color", "#3A0A14")
    glow = fx.setdefault("glow", {})
    if float(glow.get("sigma_em") or 0) <= 0:
        glow["sigma_em"] = 0.12
        glow.setdefault("color", "#22D3EE")
        glow.setdefault("strength", 0.8)
    shadow = fx.setdefault("shadow", {})
    shadow["light"] = "parallel"
    shadow["angle_deg"] = 45.0
    shadow["distance_em"] = 0.45
    shadow["size_em"] = 0.12
    shadow["falloff"] = 0.2
    shadow["strength"] = 0.7
    shadow.setdefault("color", "#141A26")
    shadow.setdefault("sigma_em", 0.12)
    shadow.setdefault("dx_em", 0.08)
    shadow.setdefault("dy_em", 0.08)
    shadow.setdefault("alpha", 0.55)
    extrude = fx.setdefault("extrude", {})
    if float(extrude.get("depth_em") or 0) <= 0:
        extrude["depth_em"] = 0.12
        extrude.setdefault("color", "#3A0A14")
    fx["follow_word"] = True
    fx["follow_sentence"] = True
    fx.setdefault("box_highlight", {
        "color": "#22D3EE", "alpha": 0.55, "pad_em": 0.12,
        "offset_y_em": 0.0, "radius_em": 0.12,
    })
    fx.setdefault("line_bar", {
        "color": "#F2C14E", "alpha": 0.8, "pad_em": 0.1,
        "offset_y_em": 0.15, "radius_em": 0.08,
    })
    emph = fx.setdefault("emphasis", {})
    emph["words"] = ["PIXEL"]
    emph.setdefault("color", "#E8A838")
    fonts = fx.setdefault("fonts", {})
    fonts["body"] = "Arimo"
    fonts["emphasis"] = "Anton"
    scatter = fx.setdefault("scatter", {})
    scatter["enabled"] = True
    anim = fx.setdefault("anim", {})
    anim.setdefault("seed", 7)
    anim.setdefault("in", "none")
    anim.setdefault("loop", "none")
    anim.setdefault("out", "none")
    anim["in_s"] = 1.2
    anim["out_s"] = 1.2
    return style


def _task(style):
    from cutora.model.timeline import Timeline
    return {
        "style": copy.deepcopy(style),
        "segments": [{
            "text": "Hello PIXEL Lock",
            "start": 0.0,
            "end": 2.0,
            "id": "s0",
            "words": [
                {"word": "Hello", "start": 0.0, "end": 0.4},
                {"word": "PIXEL", "start": 0.4, "end": 0.9},
                {"word": "Lock", "start": 0.9, "end": 1.4},
            ],
        }],
        "timeline": Timeline(CANVAS_W, CANVAS_H, 30),
        "text_overlays": [],
        "image_overlays": [],
    }


def _backdrop():
    from PySide6.QtGui import QColor, QImage, QPainter
    img = QImage(CANVAS_W, CANVAS_H, QImage.Format.Format_ARGB32_Premultiplied)
    p = QPainter(img)
    try:
        for y in range(0, CANVAS_H, 64):
            c = QColor("#2A3A4A") if (y // 64) % 2 == 0 else QColor("#4A5A3A")
            p.fillRect(0, y, CANVAS_W, 64, c)
    finally:
        p.end()
    return img


def _md5(img):
    from PySide6.QtGui import QImage
    import numpy as np
    conv = img.convertToFormat(QImage.Format.Format_ARGB32)
    w, h = conv.width(), conv.height()
    bpl = conv.bytesPerLine()
    raw = bytes(conv.constBits())
    arr = np.frombuffer(raw, dtype=np.uint8).reshape((h, bpl))[:, : w * 4]
    return hashlib.md5(arr.tobytes()).hexdigest()


def _render(style, backdrop, t=None):
    from PySide6.QtGui import QColor
    from cutora.render.export_renderer import ExportRenderer
    task = _task(style)
    when = T_S if t is None else float(t)
    try:
        from cutora.subtitle.stylepack import is_single_word_mode
        if is_single_word_mode(style):
            from cutora.subtitle.layout import maybe_auto_relayout
            maybe_auto_relayout(task, "word_mode")
    except Exception:
        pass

    def _bg(painter):
        painter.fillRect(0, 0, CANVAS_W, CANVAS_H, QColor("#3A4A5A"))

    img = ExportRenderer.render_frame(
        task, CANVAS_W, CANVAS_H, when,
        draw_background=_bg, backdrop=backdrop,
    )
    return _md5(img)


def _nudge_numeric(w):
    lo = w.minimum()
    hi = w.maximum()
    cur = w.value()
    mid = lo + (hi - lo) * 0.5
    if hasattr(w, "setDecimals"):
        target = float(mid)
        if abs(float(cur) - target) < max(0.01, (hi - lo) * 0.05):
            target = float(hi)
        w.setValue(target)
    else:
        target = int(round(mid))
        if target == int(cur):
            target = int(hi) if int(cur) != int(hi) else int(lo)
        w.setValue(target)


def _nudge_combo(w):
    n = w.count()
    if n <= 1:
        return
    i = w.currentIndex()
    for step in range(1, n):
        nxt = (i + step) % n
        if nxt != i:
            w.setCurrentIndex(nxt)
            return


def _set_combo(panel, attr, data):
    combo = getattr(panel, attr, None)
    if combo is None:
        return
    idx = combo.findData(data)
    if idx >= 0:
        combo.setCurrentIndex(idx)


def _set_checked(panel, attr, on=True):
    w = getattr(panel, attr, None)
    if w is None:
        return
    w.setChecked(bool(on))


def _install_color_mock():
    from PySide6.QtGui import QColor
    from PySide6.QtWidgets import QColorDialog
    import cutora.ui.panels.subtitle_style as ss

    def _get(*_a, **_k):
        return QColor(MAGENTA)

    QColorDialog.getColor = staticmethod(_get)
    ss.QColorDialog.getColor = _get


def _nudge_color_button(panel, widget):
    widget.click()


def _nudge(panel, attr, widget):
    from PySide6.QtWidgets import (
        QCheckBox, QComboBox, QDoubleSpinBox, QPlainTextEdit,
        QPushButton, QSlider, QSpinBox,
    )
    from cutora.ui.widgets.collapsible import ToggleSwitch
    from cutora.ui.panels.fx_fields import (
        ColorSwatch, ColorsList, FlagList, PairSpin,
    )
    from cutora.ui.widgets.inspector import SliderSpin
    from cutora.ui.panels.property import ColorOptionPanel
    from cutora.ui.panels.keyword_rules import KeywordRulesWidget

    if isinstance(widget, ColorOptionPanel):
        widget.c1 = MAGENTA
        widget._update_all_colors()
        widget.changed.emit()
        return
    if attr == "fx_bg_texture_tint":
        _set_combo(panel, "fx_bg_type", "texture")
        _set_combo(panel, "fx_bg_texture", "paper")
        widget.set_hex(MAGENTA)
        return
    if attr in ("fx_bg_grad_colors", "fx_bg_grad_highlight"):
        _set_combo(panel, "fx_bg_type", "box")
        _set_combo(panel, "fx_bg_grad_type", "linear_h")
        if attr == "fx_bg_grad_colors":
            widget.set_colors([MAGENTA, "#00FFFF"])
            return
    if attr in ("fx_emphasis_color", "fx_emphasis_palette", "fx_emphasis_auto"):
        _set_checked(panel, "sub_dyn", False)
        _set_checked(panel, "sub_dyn_tint", False)
        words = getattr(panel, "fx_emphasis_words", None)
        if words is not None:
            words.setPlainText("PIXEL")
        if attr == "fx_emphasis_auto":
            widget.set_flags(["caps"])
            widget.changed.emit()
            return
        if isinstance(widget, ColorSwatch):
            widget.set_hex(MAGENTA)
            return
        if isinstance(widget, ColorsList):
            widget.set_colors([MAGENTA, "#00FFFF"])
            return
    if isinstance(widget, ColorSwatch):
        widget.set_hex(MAGENTA)
        return
    if isinstance(widget, ColorsList):
        widget.set_colors([MAGENTA, "#00FFFF"])
        return
    if isinstance(widget, PairSpin):
        hi = widget.a.maximum()
        widget.set_pair([hi, hi])
        return
    if attr == "fx_emphasis_auto":
        widget.set_flags(["caps"])
        widget.changed.emit()
        return
    if isinstance(widget, FlagList):
        flags = list(widget.flags() or [])
        keys = [b.property("flag_key") for b in widget._boxes]
        if not keys:
            return
        pick = keys[0]
        if pick in flags:
            flags = [k for k in flags if k != pick]
        else:
            flags.append(pick)
        widget.set_flags(flags)
        return
    if isinstance(widget, KeywordRulesWidget):
        widget.set_data([{
            "word": "PIXEL", "color1": MAGENTA, "color2": "#00FFFF",
            "use_grad": False, "bold": True,
        }])
        widget.changed.emit()
        return
    if attr in ("sub_italic", "sub_font_style"):
        combo = getattr(panel, "sub_font_style", None)
        if attr == "sub_italic":
            if combo is not None:
                combo.setCurrentIndex(0)
            _set_checked(panel, "sub_bold", False)
            widget.setChecked(True)
            return
        if combo is not None:
            for name in ("Italic", "Bold Italic", "Regular"):
                idx = combo.findData(name)
                if idx >= 0 and idx != combo.currentIndex():
                    combo.setCurrentIndex(idx)
                    return
        _nudge_combo(widget)
        return
    if attr == "sub_dyn_anim":
        _set_checked(panel, "sub_dyn", True)
        _set_checked(panel, "sub_dyn_box", True)
        dur = getattr(panel, "sub_dyn_anim_dur", None)
        if dur is not None:
            dur.setValue(dur.maximum())
        widget.setChecked(not widget.isChecked())
        return
    if isinstance(widget, ToggleSwitch):
        widget.setChecked(not widget.isChecked())
        return
    if isinstance(widget, QCheckBox):
        widget.setChecked(not widget.isChecked())
        return
    if attr in ("sub_italic", "sub_font_style"):
        combo = getattr(panel, "sub_font_style", None)
        if attr == "sub_italic":
            if combo is not None:
                combo.setCurrentIndex(0)
            _set_checked(panel, "sub_bold", False)
            widget.setChecked(True)
            return
        if combo is not None:
            for name in ("Italic", "Bold Italic", "Regular"):
                idx = combo.findData(name)
                if idx >= 0 and idx != combo.currentIndex():
                    combo.setCurrentIndex(idx)
                    return
        _nudge_combo(widget)
        return
    if attr == "fx_bg_glass_sigma_em":
        widget.setValue(widget.maximum())
        return
    if attr == "fx_fonts_emphasis":
        for name in ("Arizonia", "Anton", "Arimo"):
            if widget.findText(name) >= 0 and widget.currentText() != name:
                widget.setCurrentText(name)
                return
        _nudge_combo(widget)
        return
    if attr == "fx_bg_texture_tint":
        _set_combo(panel, "fx_bg_type", "texture")
        _set_combo(panel, "fx_bg_texture", "paper")
        widget.set_hex(MAGENTA)
        return
    if attr in ("fx_bg_grad_colors", "fx_bg_grad_highlight"):
        _set_combo(panel, "fx_bg_type", "box")
        _set_combo(panel, "fx_bg_grad_type", "linear_h")
        if attr == "fx_bg_grad_colors":
            widget.set_colors([MAGENTA, "#00FFFF"])
            return
        colors = getattr(panel, "fx_bg_grad_colors", None)
        if colors is not None:
            colors.set_colors([MAGENTA, "#00FFFF"])
        _nudge_numeric(widget)
        return
    if attr in (
        "fx_emphasis_auto", "fx_emphasis_type", "fx_emphasis_color",
        "fx_emphasis_angle", "fx_emphasis_palette",
    ):
        _set_checked(panel, "sub_dyn", False)
        _set_checked(panel, "sub_dyn_tint", False)
        words = getattr(panel, "fx_emphasis_words", None)
        if words is not None:
            words.setPlainText("PIXEL")
        if attr == "fx_emphasis_auto":
            widget.set_flags(["caps"])
            widget.changed.emit()
            return
        if attr == "fx_emphasis_angle":
            _set_combo(panel, "fx_emphasis_type", "gradient")
        if attr == "fx_emphasis_type":
            _set_combo(panel, "fx_emphasis_type", "gradient")
            return
        if attr == "fx_emphasis_color" and isinstance(widget, ColorSwatch):
            widget.set_hex(MAGENTA)
            return
    if attr in ("fx_anim_in_s", "fx_anim_out_s"):
        if attr == "fx_anim_in_s":
            _set_combo(panel, "fx_anim_in", "fade")
            widget.setValue(1.5)
        else:
            _set_combo(panel, "fx_anim_out", "scale_in")
            widget.setValue(1.5)
        return
    if attr in ("sub_adv_textbox", "sub_adv_x", "sub_adv_y", "sub_adv_w", "sub_adv_h"):
        _set_checked(panel, "sub_adv_textbox", True)
        _set_checked(panel, "sub_adv_bg_en", True)
        box = {
            "sub_adv_x": 40, "sub_adv_y": 160,
            "sub_adv_w": 200, "sub_adv_h": 80,
        }
        for name, val in box.items():
            sp = getattr(panel, name, None)
            if sp is None:
                continue
            if name == attr:
                sp.setValue(val)
            elif getattr(sp, "value", lambda: None)() in (None, 0):
                sp.setValue(val)
        if attr == "sub_adv_textbox":
            return
    if attr in ("sub_dyn_anim", "sub_dyn_anim_overshoot", "sub_dyn_anim_dur"):
        _set_checked(panel, "sub_dyn", True)
        _set_checked(panel, "sub_dyn_box", True)
        dur = getattr(panel, "sub_dyn_anim_dur", None)
        if dur is not None:
            dur.setValue(dur.maximum())
        if attr == "sub_dyn_anim":
            if isinstance(widget, ToggleSwitch):
                widget.setChecked(not widget.isChecked())
            elif isinstance(widget, QCheckBox):
                widget.setChecked(not widget.isChecked())
            else:
                _nudge_numeric(widget)
            return
        if attr == "sub_dyn_anim_dur":
            return
    if attr in ("fx_fill_angle", "fx_emphasis_angle"):
        combo_attr = "fx_fill_type" if attr == "fx_fill_angle" else "fx_emphasis_type"
        _set_combo(panel, combo_attr, "gradient")
        _nudge_numeric(widget)
        return
    if attr in (
        "fx_bg_width_px", "fx_bg_height_px",
        "fx_bg_offset_x", "fx_bg_offset_y",
    ):
        _set_combo(panel, "fx_bg_size_mode", "fixed")
        _nudge_numeric(widget)
        return
    if attr in ("fx_bg_hug_pad_em", "fx_bg_hug_join_em"):
        _set_combo(panel, "fx_bg_size_mode", "hug_glyphs")
        _nudge_numeric(widget)
        return
    if attr == "fx_fill_metal":
        _set_combo(panel, "fx_fill_type", "metal")
        if isinstance(widget, QComboBox):
            _nudge_combo(widget)
        return
    if attr in ("fx_bg_texture", "fx_bg_texture_tint"):
        _set_combo(panel, "fx_bg_type", "texture")
    elif attr in ("fx_bg_align", "fx_bg_line_gap_em"):
        _set_combo(panel, "fx_bg_type", "bubble")
    elif attr in (
        "fx_bg_grad_type", "fx_bg_grad_colors",
        "fx_bg_grad_highlight", "fx_bg_blend",
    ):
        _set_combo(panel, "fx_bg_type", "box")
        if attr != "fx_bg_grad_type":
            _set_combo(panel, "fx_bg_grad_type", "linear_h")
        if attr != "fx_bg_grad_colors":
            colors = getattr(panel, "fx_bg_grad_colors", None)
            if colors is not None:
                colors.set_colors([MAGENTA, "#00FFFF"])
    elif attr == "fx_emphasis_auto":
        widget.set_flags(["caps"])
        widget.changed.emit()
        return
    elif attr == "fx_emphasis_max_per_cue":
        flags = getattr(panel, "fx_emphasis_auto", None)
        if flags is not None:
            flags.set_flags(["caps", "longest"])
            flags.changed.emit()
    elif attr in (
        "fx_emphasis_type", "fx_emphasis_color",
        "fx_emphasis_palette", "fx_emphasis_angle",
    ):
        if attr == "fx_emphasis_angle":
            _set_combo(panel, "fx_emphasis_type", "gradient")
    elif attr in (
        "fx_anim_out", "fx_anim_out_s", "fx_anim_in_s",
        "fx_anim_unit", "fx_anim_stagger_s", "fx_anim_easing",
        "fx_anim_loop",
    ):
        if attr in ("fx_anim_out", "fx_anim_out_s"):
            _set_combo(panel, "fx_anim_out", "fade")
            out_s = getattr(panel, "fx_anim_out_s", None)
            if out_s is not None and attr != "fx_anim_out_s":
                out_s.setValue(out_s.maximum())
        else:
            _set_combo(panel, "fx_anim_in", "fade")
        if attr == "fx_anim_stagger_s":
            _set_combo(panel, "fx_anim_unit", "word")
        if attr == "fx_anim_loop":
            _set_checked(panel, "sub_dyn", False)
            _set_combo(panel, "fx_anim_loop", "float_loop")
            return
    elif attr in (
        "sub_lyrics_damp", "sub_lyrics_trans", "sub_lyrics_scale",
        "sub_lyrics_feather", "sub_lyrics_gap", "sub_lyrics_alpha_decay",
        "sub_lyrics_center_offset", "sub_lyrics_center_color",
    ):
        _set_checked(panel, "sub_lyrics_en", True)
    elif attr in (
        "sub_adv_textbox", "sub_adv_x", "sub_adv_y", "sub_adv_w", "sub_adv_h",
        "sub_adv_align", "sub_adv_line_spacing", "sub_adv_text_align",
        "sub_adv_stroke_en", "sub_adv_stroke_w", "sub_adv_stroke_op",
        "sub_adv_stroke_c", "sub_adv_bg_en", "sub_adv_bg_off_x",
        "sub_adv_bg_off_y", "sub_adv_bg_w", "sub_adv_bg_h", "sub_adv_bg_rad",
        "sub_adv_bg_op", "sub_adv_bg_color", "sub_adv_bg_grad_en",
        "sub_adv_bg_grad_type", "sub_adv_bg_grad_hl",
    ):
        _set_checked(panel, "sub_adv_textbox", True)
        _set_checked(panel, "sub_adv_bg_en", True)
        if attr.startswith("sub_adv_stroke"):
            _set_checked(panel, "sub_adv_stroke_en", True)
        if attr.startswith("sub_adv_bg_grad"):
            _set_checked(panel, "sub_adv_bg_grad_en", True)
        if attr in (
            "sub_adv_textbox", "sub_adv_bg_en",
            "sub_adv_stroke_en", "sub_adv_bg_grad_en",
        ):
            return
    elif attr == "sub_dyn_stroke":
        hollow = getattr(panel, "sub_dyn_hollow", None)
        if hollow is not None and hasattr(hollow, "setValue"):
            if float(hollow.value() or 0) <= 0:
                hollow.setValue(max(hollow.minimum() + 1, 2))
    elif attr in ("sub_lines", "sub_line_spacing"):
        wrap = getattr(panel, "sub_wrap_width", None)
        if wrap is not None:
            wrap.setValue(12)
    elif attr in ("sub_dyn_anim", "sub_dyn_anim_overshoot", "sub_dyn_anim_dur"):
        _set_checked(panel, "sub_dyn", True)
        _set_checked(panel, "sub_dyn_box", True)
    elif attr == "fx_follow_tint_angle":
        _set_combo(panel, "fx_follow_tint_type", "gradient")
        _set_checked(panel, "sub_dyn", True)
        _set_checked(panel, "sub_dyn_tint", True)
    if type(widget).__name__ == "QuadSpin" and getattr(widget, "spins", None):
        _nudge_numeric(widget.spins[0])
        return
    if isinstance(widget, QComboBox):
        _nudge_combo(widget)
        return
    if isinstance(widget, SliderSpin):
        _nudge_numeric(widget)
        return
    if isinstance(widget, (QSpinBox, QDoubleSpinBox, QSlider)):
        if attr == "sub_rot":
            widget.setValue(45 if int(widget.value()) != 45 else -30)
            return
        if attr == "sub_wrap_width":
            widget.setValue(12)
            return
        _nudge_numeric(widget)
        return
    if isinstance(widget, QPlainTextEdit):
        widget.setPlainText("PIXEL\nLOCK")
        return
    if isinstance(widget, QPushButton):
        _nudge_color_button(panel, widget)
        return
    raise TypeError("no nudge for %s %s" % (attr, type(widget).__name__))


def _merge_collect(panel, collected):
    style = copy.deepcopy(panel.current_data or {})
    if not isinstance(collected, dict):
        return style
    fx = collected.get("fx")
    if isinstance(fx, dict):
        style["fx"] = copy.deepcopy(fx)
    for key, value in collected.items():
        if key == "fx":
            continue
        style[key] = copy.deepcopy(value)
    return style


def _write_evidence(rows, n_all, n_dead, n_live, n_skip, n_err):
    os.makedirs(os.path.dirname(EVIDENCE_PATH), exist_ok=True)
    lines = [
        "T18 pixel lock",
        "controls=%d live=%d dead=%d skipped_allowlist=%d errors=%d" % (
            n_all, n_live, n_dead, n_skip, n_err),
        "DEAD_CONTROL_ALLOWLIST=%r" % (DEAD_CONTROL_ALLOWLIST,),
        "",
        "attr\tsection\tkey\tkind\tstatus\tmd5_a\tmd5_b",
    ]
    for r in rows:
        lines.append("\t".join(str(x) for x in r))
    with open(EVIDENCE_PATH, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines) + "\n")


def test_t18_no_dead_controls():
    _qapp()
    _install_color_mock()
    catalog = _catalog()
    backdrop = _backdrop()
    base = _base_style()
    panel = _panel()
    # Baseline must go through the panel. load_data+collect is not a no-op
    # (percent sliders, defaults); comparing to a raw pack hashes every
    # control "live" even when the nudge did nothing.
    panel.load_data(copy.deepcopy(base))
    panel.block_signals = False
    panel.current_data = copy.deepcopy(base)
    md5_a = _render(_merge_collect(panel, panel.collect()), backdrop)
    results = []
    dead = []
    live = []
    skipped = []
    errors = []
    for attr, section, key, kind, evidence in catalog:
        if attr in DEAD_CONTROL_ALLOWLIST:
            skipped.append(attr)
            results.append((attr, section, key, kind, "allowlist", "", ""))
            continue
        style = copy.deepcopy(base)
        panel.load_data(style)
        panel.block_signals = False
        panel.current_data = style
        widget = getattr(panel, attr, None)
        if widget is None:
            errors.append("%s missing" % attr)
            results.append((attr, section, key, kind, "missing", md5_a, ""))
            dead.append(attr)
            continue
        try:
            widget.objectName()
        except RuntimeError:
            errors.append("%s deleted C++ object" % attr)
            results.append((attr, section, key, kind, "deleted", md5_a, ""))
            dead.append(attr)
            continue
        try:
            _nudge(panel, attr, widget)
            collected = panel.collect()
            style_b = _merge_collect(panel, collected)

            if attr == "fx_anim_out_s":
                md5_left = _render(copy.deepcopy(base), backdrop, t=1.5)
                md5_b = _render(style_b, backdrop, t=1.5)
                if md5_left != md5_b:
                    live.append(attr)
                    results.append(
                        (attr, section, key, kind, "live", md5_left, md5_b))
                    continue
            md5_b = _render(style_b, backdrop)
        except Exception as exc:
            errors.append("%s %s" % (attr, exc))
            results.append((attr, section, key, kind, "error:%s" % exc, md5_a, ""))
            dead.append(attr)
            continue
        if md5_a == md5_b:
            dead.append(attr)
            results.append((attr, section, key, kind, "dead", md5_a, md5_b))
        else:
            live.append(attr)
            results.append((attr, section, key, kind, "live", md5_a, md5_b))
    n_all = len(catalog)
    _write_evidence(results, n_all, len(dead), len(live), len(skipped), len(errors))
    panel.close()
    assert not dead, (
        "dead controls %d/%d (allowlist=%d errors=%d). "
        "See %s. dead=%s"
        % (len(dead), n_all, len(skipped), len(errors), EVIDENCE_PATH, dead)
    )


def test_fx_helper_is_loud(caplog, monkeypatch):
    """Missing dock_panels._fx widget must log; CUTORA_STRICT_UI=1 must raise."""
    _qapp()
    from PySide6.QtWidgets import QFormLayout, QWidget
    from cutora.ui.widgets.dock_panels import add_fx_row

    host = QWidget()
    form = QFormLayout(host)

    class _Panel:
        pass

    panel = _Panel()
    monkeypatch.delenv("CUTORA_STRICT_UI", raising=False)
    caplog.set_level(logging.ERROR, logger="cutora.ui.widgets.dock_panels")
    add_fx_row(form, panel, "fx_bg_type", "背景类型:")
    assert any(
        "fx_bg_type" in rec.getMessage() for rec in caplog.records
    ), caplog.text

    monkeypatch.setenv("CUTORA_STRICT_UI", "1")
    try:
        add_fx_row(form, panel, "fx_stroke_color", "描边色:")
        raise AssertionError("CUTORA_STRICT_UI=1 must raise on missing widget")
    except AttributeError as exc:
        assert "fx_stroke_color" in str(exc)
    host.close()
