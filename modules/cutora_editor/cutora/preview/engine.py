# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""T28: timeline preview clock + frame_at via export helpers."""
from __future__ import annotations

import math
import os
from collections import OrderedDict

import cv2
from PySide6.QtGui import QImage, QPainter

from cutora.core.fps_meter import note_preview_frame
from cutora.core.paths import user_cache_dir
from cutora.model.canvas import canvas_wh
from cutora.render.readers import _open_video_capture


def timeline_frame_index(t: float, fps: float) -> int:
    """Same discrete frame as export: floor(t * fps + 1e-6)."""
    return int(math.floor(float(t) * float(fps) + 1e-6))


def timeline_frame_time(t: float, fps: float) -> float:
    return timeline_frame_index(t, fps) / float(fps)


def preview_audio_mode(proxy_ready: bool) -> str:
    """Which audio the transport plays.

    timeline_wav: mixed preview wav is ready (every clip, same hall as export).
    preparing: wav not ready — original source, never the first-clip fx proxy.
    """
    return "timeline_wav" if proxy_ready else "preparing"


import weakref

_POOLS: "weakref.WeakSet" = weakref.WeakSet()       # 活着的池（闲置回收统一扫）
PREVIEW_POOL_IDLE_S = 30.0


def reap_idle_pools(idle_s: float = PREVIEW_POOL_IDLE_S) -> int:
    """所有预览 / 监视器解码器池：放掉闲置超过 idle_s 的解码器。画布的定时器每 5 s 调一次。"""
    n = 0
    for pool in list(_POOLS):
        try:
            n += pool.release_idle(idle_s)
        except Exception:
            pass
    return n


def live_decoder_count() -> int:
    """日志用：各池现在一共养着几个解码器。"""
    return sum(len(p) for p in list(_POOLS))


class CapturePool:
    """LRU of cv2.VideoCapture, one per source. Capacity 8.

    ``hw=False``：只走软解（Q156 中央监视器：几十条视频轮流开关 D3D11VA 硬解会话会把显卡驱动
    打挂 → 黑屏，和导出的 BUG-42 一个病根；缩略图尺寸下软解绰绰有余）。"""

    def __init__(self, capacity: int = 8, hw=None):
        self.capacity = int(capacity)
        if hw is None:
            from cutora.render.readers import preview_hw_decode
            hw = preview_hw_decode()                  # Q189d：默认软解（见 readers.preview_hw_decode）
        self.hw = bool(hw)
        self._caps: OrderedDict[str, cv2.VideoCapture] = OrderedDict()
        self._last: dict = {}                     # key → 最后一次 get 的时刻（闲置回收用）
        import threading as _th
        self._lock = _th.RLock()                  # 池表的增删在界面线程（回收）和工作线程（取帧）之间要互斥
        _POOLS.add(self)

    def __len__(self):
        return len(self._caps)

    def release_idle(self, idle_s: float) -> int:
        """Q189：放掉超过 idle_s 没被取过的解码器。一个 1080×1920 的 H.264 解码器占 40～130 MB，
        几个池各养着 8 个不用的，主界面就是 1 GB。重开时引擎会把帧状态的 decode_cursor 置 -1 → 重新 seek。"""
        import time as _time
        now = _time.monotonic()
        n = 0
        victims = []
        with self._lock:
            for key in list(self._caps.keys()):
                if now - float(self._last.get(key, now)) > float(idle_s):
                    victims.append(self._caps.pop(key, None))
                    self._last.pop(key, None)
        for cap in victims:
            try:
                if cap is not None:
                    cap.release()
            except Exception:
                pass
            n += 1
        return n

    def release_one(self) -> bool:
        """放掉最久没用的一个解码器（渐进释放用）。没有了返回 False。"""
        with self._lock:
            if not self._caps:
                return False
            _k, old = self._caps.popitem(last=False)
            self._last.pop(_k, None)
        from cutora.render.readers import _CAP_OPEN_LOCK
        with _CAP_OPEN_LOCK:
            try:
                old.release()
            except Exception:
                pass
        return True

    def get(self, source_id: str, path: str):
        import time as _time
        from cutora.render.readers import PREVIEW_DECODE_THREADS, _CAP_OPEN_LOCK
        key = str(source_id or path)
        with self._lock:
            self._last[key] = _time.monotonic()
            cap = self._caps.get(key)
            if cap is not None and cap.isOpened():
                self._caps.move_to_end(key)
                return cap
            if cap is not None:
                with _CAP_OPEN_LOCK:
                    try:
                        cap.release()
                    except Exception:
                        pass
                self._caps.pop(key, None)
        # Q188：预览解码器最多 4 条线程（以前不限 = 16 条，8 个解码器就是 128 条线程和各自的参考帧池）
        cap = _open_video_capture(path, hw=self.hw, threads=PREVIEW_DECODE_THREADS) or cv2.VideoCapture(path)
        if cap is None or not cap.isOpened():
            return None
        olds = []
        with self._lock:
            self._caps[key] = cap
            self._last[key] = _time.monotonic()
            while len(self._caps) > self.capacity:
                _k, old = self._caps.popitem(last=False)
                self._last.pop(_k, None)
                olds.append(old)
        with _CAP_OPEN_LOCK:
            for old in olds:
                try:
                    old.release()
                except Exception:
                    pass
        return cap

    def clear(self):
        with self._lock:
            caps = list(self._caps.values())
            self._caps.clear()
            self._last.clear()
        from cutora.render.readers import _CAP_OPEN_LOCK
        with _CAP_OPEN_LOCK:
            for cap in caps:
                try:
                    cap.release()
                except Exception:
                    pass


