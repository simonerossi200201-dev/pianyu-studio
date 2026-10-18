# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""v2 fx widgets for SubtitleStylePanel. Values persist into style['fx']."""
from __future__ import annotations

from dataclasses import asdict

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)
from cutora.ui.widgets.collapsible import ToggleSwitch
from cutora.ui.widgets.inspector import (
    ColorStops,
    ColorSwatch,
    EnumCombo,
    InspectForm,
    SliderSpin,
    inspector_row_of,
)
from cutora.ui.widgets.responsive import mark_explicit

from cutora.render.anim_engine import EASING_LABELS
from cutora.render.textfx.spec import from_style
from cutora.ui.i18n import tr
from cutora.ui.theme.tokens import (
    INSPECTOR_FIELD_GAP,
    INSPECTOR_ROW_GAP,
    INSPECTOR_SPIN_WIDTH,
    INSPECTOR_TEXT_H,
)
from cutora.ui.panels.fx_schema import (
    ALIGN_ITEMS,
    BG_GRAD_ITEMS,
    BG_SIZE_MODE_ITEMS,
    BG_TYPE_ITEMS,
    EMPH_ANIM_ITEMS,
    EMPH_AUTO_FLAGS,
    EMPH_FONT_ITEMS,
    EMPH_TYPE_ITEMS,
    FILL_TYPE_ITEMS,
    FX_ANIM_ITEMS,
    FX_FIELD_WIDGETS,
    FX_LABELS,
    GLOW_MODE_ITEMS,
    JOIN_ITEMS,
    SHADOW_LIGHT_ITEMS,
    LETTER_CASE_ITEMS,
    LINE_BAR_ANIM_ITEMS,
    LINE_BAR_STYLE_ITEMS,
    METAL_ITEMS,
    T04F2_DISABLED_ENUM,
    T04F2_DISABLED_PATHS,
    TEXTURE_ITEMS,
    UNIT_ITEMS,
    WORD_MODE_ITEMS,
    FADE_OFFSET_PATHS,
    FADE_PATHS,
    THICK_PATHS,
    fade_offset_to_pct,
    fade_to_pct,
    glass_sigma_to_pct,
    json_to_asdict_path,
    nested_get,
    nested_set,
    pct_to_fade,
    pct_to_fade_offset,
    pct_to_glass_sigma,
    pct_to_thick,
    pct_to_unit,
    schema_paths,
    thick_to_pct,
    unit_to_pct,
)
T04F2_TIP = "T04f-2 启用"
ColorsList = ColorStops


def _fx_label(path, label=None) -> str:
    """Canonical inspector label for an fx path (no trailing colon)."""
    text = FX_LABELS.get(path) or label or path
    return str(text)


class PairSpin(QWidget):
    """Two floats, e.g. pad_em / pos_jitter_em."""
    changed = Signal()

    def __init__(self, lo, hi, step, decimals, parent=None):
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(INSPECTOR_FIELD_GAP)
        self.a = QDoubleSpinBox(self)
        self.b = QDoubleSpinBox(self)
        for sp in (self.a, self.b):
            sp.setRange(lo, hi)
            sp.setSingleStep(step)
            sp.setDecimals(decimals)
            sp.setFixedWidth(INSPECTOR_SPIN_WIDTH)
            mark_explicit(sp, "maximumWidth")
            mark_explicit(sp, "sizePolicy")
            sp.valueChanged.connect(lambda *_a: self.changed.emit())
            row.addWidget(sp)

    def pair(self) -> list[float]:
        return [float(self.a.value()), float(self.b.value())]

    def set_pair(self, values, emit: bool = True) -> None:
        vals = list(values or (0.0, 0.0))
        x = float(vals[0] if len(vals) > 0 else 0.0)
        y = float(vals[1] if len(vals) > 1 else 0.0)
        self.a.blockSignals(True)
        self.b.blockSignals(True)
        self.a.setValue(x)
        self.b.setValue(y)
        self.a.blockSignals(False)
        self.b.blockSignals(False)
        if emit:
            self.changed.emit()

    def blockSignals(self, b: bool) -> None:
        super().blockSignals(b)
        self.a.blockSignals(b)
        self.b.blockSignals(b)


class QuadSpin(QWidget):
    """Q129：四个浮点，左 / 上 / 右 / 下（bg.grow_em）。"""
    changed = Signal()
    NAMES = ("左", "上", "右", "下")

    def __init__(self, lo, hi, step, decimals, parent=None):
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(INSPECTOR_FIELD_GAP)
        self.spins: list[QDoubleSpinBox] = []
        for name in self.NAMES:
            sp = QDoubleSpinBox(self)
            sp.setRange(lo, hi)
            sp.setSingleStep(step)
            sp.setDecimals(decimals)
            sp.setPrefix(tr(name) + " ")
            sp.setToolTip(tr("向%s扩展（em，负值收进去）" % name))
            sp.setMinimumWidth(0)
            mark_explicit(sp, "sizePolicy")
            sp.valueChanged.connect(lambda _v: self.changed.emit())
            row.addWidget(sp, 1)
            self.spins.append(sp)

    def values(self) -> list[float]:
        return [float(sp.value()) for sp in self.spins]

    def set_values(self, values, emit: bool = True) -> None:
        vals = list(values or ())
        for i, sp in enumerate(self.spins):
            v = float(vals[i]) if i < len(vals) else 0.0
            sp.blockSignals(True)
            sp.setValue(v)
            sp.blockSignals(False)
        if emit:
            self.changed.emit()

    def blockSignals(self, b: bool) -> None:
        super().blockSignals(b)
        for sp in self.spins:
            sp.blockSignals(b)


class FlagList(QWidget):
    """Multi-select flags (emphasis.auto)."""
    changed = Signal()

    def __init__(self, items, parent=None):
        super().__init__(parent)
        self._boxes = []
        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(INSPECTOR_ROW_GAP)
        for label, key in items:
            box = QCheckBox(tr(label), self)
            box.setProperty("flag_key", key)
            box.toggled.connect(lambda *_a: self.changed.emit())
            col.addWidget(box)
            self._boxes.append(box)

    def flags(self) -> list[str]:
        return [b.property("flag_key") for b in self._boxes if b.isChecked()]

    def set_flags(self, values, emit: bool = True) -> None:
        want = set(values or [])
        for box in self._boxes:
            box.blockSignals(True)
            box.setChecked(box.property("flag_key") in want)
            box.blockSignals(False)
        if emit:
            self.changed.emit()

    def blockSignals(self, b: bool) -> None:
        super().blockSignals(b)
        for box in self._boxes:
            box.blockSignals(b)


_MISSING = object()


def _on_fx(panel) -> None:
    panel._fx_dirty = True
    _sync_glass_toggle(panel)
    _sync_overlay_bg(panel)
    _sync_fill_mode(panel)
    _sync_emphasis_mode(panel)
    _sync_follow_tint_mode(panel)
    on_change = getattr(panel, "on_change", None)
    if callable(on_change):
        on_change()


