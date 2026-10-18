# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q181 · GPU 像素运算第一步：抠像 + 遮罩 + 预乘、调整图层效果链在 torch 上算。

云端没有显卡：这里把 device 定成 cpu 跑同一套 torch 代码，和 numpy / cv2 版逐像素比；服务进程 / 客户端
走真实的子进程 + 共享内存。CUDA 上的数值只会在舍入这一级上不同（自检会在 Luca 机器上再比一次）。"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("CUTORA_SKIP_ENCODER_PROBE", "1")
os.environ.setdefault("CUTORA_USER_DATA", tempfile.mkdtemp(prefix="cutora_q181_"))
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

torch = pytest.importorskip("torch")
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(["q181"])


def _arr4(q):
    w, h = q.width(), q.height()
    return np.frombuffer(q.constBits(), np.uint8).reshape(h, q.bytesPerLine())[:, :w * 4].reshape(h, w, 4).astype(int)


KEYER = {"enabled": True, "color": "#4C9452", "gain": 1.4, "balance": 0.5, "despill": 1.0, "despill_on": True,
         "despill_mode": "limit", "despill_scope": "edges", "preblur": 0.0, "black": 0.02, "white": 0.98,
         "gamma": 1.0, "erode": 0.0, "blur": 0.0, "despot": 0.0, "view": "comp"}
MASK = {"type": "ellipse", "rect": [-0.2, 0.1, 1.2, 1.2], "points": [], "rotation": -30.0, "radius": 0.0,
        "feather": 40.0, "expand": 0.0, "opacity": 1.0, "invert": False, "mode": "add", "enabled": True,
        "name": "", "id": "m1"}