def timeline_duration_sec(task: dict | None) -> float:
    if not isinstance(task, dict):
        return 0.0
    tl = task.get("timeline")
    end = 0.0
    tracks = getattr(tl, "tracks", None) if tl is not None else None
    if tracks is None and isinstance(tl, dict):
        tracks = tl.get("tracks") or []
    for tr in tracks or []:
        clips = getattr(tr, "clips", None)
        if clips is None and isinstance(tr, dict):
            clips = tr.get("clips") or []
        for c in clips or []:
            start = float(getattr(c, "start_t", 0.0) if not isinstance(c, dict) else c.get("start_t") or 0.0)
            eff = float(getattr(c, "effective_duration", 0.0) or 0.0) if not isinstance(c, dict) else 0.0
            if eff <= 0:
                inn = float(getattr(c, "in_t", 0.0) if not isinstance(c, dict) else c.get("in_t") or 0.0)
                out = float(getattr(c, "out_t", inn) if not isinstance(c, dict) else c.get("out_t") or inn)
                speed = float(getattr(c, "speed", 1.0) if not isinstance(c, dict) else c.get("speed") or 1.0)
                eff = max(0.0, (out - inn) / max(0.01, speed))
            end = max(end, start + eff)
    dur = float(task.get("duration") or 0.0)
    return max(end, dur)


def playback_end_sec(task: dict | None) -> float:
    """Q157 播放头能走到哪：时间线上最远的片段 / 渲染终点（trim_end_sec）两者取大。

    原视频时长不是上限 —— 后面接了别的片段、或把渲染范围往后拉了，播放就得跟过去。"""
    end = timeline_duration_sec(task)
    try:
        trim_end = float((task or {}).get("trim_end_sec") or 0.0) if isinstance(task, dict) else 0.0
    except (TypeError, ValueError):
        trim_end = 0.0
    return max(end, trim_end)


def preview_wav_path(task: dict, *, solo_ids=None, preview_sfx=True) -> str:
    key = _preview_key(task, solo_ids=solo_ids, preview_sfx=preview_sfx)
    d = os.path.join(user_cache_dir(), "preview_audio")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "%s.wav" % key)


def _preview_key(task: dict, *, solo_ids=None, preview_sfx=True) -> str:
    from cutora.asr.timeline_wav import preview_audio_fingerprint
    return preview_audio_fingerprint(
        task, solo_ids=solo_ids, preview_sfx=preview_sfx)


