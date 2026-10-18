# -*- coding: utf-8 -*-
"""Q50: declarative inspector layout. No reparent-after-the-fact."""
from __future__ import annotations

import ast
import copy
import hashlib
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CUTORA = os.path.join(REPO, "cutora")
CANVAS_W, CANVAS_H = 720, 1280
T_S = 0.55
TEXT = "HELLO WORLD"


def _qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(["q50-inspector"])


def _panel():
    import cutora.ui.panels.property  # noqa: F401
    from cutora.ui.panels.fonts import load_embedded_fonts
    from cutora.ui.panels.subtitle_style import SubtitleStylePanel
    load_embedded_fonts()
    return SubtitleStylePanel()


def _iter_py(root):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in ("__pycache__",)]
        for name in filenames:
            if name.endswith(".py"):
                yield os.path.join(dirpath, name)


def _is_descendant(widget, ancestor):
    cur = widget
    while cur is not None:
        if cur is ancestor:
            return True
        cur = cur.parentWidget()
    return False


def test_inspector_layout_complete():
    """Every sub_*/fx_* widget is in INSPECTOR_LAYOUT exactly once.

    'In the table' means listed as a Row.attr / extra / Section.toggle, or a
    descendant of a listed widget (group boxes that own nested controls).
    Listed twice, or created but never placed, is a fail — that is the old
    apply_dock_style deleteLater class of bug.
    """
    from PySide6.QtWidgets import QWidget
    from cutora.ui.panels.subtitle_layout import INSPECTOR_LAYOUT
    from cutora.ui.widgets.inspector import layout_attr_names

    _qapp()
    panel = _panel()
    names = layout_attr_names(INSPECTOR_LAYOUT)
    assert names, "INSPECTOR_LAYOUT is empty"
    dupes = sorted({n for n in names if names.count(n) > 1})
    assert not dupes, "attr listed more than once: %s" % dupes

    listed = []
    missing_listed = []
    for name in names:
        w = getattr(panel, name, None)
        if w is None:
            if name == "chk_detached":
                continue
            missing_listed.append(name)
            continue
        listed.append(w)
    assert not missing_listed, "table names missing on panel: %s" % missing_listed

    orphans = []
    double = []
    names_set = set(names)
    for name, w in list(panel.__dict__.items()):
        if not isinstance(w, QWidget):
            continue
        if name.startswith("_") and name not in names_set:
            continue
        owners = [owner for owner in listed if _is_descendant(w, owner)]
        if not owners:
            orphans.append(name)
        elif len(owners) > 1 and w not in listed:
            # A nested widget owned by two listed ancestors.
            double.append(name)
    panel.close()
    assert not orphans, "QWidget attrs not in INSPECTOR_LAYOUT: %s" % orphans
    assert not double, "QWidget attrs owned by two listed ancestors: %s" % double


def test_one_section_class():
    """One CollapsibleSection class; live sections use one arrow pair."""
    from cutora.ui.widgets.collapsible import (
        ARROW_COLLAPSED,
        ARROW_EXPANDED,
        CollapsibleSection,
    )

    classes = []
    for path in _iter_py(CUTORA):
        rel = os.path.relpath(path, REPO).replace("\\", "/")
        if "/build/" in "/" + rel.replace("\\", "/"):
            continue
        src = open(path, encoding="utf-8").read()
        tree = ast.parse(src, filename=rel)
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == "CollapsibleSection":
                classes.append("%s:%d" % (rel, node.lineno))
    assert len(classes) == 1, classes
    assert classes[0].replace("\\", "/").startswith("cutora/ui/widgets/collapsible.py:"), classes

    _qapp()
    panel = _panel()
    sections = panel.findChildren(CollapsibleSection)
    assert sections, "no CollapsibleSection on subtitle panel"
    arrows = set()
    for sec in sections:
        text = sec._arrow.text()
        arrows.add(text)
        assert text in (ARROW_COLLAPSED, ARROW_EXPANDED), (sec.section_title, text)
    panel.close()
    assert ARROW_COLLAPSED in arrows, arrows
    assert ARROW_EXPANDED in arrows, arrows
    assert "▶" not in arrows, arrows


def test_all_toggles_are_sections():
    """subtitle_style.py no longer builds labeled_toggle hosts."""
    from cutora.ui.widgets.collapsible import ToggleSwitch, labeled_toggle  # noqa: F401

    src = open(
        os.path.join(CUTORA, "ui", "panels", "subtitle_style.py"),
        encoding="utf-8",
    ).read()
    assert "labeled_toggle(" not in src
    assert "from cutora.ui.widgets.collapsible import labeled_toggle" not in src

    _qapp()
    panel = _panel()
    from cutora.ui.widgets.collapsible import CollapsibleSection
    toggles = {
        "sub_box": "背景色块",
        "sub_strk": "描边",
        "sub_glow_en": "发光",
        "sub_shadow_en": "投影",
        "sub_dyn": "跟随单词",
        "sub_adv_bar": "跟随句子 · 底条",
        "sub_emphasis_en": "自动着色",
        "sub_lyrics_en": "滚动歌词",
        "sub_adv_textbox": "高级文本框",
        "fx_anim_en": "动画效果",
        "sub_extrude_en": "3D 挤出",
        "sub_texture_en": "背景纹理",
        "sub_fonts_en": "双字体",
        "fx_scatter_enabled": "弹出散落",
    }
    found = {
        getattr(sec, "section_title", ""): getattr(sec, "_toggle_cb", None)
        for sec in panel.findChildren(CollapsibleSection)
    }
    missing = []
    for attr, title in toggles.items():
        w = getattr(panel, attr, None)
        assert isinstance(w, ToggleSwitch), (attr, type(w))
        cb = found.get(title)
        if cb is not w:
            missing.append((title, attr, cb is None))
    panel.close()
    assert not missing, missing


