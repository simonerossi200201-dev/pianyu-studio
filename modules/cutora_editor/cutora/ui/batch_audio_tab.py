# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q200 · 批量加载 → 音频 tab（配方）。实时听在剪辑页角色条，这里只管 40 条一次。

试听：仅当 picker 当前行 == host.curr_idx。共用进程里那一台 QAudioSink。
不调用 PreviewEngine，不 media_player.play()。离开 tab / 房间 → sink.stop()。
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from cutora.core.log import get_logger
from cutora.ui.i18n import tr
from cutora.ui.theme.tokens import (
    INSPECTOR_PAIR_GAP,
    INSPECTOR_ROW_GAP,
    INSPECTOR_SECTION_GAP,
    SPACE_2,
    SPACE_6,
)

_LOG = get_logger("cutora.ui.batch_audio_tab")
ROLES = ("main", "bgm", "sfx")
ROLE_LABEL = {"main": "原声", "bgm": "配乐", "sfx": "音效"}


def _picker_row_index(picker) -> int:
    if picker is None:
        return -1
    row = int(getattr(picker, "_last_row", -1) or -1)
    lst = getattr(picker, "list", None)
    if lst is None or row < 0 or row >= lst.count():
        return -1
    it = lst.item(row)
    if it is None:
        return -1
    try:
        return int(it.data(Qt.ItemDataRole.UserRole))
    except Exception:
        return row


