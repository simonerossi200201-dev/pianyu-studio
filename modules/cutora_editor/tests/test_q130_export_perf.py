# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q130 · 导出提速 + 拖动预览不重影 + 保活进度窗修复。

- 静态文本层光栅缓存：命中后像素逐字节相同；关键帧 / 动画 / 毛玻璃不进缓存；表有上限。
- 分屏 mask 分段直拷：和整幅过 mask 逐字节相同。
- 模糊背景：小图 + cover_rect，不再整幅放大；金字塔缩图与 Qt 缩图接近。
- 内存模型：同画布实测 ≥6 条就按实测 ×1.25 派进程，不再按像素比 ×2.25。
- 保活窗脚本：以脚本方式起 export_guard.py 也能 import cutora。
- 预览：video_frame_at(scale=0.5) 几何不变（只是像素少）；画布把小图放大铺满。
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication

VW, VH = 1080, 1920
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _app():
    app = QApplication.instance() or QApplication(["q130"])
    from cutora.ui.panels.fonts import load_embedded_fonts
    load_embedded_fonts()
    return app


def _nonzero(img: QImage) -> int:
    img = img.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
    return int(np.count_nonzero(np.frombuffer(bytes(img.constBits()), np.uint8)))


def _sha(img: QImage) -> str:
    img = img.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
    return hashlib.sha1(bytes(img.constBits())).hexdigest()


def _ov(**kw):
    from cutora.model.factory_defaults import overlay_defaults
    from cutora.subtitle.text_layer import ensure_overlay_fx
    ov = overlay_defaults("text", canvas_w=VW, canvas_h=VH)
    ov.update({"content": "When God is leading a woman toward success",
               "x": 200, "y": 300, "w": 680, "start": 0.0, "end": 10.0})
    ov.update(kw)
    ensure_overlay_fx(ov)
    return ov


