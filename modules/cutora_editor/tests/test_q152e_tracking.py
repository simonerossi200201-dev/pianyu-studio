# -*- coding: utf-8 -*-
"""Q152-E · AI 跟踪：收录的 TAPNext++ 代码（结构 / 安全加载）、跟踪缓存、跟踪计划、模板匹配假后端、
运动求解（平移 / 旋转 / 缩放 / 遮挡 / 平滑 / 对象）、烘焙（跟随 / 稳定 / 自动填满 / 精简）、命令撤销、
模型登记、检查器 + 画布 + 时间线。

真模型不在 CI 跑：CUTORA_TRACK_BACKEND=fake（模板匹配，确定性）。
"""
import math
import os
import subprocess
import sys
import threading

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt  # noqa: E402
from PySide6.QtGui import QImage, QMouseEvent, QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402


def _app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setenv("CUTORA_TRACK_BACKEND", "fake")
    monkeypatch.setenv("CUTORA_MATTE_BACKEND", "fake")
    monkeypatch.setenv("CUTORA_MATTE_CACHE", str(tmp_path / "cache" / "matte"))
    monkeypatch.setenv("CUTORA_TRACK_CACHE", str(tmp_path / "cache" / "track"))
    from cutora.core import gpu_gate
    from cutora.fx import matte_store as S
    from cutora.fx import track_store as T
    gpu_gate.reset_for_tests()
    S.invalidate()
    T.invalidate()
    yield
    gpu_gate.reset_for_tests()


def _patch_video(path, frames=30, w=320, h=180, x0=60, y0=60, dx=2.0, dy=0.0):
    """灰底上一块随机纹理（40×40）匀速移动：第 i 帧左上角 (x0 + i·dx, y0 + i·dy)。"""
    import cv2
    from test_q31_keyframes import _ffmpeg
    rng = np.random.default_rng(7)
    tex = cv2.GaussianBlur((rng.random((40, 40, 3)) * 255).astype(np.uint8), (5, 5), 1.2)
    d = os.path.join(os.path.dirname(path), "frames_" + os.path.basename(path))
    os.makedirs(d, exist_ok=True)
    for i in range(frames):
        img = np.full((h, w, 3), 100, np.uint8)
        M = np.float32([[1, 0, x0 + i * dx], [0, 1, y0 + i * dy]])
        cv2.warpAffine(tex, M, (w, h), dst=img, borderMode=cv2.BORDER_TRANSPARENT)
        cv2.imwrite(os.path.join(d, "f%03d.png" % i), img)
    subprocess.check_call([_ffmpeg(), "-y", "-loglevel", "error", "-framerate", "30",
                           "-i", os.path.join(d, "f%03d.png"), "-pix_fmt", "yuv420p", "-crf", "8", path])
    return path


def _tiny_model(**kw):
    """小号 TAPNext（随机权重；上游有几层用 torch.empty，这里统一初始化，避免 NaN）。"""
    import torch
    from cutora.third_party.tapnext import tapnextpp as P
    m = P.TAPNextPP.build(device="cpu", width=64, num_heads=4, lru_width=64, depth=2, **kw)
    g = torch.Generator().manual_seed(0)
    with torch.no_grad():
        for prm in m._model.parameters():
            prm.copy_(torch.randn(prm.shape, generator=g) * 0.05)
    return m


def _write_track(sid, fn, frames, w=400, h=200, fps=30.0):
    """fn(f) → {pid: (x_px, y_px, vis)}。"""
    from cutora.fx import track_store as T
    with T.Session(sid, fps=fps, src_w=w, src_h=h) as s:
        for f in frames:
            for pid, (x, y, v) in fn(f).items():
                s.write(f, pid, x / w, y / h, v)


# ───────────────────────────── 收录的 TAPNext++ ─────────────────────────────

def test_vendored_tapnext_runs_and_has_upstream_layout():
    import torch
    from cutora.third_party.tapnext import tapnextpp as P
    torch.manual_seed(0)
    m = _tiny_model()
    keys = set(m._model.state_dict())
    for k in ("blocks.0.vit_block.ln_1.weight", "blocks.0.vit_block.self_attention.in_proj_weight",
              "blocks.0.vit_block.self_attention.out_proj.weight", "blocks.0.vit_block.mlp.0.weight",
              "blocks.0.vit_block.mlp.3.bias", "lin_proj.weight",
              "image_pos_emb", "point_query_token", "coordinate_head.6.weight", "visible_head.6.bias"):
        assert k in keys, k
    rgb = (np.random.default_rng(0).random((90, 160, 3)) * 255).astype(np.uint8)
    q = np.array([[20.0, 30.0], [100.0, 60.0]], np.float32)
    xy, vis, prob, state = m.track_frame(rgb, query_points_xy=q)
    assert xy.shape == (2, 2) and vis.shape == (2,) and prob.shape == (2,) and state.step == 1
    xy2, _v, p2, state = m.track_frame(rgb, state=state)
    assert xy2.shape == (2, 2) and state.step == 2 and 0.0 <= float(p2.min()) and float(p2.max()) <= 1.0
    # 512 输入：位置编码插值（模型空间仍是 256）
    m5 = _tiny_model(input_resolution=512)
    xy5, _v, _p, _s = m5.track_frame(rgb, query_points_xy=q)
    assert xy5.shape == (2, 2) and np.isfinite(xy5).all()
    # 坐标换算往返
    pts = np.array([[10.0, 20.0]], np.float32)
    back = P.model_to_display(P.display_to_model(pts, 90, 160), 90, 160)
    assert np.allclose(back, pts, atol=1e-4)


