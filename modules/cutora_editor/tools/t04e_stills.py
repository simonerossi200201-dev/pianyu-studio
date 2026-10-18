# -*- coding: utf-8 -*-
"""T04e rework: real-footage stills + gallery thumbs. No g08 baseline."""
from __future__ import annotations

import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
os.chdir(REPO)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from cutora.core.runtime import init_torch_runtime
init_torch_runtime()

import cv2
from PySide6.QtCore import Qt, QRect
from PySide6.QtGui import QColor, QFont, QImage, QPainter
from PySide6.QtWidgets import QApplication

from cutora.render.export_renderer import ExportRenderer
from cutora.render.frame_renderer import compute_render_order
from cutora.render.readers import _cv2_frame_to_qimage
from cutora.subtitle.layout import relayout_task
from cutora.subtitle.stylepack import (
    SAMPLE_EN, apply_pack_to_task, builtin_dir, load_pack,
)
from cutora.ui.panels.fonts import load_embedded_fonts

IMG = os.path.join(REPO, "docs", "plan", "reports", "img")
G08 = os.path.join(REPO, "tests", "golden", "g08_stylepack_amber")
PACKS = ("minimal-white", "bar-black", "amber-karaoke", "chant", "podcast-card")
NATIVE = (1080, 1920)
LANDSCAPE = (1920, 1080)

VID_DARK = r"D:\1_PROJECT\2026-09-07\B\T0010-李良v01\clips\_stitched\T010.mp4"
VID_BRIGHT = r"D:\1_PROJECT\2026-09-07\B\T0012-李良v03\clips\_stitched\T012.mp4"

SHOTS = (
    ("bright", VID_BRIGHT, 178.0,
     "Can they still enter the Kingdom of Heaven?"),
    ("dark", VID_DARK, 21.0,
     "Unless you repent, you will all perish."),
    ("close", VID_DARK, 217.0,
     "Your definition of repentance is wrong."),
)


def _words(text, start, end):
    toks = [t for t in str(text).split() if t]
    dur = max(0.2, float(end) - float(start))
    step = dur / max(len(toks), 1)
    out = []
    for i, w in enumerate(toks):
        out.append({
            "word": w, "start": start + i * step,
            "end": start + (i + 1) * step, "wid": i,
        })
    return out


def _grab(path, t):
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        raise SystemExit("cannot open %s" % path)
    cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, t * 1000.0))
    ok, frame = cap.read()
    cap.release()
    if not ok or frame is None:
        raise SystemExit("no frame t=%.3f %s" % (t, path))
    img = _cv2_frame_to_qimage(frame)
    if img is None:
        raise SystemExit("qimage failed %s" % path)
    return img


def _to_vertical(src: QImage) -> QImage:
    return src.scaled(
        NATIVE[0], NATIVE[1],
        Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )


def _to_horizontal(src: QImage) -> QImage:
    w, h = src.width(), src.height()
    target_h = int(round(w * 9 / 16))
    if target_h < h:
        y = max(0, int(h * 0.12))
        if y + target_h > h:
            y = h - target_h
        src = src.copy(0, y, w, target_h)
    return src.scaled(
        LANDSCAPE[0], LANDSCAPE[1],
        Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )


def _adapt_style(style: dict, w: int, h: int) -> dict:
    s = dict(style)
    src_w, src_h = NATIVE
    s["pos_x"] = int(int(s.get("pos_x") or 540) * w / src_w)
    bottom = src_h - int(s.get("pos_y") or 1450)
    s["pos_y"] = h - int(bottom * h / src_h)
    return s


def _task(pack, text, t, dims):
    w, h = dims
    start = max(0.0, t - 1.5)
    end = t + 1.5
    task = {
        "dims": [w, h],
        "segments": [{
            "start": start, "end": end, "text": text, "lang": "en",
            "words": _words(text, start, end),
        }],
        "style": {},
        "overlays": [],
        "text_overlays": [],
        "image_overlays": [],
    }
    apply_pack_to_task(task, pack, apply_animation=True)
    task["style"] = _adapt_style(task.get("style") or {}, w, h)
    relayout_task(task)
    return task


def _render(task, bg: QImage, t: float) -> QImage:
    w, h = task["dims"]
    if bg.width() != w or bg.height() != h:
        bg = bg.scaled(w, h, Qt.AspectRatioMode.IgnoreAspectRatio,
                       Qt.TransformationMode.SmoothTransformation)
    order = compute_render_order(task)

    def _draw_bg(painter, image=bg):
        painter.drawImage(0, 0, image)

    return ExportRenderer.render_frame(
        task, w, h, t, draw_background=_draw_bg, render_order=order,
    )


def save(img: QImage, path: str):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    if not img.save(path, "PNG"):
        raise SystemExit("save failed %s" % path)
    print("wrote", path, img.width(), "x", img.height())


