# SPDX-License-Identifier: GPL-3.0-or-later
# Extracted from the T01 monolith by tools/split_monolith.py.
# Original lines: 1974-2030, 2067-2108, 2110-3752

import os
import numpy as np
import time
from PySide6.QtWidgets import (
    QWidget,
    QTextEdit,
    QFrame,
    QMessageBox,
    QSizePolicy,
    QFileDialog,
    QMenu,
    QLabel,
    QGraphicsOpacityEffect,
)
from PySide6.QtCore import (
    Qt,
    Signal,
    Slot,
    QPoint,
    QRect,
    QRectF,
    QPointF,
    QTimer,
    QPropertyAnimation,
    QEvent,
)
from PySide6.QtGui import (
    QColor,
    QFont,
    QPainter,
    QPen,
    QFontMetrics,
    QImage,
    QPixmap,
    QTextCursor,
    QPalette,
    QTextOption,
    QKeySequence,
    QShortcut,
)
from PySide6.QtMultimedia import QVideoSink
from cutora.render import anim_engine as anim_engine
from cutora.ui.panels.fonts import (
    DEFAULT_FONT_FAMILY,
    sanitize_font_family,
)
from cutora.subtitle.text_utils import (
    _rich_default_layout,
    _qt_align_flag_for_text,
    _draw_thumbnail_text_on_rect,
)
from cutora.model.timeline import (
    DEFAULT_TIMELINE_WIDTH,
    DEFAULT_TIMELINE_HEIGHT,
    DEFAULT_VIDEO_FIT_MODE,
)
from PySide6.QtWidgets import (
    QMenu,
    QFileDialog,
    QMessageBox,
)
from PySide6.QtCore import Qt
from cutora.render.frame_renderer import (
    BLEND_MODES,
    _BLEND_NORMAL,
    compute_render_order,
    FrameRenderer,
)
from cutora.render.readers import AsyncVideoDecoder
from cutora.model.timeline import DEFAULT_TIMELINE_HEIGHT, DEFAULT_TIMELINE_WIDTH, DEFAULT_VIDEO_FIT_MODE
from cutora.render.frame_renderer import BLEND_MODES, FrameRenderer, _BLEND_NORMAL, compute_render_order
from cutora.render.readers import AsyncVideoDecoder, PREVIEW_DECODE_THREADS
from cutora.subtitle.text_utils import _draw_thumbnail_text_on_rect, _qt_align_flag_for_text, _rich_default_layout
from cutora.ui.panels.fonts import DEFAULT_FONT_FAMILY, render_font_family, sanitize_font_family  # noqa: F401
from cutora.core import preview_perf as _preview_perf
from cutora.core.seek_trace import emit as _seek_trace_emit
from cutora.core.counters import bump as _bump

# Q188：画布同时活着的预览解码器上限 / 闲置多久回收 / 转场合成备用解码器上限。
# 播放时同一时刻通常只碰 2～4 个源；41 个源各开一个（以前）= 3.6 GB 常驻。
PREVIEW_DECODER_POOL = 6
PREVIEW_DECODER_IDLE_S = 30.0
PREVIEW_CAP_POOL = 4
from cutora.ui.alpha_checker import paint_alpha_checker, task_has_alpha_fx  # Q165

def _tl_of(*a, **k):
    from cutora.ui.main_window import _tl_of as _impl
    return _impl(*a, **k)


def _preview_solo_ids(canvas):
    win = None
    getter = getattr(canvas, "window", None)
    if callable(getter):
        try:
            win = getter()
        except Exception:
            win = None
    return getattr(getattr(win, "timeline", None), "_solo_ids", set()) or set()

# --- Inline Text Editor (Canvas) ---
class InlineTextEdit(QTextEdit):
    def __init__(self, parent, commit_cb, cancel_cb):
        super().__init__(parent)
        self.setParent(parent)
        self.setWindowFlags(Qt.WindowType.Widget)
        self._commit_cb = commit_cb
        self._cancel_cb = cancel_cb

    def paintEvent(self, event):
        super().paintEvent(event)
        if not self.hasFocus():
            return
        try:
            p = QPainter(self.viewport())
            p.setPen(QPen(QColor(255, 255, 255, 220), 1))
            r = self.cursorRect()
            p.drawLine(r.left(), r.top(), r.left(), r.bottom())
        finally:
            try:
                p.end()
            except Exception:
                pass

    def focusOutEvent(self, event):
        try:
            if callable(self._commit_cb):
                self._commit_cb()
        except RuntimeError:
            pass  # 画布已经销毁（退出时）
        finally:
            super().focusOutEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Return or event.key() == Qt.Key.Key_Enter:
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                self.insertPlainText("\n")
                return
            else:
                if callable(self._commit_cb):
                    self._commit_cb()
                return
        if event.key() == Qt.Key.Key_Escape:
            if callable(self._cancel_cb):
                self._cancel_cb()
            return
        super().keyPressEvent(event)

    def insertFromMimeData(self, source):
        # 粘贴导入逻辑：严格保留原文，仅统一换行符，绝不改字
        try:
            text = source.text()
        except Exception:
            text = None
        if text is None:
            super().insertFromMimeData(source)
            return
        normalized = str(text).replace("\r\n", "\n").replace("\r", "\n")
        self.insertPlainText(normalized)

class CanvasTextEditor(InlineTextEdit):
    """Q127：所见即所得的隐形编辑器。

    以前双击文本会把渲染结果藏起来、盖一个半透明白框 QTextEdit，字体颜色
    折行全是另一套 —— 编辑时看到的和成片完全不是一回事。现在：

    * 字仍然由 textfx 照常画（描边 / 阴影 / 渐变 / 自动折行一个不少），
      每敲一个键 content 就变、画布就重画；
    * 这个控件不画任何东西（paintEvent 直接 return），只负责键盘、IME、
      撤销重做和选区语义；
    * 光标 / 选区 / 输入法预编辑下划线由画布按真实排版（text_caret）画。
    """

    def __init__(self, canvas, commit_cb, cancel_cb):
        super().__init__(canvas, commit_cb, cancel_cb)
        # 弱引用：强引用会让画布的 Python 壳活到这个编辑框被 deleteLater 的那一刻，
        # 然后在子控件析构途中把父控件一起拖下水 —— 段错误。
        import weakref
        self._canvas_ref = weakref.ref(canvas)
        self.preedit = ""
        self.setObjectName("canvasTextEditor")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_InputMethodEnabled, True)
        self.viewport().setAutoFillBackground(False)
        self.viewport().setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        # 外观在 theme_dark.qss.j2 的 QTextEdit#canvasTextEditor（全透明），不在这里写样式
        self.setCursorWidth(0)
        self.setAcceptRichText(False)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

    def paintEvent(self, event):  # noqa: D401 - 故意不画
        return

    def keyPressEvent(self, event):
        # 排版软件的约定（Canva / Figma / PPT）：Enter 换行，Ctrl+Enter 或 Esc 结束
        # 编辑并保留；打错了在编辑态里 Ctrl+Z。老编辑框那套 Enter=提交只留给字幕。
        key = event.key()
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if event.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier):
                if callable(self._commit_cb):
                    self._commit_cb()
                return
            self.insertPlainText("\n")
            return
        if key == Qt.Key.Key_Escape:
            if callable(self._commit_cb):
                self._commit_cb()
            return
        QTextEdit.keyPressEvent(self, event)

    def inputMethodEvent(self, event):
        super().inputMethodEvent(event)
        try:
            self.preedit = str(event.preeditString() or "")
        except Exception:
            self.preedit = ""
        cb = getattr(self._canvas, "_wysiwyg_preedit_changed", None)
        if callable(cb):
            cb()

    @property
    def _canvas(self):
        try:
            return self._canvas_ref()
        except Exception:
            return None

    def inputMethodQuery(self, query):
        if query == Qt.InputMethodQuery.ImCursorRectangle:
            fn = getattr(self._canvas, "_wysiwyg_caret_widget_rect", None)
            r = fn() if callable(fn) else None
            if r is not None:
                # 候选框跟着画布上的真光标走，不是跟着这个隐形控件里的假光标
                return QRect(r.translated(-self.pos()))
        return super().inputMethodQuery(query)


class MultiGridTextEdit(QTextEdit):
    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        self.main_window = main_window
        self.task_idx = -1
        self.grid_idx = -1
        self.setObjectName("MultiGridTextEdit")

    def insertFromMimeData(self, source):
        if source.hasText():
            text = source.text()
            # 智能拦截表格多行复制，切换到专业的 CSV/TSV 解析模式
            # 以应付 Google Sheets 或 Excel 中单元格内部包含换行符（会被双引号 " " 整体包裹）的情况
            try:
                import csv, io
                # 统一转为标准换行符供 csv.reader 消化
                text = text.replace('\r\n', '\n').replace('\r', '\n')
                f = io.StringIO(text)
                # 表格复制出来默认是用 \t (Tab) 分隔列
                reader = csv.reader(f, delimiter='\t')
                
                lines = []
                for row in reader:
                    if row:
                        lines.append('\t'.join(row))
                    else:
                        lines.append("")
                        
                # 剔除末尾可能因 Excel 复制带上的多余空行
                while lines and not lines[-1].strip():
                    lines.pop()
                    
                if len(lines) > 1:
                    # 转发给父应用做向下扩散粘贴
                    if hasattr(self.main_window, 'handle_batch_grid_paste'):
                        self.main_window.handle_batch_grid_paste(self.grid_idx, lines)
                    return
            except Exception as e:
                print(f"[MultiGrid] Paste parse error: {e}")
                pass
                
        super().insertFromMimeData(source)

def _rect_after_resize(rect, dx, dy, handle, min_size=20.0):
    """Keep the rect's current aspect. The opposite corner or edge stays put."""
    src = QRectF(rect)
    aspect = float(src.width()) / float(src.height()) if src.height() > 1e-6 else 1.0
    aspect = max(aspect, 1e-6)
    handle = str(handle or "")
    floor = float(min_size)
    if handle in ("se", "nw", "ne", "sw"):
        if handle == "se":
            ax, ay = src.left(), src.top()
            px, py = src.right() + dx, src.bottom() + dy
            sx, sy = 1.0, 1.0
        elif handle == "nw":
            ax, ay = src.right(), src.bottom()
            px, py = src.left() + dx, src.top() + dy
            sx, sy = -1.0, -1.0
        elif handle == "ne":
            ax, ay = src.left(), src.bottom()
            px, py = src.right() + dx, src.top() + dy
            sx, sy = 1.0, -1.0
        else:
            ax, ay = src.right(), src.top()
            px, py = src.left() + dx, src.bottom() + dy
            sx, sy = -1.0, 1.0
        adx = max(floor, (px - ax) * sx)
        ady = max(floor, (py - ay) * sy)
        if adx >= ady * aspect:
            w = adx
            h = w / aspect
        else:
            h = ady
            w = h * aspect
        left = ax if sx > 0 else ax - w
        top = ay if sy > 0 else ay - h
        return QRectF(left, top, w, h)
    if handle in ("e", "w"):
        if handle == "e":
            w = max(floor, src.width() + dx)
            left = src.left()
        else:
            w = max(floor, src.width() - dx)
            left = src.right() - w
        h = max(floor, w / aspect)
        return QRectF(left, src.center().y() - h / 2.0, w, h)
    if handle in ("n", "s"):
        if handle == "s":
            h = max(floor, src.height() + dy)
            top = src.top()
        else:
            h = max(floor, src.height() - dy)
            top = src.bottom() - h
        w = max(floor, h * aspect)
        return QRectF(src.center().x() - w / 2.0, top, w, h)
    return QRectF(src)


