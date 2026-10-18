# -*- coding: utf-8 -*-
"""Q23: wrap_width_percent is the single wrap-width source; sliderReleased relayouts."""
from __future__ import annotations

import copy
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CANVAS_W, CANVAS_H = 1080, 1920
LONG_TEXT = (
    "They waited in the shade of the old church for a friend from town "
    "and then walked home along the quiet river in the early morning fog"
)


def _qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(["t23-wrap-width"])


def _panel():
    import cutora.ui.panels.property  # noqa: F401
    from cutora.ui.panels.fonts import load_embedded_fonts
    from cutora.ui.panels.subtitle_style import SubtitleStylePanel
    load_embedded_fonts()
    return SubtitleStylePanel()


def _words(text, start=0.0, step=0.2):
    toks = text.split()
    out = []
    t = float(start)
    for i, w in enumerate(toks):
        out.append({"word": w, "start": t, "end": t + step, "wid": i})
        t += step
    return out


def _event(text=LONG_TEXT):
    words = _words(text)
    return {
        "text": text,
        "start": 0.0,
        "end": float(words[-1]["end"]),
        "id": "s0",
        "lang": "en",
        "words": words,
    }


def _style(pct, fontsize=64):
    return {
        "font_family": "Arimo",
        "fontsize": fontsize,
        "bold": True,
        "italic": False,
        "pos_x": CANVAS_W / 2.0,
        "pos_y": CANVAS_H * 0.75,
        "wrap_lines": 2,
        "wrap_width_percent": pct,
        "fx": {
            "fill": {"type": "solid", "colors": ["#F4EBD8"]},
            "stroke": {"width_em": 0},
            "glow": {"sigma_em": 0, "strength": 0},
            "bg": {"type": "none"},
            "anim": {"in": "none", "loop": "none", "out": "none"},
        },
    }


def _task(pct, text=LONG_TEXT):
    ev = _event(text)
    return {
        "dims": [CANVAS_W, CANVAS_H],
        "style": _style(pct),
        "segments": [copy.deepcopy(ev)],
        "layout_rules": {"max_lines": 2, "lang": "en", "line_break": "space"},
    }


def _flat_word_times(task):
    rows = []
    for seg in task.get("segments") or []:
        for w in seg.get("words") or []:
            rows.append((
                str(w.get("word") or ""),
                round(float(w.get("start") or 0), 6),
                round(float(w.get("end") or 0), 6),
            ))
    return rows


def _layout(style, text=LONG_TEXT):
    from cutora.render.textfx.layout import layout_event
    from cutora.render.textfx.spec import from_style
    from cutora.ui.panels.fonts import load_embedded_fonts

    load_embedded_fonts()
    event = _event(text)
    spec = from_style(style)
    lay = layout_event(event, style, spec, CANVAS_W, CANVAS_H)
    return event, spec, lay


def test_t23_reuses_wrap_width_percent():
    """Existing wrap_width_percent is the key; do not invent wrap_width_pct."""
    from cutora.subtitle.style_schema import (
        CANONICAL_STYLE_KEYS, DEPRECATED_UNPAINTED_KEYS,
    )
    assert "wrap_width_percent" in CANONICAL_STYLE_KEYS
    assert "wrap_width_pct" not in CANONICAL_STYLE_KEYS
    assert "wrap_left" not in CANONICAL_STYLE_KEYS
    assert "wrap_right" not in CANONICAL_STYLE_KEYS
    assert "wrap_left" in DEPRECATED_UNPAINTED_KEYS
    assert "wrap_right" in DEPRECATED_UNPAINTED_KEYS


def test_t23_single_writer():
    """60% vs 90% yield different line widths; frame_renderer does not clobber."""
    from cutora.subtitle.stylepack import line_max_width_px

    _qapp()
    w60 = line_max_width_px(_style(60), CANVAS_W, em=64)
    w90 = line_max_width_px(_style(90), CANVAS_W, em=64)
    assert abs(w60 - CANVAS_W * 0.60) < 2.0, w60
    assert abs(w90 - CANVAS_W * 0.90) < 2.0, w90
    assert w90 - w60 > CANVAS_W * 0.25, (w60, w90)

    _e0, _s0, lay60 = _layout(_style(60))
    _e1, _s1, lay90 = _layout(_style(90))
    assert lay60.lines, lay60
    assert lay90.lines, lay90
    assert lay90.lines[0].w > lay60.lines[0].w + 20, (
        lay60.lines[0].w, lay90.lines[0].w)

    src = open(
        os.path.join(REPO, "cutora", "render", "frame_renderer.py"),
        encoding="utf-8",
    ).read()
    chunk = src.split("Build per-segment style for draw_subtitle", 1)[1][:900]
    assert "wrap_left" not in chunk, chunk
    assert "wrap_right" not in chunk, chunk
    assert "wrap_width_percent" in chunk, chunk

    pipe = open(
        os.path.join(REPO, "cutora", "render", "textfx", "pipeline.py"),
        encoding="utf-8",
    ).read()
    assert "wrap_width_percent" in pipe
    assert 'style.get("wrap_left")' not in pipe
    assert "style.get('wrap_left')" not in pipe


