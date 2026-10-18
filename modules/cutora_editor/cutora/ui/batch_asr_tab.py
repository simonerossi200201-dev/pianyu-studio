# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q162 · 批量加载 → 「语音转文本」分页。

把音频 / 视频里的话转成文字直接导出，不往时间线上加任何东西：

* 来源：左边勾选的媒体库视频，或者自己添加外部文件 / 文件夹（音频也行）。
* 识别：和自动字幕同一个引擎（AlignmentWorker 不给脚本 = 纯识别），语言可选 / 自动检测，模型可选。
* 输出：纯文本 .txt / 带时间戳 .txt / 字幕 .srt，放在源文件旁边或指定文件夹；可再合成一份合集。
  可选把结果同时写进媒体库这条视频的字幕。
* 后台线程逐个跑，有进度、可取消（取消在当前文件识别到下一阶段时生效）。

纯逻辑在 cutora/batch/asr_export.py。
"""
from __future__ import annotations

import os
import time

from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from cutora.batch import asr_export as AX
from cutora.core.log import get_logger
from cutora.ui.theme.tokens import SPACE_2, SPACE_3, SPACE_6

_LOG = get_logger("cutora.ui.batch_asr_tab")
_PREFS_KEY = "batch_asr_prefs"
# 线程对象挂在模块上：对话框关了识别还在跑时别被回收（和 text_locate_asr 一样）
_LIVE: dict = {"thread": None}


class AsrBatchThread(QThread):
    """逐个文件跑识别。transcribe_fn(path, language, asr_dir, on_progress, should_stop) -> segs（测试里可换掉）。"""

    file_started = Signal(int, str)            # 序号, 路径
    file_progress = Signal(int, int)           # 序号, 0–100
    file_done = Signal(int, str, object, str)  # 序号, 路径, 分段 或 None, 错误
    all_done = Signal(int, int)                # 成功数, 失败数

    def __init__(self, paths, *, language=None, asr_dir=None, transcribe_fn=None, parent=None):
        super().__init__(parent)
        self.paths = list(paths)
        self.language = language
        self.asr_dir = asr_dir
        self.transcribe_fn = transcribe_fn or AX.transcribe_file
        self._cancel = False
        self.results: dict = {}

    def cancel(self):
        self._cancel = True

    def is_cancelled(self) -> bool:
        return self._cancel

    def run(self):
        ok = bad = 0
        for i, path in enumerate(self.paths):
            if self._cancel:
                break
            self.file_started.emit(i, path)
            try:
                segs = self.transcribe_fn(
                    path, language=self.language, asr_dir=self.asr_dir,
                    on_progress=lambda p, _i=i: self.file_progress.emit(_i, int(p)),
                    should_stop=self.is_cancelled)
                if self._cancel:
                    break
                self.results[path] = segs
                ok += 1
                self.file_done.emit(i, path, segs, "")
            except Exception as exc:
                if self._cancel:
                    break
                _LOG.exception("语音转文本失败：%s", path)
                bad += 1
                self.file_done.emit(i, path, None, str(exc) or "识别失败")
        self.all_done.emit(ok, bad)


def _load_prefs() -> dict:
    try:
        from cutora.core.history import _load_app_settings
        raw = (_load_app_settings() or {}).get(_PREFS_KEY)
        return dict(raw) if isinstance(raw, dict) else {}
    except Exception:
        return {}


def _save_prefs(d: dict) -> None:
    try:
        from cutora.core.history import _load_app_settings, _save_app_settings
        s = _load_app_settings() or {}
        s[_PREFS_KEY] = dict(d)
        _save_app_settings(s)
    except Exception:
        pass


def _task_path(task) -> str:
    if not isinstance(task, dict):
        return ""
    return str(task.get("video") or task.get("path") or "")


def build_asr_tab(dlg, host, picker) -> QWidget:
    page = QWidget()
    lay = QVBoxLayout(page)
    lay.setContentsMargins(0, SPACE_2, 0, 0)
    lay.setSpacing(SPACE_3)
    prefs = _load_prefs()

    tip = QLabel("把音频 / 视频里说的话转成文字直接导出，不往时间线上加东西。识别用的是自动字幕同一个引擎。")
    tip.setObjectName("hint")
    tip.setWordWrap(True)
    lay.addWidget(tip)

    # ── 来源 ──
    src_row = QHBoxLayout()
    rb_lib = QRadioButton("左边勾选的媒体库视频")
    rb_ext = QRadioButton("外部文件 / 文件夹")
    grp = QButtonGroup(page)
    grp.addButton(rb_lib, 0)
    grp.addButton(rb_ext, 1)
    (rb_ext if prefs.get("source") == "ext" else rb_lib).setChecked(True)
    src_row.addWidget(rb_lib)
    src_row.addWidget(rb_ext)
    src_row.addStretch(1)
    lay.addLayout(src_row)

    ext_host = QWidget()
    ext_lay = QVBoxLayout(ext_host)
    ext_lay.setContentsMargins(0, 0, 0, 0)
    ext_lay.setSpacing(SPACE_2)
    ext_list = QListWidget()
    ext_list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
    ext_list.setMaximumHeight(SPACE_6 * 6)
    ext_lay.addWidget(ext_list)
    ext_btns = QHBoxLayout()
    btn_add = QPushButton("添加文件…")
    btn_add_dir = QPushButton("添加文件夹…")
    btn_del = QPushButton("移除选中")
    btn_clear = QPushButton("清空")
    for b in (btn_add, btn_add_dir, btn_del, btn_clear):
        ext_btns.addWidget(b)
    ext_btns.addStretch(1)
    ext_lay.addLayout(ext_btns)
    lay.addWidget(ext_host)

    def _ext_paths():
        return [ext_list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(ext_list.count())]

    def _add_paths(entries):
        have = {os.path.normcase(os.path.abspath(p)) for p in _ext_paths()}
        for p in AX.list_media(entries):
            k = os.path.normcase(os.path.abspath(p))
            if k in have:
                continue
            have.add(k)
            it = QListWidgetItem(os.path.basename(p))
            it.setToolTip(p)
            it.setData(Qt.ItemDataRole.UserRole, p)
            ext_list.addItem(it)
        _sync_count()

    def _pick_files():
        exts = " ".join("*" + e for e in AX.MEDIA_EXTS)
        paths, _f = QFileDialog.getOpenFileNames(page, "选择音频 / 视频", prefs.get("last_dir", ""),
                                                 "音频 / 视频 (%s)" % exts)
        if paths:
            prefs["last_dir"] = os.path.dirname(paths[0])
            _add_paths(paths)
            rb_ext.setChecked(True)

    def _pick_dir():
        d = QFileDialog.getExistingDirectory(page, "选择文件夹（只看这一层）", prefs.get("last_dir", ""))
        if d:
            prefs["last_dir"] = d
            _add_paths([d])
            rb_ext.setChecked(True)

    def _del_sel():
        for it in ext_list.selectedItems():
            ext_list.takeItem(ext_list.row(it))
        _sync_count()

    btn_add.clicked.connect(_pick_files)
    btn_add_dir.clicked.connect(_pick_dir)
    btn_del.clicked.connect(_del_sel)
    btn_clear.clicked.connect(lambda: (ext_list.clear(), _sync_count()))

    # ── 识别 ──
    opt_row = QHBoxLayout()
    opt_row.addWidget(QLabel("语言"))
    cb_lang = QComboBox()
    try:
        from cutora.asr.languages import picker_entries
        for ent in picker_entries():
            cb_lang.addItem(ent["label"], ent["code"])
            if ent.get("tooltip"):
                cb_lang.setItemData(cb_lang.count() - 1, ent["tooltip"], Qt.ItemDataRole.ToolTipRole)
    except Exception:
        cb_lang.addItem("自动检测", "auto")
    i = cb_lang.findData(prefs.get("language") or "auto")
    cb_lang.setCurrentIndex(max(0, i))
    opt_row.addWidget(cb_lang)
    opt_row.addStretch(1)
    lay.addLayout(opt_row)
    opt_row = QHBoxLayout()
    opt_row.addWidget(QLabel("模型"))
    cb_model = QComboBox()
    try:
        from cutora.asr.models import list_status
        for row in list_status(fast=True):     # Q173：打开对话框不在界面线程里校验几个 GB 的权重
            if not str(row.get("id", "")).startswith("asr-"):
                continue
            ready = row.get("status") == "ready"
            label = "%s%s" % (row.get("title_zh") or row.get("id"), "" if ready else "（未下载）")
            cb_model.addItem(label, row.get("id"))
            cb_model.setItemData(cb_model.count() - 1, ready, Qt.ItemDataRole.UserRole + 1)
    except Exception:
        _LOG.exception("列模型失败")
    if cb_model.count() == 0:
        cb_model.addItem("默认", None)
    want_model = prefs.get("model") or getattr(host, "_run_asr_id", None) or "asr-small"
    i = cb_model.findData(want_model)
    cb_model.setCurrentIndex(max(0, i))
    opt_row.addWidget(cb_model)
    opt_row.addStretch(1)
    lay.addLayout(opt_row)

    # ── 输出 ──
    fmt_row = QHBoxLayout()
    fmt_row.addWidget(QLabel("导出"))
    ck_fmt = {}
    saved_fmts = prefs.get("formats") or ["txt"]
    for key in AX.FORMATS:
        ck = QCheckBox(AX.FORMAT_LABELS[key])
        ck.setChecked(key in saved_fmts)
        ck_fmt[key] = ck
        fmt_row.addWidget(ck)
    fmt_row.addStretch(1)
    lay.addLayout(fmt_row)
    ck_merge = QCheckBox("再合成一份合集 .txt（每个文件一个标题，按顺序接起来）")
    ck_merge.setChecked(bool(prefs.get("merge")))
    lay.addWidget(ck_merge)

    dest_row = QHBoxLayout()
    rb_beside = QRadioButton("放在源文件旁边")
    rb_folder = QRadioButton("放到文件夹：")
    grp2 = QButtonGroup(page)
    grp2.addButton(rb_beside, 0)
    grp2.addButton(rb_folder, 1)
    (rb_folder if prefs.get("dest_mode") == "folder" else rb_beside).setChecked(True)
    ed_dest = QLineEdit(prefs.get("dest_dir") or "")
    ed_dest.setPlaceholderText("输出文件夹")
    btn_dest = QPushButton("浏览…")

    def _pick_dest():
        d = QFileDialog.getExistingDirectory(page, "输出文件夹", ed_dest.text() or prefs.get("last_dir", ""))
        if d:
            ed_dest.setText(d)
            rb_folder.setChecked(True)
    btn_dest.clicked.connect(_pick_dest)
    dest_row.addWidget(rb_beside)
    dest_row.addWidget(rb_folder)
    dest_row.addWidget(ed_dest, 1)
    dest_row.addWidget(btn_dest)
    lay.addLayout(dest_row)

    ck_into_task = QCheckBox("同时写进媒体库这条视频的字幕（替换现有字幕）")
    ck_into_task.setChecked(bool(prefs.get("into_task")))
    ck_into_task.setToolTip("只对媒体库里的视频有效；外部文件只导出文件。")
    lay.addWidget(ck_into_task)

    # ── 进度 / 结果 ──
    prog = QProgressBar()
    prog.setTextVisible(True)
    prog.setFormat("%v / %m")
    prog.hide()
    lay.addWidget(prog)
    msg = QLabel("")
    msg.setObjectName("hint")
    msg.setWordWrap(True)
    lay.addWidget(msg)
    results = QListWidget()
    results.setMaximumHeight(SPACE_6 * 6)
    lay.addWidget(results)
    act_row = QHBoxLayout()
    btn_cancel = QPushButton("取消")
    btn_cancel.setEnabled(False)
    btn_open = QPushButton("打开输出文件夹")
    btn_open.setEnabled(False)
    act_row.addWidget(btn_cancel)
    act_row.addWidget(btn_open)
    act_row.addStretch(1)
    lay.addLayout(act_row)
    lay.addStretch(1)

    state = {"thread": None, "written": [], "dest_dirs": [], "items": [], "t0": 0.0, "paths": []}

    def _sync_count():
        ext_host.setVisible(rb_ext.isChecked())

    rb_lib.toggled.connect(lambda _on: _sync_count())
    rb_ext.toggled.connect(lambda _on: _sync_count())

    def selected_paths() -> list:
        if rb_ext.isChecked():
            return list(_ext_paths())
        out = []
        for t in picker.selected_tasks() or []:
            p = _task_path(t)
            if p and os.path.isfile(p):
                out.append(p)
        return out

    def formats() -> list:
        return [k for k in AX.FORMATS if ck_fmt[k].isChecked()]

    def dest_dir():
        if rb_folder.isChecked():
            d = ed_dest.text().strip()
            return d or None
        return None

    def _remember():
        prefs.update({
            "source": "ext" if rb_ext.isChecked() else "lib",
            "language": cb_lang.currentData(), "model": cb_model.currentData(),
            "formats": formats(), "merge": ck_merge.isChecked(),
            "dest_mode": "folder" if rb_folder.isChecked() else "beside", "dest_dir": ed_dest.text().strip(),
            "into_task": ck_into_task.isChecked(),
        })
        _save_prefs(prefs)

    def _log(line):
        fn = getattr(dlg, "log", None)
        if callable(fn):
            try:
                fn(line)
            except Exception:
                pass

    def busy() -> bool:
        th = state["thread"]
        return th is not None and th.isRunning()

    def _asr_dir():
        mid = cb_model.currentData()
        if not mid:
            return None
        try:
            from cutora.asr.models import get_model, model_folder
            row = get_model(mid)
            return model_folder(row) if row else None
        except Exception:
            return None

    def _model_ready() -> bool:
        idx = cb_model.currentIndex()
        if idx < 0:
            return True
        ready = cb_model.itemData(idx, Qt.ItemDataRole.UserRole + 1)
        return ready is None or bool(ready)

    def start(*, transcribe_fn=None) -> bool:
        """开始转写。返回 False = 没开始（没文件 / 正在跑 / 模型没下载）。"""
        from PySide6.QtWidgets import QMessageBox
        if busy():
            msg.setText("正在转写，先等它跑完或点「取消」。")
            return False
        paths = selected_paths()
        if not paths:
            QMessageBox.information(dlg, "语音转文本", "先勾选左边的视频，或者添加外部音频 / 视频文件。")
            return False
        from cutora.ui.gpu_notice import confirm as _gpu_ok
        if transcribe_fn is None and not _gpu_ok(dlg, "batch_asr"):    # Q193
            return False
        if not formats() and not ck_merge.isChecked() and not ck_into_task.isChecked():
            QMessageBox.information(dlg, "语音转文本", "至少勾一种导出格式。")
            return False
        if rb_folder.isChecked() and not dest_dir():
            QMessageBox.information(dlg, "语音转文本", "选一个输出文件夹，或改成「放在源文件旁边」。")
            return False
        if transcribe_fn is None and not _model_ready():
            ensure = getattr(host, "_ensure_asr_model", None)
            if callable(ensure):
                try:
                    host._run_asr_id = cb_model.currentData()
                except Exception:
                    pass
                if not ensure():
                    return False
            else:
                QMessageBox.information(dlg, "语音转文本", "这个模型还没下载，先在「AI 模型」里下载。")
                return False
        _remember()
        lang = cb_lang.currentData()
        state.update(written=[], dest_dirs=[], items=[], t0=time.monotonic(), paths=list(paths))
        results.clear()
        prog.setRange(0, len(paths))
        prog.setValue(0)
        prog.show()
        msg.setText("准备识别 %d 个文件…" % len(paths))
        btn_cancel.setEnabled(True)
        btn_open.setEnabled(False)
        th = AsrBatchThread(paths, language=None if lang in (None, "auto") else lang, asr_dir=_asr_dir(),
                            transcribe_fn=transcribe_fn)
        th.file_started.connect(_on_started)
        th.file_progress.connect(_on_progress)
        th.file_done.connect(_on_done)
        th.all_done.connect(_on_all_done)
        state["thread"] = th
        _LIVE["thread"] = th
        _log("==== 语音转文本：%d 个文件 ====" % len(paths))
        try:
            dlg.btn_run.setEnabled(False)
        except Exception:
            pass
        th.start()
        return True

    def cancel():
        th = state["thread"]
        if th is not None and th.isRunning():
            th.cancel()
            msg.setText("正在取消…（当前文件识别到下一阶段时停下）")
            btn_cancel.setEnabled(False)

    def _on_started(i, path):
        msg.setText("正在识别 %d / %d：%s" % (i + 1, prog.maximum(), os.path.basename(path)))

    def _on_progress(i, p):
        msg.setText("正在识别 %d / %d：%s  %d%%" % (i + 1, prog.maximum(), os.path.basename(state["paths"][i]), p))

    def _on_done(i, path, segs, err):
        prog.setValue(i + 1)
        if err or segs is None:
            results.addItem("✘ %s：%s" % (os.path.basename(path), err or "没有结果"))
            _log("  ✘ %s：%s" % (os.path.basename(path), err))
            return
        try:
            written = AX.write_outputs(path, segs, dest_dir(), formats())
        except Exception as exc:
            _LOG.exception("写文件失败")
            results.addItem("✘ %s：写文件失败 %s" % (os.path.basename(path), exc))
            return
        state["written"].extend(written.values())
        for p in written.values():
            d = os.path.dirname(p)
            if d not in state["dest_dirs"]:
                state["dest_dirs"].append(d)
        state["items"].append((path, segs))
        n_words = sum(len(s.get("words") or []) for s in segs if isinstance(s, dict))
        results.addItem("✔ %s：%d 句%s → %s" % (
            os.path.basename(path), len(segs), ("、%d 词" % n_words) if n_words else "",
            "、".join(os.path.basename(p) for p in written.values()) or "（只写进字幕）"))
        _log("  ✔ %s：%d 句 → %s" % (os.path.basename(path), len(segs), ", ".join(written.values())))
        if ck_into_task.isChecked() and not rb_ext.isChecked():
            _write_into_task(path, segs)

    def _write_into_task(path, segs):
        key = os.path.normcase(os.path.abspath(path))
        for t in picker.selected_tasks() or []:
            if os.path.normcase(os.path.abspath(_task_path(t) or "")) != key:
                continue
            try:
                rows = []
                for s in segs:
                    if not isinstance(s, dict) or s.get("is_break"):
                        continue
                    r = dict(s)
                    r.setdefault("role", "default")
                    rows.append(r)
                from cutora.subtitle.track_source import set_segments
                set_segments(t, rows)
            except Exception:
                _LOG.exception("写进字幕失败")
                try:
                    t["segments"] = rows
                except Exception:
                    pass
            try:
                if hasattr(host, "mark_dirty"):
                    host.mark_dirty()
            except Exception:
                pass

    def _on_all_done(ok, bad):
        th = state["thread"]
        cancelled = bool(th is not None and th.is_cancelled())
        merged = None
        if ok and ck_merge.isChecked() and state["items"]:
            try:
                merged = AX.merged_path(dest_dir(), state["items"][0][0])
                os.makedirs(os.path.dirname(merged) or ".", exist_ok=True)
                with open(merged, "w", encoding="utf-8", newline="\n") as fh:
                    fh.write(AX.merged_text(state["items"], timestamps=("txt_time" in formats() and "txt" not in formats())))
                state["written"].append(merged)
                d = os.path.dirname(merged)
                if d not in state["dest_dirs"]:
                    state["dest_dirs"].append(d)
                results.addItem("✔ 合集 → %s" % os.path.basename(merged))
            except Exception as exc:
                _LOG.exception("写合集失败")
                results.addItem("✘ 合集写失败：%s" % exc)
        dt = time.monotonic() - state["t0"]
        msg.setText("%s：成功 %d、失败 %d，用时 %d 秒%s" % (
            "已取消" if cancelled else "完成", ok, bad, int(dt),
            ("，输出在 %s" % "；".join(state["dest_dirs"])) if state["dest_dirs"] else ""))
        _log("  %s：成功 %d、失败 %d" % ("已取消" if cancelled else "完成", ok, bad))
        btn_cancel.setEnabled(False)
        btn_open.setEnabled(bool(state["dest_dirs"]))
        try:
            dlg.btn_run.setEnabled(True)
        except Exception:
            pass
        try:
            if ck_into_task.isChecked() and ok and hasattr(host, "refresh_ui") and getattr(host, "curr_idx", -1) >= 0:
                host.refresh_ui()
        except Exception:
            pass

    def _open_dest():
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        for d in state["dest_dirs"][:1]:
            QDesktopServices.openUrl(QUrl.fromLocalFile(d))

    btn_cancel.clicked.connect(cancel)
    btn_open.clicked.connect(_open_dest)
    ext_host.setVisible(rb_ext.isChecked())

    page.start = start
    page.cancel = cancel
    page.busy = busy
    page.selected_paths = selected_paths
    page.formats = formats
    page.dest_dir = dest_dir
    page.sync_count = _sync_count
    page.rb_ext = rb_ext
    page.rb_lib = rb_lib
    page.ext_list = ext_list
    page.add_paths = _add_paths
    page.cb_lang = cb_lang
    page.cb_model = cb_model
    page.ck_fmt = ck_fmt
    page.ck_merge = ck_merge
    page.ck_into_task = ck_into_task
    page.rb_folder = rb_folder
    page.ed_dest = ed_dest
    page.prog = prog
    page.msg = msg
    page.results = results
    page.state = state
    return page