def _fx_reset(panel, attr):
    def _go(p=panel, a=attr):
        reset_fx_attr(p, a)
    return _go


def _place(form, label, widget, panel, attr) -> None:
    """Add a row; InspectForm gets a reset handler, QFormLayout does not.

    ``form is None``: widget is created on the panel, layout comes later
    from INSPECTOR_LAYOUT / build_inspector.
    """
    if form is None:
        return
    on_reset = _fx_reset(panel, attr)
    if isinstance(form, InspectForm):
        form.addRow(label, widget, on_reset=on_reset)
        return
    form.addRow(label, widget)


def _t04f2_label(text: str, disabled: bool) -> str:
    return "%s  · %s" % (tr(text), T04F2_TIP) if disabled else tr(text)


def _disable_enum_values(combo: QComboBox, blocked) -> None:
    model = combo.model()
    blocked = set(blocked or ())
    for i in range(combo.count()):
        if combo.itemData(i) in blocked:
            item = model.item(i)
            if item is not None:
                item.setEnabled(False)


def _form_host(form, panel):
    """Layout parent owns the widget. Parenting to panel then addRow to a
    nested form is what deleted ColorsList in the T04f panel test."""
    if form is None:
        return panel
    return form.parentWidget() or panel


def add_enum(panel, attr, items, path, form, label):
    combo = EnumCombo(_form_host(form, panel))
    for lab, val in items:
        combo.addItem(tr(lab), val)
    _disable_enum_values(combo, T04F2_DISABLED_ENUM.get(path))
    combo.currentIndexChanged.connect(lambda _i, p=panel: _on_fx(p))
    combo.setEnabled(path not in T04F2_DISABLED_PATHS)
    if path in T04F2_DISABLED_PATHS:
        combo.setToolTip(T04F2_TIP)
    setattr(panel, attr, combo)
    _place(form, _t04f2_label(_fx_label(path, label), path in T04F2_DISABLED_PATHS), combo, panel, attr)
    return combo


def add_float(panel, attr, path, form, label, lo, hi, step, decimals):
    spin = SliderSpin(lo, hi, step, decimals, parent=_form_host(form, panel))
    spin.valueChanged.connect(lambda _v, p=panel: _on_fx(p))
    spin.setEnabled(path not in T04F2_DISABLED_PATHS)
    if path in T04F2_DISABLED_PATHS:
        spin.setToolTip(T04F2_TIP)
    setattr(panel, attr, spin)
    _place(form, _t04f2_label(_fx_label(path, label), path in T04F2_DISABLED_PATHS), spin, panel, attr)
    return spin


def add_int(panel, attr, path, form, label, lo, hi):
    spin = SliderSpin(lo, hi, 1, 0, parent=_form_host(form, panel))
    spin.valueChanged.connect(lambda _v, p=panel: _on_fx(p))
    setattr(panel, attr, spin)
    _place(form, tr(_fx_label(path, label)), spin, panel, attr)
    return spin


def add_bool(panel, attr, path, form, label):
    box = QCheckBox(tr(_fx_label(path, label)), _form_host(form, panel))
    box.toggled.connect(lambda _v, p=panel: _on_fx(p))
    box.setEnabled(path not in T04F2_DISABLED_PATHS)
    if path in T04F2_DISABLED_PATHS:
        box.setToolTip(T04F2_TIP)
    setattr(panel, attr, box)
    if form is None:
        return box
    if isinstance(form, InspectForm):
        form.addRow(box, on_reset=_fx_reset(panel, attr))
    else:
        form.addRow(box)
    return box


def add_color(panel, attr, path, form, label, chip=False):
    swatch = ColorSwatch(parent=_form_host(form, panel), chip=bool(chip))
    swatch.changed.connect(lambda p=panel: _on_fx(p))
    swatch.setEnabled(path not in T04F2_DISABLED_PATHS)
    if path in T04F2_DISABLED_PATHS:
        swatch.setToolTip(T04F2_TIP)
    setattr(panel, attr, swatch)
    _place(form, _t04f2_label(_fx_label(path, label), path in T04F2_DISABLED_PATHS), swatch, panel, attr)
    return swatch


def add_colors(panel, attr, path, form, label, nmin, nmax):
    w = ColorsList(nmin=nmin, nmax=nmax, parent=_form_host(form, panel))
    w.changed.connect(lambda p=panel: _on_fx(p))
    setattr(panel, attr, w)
    _place(form, tr(_fx_label(path, label)), w, panel, attr)
    return w


def add_pair(panel, attr, path, form, label, lo, hi, step, decimals):
    w = PairSpin(lo, hi, step, decimals, parent=_form_host(form, panel))
    w.changed.connect(lambda p=panel: _on_fx(p))
    w.setEnabled(path not in T04F2_DISABLED_PATHS)
    if path in T04F2_DISABLED_PATHS:
        w.setToolTip(T04F2_TIP)
    setattr(panel, attr, w)
    _place(form, _t04f2_label(_fx_label(path, label), path in T04F2_DISABLED_PATHS), w, panel, attr)
    return w


def add_quad(panel, attr, path, form, label, lo, hi, step, decimals):
    w = QuadSpin(lo, hi, step, decimals, parent=_form_host(form, panel))
    w.changed.connect(lambda p=panel: _on_fx(p))
    setattr(panel, attr, w)
    _place(form, _fx_label(path, label), w, panel, attr)
    return w


def add_header(form, title: str) -> None:
    if form is None:
        return
    lab = QLabel("  " + tr(title))
    lab.setObjectName("sectionHeader")
    form.addRow(lab)


def mount_bg_gradient(panel, form) -> None:
    """v1 bg_gradient_* → fx.bg.grad_*. Dock style must list these attrs too."""
    add_enum(panel, "fx_bg_grad_type", BG_GRAD_ITEMS, "bg.grad_type", form, "色块渐变")
    add_colors(panel, "fx_bg_grad_colors", "bg.grad_colors", form, "渐变色标", 0, 3)
    add_float(panel, "fx_bg_grad_angle", "bg.grad_angle", form, "色块渐变角度", 0, 360, 1, 1)
    add_bool(panel, "fx_bg_grad_highlight", "bg.grad_highlight", form, "渐变高光")
    add_enum(
        panel, "fx_bg_blend",
        [("正常", "normal"), ("正片叠底", "multiply"), ("滤色", "screen"),
         ("叠加", "overlay"), ("柔光", "soft_light")],
        "bg.blend", form, "叠加模式")


