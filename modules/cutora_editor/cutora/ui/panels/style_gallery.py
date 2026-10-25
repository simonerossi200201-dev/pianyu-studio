# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Quick-mode style pack gallery."""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QGraphicsOpacityEffect, QHBoxLayout, QInputDialog,
    QLabel, QListWidget, QListWidgetItem, QMenu, QMessageBox, QPushButton,
    QSizePolicy, QToolButton, QVBoxLayout, QWidget,
)

from cutora.core.commands import CommandError
from cutora.core.history import _load_app_settings, _save_app_settings
from cutora.subtitle.io import canvas_size, window_curr_idx
from cutora.subtitle.stylepack import (
    APPLY_ANIMATION_KEY, BUILTIN_KEEP_TEXT, DELETE_BOTH_SIDES_TEXT,
    SAMPLE_EN, StylepackError, gallery_card_size, gallery_column_count,
    gallery_column_width, gallery_grid_width,
    ensure_pack_thumb, initial_apply_animation, list_pack_dirs, load_pack,
    sanitize_pack_fonts, saved_apply_animation,
)
from cutora.ui.i18n import tr
from cutora.ui.panels.style_thumbs import (
    CARD_MAX_H, CARD_W, SAVE_ROLE, badge_pure_black, badge_user, cache_key,
    far_playhead, gallery_hover_t, grab_video_frame, neutral_frame, pack_in_s,
    pack_sample, plus_card, render_pack_card,
)

ANIM_PREF_KEY = APPLY_ANIMATION_KEY

import threading as _threading
import time as _time
_THUMB_RENDER_LOCK = _threading.Lock()


def _render_thumbs_offthread(spec: dict, cancel_check=None) -> dict:
    """Grab one frame and paint the visible cards. QImage only, no QPixmap."""
    from cutora.ui.panels.style_thumbs import (
        grab_video_frame, neutral_frame, render_pack_image,
    )
    if cancel_check is not None and cancel_check():
        return {"base": None, "frames": []}
    video = spec.get("video") or ""
    cw = int(spec.get("cw") or 0)
    ch = int(spec.get("ch") or 0)
    tw = int(spec.get("tw") or 0)
    th = int(spec.get("th") or 0)
    base = grab_video_frame(video, float(spec.get("t_sec") or 0.0), cw, ch) if video else None
    if base is None or base.isNull():
        base = neutral_frame(cw, ch)
    else:
        base = base.copy()
    frames = []
    for job in spec.get("jobs") or []:
        if cancel_check is not None and cancel_check():
            break
        pack = job.get("pack") or {}
        try:
            img = render_pack_image(
                base, pack, job.get("sample") or "", tw, th, t=0.4)
        except Exception:
            continue
        frames.append({
            "row": job.get("row"),
            "key": job.get("key"),
            "pack": pack,
            "image": img.copy(),
        })
        _time.sleep(0.001)
    return {"base": base, "frames": frames}


def _tags_param(tags) -> str:
    """stylepack.save_current schema wants a comma-separated string."""
    if isinstance(tags, list):
        return ",".join(str(t).strip() for t in tags if str(t).strip())
    return str(tags or "")
EDIT_BTN_SIZE = 22
EDIT_BTN_REST_OPACITY = 0.82
EDIT_BTN_HOVER_OPACITY = 1.0