class BatchAudioTab(QWidget):
    """Recipe tab: BGM folder, role targets, spatial batch, sfx, audition."""

    def __init__(self, dlg, host, picker=None, parent=None):
        super().__init__(parent if parent is not None else dlg)
        self.setObjectName("batchAudioTab")
        self.setWindowFlags(Qt.WindowType.Widget)
        self.dlg = dlg
        self._host = host
        self.picker = picker
        self._audition_on = False
        self._build()
        self._wire_picker()
        self.sync_from_host()
        self._sync_audition_btn()

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, SPACE_2, 0, 0)
        lay.setSpacing(INSPECTOR_SECTION_GAP)

        def _title(text):
            lab = QLabel(tr(text))
            lab.setObjectName("dockSectionTitle")
            return lab

        def _form():
            form = QFormLayout()
            form.setContentsMargins(0, 0, 0, 0)
            form.setHorizontalSpacing(INSPECTOR_PAIR_GAP)
            form.setVerticalSpacing(INSPECTOR_ROW_GAP)
            form.setLabelAlignment(
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            return form

        # ── 配乐文件夹 ──
        lay.addWidget(_title("配乐"))
        bgm_row = QWidget()
        bgm_l = QHBoxLayout(bgm_row)
        bgm_l.setContentsMargins(0, 0, 0, 0)
        bgm_l.setSpacing(INSPECTOR_PAIR_GAP)
        self.ed_bgm_dir = QLineEdit()
        self.ed_bgm_dir.setObjectName("batchBgmDir")
        self.ed_bgm_dir.setPlaceholderText(tr("配乐文件夹"))
        btn_browse = QPushButton(tr("浏览…"))
        btn_browse.clicked.connect(self._browse_bgm)
        bgm_l.addWidget(self.ed_bgm_dir, 1)
        bgm_l.addWidget(btn_browse, 0)
        lay.addWidget(bgm_row)
        self.btn_assign_bgm = QPushButton(tr("给勾选的视频配乐"))
        self.btn_assign_bgm.setObjectName("btnAssignBgm")
        self.btn_assign_bgm.clicked.connect(self.assign_bgm)
        lay.addWidget(self.btn_assign_bgm)
        self.btn_bgm_nodes = QPushButton(tr("节点连线…"))
        self.btn_bgm_nodes.setObjectName("btnBgmNodes")
        self.btn_bgm_nodes.setToolTip(tr("左边是勾选的视频，右边是文件夹里的音频。点一下就能改连线，双击试听。"))
        self.btn_bgm_nodes.clicked.connect(self.open_bgm_nodes)
        lay.addWidget(self.btn_bgm_nodes)

        # ── 角色音量 ──
        lay.addWidget(_title("角色音量"))
        self.role_sliders = {}
        vol_form = _form()
        defaults = {"main": 100, "bgm": 22, "sfx": 18}
        for role in ROLES:
            row = QWidget()
            hl = QHBoxLayout(row)
            hl.setContentsMargins(0, 0, 0, 0)
            hl.setSpacing(SPACE_2)
            sl = QSlider(Qt.Orientation.Horizontal)
            sl.setObjectName("batchRoleFader_%s" % role)
            sl.setRange(0, 200)
            sl.setValue(defaults[role])
            sl.setMaximumWidth(SPACE_6 * 6)
            lab = QLabel("1.00" if role == "main" else ("0.22" if role == "bgm" else "0.18"))
            lab.setObjectName("hint")
            lab.setMinimumWidth(SPACE_6)
            sl.valueChanged.connect(lambda v, l=lab: l.setText("%.2f" % (v / 100.0)))
            hl.addWidget(sl, 1)
            hl.addWidget(lab, 0)
            vol_form.addRow(tr(ROLE_LABEL[role]), row)
            self.role_sliders[role] = sl
        lay.addLayout(vol_form)
        apply_row = QHBoxLayout()
        apply_row.setContentsMargins(0, 0, 0, 0)
        self.btn_vol_checked = QPushButton(tr("应用到勾选"))
        self.btn_vol_all = QPushButton(tr("应用到全部"))
        self.btn_vol_checked.clicked.connect(lambda: self.apply_volume("checked"))
        self.btn_vol_all.clicked.connect(lambda: self.apply_volume("all"))
        apply_row.addWidget(self.btn_vol_checked)
        apply_row.addWidget(self.btn_vol_all)
        apply_row.addStretch(1)
        lay.addLayout(apply_row)

        # ── 空间立体声 ──
        lay.addWidget(_title("空间立体声"))
        self.chk_spatial_main = QCheckBox(tr("勾选视频的原声使用空间立体声"))
        self.chk_spatial_main.setObjectName("chkSpatialMain")
        lay.addWidget(self.chk_spatial_main)
        spat_form = _form()
        self.cmb_preset = QComboBox()
        self.cmb_preset.setObjectName("cmbSpatialPreset")
        try:
            from cutora.audio.spatial import PRESETS
            for k, spec in PRESETS.items():
                self.cmb_preset.addItem(tr(spec.get("label") or k), k)
        except Exception:
            self.cmb_preset.addItem(tr("舞台现场（话筒在台上）"), "stage")
        spat_form.addRow(tr("预设"), self.cmb_preset)
        self.sp_wet = QDoubleSpinBox()
        self.sp_wet.setRange(0.0, 0.6)
        self.sp_wet.setSingleStep(0.01)
        self.sp_wet.setDecimals(2)
        spat_form.addRow(tr("湿声"), self.sp_wet)
        self.sp_width = QDoubleSpinBox()
        self.sp_width.setObjectName("spSpatialWidth")
        self.sp_width.setRange(0.0, 1.0)
        self.sp_width.setSingleStep(0.05)
        self.sp_width.setDecimals(2)
        spat_form.addRow(tr("立体声宽度"), self.sp_width)
        self.chk_mic = QCheckBox(tr("话筒质感（近讲厚度 + 极轻喷麦 + 空气感）"))
        self.chk_mic.setObjectName("chkMicTexture")
        spat_form.addRow(self.chk_mic)
        self.sp_mic = QDoubleSpinBox()
        self.sp_mic.setObjectName("spMicAmount")
        self.sp_mic.setRange(0.0, 1.5)
        self.sp_mic.setSingleStep(0.1)
        self.sp_mic.setDecimals(2)
        spat_form.addRow(tr("质感强度"), self.sp_mic)
        self.chk_import_default = QCheckBox(tr("新导入的原声默认开启"))
        self.chk_import_default.setObjectName("chkSpatialImportDefault")
        spat_form.addRow(self.chk_import_default)
        lay.addLayout(spat_form)
        note = QLabel(tr("配乐不加空间，避免发糊。"))
        note.setObjectName("hint")
        note.setWordWrap(True)
        lay.addWidget(note)
        spat_btn_row = QHBoxLayout()
        self.btn_spatial_checked = QPushButton(tr("空间应用到勾选"))
        self.btn_spatial_checked.clicked.connect(lambda: self.apply_spatial(False))
        self.btn_spatial_all = QPushButton(tr("空间应用到全部"))
        self.btn_spatial_all.clicked.connect(lambda: self.apply_spatial(True))
        spat_btn_row.addWidget(self.btn_spatial_checked)
        spat_btn_row.addWidget(self.btn_spatial_all)
        spat_btn_row.addStretch(1)
        lay.addLayout(spat_btn_row)

        # ── 音效 ──
        lay.addWidget(_title("音效"))
        self._sfx_checks = {}
        try:
            from cutora.audio.sfx_library import SFX_CATALOG
            cats = list(SFX_CATALOG)
        except Exception:
            cats = []
        for cat in cats:
            cb = QCheckBox(tr(cat.get("label") or cat.get("id") or ""))
            cb.setChecked(bool(cat.get("default_enabled")))
            self._sfx_checks[cat["id"]] = cb
            lay.addWidget(cb)
        sfx_form = _form()
        self.sp_sfx_vol = QDoubleSpinBox()
        self.sp_sfx_vol.setRange(0.0, 1.5)
        self.sp_sfx_vol.setSingleStep(0.01)
        self.sp_sfx_vol.setDecimals(2)
        self.sp_sfx_vol.setValue(0.18)
        sfx_form.addRow(tr("音效音量"), self.sp_sfx_vol)
        self.sp_interval = QDoubleSpinBox()
        self.sp_interval.setRange(5.0, 300.0)
        self.sp_interval.setSingleStep(1.0)
        self.sp_interval.setSuffix(" " + tr("秒"))
        self.sp_interval.setValue(30.0)
        sfx_form.addRow(tr("间隔"), self.sp_interval)
        lay.addLayout(sfx_form)
        sfx_btn = QHBoxLayout()
        self.btn_sfx_checked = QPushButton(tr("铺到勾选"))
        self.btn_sfx_all = QPushButton(tr("铺到全部"))
        self.btn_sfx_checked.clicked.connect(lambda: self.apply_sfx(False))
        self.btn_sfx_all.clicked.connect(lambda: self.apply_sfx(True))
        sfx_btn.addWidget(self.btn_sfx_checked)
        sfx_btn.addWidget(self.btn_sfx_all)
        sfx_btn.addStretch(1)
        lay.addLayout(sfx_btn)

        # ── 降噪（Q206）──
        lay.addWidget(_title("降噪"))
        den_form = _form()
        self.cmb_denoise = QComboBox()
        self.cmb_denoise.setObjectName("cmbDenoise")
        for _lab, _key in (
            ("关闭", "off"), ("仅去噪", "denoise"), ("播客", "podcast"),
            ("YouTube", "youtube"), ("大厅", "hall"),
        ):
            self.cmb_denoise.addItem(tr(_lab), _key)
        den_form.addRow(tr("预设"), self.cmb_denoise)
        self.cmb_denoise_strength = QComboBox()
        self.cmb_denoise_strength.setObjectName("cmbDenoiseStrength")
        for _lab, _key in (("弱", "weak"), ("中", "mid"), ("强", "strong")):
            self.cmb_denoise_strength.addItem(tr(_lab), _key)
        self.cmb_denoise_strength.setCurrentIndex(1)
        den_form.addRow(tr("强度"), self.cmb_denoise_strength)
        lay.addLayout(den_form)
        den_btn = QHBoxLayout()
        self.btn_denoise = QPushButton(tr("降噪应用到勾选"))
        self.btn_denoise.setObjectName("btnDenoiseChecked")
        self.btn_denoise.clicked.connect(self.apply_denoise)
        self.btn_denoise_audition = QPushButton(tr("试听 5 秒"))
        self.btn_denoise_audition.setObjectName("btnDenoiseAudition")
        self.btn_denoise_audition.setToolTip(tr("渲染当前原声前 5 秒，不另开播放器"))
        self.btn_denoise_audition.clicked.connect(self.audition_denoise_five)
        den_btn.addWidget(self.btn_denoise)
        den_btn.addWidget(self.btn_denoise_audition)
        den_btn.addStretch(1)
        lay.addLayout(den_btn)

        lay.addWidget(_title("停顿"))
        pause_btn = QHBoxLayout()
        self.btn_detect_silence = QPushButton(tr("检测静音"))
        self.btn_detect_silence.setObjectName("btnDetectSilence")
        self.btn_detect_silence.clicked.connect(self.detect_silence)
        self.btn_remove_silence = QPushButton(tr("删除 ≥0.8 秒停顿"))
        self.btn_remove_silence.setObjectName("btnRemoveSilence")
        self.btn_remove_silence.clicked.connect(self.remove_silences)
        pause_btn.addWidget(self.btn_detect_silence)
        pause_btn.addWidget(self.btn_remove_silence)
        pause_btn.addStretch(1)
        lay.addLayout(pause_btn)

        lay.addWidget(_title("人声闪避"))
        self.chk_duck = QCheckBox(tr("说话时压低配乐"))
        self.chk_duck.setObjectName("chkDuck")
        lay.addWidget(self.chk_duck)
        duck_form = _form()
        self.cmb_duck_depth = QComboBox()
        self.cmb_duck_depth.setObjectName("cmbDuckDepth")
        for _lab, _db in (("浅 -6 dB", -6), ("中 -12 dB", -12), ("深 -18 dB", -18)):
            self.cmb_duck_depth.addItem(tr(_lab), _db)
        self.cmb_duck_depth.setCurrentIndex(1)
        duck_form.addRow(tr("深度"), self.cmb_duck_depth)
        self.cmb_duck_release = QComboBox()
        self.cmb_duck_release.setObjectName("cmbDuckRelease")
        for _lab, _ms in (("快", 80), ("中", 250), ("慢", 600)):
            self.cmb_duck_release.addItem(tr(_lab), _ms)
        self.cmb_duck_release.setCurrentIndex(1)
        duck_form.addRow(tr("恢复"), self.cmb_duck_release)
        lay.addLayout(duck_form)
        duck_btn = QHBoxLayout()
        self.btn_duck = QPushButton(tr("闪避应用到勾选"))
        self.btn_duck.setObjectName("btnDuckChecked")
        self.btn_duck.clicked.connect(self.apply_duck)
        self.btn_auto_fade = QPushButton(tr("说话边缘 30 ms 淡化"))
        self.btn_auto_fade.setObjectName("btnAutoFades")
        self.btn_auto_fade.clicked.connect(self.apply_auto_fades)
        duck_btn.addWidget(self.btn_duck)
        duck_btn.addWidget(self.btn_auto_fade)
        duck_btn.addStretch(1)
        lay.addLayout(duck_btn)

        lay.addWidget(_title("人声分离"))
        iso_btn = QHBoxLayout()
        self.btn_iso_vocals = QPushButton(tr("只留说话"))
        self.btn_iso_vocals.setObjectName("btnIsolateVocals")
        self.btn_iso_vocals.clicked.connect(lambda: self.isolate("vocals"))
        self.btn_iso_novocals = QPushButton(tr("只留伴奏"))
        self.btn_iso_novocals.setObjectName("btnIsolateNovocals")
        self.btn_iso_novocals.clicked.connect(lambda: self.isolate("novocals"))
        iso_btn.addWidget(self.btn_iso_vocals)
        iso_btn.addWidget(self.btn_iso_novocals)
        iso_btn.addStretch(1)
        lay.addLayout(iso_btn)

        # ── 试听 / 更多 ──
        foot = QHBoxLayout()
        self.btn_audition = QPushButton(tr("试听当前这条"))
        self.btn_audition.setObjectName("btnBatchAudition")
        self.btn_audition.setToolTip(tr("仅当左边勾选行就是当前剪辑条时可用"))
        self.btn_audition.clicked.connect(self.toggle_audition)
        self.btn_more = QPushButton(tr("更多…"))
        self.btn_more.setObjectName("btnAudioMore")
        self.btn_more.setToolTip(tr("打开完整空间立体声 / 音效库 / AI 识别"))
        self.btn_more.clicked.connect(self.open_more)
        foot.addWidget(self.btn_audition)
        foot.addWidget(self.btn_more)
        foot.addStretch(1)
        lay.addLayout(foot)
        lay.addStretch(1)
        self._load_fx()

    def _wire_picker(self):
        picker = self.picker
        if picker is None:
            return
        lst = getattr(picker, "list", None)
        if lst is None:
            return
        lst.itemChanged.connect(lambda *_a: self._sync_audition_btn())
        lst.currentItemChanged.connect(lambda *_a: self._sync_audition_btn())

    def _load_fx(self):
        try:
            from cutora.audio.sfx_library import load_fx_settings
            fx = load_fx_settings() or {}
        except Exception:
            fx = {}
        self.chk_import_default.setChecked(bool(fx.get("spatial_enabled", True)))
        preset = fx.get("spatial_preset") or "stage"
        i = self.cmb_preset.findData(preset)
        self.cmb_preset.setCurrentIndex(max(0, i))
        self.sp_wet.setValue(float(fx.get("spatial_wet", 0.26)))
        self.sp_width.setValue(float(fx.get("spatial_width", 0.62)))
        self.chk_mic.setChecked(bool(fx.get("mic_texture", False)))
        self.sp_mic.setValue(float(fx.get("mic_amount", 0.5)))
        self.sp_sfx_vol.setValue(float(fx.get("sfx_volume", 0.18)))
        self.sp_interval.setValue(float(fx.get("sfx_interval_sec", 30.0)))
        en = fx.get("sfx_enabled") or {}
        for cid, cb in self._sfx_checks.items():
            if cid in en:
                cb.setChecked(bool(en[cid]))

    def _save_sfx_fx(self):
        try:
            from cutora.audio.sfx_library import load_fx_settings, save_fx_settings
            fx = load_fx_settings() or {}
            fx["sfx_volume"] = float(self.sp_sfx_vol.value())
            fx["sfx_interval_sec"] = float(self.sp_interval.value())
            fx["sfx_enabled"] = {cid: cb.isChecked() for cid, cb in self._sfx_checks.items()}
            fx["mic_texture"] = bool(self.chk_mic.isChecked())
            fx["mic_amount"] = float(self.sp_mic.value())
            fx["spatial_enabled"] = bool(self.chk_import_default.isChecked())
            save_fx_settings(fx)
        except Exception:
            _LOG.exception("[Audio] save fx from batch tab failed")

    def _bus(self):
        host = self._host
        return getattr(host, "bus", None) if host is not None else None

    def _checked_ids(self):
        picker = self.picker
        if picker is not None and hasattr(picker, "selected_indices"):
            return list(picker.selected_indices())
        host = self._host
        if host is None:
            return []
        n = len(getattr(host, "tasks", None) or [])
        idx = int(getattr(host, "curr_idx", -1))
        return [idx] if 0 <= idx < n else list(range(n))

    def _browse_bgm(self):
        start = (self.ed_bgm_dir.text() or "").strip()
        folder = QFileDialog.getExistingDirectory(self, tr("选择音频文件夹"), start)
        if folder:
            self.ed_bgm_dir.setText(folder)

    def open_bgm_nodes(self):
        """和视频页同一类的节点连线：勾选的视频对文件夹里的音频，可以试听再应用。"""
        import os
        folder = (self.ed_bgm_dir.text() or "").strip()
        host = self._host
        if host is None:
            return False
        ids = self._checked_ids()
        tasks = getattr(host, "tasks", None) or []
        videos = []
        for i in ids:
            if not (0 <= i < len(tasks)):
                continue
            task = tasks[i] or {}
            name = os.path.basename(str(task.get("video") or "")) or ("#%02d" % (i + 1))
            videos.append((i, name))
        if not videos:
            return False
        lister = getattr(host, "_list_audio_files", None)
        if folder:
            paths = list(lister(folder) if callable(lister) else [folder])
            if not paths and os.path.isdir(folder):
                paths = [folder]
        else:
            paths = []
        saved_plan = getattr(self, "_bgm_link_plan", None)
        if isinstance(saved_plan, dict) and folder:
            entries = saved_plan.get("entries") or []
            if entries and not any(os.path.normcase(str(e)).startswith(os.path.normcase(folder)) for e in entries):
                saved_plan = None
        from cutora.ui.audio_link_dialog import AudioLinkDialog
        dlg = AudioLinkDialog(videos, paths, self, tasks=tasks, host=host, plan=saved_plan)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return False
        self._bgm_link_plan = getattr(dlg, "link_plan", None)
        if not folder and isinstance(self._bgm_link_plan, dict):
            for ent in self._bgm_link_plan.get("entries") or []:
                d = ent if os.path.isdir(str(ent)) else os.path.dirname(str(ent))
                if d and os.path.isdir(d):
                    self.ed_bgm_dir.setText(d)
                    break
        fn = getattr(host, "assign_bgm_links", None)
        if not callable(fn):
            return False
        fn(dlg.mapping())
        return True

    def assign_bgm(self):
        folder = (self.ed_bgm_dir.text() or "").strip()
        bus = self._bus()
        if not folder or bus is None:
            return False
        ids = self._checked_ids()
        try:
            bus.execute(
                "batch.bgm_assign",
                {"dir": folder, "ids": ids},
                source="ui",
            )
            return True
        except Exception:
            _LOG.exception("[Audio] batch bgm assign failed")
            return False

    def apply_volume(self, scope="checked"):
        bus = self._bus()
        host = self._host
        if bus is None or host is None:
            return False
        params = {
            "src": self.role_sliders["main"].value() / 100.0,
            "bgm": self.role_sliders["bgm"].value() / 100.0,
            "sfx": self.role_sliders["sfx"].value() / 100.0,
        }
        try:
            if scope == "all":
                params["scope"] = "all"
                bus.execute("audio.volume_master", params, source="ui")
            else:
                saved = getattr(host, "curr_idx", -1)
                try:
                    for i in self._checked_ids():
                        host.curr_idx = i
                        p = dict(params)
                        p["scope"] = "current"
                        bus.execute("audio.volume_master", p, source="ui")
                finally:
                    host.curr_idx = saved
            bar = getattr(host, "mixer_bar", None)
            sync = getattr(host, "_sync_role_faders_from_task", None)
            if callable(sync):
                sync()
            elif bar is not None and 0 <= getattr(host, "curr_idx", -1) < len(getattr(host, "tasks", None) or []):
                bar.set_from_task(host.tasks[host.curr_idx])
            return True
        except Exception:
            _LOG.exception("[Audio] volume apply failed")
            return False

    def apply_spatial(self, apply_all=False):
        bus = self._bus()
        host = self._host
        if bus is None or host is None:
            return False
        self._save_sfx_fx()
        preset = self.cmb_preset.currentData() or "stage"
        wet = float(self.sp_wet.value())
        width = float(self.sp_width.value())
        enabled = bool(self.chk_spatial_main.isChecked())
        payload = {
            "preset": preset,
            "width": width,
            "wet": wet if enabled else 0.0,
            "all": bool(apply_all),
        }
        try:
            if apply_all:
                bus.execute("audio.spatial.set", payload, source="ui")
            else:
                saved = getattr(host, "curr_idx", -1)
                try:
                    for i in self._checked_ids():
                        host.curr_idx = i
                        p = dict(payload)
                        p["all"] = False
                        bus.execute("audio.spatial.set", p, source="ui")
                finally:
                    host.curr_idx = saved
            self._set_main_spatial_enabled(enabled, apply_all)
            return True
        except Exception:
            _LOG.exception("[Audio] spatial apply failed")
            return False

    def _set_main_spatial_enabled(self, enabled, apply_all):
        host = self._host
        if host is None:
            return
        tasks = getattr(host, "tasks", None) or []
        ids = list(range(len(tasks))) if apply_all else self._checked_ids()
        from cutora.audio.sfx_library import clip_audio_role
        for i in ids:
            if i < 0 or i >= len(tasks):
                continue
            tl = (tasks[i] or {}).get("timeline") if isinstance(tasks[i], dict) else None
            for tr in getattr(tl, "tracks", []) or []:
                if getattr(tr, "type", None) not in ("audio", "bgm", "sfx"):
                    continue
                for c in getattr(tr, "clips", []) or []:
                    if clip_audio_role(c) != "main":
                        continue
                    try:
                        c.spatial_enabled = bool(enabled)
                    except Exception:
                        pass

    def apply_sfx(self, apply_all=False):
        bus = self._bus()
        host = self._host
        if bus is None or host is None:
            return False
        self._save_sfx_fx()
        try:
            if apply_all:
                bus.execute("audio.sfx.apply_all", {}, source="ui")
            else:
                saved = getattr(host, "curr_idx", -1)
                try:
                    for i in self._checked_ids():
                        host.curr_idx = i
                        bus.execute("audio.sfx.apply_current", {}, source="ui")
                finally:
                    host.curr_idx = saved
            return True
        except Exception:
            _LOG.exception("[Audio] sfx apply failed")
            return False

    def _each_checked(self, name, params):
        bus = self._bus()
        host = self._host
        if bus is None or host is None:
            return False
        saved = getattr(host, "curr_idx", -1)
        try:
            for i in self._checked_ids():
                host.curr_idx = i
                bus.execute(name, dict(params), source="ui")
            return True
        except Exception:
            _LOG.exception("[Audio] %s failed", name)
            return False
        finally:
            host.curr_idx = saved

    def apply_duck(self):
        return self._each_checked("audio.duck_set", {
            "enabled": bool(self.chk_duck.isChecked()),
            "depth_db": self.cmb_duck_depth.currentData(),
            "release_ms": self.cmb_duck_release.currentData(),
            "scope": "current",
        })

    def apply_auto_fades(self):
        return self._each_checked("audio.auto_fades", {"dur": 0.03, "scope": "current"})

    def isolate(self, mode):
        return self._each_checked("audio.isolate", {"mode": mode, "scope": "current"})

    def detect_silence(self):
        return self._each_checked("audio.detect_silence", {"scope": "current"})

    def remove_silences(self):
        return self._each_checked(
            "audio.remove_silences", {"scope": "current", "min_dur": 0.8})

    def apply_denoise(self):
        bus = self._bus()
        host = self._host
        if bus is None or host is None:
            return False
        preset = self.cmb_denoise.currentData() or "off"
        strength = self.cmb_denoise_strength.currentData() or "mid"
        saved = getattr(host, "curr_idx", -1)
        try:
            for i in self._checked_ids():
                host.curr_idx = i
                bus.execute("audio.denoise", {
                    "preset": preset,
                    "strength": strength,
                    "scope": "current",
                }, source="ui")
            return True
        except Exception:
            _LOG.exception("[Audio] denoise apply failed")
            return False
        finally:
            host.curr_idx = saved

    def audition_denoise_five(self):
        """Render five seconds of the denoise chain. Does not open a player."""
        import os
        import tempfile
        from cutora.core.proc import run as _no_window_run
        host = self._host
        if host is None:
            return False
        tasks = getattr(host, "tasks", None) or []
        idx = int(getattr(host, "curr_idx", -1))
        if not (0 <= idx < len(tasks)):
            return False
        from cutora.audio.sfx_library import is_main_clip
        from cutora.audio.voice_presets import audition_ffmpeg_argv, denoise_chain, proc_for
        from cutora.core.config import FFMPEG_PATH
        preset = self.cmb_denoise.currentData() or "off"
        strength = self.cmb_denoise_strength.currentData() or "mid"
        proc = proc_for(preset, strength)
        chain = denoise_chain(proc)
        tl = (tasks[idx] or {}).get("timeline") if isinstance(tasks[idx], dict) else None
        src_path = ""
        for tr in getattr(tl, "tracks", []) or []:
            if getattr(tr, "type", None) != "audio":
                continue
            for c in getattr(tr, "clips", []) or []:
                if not is_main_clip(c):
                    continue
                sid = getattr(c, "source_id", None)
                src = getattr(tl, "sources", {}).get(sid) if sid else None
                src_path = str(getattr(src, "path", "") or "")
                if src_path:
                    break
            if src_path:
                break
        if not src_path or not os.path.isfile(src_path):
            return False
        fd, dest = tempfile.mkstemp(prefix="cutora_dn5_", suffix=".wav")
        os.close(fd)
        cmd = audition_ffmpeg_argv(FFMPEG_PATH, src_path, chain, dest)
        r = _no_window_run(cmd, capture_output=True)
        if r.returncode != 0 or not os.path.isfile(dest):
            _LOG.info("[Audio] denoise audition failed")
            return False
        _LOG.info("[Audio] denoise audition %s", os.path.basename(dest))
        return True

    def audition_allowed(self) -> bool:
        host = self._host
        if host is None:
            return False
        curr = int(getattr(host, "curr_idx", -1))
        if curr < 0:
            return False
        row = _picker_row_index(self.picker)
        if row < 0:
            row = curr
            picker = self.picker
            if picker is not None and getattr(picker, "_last_row", -1) < 0:
                picker._last_row = curr
        return row == curr

    def _sync_audition_btn(self):
        ok = self.audition_allowed()
        self.btn_audition.setEnabled(ok)
        if not ok and self._audition_on:
            self.stop_audition()

    def toggle_audition(self):
        if self._audition_on:
            self.stop_audition()
            return
        self.start_audition()

    def start_audition(self):
        if not self.audition_allowed():
            self.btn_audition.setEnabled(False)
            return False
        host = self._host
        start = getattr(host, "start_batch_audition", None)
        if not callable(start):
            return False
        ok = bool(start())
        self._audition_on = bool(ok)
        if ok:
            self.btn_audition.setText(tr("停止试听"))
        return ok

    def stop_audition(self):
        host = self._host
        stop = getattr(host, "stop_batch_audition", None)
        if callable(stop):
            stop()
        self._audition_on = False
        self.btn_audition.setText(tr("试听当前这条"))
        self._sync_audition_btn()

    def open_more(self):
        host = self._host
        if host is not None:
            fn = getattr(host, "open_audio_fx_dialog", None)
            if callable(fn):
                fn()
                return
        from cutora.ui.panels.audio_fx import AudioFxDialog
        dlg = AudioFxDialog(host if host is not None else self)
        dlg.exec()

    def sync_from_host(self):
        host = self._host
        if host is None:
            return
        tasks = getattr(host, "tasks", None) or []
        idx = int(getattr(host, "curr_idx", -1))
        if not (0 <= idx < len(tasks)):
            return
        from cutora.audio.sfx_library import clip_audio_role, gain_db_to_linear
        found = {r: [] for r in ROLES}
        spatial_on = False
        tl = (tasks[idx] or {}).get("timeline") if isinstance(tasks[idx], dict) else None
        for tr in getattr(tl, "tracks", []) or []:
            if getattr(tr, "type", None) not in ("audio", "bgm", "sfx"):
                continue
            for c in getattr(tr, "clips", []) or []:
                role = clip_audio_role(c)
                if role in found:
                    found[role].append(gain_db_to_linear(getattr(c, "gain_db", 0.0)))
                if role == "main" and bool(getattr(c, "spatial_enabled", False)):
                    spatial_on = True
        fallback = {"main": 1.0, "bgm": 0.22, "sfx": 0.18}
        for role, sl in self.role_sliders.items():
            vals = found.get(role) or []
            linear = float(vals[0]) if vals else fallback[role]
            sl.blockSignals(True)
            sl.setValue(int(round(max(0.0, min(2.0, linear)) * 100.0)))
            sl.blockSignals(False)
        self.chk_spatial_main.blockSignals(True)
        self.chk_spatial_main.setChecked(spatial_on)
        self.chk_spatial_main.blockSignals(False)
        self._sync_audition_btn()


def build_audio_tab(dlg, host, picker=None):
    return BatchAudioTab(dlg, host, picker)
