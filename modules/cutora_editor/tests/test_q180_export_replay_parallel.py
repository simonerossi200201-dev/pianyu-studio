# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q180 · 复杂工程导出提速（Luca：41 个绿幕视频 24 fps 导出要 2 小时）。

* 整帧并行：解码线程只取帧，合成线程用同一套串行代码画整帧 —— 统一叠压（视频压字 / 调整图层 / 混合模式）
  不再被迫串行；抠像 / 遮罩 / 调整图层分摊到各线程。逐帧 md5 等于串行。
* 24 fps 素材放在 30 fps 时间线上的重复源帧：调色 / 抠像 / 遮罩只算一次。
* 抠像溢色抑制、图层 fx 的格式转换走更快的写法，结果逐位不变。
* 分屏的离屏图每线程一份（多线程画分屏不串）。
* 并行进程数按这批里最多的画布尺寸估内存（以前看第 1 个视频）。"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import threading

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("CUTORA_SKIP_ENCODER_PROBE", "1")
os.environ.setdefault("CUTORA_USER_DATA", tempfile.mkdtemp(prefix="cutora_q180_"))
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

W, H = 216, 384
_CHILD_ENV = "CUTORA_Q180_CHILD"
_REPO = os.path.join(os.path.dirname(__file__), "..")


def _run_in_fresh_interpreter():
    """Full-file isolate leaves GPU/encoder dirt; this export compare needs a clean process."""
    if os.environ.get(_CHILD_ENV) == "1":
        return False
    node = (os.environ.get("PYTEST_CURRENT_TEST") or "").split(" ")[0]
    if not node:
        return False
    env = dict(os.environ)
    env[_CHILD_ENV] = "1"
    env["CUTORA_PYTEST_ISOLATED"] = "1"
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", node, "-q", "--tb=short",
         "-p", "no:cacheprovider"],
        cwd=os.path.abspath(_REPO), env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=120,
    )
    assert proc.returncode == 0, (proc.stdout or "") + "\n" + (proc.stderr or "")
    return True


def _app():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(["q180"])
    from cutora.ui.panels.fonts import load_embedded_fonts
    load_embedded_fonts()
    return app


def _ff():
    from cutora.core.config import FFMPEG_PATH
    ff = FFMPEG_PATH if os.path.isfile(FFMPEG_PATH) else shutil.which("ffmpeg")
    if not ff:
        pytest.skip("no ffmpeg")
    return ff


def _media(tmp_path, name, src, size, rate, dur=3):
    out = str(tmp_path / name)
    if not os.path.isfile(out):
        subprocess.run([_ff(), "-v", "error", "-y", "-f", "lavfi", "-i", "%s=s=%s:r=%d:d=%d" % (src, size, rate, dur),
                        "-pix_fmt", "yuv420p", out], check=True)
    return out


def _green(tmp_path):
    """24 fps 绿幕：绿底 + 中间一块彩条（抠完留下彩条）。"""
    out = str(tmp_path / "green24.mp4")
    if not os.path.isfile(out):
        subprocess.run([_ff(), "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=0x4C9452:s=320x176:r=24:d=3",
                        "-f", "lavfi", "-i", "testsrc2=s=120x120:r=24:d=3",
                        "-filter_complex", "[0][1]overlay=x=100:y=28", "-pix_fmt", "yuv420p", out], check=True)
    return out


