# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""剪气口（DaVinci Resolve Studio / Descript 级全屏智能停顿裁剪、全局+局部双视界观察与无损自检工作台）。"""
from __future__ import annotations

import math
import os
import tempfile

from PySide6.QtCore import QPointF, QRectF, Qt, QThread, QUrl, Signal
from PySide6.QtGui import QColor, QFont, QKeySequence, QPainter, QPen, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QSplitterHandle,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from cutora.audio.silence_cut import (
    UNIT_LABELS,
    UNITS,
    AudioCache,
    SilenceParams,
    analyze,
    apply_plan,
    collect_words,
    envelope,
    plan_task,
    preview_wave,
    timeline_end,
    timeline_fps,
    verify_cut,
    write_wav,
)


class _HoverThinHandle(QSplitterHandle):
    """平时完全隐藏、鼠标悬停或拖拽时显现极细微光线的自由分割条手柄。"""

    def __init__(self, orientation: Qt.Orientation, parent: QSplitter):
        super().__init__(orientation, parent)
        self._hover = False
        self._pressed = False
        self.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        if orientation == Qt.Orientation.Horizontal:
            self.setCursor(Qt.CursorShape.SplitHCursor)
        else:
            self.setCursor(Qt.CursorShape.SplitVCursor)

    def enterEvent(self, event) -> None:
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:
        self._pressed = True
        self.update()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        self._pressed = False
        self.update()
        super().mouseReleaseEvent(event)

    def paintEvent(self, _event) -> None:
        if not self._hover and not self._pressed:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w = self.width()
        h = self.height()
        line_color = QColor("#F0B72F") if self._pressed else QColor(88, 166, 255, 200)
        pen = QPen(line_color, 1.5)
        p.setPen(pen)
        if self.orientation() == Qt.Orientation.Horizontal:
            cx = w * 0.5
            p.drawLine(QPointF(cx, 2), QPointF(cx, h - 2))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(line_color)
            p.drawRoundedRect(QRectF(cx - 1.5, max(4.0, h * 0.5 - 14.0), 3.0, min(28.0, h - 8.0)), 3, 3)
        else:
            cy = h * 0.5
            p.drawLine(QPointF(2, cy), QPointF(w - 2, cy))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(line_color)
            p.drawRoundedRect(QRectF(max(4.0, w * 0.5 - 14.0), cy - 1.5, min(28.0, w - 8.0), 3.0), 3, 3)
        p.end()


class HoverThinSplitter(QSplitter):
    """不设死板的折叠限制、平时隐藏分割线、悬停显示细线可自由拖拽的分割器。"""

    def __init__(self, orientation: Qt.Orientation = Qt.Orientation.Horizontal, parent=None):
        super().__init__(orientation, parent)
        self.setHandleWidth(8)
        self.setChildrenCollapsible(True)
        self.setOpaqueResize(True)
        self.setStyleSheet("QSplitter::handle { background: transparent; border: none; }")

    def createHandle(self) -> QSplitterHandle:
        return _HoverThinHandle(self.orientation(), self)


class SilenceMacroOverview(QWidget):
    """整体全局时间线鸟瞰图：显示整条视频的全长波形、全部气口分布、当前选中游标与剪前/剪后时长压缩比。"""

    cut_clicked = Signal(int)  # 点击某处气口时发射其在当前任务中的 cut 下标

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(56)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMouseTracking(True)
        self._bins: list[float] = []
        self._duration: float = 0.0
        self._cuts: list[dict] = []
        self._flags: list[dict] = []
        self._active_cut_id: str = ""
        self._task_label: str = ""

    def set_task_overview(self, sig, duration: float, cuts: list[dict], flags: list[dict], active_cut_id: str = "", task_label: str = "") -> None:
        self._duration = max(0.01, float(duration or 0.0))
        self._cuts = list(cuts or [])
        self._flags = list(flags or [])
        self._active_cut_id = str(active_cut_id or "")
        self._task_label = str(task_label or "")
        if sig is not None and len(sig) > 0 and self._duration > 0.01:
            self._bins = envelope(sig, 0.0, self._duration, bins=240)
        else:
            self._bins = []
        self.update()

    def clear_overview(self) -> None:
        self._bins = []
        self._duration = 0.0
        self._cuts = []
        self._flags = []
        self._active_cut_id = ""
        self._task_label = ""
        self.update()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._duration > 0.01 and self._cuts:
            w = max(1, self.width())
            t_click = (event.position().x() / float(w)) * self._duration
            best_i = -1
            best_dist = 1e9
            for i, c in enumerate(self._cuts):
                p0 = float(c.get("pause_start") or c.get("start") or 0.0)
                p1 = float(c.get("pause_end") or c.get("end") or p0)
                if p0 <= t_click <= p1:
                    best_i = i
                    break
                mid = 0.5 * (p0 + p1)
                d = abs(t_click - mid)
                if d < best_dist:
                    best_dist = d
                    best_i = i
            if best_i >= 0:
                self.cut_clicked.emit(best_i)
        super().mousePressEvent(event)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w = self.width()
        h = self.height()
        p.fillRect(0, 0, w, h, QColor("#12151C"))
        p.setPen(QPen(QColor("#262B36"), 1))
        p.drawRoundedRect(QRectF(0.5, 0.5, w - 1, h - 1), 5, 5)

        if not self._bins or self._duration <= 0.02:
            p.setPen(QColor("#7D8590"))
            p.drawText(
                self.rect(),
                Qt.AlignmentFlag.AlignCenter,
                "【整体全局视图】完成智能分析后，此处将显示整条视频的全长声波鸟瞰图与全部气口分布（点击任意气口可快速定位）",
            )
            p.end()
            return

        def _x(t: float) -> int:
            return int(round(max(0.0, min(1.0, t / self._duration)) * (w - 16))) + 8

        top_pad = 18
        bot_pad = 6
        wave_h = max(16, h - top_pad - bot_pad)
        mid_y = top_pad + wave_h * 0.5

        # 1. 绘制保护提示区间 (黄色半透明)
        for fl in self._flags:
            x0 = _x(float(fl.get("start") or 0.0))
            x1 = _x(float(fl.get("end") or 0.0))
            p.fillRect(x0, top_pad, max(2, x1 - x0), wave_h, QColor(229, 169, 60, 45))

        # 2. 绘制所有可剪气口区间 (勾选=红色，未勾选=暗灰)
        active_rect = None
        for c in self._cuts:
            c0 = _x(float(c.get("start") or 0.0))
            c1 = _x(float(c.get("end") or 0.0))
            bw = max(2, c1 - c0)
            enabled = bool(c.get("enabled", True))
            col = QColor(229, 83, 75, 110) if enabled else QColor(110, 118, 129, 65)
            p.fillRect(c0, top_pad, bw, wave_h, col)
            if str(c.get("id") or "") == self._active_cut_id and self._active_cut_id:
                active_rect = QRectF(c0 - 2, top_pad - 2, bw + 4, wave_h + 4)

        # 3. 绘制全片缩略声波
        n = len(self._bins)
        avail_w = max(1.0, float(w - 16))
        bar_w = max(1.0, avail_w / float(n))
        p.setPen(Qt.PenStyle.NoPen)
        for i, val in enumerate(self._bins):
            bx = int(8 + i * bar_w)
            bh = max(2, int(min(1.0, val * 1.75) * (wave_h - 4)))
            p.setBrush(QColor("#388BFD"))
            p.drawRect(bx, int(mid_y - bh * 0.5), max(1, int(bar_w - 0.5)), bh)

        # 4. 绘制当前选中气口的高亮金框
        if active_rect is not None:
            p.setPen(QPen(QColor("#F0B72F"), 2.0))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(active_rect, 3, 3)

        # 5. 顶部信息栏：整体时长对比与气口统计
        removed_s = sum(float(c.get("remove_s") or 0.0) for c in self._cuts if c.get("enabled", True))
        after_s = max(0.0, self._duration - removed_s)
        pct = (removed_s / self._duration * 100.0) if self._duration > 0 else 0.0
        enabled_cnt = sum(1 for c in self._cuts if c.get("enabled", True))
        header_txt = (
            f"🌐 全局声波鸟瞰  {self._task_label}  |  "
            f"原片总长: {self._duration:.2f}s  ➔  剪后预计: {after_s:.2f}s  "
            f"（共剪除 {enabled_cnt}/{len(self._cuts)} 处停顿，节省 {removed_s:.2f}s / {pct:.1f}%）"
        )
        f = p.font()
        f.setPointSize(9)
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor("#C9D1D9"))
        p.drawText(QRectF(10, 2, w - 20, 15), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, header_txt)
        p.end()


