# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q191 · 主窗口里的「房间」+ 底部页面栏（同达芬奇底部那排「剪辑 / 调色 / 交付」）。

Luca：批量加载、中央监视器这些外置子窗口一开，主窗口就钉在后面拖不动，看别的程序被挡住。
现在 CUTORA 只有一个窗口：切房间 = 整个工作区换成那个房间的布局；结束点「完成并退出」回到剪辑。

- 房间第一次进入才建（``factory``），之后切走 / 切回只是换页面，不重建。
- ``keep_alive=False`` 的房间离开就拆掉（中央监视器：几十格缩略图 + 解码器，留着占内存）；
  ``keep_alive=True`` 的房间离开时只调 ``on_leave`` 释放解码器 / 播放器，表格里填了一半的东西还在。
- 进房间：剪辑页的停靠面板和快速工具带收起（记下原来的布局，回来原样恢复）、预览播放暂停、
  剪辑页的快捷键（空格播放、J/K/L、S 分割、Ctrl+Z 等）暂时让出来，房间里的表格 / 画布自己用。
- 房间内容大多原来是 QDialog：``embed_dialog`` 把它变成普通子控件（不再是独立窗口），
  它自己的「关闭」= 完成并退出；Esc 不再一下关掉（以前 Esc 会把粘了一半的表格整个丢掉）。
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QAbstractButton,
    QDialog,
    QHBoxLayout,
    QSizePolicy,
    QToolBar,
    QWidget,
)

from cutora.core.log import get_logger
from cutora.ui.theme.tokens import RADIUS_CARD, RADIUS_CTL, RADIUS_SM

_LOG = get_logger("cutora.ui.rooms")

EDIT = "edit"
PAGE_BAR_H = 34
_KEEP_SHORTCUTS = ("Ctrl+S", "Ctrl+Shift+S", "Ctrl+Shift+M", "Ctrl+,", "Ctrl+Shift+L")


# ───────────────────────────── 底部页面栏 ─────────────────────────────

class PageButton(QAbstractButton):
    """图标 + 字，选中的那一页：主题色字 + 底下一道短亮线；悬停：微亮圆角底。"""

    def __init__(self, key: str, title: str, icon_name: str, parent=None):
        super().__init__(parent)
        self.key = key
        self.icon_name = icon_name
        self.setText(title)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._hover = False
        self._icon = None
        self.badge = False                  # 房间开着但不在这一页：右上角一个小点（提醒里面有做到一半的东西）

    def _ensure_icon(self):
        if self._icon is None:
            try:
                from cutora.ui.icons import icon
                self._icon = icon(self.icon_name, size=16)
            except Exception:
                self._icon = False
        return self._icon or None

    def sizeHint(self):
        fm = self.fontMetrics()
        return QSize(fm.horizontalAdvance(self.text()) + 16 + 8 + 28, PAGE_BAR_H - 6)

    def enterEvent(self, ev):
        self._hover = True
        self.update()
        super().enterEvent(ev)

    def leaveEvent(self, ev):
        self._hover = False
        self.update()
        super().leaveEvent(ev)

    def paintEvent(self, ev):
        from cutora.ui.theme.tokens import current
        tok = current()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        r = QRectF(self.rect()).adjusted(2, 2, -2, -2)
        if self._hover and not self.isChecked():
            c = QColor(tok.fg0)
            c.setAlphaF(0.07)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(c)
            p.drawRoundedRect(r, RADIUS_CTL, RADIUS_CTL)
        on = self.isChecked()
        color = QColor(tok.accent if on else (tok.fg0 if self._hover else tok.fg1))
        fm = self.fontMetrics()
        tw = fm.horizontalAdvance(self.text())
        total = 16 + 6 + tw
        x = (self.width() - total) / 2.0
        ic = self._ensure_icon()
        if ic is not None:
            pm = ic.pixmap(16, 16)
            if not pm.isNull():
                # 图标按当前颜色上色
                tinted = QPainter(pm)
                tinted.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
                tinted.fillRect(pm.rect(), color)
                tinted.end()
                p.drawPixmap(int(x), int((self.height() - 16) / 2), pm)
        p.setPen(color)
        p.drawText(QRectF(x + 22, 0, tw + 2, self.height()),
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self.text())
        if on:
            c = QColor(tok.accent)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(c)
            p.drawRoundedRect(QRectF(self.width() / 2.0 - 12, self.height() - 3, 24, 2), RADIUS_SM, RADIUS_SM)
        if self.badge and not on:
            c = QColor(tok.accent)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(c)
            p.drawEllipse(QRectF(x + 13, 5, 5, 5))
        p.end()