def _stack_task(tmp_path):
    """Luca 的工程结构：主视频（30 fps、放大铺满）→ 调整轨（镜头模糊 + 调色 + 半透明色块）→
    圣光（24 fps、滤色、椭圆羽化遮罩）→ 绿幕人物（24 fps、抠像 + 去溢色、缩小）→ 文字。"""
    from test_q163_unified_stack import _task as base
    from cutora.model.timeline_model import Clip, MediaSource, Track
    from cutora.render import adjust_layer as AL
    task = base(tmp_path)
    tl = task["timeline"]
    v1, txt = tl.tracks
    main = _media(tmp_path, "main30.mp4", "testsrc2", "324x576", 30)
    tl.sources["m"] = MediaSource(main, duration=3.0, probe=False)
    v1.clips[0].scale = 1.1
    v1.clips[0].fit_mode = "fill"
    adj = Track("adjust")
    glow = Track("video")
    person = Track("video")
    tl.tracks[:] = [v1, adj, glow, person, txt]
    tl.sources["g"] = MediaSource(_media(tmp_path, "glow24.mp4", "testsrc", "216x384", 24), duration=3.0, probe=False)
    c = Clip("g", 0.0, 3.0, 0.0)
    c.blend_mode = "screen"
    c.loop = True
    c.masks = [{"type": "ellipse", "rect": [-0.2, 0.1, 1.2, 1.2], "rotation": -30.0, "feather": 60.0,
                "opacity": 1.0, "mode": "add", "enabled": True}]
    glow.clips.append(c)
    tl.sources["p"] = MediaSource(_green(tmp_path), duration=3.0, probe=False)
    p = Clip("p", 0.0, 3.0, 0.0)
    p.fit_mode = "fill"
    p.scale = 0.4
    p.x, p.y = 30.0, 120.0
    p.keyer = {"enabled": True, "color": "#4C9452", "gain": 1.4, "despill": 1.0, "despill_on": True,
               "despill_mode": "limit", "despill_scope": "edges", "black": 0.02, "white": 0.98}
    person.clips.append(p)
    ov = AL.adjust_defaults(W, H, start=0.0, end=3.0, track_id=adj.id)
    ov.update({"x": 0, "y": 250, "w": 216, "h": 100})
    ov["solid"].update({"color": "#3E3EAC", "opacity": 0.32, "radius": 0.09})
    ov["effect"].update({"on": True, "kinds": ["lens_blur", "grade"], "amount_lens_blur": 35, "highlights": 1.0,
                         "grade": {"exposure": -1.0, "saturation": 1.0}})
    task["image_overlays"].append(ov)
    return task


def _ctx(task, rw):
    from cutora.model.track_guard import video_tracks_for_render
    from cutora.render.compose_parallel import ExportComposeCtx
    from cutora.render.export_renderer import ExportRenderer
    from cutora.render.frame_renderer import compute_export_render_order
    from cutora.render.render_worker import _ExportCapPool
    from cutora.render.visual_stack import build_stack
    tl = task["timeline"]
    paths = {sid: src.path for sid, src in tl.sources.items()}
    states = {}
    caps = _ExportCapPool(paths, {sid: float("inf") for sid in paths}, states)
    rw.project = task
    return ExportComposeCtx(project=task, width=tl.width, height=tl.height, video_tracks=video_tracks_for_render(task),
                            video_caps=caps, frame_states=states, fallback_cap=None, overlay_path=None,
                            fallback_clip=None, render_order=compute_export_render_order(task),
                            overlay_index=ExportRenderer.build_overlay_index(task), task_glass=False,
                            stack=build_stack(task)), caps


def _serial_md5s(task, n, start=0):
    from cutora.render.render_worker import RenderWorker
    rw = RenderWorker(task, "/tmp/x.mp4", "quality", False, export_logs=False)
    ctx, caps = _ctx(task, rw)
    out = []
    canvas = None
    try:
        for f in range(start, start + n):
            canvas = rw._compose_export_frame(f / 30.0, canvas, ctx)
            out.append(hashlib.md5(bytes(canvas.constBits())).hexdigest())
    finally:
        caps.clear()
    return out


