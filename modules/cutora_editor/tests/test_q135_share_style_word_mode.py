# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q135 · 媒体库右键「共享字幕风格」：单字蹦没同步到目标视频的字幕条。

word_mode / 行数 / 宽度 决定一条字幕装几个词；以前共享只换 style，
目标视频的字幕还是一整行。共享后要按新排版重切。"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

TEXT = "When God is leading a woman toward success"


def _app():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(["q135"])
    from cutora.ui.panels.fonts import load_embedded_fonts
    load_embedded_fonts()
    return app


def _task(style):
    words = [{"word": w, "start": i * 0.5, "end": i * 0.5 + 0.4}
             for i, w in enumerate(TEXT.split())]
    return {"dims": (1080, 1920), "style": dict(style),
            "segments": [{"start": 0.0, "end": 4.0, "text": TEXT, "words": words}]}


def _mw():
    from cutora.ui.main_window import MainWindow
    return MainWindow


def test_share_single_word_mode_resplits_targets(monkeypatch):
    _app()
    from cutora.subtitle import layout
    monkeypatch.setattr(layout, "auto_relayout_enabled", lambda settings=None: True)
    from cutora.subtitle.stylepack import default_subtitle_style
    base = default_subtitle_style()
    target = _task(base)
    new = dict(base)
    new["word_mode"] = "single"
    target["style"] = new
    _mw()._relayout_after_style_copy(target, base, new)
    assert len(target["segments"]) == len(TEXT.split())
    assert [s["text"].strip() for s in target["segments"]] == TEXT.split()


def test_share_same_layout_does_not_relayout(monkeypatch):
    _app()
    from cutora.subtitle import layout
    monkeypatch.setattr(layout, "auto_relayout_enabled", lambda settings=None: True)
    calls = []
    monkeypatch.setattr(layout, "maybe_auto_relayout",
                        lambda task, key=None, settings=None: calls.append(key))
    from cutora.subtitle.stylepack import default_subtitle_style
    base = default_subtitle_style()
    target = _task(base)
    new = dict(base)
    new["font_style"] = base.get("font_style")      # 纯画法参数不影响断句
    _mw()._relayout_after_style_copy(target, base, new)
    assert calls == []
    new2 = dict(base)
    new2["word_mode"] = "single"
    _mw()._relayout_after_style_copy(target, base, new2)
    assert calls == ["word_mode"]


def test_copy_subtitle_style_calls_relayout():
    import inspect
    src = inspect.getsource(_mw().copy_subtitle_style)
    assert "_relayout_after_style_copy" in src