def add_pct_slider(panel, attr, path, form, label):
    from cutora.render.textfx.fade import FADE_CAP, FADE_HINT, OFFSET_CAP
    host = _form_host(form, panel)
    if path in FADE_PATHS:
        hi = int(round(FADE_CAP * 100.0))
        ss = SliderSpin(0, hi, 1, 0, parent=host)
        ss.setToolTip(tr("%s（0–%d%%，背景框比例。%s）" % (label, hi, FADE_HINT)))
    elif path in FADE_OFFSET_PATHS:
        hi = int(round(OFFSET_CAP * 100.0))
        ss = SliderSpin(-hi, hi, 1, 0, parent=host)
        ss.setToolTip(tr(
            "%s（−%d%%…+%d%%，把该方向渐变带的中心沿法线平移）" % (label, hi, hi)))
    else:
        ss = SliderSpin(0, 100, 1, 0, parent=host)
        ss.setToolTip(tr("%s（0–100%%，对应 0–1）" % label))
    ss.setSuffix("%")
    ss.valueChanged.connect(lambda _v, p=panel: _on_fx(p))
    setattr(panel, attr, ss)
    setattr(panel, attr + "_spin", ss.spin)
    _place(form, tr(_fx_label(path, label)), ss, panel, attr)
    return ss


def _sync_glass_toggle(panel) -> None:
    """Kept as a no-op hook: glass rows hide via _sync_overlay_bg, not gray."""
    return


_OVERLAY_BG_GLASS = (
    "fx_bg_glass_sigma_em", "fx_bg_glass_border",
    "fx_bg_glass_thickness_em", "fx_bg_glass_refraction",
    "fx_bg_glass_bevel", "fx_bg_glass_sheen", "fx_bg_glass_light_deg",
)
_OVERLAY_BG_TEXTURE = ("fx_bg_texture", "fx_bg_texture_tint")
_OVERLAY_BG_BUBBLE = ("fx_bg_align", "fx_bg_line_gap_em")
_OVERLAY_BG_GRAD = (
    "fx_bg_grad_type", "fx_bg_grad_colors", "fx_bg_grad_angle",
    "fx_bg_grad_highlight", "fx_bg_blend",
)
_OVERLAY_BG_SIZE = ("fx_bg_size_mode",)
_OVERLAY_BG_SIZE_WH = ("fx_bg_width_px", "fx_bg_height_px")
_OVERLAY_BG_OFFSET = ("fx_bg_offset_x", "fx_bg_offset_y")
_OVERLAY_BG_PAD = ("fx_bg_pad_em",)
_OVERLAY_BG_RADIUS = ("fx_bg_radius_em",)
_OVERLAY_BG_HUG_PAD = ("fx_bg_hug_pad_em",)
_OVERLAY_BG_HUG_JOIN = ("fx_bg_hug_join_em",)
_OVERLAY_BG_FADE = (
    "fx_bg_fade_top", "fx_bg_fade_top_offset",
    "fx_bg_fade_bottom", "fx_bg_fade_bottom_offset",
    "fx_bg_fade_left", "fx_bg_fade_left_offset",
    "fx_bg_fade_right", "fx_bg_fade_right_offset",
    "fx_bg_fade_hint",
)


def _mount_glass_knobs(panel, form) -> None:
    """Glass parameters shared by subtitle (enabled/disabled) and overlay (shown/hidden)."""
    host = _form_host(form, panel)
    sigma = SliderSpin(0, 100, 1, 0, parent=host)
    sigma.setSuffix("%")
    sigma.setToolTip(tr("毛玻璃模糊强度（fx.bg.glass_sigma_em，0.2–1.5 em）"))
    sigma.valueChanged.connect(lambda _v, p=panel: _on_fx(p))
    panel.fx_bg_glass_sigma_em = sigma
    panel.fx_bg_glass_sigma_em_spin = sigma.spin
    _place(form, tr(_fx_label("bg.glass_sigma_em", "毛玻璃模糊强度")),
           sigma, panel, "fx_bg_glass_sigma_em")
    add_bool(panel, "fx_bg_glass_border", "bg.glass_border", form, "玻璃反光边")
    add_pct_slider(panel, "fx_bg_glass_thickness_em", "bg.glass_thickness_em",
                   form, "玻璃厚度")
    add_pct_slider(panel, "fx_bg_glass_refraction", "bg.glass_refraction",
                   form, "边缘折射")
    add_pct_slider(panel, "fx_bg_glass_bevel", "bg.glass_bevel",
                   form, "倒角明暗")
    add_pct_slider(panel, "fx_bg_glass_sheen", "bg.glass_sheen",
                   form, "顶面光泽")
    light = SliderSpin(0, 360, 1, 0, parent=host)
    light.setSuffix("°")
    light.setToolTip(tr("光线方向（fx.bg.glass_light_deg，0–360°，315=左上）"))
    light.valueChanged.connect(lambda _v, p=panel: _on_fx(p))
    panel.fx_bg_glass_light_deg = light
    panel.fx_bg_glass_light_deg_spin = light.spin
    _place(form, tr(_fx_label("bg.glass_light_deg", "光线方向")),
           light, panel, "fx_bg_glass_light_deg")


