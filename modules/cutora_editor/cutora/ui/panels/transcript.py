# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Left-dock Transcript panel: per-sentence cards, CPS / overflow badges."""
from __future__ import annotations

from PySide6.QtCore import (
    QAbstractListModel, QEvent, QModelIndex, QSize, Qt, QTimer,
)
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QStyledItemDelegate,
    QStyle,
    QStyleOptionViewItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from cutora.core.log import get_logger
from cutora.subtitle.layout import (
    DEFAULT_CPS,
    cps,
    is_over_cps,
    is_overlong,
    layout_caps,
)
from cutora.subtitle.track_source import count_track_subs, get_segments
from cutora.ui.i18n import tr
from cutora.ui.icons import bind_icon
from cutora.ui.theme.fonts_ui import mono_qfont, ui_qfont
from cutora.ui.theme.tokens import FONT_SIZE_BODY, WARN

_LOG = get_logger("cutora.ui.panels.transcript")

MSG_TRUE_EMPTY = "还没有字幕。先自动字幕，或在「文案」里贴原文再对齐。"
MSG_NO_MEDIA = "先在媒体库里选一个条目"
MSG_UNREAD = "检测到时间线上有 %s 条字幕但读取失败"

_MAX_EDIT_LINES = 4


def _tc(sec) -> str:
    s = max(0.0, float(sec or 0))
    m, r = divmod(s, 60.0)
    h, m = divmod(int(m), 60)
    if h:
        return "%d:%02d:%05.2f" % (h, m, r)
    return "%02d:%05.2f" % (m, r)