def _replay_md5s(task, n, workers, start=0):
    from cutora.render.compose_parallel import ExportComposeCtx, LAST_RUN, run_compose_pool
    from cutora.render.render_worker import RenderWorker, _ReplayCaps
    rw = RenderWorker(task, "/tmp/x.mp4", "quality", False, export_logs=False)
    ctx, caps = _ctx(task, rw)
    got = []

    class Pipe:
        def put(self, data):
            got.append(hashlib.md5(bytes(data)).hexdigest())
            return True

    def wctx():
        iso = dict(task)
        for k in ("segments", "text_overlays", "image_overlays"):
            iso[k] = [dict(x) if isinstance(x, dict) else x for x in (iso.get(k) or [])]
        kw = {s: getattr(ctx, s) for s in ExportComposeCtx.__slots__}
        kw.update(project=iso, video_caps=_ReplayCaps(caps._paths), frame_states={})
        return ExportComposeCtx(**kw)
    tl = task["timeline"]
    try:
        run_compose_pool(render_worker=rw, decode_ctx=ctx, overlay_ctx_factory=wctx, frame_start=start,
                         frame_end=start + n, fps=30.0, width=tl.width, height=tl.height, n_workers=workers,
                         pipe_writer=Pipe(), is_running=lambda: True, stop_event=threading.Event(),
                         mad_want=set(), mad_refs=[], on_progress=None, total_frames=start + n, budget_gb=0,
                         replay=True)
    finally:
        caps.clear()
    assert LAST_RUN["workers"] == workers and sum(LAST_RUN["frames_by_worker"].values()) == n
    return got


# ───────────────────────────── 整帧并行 = 串行 ─────────────────────────────

def test_stacked_task_parallel_equals_serial(tmp_path):
    _app()
    from cutora.render.visual_stack import build_stack, needs_stack
    task = _stack_task(tmp_path)
    st = build_stack(task)
    assert needs_stack(task, stack=st) and [k for k, _ in st].count("video") == 2       # 两段视频带：以前只能串行
    ser = _serial_md5s(_stack_task(tmp_path), 36)
    assert len(set(ser)) > 20
    for workers in (2, 3):
        assert _replay_md5s(_stack_task(tmp_path), 36, workers) == ser, workers


def test_split_screen_parallel_equals_serial(tmp_path):
    _app()
    from cutora.model.timeline_model import Clip, MediaSource, Track
    from cutora.render.split_layout import apply_layout
    from test_q163_unified_stack import _task as base

    def make():
        task = base(tmp_path)
        tl = task["timeline"]
        v1, txt = tl.tracks
        v1.split_cell = "A"
        tl.sources["m"] = MediaSource(_media(tmp_path, "main30.mp4", "testsrc2", "324x576", 30),
                                      duration=3.0, probe=False)
        b = Track("video")
        b.split_cell = "B"
        tl.sources["b"] = MediaSource(_media(tmp_path, "glow24.mp4", "testsrc", "216x384", 24),
                                      duration=3.0, probe=False)
        b.clips.append(Clip("b", 0.0, 3.0, 0.0))
        tl.tracks[:] = [v1, b, txt]
        task["split"] = apply_layout({"enabled": True, "layout": "top_bottom"})
        return task
    ser = _serial_md5s(make(), 24)
    assert _replay_md5s(make(), 24, 3) == ser


def test_full_export_workers_match_and_run_parallel(tmp_path):
    """真导出（编码器 + 收尾）：统一叠压的工程 compose_workers=1 和 3 逐帧 md5 一样，3 的时候真的并行了。"""
    if _run_in_fresh_interpreter():
        return
    _app()
    ff = _ff()
    from cutora.render.compose_parallel import LAST_RUN
    from cutora.render.render_worker import RenderWorker

    def md5s(path):
        r = subprocess.run([ff, "-v", "error", "-i", path, "-f", "framemd5", "-"], capture_output=True, text=True)
        return [ln.split(",")[-1].strip() for ln in r.stdout.splitlines() if not ln.startswith("#")]
    out = {}
    for workers in (1, 3):
        task = _stack_task(tmp_path)
        dest = str(tmp_path / ("o%d.mp4" % workers))
        err = []
        LAST_RUN["workers"] = 0
        rw = RenderWorker(task, dest, "quality", False, export_logs=False, compose_workers=workers,
                          frame_range=(0, 45), video_only=True)
        rw.error.connect(lambda m: err.append(m))
        rw.render_wysiwyg_export(task)
        assert not err, err
        out[workers] = md5s(dest)
        if workers == 3:
            assert LAST_RUN.get("workers") == 3                 # 统一叠压不再退回串行
    assert len(out[1]) == 45 and out[1] == out[3]


