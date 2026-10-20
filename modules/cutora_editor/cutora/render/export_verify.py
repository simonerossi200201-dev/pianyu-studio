# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Post-export acceptance: duration, frame count, decode errors, MAD vs preview."""
from __future__ import annotations

import os
import subprocess

from cutora.core.log import get_logger
from cutora.core.proc import popen as _no_window_popen, run as _no_window_run

logger = get_logger("cutora.render.export_verify")

DURATION_TOL_SEC = 0.5
FRAME_TOL = 2
MAD_LIMIT = 0.05
CONTACT_CELL_MAX = 320
FLICKER_VAR_LIMIT = 400.0
# Q80: 3×3 contact sheet is opt-in. Missing key stays off.
SETTING_CONTACT_SHEET = "export.contact_sheet"


def _ffmpeg():
    from cutora.core.config import FFMPEG_PATH
    return FFMPEG_PATH


def _ffprobe():
    from cutora.core.config import FFPROBE_PATH
    return FFPROBE_PATH


def _run(argv, timeout=60):
    return _no_window_run(
        argv, capture_output=True, text=True, timeout=timeout,
        encoding="utf-8", errors="replace")


def probe_duration_and_frames(path: str) -> tuple[float | None, int | None]:
    """Return (duration_sec, nb_frames or None)."""
    if not path or not os.path.isfile(path):
        return None, None
    probe = _ffprobe()
    dur = None
    frames = None
    try:
        import json as _json
        r = _run([
            probe, "-v", "error", "-select_streams", "v:0",
            "-show_entries", "format=duration:stream=nb_frames",
            "-of", "json", path,
        ], timeout=20)
        if r.returncode == 0 and r.stdout:
            data = _json.loads(r.stdout)
            d_raw = str((data.get("format") or {}).get("duration") or "").strip()
            if d_raw and d_raw.upper() != "N/A":
                dur = float(d_raw)
            streams = data.get("streams") or []
            if streams:
                f_raw = str((streams[0] or {}).get("nb_frames") or "").strip()
                if f_raw and f_raw.upper() != "N/A":
                    frames = int(float(f_raw))
    except Exception:
        pass
    if dur is None:
        try:
            r = _run([
                probe, "-v", "error", "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1", path,
            ], timeout=20)
            if r.returncode == 0:
                txt = (r.stdout or "").strip()
                if txt and txt.upper() != "N/A":
                    dur = float(txt)
        except Exception:
            dur = None
    if frames is None:
        try:
            r = _run([
                probe, "-v", "error", "-select_streams", "v:0",
                "-count_frames", "-show_entries", "stream=nb_read_frames,nb_frames",
                "-of", "default=noprint_wrappers=1", path,
            ], timeout=40)
            for line in (r.stdout or "").splitlines():
                if "=" not in line:
                    continue
                k, v = line.split("=", 1)
                v = v.strip()
                if k in ("nb_read_frames", "nb_frames") and v and v.upper() != "N/A":
                    try:
                        frames = int(float(v))
                    except ValueError:
                        continue
                    if k == "nb_read_frames":
                        break
        except Exception:
            frames = None
    return dur, frames


def decode_has_error(path: str, *, fast_spotcheck: bool = False) -> str | None:
    """Full decode (or fast head/tail spotcheck for verified large exports). Return stderr snippet if ffmpeg reported an error."""
    try:
        if fast_spotcheck and os.path.isfile(path) and os.path.getsize(path) > 2 * 1024 * 1024:
            try:
                import cv2
                cap = cv2.VideoCapture(path)
                if cap.isOpened():
                    ok_frames = 0
                    for _ in range(8):
                        ok, frm = cap.read()
                        if ok and frm is not None and frm.size > 0:
                            ok_frames += 1
                        else:
                            break
                    cap.release()
                    if ok_frames >= 8:
                        return None
            except Exception:
                pass
            r = _run([
                _ffmpeg(), "-v", "error", "-threads", "2",
                "-i", path, "-frames:v", "8", "-f", "null", "-",
            ], timeout=30)
        else:
            r = _run([
                _ffmpeg(), "-v", "error", "-threads", "2", "-i", path, "-f", "null", "-",
            ], timeout=120)
    except Exception as e:
        return "decode failed: %s" % e
    err = (r.stderr or "").strip()
    if r.returncode != 0 or err:
        return err or ("ffmpeg exit %s" % r.returncode)
    return None