def test_vendored_code_has_no_extra_deps_and_license():
    root = os.path.join(os.path.dirname(__file__), "..", "cutora", "third_party", "tapnext")
    assert os.path.isfile(os.path.join(root, "LICENSE"))
    assert "Apache License" in open(os.path.join(root, "LICENSE"), encoding="utf-8").read()
    for name in os.listdir(root):
        if name.endswith(".py"):
            src = open(os.path.join(root, name), encoding="utf-8").read()
            assert "import einops" not in src, name
            assert "import torchvision" not in src and "from torchvision" not in src, name
            assert "from tapnet" not in src, name


def test_safe_checkpoint_loading(tmp_path, monkeypatch):
    import argparse
    import pickle

    import torch
    from cutora.third_party.tapnext import tapnextpp as P
    m = _tiny_model()._model
    sd = {"tapnext." + k: v for k, v in m.state_dict().items()}
    # 老命名（linear_1 / linear_2）也认
    legacy = dict(sd)
    for k in list(legacy):
        if ".vit_block.mlp.0." in k:
            legacy[k.replace(".mlp.0.", ".mlp.linear_1.")] = legacy.pop(k)
        elif ".vit_block.mlp.3." in k:
            legacy[k.replace(".mlp.3.", ".mlp.linear_2.")] = legacy.pop(k)
    ck = {"state_dict": legacy, "hyper_parameters": argparse.Namespace(lr=1e-3), "epoch": 3,
          "optimizer_states": [{"state": {0: {"exp_avg": torch.zeros(2)}}}]}
    p1 = str(tmp_path / "ok.ckpt")
    torch.save(ck, p1)
    monkeypatch.setenv("TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD", "1")
    out = P.load_state_dict_safely(p1)
    assert os.environ.get("TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD") == "1"       # 用完还原
    m2 = P.TAPNextPP.build(device="cpu", width=64, num_heads=4, lru_width=64, depth=2)._model
    m2.load_state_dict(out)
    for k, v in m.state_dict().items():
        assert torch.equal(v, m2.state_dict()[k]), k

    class Evil:
        def __reduce__(self):
            return (os.system, ("echo should-not-run",))
    ck["evil"] = Evil()
    p2 = str(tmp_path / "evil.ckpt")
    torch.save(ck, p2)
    with pytest.raises(pickle.UnpicklingError):
        P.load_state_dict_safely(p2)


# ───────────────────────────── 缓存 / 计划 ─────────────────────────────

def test_track_store_and_coverage():
    from cutora.fx import track_store as T
    sid = T.new_session_id()
    assert T.load(sid) is None and T.frames(sid) == []
    _write_track(sid, lambda f: {1: (10 + f, 20, 1.0), 2: (50, 60, 0.2 if f == 3 else 0.9)}, range(0, 5))
    d = T.load(sid)
    assert d["src_w"] == 400 and T.frames(sid) == [0, 1, 2, 3, 4]
    p = T.points_at(sid, 2)
    assert p[1][0] == pytest.approx(12 / 400) and p[2][2] == pytest.approx(0.9)
    assert T.coverage(sid)[3] == pytest.approx(0.5) and T.coverage(sid)[0] == 1.0
    assert T.coverage(sid, [1])[3] == 1.0
    with T.Session(sid) as s:
        s.remove_point(2)
    assert 2 not in T.points_at(sid, 3)
    assert T.delete_session(sid) and T.load(sid) is None
    with pytest.raises(ValueError):
        T.Session("../x")


def test_normalize_and_plan_runs():
    from cutora.fx import tracking as TR
    sp = TR.normalize({"session": "t1", "points": [{"id": 1}, {"id": 2, "enabled": False}, {"id": 1}, "x"],
                       "queries": {"10": [{"pt": 1, "x": 0.5, "y": 2}, {"pt": 1, "x": 0, "y": 0}, {"pt": 9, "x": 0, "y": 0}],
                                   "30": [{"pt": 1, "x": 0.6, "y": 0.5}, {"pt": 2, "x": 0.1, "y": 0.1}],
                                   "bad": [{"pt": 1, "x": 0, "y": 0}]},
                       "bindings": [{"mode": "follow", "target": {"kind": "overlay", "list": "text_overlays", "id": 3}},
                                    {"mode": "nope"}]})
    assert [p["id"] for p in sp["points"]] == [1, 2] and sp["points"][1]["enabled"] is False
    assert sp["queries"]["10"] == [{"pt": 1, "x": 0.5, "y": 1.0}] and "bad" not in sp["queries"]
    assert len(sp["bindings"]) == 1 and sp["bindings"][0]["apply"]["position"]
    assert TR.query_frames_of(sp, 1) == [10, 30]
    runs = TR.plan_runs(sp, (0, 40))
    by = {(r["start"], r["dir"]): r for r in runs}
    assert by[(10, "backward")]["end"] == 0 and by[(10, "forward")]["points"][1][2] == 20
    assert by[(30, "backward")]["points"][1][2] == 21 and by[(30, "forward")]["end"] == 40
    assert all(2 not in r["points"] for r in runs)                        # 关掉的点不跟
    assert [r["dir"] for r in TR.plan_runs(sp, (0, 40), "forward")] == ["forward", "forward"]
    assert TR.plan_runs(sp, (31, 40)) == []                                 # 范围里没有点选
    assert sum(TR.run_length(r) for r in runs) == 11 + 11 + 10 + 11
    assert TR.new_point(sp)["id"] == 3 and TR.is_empty(TR.normalize(None))


