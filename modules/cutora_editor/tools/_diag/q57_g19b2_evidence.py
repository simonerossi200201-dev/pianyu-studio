# -*- coding: utf-8 -*-
"""Q57 evidence: g19b-2 before (recorded bug) vs after (Q55 freeze).

Order: fingerprint -> render after -> stills / curves / table / comparison video.
Does NOT write tests/golden/**/expected.sha256.
"""
from __future__ import annotations

import json
import math
import os
import shutil
import sys
import tempfile

import cv2
import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "tools"))
os.chdir(REPO)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

CASE = "g19b-2_stitch_crossfade"
CASE_DIR = os.path.join(REPO, "tests", "golden", CASE)
BEFORE_MP4 = os.path.join(CASE_DIR, "expected.mp4")
OUT = os.path.join(REPO, "docs", "plan", "reports", "Q57_g19b2")
TRIPLES = os.path.join(OUT, "triples")
AFTER_MP4 = os.path.join(OUT, "after.mp4")
FONT_SC = os.path.join(REPO, "resources", "fonts", "Noto_Sans_SC", "NotoSansSCwght.ttf")

# Last successful record of this case: docs/plan/queue/_log/_rerecord.log
# --- g19b-2_stitch_crossfade ---  2026-09-19
LAST_FP = {
    "fonts_count": 240,
    "fonts_digest": "80429c0971f116bfb759c58db8db60baa539f3762be8dcd4da46f6b00e510e2f",
    "ffmpeg_prefix": "ffmpeg version 2026-01-07-git-af6a1dd0b2-full_build-www.gyan.dev",
    "cv2": "4.9.0",
    "numpy": "1.26.4",
    "pyside6": "6.10.2",
}

FPS = 30.0


def _log(msg: str) -> None:
    print(msg, flush=True)


def luma_bgr(fr: np.ndarray) -> np.ndarray:
    b = fr[..., 0].astype(np.float32)
    g = fr[..., 1].astype(np.float32)
    r = fr[..., 2].astype(np.float32)
    return 0.299 * r + 0.587 * g + 0.114 * b


def luma_rgb(fr: np.ndarray) -> np.ndarray:
    r = fr[..., 0].astype(np.float32)
    g = fr[..., 1].astype(np.float32)
    b = fr[..., 2].astype(np.float32)
    return 0.299 * r + 0.587 * g + 0.114 * b


def qimage_rgba(img) -> np.ndarray:
    from PySide6.QtGui import QImage
    conv = img.convertToFormat(QImage.Format.Format_RGBA8888)
    w, h = conv.width(), conv.height()
    bpl = conv.bytesPerLine()
    raw = bytes(conv.constBits())
    arr = np.frombuffer(raw, dtype=np.uint8)
    if arr.size < h * bpl:
        arr = np.frombuffer(conv.bits(), dtype=np.uint8)
    return arr.reshape((h, bpl))[:, : w * 4].reshape((h, w, 4)).copy()


def frame_index(t: float, fps: float = FPS) -> int:
    return int(math.floor(float(t) * float(fps) + 1e-6))


def ffmpeg_bin() -> str:
    p = os.path.join(REPO, "runtime", "ffmpeg", "ffmpeg.exe")
    return p if os.path.isfile(p) else "ffmpeg"


def compare_fingerprint(fp: dict) -> tuple[bool, list[str]]:
    rows = []
    ok = True

    def _check(name, got, want, prefix=False):
        nonlocal ok
        got_s = str(got or "")
        want_s = str(want or "")
        match = got_s.startswith(want_s) if prefix else got_s == want_s
        rows.append("%s: got=%s want=%s %s" % (name, got_s, want_s, "OK" if match else "MISMATCH"))
        if not match:
            ok = False

    _check("fonts_count", fp.get("fonts_count"), LAST_FP["fonts_count"])
    _check("fonts_digest", fp.get("fonts_digest"), LAST_FP["fonts_digest"])
    _check("ffmpeg", fp.get("ffmpeg"), LAST_FP["ffmpeg_prefix"], prefix=True)
    _check("cv2", fp.get("cv2"), LAST_FP["cv2"])
    _check("numpy", fp.get("numpy"), LAST_FP["numpy"])
    _check("PySide6", fp.get("pyside6"), LAST_FP["pyside6"])
    return ok, rows


