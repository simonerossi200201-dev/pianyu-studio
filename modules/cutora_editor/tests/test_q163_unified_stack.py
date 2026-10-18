# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q163 · 所有视觉轨道按时间线统一叠压：文字 / 字幕 / 图片轨挪到视频轨上面那一行（= 画面里更底下）就会被视频盖住。

约定：timeline.tracks 下标 0 = 最底层 = 时间线界面最上面那一行。"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("CUTORA_SKIP_ENCODER_PROBE", "1")
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

W, H = 216, 384


def _app():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(["q163"])
    from cutora.ui.panels.fonts import load_embedded_fonts
    load_embedded_fonts()
    return app


def _clip(tmp_path, color="0x2040A0", dur=2, size="216x384"):
    from cutora.core.config import FFMPEG_PATH
    ff = FFMPEG_PATH if os.path.isfile(FFMPEG_PATH) else shutil.which("ffmpeg")
    if not ff:
        pytest.skip("no ffmpeg")
    out = str(tmp_path / ("c_%s_%s.mp4" % (color[2:], size)))
    if not os.path.isfile(out):
        subprocess.run([ff, "-v", "error", "-y", "-f", "lavfi", "-i",
                        "color=c=%s:s=%s:r=25:d=%d" % (color, size, dur), "-pix_fmt", "yuv420p", out],
                       check=True)
    return out


def _task(tmp_path, text_below=False, half_video=False):
    """一条全屏蓝色视频 + 一条白色大字。text_below=True：文字轨在列表里排在视频轨前面（画面里更底下）。"""
    from cutora.model.factory_defaults import overlay_defaults
    from cutora.model.timeline import Timeline
    from cutora.model.timeline_model import Clip, MediaSource, Track
    from cutora.subtitle.text_layer import ensure_overlay_fx
    tl = Timeline(W, H, 25)
    v = Track("video")
    v.is_main_video = True
    tt = Track("text")
    tl.tracks[:] = [tt, v] if text_below else [v, tt]
    tl.sources["m"] = MediaSource(_clip(tmp_path), duration=2.0, probe=False)
    c = Clip("m", 0.0, 2.0, 0.0)
    if half_video:
        c.fit_mode = "fit"
        c.scale = 0.5
    v.clips.append(c)
    o = overlay_defaults("text", canvas_w=W, canvas_h=H)
    o.update({"content": "HELLO", "x": 20, "y": 150, "w": 176, "h": 80, "start": 0.0, "end": 2.0,
              "fontsize": 56, "color": "#FFFFFF", "_track_id": tt.id, "id": 77})
    ensure_overlay_fx(o)
    return {"timeline": tl, "video": tl.sources["m"].path, "dims": (W, H), "duration": 2.0,
            "text_overlays": [o], "image_overlays": [], "segments": [], "style": {}, "z_order_v2": True}


