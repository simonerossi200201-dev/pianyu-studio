# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q152-B/C · 检查器里的「遮罩」「抠像」两节。视频片段和文本 / 图片层各挂一份。

渐进披露：抠像默认只露 屏幕色（吸管）/ Key 增益 / 平衡 / 溢色抑制 / Matte 柔化 和「查看」，
其余收在「高级」里。遮罩一节：一个列表（名字、模式、显隐），下面是选中那个遮罩的参数。
所有改动都走命令总线（fx.mask_* / fx.keyer_set），一次改动一次撤销；拖滑杆时 400 ms 内
合并成一步。画布上的手柄、多边形绘制、吸管由 cutora/ui/canvas_mask.py 负责，这里只发信号。
"""
from __future__ import annotations

import time

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton, QToolButton,
    QVBoxLayout, QWidget,
)

from cutora.core.log import get_logger
from cutora.fx import graph as G
from cutora.fx import keyer as K
from cutora.fx import masks as M
from cutora.ui.i18n import tr
from cutora.ui.icons import bind_icon
from cutora.ui.theme.tokens import ICON_SM, SPACE_1, SPACE_2
from cutora.ui.widgets.collapsible import CollapsibleSection
from cutora.ui.widgets.inspector import (
    ColorSwatch, InspectForm, SliderSpin, ValueCombo, add_inspect_row, install_inspector_limits,
)

_LOG = get_logger("cutora.ui.panels.layer_fx_panel")
_COALESCE_SEC = 0.4


def _layer_key(params) -> str:
    """图层的身份 = 命令定位器（撤销会深拷贝工程，对象 id 会变，定位器不变）。"""
    p = dict(params or {})
    p.pop("node_id", None)
    return repr(sorted((str(k), repr(v)) for k, v in p.items()))


class LayerFxPanel(QWidget):
    """两节合一的控件。``bind(getter)``：getter() → (host, cmd_params, kind) 或 (None, None, None)。

    kind: "clip" / "image" / "text"。文本没有抠像节。"""

    # 画布要做的事：("draw_polygon") / ("pick_color") / ("select_mask", mask_id)
    canvas_action = Signal(str, object)
    changed = Signal()                      # 有改动落地（检查器 / 画布刷新）

    def __init__(self, parent=None, *, with_keyer: bool = True):
        super().__init__(parent)
        self._getter = None
        self._loading = False
        self._coalesce = {}
        self.active_mask_id = ""
        self.with_keyer = with_keyer
        self.node_id = ""                   # Q152-F：节点编辑器里选中的节点（检查器显示它的参数）
        self._node_layer = None
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.sec_graph = self._build_graph_card()
        root.addWidget(self.sec_graph)
        self.sec_mask = self._build_masks()
        root.addWidget(self.sec_mask)
        self.sec_key = None
        self.ai = None
        self.track = None
        self.node_params = None
        if with_keyer:
            self.sec_key = self._build_keyer()
            root.addWidget(self.sec_key)
            from cutora.ui.panels.node_params_panel import NodeParamsPanel
            self.node_params = NodeParamsPanel()
            self.node_params.patch.connect(self._on_node_params)
            self.node_params.hide()
            root.addWidget(self.node_params)
            # Q152-D：AI 抠像（只对视频片段显示；文本 / 图片层 reload 时会藏起来）
            from cutora.ui.panels.ai_matte_panel import AiMattePanel
            self.ai = AiMattePanel()
            self.ai.canvas_action.connect(self.canvas_action)
            self.ai.changed.connect(self.changed)
            root.addWidget(self.ai)
            # Q152-E：AI 跟踪（只对视频片段显示）
            from cutora.ui.panels.ai_track_panel import AiTrackPanel
            self.track = AiTrackPanel()
            self.track.canvas_action.connect(self.canvas_action)
            self.track.changed.connect(self.changed)
            root.addWidget(self.track)
        install_inspector_limits(self)

    # ─────────────────────────── 节点图（Q152-F） ───────────────────────────

    def _build_graph_card(self):
        sec = CollapsibleSection(tr("节点图"))
        sec.setExpanded(False)
        host = QWidget()
        lay = QVBoxLayout(host)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(SPACE_1)
        self.graph_info = QLabel("")
        self.graph_info.setObjectName("hint")
        self.graph_info.setWordWrap(True)
        lay.addWidget(self.graph_info)
        row = QHBoxLayout()
        row.setSpacing(SPACE_1)
        self.btn_graph_convert = QPushButton(tr("转为节点"))
        self.btn_graph_convert.setToolTip(tr("把这一层的蓝绿幕 / AI 抠像 / 遮罩搬进节点图，画面一模一样；可撤销"))
        self.btn_graph_convert.clicked.connect(self.convert_to_graph)
        self.btn_graph_open = QPushButton(tr("打开节点编辑器"))
        self.btn_graph_open.setToolTip(tr("时间线下方的节点面板（Alt+N）"))
        self.btn_graph_open.clicked.connect(self.open_node_editor)
        self.btn_graph_back = QPushButton(tr("回到图层"))
        self.btn_graph_back.setToolTip(tr("不再看这个节点的参数"))
        self.btn_graph_back.clicked.connect(lambda: self.set_node(""))
        self.btn_graph_delete = QPushButton(tr("删除节点图"))
        self.btn_graph_delete.setToolTip(tr("整张图拿掉，这一层回到不加效果的直通（可撤销）"))
        self.btn_graph_delete.clicked.connect(self.delete_graph)
        for b in (self.btn_graph_convert, self.btn_graph_open, self.btn_graph_back, self.btn_graph_delete):
            b.setObjectName("btnSecondary")
            row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)
        sec.addWidget(host)
        return sec

    def _graph(self):
        layer, _p, kind = self._layer_target()
        if layer is None or kind not in ("clip", "image"):
            return None
        return G.graph_of(layer)

    def set_node(self, nid: str):
        """节点编辑器选中节点 → 检查器显示它的参数（"" = 回到图层）。"""
        nid = str(nid or "")
        if nid == self.node_id:
            return
        self.node_id = nid
        layer, lp, _k = self._layer_target()
        self._node_layer = _layer_key(lp) if layer is not None else None
        self.active_mask_id = ""
        self.reload()
        if nid:
            self.sec_graph.setExpanded(True)
        self.changed.emit()

    def current_node(self):
        g = self._graph()
        if g is None or not self.node_id:
            return None
        return G.node_by_id(g, self.node_id)

    def convert_to_graph(self):
        if self._exec("fx.graph", {"op": "create"}, layer=True):
            self.reload()
            self.open_node_editor()
            return True
        return False

    def delete_graph(self):
        g = self._graph()
        if g is None:
            return False
        G.set_viewer(g.get("uid"), None)
        self.node_id = ""
        if self._exec("fx.graph", {"op": "delete"}, layer=True):
            self.reload()
            return True
        return False

    def open_node_editor(self):
        win = self.window()
        fn = getattr(win, "toggle_node_editor", None)
        if callable(fn):
            fn(True)

    def _on_node_params(self, patch, coalesce):
        if self._loading or not self.node_id:
            return
        self._exec("fx.node", {"op": "set", "id": self.node_id, "patch": {"params": dict(patch)}}, coalesce=coalesce)

    def _reload_graph_card(self, layer, kind, g, node):
        on = layer is not None and kind in ("clip", "image") and self.with_keyer
        self.sec_graph.setVisible(on)
        if not on:
            return
        if g is None:
            self.sec_graph.setTitle(tr("节点图"))
            self.graph_info.setText(tr("想把遮罩合并 / 相减、只在区域里调色或模糊、几种抠像串起来用？"
                                       "「转为节点」后在节点编辑器里自由连。"))
        elif node is None:
            self.sec_graph.setTitle(tr("节点图") + "  · " + tr("%d 个节点") % len(g["nodes"]))
            self.graph_info.setText(tr("这一层按节点图合成，下面的遮罩 / 抠像各节不再单独生效。"
                                       "在节点编辑器里选一个节点，这里显示它的参数。"))
            if not self.sec_graph.isExpanded():
                self.sec_graph.setExpanded(True)
        else:
            nt = G.node_type(node["type"]) or {}
            name = node.get("label") or nt.get("title", node["type"])
            self.sec_graph.setTitle(tr("节点") + "  · " + name)
            self.graph_info.setText(nt.get("doc", "") + ("" if node.get("enabled", True) else
                                                         "  " + tr("（已旁路：Ctrl+P 恢复）")))
        self.btn_graph_convert.setVisible(g is None)
        self.btn_graph_open.setVisible(g is not None)
        self.btn_graph_back.setVisible(node is not None)
        self.btn_graph_delete.setVisible(g is not None and node is None)

    # ─────────────────────────── 遮罩 ───────────────────────────

    def _build_masks(self):
        sec = CollapsibleSection(tr("遮罩"))
        sec.setExpanded(False)
        host = QWidget()
        lay = QVBoxLayout(host)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(SPACE_1)
        row = QHBoxLayout()
        row.setSpacing(SPACE_1)
        self.btn_rect = QPushButton(tr("＋矩形"))
        self.btn_ellipse = QPushButton(tr("＋椭圆"))
        self.btn_poly = QPushButton(tr("＋多边形"))
        self.btn_rect.setToolTip(tr("加一个居中的矩形遮罩，然后在画布上拖它的角 / 边；双击角切换圆角"))
        self.btn_ellipse.setToolTip(tr("加一个居中的椭圆遮罩"))
        self.btn_poly.setToolTip(tr("在画布上一个点一个点地画：点加点，双击或 Enter 闭合，Esc 取消"))
        self.btn_bezier = QPushButton(tr("＋贝塞尔"))
        self.btn_bezier.setToolTip(tr("钢笔：点 = 尖角，按住拖 = 拉出平滑手柄（Alt 只动一侧），点回起点 / 双击 / Enter 闭合。\n"
                                      "画好后：拖锚点 / 手柄，点曲线上的小圆插点，Alt+点锚点删除，双击锚点 尖角 ↔ 平滑"))
        for b in (self.btn_rect, self.btn_ellipse, self.btn_poly, self.btn_bezier):
            b.setObjectName("btnSecondary")
            row.addWidget(b)
        lay.addLayout(row)
        self.btn_rect.clicked.connect(lambda: self.add_mask("rect"))
        self.btn_ellipse.clicked.connect(lambda: self.add_mask("ellipse"))
        self.btn_poly.clicked.connect(lambda: self.canvas_action.emit("draw_polygon", None))
        self.btn_bezier.clicked.connect(lambda: self.canvas_action.emit("draw_bezier", None))
        self.mask_list = QListWidget()
        self.mask_list.setObjectName("maskList")
        self.mask_list.setFixedHeight(SPACE_2 * 9)
        self.mask_list.setToolTip(tr("从上往下按顺序叠加。选中一个，下面是它的参数，画布上出现手柄"))
        self.mask_list.currentRowChanged.connect(self._on_mask_row)
        self.mask_list.itemChanged.connect(self._on_mask_item_changed)
        lay.addWidget(self.mask_list)
        self.mask_hint = QLabel(tr("还没有遮罩。遮罩跟着这一层走：移动、缩放、旋转它，遮罩一起动。"))
        self.mask_hint.setObjectName("hint")
        self.mask_hint.setWordWrap(True)
        lay.addWidget(self.mask_hint)
        form = InspectForm()
        self.mask_mode = ValueCombo((("add", tr("相加")), ("subtract", tr("相减")), ("intersect", tr("相交"))))
        self.mask_mode.valueChanged.connect(lambda v: self._mask_patch({"mode": v}))
        form.addRow(tr("模式"), self.mask_mode)
        self.mask_feather = SliderSpin(0, 400, 1, 0)
        self.mask_feather.setToolTip(tr("边缘柔化（画布像素）"))
        self.mask_feather.valueChanged.connect(lambda v: self._mask_patch({"feather": float(v)}, coalesce="feather"))
        add_inspect_row(form, tr("羽化"), self.mask_feather)
        self.mask_expand = SliderSpin(-200, 200, 1, 0)
        self.mask_expand.setToolTip(tr("向外扩（正）/ 向内收（负），画布像素"))
        self.mask_expand.valueChanged.connect(lambda v: self._mask_patch({"expand": float(v)}, coalesce="expand"))
        add_inspect_row(form, tr("扩展"), self.mask_expand)
        self.mask_opacity = SliderSpin(0, 100, 1, 0)
        self.mask_opacity.valueChanged.connect(
            lambda v: self._mask_patch({"opacity": float(v) / 100.0}, coalesce="opacity"))
        add_inspect_row(form, tr("不透明度"), self.mask_opacity)
        self.mask_radius = SliderSpin(0, 50, 1, 0)
        self.mask_radius.setToolTip(tr("矩形圆角，占短边的百分比"))
        self.mask_radius.valueChanged.connect(lambda v: self._mask_patch({"radius": float(v) / 100.0}, coalesce="radius"))
        add_inspect_row(form, tr("圆角"), self.mask_radius)
        self.mask_rotation = SliderSpin(-180, 180, 1, 0)
        self.mask_rotation.valueChanged.connect(lambda v: self._mask_patch({"rotation": float(v)}, coalesce="rot"))
        add_inspect_row(form, tr("旋转"), self.mask_rotation)
        flags = QHBoxLayout()
        flags.setSpacing(SPACE_2)
        self.mask_invert = QCheckBox(tr("反转"))
        self.mask_invert.toggled.connect(lambda on: self._mask_patch({"invert": bool(on)}))
        flags.addWidget(self.mask_invert)
        flags.addStretch(1)
        self.btn_mask_del = QToolButton()
        bind_icon(self.btn_mask_del, "trash-2", ICON_SM, "fg1")
        self.btn_mask_del.setToolTip(tr("删除这个遮罩"))
        self.btn_mask_del.setAutoRaise(True)
        self.btn_mask_del.clicked.connect(self.delete_mask)
        flags.addWidget(self.btn_mask_del)
        form.addRow(tr(""), flags)
        self.mask_form_host = QWidget()
        self.mask_form_host.setLayout(form)
        lay.addWidget(self.mask_form_host)
        sec.addWidget(host)
        return sec

    def _build_keyer(self):
        sec = CollapsibleSection(tr("抠像（蓝绿幕）"))
        sec.setExpanded(False)
        host = QWidget()
        lay = QVBoxLayout(host)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(SPACE_1)
        top = QHBoxLayout()
        top.setSpacing(SPACE_1)
        self.key_enable = QCheckBox(tr("抠像生效"))
        self.key_enable.toggled.connect(lambda on: self._key_patch({"enabled": bool(on)}))
        top.addWidget(self.key_enable)
        top.addStretch(1)
        self.key_preset = ValueCombo((("", tr("预设…")),) + tuple(K.PRESET_LABELS))
        self.key_preset.setToolTip(tr("一键填好屏幕色和参数；手机绿幕 = 压缩块多，先糊一点再抠"))
        self.key_preset.valueChanged.connect(self._on_key_preset)
        top.addWidget(self.key_preset)
        lay.addLayout(top)
        form = InspectForm()
        crow = QHBoxLayout()
        crow.setSpacing(SPACE_1)
        self.key_color = ColorSwatch(K.DEFAULTS["color"])
        self.key_color.changed.connect(lambda: self._key_patch({"color": self.key_color.hex()}))
        crow.addWidget(self.key_color, 1)
        self.btn_pick = QToolButton()
        self.btn_pick.setText(tr("吸管"))
        self.btn_pick.setToolTip(tr("到画布上点一下屏幕色（取周围一小块的平均）"))
        self.btn_pick.setCheckable(True)
        self.btn_pick.toggled.connect(lambda on: self.canvas_action.emit("pick_color", bool(on)))
        crow.addWidget(self.btn_pick)
        form.addRow(tr("屏幕色"), crow)
        self.key_gain = SliderSpin(0.1, 4.0, 0.05, 2)
        self.key_gain.setToolTip(tr("越大抠得越狠；边缘发虚就调小一点"))
        self.key_gain.valueChanged.connect(lambda v: self._key_patch({"gain": float(v)}, coalesce="gain"))
        add_inspect_row(form, tr("Key 增益"), self.key_gain)
        self.key_balance = SliderSpin(0, 100, 1, 0)
        self.key_balance.setToolTip(tr("50 中性。往左更保留偏青的边缘，往右更保留偏品红的边缘"))
        self.key_balance.valueChanged.connect(lambda v: self._key_patch({"balance": float(v) / 100.0}, coalesce="bal"))
        add_inspect_row(form, tr("平衡"), self.key_balance)
        # Q165：蒙版阈值（= 蒙版对比度）放到主区 —— 抠完背景还发灰、半透明就把「透明阈值」往右拉
        self.key_black = SliderSpin(0, 100, 1, 0)
        self.key_black.setToolTip(tr("透明阈值（黑裁切）：蒙版里低于它的全部变成完全透明。\n"
                                     "背景抠完还隐约看得见 / 发灰 → 往右拉"))
        self.key_black.valueChanged.connect(lambda v: self._key_patch({"black": float(v) / 100.0}, coalesce="bk"))
        add_inspect_row(form, tr("透明阈值"), self.key_black)
        self.key_white = SliderSpin(0, 100, 1, 0)
        self.key_white.setToolTip(tr("不透明阈值（白裁切）：蒙版里高于它的全部变成完全不透明。\n"
                                     "人物身上有半透明的洞 → 往左拉。两个阈值靠得越近，蒙版对比度越高、边越硬"))
        self.key_white.valueChanged.connect(lambda v: self._key_patch({"white": float(v) / 100.0}, coalesce="wt"))
        add_inspect_row(form, tr("不透明阈值"), self.key_white)
        self.key_blur = SliderSpin(0, 30, 1, 0)
        self.key_blur.setToolTip(tr("Matte 边缘柔化（像素）"))
        self.key_blur.valueChanged.connect(lambda v: self._key_patch({"blur": float(v)}, coalesce="blur"))
        add_inspect_row(form, tr("柔化"), self.key_blur)
        self.key_view = ValueCombo((("comp", tr("合成")), ("matte", tr("Matte（黑白）")),
                                    ("source", tr("原图")), ("spill", tr("只看溢色抑制"))))
        self.key_view.setToolTip(tr("Matte 视图里白 = 留下、黑 = 抠掉；调好记得换回「合成」"))
        self.key_view.valueChanged.connect(lambda v: self._key_patch({"view": v}))
        form.addRow(tr("查看"), self.key_view)
        fw = QWidget()
        fw.setLayout(form)
        lay.addWidget(fw)
        # Q165：消除溢色单独一块，默认关（只抠像不动颜色）
        self.key_despill_on = QCheckBox(tr("消除溢色"))
        self.key_despill_on.setToolTip(tr("把前景边缘 / 头发 / 半透明处沾上的绿（蓝）色去掉。\n"
                                          "默认关：只抠像，画面颜色一点不改"))
        self.key_despill_on.toggled.connect(lambda on: self._key_patch({"despill_on": bool(on)}))
        lay.addWidget(self.key_despill_on)
        dform = InspectForm()
        self.key_despill = SliderSpin(0, 100, 1, 0)
        self.key_despill.setToolTip(tr("消除溢色的强度"))
        self.key_despill.valueChanged.connect(lambda v: self._key_patch({"despill": float(v) / 100.0}, coalesce="ds"))
        add_inspect_row(dform, tr("强度"), self.key_despill)
        self.key_despill_mode = ValueCombo(tuple((v, tr(t)) for v, t in K.DESPILL_MODES))
        self.key_despill_mode.setToolTip(tr("标准：只把超出的那一截屏幕色通道压下去，正常颜色基本不变\n"
                                            "强力：沿屏幕色整体减（老做法，绿衣服 / 植物也会被减灰）"))
        self.key_despill_mode.valueChanged.connect(lambda v: self._key_patch({"despill_mode": v}))
        dform.addRow(tr("方式"), self.key_despill_mode)
        self.key_despill_scope = ValueCombo(tuple((v, tr(t)) for v, t in K.DESPILL_SCOPES))
        self.key_despill_scope.setToolTip(tr("只处理边缘：实心前景内部的颜色不动（推荐）\n整个画面：所有像素都去溢色"))
        self.key_despill_scope.valueChanged.connect(lambda v: self._key_patch({"despill_scope": v}))
        dform.addRow(tr("范围"), self.key_despill_scope)
        self.key_despill_box = QWidget()
        self.key_despill_box.setLayout(dform)
        lay.addWidget(self.key_despill_box)
        adv = CollapsibleSection(tr("高级"))
        adv.setExpanded(False)
        af = InspectForm()
        self.key_gamma = SliderSpin(0.2, 5.0, 0.05, 2)
        self.key_gamma.valueChanged.connect(lambda v: self._key_patch({"gamma": float(v)}, coalesce="gm"))
        add_inspect_row(af, tr("Matte 伽马"), self.key_gamma)
        self.key_erode = SliderSpin(-20, 20, 1, 0)
        self.key_erode.setToolTip(tr("正 = 往里收掉边缘的绿边；负 = 往外扩"))
        self.key_erode.valueChanged.connect(lambda v: self._key_patch({"erode": float(v)}, coalesce="er"))
        add_inspect_row(af, tr("收缩 / 扩张"), self.key_erode)
        self.key_despot = SliderSpin(0, 10, 1, 0)
        self.key_despot.setToolTip(tr("去掉 Matte 里的小黑点 / 小白点"))
        self.key_despot.valueChanged.connect(lambda v: self._key_patch({"despot": float(v)}, coalesce="dp"))
        add_inspect_row(af, tr("去斑点"), self.key_despot)
        self.key_preblur = SliderSpin(0, 10, 1, 0)
        self.key_preblur.setToolTip(tr("算 Matte 之前先把画面糊一点（手机视频压缩块多时用）；画面本身不糊"))
        self.key_preblur.valueChanged.connect(lambda v: self._key_patch({"preblur": float(v)}, coalesce="pb"))
        add_inspect_row(af, tr("预模糊"), self.key_preblur)
        aw = QWidget()
        aw.setLayout(af)
        adv.addWidget(aw)
        lay.addWidget(adv)
        sec.addWidget(host)
        return sec

    # ─────────────────────────── 数据 ───────────────────────────

    def bind(self, getter, context=None):
        self._getter = getter
        if self.ai is not None:
            self.ai.bind(self._target, context)     # 选中 AI 抠像节点时改的是节点
        if self.track is not None:
            self.track.bind(getter, context)        # 跟踪永远挂在图层上

    def bind_context(self, context):
        """AI 抠像 / 跟踪要的额外上下文（源路径 / 源时间）。"""
        if self.ai is not None:
            self.ai.bind(self._target, context)
        if self.track is not None:
            self.track.bind(self._getter, context)

    def _layer_target(self):
        if not callable(self._getter):
            return None, None, None
        try:
            return self._getter()
        except Exception:
            _LOG.exception("取遮罩 / 抠像对象失败")
            return None, None, None

    def _target(self):
        """遮罩 / 抠像各节改的对象：没节点图 = 图层；有图没选节点 = 图层（kind="graph"，各节藏起来）；
        选了节点 = 节点的 params（kind="node:<类型>"，命令带 node_id）。"""
        host, params, kind = self._layer_target()
        if host is None or params is None or kind not in ("clip", "image"):
            return host, params, kind
        g = G.graph_of(host)
        if g is None:
            return host, params, kind
        if self._node_layer is not None and self._node_layer != _layer_key(params):
            self.node_id = ""                       # 换了图层：节点选择不跟过去
            self._node_layer = None
        n = G.node_by_id(g, self.node_id) if self.node_id else None
        if n is None:
            return host, params, "graph"
        return n["params"], dict(params, node_id=n["id"]), "node:" + n["type"]

    def reload(self):
        """检查器换了对象 / 命令执行完以后调用。"""
        layer, _lp, lkind = self._layer_target()
        host, params, kind = self._target()
        self._loading = True
        try:
            on = host is not None and params is not None
            g = G.graph_of(layer) if (layer is not None and lkind in ("clip", "image")) else None
            ntype = kind[5:] if isinstance(kind, str) and kind.startswith("node:") else None
            node = G.node_by_id(g, self.node_id) if (g is not None and ntype) else None
            self._reload_graph_card(layer if on else None, lkind, g, node)
            show_mask = on and (g is None or ntype == "mask")
            show_key = on and ((g is None and kind in ("clip", "image")) or ntype == "keyer")
            self.sec_mask.setVisible(show_mask)
            if self.sec_key is not None:
                self.sec_key.setVisible(show_key)
            if self.node_params is not None:
                self.node_params.load(ntype if on else None, host if on else {})
            if self.ai is not None:
                self.ai.reload()
            if self.track is not None:
                self.track.reload()
            if not on:
                return
            if show_mask:
                self._reload_masks(host)
            if show_key:
                self._reload_keyer(host)
            if show_mask or show_key:
                self._sync_headers(host, "image" if ntype == "keyer" else kind)
        finally:
            self._loading = False

    def _sync_headers(self, host, kind):
        """标题带上「在用」的提示，并把有内容的段自动展开（只展开，不替用户收起）。"""
        n = len([m for m in self._masks_raw(host) if (M.normalize(m) or {}).get("enabled", True)])
        self.sec_mask.setTitle(tr("遮罩") + ("  · %d" % n if n else ""))
        if n and not self.sec_mask.isExpanded():
            self.sec_mask.setExpanded(True)
        if self.sec_key is not None and kind in ("clip", "image"):
            on = K.is_active(host)
            self.sec_key.setTitle(tr("抠像（蓝绿幕）") + ("  · " + tr("开") if on else ""))
            if on and not self.sec_key.isExpanded():
                self.sec_key.setExpanded(True)

    def _masks_raw(self, host):
        raw = host.get("masks") if isinstance(host, dict) else getattr(host, "masks", None)
        return [m for m in (raw or []) if isinstance(m, dict)]

    def masks(self):
        host, _p, _k = self._target()
        return self._masks_raw(host) if host is not None else []

    def active_mask(self):
        for m in self.masks():
            if m.get("id") == self.active_mask_id:
                return M.normalize(m)
        return None

    def _reload_masks(self, host):
        masks = self._masks_raw(host)
        self.mask_list.blockSignals(True)
        try:
            self.mask_list.clear()
            for i, m in enumerate(masks):
                n = M.normalize(m) or {}
                label = n.get("name") or {"rect": tr("矩形"), "ellipse": tr("椭圆"), "polygon": tr("多边形"), "bezier": tr("贝塞尔")}.get(
                    n.get("type"), tr("遮罩"))
                mode = {"add": "+", "subtract": "−", "intersect": "∩"}.get(n.get("mode"), "+")
                it = QListWidgetItem("%s  %s" % (mode, label))
                it.setData(Qt.ItemDataRole.UserRole, str(m.get("id") or ""))
                it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEditable)
                it.setCheckState(Qt.CheckState.Checked if n.get("enabled", True) else Qt.CheckState.Unchecked)
                it.setToolTip(tr("勾 = 启用；双击名字改名"))
                self.mask_list.addItem(it)
            ids = [str(m.get("id") or "") for m in masks]
            if self.active_mask_id not in ids:
                self.active_mask_id = ids[-1] if ids else ""
            if self.active_mask_id:
                self.mask_list.setCurrentRow(ids.index(self.active_mask_id))
        finally:
            self.mask_list.blockSignals(False)
        self.mask_list.setVisible(bool(masks))
        self.mask_hint.setVisible(not masks)
        self._reload_mask_form()

    def _reload_mask_form(self):
        m = self.active_mask()
        self.mask_form_host.setVisible(m is not None)
        if m is None:
            return
        prev = self._loading
        self._loading = True
        try:
            self.mask_mode.setCurrentText(m["mode"])
            self.mask_feather.setValue(m["feather"])
            self.mask_expand.setValue(m["expand"])
            self.mask_opacity.setValue(m["opacity"] * 100.0)
            self.mask_radius.setValue(m["radius"] * 100.0)
            self.mask_rotation.setValue(m["rotation"])
            self.mask_invert.setChecked(bool(m["invert"]))
            is_rect = m["type"] == "rect"
            is_poly = m["type"] in M.PATH_TYPES
            for w in (self.mask_radius,):
                w.setEnabled(is_rect)
            self.mask_rotation.setEnabled(not is_poly)
        finally:
            self._loading = prev

    def _reload_keyer(self, host):
        k = K.spec(host)
        self.key_enable.setChecked(k["enabled"])
        self.key_color.set_hex(k["color"], emit=False)
        self.key_gain.setValue(k["gain"])
        self.key_balance.setValue(k["balance"] * 100.0)
        self.key_despill.setValue(k["despill"] * 100.0)
        self.key_despill_on.setChecked(k["despill_on"])
        self.key_despill_mode.setCurrentText(k["despill_mode"])
        self.key_despill_scope.setCurrentText(k["despill_scope"])
        self.key_blur.setValue(k["blur"])
        self.key_view.setCurrentText(k["view"])
        self.key_black.setValue(k["black"] * 100.0)
        self.key_white.setValue(k["white"] * 100.0)
        self.key_gamma.setValue(k["gamma"])
        self.key_erode.setValue(k["erode"])
        self.key_despot.setValue(k["despot"])
        self.key_preblur.setValue(k["preblur"])
        self.key_preset.setCurrentText("")
        for w in (self.key_color, self.btn_pick, self.key_gain, self.key_balance, self.key_despill,
                  self.key_blur, self.key_view, self.key_black, self.key_white, self.key_gamma,
                  self.key_erode, self.key_despot, self.key_preblur, self.key_despill_on):
            w.setEnabled(k["enabled"])
        self.key_despill_box.setEnabled(k["enabled"] and k["despill_on"])

    # ─────────────────────────── 命令 ───────────────────────────

    def _exec(self, name, extra: dict, *, coalesce: str | None = None, layer: bool = False) -> bool:
        """走命令总线。coalesce：同一个滑杆 400 ms 内的连续改动直接写对象，只记第一次的撤销。
        layer=True：命令对图层（fx.graph），不带 node_id。"""
        host, params, _kind = self._layer_target() if layer else self._target()
        if host is None or params is None:
            return False
        payload = dict(params)
        payload.update(extra)
        from cutora.core.commands import get_bus
        now = time.monotonic()
        # 键用定位器而不是对象 id：节点图每次落地都会规范化成新对象，id 会变
        key = (_layer_key(params), params.get("node_id"), name, coalesce) if coalesce else None
        if key is not None:
            last = self._coalesce.get(key)
            if last is not None and now - last < _COALESCE_SEC:
                try:
                    self._direct(host, name, extra)
                    self._coalesce[key] = now
                    win = self.window()
                    cap = getattr(win, "capture_undo_after", None)
                    if callable(cap):
                        cap()
                    self._after_change()
                    return True
                except Exception:
                    _LOG.exception("直写失败，改走命令")
        try:
            get_bus().execute(name, payload, source="ui")
        except Exception as exc:
            _LOG.exception("%s 失败", name)
            win = self.window()
            bar = getattr(win, "statusBar", None)
            if callable(bar):
                try:
                    bar().showMessage("%s：%s" % (name, exc), 4000)
                except Exception:
                    pass
            return False
        if key is not None:
            self._coalesce[key] = now
        self._after_change()
        return True

    def _direct(self, host, name, extra):
        """不经总线直接写（拖动中的中间值）。和命令体同一套规范化。"""
        if name == "fx.mask_update":
            masks = list(self._masks_raw(host))
            for i, m in enumerate(masks):
                if m.get("id") == extra["mask_id"]:
                    merged = dict(m)
                    merged.update(extra["patch"])
                    masks[i] = M.normalize(merged) or m
            self._write(host, "masks", masks)
        elif name == "fx.node":
            p = (extra.get("patch") or {}).get("params")
            if isinstance(p, dict) and isinstance(host, dict):
                host.update(p)
        elif name == "fx.keyer_set":
            cur = dict(host.get("keyer") if isinstance(host, dict) else getattr(host, "keyer", None) or {})
            cur.update(extra["patch"])
            self._write(host, "keyer", K.normalize(cur))

    @staticmethod
    def _write(host, key, value):
        if isinstance(host, dict):
            host[key] = value
        else:
            setattr(host, key, value)

    def _after_change(self):
        win = self.window()
        for name in ("video_canvas", "timeline"):
            w = getattr(win, name, None)
            if w is not None and hasattr(w, "update"):
                w.update()
        self.changed.emit()

    def _mask_patch(self, patch: dict, coalesce: str | None = None):
        if self._loading or not self.active_mask_id:
            return
        if self._exec("fx.mask_update", {"mask_id": self.active_mask_id, "patch": patch}, coalesce=coalesce):
            if "mode" in patch:
                self.reload()

    def _key_patch(self, patch: dict, coalesce: str | None = None):
        if self._loading:
            return
        if self._exec("fx.keyer_set", {"patch": patch}, coalesce=coalesce):
            if "enabled" in patch or "color" in patch:
                self.reload()
            elif "despill_on" in patch:
                self.key_despill_box.setEnabled(bool(patch["despill_on"]) and self.key_enable.isChecked())

    def _on_key_preset(self, key):
        if self._loading or not key:
            return
        patch = dict(K.PRESETS.get(key) or {})
        patch["enabled"] = True
        self._key_patch(patch)
        self.key_preset.blockSignals(True)
        self.key_preset.setCurrentText("")
        self.key_preset.blockSignals(False)

    def set_picked_color(self, hex_color: str):
        """画布吸管取到颜色以后调用。"""
        self.btn_pick.blockSignals(True)
        self.btn_pick.setChecked(False)
        self.btn_pick.blockSignals(False)
        self._key_patch({"color": str(hex_color), "enabled": True})

    def add_mask(self, kind: str, mask: dict | None = None) -> str:
        m = mask or M.default_mask(kind)
        if self._exec("fx.mask_add", {"mask": m}):
            self.active_mask_id = m["id"]
            self.reload()
            self.sec_mask.setExpanded(True)
            self.canvas_action.emit("select_mask", m["id"])
            return m["id"]
        return ""

    def update_mask(self, mask_id: str, patch: dict, *, coalesce: str | None = None) -> bool:
        """画布拖手柄用。"""
        ok = self._exec("fx.mask_update", {"mask_id": mask_id, "patch": patch}, coalesce=coalesce)
        if ok:
            self._reload_mask_form()
        return ok

    def delete_mask(self):
        if not self.active_mask_id:
            return
        if self._exec("fx.mask_delete", {"mask_id": self.active_mask_id}):
            self.active_mask_id = ""
            self.reload()
            self.canvas_action.emit("select_mask", "")

    def select_mask(self, mask_id: str):
        self.active_mask_id = str(mask_id or "")
        self.reload()
        self.canvas_action.emit("select_mask", self.active_mask_id)

    def _on_mask_row(self, row):
        if self._loading:
            return
        it = self.mask_list.item(row) if row >= 0 else None
        self.active_mask_id = str(it.data(Qt.ItemDataRole.UserRole) or "") if it is not None else ""
        self._reload_mask_form()
        self.canvas_action.emit("select_mask", self.active_mask_id)

    def _on_mask_item_changed(self, item):
        if self._loading or item is None:
            return
        mid = str(item.data(Qt.ItemDataRole.UserRole) or "")
        if not mid:
            return
        patch = {"enabled": item.checkState() == Qt.CheckState.Checked}
        text = item.text()
        for prefix in ("+  ", "−  ", "∩  "):
            if text.startswith(prefix):
                text = text[len(prefix):]
        cur = next((m for m in self.masks() if m.get("id") == mid), None)
        default_label = {"rect": "矩形", "ellipse": "椭圆", "polygon": "多边形", "bezier": "贝塞尔"}.get((cur or {}).get("type"), "遮罩")
        if text.strip() and text.strip() != default_label and text.strip() != (cur or {}).get("name", ""):
            patch["name"] = text.strip()
        self._exec("fx.mask_update", {"mask_id": mid, "patch": patch})
        self.reload()
