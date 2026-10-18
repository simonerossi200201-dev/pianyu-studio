# -*- coding: utf-8 -*-
"""Q53: timeline clips follow the theme — no hardcoded cold fills, labels never pure white.

Q169 改成实色色块：片段身体 = 这一类轨道的颜色（视频蓝 / 图片紫 / 文字赭黄 / 音频绿 / 字幕青绿），
边缘是同色更浅的一圈（斜面感），不再是「深色身体 + 内发光」。下面两条设计断言跟着改。"""
from __future__ import annotations

import os

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Pre-Q46 / pre-Q53 hardcoded clip fills. Must not appear as pixels.
_COLD_FILLS = (
    (0x49, 0x39, 0x55),
    (0x3A, 0x5A, 0x3A),
    (0x2F, 0x3B, 0x4A),
)

CLIP_START = 0.5
CLIP_END = 6.5
PPS = 40.0


def _qapp():
    from PySide6.QtWidgets import QApplication, QStyleFactory
    app = QApplication.instance() or QApplication(["q53-clip-colors"])
    app.setStyle(QStyleFactory.create("Fusion"))
    return app


def _apply_theme(theme_id):
    from cutora.ui.theme.apply import apply_theme
    apply_theme(theme_id)


def _make_timeline():
    from cutora.model.timeline import Timeline
    from cutora.model.timeline_model import Clip, Track
    from cutora.ui.timeline.widget import TimelineWidget

    model = Timeline(1080, 1920, 30)
    video = Track("video")
    video.clips.append(Clip("s1", 0.0, CLIP_END - CLIP_START, CLIP_START))
    text = Track("text")
    subs = Track("subs")
    audio = Track("audio")
    audio.clips.append(Clip("s1", 0.0, CLIP_END - CLIP_START, CLIP_START))
    model.tracks.extend([video, text, subs, audio])

    w = TimelineWidget()
    w.resize(720, 300)
    w.pixels_per_second = PPS
    w.scroll_x = 0
    w.scroll_y = 0
    w.duration = 12000
    w.position = 0
    w.task_data = {
        "timeline": model,
        "segments": [
            {"start": CLIP_START, "end": CLIP_END, "text": "Hello"},
        ],
        "text_overlays": [
            {
                "start": CLIP_START,
                "end": CLIP_END,
                "content": "Caption",
                "_track_id": text.id,
            },
        ],
    }
    return w


def _bgra(img):
    from PySide6.QtGui import QImage
    conv = img.convertToFormat(QImage.Format.Format_ARGB32)
    w, h = conv.width(), conv.height()
    bpl = conv.bytesPerLine()
    raw = bytes(conv.constBits())
    arr = np.frombuffer(raw, dtype=np.uint8).reshape((h, bpl))[:, : w * 4].copy()
    return arr.reshape((h, w, 4))


def _rgb_to_hsv(r, g, b):
    r, g, b = r / 255.0, g / 255.0, b / 255.0
    mx, mn = max(r, g, b), min(r, g, b)
    v = mx
    d = mx - mn
    s = 0.0 if mx == 0 else d / mx
    if d < 1e-9:
        h = 0.0
    elif mx == r:
        h = (60.0 * ((g - b) / d) + 360.0) % 360.0
    elif mx == g:
        h = (60.0 * ((b - r) / d) + 120.0) % 360.0
    else:
        h = (60.0 * ((r - g) / d) + 240.0) % 360.0
    return h, s, v


def _hue_dist(a, b):
    d = abs(float(a) - float(b)) % 360.0
    return min(d, 360.0 - d)


def _hex_hsv(hex_color):
    from cutora.ui.theme.tokens import hex_rgb
    r, g, b = hex_rgb(hex_color)
    return _rgb_to_hsv(r, g, b)


def _count_rgb(view, rgb, slop=3):
    tr, tg, tb = rgb
    r = view[..., 2].astype(np.int16)
    g = view[..., 1].astype(np.int16)
    b = view[..., 0].astype(np.int16)
    return int(
        (
            (np.abs(r - tr) <= slop)
            & (np.abs(g - tg) <= slop)
            & (np.abs(b - tb) <= slop)
        ).sum()
    )


