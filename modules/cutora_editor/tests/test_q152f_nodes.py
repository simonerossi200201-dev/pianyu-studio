# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q152-F · 效果节点图：数据 / 规则、求值、「转为节点」逐位一致、命令 + 撤销、查看器只在预览、
检查器节点模式、节点编辑器手势、主窗口 dock。"""
from __future__ import annotations

import copy
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt  # noqa: E402
from PySide6.QtGui import QImage, QKeyEvent, QMouseEvent, QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402


def _app():
    from test_q145_canvas_shortcuts import _app as app
    return app()


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setenv("CUTORA_MATTE_BACKEND", "fake")
    monkeypatch.setenv("CUTORA_MATTE_CACHE", str(tmp_path / "cache" / "matte"))
    from cutora.fx import graph as G
    from cutora.fx import matte_store as S
    from cutora.fx.layer_fx import clear_cache
    G.clear_viewers()
    S.invalidate()
    clear_cache()
    yield
    G.clear_viewers()
    clear_cache()


def _G():
    from cutora.fx import graph as G
    return G


def _green_scene(w=320, h=240):
    import cv2
    img = np.zeros((h, w, 3), np.uint8)
    img[:] = (30, 200, 30)
    cv2.circle(img, (160, 80), 40, (225, 180, 150), -1)
    cv2.rectangle(img, (110, 120), (210, 220), (240, 240, 240), -1)
    return img


def _ids(g, t):
    return [n["id"] for n in g["nodes"] if n["type"] == t]


# ───────────────────────────── 数据 / 规则 ─────────────────────────────

def test_registry_normalize_and_connect_rules():
    G = _G()
    g = G.empty_graph()
    assert g["edges"] == [[G.SRC, G.OUT, "in"]] and g["uid"].startswith("g")
    for t, nt in G.NODE_TYPES.items():
        assert nt["title"] and nt["doc"] and nt["category"] in ("抠像", "遮罩", "画面"), t
        assert isinstance(nt["params"](), dict)
    k = G.add_node(g, "keyer", [0, 0])["id"]
    m = G.add_node(g, "mask")["id"]
    ap = G.add_node(g, "matte_apply")["id"]
    assert G.can_connect(g, G.SRC, k, "in")
    assert not G.can_connect(g, m, k, "in")                  # 遮罩接不到画面端口
    assert G.can_connect(g, k, ap, "matte")                  # 画面接遮罩端口 = 取它的 alpha
    assert not G.can_connect(g, G.OUT, k, "in") and not G.can_connect(g, k, G.SRC, "in")
    assert not G.can_connect(g, k, k, "in") and not G.can_connect(g, k, ap, "nope")
    G.connect(g, G.SRC, k, "in")
    G.connect(g, k, ap, "in")
    assert not G.can_connect(g, ap, k, "in")                 # 成环
    with pytest.raises(ValueError):
        G.connect(g, ap, k, "in")
    G.connect(g, G.SRC, ap, "in")                            # 每个输入只留一根
    assert [e for e in g["edges"] if e[1] == ap and e[2] == "in"] == [[G.SRC, ap, "in"]]
    raw = copy.deepcopy(g)
    raw["nodes"] += [{"id": "zz", "type": "bogus"}, {"id": "src", "type": "mask"}, "junk",
                     {"id": k, "type": "blur"}]
    raw["edges"] += [["zz", ap, "matte"], [ap, k, "in"], ["n9", "nope", "in"], "bad"]
    raw["nodes"][0]["params"] = {"keyer": {"gain": 3.0}, "extra": 1}
    n = G.normalize(raw)
    ids = [x["id"] for x in n["nodes"]]
    assert ids == [k, m, ap]                                 # 坏类型 / 保留名 / 重复 id 都丢掉
    assert [ap, k, "in"] not in n["edges"] and ["zz", ap, "matte"] not in n["edges"]
    assert n["nodes"][0]["params"]["keyer"] == {"gain": 3.0} and n["nodes"][0]["params"]["extra"] == 1
    assert G.normalize({"x": 1}) is None and G.normalize(None) is None
    assert G.graph_of({"fx_graph": {}}) is None and G.has_graph({"fx_graph": n})
    assert G.new_node_id(n) == "n4"


def test_remove_insert_swap_and_layout():
    G = _G()
    g = G.empty_graph()
    g["edges"] = []
    a = G.add_node(g, "color")["id"]
    b = G.add_node(g, "blur")["id"]
    G.connect(g, G.SRC, a, "in")
    G.connect(g, a, b, "in")
    G.connect(g, b, G.OUT, "in")
    G.remove_node(g, a)                                      # 上下游自动接上
    assert G.upstream(g, b, "in") == G.SRC and G.node_by_id(g, a) is None
    c = G.add_node(g, "keyer")["id"]
    G.insert_after(g, G.SRC, c)
    assert G.upstream(g, c, "in") == G.SRC and G.upstream(g, b, "in") == c
    ops = G.add_node(g, "matte_ops")["id"]
    m1 = G.add_node(g, "mask")["id"]
    m2 = G.add_node(g, "mask")["id"]
    G.connect(g, m1, ops, "a")
    G.connect(g, m2, ops, "b")
    assert G.swap_inputs(g, ops)
    assert G.upstream(g, ops, "a") == m2 and G.upstream(g, ops, "b") == m1
    assert not G.swap_inputs(g, b)                           # 画面 + 遮罩：不同类型不换
    G.remove_node(g, m1, reconnect=True)                     # 遮罩节点没输入：只断开
    assert G.upstream(g, ops, "b") is None
    G.auto_layout(g)
    pos = {n["id"]: n["pos"] for n in g["nodes"]}
    assert g["io_pos"][G.SRC] == [40.0, 40.0]
    assert pos[c][0] < pos[b][0] < g["io_pos"][G.OUT][0]
    assert all(p[0] < g["io_pos"][G.OUT][0] for p in pos.values())


# ───────────────────────────── 求值 ─────────────────────────────

def _host_with(nodes_edges):
    G = _G()
    g = G.empty_graph()
    g["edges"] = []
    ids = {}
    for key, t, params in nodes_edges[0]:
        ids[key] = G.add_node(g, t, [0, 0], params)["id"]
    ids["src"], ids["out"] = G.SRC, G.OUT
    for a, b, p in nodes_edges[1]:
        G.connect(g, ids[a], ids[b], p)
    return {"fx_graph": g}, ids


LEFT = {"masks": [{"id": "l", "type": "rect", "rect": [0, 0, 0.5, 1]}]}
ALL = {"masks": [{"id": "a", "type": "rect", "rect": [0, 0, 1, 1]}]}


def test_evaluate_mattes_apply_modes_and_ops():
    G = _G()
    rgb = np.full((40, 60, 3), 100, np.uint8)
    host, ids = _host_with(([("m", "mask", LEFT), ("ap", "matte_apply", None)],
                            [("src", "ap", "in"), ("m", "ap", "matte"), ("ap", "out", "in")]))
    out, a = G.evaluate(rgb, None, host)
    assert out is rgb and a[20, 5] == 1.0 and a[20, 55] == 0.0
    G.node_by_id(host["fx_graph"], ids["ap"])["params"]["mode"] = "cutout"
    _o, a = G.evaluate(rgb, None, host)
    assert a[20, 5] == 0.0 and a[20, 55] == 1.0
    G.node_by_id(host["fx_graph"], ids["ap"])["params"].update(mode="replace", invert=True)
    alpha0 = np.full((40, 60), 0.5, np.float32)
    _o, a = G.evaluate(rgb, alpha0, host)
    assert a[20, 5] == 0.0 and a[20, 55] == 1.0                # 替换：原 alpha 不管
    # 遮罩运算：全 − 左 = 右半；相交 = 左；差值 = 右；没接 B = 只反转
    host, ids = _host_with(([("a", "mask", ALL), ("b", "mask", LEFT), ("op", "matte_ops", {"op": "subtract"}),
                             ("ap", "matte_apply", None)],
                            [("a", "op", "a"), ("b", "op", "b"), ("src", "ap", "in"), ("op", "ap", "matte"),
                             ("ap", "out", "in")]))
    opn = G.node_by_id(host["fx_graph"], ids["op"])["params"]
    expect = {"subtract": (0, 1), "intersect": (1, 0), "difference": (0, 1), "add": (1, 1), "max": (1, 1),
              "min": (1, 0)}
    for op, (left, right) in expect.items():
        opn["op"] = op
        _o, a = G.evaluate(rgb, None, host)
        assert (a[20, 5], a[20, 55]) == (left, right), op
    opn.update(op="subtract", invert=True)
    _o, a = G.evaluate(rgb, None, host)
    assert (a[20, 5], a[20, 55]) == (1, 0)
    G.disconnect(host["fx_graph"], ids["op"], "b")
    _o, a = G.evaluate(rgb, None, host)
    assert a.max() == 0.0                                        # 全白反转 = 全黑
    # 画面接到遮罩端口 = 用它的 alpha
    host, ids = _host_with(([("k", "matte_apply", None), ("ap", "matte_apply", None), ("m", "mask", LEFT)],
                            [("src", "k", "in"), ("m", "k", "matte"), ("src", "ap", "in"), ("k", "ap", "matte"),
                             ("ap", "out", "in")]))
    _o, a = G.evaluate(rgb, None, host)
    assert a[20, 5] == 1.0 and a[20, 55] == 0.0


def test_evaluate_region_blur_color_bypass_viewer_and_open_ends():
    G = _G()
    rgb = np.zeros((40, 60, 3), np.uint8)
    rgb[:, ::2] = 255                                            # 竖条纹：一糊就变灰
    host, ids = _host_with(([("m", "mask", LEFT), ("bl", "blur", {"size": 6.0})],
                            [("src", "bl", "in"), ("m", "bl", "mask"), ("bl", "out", "in")]))
    out, a = G.evaluate(rgb, None, host)
    assert a is None
    assert 60 < out[20, 10, 0] < 200                             # 区域里糊了
    assert np.array_equal(out[:, 40:], rgb[:, 40:])              # 区域外原样
    # 调色只在区域里
    gray = np.full((40, 60, 3), 100, np.uint8)
    host2, ids2 = _host_with(([("m", "mask", LEFT), ("c", "color", {"brightness": 0.2})],
                              [("src", "c", "in"), ("m", "c", "mask"), ("c", "out", "in")]))
    out, _a = G.evaluate(gray, None, host2)
    assert out[20, 5, 0] == 151 and out[20, 55, 0] == 100
    G.node_by_id(host2["fx_graph"], ids2["c"])["params"].update(brightness=0.0, saturation=-1.0)
    col = np.zeros((40, 60, 3), np.uint8)
    col[..., 0] = 200
    out, _a = G.evaluate(col, None, host2)
    assert out[20, 5, 0] == out[20, 5, 1] == out[20, 5, 2] and tuple(out[20, 55]) == (200, 0, 0)
    # 旁路：原样传下去
    G.node_by_id(host["fx_graph"], ids["bl"])["enabled"] = False
    out, _a = G.evaluate(rgb, None, host)
    assert np.array_equal(out, rgb)
    G.node_by_id(host["fx_graph"], ids["bl"])["enabled"] = True
    # 查看器：遮罩节点 → 黑白不透明；画面节点 → 它的结果；不存在的节点 → 正常输出
    g = host["fx_graph"]
    out, a = G.evaluate(rgb, None, host, view=ids["m"])
    assert a is None and tuple(out[20, 5]) == (255, 255, 255) and tuple(out[20, 55]) == (0, 0, 0)
    out, _a = G.evaluate(rgb, None, host, view=G.SRC)
    assert np.array_equal(out, rgb)
    G.set_viewer(g["uid"], ids["m"])
    assert G.viewer_for(host) == ids["m"] and G.preview_view(host) is None
    with G.preview_scope():
        assert G.preview_view(host) == ids["m"]
    assert G.preview_view(host) is None
    G.set_viewer(g["uid"], None)
    assert G.viewer_for(host) is None
    # out 没接 / 主输入没接：不黑屏，原样
    G.disconnect(g, G.OUT, "in")
    out, a = G.evaluate(rgb, None, host)
    assert out is rgb and a is None
    G.connect(g, ids["bl"], G.OUT, "in")
    G.disconnect(g, ids["bl"], "in")
    out, a = G.evaluate(rgb, None, host)
    assert out is rgb and a is None


def test_from_legacy_is_bit_identical(tmp_path):
    from cutora.fx import ai_matte as A
    from cutora.fx import matte_store as S
    from cutora.fx.layer_fx import apply_arrays, fx_signature, layer_has_fx
    G = _G()
    rgb = _green_scene()
    sid = S.new_session_id()
    m = np.zeros((240, 320), np.uint8)
    m[:, :210] = 255
    with S.Session(sid, fps=30.0, src_w=320, src_h=240) as sess:
        sess.write(15, 1, m, 1.0)
    src = {"src_t": 0.5, "crop": (0, 0, 0, 0), "flip": (False, False), "order": "crop_flip"}
    full = {"keyer": {"enabled": True, "color": "#1EC81E", "gain": 1.2, "despill": 0.6},
            "ai_matte": A.normalize({"session": sid, "objects": [{"id": 1}], "prompts": {}}),
            "masks": [{"id": "a", "type": "rect", "rect": [0.05, 0.05, 0.9, 0.95], "feather": 6.0},
                      {"id": "b", "type": "ellipse", "rect": [0.4, 0.3, 0.6, 0.5], "mode": "subtract"}]}
    variants = [full, {"masks": full["masks"]}, {"keyer": full["keyer"]}, {"ai_matte": full["ai_matte"]},
                dict(full, ai_matte=dict(full["ai_matte"], view="matte")), {}]
    for legacy in variants:
        for px in (1.0, 1.5):
            ref = apply_arrays(rgb, None, legacy, px, src)
            g = G.from_legacy(legacy)
            got = apply_arrays(rgb, None, {"fx_graph": g}, px, src)
            assert np.array_equal(ref[0], got[0]), legacy.keys()
            assert (ref[1] is None and got[1] is None) or np.array_equal(ref[1], got[1]), legacy.keys()
    g = G.from_legacy(full)
    assert [n["type"] for n in g["nodes"]] == ["keyer", "ai_matte", "mask", "matte_apply"]
    assert G.from_legacy({})["edges"] == [[G.SRC, G.OUT, "in"]]
    # 有图 → 老键不生效；签名按图算，查看器也进签名
    host = dict(full, fx_graph=G.from_legacy({}))
    out, a = apply_arrays(rgb, None, host, 1.0, src)
    assert np.array_equal(out, rgb) and a is None and layer_has_fx(host)
    s1 = fx_signature({"fx_graph": g}, 1.0, src)
    assert s1 != fx_signature({"fx_graph": g}, 1.0, src, view=g["nodes"][0]["id"])
    assert G.ai_nodes({"fx_graph": g})[0]["type"] == "ai_matte"


# ───────────────────────────── 命令 + 撤销 ─────────────────────────────

def test_graph_commands_routing_and_undo():
    _app()
    from test_q152a_keyframe_editor import _bus_window, _clip_task
    G = _G()
    task, _tr, clip = _clip_task()
    clip.keyer = {"enabled": True, "color": "#00FF00"}
    clip.masks = [{"id": "m1", "type": "rect", "rect": [0, 0, 0.5, 1]}]
    task["text_overlays"] = [{"id": 1, "type": "text", "content": "T", "x": 0, "y": 0, "w": 10, "h": 10,
                              "start": 0.0, "end": 1.0}]
    win, bus = _bus_window(task)
    loc = {"target": "clip", "track_id": "v0", "clip_index": 0}
    with pytest.raises(Exception):
        bus.execute("fx.graph", {"target": "overlay", "list": "text_overlays", "overlay_index": 0, "op": "create"},
                    source="ui")
    with pytest.raises(Exception):
        bus.execute("fx.node", dict(loc, op="add", type="blur"), source="ui")       # 还没有图
    bus.execute("fx.graph", dict(loc, op="create"), source="ui")
    g = clip.fx_graph
    assert [n["type"] for n in g["nodes"]] == ["keyer", "mask", "matte_apply"]
    assert not getattr(clip, "keyer", None) and not getattr(clip, "masks", None)
    again = bus.execute("fx.graph", dict(loc, op="create"), source="ui")
    assert again.get("existed")
    nk, nm, na = (_ids(clip.fx_graph, t)[0] for t in ("keyer", "mask", "matte_apply"))
    # 老命令带 node_id → 改节点
    bus.execute("fx.mask_add", dict(loc, node_id=nm, mask={"id": "m2", "type": "ellipse",
                                                          "rect": [0.2, 0.2, 0.4, 0.4]}), source="ui")
    bus.execute("fx.keyer_set", dict(loc, node_id=nk, patch={"gain": 2.0}), source="ui")
    gn = clip.fx_graph
    assert [x["id"] for x in G.node_by_id(gn, nm)["params"]["masks"]] == ["m1", "m2"]
    assert G.node_by_id(gn, nk)["params"]["keyer"]["gain"] == 2.0 and not getattr(clip, "masks", None)
    with pytest.raises(Exception):
        bus.execute("fx.keyer_set", dict(loc, node_id="n99", patch={"gain": 1.0}), source="ui")
    # 加 / 插入 / 改 / 挪
    nb = bus.execute("fx.node", dict(loc, op="add", type="blur", pos=[300, 50], after=nk), source="ui")["node"]
    gn = clip.fx_graph
    assert G.upstream(gn, nb, "in") == nk and G.upstream(gn, na, "in") == nb
    bus.execute("fx.node", dict(loc, op="set", id=nb, patch={"label": "背景虚化", "color": "#4FC3F7",
                                                              "params": {"size": 4.0}}), source="ui")
    n = G.node_by_id(clip.fx_graph, nb)
    assert n["label"] == "背景虚化" and n["params"] == {"size": 4.0, "alpha": True}
    bus.execute("fx.node", dict(loc, op="move", positions={nb: [320, 64], "src": [0, 8], "zz": [1, 1]}), source="ui")
    assert G.node_by_id(clip.fx_graph, nb)["pos"] == [320.0, 64.0] and clip.fx_graph["io_pos"]["src"] == [0.0, 8.0]
    # 连线
    with pytest.raises(Exception):
        bus.execute("fx.link", dict(loc, op="connect", **{"from": nm, "to": nb, "port": "in"}), source="ui")
    bus.execute("fx.link", dict(loc, op="connect", **{"from": nm, "to": nb, "port": "mask"}), source="ui")
    assert G.upstream(clip.fx_graph, nb, "mask") == nm
    bus.execute("fx.link", dict(loc, op="disconnect", to=nb, port="mask"), source="ui")
    assert G.upstream(clip.fx_graph, nb, "mask") is None
    with pytest.raises(Exception):
        bus.execute("fx.node", dict(loc, op="swap", id=nb), source="ui")
    # 删（上下游接上）→ 撤销回来
    n_hist = len(win.history_mgr.history)
    bus.execute("fx.node", dict(loc, op="remove", id=nb), source="ui")
    assert len(win.history_mgr.history) == n_hist + 1
    assert G.node_by_id(clip.fx_graph, nb) is None and G.upstream(clip.fx_graph, na, "in") == nk
    win.undo()
    c2 = win.tasks[0]["timeline"].tracks[0].clips[0]
    assert G.node_by_id(c2.fx_graph, nb) is not None and G.upstream(c2.fx_graph, na, "in") == nb
    # replace 保留 uid；delete 回直通
    uid = c2.fx_graph["uid"]
    g2 = copy.deepcopy(c2.fx_graph)
    g2["uid"] = "gother"
    G.auto_layout(g2)
    bus.execute("fx.graph", dict(loc, op="replace", graph=g2), source="ui")
    c3 = win.tasks[0]["timeline"].tracks[0].clips[0]
    assert c3.fx_graph["uid"] == uid
    with pytest.raises(Exception):
        bus.execute("fx.graph", dict(loc, op="replace", graph={"nodes": "x"}), source="ui")
    bus.execute("fx.graph", dict(loc, op="delete"), source="ui")
    assert not G.has_graph(win.tasks[0]["timeline"].tracks[0].clips[0])


def test_specs_and_schema():
    from cutora.core.cmds.specs_edit import build
    from cutora.model.project_schema import CLIP_KEYS
    specs = {getattr(x, "name", None) or (x.get("name") if isinstance(x, dict) else None): x for x in build()}
    assert {"fx.graph", "fx.node", "fx.link"} <= set(specs)
    assert "fx_graph" in CLIP_KEYS


# ───────────────────────────── 渲染：导出照常、查看器只在预览 ─────────────────────────────

def test_export_matches_legacy_and_viewer_is_preview_only(tmp_path):
    _app()
    from test_q31_keyframes import _color_mp4, _export_frame, _task_with_clip
    from cutora.preview.engine import PreviewEngine
    G = _G()
    media = str(tmp_path / "c.mp4")
    _color_mp4(media, "0xC83C3C", frames=30, size="160x90")
    task, _tr, clip = _task_with_clip(media, start_t=0.0, duration=1.0, canvas=(160, 90))
    clip.fit_mode = "stretch"
    clip.masks = [{"id": "l", "type": "rect", "rect": [0, 0, 0.5, 1]}]

    def _lum(img, x, y):
        return img.pixelColor(x, y).lightness()

    legacy = _export_frame(task, 0.5)
    assert _lum(legacy, 20, 45) > 40 and _lum(legacy, 140, 45) < 10
    clip.fx_graph = G.from_legacy(clip)
    clip.masks = []
    graph_exp = _export_frame(task, 0.5)
    assert graph_exp.convertToFormat(QImage.Format.Format_RGB32) == legacy.convertToFormat(QImage.Format.Format_RGB32)
    G.set_viewer(clip.fx_graph["uid"], G.SRC)                    # 查看器：只看输入
    eng = PreviewEngine()
    try:
        pv = eng.video_frame_at(task, 0.5)
        assert _lum(pv, 140, 45) > 40                            # 预览：右半也看得见
    finally:
        eng.close()
    exp = _export_frame(task, 0.5)
    assert _lum(exp, 140, 45) < 10                               # 导出：不受查看器影响


def test_image_overlay_graph_render_and_preview_scope(tmp_path):
    _app()
    import cv2
    from cutora.model.timeline import Timeline
    from cutora.model.timeline_model import Track
    from cutora.render.frame_renderer import FrameRenderer
    G = _G()
    path = str(tmp_path / "gs.png")
    cv2.imwrite(path, cv2.cvtColor(_green_scene(), cv2.COLOR_RGB2BGR))
    tl = Timeline(640, 480, 30.0)
    tl.add_track(Track("image"))
    task = {"timeline": tl, "segments": [], "style": {}, "text_overlays": [], "image_overlays": [], "dims": (640, 480)}
    ov = {"id": 1, "type": "image", "content": path, "x": 160, "y": 120, "w": 320, "h": 240,
          "start": 0.0, "end": 4.0, "opacity": 255}
    task["image_overlays"].append(ov)
    ov["fx_graph"] = G.from_legacy({"keyer": {"enabled": True, "color": "#1EC81E"},
                                    "masks": [{"id": "l", "type": "rect", "rect": [0, 0, 0.5, 1]}]})

    def _render():
        img = QImage(640, 480, QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(0xFF000000)
        p = QPainter(img)
        try:
            FrameRenderer.draw_layers(p, task, 640, 480, 1.0, render_order=["image"])
        finally:
            p.end()
        return img

    r = _render()
    assert r.pixelColor(170, 130).lightness() < 20               # 绿底抠掉
    assert r.pixelColor(160 + 150, 120 + 140).lightness() > 150  # 左半白方块在
    assert r.pixelColor(160 + 200, 120 + 140).lightness() < 20   # 右半被遮罩
    G.set_viewer(ov["fx_graph"]["uid"], G.SRC)
    assert _render().pixelColor(170, 130).lightness() < 20       # 不在预览里（导出）：查看器不生效
    with G.preview_scope():
        pv = _render()
    assert pv.pixelColor(170, 130).green() > 150 and pv.pixelColor(160 + 200, 120 + 140).lightness() > 150


# ───────────────────────────── 检查器 + 编辑器 ─────────────────────────────

def _mouse(typ, pt, button=Qt.MouseButton.LeftButton, buttons=None, mods=Qt.KeyboardModifier.NoModifier):
    if buttons is None:
        buttons = button if typ != QEvent.Type.MouseButtonRelease else Qt.MouseButton.NoButton
    return QMouseEvent(typ, QPointF(pt), QPointF(pt), button, buttons, mods)


def _vp(ed, scene_pt):
    return ed.view.mapFromScene(scene_pt)


def _drag(ed, a, b):
    """QTest 走真实的按键状态（直接调 mousePressEvent 时 QGraphicsScene 不会抓住节点）。"""
    from PySide6.QtTest import QTest
    vp = ed.view.viewport()
    QTest.mousePress(vp, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, _vp(ed, a))
    for f in (0.25, 0.5, 0.75, 1.0):
        p = QPointF(a.x() + (b.x() - a.x()) * f, a.y() + (b.y() - a.y()) * f)
        QTest.mouseMove(vp, _vp(ed, p))
    QTest.mouseRelease(vp, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, _vp(ed, b))
    QApplication.processEvents()


def _key(ed, key, mods=Qt.KeyboardModifier.NoModifier):
    ed.view.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, key, mods))
    QApplication.processEvents()


def _editor_env(kind="clip"):
    from test_q152a2_keyable_motion import _panel_env
    from cutora.ui.nodes.editor import NodeEditor
    win, panel, task, ov, clip = _panel_env(kind)
    win.prop_panel = panel
    ed = NodeEditor(win)
    ed.resize(1000, 420)
    ed.show()
    QApplication.processEvents()
    ed.refresh(force=True)
    return win, panel, ed, task, ov, clip


def test_editor_follows_inspector_and_convert():
    G = _G()
    win, panel, ed, task, ov, clip = _editor_env("overlay")
    try:
        assert ed.target is None and "文字层" in ed.empty_label.text() and not ed.btn_convert.isVisibleTo(ed)
        assert not panel.ov_fx.sec_graph.isVisibleTo(panel)            # 文字层没有节点图卡片
    finally:
        ed.close()
        win.close()
    win, panel, ed, task, ov, clip = _editor_env("clip")
    try:
        clip.masks = [{"id": "l", "type": "rect", "rect": [0, 0, 0.5, 1]}]
        fx = panel.vid_fx
        fx.reload()
        assert ed.target is not None and ed.stack.currentIndex() == 0 and ed.btn_convert.isVisibleTo(ed)
        assert fx.sec_graph.isVisibleTo(panel) and not fx.btn_graph_convert.isHidden()
        assert fx.btn_graph_open.isHidden()
        ed.btn_convert.click()
        assert G.has_graph(clip) and ed.stack.currentIndex() == 1
        nm, na = _ids(clip.fx_graph, "mask")[0], _ids(clip.fx_graph, "matte_apply")[0]
        assert set(ed.node_items) == {G.SRC, G.OUT, nm, na} and len(ed.edge_items) == 3
        assert "视频" in ed.title.text()
        # 检查器：有图没选节点 → 遮罩 / 抠像各节藏起来，卡片给「打开编辑器 / 删除节点图」
        fx.reload()
        assert not fx.sec_mask.isVisibleTo(panel) and not fx.sec_key.isVisibleTo(panel)
        assert not fx.btn_graph_open.isHidden() and not fx.btn_graph_delete.isHidden()
        assert "2 个节点" in fx.sec_graph.section_title if hasattr(fx.sec_graph, "section_title") else True
    finally:
        ed.close()
        win.close()


def test_inspector_node_mode_edits_node_params():
    G = _G()
    win, panel, ed, task, ov, clip = _editor_env("clip")
    try:
        clip.masks = [{"id": "l", "type": "rect", "rect": [0, 0, 0.5, 1]}]
        ed.convert()
        fx = panel.vid_fx
        nm, na = _ids(clip.fx_graph, "mask")[0], _ids(clip.fx_graph, "matte_apply")[0]
        # 编辑器里选遮罩节点 → 检查器显示它的遮罩
        ed._select([nm])
        ed._on_selection()
        assert fx.node_id == nm and fx.sec_mask.isVisibleTo(panel) and not fx.sec_key.isVisibleTo(panel)
        assert fx.mask_list.count() == 1 and not fx.btn_graph_back.isHidden()
        mid = fx.add_mask("ellipse")
        node = G.node_by_id(clip.fx_graph, nm)
        assert [m["id"] for m in node["params"]["masks"]] == ["l", mid] and not getattr(clip, "masks", None)
        assert fx.update_mask(mid, {"feather": 12.0}, coalesce="f")
        assert G.node_by_id(clip.fx_graph, nm)["params"]["masks"][1]["feather"] == 12.0
        # 画布取对象：选着节点 → 节点 params；layer=True → 图层本身
        from cutora.ui import canvas_mask as ck

        class _C:
            _selected_item = {"type": "video_clip", "clip": clip, "rect": QRectF(0, 0, 100, 50), "rotation": 0.0}

            def window(self):
                return win
        host, rec, kind = ck._host_and_rec(_C())
        assert host is G.node_by_id(clip.fx_graph, nm)["params"] and kind == "clip" and rec["_layer_scale"] == 1.0
        assert ck.Frame.of(host, rec) is not None
        assert ck._host_and_rec(_C(), layer=True)[0] is clip
        # 套用遮罩节点 → 通用参数面板，改「方式」写回节点
        ed._select([na])
        ed._on_selection()
        assert fx.node_id == na and fx.node_params.isVisibleTo(panel) and not fx.sec_mask.isVisibleTo(panel)
        fx.node_params.widget("matte_apply", "mode").setCurrentText("cutout")
        assert G.node_by_id(clip.fx_graph, na)["params"]["mode"] == "cutout"
        # 模糊节点的滑杆：400 ms 内连拖只记一步撤销
        nb = ed.add_node("blur", after=na)
        assert fx.node_id == nb
        s = fx.node_params.widget("blur", "size")
        n_hist = len(win.history_mgr.history)
        s.setValue(10.0)
        s.setValue(14.0)
        assert G.node_by_id(clip.fx_graph, nb)["params"]["size"] == 14.0
        assert len(win.history_mgr.history) == n_hist + 1
        # 回到图层
        fx.btn_graph_back.click()
        assert fx.node_id == "" and not fx.sec_mask.isVisibleTo(panel)
        ed.refresh()
        assert ed.selected_ids() == []
        # AI 抠像节点：AI 面板出现，会话写进节点
        na2 = ed.add_node("ai_matte", after=G.SRC)
        assert fx.node_id == na2 and fx.ai.sec.isVisibleTo(panel)
        assert fx.ai.ensure_session()
        assert G.node_by_id(clip.fx_graph, na2)["params"]["ai_matte"]["session"]
        assert not (getattr(clip, "ai_matte", None) or {}).get("session")
        # 跟踪面板始终是图层级
        assert fx.track is not None and fx.track._target()[0] is clip
        # 删除节点图
        fx.set_node("")
        assert fx.delete_graph() and not G.has_graph(clip)
        fx.reload()
        assert fx.sec_mask.isVisibleTo(panel) and not fx.btn_graph_convert.isHidden()
    finally:
        ed.close()
        win.close()


def test_editor_gestures_and_keys(monkeypatch):
    G = _G()
    win, panel, ed, task, ov, clip = _editor_env("clip")
    try:
        ed.convert()                                             # 空图：src → out
        assert clip.fx_graph["edges"] == [[G.SRC, G.OUT, "in"]]
        nc = ed.add_node("color", after=G.SRC)
        nm = ed.add_node("mask", (40.0, 300.0))
        g = clip.fx_graph
        assert G.upstream(g, nc, "in") == G.SRC and G.upstream(g, G.OUT, "in") == nc
        QApplication.processEvents()
        ed.fit_view()
        # 拖线：遮罩输出 → 调色的「限定区域」
        a = ed.node_items[nm].out_port_pos()
        b = ed.node_items[nc].in_port_pos("mask")
        _drag(ed, a, b)
        assert G.upstream(clip.fx_graph, nc, "mask") == nm and ed.wire is None
        # 遮罩输出拖到画面端口：拒绝
        _drag(ed, ed.node_items[nm].out_port_pos(), ed.node_items[G.OUT].in_port_pos("in"))
        assert G.upstream(clip.fx_graph, G.OUT, "in") == nc
        # 拎起已有的线，丢到空白处 = 断开
        b = ed.node_items[nc].in_port_pos("mask")
        _drag(ed, b, QPointF(b.x() - 60, b.y() + 160))
        assert G.upstream(clip.fx_graph, nc, "mask") is None
        # 从输出拖到空白处 → 菜单选「模糊」→ 新节点直接接上
        picked = []

        from cutora.ui.nodes import editor as E

        def _fake_exec(menu, *_a, **_k):
            for act in menu.actions():
                if act.text() == "模糊":
                    picked.append(act.text())
                    act.trigger()
                    return act
            return None
        monkeypatch.setattr(E, "_exec_menu", _fake_exec)
        a = ed.node_items[nc].out_port_pos()
        _drag(ed, a, QPointF(a.x() + 40, a.y() + 200))
        blurs = _ids(clip.fx_graph, "blur")
        assert picked and len(blurs) == 1 and G.upstream(clip.fx_graph, blurs[0], "in") == nc
        nb = blurs[0]
        # 拖节点：吸附 8 px，一次撤销
        ed._bus("fx.node", {"op": "move", "positions": {nb: [2000.0, 2000.0]}})
        it = ed.node_items[nb]
        ed.view.centerOn(it)
        p0 = QPointF(it.pos())
        n_hist = len(win.history_mgr.history)
        start = it.mapToScene(QPointF(60, 10))
        _drag(ed, start, QPointF(start.x() + 37, start.y() + 13))
        pos = G.node_by_id(clip.fx_graph, nb)["pos"]
        assert pos != [p0.x(), p0.y()] and pos[0] % 8 == 0 and pos[1] % 8 == 0
        assert len(win.history_mgr.history) == n_hist + 1
        # 键盘：Ctrl+P 旁路 / 1 查看器 / Esc / F2 / Delete
        ed._select([nc])
        ed._on_selection()
        _key(ed, Qt.Key.Key_P, Qt.KeyboardModifier.ControlModifier)
        assert G.node_by_id(clip.fx_graph, nc)["enabled"] is False and not ed.node_items[nc].enabled
        _key(ed, Qt.Key.Key_P, Qt.KeyboardModifier.ControlModifier)
        assert G.node_by_id(clip.fx_graph, nc)["enabled"] is True
        _key(ed, Qt.Key.Key_1)
        assert G.viewer_for(clip) == nc and ed.node_items[nc].viewer and ed.btn_viewer_off.isVisibleTo(ed)
        _key(ed, Qt.Key.Key_Escape)
        assert G.viewer_for(clip) is None and not ed.btn_viewer_off.isVisibleTo(ed)
        assert ed.rename(nc, label="提亮") and G.node_by_id(clip.fx_graph, nc)["label"] == "提亮"
        assert ed.set_color(nc, "#81C784") and G.node_by_id(clip.fx_graph, nc)["color"] == "#81C784"
        ev = QKeyEvent(QEvent.Type.ShortcutOverride, Qt.Key.Key_Delete, Qt.KeyboardModifier.NoModifier)
        assert ed.wants_key(ev)
        ed._select([nc])
        _key(ed, Qt.Key.Key_Delete)
        g = clip.fx_graph
        assert G.node_by_id(g, nc) is None and G.upstream(g, nb, "in") == G.SRC     # 上下游接上
        # 边上插节点 + Ctrl+T 交换 + Ctrl+L 自动排布
        nk = ed.insert_on_edge((G.SRC, nb, "in"), "keyer")
        assert G.upstream(clip.fx_graph, nk, "in") == G.SRC and G.upstream(clip.fx_graph, nb, "in") == nk
        no = ed.add_node("matte_ops", (600.0, 300.0))
        m2 = ed.add_node("mask", (400.0, 380.0))
        assert ed.connect_nodes(nm, no, "a") and ed.connect_nodes(m2, no, "b")
        ed._select([no])
        _key(ed, Qt.Key.Key_T, Qt.KeyboardModifier.ControlModifier)
        assert G.upstream(clip.fx_graph, no, "a") == m2
        _key(ed, Qt.Key.Key_L, Qt.KeyboardModifier.ControlModifier)
        g = clip.fx_graph
        assert G.node_by_id(g, nk)["pos"][0] < G.node_by_id(g, nb)["pos"][0] < g["io_pos"][G.OUT][0]
        # 搜索加节点（插在选中节点后面）
        ed._select([nk])
        pop = ed.open_search()
        pop.edit.setText("调色")
        assert pop.list.count() == 1
        pop._accept()
        ncol = _ids(clip.fx_graph, "color")[0]
        assert G.upstream(clip.fx_graph, ncol, "in") == nk and G.upstream(clip.fx_graph, nb, "in") == ncol
        # 右键菜单能建出来（不执行）
        monkeypatch.setattr(E, "_exec_menu", lambda *_a, **_k: None)
        for pt in (ed.node_items[nb].sceneBoundingRect().center(), QPointF(-3000, -3000)):
            ed.context_menu(pt, ed.mapToGlobal(ed.rect().center()))
            assert ed._last_menu is not None and ed._last_menu.actions()
        # 撤销回来的工程（对象换了）：编辑器跟着刷新，不跳视野
        win.undo()
        panel.load_data("video_clip", (win.tasks[0]["timeline"].tracks[0], win.tasks[0]["timeline"].tracks[0].clips[0]))
        ed.refresh(force=True)
        assert ed.target.host is win.tasks[0]["timeline"].tracks[0].clips[0]
    finally:
        ed.close()
        win.close()


def test_matte_coverage_band_reads_graph_ai_node(tmp_path):
    from cutora.fx import ai_matte as A
    from cutora.fx import matte_store as S
    from cutora.model.timeline_model import Clip
    G = _G()
    sid = S.new_session_id()
    with S.Session(sid, fps=30.0, src_w=4, src_h=4) as sess:
        for f in range(3):
            sess.write(f, 1, np.full((4, 4), 255, np.uint8), 1.0)
    clip = Clip("s", 0.0, 1.0, 0.0)
    g = G.empty_graph()
    n = G.add_node(g, "ai_matte", [0, 0], {"ai_matte": A.normalize({"session": sid, "objects": [{"id": 1}]})})
    G.insert_after(g, G.SRC, n["id"])
    clip.fx_graph = g
    runs = A.coverage_runs(clip)
    assert runs and runs[0][2] is False


# ───────────────────────────── 快捷键 / 主窗口 ─────────────────────────────

def test_shortcut_registered_and_help_rows():
    from cutora.ui.shortcuts import GESTURE_ROWS, SHORTCUTS, shortcut_key
    assert shortcut_key("node_editor") == "Alt+N"
    keys = [k for _i, k, _l, _s in SHORTCUTS]
    assert keys.count("Alt+N") == 1
    assert sum(1 for area, _k, _l in GESTURE_ROWS if area == "节点编辑器") >= 6


def test_main_window_dock_and_menu_toggle():
    from test_q145b_timeline_shortcuts import _window
    win = _window()
    assert win.dock_nodes.objectName() == "dockNodes" and not win.dock_nodes.isVisible()
    assert win.act_nodes.shortcut().toString() == "Alt+N"
    win.act_nodes.trigger()
    QApplication.processEvents()
    assert win.dock_nodes.isVisible() and win.act_nodes.isChecked()
    assert win.dockWidgetArea(win.dock_nodes) == Qt.DockWidgetArea.BottomDockWidgetArea
    win.toggle_keyframe_editor(True)
    QApplication.processEvents()
    assert win.dock_nodes in win.tabifiedDockWidgets(win.dock_keyframes)
    win.toggle_node_editor(False)
    QApplication.processEvents()
    assert not win.dock_nodes.isVisible() and not win.act_nodes.isChecked()
