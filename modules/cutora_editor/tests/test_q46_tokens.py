# -*- coding: utf-8 -*-
"""Q46: expanded design tokens, type hierarchy, radius set, labels, theme switch."""
from __future__ import annotations

import ast
import os
import subprocess
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UI_ROOT = os.path.join(REPO, "cutora", "ui")
_CHILD_ENV = "CUTORA_Q46_CHILD"


def _run_in_fresh_interpreter():
    """TimelineWidget then MainWindow in one interpreter hangs (q110/q122)."""
    if os.environ.get(_CHILD_ENV) == "1":
        return False
    node = (os.environ.get("PYTEST_CURRENT_TEST") or "").split(" ")[0]
    if not node:
        return False
    env = dict(os.environ)
    env[_CHILD_ENV] = "1"
    env["CUTORA_PYTEST_ISOLATED"] = "1"
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", node, "-q", "--tb=short",
         "-p", "no:cacheprovider"],
        cwd=REPO, env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=180,
    )
    assert proc.returncode == 0, (proc.stdout or "") + "\n" + (proc.stderr or "")
    return True

# Pre-fix timeline clip fills. Must not survive as a dominant blob after Q46.
_COLD_FILLS = (
    (0x49, 0x39, 0x55),
    (0x3A, 0x5A, 0x3A),
    (0x2F, 0x3B, 0x4A),
    (0x33, 0x48, 0x4A),
    (0x5B, 0x46, 0x68),
)


def _qapp():
    from PySide6.QtWidgets import QApplication, QStyleFactory
    app = QApplication.instance() or QApplication(["q46-tokens"])
    app.setStyle(QStyleFactory.create("Fusion"))
    return app


def _apply_qss(app):
    from cutora.ui.theme.qss import render
    from cutora.ui.theme.tokens import DEFAULT_THEME_ID, load_theme
    load_theme(DEFAULT_THEME_ID)
    app.setStyleSheet(render())


def _panel():
    import cutora.ui.panels.property  # noqa: F401
    from cutora.ui.panels.fonts import load_embedded_fonts
    from cutora.ui.panels.subtitle_style import SubtitleStylePanel
    load_embedded_fonts()
    return SubtitleStylePanel()


def _expand(panel):
    from cutora.ui.widgets.dock_panels import CollapsibleSection
    from PySide6.QtWidgets import QApplication
    for sec in panel.findChildren(CollapsibleSection):
        if hasattr(sec, "set_collapsed"):
            sec.set_collapsed(False)
    QApplication.processEvents()


def test_type_hierarchy():
    """Rendered section title must be larger than the row labels it governs."""
    if _run_in_fresh_interpreter():
        return
    from PySide6.QtGui import QFontInfo
    from PySide6.QtWidgets import QApplication, QLabel

    app = _qapp()
    _apply_qss(app)
    panel = _panel()
    _expand(panel)
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
    title_px = max(QFontInfo(t.font()).pixelSize() for t in titles)
    label_px = min(QFontInfo(lab.font()).pixelSize() for lab in labels)
    panel.close()
    assert title_px > label_px, (
        "section title %spx must exceed row label %spx" % (title_px, label_px)
    )


def test_q46_label_not_clipped():
    """Every inspector label's advance fits in the width it was given, even at dock min."""
    if _run_in_fresh_interpreter():
        return
    from PySide6.QtGui import QFontMetrics
    from PySide6.QtWidgets import QApplication
    from cutora.ui.theme.tokens import DOCK_RIGHT_MIN
    from cutora.ui.widgets.inspector import InspectorRow

    app = _qapp()
    _apply_qss(app)
    panel = _panel()
    _expand(panel)
    panel.setFixedWidth(DOCK_RIGHT_MIN)
    panel.show()
    QApplication.processEvents()
    panel.resize(DOCK_RIGHT_MIN, max(panel.height(), 800))
    QApplication.processEvents()

    clipped = []
    for row in panel.findChildren(InspectorRow):
        lab = row.label
        text = row.full_label_text() if hasattr(row, "full_label_text") else lab.text()
        if not lab.isVisible() or not (text or "").strip():
            continue
        fm = QFontMetrics(lab.font())
        adv = fm.horizontalAdvance(text)
        given = lab.width()
        if adv > given:
            clipped.append((text, adv, given))
    panel.close()
    assert clipped == [], clipped[:12]


