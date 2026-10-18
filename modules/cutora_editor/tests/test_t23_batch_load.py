# -*- coding: utf-8 -*-
import os

from cutora.batch.batch_load import (
    TABS, apply_to_task, display_from_sec, drag_preview, frames_to_sec,
    make_text_overlay, normalize_params, scale_preview, sec_from_display,
)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_t23_skeleton_pages_range_anim_blend_write():
    assert TABS == ("image", "video")
    p = normalize_params({
        "tab": "image", "start_frame": 0, "end_frame": 20,
        "anim_in": "fade", "blend": "normal", "image": "a.png",
    })
    assert p["start_frame"] == 0 and p["end_frame"] == 20
    task = apply_to_task({"video": "x.mp4"}, p, fps=30.0)
    assert len(task["batch_load"]) == 1
    row = task["batch_load"][0]
    assert abs(row["end_sec"] - 20 / 30.0) < 1e-6
    assert row["anim_in"] == "fade"
    assert row["blend"] == "normal"
    assert "intro" not in row


def test_t23_zero_twenty_frames_is_thumbnail():
    start, end = frames_to_sec(0, 20, fps=30.0)
    assert abs(start) < 1e-9
    assert abs(end - 20 / 30.0) < 1e-9


def test_t23_preview_drag_and_scale():
    p = {"x": 0.0, "y": 0.0, "scale": 1.0}
    moved = drag_preview(p, dx_px=10, dy_px=-5, canvas_w=1080, canvas_h=1920,
                         view_w=360, view_h=640)
    assert abs(moved["x"] - 10 * (1080 / 360)) < 1e-6
    assert abs(moved["y"] - (-5) * (1920 / 640)) < 1e-6
    scaled = scale_preview(p, 1.08)
    assert abs(scaled["scale"] - 1.08) < 1e-6


def test_t23_add_text_reuses_t11_preset():
    ov = make_text_overlay(content="hello", start=0.0, end=0.66, dims=(1080, 1920))
    assert ov["type"] == "text"
    assert ov["content"] == "hello"
    from cutora.model.factory_defaults import default_text_box
    _x, _y, box_w, box_h = default_text_box(
        1080, 1920, fontsize=float(ov.get("fontsize") or 80), lines=1)
    assert ov["w"] == box_w and ov["h"] == box_h
    assert "fontsize" in ov or "font_family" in ov
    assert "text_layout" in ov
    task = apply_to_task(
        {"video": "x.mp4", "dims": (1080, 1920)},
        {"tab": "image", "start_frame": 0, "end_frame": 20, "text": "hello",
         "text_preset": ov.get("_preset_name") or ""},
        fps=30.0,
    )
    assert len(task.get("text_overlays") or []) == 1
    assert task["text_overlays"][0]["type"] == "text"


def test_t23_menu_renamed_intro_gone():
    src = open(
        os.path.join(REPO, "cutora", "ui", "main_window.py"), encoding="utf-8"
    ).read()
    chunk_start = src.find('batch_menu = menu.addMenu(tr("批量"))')
    chunk = src[chunk_start:chunk_start + 4000]
    assert "批量加载…" in chunk
    assert "批量插图…" not in chunk
    assert "开场素材…" not in chunk
    assert "插入缩略图" not in chunk
    assert "插入开场视频" not in chunk


def test_t23_dialog_title_and_tabs():
    src = open(
        os.path.join(REPO, "cutora", "batch", "batch_dialog.py"),
        encoding="utf-8",
    ).read()
    assert 'setWindowTitle("批量加载")' in src
    assert 'addTab(tab_img, "图片")' in src
    assert 'addTab(tab_vid, "视频")' in src
    assert "起始帧" in src
    assert "添加文本" in src
    assert "VideoCanvas" in src
    assert "BatchPreview" not in src
    assert "起始空余" not in src


def test_time_unit_roundtrip():
    fps = 30.0
    for frames in (0, 1, 20, 45, 90, 1800):
        sec = sec_from_display(frames, "frame", fps)
        as_sec = display_from_sec(sec, "sec", fps)
        as_min = display_from_sec(sec, "min", fps)
        as_fr = display_from_sec(sec, "frame", fps)
        assert abs(as_sec - sec) < 1e-12
        assert abs(as_fr - frames) < 1e-6
        back_min = sec_from_display(as_min, "min", fps)
        assert abs(back_min - sec) < 1e-9
        back_sec = sec_from_display(as_sec, "sec", fps)
        assert abs(back_sec - sec) < 1e-12
        back_fr = display_from_sec(back_min, "frame", fps)
        assert abs(back_fr - frames) < 1e-6
    # 存储统一是秒：1 秒 @30fps = 30 帧 = 1/60 分钟
    one = sec_from_display(1.0, "sec", fps)
    assert abs(one - 1.0) < 1e-12
    assert abs(display_from_sec(one, "frame", fps) - 30.0) < 1e-9
    assert abs(display_from_sec(one, "min", fps) - (1.0 / 60.0)) < 1e-12
    task = apply_to_task(
        {"video": "x.mp4"},
        {
            "tab": "image",
            "start_sec": sec_from_display(0, "frame", fps),
            "end_sec": sec_from_display(0.05, "min", fps),
            "head_pad_sec": sec_from_display(1, "frame", fps),
            "tail_pad_sec": sec_from_display(2, "frame", fps),
        },
        fps=fps,
    )
    row = task["batch_load"][0]
    assert abs(row["start_sec"] - (0.0 + 1 / 30.0)) < 1e-9
    assert abs(row["end_sec"] - (0.05 * 60.0 - 2 / 30.0)) < 1e-9
    assert abs(row["head_pad_sec"] - 1 / 30.0) < 1e-9
    assert abs(row["tail_pad_sec"] - 2 / 30.0) < 1e-9


def test_t23b_dialog_strings():
    src = open(
        os.path.join(REPO, "cutora", "batch", "batch_dialog.py"),
        encoding="utf-8",
    ).read()
    assert "查找其他位置" in src
    assert "开头留空" in src
    assert "结尾留空" in src
    assert "canvas_wh" in src
    assert "ImageThumbGrid" in src
    assert "VideoCanvas" in src
    bp = open(
        os.path.join(REPO, "cutora", "batch", "batch_preview.py"),
        encoding="utf-8",
    ).read()
    assert "peek_clip_thumb" in bp
    assert "class BatchPreview" not in bp
    assert "dw * sx" not in bp.replace(" ", "")
