# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q152-A 第二批：更多参数能打关键帧 + 画布运动路径。

- bake_host 把所有打了关键帧的路径烘进副本（描边宽度、发光、字距……），老的十个顺序不变。
- 视频背景填充的模糊 / 压暗能打关键帧：fill_spec_at 预览导出共用。
- 检查器：新参数有 ⏱；打开后改值在播放头记关键帧；改别的参数不会顺手给它打点。
- 画布：位置打了关键帧的对象画出路线；拖关键帧方块改那一刻的位置，一次撤销；双击方块跳播放头。
"""
from __future__ import annotations

import copy
import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PySide6.QtCore import QEvent, Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402


def _app():
    from test_q145_canvas_shortcuts import _app as app
    return app()


# ───────────────────────────── 求值 ─────────────────────────────

def test_bake_host_bakes_every_keyed_path():
    from cutora.render.keyframe import bake_host, set_point
    ov = {"type": "text", "content": "A", "start": 1.0, "end": 4.0, "x": 0, "y": 0,
          "fx": {"stroke": {"width_em": 0.02}, "glow": {"strength": 0.0}}}
    set_point(ov, "stroke.width_em", 0.0, 0.0)
    set_point(ov, "stroke.width_em", 2.0, 0.2)
    set_point(ov, "glow.strength", 0.0, 0.0)
    set_point(ov, "glow.strength", 1.0, 1.0)
    out = bake_host(ov, 1.0)
    assert out["fx"]["stroke"]["width_em"] == pytest.approx(0.1)
    assert out["fx"]["glow"]["strength"] == pytest.approx(1.0)
    assert ov["fx"]["stroke"]["width_em"] == 0.02           # 原对象不动
    plain = {"type": "text", "content": "A", "start": 0, "end": 1}
    assert bake_host(plain, 0.5) is plain                  # 没关键帧走老路


def test_fill_spec_at_animates_blur_and_dim():
    from cutora.model.timeline_model import Clip
    from cutora.render.fill_bg import fill_spec, fill_spec_at, set_fill
    from cutora.render.keyframe import set_point
    c = Clip("s", 0.0, 4.0, 2.0)
    set_fill(c, mode="blur", blur=10.0, dim=0.1)
    assert fill_spec_at(c, 3.0) == fill_spec(c)            # 没关键帧 = 原样
    set_point(c, "bg_fill.blur", 0.0, 0.0)
    set_point(c, "bg_fill.blur", 2.0, 80.0)
    set_point(c, "bg_fill.dim", 0.0, 0.0)
    set_point(c, "bg_fill.dim", 2.0, 0.6)
    spec = fill_spec_at(c, 3.0)                             # 局部 1.0 秒
    assert spec["blur"] == pytest.approx(40.0)
    assert spec["dim"] == pytest.approx(0.3)
    assert spec["mode"] == "blur" and spec["blur_style"] == "lens"
    assert fill_spec_at(c, None) == fill_spec(c)


# ───────────────────────────── 检查器 ─────────────────────────────

def _panel_env(host_kind="overlay"):
    _app()
    from test_q62_keyframes import _win_for_task
    from cutora.core.commands import get_bus, reset_bus_for_tests
    from cutora.model.factory_defaults import overlay_defaults
    from cutora.model.timeline import Timeline
    from cutora.model.timeline_model import Clip, Track
    from cutora.ui.panels.property import PropertyPanel
    from cutora.ui.timeline.widget import TimelineWidget
    tl = Timeline(1080, 1920, 30.0)
    vtr = Track("video")
    vtr.id = "v0"
    clip = Clip("s", 0.0, 4.0, 0.0)
    vtr.clips.append(clip)
    tl.add_track(vtr)
    ov = overlay_defaults("text", canvas_w=1080, canvas_h=1920, track_id="t")
    ov.update({"content": "HELLO", "start": 0.0, "end": 4.0})
    task = {"timeline": tl, "duration": 4.0, "text_overlays": [ov], "image_overlays": [],
            "segments": [], "style": {}}
    reset_bus_for_tests()
    win = _win_for_task(task)
    get_bus().bind_window(win)
    tlw = TimelineWidget()
    tlw.set_data(4000, task)
    win.timeline = tlw
    panel = PropertyPanel()
    panel.setParent(win)
    if host_kind == "overlay":
        panel.load_data("overlay", ov)
    else:
        panel.load_data("video_clip", (vtr, clip))
    win.show()
    QApplication.processEvents()
    return win, panel, task, ov, clip


def test_extra_watches_exist_and_key_on_change():
    from cutora.render.keyframe import keyframes_of, path_enabled
    win, panel, task, ov, _clip = _panel_env("overlay")
    try:
        extra = panel._kf_extra
        for path in ("stroke.width_em", "glow.strength", "tracking_em", "bg_fill.blur", "bg_fill.dim"):
            assert path in extra, path
        watch = extra["stroke.width_em"][3]
        panel._refresh_kf_watches()
        assert watch.isVisibleTo(panel) or not watch.isHidden()
        # 打开 ⏱ → 在播放头 0 记一个点
        win.timeline.set_position(0)
        panel._on_kf_watch(watch)
        host = task["text_overlays"][0]
        assert path_enabled(host, "stroke.width_em")
        # 播放头到 2 秒，改描边宽度 → 记第二个点
        win.timeline.set_position(2000)
        QApplication.processEvents()
        panel.fx_stroke_width_em.setValue(0.15)
        QApplication.processEvents()
        pts = keyframes_of(host)["stroke.width_em"]
        assert len(pts) == 2 and pts[1]["t"] == pytest.approx(2.0) and pts[1]["v"] == pytest.approx(0.15)
        # 改别的参数（发光强度，没开关键帧）不会给描边宽度多打点
        panel.fx_glow_strength.setValue(0.7)
        QApplication.processEvents()
        assert len(keyframes_of(host)["stroke.width_em"]) == 2
        # 播放头到 1 秒：控件显示插值
        win.timeline.set_position(1000)
        QApplication.processEvents()
        panel._refresh_kf_watches()
        mid = panel.fx_stroke_width_em.value()
        first = keyframes_of(host)["stroke.width_em"][0]["v"]
        assert mid == pytest.approx((first + 0.15) / 2.0, abs=0.006)
    finally:
        win.close()


def test_video_bg_blur_watch_keys_in_storage_units():
    from cutora.render.keyframe import keyframes_of
    win, panel, task, _ov, clip = _panel_env("clip")
    try:
        panel.vid_bg_mode.setCurrentText("blur")
        QApplication.processEvents()
        watch = panel._kf_extra["bg_fill.dim"][3]
        win.timeline.set_position(0)
        panel._on_kf_watch(watch)
        clip2 = task["timeline"].tracks[0].clips[0]
        assert "bg_fill.dim" in keyframes_of(clip2)
        win.timeline.set_position(1500)
        QApplication.processEvents()
        panel.vid_bg_dim.setValue(60)
        QApplication.processEvents()
        pts = keyframes_of(clip2)["bg_fill.dim"]
        assert pts[-1]["t"] == pytest.approx(1.5) and pts[-1]["v"] == pytest.approx(0.6)
    finally:
        win.close()


# ───────────────────────────── 运动路径 ─────────────────────────────

def _canvas_with_path():
    _app()
    from test_q145_canvas_shortcuts import _canvas_texts, _click, _glyph_point
    from test_q62_keyframes import _win_for_task
    from cutora.core.commands import get_bus, reset_bus_for_tests
    from cutora.render.keyframe import set_point
    c, task, ovs = _canvas_texts(1)
    ov = ovs[0]
    set_point(ov, "x", 0.0, 60.0)
    set_point(ov, "x", 2.0, 400.0)
    set_point(ov, "y", 0.0, 200.0)
    set_point(ov, "y", 2.0, 600.0)
    reset_bus_for_tests()
    win = _win_for_task(task)
    get_bus().bind_window(win)
    c.set_data(task, 1.0)
    c.repaint()
    QApplication.processEvents()
    gx, gy = _glyph_point(c, 0)
    _click(c, gx, gy)
    c.repaint()
    QApplication.processEvents()
    return c, task, ov, win


def _to_widget(c, p):
    from test_q145_canvas_shortcuts import VH, VW
    t = c._preview_target_rect()
    return t.x() + p.x() * t.width() / VW, t.y() + p.y() * t.height() / VH


def test_motion_path_model_and_drag_key():
    from test_q145_canvas_shortcuts import _drag
    from cutora.render.keyframe import keyframes_of
    c, task, ov, win = _canvas_with_path()
    try:
        m = c._motion_model
        assert m is not None and [round(t, 3) for t, _p in m["keys"]] == [0.0, 2.0]
        (_t0, p0), (_t1, p1) = m["keys"]
        # 路线方向 = 关键帧之间的位移
        assert p1.x() - p0.x() == pytest.approx(340.0, abs=1.0)
        assert p1.y() - p0.y() == pytest.approx(400.0, abs=1.0)
        assert len(m["samples"]) >= 60                    # 每帧一个点
        n_hist = len(win.history_mgr.history)
        x1, y1 = _to_widget(c, p1)
        _drag(c, x1, y1, x1 + 20, y1 + 10)
        kfs = keyframes_of(task["text_overlays"][0])
        assert kfs["x"][1]["v"] == pytest.approx(440.0, abs=3.0)
        assert kfs["y"][1]["v"] == pytest.approx(620.0, abs=3.0)
        assert kfs["x"][0]["v"] == pytest.approx(60.0)     # 别的关键帧不动
        assert len(win.history_mgr.history) == n_hist + 1
    finally:
        c.close()
        win.close()


def test_motion_path_shift_locks_axis_and_double_click_seeks():
    from test_q145_canvas_shortcuts import SHIFT, _drag, _mouse
    from cutora.render.keyframe import keyframes_of
    from cutora.ui.timeline.widget import TimelineWidget
    c, task, ov, win = _canvas_with_path()
    try:
        (_t0, p0), _k1 = c._motion_model["keys"]
        x0, y0 = _to_widget(c, p0)
        _drag(c, x0, y0, x0 + 30, y0 + 4, mods=SHIFT)
        kfs = keyframes_of(task["text_overlays"][0])
        assert kfs["x"][0]["v"] == pytest.approx(120.0, abs=3.0)
        assert kfs["y"][0]["v"] == pytest.approx(200.0)
        tlw = TimelineWidget()
        tlw.set_data(5000, task)
        c.timeline = tlw
        win.timeline = tlw
        c.window().timeline = tlw
        c.repaint()
        QApplication.processEvents()
        (_t0, _p0), (_t1, p1) = c._motion_model["keys"]
        x1, y1 = _to_widget(c, p1)
        c.mouseDoubleClickEvent(_mouse(QEvent.Type.MouseButtonDblClick, x1, y1))
        assert tlw.position == 2000
    finally:
        c.close()
        win.close()


def test_no_path_without_position_keys():
    _app()
    from test_q145_canvas_shortcuts import _canvas_texts, _click, _glyph_point
    c, task, ovs = _canvas_texts(1)
    try:
        gx, gy = _glyph_point(c, 0)
        _click(c, gx, gy)
        c.repaint()
        QApplication.processEvents()
        assert c._motion_model is None
    finally:
        c.close()


def test_overlay_watch_and_editor_resolve_overlay_target():
    """Q152 修：PropertyPanel 有两个同名 _current_task，后一个覆盖前一个，
    文本层的 ⏱ 和关键帧编辑器都拿不到定位器。"""
    from cutora.ui.keyframe_editor import resolve_target
    win, panel, task, ov, _clip = _panel_env("overlay")
    try:
        assert panel._kf_cmd_params(ov) == {"target": "overlay", "list": "text_overlays",
                                            "overlay_index": 0}
        win.prop_panel = panel
        tgt = resolve_target(win)
        assert tgt is not None and tgt.host is ov and tgt.title.startswith("文本")
        win.timeline.set_position(500)
        panel._on_kf_watch(panel.ov_rot_kf)
        from cutora.render.keyframe import path_enabled
        assert path_enabled(task["text_overlays"][0], "rotation")
    finally:
        win.close()
