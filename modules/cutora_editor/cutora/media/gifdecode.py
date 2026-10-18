# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""GIF89a decoder that honours disposal 1/2/3, GCE delay, and interlacing."""
from __future__ import annotations

import struct

import numpy as np

from cutora.core.log import get_logger

_LOG = get_logger("cutora.media.gifdecode")

# GIF89a §15: interlaced images are stored in four passes.
_INTERLACE_PASSES = ((0, 8), (4, 8), (2, 4), (1, 2))
_DEFAULT_DELAY_CS = 10  # browsers treat delay 0 as 10cs (100 ms)


def _lzw_decode(min_code_size: int, data: bytes, pixel_count: int) -> bytes:
    clear = 1 << min_code_size
    eoi = clear + 1
    code_size = min_code_size + 1
    next_code = eoi + 1
    table = {i: bytes((i,)) for i in range(clear)}
    bits = 0
    buf = 0
    pos = 0
    n = len(data)

    def read_code():
        nonlocal bits, buf, pos, code_size
        while bits < code_size:
            if pos >= n:
                return None
            buf |= data[pos] << bits
            pos += 1
            bits += 8
        code = buf & ((1 << code_size) - 1)
        buf >>= code_size
        bits -= code_size
        return code

    out = bytearray()
    prev = None
    while len(out) < pixel_count:
        code = read_code()
        if code is None or code == eoi:
            break
        if code == clear:
            table = {i: bytes((i,)) for i in range(clear)}
            code_size = min_code_size + 1
            next_code = eoi + 1
            prev = None
            continue
        if prev is None:
            entry = table.get(code)
            if entry is None:
                break
            out.extend(entry)
            prev = code
            continue
        if code in table:
            entry = table[code]
        elif code == next_code:
            entry = table[prev] + table[prev][:1]
        else:
            _LOG.warning("GIF LZW: unexpected code %s", code)
            break
        out.extend(entry)
        if next_code < 4096:
            table[next_code] = table[prev] + entry[:1]
            next_code += 1
            if next_code == (1 << code_size) and code_size < 12:
                code_size += 1
        prev = code
    return bytes(out[:pixel_count])


def deinterlace_rows(idx: np.ndarray) -> np.ndarray:
    """Re-order an interlaced (fh, fw) index buffer into sequential rows."""
    if idx.ndim != 2:
        return idx
    fh, _fw = idx.shape
    if fh <= 1:
        return idx
    out = np.empty_like(idx)
    src = 0
    for start, step in _INTERLACE_PASSES:
        y = start
        while y < fh:
            if src >= fh:
                break
            out[y] = idx[src]
            src += 1
            y += step
    return out


def _skip_subblocks(data: bytes, pos: int) -> int:
    while pos < len(data) and data[pos] != 0:
        pos += 1 + data[pos]
    if pos < len(data) and data[pos] == 0:
        pos += 1
    return pos


def count_gif_frames(path: str) -> int:
    """Count image descriptors without LZW-decoding. 0 if not a GIF."""
    with open(path, "rb") as fh:
        data = fh.read()
    if data[:6] not in (b"GIF87a", b"GIF89a"):
        return 0
    _w, _h, packed, _bg, _aspect = struct.unpack_from("<HHBBB", data, 6)
    pos = 13
    if packed & 0x80:
        pos += 3 * (1 << ((packed & 7) + 1))
    n = 0
    while pos < len(data):
        b = data[pos]
        if b == 0x3B:
            break
        if b == 0x21:
            pos += 2
            pos = _skip_subblocks(data, pos)
            continue
        if b != 0x2C:
            pos += 1
            continue
        if pos + 10 >= len(data):
            break
        _left, _top, _fw, _fh, ipacked = struct.unpack_from("<HHHHB", data, pos + 1)
        pos += 10
        if ipacked & 0x80:
            pos += 3 * (1 << ((ipacked & 7) + 1))
        pos += 1  # LZW min code size
        pos = _skip_subblocks(data, pos)
        n += 1
    return n


