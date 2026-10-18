# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Central shortcut table for the T03b shell. T05 items are registered only."""
from __future__ import annotations

from cutora.ui.i18n import tr

# id, key sequence, Chinese label, slot on MainWindow (None = later task).
SHORTCUTS = (
    ("play_pause", "Space", "播放/暂停", "toggle_play_pause"),
    ("stop", "K", "停止", "shortcut_stop"),
    ("play_fwd", "L", "正放", "shortcut_play_fwd"),
    ("play_rev", "J", "倒放（定时反向 seek）", "shortcut_play_rev"),
    ("mark_in", "I", "设入点", "shortcut_mark_in"),
    ("mark_out", "O", "设出点", "shortcut_mark_out"),
    ("clear_in", "Shift+I", "清除入点", "shortcut_clear_in"),
    ("clear_out", "Shift+O", "清除出点", "shortcut_clear_out"),
    ("frame_prev", "Left", "前一帧", "shortcut_frame_prev"),
    ("frame_next", "Right", "后一帧", "shortcut_frame_next"),
    ("jump_back", "Shift+Left", "前 1 秒", "shortcut_jump_back"),
    ("jump_fwd", "Shift+Right", "后 1 秒", "shortcut_jump_fwd"),
    ("goto_start", "Home", "到开头", "shortcut_goto_start"),
    ("goto_end", "End", "到结尾", "shortcut_goto_end"),
    ("split", "S", "在播放头分割", "shortcut_split"),
    ("delete", "Delete", "波纹删除选中片段（时间线；Backspace = 留空隙）", None),
    ("undo", "Ctrl+Z", "撤销", "undo"),
    ("redo", "Ctrl+Y", "重做", "redo"),
    ("save", "Ctrl+S", "保存", "save_project"),
    ("save_as", "Ctrl+Shift+S", "另存为", "save_project_as"),
    ("open", "Ctrl+O", "打开", "open_project"),
    ("new", "Ctrl+N", "新建", "new_project"),
    ("import_media", "Ctrl+I", "导入媒体", "import_media"),
    ("export", "Ctrl+E", "导出", "export_video"),
    ("batch_export", "Ctrl+Shift+E", "批量导出", "toggle_batch_export"),
    ("marker", "M", "在播放头添加标记", "shortcut_marker_add"),
    ("marker_prev", "Alt+Left", "上一个标记", "shortcut_marker_prev"),
    ("marker_next", "Alt+Right", "下一个标记", "shortcut_marker_next"),
    ("marker_prev_shift", "Shift+Up", "上一个标记", "shortcut_marker_prev"),
    ("marker_next_shift", "Shift+Down", "下一个标记", "shortcut_marker_next"),
    ("settings", "Ctrl+,", "设置", "open_settings_dialog"),
    ("console", "Ctrl+Shift+L", "控制台", "open_console"),

    ("fullscreen", "F", "全屏预览", "toggle_preview_fullscreen"),
    ("safe_zones", "Shift+S", "安全区参考层", "toggle_safe_zones"),
    ("add_text", "Ctrl+T", "添加文本", "add_text_ov"),
    ("add_image", "Ctrl+Shift+I", "添加图片", "add_img_ov"),
    ("zoom_in", "+", "时间线放大", "shortcut_zoom_in"),
    ("zoom_out", "-", "时间线缩小", "shortcut_zoom_out"),
    ("zoom_fit", "Z", "时间线全览", "shortcut_zoom_fit"),
    ("zoom_selection", "Shift+Z", "缩放到选区", "shortcut_zoom_selection"),
    ("keyframe_editor", "Alt+K", "关键帧编辑器（关键帧面板 / 运动曲线）", "toggle_keyframe_editor"),
    ("node_editor", "Alt+N", "节点编辑器（图层的效果节点图）", "toggle_node_editor"),

    # 时间线加轨道（Alt + 轨道类型首字母）
    ("track_add_video", "Alt+V", "新建视频轨道", "shortcut_add_video_track"),
    ("track_add_audio", "Alt+A", "新建音频轨道", "shortcut_add_audio_track"),
    ("track_add_text", "Alt+T", "新建文本轨道", "shortcut_add_text_track"),
    ("track_add_image", "Alt+I", "新建图片轨道", "shortcut_add_image_track"),
    ("track_add_adjust", "Alt+J", "新建调整轨道（纯色 / 局部效果）", "shortcut_add_adjust_track"),

    # 画布文本层：样式刷（Canva 同款键位）
    ("text_style_copy", "Ctrl+Alt+C", "复制文本样式", "shortcut_copy_text_style"),
    ("text_style_paste", "Ctrl+Alt+V", "粘贴文本样式", "shortcut_paste_text_style"),
)


