# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Settings dialog: 外观 / 通用 / 性能 / AI / 路径 / 快捷键."""
from __future__ import annotations

import os

from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog,
    QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton,
    QSpinBox, QTabWidget, QVBoxLayout, QWidget, QTableWidget, QTableWidgetItem,
)

from cutora.core.brand import APP_VERSION
from cutora.core.history import _load_app_settings, _save_app_settings
from cutora.core.paths import FFMPEG, MODELS, user_cache_dir
from cutora.ui.i18n import localize_button_box, tr
from cutora.ui.shortcuts import help_rows


class SettingsDialog(QDialog):
    """Ctrl+, settings. Performance actions call back into MainWindow."""

    def __init__(self, host, initial_tab: int = 0, parent=None):
        super().__init__(parent or host)
        self.host = host
        self.setWindowTitle(tr("设置"))
        self.setMinimumWidth(560)
        self.resize(640, 480)
        lay = QVBoxLayout(self)
        self.tabs = QTabWidget()
        self.tabs.addTab(self._tab_appearance(), tr("外观"))
        self.tabs.addTab(self._tab_general(), tr("通用"))
        self.tabs.addTab(self._tab_performance(), tr("性能"))
        self.tabs.addTab(self._tab_ai(), tr("AI 提供商"))
        self.tabs.addTab(self._tab_models(), tr("模型"))
        self.tabs.addTab(self._tab_paths(), tr("路径"))
        self.tabs.addTab(self._tab_shortcuts(), tr("快捷键"))
        self.tabs.addTab(self._tab_api(), tr("代理接口"))
        self.tabs.addTab(self._tab_subtitle(), tr("字幕"))
        lay.addWidget(self.tabs)
        box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        localize_button_box(box)
        box.accepted.connect(self._on_ok)
        box.rejected.connect(self.reject)
        lay.addWidget(box)
        if 0 <= int(initial_tab) < self.tabs.count():
            self.tabs.setCurrentIndex(int(initial_tab))

    def _tab_appearance(self) -> QWidget:
        from cutora.ui.panels.appearance import AppearancePage
        return AppearancePage(self)

    def _tab_general(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        later = tr("将在后续版本接入")
        self.cmb_lang = QComboBox()
        self.cmb_lang.addItem(tr("中文"), "zh")
        self.cmb_lang.addItem(tr("English"), "en")
        self.cmb_lang.setEnabled(False)
        self.cmb_lang.setToolTip(later)
        form.addRow(tr("语言:"), self.cmb_lang)



        from cutora.core.autosave import DEFAULT_MINUTES, SETTING_MINUTES, interval_minutes
        self.sp_autosave = QSpinBox()
        self.sp_autosave.setRange(1, 60)
        self.sp_autosave.setValue(int(interval_minutes() or DEFAULT_MINUTES))
        self.sp_autosave.setSuffix(tr(" 分钟"))
        self.sp_autosave.setEnabled(True)
        self.sp_autosave.setToolTip(tr("每隔这么多分钟把工程写到旁边的 .autosave.json。正常保存或退出会删掉它。"))
        form.addRow(tr("自动保存间隔:"), self.sp_autosave)

        from cutora.model.canvas import (
            CANVAS_PRESETS, SETTING_NEW_CANVAS, default_new_canvas_wh,
        )
        self.cmb_canvas = QComboBox()
        for preset_w, preset_h, lab in CANVAS_PRESETS:
            self.cmb_canvas.addItem(
                "%s (%d×%d)" % (tr(lab), preset_w, preset_h),
                "%dx%d" % (preset_w, preset_h))
        cur_w, cur_h = default_new_canvas_wh()
        idx = self.cmb_canvas.findData("%dx%d" % (cur_w, cur_h))
        if idx < 0:
            idx = 0
        self.cmb_canvas.setCurrentIndex(idx)
        self.cmb_canvas.setToolTip(tr("新建工程时使用的画布尺寸。默认 720×1280。"))
        form.addRow(tr("新建工程默认画布:"), self.cmb_canvas)

        from cutora.render.export_dest import export_ask_dest
        self.chk_export_ask = QCheckBox(tr("导出前询问成品文件夹（否=就地）"))
        self.chk_export_ask.setChecked(export_ask_dest())
        form.addRow(self.chk_export_ask)

        from cutora.render.export_verify import contact_sheet_enabled
        self.chk_contact_sheet = QCheckBox(tr("导出后生成校验缩略图（排查问题用）"))
        self.chk_contact_sheet.setChecked(contact_sheet_enabled())
        self.chk_contact_sheet.setToolTip(tr(
            "默认关。打开后在成品目录的 _verify 里放一张 3×3 抽帧图，方便排查黑帧和花屏。"))
        form.addRow(self.chk_contact_sheet)

        from cutora.ui.badge_prefs import badge_edge_hide, badge_enabled
        self.chk_badge = QCheckBox(tr("最小化时显示悬浮图标"))
        self.chk_badge.setChecked(badge_enabled())
        self.chk_badge.setToolTip(tr(
            "最小化时收成屏幕上的悬浮图标。关掉则缩进任务栏。默认开。"))
        self.chk_badge_edge = QCheckBox(tr("悬浮图标贴边隐藏"))
        self.chk_badge_edge.setChecked(badge_edge_hide())
        self.chk_badge_edge.setEnabled(self.chk_badge.isChecked())
        self.chk_badge_edge.setToolTip(tr(
            "拖到屏幕边缘后缩成一条，鼠标移上去再展开。没有悬浮图标时这项不可用。"))
        self.btn_badge_reset = QPushButton(tr("重置悬浮图标位置"))
        self.btn_badge_reset.setToolTip(tr("图标跑到屏幕外面时，把它放回主屏幕右下角。"))
        form.addRow(self.chk_badge)
        form.addRow(self.chk_badge_edge)
        form.addRow(self.btn_badge_reset)
        self.chk_badge.toggled.connect(self._on_badge_toggled)
        self.chk_badge_edge.toggled.connect(self._on_badge_edge_toggled)
        self.btn_badge_reset.clicked.connect(self._on_badge_reset)

        from cutora.audio.loudness import get_loudness_settings
        ln = get_loudness_settings()
        self.cmb_loudness = QComboBox()
        self.cmb_loudness.addItem(tr("关"), "off")
        self.cmb_loudness.addItem("-14 LUFS", "lufs14")
        self.cmb_loudness.addItem("-16 LUFS", "lufs16")
        self.cmb_loudness.addItem(tr("自定义"), "custom")
        idx = max(0, self.cmb_loudness.findData(ln.mode))
        self.cmb_loudness.setCurrentIndex(idx)
        self.cmb_loudness.setToolTip(tr("导出总线响度。默认 -14 LUFS。双趟 loudnorm，真峰值 -1 dBTP。"))
        form.addRow(tr("目标响度:"), self.cmb_loudness)
        self.sp_lufs = QDoubleSpinBox()
        self.sp_lufs.setRange(-24.0, -5.0)
        self.sp_lufs.setDecimals(1)
        self.sp_lufs.setSingleStep(0.5)
        self.sp_lufs.setSuffix(" LUFS")
        self.sp_lufs.setValue(float(ln.target_i))
        self.sp_lufs.setEnabled(ln.mode == "custom")
        form.addRow(tr("自定义 LUFS:"), self.sp_lufs)
        self.cmb_loudness.currentIndexChanged.connect(
            lambda _i: self.sp_lufs.setEnabled(self.cmb_loudness.currentData() == "custom"))
        from cutora.render.export_sidecar import sidecar_flags
        from cutora.ui.widgets.collapsible import labeled_toggle
        flags = sidecar_flags()
        wrap = QWidget()
        row_sc = QHBoxLayout(wrap)
        row_sc.setContentsMargins(0, 0, 0, 0)
        row_sc.setSpacing(12)
        self._sidecar_srt_host, self.chk_sidecar_srt = labeled_toggle("SRT")
        self._sidecar_cover_host, self.chk_sidecar_cover = labeled_toggle("封面")
        self._sidecar_report_host, self.chk_sidecar_report = labeled_toggle("报告")
        self._sidecar_chapters_host, self.chk_sidecar_chapters = labeled_toggle("章节")
        self.chk_sidecar_srt.setChecked(bool(flags.get("srt")))
        self.chk_sidecar_cover.setChecked(bool(flags.get("cover")))
        self.chk_sidecar_report.setChecked(bool(flags.get("report")))
        self.chk_sidecar_chapters.setChecked(bool(flags.get("chapters")))
        row_sc.addWidget(self._sidecar_srt_host)
        row_sc.addWidget(self._sidecar_cover_host)
        row_sc.addWidget(self._sidecar_report_host)
        row_sc.addWidget(self._sidecar_chapters_host)
        row_sc.addStretch(1)
        form.addRow(tr("附带文件:"), wrap)
        row_cfg = QHBoxLayout()
        self.btn_export_cfg = QPushButton(tr("导出配置…"))
        self.btn_import_cfg = QPushButton(tr("导入配置…"))
        self.btn_export_cfg.clicked.connect(self._export_config)
        self.btn_import_cfg.clicked.connect(self._import_config)
        row_cfg.addWidget(self.btn_export_cfg)
        row_cfg.addWidget(self.btn_import_cfg)
        row_cfg.addStretch(1)
        form.addRow(tr("配置迁移:"), row_cfg)
        return w

    def _tab_performance(self) -> QWidget:
        from cutora.core.config import DEVICE
        from cutora.render.parallel import (
            PARALLEL_EXPORT_ENABLED, PARALLEL_EXPORT_WORKERS,
        )

        w = QWidget()
        lay = QVBoxLayout(w)
        self.chk_gpu = QCheckBox(tr("GPU 加速"))
        host_chk = getattr(self.host, "chk_gpu", None)
        if host_chk is not None:
            self.chk_gpu.setChecked(host_chk.isChecked())
        else:
            self.chk_gpu.setChecked(DEVICE == "cuda")
        lay.addWidget(self.chk_gpu)
        from cutora.render.encoder_probe import status_text as _enc_status
        self.lbl_encoder_probe = QLabel(_enc_status())
        self.lbl_encoder_probe.setWordWrap(True)
        self.lbl_encoder_probe.setObjectName("hint")
        lay.addWidget(self.lbl_encoder_probe)

        self.chk_parallel = QCheckBox(tr("并行批量导出"))
        self.chk_parallel.setChecked(bool(PARALLEL_EXPORT_ENABLED))
        lay.addWidget(self.chk_parallel)

        row = QHBoxLayout()
        row.addWidget(QLabel(tr("并行进程数:")))
        self.sp_workers = QSpinBox()
        self.sp_workers.setRange(1, 12)
        self.sp_workers.setValue(int(PARALLEL_EXPORT_WORKERS))
        row.addWidget(self.sp_workers)
        row.addStretch(1)
        lay.addLayout(row)

        row_n = QHBoxLayout()
        row_n.addWidget(QLabel(tr("NVENC 并发会话:")))
        self.sp_nvenc = QSpinBox()
        # Q183：0 = 自动（启动时后台实测本机能同时开几路，按驱动缓存）；>0 才是手动覆盖
        self.sp_nvenc.setRange(0, 8)
        self.sp_nvenc.setSpecialValueText(tr("自动（实测）"))
        src_text = ""
        try:
            from cutora.render.export_guard import nvenc_manual_override, nvenc_sessions_source
            manual = nvenc_manual_override()
            self.sp_nvenc.setValue(int(manual) if manual else 0)
            src_text = nvenc_sessions_source()
        except Exception:
            self.sp_nvenc.setValue(0)
        row_n.addWidget(self.sp_nvenc)
        lbl_src = QLabel(src_text)
        lbl_src.setStyleSheet("color: #8a8378;")
        row_n.addWidget(lbl_src)
        row_n.addStretch(1)
        lay.addLayout(row_n)

        btn_vram = QPushButton(tr("显存占用情况"))
        if hasattr(self.host, "_on_show_vram_info"):
            btn_vram.clicked.connect(self.host._on_show_vram_info)
        btn_free = QPushButton(tr("释放显存 (卸载 AI 模型)"))
        if hasattr(self.host, "_on_release_vram"):
            btn_free.clicked.connect(self.host._on_release_vram)
        lay.addWidget(btn_vram)
        lay.addWidget(btn_free)

        from cutora.asr.detect import get_detect_threshold
        row_d = QHBoxLayout()
        row_d.addWidget(QLabel(tr("语言检测置信度阈值:")))
        self.sp_detect_thr = QDoubleSpinBox()
        self.sp_detect_thr.setRange(0.10, 1.00)
        self.sp_detect_thr.setSingleStep(0.05)
        self.sp_detect_thr.setDecimals(2)
        self.sp_detect_thr.setValue(get_detect_threshold())
        row_d.addWidget(self.sp_detect_thr)
        row_d.addStretch(1)
        lay.addLayout(row_d)
        lay.addStretch(1)
        return w

    def _tab_ai(self) -> QWidget:
        from cutora.ui.panels.ai_settings import AIProvidersPage

        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        self.ai_page = AIProvidersPage(w)
        lay.addWidget(self.ai_page)
        return w

    def _tab_models(self) -> QWidget:
        from cutora.ui.panels.models import ModelsPage
        self.models_page = ModelsPage(self.host, self)
        return self.models_page

    def _tab_paths(self) -> QWidget:
        from cutora.asr.models import models_dir
        w = QWidget()
        form = QFormLayout(w)
        row = QHBoxLayout()
        self.ed_models_dir = QLineEdit(models_dir())
        self.btn_models_dir = QPushButton(tr("浏览…"))
        self.btn_models_dir.clicked.connect(self._browse_models_dir)
        row.addWidget(self.ed_models_dir, 1)
        row.addWidget(self.btn_models_dir)
        form.addRow(tr("模型目录:"), row)
        from cutora.audio.waveform import (
            clear_waveform_cache, waveform_cache_bytes,
        )
        cache_row = QHBoxLayout()
        bytes_now = waveform_cache_bytes()
        mb = bytes_now / (1024.0 * 1024.0)
        self.lbl_wave_cache = QLabel(tr("波形缓存 {n:.1f} MB", n=mb))
        btn_clear_wave = QPushButton(tr("清空波形缓存"))
        def _clear_wave():
            freed = clear_waveform_cache()
            self.lbl_wave_cache.setText(tr(
                "波形缓存 {n:.1f} MB",
                n=waveform_cache_bytes() / (1024.0 * 1024.0)))
            QMessageBox.information(
                self, tr("波形缓存"),
                tr("已清理 {n:.1f} MB", n=freed / (1024.0 * 1024.0)))
        btn_clear_wave.clicked.connect(_clear_wave)
        cache_row.addWidget(self.lbl_wave_cache)
        cache_row.addWidget(btn_clear_wave)
        cache_row.addStretch(1)
        form.addRow(tr("缓存目录:"), QLabel(user_cache_dir()))
        form.addRow(tr("波形缓存:"), cache_row)
        form.addRow(tr("FFmpeg:"), QLabel(FFMPEG))
        form.addRow(tr("版本:"), QLabel(APP_VERSION))
        hint = QLabel(tr("模型目录可改（需可写）。缓存与 FFmpeg 由安装布局决定。"))
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        form.addRow(hint)
        return w

    def _browse_models_dir(self) -> None:
        start = (self.ed_models_dir.text() or "").strip() or MODELS
        folder = QFileDialog.getExistingDirectory(self, tr("选择模型目录"), start)
        if folder:
            self.ed_models_dir.setText(folder)

    def _tab_shortcuts(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
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
        lay.addWidget(table)
        note = QLabel(tr("本页只读。自定义快捷键在后续任务。"))
        note.setObjectName("hint")
        lay.addWidget(note)
        return w

    def _tab_api(self) -> QWidget:
        from cutora.ui.panels.api_settings import ApiSettingsPage
        self.api_page = ApiSettingsPage(self.host, self)
        return self.api_page

    def _tab_subtitle(self) -> QWidget:
        from cutora.subtitle.layout import AUTO_RELAYOUT_KEY, auto_relayout_enabled
        w = QWidget()
        lay = QVBoxLayout(w)
        self.chk_auto_relayout = QCheckBox(tr("自动字幕后按排版规则断行"))
        self.chk_auto_relayout.setChecked(auto_relayout_enabled())
        self.chk_auto_relayout.setToolTip(
            tr("关掉则自动字幕不写入 segment.lines，画面走旧的自动换行。"))
        lay.addWidget(self.chk_auto_relayout)
        lay.addStretch(1)
        self._auto_relayout_key = AUTO_RELAYOUT_KEY
        return w

    def _on_badge_toggled(self, on: bool) -> None:
        from cutora.ui.badge_prefs import set_badge_enabled
        set_badge_enabled(bool(on))
        # Grey the child option. Do not rewrite its stored value.
        self.chk_badge_edge.setEnabled(bool(on))
        host = self.host
        fn = getattr(host, "apply_badge_pref", None)
        if callable(fn):
            fn()

    def _on_badge_edge_toggled(self, on: bool) -> None:
        if not self.chk_badge_edge.isEnabled():
            return
        from cutora.ui.badge_prefs import set_badge_edge_hide
        set_badge_edge_hide(bool(on))
        host = self.host
        fn = getattr(host, "apply_badge_edge_pref", None)
        if callable(fn):
            fn()

    def _on_badge_reset(self) -> None:
        host = self.host
        fn = getattr(host, "reset_floating_badge", None)
        if callable(fn):
            fn()
            return
        from cutora.ui.badge_prefs import clear_badge_position
        clear_badge_position()

    def _on_ok(self) -> None:
        s = _load_app_settings()
        s["ui_language"] = self.cmb_lang.currentData() or "zh"
        from cutora.core.autosave import SETTING_MINUTES
        s[SETTING_MINUTES] = int(self.sp_autosave.value())
        if getattr(self, "cmb_canvas", None) is not None:
            from cutora.model.canvas import SETTING_NEW_CANVAS
            spec = self.cmb_canvas.currentData() or "720x1280"
            s[SETTING_NEW_CANVAS] = str(spec)
        host_timer = getattr(self.host, "_restart_autosave_timer", None)
        if callable(host_timer):
            host_timer()
        if getattr(self, "cmb_loudness", None) is not None:
            from cutora.audio.loudness import SETTING_MODE, SETTING_TARGET, target_i_for
            mode = self.cmb_loudness.currentData() or "off"
            s[SETTING_MODE] = mode
            s[SETTING_TARGET] = float(self.sp_lufs.value()) if mode == "custom" else target_i_for(mode)
        if getattr(self, "chk_export_ask", None) is not None:
            from cutora.render.export_dest import SETTING_ASK
            s[SETTING_ASK] = bool(self.chk_export_ask.isChecked())
        if getattr(self, "chk_contact_sheet", None) is not None:
            from cutora.render.export_verify import SETTING_CONTACT_SHEET
            s[SETTING_CONTACT_SHEET] = bool(self.chk_contact_sheet.isChecked())
        if getattr(self, "chk_sidecar_srt", None) is not None:
            from cutora.render.export_sidecar import (
                SETTING_CHAPTERS, SETTING_COVER, SETTING_REPORT, SETTING_SRT,
            )
            s[SETTING_SRT] = bool(self.chk_sidecar_srt.isChecked())
            s[SETTING_COVER] = bool(self.chk_sidecar_cover.isChecked())
            s[SETTING_REPORT] = bool(self.chk_sidecar_report.isChecked())
            if getattr(self, "chk_sidecar_chapters", None) is not None:
                s[SETTING_CHAPTERS] = bool(self.chk_sidecar_chapters.isChecked())
        if getattr(self, "chk_auto_relayout", None) is not None:
            from cutora.subtitle.layout import AUTO_RELAYOUT_KEY
            s[AUTO_RELAYOUT_KEY] = bool(self.chk_auto_relayout.isChecked())
        if getattr(self, "sp_detect_thr", None) is not None:
            from cutora.asr.detect import THRESHOLD_SETTING_KEY
            s[THRESHOLD_SETTING_KEY] = float(self.sp_detect_thr.value())
        models_changed = None
        if getattr(self, "ed_models_dir", None) is not None:
            from cutora.asr.models import models_dir, validate_models_dir
            old = models_dir()
            want = (self.ed_models_dir.text() or "").strip()
            try:
                new = validate_models_dir(want)
            except ValueError as exc:
                QMessageBox.warning(self, tr("模型目录"), str(exc))
                return
            if os.path.normcase(new) == os.path.normcase(MODELS):
                s.pop("asr.models_dir", None)
            else:
                s["asr.models_dir"] = new
            if os.path.normcase(new) != os.path.normcase(old):
                models_changed = new
        _save_app_settings(s)
        if models_changed:
            QMessageBox.information(
                self, tr("模型目录"),
                tr("已改为 {path}。原目录里的模型不会自动搬走，请自行复制或重新下载。",
                   path=models_changed))
        gpu_on = self.chk_gpu.isChecked()
        host_chk = getattr(self.host, "chk_gpu", None)
        if host_chk is not None and host_chk.isChecked() != gpu_on:
            host_chk.blockSignals(True)
            host_chk.setChecked(gpu_on)
            host_chk.blockSignals(False)
            self.host.toggle_gpu()
        from cutora.core.history import _save_gpu_enabled
        _save_gpu_enabled(
            host_chk.isChecked() if host_chk is not None else gpu_on)
        import cutora.render.parallel as _par
        _par.PARALLEL_EXPORT_ENABLED = bool(self.chk_parallel.isChecked())
        _par.PARALLEL_EXPORT_WORKERS = int(self.sp_workers.value())
        _par._save_parallel_settings(
            _par.PARALLEL_EXPORT_ENABLED, _par.PARALLEL_EXPORT_WORKERS)
        try:
            from cutora.render.export_guard import save_nvenc_max_sessions
            save_nvenc_max_sessions(int(self.sp_nvenc.value()))
        except Exception:
            pass
        if hasattr(self.host, "act_parallel_export"):
            self.host.act_parallel_export.blockSignals(True)
            self.host.act_parallel_export.setChecked(_par.PARALLEL_EXPORT_ENABLED)
            self.host.act_parallel_export.blockSignals(False)
        if hasattr(self, "ai_page") and hasattr(self.ai_page, "save_prefs"):
            self.ai_page.save_prefs()
        if hasattr(self, "api_page") and hasattr(self.api_page, "save_prefs"):
            self.api_page.save_prefs()
        self.accept()

    def _export_config(self):
        from cutora.model.config_pack import export_config
        path, _ = QFileDialog.getSaveFileName(
            self, tr("导出配置"), "cutora-config.zip", "ZIP (*.zip)")
        if not path:
            return
        try:
            out = export_config(path)
            QMessageBox.information(
                self, tr("导出配置"),
                tr("已写入 {path}", path=out.get("path") or path))
        except Exception as exc:
            QMessageBox.warning(self, tr("导出配置"), str(exc))

    def _import_config(self):
        from cutora.model.config_pack import import_config
        path, _ = QFileDialog.getOpenFileName(
            self, tr("导入配置"), "", "ZIP (*.zip)")
        if not path:
            return
        overwrite = QMessageBox.question(
            self, tr("导入配置"),
            tr("同名项默认跳过。要覆盖已有同名项吗？"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        ) == QMessageBox.StandardButton.Yes
        try:
            report = import_config(path, overwrite=overwrite)
            skipped = []
            for sec, rec in (report.get("sections") or {}).items():
                skipped.extend(rec.get("skipped") or [])
            msg = tr("已导入。")
            if skipped:
                msg += "\n" + tr("跳过同名 {n} 项", n=str(len(skipped)))
            QMessageBox.information(self, tr("导入配置"), msg)
        except Exception as exc:
            QMessageBox.warning(self, tr("导入配置"), str(exc))
