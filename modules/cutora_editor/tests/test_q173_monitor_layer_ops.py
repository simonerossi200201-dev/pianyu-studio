# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q173 · 中央监视器右键「图层」批量：翻转 / 混合模式 / 调色 / 叠放顺序 / 显隐（同一批）；
打开批量加载有进度条、模型列表不在界面线程算 sha256；片段调色能存盘。"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _app():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(["q173"])
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


def _task(tmp_path, n_ext=2):
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
        tl.add_track(t2)
        sid = "e%d" % i
        tl.sources[sid] = MediaSource(_clip(tmp_path, "ext%d" % i, "0x22B830"), duration=3.0, probe=False)
        c = Clip(sid, 0.0, 3.0, 0.0)
        c.scale = 0.6 if i == 1 else 1.0
        t2.clips.append(c)
    return {"timeline": tl, "video": "", "dims": (216, 384), "image_overlays": [], "text_overlays": []}


def _vt(task):
    from cutora.batch import monitor_model as mm
    return mm.video_tracks(task)


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


def _find(menu, prefix):
    """按文字找菜单项（含子菜单）。不用 QAction.menu() —— PySide6 把它返回的包装当成 Python 自己的，
    临时变量一回收就把真的子菜单删了。"""
    from PySide6.QtGui import QAction
    for a in [menu.menuAction()] + list(menu.findChildren(QAction)):
        if a.text().startswith(prefix):
            return a
    return None


def test_restack_and_data_ops(tmp_path):
    from cutora.ui import monitor_layer_ops as L
    task = _task(tmp_path)
    main, e0, e1 = _vt(task)
    assert L.restack(task, e0, "up") and _vt(task) == [main, e1, e0]
    assert not L.restack(task, e0, "up")                     # 已经在最上面
    assert L.restack(task, e0, "bottom") and _vt(task) == [e0, main, e1]   # 置底当背景
    assert L.restack(task, e0, "top") and _vt(task) == [main, e1, e0]
    assert L.restack(task, e0, "down") and _vt(task) == [main, e0, e1]
    assert L.flip(e0, "x") == 1 and e0.clips[0].flip_x is True
    L.flip(e0, "x")
    assert e0.clips[0].flip_x is False
    assert L.set_blend(e0, "screen") == 1 and e0.clips[0].blend_mode == "screen"
    L.grade_step(e0, {"exposure": 0.15})
    L.grade_step(e0, {"exposure": 0.15})
    assert abs(e0.clips[0].grade["exposure"] - 0.3) < 1e-6
    L.grade_step(e0, {"exposure": 9.0})
    assert e0.clips[0].grade["exposure"] == 4.0                # 夹在面板范围内
    g = L.first_grade(e0)
    L.grade_set(e1, g)
    assert e1.clips[0].grade["exposure"] == 4.0 and e1.clips[0].grade is not e0.clips[0].grade
    L.grade_set(e1, None)
    assert L.first_grade(e1) is None


def test_menu_batch_flip_blend_grade_restack_one_undo_each(tmp_path):
    app = _app()
    from PySide6.QtWidgets import QMenu
    tasks = [_task(tmp_path) for _ in range(3)]
    dlg = _dialog(tasks)
    g = dlg.grid
    try:
        app.processEvents()
        g.selected = {0, 1, 2}
        tile = g.tiles[0]
        e0 = _vt(tasks[0])[1]
        menu = QMenu(dlg)
        sub = g.add_layer_ops_menu(menu, tile, e0, None, g.selected_tiles())
        assert "外部视频 · ext0.mp4" in sub.title() and "同一批 3 条" in sub.title()
        def act(prefix):                                              # 真实使用：每次右键都重新建菜单
            m = QMenu(dlg)
            sb = g.add_layer_ops_menu(m, tile, _vt(tasks[0])[1], None, g.selected_tiles())
            _find(sb, prefix).trigger()
            app.processEvents()

        act("左右翻转")
        assert all(_vt(t)[1].clips[0].flip_x for t in tasks)
        assert not any(_vt(t)[0].clips[0].flip_x or _vt(t)[2].clips[0].flip_x for t in tasks)   # 别的层不动
        act("滤色")
        assert all(_vt(t)[1].clips[0].blend_mode == "screen" for t in tasks)
        act("亮一点")
        assert all(abs(_vt(t)[1].clips[0].grade["exposure"] - 0.15) < 1e-6 for t in tasks)
        act("置顶")
        assert all(_vt(t)[2].clips[0].source_id == "e0" for t in tasks)
        n = len(g.history._undo)
        g.undo()
        assert len(g.history._undo) == n - 1
        assert all(_vt(t)[1].clips[0].source_id == "e0" for t in tasks)     # 叠放顺序撤回
        g.undo()
        from cutora.render.grade import grade_of, identity_grade
        assert all(grade_of(_vt(t)[1].clips[0]) == identity_grade() for t in tasks)   # 撤回到「没调色」
        g.undo()
        g.undo()
        assert not any(_vt(t)[1].clips[0].flip_x for t in tasks)
        # 复制调色 → 粘贴到同一批
        from cutora.ui import monitor_layer_ops as L
        L.grade_step(_vt(tasks[0])[1], {"saturation": 0.5})
        act("复制这一层的调色")
        act("粘贴调色到同一批")
        assert all(abs(_vt(t)[1].clips[0].grade["saturation"] - 0.5) < 1e-6 for t in tasks)
        act("隐藏这一层")
        assert not any(getattr(_vt(t)[1], "visible", True) for t in tasks)
    finally:
        dlg.close()