class PageBar(QWidget):
    """一排页面按钮，居中。左侧带精巧的「欢迎向导」收起/展开图标按钮。"""

    page_clicked = Signal(str)
    welcome_toggled = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("pageBarBody")
        self.setFixedHeight(PAGE_BAR_H)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 2, 8, 2)
        lay.setSpacing(4)
        self.welcome_btn = PageButton("welcome", "欢迎向导", "home", self)
        self.welcome_btn.setToolTip("展开 / 收起欢迎页（新建项目、打开最近工程、恢复自动备份）")
        self.welcome_btn.clicked.connect(lambda _c=False: self.welcome_toggled.emit())
        lay.addWidget(self.welcome_btn)
        lay.addStretch(1)
        self._lay = lay
        self.buttons: dict[str, PageButton] = {}

    def add_page(self, key: str, title: str, icon_name: str, tip: str = "") -> PageButton:
        b = PageButton(key, title, icon_name, self)
        if tip:
            b.setToolTip(tip)
        b.clicked.connect(lambda _c=False, k=key: self.page_clicked.emit(k))
        self._lay.insertWidget(self._lay.count(), b)
        self.buttons[key] = b
        return b

    def finish(self):
        self._lay.addStretch(1)

    def set_current(self, key: str):
        for k, b in self.buttons.items():
            b.setChecked(k == key)

    def set_badge(self, key: str, on: bool):
        b = self.buttons.get(key)
        if b is not None and b.badge != bool(on):
            b.badge = bool(on)
            b.update()

    def paintEvent(self, ev):
        from cutora.ui.theme.tokens import current
        tok = current()
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(tok.bg0))
        c = QColor(tok.line)
        p.fillRect(0, 0, self.width(), 1, c)
        p.end()


# ───────────────────────────── 把 QDialog 嵌进来 ─────────────────────────────

class _EscGuard(QObject):
    """嵌进房间的对话框：Esc 不关房间（表格编辑器里的 Esc 照样取消编辑，因为编辑器先拿到）。"""

    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.Type.KeyPress and ev.key() == Qt.Key.Key_Escape:
            return True
        return False


def embed_dialog(dlg: QDialog) -> QDialog:
    """把一个 QDialog 变成普通子控件：不再是顶层窗口、没有标题栏、不抢模态。"""
    dlg.setWindowFlags(Qt.WindowType.Widget)
    dlg.setModal(False)
    try:
        dlg.setSizeGripEnabled(False)
    except Exception:
        pass
    dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
    lay = dlg.layout()
    if lay is not None:
        m = lay.contentsMargins()
        # 房间是整块工作区：四周至少 16 px 留白（原来做窗口时贴着窗框的那几个也一样）
        lay.setContentsMargins(max(m.left(), 16), max(m.top(), 16), max(m.right(), 16), max(m.bottom(), 12))
    guard = _EscGuard(dlg)
    dlg.installEventFilter(guard)
    dlg._esc_guard = guard
    dlg._embedded_room = True
    return dlg


# ───────────────────────────── 房间管理 ─────────────────────────────

