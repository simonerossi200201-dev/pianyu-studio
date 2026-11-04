# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""T27-b: asset library = builtin pack + user assets. Thumbnail grid + subcat filter + search."""
from __future__ import annotations

import copy
import math
import os
import sys

from PySide6.QtCore import QEvent, QPointF, QRectF, QSize, Qt, QThread, QUrl, Signal
from PySide6.QtGui import (
    QBrush, QColor, QDrag, QFont, QIcon, QImage, QLinearGradient,
    QMovie, QPainter, QPen, QPixmap,
)
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QProgressBar, QPushButton, QSizePolicy,
    QTabBar, QVBoxLayout, QWidget,
)

from cutora.core.log import get_logger
from cutora.ui.i18n import tr
from cutora.ui.icons import bind_icon, icon
from cutora.ui.theme.tokens import ERR, SPACE_2, SPACE_4
from cutora.ui.widgets.responsive import mark_explicit

_LOG = get_logger("cutora.ui.panels.asset_library")
_THUMB_PX = 72
_THUMB_CACHE: dict[tuple, QPixmap] = {}
_VIDEO_EXT = {".mov", ".mp4", ".mkv", ".webm", ".avi", ".m4v"}
_AUDIO_EXT = {".mp3", ".wav", ".flac", ".aac", ".ogg", ".m4a", ".wma"}
_GIF_EXT = {".gif"}
MSG_EMPTY = "把 PNG / GIF / MOV 拖进来，或点下面的按钮导入。"

CATEGORIES = (
    ("element", "元素"),
    ("frame", "边框"),
    ("lower_third", "字幕条"),
    ("sticker", "贴纸"),
    ("animated", "动图"),
    ("video", "视频"),
    ("audio", "音频"),
)

SUBCAT_MAP: dict[str, list[tuple[str, str]]] = {
    "element": [
        ("全部元素", ""),
        ("🔥 社交互动", "社交互动|互动号召"),
        ("👆 指引标注", "指引标注|方向指引"),
        ("🕊️ 宗教圣洁", "宗教圣洁|圣洁|十字架|光环"),
        ("💮 美学印章", "美学印章|徽章印章"),
        ("🎞️ 动图元素", "动图|动态GIF"),
    ],
    "frame": [
        ("全部边框", ""),
        ("🏛️ 西方古典", "西方古典"),
        ("🏮 东方国风", "东方国风"),
        ("🌿 花卉藤蔓", "花卉藤蔓"),
        ("✨ 材质质感", "材质质感"),
        ("📐 现代极简", "现代极简"),
        ("🎬 电影取景", "电影取景"),
        ("⚡ 赛博霓虹", "赛博霓虹"),
        ("🕊️ 宗教庆典", "宗教庆典"),
    ],
    "lower_third": [
        ("全部字幕条", ""),
        ("🏷️ 带图标/缩略图 (100)", "带图标|缩略图|icon"),
        ("🎨 色彩分区/通栏 (50)", "色彩分区|通栏|split|banner"),
        ("📐 线条几何出场 (40)", "线条几何|几何|line|geom"),
        ("🖼️ 图形画报肌理 (40)", "图形画报|肌理|graphic|poster"),
        ("🌏 东方/西方/多语种 (70)", "东方|西方|多语种|国风|古典|cultural"),
    ],
    "sticker": [
        ("全部贴纸", ""),
        ("✨ 星光粒子", "星光粒子|赛博故障|星光"),
        ("🌈 光效氛围", "光效氛围"),
        ("🎨 笔触拼贴", "笔触拼贴|复古手账"),
        ("🌸 花瓣意境", "花瓣意境|花瓣|自然"),
        ("🎞️ 动图贴纸", "动图|动态特效GIF"),
    ],
    "animated": [
        ("全部动图", ""),
        ("🔥 社交互动", "社交互动|互动号召"),
        ("👆 指引标注", "指引标注|方向指引"),
        ("🕊️ 宗教圣洁", "宗教圣洁|圣洁"),
        ("✨ 星光粒子", "星光粒子|赛博故障"),
        ("🌈 光效氛围", "光效氛围"),
        ("🌸 花瓣意境", "花瓣意境|复古手账"),
    ],
    "video": [
        ("全部视频", ""),
    ],
    "audio": [
        ("全部音频", ""),
        ("🎵 配乐 BGM", "配乐 BGM|内置BGM"),
        ("✨ 转场音效", "转场音效"),
        ("🌧️ 自然天气", "自然天气"),
        ("🌊 江河流水", "江河流水"),
        ("🐦 鸟鸣生灵", "鸟鸣生灵"),
        ("👏 掌声欢笑", "掌声欢笑"),
        ("🏙️ 城市火焰", "城市火焰"),
        ("🔔 拟音场景", "拟音场景"),
    ],
}

