# SPDX-License-Identifier: GPL-3.0-or-later
# Extracted from the T01 monolith by tools/split_monolith.py.
# Original lines: 6699-6721, 6724-6768, 6771-6996, 6999-7140, 7143-7172, 7175-7229, 7232-7330, 9019-9063, 9065-9082, 9084-9103, 9105-9114, 9116-9164, 9166-9180, 9182-9192, 9194-9209, 9211-9225

import os
import re
import struct
import json
import subprocess
import cv2
import numpy as np
import time
from PySide6.QtGui import QImage
from cutora.core.config import (
    FFMPEG_PATH,
    FFPROBE_PATH,
)
from cutora.core.log import get_logger
from cutora.core.seek_trace import emit as _seek_trace_emit
import queue
import threading
from cutora.core.proc import popen as _no_window_popen, run as _no_window_run

_LOG = get_logger("cutora.render.readers")
_SEQUENCE_RE = re.compile(r"%0?(\d*)d\.(png|tga|tiff?)$", re.I)
_CAP_OPEN_LOCK = threading.RLock()
# ═══════════════════════════════════════════════════════════════════════════════
# Alpha-aware Video I/O Helpers (uncompressed ARGB MOV with alpha)
# ═══════════════════════════════════════════════════════════════════════════════

def _cv2_frame_to_qimage(frame):
    """Convert an OpenCV numpy frame (BGR/BGRA) to QImage.
    Automatically detects 3-channel (BGR) vs 4-channel (BGRA/alpha) frames."""
    if frame is None:
        return None
    fh, fw = frame.shape[:2]
    if fw <= 0 or fh <= 0:
        return None
    channels = frame.shape[2] if len(frame.shape) == 3 else 1
    if channels == 4:
        # Readers emit premultiplied BGRA. Format_ARGB32 (straight) would
        # double-multiply and draw a dark fringe on semi-transparent edges.
        return QImage(frame.data, fw, fh, frame.strides[0],
                      QImage.Format.Format_ARGB32_Premultiplied).copy()
    elif channels == 3:
        return QImage(frame.data, fw, fh, frame.strides[0],
                      QImage.Format.Format_BGR888).copy()
    else:
        return QImage(frame.data, fw, fh, frame.strides[0],
                      QImage.Format.Format_Grayscale8).copy()


def _is_sequence_path(path):
    return bool(_SEQUENCE_RE.search(str(path or "").replace("\\", "/")))


def sequence_first_frame(path):
    """Expand ``seq_%03d.png`` → ``seq_001.png``. None if not a sequence."""
    raw = str(path or "")
    m = _SEQUENCE_RE.search(raw.replace("\\", "/"))
    if not m:
        return None
    width = int(m.group(1) or 1)
    return _SEQUENCE_RE.sub(("%0" + str(width) + "d." + m.group(2)) % 1, raw, count=1)


def pix_fmt_has_alpha(pix_fmt, codec_name=""):
    """True for ffmpeg pix_fmts / codecs that carry an alpha plane.

    ProRes 4444 is ``yuva444p10le`` — the old exact-set missed every 10-bit
    yuva variant, so OpenCV opened the file and silently dropped alpha.
    """
    p = str(pix_fmt or "").lower()
    c = str(codec_name or "").lower()
    if not p and not c:
        return False
    if p in {
        "argb", "rgba", "bgra", "abgr", "rgba64le", "rgba64be",
        "yuva420p", "yuva422p", "yuva444p", "gbrap", "gbrap16le",
        "ya8", "ya16le", "pal8",
    }:
        return True
    if p.startswith("yuva") or p.startswith("gbrap") or p.startswith("rgba") \
            or p.startswith("argb") or p.startswith("bgra") or p.startswith("abgr"):
        return True
    if c in {"prores", "qtrle", "png", "gif", "webp", "apng", "targa"}:
        if "yuva" in p or p.endswith("a") or p in {"pal8", "rgba", "argb", "bgra", "abgr"}:
            return True
        if p == "" or "a" in p:
            return True
    return False


def premultiply_bgra(frame):
    """Straight BGRA uint8 → premultiplied BGRA (rgb *= a/255)."""
    if frame is None or getattr(frame, "ndim", 0) != 3 or frame.shape[2] != 4:
        return frame
    out = np.empty_like(frame)
    a = frame[..., 3].astype(np.float32) * (1.0 / 255.0)
    rgb = frame[..., :3].astype(np.float32) * a[..., None]
    out[..., :3] = np.clip(np.round(rgb), 0, 255).astype(np.uint8)
    out[..., 3] = frame[..., 3]
    return out


_PREMUL_TAGS = {
    "premultiplied", "premul", "premultiplied_alpha", "associated",
    "premultiplied_black", "premultipliedblack", "2",
}
_STRAIGHT_TAGS = {
    "straight", "str8", "unpremultiplied", "unassociated", "straight_alpha",
    "straightalpha",
}
_ALPHA_MODE_OVERRIDE = {}


def normalize_alpha_mode(raw) -> str:
    """Return ``straight`` (default) or ``premultiplied``."""
    s = str(raw or "").strip().lower().replace(" ", "_").replace("-", "_")
    if s in _PREMUL_TAGS:
        return "premultiplied"
    if s in _STRAIGHT_TAGS:
        return "straight"
    return "straight"


def set_path_alpha_mode(path: str, mode: str) -> None:
    if not path:
        return
    key = os.path.normcase(os.path.abspath(path))
    _ALPHA_MODE_OVERRIDE[key] = normalize_alpha_mode(mode)


def alpha_mode_for_path(path: str, probed=None) -> str:
    if path:
        key = os.path.normcase(os.path.abspath(str(path)))
        if key in _ALPHA_MODE_OVERRIDE:
            return _ALPHA_MODE_OVERRIDE[key]
    return normalize_alpha_mode(probed)


