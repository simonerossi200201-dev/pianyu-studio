# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""T24: GIF / APNG / animated WebP / image sequence → video clip, loop, keep alpha."""
from __future__ import annotations

import os
import re
import subprocess

from cutora.core.log import get_logger
from cutora.core.proc import popen as _no_window_popen, run as _no_window_run

_LOG = get_logger("cutora.media.animated")

ANIM_EXT = {".gif", ".webp", ".apng"}
SEQUENCE_RE = re.compile(r"%0?\d*d\.(png|tga|tiff?)$", re.I)


def _nb_frames(path: str) -> int | None:
    """ffprobe nb_frames, then count_frames. None if unreadable."""
    if not path or not os.path.isfile(path):
        return None
    from cutora.core.config import FFPROBE_PATH
    probe = FFPROBE_PATH
    try:
        r = _no_window_run(
            [probe, "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=nb_frames",
             "-of", "csv=p=0", path],
            capture_output=True, text=True, timeout=15,
        )
        raw = (r.stdout or "").strip().splitlines()
        val = raw[0].strip() if raw else ""
        if val and val.upper() != "N/A":
            n = int(float(val))
            return n
    except Exception:
        _LOG.exception("ffprobe nb_frames failed: %s", path)
    try:
        r = _no_window_run(
            [probe, "-v", "error", "-select_streams", "v:0",
             "-count_frames",
             "-show_entries", "stream=nb_read_frames,nb_frames",
             "-of", "default=noprint_wrappers=1", path],
            capture_output=True, text=True, timeout=40,
        )
        n = None
        for line in (r.stdout or "").splitlines():
            if "=" not in line:
                continue
            k, v = line.split("=", 1)
            v = v.strip()
            if k in ("nb_read_frames", "nb_frames") and v and v.upper() != "N/A":
                try:
                    n = int(float(v))
                except ValueError:
                    continue
                if k == "nb_read_frames":
                    return n
        return n
    except Exception:
        _LOG.exception("ffprobe count_frames failed: %s", path)
        return None


def classify_animated(path: str) -> str | None:
    """Return kind or None if this path is not an animated still.

    Sequences match the printf pattern. GIF is counted by our parser so a
    static ``.gif`` stays a still image without a slow ffprobe pass. WebP/APNG
    need ffprobe ``nb_frames > 1``.
    """
    raw = str(path or "")
    if SEQUENCE_RE.search(raw.replace("\\", "/")):
        return "sequence"
    ext = os.path.splitext(raw)[1].lower()
    kind = None
    if ext in ANIM_EXT:
        kind = ext.lstrip(".")
    elif ext == ".png" and os.path.basename(raw).lower().endswith(".apng"):
        kind = "apng"
    if not kind:
        return None
    if kind == "gif" and os.path.isfile(raw):
        try:
            from cutora.media.gifdecode import count_gif_frames
            n = count_gif_frames(raw)
            if n > 1:
                return "gif"
            if n == 1:
                return None
        except Exception:
            _LOG.exception("count_gif_frames failed: %s", raw)
    n = _nb_frames(raw)
    if n is None:
        _LOG.warning("cannot probe frame count, not treating as animation: %s", raw)
        return None
    if n > 1:
        return kind
    return None


def decode_animated(src: str, dest: str, ffmpeg: str, fps: float | None = None,
                    kind: str | None = None, progress=None) -> str:
    """Decode to a PNG-in-MOV with rgba so 1-bit GIF transparency is kept."""
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    kind = kind or classify_animated(src)
    if kind == "gif":
        return _decode_gif_with_disposal(src, dest, ffmpeg, progress=progress)
    cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error"]
    is_seq = (kind == "sequence") or bool(SEQUENCE_RE.search(str(src).replace("\\", "/")))
    if is_seq:
        rate = float(fps if fps not in (None, "") else 30.0)
        if rate <= 0:
            rate = 30.0
        cmd += ["-framerate", str(rate)]
    cmd += [
        "-i", src,
        "-c:v", "png", "-pix_fmt", "rgba",
        dest,
    ]
    r = _no_window_run(cmd, capture_output=True, timeout=60)
    if r.returncode != 0 or not os.path.isfile(dest):
        raise RuntimeError(
            "animated decode failed: %s" % ((r.stderr or b"")[-300:],)
        )
    return dest


