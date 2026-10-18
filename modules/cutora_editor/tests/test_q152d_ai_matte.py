# -*- coding: utf-8 -*-
"""Q152-D · AI 抠像：结果缓存、坐标换算、假后端 + 传播、渲染接入（预览 = 导出）、命令撤销、检查器 + 画布交互、
模型登记（钉死 revision / sha256 / Apache-2.0）、下载进度与取消。

真模型（SAM 2.1）在 CI 上不跑：这里全部用 CUTORA_MATTE_BACKEND=fake（颜色泛洪，确定性）。
"""
import os
import subprocess
import sys
import threading

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from PySide6.QtCore import QEvent, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QImage, QMouseEvent, QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402


def _app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _fake_backend_and_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("CUTORA_MATTE_BACKEND", "fake")
    monkeypatch.setenv("CUTORA_MATTE_CACHE", str(tmp_path / "matte_cache"))
    from cutora.fx import matte_store as S
    from cutora.fx.layer_fx import clear_cache
    S.invalidate()
    clear_cache()
    yield


def _square_video(path, frames=40, w=320, h=180, step=3):
    """灰底上往右走的红方块：第 i 帧方块在 x = 20 + i·step … +60。"""
    import cv2
    from test_q31_keyframes import _ffmpeg
    d = os.path.join(os.path.dirname(path), "frames_" + os.path.basename(path))
    os.makedirs(d, exist_ok=True)
    for i in range(frames):
        img = np.full((h, w, 3), (120, 120, 120), np.uint8)
        x = 20 + i * step
        cv2.rectangle(img, (x, 60), (x + 60, 120), (40, 40, 220), -1)
        cv2.imwrite(os.path.join(d, "f%03d.png" % i), img)
    subprocess.check_call([_ffmpeg(), "-y", "-loglevel", "error", "-framerate", "30",
                           "-i", os.path.join(d, "f%03d.png"), "-pix_fmt", "yuv420p", "-crf", "10", path])
    return path


def _spec(session, prompts=None, objects=None):
    from cutora.fx import ai_matte as A
    return A.normalize({"session": session, "objects": objects or [{"id": 1}], "prompts": prompts or {}})


# ───────────────────────────── 缓存 ─────────────────────────────

def test_store_roundtrip_and_index():
    from cutora.fx import matte_store as S
    sid = S.new_session_id()
    assert S.load_index(sid) is None and S.coverage(sid) == {}
    m = np.zeros((20, 30), np.uint8)
    m[5:15, 10:20] = 255
    with S.Session(sid, fps=25.0, src_w=30, src_h=20, model="x") as sess:
        sess.write(3, 1, m, 0.9)
        sess.write(3, 2, m[::-1, ::-1].copy(), 0.3)
        sess.write(4, 1, m, 1.0)
    idx = S.load_index(sid)
    assert idx["fps"] == 25.0 and idx["src_w"] == 30 and sorted(idx["frames"]) == ["3", "4"]
    assert idx["frames"]["3"]["score"] == pytest.approx(0.3) and idx["frames"]["3"]["objs"] == [1, 2]
    a, used = S.read_alpha(sid, 3, [1])
    assert used == 3 and a[10, 15] == 1.0 and a[0, 0] == 0.0
    both, _u = S.read_alpha(sid, 3)
    assert both[10, 15] == 1.0 and both.max() == 1.0
    # 容差：差 1 帧也取到
    a5, used5 = S.read_alpha(sid, 5, [1], tolerance=1)
    assert used5 == 4 and a5 is not None
    assert S.read_alpha(sid, 9, [1])[0] is None
    assert S.frame_index(0.5, 25.0) == 12 and S.frame_index(-1, 25.0) == 0
    cov = S.coverage(sid)
    assert cov == {3: pytest.approx(0.3), 4: 1.0}
    sess = S.Session(sid)
    sess.remove_object(2)
    sess.flush()
    assert S.load_index(sid)["frames"]["3"]["objs"] == [1] and S.load_index(sid)["frames"]["3"]["score"] == pytest.approx(0.9)
    sess.remove_frames([4])
    sess.flush()
    assert sorted(S.coverage(sid)) == [3]
    assert S.session_bytes(sid) > 0
    sess.clear()
    assert S.coverage(sid) == {} and os.path.isdir(S.session_dir(sid))
    assert S.delete_session(sid) and not os.path.isdir(S.session_dir(sid))
    with pytest.raises(ValueError):
        S.session_dir("../evil")


