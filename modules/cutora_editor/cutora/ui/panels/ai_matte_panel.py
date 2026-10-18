# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q152-D · 检查器里的「AI 抠像」一节（只挂在视频片段上）。

流程：点「点选对象」→ 到画布上点一下要抠的东西（点 = 加，Alt+点 = 减，拖 = 框）→ 这一帧立刻出结果
→ 「向前 / 向后 / 全部」传播到整段 → 时间线上这条片段出现覆盖条（绿 = 有结果，黄 = 低置信度）。
所有改数据的操作走命令总线（ai.matte.*，可撤销）；算 mask 的任务走 MatteWorker（后台线程，有进度、可取消）。
模型没下载时这一节就是一个「下载模型」按钮（许可闸门 → 进度）。
"""
from __future__ import annotations

import os

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPixmap, QIcon
from PySide6.QtWidgets import (
    QCheckBox, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMessageBox, QProgressBar,
    QPushButton, QToolButton, QVBoxLayout, QWidget,
)

from cutora.core.log import get_logger
from cutora.fx import ai_matte as A
from cutora.fx import matte_store as S
from cutora.ui.i18n import tr
from cutora.ui.theme.tokens import SPACE_1, SPACE_2
from cutora.ui.widgets.collapsible import CollapsibleSection
from cutora.ui.widgets.inspector import InspectForm, SliderSpin, ValueCombo, add_inspect_row

_LOG = get_logger("cutora.ui.panels.ai_matte_panel")


def _swatch_icon(color: str, px: int = 12) -> QIcon:
    pm = QPixmap(px, px)
    pm.fill(QColor(color))
    return QIcon(pm)


class AiMattePanel(QWidget):
    """``bind(getter, context)``：getter() → (host, cmd_params, kind)；context() → {"path", "src_t", "tl"}。"""

    canvas_action = Signal(str, object)         # ("matte_prompt", on/off)
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._getter = None
        self._context = None
        self._loading = False
        self._worker = None
        self._coalesce = {}
        # Q166：点完一批再一起算 —— 待算的帧、在跑时又点了的帧
        self._dirty_frames: set = set()
        self._pending_frame = None
        self.show_tint = True                   # 画布上半透明的彩色结果（只是看，不进工程）
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.sec = self._build()
        root.addWidget(self.sec)

    # ─────────────────────────── 构建 ───────────────────────────

    def _build(self):
        sec = CollapsibleSection(tr("AI 抠像 / 抠图"))
        sec.setExpanded(False)
        host = QWidget()
        root_lay = QVBoxLayout(host)
        root_lay.setContentsMargins(0, 0, 0, 0)
        root_lay.setSpacing(SPACE_1)

        # ═══ RMBG-2.0 发丝级全自动图片/当前帧抠图 ═══
        rmbg_row = QHBoxLayout()
        rmbg_row.setSpacing(SPACE_1)
        self.btn_rmbg_cutout = QPushButton(tr("✂️ AI 一键抠图去背景 (RMBG-2.0 发丝级)"))
        self.btn_rmbg_cutout.setToolTip(
            tr("调用本地 RMBG-2.0 (BiRefNet) 发丝级抠图模型，免点选全自动去除背景并原位替换为透明 PNG（支持 Ctrl+Z 撤销）"))
        self.btn_rmbg_cutout.clicked.connect(self._on_rmbg_cutout)
        rmbg_row.addWidget(self.btn_rmbg_cutout, 1)
        self.btn_rmbg_restore = QPushButton(tr("↩️ 还原原图"))
        self.btn_rmbg_restore.setToolTip(tr("恢复抠图前的原始图片"))
        self.btn_rmbg_restore.clicked.connect(self._on_rmbg_restore)
        self.btn_rmbg_restore.setVisible(False)
        rmbg_row.addWidget(self.btn_rmbg_restore)
        root_lay.addLayout(rmbg_row)

        self.lbl_rmbg_hint = QLabel(tr("RMBG-2.0 BiRefNet：免点选一键抠除图片/人物/商品背景（发丝级透明通道）"))
        self.lbl_rmbg_hint.setObjectName("fieldHint")
        self.lbl_rmbg_hint.setWordWrap(True)
        root_lay.addWidget(self.lbl_rmbg_hint)

        # ═══ SAM 2.1 视频动态点选追踪抠像（仅视频片段显示） ═══
        self.sam_video_box = QWidget()
        lay = QVBoxLayout(self.sam_video_box)
        lay.setContentsMargins(0, 4, 0, 0)
        lay.setSpacing(SPACE_1)
        root_lay.addWidget(self.sam_video_box)

        # 状态 / 模型
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
        self.model_combo = ValueCombo(tuple(self._model_items()))
        self.model_combo.setToolTip(tr("Large 更准（8 GB+ 显存）；Base+ 更省显存"))
        self.model_combo.valueChanged.connect(self._on_model_changed)
        mrow.addWidget(QLabel(tr("视频抠像")))
        mrow.addWidget(self.model_combo, 1)
        lay.addLayout(mrow)

        # 套用 / 查看
        top = QHBoxLayout()
        top.setSpacing(SPACE_1)
        self.chk_enable = QCheckBox(tr("套用抠图"))
        self.chk_enable.setToolTip(tr("关掉只是不套用，提示点和结果都还在"))
        self.chk_enable.toggled.connect(lambda on: self._set({"enabled": bool(on)}))
        top.addWidget(self.chk_enable)
        top.addStretch(1)
        self.view_combo = ValueCombo((("comp", tr("合成")), ("matte", tr("Matte（黑白）")), ("overlay", tr("彩色叠加"))))
        self.view_combo.setToolTip(tr("彩色叠加 = 每个对象一种颜色盖在画面上，方便检查；导出前换回「合成」"))
        self.view_combo.valueChanged.connect(lambda v: self._set({"view": v}))
        top.addWidget(QLabel(tr("查看")))
        top.addWidget(self.view_combo)
        lay.addLayout(top)

        # 对象
        orow = QHBoxLayout()
        orow.setSpacing(SPACE_1)
        orow.addWidget(QLabel(tr("对象")))
        orow.addStretch(1)
        self.btn_obj_add = QToolButton()
        self.btn_obj_add.setText("+")
        self.btn_obj_add.setToolTip(tr("再抠一个对象（各有颜色，最多 8 个）"))
        self.btn_obj_add.clicked.connect(self._on_obj_add)
        self.btn_obj_del = QToolButton()
        self.btn_obj_del.setText("−")
        self.btn_obj_del.setToolTip(tr("删掉选中的对象及它的提示点和结果"))
        self.btn_obj_del.clicked.connect(self._on_obj_del)
        orow.addWidget(self.btn_obj_add)
        orow.addWidget(self.btn_obj_del)
        lay.addLayout(orow)
        self.obj_list = QListWidget()
        self.obj_list.setObjectName("matteObjectList")
        self.obj_list.setFixedHeight(72)
        self.obj_list.itemChanged.connect(self._on_obj_item_changed)
        self.obj_list.currentRowChanged.connect(lambda _r: self._on_obj_row_changed())
        lay.addWidget(self.obj_list)
        mrow2 = QHBoxLayout()
        mrow2.setSpacing(SPACE_1)
        mrow2.addWidget(QLabel(tr("选中的对象")))
        self.obj_mode = ValueCombo((("add", tr("保留")), ("subtract", tr("挖掉"))))
        self.obj_mode.setToolTip(tr("保留 = 抠出来留下；挖掉 = 从保留的部分里减掉（例如人留下、手里的东西挖掉）"))
        self.obj_mode.valueChanged.connect(self._on_obj_mode)
        mrow2.addWidget(self.obj_mode, 1)
        lay.addLayout(mrow2)

        # 工具
        trow = QHBoxLayout()
        trow.setSpacing(SPACE_1)
        self.btn_prompt = QToolButton()
        self.btn_prompt.setText(tr("点选对象"))
        self.btn_prompt.setCheckable(True)
        self.btn_prompt.setToolTip(tr("到画布上：点 = 加一个正点；Alt+点 = 负点（不要这块）；拖 = 框；Esc 退出"))
        self.btn_prompt.toggled.connect(self._on_prompt_toggled)
        trow.addWidget(self.btn_prompt)
        self.btn_clear_frame = QToolButton()
        self.btn_clear_frame.setText(tr("清这帧的点"))
        self.btn_clear_frame.setToolTip(tr("删掉当前帧上选中对象的提示点，并重算这一帧"))
        self.btn_clear_frame.clicked.connect(self._on_clear_frame)
        trow.addWidget(self.btn_clear_frame)
        trow.addStretch(1)
        lay.addLayout(trow)
        # Q166：一次算完这一帧的全部点
        crow = QHBoxLayout()
        crow.setSpacing(SPACE_1)
        self.btn_compute = QPushButton(tr("计算这一帧"))
        self.btn_compute.setToolTip(tr("把这一帧上点好的全部点 / 框一次算出来（画布上按 Enter 也行）"))
        self.btn_compute.clicked.connect(self.compute_now)
        crow.addWidget(self.btn_compute, 1)
        self.chk_auto = QCheckBox(tr("每点一下就算"))
        self.chk_auto.setToolTip(tr("勾上：每加一个点马上重算（在算的时候点的会排队，算完再一起算一次）\n"
                                    "不勾：先把点都点好，再按「计算这一帧」/ Enter 一次算完"))
        self.chk_auto.setChecked(self.AUTO_DEFAULT)
        crow.addWidget(self.chk_auto)
        lay.addLayout(crow)
        vrow = QHBoxLayout()
        vrow.setSpacing(SPACE_1)
        self.chk_tint = QCheckBox(tr("显示彩色区域"))
        self.chk_tint.setToolTip(tr("点选时画布上盖的半透明颜色（每个对象一种颜色），只是看，不影响结果。画布上按 H 也能切换"))
        self.chk_tint.setChecked(True)
        self.chk_tint.toggled.connect(self.set_show_tint)
        vrow.addWidget(self.chk_tint)
        self.chk_merge = QCheckBox(tr("合成一整块（去接缝）"))
        self.chk_merge.setToolTip(tr("几个「保留」对象挨在一起（人 + 手里的书）时当成一个整体，\n"
                                     "把它们交界处的半透明缝补实；外轮廓不变"))
        self.chk_merge.toggled.connect(lambda on: self._set({"merge": bool(on)}))
        vrow.addWidget(self.chk_merge)
        vrow.addStretch(1)
        lay.addLayout(vrow)
        self.lbl_hint = QLabel(tr("点 = 加 · Alt+点 = 减 · 拖 = 框 · Enter = 计算"))
        self.lbl_hint.setObjectName("fieldHint")
        lay.addWidget(self.lbl_hint)

        # 传播
        prow = QHBoxLayout()
        prow.setSpacing(SPACE_1)
        self.btn_back = QPushButton(tr("◀ 向后"))
        self.btn_back.setToolTip(tr("从当前帧往片段开头传播"))
        self.btn_back.clicked.connect(lambda: self._on_propagate("backward"))
        self.btn_fwd = QPushButton(tr("向前 ▶"))
        self.btn_fwd.setToolTip(tr("从当前帧往片段结尾传播"))
        self.btn_fwd.clicked.connect(lambda: self._on_propagate("forward"))
        self.btn_all = QPushButton(tr("⟷ 全部"))
        self.btn_all.setToolTip(tr("两头都传：先往后再往前"))
        self.btn_all.clicked.connect(lambda: self._on_propagate("all"))
        prow.addWidget(self.btn_back)
        prow.addWidget(self.btn_fwd)
        prow.addWidget(self.btn_all)
        lay.addLayout(prow)
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
        self.btn_low_prev = QToolButton()
        self.btn_low_prev.setText(tr("◀ 上一处低置信"))
        self.btn_low_prev.setToolTip(tr("跳到前面最近的黄色段（时间线上点黄段也能跳）"))
        self.btn_low_prev.clicked.connect(lambda: self._jump_low(-1))
        self.btn_low_next = QToolButton()
        self.btn_low_next.setText(tr("下一处低置信 ▶"))
        self.btn_low_next.setToolTip(tr("跳到后面最近的黄色段，补一两个点再传播"))
        self.btn_low_next.clicked.connect(lambda: self._jump_low(1))
        lrow.addWidget(self.btn_low_prev)
        lrow.addWidget(self.btn_low_next)
        lrow.addStretch(1)
        lay.addLayout(lrow)

        self.btn_inpaint_erase = QPushButton(tr("🪄 无痕抹除选中对象（去人物/去话筒）"))
        self.btn_inpaint_erase.setToolTip(tr("将当前 SAM 2.1 点选追踪的人物、乱入路人或穿帮话筒从画面中无痕抹除并自动填补背景"))
        self.btn_inpaint_erase.clicked.connect(self._on_inpaint_erase)
        lay.addWidget(self.btn_inpaint_erase)

        # 输出
        form = InspectForm()
        self.sl_feather = SliderSpin(0, 60, 1, 0)
        self.sl_feather.setToolTip(tr("边缘羽化（画布像素）"))
        self.sl_feather.valueChanged.connect(lambda v: self._set({"feather": float(v)}, coalesce="feather"))
        add_inspect_row(form, tr("羽化"), self.sl_feather)
        self.sl_shrink = SliderSpin(-30, 30, 1, 0)
        self.sl_shrink.setToolTip(tr("正 = 往里收（去掉边上的背景色），负 = 往外扩"))
        self.sl_shrink.valueChanged.connect(lambda v: self._set({"shrink": float(v)}, coalesce="shrink"))
        add_inspect_row(form, tr("收缩 / 扩张"), self.sl_shrink)
        self.chk_refine = QCheckBox(tr("精修边缘（导向滤波）"))
        self.chk_refine.setToolTip(tr("用画面本身当引导把边缘贴回去，发丝 / 半透明边缘更自然；每帧多花十几毫秒"))
        self.chk_refine.toggled.connect(lambda on: self._set({"refine": bool(on)}))
        form.addRow("", self.chk_refine)
        fw = QWidget()
        fw.setLayout(form)
        lay.addWidget(fw)

        # 清除
        brow = QHBoxLayout()
        brow.setSpacing(SPACE_1)
        brow.addStretch(1)
        self.btn_clear_all = QToolButton()
        self.btn_clear_all.setText(tr("清除结果"))
        self.btn_clear_all.setToolTip(tr("删掉全部提示点和算出来的结果（对象和设置保留）"))
        self.btn_clear_all.clicked.connect(self._on_clear_all)
        self.btn_detach = QToolButton()
        self.btn_detach.setText(tr("移除"))
        self.btn_detach.setToolTip(tr("把 AI 抠像从这条片段上拿掉，并删掉缓存"))
        self.btn_detach.clicked.connect(self._on_detach)
        brow.addWidget(self.btn_clear_all)
        brow.addWidget(self.btn_detach)
        lay.addLayout(brow)
        sec.addWidget(host)
        return sec

    @staticmethod
    def _model_items():
        try:
            from cutora.asr.models import catalog
            rows = [r for r in catalog() if r.get("kind") == "matte" and r.get("enabled", True)]
        except Exception:
            rows = []
        items = [(str(r["id"]), str(r.get("title_zh") or r["id"])) for r in rows]
        return items or [("matte-sam21-large", "SAM 2.1 Large")]

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
            _LOG.exception("取 AI 抠像对象失败")
            return None, None, None

    def _ctx(self) -> dict:
        if not callable(self._context):
            return {}
        try:
            return dict(self._context() or {})
        except Exception:
            _LOG.exception("取 AI 抠像上下文失败")
            return {}

    def _layer_host(self, host):
        """时间换算用图层（选中 AI 抠像节点时 host 是节点 params，没有起止时间）。"""
        layer = self._ctx().get("layer")
        return layer if layer is not None else host

    def spec(self) -> dict:
        host, _p, _k = self._target()
        return A.spec(host) if host is not None else A.normalize(None)

    def current_object_id(self):
        it = self.obj_list.currentItem()
        if it is None:
            return None
        return int(it.data(Qt.ItemDataRole.UserRole))

    def current_frame(self):
        """当前播放头在源视频里的帧号（没有源 → None）。"""
        ctx = self._ctx()
        path = ctx.get("path")
        if not path:
            return None
        sp = self.spec()
        fps = None
        if sp["session"]:
            idx = S.load_index(sp["session"])
            if idx and idx.get("fps"):
                fps = float(idx["fps"])
        if not fps:
            from cutora.fx.matte_jobs import source_fps
            fps = source_fps(path)
        return S.frame_index(float(ctx.get("src_t") or 0.0), fps)

    def model_status(self) -> tuple:
        """(状态码, 文案)：ready / missing / fake / unknown。"""
        sp = self.spec()
        mid = sp["model"]
        from cutora.fx.segment import backend_name
        if backend_name() == "fake":
            return "fake", tr("演示后端（颜色泛洪），不需要模型")
        try:
            from cutora.asr.models import get_model, verify_model
            row = get_model(mid)
            if row is None:
                return "unknown", tr("未登记的模型 %s") % mid
            st = verify_model(row)
        except Exception:
            _LOG.exception("查模型状态失败")
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
        return "missing", tr("模型未下载（约 %d MB）") % int(row.get("size_mb") or 0)

    def _on_rmbg_cutout(self):
        host, _params, kind = self._target()
        win = self.window()
        try:
            from cutora.ui.ai_studio_room import run_rmbg_cutout_ui
            if kind == "image" and host is not None:
                run_rmbg_cutout_ui(win, target_overlay=host)
            else:
                ctx = self._ctx()
                path = ctx.get("path")
                run_rmbg_cutout_ui(win, source_path=path)
            self.reload()
        except Exception as e:
            _LOG.exception("RMBG-2.0 抠图失败: %s", e)

    def _on_rmbg_restore(self):
        host, _params, _kind = self._target()
        win = self.window()
        try:
            from cutora.ui.ai_studio_room import restore_rmbg_original_ui
            if host is not None:
                restore_rmbg_original_ui(win, host)
                self.reload()
        except Exception as e:
            _LOG.exception("还原抠图原图失败: %s", e)

    def reload(self):
        host, params, kind = self._target()
        self._loading = True
        try:
            is_video_matte = (kind == "clip" or (kind == "node:ai_matte" and self._ctx().get("layer") is not None))
            is_image_matte = (kind == "image")
            on = host is not None and params is not None and (is_video_matte or is_image_matte)
            self.sec.setVisible(on)
            if not on:
                return
            try:
                from cutora.ui.ai_studio_room import has_rmbg_original
                has_orig = has_rmbg_original(host)
            except Exception:
                has_orig = False
            self.btn_rmbg_restore.setVisible(bool(has_orig))
            self.sam_video_box.setVisible(bool(is_video_matte))
            if is_image_matte:
                self.btn_rmbg_cutout.setText(tr("✂️ AI 一键抠图去背景 (RMBG-2.0 发丝级透明抠图)"))
                title = tr("AI 抠图 (RMBG-2.0)")
                if has_orig:
                    title += "  · " + tr("已抠图")
                self.sec.setTitle(title)
                if not self.sec.isExpanded():
                    self.sec.setExpanded(True)
                return
            self.btn_rmbg_cutout.setText(tr("✂️ AI 一键抠当前帧为透明图层 (RMBG-2.0 发丝级)"))
            sp = A.spec(host)
            code, text = self.model_status()
            self.lbl_status.setText(text)
            self.btn_download.setVisible(code == "missing")
            self.model_combo.setCurrentText(sp["model"])
            self.chk_enable.setChecked(sp["enabled"])
            self.view_combo.setCurrentText(sp["view"])
            self.sl_feather.setValue(sp["feather"])
            self.sl_shrink.setValue(sp["shrink"])
            self.chk_refine.setChecked(sp["refine"])
            self.chk_merge.setChecked(bool(sp.get("merge", True)))
            cur = self.current_object_id()
            self.obj_list.blockSignals(True)
            try:
                self.obj_list.clear()
                for o in sp["objects"]:
                    it = QListWidgetItem(_swatch_icon(o["color"]), o["name"])
                    it.setToolTip(tr("挖掉") if o.get("mode") == "subtract" else tr("保留"))
                    if o.get("mode") == "subtract":
                        f = it.font()
                        f.setStrikeOut(True)
                        it.setFont(f)
                    it.setData(Qt.ItemDataRole.UserRole, int(o["id"]))
                    it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEditable)
                    it.setCheckState(Qt.CheckState.Checked if o.get("enabled", True) else Qt.CheckState.Unchecked)
                    self.obj_list.addItem(it)
                    if cur is not None and int(o["id"]) == cur:
                        self.obj_list.setCurrentItem(it)
                if self.obj_list.currentRow() < 0 and self.obj_list.count():
                    self.obj_list.setCurrentRow(0)
            finally:
                self.obj_list.blockSignals(False)
            n_frames = len(S.coverage(sp["session"])) if sp["session"] else 0
            title = tr("AI 抠像 / 抠图")
            if n_frames:
                title += "  · %d %s" % (n_frames, tr("帧"))
                if sp["enabled"] and not self.sec.isExpanded():
                    self.sec.setExpanded(True)
            self.sec.setTitle(title)
            if not self._running():
                self._update_info(sp, n_frames)
            self._sync_tool_state()
        finally:
            self._loading = False

    def _update_info(self, sp, n_frames):
        parts = []
        if sp["prompts"]:
            parts.append(tr("%d 帧有提示点") % len(sp["prompts"]))
        if n_frames:
            cov = S.coverage(sp["session"])
            low = sum(1 for v in cov.values() if v < 0.5)
            parts.append(tr("%d 帧有结果") % n_frames + (tr("，%d 帧置信度低") % low if low else ""))
            mb = S.session_bytes(sp["session"]) / (1024.0 * 1024.0)
            if mb >= 1:
                parts.append("%.0f MB" % mb)
        self.lbl_info.setText(" · ".join(parts))

    def _sync_tool_state(self):
        running = self._running()
        code, _t = self.model_status()
        usable = code in ("ready", "fake") and not running
        sp = self.spec()
        has_obj = bool(sp["objects"])
        for w in (self.btn_prompt, self.btn_clear_frame):
            w.setEnabled(usable)
        fi = self.current_frame()
        n_pts = len(A.prompts_at(sp, fi)) if fi is not None else 0
        self.btn_compute.setEnabled(code in ("ready", "fake") and n_pts > 0)
        self.btn_compute.setText(tr("计算这一帧（%d 个点）") % n_pts if n_pts else tr("计算这一帧"))
        can_prop = usable and fi is not None and bool(A.prompts_at(sp, fi))
        for w in (self.btn_back, self.btn_fwd, self.btn_all):
            w.setEnabled(can_prop)
            w.setToolTip(w.toolTip().split("\n")[0] + ("" if can_prop else "\n" + tr("先在这一帧点一下对象")))
        self.btn_obj_del.setEnabled(has_obj and len(sp["objects"]) > 1 and not running)
        self.btn_obj_add.setEnabled(not running and len(sp["objects"]) < A.MAX_OBJECTS)
        self.btn_clear_all.setEnabled(bool(sp["session"]) and not running)
        lows = A.low_frames(sp) if sp["session"] else []
        self.btn_low_prev.setVisible(bool(lows))
        self.btn_low_next.setVisible(bool(lows))
        if lows and fi is not None:
            self.btn_low_prev.setEnabled(A.neighbour_low_frame(sp, fi, -1) is not None)
            self.btn_low_next.setEnabled(A.neighbour_low_frame(sp, fi, 1) is not None)
        oid = self.current_object_id()
        obj = next((o for o in sp["objects"] if o["id"] == oid), None)
        self.obj_mode.setEnabled(obj is not None and not running)
        if obj is not None:
            self.obj_mode.blockSignals(True)
            self.obj_mode.setCurrentText(obj.get("mode", "add"))
            self.obj_mode.blockSignals(False)
        self.btn_detach.setEnabled(A.has_data({"ai_matte": sp}) and not running)
        self.model_combo.setEnabled(not running and not sp["session"])

    # ─────────────────────────── 命令 ───────────────────────────

    def _exec(self, name, extra: dict, *, coalesce: str | None = None) -> bool:
        host, params, _k = self._target()
        if host is None or params is None:
            return False
        payload = dict(params)
        payload.update(extra)
        from cutora.core.commands import get_bus
        import time
        now = time.monotonic()
        key = (id(host), name, coalesce) if coalesce else None
        if key is not None and name == "ai.matte.set":
            last = self._coalesce.get(key)
            if last is not None and now - last < 0.4:
                sp = A.spec(host)
                sp.update({k: v for k, v in extra.get("patch", {}).items() if k in ("feather", "shrink")})
                sp = A.normalize(sp)
                if isinstance(host, dict):
                    host["ai_matte"] = sp
                else:
                    host.ai_matte = sp
                self._coalesce[key] = now
                cap = getattr(self.window(), "capture_undo_after", None)
                if callable(cap):
                    cap()
                self._after_change()
                return True
        try:
            get_bus().execute(name, payload, source="ui")
        except Exception as exc:
            _LOG.exception("%s 失败", name)
            self._status_msg("%s：%s" % (name, exc))
            return False
        if key is not None:
            self._coalesce[key] = now
        self._after_change()
        return True

    def _status_msg(self, text: str, ms: int = 4000):
        bar = getattr(self.window(), "statusBar", None)
        if callable(bar):
            try:
                bar().showMessage(str(text), ms)
            except Exception:
                pass

    def _after_change(self, refresh_frame: bool = True):
        from cutora.fx.layer_fx import clear_cache
        clear_cache()
        sp = self.spec()
        if sp["session"]:
            S.invalidate(sp["session"])
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

    def _set(self, patch: dict, coalesce: str | None = None):
        if self._loading:
            return
        if self._exec("ai.matte.set", {"patch": patch}, coalesce=coalesce):
            if "enabled" in patch or "view" in patch:
                self.reload()

    def ensure_session(self) -> bool:
        sp = self.spec()
        if sp["session"]:
            return True
        ok = self._exec("ai.matte.session", {"op": "create", "model": self.model_combo.currentText()})
        if ok:
            self.reload()
        return ok

    # ---- 对象 ----
    def _on_obj_add(self):
        if not self.ensure_session():
            return
        if self._exec("ai.matte.object", {"op": "add"}):
            self.reload()
            self.obj_list.setCurrentRow(self.obj_list.count() - 1)

    def _on_obj_del(self):
        oid = self.current_object_id()
        if oid is None:
            return
        sp = self.spec()
        if self._exec("ai.matte.object", {"op": "remove", "object_id": oid}):
            if sp["session"]:
                try:
                    S.Session(sp["session"]).remove_object(oid)
                except Exception:
                    _LOG.exception("删对象缓存失败")
            self.reload()
            self._after_change()

    def _on_obj_item_changed(self, item):
        if self._loading:
            return
        oid = int(item.data(Qt.ItemDataRole.UserRole))
        patch = {"objects": [{"id": oid, "name": item.text().strip() or ("对象 %d" % oid),
                              "enabled": item.checkState() == Qt.CheckState.Checked}]}
        self._set(patch)

    def _on_obj_row_changed(self):
        self._sync_tool_state()

    def _on_obj_mode(self, mode):
        if self._loading:
            return
        oid = self.current_object_id()
        if oid is None or mode not in A.OBJECT_MODES:
            return
        self._set({"objects": [{"id": oid, "mode": str(mode)}]})
        self.reload()

    def _seek_timeline(self, t_sec: float) -> bool:
        win = self.window()
        ms = int(round(max(0.0, float(t_sec)) * 1000.0))
        for name in ("on_seek_request", "seek_to_ms"):
            fn = getattr(win, name, None)
            if callable(fn):
                try:
                    fn(ms)
                    return True
                except Exception:
                    _LOG.exception("跳转失败")
        try:
            win._timeline_ms = ms
        except Exception:
            pass
        return False

    def _jump_low(self, direction: int):
        sp = self.spec()
        fi = self.current_frame()
        if fi is None or not sp["session"]:
            return
        target = A.neighbour_low_frame(sp, fi, direction)
        if target is None:
            self._status_msg(tr("这个方向没有低置信度的帧了"))
            return
        host, _p, _k = self._target()
        idx = S.load_index(sp["session"]) or {}
        t = A.source_frame_to_timeline(self._layer_host(host), target, float(idx.get("fps") or 30.0))
        self._seek_timeline(t + 1e-4)
        self.lbl_info.setText(tr("第 %d 帧置信度低：点一两个点（Alt+点去掉多余的），再传播") % target)
        self.reload()

    def _on_model_changed(self, mid):
        if self._loading or not mid:
            return
        self._set({"model": str(mid)})
        self.reload()

    # ---- 提示 ----
    def _on_prompt_toggled(self, on):
        if self._loading:
            return
        if on and not self.ensure_session():
            self.btn_prompt.setChecked(False)
            return
        self.canvas_action.emit("matte_prompt", bool(on))

    def set_prompt_mode(self, on: bool):
        """画布退出模式时回调（Esc / 右键）。"""
        self.btn_prompt.blockSignals(True)
        self.btn_prompt.setChecked(bool(on))
        self.btn_prompt.blockSignals(False)

    def add_prompt_from_canvas(self, prompt: dict) -> bool:
        """画布点了一下 / 拖了个框：prompt 已是源帧归一化坐标（不含 obj）。

        Q166：在算的时候也收点（只是先记下，等这次算完再一起算）；传播在跑时不收。"""
        if self._running() and getattr(self._worker, "kind", "") != "segment":
            self._status_msg(tr("正在传播，等一下再点"))
            return False
        if not self.ensure_session():
            return False
        oid = self.current_object_id()
        if oid is None:
            sp = self.spec()
            oid = sp["objects"][0]["id"] if sp["objects"] else None
        if oid is None:
            return False
        fi = self.current_frame()
        if fi is None:
            self._status_msg(tr("这条片段没有可读的源视频"))
            return False
        p = dict(prompt)
        p["obj"] = int(oid)
        if not self._exec("ai.matte.prompt", {"op": "add", "frame": int(fi), "prompt": p}):
            return False
        self._dirty_frames.add(int(fi))
        if self.chk_auto.isChecked():
            self._request_segment(int(fi))
        else:
            self._sync_tool_state()
            n = len(A.prompts_at(self.spec(), fi))
            self.lbl_info.setText(tr("这一帧 %d 个点待计算 —— 点好了按「计算这一帧」或 Enter") % n)
        return True

    AUTO_DEFAULT = False

    def compute_now(self):
        """「计算这一帧」/ 画布 Enter：把当前帧上的全部点一次算出来。"""
        fi = self.current_frame()
        if fi is None:
            return False
        if not A.prompts_at(self.spec(), fi):
            self._status_msg(tr("这一帧还没有点"))
            return False
        self._request_segment(int(fi))
        return True

    def _request_segment(self, fi: int):
        """在跑 → 记下，跑完再算（用那时候的全部点）；没在跑 → 马上算。"""
        if self._running():
            self._pending_frame = int(fi)
            self.lbl_info.setText(tr("正在算…刚加的点会在算完后一起再算一次"))
            return
        self._pending_frame = None
        self._dirty_frames.discard(int(fi))
        self._start_segment(int(fi))

    def frame_dirty(self, fi) -> bool:
        return fi is not None and int(fi) in self._dirty_frames

    def set_show_tint(self, on: bool):
        self.show_tint = bool(on)
        if self.chk_tint.isChecked() != self.show_tint:
            self.chk_tint.blockSignals(True)
            self.chk_tint.setChecked(self.show_tint)
            self.chk_tint.blockSignals(False)
        self._after_view_change()

    def _after_view_change(self):
        win = self.window()
        c = getattr(win, "video_canvas", None)
        if c is not None:
            c.update()

    def _on_clear_frame(self):
        fi = self.current_frame()
        if fi is None:
            return
        oid = self.current_object_id()
        extra = {"op": "clear", "frame": int(fi)}
        if oid is not None:
            extra["object_id"] = int(oid)
        if self._exec("ai.matte.prompt", extra):
            sp = self.spec()
            if sp["session"]:
                try:
                    sess = S.Session(sp["session"])
                    if oid is None:
                        sess.remove_frames([fi])
                    else:
                        # 这一帧上该对象没提示了：把它的结果也去掉
                        import os as _os
                        try:
                            _os.remove(_os.path.join(sess.dir, "f%06d_o%d.png" % (fi, oid)))
                        except OSError:
                            pass
                        rec = sess.index["frames"].get(str(fi))
                        if rec and oid in (rec.get("objs") or []):
                            rec["objs"] = [o for o in rec["objs"] if o != oid]
                            (rec.get("scores") or {}).pop(str(oid), None)
                            if not rec["objs"]:
                                sess.index["frames"].pop(str(fi), None)
                            sess._dirty = True
                    sess.flush()
                except Exception:
                    _LOG.exception("清帧缓存失败")
            self.reload()
            self._after_change()

    # ---- 任务 ----
    def _running(self) -> bool:
        w = self._worker
        return w is not None and w.isRunning()

    def _job_common(self):
        ctx = self._ctx()
        host, _p, _k = self._target()
        sp = A.spec(host) if host is not None else None
        path = ctx.get("path")
        if not sp or not sp["session"] or not path or not os.path.isfile(str(path)):
            self._status_msg(tr("这条片段没有可读的源视频"))
            return None
        return sp, str(path)

    def _start_worker(self, kind, params):
        from cutora.ui.matte_worker import MatteWorker
        if MatteWorker.busy():
            self._status_msg(tr("已有 AI 抠像任务在跑"))
            return False
        from cutora.ui.gpu_notice import confirm as _gpu_ok
        if not _gpu_ok(self, "ai_matte"):           # Q193：没有 N 卡先说清楚
            return False
        w = MatteWorker(kind, params, parent=self)
        self._worker = w
        MatteWorker.current = w
        w.progress.connect(self._on_progress)
        w.done.connect(self._on_done)
        w.failed.connect(self._on_failed)
        w.queued.connect(self._on_queued)
        win = self.window()
        try:
            win.is_ai_running = True
        except Exception:
            pass
        self.prog.setValue(0)
        self.prog.setVisible(kind == "propagate")
        self.btn_cancel.setVisible(kind == "propagate")
        self.lbl_info.setText(tr("正在算这一帧…") if kind == "segment" else tr("正在传播…"))
        self._sync_tool_state()
        w.start()
        return True

    def _start_segment(self, frame_idx: int):
        got = self._job_common()
        if got is None:
            return
        sp, path = got
        self._start_worker("segment", {"spec": sp, "path": path, "frame": int(frame_idx), "model": sp["model"]})

    def _on_propagate(self, direction: str):
        got = self._job_common()
        if got is None:
            return
        sp, path = got
        fi = self.current_frame()
        if fi is None or not A.prompts_at(sp, fi):
            self._status_msg(tr("先在这一帧点一下对象，再传播"))
            return
        ctx = self._ctx()
        host, _p, _k = self._target()
        from cutora.fx.matte_jobs import clip_source_frame_range, source_fps
        idx = S.load_index(sp["session"]) or {}
        fps = float(idx.get("fps") or 0.0) or source_fps(path)
        a, b = clip_source_frame_range(self._layer_host(host), fps)
        _ = ctx
        passes = []
        if direction in ("backward", "all") and fi > a:
            passes.append(("backward", a))
        if direction in ("forward", "all") and fi < b:
            passes.append(("forward", b))
        if not passes:
            self._status_msg(tr("已经在片段的这一头了，没有可传播的帧"))
            return
        n_frames = sum(abs(int(end) - int(fi)) + 1 for _d, end in passes)
        est_txt = self._confirm_estimate(n_frames, sp["model"])
        if est_txt is None:
            return
        if self._start_worker("propagate", {"spec": sp, "path": path, "start": int(fi), "passes": passes,
                                            "model": sp["model"]}):
            if est_txt:
                self.lbl_info.setText(tr("正在传播 %d 帧…（预计约 %s）") % (n_frames, est_txt))

    def _confirm_estimate(self, n_frames: int, model: str):
        """传播前估时。CPU 上超过 1 分钟先问一句。返回估时文字（"" = 不显示），None = 用户取消。"""
        from cutora.fx.segment import backend_name
        if backend_name() == "fake":
            return ""
        try:
            from cutora.core.config import DEVICE
        except Exception:
            DEVICE = "cpu"
        from cutora.fx.matte_jobs import estimate_seconds, format_duration_zh
        secs, measured = estimate_seconds(n_frames, model, str(DEVICE))
        txt = format_duration_zh(secs) + ("" if measured else tr("，粗估"))
        if not str(DEVICE).startswith("cuda") and secs > 60:
            msg = tr("这台电脑没有可用的 NVIDIA 显卡，AI 抠像在 CPU 上跑。\n"
                     "传播 %d 帧大约要 %s。\n\n建议先只传一小段试试（或在「模型」里换 Base+）。继续吗？") % (n_frames, txt)
            if QMessageBox.question(self, tr("AI 抠像要跑一会儿"), msg,
                                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No) \
                    != QMessageBox.StandardButton.Yes:
                return None
        return txt

    def _on_queued(self, who):
        self.btn_cancel.setVisible(True)
        self.lbl_info.setText(tr("排队中：显卡正被「%s」使用，结束后自动开始（可取消）") % (who or tr("其他 AI 任务")))

    def _on_cancel(self):
        if self._running():
            self._worker.cancel()
            self.lbl_info.setText(tr("正在停止…"))

    def _on_progress(self, done, total, frame_idx, score):
        total = max(1, int(total))
        self.prog.setValue(int(1000 * min(1.0, done / float(total))))
        w = self._worker
        secs = 0.0
        if w is not None:
            import time
            secs = time.perf_counter() - (w.started_at or time.perf_counter())
        fps = done / secs if secs > 0.2 else 0.0
        eta = (total - done) / fps if fps > 0 else 0.0
        self.lbl_info.setText(tr("传播中 %d / %d 帧 · %.1f 帧/秒 · 还要约 %d 秒") % (done, total, fps, int(eta)))
        if done % 12 == 0:
            self._after_change(refresh_frame=True)

    def _on_done(self, result):
        self._finish_job()
        r = result or {}
        msg = ""
        if r.get("kind") == "segment":
            scores = r.get("scores") or {}
            if scores:
                low = min(scores.values())
                msg = tr("这一帧算好了（%.0f ms）") % (float(r.get("seconds", 0)) * 1000) \
                    + (tr("，置信度偏低，再点一两个点") if low < 0.5 else "")
            else:
                msg = tr("这一帧没有提示点")
        elif r.get("kind") == "propagate":
            n, secs = int(r.get("frames", 0)), float(r.get("seconds", 0.0))
            msg = tr("传播完成：%d 帧，%.1f 秒（%.1f 帧/秒）") % (n, secs, n / secs if secs > 0 else 0.0)
            if r.get("low"):
                msg += tr("；%d 帧置信度低（时间线上黄色），到那里补个点再传") % int(r["low"])
            if r.get("cancelled"):
                msg = tr("已取消，") + msg
            self._status_msg(msg, 6000)
        self.reload()
        self._after_change()
        if msg:
            self.lbl_info.setText(msg)
        pend = self._pending_frame
        if pend is not None:
            self._pending_frame = None
            self._request_segment(int(pend))

    def _on_failed(self, msg):
        self._finish_job()
        self._pending_frame = None
        self.lbl_info.setText(tr("失败：%s") % msg)
        self._status_msg(tr("AI 抠像失败：%s") % msg, 8000)
        self.reload()

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

    # ---- 下载 / 清除 ----
    def _on_download(self):
        try:
            from cutora.asr.models import get_model
            row = get_model(self.spec()["model"])
        except Exception:
            row = None
        if row is None:
            self._status_msg(tr("未登记的模型"))
            return
        from cutora.ui.model_download_dialog import ModelDownloadDialog
        dlg = ModelDownloadDialog(row, self.window())
        dlg.exec()
        self.reload()

    def _on_clear_all(self):
        sp = self.spec()
        if not sp["session"]:
            return
        if QMessageBox.question(self, tr("清除结果"), tr("删掉全部提示点和算好的结果？（对象和设置保留，可以重新点选）"),
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        if self._exec("ai.matte.session", {"op": "clear"}):
            self.reload()

    def _on_detach(self):
        if QMessageBox.question(self, tr("移除 AI 抠像"), tr("把 AI 抠像从这条片段上拿掉并删掉缓存？"),
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        self.set_prompt_mode(False)
        self.canvas_action.emit("matte_prompt", False)
        if self._exec("ai.matte.session", {"op": "detach"}):
            self.reload()

    def _on_inpaint_erase(self):
        """打开『✨ AI 工坊 → 去除人物/去话筒』并自动带入当前 SAM 2.1 点选会话。"""
        win = self.window()
        host = getattr(win, "room_host", None)
        if host is not None:
            host.enter("ai_studio", tab=1)
            dlg = host.widget("ai_studio")
            if dlg is not None and hasattr(dlg, "_start_remove_object"):
                dlg._start_remove_object()