def ffprobe_video_has_alpha_uncached(path):
    """Use ffprobe to check if a video has an alpha channel and get basic info.
    Returns dict with 'has_alpha', 'width', 'height', 'fps', 'pix_fmt' or None.

    Uncached. Product code must go through ``_probe_video_has_alpha``.
    """
    probe_path = path
    extra = []
    if _is_sequence_path(path):
        first = sequence_first_frame(path)
        if first and os.path.isfile(first):
            probe_path = first
        else:
            extra = ["-f", "image2"]
    try:
        cmd = [
            FFPROBE_PATH, "-v", "error",
            *extra,
            "-select_streams", "v:0",
            "-show_entries",
            "stream=width,height,pix_fmt,r_frame_rate,codec_name:stream_tags=alpha_mode",
            "-of", "json", probe_path
        ]
        res = _no_window_run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True, timeout=10)
        if res.returncode != 0:
            _LOG.warning("ffprobe alpha check failed (%s): %s",
                         res.returncode, (res.stderr or "")[-300:])
            return None
        data = json.loads(res.stdout or "{}")
        streams = data.get("streams", [])
        if not streams:
            return None
        v = streams[0]
        pix_fmt = str(v.get("pix_fmt", "")).lower()
        codec_name = str(v.get("codec_name", "") or "")
        has_alpha = pix_fmt_has_alpha(pix_fmt, codec_name)
        tags = v.get("tags") or {}
        raw_mode = str(
            tags.get("alpha_mode") or tags.get("ALPHA_MODE")
            or tags.get("Alpha Mode") or "").strip()
        raw_l = raw_mode.lower()
        if raw_l in {"1", "true", "yes"}:
            has_alpha = True
        if raw_l in _PREMUL_TAGS or raw_l in _STRAIGHT_TAGS:
            alpha_mode = normalize_alpha_mode(raw_mode)
        else:
            alpha_mode = alpha_mode_for_path(path, None)
        width = int(v.get("width", 0))
        height = int(v.get("height", 0))
        fps = 30.0
        r_fr = v.get("r_frame_rate", "")
        if isinstance(r_fr, str) and "/" in r_fr:
            parts = r_fr.split("/")
            try:
                num, den = float(parts[0]), float(parts[1])
                if den > 0:
                    fps = num / den
            except Exception:
                _LOG.exception("parse r_frame_rate %r", r_fr)
        return {
            "has_alpha": has_alpha, "width": width, "height": height,
            "fps": fps, "pix_fmt": pix_fmt,
            "codec_name": codec_name,
            "sequence": _is_sequence_path(path),
            "alpha_mode": alpha_mode,
        }
    except Exception:
        _LOG.exception("ffprobe alpha check crashed: %s", path)
        return None


def _probe_video_has_alpha(path):
    """Cached alpha/stream probe. Same return as the uncached ffprobe helper."""
    from cutora.media.probe_cache import probe_alpha
    return probe_alpha(path)