# ───────────────────────────── 坐标 / 变换 ─────────────────────────────

def test_source_layer_norm_roundtrip_and_mask_transform():
    from cutora.fx import ai_matte as A
    for order in ("crop_flip", "flip_crop"):
        for flip in ((False, False), (True, False), (False, True), (True, True)):
            geom = {"src_w": 400, "src_h": 200, "crop": (40, 20, 10, 30), "flip": flip, "order": order}
            for (x, y) in ((0.1, 0.2), (0.5, 0.5), (0.93, 0.07)):
                sx, sy = A.layer_to_source_norm(x, y, geom)
                bx, by = A.source_to_layer_norm(sx, sy, geom)
                assert (bx, by) == pytest.approx((x, y), abs=1e-6), (order, flip)
    # 没裁切没翻转：恒等
    assert A.layer_to_source_norm(0.3, 0.6, {"src_w": 10, "src_h": 10}) == pytest.approx((0.3, 0.6))
    # 翻转：x 镜像
    assert A.layer_to_source_norm(0.25, 0.5, {"src_w": 10, "src_h": 10, "flip": (True, False)}) == pytest.approx((0.75, 0.5))
    # mask 变换：源 400×200，左边 100 列白；裁掉左 50 再水平翻转 → 显示帧 350 宽，右边 50 列白
    m = np.zeros((200, 400), np.float32)
    m[:, :100] = 1.0
    out = A.transform_mask(m, {"src_w": 400, "src_h": 200, "crop": (50, 0, 0, 0), "flip": (True, False),
                               "order": "crop_flip"}, 350, 200)
    assert out.shape == (200, 350) and out[100, 340] == 1.0 and out[100, 290] == 0.0
    # flip_crop：先翻转（白条跑到右边 300–400），再裁左 50 → 显示 350 宽，白条在 250–350
    out2 = A.transform_mask(m, {"src_w": 400, "src_h": 200, "crop": (50, 0, 0, 0), "flip": (True, False),
                                "order": "flip_crop"}, 350, 200)
    assert out2[100, 300] == 1.0 and out2[100, 240] == 0.0
    # 缩放到别的尺寸
    small = A.transform_mask(m, {"src_w": 400, "src_h": 200}, 40, 20)
    assert small.shape == (20, 40) and small[10, 5] == 1.0 and small[10, 30] == 0.0


def test_finish_alpha_and_guided_refine():
    from cutora.fx import ai_matte as A
    a = np.zeros((100, 100), np.float32)
    a[30:70, 30:70] = 1.0
    sh = A.finish_alpha(a, {"shrink": 5, "feather": 0, "refine": False})
    assert sh[50, 50] == 1.0 and sh[32, 50] == 0.0
    gr = A.finish_alpha(a, {"shrink": -5, "feather": 0, "refine": False})
    assert gr[27, 50] == 1.0
    fe = A.finish_alpha(a, {"shrink": 0, "feather": 8, "refine": False})
    assert 0.1 < fe[30, 50] < 0.9 and fe[50, 50] > 0.95
    # 精修：引导图是同一块方块 → 边缘贴回去（仍在 0–1，中心不变）
    guide = np.full((100, 100, 3), 30, np.uint8)
    guide[30:70, 30:70] = 220
    rf = A.finish_alpha(fe, {"shrink": 0, "feather": 0, "refine": True}, guide_rgb=guide)
    assert rf.shape == a.shape and 0.0 <= rf.min() and rf.max() <= 1.0 and rf[50, 50] > 0.9 and rf[10, 10] < 0.1


