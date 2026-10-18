# SPDX-License-Identifier: GPL-3.0-or-later
# Extracted from the T01 monolith by tools/split_monolith.py.
# Original lines: 9203-9356, 9358-9661

from PySide6.QtWidgets import (
    QWidget,
    QHBoxLayout,
    QVBoxLayout,
    QPushButton,
    QLabel,
    QMessageBox,
    QSpinBox,
    QColorDialog,
    QCheckBox,
    QSizePolicy,
    QInputDialog,
    QFileDialog,
    QComboBox,
    QLineEdit,
    QFontComboBox,
)
from PySide6.QtCore import (
    Qt,
    Signal,
)
from PySide6.QtGui import QColor
from cutora.subtitle import presets as preset_manager
from PySide6.QtWidgets import (
    QFileDialog,
    QMessageBox,
)
from PySide6.QtCore import Qt
class KeywordRuleRow(QWidget):
    delete_requested = Signal(QWidget)
    
    def __init__(self, data=None, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(4, 4, 4, 4)
        main_layout.setSpacing(2)
        
        r1 = QHBoxLayout()
        r1.setContentsMargins(0,0,0,0)
        self.edit_word = QLineEdit()
        self.edit_word.setPlaceholderText("填关键词")
        self.edit_word.setFixedWidth(80)
        r1.addWidget(self.edit_word)
        
        self.btn_c1 = QPushButton()
        self.btn_c1.setFixedSize(20, 20)
        self.btn_c1.clicked.connect(self._pick_c1)
        self.color1 = "#FF0000"
        self._update_btn_color(self.btn_c1, self.color1)
        r1.addWidget(self.btn_c1)
        
        self.btn_c2 = QPushButton()
        self.btn_c2.setFixedSize(20, 20)
        self.btn_c2.clicked.connect(self._pick_c2)
        self.color2 = "#FFA500"
        self._update_btn_color(self.btn_c2, self.color2)
        r1.addWidget(self.btn_c2)
        
        self.chk_grad = QCheckBox("渐变")
        self.chk_grad.toggled.connect(lambda c: self.btn_c2.setVisible(c))
        r1.addWidget(self.chk_grad)
        
        self.chk_bold = QCheckBox("粗体")
        r1.addWidget(self.chk_bold)
        
        r1.addStretch()
        self.btn_del = QPushButton("X")
        self.btn_del.setFixedSize(20, 20)
        self.btn_del.setObjectName("btnDanger")
        self.btn_del.clicked.connect(lambda: self.delete_requested.emit(self))
        r1.addWidget(self.btn_del)
        main_layout.addLayout(r1)
        
        r2 = QHBoxLayout()
        r2.setContentsMargins(0,0,0,0)
        self.chk_stroke = QCheckBox("描边")
        r2.addWidget(self.chk_stroke)
        self.btn_stroke = QPushButton()
        self.btn_stroke.setFixedSize(20, 20)
        self.btn_stroke.clicked.connect(self._pick_stroke)
        self.color_stroke = "#000000"
        self._update_btn_color(self.btn_stroke, self.color_stroke)
        r2.addWidget(self.btn_stroke)
        self.spin_sw = QSpinBox()
        self.spin_sw.setRange(1, 20)
        self.spin_sw.setFixedWidth(40)
        self.spin_sw.setToolTip("描边基础粗细")
        r2.addWidget(self.spin_sw)
        
        self.chk_font = QCheckBox("字体")
        r2.addWidget(self.chk_font)
        
        self.combo_font = QFontComboBox()
        self.combo_font.setFixedWidth(100)
        r2.addWidget(self.combo_font)
        
        self.spin_fs = QSpinBox()
        self.spin_fs.setRange(10, 300)
        self.spin_fs.setFixedWidth(50)
        self.spin_fs.setToolTip("强制改变当前词字号")
        r2.addWidget(self.spin_fs)
        
        r2.addStretch()
        main_layout.addLayout(r2)
        
        self.chk_stroke.toggled.connect(self._on_stroke_toggled)
        self.chk_font.toggled.connect(self._on_font_toggled)
        
        if data:
            self.set_data(data)
        else:
            self.chk_stroke.setChecked(False)
            self.chk_font.setChecked(False)
            self.chk_grad.setChecked(False)
            self.chk_bold.setChecked(True)
            self.spin_sw.setValue(2)
            self.spin_fs.setValue(80)
            
    def _update_btn_color(self, btn, c_hex):
        from cutora.ui.theme.apply import apply_swatch
        apply_swatch(btn, c_hex)
        
    def _pick_c1(self):
        c = QColorDialog.getColor(QColor(self.color1), self, "选择颜色")
        if c.isValid():
            self.color1 = c.name()
            self._update_btn_color(self.btn_c1, self.color1)
    def _pick_c2(self):
        c = QColorDialog.getColor(QColor(self.color2), self, "选择末端颜色")
        if c.isValid():
            self.color2 = c.name()
            self._update_btn_color(self.btn_c2, self.color2)
    def _pick_stroke(self):
        c = QColorDialog.getColor(QColor(self.color_stroke), self, "选择描边颜色")
        if c.isValid():
            self.color_stroke = c.name()
            self._update_btn_color(self.btn_stroke, self.color_stroke)
            
    def _on_stroke_toggled(self, c):
        self.btn_stroke.setVisible(c)
        self.spin_sw.setVisible(c)
    def _on_font_toggled(self, c):
        self.combo_font.setVisible(c)
        self.spin_fs.setVisible(c)
        
    def set_data(self, data):
        self.edit_word.setText(data.get("word", ""))
        self.color1 = data.get("color1", "#FF0000")
        self._update_btn_color(self.btn_c1, self.color1)
        self.color2 = data.get("color2", "#FFA500")
        self._update_btn_color(self.btn_c2, self.color2)
        use_grad = data.get("use_grad", False)
        self.chk_grad.setChecked(use_grad)
        self.btn_c2.setVisible(use_grad)
        self.chk_bold.setChecked(bool(data.get("bold", True)))
        
        self.chk_stroke.setChecked(data.get("use_stroke", False))
        self.color_stroke = data.get("stroke_c", "#000000")
        self._update_btn_color(self.btn_stroke, self.color_stroke)
        self.spin_sw.setValue(int(data.get("stroke_w", 2)))
        
        self.chk_font.setChecked(data.get("use_font", False))
        if data.get("font_f"):
            idx = self.combo_font.findText(data["font_f"])
            if idx >= 0: self.combo_font.setCurrentIndex(idx)
        self.spin_fs.setValue(int(data.get("font_s", 80)))
        
    def get_data(self):
        return {
            "word": self.edit_word.text().strip(),
            "color1": self.color1,
            "color2": self.color2,
            "use_grad": self.chk_grad.isChecked(),
            "bold": self.chk_bold.isChecked(),
            "use_stroke": self.chk_stroke.isChecked(),
            "stroke_c": self.color_stroke,
            "stroke_w": self.spin_sw.value(),
            "use_font": self.chk_font.isChecked(),
            "font_f": self.combo_font.currentText(),
            "font_s": self.spin_fs.value()
        }

class KeywordRulesWidget(QWidget):
    changed = Signal()
    def __init__(self, parent=None):
        super().__init__(parent)
        self.v_layout = QVBoxLayout(self)
        self.v_layout.setContentsMargins(0, 0, 0, 0)
        self.v_layout.setSpacing(4)
        
        self.rows_layout = QVBoxLayout()
        self.rows_layout.setSpacing(2)
        self.v_layout.addLayout(self.rows_layout)
        
        tools_layout = QHBoxLayout()
        btn_add = QPushButton("+ 添加新规则")
        btn_add.clicked.connect(self._add_empty_row)
        btn_clear = QPushButton("清空")
        btn_clear.clicked.connect(self._clear_all)
        
        btn_apply = QPushButton("✔️ 立即应用至画面")
        btn_apply.setObjectName("btn_apply_now")
        btn_apply.clicked.connect(lambda: self.changed.emit())
        
        tools_layout.addWidget(btn_add)
        tools_layout.addWidget(btn_clear)
        tools_layout.addStretch()
        tools_layout.addWidget(btn_apply)
        self.v_layout.addLayout(tools_layout)

        # ── 预设管理区域 ──
        preset_row1 = QHBoxLayout()
        preset_row1.setSpacing(4)
        preset_row1.addWidget(QLabel("预设:"))
        self._preset_combo = QComboBox()
        self._preset_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._preset_combo.currentIndexChanged.connect(self._on_preset_selected)
        preset_row1.addWidget(self._preset_combo, 1)

        self._btn_load = QPushButton("加载")
        self._btn_load.setToolTip("加载选中的预设")
        self._btn_load.clicked.connect(self._load_preset)
        preset_row1.addWidget(self._btn_load)

        self._btn_save = QPushButton("保存")
        self._btn_save.setToolTip("将当前规则保存为新预设")
        self._btn_save.clicked.connect(self._save_preset)
        preset_row1.addWidget(self._btn_save)

        self._btn_update = QPushButton("更新")
        self._btn_update.setToolTip("覆盖更新当前选中的预设")
        self._btn_update.clicked.connect(self._update_preset)
        preset_row1.addWidget(self._btn_update)

        self.v_layout.addLayout(preset_row1)

        preset_row2 = QHBoxLayout()
        preset_row2.setSpacing(4)

        self._btn_delete = QPushButton("删除")
        self._btn_delete.setToolTip("删除当前选中的预设")
        self._btn_delete.clicked.connect(self._delete_preset)
        preset_row2.addWidget(self._btn_delete)

        self._btn_default = QPushButton("☆ 设为默认")
        self._btn_default.setToolTip("将当前选中的预设设为默认（每次新建项目自动加载）")
        self._btn_default.clicked.connect(self._set_default_preset)
        preset_row2.addWidget(self._btn_default)

        preset_row2.addStretch()

        self._btn_import = QPushButton("导入")
        self._btn_import.setToolTip("从文件导入预设")
        self._btn_import.clicked.connect(self._import_presets)
        preset_row2.addWidget(self._btn_import)

        self._btn_export = QPushButton("导出")
        self._btn_export.setToolTip("将所有预设导出到文件")
        self._btn_export.clicked.connect(self._export_presets)
        preset_row2.addWidget(self._btn_export)

        self.v_layout.addLayout(preset_row2)

        # 初始化预设列表
        self._refreshing_presets = False
        self._refresh_presets()

    # ── 规则行管理 ──

    def _add_empty_row(self):
        self._add_row({"word": "", "color1": "#FF0000", "use_grad": False, "bold": True})
        
    def _add_row(self, data):
        row = KeywordRuleRow(data)
        row.delete_requested.connect(self._remove_row)
        self.rows_layout.addWidget(row)
        
    def _remove_row(self, w):
        self.rows_layout.removeWidget(w)
        w.deleteLater()
        
    def _clear_all(self):
        while self.rows_layout.count():
            item = self.rows_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.changed.emit()

    def get_data(self):
        data = []
        for i in range(self.rows_layout.count()):
            w = self.rows_layout.itemAt(i).widget()
            if isinstance(w, KeywordRuleRow):
                rd = w.get_data()
                if rd['word']:
                    data.append(rd)
        return data
        
    def set_data(self, data):
        while self.rows_layout.count():
            item = self.rows_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for rule in data:
            self._add_row(rule)

    # ── 预设管理 ──

    def _refresh_presets(self):
        """刷新预设下拉列表。"""
        self._refreshing_presets = True
        current_text = self._preset_combo.currentText()
        self._preset_combo.clear()
        self._preset_combo.addItem("（未选择预设）", "")
        try:
            data = preset_manager.load_keyword_presets()
            presets = data.get("presets") or {}
            default_name = data.get("default_preset_name", "")
            for name in sorted(presets.keys()):
                display = f"★ {name}" if name == default_name else name
                self._preset_combo.addItem(display, name)
            # 恢复之前的选择
            for i in range(self._preset_combo.count()):
                if self._preset_combo.itemData(i) == current_text:
                    self._preset_combo.setCurrentIndex(i)
                    break
        except Exception:
            pass
        self._refreshing_presets = False

    def _on_preset_selected(self, index):
        """预设下拉选择变化时不自动加载，需要点加载按钮。"""
        pass

    def _load_preset(self):
        """加载选中的预设。"""
        name = self._preset_combo.currentData()
        if not name:
            return
        try:
            data = preset_manager.load_keyword_presets()
            presets = data.get("presets") or {}
            rules = presets.get(name)
            if isinstance(rules, list):
                self.set_data(rules)
                self.changed.emit()
        except Exception:
            pass

    def _save_preset(self):
        """保存当前规则为新预设。"""
        name, ok = QInputDialog.getText(
            self, "保存自动着色预设", "请输入预设名称:",
            text=""
        )
        if not ok or not name or not name.strip():
            return
        name = name.strip()
        rules = self.get_data()
        if preset_manager.save_named_keyword_preset(name, rules):
            self._refresh_presets()
            # 选中刚保存的预设
            for i in range(self._preset_combo.count()):
                if self._preset_combo.itemData(i) == name:
                    self._preset_combo.setCurrentIndex(i)
                    break
            QMessageBox.information(self, "成功", f"预设 \"{name}\" 已保存。")
        else:
            QMessageBox.warning(self, "失败", "保存失败。预设数量可能已达上限。")

    def _update_preset(self):
        """覆盖更新选中的预设。"""
        name = self._preset_combo.currentData()
        if not name:
            QMessageBox.information(self, "提示", "请先选择要更新的预设。")
            return
        reply = QMessageBox.question(
            self, "确认覆盖",
            f"确定要用当前规则覆盖预设 \"{name}\" 吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        rules = self.get_data()
        if preset_manager.save_named_keyword_preset(name, rules):
            QMessageBox.information(self, "成功", f"预设 \"{name}\" 已更新。")
        else:
            QMessageBox.warning(self, "失败", "更新失败。")

    def _delete_preset(self):
        """删除选中的预设。"""
        name = self._preset_combo.currentData()
        if not name:
            QMessageBox.information(self, "提示", "请先选择要删除的预设。")
            return
        reply = QMessageBox.question(
            self, "确认删除",
            f"确定要删除预设 \"{name}\" 吗？此操作不可撤销。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        if preset_manager.delete_keyword_preset(name):
            self._refresh_presets()
            QMessageBox.information(self, "成功", f"预设 \"{name}\" 已删除。")
        else:
            QMessageBox.warning(self, "失败", "删除失败。")

    def _set_default_preset(self):
        """将选中的预设设为默认。"""
        name = self._preset_combo.currentData()
        if not name:
            QMessageBox.information(self, "提示", "请先选择要设为默认的预设。")
            return
        if preset_manager.set_default_keyword_preset_name(name):
            self._refresh_presets()
            QMessageBox.information(self, "成功", f"预设 \"{name}\" 已设为默认。\n新建项目或启动软件时将自动加载。")
        else:
            QMessageBox.warning(self, "失败", "设置默认失败。")

    def _import_presets(self):
        """从文件导入预设。"""
        path, _ = QFileDialog.getOpenFileName(
            self, "导入自动着色预设", "",
            "JSON 文件 (*.json);;所有文件 (*)"
        )
        if not path:
            return
        result = preset_manager.import_keyword_presets(path)
        added = result.get("added", [])
        conflicts = result.get("conflicts", [])
        skipped = result.get("skipped", [])
        msg_parts = []
        if added:
            msg_parts.append(f"新增: {', '.join(added)}")
        if conflicts:
            msg_parts.append(f"已覆盖: {', '.join(conflicts)}")
        if skipped:
            msg_parts.append(f"跳过: {', '.join(skipped)}")
        if msg_parts:
            self._refresh_presets()
            QMessageBox.information(self, "导入结果", "\n".join(msg_parts))
        else:
            QMessageBox.information(self, "导入结果", "未找到有效的预设数据。")

    def _export_presets(self):
        """导出所有预设到文件。"""
        path, _ = QFileDialog.getSaveFileName(
            self, "导出自动着色预设", "keyword_color_presets.json",
            "JSON 文件 (*.json);;所有文件 (*)"
        )
        if not path:
            return
        if preset_manager.export_keyword_presets(path):
            QMessageBox.information(self, "成功", f"预设已导出至:\n{path}")
        else:
            QMessageBox.warning(self, "失败", "导出失败。")

    def load_default_preset_if_empty(self):
        """如果当前没有规则，尝试加载默认预设。供软件启动时调用。"""
        if self.rows_layout.count() > 0:
            return  # 已有规则，不覆盖
        rules = preset_manager.load_default_keyword_preset()
        if rules and isinstance(rules, list) and len(rules) > 0:
            self.set_data(rules)
