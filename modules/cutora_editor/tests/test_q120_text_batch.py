# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q120 · 批量加文本条：表格粘贴、自动定位到字幕时间、落到文本轨。"""
from __future__ import annotations

import pytest

from cutora.batch.text_batch import (
    apply_rows, build_overlay, locate_text, normalize, parse_paste, plan_rows,
    split_sentences,
)
from cutora.model.timeline import Timeline
from cutora.model.timeline_model import Track


# ------------------------------------------------------------ 表格粘贴

def test_paste_one_cell_per_row():
    assert parse_paste("第一句\n第二句\n第三句") == ["第一句", "第二句", "第三句"]


def test_paste_keeps_newlines_inside_a_cell():
    """Google Sheets 里单元格内部的换行是包在双引号里的，

    一个单元格就是一格内容，不管里面有多少换行 —— 不能按行 split。
    """
    clip = '"上一句。\n下一句。"\n"第二格"\n'
    assert parse_paste(clip) == ["上一句。\n下一句。", "第二格"]


def test_paste_takes_the_first_column():
    assert parse_paste("正文A\t备注\n正文B\t备注2") == ["正文A", "正文B"]


def test_paste_drops_trailing_blank_rows():
    assert parse_paste("甲\n乙\n\n\n") == ["甲", "乙"]


def test_paste_of_nothing():
    assert parse_paste("") == []
    assert parse_paste("   \n  ") == []


# ------------------------------------------------------------ 文字归一

def test_normalize_drops_punctuation_and_space():
    assert normalize("你好，世界！") == normalize("你好 世界")
    assert normalize("Hello, World.") == "helloworld"


def test_split_sentences():
    assert split_sentences("第一句。第二句！第三句？") == [
        "第一句。", "第二句！", "第三句？"]
    assert split_sentences("没有标点") == ["没有标点"]
    assert split_sentences("  ") == []


# ------------------------------------------------------------ 自动定位

SEGS = [
    {"start": 0.0, "end": 2.0, "text": "大家好，欢迎回来。"},
    {"start": 2.0, "end": 5.5, "text": "今天我们讲一个很重要的题目。"},
    {"start": 5.5, "end": 9.0, "text": "希望你能坚持看完。"},
    {"start": 9.0, "end": 14.0, "text": "我们开始吧。"},
]


def test_locate_first_two_sentences():
    """Luca 的用法：粘的是开头第一句第二句，文本条就从第一句开始、第二句说完为止。"""
    got = locate_text(SEGS, "大家好，欢迎回来。今天我们讲一个很重要的题目。")
    assert got["ok"]
    assert got["start"] == pytest.approx(0.0)
    assert got["end"] == pytest.approx(5.5)


def test_locate_ignores_punctuation_differences():
    """文案里的标点和字幕不一样是常事，不能因此就算没找到。"""
    got = locate_text(SEGS, "大家好 欢迎回来 今天我们讲一个很重要的题目")
    assert got["ok"] and got["end"] == pytest.approx(5.5)


def test_locate_a_single_sentence_in_the_middle():
    got = locate_text(SEGS, "希望你能坚持看完。")
    assert got["ok"]
    assert got["start"] == pytest.approx(5.5)
    assert got["end"] == pytest.approx(9.0)


def test_locate_tolerates_a_typo():
    """ASR 的字幕和手里的文案差一两个字，用开头几个字也要能对上。"""
    got = locate_text(SEGS, "今天我们讲一个很重要的话题。")   # 题目 → 话题
    assert got["ok"]
    assert got["start"] == pytest.approx(2.0)


def test_locate_without_subtitles_says_so():
    got = locate_text([], "随便什么")
    assert not got["ok"] and "还没有字幕" in got["why"]


def test_locate_miss_is_reported():
    got = locate_text(SEGS, "完全不相干的一段话，字幕里根本没有。")
    assert not got["ok"] and got["why"]


def test_locate_skips_segments_without_times():
    segs = [{"start": None, "end": None, "text": "坏数据"}] + SEGS
    got = locate_text(segs, "我们开始吧。")
    assert got["ok"] and got["start"] == pytest.approx(9.0)


# ------------------------------------------------------------ 排期

def _task(segs=None):
    tl = Timeline(1080, 1920, 30.0)
    tl.add_track(Track("video"))
    return {"timeline": tl, "segments": list(segs or []), "dims": (1080, 1920)}


def test_plan_uses_the_located_range():
    tasks = [_task(SEGS)]
    plans = plan_rows(tasks, [{"task_idx": 0, "text": "希望你能坚持看完。"}])
    assert plans[0]["start"] == pytest.approx(5.5)
    assert plans[0]["end"] == pytest.approx(9.0)
    assert plans[0]["how"] == "matched"


