# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q152-F · 节点编辑器（时间线下方的独立面板，和关键帧编辑器同一排 tab；视图 → 节点编辑器 / Alt+N）。

跟着检查器走：检查器里选的是视频片段或图片层，这里就显示它的效果节点图。所有改动都走命令总线
（fx.graph / fx.node / fx.link），一次手势一次撤销。

手势（Fusion / Nuke 习惯）：
* 从端口拖出线 → 松在另一个端口上连上；松在节点身上 → 接到第一个合适的端口；松在空白处 → 弹出
  「加节点」菜单，新节点直接接好。
* 从已经接了线的输入端口往外拖 = 把这根线拎起来：拖到别的端口改接，拖到空白处断开。
* 拖节点 = 移动（松手吸附到 8 px 网格）；空白处拖 = 框选；中键 / 空格拖 = 平移；滚轮 = 缩放。
* Delete 删除（上下游自动接上）· Ctrl+P 旁路 · Ctrl+T 交换两个输入 · F2 改名 · Ctrl+L 自动排布
* 1 = 在画面里只看选中节点的结果（查看器，只影响预览，不进导出）· Esc 关查看器
* Shift+空格 / Tab = 搜索加节点（插在选中节点后面）· F = 全部放进视野
"""
from __future__ import annotations

import copy
import json
import os

from PySide6.QtCore import QEvent, QPointF, Qt, QTimer, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMenu,
    QPushButton, QSizePolicy, QStackedWidget, QToolButton, QVBoxLayout, QWidget,
)

from cutora.core.log import get_logger
from cutora.fx import graph as G
from cutora.ui.i18n import tr
from cutora.ui.nodes.items import GRID, EdgeItem, NodeItem, WireItem
from cutora.ui.nodes.view import NodeScene, NodeView
from cutora.ui.theme.tokens import SPACE_1, SPACE_2, SPACE_4

_LOG = get_logger("cutora.ui.nodes.editor")

CATEGORIES = ("抠像", "遮罩", "画面")
IO_TITLES = {G.SRC: "输入 · 这一层", G.OUT: "输出 · 合成"}


# ───────────────────────────── 目标 ─────────────────────────────

class NodeTarget:
    """编辑器正在看的图层：host（视频片段 / 图片层 dict）、命令定位器、种类、检查器里那份 LayerFxPanel。"""

    def __init__(self, host, params, kind, fx_panel, title):
        self.host = host
        self.params = dict(params or {})
        self.kind = kind
        self.fx_panel = fx_panel
        self.title = title

    def graph(self):
        return G.graph_of(self.host)

    def key(self):
        """图层身份：定位器 + 图的 uid（撤销深拷贝后对象变了，身份不变）。"""
        g = self.graph()
        return (repr(sorted((str(k), repr(v)) for k, v in self.params.items() if k != "node_id")),
                g.get("uid") if g else None)


def resolve_target(window):
    """→ (NodeTarget 或 None, 空状态说明)。"""
    panel = getattr(window, "prop_panel", None)
    if panel is None:
        return None, tr("在时间线或画布上选中一个视频片段或图片层")
    ctype = getattr(panel, "current_type", None)
    try:
        if ctype == "video_clip":
            host, params, kind = panel._fx_target("clip")
        elif ctype == "overlay":
            host, params, kind = panel._fx_target("overlay")
        else:
            return None, tr("在时间线或画布上选中一个视频片段或图片层")
    except Exception:
        _LOG.exception("取节点图对象失败")
        return None, tr("在时间线或画布上选中一个视频片段或图片层")
    if host is None or not params:
        return None, tr("在时间线或画布上选中一个视频片段或图片层")
    if kind == "text":
        return None, tr("文字层不用节点图：遮罩直接在检查器里加")
    fxp = panel.fx_panel() if callable(getattr(panel, "fx_panel", None)) else None
    if kind == "clip":
        name = ""
        try:
            win_tasks = getattr(window, "tasks", None) or []
            ci = getattr(window, "curr_idx", -1)
            task = win_tasks[ci] if 0 <= ci < len(win_tasks) else None
            tl = task.get("timeline") if isinstance(task, dict) else None
            src = (getattr(tl, "sources", None) or {}).get(getattr(host, "source_id", None))
            name = os.path.basename(str(getattr(src, "path", "") or ""))
        except Exception:
            name = ""
        title = tr("视频 · %s") % (name or tr("片段"))
    else:
        title = tr("图片 · %s") % (os.path.basename(str(host.get("path") or "")) or tr("图片"))
    return NodeTarget(host, params, kind, fxp, title), ""


def allowed_types(kind) -> list:
    """这一层能加的节点类型（AI 抠像要源视频，只给视频片段）。"""
    out = []
    for t in G.NODE_TYPES:
        if t == "ai_matte" and kind != "clip":
            continue
        out.append(t)
    return out


def first_port_for(g, a, b):
    """a 的输出接到 b 时用哪个端口：先找空着的，再找能替换的。"""
    tb = G.type_of(g, b)
    ports = [p[0] for p in G.inputs_of(tb)]
    free = [p for p in ports if G.upstream(g, b, p) is None and G.can_connect(g, a, b, p)]
    if free:
        return free[0]
    for p in ports:
        if G.can_connect(g, a, b, p):
            return p
    return None


def _exec_menu(menu, pos):
    """弹菜单（测试里换掉，不阻塞）。"""
    return menu.exec(pos)


# ───────────────────────────── 搜索加节点 ─────────────────────────────

class NodeSearchPopup(QFrame):
    """Shift+空格：输入几个字筛节点类型，回车加。"""

    chosen = Signal(str)

    def __init__(self, types, parent=None):
        super().__init__(parent, Qt.WindowType.Popup)
        self.setObjectName("nodeSearchPopup")
        self.types = list(types)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(SPACE_1, SPACE_1, SPACE_1, SPACE_1)
        lay.setSpacing(SPACE_1)
        self.edit = QLineEdit()
        self.edit.setPlaceholderText(tr("搜索节点：抠像、遮罩、模糊…"))
        self.edit.textChanged.connect(self._filter)
        self.edit.returnPressed.connect(self._accept)
        self.edit.installEventFilter(self)
        self.list = QListWidget()
        self.list.itemActivated.connect(lambda _it: self._accept())
        self.list.itemClicked.connect(lambda _it: self._accept())
        lay.addWidget(self.edit)
        lay.addWidget(self.list)
        self.setMinimumWidth(SPACE_4 * 16)
        self._filter("")

    def _filter(self, text):
        q = str(text or "").strip().lower()
        self.list.clear()
        for t in self.types:
            nt = G.NODE_TYPES[t]
            hay = ("%s %s %s %s" % (nt["title"], nt["category"], t, nt["doc"])).lower()
            if q and q not in hay:
                continue
            it = QListWidgetItem("%s  ·  %s" % (nt["title"], nt["category"]))
            it.setData(Qt.ItemDataRole.UserRole, t)
            it.setToolTip(nt["doc"])
            self.list.addItem(it)
        if self.list.count():
            self.list.setCurrentRow(0)

    def eventFilter(self, obj, ev):
        if obj is self.edit and ev.type() == QEvent.Type.KeyPress:
            if ev.key() in (Qt.Key.Key_Down, Qt.Key.Key_Up):
                r = self.list.currentRow() + (1 if ev.key() == Qt.Key.Key_Down else -1)
                if 0 <= r < self.list.count():
                    self.list.setCurrentRow(r)
                return True
            if ev.key() == Qt.Key.Key_Escape:
                self.close()
                return True
        return super().eventFilter(obj, ev)

    def _accept(self):
        it = self.list.currentItem()
        if it is None:
            return
        t = str(it.data(Qt.ItemDataRole.UserRole))
        self.close()
        self.chosen.emit(t)


# ───────────────────────────── 画布（手势） ─────────────────────────────

class GraphCanvas(NodeView):
    def __init__(self, scene, editor):
        super().__init__(scene)
        self.ed = editor
        self._press_pos = None

    def _node_at(self, pt):
        for it in self.scene().items(pt):
            if isinstance(it, NodeItem):
                return it
        return None

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and not self._space:
            pt = self.mapToScene(e.position().toPoint())
            hit = self.ed.port_hit(pt)
            if hit is not None:
                self.ed.begin_wire(hit, pt)
                e.accept()
                return
            self.ed._dragging = True
            self._press_pos = {it.nid: QPointF(it.pos()) for it in self.ed.node_items.values()}
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self.ed.wire is not None:
            self.ed.update_wire(self.mapToScene(e.position().toPoint()))
            e.accept()
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if self.ed.wire is not None and e.button() == Qt.MouseButton.LeftButton:
            pt = self.mapToScene(e.position().toPoint())
            self.ed.end_wire(pt, self.mapToGlobal(e.position().toPoint()))
            e.accept()
            return
        super().mouseReleaseEvent(e)
        if e.button() == Qt.MouseButton.LeftButton and self._press_pos is not None:
            before, self._press_pos = self._press_pos, None
            self.ed._dragging = False
            self.ed.commit_moves(before)

    def mouseDoubleClickEvent(self, e):
        pt = self.mapToScene(e.position().toPoint())
        n = self._node_at(pt)
        if n is not None:
            self.ed.edit_in_inspector(n.nid)
        else:
            self.ed.open_search(pt, self.mapToGlobal(e.position().toPoint()))
        e.accept()

    def contextMenuEvent(self, e):
        pt = self.mapToScene(e.pos())
        self.ed.context_menu(pt, e.globalPos())
        e.accept()

    def keyPressEvent(self, e):
        if self.ed.handle_key(e):
            e.accept()
            return
        super().keyPressEvent(e)

    def event(self, ev):
        if ev.type() == QEvent.Type.ShortcutOverride and self.ed.wants_key(ev):
            ev.accept()
            return True
        return super().event(ev)


# ───────────────────────────── 编辑器 ─────────────────────────────

class NodeEditor(QWidget):
    node_selected = Signal(str)          # "" = 没选节点 / 选了多个

    def __init__(self, window, parent=None):
        super().__init__(parent)
        self.setObjectName("nodeEditor")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.win = window
        self.target: NodeTarget | None = None
        self.node_items: dict = {}
        self.edge_items: list = []
        self.wire = None                  # {"item", "from": (nid, kind) | None, "to": (nid, port) | None, "lift"}
        self._sig = None
        self._dragging = False
        self._syncing = False
        self._fitted_for = None
        self._last_menu = None

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        bar = QHBoxLayout()
        bar.setContentsMargins(SPACE_2, SPACE_1, SPACE_2, SPACE_1)
        bar.setSpacing(SPACE_1)
        self.title = QLabel(tr("节点"))
        self.title.setObjectName("sectionHeader")
        bar.addWidget(self.title)
        bar.addSpacing(SPACE_2)
        self.btn_add = self._tool(tr("＋节点"), tr("加一个节点，插在选中节点后面（Shift+空格 搜索）"))
        self.btn_add.clicked.connect(self._on_add_button)
        self.btn_layout = self._tool(tr("自动排布"), tr("从左到右按处理顺序排好（Ctrl+L）"))
        self.btn_layout.clicked.connect(self.auto_layout)
        self.btn_fit = self._tool(tr("适应"), tr("所有节点放进视野（F）"))
        self.btn_fit.clicked.connect(self.fit_view)
        for b in (self.btn_add, self.btn_layout, self.btn_fit):
            bar.addWidget(b)
        bar.addSpacing(SPACE_2)
        self.viewer_label = QLabel("")
        self.viewer_label.setObjectName("hintLabel")
        bar.addWidget(self.viewer_label)
        self.btn_viewer_off = self._tool(tr("关查看器"), tr("画面恢复成完整结果（Esc）"))
        self.btn_viewer_off.clicked.connect(lambda: self.set_viewer(None))
        bar.addWidget(self.btn_viewer_off)
        bar.addStretch(1)
        self.hint = QLabel(tr("拖端口连线 · Delete 删除 · Ctrl+P 旁路 · 1 查看器"))
        self.hint.setObjectName("hintLabel")
        bar.addWidget(self.hint)
        root.addLayout(bar)

        self.stack = QStackedWidget()
        empty = QWidget()
        el = QVBoxLayout(empty)
        el.setContentsMargins(SPACE_4, SPACE_4, SPACE_4, SPACE_4)
        el.setSpacing(SPACE_2)
        el.addStretch(1)
        self.empty_label = QLabel("")
        self.empty_label.setWordWrap(True)
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        el.addWidget(self.empty_label)
        self.btn_convert = QPushButton(tr("转为节点"))
        self.btn_convert.setToolTip(tr("把这一层现有的蓝绿幕 / AI 抠像 / 遮罩搬进节点图，画面一模一样；可撤销"))
        self.btn_convert.clicked.connect(self.convert)
        el.addWidget(self.btn_convert, 0, Qt.AlignmentFlag.AlignHCenter)
        el.addStretch(1)
        self.stack.addWidget(empty)
        self.scene = NodeScene(self)
        self.view = GraphCanvas(self.scene, self)
        self.stack.addWidget(self.view)
        root.addWidget(self.stack, 1)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.scene.selectionChanged.connect(self._on_selection)

        self._poll = QTimer(self)
        self._poll.setInterval(250)
        self._poll.timeout.connect(self.refresh)      # 只在显示时轮询（showEvent 开、hideEvent 停）
        self.refresh(force=True)

    def _tool(self, text, tip):
        b = QToolButton()
        b.setText(text)
        b.setToolTip(tip)
        b.setAutoRaise(True)
        b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        return b

    # ── 数据 ──
    def graph(self):
        return self.target.graph() if self.target is not None else None

    def viewer(self):
        return G.viewer_for(self.target.host) if self.target is not None else None

    def selected_ids(self) -> list:
        return [it.nid for it in self.scene.selectedItems() if isinstance(it, NodeItem)
                and it.nid not in (G.SRC, G.OUT)]

    def selected_edges(self) -> list:
        return [it for it in self.scene.selectedItems() if isinstance(it, EdgeItem)]

    # ── 跟着检查器走 ──
    def refresh(self, force: bool = False):
        if self._dragging or self.wire is not None:
            return
        if not force and not self.isVisible():
            return
        tgt, why = resolve_target(self.win)
        g = tgt.graph() if tgt is not None else None
        sig = None
        if tgt is not None:
            sig = (tgt.key(), id(tgt.host), tgt.kind, json.dumps(g, sort_keys=True, default=str) if g else None,
                   G.viewer_for(tgt.host))
        if not force and sig == self._sig and (tgt is None) == (self.target is None):
            self._sync_selection_from_inspector()
            return
        changed_host = tgt is None or self.target is None or tgt.key() != self.target.key()
        if changed_host and self.target is not None:
            G.clear_viewers()              # 换了图层：查看器不跟过去
        self.target = tgt
        self._sig = sig
        self.title.setText(tr("节点 · %s") % tgt.title if tgt is not None else tr("节点"))
        has_graph = g is not None
        for b in (self.btn_add, self.btn_layout, self.btn_fit):
            b.setEnabled(has_graph)
        if not has_graph:
            self.stack.setCurrentIndex(0)
            self.empty_label.setText(why if tgt is None else tr(
                "这一层还没有节点图。「转为节点」会把现有的蓝绿幕 / AI 抠像 / 遮罩搬进来，画面一模一样；"
                "之后可以自由串联、合并遮罩、只在区域里调色或模糊。"))
            self.btn_convert.setVisible(tgt is not None)
            self._clear_scene()
            self._sync_viewer_label()
            return
        self.stack.setCurrentIndex(1)
        keep = self.selected_ids() if not changed_host else []
        self._build(g)
        if changed_host or self._fitted_for != tgt.key():
            self._fitted_for = tgt.key()
            QTimer.singleShot(0, self.fit_view)
        insp = self._inspector_node()
        want = keep if len(keep) > 1 else ([insp] if insp else [])     # 单选跟检查器走（「回到图层」= 取消选中）
        self._select(want)
        self._sync_viewer_label()

    def _clear_scene(self):
        self._syncing = True
        try:
            self.scene.clear()
        finally:
            self._syncing = False
        self.node_items = {}
        self.edge_items = []

    def _build(self, g):
        self._clear_scene()
        view = G.viewer_for(self.target.host)
        for nid in (G.SRC, G.OUT):
            it = NodeItem(nid, nid, IO_TITLES[nid], viewer=(view == nid))
            x, y = g["io_pos"].get(nid, [0.0, 0.0])
            it.setPos(float(x), float(y))
            self.scene.addItem(it)
            self.node_items[nid] = it
        for n in g["nodes"]:
            nt = G.node_type(n["type"]) or {}
            it = NodeItem(n["id"], n["type"], nt.get("title", n["type"]), label=n.get("label", ""),
                          color=n.get("color", ""), enabled=n.get("enabled", True), viewer=(view == n["id"]))
            it.setToolTip("%s（%s）\n%s" % (nt.get("title", ""), n["id"], nt.get("doc", "")))
            it.setPos(float(n["pos"][0]), float(n["pos"][1]))
            self.scene.addItem(it)
            self.node_items[n["id"]] = it
        for a, b, port in g["edges"]:
            if a in self.node_items and b in self.node_items:
                e = EdgeItem(self.node_items[a], self.node_items[b], port)
                self.scene.addItem(e)
                self.edge_items.append(e)

    def _select(self, ids):
        self._syncing = True
        try:
            self.scene.clearSelection()
            for nid in ids:
                it = self.node_items.get(nid)
                if it is not None:
                    it.setSelected(True)
        finally:
            self._syncing = False

    def _inspector_node(self) -> str:
        fxp = self.target.fx_panel if self.target is not None else None
        nid = str(getattr(fxp, "node_id", "") or "")
        return nid if nid in self.node_items else ""

    def _sync_selection_from_inspector(self):
        """检查器里点了「回到图层」→ 这里也取消选中（反过来由 _on_selection 推过去）。"""
        if self.target is None or self.graph() is None:
            return
        want = self._inspector_node()
        sel = self.selected_ids()
        if not want and len(sel) == 1:
            self._select([])
        elif want and sel != [want] and len(sel) <= 1:
            self._select([want])

    def _on_selection(self):
        if self._syncing or self.target is None:
            return
        sel = self.selected_ids()
        nid = sel[0] if len(sel) == 1 else ""
        fxp = self.target.fx_panel
        if fxp is not None and hasattr(fxp, "set_node") and str(getattr(fxp, "node_id", "") or "") != nid:
            if nid or not sel:
                fxp.set_node(nid)
        self.node_selected.emit(nid)

    def _sync_viewer_label(self):
        v = self.viewer()
        if v and self.target is not None:
            g = self.graph()
            name = IO_TITLES.get(v) or ""
            if not name and g is not None:
                n = G.node_by_id(g, v)
                if n is not None:
                    name = n.get("label") or (G.node_type(n["type"]) or {}).get("title", v)
            self.viewer_label.setText(tr("查看器：%s") % name)
        else:
            self.viewer_label.setText("")
        self.viewer_label.setVisible(bool(v))
        self.btn_viewer_off.setVisible(bool(v))

    def fit_view(self):
        if self.node_items:
            self.view.fit_items()

    def showEvent(self, e):
        super().showEvent(e)
        self._poll.start()
        self.refresh(force=True)

    def hideEvent(self, e):
        super().hideEvent(e)
        self._poll.stop()
        if G._VIEWER:
            G.clear_viewers()
            self._present()

    # ── 命令 ──
    def _bus(self, name, extra) -> dict | None:
        if self.target is None:
            return None
        payload = dict(self.target.params)
        payload.pop("node_id", None)
        payload.update(extra)
        from cutora.core.commands import get_bus
        try:
            res = get_bus().execute(name, payload, source="ui")
        except Exception as exc:
            _LOG.info("%s 没成：%s", name, exc)
            self._toast(tr("没改成：%s") % exc)
            return None
        self._after()
        return res if isinstance(res, dict) else {}

    def _replace(self, g) -> bool:
        return self._bus("fx.graph", {"op": "replace", "graph": g}) is not None

    def _work_copy(self):
        g = self.graph()
        return copy.deepcopy(g) if g is not None else None

    def _after(self):
        self._sig = None
        self.refresh(force=True)
        fxp = self.target.fx_panel if self.target is not None else None
        if fxp is not None and hasattr(fxp, "reload"):
            try:
                fxp.reload()
            except Exception:
                _LOG.exception("刷新检查器失败")
        self._present()

    def _present(self):
        try:
            from cutora.fx.layer_fx import clear_cache
            clear_cache()
        except Exception:
            pass
        fn = getattr(self.win, "_present_preview_frame", None)
        if callable(fn):
            try:
                fn()
            except Exception:
                _LOG.exception("刷新预览失败")
        for name in ("video_canvas", "timeline"):
            w = getattr(self.win, name, None)
            if w is not None and hasattr(w, "update"):
                w.update()

    def _toast(self, msg):
        bar = getattr(self.win, "statusBar", None)
        if callable(bar):
            try:
                bar().showMessage(msg, 3000)
            except Exception:
                pass

    # ── 一步操作 ──
    def convert(self):
        if self.target is None:
            return False
        ok = self._bus("fx.graph", {"op": "create"}) is not None
        if ok:
            self._toast(tr("已转为节点图：画面不变，可以撤销"))
        return ok

    def delete_graph(self):
        if self.graph() is None:
            return False
        G.set_viewer(self.graph().get("uid"), None)
        fxp = self.target.fx_panel
        if fxp is not None and hasattr(fxp, "set_node"):
            fxp.set_node("")
        return self._bus("fx.graph", {"op": "delete"}) is not None

    def add_node(self, t, pos=None, *, after=None, link=None) -> str | None:
        """加节点。after = 插在这个节点后面；link = 拖线松在空白处：("from", 上游 id) 或 ("to", 下游 id, 端口)。"""
        g = self._work_copy()
        if g is None or t not in G.NODE_TYPES:
            return None
        if t not in allowed_types(self.target.kind):
            self._toast(tr("AI 抠像只能用在视频片段上"))
            return None
        if pos is None:
            pos = self._default_pos(after)
        pos = self._free_pos(pos[0], pos[1])
        n = G.add_node(g, t, [round(pos[0] / GRID) * GRID, round(pos[1] / GRID) * GRID])
        nid = n["id"]
        try:
            if after and after in self.node_items:
                G.insert_after(g, after, nid)
            elif link and link[0] == "from":
                port = first_port_for(g, link[1], nid)
                if port is not None:
                    G.connect(g, link[1], nid, port)
            elif link and link[0] == "to":
                if G.can_connect(g, nid, link[1], link[2]):
                    G.connect(g, nid, link[1], link[2])
        except ValueError as exc:
            _LOG.info("新节点没接上：%s", exc)
        if not self._replace(g):
            return None
        self._select([nid])
        self._on_selection()
        return nid

    def _free_pos(self, x, y):
        """别压在别的节点上：往下挪到空处。"""
        from PySide6.QtCore import QRectF
        from cutora.ui.nodes.items import NODE_W
        for _ in range(40):
            r = QRectF(x, y, NODE_W, 80.0)
            if not any(it.sceneBoundingRect().intersects(r) for it in self.node_items.values()):
                break
            y += 96.0
        return x, y

    def _default_pos(self, after):
        if after and after in self.node_items:
            p = self.node_items[after].pos()
            return (p.x() + 200.0, p.y() + 40.0)
        c = self.view.mapToScene(self.view.viewport().rect().center())
        return (c.x() - 80.0, c.y() - 30.0)

    def connect_nodes(self, a, b, port) -> bool:
        g = self.graph()
        if g is None or not G.can_connect(g, a, b, port):
            self._toast(tr("接不上：遮罩不能接到画面端口，也不能连成环"))
            return False
        return self._bus("fx.link", {"op": "connect", "from": a, "to": b, "port": port}) is not None

    def disconnect(self, b, port) -> bool:
        return self._bus("fx.link", {"op": "disconnect", "to": b, "port": port}) is not None

    def remove_nodes(self, ids=None) -> bool:
        ids = list(ids if ids is not None else self.selected_ids())
        edges = [] if ids else [e.key() for e in self.selected_edges()]
        g = self._work_copy()
        if g is None or (not ids and not edges):
            return False
        v = self.viewer()
        for nid in ids:
            if G.node_by_id(g, nid) is not None:
                G.remove_node(g, nid, reconnect=True)
        for _a, b, port in edges:
            G.disconnect(g, b, port)
        if v in ids:
            G.set_viewer(g.get("uid"), None)
        fxp = self.target.fx_panel
        if fxp is not None and getattr(fxp, "node_id", "") in ids and hasattr(fxp, "set_node"):
            fxp.set_node("")
        return self._replace(g)

    def toggle_bypass(self, ids=None) -> bool:
        ids = list(ids if ids is not None else self.selected_ids())
        g = self._work_copy()
        if g is None or not ids:
            return False
        nodes = [G.node_by_id(g, i) for i in ids]
        nodes = [n for n in nodes if n is not None]
        on = not all(not n.get("enabled", True) for n in nodes)    # 有一个开着 → 全部旁路
        for n in nodes:
            n["enabled"] = not on
        ok = self._replace(g)
        if ok:
            self._select(ids)
        return ok

    def swap_inputs(self, nid=None) -> bool:
        ids = [nid] if nid else self.selected_ids()
        if len(ids) != 1:
            return False
        return self._bus("fx.node", {"op": "swap", "id": ids[0]}) is not None

    def rename(self, nid=None, label=None) -> bool:
        ids = [nid] if nid else self.selected_ids()
        if len(ids) != 1 or self.graph() is None:
            return False
        n = G.node_by_id(self.graph(), ids[0])
        if n is None:
            return False
        if label is None:
            text, ok = QInputDialog.getText(self, tr("节点改名"), tr("名字（留空 = 用类型名）"),
                                            text=n.get("label", ""))
            if not ok:
                return False
            label = text
        ok = self._bus("fx.node", {"op": "set", "id": ids[0], "patch": {"label": str(label).strip()}}) is not None
        if ok:
            self._select(ids)
        return ok

    def set_color(self, nid, color) -> bool:
        return self._bus("fx.node", {"op": "set", "id": nid, "patch": {"color": color}}) is not None

    def auto_layout(self) -> bool:
        g = self._work_copy()
        if g is None:
            return False
        G.auto_layout(g)
        ok = self._replace(g)
        if ok:
            QTimer.singleShot(0, self.fit_view)
        return ok

    def insert_on_edge(self, edge_key, t) -> str | None:
        a, b, port = edge_key
        g = self._work_copy()
        if g is None:
            return None
        pa = self.node_items[a].pos() if a in self.node_items else QPointF(0, 0)
        pb = self.node_items[b].pos() if b in self.node_items else QPointF(200, 0)
        n = G.add_node(g, t, [round((pa.x() + pb.x()) / 2 / GRID) * GRID, round((pa.y() + pb.y()) / 2 / GRID) * GRID])
        p_in = first_port_for(g, a, n["id"])
        if p_in is None or not G.can_connect(g, n["id"], b, port):
            self._toast(tr("这个节点插不进这根线（类型不配）"))
            return None
        G.connect(g, a, n["id"], p_in)
        G.connect(g, n["id"], b, port)
        if not self._replace(g):
            return None
        self._select([n["id"]])
        self._on_selection()
        return n["id"]

    def commit_moves(self, before: dict):
        moved = {}
        for nid, it in self.node_items.items():
            p0 = before.get(nid)
            if p0 is None or (it.pos() - p0).manhattanLength() < 0.5:
                continue
            x = round(it.pos().x() / GRID) * GRID
            y = round(it.pos().y() / GRID) * GRID
            it.setPos(x, y)
            moved[nid] = [float(x), float(y)]
        if moved:
            self._bus("fx.node", {"op": "move", "positions": moved})
        return moved

    # ── 查看器 ──
    def set_viewer(self, nid):
        g = self.graph()
        if g is None:
            return
        cur = G.viewer_for(self.target.host)
        G.set_viewer(g.get("uid"), None if (not nid or nid == cur) else nid)
        self._sig = None
        self.refresh(force=True)
        self._present()
        v = G.viewer_for(self.target.host)
        if v:
            self._toast(tr("查看器：画面里只显示这个节点的结果（只影响预览，Esc 关）"))

    def edit_in_inspector(self, nid):
        if nid in (G.SRC, G.OUT):
            return
        self._select([nid])
        self._on_selection()
        dock = getattr(self.win, "dock_right", None)
        if dock is not None:
            dock.show()
            dock.raise_()

    # ── 拖线 ──
    def port_hit(self, pt):
        for it in self.scene.items(pt):
            if isinstance(it, NodeItem):
                hp = it.port_at(pt)
                if hp is not None:
                    return it, hp
        for it in self.node_items.values():
            hp = it.port_at(pt)
            if hp is not None:
                return it, hp
        return None

    def begin_wire(self, hit, pt):
        item, (side, port, kind) = hit
        g = self.graph()
        if g is None:
            return
        if side == "out":
            w = WireItem(item.out_port_pos(), kind, True)
            self.wire = {"item": w, "from": item.nid, "to": None, "lift": None}
        else:
            up = G.upstream(g, item.nid, port)
            if up is not None and up in self.node_items:      # 拎起已有的线
                src = self.node_items[up]
                w = WireItem(src.out_port_pos(), G.output_of(G.type_of(g, up)), True)
                self.wire = {"item": w, "from": up, "to": None, "lift": (item.nid, port)}
                for e in self.edge_items:
                    if e.key() == (up, item.nid, port):
                        e.setVisible(False)
            else:
                w = WireItem(item.in_port_pos(port), kind, False)
                self.wire = {"item": w, "from": None, "to": (item.nid, port), "lift": None}
        self.scene.addItem(self.wire["item"])
        self.wire["item"].update_to(pt)

    def update_wire(self, pt):
        if self.wire is not None:
            self.wire["item"].update_to(pt)

    def cancel_wire(self):
        if self.wire is None:
            return
        self.scene.removeItem(self.wire["item"])
        for e in self.edge_items:
            e.setVisible(True)
        self.wire = None

    def end_wire(self, pt, global_pos=None):
        w = self.wire
        if w is None:
            return
        self.scene.removeItem(w["item"])
        for e in self.edge_items:
            e.setVisible(True)
        self.wire = None
        g = self.graph()
        if g is None:
            return
        hit = self.port_hit(pt)
        node = None
        for it in self.scene.items(pt):
            if isinstance(it, NodeItem):
                node = it
                break
        if w["from"] is not None:
            a = w["from"]
            dst = None
            if hit is not None and hit[1][0] == "in":
                dst = (hit[0].nid, hit[1][1])
            elif node is not None and node.nid != a:
                p = first_port_for(g, a, node.nid)
                dst = (node.nid, p) if p else None
            if w["lift"] is not None:
                b0, p0 = w["lift"]
                if dst == (b0, p0):
                    return
                gg = copy.deepcopy(g)
                G.disconnect(gg, b0, p0)
                if dst is not None:
                    if not G.can_connect(gg, a, dst[0], dst[1]):
                        self._toast(tr("接不上：遮罩不能接到画面端口，也不能连成环"))
                        return
                    G.connect(gg, a, dst[0], dst[1])
                self._replace(gg)
                return
            if dst is not None:
                self.connect_nodes(a, dst[0], dst[1])
            elif node is None:
                self._menu_add_linked(pt, global_pos, ("from", a))
            return
        b, port = w["to"]
        src = None
        if hit is not None and hit[1][0] == "out":
            src = hit[0].nid
        elif node is not None and node.nid != b and node.out_kind is not None:
            src = node.nid
        if src is not None:
            self.connect_nodes(src, b, port)
        elif node is None:
            self._menu_add_linked(pt, global_pos, ("to", b, port))

    def _types_for_link(self, link) -> list:
        g = self.graph()
        out = []
        for t in allowed_types(self.target.kind):
            gg = copy.deepcopy(g)
            n = G.add_node(gg, t, [0, 0])
            if link[0] == "from":
                ok = first_port_for(gg, link[1], n["id"]) is not None
            else:
                ok = G.can_connect(gg, n["id"], link[1], link[2])
            if ok:
                out.append(t)
        return out

    def _menu_add_linked(self, pt, global_pos, link):
        types = self._types_for_link(link)
        if not types or global_pos is None:
            return
        menu = self._type_menu(types, lambda t: self.add_node(t, (pt.x(), pt.y() - 20.0), link=link))
        self._last_menu = menu
        _exec_menu(menu, global_pos)

    def _type_menu(self, types, on_pick, parent_menu=None):
        menu = parent_menu or QMenu(self)
        for cat in CATEGORIES:
            ts = [t for t in types if G.NODE_TYPES[t]["category"] == cat]
            if not ts:
                continue
            menu.addSection(cat)
            for t in ts:
                act = QAction(G.NODE_TYPES[t]["title"], menu)
                act.setToolTip(G.NODE_TYPES[t]["doc"])
                act.triggered.connect(lambda _c=False, tt=t: on_pick(tt))
                menu.addAction(act)
        menu.setToolTipsVisible(True)
        return menu

    # ── 菜单 / 搜索 ──
    def _on_add_button(self):
        if self.target is None or self.graph() is None:
            return
        sel = self.selected_ids()
        after = sel[0] if len(sel) == 1 else None
        menu = self._type_menu(allowed_types(self.target.kind), lambda t: self.add_node(t, after=after))
        self._last_menu = menu
        _exec_menu(menu, self.btn_add.mapToGlobal(self.btn_add.rect().bottomLeft()))

    def open_search(self, pt=None, global_pos=None):
        if self.target is None or self.graph() is None:
            return None
        sel = self.selected_ids()
        after = sel[0] if len(sel) == 1 else None
        pos = None if after else ((pt.x(), pt.y()) if pt is not None else None)
        pop = NodeSearchPopup(allowed_types(self.target.kind), self)
        pop.chosen.connect(lambda t: self.add_node(t, pos, after=after))
        from PySide6.QtGui import QCursor
        pop.move(global_pos if global_pos is not None else QCursor.pos())
        pop.show()
        pop.edit.setFocus()
        self._last_menu = pop
        return pop

    def context_menu(self, pt, global_pos):
        if self.target is None or self.graph() is None:
            return
        menu = QMenu(self)
        node = None
        edge = None
        for it in self.scene.items(pt):
            if isinstance(it, NodeItem):
                node = it
                break
            if isinstance(it, EdgeItem) and edge is None:
                edge = it
        if node is not None:
            if not node.isSelected():
                self._select([node.nid])
                self._on_selection()
            real = node.nid not in (G.SRC, G.OUT)
            menu.addAction(tr("查看器（只看这个结果）\t1"), lambda: self.set_viewer(node.nid)).setEnabled(
                node.nid != G.OUT)
            if real:
                menu.addAction(tr("在检查器里编辑\t双击"), lambda: self.edit_in_inspector(node.nid))
                menu.addSeparator()
                menu.addAction(tr("旁路（暂时跳过）\tCtrl+P"), lambda: self.toggle_bypass([node.nid]))
                ins = G.inputs_of(node.ntype)
                menu.addAction(tr("交换两个输入\tCtrl+T"), lambda: self.swap_inputs(node.nid)).setEnabled(
                    len(ins) >= 2 and ins[0][1] == ins[1][1])
                menu.addAction(tr("改名…\tF2"), lambda: self.rename(node.nid))
                cm = menu.addMenu(tr("颜色"))
                for c in G.NODE_COLORS:
                    cm.addAction(c or tr("默认"), lambda cc=c: self.set_color(node.nid, cc))
                menu.addSeparator()
                menu.addAction(tr("删除（上下游接上）\tDelete"), lambda: self.remove_nodes(self.selected_ids()))
        elif edge is not None:
            key = edge.key()
            menu.addAction(tr("断开"), lambda: self.disconnect(key[1], key[2]))
            sub = menu.addMenu(tr("在中间插入"))
            types = [t for t in allowed_types(self.target.kind)
                     if G.output_of(t) == G.output_of(G.type_of(self.graph(), key[0])) or edge.kind == G.MATTE]
            self._type_menu(types, lambda t: self.insert_on_edge(key, t), sub)
        else:
            sub = menu.addMenu(tr("添加节点"))
            self._type_menu(allowed_types(self.target.kind), lambda t: self.add_node(t, (pt.x(), pt.y())), sub)
            menu.addAction(tr("搜索添加…\tShift+空格"), lambda: self.open_search(pt, global_pos))
            menu.addSeparator()
            menu.addAction(tr("自动排布\tCtrl+L"), self.auto_layout)
            menu.addAction(tr("全部放进视野\tF"), self.fit_view)
            if self.viewer():
                menu.addAction(tr("关查看器\tEsc"), lambda: self.set_viewer(None))
            menu.addSeparator()
            menu.addAction(tr("删除节点图（回到直通）"), self.delete_graph)
        self._last_menu = menu
        _exec_menu(menu, global_pos)

    # ── 键盘 ──
    def wants_key(self, ev) -> bool:
        key = ev.key()
        mods = ev.modifiers() & ~Qt.KeyboardModifier.KeypadModifier
        ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
        if key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace, Qt.Key.Key_Escape, Qt.Key.Key_F2,
                   Qt.Key.Key_1, Qt.Key.Key_F, Qt.Key.Key_Tab):
            return True
        if key == Qt.Key.Key_Space and mods & Qt.KeyboardModifier.ShiftModifier:
            return True
        if key == Qt.Key.Key_Space:
            return True                     # 空格拖平移，不要触发全局播放
        if ctrl and key in (Qt.Key.Key_P, Qt.Key.Key_T, Qt.Key.Key_L, Qt.Key.Key_A):
            return True
        return False

    def handle_key(self, ev) -> bool:
        if self.target is None or self.graph() is None:
            return False
        key = ev.key()
        mods = ev.modifiers()
        ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
        shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)
        if key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.remove_nodes()
        elif ctrl and key == Qt.Key.Key_P:
            self.toggle_bypass()
        elif ctrl and key == Qt.Key.Key_T:
            self.swap_inputs()
        elif ctrl and key == Qt.Key.Key_L:
            self.auto_layout()
        elif ctrl and key == Qt.Key.Key_A:
            self._select(list(self.node_items))
            self._on_selection()
        elif key == Qt.Key.Key_F2:
            self.rename()
        elif key == Qt.Key.Key_1 and not ctrl:
            sel = [it.nid for it in self.scene.selectedItems() if isinstance(it, NodeItem)]
            if len(sel) == 1 and sel[0] != G.OUT:
                self.set_viewer(sel[0])
            else:
                self._toast(tr("先选一个节点，再按 1 在画面里只看它"))
        elif key == Qt.Key.Key_Escape:
            if self.wire is not None:
                self.cancel_wire()
            elif self.viewer():
                self.set_viewer(None)
            else:
                self.scene.clearSelection()
        elif key == Qt.Key.Key_F and not ctrl:
            self.fit_view()
        elif (key == Qt.Key.Key_Space and shift) or key == Qt.Key.Key_Tab:
            self.open_search()
        else:
            return False
        return True
