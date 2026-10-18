# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q181 · GPU 像素运算的入口：后端选择、进程内后端、跨进程客户端 / 服务端、自检、回退。

为什么要一个服务进程：批量导出是 N 个渲染子进程，每个进程各自 import torch + 建 CUDA 上下文要多吃
0.5–1 GB 内存和几百 MB 显存，进程数就得砍一半，白干。所以显卡那一侧只有**一个**进程（服务端），
渲染子进程通过共享内存把帧递过去、取结果回来（一帧 4 MB 的拷贝不到 1 毫秒）。主界面进程本来就
装着 torch（AI 字幕 / 抠像用），串行导出和预览直接在进程内算。

用法（渲染代码只认这两个函数）：
    be = backend()                       # None = 没有 GPU / 关了 / 自检没过 / 出过错 → 走 CPU
    be.key_layer(frame_u8, keyer, mask_f32, bgr=True) -> bgra_premul_u8
    be.adjust_region(crop_bgra_u8, eff, long_side, ox=, oy=, shape_u8=, mix=, fill=, border=) -> bgra_u8
任何一次调用抛异常：记一次日志、这个进程以后都走 CPU（画面只会在舍入这一级上有差别）。

开关：设置 gpu_fx（默认开）；环境变量 CUTORA_GPU_FX=0 强制关；CUTORA_GPU_FX_ADDR 是父进程给
渲染子进程的服务地址（host:port:authkey）。导出始终软件解码 —— 这里只碰像素运算，不碰解码。
"""
from __future__ import annotations

import os
import threading
import time

import numpy as np

from cutora.core.log import get_logger

_LOG = get_logger("cutora.fx.gpu")
ENV_DISABLE = "CUTORA_GPU_FX"
ENV_GRAPHS = "CUTORA_GPU_FX_GRAPHS"      # =0 关掉服务进程里的 CUDA graph（排查用）
ENV_LOCAL = "CUTORA_GPU_FX_LOCAL"        # =1 主界面进程内直接用 torch（老行为，排查用）
GRAPH_POOL_PER_KEY = 2                  # 每种（算子 × 参数 × 尺寸）最多几份静态缓冲 / graph 实例
GRAPH_MIN_FREE_VRAM_GB = 1.0            # 显存空闲低于这个数就不再录新实例，借用已有的
REMOTE_CALL_TIMEOUT = 30.0              # 渲染进程等服务回话的上限（秒）；超时 = 本进程退回 CPU，导出不会停摆
ENV_ADDR = "CUTORA_GPU_FX_ADDR"
SETTING_KEY = "gpu_fx"
SELFTEST_MAX_DIFF = 24          # 自检：和 CPU 版的最大像素差（舍入 + 形态学方核的差别）
SELFTEST_MEAN_DIFF = 1.5

_LOCK = threading.Lock()
_BACKEND = None
_RESOLVED = False
STATS = {"calls": 0, "ms": 0.0, "bytes": 0, "errors": 0, "kind": ""}


def enabled_by_settings() -> bool:
    if (os.environ.get(ENV_DISABLE) or "") == "0":
        return False
    try:
        from cutora.core.history import _load_app_settings
        return bool((_load_app_settings() or {}).get(SETTING_KEY, True))
    except Exception:
        return True


_PENDING = object()                 # _resolve 的返回值：服务进程还在起，这次先用 CPU、下次再问


def backend():
    """这个进程该用的 GPU 后端（缓存）；None = 用 CPU。线程安全。
    主界面里服务进程还没起好时返回 None 但不缓存 —— 预览先用 CPU，服务一就绪就切过去。"""
    global _BACKEND, _RESOLVED
    if _RESOLVED:
        return _BACKEND
    with _LOCK:
        if _RESOLVED:
            return _BACKEND
        be = None
        try:
            be = _resolve()
        except Exception:
            _LOG.exception("GPU 像素运算后端初始化失败，用 CPU")
            be = None
        if be is _PENDING:
            return None
        _BACKEND = be
        _RESOLVED = True
        STATS["kind"] = be.kind if be is not None else "cpu"
        return be


_WARM_THREAD = None


def warm_up_async() -> None:
    """主界面启动后在后台线程把后端选好：起 GPU 服务进程（torch / CUDA 都在那边），连上、自检、预热。
    主界面进程自己从头到尾不 import torch（Q187）。什么都不用时也没副作用。"""
    global _WARM_THREAD
    if _RESOLVED or (_WARM_THREAD is not None and _WARM_THREAD.is_alive()):
        return

    def _run():
        try:
            if not enabled_by_settings() or not has_nvidia_gpu():
                be = backend()
            else:
                ensure_gui_server(wait=True)
                be = backend()
            _LOG.info("[GPU] 预热完成：%s", be.kind if be is not None else "cpu")
        except Exception:
            _LOG.exception("[GPU] 预热失败")
    _WARM_THREAD = threading.Thread(target=_run, name="cutora-gpu-warmup", daemon=True)
    _WARM_THREAD.start()


# ───────────────────────────── 主界面常驻的 GPU 服务进程 ─────────────────────────────
#
# Q187：主界面不再在进程内建 CUDA 上下文。预览的抠像 / 调整层走这个常驻服务（和导出用的是同一套
# RemoteBackend）；并行导出时渲染子进程也直接连它，省掉再起一个。服务进程盯着主界面的 pid，主界面
# 退出它就退出。日志在 logs/gpu_fx/gpu_server.log。
_GUI_SERVER = {"proc": None, "addr": "", "starting": False, "failed": False, "workdir": ""}
_GUI_SERVER_LOCK = threading.Lock()
_NV_CACHE = {"v": None}


def has_nvidia_gpu() -> bool:
    """不碰 torch：nvidia-smi 能报出一块卡就算有（真正能不能用 CUDA 由服务进程用 torch 验证）。"""
    if _NV_CACHE["v"] is None:
        try:
            from cutora.core.runtime import _nvidia_gpu_name
            _NV_CACHE["v"] = bool(_nvidia_gpu_name())
        except Exception:
            _NV_CACHE["v"] = False
    return bool(_NV_CACHE["v"])


def gui_server_addr() -> str:
    """主界面常驻服务的地址（活着才返回；死了返回 ""）。"""
    with _GUI_SERVER_LOCK:
        proc, addr = _GUI_SERVER["proc"], _GUI_SERVER["addr"]
    if proc is None or not addr:
        return ""
    if proc.poll() is not None:
        with _GUI_SERVER_LOCK:
            _GUI_SERVER.update(proc=None, addr="")
        return ""
    return addr


def ensure_gui_server(wait: bool = False) -> str:
    """起（或确认）主界面的常驻 GPU 服务。wait=False：没起就在后台起、立刻返回 ""；
    wait=True：起好或失败再返回。失败过一次就不再试（日志里有原因）。"""
    addr = gui_server_addr()
    if addr:
        return addr
    with _GUI_SERVER_LOCK:
        if _GUI_SERVER["failed"]:
            return ""
        if _GUI_SERVER["starting"] and not wait:
            return ""
        starting_already = _GUI_SERVER["starting"]
        _GUI_SERVER["starting"] = True

    def _start():
        try:
            workdir = ""
            try:
                from cutora.core.paths import user_logs_dir
                workdir = os.path.join(user_logs_dir(), "gpu_fx")
                os.makedirs(workdir, exist_ok=True)
            except Exception:
                workdir = ""
            proc, a = start_server(timeout=60.0, workdir=workdir)
            with _GUI_SERVER_LOCK:
                _GUI_SERVER.update(proc=proc, addr=a, workdir=workdir, failed=not bool(a))
            if a:
                _LOG.info("[GPU] 主界面的 GPU 服务已启动 (pid=%s)", getattr(proc, "pid", "?"))
            else:
                _LOG.warning("[GPU] 主界面的 GPU 服务没起来，预览 / 导出的抠像走 CPU（原因见上方 [GPU] 行）")
        except Exception:
            _LOG.exception("[GPU] 起主界面的 GPU 服务失败")
            with _GUI_SERVER_LOCK:
                _GUI_SERVER.update(failed=True)
        finally:
            with _GUI_SERVER_LOCK:
                _GUI_SERVER["starting"] = False

    if wait:
        if starting_already:
            t0 = time.time()
            while _GUI_SERVER["starting"] and time.time() - t0 < 90:
                time.sleep(0.1)
        else:
            _start()
        return gui_server_addr()
    threading.Thread(target=_start, name="cutora-gpu-server-start", daemon=True).start()
    return ""


def stop_gui_server() -> None:
    with _GUI_SERVER_LOCK:
        proc = _GUI_SERVER["proc"]
        _GUI_SERVER.update(proc=None, addr="")
    if proc is not None:
        try:
            if proc.poll() is None:
                proc.kill()
        except Exception:
            pass


def reset() -> None:
    """测试用：下次 backend() 重新选。"""
    global _BACKEND, _RESOLVED
    with _LOCK:
        if _BACKEND is not None:
            try:
                _BACKEND.close()
            except Exception:
                pass
        _BACKEND = None
        _RESOLVED = False


def disable(reason: str) -> None:
    """运行中出错：这个进程以后都走 CPU。"""
    global _BACKEND, _RESOLVED
    with _LOCK:
        if _BACKEND is not None:
            _LOG.error("GPU 像素运算已关闭（本进程之后走 CPU）：%s", reason)
            try:
                _BACKEND.close()
            except Exception:
                pass
        _BACKEND = None
        _RESOLVED = True
        STATS["errors"] += 1
        STATS["kind"] = "cpu"


def _resolve():
    if not enabled_by_settings():
        _LOG.info("[GPU] 像素运算：已关闭（设置 / 环境变量）")
        return None
    addr = os.environ.get(ENV_ADDR) or ""
    if addr:
        be = RemoteBackend(addr)
        be.connect()
        if not _selftest(be):
            be.close()
            return None
        _LOG.info("[GPU] 像素运算：经服务进程 %s", addr.rsplit(":", 1)[0])
        return be
    from cutora.core.flags import is_render_child
    if is_render_child():
        return None                          # 渲染子进程没有服务地址 → 不碰 torch
    if (os.environ.get(ENV_LOCAL) or "") == "1" or (os.environ.get(ENV_DEVICE) or "").strip():
        # 排查 / 测试：进程内 torch（老行为）
        dev = cuda_device()
        if dev is None:
            _LOG.info("[GPU] 像素运算：没有可用的 CUDA 设备，用 CPU")
            return None
        be = LocalBackend(dev)
        if not _selftest(be):
            return None
        _LOG.info("[GPU] 像素运算：进程内 %s", dev)
        return be
    # 主界面：走常驻服务进程（本进程不 import torch）
    if not has_nvidia_gpu():
        _LOG.info("[GPU] 像素运算：没有 NVIDIA 显卡，用 CPU")
        return None
    with _GUI_SERVER_LOCK:
        failed = _GUI_SERVER["failed"]
    if failed:
        return None
    addr = ensure_gui_server(wait=False)
    if not addr:
        return _PENDING                      # 服务还在起：这次用 CPU，下次再问
    be = RemoteBackend(addr)
    be.connect()
    if not _selftest(be):
        be.close()
        return None
    _LOG.info("[GPU] 像素运算：经主界面的 GPU 服务 %s", addr.rsplit(":", 1)[0])
    return be


ENV_DEVICE = "CUTORA_GPU_FX_DEVICE"


def cuda_device():
    """能用就返回 "cuda"，否则 None。不建上下文（第一次真算时才建）。CUTORA_GPU_FX_DEVICE=cpu 是测试用的。"""
    forced = (os.environ.get(ENV_DEVICE) or "").strip()
    try:
        import torch
        if forced:
            return forced
        if not torch.cuda.is_available():
            return None
        return "cuda"
    except Exception:
        return None


# ───────────────────────────── 自检 ─────────────────────────────

def _selftest(be) -> bool:
    """小图过一遍抠像 + 调整层，和 CPU 版比；顺便把 CUDA 的核预热掉。"""
    try:
        from PySide6.QtGui import QImage
        from cutora.fx import layer_fx as LF
        from cutora.render import adjust_layer as AL
        rng = np.random.default_rng(1)
        h, w = 96, 128
        fr = rng.integers(0, 256, (h, w, 3), dtype=np.uint8)
        fr[:, : w // 2] = (82, 148, 76)                     # 左半绿幕（BGR）
        k = {"enabled": True, "color": "#4C9452", "gain": 1.4, "balance": 0.5, "despill": 1.0,
             "despill_on": True, "despill_mode": "limit", "despill_scope": "edges", "preblur": 0.0,
             "black": 0.02, "white": 0.98, "gamma": 1.0, "erode": 0.0, "blur": 0.0, "despot": 0.0,
             "view": "comp"}

        class _H:
            pass
        host = _H()
        host.keyer = k
        host.masks = []
        q = QImage(fr.data, w, h, fr.strides[0], QImage.Format.Format_BGR888).copy()
        ref = LF.apply_layer_fx(q, host, px_scale=1.0, _force_cpu=True)
        ref = np.frombuffer(ref.constBits(), np.uint8).reshape(h, ref.bytesPerLine())[:, :w * 4].reshape(h, w, 4)
        t0 = time.perf_counter()
        got = be.key_layer(fr, k, None, bgr=True)
        d = np.abs(got.astype(int) - ref.astype(int))
        if d.max() > SELFTEST_MAX_DIFF or d.mean() > SELFTEST_MEAN_DIFF:
            _LOG.error("[GPU] 自检失败（抠像）：max %d mean %.2f", int(d.max()), float(d.mean()))
            return False
        # 调整层：镜头模糊 + 调色 + 半透明填充
        canvas = rng.integers(0, 256, (h, w, 4), dtype=np.uint8)
        canvas[..., 3] = 255
        ov = AL.adjust_defaults(w, h, start=0.0, end=1.0)
        ov.update({"x": 20, "y": 20, "w": 80, "h": 50})
        ov["solid"].update({"color": "#3E3EAC", "opacity": 0.32})
        ov["effect"].update({"on": True, "kinds": ["lens_blur", "grade"], "amount_lens_blur": 40,
                             "grade": {"exposure": -1.0, "saturation": 1.0}})
        img_cpu = QImage(canvas.data, w, h, w * 4, QImage.Format.Format_ARGB32_Premultiplied).copy()
        img_gpu = img_cpu.copy()
        AL.apply_item(img_cpu, ov, _gpu=None)
        AL.apply_item(img_gpu, ov, _gpu=be)
        a = np.frombuffer(img_cpu.constBits(), np.uint8).reshape(h, img_cpu.bytesPerLine())[:, :w * 4]
        b = np.frombuffer(img_gpu.constBits(), np.uint8).reshape(h, img_gpu.bytesPerLine())[:, :w * 4]
        d2 = np.abs(a.astype(int) - b.astype(int))
        if d2.max() > SELFTEST_MAX_DIFF or d2.mean() > SELFTEST_MEAN_DIFF:
            _LOG.error("[GPU] 自检失败（调整图层）：max %d mean %.2f", int(d2.max()), float(d2.mean()))
            return False
        _LOG.info("[GPU] 自检通过（抠像 max %d / 调整层 max %d，%.0f ms 含预热）",
                  int(d.max()), int(d2.max()), (time.perf_counter() - t0) * 1000.0)
        return True
    except Exception:
        _LOG.exception("[GPU] 自检出错")
        return False


# ───────────────────────────── 进程内后端 ─────────────────────────────

class LocalBackend:
    kind = "local"

    def __init__(self, device):
        self.device = device
        self._lock = threading.Lock()        # 同一上下文串行提交（显卡本身就是一个队列）

    def key_layer(self, frame_u8, keyer, mask_f32, *, bgr=True):
        from cutora.fx import gpu_ops
        t0 = time.perf_counter()
        with self._lock:
            out = gpu_ops.key_layer(frame_u8, keyer, mask_f32, device=self.device, bgr=bgr)
        _count(t0, frame_u8.nbytes + out.nbytes)
        return out

    def adjust_region(self, crop_bgra_u8, eff, long_side, *, ox, oy, shape_u8, mix, fill=None, border=None):
        from cutora.fx import gpu_ops
        t0 = time.perf_counter()
        with self._lock:
            out = gpu_ops.adjust_region(crop_bgra_u8, eff, long_side, ox=ox, oy=oy, shape_u8=shape_u8,
                                        mix=mix, fill=fill, border=border, device=self.device)
        _count(t0, crop_bgra_u8.nbytes + out.nbytes)
        return out

    def close(self):
        pass


def _count(t0, nbytes):
    STATS["calls"] += 1
    STATS["ms"] += (time.perf_counter() - t0) * 1000.0
    STATS["bytes"] += int(nbytes)


def stats_line() -> str:
    n = max(1, STATS["calls"])
    return "GPU 像素运算（%s）%d 次，平均 %.1f ms/次，往返 %.0f MB" % (
        STATS["kind"] or "cpu", STATS["calls"], STATS["ms"] / n, STATS["bytes"] / 1048576.0)


# ───────────────────────────── 跨进程：共享内存 + 小消息 ─────────────────────────────
#
# 每个调用方线程一块共享内存（够大就复用，不够就换一块更大的）。请求消息只带元数据：
#   ("op", {参数}, [(名字, 偏移, shape, dtype), ...], 共享内存名)
# 服务端按名字映射同一块内存、就地读输入、算完把输出写回同一块的末尾，回 (shape, dtype, 偏移) 或 ("error", 文本)。

def _shm_open(name, size=None):
    from multiprocessing import shared_memory
    if size is None:
        return shared_memory.SharedMemory(name=name)
    return shared_memory.SharedMemory(name=name, create=True, size=int(size))


class _ThreadSlot:
    __slots__ = ("conn", "shm", "size")

    def __init__(self):
        self.conn = None
        self.shm = None
        self.size = 0


class RemoteBackend:
    kind = "remote"

    def __init__(self, addr: str):
        host, port, key = addr.rsplit(":", 2)
        self.address = (host, int(port))
        self.authkey = key.encode("utf-8")
        self._tls = threading.local()
        self._pid = os.getpid()
        self._slots = []
        self._slots_lock = threading.Lock()

    def connect(self):
        self._slot()

    def _slot(self) -> _ThreadSlot:
        s = getattr(self._tls, "slot", None)
        if s is None or s.conn is None:
            from multiprocessing.connection import Client
            s = _ThreadSlot()
            s.conn = Client(self.address, authkey=self.authkey)
            self._tls.slot = s
            with self._slots_lock:
                self._slots.append(s)
        return s

    def _ensure(self, s: _ThreadSlot, need: int):
        if s.shm is not None and s.size >= need:
            return
        if s.shm is not None:
            try:
                s.shm.close()
                s.shm.unlink()
            except Exception:
                pass
        size = max(int(need * 1.25), 4 << 20)
        s.shm = _shm_open("cutora_gpu_%d_%d_%d" % (self._pid, threading.get_ident() & 0xFFFF, int(time.time() * 1000) & 0xFFFFFF), size)
        s.size = size

    def _call(self, op: str, params: dict, arrays: dict):
        s = self._slot()
        metas = []
        off = 0
        for name, a in arrays.items():
            if a is None:
                continue
            a = np.ascontiguousarray(a)
            off = (off + 63) & ~63
            metas.append((name, off, tuple(a.shape), str(a.dtype)))
            off += a.nbytes
        out_cap = int(params.pop("_out_bytes", 0))
        self._ensure(s, off + 128 + out_cap)
        buf = s.shm.buf
        for (name, o, shape, dt), a in zip(metas, [v for v in arrays.values() if v is not None]):
            dst = np.ndarray(shape, dtype=dt, buffer=buf, offset=o)
            dst[...] = a                                               # 一次 memcpy 进共享内存
            del dst
        t0 = time.perf_counter()
        s.conn.send((op, params, metas, s.shm.name, s.size))
        # 服务进程要是卡住（不管什么原因），渲染进程不能跟着卡：30 s 没回话就当它坏了，本进程退回 CPU
        if not s.conn.poll(REMOTE_CALL_TIMEOUT):
            raise TimeoutError("GPU 服务 %.0f s 没有回话（op=%s）" % (REMOTE_CALL_TIMEOUT, op))
        reply = s.conn.recv()
        if not isinstance(reply, tuple) or not reply or reply[0] == "error":
            raise RuntimeError("GPU 服务端：%s" % (reply[1] if isinstance(reply, tuple) and len(reply) > 1 else reply))
        _tag, shape, dtype, o = reply
        n = int(np.prod(shape)) * np.dtype(dtype).itemsize
        out = np.frombuffer(bytes(buf[o:o + n]), dtype=dtype).reshape(shape)
        _count(t0, off + n)
        return out

    def ping(self) -> bool:
        out = self._call("ping", {}, {})
        return out.shape == (0,)

    def key_layer(self, frame_u8, keyer, mask_f32, *, bgr=True):
        h, w = frame_u8.shape[:2]
        return self._call("key_layer", {"keyer": keyer, "bgr": bool(bgr), "_out_bytes": h * w * 4},
                          {"frame": frame_u8, "mask": mask_f32})

    def adjust_region(self, crop_bgra_u8, eff, long_side, *, ox, oy, shape_u8, mix, fill=None, border=None):
        sh, sw = shape_u8.shape[:2]
        params = {"eff": eff, "long_side": float(long_side), "ox": int(ox), "oy": int(oy), "mix": float(mix),
                  "fill": None if fill is None else ([int(v) for v in fill[0]], float(fill[1])),
                  "border": None if border is None else ([int(v) for v in border[1]], float(border[2])),
                  "_out_bytes": sh * sw * 4}
        return self._call("adjust_region", params,
                          {"crop": crop_bgra_u8, "shape": shape_u8, "bmask": None if border is None else border[0]})

    def close(self):
        with self._slots_lock:
            slots, self._slots = self._slots, []
        for s in slots:
            try:
                if s.conn is not None:
                    s.conn.close()
            except Exception:
                pass
            try:
                if s.shm is not None:
                    s.shm.close()
                    s.shm.unlink()
            except Exception:
                pass


# ───────────────────────────── 服务端 ─────────────────────────────

def _graph_key(op, params, metas):
    import json
    return (op, json.dumps(params, sort_keys=True, default=str),
            tuple((str(n), tuple(int(v) for v in shape), str(dt)) for n, _o, shape, dt in metas))


def _make_fn(op, params, dev):
    """把一次请求变成「输入张量 dict → 输出张量」的纯函数（录 graph 和直接算都用它）。"""
    from cutora.fx import gpu_ops as _ops
    if op == "key_layer":
        keyer = params.get("keyer")
        bgr = bool(params.get("bgr", True))

        def fn(t):
            return _ops.key_layer(t["frame"], keyer, t.get("mask"), device=dev, bgr=bgr, as_tensor=True)
        return fn
    if op == "adjust_region":
        fill = params.get("fill")
        border = params.get("border")
        eff = params.get("eff") or {}
        long_side = float(params.get("long_side") or 1.0)
        ox, oy, mix = int(params["ox"]), int(params["oy"]), float(params["mix"])
        fill_t = None if fill is None else (np.array(fill[0], np.uint8), float(fill[1]))
        bcol = None if border is None else (np.array(border[0], np.uint8), float(border[1]))

        def fn(t):
            return _ops.adjust_region(
                t["crop"], eff, long_side, ox=ox, oy=oy, shape_u8=t["shape"], mix=mix, fill=fill_t,
                border=None if bcol is None else (t.get("bmask"), bcol[0], bcol[1]),
                device=dev, as_tensor=True)
        return fn
    raise ValueError("unknown op %r" % (op,))


class _FakeGraph:
    """测试用：回放 = 直接算一遍写进静态输出。"""

    def __init__(self, fn, static, out, fail_at=0):
        self.fn, self.static, self.out = fn, static, out
        self.replays = 0
        self.fail_at = fail_at

    def replay(self):
        self.replays += 1
        if self.fail_at and self.replays == self.fail_at:
            raise RuntimeError("fake replay failure")
        self.out.copy_(self.fn(self.static))


class _GraphInst:
    __slots__ = ("inputs", "out", "graph", "lock")

    def __init__(self, inputs, out, graph):
        self.inputs = inputs
        self.out = out
        self.graph = graph
        self.lock = threading.Lock()


class _CaptureGate:
    """读写锁：普通调用（回放 / 直接算）是「读」，可以很多个同时进行；录制 graph 是「写」，
    必须独占 —— 录制期间别的线程往同一条 cudaStream 上发的核会被录进 graph（torch 的
    Stream() 来自一个 32 条的池，连接一多就会两个线程共用一条），Luca 机器上就是这样
    炸的：「Cannot prepare for replay during capturing stage」。写者优先，免得饿死。"""

    def __init__(self):
        self._cv = threading.Condition()
        self._readers = 0
        self._writer = False
        self._writers_waiting = 0

    def acquire_read(self):
        with self._cv:
            while self._writer or self._writers_waiting:
                self._cv.wait()
            self._readers += 1

    def release_read(self):
        with self._cv:
            self._readers -= 1
            if self._readers == 0:
                self._cv.notify_all()

    def acquire_write(self):
        with self._cv:
            self._writers_waiting += 1
            while self._writer or self._readers:
                self._cv.wait()
            self._writers_waiting -= 1
            self._writer = True

    def release_write(self):
        with self._cv:
            self._writer = False
            self._cv.notify_all()


def new_cuda_stream(torch):
    """一条**独占**的 CUDA stream。torch.cuda.Stream() 从 32 条的池里轮流拿，连接多了就共用；
    这里直接向 cudart 要一条新的（cudaStreamNonBlocking），包成 ExternalStream。要不到就退回池。"""
    try:
        import ctypes
        import glob
        lib = None
        try:
            from cuda.bindings import runtime as _rt                     # cuda-python 在的话最省事
            err, ptr = _rt.cudaStreamCreateWithFlags(1)
            if int(err) == 0 and int(ptr):
                return torch.cuda.ExternalStream(int(ptr))
        except Exception:
            pass
        tdir = os.path.join(os.path.dirname(torch.__file__), "lib")
        cands = glob.glob(os.path.join(tdir, "cudart64_1*.dll")) + glob.glob(os.path.join(tdir, "libcudart.so*"))
        try:
            import nvidia.cuda_runtime as _ncr                            # Linux pip 轮子把 cudart 放这儿
            cands += glob.glob(os.path.join(os.path.dirname(_ncr.__file__), "lib", "libcudart.so*"))
        except Exception:
            pass
        for c in cands:
            try:
                lib = ctypes.CDLL(c)
                break
            except Exception:
                lib = None
        if lib is None:
            return torch.cuda.Stream()
        handle = ctypes.c_void_p()
        fn = lib.cudaStreamCreateWithFlags
        fn.argtypes = [ctypes.POINTER(ctypes.c_void_p), ctypes.c_uint]
        fn.restype = ctypes.c_int
        if fn(ctypes.byref(handle), 1) == 0 and handle.value:
            return torch.cuda.ExternalStream(int(handle.value))
    except Exception:
        pass
    return torch.cuda.Stream()


class _GraphPool:
    """服务进程里的 CUDA graph 缓存。

    瓶颈不在显卡算力（Luca 机器：显卡利用率 45%，24 条连接每次却要等 40 ms）而在服务进程的
    Python：一次抠像 ≈ 85 个 torch 算子、一次调整层 ≈ 140 个，每个算子的发射都握着 GIL，
    24 条线程抢一个解释器。同一个片段的抠像参数、尺寸每帧都一样，只有像素变 —— 正是
    CUDA graph 的用武之地：录一次，之后每帧只做「拷入静态输入 → 整图回放 → 拷出」三步，
    CPU 开销从几毫秒降到几十微秒。

    按 (算子, 参数, 输入形状) 建 key；每个 key 最多 GRAPH_POOL_PER_KEY 份实例（各自静态缓冲，
    借用时加锁，让拷入 / 算 / 拷出能两两交错）。录制失败或回放结果和直接算的不一样 → 该 key
    永久走直接算（记日志）。CUTORA_GPU_FX_GRAPHS=0 整体关掉。
    """

    def __init__(self, device):
        from cutora.fx import gpu_ops
        self.torch = gpu_ops.torch_mod()
        self.device = device
        mode = (os.environ.get(ENV_GRAPHS) or "1").strip().lower()
        self.fake = mode.startswith("fake")                    # 测试：cpu 设备上用假 graph 走同一套池 / 锁 / 核对逻辑
        self.fake_fail = (mode == "fake-fail")                 # 测试：假 graph 第 3 次回放抛异常（验证不卡死、退回直接算）
        self.enabled = mode != "0" and (str(device).startswith("cuda") or self.fake)
        self._pools = {}          # key -> [inst, ...]
        self._bad = set()         # key -> 不再尝试
        self._lock = threading.Lock()
        self.gate = _CaptureGate()
        self.replays = 0
        self.eager = 0
        self.errors = 0

    def _capture(self, fn, tens):
        torch = self.torch
        static = {k: torch.empty_like(v) for k, v in tens.items()}
        for k, v in tens.items():
            static[k].copy_(v)
        if self.fake:
            out = fn(static).clone()
            return _GraphInst(static, out, _FakeGraph(fn, static, out, fail_at=3 if self.fake_fail else 0))
        side = new_cuda_stream(torch)
        side.wait_stream(torch.cuda.current_stream())
        with torch.cuda.stream(side):
            for _ in range(2):                                   # 预热：常量 / 查找表上显卡，cuBLAS 工作区按这条 stream 分配
                fn(static)
        torch.cuda.current_stream().wait_stream(side)
        side.synchronize()
        g = torch.cuda.CUDAGraph()
        # 录制就在预热的那条 stream 上（cuBLAS 工作区是按 stream 建的；换 stream 录会在录制中分配 → 报错）
        with torch.cuda.graph(g, stream=side, capture_error_mode="thread_local"):
            out = fn(static)
        return _GraphInst(static, out, g)

    def _verify(self, inst, fn, tens):
        """用当前这帧的真实输入：回放 vs 直接算，必须逐字节一致。"""
        torch = self.torch
        ref = fn(tens).contiguous()
        for k, v in tens.items():
            inst.inputs[k].copy_(v)
        inst.graph.replay()
        if not self.fake:
            torch.cuda.current_stream().synchronize()
        return bool(torch.equal(ref, inst.out)), ref

    def run(self, key, fn, views, io, log):
        """返回输出张量（显卡上，可能是实例的静态输出 —— 调用方在 with 块里拷走）。
        用法：with pool.run(...) as out: ...；拿不到 graph 时 out 是直接算的结果。
        views：共享内存里的 numpy 视图；有 graph 时直接拷进静态输入，省一次显卡内拷贝。"""
        return _GraphRun(self, key, fn, views, io, log)

    def _acquire(self, key, fn, tens_fn, log):
        """借一份实例（必要时录新的）；返回 (inst, ref_out_or_None)。None → 直接算。
        调用方此刻持有 gate 的读锁；录制时这里会先放掉读锁、拿写锁独占，录完再换回读锁。"""
        if not self.enabled or key in self._bad:
            return None, None
        with self._lock:
            insts = self._pools.get(key)
            if insts is None:
                insts = self._pools[key] = []
        for inst in insts:
            if inst.lock.acquire(blocking=False):
                return inst, None
        if len(insts) < GRAPH_POOL_PER_KEY:
            self.gate.release_read()
            self.gate.acquire_write()
            try:
                if key in self._bad:
                    return None, None
                for inst in insts:                                   # 等写锁期间别人可能已经录好了
                    if inst.lock.acquire(blocking=False):
                        return inst, None
                if len(insts) < GRAPH_POOL_PER_KEY:
                    try:
                        free_b, _total = self.torch.cuda.mem_get_info() if not self.fake else (1 << 40, 1 << 40)
                    except Exception:
                        free_b = 0
                    if len(insts) == 0 or free_b >= GRAPH_MIN_FREE_VRAM_GB * (1 << 30):
                        t0 = time.perf_counter()
                        try:
                            tens = tens_fn()
                            inst = self._capture(fn, tens)
                            ok, ref = self._verify(inst, fn, tens)
                        except Exception as e:
                            self._bad.add(key)
                            log("[GPU-FX] CUDA graph 录制失败，%s 走直接算：%s: %s" % (key[0], type(e).__name__, e))
                            return None, None
                        if not ok:
                            self._bad.add(key)
                            log("[GPU-FX] CUDA graph 回放结果和直接算不一致，%s 走直接算" % (key[0],))
                            return None, None
                        inst.lock.acquire()
                        with self._lock:
                            insts.append(inst)
                        log("[GPU-FX] CUDA graph 已录制并核对：%s %s（实例 %d，%.0f ms）" % (
                            key[0], "×".join(str(v) for v in key[2][0][1][:2]), len(insts),
                            (time.perf_counter() - t0) * 1000.0))
                        return inst, ref
            finally:
                self.gate.release_write()
                self.gate.acquire_read()
        # 都在忙：等最先空出来的那份（回放很快，比直接算省得多）；等太久就直接算，绝不卡死
        inst = insts[0] if insts else None
        if inst is None:
            return None, None
        if inst.lock.acquire(timeout=2.0):
            return inst, None
        log("[GPU-FX] 等 graph 实例超过 2 s，这次直接算：%s" % (key[0],))
        return None, None


class _GraphRun:
    def __init__(self, pool, key, fn, views, io, log):
        self.pool, self.key, self.fn, self.views, self.io, self.log = pool, key, fn, views, io, log
        self.inst = None
        self._tens = None

    def _tens_fn(self):
        if self._tens is None:
            self._tens = {k: self.io.up(k, v) for k, v in self.views.items()}
        return self._tens

    def __enter__(self):
        pool = self.pool
        pool.gate.acquire_read()
        self._reading = True
        try:
            inst, ref = pool._acquire(self.key, self.fn, self._tens_fn, self.log)
            self.inst = inst
            if inst is None:
                pool.eager += 1
                return self.fn(self._tens_fn())
            if ref is not None:                                   # 刚录好：核对时已经算过这一帧
                pool.replays += 1
                return inst.out
            try:
                for k, v in self.views.items():
                    self.io.up_into(k, inst.inputs[k], v)
                inst.graph.replay()
            except Exception as e:
                # 回放出错：归还实例、这种 key 以后直接算，这一次也直接算 —— 客户端永远拿到结果，
                # 不会因为 graph 的问题整进程退回 CPU。（__enter__ 抛异常时 __exit__ 不会被调，
                # 锁必须在这里放，否则后面所有要这份实例的线程全卡死 —— Luca 机器上的停摆就是这个。）
                inst.lock.release()
                self.inst = None
                pool.errors += 1
                pool._bad.add(self.key)
                self.log("[GPU-FX] graph 回放出错，%s 改走直接算：%s: %s" % (self.key[0], type(e).__name__, e))
                pool.eager += 1
                return self.fn(self._tens_fn())
            pool.replays += 1
            return inst.out
        except BaseException:
            self._release()
            raise

    def _release(self):
        if self.inst is not None:
            self.inst.lock.release()
            self.inst = None
        if getattr(self, "_reading", False):
            self._reading = False
            self.pool.gate.release_read()

    def __exit__(self, *exc):
        self._release()
        return False


class _ConnIO:
    """服务进程里每个连接线程一套搬运工具：自己的 CUDA stream（各连接并行提交，不再全局串行）、
    把客户端的共享内存登记成页锁定（登记成功 = 显卡直接 DMA 读写那块内存，零拷贝）、
    登记不了就退到一块常驻的页锁定中转缓冲。cpu 设备（测试）时只是 numpy ↔ 张量的视图。"""

    _REG_LOGGED = False

    def __init__(self, device):
        from cutora.fx import gpu_ops
        self.torch = gpu_ops.torch_mod()
        self.device = device
        self.cuda = device is not None and str(device).startswith("cuda")
        self.stream = new_cuda_stream(self.torch) if self.cuda else None
        self.shm = None
        self.name = None
        self.registered = False
        self.base = 0
        self._stage = {}

    # ── 共享内存映射 / 页锁定登记 ──
    def map(self, name):
        if self.shm is not None and self.name == name:
            return self.shm
        self.unmap()
        self.shm = _shm_open(name)
        self.name = name
        if self.cuda:
            try:
                view = np.ndarray((self.shm.buf.nbytes,), np.uint8, buffer=self.shm.buf)
                self.base = int(view.ctypes.data)
                rc = int(self.torch.cuda.cudart().cudaHostRegister(self.base, int(view.nbytes), 0))
                self.registered = (rc == 0)
                del view
            except Exception as e:
                rc = repr(e)
                self.registered = False
            if not _ConnIO._REG_LOGGED:
                _ConnIO._REG_LOGGED = True
                if self.registered:
                    print("[GPU-FX] 共享内存已登记为页锁定：显卡直接 DMA 读写，零拷贝", flush=True)
                else:
                    print("[GPU-FX] 共享内存页锁定登记失败（rc=%s），改走页锁定中转缓冲" % (rc,), flush=True)
        return self.shm

    def unmap(self):
        if self.shm is None:
            return
        if self.registered:
            try:
                self.torch.cuda.cudart().cudaHostUnregister(self.base)
            except Exception:
                pass
        self.registered = False
        try:
            self.shm.close()
        except Exception:
            pass
        self.shm = None
        self.name = None

    def close(self):
        self.unmap()
        self._stage.clear()

    def scope(self):
        import contextlib
        return self.torch.cuda.stream(self.stream) if self.stream is not None else contextlib.nullcontext()

    def _staging(self, key, nbytes):
        st = self._stage.get(key)
        if st is None or st.numel() < nbytes:
            st = self.torch.empty((max(int(nbytes), 1 << 20),), dtype=self.torch.uint8, pin_memory=True)
            self._stage[key] = st
        return st

    # ── 上传 / 写回 ──
    def up(self, name, view: np.ndarray):
        """共享内存里的 numpy 视图 → 设备张量（同 dtype / shape）。"""
        torch = self.torch
        t = torch.from_numpy(view)
        if not self.cuda:
            return t
        if self.registered:
            return t.to(self.device, non_blocking=True)
        flat = view.reshape(-1).view(np.uint8)
        st = self._staging(name, flat.nbytes)
        st[:flat.nbytes].copy_(torch.from_numpy(flat))
        d = st[:flat.nbytes].to(self.device, non_blocking=True)
        return d.view(getattr(torch, str(view.dtype))).reshape(view.shape)

    def up_into(self, name, dst_t, view: np.ndarray):
        """共享内存视图 → 已有的设备张量（graph 的静态输入），形状 / dtype 一致。"""
        torch = self.torch
        src = torch.from_numpy(view)
        if not self.cuda or self.registered:
            dst_t.copy_(src, non_blocking=True)
            return
        flat = view.reshape(-1).view(np.uint8)
        st = self._staging(name, flat.nbytes)
        st[:flat.nbytes].copy_(torch.from_numpy(flat))
        dst_t.view(-1).view(torch.uint8).copy_(st[:flat.nbytes], non_blocking=True)

    def down(self, t, dst: np.ndarray):
        """设备张量 → 共享内存里的目标视图（shape / dtype 已一致）。回来前把本连接的 stream 等完。"""
        torch = self.torch
        t = t.contiguous()
        if not self.cuda:
            dst[...] = t.numpy()
            return
        if self.registered:
            torch.from_numpy(dst).copy_(t, non_blocking=True)
            self.stream.synchronize()
            return
        flat = dst.reshape(-1).view(np.uint8)
        st = self._staging("__out__", flat.nbytes)
        st[:flat.nbytes].copy_(t.reshape(-1).view(torch.uint8), non_blocking=True)
        self.stream.synchronize()
        flat[...] = st[:flat.nbytes].numpy()


def serve(addr_file: str, parent_pid: int | None = None) -> int:
    """GPU 像素运算服务进程的主循环。把 host:port:authkey 写进 addr_file，父进程读了再派渲染子进程。
    父进程退出就跟着退。"""
    import secrets
    import sys
    from multiprocessing.connection import Listener
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")     # 日志文件要能读中文
        except Exception:
            pass
    dev = cuda_device()
    if dev is None:
        try:
            with open(addr_file + ".err", "w", encoding="utf-8") as fh:
                fh.write("no cuda")
        except Exception:
            pass
        print("[GPU-FX] 没有可用的 CUDA 设备", file=sys.stderr)
        return 3
    from cutora.fx import gpu_ops as _ops
    _ops.torch_mod()
    graphs = _GraphPool(dev)
    print("[GPU-FX] CUDA graph：%s" % ("启用" if graphs.enabled else "关闭"), flush=True)
    key = secrets.token_hex(16)
    listener = Listener(("127.0.0.1", 0), authkey=key.encode("utf-8"))
    host, port = listener.address
    tmp = addr_file + ".tmp"
    # 设备信息给主界面看（它自己不 import torch）：显卡名 / torch 版本 / CUDA 版本
    try:
        import json
        torch = _ops.torch_mod()
        info = {"device": dev, "name": (torch.cuda.get_device_name(0) if str(dev).startswith("cuda") else dev),
                "torch": str(getattr(torch, "__version__", "")), "cuda": str(getattr(torch.version, "cuda", "") or "")}
        with open(addr_file + ".info", "w", encoding="utf-8") as fh:
            json.dump(info, fh, ensure_ascii=False)
    except Exception:
        pass
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write("%s:%d:%s" % (host, port, key))
    os.replace(tmp, addr_file)
    print("[GPU-FX] 服务已启动 %s:%d device=%s" % (host, port, dev), flush=True)
    stop = threading.Event()

    def _watch_parent():
        from cutora.render.export_guard import pid_alive
        while not stop.is_set():
            if parent_pid and not pid_alive(parent_pid):
                print("[GPU-FX] 父进程已退出，服务停止", flush=True)
                stop.set()
                os._exit(0)
            time.sleep(1.0)
    if parent_pid:
        threading.Thread(target=_watch_parent, daemon=True).start()

    totals = {"ops": 0}

    def _util_sampler():
        """每 5 s 问一次 nvidia-smi，30 s 汇报一次：显卡算力利用率 / 编码器利用率 —— 看瓶颈在哪。"""
        import subprocess
        from cutora.core.proc import run as _run
        samples = []
        last = time.time()
        while not stop.is_set():
            time.sleep(5.0)
            try:
                r = _run(["nvidia-smi", "--query-gpu=utilization.gpu,utilization.encoder,memory.used",
                          "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=4)
                if r.returncode == 0:
                    g, e, m = [float(x.strip()) for x in r.stdout.strip().splitlines()[0].split(",")[:3]]
                    samples.append((g, e, m))
            except Exception:
                pass
            if time.time() - last >= 30.0 and samples:
                n = len(samples)
                print("[GPU-FX] 近 %.0f s：显卡算力利用率平均 %.0f%%（峰 %.0f%%），编码器 %.0f%%，显存已用 %.0f MB，累计 %d 次运算" % (
                    time.time() - last, sum(x[0] for x in samples) / n, max(x[0] for x in samples),
                    sum(x[1] for x in samples) / n, samples[-1][2], totals["ops"]), flush=True)
                samples = []
                last = time.time()
    if str(dev).startswith("cuda"):
        threading.Thread(target=_util_sampler, daemon=True).start()

    def _plog(msg):
        print(msg, flush=True)

    def _serve_conn(conn):
        io = _ConnIO(dev)
        n_ops = 0
        t_busy = 0.0
        t_cpu = 0.0            # 本线程的 CPU 时间（≈ 握着 GIL 发核 / 拷贝的时间）
        t_gpu = 0.0            # CUDA 事件量出来的本 stream 占用时间
        t_open = time.perf_counter()
        _torch = io.torch
        try:
            while not stop.is_set():
                try:
                    msg = conn.recv()
                except (EOFError, OSError):
                    return
                t_op = time.perf_counter()
                c_op = time.thread_time()
                try:
                    op, params, metas, shm_name, shm_size = msg
                    if op == "ping":
                        conn.send(("ok", (0,), "uint8", 0))
                        continue
                    shm = io.map(shm_name)
                    buf = shm.buf
                    views = {}
                    end = 0
                    for name, o, shape, dt in metas:
                        n = int(np.prod(shape)) * np.dtype(dt).itemsize
                        views[name] = np.ndarray(tuple(shape), dtype=dt, buffer=buf, offset=int(o))
                        end = max(end, int(o) + n)
                    fn = _make_fn(op, params, dev)
                    gkey = _graph_key(op, params, metas)
                    with io.scope():
                        ev0 = ev1 = None
                        if io.cuda:
                            ev0 = _torch.cuda.Event(enable_timing=True)
                            ev1 = _torch.cuda.Event(enable_timing=True)
                            ev0.record()
                        with graphs.run(gkey, fn, views, io, _plog) as out:
                            shape = tuple(int(v) for v in out.shape)
                            dt = str(out.dtype).replace("torch.", "")
                            nbytes = int(np.prod(shape)) * np.dtype(dt).itemsize
                            o = (end + 63) & ~63
                            if o + nbytes > int(shm_size):
                                raise MemoryError("共享内存不够放结果（%d + %d > %d）" % (o, nbytes, shm_size))
                            dst = np.ndarray(shape, dtype=dt, buffer=buf, offset=o)
                            if ev1 is not None:
                                ev1.record()
                            io.down(out, dst)              # 里面会等完本 stream（静态输出拷走后才归还实例）
                        if ev1 is not None:
                            try:
                                t_gpu += float(ev0.elapsed_time(ev1))
                            except Exception:
                                pass
                        del dst
                    del views, out
                    conn.send(("ok", shape, dt, o))
                    n_ops += 1
                    totals["ops"] += 1
                    t_busy += time.perf_counter() - t_op
                    t_cpu += time.thread_time() - c_op
                except Exception as e:
                    _LOG.exception("[GPU-FX] 处理请求失败")
                    try:
                        conn.send(("error", "%s: %s" % (type(e).__name__, e)))
                    except Exception:
                        return
        finally:
            if n_ops:
                print("[GPU-FX] 连接结束：%d 次运算，服务端平均 %.1f ms/次（其中本线程 CPU %.1f ms、"
                      "显卡 stream %.1f ms），连接存活 %.0f s；全服务累计 graph 回放 %d / 直接算 %d" % (
                          n_ops, t_busy * 1000.0 / n_ops, t_cpu * 1000.0 / n_ops, t_gpu / n_ops,
                          time.perf_counter() - t_open, graphs.replays, graphs.eager)
                      + ("，graph 出错 %d 次" % graphs.errors if graphs.errors else ""), flush=True)
            io.close()
            try:
                conn.close()
            except Exception:
                pass

    try:
        while not stop.is_set():
            try:
                conn = listener.accept()
            except Exception:
                if stop.is_set():
                    break
                continue
            threading.Thread(target=_serve_conn, args=(conn,), daemon=True).start()
    finally:
        try:
            listener.close()
        except Exception:
            pass
    return 0


def _note_server_info(info_path: str) -> None:
    """服务进程写的设备信息 → 回填给 runtime（STARTUP 行 / 状态栏）和 config（device 判定）。"""
    try:
        import json
        with open(info_path, encoding="utf-8") as fh:
            info = json.load(fh)
        is_cuda = str(info.get("device", "")).startswith("cuda")
        from cutora.core.runtime import note_cuda_probe
        note_cuda_probe(is_cuda, info.get("name") or None)
        from cutora.core.config import note_torch_cuda
        note_torch_cuda(is_cuda, "GPU 服务进程：torch %s / CUDA %s" % (info.get("torch"), info.get("cuda")))
        _LOG.info("[GPU] 服务进程设备：%s（torch %s，CUDA %s）", info.get("name"), info.get("torch"), info.get("cuda"))
    except Exception:
        pass


def _tail(path: str, n: int = 1500) -> str:
    try:
        with open(path, encoding="utf-8", errors="ignore") as fh:
            return fh.read()[-n:].strip()
    except Exception:
        return ""


def start_server(timeout: float = 30.0, workdir: str = ""):
    """父进程（主界面）起服务进程，返回 (Popen, addr) 或 (None, "")。

    ``workdir``：放 addr 文件和 gpu_server.log 的目录（并行导出传它的临时目录，
    这样服务进程的日志会和 w*.log 一起留存到 logs/export/parallel/<时间>/）。
    """
    import subprocess
    import sys
    import tempfile
    if not enabled_by_settings():
        _LOG.info("[GPU] 像素运算服务未启动：设置里已关闭（gpu_fx=false 或 CUTORA_GPU_FX=0）")
        return None, ""
    forced_dev = (os.environ.get(ENV_DEVICE) or "").strip()
    if not forced_dev and not has_nvidia_gpu():
        _LOG.info("[GPU] 像素运算服务未启动：没有 NVIDIA 显卡")
        return None, ""
    tmpdir = workdir if (workdir and os.path.isdir(workdir)) else tempfile.mkdtemp(prefix="cutora_gpufx_")
    addr_file = os.path.join(tmpdir, "gpu_server.addr")
    for stale in (addr_file, addr_file + ".err", addr_file + ".tmp", addr_file + ".info"):
        try:
            if os.path.exists(stale):
                os.remove(stale)              # 上一次留下的地址文件：不删会被当成这次的
        except Exception:
            pass
    from cutora.core.proc import popen as _popen
    from cutora.core.paths import REPO_ROOT
    env = dict(os.environ)
    env.pop(ENV_ADDR, None)
    env["CUTORA_GPU_FX_SERVER"] = "1"
    cmd = [sys.executable, "-u", "-m", "cutora", "--gpu-fx-server", addr_file, str(os.getpid())]
    # 服务进程的 stdout/stderr 落到 addr 旁边的 server.log，起不来时有据可查
    log_path = os.path.join(tmpdir, "gpu_server.log")
    log_fh = open(log_path, "w", encoding="utf-8", errors="ignore")
    try:
        proc = _popen(cmd, cwd=REPO_ROOT, env=env, stdin=subprocess.DEVNULL,
                      stdout=log_fh, stderr=subprocess.STDOUT)
    except Exception:
        _LOG.exception("[GPU] 服务进程启动失败")
        log_fh.close()
        return None, ""
    finally:
        # Popen 已经继承了句柄；父进程这一份关掉
        try:
            log_fh.close()
        except Exception:
            pass
    t0 = time.time()
    while time.time() - t0 < timeout:
        if os.path.isfile(addr_file):
            try:
                with open(addr_file, encoding="utf-8") as fh:
                    addr = fh.read().strip()
                if addr:
                    _note_server_info(addr_file + ".info")
                    return proc, addr
            except Exception:
                pass
        if os.path.isfile(addr_file + ".err") or proc.poll() is not None:
            err = _tail(addr_file + ".err")
            _LOG.warning("[GPU] 服务进程没起来（code=%s），导出走 CPU；服务进程输出：\n%s",
                         proc.poll(), _tail(log_path) or err)
            if "no cuda" in err:
                try:
                    from cutora.core.config import note_torch_cuda
                    note_torch_cuda(False, "GPU 服务进程：torch.cuda 不可用")
                except Exception:
                    pass
            return None, ""
        time.sleep(0.1)
    _LOG.warning("[GPU] 服务进程 %.0f 秒没就绪，导出走 CPU；服务进程输出：\n%s", timeout, _tail(log_path))
    try:
        proc.kill()
    except Exception:
        pass
    return None, ""
