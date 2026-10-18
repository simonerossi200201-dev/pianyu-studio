# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q152-F · 检查器里「遮罩运算 / 套用遮罩 / 模糊 / 调色」节点的参数。

蓝绿幕、形状遮罩、AI 抠像节点直接复用检查器里原来那几节（节点 params 和图层老键同形），
这里只管新节点类型（说明文字在上面的「节点」卡片里，不重复）。改动发 ``patch(dict, coalesce)``，由 LayerFxPanel 走 fx.node set。
"""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QCheckBox, QVBoxLayout, QWidget

from cutora.fx import graph as G
from cutora.ui.i18n import tr
from cutora.ui.theme.tokens import SPACE_1
from cutora.ui.widgets.collapsible import CollapsibleSection
from cutora.ui.widgets.inspector import InspectForm, SliderSpin, ValueCombo, add_inspect_row

TYPES = ("matte_ops", "matte_apply", "blur", "color")

OP_ITEMS = (("add", "合并（相加）"), ("subtract", "相减（A 去掉 B）"), ("intersect", "相交（都有才留）"),
            ("max", "取亮"), ("min", "取暗"), ("difference", "差值"))
MODE_ITEMS = (("multiply", "只留遮罩里"), ("cutout", "挖掉遮罩里"), ("replace", "替换透明度"))


class NodeParamsPanel(QWidget):
    patch = Signal(dict, object)            # (params 补丁, coalesce 键或 None)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._loading = False
        self.ntype = None
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.sec = CollapsibleSection(tr("节点参数"))
        self.sec.setExpanded(True)
        host = QWidget()
        lay = QVBoxLayout(host)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(SPACE_1)
        self.forms = {}
        self.w = {}
        self._build_ops(lay)
        self._build_apply(lay)
        self._build_blur(lay)
        self._build_color(lay)
        self.sec.addWidget(host)
        root.addWidget(self.sec)

    # ── 构建 ──
    def _form(self, lay, t):
        f = InspectForm()
        w = QWidget()
        w.setLayout(f)
        lay.addWidget(w)
        self.forms[t] = w
        self._cur = t
        return f

    def _slider(self, key, lo, hi, step, dec, *, factor=1.0, tip=""):
        s = SliderSpin(lo, hi, step, dec)
        if tip:
            s.setToolTip(tip)
        s.valueChanged.connect(lambda v, k=key, fa=factor: self._emit({k: float(v) / fa}, k))
        self.w[(self._cur, key)] = (s, factor)
        return s

    def _check(self, key, text, tip=""):
        c = QCheckBox(text)
        if tip:
            c.setToolTip(tip)
        c.toggled.connect(lambda on, k=key: self._emit({k: bool(on)}, None))
        self.w[(self._cur, key)] = (c, None)
        return c

    def _combo(self, key, items, tip=""):
        c = ValueCombo(items)
        if tip:
            c.setToolTip(tip)
        c.valueChanged.connect(lambda v, k=key: self._emit({k: str(v)}, None))
        self.w[(self._cur, key)] = (c, None)
        return c

    def _build_ops(self, lay):
        f = self._form(lay, "matte_ops")
        add_inspect_row(f, tr("运算"), self._combo("op", OP_ITEMS, tr("B 没接时只对 A 做反转 / 收缩 / 羽化")))
        add_inspect_row(f, "", self._check("invert", tr("反转"), tr("白变黑、黑变白")))
        add_inspect_row(f, tr("收缩"), self._slider("shrink", -100, 100, 1, 0,
                                                  tip=tr("正 = 往里收，负 = 往外扩（画布像素）")))
        add_inspect_row(f, tr("羽化"), self._slider("feather", 0, 200, 1, 0, tip=tr("边缘柔化（画布像素）")))

    def _build_apply(self, lay):
        f = self._form(lay, "matte_apply")
        add_inspect_row(f, tr("方式"), self._combo("mode", MODE_ITEMS,
                                                 tr("只留遮罩里 = 遮罩外透明；挖掉 = 遮罩里透明；替换 = 透明度直接用遮罩")))
        add_inspect_row(f, "", self._check("invert", tr("反转遮罩")))

    def _build_blur(self, lay):
        f = self._form(lay, "blur")
        add_inspect_row(f, tr("大小"), self._slider("size", 0, 200, 0.5, 1, tip=tr("模糊半径（画布像素）")))
        add_inspect_row(f, "", self._check("alpha", tr("透明度也一起糊"),
                                           tr("关掉 = 只糊颜色，边缘不变软（给抠好的人物背后虚化时用）")))

    def _build_color(self, lay):
        f = self._form(lay, "color")
        add_inspect_row(f, tr("亮度"), self._slider("brightness", -100, 100, 1, 0, factor=100.0))
        add_inspect_row(f, tr("对比度"), self._slider("contrast", -100, 100, 1, 0, factor=100.0))
        add_inspect_row(f, tr("饱和度"), self._slider("saturation", -100, 100, 1, 0, factor=100.0,
                                                   tip=tr("-100 = 黑白")))
        add_inspect_row(f, tr("增益"), self._slider("gain", 0, 4, 0.01, 2, tip=tr("整体乘一个系数，1 = 不变")))

    # ── 数据 ──
    def _emit(self, p, coalesce):
        if self._loading or self.ntype is None:
            return
        self.patch.emit(dict(p), ("np", self.ntype, coalesce) if coalesce else None)

    def widget(self, ntype, key):
        got = self.w.get((ntype, key))
        return got[0] if got else None

    def load(self, ntype, params: dict):
        self.ntype = ntype if ntype in TYPES else None
        self.setVisible(self.ntype is not None)
        if self.ntype is None:
            return
        nt = G.node_type(ntype) or {}
        self.sec.setTitle(tr("节点参数 · %s") % nt.get("title", ntype))
        for t, w in self.forms.items():
            w.setVisible(t == ntype)
        defaults = nt["params"]()
        self._loading = True
        try:
            for (t, k), (wdg, fa) in self.w.items():
                if t != ntype:
                    continue
                v = params.get(k, defaults.get(k)) if isinstance(params, dict) else defaults.get(k)
                if isinstance(wdg, SliderSpin):
                    wdg.setValue(float(v or 0.0) * (fa or 1.0))
                elif isinstance(wdg, QCheckBox):
                    wdg.setChecked(bool(v))
                else:
                    wdg.setCurrentText(str(v))
        finally:
            self._loading = False
