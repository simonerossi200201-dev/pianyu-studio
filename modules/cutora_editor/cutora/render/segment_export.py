# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q160 · 单个视频分段并行导出：一条视频切成 K 段，K 个子进程同时渲染，再无损拼接、混一次音频。

媒体库里只有 1～5 条视频要导出时，原来一条视频就一个渲染进程（里面一条解码线程 + 几个合成线程，
受 GIL 限制），24 核的机器大半在闲着。这里把时间线按帧切成 K 段：

* 每段一个子进程（``python -m cutora --render-job``，和并行批量导出同一个入口），
  任务项多两个键：``frame_range=[s, e)`` 和 ``video_only=True``。子进程里仍是同一个 RenderWorker、
  同一套编码参数（CRF / preset 一字不改），只是只画这一段、只出视频。
* 父进程等全部段落完成 → ``ffmpeg -f concat -c copy`` 拼成一条纯视频 → 走整条导出一模一样的收尾
  （``RenderWorker.finish_from_video``：混音、验收、旁车、多平台副本）。
* 任何一段失败 / 编码器不一致 / 机器太小 → 整条退回原来的串行导出（同一个线程里同步跑），不丢视频。

和整条导出的差别只在编码器切片点：每段开头多一个关键帧，文件大一点点；画面内容一致（每一帧的
合成只看时间 t，不看前后帧；解码按帧号精确 seek）。金样例（golden）走的是 RenderWorker.run()，不经这里。

用不用由 ``should_use`` 决定：设置开着、CPU ≥ 6 线程、视频够长（每段至少 4 秒）、不是「多平台各渲一遍」、
不是纯音频配置。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import threading
import time

from PySide6.QtCore import QThread, Signal

from cutora.core.log import get_logger

_LOG = get_logger("cutora.render.segment_export")

SETTING_KEY = "segment_export_enabled"
ENV_KEY = "CUTORA_SEGMENT_EXPORT"          # "0" 关、"1" 开（压过设置）
MIN_CPU = 6
MIN_SEG_SEC = 4.0                           # 每段至少这么长，否则段太碎、起进程的开销吃掉收益
MAX_SEGMENTS = 8
POLL_SEC = 0.3
RAM_HEADROOM_GB = 4.0


def enabled() -> bool:
    env = os.environ.get(ENV_KEY)
    if env in ("0", "1"):
        return env == "1"
    try:
        from cutora.core.history import _load_app_settings
        s = _load_app_settings() or {}
        v = s.get(SETTING_KEY)
        return True if v is None else bool(v)
    except Exception:
        return True


def set_enabled(on: bool) -> None:
    from cutora.core.history import _load_app_settings, _save_app_settings
    s = _load_app_settings() or {}
    s[SETTING_KEY] = bool(on)
    _save_app_settings(s)