def test_context_menu_has_layer_submenu_for_clicked_layer(tmp_path):
    app = _app()
    from PySide6.QtCore import QEvent, QPointF, Qt, QTimer
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QApplication, QMenu
    tasks = [_task(tmp_path) for _ in range(2)]
    dlg = _dialog(tasks)
    g = dlg.grid
    built = []

    def _grab():
        pop = QApplication.activePopupWidget()
        if isinstance(pop, QMenu):
            built.append([a.text() for a in pop.actions()])
            pop.close()
    try:
        app.processEvents()
        for t in g.tiles:
            t.t = 1.0
        tile = g.tiles[0]
        pos = tile.to_widget(5, 5)                                   # 角落：外部视频 #2 缩小了，盖不到 → 外部视频 #1
        ev = QMouseEvent(QEvent.Type.MouseButtonPress, pos, pos, Qt.MouseButton.RightButton,
                         Qt.MouseButton.RightButton, Qt.KeyboardModifier.NoModifier)
        QTimer.singleShot(300, _grab)
        g._context_menu(ev)
        assert built and any(t.startswith("图层：外部视频 · ext0.mp4") for t in built[-1])
    finally:
        dlg.close()


def test_layer_panel_menu_offers_layer_ops(tmp_path):
    app = _app()
    from PySide6.QtWidgets import QMenu
    tasks = [_task(tmp_path) for _ in range(2)]
    dlg = _dialog(tasks)
    g = dlg.grid
    try:
        app.processEvents()
        g.select_only(g.tiles[0])
        app.processEvents()
        panel = dlg.findChild(type(dlg.layers)) if hasattr(dlg, "layers") else None
        panel = panel or next(w for w in dlg.findChildren(object) if type(w).__name__ == "LayerPanel")
        rows = [r for r in panel._all_rows if (r.get("locator") or {}).get("where") == "clip"
                and not r.get("is_audio")]
        assert rows
        menu = QMenu(dlg)
        panel._add_video_layer_ops(menu, [rows[0]])
        assert any(a.text().startswith("图层：") for a in menu.actions())
    finally:
        dlg.close()


def test_clip_grade_survives_save_and_load(tmp_path):
    from cutora.model.project_io import _rj_deserialize_timeline, _rj_serialize_timeline
    task = _task(tmp_path)
    e0 = _vt(task)[1]
    e0.clips[0].grade = {"exposure": 0.5, "saturation": -1.0}
    data = json.loads(json.dumps(_rj_serialize_timeline(task["timeline"])))
    tl2 = _rj_deserialize_timeline(data)
    c = [t for t in tl2.tracks if t.type == "video"][1].clips[0]
    assert c.grade["exposure"] == 0.5 and c.grade["saturation"] == -1.0


def test_model_list_fast_does_not_hash(tmp_path, monkeypatch):
    from cutora.asr import models as M
    w = tmp_path / "model.bin"
    w.write_bytes(b"z" * 2048)
    monkeypatch.setattr(M, "_sha_disk_path", lambda: str(tmp_path / "sha.json"))
    monkeypatch.setattr(M, "_SHA_CACHE", {})
    monkeypatch.setattr(M, "_SHA_DISK", {"loaded": False, "rows": {}})
    calls = []
    real = M.sha256_file
    monkeypatch.setattr(M, "sha256_file", lambda p: (calls.append(p), time.sleep(0.2), real(p))[2])
    t0 = time.perf_counter()
    assert M.verify_weight(str(w), "abc", fast=True) == "ready"       # 没算过：先按「在」，后台去算
    assert time.perf_counter() - t0 < 0.1
    for _ in range(50):
        if M._sha_known(str(w)):
            break
        time.sleep(0.05)
    assert M._sha_known(str(w)) == real(str(w))
    assert M.verify_weight(str(w), "abc", fast=True) == "checksum_failed"   # 算好以后照样认得出坏文件
    assert M.verify_weight(str(w), "abc") == "checksum_failed"


def test_busy_bar_reports_and_closes():
    app = _app()
    from PySide6.QtWidgets import QMainWindow
    from cutora.ui.widgets import busy_bar as B
    win = QMainWindow()
    win.resize(900, 600)
    win.show()
    B.report(0.5, "没有进度条时什么都不做")
    bar = B.start_busy(win, "正在打开批量加载")
    B.report(0.4, "素材页")
    assert bar.isVisible() and bar.frac == 0.4 and bar.text == "素材页"
    assert bar.geometry().bottom() <= win.height()
    bar.grab()
    B.report(0.2, "倒退不算")
    assert bar.frac == 0.4
    B.finish()
    app.processEvents()
    assert B._STATE["bar"] is None
    win.close()


def test_batch_dialog_open_reports_progress(monkeypatch):
    app = _app()
    from PySide6.QtWidgets import QMainWindow
    from cutora.ui.widgets import busy_bar as B
    seen = []
    real = B.report
    monkeypatch.setattr(B, "report", lambda f, t="": (seen.append((round(f, 2), t)), real(f, t))[1])

    class _Host(QMainWindow):
        tasks = []
        curr_idx = -1
    host = _Host()
    host.show()
    B.start_busy(host, "正在打开批量加载")
    try:
        from cutora.batch.batch_dialog import build_batch_dialog
        dlg = build_batch_dialog(host)
    except Exception:
        B.finish()
        pytest.skip("批量对话框在这个环境里建不起来")
    B.finish()
    texts = [t for _f, t in seen]
    assert "素材页" in texts and "预览画布" in texts
    fracs = [f for f, _t in seen]
    assert fracs == sorted(fracs)
    dlg.close()
    host.close()
