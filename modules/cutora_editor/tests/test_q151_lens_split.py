# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q151（Luca 2026-10-01）：

1. 批量加载·视频的左右分屏要抖音那种：两格等大并排，每条视频完整放进格子，
   空处垫模糊 —— 用「左右完整」，空处每格垫自己那条的模糊（或整幅垫第一条）。
   「左右」改叫「左右裁切」，两个一眼分得开。
2. 背景模糊不细：0 不糊，给一点就糊成一片。改成镜头模糊（六边形光圈、亮点成
   光斑），程度按幂曲线走，前半段很细。
3. 老工程：已经开着模糊背景的片段、没写 backdrop 的老分屏，观感不变。
"""
from __future__ import annotations

import os

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QColor, QImage, QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication(["q151"])


def _bgr(arr):
    arr = np.ascontiguousarray(arr, dtype=np.uint8)
    h, w = arr.shape[:2]
    return QImage(arr.data, w, h, w * 3, QImage.Format.Format_BGR888).copy()


def _arr(img, w=None, h=None):
    if w is not None and (img.width(), img.height()) != (w, h):
        from PySide6.QtCore import Qt
        img = img.scaled(w, h, Qt.AspectRatioMode.IgnoreAspectRatio,
                         Qt.TransformationMode.SmoothTransformation)
    img = img.convertToFormat(QImage.Format.Format_BGR888)
    ww, hh = img.width(), img.height()
    buf = np.frombuffer(img.constBits(), np.uint8).reshape(hh, img.bytesPerLine())
    return buf[:, :ww * 3].reshape(hh, ww, 3).astype(np.float32)


def _scene(w=540, h=960):
    import cv2
    img = np.full((h, w, 3), (40, 60, 90), np.uint8)
    for i in range(0, h, 80):
        cv2.putText(img, "CUTORA %d" % i, (20, i + 50), cv2.FONT_HERSHEY_SIMPLEX,
                    1.3, (200, 220, 240), 3)
    return img


# ───────────────────────────── 程度曲线 ─────────────────────────────

def test_lens_radius_curve_is_fine_at_the_low_end():
    from cutora.render.fill_bg import lens_radius_px
    long = 1920.0
    r = [lens_radius_px(a, long) for a in (0, 5, 10, 20, 45, 70, 100)]
    assert r[0] == 0.0
    assert all(a < b for a, b in zip(r, r[1:])), r
    assert r[1] < 1.0 and r[2] < 3.0            # 给一点点 = 轻微失焦，不是糊死
    assert 20.0 < r[4] < 35.0
    assert abs(r[-1] - 96.0) < 1e-6


def test_blur_grows_gradually_not_all_at_once(app):
    from cutora.render.fill_bg import blur_image
    src = _scene()
    q = _bgr(src)
    h, w = src.shape[:2]
    import cv2

    def sharp(arr):                       # 细节量：拉普拉斯方差，越糊越小
        return float(cv2.Laplacian(arr.mean(axis=2), cv2.CV_32F).var())

    base = sharp(src.astype(np.float32))
    det = [sharp(_arr(blur_image(q, a, "lens"), w, h)) for a in (5, 10, 20, 40, 70, 100)]
    assert all(a > b for a, b in zip(det, det[1:])), det
    assert det[0] > base * 0.08, (det[0], base)       # 给一点点：细节还留着
    # 旧做法：5 就已经糊没了（Luca 说的「稍微给一点就非常模糊」）
    legacy5 = sharp(_arr(blur_image(q, 5, "legacy"), w, h))
    assert det[0] > legacy5 * 50, (det[0], legacy5)
    assert det[3] > legacy5 * 5, (det[3], legacy5)    # 40 也还没有旧版 5 那么糊


def test_zero_amount_returns_same_frame(app):
    from cutora.render.fill_bg import blur_image, lens_blur
    q = _bgr(_scene())
    assert lens_blur(q, 0) is q
    assert blur_image(q, 0, "lens") is q


# ───────────────────────────── 镜头模糊的样子 ─────────────────────────────

def test_flat_color_keeps_its_color(app):
    from cutora.render.fill_bg import blur_image
    img = np.full((400, 300, 3), (30, 140, 220), np.uint8)   # 亮橙
    out = _arr(blur_image(_bgr(img), 60, "lens", 0.35), 300, 400)
    assert np.abs(out - img.astype(np.float32)).max() <= 2.0


def test_bright_point_becomes_hexagon_bokeh(app):
    from cutora.render.fill_bg import hex_kernel, lens_blur
    img = np.full((400, 400, 3), 20, np.uint8)
    img[198:202, 198:202] = 255
    out = lens_blur(_bgr(img), 80, 0.35)
    a = _arr(out)
    cy, cx = a.shape[0] // 2, a.shape[1] // 2
    lum = a.mean(axis=2)
    ring = lum[cy - 12:cy + 13, cx - 12:cx + 13]
    assert lum[cy, cx] > 20 + 3, lum[cy, cx]          # 亮点散开了还看得见
    assert ring.std() > 0.5
    # 光圈本身：六边形 —— 沿顶点方向比沿边心方向伸得远
    k = hex_kernel(20.0)
    n = k.shape[0]
    c = n // 2
    import math
    def reach(deg):
        for d in range(c, -1, -1):
            x = int(round(c + d * math.cos(math.radians(deg))))
            y = int(round(c + d * math.sin(math.radians(deg))))
            if 0 <= x < n and 0 <= y < n and k[y, x] > 0:
                return d
        return 0
    vertex = reach(15.0)          # _HEX_ROT_DEG 方向是顶点
    edge = reach(45.0)            # 转 30° 是边心
    assert vertex > edge, (vertex, edge)
    assert abs(float(k.sum()) - 1.0) < 1e-4


def test_highlights_make_the_bokeh_brighter(app):
    from cutora.render.fill_bg import lens_blur
    img = np.full((300, 300, 3), 20, np.uint8)
    img[148:152, 148:152] = 255
    q = _bgr(img)
    plain = _arr(lens_blur(q, 80, 0.0)).mean(axis=2)
    hot = _arr(lens_blur(q, 80, 0.35)).mean(axis=2)
    assert hot.max() > plain.max() + 5


def test_lens_blur_keeps_alpha_for_argb(app):
    from cutora.render.fill_bg import lens_blur
    img = QImage(200, 120, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(QColor(200, 60, 60, 255))
    out = lens_blur(img, 50)
    assert out.format() == QImage.Format.Format_ARGB32_Premultiplied
    assert out.pixelColor(out.width() // 2, out.height() // 2).alpha() == 255


# ───────────────────────────── 片段背景填充的老 / 新 ─────────────────────────────

def test_fill_spec_keeps_old_blur_clips_legacy_and_new_ones_lens():
    from cutora.render.fill_bg import fill_spec, set_fill
    assert fill_spec(None)["blur_style"] == "lens"
    # 老工程：存过 bg_fill、正开着模糊 → 老样子
    old = {"bg_fill": {"mode": "blur", "blur": 45.0, "scale": 1.12, "x": 0.0, "y": 0.0,
                       "rotation": 0.0, "dim": 0.25}}
    assert fill_spec(old)["blur_style"] == "legacy"
    # 老工程里没开模糊的片段，以后打开模糊就是镜头模糊
    off = {"bg_fill": {"mode": "none", "blur": 45.0}}
    assert fill_spec(off)["blur_style"] == "lens"
    set_fill(off, mode="blur")
    assert off["bg_fill"]["blur_style"] == "lens"
    # 显式改成镜头模糊以后就记住
    set_fill(old, blur_style="lens", highlights=0.6)
    assert fill_spec(old)["blur_style"] == "lens"
    assert abs(fill_spec(old)["highlights"] - 0.6) < 1e-9
    junk = fill_spec({"bg_fill": {"mode": "blur", "blur_style": "螺旋", "highlights": "x"}})
    assert junk["blur_style"] == "lens" and junk["highlights"] == 0.35


def test_new_clip_defaults_are_lens():
    from cutora.model.timeline_model import Clip
    c = Clip("s", 0.0, 1.0, 0.0)
    assert c.bg_fill["blur_style"] == "lens"
    assert c.bg_fill["highlights"] == 0.35


def test_property_panel_style_and_highlight_rows(app):
    from cutora.model.timeline_model import Clip
    from cutora.ui.panels.property import PropertyPanel
    p = PropertyPanel()
    c = Clip("s", 0.0, 1.0, 0.0)
    c.bg_fill = {"mode": "blur", "blur": 45.0, "scale": 1.12, "x": 0.0, "y": 0.0,
                 "rotation": 0.0, "dim": 0.25}          # 老工程的片段
    p._video_clip = c
    p.block_signals = True
    p._load_bg_fill(c)
    p.block_signals = False
    assert p.vid_bg_style.currentData() == "legacy"
    assert not p.vid_bg_hl.isEnabled()
    p.vid_bg_style.setCurrentIndex(p.vid_bg_style.findData("lens"))
    assert c.bg_fill["blur_style"] == "lens"
    assert p.vid_bg_hl.isEnabled()
    p.vid_bg_hl.setValue(60)
    assert abs(c.bg_fill["highlights"] - 0.6) < 1e-9
    from cutora.ui.panels.prop_reset import _APPLIERS
    _APPLIERS["vid.bg_hl"](p)
    assert abs(c.bg_fill["highlights"] - 0.35) < 1e-9


# ───────────────────────────── 左右完整的模糊背景 ─────────────────────────────

class _Clip:
    def __init__(self):
        self.fit_mode = "fill"


class _Track:
    def __init__(self, cell, color):
        self.split_cell = cell
        self.id = cell
        self.clips = [_Clip()]
        self.color = QColor(color)


def _paint(split, w=200, h=100, seen=None):
    from cutora.render.split_screen import paint_split_video_tracks, painting_backdrop
    modes = []

    def paint_one(p, tr, dw, dh):
        modes.append((tr.id, tr.clips[0].fit_mode, int(dw), int(dh), painting_backdrop()))
        if tr.clips[0].fit_mode == "fit":
            # 竖条素材：适应进格子后左右留白
            side = int(dw) // 4
            p.fillRect(side, 0, int(dw) - 2 * side, int(dh), tr.color)
        else:
            p.fillRect(0, 0, int(dw), int(dh), tr.color)

    canvas = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    canvas.fill(0)
    painter = QPainter(canvas)
    tracks = [_Track("A", "#FF2040"), _Track("B", "#20C040")]
    try:
        paint_split_video_tracks(painter, {"split": split}, tracks, w, h, paint_one)
    finally:
        painter.end()
    return modes, canvas


def test_new_left_right_fit_gets_per_cell_lens_backdrop():
    from cutora.render.split_layout import (
        NEW_BACKDROP, apply_split_to_task, backdrop_spec, normalize_split, recompute,
    )
    task = {}
    sp = apply_split_to_task(task, layout="left_right_fit")
    assert sp["backdrop"] == NEW_BACKDROP
    assert recompute(sp)["backdrop"] == NEW_BACKDROP
    assert normalize_split(sp)["backdrop"] == NEW_BACKDROP
    # 改别的布局再改回来，设置跟着走
    sp = apply_split_to_task(task, backdrop={"mode": "first", "blur": 20})
    assert backdrop_spec(sp)["mode"] == "first" and backdrop_spec(sp)["blur"] == 20.0
    # 没写 backdrop 的老分屏 = Q148 的整幅旧模糊
    old = backdrop_spec({"layout": "left_right_fit"})
    assert old["mode"] == "first" and old["style"] == "legacy"


def test_each_mode_blurs_each_cell_with_its_own_video(app):
    from cutora.render.split_layout import apply_layout, NEW_BACKDROP
    sp = apply_layout({"enabled": True, "layout": "left_right_fit"})
    sp["backdrop"] = dict(NEW_BACKDROP, dim=0.0)
    modes, canvas = _paint(sp)
    fills = sorted(m for m in modes if m[1] == "fill")
    fits = sorted(m for m in modes if m[1] == "fit")
    assert [(m[0], m[2], m[3]) for m in fills] == [("A", 100, 100), ("B", 100, 100)], modes
    assert all(m[4] for m in fills)              # 画底的那一遍标着 backdrop
    assert not any(m[4] for m in fits)
    assert {m[0] for m in fits} == {"A", "B"}
    # 右格留白处是右边那条（绿）的模糊，不是左边那条（红）
    right_gap = canvas.pixelColor(150 + 0, 50)
    gap = canvas.pixelColor(110, 50)
    assert gap.green() > gap.red(), (gap.red(), gap.green())
    left_gap = canvas.pixelColor(5, 50)
    assert left_gap.red() > left_gap.green()
    assert right_gap.alpha() == 255


def test_first_mode_and_legacy_split_paint_one_plate(app):
    from cutora.render.split_layout import apply_layout, NEW_BACKDROP
    sp = apply_layout({"enabled": True, "layout": "left_right_fit"})
    for d in sp["dividers"]:
        d["feather_px"] = 0.0
    modes, canvas = _paint(sp)                               # 老分屏：没写 backdrop
    assert [m[:4] for m in modes if m[1] == "fill"] == [("A", "fill", 200, 100)]
    # Q151 修：视频不能和底相加（以前 Plus 叠在底上，整条发白发透）
    for x, want in ((50, QColor("#FF2040")), (150, QColor("#20C040"))):
        got = canvas.pixelColor(x, 50)
        assert abs(got.red() - want.red()) <= 2 and abs(got.green() - want.green()) <= 2 \
            and abs(got.blue() - want.blue()) <= 2, (x, got.name(), want.name())
    sp["backdrop"] = dict(NEW_BACKDROP, mode="first")
    modes, canvas = _paint(sp)
    assert [m[:4] for m in modes if m[1] == "fill"] == [("A", "fill", 200, 100)]
    gap = canvas.pixelColor(110, 50)                          # 整幅都是第一条（红）的底
    assert gap.red() > gap.green()


def test_left_right_crop_layout_is_unchanged(app):
    from cutora.render.split_layout import apply_layout
    sp = apply_layout({"enabled": True, "layout": "left_right"})
    sp["backdrop"] = {"mode": "each"}                         # 写了也不管：只有左右完整用
    modes, _c = _paint(sp)
    assert modes and all(m[1] == "fill" and not m[4] for m in modes)
    assert len(modes) == 2


def test_assign_split_passes_backdrop_only_for_contain():
    from cutora.batch.split_assign import assign_split
    from cutora.render.split_layout import backdrop_spec

    class TL:
        tracks = []

    task = {"timeline": TL()}
    assign_split(task, "left_right_fit", [], backdrop={"mode": "first", "blur": 30})
    assert backdrop_spec(task["split"])["mode"] == "first"
    assert backdrop_spec(task["split"])["blur"] == 30.0
    task2 = {"timeline": TL()}
    assign_split(task2, "top_bottom", [], backdrop={"mode": "first"})
    assert "backdrop" not in task2["split"]


def test_labels_tell_crop_and_complete_apart():
    from cutora.render.split_layout import LAYOUT_LABELS
    assert LAYOUT_LABELS["left_right"] == "左右裁切"
    assert LAYOUT_LABELS["left_right_fit"] == "左右完整"


def test_batch_dialog_backdrop_rows(app):
    from cutora.batch.batch_dialog import build_batch_dialog
    host = QWidget()
    host.tasks = [{"video": "v1.mp4", "duration": 10.0, "dims": (720, 1280),
                   "image_overlays": []}]
    host.curr_idx = 0
    host.timeline = QWidget()
    dlg = build_batch_dialog(host)
    dlg.ck_split.setChecked(True)
    dlg.cb_split.setCurrentIndex(dlg.cb_split.findData("left_right"))
    # Q155：分屏背景所有布局都能选；左右裁切 + 每格各自时模糊程度不起作用（靠片段自己的填充）
    dlg.cb_backdrop.setCurrentIndex(dlg.cb_backdrop.findData("each"))
    assert dlg.cb_backdrop.isEnabled() and not dlg.sp_backdrop_blur.isEnabled()
    dlg.cb_split.setCurrentIndex(dlg.cb_split.findData("left_right_fit"))
    assert dlg.cb_backdrop.isEnabled() and dlg.sp_backdrop_blur.isEnabled()
    assert "完整" in dlg.cb_split.itemData(dlg.cb_split.currentIndex(), 3)  # ToolTipRole
    dlg.cb_backdrop.setCurrentIndex(dlg.cb_backdrop.findData("full"))
    dlg.sp_backdrop_blur.setValue(30)
    sp = dlg.split_params()
    assert sp["layout"] == "left_right_fit"
    assert sp["backdrop"] == {"mode": "full", "blur": 30.0, "style": "lens"}
    dlg.ck_split.setChecked(False)
    assert not dlg.cb_backdrop.isEnabled()
    # 下次打开记得上次的布局和模糊背景
    dlg2 = build_batch_dialog(host)
    assert dlg2.cb_split.currentData() == "left_right_fit"
    assert dlg2.cb_backdrop.currentData() == "full"
    assert dlg2.sp_backdrop_blur.value() == 30
