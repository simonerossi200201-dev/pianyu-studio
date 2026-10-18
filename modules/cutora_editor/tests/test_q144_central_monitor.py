# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q144 中央监视器：看图副本几何一致、分屏 / 格子 / 覆层编辑、跨视频搬文字、素材落位、撤销。"""
from __future__ import annotations

import os
import shutil
import subprocess
import time

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _app():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(["q144"])
    from cutora.ui.panels.fonts import load_embedded_fonts
    load_embedded_fonts()
    return app


def _clip(tmp_path, color="0x3070C0", dur=3, size="216x384"):
    from cutora.core.config import FFMPEG_PATH
    ff = FFMPEG_PATH if os.path.isfile(FFMPEG_PATH) else shutil.which("ffmpeg")
    if not ff:
        pytest.skip("no ffmpeg")
    out = str(tmp_path / ("c_%s_%s.mp4" % (color[2:], size)))
    if not os.path.isfile(out):
        subprocess.run([ff, "-v", "error", "-y", "-f", "lavfi", "-i",
                        "testsrc2=s=%s:r=30:d=%d" % (size, dur), "-vf",
                        "drawbox=c=%s@0.5:t=fill" % color, "-pix_fmt", "yuv420p", out],
                       check=True)
    return out


def _task(path, dur=3.0, w=216, h=384, sid="m"):
    from cutora.model.timeline import Timeline
    from cutora.model.timeline_model import Clip, MediaSource, Track
    tl = Timeline(w, h, 30)
    tr = Track("video")
    tr.is_main_video = True
    tl.add_track(tr)
    tl.sources[sid] = MediaSource(path, duration=dur, probe=False)
    tr.clips.append(Clip(sid, 0.0, dur, 0.0))
    return {"timeline": tl, "video": path, "dims": (w, h), "image_overlays": [],
            "text_overlays": []}


def _split_task(tmp_path, layout="top_bottom"):
    from cutora.batch.split_assign import assign_split
    from cutora.model.timeline_model import Clip, MediaSource, Track
    task = _task(_clip(tmp_path, "0xC03030"))
    tl = task["timeline"]
    tr2 = Track("video")
    tl.add_track(tr2)
    tl.sources["m2"] = MediaSource(_clip(tmp_path, "0x30C030"), duration=3.0, probe=False)
    tr2.clips.append(Clip("m2", 0.0, 3.0, 0.0))
    assign_split(task, layout, [tl.tracks[0], tr2], border={"style": "solid", "width_px": 6})
    return task


def _text_ov(task, content="Pray for your family", x=20, y=250, w=176, h=40):
    from cutora.model.factory_defaults import overlay_defaults
    from cutora.model.timeline_model import Track
    from cutora.subtitle.text_layer import ensure_overlay_fx
    tl = task["timeline"]
    tt = next((t for t in tl.tracks if t.type == "text"), None)
    if tt is None:
        tt = Track("text")
        tl.add_track(tt)
    o = overlay_defaults("text", canvas_w=216, canvas_h=384)
    o.update({"content": content, "x": x, "y": y, "w": w, "h": h, "start": 0.5, "end": 2.0,
              "fontsize": 18, "_track_id": tt.id, "id": 1234567})
    ensure_overlay_fx(o)
    task["text_overlays"].append(o)
    return o


