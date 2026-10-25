# SPDX-License-Identifier: GPL-3.0-or-later
# Extracted from the T01 monolith by tools/split_monolith.py.
# Original lines: 4613-5915, 14873-14918

import os
import math
import subprocess
import cv2
import numpy as np
import copy
import shutil
import time
import tempfile
import threading
from PySide6.QtCore import (
    QThread,
    Signal,
    QRectF,
)
from PySide6.QtGui import (
    QPainter,
    QImage,
)
from cutora.core.config import FFMPEG_PATH
from cutora.core.fps_meter import export_fps
from cutora.core.log import get_logger
from cutora.preview.engine import timeline_frame_index
_LOG = get_logger("cutora.render.render_worker")


def split_atempo(factor):
    """Split one tempo into ffmpeg atempo stages, each in [0.5, 100]."""
    try:
        f = float(factor)
    except (TypeError, ValueError):
        return []
    if not math.isfinite(f) or f <= 0 or abs(f - 1.0) < 1e-6:
        return []
    stages = []
    guard = 0
    while f < 0.5 and guard < 16:
        stages.append(0.5)
        f /= 0.5
        guard += 1
    while f > 100.0 and guard < 16:
        stages.append(100.0)
        f /= 100.0
        guard += 1
    if abs(f - 1.0) >= 1e-6:
        stages.append(min(100.0, max(0.5, f)))
    return stages


def _fmt_tempo(value):
    text = f"{float(value):.6f}".rstrip("0").rstrip(".")
    return text or "1"


def atempo_chain(factor):
    """Comma-prefixed atempo filters, or '' when the factor is 1."""
    stages = split_atempo(factor)
    if not stages:
        return ""
    return "".join(",atempo=%s" % _fmt_tempo(stage) for stage in stages)
try:
    from cutora.audio import spatial as _SPATIAL
    from cutora.audio import sfx_library as _SFX
except Exception as _sfx_imp_err:
    _SPATIAL = None
    _SFX = None
    _LOG.warning("[AudioFx] 音效模块未加载（不影响原有导出）: %s", _sfx_imp_err)
from cutora.render import anim_engine as anim_engine
from cutora.subtitle.text_utils import _draw_thumbnail_text_on_rect
from cutora.model.timeline import (
    DEFAULT_TIMELINE_WIDTH,
    DEFAULT_TIMELINE_HEIGHT,
    DEFAULT_TIMELINE_FPS,
    DEFAULT_VIDEO_FIT_MODE,
)
from cutora.render.frame_renderer import (
    BLEND_MODES,
    _BLEND_NORMAL,
    compute_export_render_order,
    cleanup_dbi_video_caps,
    _SE_SUB_CACHE,
    _SE_OV_CACHE,
)
from cutora.render.export_renderer import ExportRenderer
from cutora.render import export_probe as _probe
from cutora.render.readers import (
    _open_video_capture,
    _is_sequence_path,
    sequence_first_frame,
    probe_duration_sec,
    probe_video_export_params,
    _get_sar_from_cap_or_path,
    _apply_sar_correction,
    has_audio_stream,
)
from cutora.model.timeline import DEFAULT_TIMELINE_FPS, DEFAULT_TIMELINE_HEIGHT, DEFAULT_TIMELINE_WIDTH, DEFAULT_VIDEO_FIT_MODE
from cutora.render.export_renderer import ExportRenderer
from cutora.render.frame_renderer import BLEND_MODES, _BLEND_NORMAL, _SE_OV_CACHE, _SE_SUB_CACHE, cleanup_dbi_video_caps, compute_export_render_order
from cutora.render.readers import _apply_sar_correction, _get_sar_from_cap_or_path, _open_video_capture, has_audio_stream, probe_duration_sec, probe_video_export_params
from cutora.subtitle.text_utils import _draw_thumbnail_text_on_rect
from cutora.core.proc import popen as _no_window_popen, run as _no_window_run


# BUG-42：导出时最多同时开着几路解码器。拼接时间线是顺序播放的，
# 27 个源同时开着 = 27 路解码器 × N 个 worker，内存 3 GB/进程、
# 显卡驱动被压到重置（黑屏）。用完即关，最多留这么几路（5/6 格分屏同屏需 ≥6 路，绝不可低于 6 否则每帧驱逐重开 seek）。
EXPORT_MAX_OPEN_CAPS = 12
# Q180 导出合成多线程（含统一叠压）：解码线程只「取帧」并记下来（_REPLAY_TLS.record），合成线程照同一套
# 串行代码画整帧、取帧时从记录里拿（_REPLAY_TLS.replay）—— 每帧逐像素等于串行，抠像 / 遮罩 / 调整图层全部并行。
_REPLAY_TLS = threading.local()
# 处理好的片段帧（调色 → 翻转 → 渐隐 → 抠像 / 遮罩）按「同一张源帧」复用：24 fps 素材放在 30 fps 时间线上
# 每 5 帧有 1 帧是重复的源帧，抠像 / 遮罩不用再算一遍。只在导出里用。
FX_MEMO_MAX = 24
EXPORT_RGB32_FASTPATH = os.environ.get("CUTORA_EXPORT_RGB32", "1") != "0"
# Q180：导出时带抠像 / 遮罩的片段按画面上的像素数缩小后再做效果（见 _export_shrink_fx）
EXPORT_FX_AT_OUTPUT_RES = os.environ.get("CUTORA_EXPORT_FX_OUTPUT_RES", "1") != "0"


def export_hw_decode() -> bool:
    """False = 导出用软件解码（不变量 #1，修 BUG-42 黑屏的那一条）。

    `CUTORA_EXPORT_HW_DECODE=1` 只是**诊断开关**：用来 A/B 对比 golden 基线，
    确认某次哈希漂移是不是解码路径引起的。产品路径永远走软件解码，
    不要在代码里改这个默认值，也不要在任何 .bat / 快捷方式里设这个环境变量。
    """
    return (os.environ.get("CUTORA_EXPORT_HW_DECODE") or "") == "1"
EXPORT_DECODE_THREADS_ENV = "CUTORA_DECODE_THREADS"
EXPORT_DECODE_THREADS_DEFAULT = 4


def export_decode_threads() -> int:
    """Q180：导出里每个解码器的线程数。批量并行时父进程按进程数算好写进环境变量；串行导出默认 4。
    0 = 不限（FFmpeg 自己定）。"""
    raw = os.environ.get(EXPORT_DECODE_THREADS_ENV)
    if raw not in (None, ""):
        try:
            return max(0, int(raw))
        except (TypeError, ValueError):
            pass
    return EXPORT_DECODE_THREADS_DEFAULT


EXPORT_CAP_EVICT_MARGIN_S = 1.0


class _ExportCapPool:
    """Lazy, bounded pool of cv2 captures for the export loop.

    Dict-like for the call sites (``get`` / ``items`` / ``values`` / ``clear``):
    a source is opened on first ``get`` and released once the timeline has
    moved past its last clip (``evict_done``) or when the pool is full (LRU).
    Prefetch threads on an evicted source are joined before release, and the
    source's decode cursor is reset so a later reopen seeks cleanly.
    """

    def __init__(self, paths, last_end, frame_states, max_open=EXPORT_MAX_OPEN_CAPS,
                 no_seek=False):
        self._paths = dict(paths or {})
        self._last_end = dict(last_end or {})
        self._states = frame_states
        self._max = max(1, int(max_open))
        self._caps = {}          # sid → cap (insertion order = LRU order)
        self._failed = set()
        self._no_seek = bool(no_seek)
        self.opened_total = 0
        self.evicted_total = 0

    # ── dict-like ──
    def get(self, sid, default=None):
        if sid is None:
            return default
        cap = self._caps.get(sid)
        if cap is not None:
            # 刷新 LRU 顺序
            self._caps.pop(sid)
            self._caps[sid] = cap
            return cap
        if sid in self._failed or sid not in self._paths:
            return default
        return self._open(sid) or default

    def __contains__(self, sid):
        return sid in self._caps

    def items(self):
        return list(self._caps.items())

    def values(self):
        return list(self._caps.values())

    def __len__(self):
        return len(self._caps)

    def clear(self):
        for sid in list(self._caps):
            self._release(sid)

    # ── internals ──
    def _path_exists(self, path):
        if not path:
            return False
        if os.path.exists(path):
            return True
        if _is_sequence_path(path):
            first = sequence_first_frame(path)
            return bool(first and os.path.isfile(first))
        return False

    def _open(self, sid):
        path = self._paths.get(sid)
        if not self._path_exists(path):
            self._failed.add(sid)
            return None
        while len(self._caps) >= self._max:
            victim = next(iter(self._caps))
            self._release(victim)
            self.evicted_total += 1
        cap = _open_video_capture(path, hw=export_hw_decode(), threads=export_decode_threads()) \
            or cv2.VideoCapture(path)
        if cap is None or not cap.isOpened():
            self._failed.add(sid)
            return None
        self._caps[sid] = cap
        self.opened_total += 1
        st = self._states.get(sid)
        if st is None:
            self._states[sid] = {
                "last_time": -1.0, "last_frame": None, "src_path": path,
                "no_seek": self._no_seek,
            }
        else:
            # 重开：解码游标作废，last_frame / sar / src_fps 保留（同一文件）
            st["decode_cursor"] = -1.0
            st["no_seek"] = self._no_seek
            st.pop("_pf_ret", None)
            st.pop("_pf_frame", None)
            st.pop("_pf_kick", None)
        return cap

    def _release(self, sid):
        cap = self._caps.pop(sid, None)
        st = self._states.get(sid)
        if st is not None:
            try:
                from cutora.render.decode_prefetch import shutdown as _pf_shutdown
                _pf_shutdown(st)                 # Q189b：解码器放掉，它的预读线程也停
            except Exception:
                _LOG.exception("释放解码器前等待预读线程失败")
            st["decode_cursor"] = -1.0
            st.pop("_pf_ret", None)
            st.pop("_pf_frame", None)
            st.pop("_pf_kick", None)
        if cap is not None:
            try:
                cap.release()
            except Exception:
                _LOG.exception("释放 VideoCapture 失败")

    def evict_done(self, t):
        """Release sources whose last clip ended before ``t`` (with margin)."""
        for sid in list(self._caps):
            end = self._last_end.get(sid)
            if end is not None and t > end + EXPORT_CAP_EVICT_MARGIN_S:
                self._release(sid)
                self.evicted_total += 1


def _tl_of(*a, **k):
    """Call-time import: this module is loaded by --render-job before MainWindow."""
    from cutora.ui.main_window import _tl_of as _impl
    return _impl(*a, **k)

def _axis_aligned(tf) -> bool:
    """只有平移 / 缩放（没有旋转、错切、透视）。"""
    return (abs(tf.m12()) < 1e-12 and abs(tf.m21()) < 1e-12 and abs(tf.m13()) < 1e-12
            and abs(tf.m23()) < 1e-12)


class _ReplayCap:
    """Q180 合成线程里的「解码器」占位：真正的帧在解码线程记下的 replay 里。"""

    def isOpened(self):
        return True

    def get(self, _prop):
        return 0.0

    def release(self):
        pass


_REPLAY_CAP = _ReplayCap()


class _ReplayCaps:
    """Q180 合成线程用的解码器池占位：同样的源（_paths）、同样的「开得了没有」，但不解码、不预读、不回收。"""

    def __init__(self, paths):
        self._paths = dict(paths or {})

    def get(self, sid, default=None):
        return _REPLAY_CAP if sid in self._paths else default

    def __contains__(self, sid):
        return sid in self._paths

    def items(self):
        return []

    def values(self):
        return []

    def __len__(self):
        return 0

    def clear(self):
        pass

    def evict_done(self, _t):
        pass


def _stack_parallel_ok(stack) -> bool:
    """统一叠压时能不能多线程合成：全部视频轨只成一段、而且垫在最底下（覆层 / 调整图层都在它上面）。
    这时解码线程拍出的整张视频层 = 那段视频带，合成线程逐像素和串行一样。视频和文字交错的才必须串行。"""
    if not stack:
        return True
    kinds = [k for k, _p in stack]
    return kinds.count("video") <= 1 and (kinds[0] == "video" or "video" not in kinds)