def test_plan_falls_back_to_the_default_duration():
    """没字幕/对不上也照样加（Luca 定的），但要标出来。"""
    tasks = [_task([])]
    plans = plan_rows(tasks, [{"task_idx": 0, "text": "一段话"}], default_dur=4.0)
    assert plans[0]["ok"]
    assert plans[0]["start"] == pytest.approx(0.0)
    assert plans[0]["end"] == pytest.approx(4.0)
    assert plans[0]["how"] == "fallback"
    assert plans[0]["warn"]


def test_plan_respects_hand_typed_times():
    tasks = [_task(SEGS)]
    plans = plan_rows(tasks, [{"task_idx": 0, "text": "希望你能坚持看完。",
                               "start": 30.0, "dur": 2.0}], auto_locate=False)
    assert plans[0]["start"] == pytest.approx(30.0)
    assert plans[0]["end"] == pytest.approx(32.0)
    assert plans[0]["how"] == "manual"


def test_plan_skips_empty_cells():
    tasks = [_task(SEGS)]
    plans = plan_rows(tasks, [{"task_idx": 0, "text": "   "}])
    assert not plans[0]["ok"] and "空" in plans[0]["warn"]


def test_plan_can_take_segments_from_elsewhere():
    """没跑过字幕的视频：只为定位跑的那份识别结果从外面喂进来。"""
    tasks = [_task([])]
    plans = plan_rows(tasks, [{"task_idx": 0, "text": "我们开始吧。"}],
                      segments_of=lambda _t: SEGS)
    assert plans[0]["start"] == pytest.approx(9.0)
    assert plans[0]["how"] == "matched"


# ------------------------------------------------------------ 落到轨道

def test_apply_makes_one_new_text_track_per_video():
    tasks = [_task(SEGS), _task(SEGS)]
    plans = plan_rows(tasks, [
        {"task_idx": 0, "text": "大家好，欢迎回来。"},
        {"task_idx": 0, "text": "我们开始吧。"},
        {"task_idx": 1, "text": "希望你能坚持看完。"},
    ])
    stats = apply_rows(plans, tasks)
    assert stats == {"tasks": 2, "overlays": 3, "skipped": 0, "tracks": 0}
    for t in tasks:
        text_tracks = [tr for tr in t["timeline"].tracks if tr.type == "text"]
        assert len(text_tracks) == 1, "同一条视频的几行共用一条新轨"
    assert len(tasks[0]["text_overlays"]) == 2
    assert len(tasks[1]["text_overlays"]) == 1
    tid = tasks[0]["timeline"].tracks[-1].id
    assert all(ov["_track_id"] == tid for ov in tasks[0]["text_overlays"])


def test_apply_writes_the_located_times():
    tasks = [_task(SEGS)]
    plans = plan_rows(tasks, [{"task_idx": 0, "text": "希望你能坚持看完。"}])
    apply_rows(plans, tasks)
    ov = tasks[0]["text_overlays"][0]
    assert ov["start"] == pytest.approx(5.5)
    assert ov["end"] == pytest.approx(9.0)
    assert ov["content"] == "希望你能坚持看完。"


def test_template_styles_the_text_but_not_its_content():
    tasks = [_task(SEGS)]
    plans = plan_rows(tasks, [{"task_idx": 0, "text": "真正要显示的字"}])
    apply_rows(plans, tasks,
               template={"content": "模板里的示例字", "color": "#FFCC00",
                         "fontsize": 120, "_preset_name": "我的模板"})
    ov = tasks[0]["text_overlays"][0]
    assert ov["content"] == "真正要显示的字", "模板里的示例文字不许盖掉用户粘的内容"
    assert ov["color"] == "#FFCC00"
    assert ov["fontsize"] == 120
    assert ov.get("_preset_name") != "我的模板", "模板的名字不该被当成样式抄进去"


def test_build_overlay_gets_a_real_text_box():
    """Q124：框不再是整块画布 —— 和 Ctrl+T、拖 .txt 同一份几何。"""
    from cutora.model.factory_defaults import default_text_box
    ov = build_overlay("字", 1.0, 3.0, width=1080, height=1920)
    assert (ov["x"], ov["y"], ov["w"], ov["h"]) == default_text_box(1080, 1920, fontsize=ov["fontsize"])
    assert ov["fit_mode"] == "auto_height" and ov["lock_aspect"] is False
    assert ov["start"] == 1.0 and ov["end"] == 3.0
    assert ov["type"] == "text"