class PreviewClock:
    """Timeline playhead. Media-player clock when proxy ready, else wall clock."""

    SEEK_CATCH_MS = 80
    SEEK_TIMEOUT_S = 0.300

    def __init__(self):
        self.ms = 0
        self.end_ms = 0
        self.playing = False
        self.use_media = False
        self.last_ts = None
        self.seek_target = None
        self.seek_since = None
        self._last_media_ms = None

    def seek(self, ms: int, now: float | None = None) -> int:
        """Single source of truth for playhead. Ignores stale media_pos until catch-up."""
        self.ms = max(0, int(ms))
        if self.end_ms > 0:
            self.ms = min(self.ms, int(self.end_ms))
        self.seek_target = int(self.ms)
        self.seek_since = float(now if now is not None else 0.0)
        self.last_ts = float(now) if now is not None else self.last_ts
        self._last_media_ms = None
        return int(self.ms)

    def play_toggle(self, now: float) -> bool:
        if self.playing:
            self.playing = False
            self.last_ts = None
            self._last_media_ms = None
            return False
        if self.end_ms > 0 and int(self.ms) >= self.end_ms - 30:
            self.ms = 0
            self.seek_target = None
        self.playing = True
        self.last_ts = float(now)
        self._last_media_ms = None
        return True

    def tick(self, now: float, media_pos_ms=None) -> int:
        now = float(now)
        if self.seek_target is not None:
            target = int(self.seek_target)
            since = float(self.seek_since or now)
            caught = (
                self.use_media
                and media_pos_ms is not None
                and abs(int(media_pos_ms) - target) <= self.SEEK_CATCH_MS
            )
            timed_out = (now - since) >= self.SEEK_TIMEOUT_S
            if caught:
                self.ms = int(media_pos_ms)
                self._last_media_ms = int(media_pos_ms)
                self.seek_target = None
                self.seek_since = None
            elif timed_out:
                self.ms = target
                self.seek_target = None
                self.seek_since = None
            else:
                self.ms = target
                self.last_ts = now
                return int(self.ms)
        if not self.playing:
            self.last_ts = now
            return int(self.ms)
        if self.use_media and media_pos_ms is not None:
            m_ms = int(media_pos_ms)
            last_m = getattr(self, "_last_media_ms", None)
            if last_m is not None and m_ms == last_m and self.last_ts is not None:
                dt_ms = max(0, int(round((now - float(self.last_ts)) * 1000.0)))
                self.ms = min(m_ms + 45, int(self.ms) + dt_ms)
            else:
                self._last_media_ms = m_ms
                self.ms = max(m_ms, int(self.ms)) if (last_m is not None and 0 <= int(self.ms) - m_ms <= 25) else m_ms
        elif self.last_ts is not None:
            self.ms = int(self.ms) + int((now - float(self.last_ts)) * 1000)
        self.last_ts = now
        if self.end_ms > 0 and int(self.ms) >= self.end_ms - 20:
            self.ms = int(self.end_ms)
            self.playing = False
            self.last_ts = None
            self._last_media_ms = None
        return int(self.ms)


# Q145 第二期：画布按显示像素合成视频层。倍率按 1/8 一档往上取整（窗口微调大小
# 不会每次都换一种尺寸），≥ 0.9 直接满分辨率，最低 1/4。
DISPLAY_SCALE_STEP = 8
DISPLAY_SCALE_MIN = 0.25


def display_scale_step(fit: float) -> float:
    try:
        f = float(fit)
    except (TypeError, ValueError):
        return 1.0
    if not (f > 0.0) or f != f:
        return 1.0
    q = math.ceil(f * DISPLAY_SCALE_STEP - 1e-9) / float(DISPLAY_SCALE_STEP)
    if q >= 0.9:
        return 1.0
    return max(DISPLAY_SCALE_MIN, q)