def mount_overlay_bg(panel, form) -> None:
    """Shared background section: 4 always-on rows, the rest hidden by bg.type."""
    add_enum(panel, "fx_bg_type", BG_TYPE_ITEMS, "bg.type", form, "背景类型")
    add_color(panel, "fx_bg_color", "bg.color", form, "背景色")
    add_float(panel, "fx_bg_alpha", "bg.alpha", form, "背景透明度", 0, 1, 0.05, 2)
    add_float(
        panel, "fx_bg_radius_em", "bg.radius_em", form, "圆角 (em)", 0, 1, 0.05, 2)
    add_enum(
        panel, "fx_bg_size_mode", BG_SIZE_MODE_ITEMS, "bg.size_mode",
        form, "尺寸")
    add_int(panel, "fx_bg_width_px", "bg.width_px", form, "宽", 0, 10000)
    add_int(panel, "fx_bg_height_px", "bg.height_px", form, "高", 0, 10000)
    add_int(panel, "fx_bg_offset_x", "bg.offset_x", form, "偏移 X", -5000, 5000)
    add_int(panel, "fx_bg_offset_y", "bg.offset_y", form, "偏移 Y", -5000, 5000)
    add_pair(
        panel, "fx_bg_pad_em", "bg.pad_em", form, "内边距 (em)", 0, 2, 0.05, 2)
    # Q129：四边各自扩展，任何尺寸模式都生效（背景跟着文字走，往框外长）
    from cutora.render.textfx.spec import BG_GROW_EM_MAX, BG_GROW_EM_MIN
    add_quad(
        panel, "fx_bg_grow_em", "bg.grow_em", form, "四边扩展 (em)",
        BG_GROW_EM_MIN, BG_GROW_EM_MAX, 0.05, 2)
    add_float(
        panel, "fx_bg_hug_pad_em", "bg.hug_pad_em", form, "扩展 / 收缩",
        -0.3, 1.0, 0.01, 2)
    add_float(
        panel, "fx_bg_hug_join_em", "bg.hug_join_em", form, "连字",
        0, 0.15, 0.01, 2)
    _mount_glass_knobs(panel, form)
    mount_bg_gradient(panel, form)
    add_enum(panel, "fx_bg_texture", TEXTURE_ITEMS, "bg.texture", form, "纹理")
    add_color(
        panel, "fx_bg_texture_tint", "bg.texture_tint", form, "纹理着色")
    add_enum(panel, "fx_bg_align", ALIGN_ITEMS, "bg.align", form, "气泡对齐")
    add_float(
        panel, "fx_bg_line_gap_em", "bg.line_gap_em", form, "气泡行距 (em)", 0, 1, 0.02, 3)
    add_pct_slider(panel, "fx_bg_fade_top", "bg.fade.top", form, "背景框上渐隐")
    add_pct_slider(panel, "fx_bg_fade_top_offset", "bg.fade.top_offset",
                   form, "背景框上偏移")
    add_pct_slider(panel, "fx_bg_fade_bottom", "bg.fade.bottom", form, "背景框下渐隐")
    add_pct_slider(panel, "fx_bg_fade_bottom_offset", "bg.fade.bottom_offset",
                   form, "背景框下偏移")
    add_pct_slider(panel, "fx_bg_fade_left", "bg.fade.left", form, "背景框左渐隐")
    add_pct_slider(panel, "fx_bg_fade_left_offset", "bg.fade.left_offset",
                   form, "背景框左偏移")
    add_pct_slider(panel, "fx_bg_fade_right", "bg.fade.right", form, "背景框右渐隐")
    add_pct_slider(panel, "fx_bg_fade_right_offset", "bg.fade.right_offset",
                   form, "背景框右偏移")
    from cutora.render.textfx.fade import FADE_HINT
    fade_hint = QLabel(tr(FADE_HINT))
    fade_hint.setObjectName("hint")
    fade_hint.setWordWrap(True)
    if form is not None:
        form.addRow("", fade_hint)
    panel.fx_bg_fade_hint = fade_hint
    panel.fx_bg_type.currentIndexChanged.connect(
        lambda _i, p=panel: _sync_overlay_bg(p))
    _sync_overlay_bg(panel)


def _sync_overlay_bg(panel) -> None:
    combo = getattr(panel, "fx_bg_type", None)
    if combo is None:
        return
    kind = combo.currentData() or "none"
    for attr in _OVERLAY_BG_GLASS:
        _row_visible(getattr(panel, attr, None), kind == "glass")
    for attr in _OVERLAY_BG_TEXTURE:
        _row_visible(getattr(panel, attr, None), kind == "texture")
    for attr in _OVERLAY_BG_BUBBLE:
        _row_visible(getattr(panel, attr, None), kind == "bubble")
    show_extra = kind in ("box", "glass", "texture", "bubble")
    for attr in _OVERLAY_BG_SIZE:
        _row_visible(getattr(panel, attr, None), show_extra)
    size_combo = getattr(panel, "fx_bg_size_mode", None)
    size_mode = size_combo.currentData() if size_combo is not None else "fit_text"
    if size_mode not in ("fit_text", "fixed", "hug_glyphs", "line_hug"):
        size_mode = "fit_text"
    # Pad only sizes the fit_text box. Fixed ignores it.
    # hug_glyphs and line_hug share 扩展/收缩. 连字 is glyphs only.
    # radius_em rounds the rectangle and the line-hug slabs. Under
    # hug_glyphs the mask ignores it, so that row hides and 连字 shows.
    # Type "none" keeps the pad row, matching the old always-on padding line.
    show_pad = (not show_extra) or size_mode == "fit_text"
    show_wh = show_extra and size_mode == "fixed"
    show_off = show_extra and size_mode in ("fixed", "hug_glyphs", "line_hug")
    show_hug_pad = show_extra and size_mode in ("hug_glyphs", "line_hug")
    show_hug_join = show_extra and size_mode == "hug_glyphs"
    show_radius = not (show_extra and size_mode == "hug_glyphs")
    for attr in _OVERLAY_BG_RADIUS:
        _row_visible(getattr(panel, attr, None), show_radius)
    for attr in _OVERLAY_BG_PAD:
        _row_visible(getattr(panel, attr, None), show_pad)
    for attr in _OVERLAY_BG_SIZE_WH:
        _row_visible(getattr(panel, attr, None), show_wh)
    for attr in _OVERLAY_BG_OFFSET:
        _row_visible(getattr(panel, attr, None), show_off)
    for attr in _OVERLAY_BG_HUG_PAD:
        _row_visible(getattr(panel, attr, None), show_hug_pad)
    for attr in _OVERLAY_BG_HUG_JOIN:
        _row_visible(getattr(panel, attr, None), show_hug_join)
    for attr in _OVERLAY_BG_GRAD:
        _row_visible(getattr(panel, attr, None), show_extra)
    for attr in _OVERLAY_BG_FADE:
        _row_visible(getattr(panel, attr, None), show_extra)


def mount_stroke_fx(panel, form) -> None:
    add_float(
        panel, "fx_stroke_width_em", "stroke.width_em", form,
        "描边宽度", 0, 0.3, 0.01, 3)
    add_color(panel, "fx_stroke_color", "stroke.color", form, "描边颜色")
    add_enum(panel, "fx_stroke_join", JOIN_ITEMS, "stroke.join", form, "描边拐角")


def mount_glow_fx(panel, form) -> None:
    add_float(
        panel, "fx_glow_sigma_em", "glow.sigma_em", form, "发光柔度", 0, 0.6, 0.02, 3)
    sigma = getattr(panel, "fx_glow_sigma_em", None)
    if sigma is not None:
        sigma.setToolTip(tr("发光柔度（fx.glow.sigma_em，单位 em）"))
    add_float(
        panel, "fx_glow_spread_em", "glow.spread_em", form, "发光膨胀 (em)", 0, 0.2, 0.01, 3)
    add_color(panel, "fx_glow_color", "glow.color", form, "发光色")
    add_float(
        panel, "fx_glow_strength", "glow.strength", form, "发光强度", 0, 1.5, 0.05, 2)
    add_enum(panel, "fx_glow_mode", GLOW_MODE_ITEMS, "glow.mode", form, "发光模式")


