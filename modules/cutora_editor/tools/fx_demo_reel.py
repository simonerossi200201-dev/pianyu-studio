# -*- coding: utf-8 -*-
"""T04f-4c demo reel: every implemented anim, 2 s each, named, thick glass."""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile

import cv2
import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from tools.fx_contact_sheet import (  # noqa: E402
    DEFAULT_PROJECT, DEFAULT_T_MS, DEFAULT_VIDEO, SHEET_CELL_H, SHEET_CELL_W,
    _cover_bgr, _deep_merge, _event, _first_task, _grab_frame, _load_project,
    _video_path,
)

CLIP_S = 2.0
FPS = 30.0
FFMPEG = os.path.join(REPO, "runtime", "ffmpeg", "ffmpeg.exe")


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="T04f-4c fx demo reel")
    p.add_argument("--project", default=DEFAULT_PROJECT)
    p.add_argument("--video", default="")
    p.add_argument("--t-ms", type=int, default=DEFAULT_T_MS)
    p.add_argument(
        "--out",
        default=os.path.join(REPO, "docs", "plan", "reports", "img",
                             "T04f-4c_fx_demo_reel.mp4"))
    return p.parse_args(argv)


def _clips():
    from cutora.render.textfx.anim import ANIMS
    clips = [("none", "in", "无")]
    for aid, defn in ANIMS.items():
        if aid == "none":
            continue
        if "in" in defn.phases:
            name = "slide+left" if aid == "slide" else aid
            clips.append((name, "in", defn.label_zh))
        elif "loop" in defn.phases:
            clips.append((aid, "loop", defn.label_zh))
    return clips


def _style_for(pack, aid: str, phase: str) -> dict:
    from cutora.subtitle.stylepack import apply_pack_to_task
    task = {
        "timeline": {"width": SHEET_CELL_W, "height": SHEET_CELL_H},
        "style": {},
    }
    apply_pack_to_task(task, pack, apply_animation=True)
    style = task["style"]
    fx = dict(style.get("fx") or {})
    bg = dict(fx.get("bg") or {})
    bg["type"] = "glass"
    if "glass_thickness_em" not in bg:
        bg["glass_thickness_em"] = 0.14
    fx["bg"] = bg
    anim = {
        "in": "none", "loop": "none", "out": "none",
        "in_s": 1.0, "out_s": 0.25, "easing": "ease_out",
    }
    extra = {}
    sample = "the office next take"
    end = 2.2
    if phase == "in":
        anim["in"] = aid
        if aid == "stack":
            sample = "the office\nnext line"
            anim["in_s"] = 0.8
            end = 4.0
        if aid == "flip_clock":
            end = 5.0
        if aid in ("line_draw", "outline_to_fill"):
            extra["stroke"] = {"width_em": 0.08, "color": "#22D3EE"}
        if aid == "tracking_in":
            anim["in"] = "tracking_in"
    else:
        anim["loop"] = aid
        if aid == "colour_highlight":
            extra["emphasis"] = {
                "color": "#22D3EE",
                "palette": ["#7C3AED", "#22D3EE"],
            }
        if aid in (
                "karaoke", "highlight", "box_highlight",
                "colour_highlight", "rotate_highlight"):
            sample = "one two three four five six"
    fx = _deep_merge(fx, extra)
    fx["anim"] = anim
    emph = dict(fx.get("emphasis") or {})
    if not emph.get("color"):
        emph["color"] = "#22D3EE"
    fx["emphasis"] = emph
    style["fx"] = fx
    style["fontsize"] = 72
    style["fontsize_pct"] = 72.0 / float(min(SHEET_CELL_W, SHEET_CELL_H))
    style["pos_x"] = SHEET_CELL_W / 2.0
    style["pos_y"] = SHEET_CELL_H * 0.58
    style["wrap_lines"] = 4
    return style, sample, end


