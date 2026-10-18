# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q132 · 桌面图标（CUTORA.vbs → pythonw -m cutora）不经过 CUTORA.bat，
PATH 里没有 runtime/ffmpeg；whisperx.load_audio 裸调「ffmpeg」→ WinError 2。
config 导入时就把内置 ffmpeg 放到 PATH 最前面。"""
from __future__ import annotations

import os


def test_ensure_ffmpeg_on_path_prepends_once(tmp_path, monkeypatch):
    from cutora.core import config
    exe = tmp_path / "ffmpeg" / "ffmpeg.exe"
    exe.parent.mkdir()
    exe.write_bytes(b"")
    monkeypatch.setattr(config, "local_ffmpeg", str(exe))
    other = os.pathsep.join(["C:\\Windows\\system32", str(exe.parent), "C:\\x"])
    monkeypatch.setenv("PATH", other)
    config.ensure_ffmpeg_on_path()
    parts = os.environ["PATH"].split(os.pathsep)
    assert parts[0] == str(exe.parent)
    assert parts.count(str(exe.parent)) == 1
    before = os.environ["PATH"]
    config.ensure_ffmpeg_on_path()
    assert os.environ["PATH"] == before


def test_no_bundled_ffmpeg_leaves_path_alone(monkeypatch):
    from cutora.core import config
    monkeypatch.setattr(config, "local_ffmpeg", None)
    monkeypatch.setenv("PATH", "A" + os.pathsep + "B")
    config.ensure_ffmpeg_on_path()
    assert os.environ["PATH"] == "A" + os.pathsep + "B"


def test_config_import_runs_it():
    import inspect
    from cutora.core import config
    src = inspect.getsource(config)
    assert "\nensure_ffmpeg_on_path()\n" in src
