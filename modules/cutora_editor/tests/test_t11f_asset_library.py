# -*- coding: utf-8 -*-
"""T11f start: left-dock asset library is a real panel, not a placeholder label."""
from __future__ import annotations

import os

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_main_window_no_longer_placeholder_label():
    src = open(
        os.path.join(REPO, "cutora", "ui", "main_window.py"), encoding="utf-8"
    ).read()
    assert "资产库将在 T11 提供" not in src
    assert "AssetLibraryPanel" in src


def test_asset_library_has_nine_categories():
    from cutora.ui.panels.asset_library import CATEGORIES, AssetLibraryPanel
    assert len(CATEGORIES) == 7
    keys = [k for k, _ in CATEGORIES]
    assert keys == [
        "element", "frame", "lower_third", "sticker", "animated", "video", "audio",
    ]
    assert AssetLibraryPanel is not None


def test_asset_library_empty_state_offscreen():
    import subprocess
    import sys
    py = os.path.join(REPO, "runtime", "venv", "Scripts", "python.exe")
    if not os.path.isfile(py):
        py = sys.executable
    env = dict(os.environ)
    env["PYTHONPATH"] = REPO + os.pathsep + env.get("PYTHONPATH", "")
    env["QT_QPA_PLATFORM"] = "offscreen"
    code = (
        "from cutora.core.runtime import init_torch_runtime\n"
        "init_torch_runtime(render_child=False)\n"
        "from PySide6.QtWidgets import QApplication\n"
        "from cutora.ui.panels.asset_library import AssetLibraryPanel, CATEGORIES\n"
        "app = QApplication.instance() or QApplication(['t11f'])\n"
        "p = AssetLibraryPanel()\n"
        "p.show()\n"
        "assert p.tabs.count() == len(CATEGORIES) == 7\n"
        "assert p.list.count() >= 120\n"
        "assert not p.empty.isVisible()\n"
        "p._switch_cat('video')\n"
        "assert p.empty.isVisible()\n"
        "print('ok')\n"
    )
    r = subprocess.run(
        [py, "-c", code], cwd=REPO, env=env,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=60,
    )
    out = (r.stdout or "") + "\n" + (r.stderr or "")
    assert r.returncode == 0 and "ok" in (r.stdout or ""), out[-3000:]


def test_builtin_elements_120_and_stickers_100_and_animated():
    from cutora.assets import library

    library.reload_cache()
    elements = library.items_for_category("element")
    stickers = library.items_for_category("sticker")
    animated = library.items_for_category("animated")
    audio_items = library.items_for_category("audio")

    assert len(elements) >= 120, f"Expected >=120 elements, got {len(elements)}"
    assert len(stickers) >= 100, f"Expected >=100 stickers, got {len(stickers)}"

    el_gifs = [it for it in elements if str(it.get("file", "")).lower().endswith(".gif") or it.get("animated")]
    st_gifs = [it for it in stickers if str(it.get("file", "")).lower().endswith(".gif") or it.get("animated")]
    assert len(el_gifs) >= 30, f"Expected >=30 animated GIF elements, got {len(el_gifs)}"
    assert len(st_gifs) >= 25, f"Expected >=25 animated GIF stickers, got {len(st_gifs)}"
    assert len(animated) >= len(el_gifs) + len(st_gifs)

    # Sub-category filtering
    sub_cta = library.items_for_category("element", subcat="互动号召")
    assert len(sub_cta) >= 10
    sub_vfx = library.items_for_category("sticker", subcat="动态特效GIF")
    assert len(sub_vfx) >= 20

    # Audio category includes builtin BGM + SFX
    bgm_items = library.items_for_category("audio", subcat="内置BGM")
    assert len(bgm_items) >= 5
    assert len(audio_items) >= len(bgm_items)