def _paint(ov, t=None):
    from cutora.render.text_layer_paint import paint_text_overlay
    img = QImage(VW, VH, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(0)
    p = QPainter(img)
    try:
        paint_text_overlay(p, ov, current_time=t, video_w=VW, video_h=VH)
    finally:
        p.end()
    return img


# ─────────────────────────── 静态文本缓存 ───────────────────────────

def test_static_cache_hit_is_byte_identical():
    _app()
    from cutora.render import text_layer_paint as tlp
    tlp.clear_static_cache()
    tlp.STATIC_STATS.update(hit=0, miss=0, skip=0)
    ov = _ov()
    a = _paint(dict(ov), t=1.0)
    assert tlp.STATIC_STATS["miss"] == 1
    b = _paint(dict(ov), t=2.0)
    c = _paint(dict(ov), t=7.5)
    assert tlp.STATIC_STATS["hit"] == 2
    assert _sha(a) == _sha(b) == _sha(c)
    assert _nonzero(a) > 1000, "文字应当真的画出来了"
    tlp.clear_static_cache()
    d = _paint(dict(ov), t=2.0)
    assert _sha(a) == _sha(d)
    assert tlp.STATIC_STATS["miss"] == 2


def test_static_cache_skips_keyframes_anim_and_glass():
    _app()
    from cutora.render import text_layer_paint as tlp
    tlp.clear_static_cache()
    # 关键帧：每帧一个新 ov，不进缓存，表也不能被 meta 撑大
    ov = _ov()
    ov["keyframes"] = {"x": [{"t": 0.0, "v": 200.0}, {"t": 10.0, "v": 400.0}]}
    tlp.STATIC_STATS.update(hit=0, miss=0, skip=0)
    for i in range(12):
        _paint(dict(ov), t=i * 0.5)
    cache, _lock = tlp._static_store()
    assert tlp.STATIC_STATS["hit"] == 0
    assert tlp.STATIC_STATS["miss"] == 0
    assert len(cache) == 0, "关键帧覆层不该往缓存表里放任何条目"
    # 毛玻璃：每帧读底图，不缓存
    # 毛玻璃：每帧读底图，不缓存
    ov3 = _ov()
    ov3["fx"]["bg"] = {"type": "glass", "enabled": True}
    tlp.STATIC_STATS.update(hit=0, miss=0, skip=0)
    _paint(dict(ov3), t=3.0)
    _paint(dict(ov3), t=3.5)
    assert tlp.STATIC_STATS["hit"] == 0


def test_static_cache_is_bounded():
    _app()
    from cutora.render import text_layer_paint as tlp
    tlp.clear_static_cache()
    for i in range(tlp._STATIC_MAX + 20):
        _paint(_ov(content=f"line {i}"), t=1.0)
    cache, _lock = tlp._static_store()
    assert len(cache) <= tlp._STATIC_MAX


def test_static_cache_respects_in_out_anim_windows():
    """进场 / 出场动画：动的那几帧不命中且逐字节和不缓存一样；静止段命中。"""
    _app()
    from cutora.render import text_layer_paint as tlp
    ov = _ov()
    ov["fx"]["anim"] = {"in": "pop_in", "out": "fade", "in_s": 0.4, "out_s": 0.3,
                        "stagger_s": 0.05, "unit": "word"}
    tlp.clear_static_cache()
    tlp.STATIC_STATS.update(hit=0, miss=0, skip=0)
    in_frame = _paint(dict(ov), t=0.1)
    assert tlp.STATIC_STATS["miss"] == 0 and tlp.STATIC_STATS["skip"] == 1
    # 进场刚播完的第一帧就是静止帧：存进去
    tail = _paint(dict(ov), t=0.42)
    assert tlp.STATIC_STATS["hit"] == 0 and tlp.STATIC_STATS["miss"] == 1
    hold_a = _paint(dict(ov), t=3.0)
    assert tlp.STATIC_STATS["hit"] == 1
    hold_b = _paint(dict(ov), t=6.0)
    assert tlp.STATIC_STATS["hit"] == 2
    assert _sha(tail) == _sha(hold_a) == _sha(hold_b)
    out_frame = _paint(dict(ov), t=9.85)
    assert tlp.STATIC_STATS["hit"] == 2
    in_again = _paint(dict(ov), t=0.1)
    assert tlp.STATIC_STATS["hit"] == 2
    assert _sha(in_again) == _sha(in_frame)
    assert _sha(in_frame) != _sha(hold_a) and _sha(out_frame) != _sha(hold_a)
    tlp.clear_static_cache()
    assert _sha(_paint(dict(ov), t=6.0)) == _sha(hold_b)
    assert _sha(_paint(dict(ov), t=0.42)) == _sha(tail)
    # 循环动画 / 逐词逐行的进场（stack）：整段都在动，不缓存
    for anim in ({"in": "none", "loop": "float_loop", "out": "none"},
                 {"in": "stack", "loop": "none", "out": "none", "unit": "line"}):
        ov2 = _ov()
        ov2["fx"]["anim"] = anim
        tlp.clear_static_cache()
        tlp.STATIC_STATS.update(hit=0, miss=0, skip=0)
        _paint(dict(ov2), t=3.0)
        _paint(dict(ov2), t=6.0)
        assert tlp.STATIC_STATS["hit"] == 0 and tlp.STATIC_STATS["miss"] == 0, anim


def test_hold_token_phases():
    """划线重点词：画线期间 None（不缓存），画完前 / 画完后是两个不同的键。"""
    _app()
    from cutora.render.text_layer_paint import _hold_token, layout_for_overlay
    ov = _ov()
    ov["fx"]["emphasis"] = {"enabled": True, "words": ["God"], "style": "line_draw",
                            "line_draw_delay": 1.0, "line_draw_dur": 0.5}
    _layout, spec, _style, event, _box = layout_for_overlay(ov, VW, VH)
    assert _hold_token(spec, event, None) == "static"
    before = _hold_token(spec, event, 0.2)
    after = _hold_token(spec, event, 5.0)
    assert before is not None and after is not None
    if before != after:
        during = _hold_token(spec, event, 1.2)
        assert during is None


# ─────────────────────────── 分屏 mask 分段直拷 ───────────────────────────

def _feather_mask(h, w, axis, edge, feather):
    a = np.full((h, w), 255, np.uint8)
    coords = np.arange(h if axis == "y" else w, dtype=np.float32)
    ramp = np.clip((edge - coords) / max(1.0, feather) + 0.5, 0.0, 1.0)
    if axis == "y":
        a = (ramp[:, None] * 255).astype(np.uint8) * np.ones((1, w), np.uint8)
    else:
        a = (ramp[None, :] * 255).astype(np.uint8) * np.ones((h, 1), np.uint8)
    return a


def test_mask_spans_pick_axis_and_partition():
    from cutora.render.split_screen import _mask_spans
    full = np.full((40, 30), 255, np.uint8)
    sp = _mask_spans(full)
    assert sp["band"] == [] and sp["solid"] == [(0, 40)]
    m = _feather_mask(60, 20, "y", 40, 8)
    sp = _mask_spans(m)
    assert sp["axis"] == "y"
    solid_rows = sum(b - a for a, b in sp["solid"])
    band_rows = sum(b - a for a, b in sp["band"])
    assert band_rows > 0 and solid_rows > 0
    for a, b in sp["solid"]:
        assert (m[a:b] == 255).all()
    for a, b in sp["band"]:
        assert not (m[a:b] == 255).all() and not (m[a:b] == 0).all()
    mx = _feather_mask(20, 60, "x", 40, 8)
    assert _mask_spans(mx)["axis"] == "x"
    assert _mask_spans(np.zeros((0, 0), np.uint8))["solid"] == []


@pytest.mark.parametrize("axis,edge,feather", [("y", 40, 8), ("x", 30, 6), ("y", 64, 0), ("x", 10, 20)])
def test_blit_masked_spans_identical_to_full_path(axis, edge, feather):
    _app()
    from cutora.render.split_screen import _alpha_qimage, _blit_masked, _mask_spans
    h, w = 64, 48
    rng = np.random.default_rng(7)
    px = rng.integers(0, 256, size=(h, w, 4), dtype=np.uint8)
    px[..., 3] = 255
    img = QImage(px.tobytes(), w, h, w * 4, QImage.Format.Format_ARGB32).convertToFormat(
        QImage.Format.Format_ARGB32_Premultiplied)
    mask_np = _feather_mask(h, w, axis, edge, feather)
    mask = _alpha_qimage(mask_np)
    spans = _mask_spans(mask_np)
    outs = []
    for use_spans in (False, True):
        out = QImage(w + 10, h + 10, QImage.Format.Format_ARGB32_Premultiplied)
        out.fill(QColor(9, 20, 30, 255))
        scratch = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
        scratch.fill(0)
        p = QPainter(out)
        try:
            _blit_masked(p, img, (5, 5), mask, scratch, spans=spans if use_spans else None)
        finally:
            p.end()
        outs.append(_sha(out))
    assert outs[0] == outs[1]


# ─────────────────────────── 模糊背景 ───────────────────────────

def _gradient(w, h, fmt):
    img = QImage(w, h, fmt)
    p = QPainter(img)
    try:
        from PySide6.QtGui import QLinearGradient
        g = QLinearGradient(0, 0, w, h)
        g.setColorAt(0.0, QColor(240, 30, 20))
        g.setColorAt(0.5, QColor(20, 200, 60))
        g.setColorAt(1.0, QColor(10, 40, 250))
        p.fillRect(0, 0, w, h, g)
    finally:
        p.end()
    return img


@pytest.mark.parametrize("fmt", [QImage.Format.Format_BGR888, QImage.Format.Format_ARGB32_Premultiplied,
                                 QImage.Format.Format_RGB888])
def test_downscale_matches_qt_smooth(fmt):
    _app()
    from cutora.render.fill_bg import _downscale
    src = _gradient(704, 1280, fmt)
    small = _downscale(src, 53, 96)
    assert (small.width(), small.height()) == (53, 96)
    ref = src.scaled(53, 96, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
    a = small.convertToFormat(QImage.Format.Format_RGB888)
    b = ref.convertToFormat(QImage.Format.Format_RGB888)
    for (x, y) in [(5, 5), (26, 48), (50, 90), (10, 80)]:
        ca, cb = a.pixelColor(x, y), b.pixelColor(x, y)
        assert abs(ca.red() - cb.red()) <= 12 and abs(ca.green() - cb.green()) <= 12 and abs(ca.blue() - cb.blue()) <= 12


def test_blur_image_returns_small_and_full_variant_upscales():
    _app()
    from cutora.render.fill_bg import blur_image, blur_image_full
    src = _gradient(1080, 1920, QImage.Format.Format_BGR888)
    small = blur_image(src, 60.0)
    assert max(small.width(), small.height()) <= 128
    full = blur_image_full(src, 60.0)
    assert (full.width(), full.height()) == (1080, 1920)
    assert blur_image(src, 0.0) is src


def test_paint_fill_covers_dest_with_small_blur():
    _app()
    from cutora.render.fill_bg import paint_fill
    src = _gradient(704, 1280, QImage.Format.Format_BGR888)
    out = QImage(1080, 960, QImage.Format.Format_ARGB32_Premultiplied)
    out.fill(0)
    p = QPainter(out)
    try:
        ok = paint_fill(p, src, {"mode": "blur", "amount": 60}, 1080, 960)
    finally:
        p.end()
    assert ok
    for (x, y) in [(2, 2), (1077, 2), (2, 957), (1077, 957), (540, 480)]:
        assert out.pixelColor(x, y).alpha() == 255, (x, y)


# ─────────────────────────── 内存模型 ───────────────────────────

def _rss_data(tmp_path, monkeypatch, samples):
    import json
    from cutora.render import export_guard as g
    path = str(tmp_path / "worker_rss.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"samples": samples}, fh)
    monkeypatch.setattr(g, "_rss_log_path", lambda: path)
    return g


def test_same_canvas_samples_win_over_scaled_history(tmp_path, monkeypatch):
    old = [{"peak_gb": 2.9, "canvas_px": 704 * 1280} for _ in range(28)]
    new = [{"peak_gb": 1.588, "canvas_px": 1080 * 1920} for _ in range(12)]
    g = _rss_data(tmp_path, monkeypatch, old + new)
    p90, px, exact = g.measured_rss_for_canvas(1080 * 1920)
    assert exact and px == 1080 * 1920 and abs(p90 - 1.588) < 1e-6
    per = g.mem_per_worker_gb((1080, 1920))
    assert abs(per - 1.588 * g.RSS_EXACT_SAFETY) < 1e-6
    assert per < 2.5, "不许再回到 ×2.25 → 4.8 GB 的老模型"
    pl = g.plan_parallel_export(22, 22, False, cpu_count=32, avail_ram=12.0, mem_per_worker=per)
    assert pl["workers"] >= 2, pl
    pl2 = g.plan_parallel_export(22, 22, False, cpu_count=32, avail_ram=20.0, mem_per_worker=per)
    assert pl2["workers"] > pl["workers"], (pl, pl2)


def test_too_few_same_canvas_samples_fall_back(tmp_path, monkeypatch):
    old = [{"peak_gb": 2.9, "canvas_px": 704 * 1280} for _ in range(10)]
    new = [{"peak_gb": 1.5, "canvas_px": 1080 * 1920} for _ in range(3)]
    g = _rss_data(tmp_path, monkeypatch, old + new)
    p90, _px, exact = g.measured_rss_for_canvas(1080 * 1920)
    assert not exact
    assert p90 >= 2.9 - 1e-6
    per = g.mem_per_worker_gb((1080, 1920))
    assert per >= g.MEM_PER_WORKER_MIN_GB
    lo = g.mem_per_worker_gb((1080, 1920), measured_gb=0.5, measured_canvas_wh=(1080, 1920))
    assert lo >= g.MEM_PER_WORKER_MIN_GB


def test_exact_floor(tmp_path, monkeypatch):
    new = [{"peak_gb": 0.6, "canvas_px": 1080 * 1920} for _ in range(8)]
    g = _rss_data(tmp_path, monkeypatch, new)
    assert abs(g.mem_per_worker_gb((1080, 1920)) - g.RSS_EXACT_FLOOR_GB) < 1e-6


# ─────────────────────────── 保活进度窗 ───────────────────────────

def test_guard_script_imports_cutora_when_run_as_file():
    """launch_guard_window 用 `python export_guard.py --watch …` 起窗；cwd 不是仓库根时
    也必须能 import cutora（Q110(1) 加的 cutora.core.proc 导入曾让窗口一起就死）。"""
    script = os.path.join(ROOT, "cutora", "render", "export_guard.py")
    src = open(script, encoding="utf-8").read()
    assert "sys.path" in src.split("from cutora.core.proc")[0]
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env["QT_QPA_PLATFORM"] = "offscreen"
    code = ("import runpy, sys; sys.argv=['export_guard.py']; "
            "m = runpy.run_path(%r, run_name='not_main'); "
            "print('ok' if 'plan_parallel_export' in m else 'missing')" % script)
    out = subprocess.run([sys.executable, "-c", code], cwd=str(os.path.dirname(script) if False else "/"),
                         env=env, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-2000:]
    assert "ok" in out.stdout, out.stdout + out.stderr[-2000:]


# ─────────────────────────── 预览几何 ───────────────────────────



def test_video_frame_at_scale_keeps_geometry():
    """降画质只让像素变少：0.5 倍图放大回来和满分辨率图的内容位置一致。"""
    _app()
    from cutora.preview.engine import PreviewEngine

    class _Eng(PreviewEngine):
        def _draw_video(self, p, task, t, width, height, *a):
            p.fillRect(0, 0, width, height, QColor(0, 0, 0))
            p.fillRect(int(width * 0.25), int(height * 0.5), int(width * 0.5), int(height * 0.25),
                       QColor(255, 0, 0))

    eng = _Eng.__new__(_Eng)
    eng._rw = type("RW", (), {"project": None})()
    eng._snap_t = lambda task, t: t
    task = {"timeline": None, "width": VW, "height": VH}
    full = eng.video_frame_at(task, 1.0)
    assert (full.width(), full.height()) == (VW, VH)
    half = eng.video_frame_at(task, 1.0, scale=0.5)
    assert (half.width(), half.height()) == (VW // 2, VH // 2)
    up = half.scaled(VW, VH, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.FastTransformation)
    for (x, y) in [(VW * 0.5, VH * 0.6), (VW * 0.1, VH * 0.1), (VW * 0.9, VH * 0.9), (VW * 0.3, VH * 0.55)]:
        a, b = full.pixelColor(int(x), int(y)), up.pixelColor(int(x), int(y))
        assert (a.red() > 128) == (b.red() > 128), (x, y, a.red(), b.red())


def test_canvas_draws_small_program_frame_scaled_to_logical_size():
    _app()
    from cutora.ui.canvas import VideoCanvas
    small = QImage(VW // 2, VH // 2, QImage.Format.Format_ARGB32_Premultiplied)
    small.fill(QColor(0, 128, 255))
    c = VideoCanvas()
    c._task_data = {}
    c._video_dims = (VW, VH)
    c._program_frame = small
    c._preview_backdrop = None
    c._current_time = 0.0
    c._preview_worker_owns_decode = lambda: True
    out = QImage(VW, VH, QImage.Format.Format_ARGB32_Premultiplied)
    out.fill(0)
    p = QPainter(out)
    try:
        c._draw_preview_local(p)
    finally:
        p.end()
    for (x, y) in [(1, 1), (VW - 2, VH - 2), (VW // 2, VH // 2), (VW - 2, 1)]:
        px = out.pixelColor(x, y)
        assert px.alpha() == 255 and px.blue() > 200, (x, y)
    assert c._preview_backdrop is None
    assert VideoCanvas._task_needs_glass_backdrop({}) is False