class PreviewEngine:
    """frame_at(t) uses RenderWorker export helpers. One CapturePool."""

    def __init__(self):
        self.pool = CapturePool(6)                # Q189：8 → 6；闲置的会被 reap_idle_pools 放掉
        self._states: dict = {}
        from cutora.render.render_worker import RenderWorker
        self._rw = RenderWorker.__new__(RenderWorker)
        self._rw._fx_preview = True          # Q152-F：节点图查看器只在预览里生效

    def close(self):
        self.pool.clear()
        self._states.clear()

    def _snap_t(self, task: dict, t: float) -> float:
        fps = 30.0
        tl = task.get("timeline") if isinstance(task, dict) else None
        raw = getattr(tl, "fps", None) if tl is not None else None
        if not raw and isinstance(task, dict):
            raw = task.get("fps")
        try:
            if raw:
                fps = float(raw)
        except (TypeError, ValueError):
            fps = 30.0
        if fps <= 0:
            fps = 30.0
        return timeline_frame_time(t, fps)

    def video_bands_at(self, task: dict, t: float, scale: float = 1.0, bands=None) -> list:
        """Q163 统一叠压：每一段视频带（底→顶）各出一张透明图，调用方和覆层交错着叠。

        bands：[[轨道 id, …], …]；不给就按这个 task 的时间线算（visual_stack）。"""
        if bands is None:
            from cutora.render.visual_stack import build_stack, video_bands
            bands = [[str(getattr(tr, "id", "")) for tr in b] for b in video_bands(build_stack(task))]
        from cutora.render.visual_stack import BandLayers, track_blend_at
        tl = task.get("timeline") if isinstance(task, dict) else None
        by_id = {str(getattr(tr, "id", "")): tr for tr in getattr(tl, "tracks", None) or []}
        ts = self._snap_t(task, t)
        out = []
        for i, ids in enumerate(bands or []):
            ids = [str(x) for x in ids]
            if i > 0:
                # Q178：带里有轨道用了混合模式 → 一条轨道一张图，叠的时候各自混合（不然滤色在透明图上等于普通覆盖）
                modes = [(x, track_blend_at(by_id[x], ts)) for x in ids if x in by_id]
                if any(m != "normal" for _x, m in modes):
                    # 亮度蒙版先留住画面，叠到已经画好的结果上时再挖。在透明图里挖会把蒙版自己挖没。
                    from cutora.render.frame_renderer import defer_luma_matte
                    with defer_luma_matte():
                        out.append(BandLayers(
                            (self.video_frame_at(task, t, scale=scale, only={x}, bottom=False), m)
                            for x, m in modes))
                    continue
            out.append(self.video_frame_at(task, t, scale=scale, only=set(ids), bottom=(i == 0)))
        return out

    def video_frame_at(self, task: dict, t: float, size=None, scale: float = 1.0,
                       only=None, bottom: bool = True):
        """Video layer only — no overlays/subs. Canvas samples this as glass backdrop.

        Q130：``scale`` < 1 是拖动时的降画质预览：像素少画，**几何不变** ——
        画布还是按逻辑尺寸 (w, h) 排，只是 painter 整体缩了。以前拖动时把画布
        当成 540×960 去画，片段的 x/y 偏移、分屏格子却仍按 1080 算，视频就
        「跑偏」了。``size`` 仍是显式画布尺寸（老调用方）。
        """
        from cutora.render import anim_engine
        from cutora.render.frame_renderer import BLEND_MODES, _BLEND_NORMAL
        t = self._snap_t(task, t)
        if size is None:
            w, h = canvas_wh(task)
        else:
            w, h = int(size[0]), int(size[1])
        sc = float(scale or 1.0)
        if 0.0 < sc < 0.999:
            rw = max(1, int(round(w * sc)))
            rh = max(1, int(round(h * sc)))
        else:
            rw, rh = int(w), int(h)
        self._rw.project = task
        self._rw._preview_scale = sc
        video = QImage(int(rw), int(rh), QImage.Format.Format_ARGB32_Premultiplied)
        video.fill(0)
        vp = QPainter(video)
        try:
            vp.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            vp.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            if (rw, rh) != (int(w), int(h)):
                vp.scale(rw / float(w), rh / float(h))
            _kw = {} if (only is None and bottom) else {"only": only, "bottom": bottom}
            self._draw_video(
                vp, task, float(t), int(w), int(h),
                anim_engine, BLEND_MODES, _BLEND_NORMAL, **_kw)
        finally:
            vp.end()
            self._rw._preview_scale = 1.0
        return video

    def frame_at(self, task: dict, t: float, preview_scale: float = 1.0):
        """Same compositor as export: video helpers + ExportRenderer overlays/subs.

        ``preview_scale`` < 1 只把视频层画小（几何不变），覆层 / 字幕仍按
        逻辑尺寸画在放大回来的视频上 —— 文字永远是清楚的、位置永远是对的。
        画布本身不再用这个接口（它自己画覆层以收命中记录），留给测试和
        「和导出比一帧」的场合。
        """
        from PySide6.QtCore import Qt
        from cutora.render.export_renderer import ExportRenderer
        from cutora.render.textfx.spec import task_has_glass_bg
        note_preview_frame()
        t = self._snap_t(task, t)
        w, h = canvas_wh(task)
        video = self.video_frame_at(task, t, scale=float(preview_scale or 1.0))
        if (video.width(), video.height()) != (int(w), int(h)):
            video = video.scaled(
                int(w), int(h),
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation)

        def _draw_bg(p, _img=video):
            p.drawImage(0, 0, _img)

        def _band(p, tracks, index):
            # Q163：统一叠压时逐段画视频带。Q178：上面的带直接画在已经叠好的画面上（和导出一样，
            # 滤色 / 正片叠底这些混合模式和下面真的混合）
            if index > 0:
                from cutora.render import anim_engine
                from cutora.render.frame_renderer import BLEND_MODES, _BLEND_NORMAL
                self._rw.project = task
                self._draw_video(p, task, float(t), int(w), int(h), anim_engine, BLEND_MODES, _BLEND_NORMAL,
                                 only={str(getattr(x, "id", "")) for x in tracks}, bottom=False)
                return
            img = self.video_frame_at(task, t, scale=float(preview_scale or 1.0),
                                      only={str(getattr(x, "id", "")) for x in tracks},
                                      bottom=(index == 0))
            if (img.width(), img.height()) != (int(w), int(h)):
                img = img.scaled(int(w), int(h), Qt.AspectRatioMode.IgnoreAspectRatio,
                                 Qt.TransformationMode.SmoothTransformation)
            p.drawImage(0, 0, img)

        backdrop = video if task_has_glass_bg(task) else None
        return ExportRenderer.render_frame(
            task, int(w), int(h), float(t),
            draw_background=_draw_bg, backdrop=backdrop, paint_video_band=_band)

    def display_frame_at(self, task: dict, t: float, scale: float = 1.0, solo_ids=None):
        """Q175 播放用：整幅画面（视频 + 图片 / 文字 / 字幕 + 调整图层）一次合成，**按屏幕显示像素**。

        画布播放时只贴这一张图 —— 界面线程不再逐帧排字、画特效、做调整图层，声音和画面都不被它拖慢。
        几何和导出完全一样（painter 整体缩放），只是像素按显示尺寸：字在屏幕上一样清楚。
        和画布暂停时一样不叠工程级调色（暂停画面也没有它，播放 / 暂停看起来一致）。"""
        from PySide6.QtCore import QRectF, Qt
        from cutora.render.frame_renderer import FrameRenderer, compute_render_order
        from cutora.render.visual_stack import build_stack, needs_stack, paint_overlay_entry
        from cutora.fx.graph import preview_scope
        from cutora.render import anim_engine
        from cutora.render.frame_renderer import BLEND_MODES, _BLEND_NORMAL
        t = self._snap_t(task, t)
        w, h = canvas_wh(task)
        sc = float(scale or 1.0)
        if not (0.0 < sc < 0.999):
            sc = 1.0
        rw, rh = max(1, int(round(w * sc))), max(1, int(round(h * sc)))
        solo_key = tuple(sorted(str(x) for x in (solo_ids or ())))
        cache_key = (id(task), round(float(t), 4), rw, rh, solo_key)
        last_memo = getattr(self, "_last_display_memo", None)
        if last_memo is not None and last_memo[0] == cache_key and sc < 0.999:
            return last_memo[1]
        out = QImage(rw, rh, QImage.Format.Format_ARGB32_Premultiplied)
        out.fill(Qt.GlobalColor.black)
        full = QRectF(0.0, 0.0, float(w), float(h))

        def _hints(p):
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

        st = build_stack(task, solo_ids)
        stacked = needs_stack(task, stack=st)
        self._rw._preview_scale = sc
        p = QPainter(out)
        try:
            _hints(p)
            if sc != 1.0:
                p.scale(rw / float(w), rh / float(h))
            with preview_scope(True):
                if stacked:
                    vi = 0
                    backdrop = None
                    for kind, payload in st:
                        if kind == "video":
                            ids = {str(getattr(x, "id", "")) for x in payload}
                            if vi > 0:
                                # Q178：上面的带直接画在叠好的画面上，混合模式和下面真的混合（同导出）
                                self._rw.project = task
                                self._rw._preview_scale = sc
                                self._draw_video(p, task, float(t), int(w), int(h), anim_engine, BLEND_MODES,
                                                 _BLEND_NORMAL, only=ids, bottom=False)
                            else:
                                img = self.video_frame_at(task, t, scale=sc, only=ids, bottom=True)
                                p.drawImage(full, img)
                                if sc == 1.0:
                                    backdrop = img
                            vi += 1
                            continue
                        paint_overlay_entry(p, task, kind, payload, w, h, t, backdrop=backdrop)
                else:
                    img = self.video_frame_at(task, t, scale=sc)
                    p.drawImage(full, img)
                    order = compute_render_order(task, solo_ids=solo_ids)
                    FrameRenderer.draw_layers(p, task, w, h, t, None, render_order=order,
                                              backdrop=img if sc == 1.0 else None)
        finally:
            if p.isActive():
                p.end()
            self._rw._preview_scale = 1.0
        if sc < 0.999:
            self._last_display_memo = (cache_key, out)
        return out

    def _draw_video(self, p, task, t, width, height, anim_engine, BLEND_MODES, _BLEND_NORMAL,
                    only=None, bottom=True):
        """Q155：先定这一帧要不要让位给整幅背景；不分屏时外部背景图垫最底（分屏的在 split_screen 里画）。

        Q163：only = 只画这几条轨（id 集合，统一叠压的一段视频带）；bottom=False 不画整幅背景图。"""
        from cutora.render import backdrop as _bg
        from cutora.render.split_layout import split_enabled
        _bg.begin_frame(task)
        try:
            if not split_enabled(task) and bottom:
                _bg.paint_bg_image(p, task, width, height)
            return self._draw_video_tracks(p, task, t, width, height, anim_engine,
                                           BLEND_MODES, _BLEND_NORMAL, only=only, bottom=bottom)
        finally:
            _bg.end_frame()

    def _draw_video_tracks(self, p, task, t, width, height, anim_engine, BLEND_MODES, _BLEND_NORMAL,
                           only=None, bottom=True):
        from cutora.render.render_worker import RenderWorker
        from cutora.render.split_layout import split_enabled
        tl = task.get("timeline")
        tracks = getattr(tl, "tracks", None) if tl is not None else []
        sources = getattr(tl, "sources", {}) if tl is not None else {}

        def _paint_one(dest, tr, dw, dh):
            active_trans = None
            for trans in getattr(tr, "transitions", []) or []:
                if str(getattr(trans, "domain", "visual") or "visual") == "audio":
                    continue
                if trans.start_t <= t <= (trans.start_t + trans.duration):
                    active_trans = trans
                    break
            from cutora.render.track_transform import paint_track_layer, track_needs_layer
            layering = track_needs_layer(tr)
            if layering or active_trans is not None:
                _limg = getattr(dest, "_split_layer_img", None)
                if _limg is not None:
                    _limg.fill(0)
                    dest._split_layer_img = None

            def _paint_track(d2, _tr=tr, _trans=active_trans, _layering=layering, _w=dw, _h=dh):
                if _trans is not None:
                    # compose_standalone_transition via _paint_export_transition
                    self._rw._paint_export_transition(
                        d2, _tr, _trans, t, _w, _h,
                        lambda c: self._cap_for(c, sources),
                        lambda c: self._states.setdefault(
                            str(getattr(c, "source_id", "") or ""),
                            {"last_time": -1.0, "last_frame": None}),
                    )
                    return
                active = RenderWorker._find_active_clips_in_track_export(_tr, t)
                from cutora.batch.layer_folders import (
                    blend_of_folder, folder_task_scope, hidden_by_folder,
                )
                from cutora.render.child_matte import paint_parent_matted, partition_clips
                _own, _mattes = partition_clips(task, _tr, t, active)
                if _mattes:
                    _limg = getattr(d2, "_split_layer_img", None)
                    if _limg is not None:
                        _limg.fill(0)
                        d2._split_layer_img = None
                    def _one(p, clip, _ww=_w, _hh=_h):
                        cap = self._cap_for(clip, sources)
                        if cap is None:
                            return
                        sid = str(getattr(clip, "source_id", "") or "")
                        st = self._states.setdefault(sid, {"last_time": -1.0, "last_frame": None})
                        self._rw._draw_video_overlay(p, cap, clip, _ww, _hh, t, st)

                    def _blend(clip, _track=_tr, _layer=_layering):
                        name = getattr(clip, "blend_mode", None)
                        if not _layer:
                            name = name or getattr(_track, "blend_mode", "normal")
                        return name or "normal"

                    with folder_task_scope(task):
                        paint_parent_matted(d2, _w, _h, _own, _mattes, _one, _blend, task)
                    return
                if len(_own or []) != 1:
                    _limg = getattr(d2, "_split_layer_img", None)
                    if _limg is not None:
                        _limg.fill(0)
                        d2._split_layer_img = None
                for clip in _own or []:
                    if hidden_by_folder(task, clip):
                        continue
                    cap = self._cap_for(clip, sources)
                    if cap is None:
                        continue
                    blend_name = getattr(clip, "blend_mode", None)
                    if not _layering:
                        blend_name = blend_name or getattr(_tr, "blend_mode", "normal")
                    else:
                        blend_name = blend_name or "normal"
                    _fb = blend_of_folder(task, clip)
                    if _fb and str(blend_name or "normal") == "normal":
                        blend_name = _fb
                    if _layering or str(blend_name or "normal") != "normal":
                        _limg = getattr(d2, "_split_layer_img", None)
                        if _limg is not None:
                            _limg.fill(0)
                            d2._split_layer_img = None
                    from cutora.render.frame_renderer import paint_blended_video_clip
                    sid = str(getattr(clip, "source_id", "") or "")

                    def _draw_clip(p, _cap=cap, _clip=clip, _sid=sid, _ww=_w, _hh=_h):
                        st = self._states.setdefault(_sid, {"last_time": -1.0, "last_frame": None})
                        self._rw._draw_video_overlay(p, _cap, _clip, _ww, _hh, t, st)

                    with folder_task_scope(task):
                        paint_blended_video_clip(d2, blend_name, _layering, _draw_clip, _w, _h)

            paint_track_layer(dest, tr, dw, dh, _paint_track)

        _paint_one._supports_lazy_clear = True
        from cutora.model.track_guard import video_tracks_for_render
        video_tracks = video_tracks_for_render(task)
        if only is not None:
            video_tracks = [tr for tr in video_tracks if str(getattr(tr, "id", "")) in only]
        if split_enabled(task):
            from cutora.render.split_screen import paint_split_video_tracks
            paint_split_video_tracks(p, task, video_tracks, width, height, _paint_one, bottom=bottom)
            return
        for tr in tracks or []:
            if getattr(tr, "type", None) != "video":
                continue
            if not getattr(tr, "visible", True):
                continue
            if only is not None and str(getattr(tr, "id", "")) not in only:
                continue
            active_trans = None
            for trans in getattr(tr, "transitions", []) or []:
                if str(getattr(trans, "domain", "visual") or "visual") == "audio":
                    continue
                if trans.start_t <= t <= (trans.start_t + trans.duration):
                    active_trans = trans
                    break
            from cutora.render.track_transform import paint_track_layer, track_needs_layer
            layering = track_needs_layer(tr)

            def _paint_track(dest, _tr=tr, _trans=active_trans, _layering=layering):
                if _trans is not None:
                    # compose_standalone_transition via _paint_export_transition
                    self._rw._paint_export_transition(
                        dest, _tr, _trans, t, width, height,
                        lambda c: self._cap_for(c, sources),
                        lambda c: self._states.setdefault(
                            str(getattr(c, "source_id", "") or ""),
                            {"last_time": -1.0, "last_frame": None}),
                    )
                    return
                active = RenderWorker._find_active_clips_in_track_export(_tr, t)
                from cutora.batch.layer_folders import (
                    blend_of_folder, folder_task_scope, hidden_by_folder,
                )
                from cutora.render.child_matte import paint_parent_matted, partition_clips
                _own, _mattes = partition_clips(task, _tr, t, active)
                if _mattes:
                    def _one(p, clip):
                        cap = self._cap_for(clip, sources)
                        if cap is None:
                            return
                        sid = str(getattr(clip, "source_id", "") or "")
                        st = self._states.setdefault(sid, {"last_time": -1.0, "last_frame": None})
                        self._rw._draw_video_overlay(p, cap, clip, width, height, t, st)

                    def _blend(clip, _track=_tr, _layer=_layering):
                        name = getattr(clip, "blend_mode", None)
                        if not _layer:
                            name = name or getattr(_track, "blend_mode", "normal")
                        return name or "normal"

                    with folder_task_scope(task):
                        paint_parent_matted(
                            dest, width, height, _own, _mattes, _one, _blend, task)
                    return
                for clip in _own or []:
                    if hidden_by_folder(task, clip):
                        continue
                    cap = self._cap_for(clip, sources)
                    if cap is None:
                        continue
                    blend_name = getattr(clip, "blend_mode", None)
                    if not _layering:
                        blend_name = blend_name or getattr(_tr, "blend_mode", "normal")
                    else:
                        blend_name = blend_name or "normal"
                    _fb = blend_of_folder(task, clip)
                    if _fb and str(blend_name or "normal") == "normal":
                        blend_name = _fb
                    from cutora.render.frame_renderer import paint_blended_video_clip
                    sid = str(getattr(clip, "source_id", "") or "")

                    def _draw_clip(p, _cap=cap, _clip=clip, _sid=sid):
                        st = self._states.setdefault(_sid, {"last_time": -1.0, "last_frame": None})
                        self._rw._draw_video_overlay(p, _cap, _clip, width, height, t, st)

                    with folder_task_scope(task):
                        paint_blended_video_clip(dest, blend_name, _layering, _draw_clip, width, height)

            paint_track_layer(p, tr, width, height, _paint_track)

    def _cap_for(self, clip, sources):
        sid = getattr(clip, "source_id", None)
        src = sources.get(sid) if sid else None
        path = None
        if src is not None:
            preview = getattr(src, "preview_path", None) or (
                src.get("preview_path") if isinstance(src, dict) else None)
            orig = getattr(src, "path", None) or (
                src.get("path") if isinstance(src, dict) else None)
            if preview:
                from cutora.render.readers import _is_sequence_path, sequence_first_frame
                if _is_sequence_path(preview):
                    first = sequence_first_frame(preview)
                    if first and os.path.isfile(first):
                        path = preview
                elif os.path.isfile(preview):
                    path = preview
            if not path:
                path = orig
        if not path:
            return None
        key = str(sid or path)
        caps = getattr(self.pool, "_caps", None)
        if caps is not None and key not in caps:
            # Q144：解码器被 LRU 挤掉后重开，新解码器在第 0 帧；帧状态里的
            # decode_cursor 还是旧位置，顺序取帧会从头 grab 出错的画面。强制下一帧 seek。
            st = self._states.get(str(sid or ""))
            if isinstance(st, dict) and st.get("decode_cursor", -1.0) >= 0:
                st["decode_cursor"] = -1.0
        return self.pool.get(key, path)