def test_apply_skips_rows_with_no_timeline():
    tasks = [{"segments": []}]
    plans = plan_rows(tasks, [{"task_idx": 0, "text": "字"}])
    stats = apply_rows(plans, tasks)
    assert stats["skipped"] == 1 and stats["overlays"] == 0


# ------------------------------------------------------------ 表格界面

def _page(tasks):
    from PySide6.QtWidgets import QApplication
    from cutora.ui.batch_text_tab import build_text_tab
    QApplication.instance() or QApplication(["q120"])

    class _Picker:
        def selected_tasks(self):
            return list(tasks)

    class _Host:
        pass

    host = _Host()
    host.tasks = list(tasks)
    page = build_text_tab(None, host, _Picker())
    page.refresh()
    return page, host


def test_table_has_one_row_per_selected_video():
    tasks = [_task(SEGS), _task(SEGS)]
    tasks[0]["video"] = r"D:\x\v01.mp4"
    tasks[1]["video"] = r"D:\x\v02.mp4"
    page, _host = _page(tasks)
    assert page.table.rowCount() == 2
    assert [page.table.item(r, 0).text() for r in range(2)] == ["v01.mp4", "v02.mp4"]


def test_paste_fills_downward_from_the_picked_cell():
    """点中第二行再粘，就从第二行开始往下填，第一行不动。"""
    tasks = [_task(SEGS) for _ in range(3)]
    page, _host = _page(tasks)
    page.table.fill_from(0, ["原本第一行"])
    page.table.fill_from(1, ["甲", "乙"])
    got = [page.table.item(r, 1).text() for r in range(3)]
    assert got == ["原本第一行", "甲", "乙"]


def test_paste_beyond_the_last_row_is_dropped():
    tasks = [_task(SEGS)]
    page, _host = _page(tasks)
    assert page.table.fill_from(0, ["一", "二", "三"]) == 1


def test_refresh_keeps_what_was_typed():
    tasks = [_task(SEGS), _task(SEGS)]
    page, _host = _page(tasks)
    page.table.fill_from(0, ["甲", "乙"])
    page.refresh()
    assert [page.table.item(r, 1).text() for r in range(2)] == ["甲", "乙"]


def test_state_column_says_located_or_not():
    tasks = [_task(SEGS), _task([])]
    page, _host = _page(tasks)
    page.table.fill_from(0, ["希望你能坚持看完。", "字幕里没有的一段"])
    page.preview_plans()
    assert "已定位" in page.table.item(0, 2).text()
    assert "5.5" in page.table.item(0, 2).text()
    assert "默认时长" in page.table.item(1, 2).text()


def test_apply_puts_the_text_on_the_timeline():
    tasks = [_task(SEGS)]
    page, host = _page(tasks)
    page.table.fill_from(0, ["希望你能坚持看完。"])
    stats = page.apply()
    assert stats["overlays"] == 1
    ov = tasks[0]["text_overlays"][0]
    assert ov["content"] == "希望你能坚持看完。"
    assert ov["start"] == pytest.approx(5.5)
    assert ov["end"] == pytest.approx(9.0)
    assert [tr.type for tr in tasks[0]["timeline"].tracks].count("text") == 1


def test_apply_with_an_empty_table_adds_nothing():
    tasks = [_task(SEGS)]
    page, _host = _page(tasks)
    stats = page.apply()
    assert stats["overlays"] == 0
    assert not tasks[0].get("text_overlays")


# ------------------------------------------- 只要时间戳的识别（不生成字幕）

def test_locate_asr_is_cached_by_fingerprint(tmp_path, monkeypatch):
    from cutora.batch import text_locate_asr as LA
    monkeypatch.setenv("CUTORA_USER_DATA", str(tmp_path / "ud"))
    LA.reset_cache()
    vid = tmp_path / "a.mp4"
    vid.write_bytes(b"not really a video")
    calls = {"n": 0}

    def _fake(path, language="auto"):
        calls["n"] += 1
        return [{"start": 0.0, "end": 1.0, "text": "你好"}]

    monkeypatch.setattr(LA, "_transcribe_uncached", _fake)
    assert LA.segments_for_locate(str(vid))[0]["text"] == "你好"
    assert LA.segments_for_locate(str(vid))[0]["text"] == "你好"
    assert calls["n"] == 1, "同一条视频只该跑一次"
    vid.write_bytes(b"different content now")
    LA.segments_for_locate(str(vid))
    assert calls["n"] == 2, "文件变了要重跑"


