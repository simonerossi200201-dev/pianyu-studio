# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q145 · 主画布的快捷操作（和中央监视器同一套手势语法）。

VideoCanvas 继承这个 mixin。这里放：
- 视图缩放 / 平移（Ctrl+= / − / 0、Shift+Z、Alt+滚轮、中键 / 按住 H 拖）；
- 焦点路由：画布有选中对象时，方向键 / Delete / Ctrl+C 等归画布，不被全局快捷键抢走；
- 方向键微移（Shift ×10，一串按键 = 一次撤销）、Delete、Ctrl+C / X / V / D、Ctrl+A、Esc；
- Alt+拖复制、Ctrl+拖 / Ctrl+滚轮以光标为中心缩放、双击视频重置；
- 剪贴板和中央监视器共用（monitor_model 里那一份），可以跨视频粘贴。

设计说明见 docs/plan/Q145_SHORTCUTS_DESIGN.md。
"""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPen

from cutora.core.log import get_logger

_LOG = get_logger("cutora.ui.canvas_ops")

VIEW_MIN = 0.25          # 相对「适应窗口」的倍数
VIEW_MAX = 32.0
NUDGE_IDLE_MS = 700      # 这么久没再按方向键 / 滚轮，就把这一串算成一次撤销
ZOOM_DRAG_GAIN = 0.006   # Ctrl+拖：每像素 e^0.006 倍
PASTE_OFFSET = 20.0
SPLIT_SNAP_PX = 8.0      # 分界线吸附（屏幕像素）
SPLIT_SNAPS = (1.0 / 3.0, 0.5, 2.0 / 3.0)

_ARROWS = {
    Qt.Key.Key_Left: (-1, 0), Qt.Key.Key_Right: (1, 0),
    Qt.Key.Key_Up: (0, -1), Qt.Key.Key_Down: (0, 1),
}


def _mods(ev):
    m = ev.modifiers()
    return (bool(m & Qt.KeyboardModifier.ControlModifier),
            bool(m & Qt.KeyboardModifier.ShiftModifier),
            bool(m & Qt.KeyboardModifier.AltModifier))


class CanvasOpsMixin:
    """需要宿主（VideoCanvas）提供：_task_data、_video_dims、_hit_records、_selected_item、
    _selected_rect、_multi 系列方法、_nudge_record、_discrete、_commit_host_param、
    _nudge_host_param、_host_float，以及信号 item_selected / item_dragged / gesture_began /
    gesture_ended / structure_changed / gesture_cancelled / status_message。"""

    # ═════════════════════════ 初始化 ═════════════════════════

    def _ops_init(self):
        self._view_zoom = 1.0          # 相对「适应窗口」
        self._view_center = None       # 视图中心对准的画布坐标；None = 画面居中
        self._view_dims = None
        self._view_pan = None          # 中键 / H 拖动平移中：上一个窗口坐标
        self._hand_hold = False
        self._nudge_timer = QTimer(self)
        self._nudge_timer.setSingleShot(True)
        self._nudge_timer.setInterval(NUDGE_IDLE_MS)
        self._nudge_timer.timeout.connect(self._ops_end_session)
        self._ops_session = False
        self._alt_dup_pending = False
        self._structure_dirty = False
        self._zoom_drag = None
        self._paste_serial = 0
        self._paste_key = None
        self._ops_hud = ""
        self._ops_hud_timer = QTimer(self)
        self._ops_hud_timer.setSingleShot(True)
        self._ops_hud_timer.setInterval(1200)
        self._ops_hud_timer.timeout.connect(self._ops_clear_hud)

    # ═════════════════════════ 视图缩放 / 平移 ═════════════════════════

    def _apply_view(self, base: QRectF) -> QRectF:
        """「适应窗口」的矩形 → 当前视图下的矩形。视图没缩放时原样返回（坐标换算一字不变）。"""
        dims = tuple(self._video_dims)
        if self._view_dims != dims:
            self._view_dims = dims
            self._view_zoom = 1.0
            self._view_center = None
        if abs(self._view_zoom - 1.0) < 1e-9 and self._view_center is None:
            return base
        img_w, img_h = float(dims[0] or 1), float(dims[1] or 1)
        w = base.width() * self._view_zoom
        h = base.height() * self._view_zoom
        cx, cy = self._view_center if self._view_center is not None else (img_w / 2.0, img_h / 2.0)
        sx = w / img_w
        sy = h / img_h
        x = self.width() / 2.0 - cx * sx
        y = self.height() / 2.0 - cy * sy
        return QRectF(x, y, w, h)

    def _fit_rect(self) -> QRectF:
        img_w, img_h = self._video_dims
        if img_w <= 0 or img_h <= 0:
            return QRectF(0, 0, 0, 0)
        scale = min(self.width() / img_w, self.height() / img_h)
        dw, dh = img_w * scale, img_h * scale
        return QRectF((self.width() - dw) / 2, (self.height() - dh) / 2, dw, dh)

    def view_zoom_percent(self) -> float:
        fit = self._fit_rect()
        img_w = float(self._video_dims[0] or 1)
        return fit.width() * self._view_zoom / img_w * 100.0

    def _clamp_view_center(self):
        if self._view_center is None:
            return
        img_w, img_h = float(self._video_dims[0] or 1), float(self._video_dims[1] or 1)
        cx, cy = self._view_center
        self._view_center = (max(0.0, min(img_w, cx)), max(0.0, min(img_h, cy)))

    def view_zoom_at(self, factor: float, wpos=None):
        """以窗口坐标 wpos 为不动点缩放视图（None = 窗口中心）。"""
        old = self._preview_target_rect()
        if old.width() <= 0:
            return
        if wpos is None:
            wpos = QPointF(self.width() / 2.0, self.height() / 2.0)
        img_w, img_h = float(self._video_dims[0] or 1), float(self._video_dims[1] or 1)
        px = (wpos.x() - old.x()) * img_w / old.width()
        py = (wpos.y() - old.y()) * img_h / old.height()
        z = max(VIEW_MIN, min(VIEW_MAX, self._view_zoom * float(factor)))
        if abs(z - 1.0) < 0.04 and ((self._view_zoom > 1.0) != (z > 1.0) or abs(z - 1.0) < 0.02):
            self.view_fit()
            return
        self._view_zoom = z
        fit = self._fit_rect()
        s = fit.width() * z / img_w
        # 让 (px, py) 仍在 wpos：wpos = W/2 - c*s + p*s
        cx = px - (wpos.x() - self.width() / 2.0) / s
        cy = py - (wpos.y() - self.height() / 2.0) / s
        self._view_center = (cx, cy)
        self._clamp_view_center()
        self._ops_show_hud("视图 %d%%" % round(self.view_zoom_percent()))
        self.update()

    def view_fit(self):
        self._view_zoom = 1.0
        self._view_center = None
        self._ops_show_hud("视图：适应窗口")
        self.update()

    def view_actual(self):
        """100%：一个工程像素 = 一个屏幕像素。"""
        fit = self._fit_rect()
        if fit.width() <= 0:
            return
        self._view_zoom = max(VIEW_MIN, min(VIEW_MAX, float(self._video_dims[0]) / fit.width()))
        if self._view_center is None:
            self._view_center = (self._video_dims[0] / 2.0, self._video_dims[1] / 2.0)
        self._ops_show_hud("视图 100%")
        self.update()

    def view_pan(self, dx_w: float, dy_w: float):
        """按窗口像素平移视图。"""
        rect = self._preview_target_rect()
        if rect.width() <= 0:
            return
        img_w, img_h = float(self._video_dims[0] or 1), float(self._video_dims[1] or 1)
        if self._view_center is None:
            self._view_center = (img_w / 2.0, img_h / 2.0)
        cx, cy = self._view_center
        self._view_center = (cx - dx_w * img_w / rect.width(), cy - dy_w * img_h / rect.height())
        self._clamp_view_center()
        self.update()

    def view_is_zoomed(self) -> bool:
        return abs(self._view_zoom - 1.0) > 1e-9 or self._view_center is not None

    def _paint_view_badge(self, painter):
        """视图不是「适应窗口」时，左上角挂一个小牌子：倍率 + 怎么回去。"""
        if not self.view_is_zoomed() and not self._ops_hud:
            return
        from cutora.ui.theme.tokens import FONT_LABEL, RADIUS_CTL, current
        tok = current()
        texts = []
        if self.view_is_zoomed():
            texts.append("%d%%  ·  Shift+Z 适应  ·  中键拖动平移" % round(self.view_zoom_percent()))
        if self._ops_hud:
            texts.append(self._ops_hud)
        f = QFont(self.font())
        f.setPixelSize(FONT_LABEL)
        painter.save()
        try:
            painter.setFont(f)
            fm = QFontMetrics(f)
            y = 10.0
            for text in texts:
                w = fm.horizontalAdvance(text) + 16
                h = fm.height() + 8
                r = QRectF(10.0, y, w, h)
                bg = QColor(tok.bg1)
                bg.setAlpha(225)
                painter.setPen(QPen(QColor(tok.accent), 1))
                painter.setBrush(bg)
                painter.drawRoundedRect(r, RADIUS_CTL, RADIUS_CTL)
                painter.setPen(QColor(tok.fg0))
                painter.drawText(r, Qt.AlignmentFlag.AlignCenter, text)
                y += h + 6
        finally:
            painter.restore()

    def _ops_show_hud(self, text):
        self._ops_hud = str(text or "")
        self._ops_hud_timer.start()
        self.update()

    def _ops_clear_hud(self):
        self._ops_hud = ""
        self.update()

    # ═════════════════════════ 焦点路由 ═════════════════════════

    def _ops_editing_text(self) -> bool:
        return getattr(self, "_wysiwyg", None) is not None or getattr(self, "_inline_editor", None) is not None

    def _ops_targets(self):
        """(文字 / 贴纸记录列表, 视频片段记录或 None)。"""
        recs = list(self.selected_records())
        vid = self._selected_item if (self._selected_item is not None
                                      and self._selected_item.get("type") == "video_clip") else None
        return recs, vid

    def _ops_has_selection(self) -> bool:
        recs, vid = self._ops_targets()
        return bool(recs) or vid is not None

    def _ops_wants(self, ev) -> bool:
        """ShortcutOverride：这个键画布要不要自己处理（要 → 全局快捷键不触发）。"""
        if self._ops_editing_text() or not self._task_data:
            return False
        key = ev.key()
        ctrl, shift, alt = _mods(ev)
        if key in _ARROWS and not ctrl and not alt:
            return self._ops_has_selection()
        if key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace) and not ctrl:
            return bool(self.selected_records())
        if key == Qt.Key.Key_Escape:
            return (self._dragging_item is not None or self._ops_has_selection()
                    or getattr(self, "_split_drag", None) is not None)
        if ctrl and not alt:
            if key in (Qt.Key.Key_C, Qt.Key.Key_X, Qt.Key.Key_D):
                return bool(self.selected_records())
            if key == Qt.Key.Key_V:
                from cutora.batch.monitor_model import get_clipboard
                clip = get_clipboard()
                return bool(clip) and clip.get("kind") == "overlays"
            if key == Qt.Key.Key_A:
                return True
            if key in (Qt.Key.Key_Equal, Qt.Key.Key_Plus, Qt.Key.Key_Minus, Qt.Key.Key_0):
                return True
        if key == Qt.Key.Key_Z and shift and not ctrl and not alt:
            return True
        return False

    def _ops_key(self, ev) -> bool:
        """画布自己的快捷键。处理了返回 True。"""
        if self._ops_editing_text() or not self._task_data:
            return False
        key = ev.key()
        ctrl, shift, alt = _mods(ev)
        if key == Qt.Key.Key_H and not ev.isAutoRepeat() and not ctrl and not alt:
            self._hand_hold = True
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            return True
        if key in _ARROWS and not ctrl and not alt:
            if not self._ops_has_selection():
                return False
            dx, dy = _ARROWS[key]
            step = 10.0 if shift else 1.0
            self.nudge_selection(dx * step, dy * step)
            return True
        if key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace) and not ctrl:
            return self.delete_selection()
        if key == Qt.Key.Key_Escape:
            return self.escape()
        if ctrl and not alt:
            if key == Qt.Key.Key_C:
                return self.copy_selection()
            if key == Qt.Key.Key_X:
                return self.cut_selection()
            if key == Qt.Key.Key_V:
                return self.paste_clipboard()
            if key == Qt.Key.Key_D:
                return self.duplicate_selection()
            if key == Qt.Key.Key_A:
                return self.select_all_items()
            if key in (Qt.Key.Key_Equal, Qt.Key.Key_Plus):
                self.view_zoom_at(1.25)
                return True
            if key == Qt.Key.Key_Minus:
                self.view_zoom_at(0.8)
                return True
            if key == Qt.Key.Key_0:
                self.view_actual()
                return True
        if key == Qt.Key.Key_Z and shift and not ctrl and not alt:
            self.view_fit()
            return True
        return False

    def _ops_key_release(self, ev) -> bool:
        if ev.key() == Qt.Key.Key_H and not ev.isAutoRepeat():
            self._hand_hold = False
            if self._view_pan is None:
                self.unsetCursor()
            return True
        return False

    # ═════════════════════════ 连续操作 = 一次撤销 ═════════════════════════

    def _ops_begin_session(self, label):
        if not self._ops_session:
            self._ops_session = True
            self._gesture_label = str(label)
            self.gesture_began.emit()
        self._nudge_timer.start()

    def _ops_end_session(self):
        if not self._ops_session:
            return
        self._ops_session = False
        self._nudge_timer.stop()
        self.gesture_ended.emit()
        self._gesture_label = None

    # ═════════════════════════ 微移 ═════════════════════════

    def nudge_selection(self, dx: float, dy: float) -> bool:
        recs, vid = self._ops_targets()
        if not recs and vid is None:
            return False
        self._ops_begin_session("微移")
        for rec in recs:
            self._nudge_record(rec, dx, dy)
        if vid is not None:
            clip = vid.get("clip")
            if clip is not None:
                self._nudge_host_param(clip, "x", dx, getattr(clip, "x", 0.0),
                                       lambda v, _c=clip: setattr(_c, "x", v))
                self._nudge_host_param(clip, "y", dy, getattr(clip, "y", 0.0),
                                       lambda v, _c=clip: setattr(_c, "y", v))
                r = vid.get("rect")
                if r is not None:
                    vid["rect"] = r.translated(dx, dy)
        if self._selected_rect is not None and self._selected_item is not None \
                and self._selected_item.get("type") in ("overlay", "sticker", "video_clip"):
            live = self._live_record(self._selected_item)
            self._selected_rect = live.get("rect") or self._selected_rect.translated(dx, dy)
        self.item_dragged.emit()
        self._ops_show_hud("微移 %+d, %+d" % (dx, dy))
        self.update()
        return True

    # ═════════════════════════ 删除 / 复制 / 粘贴 ═════════════════════════

    def _ops_selected_data(self):
        """选中的文字 / 贴纸 → [(kind, list_name, idx, el_idx, data)]。"""
        out = []
        for rec in self.selected_records():
            if rec.get("type") == "overlay":
                d = self._record_data(rec)
                if isinstance(d, dict):
                    out.append(("overlay", rec.get("list"), rec.get("idx"), None, d))
            elif rec.get("type") == "sticker":
                try:
                    ov = self._task_data["image_overlays"][rec.get("ov_idx")]
                except Exception:
                    continue
                out.append(("sticker", "image_overlays", rec.get("ov_idx"), rec.get("el_idx"), ov))
        return out

    def _ops_clear_selection(self):
        self._multi_clear()
        self._selected_item = None
        self._selected_rect = None
        self.item_selected.emit("", None)

    def delete_selection(self) -> bool:
        rows = self._ops_selected_data()
        if not rows:
            return False
        task = self._task_data

        def _go():
            kill = {}               # list_name → {idx}
            el_kill = {}            # ov_idx → {el_idx}
            for kind, name, idx, el_idx, _d in rows:
                if kind == "overlay":
                    kill.setdefault(name, set()).add(idx)
                else:
                    el_kill.setdefault(idx, set()).add(el_idx)
            for ov_idx, els in el_kill.items():
                try:
                    ov = task["image_overlays"][ov_idx]
                except Exception:
                    continue
                elements = ov.get("elements") or []
                for e in sorted(els, reverse=True):
                    if 0 <= e < len(elements):
                        del elements[e]
                if not elements:
                    kill.setdefault("image_overlays", set()).add(ov_idx)
            n = 0
            for name, idxs in kill.items():
                lst = task.get(name) or []
                for i in sorted(idxs, reverse=True):
                    if 0 <= i < len(lst):
                        del lst[i]
                        n += 1
            return bool(n or el_kill)

        self._ops_clear_selection()
        changed = self._discrete("删除", _go)
        if changed:
            self.structure_changed.emit()
            self._ops_show_hud("已删除 %d 个（Ctrl+Z 撤销）" % len(rows))
        return True

    def _ops_clip_items(self):
        from cutora.batch.monitor_model import _plain_copy
        items = []
        for kind, _name, _idx, el_idx, d in self._ops_selected_data():
            if kind == "overlay":
                items.append((self._task_data, _plain_copy(d)))
            else:
                ov = _plain_copy(d)
                els = d.get("elements") or []
                if 0 <= el_idx < len(els):
                    ov["elements"] = [_plain_copy(els[el_idx])]
                items.append((self._task_data, ov))
        return items

    def copy_selection(self) -> bool:
        from cutora.batch.monitor_model import set_clipboard
        items = self._ops_clip_items()
        if not items:
            return False
        set_clipboard({"kind": "overlays", "items": items})
        self._paste_serial = 0
        self._ops_show_hud("已复制 %d 个 —— Ctrl+V 粘贴（可以粘到别的视频）" % len(items))
        return True

    def cut_selection(self) -> bool:
        if not self.copy_selection():
            return False
        self.delete_selection()
        self._ops_show_hud("已剪切 —— Ctrl+V 粘贴")
        return True

    def _ops_insert_copies(self, items, offset, label):
        from cutora.batch import monitor_model as mm
        task = self._task_data
        new_recs = []

        def _go():
            for src_task, ov in items:
                off = offset if src_task is task else 0.0
                new = mm.copy_overlay_into(src_task, ov, task, dx=off, dy=off)
                name = "text_overlays" if mm.overlay_kind(new) == "text" else "image_overlays"
                idx = len(task.get(name) or []) - 1
                x, y, w, h = mm.overlay_bounds(task, new)
                if name == "image_overlays" and new.get("elements"):
                    new_recs.append({"type": "sticker", "ov_idx": idx, "el_idx": 0,
                                     "rect": QRectF(x, y, w, h)})
                else:
                    new_recs.append({"type": "overlay", "list": name, "idx": idx,
                                     "rect": QRectF(x, y, w, h)})
            return bool(new_recs)

        changed = self._discrete(label, _go)
        if changed:
            self._multi_set(new_recs)
            self._selected_item = new_recs[-1]
            self._selected_rect = new_recs[-1].get("rect")
            top = new_recs[-1]
            if top["type"] == "overlay":
                self.item_selected.emit("overlay", (top["list"], top["idx"]))
            else:
                self.item_selected.emit("sticker", (top["ov_idx"], top["el_idx"]))
            self.structure_changed.emit()
        return changed

    def paste_clipboard(self) -> bool:
        from cutora.batch.monitor_model import get_clipboard
        clip = get_clipboard()
        if not clip or clip.get("kind") != "overlays" or not clip.get("items"):
            return False
        if self._paste_key is not clip:
            self._paste_key = clip
            self._paste_serial = 0
        self._paste_serial += 1
        ok = self._ops_insert_copies(clip["items"], PASTE_OFFSET * self._paste_serial, "粘贴")
        if ok:
            self._ops_show_hud("已粘贴 %d 个" % len(clip["items"]))
        return True

    def duplicate_selection(self) -> bool:
        items = self._ops_clip_items()
        if not items:
            return False
        if self._ops_insert_copies(items, PASTE_OFFSET, "原地复制"):
            self._ops_show_hud("已复制一份（Ctrl+D）")
        return True

    def select_all_items(self) -> bool:
        recs = [r for r in (self._hit_records or []) if r.get("type") in self.MULTI_TYPES]
        if not recs:
            return True
        self._multi_set(recs)
        self._selected_item = recs[-1]
        self._selected_rect = recs[-1].get("rect")
        self._ops_show_hud("已选 %d 个" % len(recs))
        return True

    def escape(self) -> bool:
        if getattr(self, "_split_drag", None) is not None:
            drag = self._split_drag
            self._split_drag = None
            orig = {}
            if drag.get("orig_pos") is not None:
                orig["pos"] = drag["orig_pos"]
            if drag.get("orig_rot") is not None:
                orig["rotation_deg"] = drag["orig_rot"]
            if orig and isinstance(self._task_data, dict):
                from cutora.render.split_layout import set_divider_on_task
                set_divider_on_task(self._task_data, drag["id"], orig)
            self._ops_show_hud("已取消")
            self.update()
            return True
        if self._dragging_item is not None:
            self._dragging_item = None
            self._drag_mode = None
            self._resize_handle = None
            self._zoom_drag = None
            self._alt_dup_pending = False
            self._snap_lines = []
            self._drag_hud = None
            self._ops_clear_selection()
            self.gesture_cancelled.emit()
            self._ops_show_hud("已取消")
            self.update()
            return True
        if self._ops_has_selection():
            self._ops_clear_selection()
            self.update()
            return True
        return False

    # ═════════════════════════ Alt+拖复制 ═════════════════════════

    def _ops_alt_duplicate(self):
        """Alt 按下后第一次拖动：把选中的（或刚点的）文字 / 贴纸复制一份，改为拖副本。"""
        self._alt_dup_pending = False
        from cutora.batch import monitor_model as mm
        task = self._task_data
        primary = self._dragging_item
        members = list(self._multi) if (self._multi and self._multi_contains(primary)) else [primary]
        new_recs = []
        new_primary = None
        for rec in members:
            rec = self._live_record(rec)
            nr = None
            if rec.get("type") == "overlay":
                d = self._record_data(rec)
                if not isinstance(d, dict):
                    continue
                name = rec.get("list")
                mm.copy_overlay_into(task, d, task)
                nr = dict(rec)
                nr["idx"] = len(task.get(name) or []) - 1
            elif rec.get("type") == "sticker":
                try:
                    ov = task["image_overlays"][rec.get("ov_idx")]
                    el = (ov.get("elements") or [])[rec.get("el_idx")]
                except Exception:
                    continue
                ov["elements"].append(mm._plain_copy(el))
                nr = dict(rec)
                nr["el_idx"] = len(ov["elements"]) - 1
            if nr is None:
                continue
            new_recs.append(nr)
            if self._same_item(rec, primary):
                new_primary = nr
        if not new_recs:
            return
        if new_primary is None:
            new_primary = new_recs[-1]
        if len(new_recs) > 1:
            self._multi_set(new_recs)
        else:
            self._multi_clear()
        self._dragging_item = new_primary
        self._selected_item = new_primary
        self._structure_dirty = True
        self._ops_show_hud("复制中（松开 Alt 也会保留副本）")

    # ═════════════════════════ Ctrl+拖 / Ctrl+滚轮 缩放 ═════════════════════════

    def _ops_video_geom(self, rec):
        """视频片段 → (格子中心 cc, 轨道平移 t, 轨道缩放 s, 片段中心 c0)。都在画布坐标。"""
        clip = rec.get("clip")
        tw, th = self._video_dims
        box = rec.get("cell") or (0.0, 0.0, float(tw), float(th))
        cc = (box[0] + box[2] / 2.0, box[1] + box[3] / 2.0)
        tr = rec.get("track")
        t = (0.0, 0.0)
        s = 1.0
        if tr is not None:
            from cutora.render.track_transform import track_props_of
            p = track_props_of(tr)
            t = (float(p["x"]), float(p["y"]))
            s = float(p["scale"]) or 1.0
        x = self._host_float(clip, "x", getattr(clip, "x", 0.0))
        y = self._host_float(clip, "y", getattr(clip, "y", 0.0))
        return cc, t, s, (cc[0] + x, cc[1] + y)

    def _ops_scale_video(self, rec, base, f, mx, my):
        """以画布点 (mx, my) 为不动点把视频片段缩放到 base.scale × f。"""
        clip = rec.get("clip")
        if clip is None:
            return 1.0
        s0, x0, y0 = base
        s1 = max(0.02, min(50.0, s0 * f))
        if abs(s1 - 1.0) < 0.03 and abs(s0 - 1.0) > 1e-6:
            s1 = 1.0
        f = s1 / s0
        cc, t, s_tr, _c = self._ops_video_geom(rec)
        # 光标点换到轨道变换之前的格子坐标
        lx = cc[0] + (mx - cc[0] - t[0]) / s_tr
        ly = cc[1] + (my - cc[1] - t[1]) / s_tr
        c0x, c0y = cc[0] + x0, cc[1] + y0
        nx = x0 + (1.0 - f) * (lx - c0x)
        ny = y0 + (1.0 - f) * (ly - c0y)
        self._commit_host_param(clip, "scale", s1, lambda v, _c=clip: setattr(_c, "scale", v))
        self._commit_host_param(clip, "x", nx, lambda v, _c=clip: setattr(_c, "x", v))
        self._commit_host_param(clip, "y", ny, lambda v, _c=clip: setattr(_c, "y", v))
        return s1

    def _ops_zoom_base(self, rec):
        from cutora.batch.monitor_model import _plain_copy
        if rec.get("type") == "video_clip":
            clip = rec.get("clip")
            return ("video", (self._host_float(clip, "scale", getattr(clip, "scale", 1.0)),
                              self._host_float(clip, "x", getattr(clip, "x", 0.0)),
                              self._host_float(clip, "y", getattr(clip, "y", 0.0))))
        d = self._record_data(rec)
        if rec.get("type") == "overlay" and isinstance(d, dict):
            return ("overlay", _plain_copy({k: d.get(k) for k in ("x", "y", "w", "h", "scale")}))
        if rec.get("type") == "sticker" and isinstance(d, dict):
            return ("sticker", _plain_copy({k: d.get(k) for k in ("x", "y", "w", "h")}))
        return (None, None)

    def _ops_apply_zoom(self, rec, base, f, mx, my):
        """按 base 状态缩放 f 倍。返回给 HUD 的百分比。"""
        kind, b = base
        if kind == "video":
            return self._ops_scale_video(rec, b, f, mx, my) * 100.0
        d = self._record_data(rec)
        if not isinstance(d, dict):
            return 100.0
        if kind == "overlay":
            for k, v in b.items():
                if v is None:
                    d.pop(k, None)
                else:
                    d[k] = v
            s0 = float(b.get("scale") or 1.0)
            if str(d.get("type") or "text") == "text" or rec.get("list") == "text_overlays":
                from cutora.batch.monitor_model import MAX_OV_SCALE, MIN_OV_SCALE
                s1 = max(MIN_OV_SCALE, min(MAX_OV_SCALE, s0 * f))
                if abs(s1 - 1.0) < 0.03:
                    s1 = 1.0
                x, y, w, h = float(b["x"] or 0), float(b["y"] or 0), float(b["w"] or 0), float(b["h"] or 0)
                g = s1 / s0
                cx, cy = x + w / 2.0, y + h / 2.0
                ncx, ncy = mx + (cx - mx) * g, my + (cy - my) * g
                d["scale"] = s1
                d["x"] = ncx - w / 2.0
                d["y"] = ncy - h / 2.0
                return s1 * 100.0
            x, y, w, h = float(b["x"] or 0), float(b["y"] or 0), float(b["w"] or 0), float(b["h"] or 0)
            d["x"] = mx + (x - mx) * f
            d["y"] = my + (y - my) * f
            d["w"] = max(4.0, w * f)
            d["h"] = max(4.0, h * f)
            return f * 100.0
        if kind == "sticker":
            try:
                ov = self._task_data["image_overlays"][rec.get("ov_idx")]
                ox, oy = float(ov.get("x") or 0), float(ov.get("y") or 0)
            except Exception:
                ox, oy = 0.0, 0.0
            x, y, w, h = float(b["x"] or 0), float(b["y"] or 0), float(b["w"] or 0), float(b["h"] or 0)
            lmx, lmy = mx - ox, my - oy
            if w * f < 4 or h * f < 4:
                return f * 100.0
            d["x"] = lmx + (x - lmx) * f
            d["y"] = lmy + (y - lmy) * f
            d["w"] = w * f
            d["h"] = h * f
            return f * 100.0
        return 100.0

    def _ops_anchor_for(self, rec, vx, vy):
        """缩放的不动点：视频 = 光标；文字 / 图片 = 自己的中心（拖一下不会跑位）。"""
        if rec.get("type") == "video_clip":
            return vx, vy
        r = rec.get("rect")
        if r is not None:
            return r.center().x(), r.center().y()
        return vx, vy

    def _ops_begin_zoom(self, rec, wpos, vx, vy):
        base = self._ops_zoom_base(rec)
        if base[0] is None:
            self._zoom_drag = None
            return False
        ax, ay = self._ops_anchor_for(rec, vx, vy)
        self._zoom_drag = {"rec": rec, "base": base, "press": QPointF(wpos), "ax": ax, "ay": ay,
                           "moved": False}
        return True

    def _ops_zoom_move(self, wpos):
        zd = self._zoom_drag
        if zd is None:
            return
        d = (wpos.x() - zd["press"].x()) - (wpos.y() - zd["press"].y())
        if not zd["moved"] and abs(d) < 2:
            return
        zd["moved"] = True
        f = math.exp(d * ZOOM_DRAG_GAIN)
        pct = self._ops_apply_zoom(zd["rec"], zd["base"], f, zd["ax"], zd["ay"])
        self._drag_hud = None
        self._ops_show_hud("缩放 %d%%" % round(pct))
        self.item_dragged.emit()
        self.update()

    def _ops_wheel(self, ev) -> bool:
        """Alt+滚轮 = 视图缩放；Ctrl+滚轮 = 缩放光标下已选中的对象（没有就缩放视图）；
        视图放大后，滚轮 / Shift+滚轮 = 上下 / 左右平移。"""
        ad = ev.angleDelta()
        delta = ad.y() or ad.x()
        if not delta:
            return False
        ctrl, shift, alt = _mods(ev)
        steps = delta / 120.0
        pos = ev.position()
        if alt and not ctrl:
            self.view_zoom_at(1.15 ** steps, pos)
            return True
        if ctrl:
            rec = self._ops_selected_under(pos)
            if rec is None:
                self.view_zoom_at(1.15 ** steps, pos)
                return True
            xy = self._video_xy(pos)
            if xy is None:
                return True
            key = ("wheel", self._rec_key(rec))
            if not self._ops_session or getattr(self, "_wheel_key", None) != key:
                self._ops_end_session()
                self._wheel_key = key
                self._wheel_base = self._ops_zoom_base(rec)
                self._wheel_f = 1.0
                self._wheel_anchor = self._ops_anchor_for(rec, xy[0], xy[1])
            self._ops_begin_session("缩放")
            self._wheel_f *= 1.1 ** steps
            ax, ay = self._wheel_anchor if rec.get("type") != "video_clip" else xy
            if rec.get("type") == "video_clip":
                # 视频：每一格以当前光标为中心，接着上一格的结果缩放
                self._wheel_base = self._ops_zoom_base(rec)
                pct = self._ops_apply_zoom(rec, self._wheel_base, 1.1 ** steps, ax, ay)
            else:
                pct = self._ops_apply_zoom(rec, self._wheel_base, self._wheel_f, ax, ay)
            self._ops_show_hud("缩放 %d%%" % round(pct))
            self.item_dragged.emit()
            self.update()
            return True
        if self.view_is_zoomed():
            if shift:
                self.view_pan(delta / 2.0, 0.0)
            else:
                self.view_pan(ad.x() / 2.0, ad.y() / 2.0)
            return True
        return False

    @staticmethod
    def _rec_key(rec):
        t = rec.get("type")
        if t == "overlay":
            return (t, rec.get("list"), rec.get("idx"))
        if t == "sticker":
            return (t, rec.get("ov_idx"), rec.get("el_idx"))
        if t == "video_clip":
            return (t, id(rec.get("clip")))
        return (t, id(rec))

    def _ops_selected_under(self, wpos):
        """光标下、而且已经选中的对象（文字 / 贴纸 / 视频）。"""
        xy = self._video_xy(wpos)
        if xy is None:
            return None
        recs, vid = self._ops_targets()
        cands = list(recs) + ([vid] if vid is not None else [])
        for rec in reversed(cands):
            live = self._live_record(rec)
            r = live.get("rect")
            if r is not None and r.contains(QPointF(xy[0], xy[1])):
                return live
        return None

    # ═════════════════════════ 双击视频 = 重置 ═════════════════════════

    def reset_video_transform(self, rec) -> bool:
        clip = rec.get("clip") if rec else None
        if clip is None:
            return False

        def _go():
            self._commit_host_param(clip, "x", 0.0, lambda v: setattr(clip, "x", v))
            self._commit_host_param(clip, "y", 0.0, lambda v: setattr(clip, "y", v))
            self._commit_host_param(clip, "scale", 1.0, lambda v: setattr(clip, "scale", v))
            self._commit_host_param(clip, "rotation", 0.0, lambda v: setattr(clip, "rotation", v))
            if hasattr(clip, "scale_y"):
                clip.scale_y = 1.0
            return True

        self._discrete("重置画面", _go)
        self._ops_show_hud("已重置位置 / 缩放 / 旋转（Ctrl+Z 撤销）")
        return True

    # ═════════════════════════ 分界线吸附 ═════════════════════════

    def _ops_snap_divider(self, pos: float, axis: str) -> float:
        if getattr(self, "_snap_hold_off", False):
            return pos
        rect = self._preview_target_rect()
        span = rect.height() if axis == "h" else rect.width()
        if span <= 0:
            return pos
        tol = SPLIT_SNAP_PX / span
        for s in SPLIT_SNAPS:
            if abs(pos - s) <= tol:
                return s
        return pos

    def _ops_snap_rotation(self, ang: float) -> float:
        if getattr(self, "_snap_hold_off", False):
            return ang
        return 0.0 if abs(ang) < 2.0 else ang