def _lower_third(img: QImage) -> QImage:
    w, h = img.width(), img.height()
    top = int(h * 0.58)
    return img.copy(0, top, w, h - top)


def write_preview(pack, dark_bg, light_bg):
    """9:16 lower-thirds stacked: dark EN / light ZH. 360×480."""
    dest = os.path.join(pack["path"], "preview.png")
    dark_task = _task(pack, SAMPLE_EN, 21.0, NATIVE)
    light_task = _task(pack, SAMPLE_EN, 178.0, NATIVE)
    dark = _lower_third(_render(dark_task, dark_bg, 21.0))
    light = _lower_third(_render(light_task, light_bg, 178.0))
    tw, th = 360, 240
    dark = dark.scaled(tw, th, Qt.AspectRatioMode.IgnoreAspectRatio,
                       Qt.TransformationMode.SmoothTransformation)
    light = light.scaled(tw, th, Qt.AspectRatioMode.IgnoreAspectRatio,
                         Qt.TransformationMode.SmoothTransformation)
    out = QImage(tw, th * 2, QImage.Format.Format_ARGB32)
    p = QPainter(out)
    p.drawImage(0, 0, dark)
    p.drawImage(0, th, light)
    p.end()
    save(out, dest)
    return dest


def _sheet(rows, path, label):
    if not rows:
        return
    w, h = rows[0][1].width(), rows[0][1].height()
    label_h = 40
    sheet = QImage(w, len(rows) * (h + label_h), QImage.Format.Format_ARGB32)
    sheet.fill(QColor("#101418"))
    painter = QPainter(sheet)
    font = QFont("Arimo", 16)
    font.setBold(True)
    painter.setFont(font)
    y = 0
    for name, img in rows:
        painter.fillRect(0, y, w, label_h, QColor("#1c242c"))
        painter.setPen(QColor("#f2f5f8"))
        painter.drawText(QRect(12, y, w - 24, label_h),
                         Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                         name)
        y += label_h
        painter.drawImage(0, y, img)
        y += h
    painter.end()
    save(sheet, path)


def main():
    _app = QApplication.instance() or QApplication([])
    assert load_embedded_fonts(), "fonts"
    os.makedirs(IMG, exist_ok=True)
    grabbed = {}
    for key, path, t, _text in SHOTS:
        grabbed[key] = _grab(path, t)

    dark_v = _to_vertical(grabbed["dark"])
    light_v = _to_vertical(grabbed["bright"])

    v_rows = []
    h_rows = []
    for slug in PACKS:
        pack = load_pack(os.path.join(builtin_dir(), slug))
        write_preview(pack, dark_v, light_v)
        for key, path, t, text in SHOTS:
            src = grabbed[key]
            for tag, dims, conv in (
                ("v", NATIVE, _to_vertical),
                ("h", LANDSCAPE, _to_horizontal),
            ):
                bg = conv(src)
                task = _task(pack, text, t, dims)
                segs = task["segments"]
                assert segs and segs[0].get("lines"), (slug, key, tag)
                canvas = _render(task, bg, t)
                out = os.path.join(IMG, "t04e_%s_%s_%s.png" % (slug, tag, key))
                save(canvas, out)
                if tag == "v":
                    v_rows.append(("%s  %s" % (slug, key), canvas))
                else:
                    h_rows.append(("%s  %s" % (slug, key), canvas))

    def _compact(rows, path, crop_v=True):
        thumbs = []
        for name, img in rows:
            piece = _lower_third(img) if crop_v else img.copy(
                0, int(img.height() * 0.55), img.width(), int(img.height() * 0.45))
            piece = piece.scaledToWidth(540, Qt.TransformationMode.SmoothTransformation)
            thumbs.append((name, piece))
        _sheet(thumbs, path, "c")

    _compact(v_rows, os.path.join(IMG, "t04e_contact_v.png"), True)
    _compact(h_rows, os.path.join(IMG, "t04e_contact_h.png"), False)

    # g08 stills on real footage, still no manifest / no expected.mp4
    pack = load_pack(os.path.join(builtin_dir(), "amber-karaoke"))
    os.makedirs(G08, exist_ok=True)
    g08_rows = []
    for i, (key, path, t, text) in enumerate(SHOTS):
        bg = _to_vertical(grabbed[key])
        task = _task(pack, text, t, NATIVE)
        canvas = _render(task, bg, t)
        save(canvas, os.path.join(IMG, "g08_seg%d.png" % i))
        g08_rows.append(("g08_seg%d  %s" % (i, key), canvas))
    _sheet(g08_rows, os.path.join(IMG, "g08_contact_sheet.png"), "g08")
    readme = os.path.join(G08, "README.md")
    with open(readme, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("# g08_stylepack_amber\n\n")
        fh.write("Amber karaoke on B-roll stills. **No baseline until Luca.**\n")
    print("g08 stills done; no manifest.json")


if __name__ == "__main__":
    main()