# Q145：画布 / 中央监视器 / 时间线里的快捷操作（焦点在哪块就归哪块，不在这里注册 QShortcut，
# 只给帮助表和设置页看）。(区域, 按键或手势, 作用)。设计见 docs/plan/Q145_SHORTCUTS_DESIGN.md。
GESTURE_ROWS = (
    ("全局", "Ctrl+Shift+M", "打开中央监视器"),
    ("全局", "← / →", "逐帧（按工程帧率）；画布有选中对象时改为微移"),
    ("画布", "拖", "移动（Shift 锁横竖，按住 N 临时不吸附）"),
    ("画布", "Alt + 拖", "复制一份再拖（文字 / 图片）"),
    ("画布", "Alt + 点", "点穿透明区域，选中下面的对象"),
    ("画布", "Ctrl + 拖", "缩放：视频以按下点为中心，文字 / 图片以自身中心（文字无损）"),
    ("画布", "Ctrl + 滚轮", "缩放光标下已选中的对象；没有选中就缩放视图"),
    ("画布", "Alt + 滚轮", "缩放视图（以光标为中心）"),
    ("画布", "中键拖 / 按住 H 拖", "平移视图；视图放大后滚轮 / Shift+滚轮也能平移"),
    ("画布", "Ctrl+= / Ctrl+−", "视图放大 / 缩小"),
    ("画布", "Shift+Z", "视图适应窗口"),
    ("画布", "Ctrl+0", "视图 100%（一个工程像素 = 一个屏幕像素）"),
    ("画布", "方向键 / Shift+方向键", "选中对象微移 1 px / 10 px（一串按键算一次撤销）"),
    ("画布", "Delete / Backspace", "删除选中的文字 / 图片"),
    ("画布", "Ctrl+C / Ctrl+X / Ctrl+V", "复制 / 剪切 / 粘贴（和中央监视器共用，可跨视频粘贴）"),
    ("画布", "Ctrl+D", "原地复制"),
    ("画布", "Ctrl+A", "全选当前画面上的文字 / 图片"),
    ("画布", "Esc", "取消正在拖的操作（恢复原样）；否则清空选择"),
    ("画布", "Shift + 拖旋转手柄", "按 15° 一档"),
    ("画布", "Alt + 拖缩放手柄", "从中心缩放（图片 / 贴纸 / 视频）"),
    ("画布", "拖分界线", "调位置（吸附 1/3、1/2、2/3）；Alt+拖 = 旋转；滚轮 = 羽化；双击复位"),
    ("画布", "双击视频画面", "重置位置 / 缩放 / 旋转"),
    ("画布", "双击文字", "进入编辑（Ctrl+Enter / 点外面 结束）"),
    ("中央监视器", "拖画面 / Ctrl+拖 / Ctrl+滚轮", "平移 / 以光标为中心缩放这一格"),
    ("中央监视器", "Alt + 拖格子", "和另一格交换"),
    ("中央监视器", "拖文字到别的视频", "移过去（Alt = 复制过去）"),
    ("中央监视器", "Ctrl / Shift + 点、框选", "多选视频；配合「同步编辑」一起改"),
    ("中央监视器", "从素材库拖素材进来", "放到落点；落在多选上 = 全部选中的都放"),
    ("中央监视器", "Ctrl+Shift+V", "粘贴到全部视频"),
    ("时间线", "V / C / R", "选择 / 切刀 / 变速工具"),
    ("时间线", "Delete", "波纹删除：片段连同空隙一起删掉，同一轨后面的内容前移补位（达芬奇习惯）"),
    ("时间线", "Backspace", "普通删除：删掉选中的片段、字幕、文字、图片，留下空隙（一次撤销）"),
    ("时间线", "Q / W 或 Shift+[ / Shift+]", "波纹修剪：播放头所在片段的开头 / 结尾剪到播放头，后面前移"),
    ("时间线", "按住播放头拖", "标尺上抓住播放头头部一直拖（擦洗，松手恢复全分辨率）"),
    ("时间线", "↑ / ↓", "跳到上一个 / 下一个剪辑点"),
    ("时间线", ", / .", "选中项微移 1 帧（Shift ×10；片段、字幕、文字、图片都行）"),
    ("时间线", "Ctrl+C / Ctrl+X / Ctrl+V", "复制 / 剪切 / 粘贴到播放头"),
    ("时间线", "Ctrl+D", "原地复制（接在选中内容后面）"),
    ("时间线", "Ctrl+= / Ctrl+−", "以播放头为中心缩放"),
    ("时间线", "Ctrl + 滚轮 / Shift + 滚轮", "以光标为中心缩放 / 横向滚动"),
    ("时间线", "拖标尺 / 拖空白处（播放头模式）", "擦洗预览（拖动期间半分辨率，松手恢复）"),
    ("时间线", "Ctrl + 点 / 框选，然后拖", "多选后整组移动（文字 / 图片也能多选）"),
    ("时间线", "Alt + 拖片段", "复制片段"),
    ("时间线", "Ctrl+B / S", "在播放头切割"),
    ("时间线", "Z / Shift+Z", "全览 / 缩放到选区（时间线有焦点时）"),
    ("时间线", "按住 N", "临时不吸附"),
    ("关键帧编辑器", "点 / Shift+点 / Ctrl+点 / 框选", "选择关键帧；点左栏参数名 = 选中这一行全部"),
    ("关键帧编辑器", "拖关键帧", "平移（吸附到帧、播放头、其它关键帧；按住 Ctrl 不吸附）"),
    ("关键帧编辑器", "Alt + 拖首 / 尾关键帧", "以另一端为中心缩放时距"),
    ("关键帧编辑器", "曲线里拖关键帧 / 手柄", "改时间和值（Shift 只改一个方向）/ 改快慢（Alt 断开两侧手柄）"),
    ("关键帧编辑器", "双击空白 / 双击关键帧", "在那个时刻加关键帧 / 播放头跳过去"),
    ("关键帧编辑器", "F9 / Shift+F9 / Ctrl+Shift+F9", "缓动 / 缓入 / 缓出"),
    ("关键帧编辑器", "J / K", "上 / 下一个关键帧（编辑器有焦点时）"),
    ("关键帧编辑器", "Shift+F3 / Shift+Z", "关键帧 ↔ 曲线 / 适应窗口"),
    ("关键帧编辑器", "Ctrl+滚轮 / 滚轮 / Ctrl+Shift+滚轮", "缩放时间 / 平移 / 缩放数值（曲线）"),
    ("时间线", "Ctrl+G / Ctrl+Shift+G", "选中的片段预合成（打组）/ 解除预合成；右键片段还有锁定、循环铺满、改色、加入 / 分离"),
    ("时间线", "点组里的一条 / 拖一条", "锁住的预合成组整组选上 / 整组一起走（解锁后才单条）"),
    ("节点编辑器", "从端口拖线", "松在端口 / 节点上 = 连上；松在空白处 = 弹出菜单，新节点直接接好"),
    ("节点编辑器", "从接了线的输入端口往外拖", "拎起这根线：拖到别的端口改接，拖到空白处断开"),
    ("节点编辑器", "拖节点 / 框选 / 中键或空格拖 / 滚轮", "移动（吸附网格）/ 多选 / 平移 / 缩放"),
    ("节点编辑器", "双击节点 / 双击空白", "在检查器里编辑它 / 搜索加节点"),
    ("节点编辑器", "Delete / Ctrl+P / Ctrl+T / F2", "删除（上下游接上）/ 旁路 / 交换两个输入 / 改名"),
    ("节点编辑器", "1 / Esc", "画面里只看选中节点的结果（只影响预览）/ 关查看器"),
    ("节点编辑器", "Shift+空格 / Tab", "搜索加节点（插在选中节点后面）"),
    ("节点编辑器", "Ctrl+L / F", "自动排布 / 全部放进视野（编辑器有焦点时）"),
)