def _clip_rects(w):
    """Widget-space (x, y, w, h) for every painted clip."""
    rows = w._track_positions().get("rows", [])
    pps = float(w.pixels_per_second)
    sx0 = float(w.scroll_x)
    out = []
    task = w.task_data or {}
    for t_obj, row_y, row_h in rows:
        kind = getattr(t_obj, "type", "")
        if kind in ("video", "audio"):
            for clip in getattr(t_obj, "clips", []) or []:
                x = float(getattr(clip, "start_t", 0.0)) * pps - sx0
                ww = float(getattr(clip, "effective_duration", 0.0) or 0.0) * pps
                if ww <= 0:
                    ww = max(0.0, float(getattr(clip, "out_t", 0.0))
                             - float(getattr(clip, "in_t", 0.0))) * pps
                out.append((kind, x, row_y, ww, row_h))
        elif kind == "text":
            tid = getattr(t_obj, "id", None)
            for ov in task.get("text_overlays") or []:
                if ov.get("_track_id") not in (None, tid) and tid is not None:
                    continue
                x = float(ov.get("start") or 0.0) * pps - sx0
                ww = (float(ov.get("end") or 0.0) - float(ov.get("start") or 0.0)) * pps
                out.append((kind, x, row_y, ww, row_h))
        elif kind in ("subs", "subtitle"):
            for seg in task.get("segments") or []:
                x = float(seg.get("start") or 0.0) * pps - sx0
                ww = (float(seg.get("end") or 0.0) - float(seg.get("start") or 0.0)) * pps
                out.append(("subtitle", x, row_y, ww, row_h))
    return out


def _slice(view, x, y, ww, hh):
    h, w = view.shape[:2]
    x0 = int(max(0, round(x)))
    y0 = int(max(0, round(y)))
    x1 = int(min(w, round(x + ww)))
    y1 = int(min(h, round(y + hh)))
    if x1 <= x0 or y1 <= y0:
        return None
    return view[y0:y1, x0:x1]


def test_q53_clip_colors_follow_theme():
    """Six palettes: clip interiors are that kind's track colour (Q169), old cold fills are gone."""
    from PySide6.QtWidgets import QApplication
    from cutora.ui.theme.tokens import BUILTIN_THEME_IDS, current, hex_rgb

    app = _qapp()
    w = _make_timeline()
    w.show()
    QApplication.processEvents()

    for tid in BUILTIN_THEME_IDS:
        _apply_theme(tid)
        QApplication.processEvents()
        w.update()
        QApplication.processEvents()
        img = w.grab().toImage()
        view = _bgra(img)
        t = current()
        want = {"video": hex_rgb(t.track_video), "audio": hex_rgb(t.track_audio),
                "text": hex_rgb(t.track_text), "subtitle": hex_rgb(t.track_subtitle)}
        rects = _clip_rects(w)
        assert rects, "%s: no clip rects" % tid
        sampled = 0
        for kind, x, y, ww, hh in rects:
            region = _slice(view, x, y, ww, hh)
            assert region is not None, "%s missing %s clip pixels" % (tid, kind)
            for rgb in _COLD_FILLS:
                n = _count_rgb(region, rgb, slop=3)
                assert n < 8, (
                    "%s %s clip still paints cold fill %s (%d px)"
                    % (tid, kind, rgb, n)
                )
            # Inset past the 5px glow so the mean is the body fill.
            inset = _slice(view, x + 10, y + 10, ww - 20, hh - 20)
            if inset is None or inset.size < 16 * 4:
                continue
            mean = inset.reshape(-1, 4).astype(np.float32).mean(axis=0)
            mb, mg, mr = float(mean[0]), float(mean[1]), float(mean[2])
            nr, ng, nb = want[kind]
            dist = ((mr - nr) ** 2 + (mg - ng) ** 2 + (mb - nb) ** 2) ** 0.5
            assert dist < 40, (
                "%s %s clip centre (%.0f,%.0f,%.0f) is not the track colour %s "
                "(dist %.1f)"
                % (tid, kind, mr, mg, mb, want[kind], dist)
            )
            sampled += 1
        assert sampled >= 3, "%s: only %d clip interiors sampled" % (tid, sampled)

    w.close()