def test_fake_tracking_job_follows_patch_and_cancels(tmp_path):
    from cutora.fx import track_store as T
    from cutora.fx import tracking as TR
    vid = _patch_video(str(tmp_path / "p.mp4"), frames=30, dx=2.0, dy=1.0)
    sid = T.new_session_id()
    # 纹理块里两点：(70, 70) 和 (90, 85)（第 0 帧）
    sp = TR.normalize({"session": sid, "points": [{"id": 1}, {"id": 2}],
                       "queries": {"10": [{"pt": 1, "x": (70 + 20) / 320, "y": (70 + 10) / 180},
                                          {"pt": 2, "x": (90 + 20) / 320, "y": (85 + 10) / 180}]}})
    prog = []
    r = TR.track(TR.FakePointBackend(), vid, sp, (0, 29), on_progress=lambda d, t, f, v: prog.append((d, t, f, v)))
    assert r["frames"] == 31 and r["runs"] == 2 and not r["cancelled"]       # 往后 11 帧 + 往前 20 帧（第 10 帧两段都算）
    assert prog[-1][1] == 31 and prog[-1][0] == 31
    for f in (0, 5, 29):
        p = T.points_at(sid, f)
        assert p[1][0] * 320 == pytest.approx(70 + 2 * f, abs=0.8), f
        assert p[1][1] * 180 == pytest.approx(70 + 1 * f, abs=0.8), f
        assert p[2][0] * 320 == pytest.approx(90 + 2 * f, abs=0.8), f
    assert T.coverage(sid)[29] == 1.0
    # 取消
    sid2 = T.new_session_id()
    sp2 = dict(sp, session=sid2)
    ev = threading.Event()

    def _p(d, *_a):
        if d >= 4:
            ev.set()
    r2 = TR.track(TR.FakePointBackend(), vid, sp2, (0, 29), "forward", on_progress=_p, cancel=ev)
    assert r2["cancelled"] and 4 <= r2["frames"] <= 5
    with pytest.raises(RuntimeError):
        TR.track(TR.FakePointBackend(), vid, TR.normalize({"session": sid, "points": [{"id": 1}]}), (0, 29))


# ───────────────────────────── 运动求解 ─────────────────────────────

def test_similarity_and_point_motion_rotation_scale():
    from cutora.fx import motion as MO
    from cutora.fx import track_store as T
    th, s, cs, cd = MO.similarity([[0, 0], [10, 0]], [[5, 5], [5, 25]])
    assert th == pytest.approx(90.0) and s == pytest.approx(2.0)
    sid = T.new_session_id()
    base = {1: (-30.0, -10.0), 2: (30.0, -10.0), 3: (0.0, 25.0)}
    center = (200.0, 100.0)

    def at(f):
        ang, sc = math.radians(f * 1.5), 1.0 + 0.01 * f
        c, s_ = math.cos(ang), math.sin(ang)
        out = {}
        for pid, (x, y) in base.items():
            out[pid] = (center[0] + 2 * f + sc * (c * x - s_ * y), center[1] + f + sc * (s_ * x + c * y),
                        0.1 if (pid == 3 and 10 <= f <= 12) else 1.0)
        return out
    _write_track(sid, at, range(0, 31))
    mo = MO.point_motion(sid, [1, 2, 3], (0, 30), 0)
    ax0, ay0 = mo.anchor0
    assert (ax0, ay0) == pytest.approx((200.0, 100 + 5 / 3.0))
    ax, ay, rot, sc, lost = mo.at(20)
    assert rot == pytest.approx(30.0, abs=1e-3) and sc == pytest.approx(1.2, abs=1e-5) and not lost
    # 锚点 = 参考帧三点中心经同一变换
    a = math.radians(30)
    d = (0.0, 5 / 3.0)
    assert ax == pytest.approx(200 + 40 + 1.2 * (math.cos(a) * d[0] - math.sin(a) * d[1]), abs=1e-3)
    # 第 11 帧点 3 被挡：只用两点，结果仍准
    assert mo.at(11)[2] == pytest.approx(16.5, abs=1e-3) and not mo.at(11)[4]
    # 单点：只有平移
    m1 = MO.point_motion(sid, [1], (0, 30), 0)
    assert m1.at(10)[2] == 0.0 and m1.at(10)[3] == 1.0
    # 平滑以后参考帧不动
    ms = MO.point_motion(sid, [1, 2, 3], (0, 30), 10, smooth_frames=5)
    assert ms.at(10)[:2] == pytest.approx(mo.at(10)[:2], abs=1e-3) and ms.at(10)[2] == pytest.approx(0.0, abs=1e-9)
    assert ms.ref_frame == 10 and ms.anchor0 == pytest.approx(mo.at(10)[:2])


