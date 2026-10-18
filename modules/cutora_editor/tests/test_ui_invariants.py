# -*- coding: utf-8 -*-
"""Q47: UI quality as global invariants. Scan all of cutora/ui, no file whitelist.

KNOWN_EXCEPTIONS may only shrink. Bump KNOWN_EXCEPTIONS_MAX to add a row —
that change is the red you have to explain.
"""
from __future__ import annotations

import ast
import os
import re

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UI_ROOT = os.path.join(REPO, "cutora", "ui")
CUTORA_ROOT = os.path.join(REPO, "cutora")
PANELS_ROOT = os.path.join(UI_ROOT, "panels")
RENDER_ROOT = os.path.join(REPO, "cutora", "render")

SPACE_GRID = frozenset({0, 1, 2, 4, 6, 8, 12, 16, 24, 32})
FONT_SCALE = frozenset({11, 12, 13, 15, 17, 20})
_PX_RE = re.compile(r"(-?\d+)px")
_FONT_SIZE_RE = re.compile(r"font-size\s*:\s*(-?\d+)\s*px", re.I)

# Frozen length of KNOWN_EXCEPTIONS. Adding a row without lowering something
# else must bump this number — the point of the gate.
KNOWN_EXCEPTIONS_MAX = 36

# (test, loc, reason, retire_by). loc is the scanner's violation string.
_SPACING_LEFT = (
    ("cutora/ui/main_window.py:2414 setSpacing 10",
     "main-window chrome still uses 10px; visual lock until Q56 rerecord"),
    ("cutora/ui/welcome.py:85 setContentsMargins 40",
     "welcome outer pad; u01 visual lock"),
    ("cutora/ui/welcome.py:85 setContentsMargins 28",
     "welcome outer pad; u01 visual lock"),
    ("cutora/ui/welcome.py:86 setSpacing 26",
     "welcome stack gap; u01 visual lock"),
    ("cutora/ui/welcome.py:153 setSpacing 14",
     "welcome actions gap; u01 visual lock"),
    ("cutora/ui/welcome.py:271 setContentsMargins 10",
     "welcome card pad; u01 visual lock"),
    ("cutora/ui/panels/appearance.py:70 setSpacing 10",
     "appearance page pre-token 10px"),
    ("cutora/ui/panels/fonts.py:763 setContentsMargins 10",
     "font manager dialog pad"),
    ("cutora/ui/panels/fonts.py:764 setSpacing 10",
     "font manager dialog gap"),
)
_DEAD_LEFT = (
    # pack / settings — not subtitle paint
    ("_preset_name", "pack metadata written by property panel", "Q50"),
    ("preset_name", "pack metadata", "Q50"),
    ("source_preset_id", "pack metadata", "Q50"),
    ("accent", "appearance palette write, not style paint", "keep"),
    ("accent_dim", "appearance palette write", "keep"),
    ("accent_hi", "appearance palette write", "keep"),
    ("api_enabled", "API settings panel", "keep"),
    ("api_port", "API settings panel", "keep"),
    ("is_detached", "layout flag, not textfx", "Q50"),
    ("use_box", "v1 alias; textfx reads fx.bg.type", "Q50"),
    ("word_mode", "ASR split only, not textfx paint", "Q39"),
    ("high_padding", "dual-write leftover; paint uses fx.box_highlight", "Q50"),
    ("high_offset_y", "dual-write leftover; paint uses fx.box_highlight", "Q50"),
    # v1 flats (keyword_rules, fade/typewriter/spectrum/metro/holy-glow/…) are
    # no longer written after the fx inspector rewrite, so they are not dead
    # keys. lyrics extras / adv stroke are still written and still unread.
    ("lyrics_center_color", "lyrics path ignores color", "Q39"),
    ("lyrics_scroll_damping", "written, never read", "Q39"),
    ("lyrics_scroll_transition", "written, never read", "Q39"),
    ("adv_line_spacing", "canvas-only leftover", "Q39"),
    ("adv_text_align", "canvas-only leftover", "Q39"),
    ("adv_stroke_color", "no render reader", "Q39"),
    ("adv_stroke_enabled", "no render reader", "Q39"),
    ("adv_stroke_opacity", "no render reader", "Q39"),
    ("adv_stroke_width", "no render reader", "Q39"),
    ("advanced_textbox_align", "no render reader", "Q39"),
    # Q137 智能断行：排版时（cutora/subtitle/reflow.py）读，不是绘制时读
    ("layout_engine", "read by subtitle/reflow at relayout, not paint", "keep"),
    ("max_words_per_line", "read by subtitle/reflow at relayout, not paint", "keep"),
    ("pause_break_sec", "read by subtitle/reflow at relayout, not paint", "keep"),
)
_HEIGHT_LEFT = (
    # Q83 unified every inspector control on INSPECTOR_CONTROL_H (28). What is
    # left is Qt's own editor inside each spin box (qt_spinbox_lineedit).
    ("QLineEdit heights=[19, 28]",
     "spin-box inner qt_spinbox_lineedit vs real line edits"),
)
KNOWN_EXCEPTIONS: tuple[tuple[str, str, str, str], ...] = tuple(
    ("spacing_grid", loc, reason, "Q56") for loc, reason in _SPACING_LEFT
) + tuple(
    ("dead_key", "dead key %s" % key, reason, retire)
    for key, reason, retire in _DEAD_LEFT
) + tuple(
    ("control_height", loc, reason, "Q50") for loc, reason in _HEIGHT_LEFT
)


