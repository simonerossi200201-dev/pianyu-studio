# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q144 · 中央监视器：一屏看全部视频的批量结果，直接在画面上改。

交互（鼠标）
  左键拖分界线          调分屏比例（贴 1/3、1/2、2/3，按住 N 不吸附）；Alt+拖 = 旋转
  左键拖画面            平移这一格的内容（没开分屏就是主视频），靠近居中会吸住
  Ctrl + 左键拖画面     以按下点为中心缩放这一格（右 / 上 = 放大），贴 100%
  Ctrl + 滚轮           同上，一格一格地缩放
  左键拖文字 / 图片      移动；拖到别的视频上松手 = 移过去（原模原样）
  Alt + 拖文字 / 图片    复制（同一条里复制一份，或复制到别的视频）
  Ctrl + 拖文字 / 图片   以中心缩放（文字无损）
  Alt + 拖格子          和同一条视频的另一格交换
  双击                  分界线 → 均分；格子 → 重置平移缩放；标题 → 在主窗口打开
  点 / Ctrl+点 / Shift+点 / 空白处框选   选视频（多选后可同步编辑、批量粘贴、批量放素材）
  右键「放大这一格」      这一格放大靠左，其余收成右边一列，选中的视频仍一起改；「恢复网格」回到均匀格子
  右键分屏布局            一次改选中的视频，或一次改全部；含「左右完整」（整幅进画面，不裁切）
  右侧图层 / 右键复制图层  按层复制文本、图片、视频、音频；原时间贴到选中的视频，轨道不够就新开
  从素材库拖图片 / 视频进来   放到落点；落在已选中的视频上 = 放到所有选中的视频

交互（键盘）
  Delete 删除选中文字 / 图片    Ctrl+C / Ctrl+V 复制粘贴（文字图片，或整套分屏）
  Ctrl+Z / Ctrl+Y 撤销重做      方向键 微移（Shift ×10）    Ctrl+A 全选    Esc 取消 / 清空选择
  Enter 在主窗口打开            0 重置当前格              Ctrl + = / - 放大缩小缩略图

性能
  视频层在后台线程按缩略图分辨率合成（分屏遮罩也按小图算）。
  正在拖的那一格马上重画；其余格子只改数据，松手后屏幕里看得到的再一格一格补，
  解码器队列里最多留一格，画面外的不解码。文字 / 图片在界面线程叠上去，不重新解码。
  右键批量换布局、粘贴图层、撤销，都走这条路，避免一次打开几十路解码把机器拖死。
