# SPDX-License-Identifier: GPL-3.0-or-later
"""音效 / 空间立体声 / AI 识别 设置对话框。"""
from __future__ import annotations

from PySide6.QtCore import (
    QThread,
    Signal,
)
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
    QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QPushButton, QScrollArea, QTabWidget, QVBoxLayout, QWidget,
)

from cutora.audio.sfx_library import (
    SFX_CATALOG, SFX_DIR, load_fx_settings,
    missing_sfx_files, place_sfx_on_task, save_fx_settings,
)
from cutora.audio.spatial import PRESETS
from cutora.ui.i18n import localize_button_box

class _AiWorker(QThread):
    one_done = Signal(int, dict)
    all_done = Signal()

    def __init__(self, items, settings, parent=None):
        super().__init__(parent)
        self.items = items  # list of (idx, video_path)
        self.settings = dict(settings)
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        from cutora.audio.sfx_ai import analyze_video
        for idx, path in self.items:
            if self._stop:
                break
            try:
                result = analyze_video(path, self.settings)
            except Exception as e:
                result = {"ok": False, "reason": str(e), "sfx": [], "scene": "other"}
            self.one_done.emit(idx, result)
        self.all_done.emit()

class AudioFxDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("空间立体声 / 音效库")
        self.setMinimumSize(560, 640)
        self.fx = load_fx_settings()
        self._checks = {}
        self._ai_worker = None
        self._build()
        self._load_to_ui()

    def _build(self):
        root = QVBoxLayout(self)
        tabs = QTabWidget()
        tabs.addTab(self._tab_spatial(), "空间立体声")
        tabs.addTab(self._tab_sfx(), "音效库")
        tabs.addTab(self._tab_ai(), "AI 识别")
        root.addWidget(tabs)

        btns = QHBoxLayout()
        self.btn_apply_cur = QPushButton("应用到当前视频")
        self.btn_apply_all = QPushButton("应用到全部视频")
        self.btn_apply_cur.clicked.connect(lambda: self._emit_apply(False))
        self.btn_apply_all.clicked.connect(lambda: self._emit_apply(True))
        btns.addWidget(self.btn_apply_cur)
        btns.addWidget(self.btn_apply_all)
        btns.addStretch(1)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        localize_button_box(box)
        box.accepted.connect(self._on_save)
        box.rejected.connect(self.reject)
        btns.addWidget(box)
        root.addLayout(btns)

        self.apply_current_requested = False
        self.apply_all_requested = False

    def _tab_spatial(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        hint = QLabel(
            "导出前默认给每条视频的原声加上真实厅堂的空间感。用的是卷积混响："
            "早期反射按真实房间几何算出来，左右两个话筒振膜分别计算，混响尾巴左右去相关——"
            "这才是「话筒架在舞台上」那种空间感的来源。讲话时混响会自动让位（侧链压缩），"
            "所以空间变大但字不会糊。只处理音频，不影响字幕和画面。"
        )
        hint.setWordWrap(True)
        hint.setObjectName("hint")
        lay.addWidget(hint)

        g = QGroupBox("默认空间立体声")
        f = QFormLayout(g)
        from cutora.ui.widgets.collapsible import labeled_toggle
        self.chk_spatial_host, self.chk_spatial = labeled_toggle(
            "新导入的原声默认开启（推荐大型演讲 / 祷告现场）")
        f.addRow(self.chk_spatial_host)
        self.cmb_preset = QComboBox()
        for k, spec in PRESETS.items():
            self.cmb_preset.addItem(spec["label"], k)
        f.addRow("空间预设:", self.cmb_preset)
        self.sp_wet = QDoubleSpinBox()
        self.sp_wet.setRange(0.0, 0.6)
        self.sp_wet.setSingleStep(0.01)
        self.sp_wet.setDecimals(2)
        f.addRow("湿声比例 Dry/Wet:", self.sp_wet)
        self.sp_width = QDoubleSpinBox()
        self.sp_width.setRange(0.0, 1.0)
        self.sp_width.setSingleStep(0.05)
        self.sp_width.setDecimals(2)
        f.addRow("立体声宽度:", self.sp_width)
        # ── 话筒质感 ──
        # 近讲效应(低频厚度) + 极轻的喷麦 + 电容麦的空气感。
        # 它刻意不碰 2~5kHz 那一段(咬字), 所以加质感不会牺牲清晰度。
        self.chk_mic = QCheckBox("话筒质感（近讲厚度 + 极轻喷麦 + 空气感）")
        f.addRow(self.chk_mic)
        self.sp_mic = QDoubleSpinBox()
        self.sp_mic.setRange(0.0, 1.5)
        self.sp_mic.setSingleStep(0.1)
        self.sp_mic.setDecimals(2)
        self.sp_mic.setToolTip("0.4 很含蓄, 0.6 明显但自然, 1.0 以上就是「贴着麦说话」了")
        f.addRow("质感强度:", self.sp_mic)
        lay.addWidget(g)
        note = QLabel("单条音频仍可在属性面板里单独开关。配乐默认不加混响，避免发糊。")
        note.setWordWrap(True)
        note.setObjectName("hint")
        lay.addWidget(note)
        lay.addStretch(1)
        return w

    def _tab_sfx(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        hint = QLabel(
            "音效按「间隔 + 持续时长」随机插入，不会一直盖住人声。"
            "大厅同期声是持续铺底；鼓掌 / Amen 按间隔出现一小段。"
            "音量请压低，避免鼓掌声盖过讲者。"
        )
        hint.setWordWrap(True)
        hint.setObjectName("hint")
        lay.addWidget(hint)

        g1 = QGroupBox("播放规则")
        f = QFormLayout(g1)
        self.sp_sfx_vol = QDoubleSpinBox()
        self.sp_sfx_vol.setRange(0.0, 1.5)
        self.sp_sfx_vol.setSingleStep(0.01)
        self.sp_sfx_vol.setDecimals(2)
        f.addRow("音效音量:", self.sp_sfx_vol)
        self.sp_interval = QDoubleSpinBox()
        self.sp_interval.setRange(5.0, 300.0)
        self.sp_interval.setSingleStep(1.0)
        self.sp_interval.setSuffix(" 秒")
        f.addRow("间隔:", self.sp_interval)
        self.sp_burst = QDoubleSpinBox()
        self.sp_burst.setRange(0.5, 20.0)
        self.sp_burst.setSingleStep(0.5)
        self.sp_burst.setSuffix(" 秒")
        f.addRow("每段持续:", self.sp_burst)
        self.sp_jitter = QDoubleSpinBox()
        self.sp_jitter.setRange(0.0, 60.0)
        self.sp_jitter.setSingleStep(1.0)
        self.sp_jitter.setSuffix(" 秒")
        f.addRow("随机抖动:", self.sp_jitter)
        self.chk_auto = QCheckBox("导出时若还没有音效，自动按上面规则铺上")
        f.addRow(self.chk_auto)
        lay.addWidget(g1)

        g2 = QGroupBox("启用的音效（可多选）")
        v = QVBoxLayout(g2)
        groups = {}
        for cat in SFX_CATALOG:
            groups.setdefault(cat["group"], []).append(cat)
        for gname, cats in groups.items():
            v.addWidget(QLabel(gname))
            for cat in cats:
                cb = QCheckBox(cat["label"])
                self._checks[cat["id"]] = cb
                v.addWidget(cb)
        miss = missing_sfx_files()
        if miss:
            lab = QLabel("尚未生成音效文件：%s。启动后会尝试自动生成。" % ", ".join(miss))
            lab.setWordWrap(True)
            lab.setObjectName("warnHint")
            v.addWidget(lab)
        else:
            lab = QLabel("音效文件目录：%s" % SFX_DIR)
            lab.setWordWrap(True)
            lab.setObjectName("hint")
            v.addWidget(lab)
        # 0829 修: 这里原来是
        #     inner = QWidget(); inner.setLayout(v)
        # v 已经是 g2 的布局(QVBoxLayout(g2)), 再 setLayout 到 inner 上,
        # Qt 会把整个布局连同里面【所有 checkbox】一起从 g2 抢到 inner 名下。
        # 而 inner 是个局部变量, 既没有父 widget 也没有别的引用 —— 函数一返回
        # 就被回收, C++ 对象连带所有 checkbox 一起析构。
        # 于是紧接着的 _load_to_ui() 去读 self._checks 里那些勾选框, 就报
        #     wrapped C/C++ object of type QCheckBox has been deleted
        # 对话框整个开不起来 (异常被 open_audio_fx_dialog 兜住, 弹成"音效"警告框);
        # 顺带 g2 的布局被抢走, 就算不报错那一栏也是空的。
        # 而且当初建的 scroll 从头到尾没被用上, 滚动条根本没生效。
        # 正确写法: 把 g2 整个放进滚动区, 布局和 checkbox 的归属一路都不动。
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(g2)
        lay.addWidget(scroll)
        return w

    def _tab_ai(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        hint = QLabel(
            "用带视觉的模型看视频画面，自动勾选该铺哪类音效。"
            "大型演讲/祷告现场会倾向鼓掌、Amen、大厅同期声。"
            "需要模型本身支持图像输入。"
        )
        hint.setWordWrap(True)
        hint.setObjectName("hint")
        lay.addWidget(hint)

        from cutora.ui.widgets.collapsible import labeled_toggle
        self.chk_ai_host, self.chk_ai = labeled_toggle(
            "启用 AI 自动识别（点下面的按钮才会分析，不会在后台偷偷调用）")
        lay.addWidget(self.chk_ai_host)

        g = QGroupBox("接口")
        f = QFormLayout(g)
        self.cmb_provider = QComboBox()
        self.cmb_provider.addItem("Gemini（Google，视觉）", "gemini")
        self.cmb_provider.addItem("Ollama 本地（llava / qwen2.5vl 等）", "ollama")
        self.cmb_provider.addItem("SpaceXAI / xAI（grok 视觉）", "spacexai")
        self.cmb_provider.addItem("OpenAI", "openai")
        self.cmb_provider.addItem("Anthropic", "anthropic")
        f.addRow("提供方:", self.cmb_provider)

        key_hint = QLabel("密钥与联网开关在「设置 → AI 提供商」中管理，不会写入设置文件。")
        key_hint.setWordWrap(True)
        key_hint.setObjectName("hint")
        f.addRow(key_hint)

        self.ed_gemini_model = QLineEdit()
        self.ed_gemini_model.setPlaceholderText("gemini-2.0-flash")
        f.addRow("Gemini 模型:", self.ed_gemini_model)

        self.ed_ollama_model = QLineEdit()
        self.ed_ollama_model.setPlaceholderText("llava")
        f.addRow("Ollama 视觉模型:", self.ed_ollama_model)

        self.ed_xai_model = QLineEdit()
        self.ed_xai_model.setPlaceholderText("grok-2-vision-1212")
        f.addRow("SpaceXAI 模型:", self.ed_xai_model)
        lay.addWidget(g)

        row = QHBoxLayout()
        self.btn_ai_cur = QPushButton("AI 分析当前视频并铺音效")
        self.btn_ai_all = QPushButton("AI 分析全部视频并铺音效")
        self.btn_ai_cur.clicked.connect(lambda: self._run_ai(False))
        self.btn_ai_all.clicked.connect(lambda: self._run_ai(True))
        row.addWidget(self.btn_ai_cur)
        row.addWidget(self.btn_ai_all)
        lay.addLayout(row)
        self.lbl_ai = QLabel("")
        self.lbl_ai.setWordWrap(True)
        self.lbl_ai.setObjectName("okHint")
        lay.addWidget(self.lbl_ai)
        lay.addStretch(1)
        return w

    def _load_to_ui(self):
        fx = self.fx
        self.chk_spatial.setChecked(bool(fx.get("spatial_enabled", True)))
        preset = fx.get("spatial_preset") or "stage"
        idx = self.cmb_preset.findData(preset)
        self.cmb_preset.setCurrentIndex(max(0, idx))
        self.sp_wet.setValue(float(fx.get("spatial_wet", 0.26)))
        self.sp_width.setValue(float(fx.get("spatial_width", 0.62)))
        self.chk_mic.setChecked(bool(fx.get("mic_texture", False)))
        self.sp_mic.setValue(float(fx.get("mic_amount", 0.5)))
        self.sp_sfx_vol.setValue(float(fx.get("sfx_volume", 0.18)))
        self.sp_interval.setValue(float(fx.get("sfx_interval_sec", 30.0)))
        self.sp_burst.setValue(float(fx.get("sfx_duration_sec", 5.0)))
        self.sp_jitter.setValue(float(fx.get("sfx_jitter_sec", 8.0)))
        self.chk_auto.setChecked(bool(fx.get("sfx_auto_apply", True)))
        en = fx.get("sfx_enabled") or {}
        for cid, cb in self._checks.items():
            default_on = next((c["default_enabled"] for c in SFX_CATALOG if c["id"] == cid), False)
            cb.setChecked(bool(en.get(cid, default_on)))
        self.chk_ai.setChecked(bool(fx.get("ai_enabled", False)))
        pidx = self.cmb_provider.findData(fx.get("ai_provider") or "gemini")
        self.cmb_provider.setCurrentIndex(max(0, pidx))
        self.ed_gemini_model.setText(str(fx.get("gemini_model") or "gemini-2.0-flash"))
        self.ed_ollama_model.setText(str(fx.get("ollama_model") or "llava"))
        self.ed_xai_model.setText(str(fx.get("xai_model") or "grok-2-vision-1212"))

    def collect_settings(self):
        fx = dict(self.fx)
        fx["spatial_enabled"] = self.chk_spatial.isChecked()
        fx["spatial_preset"] = self.cmb_preset.currentData() or "stage"
        fx["spatial_wet"] = float(self.sp_wet.value())
        fx["spatial_width"] = float(self.sp_width.value())
        fx["mic_texture"] = self.chk_mic.isChecked()
        fx["mic_amount"] = float(self.sp_mic.value())
        fx["sfx_volume"] = float(self.sp_sfx_vol.value())
        fx["sfx_interval_sec"] = float(self.sp_interval.value())
        fx["sfx_duration_sec"] = float(self.sp_burst.value())
        fx["sfx_jitter_sec"] = float(self.sp_jitter.value())
        fx["sfx_auto_apply"] = self.chk_auto.isChecked()
        fx["sfx_enabled"] = {cid: cb.isChecked() for cid, cb in self._checks.items()}
        fx["ai_enabled"] = self.chk_ai.isChecked()
        fx["ai_provider"] = self.cmb_provider.currentData() or "gemini"
        fx["gemini_model"] = self.ed_gemini_model.text().strip() or "gemini-2.0-flash"
        fx["ollama_model"] = self.ed_ollama_model.text().strip() or "llava"
        fx["xai_model"] = self.ed_xai_model.text().strip() or "grok-2-vision-1212"
        fx.pop("gemini_api_key", None)
        fx.pop("xai_api_key", None)
        fx.pop("ollama_host", None)
        self.fx = fx
        return fx

    def _on_save(self):
        save_fx_settings(self.collect_settings())
        self.accept()

    def _emit_apply(self, all_tasks):
        save_fx_settings(self.collect_settings())
        self.apply_all_requested = bool(all_tasks)
        self.apply_current_requested = not all_tasks
        self.accept()

    def _parent_win(self):
        w = self.parent()
        return w

    def _run_ai(self, all_tasks):
        win = self._parent_win()
        if win is None or not getattr(win, "tasks", None):
            QMessageBox.information(self, "提示", "请先导入视频")
            return
        fx = self.collect_settings()
        save_fx_settings(fx)
        items = []
        if all_tasks:
            for i, t in enumerate(win.tasks):
                items.append((i, t.get("video") or ""))
        else:
            idx = getattr(win, "curr_idx", -1)
            if idx < 0 or idx >= len(win.tasks):
                QMessageBox.information(self, "提示", "请先选中一条视频")
                return
            items = [(idx, win.tasks[idx].get("video") or "")]
        self.lbl_ai.setText("正在抽帧并调用 AI…")
        self.btn_ai_cur.setEnabled(False)
        self.btn_ai_all.setEnabled(False)
        self._ai_worker = _AiWorker(items, fx, self)
        self._ai_worker.one_done.connect(self._on_ai_one)
        self._ai_worker.all_done.connect(self._on_ai_all)
        self._ai_worker.start()

    def _on_ai_one(self, idx, result):
        win = self._parent_win()
        if win is None:
            return
        from cutora.audio.sfx_ai import apply_ai_result_to_settings
        fx = apply_ai_result_to_settings(self.collect_settings(), result)
        # 只把 AI 选中的音效铺到这一条，不改全局勾选（避免一条户外片关掉全部鼓掌）
        per = dict(fx)
        if result.get("ok") and result.get("sfx"):
            per["sfx_enabled"] = {c["id"]: (c["id"] in result["sfx"]) for c in SFX_CATALOG}
            if 0 <= idx < len(win.tasks):
                place_sfx_on_task(win.tasks[idx], per)
        reason = result.get("reason") or ""
        scene = result.get("scene") or ""
        sfx = ", ".join(result.get("sfx") or [])
        ok = "OK" if result.get("ok") else "失败"
        self.lbl_ai.setText(f"#{idx+1} {ok}  场景={scene}  音效={sfx}  {reason}")

    def _on_ai_all(self):
        self.btn_ai_cur.setEnabled(True)
        self.btn_ai_all.setEnabled(True)
        win = self._parent_win()
        if win is not None:
            try:
                win.timeline.update()
                win.sync_ui()
            except Exception:
                pass
        self.lbl_ai.setText(self.lbl_ai.text() + "\n分析结束。可再点保存，或应用到其它视频。")