def _green_frame(w=260, h=150, seed=2):
    rng = np.random.default_rng(seed)
    fr = rng.integers(0, 256, (h, w, 3), dtype=np.uint8)
    fr[:, : w // 2] = (82, 148, 76)                       # BGR 绿幕
    fr[40:90, 60:110] = (30, 200, 40)                     # 偏绿的前景 → 有溢色
    return fr


class _Host:
    def __init__(self, keyer=None, masks=None):
        self.keyer = keyer or {}
        self.masks = masks or []
        self.ai_matte = {}
        self.fx_graph = {}


def _cpu_layer(fr, host):
    from PySide6.QtGui import QImage
    from cutora.fx import layer_fx as LF
    h, w = fr.shape[:2]
    q = QImage(fr.data, w, h, fr.strides[0], QImage.Format.Format_BGR888).copy()
    return _arr4(LF.apply_layer_fx(q, host, px_scale=1.0, _force_cpu=True))


def test_key_layer_matches_cpu_pipeline():
    _app()
    from cutora.fx import gpu_ops as G, masks as M
    fr = _green_frame()
    h, w = fr.shape[:2]
    for name, keyer, masks in (("key", KEYER, []), ("key+mask", KEYER, [MASK]), ("mask", None, [MASK]),
                               ("axis/all", dict(KEYER, despill_mode="axis", despill_scope="all"), []),
                               ("preblur+gamma+blur", dict(KEYER, preblur=1.0, gamma=0.8, blur=3.0), [])):
        ref = _cpu_layer(fr, _Host(keyer or {"enabled": False}, masks))
        mask = M.combine(masks, w, h, 1.0) if masks else None
        got = G.key_layer(fr, keyer, mask, device="cpu").astype(int)
        d = np.abs(got - ref)
        assert d.max() <= 2 and d.mean() < 0.05, (name, d.max(), d.mean())
    # 4 通道输入（直通 alpha）也对
    bgra = np.dstack([fr, np.full((h, w), 200, np.uint8)])
    got = G.key_layer(bgra, KEYER, None, device="cpu")
    assert got.shape == (h, w, 4) and int(got[..., 3].max()) <= 200


def test_adjust_region_matches_cpu_apply_item():
    _app()
    from PySide6.QtGui import QImage
    from cutora.fx import gpu as GPU
    from cutora.render import adjust_layer as AL
    be = GPU.LocalBackend("cpu")
    rng = np.random.default_rng(3)
    h, w = 300, 240
    cases = {
        "solid+border": dict(solid=dict(on=True, color="#3E3EAC", opacity=0.32, radius=0.09, border_on=True,
                                        border_width=6, border_color="#FFFFFF"), effect=dict(on=False)),
        "lens+grade": dict(solid=dict(on=True, color="#3E3EAC", opacity=0.32),
                           effect=dict(on=True, kinds=["lens_blur", "grade"], amount_lens_blur=35, highlights=1.0,
                                       grade=dict(exposure=-2.0, saturation=2.0))),
        "blur": dict(solid=dict(on=False), effect=dict(on=True, kinds=["blur"], amount_blur=50)),
        "mosaic": dict(solid=dict(on=False), effect=dict(on=True, kinds=["mosaic"], amount_mosaic=40)),
        "grade-3d": dict(solid=dict(on=False), effect=dict(on=True, kinds=["grade"],
                                                            grade=dict(vibrance=0.6, exposure=0.5))),
        "chain+mix": dict(solid=dict(on=True, color="#FF0000", opacity=0.2),
                          effect=dict(on=True, kinds=["mosaic", "blur", "lens_blur", "grade"], amount_mosaic=20,
                                      amount_blur=30, amount_lens_blur=30, grade=dict(saturation=-0.5), mix=0.7)),
        "feather+xform": dict(feather=20, xform=dict(on=True, scale=1.2, rotation=5.0),
                              solid=dict(on=False), effect=dict(on=True, kinds=["blur"], amount_blur=30)),
    }
    for name, upd in cases.items():
        ov = AL.adjust_defaults(w, h, start=0, end=1)
        ov.update({"x": 30, "y": 90, "w": 180, "h": 120, "rotation": 12.0})
        for k, v in upd.items():
            if isinstance(v, dict):
                ov.setdefault(k, {}).update(v)
            else:
                ov[k] = v
        c = rng.integers(0, 256, (h, w, 4), np.uint8)
        c[..., 3] = 255
        c[100:130, 50:80] = (250, 250, 250, 255)
        a_img = QImage(c.data, w, h, w * 4, QImage.Format.Format_ARGB32_Premultiplied).copy()
        b_img = a_img.copy()
        base = _arr4(a_img)
        AL.apply_item(a_img, ov, _gpu=None)
        AL.apply_item(b_img, ov, _gpu=be)
        a, b = _arr4(a_img), _arr4(b_img)
        d = np.abs(a - b)
        assert d.max() <= 16 and d.mean() < 0.6, (name, d.max(), d.mean())
        assert (np.abs(a - base).sum(axis=2) > 0).mean() > 0.1, name          # 确实画了东西
        assert np.array_equal(a[:30], base[:30]) and np.array_equal(b[:30], base[:30]), name   # 范围外不动
    assert GPU.STATS["calls"] >= len(cases)


def test_backend_selftest_and_disable_fallback(monkeypatch):
    _app()
    from cutora.fx import gpu as GPU, layer_fx as LF
    from PySide6.QtGui import QImage
    GPU.reset()
    monkeypatch.setenv(GPU.ENV_DEVICE, "cpu")
    monkeypatch.delenv(GPU.ENV_ADDR, raising=False)
    monkeypatch.delenv("CUTORA_RENDER_CHILD", raising=False)
    monkeypatch.delenv(GPU.ENV_DISABLE, raising=False)
    monkeypatch.setattr(
        GPU, "enabled_by_settings",
        lambda: (os.environ.get(GPU.ENV_DISABLE) or "") != "0")
    be = GPU.backend()
    assert be is not None and be.kind == "local"
    fr = _green_frame()
    h, w = fr.shape[:2]
    q = QImage(fr.data, w, h, fr.strides[0], QImage.Format.Format_BGR888).copy()
    host = _Host(KEYER, [MASK])
    via_gpu = _arr4(LF.apply_layer_fx(q, host, px_scale=1.0))
    LF.clear_cache()
    via_cpu = _arr4(LF.apply_layer_fx(q, host, px_scale=1.0, _force_cpu=True))
    assert np.abs(via_gpu - via_cpu).max() <= 2
    # 出错一次 → 之后都走 CPU，画面照常
    LF.clear_cache()

    def boom(*a, **k):
        raise RuntimeError("boom")
    monkeypatch.setattr(be, "key_layer", boom)
    out = _arr4(LF.apply_layer_fx(q, host, px_scale=1.0))
    assert np.array_equal(out, via_cpu) and GPU.backend() is None and GPU.STATS["errors"] >= 1
    GPU.reset()
    monkeypatch.setenv(GPU.ENV_DISABLE, "0")
    assert GPU.backend() is None                       # 关掉了
    GPU.reset()


def test_render_child_without_server_stays_cpu(monkeypatch):
    from cutora.fx import gpu as GPU
    GPU.reset()
    monkeypatch.setenv("CUTORA_RENDER_CHILD", "1")
    monkeypatch.delenv(GPU.ENV_ADDR, raising=False)
    monkeypatch.delenv(GPU.ENV_DISABLE, raising=False)
    assert GPU.backend() is None
    GPU.reset()
    monkeypatch.delenv("CUTORA_RENDER_CHILD", raising=False)


def test_server_roundtrip_over_shared_memory(tmp_path, monkeypatch):
    """真起一个服务子进程（cpu 设备），客户端经共享内存送帧取结果；结果和本地算的一样。"""
    _app()
    from cutora.fx import gpu as GPU, gpu_ops as G, masks as M
    addr_file = str(tmp_path / "addr")
    env = dict(os.environ)
    env[GPU.ENV_DEVICE] = "cpu"
    env.pop(GPU.ENV_ADDR, None)
    env.pop("CUTORA_RENDER_CHILD", None)
    proc = subprocess.Popen([sys.executable, "-m", "cutora", "--gpu-fx-server", addr_file, str(os.getpid())],
                            cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        t0 = time.time()
        addr = ""
        while time.time() - t0 < 90 and proc.poll() is None:
            if os.path.isfile(addr_file):
                addr = open(addr_file, encoding="utf-8").read().strip()
                if addr:
                    break
            time.sleep(0.2)
        assert addr, (proc.poll(), proc.stdout.read()[-800:] if proc.poll() is not None else "")
        be = GPU.RemoteBackend(addr)
        be.connect()
        fr = _green_frame(320, 200)
        h, w = fr.shape[:2]
        mask = M.combine([MASK], w, h, 1.0)
        local = G.key_layer(fr, KEYER, mask, device="cpu")
        remote = be.key_layer(fr, KEYER, mask, bgr=True)
        assert np.array_equal(local, remote)
        # 调整层
        rng = np.random.default_rng(1)
        crop = rng.integers(0, 256, (160, 200, 4), np.uint8)
        shape = np.zeros((100, 140), np.uint8)
        shape[10:90, 10:130] = 255
        eff = {"kinds": ["lens_blur", "grade"], "amount_lens_blur": 30, "highlights": 0.5,
               "grade": {"exposure": -1.0, "saturation": 0.5}, "mix": 1.0}
        kw = dict(ox=30, oy=30, shape_u8=shape, mix=0.9, fill=(np.array([10, 20, 30, 255], np.uint8), 0.3),
                  border=(shape, np.array([255, 255, 255, 255], np.uint8), 0.5))
        r1 = G.adjust_region(crop, eff, 400.0, device="cpu", **kw)
        r2 = be.adjust_region(crop, eff, 400.0, **kw)
        assert np.array_equal(r1, r2)
        # 比共享内存还大的帧：自动换一块更大的
        big = _green_frame(1280, 720)
        r3 = be.key_layer(big, KEYER, None, bgr=True)
        assert r3.shape == (720, 1280, 4) and np.array_equal(r3, G.key_layer(big, KEYER, None, device="cpu"))
        # 自检也能过（客户端这边比 CPU）
        assert GPU._selftest(be)
        # Q182：多个线程同时压同一个服务（服务端每连接一条 stream，不再全局串行），结果逐字节一致
        import threading
        errors = []

        def _hammer(seed):
            try:
                r = np.random.default_rng(seed)
                for _ in range(12):
                    hh, ww = int(r.integers(64, 400)), int(r.integers(64, 400))
                    fr = r.integers(0, 256, (hh, ww, 3), np.uint8)
                    fr[..., 1] = 200
                    exp = G.key_layer(fr, KEYER, None, device="cpu")
                    got = be.key_layer(fr, KEYER, None, bgr=True)
                    if not np.array_equal(exp, got):
                        errors.append(("key", seed))
                        return
            except Exception as e:
                errors.append((repr(e), seed))

        ths = [threading.Thread(target=_hammer, args=(i,)) for i in range(4)]
        for t in ths:
            t.start()
        for t in ths:
            t.join(120)
        assert not errors, errors
        be.close()
    finally:
        try:
            proc.kill()
        except Exception:
            pass


@pytest.mark.parametrize("mode", ["fake", "fake-fail"])
def test_server_graph_pool_fake_mode(tmp_path, mode):
    """Q185：服务进程的 CUDA graph 池。cpu 设备上用 CUTORA_GPU_FX_GRAPHS=fake 走同一套
    「录制 → 核对 → 借实例 → 回放 → 归还」逻辑：结果逐字节等于直接算；两种尺寸各录一份；
    多线程同时压同一个 key 时实例数不超过 GRAPH_POOL_PER_KEY；日志里有录制和回放计数。"""
    _app()
    import threading
    from cutora.fx import gpu as GPU, gpu_ops as G
    addr_file = str(tmp_path / "addr")
    env = dict(os.environ)
    env[GPU.ENV_DEVICE] = "cpu"
    env[GPU.ENV_GRAPHS] = mode
    env.pop(GPU.ENV_ADDR, None)
    env.pop("CUTORA_RENDER_CHILD", None)
    log_path = tmp_path / "server.log"
    log_fh = open(log_path, "w", encoding="utf-8")
    proc = subprocess.Popen([sys.executable, "-m", "cutora", "--gpu-fx-server", addr_file, str(os.getpid())],
                            cwd=ROOT, env=env, stdout=log_fh, stderr=subprocess.STDOUT)
    try:
        t0 = time.time()
        addr = ""
        while time.time() - t0 < 90 and proc.poll() is None:
            if os.path.isfile(addr_file):
                addr = open(addr_file, encoding="utf-8").read().strip()
                if addr:
                    break
            time.sleep(0.2)
        assert addr, open(log_path, encoding="utf-8", errors="ignore").read()[-800:]
        errors = []

        def _hammer(seed, w, h):
            try:
                be = GPU.RemoteBackend(addr)
                be.connect()
                r = np.random.default_rng(seed)
                for _ in range(10):
                    fr = r.integers(0, 256, (h, w, 3), np.uint8)
                    fr[..., 1] = 200
                    exp = G.key_layer(fr, KEYER, None, device="cpu")
                    got = be.key_layer(fr, KEYER, None, bgr=True)
                    if not np.array_equal(exp, got):
                        errors.append(("mismatch", seed))
                        return
                be.close()
            except Exception as e:
                errors.append((repr(e), seed))

        ths = [threading.Thread(target=_hammer, args=(i, 200, 120)) for i in range(4)]
        ths.append(threading.Thread(target=_hammer, args=(9, 96, 64)))
        for t in ths:
            t.start()
        for t in ths:
            t.join(120)
        assert not errors, errors
    finally:
        try:
            proc.kill()
            proc.wait(timeout=10)
        except Exception:
            pass
        log_fh.close()
    log = open(log_path, encoding="utf-8", errors="ignore").read()
    assert "CUDA graph：启用" in log
    captured = [ln for ln in log.splitlines() if "CUDA graph 已录制并核对" in ln]
    assert captured, log[-1500:]
    # 200×120 的 key 最多 GRAPH_POOL_PER_KEY 份，96×64 至少一份
    assert sum("120×200" in ln for ln in captured) <= GPU.GRAPH_POOL_PER_KEY
    assert any("64×96" in ln for ln in captured)
    assert "回放" in log and "直接算" in log
    import re
    replays = max(int(m) for m in re.findall(r"graph 回放 (\d+)", log))
    if mode == "fake":
        assert replays >= 40, log[-1500:]          # 5 线程 × 10 次 = 50 次，除了录制那几次都走回放
    else:
        # Q185b：某份实例回放抛异常 → 那次直接算、该 key 以后直接算、实例锁归还（不卡死），客户端照样拿到正确结果
        assert "graph 回放出错" in log and "graph 出错" in log, log[-1500:]
        eager = max(int(m) for m in re.findall(r"直接算 (\d+)", log))
        assert eager >= 10, log[-1500:]


def test_start_server_launches_and_answers_ping(monkeypatch):
    """Q181 修：start_server() 本身要能起服务（之前它 import 了 parallel.py 里一个函数内的名字，
    Luca 机器上整场导出都静默走了 CPU）。用 cpu 设备真起一次，ping 通，再 kill。"""
    from cutora.fx import gpu as GPU
    monkeypatch.setenv(GPU.ENV_DEVICE, "cpu")
    monkeypatch.delenv(GPU.ENV_ADDR, raising=False)
    monkeypatch.delenv("CUTORA_RENDER_CHILD", raising=False)
    monkeypatch.setattr(GPU, "enabled_by_settings", lambda: True)
    proc, addr = GPU.start_server(timeout=90)
    try:
        assert proc is not None and addr, "start_server() 没起来服务进程"
        be = GPU.RemoteBackend(addr)
        be.connect()
        assert be.ping()
    finally:
        if proc is not None:
            proc.kill()
            proc.wait(timeout=10)


def test_main_dispatch_and_parallel_env():
    src = open(os.path.join(ROOT, "cutora", "__main__.py"), encoding="utf-8").read()
    assert "--gpu-fx-server" in src
    psrc = open(os.path.join(ROOT, "cutora", "render", "parallel.py"), encoding="utf-8").read()
    assert "start_server(workdir=self._tmpdir)" in psrc and "_stop_gpu_server" in psrc and "_GPU_ADDR_ENV" in psrc


def test_warm_up_async_resolves_backend_in_background(monkeypatch):
    _app()
    from cutora.fx import gpu as GPU
    GPU.reset()
    monkeypatch.setenv(GPU.ENV_DEVICE, "cpu")
    monkeypatch.delenv(GPU.ENV_ADDR, raising=False)
    monkeypatch.delenv("CUTORA_RENDER_CHILD", raising=False)
    monkeypatch.delenv(GPU.ENV_DISABLE, raising=False)
    monkeypatch.setattr(GPU, "enabled_by_settings", lambda: True)
    GPU.warm_up_async()
    th = GPU._WARM_THREAD
    assert th is not None
    th.join(60.0)
    assert not th.is_alive() and GPU._RESOLVED and GPU.backend() is not None
    src = open(os.path.join(ROOT, "cutora", "app.py"), encoding="utf-8").read()
    assert "warm_up_async" in src
    GPU.reset()