class RenderWorker(QThread):
    progress = Signal(int)
    export_stats = Signal(float, int, int)  # fps, frames_done, frames_total
    finished = Signal()
    error = Signal(str)
    
    def __init__(self, task, output_path, mode="quality", use_gpu=False, export_logs=True,
                 compose_workers=None, frame_range=None, video_only=False):
        super().__init__()
        self.task = task
        self.output_path = output_path
        self.mode = mode
        self.use_gpu = use_gpu
        self.export_logs = export_logs
        self.compose_workers = compose_workers
        # Q160 分段并行：只渲染 [frame_range[0], frame_range[1]) 这一段、只出视频（不混音、不验收、
        # 不写旁车、不做平台副本）；父进程拼接后统一收尾。编码参数和整条导出一字不差。
        self.frame_range = tuple(int(x) for x in frame_range) if frame_range else None
        self.video_only = bool(video_only)
        try:
            from cutora.core.history import EXPORT_VIDEO_MODE as _evm
            self.export_mode = _evm
        except Exception:
            self.export_mode = "compressed"
        self.export_audio_rate = None
        self._is_running = True
        self._geom_last_log_t = 0.0
        self.produced_paths = [output_path]
        self.ffmpeg_pid = None
        self._loudness_report = None
        self._export_t0 = None
        self._export_encoder = None
        self._pipe_stop = threading.Event()

    def stop(self):
        self._is_running = False
        ev = getattr(self, "_pipe_stop", None)
        if ev is not None:
            ev.set()

    def _write_sidecars(self, project):
        import time as _time
        from cutora.render.export_sidecar import write_all
        from cutora.render.export_profile import resolve_profile_id
        elapsed = None
        if self._export_t0 is not None:
            elapsed = round(_time.perf_counter() - self._export_t0, 3)
        write_all(self.output_path, project or self.task, {
            "profile": resolve_profile_id(task=project or self.task),
            "encoder": self._export_encoder,
            "elapsed_sec": elapsed,
            "loudness": self._loudness_report,
            "gpu": bool(self.use_gpu),
        })

    def _concat_intro_video(self, intro_info, main_path):
        intro_path = intro_info.get("path") if isinstance(intro_info, dict) else None
        if not intro_path or not os.path.exists(intro_path):
            return None

        intro_dur = intro_info.get("duration") if isinstance(intro_info, dict) else None
        if not isinstance(intro_dur, (int, float)) or intro_dur <= 0:
            intro_dur = probe_duration_sec(intro_path)
        if not isinstance(intro_dur, (int, float)) or intro_dur <= 0:
            intro_dur = 3.0

        fd, temp_out = tempfile.mkstemp(suffix=".mp4")
        os.close(fd)

        intro_has_audio = has_audio_stream(intro_path)
        main_has_audio = has_audio_stream(main_path)
        main_dur = probe_duration_sec(main_path)

        filter_complex = None
        maps = []

        if intro_has_audio and main_has_audio:
            filter_complex = "[0:v][0:a][1:v][1:a]concat=n=2:v=1:a=1[outv][outa]"
            maps = ["-map", "[outv]", "-map", "[outa]"]
        else:
            # Video concat always
            filter_complex = "[0:v][1:v]concat=n=2:v=1:a=0[outv]"
            maps = ["-map", "[outv]"]

            # Add audio concat with silence if possible
            if main_has_audio or intro_has_audio:
                intro_sil = f"anullsrc=r=48000:cl=stereo,atrim=duration={intro_dur},asetpts=N/SR/TB[a0]"
                if main_has_audio and not intro_has_audio:
                    filter_complex = f"{filter_complex};{intro_sil};[a0][1:a]concat=n=2:v=0:a=1[outa]"
                    maps = ["-map", "[outv]", "-map", "[outa]"]
                elif intro_has_audio and not main_has_audio and isinstance(main_dur, (int, float)) and main_dur > 0:
                    main_sil = f"anullsrc=r=48000:cl=stereo,atrim=duration={main_dur},asetpts=N/SR/TB[a1]"
                    filter_complex = f"{filter_complex};{main_sil};[0:a][a1]concat=n=2:v=0:a=1[outa]"
                    maps = ["-map", "[outv]", "-map", "[outa]"]

        # ── [v6.1] 中间文件的编码参数 ──
        #
        # 关键认识：这个临时文件**不是最终产物**。它随后会被主渲染循环
        # 逐帧解码、重新绘制、重新编码，用完即删。
        #
        # 原实现写死 `-c:v libx264` 且不带任何参数，等于用了 x264 的默认值
        # preset=medium + CRF=23。后果有两个，而且都是纯亏：
        #   1. 慢    —— 全片多一次 medium 转码，是整个导出里最耗时的步骤之一
        #   2. 掉画质 —— CRF 23 先压一遍，最终再按 CRF 18 压一遍，二次损失，
        #                 用户在导出设置里调的高画质被这一步提前糟蹋了
        #
        # 现在改为：优先用 NVENC 硬件编码（几乎不占 CPU），否则用
        # libx264 ultrafast；两者都用接近无损的量化参数（CQ/CRF 12），
        # 远高于最终输出的 CRF 18，所以对成片画质是**只赚不亏**。
        def _build_concat_cmd(use_nvenc):
            if use_nvenc:
                venc = ["-c:v", "h264_nvenc", "-preset", "p1",
                        "-rc", "constqp", "-qp", "12", "-pix_fmt", "yuv420p"]
            else:
                venc = ["-c:v", "libx264", "-preset", "ultrafast",
                        "-crf", "12", "-pix_fmt", "yuv420p"]
            return [
                FFMPEG_PATH, "-y",
                "-t", str(float(intro_dur)), "-i", intro_path,
                "-i", main_path,
                "-filter_complex", filter_complex,
                *maps,
                *venc,
                "-c:a", "aac",
                "-movflags", "+faststart",
                temp_out
            ]

        si = subprocess.STARTUPINFO(); si.dwFlags |= subprocess.STARTF_USESHOWWINDOW

        # 尝试顺序：NVENC（若本次导出启用了 GPU）→ libx264 ultrafast
        attempts = []
        if self.use_gpu:
            attempts.append(True)
        attempts.append(False)

        res = None
        for use_nvenc in attempts:
            res = _no_window_run(_build_concat_cmd(use_nvenc),
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 startupinfo=si)
            if res.returncode == 0:
                _LOG.info(f"[Export] 开场拼接完成 "
                      f"({'NVENC 硬件编码' if use_nvenc else 'libx264 ultrafast'})")
                return temp_out
            if use_nvenc:
                _LOG.info("[Export] 开场拼接的 NVENC 编码失败，改用 CPU (libx264 ultrafast) 重试...")

        try:
            err = (res.stderr or b"").decode("utf-8", errors="ignore")[-800:] if res else ""
            if err:
                _LOG.info(f"[Export] 开场拼接失败，FFmpeg 输出：\n{err}")
        except Exception:
            pass
        try:
            os.remove(temp_out)
        except Exception:
            pass
        return None

    def run(self):
        import time as _time
        self._export_t0 = _time.perf_counter()
        from cutora.render.export_profile import encode_args, resolve_profile
        from cutora.render.encoder_probe import pick_h264, pick_hevc
        prof = resolve_profile(task=self.task)
        if self.use_gpu:
            kind = str((prof or {}).get("vcodec") or "h264")
            _enc, reason = (pick_hevc if kind == "hevc" else pick_h264)(True)
            if not str(_enc).endswith("_nvenc"):
                print("[Export]", reason)
                self.use_gpu = False
        vcodec, _preset, _extra, _pix = encode_args(prof, self.use_gpu)
        self._export_encoder = vcodec
        if vcodec is None:
            try:
                if self._mix_audio_tracks(None, self.task, self.output_path):
                    self.produced_paths = [self.output_path]
                    self._write_sidecars(self.task)
                    self.progress.emit(100)
                    self.finished.emit()
            except Exception as e:
                self.error.emit("渲染发生最终错误: %s" % e)
            return
        video_path = self.task['video']
        from cutora.model.canvas import canvas_wh
        w, h = canvas_wh(self.task)

        temp_video = None
        intro_info = self.task.get("intro_media") if isinstance(self.task, dict) else None
        if self.video_only:
            intro_info = None            # 开场视频走时间线；分段子进程不做这步拼接
        if isinstance(intro_info, dict) and intro_info.get("type") == "video":
            temp_video = self._concat_intro_video(intro_info, video_path)
            if not temp_video:
                self.error.emit("开场视频拼接失败")
                return
            video_path = temp_video

        try:
            # T08a: fast 模式已从产品路径删除（无 UI/命令入口）。一律 WYSIWYG。
            try:
                self.render_wysiwyg(video_path, w, h)
            except (BrokenPipeError, Exception) as e:
                # 如果是因为驱动/硬件导致的 BrokenPipeError 且当前开启了 GPU
                # Q160：分段子进程不许自己换编码器（几段编码器不一致拼不起来）→ 报错，父进程整条重来
                if self.use_gpu and not self.video_only:
                    print(f"\n[CRITICAL Fallback] 检测到 GPU 编码器启动失败: {e}")
                    print("原因可能是显卡驱动过旧或不支持 NVENC API 13.0。")
                    print("正在自动切换至 CPU (libx264) 重新开始渲染...\n")
                    self.use_gpu = False  # 强制关闭 GPU
                    # 重新调用
                    self.render_wysiwyg(video_path, w, h)
                else:
                    raise e
        except Exception as e:
            self.error.emit(f"渲染发生最终错误: {str(e)}")
        finally:
            if temp_video and os.path.exists(temp_video):
                try: os.remove(temp_video)
                except: pass

    def render_fast(self, video_path, w, h):
        # T08a: leftover alias. Same pixels as quality (no AA shortcut).
        try:
            self.render_wysiwyg_export(self.task)
            self.finished.emit()
        except Exception as e:
            self.error.emit(str(e))

    def render_wysiwyg(self, video_path, width, height):
        self.render_wysiwyg_export(self.task)

    def finish_from_video(self, temp_video, project, *, duration, total_frames, fps, width, height,
                          plat_copies=None, mad_refs=None, emit_done=True) -> bool:
        """纯视频文件 → 成片：混音 → 验收（时长 / 帧数 / 可解）→ 旁车 → 多平台副本。

        整条导出的收尾，Q160 分段并行把各段拼接之后也走这同一段。失败发 error、返回 False。"""
        if not self._mix_audio_tracks(temp_video, project, self.output_path):
            return False
        try:
            from cutora.render.export_params import export_verify_expect
            from cutora.render.export_verify import mad_vs_preview, verify_export
            # 入点之后才开始写帧。验收要看写出来的这一段，不能拿整条时间线的时长来比。
            _trim_s = 0.0
            if isinstance(project, dict):
                try:
                    _trim_s = float(project.get("trim_start_sec") or 0.0)
                except (TypeError, ValueError):
                    _trim_s = 0.0
            expect_dur, expect_frames = export_verify_expect(duration, fps, _trim_s)
            if expect_frames <= 0:
                expect_dur, expect_frames = float(duration), int(total_frames)
            ok, why = verify_export(
                self.output_path,
                expected_duration=float(expect_dur),
                expected_frames=int(expect_frames),
                fps=float(fps),
                width=int(width),
                height=int(height),
            )
            if ok and mad_refs and width > 0 and height > 0:
                try:
                    mad = mad_vs_preview(
                        self.output_path, mad_refs, int(width), int(height), fps=float(fps))
                    if mad:
                        ok, why = False, [mad]
                except Exception:
                    _LOG.exception("export MAD skipped")
            if not ok:
                self.error.emit("导出验收失败: %s" % "; ".join(why))
                return False
            self._verified_ok = True
        except Exception as _ve:
            self.error.emit("导出验收失败: %s" % _ve)
            return False

        self.produced_paths = [self.output_path]
        self._write_sidecars(project)
        if plat_copies:
            try:
                from cutora.batch import script_insert_image as _sii
                produced, err = _sii.export_platform_copies(
                    self.output_path, project, plat_copies,
                    use_gpu=self.use_gpu, export_mode=self.export_mode,
                    log=print)
                if err:
                    self.error.emit("多平台副本失败: %s" % err)
                    return False
                if produced:
                    self.produced_paths = produced
            except Exception as e:
                self.error.emit("多平台副本失败: %s" % e)
                return False

        self.progress.emit(100)
        if emit_done:
            self.finished.emit()
        return True

    def export_timing(self, project):
        """(width, height, fps, export_sar, duration, total_frames) —— 整条导出和分段父进程用同一份算法。"""
        tl = _tl_of(project)
        width = int(tl.width if tl else DEFAULT_TIMELINE_WIDTH)
        height = int(tl.height if tl else DEFAULT_TIMELINE_HEIGHT)
        fps = float(tl.fps if tl else DEFAULT_TIMELINE_FPS)
        export_sar = None
        if self.export_mode == "original":
            src = self._get_primary_video_source(project)
            src_path = getattr(src, "path", None) or (src.get("path") if isinstance(src, dict) else None)
            if not src_path:
                src_path = project.get("video")
            params = probe_video_export_params(src_path)
            if params:
                # T21: 导出宽高永远是画布，original 只跟 fps / SAR / 音频采样率
                if isinstance(params.get("fps"), (int, float)) and params["fps"] > 0:
                    fps = float(params["fps"])
                export_sar = params.get("sar")
                self.export_audio_rate = params.get("audio_rate")
        if width <= 0 or height <= 0 or fps <= 0:
            return width, height, fps, export_sar, 0.0, 0
        duration = self._calc_project_duration(project)
        total_frames = max(1, int(duration * fps))
        return width, height, fps, export_sar, duration, total_frames

    def render_wysiwyg_export(self, project, _emit_done=True):
        project = project if isinstance(project, dict) else {}
        # 多平台副本：
        #   once（默认）字幕烧 1 遍，再 FFmpeg 叠各平台图（图在字幕上面，更快）
        #   each 每个平台整段单独渲染（字幕按时间线盖在图片上）
        _plat_copies = None
        _render_each = False
        try:
            from cutora.batch import script_insert_image as _sii
            _plat_copies = None if self.video_only else _sii.active_copies(project)
            if _plat_copies:
                _render_each = _sii.copies_render_mode(project) == "each"
                if not _render_each:
                    project = _sii.task_without_insert_overlays(project)
                    _LOG.info("[Export] 多平台副本：字幕先烧 1 遍，再叠 %s" %
                          ", ".join(p.get("prefix") or p.get("name") for p in _plat_copies))
                else:
                    _LOG.info("[Export] 多平台副本：多次单独渲染（字幕可盖住图片）%s" %
                          ", ".join(p.get("prefix") or p.get("name") for p in _plat_copies))
        except Exception as e:
            _LOG.info("[Export] 多平台副本准备失败，按单份导出: %s" % e)
            _plat_copies = None
            _render_each = False

        if _plat_copies and _render_each:
            from cutora.batch import script_insert_image as _sii
            dests = _sii.platform_dest_paths(self.output_path, _plat_copies)
            produced = []
            orig_out = self.output_path
            n = len(_plat_copies)
            try:
                for i, (copy, dest) in enumerate(zip(_plat_copies, dests)):
                    if not self._is_running:
                        return
                    _LOG.info("[Export] 多次渲染 %d/%d  %s → %s" % (
                        i + 1, n, copy.get("prefix") or copy.get("name"), dest))
                    self.output_path = dest
                    task_i = _sii.task_with_platform_overlay(project, copy)
                    self.render_wysiwyg_export(task_i, _emit_done=False)
                    if not self._is_running:
                        return
                    if not (os.path.isfile(dest) and os.path.getsize(dest) > 1024):
                        return
                    produced.append(dest)
            finally:
                self.output_path = orig_out
            self.produced_paths = produced
            if _emit_done:
                self.progress.emit(100)
                self.finished.emit()
            return
        # 清除缓存（避免上次导出的残留影响）
        _SE_SUB_CACHE["key"] = None; _SE_SUB_CACHE["img"] = None
        _SE_OV_CACHE["key"] = None; _SE_OV_CACHE["img"] = None
        cleanup_dbi_video_caps()  # 释放挂载视频的 VideoCapture
        tl = _tl_of(project)
        width, height, fps, export_sar, duration, total_frames = self.export_timing(project)
        if width <= 0 or height <= 0 or fps <= 0:
            self.error.emit("时间轴参数无效，无法导出")
            return

        # ── 导出诊断日志 ──
        _n_segs = len(project.get("segments", []) or [])
        _n_text = len(project.get("text_overlays", []) or [])
        _n_img = len(project.get("image_overlays", []) or [])
        _LOG.info(f"[Export] mode={self.export_mode} gpu={self.use_gpu} dims={width}x{height} fps={fps:.2f} dur={duration:.2f}s frames={total_frames}")
        _LOG.info(f"[Export] segments={_n_segs} text_overlays={_n_text} image_overlays={_n_img}")

        temp_video = None
        proc = None
        log_file = None
        log_path = None
        cap = None
        _video_caps = {}   # 多视频源 capture（finally 清理用）
        _frame_states = {}
        pipe_writer = None
        try:
            fd, temp_video = tempfile.mkstemp(suffix=".mp4")
            os.close(fd)

            if self.export_logs:
                from cutora.core.paths import user_logs_dir
                log_dir = os.path.join(user_logs_dir(), "export")
                os.makedirs(log_dir, exist_ok=True)
                stem = os.path.splitext(os.path.basename(self.output_path or "export"))[0]
                log_path = os.path.join(log_dir, stem + ".ffmpeg.log")
                log_file = open(log_path, "w", encoding="utf-8", errors="ignore")

            from cutora.core.rust_engine import is_rust_available as _rust_ok
            _use_yuv420p = bool(
                _rust_ok()
                and (int(width) % 2 == 0)
                and (int(height) % 2 == 0)
                and os.environ.get("CUTORA_EXPORT_YUV420P", "1") != "0"
            )
            self._use_yuv420p_pipe = _use_yuv420p
            self._cur_export_frame = 0
            proc = self._start_ffmpeg_rawvideo(width, height, fps, temp_video, log_file, export_sar)
            if proc is None:
                self.error.emit("无法启动 FFmpeg 编码进程")
                return
            from cutora.render.pipe_writer import FfmpegPipeWriter
            self._pipe_stop.clear()
            pipe_writer = FfmpegPipeWriter(
                proc.stdin, stop_event=self._pipe_stop,
                canvas_wh=(width, height),
                use_yuv420p=_use_yuv420p)
            pipe_writer.start()
            self.ffmpeg_pid = getattr(proc, "pid", None)
            _probe.configure()
            if _probe.on() and self.ffmpeg_pid:
                _probe.attach_ffmpeg(self.ffmpeg_pid)
            if not self.video_only:
                try:
                    from cutora.render.export_guard import write_serial_export_state
                    write_serial_export_state(self.ffmpeg_pid, self.output_path)
                except Exception:
                    pass
            self._pre_audio_th = None
            self._pre_audio_result = None
            if not self.video_only and not self.frame_range:
                try:
                    self._start_pre_encode_audio(project, self.output_path)
                except Exception:
                    pass

            # === 多轨道视频源管理 ===
            # 收集所有视频轨道的 source_id → path 映射
            _frame_states = {} # source_id → {"last_time": -1.0, "last_frame": None}
            _video_tracks_for_export = []  # [(track, clip_list)]
            _src_paths = {}    # source_id → path（按需打开，见 _ExportCapPool）
            _src_last_end = {} # source_id → 该源最后一段结束的时间线时刻

            if tl is not None:
                from cutora.model.track_guard import video_tracks_for_render
                for tr in video_tracks_for_render(project):
                    clips = getattr(tr, "clips", []) or []
                    _video_tracks_for_export.append(tr)
                    for clip_obj in clips:
                        sid = getattr(clip_obj, "source_id", None)
                        if not sid:
                            continue
                        if sid not in _src_paths:
                            src = tl.sources.get(sid) if tl else None
                            src_path = getattr(src, "path", None) or (src.get("path") if isinstance(src, dict) else None) if src else None
                            if src_path and os.path.exists(src_path):
                                _src_paths[sid] = src_path
                        try:
                            _cs = float(getattr(clip_obj, "start_t", 0.0) or 0.0)
                            _eff = float(getattr(clip_obj, "effective_duration", 0.0) or 0.0)
                            if _eff <= 0:
                                _eff = max(0.0, float(getattr(clip_obj, "out_t", 0.0)) - float(getattr(clip_obj, "in_t", 0.0)))
                            _ce = _cs + _eff
                        except Exception:
                            _ce = float("inf")
                        if bool(getattr(clip_obj, "loop", False)):
                            _ce = float("inf")
                        _src_last_end[sid] = max(_src_last_end.get(sid, 0.0), _ce)
            _max_open_caps = max(EXPORT_MAX_OPEN_CAPS, len(_video_tracks_for_export) + 2)
            _video_caps = _ExportCapPool(
                _src_paths, _src_last_end, _frame_states, max_open=_max_open_caps
            )
            _LOG.info("[Export] 视频源 %d 个，解码器按需开、最多同时 %d 路（软件解码）",
                      len(_src_paths), _max_open_caps)

            # Fallback：如果没有 Track clips，使用旧逻辑
            fallback_clip = None
            overlay_path = None
            if not _video_tracks_for_export:
                clip_source = self._get_primary_video_source(project)
                if clip_source:
                    overlay_path = getattr(clip_source, "path", None) or (clip_source.get("path") if isinstance(clip_source, dict) else None)
                if not overlay_path:
                    vo = project.get("video_overlay") or {}
                    overlay_path = vo.get("path") if isinstance(vo, dict) else None
                if not overlay_path:
                    overlay_path = project.get("video")
                if overlay_path:
                    cap = _open_video_capture(overlay_path, hw=export_hw_decode()) or cv2.VideoCapture(overlay_path)
                    if cap and cap.isOpened():
                        _frame_states["_fallback"] = {"last_time": -1.0, "last_frame": None, "src_path": overlay_path}
                    fallback_clip = {
                        "start_t": 0.0, "in_t": 0.0, "out_t": duration,
                        "fit_mode": DEFAULT_VIDEO_FIT_MODE, "scale": 1.0, "x": 0.0, "y": 0.0, "rotation": 0.0,
                    }

            # 在循环外一次性计算渲染顺序（由 Timeline 排序决定）
            export_render_order = compute_export_render_order(project)

            # [v6.1] 同样在循环外一次性构建覆层时间区间索引，
            # 把每帧的线性扫描换成二分查找。判断结果与原逻辑完全等价。
            export_overlay_index = ExportRenderer.build_overlay_index(project)
            from cutora.render.grade import prepare_grade_plan
            prepare_grade_plan(project)
            from cutora.render.split_layout import normalize_split as _norm_split
            if isinstance(project, dict) and project.get("split"):
                project["_export_normed_split"] = _norm_split(project.get("split"))
            from cutora.render.textfx.spec import frame_has_glass_bg, task_has_glass_bg
            _task_glass = task_has_glass_bg(project)

            # 8 帧画布池：合成线程独占一张，写线程独占队列里的
            mad_refs = []

            # 渲染起点裁剪（trim_start_sec）：直接从裁剪点对应的帧开始渲染
            _trim_start_sec = float(project.get("trim_start_sec") or 0.0)
            _trim_start_frame = max(0, int(_trim_start_sec * fps))
            _end_frame = int(total_frames)
            if self.frame_range:
                # Q160 分段：只画这一段；t 仍是绝对时间线时刻（frame_idx / fps），和整条导出一样
                _trim_start_frame = max(_trim_start_frame, int(self.frame_range[0]))
                _end_frame = max(_trim_start_frame, min(int(total_frames), int(self.frame_range[1])))
                _LOG.info("[Export] 分段 %d–%d / %d 帧", _trim_start_frame, _end_frame, total_frames)
            _mad_want = {
                min(total_frames - 1, _trim_start_frame + 1),
                _trim_start_frame + max(0, (total_frames - _trim_start_frame) // 2),
                max(_trim_start_frame, total_frames - 3),
            }

            from cutora.render.compose_parallel import (
                ExportComposeCtx,
                resolve_compose_workers,
            )
            n_compose = resolve_compose_workers(getattr(self, "compose_workers", None))
            # Q163：视频和覆层按时间线交错叠压时，视频不能先拍成一张再叠覆层 → 走串行合成
            from cutora.render.visual_stack import build_stack as _vs_build, needs_stack as _vs_needs
            _vstack = _vs_build(project)
            # Q180：有时间线视频轨 → 解码线程只取帧、合成线程画整帧（和串行同一套代码），
            # 统一叠压（视频压字 / 调整图层 / 混合模式）也能多线程，抠像遮罩也分摊到各线程
            _replay = bool(_video_tracks_for_export) and cap is None
            if not _replay and _vs_needs(project, stack=_vstack) and not _stack_parallel_ok(_vstack):
                if n_compose > 1:
                    _LOG.info("[Export] 视频和文字 / 字幕交错叠压，改用串行合成")
                n_compose = 1
            _LOG.info(
                "[Export] compose workers=%d cpu=%s",
                n_compose, os.cpu_count())

            def _serial_ctx():
                return ExportComposeCtx(
                    project=project,
                    width=width,
                    height=height,
                    video_tracks=_video_tracks_for_export,
                    video_caps=_video_caps,
                    frame_states=_frame_states,
                    fallback_cap=cap,
                    overlay_path=overlay_path,
                    fallback_clip=fallback_clip,
                    render_order=export_render_order,
                    overlay_index=export_overlay_index,
                    task_glass=_task_glass,
                    stack=_vstack,
                )

            def _worker_ctx():
                isolated = dict(project)
                for _k in ("segments", "text_overlays", "image_overlays"):
                    _items = isolated.get(_k) or []
                    isolated[_k] = [
                        dict(x) if isinstance(x, dict) else x for x in _items]
                return ExportComposeCtx(
                    project=isolated,
                    width=width,
                    height=height,
                    video_tracks=_video_tracks_for_export,
                    video_caps=_ReplayCaps(_src_paths) if _replay else None,
                    frame_states={},
                    fallback_cap=None,
                    overlay_path=overlay_path,
                    fallback_clip=fallback_clip,
                    render_order=export_render_order,
                    overlay_index=export_overlay_index,
                    task_glass=_task_glass,
                    stack=_vstack,
                )

            _fps_t0 = time.perf_counter()
            _fps_samples = [(0, _fps_t0)]

            def _on_compose_progress(frame_idx, total):
                if _probe.on():
                    _probe.sample_ffmpeg()
                # 进度按「这次要画的范围」算（裁剪起点 / 分段起点之后）
                self.progress.emit(int(min(100, max(0, (frame_idx - _trim_start_frame)
                                                    / max(1, _end_frame - _trim_start_frame) * 100))))
                _done = frame_idx - _trim_start_frame
                _now_t = time.perf_counter()
                _fps_samples.append((_done, _now_t))
                _win_cap = 5 if _done >= 240 else 10
                while len(_fps_samples) > _win_cap:
                    _fps_samples.pop(0)
                if len(_fps_samples) >= 2 and (_done - _fps_samples[0][0]) >= 60:
                    _exp_fps = export_fps(
                        _done - _fps_samples[0][0],
                        max(1e-3, _now_t - _fps_samples[0][1]),
                    )
                else:
                    _exp_fps = export_fps(_done, _now_t - _fps_t0)
                try:
                    self.export_stats.emit(
                        float(_exp_fps), int(frame_idx), int(total))
                except Exception:
                    _LOG.exception("export_stats 发送失败")
                try:
                    from cutora.core.paths import resolve_user_data
                    from cutora.render.export_guard import STATE_NAME, stop_flag_exists
                    if stop_flag_exists(os.path.join(resolve_user_data(), STATE_NAME)):
                        self.stop()
                except Exception:
                    _LOG.exception("读取导出停止标志失败")
                try:
                    from cutora.render.export_guard import read_degrade
                    job_dir = os.environ.get("CUTORA_JOB_DIR") or ""
                    _dg = read_degrade(job_dir) if job_dir else None
                    # BUG-40：降级只对「正在用 NVENC 的这一路」有意义。
                    # 以前任何 level（pause_spawn / serial_rest 也算）都会
                    # 把所有在编的 worker —— 包括早就在用 CPU 的 —— 在第 30
                    # 帧掐掉，然后 6/6 记失败、整批退回串行重跑。
                    _lvl = str((_dg or {}).get("level") or "gpu_off")
                    if _dg and _lvl == "gpu_off" and self.use_gpu:
                        self._degrade_abort = True
                        self.stop()
                except Exception:
                    _LOG.exception("读取导出降级标志失败")

            if n_compose > 1:
                from cutora.render.compose_parallel import run_compose_pool
                from cutora.render.export_guard import (
                    mem_per_worker_gb, record_worker_rss,
                )
                budget = mem_per_worker_gb((width, height))
                peak_gb = run_compose_pool(
                    render_worker=self,
                    decode_ctx=_serial_ctx(),
                    overlay_ctx_factory=_worker_ctx,
                    frame_start=_trim_start_frame,
                    frame_end=_end_frame,
                    fps=fps,
                    width=width,
                    height=height,
                    n_workers=n_compose,
                    pipe_writer=pipe_writer,
                    is_running=lambda: self._is_running,
                    stop_event=self._pipe_stop,
                    mad_want=_mad_want,
                    mad_refs=mad_refs,
                    on_progress=_on_compose_progress,
                    total_frames=total_frames,
                    budget_gb=budget,
                    requested=n_compose,
                    replay=_replay,
                )
                try:
                    record_worker_rss(peak_gb, (width, height), os.getpid())
                except Exception:
                    _LOG.exception("写入 worker_rss.json 失败")
            else:
                _canvas = pipe_writer.acquire_canvas()
                if _canvas is None:
                    self.stop()
                ctx = _serial_ctx()
                frame_idx = _trim_start_frame
                while self._is_running and frame_idx < _end_frame and _canvas is not None:
                    t = frame_idx / fps
                    _probe.begin("overlay")
                    try:
                        _canvas = self._compose_export_frame(t, _canvas, ctx)
                        if _probe.on():
                            if _task_glass and frame_has_glass_bg(project, t):
                                _probe.note("glass")
                            _probe.note("frames")
                    finally:
                        _probe.end("overlay")

                    _t_pipe = time.perf_counter()
                    if frame_idx in _mad_want and len(mad_refs) < 3:
                        out_t = (frame_idx - _trim_start_frame) / max(1.0, float(fps))
                        if not any(abs(r[0] - out_t) < 1e-6 for r in mad_refs):
                            mad_refs.append((out_t, _canvas.copy()))
                    if not pipe_writer.put(_canvas):
                        raise BrokenPipeError("FFmpeg Encoder Error")
                    _canvas = None
                    if _probe.on():
                        _probe.add_ms("pipe", time.perf_counter() - _t_pipe)

                    frame_idx += 1
                    self._cur_export_frame = frame_idx - _trim_start_frame
                    if frame_idx == _trim_start_frame + 1:
                        _fps_t0 = time.perf_counter()
                        _fps_samples.clear()
                        _fps_samples.append((1, _fps_t0))
                    if frame_idx < _end_frame and self._is_running:
                        _t_acq = time.perf_counter()
                        _canvas = pipe_writer.acquire_canvas()
                        if _probe.on():
                            _probe.add_ms("pipe", time.perf_counter() - _t_acq)
                        if _canvas is None:
                            break
                    if frame_idx % 30 == 0:
                        _on_compose_progress(frame_idx, total_frames)

            if pipe_writer is not None:
                if self._is_running and not getattr(self, "_degrade_abort", False):
                    pipe_writer.close()
                else:
                    pipe_writer.abort()
                pipe_writer = None
            if proc.stdin:
                try:
                    proc.stdin.close()
                except Exception:
                    _LOG.exception("关闭 ffmpeg stdin 失败")
            retcode = proc.wait()
            if getattr(self, "_degrade_abort", False):
                self.error.emit("导出中途收到降级，需 CPU 重做")
                return
            if retcode != 0:
                if log_path:
                    self.error.emit(f"导出失败 (code={retcode})，请查看详细日志：\n{log_path}")
                else:
                    self.error.emit(f"导出失败 (code={retcode})")
                return

            if not os.path.exists(temp_video) or os.path.getsize(temp_video) < 1024 * 10:
                if log_path:
                    self.error.emit(f"导出文件异常，请查看日志：\n{log_path}")
                else:
                    self.error.emit("导出文件异常")
                return

            if self.video_only:
                # Q160 分段子进程：只交出这一段的视频文件，混音 / 验收 / 旁车 / 平台副本由父进程拼接后做
                import shutil as _sh
                _sh.move(temp_video, self.output_path)
                self.produced_paths = [self.output_path]
                self.progress.emit(100)
                if _emit_done:
                    self.finished.emit()
                return

            if _probe.on():
                print("EXPORT_PROBE", _probe.dump(), flush=True)
            self.finish_from_video(temp_video, project, duration=duration, total_frames=total_frames,
                                   fps=fps, width=width, height=height, plat_copies=_plat_copies,
                                   mad_refs=mad_refs, emit_done=_emit_done)

        except BrokenPipeError:
            # BrokenPipeError 不在此处吞掉，让 run() 的 GPU→CPU 自动回退逻辑捕获
            raise
        except Exception as e:
            if self.export_logs:
                self.error.emit(f"渲染发生错误: {str(e)}\n如有需要请查看日志: UserData/logs/export/")
            else:
                self.error.emit(f"渲染发生错误: {str(e)}")
        finally:
            try:
                from cutora.render.decode_prefetch import shutdown_all
                shutdown_all(list((_frame_states or {}).values()))
            except Exception:
                _LOG.exception("解码预读线程回收失败")
            if pipe_writer is not None:
                try:
                    pipe_writer.abort()
                except Exception:
                    _LOG.exception("ffmpeg 管道写线程停止失败")
                pipe_writer = None
            # 释放所有 VideoCapture 资源
            if cap:
                try:
                    cap.release()
                except Exception:
                    _LOG.exception("释放 VideoCapture 失败")
            try:
                _video_caps.clear()   # 先 join 预读线程再 release
            except Exception:
                _LOG.exception("释放多源 VideoCapture 失败")
            self.__dict__.pop("_loop_qimg_cache", None)
            self.__dict__.pop("_direct_bgr_recipes", None)
            self.__dict__.pop("_fast_cell_elig", None)
            cleanup_dbi_video_caps()  # 释放挂载视频 VideoCapture
            if log_file:
                try:
                    log_file.close()
                except Exception:
                    _LOG.exception("关闭 ffmpeg 日志失败")
            if proc:
                try:
                    if proc.poll() is None:
                        proc.kill()
                except Exception:
                    _LOG.exception("结束 ffmpeg 进程失败")
            if temp_video and os.path.exists(temp_video):
                try:
                    os.remove(temp_video)
                except Exception:
                    _LOG.exception("删除临时视频失败")
            pre_a = (self.output_path + ".pre_audio.m4a") if self.output_path else None
            if pre_a and os.path.exists(pre_a):
                try:
                    os.remove(pre_a)
                except Exception:
                    pass

    # === WYSIWYG Export Override (ASS removed) ===
    def run_ffmpeg_cmd(self, *args, **kwargs):
        raise RuntimeError("ASS export removed")

    def generate_ass(self, *args, **kwargs):
        return None

    # render_wysiwyg / render_wysiwyg_export 唯一定义在上方 (line ~2115)
    # ⚠️ 此处不再重复定义，否则 Python 会覆盖上方正确版本

    def _cleanup_temp_video(self, temp_video):
        try:
            os.remove(temp_video)
        except Exception:
            pass

    def _start_ffmpeg_rawvideo(self, width, height, fps, output_path, log_file, sar=None):
        from cutora.render.export_params import build_rawvideo_ffmpeg_cmd, vcodec_quality_args
        from cutora.render.export_profile import encode_args, resolve_profile
        from cutora.render.parallel import EXPORT_ENC_THREADS
        prof = resolve_profile(task=self.task)
        in_pix = "yuv420p" if getattr(self, "_use_yuv420p_pipe", False) else "bgra"
        cmd = build_rawvideo_ffmpeg_cmd(
            width, height, fps, output_path,
            use_gpu=self.use_gpu,
            export_mode=self.export_mode,
            sar=sar,
            enc_threads=int(EXPORT_ENC_THREADS or 0),
            ffmpeg_path=FFMPEG_PATH,
            profile=prof,
            input_pix_fmt=in_pix,
        )
        vcodec, preset, quality_args, _pix = encode_args(prof, self.use_gpu)
        if not vcodec:
            vcodec, preset, quality_args = vcodec_quality_args(self.use_gpu, self.export_mode)
        _LOG.info(f"[Export] FFmpeg cmd: {' '.join(cmd[:8])}... -c:v {vcodec} -preset {preset} {' '.join(quality_args)} -pix_fmt yuv420p")
        try:
            return _no_window_popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=log_file if log_file else subprocess.DEVNULL,
                bufsize=10 * 1024 * 1024  # 10MB 缓冲，大幅提升写入效率
            )
        except Exception:
            return None

    def _paint_export_background(self, painter, t, ctx, tracks=None, bottom=True):
        """Video layer + prefetch. Glass reads the composited layer, not a source frame.

        Q163：tracks 给了就只画这几条（统一叠压时的一段视频带）；bottom=False 不画整幅背景图。"""
        project = ctx.project
        width, height = ctx.width, ctx.height
        _video_tracks_for_export = ctx.video_tracks if tracks is None else [
            tr for tr in ctx.video_tracks if any(tr is x for x in tracks)]
        _video_caps = ctx.video_caps
        _frame_states = ctx.frame_states
        cap = ctx.fallback_cap
        overlay_path = ctx.overlay_path
        fallback_clip = ctx.fallback_clip
        from cutora.render.split_layout import split_enabled as _split_on
        from cutora.render import backdrop as _bg
        _bg.begin_frame(project)          # Q155 整幅背景：片段自己的背景填充让位
        if _video_tracks_for_export and _split_on(project):
            def _paint_one(dest, _tr, dw, dh):
                _active_trans = None
                for _trans in getattr(_tr, "transitions", []) or []:
                    if str(getattr(_trans, "domain", "visual") or "visual") == "audio":
                        continue
                    if _trans.start_t <= t <= (_trans.start_t + _trans.duration):
                        _active_trans = _trans
                        break
                _active_clips = None
                if _active_trans is None:
                    _active_clips = self._find_active_clips_in_track_export(_tr, t)
                    if not _active_clips:
                        try:
                            dest._split_drew = False
                        except Exception:
                            pass
                        return
                try:
                    dest._split_drew = True
                except Exception:
                    pass
                from cutora.render.track_transform import (
                    paint_track_layer, track_needs_layer,
                )
                _layering = track_needs_layer(_tr)
                if _layering or _active_trans is not None:
                    _limg = getattr(dest, "_split_layer_img", None)
                    if _limg is not None:
                        _limg.fill(0)
                        dest._split_layer_img = None

                def _paint_export_track(
                        dest2, _track=_tr, _trans=_active_trans, _use_layer=_layering,
                        _w=dw, _h=dh, _clips=_active_clips):
                    if _trans is not None:
                        self._paint_export_transition(
                            dest2, _track, _trans, t, _w, _h,
                            lambda c: _video_caps.get(getattr(c, "source_id", None)),
                            lambda c: _frame_states.get(getattr(c, "source_id", None)),
                        )
                        return
                    _active_clips = _clips if _clips is not None else self._find_active_clips_in_track_export(_track, t)
                    if not _active_clips:
                        return
                    from cutora.batch.layer_folders import (
                        blend_of_folder, folder_task_scope, hidden_by_folder,
                    )
                    from cutora.render.child_matte import paint_parent_matted, partition_clips
                    _own, _mattes = partition_clips(project, _track, t, _active_clips)
                    if _mattes:
                        _limg = getattr(dest2, "_split_layer_img", None)
                        if _limg is not None:
                            _limg.fill(0)
                            dest2._split_layer_img = None
                        def _one(p, clip, _fs=_frame_states, _ww=_w, _hh=_h):
                            _cap, _ok = self._cap_or_cached(clip, _video_caps)
                            if not _ok:
                                return
                            _sid = getattr(clip, "source_id", None)
                            self._draw_video_overlay(p, _cap, clip, _ww, _hh, t, _fs.get(_sid))

                        def _blend(clip, _tr=_track, _layer=_use_layer):
                            name = getattr(clip, "blend_mode", None)
                            if not _layer:
                                name = name or getattr(_tr, "blend_mode", "normal")
                            return name or "normal"

                        with folder_task_scope(project):
                            paint_parent_matted(
                                dest2, _w, _h, _own, _mattes, _one, _blend, project)
                        return
                    if len(_own) != 1:
                        _limg = getattr(dest2, "_split_layer_img", None)
                        if _limg is not None:
                            _limg.fill(0)
                            dest2._split_layer_img = None
                    for _active in _own:
                        if hidden_by_folder(project, _active):
                            continue
                        _sid = getattr(_active, "source_id", None)
                        _cap, _ok = self._cap_or_cached(_active, _video_caps)
                        if not _ok:
                            continue
                        _blend_name = getattr(_active, "blend_mode", None)
                        if not _use_layer:
                            _blend_name = _blend_name or getattr(_track, "blend_mode", "normal")
                        else:
                            _blend_name = _blend_name or "normal"
                        _fb = blend_of_folder(project, _active)
                        if _fb and str(_blend_name or "normal") == "normal":
                            _blend_name = _fb
                        if _use_layer or str(_blend_name or "normal") != "normal":
                            _limg = getattr(dest2, "_split_layer_img", None)
                            if _limg is not None:
                                _limg.fill(0)
                                dest2._split_layer_img = None
                        from cutora.render.frame_renderer import paint_blended_video_clip

                        def _draw_clip(p, _c=_cap, _a=_active, _sid=_sid, _ww=_w, _hh=_h):
                            self._draw_video_overlay(p, _c, _a, _ww, _hh, t, _frame_states.get(_sid))

                        with folder_task_scope(project):
                            paint_blended_video_clip(dest2, _blend_name, _use_layer, _draw_clip, _w, _h)

                paint_track_layer(dest, _tr, dw, dh, _paint_export_track)

            def _fast_cell_lookup(_tr, dw, dh, lw, lh, cx, cy):
                if (
                    getattr(_REPLAY_TLS, "record", None) is not None
                    or getattr(_REPLAY_TLS, "replay", None) is not None
                ):
                    return None
                for _trans in getattr(_tr, "transitions", []) or []:
                    if str(getattr(_trans, "domain", "visual") or "visual") == "audio":
                        continue
                    if _trans.start_t <= t <= (_trans.start_t + _trans.duration):
                        return None
                _active_clips = self._find_active_clips_in_track_export(_tr, t)
                if not _active_clips or len(_active_clips) != 1:
                    return None
                _clip = _active_clips[0]
                _elig_map = self.__dict__.setdefault("_fast_cell_elig", {})
                _ekey = (id(_tr), id(_clip))
                _is_elig = _elig_map.get(_ekey)
                if _is_elig is None:
                    try:
                        from cutora.render.track_transform import track_needs_layer
                        from cutora.batch.layer_folders import (
                            blend_of_folder, hidden_by_folder,
                        )
                        from cutora.render.child_matte import partition_clips
                        _own, _mattes = partition_clips(project, _tr, t, _active_clips)
                        _bname = (
                            getattr(_clip, "blend_mode", None)
                            or getattr(_tr, "blend_mode", "normal")
                            or "normal"
                        )
                        _fb = blend_of_folder(project, _clip)
                        if _fb and str(_bname) == "normal":
                            _bname = _fb
                        _is_elig = bool(
                            not track_needs_layer(_tr)
                            and not _mattes
                            and len(_own) == 1
                            and not hidden_by_folder(project, _clip)
                            and str(_bname or "normal") == "normal"
                        )
                    except Exception:
                        _is_elig = False
                    _elig_map[_ekey] = _is_elig
                if not _is_elig:
                    return None
                _sid = getattr(_clip, "source_id", None)
                _fs = _frame_states.get(_sid)
                loop_cache = self.__dict__.get("_loop_qimg_cache")
                if loop_cache:
                    loop_idx = self._loop_clip_src_idx(None, _clip, t, _fs)
                    if loop_idx is not None:
                        lhit = loop_cache.get((id(_clip), loop_idx))
                        if lhit is not None:
                            qimg_hit = lhit[0]
                            if (
                                qimg_hit is not None
                                and qimg_hit.width() == lw
                                and qimg_hit.height() == lh
                            ):
                                if (
                                    _sid
                                    and isinstance(_video_caps, _ExportCapPool)
                                    and _sid in _video_caps
                                ):
                                    _c_st = float(getattr(_clip, "start_t", 0.0) or 0.0)
                                    _c_sp = max(0.01, float(getattr(_clip, "speed", 1.0) or 1.0))
                                    _src_len = float((_fs or {}).get("_src_len") or 0.0) if isinstance(_fs, dict) else 0.0
                                    if _src_len > 0.1 and (t - _c_st) * _c_sp > _src_len + 0.25:
                                        _video_caps._release(_sid)
                                return ("img", qimg_hit)
                recipes = self.__dict__.get("_direct_bgr_recipes")
                if recipes:
                    rkey = (id(_clip), int(dw), int(dh), int(lw), int(lh), int(cx), int(cy))
                    recipe = recipes.get(rkey)
                    if recipe is not None:
                        sx0, sy0, sw_slice, sh_slice, exp_fw, exp_fh = recipe
                        _cap, _ok = self._cap_or_cached(_clip, _video_caps)
                        if _ok:
                            frame = self._get_video_frame_at_time(_cap, _clip, t, _fs)
                            if (
                                isinstance(_fs, dict)
                                and _fs.pop("_pf_kick", False)
                                and _cap is not None
                            ):
                                try:
                                    from cutora.render.decode_prefetch import kick_read
                                    kick_read(_cap, _fs, getattr(self, "_pipe_stop", None))
                                except Exception:
                                    pass
                            if frame is not None:
                                frame = self._apply_clip_crop_frame(frame, _clip)
                                fh, fw = frame.shape[:2]
                                if fw == exp_fw and fh == exp_fh:
                                    sliced = (
                                        frame[sy0:sy0 + sh_slice, sx0:sx0 + sw_slice]
                                        if (sw_slice < fw or sh_slice < fh or sx0 > 0 or sy0 > 0)
                                        else frame
                                    )
                                    if sliced.shape[1] == lw and sliced.shape[0] == lh:
                                        return ("bgr", sliced)
                return None

            _paint_one._supports_lazy_clear = True
            _paint_one._fast_cell_lookup = _fast_cell_lookup
            from cutora.render.split_screen import paint_split_video_tracks
            paint_split_video_tracks(
                painter, project, _video_tracks_for_export, width, height, _paint_one,
                bottom=bottom)
        elif _video_tracks_for_export:
            if getattr(painter, "_canvas_needs_clear", False):
                dev = painter.device()
                if isinstance(dev, QImage):
                    dev.fill(Qt.GlobalColor.black)
                painter._canvas_needs_clear = False
            if bottom:
                _bg.paint_bg_image(painter, project, width, height)
            for _tr in _video_tracks_for_export:
                _active_trans = None
                for _trans in getattr(_tr, "transitions", []) or []:
                    if str(getattr(_trans, "domain", "visual") or "visual") == "audio":
                        continue
                    if _trans.start_t <= t <= (_trans.start_t + _trans.duration):
                        _active_trans = _trans
                        break

                from cutora.render.track_transform import (
                    paint_track_layer, track_needs_layer,
                )
                _layering = track_needs_layer(_tr)

                def _paint_export_track(
                        dest, _track=_tr, _trans=_active_trans, _use_layer=_layering):
                    if _trans is not None:
                        self._paint_export_transition(
                            dest, _track, _trans, t, width, height,
                            lambda c: _video_caps.get(getattr(c, "source_id", None)),
                            lambda c: _frame_states.get(getattr(c, "source_id", None)),
                        )
                        return

                    _active_clips = self._find_active_clips_in_track_export(_track, t)
                    if not _active_clips:
                        return
                    from cutora.batch.layer_folders import (
                        blend_of_folder, folder_task_scope, hidden_by_folder,
                    )
                    from cutora.render.child_matte import paint_parent_matted, partition_clips
                    _own, _mattes = partition_clips(project, _track, t, _active_clips)
                    if _mattes:
                        def _one(p, clip, _fs=_frame_states):
                            _cap, _ok = self._cap_or_cached(clip, _video_caps)
                            if not _ok:
                                return
                            _sid = getattr(clip, "source_id", None)
                            self._draw_video_overlay(
                                p, _cap, clip, width, height, t, _fs.get(_sid))

                        def _blend(clip, _tr=_track, _layer=_use_layer):
                            name = getattr(clip, "blend_mode", None)
                            if not _layer:
                                name = name or getattr(_tr, "blend_mode", "normal")
                            return name or "normal"

                        with folder_task_scope(project):
                            paint_parent_matted(
                                dest, width, height, _own, _mattes, _one, _blend, project)
                        return
                    for _active in _own:
                        if hidden_by_folder(project, _active):
                            continue
                        _sid = getattr(_active, "source_id", None)
                        _cap, _ok = self._cap_or_cached(_active, _video_caps)
                        if not _ok:
                            continue
                        _blend_name = getattr(_active, "blend_mode", None)
                        if not _use_layer:
                            _blend_name = _blend_name or getattr(_track, "blend_mode", "normal")
                        else:
                            _blend_name = _blend_name or "normal"
                        _fb = blend_of_folder(project, _active)
                        if _fb and str(_blend_name or "normal") == "normal":
                            _blend_name = _fb
                        from cutora.render.frame_renderer import paint_blended_video_clip

                        def _draw_clip(p, _c=_cap, _a=_active, _sid=_sid):
                            self._draw_video_overlay(p, _c, _a, width, height, t, _frame_states.get(_sid))

                        with folder_task_scope(project):
                            paint_blended_video_clip(dest, _blend_name, _use_layer, _draw_clip, width, height)

                paint_track_layer(painter, _tr, width, height, _paint_export_track)
        elif cap and cap.isOpened() and overlay_path:
            clip = self._get_active_video_clip(project, t) or fallback_clip
            if clip:
                self._draw_video_overlay(painter, cap, clip, width, height, t, _frame_states.get("_fallback"))
        _bg.end_frame()
        try:
            _video_caps.evict_done(t)
        except Exception:
            _LOG.exception("释放已播完的解码器失败")
        self._kick_export_prefetch(_video_caps, _frame_states, cap)

    def _compose_export_frame(self, t, canvas, ctx):
        """One export frame. Shared by the serial loop and compose workers."""
        def _draw_bg(p, _t=t):
            self._paint_export_background(p, _t, ctx)
        _draw_bg._handles_canvas_clear = bool(ctx.video_tracks)

        def _band(p, tracks, index, _t=t):
            # Q163：统一叠压 —— 一段视频带（和覆层交错时逐段画）
            p._canvas_cleared_black = (index == 0)
            self._paint_export_background(p, _t, ctx, tracks=tracks, bottom=(index == 0))
            p._canvas_cleared_black = False
        return ExportRenderer.render_frame(
            ctx.project, ctx.width, ctx.height, t,
            draw_background=_draw_bg,
            render_order=ctx.render_order,
            canvas=canvas,
            fast=False,
            overlay_index=ctx.overlay_index,
            paint_video_band=_band,
            stack=getattr(ctx, "stack", None),
        )

    def _record_video_fetch(self, t, ctx):
        """Q180 解码线程：按串行合成同样的顺序（统一叠压时逐段视频带）把这一帧要的源帧全取出来、记下。
        不画、不抠像 —— 那些都在合成线程里做。返回 {(片段, t, 过渡把手): [(缓存图, 帧, 状态, 标记)]}。"""
        from cutora.render.visual_stack import build_stack, needs_stack, video_bands
        rec = {}
        tiny = self.__dict__.get("_fetch_canvas")
        if tiny is None:
            tiny = self._fetch_canvas = QImage(4, 4, QImage.Format.Format_ARGB32_Premultiplied)
        st = ctx.stack if getattr(ctx, "stack", None) is not None else build_stack(ctx.project)
        p = QPainter(tiny)
        _REPLAY_TLS.record = rec
        try:
            if isinstance(ctx.project, dict) and needs_stack(ctx.project, stack=st):
                for i, band in enumerate(video_bands(st)):
                    self._paint_export_background(p, t, ctx, tracks=band, bottom=(i == 0))
            else:
                self._paint_export_background(p, t, ctx)
        finally:
            _REPLAY_TLS.record = None
            p.end()
        return rec

    def _compose_replay_frame(self, t, canvas, ctx, rec):
        """Q180 合成线程：和串行一模一样地画整帧，只是源帧从 rec 里拿。"""
        _REPLAY_TLS.replay = rec
        try:
            return self._compose_export_frame(t, canvas, ctx)
        finally:
            _REPLAY_TLS.replay = None

    def _compose_video_frame(self, t, ctx):
        """Video layer only, sequential decode. Overlay is painted separately."""
        from PySide6.QtCore import Qt
        video = QImage(
            int(ctx.width), int(ctx.height),
            QImage.Format.Format_ARGB32_Premultiplied)
        video.fill(Qt.GlobalColor.black)
        painter = QPainter(video)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            self._paint_export_background(painter, t, ctx)
        finally:
            painter.end()
        # Same object the preview path hands to glass: the painted video layer.
        # No copy, and no BGR→QImage of a cached source frame.
        backdrop = None
        if getattr(ctx, "task_glass", False):
            from cutora.render.textfx.spec import frame_has_glass_bg
            if frame_has_glass_bg(ctx.project, t):
                backdrop = video
        return video, backdrop

    def _compose_overlay_frame(self, t, canvas, ctx, video, backdrop):
        def _draw_bg(p, _img=video):
            p.drawImage(0, 0, _img)
        st = getattr(ctx, "stack", None)
        band = None
        if st is not None and _stack_parallel_ok(st):
            # Q175：只有一段视频带、垫在最底下（典型：上面有调整图层）→ 解码线程拍好的视频层就是这段带，
            # 合成线程按叠压顺序画覆层 / 调整图层。和串行逐像素一样（不透明整图贴在黑底上 = 原样拷贝）。
            def band(p, _tracks, _index, _img=video):
                p.drawImage(0, 0, _img)
        return ExportRenderer.render_frame(
            ctx.project, ctx.width, ctx.height, t,
            draw_background=_draw_bg,
            render_order=ctx.render_order,
            canvas=canvas,
            fast=False,
            overlay_index=ctx.overlay_index,
            backdrop=backdrop,
            paint_video_band=band,
            stack=st if band is not None else None,
        )

    def _kick_export_prefetch(self, video_caps, frame_states, fallback_cap):
        if os.environ.get("CUTORA_EXPORT_PREFETCH", "1") == "0":
            return
        from cutora.render.decode_prefetch import kick_read
        stop = getattr(self, "_pipe_stop", None)
        for sid, cap in (video_caps or {}).items():
            st = (frame_states or {}).get(sid)
            if st and st.pop("_pf_kick", False):
                kick_read(cap, st, stop)
        st = (frame_states or {}).get("_fallback")
        if st and st.pop("_pf_kick", False) and fallback_cap is not None:
            kick_read(fallback_cap, st, stop)

    def _write_qimage_to_ffmpeg(self, proc, canvas):
        try:
            from cutora.render.pipe_writer import qimage_to_bgra_bytes
            proc.stdin.write(qimage_to_bgra_bytes(canvas))
            return True
        except Exception:
            _LOG.exception("同步写入 ffmpeg 管道失败")
            return False

    def export_audio_filter_complex(self, project, *, has_video=True, solo_ids=None,
                                    preview_sfx=True):
        """Same -filter_complex string _mix_audio_tracks muxes. Empty if no tracks."""
        tracks = self._build_audio_tracks(
            project, solo_ids=solo_ids, preview_sfx=preview_sfx)
        target_sr = int(self.export_audio_rate) if isinstance(self.export_audio_rate, (int, float)) and self.export_audio_rate > 0 else 48000
        src_volume = float(project.get("src_volume") if project.get("src_volume") is not None else 1.0)
        if tracks and src_volume != 1.0:
            for t in tracks:
                if t.get("is_main") and abs(float(t.get("gain_db") or 0.0)) < 0.01:
                    t["gain_db"] = 20.0 * math.log10(max(src_volume, 1e-6))
        if not tracks:
            return "", tracks
        fx_settings = project.get("audio_fx") if isinstance(project.get("audio_fx"), dict) else None
        if fx_settings is None and _SFX is not None:
            try:
                fx_settings = _SFX.load_fx_settings()
            except Exception:
                fx_settings = None
        audio_index0 = 1 if has_video else 0
        filter_parts = []
        for idx, t in enumerate(tracks):
            gain_db = float(t.get("gain_db") or 0.0)
            trim_in = float(t.get("trim_in") or 0.0)
            duration = t.get("duration")
            if duration is not None:
                duration = max(0.01, float(duration))
            start_sec = float(t.get("start") or 0.0)
            pitch = float(t.get("pitch_semitones") or 0.0)
            fade_in_d = float(t.get("fade_in_dur") or 0.0)
            fade_out_d = float(t.get("fade_out_dur") or 0.0)
            fade_curve = str(t.get("fade_curve") or "tri")
            if fade_curve not in ("tri", "esin"):
                fade_curve = "tri"
            label_in = f"{idx + audio_index0}:a"
            label_out = f"a{idx}"
            src_dur = t.get("source_duration")
            try:
                src_dur = float(src_dur) if src_dur is not None else 0.0
            except (TypeError, ValueError):
                src_dur = 0.0
            loop = bool(t.get("loop", False))
            try:
                speed = float(t.get("speed") or 1.0)
            except (TypeError, ValueError):
                speed = 1.0
            if speed <= 0:
                speed = 1.0
            src_span = t.get("src_span")
            if src_span is None:
                src_span = duration
            else:
                try:
                    src_span = float(src_span)
                except (TypeError, ValueError):
                    src_span = duration
            played_src = ((src_dur - trim_in) / speed) if src_dur > 0 else 0.0
            need_loop = bool(
                loop and duration is not None and src_dur > 0.05
                and float(duration) > played_src + 0.08)

            from cutora.audio.speed_curve import curve_graph, segments as speed_segments
            speed_rows = [] if need_loop else speed_segments(
                src_span or 0.0, speed, t.get("speed_curve") or [])
            if speed_rows:
                built = curve_graph(
                    label_in, idx, trim_in, src_span, speed,
                    t.get("speed_curve"), target_sr)
                prefix, joined = built
                post = ""
                extra = str(t.get("denoise_chain") or "")
                if extra:
                    post += "," + extra
                if pitch != 0:
                    rate = pow(2.0, pitch / 12.0)
                    post += f",asetrate={target_sr}*{rate},atempo={1.0/rate},aresample={target_sr}"
                from cutora.render.keyframe import ffmpeg_gain_filter
                kf_vol = ffmpeg_gain_filter(t.get("_clip"), gain_db)
                if kf_vol:
                    post += kf_vol
                elif gain_db != 0:
                    post += f",volume={gain_db}dB"
                curve_arg = f":curve={fade_curve}" if fade_curve != "tri" else ""
                if fade_in_d > 0:
                    post += f",afade=t=in:st=0:d={fade_in_d}{curve_arg}"
                if fade_out_d > 0 and duration is not None:
                    fade_out_start = max(0.0, duration - fade_out_d)
                    post += f",afade=t=out:st={fade_out_start}:d={fade_out_d}{curve_arg}"
                body = post[1:] if post.startswith(",") else post
                if body:
                    chain = "%s;[%s]%s[%s]" % (prefix, joined, body, label_out)
                else:
                    chain = "%s;[%s]anull[%s]" % (prefix, joined, label_out)
                filter_parts.append(chain)
                continue
            from cutora.audio.loop_seam import seam_prefix, seam_seconds, want_seam
            seam_chain = None
            if need_loop and want_seam(t):
                one_src = max(0.05, src_dur - max(0.0, trim_in))
                one_loop = max(0.05, one_src / speed) if abs(speed - 1.0) >= 1e-9 else one_src
                xf = seam_seconds(one_loop)
                trim_len = duration if duration is not None else one_loop
                if xf >= 0.02:
                    looped = "lp%d" % idx
                    seam_chain = seam_prefix(
                        label_in, looped, trim_in=trim_in, target_sr=target_sr,
                        speed=speed, one_loop=one_loop, trim_len=trim_len, xf=xf,
                        atempo=atempo_chain)
                    # anull 垫一下，后面的音量 / 头尾淡化还能用逗号接上。没有这个键就走下面原来的 aloop。
                    seam_chain += ";[%s]anull" % looped
            if seam_chain:
                chain = seam_chain
            else:
                chain = f"[{label_in}]"
            if not seam_chain and need_loop:
                if trim_in > 0:
                    chain += f"atrim=start={trim_in},"
                chain += f"asetpts=N/SR/TB,aresample={target_sr}"
                one_src = max(0.05, src_dur - max(0.0, trim_in))
                if abs(speed - 1.0) >= 1e-9:
                    chain += atempo_chain(speed)
                    one_loop = max(0.05, one_src / speed)
                else:
                    one_loop = one_src
                samples = max(1, int(one_loop * target_sr))
                trim_len = duration if duration is not None else one_loop
                chain += f",aloop=loop=-1:size={samples}:start=0,atrim=duration={trim_len},asetpts=N/SR/TB"
            elif not seam_chain:
                if trim_in > 0 or src_span is not None:
                    if src_span is not None:
                        chain += f"atrim=start={trim_in}:duration={src_span},"
                    else:
                        chain += f"atrim=start={trim_in},"
                chain += f"asetpts=N/SR/TB,aresample={target_sr}"
                if abs(speed - 1.0) >= 1e-9:
                    chain += atempo_chain(speed)
            extra = str(t.get("denoise_chain") or "")
            if extra:
                chain += "," + extra
            if pitch != 0:
                rate = pow(2.0, pitch / 12.0)
                chain += f",asetrate={target_sr}*{rate},atempo={1.0/rate},aresample={target_sr}"
            from cutora.render.keyframe import ffmpeg_gain_filter
            kf_vol = ffmpeg_gain_filter(t.get("_clip"), gain_db)
            if kf_vol:
                chain += kf_vol
            elif gain_db != 0:
                chain += f",volume={gain_db}dB"
            curve_arg = f":curve={fade_curve}" if fade_curve != "tri" else ""
            if fade_in_d > 0:
                chain += f",afade=t=in:st=0:d={fade_in_d}{curve_arg}"
            if fade_out_d > 0 and duration is not None:
                fade_out_start = max(0.0, duration - fade_out_d)
                chain += f",afade=t=out:st={fade_out_start}:d={fade_out_d}{curve_arg}"
            spat_on, spat_preset, spat_wet, spat_width = False, "stage", 0.0, 0.0
            if _SPATIAL is not None:
                try:
                    spat_on, spat_preset, spat_wet, spat_width = _SPATIAL.resolve_spatial_for_track(t, fx_settings)
                except Exception:
                    _LOG.exception("resolve_spatial_for_track failed path=%s", t.get("path"))
                    spat_on = False
            if spat_on and spat_wet > 0:
                hall = ""
                if _SPATIAL is not None:
                    try:
                        hall = _SPATIAL.build_hall_reverb_filter(
                            spat_preset, spat_wet, spat_width,
                            uid=idx, src_path=t.get("path"),
                            mic=_SPATIAL.mic_amount(t, fx_settings))
                    except Exception:
                        _LOG.exception("build_hall_reverb_filter failed path=%s", t.get("path"))
                        hall = ""
                if hall:
                    chain += f",{hall}"
                else:
                    chain += f",extrastereo=m={min(1.0, max(0.0, spat_wet))}"
            elif bool(t.get("spatial_enabled", False)):
                spatial_amount = min(1.0, max(0.0, float(t.get("spatial_amount", 0.0))))
                if spatial_amount > 0:
                    chain += f",extrastereo=m={spatial_amount}"
            if start_sec > 0:
                delay_ms = int(start_sec * 1000)
                chain += f",adelay={delay_ms}|{delay_ms}"
            chain += f"[{label_out}]"
            filter_parts.append(chain)

        from cutora.render.export_params import audio_mix_trim_tail
        _trim_tail = audio_mix_trim_tail(
            float(project.get("trim_start_sec") or 0.0),
            float(project.get("trim_end_sec") or 0.0))
        from cutora.audio.ducking import ducked_filter
        ducked = ducked_filter(filter_parts, tracks, project, _trim_tail)
        if ducked is not None:
            return ducked, tracks
        if len(tracks) == 1:
            filter_complex = f"{filter_parts[0]};[a0]anull{_trim_tail}[aout]"
        else:
            mix_inputs = "".join(f"[a{i}]" for i in range(len(tracks)))
            filter_complex = (
                ";".join(filter_parts)
                + f";{mix_inputs}amix=inputs={len(tracks)}:duration=longest:dropout_transition=0:normalize=0"
                + f",alimiter=limit=0.97:level=false{_trim_tail}[aout]"
            )
        return filter_complex, tracks

    def _start_pre_encode_audio(self, project, output_path):
        if getattr(self, "_pre_audio_th", None) is not None or not output_path:
            return
        from cutora.render.export_profile import resolve_profile
        acodec = str((resolve_profile(task=project) or {}).get("audio_codec") or "aac")
        if acodec != "aac":
            return

        def _worker():
            try:
                time.sleep(0.15 + (os.getpid() % 10) * 0.12)
                if not getattr(self, "_is_running", True):
                    return
                fc, tracks = self.export_audio_filter_complex(project, has_video=False)
                if not tracks:
                    return
                from cutora.core.proc import run_filter_script
                from cutora.audio.loudness import apply_filter, get_loudness_settings, measure_file, parse_measured
                target_sr = int(self.export_audio_rate) if isinstance(self.export_audio_rate, (int, float)) and self.export_audio_rate > 0 else 48000
                cmd_before = [FFMPEG_PATH, "-y", "-threads", "1"]
                _trim_end_s = float(project.get("trim_end_sec") or 0.0)
                for t in tracks:
                    if _trim_end_s > 0.0 and not t.get("loop"):
                        _t_in = float(t.get("trim_in") or 0.0)
                        _spd = max(0.1, float(t.get("speed") or 1.0))
                        _max_in_dur = _t_in + _trim_end_s * _spd + 2.0
                        cmd_before += ["-t", f"{_max_in_dur:.2f}", "-i", t["path"]]
                    else:
                        cmd_before += ["-i", t["path"]]
                ln = get_loudness_settings()
                report = {
                    "mode": ln.mode,
                    "target_lufs": ln.target_i,
                    "applied": False,
                    "measured_lufs": None,
                }
                pre_audio = output_path + ".pre_audio.m4a"
                encode_tail = ["-vn", "-c:a", "aac", "-ar", str(int(target_sr)), "-ac", "2"]
                if ln.enabled:
                    from cutora.audio.loudness import measure_filter
                    wav = output_path + ".lnmeas.wav"
                    fc_meas = "%s;[aout]asplit=2[awav][ameas];[ameas]%s[anull]" % (
                        fc, measure_filter(ln.target_i, ln.target_tp)
                    )
                    res = run_filter_script(
                        cmd_before, fc_meas,
                        [
                            "-map", "[awav]", "-vn", "-c:a", "pcm_s16le", "-ar", str(int(target_sr)), "-ac", "2", wav,
                            "-map", "[anull]", "-vn", "-f", "null", "NUL" if os.name == "nt" else "/dev/null",
                        ],
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    )
                    if res.returncode != 0:
                        res = run_filter_script(
                            cmd_before, fc,
                            ["-map", "[aout]", "-vn", "-c:a", "pcm_s16le", "-ar", str(int(target_sr)), "-ac", "2", wav],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                        )
                    if res.returncode != 0:
                        try:
                            os.remove(wav)
                        except OSError:
                            pass
                        return
                    try:
                        try:
                            first_err = (res.stderr or b"").decode("utf-8", errors="replace")
                            measured = parse_measured(first_err)
                        except Exception:
                            measured = measure_file(FFMPEG_PATH, wav, ln.target_i, ln.target_tp)
                        report["input_i"] = measured.get("input_i")
                        report["applied"] = True
                        af = apply_filter(measured, ln.target_i, ln.target_tp)
                        mux = [FFMPEG_PATH, "-y", "-threads", "1", "-i", wav, "-af", af] + encode_tail + [pre_audio]
                        res = _no_window_run(mux, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                        try:
                            mux_err = (res.stderr or b"").decode("utf-8", errors="replace")
                            outm = parse_measured(mux_err)
                            report["measured_lufs"] = float(outm.get("output_i"))
                        except Exception:
                            pass
                    except Exception:
                        mux = [FFMPEG_PATH, "-y", "-threads", "1", "-i", wav] + encode_tail + [pre_audio]
                        res = _no_window_run(mux, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                    try:
                        os.remove(wav)
                    except OSError:
                        pass
                else:
                    res = run_filter_script(
                        cmd_before, fc,
                        ["-map", "[aout]"] + encode_tail + [pre_audio],
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    )
                if res.returncode == 0 and os.path.isfile(pre_audio) and os.path.getsize(pre_audio) > 512:
                    self._pre_audio_result = (pre_audio, report)
            except Exception:
                pass

        th = threading.Thread(target=_worker, name="cutora-pre-audio", daemon=True)
        self._pre_audio_th = th
        th.start()

    def _mix_audio_tracks(self, temp_video, project, output_path):
        has_video = bool(temp_video)
        pre_th = getattr(self, "_pre_audio_th", None)
        if pre_th is not None:
            try:
                pre_th.join(timeout=120)
            except Exception:
                pass
            self._pre_audio_th = None
        pre_res = getattr(self, "_pre_audio_result", None)
        self._pre_audio_result = None
        if has_video and pre_res is not None:
            pre_audio, pre_report = pre_res
            if pre_audio and os.path.isfile(pre_audio):
                mux = [
                    FFMPEG_PATH, "-y", "-i", temp_video, "-i", pre_audio,
                    "-map", "0:v:0", "-map", "1:a:0",
                    "-c:v", "copy", "-c:a", "copy", "-movflags", "+faststart",
                    output_path,
                ]
                res = _no_window_run(mux, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                try:
                    os.remove(pre_audio)
                except OSError:
                    pass
                if res.returncode == 0 and os.path.isfile(output_path):
                    self._loudness_report = pre_report
                    return True

        filter_complex, tracks = self.export_audio_filter_complex(
            project, has_video=has_video)

        if not tracks:
            # No audio: skip loudnorm, do not insert silence, do not error.
            if not temp_video:
                return True
            try:
                if os.path.exists(output_path):
                    os.remove(output_path)
                shutil.move(temp_video, output_path)
                return True
            except Exception as e:
                self.error.emit(f"输出文件移动失败: {e}")
                return False

        cmd_before = [FFMPEG_PATH, "-y"]
        if has_video:
            cmd_before += ["-i", temp_video]
        for t in tracks:
            cmd_before += ["-i", t["path"]]

        maps = (["-map", "0:v:0"] if has_video else []) + ["-map", "[aout]"]
        from cutora.core.proc import run_filter_script
        target_sr = int(self.export_audio_rate) if isinstance(self.export_audio_rate, (int, float)) and self.export_audio_rate > 0 else 48000
        from cutora.render.export_profile import resolve_profile
        acodec = str((resolve_profile(task=project) or {}).get("audio_codec") or "aac")
        encode_tail = []
        if has_video:
            encode_tail += ["-c:v", "copy"]
        encode_tail += [
            "-c:a", acodec,
            "-ar", str(int(target_sr)),
            "-ac", "2",
        ]
        if has_video:
            encode_tail += ["-movflags", "+faststart"]
        from cutora.audio.loudness import apply_filter, get_loudness_settings, measure_file, parse_measured
        ln = get_loudness_settings()
        self._loudness_report = {
            "mode": ln.mode,
            "target_lufs": ln.target_i,
            "applied": False,
            "measured_lufs": None,
        }
        if ln.enabled:
            wav = output_path + ".lnmeas.wav"
            from cutora.audio.loudness import measure_filter
            fc_audio, _ = self.export_audio_filter_complex(project, has_video=False)
            cmd_audio_only = [FFMPEG_PATH, "-y"]
            for t in tracks:
                cmd_audio_only += ["-i", t["path"]]
            fc_meas = "%s;[aout]asplit=2[awav][ameas];[ameas]%s[anull]" % (
                fc_audio, measure_filter(ln.target_i, ln.target_tp))
            res = run_filter_script(
                cmd_audio_only, fc_meas,
                [
                    "-map", "[awav]", "-vn",
                    "-c:a", "pcm_s16le", "-ar", str(int(target_sr)), "-ac", "2",
                    wav,
                    "-map", "[anull]", "-vn", "-f", "null", "NUL" if os.name == "nt" else "/dev/null",
                ],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if res.returncode != 0:
                res = run_filter_script(
                    cmd_before, filter_complex,
                    [
                        "-map", "[aout]", "-vn",
                        "-c:a", "pcm_s16le", "-ar", str(int(target_sr)), "-ac", "2",
                        wav,
                    ],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if res.returncode != 0:
                err_txt = (res.stderr or b"").decode("utf-8", errors="replace")[-1200:]
                print("[AudioMix] loudness mix-wav failed:\n", err_txt)
                try:
                    os.remove(wav)
                except OSError:
                    pass
                self.error.emit("音频混音失败，请查看日志")
                return False
            try:
                try:
                    First_err = (res.stderr or b"").decode("utf-8", errors="replace")
                    measured = parse_measured(First_err)
                except Exception:
                    measured = measure_file(FFMPEG_PATH, wav, ln.target_i, ln.target_tp)
                self._loudness_report["input_i"] = measured.get("input_i")
                self._loudness_report["applied"] = True
                af = apply_filter(measured, ln.target_i, ln.target_tp)
                if has_video:
                    mux = [
                        FFMPEG_PATH, "-y", "-i", temp_video, "-i", wav,
                        "-filter_complex", "[1:a]%s[aout]" % af,
                    ] + maps + encode_tail + [output_path]
                else:
                    mux = [
                        FFMPEG_PATH, "-y", "-i", wav, "-af", af,
                    ] + encode_tail + [output_path]
                res = _no_window_run(mux, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            except Exception as exc:
                _LOG.warning("loudnorm two-pass failed, mux without: %s", exc)
                if has_video:
                    mux = [
                        FFMPEG_PATH, "-y", "-i", temp_video, "-i", wav,
                        "-map", "0:v:0", "-map", "1:a:0",
                    ] + encode_tail + [output_path]
                else:
                    mux = [FFMPEG_PATH, "-y", "-i", wav] + encode_tail + [output_path]
                res = _no_window_run(mux, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                os.remove(wav)
            except OSError:
                pass
            if res.returncode != 0:
                err_txt = (res.stderr or b"").decode("utf-8", errors="replace")[-1200:]
                print("[AudioMix] ffmpeg failed:\n", err_txt)
                self.error.emit("音频混音失败，请查看日志")
                return False
            try:
                mux_err = (res.stderr or b"").decode("utf-8", errors="replace")
                outm = parse_measured(mux_err)
                self._loudness_report["measured_lufs"] = float(outm.get("output_i"))
            except Exception:
                pass
            return True

        res = run_filter_script(
            cmd_before, filter_complex,
            maps + encode_tail + [output_path],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if res.returncode != 0:
            err_txt = ""
            try:
                err_txt = (res.stderr or b"").decode("utf-8", errors="replace")[-1200:]
            except Exception:
                err_txt = ""
            print("[AudioMix] ffmpeg failed:\n", err_txt)
            self.error.emit("音频混音失败，请查看日志")
            return False
        return True

    def _paint_export_transition(self, dest, track, trans, t, w, h, cap_for, state_for):
        """Shared export/preview compositor. Calls compose_standalone_transition."""
        from cutora.render.transition_runtime import paint_standalone_transition

        def render_clip(clip_obj):
            if clip_obj is None:
                return None
            cap2 = cap_for(clip_obj) if callable(cap_for) else None
            if not (cap2 and cap2.isOpened()):
                return None
            offscreen = QImage(int(w), int(h), QImage.Format.Format_ARGB32_Premultiplied)
            offscreen.fill(0)
            p2 = QPainter(offscreen)
            p2.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            try:
                st = state_for(clip_obj) if callable(state_for) else None
                self._draw_video_overlay(
                    p2, cap2, clip_obj, int(w), int(h), t, st, extend_handles=True)
            finally:
                p2.end()
            return offscreen

        paint_standalone_transition(dest, track, trans, t, w, h, render_clip)

    def _cap_or_cached(self, clip, video_caps):
        """Open a decoder only if the clip is not already in the GIF frame cache.

        Export keeps at most EXPORT_MAX_OPEN_CAPS live. Looping stills would
        otherwise be opened, evicted, and re-opened every frame.
        """
        if getattr(_REPLAY_TLS, "replay", None) is not None:
            return None, True
        from cutora.media.frame_cache import ensure, should_cache_clip
        sid = getattr(clip, "source_id", None)
        path = None
        if video_caps is not None and hasattr(video_caps, "_paths"):
            path = (video_caps._paths or {}).get(sid)
        if path and should_cache_clip(clip, path):
            if ensure(path) is not None:
                return None, True
        cap = video_caps.get(sid) if video_caps is not None else None
        if cap is not None and cap.isOpened():
            return cap, True
        return None, False

    def _source_path_of_clip(self, clip, cap, frame_state):
        if isinstance(frame_state, dict):
            p = frame_state.get("src_path")
            if p:
                return p
        p = getattr(cap, "path", None)
        if p:
            return p
        project = getattr(self, "project", None)
        if not isinstance(project, dict):
            return None
        tl = project.get("timeline")
        sources = getattr(tl, "sources", None) if tl is not None else None
        if sources is None and isinstance(tl, dict):
            sources = tl.get("sources")
        sid = getattr(clip, "source_id", None) if not isinstance(clip, dict) else clip.get("source_id")
        src = (sources or {}).get(sid) if sid and sources else None
        if src is None:
            return None
        if isinstance(src, dict):
            return src.get("preview_path") or src.get("path")
        return getattr(src, "preview_path", None) or getattr(src, "path", None)

    def _clip_has_crop(self, clip) -> bool:
        for k in ("crop_left", "crop_right", "crop_top", "crop_bottom"):
            try:
                v = getattr(clip, k, 0) if not isinstance(clip, dict) else clip.get(k, 0)
            except Exception:
                v = 0
            if int(v or 0) > 0:
                return True
        return False

    def _cached_anim_qimage(self, cap, clip, t, frame_state, extend_handles=False):
        from cutora.media.frame_cache import qimage_at, should_cache_clip
        path = self._source_path_of_clip(clip, cap, frame_state)
        if not path or not should_cache_clip(clip, path):
            return None
        if self._clip_has_crop(clip):
            return None
        feather = int(getattr(clip, "feather", 0) if not isinstance(clip, dict) else clip.get("feather", 0) or 0)
        if feather > 0:
            return None
        from cutora.render.transition_runtime import source_duration_of, source_time_for_clip
        fps_hint = 30.0
        if isinstance(frame_state, dict) and frame_state.get("src_fps"):
            try:
                fps_hint = float(frame_state.get("src_fps") or 30.0) or 30.0
            except (TypeError, ValueError):
                fps_hint = 30.0
        elif cap is not None:
            try:
                _sf = cap.get(cv2.CAP_PROP_FPS)
                if _sf and _sf > 0:
                    fps_hint = float(_sf)
            except Exception:
                pass
        src_len = source_duration_of(clip, cap=cap, frame_state=frame_state)
        src_t, _frozen = source_time_for_clip(
            clip, t, src_len, fps=fps_hint, extend=bool(extend_handles))
        if src_t is None:
            return None
        qimg = qimage_at(path, float(src_t), cap=cap, fps_hint=fps_hint)
        if qimg is None or qimg.isNull():
            return None
        if isinstance(frame_state, dict):
            frame_state["src_path"] = path
            frame_state["src_fps"] = fps_hint
        return qimg

    def _preview_shrink(self, painter, clip, frame, target_rect):
        """Q175 只在预览、而且是降分辨率合成（播放 / 拖动）时：解码出来的整幅帧先按屏幕上真正画出来的
        像素缩小（cv2 整 2 倍减半，不占 GIL），后面的调色 / 抠像 / 羽化都在小图上做 —— 绿幕抠像
        这种逐像素的活一下子少做四分之三。暂停时满分辨率合成不走这里，画面和导出逐像素一样；导出永远不走。
        AI 抠像要按源像素对缓存，不缩。"""
        try:
            tf = painter.worldTransform()
            dev = math.hypot(tf.m11(), tf.m12())
            psc = float(getattr(self, "_preview_scale", 1.0) or 1.0)
            if 0.0 < psc < dev:
                dev = psc
            if not (0.0 < dev < 0.999):
                return frame, frame.shape[1], frame.shape[0]
            from cutora.fx import graph as _fxg
            if (getattr(clip, "ai_matte", None) if not isinstance(clip, dict) else clip.get("ai_matte")) \
                    or _fxg.ai_nodes(clip):
                return frame, frame.shape[1], frame.shape[0]
            fh, fw = frame.shape[:2]
            want_w = float(target_rect.width()) * dev
            want_h = float(target_rect.height()) * dev
            if want_w < 16 or want_h < 16 or fw < want_w * 2.0 or fh < want_h * 2.0:
                return frame, fw, fh
            # 只按整 2 倍往下减半（INTER_AREA 的 2× 快速路径，1080×1920 不到 1 毫秒），
            # 不小于屏幕上要的尺寸；剩下不到 2 倍的交给画的时候平滑缩放
            small = frame
            while small.shape[1] >= want_w * 2.0 and small.shape[0] >= want_h * 2.0 \
                    and small.shape[1] >= 32 and small.shape[0] >= 32:
                h2, w2 = small.shape[0] // 2, small.shape[1] // 2
                small = cv2.resize(small[:h2 * 2, :w2 * 2], (w2, h2), interpolation=cv2.INTER_AREA)
            return small, small.shape[1], small.shape[0]
        except Exception:
            return frame, frame.shape[1], frame.shape[0]

    def _loop_clip_src_idx(self, cap, clip, t, frame_state, extend_handles=False):
        """Return discrete source frame index for short looping clips eligible for _loop_qimg_cache."""
        if getattr(self, "_fx_preview", False):
            return None
        try:
            loop = bool(getattr(clip, "loop", False) if not isinstance(clip, dict) else clip.get("loop", False))
            if not loop:
                return None
            in_t = float(getattr(clip, "in_t", 0.0) if not isinstance(clip, dict) else clip.get("in_t", 0.0))
            out_t = float(getattr(clip, "out_t", in_t) if not isinstance(clip, dict) else clip.get("out_t", in_t))
            src_span = out_t - in_t
            if not (0.1 <= src_span <= 20.0):
                return None
            speed = max(0.01, float(getattr(clip, "speed", 1.0) if not isinstance(clip, dict) else clip.get("speed", 1.0)))
            eff_dur = float(getattr(clip, "effective_duration", 0.0)) if hasattr(clip, "effective_duration") else (src_span / speed)
            if eff_dur <= 0:
                eff_dur = src_span / speed
            if eff_dur <= src_span * 1.05:
                return None
            start_t = float(getattr(clip, "start_t", 0.0) if not isinstance(clip, dict) else clip.get("start_t", 0.0))
            if (t < start_t or t >= start_t + eff_dur) and not extend_handles:
                return None
            elig = self.__dict__.setdefault("_loop_clip_elig", {})
            cid = id(clip)
            ok = elig.get(cid)
            if ok is None:
                from cutora.render.keyframe import has_keyframes
                from cutora.fx import graph as _fxg
                from cutora.render.textfx.fade import fade_offset_tuple, fade_tuple, offsets_active
                preset = getattr(clip, "transition_preset", "none") if not isinstance(clip, dict) else clip.get("transition_preset", "none")
                has_tr = False
                tl = _tl_of(getattr(self, "project", None) or getattr(self, "task", None))
                for tr in getattr(tl, "tracks", []) or []:
                    if clip in (getattr(tr, "clips", []) or []):
                        if getattr(tr, "transitions", None):
                            has_tr = True
                        break
                ok = bool(
                    not has_keyframes(clip)
                    and not ((getattr(clip, "ai_matte", None) if not isinstance(clip, dict) else clip.get("ai_matte")) or _fxg.ai_nodes(clip))
                    and (not preset or preset == "none")
                    and not has_tr
                    and max(fade_tuple(clip)) <= 0
                    and not offsets_active(fade_offset_tuple(clip))
                )
                elig[cid] = ok
            if not ok:
                return None
            from cutora.render.transition_runtime import source_duration_of, source_time_for_clip
            fps_hint = 30.0
            if isinstance(frame_state, dict) and frame_state.get("src_fps"):
                fps_hint = float(frame_state["src_fps"] or 30.0) or 30.0
            elif cap is not None:
                _sf = cap.get(cv2.CAP_PROP_FPS)
                if _sf and _sf > 0:
                    fps_hint = float(_sf)
                    if isinstance(frame_state, dict):
                        frame_state["src_fps"] = fps_hint
            if isinstance(frame_state, dict) and frame_state.get("_src_len") is not None:
                src_len = frame_state["_src_len"]
            else:
                src_len = source_duration_of(clip, cap=cap, frame_state=frame_state)
                if isinstance(frame_state, dict):
                    frame_state["_src_len"] = src_len
            src_t, _frozen = source_time_for_clip(
                clip, t, src_len, fps=fps_hint, extend=bool(extend_handles))
            if src_t is None:
                return None
            return max(0, timeline_frame_index(max(0.0, float(src_t)), fps_hint))
        except Exception:
            return None

    def _fetch_clip_frame(self, cap, clip, t, frame_state, extend_handles=False):
        """取这个片段在 t 的源帧：(缓存的 QImage 或 None, 解码帧 或 None, frame_state, 源帧标记)。

        Q180：合成线程（_REPLAY_TLS.replay）从解码线程的记录里拿；解码线程（_REPLAY_TLS.record）照常解码并记下。
        源帧标记 = 「是不是同一张源帧」（重复帧复用处理结果用），None = 不复用。"""
        key = (id(clip), float(t), bool(extend_handles))
        replay = getattr(_REPLAY_TLS, "replay", None)
        if replay is not None:
            lst = replay.get(key)
            if not lst:
                return None, None, frame_state, None
            return lst.pop(0)
        loop_idx = self._loop_clip_src_idx(cap, clip, t, frame_state, extend_handles=extend_handles)
        if loop_idx is not None:
            loop_cache = self.__dict__.get("_loop_qimg_cache")
            if loop_cache is not None:
                lhit = loop_cache.get((id(clip), loop_idx))
                if lhit is not None:
                    if isinstance(frame_state, dict):
                        frame_state.pop("_pf_kick", None)
                        if frame_state.get("_pf_worker") is not None:
                            try:
                                from cutora.render.decode_prefetch import shutdown as _pf_shut
                                _pf_shut(frame_state)
                            except Exception:
                                pass
                    qimg_hit, rect_hit = lhit
                    tok = ("loop_qimg", id(clip), loop_idx, rect_hit)
                    record = getattr(_REPLAY_TLS, "record", None)
                    if record is not None:
                        record.setdefault(key, []).append((qimg_hit, None, None, tok))
                    return qimg_hit, None, frame_state, tok
        qimg_cached = self._cached_anim_qimage(
            cap, clip, t, frame_state, extend_handles=extend_handles)
        frame = None
        token = None
        if qimg_cached is not None:
            token = ("q", int(qimg_cached.cacheKey()))
        else:
            _t0 = time.perf_counter()
            frame = self._get_video_frame_at_time(
                cap, clip, t, frame_state, extend_handles=extend_handles)
            if frame is not None and isinstance(frame_state, dict) and frame_state.get("_gen") is not None:
                if loop_idx is not None:
                    token = ("f_loop", id(frame_state), int(frame_state["_gen"]), int(loop_idx))
                else:
                    token = ("f", id(frame_state), int(frame_state["_gen"]))
            # Q187d：每个源各花了多少「等解码」时间 —— 解码成了瓶颈之后，得知道是哪个文件
            try:
                _dm = self.__dict__.setdefault("_decode_ms", {})
                _k = os.path.basename(str((frame_state or {}).get("src_path") or "?")) if isinstance(frame_state, dict) else "?"
                _e = _dm.get(_k)
                if _e is None:
                    _e = _dm[_k] = [0.0, 0, 0]
                _e[0] += (time.perf_counter() - _t0) * 1000.0
                _e[1] += 1
                if frame is not None:
                    _e[2] = int(frame.shape[1]) * 10000 + int(frame.shape[0])
            except Exception:
                pass
        record = getattr(_REPLAY_TLS, "record", None)
        if record is not None:
            from cutora.render.decode_prefetch import _owning_copy
            fs = None
            if isinstance(frame_state, dict):
                frame_state.pop("_pf_kick", None)
                fs = {k: v for k, v in frame_state.items()
                      if not str(k).startswith("_") and k != "last_frame"}
            if frame is not None:
                # 解码器下一次读可能复用这块内存：合成线程拿走的必须是自己的一份
                frame = _owning_copy(frame)
                if not frame.flags["OWNDATA"]:
                    frame = frame.copy()
            record.setdefault(key, []).append((qimg_cached, frame, fs, token))
        return qimg_cached, frame, frame_state, token

    def _fx_memo_get(self, key, compute):
        """同一张源帧、同样的画法 → 处理结果只算一次（多线程时后来的等先来的算完）。"""
        lock = self.__dict__.get("_fx_memo_lock")
        if lock is None:
            lock = self.__dict__.setdefault("_fx_memo_lock", threading.Lock())
        with lock:
            memo = self.__dict__.setdefault("_fx_memo", {})
            inflight = self.__dict__.setdefault("_fx_inflight", {})
            hit = memo.get(key)
            if hit is not None:
                return hit
            ev = inflight.get(key)
            owner = ev is None
            if owner:
                ev = inflight[key] = threading.Event()
        if not owner:
            ev.wait(30.0)
            with lock:
                hit = memo.get(key)
            if hit is not None:
                return hit
            return compute()
        try:
            val = compute()
            with lock:
                memo[key] = val
                while len(memo) > FX_MEMO_MAX:
                    memo.pop(next(iter(memo)))
            return val
        finally:
            with lock:
                inflight.pop(key, None)
            ev.set()

    def _export_shrink_fx(self, painter, clip, frame, target_rect, t, needs_bg_fill=False):
        """Q180 导出：带抠像 / 遮罩或分屏小格子的片段，源比它在画面上占的地方大得多（Luca 的绿幕人物 1280×704 缩到
        482×265，或 4 分屏每格 1080×1920 / 704×1280 缩到 240×453）→ 先按画面上真正的像素数缩小（cv2 不占 GIL），
        画的时候 1:1 贴图，省掉 QPainter 持 GIL 做整幅双线性缩放。
        golden 基线里没有图层效果也没有分屏；AI 抠像按源像素对缓存、有转场缩放的不缩。"""
        try:
            from cutora.fx.layer_fx import layer_has_fx
            has_fx = layer_has_fx(clip)
            in_split = getattr(painter, "_split_layer_rect", None) is not None
            if not has_fx and not in_split:
                return frame, frame.shape[1], frame.shape[0]
            from cutora.fx import graph as _fxg
            if (getattr(clip, "ai_matte", None) if not isinstance(clip, dict) else clip.get("ai_matte")) \
                    or _fxg.ai_nodes(clip):
                return frame, frame.shape[1], frame.shape[0]
            tf = painter.worldTransform()
            if not _axis_aligned(tf):
                return frame, frame.shape[1], frame.shape[0]
            preset = getattr(clip, "transition_preset", "none") if not isinstance(clip, dict) \
                else clip.get("transition_preset", "none")
            if preset and preset != "none":
                return frame, frame.shape[1], frame.shape[0]
            tl = _tl_of(getattr(self, "project", None))
            for tr in getattr(tl, "tracks", []) or []:
                if clip in (getattr(tr, "clips", []) or []):
                    for trans in getattr(tr, "transitions", []) or []:
                        if trans.start_t <= t <= trans.start_t + trans.duration:
                            return frame, frame.shape[1], frame.shape[0]
                    break
            fh, fw = frame.shape[:2]
            want_w = int(round(float(target_rect.width()) * abs(tf.m11())))
            want_h = int(round(float(target_rect.height()) * abs(tf.m22())))
            if needs_bg_fill and not has_fx and min(want_w, want_h) < 360:
                if fw <= fh:
                    want_w = min(fw, max(want_w, 360))
                    want_h = min(fh, max(want_h, int(round(want_w * fh / max(1, fw)))))
                else:
                    want_h = min(fh, max(want_h, 360))
                    want_w = min(fw, max(want_w, int(round(want_h * fw / max(1, fh)))))
            thresh = 1.25 if has_fx else (1.01 if in_split else 1.15)
            if want_w < 16 or want_h < 16 or fw < want_w * thresh or fh < want_h * thresh:
                return frame, fw, fh
            interp = cv2.INTER_AREA if has_fx else cv2.INTER_LINEAR
            small = cv2.resize(frame, (want_w, want_h), interpolation=interp)
            return small, want_w, want_h
        except Exception:
            return frame, frame.shape[1], frame.shape[0]

    def _draw_video_overlay(self, painter, cap, clip, timeline_w, timeline_h, t, frame_state,
                            extend_handles=False):
        qimg_cached, frame, frame_state, src_token = self._fetch_clip_frame(
            cap, clip, t, frame_state, extend_handles=extend_handles)
        if getattr(_REPLAY_TLS, "record", None) is not None:
            return                      # Q180 解码线程：只取帧
        if (
            qimg_cached is None
            and isinstance(src_token, tuple)
            and len(src_token) == 4
            and src_token[0] == "f_loop"
        ):
            loop_cache = self.__dict__.get("_loop_qimg_cache")
            if loop_cache:
                hit = loop_cache.get((id(clip), src_token[3]))
                if hit is not None:
                    qimg_cached, cached_rect = hit
                    src_token = ("loop_qimg", id(clip), src_token[3], cached_rect)
        is_loop_qimg = isinstance(src_token, tuple) and len(src_token) == 4 and src_token[0] == "loop_qimg"
        if is_loop_qimg and qimg_cached is not None:
            _srect_fast = getattr(painter, "_split_layer_rect", None)
            if (
                _srect_fast is not None
                and qimg_cached.width() == int(round(_srect_fast[2]))
                and qimg_cached.height() == int(round(_srect_fast[3]))
            ):
                painter._split_layer_img = None
                painter._split_covered_all = True
                painter._split_drew = True
                if getattr(painter, "_accepts_direct_cell_img", False):
                    painter._split_direct_img = qimg_cached
                    return
                from PySide6.QtCore import QPoint
                painter.drawImage(
                    QPoint(int(round(_srect_fast[0])), int(round(_srect_fast[1]))),
                    qimg_cached,
                )
                return
        if qimg_cached is not None:
            frame = None
            frame_w, frame_h = qimg_cached.width(), qimg_cached.height()
        else:
            if frame is None:
                return
            frame = self._apply_clip_crop_frame(frame, clip)
            frame_h, frame_w = frame.shape[:2]
        if frame_w <= 0 or frame_h <= 0:
            return

        size_mode = getattr(clip, "size_mode", None) or (clip.get("size_mode") if isinstance(clip, dict) else None) or "project"
        fit_mode = getattr(clip, "fit_mode", None) or (clip.get("fit_mode") if isinstance(clip, dict) else None) or DEFAULT_VIDEO_FIT_MODE
        if str(size_mode).lower() == "source":
            fit_mode = "source"
        scale = float(getattr(clip, "scale", 1.0) if not isinstance(clip, dict) else clip.get("scale", 1.0))
        offset_x = float(getattr(clip, "x", 0.0) if not isinstance(clip, dict) else clip.get("x", 0.0))
        offset_y = float(getattr(clip, "y", 0.0) if not isinstance(clip, dict) else clip.get("y", 0.0))
        rotation = float(getattr(clip, "rotation", 0.0) if not isinstance(clip, dict) else clip.get("rotation", 0.0))
        opacity = float(getattr(clip, "opacity", 255) if not isinstance(clip, dict) else clip.get("opacity", 255))
        from cutora.render.keyframe import has_keyframes, resolve_clip_visual
        if has_keyframes(clip):
            vis = resolve_clip_visual(clip, t)
            scale = float(vis["scale"])
            offset_x = float(vis["x"])
            offset_y = float(vis["y"])
            rotation = float(vis["rotation"])
            opacity = float(vis["opacity"])

        # --- Transition & NLE Pipeline for RenderWorker ---
        in_t = float(getattr(clip, "in_t", 0.0) if not isinstance(clip, dict) else clip.get("in_t", 0.0))
        out_t = float(getattr(clip, "out_t", 0.0) if not isinstance(clip, dict) else clip.get("out_t", 0.0))
        speed = float(getattr(clip, "speed", 1.0) if not isinstance(clip, dict) else clip.get("speed", 1.0))
        seg_start = getattr(clip, "start_t", None) if not isinstance(clip, dict) else clip.get("start_t")
        if seg_start is None:
            seg_start = getattr(clip, "start_time", 0.0) if not isinstance(clip, dict) else clip.get("start_time", 0.0)
        seg_start = float(seg_start or 0.0)
        eff_dur = max(0.0, (out_t - in_t) / max(0.01, speed))
        seg_end = seg_start + eff_dur

        t_preset = getattr(clip, "transition_preset", "none") if not isinstance(clip, dict) else clip.get("transition_preset", "none")
        t_dur = float(getattr(clip, "transition_duration", anim_engine.DEFAULT_TRANSITION_DURATION) if not isinstance(clip, dict) else clip.get("transition_duration", anim_engine.DEFAULT_TRANSITION_DURATION))

        # --- NLE Gen-2 Standalone Transition Override ---
        t_start_override = seg_start
        t_end_override = seg_end
        clip_actual_start = seg_start
        clip_actual_end = seg_end

        if (not extend_handles) and getattr(self, "project", None):
            tl = _tl_of(self.project)
            if tl:
                for tr in getattr(tl, "tracks", []) or []:
                    if clip in (getattr(tr, "clips", []) or []):
                        for trans in getattr(tr, "transitions", []) or []:
                            if str(getattr(trans, "domain", "visual") or "visual") == "audio":
                                continue
                            trans_end = trans.start_t + trans.duration
                            if trans.start_t <= t <= trans_end:
                                parts = trans.preset.split("_")
                                base_type = "_".join(parts[:-1]) if parts[-1] in ("in", "out", "both") else trans.preset
                                if abs(clip_actual_end - trans_end) < 0.2 or clip_actual_end <= trans_end:
                                    t_preset = f"{base_type}_out"
                                    t_dur = trans.duration
                                    t_end_override = trans_end
                                elif abs(clip_actual_start - trans.start_t) < 0.2 or clip_actual_start >= trans.start_t:
                                    t_preset = f"{base_type}_in"
                                    t_dur = trans.duration
                                    t_start_override = trans.start_t
                                break
                        break

        t_op, t_scale, t_dx, t_dy = anim_engine.compute_transition_params(
            t, t_start_override, t_end_override, t_preset, t_dur
        )

        if is_loop_qimg:
            qimg = qimg_cached
            target_rect = src_token[3]
        else:
            from cutora.model.project_schema import clip_scale_y
            target_rect = self._compute_fit_rect(
                frame_w, frame_h, timeline_w, timeline_h,
                fit_mode=fit_mode, scale=scale,
                offset_x=offset_x, offset_y=offset_y,
                scale_y=clip_scale_y(clip),
            )

            from cutora.fx.layer_fx import apply_layer_fx, layer_has_fx, mask_px_scale, source_ctx
            from cutora.fx import graph as _fxg
            _needs_src = (getattr(clip, "ai_matte", None) if not isinstance(clip, dict) else clip.get("ai_matte")) \
                or bool(_fxg.ai_nodes(clip))

            # Q180：主视频这类「原样画、不转、不调色、不抠像、没边缘渐隐」的片段可以走 RGB32 快路
            _rgb32_ok = False
            _clip_needs_fill = False
            _is_pv = bool(getattr(self, "_fx_preview", False))
            if (EXPORT_RGB32_FASTPATH and qimg_cached is None and frame is not None
                    and (not _is_pv or _fxg.viewer_for(clip) is None)
                    and frame.ndim == 3 and frame.shape[2] == 3 and abs(rotation) < 1e-9 and not _needs_src):
                try:
                    from cutora.render.fill_bg import fill_spec_at, needs_fill
                    from cutora.render.textfx.fade import fade_offset_tuple, fade_tuple, offsets_active
                    _fspec = fill_spec_at(clip, t)
                    _clip_needs_fill = needs_fill(
                        _fspec, target_rect, timeline_w, timeline_h,
                        split_rect=getattr(painter, "_split_layer_rect", None),
                    )
                    _rgb32_ok = (
                        _axis_aligned(painter.worldTransform())
                        and not layer_has_fx(clip)
                        and int((getattr(clip, "feather", 0) if not isinstance(clip, dict) else clip.get("feather", 0)) or 0) <= 0
                        and max(fade_tuple(clip)) <= 0 and not offsets_active(fade_offset_tuple(clip))
                        and (not _clip_needs_fill
                             or abs(float(_fspec.get("rotation") or 0.0)) < 1e-9))
                except Exception:
                    _rgb32_ok = False

            # 分屏格内 1:1 源帧预裁切：如主视频 1080×1920 画进 1080×1261 格子（y=-337.5 或 y=+412.5），
            # 直接零拷贝切片可视区域，省去 35%~66% 的色彩转换与图层拷贝；若需背景填充则按 3 帧分桶预计算半分辨率模糊图
            _pre_blurred_fill = None
            _orig_target_rect = target_rect
            _srect = getattr(painter, "_split_layer_rect", None)
            _fx_flag = bool(getattr(clip, "flip_x", False) if not isinstance(clip, dict) else clip.get("flip_x", False))
            _fy_flag = bool(getattr(clip, "flip_y", False) if not isinstance(clip, dict) else clip.get("flip_y", False))
            if (
                _clip_needs_fill
                and _rgb32_ok
                and not _is_pv
                and frame is not None
                and not _fx_flag
                and not _fy_flag
                and str(_fspec.get("mode") or "") == "blur"
            ):
                try:
                    from cutora.render.fill_bg import blur_image
                    _fb_cache = self.__dict__.setdefault("_fill_blur_cache", {})
                    _fb_idx = (
                        (src_token[-1] // 4)
                        if (isinstance(src_token, tuple) and src_token and isinstance(src_token[-1], int))
                        else int(round(t * 7.5))
                    )
                    _fb_key = (
                        id(clip),
                        _fb_idx,
                        round(float(_fspec.get("blur") or 0.0), 2),
                        str(_fspec.get("blur_style") or "lens"),
                        round(float(_fspec.get("highlights") or 0.0), 2),
                    )
                    _pre_blurred_fill = _fb_cache.get(_fb_key)
                    if _pre_blurred_fill is None:
                        _sub_bgr = np.ascontiguousarray(frame[::2, ::2])
                        _sub_q = QImage(
                            _sub_bgr.data,
                            _sub_bgr.shape[1],
                            _sub_bgr.shape[0],
                            int(_sub_bgr.strides[0]),
                            QImage.Format.Format_BGR888,
                        )
                        _pre_blurred_fill = blur_image(
                            _sub_q,
                            _fspec["blur"],
                            _fspec["blur_style"],
                            _fspec["highlights"],
                        )
                        if len(_fb_cache) > 64:
                            _fb_cache.clear()
                        _fb_cache[_fb_key] = _pre_blurred_fill
                except Exception:
                    _pre_blurred_fill = None
            _orig_fw_pre_slice = int(frame_w)
            _orig_fh_pre_slice = int(frame_h)
            _slice_sx0 = 0
            _slice_sy0 = 0
            if (
                _srect is not None
                and _rgb32_ok
                and not _is_pv
                and frame is not None
                and abs(t_scale - 1.0) <= 1e-4
                and abs(t_dx) <= 1e-4
                and abs(t_dy) <= 1e-4
                and frame_w == int(round(target_rect.width()))
                and frame_h == int(round(target_rect.height()))
            ):
                rx = int(round(target_rect.x()))
                ry = int(round(target_rect.y()))
                ix0 = max(rx, int(math.floor(_srect[0])))
                iy0 = max(ry, int(math.floor(_srect[1])))
                ix1 = min(rx + frame_w, int(math.ceil(_srect[0] + _srect[2])))
                iy1 = min(ry + frame_h, int(math.ceil(_srect[1] + _srect[3])))
                if ix1 > ix0 and iy1 > iy0 and (ix1 - ix0 < frame_w or iy1 - iy0 < frame_h):
                    if not _clip_needs_fill or _pre_blurred_fill is not None:
                        sx0 = (rx + frame_w - ix1) if _fx_flag else (ix0 - rx)
                        sy0 = (ry + frame_h - iy1) if _fy_flag else (iy0 - ry)
                        _slice_sx0 = int(sx0)
                        _slice_sy0 = int(sy0)
                        frame = frame[sy0:sy0 + (iy1 - iy0), sx0:sx0 + (ix1 - ix0)]
                        frame_h, frame_w = frame.shape[:2]
                        target_rect = QRectF(float(ix0), float(iy0), float(frame_w), float(frame_h))

            _is_split_f_loop = bool(
                _srect is not None
                and isinstance(src_token, tuple)
                and len(src_token) == 4
                and src_token[0] == "f_loop"
            )
            memo_key = None
            if (src_token is not None and not _is_pv and not _needs_src
                    and layer_has_fx(clip)):
                memo_key = (id(clip), src_token, round(float(target_rect.width()), 4),
                            round(float(target_rect.height()), 4))
            elif (src_token is not None and not _needs_src and not layer_has_fx(clip)
                  and not _is_split_f_loop
                  and ((_is_pv and _fxg.viewer_for(clip) is None)
                       or (
                           (_clip_needs_fill or getattr(painter, "_split_layer_rect", None) is not None)
                           and (
                               (isinstance(src_token, tuple) and src_token and src_token[0] in ("f_loop", "q"))
                               or (isinstance(frame_state, dict) and float(frame_state.get("src_fps") or 30.0) < 28.5)
                           )
                       ))):
                from cutora.render.grade import host_needs_grade
                from cutora.render.textfx.fade import fade_offset_tuple, fade_tuple
                _trk = self._track_holding(clip)
                if not host_needs_grade(clip) and not host_needs_grade(_trk):
                    _fx = bool(getattr(clip, "flip_x", False) if not isinstance(clip, dict) else clip.get("flip_x", False))
                    _fy = bool(getattr(clip, "flip_y", False) if not isinstance(clip, dict) else clip.get("flip_y", False))
                    _feather = int(getattr(clip, "feather", 0) if not isinstance(clip, dict) else clip.get("feather", 0) or 0)
                    memo_key = (
                        "pv", id(clip), src_token,
                        round(float(target_rect.width()), 3),
                        round(float(target_rect.height()), 3),
                        fade_tuple(clip), fade_offset_tuple(clip),
                        _fx, _fy, _feather, _rgb32_ok,
                    )

            _can_reuse_qimg = (
                memo_key is None
                and not (
                    isinstance(src_token, tuple)
                    and len(src_token) == 4
                    and src_token[0] == "f_loop"
                )
            )
            _can_reuse_qimg_proc = _can_reuse_qimg or (memo_key is None and _is_split_f_loop)

            from cutora.batch.layer_folders import opacity_mul_current
            base_op = max(0.0, min(1.0, opacity / 255.0)) * t_op * opacity_mul_current(clip)

            if (
                _can_reuse_qimg
                and _rgb32_ok
                and not _clip_needs_fill
                and qimg_cached is None
                and frame is not None
                and not _is_pv
                and not _fx_flag
                and not _fy_flag
                and _srect is not None
                and getattr(painter, "_accepts_direct_cell_img", False)
                and not getattr(painter, "_split_filled", False)
                and base_op >= 0.9999
                and abs(rotation) < 1e-6
                and abs(t_scale - 1.0) <= 1e-4
                and int(getattr(clip, "feather", 0) if not isinstance(clip, dict) else clip.get("feather", 0) or 0) <= 0
                and frame_w == int(round(target_rect.width()))
                and frame_h == int(round(target_rect.height()))
                and int(round(target_rect.x() + t_dx)) == int(round(_srect[0]))
                and int(round(target_rect.y() + t_dy)) == int(round(_srect[1]))
                and frame_w == int(round(_srect[2]))
                and frame_h == int(round(_srect[3]))
                and _axis_aligned(painter.worldTransform())
                and abs(painter.worldTransform().m11() - 1.0) < 1e-3
                and abs(painter.worldTransform().m22() - 1.0) < 1e-3
            ):
                from cutora.render.grade import host_needs_grade
                _trk_g = self._track_holding(clip)
                if not host_needs_grade(clip) and not host_needs_grade(_trk_g):
                    self._log_export_geom(cap, frame_w, frame_h, frame_w, frame_h, timeline_w, timeline_h)
                    painter._split_layer_img = None
                    painter._split_covered_all = True
                    painter._split_drew = True
                    painter._split_direct_bgr = frame
                    if not has_keyframes(clip) and (not t_preset or t_preset == "none"):
                        _rkey = (
                            id(clip),
                            int(timeline_w),
                            int(timeline_h),
                            int(round(_srect[2])),
                            int(round(_srect[3])),
                            int(round(-_srect[0])),
                            int(round(-_srect[1])),
                        )
                        self.__dict__.setdefault("_direct_bgr_recipes", {})[_rkey] = (
                            _slice_sx0,
                            _slice_sy0,
                            int(frame_w),
                            int(frame_h),
                            _orig_fw_pre_slice,
                            _orig_fh_pre_slice,
                        )
                    if (
                        isinstance(frame_state, dict)
                        and frame_state.get("_pf_kick")
                        and cap is not None
                        and os.environ.get("CUTORA_EXPORT_PREFETCH", "1") != "0"
                        and getattr(_REPLAY_TLS, "replay", None) is None
                        and getattr(_REPLAY_TLS, "record", None) is None
                    ):
                        frame_state.pop("_pf_kick", None)
                        try:
                            from cutora.render.decode_prefetch import kick_read
                            kick_read(cap, frame_state, getattr(self, "_pipe_stop", None))
                        except Exception:
                            pass
                    return

            if (
                _is_split_f_loop
                and _rgb32_ok
                and not _clip_needs_fill
                and qimg_cached is None
                and frame is not None
                and not _is_pv
                and not _fx_flag
                and not _fy_flag
                and _srect is not None
                and getattr(painter, "_accepts_direct_cell_img", False)
                and not getattr(painter, "_split_filled", False)
                and base_op >= 0.9999
                and abs(rotation) < 1e-6
                and abs(t_scale - 1.0) <= 1e-4
                and abs(t_dx) <= 1e-4
                and abs(t_dy) <= 1e-4
                and int(getattr(clip, "feather", 0) if not isinstance(clip, dict) else clip.get("feather", 0) or 0) <= 0
                and _axis_aligned(painter.worldTransform())
                and abs(painter.worldTransform().m11() - 1.0) < 1e-3
                and abs(painter.worldTransform().m22() - 1.0) < 1e-3
            ):
                _fw_cov = int(round(target_rect.width()))
                _fh_cov = int(round(target_rect.height()))
                _lw_cov = int(round(_srect[2]))
                _lh_cov = int(round(_srect[3]))
                _ox_cov = int(round(_srect[0] - target_rect.x()))
                _oy_cov = int(round(_srect[1] - target_rect.y()))
                if (
                    _fw_cov >= _lw_cov > 0
                    and _fh_cov >= _lh_cov > 0
                    and _ox_cov >= 0
                    and _oy_cov >= 0
                    and _ox_cov + _lw_cov <= _fw_cov
                    and _oy_cov + _lh_cov <= _fh_cov
                ):
                    from cutora.render.grade import host_needs_grade
                    _trk_g = self._track_holding(clip)
                    if not host_needs_grade(clip) and not host_needs_grade(_trk_g):
                        from cutora.core.rust_engine import rust_bgr_resize_crop_premask_qimg
                        _baked = rust_bgr_resize_crop_premask_qimg(
                            frame,
                            getattr(painter, "_split_cell_mask", None),
                            _lw_cov,
                            _lh_cov,
                            _fw_cov,
                            _fh_cov,
                            _ox_cov,
                            _oy_cov,
                        )
                        if _baked is not None:
                            self._log_export_geom(cap, frame_w, frame_h, _fw_cov, _fh_cov, timeline_w, timeline_h)
                            _lcache = self.__dict__.setdefault("_loop_qimg_cache", {})
                            if len(_lcache) < 4096:
                                _lcache[(id(clip), src_token[3])] = (
                                    _baked,
                                    QRectF(float(_srect[0]), float(_srect[1]), float(_srect[2]), float(_srect[3])),
                                )
                            painter._split_layer_img = None
                            painter._split_covered_all = True
                            painter._split_drew = True
                            painter._split_direct_img = _baked
                            if (
                                isinstance(frame_state, dict)
                                and frame_state.get("_pf_kick")
                                and cap is not None
                                and os.environ.get("CUTORA_EXPORT_PREFETCH", "1") != "0"
                                and getattr(_REPLAY_TLS, "replay", None) is None
                                and getattr(_REPLAY_TLS, "record", None) is None
                            ):
                                frame_state.pop("_pf_kick", None)
                                try:
                                    from cutora.render.decode_prefetch import kick_read
                                    kick_read(cap, frame_state, getattr(self, "_pipe_stop", None))
                                except Exception:
                                    pass
                            return

            def _process(frame=frame, frame_w=frame_w, frame_h=frame_h):
                feather = int(getattr(clip, "feather", 0) if not isinstance(clip, dict) else clip.get("feather", 0))
                if (
                    _rgb32_ok
                    and qimg_cached is None
                    and frame is not None
                    and not _is_pv
                    and feather <= 0
                ):
                    try:
                        from cutora.render.grade import host_needs_grade
                        _trk_g = self._track_holding(clip)
                        if not host_needs_grade(clip) and not host_needs_grade(_trk_g):
                            from cutora.core.rust_engine import rust_bgr_to_rgb32_qimage
                            want_w, want_h = frame_w, frame_h
                            if EXPORT_FX_AT_OUTPUT_RES and _srect is not None and _axis_aligned(painter.worldTransform()):
                                tf = painter.worldTransform()
                                cw = int(round(float(target_rect.width()) * abs(tf.m11())))
                                ch = int(round(float(target_rect.height()) * abs(tf.m22())))
                                if cw >= 16 and ch >= 16 and (frame_w >= cw * 1.01 or frame_h >= ch * 1.01):
                                    want_w, want_h = cw, ch
                            rq = rust_bgr_to_rgb32_qimage(frame, want_w, want_h, reuse=_can_reuse_qimg_proc)
                            if rq is not None:
                                self._log_export_geom(cap, frame_w, frame_h, rq.width(), rq.height(), timeline_w, timeline_h)
                                if _fx_flag or _fy_flag:
                                    rq = rq.mirrored(_fx_flag, _fy_flag)
                                return rq
                    except Exception:
                        pass
                if frame is not None and _is_pv:
                    frame, frame_w, frame_h = self._preview_shrink(painter, clip, frame, target_rect)
                elif frame is not None and EXPORT_FX_AT_OUTPUT_RES:
                    frame, frame_w, frame_h = self._export_shrink_fx(
                        painter, clip, frame, target_rect, t,
                        needs_bg_fill=(_clip_needs_fill and _pre_blurred_fill is None))
                _t_scale = time.perf_counter()
                if qimg_cached is not None:
                    qimg = self._grade_clip_frame(qimg_cached, clip)
                    if _probe.on():
                        _probe.add_ms("scale", time.perf_counter() - _t_scale)
                    self._log_export_geom(cap, frame_w, frame_h, qimg.width(), qimg.height(), timeline_w, timeline_h)
                else:
                    src = frame
                    if feather > 0:
                        scale_factor = min(target_rect.width() / frame_w, target_rect.height() / frame_h)
                        feather_px = int(max(1, round(feather * max(0.01, scale_factor))))
                        src = self._apply_edge_feather_bgra(frame, feather_px)
                    if not src.flags.c_contiguous:
                        src = np.ascontiguousarray(src)
                    if _probe.on():
                        _probe.add_ms("scale", time.perf_counter() - _t_scale)
                    src_h, src_w = src.shape[:2]
                    if src.ndim == 3 and src.shape[2] == 4:
                        src_fmt = QImage.Format.Format_ARGB32_Premultiplied
                    else:
                        src_fmt = QImage.Format.Format_BGR888
                    view = QImage(src.data, src_w, src_h, int(src.strides[0]), src_fmt)
                    qimg = self._grade_clip_frame(view, clip)
                    if qimg is view:
                        qimg = None
                        if _rgb32_ok:
                            try:
                                qimg = QImage(src_w, src_h, QImage.Format.Format_RGB32)
                                dst = np.frombuffer(qimg.bits(), dtype=np.uint8).reshape(
                                    src_h, qimg.bytesPerLine() // 4, 4)[:, :src_w]
                                cv2.cvtColor(src, cv2.COLOR_BGR2BGRA, dst=dst)
                                del dst
                            except Exception:
                                qimg = None
                        if qimg is None:
                            qimg = view.copy()
                    self._log_export_geom(cap, frame_w, frame_h, qimg.width(), qimg.height(), timeline_w, timeline_h)

                flip_x = bool(getattr(clip, "flip_x", False) if not isinstance(clip, dict) else clip.get("flip_x", False))
                flip_y = bool(getattr(clip, "flip_y", False) if not isinstance(clip, dict) else clip.get("flip_y", False))
                if flip_x or flip_y:
                    qimg = qimg.mirrored(flip_x, flip_y)

                from cutora.render.frame_renderer import _fade_qimage
                if _is_pv:
                    from cutora.render.fade_strips import fade_qimage_fast
                    qimg = fade_qimage_fast(qimg, clip)
                else:
                    qimg = _fade_qimage(qimg, clip)
                src_ctx = None
                if frame is not None and _needs_src:
                    try:
                        from cutora.render.transition_runtime import source_duration_of, source_time_for_clip
                        _fps = float((frame_state or {}).get("src_fps") or 30.0) if isinstance(frame_state, dict) else 30.0
                        _src_t, _fz = source_time_for_clip(
                            clip, t, source_duration_of(clip, cap=cap, frame_state=frame_state),
                            fps=_fps or 30.0, extend=bool(extend_handles))
                        if _src_t is not None:
                            src_ctx = source_ctx(clip, _src_t, order="crop_flip")
                    except Exception:
                        src_ctx = None
                _view = _fxg.viewer_for(clip) if _is_pv else None
                return apply_layer_fx(qimg, clip, px_scale=mask_px_scale(qimg.width(), target_rect.width()), src=src_ctx,
                                      view=_view)

            qimg = self._fx_memo_get(memo_key, _process) if memo_key is not None else _process()
            if (isinstance(src_token, tuple) and len(src_token) == 4 and src_token[0] == "f_loop"
                    and qimg is not None and not qimg.isNull()
                    and getattr(painter, "_split_layer_rect", None) is None):
                loop_cache = self.__dict__.setdefault("_loop_qimg_cache", {})
                if len(loop_cache) < 4096:
                    loop_cache[(id(clip), src_token[3])] = (qimg, target_rect)

        # Q117：画面没铺满的地方先垫背景。预览里是同一个 paint_fill，
        # 一份实现两边用 —— 两边各写一遍就是预览好看导出不一样的开始。
        from cutora.batch.layer_folders import opacity_mul_current
        base_op = max(0.0, min(1.0, opacity / 255.0)) * t_op * opacity_mul_current(clip)
        try:
            from cutora.render.fill_bg import fill_spec_at, needs_fill, paint_fill
            _spec = fill_spec_at(clip, t)
            if _spec["mode"] != "none" and needs_fill(
                _spec, _orig_target_rect, timeline_w, timeline_h,
                split_rect=getattr(painter, "_split_layer_rect", None),
            ):
                _fg_opaque = bool(
                    qimg is not None
                    and qimg.format() == QImage.Format.Format_RGB32
                    and base_op >= 0.9999
                    and abs(rotation) < 1e-6
                    and abs(t_scale - 1.0) <= 1e-4
                    and abs(t_dx) <= 1e-4
                    and abs(t_dy) <= 1e-4
                )
                _limg = getattr(painter, "_split_layer_img", None)
                if _limg is not None:
                    if not _fg_opaque:
                        _limg.fill(0)
                    painter._split_layer_img = None
                painter.save()
                painter.setOpacity(base_op)
                painter._fg_axis_opaque = _fg_opaque
                paint_fill(painter, qimg, _spec, timeline_w, timeline_h,
                           target_rect, pre_blurred=_pre_blurred_fill)
                painter._fg_axis_opaque = False
                painter.restore()
        except Exception:
            _LOG.exception("导出背景填充失败")

        _srect = getattr(painter, "_split_layer_rect", None)
        if _srect is not None and abs(rotation) < 1e-6 and qimg is not None:
            try:
                if getattr(painter, "_split_qimg", None) is None:
                    painter._split_qimg = qimg
                else:
                    painter._split_qimg = False
            except Exception:
                pass
        if (
            _srect is not None
            and qimg is not None
            and qimg.format() == QImage.Format.Format_RGB32
            and base_op >= 0.9999
            and abs(rotation) < 1e-6
            and abs(t_scale - 1.0) <= 1e-4
            and (
                getattr(painter, "_split_filled", False)
                or (
                    (target_rect.x() + t_dx) <= _srect[0] + 0.5
                    and (target_rect.y() + t_dy) <= _srect[1] + 0.5
                    and (target_rect.x() + t_dx + target_rect.width()) >= (_srect[0] + _srect[2] - 0.5)
                    and (target_rect.y() + t_dy + target_rect.height()) >= (_srect[1] + _srect[3] - 0.5)
                )
            )
        ):
            painter._split_layer_img = None
            painter._split_covered_all = True
        elif getattr(painter, "_split_layer_img", None) is not None:
            painter._split_layer_img.fill(0)
            painter._split_layer_img = None

        painter.save()
        painter.setOpacity(base_op)

        _t_comp = time.perf_counter()
        if (
            abs(rotation) < 1e-6
            and abs(t_scale - 1.0) <= 0.0001
            and not (t_preset and "zoom_in" in t_preset and abs(t_scale - 1.0) > 0.01)
            and qimg is not None
            and qimg.width() == int(round(target_rect.width()))
            and qimg.height() == int(round(target_rect.height()))
            and (
                _srect is not None
                or (
                    abs((target_rect.x() + t_dx) - round(target_rect.x() + t_dx)) < 1e-3
                    and abs((target_rect.y() + t_dy) - round(target_rect.y() + t_dy)) < 1e-3
                )
            )
            and _axis_aligned(painter.worldTransform())
            and abs(painter.worldTransform().m11() - 1.0) < 1e-3
            and abs(painter.worldTransform().m22() - 1.0) < 1e-3
        ):
            from PySide6.QtCore import QPoint
            _tx = int(round(target_rect.x() + t_dx))
            _ty = int(round(target_rect.y() + t_dy))
            if (
                _srect is not None
                and getattr(painter, "_accepts_direct_cell_img", False)
                and getattr(painter, "_split_covered_all", False)
                and not getattr(painter, "_split_filled", False)
                and _tx == int(round(_srect[0]))
                and _ty == int(round(_srect[1]))
                and qimg.width() == int(round(_srect[2]))
                and qimg.height() == int(round(_srect[3]))
            ):
                painter._split_direct_img = qimg
            else:
                painter.drawImage(
                    QPoint(_tx, _ty),
                    qimg,
                )
        else:
            if t_dx or t_dy:
                painter.translate(t_dx, t_dy)
            center = target_rect.center()
            painter.translate(center)
            if rotation:
                painter.rotate(rotation)
            draw_rect = QRectF(-target_rect.width() / 2, -target_rect.height() / 2, target_rect.width(), target_rect.height())
            if t_preset and "zoom_in" in t_preset and abs(t_scale - 1.0) > 0.01:
                steps = 5
                blur_range = abs(t_scale - 1.0) * 0.4
                painter.setOpacity(max(0.01, base_op / steps))
                for i in range(steps):
                    s = t_scale - (blur_range / 2.0) + i * (blur_range / max(1, steps - 1))
                    painter.save()
                    painter.scale(s, s)
                    painter.drawImage(draw_rect, qimg)
                    painter.restore()
            else:
                if abs(t_scale - 1.0) > 0.0001:
                    painter.scale(t_scale, t_scale)
                painter.drawImage(draw_rect, qimg)
        if _probe.on():
            _probe.add_ms("compose", time.perf_counter() - _t_comp)
            text_meta = getattr(clip, "thumbnail_text", None) if not isinstance(clip, dict) else clip.get("thumbnail_text")
            has_overlay = bool(getattr(clip, "thumbnail_text_as_overlay", False)) if not isinstance(clip, dict) else bool(clip.get("thumbnail_text_as_overlay", False))
            if bool(getattr(clip, "is_thumbnail_leadin", False) if not isinstance(clip, dict) else clip.get("is_thumbnail_leadin", False)):
                if text_meta and not has_overlay:
                    _draw_thumbnail_text_on_rect(painter, text_meta, target_rect)
        painter.restore()
        if (
            not is_loop_qimg
            and isinstance(src_token, tuple)
            and len(src_token) == 4
            and src_token[0] == "f_loop"
            and _srect is not None
            and base_op >= 0.9999
            and abs(rotation) < 1e-6
            and abs(t_scale - 1.0) <= 1e-4
            and abs(t_dx) <= 1e-4
            and abs(t_dy) <= 1e-4
        ):
            try:
                painter._split_loop_bake = (
                    self.__dict__.setdefault("_loop_qimg_cache", {}),
                    (id(clip), src_token[3]),
                    QRectF(
                        float(_srect[0]), float(_srect[1]), float(_srect[2]), float(_srect[3])
                    ),
                )
            except Exception:
                pass
        if (
            isinstance(frame_state, dict)
            and frame_state.get("_pf_kick")
            and cap is not None
            and os.environ.get("CUTORA_EXPORT_PREFETCH", "1") != "0"
            and getattr(_REPLAY_TLS, "replay", None) is None
            and getattr(_REPLAY_TLS, "record", None) is None
        ):
            frame_state.pop("_pf_kick", None)
            try:
                from cutora.render.decode_prefetch import kick_read
                kick_read(cap, frame_state, getattr(self, "_pipe_stop", None))
            except Exception:
                pass

    def _track_holding(self, clip):
        if clip is None:
            return None
        project = getattr(self, "project", None)
        if project is None:
            return None
        try:
            tl = _tl_of(project)
        except Exception:
            tl = project.get("timeline") if isinstance(project, dict) else None
        tracks = getattr(tl, "tracks", None) if tl is not None else None
        if tracks is None and isinstance(tl, dict):
            tracks = tl.get("tracks") or []
        for tr in tracks or []:
            clips = getattr(tr, "clips", None) if not isinstance(tr, dict) else tr.get("clips")
            if clip in (clips or []):
                return tr
        return None

    def _grade_clip_frame(self, qimg, clip):
        """Clip + track grade on this clip's pixels. No-op when the plan is off."""
        project = getattr(self, "project", None)
        if isinstance(project, dict) and project.get("_grade_on") is False:
            return qimg
        try:
            from cutora.render.grade import apply_clip_track_grade
            return apply_clip_track_grade(qimg, clip, self._track_holding(clip))
        except Exception:
            _LOG.exception("片段调色失败")
            return qimg

    def _apply_clip_crop_frame(self, frame, clip):
        try:
            h, w = frame.shape[:2]
            cl = int(getattr(clip, "crop_left", 0) if not isinstance(clip, dict) else clip.get("crop_left", 0))
            cr = int(getattr(clip, "crop_right", 0) if not isinstance(clip, dict) else clip.get("crop_right", 0))
            ct = int(getattr(clip, "crop_top", 0) if not isinstance(clip, dict) else clip.get("crop_top", 0))
            cb = int(getattr(clip, "crop_bottom", 0) if not isinstance(clip, dict) else clip.get("crop_bottom", 0))
            if cl <= 0 and cr <= 0 and ct <= 0 and cb <= 0:
                return frame
            cl = max(0, cl); cr = max(0, cr); ct = max(0, ct); cb = max(0, cb)
            x0 = min(cl, w - 1)
            y0 = min(ct, h - 1)
            x1 = max(x0 + 1, w - cr)
            y1 = max(y0 + 1, h - cb)
            return frame[y0:y1, x0:x1].copy()
        except Exception:
            return frame

    def _apply_edge_feather_bgra(self, frame, feather):
        h, w = frame.shape[:2]
        yy, xx = np.ogrid[:h, :w]
        # 注意：xx 形状为 (1,w)，yy 形状为 (h,1)，不能直接 np.minimum.reduce(list)
        # 否则会触发 numpy: "setting an array element with a sequence"（形状不一致）
        dist = np.minimum(xx, (w - 1) - xx)
        dist = np.minimum(dist, yy)
        dist = np.minimum(dist, (h - 1) - yy)
        t = np.clip(dist / float(max(1, feather)), 0.0, 1.0)
        alpha = (t * t * (3.0 - 2.0 * t) * 255.0).astype(np.uint8)
        alpha_f = (alpha.astype(np.float32) / 255.0)[:, :, np.newaxis]
        bgr_premul = (frame.astype(np.float32) * alpha_f).astype(np.uint8)
        bgra = np.ascontiguousarray(np.dstack([bgr_premul, alpha]))
        return bgra

    def _log_export_geom(self, cap, frame_w, frame_h, qimg_w, qimg_h, tl_w, tl_h):
        try:
            now = time.time()
            if (now - self._geom_last_log_t) <= 1.0:
                return
            self._geom_last_log_t = now
            src_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or frame_w) if cap is not None else int(frame_w)
            src_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or frame_h) if cap is not None else int(frame_h)
            _LOG.info(f"[Export-OpenCV] src={src_w}x{src_h} frame={int(frame_w)}x{int(frame_h)} qimg={int(qimg_w)}x{int(qimg_h)} tl={int(tl_w)}x{int(tl_h)}")
        except Exception:
            pass

    def _compute_fit_rect(self, src_w, src_h, dst_w, dst_h, fit_mode="fit", scale=1.0, offset_x=0.0, offset_y=0.0, scale_y=1.0):
        if src_w <= 0 or src_h <= 0:
            return QRectF(0, 0, dst_w, dst_h)
        fit_mode = (fit_mode or DEFAULT_VIDEO_FIT_MODE).lower()
        sy = float(scale_y if scale_y not in (None, "") else 1.0)
        if sy <= 0:
            sy = 1.0
        if fit_mode == "source":
            tw = src_w * scale
            th = src_h * scale
            if abs(sy - 1.0) > 1e-9:
                th *= sy
            x = (dst_w - tw) / 2 + offset_x
            y = (dst_h - th) / 2 + offset_y
            return QRectF(x, y, tw, th)
        if fit_mode == "stretch":
            tw = dst_w * scale
            th = dst_h * scale
        elif fit_mode == "fill":
            ratio = max(dst_w / src_w, dst_h / src_h)
            tw = src_w * ratio * scale
            th = src_h * ratio * scale
        else:
            ratio = min(dst_w / src_w, dst_h / src_h)
            tw = src_w * ratio * scale
            th = src_h * ratio * scale
        if abs(sy - 1.0) > 1e-9:
            th *= sy
        x = (dst_w - tw) / 2 + offset_x
        y = (dst_h - th) / 2 + offset_y
        return QRectF(x, y, tw, th)

    def _get_video_frame_at_time(self, cap, clip, t, frame_state, extend_handles=False):
        try:
            from cutora.render import decode_prefetch as _pf
            no_seek = bool(isinstance(frame_state, dict) and frame_state.get("no_seek"))
            pf = None if no_seek else (
                _pf.take(frame_state) if frame_state is not None else None)
            start_t = float(getattr(clip, "start_t", 0.0) if not isinstance(clip, dict) else clip.get("start_t", 0.0))
            in_t = float(getattr(clip, "in_t", 0.0) if not isinstance(clip, dict) else clip.get("in_t", 0.0))
            out_t = float(getattr(clip, "out_t", in_t) if not isinstance(clip, dict) else clip.get("out_t", in_t))
            speed = float(getattr(clip, "speed", 1.0) if not isinstance(clip, dict) else clip.get("speed", 1.0))
            loop = bool(getattr(clip, "loop", False) if not isinstance(clip, dict) else clip.get("loop", False))
            src_dur = max(0.01, out_t - in_t)
            eff_dur = float(getattr(clip, "effective_duration", 0.0)) if hasattr(clip, "effective_duration") else (src_dur / max(0.01, speed))
            if eff_dur <= 0:
                eff_dur = src_dur / max(0.01, speed)
            in_range = not (t < start_t or t >= start_t + eff_dur)
            if not in_range and not extend_handles:
                # Used to return None → offscreen fill(0) transparent, then
                # SourceOver onto the black export canvas = 半透明画面半黑.
                return None
            from cutora.render.transition_runtime import (
                source_duration_of, source_time_for_clip,
            )
            fps_hint = 30.0
            if isinstance(frame_state, dict) and frame_state.get("src_fps"):
                try:
                    fps_hint = float(frame_state.get("src_fps") or 30.0) or 30.0
                except (TypeError, ValueError):
                    fps_hint = 30.0
            elif cap is not None:
                try:
                    _sf = cap.get(cv2.CAP_PROP_FPS)
                    if _sf and _sf > 0:
                        fps_hint = float(_sf)
                except Exception:
                    pass
            src_len = source_duration_of(clip, cap=cap, frame_state=frame_state)
            src_t, _frozen = source_time_for_clip(
                clip, t, src_len, fps=fps_hint, extend=bool(extend_handles))
            if src_t is None:
                return None
            src_t = max(0.0, float(src_t))

            def _seek_src(src_t_s, fps_hint=None):
                sfps = fps_hint
                if not sfps:
                    sfps = cap.get(cv2.CAP_PROP_FPS)
                if not sfps or sfps <= 0:
                    sfps = 30.0
                cap.set(
                    cv2.CAP_PROP_POS_FRAMES,
                    max(0, timeline_frame_index(src_t_s, sfps)))
                if _probe.on():
                    _probe.note("seek")

            def _read_src():
                t0 = time.perf_counter()
                ret, frame = cap.read()
                if _probe.on():
                    _probe.add_ms("decode", time.perf_counter() - t0)
                    _probe.note("read")
                return ret, frame

            def _grab_src():
                t0 = time.perf_counter()
                cap.grab()
                if _probe.on():
                    _probe.add_ms("decode", time.perf_counter() - t0)
                    _probe.note("grab")

            def _sar(frame, sar):
                if not sar:
                    return frame
                t0 = time.perf_counter()
                out = _apply_sar_correction(frame, sar[0], sar[1])
                if _probe.on():
                    _probe.add_ms("scale", time.perf_counter() - t0)
                return out

            if frame_state is None:
                _seek_src(src_t)
                ret, frame = _read_src()
                if not ret or frame is None:
                    return None
                sar = _get_sar_from_cap_or_path(cap, None)
                if sar:
                    frame = _sar(frame, sar)
                return frame

            # ── 用 decode_cursor 精确跟踪解码位置，正确处理 FPS 差异 ──
            if "src_fps" not in frame_state:
                sfps = cap.get(cv2.CAP_PROP_FPS)
                frame_state["src_fps"] = sfps if sfps and sfps > 0 else 30.0
            if "sar" not in frame_state:
                frame_state["sar"] = _get_sar_from_cap_or_path(cap, frame_state.get("src_path"))
            src_interval = 1.0 / frame_state["src_fps"]
            decode_cursor = frame_state.get("decode_cursor", -1.0)

            def _keep_pf():
                if pf is not None:
                    _pf.stash(frame_state, pf)

            def _commit(frame, cursor):
                if frame is None:
                    return None
                sar = frame_state.get("sar")
                if sar:
                    frame = _sar(frame, sar)
                frame_state["decode_cursor"] = cursor
                frame_state["last_frame"] = frame
                # Q180：换了一张新源帧就 +1（返回缓存的 last_frame 时不变）→ 重复帧复用处理结果
                frame_state["_gen"] = int(frame_state.get("_gen") or 0) + 1
                if not no_seek:
                    frame_state["_pf_kick"] = True
                return frame

            if no_seek:
                # Integer source index. The serial path's decode_cursor + 1
                # arithmetic is off-by-one on a jump (chunk 15 → 40 duplicated
                # one frame and the mp4 md5 moved).
                want = timeline_frame_index(src_t, frame_state["src_fps"])
                last = frame_state.get("src_frame_idx")
                last = -1 if last is None else int(last)
                if last == want:
                    if _probe.on():
                        _probe.note("cache_hit")
                    return frame_state.get("last_frame")
                if last >= 0 and want < last:
                    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    last = -1
                # read-and-discard, not grab(): H.264 grab() can skip a
                # different frame than the serial read() cursor.
                for _ in range(max(0, want - last - 1)):
                    _read_src()
                ret, frame = _read_src()
                if not ret:
                    return None
                frame_state["src_frame_idx"] = want
                return _commit(frame, src_t + src_interval)

            if decode_cursor < 0 or src_t < decode_cursor - src_interval * 1.5:
                # 首帧 或 需要回退（循环）→ 必须 seek（丢弃已预读的下一帧）
                _seek_src(src_t, frame_state.get("src_fps"))
                ret, frame = _read_src()
                if ret:
                    return _commit(frame, src_t + src_interval)
                return None

            if src_t < decode_cursor:
                # 还在当前源帧范围内 → 直接返回缓存帧（无 I/O）
                _keep_pf()
                if _probe.on():
                    _probe.note("cache_hit")
                return frame_state.get("last_frame")

            # src_t >= decode_cursor → 需要向前推进
            frames_needed = max(1, int((src_t - decode_cursor) / src_interval) + 1)
            if frames_needed > 30:
                # 跳跃太大 → seek（比逐帧 grab 更快）
                _seek_src(src_t, frame_state.get("src_fps"))
                ret, frame = _read_src()
                if ret:
                    return _commit(frame, src_t + src_interval)
                return None

            orig_needed = frames_needed
            if pf is not None:
                frames_needed -= 1
                if frames_needed <= 0:
                    ret, frame = pf
                    if ret:
                        return _commit(frame, decode_cursor + orig_needed * src_interval)
                    return None

            # 顺序推进：用 grab 跳过中间帧（不解码，极快），最后一帧 read
            for _ in range(max(0, frames_needed - 1)):
                _grab_src()
            ret, frame = _read_src()
            if ret:
                return _commit(frame, decode_cursor + orig_needed * src_interval)
            return None
        except Exception:
            _LOG.exception("导出取帧失败")
            return None

    def _calc_project_duration(self, project):
        """计算项目的真实渲染时长：扫描所有轨道/片段的最远结束时间点。

        原来的逻辑调用 _get_main_video_duration() 只取主内容片段的源素材长度
        (clip.out_t - clip.in_t)，导致开场视频插入后仅返回开场的 5 秒，
        主视频内容完全被忽略。

        新逻辑：遍历 NLE 时间轴上所有轨道的所有片段，取 start_t + effective_duration
        的最大值，再与字幕结束时间、音频轨道结束时间取最大，作为导出终点。
        """
        project = project if isinstance(project, dict) else {}

        video_dur = 0.0
        tl = _tl_of(project)
        if tl is not None:
            for tr in tl.tracks:
                if getattr(tr, "type", None) != "video":
                    continue
                for clip in getattr(tr, "clips", []) or []:
                    try:
                        start_t = float(getattr(clip, "start_t", 0.0))
                        # 优先使用 effective_duration（已考虑变速）
                        eff = float(getattr(clip, "effective_duration", 0.0))
                        if eff <= 0:
                            in_t  = float(getattr(clip, "in_t", 0.0))
                            out_t = float(getattr(clip, "out_t", in_t))
                            speed = max(0.001, float(getattr(clip, "speed", 1.0)))
                            eff = max(0.0, (out_t - in_t) / speed)
                        video_dur = max(video_dur, start_t + eff)
                    except Exception:
                        continue

        # 兜底：如果 NLE 没有视频轨道，回退到裸视频文件时长
        if video_dur <= 0:
            video_dur = probe_duration_sec(project.get("video")) or 0.0

        # 音频轨道结束时间
        audio_dur = 0.0
        tracks = self._build_audio_tracks(project)
        for t in tracks:
            start = float(t.get("start", 0.0))
            duration = t.get("duration")
            if duration is None:
                duration = probe_duration_sec(t.get("path")) or 0.0
            try:
                duration = float(duration)
            except Exception:
                duration = 0.0
            audio_dur = max(audio_dur, start + max(0.0, duration))

        # 字幕/旁白段落结束时间
        sub_end = 0.0
        for seg in project.get("segments", []) or []:
            try:
                sub_end = max(sub_end, float(seg.get("end", 0.0)))
            except Exception:
                continue

        raw_dur = max(video_dur, audio_dur, sub_end, 0.0)
        from cutora.render.export_params import cap_duration
        return cap_duration(raw_dur, project.get("trim_end_sec", 0.0))

    def _build_audio_tracks(self, project, solo_ids=None, preview_sfx=True):
        tracks = []
        if not solo_ids:
            for t in project.get("audio_tracks", []) or []:
                if isinstance(t, dict) and t.get("path") and os.path.exists(t.get("path")):
                    if (not preview_sfx
                            and str(t.get("audio_role") or "") == "sfx"):
                        continue
                    tracks.append(copy.deepcopy(t))
        tl = _tl_of(project)
        if tl is not None:
            from cutora.model.seams import apply_audio_transition_fades
            from cutora.model.track_guard import (
                compute_audio_tracks, compute_export_audio_tracks,
            )
            fade_by_id = {
                id(rec["clip"]): rec
                for rec in apply_audio_transition_fades(project)
            }
            clip_iter = (
                compute_audio_tracks(project, solo_ids=solo_ids)
                if solo_ids else compute_export_audio_tracks(project)
            )
            for clip in clip_iter:
                if not preview_sfx:
                    from cutora.audio.sfx_library import clip_audio_role
                    if clip_audio_role(clip) == "sfx":
                        continue
                source_id = getattr(clip, "source_id", None)
                src = tl.sources.get(source_id) if source_id else None
                path = getattr(src, "path", None) or (src.get("path") if isinstance(src, dict) else None) if src else None
                if not path or not os.path.exists(path):
                    continue
                proc = getattr(clip, "audio_proc", None)
                if not isinstance(proc, dict):
                    proc = {}
                from cutora.audio.voice_presets import take_sidecar
                path, denoise, miss = take_sidecar(path, proc)
                if miss:
                    _LOG.info("[Audio] proc miss %s", miss)
                if not path or not os.path.exists(path):
                    continue
                in_t = float(getattr(clip, "in_t", 0.0))
                out_t = float(getattr(clip, "out_t", in_t))
                start_t = float(getattr(clip, "start_t", 0.0))
                try:
                    speed = float(getattr(clip, "speed", 1.0) or 1.0)
                except (TypeError, ValueError):
                    speed = 1.0
                speed = min(10.0, max(0.1, speed))
                src_span = max(0.0, out_t - in_t)
                curve = getattr(clip, "speed_curve", None) or []
                if curve:
                    from cutora.audio.speed_curve import integrated_out_dur
                    duration = integrated_out_dur(src_span, speed, curve)
                else:
                    duration = src_span if abs(speed - 1.0) < 1e-9 else src_span / speed
                src_dur = getattr(src, "duration", None)
                if src_dur is None and isinstance(src, dict):
                    src_dur = src.get("duration")
                rec = fade_by_id.get(id(clip)) or {}
                tracks.append({
                    "path": path,
                    "start": start_t,
                    "duration": duration,
                    "trim_in": in_t,
                    "gain_db": float(getattr(clip, "gain_db", 0.0)),
                    "_clip": clip,
                    "pitch_semitones": float(getattr(clip, "pitch_semitones", 0.0)),
                    "fade_in_dur": float(rec.get("fade_in_dur", getattr(clip, "fade_in_dur", 0.0))),
                    "fade_out_dur": float(rec.get("fade_out_dur", getattr(clip, "fade_out_dur", 0.0))),
                    "fade_curve": rec.get("fade_curve") or "tri",
                    "spatial_enabled": bool(getattr(clip, "spatial_enabled", False)),
                    "spatial_amount": float(getattr(clip, "spatial_amount", 0.0)),
                    "is_main": bool(getattr(clip, "is_main", False)),
                    "audio_role": getattr(clip, "audio_role", None) or (
                        "main" if getattr(clip, "is_main", False) else "bgm"
                    ),
                    "loop": bool(getattr(clip, "loop", False)),
                    "source_duration": float(src_dur or 0.0),
                    "speed": speed,
                    "src_span": src_span,
                    "denoise_chain": denoise,
                    "speed_curve": list(curve or []),
                })
        # 旧 audio_clips 的兼容回退（仅旧项目未迁移时才触发）
        if not any(True for _ in tracks):
            for c in project.get("audio_clips", []) or []:
                if not isinstance(c, dict):
                    continue
                path = c.get("path")
                if not path or not os.path.exists(path):
                    continue
                if not preview_sfx and str(c.get("audio_role") or "") == "sfx":
                    continue
                clip_start = max(0.0, float(c.get("start", 0)))
                clip_end = float(c.get("end", clip_start))
                clip_len = max(0.1, clip_end - clip_start)
                trim_in = max(0.0, float(c.get("trim_in", 0)))
                volume = max(0.0, float(c.get("volume", 0.1)))
                gain_db = 20.0 * math.log10(max(volume, 1e-6))
                tracks.append({
                    "path": path,
                    "start": clip_start,
                    "duration": clip_len,
                    "trim_in": trim_in,
                    "gain_db": gain_db,
                    "pitch_semitones": float(c.get("pitch_semitones", 0.0)),
                    "fade_in_dur": float(c.get("fade_in_dur", 0.8)) if c.get("fade_in_enabled", True) else 0.0,
                    "fade_out_dur": float(c.get("fade_out_dur", 1.0)) if c.get("fade_out_enabled", True) else 0.0,
                    "spatial_enabled": bool(c.get("spatial_enabled", False)),
                    "spatial_amount": float(c.get("spatial_amount", 0.0)),
                    "is_main": bool(c.get("is_main", False)),
                    "audio_role": c.get("audio_role") or ("main" if c.get("is_main") else "bgm"),
                    "loop": bool(c.get("loop", False)),
                    "source_duration": 0.0,
                    "speed": 1.0,
                    "src_span": clip_len,
                })
        return tracks

    def _get_primary_video_source(self, project):
        tl = _tl_of(project) if isinstance(project, dict) else None
        if tl is None:
            return {}
        for tr in tl.tracks:
            if getattr(tr, "type", None) != "video":
                continue
            clips = getattr(tr, "clips", [])
            if clips:
                first_clip = clips[0]
                source_id = getattr(first_clip, "source_id", None)
                return tl.sources.get(source_id) or {}
        return {}

    def _get_active_video_clip(self, project, t):
        tl = _tl_of(project) if isinstance(project, dict) else None
        if tl is None:
            return None
        for tr in tl.tracks:
            if getattr(tr, "type", None) != "video":
                continue
            for clip in getattr(tr, "clips", []) or []:
                try:
                    start_t = float(getattr(clip, "start_t", 0.0))
                    in_t = float(getattr(clip, "in_t", 0.0))
                    out_t = float(getattr(clip, "out_t", in_t))
                    clip_len = max(0.0, out_t - in_t)
                except Exception:
                    continue
                if start_t <= t <= start_t + clip_len:
                    return clip
        return None

    @staticmethod
    def _find_active_clip_in_track_export(track, t):
        """在单个 Track 中查找 t 时刻的活跃 Clip（导出用，考虑变速）。"""
        for clip in getattr(track, "clips", []) or []:
            try:
                start_t = float(getattr(clip, "start_t", 0.0))
                eff_dur = float(getattr(clip, "effective_duration", 0.0))
                if eff_dur <= 0:
                    in_t = float(getattr(clip, "in_t", 0.0))
                    out_t = float(getattr(clip, "out_t", in_t))
                    eff_dur = max(0.0, out_t - in_t)
            except Exception:
                continue
            if start_t <= t < start_t + eff_dur:
                return clip
        return None

    def _find_active_clips_in_track(self, track, t):
        """在单个 Track 中查找 t 时刻所有活跃的 Clip，按轨道列表顺序（底→顶）。"""
        from cutora.model.timeline_model import active_clips_at
        return active_clips_at(track, t)

    @staticmethod
    def _find_active_clips_in_track_export(track, t):
        """在单个 Track 中查找 t 时刻所有活跃的 Clip（导出用，轨道列表顺序底→顶）。"""
        from cutora.model.timeline_model import active_clips_at
        return active_clips_at(track, t)

# --- Main Window (Updated to use TimelineContainer) ---
class PreviewFxWorker(QThread):
    """后台把当前视频的主音频渲成一条【带效果】的代理音轨 (0829)。

    为什么要这么绕: 空间立体声(大厅堂混响)、变调、淡入淡出这三样在导出时都是
    ffmpeg 滤镜, 而 Qt 的播放器没有任何 DSP 插入点 —— 播放器拿到什么就放什么。
    所以想在预览里听到成品效果, 只能【先渲染, 再播渲染好的那条】。
    用的滤镜和导出那条链路 (_mix_audio_tracks) 逐字一致, 听到的就是导出的。

    输出 m4a/aac 192k 而不是 wav: 48k 立体声 wav 一分钟 11MB, 十分钟的片子
    一渲就是 100MB+; aac 192k 一分钟 1.4MB, 预览听感上分不出来。
    """
    done = Signal(str, str)     # key, out_path
    failed = Signal(str, str)   # key, message

    def __init__(self, key, src, chain, out_path, parent=None):
        super().__init__(parent)
        self._key, self._src = key, src
        self._chain, self._out = chain, out_path

    def run(self):
        try:
            cmd = [FFMPEG_PATH, "-y", "-v", "error", "-i", self._src, "-vn"]
            if self._chain:
                # 0830: 空间立体声改成卷积混响后, 滤镜串里带 ';' 和标签(多路分支),
                # 这种图 -af 解析不了(它只接受单条链路), 必须走 -filter_complex。
                # 老的单链路效果(变调/淡入淡出)照旧走 -af, 一个字没动。
                if ";" in self._chain or "[" in self._chain:
                    cmd += ["-filter_complex", f"[0:a]{self._chain}[aout]",
                            "-map", "[aout]"]
                else:
                    cmd += ["-af", self._chain]
            cmd += ["-ac", "2", "-ar", "48000", "-c:a", "aac", "-b:a", "192k",
                    self._out]
            flags = 0
            if os.name == "nt":
                flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            r = _no_window_run(cmd, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, creationflags=flags)
            if r.returncode != 0 or not os.path.isfile(self._out):
                msg = (r.stderr or b"").decode("utf-8", "ignore")[-300:]
                self.failed.emit(self._key, msg or "ffmpeg 返回非零")
                return
            self.done.emit(self._key, self._out)
        except Exception as e:
            self.failed.emit(self._key, str(e)[:300])
