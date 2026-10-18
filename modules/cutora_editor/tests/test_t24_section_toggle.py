# -*- coding: utf-8 -*-
"""Q24: section-header capsules for fill / extrude / texture / dual-font / scatter / auto-color."""
from __future__ import annotations

import copy
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CANVAS_W, CANVAS_H = 720, 1280

# 有「启用」概念的段必须有胶囊；纯参数段没有。新增段漏加就会红。
SECTION_TOGGLE_RULE = {
    "基本排版": False,
    "配色方案": False,         # Q65: Resolve 填充段没有开关
    "背景色块": True,
    "描边": True,
    "发光": True,
    "投影": True,
    "跟随单词": True,
    "跟随句子 · 底条": True,
    "字幕排版": False,
    "自动着色": True,
    "滚动歌词": True,
    "高级文本框": True,
    "动画效果": True,
    "3D 挤出": True,
    "背景纹理": True,
    "双字体": True,
    "弹出散落": True,
}

# 本任务新加的六段胶囊，必须和已有四段同 objectName / 36×20。
SIX_CAPSULES = {
    "3D 挤出": "sub_extrude_en",
    "背景纹理": "sub_texture_en",
    "双字体": "sub_fonts_en",
    "弹出散落": "fx_scatter_enabled",
    "自动着色": "sub_emphasis_en",
}
EXISTING_FOUR = ("sub_strk", "sub_box", "sub_dyn")


def _qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(["t24-section-toggle"])


def _panel():
    import cutora.ui.panels.property  # noqa: F401
    from cutora.ui.panels.fonts import load_embedded_fonts
    from cutora.ui.panels.subtitle_style import SubtitleStylePanel
    load_embedded_fonts()
    return SubtitleStylePanel()


def _base_style(**fx_extra):
    from cutora.render.textfx.spec import DEFAULT_FILL
    fx = {
        "fill": {"type": "solid", "colors": [DEFAULT_FILL], "angle": 90.0, "metal": "gold"},
        "stroke": {"width_em": 0, "color": "#E11D48", "join": "round"},
        "glow": {"sigma_em": 0, "strength": 0},
        "shadow": {"distance_em": 0, "strength": 0},
        "extrude": {"depth_em": 0, "angle": 135, "color": "#3A0A14"},
        "bg": {"type": "none", "texture": "paper", "texture_tint": "#F1E9D8"},
        "fonts": {"body": "Arimo", "emphasis": ""},
        "emphasis": {
            "auto": [], "words": [], "color": "#22D3EE", "scale": 1.25,
            "font": "emphasis", "anim": "none", "max_per_cue": 2,
        },
        "anim": {"in": "none", "loop": "none", "out": "none", "seed": 7},
        "scatter": {
            "enabled": False, "size_jitter": 0.35,
            "pos_jitter_em": [1.5, 0.8], "rot_jitter_deg": 6.0,
        },
    }
    for key, val in fx_extra.items():
        if isinstance(val, dict) and isinstance(fx.get(key), dict):
            merged = dict(fx[key])
            merged.update(val)
            fx[key] = merged
        else:
            fx[key] = val
    return {
        "font_family": "Arimo",
        "fontsize": 96,
        "bold": True,
        "pos_x": 360,
        "pos_y": 640,
        "fx": fx,
    }


def _paint(style, text="HELLO 12", t=0.55):
    from cutora.render.textfx.layout import layout_event
    from cutora.render.textfx.raster import as_bgra, raster_roi
    from cutora.render.textfx.spec import from_style

    words = text.split()
    step = 0.4
    event = {
        "text": text,
        "start": 0.0,
        "end": 2.0,
        "id": "s0",
        "words": [
            {"word": w, "start": i * step, "end": i * step + step}
            for i, w in enumerate(words)
        ],
    }
    spec = from_style(style)
    lay = layout_event(event, style, spec, CANVAS_W, CANVAS_H)
    img = raster_roi(
        lay, spec, style, None, CANVAS_W, CANVAS_H,
        skip_blur=True, t=t, event=event,
    )
    owned, view = as_bgra(img)
    return owned, view, lay


def _bytes(style, text="HELLO 12"):
    _owned, view, _lay = _paint(style, text=text)
    return view.tobytes()


def _load_and_ready(panel, style):
    panel.load_data(copy.deepcopy(style))
    panel.block_signals = False
    panel.current_data = copy.deepcopy(style)


def _section_content(panel, title):
    from cutora.ui.widgets.dock_panels import CollapsibleSection
    for sec in panel.findChildren(CollapsibleSection):
        if getattr(sec, "section_title", "") == title:
            return sec
    return None


