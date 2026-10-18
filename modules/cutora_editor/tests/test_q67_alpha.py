# -*- coding: utf-8 -*-
"""Q67: GIF timing / interlace / off-thread decode; alpha proxy write-back; premul."""
from __future__ import annotations

import ast
import json
import os
import struct
import time

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from q14_media import (
    _gif_lzw_uncompressed,
    make_circle_png,
    make_prores_4444,
    white_circle_rgba,
    write_rgba_png,
)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FFPROBE = os.path.join(REPO, "runtime", "ffmpeg", "ffprobe.exe")
if not os.path.isfile(FFPROBE):
    FFPROBE = "ffprobe"


def _app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(["q67-alpha"])


def _ud(tmp_path, monkeypatch):
    root = str(tmp_path / "ud")
    os.makedirs(root, exist_ok=True)
    monkeypatch.setenv("CUTORA_USER_DATA", root)
    from cutora.core.paths import refresh_cached_paths
    refresh_cached_paths()
    return root


def _wait_until(pred, timeout=20.0, step=0.05):
    from PySide6.QtWidgets import QApplication
    deadline = time.time() + timeout
    while time.time() < deadline:
        QApplication.processEvents()
        if pred():
            return True
        time.sleep(step)
    QApplication.processEvents()
    return bool(pred())


def _probe_duration(path):
    import subprocess
    r = subprocess.run(
        [FFPROBE, "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", path],
        capture_output=True, text=True, timeout=20,
    )
    txt = (r.stdout or "").strip()
    if not txt or txt.upper() == "N/A":
        return None
    return float(txt)