def segment_count(total_frames: int, fps: float, *, cpu_count=None, use_gpu=False,
                  canvas_wh=None, avail_ram_gb=None) -> int:
    """切几段。CPU 每 4 个线程一段（2～8），短视频按每段 ≥ 4 秒再砍，内存 / NVENC 会话再砍。1 = 不分段。"""
    try:
        cpu = int(cpu_count if cpu_count is not None else (os.cpu_count() or 4))
    except (TypeError, ValueError):
        cpu = 4
    if cpu < MIN_CPU:
        return 1
    k = max(2, min(MAX_SEGMENTS, cpu // 4))
    min_frames = max(30, int(MIN_SEG_SEC * max(1.0, float(fps or 30.0))))
    k = min(k, max(1, int(total_frames) // min_frames))
    if use_gpu:
        try:
            from cutora.render.export_guard import nvenc_max_sessions
            k = min(k, max(1, int(nvenc_max_sessions())))
        except Exception:
            k = min(k, 2)
    try:
        from cutora.render.export_guard import mem_per_worker_gb, system_memory_gb
        per = float(mem_per_worker_gb(canvas_wh))
        avail = float(avail_ram_gb if avail_ram_gb is not None else system_memory_gb()[1])
        if avail > 0 and per > 0:
            k = min(k, max(1, int((avail - RAM_HEADROOM_GB) // per)))
    except Exception:
        pass
    return max(1, int(k))


def plan_segments(start_frame: int, end_frame: int, k: int) -> list:
    """[start, end) 均分成 k 段（前面的段多一帧），每段 [s, e)。"""
    start_frame, end_frame = int(start_frame), int(end_frame)
    n = max(0, end_frame - start_frame)
    k = max(1, min(int(k), n or 1))
    base, extra = divmod(n, k)
    out = []
    s = start_frame
    for i in range(k):
        e = s + base + (1 if i < extra else 0)
        out.append((s, e))
        s = e
    return [seg for seg in out if seg[1] > seg[0]]


def should_use(task, *, is_batch=False, use_gpu=False, cpu_count=None) -> bool:
    """这条视频值不值得分段。"""
    if not enabled() or not isinstance(task, dict):
        return False
    try:
        from cutora.batch import script_insert_image as _sii
        copies = _sii.active_copies(task)
        if copies and _sii.copies_render_mode(task) == "each":
            return False
    except Exception:
        pass
    try:
        from cutora.render.export_profile import encode_args, resolve_profile
        prof = resolve_profile(task=task)
        vcodec = encode_args(prof, bool(use_gpu))[0]
        if vcodec is None:
            return False                      # 纯音频配置
    except Exception:
        return False
    cpu = int(cpu_count if cpu_count is not None else (os.cpu_count() or 4))
    return cpu >= MIN_CPU


def concat_segments(paths, dest, ffmpeg_path=None) -> None:
    """无损拼接（-c copy）。各段同编码器、同参数、CFR，同 fps。"""
    from cutora.core.config import FFMPEG_PATH
    ff = ffmpeg_path or FFMPEG_PATH
    lst = dest + ".concat.txt"
    with open(lst, "w", encoding="utf-8") as fh:
        for p in paths:
            fh.write("file '%s'\n" % str(p).replace("\\", "/").replace("'", "'\\''"))
    cmd = [ff, "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", lst,
           "-c", "copy", "-fflags", "+genpts", dest]
    from cutora.core.proc import run as _run
    r = _run(cmd, capture_output=True)
    try:
        os.remove(lst)
    except OSError:
        pass
    if r.returncode != 0 or not os.path.isfile(dest) or os.path.getsize(dest) < 1024:
        err = (r.stderr or b"").decode("utf-8", "replace")[-600:]
        raise RuntimeError("拼接分段失败: %s" % err)


class SegmentExportWorker(QThread):
    """和 RenderWorker 同样的信号 / stop() / produced_paths，主窗口可以原样接上。"""

    progress = Signal(int)
    export_stats = Signal(float, int, int)
    finished = Signal()
    error = Signal(str)

    def __init__(self, task, output_path, mode="quality", use_gpu=False, export_logs=True,
                 segments=None, parent=None):
        super().__init__(parent)
        self.task = task
        self.output_path = output_path
        self.mode = mode
        self.use_gpu = bool(use_gpu)
        self.export_logs = export_logs
        self.segments_wanted = segments
        self.produced_paths = [output_path]
        self._is_running = True
        self._procs = []
        self._lock = threading.Lock()
        self._tmpdir = None
        self._serial = None
        self.used_segments = 0
        self.fell_back = False
        try:
            from cutora.core.history import EXPORT_VIDEO_MODE as _evm
            self.export_mode = _evm
        except Exception:
            self.export_mode = "compressed"

    # ───────── 外部接口 ─────────

    def stop(self):
        self._is_running = False
        with self._lock:
            procs = list(self._procs)
        for p in procs:
            try:
                if p.poll() is None:
                    p.terminate()
            except Exception:
                pass
        ser = self._serial
        if ser is not None:
            try:
                ser.stop()
            except Exception:
                pass

    # ───────── 主流程 ─────────

    def run(self):
        from cutora.render.render_worker import RenderWorker
        rw = RenderWorker(self.task, self.output_path, self.mode, self.use_gpu,
                          export_logs=self.export_logs)
        rw.export_mode = self.export_mode
        try:
            ok = self._run_segments(rw)
        except Exception as exc:
            _LOG.exception("分段并行导出异常，退回串行")
            ok = False
            self._why = str(exc)
        if ok or not self._is_running:
            return
        self._fallback_serial()

    def _fallback_serial(self):
        """任何一步不对 → 整条按原来的串行方式在本线程里重做（信号原样转发）。"""
        from cutora.render.render_worker import RenderWorker
        self.fell_back = True
        _LOG.warning("[SegExport] 退回串行导出：%s", getattr(self, "_why", ""))
        rw = RenderWorker(self.task, self.output_path, self.mode, self.use_gpu,
                          export_logs=self.export_logs)
        rw.export_mode = self.export_mode
        self._serial = rw
        rw.progress.connect(self.progress.emit)
        rw.export_stats.connect(self.export_stats.emit)
        rw.error.connect(self.error.emit)
        done = {"ok": False}
        rw.finished.connect(lambda: done.update(ok=True))
        rw.run()
        self.produced_paths = list(getattr(rw, "produced_paths", None) or [self.output_path])
        if done["ok"]:
            self.finished.emit()

    def _run_segments(self, rw) -> bool:
        from cutora.model.project_io import serialize_render_task
        from cutora.render.export_profile import encode_args, resolve_profile, resolve_profile_id
        from cutora.render.encoder_probe import pick_h264, pick_hevc
        from cutora.render.parallel import prepare_task_audio_for_export

        project = self.task if isinstance(self.task, dict) else {}
        prof = resolve_profile(task=project)
        use_gpu = self.use_gpu
        if use_gpu:
            kind = str((prof or {}).get("vcodec") or "h264")
            _enc, _reason = (pick_hevc if kind == "hevc" else pick_h264)(True)
            if not str(_enc).endswith("_nvenc"):
                use_gpu = False
        self.use_gpu = use_gpu
        if encode_args(prof, use_gpu)[0] is None:
            self._why = "纯音频配置"
            return False

        # 多平台副本「一次」模式：子进程画不带平台图的那份，父进程收尾时叠
        plat_copies = None
        render_project = project
        try:
            from cutora.batch import script_insert_image as _sii
            plat_copies = _sii.active_copies(project)
            if plat_copies:
                if _sii.copies_render_mode(project) == "each":
                    self._why = "多平台副本各渲一遍"
                    return False
                render_project = _sii.task_without_insert_overlays(project)
        except Exception:
            plat_copies = None
            render_project = project

        width, height, fps, _sar, duration, total_frames = rw.export_timing(render_project)
        if width <= 0 or height <= 0 or fps <= 0 or total_frames <= 1:
            self._why = "时间线参数无效"
            return False
        start_frame = max(0, int(float(render_project.get("trim_start_sec", 0.0) or 0.0) * fps))
        k = self.segments_wanted or segment_count(total_frames - start_frame, fps, use_gpu=use_gpu,
                                                  canvas_wh=(width, height))
        segs = plan_segments(start_frame, total_frames, k)
        if len(segs) < 2:
            self._why = "不值得分段（太短 / 机器太小）"
            return False
        self.used_segments = len(segs)
        cpu = os.cpu_count() or 8
        enc_threads = max(2, min(8, (cpu - 2) // len(segs)))
        _LOG.info("[SegExport] %d 段 × 子进程，每段 ffmpeg -threads %d，共 %d 帧 @ %.3f fps",
                  len(segs), enc_threads, total_frames, fps)

        self._tmpdir = tempfile.mkdtemp(prefix="cutora_seg_")
        prepared = prepare_task_audio_for_export(dict(render_project))
        ser_task = serialize_render_task(prepared)
        pid = None
        try:
            pid = resolve_profile_id(task=project)
        except Exception:
            pid = None
        seg_paths = []
        infos = []
        for i, (s, e) in enumerate(segs):
            out = os.path.join(self._tmpdir, "seg%02d.mp4" % i)
            seg_paths.append(out)
            job_path = os.path.join(self._tmpdir, "s%02d.json" % i)
            progress_path = os.path.join(self._tmpdir, "s%02d.progress" % i)
            result_path = os.path.join(self._tmpdir, "s%02d.result" % i)
            payload = {
                "items": [{"index": i, "task": ser_task, "output_path": out,
                           "frame_range": [int(s), int(e)], "video_only": True,
                           "export_profile_id": pid}],
                "mode": self.mode, "use_gpu": bool(use_gpu), "export_mode": self.export_mode,
                "enc_threads": int(enc_threads),
                "progress_path": progress_path, "result_path": result_path,
            }
            with open(job_path, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False)
            infos.append({"job": job_path, "progress": progress_path, "result": result_path,
                          "log": os.path.join(self._tmpdir, "s%02d.log" % i),
                          "frames": e - s, "proc": None, "cur": 0.0})
        if not self._is_running:
            return False
        for i, info in enumerate(infos):
            info["proc"] = self._launch(info, n_work=len(segs), use_gpu=use_gpu)
            time.sleep(0.04)                 # 错开一点，别同时抢着导入 / 加载字体
        if not self._poll(infos, total_frames - start_frame, fps):
            return False
        failed = [i for i, info in enumerate(infos) if not self._segment_ok(info, seg_paths[i])]
        if failed:
            self._why = "第 %s 段失败（看 %s 里的 s*.log）" % (",".join(str(i + 1) for i in failed), self._tmpdir)
            self._keep_logs()
            return False
        concat = os.path.join(self._tmpdir, "video.mp4")
        try:
            concat_segments(seg_paths, concat)
        except Exception as exc:
            self._why = str(exc)
            self._keep_logs()
            return False
        if not self._is_running:
            return False
        # 收尾和整条导出同一段代码：混音 → 验收 → 旁车 → 平台副本
        done = {"ok": False, "err": None}
        rw.error.connect(lambda m: done.update(err=m))
        rw.finished.connect(lambda: done.update(ok=True))
        rw.progress.connect(self.progress.emit)
        ok = rw.finish_from_video(concat, project, duration=duration, total_frames=total_frames,
                                  fps=fps, width=width, height=height, plat_copies=plat_copies,
                                  mad_refs=None, emit_done=True)
        if not ok:
            self._why = done["err"] or "收尾失败"
            self._keep_logs()
            return False
        self.produced_paths = list(getattr(rw, "produced_paths", None) or [self.output_path])
        self._cleanup()
        self.finished.emit()
        return True

    # ───────── 子进程 ─────────

    def _child_cmd(self, job_path):
        import sys
        from cutora.core.paths import REPO_ROOT
        return [sys.executable, "-u", "-m", "cutora", "--render-job", job_path], REPO_ROOT

    def _launch(self, info, *, n_work, use_gpu):
        from cutora.core.flags import apply_render_child
        from cutora.core.proc import popen as _popen
        from cutora.render.compose_parallel import (
            COMPOSE_WORKERS_ENV, COMPOSE_WORKERS_MAX, PARALLEL_JOB_COMPOSE_MAX,
        )
        env = dict(os.environ)
        apply_render_child(env)
        cpu = os.cpu_count() or 8
        per = max(1, cpu // max(1, n_work) // 2)
        for k in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
            env[k] = str(per)
        cap = PARALLEL_JOB_COMPOSE_MAX if int(n_work or 1) > 1 else COMPOSE_WORKERS_MAX
        env[COMPOSE_WORKERS_ENV] = str(max(1, min(cap, COMPOSE_WORKERS_MAX, cpu // max(1, n_work))))
        # Q187b：和整批并行（parallel.py）看齐 —— 解码线程上限 + GPU 像素运算服务地址。
        # 以前分段导出的子进程没有这两样：每个源 16 个解码线程、抠像 / 调整层全在 CPU 上算。
        try:
            from cutora.render.render_worker import EXPORT_DECODE_THREADS_ENV
            env[EXPORT_DECODE_THREADS_ENV] = str(max(2, min(4, cpu // max(1, n_work) // 3)))
        except Exception:
            pass
        try:
            from cutora.fx import gpu as _gpu
            env.pop(_gpu.ENV_ADDR, None)
            addr = _gpu.gui_server_addr() or _gpu.ensure_gui_server(wait=True)
            if addr:
                env[_gpu.ENV_ADDR] = addr
        except Exception:
            _LOG.exception("[SegExport] 取 GPU 像素运算服务地址失败，子进程走 CPU")
        if not use_gpu:
            env["CUDA_VISIBLE_DEVICES"] = ""
        creationflags = 0
        if os.name == "nt":
            creationflags = (getattr(subprocess, "CREATE_NO_WINDOW", 0)
                             | getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0))
        log_file = open(info["log"], "w", encoding="utf-8", errors="ignore")
        info["log_file"] = log_file
        cmd, cwd = self._child_cmd(info["job"])
        proc = _popen(cmd, cwd=cwd, env=env, stdout=log_file, stderr=subprocess.STDOUT,
                      stdin=subprocess.DEVNULL, creationflags=creationflags)
        with self._lock:
            self._procs.append(proc)
        return proc

    def _read_cur(self, path, info=None) -> float:
        try:
            with open(path, "r", encoding="utf-8") as fh:
                d = json.load(fh)
            if info is not None:
                info["fps"] = float(d.get("fps") or 0.0) if int(d.get("done", 0)) < 1 else 0.0
            if int(d.get("done", 0)) >= 1:
                return 100.0
            return float(d.get("cur", 0) or 0.0)
        except Exception:
            return -1.0

    def _poll(self, infos, frames_total, fps) -> bool:
        t0 = time.perf_counter()
        render_t0 = None
        last_emit = -1
        while True:
            if not self._is_running:
                self.stop()
                return False
            alive = False
            acc = 0.0
            sum_fps = 0.0
            for info in infos:
                p = info["proc"]
                running = p is not None and p.poll() is None
                if running:
                    alive = True
                cur = self._read_cur(info["progress"], info=info)
                if cur >= 0:
                    info["cur"] = cur
                elif p is not None and not running:
                    info["cur"] = 100.0 if p.returncode == 0 else info["cur"]
                    info["fps"] = 0.0
                if running:
                    sum_fps += max(0.0, float(info.get("fps") or 0.0))
                acc += info["cur"] / 100.0 * info["frames"]
            if acc > 0.0 and render_t0 is None:
                render_t0 = time.perf_counter()
            pct = int(min(99, acc / max(1, frames_total) * 100))
            if pct != last_emit:
                last_emit = pct
                self.progress.emit(pct)
                el = max(1e-6, time.perf_counter() - (render_t0 if render_t0 is not None else t0))
                avg_fps = float(acc / el) if render_t0 is not None else 0.0
                live_fps = max(sum_fps, avg_fps)
                try:
                    self.export_stats.emit(float(live_fps), int(acc), int(frames_total))
                except Exception:
                    pass
            if not alive:
                break
            time.sleep(POLL_SEC)
        for info in infos:
            lf = info.get("log_file")
            if lf is not None:
                try:
                    lf.close()
                except Exception:
                    pass
        return True

    @staticmethod
    def _segment_ok(info, path) -> bool:
        p = info["proc"]
        if p is None or p.returncode != 0:
            return False
        try:
            with open(info["result"], "r", encoding="utf-8") as fh:
                res = json.load(fh)
            if res.get("failed"):
                return False
        except Exception:
            return False
        return os.path.isfile(path) and os.path.getsize(path) > 1024

    def _keep_logs(self):
        """失败时把日志留在 logs/export/segment/ 里，临时段落删掉。"""
        try:
            from cutora.core.paths import user_logs_dir
            dst = os.path.join(user_logs_dir(), "export", "segment", time.strftime("%Y%m%d_%H%M%S"))
            os.makedirs(dst, exist_ok=True)
            for name in os.listdir(self._tmpdir or ""):
                if name.endswith((".log", ".result", ".json")):
                    shutil.copy2(os.path.join(self._tmpdir, name), os.path.join(dst, name))
            _LOG.warning("[SegExport] 日志保留在 %s", dst)
        except Exception:
            pass
        self._cleanup()

    def _cleanup(self):
        d = self._tmpdir
        self._tmpdir = None
        if d and os.path.isdir(d):
            shutil.rmtree(d, ignore_errors=True)