def help_rows():
    """帮助表 / 设置页用：(区域, 按键, 作用)。全局表在前，手势在后。"""
    rows = [("全局", key, tr(label)) for _sid, key, label, _slot in SHORTCUTS]
    rows.extend((area, key, tr(label)) for area, key, label in GESTURE_ROWS)
    return rows


def shortcut_key(sid: str) -> str | None:
    """Key sequence for a SHORTCUTS id, or None."""
    for row_id, key, _label, _slot in SHORTCUTS:
        if row_id == sid:
            return key
    return None


def shortcut_rows():
    """Return ``(key, label)`` pairs for the help dialog."""
    return [(key, tr(label)) for _sid, key, label, _slot in SHORTCUTS]


def install_shortcuts(window) -> None:
    """Bind application shortcuts that are not already QAction accelerators.

    File/edit actions keep their QAction shortcuts. This installs the
    remaining keys (playback, mode, zoom, later-task placeholders).
    """
    from PySide6.QtGui import QKeySequence, QShortcut
    from PySide6.QtCore import Qt

    skip = {
        "undo", "redo", "save", "save_as", "open", "new",
        "delete", "settings", "console", "import_media", "export", "batch_export",
        "play_pause", "fullscreen", "safe_zones", "add_text", "add_image",
        "zoom_fit", "zoom_selection", "keyframe_editor", "node_editor",
        # 加轨道走「编辑 → 添加轨道」里的 QAction，别在这里重复绑（会变成歧义快捷键）
        "track_add_video", "track_add_audio", "track_add_text", "track_add_image", "track_add_adjust",
    }
    window._extra_shortcuts = []
    for sid, key, label, slot in SHORTCUTS:
        if sid in skip:
            continue
        sc = QShortcut(QKeySequence(key), window)
        ctx = (Qt.ShortcutContext.WindowShortcut if sid == "toggle_mode"
               else Qt.ShortcutContext.ApplicationShortcut)
        sc.setContext(ctx)
        if slot and hasattr(window, slot):
            sc.activated.connect(getattr(window, slot))
        else:
            sc.activated.connect(
                lambda text=tr(label): window.statusBar().showMessage(
                    tr("{name} 将在后续任务实现", name=text), 2500)
            )
        window._extra_shortcuts.append(sc)


