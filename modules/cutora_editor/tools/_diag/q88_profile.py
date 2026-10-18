# -*- coding: utf-8 -*-
"""Q88: time preview + export of a fixed 720x1280 split+GIF scene.

Scene (product path, not a private helper):
  720x1280, 30 fps, 10 s
  2x2 split with glass divider
  2 looping GIF overlays
  1 text layer
  1 subtitle

Measures:
  preview play  10 s of frame_at (300 frames)
  preview scrub  5 s of random seeks
  preview drag   5 s of GIF x/y changes + frame_at
  export         10 s of RenderWorker._compose_export_frame

Writes JSON + Markdown. --phase before|after selects the report name.
Do not change product code while gathering the "before" numbers.
"""
from __future__ import annotations

import argparse
import cProfile
import json
import os
import pstats
import platform
import statistics
import struct
import subprocess
import sys
import tempfile
import time
import zlib
from io import StringIO

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("CUTORA_SKIP_ENCODER_PROBE", "1")

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

W, H = 720, 1280
FPS = 30.0
DUR = 10.0
N_PLAY = int(DUR * FPS)          # 300
N_SCRUB = int(5.0 * FPS)         # 150
N_DRAG = int(5.0 * FPS)          # 150
N_EXPORT = int(DUR * FPS)        # 300
GIF_W, GIF_H = 256, 256
GIF_FRAMES = 12

REPORT_DIR = os.path.join(REPO, "docs", "plan", "reports")


def _ffmpeg():
    from cutora.core.config import FFMPEG_PATH
    return FFMPEG_PATH


def _pct(xs, p):
    if not xs:
        return 0.0
    ys = sorted(float(x) for x in xs)
    if len(ys) == 1:
        return ys[0]
    k = (len(ys) - 1) * (p / 100.0)
    lo = int(k)
    hi = min(lo + 1, len(ys) - 1)
    t = k - lo
    return ys[lo] * (1.0 - t) + ys[hi] * t


def _stats(xs):
    xs = [float(x) for x in xs]
    if not xs:
        return {"n": 0, "p50_ms": 0.0, "p95_ms": 0.0, "max_ms": 0.0, "mean_ms": 0.0}
    return {
        "n": len(xs),
        "p50_ms": round(_pct(xs, 50), 3),
        "p95_ms": round(_pct(xs, 95), 3),
        "max_ms": round(max(xs), 3),
        "mean_ms": round(statistics.fmean(xs), 3),
    }


def _write_rgba_png(path, rgba):
    h, w = rgba.shape[:2]
    raw = b"".join(b"\x00" + rgba[y].tobytes() for y in range(h))

    def chunk(tag, data):
        return (
            struct.pack(">I", len(data)) + tag + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)
    with open(path, "wb") as fh:
        fh.write(b"\x89PNG\r\n\x1a\n")
        fh.write(chunk(b"IHDR", ihdr))
        fh.write(chunk(b"IDAT", zlib.compress(raw, 9)))
        fh.write(chunk(b"IEND", b""))


def _color_mp4(path, color, seconds, w, h, fps):
    r = subprocess.run(
        [
            _ffmpeg(), "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi",
            "-i", "color=c=%s:s=%dx%d:d=%.3f:r=%d" % (color, w, h, seconds, int(fps)),
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an", path,
        ],
        capture_output=True, timeout=60,
    )
    if r.returncode != 0 or not os.path.isfile(path):
        raise RuntimeError("color mp4 failed: %s" % ((r.stderr or b"")[-400:],))
    return path