def test_t24_section_toggle_rule():
    """Table of which sections have a header capsule. A new section must be listed."""
    from cutora.ui.widgets.dock_panels import CollapsibleSection

    _qapp()
    panel = _panel()
    found = {}
    for sec in panel.findChildren(CollapsibleSection):
        title = getattr(sec, "section_title", "")
        if not title:
            continue
        found[title] = getattr(sec, "_toggle_cb", None) is not None
    panel.close()
    extra = set(found) - set(SECTION_TOGGLE_RULE)
    missing = set(SECTION_TOGGLE_RULE) - set(found)
    assert not extra, "section not in SECTION_TOGGLE_RULE: %s" % sorted(extra)
    assert not missing, "listed section missing from panel: %s" % sorted(missing)
    for title, want in SECTION_TOGGLE_RULE.items():
        assert found[title] is want, (title, found[title], want)


def test_t24_toggle_roundtrip():
    """On → change value → off → on restores the value from before off, not the default."""
    from cutora.render.textfx.spec import DEFAULT_FILL

    _qapp()
    panel = _panel()

    # Q65: 配色方案 has no capsule; fill is always live and collect does not rewrite it.
    style = _base_style(fill={
        "type": "gradient", "colors": ["#FF0000", "#0000FF"], "angle": 12.0,
    })
    _load_and_ready(panel, style)
    got = panel.collect()
    assert got["fx"]["fill"]["type"] == "gradient"
    assert [c.upper() for c in got["fx"]["fill"]["colors"][:2]] == ["#FF0000", "#0000FF"]
    fill_sec = _section_content(panel, "配色方案")
    assert fill_sec is not None
    from PySide6.QtWidgets import QApplication
    QApplication.processEvents()
    assert getattr(fill_sec, "_toggle_cb", None) is None
    assert fill_sec._content.isEnabled() is True
    _ = DEFAULT_FILL

    # 3D 挤出
    style = _base_style(extrude={"depth_em": 0.22, "angle": 80, "color": "#3A0A14"})
    _load_and_ready(panel, style)
    panel.sub_extrude_en.setChecked(True)
    panel.sub_extrude_en.setChecked(False)
    off = panel.collect()
    assert float(off["fx"]["extrude"]["depth_em"]) == 0, off["fx"]["extrude"]
    panel.sub_extrude_en.setChecked(True)
    on = panel.collect()
    assert abs(float(on["fx"]["extrude"]["depth_em"]) - 0.22) < 1e-6, on["fx"]["extrude"]

    # 背景纹理: start as box, on → texture, change to linen, off → box, on → texture+linen
    style = _base_style(bg={"type": "box", "texture": "paper", "color": "#0B1A33"})
    _load_and_ready(panel, style)
    panel.sub_texture_en.setChecked(True)
    got = panel.collect()
    assert got["fx"]["bg"]["type"] == "texture", got["fx"]["bg"]
    idx = panel.fx_bg_texture.findData("linen")
    assert idx >= 0
    panel.fx_bg_texture.setCurrentIndex(idx)
    panel.sub_texture_en.setChecked(False)
    off = panel.collect()
    assert off["fx"]["bg"]["type"] == "box", off["fx"]["bg"]
    panel.sub_texture_en.setChecked(True)
    on = panel.collect()
    assert on["fx"]["bg"]["type"] == "texture", on["fx"]["bg"]
    assert on["fx"]["bg"]["texture"] == "linen", on["fx"]["bg"]

    # 双字体
    style = _base_style(fonts={"body": "Arimo", "emphasis": "Anton"})
    _load_and_ready(panel, style)
    panel.sub_fonts_en.setChecked(True)
    panel.sub_fonts_en.setChecked(False)
    off = panel.collect()
    assert (off["fx"]["fonts"].get("emphasis") or "") == "", off["fx"]["fonts"]
    panel.sub_fonts_en.setChecked(True)
    on = panel.collect()
    assert on["fx"]["fonts"]["emphasis"] == "Anton", on["fx"]["fonts"]

    # 弹出散落
    style = _base_style(scatter={"enabled": True, "size_jitter": 0.42})
    _load_and_ready(panel, style)
    panel.fx_scatter_enabled.setChecked(True)
    panel.fx_scatter_enabled.setChecked(False)
    off = panel.collect()
    assert off["fx"]["scatter"]["enabled"] is False, off["fx"]["scatter"]
    panel.fx_scatter_enabled.setChecked(True)
    on = panel.collect()
    assert on["fx"]["scatter"]["enabled"] is True, on["fx"]["scatter"]
    assert abs(float(on["fx"]["scatter"]["size_jitter"]) - 0.42) < 1e-6

    # 自动着色
    style = _base_style(emphasis={"auto": ["caps"], "words": ["HELLO", "LOCK"]})
    _load_and_ready(panel, style)
    panel.sub_emphasis_en.setChecked(True)
    panel.sub_emphasis_en.setChecked(False)
    off = panel.collect()
    emph = off["fx"]["emphasis"]
    assert list(emph.get("auto") or []) == [], emph
    assert list(emph.get("words") or []) == [], emph
    panel.sub_emphasis_en.setChecked(True)
    on = panel.collect()
    emph = on["fx"]["emphasis"]
    assert list(emph.get("auto") or []) == ["caps"], emph
    assert list(emph.get("words") or []) == ["HELLO", "LOCK"], emph
    panel.close()


