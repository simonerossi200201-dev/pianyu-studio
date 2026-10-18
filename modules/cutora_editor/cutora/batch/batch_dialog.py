# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""批量加载对话框。右栏是工程里的 VideoCanvas，不是另一套预览。"""
from __future__ import annotations

import os

from cutora.batch.batch_load import VIDEO_EXTS
from cutora.batch.script_insert_image import (
    DEFAULT_IMAGE,
    IMAGE_EXTS,
    PLATFORMS,
    RENDER_EACH,
    RENDER_ONCE,
    SUGGESTED_IMAGE_DIR,
    file_dialog_start,
    image_size,
    insert_into_tasks,
    is_insert_overlay,
    load_insert_prefs,
    make_overlay,
    normalize_render_mode,
    probe_video_wh,
    save_insert_prefs,
)
from cutora.core.log import get_logger
from cutora.ui.theme.tokens import (
    INSPECTOR_PAIR_GAP,
    INSPECTOR_ROW_GAP,
    INSPECTOR_SECTION_GAP,
    SPACE_1,
    SPACE_2,
    SPACE_3,
    SPACE_4,
    SPACE_6,
)

_LOG = get_logger("cutora.batch.batch_dialog")

# 任务书给定的栏宽和控件宽。都落在 4px 栅格上。
COL_VIDEO = SPACE_6 * 8 + SPACE_1  # 260
COL_PARAM = SPACE_6 * 13 + SPACE_1  # 420
SPIN_W = SPACE_6 * 3  # 96
COMBO_W = SPACE_6 * 5 + SPACE_4 + SPACE_1  # 180


def _bg_fit(task) -> str:
    tl = (task or {}).get("timeline") if isinstance(task, dict) else None
    tracks = getattr(tl, "tracks", None) or []
    for tr in tracks:
        if getattr(tr, "type", "") != "video":
            continue
        for clip in getattr(tr, "clips", None) or []:
            mode = getattr(clip, "fit_mode", None) or "fill"
            return str(mode)
    return "fill"


TEXT_TAB_MIN_W = SPACE_6 * 24   # 768：Q191 工具条一行 + 表格七列不挤
_SPLIT_KEY = "batch_dialog_columns"
_SPLIT_DEFAULT = [COL_VIDEO, COL_PARAM, COL_PARAM * 2]


def _restore_columns(splitter) -> None:
    """把上次拖成的宽度装回去。读不出来就用默认三栏。"""
    sizes = None
    try:
        from cutora.core.history import _load_app_settings
        raw = (_load_app_settings() or {}).get(_SPLIT_KEY)
        if isinstance(raw, (list, tuple)) and len(raw) == 3:
            sizes = [max(80, int(x)) for x in raw]
    except Exception:
        _LOG.exception("读批量加载栏宽失败")
    splitter.setSizes(sizes or list(_SPLIT_DEFAULT))
    splitter.splitterMoved.connect(lambda *_a: _save_columns(splitter))


_SPLIT_PREFS_KEY = "batch_split_prefs"


def _load_split_prefs() -> dict:
    """Q151：上次选的分屏布局 / 模糊背景（Luca 常用左右完整，不用每次重选）。"""
    try:
        from cutora.core.history import _load_app_settings
        raw = (_load_app_settings() or {}).get(_SPLIT_PREFS_KEY)
        return dict(raw) if isinstance(raw, dict) else {}
    except Exception:
        _LOG.exception("读批量分屏设置失败")
        return {}


def _save_split_prefs(prefs: dict) -> None:
    try:
        from cutora.core.history import _load_app_settings, _save_app_settings
        s = _load_app_settings() or {}
        s[_SPLIT_PREFS_KEY] = dict(prefs)
        _save_app_settings(s)
    except Exception:
        _LOG.exception("存批量分屏设置失败")


def _save_columns(splitter) -> None:
    try:
        from cutora.core.history import _load_app_settings, _save_app_settings
        s = _load_app_settings() or {}
        s[_SPLIT_KEY] = [int(x) for x in splitter.sizes()]
        _save_app_settings(s)
    except Exception:
        _LOG.exception("存批量加载栏宽失败")


def _busy_report(frac, text):
    try:
        from cutora.ui.widgets.busy_bar import report
        report(frac, text)
    except Exception:
        pass