# ───────────────────────────── 重复源帧只算一次 ─────────────────────────────

def test_repeated_source_frames_reuse_keyed_result(tmp_path, monkeypatch):
    _app()
    from cutora.fx import keyer as K
    calls = []
    real = K.apply

    def counting(rgb, k):
        calls.append(rgb.shape)
        return real(rgb, k)
    monkeypatch.setattr(K, "apply", counting)
    monkeypatch.setattr(
        "cutora.fx.layer_fx._gpu_eligible", lambda *a, **k: False)
    _serial_md5s(_stack_task(tmp_path), 30)
    # 30 帧时间线 = 24 张不同的源帧（每 5 帧有 1 帧重复）
    assert 23 <= len(calls) <= 25, len(calls)


def test_memo_waiters_share_one_compute():
    from cutora.render.render_worker import RenderWorker
    rw = RenderWorker({}, "/tmp/x.mp4", "quality", False, export_logs=False)
    n = [0]
    gate = threading.Event()

    def slow():
        n[0] += 1
        gate.wait(2.0)
        return "v"
    res = []
    ths = [threading.Thread(target=lambda: res.append(rw._fx_memo_get(("k",), slow))) for _ in range(4)]
    for t in ths:
        t.start()
    gate.set()
    for t in ths:
        t.join(5.0)
    assert res == ["v"] * 4 and n[0] == 1


# ───────────────────────────── 逐位不变的快写法 ─────────────────────────────

def _old_despill_limit(rgb, alpha, k):
    """Q180 之前「标准 + 只处理边缘」的写法（对照用）。"""
    import cv2
    from cutora.fx.keyer import _hex_rgb
    kr, kg, kb = _hex_rgb(k["color"])
    ds = float(k["despill"])
    dom = int(np.argmax((kr, kg, kb)))
    others = [c for c in range(3) if c != dom]
    limit = cv2.addWeighted(rgb[..., others[0]], 0.5, rgb[..., others[1]], 0.5, 0.0)
    excess = cv2.max(rgb[..., dom] - limit, 0.0) * ds
    spill_rgb = np.zeros_like(rgb)
    spill_rgb[..., dom] = excess
    if k.get("despill_scope", "edges") != "all":
        inv = 1.0 - alpha
        sig = max(1.5, 0.004 * max(alpha.shape[:2]))
        ks = int(sig * 3) * 2 + 1
        near = cv2.GaussianBlur(inv, (ks, ks), sig)
        wgt = np.clip(np.maximum(inv, near * 3.0), 0.0, 1.0).astype(np.float32)
        spill_rgb = spill_rgb * wgt[..., None]
    out = rgb - spill_rgb
    np.clip(out, 0.0, 1.0, out=out)
    return out


