# SPDX-License-Identifier: GPL-3.0-or-later
# Extracted from the T01 monolith by tools/split_monolith.py.
# Original lines: 6872-7105

from PySide6.QtWidgets import (
    QWidget,
    QHBoxLayout,
    QVBoxLayout,
    QListWidget,
    QPushButton,
    QLabel,
    QSplitter,
    QScrollArea,
    QSizePolicy,
    QAbstractItemView,
    QInputDialog,
    QMenu,
)
from PySide6.QtCore import (
    Qt,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QIcon,
    QPixmap,
)
from cutora.ui.panels.subtitle_style import SubtitleStylePanel
from PySide6.QtWidgets import (
    QAbstractItemView,
    QMenu,
)
from PySide6.QtCore import Qt
from cutora.ui.panels.subtitle_style import SubtitleStylePanel
# --- MISSING Property Panel (Restored) ---
class WordBlockPanel(QWidget):
    adv_block_request = Signal()
    adv_block_edit_request = Signal(int)
    adv_block_delete_request = Signal(int)
    adv_block_modified_request = Signal(int, object) # emitted when a block style is changed
    
    def __init__(self, parent=None):
        super().__init__(parent)
        main_layout = QVBoxLayout(self)
        self.splitter = QSplitter(Qt.Orientation.Vertical)
        main_layout.addWidget(self.splitter)
        
        # -- Left Panel --
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)
        
        self.lbl_selection = QLabel("字块选区：未选择")
        self.lbl_selection.setObjectName("hint")
        self.lbl_selection.setWordWrap(True)
        self.lbl_selection.setMaximumHeight(60)
        self.lbl_selection.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        left_layout.addWidget(self.lbl_selection)
        
        self.btn_add_block = QPushButton("添加字块（重选文字后点击）")
        pix = QPixmap(16, 16)
        pix.fill(QColor("#FFA500"))
        self.btn_add_block.setIcon(QIcon(pix))
        self.btn_add_block.clicked.connect(self.adv_block_request.emit)
        left_layout.addWidget(self.btn_add_block)
        
        self.list_blocks = QListWidget()
        self.list_blocks.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.list_blocks.currentRowChanged.connect(self._on_row_changed)
        
        # Add Context Menu to List (Rename/Delete)
        self.list_blocks.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list_blocks.customContextMenuRequested.connect(self._show_context_menu)
        
        left_layout.addWidget(self.list_blocks)
        
        btn_row = QHBoxLayout()
        self.btn_del_block = QPushButton("删除选中字块")
        self.btn_del_block.clicked.connect(self._on_delete_clicked)
        btn_row.addWidget(self.btn_del_block)
        left_layout.addLayout(btn_row)
        
        # -- Right Panel --
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)
        
        self.lbl_preview = QLabel("［字块内容预览］")
        self.lbl_preview.setObjectName("sectionHeader")
        self.lbl_preview.setWordWrap(True)
        self.lbl_preview.setMaximumHeight(80)
        self.lbl_preview.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        right_layout.addWidget(self.lbl_preview)
        
        self.style_panel = SubtitleStylePanel(is_word_block=True)
        self.style_panel.load_data({}) # init empty
        self.style_panel.param_changed.connect(self._on_style_changed)
        
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(self.style_panel)
        scroll.setObjectName("multiTextScroll")
        right_layout.addWidget(scroll)
        
        self.splitter.addWidget(left_widget)
        self.splitter.addWidget(right_widget)
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 3)
        
        self.current_blocks = []
        self.current_seg_text = ""
        self._is_loading = False

    def _show_context_menu(self, pos):
        item = self.list_blocks.itemAt(pos)
        if not item: return
        row = self.list_blocks.row(item)
        menu = QMenu(self)
        act_rename = menu.addAction("✏️ 重命名字块")
        # 全局字块不可删除
        is_global = (0 <= row < len(self.current_blocks)
                     and self.current_blocks[row].get('is_global'))
        act_del = menu.addAction("🗑 删除字块")
        if is_global:
            act_del.setEnabled(False)
        action = menu.exec(self.list_blocks.mapToGlobal(pos))
        if action == act_rename:
            self._on_rename_clicked()
        elif action == act_del:
            self._on_delete_clicked()

    def _on_rename_clicked(self):
        row = self.list_blocks.currentRow()
        if row < 0 or row >= len(self.current_blocks): return
        old_name = self.current_blocks[row].get('name', f'字块{row+1}')
        new_name, ok = QInputDialog.getText(self, "重命名字块", "输入新名称:", text=old_name)
        if ok and new_name.strip():
            self.current_blocks[row]['name'] = new_name.strip()
            self._update_list_ui()
            self.adv_block_modified_request.emit(row, self.current_blocks[row])

    def _on_row_changed(self, row):
        if self._is_loading: return
        if row < 0 or row >= len(self.current_blocks):
            self.lbl_preview.setText("［字块内容预览］无选定")
            self.style_panel.setEnabled(False)
            return
            
        b = self.current_blocks[row]
        ov = b.get('overrides', {})
        self.style_panel.setEnabled(True)
        self._is_loading = True
        import copy
        preview_data = copy.deepcopy(getattr(self, 'task_style', {}))
        preview_data.update(copy.deepcopy(ov))
        self.style_panel.load_data(preview_data)
        self._is_loading = False
        
        # update preview
        s_idx, e_idx = b.get('start_char', 0), b.get('end_char', 0)
        try:
            preview_txt = self.current_seg_text[s_idx:e_idx]
        except Exception:
            preview_txt = ""
        # 截断超长文本防止擑开 UI
        if len(preview_txt) > 80:
            preview_txt = preview_txt[:80] + '…'
        self.lbl_preview.setText(f"目前字块文本: 【 {preview_txt} 】")
        
        # 自动在画布上选中该字块
        self.adv_block_edit_request.emit(row)

    def _on_style_changed(self, key):
        if self._is_loading: return
        row = self.list_blocks.currentRow()
        if row < 0 or row >= len(self.current_blocks): return
        
        panel_data = self.style_panel.current_data or {}
        existing_ov = self.current_blocks[row].get('overrides', {})
        
        # 提取核心布局隔离属性，绝不允许被面板默认值冲刷
        _LAYOUT_KEYS = {'is_detached', 'advanced_textbox_enabled', 'advanced_textbox_x', 'advanced_textbox_y', 'advanced_textbox_w', 'advanced_textbox_h'}
        
        new_ov = {}
        ts = getattr(self, 'task_style', {})
        
        # 仅当面板值与全局基层不一致时，才存入 override，保证干净继承
        import copy
        for k, v in panel_data.items():
            if k not in _LAYOUT_KEYS:
                if k not in ts or ts.get(k) != v:
                    new_ov[k] = copy.deepcopy(v)

        for k in _LAYOUT_KEYS:
            if k in existing_ov:
                new_ov[k] = existing_ov[k]

        # 若刚才用户点击了“启用字块分离”复选框，从 panel_data 直接吃进 is_detached
        if 'is_detached' in panel_data:
            new_ov['is_detached'] = panel_data['is_detached']

        self.current_blocks[row]['overrides'] = new_ov
        self.adv_block_modified_request.emit(row, self.current_blocks[row])

    def _update_list_ui(self):
        self._is_loading = True
        curr_idx = self.list_blocks.currentRow()
        self.list_blocks.clear()
        for i, b in enumerate(self.current_blocks):
            name = b.get('name', f'字块{i+1}')
            start = b.get('start_char', 0)
            end = b.get('end_char', 0)
            ov = b.get('overrides', {})
            c = ov.get('color_text', '')
            hc = ov.get('color_high', '')
            # 视觉标记：全局字块 / 已分离字块
            prefix = "🌐" if b.get('is_global') else ("🔓" if ov.get('is_detached') else "▪")
            desc = ""
            if c: desc += f" 字:{c}"
            if hc: desc += f" 高亮:{hc}"
            self.list_blocks.addItem(f"{prefix} {name} [{start}-{end}]{desc}")
        if 0 <= curr_idx < self.list_blocks.count():
            self.list_blocks.setCurrentRow(curr_idx)
        self._is_loading = False

    def _on_delete_clicked(self):
        row = self.list_blocks.currentRow()
        if row < 0 or row >= len(self.current_blocks):
            return
        # 前端保护：全局字块不可删除
        if self.current_blocks[row].get('is_global'):
            return
        self.adv_block_delete_request.emit(row)

    def set_adv_block_label(self, text):
        if hasattr(self, 'lbl_selection'):
            self.lbl_selection.setText(text)

    def set_adv_block_list(self, blocks, seg_text="", task_style=None):
        self._is_loading = True
        self.task_style = task_style or {}
        self.current_blocks = blocks or []
        self.current_seg_text = seg_text
        self.list_blocks.clear()
        for i, b in enumerate(self.current_blocks):
            name = b.get('name', f'字块{i+1}')
            start = b.get('start_char', 0)
            end = b.get('end_char', 0)
            ov = b.get('overrides', {})
            c = ov.get('color_text', '')
            hc = ov.get('color_high', '')
            # 视觉标记：全局字块 / 已分离字块
            prefix = "🌐" if b.get('is_global') else ("🔓" if ov.get('is_detached') else "▪")
            desc = ""
            if c: desc += f" 字:{c}"
            if hc: desc += f" 高亮:{hc}"
            self.list_blocks.addItem(f"{prefix} {name} [{start}-{end}]{desc}")
        
        if len(self.current_blocks) == 0:
            self.style_panel.setEnabled(False)
            self.lbl_preview.setText("［字块内容预览］无选定")
        elif self.list_blocks.currentRow() < 0:
            self.list_blocks.setCurrentRow(0)
            
        self._is_loading = False
        
        if self.list_blocks.currentRow() >= 0:
            self._on_row_changed(self.list_blocks.currentRow())
