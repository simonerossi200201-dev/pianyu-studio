# SPDX-License-Identifier: GPL-3.0-or-later
"""Q96：没有逐词时间戳时，卡拉OK / 当前词高亮也得往前走。

以前手打或导入 SRT 的字幕没有 words，每个 run 的时间戳都等于整段，
current_run() 永远命中第一个词 —— 套上「琥珀卡拉OK」看着毫无反应。
"""
import pytest

from PySide6.QtWidgets import QApplication

from cutora.render.textfx.layout import (
    has_word_times, layout_event, spread_word_times,
)
from cutora.render.textfx.spec import from_style
from cutora.render.textfx.word_fx import current_run
from cutora.subtitle import stylepack as sp


@pytest.fixture(scope="module")
def app():
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    yield QApplication.instance() or QApplication([])


def _amber_style():
    dirs = [p for p in sp.list_pack_dirs() if p.replace("\\", "/").endswith(
        "amber-karaoke")]
    assert dirs, "内置风格包 amber-karaoke 不见了"
    task = {"video": "x.mp4", "width": 1080, "height": 1920, "style": {}}
    sp.apply_pack_to_task(task, sp.load_pack(dirs[0]), apply_animation=True)
    return task["style"]


def test_amber_pack_still_carries_karaoke(app):
    style = _amber_style()
    assert style["fx"]["anim"]["loop"] == "karaoke"
    assert style["fx"]["anim"]["unit"] == "word"


def test_has_word_times():
    assert not has_word_times({})
    assert not has_word_times({"words": []})
    assert not has_word_times({"words": [{"word": "a", "start": 1, "end": 1}]})
    assert has_word_times({"words": [{"word": "a", "start": 1, "end": 2}]})


def test_karaoke_advances_without_word_timestamps(app):
    style = _amber_style()
    spec = from_style(style)
    ev = {"start": 0.0, "end": 3.0, "text": "Find its place today",
          "lines": ["Find its place today"]}
    lay = layout_event(ev, style, spec, 1080, 1920)
    seen = []
    for t in (0.1, 0.9, 1.6, 2.4):
        run, _u, idx = current_run(lay, t)
        assert run is not None
        seen.append(idx)
    assert seen == sorted(seen)
    assert len(set(seen)) >= 3, "整段只高亮了一个词，等于没效果"


def test_real_word_timestamps_win(app):
    """有 ASR 逐词时间戳时不许被平摊覆盖。"""
    style = _amber_style()
    spec = from_style(style)
    ev = {
        "start": 0.0, "end": 3.0, "text": "Find its place",
        "lines": ["Find its place"],
        "words": [
            {"word": "Find", "start": 0.0, "end": 2.5},
            {"word": "its", "start": 2.5, "end": 2.7},
            {"word": "place", "start": 2.7, "end": 3.0},
        ],
    }
    lay = layout_event(ev, style, spec, 1080, 1920)
    run, _u, idx = current_run(lay, 2.0)
    assert idx == 0 and run.text.strip() == "Find"


def test_no_word_anim_leaves_timings_alone(app):
    """风格里没有按词走的动画就不动时间戳。"""
    style = _amber_style()
    style["fx"]["anim"] = dict(style["fx"]["anim"])
    style["fx"]["anim"]["loop"] = "none"
    spec = from_style(style)
    ev = {"start": 0.0, "end": 3.0, "text": "Find its place",
          "lines": ["Find its place"]}
    lay = layout_event(ev, style, spec, 1080, 1920)
    runs = [r for ln in lay.lines for r in ln.runs if r.text.strip()]
    assert all(r.t_start == 0.0 and r.t_end == 3.0 for r in runs)


def test_spread_is_monotonic_and_covers_span(app):
    class _R:
        def __init__(self, text, w):
            self.text, self.w = text, w
            self.t_start = self.t_end = 0.0

    class _L:
        def __init__(self, runs):
            self.runs = runs

    runs = [_R("aa", 20.0), _R("bbbb", 40.0), _R("c", 10.0)]
    spread_word_times([_L(runs)], 5.0, 9.0)
    assert runs[0].t_start == pytest.approx(5.0)
    assert runs[-1].t_end == pytest.approx(9.0)
    for a, b in zip(runs, runs[1:]):
        assert a.t_end == pytest.approx(b.t_start)
        assert a.t_end > a.t_start
    # 宽的词占的时间长
    assert (runs[1].t_end - runs[1].t_start) > (runs[0].t_end - runs[0].t_start)


def test_spread_survives_degenerate_span(app):
    class _R:
        text, w = "a", 10.0
        t_start = t_end = 0.0
    r = _R()

    class _L:
        runs = [r]
    spread_word_times([_L()], 2.0, 2.0)
    assert r.t_start == 0.0 and r.t_end == 0.0
    spread_word_times([], 0.0, 1.0)