class ShortcutHelpDialog:
    """Lazy wrapper so tests can import the table without constructing Qt."""

    @staticmethod
    def exec_dialog(parent=None) -> None:
        from PySide6.QtWidgets import (
            QDialog, QDialogButtonBox, QTableWidget, QTableWidgetItem,
            QVBoxLayout, QLabel,
        )
        from cutora.ui.i18n import localize_button_box

        dlg = QDialog(parent)
        dlg.setWindowTitle(tr("快捷键"))
        dlg.resize(760, 640)
        lay = QVBoxLayout(dlg)
        lay.addWidget(QLabel(tr("片语 Studio 快捷键 · 快捷键跟着焦点走：点一下画布，方向键 / Delete / Ctrl+C 就归画布")))
        rows = help_rows()
        table = QTableWidget(len(rows), 3)
        table.setHorizontalHeaderLabels([tr("区域"), tr("按键"), tr("功能")])
        table.horizontalHeader().setStretchLastSection(True)
        table.setAlternatingRowColors(True)
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        for i, (area, key, label) in enumerate(rows):
            table.setItem(i, 0, QTableWidgetItem(tr(area)))
            table.setItem(i, 1, QTableWidgetItem(key))
            table.setItem(i, 2, QTableWidgetItem(label))
        table.resizeColumnsToContents()
        lay.addWidget(table, 1)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        localize_button_box(box)
        box.accepted.connect(dlg.accept)
        lay.addWidget(box)
        dlg.exec()
