# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""配乐页的节点连线（升级版 · 与视频页连线导入同款 GroupLinkView 画布）。

左边是媒体库（勾选的）视频，带缩略图与时长；右边是外部音频卡片 / 编组，中间拉贝塞尔曲线连接。
支持：
- 添加文件夹 / 添加音频文件 / 清空素材
- 每组音频数、按文件名前缀自动分组、拖拽卡片换位 / 合并 / 拆组
- 端口拉线、右键批量连线、按住 Ctrl 划刀割线、双击连线或按 Delete 删除
- 「少重复连线」（优先未用过、最少重复）与「按顺序一对一」自动连线
- 双击任意音频卡片或点底部「试听」即时试听，点「应用到勾选」写入时间线。
"""
from __future__ import annotations

import hashlib
import os

from PySide6.QtCore import QObject, QRectF, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
)

from cutora.audio.bgm_plan import assign_least_repeat
from cutora.batch import link_groups as LG
from cutora.model.clip_groups import GROUP_COLORS
from cutora.ui.link_graph import _c, material_tint
from cutora.ui.link_group_graph import GroupLinkView
from cutora.ui.theme.tokens import FONT_LABEL, FONT_MICRO, RADIUS_SM, SPACE_2, SPACE_3, SPACE_4, SPACE_6

AUDIO_EXTS = (".mp3", ".wav", ".aac", ".m4a", ".flac", ".ogg", ".opus", ".wma")
THUMB_AT = 1.0
THUMB_W, THUMB_H = 192, 108


def list_audios(folder: str) -> list[str]:
    """扫描文件夹下一层的音频文件（自然序）。"""
    if not folder or not os.path.isdir(folder):
        return []
    out = []
    for name in os.listdir(folder):
        path = os.path.join(folder, name)
        if os.path.isfile(path) and os.path.splitext(name)[1].lower() in AUDIO_EXTS:
            out.append(path)
    out.sort(key=_natural_key)
    return out


def _natural_key(path: str):
    import re
    base = os.path.basename(path or "")
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", base)]


def resolve_audio_sources(entries) -> list[str]:
    """文件夹 / 单个音频文件混着给，展开并按首次出现去重。"""
    out, seen = [], set()

    def _add(path: str):
        key = os.path.normcase(os.path.abspath(path))
        if key in seen:
            return
        seen.add(key)
        out.append(path)

    for raw in entries or []:
        item = str(raw or "").strip().strip('"')
        if not item:
            continue
        if os.path.isdir(item):
            for a in list_audios(item):
                _add(a)
        elif os.path.isfile(item) and os.path.splitext(item)[1].lower() in AUDIO_EXTS:
            _add(item)
        elif not os.path.splitext(item)[1] or os.path.splitext(item)[1].lower() in AUDIO_EXTS:
            # 兼容测试里传入虚拟音频名（如 "a.mp3"）
            _add(item)
    return out


def _audio_waveform_pixmap(path: str, index: int, *, playing: bool = False) -> QPixmap:
    """为音频节点生成带声波纹理、音符徽标与格式标签的卡片缩略图。"""
    pix = QPixmap(THUMB_W, THUMB_H)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    try:
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(0, 0, THUMB_W, THUMB_H)
        col = QColor(GROUP_COLORS[int(index) % len(GROUP_COLORS)])
        bg_top = QColor(_c("bg1"))
        bg_bot = QColor(_c("bg2"))
        grad = QLinearGradient(0, 0, 0, THUMB_H)
        grad.setColorAt(0.0, bg_top)
        grad.setColorAt(1.0, bg_bot)
        p.fillRect(rect, QBrush(grad))

        tint = QColor(col)
        tint.setAlphaF(0.26 if playing else 0.14)
        p.fillRect(rect, tint)

        # 基于文件名的确定性声波柱
        digest = hashlib.md5(os.path.basename(path or str(index)).encode("utf-8", "ignore")).digest()
        n_bars = 28
        pad_x = 14.0
        usable_w = THUMB_W - pad_x * 2
        step = usable_w / n_bars
        bar_w = max(2.5, step * 0.58)
        mid_y = THUMB_H * 0.54
        max_half = THUMB_H * 0.34

        wave_col = QColor(_c("accent_hi")) if playing else QColor(col)
        wave_col.setAlphaF(0.92 if playing else 0.78)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(wave_col))
        for i in range(n_bars):
            b = digest[i % len(digest)]
            env = 0.35 + 0.65 * (1.0 - abs((i / max(1, n_bars - 1)) * 2.0 - 1.0) ** 1.4)
            ratio = (0.18 + (b / 255.0) * 0.82) * env
            bh = max(4.0, max_half * 2.0 * ratio)
            bx = pad_x + i * step + (step - bar_w) * 0.5
            by = mid_y - bh * 0.5
            p.drawRoundedRect(QRectF(bx, by, bar_w, bh), 1.5, 1.5)

        # 右上角后缀或试听状态徽标
        ext = os.path.splitext(path or "")[1].lstrip(".").upper() or "AUDIO"
        badge_text = "▶ 试听中" if playing else ("♪ " + ext)
        f = QFont(p.font())
        f.setPixelSize(FONT_MICRO if not playing else FONT_LABEL)
        f.setBold(True)
        p.setFont(f)
        fm = p.fontMetrics()
        bw = fm.horizontalAdvance(badge_text) + 12
        bh = fm.height() + 4
        brect = QRectF(THUMB_W - bw - 6, 6, bw, bh)
        bcol = QColor(_c("accent_hi")) if playing else QColor(_c("bg0"))
        bcol.setAlphaF(0.88 if playing else 0.72)
        p.setBrush(QBrush(bcol))
        p.drawRoundedRect(brect, RADIUS_SM, RADIUS_SM)
        p.setPen(_c("bg0") if playing else col)
        p.drawText(brect, Qt.AlignmentFlag.AlignCenter, badge_text)
    finally:
        p.end()
    return pix


class _ThumbBus(QObject):
    ready = Signal(str, str)


class AudioLinkDialog(QDialog):
    def __init__(self, videos, paths=None, parent=None, *, tasks=None, host=None, plan=None):
        super().__init__(parent)
        self._host = host if host is not None else getattr(parent, "_host", None)
        all_tasks = tasks if tasks is not None else (getattr(self._host, "tasks", None) or [])
        self._videos: list[tuple[int, str]] = []
        self._tasks_for_row: list[dict] = []
        for item in videos or []:
            if isinstance(item, (tuple, list)) and len(item) >= 2:
                idx, name = int(item[0]), str(item[1])
                t = all_tasks[idx] if 0 <= idx < len(all_tasks) and isinstance(all_tasks[idx], dict) else {"name": name}
                self._videos.append((idx, name))
                self._tasks_for_row.append(t)
            elif isinstance(item, dict):
                idx = len(self._videos)
                path = str(item.get("video") or item.get("path") or "")
                name = os.path.basename(path) or str(item.get("name") or ("#%02d" % (idx + 1)))
                self._videos.append((idx, name))
                self._tasks_for_row.append(item)

        initial_paths = [str(p) for p in (paths or []) if p]
        self._entries: list[str] = list(initial_paths)
        self._sources: list[str] = []
        self._paths: list[str] = []
        self._links: dict[int, str] = {}
        self._video_idx = self._videos[0][0] if self._videos else None
        self._thumb_for: dict[str, QPixmap] = {}
        self._bus = _ThumbBus(self)
        self._bus.ready.connect(self._on_thumb)
        self._mp = None
        self._ao = None
        self._playing = ""
        self.link_plan = None

        self.setWindowTitle("配乐节点连线 · 外部音频分组 / 连线到视频（双击音频试听）")
        self.setProperty("cutora_sheet", "fill")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.resize(SPACE_6 * 46, SPACE_6 * 26)
        self._build()

        if isinstance(plan, dict) and (plan.get("entries") or plan.get("sources")):
            self._entries = list(plan.get("entries") or plan.get("sources") or initial_paths)
            if plan.get("by_prefix"):
                self.graph._label_example = str(plan.get("prefix_example") or "")
            self._reload(rows=plan.get("rows"), links=plan.get("links"), auto_default=False)
            if plan.get("cols"):
                self.sp_cols.blockSignals(True)
                self.sp_cols.setValue(int(plan["cols"]))
                self.sp_cols.blockSignals(False)
            self.ed_prefix.setText(str(plan.get("prefix_example") or ""))
            self.chk_prefix.setChecked(bool(plan.get("by_prefix")))
            self._sync_prefix_ui()
        else:
            self._reload(auto_default=True)

    # ------------------------------------------------------------ 界面
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(SPACE_4, SPACE_4, SPACE_4, SPACE_4)
        root.setSpacing(SPACE_3)

        bar = QHBoxLayout()
        bar.setSpacing(SPACE_2)
        self.btn_add_dir = QPushButton("添加文件夹…")
        self.btn_add_files = QPushButton("添加音频…")
        self.btn_clear_src = QPushButton("清空素材")
        self.lbl_cols = QLabel("每组音频数")
        self.sp_cols = QSpinBox()
        self.sp_cols.setRange(1, 24)
        self.sp_cols.setValue(1)
        self.sp_cols.setToolTip(
            "按文件顺序几首一组：1 = 一首一行；>1 = 多首拼成一条配乐组按顺序播放。\n"
            "之后可随时拖动音频卡片：同行换位、拖到别的行合并、拖到底部空白处另起一组。"
        )
        self.chk_prefix = QCheckBox("按文件名前缀分组")
        self.chk_prefix.setToolTip(
            "勾上后输入文件名前缀例子（如 bgm01），按同前缀自动归成一组；留空则按最后一个分隔符分组。"
        )
        self.ed_prefix = QLineEdit()
        self.ed_prefix.setPlaceholderText("输入前缀例子，如 bgm01")
        self.ed_prefix.setMaximumWidth(SPACE_6 * 6)
        self.lbl_prefix = QLabel("")
        self.lbl_prefix.setObjectName("hint")
        self.btn_size = QPushButton("节点：中")
        self.btn_size.setToolTip("大、中、小三档切换；Ctrl + 滚轮无级缩放画布。")
        self.btn_least = QPushButton("少重复连线")
        self.btn_least.setToolTip("优先分配还没用过的配乐，其次分配用得最少的配乐。")
        self.btn_auto = QPushButton("按顺序连线")
        self.btn_auto.setToolTip("按上下顺序：第 1 条视频连第 1 组音频，第 2 条连第 2 组……")
        self.btn_clear_links = QPushButton("清空连线")
        for b in (self.btn_clear_src, self.btn_size, self.btn_least, self.btn_auto, self.btn_clear_links):
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
        for b in (self.btn_size, self.btn_least, self.btn_auto, self.btn_clear_links):
            bar.addWidget(b)
        root.addLayout(bar)

        heads = QHBoxLayout()
        heads.setSpacing(SPACE_2)
        left_h = QLabel("媒体库里的视频（勾选参与配乐）")
        right_h = QLabel("外部配乐音频 · 一行 = 一组配乐（双击音频卡片可直接试听）")
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

        # 兼容旧接口的隐藏列表（供旧调用方或单元测试读取）
        self.list_video = QListWidget(self)
        self.list_video.hide()
        self.list_audio = QListWidget(self)
        self.list_audio.hide()
        self.list_video.currentRowChanged.connect(self._on_video_row)
        self.list_audio.itemClicked.connect(self._on_audio_clicked)
        self.list_audio.itemDoubleClicked.connect(self._on_audio_play)

        tips = QLabel(
            "连线：从左边视频卡片右侧端口拖到右边音频行，或从音频行左侧端口拖到视频卡片；右键可批量连线　·　"
            "双击任意音频卡片即时试听 / 停止　·　按住 Ctrl 划一刀割断连线 · 双击连线或选中按 Delete 删除　·　"
            "短于视频时长的配乐会自动循环铺满并交叉淡化；同一条视频连多组配乐时会分层混音"
        )
        tips.setObjectName("hint")
        tips.setWordWrap(True)
        root.addWidget(tips)

        foot = QHBoxLayout()
        foot.setSpacing(SPACE_2)
        self.btn_play = QPushButton("试听")
        self.btn_play.setObjectName("btnSecondary")
        self.btn_play.setToolTip("试听当前选中的音频卡片（也可以直接在画布上双击任意音频卡片试听）")
        self.lbl_link = QLabel("")
        self.lbl_link.setObjectName("hint")
        self.lbl_status = self.lbl_link
        self.btn_cancel = QPushButton("取消")
        self.btn_cancel.setObjectName("btnSecondary")
        self.btn_apply = QPushButton("应用到勾选")
        self.btn_apply.setObjectName("btnPrimary")
        self.btn_run = self.btn_apply
        foot.addWidget(self.btn_play)
        foot.addWidget(self.lbl_link, 1)
        foot.addWidget(self.btn_cancel)
        foot.addWidget(self.btn_apply)
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
        self.btn_least.clicked.connect(self._auto_least_repeat)
        self.btn_auto.clicked.connect(self._auto)
        self.btn_clear_links.clicked.connect(self.graph.clear_links)
        self.btn_play.clicked.connect(self._play_selected)
        self.graph.linksChanged.connect(self._on_graph_changed)
        self.graph.rowsChanged.connect(self._on_graph_changed)
        self.graph.selectionChanged.connect(self._on_selection_changed)
        self.graph.cellDoubleClicked.connect(self._on_cell_double_clicked)
        self.btn_cancel.clicked.connect(self.reject)
        self.btn_apply.clicked.connect(self.accept)

    # ------------------------------------------------------------ 数据与缩略图
    def _task_rows(self) -> list[dict]:
        rows = []
        for pos, (task_idx, name) in enumerate(self._videos):
            task = self._tasks_for_row[pos] if pos < len(self._tasks_for_row) else {}
            path = str((task or {}).get("video") or (task or {}).get("path") or "")
            disp = name if name.startswith("#") else ("%02d  %s" % (task_idx + 1, name))
            rows.append({
                "title": disp,
                "subtitle": self._dur_text(task),
                "thumb": self._thumb(path),
                "key_path": path or ("video_%d" % task_idx),
                "tint": material_tint(pos + 6),
            })
        return rows

    def _source_rows(self) -> list[dict]:
        out = []
        for i, p in enumerate(self._sources):
            sub = os.path.basename(os.path.dirname(p)) or "配乐"
            if self._playing and os.path.normcase(self._playing) == os.path.normcase(p):
                sub = "▶ 正在试听 · " + sub
            out.append({
                "title": os.path.basename(p),
                "subtitle": sub,
                "thumb": _audio_waveform_pixmap(
                    p, i,
                    playing=bool(self._playing and os.path.normcase(self._playing) == os.path.normcase(p)),
                ),
                "key_path": p,
            })
        return out

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
        if not path or not os.path.isfile(path):
            return None
        cached = self._thumb_for.get(path)
        if cached is not None:
            return cached
        try:
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
                    p, peek_clip_thumb(p, THUMB_AT, THUMB_W, THUMB_H) or ""),
            )
        except Exception:
            pass
        return None

    def _on_thumb(self, path: str, thumb_file: str) -> None:
        if not thumb_file or path in self._thumb_for:
            return
        pix = QPixmap(thumb_file)
        if pix.isNull():
            return
        self._thumb_for[path] = pix
        self.graph.set_thumb(path, pix)

    def _reload(self, rows=None, links=None, *, auto_default: bool = False) -> None:
        old_sources = list(self._sources)
        old_rows = self.graph.rows() if rows is None else [list(r) for r in rows]
        old_links = self.graph.links() if links is None else [tuple(x) for x in links]
        self._sources = resolve_audio_sources(self._entries)
        self._paths = list(self._sources)
        index_of = {p: i for i, p in enumerate(self._sources)}

        if rows is None and old_sources:
            new_rows = [
                [index_of[old_sources[s]] for s in row if 0 <= s < len(old_sources) and old_sources[s] in index_of]
                for row in old_rows
            ]
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
        if auto_default and not links_keep and self._videos and new_rows:
            self._auto_least_repeat()
        else:
            self.graph.set_links(links_keep)
        self._on_graph_changed()

    # ------------------------------------------------------------ 动作
    def _seed_dir(self) -> str:
        for item in reversed(self._entries):
            return item if os.path.isdir(item) else os.path.dirname(item)
        return ""

    def _add_dir(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "选择要导入的配乐文件夹", self._seed_dir())
        if not folder:
            return
        if not list_audios(folder):
            QMessageBox.information(
                self, "这个文件夹里没有音频",
                "%s\n\n下一层里没有找到音频文件（支持 mp3 / wav / aac / m4a / flac / ogg）。" % folder,
            )
            return
        was_empty = not self._sources
        self._entries.append(folder)
        self._reload(auto_default=was_empty)

    def _add_files(self) -> None:
        pat = "音频 (%s);;所有文件 (*)" % " ".join("*" + e for e in AUDIO_EXTS)
        files, _ = QFileDialog.getOpenFileNames(self, "选择要导入的音频", self._seed_dir(), pat)
        if files:
            was_empty = not self._sources
            self._entries.extend(files)
            self._reload(auto_default=was_empty)

    def _clear_sources(self) -> None:
        self._stop()
        self._entries = []
        self._reload()

    def _on_cols(self, n: int) -> None:
        if self.chk_prefix.isChecked():
            self.chk_prefix.blockSignals(True)
            self.chk_prefix.setChecked(False)
            self.chk_prefix.blockSignals(False)
            self._sync_prefix_ui()
        self.graph.regroup_columns(int(n))

    def _on_prefix(self, on: bool) -> None:
        if on and not self.ed_prefix.text().strip():
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
        self.lbl_cols.setEnabled(not on)
        self.sp_cols.setEnabled(not on)

    def _apply_prefix_example(self) -> None:
        if not self.chk_prefix.isChecked():
            return
        ex = self.ed_prefix.text().strip()
        self.graph.regroup_prefix(ex)
        rows = self.graph.rows()
        multi = sum(1 for r in rows if len(r) > 1)
        if not self._sources:
            self.lbl_prefix.setText("")
        elif multi:
            self.lbl_prefix.setText("→ %d 组（%d 组含多段）" % (len(rows), multi))
        else:
            self.lbl_prefix.setText("→ %d 组（每段各一组，换个更短的前缀试试）" % len(rows))

    def _cycle_size(self) -> None:
        order = ("large", "medium", "small")
        labels = {"large": "大", "medium": "中", "small": "小"}
        cur = self.graph.node_size()
        nxt = order[(order.index(cur) + 1) % len(order)]
        self.graph.set_node_size(nxt)
        self.btn_size.setText("节点：%s" % labels[nxt])

    def _auto(self) -> None:
        n = self.graph.auto_link()
        if n == 0 and not self._sources:
            self.lbl_link.setText("还没有外部音频 —— 先点左上角「添加文件夹…」或「添加音频…」")

    def _auto_least_repeat(self) -> None:
        """按「未用过优先、其次最少重复」把左边每条视频连到右边的配乐组。"""
        rows = self.graph.rows()
        if not self._videos or not rows:
            if not self._sources:
                self.lbl_link.setText("还没有外部音频 —— 先点左上角「添加文件夹…」或「添加音频…」")
            return
        row_tokens = [str(ri) for ri in range(len(rows))]
        picked = assign_least_repeat(len(self._videos), row_tokens)
        pairs = [(li, int(tok)) for li, tok in enumerate(picked)]
        self.graph.set_links(pairs)

    # ------------------------------------------------------------ 同步与试听
    def _on_graph_changed(self) -> None:
        """把 GroupLinkView 的连线同步到 _links 和隐藏列表，刷新底部状态提示。"""
        rows = self.graph.rows()
        links = self.graph.links()
        by_left: dict[int, list[str]] = {}
        for li, ri in sorted(links):
            if 0 <= li < len(self._videos) and 0 <= ri < len(rows):
                for s in rows[ri]:
                    if 0 <= s < len(self._sources):
                        by_left.setdefault(li, []).append(self._sources[s])

        self._links = {}
        for li, (task_idx, _name) in enumerate(self._videos):
            paths = by_left.get(li) or []
            if paths:
                self._links[int(task_idx)] = paths[0]

        self._fill_audio()
        self._fill_videos()
        self._refresh_status()

    def _on_selection_changed(self) -> None:
        sel_media = self.graph.selected_media()
        if sel_media:
            li = sel_media[0]
            if 0 <= li < len(self._videos):
                self._video_idx = self._videos[li][0]
        self._refresh_status()

    def _refresh_status(self) -> None:
        links = self.graph.links()
        rows = self.graph.rows()
        touched = len({li for li, _ in links})
        multi = sum(1 for r in rows if len(r) > 1)
        self.btn_apply.setEnabled(bool(links))
        if not self._sources:
            self.lbl_link.setText("先在左上角点「添加文件夹…」或「添加音频…」，把配乐拉进来")
            return
        if not links:
            grp = "，已分 %d 组（%d 组含多段）" % (len(rows), multi) if multi else ("，共 %d 组" % len(rows))
            self.lbl_link.setText(
                "已加载 %d 首音频%s —— 从视频端口拖线到音频组，或点上方「少重复连线」"
                % (len(self._sources), grp)
            )
            return
        play_txt = ("  ·  正在试听：%s" % os.path.basename(self._playing)) if self._playing else ""
        self.lbl_link.setText(
            "已连 %d 条线 → %d / %d 条视频将写入配乐%s%s"
            % (
                len(links),
                touched,
                len(self._videos),
                ("（含 %d 个多段编组）" % multi) if multi else "",
                play_txt,
            )
        )

    def _on_cell_double_clicked(self, src_idx: int) -> None:
        if 0 <= int(src_idx) < len(self._sources):
            self._play(self._sources[int(src_idx)])

    def _fill_audio(self) -> None:
        self.list_audio.blockSignals(True)
        self.list_audio.clear()
        for path in self._sources:
            item = QListWidgetItem(os.path.basename(path))
            item.setData(Qt.ItemDataRole.UserRole, path)
            item.setToolTip(path)
            self.list_audio.addItem(item)
        if not self._sources:
            item = QListWidgetItem("这个文件夹里没有音频")
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            self.list_audio.addItem(item)
        self.list_audio.blockSignals(False)

    def _fill_videos(self) -> None:
        row = self.list_video.currentRow()
        self.list_video.blockSignals(True)
        self.list_video.clear()
        for idx, name in self._videos:
            linked = os.path.basename(self._links.get(idx) or "")
            text = "%s  →  %s" % (name, linked) if linked else name
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, idx)
            self.list_video.addItem(item)
        if 0 <= row < self.list_video.count():
            self.list_video.setCurrentRow(row)
        elif self.list_video.count():
            self.list_video.setCurrentRow(0)
        self.list_video.blockSignals(False)

    def _on_video_row(self, row: int) -> None:
        item = self.list_video.item(row) if row >= 0 else None
        self._video_idx = None if item is None else item.data(Qt.ItemDataRole.UserRole)

    def _on_audio_clicked(self, item) -> None:
        path = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        if not path or self._video_idx is None:
            return
        self._links[int(self._video_idx)] = str(path)
        # 同步到画布连线
        li = next((i for i, (v_idx, _n) in enumerate(self._videos) if v_idx == int(self._video_idx)), None)
        src_i = next((i for i, p in enumerate(self._sources) if p == str(path)), None)
        if li is not None and src_i is not None:
            hit = LG.row_of(self.graph.rows(), src_i)
            if hit is not None:
                ri = hit[0]
                kept = [(l_i, r_i) for l_i, r_i in self.graph.links() if l_i != li]
                kept.append((li, ri))
                self.graph.set_links(kept)
        self.btn_apply.setEnabled(bool(self._links))
        self._fill_videos()

    def _on_audio_play(self, item) -> None:
        path = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        if path:
            self._play(str(path))

    def _play_selected(self) -> None:
        if self._playing:
            self._play(self._playing)
            return
        sel_cells = self.graph.selected_cells()
        if sel_cells and 0 <= sel_cells[0] < len(self._sources):
            self._play(self._sources[sel_cells[0]])
            return
        sel_media = self.graph.selected_media()
        if sel_media and 0 <= sel_media[0] < len(self._videos):
            v_idx = self._videos[sel_media[0]][0]
            path = self._links.get(int(v_idx))
            if path:
                self._play(path)
                return
        item = self.list_audio.currentItem()
        if item is not None:
            self._on_audio_play(item)
            return
        if self._video_idx is not None and self._links.get(int(self._video_idx)):
            self._play(self._links[int(self._video_idx)])
        elif self._sources:
            self._play(self._sources[0])

    def _play(self, path: str) -> None:
        try:
            from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
        except Exception:
            self.lbl_link.setText("这台电脑放不了试听")
            return
        if self._mp is None:
            self._mp = QMediaPlayer(self)
            self._ao = QAudioOutput(self)
            self._mp.setAudioOutput(self._ao)
            self._mp.setVideoOutput(None)
        state = getattr(QMediaPlayer, "PlaybackState", None)
        playing = False
        if state is not None:
            playing = self._mp.playbackState() == state.PlayingState
        if playing and self._playing == path:
            self._mp.stop()
            old = self._playing
            self._playing = ""
            self.btn_play.setText("试听")
            self._refresh_wave_thumb(old)
            self._refresh_status()
            return
        prev = self._playing
        self._mp.setSource(QUrl.fromLocalFile(path))
        self._mp.play()
        self._playing = path
        self.btn_play.setText("停止")
        if prev and prev != path:
            self._refresh_wave_thumb(prev)
        self._refresh_wave_thumb(path)
        self._refresh_status()

    def _refresh_wave_thumb(self, path: str) -> None:
        for i, p in enumerate(self._sources):
            if os.path.normcase(p) == os.path.normcase(path):
                pix = _audio_waveform_pixmap(
                    p, i,
                    playing=bool(self._playing and os.path.normcase(self._playing) == os.path.normcase(p)),
                )
                self.graph.set_thumb(p, pix)

    # ------------------------------------------------------------ 导出结果
    def current_plan(self) -> dict:
        rows = self.graph.rows()
        links = self.graph.links()
        plan = []
        by_left: dict[int, list[int]] = {}
        for li, ri in sorted(links):
            if 0 <= li < len(self._videos) and 0 <= ri < len(rows):
                by_left.setdefault(li, []).append(ri)
        for li, r_idxs in sorted(by_left.items()):
            task_idx = self._videos[li][0]
            groups = [[self._sources[s] for s in rows[ri] if 0 <= s < len(self._sources)] for ri in r_idxs]
            groups = [g for g in groups if g]
            if groups:
                plan.append({
                    "task_index": int(task_idx),
                    "groups": groups,
                    "paths": [p for g in groups for p in g],
                })
        return {
            "entries": list(self._entries),
            "sources": list(self._sources),
            "rows": rows,
            "links": links,
            "cols": int(self.sp_cols.value()),
            "by_prefix": bool(self.chk_prefix.isChecked()),
            "prefix_example": self.ed_prefix.text().strip(),
            "plan": plan,
        }

    def mapping(self) -> dict:
        """返回 {task_idx: path_or_groups}。单首音频返回 str（100% 兼容旧调用），多首编组或多行返回 groups 列表。"""
        rows = self.graph.rows()
        links = self.graph.links()
        by_left: dict[int, list[list[str]]] = {}
        for li, ri in sorted(links):
            if 0 <= li < len(self._videos) and 0 <= ri < len(rows):
                g = [self._sources[s] for s in rows[ri] if 0 <= s < len(self._sources)]
                if g:
                    by_left.setdefault(li, []).append(g)
        out: dict = {}
        for li, groups in by_left.items():
            task_idx = int(self._videos[li][0])
            if len(groups) == 1 and len(groups[0]) == 1:
                out[task_idx] = groups[0][0]
            else:
                out[task_idx] = groups
        if not out and self._links:
            return {int(k): v for k, v in self._links.items() if v}
        return out

    def reject(self) -> None:
        self._stop()
        super().reject()

    def accept(self) -> None:
        self._stop()
        self.link_plan = self.current_plan()
        super().accept()

    def _stop(self) -> None:
        if self._mp is not None:
            try:
                self._mp.stop()
            except Exception:
                pass
        self._playing = ""
