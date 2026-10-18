# -*- coding: utf-8 -*-
"""Q14: alpha MOV/GIF/proxy gates. Imports stay inside tests (no Qt at collect)."""
from __future__ import annotations

import json
import os
import time

import numpy as np

from q14_media import (
    FFMPEG,
    expected_disposal_frames,
    make_circle_png,
    make_disposal_gif,
    make_png_sequence,
    make_prores_4444,
    make_raw_argb_mov,
    make_vp9_alpha,
    probe_pix_fmt,
)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROBE_JSON = os.path.join(REPO, "docs", "plan", "reports", "Q14_alpha_probe.json")


def _tier_name(cap):
    from cutora.render.readers import _FFmpegFrameReader, _RawARGBMOVReader
    if isinstance(cap, _RawARGBMOVReader):
        return "raw_argb"
    if isinstance(cap, _FFmpegFrameReader):
        return "ffmpeg_pipe"
    return "cv2"


def _read_one(path):
    from cutora.render.readers import _open_video_capture
    cap = _open_video_capture(path, hw=False)
    assert cap is not None and cap.isOpened(), "open failed: %s" % path
    try:
        ret, frame = cap.read()
        assert ret and frame is not None, "read failed: %s" % path
        return _tier_name(cap), frame
    finally:
        try:
            cap.release()
        except Exception:
            pass


def _alpha_var(frame):
    assert frame.ndim == 3 and frame.shape[2] == 4, "no alpha plane: shape=%s" % (frame.shape,)
    return float(np.var(frame[..., 3].astype(np.float64)))


def test_q14_alpha_matrix(tmp_path):
    png = make_circle_png(str(tmp_path / "circle.png"), w=48, h=48, radius=14, alpha=160)
    cases = {}

    p4444 = make_prores_4444(png, str(tmp_path / "p4444.mov"), seconds=0.2, profile=4)
    info = probe_pix_fmt(p4444)
    tier, frame = _read_one(p4444)
    cases["prores_4444"] = {
        "path": p4444, "tier": tier, "pix_fmt": info.get("pix_fmt"),
        "codec": info.get("codec_name"), "shape": list(frame.shape),
        "alpha_var": _alpha_var(frame), "ffmpeg_pix_fmt_out": None,
    }

    pxq = make_prores_4444(png, str(tmp_path / "p4444xq.mov"), seconds=0.2, profile=5)
    info = probe_pix_fmt(pxq)
    tier, frame = _read_one(pxq)
    cases["prores_4444xq"] = {
        "path": pxq, "tier": tier, "pix_fmt": info.get("pix_fmt"),
        "codec": info.get("codec_name"), "shape": list(frame.shape),
        "alpha_var": _alpha_var(frame),
    }

    webm = make_vp9_alpha(png, str(tmp_path / "vp9.webm"), seconds=0.2)
    info = probe_pix_fmt(webm)
    tier, frame = _read_one(webm)
    cases["vp9_alpha"] = {
        "path": webm, "tier": tier, "pix_fmt": info.get("pix_fmt"),
        "codec": info.get("codec_name"), "shape": list(frame.shape),
        "alpha_var": _alpha_var(frame),
    }

    pattern, _paths = make_png_sequence(str(tmp_path / "seq"), n=3, w=32, h=32)
    from cutora.render.readers import _open_video_capture
    cap = _open_video_capture(pattern, hw=False)
    assert cap is not None and cap.isOpened(), "png sequence did not open: %s" % pattern
    try:
        ret, frame = cap.read()
        assert ret and frame is not None
        cases["png_sequence"] = {
            "path": pattern, "tier": _tier_name(cap),
            "pix_fmt": "rgba", "shape": list(frame.shape),
            "alpha_var": _alpha_var(frame),
        }
    finally:
        cap.release()

    raw = make_raw_argb_mov(png, str(tmp_path / "argb.mov"), seconds=0.2)
    info = probe_pix_fmt(raw)
    tier, frame = _read_one(raw)
    cases["uncompressed_argb"] = {
        "path": raw, "tier": tier, "pix_fmt": info.get("pix_fmt"),
        "codec": info.get("codec_name"), "shape": list(frame.shape),
        "alpha_var": _alpha_var(frame),
    }

    os.makedirs(os.path.dirname(PROBE_JSON), exist_ok=True)
    with open(PROBE_JSON, "w", encoding="utf-8") as fh:
        json.dump(cases, fh, ensure_ascii=False, indent=2)

    for name, rec in cases.items():
        assert rec["alpha_var"] > 0, "%s alpha variance is 0 (alpha dropped). %s" % (name, rec)
        assert rec["tier"] != "cv2", "%s fell through to cv2 (alpha would be dropped): %s" % (name, rec)
        if name.startswith("prores"):
            assert rec["tier"] == "ffmpeg_pipe", rec
            pix = str(rec.get("pix_fmt") or "")
            assert "yuva" in pix, rec