class _RawARGBMOVReader:
    """Directly parses and reads raw ARGB frames from QuickTime MOV container.
    Used exclusively for uncompressed ARGB 8-bit codec (FourCC 0x20000000)
    which neither OpenCV nor FFmpeg can natively decode."""

    def __init__(self, path):
        self.path = path
        self._opened = False
        self._width = 0
        self._height = 0
        self._fps = 30.0
        self._frame_count = 0
        self._offsets = []
        self._sizes = []
        self._current_frame = 0
        self._f = None
        self._probe_and_init()

    def _probe_and_init(self):
        try:
            fsize = os.path.getsize(self.path)
            self._f = open(self.path, "rb")
            atoms = self._parse_atoms(self._f, fsize, 0)
            
            stsd = self._find_atom(atoms, "stsd")
            if not stsd: return
            data = stsd.get("data", b"")
            if len(data) < 24: return
            count = struct.unpack(">I", data[4:8])[0]
            if count < 1: return
            format_tag = data[12:16]
            if format_tag != b'\x00\x00\x00 ': return # 0x20000000 (ARGB)
            
            # Get width/height
            tkhd = self._find_atom(atoms, "tkhd")
            if tkhd and len(tkhd.get("data", b"")) >= 84:
                w_fixed = struct.unpack(">I", tkhd["data"][76:80])[0]
                h_fixed = struct.unpack(">I", tkhd["data"][80:84])[0]
                self._width = w_fixed >> 16
                self._height = h_fixed >> 16
                
            stsz = self._find_atom(atoms, "stsz")
            if not stsz: return
            sz_data = stsz.get("data", b"")
            if len(sz_data) < 12: return
            sample_size = struct.unpack(">I", sz_data[4:8])[0]
            sample_count = struct.unpack(">I", sz_data[8:12])[0]
            
            if sample_size > 0:
                self._sizes = [sample_size] * sample_count
            else:
                if len(sz_data) < 12 + sample_count * 4: return
                self._sizes = list(struct.unpack(f">{sample_count}I", sz_data[12:12+sample_count*4]))
                
            stco = self._find_atom(atoms, "stco")
            co64 = self._find_atom(atoms, "co64")
            chunk_offsets = []
            if co64:
                co_data = co64.get("data", b"")
                if len(co_data) >= 8:
                    count = struct.unpack(">I", co_data[4:8])[0]
                    if len(co_data) >= 8 + count * 8:
                        chunk_offsets = list(struct.unpack(f">{count}Q", co_data[8:8+count*8]))
            elif stco:
                co_data = stco.get("data", b"")
                if len(co_data) >= 8:
                    count = struct.unpack(">I", co_data[4:8])[0]
                    if len(co_data) >= 8 + count * 4:
                        chunk_offsets = list(struct.unpack(f">{count}I", co_data[8:8+count*4]))
            
            stsc = self._find_atom(atoms, "stsc")
            if not stsc or not chunk_offsets: return
            sc_data = stsc.get("data", b"")
            if len(sc_data) < 8: return
            stsc_count = struct.unpack(">I", sc_data[4:8])[0]
            if len(sc_data) < 8 + stsc_count * 12: return
            
            stsc_table = []
            for i in range(stsc_count):
                first_chunk, samples_per_chunk, _ = struct.unpack(">III", sc_data[8+i*12:20+i*12])
                stsc_table.append((first_chunk, samples_per_chunk))
                
            # Build frame offsets
            frame_idx = 0
            for i in range(len(chunk_offsets)):
                chunk_idx = i + 1
                samples_in_chunk = 0
                for j in range(len(stsc_table)-1, -1, -1):
                    if chunk_idx >= stsc_table[j][0]:
                        samples_in_chunk = stsc_table[j][1]
                        break
                
                offset = chunk_offsets[i]
                for s in range(samples_in_chunk):
                    if frame_idx < len(self._sizes):
                        self._offsets.append(offset)
                        offset += self._sizes[frame_idx]
                        frame_idx += 1
                        
            if not self._offsets: return
            self._frame_count = len(self._offsets)
            
            # FPS estimation (fallback)
            stts = self._find_atom(atoms, "stts")
            mdhd = self._find_atom(atoms, "mdhd")
            timescale = 30000
            if mdhd and len(mdhd.get("data", b"")) >= 24:
                version = mdhd["data"][0]
                if version == 0:
                    timescale = struct.unpack(">I", mdhd["data"][12:16])[0]
                elif version == 1 and len(mdhd["data"]) >= 32:
                    timescale = struct.unpack(">I", mdhd["data"][20:24])[0]
                    
            if stts and len(stts.get("data", b"")) >= 16:
                sample_count, sample_delta = struct.unpack(">II", stts["data"][8:16])
                if sample_delta > 0:
                    self._fps = timescale / sample_delta
                    
            if self._width > 0 and self._height > 0:
                self._opened = True
                
        except Exception:
            _LOG.exception("Raw ARGB MOV probe failed: %s", self.path)
            
    def _parse_atoms(self, f, end_pos, depth=0):
        results = []
        if depth > 10: return results
        while f.tell() < end_pos - 7:
            pos = f.tell()
            header = f.read(8)
            if len(header) < 8: break
            size, atype = struct.unpack(">I4s", header)
            atype = atype.decode("latin-1", errors="replace")
            
            header_size = 8
            if size == 1:
                ext = f.read(8)
                if len(ext) < 8: break
                size = struct.unpack(">Q", ext)[0]
                header_size = 16
            elif size == 0:
                size = end_pos - pos
                
            atom_end = pos + size
            container_types = {"moov", "trak", "mdia", "minf", "stbl", "dinf", "edts", "udta"}
            
            if atype in container_types:
                children = self._parse_atoms(f, atom_end, depth + 1)
                results.append({"type": atype, "pos": pos, "size": size, "children": children})
            elif atype in ("stsd", "stco", "co64", "stsz", "stsc", "stts", "mdhd", "tkhd", "hdlr"):
                read_size = min(size - header_size, 1024 * 1024 * 10)
                content = f.read(read_size)
                results.append({"type": atype, "pos": pos, "size": size, "data": content})
                f.seek(atom_end)
            else:
                results.append({"type": atype, "pos": pos, "size": size})
                f.seek(atom_end)
                
            if f.tell() >= end_pos: break
        return results
        
    def _find_atom(self, atoms, name):
        for a in atoms:
            if a["type"] == name: return a
            if "children" in a:
                r = self._find_atom(a["children"], name)
                if r: return r
        return None

    def isOpened(self):
        return self._opened

    def get(self, prop_id):
        if prop_id == cv2.CAP_PROP_FPS: return self._fps
        elif prop_id == cv2.CAP_PROP_FRAME_WIDTH: return float(self._width)
        elif prop_id == cv2.CAP_PROP_FRAME_HEIGHT: return float(self._height)
        elif prop_id == cv2.CAP_PROP_FRAME_COUNT: return float(self._frame_count)
        elif prop_id == cv2.CAP_PROP_POS_MSEC: return (self._current_frame / self._fps) * 1000.0 if self._fps > 0 else 0.0
        return 0.0

    def set(self, prop_id, value):
        if prop_id == cv2.CAP_PROP_POS_MSEC:
            if self._fps > 0:
                self._current_frame = int((float(value) / 1000.0) * self._fps)
                self._current_frame = max(0, min(self._current_frame, self._frame_count - 1))
            return True
        elif prop_id == cv2.CAP_PROP_POS_FRAMES:
            self._current_frame = int(value)
            self._current_frame = max(0, min(self._current_frame, self._frame_count - 1))
            return True
        return False

    def read(self):
        if not self._opened or not self._f or self._current_frame >= self._frame_count:
            return False, None
            
        offset = self._offsets[self._current_frame]
        size = self._sizes[self._current_frame]
        
        expected_size = self._width * self._height * 4
        if size != expected_size:
            return False, None
            
        try:
            self._f.seek(offset)
            raw = self._f.read(size)
            if len(raw) < size: return False, None
            
            # Uncompressed ARGB 8-bit on Windows writes
            # 32-bit integers in Little-Endian format.
            # This means the byte order in memory is: B, G, R, A.
            # OpenCV and PyQt (Format_ARGB32) both natively expect this BGRA order,
            # so we can use the raw buffer directly without any channel swapping!
            bgra = np.frombuffer(raw, dtype=np.uint8).reshape((self._height, self._width, 4)).copy()
            if alpha_mode_for_path(self.path) != "premultiplied":
                bgra = premultiply_bgra(bgra)
            self._current_frame += 1
            return True, bgra
        except Exception:
            _LOG.exception("Raw ARGB MOV read failed: %s", self.path)
            return False, None
            
    def release(self):
        self._opened = False
        if self._f:
            try: self._f.close()
            except: pass
            self._f = None

