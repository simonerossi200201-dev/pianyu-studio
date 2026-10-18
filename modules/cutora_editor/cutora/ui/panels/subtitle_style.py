# SPDX-License-Identifier: GPL-3.0-or-later
# Extracted from the T01 monolith by tools/split_monolith.py.
# Original lines: 572-671, 1989-1997, 1999-2003, 2006-2041, 6614-6646, 6764-8683

import os
import copy
from PySide6.QtWidgets import (
    QWidget,
    QHBoxLayout,
    QVBoxLayout,
    QPushButton,
    QLabel,
    QMessageBox,
    QSpinBox,
    QColorDialog,
    QSlider,
    QCheckBox,
    QScrollArea,
    QInputDialog,
    QFileDialog,
    QComboBox,
    QDoubleSpinBox,
    QPlainTextEdit,
    QMenu,
    QDialog,
    QSizePolicy,
    QToolButton,
    QAbstractSpinBox,
)
from PySide6.QtCore import (
    Qt,
    Signal,
    QSize,
)
from PySide6.QtGui import QColor
from cutora.ui.widgets.collapsible import ToggleSwitch
from cutora.ui.panels.fonts import (
    _get_default_font_family,
    register_font_combo,
    get_font_manager,
    sanitize_font_family,
    force_allowed_font,
    resolve_font_request,
    family_style_names,
    resolve_font_style,
)
from PySide6.QtWidgets import (
    QMenu,
    QFileDialog,
    QMessageBox,
)
from PySide6.QtCore import QEvent, Qt
from cutora.ui.panels.fonts import _get_default_font_family, force_allowed_font, get_font_manager, register_font_combo, sanitize_font_family

# Bound by cutora.ui.panels.property after that module finishes loading
# (extracted first to break the ColorOptionPanel cycle).
ColorOptionPanel = None

from cutora.subtitle.style_schema import CANONICAL_STYLE_KEYS

STYLE_KEYS = list(CANONICAL_STYLE_KEYS)

# collect() may keep these flats; everything else is fx. Keep in sync with
# tests/test_t32_panel.py::PANEL_FLAT_WHITELIST.
_COLLECT_FLAT_KEYS = (
    "font_family", "font_style", "fontsize", "bold", "italic",
    "pos_x", "pos_y", "rotation",
    "wrap_width_percent", "wrap_lines", "line_spacing",
    "word_mode",
    "layout_engine", "safe_margin_pct", "max_words_per_line", "pause_break_sec",
    "geom_user_set", "anchor",
    "offset_x_pct", "offset_y_pct", "fontsize_pct",
    "pos_x_pct", "pos_y_pct",
    "layout_rules", "preset_name",
    "opacity_text_global",
)


def copy_style_full(src_style: dict) -> dict:
    import copy
    from cutora.subtitle.style_schema import migrate_subtitle_style
    if not isinstance(src_style, dict):
        return {"font_family": "Arimo"}
    out = copy.deepcopy(src_style)
    migrate_subtitle_style(out)
    # Sanitize font to prevent crash
    if out.get("font_family"):
        out["font_family"] = sanitize_font_family(out.get("font_family"))
    return out

# 绑定文本预设的样式拷贝与字段白名单（包含文本对象关键字段）
_TEXT_TEMPLATE_KEYS = [
    "content", "text", "content_format", "rich_content", "text_layout",
    "x", "y", "w", "h", "rotation", "opacity"
]

def apply_text_template_to_overlay(ov, template):
    """将文本模板应用到 overlay（不覆盖 id/start/end/type 以及文本和空间等结构属性）。"""
    if not isinstance(ov, dict) or not isinstance(template, dict):
        return
    
    # ── 必须保护的所有结构属性 ──
    # 注意：移除了 'content', 'text', 'content_format', 'rich_content', 'spans'
    # 以便应用预设时能够恢复预设中保存的独立文本内容。
    structural_keys = {
        "id", "start", "end", "_track_id", "type", "track_index",
        "x", "y", "w", "h", "rotation", "opacity", "text_layout",
        "blend_mode", "_preset_name",
    }
    saved_state = {k: ov[k] for k in structural_keys if k in ov}
    
    # 额外保留原文本内容作为后备，防止预设中完全没有保存过文本导致文字消失
    orig_text_state = {
        "content": ov.get("content"),
        "text": ov.get("text"),
        "content_format": ov.get("content_format"),
        "rich_content": ov.get("rich_content"),
        "spans": ov.get("spans")
    }
    
    ov.clear()
    ov.update(copy.deepcopy(template))
    
    # Force restore ALL structural/spatial identity fields
    for k, v in saved_state.items():
        ov[k] = v
        
    # 如果模板中没有任何文本内容，退回使用原本的内容
    if "content" not in ov and "text" not in ov and "rich_content" not in ov:
        for k, v in orig_text_state.items():
            if v is not None:
                ov[k] = v

class SubtitleEditDialog(QDialog):
    def __init__(self, text, parent=None):
        super().__init__(parent)
        self.setWindowTitle("编辑字幕 (Enter确定，Shift+Enter换行)")
        self.resize(500, 300)
        self.layout = QVBoxLayout(self)
        
        self.text_edit = QPlainTextEdit(text)
        self.layout.addWidget(self.text_edit)
        self.text_edit.installEventFilter(self)

        btn_layout = QHBoxLayout()
        self.btn_ok = QPushButton("确定 (Enter)")
        self.btn_ok.clicked.connect(self.accept)
        self.btn_cancel = QPushButton("取消 (Esc)")
        self.btn_cancel.clicked.connect(self.reject)
        
        btn_layout.addStretch()
        btn_layout.addWidget(self.btn_ok)
        btn_layout.addWidget(self.btn_cancel)
        self.layout.addLayout(btn_layout)

    def eventFilter(self, obj, event):
        if obj is self.text_edit and event.type() == QEvent.Type.KeyPress:
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                    # Shift+Enter -> let text edit insert newline naturally
                    return False
                else:
                    # Enter alone -> accept dialog
                    self.accept()
                    return True
        return super().eventFilter(obj, event)