def decode_gif_rgba(path: str, progress=None):
    """Return (composited RGBA frames, delays_cs).

    ``delays_cs[i]`` is the GCE delay of frame i in 1/100 s. Delay 0 (and
    missing GCE) become 10cs, matching common GIF players. Interlaced
    frames are deinterlaced per GIF89a four-pass order.
    ``progress(done, total)`` is invoked after each composited frame.
    """
    with open(path, "rb") as fh:
        data = fh.read()
    if data[:6] not in (b"GIF87a", b"GIF89a"):
        raise ValueError("not a GIF: %s" % path)
    w, h, packed, bg_idx, _aspect = struct.unpack_from("<HHBBB", data, 6)
    pos = 13
    gct = None
    gct_size = 1 << ((packed & 7) + 1)
    if packed & 0x80:
        gct = data[pos:pos + 3 * gct_size]
        pos += 3 * gct_size
    canvas = np.zeros((h, w, 4), dtype=np.uint8)
    saved = None
    frames = []
    delays = []
    trans_idx = None
    disposal = 0
    delay_cs = _DEFAULT_DELAY_CS
    total = 0
    if callable(progress):
        try:
            total = count_gif_frames(path)
        except Exception:
            total = 0
    while pos < len(data):
        b = data[pos]
        if b == 0x3B:
            break
        if b == 0x21:
            label = data[pos + 1]
            pos += 2
            if label == 0xF9 and pos < len(data) and data[pos] == 0x04:
                gce = data[pos + 1:pos + 5]
                packed_gce = gce[0]
                disposal = (packed_gce >> 2) & 0x07
                delay_cs = struct.unpack_from("<H", gce, 1)[0]
                trans_idx = gce[3] if (packed_gce & 1) else None
                pos += 5
                if pos < len(data) and data[pos] == 0x00:
                    pos += 1
                continue
            pos = _skip_subblocks(data, pos)
            continue
        if b != 0x2C:
            pos += 1
            continue
        left, top, fw, fh, ipacked = struct.unpack_from("<HHHHB", data, pos + 1)
        pos += 10
        pal = gct
        if ipacked & 0x80:
            lct_size = 1 << ((ipacked & 7) + 1)
            pal = data[pos:pos + 3 * lct_size]
            pos += 3 * lct_size
        min_code = data[pos]
        pos += 1
        chunks = bytearray()
        while pos < len(data) and data[pos] != 0:
            n = data[pos]
            chunks.extend(data[pos + 1:pos + 1 + n])
            pos += 1 + n
        if pos < len(data) and data[pos] == 0:
            pos += 1
        if pal is None:
            _LOG.warning("GIF frame without palette: %s", path)
            continue
        if disposal == 3:
            saved = canvas.copy()
        indices = _lzw_decode(min_code, bytes(chunks), fw * fh)
        if len(indices) < fw * fh:
            indices = indices + bytes(fw * fh - len(indices))
        idx = np.frombuffer(indices[: fw * fh], dtype=np.uint8).reshape((fh, fw))
        if ipacked & 0x40:
            idx = deinterlace_rows(idx)
        pal_arr = np.frombuffer(pal, dtype=np.uint8)
        ncolors = len(pal_arr) // 3
        pal_arr = pal_arr[: ncolors * 3].reshape((ncolors, 3))
        safe = np.clip(idx, 0, ncolors - 1)
        rgb = pal_arr[safe]
        alpha = np.full((fh, fw), 255, dtype=np.uint8)
        if trans_idx is not None:
            alpha[idx == trans_idx] = 0
        y0, x0 = int(top), int(left)
        y1, x1 = min(h, y0 + fh), min(w, x0 + fw)
        sl_y, sl_x = y1 - y0, x1 - x0
        if sl_y > 0 and sl_x > 0:
            region = canvas[y0:y1, x0:x1]
            src_rgb = rgb[:sl_y, :sl_x]
            src_a = alpha[:sl_y, :sl_x]
            mask = src_a > 0
            region[..., :3][mask] = src_rgb[mask]
            region[..., 3][mask] = 255
        frames.append(canvas.copy())
        delays.append(int(delay_cs) if int(delay_cs) > 0 else _DEFAULT_DELAY_CS)
        if callable(progress):
            try:
                progress(len(frames), total)
            except Exception:
                _LOG.exception("GIF decode progress callback failed")
        if disposal in (2,):
            canvas[y0:y1, x0:x1] = 0
        elif disposal == 3 and saved is not None:
            canvas = saved
            saved = None
        trans_idx = None
        disposal = 0
        delay_cs = _DEFAULT_DELAY_CS
    if not frames:
        raise ValueError("GIF had no frames: %s" % path)
    return frames, delays