def mount_shadow_fx(panel, form) -> None:
    add_enum(
        panel, "fx_shadow_light", SHADOW_LIGHT_ITEMS, "shadow.light", form, "光源类型")
    add_float(
        panel, "fx_shadow_angle_deg", "shadow.angle_deg", form,
        "光源角度", 0, 360, 1, 1)
    add_float(
        panel, "fx_shadow_distance_em", "shadow.distance_em", form,
        "光源距离", 0, 3, 0.05, 3)
    add_float(
        panel, "fx_shadow_size_em", "shadow.size_em", form,
        "光源大小", 0, 1, 0.02, 3)
    add_float(
        panel, "fx_shadow_falloff", "shadow.falloff", form, "投影衰减", 0, 1, 0.05, 2)
    add_color(panel, "fx_shadow_color", "shadow.color", form, "投影色")
    add_float(
        panel, "fx_shadow_strength", "shadow.strength", form, "投影浓度", 0, 1.5, 0.05, 2)


def mount_glow_shadow_fx(panel, form) -> None:
    mount_glow_fx(panel, form)
    mount_shadow_fx(panel, form)


def mount_line_bar_fx(panel, form) -> None:
    add_color(
        panel, "fx_line_bar_color", "line_bar.color", form, "底条颜色", chip=True)
    add_float(
        panel, "fx_line_bar_height_em", "line_bar.height_em", form,
        "粗细", 0.02, 0.6, 0.01, 3)
    add_float(
        panel, "fx_line_bar_pad_em", "line_bar.pad_em", form,
        "左右扩张", 0, 0.6, 0.01, 3)
    add_float(
        panel, "fx_line_bar_offset_y_em", "line_bar.offset_y_em", form,
        "上下偏移", -0.5, 0.5, 0.01, 3)
    add_float(
        panel, "fx_line_bar_radius_em", "line_bar.radius_em", form,
        "圆角", 0, 0.5, 0.01, 3)
    add_float(
        panel, "fx_line_bar_glow_em", "line_bar.glow_em", form,
        "发光", 0, 0.4, 0.01, 3)
    add_enum(
        panel, "fx_line_bar_style", LINE_BAR_STYLE_ITEMS, "line_bar.style",
        form, "线型")
    add_enum(
        panel, "fx_line_bar_anim", LINE_BAR_ANIM_ITEMS, "line_bar.anim",
        form, "动画")


def mount_layout_fx(panel, form) -> None:
    add_float(
        panel, "fx_tracking_em", "tracking_em", form, "字距 (em)", -0.05, 0.5, 0.01, 3)
    add_enum(
        panel, "fx_letter_case", LETTER_CASE_ITEMS, "letter_case", form, "大小写")


def mount_word_mode(panel, form) -> None:
    combo = EnumCombo(_form_host(form, panel))
    combo.setToolTip(tr(
        "逐词出现 = 一条字幕里的词依次亮起；"
        "单字蹦 = 每个词各自成为一条字幕。两者可以同时开。"))
    for lab, val in WORD_MODE_ITEMS:
        combo.addItem(tr(lab), val)
    combo.currentIndexChanged.connect(lambda _i, p=panel: p.on_change())
    panel.sub_word_mode = combo
    if form is not None:
        form.addRow(tr("每条字幕:"), combo)


def mount_anim_fx(panel, form) -> None:
    from cutora.render.textfx.anim import phase_anim_items
    add_enum(panel, "fx_anim_in", phase_anim_items("in"), "anim.in", form, "入场")
    add_enum(
        panel, "fx_anim_loop", phase_anim_items("loop"), "anim.loop", form, "循环")
    add_enum(panel, "fx_anim_out", phase_anim_items("out"), "anim.out", form, "出场")
    add_enum(panel, "fx_anim_unit", UNIT_ITEMS, "anim.unit", form, "动画单元")
    add_float(
        panel, "fx_anim_stagger_s", "anim.stagger_s", form, "错峰 (s)", 0, 0.3, 0.01, 3)
    add_float(panel, "fx_anim_in_s", "anim.in_s", form, "入场时长 (s)", 0.05, 1.5, 0.05, 2)
    add_float(
        panel, "fx_anim_out_s", "anim.out_s", form, "出场时长 (s)", 0.05, 1.5, 0.05, 2)
    ease = EnumCombo(_form_host(form, panel))
    for lab, key in EASING_LABELS:
        ease.addItem(lab, key)
    ease.currentIndexChanged.connect(lambda _i, p=panel: _on_fx(p))
    panel.fx_anim_easing = ease
    _place(form, tr(_fx_label("anim.easing", "缓动")), ease, panel, "fx_anim_easing")
    add_int(panel, "fx_anim_seed", "anim.seed", form, "随机种子", -10 ** 6, 10 ** 6)


def mount_fill_section(panel, form, *, header: bool = True) -> None:
    if header:
        add_header(form, "填充")
    add_enum(panel, "fx_fill_type", FILL_TYPE_ITEMS, "fill.type", form, "填充类型")
    add_colors(panel, "fx_fill_colors", "fill.colors", form, "字色", 1, 5)
    add_float(panel, "fx_fill_angle", "fill.angle", form, "渐变角度", 0, 360, 1, 1)
    add_enum(panel, "fx_fill_metal", METAL_ITEMS, "fill.metal", form, "金属预设")
    combo = getattr(panel, "fx_fill_type", None)
    if combo is not None:
        combo.currentIndexChanged.connect(lambda _i, p=panel: _sync_fill_mode(p))
    _sync_fill_mode(panel)


def _row_visible(widget, on: bool) -> None:
    if widget is None:
        return
    row = inspector_row_of(widget)
    if row is not None:
        row.setVisible(on)
    widget.setVisible(on)
    parent = widget.parentWidget()
    if parent is None:
        return
    # SubtitleStylePanel stores the top layout as ``self.layout``, which
    # shadows QWidget.layout(). Always go through the QWidget method.
    layout = QWidget.layout(parent)
    if isinstance(layout, QFormLayout):
        lab = layout.labelForField(widget)
        if lab is not None:
            lab.setVisible(on)


def _sync_emphasis_mode(panel) -> None:
    combo = getattr(panel, "fx_emphasis_type", None)
    colors = getattr(panel, "fx_emphasis_color", None)
    angle = getattr(panel, "fx_emphasis_angle", None)
    if combo is None:
        return
    kind = combo.currentData() or "solid"
    if kind == "gradient":
        if isinstance(colors, ColorsList):
            colors.set_limits(2, 5)
            cols = colors.colors()
            if len(cols) < 2:
                pad = list(cols) + ["#22D3EE", "#F4EBD8"]
                colors.set_colors(pad[:2], emit=False)
        _row_visible(colors, True)
        _row_visible(angle, True)
    else:
        if isinstance(colors, ColorsList):
            colors.set_limits(1, 1)
        _row_visible(colors, True)
        _row_visible(angle, False)


