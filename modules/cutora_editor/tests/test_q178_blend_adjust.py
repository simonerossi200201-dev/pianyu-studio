# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q178 · 调整轨道 × 轨道混合模式。

* 调整轨上面的视频轨用滤色 / 正片叠底：和「调整层 + 它下面所有东西」叠好的结果混合（不再整块盖住）；
* 调整轨自己也能设混合模式 / 不透明度（轨道头右键「混合模式」或轨道「合成」）：只在它的范围里起作用；
* 预览 = 播放整幅帧 = 画布 = 监视器 = 导出。"""
from __future__ import annotations

import os
import sys
import tempfile

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("CUTORA_SKIP_ENCODER_PROBE", "1")
os.environ.setdefault("CUTORA_USER_DATA", tempfile.mkdtemp(prefix="cutora_q178_"))
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

BLUE = (160, 64, 32)          # BGR：0x2040A0
RED = (32, 48, 192)           # BGR：#C03020


def _arr(img):
    from PySide6.QtGui import QImage
    q = img.convertToFormat(QImage.Format.Format_ARGB32)
    return np.frombuffer(bytes(q.constBits()), np.uint8).reshape(
        q.height(), q.bytesPerLine() // 4, 4)[:, :q.width(), :3].astype(int)


def _screen(a, b):
    return tuple(255 - (255 - x) * (255 - y) // 255 for x, y in zip(a, b))


def _multiply(a, b):
    return tuple(x * y // 255 for x, y in zip(a, b))


def _task(tmp_path, top_mode="screen", via_props=False, adj_mode=None, adj_props=False, adj_opacity=None):
    """视频1（蓝，最底）→ 调整（红色块在上半）→ 视频2（灰，混合模式）→ 文字（最上）。"""
    from test_q163_unified_stack import W, H, _clip, _task as base
    from cutora.model.timeline_model import Clip, MediaSource, Track
    from cutora.render import adjust_layer as AL
    task = base(tmp_path)
    tl = task["timeline"]
    v1, txt = tl.tracks
    adj = Track("adjust")
    v2 = Track("video")
    tl.tracks[:] = [v1, adj, v2, txt]
    tl.sources["g"] = MediaSource(_clip(tmp_path, "0x404040"), duration=2.0, probe=False)
    c = Clip("g", 0.0, 2.0, 0.0)
    if via_props:
        v2.props = {"blend_mode": top_mode}
    else:
        c.blend_mode = top_mode
    v2.clips.append(c)
    ov = AL.adjust_defaults(W, H, start=0.0, end=2.0, track_id=adj.id)
    ov.update({"x": 10, "y": 40, "w": 196, "h": 100})
    ov["solid"]["color"] = "#C03020"
    task["image_overlays"].append(ov)
    if adj_mode and adj_props:
        adj.props = {"blend_mode": adj_mode}
    elif adj_mode:
        adj.blend_mode = adj_mode
    if adj_opacity is not None:
        adj.props = dict(getattr(adj, "props", None) or {}, opacity=adj_opacity)
    return task


def _close(px, want, tol=3):
    return all(abs(int(a) - int(b)) <= tol for a, b in zip(px, want))


def _canvas(task, t=1.0, bands=None):
    from test_q163_unified_stack import W, H
    from PySide6.QtGui import QImage, QPainter
    from cutora.ui.canvas import VideoCanvas
    cv = VideoCanvas()
    cv._task_data = task
    cv._video_dims = (W, H)
    cv._current_time = t
    if bands is not None:
        cv.set_program_frame(bands)
    img = QImage(W, H, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(0)
    p = QPainter(img)
    try:
        cv._draw_preview_local(p, export_mode=bands is None)
    finally:
        p.end()
    return _arr(img)


def test_video_above_adjust_blends_with_everything_below(tmp_path):
    from test_q163_unified_stack import _app, _export_frame
    _app()
    from cutora.preview.engine import PreviewEngine
    from cutora.render.visual_stack import BandLayers
    eng = PreviewEngine()
    try:
        for via_props in (False, True):
            task = _task(tmp_path, "screen", via_props=via_props)
            exp = _arr(_export_frame(task))
            gray = (64, 64, 64)
            assert _close(exp[90, 108], _screen(RED, gray)), via_props           # 调整色块 ⊕ 灰：亮红，不是灰
            assert _close(exp[300, 108], _screen(BLUE, gray)), via_props         # 色块外：蓝 ⊕ 灰
            assert np.array_equal(_arr(eng.frame_at(task, 1.0)), exp), via_props            # 预览 = 导出
            assert np.array_equal(_arr(eng.display_frame_at(task, 1.0, 1.0)), exp), via_props  # 播放整幅帧
            bands = eng.video_bands_at(task, 1.0)
            assert not isinstance(bands[0], BandLayers) and isinstance(bands[1], BandLayers)
            assert [m for _i, m in bands[1]] == ["screen"]
            assert np.array_equal(_canvas(task), exp), via_props                  # 画布（自己算带）
            assert np.array_equal(_canvas(task, bands=bands), exp), via_props     # 画布（工作线程送来的带）
        # 普通模式：照旧整块盖住，不拆层
        task = _task(tmp_path, "normal")
        exp = _arr(_export_frame(task))
        assert _close(exp[90, 108], (64, 64, 64)) and _close(exp[300, 108], (64, 64, 64))
        assert np.array_equal(_arr(eng.frame_at(task, 1.0)), exp)
        assert not any(isinstance(b, BandLayers) for b in eng.video_bands_at(task, 1.0))
        # 正片叠底
        task = _task(tmp_path, "multiply")
        exp = _arr(_export_frame(task))
        assert _close(exp[90, 108], _multiply(RED, (64, 64, 64)))
        assert np.array_equal(_arr(eng.frame_at(task, 1.0)), exp)
        assert np.array_equal(_canvas(task), exp)
    finally:
        eng.close()


def test_adjust_track_own_blend_mode_and_opacity(tmp_path):
    from test_q163_unified_stack import _app, _export_frame
    _app()
    from cutora.preview.engine import PreviewEngine
    eng = PreviewEngine()
    gray = (64, 64, 64)
    try:
        cases = {
            ("multiply", False): _multiply(BLUE, RED),
            ("multiply", True): _multiply(BLUE, RED),          # 轨道「合成」里设的
            ("screen", False): _screen(BLUE, RED),
        }
        for (mode, props), inside in cases.items():
            task = _task(tmp_path, "screen", adj_mode=mode, adj_props=props)
            exp = _arr(_export_frame(task))
            assert _close(exp[90, 108], _screen(inside, gray), 4), (mode, props)   # 调整层先和下面混，再被上面滤色
            assert _close(exp[300, 108], _screen(BLUE, gray)), (mode, props)       # 范围外不受影响（不会整幅变亮）
            assert np.array_equal(_arr(eng.frame_at(task, 1.0)), exp), (mode, props)
            assert np.array_equal(_arr(eng.display_frame_at(task, 1.0, 1.0)), exp), (mode, props)
            assert np.array_equal(_canvas(task), exp), (mode, props)
        # 不透明度 50%：色块半透
        task = _task(tmp_path, "normal", adj_opacity=0.5)
        top = _arr(_export_frame(_task(tmp_path, "normal")))
        exp = _arr(_export_frame(task))
        assert np.array_equal(exp, top)                       # 上面普通视频全盖住：看不出
        task = _task(tmp_path, "screen", adj_opacity=0.5)
        exp = _arr(_export_frame(task))
        half = tuple((a + b) // 2 for a, b in zip(BLUE, RED))
        assert _close(exp[90, 108], _screen(half, gray), 4)
        assert np.array_equal(_arr(eng.frame_at(task, 1.0)), exp)
    finally:
        eng.close()


def test_central_monitor_tiles_match(tmp_path, monkeypatch):
    monkeypatch.setenv("CUTORA_USER_DATA", str(tmp_path / "ud"))
    from test_q163_unified_stack import W, H, _app
    app = _app()
    from test_q144_central_monitor import _dialog, _wait
    from cutora.preview.engine import PreviewEngine
    tasks = [_task(tmp_path, "screen"), _task(tmp_path, "screen", adj_mode="multiply")]
    eng = PreviewEngine()
    try:
        refs = [_arr(eng.frame_at(t, 1.0)) for t in tasks]
    finally:
        eng.close()
    dlg = _dialog(tasks)
    g = dlg.grid
    try:
        assert _wait(app, lambda: all(t.comp is not None and t.video is not None for t in g.tiles), 30)
        for _ in range(30):
            app.processEvents()
        for i, t in enumerate(g.tiles):
            a = _arr(t.comp.toImage().scaled(W, H))
            assert np.abs(a - refs[i]).mean() < 3, i
            assert np.abs(a[90, 108] - refs[i][90, 108]).max() < 12, i
            assert np.abs(a[300, 108] - refs[i][300, 108]).max() < 12, i
    finally:
        dlg.close()