def _write_rgba_png(path, rgba) -> None:
    import struct
    import zlib
    h, w = rgba.shape[:2]
    raw = b"".join(b"\x00" + rgba[y].tobytes() for y in range(h))

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)
    with open(path, "wb") as fh:
        fh.write(b"\x89PNG\r\n\x1a\n")
        fh.write(chunk(b"IHDR", ihdr))
        fh.write(chunk(b"IDAT", zlib.compress(raw, 9)))
        fh.write(chunk(b"IEND", b""))


def _decode_gif_with_disposal(src: str, dest: str, ffmpeg: str,
                              progress=None) -> str:
    """Composite GIF disposal ourselves — ffmpeg's gif decoder is not reliable."""
    import shutil
    import tempfile
    from cutora.media.gifdecode import decode_gif_rgba
    frames, delays = decode_gif_rgba(src, progress=progress)
    if not frames:
        raise RuntimeError("GIF decoded to 0 frames: %s" % src)
    staging = tempfile.mkdtemp(prefix="cutora_gif_")
    try:
        for i, fr in enumerate(frames, start=1):
            _write_rgba_png(os.path.join(staging, "f_%04d.png" % i), fr)
        cmd = _gif_encode_cmd(ffmpeg, staging, dest, delays)
        r = _no_window_run(cmd, capture_output=True, timeout=180)
        if r.returncode != 0 or not os.path.isfile(dest):
            raise RuntimeError(
                "GIF composite encode failed: %s" % ((r.stderr or b"")[-300:],)
            )
        return dest
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _gif_encode_cmd(ffmpeg: str, staging: str, dest: str, delays) -> list[str]:
    """Constant-rate ``-framerate`` when every GCE delay matches; else concat."""
    secs = []
    for d in delays:
        cs = int(d) if d is not None else 10
        secs.append((cs if cs > 0 else 10) / 100.0)
    if not secs:
        secs = [0.1]
    pattern = os.path.join(staging, "f_%04d.png")
    unique = set(secs)
    if len(unique) == 1:
        dur = next(iter(unique))
        fps = (1.0 / dur) if dur > 0 else 10.0
        return [
            ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
            "-framerate", ("%.6g" % fps), "-i", pattern,
            "-c:v", "png", "-pix_fmt", "rgba", dest,
        ]
    list_txt = os.path.join(staging, "concat.txt")
    n = len(secs)
    with open(list_txt, "w", encoding="utf-8") as fh:
        last = None
        for i in range(n):
            p = os.path.join(staging, "f_%04d.png" % (i + 1)).replace("\\", "/")
            fh.write("file '%s'\n" % p)
            fh.write("duration %.6g\n" % secs[i])
            last = p
        fh.write("file '%s'\n" % last)
    return [
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
        "-f", "concat", "-safe", "0", "-i", list_txt,
        "-c:v", "png", "-pix_fmt", "rgba", dest,
    ]


def persistent_anim_dir(project_dir: str | None = None,
                       storage_mode: str = "library") -> str:
    """Library mode lives in the user asset tree; project mode under assets/."""
    mode = (storage_mode or "library").strip().lower()
    if mode == "project" and project_dir:
        folder = os.path.join(os.path.abspath(project_dir), "assets", "_transcodes")
    else:
        from cutora.assets.user import user_assets_root
        folder = os.path.join(user_assets_root(), "animated", "_transcodes")
    os.makedirs(folder, exist_ok=True)
    return folder


def cache_path_for(src: str, dest_dir: str | None = None) -> str:
    import hashlib
    from cutora.audio.waveform import file_fingerprint
    if dest_dir:
        folder = dest_dir
    else:
        folder = persistent_anim_dir()
    os.makedirs(folder, exist_ok=True)
    stem = os.path.splitext(os.path.basename(src))[0]
    fp = file_fingerprint(src)
    if fp is None:
        tag = "missing"
    else:
        _path, mtime_ns, size = fp
        tag = "%d_%d" % (int(mtime_ns), int(size))
    digest = hashlib.sha1(
        os.path.abspath(src).encode("utf-8", "replace")).hexdigest()[:10]
    return os.path.join(folder, "%s._cutora_anim.%s.%s.mov" % (stem, tag, digest))