def _sync_fill_mode(panel) -> None:
    combo = getattr(panel, "fx_fill_type", None)
    colors = getattr(panel, "fx_fill_colors", None)
    angle = getattr(panel, "fx_fill_angle", None)
    metal = getattr(panel, "fx_fill_metal", None)
    if combo is None:
        return
    kind = combo.currentData() or "solid"
    if kind == "gradient":
        if isinstance(colors, ColorsList):
            colors.set_limits(2, 5)
            cols = colors.colors()
            if len(cols) < 2:
                pad = list(cols) + ["#22D3EE", "#F4EBD8"]
                colors.set_colors(pad[:2], emit=False)
        _row_visible(colors, True)
        _row_visible(angle, True)
        _row_visible(metal, False)
    elif kind == "metal":
        if isinstance(colors, ColorsList):
            colors.set_limits(1, 5)
        _row_visible(colors, False)
        _row_visible(angle, False)
        _row_visible(metal, True)
    else:
        if isinstance(colors, ColorsList):
            colors.set_limits(1, 1)
        _row_visible(colors, True)
        _row_visible(angle, False)
        _row_visible(metal, False)


def mount_extrude_section(panel, form, *, header: bool = True) -> None:
    if header:
        add_header(form, "3D 挤出")
    add_float(
        panel, "fx_extrude_depth_em", "extrude.depth_em", form, "深度 (em)", 0, 0.4, 0.02, 3)
    add_float(
        panel, "fx_extrude_angle", "extrude.angle", form, "挤出角度", 0, 360, 1, 1)
    add_color(panel, "fx_extrude_color", "extrude.color", form, "挤出色")


def mount_texture_section(panel, form, *, header: bool = True) -> None:
    if header:
        add_header(form, "背景纹理")
    if getattr(panel, "fx_bg_texture", None) is None:
        add_enum(panel, "fx_bg_texture", TEXTURE_ITEMS, "bg.texture", form, "纹理")
    if getattr(panel, "fx_bg_texture_tint", None) is None:
        add_color(
            panel, "fx_bg_texture_tint", "bg.texture_tint", form, "纹理着色")
    if getattr(panel, "fx_bg_align", None) is None:
        add_enum(panel, "fx_bg_align", ALIGN_ITEMS, "bg.align", form, "气泡对齐")
    if getattr(panel, "fx_bg_line_gap_em", None) is None:
        add_float(
            panel, "fx_bg_line_gap_em", "bg.line_gap_em", form, "气泡行距 (em)", 0, 1, 0.02, 3)


def mount_fonts_emphasis(panel, form, *, header: bool = True) -> None:
    from cutora.ui.panels.fonts import register_font_combo
    if header:
        add_header(form, "双字体")
    host = _form_host(form, panel)
    # Subtitle inspector: 基本排版 → 字体 is the body face. Reuse that combo
    # so fx.fonts.body and the visible dropdown stay one widget.
    existing = getattr(panel, "sub_font", None)
    if existing is None:
        # Q145：文本层也一样 —— 属性面板顶上的「字体」就是 fx.fonts.body。以前这里
        # 另建一个「正文字体」框：两个框各存各的，画面按 fonts.body 画、字体框显示
        # font_family，一改字号又用那个过期的框把字体写回去。
        existing = getattr(panel, "_fx_body_widget", None)
    if existing is not None:
        panel.fx_fonts_body = existing
    else:
        body = EnumCombo(host)
        register_font_combo(body)
        body.currentTextChanged.connect(lambda _t, p=panel: _on_fx(p))
        panel.fx_fonts_body = body
        _place(form, tr(_fx_label("fonts.body", "正文字体")), body, panel, "fx_fonts_body")
    emph = EnumCombo(host)
    register_font_combo(emph)
    emph.currentTextChanged.connect(lambda _t, p=panel: _on_fx(p))
    panel.fx_fonts_emphasis = emph
    _place(form, tr(_fx_label("fonts.emphasis", "强调字体")), emph, panel, "fx_fonts_emphasis")


def mount_emphasis_section(panel, form, *, header: bool = True,
                           for_subtitle: bool = False) -> None:
    if header:
        add_header(form, "自动着色")
    flags = FlagList(EMPH_AUTO_FLAGS, parent=_form_host(form, panel))
    flags.changed.connect(lambda p=panel: _on_fx(p))
    panel.fx_emphasis_auto = flags
    _place(form, tr(_fx_label("emphasis.auto", "自动强调")),
           flags, panel, "fx_emphasis_auto")
    words = QPlainTextEdit(_form_host(form, panel))
    words.setPlaceholderText(tr("每行一个词"))
    words.setFixedHeight(INSPECTOR_TEXT_H)
    words.textChanged.connect(lambda p=panel: _on_fx(p))
    panel.fx_emphasis_words = words
    _place(form, tr(_fx_label("emphasis.words", "强调词表")),
           words, panel, "fx_emphasis_words")
    if for_subtitle:
        hint_em = QLabel(tr(
            "本段给词表 / 自动强调命中的词上色。颜色用下面的「强调色」，"
            "与「跟随单词 → 当前词字色」互不相干。"))
        hint_em.setObjectName("hint")
        hint_em.setWordWrap(True)
        panel.fx_emphasis_auto_hint = hint_em
        if form is not None:
            form.addRow(hint_em)
    add_enum(panel, "fx_emphasis_type", EMPH_TYPE_ITEMS, "emphasis.type", form, "强调类型")
    add_colors(panel, "fx_emphasis_color", "emphasis.colors", form, "强调色", 1, 5)
    add_float(
        panel, "fx_emphasis_angle", "emphasis.angle", form, "强调角度", 0, 360, 1, 1)
    if for_subtitle:
        hint_c = QLabel(tr(
            "只给关键词表 / 自动强调命中的词上色。正在读的那个词用「跟随单词 → 当前词字色」。"))
        hint_c.setObjectName("hint")
        hint_c.setWordWrap(True)
        panel.fx_emphasis_color_hint = hint_c
        if form is not None:
            form.addRow(hint_c)
    combo_em = getattr(panel, "fx_emphasis_type", None)
    if combo_em is not None:
        combo_em.currentIndexChanged.connect(
            lambda _i, p=panel: _sync_emphasis_mode(p))
    _sync_emphasis_mode(panel)
    add_colors(
        panel, "fx_emphasis_palette", "emphasis.palette", form, "强调调色板", 0, 8)
    add_float(
        panel, "fx_emphasis_scale", "emphasis.scale", form, "强调缩放", 0.8, 2.0, 0.05, 2)
    add_enum(
        panel, "fx_emphasis_font", EMPH_FONT_ITEMS, "emphasis.font", form, "强调用字")
    add_enum(
        panel, "fx_emphasis_anim", EMPH_ANIM_ITEMS, "emphasis.anim", form, "强调动画")
    anim = getattr(panel, "fx_emphasis_anim", None)
    if anim is not None:
        anim.setToolTip(tr(
            "弹出：词级放大回弹。划线：强调词下方 0.08 em 画线，180 ms 从左到右"))
    add_int(
        panel, "fx_emphasis_max_per_cue", "emphasis.max_per_cue", form,
        "每条上限:", 0, 8)
    mx = getattr(panel, "fx_emphasis_max_per_cue", None)
    if mx is not None:
        mx.setToolTip(tr("自动强调每条最多几个（0=不限）。词表命中不占额度。"))