class _Room:
    def __init__(self, key, title, icon_name, factory, keep_alive, on_enter, on_leave, tip):
        self.key = key
        self.title = title
        self.icon_name = icon_name
        self.factory = factory
        self.keep_alive = keep_alive
        self.on_enter = on_enter
        self.on_leave = on_leave
        self.tip = tip
        self.widget = None
        self.container = None     # 外面套的滚动区：房间再高也不把主窗口撑大（小屏 1366×768）


class ShortcutGate:
    """剪辑页快捷键的让位：房间 / 浮层开着时 acquire，全关了 release（按次数记，嵌套也对）。"""

    def __init__(self, win):
        self.win = win
        self.count = 0
        self._muted: list = []

    def acquire(self):
        self.count += 1
        if self.count == 1:
            self._mute()

    def release(self):
        if self.count <= 0:
            return
        self.count -= 1
        if self.count == 0:
            self._unmute()

    @property
    def muted(self):
        return list(self._muted)

    def _mute(self):
        from PySide6.QtGui import QAction, QShortcut
        win = self.win
        muted = []
        for act in win.findChildren(QAction):
            try:
                seqs = act.shortcuts()
            except RuntimeError:
                continue
            if not seqs:
                continue
            if any(s.toString() in _KEEP_SHORTCUTS for s in seqs):
                continue
            muted.append(("a", act, list(seqs)))
            act.setShortcuts([])
        for sc in win.findChildren(QShortcut):
            try:
                if sc.isEnabled() and sc.key().toString() not in _KEEP_SHORTCUTS:
                    muted.append(("s", sc, None))
                    sc.setEnabled(False)
            except RuntimeError:
                continue
        self._muted = muted

    def _unmute(self):
        for kind, obj, seqs in self._muted:
            try:
                if kind == "a":
                    obj.setShortcuts(seqs)
                else:
                    obj.setEnabled(True)
            except RuntimeError:
                continue
        self._muted = []