def _white(img, box=(20, 150, 176, 80)) -> int:
    from PySide6.QtGui import QImage
    q = img.convertToFormat(QImage.Format.Format_ARGB32)
    a = np.frombuffer(bytes(q.constBits()), np.uint8).reshape(q.height(), q.bytesPerLine() // 4, 4)[:, :q.width()]
    x, y, w, h = box
    roi = a[y:y + h, x:x + w, :3].astype(int)
    return int(((roi > 200).all(axis=2)).sum())


def test_stack_model_and_needs_stack(tmp_path):
    from cutora.render.visual_stack import build_stack, needs_stack, video_bands
    top = _task(tmp_path)
    st = build_stack(top)
    assert [k for k, _ in st] == ["video", "overlay"] and not needs_stack(top, stack=st)
    below = _task(tmp_path, text_below=True)
    st = build_stack(below)
    assert [k for k, _ in st] == ["overlay", "video"] and needs_stack(below, stack=st)
    assert len(video_bands(st)) == 1
    # 覆层类型交错（文本 / 图片 / 文本）也要逐层画
    from cutora.model.timeline_model import Track
    tl = top["timeline"]
    tl.tracks[:] = [tl.tracks[0], Track("text"), Track("image"), tl.tracks[1]]
    assert needs_stack(top)


def test_preview_engine_frame_follows_track_order(tmp_path):
    _app()
    from cutora.preview.engine import PreviewEngine
    eng = PreviewEngine()
    try:
        assert _white(eng.frame_at(_task(tmp_path), 1.0)) > 200          # 字在视频上面：看得见
        assert _white(eng.frame_at(_task(tmp_path, text_below=True), 1.0)) == 0   # 字在视频下面：被盖住
        # 视频缩小一半（四周露空）→ 字从视频外面露出来
        assert _white(eng.frame_at(_task(tmp_path, text_below=True, half_video=True), 1.0)) > 20
    finally:
        eng.close()


def _export_frame(task, t=1.0):
    from cutora.render.render_worker import RenderWorker
    from cutora.render.compose_parallel import ExportComposeCtx
    from cutora.model.track_guard import video_tracks_for_render
    from cutora.render.visual_stack import build_stack
    from cutora.render.frame_renderer import compute_export_render_order
    rw = RenderWorker(task, "/tmp/x.mp4", "quality", False, export_logs=False)
    rw.project = task
    from cutora.render.render_worker import _ExportCapPool
    tl = task["timeline"]
    paths = {sid: src.path for sid, src in tl.sources.items()}
    states = {}
    caps = _ExportCapPool(paths, {sid: 99.0 for sid in paths}, states)
    ctx = ExportComposeCtx(project=task, width=W, height=H, video_tracks=video_tracks_for_render(task),
                           video_caps=caps, frame_states=states, fallback_cap=None, overlay_path=None,
                           fallback_clip=None, render_order=compute_export_render_order(task),
                           overlay_index=None, task_glass=False, stack=build_stack(task))
    try:
        return rw._compose_export_frame(t, None, ctx)
    finally:
        try:
            caps.clear()
        except Exception:
            pass


def test_export_frame_follows_track_order(tmp_path):
    _app()
    assert _white(_export_frame(_task(tmp_path))) > 200
    assert _white(_export_frame(_task(tmp_path, text_below=True))) == 0


def test_canvas_and_monitor_follow_track_order(tmp_path, monkeypatch):
    monkeypatch.setenv("CUTORA_USER_DATA", str(tmp_path / "ud"))
    app = _app()
    from PySide6.QtGui import QImage, QPainter
    from cutora.ui.canvas import VideoCanvas
    for below, expect_visible in ((False, True), (True, False)):
        task = _task(tmp_path, text_below=below)
        cv = VideoCanvas()
        cv._task_data = task
        cv._video_dims = (W, H)
        cv._current_time = 1.0
        img = QImage(W, H, QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(0)
        p = QPainter(img)
        try:
            cv._draw_preview_local(p, export_mode=True)
        finally:
            p.end()
        n = _white(img)
        assert (n > 200) if expect_visible else (n == 0), (below, n)
        # 命中顺序跟画的顺序一致：字在下面时，最后登记的是视频
        if below:
            kinds = [str(r.get("type")) for r in cv._hit_records]
            assert kinds and kinds[-1] not in ("text", "text_overlay"), kinds
    from test_q144_central_monitor import _dialog, _wait
    tasks = [_task(tmp_path), _task(tmp_path, text_below=True)]
    dlg = _dialog(tasks)
    g = dlg.grid
    try:
        assert _wait(app, lambda: all(t.comp is not None and t.video is not None for t in g.tiles), 20)
        for _ in range(20):
            app.processEvents()
        a = g.tiles[0].comp.toImage()
        b = g.tiles[1].comp.toImage()
        sx = a.width() / float(W)
        box = (int(20 * sx), int(150 * sx), int(176 * sx), int(80 * sx))
        assert _white(a, box) > 20 and _white(b, box) == 0
    finally:
        dlg.close()


def test_migrate_old_order_and_new_video_track_placement(tmp_path):
    from cutora.model.timeline import Timeline
    from cutora.model.timeline_model import Track
    from cutora.render.visual_stack import migrate_old_order
    tl = Timeline(W, H, 25)
    v1, img, txt, subs, broll, aud = (Track("video"), Track("image"), Track("text"), Track("subs"),
                                      Track("video"), Track("audio"))
    tl.tracks[:] = [v1, img, txt, subs, broll, aud]
    task = {"timeline": tl}
    assert migrate_old_order(task) and task["z_order_v2"]
    assert tl.tracks == [v1, broll, img, txt, subs, aud]                # 老工程：视频回到覆层下面
    tl.tracks[:] = [txt, v1, aud]                                     # 用户排过的（有标记）不动
    assert not migrate_old_order(task) and tl.tracks == [txt, v1, aud]
    # 新加的视频轨：放在最上面那条视频之后，不盖住文字 / 字幕
    tl2 = Timeline(W, H, 25)
    a, t2, s2, au = Track("video"), Track("text"), Track("subs"), Track("audio")
    tl2.tracks[:] = [a, t2, s2, au]
    nv = Track("video")
    tl2.add_track(nv)
    assert tl2.tracks == [a, nv, t2, s2, au]
