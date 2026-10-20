# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Stitch orchestration shared by the dialog and the welcome-page entry.

No widget of its own. Callers pass folders, parameters and scripts, and
listen for progress / log / missing / done / failed. The heavy stitcher
module is imported inside methods so merely importing this file does not
load numpy or whisperx.
"""
from __future__ import annotations

import os

from PySide6.QtCore import QObject, Signal


def assign_outputs(groups, folder_of, base_of, folders):
    """Output paths StitcherDialog._start used to assign inline.

    ``groups`` is already limited to groups that have a script.
    ``out_dir`` is the first folder's ``_stitched`` (summaries live there).
    Each group's own files go to that group's folder.
    """
    folders = [f for f in (folders or []) if f]
    anchor = folders[0] if folders else ""
    out_dirs = {
        k: os.path.join(folder_of.get(k, anchor), "_stitched")
        for k in groups
    }
    out_names = {k: base_of.get(k, k) for k in groups}
    out_dir = os.path.join(anchor, "_stitched") if anchor else ""
    return out_dirs, out_names, out_dir


def plan_job(folders, group_mark, order_mark, group_by_folder, scripts):
    """Scan + the same output paths the dialog computes for a fresh job.

    Returns a dict with groups / out_dirs / out_names / out_dir, plus the
    scan's folder_of, base_of and notes. Groups without a script are left
    out, matching the filter in StitcherDialog._start.
    """
    from cutora.batch.script_stitcher import scan_groups_multi

    folders = [os.path.normpath(f) for f in (folders or []) if f and os.path.isdir(f)]
    seen = []
    for f in folders:
        if f not in seen:
            seen.append(f)
    folders = seen
    groups, folder_of, base_of, notes = scan_groups_multi(
        folders, group_mark or "", order_mark or "",
        group_by_folder=bool(group_by_folder))
    scripts = dict(scripts or {})
    # 【片语工作室联动 #1】若存在未手工粘贴文案的组，自动从 Orante 任务表 (manifest.json / orante.db) 直读文案与顺序锁
    if any(k not in scripts for k in groups):
        try:
            import sys
            from pathlib import Path
            if r"D:\0_LTX" not in sys.path:
                sys.path.insert(0, r"D:\0_LTX")
            from core.table_sync import discover_orante_batch_tasks, write_order_lock_for_cutora
            for fd in folders:
                for t in discover_orante_batch_tasks(Path(fd)):
                    if t.copy_text:
                        scripts.setdefault(t.name, t.copy_text)
                        scripts.setdefault(t.task_id, t.copy_text)
                        if t.task_dir:
                            scripts.setdefault(Path(t.task_dir).name, t.copy_text)
                            write_order_lock_for_cutora(Path(t.task_dir))
            notes.append("⚡ 已自动从 Orante 任务表同步批次文案与分镜顺序锁（免手工粘贴）")
        except Exception:
            pass
    groups = {k: v for k, v in groups.items() if k in scripts}
    out_dirs, out_names, out_dir = assign_outputs(
        groups, folder_of, base_of, folders)
    return {
        "groups": groups,
        "out_dirs": out_dirs,
        "out_names": out_names,
        "out_dir": out_dir,
        "folder_of": folder_of,
        "base_of": base_of,
        "notes": notes,
        "scripts": scripts,
    }


def preflight_continue(parent, rep, log) -> bool:
    """Fatal stops. Blocking asks, and the default answer is No.

    Same boxes and the same log lines the dialog used after a preflight
    report. True means the caller may launch analysis.
    """
    from PySide6.QtWidgets import QMessageBox

    report_dir = os.path.join(rep.folder, "_invalid")
    if rep.fatal:
        box = QMessageBox(parent)
        box.setIcon(QMessageBox.Icon.Critical)
        box.setWindowTitle("素材体检未通过 — 已中止")
        box.setText(
            f"发现 {len(rep.fatal)} 个文件根本不是可用的视频, 拼接已中止。\n\n"
            f"这些文件如果放过去, 结果只会是成品里悄悄少几条 —— "
            f"就像 8 月 23 日那次少掉的 53 和 91。\n\n"
            + (f"坏文件已移入:\n{report_dir}\n\n" if rep.quarantined else "")
            + f"完整报告: {os.path.join(report_dir, 'preflight_report.txt')}")
        box.setDetailedText(rep.text(max_each=10000))
        box.setStandardButtons(QMessageBox.StandardButton.Ok)
        box.exec()
        return False
    if rep.blocking:
        # 若只是跨模型重生导致的组内分辨率/帧率混排（时间线工程会自动按画布缩放），或处于 Orante 自动交接模式且无致命无音轨问题，则自动放行
        only_geo_diff = all(
            getattr(i, "code", "") in ("W_MIXED_RES", "W_MIXED_FPS", "W_GAP")
            for i in rep.blocking
        )
        if only_geo_diff or bool(getattr(parent, "_auto_handoff_mode", False)):
            log("⚡ 体检自动放行 %d 项跨模型分辨率/帧率差异（时间线画布将自动统一缩放）。" % len(rep.blocking))
            return True
        box = QMessageBox(parent)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("素材体检: 有 %d 项需要你确认" % len(rep.blocking))
        box.setText(
            f"文件本身都能读, 但下面 {len(rep.blocking)} 项几乎肯定会让结果不对"
            f"(无音轨 / 编号断号 / 组内分辨率或帧率不一致)。\n\n"
            f"确认这些都是有意为之, 才继续?")
        box.setDetailedText(rep.text(max_each=10000))
        box.setStandardButtons(QMessageBox.StandardButton.Yes
                               | QMessageBox.StandardButton.No)
        box.setDefaultButton(QMessageBox.StandardButton.No)
        if box.exec() != QMessageBox.StandardButton.Yes:
            log("已按你的选择中止。修好后重新开始。")
            return False
        log("你已确认放行上述 %d 项。" % len(rep.blocking))
        return True
    log("体检通过: %d 组 / %d 个片段全部可用 (%.1fs)。"
        % (rep.n_groups, rep.n_clips, rep.elapsed))
    return True


class StitchSession(QObject):
    """Run preflight, then analysis, then (unless stopped) the timeline build.

    ``stop_on_missing`` is for the welcome entry: a missing-script report
    emits ``missing`` and does not continue into the timeline. The dialog
    leaves it off and still asks the user, exactly as before.
    """

    progress = Signal(int)
    log = Signal(str)
    missing = Signal(object)
    done = Signal(object)
    failed = Signal(str)
    preflight_ready = Signal(object)
    analyzed = Signal(object)

    def __init__(self, get_model, get_align, parent=None, stop_on_missing=False):
        super().__init__(parent)
        self.get_model = get_model
        self.get_align = get_align
        self.stop_on_missing = bool(stop_on_missing)
        self.worker = None
        self.enc_worker = None
        self.pf_worker = None
        self.groups = {}
        self.scripts = {}
        self.folder_of = {}
        self.base_of = {}
        self.out_dirs = {}
        self.out_names = {}
        self.out_dir = ""
        self.folders = []
        self.cuts_map = None
        self.missing_map = {}
        self.params = {}
        self._align_fetcher = None

    def set_align_fetcher(self, fn) -> None:
        """``fn(language) -> bool``. True means that language's aligner is ready."""
        self._align_fetcher = fn

    def busy(self) -> bool:
        return bool(
            (self.worker and self.worker.isRunning())
            or (self.enc_worker and self.enc_worker.isRunning())
            or (self.pf_worker and self.pf_worker.isRunning())
        )

    def stop(self) -> None:
        if self.worker is not None and self.worker.isRunning():
            self.worker.stop()
        if self.enc_worker is not None and self.enc_worker.isRunning():
            self.enc_worker.stop()

    def set_job(self, groups, scripts, folder_of, out_dirs, out_names, out_dir, folders):
        self.groups = groups
        self.scripts = dict(scripts or {})
        self.folder_of = dict(folder_of or {})
        self.out_dirs = dict(out_dirs or {})
        self.out_names = dict(out_names or {})
        self.out_dir = out_dir or ""
        self.folders = list(folders or [])

    def start_preflight(self, folder, groups, folder_of, quarantine_bad=True):
        from cutora.batch.script_stitcher import PreflightWorker

        self.groups = groups
        self.folder_of = dict(folder_of or {})
        self.pf_worker = PreflightWorker(
            folder, groups, quarantine_bad,
            parent=self, folder_of=self.folder_of)
        self.pf_worker.log.connect(self.log)
        self.pf_worker.done.connect(self._relay_preflight)
        self.pf_worker.start()
        return self.pf_worker

    def _relay_preflight(self, rep) -> None:
        self.pf_worker = None
        self.preflight_ready.emit(rep)

    def launch_analyze(self, params):
        from cutora.batch.script_stitcher import AnalyzeWorker

        self.params = dict(params or {})
        self.worker = AnalyzeWorker(
            self.groups, self.scripts, self.get_model, self.get_align,
            language=self.params.get("language"),
            tail_pad=self.params.get("tail_pad", 0.35),
            trim_head=bool(self.params.get("trim_head", True)),
            edge_refine=bool(self.params.get("edge_refine", True)),
            out_dir=self.out_dir, out_dirs=self.out_dirs, parent=self,
            ignore_scripture_refs=bool(self.params.get("ignore_scripture_refs", True)))
        self.worker._align_fetcher = self._align_fetcher
        self.worker.need_align.connect(self._on_need_align)
        self.worker.progress.connect(self._on_worker_progress)
        self.worker.log.connect(self.log)
        self.worker.analyzed.connect(self._relay_analyzed)
        self.worker.failed.connect(self.failed)
        self.worker.start()
        return self.worker

    def _on_need_align(self, code: str) -> None:
        ok = False
        try:
            fn = self._align_fetcher
            if callable(fn):
                ok = bool(fn(str(code or "")))
            else:
                self.log.emit("没有下载界面，对齐模型没法在这里下载。")
        except Exception as exc:
            self.log.emit("对齐模型下载失败: %s" % exc)
            ok = False
        worker = self.worker
        if worker is not None:
            worker.align_fetch_finished(bool(ok))

    def _on_worker_progress(self, value) -> None:
        self.progress.emit(int(value * 0.7))

    def _relay_analyzed(self, cuts_map) -> None:
        self.cuts_map = cuts_map
        self.missing_map = getattr(self.worker, "missing_map", None) or {}
        if self.missing_map:
            # 【片语工作室联动 #2】发现缺片/遗漏时，自动定位到 Orante 对应任务的具体分镜并标上「已标记 (mark=True)」与缺词提示
            try:
                import sys
                from pathlib import Path
                if r"D:\0_LTX" not in sys.path:
                    sys.path.insert(0, r"D:\0_LTX")
                from core.table_sync import sync_stitch_missing_to_orante
                res_mark = sync_stitch_missing_to_orante(
                    missing_map=self.missing_map,
                    cuts_map=cuts_map,
                    folder_of=self.folder_of,
                    out_dirs=self.out_dirs,
                )
                marked_tasks = res_mark.get("marked_tasks") or []
                if marked_tasks:
                    detail_str = ", ".join(
                        f"{t['task_id']}({','.join('s' + str(s) for s in t['marked_segments'])})"
                        for t in marked_tasks[:12]
                    )
                    if len(marked_tasks) > 12:
                        detail_str += f" 等共 {len(marked_tasks)} 个任务"
                    self.log.emit(
                        f"⚡ 已自动将缺词/遗漏片段回传 Orante 并完成精准标记: {detail_str}\n"
                        f"👉 请回到 Orante「⑤ 收片 -> 全部任务 -> 已标记」直接一键重生补全！"
                    )
                try:
                    from core.spine_db import SpineDB
                    spine = SpineDB()
                    for grp_key, info in self.missing_map.items():
                        holes = info.get("missing") if isinstance(info, dict) else info
                        grp_folder = Path(self.folder_of.get(grp_key) or (self.folders[0] if self.folders else ""))
                        task_dir = grp_folder.parent if grp_folder.name in ("clips", "片段") else grp_folder
                        tid = str(grp_key).split("-")[0]
                        spine.report_stitch_gaps_to_orante(
                            task_id=tid,
                            task_dir=task_dir,
                            missing_entries=[e if isinstance(e, dict) else {"text": str(e)} for e in (holes or [])],
                        )
                except Exception:
                    pass
            except Exception as exc:
                self.log.emit("缺片信号同步提示: %s" % exc)
            self.missing.emit(self.missing_map)
            if self.stop_on_missing:
                return
        self.analyzed.emit(cuts_map)

    def finish_timeline(self, cuts_map, canvas_wh=None):
        """Build the same timeline payload the dialog builds. No message box."""
        from cutora.batch.script_stitcher import build_timeline_result

        try:
            payload, n_places, asproj, canvas_wh = build_timeline_result(
                cuts_map, self.out_dir, self.scripts or {}, canvas_wh)
        except Exception as exc:
            self.failed.emit("时间线生成失败: %s" % exc)
            return None
        self.progress.emit(100)
        self.log.emit("时间线工程 %d 个片段（未生成中间 mp4，画布 %s）: %s" % (
            n_places,
            ("%dx%d" % canvas_wh) if canvas_wh else "按素材尺寸",
            asproj))
        self.done.emit(payload)
        return payload