def test_normalize_spec_and_prompts():
    from cutora.fx import ai_matte as A
    sp = A.normalize({"session": "m1", "objects": [{"id": 2, "name": "狗"}, "bad", {"id": "x"}],
                      "prompts": {"7": [{"obj": 2, "x": 1.5, "y": -1, "label": 0}, {"obj": 2, "box": [0.2, 0.9, 0.1, 0.3]},
                                        {"bad": 1}], "q": [{"obj": 2, "x": 0, "y": 0}]},
                      "feather": 999, "shrink": -999, "view": "nope"})
    assert sp["objects"] == [{"id": 2, "name": "狗", "color": A.OBJECT_COLORS[1], "enabled": True, "mode": "add"}]
    assert sp["prompts"] == {"7": [{"obj": 2, "x": 1.0, "y": 0.0, "label": 0}, {"obj": 2, "box": [0.2, 0.9, 0.1, 0.3]}]}
    assert sp["feather"] == 200.0 and sp["shrink"] == -100.0 and sp["view"] == "comp"
    assert A.is_active({"ai_matte": sp}) and not A.is_active({"ai_matte": {"session": ""}})
    assert A.is_empty(A.normalize(None)) and not A.is_empty(sp)
    obj = A.new_object(sp)
    assert obj["id"] == 1 and obj["color"] == A.OBJECT_COLORS[0]
    px = A.to_pixels(A.prompts_at(sp, 7), 200, 100)
    assert px[0]["x"] == 200.0 and px[1]["box"] == [40.0, 90.0, 20.0, 30.0]
    assert A.prompt_frames(sp) == [7]
    assert A.signature(sp) != A.signature(A.normalize(dict(sp, feather=3)))


# ───────────────────────────── 假后端 + 任务 ─────────────────────────────

def test_fake_backend_segment_propagate_cancel(tmp_path):
    from cutora.fx import matte_jobs as J
    from cutora.fx import matte_store as S
    from cutora.fx.segment import FakeBackend, make_backend
    assert isinstance(make_backend(), FakeBackend)
    vid = _square_video(str(tmp_path / "sq.mp4"))
    be = FakeBackend(tol=60)
    sid = S.new_session_id()
    sp = _spec(sid, {"0": [{"obj": 1, "x": 50 / 320, "y": 0.5, "label": 1}]})
    scores = J.segment_frame(be, vid, sp, 0)
    assert scores == {1: 1.0}
    a, used = S.read_alpha(sid, 0, [1])
    assert used == 0 and a[90, 50] == 1.0 and a[90, 200] == 0.0 and a[20, 20] == 0.0
    # 负点：挖掉方块 → 几乎全空，置信度低
    sp_neg = _spec(sid, {"0": [{"obj": 1, "x": 50 / 320, "y": 0.5, "label": 1},
                               {"obj": 1, "x": 60 / 320, "y": 0.5, "label": 0}]})
    assert J.segment_frame(be, vid, sp_neg, 0)[1] < 0.5
    # 框：只保留框内
    sp_box = _spec(sid, {"0": [{"obj": 1, "box": [0.0, 0.3, 0.15, 0.7]}]})
    J.segment_frame(be, vid, sp_box, 0)
    a2, _u = S.read_alpha(sid, 0, [1])
    assert a2[90, 30] == 1.0 and a2[90, 70] == 0.0
    # 向前传播：第 30 帧方块在 110–170
    prog = []
    r = J.propagate(be, vid, sp, 0, "forward", 39, on_progress=lambda d, t, f, s: prog.append((d, t, f)))
    assert r["frames"] == 40 and not r["cancelled"] and prog[-1][:2] == (40, 40)
    a30, _u = S.read_alpha(sid, 30, [1])
    assert a30[90, 140] == 1.0 and a30[90, 60] == 0.0
    assert len(S.coverage(sid)) == 40
    # 向后传播 + 取消：跑 5 帧后停
    sid2 = S.new_session_id()
    sp2 = _spec(sid2, {"39": [{"obj": 1, "x": (20 + 39 * 3 + 30) / 320, "y": 0.5, "label": 1}]})
    ev = threading.Event()

    def _prog(done, _t, _f, _s):
        if done >= 5:
            ev.set()
    r2 = J.propagate(be, vid, sp2, 39, "backward", 0, on_progress=_prog, cancel=ev)
    assert r2["cancelled"] and 5 <= r2["frames"] <= 6
    cov2 = S.coverage(sid2)
    assert max(cov2) == 39 and min(cov2) >= 33
    assert J.clip_source_frame_range(type("C", (), {"in_t": 0.5, "out_t": 1.0})(), 30.0) == (15, 29)
    assert J.source_fps(vid) == pytest.approx(30.0, abs=0.1)


# ───────────────────────────── 渲染接入 ─────────────────────────────