def _make_gif(workdir, name, rgb0, rgb1, n_frames=GIF_FRAMES):
    import numpy as np
    pngs = []
    for i in range(n_frames):
        t = i / max(1, n_frames - 1)
        r = int(rgb0[0] + (rgb1[0] - rgb0[0]) * t)
        g = int(rgb0[1] + (rgb1[1] - rgb0[1]) * t)
        b = int(rgb0[2] + (rgb1[2] - rgb0[2]) * t)
        rgba = np.zeros((GIF_H, GIF_W, 4), dtype=np.uint8)
        m = 24
        rgba[m:GIF_H - m, m:GIF_W - m, 0] = r
        rgba[m:GIF_H - m, m:GIF_W - m, 1] = g
        rgba[m:GIF_H - m, m:GIF_W - m, 2] = b
        rgba[m:GIF_H - m, m:GIF_W - m, 3] = 255
        p = os.path.join(workdir, "%s_%02d.png" % (name, i))
        _write_rgba_png(p, rgba)
        pngs.append(p)
    pal = os.path.join(workdir, "%s_pal.png" % name)
    subprocess.run(
        [_ffmpeg(), "-y", "-hide_banner", "-loglevel", "error",
         "-i", pngs[0], "-vf", "palettegen=reserve_transparent=1", pal],
        check=True, timeout=40,
    )
    lst = os.path.join(workdir, "%s.txt" % name)
    with open(lst, "w", encoding="utf-8") as fh:
        for p in pngs:
            fh.write("file '%s'\nduration 0.08\n" % p.replace("\\", "/"))
        fh.write("file '%s'\n" % pngs[-1].replace("\\", "/"))
    gif = os.path.join(workdir, "%s.gif" % name)
    r = subprocess.run(
        [_ffmpeg(), "-y", "-hide_banner", "-loglevel", "error",
         "-f", "concat", "-safe", "0", "-i", lst, "-i", pal,
         "-lavfi", "[0:v][1:v]paletteuse=alpha_threshold=128", gif],
        capture_output=True, timeout=40,
    )
    if r.returncode != 0 or not os.path.isfile(gif):
        raise RuntimeError("gif failed: %s" % ((r.stderr or b"")[-400:],))
    return gif


def build_scene(workdir):
    """Fixed Q88 scene. Returns (task, gif_clips)."""
    from cutora.assets.user import place_on_timeline
    from cutora.media.animated import import_animated_as_clip
    from cutora.model.factory_defaults import overlay_defaults
    from cutora.model.timeline import Timeline
    from cutora.model.timeline_model import Clip, MediaSource, Track
    from cutora.render.split_layout import apply_split_to_task, set_track_cell
    from cutora.subtitle.track_source import set_segments

    colors = ("0xC04020", "0x2040C0", "0x20A040", "0xC0A020")
    cells = ("A", "B", "C", "D")
    vids = []
    for i, c in enumerate(colors):
        p = os.path.join(workdir, "cell_%s.mp4" % cells[i])
        _color_mp4(p, c, DUR, W, H, FPS)
        vids.append(p)

    gif_a = _make_gif(workdir, "sticker_a", (0, 220, 40), (40, 160, 220))
    gif_b = _make_gif(workdir, "sticker_b", (220, 40, 180), (40, 180, 220))

    tl = Timeline(W, H, FPS)
    tracks = []
    for i, path in enumerate(vids):
        src = MediaSource(path)
        src.has_audio = False
        tl.sources[src.id] = src
        tr = Track("video")
        tr.is_main_video = (i == 0)
        tr.name = "cell_%s" % cells[i]
        clip = Clip(src.id, 0.0, DUR, 0.0)
        clip.fit_mode = "fill"
        tr.clips.append(clip)
        tl.add_track(tr)
        tracks.append(tr)

    task = {
        "timeline": tl,
        "video": vids[0],
        "path": vids[0],
        "segments": [],
        "text_overlays": [],
        "image_overlays": [],
        "duration": DUR,
        "trim_end_sec": DUR,
        "style": {},
    }
    apply_split_to_task(task, layout="grid_2x2", enabled=True, border={
        "style": "glass", "width_px": 10, "color": "#F2C14E",
        "color2": "#FFFFFF", "curve": "straight", "shadow_px": 12, "glass_sigma": 8,
    })
    for tr, cid in zip(tracks, cells):
        set_track_cell(task, tr.id, cid)

    gif_clips = []
    for i, gif in enumerate((gif_a, gif_b)):
        rec = {
            "type": "animated",
            "stored_path": gif,
            "source_path": gif,
            "alpha": True,
        }
        clip = place_on_timeline(tl, rec, start_t=0.0, task=task)
        clip.loop = True
        clip.match_duration = True
        clip._match_target_dur = DUR
        clip.size_mode = "source"
        clip.x = -180.0 if i == 0 else 180.0
        clip.y = -220.0 if i == 0 else 260.0
        clip.scale = 0.85
        gif_clips.append(clip)

    ov = overlay_defaults("text", canvas_w=W, canvas_h=H, start=0.0, duration=DUR)
    ov["content"] = "Q88 preview"
    ov["fontsize"] = 64
    ov["y"] = int(H * 0.72)
    task.setdefault("text_overlays", []).append(ov)

    from cutora.subtitle.stylepack import default_fx, set_position_px
    fx = default_fx()
    fx["stroke"]["width_em"] = 0.04
    style = {
        "font_family": "Arimo",
        "fontsize": 48,
        "bold": True,
        "anchor": "bottom-center",
        "fx": fx,
    }
    set_position_px(style, W / 2.0, H * 0.88, W, H)
    task["style"] = style

    set_segments(task, [{
        "id": "s0",
        "start": 0.0,
        "end": DUR,
        "text": "split plus gif overlay",
        "words": [
            {"word": "word", "start": 0.0, "end": 3.0},
            {"word": "plus", "start": 3.0, "end": 6.0},
            {"word": "gif", "start": 6.0, "end": 8.0},
            {"word": "overlay", "start": 8.0, "end": DUR},
        ],
    }])
    return task, gif_clips


