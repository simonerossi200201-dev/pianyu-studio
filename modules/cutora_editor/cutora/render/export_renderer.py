# SPDX-License-Identifier: GPL-3.0-or-later
# Extracted from the T01 monolith by tools/split_monolith.py.
# Original lines: 2655-2760

import bisect
import time
from PySide6.QtCore import Qt
from PySide6.QtGui import (
    QPainter,
    QImage,
)
from cutora.render.frame_renderer import FrameRenderer, compute_export_render_order
from cutora.render import export_probe as _probe
from cutora.render.grade import apply_grade_to_frame


class ExportRenderer:

    @staticmethod
    def build_overlay_index(task_data):
        """把所有覆层的 [start, end] 区间预先收集并按 start 排序。

        [v6.1] 原来 _has_visible_overlay 每一帧都要线性扫描全部
        segments + text_overlays + image_overlays，复杂度 O(帧数 × 覆层数)。
        导出前构建一次索引，之后每帧改用二分查找即可。

        返回 (starts, max_end_prefix)：
            starts          —— 升序的区间起点列表
            max_end_prefix  —— max_end_prefix[i] = starts[0..i] 中最大的 end
        判断 t 是否落在任意区间内，等价于：
            在 starts 中找到最后一个 <= t 的下标 i，检查 max_end_prefix[i] >= t
        与原先的线性扫描**完全等价**。
        """
        intervals = []
        for key in ('segments', 'text_overlays', 'image_overlays'):
            for item in (task_data.get(key) or []):
                if not isinstance(item, dict):
                    continue
                try:
                    s = float(item.get('start', 0) or 0)
                    e = float(item.get('end', 0) or 0)
                except Exception:
                    continue
                intervals.append((s, e))
        if not intervals:
            return ([], [])
        intervals.sort(key=lambda x: x[0])
        starts = [s for s, _e in intervals]
        max_end_prefix = []
        running = float('-inf')
        for _s, e in intervals:
            if e > running:
                running = e
            max_end_prefix.append(running)
        return (starts, max_end_prefix)

    @staticmethod
    def _has_visible_overlay(task_data, t, overlay_index=None):
        """快速判断当前时刻是否有任何可见覆层（字幕/文本/图片）需要绘制。
        返回 False 时可完全跳过 draw_layers，节省大量渲染开销。

        overlay_index 由 build_overlay_index() 预先构建；传入时走二分查找，
        不传时退回原来的线性扫描（保证任何旧调用点都不受影响）。
        """
        if overlay_index is not None:
            starts, max_end_prefix = overlay_index
            if not starts:
                return False
            # 最后一个 start <= t 的下标
            i = bisect.bisect_right(starts, t) - 1
            if i < 0:
                return False
            return max_end_prefix[i] >= t

        # ── 回退路径：与 v6.0 行为完全一致 ──
        segments = task_data.get('segments', [])
        for seg in segments:
            if seg.get('start', 0) <= t <= seg.get('end', 0):
                return True
        for ov in (task_data.get('text_overlays') or []):
            if ov.get('start', 0) <= t <= ov.get('end', 0):
                return True
        for ov in (task_data.get('image_overlays') or []):
            if ov.get('start', 0) <= t <= ov.get('end', 0):
                return True
        return False

    @staticmethod
    def render_frame(task_data, w, h, t, draw_background=None, render_order=None,
                     canvas=None, fast=False, overlay_index=None, backdrop=None,
                     paint_video_band=None, stack=None):
        """渲染一帧。
        canvas: 可复用的 QImage 画布（避免每帧重新分配内存）。
        字幕/覆层直接在画布上绘制，确保所有动画每帧正确渲染。
        当检测到无任何可见覆层时，跳过 draw_layers 调用以加速。
        overlay_index: 可选，由 build_overlay_index() 预构建的区间索引。
        backdrop: 视频帧 QImage，供 v2 fx.bg.glass 取样。
        """
        if render_order is None:
            render_order = compute_export_render_order(task_data)
        if paint_video_band is not None and isinstance(task_data, dict):
            # Q163：视频压在覆层上面 / 覆层类型交错 → 按时间线逐层画（否则走下面的老路，逐字节不变）
            from cutora.render.visual_stack import build_stack, needs_stack
            st = stack if stack is not None else build_stack(task_data)
            if needs_stack(task_data, stack=st):
                return ExportRenderer._render_stacked(
                    task_data, w, h, t, st, paint_video_band, canvas, fast, backdrop)
        if canvas is None or canvas.width() != w or canvas.height() != h:
            canvas = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
        _t_other = time.perf_counter() if _probe.on() else 0.0
        canvas.fill(Qt.GlobalColor.black)
        painter = QPainter(canvas)
        try:
            # Fast 导出：关闭昂贵的渲染 hint（对字幕/混合模式影响很小，但能显著提速）
            ExportRenderer._apply_hints(painter, fast)
            if _probe.on():
                _probe.add_ms("other", time.perf_counter() - _t_other)
            if draw_background:
                draw_background(painter)
            if backdrop is None and isinstance(task_data, dict):
                backdrop = task_data.pop("_fx_backdrop", None)
            if backdrop is None and ExportRenderer._frame_needs_glass(task_data, t):
                # Video pixels are already on the canvas. End so copy() is not
                # reading a paint device, then keep drawing overlays on top.
                painter.end()
                backdrop = canvas.copy()
                painter = QPainter(canvas)
                ExportRenderer._apply_hints(painter, fast)

            # ── 快速跳过：无可见覆层时不调用 draw_layers ──
            if ExportRenderer._has_visible_overlay(task_data, t, overlay_index):
                FrameRenderer.draw_layers(painter, task_data, w, h, t,
                                          render_order=render_order,
                                          backdrop=backdrop)
        finally:
            t_end = time.perf_counter() if _probe.on() else 0.0
            if painter.isActive():
                painter.end()
            if _probe.on():
                _probe.add_ms("other", time.perf_counter() - t_end)
        return apply_grade_to_frame(canvas, task_data, t)

    @staticmethod
    def _render_stacked(task_data, w, h, t, st, paint_video_band, canvas, fast, backdrop):
        from cutora.render.visual_stack import paint_stack
        if canvas is None or canvas.width() != w or canvas.height() != h:
            canvas = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
        canvas.fill(Qt.GlobalColor.black)
        if backdrop is None:
            backdrop = task_data.pop("_fx_backdrop", None)
        need_glass = backdrop is None and ExportRenderer._frame_needs_glass(task_data, t)
        state = {"painter": QPainter(canvas)}
        ExportRenderer._apply_hints(state["painter"], fast)

        def _bd_after_first(p):
            if not need_glass:
                return None
            p.end()
            img = canvas.copy()
            p.begin(canvas)
            ExportRenderer._apply_hints(p, fast)
            return img
        try:
            paint_stack(state["painter"], task_data, w, h, t, paint_video_band, stack=st,
                        backdrop=backdrop, backdrop_after_first_band=_bd_after_first)
        finally:
            if state["painter"].isActive():
                state["painter"].end()
        return apply_grade_to_frame(canvas, task_data, t)

    @staticmethod
    def _apply_hints(painter, fast):
        if fast:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
            painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, False)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        else:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

    @staticmethod
    def _frame_needs_glass(task_data, t):
        if not isinstance(task_data, dict):
            return False
        from cutora.render.textfx.spec import frame_has_glass_bg
        return frame_has_glass_bg(task_data, t)