def load_seams(task) -> list[dict]:
    from cutora.render.transition_runtime import (
        clip_freeze_deficit_sec,
        clip_timeline_span,
        clips_for_transition,
        source_duration_of,
    )

    tl = task.get("timeline")
    sources = getattr(tl, "sources", {}) or {}
    track = None
    for tr in getattr(tl, "tracks", []) or []:
        if getattr(tr, "type", None) == "video":
            track = tr
            break
    if track is None:
        raise RuntimeError("no video track")
    seams = []
    trans_list = list(getattr(track, "transitions", []) or [])
    trans_list.sort(key=lambda t: float(getattr(t, "start_t", 0.0) or 0.0))
    for i, trans in enumerate(trans_list):
        t0 = float(getattr(trans, "start_t", 0.0) or 0.0)
        dur = float(getattr(trans, "duration", 0.0) or 0.0)
        t1 = t0 + dur
        out_clip, in_clip = clips_for_transition(track, trans)
        out_src = sources.get(getattr(out_clip, "source_id", None)) if out_clip is not None else None
        in_src = sources.get(getattr(in_clip, "source_id", None)) if in_clip is not None else None
        out_dur = source_duration_of(out_clip, source=out_src) if out_clip is not None else 0.0
        in_dur = source_duration_of(in_clip, source=in_src) if in_clip is not None else 0.0
        out_t = float(getattr(out_clip, "out_t", 0.0) or 0.0) if out_clip is not None else 0.0
        in_t = float(getattr(in_clip, "in_t", 0.0) or 0.0) if in_clip is not None else 0.0
        speed_out = max(0.01, float(getattr(out_clip, "speed", 1.0) or 1.0)) if out_clip is not None else 1.0
        speed_in = max(0.01, float(getattr(in_clip, "speed", 1.0) or 1.0)) if in_clip is not None else 1.0
        handle_out_sec = max(0.0, out_dur - out_t) / speed_out
        handle_in_sec = max(0.0, in_t) / speed_in
        gap_out_sec = clip_freeze_deficit_sec(out_clip, trans, "out", out_dur, FPS) if out_clip else 0.0
        gap_in_sec = clip_freeze_deficit_sec(in_clip, trans, "in", in_dur, FPS) if in_clip else 0.0
        n0 = frame_index(t0, FPS)
        n1 = frame_index(t1, FPS)
        # Inclusive window used by preview/export: start_t <= t <= start_t+dur
        while n0 / FPS < t0 - 1e-9:
            n0 += 1
        last = n1
        if last / FPS > t1 + 1e-9:
            last -= 1
        if last < n0:
            last = n0
        ns = list(range(n0, last + 1))
        sample = []
        if len(ns) == 1:
            sample = ns
        else:
            for k in range(5):
                sample.append(ns[int(round(k * (len(ns) - 1) / 4.0))])
            sample = sorted(set(sample))
            while len(sample) < 5 and len(sample) < len(ns):
                for n in ns:
                    if n not in sample:
                        sample.append(n)
                        break
                sample = sorted(sample)
        out_span = clip_timeline_span(out_clip) if out_clip is not None else (None, None)
        in_span = clip_timeline_span(in_clip) if in_clip is not None else (None, None)
        seams.append({
            "i": i,
            "preset": str(getattr(trans, "preset", "") or ""),
            "t0": t0,
            "t1": t1,
            "dur": dur,
            "n0": n0,
            "n1": last,
            "ns": ns,
            "sample": sample,
            "handle_out_sec": handle_out_sec,
            "handle_in_sec": handle_in_sec,
            "gap_out_sec": gap_out_sec,
            "gap_in_sec": gap_in_sec,
            "out_in_t": float(getattr(out_clip, "in_t", 0.0) or 0.0) if out_clip else None,
            "out_out_t": out_t,
            "in_in_t": in_t,
            "in_out_t": float(getattr(in_clip, "out_t", 0.0) or 0.0) if in_clip else None,
            "out_start": out_span[0],
            "out_end": out_span[1],
            "in_start": in_span[0],
            "in_end": in_span[1],
            "out_src_dur": out_dur,
            "in_src_dur": in_dur,
        })
    return seams