def test_place_border_lower_third_gif_and_cross_track_and_property():
    import numpy as np
    from PySide6.QtWidgets import QApplication
    from cutora.assets.user import place_on_timeline
    from cutora.assets import library
    from cutora.model.timeline_model import Track
    from cutora.render.frame_renderer import FrameRenderer
    from cutora.ui.panels.property import PropertyPanel

    app = QApplication.instance() or QApplication(["t11f_unit"])
    _ = app

    vid_track = Track("video")
    vid_track.name = "视频 1"
    vid_track.is_main_video = True
    txt_track = Track("text")
    txt_track.name = "文本 1"
    img_track = Track("image")
    img_track.name = "贴图 1"
    tracks = [vid_track, txt_track, img_track]

    class _DummyTL:
        def __init__(self, trks):
            self.tracks = list(trks)
            self.sources = {}
        def add_track(self, tr):
            self.tracks.append(tr)

    tl = _DummyTL(tracks)
    task = {
        "timeline": tl,
        "text_overlays": [],
        "image_overlays": [],
    }

    # 1. Place border_spec on a video track (cross-track freedom)
    border_item = {
        "id": "border_test_01",
        "name": "欧式鎏金雕花框",
        "type": "frame",
        "border_spec": {
            "style": "vintage_filigree",
            "thickness": 8,
            "length_ratio": 0.85,
            "corner_radius": 16,
            "color_mode": "gold_gradient",
            "primary_color": "#F5C451",
            "secondary_color": "#FFF2B2",
            "target_link": "none",
        },
    }
    b_ov = place_on_timeline(tl, border_item, start_t=1.0, task=task, target_track=vid_track)
    assert isinstance(b_ov, dict)
    assert b_ov["_track_id"] == vid_track.id
    assert b_ov["border_spec"]["thickness"] == 8

    # 2. Place lower_third_spec on video track (cross-track freedom)
    lt_item = {
        "id": "lt_test_01",
        "name": "赛博霓虹双行条",
        "type": "lower_third",
        "lower_third_spec": {
            "preset_id": "cyber_hud_01",
            "lines": ["主讲人：Alex", "AI 视觉创意总监", "CUTORA Studio"],
            "primary_color": "#00F5D4",
            "secondary_color": "#7B2CBF",
            "text_color": "#FFFFFF",
            "show_icon": True,
            "anim_dur": 0.5,
        },
    }
    lt_ov = place_on_timeline(tl, lt_item, start_t=1.0, task=task, target_track=vid_track)
    assert isinstance(lt_ov, dict)
    assert lt_ov["_track_id"] == vid_track.id
    assert "AI 视觉创意总监" in lt_ov["content"]

    # 3. Place animated GIF element
    gif_item = next(
        it for it in library.items_for_category("element")
        if str(it.get("file", "")).lower().endswith(".gif")
    )
    gif_ov = place_on_timeline(tl, gif_item, start_t=1.0, task=task, target_track=vid_track)
    assert isinstance(gif_ov, dict)
    assert gif_ov.get("animated_gif") is True

    # 4. FrameRenderer renders border_spec, lower_third_spec, and animated GIF on cross-track
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QImage, QPainter

    img = QImage(540, 960, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    hits = []
    painter = QPainter(img)
    try:
        FrameRenderer.draw_layers(
            painter, task, 540, 960, 1.2,
            hit_recorder=hits, only_track=vid_track,
        )
    finally:
        painter.end()
    assert len(hits) >= 3, f"Expected >=3 hits on vid_track, got {len(hits)}"

    # 5. PropertyPanel shows ov_border_grp and ov_lower_third_grp and edits them
    prop = PropertyPanel()
    prop.load_data("overlay", b_ov)
    assert not prop.ov_border_grp.isHidden()
    assert prop.ov_lower_third_grp.isHidden()
    prop.ov_w.setValue(600)
    prop.on_change_ov()
    assert b_ov["w"] == 600
    assert b_ov["border_spec"]["thickness"] == 8  # thickness stays constant on resize!

    prop.load_data("overlay", lt_ov)
    assert not prop.ov_lower_third_grp.isHidden()
    assert prop.ov_border_grp.isHidden()
    prop.ov_lt_lines_edit.setPlainText("第一行标题\n第二行副标题\n第三行补充\n第四行角标")
    assert len(lt_ov["lower_third_spec"]["lines"]) == 4
    assert "第四行角标" in lt_ov["content"]


def test_cross_track_timeline_and_canvas_border_resize():
    from PySide6.QtCore import QRectF
    from PySide6.QtWidgets import QApplication
    from cutora.model.timeline_model import Track
    from cutora.ui.timeline.widget import TimelineWidget
    from cutora.ui.canvas import VideoCanvas

    app = QApplication.instance() or QApplication(["t11f_tl_canvas"])
    _ = app

    tlw = TimelineWidget()
    vid_tr = Track("video")
    vid_tr.name = "视频 1"
    img_tr = Track("image")
    img_tr.name = "贴图 1"
    txt_tr = Track("text")
    txt_tr.name = "文本 1"

    class _DummyTL:
        def __init__(self, trks):
            self.tracks = list(trks)
            self.sources = {}

    tlw.timeline = _DummyTL([vid_tr, img_tr, txt_tr])
    b_ov = {
        "id": "bd_cross_01",
        "type": "image",
        "name": "测试边框",
        "x": 20, "y": 20, "w": 400, "h": 300,
        "start": 0.0, "end": 4.0,
        "_track_id": vid_tr.id,
        "border_spec": {
            "style": "vintage_filigree",
            "thickness": 10,
            "length_ratio": 0.9,
            "target_link": "canvas",
        },
    }
    t_ov = {
        "id": "lt_cross_01",
        "type": "text",
        "content": "跨轨字幕条",
        "x": 40, "y": 500, "w": 500, "h": 140,
        "start": 0.5, "end": 3.5,
        "_track_id": vid_tr.id,
        "lower_third_spec": {"preset_id": "cyber_01", "lines": ["跨轨字幕条"]},
    }
    tlw.task_data = {
        "timeline": tlw.timeline,
        "image_overlays": [b_ov],
        "text_overlays": [t_ov],
    }

    # Both image and text overlays assigned to vid_tr appear on vid_tr
    assert (0, b_ov) in tlw._get_overlays_for_track(vid_tr, "image_overlays")
    assert (0, t_ov) in tlw._get_overlays_for_track(vid_tr, "text_overlays")
    assert (0, b_ov) not in tlw._get_overlays_for_track(img_tr, "image_overlays")

    # Simulate live cross-track drag of t_ov to img_tr
    tlw.mode = "move_overlay"
    tlw.selected_overlay = ("text_overlays", 0)
    tlw._drag_target_track = img_tr
    assert (0, t_ov) in tlw._get_overlays_for_track(img_tr, "text_overlays")
    assert (0, t_ov) not in tlw._get_overlays_for_track(vid_tr, "text_overlays")
    tlw.mode = None
    tlw.selected_overlay = None
    tlw._drag_target_track = None

    # Canvas border handles and non-distorting resize
    canvas = VideoCanvas()
    canvas._task_data = tlw.task_data
    item_rec = {"type": "overlay", "list": "image_overlays", "idx": 0}
    canvas._selected_item = item_rec
    canvas._selected_rect = QRectF(20, 20, 400, 300)
    handles = canvas._handle_rects_for(canvas._selected_rect, item_rec)
    assert "len_l" in handles and "len_r" in handles
    canvas._apply_resize(item_rec, 120.0, 80.0, "se")
    assert b_ov["w"] == 520
    assert b_ov["h"] == 380
    assert b_ov["border_spec"]["thickness"] == 10
    assert b_ov["border_spec"]["target_link"] == "none"

    # Dragging len_r handle adjusts length_ratio without changing w/h
    canvas._apply_resize(item_rec, -40.0, 0.0, "len_r")
    assert b_ov["w"] == 520
    assert b_ov["h"] == 380
    assert b_ov["border_spec"]["length_ratio"] < 0.9