def test_point_motion_gaps_lost_and_runs():
    from cutora.fx import motion as MO
    from cutora.fx import track_store as T
    sid = T.new_session_id()
    _write_track(sid, lambda f: {1: (100 + 4 * f, 50, 0.0 if 5 <= f <= 7 else 1.0)}, range(0, 12))
    mo = MO.point_motion(sid, [1], (0, 11), 0)
    assert [bool(x) for x in mo.lost] == [False] * 5 + [True] * 3 + [False] * 4
    assert mo.at(6)[0] == pytest.approx(124.0)                  # 插值补的
    assert MO.lost_runs(mo) == [(5, 7)]
    assert MO.point_motion(sid, [9], (0, 11), 0) is None
    assert MO.smooth(np.arange(10.0), 3)[5] == pytest.approx(5.0)


def test_object_motion_from_matte():
    from cutora.fx import ai_matte as A
    from cutora.fx import matte_store as S
    from cutora.fx import motion as MO
    import cv2
    sid = S.new_session_id()
    with S.Session(sid, fps=30.0, src_w=400, src_h=200) as sess:
        for f in range(0, 10):
            m = np.zeros((200, 400), np.uint8)
            r = 20 + 2 * f
            cv2.circle(m, (100 + 10 * f, 100), r, 255, -1)
            sess.write(f, 1, m, 1.0)
    sp = A.normalize({"session": sid, "objects": [{"id": 1}]})
    mo = MO.object_motion(sp, (0, 9), 0)
    assert mo.at(5)[0] == pytest.approx(150.0, abs=0.6) and mo.at(5)[1] == pytest.approx(100.0, abs=0.6)
    assert mo.at(5)[3] == pytest.approx(30 / 20.0, rel=0.03) and mo.at(5)[2] == 0.0
    assert MO.object_motion(A.normalize({"session": "", "objects": []}), (0, 9), 0) is None


# ───────────────────────────── 烘焙 ─────────────────────────────

def _clip_task(w=400, h=200, canvas=(400, 200), dur=2.0):
    from cutora.model.timeline import Timeline
    from cutora.model.timeline_model import Clip, Track
    tl = Timeline(canvas[0], canvas[1], 30.0)
    tr = Track("video")
    tr.id = "v0"
    clip = Clip("s", 0.0, dur, 0.0)
    clip.fit_mode = "stretch"
    tr.clips.append(clip)
    tl.add_track(tr)
    ov = {"id": 7, "type": "text", "content": "TAG", "x": 150.0, "y": 80.0, "w": 60.0, "h": 20.0,
          "start": 0.0, "end": dur, "rotation": 0.0, "scale": 1.0}
    task = {"timeline": tl, "duration": dur, "text_overlays": [ov], "image_overlays": [], "segments": [], "style": {},
            "dims": canvas}
    return task, tr, clip, ov


def test_clip_geom_matches_fit_and_flip():
    from cutora.fx.track_bind import ClipGeom, fit_rect
    _task, _tr, clip, _ov = _clip_task()
    g = ClipGeom(clip, 0.0, 400, 200, 400, 200)
    assert g.to_canvas(0, 0) == pytest.approx((0.0, 0.0)) and g.to_canvas(400, 200) == pytest.approx((400.0, 200.0))
    clip.flip_x = True
    assert ClipGeom(clip, 0.0, 400, 200, 400, 200).to_canvas(0, 0) == pytest.approx((400.0, 0.0))
    clip.flip_x = False
    clip.crop_left = 100
    clip.scale = 0.5
    clip.x = 20
    g = ClipGeom(clip, 0.0, 400, 200, 400, 200)
    assert g.rect == pytest.approx((120.0, 50.0, 200.0, 100.0))
    assert g.to_canvas(100, 0) == pytest.approx((120.0, 50.0))
    clip.rotation = 90
    g = ClipGeom(clip, 0.0, 400, 200, 400, 200)
    assert g.to_canvas(400, 100) == pytest.approx((220.0, 200.0))      # 右边中点转到下面
    assert fit_rect(100, 50, 400, 200, "fit") == pytest.approx((0, 0, 400, 200))
    assert fit_rect(100, 100, 400, 200, "fill")[2] == pytest.approx(400.0)


def _motion_translate(sid, frames=60, dx=3.0, dy=-1.0):
    from cutora.fx import motion as MO
    _write_track(sid, lambda f: {1: (100 + dx * f, 100 + dy * f, 1.0)}, range(0, frames))
    return MO.point_motion(sid, [1], (0, frames - 1), 0)