def _sync_follow_tint_mode(panel) -> None:
    combo = getattr(panel, "fx_follow_tint_type", None)
    colors = getattr(panel, "fx_follow_tint_colors", None)
    angle = getattr(panel, "fx_follow_tint_angle", None)
    if combo is None:
        return
    kind = combo.currentData() or "solid"
    if kind == "gradient":
        if isinstance(colors, ColorsList):
            colors.set_limits(2, 5)
            cols = colors.colors()
            if len(cols) < 2:
                pad = list(cols) + ["#22D3EE", "#F4EBD8"]
                colors.set_colors(pad[:2], emit=False)
        _row_visible(colors, True)
        _row_visible(angle, True)
    else:
        if isinstance(colors, ColorsList):
            colors.set_limits(1, 1)
        _row_visible(colors, True)
        _row_visible(angle, False)


def mount_follow_word_tint(panel, form) -> None:
    """Current-word glyph color. Lives in 跟随单词, not 配色方案 / 自动着色."""
    add_enum(
        panel, "fx_follow_tint_type", EMPH_TYPE_ITEMS,
        "follow_word.tint.type", form, "类型")
    add_colors(
        panel, "fx_follow_tint_colors", "follow_word.tint.colors",
        form, "跟随字色", 1, 5)
    add_float(
        panel, "fx_follow_tint_angle", "follow_word.tint.angle",
        form, "角度", 0, 360, 1, 1)
    add_color(
        panel, "fx_follow_box_color", "follow_word.box.color",
        form, "底块颜色")
    combo = getattr(panel, "fx_follow_tint_type", None)
    if combo is not None:
        combo.currentIndexChanged.connect(
            lambda _i, p=panel: _sync_follow_tint_mode(p))
    _sync_follow_tint_mode(panel)


def mount_scatter_section(panel, form, *, header: bool = True) -> None:
    if header:
        add_header(form, "弹出散落")
    if getattr(panel, "fx_scatter_enabled", None) is None:
        add_bool(panel, "fx_scatter_enabled", "scatter.enabled", form, "随机大小/位置")
    add_float(
        panel, "fx_scatter_size_jitter", "scatter.size_jitter", form,
        "字号抖动", 0, 0.6, 0.05, 2)
    add_pair(
        panel, "fx_scatter_pos_jitter_em", "scatter.pos_jitter_em", form,
        "位置抖动 (em)", 0, 8, 0.1, 2)
    add_float(
        panel, "fx_scatter_rot_jitter_deg", "scatter.rot_jitter_deg", form,
        "旋转抖动 (°)", 0, 20, 0.5, 1)


def mount_late_sections(panel, form) -> None:
    mount_fill_section(panel, form)
    mount_extrude_section(panel, form)
    mount_texture_section(panel, form)
    mount_fonts_emphasis(panel, form)
    mount_emphasis_section(panel, form, for_subtitle=True)
    mount_scatter_section(panel, form)


def iter_fx_widgets(panel):
    seen = set()
    for attr in FX_FIELD_WIDGETS.values():
        w = getattr(panel, attr, None)
        if w is not None and id(w) not in seen:
            seen.add(id(w))
            yield w


def _set_combo_data(combo: QComboBox, value) -> None:
    idx = combo.findData(value)
    if idx < 0:
        combo.addItem(str(value), value)
        idx = combo.findData(value)
    if idx >= 0:
        combo.setCurrentIndex(idx)


def set_widget_value(panel, path: str, value) -> None:
    """Set a registered widget. Does not emit on_change (caller blocks)."""
    attr = FX_FIELD_WIDGETS.get(path)
    if not attr:
        return
    w = getattr(panel, attr, None)
    if w is None:
        return
    if path == "bg.glass_sigma_em":
        w.blockSignals(True)
        w.setValue(glass_sigma_to_pct(value if value is not None else 0.7))
        w.blockSignals(False)
        return
    if path in FADE_PATHS:
        w.blockSignals(True)
        w.setValue(fade_to_pct(value if value is not None else 0.0))
        w.blockSignals(False)
        return
    if path in FADE_OFFSET_PATHS:
        w.blockSignals(True)
        w.setValue(fade_offset_to_pct(value if value is not None else 0.0))
        w.blockSignals(False)
        return
    if path in THICK_PATHS:
        w.blockSignals(True)
        w.setValue(unit_to_pct(value if value is not None else 0.0))
        w.blockSignals(False)
        return
    if path == "bg.glass_thickness_em":
        w.blockSignals(True)
        w.setValue(thick_to_pct(value if value is not None else 0.14))
        w.blockSignals(False)
        return
    if path == "bg.glass_light_deg":
        w.blockSignals(True)
        w.setValue(int(round(float(value if value is not None else 315))))
        w.blockSignals(False)
        return
    if path == "word_mode":
        raw = str(value or "").strip().lower()
        key = "single" if raw in ("single", "word", "one") else ""
        w.blockSignals(True)
        _set_combo_data(w, key)
        w.blockSignals(False)
        return
    if path in ("fonts.body", "fonts.emphasis"):
        text = str(value or "")
        if (path == "fonts.body" and not text
                and w is getattr(panel, "_fx_body_widget", None)):
            return      # 没有单独的正文字体 = 用本层字体，框里已经是它了
        w.blockSignals(True)
        if text:
            w.setCurrentText(text)
        elif path == "fonts.emphasis":
            body = getattr(panel, "fx_fonts_body", None)
            follow = body.currentText() if body is not None else ""
            if follow and w.findText(follow) >= 0:
                w.setCurrentText(follow)
            elif w.count() > 0:
                w.setCurrentIndex(0)
        elif w.count() > 0:
            w.setCurrentIndex(0)
        w.blockSignals(False)
        return
    if isinstance(w, QComboBox):
        w.blockSignals(True)
        _set_combo_data(w, value)
        w.blockSignals(False)
        if path == "fill.type":
            _sync_fill_mode(panel)
        if path == "emphasis.type":
            _sync_emphasis_mode(panel)
        if path == "follow_word.tint.type":
            _sync_follow_tint_mode(panel)
    elif isinstance(w, ColorSwatch):
        w.set_hex("" if value in (None, "") else value, emit=False)
    elif isinstance(w, ColorsList):
        w.set_colors(value or [], emit=False)
    elif isinstance(w, QuadSpin):
        w.set_values(value, emit=False)
    elif isinstance(w, PairSpin):
        w.set_pair(value, emit=False)
    elif isinstance(w, FlagList):
        w.set_flags(value, emit=False)
    elif isinstance(w, QPlainTextEdit):
        w.blockSignals(True)
        w.setPlainText("\n".join(str(x) for x in (value or [])))
        w.blockSignals(False)
    elif isinstance(w, (QCheckBox, ToggleSwitch)):
        w.blockSignals(True)
        w.setChecked(bool(value))
        w.blockSignals(False)
    elif isinstance(w, (SliderSpin, QDoubleSpinBox, QSpinBox, QSlider)):
        w.blockSignals(True)
        if value is None:
            value = 0
        w.setValue(value)
        w.blockSignals(False)