# --- Subtitle Style Panel (Updated: Padding X/Y, Blur, Alpha) ---
class SubtitleStylePanel(QWidget):
    param_changed = Signal(object)
    font_manage_request = Signal()
    recalc_textbox_request = Signal()
    relayout_request = Signal()
    wrap_width_committed = Signal(int)
    
    def __init__(self, is_word_block=False):
        super().__init__()
        self.is_word_block = is_word_block
        self.setObjectName("SubtitleStylePanel")
        self.init_ui()
        self.current_data = None
        self.block_signals = False
        self._fx_dirty = False

    def init_ui(self):
        from cutora.ui.widgets.inspector import SliderSpin, build_inspector
        from cutora.ui.panels.subtitle_layout import INSPECTOR_LAYOUT
        from cutora.ui.theme.tokens import INSPECTOR_PAD_X, SQUARE_SM

        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)

        self.sub_font = QComboBox()
        self.sub_font.clear()
        register_font_combo(self.sub_font)
        self.sub_font.setCurrentText(_get_default_font_family())
        self.sub_font.currentTextChanged.connect(self.on_change)
        
        if self.is_word_block:
            self.chk_detached = ToggleSwitch()
            self.chk_detached.setObjectName("errHint")
            self.chk_detached.setToolTip(
                "启用字块分离 (从原文流中脱离，支持独立位移/旋转/缩放)")
            self.chk_detached.toggled.connect(self.on_change)
        else:
            self.chk_detached = None

        from cutora.ui.i18n import tr as _tr
        from cutora.ui.icons import bind_icon, populate_icon_menu
        self.sub_preset_combo = QComboBox()
        self.sub_preset_combo.currentIndexChanged.connect(self._on_text_preset_changed)
        self._menu_btn = QToolButton()
        self._menu_btn.setObjectName("presetMenuBtn")
        self._menu_btn.setText("")
        self._menu_btn.setFixedSize(SQUARE_SM, SQUARE_SM)
        self._menu_btn.setIconSize(QSize(16, 16))
        self._menu_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self._menu_btn.setAutoRaise(True)
        self._menu_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._menu_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._menu_btn.setToolTip(_tr("预设：新增 / 更新 / 重命名 / 删除 / 导入导出"))
        bind_icon(self._menu_btn, "ellipsis", size=16, color_token="fg1")
        self._style_menu = QMenu(self)
        populate_icon_menu(self._style_menu, (
            (_tr("设为默认预设"), "star", self.save_as_default_style),
            None,
            (_tr("新增预设"), "plus", self._preset_add),
            (_tr("更新当前预设"), "save", self._preset_update),
            (_tr("重命名当前预设"), "pencil", self._preset_rename),
            (_tr("删除当前预设"), "trash-2", self._preset_delete),
            None,
            (_tr("导出预设"), "upload", self._preset_export),
            (_tr("导入预设"), "download", self._preset_import),
        ))
        self._menu_btn.setMenu(self._style_menu)
        self.btn_font_manage = QPushButton("管理")
        self.btn_font_manage.setToolTip("字体管理：导入、预览、启用/停用字体")
        self.btn_font_manage.clicked.connect(self.font_manage_request.emit)
        self.font_fallback_hint = QLabel("")
        self.font_fallback_hint.setObjectName("errHint")
        self.font_fallback_hint.setWordWrap(True)
        self.font_fallback_hint.hide()

        self.sub_size = QSpinBox(); self.sub_size.setRange(10, 400); self.sub_size.valueChanged.connect(self.on_change)
        self.sub_bold = QCheckBox("粗体"); self.sub_bold.toggled.connect(self.on_change)
        self.sub_italic = QCheckBox("斜体"); self.sub_italic.toggled.connect(self.on_change)
        self.sub_font_style = QComboBox()
        self.sub_font_style.setToolTip(
            "该字族磁盘上真实存在的样式。选了非空项后粗体/斜体复选框禁用，"
            "避免叠出合成斜体。")
        self.sub_font_style.currentIndexChanged.connect(self._on_font_style_changed)
        self._rebuild_font_style_combo(_get_default_font_family(), "")

        self.sub_x = SliderSpin(0, 10000, 1, 0)
        self.sub_x.valueChanged.connect(self.on_change)
        self.lbl_x = self.sub_x.spin
        self.sub_y = SliderSpin(0, 10000, 1, 0)
        self.sub_y.valueChanged.connect(self.on_change)
        self.lbl_y = self.sub_y.spin
        from cutora.ui.i18n import tr as _tr_geom
        self.btn_restore_pack_geom = QPushButton(_tr_geom("恢复为风格默认位置"))
        self.btn_restore_pack_geom.setObjectName("restorePackGeomBtn")
        self.btn_restore_pack_geom.setToolTip(
            _tr_geom("清掉手动位置/字号，改回当前风格包的默认排布"))
        self.btn_restore_pack_geom.clicked.connect(self._restore_pack_geometry)
        from cutora.render.safe_zones import disclaimer as _sz_disc
        self.safe_zone_disclaimer = QLabel(_sz_disc() or "参考值，随平台版本变化")
        self.safe_zone_disclaimer.setObjectName("hint")
        self.safe_zone_disclaimer.setWordWrap(True)
        self.safe_zone_hint = QLabel("")
        self.safe_zone_hint.setWordWrap(True)
        self.btn_move_to_safe_zone = QPushButton("移到安全区")
        self.btn_move_to_safe_zone.clicked.connect(self._move_to_safe_zone)
        self.safe_zone_hint.hide()
        self.btn_move_to_safe_zone.hide()

        self.sub_rot = QSpinBox(); self.sub_rot.setRange(-360, 360); self.sub_rot.valueChanged.connect(self.on_change)

        self.sub_box = ToggleSwitch()
        self.sub_box.toggled.connect(self._on_bg_enable)
        from cutora.ui.panels.fx_fields import mount_overlay_bg
        mount_overlay_bg(self, None)

        self.sub_strk = ToggleSwitch()
        self.sub_strk.toggled.connect(self.on_change)
        from cutora.ui.panels.section_toggle import SectionToggle
        self._stroke_toggle = SectionToggle("stroke.enabled", False, True)
        self._bg_toggle = SectionToggle("bg.enabled", False, True)
        self._glow_toggle = SectionToggle("glow.enabled", False, True)
        self._shadow_toggle = SectionToggle("shadow.enabled", False, True)
        self._extrude_toggle = SectionToggle("extrude.depth_em", 0, 0.12)
        self._texture_toggle = SectionToggle(
            "bg.type", "none", "texture", on_when="equals_on")
        self._fonts_toggle = SectionToggle("fonts.emphasis", "", "Anton")
        self._scatter_toggle = SectionToggle("scatter.enabled", False, True)
        self._emphasis_toggle = SectionToggle(
            "emphasis",
            {"auto": [], "words": []},
            {"auto": ["numbers"], "words": []},
        )
        self.sub_extrude_en = ToggleSwitch()
        self.sub_extrude_en.toggled.connect(self.on_change)
        self.sub_texture_en = ToggleSwitch()
        self.sub_texture_en.toggled.connect(self.on_change)
        self.sub_fonts_en = ToggleSwitch()
        self.sub_fonts_en.toggled.connect(self.on_change)
        self.sub_emphasis_en = ToggleSwitch()
        self.sub_emphasis_en.toggled.connect(self.on_change)
        self.fx_scatter_enabled = ToggleSwitch()
        self.fx_scatter_enabled.toggled.connect(self.on_change)
        from cutora.ui.panels.fx_fields import mount_stroke_fx
        mount_stroke_fx(self, None)

        self.sub_glow_en = ToggleSwitch()
        self.sub_glow_en.toggled.connect(self.on_change)
        from cutora.ui.panels.fx_fields import mount_glow_fx
        mount_glow_fx(self, None)

        self.sub_shadow_en = ToggleSwitch()
        self.sub_shadow_en.toggled.connect(self.on_change)
        from cutora.ui.panels.fx_fields import mount_shadow_fx
        mount_shadow_fx(self, None)

        self.sub_dyn = ToggleSwitch()
        self.sub_dyn.toggled.connect(self.on_change)
        self.sub_dyn_tint = ToggleSwitch()
        self.sub_dyn_tint.setChecked(True)
        self.sub_dyn_tint.setToolTip("正在读的词换成这一段自己的字色，不走强调色")
        self.sub_dyn_tint.toggled.connect(self.on_change)
        self.sub_dyn_box = ToggleSwitch()
        self.sub_dyn_box.setChecked(True)
        self.sub_dyn_box.setToolTip("当前词下面加一块底。颜色是这一段自己的底块颜色。")
        self.sub_dyn_box.toggled.connect(self.on_change)
        from cutora.ui.panels.fx_fields import mount_follow_word_tint
        mount_follow_word_tint(self, None)
        self.sub_dyn_op = SliderSpin(0, 255, 1, 0)
        self.sub_dyn_op.setToolTip("弹力块透明度")
        self.sub_dyn_op.valueChanged.connect(self.on_change)
        self.sub_dyn_op_spin = self.sub_dyn_op.spin
        self.sub_dyn_pad = QSpinBox(); self.sub_dyn_pad.setRange(0, 50); self.sub_dyn_pad.setToolTip("弹力块扩张大小")
        self.sub_dyn_pad.valueChanged.connect(self.on_change)
        self.sub_dyn_off_y = QSpinBox(); self.sub_dyn_off_y.setRange(-50, 50); self.sub_dyn_off_y.setToolTip("弹力块上下偏移")
        self.sub_dyn_off_y.valueChanged.connect(self.on_change)
        self.sub_dyn_rad = QSpinBox(); self.sub_dyn_rad.setRange(0, 50); self.sub_dyn_rad.setToolTip("弹力块圆角")
        self.sub_dyn_rad.valueChanged.connect(self.on_change)
        self.sub_dyn_hollow = QCheckBox("镂空")
        self.sub_dyn_hollow.toggled.connect(self.on_change)
        self.sub_dyn_stroke = QSpinBox(); self.sub_dyn_stroke.setRange(1, 25); self.sub_dyn_stroke.setToolTip("镂空线宽")
        self.sub_dyn_stroke.valueChanged.connect(self.on_change)
        self.sub_dyn_glow = QSpinBox(); self.sub_dyn_glow.setRange(0, 50); self.sub_dyn_glow.setToolTip("发光半径")
        self.sub_dyn_glow.valueChanged.connect(self.on_change)
        self.sub_dyn_anim = ToggleSwitch()
        self.sub_dyn_anim.setToolTip("启用缩放动画")
        self.sub_dyn_anim.toggled.connect(self.on_change)
        self.sub_dyn_anim_overshoot = QDoubleSpinBox()
        self.sub_dyn_anim_overshoot.setRange(1.0, 2.0)
        self.sub_dyn_anim_overshoot.setSingleStep(0.05)
        self.sub_dyn_anim_overshoot.setDecimals(2)
        self.sub_dyn_anim_overshoot.valueChanged.connect(self.on_change)
        self.sub_dyn_anim_dur = QDoubleSpinBox()
        self.sub_dyn_anim_dur.setRange(0.01, 1.0)
        self.sub_dyn_anim_dur.setSingleStep(0.01)
        self.sub_dyn_anim_dur.setDecimals(2)
        self.sub_dyn_anim_dur.valueChanged.connect(self.on_change)

        self.sub_adv_bar = ToggleSwitch()
        self.sub_adv_bar.setToolTip("跟随句子（整句底条）")
        self.sub_adv_bar.toggled.connect(self.on_change)
        from cutora.ui.panels.fx_fields import mount_line_bar_fx
        mount_line_bar_fx(self, None)

        from cutora.subtitle.stylepack import DEFAULT_WRAP_WIDTH_PERCENT
        from cutora.ui.i18n import tr as _tr_wrap
        self.sub_wrap_width = SliderSpin(0, 100, 1, 0)
        self.sub_wrap_width.setValue(int(DEFAULT_WRAP_WIDTH_PERCENT))
        self.sub_wrap_width.setSuffix("%")
        self.sub_wrap_width.setToolTip(_tr_wrap("一行字幕可用宽度占画布宽度的百分比"))
        self.sub_wrap_width_spin = self.sub_wrap_width.spin
        self.sub_wrap_width_hint = QLabel(_tr_wrap("每行约 %s 词") % 6)
        self.sub_wrap_width.valueChanged.connect(self._on_wrap_width_drag)
        self.sub_wrap_width.slider.sliderReleased.connect(self._on_wrap_width_commit)
        self.sub_wrap_width.spin.editingFinished.connect(self._on_wrap_width_commit)

        # Q137：断句三件套。默认值 = 老行为（8% 安全边距 / 不限每行词数 / 0.6 s 停顿）
        from cutora.subtitle.reflow import PAUSE_BREAK_DEFAULT, SAFE_MARGIN_DEFAULT_PCT
        self.sub_safe_margin = SliderSpin(0, 25, 1, 0)
        self.sub_safe_margin.setValue(int(SAFE_MARGIN_DEFAULT_PCT))
        self.sub_safe_margin.setSuffix("%")
        self.sub_safe_margin.setToolTip(_tr_wrap(
            "字幕离画面左右边缘至少留多少（单边，占画布宽度）。以前固定 8%，"
            "字幕宽度范围拉到 100% 也只能用到 84%；调小它，一行就能放更多词"))
        self.sub_safe_margin.valueChanged.connect(self.on_change)
        self.sub_max_words = QSpinBox()
        self.sub_max_words.setRange(0, 12)
        self.sub_max_words.setSpecialValueText(_tr_wrap("自动"))
        self.sub_max_words.setToolTip(_tr_wrap(
            "一行最多几个词（0 = 按宽度自动）。直接影响断句：一条字幕最多 行数×这个数 个词"))
        self.sub_max_words.valueChanged.connect(self.on_change)
        self.sub_pause_break = QDoubleSpinBox()
        self.sub_pause_break.setRange(0.1, 3.0)
        self.sub_pause_break.setDecimals(1)
        self.sub_pause_break.setSingleStep(0.1)
        self.sub_pause_break.setSuffix(" s")
        self.sub_pause_break.setValue(float(PAUSE_BREAK_DEFAULT))
        self.sub_pause_break.setToolTip(_tr_wrap(
            "重新断句时，两个词之间停顿超过这么久就另起一条。调大 = 更少更满的字幕"))
        self.sub_pause_break.valueChanged.connect(self.on_change)

        self.sub_lines = QSpinBox(); self.sub_lines.setRange(1, 10); self.sub_lines.valueChanged.connect(self.on_change)
        from cutora.ui.i18n import tr as _tr_relayout
        self.btn_relayout = QPushButton(_tr_relayout("重新断句"))
        self.btn_relayout.setToolTip(_tr_relayout("按当前排版重新断行并切分，不重跑识别"))
        self.btn_relayout.clicked.connect(self.relayout_request.emit)
        self.sub_line_spacing = QSpinBox(); self.sub_line_spacing.setRange(0, 200); self.sub_line_spacing.setToolTip("字幕行与行之间的像素间距")
        self.sub_line_spacing.valueChanged.connect(self.on_change)
        from cutora.ui.panels.fx_fields import mount_layout_fx, mount_word_mode
        mount_word_mode(self, None)
        mount_layout_fx(self, None)

        self.sub_adv_textbox = ToggleSwitch()
        self.sub_adv_textbox.setToolTip("启用高级文本框（Text模式）")
        self.sub_adv_textbox.toggled.connect(self.on_change)

        self.sub_adv_x = QSpinBox(); self.sub_adv_x.setRange(0, 10000); self.sub_adv_x.valueChanged.connect(self.on_change)
        self.sub_adv_y = QSpinBox(); self.sub_adv_y.setRange(0, 10000); self.sub_adv_y.valueChanged.connect(self.on_change)
        self.sub_adv_w = QSpinBox(); self.sub_adv_w.setRange(80, 10000); self.sub_adv_w.valueChanged.connect(self.on_change)
        self.sub_adv_h = QSpinBox(); self.sub_adv_h.setRange(40, 10000); self.sub_adv_h.valueChanged.connect(self.on_change)

        self.sub_adv_align = QComboBox()
        self.sub_adv_align.addItems(["左对齐", "居中", "右对齐"])
        self.sub_adv_align.currentIndexChanged.connect(self.on_change)

        self.sub_adv_recalc = QPushButton("重算字幕显示范围")
        self.sub_adv_recalc.clicked.connect(self._on_recalc_textbox)

        self.sub_lyrics_en = ToggleSwitch()
        self.sub_lyrics_en.setToolTip("将所有字幕段以垂直滚动歌词列表形式展示，当前行居中放大，弹簧物理过渡")
        self.sub_lyrics_en.toggled.connect(self.on_change)
        self.sub_lyrics_damp = QDoubleSpinBox()
        self.sub_lyrics_damp.setRange(0.1, 1.5)
        self.sub_lyrics_damp.setSingleStep(0.05)
        self.sub_lyrics_damp.setDecimals(2)
        self.sub_lyrics_damp.setToolTip("弹簧阻尼系数 ζ：<1 有弹跳感, =1 临界阻尼, >1 过阻尼")
        self.sub_lyrics_damp.valueChanged.connect(self.on_change)
        self.sub_lyrics_trans = QDoubleSpinBox()
        self.sub_lyrics_trans.setRange(0.05, 2.0)
        self.sub_lyrics_trans.setSingleStep(0.05)
        self.sub_lyrics_trans.setDecimals(2)
        self.sub_lyrics_trans.setToolTip("段落切换时的弹簧过渡时长（秒）")
        self.sub_lyrics_trans.valueChanged.connect(self.on_change)
        self.sub_lyrics_scale = QDoubleSpinBox()
        self.sub_lyrics_scale.setRange(1.0, 2.0)
        self.sub_lyrics_scale.setSingleStep(0.05)
        self.sub_lyrics_scale.setDecimals(2)
        self.sub_lyrics_scale.setToolTip("当前行（中心行）的缩放倍数")
        self.sub_lyrics_scale.valueChanged.connect(self.on_change)
        self.sub_lyrics_feather = QSpinBox()
        self.sub_lyrics_feather.setRange(0, 300)
        self.sub_lyrics_feather.setSuffix("px")
        self.sub_lyrics_feather.setToolTip("上下边缘的柔和消失过渡区域（像素值，0=硬切边）")
        self.sub_lyrics_feather.valueChanged.connect(self.on_change)
        self.sub_lyrics_gap = QSpinBox()
        self.sub_lyrics_gap.setRange(0, 200)
        self.sub_lyrics_gap.setSuffix("px")
        self.sub_lyrics_gap.setToolTip("歌词行间距（像素），影响可见行数：间距大→行数少，间距小→行数多")
        self.sub_lyrics_gap.valueChanged.connect(self.on_change)
        self.sub_lyrics_alpha_decay = QSpinBox()
        self.sub_lyrics_alpha_decay.setRange(0, 80)
        self.sub_lyrics_alpha_decay.setSuffix("%")
        self.sub_lyrics_alpha_decay.setToolTip("每行相对上一行降低的透明度百分比（0=不衰减，30=每行变透明30%）")
        self.sub_lyrics_alpha_decay.valueChanged.connect(self.on_change)
        self.sub_lyrics_center_offset = QSpinBox()
        self.sub_lyrics_center_offset.setRange(-500, 500)
        self.sub_lyrics_center_offset.setSuffix("px")
        self.sub_lyrics_center_offset.setToolTip("中心行位置偏移（正值=偏下，负值=偏上），打破对称感")
        self.sub_lyrics_center_offset.valueChanged.connect(self.on_change)
        self.sub_lyrics_center_color = QPushButton("中心行颜色")
        self.sub_lyrics_center_color.setToolTip("中心/活动行的文字颜色（默认金黄）")
        self.sub_lyrics_center_color.clicked.connect(lambda: self.pick_c('lyrics_center_color'))

        self.sub_adv_line_spacing = QSpinBox()
        self.sub_adv_line_spacing.setRange(0, 200)
        self.sub_adv_line_spacing.setToolTip("高级文本框独立行间距")
        self.sub_adv_line_spacing.valueChanged.connect(self.on_change)
        self.sub_adv_text_align = QComboBox()
        self.sub_adv_text_align.addItems(["左对齐", "居中", "右对齐"])
        self.sub_adv_text_align.currentIndexChanged.connect(self.on_change)
        self.sub_adv_stroke_en = ToggleSwitch()
        self.sub_adv_stroke_en.setToolTip("启用独立描边")
        self.sub_adv_stroke_en.toggled.connect(self.on_change)
        self.sub_adv_stroke_w = QSpinBox()
        self.sub_adv_stroke_w.setRange(0, 40)
        self.sub_adv_stroke_w.valueChanged.connect(self.on_change)
        self.sub_adv_stroke_op = SliderSpin(0, 255, 1, 0)
        self.sub_adv_stroke_op.valueChanged.connect(self.on_change)
        self.sub_adv_stroke_op_spin = self.sub_adv_stroke_op.spin
        self.sub_adv_stroke_c = QPushButton("描边颜色")
        self.sub_adv_stroke_c.clicked.connect(lambda: self.pick_c('adv_stroke_color'))
        self.sub_adv_bg_en = ToggleSwitch()
        self.sub_adv_bg_en.setToolTip("启用背景色块")
        self.sub_adv_bg_en.toggled.connect(self.on_change)
        self.sub_adv_bg_off_x = QSpinBox()
        self.sub_adv_bg_off_x.setRange(-5000, 5000)
        self.sub_adv_bg_off_x.valueChanged.connect(self.on_change)
        self.sub_adv_bg_off_y = QSpinBox()
        self.sub_adv_bg_off_y.setRange(-5000, 5000)
        self.sub_adv_bg_off_y.valueChanged.connect(self.on_change)
        self.sub_adv_bg_w = QSpinBox()
        self.sub_adv_bg_w.setRange(10, 10000)
        self.sub_adv_bg_w.valueChanged.connect(self.on_change)
        self.sub_adv_bg_h = QSpinBox()
        self.sub_adv_bg_h.setRange(10, 10000)
        self.sub_adv_bg_h.valueChanged.connect(self.on_change)
        self.sub_adv_bg_rad = QSpinBox()
        self.sub_adv_bg_rad.setRange(0, 200)
        self.sub_adv_bg_rad.valueChanged.connect(self.on_change)
        self.sub_adv_bg_op = SliderSpin(0, 255, 1, 0)
        self.sub_adv_bg_op.valueChanged.connect(self.on_change)
        self.sub_adv_bg_op_spin = self.sub_adv_bg_op.spin
        self.sub_adv_bg_color = QPushButton("背景颜色")
        self.sub_adv_bg_color.clicked.connect(lambda: self.pick_c('adv_bg_color'))
        self.sub_adv_bg_grad_en = ToggleSwitch()
        self.sub_adv_bg_grad_en.setToolTip("启用背景渐变")
        self.sub_adv_bg_grad_en.toggled.connect(self.on_change)
        self.sub_adv_bg_grad_type = QComboBox()
        self.sub_adv_bg_grad_type.addItems(["水平", "垂直", "中心", "菲涅尔"])
        self.sub_adv_bg_grad_type.currentIndexChanged.connect(self.on_change)
        self.sub_adv_bg_grad_colors = QPushButton("选择渐变颜色")
        self.sub_adv_bg_grad_colors.clicked.connect(
            lambda: self._pick_multi_colors(
                'adv_bg_gradient_colors', "高级背景渐变颜色", '#e0c3fc,#8ec5fc'))
        self.sub_adv_bg_grad_hl = QCheckBox("渐变高光")
        self.sub_adv_bg_grad_hl.toggled.connect(self.on_change)

        self.fx_anim_en = ToggleSwitch()
        self.fx_anim_en.setToolTip("启用动画效果")
        from cutora.ui.panels.fx_fields import mount_anim_fx
        mount_anim_fx(self, None)

        from cutora.ui.panels.fx_fields import mount_late_sections
        mount_late_sections(self, None)

        self._pack_edit = None
        self._pack_edit_bar = QWidget()
        self._pack_edit_bar.setObjectName("packEditBar")
        from cutora.ui.theme.tokens import SPACE_1H
        bar = QHBoxLayout(self._pack_edit_bar)
        bar.setContentsMargins(INSPECTOR_PAD_X, SPACE_1H, INSPECTOR_PAD_X, SPACE_1H)
        self._pack_edit_label = QLabel("")
        self._pack_edit_label.setObjectName("packEditLabel")
        self._pack_edit_label.setWordWrap(True)
        bar.addWidget(self._pack_edit_label, 1)
        self._pack_edit_back = QPushButton("返回风格")
        self._pack_edit_save = QPushButton("保存")
        self._pack_edit_save_as = QPushButton("另存为…")
        self._pack_edit_discard = QPushButton("放弃")
        self._pack_edit_back.clicked.connect(self._on_pack_edit_back)
        self._pack_edit_save.clicked.connect(self._on_pack_edit_save)
        self._pack_edit_save_as.clicked.connect(self._on_pack_edit_save_as)
        self._pack_edit_discard.clicked.connect(self._on_pack_edit_discard)
        bar.addWidget(self._pack_edit_back)
        bar.addWidget(self._pack_edit_save)
        bar.addWidget(self._pack_edit_save_as)
        bar.addWidget(self._pack_edit_discard)
        self._pack_edit_bar.hide()
        self.layout.addWidget(self._pack_edit_bar)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.layout.addWidget(scroll)
        build_inspector(self, INSPECTOR_LAYOUT, scroll)

        self._refresh_text_presets()

    def enter_pack_edit(self, pack: dict, snapshot: dict | None) -> None:
        builtin = bool(pack.get("builtin"))
        name = str((pack.get("meta") or {}).get("name") or pack.get("id") or "")
        self._pack_edit = {
            "path": pack.get("path"),
            "id": pack.get("id"),
            "name": name,
            "builtin": builtin,
            "snapshot": snapshot,
        }
        self._pack_edit_label.setText("正在编辑风格包「%s」" % name)
        self._pack_edit_save.setVisible(not builtin)
        self._pack_edit_bar.show()

    def pack_edit_state(self):
        return self._pack_edit

    def _leave_pack_edit(self) -> None:
        self._pack_edit = None
        self._pack_edit_bar.hide()

    def _on_pack_edit_back(self):
        win = self.window()
        gal = getattr(win, "style_gallery", None) if win is not None else None
        tabs = getattr(win, "tabs", None) if win is not None else None
        if tabs is not None and gal is not None:
            idx = tabs.indexOf(gal)
            if idx >= 0:
                tabs.setCurrentIndex(idx)

    def _on_pack_edit_save(self):
        st = self._pack_edit or {}
        if not st or st.get("builtin"):
            return
        win = self.window()
        gallery = getattr(win, "style_gallery", None) if win is not None else None
        if gallery is not None:
            gallery.finish_pack_save()

    def _on_pack_edit_save_as(self):
        win = self.window()
        gallery = getattr(win, "style_gallery", None) if win is not None else None
        if gallery is not None:
            gallery.finish_pack_save_as()

    def _on_pack_edit_discard(self):
        win = self.window()
        gallery = getattr(win, "style_gallery", None) if win is not None else None
        if gallery is not None:
            gallery.finish_pack_discard()

    def _style_bus(self):
        win = self.window()
        bus = getattr(win, "bus", None)
        if bus is None:
            from cutora.core.commands import get_bus
            bus = get_bus()
            if win is not None and getattr(bus, "_window", None) is None:
                bus.bind_window(win)
        return bus

    def _sync_catalog(self):
        win = self.window()
        gal = getattr(win, "style_gallery", None) if win is not None else None
        if gal is not None and hasattr(gal, "reload"):
            gal.reload()
            return
        self._refresh_text_presets()

    def _selected_pack_path(self):
        combo = getattr(self, "sub_preset_combo", None)
        if combo is None:
            return ""
        return str(combo.currentData() or "")

    def _refresh_text_presets(self):
        from cutora.subtitle.stylepack import list_style_entries
        combo = getattr(self, "sub_preset_combo", None)
        if combo is None:
            return
        entries = list_style_entries()
        current = combo.currentData()
        pack_id = ""
        win = self.window()
        tasks = getattr(win, "tasks", None) or [] if win is not None else []
        try:
            idx = int(getattr(win, "curr_idx", -1) or -1)
        except (TypeError, ValueError):
            idx = -1
        if 0 <= idx < len(tasks) and isinstance(tasks[idx], dict):
            pack_id = str(tasks[idx].get("stylepack_id") or "")
        combo.blockSignals(True)
        combo.clear()
        for row in entries:
            name = row["name"]
            display = ("【默认】 %s" % name) if row.get("default") else name
            combo.addItem(display, row["path"])
        pick = combo.findData(current) if current else -1
        if pick < 0 and pack_id:
            for i, row in enumerate(entries):
                if row.get("id") == pack_id:
                    pick = i
                    break
        if pick < 0:
            for i, row in enumerate(entries):
                if row.get("default"):
                    pick = i
                    break
        if pick < 0 and entries:
            pick = 0
        if pick >= 0:
            combo.setCurrentIndex(pick)
        combo.blockSignals(False)

    def _on_text_preset_changed(self):
        if self.block_signals or self.current_data is None:
            return
        path = self._selected_pack_path()
        if not path:
            return
        from cutora.core.commands import CommandError
        from cutora.subtitle.io import canvas_size_for_window
        from cutora.subtitle.stylepack import (
            StylepackError, apply_animation_for, apply_pack_to_task, load_pack,
        )
        win = self.window()
        if win is not None and getattr(win, "sub_panel", None) is self:
            bus = self._style_bus()
            if bus is None:
                return
            try:
                bus.execute("stylepack.apply", {
                    "path": path,
                    "apply_animation": apply_animation_for(win),
                }, source="ui")
            except CommandError as exc:
                QMessageBox.warning(self, "预设", str(exc))
                return
            tasks = getattr(win, "tasks", None) or []
            try:
                idx = int(getattr(win, "curr_idx", -1) or -1)
            except (TypeError, ValueError):
                idx = -1
            if 0 <= idx < len(tasks):
                style = tasks[idx].get("style")
                if isinstance(style, dict) and self.current_data is not style:
                    self.load_data(style)
            return
        try:
            pack = load_pack(path)
        except StylepackError as exc:
            QMessageBox.warning(self, "预设", str(exc))
            return
        cw, ch = canvas_size_for_window(win)
        scratch = {
            "style": copy.deepcopy(self.current_data or {}),
            "dims": (cw, ch),
            "segments": [],
        }
        apply_pack_to_task(
            scratch, pack, apply_animation=apply_animation_for(win, scratch))
        keep_keys = [
            "id", "start", "end", "text", "content", "type", "is_detached",
            "advanced_textbox_enabled", "advanced_textbox_x",
            "advanced_textbox_y", "advanced_textbox_w", "advanced_textbox_h",
            "use_box", "track_index", "preset_name", "x", "y", "w", "h",
            "content_format", "rich_content", "text_layout", "spans",
            "transition_preset", "is_global", "locked", "hidden",
            "segment_idx", "word_idx",
        ]
        saved_state = {k: self.current_data[k] for k in keep_keys if k in self.current_data}
        self.current_data.clear()
        self.current_data.update(copy.deepcopy(scratch.get("style") or {}))
        self.current_data.update(saved_state)
        self.load_data(self.current_data)
        self.param_changed.emit(None)

    def _reselect_pack(self, path):
        if not path:
            return
        combo = self.sub_preset_combo
        combo.blockSignals(True)
        idx = combo.findData(path)
        if idx >= 0:
            combo.setCurrentIndex(idx)
        combo.blockSignals(False)

    def _preset_add(self):
        if not self.current_data:
            return
        from cutora.core.commands import CommandError
        from cutora.subtitle.stylepack import list_style_entries
        rows = list_style_entries()
        user_n = sum(1 for row in rows if not row.get("builtin"))
        default_name = "预设%d" % (user_n + 1)
        name, ok = QInputDialog.getText(
            self, "新增预设", "请输入预设名称:", text=default_name)
        if not ok or not str(name).strip():
            return
        name = str(name).strip()
        if any((not row.get("builtin")) and row["name"] == name for row in rows):
            ret = QMessageBox.question(
                self, "覆盖预设", "预设 '%s' 已存在，是否覆盖？" % name)
            if ret != QMessageBox.StandardButton.Yes:
                return
        bus = self._style_bus()
        try:
            out = bus.execute("stylepack.save_current", {
                "name": name, "tags": "预设",
            }, source="ui")
        except CommandError as exc:
            QMessageBox.warning(self, "失败", str(exc))
            return
        self._sync_catalog()
        self._reselect_pack((out or {}).get("path"))

    def _preset_update(self):
        if not self.current_data:
            return
        path = self._selected_pack_path()
        if not path:
            return
        from cutora.core.commands import CommandError
        from cutora.subtitle.stylepack import StylepackError, load_pack
        try:
            pack = load_pack(path)
        except StylepackError as exc:
            QMessageBox.warning(self, "失败", str(exc))
            return
        if pack.get("builtin"):
            QMessageBox.warning(self, "失败", "内置风格包不能覆盖，请另存为成用户包。")
            return
        bus = self._style_bus()
        try:
            bus.execute("stylepack.update", {"path": path}, source="ui")
        except CommandError as exc:
            QMessageBox.warning(self, "失败", str(exc))
            return
        if (pack.get("meta") or {}).get("default"):
            main_win = self.window()
            if hasattr(main_win, "_reload_default_subtitle_style"):
                main_win._reload_default_subtitle_style()
        self._sync_catalog()
        self._reselect_pack(path)

    def _preset_rename(self):
        path = self._selected_pack_path()
        if not path:
            return
        from cutora.core.commands import CommandError
        from cutora.subtitle.stylepack import StylepackError, list_style_entries, load_pack
        try:
            pack = load_pack(path)
        except StylepackError as exc:
            QMessageBox.warning(self, "失败", str(exc))
            return
        if pack.get("builtin"):
            QMessageBox.warning(self, "失败", "内置风格包不能重命名，请另存为成用户包。")
            return
        old_name = str((pack.get("meta") or {}).get("name") or "")
        new_name, ok = QInputDialog.getText(
            self, "重命名预设", "请输入新名称:", text=old_name)
        if not ok or not str(new_name).strip() or str(new_name).strip() == old_name:
            return
        new_name = str(new_name).strip()
        for row in list_style_entries():
            if row["name"] == new_name and row["path"] != path:
                QMessageBox.warning(self, "失败", "新名称已存在")
                return
        bus = self._style_bus()
        try:
            bus.execute("stylepack.rename", {
                "path": path, "name": new_name,
            }, source="ui")
        except CommandError as exc:
            QMessageBox.warning(self, "失败", str(exc))
            return
        if isinstance(self.current_data, dict):
            self.current_data["preset_name"] = new_name
        self._sync_catalog()
        self._reselect_pack(path)

    def _preset_delete(self):
        path = self._selected_pack_path()
        if not path:
            return
        from cutora.core.commands import CommandError
        from cutora.subtitle.stylepack import (
            BUILTIN_KEEP_TEXT, DELETE_BOTH_SIDES_TEXT, StylepackError, load_pack,
        )
        try:
            pack = load_pack(path)
        except StylepackError as exc:
            QMessageBox.warning(self, "失败", str(exc))
            return
        if pack.get("builtin"):
            QMessageBox.warning(self, "预设", BUILTIN_KEEP_TEXT)
            return
        if QMessageBox.question(
                self, "删除预设", DELETE_BOTH_SIDES_TEXT,
        ) != QMessageBox.StandardButton.Yes:
            return
        name = str((pack.get("meta") or {}).get("name") or "")
        bus = self._style_bus()
        try:
            bus.execute("stylepack.delete", {"path": path}, source="ui")
        except CommandError as exc:
            QMessageBox.warning(self, "失败", str(exc))
            return
        if self.current_data and self.current_data.get("preset_name") == name:
            self.current_data.pop("preset_name", None)
        self._sync_catalog()
        self._on_text_preset_changed()

    def _preset_export(self):
        path = self._selected_pack_path()
        if not path:
            QMessageBox.information(self, "提示", "请先选择一个预设")
            return
        from cutora.core.commands import CommandError
        from cutora.subtitle.stylepack import StylepackError, load_pack
        try:
            pack = load_pack(path)
        except StylepackError as exc:
            QMessageBox.warning(self, "失败", str(exc))
            return
        name = str((pack.get("meta") or {}).get("name") or pack.get("id") or "style")
        dest, _ = QFileDialog.getSaveFileName(
            self, "导出预设", "%s.cutorastyle" % name,
            "CUTORA style (*.cutorastyle)")
        if not dest:
            return
        if not dest.lower().endswith(".cutorastyle"):
            dest += ".cutorastyle"
        bus = self._style_bus()
        try:
            bus.execute("stylepack.export_file", {
                "pack_dir": path, "path": dest,
            }, source="ui")
        except CommandError as exc:
            QMessageBox.warning(self, "失败", str(exc))
            return
        QMessageBox.information(self, "成功", "预设已导出到 %s" % dest)

    def _preset_import(self):
        from cutora.core.commands import CommandError
        src, _ = QFileDialog.getOpenFileName(
            self, "导入预设", "",
            "字幕预设 (*.cutorastyle *.zip *.json);;CUTORA 风格包 (*.cutorastyle *.zip);;"
            "旧版预设 JSON (*.json)")
        if not src:
            return
        if src.lower().endswith(".json"):
            # Q138：老版（AutoSub v6）预设 JSON 读进来转成风格包，能转多少转多少
            from cutora.subtitle.legacy_import import import_legacy_file
            from cutora.subtitle.stylepack import StylepackError
            try:
                rep = import_legacy_file(src)
            except StylepackError as exc:
                QMessageBox.warning(self, "导入预设", str(exc))
                return
            self._sync_catalog()
            lines = ["已转换 %d 个旧版预设为风格包：" % len(rep["imported"])]
            lines += ["  · " + n for n in rep["imported"]]
            for name, why in (rep.get("notes") or {}).items():
                lines.append("「%s」有几处没法一比一还原：" % name)
                lines += ["    - " + w for w in why]
            if rep.get("skipped"):
                lines.append("跳过：" + "；".join(rep["skipped"]))
            lines.append("")
            lines.append("旧版参数和新版画法不完全对应，请到「风格」里检查一遍，微调后重新保存。")
            QMessageBox.information(self, "导入预设", "\n".join(lines))
            return
        bus = self._style_bus()
        try:
            bus.execute("stylepack.import_file", {"path": src}, source="ui")
        except CommandError as exc:
            QMessageBox.warning(self, "失败", str(exc))
            return
        self._sync_catalog()
        QMessageBox.information(self, "完成", "已导入风格包，风格列表和预设下拉都会显示。")

    def _pick_multi_colors(self, key, title, default_colors):
        """用连续的 QColorDialog 拾色器来选取多个颜色。"""
        if not self.current_data:
            return
        current_str = self.current_data.get(key, default_colors)
        existing = [c.strip() for c in current_str.split(',') if c.strip()]
        # 询问用户要设置几个颜色
        count, ok = QInputDialog.getInt(self, title, "请输入颜色数量:", len(existing) if existing else 2, 2, 8)
        if not ok:
            return
        colors = []
        for i in range(count):
            init_c = QColor(existing[i]) if i < len(existing) else QColor('#FFFFFF')
            c = QColorDialog.getColor(init_c, self, f"{title} - 颜色 {i+1}/{count}")
            if not c.isValid():
                return  # 用户取消
            colors.append(c.name())
        self.current_data[key] = ','.join(colors)
        self.param_changed.emit(None)

    def load_data(self, data):
        self.block_signals = True
        self._refresh_text_presets()
        widgets = [
            self.sub_size, self.sub_x, self.sub_y, self.sub_rot,
            self.sub_dyn_op, self.sub_dyn_pad,
            self.sub_dyn_off_y, self.sub_dyn_rad, self.sub_dyn_hollow, self.sub_dyn_stroke, self.sub_dyn_glow, self.sub_adv_bar,
            self.sub_wrap_width, self.sub_wrap_width_spin, self.sub_lines, self.sub_line_spacing, self.sub_box,
            self.sub_safe_margin, self.sub_max_words, self.sub_pause_break,
            self.sub_adv_textbox, self.sub_adv_x, self.sub_adv_y, self.sub_adv_w, self.sub_adv_h, self.sub_adv_align,
            self.sub_adv_line_spacing, self.sub_adv_text_align,
            self.sub_adv_stroke_en, self.sub_adv_stroke_w, self.sub_adv_stroke_op, self.sub_adv_stroke_c,
            self.sub_adv_bg_en, self.sub_adv_bg_off_x, self.sub_adv_bg_off_y,
            self.sub_adv_bg_w, self.sub_adv_bg_h, self.sub_adv_bg_rad,
            self.sub_adv_bg_op, self.sub_adv_bg_color,
            self.sub_adv_bg_grad_en, self.sub_adv_bg_grad_type, self.sub_adv_bg_grad_colors, self.sub_adv_bg_grad_hl,
            self.sub_strk, self.sub_glow_en, self.sub_shadow_en,
            self.sub_extrude_en, self.sub_texture_en,
            self.sub_fonts_en, self.sub_emphasis_en, self.fx_scatter_enabled,
            self.sub_dyn, self.sub_font, self.sub_bold, self.sub_italic,
            self.sub_font_style,
            self.fx_anim_en,
            self.sub_dyn_anim, self.sub_dyn_anim_overshoot, self.sub_dyn_anim_dur,
            self.sub_lyrics_en, self.sub_lyrics_damp, self.sub_lyrics_trans,
            self.sub_lyrics_scale, self.sub_lyrics_feather,
            self.sub_lyrics_gap, self.sub_lyrics_alpha_decay,
        ]
        if self.chk_detached:
            widgets.append(self.chk_detached)
        from cutora.ui.panels.fx_fields import iter_fx_widgets
        widgets.extend(list(iter_fx_widgets(self)))
        widgets = [w for w in widgets if w is not None]
        try:
            for w in widgets:
                w.blockSignals(True)
            self._load_data_body(data)
        finally:
            for w in widgets:
                try:
                    w.blockSignals(False)
                except Exception:
                    pass
            self.block_signals = False

    def _load_data_body(self, data):
        self.current_data = data
        s = force_allowed_font(data)
        from cutora.subtitle.io import canvas_size_for_window
        from cutora.subtitle.stylepack import resolve_style_geometry
        from cutora.ui.i18n import format_ui_number
        cw, ch = canvas_size_for_window(self.window())
        shown = resolve_style_geometry(s, cw, ch)
        self.sub_size.setValue(int(shown.get('fontsize', 60)))
        self.sub_x.setValue(int(shown.get('pos_x', 540)))
        self.sub_y.setValue(int(shown.get('pos_y', 1450)))
        self.sub_rot.setValue(int(s.get('rotation', 0)))

        _fx = s.get("fx") if isinstance(s.get("fx"), dict) else {}
        self.sub_adv_bar.setChecked(bool(_fx.get("follow_sentence")))
        from cutora.subtitle.stylepack import DEFAULT_WRAP_WIDTH_PERCENT
        try:
            wrap_pct = int(s.get("wrap_width_percent", DEFAULT_WRAP_WIDTH_PERCENT) or DEFAULT_WRAP_WIDTH_PERCENT)
        except (TypeError, ValueError):
            wrap_pct = int(DEFAULT_WRAP_WIDTH_PERCENT)
        wrap_pct = max(0, min(100, wrap_pct))
        self.sub_wrap_width.setValue(wrap_pct)
        self.sub_wrap_width_spin.setValue(wrap_pct)
        self._refresh_wrap_width_hint()
        self.sub_lines.setValue(int(s.get('wrap_lines', 2)))
        self.sub_line_spacing.setValue(int(s.get('line_spacing', 0) or 0))
        from cutora.subtitle.reflow import (
            max_words_per_line as _mwpl, pause_break_sec as _pbs, safe_margin_frac as _smf,
        )
        self.sub_safe_margin.setValue(int(round(_smf(s) * 100.0)))
        self.sub_max_words.setValue(int(_mwpl(s)))
        self.sub_pause_break.setValue(float(_pbs(s)))
        self.sub_adv_textbox.setChecked(bool(s.get('advanced_textbox_enabled', False)))
        self.sub_adv_x.setValue(int(s.get('advanced_textbox_x', 0) or 0))
        self.sub_adv_y.setValue(int(s.get('advanced_textbox_y', 0) or 0))
        self.sub_adv_w.setValue(int(s.get('advanced_textbox_w', 0) or 0))
        self.sub_adv_h.setValue(int(s.get('advanced_textbox_h', 0) or 0))
        _adv_align = str(s.get('advanced_textbox_align', 'center') or 'center').lower()
        self.sub_adv_align.setCurrentIndex(0 if _adv_align == 'left' else (2 if _adv_align == 'right' else 1))
        self.sub_adv_line_spacing.setValue(int(s.get('adv_line_spacing', 0) or 0))
        _adv_text_align = str(s.get('adv_text_align', s.get('advanced_textbox_align', 'center')) or 'center').lower()
        self.sub_adv_text_align.setCurrentIndex(0 if _adv_text_align == 'left' else (2 if _adv_text_align == 'right' else 1))
        self.sub_adv_stroke_en.setChecked(bool(s.get('adv_stroke_enabled', False)))
        self.sub_adv_stroke_w.setValue(int(s.get('adv_stroke_width', 2)))
        self.sub_adv_stroke_op.setValue(int(s.get('adv_stroke_opacity', 255)))
        self.sub_adv_bg_en.setChecked(bool(s.get('adv_bg_enabled', False)))
        self.sub_adv_bg_off_x.setValue(int(s.get('adv_bg_offset_x', 0)))
        self.sub_adv_bg_off_y.setValue(int(s.get('adv_bg_offset_y', 0)))
        self.sub_adv_bg_w.setValue(int(s.get('adv_bg_w', 1080)))
        self.sub_adv_bg_h.setValue(int(s.get('adv_bg_h', 280)))
        self.sub_adv_bg_rad.setValue(int(s.get('adv_bg_radius', 20)))
        self.sub_adv_bg_op.setValue(int(s.get('adv_bg_opacity', 191)))
        self.sub_adv_bg_grad_en.setChecked(bool(s.get('adv_bg_gradient_enabled', False)))
        _adv_bg_type = s.get('adv_bg_gradient_type', 'linear_h')
        self.sub_adv_bg_grad_type.setCurrentIndex(0 if _adv_bg_type == 'linear_h' else (1 if _adv_bg_type == 'linear_v' else (2 if _adv_bg_type == 'center' else 3)))
        self.sub_adv_bg_grad_hl.setChecked(bool(s.get('adv_bg_gradient_highlight', False)))
        if hasattr(self, "sub_adv_block_label"):
            self.sub_adv_block_label.setText("字块选区：未选择")
        if hasattr(self, "sub_adv_block_list"):
            self.sub_adv_block_list.clear()
        
        self.sub_box.setChecked(s.get('use_box', False))
        _fx_fonts = s.get("fx") if isinstance(s.get("fx"), dict) else {}
        _fonts = _fx_fonts.get("fonts") if isinstance(_fx_fonts.get("fonts"), dict) else {}
        _body = str(_fonts.get("body") or "").strip() or s.get("font_family")
        self.sub_font.setCurrentText(sanitize_font_family(_body))
        self._rebuild_font_style_combo(
            _body, str(s.get("font_style") or ""))
        self._refresh_font_fallback_hint(
            _body, str(s.get("font_style") or ""))
        self.sub_bold.setChecked(bool(s.get('bold', True)))
        self.sub_italic.setChecked(bool(s.get('italic', False)))
        self._sync_font_style_checks()
        if self.chk_detached:
            self.chk_detached.setChecked(bool(s.get('is_detached', False)))

        # Box transition
        _fxbg = s.get("fx") if isinstance(s.get("fx"), dict) else {}
        _bg = _fxbg.get("bg") if isinstance(_fxbg.get("bg"), dict) else {}
        self.sub_box.setChecked(str(_bg.get("type") or "none") not in ("none", ""))

        # Scrolling lyrics
        self.sub_lyrics_en.setChecked(bool(s.get('lyrics_scroll_enabled', False)))
        self.sub_lyrics_damp.setValue(float(s.get('lyrics_scroll_damping', 0.8)))
        self.sub_lyrics_trans.setValue(float(s.get('lyrics_scroll_transition', 0.4)))
        self.sub_lyrics_scale.setValue(float(s.get('lyrics_center_scale', 1.15)))
        self.sub_lyrics_feather.setValue(int(s.get('lyrics_edge_feather', 40)))
        self.sub_lyrics_gap.setValue(int(s.get('lyrics_line_gap', 20)))
        self.sub_lyrics_alpha_decay.setValue(int(s.get('lyrics_alpha_decay', 30)))
        self.sub_lyrics_center_offset.setValue(int(s.get('lyrics_center_offset', 0)))

        from cutora.ui.panels.fx_fields import load_into_panel
        from cutora.render.textfx.spec import ensure_section_enabled
        self._fx_dirty = isinstance(s.get("fx"), dict)
        load_into_panel(self, s)
        self._load_follow_word(s)
        fx_now = s.get("fx") if isinstance(s.get("fx"), dict) else {}
        ensure_section_enabled(fx_now)
        sh_now = fx_now.get("shadow") if isinstance(fx_now.get("shadow"), dict) else None
        if isinstance(sh_now, dict) and "distance_em" not in sh_now and float(
                sh_now.get("sigma_em") or 0) > 0:
            from cutora.render.textfx.spec import from_style as _fx_from_style
            conv = _fx_from_style(s).shadow
            sh_now["light"] = conv.light
            sh_now["angle_deg"] = conv.angle_deg
            sh_now["distance_em"] = max(float(conv.distance_em), 0.05)
            sh_now["size_em"] = conv.size_em
            sh_now["falloff"] = conv.falloff
            sh_now["strength"] = conv.strength
            sh_now["enabled"] = True
            if conv.color:
                sh_now.setdefault("color", conv.color)
        if isinstance(sh_now, dict) and "enabled" not in sh_now:
            sh_now["enabled"] = float(sh_now.get("distance_em") or 0) > 0
        if getattr(self, "_stroke_toggle", None) is not None:
            self._stroke_toggle.reset()
            self.sub_strk.blockSignals(True)
            self.sub_strk.setChecked(self._stroke_toggle.is_on(fx_now))
            self.sub_strk.blockSignals(False)
        if getattr(self, "_glow_toggle", None) is not None:
            self._glow_toggle.reset()
            self.sub_glow_en.blockSignals(True)
            self.sub_glow_en.setChecked(self._glow_toggle.is_on(fx_now))
            self.sub_glow_en.blockSignals(False)
        if getattr(self, "_shadow_toggle", None) is not None:
            self._shadow_toggle.reset()
            self.sub_shadow_en.blockSignals(True)
            self.sub_shadow_en.setChecked(self._shadow_toggle.is_on(fx_now))
            self.sub_shadow_en.blockSignals(False)
        if getattr(self, "_bg_toggle", None) is not None:
            self._bg_toggle.reset()
        for _name, _sw_name in (
            ("_extrude_toggle", "sub_extrude_en"),
            ("_texture_toggle", "sub_texture_en"),
            ("_fonts_toggle", "sub_fonts_en"),
            ("_scatter_toggle", "fx_scatter_enabled"),
            ("_emphasis_toggle", "sub_emphasis_en"),
        ):
            _tg = getattr(self, _name, None)
            _sw = getattr(self, _sw_name, None)
            if _tg is None or _sw is None:
                continue
            _tg.reset()
            _sw.blockSignals(True)
            _sw.setChecked(_tg.is_on(fx_now))
            _sw.blockSignals(False)
        self._sync_section_content_enabled()

        fx_anim = fx_now.get("anim") if isinstance(fx_now.get("anim"), dict) else {}
        anim_on = any(
            str(fx_anim.get(k) or "none") not in ("none", "")
            for k in ("in", "loop", "out")
        )
        self.fx_anim_en.blockSignals(True)
        self.fx_anim_en.setChecked(anim_on)
        self.fx_anim_en.blockSignals(False)
        self._sync_section_content_enabled()
        self._refresh_safe_zone_hint()

    def _on_bg_enable(self, checked=False):
        self.on_change()

    def _sync_section_content_enabled(self):
        from cutora.ui.widgets.collapsible import CollapsibleSection
        for sec in self.findChildren(CollapsibleSection):
            if not getattr(sec, "_gray_content", False):
                continue
            tg = getattr(sec, "_toggle_cb", None)
            if tg is not None and hasattr(tg, "isChecked"):
                if hasattr(sec, "set_content_enabled"):
                    sec.set_content_enabled(bool(tg.isChecked()))
                else:
                    sec._content.setEnabled(bool(tg.isChecked()))

    def _push_section_toggle(self, tg, fx):
        from cutora.ui.panels.fx_fields import set_widget_value
        from cutora.ui.panels.fx_schema import FX_FIELD_WIDGETS
        if isinstance(tg.off_value, dict):
            cur = tg.get(fx) if isinstance(tg.get(fx), dict) else {}
            for key, val in cur.items():
                path = "%s.%s" % (tg.path, key)
                if path in FX_FIELD_WIDGETS:
                    set_widget_value(self, path, val)
            if tg.path == "fill":
                from cutora.ui.panels.fx_fields import _sync_fill_mode
                _sync_fill_mode(self)
            return
        if tg.path in FX_FIELD_WIDGETS:
            set_widget_value(self, tg.path, tg.get(fx))

    def _sync_bg_type_combo(self, fx):
        from cutora.ui.panels.fx_fields import set_widget_value
        bg = fx.get("bg") if isinstance(fx.get("bg"), dict) else {}
        combo = getattr(self, "fx_bg_type", None)
        if combo is not None:
            set_widget_value(self, "bg.type", bg.get("type") or "none")
        bg_tg = getattr(self, "_bg_toggle", None)
        box = getattr(self, "sub_box", None)
        if bg_tg is not None and box is not None:
            box.blockSignals(True)
            box.setChecked(bg_tg.is_on(fx))
            box.blockSignals(False)

    def _apply_new_section_toggles(self, fx):
        sender = self.sender()
        pairs = (
            ("_extrude_toggle", "sub_extrude_en"),
            ("_fonts_toggle", "sub_fonts_en"),
            ("_scatter_toggle", "fx_scatter_enabled"),
            ("_emphasis_toggle", "sub_emphasis_en"),
        )
        for tg_name, sw_name in pairs:
            tg = getattr(self, tg_name, None)
            sw = getattr(self, sw_name, None)
            if tg is None or sw is None:
                continue
            wrote = tg.apply_checkbox(fx, sw.isChecked())
            if wrote and sw.isChecked():
                self._push_section_toggle(tg, fx)
        tex_tg = getattr(self, "_texture_toggle", None)
        tex_sw = getattr(self, "sub_texture_en", None)
        if tex_tg is None or tex_sw is None:
            return
        combo = getattr(self, "fx_bg_type", None)
        if sender is tex_sw:
            tex_tg.apply_checkbox(fx, tex_sw.isChecked())
            if tex_sw.isChecked():
                bg = fx.get("bg") if isinstance(fx.get("bg"), dict) else {}
                fx["bg"] = bg
                bg["enabled"] = True
            self._sync_bg_type_combo(fx)
        else:
            # Combo / bg toggle / collect: honor the current type, keep the
            # capsule in sync. Never force texture back over a programmatic type.
            tex_sw.blockSignals(True)
            tex_sw.setChecked(tex_tg.is_on(fx))
            tex_sw.blockSignals(False)

    def collect(self):
        """Layout flats + fx. No deprecated / v1 paint keys."""
        self.on_change()
        from cutora.subtitle.style_schema import DEPRECATED_UNPAINTED_KEYS
        data = dict(self.current_data or {})
        for k in list(data):
            if k in DEPRECATED_UNPAINTED_KEYS:
                data.pop(k)
        out = {}
        for key in _COLLECT_FLAT_KEYS:
            if key in data:
                out[key] = data[key]
        fx = data.get("fx") if isinstance(data.get("fx"), dict) else {}
        out["fx"] = dict(fx)
        return out

    def _subtitle_box_px(self):
        from cutora.subtitle.io import canvas_size_for_window
        from cutora.subtitle.stylepack import resolve_style_geometry
        cw, ch = canvas_size_for_window(self.window())
        s = resolve_style_geometry(self.current_data or {}, cw, ch)
        fs = float(s.get("fontsize") or 34)
        pad_y = float(s.get("box_padding_y") or 20)
        pad_x = float(s.get("box_padding_x") or 20)
        cx = float(s.get("pos_x") if s.get("pos_x") is not None else cw / 2.0)
        cy = float(s.get("pos_y") if s.get("pos_y") is not None else ch * 0.75)
        lines = max(1, int(s.get("wrap_lines") or 1))
        bw = max(fs * 4.0, cw * 0.6) + pad_x * 2.0
        bh = fs * 1.4 * lines + pad_y * 2.0
        return (cx - bw / 2.0, cy - bh / 2.0, bw, bh, cw, ch)

    def _refresh_safe_zone_hint(self):
        from cutora.core.history import _load_app_settings
        from cutora.render.safe_zones import (
            active_bands, box_hits, disclaimer, read_state,
        )
        s = _load_app_settings() or {}
        on, _flags = read_state(s)
        self.safe_zone_disclaimer.setText(disclaimer() or "参考值，随平台版本变化")
        self.safe_zone_disclaimer.setVisible(on)
        if not on:
            self.safe_zone_hint.hide()
            self.btn_move_to_safe_zone.hide()
            return
        x, y, w, h, cw, ch = self._subtitle_box_px()
        hits = box_hits((x, y, w, h), active_bands(s, cw, ch))
        if not hits:
            self.safe_zone_hint.hide()
            self.btn_move_to_safe_zone.hide()
            return
        names = "、".join(sorted({b.label or b.preset_name for b in hits}))
        self.safe_zone_hint.setText("字幕与不安全带相交：%s" % names)
        self.safe_zone_hint.show()
        self.btn_move_to_safe_zone.show()

    def _restore_pack_geometry(self):
        from cutora.subtitle.io import canvas_size_for_window
        from cutora.subtitle.stylepack import restore_pack_geometry
        win = self.window()
        tasks = getattr(win, "tasks", None) or []
        idx = int(getattr(win, "curr_idx", -1) or -1)
        if idx < 0 or idx >= len(tasks) or not isinstance(tasks[idx], dict):
            return
        task = tasks[idx]
        cw, ch = canvas_size_for_window(win)
        restore_pack_geometry(task, cw, ch)
        self.block_signals = True
        try:
            self.load_data(task.get("style") or self.current_data)
        finally:
            self.block_signals = False
        self.on_change()

    def _move_to_safe_zone(self):
        from cutora.core.history import _load_app_settings
        from cutora.render.safe_zones import active_bands, nudge_center_y
        from cutora.subtitle.stylepack import set_position_px
        x, y, w, h, cw, ch = self._subtitle_box_px()
        s = self.current_data
        if not isinstance(s, dict):
            return
        ny = nudge_center_y((x, y, w, h), ch, active_bands(_load_app_settings(), cw, ch))
        if ny is None:
            return
        cx = float(s.get("pos_x") if s.get("pos_x") is not None else (x + w / 2.0))
        set_position_px(s, cx, ny, cw, ch)
        self.block_signals = True
        try:
            self.sub_y.setValue(int(round(float(s.get("pos_y") or ny))))
        finally:
            self.block_signals = False
        self.on_change()

    def save_as_default_style(self):
        path = self._selected_pack_path()
        if not path:
            QMessageBox.information(self, "提示", "请先选择一个预设然后再设为默认")
            return
        from cutora.subtitle.stylepack import StylepackError, load_pack, set_pack_default
        try:
            pack = load_pack(path)
            if pack.get("builtin"):
                raise StylepackError("内置风格包不能标为默认，请先另存为")
            set_pack_default(path)
        except StylepackError as exc:
            QMessageBox.warning(self, "错误", str(exc))
            return
        main_win = self.window()
        if hasattr(main_win, "_reload_default_subtitle_style"):
            main_win._reload_default_subtitle_style()
        name = str((pack.get("meta") or {}).get("name") or "")
        QMessageBox.information(self, "成功", "【%s】已被设为默认预设" % name)
        self._sync_catalog()

    def _on_recalc_textbox(self):
        if self.block_signals:
            return
        self.recalc_textbox_request.emit()

    def _rebuild_font_style_combo(self, family, selected: str = "") -> None:
        combo = getattr(self, "sub_font_style", None)
        if combo is None:
            return
        from cutora.ui.i18n import tr
        wanted = str(selected or "").strip()
        names = family_style_names(family)
        combo.blockSignals(True)
        combo.clear()
        combo.addItem(tr("（跟随粗体/斜体）"), "")
        for name in names:
            combo.addItem(name, name)
        idx = combo.findData(wanted)
        if wanted and idx < 0:
            combo.addItem(tr("%s（缺失）") % wanted, wanted)
            idx = combo.findData(wanted)
        combo.setCurrentIndex(idx if idx >= 0 else 0)
        combo.blockSignals(False)
        self._sync_font_style_checks()

    def _sync_font_style_checks(self) -> None:
        from cutora.ui.i18n import tr
        combo = getattr(self, "sub_font_style", None)
        has_style = bool(combo is not None and combo.currentData())
        for box in (getattr(self, "sub_bold", None), getattr(self, "sub_italic", None)):
            if box is None:
                continue
            box.setEnabled(not has_style)
            if has_style:
                box.setToolTip(tr("已选字重，粗体/斜体由字面覆盖，不叠加合成斜体"))
            else:
                box.setToolTip("")

    def _on_font_style_changed(self, *_a) -> None:
        self._sync_font_style_checks()
        self.on_change()

    def _refresh_font_fallback_hint(self, requested, style_name: str = "") -> None:
        hint = getattr(self, "font_fallback_hint", None)
        if hint is None:
            return
        try:
            from shiboken6 import isValid
            if not isValid(hint):
                return
        except Exception:
            pass
        from cutora.ui.i18n import tr
        msgs = []
        rec = resolve_font_request(requested)
        if rec.get("kind") == "fallback":
            msgs.append(tr(
                "原字体「%s」未找到，已改用 %s"
                % (rec.get("requested") or "", rec.get("family") or "")
            ))
        srec = resolve_font_style(requested, style_name)
        if srec.get("kind") == "missing":
            msgs.append(tr(
                "字重「%s」在「%s」中不存在，未改用其他样式"
                % (srec.get("requested") or "", srec.get("family") or "")
            ))
        try:
            from cutora.subtitle.font_coverage import coverage_note_for_text
            sample = ""
            data = getattr(self, "current_data", None) or {}
            if isinstance(data, dict):
                sample = str(data.get("text") or data.get("sample") or "")
            if sample:
                note = coverage_note_for_text(requested, sample)
                if note:
                    msgs.append(tr(note))
        except Exception:
            pass
        if msgs:
            hint.setText("\n".join(msgs))
            hint.show()
        else:
            hint.hide()
            hint.setText("")

    def _current_cue_text(self) -> str:
        win = self.window()
        tasks = getattr(win, "tasks", None) or []
        try:
            idx = int(getattr(win, "curr_idx", -1))
        except (TypeError, ValueError):
            idx = -1
        if isinstance(tasks, list) and 0 <= idx < len(tasks):
            segs = tasks[idx].get("segments") or []
            t = None
            canvas = getattr(win, "video_canvas", None)
            for src in (canvas, win):
                if src is None:
                    continue
                for attr in ("current_time", "playhead", "_t"):
                    raw = getattr(src, attr, None)
                    try:
                        t = float(raw)
                        break
                    except (TypeError, ValueError):
                        continue
                if t is not None:
                    break
            if t is not None:
                for seg in segs:
                    if not isinstance(seg, dict):
                        continue
                    try:
                        a = float(seg.get("start") or 0)
                        b = float(seg.get("end") or 0)
                    except (TypeError, ValueError):
                        continue
                    if a <= t <= b:
                        return str(seg.get("text") or "")
            if segs and isinstance(segs[0], dict):
                return str(segs[0].get("text") or "")
        data = self.current_data if isinstance(self.current_data, dict) else {}
        return str(data.get("text") or data.get("sample") or "")

    def _refresh_wrap_width_hint(self):
        from cutora.core.log import get_logger
        from cutora.ui.i18n import tr
        hint = getattr(self, "sub_wrap_width_hint", None)
        if hint is None:
            return
        try:
            from cutora.subtitle.io import canvas_size_for_window
            from cutora.subtitle.stylepack import estimate_words_per_line
            cw, _ch = canvas_size_for_window(self.window())
            style = dict(self.current_data or {})
            if getattr(self, "sub_wrap_width", None) is not None:
                style["wrap_width_percent"] = int(self.sub_wrap_width.value())
            n = estimate_words_per_line(style, cw, self._current_cue_text())
            hint.setText(tr("每行约 %s 词") % int(n))
        except Exception:
            get_logger("cutora.ui.panels.subtitle_style").exception(
                "wrap width estimate failed")
            hint.setText(tr("每行约 %s 词") % "?")

    def _on_wrap_width_drag(self, _value=None):
        if self.block_signals or not self.current_data:
            return
        self.current_data["wrap_width_percent"] = int(self.sub_wrap_width.value())
        self.current_data.pop("wrap_left", None)
        self.current_data.pop("wrap_right", None)
        self._refresh_wrap_width_hint()

    def _on_wrap_width_commit(self):
        if self.block_signals or not self.current_data:
            return
        pct = int(self.sub_wrap_width.value())
        self.current_data["wrap_width_percent"] = pct
        self.current_data.pop("wrap_left", None)
        self.current_data.pop("wrap_right", None)
        self._refresh_wrap_width_hint()
        self.wrap_width_committed.emit(pct)

    def on_change(self):
        if self.block_signals or not self.current_data: return
        s = self.current_data
        try:
            from cutora.subtitle.stylepack import diag, diag_enabled, style_snapshot
            if diag_enabled():
                diag("panel.on_change", style=style_snapshot(s), style_id=id(s))
        except Exception:
            pass
        old_fontsize = s.get('fontsize')
        old_font_family = s.get('font_family')
        old_wrap_lines = s.get('wrap_lines')
        old_wrap_pct = s.get('wrap_width_percent')
        old_line_spacing = s.get('line_spacing', 0)
        old_pad_x = s.get('box_padding_x')
        old_word_mode = s.get('word_mode')
        old_bold = s.get('bold', True)
        old_italic = s.get('italic', False)
        old_font_style = str(s.get('font_style') or "")
        old_adv_enabled = bool(s.get('advanced_textbox_enabled', False))
        s.pop('text_grad', None)
        s.pop('high_grad', None)
        from cutora.subtitle.style_schema import DEPRECATED_UNPAINTED_KEYS
        for dead in DEPRECATED_UNPAINTED_KEYS:
            s.pop(dead, None)
        from cutora.subtitle.io import canvas_size_for_window
        from cutora.subtitle.stylepack import set_fontsize_px, set_position_px
        cw, ch = canvas_size_for_window(self.window())
        new_fs = self.sub_size.value()
        new_x = self.sub_x.value()
        new_y = self.sub_y.value()
        old_fs = float(s.get("fontsize") or 0)
        old_x = float(s.get("pos_x") or 0)
        old_y = float(s.get("pos_y") or 0)
        if abs(old_fs - new_fs) > 0.51:
            set_fontsize_px(s, new_fs, cw, ch)
        else:
            set_fontsize_px(s, new_fs, cw, ch, user=False)
        if abs(old_x - new_x) > 0.51 or abs(old_y - new_y) > 0.51:
            set_position_px(s, new_x, new_y, cw, ch)
        else:
            set_position_px(s, new_x, new_y, cw, ch, user=False)
        if self.chk_detached:
            s['is_detached'] = self.chk_detached.isChecked()
        s['rotation'] = self.sub_rot.value()

        s['high_padding'] = self.sub_dyn_pad.value()
        s['high_offset_y'] = self.sub_dyn_off_y.value()

        if getattr(self, "sub_wrap_width", None) is not None:
            s['wrap_width_percent'] = int(self.sub_wrap_width.value())
        s.pop('wrap_left', None)
        s.pop('wrap_right', None)
        s['wrap_lines'] = self.sub_lines.value()
        s['line_spacing'] = self.sub_line_spacing.value()
        # Q137：只有动过才写进样式（写了就切到新断句法）；没动过的老工程一个键都不多
        from cutora.subtitle.reflow import PAUSE_BREAK_DEFAULT, SAFE_MARGIN_DEFAULT_PCT
        old_sm, old_mw, old_pb = s.get('safe_margin_pct'), s.get('max_words_per_line'), s.get('pause_break_sec')
        sm = int(self.sub_safe_margin.value())
        mw = int(self.sub_max_words.value())
        pb = round(float(self.sub_pause_break.value()), 2)
        if old_sm is not None or sm != int(SAFE_MARGIN_DEFAULT_PCT):
            s['safe_margin_pct'] = sm
        if old_mw is not None or mw != 0:
            s['max_words_per_line'] = mw
        if old_pb is not None or abs(pb - PAUSE_BREAK_DEFAULT) > 1e-6:
            s['pause_break_sec'] = pb
        if (s.get('safe_margin_pct'), s.get('max_words_per_line'), s.get('pause_break_sec')) != (old_sm, old_mw, old_pb):
            from cutora.subtitle.layout import LAYOUT_ENGINE_SMART
            s['layout_engine'] = LAYOUT_ENGINE_SMART
            self._refresh_wrap_width_hint()
        s['advanced_textbox_enabled'] = self.sub_adv_textbox.isChecked()
        s['advanced_textbox_x'] = self.sub_adv_x.value()
        s['advanced_textbox_y'] = self.sub_adv_y.value()
        s['advanced_textbox_w'] = self.sub_adv_w.value()
        s['advanced_textbox_h'] = self.sub_adv_h.value()
        s['advanced_textbox_align'] = ['left', 'center', 'right'][min(self.sub_adv_align.currentIndex(), 2)]
        s['adv_line_spacing'] = self.sub_adv_line_spacing.value()
        text_idx = self.sub_adv_text_align.currentIndex() if hasattr(self, "sub_adv_text_align") else 1
        s['adv_text_align'] = ['left', 'center', 'right'][min(text_idx, 2)]
        s['adv_stroke_enabled'] = self.sub_adv_stroke_en.isChecked()
        s['adv_stroke_width'] = self.sub_adv_stroke_w.value()
        s['adv_stroke_opacity'] = self.sub_adv_stroke_op.value()
        s['adv_bg_enabled'] = self.sub_adv_bg_en.isChecked()
        s['adv_bg_offset_x'] = self.sub_adv_bg_off_x.value()
        s['adv_bg_offset_y'] = self.sub_adv_bg_off_y.value()
        s['adv_bg_w'] = self.sub_adv_bg_w.value()
        s['adv_bg_h'] = self.sub_adv_bg_h.value()
        s['adv_bg_radius'] = self.sub_adv_bg_rad.value()
        s['adv_bg_opacity'] = self.sub_adv_bg_op.value()
        s['adv_bg_gradient_enabled'] = self.sub_adv_bg_grad_en.isChecked()
        s['adv_bg_gradient_type'] = ['linear_h', 'linear_v', 'center', 'fresnel'][min(self.sub_adv_bg_grad_type.currentIndex(), 3)]
        s['adv_bg_gradient_highlight'] = self.sub_adv_bg_grad_hl.isChecked()
        
        if old_adv_enabled != bool(s.get('advanced_textbox_enabled', False)):
            target_use_box = not bool(s.get('advanced_textbox_enabled', False))
            s['use_box'] = target_use_box
            self.sub_box.blockSignals(True)
            self.sub_box.setChecked(target_use_box)
            self.sub_box.blockSignals(False)
        else:
            s['use_box'] = self.sub_box.isChecked()

        s['font_family'] = sanitize_font_family(self.sub_font.currentText())
        fs = ""
        combo = getattr(self, "sub_font_style", None)
        if combo is not None:
            # Family change must rebuild the real-style list.
            sender = self.sender()
            if sender is self.sub_font:
                keep = str(s.get("font_style") or combo.currentData() or "")
                self._rebuild_font_style_combo(s['font_family'], keep)
            fs = str(combo.currentData() or "").strip()
        if fs:
            s['font_style'] = fs
        else:
            s.pop('font_style', None)
        self._refresh_font_fallback_hint(s.get("font_family"), fs)
        s['bold'] = self.sub_bold.isChecked()
        s['italic'] = self.sub_italic.isChecked()

        # Scrolling lyrics
        s['lyrics_scroll_enabled'] = self.sub_lyrics_en.isChecked()
        s['lyrics_scroll_damping'] = self.sub_lyrics_damp.value()
        s['lyrics_scroll_transition'] = self.sub_lyrics_trans.value()
        s['lyrics_center_scale'] = self.sub_lyrics_scale.value()
        s['lyrics_edge_feather'] = self.sub_lyrics_feather.value()
        s['lyrics_alpha_decay'] = self.sub_lyrics_alpha_decay.value()
        s['lyrics_line_gap'] = self.sub_lyrics_gap.value()
        s['lyrics_center_offset'] = self.sub_lyrics_center_offset.value()

        from cutora.ui.panels.fx_fields import save_from_panel
        if self.sender() in (
            self.sub_strk, self.sub_box, self.sub_glow_en, self.sub_shadow_en,
            self.sub_adv_bar, self.sub_dyn, self.sub_dyn_tint, self.sub_dyn_box,
            getattr(self, "sub_extrude_en", None),
            getattr(self, "sub_texture_en", None),
            getattr(self, "sub_fonts_en", None),
            getattr(self, "sub_emphasis_en", None),
            getattr(self, "fx_scatter_enabled", None),
        ):
            self._fx_dirty = True
        prev_fx = s.get("fx") if isinstance(s.get("fx"), dict) else {}
        if self.sub_adv_bar.isChecked() and not bool(prev_fx.get("follow_sentence")):
            anim_combo = getattr(self, "fx_line_bar_anim", None)
            if anim_combo is not None and (anim_combo.currentData() or "none") == "none":
                idx = anim_combo.findData("draw")
                if idx >= 0:
                    anim_combo.blockSignals(True)
                    anim_combo.setCurrentIndex(idx)
                    anim_combo.blockSignals(False)
        save_from_panel(self, s)
        fx = s.get("fx")
        if isinstance(fx, dict):
            fonts_out = fx.get("fonts")
            if not isinstance(fonts_out, dict):
                fonts_out = {}
                fx["fonts"] = fonts_out
            fonts_out["body"] = s.get("font_family") or fonts_out.get("body") or ""
        if isinstance(fx, dict):
            bg = fx.setdefault("bg", {})
            if not isinstance(bg, dict):
                bg = {}
                fx["bg"] = bg
            sender = self.sender()
            combo = getattr(self, "fx_bg_type", None)
            from cutora.render.textfx.spec import seed_section_defaults
            from cutora.ui.panels.fx_fields import set_widget_value
            stroke_tg = getattr(self, "_stroke_toggle", None)
            if stroke_tg is not None:
                was_on = stroke_tg.is_on(fx)
                stroke_tg.apply_checkbox(fx, self.sub_strk.isChecked())
                if self.sub_strk.isChecked() and not was_on:
                    seed_section_defaults(fx, "stroke")
                    set_widget_value(
                        self, "stroke.width_em",
                        (fx.get("stroke") or {}).get("width_em"))
            glow_tg = getattr(self, "_glow_toggle", None)
            if glow_tg is not None:
                was_on = glow_tg.is_on(fx)
                glow_tg.apply_checkbox(fx, self.sub_glow_en.isChecked())
                if self.sub_glow_en.isChecked() and not was_on:
                    seed_section_defaults(fx, "glow")
                    set_widget_value(
                        self, "glow.sigma_em",
                        (fx.get("glow") or {}).get("sigma_em"))
            shadow_tg = getattr(self, "_shadow_toggle", None)
            if shadow_tg is not None:
                sh = fx.get("shadow") if isinstance(fx.get("shadow"), dict) else None
                if sh is None:
                    sh = {}
                    fx["shadow"] = sh
                prev_sh = prev_fx.get("shadow") if isinstance(
                    prev_fx.get("shadow"), dict) else {}
                if "enabled" in prev_sh and "enabled" not in sh:
                    sh["enabled"] = prev_sh["enabled"]
                was_on = shadow_tg.is_on(prev_fx) or shadow_tg.is_on(fx)
                shadow_tg.apply_checkbox(fx, self.sub_shadow_en.isChecked())
                if self.sub_shadow_en.isChecked() and not was_on:
                    from cutora.render.textfx.spec import (
                        SHADOW_ON_DISTANCE_EM, SHADOW_ON_SIZE_EM,
                        SHADOW_ON_STRENGTH,
                    )
                    if float(sh.get("distance_em") or 0) <= 0:
                        sh["distance_em"] = SHADOW_ON_DISTANCE_EM
                    if float(sh.get("size_em") or 0) <= 0:
                        sh["size_em"] = SHADOW_ON_SIZE_EM
                    if float(sh.get("strength") or 0) <= 0:
                        sh["strength"] = SHADOW_ON_STRENGTH
                    for attr, key in (
                        ("fx_shadow_distance_em", "distance_em"),
                        ("fx_shadow_size_em", "size_em"),
                        ("fx_shadow_strength", "strength"),
                    ):
                        spin = getattr(self, attr, None)
                        if spin is None:
                            continue
                        want = float(sh.get(key) or 0)
                        if abs(float(spin.value()) - want) > 1e-9:
                            spin.blockSignals(True)
                            spin.setValue(want)
                            spin.blockSignals(False)
            bg_tg = getattr(self, "_bg_toggle", None)
            if bg_tg is not None and sender is self.sub_box:
                was_on = bg_tg.is_on(fx)
                bg_tg.apply_checkbox(fx, self.sub_box.isChecked())
                if self.sub_box.isChecked() and not was_on:
                    seed_section_defaults(fx, "bg")
                if combo is not None:
                    kind = (fx.get("bg") or {}).get("type") or "none"
                    idx = combo.findData(kind)
                    if idx >= 0:
                        combo.blockSignals(True)
                        combo.setCurrentIndex(idx)
                        combo.blockSignals(False)
            elif bg_tg is not None:
                self.sub_box.blockSignals(True)
                self.sub_box.setChecked(bg_tg.is_on(fx))
                self.sub_box.blockSignals(False)
            if self.sub_lyrics_en.isChecked():
                if bg_tg is not None:
                    bg_tg.apply_checkbox(fx, False)
                else:
                    bg["enabled"] = False
                self.sub_box.blockSignals(True)
                self.sub_box.setChecked(False)
                self.sub_box.blockSignals(False)
                tex_sw = getattr(self, "sub_texture_en", None)
                if tex_sw is not None:
                    tex_sw.blockSignals(True)
                    tex_sw.setChecked(False)
                    tex_sw.blockSignals(False)
                win = self.window()
                bar = getattr(win, "statusBar", None) if win is not None else None
                if callable(bar):
                    try:
                        bar().showMessage("滚动歌词已开，常规字幕框已关", 4000)
                    except Exception:
                        pass
            self._apply_new_section_toggles(fx)
            self._sync_section_content_enabled()
            self._write_follow_word(fx)
            fx["follow_sentence"] = bool(self.sub_adv_bar.isChecked())
        elif isinstance(s.get("fx"), dict):
            self._write_follow_word(s["fx"])

        for dead in DEPRECATED_UNPAINTED_KEYS:
            s.pop(dead, None)

        if (old_font_family != s.get('font_family')
                or old_bold != s.get('bold', True)
                or old_italic != s.get('italic', False)
                or old_font_style != str(s.get('font_style') or "")):
            get_font_manager().register_font_for_style(
                s.get('font_family'),
                bold=bool(s.get('bold', True)),
                italic=bool(s.get('italic', False))
            )
        if (old_font_family != s.get('font_family')
                or old_bold != s.get('bold', True)
                or old_italic != s.get('italic', False)
                or old_font_style != str(s.get('font_style') or "")):
            self.param_changed.emit('font_family')
        elif old_adv_enabled != bool(s.get('advanced_textbox_enabled', False)):
            self.param_changed.emit('adv_textbox_toggle')
        elif old_wrap_lines != s.get('wrap_lines'):
            self.param_changed.emit('wrap_lines')
        elif old_wrap_pct != s.get('wrap_width_percent'):
            self.param_changed.emit(None)
        elif old_line_spacing != s.get('line_spacing'):
            self.param_changed.emit('line_spacing')
        elif old_fontsize != s.get('fontsize'):
            self.param_changed.emit('fontsize')
        elif old_pad_x != s.get('box_padding_x'):
            self.param_changed.emit('box_padding_x')
        elif old_word_mode != s.get('word_mode'):
            self.param_changed.emit('word_mode')
        elif old_sm != s.get('safe_margin_pct'):
            self.param_changed.emit('safe_margin_pct')
        elif old_mw != s.get('max_words_per_line'):
            self.param_changed.emit('max_words_per_line')
        elif old_pb != s.get('pause_break_sec'):
            self.param_changed.emit('pause_break_sec')
        else:
            self.param_changed.emit(None)
        self._refresh_safe_zone_hint()

    def pick_c(self, key):
        if not self.current_data: return
        init_color = QColor(self.current_data.get(key, '#FFFFFF'))
        c = QColorDialog.getColor(init_color, self)
        if c.isValid():
            self.current_data[key] = c.name()
            self.on_change()

    def _load_follow_word(self, style: dict) -> None:
        from cutora.render.textfx.spec import DEFAULT_EMPH, DEFAULT_FOLLOW_BOX, from_style
        from cutora.ui.panels.fx_fields import _sync_follow_tint_mode

        spec = from_style(style if isinstance(style, dict) else {})
        fw = spec.follow_word
        on = bool(fw)
        self.sub_dyn.blockSignals(True)
        self.sub_dyn.setChecked(on)
        self.sub_dyn.blockSignals(False)
        self.sub_dyn_tint.blockSignals(True)
        self.sub_dyn_tint.setChecked(bool(fw.tint.enabled) if on else True)
        self.sub_dyn_tint.blockSignals(False)
        self.sub_dyn_box.blockSignals(True)
        self.sub_dyn_box.setChecked(bool(fw.box.enabled) if on else True)
        self.sub_dyn_box.blockSignals(False)
        combo = getattr(self, "fx_follow_tint_type", None)
        if combo is not None:
            idx = combo.findData(fw.tint.type or "solid")
            combo.blockSignals(True)
            combo.setCurrentIndex(idx if idx >= 0 else 0)
            combo.blockSignals(False)
        colors_w = getattr(self, "fx_follow_tint_colors", None)
        if colors_w is not None:
            cols = list(fw.tint.colors or [DEFAULT_EMPH])
            colors_w.set_colors(cols, emit=False)
        angle_w = getattr(self, "fx_follow_tint_angle", None)
        if angle_w is not None:
            angle_w.blockSignals(True)
            angle_w.setValue(float(fw.tint.angle))
            angle_w.blockSignals(False)
        _sync_follow_tint_mode(self)
        sw = getattr(self, "fx_follow_box_color", None)
        if sw is not None:
            sw.set_hex(fw.box.color or DEFAULT_FOLLOW_BOX, emit=False)
        box = fw.box
        self.sub_dyn_op.setValue(int(round(float(box.alpha) * 255)))
        self.sub_dyn_pad.setValue(int(round(float(box.pad_em) * 40)))
        self.sub_dyn_off_y.setValue(int(round(float(box.offset_y_em) * 40)))
        self.sub_dyn_rad.setValue(int(round(float(box.radius_em) * 40)))
        self.sub_dyn_hollow.setChecked(
            float(box.stroke_width or 0) > 0 and float(box.alpha) < 0.2)
        self.sub_dyn_stroke.setValue(int(box.stroke_width or 4))
        self.sub_dyn_glow.setValue(int(box.glow_radius or 0))
        self.sub_dyn_anim.setChecked(float(box.overshoot or 0) > 1.0)
        self.sub_dyn_anim_overshoot.setValue(float(box.overshoot or 1.3))
        self.sub_dyn_anim_dur.setValue(float(box.duration or 0.15))

    def _write_follow_word(self, fx: dict) -> None:
        from cutora.render.textfx.spec import DEFAULT_EMPH, DEFAULT_FOLLOW_BOX

        master = bool(self.sub_dyn.isChecked())
        tint_on = bool(master and self.sub_dyn_tint.isChecked())
        box_on = bool(master and self.sub_dyn_box.isChecked())
        combo = getattr(self, "fx_follow_tint_type", None)
        tint_type = (combo.currentData() or "solid") if combo is not None else "solid"
        colors_w = getattr(self, "fx_follow_tint_colors", None)
        colors = list(colors_w.colors()) if colors_w is not None else [DEFAULT_EMPH]
        if not colors:
            colors = [DEFAULT_EMPH]
        angle_w = getattr(self, "fx_follow_tint_angle", None)
        angle = float(angle_w.value()) if angle_w is not None else 90.0
        sw = getattr(self, "fx_follow_box_color", None)
        box_color = (sw.hex() if sw is not None else "") or DEFAULT_FOLLOW_BOX
        overshoot = 0.0
        bounce_dur = 0.0
        if hasattr(self, "sub_dyn_anim") and self.sub_dyn_anim.isChecked():
            overshoot = float(self.sub_dyn_anim_overshoot.value())
            bounce_dur = float(self.sub_dyn_anim_dur.value())
        box = {
            "enabled": box_on,
            "color": box_color,
            "alpha": float(self.sub_dyn_op.value()) / 255.0,
            "pad_em": float(self.sub_dyn_pad.value()) / 40.0,
            "offset_y_em": float(self.sub_dyn_off_y.value()) / 40.0,
            "radius_em": float(self.sub_dyn_rad.value()) / 40.0,
            "stroke_width": (
                float(self.sub_dyn_stroke.value())
                if self.sub_dyn_hollow.isChecked() else 0.0),
            "glow_radius": float(self.sub_dyn_glow.value()),
            "overshoot": overshoot,
            "duration": bounce_dur,
        }
        fx["follow_word"] = {
            "tint": {
                "enabled": tint_on,
                "type": tint_type,
                "colors": colors,
                "angle": angle,
            },
            "box": box,
        }
        fx.pop("follow_word_tint", None)
        fx.pop("follow_word_box", None)
        if box_on:
            fx["box_highlight"] = {k: v for k, v in box.items() if k != "enabled"}
        else:
            fx.pop("box_highlight", None)

    def pick_box_highlight_color(self):
        if not self.current_data:
            return
        sw = getattr(self, "fx_follow_box_color", None)
        init = QColor("#0B1A33")
        if sw is not None and sw.hex():
            init = QColor(sw.hex())
        c = QColorDialog.getColor(init, self)
        if not c.isValid():
            return
        if sw is not None:
            sw.set_hex(c.name(), emit=False)
        fx = self.current_data.setdefault("fx", {})
        if not isinstance(fx, dict):
            fx = {}
            self.current_data["fx"] = fx
        bh = fx.setdefault("box_highlight", {})
        if not isinstance(bh, dict):
            bh = {}
            fx["box_highlight"] = bh
        bh["color"] = c.name()
        fw = fx.get("follow_word")
        if not isinstance(fw, dict):
            fw = {}
            fx["follow_word"] = fw
        box = fw.get("box")
        if not isinstance(box, dict):
            box = {}
            fw["box"] = box
        box["color"] = c.name()
        self._fx_dirty = True
        self.on_change()