def decode_stats(path: str, want_indices: set[int]) -> tuple[np.ndarray, np.ndarray, dict]:
    """Stream mp4. Return min_luma, mean_luma, {n: bgr frame} for want_indices."""
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        raise RuntimeError("cannot open %s" % path)
    mins = []
    means = []
    kept = {}
    n = 0
    while True:
        ok, fr = cap.read()
        if not ok:
            break
        y = luma_bgr(fr)
        mins.append(float(y.min()))
        means.append(float(y.mean()))
        if n in want_indices:
            kept[n] = fr.copy()
        n += 1
        if n % 150 == 0:
            _log("  decoded %d frames from %s" % (n, os.path.basename(path)))
    cap.release()
    return np.asarray(mins, dtype=np.float32), np.asarray(means, dtype=np.float32), kept


def _pil_font(size: int):
    from PIL import ImageFont
    if os.path.isfile(FONT_SC):
        return ImageFont.truetype(FONT_SC, size)
    return ImageFont.load_default()


def make_triple(before: np.ndarray, after: np.ndarray, dest: str, title: str) -> None:
    from PIL import Image, ImageDraw

    scale = 360 / float(before.shape[1])
    tw = 360
    th = int(round(before.shape[0] * scale))
    b = cv2.resize(before, (tw, th), interpolation=cv2.INTER_AREA)
    a = cv2.resize(after, (tw, th), interpolation=cv2.INTER_AREA)
    diff = np.abs(a.astype(np.int16) - b.astype(np.int16)).mean(axis=2)
    peak = max(float(diff.max()), 1.0)
    heat_u8 = np.clip(diff * (255.0 / peak), 0, 255).astype(np.uint8)
    heat = cv2.applyColorMap(heat_u8, cv2.COLORMAP_INFERNO)
    gap = 8
    header = 48
    canvas_w = tw * 3 + gap * 4
    canvas_h = th + header + 16
    canvas = np.full((canvas_h, canvas_w, 3), 24, dtype=np.uint8)
    x0 = gap
    canvas[header:header + th, x0:x0 + tw] = b
    x1 = x0 + tw + gap
    canvas[header:header + th, x1:x1 + tw] = a
    x2 = x1 + tw + gap
    canvas[header:header + th, x2:x2 + tw] = heat
    rgb = cv2.cvtColor(canvas, cv2.COLOR_BGR2RGB)
    im = Image.fromarray(rgb)
    draw = ImageDraw.Draw(im)
    font = _pil_font(22)
    small = _pil_font(18)
    draw.text((gap, 8), title, fill=(240, 240, 240), font=small)
    draw.text((x0, header - 28), "fix before", fill=(255, 180, 160), font=font)
    draw.text((x1, header - 28), "fix after", fill=(160, 220, 170), font=font)
    draw.text((x2, header - 28), "diff heatmap", fill=(200, 180, 255), font=font)
    im.save(dest)


def make_curves(before_min, before_mean, after_min, after_mean,
                before_a, after_a, seams, dest) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager

    if os.path.isfile(FONT_SC):
        font_manager.fontManager.addfont(FONT_SC)
        plt.rcParams["font.family"] = font_manager.FontProperties(fname=FONT_SC).get_name()
    plt.rcParams["axes.unicode_minus"] = False

    n = min(len(before_min), len(after_min), len(before_mean), len(after_mean))
    t = np.arange(n, dtype=np.float32) / FPS
    fig, axes = plt.subplots(3, 1, figsize=(14, 10), sharex=True)
    colors_win = "#f4c27a"

    def shade(ax):
        for s in seams:
            ax.axvspan(s["t0"], s["t1"], color=colors_win, alpha=0.28, linewidth=0)

    ax = axes[0]
    shade(ax)
    ax.plot(t, before_min[:n], color="#c44e3a", lw=1.4, label="before (recorded bug)")
    ax.plot(t, after_min[:n], color="#2a9d6a", lw=1.4, label="after (Q55 freeze)")
    ax.set_ylabel("min luma")
    ax.set_ylim(-2, 260)
    ax.legend(loc="upper right")
    ax.set_title("g19b-2  stitch crossfade  —  min luma / mean luma / mean alpha")
    ax.grid(True, alpha=0.25)

    ax = axes[1]
    shade(ax)
    ax.plot(t, before_mean[:n], color="#c44e3a", lw=1.4, label="before")
    ax.plot(t, after_mean[:n], color="#2a9d6a", lw=1.4, label="after")
    ax.set_ylabel("mean luma")
    ax.legend(loc="upper right")
    ax.grid(True, alpha=0.25)

    ax = axes[2]
    shade(ax)
    n_a = min(len(before_a), len(after_a), n)
    ax.plot(t[:n_a], before_a[:n_a], color="#c44e3a", lw=1.6, label="before (extend off)")
    ax.plot(t[:n_a], after_a[:n_a], color="#2a9d6a", lw=1.6, label="after (freeze on)")
    ax.set_ylabel("mean alpha")
    ax.set_xlabel("time (s)")
    ax.set_ylim(-5, 270)
    ax.legend(loc="upper right")
    ax.grid(True, alpha=0.25)

    fig.tight_layout()
    fig.savefig(dest, dpi=120)
    plt.close(fig)


