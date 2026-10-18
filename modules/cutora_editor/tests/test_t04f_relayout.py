# -*- coding: utf-8 -*-
"""T04f-1d-3: max_lines is a hard cap. Parent is Qt-free; child paints.

Hard-constraint layout tests (appendix C / D) must run both draw paths:
v1 (style has no ``fx``) and v2 (style.fx is a dict). A v2-only green
test is how the overlong-token overflow reached Luca's v1 project.
"""
from __future__ import annotations

import os

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_fontsize_triggers_auto_relayout():
    from cutora.subtitle.layout import (
        auto_relayout_call_count, maybe_auto_relayout, reset_auto_relayout_calls,
    )
    reset_auto_relayout_calls()
    task = {
        "style": {"wrap_lines": 1, "fontsize": 48, "font_family": "Arimo"},
        "segments": [{
            "start": 0.0, "end": 2.0, "lang": "en",
            "text": "Hello friends.",
            "layout_warn": "shrink",
        }],
    }
    maybe_auto_relayout(task, "fontsize", settings={})
    assert auto_relayout_call_count() == 1
    assert not task["segments"][0].get("layout_warn")
    maybe_auto_relayout(task, "color_text", settings={})
    assert auto_relayout_call_count() == 1


def test_relayout_command_schema_and_headless():
    from cutora.core.command_schema import check_schema
    from cutora.core.commands import get_registry
    spec = get_registry().get("subtitle.relayout")
    assert spec.headless is True
    assert spec.undoable is True
    assert len(spec.doc_zh) >= 40
    check_schema(spec.params_schema)
    check_schema(spec.returns_schema)


def test_max_lines_2_path_untouched():
    src = open(
        os.path.join(REPO, "cutora", "subtitle", "layout.py"), encoding="utf-8",
    ).read()
    assert "def _best_two_line_split" in src
    assert "max_lines == 2" in src and "_greedy_pack" in src
    wrap = open(
        os.path.join(REPO, "cutora", "subtitle", "stylepack.py"), encoding="utf-8",
    ).read()
    assert "max_lines=8" not in wrap
    assert "max(8, int(rules.get(\"max_lines\")" not in wrap


def test_truncate_policy_and_copy():
    src = open(
        os.path.join(REPO, "cutora", "subtitle", "layout.py"), encoding="utf-8",
    ).read()
    assert "def fit_lines_to_width" in src
    assert "v1" in src and "truncate" in src and "SAFE_MARGIN" in src
    assert "golden freeze" in src
    trs = open(
        os.path.join(REPO, "cutora", "ui", "panels", "transcript.py"),
        encoding="utf-8",
    ).read()
    assert "有单词过长，已截断，建议改短或减小字号" in trs
    fx = open(
        os.path.join(REPO, "cutora", "render", "textfx", "layout.py"),
        encoding="utf-8",
    ).read()
    assert "fit_lines_to_width" in fx
    fr = open(
        os.path.join(REPO, "cutora", "render", "frame_renderer.py"),
        encoding="utf-8",
    ).read()
    assert "reflow_and_fit" in fr or "fit_lines_to_width" in fr


def test_ui_relayout_entrypoints():
    mw = open(os.path.join(REPO, "cutora", "ui", "main_window.py"), encoding="utf-8").read()
    assert 'tr("按当前排版重新断句")' in mw
    panel = open(
        os.path.join(REPO, "cutora", "ui", "panels", "subtitle_style.py"),
        encoding="utf-8",
    ).read()
    assert "relayout_request" in panel
    assert "重新断句" in panel


REAL_SEGS = [
    "Hello friends welcome to the studio today.",
    "Please put the book on the table by the window.",
    "We went to the market for a loaf of bread.",
    "She looked at the sky and waited for the rain.",
    "A friend of mine lives in a house on the hill.",
    "Do not leave the keys in the door after you go.",
    "He asked for a cup of tea with a slice of lemon.",
    "They walked to the station in the early morning fog.",
    "This is an example of a line that must wrap cleanly.",
    "Turn left at the corner and walk to the old church.",
    "The sound of the river was a comfort to the tired hikers.",
    "Keep the fire in the stove for the rest of the night.",
    "I would like to speak to the manager of the hotel.",
    "Put a lid on the pot and let it rest for a minute.",
    "The children ran to the garden to look at the flowers.",
    "Sin rarely destroys a person all at once.",
    "They still enter the room when the door is open.",
    "Welcome to the studio today we will learn how.",
    "Peace be with you let us begin this journey.",
    "Find its place among the quiet hills of home.",
]


def _seg(text, start, end):
    words = text.split()
    n = max(1, len(words))
    step = (end - start) / n
    wlist = []
    t = start
    for w in words:
        wlist.append({"word": w, "start": t, "end": t + step})
        t += step
    wlist[-1]["end"] = end
    return {
        "start": start, "end": end, "text": text, "lang": "en", "words": wlist,
    }


