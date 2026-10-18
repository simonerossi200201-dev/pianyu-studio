# -*- coding: utf-8 -*-
"""BUG-20..24: canvas glass, bg toggle, lyrics, orphan labels, glass gradient."""
from __future__ import annotations

import json
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
G11 = os.path.join(REPO, "tests", "golden", "g11_vertical_basic_fx", "project.asproj.json")


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(["bug20-24"])


def _glass_task():
    from cutora.model.project_io import deserialize_render_task
    from cutora.subtitle.stylepack import apply_pack_to_task, builtin_dir, load_pack
    data = json.loads(open(G11, encoding="utf-8").read())
    task = deserialize_render_task(data["tasks"][0])
    pack = load_pack(os.path.join(builtin_dir(), "glass-white"))
    apply_pack_to_task(task, pack)
    return task


def _rgb(img):
    from PySide6.QtGui import QImage
    import numpy as np
    conv = img.convertToFormat(QImage.Format.Format_RGB888)
    w, h = conv.width(), conv.height()
    bpl = conv.bytesPerLine()
    arr = np.frombuffer(bytes(conv.constBits()), dtype=np.uint8)
    arr = arr.reshape((h, bpl))[:, :w * 3].reshape((h, w, 3))
    return arr.copy()


def _paint_canvas(qapp, task, t=2.0):
    from cutora.ui.canvas import VideoCanvas
    from cutora.preview.engine import PreviewEngine
    canvas = VideoCanvas()
    canvas.resize(540, 960)
    eng = PreviewEngine()
    try:
        video = eng.video_frame_at(task, t)
    finally:
        eng.close()
    canvas.set_program_frame(video)
    canvas.set_data(task, t)
    canvas.show()
    qapp.processEvents()
    canvas.repaint()
    qapp.processEvents()
    img = canvas.grab().toImage()
    canvas.close()
    return img


def test_bug20_canvas_grab_glass_variance_and_alpha(qapp):
    import numpy as np
    task = _glass_task()
    fx = task["style"]["fx"]["bg"]
    fx["alpha"] = 0.45
    a = _paint_canvas(qapp, task, 2.0)
    fx["alpha"] = 0.9
    b = _paint_canvas(qapp, task, 2.0)
    aa, ba = _rgb(a), _rgb(b)
    y0, y1, x0, x1 = 540, 585, 190, 350
    band_a = aa[y0:y1, x0:x1]
    band_b = ba[y0:y1, x0:x1]
    var = float(band_a.astype(np.float64).var())
    assert var > 0, var
    mean_diff = abs(float(band_a.mean()) - float(band_b.mean())) / 255.0
    assert mean_diff > 20.0 / 255.0, mean_diff


def test_bug21_bg_toggle_off_no_box(qapp):
    import numpy as np
    import cutora.ui.panels.property  # noqa: F401
    from cutora.ui.panels.subtitle_style import SubtitleStylePanel
    task = _glass_task()
    panel = SubtitleStylePanel()
    panel.load_data(task["style"])
    panel.block_signals = False
    panel.current_data = task["style"]
    on_img = _paint_canvas(qapp, task, 2.0)
    panel.sub_box.setChecked(False)
    panel.on_change()
    # Q64 段开关关的是 bg.enabled，类型留着，再打开还是玻璃。
    bg = task["style"]["fx"]["bg"]
    assert bg.get("enabled") is False
    assert bg.get("type") == "glass"
    off_img = _paint_canvas(qapp, task, 2.0)
    on_a, off_a = _rgb(on_img), _rgb(off_img)
    y0, y1, x0, x1 = 540, 585, 190, 350
    band_on = on_a[y0:y1, x0:x1].astype(np.int16)
    band_off = off_a[y0:y1, x0:x1].astype(np.int16)
    mean_diff = abs(float(band_on.mean()) - float(band_off.mean())) / 255.0
    panel.close()
    assert mean_diff > 10.0 / 255.0, mean_diff
    # off: video showing through, not a filled rect
    assert float(band_off.astype(np.float64).var()) > 50.0, float(band_off.var())


