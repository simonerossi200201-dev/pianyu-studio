# SPDX-License-Identifier: GPL-3.0-or-later
# Extracted from the T01 monolith by tools/split_monolith.py.
# Original lines: 6605-6718, 6725-8582

import copy
import math
import time
from cutora.core.log import get_logger as _get_logger
_LOG = _get_logger("cutora.ui.panels.property")
from PySide6.QtWidgets import (
    QHBoxLayout,
    QVBoxLayout,
    QGridLayout,
    QPushButton,
    QTextEdit,
    QMessageBox,
    QGroupBox,
    QFormLayout,
    QSpinBox,
    QColorDialog,
    QSlider,
    QCheckBox,
    QScrollArea,
    QInputDialog,
    QToolButton,
    QButtonGroup,
    QComboBox,
    QStackedWidget,
    QDoubleSpinBox,
    QMenu,
    QFileDialog,
    QLabel,
    QLineEdit,
    QWidget,
    QFrame,
    QPlainTextEdit,
    QSizePolicy,
    QAbstractSpinBox,
)
from PySide6.QtCore import (
    Qt,
    Signal,
    QTimer,
    QSize,
)
from PySide6.QtGui import (
    QColor,
    QFont,
    QTextCursor,
    QTextCharFormat,
)
from cutora.render import anim_engine as anim_engine
from cutora.subtitle import text_preset as text_preset_store
from cutora.ui.panels.fonts import (
    DEFAULT_FONT_FAMILY,
    _get_default_font_family,
    register_font_combo,
    sanitize_font_family,
)
from cutora.ui.panels.subtitle_style import apply_text_template_to_overlay
from cutora.subtitle.text_utils import (
    _RT_PROP_STROKE_COLOR,
    _RT_PROP_SHADOW_COLOR,
    ENABLE_RICH_TEXT,
    _rich_plain_text,
    _rich_default_layout,
    _normalize_text_align,
    _rich_apply_effects_to_spans,
)
from PySide6.QtWidgets import (
    QMenu,
    QMessageBox,
)
from PySide6.QtCore import Qt
from cutora.ui.panels.text_editors import (
    _editor_set_spans,
    _editor_to_spans,
)
from cutora.ui.widgets.collapsible import ToggleSwitch, CollapsibleSection
from cutora.subtitle.text_utils import ENABLE_RICH_TEXT, _RT_PROP_SHADOW_COLOR, _RT_PROP_STROKE_COLOR, _normalize_text_align, _rich_apply_effects_to_spans, _rich_default_layout, _rich_plain_text
from cutora.ui.panels.fonts import DEFAULT_FONT_FAMILY, _get_default_font_family, register_font_combo, sanitize_font_family
from cutora.ui.panels.subtitle_style import apply_text_template_to_overlay
from cutora.ui.panels.text_editors import _editor_set_spans, _editor_to_spans
from cutora.ui.panels.text_rich import RichTextEditor
from cutora.ui.widgets.collapsible import CollapsibleSection, ToggleSwitch
from cutora.ui.panels.fx_fields import (
    iter_fx_widgets,
    load_into_panel,
    mount_anim_fx,
    mount_emphasis_section,
    mount_extrude_section,
    mount_fill_section,
    mount_fonts_emphasis,
    mount_glow_fx,
    mount_layout_fx,
    mount_overlay_bg,
    mount_scatter_section,
    mount_shadow_fx,
    mount_stroke_fx,
    save_from_panel,
)
from cutora.ui.panels.section_toggle import SectionToggle
from cutora.ui.widgets.inspector import (
    EnumCombo,
    InspectForm,
    KeyframeWatch,
    BLEND_ITEMS,
    LUMA_MATTE_TIP,
    VIDEO_BLEND_ITEMS,
    SegmentedChoice,
    SliderSpin,
    ValueCombo,
    with_trailing,
    add_inspect_row,
    bind_icon_safe,
    inspector_icon_button,
    install_inspector_limits,
    inspector_row_of,
    paired_fields,
    slider_with_spin,
)
from cutora.ui.theme.tokens import (
    QWIDGETSIZE_MAX,
    INSPECTOR_OPTION_GAP,
    INSPECTOR_COLOR_MAX_W,
    INSPECTOR_FIELD_GAP,
    INSPECTOR_PAD_X,
    INSPECTOR_PAD_Y,
    INSPECTOR_ROW_GAP,
    INSPECTOR_SECTION_GAP,
    SQUARE_SM,
)


class ColorOptionPanel(QWidget):
    changed = Signal()
    def __init__(self, title, default_c1, default_c2, default_c3):
        super().__init__()
        self.default_c1 = default_c1
        self.default_c2 = default_c2
        self.default_c3 = default_c3
        
        self.layout = QHBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.setSpacing(4)
        
        from PySide6.QtWidgets import (
            QPushButton,
            QCheckBox,
            QComboBox,
        )
        self.btn_main = QPushButton(title)
        self.btn_main.setObjectName("swatchBtn")
        self.btn_main.setFixedWidth(60)
        self.layout.addWidget(self.btn_main)
        
        from cutora.ui.widgets.collapsible import labeled_toggle
        self.chk_grad_host, self.chk_grad = labeled_toggle("启用渐变")
        self.layout.addWidget(self.chk_grad_host)
        
        self.cmb_mode = QComboBox()
        self.cmb_mode.addItems(['双色渐变', '三色渐变'])
        self.layout.addWidget(self.cmb_mode)
        
        self.cmb_dir = QComboBox()
        self.cmb_dir.addItems(['垂直(上下)', '水平(左右)'])
        self.layout.addWidget(self.cmb_dir)
        
        self.c1 = default_c1
        self.c2 = default_c2
        self.c3 = default_c3
        
        self.btn_c1 = QPushButton()
        self.btn_c2 = QPushButton()
        self.btn_c3 = QPushButton()
        
        for b in (self.btn_c1, self.btn_c2, self.btn_c3):
            b.setObjectName("swatchBtn")
            b.setFixedSize(20, 20)
            self.layout.addWidget(b)
        
        self.layout.addStretch()
        
        self.cmb_mode.setVisible(False)
        self.cmb_dir.setVisible(False)
        self.btn_c1.setVisible(False)
        self.btn_c2.setVisible(False)
        self.btn_c3.setVisible(False)
        
        self.chk_grad.toggled.connect(self._on_grad_toggled)
        self.cmb_mode.currentIndexChanged.connect(self._on_mode_changed)
        
        self.btn_main.clicked.connect(self.pick_main)
        self.btn_c1.clicked.connect(lambda: self._pick_color(1))
        self.btn_c2.clicked.connect(lambda: self._pick_color(2))
        self.btn_c3.clicked.connect(lambda: self._pick_color(3))
        
        self.chk_grad.toggled.connect(lambda *_a: self.changed.emit())
        self.cmb_mode.currentIndexChanged.connect(lambda *_a: self.changed.emit())
        self.cmb_dir.currentIndexChanged.connect(lambda *_a: self.changed.emit())
        
        self._update_all_colors()

    def _on_grad_toggled(self, checked):
        self.cmb_mode.setVisible(checked)
        self.cmb_dir.setVisible(checked)
        self.btn_c1.setVisible(checked)
        self.btn_c2.setVisible(checked)
        self._on_mode_changed()
        
    def _on_mode_changed(self):
        self.btn_c3.setVisible(self.chk_grad.isChecked() and self.cmb_mode.currentIndex() == 1)
    def _update_btn(self, btn, c):
        from cutora.ui.theme.apply import apply_swatch
        apply_swatch(btn, c)
        
    def _update_all_colors(self):
        self._update_btn(self.btn_main, self.c1)
        self._update_btn(self.btn_c1, self.c1)
        self._update_btn(self.btn_c2, self.c2)
        self._update_btn(self.btn_c3, self.c3)
        
    def pick_main(self):
        self._pick_color(1)
        
    def _pick_color(self, idx):
        cur = getattr(self, f"c{idx}")
        # Standard import to prevent cyclic or missing references
        from PySide6.QtWidgets import QColorDialog
        from PySide6.QtGui import QColor
        dialog = QColorDialog(QColor(cur), self)
        if dialog.exec():
            c = dialog.selectedColor().name()
            setattr(self, f"c{idx}", c)
            self._update_all_colors()
            self.changed.emit()
            
    def get_data(self):
        return {
            "enabled": self.chk_grad.isChecked(),
            "mode": self.cmb_mode.currentIndex(),
            "dir": self.cmb_dir.currentIndex(),
            "c1": self.c1,
            "c2": self.c2,
            "c3": self.c3
        }
        
    def set_data(self, data):
        if not data: return
        self.chk_grad.setChecked(data.get("enabled", False))
        self.cmb_mode.setCurrentIndex(data.get("mode", 0))
        self.cmb_dir.setCurrentIndex(data.get("dir", 0))
        self.c1 = data.get("c1", self.default_c1)
        self.c2 = data.get("c2", self.default_c2)
        self.c3 = data.get("c3", self.default_c3)
        self._update_all_colors()

_DIR_FAMILIES = {
    "left": ("left", "right", "up", "down"),
    "right": ("left", "right", "up", "down"),
    "up": ("left", "right", "up", "down"),
    "down": ("left", "right", "up", "down"),
    "cw": ("cw", "ccw"),
    "ccw": ("cw", "ccw"),
    "horizontal": ("horizontal", "vertical"),
    "vertical": ("horizontal", "vertical"),
}


def transition_param_info(preset: str) -> dict:
    """Which inspector parameters a transition preset really reads.

    New-style presets are introspected from their render function's keyword
    arguments (so a new transition gets the right rows for free); legacy
    anim_engine presets keep intensity + easing, and zoom presets also get
    start scale / motion blur."""
    import inspect

    from cutora.render import transitions as _tx

    info = {"direction": None, "angle": False, "zoom_start": False,
            "motion_blur": False, "intensity": True, "feather": False}
    fn = _tx.TRANSITION_FUNCS.get(preset)
    if fn is None:
        base = preset.rsplit("_", 1)[0] if preset.endswith(("_in", "_out", "_both")) else preset
        if base.startswith("zoom"):
            info["zoom_start"] = True
            info["motion_blur"] = True
        return info
    bound_dir = False
    for cell in (getattr(fn, "__closure__", None) or ()):
        val = cell.cell_contents
        if callable(val):
            fn = val
        elif isinstance(val, str):
            bound_dir = True
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return info
    info["intensity"] = "intensity" in params
    info["motion_blur"] = "motion_blur" in params
    info["feather"] = "feather" in params
    # wipe_angle stores its degrees in `direction`. Other wipes take a real `angle` kw.
    if preset == "wipe_angle" or "angle" in params:
        info["angle"] = True
    if preset != "wipe_angle" and "direction" in params and not bound_dir:
        default = params["direction"].default
        if isinstance(default, str):
            info["direction"] = _DIR_FAMILIES.get(str(default), _DIR_FAMILIES["left"])
    return info