class RoomHost(QObject):
    """挂在 MainWindow 上（``win.room_host``）。"""

    room_changed = Signal(str)

    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.rooms: dict[str, _Room] = {}
        self.current = EDIT
        self._dock_state = None
        self._dock_visible: list = []
        self._quick_visible = None
        self.gate = ShortcutGate(win)
        self._room_gate = False
        self._switching = False
        self.bar = PageBar()
        self.bar.page_clicked.connect(self.enter)
        self.bar.welcome_toggled.connect(self.toggle_welcome)
        self.toolbar = QToolBar("页面", win)
        self.toolbar.setObjectName("pageBar")
        self.toolbar.setMovable(False)
        self.toolbar.setFloatable(False)
        self.toolbar.toggleViewAction().setEnabled(False)
        self.toolbar.setContextMenuPolicy(Qt.ContextMenuPolicy.PreventContextMenu)
        self.toolbar.addWidget(self.bar)
        win.addToolBar(Qt.ToolBarArea.BottomToolBarArea, self.toolbar)
        self.bar.add_page(EDIT, "剪辑", "film", "剪辑界面：媒体库、画布、时间线、检查器")
        stack = getattr(win, "shell_stack", None)
        if stack is not None:
            stack.currentChanged.connect(lambda _i: self._sync_bar_visible())
        self._sync_bar_visible()

    # ── 注册 ──
    def register(self, key, title, icon_name, factory, *, keep_alive=True,
                 on_enter=None, on_leave=None, tip=""):
        room = _Room(key, title, icon_name, factory, keep_alive, on_enter, on_leave, tip)
        self.rooms[key] = room
        self.bar.add_page(key, title, icon_name, tip)
        return room

    def finish_setup(self):
        self.bar.finish()
        self.bar.set_current(self.current)

    def widget(self, key):
        room = self.rooms.get(key)
        return room.widget if room is not None else None

    # ── 切换 ──
    def _is_on_welcome(self) -> bool:
        win = self.win
        welcome = getattr(win, "welcome_page", None)
        stack = getattr(win, "shell_stack", None)
        return bool(stack is not None and welcome is not None and stack.currentWidget() is welcome)

    def toggle_welcome(self) -> None:
        """点击底部「欢迎向导」图标：在欢迎页与剪辑台之间一键收起/展开。"""
        win = self.win
        if self._is_on_welcome():
            if hasattr(win, "show_editor"):
                win.show_editor()
        else:
            if hasattr(win, "show_welcome"):
                win.show_welcome()
        self._sync_bar_visible()

    def enter(self, key: str, **kw) -> bool:
        """进某个房间（``edit`` = 回剪辑）。kw 交给房间的 factory / on_enter。"""
        if self._switching:
            return False
        if key != EDIT and key not in self.rooms:
            return False
        if key == self.current:
            if key == EDIT and self._is_on_welcome():
                if hasattr(self.win, "show_editor"):
                    self.win.show_editor()
                self._sync_bar_visible()
                return True
            room = self.rooms.get(key)
            if room is not None and room.on_enter is not None and room.widget is not None and kw:
                room.on_enter(room.widget, **kw)
            return True
        self._switching = True
        try:
            prev = self.current
            if prev != EDIT:
                self._leave_room(prev)
            if key == EDIT:
                self._show_editor()
            else:
                if not self._show_room(key, kw):
                    if prev != EDIT:
                        self._show_editor()
                    self.current = EDIT
                    self.bar.set_current(EDIT)
                    return False
            self.current = key
            self.bar.set_current(key)
            self._sync_badges()
            self._sync_bar_visible()
            _LOG.info("[Room] %s → %s", prev, key)
        finally:
            self._switching = False
        self.room_changed.emit(key)
        return True

    def close_room(self, key: str, back: str = EDIT) -> None:
        """房间自己点了「完成 / 关闭」：拆掉它，回到 back。"""
        room = self.rooms.get(key)
        if room is None:
            return
        if self.current == key:
            self.enter(back if back in self.rooms or back == EDIT else EDIT)
        self._destroy(room)
        self._sync_badges()

    def shutdown(self):
        """关软件：回剪辑页，所有房间拆掉（试听停、解码器放掉）。"""
        if self.current != EDIT:
            self.enter(EDIT)
        for room in self.rooms.values():
            if room.widget is not None:
                if room.on_leave is not None:
                    try:
                        room.on_leave(room.widget)
                    except Exception:
                        _LOG.exception("关闭时房间 %s 清理失败", room.key)
                self._destroy(room)

    def _leave_room(self, key):
        room = self.rooms.get(key)
        if room is None or room.widget is None:
            return
        if room.on_leave is not None:
            try:
                room.on_leave(room.widget)
            except Exception:
                _LOG.exception("房间 %s 离开时清理失败", key)
        if not room.keep_alive:
            self._destroy(room)

    def _destroy(self, room):
        w = room.widget
        box = room.container or w
        room.widget = None
        room.container = None
        if w is None:
            return
        try:
            stack = self.win.shell_stack
            if stack.indexOf(box) >= 0:
                stack.removeWidget(box)
        except Exception:
            pass
        try:
            box.hide()
            box.deleteLater()
        except RuntimeError:
            pass

    def _show_room(self, key, kw) -> bool:
        room = self.rooms[key]
        win = self.win
        if room.widget is None:
            try:
                from cutora.ui.widgets.busy_bar import finish, start_busy
                start_busy(win, "正在打开%s" % room.title)
            except Exception:
                finish = None
            try:
                room.widget = room.factory(win, **kw)
            except Exception:
                _LOG.exception("建房间 %s 失败", key)
                room.widget = None
            finally:
                if finish is not None:
                    try:
                        finish()
                    except Exception:
                        pass
            if room.widget is None:
                return False
            room.container = _scroll_box(room.widget)
            win.shell_stack.addWidget(room.container)
        elif room.on_enter is not None:
            try:
                verdict = room.on_enter(room.widget, **kw)
            except Exception:
                _LOG.exception("房间 %s 进入时刷新失败", key)
                verdict = None
            if verdict == "rebuild":
                # 工程里的视频换了（导入 / 删除 / 重新打开）：留着的房间对不上了，重建
                self._destroy(room)
                return self._show_room(key, kw)
        if self.current == EDIT:
            self._hide_editor()
        win.shell_stack.setCurrentWidget(room.container or room.widget)
        room.widget.show()
        return True

    # ── 剪辑页的收起 / 恢复 ──
    def _docks(self):
        win = self.win
        out = []
        for name in ("dock_left", "dock_right", "dock_timeline", "dock_keyframes", "dock_nodes"):
            d = getattr(win, name, None)
            if d is not None:
                out.append(d)
        return out

    def _hide_editor(self):
        win = self.win
        self._editor_index = win.shell_stack.currentIndex()
        try:
            self._dock_state = win.saveState()
        except Exception:
            self._dock_state = None
        self._dock_visible = [d for d in self._docks() if d.isVisible()]
        for d in self._dock_visible:
            d.hide()
        qt = getattr(win, "quick_toolbar", None)
        self._quick_visible = bool(qt is not None and qt.isVisible())
        if qt is not None:
            qt.hide()
        self._pause_editor()
        self._mute_shortcuts(True)

    def _show_editor(self):
        win = self.win
        self._mute_shortcuts(False)
        idx = getattr(self, "_editor_index", None)
        if isinstance(idx, int) and 0 <= idx < win.shell_stack.count():
            win.shell_stack.setCurrentIndex(idx)
        else:
            host = getattr(win, "editor_host", None)
            if host is not None:
                win.shell_stack.setCurrentWidget(host)
        for d in self._dock_visible:
            d.show()
        qt = getattr(win, "quick_toolbar", None)
        if qt is not None and self._quick_visible:
            qt.show()
        if self._dock_state is not None:
            try:
                win.restoreState(self._dock_state)
            except Exception:
                pass
        self._dock_visible = []
        self._dock_state = None
        # 房间里改过工程（批量加载 / 监视器）：剪辑页重画一次
        for fn in ("refresh_ui",):
            f = getattr(win, fn, None)
            if callable(f):
                try:
                    f()
                except Exception:
                    _LOG.exception("回到剪辑时刷新失败")
        try:
            win.timeline.update()
            win.video_canvas.update()
        except Exception:
            pass
        try:
            win._sync_idle_task = None       # 空闲停帧作废，下一拍重新出帧
        except Exception:
            pass

    def _pause_editor(self):
        win = self.win
        try:
            if bool(getattr(win, "_timeline_playing", False)):
                win.toggle_play()
        except Exception:
            _LOG.exception("进房间时暂停预览失败")

    def _mute_shortcuts(self, on: bool):
        """剪辑页的快捷键在房间里让出来：QAction 暂时清掉键位（菜单照样能点），QShortcut 停用。"""
        if on and not self._room_gate:
            self._room_gate = True
            self.gate.acquire()
        elif not on and self._room_gate:
            self._room_gate = False
            self.gate.release()

    @property
    def _muted(self):
        return self.gate.muted

    # ── 页面栏 ──
    def _sync_badges(self):
        for key, room in self.rooms.items():
            self.bar.set_badge(key, room.widget is not None and room.keep_alive and key != self.current)

    def _sync_bar_visible(self):
        on_welcome = self._is_on_welcome() and self.current == EDIT
        self.toolbar.setVisible(True)
        wbtn = getattr(self.bar, "welcome_btn", None)
        if wbtn is not None:
            wbtn.setChecked(on_welcome)
        if on_welcome:
            self.bar.set_current("")
        else:
            self.bar.set_current(self.current)