def _qapp():
    from PySide6.QtWidgets import QApplication, QStyleFactory
    app = QApplication.instance() or QApplication(["q47-invariants"])
    app.setStyle(QStyleFactory.create("Fusion"))
    return app


def _apply_qss(app):
    from cutora.ui.theme.qss import render
    from cutora.ui.theme.tokens import DEFAULT_THEME_ID, load_theme
    load_theme(DEFAULT_THEME_ID)
    app.setStyleSheet(render())


def _iter_py(root):
    for dirpath, _dirs, files in os.walk(root):
        if "__pycache__" in dirpath.replace("\\", "/").split("/"):
            continue
        for name in files:
            if name.endswith(".py"):
                yield os.path.join(dirpath, name)


def _rel(path):
    return os.path.relpath(path, REPO).replace("\\", "/")


def _parse(path):
    src = open(path, encoding="utf-8").read()
    return src, ast.parse(src, filename=_rel(path))


def _const_num(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return float(node.value)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        inner = _const_num(node.operand)
        return None if inner is None else -inner
    return None


def _const_str(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _attr_chain(node):
    parts = []
    cur = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
        return ".".join(reversed(parts))
    return None


def _call_name(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _exception_locs(test_name):
    return {loc for t, loc, _r, _b in KNOWN_EXCEPTIONS if t == test_name}


def _assert_explained(test_name, hits):
    """hits must equal the exception set for this test. extras = new drift."""
    explained = _exception_locs(test_name)
    hitset = set(hits)
    extra = sorted(hitset - explained)
    stale = sorted(explained - hitset)
    assert extra == [], "unexplained %s hits (add to KNOWN_EXCEPTIONS only if you also bump MAX):\n%s" % (
        test_name, "\n".join(extra[:40]))
    assert stale == [], "stale %s exceptions (remove them and lower KNOWN_EXCEPTIONS_MAX):\n%s" % (
        test_name, "\n".join(stale[:40]))


def test_known_exceptions_only_shrink():
    assert len(KNOWN_EXCEPTIONS) <= KNOWN_EXCEPTIONS_MAX, (
        "KNOWN_EXCEPTIONS grew to %d > frozen %d"
        % (len(KNOWN_EXCEPTIONS), KNOWN_EXCEPTIONS_MAX)
    )
    assert len(KNOWN_EXCEPTIONS) == KNOWN_EXCEPTIONS_MAX, (
        "KNOWN_EXCEPTIONS is %d, frozen MAX is %d — lower MAX when you drop a row"
        % (len(KNOWN_EXCEPTIONS), KNOWN_EXCEPTIONS_MAX)
    )
    ids = [row[1] for row in KNOWN_EXCEPTIONS]
    assert len(ids) == len(set(ids)), "duplicate KNOWN_EXCEPTIONS loc"
    for test, loc, reason, retire in KNOWN_EXCEPTIONS:
        assert test and loc and reason and retire, (test, loc, reason, retire)


# ── scanners ──────────────────────────────────────────────────────────────


def scan_spacing_grid():
    hits = []
    for path in _iter_py(UI_ROOT):
        _src, tree = _parse(path)
        rel = _rel(path)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = _call_name(node.func)
            if name not in ("setSpacing", "setContentsMargins"):
                continue
            for arg in node.args:
                val = _const_num(arg)
                if val is None:
                    continue
                ival = int(round(val))
                if ival not in SPACE_GRID:
                    hits.append("%s:%d %s %s" % (rel, node.lineno, name, ival))
    return hits


def scan_type_scale():
    hits = []
    for path in _iter_py(UI_ROOT):
        src, tree = _parse(path)
        rel = _rel(path)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if _call_name(node.func) != "setPixelSize":
                continue
            if not node.args:
                continue
            val = _const_num(node.args[0])
            if val is None:
                continue
            ival = int(round(val))
            if ival not in FONT_SCALE:
                hits.append("%s:%d setPixelSize %s" % (rel, node.lineno, ival))
        for i, line in enumerate(src.splitlines(), 1):
            for match in _FONT_SIZE_RE.finditer(line):
                ival = int(match.group(1))
                if ival not in FONT_SCALE:
                    hits.append("%s:%d font-size %spx" % (rel, i, ival))
    return hits


def scan_radius_literals():
    from cutora.ui.theme.tokens import RADIUS_ALLOWED
    allowed = set(RADIUS_ALLOWED) | {0}
    hits = []
    for path in _iter_py(UI_ROOT):
        _src, tree = _parse(path)
        rel = _rel(path)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if _call_name(node.func) not in ("drawRoundedRect", "addRoundedRect"):
                continue
            if len(node.args) < 2:
                continue
            for arg in (node.args[-2], node.args[-1]):
                val = _const_num(arg)
                if val is None:
                    continue
                ival = int(round(val))
                if ival not in allowed:
                    hits.append("%s:%d rounded %s" % (rel, node.lineno, ival))
    return hits


def scan_qss_non_token_px():
    from cutora.ui.theme.qss import render
    from cutora.ui.theme.tokens import DEFAULT_THEME_ID, load_theme
    from tools.build_qss import check_rendered_qss, check_template_bare_px

    tmpl = os.path.join(UI_ROOT, "theme", "theme_dark.qss.j2")
    src = open(tmpl, encoding="utf-8").read()
    hits = list(check_template_bare_px(src))
    load_theme(DEFAULT_THEME_ID)
    qss = render()
    hits.extend(check_rendered_qss(qss))
    # Template forbids bare Npx; rendered closed-sets cover radius / min-height /
    # padding. Arithmetic of tokens (space_6 * 2 + space_2) is still a token px.
    return hits


def _frozenset_strs(node):
    out = []
    if isinstance(node, (ast.Set, ast.List, ast.Tuple)):
        for elt in node.elts:
            s = _const_str(elt)
            if s is not None:
                out.append(s)
        return out
    if isinstance(node, ast.Call) and _call_name(node.func) in ("frozenset", "set"):
        if node.args:
            return _frozenset_strs(node.args[0])
    return out


def scan_written_keys():
    """Panel writes: current_data[k], style[k], s[k], nested_set path, pick_c(k)."""
    keys = set()
    for path in _iter_py(PANELS_ROOT):
        _src, tree = _parse(path)
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for tgt in node.targets:
                    if not isinstance(tgt, ast.Subscript):
                        continue
                    k = _const_str(tgt.slice)
                    if k is None:
                        continue
                    chain = _attr_chain(tgt.value)
                    if chain is None and isinstance(tgt.value, ast.Name):
                        chain = tgt.value.id
                    if chain in (
                        "self.current_data", "current_data",
                        "style", "s", "data",
                    ) or (chain or "").endswith("current_data"):
                        keys.add(k)
            if not isinstance(node, ast.Call):
                continue
            name = _call_name(node.func)
            if name == "nested_set" and len(node.args) >= 2:
                pth = _const_str(node.args[1])
                if pth:
                    keys.add(pth)
            if name == "pick_c" and node.args:
                k = _const_str(node.args[0])
                if k:
                    keys.add(k)
    return keys


def scan_read_keys():
    """Render reads: style.get / d.get / s.get / take_known leaves."""
    keys = set()
    for path in _iter_py(RENDER_ROOT):
        _src, tree = _parse(path)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = _call_name(node.func)
            if name == "get" and node.args:
                k = _const_str(node.args[0])
                if k is not None:
                    keys.add(k)
            if name == "take_known" and len(node.args) >= 2:
                leaves = _frozenset_strs(node.args[1])
                prefix = _const_str(node.args[2]) if len(node.args) >= 3 else ""
                for leaf in leaves:
                    keys.add(leaf)
                    if prefix:
                        keys.add("%s.%s" % (prefix, leaf))
                        keys.add(prefix)
    return keys


def scan_unreachable():
    hits = []

    def walk_body(body, rel):
        for i, stmt in enumerate(body):
            if isinstance(stmt, ast.Return):
                rest = body[i + 1:]
                if rest:
                    nxt = rest[0]
                    hits.append("%s:%d return then %s" % (
                        rel, getattr(nxt, "lineno", stmt.lineno), type(nxt).__name__))
            for sub in _stmt_bodies(stmt):
                walk_body(sub, rel)

    for path in _iter_py(CUTORA_ROOT):
        rel = _rel(path)
        try:
            _src, tree = _parse(path)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                walk_body(list(node.body), rel)
    return hits


def _stmt_bodies(stmt):
    out = []
    if isinstance(stmt, ast.If):
        out.append(list(stmt.body))
        out.append(list(stmt.orelse))
    elif isinstance(stmt, (ast.For, ast.AsyncFor, ast.While)):
        out.append(list(stmt.body))
        out.append(list(stmt.orelse))
    elif isinstance(stmt, (ast.With, ast.AsyncWith)):
        out.append(list(stmt.body))
    elif isinstance(stmt, ast.Try):
        out.append(list(stmt.body))
        out.append(list(stmt.orelse))
        out.append(list(stmt.finalbody))
        for h in stmt.handlers:
            out.append(list(h.body))
    elif isinstance(stmt, ast.ExceptHandler):
        out.append(list(stmt.body))
    return out


# ── the twelve gates ──────────────────────────────────────────────────────


def test_spacing_grid():
    """All setSpacing / setContentsMargins literals ∈ {0,1,2,4,6,8,12,16,24,32}."""
    _assert_explained("spacing_grid", scan_spacing_grid())


def test_no_literal_px_in_qss():
    """Rendered QSS has no px outside the token set. Template has no bare px."""
    _assert_explained("qss_px", scan_qss_non_token_px())


def test_radius_closed_set():
    """drawRoundedRect / addRoundedRect literals ∈ 4 RADIUS tokens (+ 0)."""
    from cutora.ui.theme.qss import render
    from cutora.ui.theme.tokens import DEFAULT_THEME_ID, load_theme
    from tools.build_qss import check_rendered_qss, check_template_bare_px

    tmpl = os.path.join(UI_ROOT, "theme", "theme_dark.qss.j2")
    src = open(tmpl, encoding="utf-8").read()
    assert check_template_bare_px(src) == []
    load_theme(DEFAULT_THEME_ID)
    closed = check_rendered_qss(render())
    radius_hits = [h for h in closed if "border-radius" in h]
    assert radius_hits == [], radius_hits[:20]
    _assert_explained("radius", scan_radius_literals())


def test_type_scale_closed_set():
    """setPixelSize literals and font-size:Npx in UI python ∈ 6 type sizes."""
    _assert_explained("type_scale", scan_type_scale())


def test_type_hierarchy():
    """Rendered section title is larger than the row labels it governs."""
    from PySide6.QtGui import QFontInfo, QFontMetrics
    from PySide6.QtWidgets import QApplication, QLabel

    app = _qapp()
    _apply_qss(app)
    import cutora.ui.panels.property  # noqa: F401
    from cutora.ui.panels.fonts import load_embedded_fonts
    from cutora.ui.panels.subtitle_style import SubtitleStylePanel
    from cutora.ui.widgets.dock_panels import CollapsibleSection

    load_embedded_fonts()
    panel = SubtitleStylePanel()
    for sec in panel.findChildren(CollapsibleSection):
        if hasattr(sec, "set_collapsed"):
            sec.set_collapsed(False)
    panel.show()
    QApplication.processEvents()
    titles = [
        lab for lab in panel.findChildren(QLabel, "dockSectionTitle")
        if lab.isVisible() and (lab.text() or "").strip()
    ]
    labels = [
        lab for lab in panel.findChildren(QLabel, "inspectorLabel")
        if lab.isVisible() and (lab.text() or "").strip()
    ]
    assert titles, "no dockSectionTitle"
    assert labels, "no inspectorLabel"
    title_h = max(QFontMetrics(t.font()).height() for t in titles)
    label_h = min(QFontMetrics(lab.font()).height() for lab in labels)
    title_px = max(QFontInfo(t.font()).pixelSize() for t in titles)
    label_px = min(QFontInfo(lab.font()).pixelSize() for lab in labels)
    panel.close()
    assert title_h > label_h, "section title metrics %s must exceed row label %s" % (title_h, label_h)
    assert title_px > label_px, "section title %spx must exceed row label %spx" % (title_px, label_px)


def test_control_height_uniform():
    """Same inspector widget class has one sizeHint height across panels."""
    from PySide6.QtWidgets import (
        QApplication, QComboBox, QDoubleSpinBox, QLineEdit, QSpinBox,
    )
    from cutora.ui.widgets.collapsible import ToggleSwitch
    from cutora.ui.widgets.inspector import SliderSpin
    from cutora.ui.panels.fonts import load_embedded_fonts

    app = _qapp()
    _apply_qss(app)
    import cutora.ui.panels.property  # noqa: F401
    from cutora.ui.panels.subtitle_style import SubtitleStylePanel
    from cutora.ui.panels.property import PropertyPanel
    from cutora.ui.widgets.dock_panels import CollapsibleSection

    load_embedded_fonts()
    panels = [SubtitleStylePanel(), PropertyPanel()]
    for panel in panels:
        for sec in panel.findChildren(CollapsibleSection):
            if hasattr(sec, "set_collapsed"):
                sec.set_collapsed(False)
        panel.show()
    QApplication.processEvents()

    classes = (
        ("QComboBox", QComboBox),
        ("QSpinBox", QSpinBox),
        ("QDoubleSpinBox", QDoubleSpinBox),
        ("QLineEdit", QLineEdit),
        ("ToggleSwitch", ToggleSwitch),
        ("SliderSpin", SliderSpin),
    )
    hits = []
    for label, cls in classes:
        heights = {}
        for panel in panels:
            for w in panel.findChildren(cls):
                try:
                    h = int(w.sizeHint().height())
                except RuntimeError:
                    continue
                heights.setdefault(h, 0)
                heights[h] += 1
        if len(heights) > 1:
            hits.append("%s heights=%s" % (label, sorted(heights)))
    for panel in panels:
        panel.close()
    _assert_explained("control_height", hits)


def _selectors(qss: str):
    text = re.sub(r"/\*.*?\*/", " ", qss, flags=re.S)
    found = []
    for chunk in text.split("}"):
        if "{" not in chunk:
            continue
        sel = chunk.split("{", 1)[0]
        for part in sel.split(","):
            s = " ".join(part.split())
            if s:
                found.append(s)
    return found


def _pseudos(selector: str):
    flags = set()
    if ":hover" in selector:
        flags.add("hover")
    if ":pressed" in selector:
        flags.add("pressed")
    if ":focus" in selector:
        flags.add("focus")
    if ":disabled" in selector:
        flags.add("disabled")
    return flags


def _states_for(selectors, token: str):
    found = set()
    if "#" in token:
        needle = "#" + token.split("#", 1)[1]
        for s in selectors:
            if needle in s:
                found |= _pseudos(s)
        return found
    pat = re.compile(r"(^|[\s>])%s(?=[:#.\s]|$)" % re.escape(token))
    for s in selectors:
        if pat.search(s):
            found |= _pseudos(s)
    return found


def test_all_interactive_have_four_states():
    from cutora.ui.theme.qss import render
    from cutora.ui.theme.states import INTERACTIVE_CLASSES
    from cutora.ui.theme.tokens import DEFAULT_THEME_ID, load_theme

    load_theme(DEFAULT_THEME_ID)
    qss = render()
    selectors = _selectors(qss)
    assert selectors, "rendered QSS had no selectors"
    need = {"hover", "pressed", "focus", "disabled"}
    missing = []
    for cls in INTERACTIVE_CLASSES:
        got = _states_for(selectors, cls)
        lost = sorted(need - got)
        if lost:
            missing.append("%s missing %s (have %s)" % (cls, lost, sorted(got)))
    assert not missing, "four-state contract:\n" + "\n".join(missing)


def test_contrast_all_surfaces():
    """fg0/fg1/fg2 × bg1/bg2/bg3 and ok/warn/err × bg1/bg2, all six palettes."""
    from cutora.ui.theme.contrast import CHECKS, check_palette
    from cutora.ui.theme.tokens import BUILTIN_THEME_IDS, load_palette_data

    labels = [row[0] for row in CHECKS]
    for fg in ("fg0", "fg1", "fg2"):
        for bg in ("bg1", "bg2", "bg3"):
            assert ("%s on %s" % (fg, bg)) in labels, (fg, bg, labels)
    for fg in ("ok", "warn", "err"):
        for bg in ("bg1", "bg2"):
            assert ("%s on %s" % (fg, bg)) in labels, (fg, bg, labels)
    all_fails = []
    for tid in BUILTIN_THEME_IDS:
        data = load_palette_data(tid)
        all_fails.extend(check_palette(data, tid))
    assert all_fails == [], all_fails


def test_toggle_uniform_all():
    """Every ToggleSwitch in panels is TOGGLE_W × TOGGLE_H, sized by tokens."""
    from cutora.ui.theme.tokens import TOGGLE_H, TOGGLE_W
    from cutora.ui.widgets.collapsible import ToggleSwitch
    from cutora.ui.panels.fonts import load_embedded_fonts
    from PySide6.QtWidgets import QApplication

    src, tree = _parse(os.path.join(UI_ROOT, "widgets", "collapsible.py"))
    literal_size = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if _call_name(node.func) != "setFixedSize":
            continue
        nums = [_const_num(a) for a in node.args]
        if any(n is not None for n in nums):
            literal_size.append("collapsible.py:%d setFixedSize literals %s" % (
                node.lineno, nums))
    assert literal_size == [], literal_size

    app = _qapp()
    _apply_qss(app)
    import cutora.ui.panels.property  # noqa: F401
    from cutora.ui.panels.subtitle_style import SubtitleStylePanel
    from cutora.ui.panels.property import PropertyPanel

    load_embedded_fonts()
    panels = [SubtitleStylePanel(), PropertyPanel()]
    for p in panels:
        p.show()
    QApplication.processEvents()
    bad = []
    n = 0
    for panel in panels:
        for w in panel.findChildren(ToggleSwitch):
            n += 1
            if w.width() != TOGGLE_W or w.height() != TOGGLE_H:
                bad.append("%s %sx%s want %sx%s" % (
                    w.objectName() or type(w).__name__,
                    w.width(), w.height(), TOGGLE_W, TOGGLE_H))
    for p in panels:
        p.close()
    assert n >= 10, "expected many ToggleSwitch widgets, got %d" % n
    assert bad == [], bad


def _count_rgb(img, rgb, slop=6):
    from PySide6.QtGui import QImage
    conv = img.convertToFormat(QImage.Format.Format_RGB32)
    w, h = conv.width(), conv.height()
    if w <= 0 or h <= 0:
        return 0
    bpl = conv.bytesPerLine()
    raw = bytes(conv.constBits())
    tr, tg, tb = rgb
    n = 0
    for y in range(h):
        row = raw[y * bpl: y * bpl + w * 4]
        for x in range(w):
            b = row[x * 4]
            g = row[x * 4 + 1]
            r = row[x * 4 + 2]
            if abs(r - tr) <= slop and abs(g - tg) <= slop and abs(b - tb) <= slop:
                n += 1
    return n


def test_theme_switch_repaints_all():
    """Switching palettes repaints chrome; leftover roma gold must not dominate."""
    from PySide6.QtWidgets import QApplication
    from cutora.ui.theme.apply import apply_theme
    from cutora.ui.theme.tokens import BUILTIN_THEME_IDS, current, hex_rgb, mix_hex
    from cutora.ui.timeline.widget import TimelineWidget
    from cutora.model.timeline import Timeline
    from cutora.model.timeline_model import Clip, Track

    app = _qapp()
    _apply_qss(app)
    tl = TimelineWidget()
    tl.resize(640, 220)
    tl.pixels_per_second = 40.0
    model = Timeline(1080, 1920, 30)
    track = Track("video")
    track.clips.append(Clip("s1", 0.0, 4.0, 0.5))
    model.tracks.append(track)
    tl.task_data = {"timeline": model, "segments": []}
    tl.show()
    QApplication.processEvents()

    grabs = {}
    for tid in BUILTIN_THEME_IDS:
        apply_theme(tid)
        QApplication.processEvents()
        tl.update()
        QApplication.processEvents()
        img = tl.grab().toImage()
        grabs[tid] = img
        t = current()
        body_hex = mix_hex(t.bg1, t.bg2, 0.55)
        n_body = _count_rgb(img, hex_rgb(body_hex), slop=18)
        assert n_body > 30, "%s clip body not near mix(bg1,bg2)=%s (matched %d)" % (
            tid, body_hex, n_body)

    roma = grabs["roma-gold"]
    graphite = grabs["graphite"]
    differ = False
    for y in range(0, roma.height(), 7):
        for x in range(0, roma.width(), 7):
            if roma.pixel(x, y) != graphite.pixel(x, y):
                differ = True
                break
        if differ:
            break
    tl.close()
    assert differ, "roma-gold and graphite timeline grabs were identical"

    from cutora.ui.main_window import MainWindow
    win = MainWindow()
    win.resize(1100, 720)
    win.show()
    QApplication.processEvents()
    apply_theme("graphite")
    QApplication.processEvents()
    shot = win.grab().toImage()
    gold = hex_rgb("#C8A24A")
    n_gold = _count_rgb(shot, gold, slop=8)
    win.close()
    assert n_gold < 400, "graphite main window still has roma-gold accent pixels: %d" % n_gold


def test_no_unreachable_code():
    """Function bodies: no statements after return in the same block."""
    _assert_explained("unreachable", scan_unreachable())


def test_no_dead_written_keys():
    """Panel-written keys minus render-read keys. Leftovers must be excepted."""
    written = scan_written_keys()
    read = scan_read_keys()
    # fx nested paths: a write of "fill.type" is live if take_known saw "type"
    # under fill, or if "fill" is a known fx key and "type" is a fill leaf.
    dead = sorted(k for k in written if k not in read)
    hits = ["dead key %s" % k for k in dead]
    _assert_explained("dead_key", hits)