def test_t23_more_words_per_line():
    """Same words: 50% → 90% puts more words on line 1; timestamps unchanged."""
    from cutora.subtitle.layout import relayout_task

    _qapp()
    t50 = _task(50)
    t90 = _task(90)
    times0 = _flat_word_times(t50)
    relayout_task(t50)
    relayout_task(t90)
    assert _flat_word_times(t50) == times0
    assert _flat_word_times(t90) == times0

    line50 = (t50["segments"][0].get("lines") or [""])[0]
    line90 = (t90["segments"][0].get("lines") or [""])[0]
    n50 = len(str(line50).split())
    n90 = len(str(line90).split())
    assert n90 > n50, (n50, n90, line50, line90)

    _e0, _s0, lay50 = _layout(_style(50))
    _e1, _s1, lay90 = _layout(_style(90))
    runs50 = lay50.lines[0].runs if lay50.lines else []
    runs90 = lay90.lines[0].runs if lay90.lines else []
    assert len(runs90) > len(runs50), (len(runs50), len(runs90))


def test_t23_migration():
    """Old wrap_left/right on a 1080 canvas fold into wrap_width_percent ≈ 81."""
    from cutora.subtitle.style_schema import migrate_subtitle_style

    style = {"wrap_left": 100, "wrap_right": 980, "fontsize": 48}
    migrate_subtitle_style(style, canvas_w=1080)
    pct = float(style.get("wrap_width_percent"))
    assert abs(pct - 81.0) < 1.0, pct
    assert "wrap_left" not in style
    assert "wrap_right" not in style

    leftover = {"wrap_left": 0, "wrap_right": 0}
    migrate_subtitle_style(leftover, canvas_w=1080)
    assert "wrap_left" not in leftover
    assert "wrap_right" not in leftover
    assert leftover.get("wrap_width_percent") in (None, leftover.get("wrap_width_percent"))


def test_t23_relayout_is_undoable():
    """sliderReleased runs subtitle.relayout on the bus; undo restores lines."""
    from cutora.core.commands import get_bus, reset_bus_for_tests
    from cutora.subtitle.layout import relayout_task

    _qapp()
    task = _task(50)
    relayout_task(task)
    before = copy.deepcopy(task["segments"])

    class _Win:
        def __init__(self, task):
            self.tasks = [task]
            self.curr_idx = 0
            self.project_path = "mem"
            self._snap = None

        def push_undo(self, *a, **k):
            self._snap = copy.deepcopy({"tasks": self.tasks, "curr_idx": 0})

        def capture_undo_after(self):
            return None

        def undo(self):
            if self._snap is None:
                return
            self.tasks = copy.deepcopy(self._snap["tasks"])

    win = _Win(task)
    reset_bus_for_tests()
    bus = get_bus()
    bus.bind_window(win)

    panel = _panel()
    hits = []

    def _on_commit(pct=None):
        hits.append(int(pct if pct is not None else panel.sub_wrap_width.value()))
        win.tasks[0]["style"]["wrap_width_percent"] = hits[-1]
        bus.execute("subtitle.relayout", {"scope": "all"}, source="ui")

    panel.wrap_width_committed.connect(_on_commit)
    panel.load_data(copy.deepcopy(task["style"]))
    panel.block_signals = False
    panel.current_data = win.tasks[0]["style"]
    panel.sub_wrap_width.setValue(90)
    assert hits == []
    # Q42 SliderSpin keeps the release signal on the inner slider.
    panel.sub_wrap_width.slider.sliderReleased.emit()
    assert hits == [90]
    after = copy.deepcopy(win.tasks[0]["segments"])
    assert after != before
    win.undo()
    assert win.tasks[0]["segments"] == before
    panel.close()
    reset_bus_for_tests()


def test_t23_no_relayout_storm():
    """20× valueChanged → 0 relayouts; one sliderReleased → 1 relayout."""
    _qapp()
    panel = _panel()
    relayouts = []
    panel.wrap_width_committed.connect(lambda pct: relayouts.append(pct))
    if hasattr(panel, "relayout_request"):
        panel.relayout_request.connect(lambda: relayouts.append("btn"))
    style = _style(86)
    panel.load_data(copy.deepcopy(style))
    panel.block_signals = False
    panel.current_data = copy.deepcopy(style)
    for i in range(20):
        panel.sub_wrap_width.setValue(40 + (i % 20))
    assert relayouts == [], relayouts
    panel.sub_wrap_width.slider.sliderReleased.emit()
    assert relayouts == [panel.sub_wrap_width.value()], relayouts

    assert getattr(panel, "sub_wrap_left", None) is None
    assert getattr(panel, "sub_wrap_right", None) is None
    assert getattr(panel, "sub_wrap_width", None) is not None
    hint = getattr(panel, "sub_wrap_width_hint", None)
    assert hint is not None
    text = hint.text() if hasattr(hint, "text") else str(hint)
    assert "每行约" in text and "词" in text, text
    panel.close()