class PropertyPanel(QWidget):

    param_changed = Signal(object)
    # Q126：load_data 跑完发一次 (type_, data)，画布上方的文本工具条靠它决定
    # 显示 / 隐藏并同步值 —— 工具条只是这个面板控件的代理，没有第二份数据
    data_loaded = Signal(object, object)
    element_raise = Signal()
    element_lower = Signal()
    sync_intro_style = Signal()
    element_move_up = Signal()
    rich_text_edit_request = Signal(str, object)
    
    def __init__(self):
        super().__init__()
        self.setObjectName("PropertyPanel")
        self.layout = QVBoxLayout(self)
        self.stack = QStackedWidget()
        self.layout.addWidget(self.stack)
        self._active_text_editor = None
        self.text_element_widgets = {}
        self.shape_element_widgets = {}
        self._editing_preset_name = False
        self._video_clip = None
        self._video_track = None
        self._ov_content_timer = QTimer(self)
        self._ov_content_timer.setSingleShot(True)
        self._ov_content_timer.setInterval(300)
        self._ov_content_timer.timeout.connect(self._flush_ov_content_change)
        # Q131：建控件时 setRange / setValue 会触发 valueChanged → on_change_ov，
        # 那时 block_signals 还没定义（Q129 的「缩放」行 range 从 0.1 起，第一次
        # 就踩到了：AttributeError 'block_signals'）。先挡住，建完再放开。
        self.block_signals = True
        self.current_data = None
        self.current_type = None

        self.init_empty_panel()      # stack index 0
        self.init_overlay_panel()    # stack index 1
        self.init_audio_panel()      # stack index 2
        self.init_video_panel()      # stack index 3
        self.init_transition_panel() # stack index 4
        self.init_track_panel()      # stack index 5
        
        self.current_data = None
        self.current_type = None
        self.audio_targets = []
        self.block_signals = False
        self._ov_parent_track = None   # 当前 overlay 所属 Track（用于混合模式）
        self._ov_parent_overlay = None # 当前 sticker 的父 overlay（用于缩放/羽化）
        self._track_obj = None
        self._reset_undo_open = False
        self._reset_undo_owned = False
        self._section_reset_depth = 0
        self._install_section_resets()
        self._attach_extra_kf_watches()

    def add_slider_spin_row(self, form, label, min_v, max_v, reset_key=None):
        ss = SliderSpin(min_v, max_v, 1, 0)
        ss.valueChanged.connect(self.on_change_ov)
        add_inspect_row(form, label, ss, reset_key=reset_key)
        return ss.spin, ss.slider

    def add_slider_double_row(self, form, label, min_v, max_v, step=0.1, reset_key=None):
        ss = SliderSpin(min_v, max_v, step, 2)
        ss.valueChanged.connect(self.on_change_ov)
        add_inspect_row(form, label, ss, reset_key=reset_key)
        return ss.spin, ss.slider

    def add_slider_spin_row_video(self, form, label, min_v, max_v, reset_key=None):
        ss = SliderSpin(min_v, max_v, 1, 0)
        ss.valueChanged.connect(self.on_change_video)
        add_inspect_row(form, label, ss, reset_key=reset_key)
        return ss.spin, ss.slider

    def _add_fade_controls(self, form, prefix: str, on_change):
        """Four 0–100% sliders + per-edge center offset + 四边同步. prefix is ov or vid."""
        from cutora.render.textfx.fade import FADE_CAP, FADE_HINT, OFFSET_CAP
        from cutora.ui.i18n import tr
        hi = int(round(FADE_CAP * 100.0))
        ohi = int(round(OFFSET_CAP * 100.0))
        pairs = []
        sync = QCheckBox(tr("四边一起调"))
        sync.setToolTip(tr("勾选后出现总控，一次改四边；拖某一边也会带动其余三边"))
        sync.toggled.connect(on_change)
        form.addRow(tr("同步"), sync, reset_key="%s.fade_sync" % prefix)
        setattr(self, "%s_fade_sync" % prefix, sync)
        master = SliderSpin(0, hi, 1, 0)
        master.setSuffix("%")
        master.setToolTip(tr("四边一起调时，拖这里一次改上、下、左、右"))
        master.valueChanged.connect(on_change)
        master.slider.sliderReleased.connect(self._commit_fade_command)
        master.spin.editingFinished.connect(self._commit_fade_command)
        add_inspect_row(form, tr("四边"), master, reset_key="%s.fade_master" % prefix)
        setattr(self, "%s_fade_master" % prefix, master)
        row = inspector_row_of(master)
        if row is not None:
            row.setVisible(False)
        else:
            master.setVisible(False)
        for key, label in (
            ("fade_top", "上渐隐"),
            ("fade_bottom", "下渐隐"),
            ("fade_left", "左渐隐"),
            ("fade_right", "右渐隐"),
        ):
            ss = SliderSpin(0, hi, 1, 0)
            ss.setSuffix("%")
            ss.setToolTip(tr("%s（0–%d%%，元素包围盒比例。%s）" % (
                label, hi, FADE_HINT)))
            ss.valueChanged.connect(on_change)
            ss.slider.sliderReleased.connect(self._commit_fade_command)
            ss.spin.editingFinished.connect(self._commit_fade_command)
            add_inspect_row(form, tr(label), ss, reset_key="%s.%s" % (prefix, key))
            setattr(self, "%s_%s" % (prefix, key), ss.spin)
            setattr(self, "%s_%s_sl" % (prefix, key), ss.slider)
            setattr(self, "%s_%s_ss" % (prefix, key), ss)
            pairs.append((ss.spin, ss.slider))
            off_key = key + "_offset"
            off_label = label.replace("渐隐", "偏移")
            off = SliderSpin(-ohi, ohi, 1, 0)
            off.setSuffix("%")
            off.setToolTip(tr(
                "%s（−%d%%…+%d%%，把该方向渐变带的中心沿法线平移）"
                % (off_label, ohi, ohi)))
            off.valueChanged.connect(on_change)
            off.slider.sliderReleased.connect(self._commit_fade_command)
            off.spin.editingFinished.connect(self._commit_fade_command)
            add_inspect_row(
                form, tr(off_label), off, reset_key="%s.%s" % (prefix, off_key))
            setattr(self, "%s_%s" % (prefix, off_key), off.spin)
            setattr(self, "%s_%s_sl" % (prefix, off_key), off.slider)
        hint = QLabel(tr(FADE_HINT))
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        form.addRow("", hint)
        setattr(self, "%s_fade_hint" % prefix, hint)
        return pairs

    def _fade_edge_keys(self):
        return ("fade_top", "fade_bottom", "fade_left", "fade_right")

    def _fade_link_seed(self, prefix: str) -> int:
        vals = []
        for key in self._fade_edge_keys():
            spin = getattr(self, "%s_%s" % (prefix, key), None)
            vals.append(0 if spin is None else int(spin.value()))
        for val in vals:
            if val:
                return val
        return vals[0] if vals else 0

    def _fade_sender_value(self, prefix: str, sender):
        if sender is None:
            return None
        master = getattr(self, "%s_fade_master" % prefix, None)
        if master is not None and sender in (
            master, getattr(master, "spin", None), getattr(master, "slider", None),
        ):
            return int(master.value())
        for key in self._fade_edge_keys():
            sp = getattr(self, "%s_%s" % (prefix, key), None)
            sl = getattr(self, "%s_%s_sl" % (prefix, key), None)
            ss = getattr(self, "%s_%s_ss" % (prefix, key), None)
            if sender is sp or sender is sl or sender is ss:
                return int(sp.value()) if sp is not None else None
        return None

    def _set_fade_master_visible(self, prefix: str, on: bool) -> None:
        master = getattr(self, "%s_fade_master" % prefix, None)
        if master is None:
            return
        row = inspector_row_of(master)
        if row is not None:
            row.setVisible(bool(on))
        else:
            master.setVisible(bool(on))

    def _write_linked_fades(self, prefix: str, val: int) -> None:
        widgets = []
        for key in self._fade_edge_keys():
            ss = getattr(self, "%s_%s_ss" % (prefix, key), None)
            if ss is not None:
                widgets.append(ss)
        master = getattr(self, "%s_fade_master" % prefix, None)
        if master is not None:
            widgets.append(master)
        blocked = [(w, w.blockSignals(True)) for w in widgets]
        prev = self.block_signals
        self.block_signals = True
        try:
            for key in self._fade_edge_keys():
                spin = getattr(self, "%s_%s" % (prefix, key), None)
                if spin is not None:
                    spin.setValue(int(val))
            if master is not None:
                master.setValue(int(val))
        finally:
            self.block_signals = prev
            for w, was in blocked:
                w.blockSignals(was)

    def _sync_fade_sliders(self, prefix: str) -> None:
        sync = getattr(self, "%s_fade_sync" % prefix, None)
        if sync is None or self.block_signals:
            return
        sender = self.sender()
        checked = bool(sync.isChecked())
        if sender is sync:
            self._set_fade_master_visible(prefix, checked)
            if checked:
                self._write_linked_fades(prefix, self._fade_link_seed(prefix))
            return
        self._set_fade_master_visible(prefix, checked)
        if not checked:
            return
        val = self._fade_sender_value(prefix, sender)
        if val is None:
            return
        self._write_linked_fades(prefix, int(val))

    def _set_combo_by_data(self, combo, value, fallback=None):
        idx = combo.findData(value)
        if idx < 0 and fallback is not None:
            idx = combo.findData(fallback)
        if idx < 0:
            idx = 0
        combo.setCurrentIndex(idx)

    def init_empty_panel(self):
        w = QWidget(); l = QVBoxLayout(w)
        l.addWidget(QLabel("请选择一个层级对象（贴图/文本）"))
        self.stack.addWidget(w)

    def init_overlay_panel(self):
        w = QWidget()
        container = QWidget()
        container.setObjectName("propPanelContainer")
        container_layout = QVBoxLayout(container)
        container_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        container_layout.setSpacing(INSPECTOR_SECTION_GAP)
        container_layout.setContentsMargins(0, INSPECTOR_PAD_Y, 0, INSPECTOR_PAD_Y)

        # ═══ 辅助函数 ═══
        def _mkform():
            f = InspectForm()
            f.set_reset_resolver(self._resolve_reset_key)
            f.setSpacing(INSPECTOR_ROW_GAP)
            f.setContentsMargins(
                INSPECTOR_PAD_X, INSPECTOR_ROW_GAP, INSPECTOR_PAD_X, INSPECTOR_ROW_GAP)
            return f
        def _lbl(t):
            lb = QLabel(t); lb.setObjectName("formHint"); return lb
        def _cswatch(txt, fn, w_=None):
            b = QPushButton(txt); b.setObjectName("swatchBtn")
            b.clicked.connect(fn)
            b.setMaximumWidth(INSPECTOR_COLOR_MAX_W)
            from cutora.ui.widgets.responsive import mark_explicit
            mark_explicit(b, "maximumWidth")
            if w_:
                b.setFixedWidth(w_)
            return b
        def _spin_sl(lo, hi, fn):
            sp = QSpinBox(); sp.setRange(lo, hi); sp.valueChanged.connect(fn)
            sl = QSlider(Qt.Orientation.Horizontal); sl.setRange(lo, hi)
            sl.valueChanged.connect(sp.setValue); sp.valueChanged.connect(sl.setValue); sl.valueChanged.connect(fn)
            return sp, sl

        # ═══ Section 1: 变换 ═══
        self.image_basic_group = CollapsibleSection("变换")
        bf = _mkform()
        def _mk_geom(lo, hi, fn):
            sl = QSlider(Qt.Orientation.Horizontal)
            sl.setRange(lo, hi)
            sl.setMinimumWidth(24)
            sl.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            sp = QSpinBox()
            sp.setObjectName("geomSpin")
            sp.setRange(lo, hi)
            sp.setMinimumWidth(48)
            sp.setFixedWidth(48)
            sp.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
            sl.valueChanged.connect(sp.setValue)
            sp.valueChanged.connect(sl.setValue)
            sl.valueChanged.connect(fn)
            sp.valueChanged.connect(fn)
            return sp, sl
        # Transform: paired compact fields (position / size) like pro
        # inspectors; X/Y/W/H drag sliders are kept as hidden value mirrors
        # (their -10000..10000 range made them useless to drag).
        geom = QWidget(container)  # parented: keeps the mirror sliders alive
        geom.setObjectName("geomHost")
        geom.hide()
        self._geom_host = geom
        self.ov_x, self.ov_x_sl = _mk_geom(-10000, 10000, self.on_change_ov)
        self.ov_y, self.ov_y_sl = _mk_geom(-10000, 10000, self.on_change_ov)
        self.ov_w, self.ov_w_sl = _mk_geom(10, 10000, self.on_change_ov)
        self.ov_h, self.ov_h_sl = _mk_geom(10, 10000, self.on_change_ov)
        self.ov_rot, self.ov_rot_sl = _mk_geom(-360, 360, self.on_change_ov)
        self.ov_op, self.ov_op_sl = _mk_geom(0, 255, self.on_change_ov)
        for sl in (self.ov_x_sl, self.ov_y_sl, self.ov_w_sl, self.ov_h_sl):
            sl.setParent(geom)
            sl.hide()
        for sp, prefix in ((self.ov_x, "X"), (self.ov_y, "Y"),
                           (self.ov_w, "W"), (self.ov_h, "H")):
            sp.setPrefix(prefix + "  ")
            sp.setMinimumWidth(0)
            sp.setMaximumWidth(QWIDGETSIZE_MAX)
            sp.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.ov_rot.setSuffix("°")
        self.ov_x_kf = self._make_kf_watch("x", kind="overlay")
        self.ov_y_kf = self._make_kf_watch("y", kind="overlay")
        bf.addRow("位置", paired_fields(
            with_trailing(self.ov_x, self.ov_x_kf),
            with_trailing(self.ov_y, self.ov_y_kf)), reset_key="ov.pos")
        self.ov_lock_btn = inspector_icon_button(
            "link", "锁定宽高比", checkable=True, object_name="aspectLockBtn")
        self.ov_lock_btn.setChecked(True)
        self.ov_lock_btn.toggled.connect(
            lambda on: bind_icon_safe(
                self.ov_lock_btn, "link" if on else "unlink",
                "accent" if on else "fg2"))
        bind_icon_safe(self.ov_lock_btn, "link", "accent")
        self.ov_restore_btn = inspector_icon_button(
            "rotate-ccw", "还原宽高比", object_name="restoreAspectBtn")
        self.ov_restore_btn.clicked.connect(self._restore_ov_aspect)
        aspect_trail = QWidget()
        aspect_trail.setObjectName("aspectTrail")
        _at = QHBoxLayout(aspect_trail)
        _at.setContentsMargins(0, 0, 0, 0)
        _at.setSpacing(INSPECTOR_FIELD_GAP)
        _at.addWidget(self.ov_lock_btn)
        _at.addWidget(self.ov_restore_btn)
        bf.addRow("尺寸", paired_fields(self.ov_w, self.ov_h, trailing=aspect_trail), reset_key="ov.size")
        bf.addRow("旋转", slider_with_spin(self.ov_rot_sl, self.ov_rot), reset_key="ov.rotation")
        self.ov_rot_kf = self._attach_row_watch(self.ov_rot, "rotation", kind="overlay")
        # Q129：文本层的整体缩放 —— 像图片一样绕框中心放大缩小，排版（折行 / 字号）不动。
        # 图片层有自己的「图片属性 › 缩放」，这一行只给文本显示。
        self.ov_tscale, self.ov_tscale_sl = self.add_slider_double_row(
            bf, "缩放", 0.1, 5.0, step=0.05, reset_key="ov.tscale")
        self.ov_tscale.setToolTip("整体缩放（不影响折行和字号数值；1.00 = 原大）")
        self.ov_tscale_kf = self._attach_row_watch(self.ov_tscale, "scale", kind="overlay")
        bf.addRow("不透明度", slider_with_spin(self.ov_op_sl, self.ov_op), reset_key="ov.opacity")
        self.ov_op_kf = self._attach_row_watch(self.ov_op, "opacity", kind="overlay")
        self.ov_transition = QComboBox()
        self.ov_transition.setMinimumWidth(0)
        self.ov_transition.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        for label, val in anim_engine.TRANSITION_PRESETS: self.ov_transition.addItem(label, val)
        self.ov_transition.currentIndexChanged.connect(self.on_change_ov)
        self.ov_transition_dur = QDoubleSpinBox(); self.ov_transition_dur.setRange(0.0, 5.0); self.ov_transition_dur.setSingleStep(0.05); self.ov_transition_dur.setDecimals(2)
        self.ov_transition_dur.setSuffix(" s")
        self.ov_transition_dur.setFixedWidth(72)
        self.ov_transition_dur.valueChanged.connect(self.on_change_ov)
        self.ov_transition_dur_label = QLabel("(s)", self)
        self.ov_transition_dur_label.hide()
        self.ov_transition_label = QLabel("转场:", self)
        bf.addRow("转场", self.ov_transition, reset_key="ov.transition")
        bf.addRow("转场时长", self.ov_transition_dur, reset_key="ov.transition_dur")
        self.ov_sticker_anim_in = QComboBox()
        self.ov_sticker_anim_out = QComboBox()
        from cutora.render.stickers import sticker_anim_items
        for _lab, _val in sticker_anim_items("in"):
            self.ov_sticker_anim_in.addItem(_lab, _val)
        for _lab, _val in sticker_anim_items("out"):
            self.ov_sticker_anim_out.addItem(_lab, _val)
        self.ov_sticker_anim_in.currentIndexChanged.connect(self.on_change_ov)
        self.ov_sticker_anim_out.currentIndexChanged.connect(self.on_change_ov)
        self.ov_sticker_anim_in_label = QLabel("贴纸入场:", self)
        self.ov_sticker_anim_out_label = QLabel("贴纸出场:", self)
        bf.addRow("贴纸入场:", self.ov_sticker_anim_in, reset_key="ov.sticker_in")
        bf.addRow("贴纸出场:", self.ov_sticker_anim_out, reset_key="ov.sticker_out")
        self.ov_sticker_anim_in_label.setVisible(False)
        self.ov_sticker_anim_in.setVisible(False)
        self.ov_sticker_anim_out_label.setVisible(False)
        self.ov_sticker_anim_out.setVisible(False)
        for _w in (self.ov_sticker_anim_in, self.ov_sticker_anim_out):
            _row = inspector_row_of(_w)
            if _row is not None:
                _row.setVisible(False)
        _bfw = QWidget(); _bfw.setLayout(bf); self.image_basic_group.addWidget(_bfw)

        # ═══ Section 2: 文本属性 ═══
        self.ov_text_grp = CollapsibleSection("文本属性")
        fl = _mkform()
        from cutora.ui.i18n import tr as _tr
        from cutora.ui.icons import bind_icon, populate_icon_menu
        self.ov_preset_cat_combo = QComboBox()
        self.ov_preset_cat_combo.setMinimumWidth(0)
        self.ov_preset_cat_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.ov_preset_cat_combo.setToolTip(_tr("按文件夹分类筛选文本预设"))
        self.ov_preset_cat_combo.currentIndexChanged.connect(self._on_ov_preset_cat_changed)

        self.ov_preset_combo = QComboBox()
        self.ov_preset_combo.setMinimumWidth(0)
        self.ov_preset_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.ov_preset_combo.setToolTip(_tr("点击下拉切换文本样式预设（切换时保持当前文字内容不变）"))
        self.ov_preset_combo.currentIndexChanged.connect(self._on_ov_text_preset_changed)

        self.ov_preset_gallery_btn = QPushButton(_tr("🖼️ 预设画廊"))
        self.ov_preset_gallery_btn.setMinimumWidth(0)
        self.ov_preset_gallery_btn.setMinimumHeight(28)
        self.ov_preset_gallery_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.ov_preset_gallery_btn.setToolTip(_tr("展开大图样式画廊，按文件夹直观挑选预设（保持文字内容不变）"))
        self.ov_preset_gallery_btn.clicked.connect(self._open_ov_preset_gallery)

        self.ov_preset_menu_btn = QToolButton()
        self.ov_preset_menu_btn.setObjectName("inspectorIconBtn")
        self.ov_preset_menu_btn.setText(_tr("管理 ▾"))
        self.ov_preset_menu_btn.setMinimumHeight(28)
        self.ov_preset_menu_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.ov_preset_menu_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.ov_preset_menu_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.ov_preset_menu_btn.setToolTip(_tr("预设：新增 / 更新 / 重命名 / 删除 / 导入导出"))
        self.ov_preset_menu = QMenu(self)
        populate_icon_menu(self.ov_preset_menu, (
            (_tr("设为默认预设"), "star", self._ov_preset_save_default),
            None,
            (_tr("新增预设"), "plus", self._ov_preset_add),
            (_tr("重新保存当前预设"), "save", self._ov_preset_update),
            (_tr("重命名当前预设"), "pencil", self._ov_preset_rename),
            (_tr("删除当前预设"), "trash-2", self._ov_preset_delete),
            None,
            (_tr("导出预设"), "upload", self._ov_preset_export),
            (_tr("导入预设"), "download", self._ov_preset_import),
            None,
            (_tr("存为文本层预设…"), "plus", self._ov_save_text_layer_preset),
            (_tr("应用文本层预设为新覆层…"), "copy", self._ov_apply_text_layer_preset),
        ))
        self.ov_preset_menu_btn.setMenu(self.ov_preset_menu)
        from PySide6.QtGui import QBrush, QColor
        for act in self.ov_preset_menu.actions():
            if "重新保存当前预设" in (act.text() or ""):
                act.setIconVisibleInMenu(True)

        preset_card = QFrame()
        preset_card.setObjectName("ovPresetHeroCard")
        preset_card.setMinimumWidth(0)
        preset_card.setStyleSheet(
            "QFrame#ovPresetHeroCard {"
            "  background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #1D2536, stop:1 #171D2B);"
            "  border: 1px solid #3B5278;"
            "  border-radius: 8px;"
            "}"
            "QFrame#ovPresetHeroCard QLabel#presetHeroTitle {"
            "  color: #F3D078; font-weight: 700; font-size: 13px;"
            "}"
            "QFrame#ovPresetHeroCard QLabel#presetHeroSub {"
            "  color: #94A3B8; font-size: 11px;"
            "}"
            "QFrame#ovPresetHeroCard QPushButton {"
            "  background: #2563EB; color: #FFFFFF; border: 1px solid #60A5FA;"
            "  border-radius: 5px; padding: 2px 8px; font-weight: 700; font-size: 12px;"
            "}"
            "QFrame#ovPresetHeroCard QPushButton:hover {"
            "  background: #3B82F6;"
            "}"
        )
        card_v = QVBoxLayout(preset_card)
        card_v.setContentsMargins(8, 8, 8, 8)
        card_v.setSpacing(6)

        hero_top = QHBoxLayout()
        hero_top.setContentsMargins(0, 0, 0, 0)
        hero_top.setSpacing(6)
        hero_lbl = QLabel(_tr("✨ 样式预设库"))
        hero_lbl.setObjectName("presetHeroTitle")
        hero_lbl.setMinimumWidth(0)
        hero_top.addWidget(hero_lbl, 1)
        hero_top.addWidget(self.ov_preset_gallery_btn, 0)
        hero_top.addWidget(self.ov_preset_menu_btn, 0)
        card_v.addLayout(hero_top)

        cat_row = QHBoxLayout()
        cat_row.setContentsMargins(0, 0, 0, 0)
        cat_row.setSpacing(6)
        cat_lbl = QLabel(_tr("📁 预设分类:"))
        cat_lbl.setObjectName("presetHeroSub")
        cat_lbl.setMinimumWidth(0)
        cat_row.addWidget(cat_lbl, 0)
        cat_row.addWidget(self.ov_preset_cat_combo, 1)
        card_v.addLayout(cat_row)

        combo_row = QHBoxLayout()
        combo_row.setContentsMargins(0, 0, 0, 0)
        combo_row.setSpacing(6)
        combo_lbl = QLabel(_tr("🎨 选择样式:"))
        combo_lbl.setObjectName("presetHeroSub")
        combo_lbl.setMinimumWidth(0)
        combo_row.addWidget(combo_lbl, 0)
        combo_row.addWidget(self.ov_preset_combo, 1)
        card_v.addLayout(combo_row)

        fl.addSpanRow(preset_card)
        self.ov_rich = RichTextEditor()
        self.ov_content = self.ov_rich.edit
        self.ov_content.textChanged.connect(self._on_ov_content_changed)
        self.ov_rich.runsChanged.connect(self._on_ov_runs_changed)
        fl.addSpanRow(self.ov_rich)
        self.ov_font = QComboBox(); self.ov_font.clear(); register_font_combo(self.ov_font)
        self.ov_font.setCurrentText(_get_default_font_family()); self.ov_font.currentTextChanged.connect(self.on_change_ov)
        fl.addRow("字体:", self.ov_font, reset_key="ov.font")
        self.ov_size = QSpinBox(); self.ov_size.valueChanged.connect(self.on_change_ov)
        # Q126：QSpinBox 默认上限 99 —— 字号 100 以上在面板里根本输不进去，
        # 拖角柄放大到 120 再看面板显示的还是 99。
        self.ov_size.setRange(8, 600)
        self.ov_size.setMinimumWidth(48)
        self.ov_text_align = SegmentedChoice((
            ("left", "align-left", "左对齐"),
            ("center", "align-center", "居中"),
            ("right", "align-right", "右对齐"),
            ("justify", "align-justify", "两端对齐"),
        ))
        self.ov_text_align.currentTextChanged.connect(self.on_change_ov)
        # Q124：垂直对齐。以前只有顶对齐，想把两行字居中在下三分之一只能手算 Y
        self.ov_text_valign = SegmentedChoice((
            ("top", "align-vertical-justify-start", "顶对齐"),
            ("middle", "align-vertical-justify-center", "垂直居中"),
            ("bottom", "align-vertical-justify-end", "底对齐"),
        ))
        self.ov_text_valign.currentTextChanged.connect(self.on_change_ov)
        self.ov_writing_mode = EnumCombo()
        self.ov_writing_mode.addItem("横排", "horizontal")
        self.ov_writing_mode.addItem("竖排·从右往左", "vertical_rl")
        self.ov_writing_mode.addItem("竖排·从左往右", "vertical_lr")
        self.ov_writing_mode.currentIndexChanged.connect(self.on_change_ov)
        self.ov_fit_mode = EnumCombo()
        self.ov_fit_mode.addItem("高度自动（宽度你定）", "auto_height")
        self.ov_fit_mode.addItem("固定框", "none")
        self.ov_fit_mode.addItem("缩进框内", "shrink")
        self.ov_fit_mode.addItem("框随字长（只涨不缩）", "grow_box")
        self.ov_fit_mode.currentIndexChanged.connect(self.on_change_ov)
        fl.addRow("大小:", self.ov_size, reset_key="ov.fontsize")
        _align_host = QWidget()
        _align_row = QHBoxLayout(_align_host)
        _align_row.setContentsMargins(0, 0, 0, 0)
        _align_row.setSpacing(INSPECTOR_FIELD_GAP)
        _align_row.addWidget(self.ov_text_align, 0)
        _align_row.addWidget(self.ov_text_valign, 0)
        _align_row.addStretch(1)
        fl.addRow("对齐:", _align_host, reset_key="ov.align")
        fl.addRow("方向:", self.ov_writing_mode, reset_key="ov.writing")
        fl.addRow("适配:", self.ov_fit_mode, reset_key="ov.fit")
        self.ov_bold = QCheckBox("粗体"); self.ov_bold.toggled.connect(self.on_change_ov)
        self.ov_italic = QCheckBox("斜体"); self.ov_italic.toggled.connect(self.on_change_ov)
        rbi = QHBoxLayout(); rbi.setSpacing(INSPECTOR_OPTION_GAP); rbi.addWidget(self.ov_bold); rbi.addWidget(self.ov_italic); rbi.addStretch(1)
        fl.addRow("字形", rbi, reset_key="ov.glyph")
        self.ov_line_spacing = QSpinBox(); self.ov_line_spacing.setRange(0, 200); self.ov_line_spacing.valueChanged.connect(self.on_change_ov)
        self.ov_letter_spacing = QSpinBox(); self.ov_letter_spacing.setRange(-20, 100); self.ov_letter_spacing.valueChanged.connect(self.on_change_ov)
        fl.addRow("行距:", self.ov_line_spacing, reset_key="ov.line_spacing")
        mount_layout_fx(self, fl)
        self.ov_letter_spacing.hide()
        self.ov_pad_l = QSpinBox(); self.ov_pad_l.setRange(0, 1000); self.ov_pad_l.valueChanged.connect(self.on_change_ov)
        self.ov_pad_r = QSpinBox(); self.ov_pad_r.setRange(0, 1000); self.ov_pad_r.valueChanged.connect(self.on_change_ov)
        self.ov_pad_t = QSpinBox(); self.ov_pad_t.setRange(0, 1000); self.ov_pad_t.valueChanged.connect(self.on_change_ov)
        self.ov_pad_b = QSpinBox(); self.ov_pad_b.setRange(0, 1000); self.ov_pad_b.valueChanged.connect(self.on_change_ov)
        self.ov_first_indent = QSpinBox(); self.ov_first_indent.setRange(0, 400); self.ov_first_indent.valueChanged.connect(self.on_change_ov)
        self.ov_para_spacing = QSpinBox(); self.ov_para_spacing.setRange(0, 200); self.ov_para_spacing.valueChanged.connect(self.on_change_ov)
        for _sp, _pre in ((self.ov_pad_l, "左"), (self.ov_pad_r, "右"),
                          (self.ov_pad_t, "上"), (self.ov_pad_b, "下")):
            _sp.setPrefix(_pre + "  ")
        fl.addRow("边距", paired_fields(self.ov_pad_l, self.ov_pad_r), reset_key="ov.pad_x")
        fl.addRow("", paired_fields(self.ov_pad_t, self.ov_pad_b), reset_key="ov.pad_y")
        fl.addRow("首行缩进:", self.ov_first_indent, reset_key="ov.first_indent")
        fl.addRow("段间距:", self.ov_para_spacing, reset_key="ov.para_spacing")
        _flw = QWidget(); _flw.setLayout(fl); self.ov_text_grp.addWidget(_flw)

        # ═══ Section 3–5: fill / stroke / bg / glow / shadow via shared fx_fields ═══
        self._stroke_toggle = SectionToggle("stroke.enabled", False, True)
        self._bg_toggle = SectionToggle("bg.enabled", False, True)
        self._glow_toggle = SectionToggle("glow.enabled", False, True)
        self._shadow_toggle = SectionToggle("shadow.enabled", False, True)

        self.ov_fill_grp = sec_fill = CollapsibleSection("填充")
        ff = _mkform()
        mount_fill_section(self, ff, header=False)
        _ffw = QWidget(); _ffw.setLayout(ff); sec_fill.addWidget(_ffw)

        self.ov_strk = ToggleSwitch(self); self.ov_strk.toggled.connect(self.on_change_ov)
        self.ov_stroke_grp = sec_stroke = CollapsibleSection(
            "描边", toggle_widget=self.ov_strk, gray_content=True)
        sf = _mkform()
        mount_stroke_fx(self, sf)
        _sfw = QWidget(); _sfw.setLayout(sf); sec_stroke.addWidget(_sfw); sec_stroke.setExpanded(False)

        self.ov_bg_enable = ToggleSwitch(self); self.ov_bg_enable.toggled.connect(self.on_change_ov)
        self.ov_bg_grp = sec_bg = CollapsibleSection(
            "背景", toggle_widget=self.ov_bg_enable, gray_content=True)
        bgf = _mkform()
        mount_overlay_bg(self, bgf)
        _bgw = QWidget(); _bgw.setLayout(bgf); sec_bg.addWidget(_bgw); sec_bg.setExpanded(True)

        self.ov_glow_enable = ToggleSwitch(self); self.ov_glow_enable.toggled.connect(self.on_change_ov)
        self.ov_glow_grp = sec_glow = CollapsibleSection(
            "发光", toggle_widget=self.ov_glow_enable, gray_content=True)
        gf = _mkform()
        mount_glow_fx(self, gf)
        _gfw = QWidget(); _gfw.setLayout(gf); sec_glow.addWidget(_gfw); sec_glow.setExpanded(False)

        self.ov_shadow_enable = ToggleSwitch(); self.ov_shadow_enable.toggled.connect(self.on_change_ov)
        self.ov_shadow_grp = sec_shadow = CollapsibleSection(
            "投影", toggle_widget=self.ov_shadow_enable, gray_content=True)
        shf = _mkform()
        mount_shadow_fx(self, shf)
        _shw = QWidget(); _shw.setLayout(shf); sec_shadow.addWidget(_shw); sec_shadow.setExpanded(False)

        self.ov_extrude_grp = sec_extrude = CollapsibleSection("3D 挤出")
        ef = _mkform()
        mount_extrude_section(self, ef, header=False)
        _efw = QWidget(); _efw.setLayout(ef); sec_extrude.addWidget(_efw); sec_extrude.setExpanded(False)

        self.ov_fonts_grp = sec_fonts = CollapsibleSection("双字体")
        fontf = _mkform()
        self._fx_body_widget = self.ov_font      # 顶上的「字体」框 = fx.fonts.body，同一个框
        mount_fonts_emphasis(self, fontf, header=False)
        _fontw = QWidget(); _fontw.setLayout(fontf); sec_fonts.addWidget(_fontw); sec_fonts.setExpanded(False)

        self.ov_emphasis_grp = sec_emph = CollapsibleSection("自动着色")
        empf = _mkform()
        mount_emphasis_section(self, empf, header=False, for_subtitle=False)
        _empw = QWidget(); _empw.setLayout(empf); sec_emph.addWidget(_empw); sec_emph.setExpanded(False)

        self.ov_scatter_grp = sec_scatter = CollapsibleSection("弹出散落")
        scf = _mkform()
        mount_scatter_section(self, scf, header=False)
        _scw = QWidget(); _scw.setLayout(scf); sec_scatter.addWidget(_scw); sec_scatter.setExpanded(False)

        self.ov_anim_grp = sec_anim = CollapsibleSection("动画效果")
        af = _mkform()
        mount_anim_fx(self, af)
        _afw = QWidget(); _afw.setLayout(af); sec_anim.addWidget(_afw); sec_anim.setExpanded(False)

        # ═══ Section 8: 图片属性 ═══
        self.ov_image_grp = CollapsibleSection("图片属性")
        imf = _mkform()
        self.ov_scale, self.ov_scale_sl = self.add_slider_double_row(
            imf, "缩放:", 0.1, 5.0, reset_key="ov.scale")
        self.ov_scale_kf = self._attach_row_watch(self.ov_scale, "scale", kind="overlay")
        self.ov_flip_x = QCheckBox("左右翻转"); self.ov_flip_x.toggled.connect(self.on_change_ov)
        self.ov_flip_y = QCheckBox("上下翻转"); self.ov_flip_y.toggled.connect(self.on_change_ov)
        self.ov_keep_aspect = QCheckBox("锁定宽高比"); self.ov_keep_aspect.toggled.connect(self.on_change_ov)
        # The visible control is the chain button next to W/H; the checkbox
        # stays as the single source of truth the save/load code reads.
        self.ov_keep_aspect.toggled.connect(self.ov_lock_btn.setChecked)
        self.ov_lock_btn.toggled.connect(self.ov_keep_aspect.setChecked)
        self.ov_keep_aspect.hide()
        rflip = QHBoxLayout(); rflip.setSpacing(4); rflip.addWidget(self.ov_flip_x); rflip.addWidget(self.ov_flip_y); rflip.addWidget(self.ov_keep_aspect); rflip.addStretch(1)
        rflip.setSpacing(INSPECTOR_OPTION_GAP)
        imf.addRow("翻转", rflip, reset_key="ov.flip")
        self.ov_feather, self.ov_feather_sl = self.add_slider_spin_row(imf, "边缘羽化:", 0, 200)
        self.ov_feather.hide()
        self.ov_feather_sl.hide()
        imf.setRowVisible(imf.rowCount() - 1, False)
        self.ov_blend = ValueCombo(BLEND_ITEMS)
        self.ov_blend.valueChanged.connect(self._on_change_ov_blend)
        imf.addRow("混合模式:", self.ov_blend, reset_key="ov.blend")
        rmbg_row = QHBoxLayout(); rmbg_row.setSpacing(4)
        self.btn_ov_rmbg = QPushButton("✂️ AI 一键抠图去背景 (RMBG-2.0 发丝级)")
        self.btn_ov_rmbg.setToolTip("调用本地 RMBG-2.0 (BiRefNet) 发丝级抠图模型，免点选全自动抠除背景并原位替换为透明 PNG（支持 Ctrl+Z 撤销）")
        self.btn_ov_rmbg.clicked.connect(self._on_ov_rmbg_cutout)
        self.btn_ov_rmbg_restore = QPushButton("↩️ 还原原图")
        self.btn_ov_rmbg_restore.setToolTip("恢复抠图前的原始图片")
        self.btn_ov_rmbg_restore.clicked.connect(self._on_ov_rmbg_restore)
        rmbg_row.addWidget(self.btn_ov_rmbg, 1)
        rmbg_row.addWidget(self.btn_ov_rmbg_restore)
        imf.addRow("AI 抠图:", rmbg_row)
        _imw = QWidget(); _imw.setLayout(imf); self.ov_image_grp.addWidget(_imw)

        self.ov_fade_grp = CollapsibleSection("四边渐隐")
        fade_form = _mkform()
        self._add_fade_controls(fade_form, "ov", self._on_ov_fade)
        _fdw = QWidget(); _fdw.setLayout(fade_form); self.ov_fade_grp.addWidget(_fdw)

        # ═══ Section 9: 层级 ═══
        self.ov_element_order_grp = CollapsibleSection("层级")
        self.ov_bring_front = QPushButton("置顶"); self.ov_bring_front.clicked.connect(lambda: self.element_raise.emit())
        self.ov_move_up = QPushButton("上移"); self.ov_move_up.clicked.connect(lambda: self.element_move_up.emit())
        self.ov_move_down = QPushButton("下移"); self.ov_move_down.clicked.connect(lambda: self.element_lower.emit())
        row_order = QHBoxLayout(); row_order.setSpacing(4); row_order.addWidget(self.ov_bring_front); row_order.addWidget(self.ov_move_up); row_order.addWidget(self.ov_move_down)
        _row = QWidget(); _row.setLayout(row_order); self.ov_element_order_grp.addWidget(_row)

        self.sync_intro_style_btn = QPushButton("同步到所有视频开场（样式）")
        self.sync_intro_style_btn.clicked.connect(self.sync_intro_style.emit)
        self.sync_intro_style_btn.setVisible(False)
        self.text_elements_container = QWidget()
        self.text_items_layout = QVBoxLayout(self.text_elements_container)
        self.shape_elements_container = QWidget()
        self.shape_items_layout = QVBoxLayout(self.shape_elements_container)

        # ═══ 装配 ═══
        container_layout.addWidget(self.image_basic_group)
        container_layout.addWidget(self.ov_text_grp)
        container_layout.addWidget(sec_fill)
        container_layout.addWidget(sec_stroke)
        container_layout.addWidget(sec_bg)
        container_layout.addWidget(sec_glow)
        container_layout.addWidget(sec_shadow)
        container_layout.addWidget(sec_extrude)
        container_layout.addWidget(sec_fonts)
        container_layout.addWidget(sec_emph)
        container_layout.addWidget(sec_scatter)
        container_layout.addWidget(sec_anim)
        # Q174：调整图层（纯色 + 对下面所有画面的效果）
        from cutora.ui.panels.adjust_panel import AdjustPanel
        self.ov_adjust = AdjustPanel()
        self.ov_adjust.changed.connect(self._on_adjust_changed)
        self.ov_adjust.hide()
        container_layout.addWidget(self.ov_adjust)
        container_layout.addWidget(self.ov_image_grp)
        from cutora.ui.panels.grade import GradeEditor
        self.ov_grade = GradeEditor("图片调色")
        self.ov_grade.changed.connect(self._on_ov_grade)
        self.ov_grade.hide()
        container_layout.addWidget(self.ov_grade)
        container_layout.addWidget(self.ov_fade_grp)
        # Q152-B/C：遮罩（文本 / 图片）+ 抠像（图片）
        from cutora.ui.panels.layer_fx_panel import LayerFxPanel
        self.ov_fx = LayerFxPanel(with_keyer=True)
        self.ov_fx.bind(lambda: self._fx_target("overlay"))
        self.ov_fx.canvas_action.connect(self._on_fx_canvas_action)
        container_layout.addWidget(self.ov_fx)
        container_layout.addWidget(self.ov_element_order_grp)
        container_layout.addStretch()
        container.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        install_inspector_limits(container)
        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setObjectName("propOverlayScroll"); scroll.setWidget(container)
        self.stack.addWidget(scroll)

    def _add_audio_slider_row(self, form, label, min_v, max_v, step=0.01, reset_key=None):
        """与 add_slider_double_row 相同结构，但信号连接 on_change_audio。step 越小细度越高。"""
        decimals = 3 if step < 0.01 else 2
        ss = SliderSpin(min_v, max_v, step, decimals)
        ss.valueChanged.connect(self.on_change_audio)
        add_inspect_row(form, label, ss, reset_key=reset_key)
        return ss.spin, ss.slider

    def init_audio_panel(self):
        w = QWidget()
        cl = QVBoxLayout(w); cl.setAlignment(Qt.AlignmentFlag.AlignTop); cl.setSpacing(6); cl.setContentsMargins(8, 8, 8, 8)

        sec = CollapsibleSection("音频参数")
        form = InspectForm()
        form.set_reset_resolver(self._resolve_reset_key)
        form.setSpacing(INSPECTOR_ROW_GAP)
        form.setContentsMargins(
            INSPECTOR_PAD_X, INSPECTOR_ROW_GAP, INSPECTOR_PAD_X, INSPECTOR_ROW_GAP)
        self.audio_vol, self.audio_vol_sl = self._add_audio_slider_row(
            form, "音量:", 0.0, 5.0, step=0.001, reset_key="aud.vol")
        self.audio_vol_kf = self._attach_row_watch(self.audio_vol, "gain_db", kind="audio")
        self.audio_vol_hint = QLabel("💡 >1.0 时预览与导出均生效")
        self.audio_vol_hint.setObjectName("hint")
        form.addRow("", self.audio_vol_hint)
        self.audio_pitch, self.audio_pitch_sl = self._add_audio_slider_row(
            form, "音高:", -24.0, 24.0, step=0.01, reset_key="aud.pitch")
        self.audio_speed = QDoubleSpinBox()
        self.audio_speed.setRange(0.1, 10.0)
        self.audio_speed.setSingleStep(0.1)
        self.audio_speed.setDecimals(2)
        self.audio_speed.setSuffix(" ×")
        self.audio_speed.setToolTip("常数变速，变调保持。链接的口播会跟着视频速度走。")
        self.audio_speed.valueChanged.connect(self.on_change_audio)
        form.addRow("速度:", self.audio_speed)
        self.btn_speed_curve = QPushButton("添加速度曲线")
        self.btn_speed_curve.setToolTip("不切开片段。空曲线只用上面的常数速度。")
        self.btn_speed_curve.clicked.connect(self._toggle_speed_curve)
        form.addRow("", self.btn_speed_curve)
        self.audio_denoise = QComboBox()
        for _lab, _key in (
            ("关闭", "off"), ("仅去噪", "denoise"), ("播客", "podcast"),
            ("YouTube", "youtube"), ("大厅", "hall"),
        ):
            self.audio_denoise.addItem(_lab, _key)
        self.audio_denoise.setToolTip("大厅只做高频截止和降噪，不去回声。")
        self.audio_denoise.currentIndexChanged.connect(self.on_change_audio)
        form.addRow("降噪:", self.audio_denoise)
        self.audio_denoise_strength = QComboBox()
        for _lab, _key in (("弱", "weak"), ("中", "mid"), ("强", "strong")):
            self.audio_denoise_strength.addItem(_lab, _key)
        self.audio_denoise_strength.setCurrentIndex(1)
        self.audio_denoise_strength.currentIndexChanged.connect(self.on_change_audio)
        form.addRow("强度:", self.audio_denoise_strength)
        self.audio_stem_status = QLabel("未分离")
        self.audio_stem_status.setObjectName("hint")
        form.addRow("分离", self.audio_stem_status)
        self.btn_clear_stem = QPushButton("清除分离")
        self.btn_clear_stem.setToolTip("去掉只留说话 / 只留伴奏的结果，回到原文件。")
        self.btn_clear_stem.clicked.connect(self._clear_stem_proc)
        form.addRow("", self.btn_clear_stem)
        self.audio_fade_in_dur, self.audio_fade_in_dur_sl = self._add_audio_slider_row(
            form, "淡入:", 0.0, 30.0, step=0.01, reset_key="aud.fade_in")
        self.audio_fade_out_dur, self.audio_fade_out_dur_sl = self._add_audio_slider_row(
            form, "淡出:", 0.0, 30.0, step=0.01, reset_key="aud.fade_out")
        self.audio_spatial_enable = QCheckBox("这条音频使用空间立体声")
        self.audio_spatial_enable.setToolTip(
            "勾上：这条音频吃空间立体声；不勾：这条音频保持干声。多选时可批量勾/取消。")
        self.audio_spatial_enable.stateChanged.connect(self.on_change_audio)
        form.addRow("", self.audio_spatial_enable, reset_key="aud.spatial")
        self.audio_spatial_amount, self.audio_spatial_amount_sl = self._add_audio_slider_row(
            form, "湿声:", 0.0, 0.5, step=0.01, reset_key="aud.wet")
        self._sync_spatial_wet_enabled()
        self.audio_link_status = QLabel("未链接")
        self.audio_link_status.setObjectName("hint")
        form.addRow("链接", self.audio_link_status)
        _fw = QWidget(); _fw.setLayout(form); sec.addWidget(_fw)
        cl.addWidget(sec); cl.addStretch()
        install_inspector_limits(w)
        scroll = QScrollArea(); scroll.setWidget(w); scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setObjectName("propAudioScroll")
        self.stack.addWidget(scroll)

    def init_video_panel(self):
        """视频 Clip 属性面板（stack index 3）。"""
        w = QWidget()
        container = QWidget(); container.setObjectName("propPanelContainer")
        cl = QVBoxLayout(container); cl.setAlignment(Qt.AlignmentFlag.AlignTop)
        cl.setSpacing(INSPECTOR_SECTION_GAP)
        cl.setContentsMargins(0, INSPECTOR_PAD_Y, 0, INSPECTOR_PAD_Y)
        def _lbl(t):
            lb = QLabel(t); lb.setObjectName("formHint"); return lb
        def _mkform():
            f = InspectForm()
            f.set_reset_resolver(self._resolve_reset_key)
            f.setSpacing(INSPECTOR_ROW_GAP)
            f.setContentsMargins(
                INSPECTOR_PAD_X, INSPECTOR_ROW_GAP, INSPECTOR_PAD_X, INSPECTOR_ROW_GAP)
            return f

        # ═══ Section: 变换 ═══
        sec_xform = CollapsibleSection("变换")
        form = _mkform()
        self.vid_fit = ValueCombo((("fill", "填充（裁切）"), ("fit", "适应（留边）"), ("stretch", "拉伸")))
        self.vid_fit.valueChanged.connect(self.on_change_video)
        form.addRow("适配", self.vid_fit, reset_key="vid.fit")
        self.vid_x = QDoubleSpinBox(); self.vid_x.setRange(-5000, 5000); self.vid_x.setSingleStep(1.0); self.vid_x.valueChanged.connect(self.on_change_video)
        self.vid_y = QDoubleSpinBox(); self.vid_y.setRange(-5000, 5000); self.vid_y.setSingleStep(1.0); self.vid_y.valueChanged.connect(self.on_change_video)
        self.vid_x.setPrefix("X  "); self.vid_y.setPrefix("Y  ")
        self.vid_x_kf = self._make_kf_watch("x")
        self.vid_y_kf = self._make_kf_watch("y")
        form.addRow("位置", paired_fields(
            with_trailing(self.vid_x, self.vid_x_kf), with_trailing(self.vid_y, self.vid_y_kf)),
            reset_key="vid.pos")
        self.vid_scale = QDoubleSpinBox(); self.vid_scale.setRange(0.01, 10.0); self.vid_scale.setSingleStep(0.01); self.vid_scale.setDecimals(2); self.vid_scale.valueChanged.connect(self.on_change_video)
        self.vid_rotation = QDoubleSpinBox(); self.vid_rotation.setRange(-360, 360); self.vid_rotation.setSingleStep(1.0); self.vid_rotation.valueChanged.connect(self.on_change_video)
        self.vid_scale.setSuffix(" ×"); self.vid_rotation.setSuffix("°")
        self.vid_scale_kf = self._make_kf_watch("scale")
        self.vid_rot_kf = self._make_kf_watch("rotation")
        self.vid_lock_btn = inspector_icon_button(
            "link", "锁定宽高比", checkable=True, object_name="vidAspectLockBtn")
        self.vid_lock_btn.setChecked(True)
        self.vid_lock_btn.toggled.connect(self._on_vid_lock_toggled)
        bind_icon_safe(self.vid_lock_btn, "link", "accent")
        self.vid_restore_btn = inspector_icon_button(
            "rotate-ccw", "还原宽高比", object_name="vidRestoreAspectBtn")
        self.vid_restore_btn.clicked.connect(self._restore_video_aspect)
        vid_scale_trail = QWidget()
        vid_scale_trail.setObjectName("vidScaleTrail")
        _vsl = QHBoxLayout(vid_scale_trail)
        _vsl.setContentsMargins(0, 0, 0, 0)
        _vsl.setSpacing(INSPECTOR_FIELD_GAP)
        _vsl.addWidget(self.vid_scale_kf)
        _vsl.addWidget(self.vid_lock_btn)
        _vsl.addWidget(self.vid_restore_btn)
        form.addRow("缩放", with_trailing(self.vid_scale, vid_scale_trail), reset_key="vid.scale")
        form.addRow("旋转", with_trailing(self.vid_rotation, self.vid_rot_kf), reset_key="vid.rotation")
        self.vid_opacity, self.vid_opacity_sl = self.add_slider_spin_row_video(
            form, "不透明度", 0, 255, reset_key="vid.opacity")
        self.vid_opacity_kf = self._attach_row_watch(self.vid_opacity, "opacity")
        # Per-row ↺ replaces the old "重置: 位置/缩放/旋转" button strip; the
        # buttons stay as (hidden) API for callers that click them.
        self.vid_reset_pos = QPushButton("位置"); self.vid_reset_pos.clicked.connect(self._reset_video_pos)
        self.vid_reset_scale = QPushButton("缩放"); self.vid_reset_scale.clicked.connect(self._reset_video_scale)
        self.vid_reset_rot = QPushButton("旋转"); self.vid_reset_rot.clicked.connect(self._reset_video_rotation)
        for _b in (self.vid_reset_pos, self.vid_reset_scale, self.vid_reset_rot):
            _b.setParent(container); _b.hide()
        _fw = QWidget(); _fw.setLayout(form); sec_xform.addWidget(_fw)

        # ═══ Section: 背景填充（Q117）═══
        # 画面没铺满画幅时，露出来的地方补什么。模糊背景是最常用的那一种：
        # 原片放大 + 镜头模糊垫在底下，比黑边好看得多。
        sec_bgfill = CollapsibleSection("背景填充")
        bff = _mkform()
        self.vid_bg_mode = ValueCombo((
            ("none", "无（露黑底）"),
            ("blur", "模糊背景（露边时自动虚化）"),
            ("mirror", "镜像填充"),
            ("tile", "平铺填充"),
            ("stretch", "拉伸填充"),
        ))
        self.vid_bg_mode.valueChanged.connect(self._on_vid_bg_fill)
        bff.addRow("填充", self.vid_bg_mode, reset_key="vid.bg_mode")
        self.vid_bg_status_lbl = QLabel("")
        self.vid_bg_status_lbl.setWordWrap(True)
        self.vid_bg_status_lbl.setStyleSheet("color: #7E8B9B; font-size: 11px; padding: 2px 0;")
        bff.addRow("", self.vid_bg_status_lbl)
        # 一行只连一个信号：SliderSpin 自己会把滑杆和数字框合成一个
        # valueChanged，再去连 spin/slider 就是同一次改动发三遍。
        _bg_blur = SliderSpin(0, 100, 1, 0)
        _bg_blur.valueChanged.connect(self._on_vid_bg_fill)
        add_inspect_row(bff, "模糊程度", _bg_blur, reset_key="vid.bg_blur")
        self.vid_bg_blur, self.vid_bg_blur_sl = _bg_blur.spin, _bg_blur.slider
        # Q151：镜头模糊（六边形光圈、亮点散成光斑）；旧版柔化留给老工程
        self.vid_bg_style = ValueCombo((
            ("lens", "镜头模糊（光斑）"),
            ("legacy", "柔化（旧版）"),
        ))
        self.vid_bg_style.valueChanged.connect(self._on_vid_bg_fill)
        bff.addRow("模糊类型", self.vid_bg_style, reset_key="vid.bg_style")
        _bg_hl = SliderSpin(0, 100, 1, 0)
        _bg_hl.valueChanged.connect(self._on_vid_bg_fill)
        add_inspect_row(bff, "散景高光", _bg_hl, reset_key="vid.bg_hl")
        self.vid_bg_hl, self.vid_bg_hl_sl = _bg_hl.spin, _bg_hl.slider
        self.vid_bg_hl.setToolTip("亮点散成光斑的强度（默认 35）")
        _bg_dim = SliderSpin(0, 100, 1, 0)
        _bg_dim.valueChanged.connect(self._on_vid_bg_fill)
        add_inspect_row(bff, "压暗", _bg_dim, reset_key="vid.bg_dim")
        self.vid_bg_dim, self.vid_bg_dim_sl = _bg_dim.spin, _bg_dim.slider
        self.vid_bg_scale = QDoubleSpinBox()
        self.vid_bg_scale.setRange(0.05, 8.0)
        self.vid_bg_scale.setSingleStep(0.01)
        self.vid_bg_scale.setDecimals(2)
        self.vid_bg_scale.setSuffix(" ×")
        self.vid_bg_scale.valueChanged.connect(self._on_vid_bg_fill)
        bff.addRow("背景缩放", self.vid_bg_scale, reset_key="vid.bg_scale")
        self.vid_bg_x = QDoubleSpinBox(); self.vid_bg_x.setRange(-5000, 5000)
        self.vid_bg_y = QDoubleSpinBox(); self.vid_bg_y.setRange(-5000, 5000)
        self.vid_bg_x.setPrefix("X  "); self.vid_bg_y.setPrefix("Y  ")
        self.vid_bg_x.setSingleStep(1.0); self.vid_bg_y.setSingleStep(1.0)
        self.vid_bg_x.valueChanged.connect(self._on_vid_bg_fill)
        self.vid_bg_y.valueChanged.connect(self._on_vid_bg_fill)
        bff.addRow("背景位置", paired_fields(self.vid_bg_x, self.vid_bg_y),
                   reset_key="vid.bg_pos")
        self.vid_bg_rot = QDoubleSpinBox()
        self.vid_bg_rot.setRange(-360, 360)
        self.vid_bg_rot.setSingleStep(1.0)
        self.vid_bg_rot.setSuffix("°")
        self.vid_bg_rot.valueChanged.connect(self._on_vid_bg_fill)
        bff.addRow("背景旋转", self.vid_bg_rot, reset_key="vid.bg_rot")
        self._bg_fill_form = bff
        _bfw = QWidget(); _bfw.setLayout(bff); sec_bgfill.addWidget(_bfw)

        # ═══ Section: 播放 ═══
        sec_play = CollapsibleSection("播放")
        pf = _mkform()
        # Transition
        self.vid_transition = QComboBox()
        for label, val in anim_engine.TRANSITION_PRESETS: self.vid_transition.addItem(label, val)
        self.vid_transition.currentIndexChanged.connect(self.on_change_video)
        self.vid_transition_dur = QDoubleSpinBox(); self.vid_transition_dur.setRange(0.0, 5.0); self.vid_transition_dur.setSingleStep(0.05); self.vid_transition_dur.setDecimals(2)
        self.vid_transition_dur.valueChanged.connect(self.on_change_video)
        self.vid_transition_dur.setSuffix(" s")
        pf.addRow("转场", self.vid_transition, reset_key="vid.transition")
        pf.addRow("转场时长", self.vid_transition_dur, reset_key="vid.transition_dur")
        # Speed
        self.vid_speed = QDoubleSpinBox(); self.vid_speed.setRange(0.1, 10.0); self.vid_speed.setSingleStep(0.1); self.vid_speed.setDecimals(2); self.vid_speed.valueChanged.connect(self.on_change_video)
        self.vid_speed.setSuffix(" ×")
        pf.addRow("速度", self.vid_speed, reset_key="vid.speed")
        # Flip + Loop + Match (compact row)
        self.vid_flip_x = QCheckBox("水平翻转"); self.vid_flip_x.stateChanged.connect(self.on_change_video)
        self.vid_flip_y = QCheckBox("垂直翻转"); self.vid_flip_y.stateChanged.connect(self.on_change_video)
        self.vid_loop = QCheckBox("循环"); self.vid_loop.stateChanged.connect(self.on_change_video)
        self.vid_match_dur = QCheckBox("匹配时长"); self.vid_match_dur.stateChanged.connect(self.on_change_video)
        ropts = QHBoxLayout(); ropts.setSpacing(INSPECTOR_OPTION_GAP); ropts.addWidget(self.vid_flip_x); ropts.addWidget(self.vid_flip_y); ropts.addStretch(1)
        pf.addRow("翻转", ropts, reset_key="vid.flip")
        ropts2 = QHBoxLayout(); ropts2.setSpacing(INSPECTOR_OPTION_GAP); ropts2.addWidget(self.vid_loop); ropts2.addWidget(self.vid_match_dur); ropts2.addStretch(1)
        pf.addRow("时长", ropts2, reset_key="vid.play")
        self.vid_alpha_mode = QComboBox()
        self.vid_alpha_mode.setObjectName("vidAlphaMode")
        self.vid_alpha_mode.addItem("直通", "straight")
        self.vid_alpha_mode.addItem("预乘", "premultiplied")
        self.vid_alpha_mode.currentIndexChanged.connect(self.on_change_video)
        pf.addRow("透明通道", self.vid_alpha_mode, reset_key="vid.alpha")
        _pfw = QWidget(); _pfw.setLayout(pf); sec_play.addWidget(_pfw)

        # ═══ Section: 裁剪 ═══
        sec_crop = CollapsibleSection("裁剪 / 羽化")
        cf = _mkform()
        self.vid_crop_l = QSpinBox(); self.vid_crop_l.setRange(0, 10000); self.vid_crop_l.valueChanged.connect(self.on_change_video)
        self.vid_crop_r = QSpinBox(); self.vid_crop_r.setRange(0, 10000); self.vid_crop_r.valueChanged.connect(self.on_change_video)
        self.vid_crop_t = QSpinBox(); self.vid_crop_t.setRange(0, 10000); self.vid_crop_t.valueChanged.connect(self.on_change_video)
        self.vid_crop_b = QSpinBox(); self.vid_crop_b.setRange(0, 10000); self.vid_crop_b.valueChanged.connect(self.on_change_video)
        for _sp, _pre in ((self.vid_crop_l, "左"), (self.vid_crop_r, "右"),
                          (self.vid_crop_t, "上"), (self.vid_crop_b, "下")):
            _sp.setPrefix(_pre + "  ")
        cf.addRow("裁剪", paired_fields(self.vid_crop_l, self.vid_crop_r), reset_key="vid.crop_x")
        cf.addRow("", paired_fields(self.vid_crop_t, self.vid_crop_b), reset_key="vid.crop_y")
        self.vid_feather, self.vid_feather_sl = self.add_slider_spin_row_video(cf, "边缘羽化:", 0, 200)
        self.vid_feather.hide()
        self.vid_feather_sl.hide()
        cf.setRowVisible(cf.rowCount() - 1, False)
        self.vid_blend = ValueCombo(VIDEO_BLEND_ITEMS)
        self.vid_blend.setToolTip(LUMA_MATTE_TIP)
        self.vid_blend.valueChanged.connect(self.on_change_video_blend)
        cf.addRow("混合模式", self.vid_blend, reset_key="vid.blend")
        self.vid_matte = QCheckBox("作为上一条视频的蒙版")
        self.vid_matte.setToolTip(
            "黑白视频只挖紧挨在上面的那一条视频。黑色透明，白色保留。"
            "不挖已经画好的整幅画面。和混合模式里的「作为蒙版」不是同一个开关。")
        self.vid_matte.toggled.connect(self._on_vid_matte)
        self.vid_matte_inv = QCheckBox("蒙版反相")
        self.vid_matte_inv.setToolTip("白色变透明，黑色保留。")
        self.vid_matte_inv.toggled.connect(self._on_vid_matte)
        self.vid_matte_inv.setEnabled(False)
        _matte_row = QHBoxLayout()
        _matte_row.setSpacing(INSPECTOR_OPTION_GAP)
        _matte_row.addWidget(self.vid_matte)
        _matte_row.addWidget(self.vid_matte_inv)
        _matte_row.addStretch(1)
        cf.addRow("子蒙版", _matte_row)
        _cfw = QWidget(); _cfw.setLayout(cf); sec_crop.addWidget(_cfw); sec_crop.setExpanded(False)

        sec_fade = CollapsibleSection("四边渐隐")
        vf = _mkform()
        self._add_fade_controls(vf, "vid", self._on_vid_fade)
        _vfw = QWidget(); _vfw.setLayout(vf); sec_fade.addWidget(_vfw)

        from cutora.ui.panels.grade import GradeEditor
        self.vid_grade = GradeEditor("片段调色")
        self.vid_grade.changed.connect(self._on_vid_grade)
        self.vid_grade.scope_mode_changed.connect(self._on_scope_mode)

        # Q152-B/C：遮罩 + 蓝绿幕抠像
        from cutora.ui.panels.layer_fx_panel import LayerFxPanel
        self.vid_fx = LayerFxPanel(with_keyer=True)
        self.vid_fx.bind(lambda: self._fx_target("clip"), self._matte_context)
        self.vid_fx.canvas_action.connect(self._on_fx_canvas_action)
        # Q154：预合成组（在组里才出现，放最上面）
        from cutora.ui.panels.group_section import GroupSection
        self.vid_group = GroupSection()
        self.vid_group.bind(lambda: self._fx_target("clip")[:2])
        self.vid_group.changed.connect(lambda: self.load_data("video_clip", (self._video_track, self._video_clip))
                                       if self._video_clip is not None else None)
        cl.addWidget(self.vid_group)
        self.vid_link_status = QLabel("未链接")
        self.vid_link_status.setObjectName("formHint")
        cl.addWidget(self.vid_link_status)
        cl.addWidget(sec_xform); cl.addWidget(sec_bgfill); cl.addWidget(self.vid_fx); cl.addWidget(sec_play); cl.addWidget(sec_crop); cl.addWidget(sec_fade)
        cl.addWidget(self.vid_grade)
        cl.addStretch()
        install_inspector_limits(container)
        scroll = QScrollArea(); scroll.setWidget(container); scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setObjectName("propVideoScroll")
        self.stack.addWidget(scroll)

    def init_transition_panel(self):
        """独立转场胶带属性面板（stack index 4）。"""
        w = QWidget()
        cl = QVBoxLayout(w); cl.setAlignment(Qt.AlignmentFlag.AlignTop)
        cl.setSpacing(INSPECTOR_SECTION_GAP)
        cl.setContentsMargins(0, INSPECTOR_PAD_Y, 0, INSPECTOR_PAD_Y)
        sec = CollapsibleSection("转场参数")
        form = InspectForm()
        form.set_reset_resolver(self._resolve_reset_key)
        form.setSpacing(INSPECTOR_ROW_GAP)
        form.setContentsMargins(
            INSPECTOR_PAD_X, INSPECTOR_ROW_GAP, INSPECTOR_PAD_X, INSPECTOR_ROW_GAP)
        # 类型 + 时长 同一行
        self.trans_preset_cb = QComboBox()
        lib = getattr(anim_engine, "STANDALONE_TRANSITION_LIBRARY", None) or anim_engine.TRANSITION_PRESETS
        for label, val in lib:
            self.trans_preset_cb.addItem(label, val)
        self.trans_preset_cb.currentIndexChanged.connect(self.on_change_transition)
        self.trans_dur_sp = QDoubleSpinBox(); self.trans_dur_sp.setRange(0.05, 5.0); self.trans_dur_sp.setSingleStep(0.05); self.trans_dur_sp.setDecimals(2)
        self.trans_dur_sp.setSuffix(" s")
        self.trans_dur_sp.valueChanged.connect(self.on_change_transition)
        form.addRow("类型", self.trans_preset_cb)
        form.addRow("时长", self.trans_dur_sp, reset_key="trans.dur")
        # 缩放 + 模糊 同一行
        self.trans_zoom_start_sp = QDoubleSpinBox(); self.trans_zoom_start_sp.setRange(0.3, 1.0); self.trans_zoom_start_sp.setSingleStep(0.05); self.trans_zoom_start_sp.setDecimals(2)
        self.trans_zoom_start_sp.setValue(0.7); self.trans_zoom_start_sp.setToolTip("进场视频的起始缩放比例（默认 0.7）")
        self.trans_zoom_start_sp.valueChanged.connect(self.on_change_transition)
        self.trans_motion_blur_sp = QDoubleSpinBox(); self.trans_motion_blur_sp.setRange(0.0, 1.0); self.trans_motion_blur_sp.setSingleStep(0.05); self.trans_motion_blur_sp.setDecimals(2)
        self.trans_motion_blur_sp.setValue(0.4); self.trans_motion_blur_sp.setToolTip("运动模糊强度（0=无, 1=最强）")
        self.trans_motion_blur_sp.valueChanged.connect(self.on_change_transition)
        form.addRow("起始缩放", self.trans_zoom_start_sp, reset_key="trans.zoom")
        form.addRow("运动模糊", self.trans_motion_blur_sp, reset_key="trans.blur")
        self.trans_dir_cb = QComboBox()
        for lab, val in (
            ("左", "left"), ("右", "right"), ("上", "up"), ("下", "down"),
            ("顺时针", "cw"), ("逆时针", "ccw"),
            ("水平", "horizontal"), ("垂直", "vertical"),
        ):
            self.trans_dir_cb.addItem(lab, val)
        self.trans_dir_cb.currentIndexChanged.connect(self.on_change_transition)
        self.trans_angle_sp = QDoubleSpinBox()
        self.trans_angle_sp.setRange(0.0, 360.0)
        self.trans_angle_sp.setSingleStep(1.0)
        self.trans_angle_sp.setDecimals(1)
        self.trans_angle_sp.setToolTip("线性擦除角度（0=左→右）")
        self.trans_angle_sp.setSuffix("°")
        self.trans_angle_sp.valueChanged.connect(self.on_change_transition)
        self.trans_angle_sl = QSlider(Qt.Orientation.Horizontal)
        self.trans_angle_sl.setRange(0, 360)
        self.trans_angle_sl.valueChanged.connect(
            lambda v: self.trans_angle_sp.setValue(float(v)))
        self.trans_angle_sp.valueChanged.connect(
            lambda v: (self.trans_angle_sl.blockSignals(True),
                       self.trans_angle_sl.setValue(int(round(v))),
                       self.trans_angle_sl.blockSignals(False)))
        form.addRow("方向", self.trans_dir_cb, reset_key="trans.dir")
        self._trans_angle_host = slider_with_spin(self.trans_angle_sl, self.trans_angle_sp)
        form.addRow("角度", self._trans_angle_host, reset_key="trans.angle")
        self.trans_intensity_sp = QDoubleSpinBox()
        self.trans_intensity_sp.setObjectName("transIntensity")
        self.trans_intensity_sp.setRange(0.1, 2.5)
        self.trans_intensity_sp.setSingleStep(0.05)
        self.trans_intensity_sp.setDecimals(2)
        self.trans_intensity_sp.setValue(1.0)
        self.trans_intensity_sp.setToolTip("转场强度")
        self.trans_intensity_sp.valueChanged.connect(self.on_change_transition)
        form.addRow("强度", self.trans_intensity_sp, reset_key="trans.intensity")
        self.trans_feather = SliderSpin(0, 50, 1, 0)
        self.trans_feather.setSuffix("%")
        self.trans_feather.setToolTip("边缘羽化，按画面短边的百分比。0 为硬边。")
        self.trans_feather.valueChanged.connect(self.on_change_transition)
        form.addRow("边缘羽化", self.trans_feather, reset_key="trans.feather")
        self.trans_easing_cb = QComboBox()
        self.trans_easing_cb.setObjectName("transEasing")
        for lab, val in anim_engine.EASING_LABELS:
            self.trans_easing_cb.addItem(lab, val)
        self.trans_easing_cb.currentIndexChanged.connect(self.on_change_transition)
        form.addRow("缓动", self.trans_easing_cb, reset_key="trans.easing")
        _fw = QWidget(); _fw.setLayout(form); sec.addWidget(_fw)
        cl.addWidget(sec); cl.addStretch()
        install_inspector_limits(w)
        scroll = QScrollArea(); scroll.setWidget(w); scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.stack.addWidget(scroll)

    def init_track_panel(self):
        """轨道属性（stack index 5）：名字、变换、不透明、混合、图钉。"""
        from cutora.ui.i18n import tr
        container = QWidget()
        container.setObjectName("propPanelContainer")
        container.setMinimumWidth(0)
        container.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        cl = QVBoxLayout(container)
        cl.setAlignment(Qt.AlignmentFlag.AlignTop)
        cl.setSpacing(INSPECTOR_SECTION_GAP)
        cl.setContentsMargins(0, INSPECTOR_PAD_Y, 0, INSPECTOR_PAD_Y)

        def _mkform():
            f = InspectForm()
            f.set_reset_resolver(self._resolve_reset_key)
            f.setSpacing(INSPECTOR_ROW_GAP)
            f.setContentsMargins(
                INSPECTOR_PAD_X, INSPECTOR_ROW_GAP, INSPECTOR_PAD_X, INSPECTOR_ROW_GAP)
            return f

        self.trk_banner = QLabel("")
        self.trk_banner.setObjectName("hint")
        self.trk_banner.setWordWrap(True)
        self.trk_banner.setMinimumWidth(0)
        self.trk_banner.hide()
        cl.addWidget(self.trk_banner)

        sec_id = CollapsibleSection("轨道")
        idf = _mkform()
        self.trk_name = QLineEdit()
        self.trk_name.setPlaceholderText(tr("名字"))
        self.trk_name.setMinimumWidth(0)
        self.trk_name.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.trk_name.editingFinished.connect(self._commit_track_props)
        idf.addRow(tr("名字"), self.trk_name)
        self.trk_pin = QCheckBox(tr("图钉"))
        self.trk_pin.setToolTip(tr("钉住的轨道一起改属性，会话状态，不存工程。undo 一次回滚"))
        self.trk_pin.setMinimumWidth(0)
        self.trk_pin.toggled.connect(self._on_track_pin_toggled)
        idf.addRow(tr("联动"), self.trk_pin)
        _idw = QWidget()
        _idw.setMinimumWidth(0)
        _idw.setLayout(idf)
        sec_id.addWidget(_idw)

        sec_xform = CollapsibleSection("变换")
        xf = _mkform()
        self.trk_x = QDoubleSpinBox()
        self.trk_x.setRange(-5000, 5000)
        self.trk_x.setSingleStep(1.0)
        self.trk_x.editingFinished.connect(self._commit_track_props)
        self.trk_y = QDoubleSpinBox()
        self.trk_y.setRange(-5000, 5000)
        self.trk_y.setSingleStep(1.0)
        self.trk_y.editingFinished.connect(self._commit_track_props)
        self.trk_x.setPrefix("X  "); self.trk_y.setPrefix("Y  ")
        xf.addRow(tr("位置"), paired_fields(self.trk_x, self.trk_y), reset_key="trk.pos")
        self.trk_scale = QDoubleSpinBox()
        self.trk_scale.setRange(0.01, 10.0)
        self.trk_scale.setSingleStep(0.01)
        self.trk_scale.setDecimals(2)
        self.trk_scale.editingFinished.connect(self._commit_track_props)
        self.trk_rotation = QDoubleSpinBox()
        self.trk_rotation.setRange(-360, 360)
        self.trk_rotation.setSingleStep(1.0)
        self.trk_rotation.editingFinished.connect(self._commit_track_props)
        self.trk_scale.setSuffix(" ×"); self.trk_rotation.setSuffix("°")
        xf.addRow(tr("缩放"), self.trk_scale, reset_key="trk.scale")
        xf.addRow(tr("旋转"), self.trk_rotation, reset_key="trk.rotation")
        self.trk_flip_x = QCheckBox(tr("水平翻转"))
        self.trk_flip_x.setMinimumWidth(0)
        self.trk_flip_x.toggled.connect(self._commit_track_props)
        self.trk_flip_y = QCheckBox(tr("垂直翻转"))
        self.trk_flip_y.setMinimumWidth(0)
        self.trk_flip_y.toggled.connect(self._commit_track_props)
        _fl = QHBoxLayout(); _fl.setSpacing(INSPECTOR_OPTION_GAP)
        _fl.addWidget(self.trk_flip_x); _fl.addWidget(self.trk_flip_y); _fl.addStretch(1)
        xf.addRow(tr("翻转"), _fl, reset_key="trk.flip")
        _xfw = QWidget()
        _xfw.setMinimumWidth(0)
        _xfw.setLayout(xf)
        sec_xform.addWidget(_xfw)

        sec_comp = CollapsibleSection("合成")
        cf = _mkform()
        self.trk_opacity = QDoubleSpinBox()
        self.trk_opacity.setRange(0.0, 1.0)
        self.trk_opacity.setSingleStep(0.01)
        self.trk_opacity.setDecimals(2)
        self.trk_opacity.editingFinished.connect(self._commit_track_props)
        cf.addRow(tr("不透明度"), self.trk_opacity, reset_key="trk.opacity")
        self.trk_blend = ValueCombo(VIDEO_BLEND_ITEMS)
        self.trk_blend.setToolTip(LUMA_MATTE_TIP)
        self.trk_blend.setMinimumWidth(0)
        self.trk_blend.valueChanged.connect(self._commit_track_props)
        cf.addRow(tr("混合模式"), self.trk_blend, reset_key="trk.blend")
        _cfw = QWidget()
        _cfw.setMinimumWidth(0)
        _cfw.setLayout(cf)
        sec_comp.addWidget(_cfw)

        sec_split = CollapsibleSection("分屏")
        self._sec_split = sec_split
        sf = _mkform()
        from cutora.ui.widgets.collapsible import labeled_toggle
        self.trk_split_host, self.trk_split_enable = labeled_toggle(tr("开启分屏"))
        self.trk_split_enable.setToolTip(tr("工程级：任一轨道开启，其他视频轨进入分屏，只选格子"))
        self.trk_split_enable.toggled.connect(self._on_split_enable)
        sf.addRow(tr("分屏"), self.trk_split_host, reset_key="trk.split_on")

        self.trk_split_layout_group = QButtonGroup(self)
        self.trk_split_layout_group.setExclusive(True)
        self.trk_split_layout_btns = {}
        lay_grid = QGridLayout()
        lay_grid.setContentsMargins(0, 0, 0, 0)
        lay_grid.setSpacing(INSPECTOR_FIELD_GAP)
        from cutora.render.split_layout import LAYOUT_LABELS, LAYOUTS
        for i, key in enumerate(LAYOUTS):
            btn = QToolButton()
            btn.setObjectName("chipBtn")
            btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            btn.setText(LAYOUT_LABELS.get(key, key))
            btn.setCheckable(True)
            from cutora.batch.split_assign import LAYOUT_HINTS
            btn.setToolTip(LAYOUT_HINTS.get(key, key))
            btn.setMinimumWidth(0)
            btn.setProperty("split_layout", key)
            self.trk_split_layout_group.addButton(btn, i)
            self.trk_split_layout_btns[key] = btn
            lay_grid.addWidget(btn, i // 4, i % 4)
        self.trk_split_layout_group.idClicked.connect(self._on_split_layout)
        lay_host = QWidget()
        lay_host.setMinimumWidth(0)
        _lv = QVBoxLayout(lay_host); _lv.setContentsMargins(0, 0, 0, 0); _lv.setSpacing(INSPECTOR_FIELD_GAP)
        _cap = QLabel(tr("布局")); _cap.setObjectName("inspectorLabel")
        _lv.addWidget(_cap); _lv.addLayout(lay_grid)

        # ✨ 自定义创意分屏工作台（支持随机创意、套圆圈气泡、任意斜切/波浪/弧线切刀）
        self.trk_custom_split_box = QWidget()
        self.trk_custom_split_box.setMinimumWidth(0)
        _csl = QVBoxLayout(self.trk_custom_split_box)
        _csl.setContentsMargins(6, 6, 6, 6)
        _csl.setSpacing(4)
        self.trk_custom_split_box.setStyleSheet(
            "QWidget { background: rgba(229,169,60,0.08); border: 1px solid rgba(229,169,60,0.34); border-radius: 8px; }"
            "QPushButton { background: #1D1912; color: #F2E3C6; border: 1px solid #4A3B22; border-radius: 5px; padding: 4px 6px; font-size: 11px; }"
            "QPushButton:hover { background: #2C2418; border-color: #E5A93C; color: #F5D082; }"
        )
        self.trk_custom_split_lbl = QLabel("✨ 创意自定义：主视频最大，画布可拖圆心 / 拖右下角方柄缩放圆圈与视频 / 拖侧柄变椭圆 / 拖顶柄旋外框")
        self.trk_custom_split_lbl.setWordWrap(True)
        self.trk_custom_split_lbl.setStyleSheet("color: #E5A93C; font-size: 11px; font-weight: 600; border: none; background: transparent;")
        _csl.addWidget(self.trk_custom_split_lbl)
        _cs_grid = QGridLayout()
        _cs_grid.setContentsMargins(0, 0, 0, 0)
        _cs_grid.setSpacing(4)
        _custom_presets = [
            ("🎲 随机换创意", "random", None, None),
            ("🔀 随机交换位置", "__shuffle__", None, None),
            ("✨ 上主下副直线", "straight_top", None, "straight"),
            ("✨ 下主上副直线", "straight_bottom", None, "straight"),
            ("📐 中主直线夹心", "center_h", None, "straight"),
            ("🎞️ 动感斜夹心", "center_h_tilt", None, "straight"),
            ("📐 L形·右上主", "l_left2_bottom3", None, "straight"),
            ("📐 反L·左上主", "l_right_bottom", None, "straight"),
            ("📐 顶L·右下主", "l_left_top", None, "straight"),
            ("⬡ 六角中主四角", "hex_center", None, "straight"),
            ("⭕ 底部套圆圈", "circle_bottom", None, "straight"),
            ("🔪 黄金斜切分屏", "slash_top", 8.0, "straight"),
        ]
        for _ci, (_ctitle, _cmode, _crot, _ccurve) in enumerate(_custom_presets):
            _cbtn = QPushButton(_ctitle)
            _cbtn.setCursor(Qt.CursorShape.PointingHandCursor)
            _cbtn.clicked.connect(
                lambda _=False, m=_cmode, r=_crot, c=_ccurve: self._apply_custom_split_recipe(m, r, c)
            )
            _cs_grid.addWidget(_cbtn, _ci // 4, _ci % 4)
        _csl.addLayout(_cs_grid)
        _lv.addWidget(self.trk_custom_split_box)
        sf.addSpanRow(lay_host, reset_key="trk.split_layout")

        self.trk_shuffle_split_btn = QPushButton("🔀 随机交换外部加载视频分屏位置")
        self.trk_shuffle_split_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.trk_shuffle_split_btn.setToolTip("保持主媒体视频不动，将各个外部加载视频在分屏小格子中的位置随机重新打乱交换")
        self.trk_shuffle_split_btn.setStyleSheet(
            "QPushButton { background: #1E293B; color: #38BDF8; border: 1px solid #0284C7; border-radius: 6px; padding: 5px 10px; font-weight: 600; }"
            "QPushButton:hover { background: #0F172A; border-color: #7DD3FC; color: #BAE6FD; }"
        )
        self.trk_shuffle_split_btn.clicked.connect(self._on_shuffle_secondary_tracks)
        sf.addSpanRow(self.trk_shuffle_split_btn)

        self.trk_split_cell = QComboBox()
        self.trk_split_cell.setMinimumWidth(0)
        self.trk_split_cell.currentIndexChanged.connect(self._on_split_cell)
        sf.addRow(tr("本轨格子"), self.trk_split_cell, reset_key="trk.split_cell")

        self.trk_split_div_label = QLabel(tr("分界线 / 异形与快门孔径参数"))
        self.trk_split_div_label.setObjectName("hint")
        sf.addRow(self.trk_split_div_label)
        self.trk_split_pos = QDoubleSpinBox()
        self.trk_split_pos.setRange(0.04, 0.96)
        self.trk_split_pos.setSingleStep(0.01)
        self.trk_split_pos.setDecimals(2)
        self.trk_split_pos.editingFinished.connect(self._commit_split_divider)
        self.trk_split_rot = QDoubleSpinBox()
        self.trk_split_rot.setRange(-180, 180)
        self.trk_split_rot.setSingleStep(2.0)
        self.trk_split_rot.setToolTip("旋转分界线（如横向分屏斜线）或异形/椭圆外框角度（画面内视频保持正向不旋转）")
        self.trk_split_rot.editingFinished.connect(self._commit_split_divider)
        self.trk_split_feather = QDoubleSpinBox()
        self.trk_split_feather.setRange(0, 200)
        self.trk_split_feather.setSingleStep(1.0)
        self.trk_split_feather.editingFinished.connect(self._commit_split_divider)
        self.trk_split_rot.setSuffix("°"); self.trk_split_feather.setSuffix(" px")
        sf.addRow(tr("分界位置"), self.trk_split_pos, reset_key="trk.split_pos")
        sf.addRow(tr("角度旋转"), self.trk_split_rot, reset_key="trk.split_rot")
        sf.addRow(tr("分界羽化"), self.trk_split_feather, reset_key="trk.split_feather")

        from cutora.render.split_layout import SPLIT_SHAPES, SHAPE_LABELS
        self.trk_split_shape = QComboBox()
        self.trk_split_shape.setMinimumWidth(0)
        for _sh_k in SPLIT_SHAPES:
            self.trk_split_shape.addItem(SHAPE_LABELS.get(_sh_k, _sh_k), _sh_k)
        self.trk_split_shape.setToolTip("选择圆圈或中央主屏的形状（光滑圆形、正六角形、正八角形、菱形等）")
        self.trk_split_shape.currentIndexChanged.connect(self._commit_split_divider)

        self.trk_split_poly_sides = QSpinBox()
        self.trk_split_poly_sides.setRange(3, 64)
        self.trk_split_poly_sides.setSingleStep(1)
        self.trk_split_poly_sides.setValue(6)
        self.trk_split_poly_sides.setSuffix(" 段 (64=光滑圆)")
        self.trk_split_poly_sides.setToolTip("控制多边形/六角形的分段数量（3=三角形，5=五边形，6=六角形，调高至 64 可平滑演变为光滑圆形）")
        self.trk_split_poly_sides.valueChanged.connect(self._commit_split_divider)

        self.trk_split_shutter_twist = QDoubleSpinBox()
        self.trk_split_shutter_twist.setRange(-75.0, 75.0)
        self.trk_split_shutter_twist.setSingleStep(3.0)
        self.trk_split_shutter_twist.setDecimals(1)
        self.trk_split_shutter_twist.setValue(0.0)
        self.trk_split_shutter_twist.setSuffix("°")
        self.trk_split_shutter_twist.setToolTip("相机快门叶片穿插倾斜角（0° 为正交四角，调大角度形成旋转快门叶片穿插感）")
        self.trk_split_shutter_twist.valueChanged.connect(self._commit_split_divider)

        self.trk_split_circle_r = QDoubleSpinBox()
        self.trk_split_circle_r.setRange(0.04, 0.48)
        self.trk_split_circle_r.setSingleStep(0.01)
        self.trk_split_circle_r.setDecimals(2)
        self.trk_split_circle_r.setValue(0.15)
        self.trk_split_circle_r.setToolTip("调整圆圈/多边形/快门孔径大小，内部视频将自动跟随整体放大缩小")
        self.trk_split_circle_r.valueChanged.connect(self._commit_split_divider)

        self.trk_split_circle_aspect = QDoubleSpinBox()
        self.trk_split_circle_aspect.setRange(0.30, 3.00)
        self.trk_split_circle_aspect.setSingleStep(0.05)
        self.trk_split_circle_aspect.setDecimals(2)
        self.trk_split_circle_aspect.setValue(1.00)
        self.trk_split_circle_aspect.setToolTip("宽高比：1.00 为正比，>1 为横向拉伸，<1 为竖向拉伸")
        self.trk_split_circle_aspect.valueChanged.connect(self._commit_split_divider)

        sf.addRow(tr("形状样式"), self.trk_split_shape)
        sf.addRow(tr("多边形分段"), self.trk_split_poly_sides)
        sf.addRow(tr("快门穿插角"), self.trk_split_shutter_twist)
        sf.addRow(tr("孔径/圆大小"), self.trk_split_circle_r)
        sf.addRow(tr("宽高比"), self.trk_split_circle_aspect)

        self.trk_split_border_label = QLabel(tr("描边（全局）"))
        self.trk_split_border_label.setObjectName("hint")
        sf.addRow(self.trk_split_border_label)
        self.trk_split_border_style = ValueCombo((
            ("none", "无"), ("solid", "实线"), ("gradient", "渐变"),
            ("glass", "玻璃"), ("shadow", "阴影")))
        self.trk_split_border_style.setMinimumWidth(0)
        self.trk_split_border_style.valueChanged.connect(self._commit_split_border)
        self.trk_split_border_width = QSpinBox()
        self.trk_split_border_width.setRange(0, 64)
        self.trk_split_border_width.editingFinished.connect(self._commit_split_border)
        self.trk_split_border_curve = ValueCombo((
            ("straight", "直线"), ("arc", "穹顶下弧"), ("arch_up", "苍穹上弧"), ("s_wave", "S流线弧"), ("wave", "波浪")))
        self.trk_split_border_curve.setMinimumWidth(0)
        self.trk_split_border_curve.valueChanged.connect(self._commit_split_border)
        self.trk_split_border_shadow = QSpinBox()
        self.trk_split_border_shadow.setRange(0, 64)
        self.trk_split_border_shadow.editingFinished.connect(self._commit_split_border)
        sf.addRow(tr("描边样式"), self.trk_split_border_style, reset_key="trk.split_border_style")
        sf.addRow(tr("描边宽度"), self.trk_split_border_width, reset_key="trk.split_border_width")
        sf.addRow(tr("描边曲线"), self.trk_split_border_curve, reset_key="trk.split_border_curve")
        sf.addRow(tr("描边阴影"), self.trk_split_border_shadow, reset_key="trk.split_border_shadow")
        # Q155：留白处垫什么 —— 每格各自（左右分屏 = 左右两块）/ 单个视频撑满整幅（不割裂）
        self.trk_split_backdrop = ValueCombo((
            ("each", "每格各自"), ("full", "单个视频撑满整幅")))
        self.trk_split_backdrop.setMinimumWidth(0)
        self.trk_split_backdrop.setToolTip(tr(
            "每格各自：每一格垫自己那条视频的背景；\n单个视频撑满整幅：整个画面只垫原视频的模糊，"
            "各格留白处都露出它。\n外部图片 / 外部视频背景在中央监视器右键「整幅背景」里批量设。"))
        self.trk_split_backdrop.valueChanged.connect(self._commit_split_backdrop)
        sf.addRow(tr("分屏背景"), self.trk_split_backdrop, reset_key="trk.split_backdrop")
        _sfw = QWidget()
        _sfw.setMinimumWidth(0)
        _sfw.setLayout(sf)
        sec_split.addWidget(_sfw)

        from cutora.ui.panels.grade import GradeEditor
        self.trk_grade = GradeEditor("轨道调色")
        self.trk_grade.changed.connect(self._on_trk_grade)
        self.trk_grade.scope_mode_changed.connect(self._on_scope_mode)
        self.prj_grade = GradeEditor("工程调色")
        self.prj_grade.changed.connect(self._on_prj_grade)
        self.prj_grade.scope_mode_changed.connect(self._on_scope_mode)

        # ✨ 字幕轨道专属：指定任意源轨道转录 + 手动粘贴完整字幕 + 自动转录 + 逐句检查纠正
        # 布局（自上而下）：源轨道 / 语言 → [⚡ 自动转录][■ 取消] + 进度 → 粘贴文案对齐（可折叠）
        # → 逐句校对表（开始 / 结束 / 文本）→ 底部小按钮。尺寸 / 间距 / 圆角全部取 theme tokens。
        from cutora.ui.theme import tokens as _tk
        from PySide6.QtWidgets import (
            QTableWidget, QHeaderView, QAbstractItemView, QProgressBar,
        )
        _btn_h = _tk.INSPECTOR_CONTROL_H + _tk.SPACE_QUARTER      # 30
        _small_h = _tk.INSPECTOR_CONTROL_H                         # 28
        self.sec_sub_track = CollapsibleSection("字幕轨道 · 智能转录与手动校对")
        self.sec_sub_track.setExpanded(True)
        _sub_box = QWidget()
        _sub_box.setObjectName("subTrackBox")
        _sub_box.setMinimumWidth(0)
        _sub_box.setStyleSheet(
            f"QWidget#subTrackBox QLabel#subTrkLbl {{ color: {_tk.FG1}; font-size: {_tk.FONT_LABEL}px; }}"
            f"QWidget#subTrackBox QLabel#subTrkHint {{ color: {_tk.FG2}; font-size: {_tk.FONT_MICRO}px; }}"
            f"QWidget#subTrackBox QPushButton {{ border-radius: {_tk.RADIUS_CTL}px;"
            f" padding: 0px {_tk.SPACE_3}px; font-size: {_tk.FONT_LABEL}px; }}"
            f"QWidget#subTrackBox QPushButton#subTrkPrimary {{ background: {_tk.ACCENT}; color: {_tk.BG0};"
            f" border: 1px solid {_tk.ACCENT_HI}; font-weight: {_tk.W_SEMI}; }}"
            f"QWidget#subTrackBox QPushButton#subTrkPrimary:hover {{ background: {_tk.ACCENT_HI}; }}"
            f"QWidget#subTrackBox QPushButton#subTrkPrimary:pressed {{ background: {_tk.ACCENT_DIM}; }}"
            f"QWidget#subTrackBox QPushButton#subTrkPrimary:disabled {{ background: {_tk.BG3};"
            f" color: {_tk.FG_MUTED}; border-color: {_tk.BORDER}; }}"
            f"QWidget#subTrackBox QPushButton#subTrkSecondary {{ background: {_tk.BG2}; color: {_tk.FG0};"
            f" border: 1px solid {_tk.BORDER}; }}"
            f"QWidget#subTrackBox QPushButton#subTrkSecondary:hover {{ background: {_tk.BG3};"
            f" border-color: {_tk.ACCENT}; }}"
            f"QWidget#subTrackBox QPushButton#subTrkSecondary:disabled {{ color: {_tk.FG_MUTED}; }}"
            f"QWidget#subTrackBox QProgressBar {{ background: {_tk.BG2}; border: 1px solid {_tk.LINE};"
            f" border-radius: {_tk.RADIUS_SM}px; }}"
            f"QWidget#subTrackBox QProgressBar::chunk {{ background: {_tk.ACCENT};"
            f" border-radius: {_tk.RADIUS_SM}px; }}"
            f"QWidget#subTrackBox QTableWidget {{ background: {_tk.BG1}; alternate-background-color: {_tk.BG2};"
            f" border: 1px solid {_tk.LINE}; border-radius: {_tk.RADIUS_CTL}px; gridline-color: {_tk.LINE}; }}"
        )
        _sub_v = QVBoxLayout(_sub_box)
        _sub_v.setContentsMargins(_tk.SPACE_2, _tk.SPACE_2, _tk.SPACE_2, _tk.SPACE_3)
        _sub_v.setSpacing(_tk.SPACE_2)

        def _sub_lbl(text, name="subTrkLbl"):
            lb = QLabel(tr(text))
            lb.setObjectName(name)
            return lb

        def _sub_btn(text, name, height, max_w=None, tip=""):
            b = QPushButton(tr(text))
            b.setObjectName(name)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setFixedHeight(height)
            b.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
            if max_w:
                b.setMaximumWidth(max_w)
            if tip:
                b.setToolTip(tr(tip))
            return b

        # ① 源轨道 + 语言
        _src_grid = QGridLayout()
        _src_grid.setContentsMargins(0, 0, 0, 0)
        _src_grid.setHorizontalSpacing(_tk.SPACE_2)
        _src_grid.setVerticalSpacing(_tk.SPACE_1H)
        self.trk_sub_source_combo = QComboBox()
        self.trk_sub_source_combo.setObjectName("subTrkSourceCombo")
        self.trk_sub_source_combo.setMinimumWidth(0)
        self.trk_sub_source_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.trk_sub_source_combo.setToolTip(
            "选择要对哪一条轨道生成字幕（默认是媒体库的主视频轨道，也可指定任意视频轨道或音频轨道）"
        )
        self.trk_sub_source_combo.currentIndexChanged.connect(self._on_trk_sub_source_changed)
        self.trk_sub_lang_combo = QComboBox()
        self.trk_sub_lang_combo.setObjectName("subTrkLangCombo")
        self.trk_sub_lang_combo.setMinimumWidth(0)
        for _code, _name in (
            ("auto", "自动检测"), ("zh", "中文"), ("en", "English"), ("ar", "العربية"),
            ("mg", "Malagasy"), ("sw", "Kiswahili"), ("ja", "日本語"), ("ko", "한국어"),
            ("es", "Español"), ("fr", "Français"), ("de", "Deutsch"), ("pt", "Português"),
            ("ru", "Русский"), ("it", "Italiano"),
        ):
            self.trk_sub_lang_combo.addItem(tr(_name), _code)
        self.trk_sub_lang_combo.setToolTip(tr("转录语言；默认自动检测（支持中 / 英 / 阿拉伯 / 马达加斯加 / 斯瓦希里等）"))
        _src_grid.addWidget(_sub_lbl("转录源"), 0, 0)
        _src_grid.addWidget(self.trk_sub_source_combo, 0, 1)
        _src_grid.addWidget(_sub_lbl("语言"), 1, 0)
        _src_grid.addWidget(self.trk_sub_lang_combo, 1, 1)
        _src_grid.setColumnStretch(1, 1)
        _sub_v.addLayout(_src_grid)

        # ② 主操作行：[⚡ 自动转录] [■ 取消] + 进度
        _act_row = QHBoxLayout()
        _act_row.setContentsMargins(0, 0, 0, 0)
        _act_row.setSpacing(_tk.SPACE_2)
        self.trk_sub_auto_asr_btn = _sub_btn(
            "⚡ 自动转录", "subTrkPrimary", _btn_h, max_w=_tk.SPACE_6 * 5,
            tip="无文案自动添加字幕：对上方指定的轨道执行语音转录（后台运行，不卡界面），"
                "生成带标点的字幕并写入本字幕轨道，可在下方表格逐句校对")
        self.trk_sub_auto_asr_btn.clicked.connect(self._on_trk_sub_auto_transcribe)
        self.trk_sub_cancel_btn = _sub_btn(
            "■ 取消", "subTrkSecondary", _btn_h, max_w=_tk.SPACE_6 * 3, tip="停止正在进行的转录")
        self.trk_sub_cancel_btn.setEnabled(False)
        self.trk_sub_cancel_btn.clicked.connect(self._on_trk_sub_cancel_transcribe)
        _act_row.addWidget(self.trk_sub_auto_asr_btn, 1)
        _act_row.addWidget(self.trk_sub_cancel_btn)
        _act_row.addStretch(0)
        _sub_v.addLayout(_act_row)

        self.trk_sub_progress = QProgressBar()
        self.trk_sub_progress.setRange(0, 100)
        self.trk_sub_progress.setValue(0)
        self.trk_sub_progress.setTextVisible(False)
        self.trk_sub_progress.setFixedHeight(_tk.SPACE_1H)
        self.trk_sub_progress.setVisible(False)
        _sub_v.addWidget(self.trk_sub_progress)
        self.trk_sub_status_lbl = _sub_lbl("", "subTrkHint")
        self.trk_sub_status_lbl.setWordWrap(True)
        self.trk_sub_status_lbl.setVisible(False)
        _sub_v.addWidget(self.trk_sub_status_lbl)

        # ③ 粘贴文案对齐（可折叠）
        self.sec_sub_paste = CollapsibleSection("粘贴文案对齐")
        self.sec_sub_paste.setExpanded(False)
        _paste_box = QWidget()
        _paste_box.setMinimumWidth(0)
        _paste_v = QVBoxLayout(_paste_box)
        _paste_v.setContentsMargins(0, _tk.SPACE_1, 0, _tk.SPACE_1)
        _paste_v.setSpacing(_tk.SPACE_1H)
        _paste_hint = _sub_lbl("粘贴准确完整的文案：逐句 / 整段纯文本（自动断句补标点并对齐到源轨道时间轴），"
                               "或 SRT / [00:01.20] 带时间戳字幕。", "subTrkHint")
        _paste_hint.setWordWrap(True)
        _paste_v.addWidget(_paste_hint)
        self.trk_sub_paste_edit = QPlainTextEdit()
        self.trk_sub_paste_edit.setPlaceholderText(
            "在此粘贴准确完整的字幕内容...\n"
            "• 每行一句或整段英文/中文文案（自动断句补齐标点并对齐到目标轨道时间轴）\n"
            "• 或直接粘贴 SRT / [00:01.20] 带时间戳字幕"
        )
        self.trk_sub_paste_edit.setMinimumHeight(_tk.SPACE_6 * 3)
        self.trk_sub_paste_edit.setMaximumHeight(_tk.SPACE_6 * 4)
        self.trk_sub_paste_edit.textChanged.connect(self._on_trk_sub_paste_text_changed)
        _paste_v.addWidget(self.trk_sub_paste_edit)
        _paste_row = QHBoxLayout()
        _paste_row.setContentsMargins(0, 0, 0, 0)
        _paste_row.addStretch(1)
        self.trk_sub_apply_paste_btn = _sub_btn(
            "对齐到本轨道", "subTrkSecondary", _small_h, max_w=_tk.SPACE_6 * 5,
            tip="将粘贴字幕生成 / 对齐到本字幕轨道")
        self.trk_sub_apply_paste_btn.clicked.connect(self._on_trk_sub_apply_paste)
        _paste_row.addWidget(self.trk_sub_apply_paste_btn)
        _paste_v.addLayout(_paste_row)
        self.sec_sub_paste.addWidget(_paste_box)
        _sub_v.addWidget(self.sec_sub_paste)

        # ④ 逐句校对表
        self.trk_sub_cue_count_lbl = _sub_lbl("逐句校对（双击单元格修改时间或文字）")
        _sub_v.addWidget(self.trk_sub_cue_count_lbl)
        self.trk_sub_cue_table = QTableWidget(0, 3)
        self.trk_sub_cue_table.setObjectName("subTrkCueTable")
        self.trk_sub_cue_table.setHorizontalHeaderLabels([tr("开始"), tr("结束"), tr("文本")])
        self.trk_sub_cue_table.verticalHeader().setVisible(False)
        self.trk_sub_cue_table.verticalHeader().setDefaultSectionSize(_tk.ROW_H)
        self.trk_sub_cue_table.setAlternatingRowColors(True)
        self.trk_sub_cue_table.setWordWrap(False)
        self.trk_sub_cue_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.trk_sub_cue_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.trk_sub_cue_table.setMinimumHeight(_tk.SPACE_6 * 6)
        _thdr = self.trk_sub_cue_table.horizontalHeader()
        _thdr.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        _thdr.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        _thdr.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        _thdr.setMinimumSectionSize(_tk.SPACE_6 + _tk.SPACE_4)
        self.trk_sub_cue_table.itemChanged.connect(self._on_trk_sub_cue_item_changed)
        self.trk_sub_cue_table.cellClicked.connect(self._on_trk_sub_cue_clicked)
        _sub_v.addWidget(self.trk_sub_cue_table)

        # ⑤ 底部小按钮
        _cue_btns = QHBoxLayout()
        _cue_btns.setContentsMargins(0, 0, 0, 0)
        _cue_btns.setSpacing(_tk.SPACE_1H)
        self.trk_sub_add_cue_btn = _sub_btn("＋ 新增", "subTrkSecondary", _small_h, tip="在播放头处新增一句")
        self.trk_sub_add_cue_btn.clicked.connect(self._on_trk_sub_add_cue)
        self.trk_sub_del_cue_btn = _sub_btn("－ 删除", "subTrkSecondary", _small_h, tip="删除选中的一句")
        self.trk_sub_del_cue_btn.clicked.connect(self._on_trk_sub_del_cue)
        self.trk_sub_style_btn = _sub_btn("🎨 样式", "subTrkSecondary", _small_h, tip="调整字幕样式")
        self.trk_sub_style_btn.clicked.connect(self._on_trk_sub_open_style)
        _cue_btns.addWidget(self.trk_sub_add_cue_btn)
        _cue_btns.addWidget(self.trk_sub_del_cue_btn)
        _cue_btns.addStretch(1)
        _cue_btns.addWidget(self.trk_sub_style_btn)
        _sub_v.addLayout(_cue_btns)

        self.sec_sub_track.addWidget(_sub_box)
        # 片段在时间线 / 画布 / 撤销里被改动时，表格跟着刷新（反向同步）
        self._trk_sub_worker = None
        self._trk_sub_job_track = None
        self._trk_sub_sig = None
        self._trk_sub_sync_timer = QTimer(self)
        self._trk_sub_sync_timer.setInterval(400)
        self._trk_sub_sync_timer.timeout.connect(self._sync_sub_cue_table_from_track)

        cl.addWidget(sec_id)
        cl.addWidget(self.sec_sub_track)
        cl.addWidget(sec_xform)
        cl.addWidget(sec_comp)
        cl.addWidget(sec_split)
        cl.addWidget(self.trk_grade)
        cl.addWidget(self.prj_grade)
        cl.addStretch()
        install_inspector_limits(container)
        scroll = QScrollArea()
        scroll.setWidget(container)
        scroll.setWidgetResizable(True)
        scroll.setMinimumWidth(0)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setObjectName("propTrackScroll")
        self.stack.addWidget(scroll)

    def eventFilter(self, obj, event):
        if obj is getattr(self, "ov_preset_combo", None):
            pass
            self._editing_preset_name = True
            return False
        return False

    def _sync_transition_rows(self, preset) -> None:
        """Show only the parameters the chosen transition actually reads."""
        if not hasattr(self, "trans_dir_cb"):
            return
        info = transition_param_info(str(preset or ""))
        self.trans_dir_cb.setVisible(info["direction"] is not None)
        self._trans_angle_host.setVisible(info["angle"])
        angle_row = inspector_row_of(self._trans_angle_host)
        if angle_row is not None:
            angle_row.setVisible(bool(info["angle"]))
        self.trans_zoom_start_sp.setVisible(info["zoom_start"])
        self.trans_motion_blur_sp.setVisible(info["motion_blur"])
        self.trans_intensity_sp.setVisible(info["intensity"])
        if hasattr(self, "trans_feather"):
            show_feather = bool(info.get("feather"))
            row = inspector_row_of(self.trans_feather)
            if row is not None:
                row.setVisible(show_feather)
            else:
                self.trans_feather.setVisible(show_feather)
        allowed = info["direction"] or ()
        view = self.trans_dir_cb.view()
        first = -1
        for i in range(self.trans_dir_cb.count()):
            ok = self.trans_dir_cb.itemData(i) in allowed
            view.setRowHidden(i, not ok)
            if ok and first < 0:
                first = i
        if allowed and self.trans_dir_cb.currentData() not in allowed and first >= 0:
            self.trans_dir_cb.blockSignals(True)
            self.trans_dir_cb.setCurrentIndex(first)
            self.trans_dir_cb.blockSignals(False)

    def on_change_transition(self, *_args):
        """转场 Tape 属性变化 → 写回 TransitionItem 对象。"""
        if self.block_signals or self._video_clip is None:
            return
        trans_obj = self._video_clip  # load_data 中将 trans_obj 赋给了 _video_clip
        if hasattr(self, "trans_preset_cb"):
            preset = self.trans_preset_cb.currentData()
            trans_obj.preset = preset if preset else "none"
            self._sync_transition_rows(trans_obj.preset)
        if hasattr(self, "trans_dur_sp"):
            new_dur = float(self.trans_dur_sp.value())
            dur_diff = new_dur - getattr(trans_obj, "duration", new_dur)
            trans_obj.start_t -= dur_diff
            trans_obj.duration = new_dur
        if hasattr(self, "trans_zoom_start_sp"):
            trans_obj.zoom_scale_start = float(self.trans_zoom_start_sp.value())
        if hasattr(self, "trans_motion_blur_sp"):
            trans_obj.motion_blur = float(self.trans_motion_blur_sp.value())
        if hasattr(self, "trans_intensity_sp"):
            trans_obj.intensity = float(self.trans_intensity_sp.value())
        if hasattr(self, "trans_easing_cb"):
            ease = self.trans_easing_cb.currentData()
            trans_obj.easing = str(ease or "ease_in_out")
        if hasattr(self, "trans_dir_cb"):
            direction = self.trans_dir_cb.currentData()
            trans_obj.direction = direction if direction else "left"
        if hasattr(self, "trans_angle_sp") and str(getattr(trans_obj, "preset", "") or "") == "wipe_angle":
            trans_obj.direction = float(self.trans_angle_sp.value())
        info = transition_param_info(str(getattr(trans_obj, "preset", "") or ""))
        if info.get("angle") and str(getattr(trans_obj, "preset", "") or "") != "wipe_angle":
            trans_obj.angle = float(self.trans_angle_sp.value())
        if info.get("feather") and hasattr(self, "trans_feather"):
            trans_obj.feather = float(self.trans_feather.value())

        # 触发重新渲染预览
        main_win = self.window()
        if hasattr(main_win, "video_canvas"):
            main_win.video_canvas.update()
        if hasattr(main_win, "timeline"):
            main_win.timeline.update()

        self.param_changed.emit(None)

    def _make_kf_watch(self, path, kind="clip"):
        watch = KeyframeWatch()
        watch.set_path(path)
        watch._kf_kind = str(kind or "clip")
        watch.clicked.connect(lambda _=False, w=watch: self._on_kf_watch(w))
        watch.hide()
        if not hasattr(self, "_kf_watches"):
            self._kf_watches = []
        self._kf_watches.append(watch)
        return watch

    # Q152-A：更多能打关键帧的参数。(控件属性名, 参数路径, 对象种类, 存储值 = 控件值 × 系数)
    # 求值：覆层走 keyframe.bake_host（所有打了关键帧的路径都烘进副本），片段的背景填充走
    # fill_bg.fill_spec_at。预览和导出同一条路。
    _KF_EXTRA = (
        ("fx_stroke_width_em", "stroke.width_em", "overlay", 1.0),
        ("fx_glow_strength", "glow.strength", "overlay", 1.0),
        ("fx_glow_sigma_em", "glow.sigma_em", "overlay", 1.0),
        ("fx_glow_spread_em", "glow.spread_em", "overlay", 1.0),
        ("fx_shadow_angle_deg", "shadow.angle_deg", "overlay", 1.0),
        ("fx_shadow_distance_em", "shadow.distance_em", "overlay", 1.0),
        ("fx_tracking_em", "tracking_em", "overlay", 1.0),
        ("vid_bg_blur", "bg_fill.blur", "clip", 1.0),
        ("vid_bg_dim", "bg_fill.dim", "clip", 0.01),
    )

    def _attach_extra_kf_watches(self):
        self._kf_extra = {}
        for attr, path, kind, factor in self._KF_EXTRA:
            widget = getattr(self, attr, None)
            if widget is None:
                continue
            try:
                watch = self._attach_row_watch(widget, path, kind=kind)
            except Exception:
                from cutora.core.log import get_logger
                get_logger("cutora.ui.panels.property").exception("关键帧表 %s 挂不上", path)
                continue
            self._kf_extra[path] = (widget, kind, float(factor), watch)
        # Q175：调整图层面板里能打关键帧的参数也挂秒表（同一套：开 / 关、播放头处的值、自动记关键帧）
        adj = getattr(self, "ov_adjust", None)
        for attr, path, factor in (getattr(adj, "KF_PATHS", None) or ()):
            widget = getattr(adj, attr, None)
            if widget is None:
                continue
            try:
                watch = self._attach_row_watch(widget, path, kind="overlay")
            except Exception:
                from cutora.core.log import get_logger
                get_logger("cutora.ui.panels.property").exception("调整图层关键帧 %s 挂不上", path)
                continue
            self._kf_extra[path] = (widget, "overlay", float(factor), watch)

    def _kf_extra_value(self, path):
        rec = (getattr(self, "_kf_extra", None) or {}).get(path)
        if rec is None:
            return None
        widget, _kind, factor, _w = rec
        try:
            return float(widget.value()) * factor
        except Exception:
            return None

    @staticmethod
    def _kf_widget_tol(widget, factor) -> float:
        """控件显示精度对应的容差：别因为四舍五入把没动过的参数也打一个关键帧。"""
        dec = 0
        for cand in (widget, getattr(widget, "spin", None)):
            fn = getattr(cand, "decimals", None)
            if callable(fn):
                try:
                    dec = int(fn())
                    break
                except Exception:
                    pass
        return max(1e-6, 0.51 * (10.0 ** -dec) * abs(float(factor) or 1.0))

    def _auto_key_extras(self, kind):
        """改完一个参数后：打开了关键帧、而且控件值和播放头处的值不一样的，记一个关键帧。"""
        from cutora.render.keyframe import path_enabled, read_path, value_at
        extra = getattr(self, "_kf_extra", None) or {}
        if not extra:
            return
        if kind == "clip":
            host = self._video_clip if getattr(self, "current_type", None) == "video_clip" else None
        else:
            data = getattr(self, "current_data", None)
            host = data if isinstance(data, dict) and getattr(self, "current_type", None) in (
                "overlay", "sticker") else None
        if host is None:
            return
        t_local = self._playhead_local(host)
        for path, (widget, k, factor, _w) in extra.items():
            if k != kind or not path_enabled(host, path):
                continue
            v = self._kf_extra_value(path)
            if v is None:
                continue
            fb = read_path(host, path)
            try:
                cur = float(value_at(host, path, t_local, fb if fb is not None else v))
            except (TypeError, ValueError):
                continue
            if abs(v - cur) > self._kf_widget_tol(widget, factor):
                self._auto_key(host, path, v)

    def _sync_extra_kf_boxes(self, host, kind, t_local):
        from cutora.render.keyframe import path_enabled, read_path, value_at
        for path, (widget, k, factor, _w) in (getattr(self, "_kf_extra", None) or {}).items():
            if k != kind or host is None or not path_enabled(host, path):
                continue
            fb = read_path(host, path)
            try:
                v = float(value_at(host, path, t_local, fb if fb is not None else 0.0))
            except (TypeError, ValueError):
                continue
            prev = widget.blockSignals(True)
            try:
                widget.setValue(v / (factor or 1.0))
            finally:
                widget.blockSignals(prev)

    def _fx_target(self, which):
        """LayerFxPanel 的取数：(对象, 命令定位器, 种类)。"""
        if which == "clip":
            if getattr(self, "current_type", None) != "video_clip":
                return None, None, None
            host = getattr(self, "_video_clip", None)
            if host is None or not hasattr(host, "source_id"):
                return None, None, None
            return host, self._kf_cmd_params(host), "clip"
        if getattr(self, "current_type", None) != "overlay":
            return None, None, None
        data = getattr(self, "current_data", None)
        if not isinstance(data, dict):
            return None, None, None
        params = self._kf_cmd_params(data)
        if not params:
            return None, None, None
        kind = "image" if data.get("type") == "image" else "text"
        return data, params, kind

    def _matte_context(self):
        """AI 抠像面板要的：源视频路径、播放头对应的源时间、时间线对象。"""
        host, _params, kind = self._fx_target("clip")
        if host is None or kind != "clip":
            return {}
        win, task = self._win_and_task()
        tl = task.get("timeline") if isinstance(task, dict) else None
        path = None
        src = None
        sid = getattr(host, "source_id", None)
        if tl is not None and sid:
            src = (getattr(tl, "sources", {}) or {}).get(sid)
            if src is not None:
                path = getattr(src, "path", None) or (src.get("path") if isinstance(src, dict) else None)
        src_t = 0.0
        canvas = getattr(win, "video_canvas", None)
        fn = getattr(canvas, "_clip_source_sec", None)
        if callable(fn):
            try:
                src_t = float(fn(host, tl))
            except Exception:
                src_t = 0.0
        else:
            try:
                from cutora.render.transition_runtime import source_duration_of, source_time_for_clip
                t = float(getattr(win, "_timeline_ms", 0) or 0) / 1000.0
                st, _fz = source_time_for_clip(host, t, source_duration_of(host, source=src), extend=False)
                src_t = float(st or 0.0)
            except Exception:
                src_t = 0.0
        return {"path": path, "src_t": src_t, "tl": tl, "task": task, "layer": host}

    def fx_panel(self):
        """当前对象对应的那份遮罩 / 抠像面板（画布用）。"""
        if getattr(self, "current_type", None) == "video_clip":
            return getattr(self, "vid_fx", None)
        if getattr(self, "current_type", None) == "overlay":
            return getattr(self, "ov_fx", None)
        return None

    def _on_fx_canvas_action(self, action, arg):
        win = self.window()
        canvas = getattr(win, "video_canvas", None)
        fn = getattr(canvas, "fx_canvas_action", None)
        if callable(fn):
            fn(action, arg, self.sender())
        else:
            self.fx_action = (action, arg)        # 没有画布（测试）就记着

    def _attach_row_watch(self, widget, path, kind="clip"):
        row = inspector_row_of(widget)
        if row is None:
            return self._make_kf_watch(path, kind=kind)
        watch = row.attach_keyframe(path)
        watch._kf_kind = str(kind or "clip")
        watch.clicked.connect(lambda _=False, w=watch: self._on_kf_watch(w))
        watch.hide()
        if not hasattr(self, "_kf_watches"):
            self._kf_watches = []
        self._kf_watches.append(watch)
        return watch

    def _kf_host(self):
        ctype = getattr(self, "current_type", None)
        if ctype == "audio":
            return getattr(self, "current_data", None)
        if ctype in ("overlay", "sticker"):
            data = getattr(self, "current_data", None)
            return data if isinstance(data, dict) else None
        if ctype == "video_clip":
            return getattr(self, "_video_clip", None)
        return None

    def _kf_scale_host(self):
        if getattr(self, "current_type", None) == "sticker":
            parent = getattr(self, "_ov_parent_overlay", None)
            if isinstance(parent, dict):
                return parent
        data = getattr(self, "current_data", None)
        return data if isinstance(data, dict) else None

    def _watch_host(self, watch):
        kind = getattr(watch, "_kf_kind", "clip")
        ctype = getattr(self, "current_type", None)
        if kind == "audio":
            return self.current_data if ctype == "audio" else None
        if kind == "clip":
            return self._video_clip if ctype == "video_clip" else None
        if kind != "overlay" or ctype not in ("overlay", "sticker"):
            return None
        if watch.path() == "scale":
            return self._kf_scale_host()
        data = getattr(self, "current_data", None)
        return data if isinstance(data, dict) else None

    def _overlay_kf_value(self, path):
        extra = self._kf_extra_value(path)
        if extra is not None:
            return extra
        if path == "x":
            return int(self.ov_x.value())
        if path == "y":
            return int(self.ov_y.value())
        if path == "rotation":
            return int(self.ov_rot.value())
        if path == "opacity":
            return int(self.ov_op.value())
        if path == "scale":
            data = self.current_data if isinstance(getattr(self, "current_data", None), dict) else {}
            if getattr(self, "current_type", None) == "overlay" and data.get("type") == "text":
                return float(self.ov_tscale.value())
            return float(self.ov_scale.value())
        return 0

    def _ov_kf_path_for_sender(self):
        sender = self.sender()
        if sender is None:
            return None
        groups = (
            ("x", ("ov_x", "ov_x_sl")),
            ("y", ("ov_y", "ov_y_sl")),
            ("rotation", ("ov_rot", "ov_rot_sl")),
            ("opacity", ("ov_op", "ov_op_sl")),
            ("scale", ("ov_tscale", "ov_tscale_sl", "ov_scale", "ov_scale_sl")),
        )
        for path, names in groups:
            for name in names:
                widget = getattr(self, name, None)
                if widget is None:
                    continue
                if sender is widget or sender is widget.parent():
                    return path
        return None

    def _current_task(self):
        win = self.window()
        if win is None:
            return None
        tasks = getattr(win, "tasks", None) or []
        ci = getattr(win, "curr_idx", -1)
        if 0 <= ci < len(tasks):
            task = tasks[ci]
            return task if isinstance(task, dict) else None
        return None

    def _kf_cmd_params(self, host):
        if host is None:
            return None
        if getattr(self, "current_type", None) == "audio" or getattr(self, "_video_clip", None) is host:
            track = getattr(self, "_video_track", None)
            clip = host
            idx = 0
            tid = ""
            win = self.window()
            task = None
            if win is not None:
                tasks = getattr(win, "tasks", None) or []
                ci = getattr(win, "curr_idx", -1)
                if 0 <= ci < len(tasks):
                    task = tasks[ci]
            tl = task.get("timeline") if isinstance(task, dict) else None
            tracks = getattr(tl, "tracks", None) if tl is not None else []
            for tr in tracks or []:
                clips = getattr(tr, "clips", []) or []
                for i, c in enumerate(clips):
                    if c is clip:
                        track = tr
                        idx = i
                        break
            if track is not None:
                tid = str(getattr(track, "id", "") or "")
            return {"target": "clip", "track_id": tid, "clip_index": idx}
        if isinstance(host, dict):
            from cutora.render.keyframe import locate_overlay
            return locate_overlay(self._current_task(), host)
        return None

    def _playhead_local(self, host):
        from cutora.render.keyframe import local_time
        win = self.window()
        tlw = getattr(win, "timeline", None) if win is not None else None
        t = float(getattr(tlw, "position", 0) or 0) / 1000.0 if tlw is not None else 0.0
        return local_time(host, t)

    def _on_kf_watch(self, watch):
        from cutora.core.commands import get_bus
        from cutora.core.log import get_logger
        from cutora.render.keyframe import path_enabled, read_path
        host = self._watch_host(watch)
        if host is None:
            return
        path = watch.path()
        on = not path_enabled(host, path)
        params = self._kf_cmd_params(host)
        if not params:
            return
        val = read_path(host, path)
        if self._kf_extra_value(path) is not None:
            val = self._kf_extra_value(path)
        elif getattr(watch, "_kf_kind", "") == "overlay":
            val = self._overlay_kf_value(path)
        elif path == "gain_db" and hasattr(self, "audio_vol"):
            vol = float(self.audio_vol.value())
            val = 20.0 * math.log10(max(vol, 1e-6)) if vol > 0 else -120.0
        elif path == "x":
            val = float(self.vid_x.value())
        elif path == "y":
            val = float(self.vid_y.value())
        elif path == "scale":
            val = float(self.vid_scale.value())
        elif path == "rotation":
            val = float(self.vid_rotation.value())
        elif path == "opacity":
            val = int(self.vid_opacity.value())
        payload = dict(params)
        payload.update({
            "path": path,
            "enabled": on,
            "t": self._playhead_local(host),
            "v": val,
        })
        try:
            get_bus().execute("keyframe.enable", payload, source="ui")
        except Exception as exc:
            get_logger("cutora.ui.panels.property").exception("keyframe.enable 失败")
            win = self.window()
            bar = getattr(win, "statusBar", None) if win is not None else None
            if callable(bar):
                bar().showMessage("关键帧失败：%s" % exc, 4000)
        self._refresh_kf_watches()
        win = self.window()
        tlw = getattr(win, "timeline", None) if win is not None else None
        if tlw is not None:
            tlw.update()

    def _auto_key(self, host, path, value):
        from cutora.core.commands import get_bus
        from cutora.core.log import get_logger
        from cutora.render.keyframe import path_enabled, set_point
        if host is None or not path_enabled(host, path):
            return
        params = self._kf_cmd_params(host)
        if not params:
            return
        now = time.monotonic()
        coal_key = (id(host), str(path))
        coal = getattr(self, "_kf_coalesce", None)
        if (
            coal is not None
            and coal.get("key") == coal_key
            and (now - float(coal.get("t") or 0.0)) < 0.6
        ):
            set_point(host, path, self._playhead_local(host), value, ease="linear")
            win = self.window()
            cap = getattr(win, "capture_undo_after", None) if win is not None else None
            if callable(cap):
                cap()
            coal["t"] = now
            return
        payload = dict(params)
        payload.update({
            "path": path,
            "t": self._playhead_local(host),
            "v": value,
            "ease": "linear",
        })
        try:
            get_bus().execute("keyframe.set", payload, source="ui")
            self._kf_coalesce = {"key": coal_key, "t": now}
        except Exception as exc:
            get_logger("cutora.ui.panels.property").exception("keyframe.set 失败")
            win = self.window()
            bar = getattr(win, "statusBar", None) if win is not None else None
            if callable(bar):
                bar().showMessage("关键帧失败：%s" % exc, 4000)

    def _ensure_playhead_hook(self):
        win = self.window()
        tlw = getattr(win, "timeline", None) if win is not None else None
        if tlw is None or not hasattr(tlw, "position_changed"):
            return
        prev = getattr(self, "_kf_playhead_hooked", None)
        if prev is tlw:
            return
        if prev is not None:
            try:
                prev.position_changed.disconnect(self._refresh_kf_watches)
            except Exception:
                pass
        tlw.position_changed.connect(self._refresh_kf_watches)
        self._kf_playhead_hooked = tlw

    def _set_spin_silent(self, widget, value):
        if widget is None:
            return
        parent = widget.parent()
        w_prev = widget.blockSignals(True)
        p_prev = parent.blockSignals(True) if parent is not None else False
        try:
            widget.setValue(value)
        finally:
            widget.blockSignals(w_prev)
            if parent is not None:
                parent.blockSignals(p_prev)

    def _sync_kf_value_boxes(self, host, t_local):
        from cutora.render.keyframe import path_enabled, value_at
        if host is None:
            return
        ctype = getattr(self, "current_type", None)
        if ctype == "video_clip" or getattr(self, "_video_clip", None) is host:
            pairs = (
                ("x", getattr(self, "vid_x", None), float(getattr(host, "x", 0.0))),
                ("y", getattr(self, "vid_y", None), float(getattr(host, "y", 0.0))),
                ("scale", getattr(self, "vid_scale", None), float(getattr(host, "scale", 1.0))),
                ("rotation", getattr(self, "vid_rotation", None), float(getattr(host, "rotation", 0.0))),
            )
            for path, widget, fb in pairs:
                if widget is None or not path_enabled(host, path):
                    continue
                self._set_spin_silent(widget, float(value_at(host, path, t_local, fb)))
            if hasattr(self, "vid_opacity") and path_enabled(host, "opacity"):
                fb = float(getattr(host, "opacity", 255))
                self._set_spin_silent(
                    self.vid_opacity,
                    int(round(float(value_at(host, "opacity", t_local, fb)))),
                )
            self._sync_extra_kf_boxes(host, "clip", t_local)
        elif ctype == "audio":
            if hasattr(self, "audio_vol") and path_enabled(host, "gain_db"):
                fb = float(getattr(host, "gain_db", 0.0))
                gain = float(value_at(host, "gain_db", t_local, fb))
                vol = 10.0 ** (gain / 20.0) if gain != 0.0 else 1.0
                self._set_spin_silent(self.audio_vol, vol)
        elif ctype in ("overlay", "sticker"):
            self._sync_ov_kf_boxes()

    def _sync_ov_kf_boxes(self):
        from cutora.render.keyframe import path_enabled, value_at
        data = self.current_data if isinstance(getattr(self, "current_data", None), dict) else None
        if data is None:
            return
        t_local = self._playhead_local(data)
        pairs = (
            ("x", getattr(self, "ov_x", None), int(data.get("x", 0) or 0)),
            ("y", getattr(self, "ov_y", None), int(data.get("y", 0) or 0)),
            ("rotation", getattr(self, "ov_rot", None), int(data.get("rotation", 0) or 0)),
            ("opacity", getattr(self, "ov_op", None), int(data.get("opacity", 255) or 0)),
        )
        for path, widget, fb in pairs:
            if widget is None or not path_enabled(data, path):
                continue
            self._set_spin_silent(widget, int(round(float(value_at(data, path, t_local, fb)))))
        self._sync_extra_kf_boxes(data, "overlay", t_local)
        scale_host = self._kf_scale_host()
        text = getattr(self, "current_type", None) == "overlay" and data.get("type") == "text"
        widget = getattr(self, "ov_tscale", None) if text else getattr(self, "ov_scale", None)
        if widget is None or scale_host is None or not path_enabled(scale_host, "scale"):
            return
        fb = scale_host.get("scale", 1.0)
        try:
            fb = float(fb if fb is not None else 1.0)
        except (TypeError, ValueError):
            fb = 1.0
        t_scale = self._playhead_local(scale_host)
        self._set_spin_silent(widget, float(value_at(scale_host, "scale", t_scale, fb)))

    def _refresh_kf_watches(self):
        from cutora.render.keyframe import hits_at, path_enabled
        self._ensure_playhead_hook()
        watches = getattr(self, "_kf_watches", None) or []
        seen = {}
        for w in watches:
            host = self._watch_host(w)
            if host is None:
                w.hide()
                continue
            w.show()
            key = id(host)
            if key not in seen:
                seen[key] = self._playhead_local(host)
            t_local = seen[key]
            on = path_enabled(host, w.path())
            w.set_state(on, hits_at(host, w.path(), t_local) if on else False)
        host = self._kf_host()
        if host is not None:
            self._sync_kf_value_boxes(host, self._playhead_local(host))

    def _video_kf_path_for_sender(self):
        s = self.sender()
        if s is None:
            return None
        if s is getattr(self, "vid_x", None):
            return "x"
        if s is getattr(self, "vid_y", None):
            return "y"
        if s is getattr(self, "vid_scale", None):
            return "scale"
        if s is getattr(self, "vid_rotation", None):
            return "rotation"
        op = getattr(self, "vid_opacity", None)
        sl = getattr(self, "vid_opacity_sl", None)
        if s is op or s is sl:
            return "opacity"
        if op is not None and s is op.parent():
            return "opacity"
        return None

    def _on_vid_bg_fill(self, *_args):
        """Q117：背景填充改了 → 写回 clip.bg_fill，并按模式收起用不上的行。"""
        if self.block_signals or self._video_clip is None:
            self._sync_bg_fill_rows()
            return
        from cutora.render.fill_bg import set_fill
        self._auto_key_extras("clip")
        set_fill(
            self._video_clip,
            mode=self.vid_bg_mode.currentData(),
            blur=float(self.vid_bg_blur.value()),
            blur_style=self.vid_bg_style.currentData() or "lens",
            highlights=float(self.vid_bg_hl.value()) / 100.0,
            dim=float(self.vid_bg_dim.value()) / 100.0,
            scale=float(self.vid_bg_scale.value()),
            x=float(self.vid_bg_x.value()),
            y=float(self.vid_bg_y.value()),
            rotation=float(self.vid_bg_rot.value()),
        )
        self._sync_bg_fill_rows()
        # 和其它视频属性一样走 param_changed（Q117 写成了 self.changed —— 这个
        # 面板根本没有那个信号，结果整段 ↺ 一碰就炸，后面的行全没重置）
        self.param_changed.emit(None)

    def _sync_bg_fill_rows(self):
        """无填充时只留模式那一行；不是模糊就不显示模糊程度；若画面已铺满则提示零损耗跳过。"""
        mode = self.vid_bg_mode.currentData() if hasattr(self, "vid_bg_mode") else "none"
        on = mode not in (None, "", "none")
        for w in (self.vid_bg_dim, self.vid_bg_dim_sl, self.vid_bg_scale,
                  self.vid_bg_x, self.vid_bg_y, self.vid_bg_rot):
            w.setEnabled(on)
        blur_on = on and mode == "blur"
        self.vid_bg_blur.setEnabled(blur_on)
        self.vid_bg_blur_sl.setEnabled(blur_on)
        self.vid_bg_style.setEnabled(blur_on)
        lens_on = blur_on and (self.vid_bg_style.currentData() or "lens") == "lens"
        self.vid_bg_hl.setEnabled(lens_on)
        self.vid_bg_hl_sl.setEnabled(lens_on)
        lbl = getattr(self, "vid_bg_status_lbl", None)
        if lbl is not None:
            clip = getattr(self, "_video_clip", None)
            sc = float(getattr(clip, "scale", 1.0) or 1.0) if clip is not None else 1.0
            fmode = str(getattr(clip, "fit_mode", "fill") or "fill").lower() if clip is not None else "fill"
            if not on:
                lbl.setText("已关闭背景填充")
            elif fmode in ("fill", "stretch") and sc >= 0.999:
                lbl.setText("✓ 当前画面已完全铺满，渲染时自动跳过背景模糊计算（0 资源占用）")
            else:
                lbl.setText("仅在画面留有边距时计算背景填充")

    def _load_bg_fill(self, clip):
        from cutora.render.fill_bg import fill_spec
        spec = fill_spec(clip)
        self.vid_bg_mode.setCurrentText(spec["mode"])
        self.vid_bg_blur.setValue(int(round(spec["blur"])))
        _i = self.vid_bg_style.findData(spec["blur_style"])
        self.vid_bg_style.setCurrentIndex(_i if _i >= 0 else 0)
        self.vid_bg_hl.setValue(int(round(spec["highlights"] * 100.0)))
        self.vid_bg_dim.setValue(int(round(spec["dim"] * 100.0)))
        self.vid_bg_scale.setValue(float(spec["scale"]))
        self.vid_bg_x.setValue(float(spec["x"]))
        self.vid_bg_y.setValue(float(spec["y"]))
        self.vid_bg_rot.setValue(float(spec["rotation"]))
        self._sync_bg_fill_rows()

    def _sync_linked_video_speed(self, clip):
        try:
            from cutora.model.av_link import sync_linked_constant_speed
            win = self.window()
            tl = None
            if win is not None:
                idx = getattr(win, "curr_idx", -1)
                tasks = getattr(win, "tasks", None) or []
                if isinstance(idx, int) and 0 <= idx < len(tasks):
                    task = tasks[idx]
                    tl = task.get("timeline") if isinstance(task, dict) else getattr(task, "timeline", None)
            sync_linked_constant_speed(tl, clip)
        except Exception:
            from cutora.core.log import get_logger
            get_logger("cutora.ui.panels.property").exception("sync linked speed failed")

    def on_change_video(self, *_args):
        """视频 Clip 属性变化 → 写回 Clip 对象。"""
        if self.block_signals or self._video_clip is None:
            return
        clip = self._video_clip
        clip.fit_mode = self.vid_fit.currentText()
        path = self._video_kf_path_for_sender()
        if path == "x":
            self._auto_key(clip, "x", self.vid_x.value())
        elif path == "y":
            self._auto_key(clip, "y", self.vid_y.value())
        elif path == "scale":
            self._auto_key(clip, "scale", self.vid_scale.value())
        elif path == "rotation":
            self._auto_key(clip, "rotation", self.vid_rotation.value())
        elif path == "opacity":
            self._auto_key(
                clip, "opacity",
                int(self.vid_opacity.value()) if hasattr(self, "vid_opacity")
                else int(getattr(clip, "opacity", 255)),
            )
        clip.x = self.vid_x.value()
        clip.y = self.vid_y.value()
        clip.scale = self.vid_scale.value()
        clip.rotation = self.vid_rotation.value()
        clip.flip_x = self.vid_flip_x.isChecked()
        clip.flip_y = self.vid_flip_y.isChecked()
        clip.opacity = int(self.vid_opacity.value()) if hasattr(self, "vid_opacity") else int(getattr(clip, "opacity", 255))
        clip.speed = self.vid_speed.value()
        self._sync_linked_video_speed(clip)
        clip.loop = self.vid_loop.isChecked()
        clip.match_duration = self.vid_match_dur.isChecked()
        if hasattr(self, "vid_alpha_mode"):
            mode = self.vid_alpha_mode.currentData() or "straight"
            clip.alpha_mode = mode
            win = self.window()
            tl = None
            if win is not None:
                try:
                    idx = getattr(win, "curr_idx", -1)
                    tasks = getattr(win, "tasks", None) or []
                    if 0 <= idx < len(tasks):
                        task = tasks[idx]
                        if isinstance(task, dict):
                            tl = task.get("timeline")
                        else:
                            tl = getattr(task, "timeline", None)
                except Exception:
                    tl = None
            src = None
            if tl is not None:
                sources = getattr(tl, "sources", None) or {}
                src = sources.get(getattr(clip, "source_id", None))
            if src is not None:
                if isinstance(src, dict):
                    src["alpha_mode"] = mode
                    spath = src.get("path")
                else:
                    src.alpha_mode = mode
                    spath = getattr(src, "path", None)
                if spath:
                    try:
                        from cutora.render.readers import set_path_alpha_mode
                        set_path_alpha_mode(spath, mode)
                    except Exception:
                        from cutora.core.log import get_logger
                        get_logger("cutora.ui.panels.property").exception(
                            "set_path_alpha_mode failed")
        clip.crop_left = int(self.vid_crop_l.value()) if hasattr(self, "vid_crop_l") else int(getattr(clip, "crop_left", 0))
        clip.crop_right = int(self.vid_crop_r.value()) if hasattr(self, "vid_crop_r") else int(getattr(clip, "crop_right", 0))
        clip.crop_top = int(self.vid_crop_t.value()) if hasattr(self, "vid_crop_t") else int(getattr(clip, "crop_top", 0))
        clip.crop_bottom = int(self.vid_crop_b.value()) if hasattr(self, "vid_crop_b") else int(getattr(clip, "crop_bottom", 0))
        clip.feather = int(self.vid_feather.value()) if hasattr(self, "vid_feather") else int(getattr(clip, "feather", 0))
        from cutora.render.textfx.fade import write_fade
        write_fade(
            clip, *self._fade_from_widgets("vid"),
            offsets=self._fade_offsets_from_widgets("vid"))
        if hasattr(self, "vid_transition"):
            preset = self.vid_transition.currentData()
            clip.transition_preset = preset if preset else "none"
            clip.transition_duration = float(self.vid_transition_dur.value()) if hasattr(self, "vid_transition_dur") else anim_engine.DEFAULT_TRANSITION_DURATION
            if not hasattr(clip, "transition_duration"):
                clip.transition_duration = anim_engine.DEFAULT_TRANSITION_DURATION
        # 循环 + 匹配时长：目标 = 时间线结尾 − 本片段起点（不用播放器时长）
        if clip.match_duration:
            from cutora.model.match_span import match_target_sec
            main_win = self.window()
            task = None
            if main_win is not None:
                try:
                    idx = getattr(main_win, "curr_idx", -1)
                    tasks = getattr(main_win, "tasks", None) or []
                    if 0 <= idx < len(tasks):
                        task = tasks[idx]
                except Exception:
                    task = None
            clip._match_target_dur = match_target_sec(task, clip) if task is not None else 0.0
        else:
            clip._match_target_dur = 0.0
        self.param_changed.emit(None)

    def _on_scope_mode(self, mode):
        win = self.window()
        canvas = getattr(win, "video_canvas", None) if win is not None else None
        setter = getattr(canvas, "set_scope_mode", None) if canvas is not None else None
        if callable(setter):
            setter(mode)

    def _exec_grade(self, target, patch, extra=None):
        from cutora.core.commands import get_bus
        from cutora.core.log import get_logger
        payload = {"target": target, "patch": patch}
        if extra:
            payload.update(extra)
        try:
            get_bus().execute("grade.set", payload, source="ui")
        except Exception as exc:
            get_logger("cutora.ui.panels.property").exception("grade.set 失败 target=%s", target)
            win = self.window()
            bar = getattr(win, "statusBar", None) if win is not None else None
            if callable(bar):
                bar().showMessage("调色失败：%s" % exc, 4000)

    def _current_task(self):
        win = self.window()
        if win is None:
            return None
        try:
            idx = getattr(win, "curr_idx", -1)
            tasks = getattr(win, "tasks", None) or []
            if 0 <= idx < len(tasks):
                return tasks[idx]
        except Exception:
            return None
        return None

    def _on_vid_matte(self, *_args):
        """子蒙版：只挖上一条视频。混合模式里的「作为蒙版」仍是整幅挖空，这里不碰它。"""
        if self.block_signals or self._video_clip is None:
            return
        clip = self._video_clip
        track = self._video_track
        on = bool(self.vid_matte.isChecked())
        inv = bool(self.vid_matte_inv.isChecked()) and on
        self.vid_matte_inv.setEnabled(on)
        if not on:
            self.vid_matte_inv.blockSignals(True)
            self.vid_matte_inv.setChecked(False)
            self.vid_matte_inv.blockSignals(False)
            if track is not None:
                from cutora.render.child_matte import set_track_matte
                set_track_matte(track, "", False)
            else:
                clip.matte_for = ""
                clip.matte_invert = False
            self.param_changed.emit(None)
            return
        from cutora.render.child_matte import set_track_matte, video_above
        parent = video_above(self._current_task(), track) if track is not None else None
        if parent is None:
            self.vid_matte.blockSignals(True)
            self.vid_matte.setChecked(False)
            self.vid_matte.blockSignals(False)
            self.vid_matte_inv.setEnabled(False)
            win = self.window()
            bar = getattr(win, "statusBar", None) if win is not None else None
            if callable(bar):
                bar().showMessage("上面没有视频可以挖", 3000)
            return
        set_track_matte(track, str(getattr(parent, "id", "") or ""), inv)
        self.param_changed.emit(None)

    def _image_grade_host(self):
        if self.current_type == "overlay" and isinstance(self.current_data, dict):
            if self.current_data.get("type") == "image":
                return self.current_data
            return None
        if self.current_type == "sticker" and isinstance(self.current_data, dict):
            if self.current_data.get("kind") != "image":
                return None
            parent = getattr(self, "_ov_parent_overlay", None)
            if isinstance(parent, dict) and parent:
                return parent
            return self.current_data
        return None

    def _show_image_grade(self, host):
        btn_rest = getattr(self, "btn_ov_rmbg_restore", None)
        if btn_rest is not None:
            try:
                from cutora.ui.ai_studio_room import has_rmbg_original
                btn_rest.setVisible(bool(isinstance(host, dict) and has_rmbg_original(host)))
            except Exception:
                btn_rest.setVisible(False)
        ed = getattr(self, "ov_grade", None)
        if ed is None:
            return
        if not isinstance(host, dict):
            ed.hide()
            return
        ed.show()
        ed.load_grade(host.get("grade"))

    def _on_ov_rmbg_cutout(self):
        host = self._image_grade_host()
        win = self.window()
        try:
            from cutora.ui.ai_studio_room import run_rmbg_cutout_ui
            run_rmbg_cutout_ui(win, target_overlay=host)
            self._show_image_grade(host)
            if getattr(self, "ov_fx", None) is not None:
                self.ov_fx.reload()
        except Exception as e:
            _LOG.exception("图片属性一键抠图失败: %s", e)

    def _on_ov_rmbg_restore(self):
        host = self._image_grade_host()
        win = self.window()
        if not isinstance(host, dict):
            return
        try:
            from cutora.ui.ai_studio_room import restore_rmbg_original_ui
            restore_rmbg_original_ui(win, host)
            self._show_image_grade(host)
            if getattr(self, "ov_fx", None) is not None:
                self.ov_fx.reload()
        except Exception as e:
            _LOG.exception("图片属性还原抠图失败: %s", e)

    def _on_ov_grade(self):
        if self.block_signals:
            return
        host = self._image_grade_host()
        if not isinstance(host, dict):
            return
        host["grade"] = self.ov_grade.grade()
        self.param_changed.emit(None)

    def _on_vid_grade(self):
        if self.block_signals or self._video_clip is None:
            return
        clip = self._video_clip
        track = self._video_track
        extra = {}
        if track is not None:
            extra["track_id"] = str(getattr(track, "id", "") or "")
            clips = list(getattr(track, "clips", None) or [])
            try:
                extra["clip_index"] = clips.index(clip)
            except ValueError:
                extra["clip_index"] = 0
        self._exec_grade("clip", self.vid_grade.grade(), extra)
        self.param_changed.emit(None)

    def _on_trk_grade(self):
        if self.block_signals or self._track_obj is None:
            return
        self._exec_grade("track", self.trk_grade.grade(), {
            "track_id": str(getattr(self._track_obj, "id", "") or ""),
        })
        self.param_changed.emit(None)

    def _on_prj_grade(self):
        if self.block_signals:
            return
        self._exec_grade("project", self.prj_grade.grade())
        self.param_changed.emit(None)

    def _on_adjust_changed(self, _key=None) -> None:
        """Q174：调整图层面板自己记了撤销（改之前拍照、改完补「之后」），这里只刷新 ——
        不能再让 update_preview 补一条改完以后的「操作」（Ctrl+Z 第一下会没反应）。"""
        win = self.window()
        if win is None or not hasattr(win, "update_preview"):
            self.param_changed.emit(None)
            return
        prev_flush = getattr(win, "_preview_flushing", False)
        prev_pushed = getattr(win, "_canvas_gesture_pushed", False)
        win._preview_flushing = True
        win._canvas_gesture_pushed = True
        try:
            self.param_changed.emit(None)
        finally:
            win._preview_flushing = prev_flush
            win._canvas_gesture_pushed = prev_pushed
        try:
            win.timeline.update()            # 时间线上的名字 / 图标（纯色 ↔ 效果）跟着变
        except Exception:
            pass
        try:
            self._refresh_kf_watches()       # 关键帧菱形（这一帧有没有关键帧）
        except Exception:
            pass

    def _resolve_reset_key(self, key: str) -> None:
        from cutora.ui.panels.prop_reset import resolve_reset_key
        resolve_reset_key(self, key)

    def _open_reset_undo(self, label="重置") -> bool:
        """Snapshot once. Nested row resets inside a section reuse this entry."""
        if getattr(self, "_reset_undo_open", False):
            return False
        self._reset_undo_open = True
        win = self.window()
        push = getattr(win, "push_undo", None) if win is not None else None
        if not callable(push):
            self._reset_undo_owned = False
            return False
        push(str(label or "重置"))
        win._undo_hold = int(getattr(win, "_undo_hold", 0) or 0) + 1
        self._reset_undo_owned = True
        return True

    def _close_reset_undo(self) -> None:
        if not getattr(self, "_reset_undo_open", False):
            return
        owned = bool(getattr(self, "_reset_undo_owned", False))
        self._reset_undo_open = False
        self._reset_undo_owned = False
        if not owned:
            return
        win = self.window()
        if win is None:
            return
        win._undo_hold = max(0, int(getattr(win, "_undo_hold", 0) or 0) - 1)
        cap = getattr(win, "capture_undo_after", None)
        if callable(cap):
            cap()

    def _begin_section_reset(self, _sec) -> None:
        self._section_reset_depth = int(getattr(self, "_section_reset_depth", 0) or 0) + 1
        if self._section_reset_depth == 1:
            self._open_reset_undo("重置本段")

    def _end_section_reset(self, _sec) -> None:
        self._section_reset_depth = max(
            0, int(getattr(self, "_section_reset_depth", 1) or 1) - 1)
        if self._section_reset_depth == 0:
            self._close_reset_undo()

    def _reset_header_toggle(self, sec) -> None:
        from cutora.ui.panels.prop_reset import reset_header_toggle
        reset_header_toggle(self, sec)

    def _extra_section_reset(self, sec) -> None:
        from cutora.ui.panels.prop_reset import extra_section_reset
        extra_section_reset(self, sec)

    def _install_section_resets(self) -> None:
        from cutora.ui.widgets.collapsible import CollapsibleSection
        for sec in self.findChildren(CollapsibleSection):
            sec.begin_reset = self._begin_section_reset
            sec.end_reset = self._end_section_reset
            sec.reset_toggle = self._reset_header_toggle
            sec.extra_reset = self._extra_section_reset

    def fx_reset_value(self, path: str):
        """Text-layer fx reset uses the creation overlay, not an empty style."""
        from cutora.model.factory_defaults import text_overlay_fx_defaults
        from cutora.ui.panels.fx_fields import _MISSING
        from cutora.ui.panels.fx_schema import nested_get
        if getattr(self, "current_type", None) != "overlay":
            return _MISSING
        ov = self.current_data
        if not isinstance(ov, dict) or ov.get("type") != "text":
            return _MISSING
        fx = text_overlay_fx_defaults()
        cur = fx
        for part in str(path or "").split("."):
            if not isinstance(cur, dict) or part not in cur:
                return _MISSING
            cur = cur[part]
        return nested_get(fx, path)

    def _reset_video_pos(self):
        if self._video_clip is None:
            return
        self._resolve_reset_key("vid.pos")

    def _reset_video_scale(self):
        if self._video_clip is None:
            return
        self._resolve_reset_key("vid.scale")

    def _reset_video_rotation(self):
        if self._video_clip is None:
            return
        self._resolve_reset_key("vid.rotation")

    def on_change_video_blend(self, blend_name):
        """混合模式变化 → 写到当前 Clip。

        The inspector shows the track blend when a track is selected, so the
        same edit has to land there too or the next load snaps back.
        """
        if self.block_signals or self._video_clip is None:
            return
        self._video_clip.blend_mode = blend_name
        track = getattr(self, "_video_track", None)
        if track is not None:
            track.blend_mode = blend_name
            props = getattr(track, "props", None)
            if isinstance(props, dict):
                props["blend_mode"] = str(blend_name or "normal")
        self.param_changed.emit(None)

    def _load_track_widgets(self, track):
        from cutora.render.track_transform import (
            edit_track_ids, track_is_session_pinned, track_props_of,
        )
        from cutora.ui.i18n import tr
        prev_block = self.block_signals
        self.block_signals = True
        try:
            props = track_props_of(track)
            name = str(getattr(track, "name", "") or "")
            self.trk_name.setText(name)
            win = self.window()
            tlw = getattr(win, "timeline", None) if win is not None else None
            pinned = track_is_session_pinned(tlw, track) if tlw is not None else False
            self.trk_pin.setChecked(pinned)
            ids = edit_track_ids(tlw, track) if tlw is not None else [
                str(getattr(track, "id", "") or "")]
            n = len([i for i in ids if i])
            if pinned and n >= 1:
                self.trk_banner.setText(tr("正在编辑 {n} 条轨道", n=n))
                self.trk_banner.show()
            else:
                self.trk_banner.hide()
            self.trk_x.setValue(float(props["x"]))
            self.trk_y.setValue(float(props["y"]))
            self.trk_scale.setValue(float(props["scale"]))
            self.trk_rotation.setValue(float(props["rotation"]))
            self.trk_flip_x.setChecked(bool(props["flip_x"]))
            self.trk_flip_y.setChecked(bool(props["flip_y"]))
            self.trk_opacity.setValue(float(props["opacity"]))
            blend_items = VIDEO_BLEND_ITEMS if getattr(track, "type", "") == "video" else BLEND_ITEMS
            self.trk_blend.set_items(blend_items)
            idx = self.trk_blend.findText(str(props.get("blend_mode") or "normal"))
            self.trk_blend.setCurrentIndex(idx if idx >= 0 else 0)
            self._load_split_widgets(track)
            self._load_sub_track_widgets(track)
            if hasattr(self, "trk_grade"):
                self.trk_grade.load_grade(getattr(track, "grade", None))
            if hasattr(self, "prj_grade"):
                _win, task = self._win_and_task()
                self.prj_grade.load_grade(
                    (task or {}).get("grade") if isinstance(task, dict) else None)
        finally:
            self.block_signals = prev_block

    def _load_sub_track_widgets(self, track) -> None:
        if not hasattr(self, "sec_sub_track"):
            return
        is_subs = getattr(track, "type", "") in ("subs", "subtitle")
        self.sec_sub_track.setVisible(bool(is_subs))
        timer = getattr(self, "_trk_sub_sync_timer", None)
        if not is_subs:
            if timer is not None:
                timer.stop()
            return
        self.sec_sub_track.setExpanded(True)
        from cutora.subtitle.track_source import (
            get_track_manual_script,
            get_track_segments,
            get_track_source_id,
        )
        from cutora.subtitle.track_transcribe import (
            MAIN_SOURCE_ID,
            list_transcribe_source_tracks,
        )
        _win, task = self._win_and_task()
        src_items = list_transcribe_source_tracks(task)
        cur_src = get_track_source_id(track)
        self.trk_sub_source_combo.blockSignals(True)
        self.trk_sub_source_combo.clear()
        for sid, label in src_items:
            self.trk_sub_source_combo.addItem(label, sid)
        idx_s = self.trk_sub_source_combo.findData(cur_src)
        self.trk_sub_source_combo.setCurrentIndex(idx_s if idx_s >= 0 else 0)
        self.trk_sub_source_combo.blockSignals(False)

        segs = get_track_segments(task, track)
        manual_txt = get_track_manual_script(track)
        if hasattr(self, "sec_sub_paste"):
            # 用户粘贴过文案 → 默认展开；否则折叠，让表格占主要空间
            self.sec_sub_paste.setExpanded(bool(manual_txt.strip()) and not segs)
        if not manual_txt.strip() and segs:
            manual_txt = "\n".join(str(s.get("text") or "").strip() for s in segs if str(s.get("text") or "").strip())
        self.trk_sub_paste_edit.blockSignals(True)
        self.trk_sub_paste_edit.setPlainText(manual_txt)
        self.trk_sub_paste_edit.blockSignals(False)
        self._refresh_sub_cue_table(segs)
        self._sync_trk_sub_busy_ui()
        if timer is not None and not timer.isActive():
            timer.start()

    @staticmethod
    def _sub_cue_signature(segs) -> tuple:
        return tuple(
            (round(float(s.get("start") or 0.0), 3), round(float(s.get("end") or 0.0), 3),
             str(s.get("text") or ""))
            for s in (segs or []) if isinstance(s, dict)
        )

    def _refresh_sub_cue_table(self, segs: list[dict]) -> None:
        if not hasattr(self, "trk_sub_cue_table"):
            return
        from PySide6.QtWidgets import QTableWidgetItem
        self.trk_sub_cue_table.blockSignals(True)
        try:
            self.trk_sub_cue_table.setRowCount(len(segs))
            for r, seg in enumerate(segs):
                s_val = f"{float(seg.get('start') or 0.0):.2f}"
                e_val = f"{float(seg.get('end') or 0.0):.2f}"
                t_val = str(seg.get("text") or "")
                it_s = QTableWidgetItem(s_val)
                it_e = QTableWidgetItem(e_val)
                it_t = QTableWidgetItem(t_val)
                it_t.setToolTip(t_val)
                self.trk_sub_cue_table.setItem(r, 0, it_s)
                self.trk_sub_cue_table.setItem(r, 1, it_e)
                self.trk_sub_cue_table.setItem(r, 2, it_t)
            if hasattr(self, "trk_sub_cue_count_lbl"):
                self.trk_sub_cue_count_lbl.setText(
                    f"逐句校对 · 共 {len(segs)} 句（双击单元格修改时间或文字）"
                )
        finally:
            self.trk_sub_cue_table.blockSignals(False)
        self._trk_sub_sig = self._sub_cue_signature(segs)

    def _sync_sub_cue_table_from_track(self) -> None:
        """Timer slot: clips changed elsewhere (timeline drag, canvas edit, undo) → refresh table."""
        if (self._track_obj is None or not hasattr(self, "sec_sub_track")
                or self.sec_sub_track.isHidden()
                or getattr(self._track_obj, "type", "") not in ("subs", "subtitle")):
            return
        table = getattr(self, "trk_sub_cue_table", None)
        if table is None or table.state() == table.State.EditingState:
            return
        _win, task = self._win_and_task()
        from cutora.subtitle.track_source import get_track_segments
        segs = get_track_segments(task, self._track_obj)
        if self._sub_cue_signature(segs) == self._trk_sub_sig:
            return
        row = table.currentRow()
        self._refresh_sub_cue_table(segs)
        if 0 <= row < table.rowCount():
            table.selectRow(row)

    def _notify_sub_track_changed(self, msg: str = "") -> None:
        win, _task = self._win_and_task()
        if win is not None:
            if hasattr(win, "timeline") and win.timeline is not None:
                win.timeline.update()
            if hasattr(win, "timeline_container") and win.timeline_container is not None:
                win.timeline_container.track_header.update()
            win._sync_idle_task = None
            if hasattr(win, "video_canvas") and win.video_canvas is not None:
                win.video_canvas.update()
            if hasattr(win, "sync_ui") and callable(win.sync_ui):
                try:
                    win.sync_ui()
                except Exception:
                    pass
            if hasattr(win, "mark_dirty") and callable(win.mark_dirty):
                win.mark_dirty()
            if msg and hasattr(win, "statusBar") and callable(win.statusBar):
                win.statusBar().showMessage(msg, 4500)
        prev_flush = getattr(win, "_preview_flushing", False) if win is not None else False
        prev_pushed = getattr(win, "_canvas_gesture_pushed", False) if win is not None else False
        if win is not None:
            win._preview_flushing = True
            win._canvas_gesture_pushed = True
        try:
            self.param_changed.emit(None)
        finally:
            if win is not None:
                win._preview_flushing = prev_flush
                win._canvas_gesture_pushed = prev_pushed

    def _on_trk_sub_source_changed(self, *_args) -> None:
        if self.block_signals or self._track_obj is None:
            return
        from cutora.subtitle.track_source import set_track_source_id
        sid = str(self.trk_sub_source_combo.currentData() or "__main__")
        set_track_source_id(self._track_obj, sid)
        lbl = self.trk_sub_source_combo.currentText()
        self._notify_sub_track_changed(f"已指定字幕生成源轨道：{lbl}")

    def _on_trk_sub_paste_text_changed(self) -> None:
        if self.block_signals or self._track_obj is None:
            return
        from cutora.subtitle.track_source import set_track_manual_script
        set_track_manual_script(self._track_obj, self.trk_sub_paste_edit.toPlainText())

    # ── 自动转录（后台线程；GUI 线程只负责读 / 写时间线） ──
    def _trk_sub_busy(self) -> bool:
        w = getattr(self, "_trk_sub_worker", None)
        return w is not None and w.isRunning()

    def _sync_trk_sub_busy_ui(self) -> None:
        if not hasattr(self, "trk_sub_auto_asr_btn"):
            return
        busy = self._trk_sub_busy()
        self.trk_sub_auto_asr_btn.setEnabled(not busy)
        self.trk_sub_auto_asr_btn.setText("⏳ 转录中…" if busy else "⚡ 自动转录")
        self.trk_sub_cancel_btn.setEnabled(busy)
        self.trk_sub_source_combo.setEnabled(not busy)
        self.trk_sub_lang_combo.setEnabled(not busy)
        self.trk_sub_progress.setVisible(busy)

    def _trk_sub_set_status(self, text: str) -> None:
        lbl = getattr(self, "trk_sub_status_lbl", None)
        if lbl is None:
            return
        lbl.setText(text or "")
        lbl.setVisible(bool(text))

    def _on_trk_sub_auto_transcribe(self, *_args) -> None:
        if self._track_obj is None:
            return
        if self._trk_sub_busy():          # 重入保护：转录中再点无效
            return
        win, task = self._win_and_task()
        if not isinstance(task, dict):
            return
        from cutora.subtitle.track_source import set_track_source_id
        from cutora.subtitle.track_transcribe import (
            resolve_source_track_spans,
            title_locate_for,
        )
        from cutora.ui.panels.sub_track_worker import SubTrackTranscribeWorker
        sid = str(self.trk_sub_source_combo.currentData() or "__main__")
        src_label = self.trk_sub_source_combo.currentText()
        lang = str(self.trk_sub_lang_combo.currentData() or "auto")
        set_track_source_id(self._track_obj, sid)
        spans = resolve_source_track_spans(task, sid)       # 读时间线：GUI 线程
        if not spans:
            self._trk_sub_set_status("所选轨道上没有可转录的音频 / 视频片段")
            return
        track = self._track_obj
        worker = SubTrackTranscribeWorker(
            spans,
            language=lang,
            sub_track_id=str(getattr(track, "id", "") or ""),
            title_locate=title_locate_for(task, sid),
        )
        self._trk_sub_worker = worker
        self._trk_sub_job_track = track
        self._trk_sub_job_task = task
        self._trk_sub_job_label = src_label
        worker.progress.connect(self._on_trk_sub_worker_progress)
        worker.done.connect(self._on_trk_sub_worker_done)
        worker.failed.connect(self._on_trk_sub_worker_failed)
        worker.cancelled.connect(self._on_trk_sub_worker_cancelled)
        worker.finished.connect(self._on_trk_sub_worker_finished)
        try:
            from PySide6.QtWidgets import QApplication
            app = QApplication.instance()
            if app is not None:
                app.aboutToQuit.connect(worker.cancel)
        except Exception:
            pass
        self.trk_sub_progress.setValue(0)
        self._trk_sub_set_status("⏳ 正在转录所选轨道音频，请稍候…（可继续编辑，或点「取消」）")
        worker.start()
        self._sync_trk_sub_busy_ui()

    def _on_trk_sub_cancel_transcribe(self, *_args) -> None:
        w = getattr(self, "_trk_sub_worker", None)
        if w is None or not w.isRunning():
            return
        w.cancel()
        self.trk_sub_cancel_btn.setEnabled(False)
        self._trk_sub_set_status("正在取消…")

    def _on_trk_sub_worker_progress(self, pct: int, msg: str) -> None:
        if self.sender() is not getattr(self, "_trk_sub_worker", None):
            return
        self.trk_sub_progress.setValue(max(0, min(100, int(pct))))
        if msg:
            self._trk_sub_set_status(f"⏳ {msg}")

    def _on_trk_sub_worker_done(self, payload) -> None:
        if self.sender() is not getattr(self, "_trk_sub_worker", None):
            return
        segs, words, detected = payload
        track = getattr(self, "_trk_sub_job_track", None)
        task = getattr(self, "_trk_sub_job_task", None)
        win, cur_task = self._win_and_task()
        if track is None or not isinstance(task, dict):
            return
        tl = task.get("timeline")
        if tl is not None and track not in list(getattr(tl, "tracks", None) or []):
            self._trk_sub_set_status("字幕轨道已被删除，转录结果已丢弃")
            return
        from cutora.subtitle.track_transcribe import apply_track_transcript
        same_task = cur_task is task
        push = getattr(win, "push_undo", None) if (win is not None and same_task) else None
        if callable(push):
            push("自动转录轨道字幕")
        segs = apply_track_transcript(task, track, segs, words)
        cap = getattr(win, "capture_undo_after", None) if (win is not None and same_task) else None
        if callable(cap):
            cap()
        if self._track_obj is track:
            self._load_sub_track_widgets(track)
        label = getattr(self, "_trk_sub_job_label", "")
        lang_note = f"，语言：{detected}" if detected else ""
        self._trk_sub_set_status(f"✓ 已生成 {len(segs)} 句字幕{lang_note}")
        self._notify_sub_track_changed(
            f"🎙️ 已完成「{label}」字幕转录（共 {len(segs)} 句），可在下方表格手动检查纠正"
        )

    def _on_trk_sub_worker_failed(self, msg: str) -> None:
        if self.sender() is not getattr(self, "_trk_sub_worker", None):
            return
        self._trk_sub_set_status(f"⚠ 转录失败：{msg}")
        win, _task = self._win_and_task()
        if win is not None and hasattr(win, "statusBar") and callable(win.statusBar):
            win.statusBar().showMessage(f"字幕轨道转录失败：{msg}", 8000)

    def _on_trk_sub_worker_cancelled(self) -> None:
        if self.sender() is not getattr(self, "_trk_sub_worker", None):
            return
        self._trk_sub_set_status("已取消转录")

    def _on_trk_sub_worker_finished(self) -> None:
        w = self.sender()
        if w is getattr(self, "_trk_sub_worker", None):
            self._trk_sub_worker = None
            self._trk_sub_job_track = None
            self._trk_sub_job_task = None
        try:
            from PySide6.QtWidgets import QApplication
            app = QApplication.instance()
            if app is not None and w is not None:
                app.aboutToQuit.disconnect(w.cancel)
        except Exception:
            pass
        if w is not None:
            w.deleteLater()
        self._sync_trk_sub_busy_ui()

    def _on_trk_sub_apply_paste(self, *_args) -> None:
        if self._track_obj is None:
            return
        win, task = self._win_and_task()
        if not isinstance(task, dict):
            return
        from cutora.subtitle.track_transcribe import build_segments_from_manual_text
        raw_text = self.trk_sub_paste_edit.toPlainText()
        sid = str(self.trk_sub_source_combo.currentData() or "__main__")
        push = getattr(win, "push_undo", None) if win is not None else None
        if callable(push):
            push("粘贴并生成轨道字幕")
        segs = build_segments_from_manual_text(
            raw_text,
            task=task,
            sub_track=self._track_obj,
            source_track_id=sid,
        )
        cap = getattr(win, "capture_undo_after", None) if win is not None else None
        if callable(cap):
            cap()
        self._refresh_sub_cue_table(segs)
        self._notify_sub_track_changed(
            f"⚡ 已将粘贴的字幕生成到本轨道（共 {len(segs)} 句），可在下方表格手动检查纠正"
        )

    def _on_trk_sub_cue_item_changed(self, item) -> None:
        if self.block_signals or self._track_obj is None or item is None:
            return
        win, task = self._win_and_task()
        if not isinstance(task, dict):
            return
        from cutora.subtitle.track_source import (
            get_track_segments,
            set_track_manual_script,
            set_track_segments,
        )
        segs = [dict(s) for s in get_track_segments(task, self._track_obj)]
        row = item.row()
        if row < 0 or row >= len(segs):
            return
        it_s = self.trk_sub_cue_table.item(row, 0)
        it_e = self.trk_sub_cue_table.item(row, 1)
        it_t = self.trk_sub_cue_table.item(row, 2)
        try:
            s_val = max(0.0, float(it_s.text() if it_s else segs[row].get("start", 0.0)))
        except (TypeError, ValueError):
            s_val = float(segs[row].get("start") or 0.0)
        try:
            e_val = max(s_val + 0.1, float(it_e.text() if it_e else segs[row].get("end", s_val + 1.0)))
        except (TypeError, ValueError):
            e_val = max(s_val + 0.1, float(segs[row].get("end") or (s_val + 1.0)))
        t_val = str(it_t.text() if it_t else segs[row].get("text", "")).strip()

        push = getattr(win, "push_undo", None) if win is not None else None
        if callable(push):
            push("修改轨道字幕")
        segs[row]["start"] = round(s_val, 3)
        segs[row]["end"] = round(e_val, 3)
        segs[row]["text"] = t_val
        set_track_segments(task, self._track_obj, segs)
        set_track_manual_script(
            self._track_obj,
            "\n".join(str(s.get("text") or "") for s in segs if str(s.get("text") or "").strip()),
        )
        self.trk_sub_paste_edit.blockSignals(True)
        self.trk_sub_paste_edit.setPlainText(
            "\n".join(str(s.get("text") or "") for s in segs if str(s.get("text") or "").strip())
        )
        self.trk_sub_paste_edit.blockSignals(False)
        self._trk_sub_sig = self._sub_cue_signature(segs)
        cap = getattr(win, "capture_undo_after", None) if win is not None else None
        if callable(cap):
            cap()
        self._notify_sub_track_changed("✏️ 已保存字幕修改")

    def _on_trk_sub_cue_clicked(self, row: int, _col: int) -> None:
        if self._track_obj is None:
            return
        win, task = self._win_and_task()
        if not isinstance(task, dict):
            return
        from cutora.subtitle.track_source import get_track_segments
        segs = get_track_segments(task, self._track_obj)
        if row < 0 or row >= len(segs):
            return
        seg = segs[row]
        t_ms = int(round((float(seg.get("start") or 0.0) + 0.05) * 1000.0))
        if win is not None and hasattr(win, "timeline") and win.timeline is not None:
            win.timeline.seek_request.emit(t_ms)

    def _on_trk_sub_add_cue(self, *_args) -> None:
        if self._track_obj is None:
            return
        win, task = self._win_and_task()
        if not isinstance(task, dict):
            return
        from cutora.subtitle.track_source import get_track_segments, set_track_segments
        segs = [dict(s) for s in get_track_segments(task, self._track_obj)]
        playhead_s = 0.0
        if win is not None and hasattr(win, "timeline") and win.timeline is not None:
            playhead_s = float(getattr(win.timeline, "position", 0) or 0) / 1000.0
        start_s = round(segs[-1]["end"] if segs and playhead_s <= 0 else playhead_s, 3)
        end_s = round(start_s + 2.0, 3)
        tid = str(getattr(self._track_obj, "id", "") or "")
        push = getattr(win, "push_undo", None) if win is not None else None
        if callable(push):
            push("新增一句字幕")
        segs.append({
            "id": f"sub_{tid[:6]}_{len(segs) + 1}",
            "start": start_s,
            "end": end_s,
            "text": "新字幕内容.",
            "words": [],
            "_track_id": tid,
        })
        set_track_segments(task, self._track_obj, segs)
        cap = getattr(win, "capture_undo_after", None) if win is not None else None
        if callable(cap):
            cap()
        self._load_sub_track_widgets(self._track_obj)
        self._notify_sub_track_changed("＋ 已新增一句字幕")

    def _on_trk_sub_del_cue(self, *_args) -> None:
        if self._track_obj is None:
            return
        win, task = self._win_and_task()
        if not isinstance(task, dict):
            return
        row = self.trk_sub_cue_table.currentRow()
        from cutora.subtitle.track_source import get_track_segments, set_track_segments
        segs = [dict(s) for s in get_track_segments(task, self._track_obj)]
        if row < 0 or row >= len(segs):
            return
        push = getattr(win, "push_undo", None) if win is not None else None
        if callable(push):
            push("删除一句字幕")
        segs.pop(row)
        set_track_segments(task, self._track_obj, segs)
        cap = getattr(win, "capture_undo_after", None) if win is not None else None
        if callable(cap):
            cap()
        self._load_sub_track_widgets(self._track_obj)
        self._notify_sub_track_changed("－ 已删除选中的字幕")

    def _on_trk_sub_open_style(self, *_args) -> None:
        win, _task = self._win_and_task()
        if win is not None and hasattr(win, "right_tabs") and hasattr(win, "sub_panel"):
            idx = win.right_tabs.indexOf(win.sub_panel)
            if idx >= 0:
                win.right_tabs.setCurrentIndex(idx)

    def _av_link_status_text(self, clip) -> str:
        """Q204a：组大小 == 1 当未链接。"""
        from cutora.model.av_link import index_link_groups, partners
        if clip is None or not getattr(clip, "link_group_id", None):
            return "未链接"
        _win, task = self._win_and_task()
        tl = task.get("timeline") if isinstance(task, dict) else None
        if tl is None:
            return "未链接"
        if partners(index_link_groups(tl), clip):
            return "已链接"
        return "未链接"

    def _win_and_task(self):
        """(窗口, 当前任务)。Q152 修：以前也叫 _current_task，同名覆盖了上面那个只返回任务的版本，
        结果文本 / 图片层的关键帧 ⏱ 找不到对象（定位器是 None），点了没反应。"""
        win = self.window()
        if win is None:
            return None, None
        tasks = getattr(win, "tasks", None) or []
        idx = getattr(win, "curr_idx", -1)
        if idx < 0 or idx >= len(tasks):
            return win, None
        return win, tasks[idx]

    def _load_split_widgets(self, track):
        from cutora.render.split_layout import (
            occupied_cells, normalize_split,
        )
        from cutora.ui.i18n import tr
        win, task = self._win_and_task()
        sp = normalize_split((task or {}).get("split") if isinstance(task, dict) else None)
        on = bool(sp.get("enabled")) if task is not None else False
        self.trk_split_enable.setChecked(on)
        layout = str(sp.get("layout") or "top_bottom")
        btn_layout_key = layout
        if btn_layout_key not in self.trk_split_layout_btns:
            if btn_layout_key.startswith("center_h_arc"):
                btn_layout_key = "center_h_arc"
            elif btn_layout_key.startswith("center_h_embrace"):
                btn_layout_key = "center_h_embrace"
            elif btn_layout_key.startswith("center_h_swave"):
                btn_layout_key = "center_h_swave"
            elif btn_layout_key.startswith("center_h_tilt"):
                btn_layout_key = "center_h_tilt"
            elif btn_layout_key.startswith("center_h"):
                btn_layout_key = "center_h"
            elif btn_layout_key.startswith("l_right") and "_bottom" in btn_layout_key:
                btn_layout_key = "l_right1_bottom2"
            elif btn_layout_key.startswith("l_left") and "_top" in btn_layout_key:
                btn_layout_key = "l_left1_top2"
            elif btn_layout_key.startswith("l_left"):
                btn_layout_key = "l_left2_bottom3"
            elif btn_layout_key.startswith("hex_center") or btn_layout_key.startswith("poly_center"):
                btn_layout_key = "hex_center_4"
            elif btn_layout_key.startswith("shutter"):
                btn_layout_key = "shutter_4"
        for key, btn in self.trk_split_layout_btns.items():
            btn.setChecked(key == btn_layout_key)
        if hasattr(self, "trk_custom_split_box"):
            is_custom = (layout == "custom") or str(sp.get("custom_kind") or "") != ""
            self.trk_custom_split_box.setVisible(True)
            ckind = str(sp.get("custom_kind") or "")
            if is_custom and ckind:
                self.trk_custom_split_lbl.setText(f"✨ 当前创意：{ckind}（画布拖圆心/切线移动，Alt+拖拽改圆圈大小）")
            else:
                self.trk_custom_split_lbl.setText("✨ 创意自定义：按时间线视频数自动排布（主视频最大，支持套圆圈/斜切/波浪）")
        occ = occupied_cells(task) if task is not None else {}
        my_id = str(getattr(track, "id", "") or "")
        my_cell = getattr(track, "split_cell", None)
        self.trk_split_cell.blockSignals(True)
        try:
            self.trk_split_cell.clear()
            self.trk_split_cell.addItem(tr("不参与（全屏最上）"), None)
            for c in sp.get("cells") or []:
                cid = str(c.get("id"))
                owner = occ.get(cid)
                if owner is not None and str(getattr(owner, "id", "")) != my_id:
                    name = str(getattr(owner, "name", "") or "") or str(getattr(owner, "id", ""))
                    self.trk_split_cell.addItem("%s（%s）" % (cid, name), cid)
                    model = self.trk_split_cell.model()
                    item = model.item(self.trk_split_cell.count() - 1) if model is not None and hasattr(model, "item") else None
                    if item is not None:
                        item.setEnabled(False)
                else:
                    self.trk_split_cell.addItem(cid, cid)
            want = 0
            if my_cell:
                ix = self.trk_split_cell.findData(str(my_cell))
                if ix >= 0:
                    want = ix
            self.trk_split_cell.setCurrentIndex(want)
        finally:
            self.trk_split_cell.blockSignals(False)
        active_div, _apply_all = self._active_split_divider(sp, win)
        divs_all = list((sp or {}).get("dividers") or [])
        d1_first = divs_all[0] if divs_all else {}
        is_circ = str(active_div.get("axis")) == "circle"
        is_blade = str(active_div.get("axis")) == "blade"
        is_center_ap = bool(d1_first.get("center_main"))
        shape_div = d1_first if (is_blade and is_center_ap) else active_div
        has_shape_ctrl = is_circ or is_blade or is_center_ap
        self.trk_split_pos.setValue(float(active_div.get("pos") or 0.5))
        self.trk_split_rot.setValue(float(shape_div.get("rotation_deg") or 0.0))
        self.trk_split_feather.setValue(float(active_div.get("feather_px") if active_div.get("feather_px") is not None else 24.0))
        if hasattr(self, "trk_split_shape"):
            self.trk_split_shape.blockSignals(True)
            cur_sh = str(shape_div.get("shape") or "circle")
            ix_sh = self.trk_split_shape.findData(cur_sh)
            self.trk_split_shape.setCurrentIndex(ix_sh if ix_sh >= 0 else 0)
            self.trk_split_shape.setEnabled(has_shape_ctrl)
            self.trk_split_shape.blockSignals(False)
        if hasattr(self, "trk_split_poly_sides"):
            self.trk_split_poly_sides.blockSignals(True)
            self.trk_split_poly_sides.setValue(int(shape_div.get("sides") or 6))
            cur_sh = str(shape_div.get("shape") or "circle")
            self.trk_split_poly_sides.setEnabled(
                has_shape_ctrl and cur_sh in ("polygon", "hexagon", "pentagon", "octagon", "diamond")
            )
            self.trk_split_poly_sides.blockSignals(False)
        if hasattr(self, "trk_split_shutter_twist"):
            self.trk_split_shutter_twist.blockSignals(True)
            self.trk_split_shutter_twist.setValue(float(d1_first.get("twist_deg") or 0.0))
            self.trk_split_shutter_twist.setEnabled(is_center_ap or is_blade)
            self.trk_split_shutter_twist.blockSignals(False)
        if hasattr(self, "trk_split_circle_r"):
            self.trk_split_circle_r.blockSignals(True)
            self.trk_split_circle_r.setValue(float(shape_div.get("r", 0.15) or 0.15))
            self.trk_split_circle_r.setEnabled(has_shape_ctrl)
            self.trk_split_circle_r.blockSignals(False)
        if hasattr(self, "trk_split_circle_aspect"):
            self.trk_split_circle_aspect.blockSignals(True)
            self.trk_split_circle_aspect.setValue(float(shape_div.get("aspect", 1.0) or 1.0))
            self.trk_split_circle_aspect.setEnabled(has_shape_ctrl)
            self.trk_split_circle_aspect.blockSignals(False)
        border = sp.get("border") or {}
        bs = str(border.get("style") or "none")
        ix = self.trk_split_border_style.findText(bs)
        self.trk_split_border_style.setCurrentIndex(ix if ix >= 0 else 0)
        raw_w = border.get("width_px")
        if raw_w is None:
            from cutora.model.factory_defaults import track_defaults
            raw_w = track_defaults()["split"]["border"]["width_px"]
        self.trk_split_border_width.setValue(int(float(raw_w)))
        cv = str(border.get("curve") or "straight")
        ix = self.trk_split_border_curve.findText(cv)
        self.trk_split_border_curve.setCurrentIndex(ix if ix >= 0 else 0)
        self.trk_split_border_shadow.setValue(int(float(border.get("shadow_px") or 12)))
        from cutora.render.split_layout import backdrop_is_full
        self.trk_split_backdrop.blockSignals(True)
        try:
            self.trk_split_backdrop.setCurrentText("full" if backdrop_is_full(sp) else "each")
        finally:
            self.trk_split_backdrop.blockSignals(False)

    def _exec_split(self, name, params):
        from cutora.core.commands import get_bus
        from cutora.core.log import get_logger
        from cutora.ui.i18n import tr
        win = self.window()
        try:
            get_bus().execute(name, params, source="ui")
        except Exception:
            get_logger("cutora.ui.panels.property").exception("%s 失败 %s", name, params)
            if win is not None and hasattr(win, "statusBar") and callable(win.statusBar):
                win.statusBar().showMessage(tr("分屏设置失败"), 4000)
            raise

    def _on_split_enable(self, *_args):
        if self.block_signals:
            return
        on = bool(self.trk_split_enable.isChecked())
        layout = "top_bottom"
        for key, btn in self.trk_split_layout_btns.items():
            if btn.isChecked():
                layout = key
                break
        self._exec_split("split.set_layout", {"layout": layout, "enabled": on})
        if on and self._track_obj is not None and not getattr(self._track_obj, "split_cell", None):
            # first free cell
            win, task = self._win_and_task()
            from cutora.render.split_layout import normalize_split, occupied_cells
            sp = normalize_split((task or {}).get("split") if task else None)
            occ = occupied_cells(task) if task is not None else {}
            for c in sp.get("cells") or []:
                cid = str(c.get("id"))
                if cid not in occ:
                    self._exec_split(
                        "track.set_cell",
                        {"track_id": str(self._track_obj.id), "cell": cid},
                    )
                    break

    def _on_split_layout(self, *_args):
        if self.block_signals:
            return
        layout = None
        for key, btn in self.trk_split_layout_btns.items():
            if btn.isChecked():
                layout = key
                break
        if not layout:
            return
        self._exec_split("split.set_layout", {"layout": layout, "enabled": True})
        if self._track_obj is not None:
            prev = self.block_signals
            self.block_signals = True
            try:
                self._load_split_widgets(self._track_obj)
            finally:
                self.block_signals = prev

    def _on_shuffle_secondary_tracks(self, *_args):
        """随机交换外部加载视频在各个分屏小格子中的位置（保持主视频在 A 格不动）。"""
        win, task = self._win_and_task()
        if not isinstance(task, dict):
            return
        from cutora.render.split_layout import shuffle_secondary_tracks
        push = getattr(win, "push_undo", None) if win is not None else None
        if callable(push):
            push("随机交换分屏位置")
        res = shuffle_secondary_tracks(task)
        cap = getattr(win, "capture_undo_after", None) if win is not None else None
        if callable(cap):
            cap()
        if self._track_obj is not None:
            prev = self.block_signals
            self.block_signals = True
            try:
                self._load_split_widgets(self._track_obj)
            finally:
                self.block_signals = prev
        if win is not None:
            if hasattr(win, "update_preview") and callable(win.update_preview):
                win.update_preview()
            elif hasattr(win, "video_canvas"):
                win.video_canvas.update()
            if hasattr(win, "timeline"):
                win.timeline.update()
            if hasattr(win, "statusBar") and callable(win.statusBar):
                pairs = ", ".join(f"{c}←{t}" for t, c in (res.get("mapping") or {}).items())
                win.statusBar().showMessage(
                    f"🔀 已随机交换外部视频分屏位置（共 {res.get('count', 0)} 条轨道：{pairs}）",
                    4500,
                )
        self.param_changed.emit(None)

    def _apply_custom_split_recipe(self, mode: str, rot_deg=None, curve=None):
        """一键应用自定义分屏（随机换创意 / 套圆圈 / 斜切 / 波浪 / 弧线 / L形 / 快门 / 异形）并自动分配所有轨道。"""
        if mode == "__shuffle__":
            self._on_shuffle_secondary_tracks()
            return
        win, task = self._win_and_task()
        if not isinstance(task, dict):
            return
        from cutora.render.split_layout import apply_custom_creative_split
        push = getattr(win, "push_undo", None) if win is not None else None
        if callable(push):
            push("自定义创意分屏")
        sp = apply_custom_creative_split(
            task,
            mode=mode,
            rot_deg=rot_deg,
            curve=curve,
        )
        cap = getattr(win, "capture_undo_after", None) if win is not None else None
        if callable(cap):
            cap()
        if self._track_obj is not None:
            prev = self.block_signals
            self.block_signals = True
            try:
                self._load_split_widgets(self._track_obj)
            finally:
                self.block_signals = prev
        if win is not None:
            if hasattr(win, "update_preview") and callable(win.update_preview):
                win.update_preview()
            elif hasattr(win, "video_canvas"):
                win.video_canvas.update()
            if hasattr(win, "timeline"):
                win.timeline.update()
            if hasattr(win, "statusBar") and callable(win.statusBar):
                ckind = str(sp.get("custom_kind") or mode)
                win.statusBar().showMessage(
                    f"✨ 已应用创意自定义分屏：{ckind}（可点击「🔀 随机交换位置」随时变换外部视频所在小分屏）",
                    5000,
                )
        self.param_changed.emit(None)

    def _on_split_cell(self, *_args):
        if self.block_signals or self._track_obj is None:
            return
        cell = self.trk_split_cell.currentData()
        self._exec_split(
            "track.set_cell",
            {"track_id": str(self._track_obj.id), "cell": cell},
        )

    def _active_split_divider(self, sp: dict, win=None) -> tuple[dict, bool]:
        divs = list((sp or {}).get("dividers") or [])
        if not divs:
            return {}, False
        my_cell = getattr(self._track_obj, "split_cell", None) if self._track_obj is not None else None
        if my_cell and str(my_cell) != "A":
            for dv in divs:
                if str(dv.get("pos_cell") or "") == str(my_cell):
                    if win is not None:
                        win._active_split_divider_id = str(dv.get("id") or "d1")
                    return dv, False
        active_id = getattr(win, "_active_split_divider_id", None) if win is not None else None
        if active_id:
            for dv in divs:
                if str(dv.get("id") or "") == str(active_id):
                    return dv, False
        return divs[0], (str(divs[0].get("axis")) == "circle" and not divs[0].get("center_main"))

    def _commit_split_divider(self, *_args):
        if self.block_signals:
            return
        win, task = self._win_and_task()
        from cutora.render.split_layout import normalize_split, SHAPE_DEFAULT_SIDES
        sp = normalize_split((task or {}).get("split") if isinstance(task, dict) else None)
        active_div, apply_all_circ = self._active_split_divider(sp, win)
        if not active_div:
            return
        divs_all = list((sp or {}).get("dividers") or [])
        d1_first = divs_all[0] if divs_all else {}
        is_blade = str(active_div.get("axis")) == "blade"
        is_center_ap = bool(d1_first.get("center_main"))
        if is_blade and is_center_ap:
            active_div = d1_first
            apply_all_circ = False
        target_id = str(active_div.get("id") or "d1")
        is_circ = str(active_div.get("axis")) == "circle"
        payload: dict = {"id": target_id}
        new_pos = float(self.trk_split_pos.value())
        if abs(new_pos - float(active_div.get("pos") or 0.5)) > 1e-4:
            payload["pos"] = new_pos
        new_rot = float(self.trk_split_rot.value())
        if abs(new_rot - float(active_div.get("rotation_deg") or 0.0)) > 1e-4:
            payload["rotation_deg"] = new_rot
        cur_f = float(active_div.get("feather_px") if active_div.get("feather_px") is not None else 24.0)
        new_f = float(self.trk_split_feather.value())
        if abs(new_f - cur_f) > 1e-4:
            payload["feather_px"] = new_f
        if is_circ:
            if hasattr(self, "trk_split_shape"):
                new_sh = str(self.trk_split_shape.currentData() or "circle")
                old_sh = str(active_div.get("shape") or "circle")
                if new_sh != old_sh:
                    payload["shape"] = new_sh
                    if new_sh in ("hexagon", "pentagon", "octagon", "diamond") and hasattr(self, "trk_split_poly_sides"):
                        def_s = SHAPE_DEFAULT_SIDES.get(new_sh, 6)
                        self.trk_split_poly_sides.blockSignals(True)
                        self.trk_split_poly_sides.setValue(def_s)
                        self.trk_split_poly_sides.blockSignals(False)
                        payload["sides"] = def_s
            if hasattr(self, "trk_split_poly_sides") and "sides" not in payload:
                new_sides = int(self.trk_split_poly_sides.value())
                if new_sides != int(active_div.get("sides") or 6):
                    payload["sides"] = new_sides
            if hasattr(self, "trk_split_shutter_twist") and (is_center_ap or bool(active_div.get("center_main"))):
                new_twist = float(self.trk_split_shutter_twist.value())
                if abs(new_twist - float(active_div.get("twist_deg") or 0.0)) > 1e-4:
                    payload["twist_deg"] = new_twist
            if hasattr(self, "trk_split_circle_r"):
                new_r = float(self.trk_split_circle_r.value())
                if abs(new_r - float(active_div.get("r", 0.15) or 0.15)) > 1e-4:
                    payload["r"] = new_r
            if hasattr(self, "trk_split_circle_aspect"):
                new_asp = float(self.trk_split_circle_aspect.value())
                if abs(new_asp - float(active_div.get("aspect", 1.0) or 1.0)) > 1e-4:
                    payload["aspect"] = new_asp
            if apply_all_circ and len(payload) > 1:
                payload["apply_all_circles"] = True
        if len(payload) <= 1:
            return
        self._exec_split("split.set_divider", payload)
        if hasattr(self, "trk_split_poly_sides") and hasattr(self, "trk_split_shape"):
            cur_sh = str(self.trk_split_shape.currentData() or "circle")
            self.trk_split_poly_sides.setEnabled(
                is_circ and cur_sh in ("polygon", "hexagon", "pentagon", "octagon", "diamond")
            )

    def _commit_split_border(self, *_args):
        if self.block_signals:
            return
        self._exec_split("split.set_layout", {
            "border": {
                "style": str(self.trk_split_border_style.currentText() or "none"),
                "width_px": int(self.trk_split_border_width.value()),
                "curve": str(self.trk_split_border_curve.currentText() or "straight"),
                "shadow_px": int(self.trk_split_border_shadow.value()),
            },
        })

    def _commit_split_backdrop(self, *_args):
        if self.block_signals:
            return
        mode = str(self.trk_split_backdrop.currentText() or "each")
        bd = {"mode": mode}
        if mode == "full":
            from cutora.render.backdrop import main_cell
            _win, task = self._win_and_task()
            cell = main_cell(task) if isinstance(task, dict) else None
            if cell:
                bd["cell"] = cell
        self._exec_split("split.set_layout", {"backdrop": bd})

    def _on_track_pin_toggled(self, *_args):
        if self.block_signals or self._track_obj is None:
            return
        from cutora.render.track_transform import (
            track_is_session_pinned, toggle_session_pin,
        )
        win = self.window()
        tlw = getattr(win, "timeline", None) if win is not None else None
        if tlw is None:
            from cutora.core.log import get_logger
            get_logger("cutora.ui.panels.property").warning("图钉切换失败：没有时间线")
            if hasattr(win, "statusBar") and callable(getattr(win, "statusBar", None)):
                win.statusBar().showMessage("图钉切换失败：没有时间线", 4000)
            return
        want = bool(self.trk_pin.isChecked())
        now = track_is_session_pinned(tlw, self._track_obj)
        if want != now:
            toggle_session_pin(tlw, self._track_obj)
        header = getattr(getattr(win, "timeline_container", None), "track_header", None)
        if header is not None:
            header.update()
        self._load_track_widgets(self._track_obj)

    def _commit_track_props(self, *_args):
        if self.block_signals or self._track_obj is None:
            return
        from cutora.core.commands import get_bus
        from cutora.core.log import get_logger
        from cutora.render.track_transform import edit_track_ids, track_props_of
        from cutora.ui.i18n import tr
        win = self.window()
        tlw = getattr(win, "timeline", None) if win is not None else None
        ids = edit_track_ids(tlw, self._track_obj) if tlw is not None else [
            str(getattr(self._track_obj, "id", "") or "")]
        ids = [i for i in ids if i]
        if not ids:
            get_logger("cutora.ui.panels.property").warning("track.set_props 没有 track_ids")
            if win is not None and hasattr(win, "statusBar") and callable(win.statusBar):
                win.statusBar().showMessage("设置轨道属性失败：没有选中轨道", 4000)
            return
        props = track_props_of(self._track_obj)
        name = self.trk_name.text().strip()
        patch = {}
        if name != str(getattr(self._track_obj, "name", "") or ""):
            patch["name"] = name
        mapping = {
            "x": float(self.trk_x.value()),
            "y": float(self.trk_y.value()),
            "scale": float(self.trk_scale.value()),
            "rotation": float(self.trk_rotation.value()),
            "flip_x": bool(self.trk_flip_x.isChecked()),
            "flip_y": bool(self.trk_flip_y.isChecked()),
            "opacity": float(self.trk_opacity.value()),
            "blend_mode": str(self.trk_blend.currentText() or "normal"),
        }
        for key, val in mapping.items():
            cur = props.get(key)
            if isinstance(val, float):
                if abs(float(cur or 0) - val) > 1e-9:
                    patch[key] = val
            elif cur != val:
                patch[key] = val
        if not patch:
            return
        try:
            get_bus().execute(
                "track.set_props",
                {"track_ids": ids, "patch": patch},
                source="ui",
            )
        except Exception:
            get_logger("cutora.ui.panels.property").exception(
                "track.set_props 失败 ids=%s patch=%s", ids, patch)
            if win is not None and hasattr(win, "statusBar") and callable(win.statusBar):
                win.statusBar().showMessage(tr("设置轨道属性失败"), 4000)
            raise

    def load_data(self, type_, data):
        self.block_signals = True
        widgets = [
            self.ov_x, self.ov_y, self.ov_w, self.ov_h, self.ov_rot, self.ov_op,
            self.ov_transition, self.ov_transition_dur,
            self.ov_sticker_anim_in, self.ov_sticker_anim_out,
            self.ov_content, self.ov_font, self.ov_size, self.ov_bold, self.ov_italic, self.ov_text_align,
            self.ov_text_valign,
            self.ov_strk, self.ov_bg_enable, self.ov_glow_enable, self.ov_shadow_enable,
            self.ov_pad_l, self.ov_pad_r, self.ov_pad_t, self.ov_pad_b,
            self.ov_line_spacing, self.ov_letter_spacing,
            self.ov_scale, self.ov_flip_x, self.ov_flip_y, self.ov_keep_aspect, self.ov_lock_btn, self.ov_feather, self.ov_blend,
            self.audio_vol, self.audio_pitch, self.audio_speed,
            self.audio_denoise, self.audio_denoise_strength,
            self.audio_fade_in_dur, self.audio_fade_out_dur,
            # Video panel widgets
            self.vid_fit, self.vid_x, self.vid_y, self.vid_scale, self.vid_rotation, self.vid_lock_btn,
            self.vid_flip_x, self.vid_flip_y, self.vid_speed, self.vid_loop, self.vid_match_dur,
            self.vid_alpha_mode,
            self.vid_transition, self.vid_transition_dur, self.vid_blend,
            self.vid_matte, self.vid_matte_inv,
            # Transition panel widgets
            self.trans_preset_cb, self.trans_dur_sp,
            self.trans_zoom_start_sp, self.trans_motion_blur_sp,
            self.trans_dir_cb, self.trans_angle_sp, self.trans_feather,
            self.trans_intensity_sp, self.trans_easing_cb,
            self.ov_fade_top, self.ov_fade_bottom, self.ov_fade_left, self.ov_fade_right,
            self.ov_fade_top_offset, self.ov_fade_bottom_offset,
            self.ov_fade_left_offset, self.ov_fade_right_offset,
            self.ov_fade_sync, self.ov_fade_master,
            self.vid_fade_top, self.vid_fade_bottom, self.vid_fade_left, self.vid_fade_right,
            self.vid_fade_top_offset, self.vid_fade_bottom_offset,
            self.vid_fade_left_offset, self.vid_fade_right_offset,
            self.vid_fade_sync, self.vid_fade_master,
            self.trk_name, self.trk_pin, self.trk_x, self.trk_y,
            self.trk_scale, self.trk_rotation, self.trk_flip_x, self.trk_flip_y,
            self.trk_opacity, self.trk_blend,
            self.trk_split_enable, self.trk_split_cell,
            self.trk_split_pos, self.trk_split_circle_r, self.trk_split_circle_aspect,
            self.trk_split_rot, self.trk_split_feather,
            self.trk_split_border_style, self.trk_split_border_width,
            self.trk_split_border_curve, self.trk_split_border_shadow,
        ]
        widgets.extend(list(iter_fx_widgets(self)))
        for w in widgets:
            w.blockSignals(True)
        try:
            self._load_data_inner(type_, data, widgets)
        except Exception as e:
            print(f"[PropertyPanel.load_data] ERROR: {e}")
        finally:
            for w in widgets:
                w.blockSignals(False)
            self._sync_mirror_sliders()
            self.block_signals = False
            if type_ in ("overlay", "sticker"):
                self._refresh_kf_watches()
            try:
                self.data_loaded.emit(type_, data)
            except Exception:
                pass

    def _sync_mirror_sliders(self) -> None:
        """Spins are loaded with signals blocked, so their drag sliders never
        heard the new value (opacity showed 255 with the knob at 0)."""
        pairs = [
            ("ov_x", "ov_x_sl"), ("ov_y", "ov_y_sl"), ("ov_w", "ov_w_sl"),
            ("ov_h", "ov_h_sl"), ("ov_rot", "ov_rot_sl"), ("ov_op", "ov_op_sl"),
            ("trans_angle_sp", "trans_angle_sl"),
        ]
        for sp_name, sl_name in pairs:
            sp = getattr(self, sp_name, None)
            sl = getattr(self, sl_name, None)
            if sp is None or sl is None:
                continue
            sl.blockSignals(True)
            sl.setValue(int(round(float(sp.value()))))
            sl.blockSignals(False)

    def _load_data_inner(self, type_, data, widgets):
        self.current_data = data
        self.current_type = type_
        if hasattr(self, "ov_sticker_anim_in"):
            _st = type_ == "sticker" and isinstance(data, dict) and bool(data.get("sticker_id"))
            self.ov_sticker_anim_in_label.setVisible(_st)
            self.ov_sticker_anim_in.setVisible(_st)
            self.ov_sticker_anim_out_label.setVisible(_st)
            self.ov_sticker_anim_out.setVisible(_st)
        
        if hasattr(self, "ov_adjust"):
            _is_adj = type_ == 'overlay' and isinstance(data, dict) and data.get('type') == 'adjust'
            self.ov_adjust.setVisible(_is_adj)
            if _is_adj:
                self.ov_adjust.load(data)
        if type_ == 'overlay':
            self.stack.setCurrentIndex(1)
            ov = data
            if hasattr(self, "ov_grade"):
                self.ov_grade.hide()
            if hasattr(self, "ov_fx"):
                self.ov_fx.reload()
            self.ov_x.setValue(int(ov.get('x', 0)))
            self.ov_y.setValue(int(ov.get('y', 0)))
            self.ov_w.setValue(int(ov.get('w', 100)))
            self.ov_h.setValue(int(ov.get('h', 100)))
            self.ov_rot.setValue(int(ov.get('rotation', 0)))
            self.ov_op.setValue(int(ov.get('opacity', 255)))
            self._load_fade_widgets("ov", ov)
            if hasattr(self, "ov_tscale"):
                _is_text = ov.get("type") == "text"
                self._set_ov_tscale_visible(_is_text)
                if _is_text:
                    from cutora.render.text_layer_paint import overlay_scale
                    self.ov_tscale.setValue(overlay_scale(ov))
            if hasattr(self, "ov_transition"):
                is_adjust = ov.get("type") == "adjust"
                self._set_ov_transition_visible(not is_adjust)
                if not is_adjust:
                    self._set_combo_by_data(self.ov_transition, ov.get("transition_preset", "none"), "none")
                    self.ov_transition_dur.setValue(float(ov.get("transition_duration", anim_engine.DEFAULT_TRANSITION_DURATION)))
            
            if ov['type'] == 'adjust':
                # Q174：调整图层只用「变换」+ 自己那一组 + 遮罩；图片 / 文字 / 渐隐 / 转场那些都不适用
                self.ov_text_grp.hide()
                self.ov_image_grp.hide()
                self.ov_fade_grp.hide()
                self.ov_element_order_grp.hide()
                self.ov_lock_btn.show()
                for grp in self._ov_text_fx_groups():
                    grp.hide()
                if hasattr(self, "ov_transition"):
                    self._set_ov_transition_visible(False)
                self.sync_intro_style_btn.setVisible(False)
                self._set_ov_lock(bool(ov.get('lock_aspect', False)))
            elif ov['type'] == 'text':
                # Q145：整段文字的字体 / 字号如果是「选中文字」工具条设的（覆盖全文的 run），
                # 面板上的字体框显示的是本层字体、一改字号就回到默认字体。打开时就把它
                # 提升成本层格式：字体框、字号框显示的就是画面上真正的字。
                from cutora.subtitle.text_layer import promote_whole_runs
                promote_whole_runs(ov)
                self.ov_fade_grp.hide()
                self._refresh_ov_text_presets()
                self.ov_preset_combo.blockSignals(True)
                preset_id = ov.get("source_preset_id") or ""
                if not preset_id:
                    raw_name = str(ov.get("_preset_name") or "").strip()
                    if raw_name:
                        row = text_preset_store.resolve_preset(raw_name)
                        preset_id = (row or {}).get("id") or ""
                idx_p = self.ov_preset_combo.findData(preset_id) if preset_id else -1
                if idx_p >= 0:
                    self.ov_preset_combo.setCurrentIndex(idx_p)
                else:
                    default_id = text_preset_store.get_default_preset_id()
                    idx_d = self.ov_preset_combo.findData(default_id) if default_id else -1
                    if idx_d >= 0:
                        self.ov_preset_combo.setCurrentIndex(idx_d)
                    elif self.ov_preset_combo.count() > 0:
                        self.ov_preset_combo.setCurrentIndex(0)
                self.ov_preset_combo.blockSignals(False)
                self.ov_text_grp.show()
                self.ov_lock_btn.hide()
                self.ov_image_grp.hide()
                self.ov_element_order_grp.hide()
                for grp in self._ov_text_fx_groups():
                    grp.show()
                content_format = ov.get("content_format", "plain")
                if not ENABLE_RICH_TEXT and content_format == "rich_spans":
                    ov["content"] = _rich_plain_text(ov.get("rich_content", [])) or ov.get("content", "")
                    ov["content_format"] = "plain"
                    ov.pop("rich_content", None)
                    content_format = "plain"
                if ENABLE_RICH_TEXT and content_format == "rich_spans":
                    self.ov_content.setReadOnly(False)
                    base_style = {
                        "font_family": sanitize_font_family(ov.get("font_family", DEFAULT_FONT_FAMILY)),
                        "fontsize": int(ov.get("fontsize", 40)),
                        "bold": bool(ov.get("bold", False)),
                        "italic": bool(ov.get("italic", False)),
                        "color": self._overlay_fill_hex(ov),
                        "stroke_color": ov.get("stroke_color", "#000000"),
                        "stroke_width": int(ov.get("stroke_width", 0)),
                        "use_stroke": bool(ov.get("use_stroke", False)),
                        "shadow_color": ov.get("shadow_color", "#000000"),
                        "shadow_opacity": int(ov.get("shadow_opacity", 120)),
                        "shadow_blur": int(ov.get("shadow_blur", 0)),
                        "shadow_offset_x": int(ov.get("shadow_offset_x", 4)),
                        "shadow_offset_y": int(ov.get("shadow_offset_y", 4)),
                        "shadow_enabled": bool(ov.get("shadow_enabled", False)),
                    }
                    spans = ov.get("rich_content", []) if isinstance(ov.get("rich_content"), list) else []
                    if not spans:
                        spans = [{"text": ov.get('content', ''), **base_style}]
                    _editor_set_spans(self.ov_content, spans, base_style)
                else:
                    self.ov_content.setReadOnly(False)
                    if hasattr(self, "ov_rich"):
                        self.ov_rich.set_text_runs(
                            ov.get("content", ""),
                            ov.get("runs") or [],
                            {
                                "font_family": ov.get("font_family"),
                                "fontsize": ov.get("fontsize", 40),
                                "bold": ov.get("bold", False),
                                "italic": ov.get("italic", False),
                                "color": self._overlay_fill_hex(ov),
                            },
                        )
                    else:
                        self.ov_content.setText(ov.get('content', ''))
                ov['font_family'] = sanitize_font_family(ov.get("font_family"))
                self.ov_font.setCurrentText(ov.get('font_family'))
                # Q170：字体选择器底部样张用这条文本自己的内容
                self.ov_font.setProperty("fontSample", str(ov.get('content') or ov.get('text') or ""))
                self.ov_size.setValue(int(ov.get('fontsize', 40)))
                self.ov_bold.setChecked(ov.get('bold', False))
                self.ov_italic.setChecked(ov.get('italic', False))
                layout = _rich_default_layout(ov.get("text_layout"))
                self.ov_pad_l.setValue(int(layout.get("left_pad", 0)))
                self.ov_pad_r.setValue(int(layout.get("right_pad", 0)))
                self.ov_pad_t.setValue(int(layout.get("top_pad", 0)))
                self.ov_pad_b.setValue(int(layout.get("bottom_pad", 0)))
                self.ov_line_spacing.setValue(int(layout.get("line_spacing", 0)))
                self.ov_letter_spacing.setValue(int(layout.get("letter_spacing", 0)))
                self.ov_text_align.setCurrentText(_normalize_text_align(layout.get("align", "left")))
                if hasattr(self, "ov_text_valign"):
                    _va = str(layout.get("valign") or "top")
                    self.ov_text_valign.setCurrentText(
                        _va if _va in ("top", "middle", "bottom") else "top")
                if hasattr(self, "ov_first_indent"):
                    self.ov_first_indent.setValue(int(layout.get("first_indent", 0) or 0))
                    self.ov_para_spacing.setValue(int(layout.get("para_spacing", 0) or 0))
                if hasattr(self, "ov_writing_mode"):
                    from cutora.subtitle.style_schema import normalize_writing_mode
                    wm = normalize_writing_mode(ov.get("writing_mode"))
                    idx_w = self.ov_writing_mode.findData(wm)
                    if idx_w >= 0:
                        self.ov_writing_mode.setCurrentIndex(idx_w)
                    fm = str(ov.get("fit_mode") or "none")
                    idx_f = self.ov_fit_mode.findData(fm)
                    if idx_f >= 0:
                        self.ov_fit_mode.setCurrentIndex(idx_f)
                self._load_ov_fx(ov)
            else:
                self.ov_text_grp.hide()
                self.ov_lock_btn.show()
                self.ov_image_grp.show()
                self.ov_fade_grp.show()
                self.ov_element_order_grp.hide()
                for grp in self._ov_text_fx_groups():
                    grp.hide()
                self.sync_intro_style_btn.setVisible(bool(ov.get("is_intro")))
                self.ov_scale.setValue(float(ov.get('scale', 1.0)))
                self.ov_flip_x.setChecked(ov.get('flip_x', False))
                self.ov_flip_y.setChecked(ov.get('flip_y', False))
                self._set_ov_lock(ov.get('lock_aspect', ov.get('keep_aspect', True)))
                self.ov_feather.setValue(int(ov.get('feather', 0)))
                # 混合模式（优先元素自身，其次 Track）
                blend = ov.get('blend_mode')
                if not blend:
                    parent_track = getattr(self, '_ov_parent_track', None)
                    blend = getattr(parent_track, 'blend_mode', 'normal') if parent_track else 'normal'
                idx_b = self.ov_blend.findText(blend)
                if idx_b >= 0:
                    self.ov_blend.setCurrentIndex(idx_b)
                self._show_image_grade(ov)
        elif type_ == 'sticker':
            self.stack.setCurrentIndex(1)
            if hasattr(self, "ov_grade"):
                self.ov_grade.hide()
            self.ov_lock_btn.setVisible(data.get('kind') == 'image')
            el = data
            self.ov_x.setValue(int(el.get('x', 0)))
            self.ov_y.setValue(int(el.get('y', 0)))
            self.ov_w.setValue(int(el.get('w', 100)))
            self.ov_h.setValue(int(el.get('h', 100)))
            self.ov_rot.setValue(int(el.get('rotation', 0)))
            self.ov_op.setValue(int(el.get('opacity', 255)))
            self._load_fade_widgets("ov", el)
            if hasattr(self, "ov_transition"):
                # [FIX-IMG] 图片元素直接选中时，转场属性来自父覆层，保持可编辑
                _p_ov = getattr(self, '_ov_parent_overlay', None)
                _show_tr = isinstance(_p_ov, dict) and el.get('kind') == 'image'
                self.ov_transition_label.setVisible(_show_tr)
                self.ov_transition.setVisible(_show_tr)
                self.ov_transition_dur.setVisible(_show_tr)
                if _show_tr:
                    self._set_combo_by_data(self.ov_transition, _p_ov.get("transition_preset", "none"), "none")
                    self.ov_transition_dur.setValue(float(_p_ov.get("transition_duration", anim_engine.DEFAULT_TRANSITION_DURATION)))
            _is_sticker = bool(el.get("sticker_id"))
            self.ov_sticker_anim_in_label.setVisible(_is_sticker)
            self.ov_sticker_anim_in.setVisible(_is_sticker)
            self.ov_sticker_anim_out_label.setVisible(_is_sticker)
            self.ov_sticker_anim_out.setVisible(_is_sticker)
            if _is_sticker:
                self._set_combo_by_data(self.ov_sticker_anim_in, el.get("anim_in", "fade"), "fade")
                self._set_combo_by_data(self.ov_sticker_anim_out, el.get("anim_out", "fade"), "fade")
            self.ov_text_grp.hide()
            self.ov_image_grp.show()
            self.ov_fade_grp.show()
            self.ov_element_order_grp.show()
            for grp in self._ov_text_fx_groups():
                grp.hide()
            self._load_fade_widgets("ov", el)
            self.sync_intro_style_btn.setVisible(False)
            parent_ov = getattr(self, '_ov_parent_overlay', None) or {}
            self.ov_scale.setValue(float(parent_ov.get('scale', 1.0)))
            self.ov_flip_x.setChecked(parent_ov.get('flip_x', False))
            self.ov_flip_y.setChecked(parent_ov.get('flip_y', False))
            self._set_ov_lock(el.get(
                'lock_aspect', parent_ov.get('lock_aspect', parent_ov.get('keep_aspect', True))))
            self.ov_feather.setValue(int(parent_ov.get('feather', 0)))
            blend = el.get('blend_mode')
            if not blend:
                parent_track = getattr(self, '_ov_parent_track', None)
                blend = getattr(parent_track, 'blend_mode', 'normal') if parent_track else 'normal'
            idx_b = self.ov_blend.findText(blend)
            if idx_b >= 0:
                self.ov_blend.setCurrentIndex(idx_b)
            if el.get("kind") == "image":
                parent_host = parent_ov if isinstance(parent_ov, dict) and parent_ov else el
                self._show_image_grade(parent_host)
        elif type_ == 'audio':
            self.stack.setCurrentIndex(2)
            self.audio_targets = []
            ac = data
            if hasattr(ac, 'gain_db'):
                gain = float(getattr(ac, 'gain_db', 0.0))
                vol = 10.0 ** (gain / 20.0) if gain != 0.0 else 1.0
                pitch = float(getattr(ac, 'pitch_semitones', 0.0))
                fi = float(getattr(ac, 'fade_in_dur', 0.0))
                fo = float(getattr(ac, 'fade_out_dur', 0.0))
            else:
                vol = float(ac.get('volume', 1.0))
                pitch = float(ac.get('pitch_semitones', 0.0))
                fi = float(ac.get('fade_in_dur', 0.0))
                fo = float(ac.get('fade_out_dur', 0.0))
            # 同步 spin + slider（按各控件 step 计算 scale，与 _add_audio_slider_row 一致）
            def _scale(spin):
                return max(1, int(round(1.0 / spin.singleStep())))
            self.audio_vol.setValue(vol)
            self.audio_vol_sl.blockSignals(True)
            self.audio_vol_sl.setValue(int(round(vol * _scale(self.audio_vol))))
            self.audio_vol_sl.blockSignals(False)
            self.audio_pitch.setValue(pitch)
            if hasattr(ac, "speed"):
                self.audio_speed.setValue(float(getattr(ac, "speed", 1.0) or 1.0))
            elif isinstance(ac, dict):
                self.audio_speed.setValue(float(ac.get("speed", 1.0) or 1.0))
            proc = getattr(ac, "audio_proc", None) if not isinstance(ac, dict) else ac.get("audio_proc")
            proc = proc if isinstance(proc, dict) else {}
            pi = self.audio_denoise.findData(proc.get("preset") or "off")
            self.audio_denoise.blockSignals(True)
            self.audio_denoise.setCurrentIndex(0 if pi < 0 else pi)
            self.audio_denoise.blockSignals(False)
            si = self.audio_denoise_strength.findData(proc.get("strength") or "mid")
            self.audio_denoise_strength.blockSignals(True)
            self.audio_denoise_strength.setCurrentIndex(1 if si < 0 else si)
            self.audio_denoise_strength.blockSignals(False)
            if hasattr(self, "btn_speed_curve"):
                curve = ac.get("speed_curve") if isinstance(ac, dict) else getattr(ac, "speed_curve", None)
                self.btn_speed_curve.setText("清除速度曲线" if curve else "添加速度曲线")
            if hasattr(self, "audio_stem_status"):
                op = str(proc.get("op") or "")
                if op == "stem_vocals":
                    self.audio_stem_status.setText("只留说话")
                elif op == "stem_novocals":
                    self.audio_stem_status.setText("只留伴奏")
                else:
                    self.audio_stem_status.setText("未分离")
            self.audio_pitch_sl.blockSignals(True)
            self.audio_pitch_sl.setValue(int(round(pitch * _scale(self.audio_pitch))))
            self.audio_pitch_sl.blockSignals(False)
            self.audio_fade_in_dur.setValue(fi)
            self.audio_fade_in_dur_sl.blockSignals(True)
            self.audio_fade_in_dur_sl.setValue(int(round(fi * _scale(self.audio_fade_in_dur))))
            self.audio_fade_in_dur_sl.blockSignals(False)
            self.audio_fade_out_dur.setValue(fo)
            self.audio_fade_out_dur_sl.blockSignals(True)
            self.audio_fade_out_dur_sl.setValue(int(round(fo * _scale(self.audio_fade_out_dur))))
            self.audio_fade_out_dur_sl.blockSignals(False)
            spat_on = bool(getattr(ac, "spatial_enabled", False) if hasattr(ac, "spatial_enabled") else ac.get("spatial_enabled", False))
            spat_amt = float(getattr(ac, "spatial_amount", 0.16) if hasattr(ac, "spatial_amount") else ac.get("spatial_amount", 0.16))
            if hasattr(self, "audio_spatial_enable"):
                self.audio_spatial_enable.blockSignals(True)
                self.audio_spatial_enable.setChecked(spat_on)
                self.audio_spatial_enable.blockSignals(False)
            if hasattr(self, "audio_spatial_amount"):
                self.audio_spatial_amount.setValue(spat_amt)
                self.audio_spatial_amount_sl.blockSignals(True)
                self.audio_spatial_amount_sl.setValue(int(round(spat_amt * _scale(self.audio_spatial_amount))))
                self.audio_spatial_amount_sl.blockSignals(False)
            self._sync_spatial_wet_enabled()
            self._refresh_kf_watches()
            if hasattr(self, "audio_link_status"):
                self.audio_link_status.setText(self._av_link_status_text(ac))
        elif type_ == 'video_clip':
            self.stack.setCurrentIndex(3)
            clip = data  # 可以是 Clip 对象或 (Track, Clip) 元组
            track = None
            if isinstance(data, tuple) and len(data) == 2:
                track, clip = data
            self._video_clip = clip
            self._video_track = track
            if hasattr(self, "vid_group"):
                self.vid_group.reload()
            if hasattr(self, "vid_link_status"):
                self.vid_link_status.setText(self._av_link_status_text(clip))
            self.vid_fit.setCurrentText(str(getattr(clip, 'fit_mode', 'fill')))
            if hasattr(self, "vid_bg_mode"):
                self._load_bg_fill(clip)
            if hasattr(self, "vid_fx"):
                self.vid_fx.reload()
            self.vid_x.setValue(float(getattr(clip, 'x', 0.0)))
            self.vid_y.setValue(float(getattr(clip, 'y', 0.0)))
            self.vid_scale.setValue(float(getattr(clip, 'scale', 1.0)))
            if hasattr(self, "vid_lock_btn"):
                from cutora.model.project_schema import lock_aspect_of
                self.vid_lock_btn.setChecked(lock_aspect_of(clip))
                bind_icon_safe(
                    self.vid_lock_btn,
                    "link" if self.vid_lock_btn.isChecked() else "unlink",
                    "accent" if self.vid_lock_btn.isChecked() else "fg2")
            self.vid_rotation.setValue(float(getattr(clip, 'rotation', 0.0)))
            self.vid_flip_x.setChecked(bool(getattr(clip, 'flip_x', False)))
            self.vid_flip_y.setChecked(bool(getattr(clip, 'flip_y', False)))
            if hasattr(self, "vid_opacity"):
                self.vid_opacity.setValue(int(getattr(clip, 'opacity', 255)))
            if hasattr(self, "vid_transition"):
                preset = getattr(clip, "transition_preset", "none")
                self._set_combo_by_data(self.vid_transition, preset, "none")
            if hasattr(self, "vid_transition_dur"):
                self.vid_transition_dur.setValue(float(getattr(clip, "transition_duration", anim_engine.DEFAULT_TRANSITION_DURATION)))
            self.vid_speed.setValue(float(getattr(clip, 'speed', 1.0)))
            self.vid_loop.setChecked(bool(getattr(clip, 'loop', False)))
            self.vid_match_dur.setChecked(bool(getattr(clip, 'match_duration', False)))
            if hasattr(self, "vid_alpha_mode"):
                mode = getattr(clip, "alpha_mode", None) or "straight"
                idx_a = self.vid_alpha_mode.findData(str(mode))
                if idx_a < 0:
                    idx_a = 0
                self.vid_alpha_mode.setCurrentIndex(idx_a)
            blend_val = getattr(clip, 'blend_mode', None) or (getattr(track, 'blend_mode', 'normal') if track else 'normal')
            self.vid_blend.setCurrentText(str(blend_val))
            if hasattr(self, "vid_matte"):
                matte_on = bool(str(getattr(clip, "matte_for", "") or ""))
                self.vid_matte.setChecked(matte_on)
                self.vid_matte_inv.setChecked(matte_on and bool(getattr(clip, "matte_invert", False)))
                self.vid_matte_inv.setEnabled(matte_on)
            if hasattr(self, "vid_crop_l"):
                self.vid_crop_l.setValue(int(getattr(clip, 'crop_left', 0)))
                self.vid_crop_r.setValue(int(getattr(clip, 'crop_right', 0)))
                self.vid_crop_t.setValue(int(getattr(clip, 'crop_top', 0)))
                self.vid_crop_b.setValue(int(getattr(clip, 'crop_bottom', 0)))
            if hasattr(self, "vid_feather"):
                self.vid_feather.setValue(int(getattr(clip, 'feather', 0)))
            self._load_fade_widgets("vid", clip)
            if track is not None:
                self.vid_blend.setCurrentText(str(getattr(track, 'blend_mode', 'normal')))
            if hasattr(self, "vid_grade"):
                self.vid_grade.load_grade(getattr(clip, "grade", None))
            self._refresh_kf_watches()
        elif type_ == 'transition':
            self.stack.setCurrentIndex(4)
            trans_obj = data  # Reuse video_clip as the target transition host
            if isinstance(data, tuple) and len(data) == 2:
                track, trans_obj = data
            self._video_clip = trans_obj
            self._video_track = track
            if hasattr(self, "trans_preset_cb"):
                preset = getattr(trans_obj, "preset", "none")
                self._set_combo_by_data(self.trans_preset_cb, preset, "none")
            if hasattr(self, "trans_dur_sp"):
                self.trans_dur_sp.setValue(float(getattr(trans_obj, "duration", anim_engine.DEFAULT_TRANSITION_DURATION)))
            if hasattr(self, "trans_zoom_start_sp"):
                self.trans_zoom_start_sp.setValue(float(getattr(trans_obj, "zoom_scale_start", 0.7)))
            if hasattr(self, "trans_motion_blur_sp"):
                self.trans_motion_blur_sp.setValue(float(getattr(trans_obj, "motion_blur", 0.4)))
            if hasattr(self, "trans_intensity_sp"):
                self.trans_intensity_sp.setValue(float(getattr(trans_obj, "intensity", 1.0) or 1.0))
            if hasattr(self, "trans_easing_cb"):
                self._set_combo_by_data(
                    self.trans_easing_cb,
                    getattr(trans_obj, "easing", "ease_in_out"),
                    "ease_in_out")
            direction = getattr(trans_obj, "direction", None)
            preset_now = str(getattr(trans_obj, "preset", "") or "")
            if hasattr(self, "trans_angle_sp"):
                if preset_now == "wipe_angle":
                    try:
                        self.trans_angle_sp.setValue(float(direction))
                    except (TypeError, ValueError):
                        self.trans_angle_sp.setValue(32.0)
                else:
                    try:
                        self.trans_angle_sp.setValue(float(getattr(trans_obj, "angle", 0.0) or 0.0))
                    except (TypeError, ValueError):
                        self.trans_angle_sp.setValue(0.0)
            if hasattr(self, "trans_feather"):
                try:
                    self.trans_feather.setValue(float(getattr(trans_obj, "feather", 0.0) or 0.0))
                except (TypeError, ValueError):
                    self.trans_feather.setValue(0.0)
            if hasattr(self, "trans_dir_cb"):
                dir_key = direction
                if isinstance(direction, (int, float)):
                    ang = float(direction) % 360.0
                    nearest = min(
                        (("left", 0.0), ("down", 90.0), ("right", 180.0), ("up", 270.0)),
                        key=lambda kv: min(abs(ang - kv[1]), 360.0 - abs(ang - kv[1])))
                    dir_key = nearest[0]
                self._set_combo_by_data(self.trans_dir_cb, dir_key, "left")
                self._sync_transition_rows(getattr(trans_obj, "preset", "") or "")
        elif type_ == "track":
            self.stack.setCurrentIndex(5)
            track = data
            self._track_obj = track
            self._load_track_widgets(track)
        else:
             self.stack.setCurrentIndex(0)

    def _on_change_ov_blend(self, blend_name):
        """混合模式变更 → 写到当前元素/overlay。"""
        if self.block_signals:
            return
        if self.current_type == 'overlay' and isinstance(self.current_data, dict):
            self.current_data['blend_mode'] = blend_name
            self.param_changed.emit(None)
            return
        if self.current_type == 'sticker' and isinstance(self.current_data, dict):
            self.current_data['blend_mode'] = blend_name
            self.param_changed.emit(None)

    def _request_rich_edit(self):
        if isinstance(self.current_data, dict) and self.current_type in ("overlay", "sticker"):
            self.rich_text_edit_request.emit(self.current_type, self.current_data)

    def _apply_rich_char_format(self, fmt):
        cursor = self.ov_content.textCursor()
        if not cursor.hasSelection():
            cursor.select(QTextCursor.SelectionType.WordUnderCursor)
        self.block_signals = True
        cursor.mergeCharFormat(fmt)
        self.ov_content.mergeCurrentCharFormat(fmt)
        self.block_signals = False

    def _sync_rich_from_editor(self, ov):
        if not ENABLE_RICH_TEXT:
            ov["content_format"] = "plain"
            ov["content"] = self.ov_content.toPlainText()
            ov.pop("rich_content", None)
            ov.pop("_rich_cache", None)
            return
        base_style = {
            "font_family": sanitize_font_family(ov.get("font_family", DEFAULT_FONT_FAMILY)),
            "fontsize": int(ov.get("fontsize", 40)),
            "bold": bool(ov.get("bold", False)),
            "italic": bool(ov.get("italic", False)),
            "color": self._overlay_fill_hex(ov),
        }
        spans = _editor_to_spans(self.ov_content, base_style)
        ov["content_format"] = "rich_spans"
        ov["rich_content"] = spans
        ov["content"] = _rich_plain_text(spans)
        ov.pop("_rich_cache", None)
        for sp in spans:
            if isinstance(sp, dict) and sp.get("text") not in ("", None, "\n"):
                from cutora.subtitle.io import canvas_size_for_window
                from cutora.subtitle.stylepack import set_fontsize_px
                ov["font_family"] = sanitize_font_family(sp.get("font_family", ov.get("font_family", DEFAULT_FONT_FAMILY)))
                cw, ch = canvas_size_for_window(self.window())
                set_fontsize_px(
                    ov, int(sp.get("fontsize", ov.get("fontsize", 40))), cw, ch)
                ov["bold"] = bool(sp.get("bold", ov.get("bold", False)))
                ov["italic"] = bool(sp.get("italic", ov.get("italic", False)))
                ov["color"] = sp.get("color", self._overlay_fill_hex(ov))
                break

    def _on_ov_runs_changed(self, text, runs):
        if self.block_signals:
            return
        ov = self.current_data
        if not isinstance(ov, dict) or ov.get("type") != "text":
            return
        ov["content"] = text
        if runs:
            ov["runs"] = list(runs)
        else:
            ov.pop("runs", None)
        from cutora.subtitle.text_layer import promote_whole_runs
        if runs and promote_whole_runs(ov, text):
            # 全选后改了字体 / 字号 = 改这一层本身：面板和编辑框的「本层格式」跟着换，
            # 免得下次任何编辑又拿旧的本层字体把它盖回去。
            self._sync_ov_base_font_widgets(ov)
        self.param_changed.emit("runs")

    def _sync_ov_base_font_widgets(self, ov) -> None:
        prev = self.block_signals
        self.block_signals = True
        try:
            self.ov_font.blockSignals(True)
            self.ov_size.blockSignals(True)
            self.ov_font.setCurrentText(str(ov.get("font_family") or ""))
            self.ov_size.setValue(int(round(float(ov.get("fontsize") or 40))))
            self.ov_bold.blockSignals(True)
            self.ov_italic.blockSignals(True)
            self.ov_bold.setChecked(bool(ov.get("bold", False)))
            self.ov_italic.setChecked(bool(ov.get("italic", False)))
            self.ov_bold.blockSignals(False)
            self.ov_italic.blockSignals(False)
            self.ov_font.blockSignals(False)
            self.ov_size.blockSignals(False)
            emph_w = getattr(self, "fx_fonts_emphasis", None)
            fonts = (ov.get("fx") or {}).get("fonts") if isinstance(ov.get("fx"), dict) else None
            if emph_w is not None:
                # 没单独设强调字体 = 跟着正文字体走
                target = (str(fonts.get("emphasis")) if isinstance(fonts, dict) and fonts.get("emphasis")
                          else str(ov.get("font_family") or ""))
                if target:
                    emph_w.blockSignals(True)
                    emph_w.setCurrentText(target)
                    emph_w.blockSignals(False)
            rich = getattr(self, "ov_rich", None)
            if rich is not None:
                edit = rich.edit
                cur = edit.textCursor()
                anchor, pos = cur.anchor(), cur.position()
                rich.set_text_runs(ov.get("content", ""), ov.get("runs") or [], {
                    "font_family": ov.get("font_family"),
                    "fontsize": ov.get("fontsize", 40),
                    "bold": ov.get("bold", False),
                    "italic": ov.get("italic", False),
                    "color": self._overlay_fill_hex(ov),
                })
                from PySide6.QtGui import QTextCursor
                n = len(edit.toPlainText())
                cur = edit.textCursor()
                cur.setPosition(max(0, min(n, anchor)))
                cur.setPosition(max(0, min(n, pos)), QTextCursor.MoveMode.KeepAnchor)
                edit.blockSignals(True)
                edit.setTextCursor(cur)
                edit.blockSignals(False)
        finally:
            self.block_signals = prev

    def on_change(self):
        """fx_fields._on_fx calls panel.on_change(); overlay uses on_change_ov."""
        if self.current_type in ("overlay", "sticker"):
            self.on_change_ov()

    def _ov_text_fx_groups(self):
        groups = [
            self.ov_fill_grp, self.ov_stroke_grp, self.ov_bg_grp,
            self.ov_glow_grp, self.ov_shadow_grp, self.ov_extrude_grp,
        ]
        for name in (
            "ov_fonts_grp", "ov_emphasis_grp", "ov_scatter_grp", "ov_anim_grp",
        ):
            grp = getattr(self, name, None)
            if grp is not None:
                groups.append(grp)
        return groups

    def _set_ov_tscale_visible(self, on: bool) -> None:
        if not hasattr(self, "ov_tscale"):
            return
        row = inspector_row_of(self.ov_tscale)
        if row is not None:
            row.setVisible(on)
        self.ov_tscale.setVisible(on)
        if hasattr(self, "ov_tscale_sl"):
            self.ov_tscale_sl.setVisible(on)

    def _set_ov_transition_visible(self, on: bool) -> None:
        if not hasattr(self, "ov_transition"):
            return
        for w in (
            self.ov_transition, self.ov_transition_dur, self.ov_transition_label,
        ):
            w.setVisible(on)
            row = inspector_row_of(w)
            if row is not None:
                row.setVisible(on)

    def _overlay_fill_hex(self, ov, default="#FFFFFF") -> str:
        from cutora.subtitle.text_layer import overlay_fill_color
        return overlay_fill_color(ov if isinstance(ov, dict) else {}, default)

    def _toggle_snap_store(self):
        store = getattr(self, "_ov_toggle_snaps", None)
        if store is None:
            self._ov_toggle_snaps = store = {}
        return store

    def _stash_section_toggle(self, tg) -> None:
        if tg is None:
            return
        oid = getattr(self, "_ov_toggle_id", None)
        self._toggle_snap_store()[(oid, tg.path)] = copy.deepcopy(tg._saved)

    def _restore_section_toggle(self, tg) -> None:
        if tg is None:
            return
        oid = getattr(self, "_ov_toggle_id", None)
        key = (oid, tg.path)
        store = self._toggle_snap_store()
        if key in store:
            tg._saved = copy.deepcopy(store[key])

    def _load_ov_fx(self, ov):
        from cutora.subtitle.text_layer import overlay_to_style
        from cutora.render.textfx.spec import ensure_section_enabled
        style = overlay_to_style(ov if isinstance(ov, dict) else {}, 1080, 1920)
        load_into_panel(self, style)
        fx = style.get("fx") if isinstance(style.get("fx"), dict) else {}
        ensure_section_enabled(fx)
        if isinstance(ov, dict) and isinstance(ov.get("fx"), dict):
            ensure_section_enabled(ov["fx"])
            fx = ov["fx"]
        oid = (ov or {}).get("id") if isinstance(ov, dict) else None
        oid = oid or id(ov)
        switched = getattr(self, "_ov_toggle_id", None) != oid
        self._ov_toggle_id = oid
        st = getattr(self, "_stroke_toggle", None)
        if st is not None:
            if switched:
                st.reset()
            self._restore_section_toggle(st)
            self.ov_strk.blockSignals(True)
            self.ov_strk.setChecked(st.is_on(fx))
            self.ov_strk.blockSignals(False)
        bg_tg = getattr(self, "_bg_toggle", None)
        if bg_tg is not None:
            if switched:
                bg_tg.reset()
            self._restore_section_toggle(bg_tg)
            is_bg_on = bg_tg.is_on(fx)
            self.ov_bg_enable.blockSignals(True)
            self.ov_bg_enable.setChecked(is_bg_on)
            self.ov_bg_enable.blockSignals(False)
            if is_bg_on and hasattr(self, "ov_bg_grp") and self.ov_bg_grp is not None:
                self.ov_bg_grp.setExpanded(True)
        glow_tg = getattr(self, "_glow_toggle", None)
        if glow_tg is not None and hasattr(self, "ov_glow_enable"):
            if switched:
                glow_tg.reset()
            self._restore_section_toggle(glow_tg)
            self.ov_glow_enable.blockSignals(True)
            self.ov_glow_enable.setChecked(glow_tg.is_on(fx))
            self.ov_glow_enable.blockSignals(False)
        sh_tg = getattr(self, "_shadow_toggle", None)
        if sh_tg is not None:
            if switched:
                sh_tg.reset()
            self._restore_section_toggle(sh_tg)
            self.ov_shadow_enable.blockSignals(True)
            self.ov_shadow_enable.setChecked(sh_tg.is_on(fx))
            self.ov_shadow_enable.blockSignals(False)
        self._sync_ov_section_gray()

    def _sync_ov_section_gray(self):
        pairs = (
            (getattr(self, "ov_stroke_grp", None), getattr(self, "ov_strk", None)),
            (getattr(self, "ov_bg_grp", None), getattr(self, "ov_bg_enable", None)),
            (getattr(self, "ov_glow_grp", None), getattr(self, "ov_glow_enable", None)),
            (getattr(self, "ov_shadow_grp", None), getattr(self, "ov_shadow_enable", None)),
        )
        for sec, sw in pairs:
            if sec is None or sw is None:
                continue
            if hasattr(sec, "set_content_enabled") and hasattr(sw, "isChecked"):
                sec.set_content_enabled(bool(sw.isChecked()))

    def _apply_ov_section_toggles(self, ov):
        fx = ov.get("fx") if isinstance(ov.get("fx"), dict) else {}
        from cutora.ui.panels.fx_fields import set_widget_value
        from cutora.render.textfx.spec import seed_section_defaults
        st = getattr(self, "_stroke_toggle", None)
        if st is not None:
            was_on = st.is_on(fx)
            st.apply_checkbox(fx, self.ov_strk.isChecked())
            self._stash_section_toggle(st)
            if self.ov_strk.isChecked() and not was_on:
                seed_section_defaults(fx, "stroke")
                set_widget_value(self, "stroke.width_em",
                                 (fx.get("stroke") or {}).get("width_em"))
        glow_tg = getattr(self, "_glow_toggle", None)
        if glow_tg is not None and hasattr(self, "ov_glow_enable"):
            was_on = glow_tg.is_on(fx)
            glow_tg.apply_checkbox(fx, self.ov_glow_enable.isChecked())
            self._stash_section_toggle(glow_tg)
            if self.ov_glow_enable.isChecked() and not was_on:
                seed_section_defaults(fx, "glow")
                set_widget_value(self, "glow.sigma_em",
                                 (fx.get("glow") or {}).get("sigma_em"))
        sh_tg = getattr(self, "_shadow_toggle", None)
        if sh_tg is not None:
            sh = fx.get("shadow") if isinstance(fx.get("shadow"), dict) else {}
            fx["shadow"] = sh
            was_on = sh_tg.is_on(fx)
            sh_tg.apply_checkbox(fx, self.ov_shadow_enable.isChecked())
            self._stash_section_toggle(sh_tg)
            if self.ov_shadow_enable.isChecked() and not was_on:
                from cutora.render.textfx.spec import (
                    SHADOW_ON_DISTANCE_EM, SHADOW_ON_SIZE_EM, SHADOW_ON_STRENGTH,
                )
                if float(sh.get("distance_em") or 0) <= 0:
                    sh["distance_em"] = SHADOW_ON_DISTANCE_EM
                if float(sh.get("size_em") or 0) <= 0:
                    sh["size_em"] = SHADOW_ON_SIZE_EM
                if float(sh.get("strength") or 0) <= 0:
                    sh["strength"] = SHADOW_ON_STRENGTH
        bg_tg = getattr(self, "_bg_toggle", None)
        sender = self.sender()
        if bg_tg is not None and sender is self.ov_bg_enable:
            was_on = bg_tg.is_on(fx)
            bg_tg.apply_checkbox(fx, self.ov_bg_enable.isChecked())
            self._stash_section_toggle(bg_tg)
            if self.ov_bg_enable.isChecked() and not was_on:
                seed_section_defaults(fx, "bg")
            combo = getattr(self, "fx_bg_type", None)
            if combo is not None:
                kind = (fx.get("bg") or {}).get("type") or "none"
                idx = combo.findData(kind)
                if idx >= 0:
                    combo.blockSignals(True)
                    combo.setCurrentIndex(idx)
                    combo.blockSignals(False)
        elif bg_tg is not None:
            self.ov_bg_enable.blockSignals(True)
            self.ov_bg_enable.setChecked(bg_tg.is_on(fx))
            self.ov_bg_enable.blockSignals(False)
        ov["fx"] = fx

    def _save_text_overlay(self, ov, sender) -> None:
        """Persist text-overlay fields. Keys here must be read by paint_text_overlay."""
        if ov.get("content_format", "plain") != "rich_spans":
            ov['content'] = self.ov_content.toPlainText()
        _old_fam = str(ov.get('font_family') or "")
        ov['font_family'] = sanitize_font_family(self.ov_font.currentText())
        _emph_w = getattr(self, "fx_fonts_emphasis", None)
        if (_emph_w is not None and ov['font_family'] != _old_fam
                and str(_emph_w.currentText() or "") in ("", _old_fam)):
            # 强调字体原来就是跟着正文走的：换正文字体时一起换（不然强调词还是旧字体）
            _emph_w.blockSignals(True)
            _emph_w.setCurrentText(ov['font_family'])
            _emph_w.blockSignals(False)
        if hasattr(self, "ov_tscale"):
            sc = float(self.ov_tscale.value())
            if abs(sc - 1.0) < 1e-6:
                ov.pop("scale", None)       # 1.0 就不写键，老工程一个字节不多
            else:
                ov["scale"] = sc
        from cutora.subtitle.io import canvas_size_for_window
        from cutora.subtitle.stylepack import set_fontsize_px
        cw, ch = canvas_size_for_window(self.window())
        set_fontsize_px(ov, self.ov_size.value(), cw, ch)
        ov['bold'] = self.ov_bold.isChecked()
        ov['italic'] = self.ov_italic.isChecked()
        tracking_px = int(self.ov_letter_spacing.value())
        try:
            em = max(8.0, float(self.ov_size.value() or 40))
            track_w = getattr(self, "fx_tracking_em", None)
            if track_w is not None:
                tracking_px = int(round(float(track_w.value()) * em))
        except (TypeError, ValueError):
            pass
        ov['text_layout'] = {
            "left_pad": self.ov_pad_l.value(),
            "right_pad": self.ov_pad_r.value(),
            "top_pad": self.ov_pad_t.value(),
            "bottom_pad": self.ov_pad_b.value(),
            "line_spacing": self.ov_line_spacing.value(),
            "letter_spacing": tracking_px,
            "align": _normalize_text_align(self.ov_text_align.currentText()),
            "valign": (self.ov_text_valign.currentText() or "top")
            if hasattr(self, "ov_text_valign") else "top",
            "first_indent": int(self.ov_first_indent.value()) if hasattr(self, "ov_first_indent") else 0,
            "para_spacing": int(self.ov_para_spacing.value()) if hasattr(self, "ov_para_spacing") else 0,
        }
        if hasattr(self, "ov_writing_mode"):
            ov["writing_mode"] = self.ov_writing_mode.currentData() or "horizontal"
            ov["fit_mode"] = self.ov_fit_mode.currentData() or "none"
        self._write_ov_fx(ov)

    def _write_ov_fx(self, ov):
        from cutora.subtitle.text_layer import overlay_fx_from_legacy
        if not isinstance(ov.get("fx"), dict):
            ov["fx"] = overlay_fx_from_legacy(ov)
        self._fx_dirty = True
        self._auto_key_extras("overlay")
        save_from_panel(self, ov)
        self._apply_ov_section_toggles(ov)

    def _on_ov_content_changed(self):
        if self.block_signals:
            return
        if not self.current_data or self.current_type not in ("overlay", "sticker"):
            return
        if ENABLE_RICH_TEXT and isinstance(self.current_data, dict) and self.current_data.get("content_format") == "rich_spans":
            self._ov_content_timer.start()
            return
        self.on_change_ov()

    def _flush_ov_content_change(self):
        if self.block_signals:
            return
        if not self.current_data or self.current_type not in ("overlay", "sticker"):
            return
        ov = self.current_data
        if ENABLE_RICH_TEXT and isinstance(ov, dict) and ov.get("content_format") == "rich_spans":
            self._sync_rich_from_editor(ov)
            self.param_changed.emit("rich_content")
        else:
            self.on_change_ov()

    def _ov_canvas_size(self):
        from cutora.subtitle.io import canvas_size_for_window
        return canvas_size_for_window(self.window())

    def _get_ov_preset_id(self):
        if not hasattr(self, "ov_preset_combo"):
            return ""
        data = self.ov_preset_combo.currentData()
        if data:
            return str(data).strip()
        name = self.ov_preset_combo.currentText().strip()
        if name.startswith("【默认】"):
            name = name[4:].strip()
        if not name:
            return ""
        row = text_preset_store.resolve_preset(name)
        return str((row or {}).get("id") or "")

    def _get_ov_preset_name(self):
        pid = self._get_ov_preset_id()
        row = text_preset_store.get_preset(pid) if pid else None
        return str((row or {}).get("name") or "")

    def _refresh_ov_text_presets(self, filter_cat: str | None = None):
        rows = text_preset_store.list_presets()
        default_id = text_preset_store.get_default_preset_id()
        cats = text_preset_store.list_preset_categories(rows)

        current = ""
        current_data = getattr(self, "current_data", None)
        if isinstance(current_data, dict):
            current = str(current_data.get("source_preset_id") or "")
            if not current:
                raw_name = str(current_data.get("_preset_name") or "").strip()
                if raw_name:
                    mapped = text_preset_store.resolve_preset(raw_name)
                    current = str((mapped or {}).get("id") or "")
        if not current:
            current = self._get_ov_preset_id()

        if hasattr(self, "ov_preset_cat_combo"):
            if filter_cat is None:
                filter_cat = str(self.ov_preset_cat_combo.currentData() or "")
            self.ov_preset_cat_combo.blockSignals(True)
            self.ov_preset_cat_combo.clear()
            self.ov_preset_cat_combo.addItem(f"📂 全部预设文件夹 ({len(rows)})", "")
            for c in cats:
                cnt = sum(1 for r in rows if text_preset_store.preset_category_of(r) == c)
                self.ov_preset_cat_combo.addItem(f"📁 {c} ({cnt})", c)
            idx_c = self.ov_preset_cat_combo.findData(filter_cat)
            self.ov_preset_cat_combo.setCurrentIndex(idx_c if idx_c >= 0 else 0)
            self.ov_preset_cat_combo.blockSignals(False)
            active_cat = str(self.ov_preset_cat_combo.currentData() or "")
        else:
            active_cat = str(filter_cat or "")

        filtered_rows = [
            r for r in rows
            if not active_cat or text_preset_store.preset_category_of(r) == active_cat
        ] or rows

        from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap

        def _fast_combo_swatch(row_dict: dict) -> QIcon:
            st = (row_dict.get("style") or {}) if isinstance(row_dict, dict) else {}
            fx = (st.get("fx") or {}) if isinstance(st.get("fx"), dict) else {}
            bg = (fx.get("bg") or {}) if isinstance(fx.get("bg"), dict) else {}
            fill = (fx.get("fill") or {}) if isinstance(fx.get("fill"), dict) else {}
            fill_cols = fill.get("colors") or [st.get("color") or "#FFFFFF"]
            fg_hex = str(fill_cols[0] if fill_cols else "#FFFFFF")
            bg_hex = str(bg.get("color") or st.get("bg_color") or "#1E2636")
            grad_cols = bg.get("grad_colors") or []
            bg2_hex = str(grad_cols[1]) if len(grad_cols) >= 2 else bg_hex
            sw_key = ("combo_sw", fg_hex, bg_hex, bg2_hex)
            cached = getattr(self, "_combo_sw_cache", None)
            if cached is None:
                cached = {}
                self._combo_sw_cache = cached
            if sw_key in cached:
                return cached[sw_key]
            pm = QPixmap(44, 20)
            pm.fill(Qt.GlobalColor.transparent)
            p = QPainter(pm)
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            p.setPen(QColor("#3E4A61"))
            p.setBrush(QColor(bg_hex))
            p.drawRoundedRect(1, 1, 42, 18, 5, 5)
            if bg2_hex != bg_hex:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(bg2_hex))
                p.drawRoundedRect(22, 2, 20, 16, 4, 4)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(fg_hex))
            p.drawRoundedRect(10, 7, 24, 6, 3, 3)
            p.end()
            ic = QIcon(pm)
            cached[sw_key] = ic
            return ic

        self.ov_preset_combo.blockSignals(True)
        self.ov_preset_combo.clear()
        for row in filtered_rows:
            pid = row.get("id") or ""
            name = row.get("name") or pid
            cat = text_preset_store.preset_category_of(row)
            prefix = "【默认】 " if pid == default_id else ""
            cat_tag = "" if active_cat else f"[{cat[:2]}] "
            display_name = f"{prefix}{cat_tag}{name}"
            try:
                self.ov_preset_combo.addItem(_fast_combo_swatch(row), display_name, pid)
            except Exception:
                self.ov_preset_combo.addItem(display_name, pid)

        if current:
            idx = self.ov_preset_combo.findData(current)
            if idx >= 0:
                self.ov_preset_combo.setCurrentIndex(idx)
            elif default_id:
                idx = self.ov_preset_combo.findData(default_id)
                if idx >= 0:
                    self.ov_preset_combo.setCurrentIndex(idx)
            elif filtered_rows:
                self.ov_preset_combo.setCurrentIndex(0)
        else:
            idx = self.ov_preset_combo.findData(default_id) if default_id else -1
            if idx >= 0:
                self.ov_preset_combo.setCurrentIndex(idx)
            elif filtered_rows:
                self.ov_preset_combo.setCurrentIndex(0)
        self.ov_preset_combo.blockSignals(False)

    def _on_ov_preset_cat_changed(self):
        if not hasattr(self, "ov_preset_cat_combo"):
            return
        cat = str(self.ov_preset_cat_combo.currentData() or "")
        self._refresh_ov_text_presets(filter_cat=cat)

    def _open_ov_preset_gallery(self):
        win = self.window()
        if win is not None and hasattr(win, "_open_text_style_gallery"):
            from PySide6.QtCore import QPoint
            btn = getattr(self, "ov_preset_gallery_btn", None) or self
            anchor = btn.mapToGlobal(QPoint(0, btn.height() + 4))
            gal = win._open_text_style_gallery(anchor=anchor)
            if gal is not None and hasattr(self, "ov_preset_cat_combo"):
                cat = str(self.ov_preset_cat_combo.currentData() or "")
                if hasattr(gal, "set_category"):
                    gal.set_category(cat)

    def _on_ov_text_preset_changed(self):
        if self.block_signals or self.current_type != 'overlay':
            return
        if not isinstance(self.current_data, dict) or self.current_data.get('type') != 'text':
            return
        preset_id = self.ov_preset_combo.currentData()
        if not preset_id:
            return
        row = text_preset_store.resolve_preset(preset_id)
        if row is None:
            return
        template = text_preset_store.template_from_preset(row)
        apply_text_template_to_overlay(self.current_data, template)
        self.current_data["_preset_name"] = row.get("name") or ""
        self.current_data["source_preset_id"] = row.get("id") or ""
        self.load_data(self.current_type, self.current_data)
        self.param_changed.emit(None)

    def _ov_save_current_as(self, name, preset_id=None):
        cw, ch = self._ov_canvas_size()
        row = text_preset_store.preset_from_overlay(
            self.current_data, cw, ch, name=name, preset_id=preset_id)
        saved = text_preset_store.save_preset(row)
        if isinstance(self.current_data, dict):
            self.current_data["_preset_name"] = saved.get("name") or name
            self.current_data["source_preset_id"] = saved.get("id") or ""
        return saved

    def _ov_preset_save_default(self):
        if not isinstance(self.current_data, dict) or self.current_data.get('type') != 'text':
            return
        preset_id = self.ov_preset_combo.currentData()
        if not preset_id:
            QMessageBox.information(self, "提示", "请先选择一个预设然后再设为默认")
            return
        row = text_preset_store.get_preset(preset_id)
        if row is None:
            QMessageBox.warning(self, "错误", "设置默认预设失败")
            return
        if text_preset_store.set_default_preset_id(preset_id):
            QMessageBox.information(self, "成功", "【%s】已被设为默认预设" % (row.get("name") or preset_id))
            self._refresh_ov_text_presets()
        else:
            QMessageBox.warning(self, "错误", "设置默认预设失败")

    def _ov_preset_add(self):
        if not isinstance(self.current_data, dict) or self.current_data.get('type') != 'text':
            return
        name, ok = QInputDialog.getText(self, "新增预设", "请输入预设名称:")
        if not ok or not name:
            return
        existing = next((p for p in text_preset_store.list_presets() if p.get("name") == name), None)
        if existing:
            ret = QMessageBox.question(self, "覆盖预设", "预设 '%s' 已存在，是否覆盖？" % name)
            if ret != QMessageBox.StandardButton.Yes:
                return
        try:
            saved = self._ov_save_current_as(name, existing["id"] if existing else None)
        except ValueError as exc:
            QMessageBox.warning(self, "失败", str(exc))
            return
        self._refresh_ov_text_presets()
        idx = self.ov_preset_combo.findData(saved.get("id"))
        if idx >= 0:
            self.ov_preset_combo.setCurrentIndex(idx)

    def _ov_preset_update(self):
        if not isinstance(self.current_data, dict) or self.current_data.get('type') != 'text':
            return
        preset_id = self.ov_preset_combo.currentData()
        if not preset_id:
            return
        row = text_preset_store.get_preset(preset_id)
        if row is None:
            return
        if row.get("builtin") and not any(p["id"] == preset_id for p in text_preset_store.load_user()):
            QMessageBox.information(self, "提示", "内置预设不能覆盖，请用「新增预设」另存")
            return
        try:
            saved = self._ov_save_current_as(row.get("name") or "", preset_id)
        except ValueError as exc:
            QMessageBox.warning(self, "失败", str(exc))
            return
        self._refresh_ov_text_presets()
        idx = self.ov_preset_combo.findData(saved.get("id"))
        if idx >= 0:
            self.ov_preset_combo.setCurrentIndex(idx)

    def _ov_preset_rename(self):
        preset_id = self.ov_preset_combo.currentData()
        if not preset_id:
            return
        row = text_preset_store.get_preset(preset_id)
        if row is None:
            return
        if row.get("builtin") and not any(p["id"] == preset_id for p in text_preset_store.load_user()):
            QMessageBox.information(self, "提示", "内置预设不能重命名")
            return
        old_name = row.get("name") or ""
        new_name, ok = QInputDialog.getText(self, "重命名预设", "请输入新名称:", text=old_name)
        if not ok or not new_name or new_name == old_name:
            return
        if any(p.get("name") == new_name and p.get("id") != preset_id for p in text_preset_store.list_presets()):
            QMessageBox.warning(self, "失败", "新名称已存在")
            return
        try:
            row["name"] = new_name
            text_preset_store.save_preset(row)
        except ValueError as exc:
            QMessageBox.warning(self, "失败", str(exc))
            return
        if isinstance(self.current_data, dict) and self.current_data.get("source_preset_id") == preset_id:
            self.current_data["_preset_name"] = new_name
        self._refresh_ov_text_presets()
        idx = self.ov_preset_combo.findData(preset_id)
        if idx >= 0:
            self.ov_preset_combo.setCurrentIndex(idx)

    def _ov_preset_delete(self):
        preset_id = self.ov_preset_combo.currentData()
        if not preset_id:
            return
        try:
            ok = text_preset_store.delete_preset(preset_id)
        except ValueError as exc:
            QMessageBox.warning(self, "失败", str(exc))
            return
        if not ok:
            QMessageBox.warning(self, "失败", "删除预设失败")
            return
        if isinstance(self.current_data, dict) and self.current_data.get("source_preset_id") == preset_id:
            self.current_data.pop("source_preset_id", None)
            self.current_data.pop("_preset_name", None)
        if text_preset_store.get_default_preset_id() == preset_id:
            text_preset_store.set_default_preset_id(text_preset_store.DEFAULT_PRESET_ID)
        self._refresh_ov_text_presets()
        self._on_ov_text_preset_changed()

    def _ov_bus(self):
        win = self.window()
        return getattr(win, "bus", None)

    def _ov_save_text_layer_preset(self):
        if not isinstance(self.current_data, dict) or self.current_data.get("type") != "text":
            QMessageBox.information(self, "提示", "请先选中一条文本覆层")
            return
        bus = self._ov_bus()
        if bus is None:
            return
        name, ok = QInputDialog.getText(self, "存为文本层预设", "名称:")
        if not ok or not str(name).strip():
            return
        try:
            bus.execute(
                "text_preset.save",
                {"name": str(name).strip(), "overlay": dict(self.current_data)},
                source="ui")
        except Exception as exc:
            QMessageBox.warning(self, "失败", str(exc))
            return
        self._refresh_ov_text_presets()
        QMessageBox.information(self, "成功", "已保存文本层预设")

    def _ov_apply_text_layer_preset(self):
        bus = self._ov_bus()
        if bus is None:
            return
        try:
            listed = bus.execute("text_preset.list", {}, source="ui") or {}
        except Exception as exc:
            QMessageBox.warning(self, "失败", str(exc))
            return
        rows = listed.get("presets") or []
        if not rows:
            QMessageBox.information(self, "提示", "还没有文本层预设")
            return
        labels = [str(r.get("name") or r.get("id")) for r in rows]
        from PySide6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getItem(self, "应用文本层预设", "新建一条覆层：", labels, 0, False)
        if not ok or not name:
            return
        pid = next((r.get("id") for r in rows if str(r.get("name") or r.get("id")) == name), None)
        if not pid:
            return
        try:
            bus.execute("text_preset.apply", {"id": pid, "idempotent": False}, source="ui")
        except Exception as exc:
            QMessageBox.warning(self, "失败", str(exc))

    def _ov_preset_export(self):
        dest, _ = QFileDialog.getSaveFileName(self, "导出预设", "text_presets.json", "JSON (*.json)")
        if not dest:
            return
        if text_preset_store.export_presets(dest):
            QMessageBox.information(self, "成功", f"预设已导出到 {dest}")
        else:
            QMessageBox.warning(self, "失败", "导出预设失败")

    def _ov_preset_import(self):
        src, _ = QFileDialog.getOpenFileName(self, "导入预设", "", "JSON (*.json)")
        if not src:
            return
        result = text_preset_store.import_presets(src)
        conflicts = result.get("conflicts") or {}
        overwritten = []
        if conflicts:
            ret = QMessageBox.question(self, "名称冲突", f"发现 {len(conflicts)} 个同名预设，是否覆盖？")
            if ret == QMessageBox.StandardButton.Yes:
                for name, row in conflicts.items():
                    try:
                        if isinstance(row, dict) and row.get("id"):
                            text_preset_store.save_preset(row, overwrite=True)
                        else:
                            cw, ch = self._ov_canvas_size()
                            built = text_preset_store.preset_from_overlay(
                                row if isinstance(row, dict) else {}, cw, ch, name=str(name))
                            text_preset_store.save_preset(built, overwrite=True)
                        overwritten.append(name)
                    except ValueError:
                        pass
        self._refresh_ov_text_presets()
        msg = f"新增 {len(result.get('added') or [])} 个预设"
        if overwritten:
            msg += f"，覆盖 {len(overwritten)} 个"
        if result.get("skipped"):
            msg += f"，跳过 {len(result.get('skipped'))} 个"
        QMessageBox.information(self, "完成", msg)

    def _on_ov_preset_edit_finished(self):
        if self.block_signals:
            return
        preset_id = self.ov_preset_combo.currentData()
        new_name = self.ov_preset_combo.lineEdit().text().strip()
        self.ov_preset_combo.lineEdit().setReadOnly(True)
        if not self._editing_preset_name:
            return
        self._editing_preset_name = False
        row = text_preset_store.get_preset(preset_id) if preset_id else None
        if not row or not new_name or new_name == row.get("name"):
            self._refresh_ov_text_presets()
            return
        if row.get("builtin") and not any(p["id"] == preset_id for p in text_preset_store.load_user()):
            self._refresh_ov_text_presets()
            return
        if any(p.get("name") == new_name and p.get("id") != preset_id for p in text_preset_store.list_presets()):
            self._refresh_ov_text_presets()
            return
        try:
            row["name"] = new_name
            text_preset_store.save_preset(row)
        except ValueError:
            pass
        self._refresh_ov_text_presets()
        idx = self.ov_preset_combo.findData(preset_id)
        if idx >= 0:
            self.ov_preset_combo.setCurrentIndex(idx)

    def _load_fade_widgets(self, prefix: str, obj) -> None:
        from cutora.render.textfx.fade import fade_offset_tuple, fade_tuple
        top, bottom, left, right = fade_tuple(obj)
        getattr(self, "%s_fade_top" % prefix).setValue(int(round(top * 100.0)))
        getattr(self, "%s_fade_bottom" % prefix).setValue(int(round(bottom * 100.0)))
        getattr(self, "%s_fade_left" % prefix).setValue(int(round(left * 100.0)))
        getattr(self, "%s_fade_right" % prefix).setValue(int(round(right * 100.0)))
        toff, boff, loff, roff = fade_offset_tuple(obj)
        getattr(self, "%s_fade_top_offset" % prefix).setValue(int(round(toff * 100.0)))
        getattr(self, "%s_fade_bottom_offset" % prefix).setValue(int(round(boff * 100.0)))
        getattr(self, "%s_fade_left_offset" % prefix).setValue(int(round(loff * 100.0)))
        getattr(self, "%s_fade_right_offset" % prefix).setValue(int(round(roff * 100.0)))
        setattr(self, "_fade_committed_%s" % prefix, (
            top, bottom, left, right, toff, boff, loff, roff))
        sync = getattr(self, "%s_fade_sync" % prefix, None)
        self._set_fade_master_visible(prefix, bool(sync is not None and sync.isChecked()))
        master = getattr(self, "%s_fade_master" % prefix, None)
        if master is not None:
            # 总控只是四边的显示值：不管「四边一起调」勾没勾，都按四边重新算一遍，
            # 撤销 / 换对象后才不会停在旧值上
            blocked = master.blockSignals(True)
            try:
                master.setValue(self._fade_link_seed(prefix))
            finally:
                master.blockSignals(blocked)

    def _fade_from_widgets(self, prefix: str) -> tuple:
        return (
            getattr(self, "%s_fade_top" % prefix).value() / 100.0,
            getattr(self, "%s_fade_bottom" % prefix).value() / 100.0,
            getattr(self, "%s_fade_left" % prefix).value() / 100.0,
            getattr(self, "%s_fade_right" % prefix).value() / 100.0,
        )

    def _fade_offsets_from_widgets(self, prefix: str) -> tuple:
        return (
            getattr(self, "%s_fade_top_offset" % prefix).value() / 100.0,
            getattr(self, "%s_fade_bottom_offset" % prefix).value() / 100.0,
            getattr(self, "%s_fade_left_offset" % prefix).value() / 100.0,
            getattr(self, "%s_fade_right_offset" % prefix).value() / 100.0,
        )

    def _on_ov_fade(self, *_a):
        self._sync_fade_sliders("ov")
        self.on_change_ov()

    def _on_vid_fade(self, *_a):
        self._sync_fade_sliders("vid")
        self.on_change_video()

    def _index_of_overlay(self, task, ov) -> int:
        if not isinstance(task, dict) or ov is None:
            return -1
        ovs = task.get("image_overlays") or []
        for i, item in enumerate(ovs):
            if item is ov:
                return i
        return -1

    def _index_of_element(self, ov, el) -> int:
        if not isinstance(ov, dict) or el is None:
            return -1
        for i, item in enumerate(ov.get("elements") or []):
            if item is el:
                return i
        return -1

    def _commit_fade_command(self) -> None:
        if self.block_signals:
            return
        from cutora.core.commands import get_bus
        from cutora.render.textfx.fade import write_fade
        win = self.window()
        task = None
        if hasattr(win, "tasks"):
            idx = int(getattr(win, "curr_idx", -1) or -1)
            tasks = list(getattr(win, "tasks", None) or [])
            if 0 <= idx < len(tasks):
                task = tasks[idx]
        params = None
        prefix = None
        target_obj = None
        if self.current_type in ("overlay", "sticker") and isinstance(self.current_data, dict):
            top, bottom, left, right = self._fade_from_widgets("ov")
            toff, boff, loff, roff = self._fade_offsets_from_widgets("ov")
            prefix = "ov"
            target_obj = self.current_data
            fade_kw = {
                "fade_top": top, "fade_bottom": bottom,
                "fade_left": left, "fade_right": right,
                "fade_top_offset": toff, "fade_bottom_offset": boff,
                "fade_left_offset": loff, "fade_right_offset": roff,
            }
            if self.current_type == "overlay":
                oidx = self._index_of_overlay(task, self.current_data)
                if oidx >= 0:
                    params = {"target": "overlay", "overlay_index": oidx, **fade_kw}
            else:
                parent = getattr(self, "_ov_parent_overlay", None)
                oidx = self._index_of_overlay(task, parent)
                eidx = self._index_of_element(parent, self.current_data)
                if oidx >= 0 and eidx >= 0:
                    params = {
                        "target": "element", "overlay_index": oidx,
                        "element_index": eidx, **fade_kw,
                    }
        elif self.current_type == "video" and self._video_clip is not None:
            top, bottom, left, right = self._fade_from_widgets("vid")
            toff, boff, loff, roff = self._fade_offsets_from_widgets("vid")
            prefix = "vid"
            target_obj = self._video_clip
            track = self._video_track
            tid = getattr(track, "id", None) if track is not None else None
            clip_index = -1
            if track is not None:
                clips = list(getattr(track, "clips", None) or [])
                for i, c in enumerate(clips):
                    if c is self._video_clip:
                        clip_index = i
                        break
            if tid is not None and clip_index >= 0:
                params = {
                    "target": "clip", "track_id": str(tid),
                    "clip_index": clip_index,
                    "fade_top": top, "fade_bottom": bottom,
                    "fade_left": left, "fade_right": right,
                    "fade_top_offset": toff, "fade_bottom_offset": boff,
                    "fade_left_offset": loff, "fade_right_offset": roff,
                }
        if not params:
            return
        # Live drag already wrote the new values. Restore the last committed
        # tuple so push_undo snapshots the pre-drag state.
        old = getattr(self, "_fade_committed_%s" % prefix, None) if prefix else None
        if old is not None and target_obj is not None:
            if len(old) >= 8:
                write_fade(target_obj, *old[:4], offsets=old[4:8])
            else:
                write_fade(target_obj, *old[:4])
        try:
            get_bus().execute("overlay.set_fade", params, source="ui")
            if prefix:
                setattr(self, "_fade_committed_%s" % prefix, (
                    top, bottom, left, right, toff, boff, loff, roff))
        except Exception:
            pass

    def _set_ov_lock(self, locked):
        on = bool(locked)
        if hasattr(self, "ov_keep_aspect"):
            self.ov_keep_aspect.setChecked(on)
        if hasattr(self, "ov_lock_btn"):
            self.ov_lock_btn.setChecked(on)
            bind_icon_safe(
                self.ov_lock_btn, "link" if on else "unlink",
                "accent" if on else "fg2")

    def _restore_ov_aspect(self):
        """Put the box back on the file's ratio. Width stays."""
        if self.current_type not in ("overlay", "sticker"):
            return
        host = self.current_data
        if not isinstance(host, dict):
            return
        from cutora.model.project_schema import native_aspect_ratio
        parent = getattr(self, "_ov_parent_overlay", None) if self.current_type == "sticker" else None
        ratio = native_aspect_ratio(host, parent)
        if not ratio or ratio <= 0:
            return
        w = max(1, int(self.ov_w.value()))
        h = max(1, int(round(float(w) / float(ratio))))
        self.block_signals = True
        try:
            self.ov_w.setValue(w)
            self.ov_h.setValue(h)
        finally:
            self.block_signals = False
        host["w"] = w
        host["h"] = h
        self.param_changed.emit(None)

    def _on_vid_lock_toggled(self, on):
        bind_icon_safe(
            self.vid_lock_btn, "link" if on else "unlink",
            "accent" if on else "fg2")
        clip = getattr(self, "_video_clip", None)
        if self.block_signals or clip is None:
            return
        if isinstance(clip, dict):
            clip["lock_aspect"] = bool(on)
        else:
            clip.lock_aspect = bool(on)
        self.param_changed.emit(clip)

    def _restore_video_aspect(self):
        """Drop the extra height scale. Width (scale) stays."""
        clip = getattr(self, "_video_clip", None)
        if clip is None:
            return
        if isinstance(clip, dict):
            clip["scale_y"] = 1.0
        else:
            clip.scale_y = 1.0
        self.param_changed.emit(clip)

    def on_change_ov(self):
        if self.block_signals or not self.current_data: return
        if self.current_type not in ('overlay', 'sticker'): return
        ov = self.current_data
        sender = self.sender()
        if self.current_type == 'overlay' and ov.get('type') == 'image' and self.ov_keep_aspect.isChecked():
            w = self.ov_w.value()
            h = self.ov_h.value()
            ratio = ov.get('aspect_ratio')
            if not isinstance(ratio, (int, float)) or ratio <= 0:
                ratio = w / h if h else 1.0
            if sender in (self.ov_w, self.ov_w_sl):
                new_h = max(1, int(w / ratio))
                if new_h != h:
                    self.block_signals = True
                    self.ov_h.setValue(new_h)
                    self.block_signals = False
            elif sender in (self.ov_h, self.ov_h_sl):
                new_w = max(1, int(h * ratio))
                if new_w != w:
                    self.block_signals = True
                    self.ov_w.setValue(new_w)
                    self.block_signals = False
            ov['aspect_ratio'] = ratio
        if self.current_type == 'sticker' and ov.get('kind') == 'image' and self.ov_keep_aspect.isChecked():
            # [FIX-IMG] 图片元素调整 W/H 时按原始比例联动，避免拉伸变形
            _p_ov = getattr(self, '_ov_parent_overlay', None)
            w = self.ov_w.value()
            h = self.ov_h.value()
            ratio = ov.get('aspect_ratio')
            if not isinstance(ratio, (int, float)) or ratio <= 0:
                ratio = _p_ov.get('aspect_ratio') if isinstance(_p_ov, dict) else None
            if not isinstance(ratio, (int, float)) or ratio <= 0:
                ratio = (w / h) if h else 1.0
            if sender in (self.ov_w, self.ov_w_sl):
                new_h = max(1, int(round(w / ratio)))
                if new_h != h:
                    self.block_signals = True
                    self.ov_h.setValue(new_h)
                    self.block_signals = False
            elif sender in (self.ov_h, self.ov_h_sl):
                new_w = max(1, int(round(h * ratio)))
                if new_w != w:
                    self.block_signals = True
                    self.ov_w.setValue(new_w)
                    self.block_signals = False
            ov['aspect_ratio'] = ratio

        kf_path = self._ov_kf_path_for_sender()
        if kf_path:
            kf_host = self._kf_scale_host() if kf_path == "scale" else ov
            if isinstance(kf_host, dict):
                self._auto_key(kf_host, kf_path, self._overlay_kf_value(kf_path))

        ov['x'] = self.ov_x.value()
        ov['y'] = self.ov_y.value()
        ov['w'] = self.ov_w.value()
        ov['h'] = self.ov_h.value()
        ov['rotation'] = self.ov_rot.value()
        ov['opacity'] = self.ov_op.value()
        if ov.get("type") == "image" or self.current_type == "sticker":
            from cutora.render.textfx.fade import write_fade
            write_fade(
                ov, *self._fade_from_widgets("ov"),
                offsets=self._fade_offsets_from_widgets("ov"))
        if (
            self.current_type == 'overlay'
            and ov.get("type") != "text"
            and hasattr(self, "ov_transition")
        ):
            preset = self.ov_transition.currentData()
            ov['transition_preset'] = preset if preset else "none"
            if hasattr(self, "ov_transition_dur"):
                ov["transition_duration"] = float(self.ov_transition_dur.value())
            if "transition_duration" not in ov:
                ov["transition_duration"] = anim_engine.DEFAULT_TRANSITION_DURATION
        # [FIX-IMG] 图片元素的转场写回父覆层
        if self.current_type == 'sticker' and ov.get('kind') == 'image' and hasattr(self, "ov_transition"):
            _p_ov = getattr(self, '_ov_parent_overlay', None)
            if isinstance(_p_ov, dict) and self.ov_transition.isVisible():
                _pr = self.ov_transition.currentData()
                _p_ov['transition_preset'] = _pr if _pr else "none"
                _p_ov['transition_duration'] = float(self.ov_transition_dur.value())
        if self.current_type == 'overlay':
            if ov['type'] == 'text':
                self._save_text_overlay(ov, sender)
            elif ov['type'] == 'image':
                ov['scale'] = float(self.ov_scale.value())
                ov['flip_x'] = self.ov_flip_x.isChecked()
                ov['flip_y'] = self.ov_flip_y.isChecked()
                locked = self.ov_keep_aspect.isChecked()
                ov['lock_aspect'] = locked
                ov['keep_aspect'] = locked
                ov['feather'] = self.ov_feather.value()
        else:
            # sticker: nested image element on an image overlay
            if ov.get("sticker_id"):
                ov["anim_in"] = self.ov_sticker_anim_in.currentData() or "none"
                ov["anim_out"] = self.ov_sticker_anim_out.currentData() or "none"
            parent_ov = getattr(self, '_ov_parent_overlay', None)
            if parent_ov is not None:
                parent_ov['scale'] = float(self.ov_scale.value())
                parent_ov['flip_x'] = self.ov_flip_x.isChecked()
                parent_ov['flip_y'] = self.ov_flip_y.isChecked()
                locked = self.ov_keep_aspect.isChecked()
                ov['lock_aspect'] = locked
                parent_ov['lock_aspect'] = locked
                parent_ov['keep_aspect'] = locked
                parent_ov['feather'] = self.ov_feather.value()
        self.param_changed.emit(None)

    def set_audio_targets(self, clips):
        """Multi-selected audio clips; spatial checkbox writes all of them."""
        self.audio_targets = [c for c in (clips or []) if c is not None]

    def sync_audio_volume_linear(self, linear):
        """Role fader wrote clip.gain_db; mirror the same linear value."""
        if not hasattr(self, "audio_vol"):
            return
        if getattr(self, "current_type", None) != "audio":
            return
        try:
            linear = float(linear)
        except (TypeError, ValueError):
            return
        was = bool(getattr(self, "block_signals", False))
        self.block_signals = True
        try:
            self.audio_vol.blockSignals(True)
            if hasattr(self, "audio_vol_sl"):
                self.audio_vol_sl.blockSignals(True)
            self.audio_vol.setValue(linear)
            if hasattr(self, "audio_vol_sl"):
                scale = max(1, int(round(1.0 / max(self.audio_vol.singleStep(), 1e-6))))
                self.audio_vol_sl.setValue(int(round(linear * scale)))
        finally:
            self.audio_vol.blockSignals(False)
            if hasattr(self, "audio_vol_sl"):
                self.audio_vol_sl.blockSignals(False)
            self.block_signals = was

    def _spatial_clips(self):
        out = []
        seen = set()
        for c in [self.current_data] + list(getattr(self, "audio_targets", None) or []):
            if c is None:
                continue
            i = id(c)
            if i in seen:
                continue
            seen.add(i)
            out.append(c)
        return out

    def _sync_spatial_wet_enabled(self):
        on = bool(self.audio_spatial_enable.isChecked()) if hasattr(self, "audio_spatial_enable") else False
        if hasattr(self, "audio_spatial_amount"):
            self.audio_spatial_amount.setEnabled(on)
        if hasattr(self, "audio_spatial_amount_sl"):
            self.audio_spatial_amount_sl.setEnabled(on)

    def _audio_clip_ref(self):
        ac = self.current_data
        if ac is None or not hasattr(ac, "gain_db"):
            return None
        _win, task = self._win_and_task()
        tl = task.get("timeline") if isinstance(task, dict) else None
        if tl is None:
            return None
        for tr in getattr(tl, "tracks", []) or []:
            bag = list(getattr(tr, "clips", []) or [])
            for i, c in enumerate(bag):
                if c is ac:
                    return str(getattr(tr, "id", "") or ""), i
        return None

    def _commit_audio_fades(self, fi, fo):
        ac = self.current_data
        ref = self._audio_clip_ref()
        if ref is None:
            if hasattr(ac, "fade_in_dur"):
                ac.fade_in_dur = float(fi)
                ac.fade_out_dur = float(fo)
            elif isinstance(ac, dict):
                ac["fade_in_dur"] = float(fi)
                ac["fade_out_dur"] = float(fo)
            return
        from cutora.core.commands import get_bus
        try:
            get_bus().execute("timeline.set_clip_fades", {
                "track_id": ref[0], "clip_index": ref[1],
                "fade_in_dur": float(fi), "fade_out_dur": float(fo),
            }, source="ui")
        except Exception:
            if hasattr(ac, "fade_in_dur"):
                ac.fade_in_dur = float(fi)
                ac.fade_out_dur = float(fo)

    def on_change_audio(self):
        if self.block_signals or not self.current_data: return
        if self.current_type != 'audio': return
        ac = self.current_data
        vol = float(self.audio_vol.value())
        fi = float(self.audio_fade_in_dur.value())
        fo = float(self.audio_fade_out_dur.value())
        # 写入 Clip 对象（gain_db）或旧 dict（volume）
        if hasattr(ac, 'gain_db'):
            gain = 20.0 * math.log10(max(vol, 1e-6)) if vol > 0 else -120.0
            self._auto_key(ac, "gain_db", gain)
            ac.gain_db = gain
            ac.pitch_semitones = float(self.audio_pitch.value())
            ac.speed = float(self.audio_speed.value())
            old_fi = float(getattr(ac, "fade_in_dur", 0.0) or 0.0)
            old_fo = float(getattr(ac, "fade_out_dur", 0.0) or 0.0)
            if abs(fi - old_fi) > 1e-6 or abs(fo - old_fo) > 1e-6:
                self._commit_audio_fades(fi, fo)
        else:
            ac['volume'] = vol
            ac['pitch_semitones'] = float(self.audio_pitch.value())
            ac['speed'] = float(self.audio_speed.value())
            old_fi = float(ac.get("fade_in_dur", 0.0) or 0.0)
            old_fo = float(ac.get("fade_out_dur", 0.0) or 0.0)
            if abs(fi - old_fi) > 1e-6 or abs(fo - old_fo) > 1e-6:
                self._commit_audio_fades(fi, fo)
        spat_senders = {
            getattr(self, "audio_spatial_enable", None),
            getattr(self, "audio_spatial_amount", None),
            getattr(self, "audio_spatial_amount_sl", None),
        }
        amt = getattr(self, "audio_spatial_amount", None)
        if amt is not None:
            spat_senders.add(amt.parent())
        if self.sender() in spat_senders or self.sender() is None:
            spat_on = bool(self.audio_spatial_enable.isChecked())
            spat_amt = float(self.audio_spatial_amount.value())
            self._sync_spatial_wet_enabled()
            for target in self._spatial_clips():
                if hasattr(target, "spatial_enabled"):
                    target.spatial_enabled = spat_on
                    target.spatial_amount = spat_amt
                else:
                    target["spatial_enabled"] = spat_on
                    target["spatial_amount"] = spat_amt
        self._write_audio_proc(ac)
        self.param_changed.emit(ac)

    def _toggle_speed_curve(self):
        ac = self.current_data
        if ac is None or not hasattr(ac, "speed_curve"):
            return
        if getattr(ac, "speed_curve", None):
            ac.speed_curve = []
            self.btn_speed_curve.setText("添加速度曲线")
        else:
            from cutora.audio.speed_curve import default_curve
            span = max(0.2, float(getattr(ac, "out_t", 1) or 1) - float(getattr(ac, "in_t", 0) or 0))
            ac.speed_curve = default_curve(span)
            self.btn_speed_curve.setText("清除速度曲线")
        self.param_changed.emit(ac)

    def _clear_stem_proc(self):
        ac = self.current_data
        if ac is None:
            return
        proc = ac.get("audio_proc") if isinstance(ac, dict) else getattr(ac, "audio_proc", None)
        if not isinstance(proc, dict) or not str(proc.get("op") or "").startswith("stem_"):
            return
        if isinstance(ac, dict):
            ac.pop("audio_proc", None)
        else:
            ac.audio_proc = {}
        if hasattr(self, "audio_stem_status"):
            self.audio_stem_status.setText("未分离")
        self.param_changed.emit(ac)

    def _write_audio_proc(self, ac):
        box = getattr(self, "audio_denoise", None)
        strength = getattr(self, "audio_denoise_strength", None)
        if box is None or strength is None or ac is None:
            return
        from cutora.audio.voice_presets import proc_for
        old = ac.get("audio_proc") if isinstance(ac, dict) else getattr(ac, "audio_proc", None)
        op = str(old.get("op") or "") if isinstance(old, dict) else ""
        if op.startswith("stem_") and str(box.currentData() or "off") == "off":
            return
        new = proc_for(box.currentData() or "off", strength.currentData() or "mid")
        if isinstance(old, dict) and old.get("path") and new:
            if old.get("preset") == new.get("preset") and old.get("strength") == new.get("strength"):
                new = dict(new)
                new["path"] = old.get("path")
                if isinstance(old.get("params"), dict):
                    new["params"] = dict(old.get("params") or new.get("params") or {})
        if isinstance(ac, dict):
            if new:
                ac["audio_proc"] = new
            else:
                ac.pop("audio_proc", None)
        elif hasattr(ac, "audio_proc"):
            ac.audio_proc = new

    def pick_c_ov(self, key):
        c = QColorDialog.getColor()
        if not c.isValid():
            return
        if not ENABLE_RICH_TEXT:
            self.current_data[key] = c.name()
            self.param_changed.emit(None)
            return
        if isinstance(self.current_data, dict) and self.current_data.get("content_format") == "rich_spans":
            fmt = QTextCharFormat()
            if key == "color":
                fmt.setForeground(c)
            elif key == "bg_color":
                fmt.setBackground(c)
            elif key == "stroke_color":
                fmt.setProperty(_RT_PROP_STROKE_COLOR, c.name())
            elif key == "shadow_color":
                fmt.setProperty(_RT_PROP_SHADOW_COLOR, c.name())
            else:
                self.current_data[key] = c.name()
                self.param_changed.emit(None)
                return
            self._apply_rich_char_format(fmt)
            self._sync_rich_from_editor(self.current_data)
            self.param_changed.emit(None)
            return
        self.current_data[key] = c.name()
        self.param_changed.emit(None)

import cutora.ui.panels.subtitle_style as _t01_bind_ui_panels_subtitle_style
_t01_bind_ui_panels_subtitle_style.ColorOptionPanel = ColorOptionPanel
