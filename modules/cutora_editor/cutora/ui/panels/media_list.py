# SPDX-License-Identifier: GPL-3.0-or-later
# Extracted from the T01 monolith by tools/split_monolith.py.
# Original lines: 2280-2282, 2284-2554

from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFileDialog,
    QMenu,
    QStyle,
    QTreeWidget,
    QTreeWidgetItem,
)
from PySide6.QtCore import (
    QEvent,
    Qt,
    Signal,
)
from PySide6.QtGui import (
    QAction,
    QIcon,
    QImage,
    QKeyEvent,
    QPainter,
    QPainterPath,
    QPixmap,
)
from cutora.render.readers import _open_video_capture
from cutora.render.readers import _open_video_capture
class TaskTreeWidgetItem(QTreeWidgetItem):
    def text(self, col=0):
        return super().text(col)

class DraggableListWidget(QTreeWidget):
    files_dropped = Signal(list)
    order_changed = Signal(list, list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setHeaderHidden(True)
        self.setAcceptDrops(True)
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self.show_context_menu)
        self.itemChanged.connect(self._sync_folder_check_state)

    def _sync_folder_check_state(self, item, col):
        if item.data(0, Qt.ItemDataRole.UserRole) == "folder":
            self.blockSignals(True)
            state = item.checkState(0)
            for i in range(item.childCount()):
                item.child(i).setCheckState(0, state)
            self.blockSignals(False)

    def _get_all_leaves(self, parent=None):
        leaves = []
        root = parent if parent is not None else self.invisibleRootItem()
        for i in range(root.childCount()):
            child = root.child(i)
            if child.data(0, Qt.ItemDataRole.UserRole) == "folder":
                leaves.extend(self._get_all_leaves(child))
            else:
                leaves.append(child)
        return leaves

    def row(self, item):
        if item is None or item.data(0, Qt.ItemDataRole.UserRole) == "folder":
            return -1
        leaves = self._get_all_leaves()
        try:
            return leaves.index(item)
        except ValueError:
            return -1

    def item(self, row_idx):
        leaves = self._get_all_leaves()
        if 0 <= row_idx < len(leaves):
            return leaves[row_idx]
        return None

    def count(self):
        return len(self._get_all_leaves())

    def leaf_texts(self):
        """全部媒体名（一次遍历）。Q159：撤销快照每步都要这份名单，以前 item(i) 逐个取是 O(n²)。"""
        return [it.text(0) for it in self._get_all_leaves()]

    def takeItem(self, row_idx):
        item_to_remove = self.item(row_idx)
        if item_to_remove:
            parent = item_to_remove.parent() or self.invisibleRootItem()
            parent.removeChild(item_to_remove)
            return item_to_remove
        return None

    def addItem(self, name, filepath="", decode_thumb=True):
        item = TaskTreeWidgetItem([str(name)])
        item.setFlags((item.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEditable | Qt.ItemFlag.ItemIsDragEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled) & ~Qt.ItemFlag.ItemIsDropEnabled)
        item.setCheckState(0, Qt.CheckState.Checked)
        item.setData(0, Qt.ItemDataRole.UserRole, str(filepath or ""))

        if filepath and decode_thumb:
            import os
            if os.path.exists(filepath):
                try:
                    import cv2
                    cap = _open_video_capture(filepath)
                    if cap is not None:
                        ret, frame = cap.read()
                        if ret:
                            if len(frame.shape) == 3 and frame.shape[2] == 4:
                                frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2RGB)
                            else:
                                frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                            h, w, c = frame.shape
                            qimg = QImage(frame.data, w, h, c * w, QImage.Format.Format_RGB888)
                            pixmap = QPixmap.fromImage(qimg).scaled(64, 48, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
                            rounded = QPixmap(pixmap.size())
                            rounded.fill(Qt.GlobalColor.transparent)
                            painter = QPainter(rounded)
                            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                            path = QPainterPath()
                            from cutora.ui.theme.tokens import RADIUS_SM
                            path.addRoundedRect(0, 0, pixmap.width(), pixmap.height(), RADIUS_SM, RADIUS_SM)
                            painter.setClipPath(path)
                            painter.drawPixmap(0, 0, pixmap)
                            painter.end()
                            item.setIcon(0, QIcon(rounded))
                    cap.release()
                except Exception:
                    pass
        elif filepath:
            pix = QPixmap(64, 48)
            pix.fill(Qt.GlobalColor.darkGray)
            item.setIcon(0, QIcon(pix))
        self.addTopLevelItem(item)

    def currentRow(self):
        return self.row(self.currentItem())

    def setCurrentRow(self, row_idx):
        target = self.item(row_idx)
        if target:
            self.setCurrentItem(target)
        else:
            self.setCurrentItem(None)

    def selectedItems(self):
        sel = super().selectedItems()
        return [i for i in sel if i.data(0, Qt.ItemDataRole.UserRole) != "folder"]

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls(): 
            event.accept()
        else: 
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event):
        if event.mimeData().hasUrls():
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()
            links = [str(u.toLocalFile()) for u in event.mimeData().urls()]
            self.files_dropped.emit(links)
        else:
            old_leaves = self._get_all_leaves()
            super().dropEvent(event)
            new_leaves = self._get_all_leaves()
            self.order_changed.emit(old_leaves, new_leaves)

    def mouseDoubleClickEvent(self, event):
        item = self.itemAt(event.pos())
        if item is None:
            paths, _ = QFileDialog.getOpenFileNames(
                self, "导入视频或工程", "",
                "视频或工程 (*.mp4 *.mov *.mkv *.avi *.webm *.m4v *.cutora *.asproj.json *.asproj);;片语工程 (*.cutora *.asproj.json *.asproj);;视频文件 (*.mp4 *.mov *.mkv *.avi);;所有文件 (*)")
            if paths:
                self.files_dropped.emit(paths)
        else:
            super().mouseDoubleClickEvent(event)

    def show_context_menu(self, pos):
        menu = QMenu(self)
        # Q96: 两条最常用的共享动作置顶。菜单是自上而下 addAction 的，
        # 所以它们必须在别的条目之前加进去。
        act_copy_style = act_share = None
        src_item = self.itemAt(pos)
        parent_win = self.window()
        if (src_item is not None
                and src_item.data(0, Qt.ItemDataRole.UserRole) != "folder"
                and hasattr(parent_win, "add_bin_batch_menu_actions")):
            act_copy_style, act_share = parent_win.add_bin_batch_menu_actions(
                menu, src_item)
            menu.addSeparator()

        action_rmbg_cutout = QAction("✂️ AI 一键抠图去背景 (RMBG-2.0 发丝级透明抠图)", self)
        action_export_frame = QAction("导出选中视频首帧图片", self)
        action_export_last_frame = QAction("导出选中视频尾帧图片", self)
        action_delete = QAction("删除", self)
        action_rename = QAction("重命名", self)
        action_group = QAction("打组 (新建文件夹)", self)
        action_check_hl = QAction("选中", self)
        action_uncheck_hl = QAction("取消选中", self)
        action_select_all = QAction("全选", self)
        action_deselect_all = QAction("取消全选", self)
        
        menu.addAction(action_rmbg_cutout)
        menu.addSeparator()
        menu.addAction(action_export_frame)
        menu.addAction(action_export_last_frame)
        menu.addAction(action_delete)
        menu.addAction(action_rename)
        menu.addAction(action_group)
        menu.addSeparator()
        menu.addAction(action_check_hl)
        menu.addAction(action_uncheck_hl)
        menu.addSeparator()
        menu.addAction(action_select_all)
        menu.addAction(action_deselect_all)

        act_yellow = act_corrected = None
        if hasattr(parent_win, "_rerun_yellow_asr"):
            menu.addSeparator()
            act_yellow = QAction("重跑标黄的行", self)
            act_corrected = QAction("重跑纠正过的行", self)
            menu.addAction(act_yellow)
            menu.addAction(act_corrected)

        action = menu.exec(self.mapToGlobal(pos))
        if action == action_rmbg_cutout:
            from cutora.ui.ai_studio_room import run_rmbg_cutout_ui
            p = None
            if src_item is not None:
                raw_p = src_item.data(0, Qt.ItemDataRole.UserRole)
                if isinstance(raw_p, str) and raw_p != "folder" and os.path.exists(raw_p):
                    p = raw_p
            run_rmbg_cutout_ui(parent_win, source_path=p)
        elif action == action_group:
            self._create_folder()
        elif action == action_select_all:
            self._set_all_check_state(Qt.CheckState.Checked)
        elif action == action_deselect_all:
            self._set_all_check_state(Qt.CheckState.Unchecked)
        elif action == action_check_hl:
            for it in super().selectedItems():
                it.setCheckState(0, Qt.CheckState.Checked)
        elif action == action_uncheck_hl:
            for it in super().selectedItems():
                it.setCheckState(0, Qt.CheckState.Unchecked)
        elif action == action_rename:
            item = self.itemAt(pos)
            if item:
                self.editItem(item, 0)
        elif action == action_delete:
            self._request_delete()
        elif action == action_export_frame:
            parent_win = self.parent().window()
            if hasattr(parent_win, 'export_selected_first_frames'):
                parent_win.export_selected_first_frames()
        elif action == action_export_last_frame:
            parent_win = self.parent().window()
            if hasattr(parent_win, 'export_selected_last_frames'):
                parent_win.export_selected_last_frames()
        elif action is act_copy_style and act_copy_style is not None:
            parent_win.on_bin_copy_style(src_item)
        elif action is act_share and act_share is not None:
            parent_win.on_bin_share_tracks(src_item)
        elif action is act_yellow and act_yellow is not None:
            panel = getattr(parent_win, "_asr_batch_results", None)
            ids = panel.yellow_ids() if panel is not None else []
            parent_win._rerun_yellow_asr(ids)
        elif action is act_corrected and act_corrected is not None:
            panel = getattr(parent_win, "_asr_batch_results", None)
            ids = panel.corrected_ids() if panel is not None else []
            parent_win._rerun_corrected_asr(ids)

    def _create_folder(self):
        item = QTreeWidgetItem(["新文件夹"])
        item.setData(0, Qt.ItemDataRole.UserRole, "folder")
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsDropEnabled | Qt.ItemFlag.ItemIsDragEnabled | Qt.ItemFlag.ItemIsEditable | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(0, Qt.CheckState.Checked)
        item.setIcon(0, QApplication.style().standardIcon(QStyle.StandardPixmap.SP_DirIcon))
        
        old_leaves = self._get_all_leaves()
        
        selected = self.selectedItems()
        target_idx = -1
        if selected:
            for i in range(self.topLevelItemCount()):
                if self.topLevelItem(i) in selected:
                    target_idx = i
                    break
                    
        if target_idx == -1:
            self.addTopLevelItem(item)
        else:
            self.insertTopLevelItem(target_idx, item)
            
        for sel in selected:
            parent = sel.parent()
            if parent:
                parent.takeChild(parent.indexOfChild(sel))
            else:
                self.takeTopLevelItem(self.indexOfTopLevelItem(sel))
            item.addChild(sel)
            
        item.setExpanded(True)
        if selected:
            new_leaves = self._get_all_leaves()
            self.order_changed.emit(old_leaves, new_leaves)

    def _set_all_check_state(self, state):
        def _walk(parent):
            for i in range(parent.childCount()):
                child = parent.child(i)
                child.setCheckState(0, state)
                _walk(child)
        _walk(self.invisibleRootItem())

    def _request_delete(self):
        sel = super().selectedItems()
        for item in sel:
            if item.data(0, Qt.ItemDataRole.UserRole) == "folder":
                self._select_all_leaves(item)
        
        import PySide6.QtWidgets as QtWidgets
        key_evt = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Delete, Qt.KeyboardModifier.NoModifier)
        QtWidgets.QApplication.instance().sendEvent(self, key_evt)
        
        for item in sel:
            if item.data(0, Qt.ItemDataRole.UserRole) == "folder":
                if item.parent():
                    item.parent().removeChild(item)
                else:
                    self.invisibleRootItem().removeChild(item)

    def _select_all_leaves(self, folder_item):
        for i in range(folder_item.childCount()):
            child = folder_item.child(i)
            if child.data(0, Qt.ItemDataRole.UserRole) == "folder":
                self._select_all_leaves(child)
            else:
                child.setSelected(True)