def import_animated_as_clip(src: str, ffmpeg: str | None = None,
                            fps: float | None = None, progress=None,
                            dest_dir: str | None = None) -> dict:
    if ffmpeg is None:
        from cutora.core.config import FFMPEG_PATH
        ffmpeg = FFMPEG_PATH
    kind = classify_animated(src)
    if not kind:
        raise ValueError("not an animated still: %s" % src)
    dest = cache_path_for(src, dest_dir=dest_dir)
    if not os.path.isfile(dest):
        decode_animated(src, dest, ffmpeg, fps=fps, kind=kind, progress=progress)
    return {
        "path": dest,
        "source_path": os.path.abspath(src) if os.path.isfile(src) else str(src),
        "kind": kind,
        "loop": True,
        "alpha": True,
        "fit_mode": "fill",
    }


def try_import_animated(src: str, ffmpeg: str | None = None,
                        fps: float | None = None, progress=None,
                        dest_dir: str | None = None) -> tuple[dict | None, str | None]:
    """(clip_info, error). (None, None) if not animated; (None, err) on failure."""
    if not classify_animated(src):
        return None, None
    try:
        return import_animated_as_clip(
            src, ffmpeg=ffmpeg, fps=fps, progress=progress,
            dest_dir=dest_dir), None
    except Exception as exc:
        _LOG.exception("animated import failed: %s", src)
        return None, "导入动画失败：%s" % exc


_ANIM_WORKERS = []
_ANIM_BRIDGES = []


def start_animated_import(src: str, fps: float | None = None,
                          ffmpeg: str | None = None, on_done=None,
                          on_progress=None, parent=None,
                          dest_dir: str | None = None):
    """Decode on a QThread. ``on_done(info, err)`` and ``on_progress(i, n)``
    run on the GUI thread via a queued QObject bridge.
    """
    from PySide6.QtCore import QObject, QThread, Qt, Signal
    from PySide6.QtWidgets import QApplication

    class _Worker(QThread):
        progress = Signal(int, int)
        finished_ok = Signal(object)
        failed = Signal(str)

        def __init__(self, path, rate, ff, qparent=None, dest_dir=None):
            super().__init__(qparent)
            self._path = path
            self._rate = rate
            self._ff = ff
            self._dest_dir = dest_dir

        def run(self):
            def _prog(i, n):
                self.progress.emit(int(i), int(n))

            info, err = try_import_animated(
                self._path, ffmpeg=self._ff, fps=self._rate, progress=_prog,
                dest_dir=self._dest_dir)
            if err:
                self.failed.emit(str(err))
            else:
                self.finished_ok.emit(info)

    class _Bridge(QObject):
        ok = Signal(object)
        err = Signal(str)
        prog = Signal(int, int)

    app = QApplication.instance()
    worker = _Worker(src, fps, ffmpeg, parent, dest_dir=dest_dir)
    bridge = _Bridge()
    if app is not None:
        try:
            bridge.moveToThread(app.thread())
        except Exception:
            _LOG.exception("move gif import bridge to GUI thread")
    queued = Qt.ConnectionType.QueuedConnection
    worker.finished_ok.connect(bridge.ok, queued)
    worker.failed.connect(bridge.err, queued)
    worker.progress.connect(bridge.prog, queued)
    if callable(on_done):
        bridge.ok.connect(lambda info: on_done(info, None))
        bridge.err.connect(lambda msg: on_done(None, msg))
    if callable(on_progress):
        bridge.prog.connect(on_progress)

    def _drop(_w=worker, _b=bridge):
        try:
            _ANIM_WORKERS.remove(_w)
        except ValueError:
            pass
        try:
            _ANIM_BRIDGES.remove(_b)
        except ValueError:
            pass

    worker.finished.connect(_drop)
    _ANIM_WORKERS.append(worker)
    _ANIM_BRIDGES.append(bridge)
    worker.start()
    return worker


def report_animated_import_error(host, err: str) -> None:
    """Status bar always; at most one dialog. Never swallow."""
    msg = str(err or "导入动画失败")
    _LOG.error("%s", msg)
    if host is None:
        return
    bar_fn = getattr(host, "statusBar", None)
    if callable(bar_fn):
        try:
            bar = bar_fn()
            show = getattr(bar, "showMessage", None)
            if callable(show):
                show(msg, 5000)
        except Exception:
            _LOG.exception("statusBar.showMessage failed")
    warn = getattr(host, "warn_animated", None)
    if callable(warn):
        try:
            warn(msg)
        except Exception:
            _LOG.exception("warn_animated failed")
        return
    try:
        from PySide6.QtWidgets import QMessageBox, QWidget
        if isinstance(host, QWidget):
            QMessageBox.warning(host, "导入动画", msg)
    except Exception:
        _LOG.exception("QMessageBox.warning failed")