class _GrowEdit(QPlainTextEdit):
    """Height follows wrapped content: 1 line minimum, scroll after 4."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("transcriptEditor")
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setTabChangesFocus(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        body = ui_qfont(FONT_SIZE_BODY)
        self.setFont(body)
        self.document().setDefaultFont(body)
        self._fitting = False
        self.document().documentLayout().documentSizeChanged.connect(self.fit_height)
        self.fit_height()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.fit_height()

    def fit_height(self, *_args):
        if self._fitting:
            return
        self._fitting = True
        try:
            fm = self.fontMetrics()
            line = max(fm.lineSpacing(), fm.height())
            pad = self.contentsMargins().top() + self.contentsMargins().bottom()
            pad += 2 * max(1, self.frameWidth()) + 8
            min_h = line + pad
            max_h = line * _MAX_EDIT_LINES + pad
            doc = self.document()
            doc.setTextWidth(max(1, self.viewport().width()))
            content_h = int(round(float(doc.size().height()))) + pad
            if content_h <= max_h:
                self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
                h = max(min_h, content_h)
            else:
                self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
                h = max_h
            if abs(self.height() - h) > 1:
                self.setFixedHeight(h)
        finally:
            self._fitting = False


class _Card(QFrame):
    def __init__(self, panel: "TranscriptPanel", seg_id: int, seg: dict, caps):
        super().__init__(panel)
        self.panel = panel
        self.seg_id = int(seg_id)
        self.setObjectName("transcriptCard")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 4, 8, 4)
        lay.setSpacing(2)
        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(6)
        start = float(seg.get("start") or 0)
        end = float(seg.get("end") or 0)
        self.lbl_tc = QLabel("%s – %s" % (_tc(start), _tc(end)))
        self.lbl_tc.setFont(mono_qfont(11))
        self.lbl_tc.setObjectName("transcriptTimecode")
        top.addWidget(self.lbl_tc)
        speaker = str(seg.get("speaker") or "").strip()
        self.lbl_spk = QLabel(speaker)
        self.lbl_spk.setObjectName("hint")
        self.lbl_spk.setVisible(bool(speaker))
        top.addWidget(self.lbl_spk)
        top.addStretch(1)
        self.lbl_cps = QLabel("")
        self.lbl_over = QLabel("")
        self.btn_lock = QToolButton()
        self.btn_lock.setAutoRaise(True)
        self.btn_lock.setFixedSize(22, 22)
        self.btn_lock.setIconSize(QSize(14, 14))
        self.btn_lock.setText("")
        self.btn_lock.clicked.connect(self._toggle_lock)
        top.addWidget(self.lbl_cps)
        top.addWidget(self.lbl_over)
        top.addWidget(self.btn_lock)
        lay.addLayout(top)
        self.editor = _GrowEdit()
        self.editor.setPlainText(str(seg.get("text") or ""))
        self.editor.textChanged.connect(self._on_text_changed)
        self._editor_press = self.editor.mousePressEvent
        self.editor.mousePressEvent = self._press_word
        lay.addWidget(self.editor)
        self._dirty = False
        self.refresh_badges(seg, caps)
        self._apply_line_preview(seg)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._menu)
        self.lbl_tc.mousePressEvent = self._jump  # type: ignore[method-assign]
        QTimer.singleShot(0, self.editor.fit_height)

    def _on_text_changed(self):
        self._dirty = True

    def commit(self):
        if not self._dirty:
            return
        self._dirty = False
        text = self.editor.toPlainText()
        self.panel.run("subtitle.set_text", {"seg_id": self.seg_id, "text": text})

    def refresh_badges(self, seg: dict, caps):
        max_c, max_l = caps
        warn = QColor(WARN)
        over_cps = is_over_cps(seg, DEFAULT_CPS)
        self.lbl_cps.setVisible(over_cps)
        if over_cps:
            value = cps(seg)
            self.lbl_cps.setText("%.0f CPS" % value if value != float("inf") else "CPS")
            self.lbl_cps.setStyleSheet("color: %s; font-weight: 600;" % warn.name())
            self.lbl_cps.setToolTip(tr("阅读速度超标"))
        else:
            self.lbl_cps.clear()
        fit = str(seg.get("layout_warn") or seg.get("_fit_warn") or "")
        if fit == "shrink":
            self.lbl_over.setText(tr("字号已临时缩小，点「重新断句」"))
            self.lbl_over.setStyleSheet("color: %s; font-weight: 600;" % warn.name())
            self.lbl_over.setVisible(True)
        elif fit in ("truncate", "clip"):
            self.lbl_over.setText(tr("有单词过长，已截断，建议改短或减小字号"))
            self.lbl_over.setStyleSheet("color: #E85D4C; font-weight: 600;")
            self.lbl_over.setVisible(True)
        elif is_overlong(seg, max_c, max_l):
            self.lbl_over.setText(tr("超长，会超出画面"))
            self.lbl_over.setStyleSheet("color: %s; font-weight: 600;" % warn.name())
            self.lbl_over.setVisible(True)
        else:
            self.lbl_over.clear()
            self.lbl_over.setVisible(False)
        locked = bool(seg.get("locked"))
        bind_icon(
            self.btn_lock,
            "lock" if locked else "lock-open",
            size=14,
            color_token="warn" if locked else "fg1",
        )
        self.btn_lock.setToolTip(tr("已锁定") if locked else tr("锁定"))
        self._apply_line_preview(seg)

    def _apply_line_preview(self, seg: dict):
        from cutora.subtitle.layout import LayoutRules, line_preview
        lang = seg.get("lang")
        tip = line_preview(seg, LayoutRules(lang=lang))
        self.setToolTip(tip)
        self.editor.setToolTip(tip)

    def _toggle_lock(self):
        segs = self.panel.segments()
        cur = False
        if 0 <= self.seg_id < len(segs):
            cur = bool(segs[self.seg_id].get("locked"))
        self.panel.run("subtitle.lock", {
            "seg_id": self.seg_id, "locked": (not cur),
        })

    def _seek_word(self, seg, index):
        from cutora.audio.silence import word_time_at_char
        t = word_time_at_char(seg, index)
        if t is None:
            return False
        self.panel.run("timeline.set_playhead", {"time": float(t)})
        return True

    def _press_word(self, event):
        self._editor_press(event)
        if event.button() != Qt.MouseButton.LeftButton:
            return
        segs = self.panel.segments()
        if not (0 <= self.seg_id < len(segs)):
            return
        self._seek_word(segs[self.seg_id], self.editor.textCursor().position())

    def _jump(self, _event=None):
        segs = self.panel.segments()
        if 0 <= self.seg_id < len(segs):
            seg = segs[self.seg_id]
            t = float(seg.get("start") or 0)
            cur = self.editor.textCursor()
            if cur.position() > 0 or cur.hasSelection():
                from cutora.audio.silence import word_time_at_char
                hit = word_time_at_char(seg, cur.position())
                if hit is not None:
                    t = float(hit)
            self.panel.run("timeline.set_playhead", {"time": t})

    def mouseDoubleClickEvent(self, event):
        self.editor.setFocus()
        super().mouseDoubleClickEvent(event)

    def _menu(self, pos):
        menu = QMenu(self)
        menu.addAction(tr("跳到时间线"), self._jump)
        menu.addAction(tr("锁定/解锁"), self._toggle_lock)
        menu.addSeparator()
        menu.addAction(tr("与上一句合并"), lambda: self.panel.run(
            "subtitle.merge", {"seg_id": self.seg_id, "direction": "prev"}))
        menu.addAction(tr("与下一句合并"), lambda: self.panel.run(
            "subtitle.merge", {"seg_id": self.seg_id, "direction": "next"}))
        at_char = self.editor.textCursor().position()
        menu.addAction(tr("在光标处拆分"), lambda: self.panel.run(
            "subtitle.split", {"seg_id": self.seg_id, "at_char": at_char}))
        menu.addSeparator()
        selected = self.editor.textCursor().selectedText().strip()
        if selected:
            menu.addAction(tr("强调"), lambda w=selected: self.panel.run(
                "subtitle.emphasis.auto", {"add_word": w}))
            menu.addAction(tr("取消强调"), lambda w=selected: self.panel.run(
                "subtitle.emphasis.auto", {"remove_word": w}))
        menu.addSeparator()
        menu.addAction(tr("删除句"), lambda: self.panel.run(
            "subtitle.delete", {"seg_id": self.seg_id, "confirm": True}))
        menu.exec(self.mapToGlobal(pos))


class _SegModel(QAbstractListModel):
    def __init__(self, panel: "TranscriptPanel"):
        super().__init__(panel)
        self.panel = panel
        self._segs: list[dict] = []

    def rowCount(self, parent=QModelIndex()):
        if parent.isValid():
            return 0
        return len(self._segs)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not (0 <= index.row() < len(self._segs)):
            return None
        seg = self._segs[index.row()]
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole):
            return str(seg.get("text") or "")
        if role == Qt.ItemDataRole.UserRole:
            return seg
        return None

    def flags(self, index):
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        return (
            Qt.ItemFlag.ItemIsEnabled
            | Qt.ItemFlag.ItemIsSelectable
            | Qt.ItemFlag.ItemIsEditable
        )

    def reset_segs(self, segs: list):
        self.beginResetModel()
        self._segs = [s for s in segs if isinstance(s, dict)]
        self.endResetModel()


class _SegDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index):
        text = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
        w = max(160, option.rect.width() or 240)
        n = max(1, min(4, 1 + (len(text) // 42)))
        return QSize(w, 30 + n * 18)

    def paint(self, painter: QPainter, option, index):
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        painter.save()
        rect = opt.rect.adjusted(4, 2, -4, -2)
        selected = bool(opt.state & QStyle.StateFlag.State_Selected)
        painter.fillRect(opt.rect, QColor(40, 44, 52, 220 if selected else 160))
        seg = index.data(Qt.ItemDataRole.UserRole) or {}
        start = float(seg.get("start") or 0)
        end = float(seg.get("end") or 0)
        painter.setPen(QColor(160, 170, 180))
        painter.drawText(rect.adjusted(4, 2, -4, 0), Qt.AlignmentFlag.AlignTop,
                         "%s – %s" % (_tc(start), _tc(end)))
        painter.setPen(QColor(230, 232, 236))
        painter.drawText(
            rect.adjusted(4, 18, -4, -2),
            Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap,
            str(seg.get("text") or ""))
        painter.restore()

    def createEditor(self, parent, option, index):
        panel = index.model().panel
        seg = index.data(Qt.ItemDataRole.UserRole) or {}
        card = _Card(panel, index.row(), seg, layout_caps(panel.task()))
        card.setParent(parent)
        return card

    def setModelData(self, editor, model, index):
        if isinstance(editor, _Card):
            editor.commit()


class TranscriptPanel(QWidget):
    def __init__(self, window, parent=None):
        super().__init__(parent)
        self.window = window
        self._cards: list[_Card] = []
        self._play_idx = -1
        self._reload_guard = False
        self.setMinimumWidth(160)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(4)
        root.addLayout(self._make_toolbar())
        self.lbl_hits = QLabel("")
        self.lbl_hits.setObjectName("hint")
        self.lbl_hits.hide()
        root.addWidget(self.lbl_hits)
        self.banner = QFrame()
        self.banner.setObjectName("transcriptBanner")
        ban = QHBoxLayout(self.banner)
        ban.setContentsMargins(8, 2, 4, 2)
        self.lbl_banner = QLabel(tr("已按排版规则断行"))
        self.lbl_banner.setObjectName("hint")
        btn_x = QToolButton()
        btn_x.setAutoRaise(True)
        btn_x.setText("")
        btn_x.setFixedSize(22, 22)
        bind_icon(btn_x, "x", size=12, color_token="fg1")
        btn_x.clicked.connect(self.banner.hide)
        ban.addWidget(self.lbl_banner, 1)
        ban.addWidget(btn_x, 0)
        self.banner.hide()
        root.addWidget(self.banner)
        self.empty_box = QWidget()
        empty_l = QVBoxLayout(self.empty_box)
        empty_l.setContentsMargins(8, 16, 8, 8)
        self.empty = QLabel(tr(MSG_TRUE_EMPTY))
        self.empty.setObjectName("hint")
        self.empty.setWordWrap(True)
        self.btn_empty_auto = QPushButton(tr("用这段文案做自动字幕"))
        self.btn_empty_auto.setObjectName("btn_empty_auto")
        self.btn_empty_auto.setProperty("noElide", True)
        from cutora.ui.widgets.responsive import mark_explicit
        mark_explicit(self.btn_empty_auto, "text")
        mark_explicit(self.btn_empty_auto, "sizePolicy")
        self.btn_empty_auto.setSizePolicy(
            QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        self.btn_empty_auto.clicked.connect(self._do_empty_auto)
        self.btn_reload = QPushButton(tr("重新载入"))
        self.btn_reload.setObjectName("btn_transcript_reload")
        self.btn_reload.setProperty("noElide", True)
        mark_explicit(self.btn_reload, "text")
        mark_explicit(self.btn_reload, "sizePolicy")
        self.btn_reload.setSizePolicy(
            QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        self.btn_reload.clicked.connect(lambda: self.reload(force=True))
        self.btn_reload.hide()
        empty_l.addWidget(self.empty)
        empty_l.addWidget(self.btn_empty_auto, 0, Qt.AlignmentFlag.AlignLeft)
        empty_l.addWidget(self.btn_reload, 0, Qt.AlignmentFlag.AlignLeft)
        empty_l.addStretch(1)
        self.model = _SegModel(self)
        self.view = QListView()
        self.view.setObjectName("transcriptScroll")
        self.view.setModel(self.model)
        self.view.setItemDelegate(_SegDelegate(self.view))
        self.view.setUniformItemSizes(False)
        self.view.setSelectionMode(QListView.SelectionMode.SingleSelection)
        self.view.setEditTriggers(
            QListView.EditTrigger.DoubleClicked | QListView.EditTrigger.SelectedClicked)
        self.view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        root.addWidget(self.empty_box)
        root.addWidget(self.view, 1)
        self._commit_timer = QTimer(self)
        self._commit_timer.setSingleShot(True)
        self._commit_timer.timeout.connect(self._commit_focused)

    def _make_toolbar(self):
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        self.find_edit = QLineEdit()
        self.find_edit.setPlaceholderText(tr("查找"))
        self.find_edit.setMinimumWidth(48)
        self.replace_edit = QLineEdit()
        self.replace_edit.setPlaceholderText(tr("替换为"))
        self.replace_edit.setMinimumWidth(48)
        self.chk_regex = QCheckBox(tr("正则"))
        self.chk_case = QCheckBox(tr("区分大小写"))
        btn_find = QPushButton(tr("查找"))
        btn_rep = QPushButton(tr("替换"))
        btn_find.clicked.connect(self._do_find)
        btn_rep.clicked.connect(self._do_replace)
        more = QToolButton()
        more.setObjectName("transcriptMoreBtn")
        more.setAutoRaise(True)
        more.setFixedSize(28, 28)
        more.setIconSize(QSize(16, 16))
        more.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        more.setToolTip(tr("更多"))
        more.setText("")
        bind_icon(more, "ellipsis", size=16, color_token="fg1")
        menu = QMenu(more)
        menu.addAction(tr("全部大写"), lambda: self.run(
            "subtitle.transform_case", {"mode": "upper"}))
        menu.addAction(tr("首字母大写"), lambda: self.run(
            "subtitle.transform_case", {"mode": "title"}))
        menu.addAction(tr("全部小写"), lambda: self.run(
            "subtitle.transform_case", {"mode": "lower"}))
        menu.addAction(tr("移除句末标点"), lambda: self.run(
            "subtitle.strip_end_punct", {}))
        menu.addSeparator()
        menu.addAction(tr("批量替换词典…"), self._do_glossary)
        more.setMenu(menu)
        self.btn_more = more
        for w in (self.find_edit, self.replace_edit, self.chk_regex,
                  self.chk_case, btn_find, btn_rep, more):
            row.addWidget(w)
        return row

    def minimumSizeHint(self):
        return QSize(160, 200)

    def sizeHint(self):
        return QSize(280, 400)

    def bus(self):
        return getattr(self.window, "bus", None)

    def _curr_idx(self) -> int:
        raw = getattr(self.window, "curr_idx", -1)
        if raw is None:
            return -1
        try:
            return int(raw)
        except (TypeError, ValueError):
            return -1

    def task(self) -> dict:
        tasks = getattr(self.window, "tasks", None) or []
        idx = self._curr_idx()
        if 0 <= idx < len(tasks) and isinstance(tasks[idx], dict):
            return tasks[idx]
        if len(tasks) == 1 and isinstance(tasks[0], dict) and idx < 0:
            return tasks[0]
        return {}

    def has_selected_media(self) -> bool:
        tasks = getattr(self.window, "tasks", None) or []
        idx = self._curr_idx()
        return 0 <= idx < len(tasks)

    def segments(self) -> list:
        return list(get_segments(self.task()))

    def run(self, name: str, params: dict):
        bus = self.bus()
        if bus is None:
            return None
        self._commit_open_editors()
        return bus.execute(name, params, source="ui")

    def _commit_open_editors(self):
        for card in self._cards:
            card.commit()

    def _commit_focused(self):
        w = self.window.focusWidget() if self.window else None
        for card in self._cards:
            if w is card.editor:
                card.commit()
                return

    def _do_empty_auto(self):
        fn = getattr(self.window, "_ui_auto_subtitle", None)
        if callable(fn):
            fn()

    def show_relayout_banner(self):
        self.banner.show()

    def _segs_fp(self, segs) -> tuple:
        out = []
        for i, seg in enumerate(segs or []):
            if not isinstance(seg, dict):
                continue
            out.append((i, seg.get("start"), seg.get("end"), seg.get("text")))
        return tuple(out)

    def reload(self, force=False):
        force = bool(force or getattr(self, "_reload_force_once", False))
        self._reload_force_once = False
        if self._reload_guard:
            return
        prev_n = len(getattr(self, "_loaded_fp", ()) or ())
        segs = self.segments()
        grew = prev_n == 0 and len(segs) > 0
        bypass = bool(force or grew)
        focused = None
        w = self.window.focusWidget() if self.window else None
        for card in self._cards:
            if w is card.editor:
                focused = card.seg_id
                if card._dirty and not bypass:
                    return
        fp = self._segs_fp(segs)
        if (
            not bypass
            and fp == getattr(self, "_loaded_fp", None)
            and len(self._cards) == len(fp)
            and not any(getattr(c, "_dirty", False) for c in self._cards)
        ):
            self.set_playhead_ms(int(getattr(self.window, "_timeline_ms", 0) or 0))
            return
        caps = layout_caps(self.task())
        self._reload_guard = True
        try:
            self._rebuild_cards(segs, caps, focused)
            self._loaded_fp = fp
        finally:
            self._reload_guard = False
        self.set_playhead_ms(int(getattr(self.window, "_timeline_ms", 0) or 0))

    def _apply_empty_state(self, rows):
        if not self.has_selected_media():
            self.empty.setText(tr(MSG_NO_MEDIA))
            self.btn_empty_auto.setVisible(False)
            self.btn_reload.setVisible(False)
            self.empty_box.setVisible(True)
            self.view.setVisible(False)
            return
        n_track = count_track_subs(self.task())
        if not rows and n_track > 0:
            self.empty.setText(tr(MSG_UNREAD) % n_track)
            self.btn_empty_auto.setVisible(False)
            self.btn_reload.setVisible(True)
            _LOG.error(
                "transcript read failed: track_subs=%s readable=%s",
                n_track, len(rows or []),
            )
            self.empty_box.setVisible(True)
            self.view.setVisible(False)
            return
        self.empty.setText(tr(MSG_TRUE_EMPTY))
        self.btn_empty_auto.setVisible(True)
        self.btn_reload.setVisible(False)
        self.empty_box.setVisible(not rows)
        self.view.setVisible(bool(rows))

    def _rebuild_cards(self, segs, caps, focused):
        rows = [s for s in segs if isinstance(s, dict)]
        self.model.reset_segs(rows)
        self._apply_empty_state(rows)
        self._cards = []
        if focused is not None and 0 <= focused < self.model.rowCount():
            idx = self.model.index(focused, 0)
            self.view.setCurrentIndex(idx)
            self.view.edit(idx)

    def inspect_cards(self) -> list:
        """Materialize _Card widgets for tests / commit. Not used in 1000-fill timing."""
        segs = self.model._segs
        caps = layout_caps(self.task())
        self._cards = []
        for i, seg in enumerate(segs):
            card = _Card(self, i, seg, caps)
            card.editor.installEventFilter(self)
            self._cards.append(card)
        return self._cards

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.FocusOut and isinstance(obj, QPlainTextEdit):
            self._commit_timer.start(0)
        return super().eventFilter(obj, event)

    def set_playhead_ms(self, ms: int):
        t = max(0.0, float(ms) / 1000.0)
        idx = -1
        segs = self.segments()
        for i, seg in enumerate(segs):
            if not isinstance(seg, dict):
                continue
            if float(seg.get("start") or 0) <= t <= float(seg.get("end") or 0):
                idx = i
                break
        if idx == self._play_idx:
            return
        self._play_idx = idx
        if 0 <= idx < self.model.rowCount():
            self.view.setCurrentIndex(self.model.index(idx, 0))
            self.view.scrollTo(self.model.index(idx, 0))

    def _do_find(self):
        q = self.find_edit.text()
        out = self.run("subtitle.find", {
            "query": q,
            "regex": self.chk_regex.isChecked(),
            "case": self.chk_case.isChecked(),
        }) or {}
        n = int(out.get("count") or 0)
        self.lbl_hits.setText(tr("找到 %s 处") % n if q else "")
        self.lbl_hits.setVisible(bool(q))
        hits = out.get("hits") or []
        if hits:
            sid = int(hits[0].get("seg_id") or 0)
            if 0 <= sid < self.model.rowCount():
                idx = self.model.index(sid, 0)
                self.view.setCurrentIndex(idx)
                self.view.scrollTo(idx)
                self.view.edit(idx)

    def _do_replace(self):
        self.run("subtitle.replace", {
            "query": self.find_edit.text(),
            "replacement": self.replace_edit.text(),
            "regex": self.chk_regex.isChecked(),
            "case": self.chk_case.isChecked(),
        })

    def _do_glossary(self):
        path, _ok = QFileDialog.getOpenFileName(
            self, tr("批量替换词典"), "", "CSV (*.csv);;All (*.*)")
        if path:
            self.run("subtitle.apply_glossary", {"csv": path})