def _draw_sub(event, style, w, h):
    from PySide6.QtGui import QColor, QImage, QPainter
    from cutora.render.frame_renderer import FrameRenderer
    img = QImage(w, h, QImage.Format.Format_RGB888)
    img.fill(QColor("#3A4654"))
    p = QPainter(img)
    try:
        rect = FrameRenderer.draw_subtitle(p, event, style, 1.0, w, h)
    finally:
        p.end()
    return rect


def _assert_rect_in_margin(rect, w, h, extra=None):
    from cutora.subtitle.stylepack import SAFE_MARGIN
    mx, my = w * SAFE_MARGIN, h * SAFE_MARGIN
    x, y = float(rect.x()), float(rect.y())
    bw, bh = float(rect.width()), float(rect.height())
    info = (w, h, x, y, bw, bh, mx, my) + ((extra,) if extra else ())
    assert x >= mx - 1, info
    assert x + bw <= w - mx + 1, info
    assert y >= my - 1, info
    assert y + bh <= h - my + 1, info


def _assert_overlong_token_stays_in_margin(path="v2"):
    """One overlong token, fontsize_pct=0.10, three canvases: bbox in 8%."""
    token = "Antidisestablishmentarianismandmore"
    canvases = ((1080, 1920), (1920, 1080), (1080, 1080))
    for w, h in canvases:
        style = {
            "wrap_lines": 1, "fontsize_pct": 0.10, "font_family": "Arimo",
            "bold": True, "pos_x": w / 2.0, "pos_y": h * 0.75,
            "box_padding_x": 0, "box_padding_y": 0, "use_box": False,
            "use_stroke": False, "color_text": "#F4F7FB",
        }
        if path == "v2":
            style["fx"] = {}
        event = {
            "text": token, "start": 0.0, "end": 2.0, "lang": "en",
            "words": [{"word": token, "start": 0.0, "end": 2.0}],
        }
        rect = _draw_sub(event, style, w, h)
        assert rect is not None and not rect.isEmpty(), (path, w, h)
        _assert_rect_in_margin(rect, w, h, extra=(path, event.get("layout_warn")))
        warn = event.get("layout_warn") or event.get("_fit_warn")
        assert warn in ("shrink", "truncate"), (path, w, h, warn)
        if w <= 1080:
            assert warn == "truncate", (path, w, h, warn)


def _assert_render_no_extra_line(path="v2"):
    style = {
        "wrap_lines": 1, "fontsize": 96, "font_family": "Arimo",
        "bold": True, "pos_x": 540, "pos_y": 1600,
        "box_padding_x": 0, "box_padding_y": 0, "use_box": False,
    }
    event = {
        "lines": ["they still enter the"],
        "text": "they still enter the",
        "start": 0.0, "end": 2.0,
        "words": [
            {"word": w, "start": i * 0.4, "end": i * 0.4 + 0.4}
            for i, w in enumerate("they still enter the".split())
        ],
    }
    if path == "v2":
        style["fx"] = {}
        from cutora.render.textfx.layout import layout_event
        from cutora.render.textfx.spec import from_style
        lay = layout_event(event, style, from_style(style), 1080, 1920)
        assert len(lay.lines) == 1, (path, [ln.text for ln in lay.lines])
    rect = _draw_sub(event, style, 1080, 1920)
    assert rect is not None and not rect.isEmpty(), path
    assert float(rect.height()) < 96 * 2.2, (path, float(rect.height()))


def _assert_preset_lines_kept():
    from cutora.render.textfx.layout import _fit_event_lines
    from cutora.render.textfx.spec import from_style
    style = {
        "wrap_lines": 2, "fontsize": 28, "font_family": "Arimo", "bold": True,
    }
    preset = ["Hello friends.", "Welcome today."]
    event = {"lines": list(preset), "text": "Hello friends. Welcome today."}
    spec = from_style(style)
    fitted, em, warn = _fit_event_lines(
        event, style, spec, 28.0, "Arimo", 1080)
    assert fitted == preset, fitted
    assert warn is None
    v1 = {
        "wrap_lines": 2, "fontsize": 28, "font_family": "Arimo", "bold": True,
        "box_padding_x": 0, "box_padding_y": 0, "use_box": False,
        "pos_x": 540, "pos_y": 1400,
    }
    ev = {
        "lines": list(preset), "text": "Hello friends. Welcome today.",
        "start": 0.0, "end": 2.0,
        "words": [
            {"word": w, "start": 0.0, "end": 2.0}
            for w in "Hello friends Welcome today".split()
        ],
    }
    rect = _draw_sub(ev, v1, 1080, 1920)
    assert rect is not None and not rect.isEmpty()
    assert 28.0 < float(rect.height()) < 28.0 * 3.5, float(rect.height())


