# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""T27-b: asset library = builtin pack + user assets. Thumbnail grid + search."""
from __future__ import annotations

import os

from PySide6.QtCore import QEvent, QSize, Qt, QThread, QUrl, Signal
from PySide6.QtGui import QColor, QDrag, QIcon, QMovie, QPainter, QPixmap
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

_IMPORT_FILTER = (
    "资产 (*.png *.jpg *.jpeg *.webp *.svg *.gif *.apng *.mov *.mp4 *.mkv "
    "*.webm *.mp3 *.wav *.flac *.aac *.ogg *.m4a);;所有文件 (*)"
)


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
            p = rec.get("stored_path") or ""
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
        icon = items[0].icon()
        if not icon.isNull():
            drag.setPixmap(icon.pixmap(self.iconSize()))
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
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("assetLibrary")
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.setAcceptDrops(True)
        self._import_worker = None
        self._hover_item = None
        self._hover_movie = None
        self._pending_thumbs = 0
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
        self.search.textChanged.connect(lambda _t: self._on_cat(self.tabs.currentIndex()))
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

    def _on_cat(self, idx: int) -> None:
        self._stop_hover_movie()
        self.list.clear()
        key = self._category_key(idx)
        q = (self.search.text() or "").strip()
        from cutora.assets.library import items_for_category
        items = items_for_category(key, q, project_dir=self._project_dir())
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
            self.list.addItem(item)
            n += 1
            self._queue_video_thumb(it, item)
        self._apply_empty(n)

    def _queue_video_thumb(self, rec: dict, item: QListWidgetItem) -> None:
        path = rec.get("stored_path") or ""
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
                if got.get("id") != _id and got.get("stored_path") != _path:
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
        path = rec.get("stored_path") or ""
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


def _placeholder_thumb(it: dict) -> QPixmap:
    ext = os.path.splitext(it.get("stored_path") or "")[1].lower()
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
    finally:
        p.end()
    return out


def thumb_for(it: dict, force: bool = False):
    path = it.get("stored_path") or ""
    if not path or not os.path.isfile(path):
        return None
    key = _cache_key(path)
    if not force and key is not None and key in _THUMB_CACHE:
        return _THUMB_CACHE[key]
    ext = os.path.splitext(path)[1].lower()
    pix = None
    try:
        if ext == ".svg":
            from PySide6.QtGui import QImage
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
            pix = _placeholder_thumb(it)
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

