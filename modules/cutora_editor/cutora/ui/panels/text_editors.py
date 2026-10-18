# SPDX-License-Identifier: GPL-3.0-or-later
# Extracted from the T01 monolith by tools/split_monolith.py.
# Original lines: 3960-4001, 4003-4066, 8785-8965, 8968-8995

from PySide6.QtWidgets import (
    QHBoxLayout,
    QVBoxLayout,
    QPushButton,
    QTextEdit,
    QLabel,
    QSpinBox,
    QColorDialog,
    QComboBox,
    QDialog,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QTextCursor,
    QTextCharFormat,
)
from cutora.ui.panels.fonts import (
    DEFAULT_FONT_FAMILY,
    register_font_combo,
)
from cutora.subtitle.text_utils import (
    _RT_PROP_STROKE_COLOR,
    _RT_PROP_STROKE_WIDTH,
    _RT_PROP_SHADOW_COLOR,
    _RT_PROP_SHADOW_OPACITY,
    _RT_PROP_SHADOW_BLUR,
    _RT_PROP_SHADOW_OFF_X,
    _RT_PROP_SHADOW_OFF_Y,
    _RICH_PARSE_FAST_LIMIT,
    _RICH_PARSE_BLOCK_LIMIT,
    _rich_default_layout,
)
from PySide6.QtCore import Qt
from cutora.subtitle.text_utils import _RICH_PARSE_BLOCK_LIMIT, _RICH_PARSE_FAST_LIMIT, _RT_PROP_SHADOW_BLUR, _RT_PROP_SHADOW_COLOR, _RT_PROP_SHADOW_OFF_X, _RT_PROP_SHADOW_OFF_Y, _RT_PROP_SHADOW_OPACITY, _RT_PROP_STROKE_COLOR, _RT_PROP_STROKE_WIDTH, _rich_default_layout
from cutora.ui.panels.fonts import DEFAULT_FONT_FAMILY, register_font_combo
def _editor_set_spans(editor, spans, base_style):
    cursor = editor.textCursor()
    cursor.beginEditBlock()
    cursor.select(QTextCursor.SelectionType.Document)
    cursor.removeSelectedText()
    cursor.deletePreviousChar()
    if not spans:
        cursor.insertText("")
        cursor.endEditBlock()
        return
    for span in spans:
        if not isinstance(span, dict):
            continue
        text = str(span.get("text", ""))
        if text == "":
            continue
        fmt = QTextCharFormat()
        _set_display_family(fmt, span.get("font_family", base_style.get("font_family", DEFAULT_FONT_FAMILY)))
        fmt.setFontPointSize(float(span.get("fontsize", base_style.get("fontsize", 40))))
        fmt.setFontWeight(QFont.Weight.Bold if span.get("bold", base_style.get("bold", False)) else QFont.Weight.Normal)
        fmt.setFontItalic(bool(span.get("italic", base_style.get("italic", False))))
        fmt.setForeground(QColor(span.get("color", base_style.get("color", "#FFFFFF"))))
        bg_c = span.get("bg_color")
        if bg_c:
            bg_color = QColor(bg_c)
            bg_color.setAlpha(int(span.get("bg_opacity", 255)))
            fmt.setBackground(bg_color)
        if span.get("use_stroke", False):
            fmt.setProperty(_RT_PROP_STROKE_COLOR, span.get("stroke_color", base_style.get("stroke_color", "#000000")))
            fmt.setProperty(_RT_PROP_STROKE_WIDTH, int(span.get("stroke_width", base_style.get("stroke_width", 0))))
        if span.get("shadow_enabled", False):
            fmt.setProperty(_RT_PROP_SHADOW_COLOR, span.get("shadow_color", base_style.get("shadow_color", "#000000")))
            fmt.setProperty(_RT_PROP_SHADOW_OPACITY, int(span.get("shadow_opacity", base_style.get("shadow_opacity", 120))))
            fmt.setProperty(_RT_PROP_SHADOW_BLUR, int(span.get("shadow_blur", base_style.get("shadow_blur", 0))))
            fmt.setProperty(_RT_PROP_SHADOW_OFF_X, int(span.get("shadow_offset_x", base_style.get("shadow_offset_x", 4))))
            fmt.setProperty(_RT_PROP_SHADOW_OFF_Y, int(span.get("shadow_offset_y", base_style.get("shadow_offset_y", 4))))
        parts = text.split("\n")
        for pi, part in enumerate(parts):
            cursor.insertText(part, fmt)
            if pi < len(parts) - 1:
                cursor.insertBlock()
    cursor.endEditBlock()