"""
from __future__ import annotations

import math
import os
import threading
import time

from PySide6.QtCore import QEvent, QPointF, QRectF, QSize, Qt, QThread, QTimer, Signal
from PySide6.QtGui import (
    QColor, QCursor, QFont, QFontMetrics, QImage, QPainter, QPainterPath, QPen, QPixmap,
)
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QDialog, QHBoxLayout, QLabel, QMenu, QPushButton,
    QScrollArea, QSlider, QSplitter, QSplitterHandle, QVBoxLayout, QWidget,
)

from cutora.batch import monitor_model as mm
from cutora.ui.monitor_key_menu import KeyMenuMixin
from cutora.ui.monitor_layer_ops import LayerOpsMixin
from cutora.ui.monitor_layer_props import LayerPropsMixin
from cutora.core.log import get_logger
from cutora.ui.theme.tokens import (
    FONT_LABEL, FONT_MICRO, RADIUS_CTL, SPACE_1, SPACE_2, SPACE_3, SPACE_4, SPACE_6,
)

_LOG = get_logger("cutora.ui.central_monitor")


class _MonitorSplitHandle(QSplitterHandle):
    """平时不画。指针放上来才出现一条细的强调色。"""

    def __init__(self, orientation, parent):
        super().__init__(orientation, parent)
        self._hot = False
        self.setMouseTracking(True)

    def enterEvent(self, ev):
        self._hot = True
        self.update()
        super().enterEvent(ev)

    def leaveEvent(self, ev):
        self._hot = False
        self.update()
        super().leaveEvent(ev)

    def paintEvent(self, _ev):
        if not self._hot:
            return
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            from cutora.ui.theme.tokens import current
            pen = QPen(QColor(current().accent))
            pen.setWidthF(1.0)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            rect = self.rect()
            x = rect.center().x()
            painter.drawLine(x, rect.top() + 6, x, rect.bottom() - 6)
        finally:
            painter.end()


class _MonitorSplit(QSplitter):
    def __init__(self, parent=None):
        super().__init__(Qt.Orientation.Horizontal, parent)
        self.setObjectName("monitorSplit")
        self.setHandleWidth(7)
        self.setChildrenCollapsible(False)

    def createHandle(self):
        return _MonitorSplitHandle(self.orientation(), self)


def _pick_file(parent, title, flt) -> str:
    """右键「外部图片 / 视频作为背景」的选文件框（测试里替换掉）。"""
    from PySide6.QtWidgets import QFileDialog
    path, _f = QFileDialog.getOpenFileName(parent, title, "", flt)
    return str(path or "")


class _Palette:
    """颜色每次画的时候从当前主题现取（T.ACCENT → current().accent），跟着换肤走。"""

    def __getattr__(self, name):
        from cutora.ui.theme import tokens
        try:
            return getattr(tokens.current(), name.lower())
        except Exception:
            return getattr(tokens, name)


T = _Palette()

HEADER_H = SPACE_4 + SPACE_1 + SPACE_1        # 24
GAP = SPACE_3 + SPACE_1                        # 16 横竖间距
MARGIN = SPACE_4
TILE_MIN = SPACE_6 * 4                         # 128
TILE_MAX = SPACE_6 * 20                        # 640
TILE_DEFAULT = SPACE_6 * 7                     # 224
RENDER_MAX_W = 720                             # 设备像素上限，再大也看不出区别
DECODE_GAP_MS = 40                             # 补下一格之前留一拍，队列里始终只有一格
BAR_H = SPACE_4                                # 16  Q154：每格底部的播放条（▶ + 可拖进度 + 时间）
PLAY_TICK_MS = 33                              # 播放时钟；真正的帧率由后台合成速度决定（跟不上就丢帧）
DRAG_THRESHOLD = 3
DIVIDER_TOL = 7                                # 屏幕像素
SNAP_SCREEN = 7
IDLE_RELEASE_SEC = 4.0                         # 渲染线程闲这么久就把解码器关掉
# Q156 显卡不爆：解码一律软解；开窗先只画屏幕里的格子、一格一格来、头几格之间多留一拍；
# 同时播放的格子有上限（超过解码器池子就会反复开关文件）；关窗 / 闲置时解码器一个一个放
MONITOR_POOL = 8                               # 监视器解码器池（软解，每个 ~30 MB 内存）
WARM_GAP_MS = 110                              # 开窗头几格之间的间隔（让第一批文件慢慢开）
WARM_COUNT = 6                                 # 前几格算「热身」
PLAY_MAX = MONITOR_POOL                        # 同时播放的格子上限
RELEASE_STEP_SEC = 0.04                        # 渐进释放：两次 release 之间歇一下


# ═════════════════════════ 渲染线程 ═════════════════════════

def _mmss(t: float) -> str:
    t = max(0.0, float(t or 0.0))
    m, s = divmod(int(t + 0.5), 60)
    return "%02d:%02d" % (m, s)


class _DeadCap:
    """打不开的素材：读不到帧，但调用方不会炸。"""

    def isOpened(self):
        return False

    def read(self):
        return False, None

    def grab(self):
        return False

    def get(self, _prop):
        return 0.0

    def set(self, _prop, _val):
        return False

    def release(self):
        return None


class _LazyCap:
    """用到时才真去打开的解码器。

    PreviewEngine 每画一帧都先 _cap_for 拿解码器，哪怕这帧已经在缓存里。
    监视器几十条视频轮着画，池子一小就反复开关文件（一次 1~2 秒）。
    代理把「只读几个属性」和「帧在缓存里」这两种情况挡在外面，不开文件。
    """

    _META: dict = {}
    _META_LOCK = threading.Lock()

    def __init__(self, lazy_pool, key, path):
        object.__setattr__(self, "_lazy_pool", lazy_pool)
        object.__setattr__(self, "_key", str(key))
        object.__setattr__(self, "path", path)
        object.__setattr__(self, "_cap", None)

    def _real(self):
        cap = object.__getattribute__(self, "_cap")
        if cap is None:
            import cv2
            key = object.__getattribute__(self, "_key")
            path = object.__getattribute__(self, "path")
            lazy_pool = object.__getattribute__(self, "_lazy_pool")
            fresh = key not in getattr(lazy_pool.real, "_caps", {})
            cap = lazy_pool.real.get(key, path) or _DeadCap()
            object.__setattr__(self, "_cap", cap)
            if fresh and cap.isOpened():
                lazy_pool.resync(key, cap)
            if cap.isOpened():
                meta = {}
                for prop in (cv2.CAP_PROP_FRAME_WIDTH, cv2.CAP_PROP_FRAME_HEIGHT,
                             cv2.CAP_PROP_FPS, cv2.CAP_PROP_FRAME_COUNT):
                    try:
                        meta[prop] = cap.get(prop)
                    except Exception:
                        pass
                with _LazyCap._META_LOCK:
                    _LazyCap._META[key] = meta
        return cap

    def _meta(self):
        with _LazyCap._META_LOCK:
            return _LazyCap._META.get(object.__getattribute__(self, "_key"))

    def isOpened(self):
        if object.__getattribute__(self, "_cap") is None and self._meta():
            return True
        return self._real().isOpened()

    def get(self, prop):
        if object.__getattribute__(self, "_cap") is None:
            meta = self._meta()
            if meta and prop in meta:
                return meta[prop]
        return self._real().get(prop)

    def __getattr__(self, name):
        return getattr(self._real(), name)

    def __setattr__(self, name, value):
        setattr(self._real(), name, value)


class _LazyPool:
    """CapturePool 外面包一层：发出去的是 _LazyCap。

    解码器被挤掉 / 闲时关掉以后重开，新解码器在第 0 帧，而引擎的帧状态还记着
    「解码到第几秒」—— 顺序往后取帧会从头 grab，拿到错的画面。重开时按状态里的
    decode_cursor 把新解码器 seek 回去（resync）。
    """

    def __init__(self, capacity=MONITOR_POOL, states=None):
        from cutora.preview.engine import CapturePool
        self.real = CapturePool(capacity, hw=False)      # Q156：软解，不碰显卡硬解会话
        self.states = states if states is not None else {}

    def get(self, key, path):
        return _LazyCap(self, key, path)

    def resync(self, key, cap):
        import cv2
        st = self.states.get(key)
        if not isinstance(st, dict):
            return
        cursor = st.get("decode_cursor")
        if cursor is None or float(cursor) < 0:
            return
        fps = float(st.get("src_fps") or 0.0) or float(cap.get(cv2.CAP_PROP_FPS) or 30.0)
        try:
            cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, int(round(float(cursor) * fps))))
        except Exception:
            st["decode_cursor"] = -1.0

    def clear(self):
        self.real.clear()

    def release_gradually(self, step_sec=RELEASE_STEP_SEC, should_stop=None):
        """解码器一个一个放，中间歇一拍 —— 几十个一起 release 会让驱动抖一下（黑屏的另一半原因）。"""
        real = self.real
        while True:
            try:
                more = real.release_one() if hasattr(real, "release_one") else False
            except Exception:
                _LOG.exception("释放解码器失败")
                more = False
            if not more:
                break
            if should_stop is not None and should_stop():
                continue                     # 关窗时也要放完，只是不再歇
            time.sleep(max(0.0, float(step_sec)))
        if not hasattr(real, "release_one"):
            real.clear()


def _make_engine():
    from cutora.preview.engine import PreviewEngine
    eng = PreviewEngine()
    try:
        eng.pool.clear()
    except Exception:
        pass
    eng.pool = _LazyPool(MONITOR_POOL, eng._states)
    return eng


def render_view_frame(engine, vt: dict, t: float) -> QImage | None:
    """view_task 在 t 的视频层（缩略图分辨率）。没有视频就退回按文件取帧。"""
    vw, vh = vt.get("dims") or (0, 0)
    fb = vt.get("_fallback_path")
    if fb:
        from cutora.batch.batch_preview import fitted_background
        return fitted_background(fb, t, int(vw), int(vh), "fill")
    dur = mm.task_duration(vt)
    tt = float(t or 0.0)
    if dur > 0 and tt >= dur:
        tt = max(0.0, dur - 0.05)
    if vt.get("_bands"):
        # Q163 统一叠压：每段视频带一张，格子合成时和文字 / 字幕交错着叠
        bands = engine.video_bands_at(vt, tt, bands=vt["_bands"])
        from cutora.render.visual_stack import band_copy
        return [band_copy(b) for b in bands if b is not None]
    img = engine.video_frame_at(vt, tt)
    if img is not None and not img.isNull():
        return img.copy()
    return None


class TileRenderer(QThread):
    """后台合成视频层。同一格的新请求顶掉旧的；优先级小的先画（0 = 正在拖的那格）。"""

    ready = Signal(object, int, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cond = threading.Condition()
        self._pending: dict = {}
        self._stop = False
        self._seq = 0
        self._fallback_cache: dict = {}

    def request(self, key, vt, t, gen, prio=1):
        with self._cond:
            self._seq += 1
            self._pending[key] = (int(prio), self._seq, vt, float(t), int(gen))
            self._cond.notify()

    def bump(self, key, prio):
        with self._cond:
            item = self._pending.get(key)
            if item is not None and item[0] > prio:
                self._pending[key] = (int(prio),) + item[1:]

    def pending_count(self) -> int:
        with self._cond:
            return len(self._pending)

    def cancel_all(self):
        with self._cond:
            self._pending.clear()

    def shutdown(self, timeout_ms=150):
        """叫停。只等一小会：正在合成的那一帧（镜头模糊 + 高分辨率可能要好几秒）让它在后台
        自己收尾，线程对象留在 _RENDERERS 里直到 finished —— 以前等 3 秒就扔掉引用，
        线程没停完 QThread 就被销毁，整个程序直接崩（Q154 事故：关监视器白屏后崩溃）。"""
        with self._cond:
            self._stop = True
            self._pending.clear()
            self._cond.notify()
        try:
            if self.isRunning():
                self.wait(max(1, int(timeout_ms)))
            if not self.isRunning():
                _RENDERERS.discard(self)
        except RuntimeError:              # 程序退出时 C++ 那边已经先删了：线程早就不在了
            _RENDERERS.discard(self)

    def _on_finished(self):
        _RENDERERS.discard(self)


    def run(self):
        engine = _make_engine()
        idle_since = None
        try:
            while True:
                release = False
                with self._cond:
                    while not self._stop and not self._pending:
                        self._cond.wait(0.25)
                        if idle_since is None:
                            idle_since = time.monotonic()
                        elif time.monotonic() - idle_since > IDLE_RELEASE_SEC:
                            release = True
                            break
                    if self._stop:
                        break
                    if release:
                        idle_since = None
                if release:
                    # 闲下来就把解码器还掉（每个 30 MB 上下）；最后一帧还在状态里，
                    # 同一时间点再画不用重开文件。一个一个放（Q156）。
                    try:
                        self._release_pool(engine)
                    except Exception:
                        _LOG.exception("释放解码器失败")
                    with self._cond:
                        while not self._stop and not self._pending:
                            self._cond.wait(0.25)
                        if self._stop:
                            break
                with self._cond:
                    if not self._pending:
                        continue
                    idle_since = None
                    key = min(self._pending, key=lambda k: self._pending[k][:2])
                    _prio, _seq, vt, t, gen = self._pending.pop(key)
                img = None
                try:
                    fb = vt.get("_fallback_path")
                    if fb:
                        ck = (fb, round(t, 2), tuple(vt.get("dims") or ()))
                        img = self._fallback_cache.get(ck)
                        if img is None:
                            img = render_view_frame(engine, vt, t)
                            if len(self._fallback_cache) > 64:
                                self._fallback_cache.clear()
                            self._fallback_cache[ck] = img
                    else:
                        img = render_view_frame(engine, vt, t)
                except Exception:
                    _LOG.exception("监视器渲染失败")
                self.ready.emit(key, gen, img)
        finally:
            try:
                self._release_pool(engine, closing=True)
                engine.close()
            except Exception:
                pass

    def _release_pool(self, engine, closing=False):
        pool = getattr(engine, "pool", None)
        fn = getattr(pool, "release_gradually", None)
        if callable(fn):
            fn(should_stop=(lambda: False) if closing else (lambda: bool(self._pending)))
        elif pool is not None:
            pool.clear()


# 渲染线程不挂在界面对象下面：窗口被父窗口连带销毁、或整个程序退出时，
# 先把线程停干净，免得「QThread: Destroyed while thread is still running」崩掉
_RENDERERS: set = set()
_QUIT_HOOKED = [False]


def _shutdown_all_renderers():
    """程序退出：这时候可以等，必须等干净（退出路上销毁还在跑的 QThread 一样会崩）。"""
    for r in list(_RENDERERS):
        try:
            r.shutdown(timeout_ms=8000)
            if r.isRunning():
                r.wait(8000)
        except Exception:
            pass


def _new_renderer() -> "TileRenderer":
    r = TileRenderer(None)
    _RENDERERS.add(r)
    r.finished.connect(r._on_finished)
    app = QApplication.instance()
    if app is not None and not _QUIT_HOOKED[0]:
        app.aboutToQuit.connect(_shutdown_all_renderers)
        _QUIT_HOOKED[0] = True
    return r


# ═════════════════════════ 格子 ═════════════════════════

class _Tile:
    __slots__ = (
        "idx", "task", "label", "cw", "ch", "rect", "head", "video", "gen", "req_gen",
        "comp", "comp_dirty", "hits", "t", "vsig", "osig", "err", "ov_layer", "ov_dirty",
        "playing", "own_t", "play_t0", "play_wall0", "busy", "req_w", "video_bands",
    )

    def __init__(self, idx, task):
        self.idx = idx
        self.task = task
        self.label = mm.task_label(task, idx)
        self.cw, self.ch = mm.canvas_size(task)
        self.rect = QRectF()
        self.head = QRectF()
        self.video = None
        self.gen = 1
        self.req_gen = 0
        self.req_w = -1
        self.video_bands = None
        self.comp = None
        self.comp_dirty = True
        self.ov_layer = None
        self.ov_dirty = True
        self.hits = []
        self.t = 0.0
        self.vsig = None
        self.osig = None
        self.err = ""
        # Q154：各格独立播放 / 拖进度。own_t = 这一格的时间是自己的，不跟顶部总滑块
        self.playing = False
        self.own_t = False
        self.play_t0 = 0.0
        self.play_wall0 = 0.0
        self.busy = False

    @property
    def k(self) -> float:
        return self.rect.width() / float(self.cw) if self.cw else 1.0

    def to_canvas(self, pos) -> tuple[float, float]:
        k = self.k or 1.0
        return (pos.x() - self.rect.x()) / k, (pos.y() - self.rect.y()) / k

    def to_widget(self, x, y) -> QPointF:
        k = self.k
        return QPointF(self.rect.x() + x * k, self.rect.y() + y * k)

    def canvas_rect_to_widget(self, x, y, w, h) -> QRectF:
        k = self.k
        return QRectF(self.rect.x() + x * k, self.rect.y() + y * k, w * k, h * k)


def _hidden_track_ids(task, kind) -> set:
    out = set()
    tl = mm.timeline_of(task)
    for tr in getattr(tl, "tracks", None) or []:
        if getattr(tr, "type", "") == kind and (
                not getattr(tr, "visible", True) or getattr(tr, "mute", False)):
            out.add(getattr(tr, "id", None))
    return out


# ═════════════════════════ 网格 ═════════════════════════

class MonitorGrid(KeyMenuMixin, LayerOpsMixin, LayerPropsMixin, QWidget):
    """全部视频的格子。自己画、自己处理全部鼠标键盘；放在 QScrollArea 里。"""

    status = Signal(str)
    edited = Signal(object)          # list[task]
    selection_changed = Signal()
    open_task = Signal(object)       # task

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setAcceptDrops(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self.tiles: list[_Tile] = []
        self.tile_w = TILE_DEFAULT
        self.global_t = 0.0
        self.show_all = True
        self.show_subs = True
        self.follow_overlay = False
        self.link_edit = False
        self.selected: set[int] = set()
        self.active: int | None = None
        self.ov_sel: list = []            # [(tile_idx, ov)]
        self.video_sel: list = []         # [(tile_idx, track_id)] 画面上选中的视频层（含分屏上的全幅视频）
        self.focus_idx = None             # None = 均匀网格。有值 = 这一格放大，其余仍可同步
        self.history = mm.History()
        self._snap_off = False            # 按住 N：临时关吸附（和主画布、时间线同一个键）
        self._hover = None                # (tile_idx, kind, payload)
        self._press = None
        self._op = None
        self._band = None
        self._drop = None
        self._hud = ""
        self._hud_pos = QPointF()
        self._wheel_token = None
        self._wheel_key = None
        self._wheel_at = 0.0
        self._last_cell = None            # (tile_idx, cid) 键盘操作「当前格」
        self._renderer = _new_renderer()
        self._renderer.ready.connect(self._on_ready)
        self._renderer.start()
        self.destroyed.connect(lambda *_a, _r=self._renderer: _r.shutdown())
        self._sched = QTimer(self)
        self._sched.setSingleShot(True)
        self._sched.setInterval(0)
        self._sched.timeout.connect(self._schedule_now)
        self._compose_timer = QTimer(self)
        self._compose_timer.setSingleShot(True)
        self._compose_timer.setInterval(0)
        self._compose_timer.timeout.connect(self._compose_some)
        self._edited_pending: dict = {}
        self._edited_timer = QTimer(self)
        self._edited_timer.setSingleShot(True)
        self._edited_timer.setInterval(120)
        self._edited_timer.timeout.connect(self._flush_edited)
        self._font = QFont(self.font())
        self._font.setPixelSize(FONT_LABEL)
        self._font_small = QFont(self.font())
        self._font_small.setPixelSize(FONT_MICRO)
        # Q154：每格的播放条 + 独立播放
        self.show_bars = True
        self._play_timer = QTimer(self)
        self._play_timer.setInterval(PLAY_TICK_MS)
        self._play_timer.timeout.connect(self._play_tick)

    # ─────────── Q154：每格播放 / 进度条 ───────────

    def bar_rect(self, tile) -> QRectF:
        """这一格底部的播放条；格子太矮就不画（空矩形）。"""
        if not self.show_bars or tile.rect.height() < BAR_H * 3:
            return QRectF()
        return QRectF(tile.rect.left(), tile.rect.bottom() - BAR_H, tile.rect.width(), BAR_H)

    def _bar_parts(self, tile):
        """(播放钮, 进度轨, 时间文字宽)。"""
        bar = self.bar_rect(tile)
        if bar.isEmpty():
            return QRectF(), QRectF(), 0
        btn = QRectF(bar.left(), bar.top(), BAR_H, BAR_H)
        fm = QFontMetrics(self._font_small)
        tw = fm.horizontalAdvance("00:00 / 00:00") + SPACE_2 if bar.width() > BAR_H * 8 else 0
        track = QRectF(btn.right() + SPACE_1, bar.top(), max(1.0, bar.width() - BAR_H - SPACE_1 - tw - SPACE_1), BAR_H)
        return btn, track, tw

    def tile_duration(self, tile) -> float:
        try:
            return max(0.0, float(mm.task_duration(tile.task) or 0.0))
        except Exception:
            return 0.0

    def set_show_bars(self, on: bool):
        self.show_bars = bool(on)
        if not self.show_bars:
            self.stop_all()
        self.update()

    def playing_tiles(self) -> list:
        return [t for t in self.tiles if t.playing]

    def set_tile_time(self, tile, t: float, *, own: bool = True):
        dur = self.tile_duration(tile)
        t = max(0.0, min(float(t), dur if dur > 0 else float(t)))
        tile.own_t = bool(own)
        if abs(t - tile.t) > 1e-6:
            tile.t = t
            tile.comp_dirty = tile.ov_dirty = True
            self._invalidate_video(tile, schedule=False)
            self._request(tile, 0)
            self.update(tile.head.united(tile.rect).toAlignedRect().adjusted(-4, -4, 4, 4))
        if tile.playing:
            tile.play_t0 = tile.t
            tile.play_wall0 = time.monotonic()

    def set_tile_playing(self, tile, on: bool):
        on = bool(on) and self.tile_duration(tile) > 0
        if on == tile.playing:
            return
        if on and len(self.playing_tiles()) >= PLAY_MAX:
            # Q156：超过解码器池子就会每一帧都重开文件 —— 把最早开始播的那格停掉给它让位
            oldest = min(self.playing_tiles(), key=lambda t: t.play_wall0)
            self.set_tile_playing(oldest, False)
        tile.playing = on
        if on:
            tile.own_t = True
            tile.play_t0 = tile.t
            tile.play_wall0 = time.monotonic()
            if not self._play_timer.isActive():
                self._play_timer.start()
        elif not self.playing_tiles():
            self._play_timer.stop()
        self.update(tile.rect.toAlignedRect())

    def toggle_tile_play(self, tile):
        self.set_tile_playing(tile, not tile.playing)
        self.status.emit("%s %s" % (tile.label, "播放中（只看画面，不出声）" if tile.playing else "已暂停"))

    def play_all(self, on: bool):
        """全部播放：先屏幕里的、最多 PLAY_MAX 格（再多就互相挤解码器，每帧重开文件，显卡 / 硬盘都受不了）。"""
        if not on:
            for tile in self.tiles:
                self.set_tile_playing(tile, False)
            return
        ordered = sorted(self.tiles, key=lambda t: (not self._in_view(t), t.idx))
        n = 0
        for tile in ordered:
            if n >= PLAY_MAX:
                break
            self.set_tile_playing(tile, True)
            n += int(tile.playing)
        if len(self.tiles) > n:
            self.status.emit("一次最多同时播 %d 格（先播屏幕里的）；滚到别处点那格的 ▶ 会把最早的那格换下来" % PLAY_MAX)

    def stop_all(self):
        self.play_all(False)

    def _play_tick(self):
        playing = self.playing_tiles()
        if not playing:
            self._play_timer.stop()
            return
        now = time.monotonic()
        for tile in playing:
            dur = self.tile_duration(tile)
            if dur <= 0:
                tile.playing = False
                continue
            t = (tile.play_t0 + (now - tile.play_wall0)) % dur
            tile.t = t
            tile.comp_dirty = tile.ov_dirty = True
            if not tile.busy and self._in_view(tile):
                # 上一帧还没合成完就不再塞：后台合成多快就播多快，不排长队
                tile.busy = True
                self._invalidate_video(tile, schedule=False)
                self._request(tile, 0)
            self.update(self.bar_rect(tile).toAlignedRect().adjusted(0, -2, 0, 0))
        if not self.playing_tiles():
            self._play_timer.stop()

    def _scrub_to(self, tile, pos):
        _btn, track, _tw = self._bar_parts(tile)
        if track.isEmpty():
            return
        frac = (pos.x() - track.left()) / max(1.0, track.width())
        frac = max(0.0, min(1.0, frac))
        self.set_tile_time(tile, frac * self.tile_duration(tile))

    def _paint_bar(self, p, tile):
        bar = self.bar_rect(tile)
        if bar.isEmpty():
            return
        btn, track, tw = self._bar_parts(tile)
        p.save()
        bg = QColor(T.BG0)
        bg.setAlpha(170)
        p.fillRect(bar, bg)
        # 播放 / 暂停
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(T.ACCENT_HI if tile.playing else T.FG0))
        cx, cy = btn.center().x(), btn.center().y()
        if tile.playing:
            p.drawRect(QRectF(cx - 4, cy - 4, 3, 8))
            p.drawRect(QRectF(cx + 1, cy - 4, 3, 8))
        else:
            tri = QPainterPath(QPointF(cx - 3, cy - 4.5))
            tri.lineTo(QPointF(cx + 5, cy))
            tri.lineTo(QPointF(cx - 3, cy + 4.5))
            tri.closeSubpath()
            p.drawPath(tri)
        # 进度轨
        dur = self.tile_duration(tile)
        frac = (tile.t / dur) if dur > 0 else 0.0
        frac = max(0.0, min(1.0, frac))
        ty = track.center().y()
        p.setPen(QPen(QColor(T.LINE), 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(QPointF(track.left() + 2, ty), QPointF(track.right() - 2, ty))
        fx = track.left() + 2 + (track.width() - 4) * frac
        p.setPen(QPen(QColor(T.ACCENT), 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(QPointF(track.left() + 2, ty), QPointF(fx, ty))
        p.setPen(QPen(QColor(T.BG0), 1))
        p.setBrush(QColor(T.ACCENT_HI if tile.own_t else T.FG0))
        p.drawEllipse(QPointF(fx, ty), 4.5, 4.5)
        if tw:
            p.setFont(self._font_small)
            p.setPen(QColor(T.FG1))
            p.drawText(QRectF(track.right() + SPACE_1, bar.top(), tw, BAR_H),
                       Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                       "%s / %s" % (_mmss(tile.t), _mmss(dur)))
        p.restore()

    # 剪贴板和主画布共用（monitor_model 里那一份）
    @property
    def _clipboard(self):
        return mm.get_clipboard()

    @_clipboard.setter
    def _clipboard(self, value):
        mm.set_clipboard(value)

    # ─────────── 数据 ───────────

    def shutdown(self):
        self.key_shutdown()
        self._play_timer.stop()
        for t in self.tiles:
            t.playing = False
        try:
            self._edited_timer.stop()
            self._flush_edited()           # 关窗前把攒着的「改过了」先发出去，主窗口好刷新 / 落盘
        except Exception:
            _LOG.exception("监视器关闭前刷新失败")
        try:
            self._renderer.shutdown()
        except Exception:
            pass

    def set_tasks(self, tasks):
        old = {id(t.task): t for t in self.tiles}
        sel_ids = {id(self.tiles[i].task) for i in self.selected if i < len(self.tiles)}
        act_id = (id(self.tiles[self.active].task)
                  if self.active is not None and self.active < len(self.tiles) else None)
        ov_rows = [(id(self.tiles[i].task), ov) for i, ov in self.ov_sel if i < len(self.tiles)]
        vid_rows = [(id(self.tiles[i].task), tid) for i, tid in self.video_sel if i < len(self.tiles)]
        focus_id = (id(self.tiles[self.focus_idx].task)
                    if self.focus_idx is not None and self.focus_idx < len(self.tiles) else None)
        tiles = []
        for i, task in enumerate(tasks or []):
            tile = old.get(id(task))
            if tile is None:
                tile = _Tile(i, task)
            else:
                tile.idx = i
                tile.label = mm.task_label(task, i)
            tiles.append(tile)
        self.tiles = tiles
        by_id = {id(t.task): t.idx for t in tiles}
        self.selected = {by_id[x] for x in sel_ids if x in by_id}
        self.active = by_id.get(act_id)
        self.ov_sel = [(by_id[tid], ov) for tid, ov in ov_rows if tid in by_id]
        self.video_sel = [(by_id[task_id], vid) for task_id, vid in vid_rows if task_id in by_id]
        self.focus_idx = by_id.get(focus_id) if focus_id is not None else None
        self._hover = None
        self._fix_legacy_track_geometry()
        self._renderer.cancel_all()
        for tile in tiles:
            tile.req_gen = 0
        self.relayout()
        self.refresh_all()
        self.selection_changed.emit()

    def _fix_legacy_track_geometry(self):
        """Q145：Q144 版本用轨道变换平移过的格子 → 并进片段，出格的原像素不再是黑的。"""
        need = [t for t in self.tiles
                if any(getattr(tr, "split_cell", None) and mm.track_has_geometry(tr)
                       for tr in mm.video_tracks(t.task))]
        if not need:
            return
        tok = self.history.begin([t.task for t in need])
        n = 0
        for t in need:
            n += 1 if mm.bake_task_split_geometry(t.task) else 0
        if n:
            self._commit(tok, "修复分屏黑边", need)
            QTimer.singleShot(0, lambda n=n: self.status.emit(
                "已修复 %d 条视频的分屏黑边（格子平移改成移动片段本身，出格的原画面回来了）" % n))

    def _tile_of_task(self, task):
        for t in self.tiles:
            if t.task is task:
                return t
        return None

    def refresh_all(self, force=False):
        """外面改过工程（批量加载、主窗口）→ 签名变了的格子重画。"""
        for tile in self.tiles:
            tile.cw, tile.ch = mm.canvas_size(tile.task)
            try:
                vs = mm.view_signature(tile.task)
                osig = mm.overlay_signature(tile.task)
            except Exception:
                _LOG.exception("取签名失败")
                vs, osig = None, None
            if force or vs != tile.vsig or vs is None:
                tile.vsig = vs
                self._invalidate_video(tile, schedule=False)
            if force or osig != tile.osig or osig is None:
                tile.osig = osig
                tile.comp_dirty = tile.ov_dirty = True
        self._update_times()
        self._schedule()
        self._compose_timer.start()
        self.update()

    def _update_times(self):
        for tile in self.tiles:
            if tile.own_t:
                continue
            t = self.global_t
            if self.follow_overlay:
                ft = mm.first_overlay_time(tile.task)
                if ft is not None:
                    t = ft
            if abs(t - tile.t) > 1e-6:
                tile.t = t
                self._invalidate_video(tile, schedule=False)
                tile.comp_dirty = tile.ov_dirty = True

    def set_time(self, t):
        self.global_t = max(0.0, float(t))
        now = time.monotonic()
        for tile in self.tiles:
            # 顶部总滑块一动，各格都跟过去；正在播的从这个时间点接着播
            tile.own_t = False
            if tile.playing:
                tile.play_t0 = self.global_t
                tile.play_wall0 = now
        self._update_times()
        self._schedule()
        self._compose_timer.start()

    def set_show_all(self, on):
        self.show_all = bool(on)
        self._all_comp_dirty()

    def set_show_subs(self, on):
        self.show_subs = bool(on)
        self._all_comp_dirty()

    def set_follow_overlay(self, on):
        self.follow_overlay = bool(on)
        self._update_times()
        self._schedule()
        self._compose_timer.start()

    def _all_comp_dirty(self):
        for tile in self.tiles:
            tile.comp_dirty = tile.ov_dirty = True
        self._compose_timer.start()
        self.update()

    # ─────────── 布局 ───────────

    def viewport_rect(self) -> QRectF:
        r = self.visibleRegion().boundingRect()
        if r.isEmpty():
            if not self.isVisible():
                return QRectF()              # Q156：还没显示 → 什么都不在屏幕里，不提前开几十路解码
            return QRectF(0, 0, self.width(), self.height())
        return QRectF(r)

    def showEvent(self, ev):
        super().showEvent(ev)
        self._warm_left = WARM_COUNT
        self._schedule()
        self._compose_timer.start()

    def relayout(self, viewport_w=None):
        par = self.parentWidget()
        vw = int(viewport_w or (par.width() if par else self.width()))
        vh = int(par.height()) if par else 0
        fi = getattr(self, "focus_idx", None)
        if fi is not None and self.tiles and 0 <= int(fi) < len(self.tiles):
            self._relayout_focus(vw, vh, int(fi))
            return
        tw = max(TILE_MIN, min(TILE_MAX, int(self.tile_w)))
        cols = max(1, (vw - 2 * MARGIN + GAP) // (tw + GAP))
        cols = max(1, min(cols, len(self.tiles) or 1))
        left = max(MARGIN, (vw - (cols * (tw + GAP) - GAP)) // 2)
        y = MARGIN
        row_h = 0
        for i, tile in enumerate(self.tiles):
            c = i % cols
            if c == 0 and i > 0:
                y += row_h + GAP
                row_h = 0
            x = left + c * (tw + GAP)
            ih = tw * tile.ch / float(tile.cw or 1)
            tile.head = QRectF(x, y, tw, HEADER_H)
            tile.rect = QRectF(x, y + HEADER_H, tw, ih)
            row_h = max(row_h, HEADER_H + ih)
        total_h = int(y + row_h + MARGIN) if self.tiles else 200
        self.setFixedSize(max(vw, 200), max(total_h, vh, 200))
        for tile in self.tiles:
            want = self._render_size(tile)
            # Q156：按「上次要的尺寸」比，不按「有没有画出来」比 —— 开窗连着 3 次 relayout，
            # 以前每次都把还在后台解码的那一格作废重来，白开几遍文件
            if tile.req_w != want[0] or (tile.video is not None and tile.video.width() != want[0]):
                self._invalidate_video(tile, schedule=False)
        self._schedule()
        self.update()

    def _relayout_focus(self, vw, vh, fi):
        """一格放大靠左，其余收成右边一列。选中和同步不改。"""
        others = [t for i, t in enumerate(self.tiles) if i != fi]
        if others:
            right_w = max(TILE_MIN, min(int(vw * 0.22), TILE_MAX // 2))
            left_w = max(TILE_MIN, vw - 2 * MARGIN - GAP - right_w)
        else:
            right_w = 0
            left_w = max(TILE_MIN, vw - 2 * MARGIN)
        focus = self.tiles[fi]
        ih = left_w * focus.ch / float(focus.cw or 1)
        focus.head = QRectF(MARGIN, MARGIN, left_w, HEADER_H)
        focus.rect = QRectF(MARGIN, MARGIN + HEADER_H, left_w, ih)
        y = MARGIN
        x = MARGIN + left_w + (GAP if others else 0)
        for tile in others:
            tih = right_w * tile.ch / float(tile.cw or 1)
            tile.head = QRectF(x, y, right_w, HEADER_H)
            tile.rect = QRectF(x, y + HEADER_H, right_w, tih)
            y += HEADER_H + tih + GAP
        total_h = int(max(MARGIN + HEADER_H + ih, y) + MARGIN)
        self.setFixedSize(max(vw, 200), max(total_h, vh, 200))
        for tile in self.tiles:
            want = self._render_size(tile)
            if tile.req_w != want[0] or (tile.video is not None and tile.video.width() != want[0]):
                self._invalidate_video(tile, schedule=False)
        self._schedule()
        self.update()

    def set_focus_tile(self, idx):
        self.focus_idx = None if idx is None else int(idx)
        self.relayout()
        if self.focus_idx is None:
            self.status.emit("已恢复网格")
        else:
            self.status.emit("已放大这一格，其余选中的视频仍会一起改")

    def fit_all(self, viewport: QSize):
        """挑一个缩略图宽度，让全部视频刚好一屏放下。"""
        n = len(self.tiles)
        if not n:
            return
        best = TILE_MIN
        for tw in range(TILE_MAX, TILE_MIN - 1, -8):
            cols = max(1, (viewport.width() - 2 * MARGIN + GAP) // (tw + GAP))
            rows = math.ceil(n / cols)
            ih = max(tw * t.ch / float(t.cw or 1) for t in self.tiles)
            if rows * (ih + HEADER_H + GAP) + 2 * MARGIN <= viewport.height():
                best = tw
                break
        self.set_tile_width(best)

    def set_tile_width(self, w):
        self.tile_w = max(TILE_MIN, min(TILE_MAX, int(w)))
        self.relayout()

    def _render_size(self, tile) -> tuple[int, int]:
        dpr = max(1.0, float(self.devicePixelRatioF() or 1.0))
        rw = min(RENDER_MAX_W, max(32, int(round(tile.rect.width() * dpr))))
        rh = max(32, int(round(rw * tile.ch / float(tile.cw or 1))))
        return rw, rh

    # ─────────── 渲染调度 ───────────

    def _invalidate_video(self, tile, schedule=True, prio=None):
        tile.gen += 1
        if schedule:
            if prio is not None:
                self._request(tile, prio)
            else:
                self._schedule()

    def _request(self, tile, prio):
        rw, _rh = self._render_size(tile)
        k = rw / float(tile.cw or 1)
        try:
            vt = mm.view_task(tile.task, k)
        except Exception:
            _LOG.exception("监视器：生成看图副本失败 #%d", tile.idx + 1)
            tile.err = "读不了这条视频的时间线"
            return
        tile.req_gen = tile.gen
        tile.req_w = rw
        try:
            from cutora.render.visual_stack import build_stack, needs_stack, video_bands
            st = build_stack(tile.task)
            if needs_stack(tile.task, stack=st):
                vt["_bands"] = [[str(getattr(x, "id", "")) for x in b] for b in video_bands(st)]
        except Exception:
            _LOG.exception("监视器：算叠压顺序失败")
        self._renderer.request(id(tile), vt, tile.t, tile.gen, prio)

    def _in_view(self, tile) -> bool:
        vis = self.viewport_rect().adjusted(0, -80, 0, 80)
        return bool(tile.rect.intersects(vis) or tile.head.intersects(vis))

    def _next_stale_visible(self):
        ordered = list(self.tiles)
        active = self.active
        if active is not None:
            ordered.sort(key=lambda t: (t.idx != active, t.idx))
        for tile in ordered:
            if tile.req_gen == tile.gen:
                continue
            if self._in_view(tile):
                return tile
        return None

    def _schedule(self):
        # 拖动中只画手指下的那一格，其余等松手。
        if self._op is not None:
            return
        if not self._sched.isActive():
            self._sched.setInterval(0)
            self._sched.start()

    def _schedule_now(self):
        if self._op is not None:
            return
        # 后台还堆着一格就先别再塞。一次只解码一格，避免几十路一起开。
        if self._renderer.pending_count() >= 1:
            self._sched.setInterval(DECODE_GAP_MS)
            self._sched.start()
            return
        tile = self._next_stale_visible()
        if tile is None:
            self._sched.setInterval(0)
            return
        self._request(tile, 0 if tile.idx == self.active else 1)
        if self._next_stale_visible() is not None:
            gap = DECODE_GAP_MS
            if getattr(self, "_warm_left", 0) > 0:
                self._warm_left -= 1
                gap = WARM_GAP_MS            # 头几格慢慢来：第一批文件各自要开解码器
            self._sched.setInterval(gap)
            self._sched.start()

    def _on_ready(self, key, gen, img):
        tile = next((t for t in self.tiles if id(t) == key), None)
        if tile is not None:
            tile.busy = False
        if tile is None or gen != tile.gen:
            return
        if isinstance(img, (list, tuple)):
            tile.video_bands = list(img)
            img = img[0] if img else None
        else:
            tile.video_bands = None
        tile.video = img
        tile.err = "" if img is not None else "这一帧读不出来"
        tile.comp_dirty = True
        self._compose(tile)
        self.update(tile.rect.toAlignedRect().adjusted(-4, -4, 4, 4))

    def _compose_some(self):
        budget = time.perf_counter() + 0.012
        vis = self.viewport_rect()
        pending = [t for t in self.tiles if t.comp_dirty and t.rect.intersects(vis)]
        for tile in pending:
            self._compose(tile)
            self.update(tile.rect.toAlignedRect())
            if time.perf_counter() > budget:
                self._compose_timer.start()
                return

    def _compose(self, tile):
        """视频层 + 文字 / 图片 / 字幕 → 这一格的缩略图。命中框记下来给鼠标用。

        文字 / 图片 / 字幕单独画在一张透明图上缓存起来：视频层换帧（拖分界线、
        平移缩放）只重新叠一次，不重排文字；只有覆层变了才重画那张透明图。
        有非「正常」混合模式的层要和视频像素混合 —— 那种格子直接画在视频上。
        """
        rw, rh = self._render_size(tile)
        if tile.video is not None and not tile.video.isNull():
            rw, rh = tile.video.width(), tile.video.height()
        op = self._op
        if (op and op.get("tile") is tile and op.get("sprite") is not None
                and op["base_layer"].width() == rw and op["base_layer"].height() == rh):
            self._compose_sprite(tile, op, rw, rh)
            return
        if tile.video_bands and self._compose_stacked(tile, rw, rh):
            return
        direct = self._needs_direct(tile.task)
        img = QImage(rw, rh, QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(QColor(T.BG0))
        p = QPainter(img)
        try:
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            if tile.video is not None and not tile.video.isNull():
                p.fillRect(0, 0, rw, rh, QColor(0, 0, 0))
                p.drawImage(QRectF(0, 0, rw, rh), tile.video)
            if direct:
                tile.ov_layer = None
                tile.hits = self._paint_overlays(p, tile, rw, rh)
            else:
                layer = tile.ov_layer
                if tile.ov_dirty or layer is None or layer.width() != rw or layer.height() != rh:
                    layer = QImage(rw, rh, QImage.Format.Format_ARGB32_Premultiplied)
                    layer.fill(0)
                    lp = QPainter(layer)
                    try:
                        tile.hits = self._paint_overlays(lp, tile, rw, rh)
                    finally:
                        lp.end()
                    tile.ov_layer = layer
                p.drawImage(0, 0, layer)
        finally:
            p.end()
        tile.comp = QPixmap.fromImage(img)
        tile.comp_dirty = False
        tile.ov_dirty = False

    def _compose_stacked(self, tile, rw, rh) -> bool:
        """Q163：视频压在文字 / 字幕上面时，按时间线逐层叠（视频带 / 覆层轨交错）。"""
        try:
            from cutora.render.visual_stack import build_stack, needs_stack
            st = build_stack(tile.task)
            if not needs_stack(tile.task, stack=st):
                return False
            bands = tile.video_bands or []
            if len(bands) != sum(1 for e in st if e[0] == "video"):
                return False
        except Exception:
            _LOG.exception("监视器统一叠压失败")
            return False
        img = QImage(rw, rh, QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(QColor(0, 0, 0))
        p = QPainter(img)
        hits = []
        try:
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            vi = 0
            for kind, payload in st:
                if kind == "video":
                    b = bands[vi]
                    vi += 1
                    from cutora.render.visual_stack import draw_band
                    draw_band(p, b, QRectF(0, 0, rw, rh))         # Q178：带里有混合模式的按层混合
                elif kind == "subs_orphan":
                    hits += self._paint_overlays(p, tile, rw, rh, subs_only=True)
                elif kind == "adjust":
                    # Q174：调整图层（格子是缩小图：按格子像素 / 画布像素的比例做）
                    from cutora.render import adjust_layer as AL
                    AL.apply_on_painter(p, tile.task, payload, tile.t, tile.cw, tile.ch)
                    # 点得中、拖得动、能复制粘贴（框 = 含缩放的外接框）
                    for _i, ov, cur in AL.active_items(tile.task, payload, tile.t):
                        if not ov.get("hidden"):
                            hits.append(("image_overlays", ov, [mm.overlay_bounds(tile.task, cur)]))
                else:
                    hits += self._paint_overlays(p, tile, rw, rh, track=payload)
        finally:
            p.end()
        tile.hits = hits
        tile.ov_layer = None
        tile.comp = QPixmap.fromImage(img)
        tile.comp_dirty = False
        tile.ov_dirty = False
        return True

    def _compose_sprite(self, tile, op, rw, rh):
        """拖文字 / 图片时的快速合成：别的层是一张缓存图，被拖的那层是一张小精灵图，
        只做平移 / 缩放贴上去（< 1 ms）。松手后再完整重画一次，边缘回到清晰。"""
        img = QImage(rw, rh, QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(QColor(0, 0, 0))
        kd = rw / float(tile.cw)
        p = QPainter(img)
        try:
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            if tile.video is not None and not tile.video.isNull():
                p.drawImage(QRectF(0, 0, rw, rh), tile.video)
            p.drawImage(0, 0, op["base_layer"])
            if op["kind"] == "ov_move":
                if op.get("cross") is not None and not op.get("copy"):
                    p.setOpacity(0.3)
                    p.drawImage(0, 0, op["sprite"])
                else:
                    if op.get("cross") is not None:
                        p.setOpacity(1.0)
                        p.drawImage(0, 0, op["sprite"])
                    else:
                        dx, dy = op.get("applied") or (0.0, 0.0)
                        if op.get("copy"):
                            p.drawImage(0, 0, op["sprite"])
                        p.drawImage(QPointF(dx * kd, dy * kd), op["sprite"])
            else:
                f = float(op.get("f_eff") or 1.0)
                ax, ay = op["ax"] * kd, op["ay"] * kd
                p.translate(ax, ay)
                p.scale(f, f)
                p.translate(-ax, -ay)
                p.drawImage(0, 0, op["sprite"])
        finally:
            p.end()
        tile.comp = QPixmap.fromImage(img)
        tile.comp_dirty = False

    def _prepare_sprite(self, tile, ov):
        """按下开始拖文字 / 图片：把「别的层」和「这一层」分开各画一张。"""
        op = self._op
        if op is None or self._needs_direct(tile.task):
            return
        rw, rh = self._render_size(tile)
        if tile.video is not None and not tile.video.isNull():
            rw, rh = tile.video.width(), tile.video.height()
        base = QImage(rw, rh, QImage.Format.Format_ARGB32_Premultiplied)
        base.fill(0)
        sprite = QImage(rw, rh, QImage.Format.Format_ARGB32_Premultiplied)
        sprite.fill(0)
        p = QPainter(base)
        try:
            self._paint_overlays(p, tile, rw, rh, exclude=ov)
        finally:
            p.end()
        p = QPainter(sprite)
        try:
            self._paint_overlays(p, tile, rw, rh, only=ov)
        finally:
            p.end()
        op["base_layer"] = base
        op["sprite"] = sprite
        op["rects0"] = [tuple(r) for r in self._ov_rects(tile, ov)]
        op["ghost"] = QPixmap.fromImage(sprite)

    @staticmethod
    def _needs_direct(task) -> bool:
        for name in mm.OVERLAY_LISTS:
            for ov in task.get(name) or []:
                if isinstance(ov, dict) and str(ov.get("blend_mode") or "normal") != "normal":
                    return True
        return False

    def _paint_overlays(self, p, tile, rw, rh, only=None, exclude=None, track=None,
                        subs_only=False) -> list:
        """track：Q163 统一叠压时只画这一条轨上的（图片 / 文本 / 字幕轨）；subs_only：只画没有轨道的字幕。"""
        from cutora.render.frame_renderer import BLEND_MODES, FrameRenderer, _BLEND_NORMAL, \
            compute_export_render_order
        hits = []
        task = tile.task
        first_of = {}
        if track is not None:
            for _tr in mm.timeline_of(task).tracks if mm.timeline_of(task) is not None else []:
                _ty = getattr(_tr, "type", "")
                if _ty in ("text", "image") and getattr(_tr, "visible", True) and _ty not in first_of:
                    first_of[_ty] = getattr(_tr, "id", None)
        cw, ch = tile.cw, tile.ch
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        p.save()
        p.scale(rw / float(cw), rh / float(ch))
        try:
            try:
                order = compute_export_render_order(task)
            except Exception:
                order = ["image", "text", "subs"]
            if track is not None:
                order = [getattr(track, "type", "")]
            elif subs_only:
                order = ["subs"]
            for layer in order:
                if layer in ("image", "text"):
                    name = "image_overlays" if layer == "image" else "text_overlays"
                    hidden = _hidden_track_ids(task, layer)
                    for ov in list(task.get(name) or []):
                        if not isinstance(ov, dict) or ov is exclude:
                            continue
                        if only is not None and ov is not only:
                            continue
                        if ov.get("_track_id") and ov.get("_track_id") in hidden:
                            continue
                        if ov.get("hidden"):          # Q152-G 图层面板关掉的
                            continue
                        if track is not None:
                            tid = ov.get("_track_id") or first_of.get(layer)
                            if tid != getattr(track, "id", None):
                                continue
                        t_ov = mm.overlay_time(ov, tile.t, self.show_all)
                        if t_ov is None:
                            continue
                        p.save()
                        try:
                            blend = ov.get("blend_mode") or "normal"
                            p.setCompositionMode(BLEND_MODES.get(blend, _BLEND_NORMAL))
                            ov["_canvas_w"], ov["_canvas_h"] = cw, ch
                            rect = FrameRenderer.draw_single_overlay(p, ov, t_ov, backdrop=None)
                            if name == "image_overlays" and ov.get("type") == "image":
                                FrameRenderer.draw_stickers(p, ov, None, 0,
                                                            default_blend_name=blend,
                                                            current_time=t_ov)
                        except Exception:
                            _LOG.exception("监视器画覆层失败 #%d", tile.idx + 1)
                            rect = None
                        finally:
                            p.restore()
                        if name == "image_overlays":
                            rects = mm.image_hit_rects(ov)
                        else:
                            rects = [self._text_rect(task, ov, rect)]
                        hits.append((name, ov, rects))
                elif (layer == "subs" and only is None and self.show_subs
                      and (task.get("segments") or [])):
                    try:
                        FrameRenderer.draw_layers(p, task, cw, ch, tile.t, render_order=["subs"])
                    except Exception:
                        _LOG.exception("监视器画字幕失败 #%d", tile.idx + 1)
        finally:
            p.restore()
        return hits

    @staticmethod
    def _text_rect(task, ov, rect):
        """文字层实际占的框：画出来的框（自动高度以它为准）再按 scale 绕中心放大。"""
        if rect is None or not hasattr(rect, "width") or rect.width() <= 1:
            return mm.overlay_bounds(task, ov)
        x, y, w, h = rect.x(), rect.y(), rect.width(), rect.height()
        try:
            s = float(ov.get("scale") or 1.0)
        except (TypeError, ValueError):
            s = 1.0
        if abs(s - 1.0) > 1e-6 and abs(w - float(ov.get("w") or 0) * s) > 1.0:
            cx, cy = x + w / 2.0, y + h / 2.0
            w, h = w * s, h * s
            x, y = cx - w / 2.0, cy - h / 2.0
        return x, y, w, h

    # ─────────── 命中 ───────────

    def tile_at(self, pos) -> _Tile | None:
        for tile in self.tiles:
            if tile.rect.contains(pos) or tile.head.contains(pos):
                return tile
        return None

    def _overlay_hit(self, tile, x, y):
        for name, ov, rects in reversed(tile.hits):
            if ov.get("locked"):              # Q152-G：锁定的层拖不动
                continue
            for (rx, ry, rw, rh) in rects:
                pad = 4.0 / max(tile.k, 1e-6)
                if rx - pad <= x <= rx + rw + pad and ry - pad <= y <= ry + rh + pad:
                    return name, ov
        return None

    def _track_by_id(self, task, tid):
        tl = mm.timeline_of(task)
        want = str(tid or "")
        for tr in getattr(tl, "tracks", None) or []:
            if str(getattr(tr, "id", "") or "") == want:
                return tr
        return None

    def _clip_box(self, tile, track):
        """这条视频轨在当前时刻的画面框。点选和金框用，不参与渲染。"""
        if track is None:
            return None
        t = getattr(tile, "t", None)
        chosen = None
        for c in getattr(track, "clips", None) or []:
            if getattr(c, "hidden", False) or getattr(c, "type", "media") != "media":
                continue
            if chosen is None:
                chosen = c
            if mm._clip_active(c, t):
                chosen = c
                break
        if chosen is None:
            return None
        return mm.clip_canvas_rect(tile.task, track, chosen)

    def hit(self, pos):
        """(tile, kind, payload)。kind: head / divider / overlay / cell / None。"""
        tile = self.tile_at(pos)
        if tile is None:
            return None, None, None
        if tile.head.contains(pos):
            return tile, "head", None
        btn, track, _tw = self._bar_parts(tile)
        if not btn.isEmpty() and self.bar_rect(tile).contains(pos):
            return tile, ("playbtn" if btn.contains(pos) else "scrub"), None
        x, y = tile.to_canvas(pos)
        tol = DIVIDER_TOL / max(tile.k, 1e-6)
        d = mm.divider_at(tile.task, x, y, tol)
        if d is not None:
            return tile, "divider", d
        oh = self._overlay_hit(tile, x, y)
        if oh is not None:
            return tile, "overlay", oh
        tr, cid, rect = mm.content_target(tile.task, x, y, tile.t)
        return tile, "cell", (tr, cid, rect)

    # ─────────── 选择 ───────────

    def _targets(self, tile) -> list:
        """同步编辑：点的那格在多选里、开了同步 → 全部选中的；否则只有它自己。"""
        if self.link_edit and tile.idx in self.selected and len(self.selected) > 1:
            return [self.tiles[i] for i in sorted(self.selected) if i < len(self.tiles)]
        return [tile]

    def select_only(self, tile):
        self.selected = {tile.idx}
        self.active = tile.idx
        self.selection_changed.emit()
        self.update()

    def select_all(self):
        self.selected = {t.idx for t in self.tiles}
        self.selection_changed.emit()
        self.update()

    def clear_selection(self):
        self.selected.clear()
        self.ov_sel = []
        self.video_sel = []
        self.selection_changed.emit()
        self.update()

    def selected_tiles(self) -> list:
        return [self.tiles[i] for i in sorted(self.selected) if i < len(self.tiles)]

    # ─────────── 编辑收尾 ───────────

    def _changed(self, tiles, video=True, overlays=True, prio=1, visible_only=False):
        """改完数据。视频层只许当场重画「正在操作的那一格」。

        一批格子、画面外的格子、以及 visible_only，都只作废画面，等调度器
        在屏幕里一格一格补。prio 不再表示「把这一批立刻全部解码」。
        """
        for tile in tiles:
            if video:
                self._invalidate_video(tile, schedule=False)
                tile.vsig = None
            if overlays:
                tile.comp_dirty = tile.ov_dirty = True
                tile.osig = None
        live = (video and not visible_only and prio == 0 and len(tiles) == 1
                and self._in_view(tiles[0]))
        if live:
            self._request(tiles[0], 0)
        if video and self._op is None:
            self._schedule()
        if overlays:
            self._compose_timer.start()
        self.update()

    def _commit(self, token, label, tiles, *, overlays=True):
        self.history.commit(token, label)
        for tile in tiles:
            try:
                tile.vsig = mm.view_signature(tile.task)
                if overlays:
                    tile.osig = mm.overlay_signature(tile.task)
            except Exception:
                pass
            self._edited_pending[id(tile.task)] = tile.task
        self._edited_timer.start()
        self.selection_changed.emit()

    def _flush_edited(self):
        tasks = list(self._edited_pending.values())
        self._edited_pending.clear()
        if tasks:
            self.edited.emit(tasks)

    def _hist_add(self, token, task):
        if all(t is not task for t, _s in token):
            token.append((task, mm.snapshot(task)))

    def undo(self):
        label, tasks = self.history.undo()
        self._after_history(label, tasks, "撤销")

    def redo(self):
        label, tasks = self.history.redo()
        self._after_history(label, tasks, "重做")

    def _after_history(self, label, tasks, verb):
        if label is None:
            self.status.emit("没有可以%s的了" % verb)
            return
        tiles = [t for t in self.tiles if any(t.task is x for x in tasks)]
        live = {id(ov) for t in tiles for name in mm.OVERLAY_LISTS for ov in (t.task.get(name) or [])}
        self.ov_sel = [(i, ov) for i, ov in self.ov_sel if id(ov) in live]
        self.video_sel = [
            (i, tid) for i, tid in self.video_sel
            if i < len(self.tiles) and self._track_by_id(self.tiles[i].task, tid) is not None
        ]
        self._changed(tiles, prio=0)
        self._commit_noop(tiles)
        self.status.emit("%s：%s" % (verb, label or "编辑"))

    def _commit_noop(self, tiles):
        for tile in tiles:
            self._edited_pending[id(tile.task)] = tile.task
        self._edited_timer.start()
        self.selection_changed.emit()

    # ─────────── 画 ───────────

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        area = QRectF(ev.rect())
        p.fillRect(area, QColor(T.BG0))
        if not self.tiles:
            p.setPen(QColor(T.FG1))
            p.setFont(self._font)
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                       "没有视频。先在媒体库导入视频，或在批量加载里勾选要看的视频。")
            return
        for tile in self.tiles:
            outer = tile.head.united(tile.rect).adjusted(-4, -4, 4, 4)
            if not outer.intersects(area):
                continue
            self._paint_tile(p, tile)
        self._paint_op_overlays(p)
        if self._band is not None:
            r = QRectF(self._band[0], self._band[1]).normalized()
            c = QColor(T.ACCENT)
            c.setAlpha(40)
            p.fillRect(r, c)
            p.setPen(QPen(QColor(T.ACCENT), 1, Qt.PenStyle.DashLine))
            p.drawRect(r)
        if self._hud:
            self._paint_hud(p)

    def _paint_tile(self, p, tile):
        sel = tile.idx in self.selected
        act = tile.idx == self.active
        hov = self._hover is not None and self._hover[0] is tile
        # 标题栏
        head_bg = QColor(T.BG3 if (sel or hov) else T.BG1)
        p.fillRect(tile.head, head_bg)
        p.setFont(self._font)
        fm = QFontMetrics(self._font)
        badge = ""
        if mm.split_on(tile.task):
            from cutora.batch.split_assign import layout_label
            badge = layout_label(mm.split_of(tile.task).get("layout") or "")
        bw = fm.horizontalAdvance(badge) + SPACE_3 if badge else 0
        p.setPen(QColor(T.ACCENT_HI if act else (T.FG0 if sel else T.FG1)))
        text = fm.elidedText(tile.label, Qt.TextElideMode.ElideMiddle,
                             int(tile.head.width() - SPACE_3 - bw))
        p.drawText(tile.head.adjusted(SPACE_2, 0, -bw, 0),
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, text)
        if badge:
            p.setFont(self._font_small)
            p.setPen(QColor(T.INFO))
            p.drawText(tile.head.adjusted(0, 0, -SPACE_2, 0),
                       Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, badge)
        # 画面
        if tile.comp is not None:
            p.drawPixmap(tile.rect, tile.comp, QRectF(tile.comp.rect()))
        else:
            p.fillRect(tile.rect, QColor(T.BG2))
            p.setPen(QColor(T.FG2))
            p.setFont(self._font_small)
            p.drawText(tile.rect, Qt.AlignmentFlag.AlignCenter, tile.err or "载入中…")
        self._paint_bar(p, tile)
        # 分界线参考线：悬停 / 选中 / 正在拖
        show_guides = mm.split_on(tile.task) and (hov or sel or (self._op and self._op.get("tile") is tile))
        if show_guides:
            self._paint_dividers(p, tile)
        # 悬停的格子 / 覆层
        if hov and self._op is None and self._drop is None:
            _t, kind, payload = self._hover
            if kind == "cell" and payload and payload[0] is not None:
                _tr, cid, rect = payload
                if cid and mm.split_on(tile.task):
                    x, y, w, h = rect
                    c = QColor(T.ACCENT)
                    c.setAlpha(110)
                    p.setPen(QPen(c, 1.5))
                    p.setBrush(Qt.BrushStyle.NoBrush)
                    p.drawRect(tile.canvas_rect_to_widget(x, y, w, h).adjusted(1, 1, -1, -1))
                elif not cid:
                    box = self._clip_box(tile, _tr) or rect
                    c = QColor(T.ACCENT)
                    c.setAlpha(110)
                    p.setPen(QPen(c, 1.5))
                    p.setBrush(Qt.BrushStyle.NoBrush)
                    p.drawRect(tile.canvas_rect_to_widget(*box).adjusted(1, 1, -1, -1))
            elif kind == "overlay":
                _name, ov = payload
                for rx, ry, rw, rh in self._ov_rects(tile, ov):
                    p.setPen(QPen(QColor(T.FG0), 1, Qt.PenStyle.DashLine))
                    p.setBrush(Qt.BrushStyle.NoBrush)
                    p.drawRect(tile.canvas_rect_to_widget(rx, ry, rw, rh))
        # 选中的覆层
        for i, ov in self.ov_sel:
            if i != tile.idx:
                continue
            for rx, ry, rw, rh in self._ov_rects(tile, ov):
                r = tile.canvas_rect_to_widget(rx, ry, rw, rh)
                p.setPen(QPen(QColor(T.ACCENT), 1.5))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRect(r)
                p.setBrush(QColor(T.ACCENT))
                s = 5.0
                for cx, cy in ((r.left(), r.top()), (r.right(), r.top()),
                               (r.left(), r.bottom()), (r.right(), r.bottom())):
                    p.drawRect(QRectF(cx - s / 2, cy - s / 2, s, s))
        # 选中的视频层（分屏格子上的全幅视频 / 蒙版）。格子里的视频不画整幅框。
        for i, tid in self.video_sel:
            if i != tile.idx:
                continue
            tr = self._track_by_id(tile.task, tid)
            if tr is None or str(getattr(tr, "split_cell", "") or ""):
                continue
            box = self._clip_box(tile, tr)
            if not box:
                continue
            r = tile.canvas_rect_to_widget(*box)
            p.setPen(QPen(QColor(T.ACCENT), 1.5))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(r)
            p.setBrush(QColor(T.ACCENT))
            s = 5.0
            for cx, cy in ((r.left(), r.top()), (r.right(), r.top()),
                           (r.left(), r.bottom()), (r.right(), r.bottom())):
                p.drawRect(QRectF(cx - s / 2, cy - s / 2, s, s))
        # 边框
        if sel or act:
            p.setPen(QPen(QColor(T.ACCENT_HI if act else T.ACCENT), 3 if act else 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(tile.rect.adjusted(-1.5, -1.5, 1.5, 1.5))
        else:
            p.setPen(QPen(QColor(T.LINE), 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(tile.rect.adjusted(-0.5, -0.5, 0.5, 0.5))

    def _ov_rects(self, tile, ov):
        op = self._op
        if op and op.get("ov") is ov and op.get("tile") is tile and op.get("rects0"):
            if op["kind"] == "ov_move":
                if op.get("cross") is not None:
                    return op["rects0"]
                dx, dy = op.get("applied") or (0.0, 0.0)
                return [(x + dx, y + dy, w, h) for x, y, w, h in op["rects0"]]
            f = float(op.get("f_eff") or 1.0)
            ax, ay = op["ax"], op["ay"]
            return [(ax + (x - ax) * f, ay + (y - ay) * f, w * f, h * f)
                    for x, y, w, h in op["rects0"]]
        for name, o, rects in tile.hits:
            if o is ov:
                return rects
        return [mm.overlay_bounds(tile.task, ov)]

    def _paint_dividers(self, p, tile):
        hov_id = None
        if self._hover and self._hover[0] is tile and self._hover[1] == "divider":
            hov_id = self._hover[2]["id"]
        drag_id = None
        if self._op and self._op.get("kind") == "divider" and self._op.get("tile") is tile:
            drag_id = self._op["did"]
        for d in mm.divider_lines(tile.task):
            path = QPainterPath()
            pts = [tile.to_widget(x, y) for x, y in d["pts"]]
            path.moveTo(pts[0])
            for q in pts[1:]:
                path.lineTo(q)
            hot = d["id"] in (hov_id, drag_id)
            if hot:
                p.setPen(QPen(QColor(0, 0, 0, 120), 4))
                p.drawPath(path)
                p.setPen(QPen(QColor(T.ACCENT_HI), 2))
            else:
                p.setPen(QPen(QColor(255, 255, 255, 110), 1, Qt.PenStyle.DashLine))
            p.drawPath(path)
            if hot:
                mid = pts[len(pts) // 2]
                p.setBrush(QColor(T.ACCENT_HI))
                p.setPen(QPen(QColor(0, 0, 0, 160), 1))
                p.drawEllipse(mid, 5, 5)

    def _paint_op_overlays(self, p):
        op = self._op
        if op and op.get("kind") == "ov_move":
            tile = op["tile"]
            snap = op.get("snap") or ()
            if "cx" in snap:
                x = tile.to_widget(tile.cw / 2.0, 0).x()
                p.setPen(QPen(QColor(T.ERR), 1))
                p.drawLine(QPointF(x, tile.rect.top()), QPointF(x, tile.rect.bottom()))
            if "cy" in snap:
                y = tile.to_widget(0, tile.ch / 2.0).y()
                p.setPen(QPen(QColor(T.ERR), 1))
                p.drawLine(QPointF(tile.rect.left(), y), QPointF(tile.rect.right(), y))
            cross = op.get("cross")
            if cross is not None and op.get("ghost") is not None:
                g = op["ghost"]
                r = op.get("ghost_rect")
                if r is not None:
                    p.save()
                    p.setClipRect(cross.rect)
                    p.setOpacity(0.85)
                    p.drawPixmap(r, g, QRectF(g.rect()))
                    p.restore()
                    box = op.get("ghost_box")
                    if box is not None:
                        p.setPen(QPen(QColor(T.OK if op.get("copy") else T.ACCENT_HI), 1.5,
                                      Qt.PenStyle.DashLine))
                        p.setBrush(Qt.BrushStyle.NoBrush)
                        p.drawRect(box)
                p.setPen(QPen(QColor(T.OK if op.get("copy") else T.ACCENT_HI), 3))
                p.drawRect(cross.rect.adjusted(-2, -2, 2, 2))
        elif op and op.get("kind") == "pan":
            tile = op["tile"]
            x, y, w, h = op["rect"]
            snap = op.get("snap") or ()
            p.setPen(QPen(QColor(T.ERR), 1))
            if "x" in snap:
                cx = tile.to_widget(x + w / 2.0, 0).x()
                p.drawLine(QPointF(cx, tile.to_widget(0, y).y()), QPointF(cx, tile.to_widget(0, y + h).y()))
            if "y" in snap:
                cy = tile.to_widget(0, y + h / 2.0).y()
                p.drawLine(QPointF(tile.to_widget(x, 0).x(), cy), QPointF(tile.to_widget(x + w, 0).x(), cy))
        elif op and op.get("kind") == "zoom":
            tile = op["tile"]
            c = tile.to_widget(op["ax"] + op["rect"][0], op["ay"] + op["rect"][1])
            p.setPen(QPen(QColor(T.ACCENT_HI), 1.5))
            p.drawLine(c + QPointF(-7, 0), c + QPointF(7, 0))
            p.drawLine(c + QPointF(0, -7), c + QPointF(0, 7))
        elif op and op.get("kind") == "swap":
            tile = op["tile"]
            rects = mm.cell_rects(tile.task)
            for cid, color in ((op["cid"], T.ACCENT), (op.get("target"), T.OK)):
                if cid and cid in rects:
                    x, y, w, h = rects[cid]
                    p.setPen(QPen(QColor(color), 3))
                    p.setBrush(Qt.BrushStyle.NoBrush)
                    p.drawRect(tile.canvas_rect_to_widget(x, y, w, h).adjusted(2, 2, -2, -2))
        if self._drop is not None:
            self._paint_drop(p)

    def _paint_drop(self, p):
        info = self._drop
        for tile in info.get("targets") or []:
            p.setPen(QPen(QColor(T.OK), 3))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(tile.rect.adjusted(-2, -2, 2, 2))
            fx, fy = info.get("frac", (0.5, 0.5))
            x, y = fx * tile.cw, fy * tile.ch
            if info.get("has_video"):
                if mm.split_on(tile.task):
                    cid = mm.cell_at(tile.task, x, y)
                    r = mm.cell_rects(tile.task).get(cid)
                    if r:
                        c = QColor(T.OK)
                        c.setAlpha(60)
                        p.fillRect(tile.canvas_rect_to_widget(*r), c)
                else:
                    c = QColor(T.OK)
                    c.setAlpha(60)
                    if y < tile.ch / 2.0:
                        p.fillRect(tile.canvas_rect_to_widget(0, 0, tile.cw, tile.ch / 2.0), c)
                    else:
                        p.fillRect(tile.canvas_rect_to_widget(0, tile.ch / 2.0, tile.cw, tile.ch / 2.0), c)
            if info.get("has_image"):
                q = tile.to_widget(x, y)
                p.setPen(QPen(QColor(T.OK), 2))
                p.drawLine(q + QPointF(-9, 0), q + QPointF(9, 0))
                p.drawLine(q + QPointF(0, -9), q + QPointF(0, 9))

    def _paint_hud(self, p):
        p.setFont(self._font)
        fm = QFontMetrics(self._font)
        w = fm.horizontalAdvance(self._hud) + SPACE_4
        h = fm.height() + SPACE_2
        pos = self._hud_pos + QPointF(16, 18)
        r = QRectF(pos.x(), pos.y(), w, h)
        vis = self.viewport_rect()
        if r.right() > vis.right() - 4:
            r.moveRight(self._hud_pos.x() - 12)
        if r.bottom() > vis.bottom() - 4:
            r.moveBottom(self._hud_pos.y() - 12)
        c = QColor(T.BG1)
        c.setAlpha(230)
        p.setPen(QPen(QColor(T.ACCENT), 1))
        p.setBrush(c)
        p.drawRoundedRect(r, RADIUS_CTL, RADIUS_CTL)
        p.setPen(QColor(T.FG0))
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, self._hud)

    def _set_hud(self, text, pos=None):
        self._hud = text or ""
        if pos is not None:
            self._hud_pos = QPointF(pos)
        self.update()

    # ─────────── 鼠标 ───────────

    def _cursor_for(self, kind, payload, mods):
        if kind == "divider":
            return Qt.CursorShape.SplitVCursor if payload.get("axis") == "h" else Qt.CursorShape.SplitHCursor
        if kind == "overlay":
            if mods & Qt.KeyboardModifier.ControlModifier:
                return Qt.CursorShape.SizeFDiagCursor
            if mods & Qt.KeyboardModifier.AltModifier:
                return Qt.CursorShape.DragCopyCursor
            return Qt.CursorShape.SizeAllCursor
        if kind == "cell":
            tr = payload[0] if payload else None
            if tr is None:
                return Qt.CursorShape.ArrowCursor
            if mods & Qt.KeyboardModifier.ControlModifier:
                return Qt.CursorShape.SizeBDiagCursor
            if mods & Qt.KeyboardModifier.AltModifier and payload[1]:
                return Qt.CursorShape.DragMoveCursor
            return Qt.CursorShape.OpenHandCursor
        if kind == "head":
            return Qt.CursorShape.PointingHandCursor
        if kind in ("playbtn", "scrub"):
            return Qt.CursorShape.PointingHandCursor
        return Qt.CursorShape.ArrowCursor

    def _refresh_hover(self, pos, mods):
        tile, kind, payload = self.hit(pos)
        new = (tile, kind, payload) if tile is not None else None
        old = self._hover
        same = (old is not None and new is not None and old[0] is new[0] and old[1] == new[1]
                and (old[2] is new[2] or (kind == "divider" and old[2]["id"] == payload["id"])
                     or (kind == "overlay" and old[2][1] is payload[1])
                     or (kind == "cell" and old[2] and payload and old[2][1] == payload[1])))
        self._hover = new
        self.setCursor(self._cursor_for(kind, payload, mods))
        if not same:
            self.update()
            self.status.emit(self._hover_hint(kind, payload, tile))

    def _hover_hint(self, kind, payload, tile):
        if kind == "divider":
            return "拖动调分屏比例 · Alt+拖旋转 · 双击复位 · 按住 N 不吸附"
        if kind == "overlay":
            return "拖动移动（拖到别的视频 = 移过去）· Alt 拖 = 复制 · Ctrl 拖 / Ctrl+滚轮 = 缩放 · Delete 删除"
        if kind == "cell":
            if payload and payload[0] is None:
                return "这一格还没有视频 —— 从素材库拖一段视频进来"
            extra = " · Alt 拖到另一格 = 交换" if payload and payload[1] else ""
            who = ""
            if payload and not payload[1] and tile is not None:
                slot = mm.layer_slot(tile.task, payload[0])
                name = mm.track_display_name(tile.task, payload[0])
                if slot and slot[0] == "ext":
                    who = "【外部视频 %s】" % name
                elif slot:
                    who = "【媒体库视频 %s】" % name
                if who and self.link_edit and len(self.selected) > 1:
                    who += "（同步：选中的每条里同一批的这一层）"
            return who + "拖动平移画面 · Ctrl 拖 / Ctrl+滚轮 = 以光标为中心缩放 · 双击重置" + extra
        if kind == "head":
            return "点选 · Ctrl / Shift 多选 · 双击在主窗口打开"
        if kind == "playbtn":
            return "播放 / 暂停这一条（只看画面）；顶部「全部播放」一起播"
        if kind == "scrub":
            return "拖动看这一条的任意时刻；顶部总滑块一动，全部跟过去"
        return "空白处拖动框选 · Ctrl+A 全选"

    def mousePressEvent(self, ev):
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        pos = ev.position()
        mods = ev.modifiers()
        if ev.button() == Qt.MouseButton.RightButton:
            self._context_menu(ev)
            return
        if ev.button() != Qt.MouseButton.LeftButton:
            return
        if self._op is not None:
            return
        tile, kind, payload = self.hit(pos)
        self._press = {"pos": QPointF(pos), "tile": tile, "kind": kind, "payload": payload,
                       "mods": mods, "started": False}
        if tile is None:
            self._band = (QPointF(pos), QPointF(pos))
            self._band_base = set(self.selected) if mods & Qt.KeyboardModifier.ControlModifier else set()
            return
        if kind == "divider":
            self._start_op()           # 分界线按下就跟手，不等阈值
        elif kind == "scrub":
            # Q154：按在进度条上就跳过去，拖着走；不改选中
            self._press["started"] = True
            self._op = {"kind": "scrub", "tile": tile, "moved": True, "targets": [tile]}
            self._scrub_to(tile, pos)

    def mouseMoveEvent(self, ev):
        pos = ev.position()
        mods = ev.modifiers()
        if self._band is not None:
            self._band = (self._band[0], QPointF(pos))
            r = QRectF(self._band[0], self._band[1]).normalized()
            self.selected = set(self._band_base) | {t.idx for t in self.tiles
                                                    if t.rect.intersects(r) or t.head.intersects(r)}
            self.selection_changed.emit()
            self.update()
            return
        pr = self._press
        if pr is not None and ev.buttons() & Qt.MouseButton.LeftButton:
            if not pr["started"]:
                if (pos - pr["pos"]).manhattanLength() < DRAG_THRESHOLD:
                    return
                self._start_op()
            self._drag_op(pos, mods)
            return
        self._refresh_hover(pos, mods)

    def mouseReleaseEvent(self, ev):
        if ev.button() != Qt.MouseButton.LeftButton:
            return
        pos = ev.position()
        mods = ev.modifiers()
        if self._band is not None:
            self._band = None
            self.update()
            return
        pr = self._press
        self._press = None
        if pr is None:
            return
        if self._op is not None:
            self._finish_op(pos, mods)
            self._refresh_hover(pos, mods)
            return
        self._click(pr, mods)

    def mouseDoubleClickEvent(self, ev):
        if ev.button() != Qt.MouseButton.LeftButton:
            return
        tile, kind, payload = self.hit(ev.position())
        if tile is None:
            return
        if kind == "head":
            self.open_task.emit(tile.task)
            return
        targets = self._targets(tile)
        if kind == "divider":
            tok = self.history.begin([t.task for t in targets])
            for t in targets:
                mm.reset_dividers(t.task)
            self._changed(targets, overlays=False, prio=0)
            self._commit(tok, "分界线均分", targets)
            self.status.emit("分界线已均分")
        elif kind == "cell" and payload and payload[0] is not None:
            cid = payload[1]
            slot = None if cid else mm.layer_slot(tile.task, payload[0])
            tok = self.history.begin([t.task for t in targets])
            for t in targets:
                tr = self._slot_track(t.task, cid, slot)
                if tr is not None:
                    mm.reset_track_transform(tr)
            self._changed(targets, overlays=False, prio=0)
            self._commit(tok, "重置画面", targets)
            self.status.emit("已重置平移 / 缩放")
        elif kind == "overlay":
            self.open_task.emit(tile.task)
        self._press = None

    def _click(self, pr, mods):
        tile, kind, payload = pr["tile"], pr["kind"], pr["payload"]
        ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
        shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)
        if kind == "playbtn" and tile is not None:
            self.toggle_tile_play(tile)
            return
        if tile is None:
            if not ctrl:
                self.clear_selection()
            return
        if shift and self.active is not None:
            a, b = sorted((self.active, tile.idx))
            self.selected |= set(range(a, b + 1))
        elif ctrl:
            if kind == "overlay":
                ov = payload[1]
                if any(o is ov for _i, o in self.ov_sel):
                    self.ov_sel = [(i, o) for i, o in self.ov_sel if o is not ov]
                else:
                    self.ov_sel.append((tile.idx, ov))
                self.selected.add(tile.idx)
            elif tile.idx in self.selected:
                self.selected.discard(tile.idx)
                self.ov_sel = [(i, o) for i, o in self.ov_sel if i != tile.idx]
            else:
                self.selected.add(tile.idx)
            self.active = tile.idx
        else:
            self.selected = {tile.idx}
            self.active = tile.idx
            if kind == "overlay":
                self.ov_sel = [(tile.idx, payload[1])]
                self.video_sel = [v for v in self.video_sel if v[0] != tile.idx]
            elif kind == "cell" and payload and payload[0] is not None:
                self.ov_sel = []
                tid = str(getattr(payload[0], "id", "") or "")
                self.video_sel = [(tile.idx, tid)] if tid else []
            else:
                self.ov_sel = []
                self.video_sel = [v for v in self.video_sel if v[0] != tile.idx]
        if kind == "cell" and payload:
            self._last_cell = (tile.idx, payload[1])
            self._last_slot = None if payload[1] else mm.layer_slot(tile.task, payload[0])
        self.selection_changed.emit()
        self.update()

    # ─────────── 拖动操作 ───────────

    def _start_op(self):
        pr = self._press
        pr["started"] = True
        tile, kind, payload, mods = pr["tile"], pr["kind"], pr["payload"], pr["mods"]
        ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
        alt = bool(mods & Qt.KeyboardModifier.AltModifier)
        if tile is None or kind == "head":
            self._op = None
            return
        if tile.idx not in self.selected:
            self.selected = {tile.idx}
            self.selection_changed.emit()
        self.active = tile.idx
        targets = self._targets(tile)
        x0, y0 = tile.to_canvas(pr["pos"])
        if kind == "divider":
            pts = payload.get("pts") or [(tile.cw / 2.0, tile.ch / 2.0)]
            mid = pts[len(pts) // 2]
            self._op = {"kind": "divider", "tile": tile, "did": payload["id"],
                        "axis": payload.get("axis") or "h", "targets": targets,
                        "rot": alt, "mid": mid,
                        "token": self.history.begin([t.task for t in targets])}
        elif kind == "overlay":
            name, ov = payload
            if not any(o is ov for _i, o in self.ov_sel):
                self.ov_sel = [(tile.idx, ov)]
            base = mm._plain_copy(ov)
            # Q158 同步编辑：选中的其他视频里「同一条」文字 / 图片跟着一起动
            sibs = self._overlay_siblings(tile, ov, targets)
            token = self.history.begin([tile.task] + [t.task for t, _o in sibs])
            if ctrl:
                bx, by, bw, bh = mm.overlay_bounds(tile.task, ov)
                self._op = {"kind": "ov_scale", "tile": tile, "ov": ov, "base": base,
                            "ax": bx + bw / 2.0, "ay": by + bh / 2.0,
                            "siblings": sibs, "token": token}
            else:
                ax, ay = mm.overlay_anchor(tile.task, ov)
                self._op = {"kind": "ov_move", "tile": tile, "ov": ov, "name": name,
                            "base": base, "copy": alt, "grab": (x0 - ax, y0 - ay),
                            "bounds0": mm.overlay_bounds(tile.task, ov),
                            "anchor": (ax, ay), "cross": None, "applied": (0.0, 0.0),
                            "siblings": sibs, "sib_applied": {}, "token": token}
            self._prepare_sprite(tile, ov)
            self.status.emit("拖到别的视频上松手 = 移过去 · 按住 Alt = 复制 · Shift 锁横竖 · Esc 取消"
                             if self._op["kind"] == "ov_move" else "左右 / 上下拖动缩放 · Esc 取消")
        elif kind == "cell":
            tr, cid, rect = payload
            if tr is None:
                self._op = None
                self.status.emit("这一格还没有视频 —— 从素材库拖一段视频进来")
                return
            self._last_cell = (tile.idx, cid)
            self._last_slot = None if cid else mm.layer_slot(tile.task, tr)
            if alt and cid:
                self._op = {"kind": "swap", "tile": tile, "cid": cid, "target": None,
                            "targets": targets}
            else:
                token = self.history.begin([t.task for t in targets])
                # 老数据（Q144 用轨道变换平移过的格子）先并进片段，再以片段为基准拖
                for t in targets:
                    mm.bake_task_split_geometry(t.task)
                linked = self._linked_same_cell(targets, cid, tile, tr)
                states = [(tile, tr, mm.clip_state(tr))] + [
                    (t, tr2, mm.clip_state(tr2)) for t, tr2 in linked]
                base = mm.content_xy_scale(tr)
                common = {"tile": tile, "track": tr, "cid": cid, "rect": rect,
                          "states": states, "base": base, "targets": targets, "token": token}
                if ctrl:
                    # 不动点：按下的位置，相对格子中心
                    self._op = dict(common, kind="zoom",
                                    ax=x0 - rect[0], ay=y0 - rect[1],
                                    ux=(x0 - rect[0]) - rect[2] / 2.0,
                                    uy=(y0 - rect[1]) - rect[3] / 2.0)
                else:
                    self._op = dict(common, kind="pan")
                    self.setCursor(Qt.CursorShape.ClosedHandCursor)
        self.update()

    def _overlay_siblings(self, tile, ov, targets):
        """同步编辑开着、多选时：其他选中视频里对应的那条覆层 [(tile, ov), …]。"""
        out = []
        for t in targets or []:
            if t is tile:
                continue
            try:
                o = mm.sibling_overlay(tile.task, ov, t.task)
            except Exception:
                _LOG.exception("找同类素材失败 #%d", t.idx + 1)
                o = None
            if o is not None:
                out.append((t, o))
        return out

    def _sync_siblings_move(self, op, dx, dy):
        """主覆层累计挪了 (dx, dy)（主画布像素）→ 兄弟按各自画布比例挪到同样的相对位置。"""
        tile = op["tile"]
        done = op.setdefault("sib_applied", {})
        for t, o in op.get("siblings") or []:
            r = mm.sibling_ratio(tile.task, t.task)
            want = (dx * r, dy * r)
            had = done.get(id(o), (0.0, 0.0))
            if abs(want[0] - had[0]) < 1e-9 and abs(want[1] - had[1]) < 1e-9:
                continue
            mm.move_overlay(t.task, o, want[0] - had[0], want[1] - had[1])
            done[id(o)] = want
            t.comp_dirty = t.ov_dirty = True
            self._compose(t)
            self.update(t.rect.toAlignedRect().adjusted(-8, -8, 8, 8))

    def _sync_siblings_scale(self, op, f):
        for t, o in op.get("siblings") or []:
            base = op.setdefault("sib_base", {}).setdefault(id(o), mm._plain_copy(o))
            self._restore_ov(o, base)
            bx, by, bw, bh = mm.overlay_bounds(t.task, o)
            mm.scale_overlay(t.task, o, f, bx + bw / 2.0, by + bh / 2.0)
            t.comp_dirty = t.ov_dirty = True
            self._compose(t)
            self.update(t.rect.toAlignedRect().adjusted(-8, -8, 8, 8))

    def _linked_same_cell(self, targets, cid, tile, track=None):
        """同步编辑：其他选中视频里「同一层」。分屏 = 同一格；不分屏 = 同一批（主视频对主视频、
        第 N 条外部视频对第 N 条外部视频）。"""
        out = []
        slot = mm.layer_slot(tile.task, track) if (track is not None and not cid) else None
        for t in targets:
            if t is tile:
                continue
            tr = mm.sibling_video_track(t.task, cid, slot)
            if tr is not None and not getattr(tr, "locked", False):
                out.append((t, tr))
        return out

    def _drag_op(self, pos, mods):
        op = self._op
        if op is None:
            return
        tile = op["tile"]
        kind = op["kind"]
        if kind == "scrub":
            self._scrub_to(tile, pos)
            return
        shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)
        if not op.get("moved"):
            if (pos - self._press_pos()).manhattanLength() < DRAG_THRESHOLD:
                return
            op["moved"] = True
        x, y = tile.to_canvas(pos)
        if kind == "divider" and op.get("rot"):
            # Alt+拖分界线 = 旋转（和主画布一致），贴 0°，按住 N 不吸
            hx, hy = op["mid"]
            ang = math.degrees(math.atan2(y - hy, x - hx))
            if op["axis"] != "h":
                ang -= 90.0
            while ang > 90.0:
                ang -= 180.0
            while ang < -90.0:
                ang += 180.0
            if not self._snap_off and abs(ang) < 2.0:
                ang = 0.0
            got = mm.set_divider_rotation(tile.task, op["did"], ang)
            same = [t for t in op["targets"] if t is not tile and mm.split_on(t.task)
                    and mm.divider_compatible(mm.split_of(t.task), mm.split_of(tile.task), op["did"])]
            for t in same:
                mm.set_divider_rotation(t.task, op["did"], got)
            self._changed([tile], overlays=False, prio=0)
            self._changed(same, overlays=False, prio=1)
            self._set_hud("分界线旋转 %.1f°" % got, pos)
        elif kind == "divider":
            pos_n = (y / tile.ch) if op["axis"] == "h" else (x / tile.cw)
            if not self._snap_off:
                span = tile.ch if op["axis"] == "h" else tile.cw
                pos_n = mm.snap_divider_pos(pos_n, SNAP_SCREEN / max(tile.k * span, 1e-6))
            got = mm.set_divider_pos(tile.task, op["did"], pos_n)
            same = []
            for t in op["targets"]:
                if t is tile or not mm.split_on(t.task):
                    continue
                if not mm.divider_compatible(mm.split_of(t.task), mm.split_of(tile.task), op["did"]):
                    continue
                mm.set_divider_pos(t.task, op["did"], got)
                same.append(t)
            self._changed([tile], overlays=False, prio=0)
            self._changed(same, overlays=False, prio=1)
            n = 1 + len(same)
            self._set_hud("分界线 %.1f%%%s" % (got * 100, "  · 同步 %d 条" % n if n > 1 else ""), pos)
        elif kind == "pan":
            k = max(tile.k, 1e-6)
            dx = (pos.x() - self._press_pos().x()) / k
            dy = (pos.y() - self._press_pos().y()) / k
            if shift:
                if abs(dx) > abs(dy):
                    dy = 0.0
                else:
                    dx = 0.0
            x0_, y0_, _s0 = op["base"]
            snap = []
            if not self._snap_off and abs(x0_ + dx) < SNAP_SCREEN / k:
                dx = -x0_
                snap.append("x")
            if not self._snap_off and abs(y0_ + dy) < SNAP_SCREEN / k:
                dy = -y0_
                snap.append("y")
            op["snap"] = snap
            for t, _tr, st in op["states"]:
                mm.apply_content_affine(st, 1.0, dx, dy)
            others = [t for t, _tr, _st in op["states"] if t is not tile]
            self._changed([tile], overlays=False, prio=0)
            self._changed(others, overlays=False, prio=1)
            self._set_hud("X %+d   Y %+d%s" % (round(x0_ + dx), round(y0_ + dy),
                                                "  · 同步 %d 条" % (len(others) + 1) if others else ""), pos)
        elif kind == "zoom":
            d = (pos.x() - self._press_pos().x()) - (pos.y() - self._press_pos().y())
            f = math.exp(d * 0.006)
            _x0, _y0, s0 = op["base"]
            f = mm.clamp_zoom_factor(op["states"][0][2], f)
            if not self._snap_off and abs(s0 * f - 1.0) < 0.03:
                f = 1.0 / s0
            ux, uy = op["ux"], op["uy"]
            for t, _tr, st in op["states"]:
                mm.apply_content_affine(st, f, (1.0 - f) * ux, (1.0 - f) * uy)
            others = [t for t, _tr, _st in op["states"] if t is not tile]
            self._changed([tile], overlays=False, prio=0)
            self._changed(others, overlays=False, prio=1)
            self._set_hud("缩放 %d%%%s" % (round(s0 * f * 100),
                                          "  · 同步 %d 条" % (len(others) + 1) if others else ""), pos)
        elif kind == "swap":
            cid = mm.cell_at(tile.task, x, y) if tile.rect.contains(pos) else None
            op["target"] = cid if cid != op["cid"] else None
            self._set_hud(("交换 %s ↔ %s" % (op["cid"], cid)) if op["target"] else
                          "拖到另一格松手交换", pos)
            self.update()
        elif kind == "ov_scale":
            d = (pos.x() - self._press_pos().x()) - (pos.y() - self._press_pos().y())
            f = math.exp(d * 0.006)
            ov = op["ov"]
            self._restore_ov(ov, op["base"])
            s0 = float(op["base"].get("scale") or 1.0)
            s = mm.scale_overlay(tile.task, ov, f, op["ax"], op["ay"])
            op["f_eff"] = (s / s0) if mm.overlay_kind(ov) in ("text", "adjust") else f
            if op.get("siblings"):
                self._sync_siblings_scale(op, f)
            tile.comp_dirty = tile.ov_dirty = True
            self._compose(tile)
            self.update(tile.rect.toAlignedRect().adjusted(-8, -8, 8, 8))
            _k = mm.overlay_kind(ov)
            label = ("文字 %d%%" % round(s * 100) if _k == "text" else
                     "调整图层 %d%%" % round(s * 100) if _k == "adjust" else "图片 %d%%" % round(f * 100))
            self._set_hud(label, pos)
        elif kind == "ov_move":
            self._drag_overlay(pos, mods)

    def _press_pos(self):
        return self._press["pos"] if self._press else QPointF()

    @staticmethod
    def _restore_ov(ov, base):
        ov.clear()
        ov.update(mm._plain_copy(base))

    def _drag_overlay(self, pos, mods):
        op = self._op
        tile = op["tile"]
        ov = op["ov"]
        op["copy"] = bool(mods & Qt.KeyboardModifier.AltModifier)
        over = self.tile_at(pos)
        if over is not None and over is not tile and over.rect.contains(pos):
            # 跨视频：源视频里的层回到原位（半透明显示「要搬走的」），目标视频上画幽灵
            if op["cross"] is None:
                self._restore_ov(ov, op["base"])
                op["applied"] = (0.0, 0.0)
                if op.get("siblings"):
                    self._sync_siblings_move(op, 0.0, 0.0)
            op["cross"] = over
            op["snap"] = ()
            tile.comp_dirty = tile.ov_dirty = True
            self._compose(tile)
            r = min(over.cw / float(tile.cw), over.ch / float(tile.ch))
            gx, gy = op["grab"]
            cx, cy = over.to_canvas(pos)
            ax, ay = cx - gx * r, cy - gy * r
            ox, oy = op["anchor"]
            # 精灵图是整格大小、层在原位；整张按比例 r 贴到目标格，原点对上落点
            op["ghost_rect"] = over.canvas_rect_to_widget(
                ax - ox * r, ay - oy * r, tile.cw * r, tile.ch * r)
            bx, by, bw, bh = op["bounds0"]
            op["ghost_box"] = over.canvas_rect_to_widget(
                ax + (bx - ox) * r, ay + (by - oy) * r, bw * r, bh * r)
            op["drop_delta"] = (ax - ox * r, ay - oy * r)
            self._set_hud("%s到 #%02d  %s" % ("复制" if op["copy"] else "移动", over.idx + 1,
                                            "（松开 Alt = 移动）" if op["copy"] else "（按住 Alt = 复制）"), pos)
            self.update()
            return
        if op["cross"] is not None:
            op["cross"] = None
            tile.comp_dirty = tile.ov_dirty = True
        k = max(tile.k, 1e-6)
        dx = (pos.x() - self._press_pos().x()) / k
        dy = (pos.y() - self._press_pos().y()) / k
        if mods & Qt.KeyboardModifier.ShiftModifier:
            if abs(dx) > abs(dy):
                dy = 0.0
            else:
                dx = 0.0
        # 吸附：层的中心对齐画面中心
        bx, by, bw, bh = op["bounds0"]
        snap = []
        tol = SNAP_SCREEN / k if not self._snap_off else -1.0
        if abs(bx + bw / 2.0 + dx - tile.cw / 2.0) < tol:
            dx = tile.cw / 2.0 - (bx + bw / 2.0)
            snap.append("cx")
        if abs(by + bh / 2.0 + dy - tile.ch / 2.0) < tol:
            dy = tile.ch / 2.0 - (by + bh / 2.0)
            snap.append("cy")
        op["snap"] = snap
        ax_, ay_ = op["applied"]
        mm.move_overlay(tile.task, ov, dx - ax_, dy - ay_)
        op["applied"] = (dx, dy)
        if op.get("siblings"):
            self._sync_siblings_move(op, dx, dy)
        tile.comp_dirty = tile.ov_dirty = True
        self._compose(tile)
        self.update(tile.rect.toAlignedRect().adjusted(-8, -8, 8, 8))
        nx, ny = mm.overlay_anchor(tile.task, ov)
        self._set_hud("%sX %d   Y %d" % ("复制 · " if op["copy"] else "", round(nx), round(ny)), pos)

    def _finish_op(self, pos, mods):
        op = self._op
        self._op = None
        self._set_hud("")
        if op is None:
            return
        if op.get("kind") == "scrub":
            self.update(op["tile"].rect.toAlignedRect())
            return
        try:
            self._finish_op_body(op, pos, mods)
        finally:
            # 松手后再补屏幕里其余的格子，仍然一次一格。
            self._schedule()
            self._compose_timer.start()

    def _finish_op_body(self, op, pos, mods):
        kind = op["kind"]
        tile = op["tile"]
        if not op.get("moved"):
            # 按下就松开：没改任何东西，当成一次点击
            self._changed([tile], video=False)
            pr = {"tile": tile, "kind": "divider" if kind == "divider" else
                  ("overlay" if kind.startswith("ov_") else "cell"),
                  "payload": ((op.get("name"), op.get("ov")) if kind.startswith("ov_") else
                              (op.get("track"), op.get("cid"), op.get("rect"))),
                  "mods": mods}
            self._click(pr, mods)
            return
        if kind == "divider":
            self._commit(op["token"], "调分界线", op["targets"])
        elif kind in ("pan", "zoom"):
            self._commit(op["token"], "平移画面" if kind == "pan" else "缩放画面", op["targets"])
        elif kind == "swap":
            tgt = op.get("target")
            if tgt:
                targets = [t for t in op["targets"]
                           if mm.split_on(t.task)
                           and mm.split_of(t.task).get("layout") == mm.split_of(tile.task).get("layout")]
                tok = self.history.begin([t.task for t in targets])
                for t in targets:
                    mm.swap_cells(t.task, op["cid"], tgt)
                self._changed(targets, overlays=False, prio=0)
                self._commit(tok, "交换格子", targets)
                self.status.emit("已交换格子 %s ↔ %s" % (op["cid"], tgt))
            self.update()
        elif kind == "ov_scale":
            sib_tiles = [t for t, _o in op.get("siblings") or []]
            self._changed(sib_tiles, video=False)
            self._commit(op["token"], "缩放覆层", [tile] + sib_tiles)
        elif kind == "ov_move":
            self._finish_overlay_move(op, pos, mods)
        self.update()

    def _finish_overlay_move(self, op, pos, mods):
        tile = op["tile"]
        ov = op["ov"]
        copy_mode = bool(op.get("copy"))
        cross = op.get("cross")
        token = op["token"]
        if cross is not None:
            self._hist_add(token, cross.task)
            dx, dy = op.get("drop_delta") or (0.0, 0.0)
            new = mm.copy_overlay_into(tile.task, ov, cross.task, dx=dx, dy=dy)
            if not copy_mode:
                mm.delete_overlay(tile.task, ov)
            self.ov_sel = [(cross.idx, new)]
            self.selected = {cross.idx}
            self.active = cross.idx
            self._changed([tile, cross], video=False)
            self._commit(token, "复制覆层到其他视频" if copy_mode else "移动覆层到其他视频",
                         [tile, cross])
            self.status.emit("已%s到 #%02d" % ("复制" if copy_mode else "移动", cross.idx + 1))
            return
        ddx, ddy = op.get("applied") or (0.0, 0.0)
        if abs(ddx) < 1e-6 and abs(ddy) < 1e-6:
            self._changed([tile], video=False)
            return
        sib_tiles = [t for t, _o in op.get("siblings") or []]
        if copy_mode:
            new = mm.copy_overlay_into(tile.task, ov, tile.task)
            mm.move_overlay(tile.task, ov, -ddx, -ddy)
            self.ov_sel = [(tile.idx, new)]
            for t, o in op.get("siblings") or []:
                sx, sy = (op.get("sib_applied") or {}).get(id(o), (0.0, 0.0))
                mm.copy_overlay_into(t.task, o, t.task)
                mm.move_overlay(t.task, o, -sx, -sy)
        self._changed([tile] + sib_tiles, video=False)
        n = len(sib_tiles)
        self._commit(token, "复制覆层" if copy_mode else "移动覆层", [tile] + sib_tiles)
        if n:
            self.status.emit("同步编辑：另外 %d 条视频里的同一条%s也%s了" % (
                n, "文字" if mm.overlay_kind(ov) == "text" else "图片", "复制" if copy_mode else "挪"))

    def cancel_op(self):
        op = self._op
        if op is None:
            return False
        self._op = None
        self._press = None
        self.history.cancel(op.get("token"))
        tiles = [op["tile"]] + [t for t in op.get("targets") or [] if t is not op["tile"]]
        tiles += [t for t, _o in op.get("siblings") or [] if t not in tiles]
        self._changed(tiles, prio=0)
        self._set_hud("")
        self.status.emit("已取消")
        return True

    # ─────────── 滚轮 ───────────

    def wheelEvent(self, ev):
        if not ev.modifiers() & Qt.KeyboardModifier.ControlModifier:
            ev.ignore()
            return
        steps = ev.angleDelta().y() / 120.0
        if not steps:
            ev.accept()
            return
        pos = ev.position()
        tile, kind, payload = self.hit(pos)
        if tile is None or kind in (None, "head", "divider"):
            self.set_tile_width(self.tile_w * (1.1 ** steps))
            ev.accept()
            return
        f = 1.1 ** steps
        x, y = tile.to_canvas(pos)
        key = (id(tile), kind, id(payload[1]) if kind == "overlay" else (payload[1] if payload else None))
        now = time.monotonic()
        sibs = self._overlay_siblings(tile, payload[1], self._targets(tile)) if kind == "overlay" else []
        if self._wheel_token is None or key != self._wheel_key or now - self._wheel_at > 0.8:
            targets = ([tile] + [t for t, _o in sibs]) if kind == "overlay" else self._targets(tile)
            self._wheel_token = self.history.begin([t.task for t in targets])
            self.history.commit(self._wheel_token, "滚轮缩放")
            self._wheel_key = key
        self._wheel_at = now
        if kind == "overlay":
            _name, ov = payload
            s = mm.scale_overlay(tile.task, ov, f, x, y)
            for t, o in sibs:                       # Q158 同步编辑：同一条也按同样倍率缩（绕各自中心）
                bx, by, bw, bh = mm.overlay_bounds(t.task, o)
                mm.scale_overlay(t.task, o, f, bx + bw / 2.0, by + bh / 2.0)
            tiles = [tile] + [t for t, _o in sibs]
            self._changed(tiles, video=False)
            self._set_hud("文字 %d%%" % round(s * 100) if mm.overlay_kind(ov) == "text" else "图片缩放", pos)
            self._commit_noop(tiles)
        elif kind == "cell" and payload and payload[0] is not None:
            tr, cid, rect = payload
            targets = self._targets(tile)
            for t in targets:
                mm.bake_task_split_geometry(t.task)
            ux = (x - rect[0]) - rect[2] / 2.0
            uy = (y - rect[1]) - rect[3] / 2.0
            s_new = mm.zoom_content(tr, f, ux, uy)
            linked = self._linked_same_cell(targets, cid, tile, tr)
            for t, tr2 in linked:
                mm.zoom_content(tr2, f, ux, uy)
            self._changed([tile], overlays=False, prio=0)
            self._changed([t for t, _tr in linked], overlays=False, prio=1)
            self._set_hud("缩放 %d%%" % round(s_new * 100), pos)
            self._commit_noop([tile] + [t for t, _tr in linked])
        QTimer.singleShot(900, self._clear_wheel_hud)
        ev.accept()

    def _clear_wheel_hud(self):
        if self._op is None and time.monotonic() - self._wheel_at > 0.8:
            self._set_hud("")

    def leaveEvent(self, _ev):
        if self._op is None and self._hover is not None:
            self._hover = None
            self.update()

    # ─────────── 键盘 ───────────

    _OWN_CTRL_KEYS = (Qt.Key.Key_Z, Qt.Key.Key_Y, Qt.Key.Key_A, Qt.Key.Key_C, Qt.Key.Key_V,
                      Qt.Key.Key_X, Qt.Key.Key_D, Qt.Key.Key_Equal, Qt.Key.Key_Plus,
                      Qt.Key.Key_Minus)

    def event(self, ev):
        # Q145：监视器是独立窗口。主窗口的全局快捷键（方向键逐帧、S 切割、空格播放…）
        # 不该在这里生效 —— 不带 Ctrl / Alt 的键、以及监视器自己的 Ctrl 键都留给监视器。
        if ev.type() == QEvent.Type.ShortcutOverride:
            mods = ev.modifiers()
            ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
            alt = bool(mods & Qt.KeyboardModifier.AltModifier)
            if (not ctrl and not alt) or (ctrl and not alt and ev.key() in self._OWN_CTRL_KEYS):
                ev.accept()
                return True
        return super().event(ev)

    def keyPressEvent(self, ev):
        key = ev.key()
        mods = ev.modifiers()
        ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
        shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)
        if key == Qt.Key.Key_N and not ev.isAutoRepeat():
            self._snap_off = True
            if self._op is not None:
                self._drag_op(self.mapFromGlobal(QCursor.pos()).toPointF(), mods)
            self.status.emit("按住 N：临时不吸附")
            return
        if key == Qt.Key.Key_Escape:
            if self.cancel_op():
                return
            if self.ov_sel:
                self.ov_sel = []
            else:
                self.selected.clear()
            self.selection_changed.emit()
            self.update()
            return
        if self._op is not None:
            if key in (Qt.Key.Key_Alt, Qt.Key.Key_Shift, Qt.Key.Key_Control):
                self._drag_op(self.mapFromGlobal(QCursor.pos()).toPointF(), mods)
            return
        if ctrl and key == Qt.Key.Key_Z:
            (self.redo if shift else self.undo)()
            return
        if ctrl and key == Qt.Key.Key_Y:
            self.redo()
            return
        if ctrl and key == Qt.Key.Key_A:
            self.select_all()
            return
        if ctrl and key == Qt.Key.Key_C:
            self.copy_selection()
            return
        if ctrl and key == Qt.Key.Key_V:
            self.paste(all_tiles=shift)
            return
        if ctrl and key == Qt.Key.Key_D:
            self.duplicate_selection()
            return
        if ctrl and key == Qt.Key.Key_X:
            if self.ov_sel:
                self.copy_selection()
                self.delete_selected_overlays()
                self.status.emit("已剪切 —— Ctrl+V 粘贴（主画布上也能粘）")
            return
        if ctrl and key in (Qt.Key.Key_Equal, Qt.Key.Key_Plus):
            self.set_tile_width(self.tile_w * 1.15)
            return
        if ctrl and key == Qt.Key.Key_Minus:
            self.set_tile_width(self.tile_w / 1.15)
            return
        if key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.delete_selected_overlays()
            return
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self.active is not None:
            self.open_task.emit(self.tiles[self.active].task)
            return
        if key == Qt.Key.Key_0 and not ctrl:
            self._reset_last_cell()
            return
        arrows = {Qt.Key.Key_Left: (-1, 0), Qt.Key.Key_Right: (1, 0),
                  Qt.Key.Key_Up: (0, -1), Qt.Key.Key_Down: (0, 1)}
        if key in arrows:
            step = 10.0 if shift else 1.0
            dx, dy = arrows[key]
            self.nudge(dx * step, dy * step)
            return
        super().keyPressEvent(ev)

    def keyReleaseEvent(self, ev):
        if ev.key() == Qt.Key.Key_N and not ev.isAutoRepeat():
            self._snap_off = False
            if self._op is not None:
                self._drag_op(self.mapFromGlobal(QCursor.pos()).toPointF(), ev.modifiers())
            return
        if self._op is not None and ev.key() in (Qt.Key.Key_Alt, Qt.Key.Key_Shift, Qt.Key.Key_Control):
            self._drag_op(self.mapFromGlobal(QCursor.pos()).toPointF(), ev.modifiers())
            return
        pos = self.mapFromGlobal(QCursor.pos()).toPointF()
        if self.rect().contains(pos.toPoint()):
            self._refresh_hover(pos, ev.modifiers())
        super().keyReleaseEvent(ev)

    def nudge(self, dx, dy):
        pairs = [(self.tiles[i], ov) for i, ov in self.ov_sel if i < len(self.tiles)]
        if pairs:
            # Q158 同步编辑：选中的其他视频里的同一条一起微移
            extra = []
            for tile, ov in pairs:
                for t, o in self._overlay_siblings(tile, ov, self._targets(tile)):
                    if not any(o is p for _t, p in pairs + extra):
                        extra.append((t, o))
            pairs = pairs + extra
            tiles = []
            key = ("nudge", tuple(id(ov) for _t, ov in pairs))
            now = time.monotonic()
            if self._wheel_key != key or now - self._wheel_at > 0.8:
                tok = self.history.begin([t.task for t, _ov in pairs])
                self.history.commit(tok, "微移覆层")
                self._wheel_key = key
            self._wheel_at = now
            for tile, ov in pairs:
                mm.move_overlay(tile.task, ov, dx, dy)
                tiles.append(tile)
            self._changed(tiles, video=False)
            self._commit_noop(tiles)
            return
        if self._last_cell is None:
            self.status.emit("先点一下要微移的文字 / 图片，或者一格画面")
            return
        idx, cid = self._last_cell
        if idx >= len(self.tiles):
            return
        tile = self.tiles[idx]
        slot = getattr(self, "_last_slot", None)
        tr = self._slot_track(tile.task, cid, slot)
        if tr is None:
            return
        key = ("nudge_cell", idx, cid)
        now = time.monotonic()
        targets = self._targets(tile)
        if self._wheel_key != key or now - self._wheel_at > 0.8:
            tok = self.history.begin([t.task for t in targets])
            self.history.commit(tok, "微移画面")
            self._wheel_key = key
        self._wheel_at = now
        for t in targets:
            mm.bake_task_split_geometry(t.task)
        mm.pan_content(tr, dx, dy)
        linked = self._linked_same_cell(targets, cid, tile, tr)
        for t, tr2 in linked:
            mm.pan_content(tr2, dx, dy)
        self._changed([tile] + [t for t, _tr in linked], overlays=False, prio=0)
        self._commit_noop([tile] + [t for t, _tr in linked])

    def _reset_last_cell(self):
        if self._last_cell is None or self._last_cell[0] >= len(self.tiles):
            return
        idx, cid = self._last_cell
        tile = self.tiles[idx]
        targets = self._targets(tile)
        slot = getattr(self, "_last_slot", None)
        tok = self.history.begin([t.task for t in targets])
        for t in targets:
            tr = self._slot_track(t.task, cid, slot)
            if tr is not None:
                mm.reset_track_transform(tr)
        self._changed(targets, overlays=False, prio=0)
        self._commit(tok, "重置画面", targets)

    @staticmethod
    def _slot_track(task, cid, slot):
        """分屏 = 那一格的轨道；不分屏 = 同一批（主视频 / 第 N 条外部视频），没记过就是主视频。"""
        if cid:
            return mm.cell_track(task, cid)
        if slot:
            return mm.track_for_slot(task, slot)
        return mm.main_video_track(task)

    # ─────────── 复制 / 粘贴 / 删除 ───────────

    def copy_selection(self):
        pairs = [(self.tiles[i], ov) for i, ov in self.ov_sel if i < len(self.tiles)]
        if pairs:
            self._clipboard = {"kind": "overlays",
                               "items": [(t.task, mm._plain_copy(ov)) for t, ov in pairs]}
            self.status.emit("已复制 %d 个文字 / 图片 —— 选中别的视频按 Ctrl+V 粘贴" % len(pairs))
            return
        if self.active is not None and self.active < len(self.tiles):
            clip = mm.split_clipboard(self.tiles[self.active].task)
            if clip:
                self._clipboard = {"kind": "split", "data": clip}
                self.status.emit("已复制这条视频的分屏（布局、分界线、边框、每格平移缩放）")
                return
        self.status.emit("没有可复制的：先选中文字 / 图片，或一条开了分屏的视频")

    def paste(self, all_tiles=False):
        clip = self._clipboard
        if not clip:
            self.status.emit("剪贴板是空的")
            return
        if clip.get("kind") == "layers":
            self.paste_layers(clip.get("mode") or "material", all_tiles=all_tiles)
            return
        targets = list(self.tiles) if all_tiles else self.selected_tiles()
        if not targets and self.active is not None:
            targets = [self.tiles[self.active]]
        if not targets:
            self.status.emit("先选中要粘贴到的视频")
            return
        tok = self.history.begin([t.task for t in targets])
        if clip["kind"] == "overlays":
            new_sel = []
            for tile in targets:
                for src_task, ov in clip["items"]:
                    new = mm.copy_overlay_into(src_task, ov, tile.task)
                    new_sel.append((tile.idx, new))
            self.ov_sel = new_sel
            self._changed(targets, video=False)
            self._commit(tok, "粘贴覆层", targets)
            self.status.emit("已粘贴到 %d 条视频" % len(targets))
        elif clip["kind"] == "split":
            for tile in targets:
                mm.paste_split(tile.task, clip["data"])
            self._changed(targets, prio=1)
            self._commit(tok, "粘贴分屏", targets)
            self.status.emit("分屏已套用到 %d 条视频" % len(targets))

    def copy_layers(self, tile, layers, mode):
        from cutora.batch import layer_transfer as lt
        if tile is None:
            self.status.emit("先点一条视频")
            return
        pack = lt.pack_layers(tile.task, layers, mode)
        if not pack:
            self.status.emit("没有可复制的图层")
            return
        self._clipboard = pack
        self.status.emit(lt.describe_copy(pack))

    def paste_layers(self, mode, all_tiles=False):
        from cutora.batch import layer_transfer as lt
        pack = self._clipboard
        if not isinstance(pack, dict) or pack.get("kind") != "layers":
            self.status.emit("剪贴板里没有图层。先在右侧图层面板，或时间线上复制一层")
            return
        if mode and mode != pack.get("mode"):
            pack = dict(pack)
            pack["mode"] = mode
        targets = list(self.tiles) if all_tiles else self.selected_tiles()
        if not targets and self.active is not None:
            targets = [self.tiles[self.active]]
        if not targets:
            self.status.emit("先选中要粘贴到的视频")
            return
        tok = self.history.begin([t.task for t in targets], snap=mm.snapshot_layers)
        totals = {"added": 0, "styled": 0, "missed": 0}
        for tile in targets:
            one = lt.apply_pack(tile.task, pack)
            for key in totals:
                totals[key] += one[key]
        self.status.emit(lt.describe_apply(len(targets), totals))
        if totals["added"] == 0 and totals["styled"] == 0:
            return
        kinds = {str(it.get("kind") or "") for it in (pack.get("items") or []) if isinstance(it, dict)}
        need_video = "video" in kinds
        need_ov = bool(kinds & {"text", "image", "adjust"})
        # 文本 / 图片 / 音频不重新解码视频。只有视频层变了才补缩略图，而且只补看得到的。
        if need_video or need_ov:
            self._changed(targets, video=need_video, overlays=need_ov, visible_only=True)
        label = {"material": "粘贴素材", "style": "粘贴属性", "all": "粘贴全部属性"}.get(mode, "粘贴图层")
        self._commit(tok, label, targets)

    def _add_layer_menus(self, menu, tile):
        from cutora.batch import layer_transfer as lt
        if tile is not None:
            layers = lt.list_layers(tile.task)
            sub = menu.addMenu("复制图层")
            sub.setToolTipsVisible(True)
            if not layers:
                empty = sub.addAction("这条时间线上没有图层")
                empty.setEnabled(False)
            else:
                copy_all = (
                    ("material", "复制全部素材", "按原来的起止时间复制。对方这个时间已有内容就新开一条轨道"),
                    ("style", "复制全部属性", "只复制属性，贴到对方同一种、同一序号的图层"),
                    ("all", "复制全部图层的全部属性", "连内容、时间和素材来源一起复制，贴到对应图层"),
                )
                for mode, text, tip in copy_all:
                    act = sub.addAction(text, lambda m=mode, ls=list(layers), t=tile: self.copy_layers(t, ls, m))
                    act.setToolTip(tip)
                sub.addSeparator()
                for layer in layers:
                    menu_layer = sub.addMenu(lt.layer_title(layer))
                    menu_layer.setToolTipsVisible(True)
                    menu_layer.addAction("素材（原时间，可新开轨道）",
                                         lambda L=layer, t=tile: self.copy_layers(t, [L], "material"))
                    menu_layer.addAction("属性（贴到对应图层）",
                                         lambda L=layer, t=tile: self.copy_layers(t, [L], "style"))
                    menu_layer.addAction("全部属性",
                                         lambda L=layer, t=tile: self.copy_layers(t, [L], "all"))
        n = len(self.selected_tiles()) or (1 if tile is not None else 0)
        if n:
            paste = menu.addMenu("粘贴图层到选中的 %d 条" % n)
            paste.addAction("粘贴素材", lambda: self.paste_layers("material", False))
            paste.addAction("粘贴属性到对应图层", lambda: self.paste_layers("style", False))
            paste.addAction("粘贴全部属性", lambda: self.paste_layers("all", False))
        total = len(self.tiles)
        if total > 1:
            every = menu.addMenu("粘贴图层到全部 %d 条" % total)
            every.addAction("粘贴素材", lambda: self.paste_layers("material", True))
            every.addAction("粘贴属性到对应图层", lambda: self.paste_layers("style", True))
            every.addAction("粘贴全部属性", lambda: self.paste_layers("all", True))

    def duplicate_selection(self):
        """Ctrl+D：选中的文字 / 图片在原视频里复制一份（错开 20 px）。"""
        pairs = [(self.tiles[i], ov) for i, ov in self.ov_sel if i < len(self.tiles)]
        if not pairs:
            self.status.emit("先点选要复制的文字 / 图片")
            return
        tiles = []
        for tile, _ov in pairs:
            if tile not in tiles:
                tiles.append(tile)
        tok = self.history.begin([t.task for t in tiles])
        new_sel = []
        for tile, ov in pairs:
            new = mm.copy_overlay_into(tile.task, ov, tile.task, dx=20.0, dy=20.0)
            new_sel.append((tile.idx, new))
        self.ov_sel = new_sel
        self._changed(tiles, video=False)
        self._commit(tok, "原地复制", tiles)
        self.status.emit("已复制 %d 个（Ctrl+D）" % len(new_sel))

    def delete_selected_overlays(self):
        pairs = [(self.tiles[i], ov) for i, ov in self.ov_sel if i < len(self.tiles)]
        if not pairs:
            self.status.emit("先点选要删除的文字 / 图片")
            return
        tiles = []
        for tile, _ov in pairs:
            if tile not in tiles:
                tiles.append(tile)
        tok = self.history.begin([t.task for t in tiles])
        n = 0
        for tile, ov in pairs:
            if mm.delete_overlay(tile.task, ov):
                n += 1
        self.ov_sel = []
        self._changed(tiles, video=False)
        self._commit(tok, "删除覆层", tiles)
        self.status.emit("已删除 %d 个（Ctrl+Z 撤销）" % n)

    # ─────────── 右键菜单 ───────────

    def _context_menu(self, ev):
        from cutora.batch.split_assign import BORDER_LABELS
        pos = ev.position()
        tile, kind, payload = self.hit(pos)
        menu = QMenu(self)
        if tile is None:
            menu.addAction("全选  Ctrl+A", self.select_all)
            if self.selected:
                menu.addAction("清空选择  Esc", self.clear_selection)
            self._add_layout_menus(menu)
            self._add_layer_menus(menu, None)
            menu.exec(ev.globalPosition().toPoint())
            return
        if tile.idx not in self.selected:
            self.select_only(tile)
        targets = self.selected_tiles() or [tile]
        n = len(targets)
        suffix = "（%d 条）" % n if n > 1 else ""
        menu.addAction("在主窗口打开  Enter", lambda: self.open_task.emit(tile.task))
        if getattr(self, "focus_idx", None) == tile.idx:
            menu.addAction("恢复网格", lambda: self.set_focus_tile(None))
        else:
            menu.addAction(
                "放大这一格（其余仍同步）",
                lambda idx=tile.idx: self.set_focus_tile(idx))
        menu.addSeparator()
        # Q177：「当前图层」= 点中的文字 / 图片，或右侧图层面板里选中的那一层，或点中的视频 ——
        # 它自己的属性 + 叠放顺序，同一批一起改
        from cutora.ui.ai_studio_room import has_rmbg_original, restore_rmbg_original_ui, run_rmbg_cutout_ui
        ref = self.current_layer_ref(tile, kind, payload)
        if ref is not None and ref.kind == "video":
            cid = payload[1] if (kind == "cell" and payload and ref.track is payload[0]) else (
                getattr(ref.track, "split_cell", None) if mm.split_on(tile.task) else None)
            self.add_layer_ops_menu(menu, tile, ref.track, cid, targets)
            menu.addSeparator()
        elif ref is not None:
            self.add_layer_props_menu(menu, ref, targets)
            if ref.kind == "image":
                sibs = [o for _t, o, _tr in self.sibling_layers(ref, targets) if isinstance(o, dict)]
                if not sibs and isinstance(ref.obj, dict):
                    sibs = [ref.obj]
                if sibs:
                    menu.addAction(
                        "✂️ AI 一键抠图去背景 (RMBG-2.0 发丝级)%s" % suffix,
                        lambda ovs=list(sibs), tg=list(targets): run_rmbg_cutout_ui(
                            overlays=ovs, monitor_grid=self, monitor_tiles=tg, parent=self
                        ),
                    )
                    if any(has_rmbg_original(o) for o in sibs):
                        menu.addAction(
                            "↩️ 还原抠图前原图%s" % suffix,
                            lambda ovs=list(sibs), tg=list(targets): restore_rmbg_original_ui(
                                overlays=ovs, monitor_grid=self, monitor_tiles=tg
                            ),
                        )
            menu.addSeparator()
        if kind == "overlay":
            _name, ov = payload
            if not any(o is ov for _i, o in self.ov_sel):
                self.ov_sel = [(tile.idx, ov)]
            menu.addAction("删除这个文字 / 图片  Delete", self.delete_selected_overlays)
            menu.addAction("复制  Ctrl+C", self.copy_selection)
            menu.addSeparator()
        kind_clip = self._clipboard.get("kind") if isinstance(self._clipboard, dict) else None
        if kind_clip in ("overlays", "split"):
            what = "文字 / 图片" if kind_clip == "overlays" else "分屏"
            menu.addAction("粘贴%s到选中的视频%s  Ctrl+V" % (what, suffix), lambda: self.paste(False))
            menu.addAction("粘贴%s到全部 %d 条视频  Ctrl+Shift+V" % (what, len(self.tiles)),
                           lambda: self.paste(True))
            menu.addSeparator()
        self._add_layout_menus(menu)
        self._add_split_quick(menu, targets, suffix)       # Q171：取消分屏 / 恢复分屏，一键批量
        self._add_layer_menus(menu, tile)
        menu.addSeparator()
        self._add_key_menu(menu, tile, kind, payload, targets, suffix)   # Q171：批量蓝绿幕抠像
        # 紧邻「蓝绿幕抠像」提供「AI 图片抠图去背景 (RMBG-2.0)」
        all_img_ovs = [
            o for t in targets
            for o in (t.task.get("image_overlays") or [])
            if isinstance(o, dict) and o.get("type") != "adjust"
        ]
        if all_img_ovs:
            menu.addAction(
                "✂️ AI 图片抠图去背景 (RMBG-2.0 发丝级)%s" % suffix,
                lambda ovs=list(all_img_ovs), tg=list(targets): run_rmbg_cutout_ui(
                    overlays=ovs, monitor_grid=self, monitor_tiles=tg, parent=self
                ),
            )
            if any(has_rmbg_original(o) for o in all_img_ovs):
                menu.addAction(
                    "↩️ 还原图片抠图前原图%s" % suffix,
                    lambda ovs=list(all_img_ovs), tg=list(targets): restore_rmbg_original_ui(
                        overlays=ovs, monitor_grid=self, monitor_tiles=tg
                    ),
                )
        else:
            menu.addAction(
                "✂️ AI 抠图当前画面帧 (RMBG-2.0 发丝级)",
                lambda t=tile: run_rmbg_cutout_ui(
                    task=t.task, frame_sec=float(getattr(t, "t", 0.0) or 0.0),
                    monitor_grid=self, monitor_tiles=[t], parent=self
                ),
            )
        menu.addSeparator()
        if mm.split_on(tile.task):
            sub2 = menu.addMenu("分界线样式%s" % suffix)
            cur = str((mm.split_of(tile.task).get("border") or {}).get("style") or "none")
            for style, label in BORDER_LABELS.items():
                act = sub2.addAction(label, lambda _s=style: self._apply_border(targets, _s))
                act.setCheckable(True)
                act.setChecked(style == cur)
            menu.addAction("分界线均分%s" % suffix, lambda: self._apply_each(
                targets, mm.reset_dividers, "分界线均分"))
            if any(len(mm.divider_lines(t.task)) > 1 for t in targets):
                menu.addAction("副屏均分（保持主屏比例）%s" % suffix, lambda: self._apply_each(
                    targets, mm.reset_secondary_dividers, "副屏均分"))
            cells = list(mm.cell_rects(tile.task))
            if kind == "cell" and payload and payload[1] and len(cells) > 1:
                cid = payload[1]
                sw = menu.addMenu("格子 %s 与…交换" % cid)
                for other in cells:
                    if other != cid:
                        sw.addAction("格子 %s" % other,
                                     lambda _o=other, _c=cid: self._apply_each(
                                         targets, lambda t: mm.swap_cells(t, _c, _o), "交换格子"))
        if kind == "cell" and payload and payload[0] is not None:
            cid = payload[1]
            menu.addAction("重置这一格的平移 / 缩放%s  0" % suffix, lambda: self._apply_each(
                targets, lambda t: self._reset_cell(t, cid), "重置画面"))
        menu.addAction("重置全部格子的平移 / 缩放%s" % suffix, lambda: self._apply_each(
            targets, self._reset_all_cells, "重置画面"))
        menu.addSeparator()
        self._add_bg_fill_menu(menu, tile, targets, suffix)
        self._add_backdrop_menu(menu, tile, targets, suffix)
        if mm.split_on(tile.task):
            menu.addAction("复制这条的分屏设置  Ctrl+C", lambda: self._copy_split(tile))
        menu.addSeparator()
        self._add_undo_menu(menu, tile, targets, suffix)    # Q171：开了的东西在一个地方批量关掉
        menu.exec(ev.globalPosition().toPoint())

    # Q154：右键批量改背景填充（多选后一次改选中的全部视频）
    BG_FILL_CHOICES = (
        ("none", "无（露黑底）"),
        ("lens", "镜头模糊背景"),
        ("legacy", "高斯模糊背景（老观感）"),
        ("mirror", "镜像填充"),
        ("tile", "平铺填充"),
        ("stretch", "拉伸填充"),
    )

    def _add_bg_fill_menu(self, menu, tile, targets, suffix):
        from cutora.render.fill_bg import fill_spec
        sub = menu.addMenu("背景填充%s" % suffix)
        cur = ""
        tr = mm.main_video_track(tile.task)
        clips = list(getattr(tr, "clips", []) or []) if tr is not None else []
        if clips:
            spec = fill_spec(clips[0])
            cur = spec["mode"] if spec["mode"] != "blur" else spec["blur_style"]
        for key, label in self.BG_FILL_CHOICES:
            act = sub.addAction(label, lambda _k=key: self._apply_bg_fill(targets, _k))
            act.setCheckable(True)
            act.setChecked(key == cur)
        sub.setToolTip("画面没铺满画幅时露出来的地方补什么。改的是这条视频里全部视频片段（含分屏各格）。")
        return sub

    @staticmethod
    def set_bg_fill(task, key: str) -> int:
        """给这条视频里全部视频片段设背景填充。key：none / lens / legacy / mirror / tile / stretch。返回改了几条。

        Q155：选了填充就是要看到每格各自的填充 —— 整幅背景（外部图 / 外部视频 / 单个视频撑满）一并去掉。"""
        from cutora.model.project_schema import BG_FILL_DEFAULTS
        from cutora.render import backdrop as BG
        BG.set_each(task)
        tl = mm.timeline_of(task)
        n = 0
        for tr in getattr(tl, "tracks", None) or []:
            if getattr(tr, "type", "") != "video" or BG.is_backdrop_track(tr):
                continue
            for c in getattr(tr, "clips", None) or []:
                if getattr(c, "is_thumbnail_leadin", False) or getattr(c, "is_intro_video_leadin", False):
                    continue
                cur = dict(getattr(c, "bg_fill", None) or BG_FILL_DEFAULTS)
                if key in ("lens", "legacy"):
                    cur["mode"] = "blur"
                    cur["blur_style"] = key
                else:
                    cur["mode"] = key
                c.bg_fill = cur
                n += 1
        return n

    def _apply_bg_fill(self, targets, key):
        label = dict(self.BG_FILL_CHOICES).get(key, key)
        self._apply_each(targets, lambda task: self.set_bg_fill(task, key), "背景填充：%s" % label)
        self.status.emit("已把 %d 条视频的背景填充改成「%s」" % (len(targets), label))

    # ─────────── Q155 整幅背景（分屏不再一格一块） ───────────

    BACKDROP_LABELS = {
        "each": "每格各自（每格垫自己的模糊）",
        "full": "单个视频撑满整幅",
        "image": "外部图片",
        "video": "外部视频（循环）",
    }

    def _add_backdrop_menu(self, menu, tile, targets, suffix):
        from cutora.render import backdrop as BG
        sub = menu.addMenu("整幅背景%s" % suffix)
        sub.setToolTipsVisible(True)
        cur = BG.mode_of(tile.task)
        act = sub.addAction(self.BACKDROP_LABELS["each"],
                            lambda: self._apply_backdrop(targets, "each"))
        act.setCheckable(True)
        act.setChecked(cur == "each")
        act.setToolTip("现在的样子：分屏时每一格垫自己那条视频的背景（左右分屏就是左右两块）")
        any_split = any(mm.split_on(t.task) for t in targets)
        full = sub.addAction("单个视频撑满整幅（原视频）",
                             lambda: self._apply_backdrop(targets, "full"))
        full.setCheckable(True)
        full.setChecked(cur == "full")
        full.setEnabled(any_split)
        full.setToolTip("整个画面只垫一条视频的模糊，各格留白处都露出它 —— 左右分屏不再割裂")
        if mm.split_on(tile.task):
            cells = list(mm.cell_rects(tile.task))
            if len(cells) > 1:
                pick = sub.addMenu("单个视频撑满 · 指定哪一格")
                bd = (mm.split_of(tile.task).get("backdrop") or {})
                cur_cell = str(bd.get("cell") or BG.main_cell(tile.task) or "")
                for cid in cells:
                    a = pick.addAction("%s 屏的视频" % cid,
                                       lambda _c=cid: self._apply_backdrop(targets, "full", cell=_c))
                    a.setCheckable(True)
                    a.setChecked(cur == "full" and cid == cur_cell)
        sub.addSeparator()
        img = sub.addAction("外部图片作为背景…", lambda: self._pick_backdrop_file(targets, "image"))
        img.setCheckable(True)
        img.setChecked(cur == "image")
        img.setToolTip("选一张图，铺满整个画面垫在最底下（选中的几条一起换）")
        vid = sub.addAction("外部视频作为背景（循环铺满时长）…",
                            lambda: self._pick_backdrop_file(targets, "video"))
        vid.setCheckable(True)
        vid.setChecked(cur == "video")
        vid.setToolTip("选一段视频，在最底下加一条「整幅背景」轨：铺满画面、静音，循环播放到这条视频结束")
        if any(BG.has_external(t.task) for t in targets):
            sub.addSeparator()
            sub.addAction("去掉外部背景", lambda: self._apply_backdrop(targets, "each"))
        return sub

    def _pick_backdrop_file(self, targets, kind):
        from cutora.batch.batch_load import IMAGE_EXTS, VIDEO_EXTS
        if kind == "image":
            flt = "图片 (%s)" % " ".join("*" + e for e in IMAGE_EXTS)
            title = "选一张图作为整幅背景"
        else:
            flt = "视频 (%s)" % " ".join("*" + e for e in VIDEO_EXTS)
            title = "选一段视频作为整幅背景（循环铺满时长）"
        path = _pick_file(self, title, flt)
        if path:
            self._apply_backdrop(targets, kind, path=path)

    def _apply_backdrop(self, targets, mode, *, cell=None, path=None):
        from cutora.render import backdrop as BG
        targets = [t for t in (targets or []) if t is not None]
        if not targets:
            return 0
        duration = None
        if mode == "video":
            from cutora.batch.script_broll_scatter import probe_duration
            duration = float(probe_duration(path) or 0.0)
            if duration <= 0:
                self.status.emit("读不到这段视频的时长：%s" % os.path.basename(str(path or "")))
                return 0
        done = [0]
        skipped = [0]

        def _one(task):
            if mode == "each":
                BG.set_each(task)
            elif mode == "full":
                if not BG.set_full(task, cell if cell in mm.cell_rects(task) else None):
                    skipped[0] += 1
                    return
            elif mode == "image":
                BG.set_image(task, path)
            elif mode == "video":
                BG.set_video(task, path, duration=duration)
            done[0] += 1

        label = "整幅背景：%s" % self.BACKDROP_LABELS.get(mode, mode)
        self._apply_each(targets, _one, label)
        msg = "已把 %d 条视频的背景改成「%s」" % (done[0], self.BACKDROP_LABELS.get(mode, mode))
        if path:
            msg += "（%s）" % os.path.basename(str(path))
        if skipped[0]:
            msg += "；%d 条没开分屏，跳过" % skipped[0]
        self.status.emit(msg)
        return done[0]

    @staticmethod
    def _reset_cell(task, cid):
        tr = mm.cell_track(task, cid) if cid else mm.main_video_track(task)
        if tr is not None:
            mm.reset_track_transform(tr)

    @staticmethod
    def _reset_all_cells(task):
        for tr in mm.video_tracks(task):
            mm.reset_track_transform(tr)

    def _copy_split(self, tile):
        clip = mm.split_clipboard(tile.task)
        if clip:
            self._clipboard = {"kind": "split", "data": clip}
            self.status.emit("已复制分屏设置 —— 选中别的视频按 Ctrl+V")

    def _add_layout_menus(self, menu):
        """右键：选中的一批，和全部视频，各一份分屏布局。已经全选时只留一份。"""
        from cutora.batch.split_assign import LAYOUT_HINTS, SPLIT_CHOICES, layout_label
        everyone = list(self.tiles)
        if not everyone:
            return
        selected = self.selected_tiles()
        if selected and len(selected) < len(everyone):
            title = ("分屏布局 · 这一条" if len(selected) == 1
                     else "分屏布局 · 选中的 %d 条" % len(selected))
            groups = [(title, selected), ("分屏布局 · 全部 %d 条" % len(everyone), everyone)]
        else:
            targets = selected or everyone
            groups = [("分屏布局 · 全部 %d 条" % len(targets), targets)]
        for title, targets in groups:
            sub = menu.addMenu(title)
            sub.setToolTipsVisible(True)
            self._fill_layout_submenu(sub, targets, SPLIT_CHOICES, layout_label, LAYOUT_HINTS)

    def _fill_layout_submenu(self, sub, targets, choices, layout_label, hints):
        shared = self._shared_layout(targets)
        picked = list(targets)
        off = sub.addAction("关闭分屏", lambda tg=picked: self._apply_layout(tg, None))
        off.setCheckable(True)
        off.setChecked(shared is None)
        restorable = [t for t in picked if self._split_off_with_layout(t.task)]
        if restorable:
            back = sub.addAction("恢复关掉前的布局（%d 条）" % len(restorable),
                                 lambda tg=restorable: self.restore_split(tg))
            back.setToolTip("布局、分界线位置、每格放哪条视频都和关之前一样")
        sub.addSeparator()
        act_rand = sub.addAction(
            "🎲 随机换创意分屏样式",
            lambda tg=picked: self._apply_each(
                tg,
                lambda tk: __import__("cutora.render.split_layout", fromlist=["apply_custom_creative_split"]).apply_custom_creative_split(tk, mode="random"),
                "随机换创意分屏",
            ),
        )
        act_rand.setToolTip("从 L 形、动感斜夹心、六角形中主四角、相机快门穿插、心形、鸽子形、幸运草、橄榄叶等十余种分屏样式中随机挑一种")
        act_shuf = sub.addAction(
            "🔀 随机交换外部视频位置",
            lambda tg=picked: self._apply_each(
                tg,
                lambda tk: __import__("cutora.render.split_layout", fromlist=["shuffle_secondary_tracks"]).shuffle_secondary_tracks(tk),
                "随机交换分屏位置",
            ),
        )
        act_shuf.setToolTip("保持主媒体视频不动，随机改变各个外部加载视频出现在哪个小分屏里")
        sub.addSeparator()
        for lay in choices:
            act = sub.addAction(
                layout_label(lay),
                lambda _l=lay, tg=picked: self._apply_layout(tg, _l),
            )
            tip = hints.get(lay)
            if tip:
                act.setToolTip(tip)
            act.setCheckable(True)
            act.setChecked(shared == lay)

    @staticmethod
    def _shared_layout(targets):
        """这些视频如果是同一种布局，返回布局 id（关掉是 None）。混着的返回标记。"""
        if not targets:
            return ("mixed",)
        names = [mm.layout_name(t.task) for t in targets]
        first = names[0]
        if all(n == first for n in names):
            return first
        return ("mixed",)

    def _apply_each(self, targets, fn, label):
        tok = self.history.begin([t.task for t in targets])
        for t in targets:
            try:
                fn(t.task)
            except Exception as exc:
                _LOG.exception("%s 失败 #%d", label, t.idx + 1)
                self.status.emit("%s：#%02d 失败（%s）" % (label, t.idx + 1, exc))
        self._changed(targets, prio=1, visible_only=True)
        self._commit(tok, label, targets)

    def _apply_layout(self, targets, layout):
        from cutora.batch.split_assign import layout_label
        targets = [t for t in (targets or []) if t is not None]
        if not targets:
            self.status.emit("先选中要改分屏的视频")
            return
        want = layout or None
        changed = [t for t in targets if mm.layout_name(t.task) != want]
        shown = "关闭分屏" if want is None else layout_label(want)
        skipped = len(targets) - len(changed)
        if not changed:
            if want is None:
                self.status.emit("这 %d 条都没有开分屏" % len(targets))
            else:
                self.status.emit("这 %d 条已经是%s" % (len(targets), shown))
            return
        tok = self.history.begin([t.task for t in changed], snap=mm.snapshot_split)
        errors = 0
        for t in changed:
            try:
                mm.set_layout(t.task, want)
            except Exception as exc:
                errors += 1
                _LOG.exception("换分屏布局失败 #%d", t.idx + 1)
                self.status.emit("%s：#%02d 失败（%s）" % (shown, t.idx + 1, exc))
        self._changed(changed, overlays=False, visible_only=True)
        self._commit(tok, "关闭分屏" if want is None else "换分屏布局", changed, overlays=False)
        ok = len(changed) - errors
        if want is None:
            msg = "已关闭 %d 条的分屏" % ok
        else:
            msg = "已把 %d 条改成%s" % (ok, shown)
        if skipped:
            msg += "（%d 条本来就是，跳过）" % skipped
        if errors:
            msg += "，%d 条失败" % errors
        self.status.emit(msg)

    # Q154：批量挪分界线（对话框里的滑杆）
    def divider_choices(self, targets) -> list:
        """选中的视频里出现过的分界线：[(id, 文案)]，按序号排。"""
        seen = {}
        has_sub = False
        for t in targets:
            lines = mm.divider_lines(t.task)
            if len(lines) > 1:
                has_sub = True
            for d in lines:
                did = d["id"]
                if did not in seen:
                    seen[did] = d["axis"]
        def _sort_key(item):
            num = item[0].lstrip("d")
            return (int(num) if num.isdigit() else 999, item[0])
        out = []
        for did, axis in sorted(seen.items(), key=_sort_key):
            num = did.lstrip("d") or did
            dir_lbl = "上下" if axis == "h" else "左右"
            if has_sub:
                role = "主分界" if did == "d1" else ("副屏 %d" % max(1, int(num) - 1) if num.isdigit() else "副分界")
                out.append((did, "第 %s 条（%s）· %s" % (num, role, dir_lbl)))
            else:
                out.append((did, "第 %s 条 · %s" % (num, dir_lbl)))
        return out

    def divider_pos_of(self, targets, did) -> float | None:
        vals = [d["pos"] for t in targets for d in mm.divider_lines(t.task) if d["id"] == did]
        return (sum(vals) / len(vals)) if vals else None

    def begin_batch_divider(self, targets):
        return self.history.begin([t.task for t in targets])

    def set_batch_divider(self, targets, did, pos: float) -> int:
        """把这些视频的第 did 条分界线都挪到 pos（0–1）。只作废画面，调度器一格一格补。"""
        n = 0
        hit = []
        for t in targets:
            if any(d["id"] == did for d in mm.divider_lines(t.task)):
                try:
                    mm.set_divider_pos(t.task, did, float(pos))
                    n += 1
                    hit.append(t)
                except Exception:
                    _LOG.exception("批量挪分界线失败 #%d", t.idx + 1)
        if hit:
            self._changed(hit, overlays=False, prio=1, visible_only=True)
        return n

    def commit_batch_divider(self, token, targets, label="批量挪分界线"):
        self._commit(token, label, targets, overlays=False)

    def _apply_border(self, targets, style):
        self._apply_each(targets, lambda task: mm.set_border_style(task, style), "分界线样式")

    # ─────────── 拖素材进来 ───────────

    @staticmethod
    def _drop_paths(mime):
        out = []
        if mime is None or not mime.hasUrls():
            return out
        for url in mime.urls():
            path = url.toLocalFile()
            if path and os.path.isfile(path) and mm.media_kind(path):
                out.append(path)
        return out

    def dragEnterEvent(self, ev):
        paths = self._drop_paths(ev.mimeData())
        if not paths:
            ev.ignore()
            return
        ev.acceptProposedAction()
        self._update_drop(ev.position(), paths)

    def dragMoveEvent(self, ev):
        paths = self._drop_paths(ev.mimeData())
        if not paths or not self._update_drop(ev.position(), paths):
            ev.ignore()
            self.update()
            return
        ev.acceptProposedAction()

    def dragLeaveEvent(self, _ev):
        self._drop = None
        self._set_hud("")
        self.update()

    def _drop_targets(self, tile):
        if tile.idx in self.selected and len(self.selected) > 1:
            return self.selected_tiles()
        return [tile]

    def _update_drop(self, pos, paths):
        tile = self.tile_at(pos)
        if tile is None or not tile.rect.contains(pos):
            self._drop = None
            self._set_hud("")
            return False
        x, y = tile.to_canvas(pos)
        targets = self._drop_targets(tile)
        kinds = {mm.media_kind(p) for p in paths}
        self._drop = {"targets": targets, "frac": (x / tile.cw, y / tile.ch),
                      "has_video": "video" in kinds, "has_image": "image" in kinds}
        n_vid = sum(1 for p in paths if mm.media_kind(p) == "video")
        if len(targets) > 1:
            how = "逐条分配 %d 段视频" % n_vid if n_vid > 1 else "放到 %d 条选中的视频" % len(targets)
        else:
            if "video" in kinds:
                if mm.split_on(tile.task):
                    how = "放进格子 %s" % (mm.cell_at(tile.task, x, y) or "?")
                else:
                    how = "开分屏，放在%s" % ("上面" if y < tile.ch / 2 else "下面")
            else:
                how = "放在这里"
        self._set_hud("松开：%s" % how, pos)
        return True

    def dropEvent(self, ev):
        paths = self._drop_paths(ev.mimeData())
        pos = ev.position()
        self._drop = None
        self._set_hud("")
        tile = self.tile_at(pos)
        if not paths or tile is None or not tile.rect.contains(pos):
            ev.ignore()
            self.update()
            return
        ev.acceptProposedAction()
        self.drop_media(tile, pos, paths)

    def drop_media(self, tile, pos, paths):
        x, y = tile.to_canvas(pos)
        fx, fy = x / tile.cw, y / tile.ch
        targets = self._drop_targets(tile)
        videos = [p for p in paths if mm.media_kind(p) == "video"]
        images = [p for p in paths if mm.media_kind(p) == "image"]
        tok = self.history.begin([t.task for t in targets])
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        msgs = []
        errors = 0
        try:
            for i, t in enumerate(targets):
                tx, ty = fx * t.cw, fy * t.ch
                try:
                    if videos:
                        vid = videos[i % len(videos)] if len(videos) > 1 else videos[0]
                        msgs.append(mm.drop_video(t.task, vid, tx, ty))
                    for j, img in enumerate(images):
                        off = j * 0.04 * t.cw
                        mm.drop_image(t.task, img, tx + off, ty + off)
                except Exception as exc:
                    errors += 1
                    _LOG.exception("放素材失败 #%d", t.idx + 1)
                    msgs.append("#%02d 失败：%s" % (t.idx + 1, exc))
        finally:
            QApplication.restoreOverrideCursor()
        self.selected = {t.idx for t in targets}
        self.active = tile.idx
        self._changed(targets, prio=0)
        self._commit(tok, "放入素材", targets)
        if len(targets) == 1:
            self.status.emit(msgs[0] if msgs else "已放入 %d 张图片" % len(images))
        else:
            self.status.emit("已放到 %d 条视频%s" % (len(targets) - errors,
                                                   "，%d 条失败（见日志）" % errors if errors else ""))


# ═════════════════════════ 窗口 ═════════════════════════

SHORTCUT_HELP = """<b>鼠标</b><br>
拖分界线 — 调比例（吸附 1/3 · 1/2 · 2/3）· Alt+拖分界线 = 旋转 · 按住 N 临时不吸附<br>
拖画面 — 平移这一格（没分屏就是整个画面），Shift 锁横竖<br>
Ctrl + 拖画面 / Ctrl + 滚轮 — 以光标为中心缩放<br>
Alt + 拖格子 — 与另一格交换<br>
拖文字 / 图片 — 移动；拖到别的视频 = 移过去<br>
Alt + 拖文字 / 图片 — 复制（本条或别的视频）<br>
Ctrl + 拖文字 / 图片 — 缩放（文字无损）<br>
双击 分界线 = 均分 · 格子 = 重置 · 标题 = 主窗口打开<br>
Ctrl / Shift + 点、空白处框选 — 多选视频<br>
右键 → 分屏布局：改选中的，或一次改全部（含「左右完整」，两条都完整进画面）<br>
右侧图层，或右键「复制图层」：按层复制素材 / 属性，再批量贴到选中的视频（时间原样，不够就新开轨道）。批量粘贴和撤销只补屏幕里看得到的格子，一次解码一格<br>
从素材库拖图片 / 视频进来 — 放到落点（落在选中的视频上 = 全部选中的都放）<br><br>
<b>键盘</b><br>
Delete 删除 · Ctrl+C / X / V 复制剪切粘贴（Ctrl+Shift+V 粘到全部；和主画布共用剪贴板）· Ctrl+D 原地复制<br>
Ctrl+Z / Ctrl+Y 撤销重做 · 方向键微移（Shift ×10）<br>
Ctrl+A 全选 · Esc 取消拖动 / 清空选择 · Enter 主窗口打开 · 0 重置当前格<br>
Ctrl + = / − 缩略图大小 · Ctrl + 空白处滚轮 同上<br><br>
<b>同步编辑</b>：打开后，在多选里的任意一条上拖分界线 / 平移 / 缩放 / 换布局，
选中的全部一起变。"""


class CentralMonitorDialog(QDialog):
    """非模态窗口：工具条 + 网格 + 状态栏。"""

    edited = Signal(object)

    def __init__(self, host, tasks, parent=None, title="中央监视器"):
        owner = parent if isinstance(parent, QWidget) else (host if isinstance(host, QWidget) else None)
        super().__init__(owner)
        self.host = host
        self.setWindowTitle(title)
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.WindowMinMaxButtonsHint
                            | Qt.WindowType.WindowCloseButtonHint)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.resize(SPACE_6 * 44, SPACE_6 * 28)
        try:
            from cutora.batch.script_ui import apply_plugin_style
            apply_plugin_style(self)
        except Exception:
            pass
        root = QVBoxLayout(self)
        root.setContentsMargins(SPACE_3, SPACE_3, SPACE_3, SPACE_2)
        root.setSpacing(SPACE_2)

        bar = QHBoxLayout()
        bar.setSpacing(SPACE_3)
        self.lbl_time = QLabel("时间 0.0 s")
        self.sl_time = QSlider(Qt.Orientation.Horizontal)
        self.sl_time.setMinimumWidth(SPACE_6 * 6)
        self.sl_time.setToolTip("所有视频看同一个时间点")
        self.ck_follow = QCheckBox("各自跳到第一条文字 / 图片")
        self.ck_follow.setToolTip("每条视频显示它第一条文字 / 图片出现时的画面")
        self.ck_all = QCheckBox("显示全部文字 / 图片")
        self.ck_all.setChecked(True)
        self.ck_all.setToolTip("不看时间，每条都显示出来（不在这个时间的按它自己的中间时刻画）")
        self.ck_subs = QCheckBox("显示字幕")
        self.ck_subs.setChecked(True)
        self.ck_link = QCheckBox("同步编辑选中的视频")
        self.ck_link.setToolTip("多选后，在其中一条上改分屏 / 平移 / 缩放，选中的一起变；\n拖 / 缩放 / 微移一条文字或图片时，其他选中视频里的同一条（批量加的同一张图、同一条标题）也跟着动")
        self.sl_size = QSlider(Qt.Orientation.Horizontal)
        self.sl_size.setRange(TILE_MIN, TILE_MAX)
        self.sl_size.setValue(TILE_DEFAULT)
        self.sl_size.setMaximumWidth(SPACE_6 * 4)
        self.sl_size.setToolTip("缩略图大小（Ctrl + = / −）")
        self.btn_play_all = QPushButton("全部播放")
        self.btn_play_all.setCheckable(True)
        self.btn_play_all.setToolTip("每个窗口各自循环播放（只看画面不出声）。每个窗口左下角也有自己的 ▶，底部进度条可拖。")
        self.ck_bars = QCheckBox("进度条")
        self.ck_bars.setChecked(True)
        self.ck_bars.setToolTip("显示 / 隐藏每个窗口底部的播放进度条（关掉会停掉全部播放）")
        self.btn_fit = QPushButton("一屏看全")
        self.btn_undo = QPushButton("撤销")
        self.btn_redo = QPushButton("重做")
        self.btn_help = QPushButton("快捷键")
        for b in (self.btn_play_all, self.btn_fit, self.btn_undo, self.btn_redo, self.btn_help):
            b.setObjectName("btnSecondary")
        bar.addWidget(self.lbl_time)
        bar.addWidget(self.sl_time, 2)
        bar.addWidget(self.ck_follow)
        bar.addWidget(self.ck_all)
        bar.addWidget(self.ck_subs)
        bar.addWidget(self.ck_link)
        bar.addWidget(self.btn_play_all)
        bar.addWidget(self.ck_bars)
        bar.addStretch(1)
        bar.addWidget(QLabel("大小"))
        bar.addWidget(self.sl_size)
        bar.addWidget(self.btn_fit)
        bar.addWidget(self.btn_undo)
        bar.addWidget(self.btn_redo)
        bar.addWidget(self.btn_help)
        root.addLayout(bar)

        # Q154：批量挪分界线 —— 多选里有开分屏的视频才出现这一行
        from PySide6.QtWidgets import QComboBox, QSpinBox
        self.div_bar = QWidget()
        dl = QHBoxLayout(self.div_bar)
        dl.setContentsMargins(0, 0, 0, 0)
        dl.setSpacing(SPACE_3)
        self.lbl_div = QLabel("分界线")
        self.cb_div = QComboBox()
        self.cb_div.setToolTip("挪哪一条分界线（上下布局就一条；上 1 下 2、九宫格有好几条）")
        self.sl_div = QSlider(Qt.Orientation.Horizontal)
        self.sl_div.setRange(5, 95)
        self.sl_div.setMinimumWidth(SPACE_6 * 8)
        self.sl_div.setToolTip("拖 = 选中的视频一起挪这条分界线（上下 / 左右）。松手记一次撤销。")
        self.sp_div = QSpinBox()
        self.sp_div.setRange(5, 95)
        self.sp_div.setSuffix(" %")
        self.btn_div_even = QPushButton("均分")
        self.btn_div_even.setObjectName("btnSecondary")
        self.btn_div_even.setToolTip("选中的视频分界线全部回到均分")
        self.btn_div_sub_even = QPushButton("副屏均分")
        self.btn_div_sub_even.setObjectName("btnSecondary")
        self.btn_div_sub_even.setToolTip("保持主屏（A 格）占比不变，仅把外部副屏分界线（d2、d3…）恢复等分")
        self.lbl_div_hint = QLabel("")
        self.lbl_div_hint.setObjectName("hint")
        for w in (self.lbl_div, self.cb_div, self.sl_div, self.sp_div, self.btn_div_even, self.btn_div_sub_even):
            dl.addWidget(w)
        dl.addWidget(self.lbl_div_hint, 1)
        self.div_bar.hide()
        root.addWidget(self.div_bar)
        self._div_token = None
        self._div_targets = []
        self.cb_div.currentIndexChanged.connect(lambda _i: self._sync_divider_bar(keep_choice=True))
        self.sl_div.sliderPressed.connect(self._div_press)
        self.sl_div.valueChanged.connect(self._div_moved)
        self.sl_div.sliderReleased.connect(self._div_release)
        self.sp_div.editingFinished.connect(self._div_spin)
        self.btn_div_even.clicked.connect(self._div_even)
        self.btn_div_sub_even.clicked.connect(self._div_sub_even)

        self.scroll = _GridScroll()
        self.grid = MonitorGrid()
        self.scroll.setWidget(self.grid)
        self.scroll.grid = self.grid
        from cutora.ui.layer_panel import LayerPanel
        self.layers = LayerPanel(self.grid)
        self.grid.layer_panel = self.layers           # Q177：右键「当前图层」读面板里选中的那一层
        split = _MonitorSplit()
        split.addWidget(self.scroll)
        split.addWidget(self.layers)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 0)
        self.split = split
        root.addWidget(split, 1)

        foot = QHBoxLayout()
        foot.setSpacing(SPACE_3)
        self.lbl_status = QLabel("")
        self.lbl_status.setObjectName("hint")
        self.lbl_sel = QLabel("")
        foot.addWidget(self.lbl_status, 1)
        foot.addWidget(self.lbl_sel)
        root.addLayout(foot)

        self.grid.status.connect(self.lbl_status.setText)
        self.grid.selection_changed.connect(self._sync_bar)
        self.grid.edited.connect(self._on_edited)
        self.grid.open_task.connect(self._open_in_main)
        self._time_timer = QTimer(self)
        self._time_timer.setSingleShot(True)
        self._time_timer.setInterval(60)
        self._time_timer.timeout.connect(self._apply_time)
        self.sl_time.valueChanged.connect(self._time_moved)
        self.ck_follow.toggled.connect(self.grid.set_follow_overlay)
        self.ck_all.toggled.connect(self.grid.set_show_all)
        self.ck_subs.toggled.connect(self.grid.set_show_subs)
        self.ck_link.toggled.connect(self._link_toggled)
        self.sl_size.valueChanged.connect(self.grid.set_tile_width)
        self.btn_play_all.toggled.connect(self._on_play_all)
        self.ck_bars.toggled.connect(self._on_bars)
        self.btn_fit.clicked.connect(self.fit_all)
        self.btn_undo.clicked.connect(self.grid.undo)
        self.btn_redo.clicked.connect(self.grid.redo)
        self.btn_help.clicked.connect(self._show_help)
        self._last_activate = 0.0
        self.set_tasks(tasks)
        QTimer.singleShot(0, self.fit_all)
        self.lbl_status.setText("拖分界线、拖画面、Ctrl+拖缩放；文字可以直接拖到别的视频上。点「快捷键」看全部。")

    # ─────────── 对外 ───────────

    def set_tasks(self, tasks):
        tasks = [t for t in (tasks or []) if isinstance(t, dict)]
        # 记下每条在主窗口 tasks 里的位置：主窗口撤销 / 重做会整体换成新的 task 对象，
        # 监视器要跟着换，不然改的是已经没人用的旧对象（改了等于没改）。
        host_tasks = list(getattr(self.host, "tasks", None) or [])
        self._host_idx = [next((i for i, h in enumerate(host_tasks) if h is t), -1) for t in tasks]
        self.grid.set_tasks(tasks)
        dur = max([mm.task_duration(t) for t in tasks] or [0.0])
        self.sl_time.blockSignals(True)
        self.sl_time.setRange(0, max(1, int(math.ceil(dur * 10))))
        self.sl_time.blockSignals(False)
        self._sync_bar()

    def refresh_all(self):
        self._sync_with_host()
        self.grid.refresh_all()

    def _sync_with_host(self) -> bool:
        """主窗口的 task 对象换了（撤销 / 重做 / 重新打开）→ 监视器换成新的那一份。"""
        host_tasks = list(getattr(self.host, "tasks", None) or [])
        idx = getattr(self, "_host_idx", None) or []
        cur = [t.task for t in self.grid.tiles]
        if not host_tasks or len(idx) != len(cur):
            return False
        new = []
        changed = False
        for i, old in zip(idx, cur):
            if 0 <= i < len(host_tasks) and host_tasks[i] is not old:
                new.append(host_tasks[i])
                changed = True
            else:
                new.append(old)
        if not changed:
            return False
        self.grid.history = mm.History()      # 旧快照指向旧对象，不能再用
        self.grid.set_tasks(new)
        self._host_idx = [next((j for j, h in enumerate(host_tasks) if h is t), -1) for t in new]
        self.lbl_status.setText("主窗口撤销 / 重做过，监视器已跟上最新的工程（监视器里的撤销记录清空）")
        return True

    def fit_all(self):
        self.grid.fit_all(self.scroll.viewport().size())
        self.sl_size.blockSignals(True)
        self.sl_size.setValue(int(self.grid.tile_w))
        self.sl_size.blockSignals(False)

    # ─────────── Q154：播放 ───────────

    def _on_play_all(self, on):
        if on and not self.ck_bars.isChecked():
            self.ck_bars.setChecked(True)
        self.grid.play_all(bool(on))
        self.lbl_status.setText("全部窗口各自循环播放中（只看画面）。点某个窗口左下角的 ▶ 可以单独停 / 播，底部进度条可拖。"
                                if on else "已全部暂停")

    def _on_bars(self, on):
        self.grid.set_show_bars(bool(on))
        if not on:
            self.btn_play_all.blockSignals(True)
            self.btn_play_all.setChecked(False)
            self.btn_play_all.blockSignals(False)

    # ─────────── 内部 ───────────

    def _time_moved(self, v):
        self.lbl_time.setText("时间 %.1f s" % (v / 10.0))
        self._time_timer.start()

    def _apply_time(self):
        self.grid.set_time(self.sl_time.value() / 10.0)

    def _link_toggled(self, on):
        self.grid.link_edit = bool(on)
        self._sync_bar()

    # ─────────── Q154：批量挪分界线 ───────────

    def _div_targets_now(self) -> list:
        return [t for t in self.grid.selected_tiles() if mm.split_on(t.task)]

    def _sync_divider_bar(self, keep_choice=False):
        if self.sl_div.isSliderDown():
            return
        targets = self._div_targets_now()
        choices = self.grid.divider_choices(targets)
        if not targets or not choices:
            self.div_bar.hide()
            return
        cur = self.cb_div.currentData() if keep_choice else None
        self.cb_div.blockSignals(True)
        self.cb_div.clear()
        for did, label in choices:
            self.cb_div.addItem(label, did)
        i = self.cb_div.findData(cur) if cur else -1
        self.cb_div.setCurrentIndex(max(0, i))
        self.cb_div.blockSignals(False)
        did = self.cb_div.currentData()
        pos = self.grid.divider_pos_of(targets, did)
        if pos is not None:
            v = int(round(pos * 100))
            for w in (self.sl_div, self.sp_div):
                w.blockSignals(True)
                w.setValue(max(5, min(95, v)))
                w.blockSignals(False)
        n_hit = sum(1 for t in targets if any(d["id"] == did for d in mm.divider_lines(t.task)))
        has_sub = any(cid != "d1" for cid, _lbl in choices)
        self.btn_div_sub_even.setVisible(has_sub)
        self.lbl_div_hint.setText("选中 %d 条开了分屏，其中 %d 条有这条线" % (len(targets), n_hit))
        self.div_bar.show()

    def _div_press(self):
        self._div_targets = self._div_targets_now()
        self._div_token = self.grid.begin_batch_divider(self._div_targets)

    def _div_moved(self, v):
        targets = self._div_targets if self.sl_div.isSliderDown() else self._div_targets_now()
        did = self.cb_div.currentData()
        if not targets or not did:
            return
        self.sp_div.blockSignals(True)
        self.sp_div.setValue(int(v))
        self.sp_div.blockSignals(False)
        if not self.sl_div.isSliderDown():
            # 键盘 / 点轨道：一步一次撤销
            tok = self.grid.begin_batch_divider(targets)
            self.grid.set_batch_divider(targets, did, v / 100.0)
            self.grid.commit_batch_divider(tok, targets)
            return
        self.grid.set_batch_divider(targets, did, v / 100.0)

    def _div_release(self):
        targets, tok = self._div_targets, self._div_token
        self._div_targets, self._div_token = [], None
        if targets and tok is not None:
            self.grid.commit_batch_divider(tok, targets)
            self.lbl_status.setText("已把 %d 条视频的分界线挪到 %d%%（可撤销）" % (len(targets), self.sl_div.value()))

    def _div_spin(self):
        v = int(self.sp_div.value())
        if v != self.sl_div.value():
            self.sl_div.setValue(v)          # 走 _div_moved（非拖动分支）记一次撤销

    def _div_even(self):
        targets = self._div_targets_now()
        if not targets:
            return
        self.grid._apply_each(targets, mm.reset_dividers, "分界线均分")
        self._sync_divider_bar(keep_choice=True)

    def _div_sub_even(self):
        targets = self._div_targets_now()
        if not targets:
            return
        self.grid._apply_each(targets, mm.reset_secondary_dividers, "副屏均分")
        self._sync_divider_bar(keep_choice=True)

    def _sync_bar(self):
        n = len(self.grid.selected)
        total = len(self.grid.tiles)
        txt = "共 %d 条 · 已选 %d" % (total, n)
        if self.grid.link_edit and n > 1:
            txt += " · 同步编辑中"
        self.lbl_sel.setText(txt)
        panel = getattr(self, "layers", None)
        if panel is not None:
            panel.sync()
        self.btn_undo.setEnabled(self.grid.history.can_undo())
        self.btn_redo.setEnabled(self.grid.history.can_redo())
        try:
            self._sync_divider_bar(keep_choice=True)
        except Exception:
            _LOG.exception("刷新分界线滑杆失败")
        self.sl_size.blockSignals(True)
        self.sl_size.setValue(int(self.grid.tile_w))
        self.sl_size.blockSignals(False)

    def _show_help(self):
        from PySide6.QtWidgets import QMessageBox
        box = QMessageBox(self)
        box.setWindowTitle("中央监视器 · 快捷键")
        box.setTextFormat(Qt.TextFormat.RichText)
        box.setText(SHORTCUT_HELP)
        box.setModal(False)
        if getattr(self, "_embedded_room", False):
            from cutora.ui.inwindow import show_sheet      # Q192：快捷键说明也是主窗口里的浮层
            show_sheet(box)
            return
        box.show()

    def _on_edited(self, tasks):
        host = self.host
        try:
            if hasattr(host, "mark_dirty"):
                host.mark_dirty()
            cur = None
            idx = getattr(host, "curr_idx", -1)
            all_tasks = getattr(host, "tasks", None) or []
            if 0 <= idx < len(all_tasks):
                cur = all_tasks[idx]
            if cur is not None and any(cur is t for t in tasks):
                if hasattr(host, "refresh_ui"):
                    host.refresh_ui()
            if hasattr(host, "video_canvas"):
                host.video_canvas.update()
            if hasattr(host, "timeline"):
                host.timeline.update()
        except Exception:
            _LOG.exception("监视器改完刷新主窗口失败")
        self._host_autosave(force=False)
        self.edited.emit(tasks)
        self._sync_bar()

    def _host_autosave(self, force: bool) -> None:
        """改动都直接写在主窗口的 task 对象上（内存里已经是最新的）；这里再顺手落一份自动保存快照：
        改动中每 15 秒最多一次，关窗时强制写一次 —— 监视器里改了几十条再崩，也丢不了。"""
        fn = getattr(self.host, "_autosave_now", None)
        if not callable(fn):
            return
        try:
            fn(force=bool(force))
        except Exception:
            _LOG.exception("监视器自动保存失败")

    def _open_in_main(self, task):
        host = self.host
        tasks = getattr(host, "tasks", None) or []
        idx = next((i for i, t in enumerate(tasks) if t is task), -1)
        if idx < 0:
            return
        try:
            fl = getattr(host, "file_list", None)
            if fl is not None and hasattr(fl, "setCurrentRow"):
                fl.setCurrentRow(idx)
                item = fl.item(idx) if hasattr(fl, "item") else None
                if item is not None and hasattr(host, "on_click"):
                    host.on_click(item)
            rooms = getattr(host, "room_host", None)
            if rooms is not None and getattr(self, "_embedded_room", False):
                rooms.enter("edit")            # Q191：监视器是主窗口里的房间 → 切回剪辑页看这一条
                return
            host.raise_()
            host.activateWindow()
        except Exception:
            _LOG.exception("在主窗口打开失败")

    def changeEvent(self, ev):
        from PySide6.QtCore import QEvent
        if ev.type() == QEvent.Type.ActivationChange and self.isActiveWindow():
            now = time.monotonic()
            if now - self._last_activate > 1.0:
                self._last_activate = now
                self._sync_with_host()
                self.grid.refresh_all()
        super().changeEvent(ev)

    def _teardown(self):
        if getattr(self, "_torn_down", False):
            return
        self._torn_down = True
        try:
            self.grid.shutdown()
        finally:
            # 窗口先关掉，下一拍再落盘：整理工程快照要花点时间，别让关窗这一下卡住
            host = self.host
            QTimer.singleShot(0, lambda h=host, me=self: me._host_autosave(force=True))

    def closeEvent(self, ev):
        self._teardown()
        super().closeEvent(ev)

    def done(self, r):
        self._teardown()
        super().done(r)


class _GridScroll(QScrollArea):
    grid = None

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(False)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFrameShape(QScrollArea.Shape.NoFrame)
        self.verticalScrollBar().valueChanged.connect(self._scrolled)

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        if self.grid is not None:
            self.grid.relayout(self.viewport().width())

    def _scrolled(self, _v):
        if self.grid is not None:
            self.grid._schedule()
            self.grid._compose_timer.start()


def open_central_monitor(host, tasks=None, parent=None):
    """打开（或拉到前面）中央监视器。tasks 不给就看主窗口的全部视频。"""
    if tasks is None:
        tasks = list(getattr(host, "tasks", None) or [])
    owner = parent or host
    dlg = getattr(owner, "_central_monitor", None)
    try:
        if dlg is not None and dlg.isVisible():
            dlg.set_tasks(tasks)
            dlg.raise_()
            dlg.activateWindow()
            return dlg
    except RuntimeError:
        dlg = None
    dlg = CentralMonitorDialog(host, tasks, parent=owner)
    try:
        owner._central_monitor = dlg
    except Exception:
        pass
    dlg.show()
    return dlg
