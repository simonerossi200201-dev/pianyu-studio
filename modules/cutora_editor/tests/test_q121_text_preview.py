# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q121 · 文本分页点一行，右栏预览换成这一行；现场改的样式能存成模板。"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("CUTORA_SKIP_ENCODER_PROBE", "1")

import pytest

from cutora.batch.text_batch import plan_rows
from cutora.model.timeline import Timeline
from cutora.model.timeline_model import Track

# 第 2 行对得上 5.5s，第 3 行对得上 9.0s。起点不一样，预览时间才看得出是哪一行。
SEGS = [
    {"start": 0.0, "end": 2.0, "text": "大家好，欢迎回来。"},
    {"start": 2.0, "end": 5.5, "text": "今天我们讲一个很重要的题目。"},
    {"start": 5.5, "end": 9.0, "text": "希望你能坚持看完。"},
    {"start": 9.0, "end": 14.0, "text": "我们开始吧。"},
]


def _task(video, segs=None):
    tl = Timeline(1080, 1920, 30.0)
    tl.add_track(Track("video"))
    return {
        "timeline": tl,
        "segments": list(SEGS if segs is None else segs),
        "dims": (1080, 1920),
        "fps": 30.0,
        "duration": 20.0,
        "video": video,
        "name": os.path.basename(video),
        "text_overlays": [],
        "image_overlays": [],
        "style": {},
    }


def _app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(["q121"])


def _dialog(tasks):
    from PySide6.QtWidgets import QApplication, QWidget
    from cutora.batch.batch_dialog import build_batch_dialog
    _app()

    class Host(QWidget):
        def __init__(self):
            super().__init__()
            self.tasks = list(tasks)
            self.curr_idx = 0

    host = Host()
    dlg = build_batch_dialog(host)
    dlg._host = host
    dlg.resize(1440, 900)
    dlg.show()
    QApplication.processEvents()
    dlg.tabs.setCurrentWidget(dlg.text_tab)
    QApplication.processEvents()
    return dlg, host


def _fill(page, texts):
    from PySide6.QtWidgets import QApplication
    page.table.fill_from(0, list(texts))
    QApplication.processEvents()


def _click_row(page, row):
    """点表格这一行。走单元格点击，不是直接调预览函数。"""
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    from cutora.ui.batch_text_tab import COL_TEXT
    table = page.table
    item = table.item(row, COL_TEXT)
    assert item is not None
    table.scrollToItem(item)
    QApplication.processEvents()
    rect = table.visualRect(table.model().index(row, COL_TEXT))
    assert rect.isValid() and rect.width() > 2 and rect.height() > 2, rect
    QTest.mouseClick(table.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())
    QApplication.processEvents()
    if table.currentRow() != row:
        # 离屏时鼠标点可能偏一格。补一次「点中这一格」的当前格变化，
        # 仍然走 currentCellChanged，和键盘点到这一行是同一条路。
        table.setCurrentCell(row, COL_TEXT)
        QApplication.processEvents()
    assert table.currentRow() == row


def _plans(host, texts, page):
    rows = [
        {"task_idx": i, "text": text, "start": None, "dur": None}
        for i, text in enumerate(texts)
    ]
    return plan_rows(
        host.tasks, rows,
        auto_locate=bool(page.ck_auto.isChecked()),
        default_dur=float(page.sp_dur.value()),
    )


def test_clicking_a_row_previews_that_videos_frame():
    """点第 2 行：背景是第 2 条视频，时间是 plan_rows 给这一行的 start。"""
    texts = ["大家好，欢迎回来。", "希望你能坚持看完。", "我们开始吧。"]
    tasks = [
        _task(r"D:\x\row1.mp4"),
        _task(r"D:\x\row2.mp4"),
        _task(r"D:\x\row3.mp4"),
    ]
    dlg, host = _dialog(tasks)
    try:
        page = dlg.text_tab
        _fill(page, texts)
        _click_row(page, 1)
        plans = _plans(host, texts, page)
        assert plans[1]["start"] == pytest.approx(5.5)
        assert dlg._preview_task["video"] == tasks[1]["video"]
        assert dlg._preview_task["video"] != tasks[0]["video"]
        assert dlg.preview_canvas._current_time == pytest.approx(plans[1]["start"])
    finally:
        dlg.close()


