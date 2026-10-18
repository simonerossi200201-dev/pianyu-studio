# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q190 · 批量加文本：标题对就一定定位对；对不上写明原因；原话可以一键替换标题。

口播词用的是 Luca 2026-10-08 那批视频（T0124 绿幕 a042–a060）缓存里的真实识别结果：
- a047「THREE TYPES OF PEOPLE…」口播被写成「3 types of people…」→ 以前「未定位」
- a042「…OCTOBER 1ST?」口播被写成「october first」→ 以前截在 1.5 s（只对上「may i pray for you」）
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _words(text, t0=0.0, step=0.3, gaps=None):
    out, t = [], float(t0)
    gaps = gaps or {}
    for k, w in enumerate(text.split()):
        t += gaps.get(k, 0.0)
        out.append({"start": round(t, 3), "end": round(t + step * 0.8, 3), "text": w})
        t += step
    return out


A047 = _words("3 types of people you should distance yourself from when god begins to lift you up "
              "not everyone will be able to walk with you when you begin to rise you need wisdom "
              "concerning these 3 types of people", gaps={8: 1.0, 16: 0.6})
A042 = _words("may i pray for your october first heavenly father i pray that on this day october first "
              "you would cover and bless the person listening")
A048 = _words("pastor if i dont give 10 percent of my salary will god be angry with me will i be cursed "
              "no the bible does not say that this is a human tradition")
JESUS = _words("is jesus the son of god or god himself 99 percent of christians answer this question "
               "incorrectly but the bibles true answer will completely change the way you see your faith")
KINGDOM = _words("is the kingdom of heaven really in the sky 90 percent of people have misunderstood it many "
                 "christians have always believed that the kingdom of heaven is in heaven but what the bible tells us makes")


# ─────────────────────────────── 对齐 ───────────────────────────────

def test_number_words_and_digits_match():
    """THREE ↔ 3：以前一个字对不上就整条未定位。"""
    from cutora.batch.text_batch import locate_text
    got = locate_text(A047, "THREE TYPES OF PEOPLE YOU SHOULD DISTANCE YOURSELF FROM WHEN GOD BEGINS TO LIFT YOU UP.\n"
                            "Not everyone will be able to walk with you when you begin to rise.")
    assert got["ok"] and got["how"] == "matched", got
    assert got["start"] == pytest.approx(A047[0]["start"])
    # 终点 = 「rise」说完，不是第一句就收
    rise = [w for w in A047 if w["text"] == "rise"][0]
    assert got["end"] == pytest.approx(rise["end"])


def test_ordinal_does_not_cut_the_title_short():
    """1ST ↔ first：以前只对上前 14 个字母，文本条在「your」就结束了。"""
    from cutora.batch.text_batch import locate_text
    got = locate_text(A042, "MAY I PRAY FOR YOUR OCTOBER 1ST?")
    first = A042[6]
    assert first["text"] == "first"
    assert got["ok"] and got["how"] == "matched"
    assert got["end"] == pytest.approx(first["end"])


def test_percent_and_contractions():
    from cutora.batch.text_batch import locate_text
    got = locate_text(A048, "Pastor, if I don’t give 10% of my salary, will God be angry with me? Will I be cursed?")
    cursed = [w for w in A048 if w["text"] == "cursed"][0]
    assert got["ok"] and got["how"] == "matched" and got["score"] >= 0.99
    assert got["end"] == pytest.approx(cursed["end"])


def test_one_misheard_word_is_partial_and_listed():
    from cutora.batch.text_batch import locate_text
    title = "Is Jesus the Son of God or God Himself? 99% of Christians answer this question wrongly."
    got = locate_text(JESUS, title)
    assert got["ok"] and got["how"] in ("matched", "partial")
    assert any(a == "wrongly" for a, _b in got["diff"])


def test_different_content_says_mismatch():
    from cutora.batch.text_batch import locate_text
    got = locate_text(JESUS, "GIVE THESE FOUR THINGS TO GOD EVERY MORNING")
    assert not got["ok"] and got["how"] == "mismatch"
    assert got["why"] == "视频内容与标题文字不符"