def _editor_to_spans(editor, base_style):
    spans = []
    doc = editor.document()
    try:
        if doc.characterCount() > _RICH_PARSE_FAST_LIMIT or doc.blockCount() > _RICH_PARSE_BLOCK_LIMIT:
            text = editor.toPlainText()
            return [{
                "text": text,
                "font_family": base_style.get("font_family", DEFAULT_FONT_FAMILY),
                "fontsize": int(base_style.get("fontsize", 40)),
                "bold": bool(base_style.get("bold", False)),
                "italic": bool(base_style.get("italic", False)),
                "color": base_style.get("color", "#FFFFFF"),
            }]
    except Exception:
        text = editor.toPlainText()
        return [{
            "text": text,
            "font_family": base_style.get("font_family", DEFAULT_FONT_FAMILY),
            "fontsize": int(base_style.get("fontsize", 40)),
            "bold": bool(base_style.get("bold", False)),
            "italic": bool(base_style.get("italic", False)),
            "color": base_style.get("color", "#FFFFFF"),
        }]
    block = doc.begin()
    while block.isValid():
        it = block.begin()
        while not it.atEnd():
            frag = it.fragment()
            if frag.isValid():
                text = frag.text()
                if text:
                    fmt = frag.charFormat()
                    span = {
                        "text": text,
                        "font_family": _stored_family(fmt) or base_style.get("font_family", DEFAULT_FONT_FAMILY),
                        "fontsize": int(fmt.fontPointSize() or base_style.get("fontsize", 40)),
                        "bold": fmt.fontWeight() >= QFont.Weight.Bold,
                        "italic": fmt.fontItalic(),
                        "color": fmt.foreground().color().name() if fmt.foreground().style() != Qt.BrushStyle.NoBrush else base_style.get("color", "#FFFFFF"),
                    }
                    if fmt.background().style() != Qt.BrushStyle.NoBrush:
                        bg = fmt.background().color()
                        span["bg_color"] = bg.name()
                        span["bg_opacity"] = bg.alpha()
                    stroke_color = fmt.property(_RT_PROP_STROKE_COLOR)
                    if stroke_color:
                        span["use_stroke"] = True
                        span["stroke_color"] = stroke_color
                        span["stroke_width"] = int(fmt.property(_RT_PROP_STROKE_WIDTH) or 0)
                    shadow_color = fmt.property(_RT_PROP_SHADOW_COLOR)
                    if shadow_color:
                        span["shadow_enabled"] = True
                        span["shadow_color"] = shadow_color
                        span["shadow_opacity"] = int(fmt.property(_RT_PROP_SHADOW_OPACITY) or 120)
                        span["shadow_blur"] = int(fmt.property(_RT_PROP_SHADOW_BLUR) or 0)
                        span["shadow_offset_x"] = int(fmt.property(_RT_PROP_SHADOW_OFF_X) or 4)
                        span["shadow_offset_y"] = int(fmt.property(_RT_PROP_SHADOW_OFF_Y) or 4)
                    spans.append(span)
            it += 1
        block = block.next()
        if block.isValid():
            spans.append({"text": "\n"})
    return spans

# (AdvTextBlockDialog removed, replaced by inline WordBlockPanel)

# --- Rich Text Editor Dialog ---

def _set_display_family(fmt, family) -> None:
    """Q170：编辑框里显示用 render_font_family（会卡死的字体换默认字体），真实家族名另存一份，存盘读回它。"""
    from cutora.ui.panels.fonts import render_font_family
    from cutora.ui.panels.text_rich import _RT_FONT_FAMILY_PROP
    fam = str(family or "")
    fmt.setFontFamily(render_font_family(fam) if fam else fam)
    if fam:
        fmt.setProperty(_RT_FONT_FAMILY_PROP, fam)


def _stored_family(fmt) -> str:
    from cutora.ui.panels.text_rich import _RT_FONT_FAMILY_PROP
    fam = fmt.property(_RT_FONT_FAMILY_PROP)
    return str(fam) if fam else fmt.fontFamily()