def test_bake_follow_overlay_position_rotation_scale():
    from cutora.fx import track_bind as TB
    from cutora.fx import track_store as T
    from cutora.render.keyframe import value_at
    task, _tr, clip, ov = _clip_task()
    mo = _motion_translate(T.new_session_id())
    b = TB.normalize_binding({"mode": "follow", "target": {"kind": "overlay", "list": "text_overlays", "id": 7},
                              "ref_frame": 0, "simplify": False})
    res = TB.bake_follow(mo, clip, ov, b, 400, 200)
    assert set(res["paths"]) == {"x", "y"} and res["frames"] == 60
    xs = res["paths"]["x"]
    assert xs[0]["t"] == 0.0 and xs[0]["v"] == pytest.approx(150.0)
    assert xs[30]["v"] == pytest.approx(150.0 + 90.0) and res["paths"]["y"][30]["v"] == pytest.approx(80.0 - 30.0)
    # 精简：匀速直线只剩两头
    b2 = dict(b, simplify=True)
    r2 = TB.bake_follow(mo, clip, ov, b2, 400, 200)
    assert len(r2["paths"]["x"]) == 2
    ov2 = dict(ov, keyframes={"x": r2["paths"]["x"], "y": r2["paths"]["y"]})
    assert value_at(ov2, "x", 1.0, 0.0) == pytest.approx(150.0 + 90.0)
    # 片段放大 2 倍 + 水平翻转：位移跟着变大、方向反过来
    clip.scale = 2.0
    clip.flip_x = True
    r3 = TB.bake_follow(mo, clip, ov, b, 400, 200)
    assert r3["paths"]["x"][30]["v"] - r3["paths"]["x"][0]["v"] == pytest.approx(-180.0)
    clip.scale, clip.flip_x = 1.0, False
    # 参考帧在中间：目标当前位置在参考帧保持不变
    b4 = TB.normalize_binding(dict(b, ref_frame=30))
    r4 = TB.bake_follow(mo, clip, ov, b4, 400, 200)
    assert r4["paths"]["x"][30]["v"] == pytest.approx(150.0) and r4["paths"]["x"][0]["v"] == pytest.approx(60.0)
    # 用户偏移
    r5 = TB.bake_follow(mo, clip, ov, TB.normalize_binding(dict(b, offset=[10, -5])), 400, 200)
    assert r5["paths"]["x"][0]["v"] == pytest.approx(160.0) and r5["paths"]["y"][0]["v"] == pytest.approx(75.0)


def test_bake_follow_rotation_scale_orbit():
    from cutora.fx import motion as MO
    from cutora.fx import track_bind as TB
    from cutora.fx import track_store as T
    task, _tr, clip, ov = _clip_task()
    sid = T.new_session_id()

    def at(f):
        a = math.radians(2 * f)
        out = {}
        for pid, (x, y) in {1: (-20.0, 0.0), 2: (20.0, 0.0)}.items():
            out[pid] = (200 + x * math.cos(a) - y * math.sin(a), 100 + x * math.sin(a) + y * math.cos(a), 1.0)
        return out
    _write_track(sid, at, range(0, 46))
    mo = MO.point_motion(sid, [1, 2], (0, 45), 0)
    b = TB.normalize_binding({"mode": "follow", "target": {"kind": "overlay", "list": "text_overlays", "id": 7},
                              "apply": {"position": True, "rotation": True, "scale": True}, "simplify": False})
    res = TB.bake_follow(mo, clip, ov, b, 400, 200)
    # 第 45 帧转了 90°：覆层中心 (180, 90) 绕锚点 (200, 100) 转 90° → (210, 80)
    x45, y45 = res["paths"]["x"][45]["v"], res["paths"]["y"][45]["v"]
    assert (x45 + 30, y45 + 10) == pytest.approx((210.0, 80.0), abs=1e-3)
    assert res["paths"]["rotation"][45]["v"] == pytest.approx(90.0, abs=1e-3)
    assert res["paths"]["scale"][45]["v"] == pytest.approx(1.0, abs=1e-5)


def test_bake_stabilize_keeps_point_still_and_fills():
    from cutora.fx import track_bind as TB
    from cutora.fx import track_store as T
    from cutora.render.keyframe import value_at
    task, _tr, clip, _ov = _clip_task()
    mo = _motion_translate(T.new_session_id(), dx=2.0, dy=1.0)
    b = TB.normalize_binding({"mode": "stabilize", "fill": "none", "simplify": False})
    res = TB.bake_stabilize(mo, clip, b, 400, 200)
    assert set(res["paths"]) == {"x", "y"} and res["zoom"] == 1.0
    clip.keyframes = dict(res["paths"])
    for f in (0, 17, 59):
        t = f / 30.0
        g = TB.ClipGeom(clip, t, 400, 200, 400, 200)
        assert g.to_canvas(*mo.at(f)[:2]) == pytest.approx((100.0, 100.0), abs=1e-3), f
    assert value_at(clip, "x", 59 / 30.0, 0.0) == pytest.approx(-118.0)
    clip.keyframes = {}
    # 自动填满：放大以后每帧都不露边
    bf = TB.normalize_binding({"mode": "stabilize", "fill": "auto", "simplify": False})
    rf = TB.bake_stabilize(mo, clip, bf, 400, 200)
    assert rf["zoom"] > 1.0 and "scale" in rf["paths"]
    clip.keyframes = dict(rf["paths"])
    for f in (0, 30, 59):
        g = TB.ClipGeom(clip, f / 30.0, 400, 200, 400, 200)
        assert g.covers_canvas(margin=1.0), f
        assert g.to_canvas(*mo.at(f)[:2]) == pytest.approx((100.0, 100.0), abs=1e-3), f
    clip.keyframes = {}