class SilenceWavePreview(QWidget):
    """局部细节切点显微镜：显示选中气口前后的高倍波形、前后台词气泡、噪底线、句首句尾保护区，并支持鼠标直接拖拽红线微调切点。"""

    cut_handles_adjusted = Signal(float, float)  # (new_start, new_end)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(110)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setMouseTracking(True)
        self._sig = None
        self._cut: dict | None = None
        self._pad: float = 1.5
        self._bins: list[float] = []
        self._win_start = 0.0
        self._win_end = 1.0
        self._pause_start = 0.0
        self._pause_end = 0.0
        self._cut_start = 0.0
        self._cut_end = 0.0
        self._prev_word = ""
        self._next_word = ""
        self._dragging: str = ""  # "left" | "right" | ""

    def set_zoom_pad(self, pad: float) -> None:
        self._pad = max(0.3, min(8.0, float(pad)))
        if self._sig is not None and self._cut is not None:
            self.set_cut_view(self._sig, self._cut, pad=self._pad)

    def set_cut_view(self, sig, cut: dict, pad: float | None = None) -> None:
        self._sig = sig
        self._cut = cut
        if pad is not None:
            self._pad = max(0.3, min(8.0, float(pad)))
        if sig is None or not cut:
            self._bins = []
            self.update()
            return
        p0 = float(cut.get("pause_start") or cut.get("start") or 0.0)
        p1 = float(cut.get("pause_end") or cut.get("end") or p0)
        c0 = float(cut.get("start") or p0)
        c1 = float(cut.get("end") or p1)
        w0 = max(0.0, p0 - self._pad)
        w1 = max(w0 + 0.2, p1 + self._pad)
        self._win_start = w0
        self._win_end = w1
        self._pause_start = p0
        self._pause_end = p1
        self._cut_start = c0
        self._cut_end = c1
        self._prev_word = str(cut.get("prev_word") or "")
        self._next_word = str(cut.get("next_word") or "")
        self._bins = envelope(sig, w0, w1, bins=220)
        self.update()

    def _x_of_t(self, t: float) -> int:
        span = max(1e-6, self._win_end - self._win_start)
        return int(round((t - self._win_start) / span * self.width()))

    def _t_of_x(self, x: float) -> float:
        span = max(1e-6, self._win_end - self._win_start)
        return self._win_start + max(0.0, min(1.0, x / max(1.0, float(self.width())))) * span

    def mouseMoveEvent(self, event) -> None:
        if not self._bins or self._cut is None:
            return
        x = event.position().x()
        if self._dragging == "left":
            new_c0 = max(self._pause_start, min(self._cut_end - 0.02, self._t_of_x(x)))
            self._cut_start = new_c0
            self.cut_handles_adjusted.emit(self._cut_start, self._cut_end)
            self.update()
            return
        if self._dragging == "right":
            new_c1 = max(self._cut_start + 0.02, min(self._pause_end, self._t_of_x(x)))
            self._cut_end = new_c1
            self.cut_handles_adjusted.emit(self._cut_start, self._cut_end)
            self.update()
            return
        cx0 = self._x_of_t(self._cut_start)
        cx1 = self._x_of_t(self._cut_end)
        if abs(x - cx0) <= 8 or abs(x - cx1) <= 8:
            self.setCursor(Qt.CursorShape.SplitHCursor)
        else:
            self.setCursor(Qt.CursorShape.ArrowCursor)
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._bins and self._cut is not None:
            x = event.position().x()
            cx0 = self._x_of_t(self._cut_start)
            cx1 = self._x_of_t(self._cut_end)
            if abs(x - cx0) <= abs(x - cx1) and abs(x - cx0) <= 10:
                self._dragging = "left"
                return
            if abs(x - cx1) <= 10:
                self._dragging = "right"
                return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._dragging:
            self._dragging = ""
            self.update()
        super().mouseReleaseEvent(event)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w = self.width()
        h = self.height()
        p.fillRect(0, 0, w, h, QColor("#161920"))
        p.setPen(QPen(QColor("#262B36"), 1))
        p.drawRoundedRect(QRectF(0.5, 0.5, w - 1, h - 1), 5, 5)

        if not self._bins or self._win_end <= self._win_start:
            p.setPen(QColor("#788090"))
            p.drawText(
                self.rect(),
                Qt.AlignmentFlag.AlignCenter,
                "【局部细节显微镜】点击下方表格中的任一停顿行，即可在此放大观察前后词波形、绿色护音帧与红色切点（可直接拖动红线微调切点）",
            )
            p.end()
            return

        px0, px1 = self._x_of_t(self._pause_start), self._x_of_t(self._pause_end)
        cx0, cx1 = self._x_of_t(self._cut_start), self._x_of_t(self._cut_end)

        # 绿色保留保护区 (句尾保护 head / 句首保护 tail)
        p.fillRect(px0, 18, max(0, cx0 - px0), h - 22, QColor(46, 160, 67, 65))
        p.fillRect(cx1, 18, max(0, px1 - cx1), h - 22, QColor(46, 160, 67, 65))
        # 红色剪除区
        p.fillRect(cx0, 18, max(1, cx1 - cx0), h - 22, QColor(229, 83, 75, 90))

        # 波形柱
        top_y = 20
        wave_h = max(24, h - 26)
        mid = top_y + wave_h * 0.5
        n = len(self._bins)
        bar_w = max(1.0, w / float(n))
        p.setPen(Qt.PenStyle.NoPen)
        for i, val in enumerate(self._bins):
            bx = int(i * bar_w)
            bh = max(2, int(min(1.0, val * 1.85) * (wave_h - 10)))
            in_cut = cx0 <= bx <= cx1
            in_prot = (px0 <= bx < cx0) or (cx1 < bx <= px1)
            if in_cut:
                p.setBrush(QColor("#E5534B"))
            elif in_prot:
                p.setBrush(QColor("#3FB950"))
            else:
                p.setBrush(QColor("#58A6FF"))
            p.drawRect(bx, int(mid - bh * 0.5), max(1, int(bar_w - 0.6)), bh)

        # 左右红色可拖拽切点线 + 手柄帽
        p.setPen(QPen(QColor("#FF6B6B"), 2.0))
        p.drawLine(cx0, 16, cx0, h - 4)
        p.drawLine(cx1, 16, cx1, h - 4)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#FF6B6B"))
        p.drawRoundedRect(QRectF(cx0 - 4, mid - 12, 8, 24), 3, 3)
        p.drawRoundedRect(QRectF(cx1 - 4, mid - 12, 8, 24), 3, 3)

        # 顶部时间尺与前后台词上下文标签
        f = p.font()
        f.setPointSize(8)
        p.setFont(f)
        p.setPen(QColor("#8B949E"))
        p.drawText(QRectF(8, 2, 160, 14), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, f"窗口: {self._win_start:.2f}s ~ {self._win_end:.2f}s")

        cut_dur = max(0.0, self._cut_end - self._cut_start)
        keep_l = max(0.0, self._cut_start - self._pause_start)
        keep_r = max(0.0, self._pause_end - self._cut_end)
        mid_info = (
            f"🔬 局部切点详情：句尾留 {keep_l * 1000:.0f}ms (绿)  |  "
            f"✂ 剪除 {cut_dur:.2f}s [{self._cut_start:.2f}s–{self._cut_end:.2f}s] (红，可拖动红线微调)  |  "
            f"句首留 {keep_r * 1000:.0f}ms (绿)"
        )
        p.setPen(QColor("#E6EDF3"))
        p.drawText(QRectF(170, 2, w - 180, 14), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, mid_info)

        # 绘制前后紧邻词气泡
        if self._prev_word:
            p.setPen(QColor("#79C0FF"))
            p.drawText(
                QRectF(max(6, px0 - 170), 20, 164, 18),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                f"前词: “{self._prev_word}” ➔",
            )
        if self._next_word:
            p.setPen(QColor("#79C0FF"))
            p.drawText(
                QRectF(min(w - 170, px1 + 6), 20, 164, 18),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                f"➔ 后词: “{self._next_word}”",
            )
        p.end()


class _SilenceDetectWorker(QThread):
    """后台非阻塞分析线程：逐条提取音频包络、VAD 与词级间隙，实时上报进度并支持随时中止。"""

    progress = Signal(int, int, int, str)  # (done_count, total_count, pct, text)
    task_analyzed = Signal(int, str, object, dict, list, str)  # (idx, name, analysis, plan, words, error)
    finished_all = Signal(bool)  # stopped?

    def __init__(self, tasks: list[dict], indices: list[int], params: SilenceParams, cache: AudioCache, parent=None):
        super().__init__(parent)
        self.tasks = tasks
        self.indices = indices
        self.params = params
        self.cache = cache
        self._stop_requested = False

    def request_stop(self) -> None:
        self._stop_requested = True

    def run(self) -> None:
        total = max(1, len(self.indices))
        for pos, idx in enumerate(self.indices):
            if self._stop_requested:
                self.finished_all.emit(True)
                return
            task = self.tasks[idx]
            tname = os.path.basename(str(task.get("video") or task.get("path") or f"任务 {idx + 1}"))
            pct_start = int(round((pos / total) * 100))
            self.progress.emit(pos, total, pct_start, f"正在分析 ({pos + 1}/{total}): {tname} — 提取音频包络与 VAD 语音核验…")
            try:
                words = collect_words(task)
                an = analyze(task, self.params, cache=self.cache)
                if self._stop_requested:
                    self.finished_all.emit(True)
                    return
                plan = plan_task(task, self.params, analysis=an, cache=self.cache, task_index=idx)
                self.task_analyzed.emit(idx, tname, an, plan, words, "")
            except Exception as exc:
                self.task_analyzed.emit(idx, tname, None, {}, [], str(exc))
            pct_done = int(round(((pos + 1) / total) * 100))
            self.progress.emit(pos + 1, total, pct_done, f"已完成 ({pos + 1}/{total}): {tname}")
        self.finished_all.emit(False)


