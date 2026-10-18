# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q141 批量文本框放宽 / Q142 文本缩放无损 + 边界不硬切 / Q143 批量视频分屏 / Q144 拖选。"""
from __future__ import annotations

import os
import shutil
import subprocess

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _app():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(["q141"])
    from cutora.ui.panels.fonts import load_embedded_fonts
    load_embedded_fonts()
    return app


def _ov(**kw):
    from cutora.model.factory_defaults import overlay_defaults
    from cutora.subtitle.text_layer import ensure_overlay_fx
    o = overlay_defaults("text", canvas_w=1080, canvas_h=1920)
    o.update({"content": "When God is leading a woman toward success",
              "x": 200, "y": 600, "w": 680, "start": 0, "end": 10})
    o.update(kw)
    ensure_overlay_fx(o)
    return o


def _paint(o):
    from PySide6.QtGui import QImage, QPainter
    from cutora.render.text_layer_paint import paint_text_overlay
    img = QImage(1080, 1920, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(0)
    p = QPainter(img)
    paint_text_overlay(p, dict(o), current_time=1.0, video_w=1080, video_h=1920)
    p.end()
    return np.frombuffer(bytes(img.constBits()), np.uint8).reshape(1920, 1080, 4).copy()


# ─────────── Q141 ───────────

def test_batch_text_box_widens_to_show_the_text():
    _app()
    from cutora.batch.text_batch import build_overlay
    from cutora.render.text_layer_paint import layout_for_overlay
    tpl = {"x": 390, "y": 1500, "w": 300, "h": 80, "fontsize": 60, "font_family": "Arimo"}
    short = build_overlay("Hi", 0, 5, width=1080, height=1920, template=dict(tpl))
    assert (short["x"], short["w"]) == (390, 300)
    mid = build_overlay("Pray for your family today", 0, 5, width=1080, height=1920,
                        template=dict(tpl))
    assert mid["w"] > 300
    lay = layout_for_overlay(dict(mid), 1080, 1920)[0]
    assert len(lay.lines) == 1
    assert abs((mid["x"] + mid["w"] / 2) - (390 + 150)) < 1.0 or mid["x"] >= 0
    assert mid["x"] >= 0 and mid["x"] + mid["w"] <= 1080 + 0.5
    assert mid["fit_mode"] == "auto_height"


# ─────────── Q142 ───────────

def test_scaled_text_is_rendered_not_upsampled():
    _app()
    from cutora.render.text_layer_paint import clear_static_cache
    clear_static_cache()
    a2 = _paint(_ov(scale=2.0))[..., 3]
    clear_static_cache()
    big = _paint(_ov(fontsize=_ov()["fontsize"] * 2, w=680 * 2, x=200 - 340,
                     y=600 - _ov()["h"] / 2 if _ov().get("h") else 600))[..., 3]
    # 放大 2 倍的「部分透明边缘像素 / 实心像素」比例应当和直接用 2 倍字号画出来的差不多
    def edge_ratio(a):
        return ((a > 20) & (a < 235)).sum() / max(1, (a >= 235).sum())
    assert edge_ratio(a2) < edge_ratio(big) * 1.35 + 0.01, (edge_ratio(a2), edge_ratio(big))


def test_scaled_layout_keeps_line_breaks():
    _app()
    from cutora.render.text_layer_paint import layout_for_overlay, scaled_overlay
    for s in (0.5, 1.5, 3.0):
        o = _ov(scale=s)
        l1 = layout_for_overlay(dict(o), 1080, 1920)[0]
        l2 = layout_for_overlay(scaled_overlay(o, s), int(round(1080 * s)), int(round(1920 * s)))[0]
        t1 = [" ".join(r.text for r in ln.runs) for ln in l1.lines]
        t2 = [" ".join(r.text for r in ln.runs) for ln in l2.lines]
        assert t1 == t2, (s, t1, t2)


def test_text_crossing_canvas_edge_is_not_cut_when_scaled_down():
    """框一半伸出左边，缩到 0.5 倍后整段应当完整回到画面里（以前在 x=0 硬切）。"""
    _app()
    from cutora.render.text_layer_paint import clear_static_cache
    clear_static_cache()
    o = _ov(x=-300, content="WWWWWWWWWW WWWW", scale=0.5)
    a = _paint(o)[..., 3]
    cols = np.where(a.max(axis=0) > 0)[0]
    # 缩放中心 = 框中心；左半截原先在画布外 —— 现在应当有字画在原来被切的位置左侧
    center = -300 + o["w"] / 2
    left_edge_scaled = center + (0 - center) * 0.5
    assert cols.min() < left_edge_scaled - 5, (cols.min(), left_edge_scaled)


# ─────────── Q143 ───────────

def _clip(tmp_path, color="0x3070C0", dur=3):
    from cutora.core.config import FFMPEG_PATH
    ff = FFMPEG_PATH if os.path.isfile(FFMPEG_PATH) else shutil.which("ffmpeg")
    if not ff:
        pytest.skip("no ffmpeg")
    out = str(tmp_path / ("c_%s.mp4" % color[2:]))
    subprocess.run([ff, "-v", "error", "-y", "-f", "lavfi", "-i",
                    "color=c=%s:s=108x192:r=30:d=%d" % (color, dur), "-pix_fmt", "yuv420p", out],
                   check=True)
    return out


def _task(path, dur=6.0):
    from cutora.model.timeline import Timeline
    from cutora.model.timeline_model import Clip, MediaSource, Track
    tl = Timeline(108, 192, 30)
    tr = Track("video")
    tr.is_main_video = True
    tl.add_track(tr)
    tl.sources["m"] = MediaSource(path, duration=dur, probe=False)
    tr.clips.append(Clip("m", 0.0, dur, 0.0))
    return {"timeline": tl, "video": path, "dims": (108, 192), "image_overlays": [],
            "text_overlays": []}


@pytest.mark.parametrize("layout", ["top_bottom", "left_right", "grid_2x2", "left1_right2"])
def test_batch_video_inserts_real_clip_and_splits(tmp_path, layout):
    _app()
    from cutora.batch.script_insert_image import insert_into_tasks
    from cutora.batch.split_assign import layout_cells
    main = _clip(tmp_path, "0xC03030", 6)
    ins = _clip(tmp_path, "0x30C030", 2)
    task = _task(main)
    cells = layout_cells(layout)
    stats = insert_into_tasks([task], ins, start_sec=0.0, end_sec=6.0, split={
        "layout": layout, "main_cell": cells[0], "insert_cell": cells[-1],
        "border": {"style": "solid", "width_px": 4}, "feather": 0})
    assert stats["placed"] == 1
    assert task["image_overlays"] == []           # 不再塞进图片覆层
    tl = task["timeline"]
    batch_tr = [t for t in tl.tracks if getattr(t, "name", "") == "批量视频"][0]
    clip = batch_tr.clips[0]
    assert clip.loop and abs(clip.effective_duration - 6.0) < 1e-6
    assert task["split"]["enabled"] and task["split"]["layout"] == layout
    main_tr = [t for t in tl.tracks if getattr(t, "is_main_video", False)][0]
    assert main_tr.split_cell == cells[0] and batch_tr.split_cell == cells[-1]
    # 再插一次（replace）不叠第二个片段
    insert_into_tasks([task], ins, start_sec=0.0, end_sec=6.0)
    assert len(batch_tr.clips) == 1


def test_link_import_fills_cells_in_order(tmp_path):
    _app()
    from cutora.batch import link_import as biz
    main = _clip(tmp_path, "0xC03030", 6)
    a = _clip(tmp_path, "0x30C030", 2)
    b = _clip(tmp_path, "0x3030C0", 2)
    task = _task(main)
    plan = [{"task_index": 0, "paths": [a, b]}]
    stats = biz.apply_plan(plan, [task], split_layout="grid_2x2")
    assert stats["split_skipped"] == 0
    cells = sorted(str(t.split_cell) for t in task["timeline"].tracks if t.split_cell)
    assert cells == ["A", "B", "C"]


# ─────────── Q144 拖选 ───────────

def test_task_picker_drag_paints_rows():
    app = _app()
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from cutora.batch.script_ui import TaskPicker
    tasks = [{"video": "v%d.mp4" % i} for i in range(8)]
    p = TaskPicker(tasks)
    p.resize(300, 400)
    p.show()
    app.processEvents()
    vp = p.list.viewport()
    notes = []
    p.list.itemChanged.connect(lambda *_a: notes.append(1))

    def ev(kind, row, btn=Qt.MouseButton.LeftButton):
        r = p.list.visualItemRect(p.list.item(row))
        pos = QPointF(r.center())
        e = QMouseEvent(kind, pos, pos, btn,
                        btn if kind != QEvent.Type.MouseButtonRelease else Qt.MouseButton.NoButton,
                        Qt.KeyboardModifier.NoModifier)
        p.eventFilter(vp, e)

    ev(QEvent.Type.MouseButtonPress, 1)       # 1 勾 → 不勾
    ev(QEvent.Type.MouseMove, 3)
    ev(QEvent.Type.MouseMove, 5)
    ev(QEvent.Type.MouseButtonRelease, 5)
    assert p.selected_indices() == [0, 6, 7]
    assert len(notes) == 1                     # 整次拖选只通知一次
    ev(QEvent.Type.MouseButtonPress, 2)       # 不勾 → 勾，往上拖
    ev(QEvent.Type.MouseMove, 1)
    ev(QEvent.Type.MouseButtonRelease, 1)
    assert p.selected_indices() == [0, 1, 2, 6, 7]