class _ScopeFloat(QWidget):
    """Scope strip beside the preview, not painted over the picture.

    A frameless tool window so a canvas grab of the video does not include
    it. Default position is just outside the canvas; the user can drag it.
    """

    def __init__(self, canvas):
        super().__init__(
            canvas,
            Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint,
        )
        from PySide6.QtWidgets import QVBoxLayout

        self.setObjectName("scopeFloat")
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self._user_moved = False
        self._drag = None
        self._label = QLabel(self)
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.addWidget(self._label)
        self.resize(280, 150)
        pal = self.palette()
        pal.setColor(self.backgroundRole(), QColor(16, 16, 16))
        self.setAutoFillBackground(True)
        self.setPalette(pal)
        self.hide()

    def set_image(self, img):
        from PySide6.QtGui import QPixmap

        if img is None or img.isNull():
            self._label.clear()
            return
        max_w = 320
        w = min(max_w, max(160, int(img.width())))
        h = max(72, int(round(w * float(img.height()) / max(1, int(img.width())))))
        h = min(h, 200)
        pix = QPixmap.fromImage(img).scaled(
            max(8, w - 12),
            max(8, h - 12),
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._label.setPixmap(pix)
        if not self._user_moved:
            self.resize(w, h)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            self._user_moved = True
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag is not None and (event.buttons() & Qt.MouseButton.LeftButton):
            self.move(event.globalPosition().toPoint() - self._drag)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag = None
        super().mouseReleaseEvent(event)


# --- VideoCanvas (Restored) ---
from cutora.ui.canvas_ops import CanvasOpsMixin  # noqa: E402  Q145


class VideoCanvas(CanvasOpsMixin, QWidget):
    item_selected = Signal(str, object) 
    item_dragged = Signal()
    gesture_began = Signal()
    gesture_ended = Signal()
    multi_selection_changed = Signal(int)   # Q128：多选数量变化
    item_edit_request = Signal(str, object)
    sub_text_selected = Signal(object)
    sub_textbox_resized = Signal(object)
    sub_double_clicked = Signal(int)
    fullscreen_toggle_requested = Signal()
    structure_changed = Signal()     # Q145：增删了文字 / 图片（删除、粘贴、复制）→ 时间线要重建
    gesture_cancelled = Signal()     # Q145：Esc 取消正在拖的 → 恢复到按下前

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("VideoCanvas")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._preview_fullscreen = False
        self._init_fullscreen_chrome()
        self._video_sink = QVideoSink()
        self._video_sink.videoFrameChanged.connect(self._on_frame_changed)
        self._current_image = None
        self._task_data = None
        self._video_dims = (1920, 1080) # Timeline dims for overlays
        self._frame_dims = (1920, 1080) # Source frame dims
        self._sub_text_selecting = False
        self._sub_text_sel_range = None
        self._sub_text_sel_rects = []
        # === 多视频源：OpenCV 异步解码缓冲池 ===
        # Q188：有上限的 LRU 池 + 闲置回收。以前每个源一个解码器、永不关，41 个源的工程主界面吃 3.6 GB。
        from collections import OrderedDict as _OD
        self._async_decoders = _OD()  # source_id → AsyncVideoDecoder（LRU：最近用的在末尾）
        self._cv_caps = _OD()  # source_id → cv2.VideoCapture (转场合成备用，同样限量)
        self._decoder_reaper = QTimer(self)
        self._decoder_reaper.setInterval(5000)
        self._decoder_reaper.timeout.connect(self._reap_idle_decoders)
        self._decoder_reaper.start()
        self._cv_frame_cache = {}  # source_id → (time, QImage)
        self._cv_export_states = {}
        self._export_rw = None
        self._cv_sar_cache = {}  # source_id → (sar_num, sar_den)
        # 交互状态
        self._selected_item = None
        self._selected_rect = None
        self._drag_mode = None   # "move" | "resize" | "rotate"
        self._resize_handle = None
        self._rotate_origin = None
        self._alpha_hit_cache = {}
        self._opaque_hit_frames = {}
        self._current_time = 0
        self._painting = False
        self._geom_last_log_t = 0.0
        
        self._hit_records = []
        self._full_frame = None        # Q175 播放中工作线程合成好的整幅画面
        self._playback_full = False
        self._motion_drag = None       # Q152-A.4 运动路径拖动
        self._motion_model = None
        self._mask_mode = None         # Q152-B/C 多边形绘制 / 吸管
        self._mask_drag = None
        self._mask_geom = None
        self._dragging_item = None
        self._last_mouse_pos = QPoint()
        self._inline_editor = None
        self._inline_target = None
        # Q127：所见即所得编辑态。None = 没在编辑；否则见 _start_wysiwyg_edit
        self._wysiwyg = None
        self._edit_backing = None     # 编辑态下上一次完整绘制的底图（光标闪烁只贴图不重算）
        self._caret_only = False      # 这次 paintEvent 只需要重画光标层
        # Q128：多选 / 框选
        self._multi = []              # 多选记录（overlay / sticker），主选中也在里面
        self._multi_collapse = None   # 按在组员上没拖：松开时收成单选
        self._band = None             # 框选进行中：{x0,y0,x1,y1,add}
        self._gesture_label = None    # 一次性操作的撤销标签（对齐 / 粘贴样式）
        # ── 吸附系统 ──
        self._snap_threshold = 15.0   # 视频坐标系下的吸附阈值（像素）
        self._snap_hold_off = False   # 按住 N：这一下不吸附（和轨道的 Q69 一样）
        self._drag_origin_rect = None  # [FIX-IMG] 拖拽起始矩形（吸附以此为基准累积）
        self._drag_accum = None        # [FIX-IMG] 拖拽累积位移 [dx, dy]
        self._snap_lines = []         # 当前激活的吸附参考线 [{'axis':'x'|'y','pos':float}]
        self._axis_lock = None        # Q116：Shift 锁定的方向 'x' / 'y'
        self._drag_hud = None         # Q125：拖手柄时的「字号 · 框宽%」提示
        self._split_drag = None       # {id, mode: pos|rot}
        self._split_hover = None
        self._show_split_guides = True
        try:
            from cutora.core.history import _load_app_settings
            _s = _load_app_settings() or {}
            if "preview.split_guides" in _s:
                self._show_split_guides = bool(_s.get("preview.split_guides"))
        except Exception:
            self._show_split_guides = True
        self._ops_init()

    def _init_fullscreen_chrome(self):
        from cutora.ui.i18n import tr
        self._fs_hint = QLabel(tr("F / Esc 退出全屏"), self)
        self._fs_hint.setObjectName("fsHint")
        self._fs_hint.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._fs_hint_fx = QGraphicsOpacityEffect(self._fs_hint)
        self._fs_hint.setGraphicsEffect(self._fs_hint_fx)
        from cutora.ui.theme.tokens import DURATION_MS
        self._fs_hint_anim = QPropertyAnimation(self._fs_hint_fx, b"opacity", self)
        self._fs_hint_anim.setDuration(DURATION_MS)
        self._fs_hint_timer = QTimer(self)
        self._fs_hint_timer.setSingleShot(True)
        self._fs_hint_timer.timeout.connect(self._fade_fs_hint)
        self._fs_hint.hide()
        self._sc_f = QShortcut(QKeySequence("F"), self)
        self._sc_esc = QShortcut(QKeySequence.StandardKey.Cancel, self)
        self._sc_f.setContext(Qt.ShortcutContext.ApplicationShortcut)
        self._sc_esc.setContext(Qt.ShortcutContext.ApplicationShortcut)
        self._sc_f.activated.connect(self.fullscreen_toggle_requested.emit)
        self._sc_esc.activated.connect(self.fullscreen_toggle_requested.emit)
        self._sc_f.setEnabled(False)
        self._sc_esc.setEnabled(False)

    def set_preview_fullscreen_mode(self, on: bool):
        self._preview_fullscreen = bool(on)
        self._sc_f.setEnabled(bool(on))
        self._sc_esc.setEnabled(bool(on))
        if on:
            self.show_fs_hint()
            self.setFocus(Qt.FocusReason.OtherFocusReason)
        else:
            self._fs_hint_timer.stop()
            self._fs_hint.hide()

    def show_fs_hint(self):
        from cutora.ui.i18n import tr
        from cutora.ui.theme.tokens import current
        self._fs_hint.setText(tr("F / Esc 退出全屏"))
        self._fs_hint.setStyleSheet(
            "color: %s; background: transparent; padding: 6px 10px;" % current().fg2)
        self._fs_hint.adjustSize()
        self._place_fs_hint()
        self._fs_hint_fx.setOpacity(1.0)
        self._fs_hint.show()
        self._fs_hint.raise_()
        self._fs_hint_timer.start(3000)

    def _place_fs_hint(self):
        margin = 16
        self._fs_hint.move(
            max(0, self.width() - self._fs_hint.width() - margin),
            max(0, self.height() - self._fs_hint.height() - margin),
        )

    def _fade_fs_hint(self):
        try:
            self._fs_hint_anim.finished.disconnect()
        except TypeError:
            pass
        self._fs_hint_anim.stop()
        self._fs_hint_anim.setStartValue(max(0.0, float(self._fs_hint_fx.opacity())))
        self._fs_hint_anim.setEndValue(0.0)
        self._fs_hint_anim.finished.connect(self._fs_hint.hide)
        self._fs_hint_anim.start()

    def event(self, ev):
        # Q145：焦点路由。画布有选中对象时，方向键 / Delete / Ctrl+C… 归画布，
        # 不让全局快捷键（方向键逐帧等）先抢走。
        if ev.type() == QEvent.Type.ShortcutOverride:
            try:
                mm = getattr(self, "_mask_mode", None)
                if mm and mm.get("kind") == "matte" and ev.modifiers() == Qt.KeyboardModifier.NoModifier \
                        and ev.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_H):
                    ev.accept()                  # Q166：AI 抠像点选时 Enter = 计算、H = 彩色区域开关
                    return True
                if self._ops_wants(ev):
                    ev.accept()
                    return True
            except Exception:
                from cutora.core.log import get_logger
                get_logger("cutora.ui.canvas").exception("ShortcutOverride 判断失败")
        return super().event(ev)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_N and not event.isAutoRepeat():
            self._snap_hold_off = True
            event.accept()
            return
        if getattr(self, "_mask_mode", None):
            from cutora.ui import canvas_mask as _ck
            if _ck.key(self, event):
                event.accept()
                return
        if self._preview_fullscreen and self._inline_editor is None:
            if event.key() in (Qt.Key.Key_F, Qt.Key.Key_Escape):
                self.fullscreen_toggle_requested.emit()
                event.accept()
                return
        if self._ops_key(event):
            event.accept()
            return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event):
        if event.key() == Qt.Key.Key_N:
            self._snap_hold_off = False
            event.accept()
            return
        if self._ops_key_release(event):
            event.accept()
            return
        super().keyReleaseEvent(event)

    # ── Q188：预览解码器池 ───────────────────────────────────────────────
    def _stop_decoder_later(self, decoder):
        """在后台线程里停（join + release 可能要等它当前这一帧 read 完），别卡界面线程。"""
        import threading as _th
        try:
            decoder.running = False
            _th.Thread(target=decoder.stop, name="cutora-decoder-stop", daemon=True).start()
        except Exception:
            pass

    def _stop_all_decoders(self):
        decs = list(self._async_decoders.values())
        self._async_decoders.clear()
        for d in decs:
            self._stop_decoder_later(d)
        caps = list(self._cv_caps.values())
        self._cv_caps.clear()
        for c in caps:
            try:
                c.release()
            except Exception:
                pass

    def _reap_idle_decoders(self):
        """每 5 s：超过闲置阈值没被取过帧的解码器停掉（再用到时重开 + seek，几十毫秒）。
        顺带扫一遍所有 CapturePool（播放引擎、叠压引擎、中央监视器）和备用 _cv_caps，避免长期占用外部视频文件。"""
        win = self.window()
        is_playing = bool(getattr(win, "_timeline_playing", False)) if win is not None else False
        idle_thresh = PREVIEW_DECODER_IDLE_S if is_playing else 3.5
        try:
            import time as _time
            now = _time.perf_counter()
            for sid, d in list(self._async_decoders.items()):
                if now - float(getattr(d, "last_used", now)) > idle_thresh:
                    self._async_decoders.pop(sid, None)
                    self._cv_frame_cache.pop(sid, None)
                    self._stop_decoder_later(d)
            if not is_playing and self._cv_caps:
                caps = list(self._cv_caps.values())
                self._cv_caps.clear()
                for c in caps:
                    try:
                        c.release()
                    except Exception:
                        pass
        except Exception:
            pass
        try:
            from cutora.preview.engine import reap_idle_pools
            reap_idle_pools(idle_thresh)
        except Exception:
            pass
        if not is_playing:
            try:
                from cutora.render.frame_renderer import cleanup_dbi_video_caps
                cleanup_dbi_video_caps()
            except Exception:
                pass

    def closeEvent(self, event):
        if self._preview_fullscreen:
            event.ignore()
            self.fullscreen_toggle_requested.emit()
            return
        super().closeEvent(event)

    def hideEvent(self, event):
        # Q127：画布被藏起来 / 关掉时先把正在编辑的文本提交掉。否则隐形编辑框
        # 会在画布析构过程中 focusOut → 回调一个正在销毁的对象 → 段错误。
        if self._inline_editor is not None:
            try:
                self._commit_inline_edit()
            except Exception:
                pass
        super().hideEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._edit_backing = None
        self._caret_only = False
        if self._wysiwyg is not None:
            try:
                d = self._wysiwyg["data"]
                wrect, _ = self._video_rect_to_widget(QRectF(
                    float(d.get("x") or 0), float(d.get("y") or 0),
                    float(d.get("w") or 1), float(d.get("h") or 1)))
                if wrect is not None:
                    self._wysiwyg["editor"].setGeometry(wrect)
            except Exception:
                pass
        if self._preview_fullscreen:
            self._place_fs_hint()
        self._place_scope_float()

    def get_video_sink(self): return self._video_sink

    def _on_frame_changed(self, frame):
        # QVideoSink 仅触发刷新，不参与解码/几何
        if frame.isValid() and not self._painting:
            self.update()
    
    def _log_preview_geom(self, tag, src_w, src_h, frame_w, frame_h, qimg_w, qimg_h, tl_w, tl_h):
        try:
            now = time.time()
            mismatch = (int(src_w), int(src_h)) != (int(frame_w), int(frame_h)) or (int(frame_w), int(frame_h)) != (int(qimg_w), int(qimg_h))
            if mismatch or (now - self._geom_last_log_t) > 1.0:
                print(f"[{tag}] src={int(src_w)}x{int(src_h)} frame={int(frame_w)}x{int(frame_h)} qimg={int(qimg_w)}x{int(qimg_h)} tl={int(tl_w)}x{int(tl_h)}")
                self._geom_last_log_t = now
        except Exception:
            pass

    def _check_program_frame_res(self, target_rect) -> None:
        """Q145：播放时视频层按显示像素合成。停下来 / 画布变大 / 放大查看以后，手上的帧
        比主窗口现在要的小，就（防抖后）请主窗口重合成一帧。拖动 / 擦洗时的半分辨率不算。"""
        if getattr(self, "_program_frame_placeholder", False) or getattr(self, "_playback_full", False):
            return
        prog = getattr(self, "_program_frame", None)
        if prog is None or prog.isNull():
            return
        tw = int(self._video_dims[0] or 0)
        if tw <= 0 or prog.width() >= tw:
            return
        # 要不要补清楚的由 _request_sharper_frame 问主窗口（播放 / 拖动时小图是对的）
        timer = getattr(self, "_sharpen_timer", None)
        if timer is None:
            from PySide6.QtCore import QTimer
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.setInterval(120)
            timer.timeout.connect(self._request_sharper_frame)
            self._sharpen_timer = timer
        if not timer.isActive():
            timer.start()

    def _request_sharper_frame(self) -> None:
        if getattr(self, "_dragging_item", None) is not None:
            return
        try:
            win = self.window()
        except Exception:
            return
        if getattr(win, "video_canvas", None) is not self:
            return
        scale_fn = getattr(win, "_preview_interaction_scale", None)
        present = getattr(win, "_present_preview_frame", None)
        if not callable(scale_fn) or not callable(present):
            return
        prog = getattr(self, "_program_frame", None)
        tw = int(self._video_dims[0] or 0)
        if prog is None or tw <= 0:
            return
        try:
            want = int(round(tw * float(scale_fn())))
        except Exception:
            return
        if want > prog.width() + 2:
            present()

    def set_program_frame(self, qimg):
        # Q175：播放时工作线程送来的是整幅成品（视频 + 覆层 + 调整图层），画布只管贴
        from cutora.preview.worker import FullFrame
        if isinstance(qimg, FullFrame):
            from cutora.core.fps_meter import note_preview_frame_if_counting
            note_preview_frame_if_counting()
            self._full_frame = qimg.image
            self._kick_scope(qimg.image)
            self.update()
            return
        self._full_frame = None
        self._playback_full = False
        # Q163 统一叠压：工作线程可能送来「每段视频带一张」的列表（底→顶）
        if isinstance(qimg, (list, tuple)):
            bands = [b for b in qimg if b is not None]
            self._program_bands = bands or None
            qimg = bands[0] if bands else None
        else:
            self._program_bands = None
        self._program_frame = qimg
        self._program_frame_placeholder = bool(
            qimg is not None and not qimg.isNull()
            and int(qimg.width()) * int(qimg.height()) < 64)
        if not self._program_frame_placeholder:
            self._kick_scope(qimg)
        self.update()

    def _paint_stacked_scene(self, painter, project, tl, tw, th, export_mode=False) -> bool:
        """Q163：视频压在文字 / 字幕 / 图片上面（或覆层类型交错）时，按时间线逐层画。

        画了返回 True；不需要（老画法就对）或视频带还没准备好返回 False（走老路）。"""
        if tl is None or not isinstance(project, dict):
            return False
        try:
            from cutora.render.visual_stack import build_stack, needs_stack, paint_overlay_entry, video_bands
            solo = _preview_solo_ids(self) if not export_mode else None
            stack = build_stack(project, solo)
            if not needs_stack(project, stack=stack):
                return False
            vbands = video_bands(stack)
            bands = getattr(self, "_program_bands", None)
            if not bands or len(bands) != len(vbands):
                if self._preview_worker_owns_decode() and not export_mode:
                    return False               # 工作线程马上送分段图来；这一帧先按老样子
                eng = getattr(self, "_stack_engine", None)
                if eng is None:
                    from cutora.preview.engine import PreviewEngine
                    eng = self._stack_engine = PreviewEngine()
                bands = eng.video_bands_at(
                    project, float(self._current_time),
                    bands=[[str(getattr(t, "id", "")) for t in b] for b in vbands])
            from cutora.fx.graph import preview_scope as _fx_preview_scope
            backdrop = bands[0] if bands else None
            if isinstance(backdrop, list):                 # Q178：拆层的带不当玻璃底图
                backdrop = None
            if backdrop is not None and (backdrop.width() != tw or backdrop.height() != th):
                backdrop = None
            self._preview_backdrop = backdrop
            vi = 0
            # Q174：有调整图层 → 先画到一张整幅的离屏图上（调整图层要读回「它下面已经画好的」），最后整张贴到画布
            scene = None
            outer = painter
            if any(k == "adjust" for k, _p in stack):
                from PySide6.QtGui import QImage as _QImage
                scene = _QImage(int(tw), int(th), _QImage.Format.Format_ARGB32_Premultiplied)
                scene.fill(Qt.GlobalColor.black)
                painter = QPainter(scene)
                painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
                painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
                painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
            try:
                with _fx_preview_scope(not export_mode):
                    for kind, payload in stack:
                        if kind == "video":
                            img = bands[vi] if vi < len(bands) else None
                            vi += 1
                            from cutora.render.visual_stack import draw_band
                            # Q178：带里有滤色 / 正片叠底这类混合模式的轨道 → 一层一层按模式混合
                            draw_band(painter, img, QRectF(0.0, 0.0, float(tw), float(th)))
                            self._collect_video_clip_hits(project, tl, tw, th, only=payload)
                            continue
                        paint_overlay_entry(painter, project, kind, payload, tw, th, self._current_time,
                                            hit_recorder=self._hit_records, backdrop=backdrop)
            finally:
                if scene is not None:
                    if painter.isActive():
                        painter.end()
                    painter = outer
                    painter.drawImage(0, 0, scene)
            _preview_perf.mark("video")
            _preview_perf.mark("draw_layers")
            return True
        except Exception:
            from cutora.core.log import get_logger
            get_logger("cutora.ui.canvas").exception("统一叠压画面失败，按老画法")
            return False

    def _preview_worker_owns_decode(self) -> bool:
        """True when MainWindow's preview worker will deliver the frame.

        Opening a capture on the UI thread to paint that first frame is the
        click stall. The plate stays up until set_program_frame swaps it.
        """
        try:
            win = self.window()
        except Exception:
            return False
        worker = getattr(win, "_preview_worker", None)
        try:
            return worker is not None and bool(worker.isRunning())
        except Exception:
            return False

    def set_scope_mode(self, mode):
        from cutora.render.scopes import ScopeSampler
        sampler = getattr(self, "_scope_sampler", None)
        if sampler is None:
            sampler = ScopeSampler()
            self._scope_sampler = sampler
        sampler.set_mode(mode)
        if str(mode or "off") == "off":
            self._scope_overlay = None
            flo = getattr(self, "_scope_float_win", None)
            if flo is not None:
                flo.hide()
        self.update()

    def _kick_scope(self, qimg):
        """Sample off this thread. Skip 2 of every 3 frames; downscale is in ScopeSampler."""
        from cutora.render.scopes import SCOPE_EVERY
        sampler = getattr(self, "_scope_sampler", None)
        if sampler is None or sampler.mode == "off":
            return
        if qimg is None or qimg.isNull():
            return
        n = int(getattr(self, "_scope_n", 0)) + 1
        self._scope_n = n
        if n % SCOPE_EVERY != 1:
            return
        if getattr(self, "_scope_busy", False):
            return
        copy = qimg.copy()
        self._scope_busy = True

        def _work(img=copy, sam=sampler):
            try:
                sam.compute(img)
                from PySide6.QtCore import QMetaObject, Qt
                QMetaObject.invokeMethod(
                    self, "_apply_scope_overlay",
                    Qt.ConnectionType.QueuedConnection)
            except Exception:
                from cutora.core.log import get_logger
                get_logger("cutora.ui.canvas").exception("示波器计算失败")
            finally:
                self._scope_busy = False

        import threading
        threading.Thread(target=_work, name="q32-scope", daemon=True).start()

    @Slot()
    def _apply_scope_overlay(self):
        sampler = getattr(self, "_scope_sampler", None)
        if sampler is None or sampler.mode == "off":
            self._scope_overlay = None
            flo = getattr(self, "_scope_float_win", None)
            if flo is not None:
                flo.hide()
            self.update()
            return
        tw = max(8, int(self._video_dims[0] or 8))
        th = max(8, int(self._video_dims[1] or 8))
        oh = max(48, th // 4)
        try:
            self._scope_overlay = sampler.overlay_qimage(tw, oh)
        except Exception:
            from cutora.core.log import get_logger
            get_logger("cutora.ui.canvas").exception("示波器叠加生成失败")
            self._scope_overlay = None
        self._show_scope_float()
        self.update()

    def _scope_float(self):
        flo = getattr(self, "_scope_float_win", None)
        if flo is None:
            flo = _ScopeFloat(self)
            self._scope_float_win = flo
        return flo

    def _place_scope_float(self):
        """Park the scope just outside the canvas so it does not cover the picture."""
        flo = getattr(self, "_scope_float_win", None)
        if flo is None or not flo.isVisible() or flo._user_moved:
            return
        gp = self.mapToGlobal(QPoint(self.width() + 8, 8))
        screen = self.screen()
        if screen is not None:
            avail = screen.availableGeometry()
            if gp.x() + flo.width() > avail.right():
                gp = self.mapToGlobal(QPoint(-flo.width() - 8, 8))
            if gp.y() + flo.height() > avail.bottom():
                gp.setY(max(avail.top(), avail.bottom() - flo.height()))
            if gp.y() < avail.top():
                gp.setY(avail.top())
        flo.move(gp)

    def _show_scope_float(self):
        overlay = getattr(self, "_scope_overlay", None)
        sampler = getattr(self, "_scope_sampler", None)
        if sampler is None or sampler.mode == "off" or overlay is None or overlay.isNull():
            flo = getattr(self, "_scope_float_win", None)
            if flo is not None:
                flo.hide()
            return
        flo = self._scope_float()
        flo.set_image(overlay)
        flo.show()
        self._place_scope_float()

    def set_data(self, task_data, time):
        if task_data is not self._task_data and self._inline_editor is not None:
            self._cancel_inline_edit()
        # 如果切换了项目，释放旧的 OpenCV 缓存（Q188：异步解码器也一起停，以前换工程后老的还在跑）
        if task_data is not self._task_data:
            self._stop_all_decoders()
            self._cv_frame_cache.clear()
            self._cv_sar_cache.clear()
            self._cv_export_states = {}
            # ── 清除旧选中/拖拽状态，避免跨视频残留干扰 ──
            self._selected_item = None
            self._selected_rect = None
            self._multi = []
            self._band = None
            self._dragging_item = None
            self._drag_mode = None
            self._resize_handle = None
            self._snap_lines = []
            self._hit_records = []
            self._alpha_hit_cache = {}
            self._opaque_hit_frames = {}
            try:
                from cutora.render.textfx import clear_roi_cache
                clear_roi_cache()
            except Exception:
                pass
        self._task_data = task_data
        if task_data:
            tl = _tl_of(task_data)
            if tl is not None:
                tw = int(tl.width or DEFAULT_TIMELINE_WIDTH)
                th = int(tl.height or DEFAULT_TIMELINE_HEIGHT)
                if tw > 0 and th > 0:
                    self._video_dims = (tw, th)
        if task_data and (not self._current_image or self._current_image.isNull()):
            self._frame_dims = task_data.get("dims", self._frame_dims)
        self._current_time = time
        self.update()

    def _video_rect_to_widget(self, rect):
        target_rect = self._preview_target_rect()
        if target_rect.width() <= 0 or target_rect.height() <= 0:
            return None, 1.0
        scale_x = target_rect.width() / self._video_dims[0] if self._video_dims[0] else 1.0
        scale_y = target_rect.height() / self._video_dims[1] if self._video_dims[1] else 1.0
        wx = target_rect.x() + rect.x() * scale_x
        wy = target_rect.y() + rect.y() * scale_y
        ww = rect.width() * scale_x
        wh = rect.height() * scale_y
        return QRect(int(wx), int(wy), max(1, int(ww)), max(1, int(wh))), min(scale_x, scale_y)

    @staticmethod
    def _discard_editor(editor):
        """退役一个编辑框：先摘掉回调再 deleteLater。

        Q127 抓到的坑：deleteLater 的老编辑框还活到事件循环下一拍，新编辑框
        一 setFocus，老框的 focusOutEvent 触发 → 回调 _commit_inline_edit →
        把刚开始的新编辑当场提交掉。第二次双击永远进不了编辑态。
        """
        if editor is None:
            return
        try:
            editor._commit_cb = None
            editor._cancel_cb = None
        except Exception:
            pass
        try:
            editor.blockSignals(True)
            editor.hide()
        except Exception:
            pass
        editor.deleteLater()

    def _cancel_inline_edit(self):
        if self._inline_editor is None:
            return
        self._end_wysiwyg()
        if isinstance(self._inline_target, dict):
            data = self._inline_target.get("data")
            orig = self._inline_target.get("orig_text")
            if isinstance(data, dict) and orig is not None:
                if self._inline_target.get("type") in ("sub", "sub_span"):
                    data["text"] = orig
                    if self._inline_target.get("type") != "sub_span":
                        data["edited_text"] = orig
                else:
                    data["content"] = orig
                    data["content_format"] = "plain"
                    data.pop("rich_content", None)
                data.pop("_inline_editing", None)
                self.update()
        self._discard_editor(self._inline_editor)
        self._inline_editor = None
        self._inline_target = None

    def _commit_inline_edit(self):
        if self._inline_editor is None or not isinstance(self._inline_target, dict):
            return
        self._end_wysiwyg()
        text = self._inline_editor.toPlainText()
        target = self._inline_target
        data = target.get("data")
        if isinstance(data, dict):
            if target.get("type") in ("sub", "sub_span"):
                data["text"] = text
                if target.get("type") != "sub_span":
                    data["edited_text"] = text
                    data.pop("words", None)
            else:
                data["content"] = text
                data["content_format"] = "plain"
                data.pop("rich_content", None)
            data.pop("_inline_editing", None)
        self._discard_editor(self._inline_editor)
        self._inline_editor = None
        self._inline_target = None
        if target.get("type") == "overlay":
            self.item_selected.emit("overlay", (target.get("list"), target.get("idx")))
        elif target.get("type") == "sticker":
            self.item_selected.emit("sticker", (target.get("ov_idx"), target.get("el_idx")))
        elif target.get("type") == "sub":
            self.item_selected.emit("sub", target.get("idx"))
        self.item_dragged.emit()
        self.update()

    def _inline_on_text_changed(self):
        if self._inline_editor is None or not isinstance(self._inline_target, dict):
            return
        data = self._inline_target.get("data")
        if self._wysiwyg is not None and isinstance(data, dict):
            data["content"] = self._wysiwyg_display_text()
            data["content_format"] = "plain"
            data.pop("rich_content", None)
            self._wysiwyg["caret_on"] = True
            self.update()
            return
        if isinstance(data, dict):
            if self._inline_target.get("type") in ("sub", "sub_span"):
                data["text"] = self._inline_editor.toPlainText()
                if self._inline_target.get("type") != "sub_span":
                    data["edited_text"] = self._inline_editor.toPlainText()
                    data.pop("words", None)
            else:
                data["content"] = self._inline_editor.toPlainText()
                data["content_format"] = "plain"
                data.pop("rich_content", None)
        self.update()

    def _start_inline_edit(self, record, at=None):
        """*at*：双击落点（视频坐标），文本层用它决定光标落在哪个字上。"""
        if not record:
            return
        rect = record.get("rect")
        if rect is None:
            return
        if self._inline_editor is not None:
            self._commit_inline_edit()
        data = None
        target = {"type": record.get("type")}
        if record.get("type") == "overlay":
            list_name = record.get("list")
            idx = record.get("idx")
            target.update({"list": list_name, "idx": idx})
            if isinstance(self._task_data, dict) and list_name in self._task_data:
                try:
                    data = self._task_data[list_name][idx]
                except Exception:
                    data = None
        elif record.get("type") == "sticker":
            ov_idx = record.get("ov_idx")
            el_idx = record.get("el_idx")
            target.update({"ov_idx": ov_idx, "el_idx": el_idx})
            try:
                ov = self._task_data.get("image_overlays", [])[ov_idx]
                data = (ov.get("elements") or [])[el_idx]
            except Exception:
                data = None
        elif record.get("type") in ("sub", "sub_span"):
            # Ensure "sub_span" uses its seg_idx
            idx = record.get("seg_idx") if record.get("type") == "sub_span" else record.get("idx")
            b_idx = record.get("span_idx", -1) if record.get("type") == "sub_span" else -1
            target.update({"idx": idx, "adv_block_idx": b_idx, "type": record.get("type")})
            if isinstance(self._task_data, dict):
                try:
                    seg = self._task_data.get("segments", [])[idx]
                    if b_idx >= 0:
                        data = seg.get("adv_blocks", [])[b_idx].setdefault("overrides", {})
                        target["orig_text"] = data.get("text", record.get("extracted_text", ""))
                    else:
                        data = seg
                        target["orig_text"] = data.get("text", "")
                except Exception:
                    data = None
        if not isinstance(data, dict):
            return
        target["data"] = data
        if "orig_text" not in target:
            target["orig_text"] = data.get("text", "") if record.get("type") in ("sub", "sub_span") else data.get("content", "")
        self._inline_target = target

        wrect, scale = self._video_rect_to_widget(rect)
        if wrect is None:
            self._inline_target = None
            return
        if record.get("type") == "overlay" and str(data.get("type") or "text") == "text":
            # Q127：文本层走所见即所得，字幕 / 贴纸暂时还是老编辑框
            self._start_wysiwyg_edit(record, data, target, wrect, at=at)
            return
        data["_inline_editing"] = True
        editor = InlineTextEdit(self, self._commit_inline_edit, self._cancel_inline_edit)
        editor.setAcceptRichText(False)
        editor.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        editor.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        editor.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        editor.setGeometry(wrect)
        if record.get("type") in ("sub", "sub_span"):
            base_style = self._task_data.get("style", {})
            role_id = data.get("role", "default")
            from cutora.subtitle.stylepack import resolve_paint_style
            style = resolve_paint_style(self._task_data, role_id)
            font_family = style.get("font_family", DEFAULT_FONT_FAMILY)
            fontsize = style.get("fontsize", 60)
            bold = style.get("font_bold", style.get("bold", True))
            italic = style.get("font_italic", style.get("italic", False))
            
            fontsize = float(fontsize)
            font_color = str(style.get("font_color", "#FFFFFF"))
            
            adv_box_enabled = bool(style.get('advanced_textbox_enabled', False))
            adv_align = str(style.get('adv_text_align', style.get('advanced_textbox_align', 'center')) or 'center').lower()
            if adv_align not in ('left', 'center', 'right'):
                adv_align = 'center'
                
            line_gap = int(style.get('adv_line_spacing', style.get('line_spacing', 0)) or 0) if adv_box_enabled else int(style.get('line_spacing', 0) or 0)
            target["_line_gap"] = line_gap
            layout = _rich_default_layout({"align": adv_align})
            
            text_to_edit = data.get("edited_text")
            if text_to_edit is None:
                text_to_edit = data.get("text", "")
        else:
            font_family = data.get("font_family", DEFAULT_FONT_FAMILY)
            fontsize = float(data.get("fontsize", 40))
            bold = bool(data.get("bold", False))
            italic = bool(data.get("italic", False))
            font_color = str(data.get("font_color", "#FFFFFF"))
            layout = _rich_default_layout(data.get("text_layout"))
            target["_line_gap"] = 0
            text_to_edit = data.get("content", "")

        from cutora.ui.theme.apply import apply_inline_editor
        apply_inline_editor(editor, font_color)

        editor.setPlainText(text_to_edit)

        font = QFont(render_font_family(font_family))
        font.setBold(bool(bold))
        font.setItalic(bool(italic))
        font_size = max(6, int(float(fontsize) * max(scale, 0.1)))
        font.setPixelSize(font_size)
        editor.setFont(font)
        editor.setViewportMargins(
            int(layout["left_pad"] * scale),
            int(layout["top_pad"] * scale),
            int(layout["right_pad"] * scale),
            int(layout["bottom_pad"] * scale)
        )
        editor.setAlignment(_qt_align_flag_for_text(layout.get("align", "center") if record.get("type") == "sub" else layout.get("align", "left")))
        editor.setWordWrapMode(QTextOption.WrapMode.WordWrap)
        opt = QTextOption()
        opt.setWrapMode(QTextOption.WrapMode.WordWrap)
        opt.setAlignment(_qt_align_flag_for_text(layout.get("align", "center") if record.get("type") == "sub" else layout.get("align", "left")))
        editor.document().setDefaultTextOption(opt)
        editor.document().setDefaultTextOption(opt)
        editor.document().setDocumentMargin(0)
        editor.setContentsMargins(0, 0, 0, 0)
        
        # Apply strict line height if line spacing is provided
        if target.get("_line_gap", 0) != 0:
            fm = QFontMetrics(font)
            line_h = fm.height()
            line_step = line_h + target["_line_gap"]
            cursor = editor.textCursor()
            cursor.select(QTextCursor.SelectionType.Document)
            block_fmt = cursor.blockFormat()
            block_fmt.setLineHeight(line_step, 4) # 4 = QTextBlockFormat.LineHeightTypes.FixedHeight
            cursor.setBlockFormat(block_fmt)
            cursor.clearSelection()
            editor.setTextCursor(cursor)
            
        pal = editor.palette()
        pal.setColor(QPalette.ColorRole.Text, QColor("#FFFFFF"))
        pal.setColor(QPalette.ColorRole.HighlightedText, QColor("#FFFFFF"))
        editor.setPalette(pal)
        apply_inline_editor(editor, font_color)
        editor.textChanged.connect(self._inline_on_text_changed)
        editor.show()
        editor.setFocus()
        cursor = editor.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        editor.setTextCursor(cursor)
        self._inline_editor = editor

    # ══════════════════════════════════════════════════════════════════
    # Q128：多选（Shift+点 / 框选）、成组移动、对齐 / 分布、复制粘贴样式
    # ══════════════════════════════════════════════════════════════════
    MULTI_TYPES = ("overlay", "sticker")
    BAND_MIN_PX = 4.0

    def _multi_contains(self, rec) -> bool:
        return any(self._same_item(rec, r) for r in self._multi)

    def _multi_set(self, records):
        self._multi = [r for r in records if isinstance(r, dict) and r.get("type") in self.MULTI_TYPES]
        self.multi_selection_changed.emit(len(self._multi))
        self.update()

    def _multi_clear(self):
        if self._multi:
            self._multi = []
            self.multi_selection_changed.emit(0)

    def _multi_toggle(self, rec):
        """Shift+点：加入 / 移出多选。主选中永远是最后点的那个。"""
        if not isinstance(rec, dict) or rec.get("type") not in self.MULTI_TYPES:
            return
        if self._multi_contains(rec):
            rest = [r for r in self._multi if not self._same_item(r, rec)]
            self._multi_set(rest)
            if rest:
                self._selected_item = rest[-1]
                self._selected_rect = rest[-1].get("rect")
            else:
                self._selected_item = None
                self._selected_rect = None
            return
        base = list(self._multi)
        if self._selected_item is not None and not self._multi_contains(self._selected_item) \
                and self._selected_item.get("type") in self.MULTI_TYPES:
            base.append(self._selected_item)
        base.append(rec)
        self._multi_set(base)
        self._selected_item = rec
        self._selected_rect = rec.get("rect")

    def _live_record(self, rec):
        """_hit_records 每次重画都是新 dict；按身份找回当前那份（带最新 rect）。"""
        for r in reversed(self._hit_records or []):
            if self._same_item(r, rec):
                return r
        return rec

    def selected_records(self):
        """当前选中的全部记录（多选 → 多条；否则主选中一条；没有 → 空）。"""
        if self._multi:
            return [self._live_record(r) for r in self._multi]
        if self._selected_item is not None and self._selected_item.get("type") in self.MULTI_TYPES:
            return [self._live_record(self._selected_item)]
        return []

    def _record_data(self, rec):
        """记录 → 它背后的数据 dict（文本层 / 贴纸元素）。"""
        try:
            if rec.get("type") == "overlay":
                return self._task_data[rec.get("list")][rec.get("idx")]
            if rec.get("type") == "sticker":
                ov = self._task_data.get("image_overlays", [])[rec.get("ov_idx")]
                return (ov.get("elements") or [])[rec.get("el_idx")]
        except Exception:
            return None
        return None

    def selected_text_overlays(self):
        out = []
        for rec in self.selected_records():
            if rec.get("type") != "overlay" or rec.get("list") != "text_overlays":
                continue
            d = self._record_data(rec)
            if isinstance(d, dict):
                out.append(d)
        return out

    def _record_box(self, rec):
        r = rec.get("rect")
        if r is None:
            return None
        return (float(r.x()), float(r.y()), float(r.width()), float(r.height()))

    def _nudge_record(self, rec, dx, dy):
        """把一条记录平移 (dx, dy)，关键帧感知（和主拖拽同一条写回路径）。"""
        if not dx and not dy:
            return
        if rec.get("type") == "overlay":
            ov = self._record_data(rec)
            if not isinstance(ov, dict):
                return
            if dx:
                self._nudge_host_param(ov, "x", dx, ov.get("x", 0.0),
                                       lambda v, _ov=ov: _ov.__setitem__("x", v))
            if dy:
                self._nudge_host_param(ov, "y", dy, ov.get("y", 0.0),
                                       lambda v, _ov=ov: _ov.__setitem__("y", v))
        elif rec.get("type") == "sticker":
            try:
                ov = self._task_data.get("image_overlays", [])[rec.get("ov_idx")]
                el = (ov.get("elements") or [])[rec.get("el_idx")]
            except Exception:
                return
            ldx, ldy = float(dx), float(dy)
            rot = float(ov.get("rotation", 0) or 0)
            if rot:
                import math
                rad = math.radians(-rot)
                c, s = math.cos(rad), math.sin(rad)
                ldx, ldy = ldx * c - ldy * s, ldx * s + ldy * c
            if ldx:
                self._nudge_host_param(el, "x", ldx, el.get("x", 0.0),
                                       lambda v, _el=el: _el.__setitem__("x", v))
            if ldy:
                self._nudge_host_param(el, "y", ldy, el.get("y", 0.0),
                                       lambda v, _el=el: _el.__setitem__("y", v))
        r = rec.get("rect")
        if r is not None:
            rec["rect"] = r.translated(dx, dy)

    def _multi_move_others(self, dx, dy):
        """主选中拖了 (dx, dy)，组里其它成员跟着走。"""
        if not self._multi or self._dragging_item is None:
            return
        if not self._multi_contains(self._dragging_item):
            return
        for rec in self._multi:
            if self._same_item(rec, self._dragging_item):
                continue
            self._nudge_record(rec, dx, dy)

    # ── 一次性操作的撤销包装 ─────────────────────────────────────────
    def _discrete(self, label, fn):
        """对齐 / 粘贴样式这类一下子完成的改动：一步撤销，标签是 label。"""
        self._gesture_label = str(label or "")
        self.gesture_began.emit()
        try:
            changed = fn()
        finally:
            if changed:
                self.item_dragged.emit()
            self.gesture_ended.emit()
            self._gesture_label = None
        self.update()
        return changed

    def align_selection(self, mode):
        from cutora.ui.align_ops import ALIGN_LABELS, align
        recs = self.selected_records()
        boxes = [self._record_box(r) for r in recs]
        if not recs or any(b is None for b in boxes):
            return False
        tw, th = self._video_dims

        def _go():
            deltas = align(boxes, mode, bounds=(0.0, 0.0, float(tw), float(th)))
            moved = False
            for rec, (dx, dy) in zip(recs, deltas):
                if abs(dx) > 1e-6 or abs(dy) > 1e-6:
                    self._nudge_record(rec, dx, dy)
                    moved = True
            self._resync_selection_rects()
            return moved
        return self._discrete(ALIGN_LABELS.get(mode, "对齐"), _go)

    def distribute_selection(self, axis):
        from cutora.ui.align_ops import DISTRIBUTE_LABELS, distribute
        recs = self.selected_records()
        boxes = [self._record_box(r) for r in recs]
        if len(recs) < 3 or any(b is None for b in boxes):
            return False

        def _go():
            deltas = distribute(boxes, axis)
            moved = False
            for rec, (dx, dy) in zip(recs, deltas):
                if abs(dx) > 1e-6 or abs(dy) > 1e-6:
                    self._nudge_record(rec, dx, dy)
                    moved = True
            self._resync_selection_rects()
            return moved
        return self._discrete(DISTRIBUTE_LABELS.get(axis, "分布"), _go)

    def _resync_selection_rects(self):
        """位移写回数据后，把主选中框和多选记录的 rect 跟上（下次重画会再校准）。"""
        if self._selected_item is not None:
            live = self._live_record(self._selected_item)
            if live is not None and live.get("rect") is not None:
                self._selected_rect = QRectF(live.get("rect"))

    def copy_text_style(self):
        """复制主选中（或唯一选中）文本层的样式到样式剪贴板。"""
        from cutora.ui.text_style_clipboard import copy_from
        recs = self.selected_records()
        primary = None
        if self._selected_item is not None:
            for r in recs:
                if self._same_item(r, self._selected_item):
                    primary = r
                    break
        if primary is None and recs:
            primary = recs[-1]
        if primary is None or primary.get("type") != "overlay" or primary.get("list") != "text_overlays":
            return None
        d = self._record_data(primary)
        if not isinstance(d, dict):
            return None
        return copy_from(d)

    def paste_text_style(self):
        """把样式剪贴板刷到所有选中的文本层。返回改了几条。"""
        from cutora.ui.text_style_clipboard import has_clipboard, paste_to
        if not has_clipboard():
            return 0
        targets = self.selected_text_overlays()
        if not targets:
            return 0
        n = {"v": 0}

        def _go():
            n["v"] = paste_to(targets)
            return n["v"] > 0
        self._discrete("粘贴样式", _go)
        if n["v"] and self._selected_item is not None and self._selected_item.get("type") == "overlay":
            # 让属性面板 / 工具条重新读一遍主选中
            self.item_selected.emit("overlay", (self._selected_item.get("list"), self._selected_item.get("idx")))
        return n["v"]

    # ── 框选 ──────────────────────────────────────────────────────────
    def _band_begin(self, video_x, video_y, additive):
        self._band = {"x0": float(video_x), "y0": float(video_y),
                      "x1": float(video_x), "y1": float(video_y), "add": bool(additive)}

    def _band_rect(self):
        b = self._band
        if not b:
            return None
        return QRectF(QPointF(min(b["x0"], b["x1"]), min(b["y0"], b["y1"])),
                      QPointF(max(b["x0"], b["x1"]), max(b["y0"], b["y1"])))

    def _band_update(self, video_x, video_y):
        if not self._band:
            return False
        self._band["x1"] = float(video_x)
        self._band["y1"] = float(video_y)
        self.update()
        return True

    def _band_finish(self):
        b = self._band
        self._band = None
        if not b:
            return False
        rect = QRectF(QPointF(min(b["x0"], b["x1"]), min(b["y0"], b["y1"])),
                      QPointF(max(b["x0"], b["x1"]), max(b["y0"], b["y1"])))
        target_rect = self._preview_target_rect()
        sx = target_rect.width() / float(self._video_dims[0] or 1) if target_rect.width() > 0 else 1.0
        if max(rect.width(), rect.height()) * sx < self.BAND_MIN_PX:
            self.update()
            return False
        picked = []
        for rec in self._hit_records or []:
            if rec.get("type") not in self.MULTI_TYPES:
                continue
            r = rec.get("rect")
            if r is not None and rect.intersects(QRectF(r)):
                picked.append(rec)
        if b.get("add"):
            base = [r for r in self._multi]
            for rec in picked:
                if not any(self._same_item(rec, r) for r in base):
                    base.append(rec)
            picked = base
        if picked:
            self._multi_set(picked)
            self._selected_item = picked[-1]
            self._selected_rect = picked[-1].get("rect")
            top = picked[-1]
            if top.get("type") == "overlay":
                self.item_selected.emit("overlay", (top.get("list"), top.get("idx")))
            elif top.get("type") == "sticker":
                self.item_selected.emit("sticker", (top.get("ov_idx"), top.get("el_idx")))
        else:
            self._multi_clear()
        self.update()
        return True

    def _draw_multi_chrome(self, painter):
        """多选：每个成员一圈细线，整组一个虚线包围盒；框选中画选择框。"""
        from cutora.ui.theme.tokens import current as _tok
        tok = _tok()
        accent = QColor(tok.accent)
        target_rect = self._preview_target_rect()
        sx = target_rect.width() / float(self._video_dims[0] or 1) if target_rect.width() > 0 else 1.0
        px = 1.0 / max(sx, 1e-6)
        if len(self._multi) >= 2:
            painter.save()
            try:
                painter.setBrush(Qt.BrushStyle.NoBrush)
                boxes = []
                for rec in self._multi:
                    live = self._live_record(rec)
                    r = live.get("rect")
                    if r is None:
                        continue
                    boxes.append(QRectF(r))
                    if self._selected_item is not None and self._same_item(live, self._selected_item):
                        continue   # 主选中已经有自己的框和手柄
                    painter.setPen(QPen(accent, 1.5 * px))
                    painter.drawRect(QRectF(r))
                if boxes:
                    u = boxes[0]
                    for r in boxes[1:]:
                        u = u.united(r)
                    pen = QPen(accent, 1.0 * px, Qt.PenStyle.DashLine)
                    painter.setPen(pen)
                    painter.drawRect(u.adjusted(-4 * px, -4 * px, 4 * px, 4 * px))
            finally:
                painter.restore()
        band = self._band_rect()
        if band is not None and band.width() + band.height() > 0:
            painter.save()
            try:
                fill = QColor(accent)
                fill.setAlpha(40)
                painter.setBrush(fill)
                painter.setPen(QPen(accent, 1.0 * px))
                painter.drawRect(band)
            finally:
                painter.restore()

    def selected_image_overlays(self):
        out = []
        seen = set()
        img_list = (self._task_data or {}).get("image_overlays") or []
        for rec in self.selected_records():
            ov = None
            if rec.get("type") == "sticker":
                idx = rec.get("ov_idx")
                if isinstance(idx, int) and 0 <= idx < len(img_list):
                    ov = img_list[idx]
            elif rec.get("type") == "overlay" and rec.get("list") == "image_overlays":
                idx = rec.get("idx")
                if isinstance(idx, int) and 0 <= idx < len(img_list):
                    ov = img_list[idx]
            if isinstance(ov, dict) and ov.get("type") != "adjust" and id(ov) not in seen:
                seen.add(id(ov))
                out.append(ov)
        if not out:
            win = self.window()
            tl_w = getattr(win, "timeline", None) if win is not None else None
            sel_ov = getattr(tl_w, "selected_overlay", None) if tl_w is not None else None
            if isinstance(sel_ov, tuple) and len(sel_ov) == 2 and sel_ov[0] == "image_overlays":
                idx = sel_ov[1]
                if isinstance(idx, int) and 0 <= idx < len(img_list):
                    ov = img_list[idx]
                    if isinstance(ov, dict) and ov.get("type") != "adjust":
                        out.append(ov)
        return out

    def _activate_sam2_video_matte_from_canvas(self):
        """在画布右键一键展开右侧「AI 抠像 (SAM 2.1)」面板并进入点选对象模式。"""
        win = self.window()
        if win is None:
            return
        prop = getattr(win, "prop_panel", None)
        vid_fx = getattr(prop, "vid_fx", None) if prop is not None else None
        ai_panel = getattr(vid_fx, "ai", None) if vid_fx is not None else None
        if ai_panel is not None:
            try:
                if hasattr(prop, "stack") and hasattr(prop, "video_scroll"):
                    prop.stack.setCurrentWidget(prop.video_scroll)
                ai_panel.sec.setVisible(True)
                ai_panel.sec.setExpanded(True)
                ai_panel.btn_prompt.setChecked(True)
                if hasattr(win, "statusBar") and callable(win.statusBar):
                    win.statusBar().showMessage("🎯 已开启 SAM 2.1 视频 AI 抠像点选模式：在画面上左键点选人物/物体（Alt+左键排除），按 Enter 计算并传播整段", 7000)
            except Exception:
                pass

    def _context_menu(self, event):
        """右键：AI 抠图去背景 (RMBG-2.0) / 视频点选抠像 (SAM 2.1)、复制 / 粘贴样式、对齐、分布、导出当前帧。"""
        from cutora.ui.align_ops import ALIGN_LABELS, ALIGN_MODES, DISTRIBUTE_AXES, DISTRIBUTE_LABELS
        from cutora.ui.text_style_clipboard import has_clipboard
        xy = self._video_xy(event.position())
        if xy is not None:
            from cutora.render.hitmask import pick_record
            under = pick_record(self._hit_records, xy[0], xy[1], alt_bbox=False)
            if under is not None and under.get("type") in self.MULTI_TYPES \
                    and not self._multi_contains(under) \
                    and not self._same_item(under, self._selected_item):
                # 右键点在没选中的东西上：先选中它（所有编辑器的习惯）
                self._multi_clear()
                self._selected_item = under
                self._selected_rect = under.get("rect")
                if under.get("type") == "overlay":
                    self.item_selected.emit("overlay", (under.get("list"), under.get("idx")))
                else:
                    self.item_selected.emit("sticker", (under.get("ov_idx"), under.get("el_idx")))
                self.update()
        recs = self.selected_records()
        texts = self.selected_text_overlays()
        img_ovs = self.selected_image_overlays()
        menu = QMenu(self)

        # ── 置顶：AI 抠图 (RMBG-2.0) 与 视频 AI 抠像 (SAM 2.1) ──
        from cutora.ui.ai_studio_room import has_rmbg_original, open_quick_ai_creator, restore_rmbg_original_ui, run_rmbg_cutout_ui
        win_top = self.window()
        if img_ovs:
            act_rmbg = menu.addAction("✂️ AI 一键抠图去背景 (RMBG-2.0 发丝级透明抠图)")
            act_rmbg.triggered.connect(
                lambda: run_rmbg_cutout_ui(win=win_top, overlays=img_ovs, task=self._task_data, parent=self)
            )
            if any(has_rmbg_original(ov) for ov in img_ovs):
                act_restore = menu.addAction("↩️ 还原抠图前原图")
                act_restore.triggered.connect(
                    lambda: restore_rmbg_original_ui(win=win_top, overlays=img_ovs)
                )
        else:
            act_rmbg_frame = menu.addAction("✂️ AI 一键抠图去背景 (RMBG-2.0 抠当前画面/选中素材)")
            act_rmbg_frame.triggered.connect(
                lambda: run_rmbg_cutout_ui(win=win_top, task=self._task_data, parent=self)
            )
        act_sam = menu.addAction("🎯 AI 视频点选追踪抠像 (SAM 2.1 动态人物抠像)")
        act_sam.triggered.connect(self._activate_sam2_video_matte_from_canvas)
        act_ai_capsule = menu.addAction("✨ 打开 AI 创作与精修胶囊 (21项模型全集)…")
        act_ai_capsule.triggered.connect(
            lambda: open_quick_ai_creator(win=win_top, initial_cap_id="rmbg2_sam2_matte", parent=self)
        )
        menu.addSeparator()

        if texts:
            act_copy = menu.addAction("复制样式\tCtrl+Alt+C")
            act_copy.triggered.connect(lambda: self.copy_text_style())
            act_paste = menu.addAction("粘贴样式\tCtrl+Alt+V")
            act_paste.setEnabled(has_clipboard())
            act_paste.triggered.connect(lambda: self.paste_text_style())
            menu.addSeparator()
        if recs:
            sub = menu.addMenu("对齐" + ("（相对彼此）" if len(recs) >= 2 else "（相对画布）"))
            for mode in ALIGN_MODES:
                a = sub.addAction(ALIGN_LABELS[mode])
                a.triggered.connect(lambda _c=False, m=mode: self.align_selection(m))
                if mode == "right":
                    sub.addSeparator()
            if len(recs) >= 3:
                dist = menu.addMenu("等距分布")
                for axis in DISTRIBUTE_AXES:
                    a = dist.addAction(DISTRIBUTE_LABELS[axis])
                    a.triggered.connect(lambda _c=False, ax=axis: self.distribute_selection(ax))
            menu.addSeparator()
        chk = menu.addAction("透明处显示棋盘格（抠像 / 遮罩时）")
        chk.setCheckable(True)
        chk.setChecked(bool(getattr(self, "alpha_checker", True)))
        chk.setToolTip("只影响预览。导出时透明的地方露出下面的轨道，下面什么都没有就是黑色")
        chk.toggled.connect(lambda on: (setattr(self, "alpha_checker", bool(on)), self.update()))
        export_action = menu.addAction("导出当前帧 (无损 PNG)")
        export_action.triggered.connect(self.export_current_frame)
        menu.exec(event.globalPosition().toPoint())

    # ══════════════════════════════════════════════════════════════════
    # Q127：文本层所见即所得编辑（字照常渲染，只叠光标 / 选区）
    # ══════════════════════════════════════════════════════════════════
    CARET_BLINK_MS = 530

    def _start_wysiwyg_edit(self, record, data, target, wrect, at=None):
        from cutora.subtitle.text_layer import ensure_overlay_fx
        ensure_overlay_fx(data)          # 从这一刻起只走 textfx 一条排版路径
        editor = CanvasTextEditor(self, self._commit_inline_edit, self._cancel_inline_edit)
        editor.setGeometry(wrect)        # 只给输入法定位用，控件本身不画
        editor.setPlainText(str(data.get("content") or ""))
        blink = QTimer(self)
        blink.setInterval(self.CARET_BLINK_MS)
        blink.timeout.connect(self._wysiwyg_blink)
        self._wysiwyg = {
            "editor": editor, "data": data, "target": target,
            "map": None, "map_key": None,
            "blink": blink, "caret_on": True, "drag": False,
        }
        self._inline_editor = editor
        editor.textChanged.connect(self._inline_on_text_changed)
        editor.cursorPositionChanged.connect(self._wysiwyg_cursor_moved)
        editor.selectionChanged.connect(self._wysiwyg_cursor_moved)
        editor.show()
        editor.setFocus(Qt.FocusReason.MouseFocusReason)
        cursor = editor.textCursor()
        idx = None
        if at is not None:
            idx = self._wysiwyg_index_at(float(at[0]), float(at[1]), strict=False)
        if idx is None:
            cursor.movePosition(QTextCursor.MoveOperation.End)
        else:
            # 双击落在哪个词上就选中哪个词（Canva / Figma 的习惯）
            cursor.setPosition(int(idx))
            cursor.select(QTextCursor.SelectionType.WordUnderCursor)
        editor.setTextCursor(cursor)
        blink.start()
        self._edit_backing = None
        self.update()

    def _end_wysiwyg(self):
        ws = self._wysiwyg
        if ws is None:
            return
        try:
            ws["blink"].stop()
            ws["blink"].deleteLater()
        except Exception:
            pass
        data = ws.get("data")
        editor = ws.get("editor")
        # 预编辑（拼音还没上屏）不算内容
        if isinstance(data, dict) and editor is not None:
            try:
                data["content"] = editor.toPlainText()
            except Exception:
                pass
        self._wysiwyg = None
        self._edit_backing = None
        self._caret_only = False

    def _wysiwyg_display_text(self):
        """编辑器文本 + 光标处的输入法预编辑串（拼音打到一半也要看得见）。"""
        ws = self._wysiwyg
        if ws is None:
            return ""
        editor = ws["editor"]
        text = editor.toPlainText()
        pre = str(getattr(editor, "preedit", "") or "")
        if not pre:
            return text
        pos = int(editor.textCursor().position())
        return text[:pos] + pre + text[pos:]

    def _wysiwyg_preedit_changed(self):
        self._inline_on_text_changed()

    def _wysiwyg_cursor_moved(self):
        ws = self._wysiwyg
        if ws is None:
            return
        ws["caret_on"] = True
        try:
            ws["blink"].start()   # 重新计时，移动时光标常亮
        except Exception:
            pass
        self.update()

    def _wysiwyg_blink(self):
        ws = self._wysiwyg
        if ws is None:
            return
        ws["caret_on"] = not ws["caret_on"]
        # 只重画光标那一小块：paintEvent 看到 _caret_only 就贴底图，不重新合成
        self._caret_only = True
        r = self._wysiwyg_caret_widget_rect()
        if r is None:
            QWidget.update(self)
        else:
            QWidget.update(self, r.adjusted(-6, -6, 6, 6))

    def update(self, *args):  # noqa: D401 - 任何常规刷新都让底图失效
        self._caret_only = False
        super().update(*args)

    def _wysiwyg_map(self):
        """当前排版的光标几何，按 (内容 + 样式) 缓存，样式没变就不重排。"""
        ws = self._wysiwyg
        if ws is None:
            return None
        data = ws["data"]
        try:
            import json
            key = json.dumps(data, sort_keys=True, default=str, ensure_ascii=False)
        except Exception:
            key = repr(data)
        if ws["map"] is None or ws["map_key"] != key:
            from cutora.render.text_caret import build_caret_map
            tw, th = self._video_dims
            try:
                ws["map"] = build_caret_map(data, int(tw), int(th))
            except Exception:
                from cutora.core.log import get_logger
                get_logger("cutora.ui.canvas").exception("caret map failed")
                ws["map"] = None
            ws["map_key"] = key
        return ws["map"]

    def _wysiwyg_positions(self):
        """(光标显示位置, 选区起, 选区止, 预编辑长度) —— 都是显示文本里的下标。"""
        ws = self._wysiwyg
        editor = ws["editor"]
        cur = editor.textCursor()
        pos = int(cur.position())
        a, b = int(cur.selectionStart()), int(cur.selectionEnd())
        pre = len(str(getattr(editor, "preedit", "") or ""))
        if pre:
            return pos + pre, pos, pos, pre
        return pos, a, b, 0

    def _wysiwyg_scene_transform(self):
        """视频坐标 → 控件坐标（含预览缩放和框旋转）。"""
        from PySide6.QtGui import QTransform
        cm = self._wysiwyg_map()
        target_rect = self._preview_target_rect()
        if cm is None or target_rect.width() <= 0 or target_rect.height() <= 0:
            return None, None
        sx = target_rect.width() / float(self._video_dims[0] or 1)
        sy = target_rect.height() / float(self._video_dims[1] or 1)
        xf = QTransform()
        xf.translate(target_rect.x(), target_rect.y())
        xf.scale(sx, sy)
        xf = cm.transform() * xf
        return cm, xf

    def _wysiwyg_caret_widget_rect(self):
        """光标在控件坐标里的矩形（给输入法候选框和局部刷新用）。"""
        if self._wysiwyg is None:
            return None
        cm, xf = self._wysiwyg_scene_transform()
        if cm is None:
            return None
        pos, _a, _b, _pre = self._wysiwyg_positions()
        r = cm.caret(pos)
        if r.height() <= 0 and r.width() <= 0:
            r = QRectF(r.x(), r.y(), 1.0, float(cm.em))
        poly = xf.map(QRectF(r.x() - 1.0, r.y(), max(2.0, r.width() + 2.0), max(2.0, r.height())))
        return poly.boundingRect().toAlignedRect()

    def _wysiwyg_index_at(self, video_x, video_y, strict=True):
        """视频坐标 → 文本下标；strict 时点在框外返回 None。"""
        cm = self._wysiwyg_map()
        if cm is None:
            return None
        x, y = cm.unrotate(float(video_x), float(video_y))
        if strict:
            bx, by, bw, bh = cm.box
            pad = float(cm.em) * 0.35
            if not (bx - pad <= x <= bx + bw + pad and by - pad <= y <= by + bh + pad):
                return None
        return cm.hit(x, y)

    def _wysiwyg_index_at_widget(self, widget_pos, strict=True):
        xy = self._video_xy(widget_pos)
        if xy is None:
            return None
        return self._wysiwyg_index_at(xy[0], xy[1], strict=strict)

    def _wysiwyg_press(self, event):
        """编辑态下的按下：框内定光标（Shift 扩选），框外提交并放行给正常流程。"""
        ws = self._wysiwyg
        if ws is None:
            return False
        idx = self._wysiwyg_index_at_widget(event.position(), strict=True)
        if idx is None:
            self._commit_inline_edit()
            return False
        editor = ws["editor"]
        cur = editor.textCursor()
        keep = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        cur.setPosition(int(idx), QTextCursor.MoveMode.KeepAnchor if keep else QTextCursor.MoveMode.MoveAnchor)
        editor.setTextCursor(cur)
        editor.setFocus(Qt.FocusReason.MouseFocusReason)
        ws["drag"] = True
        ws["caret_on"] = True
        self.update()
        return True

    def _wysiwyg_move(self, event):
        ws = self._wysiwyg
        if ws is None or not ws.get("drag"):
            return False
        idx = self._wysiwyg_index_at_widget(event.position(), strict=False)
        if idx is None:
            return True
        editor = ws["editor"]
        cur = editor.textCursor()
        if int(cur.position()) != int(idx):
            cur.setPosition(int(idx), QTextCursor.MoveMode.KeepAnchor)
            editor.setTextCursor(cur)
            self.update()
        return True

    def _wysiwyg_release(self, event):
        ws = self._wysiwyg
        if ws is None or not ws.get("drag"):
            return False
        ws["drag"] = False
        return True

    def _wysiwyg_double_click(self, event):
        ws = self._wysiwyg
        if ws is None:
            return False
        idx = self._wysiwyg_index_at_widget(event.position(), strict=True)
        if idx is None:
            return False
        editor = ws["editor"]
        cur = editor.textCursor()
        cur.setPosition(int(idx))
        cur.select(QTextCursor.SelectionType.WordUnderCursor)
        editor.setTextCursor(cur)
        ws["drag"] = False
        self.update()
        return True

    def _draw_wysiwyg_layer(self, painter):
        """选区、输入法下划线、光标 —— 全按 text_caret 的真实排版画。"""
        ws = self._wysiwyg
        if ws is None:
            return
        cm, xf = self._wysiwyg_scene_transform()
        if cm is None:
            return
        pos, a, b, pre = self._wysiwyg_positions()
        target_rect = self._preview_target_rect()
        sx = target_rect.width() / float(self._video_dims[0] or 1)
        px = 1.0 / max(sx, 1e-6)           # 一个设备像素在视频坐标里的长度
        from cutora.ui.theme.tokens import current as _tok
        tok = _tok()
        painter.save()
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setClipRect(target_rect)
            painter.setTransform(xf, True)
            if b > a:
                sel = QColor(tok.accent)
                sel.setAlpha(96)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(sel)
                for r in cm.line_rects(a, b):
                    painter.drawRect(r)
            if pre:
                # 拼音 / 假名还没上屏：一条下划线告诉用户这一段还在组字
                painter.setBrush(Qt.BrushStyle.NoBrush)
                pen = QPen(QColor(255, 255, 255, 230), 2.0 * px)
                painter.setPen(pen)
                for r in cm.line_rects(pos - pre, pos):
                    y = r.bottom() - 1.0 * px
                    painter.drawLine(QPointF(r.left(), y), QPointF(r.right(), y))
            if ws.get("caret_on", True):
                r = cm.caret(pos)
                if cm.vertical:
                    x0, y0, x1, y1 = r.left(), r.top(), r.right(), r.top()
                    if r.width() <= 0:
                        x1 = x0 + float(cm.em)
                else:
                    x0, y0, x1, y1 = r.left(), r.top(), r.left(), r.bottom()
                    if r.height() <= 0:
                        y1 = y0 + float(cm.em) * 1.2
                painter.setPen(QPen(QColor(0, 0, 0, 150), 4.0 * px))   # 深色描边：白底上也看得见
                painter.drawLine(QPointF(x0, y0), QPointF(x1, y1))
                painter.setPen(QPen(QColor(255, 255, 255, 240), 2.0 * px))
                painter.drawLine(QPointF(x0, y0), QPointF(x1, y1))
        finally:
            painter.restore()

    def paintEvent(self, event):
        _bump("canvas.paint")
        if self._painting:
            return
        self._painting = True
        self._preview_backdrop = None
        painter = None
        _preview_perf.begin_frame(count=not getattr(self, "_playback_full", False))
        try:
            if self._wysiwyg is not None:
                # 编辑态：整幅画面先合成到底图，光标闪烁只贴底图 + 画光标层，
                # 不会每 530ms 把视频帧 / 分屏 / 所有覆层重新合成一遍。
                dpr = float(self.devicePixelRatioF() or 1.0)
                want = self.size() * dpr
                reuse = (self._caret_only and self._edit_backing is not None
                         and self._edit_backing.size() == want)
                if not reuse:
                    pm = QPixmap(want)
                    pm.setDevicePixelRatio(dpr)
                    pp = QPainter(pm)
                    try:
                        self._paint_scene(pp)
                    finally:
                        pp.end()
                    self._edit_backing = pm
                self._caret_only = False
                painter = QPainter(self)
                painter.drawPixmap(0, 0, self._edit_backing)
                self._draw_wysiwyg_layer(painter)
            else:
                self._edit_backing = None
                self._caret_only = False
                painter = QPainter(self)
                self._paint_scene(painter)
        except Exception:
            from cutora.core.log import get_logger
            get_logger("cutora.ui.canvas").exception("paintEvent failed")
            if os.environ.get("CUTORA_PERF", "") == "1" or os.environ.get(
                    "CUTORA_DEBUG_STYLEPACK", "") == "1":
                raise
        finally:
            self._painting = False
            if painter is not None:
                painter.end()
            _preview_perf.end_frame()

    def _paint_scene(self, painter):
        """整幅预览（底色 + 画面 + 覆层 + 手柄）。paintEvent 和编辑态底图共用。"""
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            from cutora.ui.theme.tokens import current
            tok = current()
            painter.fillRect(self.rect(), QColor(tok.bg0))

            # 计算画布显示区域（保持 Timeline 宽高比）
            target_rect = QRectF(self.rect())
            if self._video_dims[0] > 0 and self._video_dims[1] > 0:
                img_w = self._video_dims[0]
                img_h = self._video_dims[1]
                if img_w > 0 and img_h > 0:
                    scale = min(self.width() / img_w, self.height() / img_h)
                    draw_w = int(img_w * scale)
                    draw_h = int(img_h * scale)
                    draw_x = (self.width() - draw_w) // 2
                    draw_y = (self.height() - draw_h) // 2
                    target_rect = self._apply_view(QRectF(draw_x, draw_y, draw_w, draw_h))
                    if self._alpha_checker_wanted():
                        # Q165：画面里有抠像 / 遮罩 → 透明处画棋盘格（以前是黑底，抠了也看着像没抠）
                        paint_alpha_checker(painter, target_rect)
                    else:
                        painter.fillRect(target_rect, Qt.GlobalColor.black)
                    painter.setPen(QPen(QColor(tok.line), 1))
                    painter.setBrush(Qt.BrushStyle.NoBrush)
                    painter.drawRect(target_rect.adjusted(0, 0, -1, -1))
                    if self._task_data:
                        painter.save()
                        try:
                            # ★ 关键：先裁剪再绘制，超出画布的内容一律不显示
                            painter.setClipRect(target_rect)
                            painter.translate(target_rect.topLeft())
                            s_x = target_rect.width() / self._video_dims[0]
                            s_y = target_rect.height() / self._video_dims[1]
                            painter.scale(s_x, s_y)
                            self._draw_preview_local(painter)
                        finally:
                            painter.restore()
                        self._check_program_frame_res(target_rect)
            self._paint_view_badge(painter)
        except Exception:
            from cutora.core.log import get_logger
            get_logger("cutora.ui.canvas").exception("paintEvent failed")
            if os.environ.get("CUTORA_PERF", "") == "1" or os.environ.get(
                    "CUTORA_DEBUG_STYLEPACK", "") == "1":
                raise

    # Q165：透明处显示棋盘格（右键菜单可关；只在画面里有抠像 / AI 抠像 / 遮罩时生效）
    alpha_checker = True

    def _alpha_checker_wanted(self) -> bool:
        if not getattr(self, "alpha_checker", True) or getattr(self, "_export_snapshot_mode", False):
            return False
        return task_has_alpha_fx(self._task_data)

    @staticmethod
    def _task_needs_glass_backdrop(project) -> bool:
        try:
            from cutora.render.textfx.spec import task_has_glass_bg
            return bool(task_has_glass_bg(project))
        except Exception:
            return False

    def _draw_preview_local(self, painter, export_mode=False):
        project = self._task_data or {}
        tl = _tl_of(project) if isinstance(project, dict) and project else None
        tw = int(tl.width if tl else self._video_dims[0])
        th = int(tl.height if tl else self._video_dims[1])
        if not export_mode and self._paint_full_frame(painter, tw, th):
            return
        self._hit_records = []
        self._preview_backdrop = None
        _stacked_done = self._paint_stacked_scene(painter, project, tl, tw, th, export_mode)
        if not _stacked_done:
            prog = getattr(self, "_program_frame", None)
            # === 多轨道视频叠压渲染（底→顶）+ 混合模式 ===
            main_video_path = project.get("video", "") if isinstance(project, dict) else ""
            if prog is not None and not prog.isNull():
                painter.save()
                try:
                    # Fast blit. Smooth-scaling a 1080×1920 program frame on the
                    # GUI thread is the Q71 200 ms open stall.
                    painter.setRenderHint(
                        QPainter.RenderHint.SmoothPixmapTransform, False)
                    painter.drawImage(
                        QRectF(0.0, 0.0, float(tw), float(th)), prog)
                finally:
                    painter.restore()
                if prog.width() == tw and prog.height() == th:
                    self._preview_backdrop = prog
                else:
                    self._preview_backdrop = None
                    if (prog.width() * prog.height() >= 64
                            and self._task_needs_glass_backdrop(project)):
                        bg_key = (int(prog.cacheKey()), int(tw), int(th))
                        bg_cached = getattr(self, "_scaled_backdrop_cache", None)
                        if bg_cached is not None and bg_cached[0] == bg_key:
                            self._preview_backdrop = bg_cached[1]
                        else:
                            scaled_bg = prog.scaled(
                                tw, th, Qt.AspectRatioMode.IgnoreAspectRatio,
                                Qt.TransformationMode.FastTransformation)
                            self._scaled_backdrop_cache = (bg_key, scaled_bg)
                            self._preview_backdrop = scaled_bg
                if tl is not None and prog.width() * prog.height() >= 64:
                    # Q116：分屏时也要收命中记录，否则点上下屏的画面选不中片段
                    self._collect_video_clip_hits(project, tl, tw, th)
            elif self._preview_worker_owns_decode():
                # First frame is still on the preview worker. Keep the plate
                # (or the previous program frame) instead of opening a decoder here.
                pass
            elif tl is not None:
                from cutora.render.split_layout import split_enabled as _split_on
                from cutora.render import backdrop as _bg
                _bg.begin_frame(project)          # Q155 整幅背景：片段自己的背景填充让位
                if _split_on(project):
                    def _paint_one(dest, _tr, dw, dh):
                        if not getattr(_tr, "visible", True):
                            return
                        solo_ids = _preview_solo_ids(self)
                        from cutora.model.track_guard import track_is_muted
                        if track_is_muted(_tr, task=project, solo_ids=solo_ids):
                            return
                        active_trans = None
                        for trans in getattr(_tr, "transitions", []) or []:
                            if str(getattr(trans, "domain", "visual") or "visual") == "audio":
                                continue
                            if trans.start_t <= self._current_time <= (trans.start_t + trans.duration):
                                active_trans = trans
                                break
                        from cutora.render.track_transform import (
                            paint_track_layer, track_needs_layer,
                        )
                        layering = track_needs_layer(_tr)

                        def _paint_preview_track(d2, __tr=_tr, _trans=active_trans, _layering=layering, _w=dw, _h=dh):
                            if _trans is not None:
                                self._draw_standalone_transition_composite(
                                    d2, __tr, _trans, tl, _w, _h)
                                return
                            active_clips = self._find_active_clips_in_track(__tr, self._current_time)
                            if not active_clips:
                                return
                            for active_clip in active_clips:
                                src_id = getattr(active_clip, "source_id", None)
                                src = tl.sources.get(src_id) if src_id else None
                                src_path = getattr(src, "path", None) or (src.get("path") if isinstance(src, dict) else None) if src else None
                                blend_name = getattr(active_clip, "blend_mode", None)
                                if not _layering:
                                    blend_name = blend_name or getattr(__tr, "blend_mode", "normal")
                                else:
                                    blend_name = blend_name or "normal"
                                blend_mode = BLEND_MODES.get(blend_name, _BLEND_NORMAL)
                                d2.save()
                                d2.setCompositionMode(blend_mode)
                                try:
                                    if src_path:
                                        frame_img = self._get_cv_frame_for_preview(src_id or src_path, src_path, active_clip, self._current_time)
                                        if frame_img and not frame_img.isNull():
                                            self._preview_backdrop = frame_img
                                            self._draw_video_overlay_preview_qimage(d2, active_clip, frame_img, _w, _h)
                                            # Q116：分屏格子里也要登记命中，点哪个屏选哪个片段
                                            _cell = None
                                            _cid = getattr(__tr, "split_cell", None)
                                            if _cid:
                                                from cutora.render.split_screen import (
                                                    cell_content_box,
                                                )
                                                _cell = cell_content_box(
                                                    (project or {}).get("split"),
                                                    _cid, tw, th)
                                            from cutora.render.split_screen import (
                                                painting_backdrop,
                                            )
                                            if not painting_backdrop():
                                                self._remember_video_hit(
                                                    active_clip, frame_img, tw, th,
                                                    __tr, dest=_cell)
                                finally:
                                    d2.restore()

                        paint_track_layer(dest, _tr, dw, dh, _paint_preview_track)

                    from cutora.model.track_guard import video_tracks_for_render
                    vis = video_tracks_for_render(project, solo_ids=_preview_solo_ids(self))
                    from cutora.render.split_screen import paint_split_video_tracks
                    paint_split_video_tracks(painter, project, vis, tw, th, _paint_one)
                else:
                  from cutora.model.track_guard import video_tracks_for_render
                  _bg.paint_bg_image(painter, project, tw, th)
                  for tr in video_tracks_for_render(
                          project, solo_ids=_preview_solo_ids(self)):
                    # --- NLE Gen-2: 检测独立转场白条 ---
                    active_trans = None
                    for trans in getattr(tr, "transitions", []) or []:
                        if str(getattr(trans, "domain", "visual") or "visual") == "audio":
                            continue
                        if trans.start_t <= self._current_time <= (trans.start_t + trans.duration):
                            active_trans = trans
                            break

                    from cutora.render.track_transform import (
                        paint_track_layer, track_needs_layer,
                    )
                    layering = track_needs_layer(tr)

                    def _paint_preview_track(dest, _tr=tr, _trans=active_trans, _layering=layering):
                        if _trans is not None:
                            self._draw_standalone_transition_composite(
                                dest, _tr, _trans, tl, tw, th)
                            return
                        active_clips = self._find_active_clips_in_track(_tr, self._current_time)
                        if not active_clips:
                            return
                        for active_clip in active_clips:
                            src_id = getattr(active_clip, "source_id", None)
                            src = tl.sources.get(src_id) if src_id else None
                            src_path = getattr(src, "path", None) or (src.get("path") if isinstance(src, dict) else None) if src else None
                            is_intro = bool(getattr(active_clip, "is_intro_video_leadin", False))
                            if is_intro:
                                _it = float(getattr(active_clip, "in_t", 0))
                                _ot = float(getattr(active_clip, "out_t", 0))
                                _st = float(getattr(active_clip, "start_t", 0))
                                print(f"[INTRO_DBG] t={self._current_time:.2f} src_id={src_id} src_path={src_path} in_t={_it} out_t={_ot} start_t={_st}")
                            blend_name = getattr(active_clip, "blend_mode", None)
                            if not _layering:
                                blend_name = blend_name or getattr(_tr, "blend_mode", "normal")
                            else:
                                blend_name = blend_name or "normal"
                            blend_mode = BLEND_MODES.get(blend_name, _BLEND_NORMAL)
                            dest.save()
                            dest.setCompositionMode(blend_mode)
                            try:
                                if src_path:
                                    frame_img = self._get_cv_frame_for_preview(src_id or src_path, src_path, active_clip, self._current_time)
                                    if is_intro:
                                        print(f"[INTRO_DBG] frame_img={'OK' if frame_img and not frame_img.isNull() else 'NONE/NULL'}")
                                    if frame_img and not frame_img.isNull():
                                        self._preview_backdrop = frame_img
                                        self._draw_video_overlay_preview_qimage(dest, active_clip, frame_img, tw, th)
                                        self._remember_video_hit(active_clip, frame_img, tw, th, _tr)
                                elif is_intro:
                                    print(f"[INTRO_DBG] NO src_path! sources keys={list(tl.sources.keys())}")
                            finally:
                                dest.restore()

                    paint_track_layer(painter, tr, tw, th, _paint_preview_track)
            else:
                clip = self._get_active_video_clip_preview(project, self._current_time)
                main_path = project.get("video", "") if isinstance(project, dict) else ""
                if clip and main_path:
                    frame_img = self._get_cv_frame_for_preview("main_preview", main_path, clip, self._current_time)
                    if frame_img and not frame_img.isNull():
                        self._preview_backdrop = frame_img
                        self._draw_video_overlay_preview_qimage(painter, clip, frame_img, tw, th)
                        self._remember_video_hit(clip, frame_img, tw, th, None)

            from cutora.render.backdrop import end_frame as _bg_end
            _bg_end()
            _preview_perf.mark("video")
            order = compute_render_order(
                project, solo_ids=_preview_solo_ids(self) if not export_mode else None)
            from cutora.fx.graph import preview_scope as _fx_preview_scope
            with _fx_preview_scope(not export_mode):      # Q152-F：图片层的节点图查看器只在预览里
                FrameRenderer.draw_layers(painter, project, tw, th, self._current_time,
                                          self._hit_records, render_order=order,
                                          backdrop=self._preview_backdrop)
            _preview_perf.mark("draw_layers")
        # Scopes live in _ScopeFloat, beside the picture. Do not paint them here.
        if not export_mode:
            self._draw_safe_zones(painter, tw, th)
            if self._split_guides_visible():
                self._draw_split_handles(painter, tw, th)
            self._draw_selection_outline(painter)
            # Q152-A.4：位置打了关键帧的对象，画出运动路径（可拖关键帧方块）
            from cutora.ui import canvas_motion as _cm
            _cm.draw(self, painter)
            from cutora.ui import canvas_mask as _ck
            _ck.draw(self, painter)
            self._draw_multi_chrome(painter)
            self._draw_snap_guides(painter)
            self._draw_drag_hud(painter)
        _preview_perf.mark("chrome")

    def _paint_full_frame(self, painter, tw, th) -> bool:
        """Q175 播放中：贴工作线程合成好的整幅画面（按显示像素），再画选中框 / 安全区这些界面元素。
        命中记录沿用停下来时那份（播放中不重算，省下逐帧排字）。没有整幅帧 → False，走原来的画法。"""
        ff = getattr(self, "_full_frame", None)
        if ff is None or ff.isNull() or not getattr(self, "_playback_full", False):
            return False
        if getattr(self, "_dragging_item", None) is not None or getattr(self, "_wysiwyg", None) is not None:
            return False
        painter.save()
        try:
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            painter.drawImage(QRectF(0.0, 0.0, float(tw), float(th)), ff)
        finally:
            painter.restore()
        _preview_perf.mark("video")
        _preview_perf.mark("draw_layers")
        self._draw_safe_zones(painter, tw, th)
        if self._split_guides_visible():
            self._draw_split_handles(painter, tw, th)
        self._draw_selection_outline(painter)
        _preview_perf.mark("chrome")
        return True

    def _split_handles(self, tw, th):
        from cutora.render.split_layout import split_enabled
        from cutora.render.split_screen import handle_centers
        task = self._task_data
        if not split_enabled(task):
            return []
        return handle_centers(task.get("split"), tw, th)

    def _hit_split_handle(self, video_x, video_y, tw, th, radius=18.0):
        import math
        hit_r = max(float(radius), min(float(tw), float(th)) * 0.024)
        best = None
        best_d = hit_r
        vx, vy = float(video_x), float(video_y)
        for h in self._split_handles(tw, th):
            if str(h.get("axis")) == "circle":
                # Check the 5 specialized circle/ellipse handles first:
                # rot_pt (outer frame rotation), scale_pt (uniform size), rx_pt (width/aspect), ry_pt (height/aspect), center (move)
                candidates = [
                    ("rot", h.get("rot_pt")),
                    ("scale", h.get("scale_pt")),
                    ("rx", h.get("rx_pt")),
                    ("ry", h.get("ry_pt")),
                    ("pos", (h["x"], h["y"])),
                ]
                for sub_mode, pt in candidates:
                    if not pt:
                        continue
                    d = math.hypot(vx - float(pt[0]), vy - float(pt[1]))
                    if d <= best_d:
                        best = dict(h)
                        best["sub_mode"] = sub_mode
                        best_d = d
                continue
            dx = vx - h["x"]
            dy = vy - h["y"]
            d = math.hypot(dx, dy)
            if d <= best_d:
                best = dict(h)
                best["sub_mode"] = "pos"
                best_d = d
        return best

    def _split_track_selected(self):
        """A timeline track that owns a split cell is selected."""
        win = self.window()
        tl = getattr(win, "timeline", None) if win is not None else None
        tr = getattr(tl, "selected_track", None) if tl is not None else None
        return bool(getattr(tr, "split_cell", None))

    def _split_guides_visible(self):
        """Reference line. Off follows the app setting; drag or a selected cell shows it."""
        if getattr(self, "_split_drag", None) is not None:
            return True
        if self._split_track_selected():
            return True
        return bool(getattr(self, "_show_split_guides", True))

    def _draw_split_handles(self, painter, tw, th):
        """Preview chrome: Gold-themed divider path + interactive handles (center, scale, ellipse aspect, rotation)."""
        from PySide6.QtGui import QColor, QPainterPath, QPen
        handles = self._split_handles(tw, th)
        if not handles:
            return
        painter.save()
        try:
            gold_main = QColor("#E5A93C")
            gold_bright = QColor("#F5D082")
            gold_dark = QColor("#14110B")
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            base_scale = max(1.0, min(float(tw), float(th)) / 1080.0)
            r = max(7.0, 9.0 * base_scale)
            for h in handles:
                pts = h.get("pts") or []
                hover = self._split_hover == h.get("id")
                if len(pts) >= 2:
                    path = QPainterPath()
                    path.moveTo(float(pts[0][0]), float(pts[0][1]))
                    for x, y in pts[1:]:
                        path.lineTo(float(x), float(y))
                    pen = QPen(gold_bright if hover else gold_main, max(2.0, 2.2 * base_scale))
                    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
                    painter.setPen(pen)
                    painter.setBrush(Qt.BrushStyle.NoBrush)
                    painter.drawPath(path)
                if str(h.get("axis")) == "circle":
                    # 1. Center move handle (●)
                    painter.setBrush(gold_bright if hover else gold_dark)
                    painter.setPen(QPen(gold_main, max(2.0, 2.2 * base_scale)))
                    painter.drawEllipse(QPointF(h["x"], h["y"]), r, r)
                    # 2. SE (45°) Uniform Scale handle (■) - scales circle + video together
                    sp_pt = h.get("scale_pt")
                    if sp_pt:
                        sr = r * 0.88
                        painter.setBrush(gold_main)
                        painter.setPen(QPen(gold_dark, max(1.5, 1.8 * base_scale)))
                        painter.drawRect(QRectF(sp_pt[0] - sr, sp_pt[1] - sr, sr * 2.0, sr * 2.0))
                    # 3. East (↔) & South (↕) Ellipse Aspect handles (●)
                    for apt in (h.get("rx_pt"), h.get("ry_pt")):
                        if apt:
                            ar = r * 0.78
                            painter.setBrush(gold_bright)
                            painter.setPen(QPen(gold_dark, max(1.5, 1.8 * base_scale)))
                            painter.drawEllipse(QPointF(apt[0], apt[1]), ar, ar)
                    # 4. North (⟳) Outer-Frame Rotation handle
                    rot_pt = h.get("rot_pt")
                    if rot_pt:
                        rr = r * 0.85
                        painter.setBrush(QColor("#FFF3D1"))
                        painter.setPen(QPen(gold_main, max(2.0, 2.2 * base_scale)))
                        painter.drawEllipse(QPointF(rot_pt[0], rot_pt[1]), rr, rr)
                else:
                    painter.setBrush(gold_bright if hover else gold_dark)
                    painter.setPen(QPen(gold_main, max(2.0, 2.2 * base_scale)))
                    painter.drawEllipse(QPointF(h["x"], h["y"]), r, r)
        finally:
            painter.restore()

    def _commit_split_handle(self, handle, patch):
        from cutora.core.commands import get_bus
        from cutora.core.log import get_logger
        try:
            get_bus().execute(
                "split.set_divider",
                {"id": handle["id"], **patch},
                source="ui",
            )
        except Exception:
            get_logger("cutora.ui.canvas").exception(
                "split.set_divider 失败 id=%s patch=%s", handle.get("id"), patch)
            win = self.window()
            if win is not None and hasattr(win, "statusBar") and callable(win.statusBar):
                win.statusBar().showMessage("设置分界线失败", 4000)
            raise

    def fx_canvas_action(self, action, arg, panel=None):
        """检查器「遮罩 / 抠像」节要画布做的事（画多边形 / 吸管 / 选中遮罩）。"""
        from cutora.ui import canvas_mask as _ck
        _ck.fx_canvas_action(self, action, arg, panel)

    def _video_xy(self, widget_pos):
        target_rect = self._preview_target_rect()
        if target_rect.width() <= 0 or target_rect.height() <= 0:
            return None
        scale_x = self._video_dims[0] / target_rect.width()
        scale_y = self._video_dims[1] / target_rect.height()
        return (
            (widget_pos.x() - target_rect.x()) * scale_x,
            (widget_pos.y() - target_rect.y()) * scale_y,
        )

    def _draw_safe_zones(self, painter, tw, th):
        """Preview-only chrome. Export path never calls this."""
        from cutora.core.history import _load_app_settings
        from cutora.render.safe_zones import active_bands, disclaimer
        bands = active_bands(_load_app_settings(), tw, th)
        if not bands:
            return
        painter.save()
        try:
            from PySide6.QtGui import QFont
            font = QFont(painter.font())
            font.setPixelSize(max(12, int(min(tw, th) * 0.018)))
            painter.setFont(font)
            note = disclaimer()
            for b in bands:
                c = QColor(b.color)
                fill = QColor(c)
                fill.setAlpha(70)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(fill)
                painter.drawRect(QRectF(b.x, b.y, b.w, b.h))
                edge = QColor(c)
                edge.setAlpha(200)
                pen = QPen(edge, 2, Qt.PenStyle.DashLine)
                painter.setPen(pen)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRect(QRectF(b.x, b.y, b.w, b.h))
                label = b.label or b.preset_name
                if note:
                    label = "%s · %s" % (label, note) if label else note
                if label:
                    painter.setPen(QColor("#F4EBD8"))
                    painter.drawText(
                        QRectF(b.x + 6, b.y + 4, max(40.0, b.w - 12), 28),
                        int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop),
                        label)
        finally:
            painter.restore()

    def _draw_standalone_transition_composite(self, painter, track, trans, tl, tw, th):
        """NLE 独立转场 — 与导出同一条 RenderWorker._paint_export_transition。"""
        from cutora.render.readers import _open_video_capture
        from cutora.render.render_worker import RenderWorker
        t = self._current_time
        rw = getattr(self, "_export_rw", None)
        if rw is None:
            rw = RenderWorker.__new__(RenderWorker)
            rw._geom_last_log_t = 0.0
            self._export_rw = rw
        rw.project = self._task_data
        sources = getattr(tl, "sources", {}) or {}
        try:
            fps = float(getattr(tl, "fps", 30.0) or 30.0)
        except (TypeError, ValueError):
            fps = 30.0
        if fps <= 0:
            fps = 30.0

        def cap_for(clip):
            if clip is None:
                return None
            sid = str(getattr(clip, "source_id", "") or "")
            cap = self._cv_caps.get(sid)
            if cap is not None and cap.isOpened():
                self._cv_caps.move_to_end(sid)
                return cap
            src = sources.get(getattr(clip, "source_id", None))
            path = None
            if src is not None:
                path = getattr(src, "path", None) or (
                    src.get("path") if isinstance(src, dict) else None)
            if not path:
                return None
            from cutora.render.readers import preview_hw_decode
            cap = _open_video_capture(path, hw=preview_hw_decode(), threads=PREVIEW_DECODE_THREADS)
            if cap is not None and cap.isOpened():
                self._cv_caps[sid] = cap
                while len(self._cv_caps) > PREVIEW_CAP_POOL:
                    _k, old = self._cv_caps.popitem(last=False)
                    try:
                        old.release()
                    except Exception:
                        pass
                return cap
            return None

        def state_for(clip):
            sid = str(getattr(clip, "source_id", "") or "")
            st = self._cv_export_states.setdefault(
                sid, {"last_time": -1.0, "last_frame": None})
            st["src_fps"] = fps
            return st

        rw._paint_export_transition(
            painter, track, trans, float(t), int(tw), int(th), cap_for, state_for)

    def _find_active_clip_in_track(self, track, t):
        """在单个 Track 中查找 t 时刻的活跃 Clip（考虑变速后的 effective_duration）。"""
        for clip in getattr(track, "clips", []) or []:
            try:
                start_t = float(getattr(clip, "start_t", 0.0))
                eff_dur = float(getattr(clip, "effective_duration", 0.0))
                if eff_dur <= 0:
                    in_t = float(getattr(clip, "in_t", 0.0))
                    out_t = float(getattr(clip, "out_t", in_t))
                    eff_dur = max(0.0, out_t - in_t)
            except Exception:
                continue
            if start_t <= t < start_t + eff_dur:
                return clip
        return None

    def _find_active_clips_in_track(self, track, t):
        """在单个 Track 中查找 t 时刻所有活跃的 Clip，按轨道列表顺序（底→顶）。"""
        from cutora.model.timeline_model import active_clips_at
        return active_clips_at(track, t)

    def _get_cv_frame_for_preview(self, source_id, path, clip, current_time,
                                 extend_handles=False, source=None):
        """使用异步环形队列预读解码视频帧，彻底解放 UI 主线程。支持变速和循环。"""
        try:
            # 检查 L1 缓存（同一时间直接复用避免 QImage 隐式消耗）
            cached = self._cv_frame_cache.get(source_id)
            if cached and abs(cached[0] - current_time) < 0.02:
                return cached[1]

            from cutora.render.transition_runtime import (
                source_duration_of, source_time_for_clip,
            )
            src_len = source_duration_of(clip, source=source)
            fps = 30.0
            task = getattr(self, "_task_data", None)
            tl = _tl_of(task) if task else None
            if tl is not None:
                try:
                    fps = float(getattr(tl, "fps", 30.0) or 30.0)
                except (TypeError, ValueError):
                    fps = 30.0
            if fps <= 0:
                fps = 30.0
            src_t, _fr = source_time_for_clip(
                clip, current_time, src_len, fps=fps, extend=bool(extend_handles))
            if src_t is None:
                return None
            source_time = max(0.0, float(src_t))
            target_msec = source_time * 1000.0

            # 提取挂载的异步解码器守护进程（Q188：LRU 池，超过上限就停掉最久没用的）
            decoder = self._async_decoders.get(source_id)
            if decoder is not None:
                self._async_decoders.move_to_end(source_id)
            else:
                decoder = AsyncVideoDecoder(path, source_id)
                decoder.seek_req = target_msec  # 从实际需要的位置开始解码，而非始终从 0
                _seek_trace_emit(
                    "seek_req", msec=target_msec, qlen=0,
                    source_id=source_id, where="decoder_start")
                decoder.start()
                self._async_decoders[source_id] = decoder
                while len(self._async_decoders) > PREVIEW_DECODER_POOL:
                    _old_id, old = self._async_decoders.popitem(last=False)
                    self._cv_frame_cache.pop(_old_id, None)
                    self._stop_decoder_later(old)

            # O(1) 毫无阻塞地从环形队列中弹出预解压帧！
            qimg = decoder.get_frame(target_msec)
            
            if qimg is not None:
                self._cv_frame_cache[source_id] = (current_time, qimg)
                return qimg
            return None
        except Exception:
            return None

    def _draw_video_overlay_preview_qimage(self, painter, clip, qimg, tw, th):
        """用 QImage 帧绘制视频覆层（非主视频源）。复用主函数以支持 flip 等。"""
        return self._draw_video_overlay_preview(painter, clip, tw, th, frame_override=qimg)

    def _clip_draw_params(self, clip, fw, fh, tw, th):
        """Fit rect and the transform the preview painter applies to a clip."""
        fit_mode = getattr(clip, "fit_mode", None) or (
            clip.get("fit_mode") if isinstance(clip, dict) else None) or DEFAULT_VIDEO_FIT_MODE
        scale = float(getattr(clip, "scale", 1.0) if not isinstance(clip, dict) else clip.get("scale", 1.0))
        offset_x = float(getattr(clip, "x", 0.0) if not isinstance(clip, dict) else clip.get("x", 0.0))
        offset_y = float(getattr(clip, "y", 0.0) if not isinstance(clip, dict) else clip.get("y", 0.0))
        rotation = float(getattr(clip, "rotation", 0.0) if not isinstance(clip, dict) else clip.get("rotation", 0.0))
        opacity = float(getattr(clip, "opacity", 255) if not isinstance(clip, dict) else clip.get("opacity", 255))
        from cutora.render.keyframe import has_keyframes, resolve_clip_visual
        _kf_t = getattr(self, "_current_time", None)
        if _kf_t is not None and has_keyframes(clip):
            vis = resolve_clip_visual(clip, _kf_t)
            scale = float(vis["scale"])
            offset_x = float(vis["x"])
            offset_y = float(vis["y"])
            rotation = float(vis["rotation"])
            opacity = float(vis["opacity"])
        flip_x = bool(getattr(clip, "flip_x", False) if not isinstance(clip, dict) else clip.get("flip_x", False))
        flip_y = bool(getattr(clip, "flip_y", False) if not isinstance(clip, dict) else clip.get("flip_y", False))
        from cutora.model.project_schema import clip_scale_y
        rect = self._compute_fit_rect_preview(
            fw, fh, tw, th, fit_mode, scale, offset_x, offset_y,
            scale_y=clip_scale_y(clip))
        return {
            "rotation": rotation,
            "opacity": opacity,
            "flip_x": flip_x,
            "flip_y": flip_y,
            "rect": rect,
        }

    def _clip_source_sec(self, clip, tl):
        t = float(getattr(self, "_current_time", 0.0) or 0.0)
        try:
            from cutora.render.transition_runtime import (
                source_duration_of, source_time_for_clip,
            )
            src = None
            sid = getattr(clip, "source_id", None)
            if tl is not None and sid:
                src = (getattr(tl, "sources", {}) or {}).get(sid)
            src_len = source_duration_of(clip, source=src)
            fps = 30.0
            if tl is not None:
                try:
                    fps = float(getattr(tl, "fps", 30.0) or 30.0)
                except (TypeError, ValueError):
                    fps = 30.0
            if fps <= 0:
                fps = 30.0
            src_t, _fr = source_time_for_clip(
                clip, t, src_len, fps=fps, extend=False)
            if src_t is None:
                return 0.0
            return max(0.0, float(src_t))
        except Exception:
            return max(0.0, t)

    def _opaque_aspect_frame(self, path):
        cache = self._opaque_hit_frames
        key = str(path)
        hit = cache.get(key)
        if hit is not None:
            return hit
        from PySide6.QtGui import QColor, QImage
        w, h = 16, 9
        try:
            from cutora.render.readers import _probe_video_has_alpha
            info = _probe_video_has_alpha(path) or {}
            w = max(1, int(info.get("width") or 16))
            h = max(1, int(info.get("height") or 9))
        except Exception:
            w, h = 16, 9
        m = max(w, h)
        if m > 32:
            s = 32.0 / float(m)
            w = max(1, int(round(w * s)))
            h = max(1, int(round(h * s)))
        img = QImage(w, h, QImage.Format.Format_ARGB32)
        img.fill(QColor(255, 255, 255, 255))
        # Hit-rect stand-in, not source pixels. Eyedropper / GIF click-through
        # must not treat this as a real frame (16×9 is already ≥ 64 px).
        img.setText("cutora.hit", "aspect")
        cache[key] = img
        return img

    def _sync_alpha_frame(self, key, path, clip, tl):
        t = float(getattr(self, "_current_time", 0.0) or 0.0)
        bucket = int(round(t * 10.0))
        cache = self._alpha_hit_cache
        ck = (str(key), bucket)
        if ck in cache:
            return cache[ck]
        from cutora.render.readers import read_video_frame_qimage
        mode = getattr(clip, "alpha_mode", None)
        img = read_video_frame_qimage(
            path, self._clip_source_sec(clip, tl), alpha_mode=mode)
        if len(cache) > 6:
            cache.clear()
        cache[ck] = img
        return img

    def _frame_for_video_hit(self, clip, tl):
        src_id = getattr(clip, "source_id", None)
        src = None
        if tl is not None and src_id:
            src = (getattr(tl, "sources", {}) or {}).get(src_id)
        path = None
        if src is not None:
            path = getattr(src, "path", None) or (
                src.get("path") if isinstance(src, dict) else None)
        if not path:
            return None
        key = src_id or path
        cached = self._cv_frame_cache.get(key)
        if cached:
            frame = cached[1]
            if frame is not None and not frame.isNull():
                alpha = bool(getattr(clip, "alpha", False))
                if (not alpha) or frame.hasAlphaChannel():
                    return frame
        decoder = self._async_decoders.get(key)
        if decoder is not None:
            frame = getattr(decoder, "last_frame", None)
            if frame is not None and not frame.isNull():
                return frame
        # Program frame is already on screen. Opening a capture here is the
        # Q71 200ms stall (AsyncVideoDecoder.__init__ calls _open_video_capture
        # on the GUI thread). Hit rects only need aspect; pixel masks fill in
        # on a later paint once a decoder already exists.
        if not bool(getattr(clip, "alpha", False)):
            return self._opaque_aspect_frame(path)
        alpha_cached = self._alpha_hit_cache.get(
            (str(key), int(round(float(getattr(self, "_current_time", 0.0) or 0.0) * 10.0))))
        if alpha_cached is not None:
            return alpha_cached
        # GIF / alpha click-through needs the real alpha plane. One closed
        # capture, not AsyncVideoDecoder (that is the Q71 GUI-thread stall).
        alt = self._sync_alpha_frame(key, path, clip, tl)
        if alt is not None and not alt.isNull():
            return alt
        return self._opaque_aspect_frame(path)

    def _remember_video_hit(self, clip, frame, tw, th, track, dest=None):
        """记下这个片段画在哪，供点选命中。

        ``dest`` 是分屏格子在画布上的 (x, y, w, h)：片段是按格子尺寸排版的，
        算完再整体平移到格子位置，跟 paint_split_video_tracks 的换算一致。
        """
        if frame is None or frame.isNull():
            return
        stub = (
            int(frame.width()) * int(frame.height()) < 64
            or frame.text("cutora.hit") == "aspect")
        if bool(getattr(clip, "alpha", False)) and (
                stub or not frame.hasAlphaChannel()):
            tl = _tl_of(self._task_data) if self._task_data else None
            src_id = getattr(clip, "source_id", None)
            src = (getattr(tl, "sources", {}) or {}).get(src_id) if tl is not None and src_id else None
            path = getattr(src, "path", None) if src is not None else None
            if path:
                alt = self._sync_alpha_frame(src_id or path, path, clip, tl)
                if alt is not None and not alt.isNull():
                    frame = alt
                    stub = (
                        int(frame.width()) * int(frame.height()) < 64
                        or frame.text("cutora.hit") == "aspect")
        dw, dh = (float(tw), float(th))
        if dest is not None:
            dw, dh = float(dest[2]), float(dest[3])
        params = self._clip_draw_params(clip, frame.width(), frame.height(), dw, dh)
        rect = params["rect"]
        if rect is None or rect.width() <= 1.0 or rect.height() <= 1.0:
            return
        if track is not None:
            from cutora.render.track_transform import (
                compose_track_transform, track_needs_layer, track_props_of,
            )
            if track_needs_layer(track):
                rect = compose_track_transform(rect, track_props_of(track), (dw, dh))
        if dest is not None:
            # 格子里的坐标 → 画布坐标；超出格子的部分被 mask 挡住，点不中
            rect = rect.translated(float(dest[0]), float(dest[1]))
            rect = rect.intersected(QRectF(
                float(dest[0]), float(dest[1]), float(dest[2]), float(dest[3])))
            if rect.width() <= 1.0 or rect.height() <= 1.0:
                return
        rec = {
            "type": "video_clip",
            "clip": clip,
            "track": track,
            "rect": rect,
            "locked": bool(getattr(clip, "locked", False)),
            "frame_w": frame.width(),
            "frame_h": frame.height(),
            "frame": frame,            # Q152-C 吸管取色用（只是引用）
            "flip_x": bool(params["flip_x"]),
            "flip_y": bool(params["flip_y"]),
            "tw": tw,
            "th": th,
            "cell": tuple(dest) if dest is not None else None,
        }
        if stub:
            prog = getattr(self, "_program_frame", None)
            if (prog is not None and not prog.isNull()
                    and int(prog.width()) * int(prog.height()) >= 64
                    and not getattr(self, "_program_frame_placeholder", False)):
                rec["frame"] = prog
            self._hit_records.append(rec)
            return
        from cutora.render.hitmask import attach_mask
        op = max(0.0, min(1.0, float(params["opacity"]) / 255.0))
        attach_mask(
            rec, frame,
            rotation=params["rotation"],
            flip_x=params["flip_x"],
            flip_y=params["flip_y"],
            opacity=op,
        )
        self._hit_records.append(rec)

    def _collect_video_clip_hits(self, project, tl, tw, th, only=None):
        """Hit targets for clips already baked into the program frame.

        only：Q163 统一叠压时只登记这一段视频带里的轨道（命中顺序跟画的顺序一致）。"""
        try:
            from cutora.model.track_guard import track_is_muted, video_tracks_for_render
        except Exception:
            return
        solo = _preview_solo_ids(self)
        split_on = False
        try:
            from cutora.render.split_layout import split_enabled
            split_on = bool(split_enabled(project))
        except Exception:
            split_on = False
        for tr in video_tracks_for_render(project, solo_ids=solo):
            if not getattr(tr, "visible", True):
                continue
            if only is not None and not any(tr is x for x in only):
                continue
            try:
                if track_is_muted(tr, task=project, solo_ids=solo):
                    continue
            except Exception:
                pass
            dest = None
            if split_on:
                cid = getattr(tr, "split_cell", None)
                if cid:
                    from cutora.render.split_screen import cell_content_box
                    dest = cell_content_box(
                        (project or {}).get("split"), cid, tw, th)
                    if dest is None:
                        continue
            for clip in self._find_active_clips_in_track(tr, self._current_time):
                try:
                    frame = self._frame_for_video_hit(clip, tl)
                    self._remember_video_hit(clip, frame, tw, th, tr, dest=dest)
                except Exception:
                    from cutora.core.log import get_logger
                    get_logger("cutora.ui.canvas").exception("video clip hit failed")

    def _draw_video_overlay_preview(self, painter, clip, tw, th, frame_override=None):
        frame = frame_override or self._current_image
        if not frame or frame.isNull():
            return None
        # 翻转处理
        flip_x = bool(getattr(clip, "flip_x", False) if not isinstance(clip, dict) else clip.get("flip_x", False))
        flip_y = bool(getattr(clip, "flip_y", False) if not isinstance(clip, dict) else clip.get("flip_y", False))
        if flip_x or flip_y:
            frame = frame.mirrored(flip_x, flip_y)
        frame = self._apply_clip_crop_qimage(frame, clip)
        frame = self._grade_clip_frame_preview(frame, clip)
        fw = frame.width()
        fh = frame.height()
        if fw <= 0 or fh <= 0:
            return None
        params = self._clip_draw_params(clip, fw, fh, tw, th)
        rotation = params["rotation"]
        opacity = params["opacity"]
        target_rect = params["rect"]
        feather = int(getattr(clip, "feather", 0) if not isinstance(clip, dict) else clip.get("feather", 0))
        if feather > 0:
            scale_factor = min(target_rect.width() / fw, target_rect.height() / fh)
            feather_px = int(max(1, round(feather * max(0.01, scale_factor))))
            frame = self._apply_edge_feather_qimage(frame, feather_px)
        from cutora.render.frame_renderer import _fade_qimage
        frame = _fade_qimage(frame, clip)
        # Q152-B/C：抠像 + 遮罩（和导出同一个 apply_layer_fx）
        from cutora.fx.layer_fx import apply_layer_fx, mask_px_scale, source_ctx
        from cutora.fx import graph as _fxg
        src_ctx = None
        if getattr(clip, "ai_matte", None) or _fxg.ai_nodes(clip):
            # Q152-D：AI 抠像按源时间查缓存；画布这条链是 翻转 → 裁切
            task = getattr(self, "_task_data", None)
            src_ctx = source_ctx(clip, self._clip_source_sec(clip, _tl_of(task) if task else None),
                                 order="flip_crop")
        frame = apply_layer_fx(frame, clip, px_scale=mask_px_scale(fw, target_rect.width()), src=src_ctx,
                               view=_fxg.viewer_for(clip))
        start_t = float(getattr(clip, "start_t", 0.0) if not isinstance(clip, dict) else clip.get("start_t", 0.0))
        eff_dur = float(getattr(clip, "effective_duration", 0.0)) if hasattr(clip, "effective_duration") else 0.0
        if eff_dur <= 0.0:
            in_t = float(getattr(clip, "in_t", 0.0) if not isinstance(clip, dict) else clip.get("in_t", 0.0))
            out_t = float(getattr(clip, "out_t", in_t) if not isinstance(clip, dict) else clip.get("out_t", in_t))
            speed = float(getattr(clip, "speed", 1.0) if not isinstance(clip, dict) else clip.get("speed", 1.0))
            eff_dur = max(0.0, (out_t - in_t) / max(0.01, speed))
        end_t = start_t + eff_dur
        t_preset = getattr(clip, "transition_preset", None) if not isinstance(clip, dict) else clip.get("transition_preset", None)
        if not t_preset:
            t_preset = "none"
        t_dur = float(getattr(clip, "transition_duration", anim_engine.DEFAULT_TRANSITION_DURATION)
                      if not isinstance(clip, dict) else clip.get("transition_duration", anim_engine.DEFAULT_TRANSITION_DURATION))
        current_time = getattr(self, "_current_time", None)
        
        # --- NLE Gen-2 Standalone Transition Override ---
        t_start_override = start_t
        t_end_override = end_t
        clip_actual_start = start_t
        clip_actual_end = end_t
        
        if current_time is not None and getattr(self, "task_data", None):
            tl = _tl_of(self.task_data)
            if tl:
                for tr in getattr(tl, "tracks", []) or []:
                    if clip in (getattr(tr, "clips", []) or []):
                        for trans in getattr(tr, "transitions", []) or []:
                            trans_end = trans.start_t + trans.duration
                            if trans.start_t <= current_time <= trans_end:
                                parts = trans.preset.split("_")
                                base_type = "_".join(parts[:-1]) if parts[-1] in ("in", "out", "both") else trans.preset
                                
                                # Outgoing clip (ends near or before the transition end)
                                if abs(clip_actual_end - trans_end) < 0.2 or clip_actual_end <= trans_end:
                                    t_preset = f"{base_type}_out"
                                    t_dur = trans.duration
                                    t_end_override = trans_end
                                    
                                # Incoming clip (starts near or after the transition start)
                                elif abs(clip_actual_start - trans.start_t) < 0.2 or clip_actual_start >= trans.start_t:
                                    t_preset = f"{base_type}_in"
                                    t_dur = trans.duration
                                    t_start_override = trans.start_t
                                    
                                break
                        break

        t_op, t_scale, t_dx, t_dy = anim_engine.compute_transition_params(
            current_time, t_start_override, t_end_override, t_preset, t_dur
        )
        # Q117：画面没铺满的地方先垫背景（模糊/镜像/平铺/拉伸），再画前景。
        # 跟导出走同一个 fill_bg.paint_fill，不另写一份。
        try:
            from cutora.render.fill_bg import fill_spec_at, needs_fill, paint_fill
            _spec = fill_spec_at(clip, current_time)
            if _spec["mode"] != "none" and needs_fill(
                _spec, target_rect, tw, th,
                split_rect=getattr(painter, "_split_layer_rect", None),
            ):
                painter.save()
                painter.setOpacity(max(0.0, min(1.0, opacity / 255.0)) * t_op)
                paint_fill(painter, frame, _spec, tw, th, target_rect)
                painter.restore()
        except Exception:
            from cutora.core.log import get_logger
            get_logger("cutora.ui.canvas").exception("预览背景填充失败")
        painter.save()
        base_op = max(0.0, min(1.0, opacity / 255.0)) * t_op
        painter.setOpacity(base_op)

        if t_dx or t_dy:
            painter.translate(t_dx, t_dy)
            
        center = target_rect.center()
        painter.translate(center)
        if rotation:
            painter.rotate(rotation)
            
        draw_rect = QRectF(-target_rect.width() / 2, -target_rect.height() / 2, target_rect.width(), target_rect.height())

        # --- Phase 6: Radial Motion Blur Renderer ---
        if t_preset and "zoom_in" in t_preset and abs(t_scale - 1.0) > 0.01:
            steps = 5
            blur_range = abs(t_scale - 1.0) * 0.4  # the delta scale spread
            painter.setOpacity(max(0.01, base_op / steps))
            for i in range(steps):
                s = t_scale - (blur_range / 2.0) + i * (blur_range / max(1, steps - 1))
                painter.save()
                painter.scale(s, s)
                painter.drawImage(draw_rect, frame)
                painter.restore()
        else:
            if abs(t_scale - 1.0) > 0.0001:
                painter.scale(t_scale, t_scale)
            painter.drawImage(draw_rect, frame)
            text_meta = getattr(clip, "thumbnail_text", None) if not isinstance(clip, dict) else clip.get("thumbnail_text")
            has_overlay = bool(getattr(clip, "thumbnail_text_as_overlay", False)) if not isinstance(clip, dict) else bool(clip.get("thumbnail_text_as_overlay", False))
            if bool(getattr(clip, "is_thumbnail_leadin", False) if not isinstance(clip, dict) else clip.get("is_thumbnail_leadin", False)):
                if text_meta and not has_overlay:
                    _draw_thumbnail_text_on_rect(painter, text_meta, target_rect)
        painter.restore()
        return target_rect

    def _grade_clip_frame_preview(self, qimg, clip):
        task = getattr(self, "_task_data", None)
        if isinstance(task, dict) and task.get("_grade_on") is False:
            return qimg
        try:
            from cutora.render.grade import apply_clip_track_grade
            track = None
            tl = _tl_of(task) if task else None
            tracks = getattr(tl, "tracks", None) if tl is not None else None
            if tracks is None and isinstance(tl, dict):
                tracks = tl.get("tracks") or []
            for tr in tracks or []:
                clips = getattr(tr, "clips", None) if not isinstance(tr, dict) else tr.get("clips")
                if clip in (clips or []):
                    track = tr
                    break
            return apply_clip_track_grade(qimg, clip, track)
        except Exception:
            from cutora.core.log import get_logger
            get_logger("cutora.ui.canvas").exception("预览片段调色失败")
            return qimg

    def _apply_clip_crop_qimage(self, qimg, clip):
        try:
            cw = qimg.width()
            ch = qimg.height()
            cl = int(getattr(clip, "crop_left", 0) if not isinstance(clip, dict) else clip.get("crop_left", 0))
            cr = int(getattr(clip, "crop_right", 0) if not isinstance(clip, dict) else clip.get("crop_right", 0))
            ct = int(getattr(clip, "crop_top", 0) if not isinstance(clip, dict) else clip.get("crop_top", 0))
            cb = int(getattr(clip, "crop_bottom", 0) if not isinstance(clip, dict) else clip.get("crop_bottom", 0))
            if cl <= 0 and cr <= 0 and ct <= 0 and cb <= 0:
                return qimg
            cl = max(0, cl); cr = max(0, cr); ct = max(0, ct); cb = max(0, cb)
            x = min(cl, cw - 1)
            y = min(ct, ch - 1)
            w = max(1, cw - cl - cr)
            h = max(1, ch - ct - cb)
            return qimg.copy(x, y, w, h)
        except Exception:
            return qimg

    def _apply_edge_feather_qimage(self, qimg, feather):
        try:
            feather = int(max(0, feather))
            if feather <= 0:
                return qimg
            img = qimg.convertToFormat(QImage.Format.Format_BGR888)
            w = img.width()
            h = img.height()
            ptr = img.bits()
            arr = np.frombuffer(ptr, np.uint8).reshape((h, img.bytesPerLine()))
            bgr = arr[:, :w * 3].reshape((h, w, 3)).copy()
            yy, xx = np.ogrid[:h, :w]
            # 注意：xx 形状为 (1,w)，yy 形状为 (h,1)，不能直接 np.minimum.reduce(list)
            # 否则会触发 numpy: "setting an array element with a sequence"（形状不一致）
            dist = np.minimum(xx, (w - 1) - xx)
            dist = np.minimum(dist, yy)
            dist = np.minimum(dist, (h - 1) - yy)
            t = np.clip(dist / float(max(1, feather)), 0.0, 1.0)
            alpha = (t * t * (3.0 - 2.0 * t) * 255.0).astype(np.uint8)
            alpha_f = (alpha.astype(np.float32) / 255.0)[:, :, np.newaxis]
            bgr_premul = (bgr.astype(np.float32) * alpha_f).astype(np.uint8)
            bgra = np.ascontiguousarray(np.dstack([bgr_premul, alpha]))
            out = QImage(bgra.data, w, h, bgra.strides[0], QImage.Format.Format_ARGB32_Premultiplied)
            return out.copy()
        except Exception:
            return qimg

    def _compute_fit_rect_preview(self, src_w, src_h, dst_w, dst_h, fit_mode, scale, offset_x, offset_y, scale_y=1.0):
        if src_w <= 0 or src_h <= 0:
            return QRectF(0, 0, dst_w, dst_h)
        fit_mode = (fit_mode or DEFAULT_VIDEO_FIT_MODE).lower()
        if fit_mode == "stretch":
            tw = dst_w * scale
            th = dst_h * scale
        elif fit_mode == "fill":
            ratio = max(dst_w / src_w, dst_h / src_h)
            tw = src_w * ratio * scale
            th = src_h * ratio * scale
        else:
            ratio = min(dst_w / src_w, dst_h / src_h)
            tw = src_w * ratio * scale
            th = src_h * ratio * scale
        sy = float(scale_y if scale_y not in (None, "") else 1.0)
        if sy > 0 and abs(sy - 1.0) > 1e-9:
            th *= sy
        x = (dst_w - tw) / 2 + offset_x
        y = (dst_h - th) / 2 + offset_y
        return QRectF(x, y, tw, th)

    def _get_active_video_clip_preview(self, project, t):
        tl = _tl_of(project) if isinstance(project, dict) and project else None
        if tl is None:
            return None
        for tr in tl.tracks:
            if getattr(tr, "type", None) != "video":
                continue
            for clip in getattr(tr, "clips", []) or []:
                try:
                    start_t = float(getattr(clip, "start_t", 0.0))
                    in_t = float(getattr(clip, "in_t", 0.0))
                    out_t = float(getattr(clip, "out_t", in_t))
                    clip_len = max(0.0, out_t - in_t)
                except Exception:
                    continue
                if start_t <= t <= start_t + clip_len:
                    return clip
        return None

    def export_current_frame(self):
        if not self._task_data:
            return
        default_name = f"frame_{int(self._current_time * 1000)}ms.png"
        save_path, _ = QFileDialog.getSaveFileName(self, "导出当前帧", default_name, "PNG Images (*.png)")
        if not save_path:
            return
        try:
            tw = int(self._video_dims[0])
            th = int(self._video_dims[1])
            canvas = QImage(tw, th, QImage.Format.Format_ARGB32_Premultiplied)
            canvas.fill(Qt.GlobalColor.black)
            
            # Q145：画布上的视频层可能是按显示像素合成的小图，导出当前帧要满分辨率重合成一张
            saved_prog = getattr(self, "_program_frame", None)
            saved_bands = getattr(self, "_program_bands", None)
            self._program_bands = None       # Q163：统一叠压的分段图也按满分辨率重算
            if saved_prog is not None and not saved_prog.isNull() and (
                    saved_prog.width() != tw or saved_prog.height() != th):
                try:
                    from cutora.preview.engine import PreviewEngine
                    _eng = PreviewEngine()
                    try:
                        self._program_frame = _eng.video_frame_at(
                            self._task_data, float(self._current_time))
                    finally:
                        _eng.close()
                except Exception:
                    self._program_frame = saved_prog
            painter = QPainter(canvas)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            try:
                self._draw_preview_local(painter, export_mode=True)
            finally:
                painter.end()
                self._program_frame = saved_prog
                self._program_bands = saved_bands
                
            canvas.save(save_path, "PNG")
            QMessageBox.information(self, "导出成功", f"当前帧已成功导出至：\n{save_path}")
        except Exception as e:
            print(f"Error exporting frame: {e}")
            QMessageBox.warning(self, "导出失败", f"无法导出当前帧：{e}")

    def _playhead_t(self):
        return float(getattr(self, "_current_time", 0.0) or 0.0)

    def _commit_host_param(self, host, path, value, static_assign=None):
        """Write *value* as a keyframe at the playhead when that path is
        enabled; otherwise assign the static field. Same mutation as
        PropertyPanel._auto_key (set_point at local t).
        """
        from cutora.render.keyframe import local_time, path_enabled, set_point
        if host is None:
            return
        if path_enabled(host, path):
            set_point(host, path, local_time(host, self._playhead_t()), value)
            return
        if callable(static_assign):
            static_assign(value)

    def _nudge_host_param(self, host, path, delta, fallback, static_assign):
        from cutora.render.keyframe import local_time, path_enabled, value_at
        if host is None:
            return
        fb = float(fallback)
        if path_enabled(host, path):
            cur = float(value_at(host, path, local_time(host, self._playhead_t()), fb))
            self._commit_host_param(host, path, cur + float(delta))
            return
        if callable(static_assign):
            static_assign(fb + float(delta))

    def _arm_drag(self, item, mode, handle, pos, began=True):
        """Start one move/resize gesture. Snapshot is taken before any delta."""
        self._dragging_item = item
        self._drag_mode = mode
        self._resize_handle = handle
        self._last_mouse_pos = QPointF(pos)
        self._drag_origin_rect = QRectF(self._selected_rect) if self._selected_rect is not None else None
        self._drag_accum = [0.0, 0.0]
        self._sub_drag_base = None
        if began:
            self.gesture_began.emit()

    def mousePressEvent(self, event):
        click_pos = event.position()
        if not self._task_data: return
        # Q145：中键拖 / 按住 H 左键拖 = 平移视图
        if event.button() == Qt.MouseButton.MiddleButton or (
                event.button() == Qt.MouseButton.LeftButton and self._hand_hold):
            self._view_pan = QPointF(click_pos)
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            return

        if event.button() == Qt.MouseButton.RightButton and getattr(self, "_mask_mode", None):
            # 多边形 / 贝塞尔 / AI 点选 / 跟踪模式里右键 = 退一步 / 退出（以前先弹了右键菜单，模式里的右键没用上）
            _xy = self._video_xy(click_pos)
            if _xy is not None:
                from cutora.ui import canvas_mask as _ck
                if _ck.press(self, _xy[0], _xy[1], event):
                    self.update()
                    return
        if event.button() == Qt.MouseButton.RightButton:
            if self._wysiwyg is not None:
                self._commit_inline_edit()
            self._context_menu(event)
            return
        if self._wysiwyg is not None:
            if self._wysiwyg_press(event):
                return
        elif self._inline_editor is not None:
            if self._inline_editor.geometry().contains(QPoint(int(click_pos.x()), int(click_pos.y()))):
                return
            self._commit_inline_edit()
        
        # Widget 坐标 → 视频坐标（使用实际预览区域，保证 1:1 同步）
        target_rect = self._preview_target_rect()
        if target_rect.width() <= 0 or target_rect.height() <= 0:
            return
        scale_x = self._video_dims[0] / target_rect.width()
        scale_y = self._video_dims[1] / target_rect.height()
        video_mx = (click_pos.x() - target_rect.x()) * scale_x
        video_my = (click_pos.y() - target_rect.y()) * scale_y
        
        from cutora.ui import canvas_mask as _ck
        if _ck.press(self, video_mx, video_my, event):
            return
        from cutora.ui import canvas_motion as _cm
        if _cm.press(self, video_mx, video_my, event):
            return
        tw, th = self._video_dims
        hit = self._hit_split_handle(video_mx, video_my, tw, th)
        if hit is not None:
            import copy
            win = self.window()
            if win is not None:
                win._active_split_divider_id = str(hit["id"])
            sub_mode = hit.get("sub_mode") or "pos"
            if str(hit.get("axis")) == "circle":
                if event.modifiers() & Qt.KeyboardModifier.AltModifier and sub_mode == "pos":
                    sub_mode = "scale"
                mode = sub_mode
            else:
                mode = "rot" if (event.modifiers() & Qt.KeyboardModifier.AltModifier) else "pos"
            self._split_drag = {
                "id": hit["id"],
                "mode": mode,
                "handle": hit,
                "orig_split": copy.deepcopy(self._task_data.get("split")) if isinstance(self._task_data, dict) else None,
                "orig_pos": hit.get("pos"),
                "orig_rot": hit.get("rotation_deg"),
                "orig_cx": hit.get("cx"),
                "orig_cy": hit.get("cy"),
                "orig_r": hit.get("r"),
                "orig_aspect": hit.get("aspect", 1.0),
                "orig_rx_px": hit.get("rx_px"),
                "orig_ry_px": hit.get("ry_px"),
                "pos": hit.get("pos"),
                "rotation_deg": hit.get("rotation_deg"),
                "r": hit.get("r"),
                "aspect": hit.get("aspect", 1.0),
                "last_patch": None,
            }
            self._last_mouse_pos = QPointF(video_mx, video_my)
            self.update()
            return

        clicked_item = None
        if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            for record in reversed(self._hit_records):
                if record['rect'].contains(video_mx, video_my):
                    # 【允许在任何字幕上（含普通内嵌模式）运用字块选取】
                    if record.get("type") == "sub":
                        hit = self._find_word_hit(QPointF(video_mx, video_my), record)
                        if hit is not None:
                            self._dragging_item = None
                            self._drag_mode = None
                            self._resize_handle = None
                            self._selected_item = record
                            self._selected_rect = record.get('rect')
                            self._sub_text_selecting = True
                            self._sub_text_sel_range = {
                                "seg_idx": record.get("idx"),
                                "start_char": hit.get("char_start"),
                                "end_char": hit.get("char_end")
                            }
                            self._update_sub_text_sel_rects(record)
                            self._last_mouse_pos = QPointF(video_mx, video_my)
                            self.update()
                            return
                    break
            # Q128：Shift+点文本层 / 贴纸 → 加入或移出多选
            from cutora.render.hitmask import pick_record as _pick
            shift_hit = _pick(self._hit_records, video_mx, video_my, alt_bbox=False)
            if shift_hit is not None and shift_hit.get("type") in self.MULTI_TYPES:
                self._multi_toggle(shift_hit)
                if self._selected_item is not None:
                    top = self._selected_item
                    if top.get("type") == "overlay":
                        self.item_selected.emit("overlay", (top.get("list"), top.get("idx")))
                    else:
                        self.item_selected.emit("sticker", (top.get("ov_idx"), top.get("el_idx")))
                else:
                    self.item_selected.emit("", None)
                self.update()
                return
        # 先检测是否点击到缩放手柄
        if self._selected_item is not None and self._selected_rect is not None:
            handle = self._hit_resize_handle(
                QPointF(video_mx, video_my), self._selected_rect, self._selected_item)
            if handle and self._selected_item.get('type') in ('overlay', 'video_clip', 'sticker'):
                mode = "rotate" if handle == "rot" else "resize"
                self._arm_drag(self._selected_item, mode, handle, QPointF(video_mx, video_my))
                if mode == "rotate":
                    self._begin_rotate(self._selected_item, video_mx, video_my)
                return
            if handle and self._selected_item.get('type') == 'sub' and self._is_sub_adv_enabled(self._selected_item):
                self._arm_drag(self._selected_item, "resize", handle, QPointF(video_mx, video_my))
                return
            if handle and self._selected_item.get('type') == 'sub_span':
                self._arm_drag(self._selected_item, "resize", handle, QPointF(video_mx, video_my))
                return

        from cutora.render.hitmask import pick_record
        alt_bbox = bool(event.modifiers() & Qt.KeyboardModifier.AltModifier)
        clicked_item = pick_record(
            self._hit_records, video_mx, video_my, alt_bbox=alt_bbox)

        if clicked_item:
            self._multi_collapse = None
            if self._multi and not self._multi_contains(clicked_item):
                self._multi_clear()          # 点了组外的东西：散伙
            elif self._multi:
                # 点在组员上：按住能拖整组；松开没拖过 → 只选这一条（Canva / Figma 的习惯）
                self._multi_collapse = clicked_item
            self._selected_item = clicked_item
            self._selected_rect = clicked_item.get('rect')
            self._arm_drag(clicked_item, "move", None, QPointF(video_mx, video_my), began=False)
            # Q145：Ctrl+拖 = 以按下点（文字 / 图片以自身中心）缩放；Alt+拖 = 复制一份再拖
            _mods = event.modifiers()
            if (_mods & Qt.KeyboardModifier.ControlModifier) and \
                    clicked_item.get('type') in ('video_clip', 'overlay', 'sticker'):
                if self._ops_begin_zoom(clicked_item, click_pos, video_mx, video_my):
                    self._drag_mode = "zoom"
                    self._multi_collapse = None
            elif (_mods & Qt.KeyboardModifier.AltModifier) and \
                    clicked_item.get('type') in self.MULTI_TYPES:
                self._alt_dup_pending = True
            if clicked_item['type'] == 'overlay':
                payload = (clicked_item.get('list'), clicked_item['idx'])
                self.item_selected.emit(clicked_item['type'], payload)
            elif clicked_item['type'] == 'sticker':
                payload = (clicked_item.get('ov_idx'), clicked_item.get('el_idx'))
                self.item_selected.emit(clicked_item['type'], payload)
            elif clicked_item['type'] == 'video_clip':
                clip_obj = clicked_item.get('clip')
                if clip_obj is not None:
                    self.item_selected.emit('video_clip', clip_obj)
            elif clicked_item['type'] == 'sub_span':
                b_idx = clicked_item.get('block_idx')
                sp_idx = clicked_item.get('span_idx')
                self.item_selected.emit('sub_span', (clicked_item.get('seg_idx'), b_idx if isinstance(b_idx, int) and b_idx >= 0 else sp_idx))
            else:
                self.item_selected.emit(clicked_item['type'], clicked_item['idx'])
            # After select: the panel sync must not have cleared this drag.
            self.gesture_began.emit()
        else:
            self._dragging_item = None
            self._drag_mode = None
            self._resize_handle = None
            self._selected_item = None
            self._selected_rect = None
            self._multi_clear()
            self.item_selected.emit("", None)
            # Q128：空白处按下 → 可能是框选（松开时够大才算）
            self._band_begin(video_mx, video_my, additive=False)

    def mouseDoubleClickEvent(self, event):
        if not self._task_data:
            return
        if self._wysiwyg is not None and self._wysiwyg_double_click(event):
            return
        _xy = self._video_xy(event.position())
        if _xy is not None:
            from cutora.ui import canvas_mask as _ck
            if _ck.double_click(self, _xy[0], _xy[1]):
                return
            from cutora.ui import canvas_motion as _cm
            if _cm.double_click(self, _xy[0], _xy[1]):
                return
        target_rect = self._preview_target_rect()
        if target_rect.width() <= 0 or target_rect.height() <= 0:
            return
        scale_x = self._video_dims[0] / target_rect.width()
        scale_y = self._video_dims[1] / target_rect.height()
        video_mx = (event.position().x() - target_rect.x()) * scale_x
        video_my = (event.position().y() - target_rect.y()) * scale_y
        tw, th = self._video_dims
        hit = self._hit_split_handle(video_mx, video_my, tw, th)
        if hit is not None:
            self._commit_split_handle(hit, {"pos": 0.5, "rotation_deg": 0.0})
            return
        for record in reversed(self._hit_records or []):
            rect = record.get("rect")
            if rect is None or not rect.contains(QPointF(video_mx, video_my)):
                continue
            if record.get("type") == "overlay" and record.get("list") == "text_overlays":
                self._start_inline_edit(record, at=(video_mx, video_my))
                return
            if record.get("type") in ("sub", "sub_span"):
                self._start_inline_edit(record)
                return
            if record.get("type") == "sticker":
                ov_idx = record.get("ov_idx")
                el_idx = record.get("el_idx")
                try:
                    ov = self._task_data.get("image_overlays", [])[ov_idx]
                    el = (ov.get("elements") or [])[el_idx]
                    if el.get("kind") == "text":
                        self._start_inline_edit(record)
                        return
                except Exception:
                    return
                return            # 图片贴纸：双击不穿透到下面的视频
            if record.get("type") == "video_clip":
                # Q145：双击视频画面 = 重置位置 / 缩放 / 旋转（和中央监视器双击格子一致）
                if not getattr(record.get("clip"), "keyframes", None):
                    self.reset_video_transform(record)
                return

    def mouseMoveEvent(self, event):
        if self._preview_fullscreen:
            self.show_fs_hint()
        if self._view_pan is not None:
            p = QPointF(event.position())
            d = p - self._view_pan
            self._view_pan = p
            self.view_pan(d.x(), d.y())
            return
        if self._wysiwyg is not None and self._wysiwyg_move(event):
            return
        if getattr(self, "_mask_drag", None) is not None or getattr(self, "_mask_mode", None):
            xy = self._video_xy(event.position())
            if xy is not None:
                from cutora.ui import canvas_mask as _ck
                if _ck.move(self, xy[0], xy[1], event):
                    return
        if getattr(self, "_motion_drag", None) is not None:
            xy = self._video_xy(event.position())
            if xy is not None:
                from cutora.ui import canvas_motion as _cm
                _cm.move(self, xy[0], xy[1], event)
            return
        if self._band is not None and (event.buttons() & Qt.MouseButton.LeftButton):
            xy = self._video_xy(event.position())
            if xy is not None:
                self._band_update(xy[0], xy[1])
            return
        if self._split_drag is not None:
            xy = self._video_xy(event.position())
            if xy is None:
                return
            video_x, video_y = xy
            tw, th = self._video_dims
            handle = self._split_drag.get("handle") or {"id": self._split_drag["id"], "axis": "h"}
            from cutora.render.split_layout import set_divider_on_task
            task = self._task_data
            axis = str(handle.get("axis") or "h")
            if axis == "circle":
                import math
                sub_mode = str(self._split_drag.get("mode") or "pos")
                hx = float(self._split_drag.get("cx", handle.get("cx", 0.5))) * float(tw)
                hy = float(self._split_drag.get("cy", handle.get("cy", 0.5))) * float(th)
                base = max(1.0, float(min(tw, th)))
                rot_deg = float(self._split_drag.get("rotation_deg", handle.get("rotation_deg", 0.0)) or 0.0)
                rad = math.radians(rot_deg)
                ct, st = math.cos(rad), math.sin(rad)
                dx, dy = video_x - hx, video_y - hy
                lx = dx * ct + dy * st
                ly = -dx * st + dy * ct
                all_circ = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
                if sub_mode == "scale":
                    cur_aspect = float(self._split_drag.get("aspect", handle.get("aspect", 1.0)) or 1.0)
                    sa = math.sqrt(max(0.25, min(4.0, cur_aspect)))
                    eff_r_px = math.hypot(lx / sa, ly * sa)
                    new_r = max(0.04, min(0.48, eff_r_px / base))
                    patch = {"r": new_r}
                    if all_circ:
                        patch["apply_all_circles"] = True
                elif sub_mode == "rx":
                    orig_ry_px = float(self._split_drag.get("orig_ry_px") or handle.get("ry_px") or (0.15 * base))
                    new_rx = max(0.03, min(0.60, abs(lx) / base))
                    new_ry = max(0.03, min(0.60, orig_ry_px / base))
                    new_r = max(0.04, min(0.48, math.sqrt(new_rx * new_ry)))
                    new_aspect = max(0.30, min(3.00, new_rx / max(0.02, new_ry)))
                    patch = {"r": new_r, "aspect": new_aspect}
                    if all_circ:
                        patch["apply_all_circles"] = True
                elif sub_mode == "ry":
                    orig_rx_px = float(self._split_drag.get("orig_rx_px") or handle.get("rx_px") or (0.15 * base))
                    new_rx = max(0.03, min(0.60, orig_rx_px / base))
                    new_ry = max(0.03, min(0.60, abs(ly) / base))
                    new_r = max(0.04, min(0.48, math.sqrt(new_rx * new_ry)))
                    new_aspect = max(0.30, min(3.00, new_rx / max(0.02, new_ry)))
                    patch = {"r": new_r, "aspect": new_aspect}
                    if all_circ:
                        patch["apply_all_circles"] = True
                elif sub_mode == "rot":
                    ang = math.degrees(math.atan2(video_y - hy, video_x - hx)) + 90.0
                    while ang > 180.0:
                        ang -= 360.0
                    while ang < -180.0:
                        ang += 360.0
                    ang = self._ops_snap_rotation(ang)
                    patch = {"rotation_deg": round(ang, 1)}
                    if all_circ:
                        patch["apply_all_circles"] = True
                else:
                    cx = max(0.04, min(0.96, video_x / max(1.0, float(tw))))
                    cy = max(0.04, min(0.96, video_y / max(1.0, float(th))))
                    patch = {"cx": cx, "cy": cy, "pos": cy}
            elif self._split_drag.get("mode") == "rot":
                import math
                hx, hy = handle.get("x", tw / 2.0), handle.get("y", th / 2.0)
                ang = math.degrees(math.atan2(video_y - hy, video_x - hx))
                if axis != "h":
                    ang = ang - 90.0
                ang = self._ops_snap_rotation(ang)        # Q145：贴 0°（按住 N 不吸）
                patch = {"rotation_deg": max(-45.0, min(45.0, ang))}
            else:
                pos = video_y / max(1.0, float(th)) if axis == "h" else video_x / max(1.0, float(tw))
                pos = self._ops_snap_divider(pos, axis)   # Q145：吸 1/3、1/2、2/3
                patch = {"pos": pos}
            if isinstance(task, dict):
                set_divider_on_task(task, handle["id"], patch)
            self._split_drag.update(patch)
            self._split_drag["last_patch"] = dict(patch)
            win = self.window()
            fn = getattr(win, "_present_preview_frame", None)
            if callable(fn):
                try:
                    fn()
                except Exception:
                    pass
            self.update()
            self._last_mouse_pos = QPointF(video_x, video_y)
            return
        xy = self._video_xy(event.position())
        if xy is not None:
            tw, th = self._video_dims
            hit = self._hit_split_handle(xy[0], xy[1], tw, th)
            hid = hit.get("id") if hit else None
            if hid != self._split_hover:
                self._split_hover = hid
                self.update()
        if self._sub_text_selecting and self._selected_item is not None:
            curr_pos = event.position()
            target_rect = self._preview_target_rect()
            if target_rect.width() <= 0 or target_rect.height() <= 0:
                return
            scale_x = self._video_dims[0] / target_rect.width()
            scale_y = self._video_dims[1] / target_rect.height()
            video_x = (curr_pos.x() - target_rect.x()) * scale_x
            video_y = (curr_pos.y() - target_rect.y()) * scale_y
            hit = self._find_word_hit(QPointF(video_x, video_y), self._selected_item)
            if hit is not None:
                self._sub_text_sel_range["drag_char_start"] = hit.get("char_start")
                self._sub_text_sel_range["drag_char_end"] = hit.get("char_end")
                self._update_sub_text_sel_rects(self._selected_item)
                self.update()
            return
        if self._dragging_item:
            curr_pos = event.position()
            # Widget 坐标 → 视频坐标：保持世界坐标 delta
            target_rect = self._preview_target_rect()
            if target_rect.width() <= 0 or target_rect.height() <= 0:
                return
            scale_x = self._video_dims[0] / target_rect.width()
            scale_y = self._video_dims[1] / target_rect.height()
            video_x = (curr_pos.x() - target_rect.x()) * scale_x
            video_y = (curr_pos.y() - target_rect.y()) * scale_y
            dx = video_x - self._last_mouse_pos.x()
            dy = video_y - self._last_mouse_pos.y()

            if self._drag_mode == "zoom":
                self._ops_zoom_move(event.position())
                return

            if self._drag_mode == "rotate":
                self._drag_mods = event.modifiers()
                self._apply_rotate(self._dragging_item, video_x, video_y)
                self._last_mouse_pos = QPointF(video_x, video_y)
                self.item_dragged.emit()
                self.update()
                return

            if self._drag_mode == "resize" and self._resize_handle:
                # ── 缩放吸附：对被拽边/角做画布边缘+中心吸附 ──
                # [FIX-IMG] 与移动一致：吸附基于“起始矩形 + 累积位移”，
                # 否则被拽的边恰好贴在画布边缘时（例如图片按画布宽度适配）会被永久锁死
                if (self._drag_origin_rect is not None and self._drag_accum is not None
                        and self._selected_rect is not None):
                    self._drag_accum[0] += dx
                    self._drag_accum[1] += dy
                    _cur_rect = self._selected_rect
                    self._selected_rect = self._drag_origin_rect
                    try:
                        _tx, _ty = self._calc_snap_resize(self._drag_accum[0], self._drag_accum[1], self._resize_handle)
                    finally:
                        self._selected_rect = _cur_rect
                    _h = self._resize_handle
                    if _h in ('w', 'nw', 'sw'):
                        _ax = _cur_rect.left() - self._drag_origin_rect.left()
                    elif _h in ('e', 'ne', 'se'):
                        _ax = _cur_rect.right() - self._drag_origin_rect.right()
                    else:
                        _ax = _tx
                    if _h in ('n', 'nw', 'ne'):
                        _ay = _cur_rect.top() - self._drag_origin_rect.top()
                    elif _h in ('s', 'sw', 'se'):
                        _ay = _cur_rect.bottom() - self._drag_origin_rect.bottom()
                    else:
                        _ay = _ty
                    snap_dx, snap_dy = _tx - _ax, _ty - _ay
                else:
                    snap_dx, snap_dy = self._calc_snap_resize(dx, dy, self._resize_handle)
                self._apply_resize(
                    self._dragging_item, snap_dx, snap_dy, self._resize_handle,
                    event.modifiers())
            else:
                if self._alt_dup_pending:
                    self._ops_alt_duplicate()     # Q145：Alt+拖，第一下移动时复制
                # ── 移动吸附：对元素矩形做画布中心+边缘吸附 ──
                # [FIX-IMG] 吸附基于“起始矩形 + 累积位移”计算。
                # 原实现用当前矩形 + 单帧位移，元素恰好贴在画布边缘/中心时
                # 每帧位移都会被吸附回原位，导致元素永远拖不动。
                if self._selected_rect is not None and self._drag_origin_rect is not None and self._drag_accum is not None:
                    self._drag_accum[0] += dx
                    self._drag_accum[1] += dy
                    acc_x, acc_y = self._lock_axis(
                        self._drag_accum[0], self._drag_accum[1], event.modifiers())
                    snap_x, snap_y = self._calc_snap(
                        self._drag_origin_rect, acc_x, acc_y)
                    dx = snap_x - (self._selected_rect.x() - self._drag_origin_rect.x())
                    dy = snap_y - (self._selected_rect.y() - self._drag_origin_rect.y())
                elif self._selected_rect is not None:
                    dx, dy = self._lock_axis(dx, dy, event.modifiers())
                    dx, dy = self._calc_snap(self._selected_rect, dx, dy)
                else:
                    self._snap_lines = []

                self._multi_collapse = None       # 拖过了，组保留
                self._multi_move_others(dx, dy)   # Q128：组里其它成员同步平移
                if self._dragging_item['type'] == 'overlay':
                    list_name = self._dragging_item.get('list')
                    if list_name:
                        ov = self._task_data[list_name][self._dragging_item['idx']]
                        self._nudge_host_param(
                            ov, "x", dx, ov.get("x", 0.0),
                            lambda v, _ov=ov: _ov.__setitem__("x", v))
                        self._nudge_host_param(
                            ov, "y", dy, ov.get("y", 0.0),
                            lambda v, _ov=ov: _ov.__setitem__("y", v))
                        if self._selected_rect is not None:
                            self._selected_rect = self._selected_rect.translated(dx, dy)
                elif self._dragging_item['type'] == 'sticker':
                    ov_idx = self._dragging_item.get('ov_idx')
                    el_idx = self._dragging_item.get('el_idx')
                    if isinstance(ov_idx, int) and isinstance(el_idx, int):
                        image_ovs = self._task_data.get('image_overlays', [])
                        if 0 <= ov_idx < len(image_ovs):
                            ov = image_ovs[ov_idx]
                            elements = ov.get('elements', [])
                            if 0 <= el_idx < len(elements):
                                el = elements[el_idx]
                                ov_scale = float(ov.get('scale', 1.0))
                                ov_rot = float(ov.get('rotation', 0))
                                # [FIX-IMG] overlay 缩放是以元素中心为基点做的，不改变 el_x/el_y 的位移增益，
                                # 元素位移与画布位移为 1:1（与 _apply_resize 的换算一致）。
                                # 原来多除了一次 ov_scale，缩放过的图片拖拽会跟不上鼠标。
                                local_dx = dx
                                local_dy = dy
                                if ov_rot != 0:
                                    import math
                                    rad = math.radians(-ov_rot)
                                    cos_r = math.cos(rad)
                                    sin_r = math.sin(rad)
                                    rot_dx = local_dx * cos_r - local_dy * sin_r
                                    rot_dy = local_dx * sin_r + local_dy * cos_r
                                    local_dx, local_dy = rot_dx, rot_dy
                                self._nudge_host_param(
                                    el, "x", local_dx, el.get("x", 0.0),
                                    lambda v, _el=el: _el.__setitem__("x", v))
                                self._nudge_host_param(
                                    el, "y", local_dy, el.get("y", 0.0),
                                    lambda v, _el=el: _el.__setitem__("y", v))
                                # [FIX-IMG] 同步变换框，否则选择框不跟随图片，
                                # 且吸附计算会一直基于旧矩形导致图片被“粘住”无法移动
                                if self._selected_rect is not None:
                                    self._selected_rect = self._selected_rect.translated(dx, dy)
                elif self._dragging_item['type'] == 'sub_span':
                    seg_idx = self._dragging_item.get('seg_idx')
                    span_idx = self._dragging_item.get('span_idx')
                    segs = self._task_data.get('segments', [])
                    if isinstance(seg_idx, int) and 0 <= seg_idx < len(segs):
                        seg = segs[seg_idx]
                        # 找到对应的 adv_block 并更新其 overrides（持久层）
                        b_idx = self._dragging_item.get('block_idx')
                        adv_spans = seg.get("adv_rich_spans", [])
                        if b_idx is None and isinstance(span_idx, int) and 0 <= span_idx < len(adv_spans):
                            b_idx = adv_spans[span_idx].get("_block_idx")
                        if isinstance(b_idx, int):
                            adv_blocks = seg.get("adv_blocks", [])
                            if 0 <= b_idx < len(adv_blocks):
                                ov = adv_blocks[b_idx].get("overrides", {})
                                ov["advanced_textbox_x"] = float(ov.get("advanced_textbox_x", 0.0)) + dx
                                ov["advanced_textbox_y"] = float(ov.get("advanced_textbox_y", 0.0)) + dy
                                ov["advanced_textbox_enabled"] = True

                                adv_blocks[b_idx]["overrides"] = ov
                                # 同步更新 span 用于即时视觉反馈（不重复加 dx）
                                if isinstance(span_idx, int) and 0 <= span_idx < len(adv_spans):
                                    adv_spans[span_idx]["advanced_textbox_x"] = ov["advanced_textbox_x"]
                                    adv_spans[span_idx]["advanced_textbox_y"] = ov["advanced_textbox_y"]
                    if self._selected_rect is not None:
                        self._selected_rect = self._selected_rect.translated(dx, dy)
                elif self._dragging_item['type'] == 'sub':
                    seg, style, _ = self._get_sub_style_and_seg(self._dragging_item)
                    if isinstance(style, dict):
                        if bool(style.get("advanced_textbox_enabled", False)):
                            style["advanced_textbox_x"] = float(style.get("advanced_textbox_x", 0.0)) + dx
                            style["advanced_textbox_y"] = float(style.get("advanced_textbox_y", 0.0)) + dy
                        else:
                            from cutora.subtitle.stylepack import (
                                rendered_center_px, set_position_px,
                            )
                            cw, ch = int(self._video_dims[0]), int(self._video_dims[1])
                            # dx/dy are this frame's movement, already snapped.
                            # The gesture base is the center the paint used,
                            # not a missing pos_y turned into 0 (the top edge).
                            base = getattr(self, "_sub_drag_base", None)
                            if base is None:
                                cx, cy = rendered_center_px(style, cw, ch)
                                base = [cx, cy]
                                self._sub_drag_base = base
                            base[0] += float(dx)
                            base[1] += float(dy)
                            set_position_px(style, base[0], base[1], cw, ch)
                    if self._selected_rect is not None:
                        self._selected_rect = self._selected_rect.translated(dx, dy)
                elif self._dragging_item['type'] == 'video_clip':
                    clip = self._dragging_item.get('clip')
                    if clip is not None:
                        self._nudge_host_param(
                            clip, "x", dx, getattr(clip, "x", 0.0),
                            lambda v, _c=clip: setattr(_c, "x", v))
                        self._nudge_host_param(
                            clip, "y", dy, getattr(clip, "y", 0.0),
                            lambda v, _c=clip: setattr(_c, "y", v))
                        if self._selected_rect is not None:
                            self._selected_rect = self._selected_rect.translated(dx, dy)
            
            self._last_mouse_pos = QPointF(video_x, video_y)
            self.item_dragged.emit()
            self.update()

    def wheelEvent(self, event):
        xy = self._video_xy(event.position())
        if xy is not None:
            tw, th = self._video_dims
            hit = self._hit_split_handle(xy[0], xy[1], tw, th, radius=28.0)
            if hit is not None:
                from cutora.render.split_layout import normalize_split
                sp = normalize_split((self._task_data or {}).get("split"))
                cur = 24.0
                for d in sp.get("dividers") or []:
                    if str(d.get("id")) == str(hit["id"]):
                        cur = float(d.get("feather_px") or 24.0)
                        break
                delta = 4.0 if event.angleDelta().y() > 0 else -4.0
                self._commit_split_handle(hit, {"feather_px": max(0.0, cur + delta)})
                event.accept()
                return
        if self._ops_wheel(event):
            event.accept()
            return
        super().wheelEvent(event)

    def mouseReleaseEvent(self, event):
        if self._view_pan is not None:
            self._view_pan = None
            if self._hand_hold:
                self.setCursor(Qt.CursorShape.OpenHandCursor)
            else:
                self.unsetCursor()
            return
        if self._wysiwyg is not None and self._wysiwyg_release(event):
            return
        if getattr(self, "_mask_drag", None) is not None or getattr(self, "_mask_mode", None):
            from cutora.ui import canvas_mask as _ck
            _ck.release(self)
            return
        if getattr(self, "_motion_drag", None) is not None:
            from cutora.ui import canvas_motion as _cm
            _cm.release(self)
            return
        if self._band is not None:
            self._band_finish()
            return
        if self._split_drag is not None:
            import copy
            drag = self._split_drag
            self._split_drag = None
            task = self._task_data
            final = dict(drag.get("last_patch") or {})
            if final:
                if isinstance(task, dict) and isinstance(drag.get("orig_split"), dict):
                    task["split"] = copy.deepcopy(drag["orig_split"])
                self._commit_split_handle({"id": drag["id"]}, final)
            else:
                win = self.window()
                panel = getattr(win, "prop_panel", None) if win is not None else None
                if panel is not None and getattr(panel, "current_type", None) == "track":
                    cur = getattr(panel, "_track_obj", None)
                    if cur is not None:
                        panel.load_data("track", cur)
            self.update()
            return
        if self._sub_text_selecting and self._selected_item is not None:
            sel = self._sub_text_sel_range or {}
            hits = self._get_sub_word_hits(self._selected_item)
            if hits:
                c1 = sel.get("start_char")
                c2 = sel.get("end_char")
                c3 = sel.get("drag_char_start", c1)
                c4 = sel.get("drag_char_end", c2)
                if c1 is not None and c2 is not None and c3 is not None and c4 is not None:
                    start_char = min(c1, c2, c3, c4)
                    end_char = max(c1, c2, c3, c4)
                    
                    seg = None
                    if self._task_data:
                        segs = self._task_data.get("segments", [])
                        seg_idx = sel.get("seg_idx")
                        if isinstance(seg_idx, int) and 0 <= seg_idx < len(segs):
                            seg = segs[seg_idx]
                    
                    text = seg.get("text") or "" if isinstance(seg, dict) else ""
                    
                    payload = {
                        "seg_idx": sel.get("seg_idx"),
                        "start_char": start_char,
                        "end_char": end_char,
                        "text": text
                    }
                    self.sub_text_selected.emit(payload)
            self._sub_text_selecting = False
            self._sub_text_sel_range = None
            self.update()
            return
        if self._dragging_item and self._dragging_item.get('type') == 'sub' and self._drag_mode == "resize":
            seg_idx = self._dragging_item.get("idx")
            if isinstance(seg_idx, int):
                self.sub_textbox_resized.emit({"seg_idx": seg_idx})
        if self._dragging_item is not None and self._drag_mode in ("move", "resize", "rotate", "zoom"):
            self.gesture_ended.emit()
        self._alt_dup_pending = False
        self._zoom_drag = None
        self._drag_mods = None
        if self._structure_dirty:
            self._structure_dirty = False
            self.structure_changed.emit()
        if getattr(self, "_multi_collapse", None) is not None:
            keep = self._multi_collapse
            self._multi_collapse = None
            self._multi_clear()
            self._selected_item = keep
            self._selected_rect = keep.get("rect")
            self.update()
        self._dragging_item = None
        self._drag_mode = None
        self._resize_handle = None
        self._rotate_origin = None
        self._snap_lines = []
        self._axis_lock = None
        self._drag_hud = None
        self._drag_origin_rect = None
        self._drag_accum = None
        self._sub_drag_base = None

    def _preview_target_rect(self):
        img_w, img_h = self._video_dims
        if img_w <= 0 or img_h <= 0:
            return QRectF(0, 0, 0, 0)
        scale = min(self.width() / img_w, self.height() / img_h)
        draw_w = img_w * scale
        draw_h = img_h * scale
        draw_x = (self.width() - draw_w) / 2
        draw_y = (self.height() - draw_h) / 2
        return self._apply_view(QRectF(draw_x, draw_y, draw_w, draw_h))

    def _get_sub_style_and_seg(self, item):
        if not item or item.get("type") != "sub":
            return None, None, None
        if not self._task_data:
            return None, None, None
        seg_idx = item.get("idx")
        segs = self._task_data.get("segments", [])
        if not isinstance(seg_idx, int) or seg_idx < 0 or seg_idx >= len(segs):
            return None, None, None
        seg = segs[seg_idx]
        role_id = seg.get("role", "default")
        from cutora.subtitle.stylepack import resolve_paint_style
        style = resolve_paint_style(self._task_data, role_id)
        return seg, style, role_id

    def _is_sub_adv_enabled(self, item):
        _, style, _ = self._get_sub_style_and_seg(item)
        if not isinstance(style, dict):
            return False
        return bool(style.get("advanced_textbox_enabled", False))

    def _get_sub_word_hits(self, item):
        seg_idx = item.get("idx") if isinstance(item, dict) else None
        for rec in reversed(self._hit_records or []):
            if rec.get("type") == "sub" and rec.get("idx") == seg_idx:
                hits = rec.get("word_hits")
                if isinstance(hits, list) and hits:
                    return hits
        try:
            from cutora.render.textfx.pipeline import word_hits_for
            hits = word_hits_for(seg_idx)
            if hits:
                return hits
        except Exception:
            pass
        return []

    def _find_word_hit(self, pos, item):
        hits = self._get_sub_word_hits(item)
        for h in hits:
            rect = h.get("rect")
            if rect is not None and rect.contains(pos):
                return h
        return None

    def _update_sub_text_sel_rects(self, item):
        if not self._sub_text_sel_range or not item:
            self._sub_text_sel_rects = []
            return
        hits = self._get_sub_word_hits(item)
        if not hits:
            self._sub_text_sel_rects = []
            return
            
        c1 = self._sub_text_sel_range.get("start_char")
        c2 = self._sub_text_sel_range.get("end_char")
        c3 = self._sub_text_sel_range.get("drag_char_start", c1)
        c4 = self._sub_text_sel_range.get("drag_char_end", c2)
        
        if c1 is None or c2 is None:
            self._sub_text_sel_rects = []
            return
            
        lo = min(c1, c2, c3, c4)
        hi = max(c1, c2, c3, c4)
        
        rects = []
        for h in hits:
            hc_start = h.get("char_start", -1)
            hc_end = h.get("char_end", -1)
            if hc_start is not None and hc_end is not None and hc_start >= lo and hc_end <= hi:
                r = h.get("rect")
                if r is not None:
                    rects.append(r)
        self._sub_text_sel_rects = rects

    # ═══════════════════════════════════════════════════════════════
    #  吸附系统（Canvas Snap System）
    # ═══════════════════════════════════════════════════════════════

    def _lock_axis(self, dx, dy, modifiers=None):
        """Q116：按住 Shift 锁定一个方向 —— 走得多的那一边留下，另一边归零。

        画面被裁到刚好铺满格子时，只要横着挪一点点就会露黑边；锁轴之后
        只上下动，怎么拖都不会露。锁住的方向上还会画一条参考线。
        """
        try:
            from PySide6.QtCore import Qt as _Qt
            shift = bool(modifiers is not None
                         and (modifiers & _Qt.KeyboardModifier.ShiftModifier))
        except Exception:
            shift = False
        if not shift:
            self._axis_lock = None
            return dx, dy
        if abs(dx) >= abs(dy):
            self._axis_lock = "x"
            return dx, 0.0
        self._axis_lock = "y"
        return 0.0, dy

    def _drag_cell_box(self):
        """正在拖的片段所在的分屏格子 (x, y, w, h)；没分屏就返回整块画布。"""
        cw, ch = float(self._video_dims[0]), float(self._video_dims[1])
        item = self._dragging_item or self._selected_item
        cell = (item or {}).get("cell") if isinstance(item, dict) else None
        if cell and len(cell) == 4:
            return (float(cell[0]), float(cell[1]), float(cell[2]), float(cell[3]))
        return (0.0, 0.0, cw, ch)

    def _calc_snap(self, rect, dx, dy):
        """移动模式下的吸附计算。
        检测元素矩形的 6 个锚点（left/center_x/right, top/center_y/bottom）
        与画布的 6 条参考线（中心 x/y + 四边缘）的距离，找到最近的吸附目标。
        返回修正后的 (dx, dy)，同时更新 self._snap_lines。
        """
        if getattr(self, "_snap_hold_off", False):
            self._snap_lines = []
            return dx, dy
        bx, by, cw, ch = self._drag_cell_box()
        thresh = self._snap_threshold
        snap_lines = []

        # 目标参考线（分屏时是本格子的边和中线，不是整块画布的）
        x_targets = [bx, bx + cw / 2.0, bx + cw]   # 左边缘, 水平中心, 右边缘
        y_targets = [by, by + ch / 2.0, by + ch]   # 上边缘, 垂直中心, 下边缘
        # Q125：别的文本框 / 图片 / 视频片段也能吸 —— 两条字对齐到同一条中线
        ox, oy = self._other_snap_targets()
        x_targets.extend(ox)
        y_targets.extend(oy)

        # 元素平移后的预测矩形
        moved = QRectF(rect.x() + dx, rect.y() + dy, rect.width(), rect.height())

        # 元素锚点
        x_anchors = [moved.left(), moved.center().x(), moved.right()]
        y_anchors = [moved.top(), moved.center().y(), moved.bottom()]

        # X 轴吸附
        best_x_snap = None
        best_x_dist = thresh + 1
        for anchor in x_anchors:
            for target in x_targets:
                dist = abs(anchor - target)
                if dist < best_x_dist:
                    best_x_dist = dist
                    best_x_snap = (anchor, target)

        if best_x_snap is not None and best_x_dist <= thresh:
            correction = best_x_snap[1] - best_x_snap[0]
            dx += correction
            snap_lines.append({'axis': 'x', 'pos': best_x_snap[1]})

        # Y 轴吸附
        best_y_snap = None
        best_y_dist = thresh + 1
        for anchor in y_anchors:
            for target in y_targets:
                dist = abs(anchor - target)
                if dist < best_y_dist:
                    best_y_dist = dist
                    best_y_snap = (anchor, target)

        if best_y_snap is not None and best_y_dist <= thresh:
            correction = best_y_snap[1] - best_y_snap[0]
            dy += correction
            snap_lines.append({'axis': 'y', 'pos': best_y_snap[1]})

        # Q116：锁了方向就把那条轴线画出来，让人看得见自己在沿哪条线走
        lock = getattr(self, "_axis_lock", None)
        if lock == "x":
            snap_lines.append({'axis': 'y', 'pos': moved.center().y()})
        elif lock == "y":
            snap_lines.append({'axis': 'x', 'pos': moved.center().x()})

        self._snap_lines = snap_lines
        return dx, dy

    def _calc_snap_resize(self, dx, dy, handle):
        """缩放模式下的吸附计算。
        按住 N 时和移动吸附一样，这一下不吸。
        根据 handle 方向，检测受影响的边坐标与画布边缘/中心的距离。
        返回修正后的 (dx, dy)，同时更新 self._snap_lines。
        """
        if getattr(self, "_snap_hold_off", False):
            self._snap_lines = []
            return dx, dy
        rect = self._selected_rect
        if rect is None:
            self._snap_lines = []
            return dx, dy

        cw, ch = float(self._video_dims[0]), float(self._video_dims[1])
        thresh = self._snap_threshold
        snap_lines = []
        x_targets = [0.0, cw / 2.0, cw]
        y_targets = [0.0, ch / 2.0, ch]

        # 根据 handle 确定受影响的边的新位置
        # X 轴
        if handle in ('w', 'nw', 'sw'):
            new_edge = rect.left() + dx
            best_dist = thresh + 1
            best_target = None
            for t in x_targets:
                d = abs(new_edge - t)
                if d < best_dist:
                    best_dist = d
                    best_target = t
            if best_target is not None and best_dist <= thresh:
                dx += (best_target - new_edge)
                snap_lines.append({'axis': 'x', 'pos': best_target})
        elif handle in ('e', 'ne', 'se'):
            new_edge = rect.right() + dx
            best_dist = thresh + 1
            best_target = None
            for t in x_targets:
                d = abs(new_edge - t)
                if d < best_dist:
                    best_dist = d
                    best_target = t
            if best_target is not None and best_dist <= thresh:
                dx += (best_target - new_edge)
                snap_lines.append({'axis': 'x', 'pos': best_target})

        # Y 轴
        if handle in ('n', 'nw', 'ne'):
            new_edge = rect.top() + dy
            best_dist = thresh + 1
            best_target = None
            for t in y_targets:
                d = abs(new_edge - t)
                if d < best_dist:
                    best_dist = d
                    best_target = t
            if best_target is not None and best_dist <= thresh:
                dy += (best_target - new_edge)
                snap_lines.append({'axis': 'y', 'pos': best_target})
        elif handle in ('s', 'sw', 'se'):
            new_edge = rect.bottom() + dy
            best_dist = thresh + 1
            best_target = None
            for t in y_targets:
                d = abs(new_edge - t)
                if d < best_dist:
                    best_dist = d
                    best_target = t
            if best_target is not None and best_dist <= thresh:
                dy += (best_target - new_edge)
                snap_lines.append({'axis': 'y', 'pos': best_target})

        self._snap_lines = snap_lines
        return dx, dy

    def _draw_snap_guides(self, painter):
        """绘制当前激活的吸附参考线（仅在拖动过程中可见）。"""
        if not self._snap_lines:
            return
        cw, ch = float(self._video_dims[0]), float(self._video_dims[1])
        painter.save()
        try:
            guide_color = QColor(0, 220, 255, 200)
            pen = QPen(guide_color, 1.8, Qt.PenStyle.DashLine)
            pen.setDashPattern([8, 5])
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            for snap in self._snap_lines:
                if snap['axis'] == 'x':
                    x = snap['pos']
                    painter.drawLine(QPointF(x, 0), QPointF(x, ch))
                else:
                    y = snap['pos']
                    painter.drawLine(QPointF(0, y), QPointF(cw, y))
            # 如果同时存在 x 和 y 吸附线，在交叉点绘制十字标记
            x_snaps = [s['pos'] for s in self._snap_lines if s['axis'] == 'x']
            y_snaps = [s['pos'] for s in self._snap_lines if s['axis'] == 'y']
            if x_snaps and y_snaps:
                cross_pen = QPen(QColor(0, 255, 200, 240), 2.5, Qt.PenStyle.SolidLine)
                painter.setPen(cross_pen)
                cross_size = 12.0
                for cx in x_snaps:
                    for cy in y_snaps:
                        painter.drawLine(QPointF(cx - cross_size, cy), QPointF(cx + cross_size, cy))
                        painter.drawLine(QPointF(cx, cy - cross_size), QPointF(cx, cy + cross_size))
        finally:
            painter.restore()

    def _draw_selection_outline(self, painter):
        if self._selected_item is None or self._selected_rect is None:
            return
        item_type = self._selected_item.get('type')
        # 支持字幕整体(sub)、分离字块(sub_span)、覆层(overlay)、贴纸(sticker)、视频片段(video_clip)
        if item_type not in ('overlay', 'video_clip', 'sub', 'sub_span', 'sticker'):
            return
        
        is_adv = self._is_sub_adv_enabled(self._selected_item)
        rect = self._selected_rect
        if item_type in ('overlay', 'video_clip'):
            # Q152：位置打了关键帧时对象每帧在动，框跟着这一帧刚画出来的位置走
            try:
                live = self._live_record(self._selected_item).get("rect")
                if live is not None:
                    rect = live
            except Exception:
                pass
        painter.save()
        try:
            # 无论什么模式的字幕，只要有字块选中高亮，都画出蓝色蒙版底层
            if item_type == "sub" and self._sub_text_sel_rects:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(90, 140, 255, 120))
                for srect in self._sub_text_sel_rects:
                    painter.drawRect(srect)
                    
            # 如果是普通字幕（不是高级框），且没有别的东西要画，则不画虚线外框和手柄，直接退出
            if item_type == 'sub' and not is_adv:
                return
                
            # 分离字块用不同颜色的边框标识，与全局/字幕区分
            if item_type == 'sub_span':
                pen = QPen(QColor(255, 160, 60, 230), 2.4, Qt.PenStyle.DashLine)
            else:
                pen = QPen(QColor(120, 180, 255, 230), 2.4, Qt.PenStyle.DashLine)
            pen.setDashPattern([6, 4])
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(rect)
            
            for hrect in self._handle_rects_for(rect, self._selected_item).values():
                if item_type == 'sub_span':
                    painter.setPen(QPen(QColor(200, 120, 30, 255), 1.4))
                    painter.setBrush(QColor(255, 220, 180, 230))
                else:
                    painter.setPen(QPen(QColor(60, 120, 220, 255), 1.4))
                    painter.setBrush(QColor(220, 235, 255, 230))
                from cutora.ui.theme.tokens import RADIUS_SM
                painter.drawRoundedRect(hrect, RADIUS_SM, RADIUS_SM)
        finally:
            painter.restore()

    def _is_auto_height_text(self, item) -> bool:
        """高度跟着文字走的文本层：上下边没有东西可拖。"""
        if not isinstance(item, dict) or item.get("type") != "overlay":
            return False
        if item.get("list") != "text_overlays":
            return False
        ov = self._record_data(item)
        return isinstance(ov, dict) and str(ov.get("fit_mode") or "") == "auto_height"

    def _handle_rects_for(self, rect, item):
        """这个对象该画哪些手柄。Q129：auto_height 的文本不画上下边柄 ——
        以前八个都画，用户去拖上下边，什么都不发生，还弹一个「高度自动」。"""
        kind = item.get("type") if isinstance(item, dict) else None
        rects = self._resize_handle_rects(
            rect, with_rotate=kind in ("overlay", "video_clip", "sticker"))
        if self._is_auto_height_text(item):
            rects.pop("n", None)
            rects.pop("s", None)
        return rects

    def _resize_handle_rects(self, rect, with_rotate=False):
        size = max(18.0, min(32.0, rect.width() * 0.08))
        hs = size / 2.0
        cx = rect.center().x()
        cy = rect.center().y()
        rects = {
            "nw": QRectF(rect.left() - hs, rect.top() - hs, size, size),
            "n": QRectF(cx - hs, rect.top() - hs, size, size),
            "ne": QRectF(rect.right() - hs, rect.top() - hs, size, size),
            "e": QRectF(rect.right() - hs, cy - hs, size, size),
            "se": QRectF(rect.right() - hs, rect.bottom() - hs, size, size),
            "s": QRectF(cx - hs, rect.bottom() - hs, size, size),
            "sw": QRectF(rect.left() - hs, rect.bottom() - hs, size, size),
            "w": QRectF(rect.left() - hs, cy - hs, size, size),
        }
        if with_rotate:
            # Same rounded square as the other handles, sitting above the top edge.
            pad = 8.0
            rects["rot"] = QRectF(cx - hs, rect.top() - hs - pad - size, size, size)
        return rects

    def _hit_resize_handle(self, pos, rect, item=None):
        for key, hrect in self._handle_rects_for(rect, item).items():
            if hrect.contains(pos):
                return key
        return None

    def _aspect_hosts(self, item):
        data = self._task_data or {}
        kind = item.get("type")
        if kind == "overlay":
            try:
                return data[item.get("list")][item["idx"]], None
            except Exception:
                return None, None
        if kind == "sticker":
            try:
                ov = data["image_overlays"][item["ov_idx"]]
                return (ov.get("elements") or [])[item["el_idx"]], ov
            except Exception:
                return None, None
        if kind == "video_clip":
            return item.get("clip"), None
        return None, None

    def _host_float(self, host, path, fallback):
        from cutora.render.keyframe import local_time, path_enabled, value_at
        fb = float(fallback)
        if host is None:
            return fb
        if path_enabled(host, path):
            return float(value_at(host, path, local_time(host, self._playhead_t()), fb))
        raw = host.get(path, fb) if isinstance(host, dict) else getattr(host, path, fb)
        try:
            return float(raw)
        except (TypeError, ValueError):
            return fb

    def _begin_rotate(self, item, video_x, video_y):
        rect = self._selected_rect
        host, _parent = self._aspect_hosts(item)
        if rect is None or host is None:
            self._rotate_origin = None
            return
        import math
        if isinstance(host, dict):
            fb = host.get("rotation", 0.0)
        else:
            fb = getattr(host, "rotation", 0.0)
        ang = math.degrees(math.atan2(
            video_y - rect.center().y(), video_x - rect.center().x()))
        self._rotate_origin = (ang, self._host_float(host, "rotation", fb))

    def _apply_rotate(self, item, video_x, video_y):
        origin = self._rotate_origin
        rect = self._selected_rect
        host, _parent = self._aspect_hosts(item)
        if not origin or rect is None or host is None:
            return
        import math
        ang = math.degrees(math.atan2(
            video_y - rect.center().y(), video_x - rect.center().x()))
        delta = ang - origin[0]
        while delta > 180.0:
            delta -= 360.0
        while delta < -180.0:
            delta += 360.0
        new_rot = origin[1] + delta
        mods = getattr(self, "_drag_mods", None)
        if mods is None:
            from PySide6.QtWidgets import QApplication as _QApp
            mods = _QApp.keyboardModifiers()
        if mods & Qt.KeyboardModifier.ShiftModifier:
            new_rot = round(new_rot / 15.0) * 15.0        # Q145：Shift = 15° 一档
            self._drag_hud = "旋转 %d°" % int(round(new_rot))
        if isinstance(host, dict):
            self._commit_host_param(
                host, "rotation", new_rot,
                lambda v, _h=host: _h.__setitem__("rotation", v))
        else:
            self._commit_host_param(
                host, "rotation", new_rot,
                lambda v, _h=host: setattr(_h, "rotation", v))

    # ------------------------------------------------------------------
    # Q125：文本框手柄（文本工具改造第二批）
    # ------------------------------------------------------------------
    TEXT_FONT_MIN = 8.0
    TEXT_FONT_MAX = 600.0

    def _apply_text_resize(self, ov, rect, dx, dy, handle, modifiers=None):
        """文本框的八个手柄，按所有排版软件的约定：

        - 角柄：等比缩放整段文字 —— 字号跟着变，框随字走；
        - 左右边柄：只改框宽，字号不变，文字重新折行；
        - 上下边柄：高度自动的框不理它（高度由文字决定），固定框改框高；
        - 按住 Shift 反过来：角柄变成只改框、不动字号。

        以前八个手柄都只改框的大小、字号一动不动，而且默认锁比例 ——
        拖右边那个手柄，高度会跟着一起缩。
        """
        if modifiers is None:
            modifiers = Qt.KeyboardModifier.NoModifier
        shift = bool(modifiers & Qt.KeyboardModifier.ShiftModifier)
        handle = str(handle or "")
        corner = handle in ("nw", "ne", "sw", "se")
        auto_h = str(ov.get("fit_mode") or "") == "auto_height"
        min_w = 24.0

        def _put(new_rect):
            self._commit_host_param(
                ov, "x", float(new_rect.x()),
                lambda v, _ov=ov: _ov.__setitem__("x", v))
            self._commit_host_param(
                ov, "y", float(new_rect.y()),
                lambda v, _ov=ov: _ov.__setitem__("y", v))
            ov["w"] = float(max(min_w, new_rect.width()))
            ov["h"] = float(max(8.0, new_rect.height()))
            self._selected_rect = QRectF(new_rect)

        if corner and not shift:
            # 等比：对角那个点钉住，缩放比例按宽算，字号乘同一个比例
            scaled = _rect_after_resize(rect, dx, dy, handle, min_w)
            if rect.width() <= 0.5:
                return
            ratio = float(scaled.width()) / float(rect.width())
            try:
                em = float(ov.get("fontsize") or 40.0)
            except (TypeError, ValueError):
                em = 40.0
            new_em = max(self.TEXT_FONT_MIN, min(self.TEXT_FONT_MAX, em * ratio))
            ratio = new_em / em if em > 0 else 1.0
            new_w = max(min_w, rect.width() * ratio)
            new_h = rect.height() * ratio
            # 按被拖的角决定钉住哪个角
            if handle == "se":
                left, top = rect.left(), rect.top()
            elif handle == "nw":
                left, top = rect.right() - new_w, rect.bottom() - new_h
            elif handle == "ne":
                left, top = rect.left(), rect.bottom() - new_h
            else:  # sw
                left, top = rect.right() - new_w, rect.top()
            # 走 stylepack 的写入口（和属性面板同一条路；有 fontsize_pct 的样式会一起更新）
            from cutora.subtitle.stylepack import set_fontsize_px
            set_fontsize_px(ov, new_em, int(self._video_dims[0]), int(self._video_dims[1]))
            _put(QRectF(left, top, new_w, new_h))
            self._drag_hud = ("字号 %d px" % int(round(new_em)), "框宽 " + self._width_pct(new_w))
            return

        new_rect = QRectF(rect)
        if handle in ("w", "nw", "sw"):
            new_rect.setLeft(min(new_rect.right() - min_w, new_rect.left() + dx))
        if handle in ("e", "ne", "se"):
            new_rect.setRight(max(new_rect.left() + min_w, new_rect.right() + dx))
        if handle in ("n", "nw", "ne") and (shift or not auto_h):
            new_rect.setTop(min(new_rect.bottom() - 8.0, new_rect.top() + dy))
        if handle in ("s", "sw", "se") and (shift or not auto_h):
            new_rect.setBottom(max(new_rect.top() + 8.0, new_rect.bottom() + dy))
        if handle in ("n", "s") and auto_h and not shift:
            # 高度由文字决定，上下边柄没东西可拖（Q129 起根本不画这两个柄）
            self._drag_hud = ("高度跟着文字走", "拖左右边改框宽")
            return
        _put(new_rect)
        if handle in ("n", "s"):
            self._drag_hud = ("框高 %d px" % int(round(new_rect.height())),
                              "框宽 " + self._width_pct(new_rect.width()))
        else:
            self._drag_hud = ("框宽 " + self._width_pct(new_rect.width()),
                              "%d px" % int(round(new_rect.width())))

    def _width_pct(self, w) -> str:
        cw = float(self._video_dims[0] or 1)
        return "%d%%" % int(round(100.0 * float(w) / cw))

    def _draw_drag_hud(self, painter):
        """拖手柄时在框上方显示「字号 · 框宽占画布百分比」。松手就没。"""
        hud = getattr(self, "_drag_hud", None)
        rect = self._selected_rect
        from cutora.ui import canvas_mask as _ck
        mask_hud = _ck.hud_text(self)
        if mask_hud:
            hud = (mask_hud, "")
            rect = rect or QRectF(0, 0, float(self._video_dims[0]), float(min(self._video_dims[1], 120)))
        if not hud or rect is None or (self._dragging_item is None and not mask_hud):
            return
        text = "%s  ·  %s" % (hud[0], hud[1]) if hud[1] else str(hud[0])
        painter.save()
        try:
            f = painter.font()
            f.setPixelSize(max(12, int(min(self._video_dims) * 0.02)))
            f.setBold(True)
            painter.setFont(f)
            fm = painter.fontMetrics()
            pad = 8
            tw = fm.horizontalAdvance(text) + pad * 2
            th = fm.height() + pad
            x = rect.center().x() - tw / 2.0
            y = rect.top() - th - 10
            if y < 4:
                y = rect.bottom() + 10
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(20, 22, 28, 220))
            from cutora.ui.theme.tokens import RADIUS_CTL
            painter.drawRoundedRect(QRectF(x, y, tw, th), RADIUS_CTL, RADIUS_CTL)
            painter.setPen(QColor(255, 224, 130))
            painter.drawText(QRectF(x, y, tw, th), Qt.AlignmentFlag.AlignCenter, text)
        finally:
            painter.restore()

    def _other_snap_targets(self):
        """别的文本框 / 图片 / 视频片段的边和中线，拖的时候能吸过去。"""
        xs, ys = [], []
        me = self._dragging_item or self._selected_item
        for rec in self._hit_records or []:
            if not isinstance(rec, dict):
                continue
            if rec.get("type") not in ("overlay", "sticker", "video_clip"):
                continue
            if me is not None and self._same_item(rec, me):
                continue
            r = rec.get("rect")
            if r is None or r.width() <= 1 or r.height() <= 1:
                continue
            xs.extend((r.left(), r.center().x(), r.right()))
            ys.extend((r.top(), r.center().y(), r.bottom()))
        return xs, ys

    @staticmethod
    def _same_item(a, b) -> bool:
        if a is b:
            return True
        if not isinstance(a, dict) or not isinstance(b, dict):
            return False
        if a.get("type") != b.get("type"):
            return False
        if a.get("type") == "overlay":
            return a.get("list") == b.get("list") and a.get("idx") == b.get("idx")
        if a.get("type") == "sticker":
            return (a.get("ov_idx") == b.get("ov_idx")
                    and a.get("el_idx") == b.get("el_idx"))
        if a.get("type") == "video_clip":
            return a.get("clip") is b.get("clip")
        return False

    def _apply_resize(self, item, dx, dy, handle, modifiers=None):
        rect = self._selected_rect
        if rect is None:
            return
        if modifiers is None:
            modifiers = Qt.KeyboardModifier.NoModifier
        new_rect = QRectF(rect)
        min_size = 20.0
        # Q145：Alt = 从中心缩放（对边同时动 → 位移加倍再把中心放回原处）
        from_center = (bool(modifiers & Qt.KeyboardModifier.AltModifier)
                       and item.get("type") in ("overlay", "sticker", "video_clip")
                       and not (item.get("type") == "overlay"
                                and item.get("list") == "text_overlays"))
        if from_center:
            dx, dy = dx * 2.0, dy * 2.0
        if handle in ("w", "nw", "sw"):
            new_rect.setLeft(new_rect.left() + dx)
        if handle in ("e", "ne", "se"):
            new_rect.setRight(new_rect.right() + dx)
        if handle in ("n", "nw", "ne"):
            new_rect.setTop(new_rect.top() + dy)
        if handle in ("s", "sw", "se"):
            new_rect.setBottom(new_rect.bottom() + dy)
        if new_rect.width() < min_size:
            new_rect.setWidth(min_size)
        if new_rect.height() < min_size:
            new_rect.setHeight(min_size)

        host, parent = self._aspect_hosts(item)
        locked = False
        if host is not None and item.get("type") in ("overlay", "sticker", "video_clip"):
            from cutora.model.project_schema import lock_aspect_of
            locked = lock_aspect_of(host, parent)
            if modifiers & Qt.KeyboardModifier.ShiftModifier:
                locked = not locked
        if locked and item.get("type") in ("overlay", "sticker", "video_clip"):
            new_rect = _rect_after_resize(rect, dx, dy, handle, min_size)
        if from_center:
            new_rect.moveCenter(rect.center())

        if item.get('type') == 'overlay':
            list_name = item.get('list')
            if list_name and list_name in self._task_data:
                ov = self._task_data[list_name][item['idx']]
                if list_name == "text_overlays" and str(ov.get("type") or "text") == "text":
                    # Q125：文本框按排版软件的约定 —— 角柄缩字号，边柄改折行宽
                    self._apply_text_resize(ov, rect, dx, dy, handle, modifiers)
                    return
                self._commit_host_param(
                    ov, "x", float(new_rect.x()),
                    lambda v, _ov=ov: _ov.__setitem__("x", v))
                self._commit_host_param(
                    ov, "y", float(new_rect.y()),
                    lambda v, _ov=ov: _ov.__setitem__("y", v))
                ov['w'] = float(new_rect.width())
                ov['h'] = float(new_rect.height())
                self._selected_rect = new_rect
        elif item.get('type') == 'sticker':
            ov_idx = item.get('ov_idx')
            el_idx = item.get('el_idx')
            if isinstance(ov_idx, int) and isinstance(el_idx, int):
                image_ovs = self._task_data.get('image_overlays', [])
                if 0 <= ov_idx < len(image_ovs):
                    elements = image_ovs[ov_idx].get('elements', [])
                    if 0 <= el_idx < len(elements):
                        el = elements[el_idx]
                        ov = image_ovs[ov_idx]
                        ov_scale = float(ov.get('scale', 1.0))
                        if ov_scale == 0: ov_scale = 1.0
                        new_w = float(new_rect.width()) / ov_scale
                        new_h = float(new_rect.height()) / ov_scale
                        cx_s = float(new_rect.x()) + float(new_rect.width()) / 2.0
                        cy_s = float(new_rect.y()) + float(new_rect.height()) / 2.0
                        new_x = cx_s - new_w / 2.0 - float(ov.get('x', 0))
                        new_y = cy_s - new_h / 2.0 - float(ov.get('y', 0))
                        self._commit_host_param(
                            el, "x", new_x,
                            lambda v, _el=el: _el.__setitem__("x", v))
                        self._commit_host_param(
                            el, "y", new_y,
                            lambda v, _el=el: _el.__setitem__("y", v))
                        el['w'] = new_w
                        el['h'] = new_h
                        self._selected_rect = new_rect
        elif item.get('type') == 'video_clip':
            clip = item.get('clip')
            if clip is None:
                return
            old_rect = rect
            if old_rect.width() <= 0 or old_rect.height() <= 0:
                return
            scale_w = new_rect.width() / old_rect.width()
            scale_h = new_rect.height() / old_rect.height()
            scale_factor = max(scale_w, scale_h)
            if not locked:
                scale_factor = scale_w
            from cutora.render.keyframe import local_time, path_enabled, value_at
            t_local = local_time(clip, self._playhead_t())
            old_scale = float(getattr(clip, "scale", 1.0))
            if path_enabled(clip, "scale"):
                old_scale = float(value_at(clip, "scale", t_local, old_scale))
            self._commit_host_param(
                clip, "scale", old_scale * scale_factor,
                lambda v, _c=clip: setattr(_c, "scale", v))
            if not locked and abs(scale_w) > 1e-6:
                old_sy = float(getattr(clip, "scale_y", 1.0) or 1.0)
                setattr(clip, "scale_y", old_sy * (scale_h / scale_w))
            delta_c = new_rect.center() - old_rect.center()
            self._nudge_host_param(
                clip, "x", delta_c.x(), getattr(clip, "x", 0.0),
                lambda v, _c=clip: setattr(_c, "x", v))
            self._nudge_host_param(
                clip, "y", delta_c.y(), getattr(clip, "y", 0.0),
                lambda v, _c=clip: setattr(_c, "y", v))
            self._selected_rect = new_rect
        elif item.get('type') == 'sub':
            seg, style, _ = self._get_sub_style_and_seg(item)
            if seg is None or not isinstance(style, dict):
                return
            if not bool(style.get("advanced_textbox_enabled", False)):
                return
            if new_rect.width() < 80.0:
                new_rect.setWidth(80.0)
            if new_rect.height() < 40.0:
                new_rect.setHeight(40.0)
            style["advanced_textbox_x"] = float(new_rect.x())
            style["advanced_textbox_y"] = float(new_rect.y())
            style["advanced_textbox_w"] = float(new_rect.width())
            style["advanced_textbox_h"] = float(new_rect.height())
            self._selected_rect = new_rect
        elif item.get('type') == 'sub_span':
            # 分离字块的独立缩放：直接修改对应 adv_block 的绝对坐标
            seg_idx = item.get('seg_idx')
            span_idx = item.get('span_idx')
            segs = self._task_data.get('segments', [])
            if isinstance(seg_idx, int) and 0 <= seg_idx < len(segs):
                seg = segs[seg_idx]
                b_idx = None
                adv_spans = seg.get("adv_rich_spans", [])
                if isinstance(span_idx, int) and 0 <= span_idx < len(adv_spans):
                    b_idx = adv_spans[span_idx].get("_block_idx")
                if isinstance(b_idx, int):
                    adv_blocks = seg.get("adv_blocks", [])
                    if 0 <= b_idx < len(adv_blocks):
                        ov = adv_blocks[b_idx].get("overrides", {})
                        if new_rect.width() < 40.0:
                            new_rect.setWidth(40.0)
                        if new_rect.height() < 30.0:
                            new_rect.setHeight(30.0)
                        ov["advanced_textbox_x"] = float(new_rect.x())
                        ov["advanced_textbox_y"] = float(new_rect.y())
                        ov["advanced_textbox_w"] = float(new_rect.width())
                        ov["advanced_textbox_h"] = float(new_rect.height())
                        ov["advanced_textbox_enabled"] = True

                        adv_blocks[b_idx]["overrides"] = ov
                        # 同步到 span 用于即时视觉反馈
                        if isinstance(span_idx, int) and 0 <= span_idx < len(adv_spans):
                            adv_spans[span_idx]["advanced_textbox_x"] = ov["advanced_textbox_x"]
                            adv_spans[span_idx]["advanced_textbox_y"] = ov["advanced_textbox_y"]
                            adv_spans[span_idx]["advanced_textbox_w"] = ov["advanced_textbox_w"]
                            adv_spans[span_idx]["advanced_textbox_h"] = ov["advanced_textbox_h"]
            self._selected_rect = new_rect
