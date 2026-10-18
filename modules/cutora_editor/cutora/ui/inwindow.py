# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q192 · 所有弹窗都在主窗口里面（浮层 / 侧滑面板），不再是独立的系统窗口。

Luca：子窗口一开，主窗口就钉在后面拖不动；看别的程序（比如 Google 表格）被挡住。
第二期把批量加载 / 中央监视器做成了房间；这一期把剩下的弹窗也收进主窗口：

- 设置类（设置、AI 提供商、空间立体声 / 音效库、字体管理…）：从右侧滑出的整高面板；
- ``cutora_sheet`` = ``fill``：铺满主窗口客户区，主窗口拉大缩小时它跟着变（视频连线导入、图片节点连线一次要对几十条）；
- 其它对话框、确认框、提示框、输入框、取色器：窗口中间的浮层卡片，背后压一层暗幕（点不到下面的东西）；
- 文件选择框保持系统原生（Windows 资源管理器那个，行业惯例）；独立导出监视器和它自己的提示框照旧在外面。

做法：在主窗口建好后给 ``QDialog.exec`` 和 ``QMessageBox`` / ``QInputDialog`` / ``QColorDialog`` 的静态函数
各套一层：对话框属于主窗口（或没有父窗口）时，把它当成普通子控件放进主窗口的浮层里，跑一个本地事件循环，
等它 ``finished`` 再返回 —— 调用方的代码（``if dlg.exec() == Accepted``、``QMessageBox.question(...)``）一行不用改。
浮层开着时，剪辑页的快捷键暂时让出来（空格不会去播放），关掉就恢复。
"""
from __future__ import annotations

import weakref

from PySide6.QtCore import QEvent, QEventLoop, QObject, QRect, Qt, QTimer
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QApplication,
    QColorDialog,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from cutora.core.log import get_logger

_LOG = get_logger("cutora.ui.inwindow")

_STATE = {"win": None, "orig": {}, "installed": False}
SHEET_MARGIN = 48
RIGHT_CLASSES = frozenset({
    "SettingsDialog", "AIProvidersDialog", "AudioFxDialog", "FontManagerDialog",
    "LanguageSupportDialog", "ConsoleView", "GlossaryDialog",
})


# ───────────────────────────── 浮层 ─────────────────────────────

class _Layer(QWidget):
    """一层：暗幕 + 卡片。暗幕吃掉鼠标，下面的界面点不到（和以前的模态一样）。"""

    def __init__(self, host, dlg, placement, modal=True):
        super().__init__(host)
        self.dlg = dlg
        self.placement = placement
        self.modal = bool(modal)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self.card = QFrame(self)
        self.card.setObjectName("sheetCard")
        lay = QVBoxLayout(self.card)
        lay.setContentsMargins(16, 8, 16, 16)
        lay.setSpacing(8)
        head = QHBoxLayout()
        head.setSpacing(8)
        self.title = QLabel(dlg.windowTitle() or "")
        self.title.setObjectName("sheetTitle")
        self.btn_x = QPushButton("✕")
        self.btn_x.setObjectName("sheetClose")
        self.btn_x.setFixedSize(28, 28)
        self.btn_x.setToolTip("关闭（Esc）")
        self.btn_x.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_x.clicked.connect(dlg.reject)
        head.addWidget(self.title, 1)
        head.addWidget(self.btn_x)
        lay.addLayout(head)
        lay.addWidget(dlg, 1)
        dlg.windowTitleChanged.connect(self.title.setText)

    def desired(self):
        d = self.dlg
        hint = d.size() if d.testAttribute(Qt.WidgetAttribute.WA_Resized) else d.sizeHint()
        w = max(hint.width(), d.minimumSizeHint().width(), d.minimumWidth(), 280)
        h = max(hint.height(), d.minimumSizeHint().height(), d.minimumHeight(), 80)
        return w + 32, h + 10 + 14 + 36

    def relayout(self):
        host = self.parentWidget()
        r = host.rect()
        w, h = self.desired()
        if self.placement == "right":
            cw = int(min(max(w, 440), max(440, r.width() * 0.5)))
            cw = min(cw, r.width() - 16)
            card = QRect(r.width() - cw - 8, 8, cw, r.height() - 16)
        elif self.placement == "fill":
            # 铺满客户区，只留 8px 让圆角和暗幕还看得见。窗口一变，eventFilter 会再排一次。
            inset = 8
            cw = min(r.width(), max(240, r.width() - 2 * inset))
            ch = min(r.height(), max(160, r.height() - 2 * inset))
            card = QRect((r.width() - cw) // 2, (r.height() - ch) // 2, cw, ch)
        else:
            maxw = max(240, r.width() - 2 * SHEET_MARGIN)
            maxh = max(160, r.height() - 2 * SHEET_MARGIN)
            cw, ch = min(w, maxw), min(h, maxh)
            card = QRect((r.width() - cw) // 2, (r.height() - ch) // 2, cw, ch)
        if self.modal:
            self.setGeometry(r)
            self.card.setGeometry(card)
        else:
            # 不阻塞的面板（控制台 / 语言支持 / 快捷键说明）：只占卡片那一块，旁边照常能点
            self.setGeometry(card)
            self.card.setGeometry(QRect(0, 0, card.width(), card.height()))

    def paintEvent(self, ev):
        if not self.modal:
            return
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(0, 0, 0, 115))
        p.end()

    def mousePressEvent(self, ev):
        ev.accept()            # 点暗幕不关（防误点丢掉填了一半的东西）；关用 ✕ / Esc / 按钮

    def mouseReleaseEvent(self, ev):
        ev.accept()

    def wheelEvent(self, ev):
        ev.accept()


class OverlayHost(QWidget):
    """主窗口上方的透明层，装着一层层浮层。没有浮层时隐藏（不吃任何事件）。"""

    def __init__(self, win):
        super().__init__(win)
        self.setObjectName("overlayHost")
        self.win = win
        self.layers: list[_Layer] = []
        self.hide()
        win.installEventFilter(self)

    def eventFilter(self, obj, ev):
        if obj is self.win and ev.type() in (QEvent.Type.Resize, QEvent.Type.Show):
            if self.layers:
                self._relayout()
        return False

    def _relayout(self):
        self.setGeometry(self.win.rect())
        for layer in self.layers:
            layer.relayout()
        self._sync_mask()

    def _sync_mask(self):
        """只有不阻塞的面板时，透明层只在面板那一块接鼠标，其余地方点得到下面的界面。"""
        from PySide6.QtGui import QRegion
        if any(layer.modal for layer in self.layers):
            self.clearMask()
            return
        region = QRegion()
        for layer in self.layers:
            region = region.united(QRegion(layer.geometry()))
        if region.isEmpty():
            self.clearMask()
        else:
            self.setMask(region)

    def push(self, dlg, placement="center", modal=True) -> _Layer:
        layer = _Layer(self, dlg, placement, modal=modal)
        self.layers.append(layer)
        self.show()
        self.raise_()
        self._relayout()
        layer.show()
        layer.raise_()
        return layer

    def pop(self, layer):
        if layer in self.layers:
            self.layers.remove(layer)
        try:
            layer.hide()
            layer.deleteLater()
        except RuntimeError:
            pass
        if not self.layers:
            self.clearMask()
            self.hide()
        else:
            self.layers[-1].raise_()
            self._sync_mask()


def _host(win) -> OverlayHost:
    h = getattr(win, "_overlay_host", None)
    if h is None:
        h = OverlayHost(win)
        win._overlay_host = h
    return h


# ───────────────────────────── 哪些对话框进主窗口 ─────────────────────────────

def main_window():
    ref = _STATE.get("win")
    return ref() if ref is not None else None


def host_window_for(dlg):
    """这个对话框该进哪个主窗口；返回 None = 照旧做系统窗口。"""
    win = main_window()
    if win is None:
        return None
    try:
        if not win.isVisible() or win.isMinimized():
            return None
        if isinstance(dlg, QFileDialog):
            return None
        if dlg.property("cutora_external"):
            return None
        if dlg.windowFlags() & Qt.WindowType.WindowStaysOnTopHint:
            return None                     # 崩溃提示之类：必须单独顶在最上面
        if dlg.isVisible() and dlg.isWindow():
            return None                     # 已经作为独立窗口显示着了，不去拆它
        parent = dlg.parentWidget()
        if parent is None:
            return win
        top = parent.window()
        return win if top is win else None
    except RuntimeError:
        return None


def placement_for(dlg) -> str:
    want = dlg.property("cutora_sheet")
    if want in ("right", "center", "fill"):
        return str(want)
    return "right" if type(dlg).__name__ in RIGHT_CLASSES else "center"


# ───────────────────────────── exec 本体 ─────────────────────────────

def run_in_window(dlg, win=None) -> int:
    """把 dlg 放进主窗口浮层里跑到结束，返回 ``dlg.result()``（和 exec 一样）。"""
    win = win or host_window_for(dlg)
    if win is None:
        return _STATE["orig"]["exec"](dlg)
    host = _host(win)
    orig_parent = dlg.parentWidget()
    orig_flags = dlg.windowFlags()
    placement = placement_for(dlg)
    if isinstance(dlg, QMessageBox):
        try:
            from cutora.ui.i18n import localize_message_box
            localize_message_box(dlg)
        except Exception:
            pass
    dlg.setResult(0)
    dlg.setWindowFlags(Qt.WindowType.Widget)
    layer = host.push(dlg, placement)
    gate = _gate(win)
    if gate is not None:
        gate.acquire()
    loop = QEventLoop()
    done = {"v": False}

    def _finish(_r=0):
        done["v"] = True
        loop.quit()

    dlg.finished.connect(_finish)
    _LOG.info("[Sheet] 打开 %s「%s」(%s)", type(dlg).__name__, dlg.windowTitle(), placement)
    try:
        dlg.show()
        _focus_first(dlg)
        if not done["v"]:
            loop.exec()
    finally:
        try:
            dlg.finished.disconnect(_finish)
        except (RuntimeError, TypeError):
            pass
        if gate is not None:
            gate.release()
        host.pop(layer)
        try:
            dlg.hide()
            dlg.setParent(orig_parent, orig_flags)
            dlg.hide()
        except RuntimeError:
            pass
    try:
        res = dlg.result()
    except RuntimeError:
        res = 0
    _LOG.info("[Sheet] 关闭 %s → %s", type(dlg).__name__, res)
    if dlg.testAttribute(Qt.WidgetAttribute.WA_DeleteOnClose):
        dlg.deleteLater()
    return res


def show_sheet(dlg, win=None, modal=False):
    """不阻塞版（以前是 dlg.show() 的非模态窗口）：放进浮层，关掉（finished / 隐藏）就收起。
    默认不压暗幕、不抢快捷键 —— 旁边的界面照常能点。"""
    win = win or host_window_for(dlg) or main_window()
    if win is None:
        dlg.show()
        return None
    host = _host(win)
    if getattr(dlg, "_sheet_layer", None) is not None:
        return dlg._sheet_layer
    orig_parent = dlg.parentWidget()
    orig_flags = dlg.windowFlags()
    dlg.setWindowFlags(Qt.WindowType.Widget)
    layer = host.push(dlg, placement_for(dlg), modal=modal)
    gate = _gate(win) if modal else None
    if gate is not None:
        gate.acquire()
    dlg._sheet_layer = layer

    def _close(_r=0):
        if getattr(dlg, "_sheet_layer", None) is None:
            return
        dlg._sheet_layer = None
        try:
            dlg.finished.disconnect(_close)
        except (RuntimeError, TypeError):
            pass
        if gate is not None:
            gate.release()
        host.pop(layer)
        try:
            dlg.setParent(orig_parent, orig_flags)
            dlg.hide()
        except RuntimeError:
            pass

    dlg.finished.connect(_close)
    dlg.show()
    _focus_first(dlg)
    return layer


def _focus_first(dlg):
    try:
        if isinstance(dlg, QMessageBox):
            b = dlg.defaultButton()
            if b is not None:
                b.setFocus()
                return
        w = dlg.focusWidget() or dlg.nextInFocusChain()
        (w or dlg).setFocus(Qt.FocusReason.OtherFocusReason)
    except RuntimeError:
        pass


def _gate(win):
    rooms = getattr(win, "room_host", None)
    return getattr(rooms, "gate", None)


# ───────────────────────────── 静态函数的替身 ─────────────────────────────

def _parent_ok(parent):
    return parent if isinstance(parent, QWidget) else None


def _target_ok(parent) -> bool:
    """静态函数（QMessageBox.warning 之类）该不该走主窗口浮层：主窗口在、看得见，父窗口属于它。"""
    win = main_window()
    if win is None:
        return False
    try:
        if not win.isVisible() or win.isMinimized():
            return False
        probe = _parent_ok(parent)
        return probe is None or probe.window() is win
    except RuntimeError:
        return False


def _msg(icon, parent, title, text, buttons=None, default=None):
    box = QMessageBox(_parent_ok(parent) or main_window())
    box.setIcon(icon)
    box.setWindowTitle(str(title or ""))
    box.setText(str(text or ""))
    if buttons is None:
        buttons = QMessageBox.StandardButton.Ok
    box.setStandardButtons(buttons)
    if default is not None and default != QMessageBox.StandardButton.NoButton:
        box.setDefaultButton(default)
    try:
        from cutora.ui.i18n import localize_message_box
        localize_message_box(box)                 # 是 / 否 / 确定 / 取消 / 保存 …
    except Exception:
        pass
    r = box.exec()
    clicked = box.clickedButton()
    if clicked is not None:
        sb = box.standardButton(clicked)
        if sb != QMessageBox.StandardButton.NoButton:
            return sb
    # 没点按钮就关了（Esc / ✕ / 代码里 accept）：接受 = 默认按钮，否则 = Esc 按钮
    if int(r) == int(QDialog.DialogCode.Accepted):
        b = box.defaultButton() or (box.buttons()[0] if box.buttons() else None)
    else:
        b = box.escapeButton()
    if b is not None:
        sb = box.standardButton(b)
        if sb != QMessageBox.StandardButton.NoButton:
            return sb
    try:
        sb = QMessageBox.StandardButton(int(r))
        if sb in (QMessageBox.StandardButton.NoButton,):
            raise ValueError
        return sb
    except Exception:
        return QMessageBox.StandardButton.Cancel


def _msg_args(args, kwargs, default_buttons):
    parent = args[0] if len(args) > 0 else kwargs.get("parent")
    title = args[1] if len(args) > 1 else kwargs.get("title", "")
    text = args[2] if len(args) > 2 else kwargs.get("text", "")
    buttons = args[3] if len(args) > 3 else kwargs.get("buttons", default_buttons)
    default = args[4] if len(args) > 4 else kwargs.get("defaultButton", None)
    return parent, title, text, buttons, default


def _make_static(icon, default_buttons, orig_name):
    def _fn(*args, **kwargs):
        try:
            parent, title, text, buttons, default = _msg_args(args, kwargs, default_buttons)
        except Exception:
            return _STATE["orig"][orig_name](*args, **kwargs)
        if not _target_ok(parent):
            return _STATE["orig"][orig_name](*args, **kwargs)
        return _msg(icon, parent, title, text, buttons, default)
    _fn.__name__ = orig_name
    return _fn


def _input(parent, title, label, setup):
    dlg = QInputDialog(_parent_ok(parent) or main_window())
    dlg.setWindowTitle(str(title or ""))
    dlg.setLabelText(str(label or ""))
    setup(dlg)
    ok = dlg.exec() == QDialog.DialogCode.Accepted
    return dlg, ok


def get_text(parent, title, label, echo=QLineEdit.EchoMode.Normal, text="", *a, **kw):
    text = kw.get("text", text)
    echo = kw.get("echo", echo)

    def _s(d):
        d.setInputMode(QInputDialog.InputMode.TextInput)
        d.setTextEchoMode(echo)
        d.setTextValue(str(text or ""))
    dlg, ok = _input(parent, title, label, _s)
    return (dlg.textValue() if ok else ""), ok


def get_item(parent, title, label, items, current=0, editable=True, *a, **kw):
    current = kw.get("current", current)
    editable = kw.get("editable", editable)
    items = [str(x) for x in (items or [])]

    def _s(d):
        d.setComboBoxItems(items)
        d.setComboBoxEditable(bool(editable))
        if 0 <= int(current) < len(items):
            d.setTextValue(items[int(current)])
    dlg, ok = _input(parent, title, label, _s)
    return (dlg.textValue() if ok else (items[int(current)] if 0 <= int(current) < len(items) else "")), ok


def get_int(parent, title, label, value=0, minValue=-2147483647, maxValue=2147483647, step=1, *a, **kw):
    value = kw.get("value", value)
    minValue = kw.get("minValue", kw.get("min", minValue))
    maxValue = kw.get("maxValue", kw.get("max", maxValue))
    step = kw.get("step", step)

    def _s(d):
        d.setInputMode(QInputDialog.InputMode.IntInput)
        d.setIntRange(int(minValue), int(maxValue))
        d.setIntStep(int(step))
        d.setIntValue(int(value))
    dlg, ok = _input(parent, title, label, _s)
    return (dlg.intValue() if ok else int(value)), ok


def get_double(parent, title, label, value=0.0, minValue=-2147483647.0, maxValue=2147483647.0,
               decimals=1, *a, **kw):
    value = kw.get("value", value)
    minValue = kw.get("minValue", kw.get("min", minValue))
    maxValue = kw.get("maxValue", kw.get("max", maxValue))
    decimals = kw.get("decimals", decimals)

    def _s(d):
        d.setInputMode(QInputDialog.InputMode.DoubleInput)
        d.setDoubleDecimals(int(decimals))
        d.setDoubleRange(float(minValue), float(maxValue))
        d.setDoubleValue(float(value))
    dlg, ok = _input(parent, title, label, _s)
    return (dlg.doubleValue() if ok else float(value)), ok


def get_color(initial=None, parent=None, title="", *a, **kw):
    initial = kw.get("initial", initial)
    parent = kw.get("parent", parent)
    title = kw.get("title", title)
    dlg = QColorDialog(_parent_ok(parent) or main_window())
    dlg.setOption(QColorDialog.ColorDialogOption.DontUseNativeDialog, True)
    if title:
        dlg.setWindowTitle(str(title))
    else:
        dlg.setWindowTitle("选择颜色")
    if initial is not None:
        try:
            dlg.setCurrentColor(QColor(initial))
        except Exception:
            pass
    if dlg.exec() == QDialog.DialogCode.Accepted:
        return dlg.selectedColor()
    return QColor()


# ───────────────────────────── 安装 ─────────────────────────────

def _patched_exec(self):
    win = host_window_for(self)
    if win is None:
        return _STATE["orig"]["exec"](self)
    return run_in_window(self, win)


def install(win) -> None:
    """MainWindow 建好后调一次（只在界面进程）。"""
    import os
    if os.environ.get("CUTORA_INWINDOW", "1") == "0":
        return                                  # 排障开关：弹窗照旧做独立窗口
    _STATE["win"] = weakref.ref(win)
    if _STATE["installed"]:
        return
    _STATE["installed"] = True
    orig = _STATE["orig"]
    _STATE["skipped"] = []
    if _is_native(QDialog.exec):
        orig["exec"] = QDialog.exec
        QDialog.exec = _patched_exec
    else:
        orig["exec"] = QDialog.exec
        _STATE["skipped"].append("exec")
    for name, icon, buttons in (
            ("warning", QMessageBox.Icon.Warning, QMessageBox.StandardButton.Ok),
            ("information", QMessageBox.Icon.Information, QMessageBox.StandardButton.Ok),
            ("critical", QMessageBox.Icon.Critical, QMessageBox.StandardButton.Ok),
            ("question", QMessageBox.Icon.Question,
             QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)):
        orig[name] = getattr(QMessageBox, name)
        if _is_native(orig[name]):
            setattr(QMessageBox, name, staticmethod(_make_static(icon, buttons, name)))
        else:
            _STATE["skipped"].append(name)       # 已经被别人（测试）换掉了：不覆盖
    for name, fn in (("getText", get_text), ("getItem", get_item),
                     ("getInt", get_int), ("getDouble", get_double)):
        orig["input_" + name] = getattr(QInputDialog, name)
        if _is_native(orig["input_" + name]):
            setattr(QInputDialog, name, staticmethod(_wrap_input(name, fn)))
        else:
            _STATE["skipped"].append(name)
    orig["getColor"] = QColorDialog.getColor
    if _is_native(orig["getColor"]):
        QColorDialog.getColor = staticmethod(_wrap_color())
    else:
        _STATE["skipped"].append("getColor")
    _LOG.info("[Sheet] 弹窗进主窗口：已接管 QDialog.exec / QMessageBox / QInputDialog / QColorDialog")


def _wrap_input(name, fn):
    def _f(*args, **kwargs):
        parent = args[0] if args else kwargs.get("parent")
        if not _target_ok(parent):
            return _STATE["orig"]["input_" + name](*args, **kwargs)
        return fn(*args, **kwargs)
    _f.__name__ = name
    return _f


def _wrap_color():
    def _f(*args, **kwargs):
        parent = kwargs.get("parent", args[1] if len(args) > 1 else None)
        if not _target_ok(parent):
            return _STATE["orig"]["getColor"](*args, **kwargs)
        return get_color(*args, **kwargs)
    _f.__name__ = "getColor"
    return _f


def _is_native(fn) -> bool:
    """Qt 自己的（C++ 绑定）函数，不是谁用 Python 换上去的。"""
    import types
    return not isinstance(fn, (types.FunctionType, types.MethodType, staticmethod))


def uninstall() -> None:
    """测试用：恢复原样。"""
    orig = _STATE["orig"]
    if not _STATE["installed"]:
        return
    skipped = set(_STATE.get("skipped") or [])
    if "exec" not in skipped:
        QDialog.exec = orig["exec"]
    for name in ("warning", "information", "critical", "question"):
        if name not in skipped:
            setattr(QMessageBox, name, orig[name])
    for name in ("getText", "getItem", "getInt", "getDouble"):
        if name not in skipped:
            setattr(QInputDialog, name, orig["input_" + name])
    if "getColor" not in skipped:
        QColorDialog.getColor = orig["getColor"]
    _STATE["installed"] = False
    _STATE["win"] = None