class _FFmpegFrameReader:
    """Drop-in replacement for cv2.VideoCapture using FFmpeg subprocess pipe.
    Handles codecs OpenCV cannot decode (e.g. uncompressed ARGB QuickTime MOV
    with a transparency channel)."""

    def __init__(self, path, alpha_mode=None, threads=None):
        self._threads = int(threads) if threads else 0
        self.path = path
        self._opened = False
        self._width = 0
        self._height = 0
        self._fps = 30.0
        self._frame_count = 0
        self._has_alpha = False
        self._channels = 3
        self._pix_fmt_out = "bgr24"
        self._frame_size = 0
        self._proc = None
        self._current_msec = 0.0
        self._sequence = False
        self._codec_name = ""
        self._alpha_mode = normalize_alpha_mode(alpha_mode) if alpha_mode else None
        self._premul_in_filter = False
        self._needs_premultiply = False
        self._probe_and_init()

    def _probe_and_init(self):
        info = _probe_video_has_alpha(self.path)
        if not info or info["width"] <= 0 or info["height"] <= 0:
            return
        self._width = info["width"]
        self._height = info["height"]
        self._fps = info["fps"] if info["fps"] > 0 else 30.0
        self._has_alpha = bool(info["has_alpha"]) or _is_sequence_path(self.path)
        self._sequence = bool(info.get("sequence")) or _is_sequence_path(self.path)
        self._codec_name = str(info.get("codec_name") or "").lower()
        probed_mode = info.get("alpha_mode")
        if self._alpha_mode is None:
            self._alpha_mode = alpha_mode_for_path(self.path, probed_mode)
        else:
            set_path_alpha_mode(self.path, self._alpha_mode)
        if self._has_alpha:
            self._pix_fmt_out = "bgra"
            self._channels = 4
            self._needs_premultiply = self._alpha_mode != "premultiplied"
            self._premul_in_filter = self._needs_premultiply
        else:
            self._pix_fmt_out = "bgr24"
            self._channels = 3
            self._needs_premultiply = False
            self._premul_in_filter = False
        self._frame_size = self._width * self._height * self._channels
        try:
            cmd = [
                FFPROBE_PATH, "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1", self.path
            ]
            r = _no_window_run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, timeout=10)
            dur = float(r.stdout.strip()) if r.returncode == 0 and r.stdout.strip() else 0.0
            if dur > 0:
                self._frame_count = int(dur * self._fps)
        except Exception:
            pass
        self._opened = True

    def isOpened(self):
        return self._opened

    def get(self, prop_id):
        if prop_id == cv2.CAP_PROP_FPS:
            return self._fps
        elif prop_id == cv2.CAP_PROP_FRAME_WIDTH:
            return float(self._width)
        elif prop_id == cv2.CAP_PROP_FRAME_HEIGHT:
            return float(self._height)
        elif prop_id == cv2.CAP_PROP_FRAME_COUNT:
            return float(self._frame_count)
        elif prop_id == cv2.CAP_PROP_POS_MSEC:
            return self._current_msec
        return 0.0

    def set(self, prop_id, value):
        if prop_id == cv2.CAP_PROP_POS_MSEC:
            self._seek_to(float(value) / 1000.0)
            return True
        elif prop_id == cv2.CAP_PROP_POS_FRAMES:
            self._seek_to(float(value) / max(1.0, self._fps))
            return True
        return False

    def _seek_to(self, time_sec):
        self._kill_proc()
        self._current_msec = max(0.0, time_sec) * 1000.0
        cmd = [FFMPEG_PATH, "-loglevel", "error"]
        if self._threads > 0:
            cmd += ["-threads", str(self._threads)]      # Q180：解码线程数（见 _open_video_capture）
        # Native vp9/vp8 drop the alpha plane; libvpx keeps yuva / alpha_mode=1.
        if self._has_alpha and self._codec_name in {"vp9", "vp8", "libvpx", "libvpx-vp9"}:
            cmd += ["-c:v", "libvpx-vp9" if "vp9" in self._codec_name else "libvpx"]
        if time_sec > 0.01 and not self._sequence:
            cmd += ["-ss", f"{time_sec:.4f}"]
        if self._sequence:
            cmd += ["-framerate", str(self._fps or 30.0), "-f", "image2"]
            if time_sec > 0.01 and self._fps > 0:
                cmd += ["-start_number", str(int(time_sec * self._fps) + 1)]
        cmd += ["-i", self.path]
        if self._premul_in_filter:
            cmd += ["-vf", "format=rgba,premultiply=inplace=1"]
        cmd += [
            "-f", "rawvideo",
            "-pix_fmt", self._pix_fmt_out,
            "-v", "error",
            "pipe:1"
        ]
        try:
            self._proc = _no_window_popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                bufsize=self._frame_size * 8
            )
        except Exception:
            self._proc = None

    def read(self):
        if not self._opened:
            return False, None
        if self._proc is None:
            self._seek_to(self._current_msec / 1000.0)
        if self._proc is None:
            return False, None
        try:
            raw = self._proc.stdout.read(self._frame_size)
            if raw is None or len(raw) < self._frame_size:
                return False, None
            frame = np.frombuffer(raw, dtype=np.uint8).reshape(
                (self._height, self._width, self._channels)).copy()
            if self._channels == 4 and self._needs_premultiply and not self._premul_in_filter:
                frame = premultiply_bgra(frame)
            self._current_msec += 1000.0 / self._fps
            return True, frame
        except Exception:
            _LOG.exception("FFmpeg pipe read failed: %s", self.path)
            return False, None

    def grab(self):
        if not self._opened or self._proc is None:
            return False
        try:
            raw = self._proc.stdout.read(self._frame_size)
            if raw is None or len(raw) < self._frame_size:
                return False
            self._current_msec += 1000.0 / self._fps
            return True
        except Exception:
            return False

    def _kill_proc(self):
        if self._proc:
            try:
                self._proc.stdout.close()
            except Exception:
                pass
            try:
                self._proc.kill()
                self._proc.wait(timeout=2)
            except Exception:
                pass
            self._proc = None

    def release(self):
        self._kill_proc()
        self._opened = False