def _qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(["q88-profile"])


def _compose_export_frame(task, t, rw, ctx, canvas):
    return rw._compose_export_frame(t, canvas, ctx)


def _make_export_ctx(task):
    import threading
    from PySide6.QtCore import Qt, QThread
    from PySide6.QtGui import QImage
    from cutora.model.canvas import canvas_wh
    from cutora.model.track_guard import video_tracks_for_render
    from cutora.render.compose_parallel import ExportComposeCtx
    from cutora.render.export_renderer import ExportRenderer
    from cutora.render.frame_renderer import compute_export_render_order
    from cutora.render.render_worker import RenderWorker, _ExportCapPool
    from cutora.render.textfx.spec import task_has_glass_bg

    w, h = canvas_wh(task)
    tl = task.get("timeline")
    rw = RenderWorker.__new__(RenderWorker)
    QThread.__init__(rw)
    rw._geom_last_log_t = 0.0
    rw.project = task
    rw._pipe_stop = threading.Event()
    rw._is_running = True
    video_tracks = video_tracks_for_render(task)
    src_paths = {}
    src_last_end = {}
    frame_states = {}
    for tr in video_tracks:
        for clip_obj in getattr(tr, "clips", []) or []:
            sid = getattr(clip_obj, "source_id", None)
            if not sid:
                continue
            src = tl.sources.get(sid) if tl else None
            src_path = getattr(src, "path", None) if src else None
            if src_path and os.path.exists(src_path):
                src_paths[sid] = src_path
            try:
                cs = float(getattr(clip_obj, "start_t", 0.0) or 0.0)
                eff = float(getattr(clip_obj, "effective_duration", 0.0) or 0.0)
                if eff <= 0:
                    eff = max(
                        0.0,
                        float(getattr(clip_obj, "out_t", 0.0))
                        - float(getattr(clip_obj, "in_t", 0.0)),
                    )
                ce = cs + eff
            except Exception:
                ce = float("inf")
            if bool(getattr(clip_obj, "loop", False)):
                ce = float("inf")
            src_last_end[sid] = max(src_last_end.get(sid, 0.0), ce)
    video_caps = _ExportCapPool(src_paths, src_last_end, frame_states)
    canvas = QImage(int(w), int(h), QImage.Format.Format_ARGB32_Premultiplied)
    canvas.fill(Qt.GlobalColor.black)
    ctx = ExportComposeCtx(
        project=task, width=w, height=h,
        video_tracks=video_tracks, video_caps=video_caps,
        frame_states=frame_states, fallback_cap=None,
        overlay_path=None, fallback_clip=None,
        render_order=compute_export_render_order(task),
        overlay_index=ExportRenderer.build_overlay_index(task),
        task_glass=task_has_glass_bg(task),
    )
    return rw, ctx, canvas, video_caps


def _time_loop(fn, n, label):
    xs = []
    t0 = time.perf_counter()
    for i in range(n):
        s = time.perf_counter()
        fn(i)
        xs.append((time.perf_counter() - s) * 1000.0)
        if (i + 1) % 50 == 0:
            print("  %s %d/%d  last=%.1fms" % (label, i + 1, n, xs[-1]), flush=True)
    wall = time.perf_counter() - t0
    out = _stats(xs)
    out["wall_s"] = round(wall, 3)
    out["ms_per_frame"] = round((wall / max(1, n)) * 1000.0, 3)
    print("  %s done n=%d p50=%.1f p95=%.1f max=%.1f wall=%.2fs" % (
        label, out["n"], out["p50_ms"], out["p95_ms"], out["max_ms"], wall), flush=True)
    return out


