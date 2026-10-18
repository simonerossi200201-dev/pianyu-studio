# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q167 · 贝塞尔遮罩：数据 / 栅格化 / 插点不变形 / 手柄编辑；画布钢笔绘制和编辑。"""
import os
import sys

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PySide6.QtCore import QEvent, Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402


def _blob():
    """四个平滑锚点的「圆」。"""
    k = 0.55 * 0.3
    return {"type": "bezier", "points": [
        [0.5, 0.2, -k, 0, k, 0], [0.8, 0.5, 0, -k, 0, k],
        [0.5, 0.8, k, 0, -k, 0], [0.2, 0.5, 0, k, 0, -k]]}


def test_bezier_normalize_raster_and_edit_helpers():
    from cutora.fx import masks as M
    m = M.normalize(_blob())
    assert m["type"] == "bezier" and len(m["points"]) == 4 and len(m["points"][0]) == 6
    # 两个值的老写法也收（手柄补 0）；少于 2 个锚点不合法
    assert M.normalize({"type": "bezier", "points": [[0.1, 0.1], [0.9, 0.9]]})["points"][1] == [0.9, 0.9, 0, 0, 0, 0]
    assert M.normalize({"type": "bezier", "points": [[0.1, 0.1]]}) is None
    # 栅格化 ≈ 圆（半径 0.3 → 面积 π·0.09 ≈ 0.283）
    a = M.rasterize(m, 200, 200)
    assert a[100, 100] == 1.0 and a[5, 5] == 0.0
    assert abs(a.mean() - np.pi * 0.09) < 0.01
    # 圆比同顶点的菱形（尖角）大
    sharp = M.normalize({"type": "bezier", "points": [p[:2] for p in m["points"]]})
    assert M.rasterize(sharp, 200, 200).mean() < a.mean() - 0.05
    # 外接框按曲线算
    bx = M.bounds(m)
    assert bx[0] == pytest.approx(0.2, abs=0.01) and bx[2] == pytest.approx(0.6, abs=0.01)
    # 中点插点：多一个锚点，形状不变
    m2 = M.insert_point(m, 0, 0.0, 0.0)
    assert len(m2["points"]) == 5
    assert np.abs(M.rasterize(m2, 200, 200) - a).mean() < 0.002
    # 拖锚点：手柄跟着走（相对偏移）
    m3 = M.with_point(m, 0, 0.5, 0.1)
    assert m3["points"][0][2:] == m["points"][0][2:]
    # 平移：手柄不变
    mv = M.moved(m, 0.1, 0.0)
    assert mv["points"][1][:2] == pytest.approx([0.9, 0.5]) and mv["points"][1][2:] == m["points"][1][2:]
    # 拖手柄：对称（对面那根反向、保持自己的长度）/ Alt 打断
    h = M.with_handle(m, 0, "out", 0.5 + 0.1, 0.2 + 0.1)
    p = h["points"][0]
    assert p[4:6] == pytest.approx([0.1, 0.1])
    assert p[2] < 0 and p[3] < 0 and np.hypot(p[2], p[3]) == pytest.approx(0.165, abs=1e-6)
    h2 = M.with_handle(m, 0, "out", 0.6, 0.3, symmetric=False)
    assert h2["points"][0][2:4] == m["points"][0][2:4]
    # 尖角 ↔ 平滑
    t1 = M.toggle_smooth(m, 0)
    assert t1["points"][0][2:] == [0, 0, 0, 0]
    t2 = M.toggle_smooth(t1, 0)
    assert any(abs(v) > 1e-6 for v in t2["points"][0][2:])
    # 删点：至少留 2 个
    two = M.normalize({"type": "bezier", "points": [[0.1, 0.1, 0, 0, 0.3, 0], [0.9, 0.9, 0, 0, -0.3, 0]]})
    assert M.remove_point(two, 0) is None
    assert len(M.remove_point(m, 0)["points"]) == 3
    # 签名（缓存键）随手柄变
    assert M.signature([m]) != M.signature([h])