def _qimage_to_rgb(img):
    import numpy as np
    from PySide6.QtGui import QImage
    conv = img.convertToFormat(QImage.Format.Format_RGB888)
    w, h = conv.width(), conv.height()
    ptr = conv.constBits()
    buf = bytes(ptr)[: h * conv.bytesPerLine()]
    arr = np.frombuffer(buf, dtype=np.uint8).reshape(h, conv.bytesPerLine())
    return arr[:, : w * 3].reshape(h, w, 3).copy()


def _extract_rgb_frame(path: str, t: float, width: int, height: int, fps: float = 30.0):
    import numpy as np
    # Q187：-ss 落在目标帧的前半帧处。以前是 "%.3f" % t：t = 6404/30 = 213.4667 会写成 213.467，比该帧
    # 的 pts（213.46667）晚了 0.3 ms，ffmpeg 就把这一帧丢掉、交出下一帧 —— 刚好撞上画面切换时
    # MAD 0.116 判「失败」，整条视频被串行重导一遍（Luca 42 个里中了 1 个）。
    seek_t = max(0.0, float(t) - 0.5 / max(1.0, float(fps or 30.0)))
    r = _no_window_run(
        [
            _ffmpeg(), "-v", "error", "-threads", "2", "-ss", "%.6f" % seek_t,
            "-i", path, "-frames:v", "1", "-f", "rawvideo",
            "-pix_fmt", "rgb24", "-s", "%dx%d" % (width, height), "-",
        ],
        capture_output=True, timeout=30)
    if r.returncode != 0 or not r.stdout:
        return None
    need = int(width) * int(height) * 3
    if len(r.stdout) < need:
        return None
    return np.frombuffer(r.stdout[:need], dtype=np.uint8).reshape(
        int(height), int(width), 3)


def mad_vs_preview(path, preview_frames, width, height, fps: float = 30.0) -> str | None:
    """preview_frames: list of (t, QImage or ndarray). Fail if MAD > 0.05."""
    if not preview_frames:
        return None
    import numpy as np
    cap = None
    try:
        import cv2
        cap = cv2.VideoCapture(path)
        if not cap.isOpened():
            cap = None
    except Exception:
        cap = None
    try:
        for item in preview_frames:
            try:
                t, ref = item
            except Exception:
                continue
            try:
                if hasattr(ref, "convertToFormat"):
                    ref_rgb = _qimage_to_rgb(ref)
                else:
                    ref_rgb = np.asarray(ref)
            except Exception as e:
                return "MAD compare failed: %s" % e
            got = None
            if cap is not None:
                try:
                    import cv2
                    fidx = max(0, int(round(float(t) * max(1.0, float(fps or 30.0)))))
                    cap.set(cv2.CAP_PROP_POS_FRAMES, fidx)
                    ok_r, bgr = cap.read()
                    if ok_r and bgr is not None:
                        if bgr.shape[1] != int(width) or bgr.shape[0] != int(height):
                            bgr = cv2.resize(bgr, (int(width), int(height)))
                        got_cv = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
                        if ref_rgb.shape[:2] == got_cv.shape[:2]:
                            mad_cv = float(np.mean(np.abs(
                                got_cv.astype(np.float32) - ref_rgb.astype(np.float32)))) / 255.0
                            if mad_cv <= MAD_LIMIT:
                                continue
                except Exception:
                    pass
            got = _extract_rgb_frame(path, float(t), width, height, fps=fps)
            if got is None and float(t) > 0.05:
                got = _extract_rgb_frame(
                    path, max(0.0, float(t) - 0.25), width, height, fps=fps)
            if got is None:
                return "could not extract frame at t=%.2f" % float(t)
            try:
                if ref_rgb.shape[0] != got.shape[0] or ref_rgb.shape[1] != got.shape[1]:
                    continue
                mad = float(np.mean(np.abs(
                    got.astype(np.float32) - ref_rgb.astype(np.float32)))) / 255.0
            except Exception as e:
                return "MAD compare failed: %s" % e
            if mad > MAD_LIMIT:
                return "MAD %.3f > %.2f at t=%.2f" % (mad, MAD_LIMIT, float(t))
        return None
    finally:
        if cap is not None:
            try:
                cap.release()
            except Exception:
                pass


