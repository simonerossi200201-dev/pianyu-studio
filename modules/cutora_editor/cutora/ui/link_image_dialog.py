# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""图片节点连线：左边工程里的视频，右边图片，一张图可以连多条视频。

画布在 `cutora/ui/link_graph.py`（海报模式：每个节点一张清楚的缩略图，
下面一行文件名）。插入仍走 `cutora/batch/script_insert_image.py`，
时间、适配、轨道用批量加载里已经填好的。
"""
from __future__ import annotations

import os

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QCursor, QIcon, QImageReader, QPixmap
from PySide6.QtWidgets import (
    QDialog, QFileDialog, QHBoxLayout, QLabel, QMenu, QMessageBox,
    QPushButton, QSizePolicy, QVBoxLayout,
)

from cutora.batch import link_image as biz
from cutora.batch.batch_load import IMAGE_EXTS, VIDEO_EXTS
from cutora.ui.link_graph import (
    POSTER_THUMB_H, POSTER_THUMB_W, LinkGraphView, material_tint,
)
from cutora.ui.theme.tokens import SPACE_2, SPACE_3, SPACE_4, SPACE_6

_THUMB_AT = 1.0


class _ThumbBus(QObject):
    ready = Signal(str, str)


class LinkImageDialog(QDialog):
    def __init__(self, tasks, parent=None, host=None, folder="", extras=None,
                 checked=None, spec=None, plan=None):
        super().__init__(parent)
        self._tasks = list(tasks or [])
        self._host = host if host is not None else parent
        self._spec = spec if spec is not None else (lambda: {})
        self._checked = [int(i) for i in (checked or [])]
        self._restore_plan = [dict(row) for row in (plan or []) if isinstance(row, dict)]
        self.link_plan = []
        self._entries: list[str] = []
        self._sources: list[str] = []
        self._hidden: set[str] = set()
        self._parked: list[tuple[int, str]] = []
        self._thumb_for: dict[str, QPixmap] = {}
        self._pending: list[str] = []
        self._bus = _ThumbBus(self)
        self._bus.ready.connect(self._on_video_thumb)
        if folder and os.path.isdir(folder):
            self._entries.append(folder)
        for raw in extras or []:
            item = str(raw or "").strip().strip('"')
            if item and os.path.isfile(item):
                self._entries.append(item)
        self.setWindowTitle("节点连线 · 把一张图指定给多条视频")
        # 和视频分页的连线导入一样：铺满主窗口客户区，跟着窗口缩放。
        # 主窗口没起来时（测试 / 独立窗口）仍用下面这个尺寸。
        self.setProperty("cutora_sheet", "fill")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.resize(SPACE_6 * 46, SPACE_6 * 26)
        self._build()
        self._reload()
        self._apply_restore()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(SPACE_4, SPACE_4, SPACE_4, SPACE_4)
        root.setSpacing(SPACE_3)

        bar = QHBoxLayout()
        bar.setSpacing(SPACE_2)
        self.btn_add_dir = QPushButton("添加文件夹…")
        self.btn_add_files = QPushButton("添加图片…")
        self.btn_clear_src = QPushButton("清空图片")
        self.btn_hide = QPushButton("隐藏这张图")
        self.btn_show_hidden = QPushButton("显示已隐藏")
        self.btn_all = QPushButton("这张图连到全部视频")
        self.btn_checked = QPushButton("这张图连到勾选的视频")
        self.btn_auto = QPushButton("按顺序一对一")
        self.btn_size = QPushButton("节点：大")
        self.btn_size.setToolTip("大、中、小三档。图多的时候改小，看细节的时候改大。")
        self.btn_clear_links = QPushButton("清空连线")
        for b in (self.btn_clear_src, self.btn_hide, self.btn_show_hidden,
                  self.btn_size, self.btn_auto, self.btn_clear_links):
            b.setObjectName("btnSecondary")
        self.btn_all.setObjectName("btnPrimary")
        for b in (self.btn_add_dir, self.btn_add_files, self.btn_clear_src,
                  self.btn_hide, self.btn_show_hidden):
            bar.addWidget(b)
        bar.addStretch(1)
        for b in (self.btn_size, self.btn_all, self.btn_checked,
                  self.btn_auto, self.btn_clear_links):
            bar.addWidget(b)
        root.addLayout(bar)

        heads = QHBoxLayout()
        heads.setSpacing(SPACE_2)
        left_h = QLabel("工程里的视频")
        right_h = QLabel("要叠上去的图片")
        for lab in (left_h, right_h):
            lab.setObjectName("sectionHeader")
        right_h.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        heads.addWidget(left_h, 1)
        heads.addWidget(right_h, 1)
        root.addLayout(heads)

        self.graph = LinkGraphView(self)
        self.graph.set_poster(True)
        self.graph.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.graph.setMinimumSize(0, 0)
        root.addWidget(self.graph, 1)

        tips = QLabel(
            "左边的视频和右边的图各自往下翻，互不影响。"
            "点一张就选中它；按住 Shift 再点，连选中间这一段；在空白处拖出框，框选这一列。"
            "选好之后在节点上点右键，指定要连到的目标，可以一次连上多张。藏起来的图不会出现在菜单里。"
            "拖一条线也能连：离端口有一小段距离就会吸上去，拉出来和放上去都算。"
            "节点可以切大、中、小。"
            "双击一条线删掉它；按住 Ctrl 划过连线也能割断。"
            "每张图有自己的颜色，连线跟图走。"
            "不想看的图：选中后点「隐藏这张图」，或右键菜单里的「隐藏所选图片」。"
            "藏起来的图不会加进去；「显示已隐藏」拿回来时，原来的连线还在。"
            "「这张图连到全部视频」和「连到勾选的视频」会让那些视频只连这一张图。"
            "这里只记住哪张图对应哪条视频，并回到批量加载里预览。"
            "大小、位置和时间在主窗口里调，点「插入到所选视频」才进时间线。")
        tips.setObjectName("hint")
        tips.setWordWrap(True)
        root.addWidget(tips)

        foot = QHBoxLayout()
        foot.setSpacing(SPACE_2)
        self.lbl_status = QLabel("")
        self.lbl_status.setObjectName("hint")
        self.btn_cancel = QPushButton("取消")
        self.btn_cancel.setObjectName("btnSecondary")
        self.btn_run = QPushButton("保存对应关系")
        self.btn_run.setObjectName("btnPrimary")
        foot.addWidget(self.lbl_status, 1)
        foot.addWidget(self.btn_cancel)
        foot.addWidget(self.btn_run)
        root.addLayout(foot)

        self.btn_add_dir.clicked.connect(self._add_dir)
        self.btn_add_files.clicked.connect(self._add_files)
        self.btn_clear_src.clicked.connect(self._clear_sources)
        self.btn_hide.clicked.connect(self._hide_current)
        self.btn_show_hidden.clicked.connect(self._show_hidden)
        self.graph.nodeRightClicked.connect(self._on_node_right_click)
        self.btn_all.clicked.connect(self._link_all)
        self.btn_checked.clicked.connect(self._link_checked)
        self.btn_auto.clicked.connect(self._auto)
        self.btn_size.clicked.connect(self._cycle_size)
        self.btn_clear_links.clicked.connect(self.graph.clear_links)
        self.graph.linksChanged.connect(self._refresh_status)
        self.btn_cancel.clicked.connect(self.reject)
        self.btn_run.clicked.connect(self._run)

    def _task_rows(self) -> list[dict]:
        rows = []
        for i, task in enumerate(self._tasks):
            path = (task or {}).get("video") or (task or {}).get("path") or ""
            name = os.path.basename(path) or ("视频 %d" % (i + 1))
            dur = self._dur_text(task)
            tip = path or name
            if dur:
                tip = "%s\n%s" % (tip, dur)
            rows.append({
                "title": "%02d  %s" % (i + 1, name),
                "thumb": self._thumb_for.get(path),
                "key_path": path,
                "tooltip": tip,
            })
        return rows

    def _key(self, path: str) -> str:
        if not path:
            return ""
        try:
            return os.path.normcase(os.path.abspath(path))
        except Exception:
            return os.path.normcase(str(path))

    def _visible_sources(self) -> list[str]:
        return [p for p in self._sources if self._key(p) not in self._hidden]

    def _capture_links(self) -> list[tuple[int, str]]:
        """屏幕上的线，加上已经藏起来、还留着的线。按图片路径记，不按下标。"""
        out = []
        seen = set()
        for li, path in self.graph.link_key_paths():
            item = (int(li), self._key(path))
            if item[1] and item not in seen:
                seen.add(item)
                out.append(item)
        for item in self._parked:
            if item[1] and item not in seen:
                seen.add(item)
                out.append(item)
        return out

    def _source_rows(self) -> list[dict]:
        rows = []
        order = {self._key(path): i for i, path in enumerate(self._sources)}
        for path in self._visible_sources():
            rows.append({
                "title": os.path.basename(path),
                "thumb": self._thumb_for.get(path),
                "key_path": path,
                "tooltip": path,
                "tint": material_tint(order.get(self._key(path), 0)),
            })
        return rows

    @staticmethod
    def _dur_text(task) -> str:
        try:
            dur = float((task or {}).get("duration")
                        or (task or {}).get("trim_end_sec") or 0)
        except (TypeError, ValueError):
            dur = 0.0
        if dur <= 0:
            return ""
        m, s = divmod(int(round(dur)), 60)
        return "%d:%02d" % (m, s)

    def _reload(self, keep_links: bool = False) -> None:
        saved = self._capture_links() if keep_links else []
        chosen_key = ""
        if keep_links:
            ri = self.graph.chosen("right")
            if ri is not None:
                chosen_key = self._key(self.graph.node_key("right", ri))
        else:
            self._parked = []
        self._sources = biz.resolve_images(self._entries)
        alive = {self._key(p) for p in self._sources}
        self._hidden &= alive
        self.graph.set_items(self._task_rows(), self._source_rows())
        vis = {self._key(p): i for i, p in enumerate(self._visible_sources())}
        pairs = []
        parked = []
        seen_pairs = set()
        seen_parked = set()
        for li, key in saved:
            if key not in alive or not (0 <= li < len(self._tasks)):
                continue
            ri = vis.get(key)
            if ri is None:
                item = (li, key)
                if item not in seen_parked:
                    seen_parked.add(item)
                    parked.append(item)
                continue
            pair = (li, ri)
            if pair not in seen_pairs:
                seen_pairs.add(pair)
                pairs.append(pair)
        self._parked = parked
        if pairs:
            self.graph.set_links(pairs)
        if chosen_key and chosen_key in vis:
            self.graph.choose("right", vis[chosen_key])
        self._schedule_thumbs()
        self._refresh_status()

    def _schedule_thumbs(self) -> None:
        pending = []
        for task in self._tasks:
            path = (task or {}).get("video") or (task or {}).get("path") or ""
            if path and path not in self._thumb_for:
                pending.append(path)
        for path in self._visible_sources():
            if path not in self._thumb_for:
                pending.append(path)
        self._pending = pending
        QTimer.singleShot(0, self._pump_thumbs)

    def _pump_thumbs(self) -> None:
        batch = self._pending[:4]
        self._pending = self._pending[4:]
        for path in batch:
            self._load_thumb(path)
        if self._pending:
            QTimer.singleShot(0, self._pump_thumbs)

    def _load_thumb(self, path: str) -> None:
        if not path or path in self._thumb_for or not os.path.isfile(path):
            return
        ext = os.path.splitext(path)[1].lower()
        if ext in VIDEO_EXTS:
            self._request_video_thumb(path)
            return
        if ext not in IMAGE_EXTS:
            return
        reader = QImageReader(path)
        reader.setAutoTransform(True)
        sz = reader.size()
        if sz.isValid() and sz.width() > 0 and sz.height() > 0:
            sz.scale(int(POSTER_THUMB_W), int(POSTER_THUMB_H),
                     Qt.AspectRatioMode.KeepAspectRatio)
            if sz.width() > 0 and sz.height() > 0:
                reader.setScaledSize(sz)
        img = reader.read()
        if img is None or img.isNull():
            return
        pix = QPixmap.fromImage(img)
        if pix.isNull():
            return
        self._thumb_for[path] = pix
        self.graph.set_thumb(path, pix)

    def _request_video_thumb(self, path: str) -> None:
        from cutora.ui.timeline.thumbs import peek_clip_thumb, request_clip_thumb
        hit = peek_clip_thumb(path, _THUMB_AT, POSTER_THUMB_W, POSTER_THUMB_H)
        if hit:
            self._on_video_thumb(path, hit)
            return
        request_clip_thumb(
            path, _THUMB_AT, POSTER_THUMB_W, POSTER_THUMB_H,
            on_done=lambda _key="", p=path: self._bus.ready.emit(
                p, peek_clip_thumb(p, _THUMB_AT, POSTER_THUMB_W, POSTER_THUMB_H) or ""))

    def _on_video_thumb(self, path: str, thumb_file: str) -> None:
        if not thumb_file or path in self._thumb_for:
            return
        pix = QPixmap(thumb_file)
        if pix.isNull():
            return
        self._thumb_for[path] = pix
        self.graph.set_thumb(path, pix)

    def _seed_dir(self) -> str:
        for item in reversed(self._entries):
            return item if os.path.isdir(item) else os.path.dirname(item)
        return ""

    def _add_dir(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, "选择图片文件夹", self._seed_dir())
        if not folder:
            return
        if not biz.resolve_images([folder]):
            QMessageBox.information(
                self, "这个文件夹里没有图片",
                "%s\n\n下一层里没有找到图片。（只看这一层，不往子文件夹里翻。）"
                % folder)
            return
        self._entries.append(folder)
        self._reload(keep_links=True)

    def _add_files(self) -> None:
        pat = "图片 (%s);;所有文件 (*)" % " ".join("*" + e for e in IMAGE_EXTS)
        files, _ = QFileDialog.getOpenFileNames(
            self, "选择图片", self._seed_dir(), pat)
        if files:
            self._entries.extend(files)
            self._reload(keep_links=True)

    def _clear_sources(self) -> None:
        self._entries = []
        self._hidden.clear()
        self._parked = []
        self._reload()

    def _hide_index(self, ri: int) -> bool:
        """藏起右边这一张。它上面的连线先收着，显示回来时还在。"""
        shown = self._visible_sources()
        ri = int(ri)
        if not (0 <= ri < len(shown)):
            return False
        if ri + 1 < len(shown):
            nxt = self._key(shown[ri + 1])
        elif ri > 0:
            nxt = self._key(shown[ri - 1])
        else:
            nxt = ""
        self._hidden.add(self._key(shown[ri]))
        self._reload(keep_links=True)
        if nxt:
            for i, path in enumerate(self._visible_sources()):
                if self._key(path) == nxt:
                    self.graph.choose("right", i)
                    break
        return True

    def _hide_current(self) -> int:
        selected = self.graph.selected_indices("right")
        if len(selected) > 1:
            return self._hide_selected()
        shown = self._visible_sources()
        ri = self.graph.chosen("right")
        if ri is None:
            if len(shown) == 1:
                ri = 0
            else:
                QMessageBox.information(
                    self, "先点一张图",
                    "先点中不想看的那一张，再隐藏。"
                    "多选之后可以用右键菜单里的「隐藏所选图片」。")
                return 0
        return 1 if self._hide_index(ri) else 0

    def _hide_selected(self) -> int:
        """把右边选中的图一起藏起来。先记下路径再重排，下标会变。"""
        shown = self._visible_sources()
        indices = self.graph.selected_indices("right")
        if not indices:
            chosen = self.graph.chosen("right")
            if chosen is None:
                return 0
            indices = [chosen]
        keys = []
        for ri in indices:
            if 0 <= int(ri) < len(shown):
                key = self._key(shown[int(ri)])
                if key and key not in keys:
                    keys.append(key)
        if not keys:
            return 0
        hide_at = set(keys)
        nxt = ""
        last = max(int(i) for i in indices)
        for i, path in enumerate(shown):
            key = self._key(path)
            if key in hide_at:
                continue
            nxt = key
            if i > last:
                break
        for key in keys:
            self._hidden.add(key)
        self._reload(keep_links=True)
        if nxt:
            for i, path in enumerate(self._visible_sources()):
                if self._key(path) == nxt:
                    self.graph.choose("right", i)
                    break
        return len(keys)

    def target_entries(self, side: str) -> list[tuple[int, str]]:
        """右键菜单里能指定的目标。藏起来的图不在里面。"""
        side = "right" if side == "right" else "left"
        if side == "left":
            return [
                (i, os.path.basename(path) or path)
                for i, path in enumerate(self._visible_sources())
            ]
        rows = []
        for i, task in enumerate(self._tasks):
            path = (task or {}).get("video") or (task or {}).get("path") or ""
            name = os.path.basename(path) or ("视频 %d" % (i + 1))
            rows.append((i, "%02d  %s" % (i + 1, name)))
        return rows

    def _menu_thumb(self, side: str, index: int):
        if side == "left":
            shown = self._visible_sources()
            if 0 <= index < len(shown):
                return self._thumb_for.get(shown[index])
            return None
        if 0 <= index < len(self._tasks):
            task = self._tasks[index] or {}
            path = task.get("video") or task.get("path") or ""
            return self._thumb_for.get(path)
        return None

    def assign_selected_to(self, side: str, target_index: int) -> int:
        """把这一列当前选中的节点连到对面那一个。已有的连线留下。"""
        side = "right" if side == "right" else "left"
        selected = self.graph.selected_indices(side)
        if not selected:
            chosen = self.graph.chosen(side)
            if chosen is None:
                return 0
            selected = [chosen]
        target_index = int(target_index)
        if side == "left":
            pairs = [(li, target_index) for li in selected]
        else:
            pairs = [(target_index, ri) for ri in selected]
        return self.graph.add_links(pairs)

    def _popup_target_menu(self, side: str) -> None:
        side = "right" if side == "right" else "left"
        selected = self.graph.selected_indices(side)
        n = len(selected) or (1 if self.graph.chosen(side) is not None else 0)
        menu = QMenu(self)
        header = menu.addAction("把所选 %d 个连到…" % n)
        header.setEnabled(False)
        entries = self.target_entries(side)
        if not entries:
            empty = menu.addAction("没有可指定的目标")
            empty.setEnabled(False)
        for index, title in entries:
            action = menu.addAction(title)
            pix = self._menu_thumb(side, index)
            if pix is not None and not pix.isNull():
                icon = pix.scaled(
                    int(SPACE_6), int(SPACE_6),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation)
                action.setIcon(QIcon(icon))
            action.triggered.connect(
                lambda _checked=False, s=side, i=index: self.assign_selected_to(s, i))
        if side == "right":
            menu.addSeparator()
            hide = menu.addAction("隐藏所选图片")
            hide.triggered.connect(self._hide_selected)
        menu.exec(QCursor.pos())

    def _on_node_right_click(self, side: str, index: int) -> None:
        side = "right" if side == "right" else "left"
        index = int(index)
        if index not in self.graph.selected_indices(side):
            self.graph.choose(side, index)
        self._popup_target_menu(side)

    def _show_hidden(self) -> int:
        n = len(self._hidden)
        if not n:
            return 0
        self._hidden.clear()
        self._reload(keep_links=True)
        return n

    def _image_index(self):
        chosen = self.graph.chosen("right")
        if chosen is not None:
            return chosen
        if len(self._visible_sources()) == 1:
            return 0
        return None

    def _need_image(self) -> int | None:
        ri = self._image_index()
        if ri is None:
            QMessageBox.information(
                self, "先点一张图",
                "右边有多张图。先点中要指定的那一张，再连到视频上。")
            return None
        if not self._tasks:
            QMessageBox.information(self, "没有视频", "工程里还没有视频。")
            return None
        return ri

    def _link_all(self) -> int:
        ri = self._need_image()
        if ri is None:
            return 0
        self.graph.choose("right", ri)
        return self.graph.assign_right_to_lefts(ri, range(len(self._tasks)))

    def _link_checked(self) -> int:
        if not self._checked:
            QMessageBox.information(
                self, "没有勾选视频",
                "先在批量加载左边勾选要加这张图的视频，再回来点这个按钮。")
            return 0
        ri = self._need_image()
        if ri is None:
            return 0
        self.graph.choose("right", ri)
        return self.graph.assign_right_to_lefts(ri, self._checked)

    def _cycle_size(self) -> None:
        order = ("large", "medium", "small")
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
            QMessageBox.information(
                self, "没得连",
                "左边或右边是空的。先把图片加进来。")

    def _refresh_status(self) -> None:
        pairs = self.graph.links()
        videos = len({li for li, _ in pairs})
        images = len({ri for _, ri in pairs})
        visible = self._visible_sources()
        hidden_n = len(self._hidden)
        self.btn_run.setEnabled(bool(pairs))
        self.btn_show_hidden.setEnabled(hidden_n > 0)
        self.btn_show_hidden.setText(
            "显示已隐藏 (%d)" % hidden_n if hidden_n else "显示已隐藏")
        if not self._sources:
            self.lbl_status.setText("右边还没有图片。点「添加文件夹…」或「添加图片…」。")
        elif not visible:
            self.lbl_status.setText(
                "图片都藏起来了（%d 张）。点「显示已隐藏」再拿出来。" % hidden_n)
        elif not pairs:
            self.lbl_status.setText(
                "正在看 %d 张图、%d 条视频。点一张图再连，或框选后右键指定。%s"
                % (len(visible), len(self._tasks),
                   "已隐藏 %d 张。" % hidden_n if hidden_n else ""))
        else:
            self.lbl_status.setText(
                "%d 条连线 · %d 张图对应 %d 条视频%s。保存后回主窗口预览，再插入。"
                % (len(pairs), images, videos,
                   "  ·  已隐藏 %d 张" % hidden_n if hidden_n else ""))

    def _apply_restore(self) -> None:
        plan = self._restore_plan
        self._restore_plan = []
        if not plan:
            return
        vis = {self._key(path): i for i, path in enumerate(self._visible_sources())}
        pairs = []
        for row in plan:
            try:
                li = int(row.get("task_index", -1))
            except (TypeError, ValueError):
                continue
            if not (0 <= li < len(self._tasks)):
                continue
            for path in row.get("paths") or []:
                ri = vis.get(self._key(path))
                if ri is not None:
                    pairs.append((li, ri))
        if pairs:
            self.graph.set_links(pairs)

    def commit_plan(self) -> list:
        """只整理对应关系，不往时间线上放任何东西。"""
        plan = biz.plan_from_links(
            self.graph.links(), self._tasks, self._visible_sources())
        self.link_plan = plan
        return plan

    def _run(self) -> None:
        if not self.commit_plan():
            return
        self.accept()