def test_simplify_rdp():
    from cutora.fx.track_bind import simplify
    ts = list(range(10))
    assert simplify(ts, [2.0 * t for t in ts], 0.1) == [0, 9]
    zig = [0, 0, 0, 5, 0, 0, 0, 0, 0, 0]
    assert 3 in simplify(ts, zig, 0.1)
    assert simplify([0, 1], [0, 1], 0.1) == [0, 1]


def test_binding_normalize_and_same_target():
    from cutora.fx.track_bind import normalize_binding, same_target
    assert normalize_binding({"mode": "x"}) is None and normalize_binding("x") is None
    s = normalize_binding({"mode": "stabilize", "target": {"kind": "overlay", "list": "text_overlays"}, "smooth": 999})
    assert s["target"] == {"kind": "self"} and s["smooth"] == 61
    a = normalize_binding({"mode": "follow", "target": {"kind": "overlay", "list": "text_overlays", "id": 3, "index": 0}})
    b = normalize_binding({"mode": "follow", "target": {"kind": "overlay", "list": "text_overlays", "id": 3, "index": 5}})
    c = normalize_binding({"mode": "follow", "target": {"kind": "clip", "track_id": "v1", "clip_index": 0}})
    assert same_target(a, b) and not same_target(a, c)
    assert normalize_binding({"mode": "follow", "target": {"kind": "weird"}}) is None


# ───────────────────────────── 命令 ─────────────────────────────

def test_track_commands_and_undo():
    _app()
    from test_q62_keyframes import _win_for_task
    from cutora.core.commands import get_bus, reset_bus_for_tests
    from cutora.fx import track_bind as TB
    from cutora.fx import track_store as T
    task, tr, clip, ov = _clip_task()
    reset_bus_for_tests()
    win = _win_for_task(task)
    bus = get_bus()
    bus.bind_window(win)
    loc = {"target": "clip", "track_id": "v0", "clip_index": 0}
    with pytest.raises(Exception):
        bus.execute("track.session", {"target": "overlay", "list": "text_overlays", "overlay_index": 0, "op": "create"},
                    source="ui")
    sid = bus.execute("track.session", dict(loc, op="create", model="track-tapnextpp-256"), source="ui")["session"]
    assert clip.tracking["session"] == sid and clip.tracking["model"] == "track-tapnextpp-256"
    assert [p["id"] for p in clip.tracking["points"]] == [1]
    bus.execute("track.point", dict(loc, op="add", name="右眼"), source="ui")
    bus.execute("track.point", dict(loc, op="place", point_id=1, frame=5, x=0.25, y=0.5), source="ui")
    bus.execute("track.point", dict(loc, op="place", point_id=1, frame=5, x=0.3, y=0.5), source="ui")     # 挪
    bus.execute("track.point", dict(loc, op="place", point_id=2, frame=5, x=0.6, y=0.4), source="ui")
    assert clip.tracking["queries"]["5"] == [{"pt": 1, "x": 0.3, "y": 0.5}, {"pt": 2, "x": 0.6, "y": 0.4}]
    with pytest.raises(Exception):
        bus.execute("track.point", dict(loc, op="place", point_id=9, frame=5, x=0.1, y=0.1), source="ui")
    bus.execute("track.point", dict(loc, op="unplace", point_id=2, frame=5), source="ui")
    assert clip.tracking["queries"]["5"] == [{"pt": 1, "x": 0.3, "y": 0.5}]
    bus.execute("track.set", dict(loc, patch={"points": [{"id": 2, "enabled": False, "color": "#112233"}]}), source="ui")
    assert clip.tracking["points"][1]["enabled"] is False and clip.tracking["points"][1]["color"] == "#112233"
    # apply：跟随文字覆层
    mo = _motion_translate(sid)
    b = TB.normalize_binding({"mode": "follow", "target": {"kind": "overlay", "list": "text_overlays", "id": 7, "index": 0}})
    res = TB.bake_follow(mo, clip, ov, b, 400, 200)
    bus.execute("track.apply", dict(loc, target_loc={"target": "overlay", "list": "text_overlays", "overlay_index": 0},
                                    paths=res["paths"], binding=b), source="ui")
    ov_now = win.tasks[0]["text_overlays"][0]
    assert len(ov_now["keyframes"]["x"]) == 2 and len(clip.tracking["bindings"]) == 1
    # 同一目标再应用：覆盖不重复
    bus.execute("track.apply", dict(loc, target_loc={"target": "overlay", "list": "text_overlays", "overlay_index": 0},
                                    paths=res["paths"], binding=b), source="ui")
    assert len(clip.tracking["bindings"]) == 1
    with pytest.raises(Exception):
        bus.execute("track.apply", dict(loc, target_loc=loc, paths=res["paths"], binding=b), source="ui")
    # 稳定：写到片段自己
    bs = TB.normalize_binding({"mode": "stabilize"})
    rs = TB.bake_stabilize(mo, clip, bs, 400, 200)
    bus.execute("track.apply", dict(loc, paths=rs["paths"], binding=bs), source="ui")
    assert "x" in clip.keyframes and len(clip.tracking["bindings"]) == 2
    win.undo()
    c2 = win.tasks[0]["timeline"].tracks[0].clips[0]
    assert not (getattr(c2, "keyframes", None) or {}).get("x") and len(c2.tracking["bindings"]) == 1
    bus.execute("track.unbind", dict(loc, index=0), source="ui")
    c3 = win.tasks[0]["timeline"].tracks[0].clips[0]
    assert c3.tracking["bindings"] == [] and win.tasks[0]["text_overlays"][0]["keyframes"]["x"]    # 关键帧保留
    bus.execute("track.point", dict(loc, op="remove", point_id=1), source="ui")
    c4 = win.tasks[0]["timeline"].tracks[0].clips[0]
    assert [p["id"] for p in c4.tracking["points"]] == [2] and c4.tracking["queries"] == {}
    with T.Session(sid, fps=30.0, src_w=4, src_h=4) as s:
        s.write(0, 2, 0.5, 0.5, 1.0)
    bus.execute("track.session", dict(loc, op="detach"), source="ui")
    assert win.tasks[0]["timeline"].tracks[0].clips[0].tracking == {} and T.load(sid) is None


