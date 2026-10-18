# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""连线导入对话框（Q154 第二代）：左边媒体库、右边外部片段一行一组，中间拉线。

画布在 `cutora/ui/link_group_graph.py`，分组逻辑在 `cutora/batch/link_groups.py`，
真正写时间线的在 `cutora/batch/link_import.py`。这个对话框**只保存连线关系**
（``self.link_plan``），不动时间线 —— 回到「批量加载」的视频分页点「插入到所选视频」
才按当时的循环 / 静音 / 分屏设置写进去，中间随时可以再打开来改。
"""
from __future__ import annotations

import os

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QSizePolicy, QSpinBox,
    QVBoxLayout,
)

from cutora.batch import link_groups as LG
from cutora.batch import link_import as biz
from cutora.ui.link_graph import material_tint
from cutora.ui.link_group_graph import GroupLinkView
from cutora.ui.theme.tokens import SPACE_2, SPACE_3, SPACE_4, SPACE_6

THUMB_AT = 1.0          # 取第 1 秒那一帧，避免开头黑场
THUMB_W, THUMB_H = 192, 108


class _ThumbBus(QObject):
    """缩略图是在后台线程算好的，必须靠信号回到主线程再碰界面。"""

    ready = Signal(str, str)     # path, thumb_file


class LinkImportDialog(QDialog):
    def __init__(self, tasks, parent=None, host=None, plan=None):
        super().__init__(parent)
        self._tasks = list(tasks or [])
        self._host = host if host is not None else parent
        self._entries: list[str] = []
        self._sources: list[str] = []
        self._thumb_for: dict[str, QPixmap] = {}
        self._bus = _ThumbBus(self)
        self._bus.ready.connect(self._on_thumb)
        self.link_plan = None
        self.setWindowTitle("连线导入 · 外部片段分组（预合成）后连到媒体库的视频")
        # 在主窗口里铺满客户区，并跟着窗口缩放。一次要连几十条视频，固定小卡片不够用。
        # 主窗口没起来时（测试 / 独立窗口）仍用下面这个尺寸。
        self.setProperty("cutora_sheet", "fill")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.resize(SPACE_6 * 46, SPACE_6 * 26)
        self._build()
        if isinstance(plan, dict) and plan.get("entries"):
            self._entries = list(plan.get("entries") or [])
            if plan.get("by_prefix"):
                self.graph._label_example = str(plan.get("prefix_example") or "")
            self._reload(rows=plan.get("rows"), links=plan.get("links"))
            if plan.get("cols"):
                self.sp_cols.blockSignals(True)
                self.sp_cols.setValue(int(plan["cols"]))
                self.sp_cols.blockSignals(False)
            self.ed_prefix.setText(str(plan.get("prefix_example") or ""))
            self.chk_prefix.setChecked(bool(plan.get("by_prefix")))
            self._sync_prefix_ui()
        else:
            self._reload()

    # ------------------------------------------------------------ 界面
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(SPACE_4, SPACE_4, SPACE_4, SPACE_4)
        root.setSpacing(SPACE_3)

        bar = QHBoxLayout()
        bar.setSpacing(SPACE_2)
        self.btn_add_dir = QPushButton("添加文件夹…")
        self.btn_add_files = QPushButton("添加视频…")
        self.btn_clear_src = QPushButton("清空素材")
        self.lbl_cols = QLabel("每组片段数")
        self.sp_cols = QSpinBox()
        self.sp_cols.setRange(1, 24)
        self.sp_cols.setValue(1)
        self.sp_cols.setToolTip(
            "按文件顺序几段一组：3 = 第 1、2、3 段一组，4、5、6 段一组……\n"
            "这只是快速排一下，之后随便拖：同行换位、拖到别的组、拖到最下面空白处另起一组。")
        self.chk_prefix = QCheckBox("按文件名前缀分组")
        self.chk_prefix.setToolTip(
            "勾上以后在右边输入一个例子，比如 video01：软件学它的规律（字母照抄、数字看位数），\n"
            "文件名开头对得上同一截的就是一组：video01_a / video01_b → 一组，video02_a / video02_b → 另一组。\n"
            "留空 = 自动按最后一个 _ - 空格 . 前面的部分分组。")
        self.ed_prefix = QLineEdit()
        self.ed_prefix.setPlaceholderText("输入一个例子，如 video01")
        self.ed_prefix.setToolTip("照着你的文件名打一个开头的例子。数字只看位数：video01 认 video00–video99；v1 认 v0–v9。")
        self.ed_prefix.setMaximumWidth(SPACE_6 * 6)
        self.lbl_prefix = QLabel("")
        self.lbl_prefix.setObjectName("hint")
        self.btn_size = QPushButton("节点：中")
        self.btn_size.setToolTip("大、中、小三档，缩略图一直有。Ctrl + 滚轮整体缩放。")
        self.btn_auto = QPushButton("自动连线")
        self.btn_auto.setToolTip("按上下顺序：第 1 条媒体连第 1 组，第 2 条连第 2 组……")
        self.btn_clear_links = QPushButton("清空连线")
        for b in (self.btn_clear_src, self.btn_size, self.btn_auto, self.btn_clear_links):
            b.setObjectName("btnSecondary")
        for b in (self.btn_add_dir, self.btn_add_files, self.btn_clear_src):
            bar.addWidget(b)
        bar.addSpacing(SPACE_4)
        bar.addWidget(self.lbl_cols)
        bar.addWidget(self.sp_cols)
        bar.addWidget(self.chk_prefix)
        bar.addWidget(self.ed_prefix)
        bar.addWidget(self.lbl_prefix)
        bar.addStretch(1)
        for b in (self.btn_size, self.btn_auto, self.btn_clear_links):
            bar.addWidget(b)
        root.addLayout(bar)

        heads = QHBoxLayout()
        heads.setSpacing(SPACE_2)
        left_h = QLabel("媒体库里的视频")
        right_h = QLabel("要加进来的外部片段 · 一行 = 一个预合成组（按行内顺序拼接）")
        for lab in (left_h, right_h):
            lab.setObjectName("sectionHeader")
        right_h.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        heads.addWidget(left_h, 1)
        heads.addWidget(right_h, 2)
        root.addLayout(heads)

        self.graph = GroupLinkView(self)
        self.graph.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.graph.setMinimumSize(0, 0)
        root.addWidget(self.graph, 1)

        tips = QLabel(
            "拖片段：同一行里换位 · 拖到别的行 = 加入那一组（插在松手的位置）· 拖到最下面空白处 = 另起一组 · 框选 / Ctrl / Shift 多选后一起拖　·　"
            "连线：从媒体卡片右边的点拖到一行上，或从行左边的点拖到媒体上；右键媒体 / 片段可以批量连　·　"
            "按住 Ctrl 划一刀割线 · 双击一条线 / 选中按 Delete 删掉　·　"
            "滚轮整页滚；左右两边各有一根细滚动条（或鼠标放在那一列上 Alt + 滚轮）只滚那一列，方便左右对照")
        tips.setObjectName("hint")
        tips.setWordWrap(True)
        root.addWidget(tips)

        foot = QHBoxLayout()
        foot.setSpacing(SPACE_2)
        self.lbl_status = QLabel("")
        self.lbl_status.setObjectName("hint")
        self.btn_cancel = QPushButton("取消")
        self.btn_cancel.setObjectName("btnSecondary")
        self.btn_run = QPushButton("保存连线")
        self.btn_run.setObjectName("btnPrimary")
        self.btn_run.setToolTip("只记住对应关系，不动时间线。回到「批量加载」的视频分页，点「插入到所选视频」才真正写进去。")
        foot.addWidget(self.lbl_status, 1)
        foot.addWidget(self.btn_cancel)
        foot.addWidget(self.btn_run)
        root.addLayout(foot)

        self.btn_add_dir.clicked.connect(self._add_dir)
        self.btn_add_files.clicked.connect(self._add_files)
        self.btn_clear_src.clicked.connect(self._clear_sources)
        self.sp_cols.valueChanged.connect(self._on_cols)
        self.chk_prefix.toggled.connect(self._on_prefix)
        self._prefix_timer = QTimer(self)
        self._prefix_timer.setSingleShot(True)
        self._prefix_timer.setInterval(300)
        self._prefix_timer.timeout.connect(self._apply_prefix_example)
        self.ed_prefix.textChanged.connect(lambda _t: self._prefix_timer.start())
        self.ed_prefix.returnPressed.connect(self._apply_prefix_example)
        self.ed_prefix.setVisible(False)
        self.lbl_prefix.setVisible(False)
        self.btn_size.clicked.connect(self._cycle_size)
        self.btn_auto.clicked.connect(self._auto)
        self.btn_clear_links.clicked.connect(self.graph.clear_links)
        self.graph.linksChanged.connect(self._refresh_status)
        self.graph.rowsChanged.connect(self._refresh_status)
        self.btn_cancel.clicked.connect(self.reject)
        self.btn_run.clicked.connect(self._save)

    # ------------------------------------------------------------ 数据
    def _task_rows(self) -> list[dict]:
        rows = []
        for i, task in enumerate(self._tasks):
            path = (task or {}).get("video") or (task or {}).get("path") or ""
            name = os.path.basename(path) or ("视频 %d" % (i + 1))
            rows.append({
                "title": "%02d  %s" % (i + 1, name),
                "subtitle": self._dur_text(task),
                "thumb": self._thumb(path),
                "key_path": path,
                "tint": material_tint(i + 6),
            })
        return rows

    def _source_rows(self) -> list[dict]:
        return [{"title": os.path.basename(p), "subtitle": os.path.basename(os.path.dirname(p)),
                 "thumb": self._thumb(p), "key_path": p} for p in self._sources]

    @staticmethod
    def _dur_text(task) -> str:
        try:
            dur = float((task or {}).get("duration") or (task or {}).get("trim_end_sec") or 0)
        except (TypeError, ValueError):
            dur = 0.0
        if dur <= 0:
            return ""
        m, s = divmod(int(round(dur)), 60)
        return "%d:%02d" % (m, s)

    def _thumb(self, path: str):
        """先看缓存；没有就丢给后台，回来再就地补上，绝不在这里等 ffmpeg。"""
        if not path:
            return None
        cached = self._thumb_for.get(path)
        if cached is not None:
            return cached
        from cutora.ui.timeline.thumbs import peek_clip_thumb, request_clip_thumb
        hit = peek_clip_thumb(path, THUMB_AT, THUMB_W, THUMB_H)
        if hit:
            pix = QPixmap(hit)
            if not pix.isNull():
                self._thumb_for[path] = pix
                return pix
        request_clip_thumb(
            path, THUMB_AT, THUMB_W, THUMB_H,
            on_done=lambda _key="", p=path: self._bus.ready.emit(
                p, peek_clip_thumb(p, THUMB_AT, THUMB_W, THUMB_H) or ""))
        return None

    def _on_thumb(self, path: str, thumb_file: str) -> None:
        if not thumb_file or path in self._thumb_for:
            return
        pix = QPixmap(thumb_file)
        if pix.isNull():
            return
        self._thumb_for[path] = pix
        self.graph.set_thumb(path, pix)

    def _reload(self, rows=None, links=None) -> None:
        """素材变了：按路径把分组和连线对回去（新加的段按当前每组片段数 / 前缀补上）。"""
        old_sources = list(self._sources)
        old_rows = self.graph.rows() if rows is None else [list(r) for r in rows]
        old_links = self.graph.links() if links is None else [tuple(x) for x in links]
        self._sources = biz.resolve_sources(self._entries)
        index_of = {p: i for i, p in enumerate(self._sources)}
        if rows is None and old_sources:
            # 按路径搬：老行里的段路径还在就留在原行；新来的段再按规则分
            new_rows = [[index_of[old_sources[s]] for s in row if 0 <= s < len(old_sources) and old_sources[s] in index_of]
                        for row in old_rows]
            placed = {s for row in new_rows for s in row}
            fresh = [i for i in range(len(self._sources)) if i not in placed]
            if fresh:
                if self.chk_prefix.isChecked():
                    extra = LG.rows_by_pattern([self._sources[i] for i in fresh], self.ed_prefix.text())
                    new_rows += [[fresh[j] for j in row] for row in extra]
                else:
                    cols = int(self.sp_cols.value())
                    new_rows += [[fresh[j] for j in row] for row in LG.rows_by_columns(len(fresh), cols)]
            new_rows = [r for r in new_rows if r]
            links_keep = LG.remap_links(old_links, old_rows, new_rows) if old_rows else []
        elif rows is not None:
            new_rows, links_keep = old_rows, old_links
        else:
            n = len(self._sources)
            if self.chk_prefix.isChecked():
                new_rows = LG.rows_by_pattern(self._sources, self.ed_prefix.text())
            else:
                new_rows = LG.rows_by_columns(n, int(self.sp_cols.value()))
            links_keep = []
        self.graph.set_items(self._task_rows(), self._source_rows(), rows=new_rows)
        self.graph.set_links(links_keep)
        self._refresh_status()

    # ------------------------------------------------------------ 动作
    def _seed_dir(self) -> str:
        for item in reversed(self._entries):
            return item if os.path.isdir(item) else os.path.dirname(item)
        return ""

    def _add_dir(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "选择要导入的视频文件夹", self._seed_dir())
        if not folder:
            return
        if not biz.list_videos(folder):
            QMessageBox.information(
                self, "这个文件夹里没有视频",
                "%s\n\n下一层里没有找到视频文件。（只看这一层，不往子文件夹里翻。）" % folder)
            return
        self._entries.append(folder)
        self._reload()

    def _add_files(self) -> None:
        pat = "视频 (%s);;所有文件 (*)" % " ".join("*" + e for e in biz.VIDEO_EXTS)
        files, _ = QFileDialog.getOpenFileNames(self, "选择要导入的视频", self._seed_dir(), pat)
        if files:
            self._entries.extend(files)
            self._reload()

    def _clear_sources(self) -> None:
        self._entries = []
        self._reload()

    def _on_cols(self, n: int) -> None:
        if self.chk_prefix.isChecked():
            self.chk_prefix.blockSignals(True)
            self.chk_prefix.setChecked(False)
            self.chk_prefix.blockSignals(False)
        self.graph.regroup_columns(int(n))

    def _on_prefix(self, on: bool) -> None:
        if on and not self.ed_prefix.text().strip():
            # 第一次勾上：照第一个文件名给个例子，用户看一眼就明白该怎么写
            self.ed_prefix.blockSignals(True)
            self.ed_prefix.setText(LG.suggest_example(self._sources))
            self.ed_prefix.blockSignals(False)
        self._sync_prefix_ui()
        if on:
            self._apply_prefix_example()
            self.ed_prefix.setFocus()
            self.ed_prefix.selectAll()
        else:
            self.graph.regroup_columns(int(self.sp_cols.value()))

    def _sync_prefix_ui(self) -> None:
        on = self.chk_prefix.isChecked()
        self.ed_prefix.setVisible(on)
        self.lbl_prefix.setVisible(on)
        if on:
            self.lbl_prefix.setText(LG.describe_grouping(self._sources, self.ed_prefix.text()))

    def _apply_prefix_example(self) -> None:
        if not self.chk_prefix.isChecked():
            return
        self._prefix_timer.stop()
        self.graph.regroup_prefix(self.ed_prefix.text())
        self._sync_prefix_ui()

    def _cycle_size(self) -> None:
        order = ("medium", "large", "small")
        labels = {"large": "节点：大", "medium": "节点：中", "small": "节点：小"}
        cur = self.graph.node_size()
        try:
            nxt = order[(order.index(cur) + 1) % len(order)]
        except ValueError:
            nxt = "medium"
        self.graph.set_node_size(nxt)
        self.btn_size.setText(labels[nxt])

    def _auto(self) -> None:
        n = self.graph.auto_link()
        if not n:
            QMessageBox.information(self, "没得连", "左边或右边是空的。先把要导入的视频加进来。")

    def _refresh_status(self) -> None:
        pairs = self.graph.links()
        rows = self.graph.rows()
        lefts = len({li for li, _ in pairs})
        multi = sum(1 for r in rows if len(r) > 1)
        self.btn_run.setEnabled(bool(pairs))
        if self.chk_prefix.isChecked():
            self.lbl_prefix.setText(LG.describe_grouping(self._sources, self.ed_prefix.text()))
        if not self._sources:
            self.lbl_status.setText("右边还没有素材。点「添加文件夹…」或「添加视频…」。")
        elif not pairs:
            self.lbl_status.setText(
                "共 %d 段外部片段，分成 %d 组（%d 组是多段预合成）。从卡片上拖一条线到对面即可；"
                "「自动连线」按上下顺序一一对应。" % (len(self._sources), len(rows), multi))
        else:
            self.lbl_status.setText(
                "%d 条连线 · 将给 %d 条媒体库视频新建 %d 条视频轨道（多段的组进时间线就是一个预合成）"
                % (len(pairs), lefts, len(pairs)))

    def current_plan(self) -> dict | None:
        pairs = self.graph.links()
        if not pairs:
            return None
        rows = self.graph.rows()
        plan = LG.plan_from_rows(pairs, self._tasks, self._sources, rows)
        colors = self.graph.row_colors()
        by_task = {}
        for li, ri in sorted(set(pairs)):
            by_task.setdefault(li, []).append(colors[ri] if ri < len(colors) else "")
        for row in plan:
            row["colors"] = by_task.get(row["task_index"], [])
        return {"entries": list(self._entries), "sources": list(self._sources), "rows": rows,
                "links": [list(p) for p in pairs], "cols": int(self.sp_cols.value()),
                "by_prefix": bool(self.chk_prefix.isChecked()), "prefix_example": self.ed_prefix.text().strip(),
                "plan": plan}

    def _save(self) -> None:
        plan = self.current_plan()
        if plan is None:
            return
        self.link_plan = plan
        self.accept()
