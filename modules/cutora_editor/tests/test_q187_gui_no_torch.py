# SPDX-License-Identifier: GPL-3.0-or-later
"""Q187：主界面进程不再常驻 torch / CUDA。

Luca 机器上主界面进程占 3.4～5.1 GB，是全机第一，并行导出因此只能开 4～5 个进程。
Python 堆里的图像只有 0.16 GB，大头是 torch / cuDNN / cuBLAS 的 DLL 和 CUDA 上下文 ——
它们在启动时就被 import 进来（为了运行库加载顺序 + CUDA 体检），之后一直占着。

现在：启动期预加载 System32 的 MSVC 运行库（保住加载顺序）、显卡信息问 nvidia-smi、
抠像 / 调整层预览走常驻的 GPU 服务进程、ASR / AI 抠像在真正用到时才加载 torch。
这里验证：跑完整个 GUI 启动流程后，sys.modules 里没有 torch / ctranslate2 / whisperx / transformers。
"""
from __future__ import annotations

import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_HEAVY = ("torch", "ctranslate2", "whisperx", "transformers")

_SCRIPT = r'''
import os, sys
sys.path.insert(0, %(repo)r)
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["CUTORA_GPU_FX"] = "0"                      # 别真的起服务进程
from cutora.core.runtime import init_torch_runtime, cuda_warmup_status
init_torch_runtime(render_child=False)
assert "torch" not in sys.modules, "init_torch_runtime 还在 import torch"
from cutora.core import config
assert "torch" not in sys.modules, "config.detect_gpu_config 还在 import torch"
import cutora.app as A
from PySide6.QtWidgets import QApplication, QWidget
from PySide6.QtGui import QColor, QFont, QPixmap
app = QApplication(["cutora-test"])
import cutora.core.migrate_user_data as MU
MU.prompt_and_import = lambda parent=None: None
import cutora.ui.splash as SP
from cutora.ui.splash import BrandSplash
def _mk():
    pix = QPixmap(64, 36); pix.fill(QColor("#141210")); return BrandSplash(pix)
SP.make_splash = _mk
import cutora.ui.panels.fonts as FP
FP.load_embedded_fonts = lambda: True
class _Mgr:
    def get_font(self, *a, **k): return QFont()
FP.get_font_manager = lambda: _Mgr()
app.exec = lambda: 0
app.setStyle = lambda *a, **k: None
app.setFont = lambda *a, **k: None
class DummyWin(QWidget):
    def resume_project_queue(self): return None
A.run_gui_startup(app, window_factory=DummyWin)
A._log_startup_identity()
heavy = [m for m in sys.modules if m.split(".")[0] in %(heavy)r]
print("HEAVY=" + ",".join(sorted(set(x.split(".")[0] for x in heavy))))
print("WARMUP=%%s" %% (cuda_warmup_status(),))
from cutora.core.runtime import shutdown_qapplication
shutdown_qapplication()
'''


def test_gui_startup_does_not_import_torch(tmp_path):
    env = dict(os.environ)
    env["CUTORA_USER_DATA"] = str(tmp_path)
    env.pop("CUTORA_EAGER_TORCH", None)
    code = _SCRIPT % {"repo": REPO, "heavy": _HEAVY}
    proc = subprocess.run([sys.executable, "-c", code], cwd=REPO, capture_output=True, text=True,
                          timeout=240, env=env)
    out = (proc.stdout or "") + "\n" + (proc.stderr or "")
    assert proc.returncode == 0, out[-3000:]
    line = [ln for ln in out.splitlines() if ln.startswith("HEAVY=")]
    assert line, out[-2000:]
    assert line[0] == "HEAVY=", "主界面启动后不该加载这些：" + line[0]


def test_pkg_version_does_not_import():
    from cutora import app as A
    before = set(sys.modules)
    v = A._pkg_version("torch")
    assert v and v != ""
    assert "torch" not in (set(sys.modules) - before)


def test_detect_gpu_config_without_torch(monkeypatch):
    """torch 没加载时用 nvidia-smi 的 compute_cap 判断，规则和 torch 版一样；服务进程报 CUDA 不可用则回 cpu。"""
    from cutora.core import config as C
    monkeypatch.setattr(C, "_TORCH_CUDA_PROBE", None)
    monkeypatch.setitem(sys.modules, "torch", None) if False else None
    saved = sys.modules.pop("torch", None)
    try:
        monkeypatch.setattr(C, "_CC_CACHE", {"v": 120, "done": True})
        dev, ct, why = C.detect_gpu_config(verbose=False)
        assert (dev, ct) == ("cuda", "float16") and "sm_120" in why
        monkeypatch.setattr(C, "_CC_CACHE", {"v": 61, "done": True})
        assert C.detect_gpu_config(verbose=False)[:2] == ("cuda", "int8")
        monkeypatch.setattr(C, "_CC_CACHE", {"v": None, "done": True})
        assert C.detect_gpu_config(verbose=False)[0] == "cpu"
        monkeypatch.setattr(C, "_CC_CACHE", {"v": 86, "done": True})
        C.note_torch_cuda(False, "测试：torch 说没有 CUDA")
        assert C.detect_gpu_config(verbose=False)[0] == "cpu"
        C.note_torch_cuda(True, "")
        assert C.detect_gpu_config(verbose=False)[0] == "cuda"
    finally:
        if saved is not None:
            sys.modules["torch"] = saved