def test_title_running_past_the_window_is_window_short():
    from cutora.batch.text_batch import locate_text
    title = ("Is the kingdom of heaven really in the sky? 90% of people have misunderstood it. Many Christians "
             "have always believed that the kingdom of heaven is in heaven, but what the Bible tells us makes it "
             "clear that this is not the case at all.")
    got = locate_text(KINGDOM, title)
    assert not got["ok"] and got["how"] == "window_short"


def test_no_speech_and_empty():
    from cutora.batch.title_align import align_title
    assert align_title([], "hello world")["how"] == "no_speech"
    assert align_title(A042, "  ")["how"] == "empty"


def test_chinese_still_works():
    from cutora.batch.text_batch import locate_text
    segs = [{"start": 0.0, "end": 2.0, "text": "大家好，欢迎回来。"},
            {"start": 2.0, "end": 5.5, "text": "今天我们讲一个很重要的题目。"}]
    got = locate_text(segs, "大家好 欢迎回来 今天我们讲一个很重要的话题")
    assert got["ok"] and got["start"] == pytest.approx(0.0) and got["end"] == pytest.approx(5.5)


# ─────────────────────────────── 原话 → 标题 ───────────────────────────────

def test_replacement_follows_case_sentence_count_and_line_break():
    from cutora.batch.title_align import replacement_title, sentences_with_times
    sents = sentences_with_times(JESUS, [{"start": 0.0, "end": 9.0,
                                          "text": "Is Jesus the Son of God, or God Himself? 99% of Christians answer this "
                                                  "question incorrectly. But the Bible's true answer will change."}])
    assert [s["text"] for s in sents][:2] == ["Is Jesus the Son of God, or God Himself?",
                                              "99% of Christians answer this question incorrectly."]
    caps = replacement_title("IS JESUS THE SON OF GOD OR IS HE GOD?\nWRONG TITLE HERE.", sents)
    assert caps == "IS JESUS THE SON OF GOD, OR GOD HIMSELF?\n99% OF CHRISTIANS ANSWER THIS QUESTION INCORRECTLY."
    one = replacement_title("Something else entirely?", sents)
    assert one == "Is Jesus the Son of God, or God Himself?"


def test_sentences_fall_back_to_pauses_without_punctuation():
    from cutora.batch.title_align import spoken_sentences
    ss = spoken_sentences(A047)
    assert len(ss) >= 2 and ss[0]["text"].startswith("3 types of people")


# ─────────────────────────────── 缓存 / 整条视频 ───────────────────────────────

def test_cache_v3_keeps_sentences_and_reads_v2(tmp_path, monkeypatch):
    import json
    monkeypatch.setenv("CUTORA_USER_DATA", str(tmp_path / "ud"))
    import cutora.batch.text_locate_asr as LA
    LA.reset_cache()
    v = tmp_path / "a.mp4"
    v.write_bytes(b"x" * 100)
    LA.store(str(v), A042[:4], 0.0, LA.FULL_VIDEO, sentences=[{"start": 0, "end": 1, "text": "May I pray?"}])
    words, sents, label = LA.lookup_best(str(v), 0.0, 15.0)
    assert label == "full" and len(words) == 4 and sents[0]["text"] == "May I pray?"
    # v2 文件（只有词列表）照样能读
    path = LA._cache_path()
    data = json.load(open(path, encoding="utf-8"))
    key = next(iter(data["items"]))
    data = {"v": 2, "window": [0, 15], "items": {key.replace("|full|", "|0.00-15.00|"): A042[:3]}}
    json.dump(data, open(path, "w", encoding="utf-8"))
    LA.reset_cache()
    words, sents, label = LA.lookup_best(str(v), 0.0, 15.0)
    assert len(words) == 3 and sents == []


