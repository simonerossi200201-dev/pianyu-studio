# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q128：文本样式缩略图画廊（文本工具改造第五批第 15 条）。

下拉框里一排名字挑不出样式来。Canva 的「文字样式」是一格一格画出来的：
看一眼就知道选了会长什么样。这里把每个文本预设用真正的 textfx 渲染成一张
缩略图（同一套 paint_text_overlay，所见即所得），点一格就把**样式**刷到
画布上所有选中的文本层 —— 正文、位置、时间不动（和 Canva 一致；老的
「预设」下拉框会连正文一起换，那是模板语义，两者并存）。

「把当前文本存为样式」走 text_preset.preset_from_overlay + save_preset，
和 Q121 批量文本页、属性面板的存预设是同一份存储。
"""
from __future__ import annotations

import copy

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from cutora.core.log import get_logger
from cutora.ui.i18n import tr
from cutora.ui.theme.tokens import SPACE_1, SPACE_2

_LOG = get_logger("cutora.ui.text_style_gallery")

THUMB_W, THUMB_H = 168, 96
SAMPLE_TEXT = "Aa 样式"

_THUMB_CACHE: dict[tuple, QPixmap] = {}


# ─────────────────────────── 样式 ⇄ 预设 ───────────────────────────

def style_from_preset(row: dict) -> dict:
    """预设 → 可以 paste_style 的样式 dict（不含正文 / 几何）。"""
    from cutora.subtitle.text_preset import template_from_preset
    from cutora.ui.text_style_clipboard import copy_style
    template = template_from_preset(row)
    style = copy_style(template)
    style["_preset_name"] = str(row.get("name") or "")
    return style


def apply_preset_style(ov: dict, row: dict) -> bool:
    """把预设的样式刷到一条文本层上，正文 / 几何 / 时间不动。"""
    from cutora.ui.text_style_clipboard import paste_style
    changed = paste_style(ov, style_from_preset(row))
    if str(ov.get("source_preset_id") or "") != str(row.get("id") or ""):
        ov["source_preset_id"] = str(row.get("id") or "")
        changed = True
    return changed


# ─────────────────────────── 缩略图 ───────────────────────────

def _thumb_key(row: dict) -> tuple:
    return (str(row.get("id") or ""), str(row.get("updated_at") or ""), THUMB_W, THUMB_H)


def render_preset_thumb(row: dict, w: int = THUMB_W, h: int = THUMB_H) -> QPixmap:
    """用 textfx 把预设画成一张小图。失败就给一张只写名字的兜底图。"""
    key = _thumb_key(row)
    hit = _THUMB_CACHE.get(key)
    if hit is not None:
        return hit
    img = QImage(int(w), int(h), QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(QColor(28, 30, 36))
    painter = QPainter(img)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        # 底下铺一点斜纹，让透明底 / 白字都看得出来
        painter.setPen(QColor(255, 255, 255, 10))
        for x in range(-h, w, 12):
            painter.drawLine(x, h, x + h, 0)
        try:
            _paint_sample(painter, row, w, h, backdrop=img.copy())
        except Exception:
            _LOG.exception("preset thumb failed: %s", row.get("id"))
            painter.setPen(QColor(230, 230, 230))
            painter.drawText(img.rect(), Qt.AlignmentFlag.AlignCenter, str(row.get("name") or "?"))
    finally:
        painter.end()
    pm = QPixmap.fromImage(img)
    _THUMB_CACHE[key] = pm
    return pm


def _paint_sample(
    painter: QPainter, row: dict, w: int, h: int, backdrop: QImage | None = None,
) -> None:
    """在 w×h 的小画布上按预设样式画 SAMPLE_TEXT。

    缩略图不是 1080 宽的画布：字号按比例缩，把整个样式（描边 / 阴影 / 底板
    都以 em 计）一起缩下去，看起来就是成片里那条字的等比小样。
    """
    from cutora.render.text_layer_paint import paint_text_overlay
    from cutora.subtitle.text_layer import ensure_overlay_fx
    from cutora.subtitle.text_preset import template_from_preset
    ov = template_from_preset(row)
    ov = copy.deepcopy(ov) if isinstance(ov, dict) else {}
    ov["type"] = "text"
    text = str(row.get("text") or "").strip()
    if not text or len(text) > 20 or "\n" in text:
        text = SAMPLE_TEXT
    ov["content"] = text
    ov.pop("rich_content", None)
    ov["content_format"] = "plain"
    ov.pop("runs", None)
    ov.pop("keyframes", None)
    ov["rotation"] = 0
    ov["opacity"] = 255
    # 字号：让一行字大约占宽度的 70%，最多占高度的 55%（CJK 全角字按 1.12em 计，避免单行文本条在缩略图里断成两行）
    char_units = sum(1.12 if ord(c) > 0x2E80 else 0.70 for c in text)
    em = max(10.0, min(float(h) * 0.36, float(w) * 0.70 / max(2.5, char_units)))
    from cutora.subtitle.stylepack import set_fontsize_px
    set_fontsize_px(ov, em, int(w), int(h), user=False)
    ov["fit_mode"] = "none"
    ov["writing_mode"] = "horizontal"
    lay = dict(ov.get("text_layout") or {})
    lay.update({"left_pad": 0, "right_pad": 0, "top_pad": 0, "bottom_pad": 0,
                "align": "center", "valign": "middle", "first_indent": 0, "para_spacing": 0})
    ov["text_layout"] = lay
    ov["x"], ov["y"], ov["w"], ov["h"] = 0, 0, int(w), int(h)
    ensure_overlay_fx(ov)
    fx = ov.get("fx")
    if isinstance(fx, dict):
        fx.pop("anim", None)
    paint_text_overlay(painter, ov, None, backdrop, video_w=int(w), video_h=int(h))


def invalidate_thumbs() -> None:
    _THUMB_CACHE.clear()


# ─────────────────────────── 画廊控件 ───────────────────────────

class TextStyleGallery(QFrame):
    """弹出式样式画廊（支持文件夹分类浏览）。``style_picked(preset_id)``；``save_requested(name)``。"""

    style_picked = Signal(str)
    save_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setObjectName("textStyleGallery")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._rows: list[dict] = []
        self._current_cat: str = ""
        self._current_id: str | None = None
        self._cat_btns: dict[str, QPushButton] = {}
        lay = QVBoxLayout(self)
        lay.setContentsMargins(SPACE_2, SPACE_2, SPACE_2, SPACE_2)
        lay.setSpacing(SPACE_1)
        head = QHBoxLayout()
        self.lbl_title = QLabel(tr("🎨 文本样式画廊（点击切换样式 · 文字内容保持不变）"))
        self.lbl_title.setObjectName("ctxLabel")
        head.addWidget(self.lbl_title)
        head.addStretch(1)
        self.bt_save = QPushButton(tr("＋ 把当前文本存为样式…"))
        self.bt_save.setToolTip(tr("用画布上选中的那条文本的样式新建一个预设（不存正文）"))
        self.bt_save.clicked.connect(self._on_save)
        head.addWidget(self.bt_save)
        lay.addLayout(head)

        # 文件夹分类导航栏
        self.cat_host = QWidget()
        self.cat_bar = QHBoxLayout(self.cat_host)
        self.cat_bar.setContentsMargins(0, 2, 0, 2)
        self.cat_bar.setSpacing(6)
        lay.addWidget(self.cat_host)

        self.list = QListWidget()
        self.list.setObjectName("textStyleList")
        self.list.setViewMode(QListWidget.ViewMode.IconMode)
        self.list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.list.setMovement(QListWidget.Movement.Static)
        self.list.setIconSize(QSize(THUMB_W, THUMB_H))
        self.list.setGridSize(QSize(THUMB_W + SPACE_2 * 2, THUMB_H + SPACE_2 * 3 + 14))
        self.list.setUniformItemSizes(True)
        self.list.setWordWrap(True)
        self.list.setSpacing(SPACE_1)
        self.list.itemClicked.connect(self._on_item)
        lay.addWidget(self.list, 1)
        self.setMinimumWidth((THUMB_W + SPACE_2 * 2 + SPACE_1) * 3 + SPACE_2 * 2 + 32)
        self.setMinimumHeight(THUMB_H * 3 + 160)

    def _rebuild_cat_buttons(self) -> None:
        from cutora.subtitle.text_preset import list_preset_categories, preset_category_of
        while self.cat_bar.count():
            item = self.cat_bar.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._cat_btns.clear()
        cats = list_preset_categories(self._rows)
        counts: dict[str, int] = {"": len(self._rows)}
        for r in self._rows:
            c = preset_category_of(r)
            counts[c] = counts.get(c, 0) + 1
        all_specs = [("", f"📂 全部 ({counts['']})")] + [
            (c, f"📁 {c} ({counts.get(c, 0)})") for c in cats
        ]
        for cat_key, label in all_specs:
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            is_sel = (cat_key == self._current_cat)
            btn.setChecked(is_sel)
            btn.setStyleSheet(
                "QPushButton { padding: 5px 12px; border-radius: 6px; font-weight: 600; "
                "background: %s; color: %s; border: 1px solid %s; }"
                "QPushButton:hover { background: #2D3748; }"
                % (
                    "#3B82F6" if is_sel else "#1E2430",
                    "#FFFFFF" if is_sel else "#CBD5E1",
                    "#60A5FA" if is_sel else "#334155",
                )
            )
            btn.clicked.connect(lambda _checked=False, ck=cat_key: self.set_category(ck))
            self.cat_bar.addWidget(btn)
            self._cat_btns[cat_key] = btn
        self.cat_bar.addStretch(1)

    def set_category(self, category: str) -> None:
        self._current_cat = str(category or "")
        self._rebuild_cat_buttons()
        self._populate_list(self._current_id)

    def _populate_list(self, current_id: str | None = None) -> int:
        from cutora.subtitle.text_preset import preset_category_of
        self.list.clear()
        shown = 0
        for row in self._rows:
            if self._current_cat and preset_category_of(row) != self._current_cat:
                continue
            item = QListWidgetItem(QIcon(render_preset_thumb(row)), str(row.get("name") or row.get("id")))
            item.setData(Qt.ItemDataRole.UserRole, str(row.get("id") or ""))
            cat = preset_category_of(row)
            item.setToolTip(f"【{cat}】{row.get('name') or ''}")
            item.setTextAlignment(Qt.AlignmentFlag.AlignHCenter)
            self.list.addItem(item)
            shown += 1
            if current_id and str(row.get("id")) == str(current_id):
                self.list.setCurrentItem(item)
        return shown

    # ── 数据 ──
    def reload(self, current_id: str | None = None, category: str | None = None) -> int:
        from cutora.subtitle import text_preset as store
        self._rows = list(store.list_presets())
        self._current_id = current_id
        if category is not None:
            self._current_cat = str(category)
        self._rebuild_cat_buttons()
        return self._populate_list(current_id)

    def rows(self) -> list[dict]:
        return list(self._rows)

    # ── 交互 ──
    def _on_item(self, item):
        pid = str(item.data(Qt.ItemDataRole.UserRole) or "")
        if pid:
            self.style_picked.emit(pid)

    def _on_save(self):
        name, ok = QInputDialog.getText(self, tr("存为样式"), tr("样式名称"))
        name = str(name or "").strip()
        if ok and name:
            self.save_requested.emit(name)

    def popup_at(self, global_pos):
        self.adjustSize()
        self.move(global_pos)
        self.show()
        self.raise_()