def test_gpu_backend_in_gui_is_remote_and_nonblocking(monkeypatch):
    """主界面里 backend()：服务还没起好 → 返回 None 但不缓存（下次再问）；起好了 → RemoteBackend。"""
    from cutora.fx import gpu as GPU
    GPU.reset()
    monkeypatch.delenv(GPU.ENV_ADDR, raising=False)
    monkeypatch.delenv(GPU.ENV_DEVICE, raising=False)
    monkeypatch.delenv(GPU.ENV_LOCAL, raising=False)
    monkeypatch.delenv("CUTORA_RENDER_CHILD", raising=False)
    monkeypatch.setattr(GPU, "enabled_by_settings", lambda: True)
    monkeypatch.setattr(GPU, "has_nvidia_gpu", lambda: True)
    calls = []
    monkeypatch.setattr(GPU, "ensure_gui_server", lambda wait=False: (calls.append(wait), "")[1])
    assert GPU.backend() is None
    assert GPU._RESOLVED is False and calls == [False]
    assert GPU.backend() is None and len(calls) == 2          # 没缓存，再问了一次
    # 服务起好了：拿到的是 RemoteBackend（这里不真连）
    monkeypatch.setattr(GPU, "ensure_gui_server", lambda wait=False: "127.0.0.1:1:deadbeef")

    class _FakeRemote:
        kind = "remote"

        def __init__(self, addr):
            self.addr = addr

        def connect(self):
            pass

        def close(self):
            pass
    monkeypatch.setattr(GPU, "RemoteBackend", _FakeRemote)
    monkeypatch.setattr(GPU, "_selftest", lambda be: True)
    be = GPU.backend()
    assert isinstance(be, _FakeRemote) and GPU._RESOLVED is True
    GPU.reset()


def test_parallel_reuses_gui_server_source():
    import inspect
    from cutora.render import parallel as P
    src = inspect.getsource(P.ParallelExportManager.start)
    assert "gui_server_addr()" in src and "复用主界面的 GPU 像素运算服务" in src


def test_mad_sample_seeks_half_a_frame_early(monkeypatch):
    """Q187b：验收抽帧 -ss 落在目标帧前半帧。t=6404/30 以前被写成 213.467（比帧 pts 晚 0.3 ms），
    ffmpeg 丢掉该帧交出下一帧 → 画面切换处 MAD 误判失败、整条重导。"""
    from cutora.render import export_verify as V
    seen = {}

    class _R:
        returncode = 1
        stdout = b""

    def fake_run(argv, **kw):
        seen["argv"] = argv
        return _R()
    monkeypatch.setattr(V, "_no_window_run", fake_run)
    monkeypatch.setattr(V, "_ffmpeg", lambda: "ffmpeg")
    t = 6404 / 30.0
    V._extract_rgb_frame("x.mp4", t, 720, 1280, fps=30.0)
    ss = float(seen["argv"][seen["argv"].index("-ss") + 1])
    assert abs(ss - (t - 1 / 60.0)) < 1e-6
    assert ss < t and ss > (6403 / 30.0)


def test_affine_tables_published_atomically_under_threads():
    """Q187b：4 条线程同时第一次要调色查表，不能有人拿到 None（以前报 np.take size 1，整帧调整图层丢掉）。"""
    import threading
    import numpy as np
    from cutora.render import grade as G
    G._AFFINE_TABLES = None
    errs = []

    def _go():
        try:
            lin, srgb = G._affine_tables()
            assert lin.shape == (256, 1, 3) and srgb.shape == (65536,)
            img = np.full((8, 8, 3), 128, np.uint8)
            aff = G._affine_of(G.normalize_grade({"exposure": 0.5}))
            out = G.apply_affine_u8(img, aff, bgr=True)
            assert out.shape == img.shape
        except Exception as e:
            errs.append(repr(e))
    ths = [threading.Thread(target=_go) for _ in range(8)]
    for t in ths:
        t.start()
    for t in ths:
        t.join(30)
    assert not errs, errs


def test_segment_export_children_get_gpu_addr_and_decode_threads(monkeypatch):
    import inspect
    from cutora.render import segment_export as SE
    src = inspect.getsource(SE.SegmentExportWorker._launch)
    assert "ENV_ADDR" in src and "EXPORT_DECODE_THREADS_ENV" in src
    from cutora.ui import main_window as MW
    msrc = inspect.getsource(MW)
    assert "len(self.batch_queue) < min(4, int(PARALLEL_EXPORT_WORKERS or 1))" in msrc