_IMPORT_FILTER = (
    "资产 (*.png *.jpg *.jpeg *.webp *.svg *.gif *.apng *.mov *.mp4 *.mkv "
    "*.webm *.mp3 *.wav *.flac *.aac *.ogg *.m4a);;所有文件 (*)"
)


def preview_audio_record(rec: dict) -> bool:
    """Non-blocking preview of an audio asset (SFX or BGM WAV/MP3)."""
    if not isinstance(rec, dict):
        return False
    try:
        from cutora.audio import sfx_social
        if rec.get("audio_kind") == "sfx" and rec.get("id"):
            if sfx_social.preview_sfx(str(rec["id"])):
                return True
    except Exception:
        pass
    path = rec.get("stored_path") or rec.get("source_path") or ""
    if not path or not os.path.isfile(path):
        return False
    abs_path = os.path.abspath(path)
    if sys.platform == "win32" and abs_path.lower().endswith(".wav"):
        try:
            import winsound
            flags = winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT
            winsound.PlaySound(abs_path, flags)
            return True
        except Exception:
            pass
    try:
        from cutora.audio import sfx_social
        from PySide6.QtMultimedia import QSoundEffect
        if getattr(sfx_social, "_preview_qeffect", None) is None:
            sfx_social._preview_qeffect = QSoundEffect()
        eff = sfx_social._preview_qeffect
        eff.stop()
        eff.setSource(QUrl.fromLocalFile(abs_path))
        eff.setVolume(0.85)
        eff.play()
        return True
    except Exception:
        pass
    return False


