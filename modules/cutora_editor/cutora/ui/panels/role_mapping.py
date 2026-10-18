# SPDX-License-Identifier: GPL-3.0-or-later
# Extracted from the T01 monolith by tools/split_monolith.py.
# Original lines: 6594-6868

import copy
from PySide6.QtWidgets import (
    QWidget,
    QHBoxLayout,
    QVBoxLayout,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QTextEdit,
    QLabel,
    QMessageBox,
    QGroupBox,
    QFormLayout,
    QColorDialog,
    QCheckBox,
    QScrollArea,
    QFrame,
    QSizePolicy,
    QAbstractItemView,
    QInputDialog,
    QDoubleSpinBox,
)
from PySide6.QtCore import (
    Qt,
    Signal,
)
from PySide6.QtGui import QColor
from cutora.ui.panels.subtitle_style import SubtitleStylePanel
from PySide6.QtWidgets import (
    QAbstractItemView,
    QMessageBox,
)
from PySide6.QtCore import Qt
from cutora.ui.panels.subtitle_style import SubtitleStylePanel
from cutora.ui.theme.tokens import (
    INSPECTOR_FIELD_GAP,
    INSPECTOR_PAD_X,
    INSPECTOR_PAD_Y,
    INSPECTOR_PAIR_GAP,
    INSPECTOR_ROW_GAP,
    INSPECTOR_SECTION_GAP,
    INSPECTOR_TEXT_H,
)
from cutora.ui.widgets.collapsible import CollapsibleSection
from cutora.ui.widgets.inspector import InspectForm, install_inspector_limits, paired_fields


