# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q150 · 批量加载 → 文本：「识别开头时间戳」看得见进度和结果。

Luca：点了识别，表格里一排「空文本，跳过」，不知道到底识别了没有、识别到哪了。
现在：进度条 n / N + 正在转哪条；每行状态写「已识别 N 个词」/「识别失败：原因」；
悬停看识别到的原话；粘上标题后按时间戳定位（一行对一条视频）。"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _task(path):
    from cutora.model.timeline import Timeline
    return {"video": path, "segments": [], "timeline": Timeline(1080, 1920, 30.0),
            "text_overlays": [], "image_overlays": [], "dims": [1080, 1920]}


WORDS = [{"start": 0.2, "end": 0.5, "text": "When"}, {"start": 0.5, "end": 0.8, "text": "God"},
         {"start": 0.8, "end": 1.1, "text": "is"}, {"start": 1.1, "end": 1.6, "text": "leading"},
         {"start": 1.6, "end": 1.9, "text": "you"}, {"start": 3.0, "end": 3.4, "text": "first"}]


def _page(tasks):
    from PySide6.QtWidgets import QApplication
    from cutora.ui.batch_text_tab import build_text_tab
    QApplication.instance() or QApplication(["q150"])

    class _Picker:
        def selected_tasks(self):
            return list(tasks)

    class _Host:
        pass

    host = _Host()
    host.tasks = list(tasks)
    page = build_text_tab(None, host, _Picker())
    page.refresh()
    return page


def test_progress_status_and_title_locate(tmp_path, monkeypatch):
    monkeypatch.setenv("CUTORA_USER_DATA", str(tmp_path / "ud"))
    import cutora.batch.text_locate_asr as LA
    LA.reset_cache()
    tasks = [_task(str(tmp_path / "v01.mp4")), _task(str(tmp_path / "v02.mp4"))]
    for t in tasks:
        open(t["video"], "wb").write(b"x")
    calls = {}

    def fake_queue(paths, on_done, **_kw):
        calls["paths"] = list(paths)
        calls["on_done"] = on_done
        return len(paths)

    monkeypatch.setattr(LA, "queue_locate_batch", fake_queue)
    page = _page(tasks)
    st = lambda r: page.table.item(r, 2).text()   # noqa: E731
    assert "还没识别" in st(0)
    page.run_locate_asr()
    assert page.asr_prog.isVisibleTo(page) and page.asr_prog.maximum() == 2
    assert st(0) == "识别中…" and st(1) == "排队识别…"
    assert "第 1 / 2 条" in page.asr_msg.text()
    # 第一条识别完
    LA.store(tasks[0]["video"], WORDS, 0.0, 15.0)
    calls["on_done"](tasks[0]["video"], WORDS, "")
    assert page.asr_prog.value() == 1
    assert "已识别 6 个词" in st(0) and st(1) == "识别中…"
    assert "When God is leading you" in page.table.item(0, 2).toolTip()
    # 第二条失败：原因写在状态和汇总里，不藏在按钮提示里
    calls["on_done"](tasks[1]["video"], [], "显存不够")
    assert "识别失败：显存不够" in st(1)
    assert not page.asr_prog.isVisibleTo(page)
    assert "1 条成功" in page.asr_msg.text() and "显存不够" in page.asr_msg.text()
    assert page.btn_asr.isEnabled()
    # 粘上标题：按这条视频自己的时间戳定位
    page.table.fill_from(0, ["When God is leading you"])
    page.preview_plans()
    assert st(0).startswith("已定位") and "0.2–1.9s" in st(0)