def _scroll_box(widget):
    """房间外面套一层滚动区：窗口比房间的最小尺寸还小时出滚动条，而不是把主窗口撑到屏幕外。

    （QStackedWidget 的最小尺寸取所有页里最大的：不套的话，切回剪辑页主窗口也被撑着缩不回来。）"""
    from PySide6.QtWidgets import QFrame, QScrollArea
    box = QScrollArea()
    box.setObjectName("roomScroll")
    box.setWidgetResizable(True)
    box.setFrameShape(QFrame.Shape.NoFrame)
    box.setMinimumSize(1, 1)
    box.setWidget(widget)
    box.viewport().setAutoFillBackground(False)
    return box


# ───────────────────────────── 具体房间 ─────────────────────────────

def _make_batch_room(win, **_kw):
    from cutora.batch.batch_dialog import build_batch_dialog
    dlg = build_batch_dialog(win)
    embed_dialog(dlg)
    dlg._room_win = win
    host = win.room_host
    dlg.finished.connect(lambda _c=0: host.close_room("batch"))
    return dlg


def _leave_batch_room(dlg):
    """批量加载切走（没关）：试听停掉、预览解码器放掉；表格 / 勾选原样留着。"""
    tab = getattr(dlg, "text_tab", None)
    fn = getattr(tab, "stop_play", None)
    if callable(fn):
        fn()
    audio = getattr(dlg, "audio_tab", None)
    stop_au = getattr(audio, "stop_audition", None)
    if callable(stop_au):
        stop_au()
    canvas = getattr(dlg, "preview_canvas", None)
    stop = getattr(canvas, "_stop_all_decoders", None)
    if callable(stop):
        stop()