class RichTextEditorDialog(QDialog):
    def __init__(self, parent, base_style, spans=None, layout=None):
        super().__init__(parent)
        self.setWindowTitle("富文本编辑")
        self.setMinimumSize(640, 420)
        self.setSizeGripEnabled(True)
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.Window)
        self.base_style = base_style or {}
        self.layout_conf = _rich_default_layout(layout)

        self.editor = QTextEdit()
        self.editor.setAcceptRichText(True)

        toolbar = QHBoxLayout()
        self.font_combo = QComboBox()
        register_font_combo(self.font_combo)
        self.font_combo.setCurrentText(self.base_style.get("font_family", DEFAULT_FONT_FAMILY))
        self.font_combo.currentTextChanged.connect(self._apply_font_family)
        toolbar.addWidget(self.font_combo)

        self.size_spin = QSpinBox()
        self.size_spin.setRange(6, 300)
        self.size_spin.setValue(int(self.base_style.get("fontsize", 40)))
        self.size_spin.valueChanged.connect(self._apply_font_size)
        toolbar.addWidget(self.size_spin)

        self.bold_btn = QPushButton("B")
        self.bold_btn.setCheckable(True)
        self.bold_btn.setChecked(bool(self.base_style.get("bold", False)))
        self.bold_btn.clicked.connect(self._toggle_bold)
        toolbar.addWidget(self.bold_btn)

        self.italic_btn = QPushButton("I")
        self.italic_btn.setCheckable(True)
        self.italic_btn.setChecked(bool(self.base_style.get("italic", False)))
        self.italic_btn.clicked.connect(self._toggle_italic)
        toolbar.addWidget(self.italic_btn)

        self.color_btn = QPushButton("颜色")
        self.color_btn.clicked.connect(self._pick_text_color)
        toolbar.addWidget(self.color_btn)

        self.bg_btn = QPushButton("背景色")
        self.bg_btn.clicked.connect(self._pick_bg_color)
        toolbar.addWidget(self.bg_btn)

        layout_row = QHBoxLayout()
        self.pad_l = QSpinBox(); self.pad_l.setRange(0, 1000); self.pad_l.setValue(int(self.layout_conf.get("left_pad", 0)))
        self.pad_r = QSpinBox(); self.pad_r.setRange(0, 1000); self.pad_r.setValue(int(self.layout_conf.get("right_pad", 0)))
        self.pad_t = QSpinBox(); self.pad_t.setRange(0, 1000); self.pad_t.setValue(int(self.layout_conf.get("top_pad", 0)))
        self.pad_b = QSpinBox(); self.pad_b.setRange(0, 1000); self.pad_b.setValue(int(self.layout_conf.get("bottom_pad", 0)))
        layout_row.addWidget(QLabel("左缩进")); layout_row.addWidget(self.pad_l)
        layout_row.addWidget(QLabel("右缩进")); layout_row.addWidget(self.pad_r)
        layout_row.addWidget(QLabel("上缩进")); layout_row.addWidget(self.pad_t)
        layout_row.addWidget(QLabel("下缩进")); layout_row.addWidget(self.pad_b)

        spacing_row = QHBoxLayout()
        self.line_spacing = QSpinBox(); self.line_spacing.setRange(0, 200); self.line_spacing.setValue(int(self.layout_conf.get("line_spacing", 0)))
        self.letter_spacing = QSpinBox(); self.letter_spacing.setRange(-20, 100); self.letter_spacing.setValue(int(self.layout_conf.get("letter_spacing", 0)))
        spacing_row.addWidget(QLabel("行间距")); spacing_row.addWidget(self.line_spacing)
        spacing_row.addWidget(QLabel("字间距")); spacing_row.addWidget(self.letter_spacing)

        effects_row = QHBoxLayout()
        self.stroke_w = QSpinBox(); self.stroke_w.setRange(0, 50); self.stroke_w.setValue(int(self.base_style.get("stroke_width", 0)))
        self.stroke_c = QPushButton("描边色")
        self.stroke_c.clicked.connect(lambda: self._pick_effect_color(self.stroke_c))
        self.shadow_c = QPushButton("投影色")
        self.shadow_c.clicked.connect(lambda: self._pick_effect_color(self.shadow_c))
        self.shadow_op = QSpinBox(); self.shadow_op.setRange(0, 255); self.shadow_op.setValue(int(self.base_style.get("shadow_opacity", 120)))
        self.shadow_blur = QSpinBox(); self.shadow_blur.setRange(0, 50); self.shadow_blur.setValue(int(self.base_style.get("shadow_blur", 0)))
        self.shadow_off_x = QSpinBox(); self.shadow_off_x.setRange(-200, 200); self.shadow_off_x.setValue(int(self.base_style.get("shadow_offset_x", 4)))
        self.shadow_off_y = QSpinBox(); self.shadow_off_y.setRange(-200, 200); self.shadow_off_y.setValue(int(self.base_style.get("shadow_offset_y", 4)))
        self.apply_fx_btn = QPushButton("应用效果到选区")
        self.apply_fx_btn.clicked.connect(self._apply_effects_to_selection)
        effects_row.addWidget(QLabel("描边宽")); effects_row.addWidget(self.stroke_w)
        effects_row.addWidget(self.stroke_c)
        effects_row.addWidget(self.shadow_c)
        effects_row.addWidget(QLabel("投影透明")); effects_row.addWidget(self.shadow_op)
        effects_row.addWidget(QLabel("投影羽化")); effects_row.addWidget(self.shadow_blur)
        effects_row.addWidget(QLabel("X")); effects_row.addWidget(self.shadow_off_x)
        effects_row.addWidget(QLabel("Y")); effects_row.addWidget(self.shadow_off_y)
        effects_row.addWidget(self.apply_fx_btn)

        btn_row = QHBoxLayout()
        ok_btn = QPushButton("确定")
        cancel_btn = QPushButton("取消")
        ok_btn.clicked.connect(self.accept)
        cancel_btn.clicked.connect(self.reject)
        btn_row.addStretch(1)
        btn_row.addWidget(ok_btn)
        btn_row.addWidget(cancel_btn)

        root = QVBoxLayout(self)
        root.addLayout(toolbar)
        root.addWidget(self.editor, 1)
        root.addLayout(layout_row)
        root.addLayout(spacing_row)
        root.addLayout(effects_row)
        root.addLayout(btn_row)

        _editor_set_spans(self.editor, spans or [], self.base_style)

    def _apply_char_format(self, fmt):
        cursor = self.editor.textCursor()
        if not cursor.hasSelection():
            cursor.select(QTextCursor.SelectionType.WordUnderCursor)
        cursor.mergeCharFormat(fmt)
        self.editor.mergeCurrentCharFormat(fmt)

    def _apply_font_family(self, font):
        fmt = QTextCharFormat()
        font_family_name = font if isinstance(font, str) else font.family()
        _set_display_family(fmt, font_family_name)
        self._apply_char_format(fmt)

    def _apply_font_size(self, size):
        fmt = QTextCharFormat()
        fmt.setFontPointSize(float(size))
        self._apply_char_format(fmt)

    def _toggle_bold(self):
        fmt = QTextCharFormat()
        fmt.setFontWeight(QFont.Weight.Bold if self.bold_btn.isChecked() else QFont.Weight.Normal)
        self._apply_char_format(fmt)

    def _toggle_italic(self):
        fmt = QTextCharFormat()
        fmt.setFontItalic(self.italic_btn.isChecked())
        self._apply_char_format(fmt)

    def _pick_text_color(self):
        c = QColorDialog.getColor()
        if not c.isValid():
            return
        fmt = QTextCharFormat()
        fmt.setForeground(c)
        self._apply_char_format(fmt)

    def _pick_bg_color(self):
        c = QColorDialog.getColor()
        if not c.isValid():
            return
        fmt = QTextCharFormat()
        fmt.setBackground(c)
        self._apply_char_format(fmt)

    def _pick_effect_color(self, btn):
        c = QColorDialog.getColor()
        if not c.isValid():
            return
        btn.setProperty("color", c.name())
        from cutora.ui.theme.apply import apply_swatch
        apply_swatch(btn, c.name())

    def _apply_effects_to_selection(self):
        fmt = QTextCharFormat()
        stroke_color = self.stroke_c.property("color") or self.base_style.get("stroke_color", "#000000")
        shadow_color = self.shadow_c.property("color") or self.base_style.get("shadow_color", "#000000")
        fmt.setProperty(_RT_PROP_STROKE_COLOR, stroke_color)
        fmt.setProperty(_RT_PROP_STROKE_WIDTH, int(self.stroke_w.value()))
        fmt.setProperty(_RT_PROP_SHADOW_COLOR, shadow_color)
        fmt.setProperty(_RT_PROP_SHADOW_OPACITY, int(self.shadow_op.value()))
        fmt.setProperty(_RT_PROP_SHADOW_BLUR, int(self.shadow_blur.value()))
        fmt.setProperty(_RT_PROP_SHADOW_OFF_X, int(self.shadow_off_x.value()))
        fmt.setProperty(_RT_PROP_SHADOW_OFF_Y, int(self.shadow_off_y.value()))
        self._apply_char_format(fmt)

    def get_result(self):
        spans = _editor_to_spans(self.editor, self.base_style)

        layout = {
            "left_pad": self.pad_l.value(),
            "right_pad": self.pad_r.value(),
            "top_pad": self.pad_t.value(),
            "bottom_pad": self.pad_b.value(),
            "line_spacing": self.line_spacing.value(),
            "letter_spacing": self.letter_spacing.value()
        }
        return spans, layout

class SimpleTextEditorDialog(QDialog):
    def __init__(self, parent, text=""):
        super().__init__(parent)
        self.setWindowTitle("文本编辑")
        self.setMinimumSize(520, 360)
        self.setSizeGripEnabled(True)
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.Window)

        self.editor = QTextEdit()
        self.editor.setAcceptRichText(False)
        self.editor.setPlainText(text or "")

        ok_btn = QPushButton("确定")
        cancel_btn = QPushButton("取消")
        ok_btn.clicked.connect(self.accept)
        cancel_btn.clicked.connect(self.reject)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        btn_row.addWidget(ok_btn)
        btn_row.addWidget(cancel_btn)

        layout = QVBoxLayout(self)
        layout.addWidget(self.editor)
        layout.addLayout(btn_row)

    def get_text(self):
        return self.editor.toPlainText()