def _hotspots(pr, n=30):
    buf = StringIO()
    stats = pstats.Stats(pr, stream=buf)
    stats.sort_stats("tottime")
    stats.print_stats(n)
    text = buf.getvalue()
    rows = []
    started = False
    for line in text.splitlines():
        if "ncalls" in line and "tottime" in line:
            started = True
            continue
        if not started:
            continue
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 5)
        if len(parts) < 6:
            continue
        try:
            rows.append({
                "ncalls": parts[0],
                "tottime": float(parts[1]),
                "percall": float(parts[2]),
                "cumtime": float(parts[3]),
                "location": parts[5],
            })
        except (TypeError, ValueError):
            continue
        if len(rows) >= n:
            break
    return rows, text


def _classify(loc: str) -> str:
    s = loc.lower()
    if any(k in s for k in ("read", "decode", "ffmpeg", "videocapture", "cap.read",
                             "gifdecode", "animated", "pipe")):
        return "解码"
    if any(k in s for k in ("cvtcolor", "bgra", "premultiply", "rgb", "bgr",
                             "converttoformat")):
        return "颜色转换"
    if any(k in s for k in ("gaussian", "mgrid", "numpy", "ndimage", "distance",
                             "split_screen", "_draw_glass", "_build_masks")):
        return "整帧 numpy"
    if any(k in s for k in ("qimage", "copy()", ".copy", "frombuffer")):
        return "重复构建 QImage"
    if any(k in s for k in ("cache",)):
        return "没缓存 / 缓存未命中"
    if any(k in s for k in ("paintevent", "main_window", "present_preview",
                             "qapplication", "processevents")):
        return "主线程阻塞"
    if any(k in s for k in ("grade", "lut", "trilinear")):
        return "调色"
    if any(k in s for k in ("textfx", "layout", "drawtext", "subtitle", "overlay")):
        return "文字 / 字幕"
    return "合成 / 其它"


def _machine():
    info = {
        "os": platform.platform(),
        "python": sys.version.split()[0],
        "cpu": platform.processor() or platform.machine(),
        "w": W, "h": H, "fps": FPS, "dur_s": DUR,
    }
    try:
        import psutil
        info["ram_gb"] = round(psutil.virtual_memory().total / (1024 ** 3), 1)
        info["cpu_count"] = psutil.cpu_count(logical=True)
    except Exception:
        info["cpu_count"] = os.cpu_count()
    return info


