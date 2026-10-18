# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q139 · 字幕画面上出现前一个词（「Bible」画成「Does」）。

根因：单字蹦切出来的每个词继承母句 id；字幕光栅缓存按 (id, 样式, 框尺寸)
命中，不看正文 —— 框一样大的两个词就拿到对方的图。三层堵死：
1. 光栅缓存 key 带上正文；2. 切分出来的每条字幕发新 id；
3. 写回 / 加载时把重复 id 补成新的。"""
from __future__ import annotations

import hashlib
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

WORDS = ["What", "Does", "the", "Bible", "Really", "Say?", "Amen", "Jesus", "Bible", "Does"]


def _app():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(["q139"])
    from cutora.ui.panels.fonts import load_embedded_fonts
    load_embedded_fonts()
    return app


def _render(seg, style, t):
    from PySide6.QtGui import QColor, QImage, QPainter
    from cutora.render.frame_renderer import FrameRenderer
    img = QImage(1080, 1920, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(QColor(30, 30, 30))
    p = QPainter(img)
    try:
        FrameRenderer.draw_subtitle(p, seg, style, t, 1080, 1920, seg_idx=0)
    finally:
        p.end()
    return hashlib.md5(img.constBits().tobytes()).hexdigest()


def test_shared_ids_never_paint_the_other_word():
    _app()
    from cutora.render.textfx import clear_roi_cache
    from cutora.subtitle.stylepack import default_subtitle_style
    style = default_subtitle_style()
    style["fontsize"] = 90
    style["word_mode"] = "single"
    segs = [{"id": "same-id", "start": i, "end": i + 1, "text": w,
             "words": [{"word": w, "start": i, "end": i + 1}]} for i, w in enumerate(WORDS)]
    clear_roi_cache()
    shared = [_render(s, style, s["start"] + 0.3) for s in segs]
    clear_roi_cache()
    own = [_render(dict(s, id="id-%d" % i), style, s["start"] + 0.3) for i, s in enumerate(segs)]
    assert shared == own
    # 同一个词两处出现应当相同，不同词必须不同
    assert own[3] == own[8] and own[1] == own[9]
    assert own[1] != own[3] != own[0]


def test_split_parts_get_their_own_ids():
    _app()
    from cutora.subtitle.layout import LayoutRules, relayout_task
    from cutora.subtitle.stylepack import default_subtitle_style
    text = " ".join(WORDS[:6])
    words = [{"word": w, "start": i * 0.4, "end": i * 0.4 + 0.3} for i, w in enumerate(WORDS[:6])]
    style = default_subtitle_style()
    style["word_mode"] = "single"
    task = {"dims": (1080, 1920), "style": style,
            "segments": [{"id": "mother", "start": 0.0, "end": 2.4, "text": text, "words": words}]}
    relayout_task(task, LayoutRules(max_chars_per_line=42, max_lines=2))
    ids = [s["id"] for s in task["segments"]]
    assert len(task["segments"]) == 6
    assert ids[0] == "mother"
    assert len(set(ids)) == len(ids)


def test_ensure_segment_ids_dedupes_and_set_segments_applies_it():
    from cutora.subtitle.seg_ids import ensure_segment_ids
    from cutora.subtitle.track_source import set_segments
    segs = [{"id": "dup", "text": "a"}, {"id": "dup", "text": "b"}, {"text": "c"}, {"id": "dup", "text": "d"}]
    n = ensure_segment_ids({"segments": segs})
    assert n == 3
    assert len({s["id"] for s in segs}) == 4 and segs[0]["id"] == "dup"
    task = {"segments": []}
    out = set_segments(task, [{"id": "x", "text": "1"}, {"id": "x", "text": "2"}])
    assert out[0]["id"] == "x" and out[1]["id"] != "x"