def _assert_relayout_max_lines(max_l, expect_eq=False):
    from cutora.subtitle.layout import LayoutRules, relayout_task
    from cutora.subtitle.stylepack import SAFE_MARGIN
    from cutora.render.textfx.layout import layout_event
    from cutora.render.textfx.spec import from_style
    segs = []
    t = 0.0
    for text in REAL_SEGS:
        segs.append(_seg(text, t, t + 2.0))
        t += 2.0
    orig_cover = sum(s["end"] - s["start"] for s in segs)
    t0, t1 = segs[0]["start"], segs[-1]["end"]
    task = {
        "timeline": {"width": 1080, "height": 1920},
        "style": {
            "wrap_lines": max_l, "fontsize": 96, "font_family": "Arimo",
            "bold": True, "pos_x": 540, "pos_y": 1400,
        },
        "layout_rules": {"max_chars_per_line": 42, "max_lines": max_l,
                         "balance_lines": True, "line_break": "space"},
        "segments": segs,
    }
    out = relayout_task(task, LayoutRules(max_lines=max_l, lang="en"))
    assert out["changed"] >= 1
    new = task["segments"]
    assert new[0]["start"] == t0
    assert abs(new[-1]["end"] - t1) < 1e-6
    cover = sum(max(0.0, s["end"] - s["start"]) for s in new)
    assert abs(cover - orig_cover) < 1e-3, (cover, orig_cover)
    ordered = sorted(new, key=lambda s: float(s["start"]))
    for a, b in zip(ordered, ordered[1:]):
        assert float(a["end"]) <= float(b["start"]) + 1e-4, (a, b)
    style = task["style"]
    spec = from_style(style)
    w, h = 1080, 1920
    mx, my = w * SAFE_MARGIN, h * SAFE_MARGIN
    for path in ("v1", "v2"):
        st = dict(style)
        st["box_padding_x"] = 0
        st["box_padding_y"] = 0
        st["use_box"] = False
        if path == "v2":
            st["fx"] = {}
        else:
            st.pop("fx", None)
        for seg in new:
            ev = dict(seg)
            if path == "v2":
                lay = layout_event(ev, st, spec, w, h)
                n = len(lay.lines)
                assert n <= max_l, (path, seg["text"], [ln.text for ln in lay.lines])
                if max_l == 1:
                    assert n == 1
                    assert lay.text_x >= mx - 8, (path, seg["text"], lay.text_x, mx)
                    assert lay.text_x + lay.text_w <= w - mx + 8, (
                        path, seg["text"], lay.text_x, lay.text_w, mx)
            rect = _draw_sub(ev, st, w, h)
            assert rect is not None and not rect.isEmpty(), (path, seg["text"])
            if max_l == 1:
                _assert_rect_in_margin(rect, w, h, extra=(path, seg["text"]))


def _assert_single_word_mode():
    from cutora.subtitle.layout import LayoutRules, relayout_task
    text = "they still enter the room today"
    task = {
        "timeline": {"width": 1080, "height": 1920},
        "style": {
            "wrap_lines": 1, "word_mode": "single", "fontsize": 48,
            "font_family": "Arimo",
        },
        "segments": [_seg(text, 0.0, 3.0)],
    }
    relayout_task(task, LayoutRules(max_lines=1, lang="en"))
    segs = task["segments"]
    assert len(segs) == len(text.split()), [s["text"] for s in segs]
    for s in segs:
        assert len(str(s["text"]).split()) == 1, s["text"]


def _assert_two_rulers_agree():
    # shrink/truncate 自 T06-2 起为合法行为，其边界由
    # tests/test_t06_linebreak.py::test_reflow_fits_width_and_converges
    # （scale ≥ 85% 下限；可见文本 ≠ 原文 ⇒ 必须以 … 结尾）和
    # tests/test_t06_linebreak.py::test_g09_seg3_ink_inside_safe_zone
    # （安全区像素锁）两条测试保证。本断言只负责 clip（硬裁、看不见地丢内容）。
    import random
    from cutora.subtitle.layout import LayoutRules, relayout_task
    from cutora.render.textfx.layout import layout_event
    from cutora.render.textfx.spec import from_style
    rng = random.Random(20260911)
    samples = REAL_SEGS[:]
    sizes = (28, 48, 72)
    n = 0
    for text in samples:
        for fs in sizes:
            if n >= 100:
                break
            task = {
                "timeline": {"width": 1080, "height": 1920},
                "style": {
                    "wrap_lines": 2, "fontsize": fs, "font_family": "Arimo",
                    "bold": True, "pos_x": 540, "pos_y": 1400,
                },
                "segments": [_seg(text, 0.0, 2.0)],
            }
            relayout_task(task, LayoutRules(max_lines=2, lang="en"))
            for path in ("v1", "v2"):
                st = dict(task["style"])
                if path == "v2":
                    st["fx"] = {}
                else:
                    st.pop("fx", None)
                for seg in task["segments"]:
                    ev = dict(seg)
                    if path == "v2":
                        lay = layout_event(
                            ev, st, from_style(st), 1080, 1920)
                        assert len(lay.lines) <= 2, (
                            path, text, fs, [ln.text for ln in lay.lines])
                    rect = _draw_sub(ev, st, 1080, 1920)
                    assert rect is not None and not rect.isEmpty(), (path, text, fs)
                    warn = ev.get("layout_warn") or ev.get("_fit_warn")
                    assert warn not in ("clip",), (
                        path, text, fs, warn, ev.get("lines"))
            n += 1
        if n >= 100:
            break
    assert n >= 60
    _ = rng