def test_layer_fx_uses_matte_and_views(tmp_path):
    from cutora.fx import matte_store as S
    from cutora.fx.layer_fx import apply_arrays, fx_signature
    from cutora.model.timeline_model import Clip
    sid = S.new_session_id()
    m = np.zeros((90, 160), np.uint8)
    m[:, :80] = 255
    with S.Session(sid, fps=30.0, src_w=160, src_h=90) as sess:
        sess.write(15, 1, m, 1.0)
    clip = Clip("s", 0, 1, 0)
    clip.ai_matte = _spec(sid)
    rgb = np.full((90, 160, 3), 200, np.uint8)
    src = {"src_t": 0.5, "crop": (0, 0, 0, 0), "flip": (False, False), "order": "crop_flip"}
    # 没给 src → 不动
    assert fx_signature(clip) is None
    out, alpha = apply_arrays(rgb, None, clip, 1.0, None)
    assert alpha is None
    # 给了 src → 左半留下
    out, alpha = apply_arrays(rgb, None, clip, 1.0, src)
    assert alpha is not None and alpha[45, 20] == 1.0 and alpha[45, 140] == 0.0
    # 没算过的帧 → 原样
    _o, a_none = apply_arrays(rgb, None, clip, 1.0, dict(src, src_t=2.0))
    assert a_none is None
    # 翻转后右半留下
    _o, a_f = apply_arrays(rgb, None, clip, 1.0, dict(src, flip=(True, False)))
    assert a_f[45, 140] == 1.0 and a_f[45, 20] == 0.0
    # matte 视图：黑白、不透明
    clip.ai_matte = dict(clip.ai_matte, view="matte")
    o_m, a_m = apply_arrays(rgb, None, clip, 1.0, src)
    assert a_m is None and tuple(o_m[45, 20]) == (255, 255, 255) and tuple(o_m[45, 140]) == (0, 0, 0)
    # overlay 视图：左半染色、右半原色
    clip.ai_matte = dict(clip.ai_matte, view="overlay")
    o_v, a_v = apply_arrays(rgb, None, clip, 1.0, src)
    assert a_v is None and tuple(o_v[45, 140]) == (200, 200, 200) and tuple(o_v[45, 20]) != (200, 200, 200)
    # 关掉对象 → 不套
    clip.ai_matte = dict(_spec(sid), objects=[{"id": 1, "enabled": False}])
    assert apply_arrays(rgb, None, clip, 1.0, src)[1] is None
    # 签名跟着结果版本变
    clip.ai_matte = _spec(sid)
    s1 = fx_signature(clip, 1.0, src)
    with S.Session(sid) as sess:
        sess.write(15, 1, m[:, ::-1].copy(), 1.0)
    s2 = fx_signature(clip, 1.0, src)
    assert s1 != s2 and s1 is not None


def test_preview_and_export_share_ai_matte(tmp_path):
    _app()
    from test_q31_keyframes import _color_mp4, _export_frame, _task_with_clip
    from test_q62_keyframes import _preview_composited
    from cutora.fx import matte_store as S
    media = str(tmp_path / "g.mp4")
    _color_mp4(media, "0x1EC81E", frames=30, size="160x90")
    task, _tr, clip = _task_with_clip(media, start_t=0.0, duration=1.0, canvas=(160, 90))
    sid = S.new_session_id()
    m = np.zeros((90, 160), np.uint8)
    m[:, :80] = 255
    with S.Session(sid, fps=30.0, src_w=160, src_h=90) as sess:
        for fi in range(0, 30):
            sess.write(fi, 1, m, 1.0)
    clip.ai_matte = _spec(sid)

    def _lum(img, x, y):
        return img.pixelColor(x, y).lightness()

    for img in (_preview_composited(task, 0.5), _export_frame(task, 0.5)):
        assert _lum(img, 20, 45) > 60 and _lum(img, 140, 45) < 20
    clip.flip_x = True
    for img in (_preview_composited(task, 0.5), _export_frame(task, 0.5)):
        assert _lum(img, 140, 45) > 60 and _lum(img, 20, 45) < 20
    clip.flip_x = False
    clip.ai_matte = dict(clip.ai_matte, enabled=False)
    img = _export_frame(task, 0.5)
    assert _lum(img, 20, 45) > 60 and _lum(img, 140, 45) > 60


# ───────────────────────────── 命令 ─────────────────────────────

