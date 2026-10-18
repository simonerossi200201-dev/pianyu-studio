# -*- coding: utf-8 -*-
"""A2: timeline 自动字幕 renamed 重新识别; confirm mentions stitch already has subs."""
from __future__ import annotations

import os

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_reidentify_label_and_confirm_in_source():
    src = open(
        os.path.join(REPO, "cutora", "ui", "main_window.py"), encoding="utf-8"
    ).read()
    assert 'QAction(tr("重新识别")' in src
    assert 'QPushButton(tr("② 重新识别")' in src
    assert "拼接已经带上字幕" in src
    assert "确定重新识别" in src
    assert 'QAction(tr("自动字幕")' not in src
    assert 'QPushButton(tr("② 自动字幕")' not in src
