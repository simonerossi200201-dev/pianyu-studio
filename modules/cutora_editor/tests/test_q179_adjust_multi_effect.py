# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q179 · 调整图层的效果可以多选、同时起作用（镜头模糊 + 调色压暗 …）。

* 存法：effect["kinds"] + 每种模糊自己的强度 amount_<种类>；固定顺序 马赛克 → 高斯 → 镜头 → 调色；
* 老工程（单选 kind / amount，含 effect.amount 关键帧）画面不变；面板一打开就无损换成新存法；
* 属性面板勾选 / 强度 / 秒表；中央监视器批量属性；预览 = 导出。"""
from __future__ import annotations

import os
import sys
import tempfile

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("CUTORA_SKIP_ENCODER_PROBE", "1")
os.environ.setdefault("CUTORA_USER_DATA", tempfile.mkdtemp(prefix="cutora_q179_"))
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from test_q174_adjust_track import _app, _arr, _img, _ov, _sharp  # noqa: E402

BOX = (60, 60, 80, 80)


def _multi(kinds, **kw):
    ov = _ov(x=50, y=50, w=100, h=100)
    ov["solid"]["on"] = False
    ov["effect"].update({"on": True, "kinds": list(kinds)})
    ov["effect"].update(kw)
    return ov


def test_effect_of_legacy_and_new_shape():
    from cutora.render import adjust_layer as AL
    old = _ov()
    old["effect"].update({"on": True, "kind": "mosaic", "amount": 55})
    e = AL.effect_of(old)
    assert e["kinds"] == ["mosaic"] and e["amount_mosaic"] == 55 and e["amount_lens_blur"] == 55
    assert AL.effect_label(e) == "马赛克" and AL.effect_active(e)
    e = AL.effect_of(_multi(["grade", "lens_blur", "nope", "grade"], amount_lens_blur=70))
    assert e["kinds"] == ["lens_blur", "grade"] and e["amount_lens_blur"] == 70      # 按固定顺序、去重、丢坏值
    assert AL.effect_label(e) == "镜头模糊+调色"
    assert not AL.effect_active(AL.effect_of(_multi([])))                           # 一种都没勾：不算
    # 串着做的半径 = 各自相加（借够像素，边缘不发硬）
    two = {"kinds": ["blur", "mosaic"], "amount_blur": 50, "amount_mosaic": 50}
    r = AL.effect_radius_px(two, 1920)
    assert r == pytest.approx(AL.effect_radius_px({"kind": "blur", "amount": 50}, 1920)
                              + AL.effect_radius_px({"kind": "mosaic", "amount": 50}, 1920))


def test_lens_blur_plus_grade_both_apply():
    _app()
    from cutora.render import adjust_layer as AL
    ref = _arr(_img(200, 200, noise=True))
    # 只镜头模糊
    a_lens = _img(200, 200, noise=True)
    AL.apply_item(a_lens, _multi(["lens_blur"], amount_lens_blur=60))
    a_lens = _arr(a_lens)
    # 镜头模糊 + 调色压暗
    both = _img(200, 200, noise=True)
    ov = _multi(["lens_blur", "grade"], amount_lens_blur=60)
    ov["effect"]["grade"] = {"exposure": -1.0}
    AL.apply_item(both, ov)
    both = _arr(both)
    assert _sharp(both, BOX) < _sharp(ref, BOX) * 0.6                     # 还是糊的
    x, y, w, h = BOX
    assert both[y:y + h, x:x + w].mean() < a_lens[y:y + h, x:x + w].mean() * 0.75     # 而且压暗了
    assert np.array_equal(both[:40], ref[:40])                             # 形状外不动
    # 等于「先镜头模糊、再在同一块里调色」分两层做（调色逐像素，结果一样）
    seq = _img(200, 200, noise=True)
    AL.apply_item(seq, _multi(["lens_blur"], amount_lens_blur=60))
    g = _multi(["grade"])
    g["effect"]["grade"] = {"exposure": -1.0}
    AL.apply_item(seq, g)
    assert np.abs(_arr(seq)[y:y + h, x:x + w] - both[y:y + h, x:x + w]).max() <= 1
    # 马赛克 + 高斯：各自的强度都起作用
    m1 = _img(200, 200, noise=True)
    AL.apply_item(m1, _multi(["mosaic"], amount_mosaic=40))
    m2 = _img(200, 200, noise=True)
    AL.apply_item(m2, _multi(["mosaic", "blur"], amount_mosaic=40, amount_blur=40))
    a1, a2 = _arr(m1)[y:y + h, x:x + w], _arr(m2)[y:y + h, x:x + w]

    def energy(a):                       # 相邻像素差的平方和：模糊会把马赛克的硬台阶抹软
        r = a.mean(axis=2)
        return float((np.diff(r, axis=1) ** 2).sum() + (np.diff(r, axis=0) ** 2).sum())
    assert np.abs(a2 - a1).mean() > 2 and energy(a2) < energy(a1) * 0.8


def test_migrate_keeps_picture_and_keyframes():
    _app()
    from cutora.render import adjust_layer as AL
    from cutora.render.keyframe import bake_host, path_enabled, set_point
    old = _ov(x=50, y=50, w=100, h=100)
    old["solid"]["on"] = False
    old["effect"].update({"on": True, "kind": "blur", "amount": 30})
    set_point(old, "effect.amount", 0.0, 10.0)
    set_point(old, "effect.amount", 2.0, 90.0)

    def render(o, t):
        img = _img(200, 200, noise=True)
        AL.apply_item(img, bake_host(o, t))
        return _arr(img)
    before = [render(old, t) for t in (0.0, 1.0, 2.0)]
    assert AL.migrate_effect(old)
    assert old["effect"]["kinds"] == ["blur"] and old["effect"]["amount_blur"] == 30
    assert path_enabled(old, "effect.amount_blur") and not path_enabled(old, "effect.amount")
    assert [np.array_equal(a, render(old, t)) for a, t in zip(before, (0.0, 1.0, 2.0))] == [True] * 3
    assert not AL.migrate_effect(old)                                    # 只换一次
    # 勾 / 取消
    assert AL.set_effect_kind(old, "grade", True)
    assert old["effect"]["kinds"] == ["blur", "grade"]
    AL.set_effect_kind(old, "blur", False)
    assert old["effect"]["kinds"] == ["grade"] and old["effect"]["kind"] == "grade"
    off = _ov()
    AL.set_effect_kind(off, "mosaic", True)
    assert off["effect"]["on"] and AL.effect_of(off)["kinds"] == ["mosaic", "lens_blur"]   # 固定先马赛克


def test_preview_matches_export_with_multiple_effects(tmp_path):
    from test_q163_unified_stack import _export_frame
    from test_q175_playback_adjust3 import _adjust_task
    _app()
    from cutora.preview.engine import PreviewEngine
    task, _tr, ov = _adjust_task(tmp_path, "blur")
    ov["effect"].update({"kinds": ["lens_blur", "grade"], "amount_lens_blur": 50,
                         "grade": {"exposure": -0.8, "saturation": -0.5}})
    eng = PreviewEngine()
    try:
        a = _arr(eng.frame_at(task, 1.0))
        assert np.array_equal(a, _arr(_export_frame(task)))
        assert np.array_equal(_arr(eng.display_frame_at(task, 1.0, 1.0)), a)
    finally:
        eng.close()


def test_property_panel_multi_select_and_undo():
    from PySide6.QtWidgets import QApplication
    from test_q145b_timeline_shortcuts import _load
    win, _tl, _task = _load()
    win.timer.stop()
    win.timeline.position = 0
    ov = win.add_adjust_item(start=0.0, dur=4.0)
    for _ in range(5):
        QApplication.processEvents()
    pp = win.prop_panel
    ap = pp.ov_adjust
    assert ap._ov is ov
    assert "effect.amount_lens_blur" in pp._kf_extra and "effect.amount_mosaic" in pp._kf_extra
    n_hist = win.history_mgr.current_index
    ap.kind_checks["grade"].setChecked(True)                 # 勾上调色：效果总开关跟着打开
    assert ap.effect_on.isChecked()
    ap.g_exp.setValue(-1.0)
    ap.amt_lens.setValue(65)
    ap._emit_timer.stop()
    ap._flush()
    for _ in range(5):
        QApplication.processEvents()
    cur = win.tasks[win.curr_idx]["image_overlays"][-1]
    e = cur["effect"]
    assert e["on"] and e["kinds"] == ["lens_blur", "grade"] and e["amount_lens_blur"] == 65
    assert e["grade"]["exposure"] == pytest.approx(-1.0)
    assert ap._amount_rows["lens_blur"].isVisibleTo(ap) and not ap._amount_rows["mosaic"].isVisibleTo(ap)
    assert ap._grade_rows[0].isVisibleTo(ap) and ap._hl_row.isVisibleTo(ap)
    assert win.history_mgr.current_index == n_hist + 1                      # 一次连续调整 = 一步撤销
    win.undo()
    cur = win.tasks[win.curr_idx]["image_overlays"][-1]
    assert not cur["effect"]["on"]


def test_monitor_props_toggle_effects_on_all_selected(tmp_path, monkeypatch):
    monkeypatch.setenv("CUTORA_USER_DATA", str(tmp_path / "ud"))
    _app()
    from PySide6.QtWidgets import QMenu
    from test_q177_stack_order import _monitor
    dlg, g, tasks = _monitor(tmp_path)
    try:
        g.selected = {0, 1, 2}
        g.active = 0
        panel = g.layer_panel
        panel.sync()
        row = next(i for i, r in enumerate(panel._layers) if r["kind"] == "adjust")
        idx = panel.model.index(row, 0)
        panel.lst.selectionModel().select(idx, panel.lst.selectionModel().SelectionFlag.ClearAndSelect)
        ref = g.current_layer_ref(g.tiles[0], None, None)
        menu = QMenu()
        sub = g.add_layer_props_menu(menu, ref, g.selected_tiles())
        w = next(a.defaultWidget() for a in sub.actions() if hasattr(a, "defaultWidget") and a.defaultWidget())
        assert w._props_widgets["effect.use_blur"].isChecked()
        assert not w._props_widgets["effect.use_grade"].isChecked()
        w._props_widgets["effect.use_grade"].setChecked(True)
        w._props_widgets["effect.grade.exposure"].setValue(-0.5)
        w._props_flush()
        for t in tasks:
            ov = next(o for o in t["image_overlays"] if o.get("type") == "adjust")
            assert ov["effect"]["kinds"] == ["blur", "grade"]
            assert ov["effect"]["grade"]["exposure"] == pytest.approx(-0.5)
        w._props_close()
        g.undo()
        for t in tasks:
            ov = next(o for o in t["image_overlays"] if o.get("type") == "adjust")
            assert "kinds" not in ov["effect"] and ov["effect"]["kind"] == "blur"
    finally:
        dlg.close()