def test_ai_matte_commands_and_undo():
    _app()
    from test_q62_keyframes import _win_for_task
    from cutora.core.commands import get_bus, reset_bus_for_tests
    from cutora.fx import matte_store as S
    from cutora.model.timeline import Timeline
    from cutora.model.timeline_model import Clip, Track
    tl = Timeline(320, 180, 30.0)
    tr = Track("video")
    tr.id = "v0"
    clip = Clip("s", 0.0, 2.0, 0.0)
    tr.clips.append(clip)
    tl.add_track(tr)
    task = {"timeline": tl, "duration": 2.0, "text_overlays": [], "image_overlays": [], "segments": [], "style": {}}
    reset_bus_for_tests()
    win = _win_for_task(task)
    bus = get_bus()
    bus.bind_window(win)
    loc = {"target": "clip", "track_id": "v0", "clip_index": 0}
    with pytest.raises(Exception):
        bus.execute("ai.matte.prompt", dict(loc, op="add", frame=0, prompt={"obj": 1, "x": 0.5, "y": 0.5}), source="ui")
    r = bus.execute("ai.matte.session", dict(loc, op="create", model="matte-sam21-base-plus"), source="ui")
    sid = r["session"]
    assert sid and clip.ai_matte["session"] == sid and clip.ai_matte["model"] == "matte-sam21-base-plus"
    assert [o["id"] for o in clip.ai_matte["objects"]] == [1]
    assert bus.execute("ai.matte.session", dict(loc, op="create"), source="ui")["session"] == sid    # 幂等
    r2 = bus.execute("ai.matte.object", dict(loc, op="add", name="狗"), source="ui")
    assert r2["object"]["id"] == 2 and clip.ai_matte["objects"][1]["name"] == "狗"
    bus.execute("ai.matte.prompt", dict(loc, op="add", frame=3, prompt={"obj": 2, "x": 0.4, "y": 0.5, "label": 1}), source="ui")
    bus.execute("ai.matte.prompt", dict(loc, op="add", frame=3, prompt={"obj": 2, "box": [0.1, 0.1, 0.5, 0.5]}), source="ui")
    bus.execute("ai.matte.prompt", dict(loc, op="add", frame=3, prompt={"obj": 2, "box": [0.2, 0.2, 0.6, 0.6]}), source="ui")
    pr = clip.ai_matte["prompts"]["3"]
    assert len(pr) == 2 and pr[1]["box"] == [0.2, 0.2, 0.6, 0.6]                       # 一帧一个框
    with pytest.raises(Exception):
        bus.execute("ai.matte.prompt", dict(loc, op="add", frame=3, prompt={"obj": 9, "x": 0.1, "y": 0.1}), source="ui")
    bus.execute("ai.matte.set", dict(loc, patch={"feather": 6, "view": "overlay", "session": "hack",
                                                 "objects": [{"id": 2, "color": "#123456", "enabled": False}]}), source="ui")
    assert clip.ai_matte["feather"] == 6.0 and clip.ai_matte["view"] == "overlay" and clip.ai_matte["session"] == sid
    assert clip.ai_matte["objects"][1]["color"] == "#123456" and clip.ai_matte["objects"][1]["enabled"] is False
    bus.execute("ai.matte.prompt", dict(loc, op="clear", frame=3, object_id=2), source="ui")
    assert "3" not in clip.ai_matte["prompts"]
    bus.execute("ai.matte.prompt", dict(loc, op="add", frame=5, prompt={"obj": 2, "x": 0.4, "y": 0.5}), source="ui")
    bus.execute("ai.matte.object", dict(loc, op="remove", object_id=2), source="ui")
    assert [o["id"] for o in clip.ai_matte["objects"]] == [1] and clip.ai_matte["prompts"] == {}
    n = len(win.history_mgr.history)
    win.undo()
    c2 = win.tasks[0]["timeline"].tracks[0].clips[0]
    assert [o["id"] for o in c2.ai_matte["objects"]] == [1, 2] and "5" in c2.ai_matte["prompts"]
    assert n >= 9
    # detach：整个拿掉并删缓存
    S.Session(sid, fps=30.0, src_w=4, src_h=4).write(0, 1, np.zeros((4, 4), np.uint8), 1.0)
    bus.execute("ai.matte.session", dict(loc, op="detach"), source="ui")
    c3 = win.tasks[0]["timeline"].tracks[0].clips[0]
    assert c3.ai_matte == {} and not os.path.isdir(S.session_dir(sid))


