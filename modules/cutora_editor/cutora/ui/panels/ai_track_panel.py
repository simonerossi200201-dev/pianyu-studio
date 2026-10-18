# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q152-E · 检查器里的「AI 跟踪」一节（只挂在视频片段上）。

流程：「在画面上放点」→ 画布上点要跟的位置（点 = 放 / 挪当前选中的点，Alt+点 = 去掉这一帧上的点）
→ 「向前 / 向后 / 全部」跟踪（后台线程、进度、可取消、和 AI 字幕 / 抠像排队用显卡）
→ 时间线这条片段上出现蓝条（红 = 有点被挡 / 跟丢，点一下跳过去，在那附近再放一次点重跟）
→ 「应用到」：让文字 / 图片 / 另一条视频跟着动（跟随），或者让这条片段自己反着动（稳定），生成普通关键帧。

数据改动走命令总线（track.*，可撤销），跟踪走 MatteWorker(kind="track")，烘焙在 cutora/fx/track_bind.py。
对象跟踪不需要放点：直接用这条片段「AI 抠像」算好的 mask（质心 / 面积 / 主轴）。
"""
from __future__ import annotations

import os

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMessageBox, QProgressBar, QPushButton,
    QToolButton, QVBoxLayout, QWidget,
)

from cutora.core.log import get_logger
from cutora.fx import ai_matte as A
from cutora.fx import track_store as T
from cutora.fx import tracking as TR
from cutora.ui.i18n import tr
from cutora.ui.theme.tokens import SPACE_1, SPACE_2
from cutora.ui.widgets.collapsible import CollapsibleSection
from cutora.ui.widgets.inspector import InspectForm, SliderSpin, ValueCombo, add_inspect_row

_LOG = get_logger("cutora.ui.panels.ai_track_panel")


def _dot_icon(color: str, px: int = 12) -> QIcon:
    pm = QPixmap(px, px)
    pm.fill(Qt.GlobalColor.transparent)
    from PySide6.QtGui import QPainter
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setBrush(QColor(color))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawEllipse(1, 1, px - 2, px - 2)
    p.end()
    return QIcon(pm)


class AiTrackPanel(QWidget):
    """``bind(getter, context)``：getter() → (host, cmd_params, kind)；context() → {"path", "src_t", "tl", "task"}。"""

    canvas_action = Signal(str, object)         # ("track_place", on/off)
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._getter = None
        self._context = None
        self._loading = False
        self._worker = None
        self._targets = []                      # 目标下拉对应的 (label, binding_target, cmd_loc)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.sec = self._build()
        root.addWidget(self.sec)

    # ─────────────────────────── 构建 ───────────────────────────

    def _build(self):
        sec = CollapsibleSection(tr("AI 跟踪"))
        sec.setExpanded(False)
        host = QWidget()
        lay = QVBoxLayout(host)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(SPACE_1)

        st = QHBoxLayout()
        st.setSpacing(SPACE_1)
        self.lbl_status = QLabel("")
        self.lbl_status.setObjectName("fieldHint")
        self.lbl_status.setWordWrap(True)
        st.addWidget(self.lbl_status, 1)
        self.btn_download = QPushButton(tr("下载模型…"))
        self.btn_download.setToolTip(tr("先看许可和校验值，同意后从 huggingface.co 下载到本机；全程本地推理"))
        self.btn_download.clicked.connect(self._on_download)
        st.addWidget(self.btn_download)
        lay.addLayout(st)
        mrow = QHBoxLayout()
        mrow.setSpacing(SPACE_1)
        mrow.addWidget(QLabel(tr("模型")))
        self.model_combo = ValueCombo(tuple(self._model_items()))
        self.model_combo.setToolTip(tr("512 最准；256 快三四倍，素材简单时够用"))
        self.model_combo.valueChanged.connect(self._on_model_changed)
        mrow.addWidget(self.model_combo, 1)
        lay.addLayout(mrow)

        # 跟踪点
        prow = QHBoxLayout()
        prow.setSpacing(SPACE_1)
        prow.addWidget(QLabel(tr("跟踪点")))
        prow.addStretch(1)
        self.btn_pt_add = QToolButton()
        self.btn_pt_add.setText("+")
        self.btn_pt_add.setToolTip(tr("再加一个点（2 个以上能算旋转和缩放，最多 8 个）"))
        self.btn_pt_add.clicked.connect(self._on_point_add)
        self.btn_pt_del = QToolButton()
        self.btn_pt_del.setText("−")
        self.btn_pt_del.setToolTip(tr("删掉选中的点和它的跟踪结果"))
        self.btn_pt_del.clicked.connect(self._on_point_del)
        prow.addWidget(self.btn_pt_add)
        prow.addWidget(self.btn_pt_del)
        lay.addLayout(prow)
        self.pt_list = QListWidget()
        self.pt_list.setObjectName("trackPointList")
        self.pt_list.setFixedHeight(72)
        self.pt_list.itemChanged.connect(self._on_point_item_changed)
        self.pt_list.currentRowChanged.connect(lambda _r: self._sync_tool_state())
        lay.addWidget(self.pt_list)

        trow = QHBoxLayout()
        trow.setSpacing(SPACE_1)
        self.btn_place = QToolButton()
        self.btn_place.setText(tr("在画面上放点"))
        self.btn_place.setCheckable(True)
        self.btn_place.setToolTip(tr("点 = 把选中的点放在这里（这一帧）；Alt+点 = 去掉这一帧上的点；右键 / Esc 退出"))
        self.btn_place.toggled.connect(self._on_place_toggled)
        self.btn_unplace = QToolButton()
        self.btn_unplace.setText(tr("去掉这帧的点"))
        self.btn_unplace.clicked.connect(self._on_unplace)
        trow.addWidget(self.btn_place)
        trow.addWidget(self.btn_unplace)
        trow.addStretch(1)
        lay.addLayout(trow)
        self.lbl_hint = QLabel(tr("选纹理清楚、不会被挡的角点最稳；跟丢了到红段附近再放一次"))
        self.lbl_hint.setObjectName("fieldHint")
        self.lbl_hint.setWordWrap(True)
        lay.addWidget(self.lbl_hint)

        # 跟踪
        krow = QHBoxLayout()
        krow.setSpacing(SPACE_1)
        self.btn_back = QPushButton(tr("◀ 向后"))
        self.btn_back.setToolTip(tr("从放点的那帧往片段开头跟"))
        self.btn_back.clicked.connect(lambda: self._on_track("backward"))
        self.btn_fwd = QPushButton(tr("向前 ▶"))
        self.btn_fwd.setToolTip(tr("从放点的那帧往片段结尾跟"))
        self.btn_fwd.clicked.connect(lambda: self._on_track("forward"))
        self.btn_all = QPushButton(tr("⟷ 全部"))
        self.btn_all.setToolTip(tr("两头都跟"))
        self.btn_all.clicked.connect(lambda: self._on_track("all"))
        for b in (self.btn_back, self.btn_fwd, self.btn_all):
            krow.addWidget(b)
        lay.addLayout(krow)
        pr2 = QHBoxLayout()
        pr2.setSpacing(SPACE_1)
        self.prog = QProgressBar()
        self.prog.setRange(0, 1000)
        self.prog.setTextVisible(False)
        self.prog.setFixedHeight(SPACE_2 + 2)
        self.prog.hide()
        pr2.addWidget(self.prog, 1)
        self.btn_cancel = QToolButton()
        self.btn_cancel.setText(tr("取消"))
        self.btn_cancel.clicked.connect(self._on_cancel)
        self.btn_cancel.hide()
        pr2.addWidget(self.btn_cancel)
        lay.addLayout(pr2)
        self.lbl_info = QLabel("")
        self.lbl_info.setObjectName("fieldHint")
        self.lbl_info.setWordWrap(True)
        lay.addWidget(self.lbl_info)
        lrow = QHBoxLayout()
        lrow.setSpacing(SPACE_1)
        self.btn_lost_prev = QToolButton()
        self.btn_lost_prev.setText(tr("◀ 上一处跟丢"))
        self.btn_lost_prev.clicked.connect(lambda: self._jump_lost(-1))
        self.btn_lost_next = QToolButton()
        self.btn_lost_next.setText(tr("下一处跟丢 ▶"))
        self.btn_lost_next.clicked.connect(lambda: self._jump_lost(1))
        lrow.addWidget(self.btn_lost_prev)
        lrow.addWidget(self.btn_lost_next)
        lrow.addStretch(1)
        lay.addLayout(lrow)

        # 应用
        self.sec_apply = CollapsibleSection(tr("应用到"))
        self.sec_apply.setExpanded(True)
        ah = QWidget()
        al = QVBoxLayout(ah)
        al.setContentsMargins(0, 0, 0, 0)
        al.setSpacing(SPACE_1)
        form = InspectForm()
        self.src_combo = ValueCombo((("points", tr("点跟踪")), ("object", tr("对象跟踪（用 AI 抠像的结果）"))))
        self.src_combo.setToolTip(tr("对象跟踪不用放点：用这条片段「AI 抠像」算好的 mask 的位置 / 大小"))
        self.src_combo.valueChanged.connect(lambda _v: self._sync_tool_state())
        form.addRow(tr("运动来源"), self.src_combo)
        self.mode_combo = ValueCombo((("follow", tr("跟随：让别的东西跟着动")), ("stabilize", tr("稳定：这条片段自己反着动"))))
        self.mode_combo.valueChanged.connect(lambda _v: self._sync_tool_state())
        form.addRow(tr("方式"), self.mode_combo)
        self.target_combo = ValueCombo((("", tr("（没有可选的目标）")),))
        self.target_combo.setToolTip(tr("时间上和这条片段重叠的文字 / 图片 / 调整图层 / 其他视频"))
        form.addRow(tr("目标"), self.target_combo)
        crow = QHBoxLayout()
        crow.setSpacing(SPACE_2)
        self.chk_pos = QCheckBox(tr("位置"))
        self.chk_pos.setChecked(True)
        self.chk_rot = QCheckBox(tr("旋转"))
        self.chk_scale = QCheckBox(tr("缩放"))
        for c in (self.chk_pos, self.chk_rot, self.chk_scale):
            crow.addWidget(c)
        crow.addStretch(1)
        form.addRow(tr("跟着"), crow)
        self.sl_smooth = SliderSpin(0, 30, 1, 0)
        self.sl_smooth.setToolTip(tr("把抖动抹平（帧数）；0 = 原样"))
        add_inspect_row(form, tr("平滑"), self.sl_smooth)
        self.chk_fill = QCheckBox(tr("自动放大，不露黑边"))
        self.chk_fill.setChecked(True)
        self.chk_fill.setToolTip(tr("稳定以后画面边缘会空出来：整体放大到每一帧都铺满（最多 3 倍）"))
        form.addRow("", self.chk_fill)
        self.chk_simplify = QCheckBox(tr("精简关键帧"))
        self.chk_simplify.setChecked(True)
        self.chk_simplify.setToolTip(tr("误差小于 0.25 像素的中间帧不打关键帧，曲线编辑器里更好改"))
        form.addRow("", self.chk_simplify)
        fw = QWidget()
        fw.setLayout(form)
        al.addWidget(fw)
        brow = QHBoxLayout()
        brow.setSpacing(SPACE_1)
        self.btn_apply = QPushButton(tr("应用（生成关键帧）"))
        self.btn_apply.setToolTip(tr("以当前帧为基准：目标现在在哪，之后就保持这个相对位置跟着走"))
        self.btn_apply.clicked.connect(self._on_apply)
        self.btn_rebake = QToolButton()
        self.btn_rebake.setText(tr("更新全部绑定"))
        self.btn_rebake.setToolTip(tr("重新跟踪以后点这里，把之前应用过的目标全部按新结果重算"))
        self.btn_rebake.clicked.connect(self._on_rebake_all)
        brow.addWidget(self.btn_apply, 1)
        brow.addWidget(self.btn_rebake)
        al.addLayout(brow)
        self.bind_list = QListWidget()
        self.bind_list.setObjectName("trackBindingList")
        self.bind_list.setFixedHeight(56)
        al.addWidget(self.bind_list)
        urow = QHBoxLayout()
        urow.addStretch(1)
        self.btn_unbind = QToolButton()
        self.btn_unbind.setText(tr("删除绑定记录"))
        self.btn_unbind.setToolTip(tr("只删记录；已经生成的关键帧保留（在关键帧面板里删）"))
        self.btn_unbind.clicked.connect(self._on_unbind)
        urow.addWidget(self.btn_unbind)
        al.addLayout(urow)
        self.sec_apply.addWidget(ah)
        lay.addWidget(self.sec_apply)

        drow = QHBoxLayout()
        drow.addStretch(1)
        self.btn_clear = QToolButton()
        self.btn_clear.setText(tr("清除结果"))
        self.btn_clear.clicked.connect(self._on_clear)
        self.btn_detach = QToolButton()
        self.btn_detach.setText(tr("移除"))
        self.btn_detach.clicked.connect(self._on_detach)
        drow.addWidget(self.btn_clear)
        drow.addWidget(self.btn_detach)
        lay.addLayout(drow)
        sec.addWidget(host)
        return sec

    @staticmethod
    def _model_items():
        try:
            from cutora.asr.models import catalog
            rows = [r for r in catalog() if r.get("kind") == "track" and r.get("enabled", True)]
        except Exception:
            rows = []
        items = [(str(r["id"]), str(r.get("title_zh") or r["id"])) for r in rows]
        return items or [("track-tapnextpp-512", "TAPNext++ 512")]

    # ─────────────────────────── 数据 ───────────────────────────

    def bind(self, getter, context=None):
        self._getter = getter
        self._context = context

    def _target(self):
        if not callable(self._getter):
            return None, None, None
        try:
            return self._getter()
        except Exception:
            _LOG.exception("取跟踪对象失败")
            return None, None, None

    def _ctx(self) -> dict:
        if not callable(self._context):
            return {}
        try:
            return dict(self._context() or {})
        except Exception:
            _LOG.exception("取跟踪上下文失败")
            return {}

    def spec(self) -> dict:
        host, _p, _k = self._target()
        return TR.spec(host) if host is not None else TR.normalize(None)

    def current_point_id(self):
        it = self.pt_list.currentItem()
        return None if it is None else int(it.data(Qt.ItemDataRole.UserRole))

    def fps(self):
        sp = self.spec()
        data = T.load(sp["session"]) if sp["session"] else None
        if data and data.get("fps"):
            return float(data["fps"])
        path = self._ctx().get("path")
        if not path:
            return None
        from cutora.fx.matte_jobs import source_fps
        return source_fps(path)

    def current_frame(self):
        ctx = self._ctx()
        if not ctx.get("path"):
            return None
        fps = self.fps()
        if not fps:
            return None
        from cutora.fx.matte_store import frame_index
        return frame_index(float(ctx.get("src_t") or 0.0), fps)

    def frame_range(self):
        host, _p, _k = self._target()
        fps = self.fps()
        if host is None or not fps:
            return None
        from cutora.fx.matte_jobs import clip_source_frame_range
        return clip_source_frame_range(host, fps)

    def model_status(self) -> tuple:
        if TR.backend_name() == "fake":
            return "fake", tr("演示后端（模板匹配），不需要模型")
        mid = self.spec()["model"]
        try:
            from cutora.asr.models import get_model, verify_model
            row = get_model(mid)
            if row is None:
                return "unknown", tr("未登记的模型 %s") % mid
            st = verify_model(row)
        except Exception:
            _LOG.exception("查跟踪模型状态失败")
            return "unknown", tr("模型状态未知")
        try:
            from cutora.core.config import DEVICE
        except Exception:
            DEVICE = "cpu"
        dev = tr("显卡") if str(DEVICE).startswith("cuda") else tr("CPU（慢，适合短片段）")
        if st == "ready":
            return "ready", tr("模型就绪 · 本地推理 · %s") % dev
        if st == "checksum_failed":
            return "missing", tr("模型文件校验失败，请重新下载")
        return "missing", tr("模型未下载（约 %.1f GB）") % (float(row.get("size_mb") or 0) / 1024.0)

    def reload(self):
        host, params, kind = self._target()
        self._loading = True
        try:
            on = host is not None and params is not None and kind == "clip"
            self.sec.setVisible(on)
            if not on:
                return
            sp = TR.spec(host)
            code, text = self.model_status()
            self.lbl_status.setText(text)
            self.btn_download.setVisible(code == "missing")
            self.model_combo.setCurrentText(sp["model"])
            cur = self.current_point_id()
            self.pt_list.blockSignals(True)
            try:
                self.pt_list.clear()
                for p in sp["points"]:
                    n_q = len(TR.query_frames_of(sp, p["id"]))
                    label = p["name"] + ("" if n_q else "  " + tr("（还没放）"))
                    it = QListWidgetItem(_dot_icon(p["color"]), label)
                    it.setData(Qt.ItemDataRole.UserRole, int(p["id"]))
                    it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                    it.setCheckState(Qt.CheckState.Checked if p.get("enabled", True) else Qt.CheckState.Unchecked)
                    self.pt_list.addItem(it)
                    if cur is not None and int(p["id"]) == cur:
                        self.pt_list.setCurrentItem(it)
                if self.pt_list.currentRow() < 0 and self.pt_list.count():
                    self.pt_list.setCurrentRow(0)
            finally:
                self.pt_list.blockSignals(False)
            self._reload_targets()
            self._reload_bindings(sp)
            n = len(T.frames(sp["session"])) if sp["session"] else 0
            title = tr("AI 跟踪")
            if n:
                title += "  · %d %s" % (n, tr("帧"))
                if not self.sec.isExpanded():
                    self.sec.setExpanded(True)
            self.sec.setTitle(title)
            if not self._running():
                self._update_info(sp, n)
            self._sync_tool_state()
        finally:
            self._loading = False

    def _update_info(self, sp, n):
        parts = []
        if sp["queries"]:
            parts.append(tr("%d 帧上放了点") % len(sp["queries"]))
        if n:
            lost = len(TR.lost_frames(sp))
            parts.append(tr("%d 帧有跟踪结果") % n + (tr("，%d 帧有点跟丢") % lost if lost else ""))
        self.lbl_info.setText(" · ".join(parts))

    def _reload_targets(self):
        """跟随目标：任何能移动 / 旋转 / 缩放的图层（文字、图片、调整、其他视频，含蒙版）。不要求时间重叠。"""
        host, _p, _k = self._target()
        ctx = self._ctx()
        task = ctx.get("task") if isinstance(ctx.get("task"), dict) else None
        items = []
        if host is not None and task is not None:
            from cutora.fx.track_bind import list_follow_targets
            for rec in list_follow_targets(task, host):
                if rec["kind"] == "overlay":
                    lst = rec["list"]
                    i = int(rec["index"])
                    ov = (task.get(lst) or [])[i]
                    if rec["role"] == "text":
                        name = (rec["name"] or "")[:16] or tr("文字")
                        label = tr("文字：%s") % name
                    elif rec["role"] == "adjust":
                        from cutora.batch.layer_transfer import _adjust_label
                        label = tr("调整图层：%s") % _adjust_label(ov)
                    else:
                        label = tr("图片：%s") % (rec["name"] or "")[:20]
                    key = "ov:%s:%s" % (lst, ov.get("id") if ov.get("id") is not None else "#%d" % i)
                    items.append((label, {"kind": "overlay", "list": lst, "id": ov.get("id"), "index": i},
                                  {"target": "overlay", "list": lst, "overlay_index": i}, key))
                else:
                    tid = rec["track_id"]
                    ci = int(rec["clip_index"])
                    nm = rec["name"] or tr("视频 %s-%d") % (tid, ci + 1)
                    items.append((tr("视频：%s") % nm, {"kind": "clip", "track_id": tid, "clip_index": ci},
                                  {"target": "clip", "track_id": tid, "clip_index": ci}, "clip:%s:%d" % (tid, ci)))
        self._targets = items
        prev = self.target_combo.currentText()
        self.target_combo.blockSignals(True)
        try:
            self.target_combo.clear()
            if not items:
                self.target_combo.addItem(tr("（没有可跟随的文字 / 图片 / 视频）"), "")
            for label, _b, _l, key in items:
                self.target_combo.addItem(label, key)
            if prev:
                self.target_combo.setCurrentText(prev)
        finally:
            self.target_combo.blockSignals(False)

    def _reload_bindings(self, sp):
        self.bind_list.clear()
        for b in sp["bindings"]:
            t = b["target"]
            if b["mode"] == "stabilize":
                label = tr("稳定这条片段")
            elif t["kind"] == "overlay":
                label = tr("跟随 → %s #%d") % (tr("文字") if t["list"] == "text_overlays" else tr("图片"), t["index"] + 1)
            else:
                label = tr("跟随 → 视频 %s-%d") % (t["track_id"], t["clip_index"] + 1)
            names = {"position": tr("位置"), "rotation": tr("旋转"), "scale": tr("缩放")}
            what = "、".join(names[k] for k in ("position", "rotation", "scale") if b["apply"][k])
            label += "（%s%s）" % (what, tr("，对象跟踪") if b["source"] == "object" else "")
            self.bind_list.addItem(label)

    def _sync_tool_state(self):
        running = self._running()
        code, _t = self.model_status()
        usable = code in ("ready", "fake") and not running
        sp = self.spec()
        self.btn_place.setEnabled(usable or self.btn_place.isChecked())
        fi = self.current_frame()
        self.btn_unplace.setEnabled(not running and fi is not None and bool(TR.queries_at(sp, fi)))
        has_q = bool(sp["queries"])
        for b in (self.btn_back, self.btn_fwd, self.btn_all):
            b.setEnabled(usable and has_q)
        self.btn_pt_add.setEnabled(not running and len(sp["points"]) < TR.MAX_POINTS)
        self.btn_pt_del.setEnabled(not running and len(sp["points"]) > 1)
        lows = TR.lost_frames(sp) if sp["session"] else []
        self.btn_lost_prev.setVisible(bool(lows))
        self.btn_lost_next.setVisible(bool(lows))
        if lows and fi is not None:
            self.btn_lost_prev.setEnabled(TR.neighbour_lost_frame(sp, fi, -1) is not None)
            self.btn_lost_next.setEnabled(TR.neighbour_lost_frame(sp, fi, 1) is not None)
        src = self.src_combo.currentText()
        follow = self.mode_combo.currentText() != "stabilize"
        self.target_combo.setEnabled(follow and bool(self._targets))
        self.chk_fill.setVisible(not follow)
        if src == "object":
            host, _p, _k = self._target()
            have = bool(host is not None and A.spec(host)["session"])
        else:
            have = bool(sp["session"] and T.frames(sp["session"]))
        self.btn_apply.setEnabled(not running and have and (not follow or bool(self._targets)))
        self.btn_rebake.setEnabled(not running and bool(sp["bindings"]))
        self.btn_unbind.setEnabled(not running and self.bind_list.count() > 0)
        self.btn_clear.setEnabled(not running and bool(sp["session"]))
        self.btn_detach.setEnabled(not running and not TR.is_empty(sp))
        self.model_combo.setEnabled(not running)

    # ─────────────────────────── 命令 ───────────────────────────

    def _exec(self, name, extra: dict, *, loc: dict | None = None):
        host, params, _k = self._target()
        if host is None or params is None:
            return None
        payload = dict(loc if loc is not None else params)
        payload.update(extra)
        from cutora.core.commands import get_bus
        try:
            out = get_bus().execute(name, payload, source="ui")
        except Exception as exc:
            _LOG.exception("%s 失败", name)
            self._status_msg("%s：%s" % (name, exc))
            return None
        self._after_change()
        return out if isinstance(out, dict) else {"ok": True}

    def _status_msg(self, text: str, ms: int = 4000):
        bar = getattr(self.window(), "statusBar", None)
        if callable(bar):
            try:
                bar().showMessage(str(text), ms)
            except Exception:
                pass

    def _after_change(self, refresh_frame: bool = True):
        win = self.window()
        if refresh_frame:
            fn = getattr(win, "_present_preview_frame", None)
            if callable(fn):
                try:
                    fn()
                except Exception:
                    pass
        for name in ("video_canvas", "timeline"):
            w = getattr(win, name, None)
            if w is not None and hasattr(w, "update"):
                w.update()
        self.changed.emit()

    def ensure_session(self) -> bool:
        if self.spec()["session"]:
            return True
        ok = self._exec("track.session", {"op": "create", "model": self.model_combo.currentText()})
        if ok:
            self.reload()
        return bool(ok)

    def _on_model_changed(self, mid):
        if self._loading or not mid:
            return
        if self.spec()["session"] or self.spec()["points"]:
            self._exec("track.set", {"patch": {"model": str(mid)}})
        self.reload()

    def _on_point_add(self):
        if not self.ensure_session():
            return
        if self._exec("track.point", {"op": "add"}):
            self.reload()
            self.pt_list.setCurrentRow(self.pt_list.count() - 1)

    def _on_point_del(self):
        pid = self.current_point_id()
        if pid is None:
            return
        sp = self.spec()
        if self._exec("track.point", {"op": "remove", "point_id": pid}):
            if sp["session"]:
                try:
                    with T.Session(sp["session"]) as sess:
                        sess.remove_point(pid)
                except Exception:
                    _LOG.exception("删点缓存失败")
            self.reload()

    def _on_point_item_changed(self, item):
        if self._loading:
            return
        pid = int(item.data(Qt.ItemDataRole.UserRole))
        self._exec("track.set", {"patch": {"points": [{"id": pid, "enabled": item.checkState() == Qt.CheckState.Checked}]}})
        self.reload()

    def _on_place_toggled(self, on):
        if self._loading:
            return
        if on and not self.ensure_session():
            self.set_place_mode(False)
            return
        if on:
            self.sec.setExpanded(True)
        self.canvas_action.emit("track_place", bool(on))

    def set_place_mode(self, on: bool):
        self.btn_place.blockSignals(True)
        self.btn_place.setChecked(bool(on))
        self.btn_place.blockSignals(False)

    def place_from_canvas(self, sx: float, sy: float) -> bool:
        """画布点了一下：源帧归一化坐标。放 / 挪当前选中的点。"""
        if self._running():
            self._status_msg(tr("跟踪还在跑，等一下"))
            return False
        if not self.ensure_session():
            return False
        pid = self.current_point_id()
        sp = self.spec()
        if pid is None:
            pid = sp["points"][0]["id"] if sp["points"] else None
        fi = self.current_frame()
        if pid is None or fi is None:
            self._status_msg(tr("这条片段没有可读的源视频"))
            return False
        ok = self._exec("track.point", {"op": "place", "point_id": int(pid), "frame": int(fi),
                                        "x": float(sx), "y": float(sy)})
        if ok:
            self.reload()
            # 下一个还没放的点自动选中，连着点更顺手
            sp = self.spec()
            for i, p in enumerate(sp["points"]):
                if not TR.query_frames_of(sp, p["id"]):
                    self.pt_list.setCurrentRow(i)
                    break
        return bool(ok)

    def unplace_from_canvas(self, point_id: int) -> bool:
        fi = self.current_frame()
        if fi is None:
            return False
        ok = self._exec("track.point", {"op": "unplace", "point_id": int(point_id), "frame": int(fi)})
        if ok:
            self.reload()
        return bool(ok)

    def _on_unplace(self):
        pid = self.current_point_id()
        sp = self.spec()
        fi = self.current_frame()
        if fi is None:
            return
        qs = TR.queries_at(sp, fi)
        if pid is None or not any(int(q["pt"]) == pid for q in qs):
            pid = int(qs[0]["pt"]) if qs else None
        if pid is not None:
            self.unplace_from_canvas(pid)

    def _seek_timeline(self, t_sec: float):
        win = self.window()
        ms = int(round(max(0.0, float(t_sec)) * 1000.0))
        fn = getattr(win, "on_seek_request", None)
        if callable(fn):
            try:
                fn(ms)
                return
            except Exception:
                _LOG.exception("跳转失败")
        try:
            win._timeline_ms = ms
        except Exception:
            pass

    def _jump_lost(self, direction: int):
        sp = self.spec()
        fi = self.current_frame()
        if fi is None:
            return
        target = TR.neighbour_lost_frame(sp, fi, direction)
        if target is None:
            self._status_msg(tr("这个方向没有跟丢的帧了"))
            return
        host, _p, _k = self._target()
        self._seek_timeline(A.source_frame_to_timeline(host, target, self.fps() or 30.0) + 1e-4)
        self.lbl_info.setText(tr("第 %d 帧有点跟丢了：在这帧把它放回正确位置，再跟踪一次") % target)
        self.reload()

    # ---- 跟踪任务 ----
    def _running(self) -> bool:
        w = self._worker
        return w is not None and w.isRunning()

    def _on_track(self, direction: str):
        sp = self.spec()
        path = self._ctx().get("path")
        rng = self.frame_range()
        if not sp["session"] or not path or not os.path.isfile(str(path)) or rng is None:
            self._status_msg(tr("这条片段没有可读的源视频"))
            return
        from cutora.ui.gpu_notice import confirm as _gpu_ok
        if not _gpu_ok(self, "tracking"):           # Q193：没有 N 卡先说清楚
            return
        runs = TR.plan_runs(sp, rng, direction)
        if not runs:
            self._status_msg(tr("这个方向上没有可跟的帧：先在画面上放点"))
            return
        n = sum(TR.run_length(r) for r in runs)
        est = self._confirm_estimate(n, sp["model"])
        if est is None:
            return
        from cutora.ui.matte_worker import MatteWorker
        if MatteWorker.busy():
            self._status_msg(tr("已有 AI 任务在跑"))
            return
        w = MatteWorker("track", {"spec": sp, "path": str(path), "frame_range": list(rng), "direction": direction,
                                  "model": sp["model"], "gpu_label": "AI 跟踪"}, parent=self)
        self._worker = w
        MatteWorker.current = w
        w.progress.connect(self._on_progress)
        w.done.connect(self._on_done)
        w.failed.connect(self._on_failed)
        w.queued.connect(self._on_queued)
        try:
            self.window().is_ai_running = True
        except Exception:
            pass
        self.sec.setExpanded(True)
        self.prog.setValue(0)
        self.prog.show()
        self.btn_cancel.show()
        self.lbl_info.setText(tr("正在跟踪 %d 帧…") % n + (tr("（预计约 %s）") % est if est else ""))
        self._sync_tool_state()
        w.start()

    def _confirm_estimate(self, n_frames: int, model: str):
        if TR.backend_name() == "fake":
            return ""
        try:
            from cutora.core.config import DEVICE
        except Exception:
            DEVICE = "cpu"
        from cutora.fx.matte_jobs import estimate_seconds, format_duration_zh
        secs, measured = estimate_seconds(n_frames, model, str(DEVICE))
        txt = format_duration_zh(secs) + ("" if measured else tr("，粗估"))
        if not str(DEVICE).startswith("cuda") and secs > 60:
            msg = tr("这台电脑没有可用的 NVIDIA 显卡，AI 跟踪在 CPU 上跑。\n跟踪 %d 帧大约要 %s。\n\n"
                     "建议先只跟一小段，或换 256 档模型。继续吗？") % (n_frames, txt)
            if QMessageBox.question(self, tr("AI 跟踪要跑一会儿"), msg,
                                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No) \
                    != QMessageBox.StandardButton.Yes:
                return None
        return txt

    def _on_queued(self, who):
        self.btn_cancel.show()
        self.lbl_info.setText(tr("排队中：显卡正被「%s」使用，结束后自动开始（可取消）") % (who or tr("其他 AI 任务")))

    def _on_cancel(self):
        if self._running():
            self._worker.cancel()
            self.lbl_info.setText(tr("正在停止…"))

    def _on_progress(self, done, total, frame_idx, vis):
        total = max(1, int(total))
        self.prog.setValue(int(1000 * min(1.0, done / float(total))))
        import time
        w = self._worker
        secs = time.perf_counter() - (w.started_at if w is not None and w.started_at else time.perf_counter())
        fps = done / secs if secs > 0.2 else 0.0
        eta = (total - done) / fps if fps > 0 else 0.0
        self.lbl_info.setText(tr("跟踪中 %d / %d 帧 · %.1f 帧/秒 · 还要约 %d 秒") % (done, total, fps, int(eta)))
        if done % 15 == 0:
            self._after_change(refresh_frame=False)

    def _finish_job(self):
        from cutora.ui.matte_worker import MatteWorker
        if MatteWorker.current is self._worker:
            MatteWorker.current = None
        self._worker = None
        try:
            self.window().is_ai_running = False
        except Exception:
            pass
        self.prog.hide()
        self.btn_cancel.hide()

    def _on_done(self, r):
        self._finish_job()
        r = r or {}
        n, secs = int(r.get("frames", 0)), float(r.get("seconds", 0.0))
        msg = tr("跟踪完成：%d 帧，%.1f 秒（%.1f 帧/秒）") % (n, secs, n / secs if secs > 0 else 0.0)
        if r.get("lost"):
            msg += tr("；%d 帧有点跟丢（时间线上红色），到那附近再放一次点重跟") % int(r["lost"])
        if r.get("cancelled"):
            msg = tr("已取消，") + msg
        self._status_msg(msg, 6000)
        self.reload()
        self._after_change(refresh_frame=False)
        self.lbl_info.setText(msg)

    def _on_failed(self, msg):
        self._finish_job()
        self.lbl_info.setText(tr("失败：%s") % msg)
        self._status_msg(tr("AI 跟踪失败：%s") % msg, 8000)
        self.reload()

    # ---- 应用 / 绑定 ----
    def current_binding(self) -> dict | None:
        from cutora.fx.track_bind import normalize_binding
        mode = self.mode_combo.currentText() or "follow"
        target = {"kind": "self"}
        if mode == "follow":
            key = self.target_combo.currentText()
            hit = next((it for it in self._targets if it[3] == key), None)
            if hit is None:
                return None
            target = hit[1]
        fi = self.current_frame()
        sp = self.spec()
        return normalize_binding({
            "mode": mode, "source": self.src_combo.currentText() or "points",
            "points": [p["id"] for p in sp["points"] if p.get("enabled", True)],
            "target": target,
            "apply": {"position": self.chk_pos.isChecked(), "rotation": self.chk_rot.isChecked(),
                      "scale": self.chk_scale.isChecked()},
            "smooth": int(self.sl_smooth.value()), "offset": [0, 0],
            "fill": "auto" if self.chk_fill.isChecked() else "none",
            "ref_frame": int(fi or 0), "simplify": self.chk_simplify.isChecked()})

    def _locate_target(self, binding: dict):
        """绑定记录 → (目标对象, 命令定位)；按 id 找覆层（下标可能已经变了）。"""
        host, params, _k = self._target()
        ctx = self._ctx()
        task = ctx.get("task") if isinstance(ctx.get("task"), dict) else None
        t = binding["target"]
        if binding["mode"] == "stabilize" or t["kind"] == "self":
            return host, dict(params)
        if task is None:
            return None, None
        if t["kind"] == "overlay":
            lst = task.get(t["list"]) or []
            idx = None
            if t.get("id") is not None:
                for i, ov in enumerate(lst):
                    if isinstance(ov, dict) and ov.get("id") == t["id"]:
                        idx = i
                        break
            if idx is None and 0 <= int(t.get("index", -1)) < len(lst):
                idx = int(t["index"])
            if idx is None:
                return None, None
            return lst[idx], {"target": "overlay", "list": t["list"], "overlay_index": idx}
        tl = task.get("timeline")
        for tr_obj in getattr(tl, "tracks", []) or []:
            if str(getattr(tr_obj, "id", "") or "") == t["track_id"]:
                clips = getattr(tr_obj, "clips", []) or []
                if 0 <= t["clip_index"] < len(clips):
                    return clips[t["clip_index"]], {"target": "clip", "track_id": t["track_id"],
                                                    "clip_index": t["clip_index"]}
        return None, None

    def bake(self, binding: dict):
        """算关键帧（不写）。返回 (paths, info) 或 (None, 原因)。"""
        from cutora.fx import track_bind as TB
        host, _p, _k = self._target()
        rng = self.frame_range()
        if host is None or rng is None:
            return None, tr("这条片段没有可读的源视频")
        target, loc = self._locate_target(binding)
        if target is None:
            return None, tr("找不到绑定的目标（可能已被删除）")
        motion = TB.motion_for(host, binding, rng)
        if motion is None:
            return None, (tr("没有对象跟踪数据：先在「AI 抠像」里把对象抠出来并传播") if binding["source"] == "object"
                          else tr("没有跟踪结果：先放点并跟踪"))
        ctx = self._ctx()
        task = ctx.get("task") if isinstance(ctx.get("task"), dict) else {}
        from cutora.model.canvas import canvas_wh
        cw, ch = canvas_wh(task)
        if binding["mode"] == "stabilize":
            res = TB.bake_stabilize(motion, host, binding, int(cw), int(ch))
        else:
            res = TB.bake_follow(motion, host, target, binding, int(cw), int(ch))
        if not res["paths"]:
            return None, tr("目标和这条片段在时间上没有重叠，或者没勾选要跟的属性")
        res["loc"] = loc
        return res["paths"], res

    def apply_binding(self, binding: dict) -> bool:
        from PySide6.QtWidgets import QApplication
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            paths, info = self.bake(binding)
        finally:
            QApplication.restoreOverrideCursor()
        if paths is None:
            self._status_msg(info)
            self.lbl_info.setText(str(info))
            return False
        extra = {"paths": paths, "binding": binding}
        if binding["mode"] == "follow":
            extra["target_loc"] = info["loc"]
        out = self._exec("track.apply", extra)
        if out:
            n_keys = sum(len(v) for v in paths.values())
            msg = tr("已生成关键帧：%d 帧 → %d 个关键帧") % (int(info.get("frames", 0)), n_keys)
            if info.get("zoom", 1.0) > 1.0 + 1e-6:
                msg += tr("，放大 %.0f%% 填满黑边") % ((info["zoom"] - 1.0) * 100.0)
            self.reload()
            self.lbl_info.setText(msg)
        return bool(out)

    def _on_apply(self):
        b = self.current_binding()
        if b is None:
            self._status_msg(tr("先选一个目标"))
            return
        self.apply_binding(b)

    def _on_rebake_all(self):
        sp = self.spec()
        ok = 0
        for b in list(sp["bindings"]):
            if self.apply_binding(b):
                ok += 1
        self.lbl_info.setText(tr("已更新 %d / %d 个绑定") % (ok, len(sp["bindings"])))

    def _on_unbind(self):
        row = self.bind_list.currentRow()
        if row < 0:
            row = self.bind_list.count() - 1
        if row >= 0 and self._exec("track.unbind", {"index": int(row)}):
            self.reload()

    # ---- 下载 / 清除 ----
    def _on_download(self):
        try:
            from cutora.asr.models import get_model
            row = get_model(self.spec()["model"] or self.model_combo.currentText())
        except Exception:
            row = None
        if row is None:
            self._status_msg(tr("未登记的模型"))
            return
        from cutora.ui.model_download_dialog import ModelDownloadDialog
        ModelDownloadDialog(row, self.window()).exec()
        self.reload()

    def _on_clear(self):
        if QMessageBox.question(self, tr("清除跟踪结果"), tr("删掉全部放下的点和跟踪结果？（点和绑定记录保留）"),
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No) \
                != QMessageBox.StandardButton.Yes:
            return
        if self._exec("track.session", {"op": "clear"}):
            self.reload()

    def _on_detach(self):
        if QMessageBox.question(self, tr("移除 AI 跟踪"), tr("把 AI 跟踪从这条片段上拿掉并删掉缓存？已生成的关键帧保留。"),
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No) \
                != QMessageBox.StandardButton.Yes:
            return
        self.set_place_mode(False)
        self.canvas_action.emit("track_place", False)
        if self._exec("track.session", {"op": "detach"}):
            self.reload()

