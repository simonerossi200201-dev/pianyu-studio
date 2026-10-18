# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Rich-text toolbar + QTextEdit ↔ runs helpers for the text overlay panel."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont, QTextCharFormat, QTextCursor, QTextFormat
from PySide6.QtWidgets import (
    QColorDialog,
    QComboBox,
    QHBoxLayout,
    QSpinBox,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from cutora.ui.i18n import tr
from cutora.ui.theme.tokens import INSPECTOR_FIELD_GAP, INSPECTOR_SPIN_WIDTH, INSPECTOR_TEXT_H
from cutora.ui.widgets.responsive import mark_explicit

# Store family on the format; do not read QTextCharFormat.fontFamily()
# (Q44: PySide6 fragment.fontFamily access violation).
try:
    _RT_FONT_FAMILY_PROP = int(QTextFormat.Property.UserProperty) + 17
except (AttributeError, TypeError):
    _RT_FONT_FAMILY_PROP = 0x100011


# The editor previews text at a readable UI size, not at the layer's real
# size (a 40px layer used to fill the box with two giant words). Real sizes
# stay the data; the editor multiplies by ``_rich_scale`` for display only.
_EDITOR_BASE_PT = 11.0


def _editor_scale(editor) -> float:
    try:
        k = float(editor.property("_rich_scale") or 1.0)
    except (TypeError, ValueError):
        k = 1.0
    return k if k > 0 else 1.0


def _scale_for_base(base: dict) -> float:
    try:
        size = float((base or {}).get("fontsize") or 0)
    except (TypeError, ValueError):
        size = 0.0
    return _EDITOR_BASE_PT / size if size > 0 else 1.0


def _set_fmt_family(fmt: QTextCharFormat, family: str) -> None:
    family = str(family or "")
    if not family:
        return
    # Q170：编辑框里画字用 render_font_family（体检判定会卡死的字体换默认字体）；真实家族名存在自定义属性里，读回用它
    from cutora.ui.panels.fonts import render_font_family
    shown = render_font_family(family)
    if hasattr(fmt, "setFontFamilies"):
        fmt.setFontFamilies([shown])
    else:
        fmt.setFontFamily(shown)
    fmt.setProperty(_RT_FONT_FAMILY_PROP, family)


class RichTextBar(QWidget):
    """Bold / italic / underline / color / size / family / clear-format."""

    formatChanged = Signal()

    def __init__(self, editor: QTextEdit, parent=None):
        super().__init__(parent)
        self._editor = editor
        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(INSPECTOR_FIELD_GAP)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(INSPECTOR_FIELD_GAP)

        from cutora.ui.widgets.inspector import inspector_icon_button

        self.btn_bold = inspector_icon_button("bold", tr("粗体（选中文字）"), checkable=True)
        self.btn_bold.clicked.connect(self._toggle_bold)
        self.btn_italic = inspector_icon_button("italic", tr("斜体（选中文字）"), checkable=True)
        self.btn_italic.clicked.connect(self._toggle_italic)
        self.btn_underline = inspector_icon_button(
            "underline", tr("下划线（选中文字）"), checkable=True)
        self.btn_underline.clicked.connect(self._toggle_underline)
        self.btn_color = inspector_icon_button("type", tr("选中文字的颜色"))
        self.btn_color.clicked.connect(self._pick_color)

        self.size_spin = QSpinBox(self)
        self.size_spin.setObjectName("richSizeSpin")
        self.size_spin.setRange(8, 400)
        self.size_spin.setValue(40)
        self.size_spin.setMaximumWidth(INSPECTOR_SPIN_WIDTH)
        mark_explicit(self.size_spin, "maximumWidth")
        self.size_spin.setToolTip(tr("选中文字的字号"))
        self.size_spin.valueChanged.connect(self._set_size)

        from cutora.ui.panels.fonts import register_font_combo
        from PySide6.QtWidgets import QSizePolicy
        self.font_combo = QComboBox(self)
        self.font_combo.setMinimumWidth(0)
        self.font_combo.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.font_combo.setToolTip(tr("选中文字的字体"))
        register_font_combo(self.font_combo)
        self.font_combo.currentTextChanged.connect(self._set_family)

        self.btn_clear = inspector_icon_button(
            "remove-formatting", tr("清除格式（回到本层正文字体/字号/颜色）"))
        self.btn_clear.clicked.connect(self._clear_format)

        for w in (self.btn_bold, self.btn_italic, self.btn_underline, self.btn_color):
            w.setParent(self)
            row.addWidget(w, 0)
        row.addSpacing(INSPECTOR_FIELD_GAP)
        row.addWidget(self.size_spin, 0)
        row.addStretch(1)
        self.btn_clear.setParent(self)
        row.addWidget(self.btn_clear, 0)
        col.addLayout(row)
        col.addWidget(self.font_combo)
        editor.cursorPositionChanged.connect(self._sync_from_cursor)

    def _merge(self, fmt: QTextCharFormat) -> None:
        cursor = self._editor.textCursor()
        if not cursor.hasSelection():
            cursor.select(QTextCursor.SelectionType.WordUnderCursor)
        cursor.mergeCharFormat(fmt)
        self._editor.mergeCurrentCharFormat(fmt)
        self.formatChanged.emit()

    def _toggle_bold(self) -> None:
        fmt = QTextCharFormat()
        on = self.btn_bold.isChecked()
        fmt.setFontWeight(QFont.Weight.Bold if on else QFont.Weight.Normal)
        self._merge(fmt)

    def _toggle_italic(self) -> None:
        fmt = QTextCharFormat()
        fmt.setFontItalic(self.btn_italic.isChecked())
        self._merge(fmt)

    def _toggle_underline(self) -> None:
        fmt = QTextCharFormat()
        fmt.setFontUnderline(self.btn_underline.isChecked())
        self._merge(fmt)

    def _pick_color(self) -> None:
        c = QColorDialog.getColor(
            self._editor.textColor(), self, tr("文字颜色"))
        if not c.isValid():
            return
        fmt = QTextCharFormat()
        fmt.setForeground(c)
        self._merge(fmt)

    def _set_size(self, value: int) -> None:
        fmt = QTextCharFormat()
        fmt.setFontPointSize(float(value) * _editor_scale(self._editor))
        self._merge(fmt)

    def _set_family(self, family: str) -> None:
        family = str(family or "").strip()
        if not family:
            return
        fmt = QTextCharFormat()
        _set_fmt_family(fmt, family)
        self._merge(fmt)

    def _clear_format(self) -> None:
        cursor = self._editor.textCursor()
        if not cursor.hasSelection():
            cursor.select(QTextCursor.SelectionType.Document)
        base = self._editor.property("_rich_base") or {}
        if not isinstance(base, dict):
            base = {}
        fmt = _base_char_format(base, _editor_scale(self._editor))
        cursor.setCharFormat(fmt)
        self._editor.setCurrentCharFormat(fmt)
        self.formatChanged.emit()

    def _sync_from_cursor(self) -> None:
        fmt = self._editor.currentCharFormat()
        self.btn_bold.blockSignals(True)
        self.btn_italic.blockSignals(True)
        self.btn_underline.blockSignals(True)
        self.size_spin.blockSignals(True)
        self.font_combo.blockSignals(True)
        self.btn_bold.setChecked(fmt.fontWeight() >= int(QFont.Weight.Bold))
        self.btn_italic.setChecked(bool(fmt.fontItalic()))
        self.btn_underline.setChecked(bool(fmt.fontUnderline()))
        size = fmt.fontPointSize()
        if size > 0:
            self.size_spin.setValue(int(round(size / _editor_scale(self._editor))))
        fam = fmt.property(_RT_FONT_FAMILY_PROP)
        if fam:
            self.font_combo.setCurrentText(str(fam))
        self.btn_bold.blockSignals(False)
        self.btn_italic.blockSignals(False)
        self.btn_underline.blockSignals(False)
        self.size_spin.blockSignals(False)
        self.font_combo.blockSignals(False)


class RichTextEditor(QWidget):
    """QTextEdit + toolbar. Emits runsChanged({text, runs})."""

    runsChanged = Signal(str, list)

    def __init__(self, parent=None):
        super().__init__(parent)
        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(INSPECTOR_FIELD_GAP)
        self.edit = QTextEdit(self)
        self.edit.setAcceptRichText(True)
        self.edit.setObjectName("richEdit")
        self.edit.setMinimumHeight(INSPECTOR_TEXT_H + INSPECTOR_TEXT_H // 2)
        self.edit.setMaximumHeight(INSPECTOR_TEXT_H * 2)
        mark_explicit(self.edit, "minimumHeight")
        mark_explicit(self.edit, "maximumHeight")
        self.bar = RichTextBar(self.edit, self)
        col.addWidget(self.bar)
        col.addWidget(self.edit)
        self.edit.textChanged.connect(self._emit_runs)
        self.bar.formatChanged.connect(self._emit_runs)
        self._busy = False

    def _emit_runs(self) -> None:
        if self._busy:
            return
        text, runs = document_to_runs(self.edit)
        self.runsChanged.emit(text, runs)

    def set_text_runs(self, text: str, runs: list | None, base: dict | None = None) -> None:
        self._busy = True
        try:
            apply_runs_to_editor(self.edit, text or "", runs or [], base or {})
        finally:
            self._busy = False
        # 工具条的字号 / 字体框显示光标处真正的格式（以前一直停在 40 / 默认字体）
        self.bar._sync_from_cursor()


def _base_char_format(base: dict, scale: float = 1.0) -> QTextCharFormat:
    fmt = QTextCharFormat()
    if base.get("font_family"):
        _set_fmt_family(fmt, str(base["font_family"]))
    try:
        size = float(base.get("fontsize") or 0)
    except (TypeError, ValueError):
        size = 0.0
    if size > 0:
        fmt.setFontPointSize(size * scale)
    fmt.setFontWeight(
        QFont.Weight.Bold if base.get("bold") else QFont.Weight.Normal)
    fmt.setFontItalic(bool(base.get("italic")))
    fmt.setFontUnderline(bool(base.get("underline")))
    color = str(base.get("color") or "")
    if color:
        fmt.setForeground(QColor(color))
    return fmt


def document_to_runs(editor: QTextEdit) -> tuple[str, list]:
    """Walk the document and emit runs for spans whose format differs from default."""
    doc = editor.document()
    text = editor.toPlainText()
    if doc is None or not text:
        return text, []
    block = doc.begin()
    runs = []
    pos = 0
    base = editor.property("_rich_base") or {}
    if not isinstance(base, dict):
        base = {}
    scale = _editor_scale(editor)
    default = _base_char_format(base, scale)
    while block.isValid():
        it = block.begin()
        while not it.atEnd():
            frag = it.fragment()
            if frag.isValid():
                piece = frag.text().replace("\u2029", "\n")
                fmt = QTextCharFormat(frag.charFormat())
                ov = _overrides_from_format(fmt, default, scale)
                n = len(piece)
                if ov and n:
                    runs.append({"start": pos, "end": pos + n, "overrides": ov})
                pos += n
            it += 1
        block = block.next()
        if block.isValid():
            # QTextDocument block separators are newlines in toPlainText.
            pos = min(len(text), pos + 1)
    return text, runs


def _overrides_from_format(fmt: QTextCharFormat, default: QTextCharFormat,
                           scale: float = 1.0) -> dict:
    ov = {}
    bold = fmt.fontWeight() >= int(QFont.Weight.Bold)
    dbold = default.fontWeight() >= int(QFont.Weight.Bold)
    if bold != dbold:
        ov["bold"] = bold
    if bool(fmt.fontItalic()) != bool(default.fontItalic()):
        ov["italic"] = bool(fmt.fontItalic())
    if bool(fmt.fontUnderline()) != bool(default.fontUnderline()):
        ov["underline"] = bool(fmt.fontUnderline())
    size = fmt.fontPointSize() / scale
    dsize = default.fontPointSize() / scale
    if size > 0 and (dsize <= 0 or abs(size - dsize) > 0.51):
        ov["fontsize"] = int(round(size))
    brush = fmt.foreground()
    fg = brush.color() if brush.style() != Qt.BrushStyle.NoBrush else QColor()
    dbrush = default.foreground()
    dfg = dbrush.color() if dbrush.style() != Qt.BrushStyle.NoBrush else QColor()
    if fg.isValid() and fg.alpha() > 0:
        dname = dfg.name().upper() if dfg.isValid() else ""
        if fg.name().upper() != dname:
            ov["color"] = fg.name().upper()
    fam = fmt.property(_RT_FONT_FAMILY_PROP)
    dfam = default.property(_RT_FONT_FAMILY_PROP)
    if fam and str(fam) != str(dfam or ""):
        ov["font_family"] = str(fam)
    return ov


def apply_runs_to_editor(editor: QTextEdit, text: str, runs: list, base: dict) -> None:
    editor.blockSignals(True)
    editor.setProperty("_rich_base", dict(base or {}))
    scale = _scale_for_base(base or {})
    editor.setProperty("_rich_scale", scale)
    editor.setPlainText(text or "")
    cursor = editor.textCursor()
    base_fmt = _base_char_format(base or {}, scale)
    cursor.select(QTextCursor.SelectionType.Document)
    cursor.setCharFormat(base_fmt)
    cursor.clearSelection()
    # Q145：run 的范围超过正文长度（预设里存的是预设那段字的长度）时，以前
    # setPosition 越界直接失败 —— 这段格式没进编辑框，之后编辑框任何改动都会把
    # 它当成「没有格式」写回去，字体就变回默认了。夹到正文长度内。
    n = len(editor.toPlainText())
    for item in runs or []:
        if not isinstance(item, dict):
            continue
        try:
            start = max(0, min(n, int(item.get("start") or 0)))
            end = max(0, min(n, int(item.get("end") or 0)))
        except (TypeError, ValueError):
            continue
        ov = item.get("overrides") if isinstance(item.get("overrides"), dict) else {}
        if end <= start or not ov:
            continue
        cursor.setPosition(start)
        cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
        fmt = QTextCharFormat(base_fmt)
        if "bold" in ov:
            fmt.setFontWeight(QFont.Weight.Bold if ov["bold"] else QFont.Weight.Normal)
        if "italic" in ov:
            fmt.setFontItalic(bool(ov["italic"]))
        if "underline" in ov:
            fmt.setFontUnderline(bool(ov["underline"]))
        if ov.get("fontsize"):
            fmt.setFontPointSize(float(ov["fontsize"]) * scale)
        if ov.get("font_family"):
            _set_fmt_family(fmt, str(ov["font_family"]))
        if ov.get("color"):
            fmt.setForeground(QColor(str(ov["color"])))
        cursor.mergeCharFormat(fmt)
    cursor.clearSelection()
    editor.setCurrentCharFormat(base_fmt)
    editor.blockSignals(False)