def test_q14_straight_alpha_no_dark_fringe(tmp_path):
    png = make_circle_png(str(tmp_path / "disc.png"), w=64, h=64, radius=18, alpha=128)
    mov = make_prores_4444(png, str(tmp_path / "disc.mov"), seconds=0.15, profile=4)
    from cutora.render.readers import _cv2_frame_to_qimage, _open_video_capture
    from PySide6.QtGui import QColor, QImage, QPainter

    cap = _open_video_capture(mov, hw=False)
    assert cap is not None
    try:
        ok, frame = cap.read()
    finally:
        cap.release()
    assert ok and frame is not None and frame.shape[2] == 4
    qimg = _cv2_frame_to_qimage(frame)
    assert qimg is not None
    h, w = frame.shape[:2]
    bg = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    bg.fill(QColor(255, 0, 0))
    p = QPainter(bg)
    p.drawImage(0, 0, qimg)
    p.end()
    ptr = bg.constBits()
    arr = np.frombuffer(ptr, np.uint8).reshape((h, bg.bytesPerLine()))[:, : w * 4]
    bgra = arr.reshape((h, w, 4)).copy()
    # little-endian ARGB32 = B,G,R,A
    cy, cx = h // 2, w // 2
    center = bgra[cy, cx, :3].astype(np.int16)
    # ring: source alpha between 40 and 200
    src_a = frame[..., 3]
    ring = (src_a > 40) & (src_a < 200)
    assert int(ring.sum()) > 8, "no antialiased rim to sample"
    ring_bgr = bgra[ring][:, :3].astype(np.int16)
    # Both white and red have R=255; a dark fringe pulls R down toward 0.
    ring_r = ring_bgr[:, 2]
    assert int(ring_r.min()) >= 200, (
        "dark fringe: rim R min=%d center=%s (straight alpha treated as premul?)"
        % (int(ring_r.min()), center.tolist())
    )
    center_luma = float(0.114 * center[0] + 0.587 * center[1] + 0.299 * center[2])
    ring_luma = 0.114 * ring_bgr[:, 0] + 0.587 * ring_bgr[:, 1] + 0.299 * ring_bgr[:, 2]
    # Rim must not be a black halo (darker than both the disc and the red field).
    red_luma = 0.299 * 255.0
    assert float(ring_luma.min()) >= min(center_luma, red_luma) - 8.0, (
        "rim luma %s < center %s and red %s" % (
            float(ring_luma.min()), center_luma, red_luma)
    )


def test_q14_gif_disposal(tmp_path):
    gif = make_disposal_gif(str(tmp_path / "disp.gif"), size=16)
    from cutora.core.config import FFMPEG_PATH
    from cutora.media.animated import import_animated_as_clip
    clip = import_animated_as_clip(gif, ffmpeg=FFMPEG_PATH)
    assert clip["alpha"] is True
    dest = str(tmp_path / "frames")
    os.makedirs(dest, exist_ok=True)
    pattern = os.path.join(dest, "f_%02d.png")
    import subprocess
    r = subprocess.run(
        [FFMPEG_PATH, "-y", "-hide_banner", "-loglevel", "error",
         "-i", clip["path"], "-vsync", "0", "-pix_fmt", "rgba", pattern],
        capture_output=True, timeout=30,
    )
    assert r.returncode == 0, r.stderr
    import cv2
    f2_path = os.path.join(dest, "f_02.png")
    f3_path = os.path.join(dest, "f_03.png")
    assert os.path.isfile(f2_path) and os.path.isfile(f3_path), os.listdir(dest)
    got2 = cv2.imread(f2_path, cv2.IMREAD_UNCHANGED)
    got3 = cv2.imread(f3_path, cv2.IMREAD_UNCHANGED)
    assert got2 is not None and got3 is not None
    # cv2 is BGRA; reference is RGBA
    exp2, exp3 = expected_disposal_frames(16)
    g2 = np.dstack([got2[..., 2], got2[..., 1], got2[..., 0], got2[..., 3]])
    g3 = np.dstack([got3[..., 2], got3[..., 1], got3[..., 0], got3[..., 3]])
    # Palette quantization: allow a small channel slop but no leftover pixels
    # from a missed disposal (ghost / black matte).
    def _close(got, exp, name):
        assert got.shape == exp.shape, (name, got.shape, exp.shape)
        diff = np.abs(got.astype(np.int16) - exp.astype(np.int16))
        # alpha must agree: opaque vs clear
        a_got = got[..., 3] > 40
        a_exp = exp[..., 3] > 40
        mismatch = int(np.sum(a_got != a_exp))
        assert mismatch == 0, "%s alpha ghost/hole pixels=%d" % (name, mismatch)
        # where both opaque, color within palette slop
        both = a_got & a_exp
        if both.any():
            mad = float(diff[both][..., :3].mean())
            assert mad < 25.0, "%s opaque MAD=%.2f" % (name, mad)

    _close(g2, exp2, "frame2")
    _close(g3, exp3, "frame3")