def test_clip_schema_has_tracking():
    from cutora.model.project_schema import CLIP_KEYS
    from cutora.model.timeline_model import Clip
    assert "tracking" in CLIP_KEYS and Clip("s", 0, 1, 0).tracking == {}


# ───────────────────────────── 登记 / 校验缓存 ─────────────────────────────

def test_track_models_registered_and_pinned():
    from cutora.asr.models import catalog, get_model, iter_files
    from cutora.core.licenses import is_license_allowed
    rows = {r["id"]: r for r in catalog() if r.get("kind") == "track"}
    assert {"track-tapnextpp-512", "track-tapnextpp-256"} <= set(rows)
    for r in rows.values():
        assert r["license"] == "Apache-2.0" and is_license_allowed(r["license"]) and r["repo"] == "google/tapnet"
        assert len(r["revision"]) == 40 and not r.get("gated") and r.get("enabled", True)
        files = iter_files(r)
        assert len(files) == 1 and files[0]["filename"] == r["weight_file"] and len(files[0]["sha256"]) == 64
        assert files[0]["sha256"] == r["sha256"] and int(files[0]["size"]) > 2_000_000_000
    assert rows["track-tapnextpp-512"]["input_resolution"] == 512 and get_model("track-tapnextpp-512").get("default")
    assert rows["track-tapnextpp-256"]["input_resolution"] == 256


def test_verify_weight_hash_is_cached(tmp_path, monkeypatch):
    import hashlib
    import time
    from cutora.asr import models as Mo
    p = tmp_path / "w.bin"
    p.write_bytes(b"abc" * 1000)
    want = hashlib.sha256(b"abc" * 1000).hexdigest()
    calls = []
    real = Mo.sha256_file
    monkeypatch.setattr(Mo, "sha256_file", lambda path: calls.append(path) or real(path))
    assert Mo.verify_weight(str(p), want) == "ready" and Mo.verify_weight(str(p), want) == "ready"
    assert len(calls) == 1
    time.sleep(0.01)
    p.write_bytes(b"xyz" * 1000)                 # 改了 → 重算
    os.utime(p, None)
    assert Mo.verify_weight(str(p), want) == "checksum_failed" and len(calls) == 2


# ───────────────────────────── 检查器 + 画布 + 时间线 ─────────────────────────────

def _wait(panel, timeout=30.0):
    import time
    t0 = time.time()
    while panel._running() and time.time() - t0 < timeout:
        QApplication.processEvents()
        time.sleep(0.02)
    QApplication.processEvents()
    assert not panel._running()


def _mouse(typ, x, y, mods=Qt.KeyboardModifier.NoModifier, button=Qt.MouseButton.LeftButton):
    return QMouseEvent(typ, QPointF(x, y), QPointF(x, y), button,
                       button if typ != QEvent.Type.MouseButtonRelease else Qt.MouseButton.NoButton, mods)