def test_keyer_limit_despill_is_bit_identical():
    from cutora.fx import keyer as K
    rng = np.random.default_rng(5)
    for shape in ((97, 131, 3), (360, 640, 3)):
        img = rng.integers(0, 256, shape, dtype=np.uint8)
        img[: shape[0] // 2, :, 1] = 200                           # 一半偏绿：有溢色、有抠掉的
        rgb = np.multiply(img, np.float32(1.0 / 255.0), dtype=np.float32)
        for color in ("#4C9452", "#1E3CDC"):
            for scope in ("edges", "all"):
                for ds in (0.4, 1.0):
                    k = K.normalize({"enabled": True, "color": color, "gain": 1.4, "despill": ds,
                                     "despill_on": True, "despill_mode": "limit", "despill_scope": scope,
                                     "black": 0.02, "white": 0.98})
                    alpha, out = K.matte_and_despill(rgb, k)
                    assert np.array_equal(out, _old_despill_limit(rgb, alpha, k)), (color, scope, ds)


def _old_to_qimage(rgb, alpha):
    import cv2
    from PySide6.QtGui import QImage
    h, w = rgb.shape[:2]
    if alpha is None:
        a = np.full((h, w), 255, dtype=np.uint8)
        rgbp = rgb
    else:
        a = cv2.convertScaleAbs(np.ascontiguousarray(alpha, dtype=np.float32), alpha=255.0)
        af = cv2.merge([a, a, a]).astype(np.float32) * np.float32(1.0 / 255.0)
        rgbp = cv2.convertScaleAbs(cv2.multiply(rgb.astype(np.float32), af))
    out = np.ascontiguousarray(cv2.merge([np.ascontiguousarray(rgbp[..., 2]), np.ascontiguousarray(rgbp[..., 1]),
                                          np.ascontiguousarray(rgbp[..., 0]), a]))
    return QImage(out.data, w, h, w * 4, QImage.Format.Format_ARGB32_Premultiplied).copy()


def test_layer_fx_conversions_are_bit_identical():
    _app()
    from PySide6.QtGui import QImage
    from cutora.fx import layer_fx as LF
    # 预乘：所有 (值, alpha) 组合
    v = np.tile(np.arange(256, dtype=np.uint8), (256, 1))
    a = np.repeat(np.arange(256, dtype=np.float32)[:, None], 256, axis=1) / np.float32(255.0)
    rgb = np.ascontiguousarray(np.dstack([v, v[:, ::-1], v]))
    assert LF._to_qimage(rgb, a) == _old_to_qimage(rgb, a)
    rng = np.random.default_rng(9)
    for (w, h) in ((131, 77), (64, 64)):
        rgb = rng.integers(0, 256, (h, w, 3), dtype=np.uint8)
        al = rng.random((h, w), dtype=np.float32)
        assert LF._to_qimage(rgb, al) == _old_to_qimage(rgb, al)
        assert LF._to_qimage(rgb, None) == _old_to_qimage(rgb, None)
        bgr = rng.integers(0, 256, (h, w, 3), dtype=np.uint8)
        q3 = QImage(bgr.data, w, h, bgr.strides[0], QImage.Format.Format_BGR888).copy()
        r3, a3 = LF._as_arrays(q3)
        assert np.array_equal(r3, bgr[..., ::-1]) and a3 is None
        bgra = rng.integers(0, 256, (h, w, 4), dtype=np.uint8)
        q4 = QImage(bgra.data, w, h, w * 4, QImage.Format.Format_ARGB32).copy()
        r4, a4 = LF._as_arrays(q4)
        assert np.array_equal(r4, bgra[..., 2::-1]) and np.array_equal(a4, bgra[..., 3].astype(np.float32) / 255.0)


# ───────────────────────────── 并行进程数 ─────────────────────────────

def test_dominant_canvas_and_plan():
    from types import SimpleNamespace
    from cutora.render import export_guard as G
    tasks = [{"timeline": SimpleNamespace(width=1080, height=1920)}] + \
        [{"timeline": SimpleNamespace(width=720, height=1280)} for _ in range(40)]
    assert G.dominant_canvas(tasks) == (720, 1280)
    assert G.dominant_canvas([{"timeline": {"width": 1080, "height": 1920}}]) == (1080, 1920)
    assert G.dominant_canvas([]) is None
    # Luca 那台：可用 18.4 GB、32 线程、每进程实测 ×1.25 ≈ 1.7 GB → 不再只派 3 个
    pl = G.plan_parallel_export(41, 8, False, cpu_count=32, avail_ram=18.4, mem_per_worker=1.71)
    assert pl["workers"] >= 5, pl
    pl3 = G.plan_parallel_export(41, 8, False, cpu_count=32, avail_ram=18.4, mem_per_worker=3.7)
    assert pl3["workers"] == 3, pl3          # 估成 3.7 GB 时（老算法）只派 3 个 —— Luca 第一次就是这样


def test_legacy_rss_samples_discounted_and_gen2_preferred(tmp_path, monkeypatch):
    """老版本记下的样本（在途缓冲大的时候）按收掉的量折算；新样本够数后只用新样本。"""
    import json
    import time
    from cutora.render import export_guard as G
    path = str(tmp_path / "worker_rss.json")
    px = 720 * 1280
    legacy = [{"peak_gb": 1.85, "canvas_px": px, "at": time.time()} for _ in range(20)]
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"samples": legacy}, fh)
    monkeypatch.setattr(G, "_rss_log_path", lambda: path)
    p90, _px, exact = G.measured_rss_for_canvas(px)
    assert exact and abs(p90 - (1.85 - G.LEGACY_INFLIGHT_DISCOUNT_GB)) < 1e-6
    for _ in range(6):
        G.record_worker_rss(1.3, (720, 1280))
    p90, _px, exact = G.measured_rss_for_canvas(px)
    assert exact and abs(p90 - 1.3) < 1e-6