def test_q53_inner_glow_present():
    """Text-track clip (Q169): body is track_text; the rim is a lighter tint of the same hue."""
    from PySide6.QtWidgets import QApplication
    from cutora.ui.theme.tokens import current, hex_rgb

    app = _qapp()
    _apply_theme("roma-gold")
    w = _make_timeline()
    w.show()
    QApplication.processEvents()
    w.repaint()
    QApplication.processEvents()
    img = w.grab().toImage()
    view = _bgra(img)
    t = current()
    want_h, want_s, _want_v = _hex_hsv(t.track_text)
    bg = hex_rgb(t.bg1)

    text_rects = [r for r in _clip_rects(w) if r[0] == "text"]
    assert text_rects, "no text-track clip"
    kind, x, y, ww, hh = text_rects[0]
    full = _slice(view, x, y, ww, hh)
    assert full is not None and full.shape[0] >= 20 and full.shape[1] >= 80, full.shape if full is not None else None

    # Skip the left label. Use the right-hand body.
    body = full[:, int(full.shape[1] * 0.35):]
    bh, bw = body.shape[:2]
    ring = 5
    edge_mask = np.zeros((bh, bw), dtype=bool)
    edge_mask[:ring, :] = True
    edge_mask[-ring:, :] = True
    edge_mask[:, :ring] = True
    edge_mask[:, -ring:] = True
    # 最外 1px 是深色描边，不算在「浅边」里
    edge_mask[0, :] = False
    edge_mask[-1, :] = False
    edge_mask[:, 0] = False
    edge_mask[:, -1] = False
    centre_mask = np.zeros((bh, bw), dtype=bool)
    pad = 10
    centre_mask[pad:bh - pad, pad:bw - pad] = True

    edge = body[edge_mask].astype(np.float32)
    centre = body[centre_mask].astype(np.float32)
    assert len(edge) > 20 and len(centre) > 20

    def _sats(pix):
        out = []
        hues = []
        for p in pix:
            h, s, v = _rgb_to_hsv(p[2], p[1], p[0])
            out.append(s)
            hues.append((h, s))
        return np.array(out), hues

    edge_sat, edge_hs = _sats(edge)
    centre_sat, centre_hs = _sats(centre)
    c_s = float(centre_sat.mean())
    assert c_s >= 0.30, "text clip centre sat %.3f: body is no longer a colour block" % c_s
    centre_h = float(np.mean([h for h, _s in centre_hs]))
    assert _hue_dist(centre_h, want_h) < 20, (
        "text clip centre hue %.1f not near track_text hue %.1f (%s)"
        % (centre_h, want_h, t.track_text)
    )
    # 边缘是同色更浅的一圈：亮度高于中心、色相不变
    e_v = float(edge.mean())
    c_v = float(centre.mean())
    assert e_v > c_v + 2.0, "text clip rim (%.1f) is not lighter than the body (%.1f)" % (e_v, c_v)
    glow_hues = [h for h, s in edge_hs if s >= 0.12]
    assert glow_hues, "text clip rim lost its hue"
    assert _hue_dist(float(np.mean(glow_hues)), want_h) < 28

    # 和底色拉得开（色块就是为了一眼分清）
    cmean = centre.mean(axis=0)
    dist = (
        (float(cmean[2]) - bg[0]) ** 2
        + (float(cmean[1]) - bg[1]) ** 2
        + (float(cmean[0]) - bg[2]) ** 2
    ) ** 0.5
    assert dist > 60, "text clip centre (%.0f,%.0f,%.0f) too close to bg1 %s (dist %.1f)" % (
        float(cmean[2]), float(cmean[1]), float(cmean[0]), t.bg1, dist,
    )
    w.close()


def test_q53_no_pure_white_text():
    """Timeline grab must not contain #ffffff — labels use fg0, not pure white."""
    from PySide6.QtWidgets import QApplication

    app = _qapp()
    _apply_theme("roma-gold")
    w = _make_timeline()
    w.show()
    QApplication.processEvents()
    w.repaint()
    QApplication.processEvents()
    view = _bgra(w.grab().toImage())
    n = _count_rgb(view, (255, 255, 255), slop=0)
    w.close()
    assert n == 0, "timeline still has %d pure-white pixels" % n


def test_q53_glow_cached():
    """Same-size clip painted 100 times builds the inner-glow pixmap once."""
    from PySide6.QtWidgets import QApplication
    from cutora.model.timeline import Timeline
    from cutora.model.timeline_model import Clip, Track
    from cutora.ui.timeline.widget import (
        TimelineWidget,
        clip_glow_build_count,
        reset_clip_glow_cache,
    )

    app = _qapp()
    _apply_theme("roma-gold")
    model = Timeline(1080, 1920, 30)
    track = Track("video")
    track.clips.append(Clip("s1", 0.0, 4.0, 0.5))
    model.tracks.append(track)
    w = TimelineWidget()
    w.resize(640, 180)
    w.pixels_per_second = PPS
    w.duration = 8000
    w.task_data = {"timeline": model, "segments": []}
    w.show()
    QApplication.processEvents()

    reset_clip_glow_cache()
    w.repaint()
    QApplication.processEvents()
    first = clip_glow_build_count()
    assert first == 1, "first paint built %d glow pixmaps, want 1" % first
    for _ in range(99):
        w.repaint()
    w.close()
    assert clip_glow_build_count() == 1, (
        "100 same-size paints built %d glow pixmaps" % clip_glow_build_count()
    )
