# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q172 · 中央监视器不分屏时：拖 / 缩放的是点中的那一层（外部视频），同步编辑按「同一批」跟着动。

以前不分屏一律改「媒体库主视频」轨 —— 被铺满的外部绿幕视频盖着看不见，外部视频本身不动，
主视频却被悄悄挪走 / 缩小；回到主窗口把外部视频缩小，才露出主视频「是黑的」（其实是被挪出画面了）。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _app():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(["q172"])
    from cutora.ui.panels.fonts import load_embedded_fonts
    load_embedded_fonts()
    return app


def _clip(tmp_path, name, color):
    from cutora.core.config import FFMPEG_PATH
    ff = FFMPEG_PATH if os.path.isfile(FFMPEG_PATH) else shutil.which("ffmpeg")
    if not ff:
        pytest.skip("no ffmpeg")
    out = str(tmp_path / ("%s.mp4" % name))
    if not os.path.isfile(out):
        subprocess.run([ff, "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=%s:s=216x384:r=30:d=3" % color,
                        "-pix_fmt", "yuv420p", out], check=True)
    return out


def _task(tmp_path, n_ext=1):
    from cutora.model.timeline import Timeline
    from cutora.model.timeline_model import Clip, MediaSource, Track
    tl = Timeline(216, 384, 30)
    tr = Track("video")
    tr.is_main_video = True
    tl.add_track(tr)
    tl.sources["m"] = MediaSource(_clip(tmp_path, "main", "0x3070C0"), duration=3.0, probe=False)
    tr.clips.append(Clip("m", 0.0, 3.0, 0.0))
    for i in range(n_ext):
        t2 = Track("video")
        t2.is_main_video = False
        tl.add_track(t2)
        sid = "e%d" % i
        tl.sources[sid] = MediaSource(_clip(tmp_path, "ext%d" % i, "0x22B830"), duration=3.0, probe=False)
        c = Clip(sid, 0.0, 3.0, 0.0)
        c.fit_mode = "fill"
        t2.clips.append(c)
    return {"timeline": tl, "video": "", "dims": (216, 384), "image_overlays": [], "text_overlays": []}


def _main(task):
    return task["timeline"].tracks[0]


def _ext(task, i=0):
    return task["timeline"].tracks[1 + i]


def _dialog(tasks):
    from cutora.ui.central_monitor import CentralMonitorDialog

    class _Host:
        pass
    host = _Host()
    host.tasks = tasks
    dlg = CentralMonitorDialog(host, tasks)
    dlg.resize(1200, 800)
    dlg.show()
    return dlg


def test_hit_picks_topmost_layer_not_main(tmp_path):
    from cutora.batch import monitor_model as mm
    task = _task(tmp_path)
    tr, cid, _r = mm.content_target(task, 108, 192, 1.0)
    assert tr is _ext(task) and cid is None                 # 铺满的外部视频在最上面 → 点中的是它
    ext = _ext(task).clips[0]
    ext.scale = 0.5
    assert mm.content_target(task, 108, 192, 1.0)[0] is _ext(task)
    assert mm.content_target(task, 5, 5, 1.0)[0] is _main(task)    # 缩小后露出来的角落 = 主视频
    ext.scale = 1.0
    _ext(task).locked = True                                        # 锁住 / 隐藏的层点不中，落到下面
    assert mm.content_target(task, 108, 192, 1.0)[0] is _main(task)
    _ext(task).locked = False
    _ext(task).visible = False
    assert mm.content_target(task, 108, 192, 1.0)[0] is _main(task)
    _ext(task).visible = True
    assert mm.content_target(task, 108, 192, 5.0)[0] is _main(task)    # 片段不在这个时刻 → 不算，退回主视频


def test_layer_slot_is_batch_identity(tmp_path):
    from cutora.batch import monitor_model as mm
    a, b = _task(tmp_path, 2), _task(tmp_path, 2)
    assert mm.layer_slot(a, _main(a)) == ("main",)
    assert mm.layer_slot(a, _ext(a, 1)) == ("ext", 1)
    assert mm.track_for_slot(b, ("ext", 1)) is _ext(b, 1)
    assert mm.track_for_slot(b, ("main",)) is _main(b)
    assert mm.track_for_slot(b, ("ext", 5)) is None
    assert mm.track_display_name(a, _ext(a, 0)) == "ext0.mp4"


def _drag(app, g, tile, cx, cy, dx, dy, ctrl=False):
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtTest import QTest
    mod = Qt.KeyboardModifier.ControlModifier if ctrl else Qt.KeyboardModifier.NoModifier
    p0 = tile.to_widget(cx, cy)
    QTest.mousePress(g, Qt.MouseButton.LeftButton, mod, p0.toPoint())
    for i in range(1, 9):
        QTest.mouseMove(g, (p0 + QPointF(dx * i / 8.0, dy * i / 8.0)).toPoint())
    QTest.mouseRelease(g, Qt.MouseButton.LeftButton, mod, (p0 + QPointF(dx, dy)).toPoint())
    app.processEvents()