class RoleMappingPanel(QWidget):
    style_changed = Signal(object)
    bind_request = Signal(object)
    smart_match_request = Signal(object)
    roles_updated = Signal()

    def __init__(self):
        super().__init__()
        self.task = None
        self.current_role_id = "default"
        self._build_ui()

    def _build_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        main_layout.addWidget(scroll, 1)

        content = QWidget()
        scroll.setWidget(content)
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, INSPECTOR_PAD_Y, 0, 0)
        content_layout.setSpacing(INSPECTOR_SECTION_GAP)

        def _form():
            f = InspectForm()
            f.setSpacing(INSPECTOR_ROW_GAP)
            f.setContentsMargins(INSPECTOR_PAD_X, INSPECTOR_ROW_GAP, INSPECTOR_PAD_X, INSPECTOR_ROW_GAP)
            return f

        # ── 角色 ──
        sec_roles = CollapsibleSection("角色")
        rf = _form()
        self.role_list = QListWidget()
        self.role_list.setObjectName("roleList")
        self.role_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.role_list.currentItemChanged.connect(self._on_role_selected)
        self.role_list.setFixedHeight(INSPECTOR_TEXT_H * 2)
        rf.addSpanRow(self.role_list)
        btn_host = QWidget()
        btn_row = QHBoxLayout(btn_host)
        btn_row.setContentsMargins(0, 0, 0, 0)
        btn_row.setSpacing(INSPECTOR_FIELD_GAP)
        self.btn_add = QPushButton("新建")
        self.btn_rename = QPushButton("重命名")
        self.btn_delete = QPushButton("删除")
        self.btn_add.clicked.connect(self._on_add_role)
        self.btn_rename.clicked.connect(self._on_rename_role)
        self.btn_delete.clicked.connect(self._on_delete_role)
        for b in (self.btn_add, self.btn_rename, self.btn_delete):
            b.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            btn_row.addWidget(b, 1)
        rf.addSpanRow(btn_host)
        self.lbl_role_name = QLabel("-")
        self.lbl_role_name.setObjectName("roleName")
        self.btn_color = QPushButton("颜色")
        self.btn_color.setToolTip("角色在时间线上的标记色")
        self.btn_color.clicked.connect(self._on_pick_color)
        info_host = QWidget()
        info_row = QHBoxLayout(info_host)
        info_row.setContentsMargins(0, 0, 0, 0)
        info_row.setSpacing(INSPECTOR_PAIR_GAP)
        info_row.addWidget(self.lbl_role_name, 1)
        info_row.addWidget(self.btn_color, 0)
        rf.addRow("当前角色", info_host)
        _rw = QWidget(); _rw.setLayout(rf); sec_roles.addWidget(_rw)
        content_layout.addWidget(sec_roles)

        # ── 绑定到字幕 ──
        sec_bind = CollapsibleSection("绑定到字幕")
        bf = _form()
        self.bind_text = QTextEdit()
        self.bind_text.setPlaceholderText("输入要匹配的文本（按 clean_text 包含匹配）")
        self.bind_text.setFixedHeight(INSPECTOR_TEXT_H)
        bf.addSpanRow(self.bind_text)
        self.btn_smart_match = QPushButton("智能匹配字幕范围")
        self.btn_smart_match.clicked.connect(self._on_smart_match)
        bf.addRow("", self.btn_smart_match)
        from cutora.ui.widgets.collapsible import labeled_toggle
        self.use_time_range_host, self.use_time_range = labeled_toggle("启用时间范围")
        self.use_time_range.setChecked(True)
        self.use_time_range_host.hide()
        bf.addRow("限定时间", self.use_time_range)
        self.range_start = QDoubleSpinBox()
        self.range_end = QDoubleSpinBox()
        for sp, tip in ((self.range_start, "开始（秒）"), (self.range_end, "结束（秒）")):
            sp.setRange(0.0, 999999.0)
            sp.setDecimals(3)
            sp.setSingleStep(0.1)
            sp.setToolTip(tip)
        bf.addRow("起止 (秒)", paired_fields(self.range_start, self.range_end))
        self.use_time_range.toggled.connect(self.range_start.setEnabled)
        self.use_time_range.toggled.connect(self.range_end.setEnabled)
        self.btn_apply = QPushButton("应用到字幕")
        self.btn_apply.setObjectName("btnPrimary")
        self.btn_apply.clicked.connect(self._on_apply_bind)
        bf.addRow("", self.btn_apply)
        _bw = QWidget(); _bw.setLayout(bf); sec_bind.addWidget(_bw)
        content_layout.addWidget(sec_bind)
        install_inspector_limits(content)

        # 主区域：样式面板
        self.style_panel = SubtitleStylePanel()
        self.style_panel.param_changed.connect(self._on_style_changed)
        self.style_panel.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.style_panel.setMinimumHeight(max(self.style_panel.minimumHeight(), self.style_panel.sizeHint().height()))
        content_layout.addWidget(self.style_panel, 1)

    def _get_clean_style(self, panel_data, base_style):
        import copy
        new_ov = {}
        ts = base_style
        _LAYOUT_KEYS = {
            "is_detached",
            "advanced_textbox_enabled",
            "advanced_textbox_x",
            "advanced_textbox_y",
            "advanced_textbox_w",
            "advanced_textbox_h",
        }
        for k, v in panel_data.items():
            if k not in _LAYOUT_KEYS:
                if k not in ts or ts.get(k) != v:
                    new_ov[k] = copy.deepcopy(v)
        for k in _LAYOUT_KEYS:
            if k in panel_data:
                new_ov[k] = panel_data[k]
        return new_ov

    def load_task(self, task):
        self.task = task
        self._refresh_roles()
        self._set_enabled(bool(task))

    def _set_enabled(self, enabled):
        self.role_list.setEnabled(enabled)
        self.btn_add.setEnabled(enabled)
        self.btn_rename.setEnabled(enabled)
        self.btn_delete.setEnabled(enabled)
        self.btn_color.setEnabled(enabled)
        self.style_panel.setEnabled(enabled)
        self.bind_text.setEnabled(enabled)
        self.use_time_range.setEnabled(enabled)
        self.range_start.setEnabled(enabled)
        self.range_end.setEnabled(enabled)
        self.btn_apply.setEnabled(enabled)

    def _refresh_roles(self):
        self.role_list.clear()
        if not self.task:
            self.current_role_id = "default"
            self.lbl_role_name.setText("-")
            return
        roles = self.task.get("subtitle_roles", {})
        if "default" not in roles:
            roles["default"] = {"name": "默认", "style": copy.deepcopy(self.task.get("style", {})), "color": "#CCCCCC"}
        for rid, info in roles.items():
            name = info.get("name", rid)
            item = QListWidgetItem(name)
            item.setData(Qt.ItemDataRole.UserRole, rid)
            self.role_list.addItem(item)
        self._select_role("default")

    def _select_role(self, role_id):
        for i in range(self.role_list.count()):
            item = self.role_list.item(i)
            if item.data(Qt.ItemDataRole.UserRole) == role_id:
                self.role_list.setCurrentRow(i)
                return
        if self.role_list.count() > 0:
            self.role_list.setCurrentRow(0)

    def _on_role_selected(self, current, _prev):
        if not current or not self.task:
            return
        role_id = current.data(Qt.ItemDataRole.UserRole)
        self.current_role_id = role_id
        roles = self.task.get("subtitle_roles", {})
        info = roles.get(role_id, {})
        self.lbl_role_name.setText(info.get("name", role_id))
        self._update_color_button(info.get("color", "#CCCCCC"))
        if role_id == "default":
            self.style_panel.load_data(self.task.get("style", {}))
        else:
            self.style_panel.load_data(info.get("style", {}))

    def _update_color_button(self, color_hex):
        c = QColor(color_hex) if color_hex else QColor("#CCCCCC")
        if not c.isValid():
            c = QColor("#CCCCCC")
        from cutora.ui.theme.apply import apply_swatch
        apply_swatch(self.btn_color, c.name())

    def _on_pick_color(self):
        if not self.task:
            return
        roles = self.task.get("subtitle_roles", {})
        info = roles.get(self.current_role_id, {})
        init_c = QColor(info.get("color", "#CCCCCC"))
        c = QColorDialog.getColor(init_c, self, "选择角色颜色")
        if c.isValid():
            info["color"] = c.name()
            self._update_color_button(c.name())
            self.roles_updated.emit()

    def _on_add_role(self):
        if not self.task:
            return
        name, ok = QInputDialog.getText(self, "新建角色", "请输入角色名称:")
        if not ok or not name.strip():
            return
        role_id = self._generate_role_id()
        self.task.setdefault("subtitle_roles", {})[role_id] = {
            "name": name.strip(),
            "style": copy.deepcopy(self.task.get("style", {})),
            "color": "#CCCCCC"
        }
        self._refresh_roles()
        self._select_role(role_id)
        self.roles_updated.emit()

    def _on_rename_role(self):
        if not self.task or not self.current_role_id:
            return
        roles = self.task.get("subtitle_roles", {})
        info = roles.get(self.current_role_id, {})
        old_name = info.get("name", self.current_role_id)
        new_name, ok = QInputDialog.getText(self, "重命名角色", "请输入新名称:", text=old_name)
        if not ok or not new_name.strip():
            return
        info["name"] = new_name.strip()
        self._refresh_roles()
        self._select_role(self.current_role_id)
        self.roles_updated.emit()

    def _on_delete_role(self):
        if not self.task or not self.current_role_id:
            return
        if self.current_role_id == "default":
            QMessageBox.information(self, "提示", "默认角色不可删除")
            return
        resp = QMessageBox.question(self, "确认删除", "删除角色后将回退到默认角色，是否继续？")
        if resp != QMessageBox.StandardButton.Yes:
            return
        role_id = self.current_role_id
        for seg in self.task.get("segments", []) or []:
            if isinstance(seg, dict) and seg.get("role") == role_id:
                seg["role"] = "default"
        self.task.get("subtitle_roles", {}).pop(role_id, None)
        self._refresh_roles()
        self.roles_updated.emit()

    def _generate_role_id(self):
        roles = self.task.get("subtitle_roles", {}) if self.task else {}
        base = "role"
        idx = 1
        while f"{base}_{idx}" in roles:
            idx += 1
        return f"{base}_{idx}"

    def _on_style_changed(self, key):
        self.style_changed.emit({"role_id": self.current_role_id, "key": key})

    def _on_apply_bind(self):
        payload = {
            "role_id": self.current_role_id,
            "text": self.bind_text.toPlainText(),
            "use_range": self.use_time_range.isChecked(),
            "start": self.range_start.value(),
            "end": self.range_end.value()
        }
        self.bind_request.emit(payload)

    def _on_smart_match(self):
        payload = {
            "text": self.bind_text.toPlainText()
        }
        self.smart_match_request.emit(payload)

    def set_time_range(self, start, end, enable=True):
        if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
            return
        self.range_start.setValue(float(start))
        self.range_end.setValue(float(end))
        self.use_time_range.setChecked(bool(enable))
