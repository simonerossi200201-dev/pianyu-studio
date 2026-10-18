# SPDX-License-Identifier: GPL-3.0-or-later
# Extracted from the T01 monolith by tools/split_monolith.py.
# Original lines: 234-252, 255-265, 268-275, 278-278, 281-285, 305-308, 4636-4781, 4784-5246

import sys
import os
import json
import subprocess

from cutora.core import safe_json
from cutora.core.log import get_logger
import shutil
import time
import tempfile
from PySide6.QtCore import (
    Signal,
    QTimer,
    QObject,
)
try:
    from cutora.render import export_guard as _GUARD
except Exception as _e:
    _GUARD = None
    print(f"[Guard] export_guard 不可用，导出保活功能关闭（不影响导出本身）: {_e}")
from cutora.model.project_io import (
    serialize_render_task,
    deserialize_render_task,
)
import threading
from cutora.core.flags import is_render_child
from cutora.core.history import _load_app_settings, _save_app_settings
from cutora.core.proc import popen as _no_window_popen, run as _no_window_run

try:
    from cutora.audio import sfx_library as _SFX
except Exception:
    _SFX = None

_LOG = get_logger("cutora.render.parallel")


def worker_progress_payload(done, cur, total, rss_gb, pid, fps=0.0):
    """One worker's .progress JSON. fps sits next to rss_gb (BUG-41)."""
    return {
        "done": int(done),
        "cur": int(cur),
        "total": int(total),
        "rss_gb": round(float(rss_gb or 0.0), 3),
        "pid": int(pid or 0),
        "fps": round(float(fps or 0.0), 2),
    }


def _atomic_progress_write(path, payload):
    """Write ``payload`` via tmp+replace. PermissionError: retry; other errors log."""
    if not path:
        return
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as pf:
            json.dump(payload, pf)
    except Exception:
        _LOG.exception("写入进度临时文件失败: %s", path)
        return
    for _ in range(30):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            time.sleep(0.02)
        except Exception:
            _LOG.exception("替换进度文件失败: %s", path)
            return
    _LOG.error("进度文件被占用，丢掉这一帧: %s", path)


def write_worker_progress(path, done=0, cur=0, total=1, rss_gb=0.0, pid=0, fps=0.0):
    """Atomic write of a worker progress file. Used by tests and workers."""
    _atomic_progress_write(
        path, worker_progress_payload(done, cur, total, rss_gb, pid, fps)
    )


def prepare_task_audio_for_export(task):
    """导出前按设置补音效。空间立体声只认 clip.spatial_enabled。失败不影响导出。"""
    if task is None or _SFX is None:
        return task
    try:
        _SFX.prepare_task_audio_for_export(task)
    except Exception as e:
        print(f"[AudioFx] prepare skipped: {e}")
    return task

# ═══════════════════════════════════════════════════════════════════════════
# [v6.1] 并行批量导出设置
#
# 原本的批量导出是严格串行的：整条流水线（解码 → Qt 绘制 → 写 FFmpeg 管道）
# 全部挤在一个 Python 线程里，在 24 核的机器上只能用满 1 个核。
#
# 不能简单地多开几个 QThread —— 渲染路径依赖一批模块级全局缓存
# (_SE_SUB_CACHE / _IMAGE_CACHE / _DBI_VIDEO_CAPS ...)，而且
# cv2.VideoCapture 完全非线程安全。多线程会导致画面串台甚至崩溃。
#
# 正确解法是多进程：每个子进程拥有独立的全局缓存和解码器，天然隔离。
# 子进程通过 `python -m cutora --render-job <json>` 启动。
#
# 该功能默认关闭，可在「性能」菜单开启，随时可回退到原串行逻辑。
# ═══════════════════════════════════════════════════════════════════════════

# 子进程标记：为 "1" 时表示当前进程是纯渲染子进程，
# 不需要 torch / whisperx / CUDA 上下文（省下每进程约 0.5-1GB 显存）。
IS_RENDER_CHILD = is_render_child()