def test_full_video_is_chunked(monkeypatch):
    import cutora.batch.text_locate_asr as LA
    calls = []

    class _C:
        last_segments = []

        def transcribe(self, path, dur, lang, timeout_sec=0, win_start=0.0, win_end=0.0):
            calls.append((win_start, win_end))
            self.last_segments = [("Chunk %d." % len(calls), win_start, win_end)]
            return [("w%d" % len(calls), win_start + 0.1, win_start + 0.4)], False, 2

    monkeypatch.setattr(LA, "_media_duration", lambda p: 70.0)
    words, sents = LA._transcribe_full(_C(), "x.mp4", "en")
    assert calls[0] == (0.0, 30.0) and calls[-1][1] == 70.0 and len(calls) == 3
    assert len(words) == 3 and len(sents) == 3


# ─────────────────────────────── 界面 ───────────────────────────────

def _task(path, words=None):
    from cutora.model.timeline import Timeline
    return {"video": path, "segments": [], "timeline": Timeline(1080, 1920, 30.0),
            "text_overlays": [], "image_overlays": [], "dims": [1080, 1920]}


def _page(tasks):
    from PySide6.QtWidgets import QApplication
    from cutora.ui.batch_text_tab import build_text_tab
    QApplication.instance() or QApplication(["q190"])

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


@pytest.fixture
def three(tmp_path, monkeypatch):
    monkeypatch.setenv("CUTORA_USER_DATA", str(tmp_path / "ud"))
    import cutora.batch.text_locate_asr as LA
    LA.reset_cache()
    tasks = []
    for name, words, sent in (("a047.mp4", A047, "3 types of people you should distance yourself from. When God begins to lift you up."),
                              ("a042.mp4", A042, "May I pray for your October first? Heavenly Father, I pray."),
                              ("a050.mp4", JESUS, "Is Jesus the Son of God, or God Himself? 99% of Christians answer this question incorrectly.")):
        p = tmp_path / name
        p.write_bytes(name.encode() * 10)
        LA.store(str(p), words, 0.0, 15.0,
                 sentences=[{"start": words[0]["start"], "end": words[-1]["end"], "text": sent}])
        tasks.append(_task(str(p)))
    monkeypatch.setattr(LA, "queue_locate_batch", lambda *a, **k: 0)
    return tasks


def test_status_says_why(three):
    from cutora.ui.batch_text_tab import COL_SAID, COL_STATE
    page = _page(three)
    t = page.table
    t.fill_from(0, ["THREE TYPES OF PEOPLE YOU SHOULD DISTANCE YOURSELF FROM WHEN GOD BEGINS TO LIFT YOU UP",
                    "MAY I PRAY FOR YOUR OCTOBER 1ST?",
                    "GIVE THESE FOUR THINGS TO GOD EVERY MORNING"])
    page.preview_plans()
    st = lambda r: t.item(r, COL_STATE).text()   # noqa: E731
    assert st(0).startswith("已定位"), st(0)
    assert st(1).startswith("已定位"), st(1)
    assert "视频内容与标题文字不符" in st(2)
    assert "Is Jesus the Son of God" in t.item(2, COL_SAID).text()
    assert page.btn_said_bad.isEnabled() and page.btn_said_all.isEnabled()


def test_use_said_single_bad_and_undo(three):
    from cutora.ui.batch_text_tab import COL_STATE, COL_TEXT
    page = _page(three)
    t = page.table
    t.fill_from(0, ["THREE TYPES OF PEOPLE YOU SHOULD DISTANCE YOURSELF FROM WHEN GOD BEGINS TO LIFT YOU UP",
                    "MAY I PRAY FOR YOUR OCTOBER 1ST?",
                    "GIVE THESE FOUR THINGS TO GOD EVERY MORNING"])
    page.preview_plans()
    n = page.use_said_rows(True)          # 只替换不符的
    assert n == 1
    assert t.cell_value(2, COL_TEXT) == "IS JESUS THE SON OF GOD, OR GOD HIMSELF?"
    assert t.cell_value(0, COL_TEXT).startswith("THREE TYPES")      # 吻合的不动
    assert t.item(2, COL_STATE).text().startswith("已定位") and "已换成原话" in t.item(2, COL_STATE).text()
    t.undo()
    page.preview_plans()
    assert t.cell_value(2, COL_TEXT) == "GIVE THESE FOUR THINGS TO GOD EVERY MORNING"
    assert page.use_said(1) is True       # 单条：跟原标题的全大写
    assert t.cell_value(1, COL_TEXT) == "MAY I PRAY FOR YOUR OCTOBER FIRST?"


