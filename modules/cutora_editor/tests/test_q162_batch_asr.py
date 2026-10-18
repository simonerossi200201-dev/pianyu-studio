# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q162 · 批量加载「语音转文本」：纯文本 / 带时间戳 / srt / 合集的生成；分页接线；后台跑 + 取消。"""
from __future__ import annotations

import os
import sys
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

SEGS = [{"start": 0.5, "end": 2.0, "text": "第一句 话", "words": [{"word": "第一句", "start": 0.5, "end": 1.2}]},
        {"start": 2.5, "end": 4.0, "text": "second line", "words": []},
        {"start": 4.0, "end": 4.0, "text": "", "is_break": True}]


def test_transcript_text_and_outputs(tmp_path):
    from cutora.batch import asr_export as AX
    assert AX.transcript_text(SEGS) == "第一句 话\nsecond line\n"
    assert AX.transcript_text(SEGS, timestamps=True).splitlines()[0] == "[00:00.50 → 00:02.00] 第一句 话"
    src = str(tmp_path / "a.mp4")
    open(src, "wb").write(b"x")
    out = AX.write_outputs(src, SEGS, None, ["txt", "txt_time", "srt"])
    assert set(out) == {"txt", "txt_time", "srt"}
    assert out["txt"] == str(tmp_path / "a.txt") and open(out["txt"], encoding="utf-8").read() == "第一句 话\nsecond line\n"
    assert out["txt_time"].endswith("a.时间轴.txt")
    srt = open(out["srt"], encoding="utf-8").read()
    assert "00:00:00,500 --> 00:00:02,000" in srt and "second line" in srt
    dest = tmp_path / "out"
    out2 = AX.write_outputs(src, SEGS, str(dest), ["txt"])
    assert out2["txt"] == str(dest / "a.txt") and dest.is_dir()
    merged = AX.merged_text([(src, SEGS), (str(tmp_path / "b.mp3"), [])])
    assert merged.startswith("## a.mp4\n第一句 话") and "## b.mp3\n（没有识别到内容）" in merged
    assert AX.merged_path(str(dest), src).startswith(str(dest))
    (tmp_path / "f").mkdir()
    for n in ("x.mp3", "y.MP4", "z.txt"):
        open(tmp_path / "f" / n, "wb").write(b"x")
    found = AX.list_media([str(tmp_path / "f"), src, src])
    assert [os.path.basename(p) for p in found] == ["x.mp3", "y.MP4", "a.mp4"]


def _fake_transcribe(path, *, language=None, asr_dir=None, on_progress=None, should_stop=None):
    for p in (20, 60, 100):
        if callable(should_stop) and should_stop():
            raise RuntimeError("已取消")
        if callable(on_progress):
            on_progress(p)
        time.sleep(0.02)
    if path.endswith("bad.mp4"):
        raise RuntimeError("读不了")
    return [dict(s) for s in SEGS]


def _wait(app, cond, sec=10.0):
    end = time.perf_counter() + sec
    while time.perf_counter() < end:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    return False


def test_batch_dialog_asr_tab_runs_and_exports(tmp_path, monkeypatch):
    monkeypatch.setenv("CUTORA_USER_DATA", str(tmp_path / "ud"))
    from PySide6.QtWidgets import QApplication, QWidget
    app = QApplication.instance() or QApplication(["q162"])
    from cutora.batch.batch_dialog import build_batch_dialog
    vids = []
    for n in ("v1.mp4", "v2.mp4", "bad.mp4"):
        p = str(tmp_path / n)
        open(p, "wb").write(b"x")
        vids.append(p)
    host = QWidget()
    host.tasks = [{"video": p, "duration": 10.0, "dims": (720, 1280), "image_overlays": [], "text_overlays": [],
                   "segments": []} for p in vids]
    host.curr_idx = 0
    host.timeline = QWidget()
    dlg = build_batch_dialog(host)
    page = dlg.asr_tab
    dlg.tabs.setCurrentWidget(page)
    app.processEvents()
    assert dlg.btn_run.text() == "开始转写"
    page.rb_lib.setChecked(True)
    page.ck_fmt["txt"].setChecked(True)
    page.ck_fmt["srt"].setChecked(True)
    page.ck_fmt["txt_time"].setChecked(False)
    page.ck_merge.setChecked(True)
    page.ck_into_task.setChecked(True)
    page.rb_folder.setChecked(True)
    page.ed_dest.setText(str(tmp_path / "out"))
    assert len(page.selected_paths()) == 3
    assert page.start(transcribe_fn=_fake_transcribe)
    assert page.busy()
    assert _wait(app, lambda: not page.busy())
    assert _wait(app, lambda: "完成" in page.msg.text())
    out = tmp_path / "out"
    assert (out / "v1.txt").is_file() and (out / "v1.srt").is_file() and (out / "v2.txt").is_file()
    assert not (out / "bad.txt").exists()
    merged = [p for p in os.listdir(out) if p.startswith("转写合集_")]
    assert len(merged) == 1 and "## v1.mp4" in open(out / merged[0], encoding="utf-8").read()
    assert host.tasks[0]["segments"] and host.tasks[0]["segments"][0]["text"] == "第一句 话"
    assert not host.tasks[2]["segments"]
    texts = [page.results.item(i).text() for i in range(page.results.count())]
    assert any(t.startswith("✔ v1.mp4") for t in texts) and any(t.startswith("✘ bad.mp4") for t in texts)
    assert dlg.btn_run.isEnabled()
    # 外部文件来源 + 取消
    page.rb_ext.setChecked(True)
    page.add_paths([str(tmp_path)])
    assert page.ext_list.count() == 3 and len(page.selected_paths()) == 3

    def _slow(path, **kw):
        for _ in range(50):
            if kw.get("should_stop") and kw["should_stop"]():
                raise RuntimeError("已取消")
            time.sleep(0.02)
        return [dict(s) for s in SEGS]
    assert page.start(transcribe_fn=_slow)
    page.cancel()
    assert _wait(app, lambda: not page.busy())
    assert _wait(app, lambda: "已取消" in page.msg.text())
    dlg.close()


def test_asr_thread_reports_each_file(tmp_path):
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(["q162"])
    from cutora.ui.batch_asr_tab import AsrBatchThread
    th = AsrBatchThread(["a.mp4", "bad.mp4"], transcribe_fn=_fake_transcribe)
    done = []
    th.file_done.connect(lambda i, p, s, e: done.append((i, p, s is not None, e)))
    fin = []
    th.all_done.connect(lambda ok, bad: fin.append((ok, bad)))
    th.start()
    assert _wait(app, lambda: bool(fin))
    assert fin == [(1, 1)] and done[0][2] is True and done[1][2] is False and done[1][3] == "读不了"