def run(phase="before", quick=False):
    _qapp()
    n_play = 30 if quick else N_PLAY
    n_scrub = 15 if quick else N_SCRUB
    n_drag = 15 if quick else N_DRAG
    n_export = 30 if quick else N_EXPORT

    work = tempfile.mkdtemp(prefix="q88_profile_")
    print("workdir", work, flush=True)
    t_build = time.perf_counter()
    task, gif_clips = build_scene(work)
    print("scene built in %.2fs" % (time.perf_counter() - t_build), flush=True)

    from cutora.preview.engine import PreviewEngine
    eng = PreviewEngine()

    def play_one(i):
        eng.frame_at(task, i / FPS)

    print("warmup 3 frames", flush=True)
    for i in range(3):
        eng.frame_at(task, i / FPS)

    print("preview play", flush=True)
    play = _time_loop(play_one, n_play, "play")

    print("preview scrub", flush=True)
    # Jump around so the decoder cannot stay sequential.
    def scrub_one(i):
        t = ((i * 7) % n_play) / FPS
        eng.frame_at(task, t)
    scrub = _time_loop(scrub_one, n_scrub, "scrub")

    print("preview drag GIF", flush=True)
    clip = gif_clips[0]
    base_x = float(clip.x)
    base_y = float(clip.y)

    def drag_one(i):
        clip.x = base_x + float(i % 40)
        clip.y = base_y + float((i * 3) % 50)
        # Product drag path drops preview resolution until mouse-up.
        eng.frame_at(task, (i % n_play) / FPS, preview_scale=0.5)
    drag = _time_loop(drag_one, n_drag, "drag")
    clip.x = base_x
    clip.y = base_y

    print("export compose", flush=True)
    rw, ctx, canvas, video_caps = _make_export_ctx(task)
    for i in range(2):
        _compose_export_frame(task, i / FPS, rw, ctx, canvas)

    def export_one(i):
        _compose_export_frame(task, i / FPS, rw, ctx, canvas)
    t_ex = time.perf_counter()
    export = _time_loop(export_one, n_export, "export")
    export["total_s"] = round(time.perf_counter() - t_ex, 3)
    try:
        video_caps.clear()
    except Exception:
        pass

    print("cProfile 60 play frames", flush=True)
    pr = cProfile.Profile()
    n_prof = min(60, n_play)

    def _prof():
        for i in range(n_prof):
            eng.frame_at(task, i / FPS)

    pr.enable()
    _prof()
    pr.disable()
    rows, raw = _hotspots(pr, 30)

    eng.close()

    payload = {
        "phase": phase,
        "quick": bool(quick),
        "machine": _machine(),
        "scene": {
            "canvas": [W, H],
            "fps": FPS,
            "duration_s": DUR,
            "split": "grid_2x2 glass",
            "gifs": 2,
            "gif_px": [GIF_W, GIF_H],
            "gif_frames": GIF_FRAMES,
            "text_layer": True,
            "subtitles": True,
        },
        "preview_play": play,
        "preview_scrub": scrub,
        "preview_drag_gif": drag,
        "export": export,
        "cprofile_top30": rows,
        "cprofile_raw": raw,
        "workdir": work,
    }
    os.makedirs(REPORT_DIR, exist_ok=True)
    json_path = os.path.join(REPORT_DIR, "Q88_profile_%s.json" % phase)
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    md_path = os.path.join(REPORT_DIR, "Q88_profile_%s.md" % phase)
    _write_md(payload, md_path, phase)
    print("wrote", json_path, flush=True)
    print("wrote", md_path, flush=True)
    return payload