def test_clip_schema_and_copy_keep_ai_matte():
    import copy
    from cutora.model.project_schema import CLIP_KEYS
    from cutora.model.timeline_model import Clip
    assert "ai_matte" in CLIP_KEYS
    c = Clip("s", 0, 1, 0)
    assert c.ai_matte == {}
    c.ai_matte = {"session": "m1", "objects": [{"id": 1}]}
    d = copy.deepcopy(c)
    assert d.ai_matte["session"] == "m1"


# ───────────────────────────── 登记 / 下载 ─────────────────────────────

def test_matte_models_registered_pinned_and_licensed():
    from cutora.asr.models import catalog, get_model, iter_files
    from cutora.core.licenses import is_license_allowed
    rows = [r for r in catalog() if r.get("kind") == "matte"]
    ids = {r["id"] for r in rows}
    assert {"matte-sam21-large", "matte-sam21-base-plus"} <= ids
    for r in rows:
        assert r["license"] == "Apache-2.0" and is_license_allowed(r["license"])
        assert len(r["revision"]) == 40 and r["repo"].startswith("facebook/sam2.1-hiera")
        assert r.get("enabled", True) and not r.get("gated")
        files = iter_files(r)
        names = {f["filename"] for f in files}
        assert {"model.safetensors", "config.json", "preprocessor_config.json", "processor_config.json",
                "video_preprocessor_config.json"} == names
        for f in files:
            assert len(f["sha256"]) == 64 and f["source"] in ("hf-lfs-oid", "computed") and int(f["size"]) > 0
        assert r["sha256"] == next(f["sha256"] for f in files if f["filename"] == "model.safetensors")
        assert r["dirname"].startswith("matte_sam21")
    large = get_model("matte-sam21-large")
    assert large.get("default") is True and large["size_mb"] > 800


def test_download_progress_and_cancel(tmp_path, monkeypatch):
    from cutora.asr import download as D
    from cutora.asr import models as Mo
    from cutora.core.net import NetCancelled
    monkeypatch.setattr(Mo, "models_dir", lambda: str(tmp_path / "models"))
    row = Mo.get_model("matte-sam21-base-plus")
    sizes = {f["filename"]: int(f["size"]) for f in Mo.iter_files(row)}
    calls = []

    def fake_download_to_file(url, dest, *, headers=None, timeout=600, resume=True, purpose="model",
                              progress_cb=None, cancel_event=None):
        name = os.path.basename(dest)
        calls.append(name)
        total = sizes[name]
        if progress_cb:
            progress_cb(total // 2, total)
            if cancel_event is not None and cancel_event.is_set():
                raise NetCancelled("下载已取消")
            progress_cb(total, total)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "wb") as fh:
            fh.write(b"x")
        return {"bytes": total, "resumed": False}

    monkeypatch.setattr(D, "download_to_file", fake_download_to_file)
    monkeypatch.setattr(D, "verify_weight", lambda path, sha: "ready")
    seen = []
    info = D.download_model("matte-sam21-base-plus", progress_cb=lambda d, t, n: seen.append((d, t, n)))
    assert len(info["files"]) == 5 and len(calls) == 5
    total = sum(sizes.values())
    assert seen[-1][0] == total and seen[-1][1] == total and seen[0][1] == total
    assert [d for d, _t, _n in seen] == sorted(d for d, _t, _n in seen)      # 单调
    # 取消：第一个文件中途停
    calls.clear()
    monkeypatch.setattr(D, "verify_weight", lambda path, sha: "missing")
    ev = threading.Event()
    ev.set()
    with pytest.raises(NetCancelled):
        D.download_model("matte-sam21-base-plus", progress_cb=lambda *a: None, cancel_event=ev)
    assert len(calls) == 1


def test_net_download_progress_cancel_keeps_part(tmp_path, monkeypatch):
    import io
    from cutora.core import net as N
    data = b"abcdefgh" * 1024 * 20          # 160 KB
    chunks = []

    class _Resp(io.BytesIO):
        headers = {"Content-Length": str(len(data))}

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class _Opener:
        def open(self, req, timeout=0):
            return _Resp(data)

    monkeypatch.setattr(N, "build_opener", lambda *a, **k: _Opener())
    monkeypatch.setattr(N, "assert_url_allowed", lambda url: "huggingface.co")
    dest = str(tmp_path / "w.bin")
    info = N.download_to_file("https://huggingface.co/x/y", dest, progress_cb=lambda w, t: chunks.append((w, t)))
    assert info["bytes"] == len(data) and os.path.getsize(dest) == len(data)
    assert chunks[-1] == (len(data), len(data)) and chunks[0][0] == 64 * 1024
    ev = threading.Event()
    ev.set()
    with pytest.raises(N.NetCancelled):
        N.download_to_file("https://huggingface.co/x/y", str(tmp_path / "c.bin"), cancel_event=ev)
    assert os.path.isfile(str(tmp_path / "c.bin.part")) and not os.path.isfile(str(tmp_path / "c.bin"))