def test_parallel_jobs_cap_compose_threads(tmp_path, monkeypatch):
    """批量并行：每个进程最多 4 个合成线程（画帧被 GIL 卡住，线程多了只是排队），省下的内存多开进程。"""
    from cutora.render.compose_parallel import PARALLEL_JOB_COMPOSE_MAX
    assert PARALLEL_JOB_COMPOSE_MAX == 4


def test_parallel_done_count_is_finished_files_not_progress_sum(tmp_path):
    """Luca：小窗写「已完成 3 / 36」，文件夹里一个成品都没有 —— 以前「已完成」= 各进程进度之和取整。"""
    import json
    import threading as _th
    from cutora.render import parallel as P
    mgr = P.ParallelExportManager.__new__(P.ParallelExportManager)
    mgr._lock = _th.Lock()
    mgr._snapshot = {"acc": 0.0, "done": 0, "all_done": False, "fps": 0.0}
    mgr._finished_infos = []
    mgr._done_items = 0
    mgr._launch_done = True
    mgr._degrade_reason = ""
    mgr._hb = None
    mgr._failed = []
    running = {}

    class _Proc:
        pid = 1

        def poll(self):
            return None
    for wid, cur in enumerate((89, 72, 65, 89)):
        prog = tmp_path / ("w%d.progress" % wid)
        prog.write_text(json.dumps({"done": 0, "cur": cur, "total": 9}), encoding="utf-8")
        running[wid] = {"proc": _Proc(), "progress_path": str(prog), "indices": list(range(9)),
                        "partial": 0.0}
    mgr._running = running
    mgr._sample()
    assert mgr._snapshot["done"] == 0 and abs(mgr._snapshot["acc"] - 3.15) < 1e-6
    (tmp_path / "w0.progress").write_text(json.dumps({"done": 1, "cur": 10, "total": 9}), encoding="utf-8")
    mgr._sample()
    assert mgr._snapshot["done"] == 1


def test_affine_grade_fast_path_matches_formula():
    """Q180：曝光 / 对比 / 色温 / 色调 / 饱和 / 提升 / 增益在线性空间里是仿射 → 直接算；和逐像素公式差 ≤ 1 级
    （老的 33³ 三线性查表在暗部 / 高饱和处差到 18 级）。gamma / 自然饱和 / 曲线 / HSL / LUT 文件照旧走表。"""
    from cutora.render import grade as G
    rng = np.random.default_rng(5)
    img = rng.integers(0, 256, (120, 160, 3), dtype=np.uint8)
    for g in ({"exposure": -2.0, "saturation": 2.0}, {"contrast": 0.5, "temperature": 800, "tint": 20},
              {"lift": [0.05, 0, 0, 0.02], "gain": [0, 0.1, 0, 0.1], "saturation": 0.3}):
        gs = [G.normalize_grade(g)]
        aff = G.affine_of_stack(gs)
        assert aff is not None, g
        fast = G.apply_affine_u8(img, aff)
        truth = np.clip(np.rint(G.apply_grade_rgb(img.astype(np.float32) / 255.0, gs[0]) * 255.0), 0, 255)
        assert np.abs(fast.astype(int) - truth.astype(int)).max() <= 1, g
        bgr = G.apply_affine_u8(np.ascontiguousarray(img[..., ::-1]), aff, bgr=True)
        assert np.abs(bgr[..., ::-1].astype(int) - fast.astype(int)).max() <= 1    # 通道顺序不同，浮点舍入差 ≤ 1
    for g in ({"gamma": [0.2, 0, 0, 0]}, {"vibrance": 0.5}, {"hsl": {"red": {"s": 0.3}}}):
        assert G.affine_of_stack([G.normalize_grade(g)]) is None, g
    assert G.affine_of_stack([G.normalize_grade({"exposure": 1}), G.normalize_grade({"saturation": 1})]) is None