def get_widget_value(panel, path: str):
    attr = FX_FIELD_WIDGETS.get(path)
    if not attr:
        return _MISSING
    w = getattr(panel, attr, None)
    if w is None:
        return _MISSING
    if path == "bg.glass_sigma_em":
        return pct_to_glass_sigma(w.value())
    if path in FADE_PATHS:
        return pct_to_fade(w.value())
    if path in FADE_OFFSET_PATHS:
        return pct_to_fade_offset(w.value())
    if path in THICK_PATHS:
        return pct_to_unit(w.value())
    if path == "bg.glass_thickness_em":
        return pct_to_thick(w.value())
    if path == "word_mode":
        return w.currentData() or ""
    if path in ("fonts.body", "fonts.emphasis"):
        return w.currentText()
    if isinstance(w, QComboBox):
        return w.currentData()
    if isinstance(w, ColorSwatch):
        return w.hex()
    if isinstance(w, ColorsList):
        return w.colors()
    if isinstance(w, QuadSpin):
        return w.values()
    if isinstance(w, PairSpin):
        return w.pair()
    if isinstance(w, FlagList):
        return w.flags()
    if isinstance(w, QPlainTextEdit):
        return [ln.strip() for ln in w.toPlainText().splitlines() if ln.strip()]
    if isinstance(w, (QCheckBox, ToggleSwitch)):
        return bool(w.isChecked())
    if isinstance(w, SliderSpin):
        return float(w.value()) if getattr(w, "_decimals", 0) else int(w.value())
    if isinstance(w, (QDoubleSpinBox, QSlider)):
        return float(w.value())
    if isinstance(w, QSpinBox):
        return int(w.value())
    return None


def load_into_panel(panel, style: dict) -> None:
    spec = from_style(style)
    payload = asdict(spec)
    # Mode keys first: schema_paths() is a set; fill.type must precede fill.colors.
    def _path_order(path: str) -> tuple:
        tail = path.rsplit(".", 1)[-1]
        return (0 if tail in ("type", "light", "mode") else 1, path)

    for path in sorted(schema_paths(), key=_path_order):
        dc_path = json_to_asdict_path(path)
        set_widget_value(panel, path, nested_get(payload, dc_path))
    _sync_glass_toggle(panel)
    _sync_overlay_bg(panel)
    _sync_fill_mode(panel)
    _sync_emphasis_mode(panel)
    _sync_follow_tint_mode(panel)
    mode = ""
    if isinstance(style, dict):
        raw = str(style.get("word_mode") or "").strip().lower()
        if raw in ("single", "word", "one"):
            mode = "single"
    set_widget_value(panel, "word_mode", mode)


def save_from_panel(panel, style: dict) -> None:
    if not getattr(panel, "_fx_dirty", False) and not isinstance(style.get("fx"), dict):
        _save_word_mode(panel, style)
        return
    prev = style.get("fx") if isinstance(style.get("fx"), dict) else {}
    fx: dict = {}
    for path in schema_paths():
        val = get_widget_value(panel, path)
        if val is _MISSING:
            continue
        nested_set(fx, path, val)
    for key, value in prev.items():
        if key not in fx:
            fx[key] = value
    prev_shadow = prev.get("shadow")
    cur_shadow = fx.get("shadow")
    if isinstance(prev_shadow, dict) and isinstance(cur_shadow, dict):
        from cutora.render.textfx.spec import SHADOW_OLD_KEYS
        retired = SHADOW_OLD_KEYS - {"color"}
        for key, value in prev_shadow.items():
            if key in retired:
                continue
            if key not in cur_shadow:
                cur_shadow[key] = value
        for key in retired:
            cur_shadow.pop(key, None)
    for family in ("stroke", "glow", "bg"):
        prev_d = prev.get(family)
        cur_d = fx.get(family)
        if isinstance(prev_d, dict) and isinstance(cur_d, dict):
            if "enabled" in prev_d and "enabled" not in cur_d:
                cur_d["enabled"] = prev_d["enabled"]
    emph = fx.get("emphasis")
    if isinstance(emph, dict):
        cols = emph.get("colors")
        if isinstance(cols, list) and cols:
            emph["color"] = cols[0]
    style["fx"] = fx
    _save_word_mode(panel, style)


def _save_word_mode(panel, style: dict) -> None:
    mode = get_widget_value(panel, "word_mode")
    if mode is _MISSING:
        return
    mode = mode or ""
    if mode:
        style["word_mode"] = "single"
    else:
        style.pop("word_mode", None)


def apply_widget_sample(panel, path: str, value) -> None:
    """Unblock, set, mark dirty, write through on_change."""
    set_widget_value(panel, path, value)
    if path == "word_mode":
        panel.on_change()
        return
    panel._fx_dirty = True
    panel.on_change()


_SPEC_DEFAULTS = None


def fx_default(path: str):
    """Product default for an fx JSON path (from_style({}))."""
    global _SPEC_DEFAULTS
    if _SPEC_DEFAULTS is None:
        _SPEC_DEFAULTS = asdict(from_style({}))
    return nested_get(_SPEC_DEFAULTS, json_to_asdict_path(path))


def reset_fx_attr(panel, attr: str) -> None:
    """Restore one registered widget to the product default.

    Subtitle panels use ``fx_default`` (empty style). A property panel can
    override with ``fx_reset_value(path)`` so a text layer returns to the
    creation overlay instead. Returns ``_MISSING`` to keep ``fx_default``.
    """
    path = None
    for pth, name in FX_FIELD_WIDGETS.items():
        if name == attr:
            path = pth
            break
    if path is None:
        from cutora.core.log import get_logger
        get_logger("cutora.ui.panels.fx_fields").warning(
            "reset_fx_attr: no path for %r", attr)
        return
    default = fx_default(path)
    custom = getattr(panel, "fx_reset_value", None)
    if callable(custom):
        alt = custom(path)
        if alt is not _MISSING:
            default = alt
    opener = getattr(panel, "_open_reset_undo", None)
    closer = getattr(panel, "_close_reset_undo", None)
    opened = False
    if callable(opener) and not getattr(panel, "_reset_undo_open", False):
        opened = bool(opener("重置"))
    try:
        set_widget_value(panel, path, default)
        _on_fx(panel)
    finally:
        if opened and callable(closer):
            closer()