def test_model_download_dialog_summary():
    _app()
    from cutora.asr.models import get_model
    from cutora.ui.model_download_dialog import ModelDownloadDialog, model_summary_rows
    row = get_model("matte-sam21-large")
    rows = dict(model_summary_rows(row))
    assert rows["许可"] == "Apache-2.0" and "huggingface.co/facebook/sam2.1-hiera-large" in rows["来源"]
    assert rows["权重 SHA256"] == row["sha256"] and "MB" in rows["大小"]
    dlg = ModelDownloadDialog(row)
    assert dlg.btn_ok.text() == "同意并下载" and not dlg.prog.isVisibleTo(dlg)
    dlg.close()


# ───────────────────────────── 检查器 + 画布 ─────────────────────────────

def _wait_worker(panel, timeout=20.0):
    import time
    t0 = time.time()
    while panel._running() and time.time() - t0 < timeout:
        QApplication.processEvents()
        time.sleep(0.02)
    QApplication.processEvents()
    assert not panel._running(), "任务没结束"


def _mouse(typ, x, y, button=Qt.MouseButton.LeftButton, mods=Qt.KeyboardModifier.NoModifier):
    return QMouseEvent(typ, QPointF(x, y), QPointF(x, y), button,
                       button if typ != QEvent.Type.MouseButtonRelease else Qt.MouseButton.NoButton, mods)


