# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""剪气口（DaVinci Resolve Studio / Descript 级智能停顿裁剪与自检）交互对话框。"""
from __future__ import annotations

import os
import tempfile

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from cutora.audio.silence_cut import (
    UNIT_LABELS,
    UNITS,
    AudioCache,
    SilenceParams,
    analyze,
    apply_plan,
    envelope,
    plan_task,
    preview_wave,
    verify_cut,
    write_wav,
)


class SilenceWavePreview(QWidget):
    """显示选中气口附近的波形包络、原停顿区间与红色剪除区域。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(76)
        self._bins: list[float] = []
        self._win_start = 0.0
        self._win_end = 1.0
        self._pause_start = 0.0
        self._pause_end = 0.0
        self._cut_start = 0.0
        self._cut_end = 0.0

    def set_cut_view(self, sig, cut: dict, pad: float = 1.5) -> None:
        if sig is None or not cut:
            self._bins = []
            self.update()
            return
        p0 = float(cut.get("pause_start") or cut.get("start") or 0.0)
        p1 = float(cut.get("pause_end") or cut.get("end") or p0)
        c0 = float(cut.get("start") or p0)
        c1 = float(cut.get("end") or p1)
        w0 = max(0.0, p0 - pad)
        w1 = max(w0 + 0.2, p1 + pad)
        self._win_start = w0
        self._win_end = w1
        self._pause_start = p0
        self._pause_end = p1
        self._cut_start = c0
        self._cut_end = c1
        self._bins = envelope(sig, w0, w1, bins=160)
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = self.width()
        h = self.height()
        p.fillRect(0, 0, w, h, QColor("#161920"))
        if not self._bins or self._win_end <= self._win_start:
            p.setPen(QColor("#788090"))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "点击表格中的任一停顿行查看前后波形与切点保护区")
            return

        span = self._win_end - self._win_start

        def _x(t: float) -> int:
            return int(round((t - self._win_start) / span * w))

        px0, px1 = _x(self._pause_start), _x(self._pause_end)
        cx0, cx1 = _x(self._cut_start), _x(self._cut_end)
        # 绿色保留保护区 (head / tail)
        p.fillRect(px0, 0, max(0, cx0 - px0), h, QColor(46, 160, 67, 55))
        p.fillRect(cx1, 0, max(0, px1 - cx1), h, QColor(46, 160, 67, 55))
        # 红色剪除区
        p.fillRect(cx0, 0, max(1, cx1 - cx0), h, QColor(229, 83, 75, 85))

        # 波形柱
        mid = h * 0.5
        n = len(self._bins)
        bar_w = max(1.0, w / float(n))
        p.setPen(Qt.PenStyle.NoPen)
        for i, val in enumerate(self._bins):
            bx = int(i * bar_w)
            bh = max(2, int(min(1.0, val * 1.8) * (h - 14)))
            in_cut = cx0 <= bx <= cx1
            p.setBrush(QColor("#E5534B") if in_cut else QColor("#58A6FF"))
            p.drawRect(bx, int(mid - bh * 0.5), max(1, int(bar_w - 1)), bh)

        p.setPen(QPen(QColor("#E5534B"), 1.5))
        p.drawLine(cx0, 0, cx0, h)
        p.drawLine(cx1, 0, cx1, h)


class SilenceCutDialog(QDialog):
    """剪气口对话框：支持单条与全工程批量检测、逐刀勾选、前后试听及无损自检。"""

    def __init__(self, host, parent=None, *, batch_default: bool = False):
        super().__init__(parent or host)
        self.host = host
        self.setWindowTitle("剪气口（智能停顿裁剪与无损自检）")
        self.resize(980, 680)
        self._cache = AudioCache()
        self._plans: dict[int, dict] = {}
        self._analyses: dict[int, object] = {}
        self._words_before: dict[int, list] = {}
        self._row_meta: list[tuple[int, str, dict]] = []
        self._player = None
        self._audio_out = None
        self._tmp_wavs: list[str] = []

        self._build_ui(batch_default=batch_default)
        self.run_detect()

    def _build_ui(self, *, batch_default: bool = False) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(10)

        # 顶部参数区
        grp = QGroupBox("剪气口参数设定（三路融合防误伤：ASR 词时间戳 + Silero VAD + 10ms 自适应能量包络）")
        top = QVBoxLayout(grp)

        row1 = QHBoxLayout()
        row1.addWidget(QLabel("作用范围："))
        self.cb_scope = QComboBox()
        self.cb_scope.addItem("当前选中视频", "current")
        self.cb_scope.addItem("全部视频任务（批量剪气口）", "all")
        if batch_default:
            self.cb_scope.setCurrentIndex(1)
        row1.addWidget(self.cb_scope)

        row1.addSpacing(14)
        row1.addWidget(QLabel("当两句间隔超过："))
        self.sp_thr = QDoubleSpinBox()
        self.sp_thr.setRange(0.05, 3600.0)
        self.sp_thr.setDecimals(2)
        self.sp_thr.setSingleStep(0.1)
        self.sp_thr.setValue(1.0)
        row1.addWidget(self.sp_thr)

        self.cb_thr_unit = QComboBox()
        for u in UNITS:
            self.cb_thr_unit.addItem(f"{UNIT_LABELS[u]} ({u})", u)
        self.cb_thr_unit.setCurrentIndex(UNITS.index("s"))
        row1.addWidget(self.cb_thr_unit)

        row1.addSpacing(14)
        row1.addWidget(QLabel("裁剪后保留间隔："))
        self.sp_keep = QDoubleSpinBox()
        self.sp_keep.setRange(0.0, 600.0)
        self.sp_keep.setDecimals(2)
        self.sp_keep.setSingleStep(0.05)
        self.sp_keep.setValue(0.25)
        row1.addWidget(self.sp_keep)

        self.cb_keep_unit = QComboBox()
        for u in UNITS:
            self.cb_keep_unit.addItem(f"{UNIT_LABELS[u]} ({u})", u)
        self.cb_keep_unit.setCurrentIndex(UNITS.index("s"))
        row1.addWidget(self.cb_keep_unit)
        row1.addStretch(1)
        top.addLayout(row1)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("句尾保护帧："))
        self.sp_head = QSpinBox()
        self.sp_head.setRange(0, 30)
        self.sp_head.setValue(2)
        self.sp_head.setSuffix(" 帧")
        row2.addWidget(self.sp_head)

        row2.addSpacing(10)
        row2.addWidget(QLabel("句首保护帧："))
        self.sp_tail = QSpinBox()
        self.sp_tail.setRange(0, 30)
        self.sp_tail.setValue(2)
        self.sp_tail.setSuffix(" 帧")
        row2.addWidget(self.sp_tail)

        row2.addSpacing(14)
        self.ck_breaths = QCheckBox("保留自然呼吸/气声（防误伤）")
        self.ck_breaths.setChecked(True)
        row2.addWidget(self.ck_breaths)

        self.ck_vad = QCheckBox("启用 Silero VAD 语音核验")
        self.ck_vad.setChecked(True)
        row2.addWidget(self.ck_vad)

        self.ck_edges = QCheckBox("同时裁剪片头片尾静音")
        self.ck_edges.setChecked(True)
        row2.addWidget(self.ck_edges)

        row2.addStretch(1)
        self.btn_detect = QPushButton("🔍 重新检测停顿")
        self.btn_detect.clicked.connect(self.run_detect)
        row2.addWidget(self.btn_detect)
        top.addLayout(row2)
        root.addWidget(grp)

        # 波形预览 + 试听栏
        wave_bar = QHBoxLayout()
        self.lbl_summary = QLabel("正在等待检测…")
        wave_bar.addWidget(self.lbl_summary, 1)
        self.btn_prev_before = QPushButton("▶ 试听原停顿")
        self.btn_prev_before.clicked.connect(lambda: self.preview_selected(after=False))
        self.btn_prev_after = QPushButton("▶ 试听剪后接缝")
        self.btn_prev_after.clicked.connect(lambda: self.preview_selected(after=True))
        wave_bar.addWidget(self.btn_prev_before)
        wave_bar.addWidget(self.btn_prev_after)
        root.addLayout(wave_bar)

        self.wave_view = SilenceWavePreview(self)
        root.addWidget(self.wave_view)

        # 切点与复核标记表格
        self.table = QTableWidget(0, 7, self)
        self.table.setHorizontalHeaderLabels([
            "执行", "任务", "停顿区间 (秒)", "原停顿", "剪除时长", "剪后间隔", "状态与安全自检说明",
        ])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(6, QHeaderView.ResizeMode.Stretch)
        self.table.itemSelectionChanged.connect(self._on_row_selected)
        self.table.itemChanged.connect(self._on_item_changed)
        root.addWidget(self.table, 1)

        # 底部自检报告与执行按钮
        bot = QHBoxLayout()
        self.lbl_verify = QLabel("提示：绿色为安全气口，黄色为含呼吸/笑声/独立素材的保护提示。")
        self.lbl_verify.setWordWrap(True)
        bot.addWidget(self.lbl_verify, 1)

        self.btn_apply = QPushButton("✂ 执行剪气口并运行无损自检")
        self.btn_apply.setMinimumHeight(34)
        self.btn_apply.clicked.connect(self.run_apply)
        bot.addWidget(self.btn_apply)

        btn_close = QPushButton("关闭")
        btn_close.clicked.connect(self.close)
        bot.addWidget(btn_close)
        root.addLayout(bot)

    def current_params(self) -> SilenceParams:
        return SilenceParams(
            threshold=float(self.sp_thr.value()),
            unit=str(self.cb_thr_unit.currentData() or "s"),
            keep=float(self.sp_keep.value()),
            keep_unit=str(self.cb_keep_unit.currentData() or "s"),
            head_frames=int(self.sp_head.value()),
            tail_frames=int(self.sp_tail.value()),
            keep_breaths=bool(self.ck_breaths.isChecked()),
            use_vad=bool(self.ck_vad.isChecked()),
            trim_edges=bool(self.ck_edges.isChecked()),
            xfade_ms=5.0,
        )

    def target_task_indices(self) -> list[int]:
        tasks = list(getattr(self.host, "tasks", None) or [])
        if not tasks:
            return []
        if self.cb_scope.currentData() == "all":
            return list(range(len(tasks)))
        cur = getattr(self.host, "curr_idx", getattr(self.host, "current_task_index", 0))
        if not isinstance(cur, int) or not (0 <= cur < len(tasks)):
            cur = 0
        return [cur]

    def run_detect(self) -> dict:
        from cutora.audio.silence_cut import collect_words

        tasks = list(getattr(self.host, "tasks", None) or [])
        indices = self.target_task_indices()
        params = self.current_params()
        self._plans.clear()
        self._analyses.clear()
        self._words_before.clear()
        self._row_meta.clear()

        self.table.blockSignals(True)
        self.table.setRowCount(0)
        total_cuts = 0
        total_rem = 0.0
        total_flags = 0

        for idx in indices:
            task = tasks[idx]
            tname = os.path.basename(str(task.get("video") or task.get("path") or f"任务 {idx + 1}"))
            try:
                self._words_before[idx] = collect_words(task)
                an = analyze(task, params, cache=self._cache)
                plan = plan_task(task, params, analysis=an, cache=self._cache, task_index=idx)
                self._analyses[idx] = an
                self._plans[idx] = plan
            except Exception as exc:
                r = self.table.rowCount()
                self.table.insertRow(r)
                self.table.setItem(r, 1, QTableWidgetItem(tname))
                self.table.setItem(r, 6, QTableWidgetItem(f"检测失败: {exc}"))
                self._row_meta.append((idx, "error", {}))
                continue

            for cut in plan.get("cuts") or []:
                r = self.table.rowCount()
                self.table.insertRow(r)
                chk = QTableWidgetItem("")
                chk.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsUserCheckable)
                chk.setCheckState(Qt.CheckState.Checked if cut.get("enabled", True) else Qt.CheckState.Unchecked)
                self.table.setItem(r, 0, chk)
                self.table.setItem(r, 1, QTableWidgetItem(f"#{idx + 1} {tname}"))
                self.table.setItem(r, 2, QTableWidgetItem(f"{cut['pause_start']:.2f}s – {cut['pause_end']:.2f}s"))
                self.table.setItem(r, 3, QTableWidgetItem(f"{cut['pause_s']:.2f}s"))
                self.table.setItem(r, 4, QTableWidgetItem(f"-{cut['remove_s']:.2f}s ({cut['remove_frames']}帧)"))
                self.table.setItem(r, 5, QTableWidgetItem(f"{cut['result_s']:.2f}s"))
                note = cut.get("note") or "✓ 三路信号均确认为纯静音气口，安全裁剪"
                it_note = QTableWidgetItem(note)
                if cut.get("status") == "warn":
                    it_note.setForeground(QColor("#E5A93C"))
                else:
                    it_note.setForeground(QColor("#56D364"))
                self.table.setItem(r, 6, it_note)
                self._row_meta.append((idx, "cut", cut))
                if cut.get("enabled", True):
                    total_cuts += 1
                    total_rem += float(cut["remove_s"])

            for fl in plan.get("flags") or []:
                r = self.table.rowCount()
                self.table.insertRow(r)
                dash = QTableWidgetItem("保护")
                dash.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                self.table.setItem(r, 0, dash)
                self.table.setItem(r, 1, QTableWidgetItem(f"#{idx + 1} {tname}"))
                self.table.setItem(r, 2, QTableWidgetItem(f"{fl['start']:.2f}s – {fl['end']:.2f}s"))
                self.table.setItem(r, 3, QTableWidgetItem(f"{fl['gap_s']:.2f}s"))
                self.table.setItem(r, 4, QTableWidgetItem("0.00s (保护不剪)"))
                self.table.setItem(r, 5, QTableWidgetItem(f"{fl.get('remain_s', fl['gap_s']):.2f}s"))
                it_fl = QTableWidgetItem(f"⚠ {fl.get('note') or ''}")
                it_fl.setForeground(QColor("#E5A93C"))
                self.table.setItem(r, 6, it_fl)
                self._row_meta.append((idx, "flag", fl))
                total_flags += 1

        self.table.blockSignals(False)
        self.lbl_summary.setText(
            f"共扫描 {len(indices)} 条视频 · 检出可剪气口 {total_cuts} 处（预计缩短 {total_rem:.2f} 秒）· 防误伤保护提示 {total_flags} 处"
        )
        if self.table.rowCount() > 0:
            self.table.selectRow(0)
        return {"count": len(indices), "total_cuts": total_cuts, "total_removed_s": total_rem, "total_flags": total_flags}

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if item is None or item.column() != 0:
            return
        row = item.row()
        if not (0 <= row < len(self._row_meta)):
            return
        _idx, kind, data = self._row_meta[row]
        if kind == "cut":
            data["enabled"] = item.checkState() == Qt.CheckState.Checked

    def _on_row_selected(self) -> None:
        row = self.table.currentRow()
        if not (0 <= row < len(self._row_meta)):
            return
        idx, _kind, data = self._row_meta[row]
        an = self._analyses.get(idx)
        if an is not None:
            self.wave_view.set_cut_view(an.sig, data)

    def preview_selected(self, *, after: bool = False) -> None:
        row = self.table.currentRow()
        if not (0 <= row < len(self._row_meta)):
            return
        idx, kind, data = self._row_meta[row]
        an = self._analyses.get(idx)
        if an is None:
            return
        t0 = float(data.get("pause_start") or data.get("start") or 0.0)
        t1 = float(data.get("pause_end") or data.get("end") or t0)
        cut_arg = data if (after and kind == "cut") else None
        wav_arr = preview_wave(an.sig, t0, t1, applied_cut=cut_arg, pad=1.8)
        fd, path = tempfile.mkstemp(suffix=".wav", prefix="cutora_silence_prev_")
        os.close(fd)
        write_wav(path, wav_arr)
        self._tmp_wavs.append(path)
        try:
            from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
            if self._player is None:
                self._player = QMediaPlayer(self)
                self._audio_out = QAudioOutput(self)
                self._player.setAudioOutput(self._audio_out)
            self._player.stop()
            self._player.setSource(QUrl.fromLocalFile(path))
            self._player.play()
        except Exception:
            pass

    def run_apply(self) -> dict:
        tasks = list(getattr(self.host, "tasks", None) or [])
        params = self.current_params()
        if not self._plans:
            self.run_detect()

        undo = getattr(self.host, "push_undo", None) or getattr(self.host, "_push_undo_snapshot", None)
        if callable(undo):
            try:
                undo("剪气口")
            except TypeError:
                try:
                    undo()
                except Exception:
                    pass
            except Exception:
                pass

        applied_tasks = 0
        total_cuts = 0
        total_rem = 0.0
        verify_ok = 0
        max_db_err = 0.0
        worst_pause = 0.0

        for idx, plan in list(self._plans.items()):
            if not (0 <= idx < len(tasks)):
                continue
            task = tasks[idx]
            an_before = self._analyses.get(idx)
            w_before = self._words_before.get(idx)
            res = apply_plan(task, plan, force=True)
            if res["n_cuts"] > 0:
                applied_tasks += 1
                total_cuts += res["n_cuts"]
                total_rem += res["removed_s"]
            ver = verify_cut(
                task,
                res["applied_cuts"],
                words_before=w_before,
                sig_before=an_before.sig if an_before is not None else None,
                floor_db=an_before.floor_db if an_before is not None else -70.0,
                params=params,
                cache=self._cache,
            )
            if ver["ok"]:
                verify_ok += 1
            max_db_err = max(max_db_err, float(ver["audio_check"]["max_db_diff"]))
            worst_pause = max(worst_pause, float(ver["longest_pause_after_s"]))

        msg = (
            f"✓ 已对 {applied_tasks} 条视频完成剪气口（共剪除 {total_cuts} 处停顿，累计缩短 {total_rem:.2f} 秒）。\n"
            f"✓ 自检结果：{verify_ok}/{len(self._plans)} 条通过词级与包络无损核验（最大包络偏差 {max_db_err:.2f} dB，剪后最长静音 {worst_pause:.2f}s）。"
        )
        self.lbl_verify.setText(msg)
        for fn_name in ("_refresh_after_timeline_edit", "update_timeline_ui", "update_preview"):
            fn = getattr(self.host, fn_name, None)
            if callable(fn):
                try:
                    fn()
                except Exception:
                    pass
        rebuild = getattr(self.host, "_rebuild_preview_wav", None)
        if callable(rebuild):
            try:
                rebuild(immediate=False)
            except Exception:
                pass
        self.run_detect()
        return {
            "applied_tasks": applied_tasks,
            "total_cuts": total_cuts,
            "total_removed_s": round(total_rem, 3),
            "verify_ok": verify_ok,
        }

    def closeEvent(self, event) -> None:
        if self._player is not None:
            try:
                self._player.stop()
            except Exception:
                pass
        for p in self._tmp_wavs:
            try:
                if os.path.isfile(p):
                    os.remove(p)
            except OSError:
                pass
        super().closeEvent(event)


def open_silence_cut_dialog(host, *, batch_default: bool = False) -> SilenceCutDialog:
    dlg = SilenceCutDialog(host, parent=host, batch_default=batch_default)
    dlg.exec()
    return dlg