def contact_sheet_enabled(settings=None) -> bool:
    """True only when the user turned on export contact sheets. Default off."""
    if settings is None:
        from cutora.core.history import _load_app_settings
        settings = _load_app_settings() or {}
    return bool((settings or {}).get(SETTING_CONTACT_SHEET, False))


def set_contact_sheet_enabled(on: bool) -> None:
    from cutora.core.history import _load_app_settings, _save_app_settings
    s = _load_app_settings() or {}
    s[SETTING_CONTACT_SHEET] = bool(on)
    _save_app_settings(s)


def verify_export(
    path: str,
    *,
    expected_duration: float | None = None,
    expected_frames: int | None = None,
    fps: float | None = None,
    preview_frames=None,
    width: int | None = None,
    height: int | None = None,
) -> tuple[bool, list[str]]:
    """Return (ok, reasons). Any failing check is a failed export."""
    reasons = []
    if not path or not os.path.isfile(path):
        return False, ["output missing"]

    duration_ok = True
    frames_ok = True
    dur, frames = probe_duration_and_frames(path)
    if expected_duration is not None and expected_duration > 0:
        if dur is None:
            reasons.append("ffprobe duration missing")
            duration_ok = False
        elif abs(dur - float(expected_duration)) > DURATION_TOL_SEC:
            reasons.append("duration %.3fs vs expected %.3fs" % (
                dur, float(expected_duration)))
            duration_ok = False

    expect_n = expected_frames
    if expect_n is None and expected_duration and fps:
        expect_n = max(1, int(float(expected_duration) * float(fps)))
    if expect_n is not None:
        if frames is None:
            reasons.append("ffprobe frame count missing")
            frames_ok = False
        elif abs(int(frames) - int(expect_n)) > FRAME_TOL:
            reasons.append("frames %s vs expected %s" % (frames, expect_n))
            frames_ok = False

    spotcheck = bool(
        duration_ok and frames_ok
        and expected_duration is not None and expected_duration > 0
        and expect_n is not None and expect_n > 0
    )
    dec = decode_has_error(path, fast_spotcheck=spotcheck)
    if dec:
        reasons.append("ffmpeg decode: %s" % dec[:240])

    # MAD is the expensive preview-engine step. Duration + frame count already
    # catch truncated / flower-screen files; skip MAD unless those passed.
    if preview_frames and width and height and duration_ok and frames_ok:
        mad = mad_vs_preview(path, preview_frames, int(width), int(height))
        if mad:
            reasons.append(mad)

    # Sheet is a debug thumbnail. It is not part of the pass/fail decision.
    if contact_sheet_enabled():
        try:
            sheet = write_contact_sheet(
                path, duration=dur, n_frames=frames, fps=fps)
            if not sheet:
                logger.error("export contact sheet missing for %s", path)
        except Exception:
            logger.exception("export contact sheet failed for %s", path)

    return (not reasons), reasons


def contact_sheet_path(path: str) -> str:
    folder = os.path.dirname(os.path.abspath(path or ""))
    stem = os.path.splitext(os.path.basename(path or "export"))[0]
    return os.path.join(folder, "_verify", stem + ".png")