def test_drag_moves_external_and_syncs_same_batch(tmp_path):
    app = _app()
    tasks = [_task(tmp_path) for _ in range(3)]
    dlg = _dialog(tasks)
    g = dlg.grid
    try:
        app.processEvents()
        for t in g.tiles:
            t.t = 1.0
        g.link_edit = True
        g.selected = {0, 1, 2}
        tile = g.tiles[0]
        _drag(app, g, tile, 108, 192, 40, 0)
        xs = [_ext(t).clips[0].x for t in tasks]
        assert xs[0] > 10 and all(abs(x - xs[0]) < 1e-6 for x in xs), xs   # 三条的外部视频一起挪
        assert all(_main(t).clips[0].x == 0.0 and _main(t).clips[0].scale == 1.0 for t in tasks)  # 主视频没被动
        # Ctrl + 拖 = 缩放，同一批一起缩
        _drag(app, g, tile, 108, 192, 40, -40, ctrl=True)
        ss = [_ext(t).clips[0].scale for t in tasks]
        assert ss[0] > 1.2 and all(abs(s - ss[0]) < 1e-6 for s in ss), ss
        assert all(_main(t).clips[0].scale == 1.0 for t in tasks)
        g.undo()
        g.undo()
        assert all(_ext(t).clips[0].x == 0.0 and _ext(t).clips[0].scale == 1.0 for t in tasks)
    finally:
        dlg.close()


def test_without_sync_only_this_tile_and_hint_names_layer(tmp_path):
    app = _app()
    tasks = [_task(tmp_path) for _ in range(2)]
    dlg = _dialog(tasks)
    g = dlg.grid
    msgs = []
    g.status.connect(msgs.append)
    try:
        app.processEvents()
        for t in g.tiles:
            t.t = 1.0
        g.link_edit = False
        g.selected = {0, 1}
        _drag(app, g, g.tiles[0], 108, 192, 30, 0)
        assert _ext(tasks[0]).clips[0].x > 10 and _ext(tasks[1]).clips[0].x == 0.0
        tile, kind, payload = g.hit(g.tiles[1].to_widget(108, 192))
        assert "外部视频 ext0.mp4" in g._hover_hint(kind, payload, tile)
    finally:
        dlg.close()


def test_double_click_reset_targets_same_layer(tmp_path):
    app = _app()
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    tasks = [_task(tmp_path) for _ in range(2)]
    for t in tasks:
        _ext(t).clips[0].x = 30.0
        _main(t).clips[0].x = 12.0
    dlg = _dialog(tasks)
    g = dlg.grid
    try:
        app.processEvents()
        for t in g.tiles:
            t.t = 1.0
        g.link_edit = True
        g.selected = {0, 1}
        g.tiles[0].t = 1.0
        p = g.tiles[0].to_widget(108 + 30, 192)
        QTest.mouseDClick(g, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, p.toPoint())
        app.processEvents()
        # 重置的是外部视频那一层（轨道变换），主视频片段的位置不动
        assert _ext(tasks[0]).clips[0].x == 0.0 and _ext(tasks[1]).clips[0].x == 0.0
        assert _main(tasks[0]).clips[0].x == 12.0 and _main(tasks[1]).clips[0].x == 12.0
    finally:
        dlg.close()


def test_model_sha_cache_survives_restart(tmp_path, monkeypatch):
    """打开「批量」对话框时校验模型权重：算过一次的 sha256 记在磁盘上，重开软件不再重算（以前卡 14 秒）。"""
    from cutora.asr import models as M
    w = tmp_path / "w.bin"
    w.write_bytes(b"x" * 4096)
    monkeypatch.setattr(M, "_sha_disk_path", lambda: str(tmp_path / "sha.json"))
    monkeypatch.setattr(M, "_SHA_CACHE", {})
    monkeypatch.setattr(M, "_SHA_DISK", {"loaded": False, "rows": {}})
    calls = []
    real = M.sha256_file
    monkeypatch.setattr(M, "sha256_file", lambda p: (calls.append(p), real(p))[1])
    d1 = M._sha256_cached(str(w))
    assert len(calls) == 1
    monkeypatch.setattr(M, "_SHA_CACHE", {})                          # 模拟重开软件
    monkeypatch.setattr(M, "_SHA_DISK", {"loaded": False, "rows": {}})
    assert M._sha256_cached(str(w)) == d1 and len(calls) == 1
    w.write_bytes(b"y" * 4097)                                       # 文件变了就重算
    M._sha256_cached(str(w))
    assert len(calls) == 2