def _arr(img):
    from PySide6.QtGui import QImage
    q = img.convertToFormat(QImage.Format.Format_ARGB32)
    a = np.frombuffer(bytes(q.constBits()), np.uint8).reshape(q.height(), q.bytesPerLine() // 4, 4)
    return a[:, :q.width()].astype(int)


# ─────────── 看图副本 ───────────

def test_view_task_matches_full_render_downscaled(tmp_path):
    _app()
    from cutora.batch import monitor_model as mm
    from cutora.preview.engine import PreviewEngine
    task = _split_task(tmp_path)
    tl = task["timeline"]
    tl.tracks[1].props.update({"x": 30.0, "scale": 1.3})
    tl.tracks[0].clips[0].x = 12.0
    mm.set_divider_pos(task, "d1", 0.42)
    full = PreviewEngine().video_frame_at(task, 1.0)
    vt = mm.view_task(task, 0.5)
    assert vt["dims"] == (108, 192)
    small = PreviewEngine().video_frame_at(vt, 1.0)
    ref = full.scaled(small.width(), small.height())
    diff = np.abs(_arr(ref) - _arr(small))
    assert diff.mean() < 3.0, diff.mean()
    # 原工程一个字节都没动
    assert tl.tracks[1].props["x"] == 30.0 and tl.tracks[0].clips[0].x == 12.0
    assert mm.split_of(task)["border"]["width_px"] == 6


def test_view_task_keys_sources_by_path():
    """两个工程都叫 "m" 的素材指向不同文件 —— 看图副本里不能串。"""
    _app()
    from cutora.batch import monitor_model as mm
    a = _task("/tmp/a_q144.mp4")
    b = _task("/tmp/b_q144.mp4")
    ka = list(mm.view_task(a, 0.5)["timeline"].sources)
    kb = list(mm.view_task(b, 0.5)["timeline"].sources)
    assert ka != kb
    same = _task("/tmp/a_q144.mp4", sid="other")
    assert list(mm.view_task(same, 0.5)["timeline"].sources) == ka


def test_lazy_cap_does_not_reopen_for_cached_frame(tmp_path):
    _app()
    from cutora.batch import monitor_model as mm
    from cutora.ui.central_monitor import _make_engine, render_view_frame
    task = _task(_clip(tmp_path))
    eng = _make_engine()
    vt = mm.view_task(task, 0.5)
    assert render_view_frame(eng, vt, 1.0) is not None
    opened = []
    real_get = eng.pool.real.get

    def spy(key, path):
        opened.append(key)
        return real_get(key, path)
    eng.pool.real.get = spy
    eng.pool.real.clear()                          # 解码器被挤出池子
    img = render_view_frame(eng, mm.view_task(task, 0.5), 1.0)
    assert img is not None and not img.isNull()
    assert opened == []                            # 同一帧：不重开文件


def test_reopened_decoder_gives_right_frame(tmp_path):
    """解码器闲时关掉 / 被挤掉后重开，顺序往后取帧不能从第 0 帧 grab。"""
    _app()
    from cutora.batch import monitor_model as mm
    from cutora.ui.central_monitor import _make_engine, render_view_frame
    task = _task(_clip(tmp_path, dur=3))
    eng = _make_engine()
    assert render_view_frame(eng, mm.view_task(task, 0.5), 1.0) is not None
    eng.pool.clear()
    got = render_view_frame(eng, mm.view_task(task, 0.5), 1.2)
    ref = render_view_frame(_make_engine(), mm.view_task(task, 0.5), 1.2)
    early = render_view_frame(_make_engine(), mm.view_task(task, 0.5), 0.2)
    d_ref = np.abs(_arr(got) - _arr(ref)).mean()
    d_early = np.abs(_arr(got) - _arr(early)).mean()
    assert d_ref < 1.0 and d_early > d_ref + 1.0, (d_ref, d_early)


def test_preview_engine_evicted_decoder_seeks(tmp_path):
    """主预览同样的坑：PreviewEngine 的解码器池满了挤掉再重开，不能拿到第 0 帧附近的画面。"""
    _app()
    from cutora.preview.engine import CapturePool, PreviewEngine
    a = _task(_clip(tmp_path, dur=3), sid="sa")
    b = _task(_clip(tmp_path, "0x30C030", dur=3), sid="sb")
    eng = PreviewEngine()
    eng.pool = CapturePool(1)
    eng.video_frame_at(a, 1.0)
    eng.video_frame_at(b, 1.0)                      # 挤掉 a 的解码器
    got = eng.video_frame_at(a, 1.2)
    ref = PreviewEngine().video_frame_at(a, 1.2)
    assert np.abs(_arr(got) - _arr(ref)).mean() < 1.0


# ─────────── 分屏 / 格子 ───────────

def test_divider_snap_and_zoom_about_cursor(tmp_path):
    from cutora.batch import monitor_model as mm
    assert mm.snap_divider_pos(0.49, 0.02) == 0.5
    assert mm.snap_divider_pos(0.40, 0.02) == 0.40
    task = _task(_clip(tmp_path))
    tr = task["timeline"].tracks[0]
    c = tr.clips[0]
    c.x, c.y = 10.0, -6.0
    # 以相对格子中心 (30, 40) 的点为不动点放大 2 倍：那一点下的画面原地不动
    mm.zoom_content(tr, 2.0, 30.0, 40.0, snap_one=False)
    assert abs(c.scale - 2.0) < 1e-9
    # 画面上一点 p（相对片段中心 q = p - c）→ 新位置 c' + 2q；p = u 时应仍是 u
    ux, uy = 30.0, 40.0
    assert abs((c.x + 2.0 * (ux - 10.0)) - ux) < 1e-6
    assert abs((c.y + 2.0 * (uy + 6.0)) - uy) < 1e-6


def test_set_layout_keeps_occupants_and_swap(tmp_path):
    _app()
    from cutora.batch import monitor_model as mm
    task = _split_task(tmp_path)
    tl = task["timeline"]
    main, other = tl.tracks[0], tl.tracks[1]
    assert (main.split_cell, other.split_cell) == ("A", "B")
    assert mm.swap_cells(task, "A", "B")
    assert (main.split_cell, other.split_cell) == ("B", "A")
    mm.set_layout(task, "grid_2x2")
    assert (other.split_cell, main.split_cell) == ("A", "B")
    mm.set_layout(task, None)
    assert not mm.split_on(task)
    mm.set_layout(task, "left_right")
    assert mm.split_of(task)["layout"] == "left_right"
    assert {main.split_cell, other.split_cell} == {"A", "B"}


def test_paste_split_copies_dividers_and_cell_props(tmp_path):
    _app()
    from cutora.batch import monitor_model as mm
    src = _split_task(tmp_path)
    mm.set_divider_pos(src, "d1", 0.3)
    c1 = src["timeline"].tracks[1].clips[0]
    c1.x, c1.y, c1.scale = 10.0, -5.0, 1.5
    clip = mm.split_clipboard(src)
    dst = _task(_clip(tmp_path, "0x3030C0"))
    from cutora.model.timeline_model import Clip, MediaSource, Track
    tr2 = Track("video")
    dst["timeline"].add_track(tr2)
    dst["timeline"].sources["x"] = MediaSource(_clip(tmp_path, "0x30C030"), duration=3.0, probe=False)
    tr2.clips.append(Clip("x", 0.0, 3.0, 0.0))
    mm.paste_split(dst, clip)
    assert mm.split_on(dst)
    assert abs(mm.split_of(dst)["dividers"][0]["pos"] - 0.3) < 1e-6
    b = mm.cell_track(dst, "B")
    assert b is not None and b.clips[0].scale == 1.5 and b.clips[0].x == 10.0


# ─────────── Q145：格子平移不能把出格的原画面裁成黑 ───────────

def _cell_a_top_rows(task, rows=40):
    from cutora.preview.engine import PreviewEngine
    img = PreviewEngine().video_frame_at(task, 1.0)
    a = _arr(img)
    return a[:rows, :, :3]


def test_pan_cell_reveals_original_pixels_not_black(tmp_path):
    """上下分屏，上格往下拖 60 px：露出来的是原视频（出格部分），不是黑边。"""
    _app()
    from cutora.batch import monitor_model as mm
    task = _split_task(tmp_path)
    tr = mm.cell_track(task, "A")
    mm.pan_content(tr, 0.0, 60.0)
    top = _cell_a_top_rows(task)
    assert (top.max(axis=2) < 8).mean() < 0.05, (top.max(axis=2) < 8).mean()
    # 放大后再往下拖一大截：露出的仍然是原画面
    mm.reset_track_transform(tr)
    mm.zoom_content(tr, 1.5, 0.0, 0.0)
    mm.pan_content(tr, 0.0, 120.0)
    top = _cell_a_top_rows(task)
    assert (top.max(axis=2) < 8).mean() < 0.05


def test_legacy_track_offset_is_baked_into_clips(tmp_path):
    """Q144 存下来的轨道位移（会出黑边）→ 并进片段：画面等效、黑边消失。"""
    _app()
    from cutora.batch import monitor_model as mm
    task = _split_task(tmp_path)
    tr = mm.cell_track(task, "A")
    mm.set_track_props(tr, x=4.0, y=60.0, scale=1.2)
    black_before = (_cell_a_top_rows(task).max(axis=2) < 8).mean()
    assert black_before > 0.5                       # 老渲染：顶部是黑的
    assert mm.bake_task_split_geometry(task) == 1
    c = tr.clips[0]
    assert abs(c.y - 60.0) < 1e-9 and abs(c.x - 4.0) < 1e-9 and abs(c.scale - 1.2) < 1e-9
    assert not mm.track_has_geometry(tr)
    assert (_cell_a_top_rows(task).max(axis=2) < 8).mean() < 0.05
    # 下半部分（黑边以外）画面和原来一致：等效变换
    assert mm.bake_task_split_geometry(task) == 0   # 幂等


def test_monitor_open_fixes_legacy_black_edges(tmp_path):
    app = _app()
    from cutora.batch import monitor_model as mm
    task = _split_task(tmp_path)
    tr = mm.cell_track(task, "A")
    mm.set_track_props(tr, y=60.0)
    dlg = _dialog([task])
    try:
        assert not mm.track_has_geometry(tr) and abs(tr.clips[0].y - 60.0) < 1e-9
        dlg.grid.undo()                               # 可以撤销
        assert mm.track_has_geometry(tr)
    finally:
        dlg.close()
    app.processEvents()


# ─────────── 覆层 ───────────

def test_copy_overlay_between_videos_is_identical_and_rescaled(tmp_path):
    _app()
    from cutora.batch import monitor_model as mm
    a = _task(_clip(tmp_path))
    b = _task(_clip(tmp_path))
    ov = _text_ov(a)
    new = mm.copy_overlay_into(a, ov, b, dx=5, dy=7)
    assert new is b["text_overlays"][0] and new is not ov
    assert new["content"] == ov["content"] and new["fx"] == ov["fx"]
    assert new["id"] != ov["id"]
    assert (new["x"], new["y"]) == (ov["x"] + 5, ov["y"] + 7)
    tt_b = [t for t in b["timeline"].tracks if t.type == "text"]
    assert tt_b and new["_track_id"] == tt_b[0].id
    # 目标画布大一倍 → 整层放大一倍
    big = _task(_clip(tmp_path), w=432, h=768)
    new2 = mm.copy_overlay_into(a, ov, big)
    assert abs(new2["x"] - ov["x"] * 2) < 1e-6 and abs(new2["w"] - ov["w"] * 2) < 1e-6
    assert abs(float(new2["fontsize"]) - float(ov["fontsize"]) * 2) < 1e-6


def test_scale_overlay_keeps_anchor_and_delete(tmp_path):
    _app()
    from cutora.batch import monitor_model as mm
    a = _task(_clip(tmp_path))
    ov = _text_ov(a)
    cx0 = ov["x"] + ov["w"] / 2
    mm.scale_overlay(a, ov, 1.5, cx0, ov["y"] + ov["h"] / 2)
    assert abs(ov["scale"] - 1.5) < 1e-9
    assert abs(ov["x"] + ov["w"] / 2 - cx0) < 1e-6
    assert mm.delete_overlay(a, ov)
    assert a["text_overlays"] == []


def test_drop_image_and_video_into_cells(tmp_path):
    _app()
    from PySide6.QtGui import QColor, QImage
    from cutora.batch import monitor_model as mm
    png = str(tmp_path / "logo.png")
    img = QImage(100, 50, QImage.Format.Format_ARGB32)
    img.fill(QColor(200, 150, 60))
    img.save(png)
    task = _task(_clip(tmp_path))
    ov = mm.drop_image(task, png, 108, 192)
    el = ov["elements"][0]
    assert abs(el["x"] + el["w"] / 2 - 108) < 1e-6 and abs(el["y"] + el["h"] / 2 - 192) < 1e-6
    assert not ov["id"].startswith("ov_insert_")          # 不会被批量加载的「替换」清掉
    vid = _clip(tmp_path, "0x30C030")
    msg = mm.drop_video(task, vid, 100, 50)               # 上半 → 开分屏，新视频在上
    assert mm.split_on(task), msg
    main = mm.main_video_track(task)
    assert main.split_cell == "B" and mm.cell_track(task, "A") is not main
    # 再拖一段到主视频那一格：主视频挪走、新视频进 B（没空格 → 替换另一格再互换）
    vid2 = _clip(tmp_path, "0x3030C0")
    mm.drop_video(task, vid2, 100, 300)
    assert mm.cell_track(task, "B") is not main
    assert main.split_cell == "A"


def test_history_undo_redo_restores_everything(tmp_path):
    _app()
    from cutora.batch import monitor_model as mm
    task = _split_task(tmp_path)
    ov = _text_ov(task)
    h = mm.History()
    tok = h.begin([task])
    mm.set_divider_pos(task, "d1", 0.3)
    mm.move_overlay(task, ov, 10, 10)
    mm.zoom_content(task["timeline"].tracks[1], 2.0, 0.0, 0.0)
    mm.drop_video(task, _clip(tmp_path, "0x3030C0"), 100, 300)
    h.commit(tok, "x")
    after = (mm.split_of(task)["dividers"][0]["pos"], ov["x"])
    h.undo()
    assert abs(mm.split_of(task)["dividers"][0]["pos"] - 0.5) < 1e-6
    assert ov["x"] == 20 and task["text_overlays"][0] is ov
    assert task["timeline"].tracks[1].clips[0].scale == 1.0
    assert len(task["timeline"].tracks) == 3
    h.redo()
    assert (mm.split_of(task)["dividers"][0]["pos"], ov["x"]) == after


def test_left_right_fit_batch_undo_keeps_text_and_clip():
    """换左右完整只记分屏。撤销不把之后改过的文字、片段位置退回去。"""
    from cutora.batch import monitor_model as mm
    from cutora.model.timeline import Timeline
    from cutora.model.timeline_model import Clip, MediaSource, Track
    tl = Timeline(1080, 1920, 30)
    a = Track("video")
    a.is_main_video = True
    b = Track("video")
    tl.add_track(a)
    tl.add_track(b)
    tl.sources["a"] = MediaSource("a.mp4", duration=3, probe=False)
    tl.sources["b"] = MediaSource("b.mp4", duration=3, probe=False)
    a.clips.append(Clip("a", 0, 3, 0))
    b.clips.append(Clip("b", 0, 3, 0))
    a.clips[0].x = 4
    text = {"content": "标题", "x": 9}
    task = {"timeline": tl, "text_overlays": [text], "image_overlays": [], "dims": (1080, 1920)}
    h = mm.History()
    tok = h.begin([task], snap=mm.snapshot_split)
    assert tok[0][1]["kind"] == "split"
    mm.set_layout(task, "left_right_fit")
    h.commit(tok, "换分屏布局")
    assert mm.layout_name(task) == "left_right_fit"
    assert a.split_cell == "A" and b.split_cell == "B"
    text["x"] = 40
    a.clips[0].x = 15
    h.undo()
    assert mm.layout_name(task) is None
    assert a.split_cell is None and b.split_cell is None
    assert text["content"] == "标题" and text["x"] == 40
    assert a.clips[0].x == 15
    h.redo()
    assert mm.layout_name(task) == "left_right_fit"
    assert a.split_cell == "A" and b.split_cell == "B"
    assert text["x"] == 40 and a.clips[0].x == 15


# ─────────── 界面 ───────────

def _wait(app, cond, sec=10.0):
    end = time.perf_counter() + sec
    while time.perf_counter() < end:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.005)
    return False