class _AssetGrid(QListWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setViewMode(QListWidget.ViewMode.IconMode)
        self.setIconSize(QSize(72, 72))
        self.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.setMovement(QListWidget.Movement.Static)
        self.setSpacing(8)
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
        self.setDefaultDropAction(Qt.DropAction.CopyAction)

    def mimeData(self, items):
        md = super().mimeData(items)
        urls = []
        for it in items:
            rec = it.data(Qt.ItemDataRole.UserRole) or {}
            p = rec.get("stored_path") or rec.get("source_path") or ""
            if p and os.path.isfile(p):
                urls.append(QUrl.fromLocalFile(p))
        if urls:
            md.setUrls(urls)
        return md

    def startDrag(self, supported):
        items = self.selectedItems()
        if not items:
            return
        drag = QDrag(self)
        drag.setMimeData(self.mimeData(items))
        ic = items[0].icon()
        if not ic.isNull():
            drag.setPixmap(ic.pixmap(self.iconSize()))
        drag.exec(Qt.DropAction.CopyAction)


class _ImportWorker(QThread):
    progress = Signal(int, int, str)
    item_ready = Signal(object)
    failed = Signal(str, str)
    finished_all = Signal()

    def __init__(self, paths, parent=None, host=None,
                 storage_mode="library", project_dir=None):
        super().__init__(parent)
        self.paths = list(paths)
        self.host = host
        self.storage_mode = storage_mode or "library"
        self.project_dir = project_dir
        self._cancel = False

    def run(self):
        from cutora.assets.user import import_user_asset
        n = len(self.paths)
        for i, path in enumerate(self.paths):
            if self._cancel:
                break
            try:
                rec = import_user_asset(
                    path, host=self.host,
                    storage_mode=self.storage_mode,
                    project_dir=self.project_dir)
                self.item_ready.emit(rec)
            except Exception as exc:
                _LOG.exception("import user asset failed: %s", path)
                self.failed.emit(path, str(exc))
            self.progress.emit(i + 1, n, os.path.basename(path))
        self.finished_all.emit()


class AssetLibraryPanel(QWidget):
    auto_border_layer_requested = Signal(dict)
    auto_border_video_requested = Signal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("assetLibrary")
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.setAcceptDrops(True)
        self._import_worker = None
        self._hover_item = None
        self._hover_movie = None
        self._pending_thumbs = 0
        self._active_cat_key = ""
        self._saved_subcat_by_cat: dict[str, str] = {}

        lay = QVBoxLayout(self)
        lay.setContentsMargins(SPACE_2, SPACE_2, SPACE_2, SPACE_2)
        top = QHBoxLayout()
        top.setSpacing(SPACE_2)
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("搜索资产"))
        top.addWidget(self.search, 1)
        self.btn_import = QPushButton(tr("导入"))
        self.btn_import.setObjectName("assetLibraryImport")
        self.btn_import.setProperty("noElide", True)
        mark_explicit(self.btn_import, "text")
        mark_explicit(self.btn_import, "sizePolicy")
        self.btn_import.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        bind_icon(self.btn_import, "upload", size=16, color_token="fg1")
        self.btn_import.clicked.connect(self._import_dialog)
        top.addWidget(self.btn_import, 0)
        self.storage_mode = QComboBox()
        self.storage_mode.setObjectName("assetLibraryStorage")
        self.storage_mode.addItem(tr("存到软件库（所有工程共用）"), "library")
        self.storage_mode.addItem(tr("随工程保存"), "project")
        self.storage_mode.setToolTip(tr("导入时把素材存到软件库，还是复制进当前工程文件夹"))
        top.addWidget(self.storage_mode, 0)
        lay.addLayout(top)

        self.tabs = QTabBar()
        self.tabs.setObjectName("assetLibraryTabs")
        self.tabs.setExpanding(False)
        self.tabs.setDrawBase(False)
        self.tabs.setUsesScrollButtons(True)
        for _key, label in CATEGORIES:
            self.tabs.addTab(tr(label))
        lay.addWidget(self.tabs)

        # Sub-category filter bar + context action buttons
        self.subcat_box = QWidget()
        self.subcat_box.setObjectName("assetLibrarySubcatBar")
        sub_lay = QHBoxLayout(self.subcat_box)
        sub_lay.setContentsMargins(0, 2, 0, 2)
        sub_lay.setSpacing(SPACE_2)

        self.subcat_bar = QComboBox()
        self.subcat_bar.setObjectName("assetLibrarySubcatCombo")
        self.subcat_bar.setToolTip(tr("按子分类筛选当前标签页的资产"))
        sub_lay.addWidget(self.subcat_bar, 1)

        self.btn_preview_audio = QPushButton(tr("▶ 试听选中音频"))
        self.btn_preview_audio.setObjectName("assetLibraryPreviewAudio")
        self.btn_preview_audio.setToolTip(tr("立即试听当前选中的配乐或音效"))
        self.btn_preview_audio.clicked.connect(self._on_preview_selected_audio)
        self.btn_preview_audio.hide()
        sub_lay.addWidget(self.btn_preview_audio, 0)

        self.btn_auto_border_layer = QPushButton(tr("▣ 给选中图层加自动边框"))
        self.btn_auto_border_layer.setObjectName("assetLibraryAutoBorderLayer")
        self.btn_auto_border_layer.setToolTip(tr("用当前选中的边框样式自动贴合画布上选中的图层/图片/文字框"))
        self.btn_auto_border_layer.clicked.connect(lambda: self._apply_auto_border("layer"))
        self.btn_auto_border_layer.hide()
        sub_lay.addWidget(self.btn_auto_border_layer, 0)

        self.btn_auto_border_video = QPushButton(tr("□ 给整个视频加自动边框"))
        self.btn_auto_border_video.setObjectName("assetLibraryAutoBorderVideo")
        self.btn_auto_border_video.setToolTip(tr("用当前选中的边框样式为整个视频画面添加全屏装饰边框"))
        self.btn_auto_border_video.clicked.connect(lambda: self._apply_auto_border("canvas"))
        self.btn_auto_border_video.hide()
        sub_lay.addWidget(self.btn_auto_border_video, 0)

        lay.addWidget(self.subcat_box)

        self.progress = QProgressBar()
        self.progress.setObjectName("assetImportProgress")
        self.progress.setTextVisible(False)
        self.progress.hide()
        lay.addWidget(self.progress)
        self.list = _AssetGrid()
        self.list.setObjectName("assetLibraryGrid")
        self.list.setMouseTracking(True)
        self.list.viewport().setMouseTracking(True)
        self.list.viewport().installEventFilter(self)
        lay.addWidget(self.list, 1)
        self.empty_box = QWidget()
        self.empty_box.setObjectName("assetLibraryEmpty")
        empty_l = QVBoxLayout(self.empty_box)
        empty_l.setContentsMargins(SPACE_2, SPACE_4, SPACE_2, SPACE_2)
        self.empty_icon = QLabel()
        self.empty_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_icon.setPixmap(icon("image", size=32).pixmap(32, 32))
        self.empty = QLabel(tr(MSG_EMPTY))
        self.empty.setObjectName("hint")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.setWordWrap(True)
        self.btn_empty_import = QPushButton(tr("导入素材"))
        self.btn_empty_import.setObjectName("assetLibraryEmptyImport")
        self.btn_empty_import.setProperty("noElide", True)
        mark_explicit(self.btn_empty_import, "text")
        mark_explicit(self.btn_empty_import, "sizePolicy")
        self.btn_empty_import.setSizePolicy(
            QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        bind_icon(self.btn_empty_import, "upload", size=16)
        self.btn_empty_import.clicked.connect(self._import_dialog)
        empty_l.addWidget(self.empty_icon)
        empty_l.addWidget(self.empty)
        empty_l.addWidget(self.btn_empty_import, 0, Qt.AlignmentFlag.AlignHCenter)
        empty_l.addStretch(1)
        lay.addWidget(self.empty_box)
        self.tabs.currentChanged.connect(self._on_cat)
        self.subcat_bar.currentIndexChanged.connect(self._on_subcat_changed)
        self.search.textChanged.connect(lambda _t: self._refresh_grid())
        self.list.itemClicked.connect(self._on_item_clicked)
        self.list.itemDoubleClicked.connect(self._on_item_activated)
        self._on_cat(0)

    def sizeHint(self):
        return QSize(280, 480)

    def _category_key(self, idx: int) -> str:
        if 0 <= idx < len(CATEGORIES):
            return CATEGORIES[idx][0]
        return ""

    def _select_category(self, key: str) -> None:
        for i, (k, _label) in enumerate(CATEGORIES):
            if k == key:
                if self.tabs.currentIndex() != i:
                    self.tabs.setCurrentIndex(i)
                else:
                    self._on_cat(i)
                return
        self._on_cat(self.tabs.currentIndex())

    def _apply_empty(self, n: int) -> None:
        empty = n == 0
        self.empty_box.setVisible(empty)
        self.list.setVisible(not empty)

    def _project_dir(self) -> str | None:
        host = self.window()
        path = getattr(host, "project_path", None) if host is not None else None
        if path and os.path.isfile(path):
            return os.path.dirname(os.path.abspath(path))
        if path and os.path.isdir(path):
            return os.path.abspath(path)
        return None

    def _storage_mode(self) -> str:
        box = getattr(self, "storage_mode", None)
        if box is None:
            return "library"
        data = box.currentData()
        return str(data or "library")

    def _populate_subcat_bar(self, cat_key: str) -> None:
        choices = SUBCAT_MAP.get(cat_key, [("全部", "")])
        prev_val = self._saved_subcat_by_cat.get(cat_key, "")
        self.subcat_bar.blockSignals(True)
        try:
            self.subcat_bar.clear()
            target_idx = 0
            for idx_c, (label, val) in enumerate(choices):
                self.subcat_bar.addItem(tr(label), val)
                if val == prev_val:
                    target_idx = idx_c
            self.subcat_bar.setCurrentIndex(target_idx)
        finally:
            self.subcat_bar.blockSignals(False)
        self.btn_preview_audio.setVisible(cat_key == "audio")
        self.btn_auto_border_layer.setVisible(cat_key == "frame")
        self.btn_auto_border_video.setVisible(cat_key == "frame")
        self.subcat_box.setVisible(len(choices) > 1 or cat_key in ("audio", "frame"))

    def _on_subcat_changed(self, _idx: int) -> None:
        cat_key = self._category_key(self.tabs.currentIndex())
        if cat_key:
            self._saved_subcat_by_cat[cat_key] = str(self.subcat_bar.currentData() or "")
        self._refresh_grid()

    def _switch_cat(self, cat_key: str) -> None:
        for i, (k, _) in enumerate(CATEGORIES):
            if k == cat_key:
                self.tabs.setCurrentIndex(i)
                return

    def _on_cat(self, idx: int) -> None:
        key = self._category_key(idx)
        if key != self._active_cat_key:
            self._active_cat_key = key
            self._populate_subcat_bar(key)
        self._refresh_grid()

    def _refresh_grid(self) -> None:
        self._stop_hover_movie()
        self.list.clear()
        idx = self.tabs.currentIndex()
        key = self._category_key(idx)
        q = (self.search.text() or "").strip()
        subcat = str(self.subcat_bar.currentData() or "") if hasattr(self, "subcat_bar") else ""
        from cutora.assets.library import items_for_category
        items = items_for_category(key, q, project_dir=self._project_dir(), subcat=subcat)
        n = 0
        for it in items:
            item = QListWidgetItem(str(it.get("name") or it.get("id")))
            item.setData(Qt.ItemDataRole.UserRole, it)
            pix = thumb_for(it)
            if pix is None or pix.isNull():
                pix = _placeholder_thumb(it)
            item.setIcon(QIcon(_decorate_thumb(pix, it)))
            if it.get("storage_mode") == "project":
                item.setText(str(it.get("name") or it.get("id")) + " ·工程")
            if it.get("missing"):
                item.setForeground(QColor(ERR))
                item.setToolTip(tr("源文件找不到：%s") % (it.get("source_path") or it.get("stored_path") or ""))
            elif it.get("storage_mode") == "project":
                item.setToolTip(tr("随工程保存：%s") % (it.get("stored_path") or ""))
            elif it.get("referenced"):
                item.setToolTip(tr("引用外部文件：%s") % (it.get("source_path") or ""))
            elif it.get("type") == "audio":
                dur = float(it.get("duration") or 0.0)
                sub_lbl = str(it.get("subcat") or it.get("group") or "音频")
                item.setToolTip(f"{it.get('name')} ({sub_lbl} · {dur:.1f}s) — 单击试听，双击加入时间线")
            self.list.addItem(item)
            n += 1
            self._queue_video_thumb(it, item)
        self._apply_empty(n)

    def _on_item_clicked(self, item: QListWidgetItem) -> None:
        if item is None:
            return
        rec = item.data(Qt.ItemDataRole.UserRole) or {}
        if rec.get("type") == "audio":
            preview_audio_record(rec)

    def _on_preview_selected_audio(self) -> None:
        items = self.list.selectedItems()
        if not items and self.list.count() > 0:
            items = [self.list.item(0)]
        if not items:
            return
        rec = items[0].data(Qt.ItemDataRole.UserRole) or {}
        preview_audio_record(rec)

    def _selected_or_default_frame_item(self) -> dict:
        items = self.list.selectedItems()
        if items:
            rec = items[0].data(Qt.ItemDataRole.UserRole)
            if isinstance(rec, dict):
                return copy.deepcopy(rec)
        if self.list.count() > 0:
            rec = self.list.item(0).data(Qt.ItemDataRole.UserRole)
            if isinstance(rec, dict):
                return copy.deepcopy(rec)
        return {
            "id": "frame_auto_gold",
            "name": "皇家流金自动边框",
            "type": "frame",
            "border_spec": {
                "style": "vintage_filigree",
                "category": "西方古典",
                "thickness": 12,
                "length_ratio": 1.0,
                "color_mode": "gold_gradient",
                "primary_color": "#FFD700",
                "secondary_color": "#B8860B",
                "corner_ornament": True,
                "glow": True,
            },
        }

    def _apply_auto_border(self, target_mode: str = "canvas") -> None:
        rec = self._selected_or_default_frame_item()
        spec = copy.deepcopy(rec.get("border_spec") or {
            "style": "vintage_filigree",
            "category": rec.get("subcat") or "西方古典",
            "thickness": 12,
            "length_ratio": 1.0,
            "color_mode": "gold_gradient",
            "primary_color": "#FFD700",
            "secondary_color": "#B8860B",
            "corner_ornament": True,
            "glow": True,
        })
        host = self.window()
        tasks = getattr(host, "tasks", None) or []
        curr_idx = int(getattr(host, "curr_idx", -1) or -1)
        if 0 <= curr_idx < len(tasks):
            task = tasks[curr_idx]
            tl = task.get("timeline") if isinstance(task, dict) else None
            tw = int(getattr(tl, "width", 1080) or 1080) if tl is not None else 1080
            th = int(getattr(tl, "height", 1920) or 1920) if tl is not None else 1920
            if target_mode == "layer":
                canvas = getattr(host, "video_canvas", None)
                sel_ov = getattr(canvas, "selected_overlay", None) if canvas is not None else None
                if isinstance(sel_ov, dict) and not sel_ov.get("border_spec"):
                    spec["x"] = int(sel_ov.get("x", 0))
                    spec["y"] = int(sel_ov.get("y", 0))
                    spec["w"] = int(sel_ov.get("w", tw))
                    spec["h"] = int(sel_ov.get("h", th))
                    if sel_ov.get("id"):
                        spec["target_link"] = str(sel_ov["id"])
                    if "start" in sel_ov and "end" in sel_ov:
                        rec["duration"] = max(0.2, float(sel_ov["end"]) - float(sel_ov["start"]))
                else:
                    margin_x = int(tw * 0.06)
                    margin_y = int(th * 0.06)
                    spec["x"] = margin_x
                    spec["y"] = margin_y
                    spec["w"] = tw - margin_x * 2
                    spec["h"] = th - margin_y * 2
                    spec["target_link"] = "selected_layer"
            else:
                spec["x"] = 0
                spec["y"] = 0
                spec["w"] = tw
                spec["h"] = th
                spec["target_link"] = "canvas"
        rec["type"] = "frame"
        rec["border_spec"] = spec
        if target_mode == "layer":
            self.auto_border_layer_requested.emit(rec)
        else:
            self.auto_border_video_requested.emit(rec)
        place = getattr(host, "place_asset_on_timeline", None) if host is not None else None
        if callable(place):
            place(rec)

    def _queue_video_thumb(self, rec: dict, item: QListWidgetItem) -> None:
        path = rec.get("stored_path") or rec.get("source_path") or ""
        if rec.get("missing") or not path or not os.path.isfile(path):
            return
        ext = os.path.splitext(path)[1].lower()
        if ext not in _VIDEO_EXT:
            return
        from cutora.ui.timeline.thumbs import peek_clip_thumb, request_clip_thumb
        if peek_clip_thumb(path, 0.0, _THUMB_PX, _THUMB_PX):
            pix = thumb_for(rec, force=True)
            if pix is not None and not pix.isNull():
                item.setIcon(QIcon(_decorate_thumb(pix, rec)))
            return
        self._pending_thumbs += 1
        asset_id = rec.get("id")

        def _done(_k="", _id=asset_id, _path=path):
            self._pending_thumbs = max(0, self._pending_thumbs - 1)
            for i in range(self.list.count()):
                it = self.list.item(i)
                got = it.data(Qt.ItemDataRole.UserRole) or {}
                if got.get("id") != _id and (got.get("stored_path") or got.get("source_path")) != _path:
                    continue
                pix = thumb_for(got, force=True)
                if pix is not None and not pix.isNull():
                    it.setIcon(QIcon(_decorate_thumb(pix, got)))
                break

        request_clip_thumb(path, 0.0, _THUMB_PX, _THUMB_PX, on_done=_done)

    def _on_item_activated(self, item: QListWidgetItem) -> None:
        rec = item.data(Qt.ItemDataRole.UserRole) or {}
        if rec.get("missing"):
            host = self.window()
            bar = getattr(host, "statusBar", None)
            if callable(bar):
                try:
                    bar().showMessage("源文件找不到，无法放到时间线", 5000)
                except Exception:
                    _LOG.exception("statusBar failed")
            return
        host = self.window()
        place = getattr(host, "place_asset_on_timeline", None)
        if callable(place):
            try:
                place(rec)
            except Exception:
                _LOG.exception("place asset failed")
                bar = getattr(host, "statusBar", None)
                if callable(bar):
                    try:
                        bar().showMessage("无法把资产放到时间线", 5000)
                    except Exception:
                        _LOG.exception("statusBar failed after place")

    def eventFilter(self, obj, event):
        if obj is self.list.viewport():
            et = event.type()
            if et == QEvent.Type.Leave:
                self._stop_hover_movie()
            elif et == QEvent.Type.MouseMove:
                hit = self.list.itemAt(event.position().toPoint() if hasattr(event, "position") else event.pos())
                if hit is not self._hover_item:
                    self._set_hover_item(hit)
        return super().eventFilter(obj, event)

    def _set_hover_item(self, item):
        self._stop_hover_movie()
        self._hover_item = item
        if item is None:
            return
        rec = item.data(Qt.ItemDataRole.UserRole) or {}
        path = rec.get("stored_path") or rec.get("source_path") or ""
        ext = os.path.splitext(path)[1].lower()
        if ext not in _GIF_EXT or not os.path.isfile(path):
            return
        movie = QMovie(path)
        if not movie.isValid():
            return
        movie.setScaledSize(QSize(_THUMB_PX, _THUMB_PX))

        def _frame(_n, _item=item, _rec=rec, _movie=movie):
            if _item is not self._hover_item:
                return
            fr = _movie.currentPixmap()
            if fr.isNull():
                return
            _item.setIcon(QIcon(_decorate_thumb(fr, _rec)))

        movie.frameChanged.connect(_frame)
        self._hover_movie = movie
        movie.start()

    def _stop_hover_movie(self):
        movie = self._hover_movie
        self._hover_movie = None
        item = self._hover_item
        self._hover_item = None
        if movie is not None:
            movie.stop()
            movie.deleteLater()
        if item is not None and item.listWidget() is self.list:
            rec = item.data(Qt.ItemDataRole.UserRole) or {}
            pix = thumb_for(rec)
            if pix is None or pix.isNull():
                pix = _placeholder_thumb(rec)
            item.setIcon(QIcon(_decorate_thumb(pix, rec)))

    def mouseDoubleClickEvent(self, event):
        if self.list.count() == 0:
            self._import_dialog()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def _import_dialog(self):
        paths, _ok = QFileDialog.getOpenFileNames(self, tr("导入资产"), "", _IMPORT_FILTER)
        self.import_paths(list(paths or []))

    def import_paths(self, paths) -> None:
        """Kick off a background import and return immediately."""
        files = [p for p in (paths or []) if p and os.path.isfile(p)]
        if not files:
            return
        worker = self._import_worker
        if worker is not None and worker.isRunning():
            worker._cancel = True
            worker.wait(50)
        self.progress.setRange(0, len(files))
        self.progress.setValue(0)
        self.progress.show()
        mode = self._storage_mode()
        project_dir = self._project_dir()
        if mode == "project" and not project_dir:
            host = self.window()
            bar = getattr(host, "statusBar", None) if host is not None else None
            if callable(bar):
                try:
                    bar().showMessage("随工程保存需要先打开或保存工程", 5000)
                except Exception:
                    pass
            mode = "library"
        worker = _ImportWorker(
            files, self, host=self.window(),
            storage_mode=mode, project_dir=project_dir)
        self._import_worker = worker
        worker.progress.connect(self._on_import_progress)
        worker.item_ready.connect(self._on_import_item)
        worker.failed.connect(self._on_import_failed)
        worker.finished_all.connect(self._on_import_finished)
        worker.start()

    def _on_import_progress(self, done: int, total: int, _name: str) -> None:
        self.progress.setRange(0, max(1, total))
        self.progress.setValue(int(done))

    def _on_import_item(self, rec: dict) -> None:
        key = rec.get("type") or ""
        self._select_category(key)

    def _on_import_failed(self, path: str, message: str) -> None:
        host = self.window()
        bar = getattr(host, "statusBar", None)
        if callable(bar):
            try:
                bar().showMessage("导入资产失败：%s" % message, 5000)
            except Exception:
                _LOG.exception("statusBar failed")

    def _on_import_finished(self) -> None:
        self.progress.hide()
        self._on_cat(self.tabs.currentIndex())

    def _import_one(self, path: str) -> None:
        self.import_paths([path])

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        if not event.mimeData().hasUrls():
            event.ignore()
            return
        paths = []
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if path and os.path.isfile(path):
                paths.append(path)
        self.import_paths(paths)
        event.acceptProposedAction()


def _cache_key(path: str) -> tuple | None:
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (path, int(getattr(st, "st_mtime_ns", int(st.st_mtime * 1e9))), int(st.st_size))


def _render_audio_card_thumb(it: dict) -> QPixmap:
    is_bgm = it.get("audio_kind") == "bgm" or it.get("subcat") == "配乐 BGM"
    uid = f"audio_thumb:{it.get('id')}:{is_bgm}:{it.get('duration')}"
    if uid in _THUMB_CACHE:
        return _THUMB_CACHE[uid]
    pm = QPixmap(_THUMB_PX, _THUMB_PX)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    grad = QLinearGradient(0, 0, _THUMB_PX, _THUMB_PX)
    if is_bgm:
        grad.setColorAt(0.0, QColor("#2B194D"))
        grad.setColorAt(1.0, QColor("#151B30"))
        accent = QColor("#FFD166")
        badge_txt = "BGM"
    else:
        grad.setColorAt(0.0, QColor("#12353B"))
        grad.setColorAt(1.0, QColor("#141E28"))
        accent = QColor("#00F5D4")
        badge_txt = "SFX"
    p.setPen(QPen(accent, 1.5))
    p.setBrush(QBrush(grad))
    p.drawRoundedRect(QRectF(2, 2, _THUMB_PX - 4, _THUMB_PX - 4), 10, 10)

    # Waveform bars
    seed = hash(str(it.get("id") or "audio")) & 0xFFFF
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(accent)
    for i in range(9):
        bh = 8 + ((seed >> (i % 8)) & 3) * 5 + int(6 * abs(math.sin(i * 0.9)))
        bx = 10 + i * 6
        by = 36 - bh // 2
        p.drawRoundedRect(QRectF(bx, by, 4, bh), 2, 2)

    # Top-left badge (BGM / SFX)
    p.setFont(QFont("Segoe UI", 7, QFont.Weight.Bold))
    p.setPen(accent)
    p.drawText(QRectF(6, 5, 36, 14), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, badge_txt)

    # Bottom-right duration badge
    dur = float(it.get("duration") or 0.0)
    if dur > 0:
        p.setPen(QColor("#FFFFFF"))
        p.drawText(QRectF(24, _THUMB_PX - 18, _THUMB_PX - 28, 14), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, f"{dur:.1f}s")
    p.end()
    _THUMB_CACHE[uid] = pm
    return pm


def _render_procedural_spec_thumb(it: dict) -> QPixmap | None:
    bd_spec = it.get("border_spec")
    lt_spec = it.get("lower_third_spec")
    if not isinstance(bd_spec, dict) and not isinstance(lt_spec, dict):
        return None
    uid = ("spec_thumb", str(it.get("id") or it.get("name")))
    if uid in _THUMB_CACHE:
        return _THUMB_CACHE[uid]

    img = QImage(_THUMB_PX, _THUMB_PX, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    try:
        if isinstance(bd_spec, dict):
            try:
                from cutora.render.border_engine import render_border
                ov_dummy = {"x": 4, "y": 4, "w": _THUMB_PX - 8, "h": _THUMB_PX - 8, "border_spec": bd_spec, "start": 0.0, "end": 5.0}
                render_border(p, ov_dummy, 1.0, _THUMB_PX, _THUMB_PX)
            except Exception:
                c1 = QColor(str(bd_spec.get("primary_color") or "#FFD700"))
                p.setPen(QPen(c1, 3.0))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRoundedRect(QRectF(6, 6, _THUMB_PX - 12, _THUMB_PX - 12), 6, 6)
        elif isinstance(lt_spec, dict):
            try:
                from cutora.render.lower_third_engine import render_lower_third
                ov_dummy = {
                    "x": 4, "y": 18, "w": _THUMB_PX - 8, "h": 36,
                    "content": str(lt_spec.get("default_text") or it.get("name") or "字幕条"),
                    "lower_third_spec": lt_spec, "start": 0.0, "end": 5.0,
                }
                render_lower_third(p, ov_dummy, 2.0, _THUMB_PX, _THUMB_PX)
            except Exception:
                c1 = QColor(str(lt_spec.get("primary_color") or "#1E293B"))
                c2 = QColor(str(lt_spec.get("secondary_color") or "#FFD700"))
                p.setPen(QPen(c2, 2.0))
                p.setBrush(c1)
                p.drawRoundedRect(QRectF(4, 22, _THUMB_PX - 8, 28), 6, 6)
                p.setPen(QColor("#FFFFFF"))
                p.setFont(QFont("Microsoft YaHei", 7, QFont.Weight.Bold))
                p.drawText(QRectF(6, 22, _THUMB_PX - 12, 28), Qt.AlignmentFlag.AlignCenter, str(it.get("name") or "字幕条")[:6])
    finally:
        p.end()
    pm = QPixmap.fromImage(img)
    _THUMB_CACHE[uid] = pm
    return pm


def _placeholder_thumb(it: dict) -> QPixmap:
    if it.get("type") == "audio":
        return _render_audio_card_thumb(it)
    spec_pm = _render_procedural_spec_thumb(it)
    if spec_pm is not None:
        return spec_pm
    path = it.get("stored_path") or it.get("source_path") or ""
    ext = os.path.splitext(path)[1].lower()
    name = "music" if ext in _AUDIO_EXT else "image"
    pix = icon(name, size=_THUMB_PX).pixmap(_THUMB_PX, _THUMB_PX)
    if pix.isNull():
        pix = QPixmap(_THUMB_PX, _THUMB_PX)
        pix.fill(Qt.GlobalColor.transparent)
    return pix


def _decorate_thumb(pix: QPixmap, rec: dict) -> QPixmap:
    if pix is None or pix.isNull():
        pix = QPixmap(_THUMB_PX, _THUMB_PX)
        pix.fill(Qt.GlobalColor.transparent)
    out = QPixmap(pix)
    p = QPainter(out)
    try:
        if rec.get("missing"):
            p.fillRect(out.rect(), QColor(180, 84, 74, 90))
            p.setPen(QColor(ERR))
            p.drawRect(out.rect().adjusted(0, 0, -1, -1))
        elif rec.get("storage_mode") == "project":
            p.fillRect(0, 0, 10, 10, QColor(242, 193, 78, 220))
        elif rec.get("referenced"):
            badge = icon("folder-open", size=16).pixmap(16, 16)
            if not badge.isNull():
                p.drawPixmap(max(0, out.width() - 18), max(0, out.height() - 18), badge)
        path = str(rec.get("stored_path") or rec.get("source_path") or "")
        if rec.get("is_animated_gif") or path.lower().endswith(".gif"):
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(20, 24, 36, 210))
            p.drawRoundedRect(QRectF(out.width() - 26, 2, 24, 13), 4, 4)
            p.setPen(QColor("#00F5D4"))
            p.setFont(QFont("Segoe UI", 6, QFont.Weight.Bold))
            p.drawText(QRectF(out.width() - 26, 2, 24, 13), Qt.AlignmentFlag.AlignCenter, "GIF")
    finally:
        p.end()
    return out


def thumb_for(it: dict, force: bool = False):
    if it.get("type") == "audio":
        return _render_audio_card_thumb(it)
    path = it.get("stored_path") or it.get("source_path") or ""
    if not path or not os.path.isfile(path):
        return _render_procedural_spec_thumb(it)
    key = _cache_key(path)
    if not force and key is not None and key in _THUMB_CACHE:
        return _THUMB_CACHE[key]
    ext = os.path.splitext(path)[1].lower()
    pix = None
    try:
        if ext == ".svg":
            from PySide6.QtSvg import QSvgRenderer
            rend = QSvgRenderer(path)
            img = QImage(_THUMB_PX, _THUMB_PX, QImage.Format.Format_ARGB32_Premultiplied)
            img.fill(0)
            p = QPainter(img)
            rend.render(p)
            p.end()
            pix = QPixmap.fromImage(img)
        elif ext in _VIDEO_EXT:
            from cutora.ui.timeline.thumbs import peek_clip_thumb
            hit = peek_clip_thumb(path, 0.0, _THUMB_PX, _THUMB_PX)
            if hit:
                pix = QPixmap(hit)
        elif ext in _AUDIO_EXT:
            pix = _render_audio_card_thumb(it)
        else:
            pix = QPixmap(path)
        if pix is not None and not pix.isNull():
            pix = pix.scaled(
                _THUMB_PX, _THUMB_PX,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation)
        else:
            pix = None
    except Exception:
        _LOG.exception("thumb failed: %s", path)
        pix = None
    if key is not None and pix is not None:
        _THUMB_CACHE[key] = pix
    return pix


def _thumb_for(it: dict):
    """Back-compat alias used by older tests."""
    return thumb_for(it)
