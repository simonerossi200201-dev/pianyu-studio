# -*- coding: utf-8 -*-
"""Offscreen preview paintEvent benchmark (T14 §2).

    runtime/venv/Scripts/python.exe tools/preview_bench.py

Loads golden g01 video + 30s of one-sentence-per-second subtitles with
word highlight, a background box, and the g01 entrance animation. Drives
the canvas at 30 fps for 30 seconds of timeline. Requires CUTORA_PERF=1
(this script sets it before importing UI).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

os.environ["CUTORA_PERF"] = "1"
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if "CUTORA_USER_DATA" not in os.environ:
    os.environ["CUTORA_USER_DATA"] = tempfile.mkdtemp(prefix="cutora_t14_bench_")

from cutora.core.runtime import init_torch_runtime

init_torch_runtime(render_child=False)

from cutora.app import apply_ui_locale
from PySide6.QtCore import QEventLoop, QTimer, Qt
from PySide6.QtWidgets import QApplication, QStyleFactory
from cutora.core import preview_perf
from cutora.core.commands import get_bus
from cutora.ui.main_window import MainWindow
from cutora.ui.panels.fonts import load_embedded_fonts
from cutora.ui.theme.apply import apply_app_qss
from cutora.ui.theme.fonts_ui import register_ui_fonts, ui_qfont

G01_PROJ = os.path.join(
    REPO, "tests", "golden", "g01_vertical_basic", "project.asproj.json")
G01_WORDS = (
    "Hello friends welcome to the studio today we will learn how to edit "
    "by meaning peace be with you let us begin this journey together"
).split()
BENCH_DURATION_S = 30.0
BENCH_FPS = 30.0
REPORT_DIR = os.path.join(REPO, "docs", "plan", "reports")


def _pump(app, ms: int = 80) -> None:
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()
    app.processEvents()


def make_segments(n: int = 30, dur: float = 1.0) -> list[dict]:
    """One sentence per second, each word timed for highlight."""
    segs = []
    words_n = 6
    for i in range(n):
        chunk = [G01_WORDS[(i * words_n + k) % len(G01_WORDS)]
                 for k in range(words_n)]
        text = " ".join(chunk)
        start = float(i) * dur
        end = start + dur
        wdur = dur / float(words_n)
        words = []
        t = start
        for wi, w in enumerate(chunk):
            words.append({
                "word": w, "start": t, "end": t + wdur, "wid": i * 10 + wi,
            })
            t += wdur
        segs.append({
            "start": start, "end": end, "text": text,
            "words": words, "role": "default",
        })
    return segs


def _apply_pack(task: dict, pack_id: str | None) -> None:
    if not pack_id:
        return
    from cutora.subtitle.stylepack import apply_pack_to_task, builtin_dir, load_pack
    path = os.path.join(builtin_dir(), pack_id)
    pack = load_pack(path)
    apply_pack_to_task(task, pack, apply_animation=True)


def _inject_fade_overlay(task: dict, amount: float) -> None:
    amt = max(0.0, min(0.5, float(amount)))
    ovs = task.setdefault("image_overlays", [])
    ovs.append({
        "type": "image",
        "start": 0.0, "end": 30.0,
        "x": 80, "y": 80, "w": 640, "h": 360,
        "opacity": 255,
        "elements": [{
            "kind": "shape", "shape": "round_rect",
            "x": 0, "y": 0, "w": 640, "h": 360,
            "fill_color": "#22D3EE", "opacity": 255,
            "radius": 24,
            "fade_top": amt, "fade_bottom": amt,
            "fade_left": 0.0, "fade_right": 0.0,
        }],
    })


def _inject_emphasis_scatter(task: dict) -> None:
    style = task.setdefault("style", {})
    fx = dict(style.get("fx") or {})
    fx["emphasis"] = {
        "auto": ["numbers"],
        "words": ["CUTORA"],
        "color": "#22D3EE",
        "palette": ["#7C3AED", "#22D3EE"],
        "scale": 1.25,
        "font": "emphasis",
        "anim": "none",
        "max_per_cue": 2,
    }
    fx["scatter"] = {
        "enabled": True,
        "size_jitter": 0.2,
        "pos_jitter_em": [0.4, 0.2],
        "rot_jitter_deg": 4.0,
    }
    style["fx"] = fx


def _inject_loop_anim(task: dict, name: str) -> None:
    style = task.setdefault("style", {})
    fx = dict(style.get("fx") or {})
    anim = dict(fx.get("anim") or {})
    anim["in"] = "none"
    anim["out"] = "none"
    anim["loop"] = str(name)
    fx["anim"] = anim
    style["fx"] = fx


def _inject_in_anim(task: dict, name: str, in_s: float = 1.0) -> None:
    style = task.setdefault("style", {})
    style["anim_in_type"] = "none"
    fx = dict(style.get("fx") or {})
    anim = dict(fx.get("anim") or {})
    anim["in"] = str(name)
    anim["out"] = "none"
    anim["loop"] = "none"
    anim["in_s"] = float(in_s)
    fx["anim"] = anim
    style["fx"] = fx


def _inject_demo_load(task: dict) -> None:
    """Thick glass + fade in + karaoke + overlay fade. No --flat-glass."""
    _apply_pack(task, "glass-white")
    _inject_fade_overlay(task, 0.25)
    style = task.setdefault("style", {})
    style["anim_in_type"] = "none"
    fx = dict(style.get("fx") or {})
    bg = dict(fx.get("bg") or {})
    bg["type"] = "glass"
    if "glass_thickness_em" not in bg:
        bg["glass_thickness_em"] = 0.14
    fx["bg"] = bg
    anim = dict(fx.get("anim") or {})
    anim["in"] = "fade"
    anim["loop"] = "karaoke"
    anim["out"] = "none"
    anim["in_s"] = 1.0
    anim["easing"] = "ease_out"
    fx["anim"] = anim
    emph = dict(fx.get("emphasis") or {})
    if not emph.get("color"):
        emph["color"] = "#22D3EE"
    fx["emphasis"] = emph
    style["fx"] = fx


def _prepare_task(win, duration_s: float, pack_id: str | None = None,
                  flat_glass: bool = False, overlay_fade=None,
                  emphasis_scatter: bool = False, loop_anim: str | None = None,
                  in_anim: str | None = None, demo_load: bool = False) -> dict:
    task = win.tasks[win.curr_idx]
    style = task.setdefault("style", {})
    style["dynamic_box"] = True
    style["use_box"] = True
    style["use_stroke"] = True
    if not style.get("anim_in_type") or style.get("anim_in_type") == "none":
        style["anim_in_type"] = "fade"
    segs = make_segments(n=int(duration_s), dur=1.0)
    task["segments"] = segs
    task["text"] = " ".join(s["text"] for s in segs)
    _apply_pack(task, pack_id)
    if demo_load:
        _inject_demo_load(task)
    if flat_glass and not demo_load:
        style = task.setdefault("style", {})
        fx = dict(style.get("fx") or {})
        bg = dict(fx.get("bg") or {})
        bg["glass_thickness_em"] = 0.0
        fx["bg"] = bg
        style["fx"] = fx
    if overlay_fade is not None and not demo_load:
        _inject_fade_overlay(task, overlay_fade)
    if emphasis_scatter:
        _inject_emphasis_scatter(task)
    if not demo_load:
        if loop_anim:
            _inject_loop_anim(task, loop_anim)
        if in_anim:
            _inject_in_anim(task, in_anim, in_s=1.0)
    tl = task.get("timeline")
    if tl is not None:
        for tr in getattr(tl, "tracks", []) or []:
            if getattr(tr, "type", None) != "video":
                continue
            for clip in getattr(tr, "clips", []) or []:
                clip.loop = True
                clip.match_duration = True
                clip._match_target_dur = float(duration_s)
    return task


def _inject_stickers(task: dict, n: int) -> None:
    from cutora.render.stickers import catalog_items, default_size_px, raster_sticker
    from cutora.subtitle.io import canvas_size
    n = max(0, int(n))
    if n <= 0:
        return
    items = catalog_items()
    if not items:
        return
    w, h = canvas_size(task)
    size = default_size_px(w, h)
    px = max(1, int(round(size)))
    ovs = task.setdefault("image_overlays", [])
    for i in range(n):
        it = items[i % len(items)]
        sid = str(it.get("id") or "")
        raster_sticker(sid, px)
        dx = (i % 5) * (w / 6.0) + w * 0.08
        dy = (i // 5) * (h / 6.0) + h * 0.08
        ovs.append({
            "type": "image",
            "start": 0.0, "end": 30.0,
            "x": 0, "y": 0, "w": w, "h": h,
            "opacity": 255,
            "elements": [{
                "kind": "image",
                "sticker_id": sid,
                "x": dx, "y": dy, "w": size, "h": size,
                "opacity": 255, "anim_in": "none", "anim_out": "none",
            }],
        })


def _quiet_playback(win) -> None:
    win._preview_fx_on = False
    win._sfx_preview_on = False
    win._timeline_playing = False
    if hasattr(win, "timer"):
        win.timer.stop()
    if hasattr(win, "media_player"):
        win.media_player.pause()
    if hasattr(win, "_suspend_preview_timers"):
        win._suspend_preview_timers()


def _nudge_geom(task, canvas, frame_i: int) -> None:
    """Simulate a position slider tick so cache-invalidation is measurable."""
    from cutora.subtitle.stylepack import resolve_style_geometry, set_position_px
    style = task.get("style")
    if not isinstance(style, dict):
        return
    dims = getattr(canvas, "_video_dims", (1920, 1080))
    cw, ch = int(dims[0] or 1920), int(dims[1] or 1080)
    got = resolve_style_geometry(style, cw, ch)
    # Monotonic: every frame a new pos so layout/ROI cannot ping-pong-hit.
    set_position_px(
        style,
        float(got.get("pos_x") or cw / 2.0),
        float(got.get("pos_y") or ch * 0.75) + 2.0,
        cw, ch,
    )


def _drive(canvas, task, duration_s: float, fps: float, app,
           hold_t: float | None = None, scrub: bool = False) -> dict:
    n_frames = int(duration_s * fps)
    budget = 1.0 / fps
    canvas.set_data(task, 0.0)
    app.processEvents()
    warm = (hold_t, hold_t, hold_t, hold_t) if hold_t is not None else (0.0, 0.4, 1.0, 2.0)
    for t in warm:
        canvas._current_time = t
        canvas.repaint()
        _pump(app, 40)
    try:
        from cutora.render.textfx.pipeline import reset_degrade, reset_draw_counters
        reset_degrade()
        reset_draw_counters()
    except Exception:
        pass
    preview_perf.reset_samples()
    wall0 = time.perf_counter()
    deadline = wall0
    for i in range(n_frames):
        if scrub:
            _nudge_geom(task, canvas, i)
        canvas._current_time = hold_t if hold_t is not None else (i / fps)
        canvas.repaint()
        deadline += budget
        now = time.perf_counter()
        remain = deadline - now
        if remain > 0.002:
            time.sleep(remain - 0.001)
        while time.perf_counter() < deadline:
            pass
    wall_s = time.perf_counter() - wall0
    preview_perf.flush()
    out = preview_perf.summary()
    out["wall_s"] = round(wall_s, 3)
    out["target_frames"] = n_frames
    out["actual_fps"] = round(n_frames / wall_s, 3) if wall_s else 0.0
    try:
        from cutora.render.textfx.pipeline import draw_counters
        out.update(draw_counters())
    except Exception:
        pass
    return out


def _save_outputs(summary: dict, tag: str, out_dir: str, prefix: str = "T14_preview_perf_before") -> None:
    os.makedirs(out_dir, exist_ok=True)
    sum_path = os.path.join(out_dir, "%s_%s.json" % (prefix, tag))
    with open(sum_path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    src = summary.get("jsonl")
    if src and os.path.isfile(src):
        dst = os.path.join(out_dir, "%s_%s.jsonl" % (prefix, tag))
        shutil.copy2(src, dst)
        summary["jsonl_copy"] = dst
    print("wrote", sum_path, flush=True)


def _print_summary(tag: str, summary: dict) -> None:
    print("=== preview_bench %s ===" % tag, flush=True)
    keys = (
        "n", "target_frames", "wall_s", "actual_fps", "dropped",
        "draw_layers_pct_of_total", "video_pct_of_total",
        "layout_pct_of_draw_layers", "stroke_shadow_pct_of_draw_layers",
        "bg_box_pct_of_draw_layers", "total_ms", "draw_layers_ms",
        "video_ms", "layout_ms", "stroke_shadow_ms", "bg_box_ms",
        "draw_subtitle_ms", "canvas_w", "canvas_h",
        "fx_spec_ms", "fx_hash_ms", "fx_layout_ms", "fx_raster_ms",
        "fx_bd_fmt_ms", "fx_glass_blur_ms", "fx_glass_compose_ms",
        "roi_cache_hits", "roi_cache_misses",
        "text_raster_calls", "bg_raster_calls", "subtitle_draw_calls",
        "layout_cache_hits", "layout_cache_misses",
    )
    extra = summary.get("extra") or {}
    for k in keys:
        if k in summary:
            print("  %s: %s" % (k, summary[k]), flush=True)
        elif k in extra:
            print("  %s: %s" % (k, extra[k]), flush=True)


def _force_canvas_size(canvas, w: int, h: int, app) -> None:
    """Offscreen showFullScreen stays at the fake screen size; pin the widget."""
    canvas.setWindowFlags(Qt.WindowType.Window)
    canvas.setMinimumSize(w, h)
    canvas.resize(w, h)
    canvas.show()
    _pump(app, 80)
    if canvas.width() < w or canvas.height() < h:
        canvas.setFixedSize(w, h)
        _pump(app, 80)
    print("canvas size %dx%d (wanted %dx%d)" % (
        canvas.width(), canvas.height(), w, h), flush=True)


def _run_mode(app, win, canvas, task, tag, duration_s, fps, out_dir,
              prefix: str = "T14_preview_perf_before",
              hold_t: float | None = None, scrub: bool = False) -> dict:
    preview_perf.set_extra({
        "mode": tag,
        "canvas_w": int(canvas.width()),
        "canvas_h": int(canvas.height()),
        "video_dims": list(getattr(canvas, "_video_dims", (0, 0))),
    })
    canvas.set_data(task, 1.2)
    canvas.repaint()
    _pump(app, 80)
    pix = canvas.grab()
    img_dir = os.path.join(out_dir, "img")
    os.makedirs(img_dir, exist_ok=True)
    grab_name = "T14_bench_%s.png" % tag
    if prefix and not prefix.startswith("T14_"):
        grab_name = "%s_%s.png" % (prefix, tag)
    img_path = os.path.join(img_dir, grab_name)
    pix.save(img_path, "PNG")
    summary = _drive(
        canvas, task, duration_s, fps, app, hold_t=hold_t, scrub=scrub)
    extra = summary.get("extra") or {}
    extra["canvas_w"] = int(canvas.width())
    extra["canvas_h"] = int(canvas.height())
    extra["grab"] = img_path
    summary["extra"] = extra
    _save_outputs(summary, tag, out_dir, prefix=prefix)
    _print_summary(tag, summary)
    return summary


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="T14 preview paintEvent bench")
    p.add_argument("--duration", type=float, default=BENCH_DURATION_S)
    p.add_argument("--fps", type=float, default=BENCH_FPS)
    p.add_argument("--mode", choices=("window", "fullscreen", "both"),
                   default="both")
    p.add_argument("--out-dir", default=REPORT_DIR)
    p.add_argument("--project", default=G01_PROJ)
    p.add_argument("--pack", default="", help="builtin style pack id to apply")
    p.add_argument("--tag-prefix", default="T14_preview_perf_before",
                   help="output json filename prefix")
    p.add_argument("--hold", type=float, default=None,
                   help="keep playhead at this t (cache-hit / stay bench)")
    p.add_argument("--flat-glass", action="store_true",
                   help="force bg.glass_thickness_em=0 after applying --pack")
    p.add_argument("--overlay-fade", type=float, default=None,
                   help="inject a shape overlay with four-edge fade (0–0.5)")
    p.add_argument("--emphasis-scatter", action="store_true",
                   help="enable emphasis.auto + scatter for T04f-3 bench")
    p.add_argument("--loop-anim", default="",
                   help="set fx.anim.loop (float_loop / zoom_slow / scroll)")
    p.add_argument("--in-anim", default="",
                   help="set fx.anim.in (reveal_zoom_blur) and in_s=1")
    p.add_argument("--scrub-geom", action="store_true",
                   help="nudge pos_y every frame (slider-drag cache miss)")
    p.add_argument("--demo-load", action="store_true",
                   help="full load: glass-white + fade + karaoke + overlay fade")
    p.add_argument("--stickers", type=int, default=0,
                   help="insert N cached stickers")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    apply_ui_locale()
    app = QApplication.instance() or QApplication(["preview-bench"])
    load_embedded_fonts()
    register_ui_fonts()
    app.setFont(ui_qfont())
    app.setStyle(QStyleFactory.create("Fusion"))
    apply_app_qss(app)

    win = MainWindow()
    win.show()
    _pump(app, 200)
    win.show_editor()
    win.set_ui_mode("pro", persist=False)
    win.resize(1700, 1000)
    _pump(app, 200)

    bus = get_bus()
    bus.bind_window(win)
    bus.execute("project.open", {"path": args.project}, source="api")
    _pump(app, 250)
    _quiet_playback(win)
    task = _prepare_task(
        win, args.duration, pack_id=(args.pack or None),
        flat_glass=bool(args.flat_glass),
        overlay_fade=args.overlay_fade,
        emphasis_scatter=bool(args.emphasis_scatter),
        loop_anim=(args.loop_anim or None),
        in_anim=(args.in_anim or None),
        demo_load=bool(args.demo_load))
    if args.stickers:
        _inject_stickers(task, args.stickers)
    canvas = win.video_canvas
    results = {}
    prefix = args.tag_prefix

    if args.mode in ("window", "both"):
        results["window"] = _run_mode(
            app, win, canvas, task, "window",
            args.duration, args.fps, args.out_dir, prefix=prefix,
            hold_t=args.hold, scrub=args.scrub_geom)

    if args.mode in ("fullscreen", "both"):
        jsonl = (preview_perf.summary() or {}).get("jsonl")
        if jsonl and os.path.isfile(jsonl):
            os.remove(jsonl)
        preview_perf.reset_samples()
        _force_canvas_size(canvas, 1920, 1080, app)
        results["fullscreen"] = _run_mode(
            app, win, canvas, task, "fullscreen",
            args.duration, args.fps, args.out_dir, prefix=prefix,
            hold_t=args.hold, scrub=args.scrub_geom)

    combined = os.path.join(args.out_dir, prefix + ".json")
    with open(combined, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(results, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    print("wrote", combined, flush=True)
    win.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