def _enter_batch_room(dlg, **_kw):
    """切回批量加载：工程里的视频没变就原样继续；变了（导入 / 删除 / 换工程）就重建。"""
    if not _same_tasks(dlg, getattr(dlg, "_room_win", None)):
        return "rebuild"
    tab = getattr(dlg, "text_tab", None)
    fn = getattr(tab, "preview_plans", None)
    if callable(fn):
        fn()
    return None


def _make_monitor_room(win, tasks=None, back=None, on_edited=None, **_kw):
    from cutora.ui.central_monitor import CentralMonitorDialog
    if tasks is None:
        tasks = monitor_tasks_from_selection(win)
    dlg = CentralMonitorDialog(win, list(tasks), parent=None)
    embed_dialog(dlg)
    _wire_monitor(dlg, win, back, on_edited)
    return dlg


def _wire_monitor(dlg, win, back, on_edited):
    dlg._room_back = back or EDIT
    old = getattr(dlg, "_room_edited_cb", None)
    if old is not None:
        try:
            dlg.edited.disconnect(old)
        except (RuntimeError, TypeError):
            pass
    dlg._room_edited_cb = None
    if callable(on_edited):
        dlg._room_edited_cb = on_edited
        dlg.edited.connect(on_edited)
    dlg.finished.connect(lambda _c=0, d=dlg: win.room_host.close_room("monitor", back=getattr(d, "_room_back", EDIT)))


def _enter_monitor_room(dlg, tasks=None, back=None, on_edited=None, **_kw):
    if tasks is not None:
        dlg.set_tasks(list(tasks))
    if back is not None:
        dlg._room_back = back
    try:
        dlg._sync_with_host()
        dlg.grid.refresh_all()
    except Exception:
        _LOG.exception("监视器刷新失败")


def _leave_monitor_room(dlg):
    try:
        dlg._teardown()
    except Exception:
        _LOG.exception("监视器收尾失败")


def _same_tasks(dlg, win) -> bool:
    picker = getattr(dlg, "picker", None)
    tasks = list(getattr(win, "tasks", None) or [])
    have = list(getattr(picker, "_tasks", None) or [])
    return len(have) == len(tasks) and all(a is b for a, b in zip(have, tasks))