def test_preview_shows_that_cells_text_and_only_one():
    """预览里正好一条，文字就是这一格；再点下一行还是一条，不会越点越多。"""
    from cutora.ui.batch_text_tab import COL_TEXT
    second = "希望你能坚持看完。\n还没说完"
    third = "我们开始吧。"
    texts = ["大家好，欢迎回来。", second, third]
    tasks = [
        _task(r"D:\x\a.mp4"),
        _task(r"D:\x\b.mp4"),
        _task(r"D:\x\c.mp4"),
    ]
    dlg, _host = _dialog(tasks)
    try:
        page = dlg.text_tab
        _fill(page, texts)
        _click_row(page, 1)
        ovs = dlg._preview_task["text_overlays"]
        assert len(ovs) == 1
        assert ovs[0]["content"] == page.table.item(1, COL_TEXT).text() == second
        _click_row(page, 2)
        ovs = dlg._preview_task["text_overlays"]
        assert len(ovs) == 1
        assert ovs[0]["content"] == page.table.item(2, COL_TEXT).text() == third
    finally:
        dlg.close()


def test_preview_does_not_land_on_the_real_task():
    """点了几行，工程里的 task 一条文本都不多出来。"""
    texts = ["大家好，欢迎回来。", "希望你能坚持看完。", "我们开始吧。"]
    tasks = [
        _task(r"D:\x\p1.mp4"),
        _task(r"D:\x\p2.mp4"),
        _task(r"D:\x\p3.mp4"),
    ]
    dlg, host = _dialog(tasks)
    try:
        page = dlg.text_tab
        _fill(page, texts)
        _click_row(page, 0)
        _click_row(page, 1)
        assert len(dlg._preview_task["text_overlays"]) == 1
        assert dlg._preview_task not in host.tasks
        for task in host.tasks:
            assert task.get("text_overlays") == []
            assert [tr.type for tr in task["timeline"].tracks] == ["video"]
    finally:
        dlg.close()


def test_template_box_is_loaded_verbatim_into_preview_and_apply(tmp_path, monkeypatch):
    """Q136：模板就是成品。存进模板的框（位置 / 宽高比例）、字号、颜色、缩放
    原样进预览，也原样加到每条视频上；这一页没有自己的字号 / 颜色 / 描边。"""
    from cutora.subtitle import text_preset as TP
    monkeypatch.setattr(TP, "_user_path", lambda: str(tmp_path / "text_presets.json"))
    monkeypatch.setattr(TP, "_legacy_path", lambda: str(tmp_path / "legacy.json"))
    TP.reset_runtime_state_for_tests()
    ov = {
        "type": "text", "content": "Sample", "x": 90, "y": 1500, "w": 900, "h": 220,
        "fontsize": 64, "font_family": "Arimo", "bold": True, "color": "#E81CFF",
        "use_stroke": True, "stroke_width": 6, "stroke_color": "#000000",
        "scale": 1.3, "rotation": 5, "fit_mode": "auto_height",
    }
    TP.save_preset(TP.preset_from_overlay(ov, 1080, 1920, name="Q136成品"))

    texts = ["When God is leading a woman toward success", "第二条", "第三条"]
    tasks = [_task(r"D:\x\s1.mp4"), _task(r"D:\x\s2.mp4"), _task(r"D:\x\s3.mp4")]
    dlg, host = _dialog(tasks)
    try:
        page = dlg.text_tab
        for attr in ("sp_fontsize", "cb_color", "sp_stroke", "btn_save_preset", "ck_asr"):
            assert not hasattr(page, attr), attr
        page.refill_presets("Q136成品")
        page.cb_preset.setCurrentIndex(page.cb_preset.findData("Q136成品"))
        _fill(page, texts)
        _click_row(page, 0)
        spec = page.current_preview()
        pv = spec["overlay"]
        assert abs(pv["x"] - 90) < 1 and abs(pv["y"] - 1500) < 1
        assert abs(pv["w"] - 900) < 1 and abs(pv["h"] - 220) < 1
        assert int(pv["fontsize"]) == 64 and pv["color"] == "#E81CFF"
        assert abs(float(pv["scale"]) - 1.3) < 1e-6 and float(pv["rotation"]) == 5
        assert pv["content"] == texts[0]
        page.apply()
        for task in host.tasks:
            got = task["text_overlays"][0]
            assert abs(got["x"] - 90) < 1 and abs(got["w"] - 900) < 1
            assert int(got["fontsize"]) == 64 and abs(float(got["scale"]) - 1.3) < 1e-6
    finally:
        dlg.close()