def make_compare_video(before_mp4: str, after_mp4: str, seams: list[dict], dest: str) -> None:
    import subprocess

    pad = 0.15
    ff = ffmpeg_bin()
    parts = []
    tmp = tempfile.mkdtemp(prefix="q57_cmp_")
    try:
        for s in seams:
            a = max(0.0, s["t0"] - pad)
            b = s["t1"] + pad
            outp = os.path.join(tmp, "seam%d.mp4" % s["i"])
            # left = before, right = after. Half-res so the file is watchable.
            font = os.path.join(REPO, "resources", "fonts", "Roboto", "Roboto-Bold.ttf")
            if not os.path.isfile(font):
                font = os.path.join(REPO, "resources", "fonts", "LiberationSans", "LiberationSans-Bold.ttf")
            font_esc = font.replace("\\", "/").replace(":", "\\:")
            def _dt(label):
                if not os.path.isfile(font):
                    return ""
                return (
                    ",drawtext=fontfile='%s':text='%s':x=16:y=24:"
                    "fontsize=36:fontcolor=white:borderw=2:bordercolor=black"
                    % (font_esc, label)
                )
            filt = (
                "[0:v]trim=start=%.4f:end=%.4f,setpts=PTS-STARTPTS,scale=540:960,setsar=1%s[left];"
                "[1:v]trim=start=%.4f:end=%.4f,setpts=PTS-STARTPTS,scale=540:960,setsar=1%s[right];"
                "[left][right]hstack=inputs=2[v]"
                % (a, b, _dt("BEFORE"), a, b, _dt("AFTER"))
            )
            def _hstack(filter_complex):
                return subprocess.run(
                    [
                        ff, "-y", "-hide_banner", "-loglevel", "error",
                        "-i", before_mp4, "-i", after_mp4,
                        "-filter_complex", filter_complex, "-map", "[v]",
                        "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                        "-pix_fmt", "yuv420p", outp,
                    ],
                    capture_output=True,
                )

            r = _hstack(filt)
            if r.returncode != 0 or not os.path.isfile(outp):
                filt_plain = (
                    "[0:v]trim=start=%.4f:end=%.4f,setpts=PTS-STARTPTS,scale=540:960,setsar=1[left];"
                    "[1:v]trim=start=%.4f:end=%.4f,setpts=PTS-STARTPTS,scale=540:960,setsar=1[right];"
                    "[left][right]hstack=inputs=2[v]" % (a, b, a, b)
                )
                r = _hstack(filt_plain)
            if r.returncode != 0 or not os.path.isfile(outp):
                raise RuntimeError("hstack seam %d failed: %s" % (
                    s["i"], (r.stderr or b"").decode("utf-8", "replace")[-800:]))
            parts.append(outp)
        concat_list = os.path.join(tmp, "list.txt")
        with open(concat_list, "w", encoding="utf-8", newline="\n") as fh:
            for p in parts:
                fh.write("file '%s'\n" % p.replace("\\", "/"))
        once = os.path.join(tmp, "once.mp4")
        r = subprocess.run(
            [ff, "-y", "-hide_banner", "-loglevel", "error",
             "-f", "concat", "-safe", "0", "-i", concat_list,
             "-c", "copy", once],
            capture_output=True,
        )
        if r.returncode != 0:
            raise RuntimeError("concat failed: %s" % (r.stderr or b"").decode("utf-8", "replace")[-800:])
        twice_list = os.path.join(tmp, "twice.txt")
        with open(twice_list, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("file '%s'\n" % once.replace("\\", "/"))
            fh.write("file '%s'\n" % once.replace("\\", "/"))
        r = subprocess.run(
            [ff, "-y", "-hide_banner", "-loglevel", "error",
             "-f", "concat", "-safe", "0", "-i", twice_list,
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
             "-pix_fmt", "yuv420p", "-movflags", "+faststart", dest],
            capture_output=True,
        )
        if r.returncode != 0 or not os.path.isfile(dest):
            raise RuntimeError("loop failed: %s" % (r.stderr or b"").decode("utf-8", "replace")[-800:])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def sample_layer_alpha(task, ns: list[int], extend: bool) -> dict:
    """PreviewEngine video layer at given frame indices. Returns n -> (min_luma, mean_alpha, min_alpha)."""
    from cutora.preview.engine import PreviewEngine
    from cutora.render.render_worker import RenderWorker

    orig = RenderWorker._paint_export_transition
    if not extend:
        from PySide6.QtGui import QImage, QPainter
        from cutora.render.transition_runtime import paint_standalone_transition

        def _paint_bug(self, dest, track, trans, t, w, h, cap_for, state_for):
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
                        p2, cap2, clip_obj, int(w), int(h), t, st, extend_handles=False)
                finally:
                    p2.end()
                return offscreen

            paint_standalone_transition(dest, track, trans, t, w, h, render_clip)

        RenderWorker._paint_export_transition = _paint_bug
    out = {}
    eng = PreviewEngine()
    try:
        for i, n in enumerate(ns):
            t = n / FPS
            img = eng.video_frame_at(task, t)
            arr = qimage_rgba(img)
            y = luma_rgb(arr[..., :3])
            a = arr[..., 3]
            out[n] = {
                "min_luma": float(y.min()),
                "mean_luma": float(y.mean()),
                "min_alpha": float(a.min()),
                "mean_alpha": float(a.mean()),
            }
            if (i + 1) % 10 == 0:
                _log("  layer extend=%s %d/%d" % (extend, i + 1, len(ns)))
    finally:
        eng.close()
        RenderWorker._paint_export_transition = orig
    return out