def _radius_allowed():
    from cutora.ui.theme.tokens import RADIUS_ALLOWED
    return set(RADIUS_ALLOWED) | {0}


def _const_num(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return float(node.value)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        inner = _const_num(node.operand)
        return None if inner is None else -inner
    return None


def test_radius_closed_set():
    """QSS border-radius and cutora/ui drawRoundedRect literals ∈ 4 tokens."""
    from cutora.ui.theme.qss import render
    from cutora.ui.theme.tokens import DEFAULT_THEME_ID, load_theme
    from tools.build_qss import check_rendered_qss, check_template_bare_px

    tmpl = os.path.join(REPO, "cutora", "ui", "theme", "theme_dark.qss.j2")
    src = open(tmpl, encoding="utf-8").read()
    bare = check_template_bare_px(src)
    assert bare == [], bare[:20]
    load_theme(DEFAULT_THEME_ID)
    closed = check_rendered_qss(render())
    assert closed == [], closed[:20]

    allowed = _radius_allowed()
    hits = []
    for dirpath, _dirs, files in os.walk(UI_ROOT):
        for name in files:
            if not name.endswith(".py"):
                continue
            path = os.path.join(dirpath, name)
            rel = os.path.relpath(path, REPO)
            tree = ast.parse(open(path, encoding="utf-8").read(), filename=rel)
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                if not isinstance(func, ast.Attribute):
                    continue
                if func.attr not in ("drawRoundedRect", "addRoundedRect"):
                    continue
                if len(node.args) < 2:
                    continue
                rx = _const_num(node.args[-2])
                ry = _const_num(node.args[-1])
                for val in (rx, ry):
                    if val is None:
                        continue
                    ival = int(round(val))
                    if ival not in allowed:
                        hits.append("%s:%d %s %s" % (
                            rel.replace("\\", "/"), node.lineno, func.attr, val))
    assert hits == [], hits


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
            # BGRA on little-endian
            b = row[x * 4]
            g = row[x * 4 + 1]
            r = row[x * 4 + 2]
            if abs(r - tr) <= slop and abs(g - tg) <= slop and abs(b - tb) <= slop:
                n += 1
    return n


def test_theme_switch_repaints_all():
    """Switching palettes repaints chrome; old cold timeline fills must be gone."""
    if _run_in_fresh_interpreter():
        return
    from PySide6.QtWidgets import QApplication
    from cutora.ui.theme.apply import apply_theme
    from cutora.ui.theme.tokens import BUILTIN_THEME_IDS, current, hex_rgb, mix_hex
    from cutora.ui.timeline.widget import TimelineWidget

    app = _qapp()
    _apply_qss(app)

    from cutora.model.timeline import Timeline
    from cutora.model.timeline_model import Clip, Track

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
        palette_rgbs = []
        for key in (
            "bg0", "bg1", "bg2", "bg3", "line", "border",
            "fg0", "fg1", "fg2", "accent", "track_video", "track_audio",
            "track_subtitle", "track_text", "track_image",
        ):
            palette_rgbs.append(hex_rgb(getattr(t, key)))
        for rgb in _COLD_FILLS:
            close = False
            for pr, pg, pb in palette_rgbs:
                if abs(rgb[0] - pr) <= 14 and abs(rgb[1] - pg) <= 14 and abs(rgb[2] - pb) <= 14:
                    close = True
                    break
            if close:
                continue
            n = _count_rgb(img, rgb, slop=4)
            assert n < 80, "%s still paints cold fill %s (%d px)" % (tid, rgb, n)
        # Q169: clip body is the track colour itself (solid block, slight top→bottom gradient).
        body_hex = t.track_video
        n_body = _count_rgb(img, hex_rgb(body_hex), slop=18)
        assert n_body > 30, (
            "%s clip body not near track_video=%s (matched %d)"
            % (tid, body_hex, n_body)
        )

    # Distinct palettes must not produce identical grabs.
    roma = grabs["roma-gold"]
    graphite = grabs["graphite"]
    assert roma.size() == graphite.size()
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
    # MainWindow chrome after graphite is ui_golden. Nested isolate grandchild
    # hangs on MainWindow() while the full suite holds CUDA (90s and 180s).