def test_keyer_channel_patch_is_bit_identical_and_decoder_threads():
    from cutora.fx import keyer as K
    from cutora.render import readers, render_worker as RW
    rng = np.random.default_rng(6)
    img = rng.integers(0, 256, (90, 130, 3), dtype=np.uint8)
    img[:45, :, 1] = 200
    k = K.normalize({"enabled": True, "color": "#4C9452", "gain": 1.4, "despill": 1.0, "despill_on": True,
                     "despill_mode": "limit", "despill_scope": "edges", "black": 0.02, "white": 0.98})
    rgb = np.multiply(img, np.float32(1.0 / 255.0), dtype=np.float32)
    a, patch = K.matte_and_despill(rgb, k, channel_patch=True)
    assert isinstance(patch, K.ChannelPatch)
    a2, full = K.matte_and_despill(rgb, k)
    assert np.array_equal(a, a2) and np.array_equal(patch.full(), full)
    out8, a8 = K.apply(img, k)
    import cv2
    assert np.array_equal(out8, cv2.convertScaleAbs(full, alpha=255.0))
    # 解码线程：导出里每个解码器按环境变量 / 默认 4 个（以前 FFmpeg 自己定 = 每个源 16 个）
    assert RW.export_decode_threads() == RW.EXPORT_DECODE_THREADS_DEFAULT
    import inspect
    assert "threads" in inspect.signature(readers._open_video_capture).parameters


def test_partition_balances_by_estimated_work_lpt():
    """Q184：任务按「时长 × 画布像素」从大到小放进最轻的 worker，不再轮询。
    Luca 的 6 个视频 / 4 个进程（时长 128/197/168/152/122/204 s）：轮询会让 worker0 拿 128+122、
    worker1 拿 197+204=401 s；LPT 后最重的 worker 是 168+122=290 s。"""
    from cutora.render import parallel as P
    durs = [128, 197, 168, 152, 122, 204]

    def task(d):
        return {"timeline": {"width": 720, "height": 1280,
                             "tracks": [{"clips": [{"start_t": 0.0, "in_t": 0.0, "out_t": float(d)}]}]}}
    jobs = [(task(d), f"o{i}.mp4") for i, d in enumerate(durs)]
    mgr = P.ParallelExportManager.__new__(P.ParallelExportManager)
    mgr._jobs = jobs
    mgr._workers = 4
    chunks = mgr._partition()
    assert sorted(i for c in chunks for i in c) == list(range(6)) and len(chunks) == 4
    loads = [sum(durs[i] for i in c) for c in chunks]
    assert max(loads) == 290, (chunks, loads)
    # 每个 worker 内部保持原顺序
    assert all(c == sorted(c) for c in chunks)
    # 估不出工作量的任务按平均值算，不会把它们全堆到一个 worker
    mgr._jobs = [({}, "a"), ({}, "b"), ({}, "c"), ({}, "d")]
    mgr._workers = 2
    assert [len(c) for c in mgr._partition()] == [2, 2]
    assert P.job_weight(task(10)) == 10 * 720 * 1280 / 1e6
    assert P.job_weight({}) == 0.0