def _default_parallel_workers():
    """默认并行进程数：核数的 1/4，夹在 2~8 之间。

    现代 GeForce RTX 显卡驱动允许 8 路并发 NVENC 编码。
    """
    try:
        n = os.cpu_count() or 4
    except Exception:
        n = 4
    return max(2, min(8, n // 4))

def _load_parallel_settings():
    s = _load_app_settings()
    enabled = bool(s.get("parallel_export_enabled", False))
    try:
        workers = int(s.get("parallel_export_workers", 0)) or _default_parallel_workers()
        if workers == 6 and not s.get("parallel_workers_manual") and _default_parallel_workers() > 6:
            workers = _default_parallel_workers()
    except Exception:
        workers = _default_parallel_workers()
    return enabled, max(1, min(12, workers))

PARALLEL_EXPORT_ENABLED, PARALLEL_EXPORT_WORKERS = _load_parallel_settings()

def _save_parallel_settings(enabled, workers):
    s = _load_app_settings()
    s["parallel_export_enabled"] = bool(enabled)
    s["parallel_export_workers"] = int(workers)
    _save_app_settings(s)

# 每个渲染子进程给 FFmpeg 的 -threads 上限。0 = 交给 FFmpeg 自己决定（原行为）。
# 并行导出时由父进程按「(CPU 核数 - 2) / 进程数」算出来写进 job 文件，
# 避免 N 个子进程各自开满线程池，把主界面的 CPU 时间片抢光。
EXPORT_ENC_THREADS = 0

def _load_and_validate_job(job_path):
    """Load a render-job JSON, check schema, and keep sidecar paths in the job dir."""
    job_path = os.path.abspath(job_path)
    if not os.path.isfile(job_path):
        raise ValueError("job file not found")
    job_dir = os.path.dirname(job_path)
    with open(job_path, "r", encoding="utf-8") as f:
        job = safe_json.load(f, reject_duplicate_keys=True)
    if not isinstance(job, dict):
        raise ValueError("job must be a JSON object")
    items = job.get("items")
    if not isinstance(items, list) or not items:
        raise ValueError("job.items must be a non-empty list")
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("job item must be an object")
        if "task" not in item or "output_path" not in item:
            raise ValueError("job item needs task and output_path")
    for key in ("progress_path", "result_path"):
        rel = job.get(key)
        if not rel:
            continue
        abs_p = os.path.abspath(rel)
        if os.path.dirname(abs_p) != job_dir:
            raise ValueError("%s must stay in the parent job directory" % key)
    return job

# ───────────────────────────────────────────────────────────────────────────
#  渲染子进程入口
#  启动方式： python -m cutora --render-job <job.json>
#  环境变量： CUTORA_RENDER_CHILD=1  （由父进程设置，用于跳过 torch/CUDA）
# ───────────────────────────────────────────────────────────────────────────

def run_render_job(job_path):
    """在子进程中渲染**一批**视频。返回进程退出码。

    这里刻意复用与串行导出**完全相同**的 RenderWorker，只是不调用 .start()
    而是直接调用 .run() 在当前线程同步执行。渲染逻辑一行都没有分叉，
    所以并行导出与串行导出的输出结果必然逐字节一致。

    为什么一个子进程要处理一批、而不是一个视频？
        进程启动本身有固定开销 —— 导入 PySide6 加上注册 232 个内嵌字体，
        实测约 0.5~1 秒。如果一个视频起一个进程，这笔开销要乘以视频数量；
        实测在短视频上甚至会把并行的收益全部吃掉（6 个 4 秒视频反而更慢）。
        改成一个进程包干一批之后，这笔开销总共只付「进程数」次。

    单个视频失败不会中断整批：记录下来继续做下一个，最后把失败清单写进
    result 文件，交给父进程用串行方式重试。
    """
    try:
        import cv2
        cv2.setNumThreads(max(1, int(os.environ.get("OMP_NUM_THREADS") or 2)))
    except Exception:
        pass
    from cutora.render.render_worker import RenderWorker
    try:
        job = _load_and_validate_job(job_path)
    except Exception as e:
        print(f"[RenderJob] 无法读取任务文件: {e}", file=sys.stderr)
        return 2

    progress_path = job.get("progress_path")
    result_path = job.get("result_path")
    items = job.get("items") or []
    total = len(items)
    job_dir = os.path.dirname(os.path.abspath(job_path))
    os.environ["CUTORA_JOB_DIR"] = job_dir

    # 任务文件为空或格式不对时必须以非零码退出。
    # 否则父进程会以为「成功渲染了 0 个视频」，这批视频就被静默丢掉了。
    if total == 0:
        print("[RenderJob] 任务文件中没有任何待渲染项", file=sys.stderr)
        return 8
    mode = job.get("mode", "quality")
    use_gpu = bool(job.get("use_gpu", False))
    export_mode = job.get("export_mode", "compressed")
    try:
        from cutora.core.runtime import mimalloc_status
        from cutora.render.render_worker import export_decode_threads
        print("[RenderJob] %s；每个解码器 %d 线程" % (mimalloc_status(), export_decode_threads()), flush=True)
    except Exception:
        pass

    # [v6.2] 编码线程上限。原来 FFmpeg 一律 -threads 0（用满所有核），
    # 6 个子进程在 24 核机器上会开出上百个编码线程互相抢核，主界面被饿死
    # → Windows 判定「未响应」→ 把窗口换成白板 ghost。
    global EXPORT_ENC_THREADS
    try:
        EXPORT_ENC_THREADS = int(job.get("enc_threads", 0) or 0)
    except Exception:
        EXPORT_ENC_THREADS = 0

    # 降级标记：每个视频开始前读一次（尚未开编的下一条可以改 CPU）。
    # 已经开编的当前文件由 RenderWorker 每 30 帧检查，收到则干净收尾并
    # 标记「需 CPU 重做」，绝不在同一个文件里换编码器。
    degraded_once = False

    state = {"done": 0, "cur": 0}

    def _self_rss_gb():
        # BUG-41：父进程手里的 pid 是 venv 的 python.exe 启动壳（峰值 4 MB），
        # 真正的解释器是它的子进程。只有子进程自己知道自己的 RSS，
        # 所以由这里量了写进进度文件，父进程照抄。
        try:
            if _GUARD is not None:
                return float(_GUARD.process_rss_gb(os.getpid()) or 0.0)
        except Exception:
            pass
        return 0.0

    def _write_progress():
        _atomic_progress_write(progress_path, {
            "done": state["done"], "cur": state["cur"], "total": total,
            "rss_gb": round(_self_rss_gb(), 3), "pid": os.getpid(),
            "fps": round(float(state.get("fps") or 0.0), 2),
        })

    failed = []
    _write_progress()

    for n, item in enumerate(items):
        gidx = item.get("index", n)
        out_path = item.get("output_path")
        state["cur"] = 0
        _write_progress()
        # [v6.2] 每个视频开始前检查一次降级指令（一次极廉价的 os.path.exists）
        if use_gpu and _GUARD is not None and not degraded_once:
            try:
                _dg = _GUARD.read_degrade(job_dir)
            except Exception:
                _dg = None
            if _dg:
                degraded_once = True
                use_gpu = False
                print(f"[RenderJob] 收到降级指令，本进程尚未开编的后续视频改用 CPU："
                      f"{_dg.get('reason', '')}", flush=True)
                # 编码线程也收一收，把 CPU 让一点给主界面
                if EXPORT_ENC_THREADS:
                    EXPORT_ENC_THREADS = max(2, EXPORT_ENC_THREADS - 1)

        try:
            task = deserialize_render_task(item.get("task"))
            # Q160 分段并行：只画 [s, e) 这一段、只出视频；导出配置 id 由父进程带来（序列化会丢 _ 开头的键）
            frame_range = item.get("frame_range")
            video_only = bool(item.get("video_only"))
            if item.get("export_profile_id") and isinstance(task, dict):
                task["_export_profile_id"] = item.get("export_profile_id")
            worker = RenderWorker(task, out_path, mode, use_gpu, export_logs=True,
                                  frame_range=frame_range if frame_range else None,
                                  video_only=video_only)
            worker.export_mode = export_mode

            st = {"error": None, "done": False}

            def _on_pct(p):
                state["cur"] = int(p)
                _write_progress()

            def _on_stats(fps, _done, _total):
                try:
                    state["fps"] = float(fps)
                except Exception:
                    _LOG.exception("worker fps 字段无法解析")
                    state["fps"] = 0.0
                _write_progress()

            worker.progress.connect(_on_pct)
            worker.export_stats.connect(_on_stats)
            worker.error.connect(lambda msg: st.update(error=msg))
            worker.finished.connect(lambda: st.update(done=True))

            worker.run()   # 同步执行，不开新线程

            if st["error"]:
                print(f"[RenderJob] #{gidx} 渲染失败: {st['error']}", file=sys.stderr)
                failed.append(gidx)
            elif not st["done"]:
                print(f"[RenderJob] #{gidx} 未正常完成", file=sys.stderr)
                failed.append(gidx)
            elif not (out_path and os.path.exists(out_path)):
                print(f"[RenderJob] #{gidx} 输出文件不存在", file=sys.stderr)
                failed.append(gidx)
            elif video_only:
                print(f"[RenderJob] #{gidx} 分段完成: {out_path}")     # 验收由父进程拼接后统一做
            else:
                from cutora.render.export_params import verify_window_for_task
                from cutora.render.export_verify import verify_export
                expect_dur, expect_frames, expect_fps = verify_window_for_task(task)
                ok, why = verify_export(
                    out_path,
                    expected_duration=expect_dur or None,
                    expected_frames=expect_frames or None,
                    fps=expect_fps if expect_frames else None)
                if not ok:
                    print(f"[RenderJob] #{gidx} 验收失败: {'; '.join(why)}",
                          file=sys.stderr)
                    failed.append(gidx)
                else:
                    print(f"[RenderJob] #{gidx} 完成: {out_path}")
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"[RenderJob] #{gidx} 异常: {e}", file=sys.stderr)
            failed.append(gidx)

        state["done"] = n + 1
        state["cur"] = 0
        _write_progress()

    # 写出结果清单。父进程读不到这个文件就认为整个子进程崩了，
    # 会把这一批全部退回串行重试 —— 宁可慢，也绝不丢视频。
    if result_path:
        try:
            tmp = result_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as rf:
                json.dump({"failed": failed,
                           "completed": [it.get("index") for it in items]}, rf)
            os.replace(tmp, result_path)
        except Exception as e:
            print(f"[RenderJob] 写入结果文件失败: {e}", file=sys.stderr)
            return 7

    return 0 if not failed else 3

# ───────────────────────────────────────────────────────────────────────────
#  父进程侧：并行渲染调度器
# ───────────────────────────────────────────────────────────────────────────

def job_weight(task) -> float:
    """一个导出任务的工作量估计：时间轴时长（秒）× 画布像素 / 1e6。读不到 → 0。"""
    try:
        tl = task.get("timeline") if isinstance(task, dict) else None
        if tl is None:
            return 0.0
        if isinstance(tl, dict):
            w, h = int(tl.get("width") or 0), int(tl.get("height") or 0)
            tracks = tl.get("tracks") or []
        else:
            w, h = int(getattr(tl, "width", 0) or 0), int(getattr(tl, "height", 0) or 0)
            tracks = getattr(tl, "tracks", None) or []
        end = 0.0
        for tr in tracks:
            clips = (tr.get("clips") if isinstance(tr, dict) else getattr(tr, "clips", None)) or []
            for c in clips:
                g = (lambda k, d=0.0: float((c.get(k, d) if isinstance(c, dict) else getattr(c, k, d)) or 0.0))
                dur = g("effective_duration")
                if dur <= 0:
                    dur = max(0.0, g("out_t") - g("in_t"))
                end = max(end, g("start_t") + dur)
        if end <= 0:
            return 0.0
        px = (w * h) if (w > 0 and h > 0) else 1920 * 1080
        return end * px / 1e6
    except Exception:
        return 0.0


class ParallelExportManager(QObject):
    """同时运行多个渲染子进程，并汇总进度。

    v6.2 的三处结构性改动（都是为了「主界面不要白板」）：

      1. **所有阻塞操作搬离 Qt 主线程。**
         v6.1 把 `proc.poll()` + 读 N 个进度文件放在主线程的 QTimer 里。
         并行导出时磁盘被 6 路 FFmpeg 写满，一次 open() 可能阻塞好几秒，
         主线程每 400ms 就去踩一次这个雷 —— 累积起来就是「未响应」。
         现在后台线程负责起进程 / 轮询 / 写心跳，主线程只读一个内存快照。

      2. **错峰启动 + 降低子进程优先级。**
         6 个子进程同时 import PySide6 并注册 232 个字体，是整场导出里
         内存和 CPU 压力最大的一瞬间，恰好也是白板最容易发生的时刻。
         现在逐个错峰拉起，并且全部用 BELOW_NORMAL 优先级 ——
         渲染慢一点点没关系，但主界面必须抢得到 CPU 把自己画出来。

      3. **进度不再只存在于主窗口里。**
         后台线程持续写心跳文件和 state.json，另一个独立进程的保活窗口
         读它们显示进度。主界面白板与否，用户都能看到真实进度。

    安全性设计（v6.1 的保证全部保留）：
      · 子进程只是把串行路径搬到另一个进程里跑，渲染代码零分叉
      · 任何子进程失败都会被记录下来，交回主程序用原来的串行方式重试
      · 进度通过临时文件传递，不用管道
      · 所有 Qt 信号仍然只在主线程的 QTimer 回调里发出
    """

    progress = Signal(int)        # 总体进度 0-100
    export_stats = Signal(float, int, int)  # 各 worker fps 之和, 已完成项, 总数
    stage = Signal(int, int)      # (已完成数, 总数)
    finished = Signal(list)       # 失败任务在 jobs 列表中的下标 [int, ...]
    degraded = Signal(str)        # [v6.2] 触发降级时的说明文字

    # 上一次导出留下的保活窗口进程（新一轮导出前先收掉，避免窗口越开越多）
    _PREV_GUARD = None

    def __init__(self, jobs, mode, use_gpu, export_mode, workers, parent=None,
                 gpu_workers=None, enc_threads=0, spawn_stagger=1.2):
        super().__init__(parent)
        self._jobs = list(jobs)                 # [(task, out_path), ...]
        self._mode = mode
        self._use_gpu = bool(use_gpu)
        self._export_mode = export_mode
        self._workers = max(1, min(int(workers), len(self._jobs)))
        # 允许使用 NVENC 的进程数。消费级显卡的并发编码会话数有限，
        # 超了 FFmpeg 会直接开不出编码器；显存被吃干净还会让桌面合成器
        # 分配不到窗口表面 —— 那才是真正意义上的「白板」。
        if not self._use_gpu:
            self._gpu_workers = 0
        elif gpu_workers is None:
            self._gpu_workers = self._workers
        else:
            self._gpu_workers = max(0, min(int(gpu_workers), self._workers))
        self._enc_threads = int(enc_threads or 0)
        self._spawn_stagger = max(0.0, float(spawn_stagger))

        self._lock = threading.RLock()
        self._running = {}                      # wid → {proc, paths, indices}
        self._finished_infos = []
        self._failed = []
        self._done_items = 0
        self._tmpdir = None
        self._stopped = False
        self._launch_done = False
        self._chunks = self._partition()
        self._chunk_paths = {}                  # wid → dict(paths)
        self._snapshot = {"acc": 0.0, "done": 0, "all_done": False,
                          "latest": "", "degraded": ""}
        self._bg = None
        self._hb = None
        self._guard_proc = None
        self._watchdog = None
        self._degrade_reason = ""
        # 主界面存活探针的宿主（MainWindow）。只用普通属性读取，
        # 不调用任何 Qt 方法，所以后台线程读它是安全的。
        self._ui_owner = parent
        self._out_dirs = []
        self._canvas_wh = (1080, 1920)
        for _t, _p in self._jobs:
            d = os.path.dirname(_p) if _p else ""
            if d and d not in self._out_dirs:
                self._out_dirs.append(d)
        try:
            # Q180：实测 RSS 记在这批最多的画布尺寸名下（以前记在第 1 个视频的尺寸下）
            from cutora.render.export_guard import dominant_canvas
            self._canvas_wh = dominant_canvas([_t for _t, _p in self._jobs]) or (1080, 1920)
        except Exception:
            pass

        self._timer = QTimer(self)
        self._timer.setInterval(700)
        self._timer.timeout.connect(self._poll)

    def _partition(self):
        """把任务分给各个 worker，按「时长 × 画布像素」配平。

        Q184：以前是轮询（第 i 个给第 i % n 个 worker），视频长短差得多时尾巴很长 ——
        Luca 的 6 个视频 / 4 个进程：两个 worker 各拿两个，最后一分多钟只剩一个进程在跑，
        整体帧率从 158 掉到 73。现在按估算的工作量从大到小依次放进当前最轻的 worker
        （LPT），每个 worker 内部仍按原顺序。估不出工作量的视频按平均值算。
        """
        n = max(1, self._workers)
        weights = [job_weight(t) for t, _p in self._jobs]
        known = [w for w in weights if w > 0]
        fill = (sum(known) / len(known)) if known else 1.0
        weights = [w if w > 0 else fill for w in weights]
        order = sorted(range(len(self._jobs)), key=lambda i: (-weights[i], i))
        chunks = [[] for _ in range(n)]
        load = [0.0] * n
        for i in order:
            k = min(range(n), key=lambda j: (load[j], j))
            chunks[k].append(i)
            load[k] += weights[i]
        return [sorted(c) for c in chunks if c]

    # ── 生命周期 ──────────────────────────────────────────────────────────

    def start(self):
        if not self._jobs:
            self.finished.emit([])
            return
        try:
            self._tmpdir = tempfile.mkdtemp(prefix="cutora_parallel_")
        except Exception as e:
            print(f"[Parallel] 无法创建临时目录，退回串行导出: {e}")
            self.finished.emit(list(range(len(self._jobs))))
            return

        # Q181：GPU 像素运算服务进程（一个 CUDA 上下文服务全部渲染子进程）。起不来就全程 CPU，导出不受影响。
        self._gpu_proc, self._gpu_addr = None, ""
        try:
            from cutora.fx import gpu as _gpu
            # Q187：主界面已经常驻一个 GPU 服务（预览在用）→ 直接给渲染子进程用，不再起第二个 CUDA 上下文
            addr = _gpu.gui_server_addr()
            if addr:
                try:
                    _be = _gpu.RemoteBackend(addr)
                    _be.connect()
                    if not _be.ping():
                        addr = ""
                    _be.close()
                except Exception:
                    addr = ""
            if addr:
                self._gpu_proc, self._gpu_addr = None, addr
                print("[Parallel] 复用主界面的 GPU 像素运算服务 %s" % addr.rsplit(":", 1)[0], flush=True)
            else:
                self._gpu_proc, self._gpu_addr = _gpu.start_server(workdir=self._tmpdir)
            if self._gpu_addr and self._gpu_proc is not None:
                print("[Parallel] GPU 像素运算服务已启动 (pid=%s)" % getattr(self._gpu_proc, "pid", "?"), flush=True)
            elif not self._gpu_addr:
                print("[Parallel] GPU 像素运算未启用（没有 CUDA / 已关闭 / 服务没起来，原因见上方 [GPU] 行），走 CPU", flush=True)
        except Exception:
            import traceback
            print("[Parallel] GPU 像素运算服务启动失败（走 CPU）:\n" + traceback.format_exc(), flush=True)
            self._gpu_proc, self._gpu_addr = None, ""

        # ① 先把所有 job 文件写好。这一步要序列化全部任务，是主线程上唯一
        #    比较重的动作，放在导出开始前一次做完，之后主线程就彻底闲下来。
        try:
            for wid, indices in enumerate(self._chunks):
                self._write_job_file(wid, indices)
        except Exception as e:
            print(f"[Parallel] 任务文件写入失败，退回串行导出: {e}")
            self._cleanup_tmp()
            self.finished.emit(list(range(len(self._jobs))))
            return

        print(f"[Parallel] 启动并行导出：{len(self._jobs)} 个视频 / {len(self._chunks)} 个进程 "
              f"(每进程约 {len(self._jobs) // max(1, len(self._chunks))} 个"
              f"，GPU 编码 {self._gpu_workers} 路，每进程 FFmpeg 线程上限 "
              f"{self._enc_threads or '不限'})")

        # ② 心跳文件 + 保活进度窗 + 资源看门狗
        self._start_guard()

        # ③ 后台线程：错峰起进程 → 持续轮询 → 写心跳
        self._bg = threading.Thread(target=self._bg_loop, daemon=True,
                                    name="CUTORAParallelPoller")
        self._bg.start()
        self._timer.start()

    def _start_guard(self):
        if _GUARD is None:
            return
        try:
            # 上一轮的保活窗口收掉
            prev = ParallelExportManager._PREV_GUARD
            if prev is not None and prev.poll() is None:
                try:
                    prev.terminate()
                except Exception:
                    pass
            # state.json 刻意**不放在临时目录**里：临时目录导出结束就会被删掉，
            # 而保活窗口在导出完成之后还要继续显示结果。
            from cutora.core.paths import resolve_user_data
            state_dir = resolve_user_data()
            os.makedirs(state_dir, exist_ok=True)
            state_path = os.path.join(state_dir, _GUARD.STATE_NAME)
            self._hb = _GUARD.HeartbeatWriter(
                state_path, self._out_dirs, len(self._jobs),
                len(self._chunks), self._gpu_workers, self._tmpdir)
            self._hb.update(0, status="running")
            # 主进程提优先级 + 阻止系统休眠（子进程是 BELOW_NORMAL，中间隔两档）
            _GUARD.boost_main_process(True)
            _GUARD.keep_system_awake(True)
            self._guard_proc = _GUARD.launch_guard_window(state_path)
            ParallelExportManager._PREV_GUARD = self._guard_proc
            self._watchdog = _GUARD.ResourceWatchdog(
                self._tmpdir, self._gpu_workers > 0,
                on_degrade=self._on_watchdog_degrade)
            self._watchdog.start()
        except Exception as e:
            print(f"[Parallel] 保活组件启动失败（不影响导出）: {e}")

    def _on_watchdog_degrade(self, reason):
        """看门狗线程回调 —— 注意：这里在**非主线程**，绝不能碰任何 Qt 控件。"""
        self._degrade_reason = reason
        n = 0
        with self._lock:
            n = len(self._running)
            self._snapshot["degraded"] = reason
            low = str(reason or "")
            if "改串行" in low:
                self._snapshot["serial_rest"] = True
            if "暂停派发" in low:
                self._snapshot["pause_spawn"] = True
        print("[Guard] 已请求降级，但 %d 个视频正在编码中，本次不中途换编码器；"
              "收尾后该片改 CPU 重做 — %s" % (n, reason), flush=True)

    def stop(self):
        self._stopped = True
        with self._lock:
            running = list(self._running.items())
            self._running.clear()
        for wid, info in running:
            proc = info.get("proc")
            try:
                if proc and proc.poll() is None:
                    proc.terminate()
            except Exception:
                pass
            try:
                info["log_file"].close()
            except Exception:
                pass
        self._timer.stop()
        self._stop_guard(status="stopped")
        self._cleanup_tmp()

    def _stop_gpu_server(self):
        proc = getattr(self, "_gpu_proc", None)
        self._gpu_proc, self._gpu_addr = None, ""
        if proc is None:
            return
        try:
            if proc.poll() is None:
                proc.kill()
        except Exception:
            pass

    def _stop_guard(self, status="finished", ok=0, failed=0, latest=""):
        self._stop_gpu_server()
        if _GUARD is not None:
            try:
                _GUARD.boost_main_process(False)
                _GUARD.keep_system_awake(False)
            except Exception:
                pass
        try:
            if self._watchdog is not None:
                self._watchdog.stop()
                self._watchdog = None
        except Exception:
            pass
        try:
            if self._hb is not None:
                if status == "finished":
                    self._hb.finish(ok, failed, latest=latest,
                                    degraded=self._degrade_reason)
                else:
                    self._hb.update(ok, latest=latest, status=status,
                                    degraded=self._degrade_reason)
                    self._hb.cleanup_locks()
                self._hb = None
        except Exception:
            pass

    # ── 内部 ─────────────────────────────────────────────────────────────

    def _script_path(self):
        """Override in tests to point at a fake child script. Production uses -m cutora."""
        return None

    def _child_cmd(self, job_path):
        script = self._script_path()
        if isinstance(script, str) and script.endswith(".py"):
            return [sys.executable, "-u", script, "--render-job", job_path], os.path.dirname(script)
        from cutora.core.paths import REPO_ROOT as _T01_REPO_ROOT
        return [sys.executable, "-u", "-m", "cutora", "--render-job", job_path], _T01_REPO_ROOT

    def _write_job_file(self, wid, indices):
        """主线程：把这一批任务序列化到 w{wid}.json。"""
        job_path = os.path.join(self._tmpdir, f"w{wid}.json")
        progress_path = os.path.join(self._tmpdir, f"w{wid}.progress")
        result_path = os.path.join(self._tmpdir, f"w{wid}.result")
        log_path = os.path.join(self._tmpdir, f"w{wid}.log")

        payload = {
            "items": [{"index": i,
                       "task": serialize_render_task(prepare_task_audio_for_export(self._jobs[i][0])),
                       "output_path": self._jobs[i][1]} for i in indices],
            "mode": self._mode,
            # 只有前 N 个 worker 拿得到 GPU 编码名额，其余走 CPU。
            "use_gpu": bool(wid < self._gpu_workers),
            "export_mode": self._export_mode,
            "enc_threads": self._enc_threads,
            "progress_path": progress_path,
            "result_path": result_path,
        }
        with open(job_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)

        self._chunk_paths[wid] = {
            "job_path": job_path, "progress_path": progress_path,
            "result_path": result_path, "log_path": log_path,
            "indices": list(indices), "use_gpu": bool(wid < self._gpu_workers),
        }

    def _launch(self, wid):
        """后台线程：真正把子进程拉起来。"""
        p = self._chunk_paths[wid]
        env = dict(os.environ)
        from cutora.core.flags import apply_render_child
        apply_render_child(env)
        # 让每个子进程的 OpenCV / BLAS 只用少量线程，
        # 避免 N 个进程各开满线程互相抢核，反而更慢。
        n_work = max(1, len(self._chunks))
        cpu = os.cpu_count() or 8
        per_proc_threads = max(1, cpu // n_work // 2)
        env["OMP_NUM_THREADS"] = str(per_proc_threads)
        env["MKL_NUM_THREADS"] = str(per_proc_threads)
        env["OPENBLAS_NUM_THREADS"] = str(per_proc_threads)
        env["NUMEXPR_NUM_THREADS"] = str(per_proc_threads)
        # Q11 overlay threads default to min(CPU, 8) per process. N parallel
        # jobs would then open N×8 painters. Split the cap across jobs.
        from cutora.render.compose_parallel import (
            COMPOSE_WORKERS_ENV,
            COMPOSE_WORKERS_MAX,
            PARALLEL_JOB_COMPOSE_MAX,
        )
        env[COMPOSE_WORKERS_ENV] = str(max(1, min(COMPOSE_WORKERS_MAX, PARALLEL_JOB_COMPOSE_MAX,
                                                  cpu // n_work)))
        # Q180：每个解码器的线程数。不设的话 FFmpeg 每个源开 16 个解码线程，6 个进程 × 3 个源就是近 300 个
        from cutora.render.render_worker import EXPORT_DECODE_THREADS_ENV
        env[EXPORT_DECODE_THREADS_ENV] = str(max(2, min(4, cpu // n_work // 3)))
        # Q181：GPU 像素运算服务地址（没有就不设 → 子进程走 CPU）
        from cutora.fx.gpu import ENV_ADDR as _GPU_ADDR_ENV
        env.pop(_GPU_ADDR_ENV, None)
        if getattr(self, "_gpu_addr", ""):
            env[_GPU_ADDR_ENV] = self._gpu_addr
        # 子进程只做渲染，禁止它们碰 CUDA，省下每进程 0.5~1GB 显存。
        if not p["use_gpu"]:
            env["CUDA_VISIBLE_DEVICES"] = ""

        creationflags = 0
        if os.name == "nt":
            # BELOW_NORMAL：渲染慢几个百分点无所谓，但主界面必须抢得到 CPU
            # 把自己画出来 —— 画不出来就会被 Windows 换成白板。
            creationflags = (getattr(subprocess, "CREATE_NO_WINDOW", 0)
                             | getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0))

        log_file = open(p["log_path"], "w", encoding="utf-8", errors="ignore")
        cmd, child_cwd = self._child_cmd(p["job_path"])
        proc = _no_window_popen(
            cmd,
            cwd=child_cwd,
            env=env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            creationflags=creationflags,
        )
        info = {"proc": proc, "progress_path": p["progress_path"],
                "result_path": p["result_path"], "log_path": p["log_path"],
                "log_file": log_file, "indices": list(p["indices"]),
                "partial": 0.0}
        with self._lock:
            self._running[wid] = info
            if self._hb is not None:
                self._hb.pids = [i["proc"].pid for i in self._running.values()]
        print(f"[Parallel] worker {wid} 已启动 (pid={proc.pid})，负责 {len(p['indices'])} 个视频"
              f"，编码：{'GPU/NVENC' if p['use_gpu'] else 'CPU/libx264'}")

    # 兼容旧调用点（自测脚本等）
    def _spawn(self, wid, indices):
        self._write_job_file(wid, indices)
        self._launch(wid)

    def _read_progress_dict(self, path):
        try:
            if _GUARD is not None:
                d = _GUARD.read_json_allow_replace(path)
            else:
                with open(path, "r", encoding="utf-8") as f:
                    d = json.load(f)
            return d if isinstance(d, dict) else None
        except Exception:
            return None

    def _read_progress(self, path):
        """读取子进程写的进度（已完成个数 + 当前这个的百分比）。"""
        d = self._read_progress_dict(path)
        if not d:
            return None
        try:
            return float(d.get("done", 0)) + float(d.get("cur", 0)) / 100.0
        except Exception:
            return None

    # ── 后台线程：起进程 + 轮询 + 写心跳 ───────────────────────────────────

    def _bg_loop(self):
        # ① 错峰启动。同时拉起 6 个进程会在同一瞬间产生 6 份
        #    「import PySide6 + 注册 232 个字体 + 建解码器」的峰值负载，
        #    这正是主界面最容易被挤成白板的时刻。隔开一点就平稳很多。
        #    可用内存 < 2.0 GB：暂停派发（挂起，不是继续跑）。
        #    可用内存 < 1.0 GB：还没启动的任务改串行。
        for wid in range(len(self._chunks)):
            if self._stopped:
                break
            while not self._stopped:
                avail = 99.0
                serial_rest = False
                try:
                    if _GUARD is not None:
                        avail = float(_GUARD.system_memory_gb()[1] or 0.0)
                except Exception:
                    avail = 99.0
                with self._lock:
                    serial_rest = bool(self._snapshot.get("serial_rest"))
                    pause = bool(self._snapshot.get("pause_spawn"))
                if serial_rest or (0 < avail < 1.0):
                    rest = list(range(wid, len(self._chunks)))
                    print("[Guard] 可用内存 %.2f GB < 1.0，未启动的 %d 个 worker 改串行"
                          % (avail, len(rest)), flush=True)
                    with self._lock:
                        for r in rest:
                            self._failed.extend(self._chunks[r])
                            self._done_items += len(self._chunks[r])
                    wid = len(self._chunks)
                    break
                if pause or (0 < avail < 2.0):
                    print("[Guard] 可用内存 %.2f GB < 2.0，暂停派发新 worker"
                          % avail, flush=True)
                    with self._lock:
                        self._snapshot["degraded"] = (
                            "可用内存 %.2f GB，已暂停派发新 worker" % avail)
                    time.sleep(1.0)
                    continue
                break
            if wid >= len(self._chunks) or self._stopped:
                break
            try:
                self._launch(wid)
            except Exception as e:
                print(f"[Parallel] worker {wid} 启动失败: {e}")
                with self._lock:
                    self._failed.extend(self._chunks[wid])
                    self._done_items += len(self._chunks[wid])
            if wid < len(self._chunks) - 1 and self._spawn_stagger > 0:
                time.sleep(self._spawn_stagger)
        self._launch_done = True

        # ② 持续轮询
        while not self._stopped:
            try:
                if self._honor_stop_flag():
                    break
                self._sample()
            except Exception as e:
                print(f"[Parallel] 轮询异常（已忽略）: {e}")
            with self._lock:
                all_done = self._snapshot.get("all_done")
            if all_done:
                break
            time.sleep(0.4)

    def _shed_if_starving(self):
        """可用内存掉到 SHED_RAM_GB 以下就杀最晚启动的 worker（它的视频退回串行）。

        暂停派发救不了已经在跑的进程；系统一旦开始换页，桌面就黑。
        每 6 秒最多杀一个，杀到可用内存回到线上为止。
        """
        if _GUARD is None:
            return False
        try:
            avail = float(_GUARD.system_memory_gb()[1] or 0.0)
        except Exception:
            return False
        shed_gb = float(getattr(_GUARD, "SHED_RAM_GB", 1.5))
        if not (0 < avail < shed_gb):
            return False
        now = time.time()
        if now - float(getattr(self, "_last_shed_t", 0.0) or 0.0) < 6.0:
            return False
        with self._lock:
            if len(self._running) <= 1:
                return False
            wid = max(self._running)
            info = self._running.get(wid)
        proc = info.get("proc") if info else None
        pid = getattr(proc, "pid", None)
        if not pid:
            return False
        self._last_shed_t = now
        msg = ("可用内存仅剩 %.2f GB，已停掉最晚启动的 worker %d（%d 个视频改串行重做），"
               "免得系统换页黑屏" % (avail, wid, len(info.get("indices") or [])))
        print("[Guard] " + msg, flush=True)
        with self._lock:
            self._snapshot["degraded"] = msg
            self._snapshot["pause_spawn"] = True
        self._degrade_reason = msg
        try:
            _GUARD.terminate_pids([pid])
        except Exception:
            pass
        return True

    def _sample(self):
        """后台线程：读进度文件、回收结束的 worker、更新快照与心跳。"""
        try:
            self._shed_if_starving()
        except Exception as e:
            print(f"[Parallel] 甩负载检查异常（已忽略）: {e}")
        with self._lock:
            items = list(self._running.items())

        for wid, info in items:
            proc = info["proc"]
            rc = proc.poll()

            pd = self._read_progress_dict(info["progress_path"])
            if pd:
                try:
                    p = float(pd.get("done", 0)) + float(pd.get("cur", 0)) / 100.0
                    info["partial"] = min(p, len(info["indices"]))
                    # Q180：真正写完（混音 + 验收都过了）的个数；partial 只是进度（含正在画的那条）
                    info["done_n"] = min(int(pd.get("done", 0) or 0), len(info["indices"]))
                except Exception:
                    pass
                # BUG-41：以子进程自报的 RSS 为准（父进程的 pid 是 4 MB 的启动壳）
                try:
                    rss_child = float(pd.get("rss_gb") or 0.0)
                    if rss_child > 0:
                        info["peak_rss"] = max(float(info.get("peak_rss") or 0), rss_child)
                except Exception:
                    pass
                try:
                    fps_child = float(pd.get("fps") or 0.0)
                    if fps_child > 0:
                        info["fps"] = fps_child
                except Exception:
                    _LOG.exception("读取 worker fps 失败")

            if rc is None:
                try:
                    if _GUARD is not None and info.get("proc") is not None:
                        rss = _GUARD.process_rss_gb(info["proc"].pid)
                        if rss > 0:
                            info["peak_rss"] = max(float(info.get("peak_rss") or 0), rss)
                except Exception:
                    pass
                continue

            # 该 worker 已结束
            try:
                info["log_file"].close()
            except Exception:
                pass
            with self._lock:
                self._running.pop(wid, None)
            info["partial"] = len(info["indices"])
            info["done_n"] = len(info["indices"])
            self._done_items += len(info["indices"])

            failed_here = None
            try:
                with open(info["result_path"], "r", encoding="utf-8") as rf:
                    failed_here = json.load(rf).get("failed", [])
            except Exception:
                failed_here = None

            if failed_here is None:
                # 结果文件都没写出来 → 子进程整个崩了。
                # 保守起见把它负责的全部退回串行重试，宁可慢也不丢视频。
                tail = ""
                try:
                    with open(info["log_path"], "r", encoding="utf-8", errors="ignore") as lf:
                        tail = lf.read()[-1500:]
                except Exception:
                    pass
                print(f"[Parallel] ✗ worker {wid} 异常退出 (code={rc})，"
                      f"其负责的 {len(info['indices'])} 个视频将改用串行方式重试。\n{tail}")
                self._failed.extend(info["indices"])
            else:
                if failed_here:
                    print(f"[Parallel] worker {wid} 完成，其中 {len(failed_here)} 个失败，将串行重试")
                    self._failed.extend(failed_here)
                else:
                    print(f"[Parallel] ✓ worker {wid} 全部完成 ({len(info['indices'])} 个)")
            self._finished_infos.append(info)
            try:
                if _GUARD is not None:
                    peak = float(info.get("peak_rss") or 0)
                    if peak <= 0 and info.get("proc") is not None:
                        peak = _GUARD.process_rss_gb(info["proc"].pid)
                    if peak > 0:
                        canvas = getattr(self, "_canvas_wh", None)
                        _GUARD.record_worker_rss(
                            peak, canvas_wh=canvas,
                            pid=getattr(info.get("proc"), "pid", None))
            except Exception:
                pass

        # ── 汇总 ──────────────────────────────────────────────────────
        with self._lock:
            acc = sum(i["partial"] for i in self._running.values()) \
                + sum(i["partial"] for i in self._finished_infos)
            completed = sum(int(i.get("done_n") or 0) for i in self._running.values()) \
                + sum(int(i.get("done_n") or 0) for i in self._finished_infos)
            n_running = len(self._running)
            total_fps = 0.0
            for i in self._running.values():
                try:
                    total_fps += float(i.get("fps") or 0.0)
                except Exception:
                    _LOG.exception("汇总 worker fps 失败")
            self._snapshot["acc"] = acc
            # Q180：「已完成」只数真正写完的文件（以前是各进程进度的和取整：4 个进程各画到 80% 就显示「已完成 3 个」，
            # 停下来一个成品都没有）
            self._snapshot["done"] = completed
            self._snapshot["fps"] = total_fps
            if self._launch_done and n_running == 0:
                self._snapshot["all_done"] = True
            if self._degrade_reason:
                self._snapshot["degraded"] = self._degrade_reason

        # ── 心跳（这是主界面白板时用户唯一的信息来源） ──────────────────
        if self._hb is not None:
            try:
                self._hb.pids = [i["proc"].pid for i in self._running.values()]
                self._hb.update(completed, status="running",
                                degraded=self._degrade_reason,
                                extra={"ui_tick": float(getattr(
                                    self._ui_owner, "_ui_last_tick", 0) or 0),
                                       "progress": round(float(acc), 3)})
            except Exception:
                pass

    # ── 主线程：只读内存快照，不做任何可能阻塞的事 ──────────────────────────

    def _honor_stop_flag(self):
        """Background-thread safe: stop spawning and kill children if flag exists."""
        if _GUARD is None or self._hb is None:
            return False
        try:
            if not _GUARD.stop_flag_exists(self._hb.state_path):
                return False
        except Exception:
            return False
        self._stopped = True
        with self._lock:
            running = list(self._running.items())
        for _wid, info in running:
            proc = info.get("proc")
            try:
                if proc is not None and proc.poll() is None:
                    proc.terminate()
            except Exception:
                pass
        return True

    def _poll(self):
        if self._stopped:
            return
        if self._honor_stop_flag():
            self.stop()
            return
        with self._lock:
            acc = self._snapshot.get("acc", 0.0)
            all_done = self._snapshot.get("all_done", False)
            degraded = self._snapshot.get("degraded", "")
            fps = float(self._snapshot.get("fps") or 0.0)

        total = len(self._jobs)
        overall = int(acc / total * 100) if total else 100
        self.progress.emit(max(0, min(100, overall)))
        with self._lock:
            completed = int(self._snapshot.get("done", 0) or 0)
        self.stage.emit(min(completed, total), total)
        if fps > 0:
            try:
                self.export_stats.emit(float(fps), int(acc), int(total))
            except Exception:
                _LOG.exception("并行 export_stats 发送失败")

        if degraded and degraded != getattr(self, "_degrade_emitted", ""):
            self._degrade_emitted = degraded
            try:
                self.degraded.emit(degraded)
            except Exception:
                pass

        if all_done:
            self._timer.stop()
            failed = sorted(set(self._failed))
            ok = len(self._jobs) - len(failed)
            print(f"[Parallel] 并行导出结束：成功 {ok} 个，需串行重试 {len(failed)} 个")
            self._stop_guard(status="finished", ok=ok, failed=len(failed))
            self._cleanup_tmp()
            self.finished.emit(failed)

    def _cleanup_tmp(self):
        if not self._tmpdir:
            return
        # 先把取证材料留下来（w*.log / w*.result / degrade.flag，都是小文本），
        # 再删临时目录。以前一 rmtree，「6 个 worker 为什么在第 30 帧全停」就
        # 再也查不到了。
        try:
            from cutora.core.paths import user_logs_dir
            keep_dir = os.path.join(user_logs_dir(), "export", "parallel",
                                    time.strftime("%Y%m%d-%H%M%S"))
            os.makedirs(keep_dir, exist_ok=True)
            for name in os.listdir(self._tmpdir):
                low = name.lower()
                if low.endswith((".log", ".result", ".flag", ".progress")):
                    try:
                        shutil.copy2(os.path.join(self._tmpdir, name),
                                     os.path.join(keep_dir, name))
                    except Exception:
                        pass
            # 复用主界面 GPU 服务时，它的日志不在临时目录里 —— 也留一份
            try:
                from cutora.fx import gpu as _gpu
                gdir = _gpu._GUI_SERVER.get("workdir") or ""
                src = os.path.join(gdir, "gpu_server.log") if gdir else ""
                if src and os.path.isfile(src) and not os.path.isfile(os.path.join(keep_dir, "gpu_server.log")):
                    shutil.copy2(src, os.path.join(keep_dir, "gpu_server.log"))
            except Exception:
                pass
            # 只留最近 10 次
            root = os.path.dirname(keep_dir)
            runs = sorted(d for d in os.listdir(root)
                          if os.path.isdir(os.path.join(root, d)))
            for d in runs[:-10]:
                shutil.rmtree(os.path.join(root, d), ignore_errors=True)
            print(f"[Parallel] worker 日志已留存: {keep_dir}", flush=True)
        except Exception:
            pass
        try:
            shutil.rmtree(self._tmpdir, ignore_errors=True)
        except Exception:
            pass
        self._tmpdir = None
