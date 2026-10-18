# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q175 · 播放流畅（整幅画面在工作线程按显示像素合成，画布只贴图）+ 调整轨第三期：
描边生成动画、跟踪绑定调整图层、图层复制 / 粘贴 / 跨视频搬运支持调整图层。"""
from __future__ import annotations

import os
import sys
import tempfile
import time

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("CUTORA_SKIP_ENCODER_PROBE", "1")
os.environ.setdefault("CUTORA_USER_DATA", tempfile.mkdtemp(prefix="cutora_q175_"))
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _app():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(["q175"])
    from cutora.ui.panels.fonts import load_embedded_fonts
    load_embedded_fonts()
    return app


def _arr(img):
    from PySide6.QtGui import QImage
    q = img.convertToFormat(QImage.Format.Format_ARGB32)
    return np.frombuffer(bytes(q.constBits()), np.uint8).reshape(
        q.height(), q.bytesPerLine() // 4, 4)[:, :q.width(), :3].astype(int)


def _adjust_task(tmp_path, effect="mosaic", text_on_top=False):
    from test_q163_unified_stack import W, H, _task
    from cutora.model.timeline_model import Track
    from cutora.render import adjust_layer as AL
    task = _task(tmp_path)
    tl = task["timeline"]
    tr = Track("adjust")
    if text_on_top:
        tl.tracks.insert(1, tr)
    else:
        tl.tracks.append(tr)
    ov = AL.adjust_defaults(W, H, start=0.0, end=2.0, track_id=tr.id)
    ov.update({"x": 10, "y": 140, "w": 196, "h": 100, "id": 501})
    if effect:
        ov["solid"]["on"] = False
        ov["effect"].update({"on": True, "kind": effect, "amount": 60})
    task["image_overlays"].append(ov)
    return task, tr, ov


# ───────────────────────────── 播放：整幅帧 ─────────────────────────────

def test_display_frame_matches_export_compositor(tmp_path):
    _app()
    from PySide6.QtCore import Qt
    from cutora.preview.engine import PreviewEngine
    eng = PreviewEngine()
    try:
        for top in (False, True):
            task, _tr, _ov = _adjust_task(tmp_path, "blur", text_on_top=top)
            ref = eng.frame_at(task, 1.0)
            assert np.array_equal(_arr(eng.display_frame_at(task, 1.0, 1.0)), _arr(ref))      # 满分辨率：逐像素一样
            half = eng.display_frame_at(task, 1.0, 0.5)
            assert (half.width(), half.height()) == (108, 192)                                  # 按显示像素
            small_ref = ref.scaled(108, 192, Qt.AspectRatioMode.IgnoreAspectRatio,
                                   Qt.TransformationMode.SmoothTransformation)
            assert np.abs(_arr(half) - _arr(small_ref)).mean() < 4
    finally:
        eng.close()


def test_preview_shrink_only_when_painting_smaller():
    _app()
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QImage, QPainter
    from cutora.render.render_worker import RenderWorker
    frame = np.zeros((1920, 1080, 3), np.uint8)
    img = QImage(540, 960, QImage.Format.Format_ARGB32_Premultiplied)
    p = QPainter(img)
    try:
        f, w, h = RenderWorker._preview_shrink(p, {}, frame, QRectF(0, 0, 1080, 1920))
        assert (w, h) == (1080, 1920) and f is frame                     # 1:1 画：不动（暂停 / 导出）
        p.scale(0.25, 0.25)
        f, w, h = RenderWorker._preview_shrink(p, {}, frame, QRectF(0, 0, 1080, 1920))
        assert (w, h) == (270, 480) and f.shape[:2] == (480, 270)          # 1/4 显示：解码帧先减半两次
        f, w, h = RenderWorker._preview_shrink(p, {"ai_matte": {"session": "x"}}, frame, QRectF(0, 0, 1080, 1920))
        assert (w, h) == (1080, 1920)                                     # AI 抠像按源像素对缓存：不缩
    finally:
        p.end()


def test_canvas_pastes_full_frame_and_keeps_hits(tmp_path):
    _app()
    from PySide6.QtGui import QColor, QImage, QPainter
    from test_q163_unified_stack import W, H
    from cutora.preview.worker import FullFrame
    from cutora.ui.canvas import VideoCanvas
    task, _tr, _ov = _adjust_task(tmp_path)
    cv = VideoCanvas()
    cv._task_data = task
    cv._video_dims = (W, H)
    cv._current_time = 1.0

    def paint():
        img = QImage(W, H, QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(0)
        p = QPainter(img)
        try:
            cv._draw_preview_local(p)
        finally:
            p.end()
        return _arr(img)
    paint()
    hits = list(cv._hit_records)
    assert any(r.get("adjust") for r in hits)
    ff = QImage(W // 2, H // 2, QImage.Format.Format_ARGB32_Premultiplied)
    ff.fill(QColor(0, 200, 0))
    cv.set_program_frame(FullFrame(ff, 1.0))
    cv._playback_full = True
    a = paint()
    assert tuple(a[H // 2, W // 2]) == (0, 200, 0)                       # 播放中：只贴工作线程的整幅帧
    assert cv._hit_records == hits                                        # 命中记录沿用（播放中不重算）
    cv.set_program_frame(None)                                            # 普通帧来了 → 回到原来的画法
    assert cv._full_frame is None and not cv._playback_full


def test_worker_full_mode_and_stale_frames(tmp_path):
    app = _app()
    from cutora.preview.worker import FullFrame, PreviewWorker
    task, _tr, _ov = _adjust_task(tmp_path)
    w = PreviewWorker()
    got = []
    w.frame_ready.connect(lambda img, t, g: got.append((img, t)))
    w.start()
    try:
        w.request(task, 0.5, scale=0.5, full=True)
        end = time.time() + 20
        while time.time() < end and not got:
            app.processEvents()
            time.sleep(0.01)
        assert got and isinstance(got[0][0], FullFrame) and got[0][0].image.width() == 108
        assert w.cost > 0
        # 普通帧一直被新请求顶掉也不能一张都不送（以前播放卡住、画面不动的原因之一）
        got.clear()
        end = time.time() + 1.5
        k = 0
        while time.time() < end:
            w.request(task, 0.1 + 0.01 * k, scale=0.5)
            k += 1
            app.processEvents()
            time.sleep(0.002)
        assert got
    finally:
        w.shutdown()


def test_main_window_playback_uses_full_frames(tmp_path):
    from PySide6.QtWidgets import QApplication
    from test_q145b_timeline_shortcuts import _load
    from cutora.preview.worker import FullFrame
    task, _tr, _ov = _adjust_task(tmp_path)
    task.update({"video": task["video"], "overlays": [], "audio_clips": [], "src_volume": 1.0,
                 "intro_media": None, "video_overlay": {}, "audio_tracks": [], "guard_error": None, "text": ""})
    win, _tlw, task = _load(task)
    seen = []
    win._preview_worker.frame_ready.connect(lambda img, t, g: seen.append(isinstance(img, FullFrame)))
    win.toggle_play()
    if not win.timer.isActive():
        win.timer.start(33)
    try:
        end = time.time() + 3.0
        while time.time() < end and sum(seen) < 3:
            QApplication.processEvents()
            time.sleep(0.005)
        assert sum(seen) >= 3 and win.video_canvas._playback_full
    finally:
        if win._timeline_playing:
            win.toggle_play()
    seen.clear()
    end = time.time() + 3.0
    while time.time() < end and not (seen and not seen[-1]):
        win.sync_ui()
        QApplication.processEvents()
        time.sleep(0.02)
    assert seen and not seen[-1] and not win.video_canvas._playback_full    # 停下来：回到逐层画（可编辑）
    win.timer.stop()


# ───────────────────────────── 描边生成动画 ─────────────────────────────

def test_stroke_progress_and_masks():
    _app()
    from PySide6.QtGui import QImage
    from cutora.render import adjust_layer as AL
    ov = AL.adjust_defaults(300, 300, start=1.0, end=6.0, track_id="a")
    ov.update({"x": 50, "y": 50, "w": 200, "h": 200})
    assert AL.stroke_progress(ov, 1.5) is None                            # 没开边框 / 没开动画
    ov["solid"].update({"border_on": True, "border_width": 10, "border_color": "#FF0000", "radius": 0.2,
                        "stroke_in": 2.0, "stroke_out": 1.0, "color": "#0000FF", "fill_follow": True})
    assert AL.stroke_progress(ov, 1.0) == 0.0 and AL.stroke_progress(ov, 3.0) == 1.0
    assert AL.stroke_progress(ov, 2.0) == pytest.approx(0.5) and AL.stroke_progress(ov, 5.5) == pytest.approx(0.5)
    assert AL.stroke_progress(ov, 6.0) == 0.0

    def red_and_blue(t, **sol):
        o = dict(ov, solid=dict(ov["solid"], **sol))
        img = QImage(300, 300, QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(0xFF000000)
        AL.apply_item(img, dict(o, _stroke_p=AL.stroke_progress(o, t)))
        a = _arr(img)
        return int((a[..., 2] > 200).sum()), int((a[..., 0] > 100).sum())
    reds = [red_and_blue(t)[0] for t in (1.3, 1.8, 2.4, 3.5)]
    assert reds[0] < reds[1] < reds[2] < reds[3]                           # 一点点画出来
    assert red_and_blue(1.5)[1] == 0 and red_and_blue(3.5)[1] > 20000       # 填充等描边快画完才出来
    assert red_and_blue(1.5, fill_follow=False)[1] > 20000
    # 起笔位置 / 方向：顺时针从上边中间起笔，刚开始右半边有、左半边没有；向两边同时 → 两边都有
    o = dict(ov, solid=dict(ov["solid"], stroke_start="top", stroke_dir="cw", fill_follow=False, on=True,
                            opacity=0.0))
    img = QImage(300, 300, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(0xFF000000)
    AL.apply_item(img, dict(o, _stroke_p=0.1))
    a = _arr(img)
    assert (a[50:62, 155:250, 2] > 200).sum() > 50 and (a[50:62, 50:145, 2] > 200).sum() == 0
    img.fill(0xFF000000)
    AL.apply_item(img, dict(o, solid=dict(o["solid"], stroke_dir="both"), _stroke_p=0.1))
    a = _arr(img)
    assert (a[50:62, 155:250, 2] > 200).sum() > 30 and (a[50:62, 50:145, 2] > 200).sum() > 30


def test_adjust_panel_stroke_controls():
    _app()
    from cutora.render import adjust_layer as AL
    from cutora.ui.panels.adjust_panel import AdjustPanel
    ov = AL.adjust_defaults(1080, 1920, start=0, end=5, track_id="a")
    p = AdjustPanel()
    p.show()
    p.load(ov)
    assert not any(r.isVisible() for r in p._stroke_rows)
    p.border_on.setChecked(True)
    assert all(r.isVisible() for r in p._stroke_rows)
    p.stroke_in.setValue(1.5)
    p.stroke_dir.setCurrentText("both")
    p.stroke_start.setCurrentText("bottom")
    p.fill_follow.setChecked(True)
    sol = ov["solid"]
    assert (sol["stroke_in"], sol["stroke_dir"], sol["stroke_start"], sol["fill_follow"]) == (1.5, "both", "bottom", True)
    p.close()


# ───────────────────────────── 跟踪 ─────────────────────────────

def test_tracking_follow_drives_adjust_layer(tmp_path, monkeypatch):
    monkeypatch.setenv("CUTORA_TRACK_CACHE", str(tmp_path / "track"))
    from cutora.fx import track_bind as TB
    from cutora.fx import track_store as T
    T.invalidate()
    from test_q152e_tracking import _clip_task, _motion_translate
    from cutora.model.timeline_model import Track
    from cutora.render import adjust_layer as AL
    task, _vtr, clip, _txt = _clip_task()
    atr = Track("adjust")
    task["timeline"].add_track(atr)
    ov = AL.adjust_defaults(400, 200, start=0.0, end=2.0, track_id=atr.id)
    ov.update({"x": 150.0, "y": 80.0, "w": 60.0, "h": 40.0, "id": 9})
    task["image_overlays"].append(ov)
    mo = _motion_translate(T.new_session_id())
    b = TB.normalize_binding({"mode": "follow", "target": {"kind": "overlay", "list": "image_overlays", "id": 9},
                              "ref_frame": 0})
    res = TB.bake_follow(mo, clip, ov, b, 400, 200)
    ov["keyframes"] = {"x": res["paths"]["x"], "y": res["paths"]["y"]}
    (_i, _o, cur), = AL.active_items(task, atr, 1.0)
    assert cur["x"] == pytest.approx(150.0 + 90.0, abs=0.5) and cur["y"] == pytest.approx(80.0 - 30.0, abs=0.5)
    # 跟踪面板的目标列表里叫「调整图层」
    from cutora.batch.layer_transfer import _adjust_label
    assert _adjust_label(ov).startswith("调整")


# ───────────────────────────── 复制 / 粘贴 / 搬运 ─────────────────────────────

def test_layer_transfer_and_monitor_copy_keep_adjust(tmp_path):
    from cutora.batch import layer_model as lm
    from cutora.batch import layer_transfer as lt
    from cutora.batch import monitor_model as mm
    src, atr, ov = _adjust_task(tmp_path, "lens_blur")
    rows = lt.list_layers(src)
    adj = [r for r in rows if r["kind"] == "adjust"]
    assert len(adj) == 1 and adj[0]["label"].startswith("调整") and not [r for r in rows if r["kind"] == "image"]
    assert lm.rows(src)[0]["kind"] == "adjust"                           # 图层面板里排最上面
    dst, _t2, _o2 = _adjust_task(tmp_path, None)
    dst["image_overlays"].clear()
    dst["timeline"].tracks[:] = [t for t in dst["timeline"].tracks if t.type != "adjust"]
    pack = lt.pack_layers(src, adj, "material")
    out = lt.apply_pack(dst, pack)
    assert out["added"] == 1
    new = dst["image_overlays"][-1]
    ntr = next(t for t in dst["timeline"].tracks if t.id == new["_track_id"])
    assert ntr.type == "adjust" and new["effect"]["kind"] == "lens_blur"     # 搬过去还挂在调整轨上
    assert dst["timeline"].tracks[-1] is ntr or ntr.type == "adjust"
    # 属性：改对方第一块调整图层的设置
    new["effect"]["kind"] = "mosaic"
    out = lt.apply_pack(dst, dict(pack, mode="style"))
    assert out["styled"] == 1 and new["effect"]["kind"] == "lens_blur"
    # 中央监视器 / 画布的覆层复制
    dst2, _t3, _o3 = _adjust_task(tmp_path, None)
    dst2["image_overlays"].clear()
    dst2["timeline"].tracks[:] = [t for t in dst2["timeline"].tracks if t.type != "adjust"]
    cp = mm.copy_overlay_into(src, ov, dst2)
    assert mm.overlay_kind(cp) == "adjust" and cp in dst2["image_overlays"]
    assert next(t for t in dst2["timeline"].tracks if t.id == cp["_track_id"]).type == "adjust"
    # 图层面板拖动：调整图层不能挪到图片轨上
    from cutora.model.timeline_model import Track
    itr = Track("image")
    src["timeline"].add_track(itr)
    src["image_overlays"].insert(0, {"type": "image", "_track_id": itr.id, "start": 0, "end": 1, "elements": []})
    assert not lm.move_overlay(src, {"where": "overlay", "list": "image_overlays", "index": 1},
                               {"where": "overlay", "list": "image_overlays", "index": 0})
    assert src["image_overlays"][1]["_track_id"] == atr.id


def test_timeline_copy_paste_adjust(tmp_path):
    from test_q145b_timeline_shortcuts import _load
    from cutora.render import adjust_layer as AL
    win, tl, task = _load()
    win.timeline.position = 0
    ov = win.add_adjust_item(start=0.5, dur=1.0)
    task = win.tasks[win.curr_idx]
    idx = task["image_overlays"].index(ov)
    tl._reset_selection()
    tl.selected_overlay = ("image_overlays", idx)
    assert tl.copy_selection()
    tl.position = 4000
    tl.paste_at_playhead()
    task = win.tasks[win.curr_idx]
    adj = [o for o in task["image_overlays"] if AL.is_adjust(o)]
    assert len(adj) == 2 and adj[1]["_track_id"] == adj[0]["_track_id"] and adj[1]["start"] == pytest.approx(4.0)
    # 快照来自别的视频（轨道 id 对不上）：不能挂到图片轨，要挂（必要时新建）调整轨
    snap = tl._snapshot_selection()
    for _n, o in snap["overlays"]:
        o["_track_id"] = "elsewhere"
    task["timeline"].tracks[:] = [t for t in task["timeline"].tracks if t.type != "adjust"]
    task["image_overlays"][:] = [o for o in task["image_overlays"] if not AL.is_adjust(o)]
    assert tl._paste_snapshot(snap, 1.0)
    pasted = [o for o in task["image_overlays"] if AL.is_adjust(o)]
    assert pasted and next(t for t in task["timeline"].tracks if t.id == pasted[0]["_track_id"]).type == "adjust"


# ───────────────────────────── 画面变换 / 全画面 / 关键帧 / 并行导出 ─────────────────────────────

def _grid(w=300, h=300):
    from PySide6.QtGui import QColor, QImage, QPainter
    img = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(QColor(32, 48, 64))
    p = QPainter(img)
    try:
        p.setPen(QColor(255, 208, 96))
        for i in range(0, w, 30):
            p.drawLine(i, 0, i, h)
            p.drawLine(0, i, w, i)
        p.fillRect(140, 140, 20, 20, QColor(255, 0, 0))          # 中心一块红
    finally:
        p.end()
    return img


def test_xform_moves_content_below_within_range():
    _app()
    from cutora.render import adjust_layer as AL
    ref = _arr(_grid())
    # 整个画面放大 2 倍（绕画面中心）：中心红块变成 40×40
    img = _grid()
    ov = AL.adjust_defaults(300, 300, start=0, end=5)
    ov.update({"range": "full", "xform": {"on": True, "scale": 2.0}})
    ov["solid"]["on"] = False
    AL.apply_item(img, ov)
    a = _arr(img)
    red = (a[..., 2] > 200) & (a[..., 1] < 60)
    ys, xs = np.nonzero(red)
    assert 36 <= xs.max() - xs.min() + 1 <= 42 and abs(xs.mean() - 150) < 2
    # 平移 + 露黑边 / 镜像补边
    img = _grid()
    ov.update({"xform": {"on": True, "x": 60.0}})
    AL.apply_item(img, ov)
    a = _arr(img)
    assert a[150, 10].max() < 5                                   # 左边露出黑边
    img = _grid()
    ov.update({"xform": {"on": True, "x": 60.0, "edge": "mirror"}})
    AL.apply_item(img, ov)
    assert _arr(img)[150, 10].max() > 20
    # 只在这块形状里（局部放大镜），形状外面不动
    img = _grid()
    ov2 = AL.adjust_defaults(300, 300, start=0, end=5)
    ov2.update({"x": 100, "y": 100, "w": 100, "h": 100, "xform": {"on": True, "scale": 2.0}})
    ov2["solid"]["on"] = False
    AL.apply_item(img, ov2)
    a = _arr(img)
    assert np.array_equal(a[:95], ref[:95]) and not np.array_equal(a[100:200, 100:200], ref[100:200, 100:200])
    # 关掉 / 不变的变换：一个像素都不动
    img = _grid()
    ov2["xform"] = {"on": True}
    AL.apply_item(img, ov2)
    assert np.array_equal(_arr(img), ref)


def test_full_range_does_not_block_picking_and_masks_narrow_it(tmp_path):
    _app()
    from cutora.render import adjust_layer as AL
    task, tr, ov = _adjust_task(tmp_path, "blur")
    assert AL.hit_records(task, tr, 1.0)
    ov["range"] = "full"
    assert AL.hit_records(task, tr, 1.0) == []                     # 全画面：不挡下面的东西
    img = _grid()
    ov3 = AL.adjust_defaults(300, 300, start=0, end=5)
    ov3.update({"range": "full", "masks": [{"type": "rect", "rect": [0.0, 0.0, 0.5, 1.0], "enabled": True}]})
    ov3["solid"].update({"color": "#FFFFFF"})
    AL.apply_item(img, ov3)
    a = _arr(img)
    assert a[150, 40].min() > 240 and a[150, 260].min() < 240         # 遮罩把全画面收窄到左半边


def test_keyframes_animate_xform_and_effect(tmp_path):
    from cutora.render import adjust_layer as AL
    from cutora.render.keyframe import set_point
    task, tr, ov = _adjust_task(tmp_path, "blur")
    ov["xform"] = {"on": True}
    set_point(ov, "xform.scale", 0.0, 1.0)
    set_point(ov, "xform.scale", 2.0, 2.0)
    set_point(ov, "effect.amount", 0.0, 0.0)
    set_point(ov, "effect.amount", 2.0, 80.0)
    (_i, _o, cur), = AL.active_items(task, tr, 1.0)
    assert AL.xform_of(cur)["scale"] == pytest.approx(1.5) and AL.effect_of(cur)["amount"] == pytest.approx(40.0)
    assert AL.xform_of(ov)["scale"] == pytest.approx(1.0)               # 原覆层不被烘焙改掉


def test_panel_keyframes_xform_through_property_panel():
    from PySide6.QtWidgets import QApplication
    from test_q145b_timeline_shortcuts import _load
    from cutora.render.keyframe import path_enabled, value_at
    win, _tl, _task = _load()
    win.timer.stop()                         # 别让播放时钟在测试中途挪播放头
    win.timeline.position = 0
    win.add_adjust_item(start=0.0, dur=4.0)
    for _ in range(5):
        QApplication.processEvents()
    pp = win.prop_panel
    ap = pp.ov_adjust
    ap.xf_on.setChecked(True)
    ap.range.setCurrentText("full")
    ap._emit_timer.stop()
    ap._flush()
    ov = win.tasks[win.curr_idx]["image_overlays"][-1]
    assert ov["range"] == "full" and ov["xform"]["on"]
    watch = pp._kf_extra["xform.scale"][3]
    win._timeline_ms = 0
    win.timeline.position = 0
    pp._on_kf_watch(watch)
    ov = win.tasks[win.curr_idx]["image_overlays"][-1]
    assert path_enabled(ov, "xform.scale")
    win.timeline.position = 2000
    pp._refresh_kf_watches()
    ap.xf_scale.setValue(150)
    ap._emit_timer.stop()
    ap._flush()
    ov = win.tasks[win.curr_idx]["image_overlays"][-1]
    assert value_at(ov, "xform.scale", 1.0, 1.0) == pytest.approx(1.25), ov.get("keyframes")
    win.timeline.position = 0
    pp._refresh_kf_watches()
    assert ap.xf_scale.value() == pytest.approx(100.0)                 # 播放头移回去，控件显示那一刻的值


def test_parallel_export_compose_is_identical_for_adjust(tmp_path):
    _app()
    from test_q163_unified_stack import W, H
    from cutora.model.track_guard import video_tracks_for_render
    from cutora.render.compose_parallel import ExportComposeCtx
    from cutora.render.frame_renderer import compute_export_render_order
    from cutora.render.render_worker import RenderWorker, _ExportCapPool, _stack_parallel_ok
    from cutora.render.visual_stack import build_stack
    for eff, top in (("lens_blur", False), ("mosaic", True)):
        task, _tr, ov = _adjust_task(tmp_path, eff, text_on_top=top)
        ov["xform"] = {"on": True, "scale": 1.3, "rotation": 5.0}
        rw = RenderWorker(task, "/tmp/x.mp4", "quality", False, export_logs=False)
        rw.project = task
        tl = task["timeline"]
        paths = {sid: src.path for sid, src in tl.sources.items()}
        st = build_stack(task)
        assert _stack_parallel_ok(st)

        def ctx():
            states = {}
            caps = _ExportCapPool(paths, {sid: 99.0 for sid in paths}, states)
            return ExportComposeCtx(project=task, width=W, height=H, video_tracks=video_tracks_for_render(task),
                                    video_caps=caps, frame_states=states, fallback_cap=None, overlay_path=None,
                                    fallback_clip=None, render_order=compute_export_render_order(task),
                                    overlay_index=None, task_glass=False, stack=st)
        serial = rw._compose_export_frame(1.0, None, ctx())
        c = ctx()
        video, bd = rw._compose_video_frame(1.0, c)
        par = rw._compose_overlay_frame(1.0, None, c, video, bd)
        assert np.array_equal(_arr(serial), _arr(par)), eff                 # 多线程合成和串行逐像素一样
    # 文字压在视频下面（两段视频带）还是串行
    assert not _stack_parallel_ok([("video", []), ("overlay", None), ("video", []), ("adjust", None)])


def test_export_with_adjust_layer_runs_parallel_and_matches_serial(tmp_path):
    """有调整图层的工程以前一律串行合成；现在只有一段视频带时多线程合成，成片逐帧一样。"""
    import shutil
    import subprocess
    _app()
    ff = shutil.which("ffmpeg")
    if not ff:
        pytest.skip("no ffmpeg")
    from cutora.render.compose_parallel import LAST_RUN
    from cutora.render.render_worker import RenderWorker
    noisy = str(tmp_path / "noisy.mp4")
    subprocess.run([ff, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=216x384:r=25:d=2",
                    "-pix_fmt", "yuv420p", noisy], check=True)

    def frame_md5s(path):
        r = subprocess.run([ff, "-v", "error", "-i", path, "-f", "framemd5", "-"], capture_output=True, text=True)
        return [ln.split(",")[-1].strip() for ln in r.stdout.splitlines() if not ln.startswith("#")]
    out = {}
    for workers in (1, 2):
        task, _tr, ov = _adjust_task(tmp_path, "lens_blur")
        ov["xform"] = {"on": True, "scale": 1.2}
        task["timeline"].sources["m"].path = noisy
        task["video"] = noisy
        dest = str(tmp_path / ("o%d.mp4" % workers))
        err = []
        rw = RenderWorker(task, dest, "quality", False, export_logs=False, compose_workers=workers)
        rw.error.connect(lambda m: err.append(m))
        rw.run()
        assert not err, err
        out[workers] = frame_md5s(dest)
        if workers == 2:
            assert LAST_RUN.get("workers") == 2
    assert len(out[1]) == 50 and out[1] == out[2]


def test_share_adjust_track_copies_adjust_bars(tmp_path):
    """Luca 实测：调整轨开了共享，共享两次都只在目标视频里建了空的调整轨，调整条没带过去。"""
    from test_q145b_timeline_shortcuts import _load, _task
    from cutora.render import adjust_layer as AL
    win, _tl, src = _load()
    win.timer.stop()
    ov = win.add_adjust_item(start=0.0, dur=8.0)
    ov["effect"].update({"on": True, "kind": "mosaic"})
    ov["xform"] = {"on": True, "x": 20.0}
    src = win.tasks[win.curr_idx]
    atr = next(t for t in src["timeline"].tracks if t.type == "adjust")
    atr.enable_batch = True
    dst = _task()
    dst["timeline"].width, dst["timeline"].height = 640, 360            # 画布大一倍
    win.tasks.append(dst)
    n = win.command_share_tracks(0, [1], track_ids=[str(atr.id)])["applied"]
    assert n == 1
    ntr = [t for t in dst["timeline"].tracks if t.type == "adjust"]
    bars = [o for o in dst["image_overlays"] if AL.is_adjust(o)]
    assert len(ntr) == 1 and len(bars) == 1 and bars[0]["_track_id"] == ntr[0].id
    assert bars[0]["effect"]["kind"] == "mosaic" and bars[0]["xform"]["x"] == pytest.approx(40.0)
    assert bars[0]["w"] == pytest.approx(ov["w"] * 2)
    # 图片轨共享不会把调整条带过去
    assert not [o for o in dst["image_overlays"] if not AL.is_adjust(o) and o.get("_track_id") == ntr[0].id]
    win.tasks.pop()
