# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Inspector grade editor: wheels + sliders + LUT. Q25 row layout."""
from __future__ import annotations

import os

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractButton,
    QButtonGroup,
    QFileDialog,
    QStackedWidget,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from cutora.core.log import get_logger
from cutora.render.grade import (
    HUE_BANDS,
    identity_grade,
    normalize_grade,
    user_luts_dir,
)
from cutora.ui.i18n import tr
from cutora.ui.theme.tokens import (
    INSPECTOR_PAIR_GAP,
    INSPECTOR_CONTROL_H,
    INSPECTOR_FIELD_GAP,
    INSPECTOR_WHEEL_MAX_PX,
    INSPECTOR_WHEEL_MIN_PX,
    current,
    INSPECTOR_PAD_X,
    INSPECTOR_ROW_GAP,
    INSPECTOR_SECTION_GAP,
)
from cutora.ui.widgets.collapsible import CollapsibleSection
from cutora.ui.widgets.inspector import (
    ColorWheel,
    InspectForm,
    SliderSpin,
    add_inspect_row,
    install_inspector_limits,
)

_LOG = get_logger("cutora.ui.panels.grade")
_HUE_ZH = {
    "red": "红",
    "yellow": "黄",
    "green": "绿",
    "cyan": "青",
    "blue": "蓝",
    "magenta": "品红",
}


_HUE_DEG = {"red": 0, "yellow": 55, "green": 120, "cyan": 185, "blue": 225, "magenta": 300}


class _HueChip(QAbstractButton):
    """Round colour chip for picking an HSL band. A dot marks edited bands."""

    def __init__(self, band, parent=None):
        super().__init__(parent)
        self.band = band
        self.edited = False
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(tr(_HUE_ZH.get(band, band)))
        self.setFixedSize(INSPECTOR_CONTROL_H, INSPECTOR_CONTROL_H)

    def sizeHint(self) -> QSize:
        return QSize(INSPECTOR_CONTROL_H, INSPECTOR_CONTROL_H)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        try:
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            tok = current()
            r = QRectF(self.rect()).adjusted(4, 4, -4, -4)
            col = QColor.fromHsv(_HUE_DEG.get(self.band, 0), 190, 225)
            if not self.isEnabled():
                col.setAlpha(90)
            if self.isChecked():
                p.setPen(QPen(QColor(tok.fg0), 2))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawEllipse(QRectF(self.rect()).adjusted(1, 1, -1, -1))
            elif self.underMouse():
                p.setPen(QPen(QColor(tok.fg2), 1))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawEllipse(QRectF(self.rect()).adjusted(1, 1, -1, -1))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(col)
            p.drawEllipse(r)
            if self.edited:
                p.setBrush(QColor(tok.fg0))
                c = r.center()
                p.drawEllipse(c, 2.5, 2.5)
        finally:
            p.end()

    def enterEvent(self, event):
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.update()
        super().leaveEvent(event)


class _WheelRow(QWidget):
    """Three wheels side by side that shrink/grow with the dock width."""

    def __init__(self, wheels, captions, parent=None):
        super().__init__(parent)
        self._wheels = list(wheels)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(INSPECTOR_PAD_X, INSPECTOR_ROW_GAP, INSPECTOR_PAD_X, INSPECTOR_ROW_GAP)
        lay.setSpacing(INSPECTOR_PAIR_GAP)
        for w, lab in zip(self._wheels, captions):
            col = QVBoxLayout()
            col.setContentsMargins(0, 0, 0, 0)
            col.setSpacing(INSPECTOR_FIELD_GAP)
            cap = QLabel(lab)
            cap.setObjectName("inspectorLabel")
            cap.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            col.addWidget(cap)
            col.addWidget(w, 0, Qt.AlignmentFlag.AlignHCenter)
            w.setToolTip(tr("拖动圆盘：色偏　外环滚轮：明暗　双击：复位"))
            lay.addLayout(col, 1)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        m = self.layout().contentsMargins()
        gaps = self.layout().spacing() * (len(self._wheels) - 1)
        avail = self.width() - m.left() - m.right() - gaps
        side = int(avail / max(1, len(self._wheels)))
        side = max(INSPECTOR_WHEEL_MIN_PX, min(INSPECTOR_WHEEL_MAX_PX, side))
        for w in self._wheels:
            if w.width() != side:
                w.setFixedSize(side, side)


