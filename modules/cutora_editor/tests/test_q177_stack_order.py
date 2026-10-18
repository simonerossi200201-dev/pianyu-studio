# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q177 · 叠压顺序一套到底：共享轨道保持原来的叠压位置；图层面板拖动 = 时间线轨道顺序；
中央监视器右键「当前图层」：它自己的属性（同一批一起改）+ 叠放顺序上移 / 下移 / 置顶 / 置底。"""
from __future__ import annotations

import os
import sys
import tempfile

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("CUTORA_SKIP_ENCODER_PROBE", "1")
os.environ.setdefault("CUTORA_USER_DATA", tempfile.mkdtemp(prefix="cutora_q177_"))
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _app():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(["q177"])
    from cutora.ui.panels.fonts import load_embedded_fonts
    load_embedded_fonts()
    return app


def _types(tl):
    return [t.type for t in tl.tracks]


# ───────────────────────────── 共享轨道保持叠压位置 ─────────────────────────────

def test_place_shared_track_keeps_stack_position():
    from cutora.model.timeline import Timeline
    from cutora.model.timeline_model import Track
    from cutora.ui.main_window import place_shared_track
    src = Timeline(320, 180, 25)
    v1, adj, img, v2, txt, au = (Track("video"), Track("adjust"), Track("image"), Track("video"),
                                 Track("text"), Track("audio"))
    src.tracks[:] = [v1, adj, img, v2, txt, au]
    dst = Timeline(320, 180, 25)
    m, g, t2, a2 = Track("video"), Track("video"), Track("text"), Track("audio")
    dst.tracks[:] = [m, g, t2, a2]
    new_adj = Track("adjust")
    place_shared_track(src, adj, dst, new_adj)
    assert dst.tracks == [m, new_adj, g, t2, a2]               # 源里在第一条视频上面 → 目标里也一样
    # 同一次共享多条：后放的以先放的为准
    placed = {id(adj): new_adj}
    new_img = Track("image")
    place_shared_track(src, img, dst, new_img, placed)
    assert dst.tracks == [m, new_adj, new_img, g, t2, a2]
    # 源里在最底下 → 目标里画面最底下；声音轨放声音那一域
    src2 = Timeline(320, 180, 25)
    bottom = Track("image")
    src2.tracks[:] = [bottom, Track("video")]
    nb = Track("image")
    place_shared_track(src2, bottom, dst, nb)
    assert dst.tracks[0] is nb
    na = Track("audio")
    place_shared_track(src, au, dst, na)
    assert dst.tracks.index(na) > dst.tracks.index(t2)


def test_share_tracks_command_keeps_order():
    from test_q145b_timeline_shortcuts import _load, _task
    win, _tl, src = _load()
    win.timer.stop()
    win.add_adjust_item(start=0.0, dur=8.0)
    src = win.tasks[win.curr_idx]
    tl = src["timeline"]
    atr = next(t for t in tl.tracks if t.type == "adjust")
    tl.tracks.remove(atr)
    tl.tracks.insert(1, atr)                                   # 视频 → 调整 → 文字 → 图片 → 字幕
    atr.enable_batch = True
    dst = _task()
    win.tasks.append(dst)
    try:
        win.command_share_tracks(0, [1], track_ids=[str(atr.id)])
        types = _types(dst["timeline"])
        assert types.index("adjust") == types.index("video") + 1, types
    finally:
        win.tasks.pop()


# ───────────────────────────── 时间线轨道顺序 = 图层面板顺序 ─────────────────────────────

def _layer_task():
    from cutora.model.timeline import Timeline
    from cutora.model.timeline_model import Clip, Track
    tl = Timeline(320, 180, 25)
    bg = Track("video")
    bg.bg_role = "backdrop"
    v = Track("video")
    v.is_main_video = True
    v.clips.append(Clip("s", 0.0, 2.0, 0.0))
    txt, img, adj, au = Track("text"), Track("image"), Track("adjust"), Track("audio")
    au.clips.append(Clip("s", 0.0, 2.0, 0.0))
    tl.tracks[:] = [bg, v, txt, img, adj, au]
    t1 = {"type": "text", "content": "A", "start": 0, "end": 2, "_track_id": txt.id, "x": 0, "y": 0, "w": 50, "h": 20}
    t2 = {"type": "text", "content": "B", "start": 0, "end": 2, "_track_id": txt.id, "x": 0, "y": 0, "w": 50, "h": 20}
    im = {"type": "image", "start": 0, "end": 2, "_track_id": img.id, "elements": [{"path": "x.png", "x": 0,
                                                                                   "y": 0, "w": 9, "h": 9}]}
    from cutora.render import adjust_layer as AL
    ad = AL.adjust_defaults(320, 180, start=0, end=2, track_id=adj.id)
    task = {"timeline": tl, "text_overlays": [t1, t2], "image_overlays": [im, ad], "segments": [], "style": {},
            "dims": [320, 180], "duration": 2.0}
    return task, dict(bg=bg, v=v, txt=txt, img=img, adj=adj, au=au, t1=t1, t2=t2, im=im, ad=ad)


def test_rows_follow_track_stack_and_reorder_moves_tracks():
    from cutora.batch import layer_model as lm
    task, n = _layer_task()
    rows = lm.rows(task)
    kinds = [r["kind"] for r in rows]
    assert kinds[0] == "adjust" and kinds[-1] == "audio"        # 最上面的轨道排第一，声音最后
    assert kinds.index("image") < kinds.index("text") < kinds.index("video")
    texts = [lm.resolve(task, r["locator"])["content"] for r in rows if r["kind"] == "text"]
    assert texts == ["B", "A"]                                 # 同一条轨道里后画的在前
    # 把文字拖到调整图层上面 → 文字轨挪到调整轨上面
    row_t = next(r for r in rows if r["kind"] == "text")
    row_a = next(r for r in rows if r["kind"] == "adjust")
    assert lm.reorder_rows(task, [row_t], row_a)
    tl = task["timeline"]
    assert tl.tracks.index(n["txt"]) == tl.tracks.index(n["adj"]) + 1 and task["z_order_v2"]
    # 拖到最底 → 垫到最底（整幅背景轨仍在它下面）
    row_t = next(r for r in lm.rows(task) if r["kind"] == "text")
    assert lm.reorder_rows(task, [row_t], None)
    assert tl.tracks[0] is n["bg"] and tl.tracks[1] is n["txt"]
    # 同一条轨道里两条文字：只换先后，不挪轨道
    rows = lm.rows(task)
    ra = next(r for r in rows if r["kind"] == "text" and lm.resolve(task, r["locator"])["content"] == "A")
    rb = next(r for r in rows if r["kind"] == "text" and lm.resolve(task, r["locator"])["content"] == "B")
    assert lm.reorder_rows(task, [ra], rb)
    assert [o["content"] for o in task["text_overlays"]] == ["B", "A"]
    assert tl.tracks[1] is n["txt"]
    # 声音不参与
    assert not lm.can_reorder(next(r for r in rows if r["kind"] == "audio"))


def test_move_track_up_down_top_bottom():
    from cutora.batch import layer_model as lm
    task, n = _layer_task()
    tl = task["timeline"]
    assert lm.move_track(task, n["adj"], "down") and tl.tracks.index(n["adj"]) == 3
    assert lm.move_track(task, n["adj"], "bottom") and tl.tracks[1] is n["adj"] and tl.tracks[0] is n["bg"]
    assert not lm.move_track(task, n["adj"], "down")
    assert lm.move_track(task, n["adj"], "top") and tl.tracks[-2] is n["adj"] and tl.tracks[-1] is n["au"]
    assert not lm.move_track(task, n["adj"], "up")
    assert not lm.move_track(task, n["au"], "up")              # 声音轨不在画面叠压里


# ───────────────────────────── 中央监视器 ─────────────────────────────

def _monitor(tmp_path, n=3):
    from test_q144_central_monitor import _dialog, _wait
    from test_q175_playback_adjust3 import _adjust_task
    tasks = [_adjust_task(tmp_path, "blur")[0] for _ in range(n)]
    dlg = _dialog(tasks)
    app = _app()
    g = dlg.grid
    _wait(app, lambda: all(t.comp is not None for t in g.tiles), 20)
    return dlg, g, tasks


def test_monitor_layer_props_batch_and_undo(tmp_path, monkeypatch):
    monkeypatch.setenv("CUTORA_USER_DATA", str(tmp_path / "ud"))
    _app()
    from PySide6.QtWidgets import QMenu
    from cutora.batch import layer_model as lm
    dlg, g, tasks = _monitor(tmp_path)
    try:
        g.selected = {0, 1, 2}
        g.active = 0
        panel = g.layer_panel
        panel.sync()
        row = next(i for i, r in enumerate(panel._layers) if r["kind"] == "adjust")
        idx = panel.model.index(row, 0)
        panel.lst.selectionModel().select(idx, panel.lst.selectionModel().SelectionFlag.ClearAndSelect)
        ref = g.current_layer_ref(g.tiles[0], None, None)
        assert ref is not None and ref.kind == "adjust"          # 面板里选中的那一层（全画面时画面上点不中）
        pairs = g.sibling_layers(ref, g.selected_tiles())
        assert len(pairs) == 3
        menu = QMenu()
        sub = g.add_layer_props_menu(menu, ref, g.selected_tiles())
        w = next(a.defaultWidget() for a in sub.actions() if hasattr(a, "defaultWidget") and a.defaultWidget())
        w._props_widgets["effect.amount_blur"].setValue(77)          # Q179：各种效果各自的强度
        w._props_widgets["xform.on"].setChecked(True)
        w._props_widgets["xform.scale"].setValue(150)
        w._props_flush()
        for t in tasks:
            ov = next(o for o in t["image_overlays"] if o.get("type") == "adjust")
            assert ov["effect"]["amount_blur"] == 77 and ov["xform"]["on"] and ov["xform"]["scale"] == pytest.approx(1.5)
        w._props_close()
        g.undo()
        for t in tasks:
            ov = next(o for o in t["image_overlays"] if o.get("type") == "adjust")
            assert ov["effect"]["amount"] == 60 and not (ov.get("xform") or {}).get("on")
        # 叠放顺序：三条视频里的调整轨一起往下挪一层，再撤销
        before = [[x.type for x in t["timeline"].tracks] for t in tasks]
        assert g.layer_restack_tracks(g.sibling_layers(ref, g.selected_tiles()), "down", "下移一层") == 3
        for t in tasks:
            tys = [x.type for x in t["timeline"].tracks]
            assert tys.index("adjust") < tys.index("text")
        g.undo()
        assert [[x.type for x in t["timeline"].tracks] for t in tasks] == before
        # 图层面板拖动：同样改时间线轨道顺序
        panel.sync()
        rows = panel._layers
        r_txt = next(r for r in rows if r["kind"] == "text")
        r_adj = next(r for r in rows if r["kind"] == "adjust")
        panel._reorder([r_txt], r_adj)
        tl0 = tasks[0]["timeline"]
        assert tl0.tracks.index(lm.track_of_row(tasks[0], r_txt)) > tl0.tracks.index(
            next(x for x in tl0.tracks if x.type == "adjust"))
        assert [r["kind"] for r in panel._layers][0] == "text"   # 面板也跟着刷新了
    finally:
        dlg.close()


def test_monitor_video_layer_props(tmp_path, monkeypatch):
    monkeypatch.setenv("CUTORA_USER_DATA", str(tmp_path / "ud"))
    _app()
    from PySide6.QtWidgets import QMenu
    from cutora.ui.monitor_layer_props import LayerRef
    dlg, g, tasks = _monitor(tmp_path, 2)
    try:
        g.selected = {0, 1}
        main = next(t for t in tasks[0]["timeline"].tracks if t.type == "video")
        ref = LayerRef(g.tiles[0], "video", None, main)
        menu = QMenu()
        sub = g.add_layer_props_menu(menu, ref, g.selected_tiles(), include_restack=False)
        w = next(a.defaultWidget() for a in sub.actions() if hasattr(a, "defaultWidget") and a.defaultWidget())
        w._props_widgets["opacity"].setValue(50)
        w._props_widgets["scale"].setValue(80)
        w._props_flush()
        for t in tasks:
            c = next(x for x in t["timeline"].tracks if x.type == "video").clips[0]
            assert c.opacity == 128 and c.scale == pytest.approx(0.8)
        w._props_close()
        g.undo()
        c = next(x for x in tasks[1]["timeline"].tracks if x.type == "video").clips[0]
        assert getattr(c, "opacity", 255) == 255 and c.scale == pytest.approx(1.0)
    finally:
        dlg.close()