def test_t24_toggle_off_is_noop():
    """Off pixels match a style where that family was never set, byte-for-byte."""
    from cutora.ui.panels.fonts import load_embedded_fonts

    _qapp()
    load_embedded_fonts()
    panel = _panel()

    cases = []

    ext_on = _base_style(extrude={"depth_em": 0.18, "angle": 135, "color": "#3A0A14"})
    ext_never = _base_style()
    ext_never["fx"].pop("extrude", None)
    cases.append(("extrude", "sub_extrude_en", ext_on, ext_never, "HELLO"))

    tex_on = _base_style(bg={
        "type": "texture", "texture": "linen", "texture_tint": "#E4D5C0",
        "alpha": 0.9, "color": "#F1E9D8",
    })
    tex_never = _base_style()
    # never-set bg stays type=none; dropping the key is the same family-off
    tex_never["fx"]["bg"] = {"type": "none"}
    cases.append(("texture", "sub_texture_en", tex_on, tex_never, "HELLO"))

    font_on = _base_style(
        fonts={"body": "Arimo", "emphasis": "Anton"},
        emphasis={"words": ["HELLO"], "auto": [], "color": "#22D3EE", "scale": 1.4},
    )
    font_never = copy.deepcopy(font_on)
    font_never["fx"]["fonts"]["emphasis"] = ""
    cases.append(("fonts", "sub_fonts_en", font_on, font_never, "HELLO WORLD"))

    sc_on = _base_style(scatter={
        "enabled": True, "size_jitter": 0.5,
        "pos_jitter_em": [2.0, 1.2], "rot_jitter_deg": 10.0,
    })
    sc_never = _base_style()
    sc_never["fx"].pop("scatter", None)
    cases.append(("scatter", "fx_scatter_enabled", sc_on, sc_never, "HELLO"))

    em_on = _base_style(emphasis={
        "auto": ["numbers"], "words": ["HELLO"], "color": "#E11D48", "scale": 1.4,
    })
    em_never = _base_style()
    em_never["fx"]["emphasis"]["auto"] = []
    em_never["fx"]["emphasis"]["words"] = []
    cases.append(("emphasis", "sub_emphasis_en", em_on, em_never, "HELLO 12"))

    failed = []
    for name, attr, on_style, never_style, text in cases:
        _load_and_ready(panel, on_style)
        sw = getattr(panel, attr)
        sw.setChecked(True)
        sw.setChecked(False)
        off_style = _merge_style(on_style, panel.collect())
        off_b = _bytes(off_style, text=text)
        never_b = _bytes(never_style, text=text)
        if off_b != never_b:
            failed.append(name)
            continue
        # on must actually differ from never, otherwise the family is a no-op
        on_b = _bytes(on_style, text=text)
        if on_b == never_b:
            failed.append("%s-on-is-also-noop" % name)
    panel.close()
    assert not failed, failed


def _merge_style(base, collected):
    style = copy.deepcopy(base)
    fx = collected.get("fx")
    if isinstance(fx, dict):
        style["fx"] = copy.deepcopy(fx)
    return style


def test_t24_toggle_uniform():
    """Six new capsules share objectName and 36×20 with the existing four."""
    from cutora.ui.widgets.collapsible import ToggleSwitch
    from cutora.ui.widgets.dock_panels import CollapsibleSection

    _qapp()
    panel = _panel()
    names = set()
    sizes = set()
    for title, attr in SIX_CAPSULES.items():
        w = getattr(panel, attr, None)
        assert w is not None, attr
        assert isinstance(w, ToggleSwitch), (attr, type(w))
        assert w.width() == 36 and w.height() == 20, (attr, w.width(), w.height())
        names.add(w.objectName())
        sizes.add((w.width(), w.height()))
        sec = _section_content(panel, title)
        assert sec is not None, title
        assert sec._toggle_cb is w, (title, sec._toggle_cb, w)
    assert len(names) == 1, names
    assert sizes == {(36, 20)}, sizes
    shared = names.pop()
    assert shared, "objectName must be non-empty"
    for attr in EXISTING_FOUR:
        w = getattr(panel, attr)
        assert isinstance(w, ToggleSwitch), attr
        assert w.objectName() == shared, (attr, w.objectName(), shared)
        assert w.width() == 36 and w.height() == 20, attr
    # every capsule-bearing section uses the same widget class
    for sec in panel.findChildren(CollapsibleSection):
        tg = getattr(sec, "_toggle_cb", None)
        if tg is None:
            continue
        if getattr(sec, "section_title", "") in SIX_CAPSULES:
            assert isinstance(tg, ToggleSwitch), sec.section_title
            assert tg.objectName() == shared
    panel.close()