def nine_frame_times(
    duration: float | None,
    n_frames: int | None = None,
    fps: float | None = None,
) -> list[float]:
    """First / middle / last three timestamps (nine total)."""
    dur = max(0.0, float(duration or 0.0))
    n = int(n_frames) if n_frames else 0
    rate = float(fps) if fps else 0.0
    if n > 0 and rate > 0:
        idxs = [
            0, min(1, n - 1), min(2, n - 1),
            max(0, n // 2 - 1), n // 2, min(n - 1, n // 2 + 1),
            max(0, n - 3), max(0, n - 2), max(0, n - 1),
        ]
        last_t = max(0.0, (n - 1) / rate)
        cap = dur - 1e-3 if dur > 0 else last_t
        return [max(0.0, min(cap, i / rate)) for i in idxs]
    if dur <= 0:
        return [0.0] * 9
    step = (1.0 / rate) if rate > 0 else max(dur / 25.0, 0.04)
    mid = dur * 0.5
    end = max(0.0, dur - step)
    return [
        0.0, min(end, step), min(end, 2 * step),
        max(0.0, mid - step), mid, min(end, mid + step),
        max(0.0, end - 2 * step), max(0.0, end - step), end,
    ]


def _probe_wh(path: str) -> tuple[int, int] | None:
    try:
        r = _run([
            _ffprobe(), "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-of", "csv=p=0:s=x", path,
        ], timeout=15)
    except Exception:
        logger.exception("ffprobe size failed for %s", path)
        return None
    txt = (r.stdout or "").strip().splitlines()
    raw = txt[0].strip() if txt else ""
    if "x" not in raw:
        return None
    try:
        w_s, h_s = raw.split("x", 1)
        w, h = int(w_s), int(h_s)
    except ValueError:
        return None
    if w < 2 or h < 2:
        return None
    return w, h


def _cell_wh(src_w: int, src_h: int) -> tuple[int, int]:
    cap = float(CONTACT_CELL_MAX)
    scale = min(1.0, cap / float(max(src_w, src_h)))
    cw = max(2, int(round(src_w * scale)))
    ch = max(2, int(round(src_h * scale)))
    return cw, ch


def write_contact_sheet(
    path: str,
    *,
    duration: float | None = None,
    n_frames: int | None = None,
    fps: float | None = None,
) -> str | None:
    """Extract 9 frames (3+3+3) and tile a 3×3 PNG beside the export."""
    import numpy as np

    if not path or not os.path.isfile(path):
        logger.error("contact sheet skipped: output missing %s", path)
        return None
    wh = _probe_wh(path)
    if wh is None:
        logger.error("contact sheet skipped: cannot probe size %s", path)
        return None
    src_w, src_h = wh
    cw, ch = _cell_wh(src_w, src_h)
    if duration is None or n_frames is None:
        dur, frames = probe_duration_and_frames(path)
        if duration is None:
            duration = dur
        if n_frames is None:
            n_frames = frames
    times = nine_frame_times(duration, n_frames=n_frames, fps=fps)
    cells = []
    for t in times:
        frame = _extract_rgb_frame(path, float(t), cw, ch)
        if frame is None and float(t) > 0.02:
            frame = _extract_rgb_frame(path, max(0.0, float(t) - 0.04), cw, ch)
        if frame is None:
            logger.error("contact sheet: no frame at t=%.3f in %s", t, path)
            frame = np.zeros((ch, cw, 3), dtype=np.uint8)
        cells.append(frame)
    rows = [
        np.concatenate(cells[0:3], axis=1),
        np.concatenate(cells[3:6], axis=1),
        np.concatenate(cells[6:9], axis=1),
    ]
    sheet = np.concatenate(rows, axis=0)
    dest = contact_sheet_path(path)
    parent = os.path.dirname(dest)
    try:
        os.makedirs(parent, exist_ok=True)
    except OSError:
        logger.exception("contact sheet mkdir failed %s", parent)
        return None
    try:
        import cv2
        bgr = cv2.cvtColor(sheet, cv2.COLOR_RGB2BGR)
        ok, buf = cv2.imencode(".png", bgr)
        if not ok:
            logger.error("contact sheet encode failed %s", dest)
            return None
        with open(dest, "wb") as fh:
            fh.write(buf.tobytes())
    except Exception:
        logger.exception("contact sheet write failed %s", dest)
        return None
    return dest


def contact_sheet_flicker_var(png_path: str) -> float:
    """Mean variance of adjacent-cell pixel differences. Flower-screen is high."""
    import numpy as np
    try:
        import cv2
        img = cv2.imread(png_path, cv2.IMREAD_COLOR)
    except Exception:
        logger.exception("flicker var: cannot read %s", png_path)
        return 0.0
    if img is None:
        logger.error("flicker var: empty image %s", png_path)
        return 0.0
    h, w = img.shape[:2]
    if w < 3 or h < 3 or w % 3 or h % 3:
        logger.error("flicker var: not 3x3 %s (%sx%s)", png_path, w, h)
        return 0.0
    cw, ch = w // 3, h // 3
    cells = []
    for row in range(3):
        for col in range(3):
            cells.append(img[row * ch:(row + 1) * ch, col * cw:(col + 1) * cw])
    vars_ = []
    for a, b in zip(cells, cells[1:]):
        diff = a.astype(np.float32) - b.astype(np.float32)
        vars_.append(float(np.var(diff)))
    if not vars_:
        return 0.0
    return float(sum(vars_) / len(vars_))