def test_locate_asr_never_writes_subtitles(tmp_path, monkeypatch):
    """只要时间戳：结果不许进 task['segments']，不许建字幕轨。"""
    from cutora.batch import text_locate_asr as LA
    monkeypatch.setenv("CUTORA_USER_DATA", str(tmp_path / "ud2"))
    LA.reset_cache()
    vid = tmp_path / "b.mp4"
    vid.write_bytes(b"x")
    monkeypatch.setattr(
        LA, "_transcribe_uncached",
        lambda p, language="auto": [{"start": 1.0, "end": 2.0, "text": "一句话"}])
    task = _task([])
    task["video"] = str(vid)
    segs = LA.segments_for_locate(str(vid))
    assert segs and task["segments"] == []
    assert [tr.type for tr in task["timeline"].tracks] == ["video"]
    # 但定位照样用得上
    plans = plan_rows([task], [{"task_idx": 0, "text": "一句话"}],
                      segments_of=lambda _t: segs)
    assert plans[0]["start"] == pytest.approx(1.0)


def test_locate_asr_slims_the_segments(tmp_path, monkeypatch):
    from cutora.batch import text_locate_asr as LA
    monkeypatch.setenv("CUTORA_USER_DATA", str(tmp_path / "ud3"))
    LA.reset_cache()
    vid = tmp_path / "c.mp4"
    vid.write_bytes(b"x")
    monkeypatch.setattr(
        LA, "_transcribe_uncached",
        lambda p, language="auto": LA._slim([
            {"start": 0.0, "end": 1.0, "text": " 有效 ",
             "words": [{"w": 1}] * 50},
            {"start": None, "end": 2.0, "text": "坏时间"},
            {"start": 2.0, "end": 3.0, "text": "   "},
        ]))
    segs = LA.segments_for_locate(str(vid))
    assert segs == [{"start": 0.0, "end": 1.0, "text": "有效"}]


# ----------------------------------------- 对话框里这一页的位置（Q123）

def _dialog(tasks):
    from PySide6.QtWidgets import QApplication, QTabWidget, QWidget
    QApplication.instance() or QApplication(["q123"])
    from cutora.batch.batch_dialog import build_batch_dialog

    class Host(QWidget):
        def __init__(self):
            super().__init__()
            self.tasks = list(tasks)
            self.curr_idx = 0

    host = Host()
    dlg = build_batch_dialog(host)
    tabs = dlg.findChild(QTabWidget)
    return dlg, tabs


def test_the_old_single_line_add_text_is_gone():
    """参数栏里那个「添加文本」单行框删掉了：它只能给一整批加同一句话，

    和文本分页摆在一起只会让人不知道该用哪个。
    """
    tasks = [_task(SEGS)]
    dlg, _tabs = _dialog(tasks)
    try:
        from PySide6.QtWidgets import QLabel
        labels = [w.text() for w in dlg.findChildren(QLabel)]
        assert "添加文本" not in labels
    finally:
        dlg.close()


def test_text_tab_hides_the_image_only_groups():
    """目标轨道写的是 Images N，多平台是给图片出多版的 —— 文本页不该看见。"""
    tasks = [_task(SEGS)]
    dlg, tabs = _dialog(tasks)
    try:
        names = [tabs.tabText(i) for i in range(tabs.count())]
        # 音频页、片段散落在 6c3cd445 就已经在。文本仍是第 3 页（下标 2）。
        assert names == ["图片", "视频", "文本", "音频", "语音转文本", "片段散落"]
        tabs.setCurrentIndex(0)
        from PySide6.QtWidgets import QLabel
        def _visible_titles():
            return {w.text() for w in dlg.findChildren(QLabel)
                    if w.isVisibleTo(dlg) and w.text() in
                    ("时间", "外观", "导出", "多平台")}
        dlg.show()
        on_image = _visible_titles()
        tabs.setCurrentIndex(2)
        on_text = _visible_titles()
        assert "外观" in on_image and "时间" in on_image
        assert not on_text, "文本页不该留下这几组：%s" % on_text
    finally:
        dlg.close()


def test_columns_are_draggable():
    """中间那栏以前是写死的 420，文本表格右边被切出画面外还拉不动。"""
    from PySide6.QtWidgets import QSplitter
    tasks = [_task(SEGS)]
    dlg, tabs = _dialog(tasks)
    try:
        assert isinstance(dlg.column_layout, QSplitter)
        assert dlg.column_layout.count() == 3
        dlg.show()
        tabs.setCurrentIndex(2)
        from PySide6.QtWidgets import QApplication
        QApplication.processEvents()
        # 切到文本页时中间栏要够宽
        from cutora.batch.batch_dialog import TEXT_TAB_MIN_W
        assert dlg.column_layout.sizes()[1] >= TEXT_TAB_MIN_W - 1
    finally:
        dlg.close()