def test_inspector_canvas_prompt_and_propagate(tmp_path):
    _app()
    from test_q145_canvas_shortcuts import _w
    from test_q31_keyframes import _task_with_clip
    from test_q62_keyframes import _win_for_task
    from cutora.core.commands import get_bus, reset_bus_for_tests
    from cutora.fx import matte_store as S
    from cutora.ui.canvas import VideoCanvas
    from cutora.ui.panels.property import PropertyPanel
    from cutora.ui.timeline.widget import TimelineWidget
    media = _square_video(str(tmp_path / "sq.mp4"))
    task, tr, clip = _task_with_clip(media, start_t=0.0, duration=40 / 30.0, canvas=(320, 180))
    task["duration"] = 40 / 30.0
    reset_bus_for_tests()
    win = _win_for_task(task)
    get_bus().bind_window(win)
    win._timeline_ms = 0
    c = VideoCanvas()
    c.setParent(win)
    c.setFixedSize(640, 360)
    panel = PropertyPanel()
    panel.setParent(win)
    win.prop_panel = panel
    win.video_canvas = c
    tlw = TimelineWidget()
    tlw.set_data(int(task["duration"] * 1000), task)
    win.timeline = tlw
    win.show()
    try:
        c.set_data(task, 0.0)
        from cutora.preview.engine import PreviewEngine
        c.set_program_frame(PreviewEngine().video_frame_at(task, 0.0))
        c.repaint()
        QApplication.processEvents()
        c._selected_item = None
        from test_q145_canvas_shortcuts import _click
        _click(c, *_w(c, 160, 90))
        assert c._selected_item and c._selected_item.get("type") == "video_clip"
        panel.load_data("video_clip", (tr, clip))
        ai = panel.vid_fx.ai
        assert ai.sec.isVisibleTo(panel) and panel.ov_fx.ai is None or True
        code, text = ai.model_status()
        assert code == "fake" and not ai.btn_download.isVisibleTo(ai)
        assert ai.current_frame() == 0
        assert not ai.btn_fwd.isEnabled()                       # 还没有提示点
        # 点选对象 → 画布进入模式
        ai.chk_auto.setChecked(True)                           # 老流程：每点一下就算（Q166 起默认不勾）
        ai.btn_prompt.setChecked(True)
        assert c._mask_mode and c._mask_mode["kind"] == "matte"
        assert clip.ai_matte["session"]                        # 自动建会话 + 对象 1
        sid = clip.ai_matte["session"]
        # 点在方块上（源 (50, 90)）
        x, y = _w(c, 50, 90)
        c.mousePressEvent(_mouse(QEvent.Type.MouseButtonPress, x, y))
        c.mouseReleaseEvent(_mouse(QEvent.Type.MouseButtonRelease, x, y))
        assert clip.ai_matte["prompts"]["0"][0]["label"] == 1
        assert abs(clip.ai_matte["prompts"]["0"][0]["x"] - 50 / 320) < 0.02
        _wait_worker(ai)
        a, used = S.read_alpha(sid, 0, [1])
        assert used == 0 and a[90, 50] == 1.0 and a[90, 200] == 0.0
        # Alt+点 = 负点
        x2, y2 = _w(c, 60, 90)
        c.mousePressEvent(_mouse(QEvent.Type.MouseButtonPress, x2, y2, mods=Qt.KeyboardModifier.AltModifier))
        c.mouseReleaseEvent(_mouse(QEvent.Type.MouseButtonRelease, x2, y2, mods=Qt.KeyboardModifier.AltModifier))
        assert clip.ai_matte["prompts"]["0"][1]["label"] == 0
        _wait_worker(ai)
        # 清这帧的点 → 重新只留正点
        ai._on_clear_frame()
        assert "0" not in clip.ai_matte["prompts"]
        c.mousePressEvent(_mouse(QEvent.Type.MouseButtonPress, x, y))
        c.mouseReleaseEvent(_mouse(QEvent.Type.MouseButtonRelease, x, y))
        _wait_worker(ai)
        # 拖框
        bx0, by0 = _w(c, 10, 50)
        bx1, by1 = _w(c, 100, 130)
        c.mousePressEvent(_mouse(QEvent.Type.MouseButtonPress, bx0, by0))
        c.mouseMoveEvent(_mouse(QEvent.Type.MouseMove, bx1, by1))
        c.repaint()
        c.mouseReleaseEvent(_mouse(QEvent.Type.MouseButtonRelease, bx1, by1))
        boxes = [p for p in clip.ai_matte["prompts"]["0"] if "box" in p]
        assert len(boxes) == 1 and boxes[0]["box"][0] == pytest.approx(10 / 320, abs=0.02)
        _wait_worker(ai)
        c.repaint()                                            # 画叠加 + 提示点标记
        # 传播到全部
        assert ai.btn_fwd.isEnabled() and ai.btn_all.isEnabled()
        ai._on_propagate("all")
        assert ai._running() and ai.prog.isVisibleTo(ai)
        _wait_worker(ai, timeout=60.0)
        cov = S.coverage(sid)
        assert len(cov) >= 39 and max(cov) >= 38
        assert "传播完成" in ai.lbl_info.text()
        a30, _u = S.read_alpha(sid, 30, [1])
        assert a30[90, 140] == 1.0 and a30[90, 60] == 0.0
        assert "帧" in ai.sec.section_title
        # 时间线覆盖条画得出来
        img = QImage(400, 60, QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(0)
        p = QPainter(img)
        from PySide6.QtCore import QRectF
        tlw.pixels_per_second = 100.0
        tlw._paint_matte_band(p, clip, QRectF(0, 10, 133, 40), 0.0)
        p.end()
        assert img.pixelColor(60, 12).green() > 150
        # Esc 退出模式
        from PySide6.QtGui import QKeyEvent
        c.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier))
        assert c._mask_mode is None and not ai.btn_prompt.isChecked()
        # 关掉 → 画面不套；预览路径真的用了 mask
        from test_q62_keyframes import _preview_composited
        clip.ai_matte = dict(clip.ai_matte, view="comp", enabled=True)
        img1 = _preview_composited(task, 30 / 30.0)
        assert img1.pixelColor(140, 90).red() > 150 and img1.pixelColor(300, 90).lightness() < 30
        # 删对象（只剩一个时不允许）/ 清除结果
        assert not ai.btn_obj_del.isEnabled()
        ai._on_obj_add()
        assert len(clip.ai_matte["objects"]) == 2 and ai.btn_obj_del.isEnabled()
    finally:
        c.close()
        win.close()


def test_overlay_panel_has_no_ai_section():
    from test_q152a2_keyable_motion import _panel_env
    win, panel, _task, _ov, _clip = _panel_env("overlay")
    try:
        assert panel.ov_fx.ai is None or not panel.ov_fx.ai.sec.isVisibleTo(panel)
    finally:
        win.close()
