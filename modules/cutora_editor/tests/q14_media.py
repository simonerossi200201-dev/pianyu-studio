# -*- coding: utf-8 -*-
"""Shared Q14 fixture builders (PNG / GIF / alpha MOV). No Qt, no product imports."""
from __future__ import annotations

import os
import struct
import subprocess
import zlib

import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FFMPEG = os.path.join(REPO, "runtime", "ffmpeg", "ffmpeg.exe")
FFPROBE = os.path.join(REPO, "runtime", "ffmpeg", "ffprobe.exe")
if not os.path.isfile(FFMPEG):
    FFMPEG = "ffmpeg"
if not os.path.isfile(FFPROBE):
    FFPROBE = "ffprobe"


def write_rgba_png(path, rgba):
    h, w = rgba.shape[:2]
    raw = b"".join(b"\x00" + rgba[y].tobytes() for y in range(h))

    def chunk(tag, data):
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)
    with open(path, "wb") as fh:
        fh.write(b"\x89PNG\r\n\x1a\n")
        fh.write(chunk(b"IHDR", ihdr))
        fh.write(chunk(b"IDAT", zlib.compress(raw, 9)))
        fh.write(chunk(b"IEND", b""))


def white_circle_rgba(w=64, h=64, radius=20.0, alpha=128):
    """Straight-alpha white disc on transparent, with a 1.5px antialiased rim."""
    yy, xx = np.ogrid[:h, :w]
    cy, cx = (h - 1) / 2.0, (w - 1) / 2.0
    dist = np.sqrt((yy - cy) ** 2 + (xx - cx) ** 2)
    t = np.clip(radius + 1.5 - dist, 0.0, 1.0)
    rgba = np.zeros((h, w, 4), dtype=np.uint8)
    rgba[..., 0] = 255
    rgba[..., 1] = 255
    rgba[..., 2] = 255
    rgba[..., 3] = np.round(t * float(alpha)).astype(np.uint8)
    return rgba


def ffmpeg_run(args, timeout=60):
    r = subprocess.run(
        [FFMPEG, "-y", "-hide_banner", "-loglevel", "error"] + args,
        capture_output=True, timeout=timeout,
    )
    if r.returncode != 0:
        raise RuntimeError(
            "ffmpeg failed %s\n%s" % (args, (r.stderr or b"")[-800:])
        )
    return r


def make_circle_png(path, w=64, h=64, radius=20.0, alpha=128):
    write_rgba_png(path, white_circle_rgba(w, h, radius, alpha))
    return path


def make_static_gif(dest, w=16, h=16):
    """Single-frame GIF (static still, not an animation)."""
    folder = os.path.dirname(dest) or "."
    os.makedirs(folder, exist_ok=True)
    png = os.path.join(folder, "_static.gif.png")
    rgba = np.zeros((h, w, 4), dtype=np.uint8)
    rgba[:, :, 0] = 200
    rgba[:, :, 3] = 255
    write_rgba_png(png, rgba)
    pal = os.path.join(folder, "_static.gif.pal.png")
    ffmpeg_run(["-i", png, "-vf", "palettegen=reserve_transparent=1", pal])
    ffmpeg_run([
        "-i", png, "-i", pal,
        "-lavfi", "[0:v][1:v]paletteuse=alpha_threshold=128",
        "-frames:v", "1",
        dest,
    ])
    return dest


def make_prores_4444(png, dest, seconds=0.4, profile=4, fps=30, timeout=120):
    """profile 4 = 4444, 5 = 4444 XQ. Straight alpha, yuva444p10le."""
    ffmpeg_run([
        "-loop", "1", "-framerate", str(fps), "-i", png,
        "-t", str(seconds),
        "-c:v", "prores_ks", "-profile:v", str(profile),
        "-pix_fmt", "yuva444p10le",
        dest,
    ], timeout=timeout)
    return dest


def make_large_prores_4444(dest, seconds=30, fps=30, w=1920, h=1080, timeout=600):
    """Detailed 1080p ProRes 4444 with alpha (~1–2 GB at 30s). Not a still loop."""
    ffmpeg_run([
        "-f", "lavfi",
        "-i", "testsrc2=size=%dx%d:rate=%d:duration=%s,noise=alls=1:allf=t"
        % (w, h, fps, seconds),
        "-c:v", "prores_ks", "-profile:v", "4",
        "-pix_fmt", "yuva444p10le",
        dest,
    ], timeout=timeout)
    return dest


def make_vp9_alpha(png, dest, seconds=0.4, fps=30):
    ffmpeg_run([
        "-loop", "1", "-framerate", str(fps), "-i", png,
        "-t", str(seconds),
        "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p",
        "-auto-alt-ref", "0", "-b:v", "2M",
        dest,
    ], timeout=120)
    return dest


def make_raw_argb_mov(png, dest, seconds=0.2, fps=30):
    ffmpeg_run([
        "-loop", "1", "-framerate", str(fps), "-i", png,
        "-t", str(seconds),
        "-c:v", "rawvideo", "-pix_fmt", "argb",
        "-f", "mov",
        dest,
    ], timeout=60)
    return dest


