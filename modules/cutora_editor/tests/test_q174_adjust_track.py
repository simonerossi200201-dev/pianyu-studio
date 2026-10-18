# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q174 · 调整轨道：纯色块（填充 / 圆角 / 边框 / 不透明度）+ 对它下面整幅画面（视频、图片、文字、字幕）
在形状里做效果（镜头模糊 / 高斯模糊 / 马赛克 / 调色），形状 × 羽化 × 遮罩；预览 = 导出 = 画布 = 监视器。"""
from __future__ import annotations

import json
import os
import sys
import tempfile

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("CUTORA_SKIP_ENCODER_PROBE", "1")
os.environ.setdefault("CUTORA_USER_DATA", tempfile.mkdtemp(prefix="cutora_q174_"))
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _app():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(["q174"])
    from cutora.ui.panels.fonts import load_embedded_fonts
    load_embedded_fonts()
    return app


def _img(w, h, fill=None, noise=False):
    from PySide6.QtGui import QImage
    img = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    a = np.frombuffer(img.bits(), np.uint8).reshape(h, img.bytesPerLine())[:, :w * 4].reshape(h, w, 4)
    if noise:
        rng = np.random.default_rng(7)
        a[..., :3] = rng.integers(0, 255, (h, w, 3), dtype=np.uint8)
    else:
        a[..., :3] = fill if fill is not None else (160, 64, 32)
    a[..., 3] = 255
    return img


def _arr(img):
    from PySide6.QtGui import QImage
    q = img.convertToFormat(QImage.Format.Format_ARGB32)
    return np.frombuffer(bytes(q.constBits()), np.uint8).reshape(
        q.height(), q.bytesPerLine() // 4, 4)[:, :q.width(), :3].astype(int)


def _ov(cw=200, ch=200, **kw):
    from cutora.render import adjust_layer as AL
    ov = AL.adjust_defaults(cw, ch, start=0.0, end=5.0, track_id="adj")
    ov.update(kw)
    return ov


def _effect(ov, kind, amount=60, solid=False):
    ov["solid"]["on"] = solid
    ov["effect"].update({"on": True, "kind": kind, "amount": amount})
    return ov


def _sharp(a, box):
    x, y, w, h = box
    r = a[y:y + h, x:x + w].mean(axis=2)
    return float(np.abs(np.diff(r, axis=1)).sum() + np.abs(np.diff(r, axis=0)).sum())


# ───────────────────────────── 纯 numpy / QImage ─────────────────────────────

def test_defaults_are_a_centered_solid():
    from cutora.render import adjust_layer as AL
    ov = AL.adjust_defaults(1080, 1920, start=1.0, end=6.0, track_id="t1")
    assert ov["type"] == "adjust" and ov["_track_id"] == "t1" and ov["name"] == "调整"
    assert (ov["x"], ov["y"], ov["w"], ov["h"]) == (216.0, 576.0, 648.0, 768.0)
    assert AL.solid_of(ov)["on"] and not AL.effect_of(ov)["on"]
    # 坏数据：钳住、补默认
    bad = dict(ov, solid={"opacity": 9, "radius": -1, "color": None}, effect={"kind": "nope", "amount": "x"})
    s, e = AL.solid_of(bad), AL.effect_of(bad)
    assert s["opacity"] == 1.0 and s["radius"] == 0.0 and e["kind"] == "lens_blur" and e["amount"] == 40


def test_solid_fill_radius_border_opacity():
    _app()
    from cutora.render import adjust_layer as AL
    img = _img(200, 200)
    ov = _ov(x=40, y=40, w=120, h=120)
    ov["solid"].update({"color": "#FF0000", "radius": 0.5})
    AL.apply_item(img, ov)
    a = _arr(img)
    assert tuple(a[100, 100]) == (0, 0, 255)                    # BGR：中心红
    assert tuple(a[41, 41]) == (160, 64, 32)                    # 圆角：角上没填
    assert tuple(a[10, 10]) == (160, 64, 32)                    # 外面不动
    img = _img(200, 200)
    ov = _ov(x=40, y=40, w=120, h=120)
    ov["solid"].update({"color": "#000080", "opacity": 0.5, "border_on": True, "border_width": 6,
                        "border_color": "#00FF00"})
    AL.apply_item(img, ov)
    a = _arr(img)
    assert tuple(a[42, 100]) == (0, 255, 0)                     # 边框
    mid = a[100, 100]
    assert 140 <= mid[0] <= 150 and mid[2] < 20                 # 半透明填充：底色和填充各一半


def test_effects_change_only_inside_shape_and_feather_is_soft():
    _app()
    from cutora.render import adjust_layer as AL
    box = (60, 60, 80, 80)
    for kind in ("blur", "lens_blur", "mosaic"):
        base = _img(200, 200, noise=True)
        ref = _arr(base)
        img = _img(200, 200, noise=True)
        AL.apply_item(img, _effect(_ov(x=50, y=50, w=100, h=100), kind))
        a = _arr(img)
        assert _sharp(a, box) < _sharp(ref, box) * 0.6, kind
        assert np.array_equal(a[:40], ref[:40]) and np.array_equal(a[:, :40], ref[:, :40]), kind
    # 调色：曝光 +1 → 形状里变亮
    img = _img(200, 200)
    ov = _effect(_ov(x=50, y=50, w=100, h=100), "grade")
    ov["effect"]["grade"]["exposure"] = 1.0
    AL.apply_item(img, ov)
    a = _arr(img)
    assert a[100, 100].sum() > (160 + 64 + 32) + 60 and tuple(a[10, 10]) == (160, 64, 32)
    # 羽化：边上是过渡，不是一刀切
    img = _img(200, 200, fill=(0, 0, 0))
    ov = _ov(x=50, y=50, w=100, h=100, feather=30)
    ov["solid"]["color"] = "#FFFFFF"
    AL.apply_item(img, ov)
    row = _arr(img)[100, :, 0]
    assert 20 < row[50] < 235 and row[100] > 240 and row[20] < 30


def test_user_mask_rotation_and_preview_scale():
    _app()
    from cutora.render import adjust_layer as AL
    # 遮罩只留左半边
    img = _img(200, 200)
    ov = _ov(x=50, y=50, w=100, h=100)
    ov["solid"]["color"] = "#FFFFFF"
    ov["masks"] = [{"type": "rect", "rect": [0.0, 0.0, 0.5, 1.0], "enabled": True}]
    AL.apply_item(img, ov)
    a = _arr(img)
    assert a[100, 70].min() > 240 and tuple(a[100, 130]) == (160, 64, 32)
    # 旋转 45°：原来的角落外面，旋转后盖住了上面中间
    img = _img(200, 200)
    ov = _ov(x=60, y=60, w=80, h=80, rotation=45)
    ov["solid"]["color"] = "#FFFFFF"
    AL.apply_item(img, ov)
    a = _arr(img)
    assert a[47, 100].min() > 240 and tuple(a[62, 62]) == (160, 64, 32)
    # 预览缩小一半 ≈ 原尺寸结果缩小一半（强度按画面长边算，不跟分辨率走）
    from PySide6.QtCore import Qt
    big = _img(400, 400, noise=True)
    small = big.scaled(200, 200, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
    ov = _effect(_ov(400, 400, x=100, y=100, w=200, h=200, feather=10), "blur", 50)
    AL.apply_item(big, ov)
    AL.apply_item(small, ov, scale=0.5)
    b = _arr(big.scaled(200, 200, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation))
    assert np.abs(b - _arr(small))[60:140, 60:140].mean() < 6


def test_effect_strength_is_resolution_independent():
    from cutora.render import adjust_layer as AL
    e = {"kind": "blur", "amount": 50}
    assert AL.effect_radius_px(e, 1920) == pytest.approx(2 * AL.effect_radius_px(e, 960))
    assert AL.mosaic_cell_px(100, 1920) > AL.mosaic_cell_px(10, 1920) > 0


# ───────────────────────────── 叠压：预览 = 导出 = 画布 = 监视器 ─────────────────────────────

def _stack_task(tmp_path, effect=None, adjust_below_text=False, hidden=False):
    from test_q163_unified_stack import W, H, _task
    from cutora.model.timeline_model import Track
    from cutora.render import adjust_layer as AL
    task = _task(tmp_path)
    tl = task["timeline"]
    tr = Track("adjust")
    tr.visible = not hidden
    if adjust_below_text:
        tl.tracks.insert(1, tr)                   # 视频 → 调整 → 文字：字在调整层上面，不受影响
    else:
        tl.tracks.append(tr)
    ov = AL.adjust_defaults(W, H, start=0.0, end=2.0, track_id=tr.id)
    ov.update({"x": 10, "y": 140, "w": 196, "h": 100})
    if effect:
        _effect(ov, effect)
    task["image_overlays"].append(ov)
    return task


def test_stack_model(tmp_path):
    from cutora.render.visual_stack import build_stack, needs_stack, stack_signature
    t = _stack_task(tmp_path)
    st = build_stack(t)
    assert [k for k, _ in st] == ["video", "overlay", "adjust"] and needs_stack(t, stack=st)
    assert ("a", str(t["timeline"].tracks[-1].id)) in stack_signature(t)
    h = _stack_task(tmp_path, hidden=True)
    assert "adjust" not in [k for k, _ in build_stack(h)]


def test_preview_export_canvas_agree_and_text_below_is_affected(tmp_path):
    _app()
    from test_q163_unified_stack import W, H, _export_frame, _task
    from PySide6.QtGui import QImage, QPainter
    from cutora.preview.engine import PreviewEngine
    from cutora.ui.canvas import VideoCanvas
    box = (20, 150, 176, 80)
    eng = PreviewEngine()
    try:
        base = _arr(eng.frame_at(_task(tmp_path), 1.0))
        cases = {"solid": _stack_task(tmp_path), "blur": _stack_task(tmp_path, "blur"),
                 "lens": _stack_task(tmp_path, "lens_blur"), "mosaic": _stack_task(tmp_path, "mosaic"),
                 "above": _stack_task(tmp_path, "blur", adjust_below_text=True),
                 "hidden": _stack_task(tmp_path, "blur", hidden=True)}
        out = {}
        for name, task in cases.items():
            a = _arr(eng.frame_at(task, 1.0))
            assert np.array_equal(a, _arr(_export_frame(task))), name        # 预览 = 导出
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
            if name != "hidden":       # 没有调整层 / 视频在上面时画布走非叠压路径（要真解码的帧），不在这里比
                assert np.array_equal(_arr(img), a), name                    # 画布 = 预览
            assert np.array_equal(a[:120], base[:120]), name                 # 形状外面不动
            out[name] = a
            if name not in ("hidden",):
                assert any(r.get("adjust") for r in cv._hit_records), name
        assert tuple(out["solid"][190, 108]) == (142, 92, 122)               # #7A5C8E 盖住字和视频
        s0 = _sharp(base, box)
        for name in ("blur", "lens", "mosaic"):
            assert _sharp(out[name], box) < s0 * 0.5, name                   # 下面的字被糊了
        assert _sharp(out["above"], box) == pytest.approx(s0, rel=1e-6)       # 字在调整层上面：清楚
        assert np.array_equal(out["hidden"], base)
    finally:
        eng.close()


def test_central_monitor_tiles_match(tmp_path, monkeypatch):
    monkeypatch.setenv("CUTORA_USER_DATA", str(tmp_path / "ud"))
    app = _app()
    from test_q144_central_monitor import _dialog, _wait
    from test_q163_unified_stack import W, H
    from cutora.preview.engine import PreviewEngine
    tasks = [_stack_task(tmp_path), _stack_task(tmp_path, "blur")]
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
            assert np.abs(a[190, 108] - refs[i][190, 108]).max() < 12, i
    finally:
        dlg.close()


def test_canvas_select_move_resize(monkeypatch, tmp_path):
    monkeypatch.setenv("CUTORA_USER_DATA", str(tmp_path / "ud"))
    app = _app()
    from test_q145_canvas_shortcuts import _click, _drag
    from cutora.model.timeline import Timeline
    from cutora.model.timeline_model import Track
    from cutora.render import adjust_layer as AL
    from cutora.ui.canvas import VideoCanvas
    VW, VH = 1080, 1920
    tl = Timeline(VW, VH, 30.0)
    a = Track("adjust")
    tl.tracks.append(a)
    ov = AL.adjust_defaults(VW, VH, start=0.0, end=5.0, track_id=a.id)
    task = {"timeline": tl, "segments": [], "style": {}, "text_overlays": [], "image_overlays": [ov],
            "duration": 5.0, "dims": [VW, VH], "z_order_v2": True}
    c = VideoCanvas()
    c.setFixedSize(540, 960)
    c.set_data(task, 0.5)
    c.show()
    app.processEvents()
    c.repaint()
    try:
        from PySide6.QtGui import QImage
        t = c._preview_target_rect()
        sx = t.width() / VW
        shot = c.grab().toImage().convertToFormat(QImage.Format.Format_ARGB32)
        assert shot.pixelColor(int(t.x() + 540 * sx), int(t.y() + 960 * sx)).name() == "#7a5c8e"
        sel = []
        c.item_selected.connect(lambda *x: sel.append(x))
        cx, cy = t.x() + 540 * sx, t.y() + 960 * sx
        _click(c, cx, cy)
        assert sel and sel[-1] == ("overlay", ("image_overlays", 0))
        x0, y0 = ov["x"], ov["y"]
        _drag(c, cx, cy, cx + 50, cy + 30)
        assert (ov["x"] - x0, ov["y"] - y0) == pytest.approx((100, 60), abs=2)
        w0 = ov["w"]
        brx, bry = t.x() + (ov["x"] + ov["w"]) * sx, t.y() + (ov["y"] + ov["h"]) * sx
        _drag(c, brx, bry, brx + 40, bry + 40)
        assert ov["w"] > w0 + 40
    finally:
        c.close()


# ───────────────────────────── 主窗口：轨道 / 面板 / 撤销 / 存盘 ─────────────────────────────

def test_main_window_add_edit_undo_save(monkeypatch):
    from PySide6.QtWidgets import QApplication
    from test_q145b_timeline_shortcuts import _load
    win, _tlw, task = _load()
    win.timeline.position = 1000
    ntr = len(task["timeline"].tracks)
    tr = win.shortcut_add_track("adjust")             # 新建调整轨 = 顺手放一块纯色，一步撤销
    for _ in range(5):
        QApplication.processEvents()
    cur = win.tasks[win.curr_idx]
    types = [t.type for t in cur["timeline"].tracks]
    assert tr is not None and tr.type == "adjust" and types[-1] == "adjust"   # 叠在最上面
    ov = cur["image_overlays"][-1]
    assert ov["type"] == "adjust" and ov["_track_id"] == tr.id and ov["start"] == pytest.approx(1.0)
    assert [e["desc"] for e in win.history_mgr.history][-1] == "添加调整图层"
    # 时间线：调整轨只列调整图层，图片轨不列它
    img_tr = next(t for t in cur["timeline"].tracks if t.type == "image")
    assert [o for _i, o in win.timeline._get_overlays_for_track(tr, "image_overlays")] == [ov]
    assert ov not in [o for _i, o in win.timeline._get_overlays_for_track(img_tr, "image_overlays")]
    from cutora.ui.main_window import track_is_empty
    assert not track_is_empty(tr, cur)
    # 再按轨道头「+」：同一条轨道上再来一块
    win._on_import_to_track(tr)
    assert sum(1 for o in cur["image_overlays"] if o.get("_track_id") == tr.id) == 2
    win.undo()
    cur = win.tasks[win.curr_idx]
    ov = cur["image_overlays"][-1]
    assert ov["type"] == "adjust" and sum(1 for o in cur["image_overlays"] if o.get("type") == "adjust") == 1
    # 属性面板
    win.video_canvas.item_selected.emit("overlay", ("image_overlays", cur["image_overlays"].index(ov)))
    for _ in range(5):
        QApplication.processEvents()
    pp = win.prop_panel
    assert pp.ov_adjust.isVisible() and pp.ov_adjust._ov is ov
    n_hist = win.history_mgr.current_index
    pp.ov_adjust.effect_on.setChecked(True)
    pp.ov_adjust.solid_on.setChecked(False)
    pp.ov_adjust.kind_checks["lens_blur"].setChecked(False)       # Q179：效果多选（勾框）
    pp.ov_adjust.kind_checks["mosaic"].setChecked(True)
    pp.ov_adjust._emit_timer.stop()
    pp.ov_adjust._flush()
    for _ in range(10):
        QApplication.processEvents()
    assert ov["effect"]["on"] and ov["effect"]["kind"] == "mosaic" and not ov["solid"]["on"]
    assert ov["effect"]["kinds"] == ["mosaic"]
    hm = win.history_mgr
    assert hm.current_index == n_hist + 1 == len(hm.history) - 1 and hm.history[-1]["desc"] == "调整图层"           # 一次调整 = 一步，不多补「操作」
    win.undo()
    o2 = win.tasks[win.curr_idx]["image_overlays"][-1]
    assert o2["solid"]["on"] and not o2["effect"]["on"]
    win.undo()
    cur = win.tasks[win.curr_idx]
    assert len(cur["timeline"].tracks) == ntr and not any(o.get("type") == "adjust" for o in cur["image_overlays"])
    win.redo()
    win.redo()
    cur = win.tasks[win.curr_idx]
    assert cur["image_overlays"][-1]["effect"]["kind"] == "mosaic"
    # 存盘再读：轨道类型、覆层、关联都在
    from cutora.model.project_io import deserialize_render_task, serialize_render_task
    back = deserialize_render_task(json.loads(json.dumps(serialize_render_task(cur))))
    atr = next(t for t in back["timeline"].tracks if t.type == "adjust")
    bov = back["image_overlays"][-1]
    assert bov["type"] == "adjust" and bov["_track_id"] == atr.id and bov["effect"]["kind"] == "mosaic"


def test_new_video_track_goes_below_adjust_and_side_paths_skip_it():
    from cutora.model.timeline import Timeline
    from cutora.model.timeline_model import Track
    tl = Timeline(320, 180, 25)
    v, a = Track("video"), Track("adjust")
    tl.tracks[:] = [v, a]
    nv = Track("video")
    tl.add_track(nv)
    assert tl.tracks == [v, nv, a]
    from cutora.render import adjust_layer as AL
    ov = AL.adjust_defaults(320, 180, start=0.0, end=1.0, track_id=a.id)
    ov["masks"] = [{"type": "rect", "enabled": True}]
    task = {"timeline": tl, "image_overlays": [ov], "text_overlays": []}
    from cutora.batch.layer_transfer import list_layers
    assert not [r for r in list_layers(task) if r.get("kind") == "image"]
    from cutora.ui.alpha_checker import _scan
    assert not _scan(task)
    from cutora.ui.shortcuts import SHORTCUTS
    row = next(r for r in SHORTCUTS if r[0] == "track_add_adjust")
    from cutora.ui.main_window import MainWindow
    assert callable(getattr(MainWindow, row[3], None))