def _write_md(payload, path, phase):
    before = None
    if phase == "after":
        prev = os.path.join(REPORT_DIR, "Q88_profile_before.json")
        if os.path.isfile(prev):
            with open(prev, encoding="utf-8") as fh:
                before = json.load(fh)
    m = payload["machine"]
    lines = []
    lines.append("# Q88 预览 / 导出性能（%s）" % ("改前" if phase == "before" else "改后"))
    lines.append("")
    lines.append("固定场景：%d×%d 竖屏、30 fps、10 秒、2×2 分屏（玻璃带）+ 2 个 GIF 叠加 + 1 个文本层 + 字幕。" % (W, H))
    lines.append("预览走 `PreviewEngine.frame_at`，导出走 `RenderWorker._compose_export_frame`（不含 x264）。")
    lines.append("脚本：`tools/_diag/q88_profile.py`。")
    lines.append("")
    lines.append("机器：`%s` · CPU `%s` · 逻辑核 %s · RAM %s GB · Python %s" % (
        m.get("os"), m.get("cpu"), m.get("cpu_count"), m.get("ram_gb", "?"), m.get("python")))
    if payload.get("quick"):
        lines.append("")
        lines.append("**注意：这是 `--quick` 抽样，不是验收数字。**")
    lines.append("")
    lines.append("## 帧耗时")
    lines.append("")
    lines.append("| 路径 | n | p50 ms | p95 ms | max ms | mean ms | 墙钟 s |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|")
    for key, label in (
        ("preview_play", "预览播放"),
        ("preview_scrub", "拖动播放头"),
        ("preview_drag_gif", "画布拖动 GIF"),
        ("export", "导出合成"),
    ):
        s = payload[key]
        lines.append("| %s | %s | %.2f | %.2f | %.2f | %.2f | %.3f |" % (
            label, s["n"], s["p50_ms"], s["p95_ms"], s["max_ms"], s["mean_ms"],
            s.get("wall_s") or s.get("total_s") or 0.0))
    lines.append("")
    if before:
        lines.append("## 前后对照")
        lines.append("")
        lines.append("| 路径 | 改前 p95 | 改后 p95 | 改后/改前 | 门槛 |")
        lines.append("|---|---:|---:|---:|---|")
        gates = {
            "preview_play": ("≤ 33 ms 且 ≤ 改前 40%", 33.0, 0.40),
            "preview_drag_gif": ("≤ 40 ms 且 ≤ 改前 40%", 40.0, 0.40),
            "export": ("总耗时 ≤ 改前 60%", None, 0.60),
        }
        for key, label in (
            ("preview_play", "预览播放 p95"),
            ("preview_scrub", "拖动播放头 p95"),
            ("preview_drag_gif", "拖动 GIF p95"),
            ("export", "导出总耗时"),
        ):
            b = before[key]
            a = payload[key]
            if key == "export":
                bv = float(b.get("total_s") or b.get("wall_s") or 0)
                av = float(a.get("total_s") or a.get("wall_s") or 0)
                ratio = (av / bv) if bv else 0.0
                gate = "≤ 改前 60%"
                ok = av <= bv * 0.60 + 1e-9
                lines.append("| %s | %.3f s | %.3f s | %.1f%% | %s %s |" % (
                    label, bv, av, ratio * 100.0, gate, "PASS" if ok else "FAIL"))
            else:
                bv = float(b["p95_ms"])
                av = float(a["p95_ms"])
                ratio = (av / bv) if bv else 0.0
                abs_lim, rel = gates.get(key, (None, None, None))[1:] if key in gates else (None, None)
                ok_rel = (rel is None) or (av <= bv * rel + 1e-9)
                ok_abs = (abs_lim is None) or (av <= abs_lim + 1e-9)
                gate = gates[key][0] if key in gates else "—"
                lines.append("| %s | %.2f | %.2f | %.1f%% | %s %s |" % (
                    label, bv, av, ratio * 100.0, gate,
                    "PASS" if (ok_rel and ok_abs) else "FAIL"))
        lines.append("")
    lines.append("## cProfile 前 30（预览 frame_at，60 帧）")
    lines.append("")
    lines.append("| # | tottime s | ncalls | 位置 | 原因 |")
    lines.append("|---:|---:|---:|---|---|")
    for i, row in enumerate(payload.get("cprofile_top30") or [], start=1):
        loc = row.get("location") or ""
        lines.append("| %d | %.4f | %s | `%s` | %s |" % (
            i, float(row.get("tottime") or 0), row.get("ncalls"),
            loc.replace("|", "\\|"), _classify(loc)))
    lines.append("")
    if phase == "before":
        lines.append("## 热点说明（改前）")
        lines.append("")
        lines.append("分类来自函数名，结合这条场景的已知路径：")
        lines.append("")
        lines.append("1. **分屏玻璃带**（`split_screen._draw_glass_band`）：每一帧对整幅 720×1280 做 numpy 距离场 + `cv2.GaussianBlur`，几何没变也重算。Q86 的缓存还没落到产品路径。")
        lines.append("2. **GIF / 透明 clip 解码**：`import_animated_as_clip` 把 GIF 转成 PNG-in-MOV（rgba），预览每帧走 `_FFmpegFrameReader.read` 的 bgra 管道；循环回到第 0 帧会 `_seek_to` 杀掉 ffmpeg 再拉起。动图帧数有限，没有按帧的预乘 QImage 缓存。")
        lines.append("3. **QImage 构建**：`_draw_video_overlay` 每帧 `QImage(numpy)` 再 `.copy()`（不变量 9 要求 copy，但不能每帧从解码缓冲新建）。")
        lines.append("4. **预览在 UI 线程**：`MainWindow._present_preview_frame` 直接调 `video_frame_at`，解码和合成堵住事件循环。这条脚本量的是引擎本身；UI 延迟另测。")
        lines.append("5. **颜色 / 拷贝**：alpha 源输出 BGRA 预乘；玻璃带再 `convertToFormat` + `bytes(constBits())` 整帧拷一次。")
        lines.append("")
        lines.append("验收门槛（改后）：预览播放 p95 ≤ 33 ms，拖动 GIF p95 ≤ 40 ms，且都 ≤ 改前的 40%；导出总耗时 ≤ 改前的 60%；golden 逐字节不变。")
        lines.append("")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", choices=("before", "after"), default="before")
    ap.add_argument("--quick", action="store_true")
    args = ap.parse_args(argv)
    run(phase=args.phase, quick=args.quick)


if __name__ == "__main__":
    main()