def _add_exit_button(dlg, win, key, busy=None, stop=None):
    """原来靠窗口标题栏 ✕ 关的对话框：嵌进房间后在底部右下角补一个「完成并退出」。"""
    from PySide6.QtWidgets import QHBoxLayout, QMessageBox, QPushButton
    lay = dlg.layout()
    if lay is None:
        return None
    row = QHBoxLayout()
    row.addStretch(1)
    btn = QPushButton("完成并退出")
    btn.setToolTip("回到剪辑界面")

    def _go():
        if callable(busy) and busy():
            r = QMessageBox.question(
                dlg, "还在处理",
                "还在处理中。退出会停止当前任务，已经完成的部分保留。\n确定退出吗？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if r != QMessageBox.StandardButton.Yes:
                return
            if callable(stop):
                try:
                    stop()
                except Exception:
                    _LOG.exception("房间 %s 停止任务失败", key)
        dlg.reject()

    btn.clicked.connect(_go)
    row.addWidget(btn)
    lay.addLayout(row)
    dlg.btn_room_exit = btn
    return btn


def _make_stitch_room(win, **_kw):
    from cutora.batch.script_stitcher import StitcherDialog
    from cutora.ui import main_window as MW
    dlg = StitcherDialog(win, MW.get_whisperx_model, MW.get_align_model, win._on_stitcher_done)
    embed_dialog(dlg)

    def _stop():
        sess = getattr(dlg, "_session", None)
        if sess is not None and sess.busy():
            sess.stop()
        for name in ("worker", "enc_worker", "pf_worker"):
            w = getattr(dlg, name, None)
            if w is not None and w.isRunning() and hasattr(w, "stop"):
                w.stop()

    _add_exit_button(dlg, win, "stitch", busy=dlg._busy, stop=_stop)
    dlg.finished.connect(lambda _c=0: win.room_host.close_room("stitch"))
    return dlg


def monitor_tasks_from_selection(win):
    """媒体库里多选了就看选中的，否则看全部（和以前菜单打开一样）。"""
    tasks = list(getattr(win, "tasks", None) or [])
    try:
        rows = sorted({win.file_list.row(it) for it in win.file_list.selectedItems()})
    except Exception:
        rows = []
    if len(rows) > 1:
        tasks = [win.tasks[r] for r in rows if 0 <= r < len(win.tasks)]
    return tasks


def _make_ai_studio_room(win, **kw):
    from cutora.ui.ai_studio_room import AiStudioRoom
    dlg = AiStudioRoom(win, parent=None)
    embed_dialog(dlg)
    dlg.finished.connect(lambda _c=0: win.room_host.close_room("ai_studio"))
    if kw.get("tab") is not None:
        try:
            dlg.tabs.setCurrentIndex(int(kw["tab"]))
        except Exception:
            pass
    return dlg


def _enter_ai_studio_room(dlg, **kw):
    try:
        dlg.refresh_from_editor()
        if kw.get("tab") is not None:
            dlg.tabs.setCurrentIndex(int(kw["tab"]))
    except Exception:
        _LOG.exception("AI 工坊刷新失败")
    return None


def _has_unconsumed_stitch_handoff() -> bool:
    try:
        import json as _json
        import os as _os
        from pathlib import Path as _Path
        p = str(_Path(__file__).resolve().parents[4] / "data" / "cutora_stitch_handoff.json")
        if not _os.path.isfile(p):
            return False
        with open(p, "r", encoding="utf-8") as f:
            d = _json.load(f)
        return bool(isinstance(d, dict) and not d.get("consumed") and d.get("clip_dirs"))
    except Exception:
        return False


def _enter_stitch_room(dlg, **_kw):
    try:
        if hasattr(dlg, "_busy") and not dlg._busy() and hasattr(dlg, "_sync_from_orante"):
            has_handoff = _has_unconsumed_stitch_handoff()
            scripts, _ = dlg._collect_scripts() if hasattr(dlg, "_collect_scripts") else ({}, [])
            if has_handoff or not scripts:
                dlg._sync_from_orante(quiet=not has_handoff)
    except Exception:
        _LOG.exception("智能拼接房间同步 Orante 失败")
    return None


def _make_silence_room(win, **kw):
    from cutora.ui.silence_cut_dialog import SilenceCutDialog
    batch_def = bool(kw.get("batch_default", False))
    dlg = SilenceCutDialog(win, parent=None, batch_default=batch_def)
    embed_dialog(dlg)
    dlg.finished.connect(lambda _c=0: win.room_host.close_room("silence"))
    return dlg


def _enter_silence_room(dlg, **kw):
    try:
        if "batch_default" in kw and hasattr(dlg, "cb_scope"):
            dlg.cb_scope.setCurrentIndex(1 if kw["batch_default"] else 0)
        if hasattr(dlg, "_populate_initial_task_overview") and not getattr(dlg, "_busy", False):
            dlg._populate_initial_task_overview()
    except Exception:
        _LOG.exception("剪气口房间刷新失败")
    return None


def install(win) -> RoomHost:
    """MainWindow 建好外壳后调一次。"""
    host = RoomHost(win)
    win.room_host = host
    host.register("batch", "批量加载", "layers", _make_batch_room, keep_alive=True,
                  on_enter=_enter_batch_room, on_leave=_leave_batch_room,
                  tip="批量往勾选的视频里加图片 / 视频 / 文本 / 语音转文本")
    host.register("monitor", "中央监视器", "eye", _make_monitor_room, keep_alive=False,
                  on_enter=_enter_monitor_room, on_leave=_leave_monitor_room,
                  tip="一屏看全部视频，直接在画面上调分屏、平移缩放、搬文字、拖素材（Ctrl+Shift+M）")
    host.register("stitch", "片段拼接", "link", _make_stitch_room, keep_alive=True,
                  on_enter=_enter_stitch_room,
                  tip="片段智能拼接：按文案把素材片段拼成时间线工程（切走时后台照样跑）")
    host.register("silence", "剪气口", "scissors", _make_silence_room, keep_alive=True,
                  on_enter=_enter_silence_room,
                  tip="智能剪气口工作台：三路信号防误伤、全局鸟瞰+局部切点显微镜、支持批量停顿裁剪与无损自检")
    host.register("ai_studio", "✨ AI 工坊", "sparkles", _make_ai_studio_room, keep_alive=True,
                  on_enter=_enter_ai_studio_room,
                  tip="本地 AI 视频生成 (LTX-2.5) · 去人物/去话筒 (SAM 2.1) · 深度音频降噪 (Resemble) · Simone 人像精修与 3D LUT")
    host.finish_setup()
    try:
        from cutora.ui.inwindow import install as _install_sheets
        _install_sheets(win)
    except Exception:
        _LOG.exception("弹窗进主窗口安装失败")
    try:
        import os as _os
        from PySide6.QtCore import QTimer as _QTimer
        _open_proj = (_os.environ.get("CUTORA_OPEN_PROJECT") or "").strip()
        if _open_proj and _os.path.isfile(_open_proj) and hasattr(win, "command_project_open"):
            _QTimer.singleShot(250, lambda p=_open_proj: win.command_project_open(path=p))
        _start_room = (_os.environ.get("CUTORA_START_ROOM") or "").strip()
        if _start_room in ("stitch", "ai_studio", "batch", "monitor"):
            _QTimer.singleShot(350, lambda r=_start_room: host.enter(r))

        def _poll_stitch_handoff():
            try:
                if not _has_unconsumed_stitch_handoff():
                    return
                if hasattr(win, "raise_") and hasattr(win, "activateWindow"):
                    win.showNormal()
                    win.raise_()
                    win.activateWindow()
                host.enter("stitch")
                room_obj = host.rooms.get("stitch")
                dlg = getattr(room_obj, "widget", None) if room_obj else None
                if dlg is not None and hasattr(dlg, "_busy") and not dlg._busy() and hasattr(dlg, "_sync_from_orante"):
                    dlg._sync_from_orante(quiet=False)
            except Exception:
                pass

        _ht = _QTimer(win)
        _ht.setInterval(1500)
        _ht.timeout.connect(_poll_stitch_handoff)
        _ht.start()
        win._stitch_handoff_timer = _ht
    except Exception:
        pass
    return host