def build_batch_dialog(parent):
    """Build the batch-load dialog. Caller shows it."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import (
        QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFormLayout, QFrame,
        QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton,
        QScrollArea, QSizePolicy, QSpinBox, QSplitter, QTabWidget, QVBoxLayout,
        QWidget, QFileDialog, QMessageBox,
    )
    from cutora.batch.batch_load import (
        BLENDS, display_from_sec, placed_offset, sec_from_display,
    )
    from cutora.batch.batch_preview import (
        ImageThumbGrid, fitted_background, prime_video_still, task_background,
        _timeline_has_video,
    )
    from cutora.batch.script_ui import TaskPicker, apply_plugin_style
    from cutora.model.canvas import canvas_wh as _canvas_wh
    from cutora.render.textfx.anim import phase_anim_items
    from cutora.ui.canvas import VideoCanvas
    from cutora.ui.widgets.collapsible import CollapsibleSection

    dlg = QDialog(parent)
    dlg.setWindowTitle("批量加载")
    dlg.resize(SPACE_6 * 45, SPACE_6 * 28)  # 1440 x 896
    apply_plugin_style(dlg)

    root = QVBoxLayout(dlg)
    root.setContentsMargins(SPACE_4, SPACE_4, SPACE_4, SPACE_3)
    root.setSpacing(SPACE_3)

    def _card(inner, pad=SPACE_3):
        """Q191：每一栏是一张圆角卡片（主界面面板同款），卡片之间留白，分隔条平时看不见。"""
        card = QFrame()
        card.setObjectName("roomCard")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(pad, pad, pad, pad)
        lay.setSpacing(0)
        lay.addWidget(inner, 1)
        card.inner = inner
        return card

    # Q123：三栏改成可拖的分隔条。以前中间那栏是 setFixedWidth/setMaximumWidth
    # 写死的 420，文本分页的表格比图片页宽得多，右边直接被切出画面外，
    # 用户也拉不动。现在宽度可拖、下次打开还记得。
    columns = QSplitter(Qt.Orientation.Horizontal)
    columns.setObjectName("roomSplit")
    columns.setChildrenCollapsible(False)
    dlg.column_layout = columns

    video_inner = QWidget()
    video_inner.setObjectName("batchVideoColumn")
    video_lay = QVBoxLayout(video_inner)
    video_lay.setContentsMargins(0, 0, 0, 0)
    video_lay.setSpacing(0)
    picker = TaskPicker(list(getattr(parent, "tasks", None) or []), video_inner)
    video_lay.addWidget(picker)
    video_col = _card(video_inner)
    video_col.setMinimumWidth(SPACE_6 * 5)
    video_col.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
    dlg.video_column = video_col
    dlg.picker = picker

    param_body = QWidget()
    param_lay = QVBoxLayout(param_body)
    param_lay.setContentsMargins(0, 0, SPACE_4, SPACE_2)
    param_lay.setSpacing(INSPECTOR_SECTION_GAP)

    def _title(text):
        lab = QLabel(text)
        lab.setObjectName("dockSectionTitle")
        return lab

    def _group(title):
        host = QWidget()
        lay = QVBoxLayout(host)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(INSPECTOR_ROW_GAP)
        lay.addWidget(_title(title))
        return host, lay

    def _form():
        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setHorizontalSpacing(INSPECTOR_PAIR_GAP)
        form.setVerticalSpacing(INSPECTOR_ROW_GAP)
        form.setLabelAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        form.setFormAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        form.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        return form

    def _fixed(widget, width):
        widget.setFixedWidth(int(width))
        host = QWidget()
        row = QHBoxLayout(host)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        row.addWidget(
            widget, 0,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        row.addStretch(1)
        return host

    def _path_row(edit, button):
        edit.setMinimumWidth(0)
        host = QWidget()
        row = QHBoxLayout(host)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(INSPECTOR_PAIR_GAP)
        row.addWidget(edit, 1)
        row.addWidget(button, 0)
        return host

    prefs = load_insert_prefs()

    _busy_report(0.15, "素材页")
    # —— 素材 ——
    media_host, media_lay = _group("素材")
    media_form = _form()
    ed_dir = QLineEdit()
    ed_dir.setPlaceholderText(r"例如 D:\1_PROJECT\Tiktok_Pic")
    init_dir = (prefs.get("default_dir") or "").strip()
    if not init_dir and os.path.isdir(SUGGESTED_IMAGE_DIR):
        init_dir = SUGGESTED_IMAGE_DIR
    if init_dir:
        ed_dir.setText(init_dir)
    btn_dir = QPushButton("浏览…")
    media_form.addRow("默认找图目录", _path_row(ed_dir, btn_dir))
    dir_row = QWidget()
    dir_row.setLayout(media_form)
    media_lay.addWidget(dir_row)
    media_title = media_lay.itemAt(0).widget() if media_lay.count() else None

    tabs = QTabWidget()
    tab_img = QWidget()
    tab_vid = QWidget()
    tabs.addTab(tab_img, "图片")
    tabs.addTab(tab_vid, "视频")
    media_lay.addWidget(tabs, 1)

    thumb_grid = ImageThumbGrid()
    img_lay = QVBoxLayout(tab_img)
    img_lay.setContentsMargins(0, SPACE_2, 0, 0)
    img_lay.setSpacing(INSPECTOR_ROW_GAP)
    img_lay.addWidget(thumb_grid, 1)
    ed_img = QLineEdit()
    ed_img.setPlaceholderText("单击缩略图，或查找其他位置")
    if os.path.isfile(DEFAULT_IMAGE):
        ed_img.setText(DEFAULT_IMAGE)
    btn_img = QPushButton("查找其他位置…")
    img_lay.addWidget(_path_row(ed_img, btn_img))

    # 图片节点连线：一张图连到多条视频。上面「选一张、插到勾选的视频」照旧。
    btn_img_link = QPushButton("节点连线…  一张图指定给多条视频")
    btn_img_link.setObjectName("btnSecondary")
    btn_img_link.setToolTip(
        "左边是工程里的视频，右边是图片，每个节点都有文件名和缩略图。\n"
        "左边的视频和右边的图各自往下翻。点选、按住 Shift 连选，或拖出框多选，\n"
        "然后在节点上点右键，批量指定要连到的目标。藏起来的图不会出现在菜单里。\n"
        "确定后只记住对应关系，并在右边预览。大小、位置、时间在这一页调，\n"
        "点「插入到所选视频」才写进时间线。每张图有自己的连线颜色。")
    img_lay.addWidget(btn_img_link)
    dlg.btn_link_image = btn_img_link

    vid_grid = ImageThumbGrid(exts=VIDEO_EXTS)
    vid_grid.setObjectName("batchVideoGrid")
    vid_lay = QVBoxLayout(tab_vid)
    vid_lay.setContentsMargins(0, SPACE_2, 0, 0)
    vid_lay.setSpacing(INSPECTOR_ROW_GAP)
    vid_lay.addWidget(vid_grid, 1)
    ed_vid = QLineEdit()
    ed_vid.setPlaceholderText("选择要叠上的视频")
    btn_vid = QPushButton("浏览…")
    vid_lay.addWidget(_path_row(ed_vid, btn_vid))

    # Q111: 连线导入 —— 左边媒体库、右边外部视频，拉线决定谁进谁的时间线。
    # 上面那一套（选一段视频叠到勾选的任务上）原样保留，这只是多一个入口。
    btn_link = QPushButton("连线导入…  把多条外部视频分别加到各自的时间线")
    btn_link.setObjectName("btnSecondary")
    btn_link.setToolTip(
        "左边是媒体库里的视频，右边是要加进来的外部片段，一行一组（预合成）。\n"
        "「每组片段数」按顺序分组，或按文件名前缀分组，之后随便拖着调。\n"
        "拉一条线 = 给左边那条视频新建一条视频轨道，把那一组按顺序拼进去。\n"
        "确定后只记住对应关系；点「插入到所选视频」才按这一页的循环 / 静音 / 分屏写进时间线。")
    vid_lay.addWidget(btn_link)
    dlg.btn_link_import = btn_link

    _busy_report(0.35, "文本页")
    # Q120：文本分页 —— 一行一条视频，表格里粘文案，按字幕时间自动定位时长
    from cutora.ui.batch_text_tab import build_text_tab
    tab_text = build_text_tab(dlg, parent, picker)
    tabs.addTab(tab_text, "文本")
    dlg.text_tab = tab_text
    _busy_report(0.45, "音频")
    from cutora.ui.i18n import tr as _tr_tab
    from cutora.ui.batch_audio_tab import build_audio_tab
    tab_audio = build_audio_tab(dlg, parent, picker)
    tabs.addTab(tab_audio, _tr_tab("音频"))
    dlg.audio_tab = tab_audio
    _busy_report(0.50, "语音转文本（模型列表）")
    # Q162：语音转文本 —— 音频 / 视频里的话转成文字直接导出（不往时间线上加东西）
    from cutora.ui.batch_asr_tab import build_asr_tab
    tab_asr = build_asr_tab(dlg, parent, picker)
    tabs.addTab(tab_asr, "语音转文本")
    dlg.asr_tab = tab_asr
    _busy_report(0.55, "片段散落")
    from cutora.batch.script_broll_scatter import build_dialog as build_scatter_tab
    tab_scatter = build_scatter_tab(parent, picker=picker)
    tabs.addTab(tab_scatter, "片段散落")
    dlg.scatter_tab = tab_scatter
    param_lay.addWidget(media_host, 1)             # Q191：表格 / 缩略图跟着窗口高度伸缩

    _busy_report(0.62, "时间 / 外观")
    # —— 时间 ——
    time_host, time_lay = _group("时间")
    time_form = _form()
    sp_start = QDoubleSpinBox()
    sp_end = QDoubleSpinBox()
    sp_head = QDoubleSpinBox()
    sp_tail = QDoubleSpinBox()
    cb_unit = QComboBox()
    cb_unit.addItem("帧", "frame")
    cb_unit.addItem("秒", "sec")
    cb_unit.addItem("分钟", "min")
    time_form.addRow("时间单位", _fixed(cb_unit, COMBO_W))
    time_form.addRow("起始帧", _fixed(sp_start, SPIN_W))
    time_form.addRow("结束帧", _fixed(sp_end, SPIN_W))
    time_form.addRow("开头留空", _fixed(sp_head, SPIN_W))
    time_form.addRow("结尾留空", _fixed(sp_tail, SPIN_W))
    hint_frames = QLabel(
        "0–20 帧 = 缩略图效果（约 0.67 秒 @30fps）。切单位只换显示，存储统一为秒。")
    hint_frames.setObjectName("hint")
    hint_frames.setWordWrap(True)
    time_form.addRow(hint_frames)
    ck_after_text = QCheckBox("图片接在标题后面，直到这条视频结束")
    ck_after_text.setToolTip(
        "文本分页里的标题按时间戳剪好之后，这张图从标题结束的那一帧开始，"
        "一直放到该视频的结尾。每条视频用自己的标题时间。\n"
        "还没识别过的，先到「文本」页点「识别开头时间戳」。"
        "已经手剪过的文本条也会被认出来。")
    time_form.addRow(ck_after_text)
    time_lay.addLayout(time_form)
    dlg.ck_after_text = ck_after_text

    def _sync_follow_time(on=None):
        follow = bool(ck_after_text.isChecked()) and tabs.currentIndex() == 0
        for sp in (sp_start, sp_end, sp_head, sp_tail, cb_unit):
            sp.setEnabled(not follow)

    ck_after_text.toggled.connect(_sync_follow_time)
    tabs.currentChanged.connect(lambda _i: _sync_follow_time())
    _follow = {"missed": [], "used": 0}

    def _reset_follow():
        _follow["missed"] = []
        _follow["used"] = 0

    def _image_follow_span(task, dur):
        """勾了「接在标题后面」时返回 (start, end)，否则 None，插入逻辑改用手填时间。"""
        if not ck_after_text.isChecked() or tabs.currentIndex() != 0:
            return None
        page = getattr(dlg, "text_tab", None)
        text, segs = "", []
        if page is not None:
            ask = getattr(page, "text_for_task", None)
            take = getattr(page, "segments_for_task", None)
            if callable(ask):
                text = ask(task) or ""
            if callable(take):
                segs = take(task) or []
        from cutora.batch.text_batch import follow_title_span
        got = follow_title_span(task, segs, text, dur)
        name = os.path.basename(
            str((task or {}).get("video") or (task or {}).get("path") or "视频"))
        if not got:
            if name and name not in _follow["missed"]:
                _follow["missed"].append(name)
            return None
        _follow["used"] += 1
        return float(got[0]), float(got[1])

    def _follow_note():
        if not ck_after_text.isChecked() or tabs.currentIndex() != 0:
            return ""
        lines = []
        if _follow["used"]:
            lines.append("图片已接在标题后面，直到各自视频结束。")
        if _follow["missed"]:
            lines.append(
                "这几条还没有标题时间戳，仍用上面手填的时间："
                + "、".join(_follow["missed"]))
        return ("\n" + "\n".join(lines)) if lines else ""

    param_lay.addWidget(time_host)
    _time_sec = {
        "start": 0.0,
        "end": 20.0 / 30.0,
        "head_pad": 0.0,
        "tail_pad": 0.0,
    }

    # —— 外观 ——
    look_host, look_lay = _group("外观")
    look_form = _form()
    cb_anim_in = QComboBox()
    for lab, val in phase_anim_items("in"):
        cb_anim_in.addItem(lab, val)
    idx_in = cb_anim_in.findData("fade")
    if idx_in >= 0:
        cb_anim_in.setCurrentIndex(idx_in)
    cb_anim_out = QComboBox()
    for lab, val in phase_anim_items("out"):
        cb_anim_out.addItem(lab, val)
    idx_out = cb_anim_out.findData("fade")
    if idx_out >= 0:
        cb_anim_out.setCurrentIndex(idx_out)
    look_form.addRow("入场动画", _fixed(cb_anim_in, COMBO_W))
    look_form.addRow("出场动画", _fixed(cb_anim_out, COMBO_W))
    cb_blend = QComboBox()
    for b in BLENDS:
        cb_blend.addItem(b, b)
    look_form.addRow("混合模式", _fixed(cb_blend, COMBO_W))
    cb_fit = QComboBox()
    cb_fit.addItem("铺满画面 fill", "fill")
    cb_fit.addItem("完整放下 fit", "fit")
    cb_fit.addItem("原始大小 source", "source")
    look_form.addRow("适配", _fixed(cb_fit, COMBO_W))
    sp_scale = QDoubleSpinBox()
    sp_scale.setRange(0.05, 5.0)
    sp_scale.setDecimals(2)
    sp_scale.setSingleStep(0.05)
    sp_scale.setValue(1.0)
    look_form.addRow("缩放", _fixed(sp_scale, SPIN_W))
    sp_x = QDoubleSpinBox()
    sp_x.setRange(-4000, 4000)
    sp_x.setDecimals(1)
    sp_x.setValue(0)
    sp_x.setSuffix(" px")
    sp_x.setFixedWidth(SPIN_W)
    sp_y = QDoubleSpinBox()
    sp_y.setRange(-4000, 4000)
    sp_y.setDecimals(1)
    sp_y.setValue(0)
    sp_y.setSuffix(" px")
    sp_y.setFixedWidth(SPIN_W)
    xy = QWidget()
    xy_row = QHBoxLayout(xy)
    xy_row.setContentsMargins(0, 0, 0, 0)
    xy_row.setSpacing(INSPECTOR_PAIR_GAP)
    xy_row.addWidget(QLabel("X"))
    xy_row.addWidget(sp_x)
    xy_row.addWidget(QLabel("Y"))
    xy_row.addWidget(sp_y)
    xy_row.addStretch(1)
    look_form.addRow("位置偏移", xy)

    # Q123：外观里那个「添加文本」单行输入框去掉了。它只能给一整批视频加
    # 同一句话，Q120 的「文本」分页是一行一条视频、还能按字幕自动定位，
    # 两个摆在一起只会让人不知道该用哪个。batch_load 里的 text/text_preset
    # 参数保留（配方和命令行还在用），只是对话框不再往里填。

    sp_track = QSpinBox()
    sp_track.setRange(1, 8)
    sp_track.setValue(1)
    sp_track.setPrefix("Images ")
    look_form.addRow("目标轨道", _fixed(sp_track, SPIN_W))
    ck_replace = QCheckBox("替换本脚本之前插入的同一张图")
    ck_replace.setChecked(True)
    look_form.addRow(ck_replace)
    look_lay.addLayout(look_form)
    param_lay.addWidget(look_host)

    _busy_report(0.72, "视频设置")
    # —— 视频（Q143：只在「视频」分页显示）——
    from cutora.batch.split_assign import (
        BORDER_LABELS, SPLIT_CHOICES, SPLIT_DEFAULT_LAYOUT, layout_cells, layout_label,
    )
    vprop_host, vprop_lay = _group("视频")
    vprop_form = _form()
    ck_vloop = QCheckBox("素材比时间段短时循环铺满")
    ck_vloop.setChecked(True)
    ck_vmute = QCheckBox("静音（不带素材原声）")
    ck_vmute.setChecked(True)
    vprop_form.addRow(ck_vloop)
    vprop_form.addRow(ck_vmute)
    ck_split = QCheckBox("同时开分屏")
    ck_split.setToolTip(
        "原视频和插入的视频各占一格。默认上下分屏；\n"
        "开完可以在右边预览里直接拖分界线，或者到「中央监视器」里逐条微调。")
    vprop_form.addRow(ck_split)
    cb_split = QComboBox()
    from cutora.batch.split_assign import LAYOUT_HINTS
    for lay in SPLIT_CHOICES:
        cb_split.addItem(layout_label(lay), lay)
        if lay in LAYOUT_HINTS:
            cb_split.setItemData(cb_split.count() - 1, LAYOUT_HINTS[lay],
                                 Qt.ItemDataRole.ToolTipRole)
    cb_split.setCurrentIndex(max(0, cb_split.findData(SPLIT_DEFAULT_LAYOUT)))
    vprop_form.addRow("分屏布局", _fixed(cb_split, COMBO_W))
    # Q151：左右完整的模糊背景（抖音那种两条并排、空处垫虚化）
    from cutora.render.split_layout import NEW_BACKDROP
    cb_backdrop = QComboBox()
    cb_backdrop.addItem("每格各自", "each")
    cb_backdrop.addItem("单个视频撑满整幅", "full")
    cb_backdrop.setToolTip(
        "分屏时留白处垫什么。\n每格各自：每一格垫自己那条视频的背景（左右分屏就是左右两块）；\n"
        "单个视频撑满整幅：整个画面只垫原视频的镜头模糊，各格留白处都露出它，不割裂。\n"
        "外部图片 / 外部视频背景在中央监视器右键「整幅背景」里批量设。")
    vprop_form.addRow("分屏背景", _fixed(cb_backdrop, COMBO_W))
    sp_backdrop_blur = QSpinBox()
    sp_backdrop_blur.setRange(0, 100)
    sp_backdrop_blur.setValue(int(NEW_BACKDROP["blur"]))
    sp_backdrop_blur.setToolTip("镜头模糊程度，0 = 不模糊。前半段很细，往后才越来越糊")
    vprop_form.addRow("模糊程度", _fixed(sp_backdrop_blur, SPIN_W))
    cb_cell_main = QComboBox()
    cb_cell_ins = QComboBox()
    vprop_form.addRow("原视频放在", _fixed(cb_cell_main, COMBO_W))
    vprop_form.addRow("插入视频放在", _fixed(cb_cell_ins, COMBO_W))
    cb_border = QComboBox()
    for key, lab in BORDER_LABELS.items():
        cb_border.addItem(lab, key)
    vprop_form.addRow("分界线", _fixed(cb_border, COMBO_W))
    sp_border_w = QSpinBox()
    sp_border_w.setRange(0, 80)
    sp_border_w.setValue(12)
    sp_border_w.setSuffix(" px")
    vprop_form.addRow("分界线粗细", _fixed(sp_border_w, SPIN_W))
    sp_feather = QSpinBox()
    sp_feather.setRange(0, 400)
    sp_feather.setValue(0)
    sp_feather.setSuffix(" px")
    sp_feather.setToolTip("两格交界处的柔和过渡宽度，0 = 硬边")
    vprop_form.addRow("交界羽化", _fixed(sp_feather, SPIN_W))
    vprop_lay.addLayout(vprop_form)
    param_lay.addWidget(vprop_host)
    split_rows = (cb_split, cb_cell_main, cb_cell_ins, cb_border, sp_border_w, sp_feather)
    backdrop_rows = (cb_backdrop, sp_backdrop_blur)

    def _refill_cells(_i=0):
        cells = layout_cells(cb_split.currentData() or SPLIT_DEFAULT_LAYOUT)
        for cb, want in ((cb_cell_main, 0), (cb_cell_ins, 1)):
            cur = cb.currentData()
            cb.blockSignals(True)
            cb.clear()
            for c in cells:
                cb.addItem("%s 屏" % c, c)
            idx = cb.findData(cur) if cur else -1
            cb.setCurrentIndex(idx if idx >= 0 else min(want, len(cells) - 1))
            cb.blockSignals(False)

    def _sync_split_enabled(_on=None):
        on = ck_split.isChecked()
        from cutora.render.split_layout import CONTAIN_LAYOUTS
        contain = on and (cb_split.currentData() in CONTAIN_LAYOUTS)
        blur_on = contain or (on and cb_backdrop.currentData() == "full")
        for w, en in ([(w, on) for w in split_rows] + [(cb_backdrop, on)]
                      + [(sp_backdrop_blur, blur_on)]):
            w.setEnabled(en)
            holder = w.parentWidget()          # _fixed() 包的那一层
            lab = vprop_form.labelForField(holder) if holder is not None else None
            if lab is not None:
                lab.setEnabled(en)

    _prefs = _load_split_prefs()
    _i = cb_split.findData(_prefs.get("layout"))
    if _i >= 0:
        cb_split.setCurrentIndex(_i)
    _bm = _prefs.get("backdrop_mode")
    _i = cb_backdrop.findData("full" if _bm == "first" else _bm)     # 老偏好 first = 整幅垫原视频
    if _i >= 0:
        cb_backdrop.setCurrentIndex(_i)
    try:
        if _prefs.get("backdrop_blur") is not None:
            sp_backdrop_blur.setValue(int(round(float(_prefs["backdrop_blur"]))))
    except (TypeError, ValueError):
        pass

    def _remember_split(*_a):
        _save_split_prefs({"layout": cb_split.currentData(),
                           "backdrop_mode": cb_backdrop.currentData(),
                           "backdrop_blur": int(sp_backdrop_blur.value())})

    cb_split.currentIndexChanged.connect(_refill_cells)
    cb_split.currentIndexChanged.connect(_sync_split_enabled)
    cb_split.currentIndexChanged.connect(_remember_split)
    cb_backdrop.currentIndexChanged.connect(_remember_split)
    cb_backdrop.currentIndexChanged.connect(_sync_split_enabled)
    sp_backdrop_blur.valueChanged.connect(_remember_split)
    ck_split.toggled.connect(_sync_split_enabled)
    _refill_cells()
    _sync_split_enabled()

    def split_params():
        if not ck_split.isChecked():
            return None
        return {
            "layout": cb_split.currentData() or SPLIT_DEFAULT_LAYOUT,
            "main_cell": cb_cell_main.currentData(),
            "insert_cell": cb_cell_ins.currentData(),
            "border": {"style": cb_border.currentData() or "none",
                       "width_px": int(sp_border_w.value())},
            "feather": float(sp_feather.value()),
            "backdrop": {"mode": cb_backdrop.currentData() or "each",
                         "blur": float(sp_backdrop_blur.value()),
                         "style": "lens"},
        }

    vprop_host.setVisible(False)
    dlg.vprop_host = vprop_host
    dlg.ck_split = ck_split
    dlg.cb_split = cb_split
    dlg.cb_cell_main = cb_cell_main
    dlg.cb_cell_ins = cb_cell_ins
    dlg.cb_border = cb_border
    dlg.sp_feather = sp_feather
    dlg.cb_backdrop = cb_backdrop
    dlg.sp_backdrop_blur = sp_backdrop_blur
    dlg.ck_vloop = ck_vloop
    dlg.ck_vmute = ck_vmute
    dlg.split_params = split_params

    # —— 多平台（默认收起）——
    multi = CollapsibleSection("多平台", initially_collapsed=True)
    ck_multi = QCheckBox("多平台副本（导出时按平台各出一份）")
    hint = QLabel(
        "勾选平台后各选一张图。时间线预览用第一个勾选平台的图。"
        "一次性渲染：字幕只烧一遍再叠图（图在字幕上面，更快）。"
        "多次单独渲染：每个平台整段单独渲染，字幕按时间线盖在图片上。"
    )
    hint.setObjectName("hint")
    hint.setWordWrap(True)
    plat_box = QWidget()
    plat_l = QVBoxLayout(plat_box)
    plat_l.setContentsMargins(0, 0, 0, 0)
    plat_l.setSpacing(INSPECTOR_ROW_GAP)
    plat_l.addWidget(ck_multi)
    plat_l.addWidget(hint)
    platform_rows = []

    def _picker_start(edit=None):
        cur = ""
        if edit is not None:
            cur = edit.text().strip()
        if not cur:
            cur = ed_img.text().strip()
        return file_dialog_start(
            cur,
            ed_dir.text().strip() or getattr(dlg, "_last_image_dir", ""),
        )

    def _remember_image_dir(path):
        folder = os.path.dirname(path) if path else ""
        if folder and os.path.isdir(folder):
            dlg._last_image_dir = folder
            if not ed_dir.text().strip():
                ed_dir.setText(folder)
                save_insert_prefs(default_dir=folder)
                _refresh_grid()

    def _browse_into(edit):
        path, _ = QFileDialog.getOpenFileName(
            dlg, "选择平台图片", _picker_start(edit),
            "Images (*.png *.jpg *.jpeg *.webp *.bmp);;All (*.*)")
        if path:
            edit.setText(path)
            _remember_image_dir(path)

    for pid, pname, pprefix in PLATFORMS:
        row_w = QWidget()
        h = QHBoxLayout(row_w)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(INSPECTOR_PAIR_GAP)
        ck = QCheckBox(pname)
        ck.setChecked(True)
        ck.setMinimumWidth(SPIN_W)
        ed = QLineEdit()
        ed.setPlaceholderText("%s 用图" % pname)
        ed.setMinimumWidth(0)
        btn = QPushButton("浏览…")
        h.addWidget(ck)
        h.addWidget(ed, 1)
        h.addWidget(btn)
        plat_l.addWidget(row_w)
        btn.clicked.connect(lambda _=False, e=ed: _browse_into(e))
        platform_rows.append({
            "id": pid, "name": pname, "prefix": pprefix,
            "ck": ck, "ed": ed, "btn": btn, "row": row_w,
        })
    multi.addWidget(plat_box)
    param_lay.addWidget(multi)
    dlg.multi_section = multi

    _busy_report(0.80, "导出设置")
    # —— 导出 ——
    export_host, export_lay = _group("导出")
    export_form = _form()
    cb_render = QComboBox()
    cb_render.addItem("一次性渲染（默认，更快）", RENDER_ONCE)
    cb_render.addItem("多次单独渲染（字幕盖住图）", RENDER_EACH)
    idx_rm = cb_render.findData(normalize_render_mode(prefs.get("render_mode")))
    if idx_rm >= 0:
        cb_render.setCurrentIndex(idx_rm)
    export_form.addRow("导出渲染", _fixed(cb_render, COMBO_W))
    export_lay.addLayout(export_form)
    param_lay.addWidget(export_host)

    forms = (media_form, time_form, look_form, export_form)

    def _lock_labels():
        labels = []
        for form in forms:
            for i in range(form.rowCount()):
                item = form.itemAt(i, QFormLayout.ItemRole.LabelRole)
                w = item.widget() if item is not None else None
                if isinstance(w, QLabel) and (w.text() or "").strip():
                    labels.append(w)
        if not labels:
            return
        width = max(lb.sizeHint().width() for lb in labels)
        for lb in labels:
            lb.setMinimumWidth(width)
        dlg.label_col_w = width

    _lock_labels()

    scroll = QScrollArea()
    scroll.setObjectName("batchParamColumn")
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    scroll.setWidget(param_body)
    scroll.viewport().setAutoFillBackground(False)
    param_body.setAutoFillBackground(False)
    param_card = _card(scroll, pad=SPACE_3)
    param_card.setMinimumWidth(SPACE_6 * 8)
    param_card.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
    dlg.param_column = param_card

    _busy_report(0.86, "预览画布")
    canvas = VideoCanvas(dlg)
    canvas.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
    canvas.setMinimumWidth(SPACE_6 * 8)
    dlg.preview_canvas = canvas
    canvas_card = _card(canvas, pad=0)
    canvas_card.setMinimumWidth(SPACE_6 * 8)

    columns.addWidget(video_col)
    columns.addWidget(param_card)
    columns.addWidget(canvas_card)
    columns.setStretchFactor(0, 0)
    columns.setStretchFactor(1, 0)
    columns.setStretchFactor(2, 1)
    _restore_columns(columns)
    root.addWidget(columns, 1)

    # Q191：运行日志平时收成底栏里的一行（最后一条消息），点「日志」才展开，不再常年占一大块
    log_box = QPlainTextEdit()
    log_box.setReadOnly(True)
    log_box.setPlaceholderText("运行日志…")
    log_box.setFixedHeight(SPACE_6 * 4)
    log_box.hide()
    root.addWidget(log_box, 0)
    row_btn = QHBoxLayout()
    row_btn.setSpacing(INSPECTOR_PAIR_GAP)
    btn_log = QPushButton("日志")
    btn_log.setObjectName("btn_ghost")
    btn_log.setCheckable(True)
    btn_log.setToolTip("展开 / 收起运行日志")
    btn_log.toggled.connect(log_box.setVisible)
    lbl_last = QLabel("")
    lbl_last.setObjectName("hint")
    lbl_last.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    dlg.log_box = log_box
    dlg.btn_log = btn_log
    dlg.lbl_last_log = lbl_last
    btn_run = QPushButton("插入到所选视频")
    btn_run.setObjectName("btn_primary")
    btn_close = QPushButton("完成并退出" if getattr(parent, "room_host", None) is not None else "关闭")
    btn_close.setToolTip("回到剪辑界面（已经加进去的内容都在时间线上）")
    btn_monitor = QPushButton("中央监视器…")
    btn_monitor.setObjectName("btnSecondary")
    btn_monitor.setToolTip(
        "一屏看全部勾选视频的批量结果，直接在画面上改：拖分界线、拖 / Ctrl+拖缩放每一格、"
        "把文字拖到别的视频、从素材库拖素材进去（没勾选就看全部）")
    row_btn.addWidget(btn_monitor)
    row_btn.addWidget(btn_log)
    row_btn.addWidget(lbl_last, 1)
    row_btn.addWidget(btn_run)
    row_btn.addWidget(btn_close)
    root.addLayout(row_btn)

    dlg.ed_dir = ed_dir
    dlg.ed_img = ed_img
    dlg.ed_vid = ed_vid
    dlg.tabs = tabs
    dlg.thumb_grid = thumb_grid
    dlg.vid_grid = vid_grid
    dlg.sp_x = sp_x
    dlg.sp_y = sp_y
    dlg.sp_scale = sp_scale
    dlg.cb_fit = cb_fit
    dlg.cb_blend = cb_blend
    dlg.btn_run = btn_run
    dlg.btn_monitor = btn_monitor
    dlg._preview_task = None
    dlg._preview_image_track = None
    dlg._media_wh = (0, 0)
    dlg._image_link_plan = []

    def _log(msg=""):
        log_box.appendPlainText(str(msg))
        bar = log_box.verticalScrollBar()
        bar.setValue(bar.maximum())
        last = str(msg or "").strip().splitlines()
        if last:
            lbl_last.setText(last[-1])
            lbl_last.setToolTip(last[-1])

    dlg.log = _log

    def _task_fps():
        for t in getattr(parent, "tasks", None) or []:
            try:
                fps = float((t or {}).get("fps") or 0)
            except Exception:
                _LOG.exception("读取任务 fps 失败")
                fps = 0
            if fps > 0:
                return fps
        return 30.0

    def _first_checked():
        tasks = picker.selected_tasks()
        if tasks:
            return tasks[0]
        return _current_task()

    def _current_task():
        t = picker.focused_task()
        if t is not None:
            return t
        tasks = list(getattr(parent, "tasks", None) or [])
        idx = int(getattr(parent, "curr_idx", -1) or -1)
        if 0 <= idx < len(tasks):
            return tasks[idx]
        return tasks[0] if tasks else None

    def _task_index(task):
        if task is None:
            return -1
        for i, item in enumerate(getattr(parent, "tasks", None) or []):
            if item is task:
                return i
        return -1

    def _preview_src_task():
        """有连线关系时，预览跟着当前点中的那条视频走。"""
        if tabs.currentIndex() == 0 and getattr(dlg, "_image_link_plan", None):
            return _current_task()
        return _first_checked()

    def _linked_paths_for(task):
        plan = getattr(dlg, "_image_link_plan", None) or []
        idx = _task_index(task)
        if idx < 0:
            return []
        for row in plan:
            try:
                row_idx = int(row.get("task_index", -1))
            except (TypeError, ValueError):
                continue
            if row_idx != idx:
                continue
            return [p for p in (row.get("paths") or []) if p and os.path.isfile(p)]
        return []

    def _preview_paths():
        if tabs.currentIndex() == 0:
            linked = _linked_paths_for(_preview_src_task())
            if linked:
                return linked
        path = _media_path()
        if path and os.path.isfile(path):
            return [path]
        return []

    def _canvas_size():
        forced = getattr(dlg, "_preview_size_task", None)
        if isinstance(forced, dict):
            return _canvas_wh(forced)
        return _canvas_wh(_preview_src_task() or _current_task())

    def _preview_time():
        return float(_time_sec["start"])

    def _ensure_task():
        cw, ch = _canvas_size()
        cw, ch = max(1, int(cw)), max(1, int(ch))
        fps = _task_fps()
        task = dlg._preview_task
        tl = task.get("timeline") if isinstance(task, dict) else None
        same = (
            tl is not None
            and int(getattr(tl, "width", 0) or 0) == cw
            and int(getattr(tl, "height", 0) or 0) == ch
        )
        if not same:
            from cutora.model.timeline import Timeline
            from cutora.model.timeline_model import Track
            tl = Timeline(cw, ch, fps)
            img = Track("image")
            tl.add_track(img)
            task = {
                "timeline": tl,
                "image_overlays": [],
                "text_overlays": [],
                "segments": [],
                "dims": (cw, ch),
                "fps": fps,
                "style": {},
                "duration": 1.0e6,
                "video": "",
            }
            dlg._preview_task = task
            dlg._preview_image_track = img
            canvas.set_data(task, _preview_time())
        canvas._video_dims = (cw, ch)
        canvas._current_time = _preview_time()
        return task, cw, ch

    def _ensure_text_track(task):
        tl = task.get("timeline") if isinstance(task, dict) else None
        if tl is None:
            return None
        for tr in getattr(tl, "tracks", None) or []:
            if getattr(tr, "type", "") == "text":
                dlg._preview_text_track = tr
                return tr
        from cutora.model.timeline_model import Track
        tr = Track("text")
        tl.add_track(tr)
        dlg._preview_text_track = tr
        return tr

    def show_text_preview():
        """右栏换成文本分页当前行：这一行的视频、plan 的起点、最多一条文本。

        画在 dlg._preview_task 上，不写进工程里的任何 task。
        """
        tab = getattr(dlg, "text_tab", None)
        if tab is None or not hasattr(tab, "current_preview"):
            return
        spec = tab.current_preview()
        if not spec:
            return
        src = spec.get("task") if isinstance(spec.get("task"), dict) else None
        dlg._preview_size_task = src
        try:
            task, cw, ch = _ensure_task()
        finally:
            dlg._preview_size_task = None
        path = str(spec.get("path") or "")
        try:
            t = float(spec.get("time") or 0.0)
        except (TypeError, ValueError):
            t = 0.0
        task["video"] = path
        try:
            if isinstance(src, dict) and _timeline_has_video(src):
                frame = task_background(src, t, cw, ch, _bg_fit(src))
            else:
                frame = fitted_background(path, t, cw, ch, _bg_fit(src))
        except Exception:
            _LOG.exception("文本预览取背景帧失败")
            frame = None
        if frame is None:
            canvas._program_frame = None
        else:
            canvas.set_program_frame(frame)
        task["image_overlays"] = []
        ov = spec.get("overlay")
        if isinstance(ov, dict) and str(ov.get("content") or "").strip():
            tr = _ensure_text_track(task)
            ov = dict(ov)
            ov["_track_id"] = getattr(tr, "id", None)
            start = float(ov.get("start") or 0.0)
            end = float(ov.get("end") if ov.get("end") is not None else start)
            if start > t:
                start = t
            if end < t:
                end = t
            ov["start"] = start
            ov["end"] = end
            task["text_overlays"] = [ov]
        else:
            task["text_overlays"] = []
        canvas.set_data(task, t)
        canvas._current_time = t
        canvas._video_dims = (cw, ch)
        canvas.update()

    dlg.show_text_preview = show_text_preview

    def _paint_background():
        tab = getattr(dlg, "text_tab", None)
        if (tabs.currentWidget() is tab and tab is not None
                and getattr(tab, "_preview_row", None) is not None):
            show_text_preview()
            return
        _task, cw, ch = _ensure_task()
        src_task = _preview_src_task()
        # Q134：拼接工程的 video 只是第一段切片；按时间线合成这一帧的视频层。
        frame = task_background(src_task, _preview_time(), cw, ch, _bg_fit(src_task))
        if frame is None:
            canvas._program_frame = None
        else:
            canvas.set_program_frame(frame)
        canvas._current_time = _preview_time()
        canvas._video_dims = (cw, ch)

    def _media_path():
        if tabs.currentIndex() == 1:
            return ed_vid.text().strip().strip('"')
        return ed_img.text().strip().strip('"')

    def _measure(path):
        ext = os.path.splitext(path)[1].lower()
        if ext in VIDEO_EXTS:
            iw, ih = probe_video_wh(path)
        else:
            iw, ih = image_size(path)
        if iw <= 0 or ih <= 0:
            cw, ch = canvas._video_dims
            return max(1, int(cw)), max(1, int(ch))
        return int(iw), int(ih)

    def _sync_overlay():
        tab = getattr(dlg, "text_tab", None)
        if tabs.currentWidget() is tab and tab is not None:
            task = dlg._preview_task
            if isinstance(task, dict):
                task["image_overlays"] = []
            if getattr(tab, "_preview_row", None) is not None:
                show_text_preview()
            else:
                canvas.update()
            return
        task, cw, ch = _ensure_task()
        paths = _preview_paths()
        if not paths:
            task["image_overlays"] = []
            dlg._media_wh = (0, 0)
            canvas.update()
            return
        path = paths[0]
        if (os.path.splitext(path)[1].lower() in VIDEO_EXTS and tabs.currentIndex() == 1
                and ck_split.isChecked()):
            # Q143：分屏预览 —— 在第一条勾选视频的副本上真的放片段、开分屏，
            # 用和画布同一套合成出这一帧；工程里的任务一个字节都不动
            frame = _split_preview_frame(path, cw, ch)
            task["image_overlays"] = []
            if frame is not None:
                canvas.set_program_frame(frame)
            canvas._current_time = _preview_time()
            canvas._video_dims = (cw, ch)
            canvas.update()
            return
        track = dlg._preview_image_track
        ovs = []
        for one in paths:
            if os.path.splitext(one)[1].lower() in VIDEO_EXTS:
                prime_video_still(one, _preview_time())
            iw, ih = _measure(one)
            if not ovs:
                dlg._media_wh = (iw, ih)
            ovs.append(make_overlay(
                one, 0.0, 1.0e6, cw, ch, iw, ih,
                cb_fit.currentData() or "fill",
                float(sp_scale.value()),
                float(sp_x.value()),
                float(sp_y.value()),
                getattr(track, "id", None),
                int(sp_track.value()),
                blend=cb_blend.currentData() or "normal",
                anim_in=cb_anim_in.currentData() or "fade",
                anim_out=cb_anim_out.currentData() or "fade",
            ))
        # Q158：入场 / 出场动画现在真的会画（transition_preset）。对话框里的预览停在开头那一帧，
        # 淡入时图片是透明的，摆位置就看不见了 —— 预览里不画转场，动画去时间线上看
        for ov in ovs:
            ov["transition_preset"] = "none"
        task["image_overlays"] = ovs
        canvas._current_time = _preview_time()
        canvas._video_dims = (cw, ch)
        canvas.update()

    def _split_preview_frame(path, cw, ch):
        import copy as _copy
        from cutora.batch.batch_preview import task_background
        from cutora.batch.script_insert_image import _place_video_clip
        from cutora.batch.split_assign import assign_split, layout_cells
        from cutora.batch.link_import import main_video_track
        src = _first_checked()
        if not isinstance(src, dict) or src.get("timeline") is None:
            return None
        try:
            probe = _copy.deepcopy(src)
            tl = probe.get("timeline")
            t = _preview_time()
            _place_video_clip(probe, tl, path, 0.0, max(t + 1.0, 30.0),
                              fit=cb_fit.currentData() or "fill",
                              scale=float(sp_scale.value()), x=float(sp_x.value()),
                              y=float(sp_y.value()), loop=True, mute=True,
                              blend=cb_blend.currentData() or "normal", replace=True)
            sp = split_params() or {}
            lay = sp.get("layout") or "top_bottom"
            cells = layout_cells(lay)
            order = [c for c in (sp.get("main_cell"), sp.get("insert_cell")) if c in cells]
            ins = [tr for tr in tl.tracks if getattr(tr, "name", "") == "批量视频"]
            assign_split(probe, lay, [main_video_track(tl), ins[-1] if ins else None][:len(order)],
                         border=sp.get("border"), feather_px=sp.get("feather"), cells=order,
                         backdrop=sp.get("backdrop"))
            return task_background(probe, t, cw, ch)
        except Exception:
            _LOG.exception("分屏预览失败")
            return None

    def _pull_text_style():
        """画布上拖出来的位置 / 大小 / 旋转写回这一批的样式，不写进任何视频。"""
        tab = getattr(dlg, "text_tab", None)
        style = getattr(tab, "style", None) if tab is not None else None
        if not isinstance(style, dict):
            return
        task = dlg._preview_task or {}
        ovs = task.get("text_overlays") or []
        if not ovs or not isinstance(ovs[0], dict):
            return
        ov = ovs[0]
        for key in ("x", "y", "w", "h", "rotation"):
            if ov.get(key) is None:
                continue
            try:
                style[key] = float(ov[key])
            except (TypeError, ValueError):
                continue

    def _pull_from_canvas():
        tab = getattr(dlg, "text_tab", None)
        if tabs.currentWidget() is tab and tab is not None:
            _pull_text_style()
            return
        task = dlg._preview_task or {}
        ovs = task.get("image_overlays") or []
        if not ovs:
            return
        els = ovs[0].get("elements") or []
        if not els or not isinstance(els[0], dict):
            return
        el = els[0]
        iw, ih = dlg._media_wh
        if iw <= 0 or ih <= 0:
            iw, ih = _measure(el.get("path") or _media_path())
        cw, ch = canvas._video_dims
        ox, oy, scale = placed_offset(
            el.get("x"), el.get("y"), el.get("w"), el.get("h"),
            iw, ih, cw, ch, cb_fit.currentData() or "fill",
        )
        for sp, val in ((sp_x, ox), (sp_y, oy), (sp_scale, scale)):
            sp.blockSignals(True)
            sp.setValue(float(val))
            sp.blockSignals(False)

    def _refresh_grid():
        folder = ed_dir.text().strip()
        thumb_grid.set_folder(folder)
        vid_grid.set_folder(folder)

    def _browse():
        start = ed_dir.text().strip() or _picker_start(ed_img)
        path, _ = QFileDialog.getOpenFileName(
            dlg, "查找其他位置", start,
            "Images (*.png *.jpg *.jpeg *.webp *.bmp);;All (*.*)")
        if path:
            ed_img.setText(path)
            _remember_image_dir(path)

    def _browse_dir():
        start = file_dialog_start(ed_dir.text().strip(), SUGGESTED_IMAGE_DIR)
        d = QFileDialog.getExistingDirectory(dlg, "选择默认找图目录", start)
        if d:
            ed_dir.setText(d)
            save_insert_prefs(default_dir=d)
            _refresh_grid()

    def _browse_vid():
        path, _ = QFileDialog.getOpenFileName(
            dlg, "选择视频", _picker_start(ed_vid),
            "Videos (*.mp4 *.mov *.mkv *.webm *.avi *.m4v);;All (*.*)")
        if path:
            tabs.setCurrentIndex(1)
            ed_vid.setText(path)
            _remember_image_dir(path)

    def _apply_unit_format():
        unit = cb_unit.currentData() or "frame"
        if unit == "frame":
            dec, suffix, lo, hi, step = 0, " 帧", 0, 999999, 1
        elif unit == "min":
            dec, suffix, lo, hi, step = 4, " 分", 0, 9999, 0.01
        else:
            dec, suffix, lo, hi, step = 3, " 秒", 0, 99999, 0.01
        for sp in (sp_start, sp_end, sp_head, sp_tail):
            sp.blockSignals(True)
            sp.setDecimals(dec)
            sp.setSuffix(suffix)
            sp.setRange(lo, hi)
            sp.setSingleStep(step)
            sp.blockSignals(False)

    def _show_times():
        unit = cb_unit.currentData() or "frame"
        fps = _task_fps()
        _apply_unit_format()
        mapping = (
            ("start", sp_start), ("end", sp_end),
            ("head_pad", sp_head), ("tail_pad", sp_tail),
        )
        for key, sp in mapping:
            val = display_from_sec(_time_sec[key], unit, fps)
            sp.blockSignals(True)
            sp.setValue(round(val) if unit == "frame" else val)
            sp.blockSignals(False)

    def _spin_to_sec(key, sp):
        unit = cb_unit.currentData() or "frame"
        _time_sec[key] = max(0.0, sec_from_display(sp.value(), unit, _task_fps()))
        if key == "start":
            _paint_background()

    def _set_multi_enabled(on):
        plat_box.setEnabled(bool(on))
        hint.setEnabled(bool(on))
        cb_render.setEnabled(bool(on))
        thumb_grid.setEnabled(not bool(on))
        ed_img.setEnabled(not bool(on))
        btn_img.setEnabled(not bool(on))
        btn_img_link.setEnabled(not bool(on))
        if on:
            src = ed_img.text().strip().strip('"')
            is_default = False
            try:
                is_default = bool(src) and os.path.normcase(os.path.abspath(src)) == \
                    os.path.normcase(os.path.abspath(DEFAULT_IMAGE))
            except Exception:
                _LOG.exception("判断默认图失败")
                is_default = False
            if src and os.path.isfile(src) and not is_default:
                for pr in platform_rows:
                    if not pr["ed"].text().strip():
                        pr["ed"].setText(src)

    def _restore_from_tasks():
        cfg = None
        img = None
        for t in getattr(parent, "tasks", None) or []:
            c = (t or {}).get("platform_copies")
            if isinstance(c, dict) and c.get("enabled") and c.get("platforms"):
                cfg = c
                break
        if cfg is None:
            return
        multi.set_collapsed(False)
        ck_multi.setChecked(True)
        by_id = {p.get("id"): p for p in (cfg.get("platforms") or []) if isinstance(p, dict)}
        for pr in platform_rows:
            p = by_id.get(pr["id"])
            if p and p.get("image"):
                pr["ck"].setChecked(True)
                pr["ed"].setText(p.get("image") or "")
                if img is None:
                    img = p.get("image")
            else:
                pr["ck"].setChecked(False)
        if img:
            ed_img.setText(img)
        try:
            _time_sec["start"] = float(cfg.get("start_sec") or 0)
            abs_end = cfg.get("end_sec")
            if abs_end not in (None, ""):
                _time_sec["end"] = float(abs_end)
            _time_sec["tail_pad"] = float(cfg.get("end_pad_sec") or 0)
            _time_sec["head_pad"] = float(cfg.get("head_pad_sec") or 0)
            _show_times()
            sp_track.setValue(int(cfg.get("image_track") or 1))
            fit = cfg.get("fit") or "fill"
            if fit == "stretch":
                fit = "fill"
            idx = cb_fit.findData(fit)
            if idx >= 0:
                cb_fit.setCurrentIndex(idx)
            sp_scale.setValue(float(cfg.get("scale") or 1.0))
            sp_x.setValue(float(cfg.get("x") or 0))
            sp_y.setValue(float(cfg.get("y") or 0))
            idx_rm = cb_render.findData(normalize_render_mode(cfg.get("render_mode")))
            if idx_rm >= 0:
                cb_render.setCurrentIndex(idx_rm)
        except Exception:
            _LOG.exception("从任务恢复批量加载参数失败")

    def _collect_platforms():
        rows = []
        for pr in platform_rows:
            if not pr["ck"].isChecked():
                continue
            path = pr["ed"].text().strip().strip('"')
            if not path or not os.path.isfile(path):
                raise ValueError("请给 %s 选择一张有效图片。" % pr["name"])
            ext = os.path.splitext(path)[1].lower()
            if ext not in IMAGE_EXTS:
                raise ValueError("%s 的图片格式不支持：%s" % (pr["name"], ext))
            rows.append({
                "id": pr["id"],
                "name": pr["name"],
                "prefix": pr["prefix"],
                "image": os.path.abspath(path),
            })
        if not rows:
            raise ValueError("多平台副本已开启，请至少勾选一个平台并选择图片。")
        return rows

    def _run_text_tab():
        """文本分页的「插入到所选视频」。"""
        if not picker.selected_tasks():
            QMessageBox.warning(dlg, "没有选中视频", "请至少勾选一条要加载的视频。")
            return
        btn_run.setEnabled(False)
        try:
            stats = dlg.text_tab.apply()
        except Exception as exc:
            _LOG.exception("批量加文本失败")
            QMessageBox.warning(dlg, "批量加文本", str(exc) or "出错了")
            return
        finally:
            btn_run.setEnabled(True)
        if not stats.get("overlays"):
            QMessageBox.information(
                dlg, "批量加文本",
                "没有可加的内容。表格里的「文本内容」那一列是空的吗？")
            return
        _log("==== 批量加文本 ====")
        _log("  %d 条视频，共 %d 条文本" % (stats.get("tasks", 0),
                                          stats.get("overlays", 0)))
        try:
            if hasattr(parent, "timeline"):
                parent.timeline.update()
            if hasattr(parent, "video_canvas"):
                parent.video_canvas.update()
            if hasattr(parent, "refresh_ui"):
                parent.refresh_ui()
        except Exception:
            _LOG.exception("加完文本刷新界面失败")
        QMessageBox.information(
            dlg, "批量加文本",
            "已给 %d 条视频加上 %d 条文本。"
            % (stats.get("tasks", 0), stats.get("overlays", 0)))

    def _commit_linked_images(plan) -> bool:
        """节点里记住的对应关系，按主窗口当前的大小、位置、时间写进时间线。

        勾选的视频里一张都没连上时返回 False，交给原来的单图插入。
        """
        checked = [int(i) for i in picker.selected_indices()]
        if not checked:
            QMessageBox.warning(dlg, "没有选中视频", "请至少勾选一条要加载的视频。")
            return True
        tasks = list(getattr(parent, "tasks", None) or [])
        rows = []
        for row in plan or []:
            try:
                idx = int(row.get("task_index", -1))
            except (TypeError, ValueError):
                continue
            if idx not in checked or not (0 <= idx < len(tasks)):
                continue
            paths = [p for p in (row.get("paths") or []) if p and os.path.isfile(p)]
            if paths:
                rows.append({"task_index": idx, "paths": paths})
        if not rows:
            return False
        fps = _task_fps()
        vis_start = _time_sec["start"] + _time_sec["head_pad"]
        vis_end = max(vis_start, _time_sec["end"] - _time_sec["tail_pad"])
        if vis_end < vis_start:
            vis_start, vis_end = vis_end, vis_start
        _reset_follow()
        btn_run.setEnabled(False)
        try:
            from cutora.batch import link_image as link_biz
            from cutora.batch.batch_load import apply_to_task
            from cutora.batch.script_insert_image import task_duration
            _log("==== 按节点连线加载 ====")

            def _note(task, path):
                try:
                    dur = float(task_duration(task, parent) or 0)
                except Exception:
                    dur = 0.0
                span = _image_follow_span(task, dur) if dur > 0 else None
                apply_to_task(task, {
                    "tab": "image",
                    "start_sec": float(span[0] if span else _time_sec["start"]),
                    "end_sec": float(span[1] if span else _time_sec["end"]),
                    "head_pad_sec": float(_time_sec["head_pad"]),
                    "tail_pad_sec": float(_time_sec["tail_pad"]),
                    "anim_in": cb_anim_in.currentData() or "fade",
                    "anim_out": cb_anim_out.currentData() or "fade",
                    "blend": cb_blend.currentData() or "normal",
                    "fit_mode": cb_fit.currentData() or "fill",
                    "image": path,
                    "x": float(sp_x.value()),
                    "y": float(sp_y.value()),
                    "scale": float(sp_scale.value()),
                }, fps=fps)

            stats = link_biz.apply_plan(
                rows, tasks,
                replace=bool(ck_replace.isChecked()),
                host=parent,
                log=_log,
                anim_in=cb_anim_in.currentData() or "fade",
                anim_out=cb_anim_out.currentData() or "fade",
                blend=cb_blend.currentData() or "normal",
                on_image=_note,
                start_sec=vis_start,
                end_sec=vis_end,
                end_pad_sec=float(_time_sec["tail_pad"]),
                span_of=_image_follow_span,
                image_track=int(sp_track.value()),
                fit=cb_fit.currentData() or "fill",
                scale=float(sp_scale.value()),
                x=float(sp_x.value()),
                y=float(sp_y.value()),
            )
            if hasattr(parent, "refresh_ui") and getattr(parent, "curr_idx", -1) >= 0:
                parent.refresh_ui()
            if hasattr(parent, "video_canvas"):
                parent.video_canvas.update()
            if hasattr(parent, "timeline"):
                parent.timeline.update()
            skipped = len(checked) - len(rows)
            extra = ""
            if skipped > 0:
                extra = "\n另外 %d 条勾选的视频没有连上图，这次没加。" % skipped
            QMessageBox.information(
                dlg, "批量加载完成",
                "已按节点连线，用当前的大小、位置和时间，加载到 %d 条视频。%s%s"
                % (stats.get("tasks", 0), extra, _follow_note()))
        except Exception as e:
            _log("✘ %s" % e)
            QMessageBox.warning(dlg, "失败", str(e))
        finally:
            btn_run.setEnabled(True)
        return True

    def _run():
        if tabs.currentWidget() is dlg.text_tab:
            _run_text_tab()
            return
        if tabs.currentWidget() is getattr(dlg, "audio_tab", None):
            tab = dlg.audio_tab
            fn = getattr(tab, "assign_bgm", None)
            if callable(fn):
                fn()
            return
        if tabs.currentWidget() is getattr(dlg, "asr_tab", None):
            dlg.asr_tab.start()
            return
        if tabs.currentWidget() is getattr(dlg, "scatter_tab", None):
            fn = getattr(dlg.scatter_tab, "run", None)
            if callable(fn):
                fn()
            return
        if tabs.currentIndex() == 0 and not ck_multi.isChecked():
            plan = getattr(dlg, "_image_link_plan", None) or []
            if plan and _commit_linked_images(plan):
                return
        if tabs.currentIndex() == 1:
            vplan = getattr(dlg, "_video_link_plan", None)
            if vplan and _commit_linked_videos(vplan):
                return
        copies = None
        preview_name = ""


        if ck_multi.isChecked():
            try:
                copies = {
                    "enabled": True,
                    "platforms": _collect_platforms(),
                    "render_mode": cb_render.currentData() or RENDER_ONCE,
                }
            except ValueError as e:
                QMessageBox.warning(dlg, "多平台副本", str(e))
                return
            path = copies["platforms"][0]["image"]
            preview_name = copies["platforms"][0]["name"]
            ed_img.setText(path)
        else:
            path = _media_path()
        if not path or not os.path.isfile(path):
            QMessageBox.warning(dlg, "素材无效", "请先选择一张图片或一段视频。")
            return
        tasks = picker.selected_tasks()
        if not tasks:
            QMessageBox.warning(dlg, "没有选中视频", "请至少勾选一条要加载的视频。")
            return
        fps = _task_fps()
        vis_start = _time_sec["start"] + _time_sec["head_pad"]
        vis_end = max(vis_start, _time_sec["end"] - _time_sec["tail_pad"])
        if vis_end < vis_start:
            vis_start, vis_end = vis_end, vis_start
        start_f = int(round(vis_start * fps))
        end_f = int(round(vis_end * fps))
        start = vis_start
        abs_end = vis_end
        _reset_follow()
        btn_run.setEnabled(False)
        try:
            _log("==== 开始批量加载 ====")
            from cutora.batch.batch_load import apply_to_task
            stats = insert_into_tasks(
                tasks,
                path,
                start_sec=start,
                end_pad_sec=_time_sec["tail_pad"],
                end_sec=abs_end,
                image_track=int(sp_track.value()),
                fit=cb_fit.currentData() or "fill",
                scale=float(sp_scale.value()),
                x=float(sp_x.value()),
                y=float(sp_y.value()),
                replace=bool(ck_replace.isChecked()),
                host=parent,
                log=_log,
                platform_copies=copies,
                video_loop=bool(ck_vloop.isChecked()),
                video_mute=bool(ck_vmute.isChecked()),
                blend=cb_blend.currentData() or "normal",
                split=split_params() if tabs.currentIndex() == 1 else None,
                span_of=_image_follow_span if tabs.currentIndex() == 0 else None,
            )
            for t in tasks:
                apply_to_task(t, {
                    "tab": "video" if tabs.currentIndex() == 1 else "image",
                    "start_sec": _time_sec["start"],
                    "end_sec": _time_sec["end"],
                    "head_pad_sec": _time_sec["head_pad"],
                    "tail_pad_sec": _time_sec["tail_pad"],
                    "start_frame": start_f, "end_frame": end_f,
                    "anim_in": cb_anim_in.currentData() or "fade",
                    "anim_out": cb_anim_out.currentData() or "fade",
                    "blend": cb_blend.currentData() or "normal",
                    "fit_mode": cb_fit.currentData() or "fill",
                    "image": path if tabs.currentIndex() == 0 else "",
                    "video": path if tabs.currentIndex() == 1 else "",
                    "x": float(sp_x.value()), "y": float(sp_y.value()),
                    "scale": float(sp_scale.value()),
                    "platform_copies": copies or {},
                }, fps=fps)
                ovs = t.get("image_overlays") or []
                for ov in ovs:
                    if is_insert_overlay(ov):
                        ov["blend_mode"] = cb_blend.currentData() or "normal"
                        from cutora.batch.script_insert_image import stamp_transition
                        stamp_transition(ov, cb_anim_in.currentData() or "fade",
                                         cb_anim_out.currentData() or "fade")
                        for el in ov.get("elements") or []:
                            if isinstance(el, dict):
                                el["blend_mode"] = ov["blend_mode"]
            if hasattr(parent, "refresh_ui") and getattr(parent, "curr_idx", -1) >= 0:
                parent.refresh_ui()
            if hasattr(parent, "video_canvas"):
                parent.video_canvas.update()
            if hasattr(parent, "timeline"):
                parent.timeline.update()
            _log("完成：%d 条视频已插入。" % stats["placed"])
            extra = ""
            cfg = stats.get("platform_copies")
            if cfg:
                names = "、".join(p["prefix"] for p in cfg["platforms"])
                if normalize_render_mode(cfg.get("render_mode")) == "each":
                    how = "点「批量导出」时每个平台整段单独渲染，字幕会盖在图片上。"
                else:
                    how = "点「批量导出」时字幕只烧一遍，再按平台叠图（图在字幕上面）。"
                extra = (
                    "\n\n已开启多平台副本：%s"
                    "\n时间线预览用 %s 的图：%s"
                    "\n%s"
                ) % (names, preview_name or cfg["platforms"][0]["name"],
                     os.path.basename(stats.get("image") or path), how)
            save_insert_prefs(
                default_dir=ed_dir.text().strip(),
                render_mode=cb_render.currentData() or RENDER_ONCE,
            )
            QMessageBox.information(
                dlg, "批量加载完成",
                "已加载到 %d 条视频的 Images %d。\n第 %d–%d 帧（%.2f–%.2f 秒）。%s%s"
                % (stats["placed"], int(sp_track.value()), start_f, end_f,
                   start, abs_end, extra, _follow_note()))
        except Exception as e:
            _log("✘ %s" % e)
            QMessageBox.warning(dlg, "失败", str(e))
        finally:
            btn_run.setEnabled(True)

    def _on_thumb(path):
        tabs.setCurrentIndex(0)
        ed_img.setText(path)

    def _on_vid_thumb(path):
        tabs.setCurrentIndex(1)
        ed_vid.setText(path)

    def _on_picker(*_a):
        _paint_background()
        _sync_overlay()

    btn_img.clicked.connect(_browse)
    def remember_video_links(plan):
        """Q154：只记住视频连线关系（分组 + 对应），勾上这些视频。不写时间线，点「插入到所选视频」才写。"""
        dlg._video_link_plan = plan if isinstance(plan, dict) and plan.get("plan") else None
        want = set()
        for row in (dlg._video_link_plan or {}).get("plan") or []:
            try:
                want.add(int(row.get("task_index", -1)))
            except (TypeError, ValueError):
                continue
        want.discard(-1)
        if want:
            picker.list.blockSignals(True)
            first = None
            for i in range(picker.list.count()):
                it = picker.list.item(i)
                idx = int(it.data(Qt.ItemDataRole.UserRole))
                on = idx in want
                it.setData(Qt.ItemDataRole.CheckStateRole,
                           Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)
                if on and first is None:
                    first = i
            if first is not None:
                picker._last_row = first
            picker._refresh_count()
            picker.list.blockSignals(False)
            n_links = len(dlg._video_link_plan.get("links") or [])
            n_groups = sum(1 for r in dlg._video_link_plan.get("rows") or [] if len(r) > 1)
            btn_link.setText("连线导入…  已记住 %d 条连线 → %d 条视频%s" % (
                n_links, len(want), ("，%d 个预合成组" % n_groups) if n_groups else ""))
        else:
            btn_link.setText("连线导入…  把多条外部视频分别加到各自的时间线")

    dlg.remember_video_links = remember_video_links

    def _open_link_import():
        from cutora.ui.link_import_dialog import LinkImportDialog
        tasks = list(getattr(parent, "tasks", None) or [])
        if not tasks:
            QMessageBox.warning(
                dlg, "媒体库是空的",
                "先把要被加内容的视频导入进来，再用连线导入。")
            return
        sub = LinkImportDialog(tasks, dlg, host=parent, plan=getattr(dlg, "_video_link_plan", None))
        if sub.exec() != QDialog.DialogCode.Accepted:
            return
        remember_video_links(getattr(sub, "link_plan", None))

    def _commit_linked_videos(plan) -> bool:
        """Q154：按记住的连线把外部片段写进时间线（多段的组 = 预合成）。用这一页当前的循环 / 静音 / 分屏设置。"""
        from cutora.batch import link_import as biz
        tasks = list(getattr(parent, "tasks", None) or [])
        rows = plan.get("plan") or []
        if not rows or not tasks:
            return False
        sp = split_params()
        btn_run.setEnabled(False)
        try:
            _log("==== 按连线导入外部视频 ====")
            stats = biz.apply_plan(
                rows, tasks, host=parent,
                loop=bool(ck_vloop.isChecked()), match_duration=True,
                mute=bool(ck_vmute.isChecked()), log=_log,
                split_layout=(sp or {}).get("layout") if sp else None,
                split_border=(sp or {}).get("border") if sp else None,
                split_feather=(sp or {}).get("feather") if sp else None,
                split_backdrop=(sp or {}).get("backdrop") if sp else None,
                split_cells=[c for c in ((sp or {}).get("main_cell"), (sp or {}).get("insert_cell")) if c] if sp else None)
        except Exception as e:
            _log("✘ %s" % e)
            QMessageBox.warning(dlg, "导入失败", str(e))
            return True
        finally:
            btn_run.setEnabled(True)
        if hasattr(parent, "refresh_ui") and getattr(parent, "curr_idx", -1) >= 0:
            parent.refresh_ui()
        if hasattr(parent, "timeline"):
            parent.timeline.update()
        if hasattr(parent, "video_canvas"):
            parent.video_canvas.update()
        preview = getattr(dlg, "preview_canvas", None)
        if preview is not None and hasattr(preview, "update"):
            preview.update()
        msg = "给 %d 条视频新建了 %d 条轨道，放入 %d 个片段" % (stats["tasks"], stats["tracks"], stats["clips"])
        if stats.get("groups"):
            msg += "，其中 %d 条轨道是预合成组（时间线上一组一个颜色，点一条选整组）" % stats["groups"]
        msg += "。"
        if sp:
            msg += "\n分屏已开：原视频在第一格，连进来的依次进后面的格子；一条视频连了两组的自动用「上 1 下 2」。"
            if stats.get("split_skipped"):
                msg += "\n有 %d 条轨道没分到格子 —— 格子不够，换一个格子多的布局。" % stats["split_skipped"]
        _log("完成：%s" % msg.replace("\n", " "))
        QMessageBox.information(dlg, "导入完成", msg)
        remember_video_links(None)
        return True

    btn_link.clicked.connect(_open_link_import)

    def remember_image_links(plan):
        """记住哪张图对应哪条视频，勾上这些视频，并按当前的大小位置预览。不写时间线。"""
        dlg._image_link_plan = [dict(row) for row in (plan or []) if isinstance(row, dict)]
        want = set()
        for row in dlg._image_link_plan:
            try:
                want.add(int(row.get("task_index", -1)))
            except (TypeError, ValueError):
                continue
        want.discard(-1)
        if want:
            picker.list.blockSignals(True)
            first = None
            for i in range(picker.list.count()):
                it = picker.list.item(i)
                idx = int(it.data(Qt.ItemDataRole.UserRole))
                on = idx in want
                it.setData(
                    Qt.ItemDataRole.CheckStateRole,
                    Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)
                if on and first is None:
                    first = i
            if first is not None:
                picker._last_row = first
            picker._refresh_count()
            picker.list.blockSignals(False)
            dlg.btn_link_image.setText("节点连线…  已记住 %d 条视频" % len(want))
        else:
            dlg.btn_link_image.setText("节点连线…  一张图指定给多条视频")
        refresh_image_preview()

    def refresh_image_preview():
        _paint_background()
        _sync_overlay()

    dlg.remember_image_links = remember_image_links
    dlg.refresh_image_preview = refresh_image_preview

    def _open_link_image():
        from cutora.ui.link_image_dialog import LinkImageDialog
        if ck_multi.isChecked():
            QMessageBox.information(
                dlg, "先关掉多平台副本",
                "多平台副本是按平台各出一张图。要按连线把一张图指定给多条视频，先取消勾选多平台。")
            return
        tasks = list(getattr(parent, "tasks", None) or [])
        if not tasks:
            QMessageBox.warning(
                dlg, "媒体库是空的",
                "先把要被加图片的视频导入进来，再用节点连线。")
            return
        extra = ed_img.text().strip().strip('"')
        sub = LinkImageDialog(
            tasks, dlg, host=parent,
            folder=ed_dir.text().strip(),
            extras=[extra] if extra else None,
            checked=picker.selected_indices(),
            spec=_image_link_spec,
            plan=getattr(dlg, "_image_link_plan", None),
        )
        if sub.exec() != QDialog.DialogCode.Accepted:
            return
        remember_image_links(getattr(sub, "link_plan", None))

    def _image_link_spec():
        fps = _task_fps()
        vis_start = _time_sec["start"] + _time_sec["head_pad"]
        vis_end = max(vis_start, _time_sec["end"] - _time_sec["tail_pad"])
        return {
            "start_sec": vis_start,
            "end_sec": vis_end,
            "end_pad_sec": float(_time_sec["tail_pad"]),
            "image_track": int(sp_track.value()),
            "fit": cb_fit.currentData() or "fill",
            "scale": float(sp_scale.value()),
            "x": float(sp_x.value()),
            "y": float(sp_y.value()),
            "replace": bool(ck_replace.isChecked()),
            "blend": cb_blend.currentData() or "normal",
            "anim_in": cb_anim_in.currentData() or "fade",
            "anim_out": cb_anim_out.currentData() or "fade",
            "fps": fps,
            "recipe_start": float(_time_sec["start"]),
            "recipe_end": float(_time_sec["end"]),
            "head_pad_sec": float(_time_sec["head_pad"]),
            "tail_pad_sec": float(_time_sec["tail_pad"]),
        }

    btn_img_link.clicked.connect(_open_link_image)

    def _on_tab_changed(_i=0):
        # 文本分页的行跟着左边的勾选走，切过来时重建一次
        on_text = tabs.currentWidget() is dlg.text_tab
        on_asr = tabs.currentWidget() is getattr(dlg, "asr_tab", None)
        on_audio = tabs.currentWidget() is getattr(dlg, "audio_tab", None)
        on_scatter = tabs.currentWidget() is getattr(dlg, "scatter_tab", None)
        recipe = on_text or on_asr or on_audio or on_scatter
        audio_tab = getattr(dlg, "audio_tab", None)
        if audio_tab is not None and not on_audio:
            stop = getattr(audio_tab, "stop_audition", None)
            if callable(stop):
                stop()
        if on_audio:
            sync = getattr(audio_tab, "sync_from_host", None)
            if callable(sync):
                sync()
        if not on_text:
            # 预览用的那条文本只活在对话框自己的 task 上，离开这一页就清掉
            task = dlg._preview_task
            if isinstance(task, dict):
                task["text_overlays"] = []
        if on_text:
            dlg.text_tab.refresh()
        # Q191：「默认找图目录」只跟图片 / 视频分页有关，文本 / 语音转文本分页把它和「素材」标题收起来
        dir_row.setVisible(not recipe)
        if media_title is not None:
            media_title.setVisible(not recipe)
        if on_text or on_asr or on_scatter:
            # 文本分页是张表，比图片那几个控件宽得多：中间这栏拿到窗口的六成左右（预览留够看清的宽度）。
            # 只在切过来时调一次，之后用户拖成什么样算什么样
            sizes = list(columns.sizes())
            total = sum(sizes)
            if len(sizes) == 3 and total > 0:
                want = max(TEXT_TAB_MIN_W, int((total - sizes[0]) * 0.62))
                if sizes[1] < want:
                    take = min(want - sizes[1], max(0, sizes[2] - SPACE_6 * 10))
                    if take > 0:
                        sizes[1] += take
                        sizes[2] -= take
                        columns.setSizes(sizes)
        if on_text:
            btn_run.setText("按表格加文本")
        elif on_asr:
            btn_run.setText("开始转写")
        elif on_audio:
            btn_run.setText("给勾选的视频配乐")
        elif on_scatter:
            btn_run.setText("切开并撒播到所选视频")
        else:
            btn_run.setText("插入到所选视频")
        # Q123：「时间」和「外观」整组是给图片/视频叠加用的（目标轨道那一栏
        # 写的是 Images N）。文本分页自己有模板和时长，这两组跟它没关系，
        # 留在那儿只会让人以为文本会落到图片轨上。
        # 多平台是给图片出多版用的，文本更用不上。语音转文本也一样。
        for host in (time_host, look_host, export_host, multi):
            host.setVisible(not recipe)
        vprop_host.setVisible(tabs.currentIndex() == 1)
        if not on_text:
            _paint_background()

    tabs.currentChanged.connect(_on_tab_changed)
    dlg.text_tab.refresh()
    btn_vid.clicked.connect(_browse_vid)
    btn_dir.clicked.connect(_browse_dir)
    ed_dir.editingFinished.connect(_refresh_grid)
    thumb_grid.image_chosen.connect(_on_thumb)
    vid_grid.image_chosen.connect(_on_vid_thumb)
    ed_img.textChanged.connect(lambda _t="": _sync_overlay())
    ed_vid.textChanged.connect(lambda _t="": _sync_overlay())

    def _split_changed(*_a):
        _paint_background()
        _sync_overlay()

    for _w, _sig in ((ck_split, "toggled"), (cb_split, "currentIndexChanged"),
                     (cb_cell_main, "currentIndexChanged"), (cb_cell_ins, "currentIndexChanged"),
                     (cb_border, "currentIndexChanged"), (sp_border_w, "valueChanged"),
                     (sp_feather, "valueChanged"), (cb_backdrop, "currentIndexChanged"),
                     (sp_backdrop_blur, "valueChanged")):
        getattr(_w, _sig).connect(_split_changed)
    tabs.currentChanged.connect(lambda _i=0: _sync_overlay())
    sp_x.valueChanged.connect(lambda _v=0: _sync_overlay())
    sp_y.valueChanged.connect(lambda _v=0: _sync_overlay())
    sp_scale.valueChanged.connect(lambda _v=0: _sync_overlay())
    cb_fit.currentIndexChanged.connect(lambda _i=0: _sync_overlay())
    cb_blend.currentIndexChanged.connect(lambda _i=0: _sync_overlay())
    cb_anim_in.currentIndexChanged.connect(lambda _i=0: _sync_overlay())
    cb_anim_out.currentIndexChanged.connect(lambda _i=0: _sync_overlay())
    cb_unit.currentIndexChanged.connect(lambda _i=0: _show_times())
    sp_start.valueChanged.connect(lambda _v=0: _spin_to_sec("start", sp_start))
    sp_end.valueChanged.connect(lambda _v=0: _spin_to_sec("end", sp_end))
    sp_head.valueChanged.connect(lambda _v=0: _spin_to_sec("head_pad", sp_head))
    sp_tail.valueChanged.connect(lambda _v=0: _spin_to_sec("tail_pad", sp_tail))
    ck_multi.toggled.connect(_set_multi_enabled)
    picker.list.itemChanged.connect(_on_picker)
    picker.list.currentItemChanged.connect(lambda *_a: _on_picker())
    canvas.item_dragged.connect(_pull_from_canvas)
    _set_multi_enabled(False)
    _show_times()
    _refresh_grid()
    _restore_from_tasks()
    _paint_background()
    _sync_overlay()
    _lock_labels()
    btn_run.clicked.connect(_run)
    btn_close.clicked.connect(dlg.reject)

    _busy_report(0.95, "收尾")
    # Q144：中央监视器。跟着左边的勾选走；在监视器里改完，这边的预览跟着刷新
    def _monitor_tasks():
        return picker.selected_tasks() or list(getattr(parent, "tasks", None) or [])

    def _open_monitor():
        rooms = getattr(parent, "room_host", None)
        if rooms is not None and getattr(dlg, "_embedded_room", False):
            # Q191：中央监视器是另一个房间。看完回到批量加载，这边的预览跟着刷新
            rooms.enter("monitor", tasks=_monitor_tasks(), back="batch",
                        on_edited=lambda _t=None: _on_picker())
            return
        from cutora.ui.central_monitor import open_central_monitor
        mon = open_central_monitor(parent, _monitor_tasks(), parent=dlg)
        if not getattr(mon, "_batch_wired", False):
            mon._batch_wired = True
            mon.edited.connect(lambda _t=None: _on_picker())
        dlg.monitor = mon

    def _monitor_follow(*_a):
        mon = getattr(dlg, "monitor", None)
        try:
            if mon is not None and mon.isVisible():
                mon.set_tasks(_monitor_tasks())
        except RuntimeError:
            dlg.monitor = None

    def _monitor_refresh():
        mon = getattr(dlg, "monitor", None)
        try:
            if mon is not None and mon.isVisible():
                mon.refresh_all()
        except RuntimeError:
            dlg.monitor = None

    def _monitor_close(*_a):
        mon = getattr(dlg, "monitor", None)
        try:
            if mon is not None:
                mon.close()
        except RuntimeError:
            pass
        dlg.monitor = None

    dlg.monitor = None
    dlg.refresh_monitor = _monitor_refresh
    dlg.finished.connect(_monitor_close)
    btn_monitor.clicked.connect(_open_monitor)
    picker.list.itemChanged.connect(_monitor_follow)
    btn_run.clicked.connect(_monitor_refresh)

    def _persist_prefs():
        save_insert_prefs(
            default_dir=ed_dir.text().strip(),
            render_mode=cb_render.currentData() or RENDER_ONCE,
        )

    dlg.finished.connect(lambda _c=0: _persist_prefs())

    def _stop_text_audio(*_a):
        fn = getattr(getattr(dlg, "text_tab", None), "stop_play", None)
        if callable(fn):
            try:
                fn()          # Q190：关窗时试听的声音一起停，播放器放掉
            except RuntimeError:
                pass
    dlg.finished.connect(_stop_text_audio)
    return dlg