def test_inspector_canvas_track_and_apply(tmp_path):
    _app()
    from test_q145_canvas_shortcuts import _click, _w
    from test_q31_keyframes import _task_with_clip
    from test_q62_keyframes import _win_for_task
    from cutora.core.commands import get_bus, reset_bus_for_tests
    from cutora.fx import track_store as T
    from cutora.preview.engine import PreviewEngine
    from cutora.render.keyframe import value_at
    from cutora.ui.canvas import VideoCanvas
    from cutora.ui.panels.property import PropertyPanel
    from cutora.ui.timeline.widget import TimelineWidget
    media = _patch_video(str(tmp_path / "p.mp4"), frames=30, dx=2.0, dy=0.0)
    task, tr, clip = _task_with_clip(media, start_t=0.0, duration=1.0, canvas=(320, 180))
    task["text_overlays"] = [{"id": 11, "type": "text", "content": "LABEL", "x": 40.0, "y": 20.0, "w": 60.0,
                              "h": 20.0, "start": 0.0, "end": 1.0, "rotation": 0.0, "scale": 1.0}]
    reset_bus_for_tests()
    win = _win_for_task(task)
    get_bus().bind_window(win)
    win._timeline_ms = 0

    def _seek(ms, _w=win):
        _w._timeline_ms = ms
        c.set_data(_w.tasks[0], ms / 1000.0)
    win.on_seek_request = _seek
    c = VideoCanvas()
    c.setParent(win)
    c.setFixedSize(640, 360)
    panel = PropertyPanel()
    panel.setParent(win)
    win.prop_panel = panel
    win.video_canvas = c
    tlw = TimelineWidget()
    tlw.set_data(1000, task)
    win.timeline = tlw
    win.show()
    try:
        c.set_data(task, 0.0)
        c.set_program_frame(PreviewEngine().video_frame_at(task, 0.0))
        c.repaint()
        QApplication.processEvents()
        _click(c, *_w(c, 250, 150))
        assert c._selected_item and c._selected_item.get("type") == "video_clip"
        panel.load_data("video_clip", (tr, clip))
        tp = panel.vid_fx.track
        assert tp is not None and tp.sec.isVisibleTo(panel)
        assert tp.model_status()[0] == "fake" and not tp.btn_download.isVisibleTo(tp)
        assert tp.btn_place.isEnabled() and not tp.btn_all.isEnabled()
        tp.btn_place.setChecked(True)
        assert c._mask_mode and c._mask_mode["kind"] == "track" and clip.tracking["session"]
        sid = clip.tracking["session"]
        # 放点 1：纹理块里 (70, 70)
        x, y = _w(c, 70, 70)
        c.mousePressEvent(_mouse(QEvent.Type.MouseButtonPress, x, y))
        c.mouseReleaseEvent(_mouse(QEvent.Type.MouseButtonRelease, x, y))
        q = clip.tracking["queries"]["0"][0]
        assert q["pt"] == 1 and q["x"] == pytest.approx(70 / 320, abs=0.01)
        # 加第 2 个点并放下
        tp._on_point_add()
        assert tp.current_point_id() == 2
        x2, y2 = _w(c, 90, 85)
        c.mousePressEvent(_mouse(QEvent.Type.MouseButtonPress, x2, y2))
        assert len(clip.tracking["queries"]["0"]) == 2
        # Alt+点 = 去掉最近的点选
        c.mousePressEvent(_mouse(QEvent.Type.MouseButtonPress, x2, y2, mods=Qt.KeyboardModifier.AltModifier))
        assert len(clip.tracking["queries"]["0"]) == 1
        c.mousePressEvent(_mouse(QEvent.Type.MouseButtonPress, x2, y2))
        assert len(clip.tracking["queries"]["0"]) == 2
        c.repaint()                                                   # 画轨迹 / 方框
        # 跟踪
        assert tp.btn_all.isEnabled()
        tp._on_track("all")
        assert tp._running() and tp.prog.isVisibleTo(tp)
        _wait(tp)
        assert "跟踪完成" in tp.lbl_info.text() and "帧" in tp.sec.section_title
        p = T.points_at(sid, 25)
        assert p[1][0] * 320 == pytest.approx(70 + 50, abs=1.0)
        c.repaint()
        # 时间线跟踪条
        img = QImage(400, 60, QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(0)
        pa = QPainter(img)
        tlw.pixels_per_second = 300.0
        tlw._paint_track_band(pa, clip, QRectF(0, 10, 300, 40), 0.0)
        pa.end()
        assert img.pixelColor(100, 15).blue() > 150
        # Esc 退出放点
        from PySide6.QtGui import QKeyEvent
        c.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier))
        assert c._mask_mode is None and not tp.btn_place.isChecked()
        # 应用：文字跟着走
        tp.reload()
        assert tp.target_combo.count() >= 1 and tp.target_combo.currentText().startswith("ov:text_overlays")
        assert tp.btn_apply.isEnabled()
        tp.btn_apply.click()
        ov = win.tasks[0]["text_overlays"][0]
        assert ov["keyframes"]["x"] and "已生成关键帧" in tp.lbl_info.text()
        assert value_at(ov, "x", 25 / 30.0, 0.0) == pytest.approx(40 + 50, abs=1.0)
        assert value_at(ov, "y", 25 / 30.0, 0.0) == pytest.approx(20, abs=1.0)
        assert tp.bind_list.count() == 1 and tp.btn_rebake.isEnabled()
        # 更新全部绑定（重新跟踪以后）
        tp._on_rebake_all()
        assert "已更新 1 / 1" in tp.lbl_info.text()
        # 稳定
        tp.mode_combo.setCurrentText("stabilize")
        tp.chk_fill.setChecked(False)
        tp.btn_apply.click()
        cl = win.tasks[0]["timeline"].tracks[0].clips[0]
        assert cl.keyframes.get("x") and tp.bind_list.count() == 2
        assert value_at(cl, "x", 25 / 30.0, 0.0) == pytest.approx(-50 * (320 / 320.0), abs=1.5)
    finally:
        c.close()
        win.close()


def test_overlay_panel_has_no_track_section():
    from test_q152a2_keyable_motion import _panel_env
    win, panel, _task, _ov, _clip = _panel_env("overlay")
    try:
        assert panel.ov_fx.track is None or not panel.ov_fx.track.sec.isVisibleTo(panel)
    finally:
        win.close()