class GradeEditor(QWidget):
    changed = Signal()
    scope_mode_changed = Signal(str)

    def __init__(self, title="调色", parent=None):
        super().__init__(parent)
        self.setObjectName("gradeEditor")
        self._block = False
        self._grade = identity_grade()
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(INSPECTOR_SECTION_GAP)

        sec_exp = CollapsibleSection(title)
        self.sec_basic = sec_exp
        form = InspectForm()
        form.set_reset_resolver(self._resolve_grade_key)
        form.setSpacing(INSPECTOR_ROW_GAP)
        form.setContentsMargins(
            INSPECTOR_PAD_X, INSPECTOR_ROW_GAP, INSPECTOR_PAD_X, INSPECTOR_ROW_GAP)
        self.exposure = SliderSpin(-4.0, 4.0, 0.01, 2)
        self.contrast = SliderSpin(-1.0, 3.0, 0.01, 2)
        self.pivot = SliderSpin(0.0, 1.0, 0.01, 2)
        self.temperature = SliderSpin(-5500.0, 8000.0, 10.0, 0)
        self.tint = SliderSpin(-200.0, 200.0, 1.0, 0)
        self.saturation = SliderSpin(-1.0, 4.0, 0.01, 2)
        self.vibrance = SliderSpin(-1.0, 2.0, 0.01, 2)
        add_inspect_row(form, tr("曝光"), self.exposure, reset_key="exposure")
        add_inspect_row(form, tr("对比"), self.contrast, reset_key="contrast")
        add_inspect_row(form, tr("支点"), self.pivot, reset_key="pivot")
        add_inspect_row(form, tr("色温"), self.temperature, reset_key="temperature")
        add_inspect_row(form, tr("色调"), self.tint, reset_key="tint")
        add_inspect_row(form, tr("饱和"), self.saturation, reset_key="saturation")
        add_inspect_row(form, tr("自然饱和"), self.vibrance, reset_key="vibrance")
        host = QWidget()
        host.setLayout(form)
        sec_exp.addWidget(host)
        root.addWidget(sec_exp)

        sec_lgg = CollapsibleSection(tr("色轮"))
        self.sec_lgg = sec_lgg
        self.wheel_lift = ColorWheel()
        self.wheel_gamma = ColorWheel()
        self.wheel_gain = ColorWheel()
        wh = _WheelRow(
            (self.wheel_lift, self.wheel_gamma, self.wheel_gain),
            (tr("阴影"), tr("伽马"), tr("高光")))
        sec_lgg.addWidget(wh)
        root.addWidget(sec_lgg)

        sec_lv = CollapsibleSection(tr("色阶"))
        self.sec_levels = sec_lv
        lvf = InspectForm()
        lvf.set_reset_resolver(self._resolve_grade_key)
        lvf.setSpacing(INSPECTOR_ROW_GAP)
        lvf.setContentsMargins(
            INSPECTOR_PAD_X, INSPECTOR_ROW_GAP, INSPECTOR_PAD_X, INSPECTOR_ROW_GAP)
        self.lv_in_black = SliderSpin(0.0, 255.0, 1.0, 0)
        self.lv_in_white = SliderSpin(0.0, 255.0, 1.0, 0)
        self.lv_gamma = SliderSpin(0.10, 10.0, 0.01, 2)
        self.lv_out_black = SliderSpin(0.0, 255.0, 1.0, 0)
        self.lv_out_white = SliderSpin(0.0, 255.0, 1.0, 0)
        tip = tr("默认不改变画面。输入黑白拉开两端，伽马大于 1 提亮中间调，输出黑白决定最暗和最亮落到哪里。")
        for lab, widget, key in (
            (tr("输入黑"), self.lv_in_black, "levels.in_black"),
            (tr("输入白"), self.lv_in_white, "levels.in_white"),
            (tr("伽马"), self.lv_gamma, "levels.gamma"),
            (tr("输出黑"), self.lv_out_black, "levels.out_black"),
            (tr("输出白"), self.lv_out_white, "levels.out_white"),
        ):
            widget.setToolTip(tip)
            add_inspect_row(lvf, lab, widget, reset_key=key)
        lv_host = QWidget()
        lv_host.setLayout(lvf)
        sec_lv.addWidget(lv_host)
        sec_lv.setExpanded(False)
        root.addWidget(sec_lv)

        sec_hsl = CollapsibleSection(tr("HSL 限定"))
        self.sec_hsl = sec_hsl
        hf = InspectForm()
        hf.set_reset_resolver(self._resolve_grade_key)
        hf.setSpacing(INSPECTOR_ROW_GAP)
        hf.setContentsMargins(
            INSPECTOR_PAD_X, INSPECTOR_ROW_GAP, INSPECTOR_PAD_X, INSPECTOR_ROW_GAP)
        self.hsl = {}
        chips = QWidget()
        cl = QHBoxLayout(chips)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(INSPECTOR_FIELD_GAP)
        self._hue_group = QButtonGroup(self)
        self._hue_group.setExclusive(True)
        self._hue_chips = {}
        self._hue_pages = QStackedWidget()
        for i, name in enumerate(HUE_BANDS):
            chip = _HueChip(name)
            self._hue_group.addButton(chip, i)
            self._hue_chips[name] = chip
            cl.addWidget(chip)
            page = QWidget()
            pf = InspectForm(page)
            pf.set_reset_resolver(self._resolve_grade_key)
            pf.setContentsMargins(0, 0, 0, 0)
            pf.setSpacing(INSPECTOR_ROW_GAP)
            sh = SliderSpin(-1.0, 1.0, 0.01, 2)
            ss = SliderSpin(-1.0, 1.0, 0.01, 2)
            sl = SliderSpin(-1.0, 1.0, 0.01, 2)
            for lab, sp, comp in (
                (tr("色相"), sh, "h"), (tr("饱和"), ss, "s"), (tr("亮度"), sl, "l"),
            ):
                add_inspect_row(pf, lab, sp, reset_key="hsl.%s.%s" % (name, comp))
            self.hsl[name] = (sh, ss, sl)
            self._hue_pages.addWidget(page)
        cl.addStretch(1)
        self._hue_band_label = QLabel()
        self._hue_band_label.setObjectName("inspectorLabel")
        cl.addWidget(self._hue_band_label)
        self._hue_group.idToggled.connect(self._on_hue_band)
        self._hue_group.button(0).setChecked(True)
        add_inspect_row(hf, tr("颜色"), chips)
        hf.addSpanRow(self._hue_pages)
        hhost = QWidget()
        hhost.setLayout(hf)
        sec_hsl.addWidget(hhost)
        sec_hsl.setExpanded(False)
        root.addWidget(sec_hsl)

        sec_lut = CollapsibleSection(tr("创意 LUT"))
        self.sec_lut = sec_lut
        lf = InspectForm()
        lf.set_reset_resolver(self._resolve_grade_key)
        lf.setSpacing(INSPECTOR_ROW_GAP)
        lf.setContentsMargins(
            INSPECTOR_PAD_X, INSPECTOR_ROW_GAP, INSPECTOR_PAD_X, INSPECTOR_ROW_GAP)
        self.cube_label = QLabel(tr("未导入"))
        self.cube_label.setObjectName("formHint")
        self.cube_label.setWordWrap(True)
        btn_row = QWidget()
        br = QHBoxLayout(btn_row)
        br.setContentsMargins(0, 0, 0, 0)
        br.setSpacing(INSPECTOR_FIELD_GAP)
        self.btn_cube = QPushButton(tr("导入 .cube"))
        self.btn_cube.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.btn_clear_cube = QPushButton(tr("清除"))
        self.btn_clear_cube.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.btn_ai_lut = QPushButton(tr("🪄 AI 调色"))
        self.btn_ai_lut.setToolTip(tr("使用 Autograde 自动计算色温/曝光并生成挂载 3D LUT"))
        self.btn_ai_lut.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.btn_ai_relight = QPushButton(tr("✨ 人像提亮 v6h"))
        self.btn_ai_relight.setToolTip(tr("打开 Simone 人像修复管线 (Relight v6h + 时域降噪)"))
        self.btn_ai_relight.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.btn_ai_lut.clicked.connect(lambda: getattr(self.window(), "room_host", None) and self.window().room_host.enter("ai_studio", tab=3))
        self.btn_ai_relight.clicked.connect(lambda: getattr(self.window(), "room_host", None) and self.window().room_host.enter("ai_studio", tab=3))
        br.addWidget(self.btn_cube)
        br.addWidget(self.btn_clear_cube)
        br.addWidget(self.btn_ai_lut)
        br.addWidget(self.btn_ai_relight)
        br.addStretch()
        add_inspect_row(lf, tr("文件"), btn_row)
        add_inspect_row(lf, "", self.cube_label)
        self.cube_mix = SliderSpin(0.0, 100.0, 1.0, 0)
        add_inspect_row(lf, tr("强度"), self.cube_mix, reset_key="cube_mix")
        lhost = QWidget()
        lhost.setLayout(lf)
        sec_lut.addWidget(lhost)
        root.addWidget(sec_lut)

        sec_scope = CollapsibleSection(tr("示波器"))
        self.sec_scope = sec_scope
        sf = InspectForm()
        sf.setSpacing(INSPECTOR_ROW_GAP)
        sf.setContentsMargins(
            INSPECTOR_PAD_X, INSPECTOR_ROW_GAP, INSPECTOR_PAD_X, INSPECTOR_ROW_GAP)
        from cutora.ui.widgets.inspector import EnumCombo
        self.scope_mode = EnumCombo()
        self.scope_mode.addItem(tr("关"), "off")
        self.scope_mode.addItem(tr("波形（亮度）"), "luma")
        self.scope_mode.addItem(tr("波形（RGB）"), "parade")
        self.scope_mode.addItem(tr("矢量"), "vector")
        self.scope_mode.addItem(tr("直方图"), "hist")
        add_inspect_row(sf, tr("预览"), self.scope_mode)
        shost = QWidget()
        shost.setLayout(sf)
        sec_scope.addWidget(shost)
        root.addWidget(sec_scope)

        for w in (
            self.exposure, self.contrast, self.pivot, self.temperature,
            self.tint, self.saturation, self.vibrance, self.cube_mix,
            self.lv_in_black, self.lv_in_white, self.lv_gamma,
            self.lv_out_black, self.lv_out_white,
        ):
            w.valueChanged.connect(self._on_ui)
        for w in (self.wheel_lift, self.wheel_gamma, self.wheel_gain):
            w.changed.connect(self._on_ui)
        for pair in self.hsl.values():
            for w in pair:
                w.valueChanged.connect(self._on_ui)
        self.btn_cube.clicked.connect(self._pick_cube)
        self.btn_clear_cube.clicked.connect(self._clear_cube)
        self.scope_mode.currentIndexChanged.connect(self._on_scope)
        install_inspector_limits(self)
        self.load_grade(identity_grade())

    def _on_hue_band(self, idx, on) -> None:
        if not on:
            return
        self._hue_pages.setCurrentIndex(idx)
        name = HUE_BANDS[idx]
        self._hue_band_label.setText(tr(_HUE_ZH.get(name, name)))

    def _refresh_hue_marks(self) -> None:
        for name, (sh, ss, sl) in self.hsl.items():
            chip = self._hue_chips.get(name)
            if chip is None:
                continue
            edited = any(abs(float(w.value())) > 1e-6 for w in (sh, ss, sl))
            if edited != chip.edited:
                chip.edited = edited
                chip.update()

    def _owning_panel(self):
        w = self.parent()
        while w is not None:
            if callable(getattr(w, "_open_reset_undo", None)):
                return w
            w = w.parent()
        return None

    def _resolve_grade_key(self, key: str) -> None:
        panel = self._owning_panel()
        opened = False
        if panel is not None and not getattr(panel, "_reset_undo_open", False):
            opened = bool(panel._open_reset_undo("重置"))
        try:
            self._apply_grade_default(key)
        finally:
            if opened:
                panel._close_reset_undo()

    def _apply_grade_default(self, key: str) -> None:
        ident = identity_grade()
        if key == "cube_mix":
            self.cube_mix.setValue(100.0)
            return
        if str(key).startswith("hsl."):
            _hsl, band, comp = str(key).split(".", 2)
            idx = {"h": 0, "s": 1, "l": 2}[comp]
            pair = self.hsl.get(band)
            if pair is not None:
                pair[idx].setValue(0.0)
            return
        if str(key).startswith("levels."):
            field = str(key).split(".", 1)[1]
            widget = getattr(self, "lv_" + field, None)
            ident_lv = ident.get("levels") or {}
            if widget is not None and field in ident_lv:
                widget.setValue(float(ident_lv[field]))
            return
        widget = getattr(self, key, None)
        if widget is not None and key in ident:
            widget.setValue(float(ident[key]))

    def reset_extra(self, sec) -> None:
        """Wheels, LUT file and scope are not ordinary rows."""
        if sec is getattr(self, "sec_lgg", None):
            for wheel in (self.wheel_lift, self.wheel_gamma, self.wheel_gain):
                wheel.reset()
            return
        if sec is getattr(self, "sec_levels", None):
            for key in ("levels.in_black", "levels.in_white", "levels.gamma",
                        "levels.out_black", "levels.out_white"):
                self._apply_grade_default(key)
            return
        if sec is getattr(self, "sec_lut", None):
            self._clear_cube()
            return
        if sec is getattr(self, "sec_scope", None):
            idx = self.scope_mode.findData("off")
            if idx >= 0 and self.scope_mode.currentIndex() != idx:
                self.scope_mode.setCurrentIndex(idx)

    def _reset_key(self, key):
        self._resolve_grade_key(key)

    def _on_scope(self, *_args):
        self.scope_mode_changed.emit(str(self.scope_mode.currentData() or "off"))

    def _wheel_to_lgg(self, wheel, y_scale=1.0):
        r, g, y = wheel.value()
        b = -0.5 * (r + g)
        return [float(r), float(g), float(b), float(y) * y_scale]

    def _lgg_to_wheel(self, wheel, vec):
        if not vec or len(vec) < 4:
            wheel.set_value(0.0, 0.0, 0.0)
            return
        wheel.set_value(float(vec[0]), float(vec[1]), float(vec[3]))

    def grade(self) -> dict:
        g = identity_grade()
        g["exposure"] = float(self.exposure.value())
        g["contrast"] = float(self.contrast.value())
        g["pivot"] = float(self.pivot.value())
        g["temperature"] = float(self.temperature.value())
        g["tint"] = float(self.tint.value())
        g["saturation"] = float(self.saturation.value())
        g["vibrance"] = float(self.vibrance.value())
        g["lift"] = self._wheel_to_lgg(self.wheel_lift)
        g["gamma"] = self._wheel_to_lgg(self.wheel_gamma)
        g["gain"] = self._wheel_to_lgg(self.wheel_gain)
        for name, (sh, ss, sl) in self.hsl.items():
            g["hsl"][name] = {
                "h": float(sh.value()),
                "s": float(ss.value()),
                "l": float(sl.value()),
            }
        g["cube"] = str(self._grade.get("cube") or "")
        g["cube_mix"] = float(self.cube_mix.value()) / 100.0
        g["curves"] = dict(self._grade.get("curves") or identity_grade()["curves"])
        g["levels"] = {
            "in_black": float(self.lv_in_black.value()),
            "in_white": float(self.lv_in_white.value()),
            "gamma": float(self.lv_gamma.value()),
            "out_black": float(self.lv_out_black.value()),
            "out_white": float(self.lv_out_white.value()),
        }
        self._grade = normalize_grade(g)
        return dict(self._grade)

    def load_grade(self, raw) -> None:
        g = normalize_grade(raw)
        self._block = True
        try:
            self.exposure.setValue(g["exposure"])
            self.contrast.setValue(g["contrast"])
            self.pivot.setValue(g["pivot"])
            self.temperature.setValue(g["temperature"])
            self.tint.setValue(g["tint"])
            self.saturation.setValue(g["saturation"])
            self.vibrance.setValue(g["vibrance"])
            self._lgg_to_wheel(self.wheel_lift, g["lift"])
            self._lgg_to_wheel(self.wheel_gamma, g["gamma"])
            self._lgg_to_wheel(self.wheel_gain, g["gain"])
            lv = g.get("levels") or {}
            self.lv_in_black.setValue(float(lv.get("in_black", 0.0) or 0.0))
            self.lv_in_white.setValue(float(lv.get("in_white", 255.0) or 0.0))
            self.lv_gamma.setValue(float(lv.get("gamma", 1.0) or 1.0))
            self.lv_out_black.setValue(float(lv.get("out_black", 0.0) or 0.0))
            self.lv_out_white.setValue(float(lv.get("out_white", 255.0) or 0.0))
            for name, (sh, ss, sl) in self.hsl.items():
                rec = g["hsl"].get(name) or {}
                sh.setValue(float(rec.get("h", 0) or 0))
                ss.setValue(float(rec.get("s", 0) or 0))
                sl.setValue(float(rec.get("l", 0) or 0))
            self._refresh_hue_marks()
            mix = float(g.get("cube_mix", 1.0) or 0.0)
            self.cube_mix.setValue(mix * 100.0 if mix <= 1.0 + 1e-6 else mix)
            cube = str(g.get("cube") or "")
            self.cube_label.setText(os.path.basename(cube) if cube else tr("未导入"))
            self._grade = g
        finally:
            self._block = False

    def _on_ui(self, *_args):
        self._refresh_hue_marks()
        if self._block:
            return
        self.grade()
        self.changed.emit()

    def _pick_cube(self):
        start = user_luts_dir()
        path, _ok = QFileDialog.getOpenFileName(
            self, tr("导入 LUT"), start, "LUT (*.cube)")
        if not path:
            return
        if not path.lower().endswith(".cube"):
            _LOG.warning("拒绝非 .cube LUT: %s", path)
            return
        try:
            from cutora.render.grade import parse_cube
            parse_cube(path)
        except Exception as exc:
            _LOG.exception("导入 LUT 失败: %s", path)
            win = self.window()
            bar = getattr(win, "statusBar", None) if win is not None else None
            if callable(bar):
                bar().showMessage(tr("导入 LUT 失败：%s") % exc, 4000)
            return
        self._grade["cube"] = path
        self.cube_label.setText(os.path.basename(path))
        self.changed.emit()

    def _clear_cube(self):
        self._grade["cube"] = ""
        self.cube_label.setText(tr("未导入"))
        self.changed.emit()