class SilenceCutDialog(QDialog):
    """全屏级剪气口工作台：点开即显、参数预设先行、带进度防连点后台分析、整体+局部双视界观察与批量快捷操作。"""

    def __init__(self, host, parent=None, *, batch_default: bool = False):
        qparent = parent if isinstance(parent, QWidget) else (host if isinstance(host, QWidget) else None)
        super().__init__(qparent)
        self.host = host
        self.setWindowTitle("✂ 智能剪气口工作台（三路防误伤 · 全局鸟瞰 + 局部切点显微镜 · 批量无损自检）")
        # 设为 fill 模式：在 CUTORA 主窗口中自动铺满整个软件客户区，并随窗口自由缩放
        self.setProperty("cutora_sheet", "fill")
        self.setMinimumSize(520, 380)
        self.resize(1460, 880)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self._cache = AudioCache()
        self._plans: dict[int, dict] = {}
        self._analyses: dict[int, object] = {}
        self._words_before: dict[int, list] = {}
        self._row_meta: list[tuple[int, str, dict]] = []
        self._filter_task_idx: int | None = None  # None = 显示全部已分析任务
        self._busy: bool = False
        self._worker: _SilenceDetectWorker | None = None
        self._player = None
        self._audio_out = None
        self._tmp_wavs: list[str] = []
        self._last_preview_wav: str = ""
        self._pending_preview_after: bool | None = None

        self._build_ui(batch_default=batch_default)
        self._bind_shortcuts()
        # 界面立即出现，先展示任务列表概览，不阻塞主线程自动跑耗时分析！
        self._populate_initial_task_overview()

    def _build_ui(self, *, batch_default: bool = False) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 8, 12, 8)
        root.setSpacing(8)

        # ── 1. 顶部参数与场景预设卡片 ──
        grp = QGroupBox("⚙ 第一步：设定剪气口参数与场景预设（三路融合防误伤：ASR 词时间戳 + Silero VAD + 10ms 自适应能量包络）")
        top = QVBoxLayout(grp)
        top.setContentsMargins(12, 8, 12, 8)
        top.setSpacing(6)

        # 第一行：作用范围 + 核心阈值 + 一键场景预设
        row1 = QHBoxLayout()
        row1.setSpacing(8)
        row1.addWidget(QLabel("作用范围:"))
        self.cb_scope = QComboBox()
        self.cb_scope.addItem("🎯 当前选中视频", "current")
        self.cb_scope.addItem("📚 全部视频任务（批量剪气口）", "all")
        if batch_default:
            self.cb_scope.setCurrentIndex(1)
        self.cb_scope.currentIndexChanged.connect(self._on_scope_changed)
        row1.addWidget(self.cb_scope)

        row1.addSpacing(8)
        row1.addWidget(QLabel("当停顿超过:"))
        self.sp_thr = QDoubleSpinBox()
        self.sp_thr.setRange(0.05, 3600.0)
        self.sp_thr.setDecimals(2)
        self.sp_thr.setSingleStep(0.1)
        self.sp_thr.setValue(1.0)
        row1.addWidget(self.sp_thr)

        self.cb_thr_unit = QComboBox()
        for u in UNITS:
            self.cb_thr_unit.addItem(f"{UNIT_LABELS[u]} ({u})", u)
        self.cb_thr_unit.setCurrentIndex(UNITS.index("s"))
        row1.addWidget(self.cb_thr_unit)

        row1.addSpacing(8)
        row1.addWidget(QLabel("剪后保留间隔:"))
        self.sp_keep = QDoubleSpinBox()
        self.sp_keep.setRange(0.0, 600.0)
        self.sp_keep.setDecimals(2)
        self.sp_keep.setSingleStep(0.05)
        self.sp_keep.setValue(0.25)
        row1.addWidget(self.sp_keep)

        self.cb_keep_unit = QComboBox()
        for u in UNITS:
            self.cb_keep_unit.addItem(f"{UNIT_LABELS[u]} ({u})", u)
        self.cb_keep_unit.setCurrentIndex(UNITS.index("s"))
        row1.addWidget(self.cb_keep_unit)

        row1.addSpacing(12)
        row1.addWidget(QLabel("场景预设:"))
        self._preset_btns: list[QPushButton] = []
        presets = [
            ("⚡ 快节奏口播", 0.55, "s", 0.14, "s", 2, 2, "适合短视频高密度口播：停顿 >0.55s 即剪至 0.14s"),
            ("🎙 标准自媒体", 0.80, "s", 0.22, "s", 2, 2, "适合日常自媒体解说：停顿 >0.80s 剪至 0.22s"),
            ("🙏 自然祷告/叙事", 1.00, "s", 0.28, "s", 3, 3, "适合情感叙事与祷告视频：停顿 >1.0s 剪至 0.28s，保留自然韵律"),
            ("🎬 帧级精剪(25帧)", 25.0, "frames", 6.0, "frames", 2, 2, "按帧精确控制：超过 25 帧的停顿剪至保留 6 帧"),
        ]
        for label, thr, tu, keep, ku, hf, tf, tip in presets:
            b = QPushButton(label)
            b.setToolTip(tip)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(
                lambda _c=False, _t=thr, _tu=tu, _k=keep, _ku=ku, _hf=hf, _tf=tf: self._apply_preset(_t, _tu, _k, _ku, _hf, _tf)
            )
            row1.addWidget(b)
            self._preset_btns.append(b)

        row1.addStretch(1)
        top.addLayout(row1)

        # 第二行：护音帧 + 防误伤开关 + 开始分析主按钮
        row2 = QHBoxLayout()
        row2.setSpacing(8)
        row2.addWidget(QLabel("句尾保护:"))
        self.sp_head = QSpinBox()
        self.sp_head.setRange(0, 30)
        self.sp_head.setValue(2)
        self.sp_head.setSuffix(" 帧")
        self.sp_head.setToolTip("前一个词结束后额外保留的保护帧数，防止吞掉尾音辅音")
        row2.addWidget(self.sp_head)

        row2.addSpacing(6)
        row2.addWidget(QLabel("句首保护:"))
        self.sp_tail = QSpinBox()
        self.sp_tail.setRange(0, 30)
        self.sp_tail.setValue(2)
        self.sp_tail.setSuffix(" 帧")
        self.sp_tail.setToolTip("后一个词开始前额外保留的保护帧数，防止切掉起音字头")
        row2.addWidget(self.sp_tail)

        row2.addSpacing(10)
        self.ck_breaths = QCheckBox("保留自然呼吸/气声（防误伤）")
        self.ck_breaths.setChecked(True)
        row2.addWidget(self.ck_breaths)

        self.ck_vad = QCheckBox("启用 Silero VAD 语音核验")
        self.ck_vad.setChecked(True)
        row2.addWidget(self.ck_vad)

        self.ck_edges = QCheckBox("同时裁剪片头片尾静音")
        self.ck_edges.setChecked(True)
        row2.addWidget(self.ck_edges)

        row2.addStretch(1)

        self.btn_detect = QPushButton("🔍 第二步：开始智能分析 (Ctrl+R)")
        self.btn_detect.setMinimumHeight(34)
        self.btn_detect.setMinimumWidth(210)
        self.btn_detect.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_detect.setStyleSheet(
            "QPushButton { background-color: #1F6FEB; color: #FFFFFF; font-weight: bold; border-radius: 5px; padding: 4px 14px; }"
            "QPushButton:hover { background-color: #388BFD; }"
            "QPushButton:disabled { background-color: #30363D; color: #8B949E; }"
        )
        self.btn_detect.clicked.connect(self.start_detect_async)
        row2.addWidget(self.btn_detect)

        self.btn_stop = QPushButton("⏹ 停止")
        self.btn_stop.setMinimumHeight(34)
        self.btn_stop.setVisible(False)
        self.btn_stop.clicked.connect(self._request_stop_worker)
        row2.addWidget(self.btn_stop)

        top.addLayout(row2)

        # 第三行：显式进度条与实时状态反馈（防卡死焦虑、防重复点击）
        prog_row = QHBoxLayout()
        prog_row.setSpacing(8)
        self.progress_bar = QProgressBar(self)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setMinimumHeight(18)
        self.progress_bar.setMaximumHeight(20)
        self.progress_bar.setFormat("%p%")
        prog_row.addWidget(self.progress_bar, 2)

        self.lbl_summary = QLabel("✨ 已就绪：请先在上方确认停顿阈值与保护设置，然后点击「🔍 第二步：开始智能分析」。")
        self.lbl_summary.setStyleSheet("color: #58A6FF; font-weight: bold;")
        prog_row.addWidget(self.lbl_summary, 3)
        top.addLayout(prog_row)

        root.addWidget(grp)

        # ── 2. 中部核心工作区（上下可自由拖拽的隐形分割器：上方为全局+局部双波形观察区，下方为左任务导航+右切点明细表）──
        self.v_splitter = HoverThinSplitter(Qt.Orientation.Vertical, self)

        # === 上半区：整体全局鸟瞰图 + 局部切点显微镜 ===
        vis_pane = QFrame(self.v_splitter)
        vis_lay = QVBoxLayout(vis_pane)
        vis_lay.setContentsMargins(0, 0, 0, 2)
        vis_lay.setSpacing(6)

        # 整体全局鸟瞰条
        self.macro_view = SilenceMacroOverview(vis_pane)
        self.macro_view.cut_clicked.connect(self._on_macro_cut_clicked)
        vis_lay.addWidget(self.macro_view)

        # 局部细节控制栏 + 试听与微调工具条
        micro_bar = QHBoxLayout()
        micro_bar.setSpacing(6)
        lbl_micro = QLabel("🔬 局部切点显微镜:")
        lbl_micro.setStyleSheet("font-weight: bold; color: #C9D1D9;")
        micro_bar.addWidget(lbl_micro)

        micro_bar.addWidget(QLabel("局部视野:"))
        self.cb_zoom = QComboBox()
        for pad_s, z_lbl in ((0.8, "±0.8秒 (特写)"), (1.5, "±1.5秒 (标准)"), (3.0, "±3.0秒 (宽视界)"), (5.0, "±5.0秒 (全景)")):
            self.cb_zoom.addItem(z_lbl, pad_s)
        self.cb_zoom.setCurrentIndex(1)
        self.cb_zoom.currentIndexChanged.connect(lambda _i: self.wave_view.set_zoom_pad(float(self.cb_zoom.currentData() or 1.5)))
        micro_bar.addWidget(self.cb_zoom)

        micro_bar.addSpacing(8)
        self.btn_nudge_l_minus = QPushButton("◀ 左切点 -1帧 ([)")
        self.btn_nudge_l_minus.setToolTip("将当前气口的左切点向左扩 1 帧")
        self.btn_nudge_l_minus.clicked.connect(lambda: self.nudge_selected_cut(-1, 0))
        micro_bar.addWidget(self.btn_nudge_l_minus)

        self.btn_nudge_l_plus = QPushButton("左切点 +1帧 ▶")
        self.btn_nudge_l_plus.setToolTip("将当前气口的左切点向右缩 1 帧（多留句尾护音）")
        self.btn_nudge_l_plus.clicked.connect(lambda: self.nudge_selected_cut(1, 0))
        micro_bar.addWidget(self.btn_nudge_l_plus)

        self.btn_nudge_r_minus = QPushButton("◀ 右切点 -1帧")
        self.btn_nudge_r_minus.setToolTip("将当前气口的右切点向左缩 1 帧（多留句首护音）")
        self.btn_nudge_r_minus.clicked.connect(lambda: self.nudge_selected_cut(0, -1))
        micro_bar.addWidget(self.btn_nudge_r_minus)

        self.btn_nudge_r_plus = QPushButton("右切点 +1帧 (]) ▶")
        self.btn_nudge_r_plus.setToolTip("将当前气口的右切点向右扩 1 帧")
        self.btn_nudge_r_plus.clicked.connect(lambda: self.nudge_selected_cut(0, 1))
        micro_bar.addWidget(self.btn_nudge_r_plus)

        micro_bar.addStretch(1)
        self.btn_prev_before = QPushButton("▶ 试听原停顿 (B)")
        self.btn_prev_before.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_prev_before.setToolTip("试听当前选中气口裁剪前的原始停顿与前后人声 (快捷键: B)")
        self.btn_prev_before.clicked.connect(lambda *_a: self.preview_selected(after=False))
        self.btn_prev_after = QPushButton("▶ 试听剪后接缝 (Space)")
        self.btn_prev_after.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_prev_after.setToolTip("试听当前选中气口裁剪并施加 5ms 微淡化后的自然接缝效果 (快捷键: Space)")
        self.btn_prev_after.setStyleSheet("QPushButton { font-weight: bold; color: #56D364; }")
        self.btn_prev_after.clicked.connect(lambda *_a: self.preview_selected(after=True))
        micro_bar.addWidget(self.btn_prev_before)
        micro_bar.addWidget(self.btn_prev_after)
        vis_lay.addLayout(micro_bar)

        self.wave_view = SilenceWavePreview(vis_pane)
        self.wave_view.cut_handles_adjusted.connect(self._on_wave_handles_dragged)
        vis_lay.addWidget(self.wave_view, 1)

        # === 下半区：左右自由拖拽分割器（左侧：批量任务总览导航；右侧：批量快捷筛选栏 + 切点复核表）===
        self.h_splitter = HoverThinSplitter(Qt.Orientation.Horizontal, self.v_splitter)

        # 左栏：批量任务总览与快速过滤表
        left_pane = QFrame(self.h_splitter)
        left_lay = QVBoxLayout(left_pane)
        left_lay.setContentsMargins(0, 2, 4, 0)
        left_lay.setSpacing(4)
        lbl_tasks = QLabel("📂 视频任务总览（点击可筛选单条视频）:")
        lbl_tasks.setStyleSheet("font-weight: bold;")
        left_lay.addWidget(lbl_tasks)

        self.task_table = QTableWidget(0, 4, left_pane)
        self.task_table.setHorizontalHeaderLabels(["任务", "原长", "可剪气口", "预计缩短"])
        self.task_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.task_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.task_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        th = self.task_table.horizontalHeader()
        th.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        th.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        th.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        th.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.task_table.itemSelectionChanged.connect(self._on_task_filter_selected)
        left_lay.addWidget(self.task_table, 1)

        # 右栏：批量快捷操作工具条 + 切点明细表格
        right_pane = QFrame(self.h_splitter)
        right_lay = QVBoxLayout(right_pane)
        right_lay.setContentsMargins(4, 2, 0, 0)
        right_lay.setSpacing(4)

        batch_bar = QHBoxLayout()
        batch_bar.setSpacing(6)
        lbl_cuts = QLabel("✂ 气口切点明细与复核:")
        lbl_cuts.setStyleSheet("font-weight: bold;")
        batch_bar.addWidget(lbl_cuts)

        self.btn_sel_all = QPushButton("☑ 全选")
        self.btn_sel_all.clicked.connect(lambda: self.batch_set_checked("all"))
        batch_bar.addWidget(self.btn_sel_all)

        self.btn_sel_none = QPushButton("☐ 全不选")
        self.btn_sel_none.clicked.connect(lambda: self.batch_set_checked("none"))
        batch_bar.addWidget(self.btn_sel_none)

        self.btn_sel_inv = QPushButton("🔄 反选")
        self.btn_sel_inv.clicked.connect(lambda: self.batch_set_checked("invert"))
        batch_bar.addWidget(self.btn_sel_inv)

        self.btn_sel_safe = QPushButton("🛡 仅选纯静音安全项")
        self.btn_sel_safe.setToolTip("只勾选三路信号 100% 确认为纯静音的气口，自动跳过任何带黄色提示的气口")
        self.btn_sel_safe.clicked.connect(lambda: self.batch_set_checked("safe_only"))
        batch_bar.addWidget(self.btn_sel_safe)

        self.btn_sel_long = QPushButton("⏱ 仅选超长停顿 (>1.5s)")
        self.btn_sel_long.setToolTip("只勾选停顿时长超过 1.5 秒的明显长气口")
        self.btn_sel_long.clicked.connect(lambda: self.batch_set_checked("long_only"))
        batch_bar.addWidget(self.btn_sel_long)

        batch_bar.addStretch(1)
        lbl_sc_hint = QLabel("快捷键: Space=试听剪后 | B=试听原片 | X=切换勾选 | [/]=微调切点")
        lbl_sc_hint.setStyleSheet("color: #8B949E; font-size: 11px;")
        batch_bar.addWidget(lbl_sc_hint)
        right_lay.addLayout(batch_bar)

        self.table = QTableWidget(0, 8, right_pane)
        self.table.setHorizontalHeaderLabels([
            "执行", "任务", "停顿区间 (秒)", "前后台词上下文", "原停顿", "剪除时长", "剪后间隔", "状态与三路信号安全自检说明",
        ])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
        hh.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(6, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(7, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(3, 190)
        self.table.itemSelectionChanged.connect(self._on_row_selected)
        self.table.itemChanged.connect(self._on_item_changed)
        right_lay.addWidget(self.table, 1)

        self.h_splitter.addWidget(left_pane)
        self.h_splitter.addWidget(right_pane)
        self.h_splitter.setStretchFactor(0, 2)
        self.h_splitter.setStretchFactor(1, 5)
        self.h_splitter.setSizes([340, 1060])

        self.v_splitter.addWidget(vis_pane)
        self.v_splitter.addWidget(self.h_splitter)
        self.v_splitter.setStretchFactor(0, 2)
        self.v_splitter.setStretchFactor(1, 4)
        self.v_splitter.setSizes([240, 500])

        root.addWidget(self.v_splitter, 1)

        # ── 3. 底部自检报告与执行栏 ──
        bot = QHBoxLayout()
        bot.setSpacing(8)
        self.lbl_verify = QLabel(
            "💡 操作流程：① 调整上方参数或点击场景预设 ➔ ② 点击「开始智能分析」 ➔ ③ 在全局/局部视图试听复核 ➔ ④ 点击右侧「执行剪气口并运行无损自检」。"
        )
        self.lbl_verify.setWordWrap(True)
        bot.addWidget(self.lbl_verify, 1)

        self.btn_apply = QPushButton("✂ 第三步：执行剪气口并运行无损自检 (Ctrl+Enter)")
        self.btn_apply.setMinimumHeight(38)
        self.btn_apply.setMinimumWidth(260)
        self.btn_apply.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_apply.setStyleSheet(
            "QPushButton { background-color: #238636; color: #FFFFFF; font-weight: bold; border-radius: 5px; padding: 6px 18px; }"
            "QPushButton:hover { background-color: #2EA043; }"
            "QPushButton:disabled { background-color: #30363D; color: #8B949E; }"
        )
        self.btn_apply.clicked.connect(self.run_apply)
        bot.addWidget(self.btn_apply)

        btn_close = QPushButton("完成并退出")
        btn_close.setMinimumHeight(38)
        btn_close.clicked.connect(self.close)
        bot.addWidget(btn_close)
        root.addLayout(bot)

    def _bind_shortcuts(self) -> None:
        QShortcut(QKeySequence(Qt.Key.Key_Space), self, activated=lambda: self.preview_selected(after=True))
        QShortcut(QKeySequence("B"), self, activated=lambda: self.preview_selected(after=False))
        QShortcut(QKeySequence("X"), self, activated=self.toggle_current_row_checked)
        QShortcut(QKeySequence("["), self, activated=lambda: self.nudge_selected_cut(-1, 0))
        QShortcut(QKeySequence("]"), self, activated=lambda: self.nudge_selected_cut(0, 1))
        QShortcut(QKeySequence("Ctrl+R"), self, activated=self.start_detect_async)
        QShortcut(QKeySequence("F5"), self, activated=self.start_detect_async)
        QShortcut(QKeySequence("Ctrl+Return"), self, activated=self.run_apply)
        # 安装事件过滤器：确保在全屏 RoomHost 模式下（RoomHost 会静音主窗 QShortcut）
        # 以及焦点在表格/波形图上时，B / Space / X / [ / ] 等快捷键依然 100% 灵敏响应。
        for w in (self, self.table, self.task_table, self.wave_view, self.macro_view, self.btn_prev_before, self.btn_prev_after):
            w.installEventFilter(self)

    def eventFilter(self, obj, event) -> bool:
        from PySide6.QtCore import QEvent
        from PySide6.QtWidgets import QAbstractSpinBox, QLineEdit
        if event.type() == QEvent.Type.KeyPress:
            fw = QApplication.focusWidget()
            if isinstance(fw, (QAbstractSpinBox, QLineEdit)):
                return super().eventFilter(obj, event)
            key = event.key()
            mods = event.modifiers()
            ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
            if ctrl and key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self.run_apply()
                return True
            if (ctrl and key == Qt.Key.Key_R) or key == Qt.Key.Key_F5:
                self.start_detect_async()
                return True
            if not ctrl and not (mods & Qt.KeyboardModifier.AltModifier):
                if key == Qt.Key.Key_Space:
                    self.preview_selected(after=True)
                    return True
                if key == Qt.Key.Key_B:
                    self.preview_selected(after=False)
                    return True
                if key == Qt.Key.Key_X:
                    self.toggle_current_row_checked()
                    return True
                if key == Qt.Key.Key_BracketLeft:
                    self.nudge_selected_cut(-1, 0)
                    return True
                if key == Qt.Key.Key_BracketRight:
                    self.nudge_selected_cut(0, 1)
                    return True
        return super().eventFilter(obj, event)

    def _apply_preset(self, thr: float, thr_unit: str, keep: float, keep_unit: str, head_f: int, tail_f: int) -> None:
        if self._busy:
            return
        self.sp_thr.setValue(float(thr))
        if thr_unit in UNITS:
            self.cb_thr_unit.setCurrentIndex(UNITS.index(thr_unit))
        self.sp_keep.setValue(float(keep))
        if keep_unit in UNITS:
            self.cb_keep_unit.setCurrentIndex(UNITS.index(keep_unit))
        self.sp_head.setValue(int(head_f))
        self.sp_tail.setValue(int(tail_f))
        self.lbl_summary.setText(
            f"✓ 已套用预设参数（阈值 > {thr}{thr_unit}，保留 {keep}{keep_unit}）。请点击「🔍 第二步：开始智能分析」执行扫描。"
        )

    def _on_scope_changed(self, _idx: int = 0) -> None:
        if self._busy:
            return
        self._populate_initial_task_overview()

    def _populate_initial_task_overview(self) -> None:
        """打开面板瞬间（<2ms）填充左侧视频任务概览，绝不阻塞主线程。"""
        tasks = list(getattr(self.host, "tasks", None) or [])
        indices = self.target_task_indices()
        self.task_table.blockSignals(True)
        self.task_table.setRowCount(0)

        # 第一行：全部汇总
        self.task_table.insertRow(0)
        it_all = QTableWidgetItem(f"🌐 全部目标任务 ({len(indices)} 条)")
        it_all.setData(Qt.ItemDataRole.UserRole, None)
        self.task_table.setItem(0, 0, it_all)
        self.task_table.setItem(0, 1, QTableWidgetItem("—"))
        self.task_table.setItem(0, 2, QTableWidgetItem("待分析"))
        self.task_table.setItem(0, 3, QTableWidgetItem("—"))

        for idx in indices:
            if not (0 <= idx < len(tasks)):
                continue
            t = tasks[idx]
            tname = os.path.basename(str(t.get("video") or t.get("path") or f"任务 {idx + 1}"))
            dur = float(t.get("duration") or timeline_end(t) or 0.0)
            r = self.task_table.rowCount()
            self.task_table.insertRow(r)
            it = QTableWidgetItem(f"#{idx + 1} {tname}")
            it.setData(Qt.ItemDataRole.UserRole, idx)
            self.task_table.setItem(r, 0, it)
            self.task_table.setItem(r, 1, QTableWidgetItem(f"{dur:.1f}s" if dur > 0 else "—"))
            plan = self._plans.get(idx)
            if plan is not None:
                cuts = [c for c in (plan.get("cuts") or []) if c.get("enabled", True)]
                rem_s = sum(float(c.get("remove_s") or 0.0) for c in cuts)
                self.task_table.setItem(r, 2, QTableWidgetItem(f"{len(cuts)} 处"))
                self.task_table.setItem(r, 3, QTableWidgetItem(f"-{rem_s:.2f}s"))
            else:
                self.task_table.setItem(r, 2, QTableWidgetItem("待分析"))
                self.task_table.setItem(r, 3, QTableWidgetItem("—"))

        self.task_table.blockSignals(False)
        if not self._plans:
            self.progress_bar.setValue(0)
            self.lbl_summary.setText(
                f"✨ 已就绪（共选定 {len(indices)} 条视频任务）。请先设置上方参数，再点击「🔍 第二步：开始智能分析」。"
            )

    def _set_busy(self, busy: bool, status_text: str = "") -> None:
        """统一防连点保护锁：分析或执行剪辑期间禁用可触发重入的控件，避免多次狂点导致软件崩溃。"""
        self._busy = bool(busy)
        self.btn_detect.setEnabled(not busy)
        self.btn_apply.setEnabled(not busy)
        self.cb_scope.setEnabled(not busy)
        self.btn_stop.setVisible(busy)
        for b in self._preset_btns:
            b.setEnabled(not busy)
        if busy:
            self.btn_detect.setText("⏳ 正在智能分析中…")
            if status_text:
                self.lbl_summary.setText(status_text)
        else:
            self.btn_detect.setText("🔍 第二步：开始智能分析 (Ctrl+R)")
            if status_text:
                self.lbl_summary.setText(status_text)

    def current_params(self) -> SilenceParams:
        return SilenceParams(
            threshold=float(self.sp_thr.value()),
            unit=str(self.cb_thr_unit.currentData() or "s"),
            keep=float(self.sp_keep.value()),
            keep_unit=str(self.cb_keep_unit.currentData() or "s"),
            head_frames=int(self.sp_head.value()),
            tail_frames=int(self.sp_tail.value()),
            keep_breaths=bool(self.ck_breaths.isChecked()),
            use_vad=bool(self.ck_vad.isChecked()),
            trim_edges=bool(self.ck_edges.isChecked()),
            xfade_ms=5.0,
        )

    def target_task_indices(self) -> list[int]:
        tasks = list(getattr(self.host, "tasks", None) or [])
        if not tasks:
            return []
        if self.cb_scope.currentData() == "all":
            return list(range(len(tasks)))
        cur = getattr(self.host, "curr_idx", getattr(self.host, "current_task_index", 0))
        if not isinstance(cur, int) or not (0 <= cur < len(tasks)):
            cur = 0
        return [cur]

    def start_detect_async(self) -> None:
        """点击「开始智能分析」按钮后启动后台分析线程，实时刷新进度条并防重复点击。"""
        if self._busy:
            return
        tasks = list(getattr(self.host, "tasks", None) or [])
        indices = self.target_task_indices()
        if not indices:
            self.lbl_summary.setText("⚠ 当前工程中没有可分析的视频任务。")
            return

        params = self.current_params()
        self._plans.clear()
        self._analyses.clear()
        self._words_before.clear()
        self._row_meta.clear()
        self._filter_task_idx = None
        self.table.setRowCount(0)
        self.macro_view.clear_overview()
        self.wave_view.set_cut_view(None, {})

        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self._set_busy(True, f"⏳ 正在准备分析 {len(indices)} 条视频任务…")

        worker = _SilenceDetectWorker(tasks, indices, params, self._cache, self)
        self._worker = worker
        worker.progress.connect(self._on_worker_progress)
        worker.task_analyzed.connect(self._on_worker_task_analyzed)
        worker.finished_all.connect(self._on_worker_finished)
        worker.start()

    def _request_stop_worker(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            self._worker.request_stop()
            self.lbl_summary.setText("⏹ 正在安全停止分析线程，请稍候…")

    def _on_worker_progress(self, _done: int, _total: int, pct: int, msg: str) -> None:
        self.progress_bar.setValue(max(0, min(100, int(pct))))
        self.lbl_summary.setText(msg)

    def _on_worker_task_analyzed(self, idx: int, _tname: str, an: object, plan: dict, words: list, err: str) -> None:
        if err:
            self._plans[idx] = {"error": err, "cuts": [], "flags": []}
            return
        self._words_before[idx] = words
        self._analyses[idx] = an
        self._plans[idx] = plan

    def _on_worker_finished(self, stopped: bool) -> None:
        self._worker = None
        self._rebuild_tables_from_plans()
        summary = self._compute_summary_dict()
        if stopped:
            self._pending_preview_after = None
            self._set_busy(
                False,
                f"⏹ 已中止分析（已分析 {len(self._plans)} 条视频，检出可剪气口 {summary['total_cuts']} 处，预计缩短 {summary['total_removed_s']:.2f}s）",
            )
        else:
            self.progress_bar.setValue(100)
            self._set_busy(
                False,
                f"✓ 分析完成：共扫描 {summary['count']} 条视频 · 检出可剪气口 {summary['total_cuts']} 处"
                f"（预计缩短 {summary['total_removed_s']:.2f} 秒）· 防误伤保护提示 {summary['total_flags']} 处",
            )
            if self._pending_preview_after is not None:
                pend = self._pending_preview_after
                self._pending_preview_after = None
                self.preview_selected(after=pend)

    def run_detect(self) -> dict:
        """同步检测接口（供无头测试、MCP 或脚本直接调用，同时刷新 UI 进度条与表格）。"""
        if self._busy:
            return self._compute_summary_dict()
        tasks = list(getattr(self.host, "tasks", None) or [])
        indices = self.target_task_indices()
        params = self.current_params()
        self._plans.clear()
        self._analyses.clear()
        self._words_before.clear()
        self._row_meta.clear()
        self._filter_task_idx = None

        total = max(1, len(indices))
        self._set_busy(True, "⏳ 正在分析停顿…")
        try:
            for pos, idx in enumerate(indices):
                task = tasks[idx]
                self.progress_bar.setValue(int(round((pos / total) * 100)))
                try:
                    self._words_before[idx] = collect_words(task)
                    an = analyze(task, params, cache=self._cache)
                    plan = plan_task(task, params, analysis=an, cache=self._cache, task_index=idx)
                    self._analyses[idx] = an
                    self._plans[idx] = plan
                except Exception as exc:
                    self._plans[idx] = {"error": str(exc), "cuts": [], "flags": []}
                self.progress_bar.setValue(int(round(((pos + 1) / total) * 100)))
        finally:
            self._rebuild_tables_from_plans()
            summary = self._compute_summary_dict()
            self._set_busy(
                False,
                f"✓ 共扫描 {summary['count']} 条视频 · 检出可剪气口 {summary['total_cuts']} 处"
                f"（预计缩短 {summary['total_removed_s']:.2f} 秒）· 防误伤保护提示 {summary['total_flags']} 处",
            )
        return summary

    def _compute_summary_dict(self) -> dict:
        total_cuts = 0
        total_rem = 0.0
        total_flags = 0
        for plan in self._plans.values():
            for c in plan.get("cuts") or []:
                if c.get("enabled", True):
                    total_cuts += 1
                    total_rem += float(c.get("remove_s") or 0.0)
            total_flags += len(plan.get("flags") or [])
        return {
            "count": len(self._plans),
            "total_cuts": total_cuts,
            "total_removed_s": round(total_rem, 3),
            "total_flags": total_flags,
        }

    def _rebuild_tables_from_plans(self) -> None:
        """根据 self._plans 刷新左侧任务总览表与右侧切点明细表。"""
        tasks = list(getattr(self.host, "tasks", None) or [])
        indices = self.target_task_indices()

        # 1. 刷新左侧任务总览
        self.task_table.blockSignals(True)
        self.task_table.setRowCount(0)
        sum_info = self._compute_summary_dict()
        self.task_table.insertRow(0)
        it_all = QTableWidgetItem(f"🌐 全部已分析 ({len(self._plans)} 条)")
        it_all.setData(Qt.ItemDataRole.UserRole, None)
        self.task_table.setItem(0, 0, it_all)
        self.task_table.setItem(0, 1, QTableWidgetItem("—"))
        self.task_table.setItem(0, 2, QTableWidgetItem(f"{sum_info['total_cuts']} 处"))
        self.task_table.setItem(0, 3, QTableWidgetItem(f"-{sum_info['total_removed_s']:.2f}s"))

        selected_left_row = 0
        for idx in indices:
            if not (0 <= idx < len(tasks)):
                continue
            t = tasks[idx]
            tname = os.path.basename(str(t.get("video") or t.get("path") or f"任务 {idx + 1}"))
            dur = float(t.get("duration") or timeline_end(t) or 0.0)
            r = self.task_table.rowCount()
            self.task_table.insertRow(r)
            it = QTableWidgetItem(f"#{idx + 1} {tname}")
            it.setData(Qt.ItemDataRole.UserRole, idx)
            self.task_table.setItem(r, 0, it)
            self.task_table.setItem(r, 1, QTableWidgetItem(f"{dur:.1f}s" if dur > 0 else "—"))
            plan = self._plans.get(idx)
            if plan is not None:
                cuts = [c for c in (plan.get("cuts") or []) if c.get("enabled", True)]
                rem_s = sum(float(c.get("remove_s") or 0.0) for c in cuts)
                self.task_table.setItem(r, 2, QTableWidgetItem(f"{len(cuts)} 处"))
                self.task_table.setItem(r, 3, QTableWidgetItem(f"-{rem_s:.2f}s"))
            else:
                self.task_table.setItem(r, 2, QTableWidgetItem("待分析"))
                self.task_table.setItem(r, 3, QTableWidgetItem("—"))
            if self._filter_task_idx == idx:
                selected_left_row = r

        self.task_table.selectRow(selected_left_row)
        self.task_table.blockSignals(False)

        # 2. 刷新右侧切点表
        self._populate_cuts_table()

    def _populate_cuts_table(self) -> None:
        tasks = list(getattr(self.host, "tasks", None) or [])
        self._row_meta.clear()
        self.table.blockSignals(True)
        self.table.setRowCount(0)

        for idx, plan in self._plans.items():
            if self._filter_task_idx is not None and idx != self._filter_task_idx:
                continue
            task = tasks[idx] if 0 <= idx < len(tasks) else {}
            tname = os.path.basename(str(task.get("video") or task.get("path") or f"任务 {idx + 1}"))
            err = plan.get("error")
            if err:
                r = self.table.rowCount()
                self.table.insertRow(r)
                self.table.setItem(r, 1, QTableWidgetItem(f"#{idx + 1} {tname}"))
                self.table.setItem(r, 7, QTableWidgetItem(f"检测失败: {err}"))
                self._row_meta.append((idx, "error", {}))
                continue

            for cut in plan.get("cuts") or []:
                r = self.table.rowCount()
                self.table.insertRow(r)
                chk = QTableWidgetItem("")
                chk.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsUserCheckable)
                chk.setCheckState(Qt.CheckState.Checked if cut.get("enabled", True) else Qt.CheckState.Unchecked)
                self.table.setItem(r, 0, chk)
                self.table.setItem(r, 1, QTableWidgetItem(f"#{idx + 1} {tname}"))
                self.table.setItem(r, 2, QTableWidgetItem(f"{cut['pause_start']:.2f}s – {cut['pause_end']:.2f}s"))
                pw = str(cut.get("prev_word") or "")
                nw = str(cut.get("next_word") or "")
                ctx_str = f"“…{pw}” ➔ “{nw}…”" if (pw or nw) else "（片头/片尾或无字幕段）"
                self.table.setItem(r, 3, QTableWidgetItem(ctx_str))
                self.table.setItem(r, 4, QTableWidgetItem(f"{cut['pause_s']:.2f}s"))
                self.table.setItem(r, 5, QTableWidgetItem(f"-{cut['remove_s']:.2f}s ({cut['remove_frames']}帧)"))
                self.table.setItem(r, 6, QTableWidgetItem(f"{cut['result_s']:.2f}s"))
                note = cut.get("note") or "✓ 三路信号均确认为纯静音气口，安全裁剪"
                it_note = QTableWidgetItem(note)
                if cut.get("status") == "warn":
                    it_note.setForeground(QColor("#E5A93C"))
                else:
                    it_note.setForeground(QColor("#56D364"))
                self.table.setItem(r, 7, it_note)
                self._row_meta.append((idx, "cut", cut))

            for fl in plan.get("flags") or []:
                r = self.table.rowCount()
                self.table.insertRow(r)
                dash = QTableWidgetItem("保护")
                dash.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                self.table.setItem(r, 0, dash)
                self.table.setItem(r, 1, QTableWidgetItem(f"#{idx + 1} {tname}"))
                self.table.setItem(r, 2, QTableWidgetItem(f"{fl['start']:.2f}s – {fl['end']:.2f}s"))
                self.table.setItem(r, 3, QTableWidgetItem("（呼吸/气声/保护区）"))
                self.table.setItem(r, 4, QTableWidgetItem(f"{fl['gap_s']:.2f}s"))
                self.table.setItem(r, 5, QTableWidgetItem("0.00s (保护不剪)"))
                self.table.setItem(r, 6, QTableWidgetItem(f"{fl.get('remain_s', fl['gap_s']):.2f}s"))
                it_fl = QTableWidgetItem(f"⚠ {fl.get('note') or ''}")
                it_fl.setForeground(QColor("#E5A93C"))
                self.table.setItem(r, 7, it_fl)
                self._row_meta.append((idx, "flag", fl))

        self.table.blockSignals(False)
        if self.table.rowCount() > 0:
            self.table.selectRow(0)
        else:
            # 若当前筛选的任务无切点，仍展示其全局鸟瞰图
            target_idx = self._filter_task_idx
            if target_idx is None and self._plans:
                target_idx = next(iter(self._plans.keys()))
            if target_idx is not None:
                self._update_macro_for_task(target_idx, active_cut_id="")

    def _on_task_filter_selected(self) -> None:
        row = self.task_table.currentRow()
        if row < 0:
            return
        it = self.task_table.item(row, 0)
        if it is None:
            return
        idx = it.data(Qt.ItemDataRole.UserRole)
        self._filter_task_idx = idx if isinstance(idx, int) else None
        self._populate_cuts_table()

    def _update_macro_for_task(self, idx: int, active_cut_id: str = "") -> None:
        tasks = list(getattr(self.host, "tasks", None) or [])
        an = self._analyses.get(idx)
        plan = self._plans.get(idx) or {}
        if an is None:
            self.macro_view.clear_overview()
            return
        t = tasks[idx] if 0 <= idx < len(tasks) else {}
        tname = os.path.basename(str(t.get("video") or t.get("path") or f"任务 {idx + 1}"))
        self.macro_view.set_task_overview(
            an.sig,
            float(getattr(an, "end", 0.0) or 0.0),
            plan.get("cuts") or [],
            plan.get("flags") or [],
            active_cut_id=active_cut_id,
            task_label=f"#{idx + 1} {tname}",
        )

    def _on_macro_cut_clicked(self, cut_index_in_task: int) -> None:
        row = self.table.currentRow()
        cur_task_idx = None
        if 0 <= row < len(self._row_meta):
            cur_task_idx = self._row_meta[row][0]
        elif self._filter_task_idx is not None:
            cur_task_idx = self._filter_task_idx
        elif self._plans:
            cur_task_idx = next(iter(self._plans.keys()))
        if cur_task_idx is None:
            return
        plan = self._plans.get(cur_task_idx) or {}
        cuts = plan.get("cuts") or []
        if not (0 <= cut_index_in_task < len(cuts)):
            return
        target_cut = cuts[cut_index_in_task]
        for r, (r_idx, kind, data) in enumerate(self._row_meta):
            if r_idx == cur_task_idx and kind == "cut" and data is target_cut:
                self.table.selectRow(r)
                self.table.scrollToItem(self.table.item(r, 0))
                break

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if item is None or item.column() != 0:
            return
        row = item.row()
        if not (0 <= row < len(self._row_meta)):
            return
        idx, kind, data = self._row_meta[row]
        if kind == "cut":
            data["enabled"] = item.checkState() == Qt.CheckState.Checked
            self._update_macro_for_task(idx, active_cut_id=str(data.get("id") or ""))
            self._refresh_summary_banner()

    def _refresh_summary_banner(self) -> None:
        s = self._compute_summary_dict()
        self.lbl_summary.setText(
            f"✓ 已分析 {s['count']} 条视频 · 当前已勾选可剪气口 {s['total_cuts']} 处"
            f"（预计缩短 {s['total_removed_s']:.2f} 秒）· 防误伤保护提示 {s['total_flags']} 处"
        )

    def _on_row_selected(self) -> None:
        row = self.table.currentRow()
        if not (0 <= row < len(self._row_meta)):
            return
        idx, _kind, data = self._row_meta[row]
        an = self._analyses.get(idx)
        if an is not None:
            self.wave_view.set_cut_view(an.sig, data, pad=float(self.cb_zoom.currentData() or 1.5))
            self._update_macro_for_task(idx, active_cut_id=str(data.get("id") or ""))

    def _on_wave_handles_dragged(self, new_start: float, new_end: float) -> None:
        """用户在局部显微镜上拖动红色切点线时，实时按帧对齐并更新当前切点数据与表格显示。"""
        row = self.table.currentRow()
        if not (0 <= row < len(self._row_meta)):
            return
        idx, kind, data = self._row_meta[row]
        if kind != "cut":
            return
        tasks = list(getattr(self.host, "tasks", None) or [])
        task = tasks[idx] if 0 <= idx < len(tasks) else {}
        fps = max(1.0, float(timeline_fps(task) or 30.0))
        f0 = int(round(new_start * fps))
        f1 = max(f0 + 1, int(round(new_end * fps)))
        c0 = f0 / fps
        c1 = f1 / fps
        rem_f = f1 - f0
        rem_s = rem_f / fps
        pause_s = float(data.get("pause_s") or (data.get("pause_end", c1) - data.get("pause_start", c0)))
        res_s = max(0.0, pause_s - rem_s)
        data["frame_start"] = f0
        data["frame_end"] = f1
        data["start"] = c0
        data["end"] = c1
        data["remove_frames"] = rem_f
        data["remove_s"] = rem_s
        data["result_s"] = res_s
        self.table.blockSignals(True)
        self.table.setItem(row, 5, QTableWidgetItem(f"-{rem_s:.2f}s ({rem_f}帧)"))
        self.table.setItem(row, 6, QTableWidgetItem(f"{res_s:.2f}s"))
        self.table.blockSignals(False)
        self._update_macro_for_task(idx, active_cut_id=str(data.get("id") or ""))
        self._refresh_summary_banner()

    def nudge_selected_cut(self, delta_left_frames: int, delta_right_frames: int) -> None:
        """按帧微调当前选中气口的左/右切点。"""
        row = self.table.currentRow()
        if not (0 <= row < len(self._row_meta)):
            return
        idx, kind, data = self._row_meta[row]
        if kind != "cut":
            return
        tasks = list(getattr(self.host, "tasks", None) or [])
        task = tasks[idx] if 0 <= idx < len(tasks) else {}
        fps = max(1.0, float(timeline_fps(task) or 30.0))
        p0 = float(data.get("pause_start") or 0.0)
        p1 = float(data.get("pause_end") or 0.0)
        f0 = int(data.get("frame_start") or round(float(data.get("start") or p0) * fps)) + int(delta_left_frames)
        f1 = int(data.get("frame_end") or round(float(data.get("end") or p1) * fps)) + int(delta_right_frames)
        min_f = int(math.ceil(p0 * fps))
        max_f = int(math.floor(p1 * fps))
        f0 = max(min_f, min(f0, f1 - 1))
        f1 = max(f0 + 1, min(max_f, f1))
        self._on_wave_handles_dragged(f0 / fps, f1 / fps)
        an = self._analyses.get(idx)
        if an is not None:
            self.wave_view.set_cut_view(an.sig, data)

    def toggle_current_row_checked(self) -> None:
        row = self.table.currentRow()
        if not (0 <= row < len(self._row_meta)):
            return
        it = self.table.item(row, 0)
        if it is None or not (it.flags() & Qt.ItemFlag.ItemIsUserCheckable):
            return
        new_state = Qt.CheckState.Unchecked if it.checkState() == Qt.CheckState.Checked else Qt.CheckState.Checked
        it.setCheckState(new_state)

    def batch_set_checked(self, mode: str) -> None:
        """批量快捷勾选操作：all / none / invert / safe_only / long_only。"""
        if self._busy or not self._row_meta:
            return
        self.table.blockSignals(True)
        for r, (_idx, kind, data) in enumerate(self._row_meta):
            if kind != "cut":
                continue
            it = self.table.item(r, 0)
            if it is None:
                continue
            cur = bool(data.get("enabled", True))
            if mode == "all":
                want = True
            elif mode == "none":
                want = False
            elif mode == "invert":
                want = not cur
            elif mode == "safe_only":
                want = data.get("status") != "warn"
            elif mode == "long_only":
                want = float(data.get("pause_s") or 0.0) >= 1.5
            else:
                want = cur
            data["enabled"] = want
            it.setCheckState(Qt.CheckState.Checked if want else Qt.CheckState.Unchecked)
        self.table.blockSignals(False)
        self._on_row_selected()
        self._refresh_summary_banner()

    def _play_wav_file(self, path: str) -> bool:
        """在 Windows 与跨平台环境下可靠播放试听 WAV 文件（无头测试环境自动静默记录）。"""
        import sys as _sys
        self._last_preview_wav = path
        if os.environ.get("PYTEST_CURRENT_TEST") or os.environ.get("QT_QPA_PLATFORM") == "offscreen":
            return True
        if _sys.platform == "win32":
            try:
                import winsound
                winsound.PlaySound(None, winsound.SND_PURGE)
                winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC)
                return True
            except Exception:
                pass
        try:
            from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
            if self._player is None:
                self._player = QMediaPlayer(self)
                self._audio_out = QAudioOutput(self)
                self._audio_out.setVolume(1.0)
                self._player.setAudioOutput(self._audio_out)
            self._player.stop()
            self._player.setSource(QUrl.fromLocalFile(path))
            self._player.play()
            return True
        except Exception:
            return False

    def preview_selected(self, *, after: bool = False) -> None:
        if self._busy:
            self.lbl_verify.setText("⏳ 正在智能分析气口中，分析完成后将自动为您播放试听…")
            self._pending_preview_after = bool(after)
            return
        if not self._plans:
            tasks = list(getattr(self.host, "tasks", None) or [])
            if not tasks:
                self.lbl_verify.setText("⚠ 当前工程中没有可试听的视频任务。")
                return
            self._pending_preview_after = bool(after)
            self.lbl_verify.setText("🔍 尚未分析气口切点，正在为您自动启动智能分析，完成后立即播放试听…")
            self.start_detect_async()
            return

        row = self.table.currentRow()
        if not (0 <= row < len(self._row_meta)) and self._row_meta:
            self.table.selectRow(0)
            row = 0

        tasks = list(getattr(self.host, "tasks", None) or [])
        if not (0 <= row < len(self._row_meta)):
            # 已分析但当前阈值下未检出可剪气口：试听第一条已分析视频的开头人声，给用户即时听觉反馈
            first_idx = next(iter(self._analyses.keys()), None)
            an = self._analyses.get(first_idx) if first_idx is not None else None
            if an is not None and len(getattr(an, "sig", [])) > 0:
                wav_arr = preview_wave(an.sig, 0.0, min(3.0, float(getattr(an, "end", 3.0) or 3.0)), applied_cut=None, pad=0.0)
                fd, path = tempfile.mkstemp(suffix=".wav", prefix="cutora_silence_prev_")
                os.close(fd)
                write_wav(path, wav_arr)
                self._tmp_wavs.append(path)
                self._play_wav_file(path)
                self.lbl_verify.setText(
                    "🔊 当前参数阈值下未检出超长停顿，正在试听原片开头人声（若需检出更短停顿，请调小上方「当停顿超过」数值后重新分析）。"
                )
            else:
                self.lbl_verify.setText("ℹ 当前列表没有可试听的气口切点，请调整上方阈值后点击「开始智能分析」。")
            return

        idx, kind, data = self._row_meta[row]
        if kind == "error":
            self.lbl_verify.setText(f"⚠ 该任务检测失败，无法试听：{data}")
            return
        an = self._analyses.get(idx)
        if an is None and 0 <= idx < len(tasks):
            try:
                an = analyze(tasks[idx], self.current_params(), cache=self._cache)
                self._analyses[idx] = an
            except Exception:
                an = None
        if an is None or len(getattr(an, "sig", [])) == 0:
            self.lbl_verify.setText("⚠ 未能读取该视频的音频波形，无法试听。")
            return
        t0 = float(data.get("pause_start") or data.get("start") or 0.0)
        t1 = float(data.get("pause_end") or data.get("end") or t0)
        cut_arg = data if (after and kind == "cut") else None
        pad_s = float(self.cb_zoom.currentData() or 1.8) if hasattr(self, "cb_zoom") else 1.8
        wav_arr = preview_wave(an.sig, t0, t1, applied_cut=cut_arg, pad=max(1.2, min(3.0, pad_s)))
        fd, path = tempfile.mkstemp(suffix=".wav", prefix="cutora_silence_prev_")
        os.close(fd)
        write_wav(path, wav_arr)
        self._tmp_wavs.append(path)
        self._play_wav_file(path)
        tname = os.path.basename(str((tasks[idx] if 0 <= idx < len(tasks) else {}).get("video") or f"任务 {idx + 1}"))
        if after and kind == "cut":
            rem_s = float(data.get("remove_s") or 0.0)
            res_s = float(data.get("result_s") or 0.0)
            self.lbl_verify.setText(
                f"🔊 正在试听【剪后接缝 (Space)】：#{idx + 1} {tname}  [{t0:.2f}s – {t1:.2f}s]  "
                f"已剪除 {rem_s:.2f}s ➔ 保留 {res_s:.2f}s 自然气口（含 5ms 防爆音微淡化）"
            )
        else:
            gap_s = float(data.get("pause_s") or data.get("gap_s") or (t1 - t0))
            self.lbl_verify.setText(
                f"🔊 正在试听【原停顿 (B)】：#{idx + 1} {tname}  [{t0:.2f}s – {t1:.2f}s]  "
                f"原始停顿时长 {gap_s:.2f}s（含切点前后各 {max(1.2, min(3.0, pad_s)):.1f}s 对白语境）"
            )

    def run_apply(self) -> dict:
        if self._busy:
            return {}
        tasks = list(getattr(self.host, "tasks", None) or [])
        params = self.current_params()
        if not self._plans:
            self.run_detect()
        if not self._plans:
            return {"applied_tasks": 0, "total_cuts": 0, "total_removed_s": 0.0, "verify_ok": 0}

        self._set_busy(True, "⏳ 正在执行全轨道无损波纹剪气口并运行词级自检…")
        QApplication.processEvents()

        undo = getattr(self.host, "push_undo", None) or getattr(self.host, "_push_undo_snapshot", None)
        if callable(undo):
            try:
                undo("剪气口")
            except TypeError:
                try:
                    undo()
                except Exception:
                    pass
            except Exception:
                pass

        applied_tasks = 0
        total_cuts = 0
        total_rem = 0.0
        verify_ok = 0
        max_db_err = 0.0
        worst_pause = 0.0

        plan_items = list(self._plans.items())
        total_p = max(1, len(plan_items))
        try:
            for pos, (idx, plan) in enumerate(plan_items):
                if not (0 <= idx < len(tasks)) or plan.get("error"):
                    continue
                self.progress_bar.setValue(int(round(((pos + 0.5) / total_p) * 100)))
                task = tasks[idx]
                an_before = self._analyses.get(idx)
                w_before = self._words_before.get(idx)
                res = apply_plan(task, plan, force=True)
                if res["n_cuts"] > 0:
                    applied_tasks += 1
                    total_cuts += res["n_cuts"]
                    total_rem += res["removed_s"]
                ver = verify_cut(
                    task,
                    res["applied_cuts"],
                    words_before=w_before,
                    sig_before=an_before.sig if an_before is not None else None,
                    floor_db=an_before.floor_db if an_before is not None else -70.0,
                    params=params,
                    cache=self._cache,
                )
                if ver["ok"]:
                    verify_ok += 1
                max_db_err = max(max_db_err, float(ver["audio_check"]["max_db_diff"]))
                worst_pause = max(worst_pause, float(ver["longest_pause_after_s"]))
                self.progress_bar.setValue(int(round(((pos + 1) / total_p) * 100)))
        finally:
            self._set_busy(False)

        msg = (
            f"✓ 已对 {applied_tasks} 条视频完成剪气口（共剪除 {total_cuts} 处停顿，累计缩短 {total_rem:.2f} 秒）。\n"
            f"✓ 自检结果：{verify_ok}/{len(self._plans)} 条通过词级与包络无损核验（最大包络偏差 {max_db_err:.2f} dB，剪后最长静音 {worst_pause:.2f}s）。"
        )
        self.lbl_verify.setText(msg)
        for fn_name in ("_refresh_after_timeline_edit", "update_timeline_ui", "update_preview"):
            fn = getattr(self.host, fn_name, None)
            if callable(fn):
                try:
                    fn()
                except Exception:
                    pass
        rebuild = getattr(self.host, "_rebuild_preview_wav", None)
        if callable(rebuild):
            try:
                rebuild(immediate=False)
            except Exception:
                pass
        self.run_detect()
        return {
            "applied_tasks": applied_tasks,
            "total_cuts": total_cuts,
            "total_removed_s": round(total_rem, 3),
            "verify_ok": verify_ok,
        }

    def closeEvent(self, event) -> None:
        if self._worker is not None and self._worker.isRunning():
            self._worker.request_stop()
            self._worker.wait(1500)
        try:
            import sys as _sys
            if _sys.platform == "win32":
                import winsound
                winsound.PlaySound(None, winsound.SND_PURGE)
        except Exception:
            pass
        if self._player is not None:
            try:
                self._player.stop()
            except Exception:
                pass
        for p in self._tmp_wavs:
            try:
                if os.path.isfile(p):
                    os.remove(p)
            except OSError:
                pass
        super().closeEvent(event)


def open_silence_cut_dialog(host, *, batch_default: bool = False) -> SilenceCutDialog:
    room_host = getattr(host, "room_host", None)
    if room_host is not None and hasattr(room_host, "rooms") and "silence" in room_host.rooms:
        room_host.enter("silence", batch_default=batch_default)
        room = room_host.rooms.get("silence")
        if room is not None and getattr(room, "widget", None) is not None:
            return room.widget
    dlg = SilenceCutDialog(host, parent=host, batch_default=batch_default)
    dlg.exec()
    return dlg
