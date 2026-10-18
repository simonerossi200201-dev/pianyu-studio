# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q152-D · AI 抠像的两种任务：单帧分割（点一下立刻出）和传播（后台线程，一帧帧写缓存）。

没有 Qt。界面那边用 ``cutora.ui.matte_worker.MatteWorker`` 把这里的函数放进 QThread。
所有像素都只在内存里走一遍，日志只记帧数和耗时。
"""
from __future__ import annotations

import time

import numpy as np

from cutora.core.log import get_logger
from cutora.fx import ai_matte as A
from cutora.fx import matte_store as S

_LOG = get_logger("cutora.fx.matte_jobs")


class FrameFeed:
    """按帧号读源视频（RGB uint8）。倒着读按块：先顺序解一块再倒过来吐，避免逐帧硬 seek。"""

    CHUNK = 24

    def __init__(self, path: str):
        import cv2
        from cutora.render.readers import _open_video_capture
        self.path = path
        self.cap = _open_video_capture(path, hw=False)
        if self.cap is None or not self.cap.isOpened():
            raise RuntimeError("打不开视频：%s" % path)
        self.fps = float(self.cap.get(cv2.CAP_PROP_FPS) or 0.0) or 30.0
        self.count = int(self.cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        self.w = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        self.h = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        self._pos = None

    def close(self):
        try:
            self.cap.release()
        except Exception:
            pass

    def _to_rgb(self, frame):
        import cv2
        if frame is None:
            return None
        if frame.ndim == 3 and frame.shape[2] == 4:
            return cv2.cvtColor(frame, cv2.COLOR_BGRA2RGB)
        return cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

    def _seek(self, idx: int):
        import cv2
        if self._pos == idx:
            return
        self.cap.set(cv2.CAP_PROP_POS_FRAMES, int(idx))
        self._pos = int(idx)

    def _read(self):
        ok, frame = self.cap.read()
        if not ok or frame is None:
            return None
        if self._pos is not None:
            self._pos += 1
        rgb = self._to_rgb(frame)
        if rgb is not None and (not self.w or not self.h):
            self.h, self.w = rgb.shape[:2]
        return rgb

    def read_at(self, idx: int):
        self._seek(idx)
        rgb = self._read()
        if rgb is None:
            return None
        return rgb

    def iter_forward(self, start: int, end: int):
        """start..end（含）。"""
        self._seek(start)
        for idx in range(int(start), int(end) + 1):
            rgb = self._read()
            if rgb is None:
                return
            yield idx, rgb

    def iter_backward(self, start: int, end: int):
        """start 往回到 end（含），start >= end。"""
        hi = int(start)
        lo_limit = int(end)
        while hi >= lo_limit:
            lo = max(lo_limit, hi - self.CHUNK + 1)
            chunk = []
            self._seek(lo)
            for idx in range(lo, hi + 1):
                rgb = self._read()
                if rgb is None:
                    break
                chunk.append((idx, rgb))
            if not chunk:
                return
            for item in reversed(chunk):
                yield item
            hi = lo - 1


def _u8(alpha: np.ndarray) -> np.ndarray:
    """float 0–1 → uint8，并缩到缓存上限（S.MASK_MAX_EDGE）。后端已经按上限出的就不再缩。"""
    h, w = alpha.shape[:2]
    mh, mw = S.mask_dims(h, w)
    if (mh, mw) != (h, w):
        import cv2
        alpha = cv2.resize(np.ascontiguousarray(alpha, dtype=np.float32), (mw, mh), interpolation=cv2.INTER_AREA)
    return np.clip(alpha * 255.0 + 0.5, 0, 255).astype(np.uint8)


# 没实测过时的保守估计（秒 / 帧）：CPU 很慢，GPU 很快；实测一次以后用实测值
_DEFAULT_SEC_PER_FRAME = {
    ("matte-sam21-large", "cpu"): 4.0, ("matte-sam21-base-plus", "cpu"): 2.0,
    ("matte-sam21-large", "cuda"): 0.08, ("matte-sam21-base-plus", "cuda"): 0.05,
    ("track-tapnextpp-512", "cpu"): 6.0, ("track-tapnextpp-256", "cpu"): 1.5,
    ("track-tapnextpp-512", "cuda"): 0.08, ("track-tapnextpp-256", "cuda"): 0.03,
}


def estimate_seconds(n_frames: int, model: str, device: str) -> tuple:
    """(秒数, 是否实测过)。"""
    dev = "cuda" if str(device).startswith("cuda") else "cpu"
    measured = S.measured_speed(model, dev)
    if measured:
        return float(n_frames) * measured, True
    per = _DEFAULT_SEC_PER_FRAME.get((model, dev), 4.0 if dev == "cpu" else 0.08)
    return float(n_frames) * per, False


def format_duration_zh(seconds: float) -> str:
    s = max(0, int(round(seconds)))
    if s < 60:
        return "%d 秒" % max(1, s)
    m = s // 60
    if m < 60:
        return "%d 分钟" % max(1, int(s / 60.0 + 0.5))
    return "%d 小时 %d 分钟" % (m // 60, m % 60)


def open_session(sp: dict, feed: FrameFeed) -> S.Session:
    return S.Session(sp["session"], fps=feed.fps, src_w=feed.w, src_h=feed.h, model=sp.get("model"))


def segment_frame(backend, path: str, sp: dict, frame_idx: int) -> dict:
    """单帧：用这一帧上的全部提示重算，写缓存。返回 {obj_id: score}。"""
    t0 = time.perf_counter()
    feed = FrameFeed(path)
    try:
        rgb = feed.read_at(int(frame_idx))
        if rgb is None:
            raise RuntimeError("读不到第 %d 帧" % frame_idx)
        h, w = rgb.shape[:2]
        prompts = A.to_pixels(A.prompts_at(sp, frame_idx), w, h)
        out = backend.segment(rgb, prompts) if prompts else {}
        scores = {}
        with open_session(sp, feed) as sess:
            objs_with_prompts = {int(p.get("obj", 1)) for p in prompts}
            # 这一帧上没有提示的对象：保留原来的结果；有提示但模型没回的：删掉
            for oid in objs_with_prompts:
                if oid in out:
                    alpha, score = out[oid]
                    sess.write(frame_idx, oid, _u8(alpha), score)
                    scores[oid] = float(score)
            if not prompts:
                sess.remove_frames([frame_idx])
        _LOG.info("AI 抠像单帧 frame=%d objs=%d 用时 %.0f ms", frame_idx, len(scores), (time.perf_counter() - t0) * 1000)
        return scores
    finally:
        feed.close()


def propagate(backend, path: str, sp: dict, start_frame: int, direction: str, end_frame: int,
              on_progress=None, cancel=None) -> dict:
    """从 start_frame 开始沿 direction（forward / backward）传播到 end_frame（含），结果写缓存。

    on_progress(done, total, frame_idx, score)；cancel: threading.Event。
    返回 {"frames": n, "seconds": s, "cancelled": bool, "low": 低置信度帧数}。"""
    t0 = time.perf_counter()
    feed = FrameFeed(path)
    try:
        start_frame = int(max(0, start_frame))
        end_frame = int(max(0, end_frame))          # 不按 CAP_PROP_FRAME_COUNT 截：有的解码器少报一帧，读不到自然停
        if direction == "backward":
            frames = feed.iter_backward(start_frame, end_frame)
            order = list(range(start_frame, end_frame - 1, -1))
        else:
            frames = feed.iter_forward(start_frame, end_frame)
            order = list(range(start_frame, end_frame + 1))
        total = max(1, len(order))
        w, h = feed.w, feed.h
        if not w or not h:
            probe = feed.read_at(start_frame)
            if probe is None:
                raise RuntimeError("读不到第 %d 帧" % start_frame)
            h, w = probe.shape[:2]
            frames = feed.iter_backward(start_frame, end_frame) if direction == "backward" \
                else feed.iter_forward(start_frame, end_frame)
        prompts_by_offset = {}
        for off, fi in enumerate(order):
            pr = A.prompts_at(sp, fi)
            if pr:
                prompts_by_offset[off] = A.to_pixels(pr, w, h)
        if 0 not in prompts_by_offset:
            raise RuntimeError("起始帧上没有提示点")
        done = {"n": 0, "low": 0, "cancelled": False}
        with open_session(sp, feed) as sess:
            def _on_frame(frame_idx, masks):
                worst = 1.0
                for oid, (alpha, score) in masks.items():
                    sess.write(frame_idx, oid, _u8(alpha), score)
                    worst = min(worst, float(score))
                done["n"] += 1
                if worst < 0.5:
                    done["low"] += 1
                if on_progress is not None:
                    on_progress(done["n"], total, int(frame_idx), worst)
                if cancel is not None and cancel.is_set():
                    done["cancelled"] = True
                    return False
                return True

            backend.propagate(frames, prompts_by_offset, _on_frame, cancel=cancel)
        secs = time.perf_counter() - t0
        if getattr(backend, "name", "") != "fake" and done["n"] >= 8:
            dev = str(getattr(backend, "device", "cpu") or "cpu")
            S.record_speed(str(sp.get("model") or ""), "cuda" if dev.startswith("cuda") else "cpu",
                           secs / float(done["n"]), done["n"])
        _LOG.info("AI 抠像传播 %s %d 帧 用时 %.1fs（%.1f fps）%s", direction, done["n"], secs,
                  done["n"] / secs if secs > 0 else 0.0, "已取消" if done["cancelled"] else "")
        return {"frames": done["n"], "seconds": secs, "cancelled": done["cancelled"], "low": done["low"]}
    finally:
        feed.close()


def clip_source_frame_range(clip, fps: float) -> tuple:
    """片段用到的源帧范围 [in, out]（含）。"""
    in_t = float(getattr(clip, "in_t", 0.0) or 0.0)
    out_t = float(getattr(clip, "out_t", in_t) or in_t)
    a = S.frame_index(in_t, fps)
    b = max(a, S.frame_index(out_t, fps) - 1)
    return a, b


_FPS_CACHE: dict = {}


def source_fps(path: str) -> float:
    """源视频帧率（按路径缓存，开一次 capture）。"""
    key = str(path or "")
    hit = _FPS_CACHE.get(key)
    if hit:
        return hit
    fps = 30.0
    try:
        import cv2
        cap = cv2.VideoCapture(key)
        try:
            v = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
            if v > 0:
                fps = v
        finally:
            cap.release()
    except Exception:
        pass
    if fps <= 1.0:
        try:
            feed = FrameFeed(key)
            fps = feed.fps
            feed.close()
        except Exception:
            fps = 30.0
    _FPS_CACHE[key] = fps
    return fps