def _event(text=TEXT):
    words = text.split()
    step = 0.4
    return {
        "text": text,
        "start": 0.0,
        "end": 2.0,
        "id": "s0",
        "words": [
            {"word": w, "start": i * step, "end": i * step + step}
            for i, w in enumerate(words)
        ],
    }


def _task(style):
    from cutora.model.timeline import Timeline
    return {
        "style": copy.deepcopy(style),
        "segments": [_event()],
        "timeline": Timeline(CANVAS_W, CANVAS_H, 30),
        "text_overlays": [],
        "image_overlays": [],
    }


def _preview_bytes(style):
    from cutora.render.textfx.layout import layout_event
    from cutora.render.textfx.raster import as_bgra, raster_roi
    from cutora.render.textfx.spec import from_style
    from cutora.ui.panels.fonts import load_embedded_fonts

    load_embedded_fonts()
    spec = from_style(style)
    ev = _event()
    lay = layout_event(ev, style, spec, CANVAS_W, CANVAS_H)
    img = raster_roi(
        lay, spec, style, None, CANVAS_W, CANVAS_H,
        skip_blur=True, t=T_S, event=ev,
    )
    _owned, view = as_bgra(img)
    return view.tobytes()


def _export_bytes(style):
    from PySide6.QtGui import QColor, QImage
    from cutora.render.export_renderer import ExportRenderer

    def _bg(painter):
        painter.fillRect(0, 0, CANVAS_W, CANVAS_H, QColor("#1A1A2A"))

    img = ExportRenderer.render_frame(
        _task(style), CANVAS_W, CANVAS_H, T_S, draw_background=_bg,
    )
    conv = img.convertToFormat(QImage.Format.Format_ARGB32)
    raw = bytes(conv.constBits())
    return hashlib.md5(raw).hexdigest(), raw


def _default_style():
    from cutora.render.textfx.spec import DEFAULT_FILL
    return {
        "font_family": "Arimo",
        "fontsize": 96,
        "bold": True,
        "pos_x": 360,
        "pos_y": 640,
        "fx": {
            "fill": {"type": "solid", "colors": [DEFAULT_FILL], "angle": 90.0},
            "stroke": {"width_em": 0, "color": "#E11D48", "join": "round"},
            "glow": {"sigma_em": 0, "strength": 0},
            "shadow": {"enabled": False},
            "bg": {"type": "none"},
            "anim": {"in": "none", "loop": "none", "out": "none"},
            "emphasis": {"auto": [], "words": [], "color": "#22D3EE"},
        },
    }


def test_fill_toggle_does_not_change_color():
    """配色方案 has no UI lock; collect does not rewrite fill / pixels."""
    from PySide6.QtWidgets import QApplication
    from cutora.ui.panels.fonts import load_embedded_fonts
    from cutora.render.textfx.spec import DEFAULT_FILL

    _qapp()
    load_embedded_fonts()
    panel = _panel()
    style = _default_style()
    panel.load_data(copy.deepcopy(style))
    panel.block_signals = False
    panel.current_data = copy.deepcopy(style)

    before_preview = _preview_bytes(style)
    before_export_md5, before_export = _export_bytes(style)

    assert getattr(panel, "sub_fill_en", None) is None
    fill_sec = None
    from cutora.ui.widgets.collapsible import CollapsibleSection
    for sec in panel.findChildren(CollapsibleSection):
        if getattr(sec, "section_title", "") == "配色方案":
            fill_sec = sec
            break
    assert fill_sec is not None
    assert getattr(fill_sec, "_toggle_cb", None) is None
    QApplication.processEvents()
    assert fill_sec._content.isEnabled() is True

    got = panel.collect()
    fill = (got.get("fx") or {}).get("fill") or {}
    assert fill.get("type") == "solid", fill
    cols = [str(c).upper() for c in (fill.get("colors") or [])]
    assert cols[:1] == [DEFAULT_FILL.upper()], cols

    after_style = copy.deepcopy(style)
    after_style["fx"] = copy.deepcopy(got["fx"])
    after_preview = _preview_bytes(after_style)
    after_export_md5, after_export = _export_bytes(after_style)
    panel.close()
    assert after_preview == before_preview
    assert after_export == before_export
    assert after_export_md5 == hashlib.md5(before_export).hexdigest()