def alpha_series(n_frames: int, layer: dict) -> np.ndarray:
    """Mean alpha per frame. Outside sampled indices: 255 (no transition / not sampled)."""
    arr = np.full(n_frames, 255.0, dtype=np.float32)
    for n, row in layer.items():
        if 0 <= n < n_frames:
            arr[n] = row["mean_alpha"]
    return arr


def write_numbers(seams, before_min, after_min, layer_before, layer_after, dest_md, dest_json) -> dict:
    rows = []
    gate_fail = []
    for s in seams:
        ns = s["ns"]
        bmin = min(float(before_min[n]) for n in ns if n < len(before_min))
        amin = min(float(after_min[n]) for n in ns if n < len(after_min))
        b_a = min(layer_before[n]["min_alpha"] for n in ns if n in layer_before)
        a_a = min(layer_after[n]["min_alpha"] for n in ns if n in layer_after)
        b_aluma = min(layer_before[n]["min_luma"] for n in ns if n in layer_before)
        a_aluma = min(layer_after[n]["min_luma"] for n in ns if n in layer_after)
        handle_out_f = s["handle_out_sec"] * FPS
        handle_in_f = s["handle_in_sec"] * FPS
        gap_out_f = s["gap_out_sec"] * FPS
        gap_in_f = s["gap_in_sec"] * FPS
        trans_f = len(ns)
        row = {
            "seam": s["i"],
            "t0": round(s["t0"], 4),
            "t1": round(s["t1"], 4),
            "preset": s["preset"],
            "handle_out_frames": round(handle_out_f, 3),
            "handle_in_frames": round(handle_in_f, 3),
            "trans_frames": trans_f,
            "gap_out_frames": round(gap_out_f, 3),
            "gap_in_frames": round(gap_in_f, 3),
            "gap_frames": round(max(gap_out_f, gap_in_f), 3),
            "before_min_luma_encoded": round(bmin, 4),
            "after_min_luma_encoded": round(amin, 4),
            "before_min_luma_layer": round(b_aluma, 4),
            "after_min_luma_layer": round(a_aluma, 4),
            "before_min_alpha": round(b_a, 4),
            "after_min_alpha": round(a_a, 4),
            "in_in_t": s["in_in_t"],
            "out_out_t": s["out_out_t"],
            "in_src_dur": s["in_src_dur"],
            "out_src_dur": s["out_src_dur"],
        }
        rows.append(row)
        # Gate: freeze must fill the video layer. Encoded min luma of real
        # footage can be ~0 (dark clothing); the hole is alpha < 255 and a
        # collapsed layer. Stop if after still has a hole.
        if a_a < 255.0 - 0.5:
            gate_fail.append("seam %d after min alpha=%s" % (s["i"], a_a))
        if a_aluma < 1.0 and a_a < 255.0 - 0.5:
            gate_fail.append("seam %d after layer min luma=%s with transparent hole" % (s["i"], a_aluma))
    blob = {
        "seams": rows,
        "note": (
            "Project has 5 clips and 4 cross_dissolve seams (not 5). "
            "Handle = leftover source frames on the outgoing tail / incoming head. "
            "Gap = freeze deficit (need minus leftover). "
            "Encoded min luma can be ~0 on real footage; the defect lock is layer alpha."
        ),
        "gate_fail": gate_fail,
        "gate_ok": not gate_fail,
    }
    with open(dest_json, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(blob, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    lines = [
        "# Q57 g19b-2 numbers",
        "",
        "Five clips, **four** `cross_dissolve` seams (the project has no fifth join).",
        "",
        "| seam | t | handle out/in (f) | trans (f) | gap out/in (f) | before min Y / α | after min Y / α |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(
            "| %d | %.3f–%.3f | %.2f / %.2f | %d | %.2f / %.2f | **%.1f / %.1f** | **%.1f / %.1f** |"
            % (
                r["seam"], r["t0"], r["t1"],
                r["handle_out_frames"], r["handle_in_frames"],
                r["trans_frames"],
                r["gap_out_frames"], r["gap_in_frames"],
                r["before_min_luma_layer"], r["before_min_alpha"],
                r["after_min_luma_layer"], r["after_min_alpha"],
            )
        )
    lines += [
        "",
        "Y / α are **video-layer** (PreviewEngine, before compositing onto the black export canvas).",
        "Before = same path with `extend_handles=False` (the Q55 hole). After = freeze on.",
        "",
        "gate_ok = **%s**" % blob["gate_ok"],
    ]
    if gate_fail:
        lines.append("")
        lines.append("gate failures:")
        lines.extend("- " + g for g in gate_fail)
    with open(dest_md, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines) + "\n")
    return blob


def main() -> int:
    os.makedirs(TRIPLES, exist_ok=True)
    import golden_check as gc

    _log("=== Q57 fingerprint ===")
    fp = gc.collect_fingerprint()
    fp_text = gc.format_fingerprint(fp)
    with open(os.path.join(OUT, "fingerprint.txt"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(fp_text)
    fp_ok, fp_rows = compare_fingerprint(fp)
    with open(os.path.join(OUT, "fingerprint_compare.txt"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(fp_rows) + "\n")
        fh.write("MATCH=%s\n" % fp_ok)
    for ln in fp_rows:
        _log("  " + ln)
    if not fp_ok:
        _log("FINGERPRINT MISMATCH — will still gather evidence, must NOT re-record")

    _log("=== boot host + render after ===")
    host, _app = gc.boot_host()
    try:
        from PySide6 import __version__ as PYSIDE_VERSION_STR
        fp["pyside6"] = PYSIDE_VERSION_STR
    except Exception:
        pass
    fp_ok, fp_rows = compare_fingerprint(fp)
    with open(os.path.join(OUT, "fingerprint_compare.txt"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(fp_rows) + "\n")
        fh.write("MATCH=%s\n" % fp_ok)
    if not fp_ok:
        _log("FINGERPRINT MISMATCH after Qt import")

    manifest = gc.load_manifest(CASE_DIR)
    project = os.path.join(CASE_DIR, manifest.get("project", "project.asproj.json"))
    _data, tasks = gc.load_tasks(host, project)
    gc.reset_case_caches()
    tmp = tempfile.mkdtemp(prefix="q57_after_")
    try:
        outs = gc.render_serial(host, tasks, tmp, manifest.get("mode") or "quality", False)
        if not outs or not os.path.isfile(outs[0]):
            raise RuntimeError("after render produced no file")
        shutil.copy2(outs[0], AFTER_MP4)
        after_hash = gc.sha256_file(AFTER_MP4)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    _log("AFTER sha256=%s" % after_hash)
    with open(os.path.join(OUT, "after.sha256"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write("%s  after.mp4\n" % after_hash)

    task = tasks[0]
    seams = load_seams(task)
    _log("seams: %d" % len(seams))
    for s in seams:
        _log("  seam %d  %.3f-%.3f  frames %d-%d  sample %s  handle_in=%.2f gap_in=%.2f" % (
            s["i"], s["t0"], s["t1"], s["n0"], s["n1"], s["sample"],
            s["handle_in_sec"] * FPS, s["gap_in_sec"] * FPS))

    want = set()
    layer_ns = []
    for s in seams:
        want.update(s["sample"])
        layer_ns.extend(s["ns"])
    layer_ns = sorted(set(layer_ns))

    _log("=== decode before (expected.mp4) ===")
    bmin, bmean, bkeep = decode_stats(BEFORE_MP4, want)
    _log("=== decode after ===")
    amin, amean, akeep = decode_stats(AFTER_MP4, want)
    _log("frames before=%d after=%d" % (len(bmin), len(amin)))

    _log("=== triples ===")
    for s in seams:
        for n in s["sample"]:
            if n not in bkeep or n not in akeep:
                _log("  missing frame %d" % n)
                continue
            dest = os.path.join(
                TRIPLES, "seam%d_f%04d_t%.3f.png" % (s["i"], n, n / FPS))
            make_triple(
                bkeep[n], akeep[n], dest,
                "seam %d  frame %d  t=%.3fs  (%.3f-%.3fs)" % (s["i"], n, n / FPS, s["t0"], s["t1"]),
            )
            _log("  wrote %s" % os.path.basename(dest))

    _log("=== in-memory layer alpha (transition windows) ===")
    layer_after = sample_layer_alpha(task, layer_ns, extend=True)
    layer_before = sample_layer_alpha(task, layer_ns, extend=False)
    n_frames = max(len(bmin), len(amin))
    before_a = alpha_series(n_frames, layer_before)
    after_a = alpha_series(n_frames, layer_after)

    _log("=== curves ===")
    make_curves(
        bmin, bmean, amin, amean, before_a, after_a, seams,
        os.path.join(OUT, "luma_alpha_curves.png"),
    )

    _log("=== numbers ===")
    blob = write_numbers(
        seams, bmin, amin, layer_before, layer_after,
        os.path.join(OUT, "numbers.md"),
        os.path.join(OUT, "numbers.json"),
    )

    _log("=== comparison video ===")
    make_compare_video(BEFORE_MP4, AFTER_MP4, seams, os.path.join(OUT, "Q57_before_after.mp4"))

    gate_path = os.path.join(OUT, "GATE.txt")
    if not fp_ok:
        gate = "STOP_FINGERPRINT"
    elif not blob["gate_ok"]:
        gate = "STOP_STILL_BLACK"
    else:
        gate = "PASS"
    with open(gate_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(gate + "\n")
        fh.write("after_sha256=%s\n" % after_hash)
        fh.write("fp_ok=%s\n" % fp_ok)
        fh.write("gate_ok=%s\n" % blob["gate_ok"])
        for g in blob.get("gate_fail") or []:
            fh.write("fail: %s\n" % g)
    _log("GATE=%s after=%s" % (gate, after_hash[:12]))
    return 0 if gate == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