class StyleGalleryPanel(QWidget):
    def __init__(self, window, parent=None):
        super().__init__(parent)
        self.window = window
        self._packs: list[dict] = []
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        filt = QHBoxLayout()
        self.cmb_tag = QComboBox()
        self.cmb_tag.addItem(tr("全部标签"), "")
        self.cmb_tag.currentIndexChanged.connect(self._refill)
        filt.addWidget(self.cmb_tag, 1)
        btn_imp = QPushButton(tr("导入"))
        btn_exp = QPushButton(tr("导出"))
        btn_save = QPushButton(tr("存成风格包"))
        btn_save.setMinimumWidth(0)
        btn_save.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        btn_imp.setMinimumWidth(0)
        btn_exp.setMinimumWidth(0)
        btn_imp.clicked.connect(self._import)
        btn_exp.clicked.connect(self._export)
        btn_save.clicked.connect(self._save_current)
        filt.addWidget(btn_imp)
        filt.addWidget(btn_exp)
        filt.addWidget(btn_save)
        root.addLayout(filt)
        anim_row = QHBoxLayout()
        self.chk_anim = QCheckBox(tr("同时应用该风格的动画"))
        self.chk_anim.setObjectName("stylepackAnimSwitch")
        self.lbl_anim = QLabel(tr("覆盖入场/出场动画。"))
        self.lbl_anim.setObjectName("hint")
        self.lbl_anim.setWordWrap(False)
        self.chk_anim.setToolTip(tr("改过动画时默认关闭。"))
        self.lbl_anim.setToolTip(tr("改过动画时默认关闭。"))
        anim_row.addWidget(self.chk_anim, 0)
        anim_row.addWidget(self.lbl_anim, 1)
        root.addLayout(anim_row)
        self.chk_anim.toggled.connect(self._save_anim_pref)
        self._load_anim_pref()
        self.list = QListWidget()
        self.list.setViewMode(QListWidget.ViewMode.IconMode)
        self.list.setFlow(QListWidget.Flow.LeftToRight)
        self.list.setIconSize(QSize(CARD_W, CARD_MAX_H))
        self.list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.list.setMovement(QListWidget.Movement.Static)
        self.list.setWrapping(True)
        self.list.setSpacing(0)
        self.list.setMouseTracking(True)
        self.list.viewport().setMouseTracking(True)
        self.list.setContentsMargins(0, 0, 0, 0)
        self.list.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._context_menu)
        self.list.itemClicked.connect(self._clicked)
        self.list.itemEntered.connect(self._hover)
        self._thumb_cache: dict = {}
        self._thumb_src = None
        self._thumb_ms = None
        root.addWidget(self.list, 1)
        self._hover_timer = QTimer(self)
        self._hover_timer.setSingleShot(False)
        self._hover_timer.setInterval(50)
        self._hover_timer.timeout.connect(self._hover_tick)
        self._hover_item = None
        self._hover_t0 = 0.0
        self._thumb_ctx = {}
        self._edit_btns: list = []
        self._edit_fxs: list = []
        self._edit_items: list = []
        self._edit_btn = None
        self._edit_fx = None
        self._edit_item = None
        self.list.verticalScrollBar().valueChanged.connect(self._on_gallery_scrolled)
        self.list.horizontalScrollBar().valueChanged.connect(self._on_gallery_scrolled)
        self.hint = QLabel("")
        self.hint.setObjectName("hint")
        self.hint.setWordWrap(True)
        root.addWidget(self.hint)
        self.reload()

    def bus(self):
        return getattr(self.window, "bus", None)

    def reload(self):
        import os as _os
        from cutora.subtitle.stylepack import PACK_THUMB
        self._packs = []
        tags = set()
        for path in list_pack_dirs():
            try:
                if not _os.path.isfile(_os.path.join(path, PACK_THUMB)):
                    ensure_pack_thumb(path)
                pack = sanitize_pack_fonts(load_pack(path))
            except StylepackError:
                continue
            self._packs.append(pack)
            for t in pack.get("meta", {}).get("tags") or []:
                tags.add(str(t))
        cur = self.cmb_tag.currentData()
        self.cmb_tag.blockSignals(True)
        self.cmb_tag.clear()
        self.cmb_tag.addItem(tr("全部标签"), "")
        for t in sorted(tags):
            self.cmb_tag.addItem(t, t)
        idx = max(0, self.cmb_tag.findData(cur))
        self.cmb_tag.setCurrentIndex(idx)
        self.cmb_tag.blockSignals(False)
        self._load_anim_pref()
        self._refill()
        self._refresh_preset_dropdowns()

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, self.schedule_visible_thumbs)

    def _viewport_width(self) -> int:
        vw = self.list.viewport().width()
        if vw < 40:
            vw = max(self.width() - 16, 200)
        return int(vw)

    def _column_width(self) -> int:
        return gallery_column_width(self._viewport_width(), 10)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        col_w = self._column_width()
        if getattr(self, "_last_col_w", None) != col_w and self._packs:
            self._refill()

    def _refill(self):
        want = self.cmb_tag.currentData() or ""
        task, cw, ch, t_ms, sample, video = self._frame_context()
        visible = []
        for pack in self._packs:
            tags = pack.get("meta", {}).get("tags") or []
            if want and want not in tags:
                continue
            visible.append(pack)
        vw = self._viewport_width()
        col_w = gallery_column_width(vw, 10)
        grid_w = gallery_grid_width(vw)
        if gallery_column_count(vw) == 2:
            grid_w = max(80, (vw - 24) // 2)
            col_w = min(col_w, grid_w)
        self._last_col_w = col_w
        tw, th = gallery_card_size(cw, ch, col_w=col_w)
        self.list.setIconSize(QSize(tw, th))
        self.list.setGridSize(QSize(grid_w, th + 26))
        src = (video or "", cw, ch, tw, th)
        same_frame = src == self._thumb_src and not far_playhead(self._thumb_ms, t_ms)
        if not same_frame:
            self._thumb_cache = {}
            self._thumb_src = src
            self._thumb_ms = t_ms
        prev_base = (self._thumb_ctx or {}).get("base") if same_frame else None
        self._thumb_ctx = {
            "base": prev_base,
            "tw": tw, "th": th, "t_ms": t_ms,
            "cw": cw, "ch": ch, "video": video or "",
            "sample": sample,
        }
        self.list.clear()
        plus = QListWidgetItem(tr("新建风格包"))
        plus.setData(Qt.ItemDataRole.UserRole, SAVE_ROLE)
        plus.setIcon(QIcon(plus_card(tw, th)))
        plus.setToolTip(tr("把当前样式存成风格包"))
        self.list.addItem(plus)
        for pack in visible:
            self.list.addItem(self._pack_item(
                pack, tw, th, t_ms, cw, ch, sample, video))
        self._sync_edit_btns()
        if not getattr(self, "_defer_thumb_paint", False) and self.isVisible():
            self.schedule_visible_thumbs()

    def _frame_context(self):
        task = self._task()
        if task is None:
            return None, 1080, 1920, 0, SAMPLE_EN, ""
        cw, ch = canvas_size(task)
        t_ms = int(getattr(self.window, "_timeline_ms", 0) or 0)
        t_sec = t_ms / 1000.0
        sample = SAMPLE_EN
        segs = task.get("segments") or []
        hit = None
        for seg in segs:
            if float(seg.get("start") or 0) <= t_sec <= float(seg.get("end") or 0):
                hit = seg
                break
        if hit is None and segs:
            hit = segs[0]
            t_ms = int(float(hit.get("start") or 0) * 1000 + 50)
        if hit is not None:
            sample = str(hit.get("text") or sample).strip() or sample
        video = str(task.get("video") or task.get("path") or "")
        return task, cw, ch, t_ms, sample, video

    def _base_frame(self, video, t_sec, cw, ch):
        frame = grab_video_frame(video, t_sec, cw, ch) if video else None
        if frame is None or frame.isNull():
            return neutral_frame(cw, ch)
        return frame

    def _pending_card(self, tw, th):
        key = ("pending", int(tw), int(th))
        pix = self._thumb_cache.get(key)
        if pix is not None:
            return pix
        from PySide6.QtGui import QPixmap
        pix = QPixmap(max(1, int(tw)), max(1, int(th)))
        pix.fill(Qt.GlobalColor.transparent)
        self._thumb_cache[key] = pix
        return pix

    def _viewport_ready(self) -> bool:
        vp = self.list.viewport().rect()
        return vp.width() >= 40 and vp.height() >= 40

    def _thumbs_to_paint(self):
        """Pack rows to render now: only when visible, or at most first 8 on first layout."""
        if not self.isVisible():
            return []
        ready = self._viewport_ready()
        vp = self.list.viewport().rect()
        out = []
        for i in range(self.list.count()):
            it = self.list.item(i)
            if it is None:
                continue
            role = it.data(Qt.ItemDataRole.UserRole)
            if not role or role == SAVE_ROLE:
                continue
            if ready:
                rect = self.list.visualItemRect(it)
                if rect.height() <= 0 or not rect.intersects(vp):
                    continue
            out.append(it)
            if not ready and len(out) >= 8:
                break
        return out

    def _on_gallery_scrolled(self, *_args):
        self._reposition_edit_btns()
        if getattr(self, "_defer_thumb_paint", False) or not self.isVisible():
            return
        # Newly revealed cards only. Render off-thread so scrolling never stalls UI.
        self.schedule_visible_thumbs()

    def schedule_visible_thumbs(self):
        """Paint the on-screen cards off the UI thread. Click stays responsive."""
        from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

        self._thumb_gen = int(getattr(self, "_thumb_gen", 0)) + 1
        gen = self._thumb_gen
        try:
            self.list.doItemsLayout()
        except Exception:
            pass
        ctx = dict(self._thumb_ctx or {})
        items = self._thumbs_to_paint()
        jobs = []
        t_ms = int(ctx.get("t_ms") or 0)
        cw = int(ctx.get("cw") or 0)
        ch = int(ctx.get("ch") or 0)
        video = ctx.get("video") or ""
        sample = ctx.get("sample") or SAMPLE_EN
        tw = int(ctx.get("tw") or 0)
        th = int(ctx.get("th") or 0)
        if tw <= 0 or th <= 0 or not items:
            return
        for it in items:
            path = it.data(Qt.ItemDataRole.UserRole)
            pack = next((p for p in self._packs if p.get("path") == path), None)
            if pack is None:
                continue
            row = self.list.row(it)
            card_sample = pack_sample(pack, sample)
            key = cache_key(
                pack.get("id"), t_ms, cw, ch, card_sample, (0, 0, cw, ch), video)
            if self._thumb_cache.get(key) is not None:
                it.setIcon(QIcon(self._thumb_cache[key]))
                continue
            jobs.append({
                "row": row,
                "pack": pack,
                "sample": card_sample,
                "key": key,
            })
        if not jobs:
            return
        bus = getattr(self, "_thumb_bus", None)
        if bus is None:
            class _Bus(QObject):
                ready = Signal(int, object)

            bus = _Bus(self)
            bus.ready.connect(self._apply_scheduled_thumbs)
            self._thumb_bus = bus
        spec = {
            "video": video,
            "t_sec": t_ms / 1000.0,
            "cw": cw,
            "ch": ch,
            "tw": tw,
            "th": th,
            "jobs": jobs,
        }

        panel_ref = self

        class _Job(QRunnable):
            def __init__(self, spec, bus, gen):
                super().__init__()
                self.spec = spec
                self.bus = bus
                self.gen = gen
                self.setAutoDelete(True)

            def run(self):
                cancel_fn = lambda: self.gen != int(getattr(panel_ref, "_thumb_gen", 0))
                if cancel_fn():
                    return
                payload = None
                try:
                    with _THUMB_RENDER_LOCK:
                        if cancel_fn():
                            return
                        payload = _render_thumbs_offthread(self.spec, cancel_check=cancel_fn)
                except Exception:
                    from cutora.core.log import get_logger
                    get_logger("cutora.ui.panels.style_gallery").exception(
                        "gallery thumb job failed")
                if cancel_fn():
                    return
                try:
                    self.bus.ready.emit(self.gen, payload)
                except RuntimeError:
                    pass

        QThreadPool.globalInstance().start(_Job(spec, bus, gen))

    def _apply_scheduled_thumbs(self, gen, payload):
        if gen != int(getattr(self, "_thumb_gen", 0)):
            return
        if not payload:
            return
        base = payload.get("base")
        if base is not None:
            ctx = dict(self._thumb_ctx or {})
            ctx["base"] = base
            self._thumb_ctx = ctx
        from PySide6.QtGui import QPixmap
        for frame in payload.get("frames") or []:
            row = int(frame.get("row") or -1)
            img = frame.get("image")
            key = frame.get("key")
            pack = frame.get("pack") or {}
            item = self.list.item(row) if row >= 0 else None
            if item is None or img is None:
                continue
            pix = QPixmap.fromImage(img)
            if not pack.get("builtin"):
                pix = badge_user(pix)
                if self._pack_has_pure_black(pack):
                    pix = badge_pure_black(pix)
            if key is not None:
                self._thumb_cache[key] = pix
            item.setIcon(QIcon(pix))

    def _paint_visible_thumbs(self):
        """Render only the cards the user can see. The rest stay placeholders
        until a scroll or resize brings them in."""
        ctx = self._thumb_ctx or {}
        tw = int(ctx.get("tw") or 0)
        th = int(ctx.get("th") or 0)
        if tw <= 0 or th <= 0:
            return
        try:
            self.list.doItemsLayout()
        except Exception:
            pass
        items = self._thumbs_to_paint()
        if not items:
            return
        pending = []
        t_ms = int(ctx.get("t_ms") or 0)
        cw = int(ctx.get("cw") or 0)
        ch = int(ctx.get("ch") or 0)
        video = ctx.get("video") or ""
        sample = ctx.get("sample") or SAMPLE_EN
        for it in items:
            path = it.data(Qt.ItemDataRole.UserRole)
            pack = next((p for p in self._packs if p.get("path") == path), None)
            if pack is None:
                continue
            card_sample = pack_sample(pack, sample)
            key = cache_key(
                pack.get("id"), t_ms, cw, ch, card_sample, (0, 0, cw, ch), video)
            pix = self._thumb_cache.get(key)
            if pix is not None:
                it.setIcon(QIcon(pix))
                continue
            pending.append((it, pack, card_sample, key))
        if not pending:
            return
        base = ctx.get("base")
        if base is None or getattr(base, "isNull", lambda: True)():
            base = self._base_frame(video, t_ms / 1000.0, cw, ch)
            ctx["base"] = base
            self._thumb_ctx = ctx
        for it, pack, card_sample, key in pending:
            try:
                pix = render_pack_card(base, pack, card_sample, tw, th, t=0.4)
            except Exception:
                pix = plus_card(tw, th)
            if not pack.get("builtin"):
                pix = badge_user(pix)
                if self._pack_has_pure_black(pack):
                    pix = badge_pure_black(pix)
            self._thumb_cache[key] = pix
            it.setIcon(QIcon(pix))

    def _pack_item(self, pack, tw, th, t_ms, cw, ch, sample, video=""):
        name = str(pack.get("meta", {}).get("name") or pack.get("id"))
        item = QListWidgetItem(name)
        item.setData(Qt.ItemDataRole.UserRole, pack.get("path"))
        card_sample = pack_sample(pack, sample)
        crop = (0, 0, cw, ch)
        key = cache_key(
            pack.get("id"), t_ms, cw, ch, card_sample, crop, video or "")
        pix = self._thumb_cache.get(key)
        if pix is None:
            pix = self._pending_card(tw, th)
        item.setIcon(QIcon(pix))
        tip = name
        if pack.get("font_missing"):
            tip = tip + " · " + tr("缺字体，已退回默认")
        item.setToolTip(tip)
        return item

    def _pack_has_pure_black(self, pack):
        from cutora.render.textfx.spec import collect_pure_black
        body = {
            "style": pack.get("style") or {},
            "animation": pack.get("animation") or {},
        }
        return bool(collect_pure_black(body))

    def _clicked(self, item):
        path = item.data(Qt.ItemDataRole.UserRole)
        if path == SAVE_ROLE:
            self._save_current()
            return
        if not path:
            return
        if not self._confirm_leave_edit():
            return
        try:
            pack = sanitize_pack_fonts(load_pack(path))
        except StylepackError as exc:
            QMessageBox.warning(self, tr("风格包"), str(exc))
            return
        bus = self.bus()
        if bus is None:
            return
        try:
            out = bus.execute("stylepack.apply", {
                "path": path,
                "apply_animation": bool(self.chk_anim.isChecked()),
            }, source="ui")
        except CommandError as exc:
            QMessageBox.warning(self, tr("风格包"), str(exc))
            return
        name = (pack.get("meta") or {}).get("name") or pack.get("id") or ""
        self.hint.clear()
        self.hint.setText(tr("已应用 %s · Ctrl+Z 撤销") % name)
        if out.get("font_missing"):
            self.hint.setText(self.hint.text() + " · " + tr("缺字体，已退回默认"))

    def _task(self):
        idx = window_curr_idx(self.window)
        tasks = getattr(self.window, "tasks", None) or []
        if 0 <= idx < len(tasks):
            return tasks[idx]
        return None

    def _saved_anim_pref(self) -> bool:
        return saved_apply_animation()

    def _load_anim_pref(self):
        task = self._task()
        apply_anim = initial_apply_animation((task or {}).get("style"))
        chk = getattr(self, "chk_anim", None)
        if chk is None:
            return
        chk.blockSignals(True)
        chk.setChecked(bool(apply_anim))
        chk.blockSignals(False)

    def _save_anim_pref(self, checked=False):
        data = _load_app_settings() or {}
        data[ANIM_PREF_KEY] = bool(self.chk_anim.isChecked())
        _save_app_settings(data)

    def showEvent(self, event):
        super().showEvent(event)
        self._load_anim_pref()
        self._refill()

    def _import(self):
        path, _ = QFileDialog.getOpenFileName(
            self, tr("导入风格包"), "", "CUTORA style (*.cutorastyle *.zip)")
        if not path:
            return
        bus = self.bus()
        try:
            bus.execute("stylepack.import_file", {"path": path}, source="ui")
        except CommandError as exc:
            QMessageBox.warning(self, tr("风格包"), str(exc))
            return
        self.reload()

    def _export(self):
        item = self.list.currentItem()
        if item is None:
            return
        src = item.data(Qt.ItemDataRole.UserRole)
        dest, _ = QFileDialog.getSaveFileName(
            self, tr("导出风格包"), "", "CUTORA style (*.cutorastyle)")
        if not dest:
            return
        bus = self.bus()
        try:
            bus.execute("stylepack.export_file", {
                "pack_dir": src, "path": dest,
            }, source="ui")
        except CommandError as exc:
            QMessageBox.warning(self, tr("风格包"), str(exc))

    def _save_current(self):
        name, ok = QInputDialog.getText(
            self, tr("把当前样式存成风格包"), tr("名称"))
        if not ok or not str(name).strip():
            return
        tags, _ok = QInputDialog.getText(
            self, tr("把当前样式存成风格包"), tr("标签（逗号分隔，可空）"))
        bus = self.bus()
        try:
            bus.execute("stylepack.save_current", {
                "name": str(name).strip(),
                "tags": str(tags or ""),
            }, source="ui")
        except CommandError as exc:
            QMessageBox.warning(self, tr("风格包"), str(exc))
            return
        self.reload()

    def _context_menu(self, pos):
        item = self.list.itemAt(pos)
        if item is None:
            return
        path = item.data(Qt.ItemDataRole.UserRole)
        if not path or path == SAVE_ROLE:
            return
        pack = next((p for p in self._packs if p.get("path") == path), None)
        if pack is None:
            return
        menu = QMenu(self)
        act_edit = menu.addAction(tr("编辑"))
        act_dup = menu.addAction(tr("复制为新预设"))
        act_ren = act_del = act_exp = None
        if not pack.get("builtin"):
            act_ren = menu.addAction(tr("重命名"))
            act_del = menu.addAction(tr("删除"))
            act_exp = menu.addAction(tr("导出"))
        chosen = menu.exec(self.list.mapToGlobal(pos))
        if chosen is act_edit:
            self._start_edit(pack)
        elif chosen is act_dup:
            self._duplicate_pack(pack)
        elif act_ren is not None and chosen is act_ren:
            self._rename_pack(path)
        elif act_del is not None and chosen is act_del:
            self._delete_pack(path)
        elif act_exp is not None and chosen is act_exp:
            self.list.setCurrentItem(item)
            self._export()

    def _rename_pack(self, path):
        name, ok = QInputDialog.getText(self, tr("重命名风格包"), tr("名称"))
        if not ok or not str(name).strip():
            return
        bus = self.bus()
        try:
            bus.execute("stylepack.rename", {
                "path": path, "name": str(name).strip(),
            }, source="ui")
        except CommandError as exc:
            QMessageBox.warning(self, tr("风格包"), str(exc))
            return
        self.reload()

    def _refresh_preset_dropdowns(self):
        win = self.window
        panels = []
        for obj in (
            getattr(win, "sub_panel", None),
            getattr(getattr(win, "role_panel", None), "style_panel", None),
            getattr(getattr(win, "word_block_panel", None), "style_panel", None),
        ):
            if obj is not None and obj not in panels:
                panels.append(obj)
        for panel in panels:
            refresh = getattr(panel, "_refresh_text_presets", None)
            if callable(refresh):
                refresh()

    def _delete_pack(self, path):
        try:
            pack = load_pack(path)
        except StylepackError as exc:
            QMessageBox.warning(self, tr("风格包"), str(exc))
            return
        if pack.get("builtin"):
            QMessageBox.warning(self, tr("风格包"), tr(BUILTIN_KEEP_TEXT))
            return
        if QMessageBox.question(
                self, tr("删除风格包"), tr(DELETE_BOTH_SIDES_TEXT),
        ) != QMessageBox.StandardButton.Yes:
            return
        bus = self.bus()
        try:
            bus.execute("stylepack.delete", {"path": path}, source="ui")
        except CommandError as exc:
            QMessageBox.warning(self, tr("风格包"), str(exc))
            return
        self.reload()

    def _hover(self, item):
        path = item.data(Qt.ItemDataRole.UserRole)
        if path == SAVE_ROLE:
            self._hover_timer.stop()
            self._hover_item = None
            return
        pack = next((p for p in self._packs if p.get("path") == path), None)
        if pack is None:
            return
        import time
        self._hover_item = item
        self._hover_t0 = time.perf_counter()
        if not self._hover_timer.isActive():
            self._hover_timer.start()

    def _hover_tick(self):
        item = self._hover_item
        if item is None:
            self._hover_timer.stop()
            return
        from PySide6.QtGui import QCursor
        under = self.list.itemAt(self.list.viewport().mapFromGlobal(QCursor.pos()))
        if under is not item:
            self._hover_timer.stop()
            self._hover_item = None
            self._reposition_edit_btns()
            return
        path = item.data(Qt.ItemDataRole.UserRole)
        pack = next((p for p in self._packs if p.get("path") == path), None)
        ctx = self._thumb_ctx or {}
        base = ctx.get("base")
        if pack is None or base is None:
            return
        import time
        t = gallery_hover_t(time.perf_counter() - self._hover_t0, pack_in_s(pack))
        sample = pack_sample(pack, SAMPLE_EN)
        try:
            pix = render_pack_card(
                base, pack, sample, int(ctx["tw"]), int(ctx["th"]), t=t)
        except Exception:
            return
        if not pack.get("builtin"):
            pix = badge_user(pix)
            if self._pack_has_pure_black(pack):
                pix = badge_pure_black(pix)
        item.setIcon(QIcon(pix))
        self._place_edit_btn(item)

    def _make_edit_btn(self):
        btn = QToolButton(self.list.viewport())
        btn.setObjectName("stylepackEditBtn")
        btn.setFixedSize(EDIT_BTN_SIZE, EDIT_BTN_SIZE)
        btn.setAutoRaise(False)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setToolTip(tr("编辑"))
        fx = QGraphicsOpacityEffect(btn)
        btn.setGraphicsEffect(fx)
        fx.setOpacity(EDIT_BTN_REST_OPACITY)
        btn.clicked.connect(self._pencil_clicked)
        return btn, fx

    def _pack_list_items(self):
        items = []
        for i in range(self.list.count()):
            it = self.list.item(i)
            if it is None:
                continue
            path = it.data(Qt.ItemDataRole.UserRole)
            if not path or path == SAVE_ROLE:
                continue
            items.append(it)
        return items

    def _sync_edit_btns(self):
        items = self._pack_list_items()
        while len(self._edit_btns) < len(items):
            btn, fx = self._make_edit_btn()
            self._edit_btns.append(btn)
            self._edit_fxs.append(fx)
        self._edit_items = items
        for i, btn in enumerate(self._edit_btns):
            if i >= len(items):
                btn.hide()
                continue
            self._place_edit_btn(items[i], btn, self._edit_fxs[i])
        if items:
            self._edit_btn = self._edit_btns[0]
            self._edit_fx = self._edit_fxs[0]
            self._edit_item = items[0]
        else:
            self._edit_btn = None
            self._edit_fx = None
            self._edit_item = None

    def _reposition_edit_btns(self, *_args):
        for i, item in enumerate(self._edit_items):
            if i < len(self._edit_btns):
                self._place_edit_btn(item, self._edit_btns[i], self._edit_fxs[i])

    def _place_edit_btn(self, item, btn=None, fx=None):
        from cutora.ui.icons import icon
        if item is None:
            return
        if btn is None or fx is None:
            if item in self._edit_items:
                idx = self._edit_items.index(item)
                btn = self._edit_btns[idx]
                fx = self._edit_fxs[idx]
            else:
                if not self._edit_btns:
                    btn, fx = self._make_edit_btn()
                    self._edit_btns.append(btn)
                    self._edit_fxs.append(fx)
                    self._edit_items.append(item)
                else:
                    btn = self._edit_btns[0]
                    fx = self._edit_fxs[0]
        rect = self.list.visualItemRect(item)
        btn.setIcon(icon("pencil", size=14))
        btn.move(rect.right() - 24, rect.bottom() - 24)
        hovering = self._hover_item is item
        if fx is not None:
            fx.setOpacity(
                EDIT_BTN_HOVER_OPACITY if hovering else EDIT_BTN_REST_OPACITY)
        btn.show()
        btn.raise_()
        self._edit_btn = btn
        self._edit_fx = fx
        self._edit_item = item

    def _pencil_clicked(self):
        btn = self.sender()
        item = None
        if btn in self._edit_btns:
            idx = self._edit_btns.index(btn)
            if 0 <= idx < len(self._edit_items):
                item = self._edit_items[idx]
        if item is None:
            item = self._edit_item
        if item is None:
            return
        path = item.data(Qt.ItemDataRole.UserRole)
        pack = next((p for p in self._packs if p.get("path") == path), None)
        if pack is None:
            return
        self._start_edit(pack)

    def _style_panel(self):
        return getattr(self.window, "sub_panel", None)

    def _confirm_leave_edit(self) -> bool:
        panel = self._style_panel()
        st = panel.pack_edit_state() if panel is not None and hasattr(panel, "pack_edit_state") else None
        if not st:
            return True
        ans = QMessageBox.question(
            self, tr("放弃当前编辑？"),
            tr("正在编辑预设「%s」。切换将放弃未保存的修改。") % st.get("name", ""),
        )
        if ans != QMessageBox.StandardButton.Yes:
            return False
        self.finish_pack_discard()
        return True

    def _snapshot_task(self):
        import copy
        task = self._task()
        if task is None:
            return None
        return {
            "style": copy.deepcopy(task.get("style") or {}),
            "layout_rules": copy.deepcopy(task.get("layout_rules") or {}),
        }

    def _restore_snapshot(self, snap):
        task = self._task()
        if task is None or not snap:
            return
        task["style"] = snap.get("style") or {}
        task["layout_rules"] = snap.get("layout_rules") or {}
        panel = self._style_panel()
        if panel is not None:
            panel.load_data(task.get("style"))

    def _focus_style_tab(self):
        win = self.window
        dock = getattr(win, "dock_right", None)
        if dock is not None:
            dock.show()
            dock.raise_()
        tabs = (
            getattr(win, "tabs", None)
            or getattr(win, "right_tabs", None)
            or getattr(win, "prop_tabs", None)
        )
        panel = self._style_panel()
        if tabs is not None and panel is not None:
            idx = tabs.indexOf(panel)
            if idx >= 0:
                tabs.setCurrentIndex(idx)
        if panel is not None:
            bar = getattr(panel, "_pack_edit_bar", None)
            if bar is not None:
                bar.show()
                bar.raise_()

    def _start_edit(self, pack):
        if not self._confirm_leave_edit():
            return
        snap = self._snapshot_task()
        bus = self.bus()
        if bus is None:
            return
        try:
            bus.execute("stylepack.apply", {
                "path": pack.get("path"),
                "apply_animation": bool(self.chk_anim.isChecked()),
            }, source="ui")
        except CommandError as exc:
            QMessageBox.warning(self, tr("风格包"), str(exc))
            return
        panel = self._style_panel()
        if panel is not None and hasattr(panel, "enter_pack_edit"):
            panel.enter_pack_edit(pack, snap)
        self._focus_style_tab()
        name = (pack.get("meta") or {}).get("name") or pack.get("id")
        self.hint.setText(tr("正在编辑「%s」") % name)

    def _duplicate_pack(self, pack):
        if not self._confirm_leave_edit():
            return
        meta = pack.get("meta") or {}
        default = "%s 副本" % (meta.get("name") or pack.get("id") or "pack")
        tags = meta.get("tags") or []
        bus = self.bus()
        try:
            bus.execute("stylepack.apply", {
                "path": pack.get("path"),
                "apply_animation": True,
            }, source="ui")
            bus.execute("stylepack.save_current", {
                "name": default,
                "tags": _tags_param(tags),
            }, source="ui")
        except CommandError as exc:
            QMessageBox.warning(self, tr("风格包"), str(exc))
            return
        self.reload()

    def finish_pack_save(self):
        panel = self._style_panel()
        st = panel.pack_edit_state() if panel is not None else None
        if not st or st.get("builtin"):
            return
        bus = self.bus()
        try:
            bus.execute("stylepack.update", {"path": st["path"]}, source="ui")
        except CommandError as exc:
            QMessageBox.warning(self, tr("风格包"), str(exc))
            return
        if panel is not None:
            panel._leave_pack_edit()
        self.hint.setText(tr("已保存「%s」") % st.get("name", ""))
        self.reload()

    def finish_pack_save_as(self):
        panel = self._style_panel()
        st = panel.pack_edit_state() if panel is not None else None
        default = "%s 副本" % ((st or {}).get("name") or "pack")
        from PySide6.QtWidgets import QLineEdit
        name, ok = QInputDialog.getText(
            self, tr("另存为新预设"), tr("名称"),
            QLineEdit.EchoMode.Normal, default)
        if not ok or not str(name).strip():
            return
        tags = ""
        src = next((p for p in self._packs if p.get("path") == (st or {}).get("path")), None)
        if src:
            tags = (src.get("meta") or {}).get("tags") or []
        bus = self.bus()
        try:
            bus.execute("stylepack.save_current", {
                "name": str(name).strip(),
                "tags": _tags_param(tags),
            }, source="ui")
        except CommandError as exc:
            QMessageBox.warning(self, tr("风格包"), str(exc))
            return
        if panel is not None:
            panel._leave_pack_edit()
        self.reload()

    def finish_pack_discard(self):
        panel = self._style_panel()
        st = panel.pack_edit_state() if panel is not None else None
        if st:
            self._restore_snapshot(st.get("snapshot"))
        if panel is not None:
            panel._leave_pack_edit()
        self.hint.setText(tr("已放弃编辑"))