def _is_frame_corrupt(frame):
    """Detect corrupt frames from failed hardware video decoding.
    Intel iGPU Quick Sync often returns solid green (NV12 not converted)
    or all-black frames when HW accel fails silently.

    Q171：以前只看画面正中 8×8 —— 一块干净的绿幕 / 蓝幕、或者黑场开头，都会被当成「解坏了」，
    整条素材掉到最慢的 ffmpeg 管道解码（抠绿幕的素材恰恰全是这种）。现在看五块（中间 + 四个象限中心）：
    五块全是同一种灰阶纯色（黑 / 灰）才算坏；「经典解坏绿」只认 NV12 全零转出来的那种（R、B≈0，G≈135），
    真绿幕的 R / B 不会是 0；有颜色的整幅纯色（色卡、纯色背景）和以前一样算正常。"""
    if frame is None:
        return True
    try:
        h, w = frame.shape[:2]
        if h <= 0 or w <= 0:
            return True
        pts = ((h // 2, w // 2), (h // 4, w // 4), (h // 4, 3 * w // 4), (3 * h // 4, w // 4),
               (3 * h // 4, 3 * w // 4))
        means = []
        for cy, cx in pts:
            patch = frame[max(0, cy - 4):cy + 4, max(0, cx - 4):cx + 4]
            if patch.size == 0:
                return True
            flat = patch.reshape(-1, patch.shape[-1] if patch.ndim == 3 else 1).astype(np.float32)
            if float(flat.var(axis=0).max()) >= 1.0:
                return False                       # 有一块有纹理 = 正常画面（按通道算方差，纯色 ≠ 有纹理）
            means.append(flat.mean(axis=0))
        spread = float(np.max(np.ptp(np.array(means, dtype=np.float32), axis=0)))
        if spread > 6.0:
            return False                           # 几块是不同的纯色（例如纯色背景上的图形）
        m = means[0]
        if len(m) >= 3:
            b_mean, g_mean, r_mean = float(m[0]), float(m[1]), float(m[2])
            if g_mean > 100 and r_mean < 8 and b_mean < 8:
                return True                        # 经典 NV12 全零 → 绿屏
            if max(b_mean, g_mean, r_mean) - min(b_mean, g_mean, r_mean) > 6.0:
                return False                       # 整幅一种「有颜色」的纯色（色卡 / 纯色背景）是正常画面
        return True                                # 整幅一种灰阶纯色（黑屏 / 解坏）
    except Exception:
        return False


def _capture_looks_ok(cap) -> bool:
    """第一帧像解坏了，再看片子 40% 处的一帧 —— 黑场淡入 / 还没进人的空绿幕开头不是解坏。"""
    ret, frame = cap.read()
    if ret and frame is not None and not _is_frame_corrupt(frame):
        return True
    if not ret or frame is None:
        return False
    try:
        n = float(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        if n > 2:
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(n * 0.4))
            ret2, frame2 = cap.read()
            if ret2 and frame2 is not None and not _is_frame_corrupt(frame2):
                return True
    except Exception:
        pass
    return False


def _open_video_capture(path, hw: bool = True, alpha_mode=None, threads=None):
    """Open a video file with robust fallback for systems without dedicated GPU.
    Fallback chain: HW-accel OpenCV → SW-only OpenCV → Raw ARGB MOV → FFmpeg pipe.
    Detects corrupt frames (green/black) from failed Intel iGPU HW decoding.

    ``hw=False`` skips the D3D11VA/NVDEC tier entirely. The export path uses
    it (BUG-42): N workers × dozens of hardware decoder sessions + NVENC was
    enough to reset the display driver — worker logs show
    ``Unable to lock D3D11VA surface (887a0005)`` = DXGI_ERROR_DEVICE_REMOVED,
    i.e. the black screen, followed by garbage frames (花屏). Software H.264
    decoding is bit-exact and cheap at these sizes.

    Alpha sources (ProRes 4444, VP9-alpha, PNG sequence, uncompressed ARGB)
    skip OpenCV entirely: ``cv2.VideoCapture`` always returns BGR and drops
    the alpha plane.

    ``threads``（Q180）：这个解码器最多用几个线程。不给 = FFmpeg 自己定（= CPU 线程数、最多 16）——
    导出时 6 个进程 × 3 个源 × 16 = 近 300 个解码线程互相抢核，是 Luca 那台 32 线程机器上
    每帧 CPU 比云端贵 3 倍的来源之一。解出来的像素和线程数无关（H.264 解码是确定的）。
    """
    _thr = []
    if threads:
        try:
            _thr = [cv2.CAP_PROP_N_THREADS, max(1, int(threads))]
        except Exception:
            _thr = []

    info = None
    if alpha_mode:
        set_path_alpha_mode(path, alpha_mode)
    ext = os.path.splitext(str(path or ""))[1].lower()
    maybe_alpha = _is_sequence_path(path) or ext in {
        ".mov", ".webm", ".gif", ".png", ".apng", ".tga", ".tif", ".tiff",
    }
    if maybe_alpha:
        info = _probe_video_has_alpha(path)
    wants_alpha = _is_sequence_path(path) or bool(info and info.get("has_alpha"))
    if wants_alpha:
        if (not _is_sequence_path(path)) and str(path).lower().endswith(".mov"):
            try:
                raw_reader = _RawARGBMOVReader(path)
                if raw_reader.isOpened():
                    _LOG.info("[VideoIO] uncompressed ARGB MOV: %s", os.path.basename(path))
                    return raw_reader
            except Exception:
                _LOG.exception("Raw ARGB open failed: %s", path)
        try:
            reader = _FFmpegFrameReader(path, alpha_mode=alpha_mode, threads=threads)
            if reader.isOpened() and reader._has_alpha:
                _LOG.info("[VideoIO] FFmpeg pipe bgra for alpha source: %s pix=%s",
                          os.path.basename(path), (info or {}).get("pix_fmt"))
                return reader
            if reader.isOpened():
                _LOG.warning("[VideoIO] FFmpeg pipe opened but has_alpha=False: %s pix=%s",
                             path, (info or {}).get("pix_fmt"))
                return reader
        except Exception:
            _LOG.exception("FFmpeg pipe open failed for alpha source: %s", path)
        _LOG.warning("[VideoIO] alpha source falling through to OpenCV (alpha will be lost): %s", path)

    # --- Tier 1: Try hardware-accelerated OpenCV ---
    if hw:
        with _CAP_OPEN_LOCK:
            try:
                cap = cv2.VideoCapture(path, cv2.CAP_FFMPEG,
                                       [cv2.CAP_PROP_HW_ACCELERATION, cv2.VIDEO_ACCELERATION_ANY] + _thr)
                if cap.isOpened():
                    if _capture_looks_ok(cap):
                        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                        return cap
                    # HW accel produced corrupt frame — release and fall through
                    print(f"[VideoIO] HW-accel produced corrupt frame for '{os.path.basename(path)}', falling back to SW decoding")
                    cap.release()
            except Exception:
                pass

    # --- Tier 2: Software-only OpenCV (no HW acceleration) ---
    with _CAP_OPEN_LOCK:
        try:
            cap = cv2.VideoCapture(path, cv2.CAP_FFMPEG,
                                   [cv2.CAP_PROP_HW_ACCELERATION, cv2.VIDEO_ACCELERATION_NONE] + _thr)
            if not cap.isOpened():
                cap = cv2.VideoCapture(path)
            if cap.isOpened():
                if _capture_looks_ok(cap):
                    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    return cap
                cap.release()
        except Exception:
            pass
        
    # --- Tier 3: Raw ARGB parsing (uncompressed ARGB MOV) ---
    try:
        if path.lower().endswith('.mov'):
            raw_reader = _RawARGBMOVReader(path)
            if raw_reader.isOpened():
                print(f"[VideoIO] OpenCV failed, using Raw ARGB MOV decoder for '{os.path.basename(path)}'")
                return raw_reader
    except Exception:
        pass
        
    # --- Tier 4: FFmpeg pipe decoder (universal fallback) ---
    try:
        reader = _FFmpegFrameReader(path, alpha_mode=alpha_mode, threads=threads)
        if reader.isOpened():
            print(f"[VideoIO] OpenCV failed, using FFmpeg pipe decoder for '{os.path.basename(path)}'")
            return reader
    except Exception:
        pass
    return None


def read_video_frame_qimage(path, time_sec=0.0, alpha_mode=None):
    """One frame for hit-testing. Not the playback path; the capture is closed."""
    cap = _open_video_capture(path, hw=False, alpha_mode=alpha_mode)
    if cap is None or not getattr(cap, "isOpened", lambda: False)():
        return None
    try:
        try:
            cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, float(time_sec)) * 1000.0)
        except Exception:
            pass
        ok, frame = cap.read()
        if not ok or frame is None:
            return None
        return _cv2_frame_to_qimage(frame)
    finally:
        try:
            cap.release()
        except Exception:
            pass


BACK_SEEK_MS = 100.0
FORWARD_CATCHUP_MS = 2000.0
SEEK_DEBOUNCE_S = 0.15


def need_hard_seek(msec, first_msec, last_msec, last_time, empty):
    """True when preview must cap.set; False = sequential catch-up or wait."""
    if empty:
        if last_time < 0:
            return False
        if msec < last_time - BACK_SEEK_MS:
            return True
        return msec > last_time + FORWARD_CATCHUP_MS
    if msec < first_msec - BACK_SEEK_MS:
        return True
    return msec > last_msec + FORWARD_CATCHUP_MS


# Q188：预览解码器的开销。每个 AsyncVideoDecoder = 一个 FFmpeg 解码器（以前不限线程 = 16 条解码线程 +
# 各自的参考帧池）+ 一条预读队列（以前 30 张 QImage ≈ 40 MB）+ 一条 Python 线程。Luca 的工程 41 个源，
# 画布给每个源各开一个、永不关：主界面常驻 3.6 GB、私有提交 7 GB，650 多条解码线程。
# 现在：每个解码器最多 PREVIEW_DECODE_THREADS 条线程、预读 PREVIEW_LOOKAHEAD 帧，画布那边做 LRU 池 + 闲置回收。
# 实测（1080×1920 H.264）：16 线程 132 MB / 4 线程 53 MB / 2 线程 40 MB 一个解码器；预览只要 30 fps，2 条够。
PREVIEW_DECODE_THREADS = 2
PREVIEW_LOOKAHEAD = 16


def preview_hw_decode() -> bool:
    """Q189d：预览解码默认走软解。硬解（D3D11VA）每个会话在显卡驱动里起十几条线程（Luca 机器上
    nvwgf2umx.dll 名下 132 条）、占显存、而且几十条视频轮流开关硬解会话会把驱动打挂（Q156 监视器
    就是因此改软解的）。≤1080×1920 的源两条软解线程足够预览 30 fps。CUTORA_PREVIEW_HW=1 可切回。"""
    return (os.environ.get("CUTORA_PREVIEW_HW") or "") == "1"


class AsyncVideoDecoder(threading.Thread):
    def __init__(self, path, source_id, threads=None, lookahead=None):
        super().__init__()
        self.path = path
        self.source_id = source_id
        self.daemon = True
        self.cap = _open_video_capture(path, hw=preview_hw_decode(), threads=int(threads or PREVIEW_DECODE_THREADS))
        if self.cap is None:
            # Last resort: raw OpenCV (may still fail for some codecs)
            self.cap = cv2.VideoCapture(path)
        self.sar = _get_sar_from_cap_or_path(self.cap, path) if not isinstance(self.cap, _FFmpegFrameReader) else None
        self.fps = self.cap.get(cv2.CAP_PROP_FPS) or 30.0
        if self.fps <= 0: self.fps = 30.0
        self.queue = queue.Queue(maxsize=int(lookahead or PREVIEW_LOOKAHEAD))
        self.last_used = time.perf_counter()
        self.seek_req = 0.0 # Initial seek immediately to 0
        self.seek_generation = 0
        self._last_seek_accept = 0.0
        _seek_trace_emit(
            "seek_req", msec=0.0, qlen=0, source_id=source_id, where="init")
        self.lock = threading.Lock()
        self._wake = threading.Condition(self.lock)      # Q189e：队列满 / 到尾了就睡在这上面，不再 100 Hz 轮询
        self.running = True
        self.last_frame = None
        self.last_time = -1.0

    def _unpack_item(self, item):
        if len(item) >= 3:
            return item[0], item[1], item[2]
        return item[0], item[1], self.seek_generation

    def _request_hard_seek(self, msec, where):
        now = time.perf_counter()
        qlen = self.queue.qsize()
        if self.seek_req >= 0:
            self.seek_req = msec
            _seek_trace_emit(
                "seek_req", msec=msec, qlen=qlen,
                source_id=self.source_id, where=where + "_retarget")
            self._wake.notify_all()
            return
        if now - self._last_seek_accept < SEEK_DEBOUNCE_S:
            _seek_trace_emit(
                "seek_req", msec=msec, qlen=qlen,
                source_id=self.source_id, where=where + "_debounce")
            return
        self._last_seek_accept = now
        self.seek_generation += 1
        self.seek_req = msec
        self._wake.notify_all()
        _seek_trace_emit(
            "seek_req", msec=msec, qlen=qlen,
            source_id=self.source_id, where=where)
        if qlen > 0:
            try:
                *_, last_item = self.queue.queue
                _m, img, _g = self._unpack_item(last_item)
                self.last_frame = img
                self.last_time = _m
            except Exception:
                pass
            self.queue.queue.clear()

    def get_frame(self, msec):
        self.last_used = time.perf_counter()
        with self.lock:
            drained = False
            while not self.queue.empty():
                item_msec, qimg, gen = self._unpack_item(self.queue.queue[0])
                if gen != self.seek_generation:
                    self.queue.get()
                    drained = True
                    continue
                break
            if drained:
                self._wake.notify_all()
            empty = self.queue.empty()
            if empty:
                first_msec = last_msec = None
            else:
                first_msec = self._unpack_item(self.queue.queue[0])[0]
                last_msec = self._unpack_item(self.queue.queue[-1])[0]
            if need_hard_seek(msec, first_msec, last_msec, self.last_time, empty):
                self._request_hard_seek(
                    msec, "empty_jump" if empty else "range_jump")

        half = (1000.0 / self.fps) * 0.5
        consumed = False
        try:
            while not self.queue.empty():
                item_msec, qimg, gen = self._unpack_item(self.queue.queue[0])
                if gen != self.seek_generation or item_msec < msec - half:
                    self.queue.get()
                    consumed = True
                    self.last_frame = qimg
                    self.last_time = item_msec
                else:
                    return qimg
            return self.last_frame
        finally:
            if consumed:
                with self.lock:
                    self._wake.notify_all()          # 队列腾出位置了：叫醒解码线程

    def run(self):
        while self.running:
            seek_target = -1.0
            with self.lock:
                if self.seek_req >= 0:
                    seek_target = self.seek_req
                    self.seek_req = -1.0
            
            cap = self.cap
            if cap is None:
                break
            if seek_target >= 0:
                t0 = time.perf_counter()
                cap.set(cv2.CAP_PROP_POS_MSEC, seek_target)
                _seek_trace_emit(
                    "cap_set",
                    msec=seek_target,
                    dt_ms=round((time.perf_counter() - t0) * 1000.0, 3),
                    source_id=self.source_id,
                )
            
            if not self.queue.full():
                current_msec = cap.get(cv2.CAP_PROP_POS_MSEC)
                ret, frame = cap.read()
                if ret and frame is not None:
                    fh, fw = frame.shape[:2]
                    max_preview_dim = 800.0
                    if fw > max_preview_dim or fh > max_preview_dim:
                        hw_scale = min(max_preview_dim / fw, max_preview_dim / fh)
                        new_w = int(fw * hw_scale)
                        new_h = int(fh * hw_scale)
                        frame = cv2.resize(frame, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
                    
                    if self.sar:
                        frame = _apply_sar_correction(frame, self.sar[0], self.sar[1])
                        
                    fh, fw = frame.shape[:2]
                    qimg = _cv2_frame_to_qimage(frame)
                    
                    with self.lock:
                        if self.seek_req < 0:
                            self.queue.put(
                                (current_msec, qimg, self.seek_generation))
                            self.last_time = current_msec
                else:
                    with self.lock:                      # 到尾 / 读失败：等 seek 或停（get_frame / stop 会 notify）
                        if self.running and self.seek_req < 0:
                            self._wake.wait()
            else:
                with self.lock:                          # 队列满：等消费者取走或 seek
                    if self.running and self.seek_req < 0 and self.queue.full():
                        self._wake.wait()

    def stop(self, join_timeout: float = 1.0):
        """停线程、放解码器、清预读队列。先等线程退出再 release（线程还在 read 时 release 会崩）。"""
        self.running = False
        try:
            with self.lock:
                self._wake.notify_all()
        except Exception:
            pass
        try:
            if self.is_alive() and threading.current_thread() is not self:
                self.join(timeout=join_timeout)
        except Exception:
            pass
        cap, self.cap = self.cap, None
        if cap is not None:
            try:
                cap.release()
            except Exception:
                pass
        try:
            with self.lock:
                self.queue.queue.clear()
        except Exception:
            pass
        self.last_frame = None

# --- Render Worker (Fixed: 修复 Pipe 死锁，改用日志文件记录 stderr) ---
def probe_bitrates(video_path):
    try:
        cmd = [
            FFPROBE_PATH, "-v", "error",
            "-show_entries", "format=bit_rate:stream=codec_type,bit_rate,width,height",
            "-of", "json", video_path
        ]
        res = _no_window_run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if res.returncode != 0:
            return None
        data = json.loads(res.stdout or "{}")
        fmt = data.get("format", {})
        total_bps = int(float(fmt["bit_rate"])) if fmt.get("bit_rate") else None
        video_bps = None
        audio_bps = None
        width = None
        height = None
        for st in data.get("streams", []):
            if st.get("codec_type") == "video":
                if st.get("bit_rate"):
                    video_bps = int(float(st["bit_rate"]))
                if st.get("width"):
                    width = int(st["width"])
                if st.get("height"):
                    height = int(st["height"])
            elif st.get("codec_type") == "audio":
                if st.get("bit_rate"):
                    audio_bps = int(float(st["bit_rate"]))
        if not total_bps:
            if video_bps and audio_bps:
                total_bps = video_bps + audio_bps
            elif video_bps:
                total_bps = video_bps
        if not total_bps:
            return None
        return {
            "total_bps": total_bps,
            "video_bps": video_bps,
            "audio_bps": audio_bps,
            "width": width,
            "height": height
        }
    except Exception:
        return None

def probe_duration_sec(media_path):
    from cutora.media.probe_cache import probe_duration_sec as _cached
    return _cached(media_path)

def _parse_ratio_str(val):
    try:
        if not val:
            return None
        if isinstance(val, (int, float)):
            return None
        txt = str(val).strip()
        if not txt or txt in ("0:1", "0/1", "N/A"):
            return None
        sep = ":" if ":" in txt else ("/" if "/" in txt else None)
        if not sep:
            return None
        a, b = txt.split(sep, 1)
        num = float(a)
        den = float(b)
        if num <= 0 or den <= 0:
            return None
        return (num, den)
    except Exception:
        return None

def _ratio_to_float(ratio):
    try:
        if not ratio:
            return None
        num, den = ratio
        if not num or not den:
            return None
        return float(num) / float(den)
    except Exception:
        return None

def probe_video_export_params(video_path):
    try:
        if not video_path or not os.path.exists(video_path):
            return None
        cmd = [
            FFPROBE_PATH, "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height,avg_frame_rate,r_frame_rate,sample_aspect_ratio",
            "-of", "json", video_path
        ]
        res = _no_window_run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if res.returncode != 0:
            return None
        data = json.loads(res.stdout or "{}")
        streams = data.get("streams", [])
        v = streams[0] if streams else {}
        width = int(v.get("width")) if v.get("width") else None
        height = int(v.get("height")) if v.get("height") else None
        fps = None
        for key in ("avg_frame_rate", "r_frame_rate"):
            ratio = _parse_ratio_str(v.get(key))
            fps = _ratio_to_float(ratio)
            if fps and fps > 0:
                break
        sar = _parse_ratio_str(v.get("sample_aspect_ratio"))

        audio_rate = None
        cmd_a = [
            FFPROBE_PATH, "-v", "error",
            "-select_streams", "a:0",
            "-show_entries", "stream=sample_rate",
            "-of", "default=noprint_wrappers=1:nokey=1",
            video_path
        ]
        res_a = _no_window_run(cmd_a, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if res_a.returncode == 0:
            sr_txt = (res_a.stdout or "").strip()
            if sr_txt.isdigit():
                audio_rate = int(sr_txt)

        return {
            "width": width,
            "height": height,
            "fps": fps,
            "sar": sar,
            "audio_rate": audio_rate
        }
    except Exception:
        return None

def probe_sample_aspect_ratio(video_path):
    try:
        cmd = [
            FFPROBE_PATH, "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=sample_aspect_ratio",
            "-of", "default=noprint_wrappers=1:nokey=1",
            video_path
        ]
        res = _no_window_run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if res.returncode != 0:
            return None
        return _parse_ratio_str(res.stdout.strip())
    except Exception:
        return None

def _get_sar_from_cap_or_path(cap, path=None):
    try:
        num = cap.get(cv2.CAP_PROP_SAR_NUM)
        den = cap.get(cv2.CAP_PROP_SAR_DEN)
        if num and den and num > 0 and den > 0:
            return (float(num), float(den))
    except Exception:
        pass
    if path:
        return probe_sample_aspect_ratio(path)
    return None

def _apply_sar_correction(frame, sar_num, sar_den):
    try:
        if not sar_num or not sar_den:
            return frame
        ratio = float(sar_num) / float(sar_den)
        if abs(ratio - 1.0) < 1e-6:
            return frame
        h, w = frame.shape[:2]
        new_w = int(round(w * ratio))
        new_h = h
        if new_w <= 0 or new_h <= 0 or (new_w == w and new_h == h):
            return frame
        interp = cv2.INTER_AREA if new_w < w else cv2.INTER_LINEAR
        return cv2.resize(frame, (new_w, new_h), interpolation=interp)
    except Exception:
        return frame

def has_audio_stream(video_path):
    try:
        cmd = [
            FFPROBE_PATH, "-v", "error",
            "-select_streams", "a:0",
            "-show_entries", "stream=codec_type",
            "-of", "default=noprint_wrappers=1:nokey=1",
            video_path
        ]
        res = _no_window_run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if res.returncode != 0:
            return False
        return bool(res.stdout.strip())
    except Exception:
        return False