def test_filter_problem_rows(three):
    page = _page(three)
    t = page.table
    t.fill_from(0, ["THREE TYPES OF PEOPLE YOU SHOULD DISTANCE YOURSELF FROM WHEN GOD BEGINS TO LIFT YOU UP",
                    "MAY I PRAY FOR YOUR OCTOBER 1ST?",
                    "GIVE THESE FOUR THINGS TO GOD EVERY MORNING"])
    page.preview_plans()
    page.btn_filter.setChecked(True)
    assert [t.isRowHidden(r) for r in range(3)] == [True, True, False]
    page.btn_filter.setChecked(False)
    assert not any(t.isRowHidden(r) for r in range(3))


def test_pick_words_sets_exact_span(three):
    from cutora.ui.batch_text_tab import COL_STATE
    page = _page(three)
    t = page.table
    t.fill_from(0, ["", "", "GIVE THESE FOUR THINGS TO GOD EVERY MORNING"])
    page.preview_plans()
    t.setCurrentCell(2, 1)
    assert page.strip_box.isVisibleTo(page)
    strip = page.strip
    assert len(strip.words) == len(JESUS)
    strip.on_pick(JESUS[0]["start"], JESUS[5]["end"])      # 拖选 is … god
    assert t.item(2, COL_STATE).text().startswith("手选词")
    plan = page._last_plans[2]
    assert plan["start"] == pytest.approx(JESUS[0]["start"]) and plan["end"] == pytest.approx(JESUS[5]["end"])
    strip.clear_pick()
    assert "视频内容与标题文字不符" in t.item(2, COL_STATE).text()


def test_window_short_schedules_full_video(tmp_path, monkeypatch):
    monkeypatch.setenv("CUTORA_USER_DATA", str(tmp_path / "ud"))
    import cutora.batch.text_locate_asr as LA
    LA.reset_cache()
    p = tmp_path / "k.mp4"
    p.write_bytes(b"k" * 50)
    LA.store(str(p), KINGDOM, 0.0, 15.0)
    seen = {}

    def fake_queue(paths, on_done, **kw):
        seen["paths"], seen["kw"], seen["cb"] = list(paths), kw, on_done
        return len(paths)

    monkeypatch.setattr(LA, "queue_locate_batch", fake_queue)
    from cutora.ui.batch_text_tab import COL_STATE
    page = _page([_task(str(p))])
    title = ("Is the kingdom of heaven really in the sky? 90% of people have misunderstood it. Many Christians "
             "have always believed that the kingdom of heaven is in heaven, but what the Bible tells us makes it "
             "clear that this is not the case at all.")
    page.table.fill_from(0, [title])
    page.preview_plans()
    assert "正在整条识别" in page.table.item(0, COL_STATE).text()
    assert seen["paths"] == [str(p)] and seen["kw"]["end"] == LA.FULL_VIDEO
    full = KINGDOM + _words("it clear that this is not the case at all and we", t0=15.5)
    LA.store(str(p), full, 0.0, LA.FULL_VIDEO)
    seen["cb"](str(p), full, "")
    assert page.table.item(0, COL_STATE).text().startswith("已定位")


def test_paste_kicks_auto_locate(three, monkeypatch):
    page = _page(three)
    calls = []
    page.run_locate_asr = lambda *a, **k: calls.append(1)
    import cutora.ui.batch_text_tab as BT
    assert BT.AUTO_LOCATE_DELAY_MS <= 1000
    page.table.fill_from(0, ["x"])
    # 已经识别过的不会再跑；这里三条都有缓存 → 不触发
    page.kick_auto_locate()
    from PySide6.QtWidgets import QApplication
    import time
    t0 = time.time()
    while time.time() - t0 < 1.2:
        QApplication.processEvents()
    assert calls == []