def test_canvas_pen_draw_and_edit_bezier():
    from test_q145_canvas_shortcuts import _drag, _mouse
    from test_q152b_masks_keyer import _canvas_env, _to_widget
    from cutora.ui.canvas_mask import Frame, _host_and_rec
    c, task, ov, win, panel = _canvas_env()
    try:
        fx = panel.ov_fx
        fx.btn_bezier.click()
        assert c._mask_mode and c._mask_mode["kind"] == "bezier"
        host, rec, _k = _host_and_rec(c)
        frame = Frame.of(host, rec)

        def W(nx, ny):
            return _to_widget(c, frame.to_canvas(nx, ny))
        # 1：点 = 尖角
        x, y = W(0.2, 0.2)
        c.mousePressEvent(_mouse(QEvent.Type.MouseButtonPress, x, y))
        c.mouseReleaseEvent(_mouse(QEvent.Type.MouseButtonRelease, x, y, buttons=Qt.MouseButton.NoButton))
        # 2：按住拖 = 平滑（对称手柄）
        x0, y0 = W(0.8, 0.3)
        x1, y1 = W(0.9, 0.4)
        _drag(c, x0, y0, x1, y1)
        # 3：Alt 拖 = 只动出手柄
        x0, y0 = W(0.5, 0.8)
        x1, y1 = W(0.4, 0.9)
        _drag(c, x0, y0, x1, y1, mods=Qt.KeyboardModifier.AltModifier)
        c.repaint()                                          # 画钢笔预览
        QApplication.processEvents()
        assert "贝塞尔" in (c._mask_mode and __import__("cutora.ui.canvas_mask", fromlist=["x"]).hud_text(c))
        # 点回起点 = 闭合
        x, y = W(0.2, 0.2)
        c.mousePressEvent(_mouse(QEvent.Type.MouseButtonPress, x, y))
        assert c._mask_mode is None
        bz = [m for m in ov["masks"] if m["type"] == "bezier"]
        assert len(bz) == 1
        pts = bz[0]["points"]
        assert len(pts) == 3
        assert pts[0][2:] == [0, 0, 0, 0]
        assert pts[1][4:6] == pytest.approx([0.1, 0.1], abs=0.02) and pts[1][2:4] == pytest.approx([-0.1, -0.1], abs=0.02)
        assert pts[2][4:6] == pytest.approx([-0.1, 0.1], abs=0.02) and pts[2][2:4] == [0, 0]
        mid = bz[0]["id"]
        # 编辑：选中后有锚点 / 手柄 / 中点
        c.repaint()
        QApplication.processEvents()
        geom = c._mask_geom
        assert geom["mask"]["id"] == mid and "v1" in geom["handles"] and "o1" in geom["handles"]
        assert len(geom["mids"]) == 3
        # 拖出手柄 o1：入手柄对称跟着转
        hx, hy = _to_widget(c, geom["handles"]["o1"])
        tx, ty = W(0.8 + 0.1, 0.3 - 0.1)
        n_hist = len(win.history_mgr.history)
        _drag(c, hx, hy, tx, ty)
        p1 = ov["masks"][-1]["points"][1]
        assert p1[4:6] == pytest.approx([0.1, -0.1], abs=0.02) and p1[2:4] == pytest.approx([-0.1, 0.1], abs=0.02)
        assert len(win.history_mgr.history) == n_hist + 1
        # 点曲线中点 = 插点
        c.repaint()
        QApplication.processEvents()
        mx, my = _to_widget(c, c._mask_geom["mids"][0][1])
        c.mousePressEvent(_mouse(QEvent.Type.MouseButtonPress, mx, my))
        c.mouseReleaseEvent(_mouse(QEvent.Type.MouseButtonRelease, mx, my, buttons=Qt.MouseButton.NoButton))
        assert len(ov["masks"][-1]["points"]) == 4
        # 双击锚点 0：尖角 → 平滑
        c.repaint()
        QApplication.processEvents()
        vx, vy = _to_widget(c, c._mask_geom["handles"]["v0"])
        c.mouseDoubleClickEvent(_mouse(QEvent.Type.MouseButtonDblClick, vx, vy))
        assert any(abs(v) > 1e-6 for v in ov["masks"][-1]["points"][0][2:])
        # Alt+点锚点 = 删
        c.repaint()
        QApplication.processEvents()
        vx, vy = _to_widget(c, c._mask_geom["handles"]["v2"])
        c.mousePressEvent(_mouse(QEvent.Type.MouseButtonPress, vx, vy, Qt.KeyboardModifier.AltModifier))
        c.mouseReleaseEvent(_mouse(QEvent.Type.MouseButtonRelease, vx, vy, Qt.KeyboardModifier.AltModifier,
                                   Qt.MouseButton.NoButton))
        assert len(ov["masks"][-1]["points"]) == 3
        # 列表里叫「贝塞尔」
        assert "贝塞尔" in fx.mask_list.item(fx.mask_list.count() - 1).text()
        # 钢笔：右键退一步、Enter 闭合、点太少取消
        fx.btn_bezier.click()
        x, y = W(0.1, 0.1)
        c.mousePressEvent(_mouse(QEvent.Type.MouseButtonPress, x, y))
        c.mouseReleaseEvent(_mouse(QEvent.Type.MouseButtonRelease, x, y, buttons=Qt.MouseButton.NoButton))
        c.mousePressEvent(_mouse(QEvent.Type.MouseButtonPress, x, y, button=Qt.MouseButton.RightButton,
                                 buttons=Qt.MouseButton.RightButton))
        assert c._mask_mode["points"] == []
        from PySide6.QtGui import QKeyEvent
        c.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier))
        assert c._mask_mode is None and len([m for m in ov["masks"] if m["type"] == "bezier"]) == 1
    finally:
        c.close()
        win.close()