def _paint_frame(style, event, spec, lay, bg, t, label,
                 from_style, layout_event, raster_roi, QImage, QPainter, QColor):
    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import QFont, QPen
    roi = raster_roi(
        lay, spec, style, bg, SHEET_CELL_W, SHEET_CELL_H,
        skip_blur=False, t=t, event=event)
    frame = QImage(SHEET_CELL_W, SHEET_CELL_H, QImage.Format.Format_ARGB32_Premultiplied)
    frame.fill(0)
    p = QPainter(frame)
    p.drawImage(0, 0, bg)
    p.drawImage(lay.origin[0], lay.origin[1], roi)
    bar = QRectF(0, 0, SHEET_CELL_W, 44)
    p.fillRect(bar, QColor(11, 26, 51, 230))
    p.setPen(QPen(QColor("#F4EBD8")))
    font = QFont("Arimo", 18)
    font.setBold(True)
    p.setFont(font)
    p.drawText(bar.adjusted(16, 0, -16, 0),
               int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
               label)
    p.end()
    return frame


def _qimage_bgr(qimg) -> np.ndarray:
    from cutora.render.textfx.raster import as_bgra
    _o, bgra = as_bgra(qimg)
    bgr = np.empty((bgra.shape[0], bgra.shape[1], 3), dtype=np.uint8)
    a = bgra[..., 3].astype(np.float32) / 255.0
    for i in range(3):
        bgr[..., i] = (bgra[..., i].astype(np.float32) * a
                       + 20.0 * (1.0 - a)).clip(0, 255).astype(np.uint8)
    return bgr


def main(argv=None) -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    if "CUTORA_USER_DATA" not in os.environ:
        os.environ["CUTORA_USER_DATA"] = tempfile.mkdtemp(prefix="cutora_fx_reel_")
    from cutora.core.runtime import init_torch_runtime
    init_torch_runtime(render_child=False)
    args = parse_args(argv)

    from PySide6.QtGui import QColor, QImage, QPainter
    from PySide6.QtWidgets import QApplication, QStyleFactory
    from cutora.app import apply_ui_locale
    from cutora.render.textfx.layout import layout_event
    from cutora.render.textfx.raster import bgr_numpy_to_qimage, raster_roi
    from cutora.render.textfx.spec import from_style
    from cutora.subtitle.stylepack import builtin_dir, load_pack
    from cutora.ui.panels.fonts import load_embedded_fonts
    from cutora.ui.theme.apply import apply_app_qss
    from cutora.ui.theme.fonts_ui import register_ui_fonts, ui_qfont

    apply_ui_locale()
    app = QApplication.instance() or QApplication(["fx-demo-reel"])
    load_embedded_fonts()
    register_ui_fonts()
    app.setFont(ui_qfont())
    app.setStyle(QStyleFactory.create("Fusion"))
    apply_app_qss(app)

    data = _load_project(args.project)
    task = _first_task(data)
    vid = _video_path(task, args.video or None)
    frame_bgr = _grab_frame(vid, args.t_ms)
    crop = _cover_bgr(frame_bgr, SHEET_CELL_W, SHEET_CELL_H)
    qbg = bgr_numpy_to_qimage(crop).convertToFormat(
        QImage.Format.Format_ARGB32_Premultiplied)
    pack = load_pack(os.path.join(builtin_dir(), "glass-white"))
    clips = _clips()
    n_frames = int(round(CLIP_S * FPS))
    tmp = tempfile.mkdtemp(prefix="cutora_reel_")
    idx = 0
    try:
        for aid, phase, title in clips:
            style, sample, end = _style_for(pack, aid, phase)
            event = _event(sample, end=end)
            spec = from_style(style)
            lay = layout_event(event, style, spec, SHEET_CELL_W, SHEET_CELL_H)
            label = "%s  %s" % (title, aid)
            print("clip", label, "lines", len(lay.lines), flush=True)
            for f in range(n_frames):
                t = f / FPS
                img = _paint_frame(
                    style, event, spec, lay, qbg, t, label,
                    from_style, layout_event, raster_roi,
                    QImage, QPainter, QColor)
                path = os.path.join(tmp, "f%05d.png" % idx)
                if not img.save(path, "PNG"):
                    raise SystemExit("failed to write " + path)
                idx += 1
        os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
        cmd = [
            FFMPEG, "-y",
            "-framerate", str(int(FPS)),
            "-i", os.path.join(tmp, "f%05d.png"),
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-crf", "18", "-preset", "veryfast",
            "-movflags", "+faststart",
            args.out,
        ]
        print("ffmpeg", " ".join(cmd), flush=True)
        subprocess.check_call(cmd, cwd=REPO)
        print("wrote", args.out, "frames", idx, "clips", len(clips), flush=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    _ = cv2
    _ = DEFAULT_VIDEO
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