def test_q14_uses_shared_cap_pool(tmp_path):
    src = open(os.path.join(REPO, "cutora", "render", "render_worker.py"),
               encoding="utf-8").read()
    assert src.count("_open_video_capture(") == 2
    assert "hw=export_hw_decode()" in src
    body = src[src.index("class RenderWorker"):]
    assert "_open_video_capture(src_path)" not in body
    assert "prores_ks" not in src
    png = make_circle_png(str(tmp_path / "c.png"), w=32, h=32, radius=10, alpha=140)
    mov = make_prores_4444(png, str(tmp_path / "a.mov"), seconds=0.15, profile=4)
    from cutora.render.render_worker import EXPORT_MAX_OPEN_CAPS, _ExportCapPool
    states = {}
    pool = _ExportCapPool({"alpha": mov}, {"alpha": 1.0}, states)
    cap = pool.get("alpha")
    assert cap is not None and cap.isOpened()
    assert pool.opened_total == 1
    assert len(pool) <= EXPORT_MAX_OPEN_CAPS
    ok, frame = cap.read()
    assert ok and frame is not None and frame.shape[2] == 4
    assert _alpha_var(frame) > 0
    assert _tier_name(cap) == "ffmpeg_pipe"
    pool.clear()


def test_q14_large_alpha_export_budget(tmp_path):
    """1080x1920 ProRes 4444 decode cost; proxy when over +8 ms/frame."""
    png = make_circle_png(
        str(tmp_path / "big.png"), w=1080, h=1920, radius=400, alpha=140)
    mov = make_prores_4444(
        png, str(tmp_path / "big4444.mov"), seconds=0.4, profile=4, fps=30)
    h264 = str(tmp_path / "big.h264.mp4")
    from q14_media import ffmpeg_run
    ffmpeg_run([
        "-loop", "1", "-framerate", "30", "-i", png,
        "-t", "0.4", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18",
        h264,
    ], timeout=120)

    def _ms_per_frame(path, n=8):
        from cutora.render.readers import _open_video_capture
        cap = _open_video_capture(path, hw=False)
        assert cap is not None
        try:
            # warm
            cap.read()
            t0 = time.perf_counter()
            got = 0
            for _ in range(n):
                ok, fr = cap.read()
                if not ok:
                    break
                got += 1
            dt = time.perf_counter() - t0
            return (dt / max(1, got)) * 1000.0, got
        finally:
            cap.release()

    alpha_ms, n_a = _ms_per_frame(mov)
    base_ms, n_b = _ms_per_frame(h264)
    delta = alpha_ms - base_ms
    report = {
        "alpha_ms_per_frame": alpha_ms,
        "h264_ms_per_frame": base_ms,
        "delta_ms": delta,
        "frames_alpha": n_a,
        "frames_h264": n_b,
        "threshold_ms": 8.0,
    }
    os.makedirs(os.path.dirname(PROBE_JSON), exist_ok=True)
    budget_path = os.path.join(REPO, "docs", "plan", "reports", "Q14_alpha_budget.json")
    with open(budget_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)

    from cutora.media.alpha_proxy import (
        ENCODING_FLOOR_MAD, ensure_alpha_proxy, measure_decode_ms,
        preview_path_for,
    )
    # If the machine is under the +8ms budget, still lock the trigger by
    # forcing a 0 ms threshold; the product default stays 8.
    force = 0.0 if delta <= 8.0 else 8.0
    proxy = ensure_alpha_proxy(
        mov, host=None, threshold_ms=force, sync=True)
    assert proxy and os.path.isfile(proxy), "proxy path was not produced"
    assert preview_path_for(mov) == proxy
    # export still uses the original (caller keeps src path)
    from cutora.render.readers import _open_video_capture
    cap_o = _open_video_capture(mov, hw=False)
    cap_p = _open_video_capture(proxy, hw=False)
    try:
        ok_o, fo = cap_o.read()
        ok_p, fp = cap_p.read()
    finally:
        cap_o.release()
        cap_p.release()
    assert ok_o and ok_p
    # compare RGB of opaque-ish pixels
    if fo.shape != fp.shape:
        import cv2
        fp = cv2.resize(fp, (fo.shape[1], fo.shape[0]), interpolation=cv2.INTER_LINEAR)
    a = fo[..., 3] if fo.shape[2] == 4 else np.full(fo.shape[:2], 255, np.uint8)
    mask = a > 40
    mad = float(np.abs(fo[..., :3].astype(np.int16) - fp[..., :3].astype(np.int16))[mask].mean()) if mask.any() else 0.0
    report["proxy_vs_original_mad"] = mad
    report["encoding_floor_mad"] = ENCODING_FLOOR_MAD
    with open(budget_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)
    assert mad <= ENCODING_FLOOR_MAD, "proxy MAD %.3f > floor %s" % (mad, ENCODING_FLOOR_MAD)
    # measure_decode_ms is the same helper the importer uses
    ms = measure_decode_ms(mov, n=4)
    assert ms > 0