def make_png_sequence(folder, n=3, w=32, h=32):
    os.makedirs(folder, exist_ok=True)
    paths = []
    for i in range(n):
        rgba = white_circle_rgba(w, h, radius=8.0 + i, alpha=180)
        p = os.path.join(folder, "seq_%03d.png" % (i + 1))
        write_rgba_png(p, rgba)
        paths.append(p)
    pattern = os.path.join(folder, "seq_%03d.png")
    return pattern, paths


def _patch_gif_disposals(gif_bytes, disposals):
    out = bytearray(gif_bytes)
    i = 0
    di = 0
    while i < len(out) - 7 and di < len(disposals):
        if out[i] == 0x21 and out[i + 1] == 0xF9 and out[i + 2] == 0x04:
            packed = out[i + 3]
            packed = (packed & ~0x1C) | ((int(disposals[di]) & 7) << 2)
            out[i + 3] = packed
            di += 1
            i += 8
            continue
        i += 1
    if di < len(disposals):
        raise RuntimeError("GIF had %d GCE blocks, needed %d" % (di, len(disposals)))
    return bytes(out)


def _gif_lzw_uncompressed(indices):
    """GIF LZW with frequent CLEAR so the table never grows (full-canvas frames)."""
    min_code_size = 8
    clear = 1 << min_code_size
    eoi = clear + 1
    code_size = min_code_size + 1
    bits = 0
    buf = 0
    out = bytearray()

    def emit(code):
        nonlocal bits, buf
        buf |= int(code) << bits
        bits += code_size
        while bits >= 8:
            out.append(buf & 0xFF)
            buf >>= 8
            bits -= 8

    emit(clear)
    n = 0
    for idx in indices:
        emit(int(idx) & 0xFF)
        n += 1
        if n >= 250:
            emit(clear)
            n = 0
    emit(eoi)
    if bits:
        out.append(buf & 0xFF)
    return bytes([min_code_size]) + _gif_subblocks(bytes(out))


def _gif_subblocks(data):
    out = bytearray()
    i = 0
    while i < len(data):
        n = min(255, len(data) - i)
        out.append(n)
        out.extend(data[i:i + n])
        i += n
    out.append(0)
    return bytes(out)


def make_disposal_gif(dest, size=16):
    """3-frame GIF using disposal 1 / 2 / 3.

    Frame 1 (do-not-dispose): red left half, rest transparent.
    Frame 2 (restore-background): green right half (full-canvas descriptor
    so disposal 2 clears the whole screen, including leftover red).
    Frame 3 (restore-previous): blue bottom half.
    """
    folder = os.path.dirname(dest) or "."
    os.makedirs(folder, exist_ok=True)
    h = w = int(size)
    # index 0 = transparent, 1 = red, 2 = green, 3 = blue
    f0 = np.zeros((h, w), dtype=np.uint8)
    f0[:, : w // 2] = 1
    f1 = np.zeros((h, w), dtype=np.uint8)
    f1[:, w // 2 :] = 2
    f2 = np.zeros((h, w), dtype=np.uint8)
    f2[h // 2 :, :] = 3
    gct = bytes([
        0, 0, 0,
        220, 0, 0,
        0, 200, 0,
        0, 0, 210,
    ]) + bytes(3 * (256 - 4))
    out = bytearray()
    out.extend(b"GIF89a")
    out.extend(struct.pack("<HH", w, h))
    out.append(0xF7)  # GCT, 8-bit
    out.append(0)     # bg index
    out.append(0)     # aspect
    out.extend(gct)
    delay = 10  # 0.1s in 1/100s
    for disposal, indices in ((1, f0), (2, f1), (3, f2)):
        packed = ((int(disposal) & 7) << 2) | 0x01  # transparent index follows
        out.extend(bytes([0x21, 0xF9, 0x04, packed, delay & 0xFF, (delay >> 8) & 0xFF, 0, 0x00]))
        out.extend(bytes([0x2C]))
        out.extend(struct.pack("<HHHH", 0, 0, w, h))
        out.append(0)  # no local table
        out.extend(_gif_lzw_uncompressed(indices.reshape(-1)))
    out.append(0x3B)
    with open(dest, "wb") as fh:
        fh.write(out)
    return dest


def expected_disposal_frames(size=16):
    """Reference RGBA for displayed frames 2 and 3 (1-based)."""
    h = w = int(size)
    f2 = np.zeros((h, w, 4), dtype=np.uint8)
    f2[:, : w // 2, 0] = 220
    f2[:, : w // 2, 3] = 255
    f2[:, w // 2 :, 1] = 200
    f2[:, w // 2 :, 3] = 255
    f3 = np.zeros((h, w, 4), dtype=np.uint8)
    f3[h // 2 :, :, 2] = 210
    f3[h // 2 :, :, 3] = 255
    return f2, f3


def probe_pix_fmt(path):
    r = subprocess.run(
        [FFPROBE, "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height,pix_fmt,codec_name",
         "-of", "default=noprint_wrappers=1", path],
        capture_output=True, text=True, timeout=20,
    )
    info = {}
    for line in (r.stdout or "").splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            info[k.strip()] = v.strip()
    return info