def _probe_nb_frames(path):
    import subprocess
    r = subprocess.run(
        [FFPROBE, "-v", "error", "-select_streams", "v:0",
         "-count_frames", "-show_entries", "stream=nb_read_frames,nb_frames",
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


def _write_gif(dest, frames, delay_cs=4, interlaced=False, w=None, h=None):
    """Indexed GIF. ``frames`` is a list of (h, w) uint8 index arrays."""
    first = np.asarray(frames[0], dtype=np.uint8)
    h = int(h if h is not None else first.shape[0])
    w = int(w if w is not None else first.shape[1])
    max_idx = 0
    for fr in frames:
        max_idx = max(max_idx, int(np.max(fr)))
    ncolors = max(4, int(max_idx) + 1)
    bits = 1
    while (1 << bits) < ncolors and bits < 8:
        bits += 1
    gct_n = 1 << bits
    gct = bytearray()
    gct.extend(bytes([0, 0, 0]))  # 0 = transparent/black
    for i in range(1, gct_n):
        gct.extend(bytes([(i * 37) % 256, (i * 17) % 256, (i * 53) % 256]))
    out = bytearray()
    out.extend(b"GIF89a")
    out.extend(struct.pack("<HH", w, h))
    out.append(0x80 | (bits - 1))
    out.append(0)
    out.append(0)
    out.extend(bytes(gct))
    for fr in frames:
        packed = 0x01  # transparent color follows; disposal 0
        delay = int(delay_cs) & 0xFFFF
        out.extend(bytes([
            0x21, 0xF9, 0x04, packed,
            delay & 0xFF, (delay >> 8) & 0xFF, 0, 0x00,
        ]))
        out.extend(bytes([0x2C]))
        out.extend(struct.pack("<HHHH", 0, 0, w, h))
        ipacked = 0x40 if interlaced else 0
        out.append(ipacked)
        idx = np.asarray(fr, dtype=np.uint8)
        if interlaced:
            rows = []
            for start, step in ((0, 8), (4, 8), (2, 4), (1, 2)):
                y = start
                while y < h:
                    rows.append(idx[y])
                    y += step
            stored = np.stack(rows, axis=0)
        else:
            stored = idx
        out.extend(_gif_lzw_uncompressed(stored.reshape(-1)))
    out.append(0x3B)
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    with open(dest, "wb") as fh:
        fh.write(out)
    return dest


def test_q67_gif_frame_timing(tmp_path, monkeypatch):
    """25 fps GIF (4cs/frame) must keep that duration, not the old 10 fps."""
    _ud(tmp_path, monkeypatch)
    n = 10
    delay_cs = 4
    frames = []
    for i in range(n):
        fr = np.zeros((8, 8), dtype=np.uint8)
        fr[:, :] = 1 + (i % 3)
        frames.append(fr)
    gif = _write_gif(str(tmp_path / "t25.gif"), frames, delay_cs=delay_cs)
    from cutora.media.gifdecode import decode_gif_rgba
    decoded, delays = decode_gif_rgba(gif)
    assert len(decoded) == n, len(decoded)
    assert delays == [delay_cs] * n, delays
    from cutora.core.config import FFMPEG_PATH
    from cutora.media.animated import import_animated_as_clip
    clip = import_animated_as_clip(gif, ffmpeg=FFMPEG_PATH)
    assert os.path.isfile(clip["path"])
    dur = _probe_duration(clip["path"])
    assert dur is not None
    expect = n * (delay_cs / 100.0)
    ten_fps = n / 10.0
    fps_got = n / max(dur, 1e-6)
    assert abs(dur - ten_fps) > 0.25, (
        "imported GIF still looks like 10fps: duration=%s (25fps would be %s)"
        % (dur, expect))
    assert abs(dur - expect) < 0.12 or abs(fps_got - 25.0) < 4.0, (
        "duration=%s expected ~%s (25fps); fps_got=%s" % (dur, expect, fps_got))
    nb = _probe_nb_frames(clip["path"])
    assert nb == n, "frame count %s != %s" % (nb, n)


def test_q67_interlaced_gif(tmp_path):
    """After deinterlace, row 1 and row 2 are adjacent original rows."""
    h, w = 8, 4
    idx = np.zeros((h, w), dtype=np.uint8)
    for y in range(h):
        idx[y, :] = y + 1  # unique palette index per original row
    gif = _write_gif(str(tmp_path / "lace.gif"), [idx], delay_cs=10, interlaced=True)
    from cutora.media.gifdecode import decode_gif_rgba
    frames, _delays = decode_gif_rgba(gif)
    assert len(frames) == 1
    fr = frames[0]
    # Palette index y+1 → red channel (i * 37) % 256 with i = y+1
    def row_id(y):
        r = int(fr[y, 0, 0])
        for i in range(1, 16):
            if (i * 37) % 256 == r:
                return i
        return None

    id1 = row_id(1)
    id2 = row_id(2)
    assert id1 == 2, "decoded row 1 is original row %s (want 2); not deinterlaced?" % id1
    assert id2 == 3, "decoded row 2 is original row %s (want 3)" % id2
    assert id2 == id1 + 1, "row 1 and row 2 are not adjacent original rows (%s, %s)" % (
        id1, id2)


def test_q67_gif_decode_off_main_thread(tmp_path, monkeypatch):
    """A large GIF must not block the UI thread for 200ms+ in one call."""
    _app()
    _ud(tmp_path, monkeypatch)
    h = w = 96
    n = 60
    frames = []
    for i in range(n):
        fr = np.zeros((h, w), dtype=np.uint8)
        fr[:, :] = 1 + (i % 5)
        fr[i % h, :] = 6
        frames.append(fr)
    gif = _write_gif(str(tmp_path / "big.gif"), frames, delay_cs=10)
    from types import SimpleNamespace
    from cutora.model.timeline import Timeline
    from cutora.ui.main_window import MainWindow

    tl = Timeline(720, 1280, 30.0)
    task = {"timeline": tl, "image_overlays": []}

    class Host:
        curr_idx = 0
        tasks = [task]
        _timeline_template = {"width": 720, "height": 1280, "fps": 30.0}
        timeline = SimpleNamespace(
            _last_drop_target_track=None, position=0, duration=5000,
            set_data=lambda *a, **k: None, update=lambda: None,
        )
        video_canvas = SimpleNamespace(update=lambda: None)
        dropped_images = []
        dropped_videos = []

        def push_undo(self):
            return None

        def statusBar(self):
            return SimpleNamespace(showMessage=lambda *a, **k: None)

        def _drop_image_file(self, task, tl, path, start_t, target_track=None):
            self.dropped_images.append(path)

        def _drop_video_file(self, task, tl, path, start_t, target_track=None, loop=False):
            self.dropped_videos.append((path, loop))

        def _drop_audio_file(self, *a, **k):
            return None

    host = Host()
    t0 = time.perf_counter()
    MainWindow._handle_timeline_drop(host, gif, 0.0, "visual")
    blocked = time.perf_counter() - t0
    assert blocked < 0.2, (
        "GIF decode blocked the UI thread for %.0f ms (limit 200)"
        % (blocked * 1000.0))
    assert _wait_until(lambda: bool(host.dropped_videos), timeout=30.0), (
        "async GIF import never dropped a video clip")
    path, loop = host.dropped_videos[0]
    assert loop is True
    assert os.path.isfile(path)


def test_q67_proxy_written_back(tmp_path, monkeypatch):
    """After import, item.json and the timeline MediaSource both get preview_path."""
    from PySide6.QtWidgets import QApplication, QWidget
    _app()
    ud = _ud(tmp_path, monkeypatch)
    monkeypatch.setattr("cutora.media.alpha_proxy.PROXY_THRESHOLD_MS", 0.0)
    png = make_circle_png(str(tmp_path / "disc.png"), w=96, h=96, radius=30, alpha=160)
    mov = make_prores_4444(png, str(tmp_path / "alpha.mov"), seconds=0.25, profile=4)

    from cutora.assets.user import import_user_asset, place_on_timeline
    from cutora.model.timeline import Timeline

    tl = Timeline(96, 96, 30.0)

    class Host(QWidget):
        def __init__(self):
            super().__init__()
            self.tasks = [{"timeline": tl}]
            self._msgs = []

        def statusBar(self):
            host = self
            class _Bar:
                def showMessage(self, msg, timeout=0):
                    host._msgs.append(msg)
            return _Bar()

    host = Host()
    rec = import_user_asset(mov, host=host)
    assert rec.get("alpha") is True
    clip = place_on_timeline(tl, rec, start_t=0.0)
    src = tl.sources[clip.source_id]
    meta = os.path.join(rec["_dir"], "item.json")

    def _ready():
        QApplication.processEvents()
        data = {}
        if os.path.isfile(meta):
            with open(meta, encoding="utf-8") as fh:
                data = json.load(fh)
        preview = data.get("preview_path") or getattr(src, "preview_path", None)
        return bool(preview) and (
            os.path.isfile(preview) or ("%d" in str(preview)))

    assert _wait_until(_ready, timeout=60.0), (
        "proxy never wrote preview_path; item=%s src=%s"
        % (json.load(open(meta, encoding="utf-8")) if os.path.isfile(meta) else None,
           getattr(src, "preview_path", None)))
    with open(meta, encoding="utf-8") as fh:
        data = json.load(fh)
    json_preview = data.get("preview_path")
    assert json_preview, data
    assert os.path.isfile(json_preview) or ("%d" in str(json_preview)), json_preview
    assert getattr(src, "preview_path", None), "MediaSource.preview_path still empty"
    assert os.path.normcase(os.path.abspath(src.preview_path)) == os.path.normcase(
        os.path.abspath(json_preview)) or src.preview_path == json_preview
    # Scaled proxy, not libvpx full-res webm.
    assert not str(src.preview_path).lower().endswith(".webm"), src.preview_path
    from cutora.render.readers import _probe_video_has_alpha
    info = _probe_video_has_alpha(src.preview_path)
    if info and info.get("width") and info.get("height"):
        assert max(int(info["width"]), int(info["height"])) <= 1280, info
    _ = ud


def test_q67_premultiplied_not_doubled(tmp_path, monkeypatch):
    """Already-premultiplied ProRes marked as such must not darken the rim."""
    _app()
    _ud(tmp_path, monkeypatch)
    rgba = white_circle_rgba(w=64, h=64, radius=18.0, alpha=128)
    a = rgba[..., 3].astype(np.float32) / 255.0
    premul = rgba.copy()
    premul[..., :3] = np.clip(
        np.round(premul[..., :3].astype(np.float32) * a[..., None]), 0, 255
    ).astype(np.uint8)
    png = str(tmp_path / "premul.png")
    write_rgba_png(png, premul)
    mov = make_prores_4444(png, str(tmp_path / "premul.mov"), seconds=0.15, profile=4)

    from PySide6.QtCore import QThread
    from PySide6.QtGui import QColor, QImage, QPainter
    from cutora.render.readers import (
        _cv2_frame_to_qimage, _open_video_capture, set_path_alpha_mode,
    )
    from cutora.render.render_worker import RenderWorker

    def _composite(frame):
        qimg = _cv2_frame_to_qimage(frame)
        h, w = frame.shape[:2]
        bg = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
        bg.fill(QColor(255, 0, 0))
        p = QPainter(bg)
        p.drawImage(0, 0, qimg)
        p.end()
        raw = bytes(bg.constBits())
        arr = np.frombuffer(raw, np.uint8).reshape((h, bg.bytesPerLine()))[:, : w * 4]
        return arr.reshape((h, w, 4)).copy()

    def _rim_luma(bgra, alpha):
        ring = (alpha > 40) & (alpha < 200)
        assert int(ring.sum()) > 8, "no antialiased rim"
        pix = bgra[ring][:, :3].astype(np.float64)
        return 0.114 * pix[:, 0] + 0.587 * pix[:, 1] + 0.299 * pix[:, 2]

    def _read(path, mode):
        set_path_alpha_mode(path, mode)
        cap = _open_video_capture(path, hw=False, alpha_mode=mode)
        assert cap is not None
        try:
            ok, frame = cap.read()
        finally:
            cap.release()
        assert ok and frame is not None and frame.shape[2] == 4
        return _composite(frame), frame

    preview, frame = _read(mov, "premultiplied")
    doubled, frame_s = _read(mov, "straight")
    src_a = frame[..., 3]
    luma_p = _rim_luma(preview, src_a)
    luma_d = _rim_luma(doubled, src_a)
    # Marking as 预乘 must skip the second multiply; treating as 直通 darkens the rim.
    assert float(luma_p.mean()) > float(luma_d.mean()) + 8.0, (
        "premul-marked rim luma %.1f is not brighter than straight-marked %.1f "
        "(second premultiply still applied?)" % (float(luma_p.mean()), float(luma_d.mean())))
    assert float(luma_p.mean()) >= 120.0, (
        "preview rim too dark even when marked premul: mean=%s" % float(luma_p.mean()))

    from cutora.assets.user import import_user_asset, place_on_timeline
    from cutora.model.timeline import Timeline
    rec = import_user_asset(mov)
    rec["alpha_mode"] = "premultiplied"
    tl = Timeline(64, 64, 30.0)
    clip = place_on_timeline(tl, rec, start_t=0.0)
    clip.alpha_mode = "premultiplied"
    src = tl.sources[clip.source_id]
    src.alpha_mode = "premultiplied"
    set_path_alpha_mode(src.path, "premultiplied")
    clip.fit_mode = "stretch"
    rw = RenderWorker.__new__(RenderWorker)
    QThread.__init__(rw)
    rw._geom_last_log_t = 0.0
    cap_e = _open_video_capture(src.path, hw=False, alpha_mode="premultiplied")
    try:
        ok_e, exp_frame = cap_e.read()
        assert ok_e and exp_frame is not None
        canvas = QImage(64, 64, QImage.Format.Format_ARGB32_Premultiplied)
        canvas.fill(QColor(255, 0, 0))
        p = QPainter(canvas)
        st = {"last_time": -1.0, "last_frame": None}
        try:
            rw._draw_video_overlay(p, cap_e, clip, 64, 64, 0.0, st)
        finally:
            p.end()
        raw = bytes(canvas.constBits())
        arr = np.frombuffer(raw, np.uint8).reshape(
            (64, canvas.bytesPerLine()))[:, : 64 * 4]
        export_bgra = arr.reshape((64, 64, 4)).copy()
    finally:
        cap_e.release()
    luma_e = _rim_luma(export_bgra, exp_frame[..., 3])
    assert float(luma_e.mean()) > float(luma_d.mean()) + 8.0, (
        "export rim luma %.1f not brighter than doubled %.1f"
        % (float(luma_e.mean()), float(luma_d.mean())))
    mad = float(np.abs(preview.astype(np.int16) - export_bgra.astype(np.int16)).mean())
    assert mad < 12.0, "preview vs export MAD=%.2f" % mad


def _call_name(node):
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def test_q67_no_widget_calls_off_thread():
    """Worker-thread call graph in alpha_proxy.py must not touch Qt widgets."""
    path = os.path.join(REPO, "cutora", "media", "alpha_proxy.py")
    src = open(path, encoding="utf-8").read()
    tree = ast.parse(src, filename="alpha_proxy.py")
    funcs = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            funcs.setdefault(node.name, node)
            # nested _run inside ensure_alpha_proxy
            if node.name == "ensure_alpha_proxy":
                for inner in ast.walk(node):
                    if (isinstance(inner, ast.FunctionDef)
                            and inner.name == "_run"
                            and inner is not node):
                        funcs["_run"] = inner
    starts = [n for n in (
        "_run_build", "_run", "_build_proxy", "_encode_prores_proxy",
        "_encode_qtrle_proxy", "_encode_png_seq", "_encode_vp9",
        "_emit_proxy_result",
    ) if n in funcs]
    assert starts, "no worker functions found in alpha_proxy.py"
    seen = set()
    stack = list(starts)
    while stack:
        name = stack.pop()
        if name in seen or name not in funcs:
            continue
        seen.add(name)
        for n in ast.walk(funcs[name]):
            if isinstance(n, ast.Call):
                called = _call_name(n)
                if called and called in funcs and called not in seen:
                    stack.append(called)
    forbidden_attr = {
        "statusBar", "showMessage", "setText", "setValue", "update",
        "repaint", "setRange",
    }
    forbidden_name = {
        "QMessageBox", "QWidget", "QLabel", "QProgressBar", "QStatusBar",
        "report_proxy_status",
    }
    hits = []
    for name in sorted(seen):
        node = funcs[name]
        for n in ast.walk(node):
            if isinstance(n, ast.Attribute) and n.attr in forbidden_attr:
                hits.append("%s uses .%s" % (name, n.attr))
            if isinstance(n, ast.Name) and n.id in (forbidden_name | forbidden_attr):
                hits.append("%s names %s" % (name, n.id))
            if isinstance(n, ast.Call):
                called = _call_name(n)
                if called in forbidden_name:
                    hits.append("%s calls %s" % (name, called))
    assert not hits, "worker-thread functions touch widgets: %s" % hits
