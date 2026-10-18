# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q165 · 抠像：消除溢色单独开关 / 方式 / 范围；蒙版阈值在主区；老工程画面不变；预览透明处棋盘格。"""
import os
import sys

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_q152b_masks_keyer import _green_scene  # noqa: E402


def test_despill_off_by_default_keeps_colors():
    from cutora.fx import keyer as K
    img = _green_scene()
    # 绿衣服（实心前景里偏绿的颜色）—— 只抠像时不该被改
    img[150:170, 130:150] = (60, 140, 70)
    out, a = K.apply(img, {"enabled": True})
    assert np.array_equal(out, img) and a[5, 5] == 0
    k = K.normalize({"enabled": True})
    assert k["despill_on"] is False and k["despill_mode"] == "limit" and k["despill_scope"] == "edges"


def test_despill_modes_and_scope():
    from cutora.fx import keyer as K
    img = _green_scene()
    img[150:170, 130:150] = (60, 140, 70)          # 白方块里的一块绿衣服（不透明）
    base = {"enabled": True, "despill_on": True, "white": 0.4}     # 不透明阈值：半透的绿衣服填实
    edge, a = K.apply(img, dict(base))
    assert a[160, 140] == 255
    # 只处理边缘：实心前景深处的绿衣服不动；背景（全透明处）的绿被压掉
    assert tuple(edge[160, 140]) == (60, 140, 70)
    g = edge[5, 5]
    assert int(g[1]) <= max(int(g[0]), int(g[2])) + 2
    # 整个画面 + 标准：绿衣服的 G 被压到 R/B 平均（65）
    allp, _ = K.apply(img, dict(base, despill_scope="all"))
    r, gg, b = (int(x) for x in allp[160, 140])
    assert (r, b) == (60, 70) and abs(gg - 65) <= 1
    # 肤色（G 不超出 R/B 平均）标准模式下不动
    assert tuple(allp[80, 160]) == (225, 180, 150)
    # 强力（老算法）也能用
    axis, _ = K.apply(img, dict(base, despill_scope="all", despill_mode="axis"))
    assert not np.array_equal(axis, allp)
    # 签名带上新键
    s1 = K.signature(K.normalize(dict(base)))
    s2 = K.signature(K.normalize(dict(base, despill_scope="all")))
    assert s1 != s2


def test_legacy_saved_keyer_keeps_old_look():
    from cutora.fx import keyer as K
    old_saved = {k: v for k, v in K.DEFAULTS.items()
                 if k not in ("despill_on", "despill_mode", "despill_scope")}
    old_saved.update(enabled=True, despill=0.6)
    k = K.normalize(old_saved)
    assert k["despill_on"] is True and k["despill_mode"] == "axis" and k["despill_scope"] == "all"
    # 老工程里溢色抑制拉到 0 的：仍然不去溢色
    k0 = K.normalize(dict(old_saved, despill=0.0))
    assert k0["despill_on"] is False
    # 存过一次（新版本写回整份）就按新键走
    k2 = K.normalize(dict(k, despill_on=False))
    assert k2["despill_on"] is False


def test_panel_despill_controls_and_thresholds():
    from test_q152a2_keyable_motion import _panel_env
    win, panel, task, ov, clip = _panel_env("clip")
    try:
        fx = panel.vid_fx
        fx.key_enable.setChecked(True)
        clip2 = task["timeline"].tracks[0].clips[0]
        assert clip2.keyer["despill_on"] is False
        assert fx.key_despill_on.isEnabled() and not fx.key_despill_box.isEnabled()
        # 阈值在主区（不在折叠的「高级」里）
        assert fx.key_black.isVisibleTo(fx) or fx.sec_key.isExpanded() is False
        fx.key_black.setValue(20)
        fx.key_white.setValue(80)
        assert abs(clip2.keyer["black"] - 0.2) < 1e-6 and abs(clip2.keyer["white"] - 0.8) < 1e-6
        fx.key_despill_on.setChecked(True)
        assert clip2.keyer["despill_on"] is True and fx.key_despill_box.isEnabled()
        fx.key_despill_mode.setCurrentText("axis")
        fx.key_despill_scope.setCurrentText("all")
        assert clip2.keyer["despill_mode"] == "axis" and clip2.keyer["despill_scope"] == "all"
        fx.key_despill_on.setChecked(False)
        assert clip2.keyer["despill_on"] is False and not fx.key_despill_box.isEnabled()
    finally:
        win.close()


def test_canvas_checker_behind_transparent_video():
    from PySide6.QtWidgets import QApplication
    from test_q152b_masks_keyer import _canvas_env
    from cutora.ui.alpha_checker import task_has_alpha_fx
    c, task, ov, win, panel = _canvas_env()
    try:
        assert not task_has_alpha_fx(task)
        img0 = c.grab().toImage()
        t = c._preview_target_rect()
        px = (int(t.x()) + 3, int(t.y()) + 3)
        c0 = img0.pixelColor(*px)
        assert (c0.red(), c0.green(), c0.blue()) == (0, 0, 0)       # 没有抠像：照旧黑底
        from cutora.model.timeline_model import Clip, Track
        tl = task["timeline"]
        tr = Track("video")
        clip = Clip("nosuch_source", 0.0, 5.0, 0.0)       # 没有源：画不出来 = 全透明
        tr.clips.append(clip)
        tl.tracks.insert(0, tr)
        assert not task_has_alpha_fx(task)
        clip.keyer = {"enabled": True}
        assert task_has_alpha_fx(task)
        c.repaint()
        QApplication.processEvents()
        img1 = c.grab().toImage()
        seen = {img1.pixelColor(px[0] + dx, px[1] + dy).red() for dx in (0, 9) for dy in (0, 9)}
        assert seen != {0}
        c.alpha_checker = False
        c.repaint()
        QApplication.processEvents()
        c2 = c.grab().toImage().pixelColor(*px)
        assert (c2.red(), c2.green(), c2.blue()) == (0, 0, 0)
    finally:
        win.close()