def _task_canvas(video, w, h):
    tl = Timeline(int(w), int(h), 30.0)
    tl.add_track(Track("video"))
    return {
        "timeline": tl,
        "segments": list(SEGS),
        "dims": (int(w), int(h)),
        "fps": 30.0,
        "duration": 20.0,
        "video": video,
        "name": os.path.basename(video),
        "text_overlays": [],
        "image_overlays": [],
        "style": {},
    }


def _near(color, r, g, b, tol=28):
    return (abs(color.red() - r) <= tol and abs(color.green() - g) <= tol
            and abs(color.blue() - b) <= tol and color.alpha() > 200)


def test_preset_box_scales_onto_each_canvas_and_preview_paints_it(tmp_path, monkeypatch):
    """1080 上存的框，套到 720 时按比例缩。右栏预览画的是这块底板，不是只有视频。"""
    from PySide6.QtWidgets import QApplication
    from cutora.subtitle import text_preset as TP
    monkeypatch.setattr(TP, "_user_path", lambda: str(tmp_path / "text_presets.json"))
    monkeypatch.setattr(TP, "_legacy_path", lambda: str(tmp_path / "legacy.json"))
    TP.reset_runtime_state_for_tests()
    ov = {
        "type": "text", "content": "Hi", "x": 90, "y": 1500, "w": 900, "h": 220,
        "fontsize": 64, "font_family": "Arimo", "bold": True, "color": "#FFFFFF",
        "scale": 1.0, "rotation": 0, "fit_mode": "auto_height",
        "text_layout": {"align": "center", "valign": "top",
                        "left_pad": 0, "right_pad": 0, "top_pad": 0, "bottom_pad": 0,
                        "line_spacing": 0, "letter_spacing": 0},
        "fx": {"bg": {"type": "box", "enabled": True, "color": "#00E05A",
                      "alpha": 1.0, "size_mode": "fit_text"}},
    }
    TP.save_preset(TP.preset_from_overlay(ov, 1080, 1920, name="跨画布"))
    tasks = [
        _task_canvas(r"D:\x\wide.mp4", 1080, 1920),
        _task_canvas(r"D:\x\narrow.mp4", 720, 1280),
    ]
    dlg, host = _dialog(tasks)
    try:
        page = dlg.text_tab
        page.refill_presets("跨画布")
        page.cb_preset.setCurrentIndex(page.cb_preset.findData("跨画布"))
        _fill(page, ["Hi", "Hi"])
        _click_row(page, 0)
        wide = page.current_preview()["overlay"]
        assert abs(wide["x"] - 90) < 1 and abs(wide["w"] - 900) < 1
        assert int(wide["fontsize"]) == 64
        _click_row(page, 1)
        narrow = page.current_preview()["overlay"]
        assert narrow["x"] == pytest.approx(60, abs=0.5)
        assert narrow["y"] == pytest.approx(1000, abs=0.5)
        assert narrow["w"] == pytest.approx(600, abs=0.5)
        assert narrow["h"] == pytest.approx(220 * 1280 / 1920, abs=0.5)
        assert narrow["fontsize"] == pytest.approx(64 * 720 / 1080)
        assert abs(float(narrow.get("scale") or 1.0) - 1.0) < 1e-6
        assert dlg.preview_canvas._video_dims == (720, 1280)
        bg = ((narrow.get("fx") or {}).get("bg") or {})
        assert bg.get("type") == "box" and str(bg.get("color") or "").upper() == "#00E05A"
        canvas = dlg.preview_canvas
        canvas.repaint()
        QApplication.processEvents()
        img = canvas.grab().toImage()
        assert img.width() > 20 and img.height() > 20
        dpr = float(canvas.devicePixelRatioF() or 1.0)
        vw, vh = canvas._video_dims
        scale = min(canvas.width() / vw, canvas.height() / vh)
        draw_w = int(vw * scale)
        draw_h = int(vh * scale)
        ox = (canvas.width() - draw_w) // 2
        oy = (canvas.height() - draw_h) // 2

        def _to_canvas(px, py):
            return (px / dpr - ox) / scale, (py / dpr - oy) / scale

        greens = []
        for py in range(0, img.height(), 2):
            for px in range(0, img.width(), 2):
                if _near(img.pixelColor(px, py), 0, 224, 90):
                    greens.append(_to_canvas(px, py))
        sx, sy = _to_canvas(
            int((ox + (narrow["x"] + narrow["w"] * 0.15) * scale) * dpr),
            int((oy + (narrow["y"] + 12) * scale) * dpr))
        sample = img.pixelColor(
            max(0, min(img.width() - 1, int((ox + 30 * scale) * dpr))),
            max(0, min(img.height() - 1, int((oy + (narrow["y"] + 8) * scale) * dpr))))
        assert greens, "预览没画出底板 sample=%s,%s,%s at canvas %s" % (
            sample.red(), sample.green(), sample.blue(), (round(sx, 1), round(sy, 1)))
        xs = [p[0] for p in greens]
        ys = [p[1] for p in greens]
        mid_x = (min(xs) + max(xs)) / 2.0
        mid_y = (min(ys) + max(ys)) / 2.0
        assert narrow["x"] <= mid_x <= narrow["x"] + narrow["w"]
        assert narrow["y"] - 20 <= mid_y <= narrow["y"] + narrow["h"]
        assert min(ys) > vh * 0.5
        page.apply()
        got_w = host.tasks[0]["text_overlays"][0]
        got_n = host.tasks[1]["text_overlays"][0]
        assert abs(got_w["x"] - 90) < 1 and abs(got_w["w"] - 900) < 1
        assert int(got_w["fontsize"]) == 64
        assert got_n["x"] == pytest.approx(60, abs=0.5)
        assert got_n["w"] == pytest.approx(600, abs=0.5)
        assert got_n["fontsize"] == pytest.approx(64 * 720 / 1080)
        assert got_n.get("source_preset_id")
    finally:
        dlg.close()


def test_empty_cell_does_not_throw_or_draw():
    """空格子点下去不抛异常，预览上也不留文字。"""
    texts = ["大家好，欢迎回来。", "希望你能坚持看完。", ""]
    tasks = [
        _task(r"D:\x\e1.mp4"),
        _task(r"D:\x\e2.mp4"),
        _task(r"D:\x\e3.mp4"),
    ]
    dlg, host = _dialog(tasks)
    try:
        page = dlg.text_tab
        _fill(page, texts)
        _click_row(page, 1)
        assert len(dlg._preview_task["text_overlays"]) == 1
        _click_row(page, 2)
        assert dlg._preview_task["text_overlays"] == []
        for task in host.tasks:
            assert task.get("text_overlays") == []
    finally:
        dlg.close()
