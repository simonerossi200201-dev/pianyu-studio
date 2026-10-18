# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q137 · 字幕断句：合并碎句、按意群切、每行最多词数、左右安全边距、停顿阈值。"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _app():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(["q137"])
    from cutora.ui.panels.fonts import load_embedded_fonts
    load_embedded_fonts()
    return app


def _words(text, t0=0.0, step=0.4, gap=0.0):
    out, t = [], t0
    for w in text.split():
        out.append({"word": w, "start": round(t, 3), "end": round(t + step, 3)})
        t += step + gap
    return out


def _style(**kw):
    from cutora.subtitle.stylepack import default_subtitle_style
    s = default_subtitle_style()
    s.update({"fontsize": 64, "wrap_lines": 2, "wrap_width_percent": 86, "layout_engine": 2})
    s.update(kw)
    return s


# ─────────────── 切口优先级 ───────────────

def test_cut_cost_priority():
    from cutora.subtitle.reflow import cut_cost
    after_comma = cut_cost("Lord,", "and")
    before_num = cut_cost("verse", "3")
    before_cap = cut_cost("said", "Jesus")
    before_conj = cut_cost("mercy", "because")
    anywhere = cut_cost("great", "mercy")
    after_the = cut_cost("the", "Lord")
    assert after_comma < before_num < before_cap < before_conj < anywhere < after_the


def test_split_run_prefers_punctuation_and_avoids_orphans():
    from cutora.subtitle.reflow import split_run
    toks = "Trust in the Lord with all your heart, and lean not on your own understanding".split()
    fits = lambda seg: len(" ".join(seg)) <= 40      # 模拟两行的容量
    parts = split_run(toks, fits)
    texts = [" ".join(p) for p in parts]
    assert texts[0].endswith("heart,"), texts
    assert all(len(p) > 1 for p in parts), texts
    assert all(len(t) <= 40 for t in texts)
    assert len(parts) == 2


# ─────────────── 跨字幕重排 ───────────────

def test_reflow_merges_fragmented_asr_segments():
    _app()
    from cutora.subtitle.reflow import reflow_task
    text = "What does the Bible really say about fear and worry"
    words = _words(text, step=0.3)
    # ASR 给的碎句：2–3 个词一条，中间没有停顿
    segs, i = [], 0
    for n in (2, 3, 2, 3):
        chunk = words[i:i + n]
        segs.append({"id": "s%d" % i, "start": chunk[0]["start"], "end": chunk[-1]["end"],
                     "text": " ".join(w["word"] for w in chunk), "words": chunk})
        i += n
    task = {"dims": (1080, 1920), "style": _style(), "segments": segs}
    out = reflow_task(task)
    assert out["after"] < 4, (out, [s["text"] for s in task["segments"]])
    joined = " ".join(s["text"] for s in task["segments"])
    assert joined == text
    ids = [s["id"] for s in task["segments"]]
    assert len(set(ids)) == len(ids)
    ends = [s["end"] for s in task["segments"]]
    starts = [s["start"] for s in task["segments"]]
    assert all(a <= b for a, b in zip(ends, starts[1:]))


def test_reflow_breaks_on_sentence_end_and_pause():
    _app()
    from cutora.subtitle.reflow import reflow_task
    w1 = _words("Amen.", 0.0)
    w2 = _words("God is good", 0.5)
    w3 = _words("all the time", 5.0)         # 大停顿
    words = w1 + w2 + w3
    seg = {"id": "x", "start": 0.0, "end": words[-1]["end"],
           "text": " ".join(w["word"] for w in words), "words": words}
    task = {"dims": (1080, 1920), "style": _style(pause_break_sec=0.6), "segments": [seg]}
    reflow_task(task)
    texts = [s["text"] for s in task["segments"]]
    assert texts == ["Amen.", "God is good", "all the time"], texts


def test_reflow_keeps_locked_and_edited_segments():
    _app()
    from cutora.subtitle.reflow import reflow_task
    a = _words("keep me", 0.0)
    b = _words("hand edited", 1.0)
    c = _words("free to flow here", 2.0)
    segs = [
        {"id": "a", "start": 0.0, "end": 0.8, "text": "keep me", "words": a, "locked": True},
        {"id": "b", "start": 1.0, "end": 1.8, "text": "hand edited", "words": b, "edited_text": "HAND EDITED!"},
        {"id": "c", "start": 2.0, "end": 3.6, "text": "free to flow here", "words": c},
    ]
    task = {"dims": (1080, 1920), "style": _style(), "segments": segs}
    reflow_task(task)
    assert task["segments"][0]["locked"] and task["segments"][0]["text"] == "keep me"
    assert task["segments"][1]["edited_text"] == "HAND EDITED!"


def test_max_words_per_line_caps_a_segment():
    _app()
    from cutora.subtitle.reflow import group_words
    words = _words("one two three four five six seven eight nine ten eleven twelve")
    task = {"dims": (1080, 1920), "style": _style(max_words_per_line=2, fontsize=30)}
    groups = group_words(words, task["style"], task, max_lines=2, merge_runs=False)
    assert all(len(g) <= 4 for g in groups), [len(g) for g in groups]


def test_single_word_mode_is_one_word_per_cue():
    _app()
    from cutora.subtitle.reflow import group_words
    words = _words("What does the Bible say")
    task = {"dims": (1080, 1920), "style": _style(word_mode="single")}
    groups = group_words(words, task["style"], task)
    assert [g[0]["word"] for g in groups] == "What does the Bible say".split()


# ─────────────── 安全边距 ───────────────

def test_safe_margin_gated_by_style_key():
    _app()
    from cutora.render.textfx.layout import _line_avail_width
    from cutora.render.textfx.spec import from_style
    from cutora.subtitle.stylepack import SAFE_MARGIN
    base = _style(wrap_width_percent=100)
    base.pop("layout_engine")
    old = _line_avail_width(base, 1080, 64.0, 0.0)
    assert abs(old - 1080 * (1 - 2 * SAFE_MARGIN)) < 1e-6
    tight = dict(base, safe_margin_pct=2)
    assert abs(_line_avail_width(tight, 1080, 64.0, 0.0) - 1080 * 0.96) < 1e-6
    wide = dict(base, safe_margin_pct=15)
    assert _line_avail_width(wide, 1080, 64.0, 0.0) < old
    from_style(tight)    # 多出来的键不许让 spec 解析报错


def test_estimate_words_per_line_matches_measure_and_cap():
    _app()
    from cutora.subtitle.stylepack import estimate_words_per_line
    text = "the quick brown fox jumps over the lazy dog again and again"
    s = _style(wrap_width_percent=100)
    n_default = estimate_words_per_line(s, 1080, text)
    n_tight = estimate_words_per_line(dict(s, safe_margin_pct=0), 1080, text)
    assert n_tight >= n_default
    assert estimate_words_per_line(dict(s, max_words_per_line=3), 1080, text) == 3


def test_panel_writes_keys_only_when_touched():
    _app()
    from cutora.ui.panels.subtitle_style import SubtitleStylePanel
    panel = SubtitleStylePanel()
    style = _style()
    style.pop("layout_engine")
    panel.load_data(style)
    panel.on_change()
    assert "safe_margin_pct" not in style and "layout_engine" not in style
    panel.sub_safe_margin.setValue(3)
    assert style["safe_margin_pct"] == 3 and style["layout_engine"] == 2
    panel.sub_max_words.setValue(4)
    assert style["max_words_per_line"] == 4