def _dialog(tasks):
    from cutora.ui.central_monitor import CentralMonitorDialog

    class _Host:
        pass
    host = _Host()
    host.tasks = tasks
    dlg = CentralMonitorDialog(host, tasks)
    dlg.resize(1200, 800)
    dlg.show()
    return dlg


def test_monitor_renders_and_drags(tmp_path):
    app = _app()
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtTest import QTest
    from cutora.batch import monitor_model as mm
    tasks = [_split_task(tmp_path), _task(_clip(tmp_path)), _task(_clip(tmp_path, "0x30C030"))]
    ov = _text_ov(tasks[1])
    dlg = _dialog(tasks)
    g = dlg.grid
    try:
        assert _wait(app, lambda: all(t.video is not None and not t.comp_dirty for t in g.tiles))
        # 分界线：按住就跟手
        t0 = g.tiles[0]
        d = mm.divider_lines(t0.task)[0]
        p0 = t0.to_widget(*d["pts"][12])
        QTest.mousePress(g, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, p0.toPoint())
        target = QPoint(int(p0.x()), int(t0.to_widget(0, 0.3 * t0.ch).y()))
        QTest.mouseMove(g, target)
        QTest.mouseRelease(g, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, target)
        pos = mm.split_of(t0.task)["dividers"][0]["pos"]
        assert abs(pos - 0.3) < 0.03 or abs(pos - 1 / 3) < 1e-6, pos
        # Ctrl + 拖 = 缩放这一格（B 格，避开文字）
        c = t0.to_widget(60, 330)
        QTest.mousePress(g, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ControlModifier, c.toPoint())
        for i in range(1, 8):
            QTest.mouseMove(g, (c + QPointF(i * 5, -i * 5)).toPoint())
        QTest.mouseRelease(g, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ControlModifier,
                           (c + QPointF(35, -35)).toPoint())
        assert mm.cell_track(t0.task, "B").clips[0].scale > 1.2
        # 文字跨视频拖：#2 → #3，原模原样搬过去
        t1, t2 = g.tiles[1], g.tiles[2]
        assert _wait(app, lambda: bool(t1.hits))
        a = t1.to_widget(ov["x"] + ov["w"] / 2, ov["y"] + ov["h"] / 2)
        b = t2.to_widget(108, 100)
        QTest.mousePress(g, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, a.toPoint())
        for i in range(1, 11):
            QTest.mouseMove(g, (a + (b - a) * (i / 10.0)).toPoint())
        QTest.mouseRelease(g, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, b.toPoint())
        assert t1.task["text_overlays"] == []
        moved = t2.task["text_overlays"][0]
        assert moved["content"] == ov["content"]
        assert abs((moved["y"] + moved["h"] / 2) - 100) < 6
        # Delete 删掉，Ctrl+Z 回来
        QTest.keyClick(g, Qt.Key.Key_Delete)
        assert t2.task["text_overlays"] == []
        QTest.keyClick(g, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
        assert len(t2.task["text_overlays"]) == 1
        # 复制 → 选中全部 → 粘贴
        g.ov_sel = [(t2.idx, t2.task["text_overlays"][0])]
        QTest.keyClick(g, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
        QTest.keyClick(g, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
        QTest.keyClick(g, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
        assert [len(t.task["text_overlays"]) for t in g.tiles] == [1, 1, 2]
        # 同步编辑：多选里拖一条的分界线，别的同布局的一起变
        g._apply_layout(g.tiles[1:], "top_bottom")
        assert all(mm.split_on(t.task) for t in g.tiles)
        dlg.ck_link.setChecked(True)
        g.selected = {0, 1, 2}
        d = mm.divider_lines(t0.task)[0]
        p0 = t0.to_widget(*d["pts"][3])
        QTest.keyPress(g, Qt.Key.Key_N)                  # 按住 N：不吸附
        QTest.mousePress(g, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, p0.toPoint())
        target = QPoint(int(p0.x()), int(t0.to_widget(0, 0.62 * t0.ch).y()))
        QTest.mouseMove(g, target)
        QTest.mouseRelease(g, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, target)
        QTest.keyRelease(g, Qt.Key.Key_N)
        poss = [round(mm.split_of(t.task)["dividers"][0]["pos"], 3) for t in g.tiles]
        assert len(set(poss)) == 1 and abs(poss[0] - 0.62) < 0.03, poss
        # Esc 取消正在拖的
        before = mm.split_of(t0.task)["dividers"][0]["pos"]
        QTest.mousePress(g, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                         t0.to_widget(*mm.divider_lines(t0.task)[0]["pts"][3]).toPoint())
        QTest.mouseMove(g, QPoint(int(p0.x()), int(t0.to_widget(0, 0.2 * t0.ch).y())))
        QTest.keyClick(g, Qt.Key.Key_Escape)
        assert abs(mm.split_of(t0.task)["dividers"][0]["pos"] - before) < 1e-9
        QTest.mouseRelease(g, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                           QPoint(int(p0.x()), int(t0.to_widget(0, 0.2 * t0.ch).y())))
    finally:
        dlg.close()


def test_monitor_drop_media_on_selection(tmp_path):
    app = _app()
    from PySide6.QtGui import QColor, QImage
    from cutora.batch import monitor_model as mm
    tasks = [_task(_clip(tmp_path)) for _ in range(3)]
    png = str(tmp_path / "logo.png")
    img = QImage(60, 60, QImage.Format.Format_ARGB32)
    img.fill(QColor(90, 160, 200))
    img.save(png)
    dlg = _dialog(tasks)
    g = dlg.grid
    try:
        assert _wait(app, lambda: all(t.video is not None for t in g.tiles))
        g.selected = {0, 2}
        t0 = g.tiles[0]
        vids = [_clip(tmp_path, "0x30C030"), _clip(tmp_path, "0x3030C0")]
        g.drop_media(t0, t0.to_widget(108, 300), [png] + vids)
        assert len(tasks[0]["image_overlays"]) == 1 and len(tasks[2]["image_overlays"]) == 1
        assert tasks[1]["image_overlays"] == []
        assert mm.split_on(tasks[0]) and mm.split_on(tasks[2]) and not mm.split_on(tasks[1])
        # 两段视频分给两条：各拿一段
        def ins_path(task):
            tr = [t for t in mm.video_tracks(task) if t is not mm.main_video_track(task)][0]
            return task["timeline"].sources[tr.clips[0].source_id].path
        assert ins_path(tasks[0]) != ins_path(tasks[2])
        g.undo()
        assert tasks[0]["image_overlays"] == [] and not mm.split_on(tasks[0])
    finally:
        dlg.close()


def test_context_menu_batch_layout_includes_fit_and_skips_offscreen(tmp_path):
    """选中几条或一次改全部，菜单里有左右完整；画面外的格子不进解码队列。"""
    app = _app()
    from PySide6.QtWidgets import QMenu
    from cutora.batch import monitor_model as mm
    path = _clip(tmp_path)
    tasks = [_task(path) for _ in range(6)]
    tasks[0]["text_overlays"] = [{"content": "标题", "x": 12, "y": 20, "w": 40, "h": 16}]
    dlg = _dialog(tasks)
    g = dlg.grid
    try:
        app.processEvents()
        g.relayout = lambda *a, **k: None
        g.selected = {0, 1, 2}
        menu = QMenu(dlg)
        g._add_layout_menus(menu)
        actions = list(menu.actions())
        titles = [a.text() for a in actions]
        assert any("选中的 3 条" in t for t in titles)
        assert any("全部 6 条" in t for t in titles)
        sub_act = next(a for a in actions if a.menu() is not None and "选中的 3" in a.text())
        sub = sub_act.menu()
        sub_actions = list(sub.actions())
        labels = [a.text() for a in sub_actions]
        # Q151：「左右」改叫「左右裁切」，和「左右完整」一眼分得开
        assert "左右裁切" in labels and "左右完整" in labels
        fit = next(a for a in sub_actions if a.text() == "左右完整")
        assert "完整" in (fit.toolTip() or "")
        for tile in g.tiles[3:]:
            tile.rect.moveTop(20000)
            tile.head.moveTop(20000)
        g._sched.stop()
        g._renderer.cancel_all()
        seen = []
        orig = g._renderer.request

        def spy(key, vt, t, gen, prio=1):
            seen.append(key)
            return orig(key, vt, t, gen, prio)

        g._renderer.request = spy
        g._apply_layout(g.selected_tiles(), "left_right_fit")
        for _ in range(8):
            app.processEvents()
        assert [mm.layout_name(t.task) for t in g.tiles[:3]] == ["left_right_fit"] * 3
        assert all(mm.layout_name(t.task) is None for t in g.tiles[3:])
        assert tasks[0]["text_overlays"][0]["content"] == "标题"
        off = {id(t) for t in g.tiles[3:]}
        assert seen and not (set(seen) & off)
        g._apply_layout(g.selected_tiles(), "left_right_fit")
        g.undo()
        assert mm.layout_name(tasks[0]) is None
        assert tasks[0]["text_overlays"][0]["x"] == 12
        g._apply_layout(list(g.tiles), "left_right_fit")
        assert all(mm.layout_name(t.task) == "left_right_fit" for t in g.tiles)
        assert tasks[0]["text_overlays"][0]["content"] == "标题"
    finally:
        dlg.close()


def test_batch_dialog_opens_monitor_and_follows_checks(tmp_path):
    app = _app()
    from PySide6.QtWidgets import QWidget
    from cutora.batch.batch_dialog import build_batch_dialog
    from cutora.ui.canvas import VideoCanvas
    tasks = [_task(_clip(tmp_path)) for _ in range(3)]
    host = QWidget()
    host.tasks = tasks
    host.curr_idx = 0
    host.video_canvas = VideoCanvas()
    host.timeline = QWidget()
    host.refresh_ui = lambda: None
    dlg = build_batch_dialog(host)
    dlg.show()
    try:
        lst = dlg.picker.list
        for i in range(lst.count()):
            dlg.picker._set_checked(lst.item(i), False)
        dlg.btn_monitor.click()
        mon = dlg.monitor
        assert mon is not None and mon.isVisible()
        assert len(mon.grid.tiles) == 3                  # 一条没勾 → 看全部
        dlg.picker._set_checked(lst.item(1), True)
        app.processEvents()
        assert [t.task for t in mon.grid.tiles] == [tasks[1]]
        mon.close()
    finally:
        dlg.close()


def test_monitor_follows_host_undo_replacing_tasks(tmp_path):
    """主窗口撤销会把 tasks 整体换成新对象 —— 监视器要改到新对象上，不能改孤儿。"""
    app = _app()
    import copy
    from cutora.batch import monitor_model as mm
    tasks = [_task(_clip(tmp_path)), _task(_clip(tmp_path))]
    dlg = _dialog(tasks)
    try:
        dlg.host.tasks = [copy.deepcopy(t) for t in tasks]      # 模拟主窗口撤销
        dlg.refresh_all()
        assert [t.task for t in dlg.grid.tiles] == dlg.host.tasks
        mm.pan_content(dlg.grid.tiles[0].task["timeline"].tracks[0], 5.0, 0.0)
        assert dlg.host.tasks[0]["timeline"].tracks[0].clips[0].x == 5.0
        assert tasks[0]["timeline"].tracks[0].clips[0].x == 0.0
    finally:
        dlg.close()
    app.processEvents()


def test_monitor_alt_drag_divider_rotates_and_ctrl_d_duplicates(tmp_path):
    app = _app()
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    from cutora.batch import monitor_model as mm
    task = _split_task(tmp_path)
    ov = _text_ov(task, y=40)
    dlg = _dialog([task])
    g = dlg.grid
    try:
        assert _wait(app, lambda: all(t.video is not None and not t.comp_dirty for t in g.tiles))
        t0 = g.tiles[0]
        d = mm.divider_lines(t0.task)[0]
        p0 = t0.to_widget(*d["pts"][20])
        QTest.mousePress(g, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.AltModifier, p0.toPoint())
        QTest.mouseMove(g, QPoint(int(p0.x()), int(p0.y()) + 25))
        QTest.mouseRelease(g, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.AltModifier,
                           QPoint(int(p0.x()), int(p0.y()) + 25))
        rot = mm.split_of(task)["dividers"][0]["rotation_deg"]
        assert abs(rot) > 5, rot
        g.ov_sel = [(0, ov)]
        QTest.keyClick(g, Qt.Key.Key_D, Qt.KeyboardModifier.ControlModifier)
        assert len(task["text_overlays"]) == 2
        assert task["text_overlays"][1]["x"] == ov["x"] + 20
        g.undo()
        assert len(task["text_overlays"]) == 1
    finally:
        dlg.close()
    app.processEvents()