def test_bug22_lyrics_independent(qapp):
    import numpy as np
    from cutora.subtitle.stylepack import apply_pack_to_task, builtin_dir, load_pack
    from cutora.model.project_io import deserialize_render_task
    data = json.loads(open(G11, encoding="utf-8").read())
    task = deserialize_render_task(data["tasks"][0])
    pack = load_pack(os.path.join(builtin_dir(), "lyrics-scroll"))
    apply_pack_to_task(task, pack)
    st = task.get("style") or {}
    fx = st.get("fx") if isinstance(st.get("fx"), dict) else {}
    anim = fx.get("anim") if isinstance(fx.get("anim"), dict) else {}
    st["lyrics_scroll_enabled"] = True
    st["advanced_textbox_enabled"] = False
    if isinstance(fx.get("bg"), dict):
        fx["bg"]["type"] = "none"
    assert st.get("lyrics_scroll_enabled") is True
    assert anim.get("loop") == "scroll"
    src = open(os.path.join(REPO, "cutora", "render", "frame_renderer.py"),
               encoding="utf-8").read()
    assert "advanced_textbox_enabled" not in src.split("Check for Scrolling Lyrics Mode", 1)[1][:400]
    ss = open(os.path.join(REPO, "cutora", "ui", "panels", "subtitle_style.py"),
              encoding="utf-8").read()
    assert "lyrics_group.setVisible" not in ss
    on_img = _paint_canvas(qapp, task, 2.0)
    st["lyrics_scroll_enabled"] = False
    off_img = _paint_canvas(qapp, task, 2.0)
    on_a, off_a = _rgb(on_img), _rgb(off_img)
    d = np.abs(on_a.astype(np.int16) - off_a.astype(np.int16))
    rows = np.where((d.max(axis=2) > 25).any(axis=1))[0]
    assert rows.size > 40, int(rows.size)
    span = int(rows.max() - rows.min()) if rows.size else 0
    assert span > 80, span


def test_bug23_no_orphan_labels(qapp):
    import cutora.ui.panels.property  # noqa: F401
    from cutora.ui.panels.subtitle_style import SubtitleStylePanel
    from cutora.ui.widgets.dock_panels import CollapsibleSection
    from PySide6.QtWidgets import QFormLayout, QWidget
    panel = SubtitleStylePanel()
    panel.load_data({"fontsize": 72, "fx": {"bg": {"type": "glass"}}})
    for sec in panel.findChildren(CollapsibleSection):
        if hasattr(sec, "set_collapsed"):
            sec.set_collapsed(False)
    panel.show()
    qapp.processEvents()
    orphans = []
    for form in panel.findChildren(QFormLayout):
        for i in range(form.rowCount()):
            lab_item = form.itemAt(i, QFormLayout.ItemRole.LabelRole)
            field_item = form.itemAt(i, QFormLayout.ItemRole.FieldRole)
            lab = lab_item.widget() if lab_item else None
            field = field_item.widget() if field_item else None
            if lab is not None and lab.isVisible() and (lab.text() or "").strip():
                if field is None:
                    lay = field_item.layout() if field_item else None
                    if lay is not None:
                        continue
                    orphans.append(lab.text())
                elif not field.isVisible():
                    orphans.append(lab.text())
    panel.close()
    assert orphans == [], orphans


def test_bug24_glass_linear_h_and_multiply(qapp):
    import numpy as np
    task = _glass_task()
    bg = task["style"]["fx"]["bg"]
    bg["type"] = "glass"
    bg["grad_type"] = "linear_h"
    bg["grad_colors"] = ["#FF0000", "#0000FF"]
    bg["alpha"] = 0.7
    bg["blend"] = "normal"
    from cutora.preview.engine import PreviewEngine
    eng = PreviewEngine()
    try:
        img_n = eng.frame_at(task, 2.0)
        bg["blend"] = "multiply"
        img_m = eng.frame_at(task, 2.0)
    finally:
        eng.close()
    n, m = _rgb(img_n), _rgb(img_m)
    y0, y1 = 1040, 1200
    left = n[y0:y1, 200:360].mean(axis=(0, 1))
    right = n[y0:y1, 720:880].mean(axis=(0, 1))
    assert abs(float(left[0]) - float(right[0])) > 5, (left, right)
    assert float(m[y0:y1, 200:880].mean()) < float(n[y0:y1, 200:880].mean())


def test_section_header_toggle_rule(qapp):
    import cutora.ui.panels.property  # noqa: F401
    from cutora.ui.panels.subtitle_style import SubtitleStylePanel
    from cutora.ui.widgets.dock_panels import CollapsibleSection
    from cutora.ui.widgets.collapsible import ToggleSwitch
    panel = SubtitleStylePanel()
    panel.load_data({"fx": {"bg": {"type": "none"}}})
    enable = {
        "弹出散落", "高级文本框",
        "滚动歌词", "动画效果",
        "3D 挤出", "背景纹理", "双字体", "自动着色",
    }
    param = {
        "字幕排版",
    }
    found = {}
    for sec in panel.findChildren(CollapsibleSection):
        title = getattr(sec, "section_title", "")
        found[title] = getattr(sec, "_toggle_cb", None) is not None
    panel.close()
    for t in enable:
        assert found.get(t) is True, (t, found)
    for t in param:
        assert found.get(t) is False, (t, found)
    src = open(os.path.join(REPO, "cutora", "ui", "widgets", "dock_panels.py"),
               encoding="utf-8").read()
    assert 'CollapsibleSection("背景渐变"' not in src
