# -*- coding: utf-8 -*-
"""T04f-0 raster tests. Parent is Qt-free; child process does the painting."""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACK = os.path.join(REPO, "resources", "stylepacks", "glass-white")


def _child_env():
    env = dict(os.environ)
    for key in list(env):
        if key.startswith("PYTEST") or key == "CUTORA_PYTEST_ISOLATED":
            env.pop(key, None)
    env["PYTHONPATH"] = REPO + os.pathsep + env.get("PYTHONPATH", "")
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["PYTHONUNBUFFERED"] = "1"
    env["CUTORA_USER_DATA"] = os.path.join(
        os.environ.get("TEMP") or os.environ.get("TMP") or ".",
        "cutora_t04f_raster_ud")
    os.makedirs(env["CUTORA_USER_DATA"], exist_ok=True)
    ffmpeg = os.path.join(REPO, "runtime", "ffmpeg")
    path = env.get("PATH", "")
    if os.path.normcase(ffmpeg) not in os.path.normcase(path):
        env["PATH"] = ffmpeg + os.pathsep + path
    return env


def test_textfx_raster_child():
    import tempfile
    py = os.path.join(REPO, "runtime", "venv", "Scripts", "python.exe")
    log_dir = tempfile.mkdtemp(prefix="cutora_t04f_raster_")
    log_path = os.path.join(log_dir, "child.log")
    with open(log_path, "w", encoding="utf-8", newline="\n") as fh:
        proc = subprocess.run(
            [py, "-u", os.path.join(REPO, "tests", "test_t04f_textfx.py"),
             "--raster"],
            cwd=REPO,
            env=_child_env(),
            stdout=fh,
            stderr=subprocess.STDOUT,
            timeout=180,
        )
    out = open(log_path, encoding="utf-8", errors="replace").read()
    assert proc.returncode == 0, "rc=%s\n%s" % (proc.returncode, out)
    assert "T04F_RASTER_OK" in out, out


def _event(text="Find its place", t0=0.0, t1=2.0):
    chunk = text.split()
    step = (t1 - t0) / max(1, len(chunk))
    words, t = [], t0
    for w in chunk:
        words.append({"word": w, "start": t, "end": t + step})
        t += step
    return {"text": text, "start": t0, "end": t1, "words": words, "id": "seg0"}


def _style_for(canvas_w, canvas_h, scatter=False, seed=0):
    from cutora.subtitle.stylepack import apply_pack_to_task, load_pack
    pack = load_pack(PACK)
    task = {"timeline": {"width": canvas_w, "height": canvas_h}, "style": {}}
    apply_pack_to_task(task, pack, apply_animation=True)
    style = task["style"]
    fx = dict(style.get("fx") or {})
    fx["anim"] = dict(fx.get("anim") or {})
    fx["anim"]["seed"] = seed
    if scatter:
        fx["scatter"] = {
            "enabled": True, "size_jitter": 0.35, "pos_jitter_em": [1.5, 0.8],
        }
    style["fx"] = fx
    return style


def _roi_hash(style, event, w, h):
    from cutora.render.textfx.layout import layout_event
    from cutora.render.textfx.raster import as_bgra, raster_roi
    from cutora.render.textfx.spec import from_style
    spec = from_style(style)
    lay = layout_event(event, style, spec, w, h)
    img = raster_roi(lay, spec, style, None, w, h, skip_blur=False)
    _owned, bgra = as_bgra(img)
    return hashlib.sha256(bgra.tobytes()).hexdigest(), img, lay


def run_raster_suite() -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    print("T04F_RASTER_BOOT", flush=True)
    from cutora.core.runtime import init_torch_runtime
    init_torch_runtime(render_child=False)
    print("T04F_RASTER_TORCH", flush=True)
    from cutora.app import apply_ui_locale
    apply_ui_locale()
    from PySide6.QtGui import QColor, QImage, QPainter
    from PySide6.QtWidgets import QApplication, QStyleFactory
    from cutora.ui.panels.fonts import load_embedded_fonts
    from cutora.ui.theme.apply import apply_app_qss
    from cutora.ui.theme.fonts_ui import register_ui_fonts, ui_qfont
    print("T04F_RASTER_IMPORTS", flush=True)
    from cutora.render.frame_renderer import FrameRenderer
    from cutora.render.textfx.layout import layout_event
    from cutora.render.textfx.raster import as_bgra, raster_roi
    from cutora.render.textfx.spec import from_style

    app = QApplication.instance() or QApplication(["t04f-raster"])
    load_embedded_fonts()
    register_ui_fonts()
    app.setFont(ui_qfont())
    app.setStyle(QStyleFactory.create("Fusion"))
    apply_app_qss(app)
    print("T04F_RASTER_APP", flush=True)

    style = _style_for(1080, 1920)
    event = _event()
    h1, img1, lay1 = _roi_hash(style, event, 1080, 1920)
    h2, img2, lay2 = _roi_hash(style, event, 1080, 1920)
    assert h1 == h2, (h1, h2)
    assert lay1.origin == lay2.origin
    assert img1.width() == img2.width()

    s0 = _style_for(1080, 1920, scatter=True, seed=0)
    s1 = _style_for(1080, 1920, scatter=True, seed=99)
    hs0, _, _ = _roi_hash(s0, event, 1080, 1920)
    hs1, _, _ = _roi_hash(s1, event, 1080, 1920)
    assert hs0 != hs1, (hs0, hs1)

    style_h = _style_for(1920, 1080)
    _, img_h, lay_h = _roi_hash(style_h, event, 1920, 1080)
    assert img_h.width() * img_h.height() < 1920 * 1080 * 0.5
    assert lay_h.roi_w < 1920
    assert lay_h.roi_h < 1080

    for w, h in ((1080, 1920), (1920, 1080), (1080, 1080)):
        st = _style_for(w, h)
        spec = from_style(st)
        lay = layout_event(event, st, spec, w, h)
        assert 0 <= lay.origin[0] and 0 <= lay.origin[1]
        assert lay.origin[0] + lay.roi_w <= w
        assert lay.origin[1] + lay.roi_h <= h
        assert lay.text_x >= -1 and lay.text_y >= -1
        assert lay.text_x + lay.text_w <= w + 1
        assert lay.text_y + lay.text_h <= h + 1

    w, h = 640, 360
    canvas = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    canvas.fill(QColor("#1E3A5F"))
    painter = QPainter(canvas)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = FrameRenderer.draw_subtitle(
            painter, _event(), _style_for(w, h), 1.0, w, h)
        assert rect.width() > 0 and rect.height() > 0
        v1 = {
            "font_family": "Arimo", "fontsize": 24, "pos_x": 320, "pos_y": 280,
            "color_text": "#F4F7FB", "use_stroke": False, "use_box": False,
        }
        rect2 = FrameRenderer.draw_subtitle(
            painter, _event("Hello"), v1, 1.0, w, h)
        assert rect2 is not None
    finally:
        painter.end()

    backdrop = QImage(w, h, QImage.Format.Format_RGB888)
    backdrop.fill(QColor("#C45C26"))
    stg = _style_for(w, h)
    spec = from_style(stg)
    lay = layout_event(_event(), stg, spec, w, h)
    gimg = raster_roi(lay, spec, stg, backdrop, w, h, skip_blur=False)
    _owned, bgra = as_bgra(gimg)
    assert bgra[..., 2].max() > 40
    assert bgra[..., 3].max() > 0

    stg["fx"]["glow"] = {
        "sigma_em": 0.18, "spread_em": 0.02, "color": "#22D3EE", "strength": 1.0,
    }
    spec = from_style(stg)
    lay = layout_event(_event("WOW"), stg, spec, w, h)
    gimg = raster_roi(lay, spec, stg, None, w, h, skip_blur=False)
    _owned, bgra = as_bgra(gimg)
    alpha = bgra[..., 3].astype("float64")
    xs = alpha[alpha.shape[0] // 2]
    nz = xs[xs > 8]
    if len(nz) > 8:
        assert abs(nz[1:] - nz[:-1]).mean() < 40

    _assert_glass_sample_keeps_inner_colors()
    _assert_glass_left_edge_split()
    _assert_glass_two_backdrops_differ_box_cached()
    _assert_glass_hold_reuses_cache()
    _assert_glass_sigma_matches_request()
    _assert_builtin_cards_have_text_color()
    _assert_pack_centers_painted()
    _assert_long_sentence_stays_in_margin()
    _assert_em_equal_across_canvases()
    _assert_visible_hit_uses_bg_panel()
    _assert_v2_records_word_hits()
    _assert_glass_skips_v1_box()
    _assert_extrude_sides_not_black()
    _assert_dual_font_run()
    _assert_bubble_alternate()
    _assert_neon_uses_gaussian()
    _assert_glass_top_highlight()
    _assert_torn_edge_is_smooth()
    _assert_glass_thickness_zero_matches_flat()
    _assert_glass_thick_bevel_lit_corner()
    _assert_glass_thick_pixels_locked()
    import test_t04f_relayout as _rel
    _rel._assert_overlong_token_stays_in_margin("v2")
    _rel._assert_overlong_token_stays_in_margin("v1")
    _rel._assert_render_no_extra_line("v2")
    _rel._assert_render_no_extra_line("v1")
    _rel._assert_preset_lines_kept()
    _rel._assert_relayout_max_lines(1, expect_eq=True)
    _rel._assert_relayout_max_lines(2, expect_eq=False)
    _rel._assert_single_word_mode()
    _rel._assert_two_rulers_agree()

    print("T04F_RASTER_OK", flush=True)
    os._exit(0)
    return 0


def _stripes_backdrop(w, h):
    from PySide6.QtGui import QColor, QImage, QPainter
    img = QImage(w, h, QImage.Format.Format_RGB888)
    p = QPainter(img)
    p.fillRect(0, 0, 50, h, QColor(255, 0, 0))
    p.fillRect(50, 0, 50, h, QColor(0, 255, 0))
    p.fillRect(100, 0, 50, h, QColor(0, 0, 255))
    p.fillRect(150, 0, w - 150, h, QColor(255, 0, 255))
    p.end()
    return img


def _assert_glass_sample_keeps_inner_colors():
    """§1.1: inner of rect is green|blue, not the outer red|magenta."""
    from PySide6.QtCore import QRectF
    from cutora.render.textfx.raster import as_bgra, glass_sampled_image
    w, h = 200, 100
    bd = _stripes_backdrop(w, h)
    rect = QRectF(60, 10, 80, 80)
    img = glass_sampled_image(
        bd, rect, w, h, sigma=8.0, color="#0B1A33", alpha=0.35)
    assert img is not None and not img.isNull()
    assert abs(img.width() - 80) <= 2, img.width()
    owned, bgra = as_bgra(img)
    row = img.height() // 2
    # local x=8 → video x=68 → green stripe; local x=72 → video x=132 → blue
    lg, lb = int(bgra[row, 8, 1]), int(bgra[row, 8, 0])
    rg, rb = int(bgra[row, 72, 1]), int(bgra[row, 72, 0])
    assert lg > lb + 20, (lg, lb, "left should be green, not outer red")
    assert rb > rg + 20, (rg, rb, "right should be blue, not outer magenta")
    split = 40
    diff = bgra[row, :, 1].astype(int) - bgra[row, :, 0].astype(int)
    cross = next((i for i, d in enumerate(diff) if d < 0), None)
    assert cross is not None
    assert abs(cross - split) <= 2, (cross, split)


def _assert_glass_left_edge_split():
    """§1.1: rect against x=0, actual pad_left < requested; split stays put."""
    from PySide6.QtCore import QRectF
    from cutora.render.textfx.raster import as_bgra, glass_sampled_image
    w, h = 200, 100
    bd = _stripes_backdrop(w, h)
    rect = QRectF(0, 10, 80, 80)
    img = glass_sampled_image(
        bd, rect, w, h, sigma=8.0, color="#0B1A33", alpha=0.35)
    assert img is not None
    owned, bgra = as_bgra(img)
    row = img.height() // 2
    # red (R>G) then green (G>R); split at video x=50 = local 50
    diff = bgra[row, :, 1].astype(int) - bgra[row, :, 2].astype(int)
    cross = next((i for i, d in enumerate(diff) if d > 0), None)
    assert cross is not None
    assert abs(cross - 50) <= 2, (cross, 50)


def _fit_step_sigma(profile, x0_hint):
    """Least-squares erf fit of a blurred unit step. Returns σ in pixels."""
    import math
    import numpy as np
    y = np.asarray(profile, dtype=np.float64)
    span = float(y.max() - y.min())
    assert span > 8.0, span
    y = (y - y.min()) / (span + 1e-9)
    if float(y[4]) > float(y[-5]):
        y = 1.0 - y
    xs = np.arange(len(y), dtype=np.float64)
    idx = int(np.searchsorted(y, 0.5))
    x0 = float(idx) if 0 < idx < len(y) - 1 else float(x0_hint)
    best_s, best_e = 1.0, 1e18
    hi = max(8.0, len(y) / 2.0)
    for s in np.linspace(0.4, hi, 360):
        scale = s * math.sqrt(2.0)
        pred = 0.5 * (1.0 + np.array(
            [math.erf((x - x0) / scale) for x in xs], dtype=np.float64))
        err = float(np.mean((pred - y) ** 2))
        if err < best_e:
            best_e, best_s = err, float(s)
    return best_s


def _assert_glass_sigma_matches_request():
    """§1: effective glass σ within ±10% of glass_sigma_em * em."""
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QColor, QImage, QPainter
    from cutora.render.textfx.raster import as_bgra, glass_sampled_image
    em = 36.0
    w, h = 800, 400
    bd = QImage(w, h, QImage.Format.Format_RGB888)
    p = QPainter(bd)
    p.fillRect(0, 0, w // 2, h, QColor(0, 0, 0))
    p.fillRect(w // 2, 0, w - w // 2, h, QColor(255, 255, 255))
    p.end()
    rect = QRectF(200, 80, 400, 240)
    for gsem in (0.1, 0.3, 0.7, 1.2):
        sigma = gsem * em
        img = glass_sampled_image(
            bd, rect, w, h, sigma, "#0B1A33", 0.35)
        assert img is not None and not img.isNull(), gsem
        _owned, bgra = as_bgra(img)
        row = img.height() // 2
        profile = bgra[row, :, :3].astype("float64").mean(axis=1)
        x0 = img.width() * 0.5
        eff = _fit_step_sigma(profile, x0)
        rel = abs(eff - sigma) / sigma
        assert rel <= 0.10, (gsem, sigma, eff, rel)


def _pack_fill_hex(style):
    """Live fill color: fx.fill.colors[0], then v1 color_text, then cream."""
    s = style if isinstance(style, dict) else {}
    fx = s.get("fx") if isinstance(s.get("fx"), dict) else {}
    fill = fx.get("fill") if isinstance(fx.get("fill"), dict) else {}
    colors = list(fill.get("colors") or [])
    if colors:
        return str(colors[0])
    return str(s.get("color_text") or "#F4F7FB")


def _assert_builtin_cards_have_text_color():
    """Every builtin pack card has pixels near fx.fill.colors[0]."""
    import numpy as np
    from PySide6.QtGui import QColor, QImage
    from cutora.render.textfx.spec import rgb_tuple
    from cutora.subtitle.stylepack import (
        gallery_card_size, list_pack_dirs, load_pack,
    )
    from cutora.ui.panels.style_thumbs import pack_sample, render_pack_card
    base = QImage(1080, 1920, QImage.Format.Format_RGB888)
    base.fill(QColor("#5A6570"))
    tw, th = gallery_card_size(1080, 1920, col_w=180)
    assert abs(tw / float(th) - 5.6) < 0.08
    hits = []
    sizes = set()
    for path in list_pack_dirs():
        if "resources" not in path.replace("\\", "/"):
            continue
        pack = load_pack(path)
        color = _pack_fill_hex(pack.get("style") or {})
        sample = pack_sample(pack)
        pix = render_pack_card(base, pack, sample, tw, th, t=0.4)
        sizes.add((pix.width(), pix.height()))
        assert abs(pix.width() / float(max(1, pix.height())) - 5.6) < 0.08, pack.get("id")
        img = pix.toImage().convertToFormat(QImage.Format.Format_RGB888)
        w, h = img.width(), img.height()
        bpl = img.bytesPerLine()
        ptr = img.bits()
        arr = np.frombuffer(ptr, dtype=np.uint8, count=bpl * h)
        arr = arr.reshape(h, bpl)[:, : w * 3].reshape(h, w, 3).astype(np.float32)
        tr, tg, tb = rgb_tuple(color)
        dist = np.sqrt(
            (arr[..., 0] - tr) ** 2 + (arr[..., 1] - tg) ** 2
            + (arr[..., 2] - tb) ** 2)
        n = int((dist < 60).sum())
        hits.append((pack.get("id"), color, n))
        assert n >= 30, hits[-1]
    assert len(hits) >= 5, hits
    assert len(sizes) == 1, sizes


LONG_SENTENCE = (
    "The children ran through the garden path toward the old wooden "
    "gate beyond the hill and they did not look back at all."
)


def _painted_bbox(img, color, thresh=70):
    import numpy as np
    from PySide6.QtGui import QImage
    from cutora.render.textfx.spec import rgb_tuple
    rgb = img.convertToFormat(QImage.Format.Format_RGB888)
    w, h = rgb.width(), rgb.height()
    bpl = rgb.bytesPerLine()
    ptr = rgb.bits()
    arr = np.frombuffer(ptr, dtype=np.uint8, count=bpl * h)
    arr = arr.reshape(h, bpl)[:, : w * 3].reshape(h, w, 3).astype(np.float32)
    tr, tg, tb = rgb_tuple(color)
    dist = np.sqrt(
        (arr[..., 0] - tr) ** 2 + (arr[..., 1] - tg) ** 2
        + (arr[..., 2] - tb) ** 2)
    ys, xs = np.where(dist < thresh)
    assert len(xs) >= 20, (color, len(xs))
    x0, x1 = int(xs.min()), int(xs.max())
    y0, y1 = int(ys.min()), int(ys.max())
    return x0, y0, x1 - x0 + 1, y1 - y0 + 1, (float(ys.mean()) / float(h))


def _assert_pack_centers_painted():
    """Three canvases: painted subtitle centroid sits at 56%–60% height."""
    from PySide6.QtGui import QColor, QImage, QPainter
    from cutora.render.frame_renderer import FrameRenderer
    from cutora.subtitle.stylepack import apply_pack_to_task, list_pack_dirs, load_pack
    from cutora.ui.panels.style_thumbs import pack_sample
    canvases = ((1080, 1920), (1920, 1080), (1080, 1080))
    for path in list_pack_dirs():
        if "resources" not in path.replace("\\", "/"):
            continue
        pack = load_pack(path)
        color = _pack_fill_hex(pack.get("style") or {})
        sample = pack_sample(pack)
        event = {
            "text": sample, "start": 0.0, "end": 4.0, "words": [
                {"word": sample, "start": 0.0, "end": 4.0}],
        }
        for w, h in canvases:
            task = {"timeline": {"width": w, "height": h}, "style": {}}
            apply_pack_to_task(task, pack, apply_animation=True)
            img = QImage(w, h, QImage.Format.Format_RGB888)
            img.fill(QColor("#3A4654"))
            p = QPainter(img)
            try:
                FrameRenderer.draw_subtitle(
                    p, event, task["style"], 5.0, w, h, backdrop=img)
            finally:
                p.end()
            _x, _y, _bw, _bh, cy = _painted_bbox(img, color)
            assert 0.56 <= cy <= 0.60, (pack.get("id"), w, h, cy)


def _assert_long_sentence_stays_in_margin():
    """Every builtin pack, three canvases, 60+ chars stay inside 8% inset."""
    from PySide6.QtGui import QColor, QImage, QPainter
    from cutora.render.frame_renderer import FrameRenderer
    from cutora.subtitle.stylepack import (
        SAFE_MARGIN, apply_pack_to_task, effective_max_lines, list_pack_dirs,
        load_pack,
    )
    assert len(LONG_SENTENCE) >= 60
    canvases = ((1080, 1920), (1920, 1080), (1080, 1080))
    words = [
        {"word": tok, "start": i * 0.1, "end": i * 0.1 + 0.1}
        for i, tok in enumerate(LONG_SENTENCE.split())
    ]
    for path in list_pack_dirs():
        if "resources" not in path.replace("\\", "/"):
            continue
        pack = load_pack(path)
        color = _pack_fill_hex(pack.get("style") or {})
        for w, h in canvases:
            task = {
                "timeline": {"width": w, "height": h}, "style": {},
                "segments": [{
                    "text": LONG_SENTENCE, "start": 0.0, "end": 6.0,
                    "lang": "en", "words": [dict(x) for x in words],
                }],
            }
            apply_pack_to_task(task, pack, apply_animation=True)
            max_l = effective_max_lines(task["style"], task)
            segs = task.get("segments") or []
            assert segs
            for event in segs:
                img = QImage(w, h, QImage.Format.Format_RGB888)
                img.fill(QColor("#3A4654"))
                p = QPainter(img)
                try:
                    rect = FrameRenderer.draw_subtitle(
                        p, event, task["style"], 8.0, w, h, backdrop=img)
                finally:
                    p.end()
                assert rect is not None and not rect.isEmpty(), pack.get("id")
                hx, hy = float(rect.x()), float(rect.y())
                hw, hh = float(rect.width()), float(rect.height())
                if isinstance((task["style"] or {}).get("fx"), dict):
                    from cutora.render.textfx.layout import layout_event
                    from cutora.render.textfx.spec import from_style
                    lay = layout_event(
                        event, task["style"], from_style(task["style"]), w, h)
                    assert len(lay.lines) <= max_l, (
                        pack.get("id"), [ln.text for ln in lay.lines])
                    x, y, bw, bh = (
                        lay.text_x, lay.text_y, lay.text_w, lay.text_h)
                    mx = w * SAFE_MARGIN
                    my = h * SAFE_MARGIN
                    slack = 8
                    assert x >= mx - slack, (pack.get("id"), w, h, x, mx)
                    assert x + bw <= w - mx + slack, (
                        pack.get("id"), w, h, x, bw, mx)
                    if bh <= h * (1.0 - 2.0 * SAFE_MARGIN) + 1:
                        assert y >= my - slack, (pack.get("id"), w, h, y, my)
                        assert y + bh <= h - my + slack, (
                            pack.get("id"), w, h, y, bh, my)
                else:
                    assert hx >= -1 and hy >= -1
                    lines = event.get("lines") or []
                    if lines:
                        assert len(lines) <= max_l, (pack.get("id"), lines)
                _painted_bbox(img, color, thresh=80)


SIN_RARELY = (
    "Sin rarely destroys a person all at once. It erodes the heart slowly."
)


def _assert_em_equal_across_canvases():
    """T04f-1c: min(w,h) → three canvases share em; landscape block ≤ 35%."""
    from PySide6.QtGui import QColor, QImage, QPainter
    from cutora.render.frame_renderer import FrameRenderer
    from cutora.subtitle.stylepack import (
        apply_pack_to_task, load_pack, resolve_style_geometry,
    )
    pack = load_pack(os.path.join(REPO, "resources", "stylepacks",
                                  "minimal-white"))
    event = {
        "text": SIN_RARELY, "start": 0.0, "end": 8.0, "lang": "en",
        "words": [
            {"word": w, "start": i * 0.1, "end": i * 0.1 + 0.1}
            for i, w in enumerate(SIN_RARELY.split())
        ],
    }
    ems = []
    ratios = []
    canvases = ((1080, 1920), (1920, 1080), (1080, 1080))
    for w, h in canvases:
        task = {"timeline": {"width": w, "height": h}, "style": {}}
        apply_pack_to_task(task, pack, apply_animation=True)
        ems.append(float(resolve_style_geometry(task["style"], w, h)["fontsize"]))
        img = QImage(w, h, QImage.Format.Format_RGB888)
        img.fill(QColor("#3A4654"))
        p = QPainter(img)
        try:
            rect = FrameRenderer.draw_subtitle(
                p, event, task["style"], 4.0, w, h, backdrop=img)
        finally:
            p.end()
        assert rect is not None and not rect.isEmpty(), (w, h)
        color = _pack_fill_hex(task["style"])
        _x, _y, _bw, bh, _cy = _painted_bbox(img, color)
        ratios.append(float(bh) / float(h))
    assert max(ems) - min(ems) <= 2.0, ems
    assert ratios[1] <= 0.35, ratios


def _assert_glass_two_backdrops_differ_box_cached():
    """§1.2: glass ROI changes with backdrop; box does not."""
    from PySide6.QtGui import QColor, QImage, QPainter
    from cutora.render.textfx.cache import ROI_CACHE
    from cutora.render.textfx.pipeline import draw as pdraw
    from cutora.render.textfx import clear_roi_cache
    from cutora.render.textfx.raster import as_bgra
    w, h = 640, 360
    event = _event()
    st = _style_for(w, h)
    red = QImage(w, h, QImage.Format.Format_RGB888)
    red.fill(QColor(200, 30, 30))
    blue = QImage(w, h, QImage.Format.Format_RGB888)
    blue.fill(QColor(30, 30, 200))

    def _hash(style, bg):
        img = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(0)
        p = QPainter(img)
        pdraw(p, event, style, 1.0, w, h, bg)
        p.end()
        _o, arr = as_bgra(img)
        return hashlib.sha256(arr.tobytes()).hexdigest()

    clear_roi_cache()
    h_red = _hash(st, red)
    h_blue = _hash(st, blue)
    assert h_red != h_blue, "glass must resample backdrop each call"
    st_box = dict(st)
    st_box["fx"] = dict(st.get("fx") or {})
    st_box["fx"]["bg"] = dict(st_box["fx"].get("bg") or {})
    st_box["fx"]["bg"]["type"] = "box"
    clear_roi_cache()
    b1 = _hash(st_box, red)
    n_before = len(ROI_CACHE)
    b2 = _hash(st_box, blue)
    assert b1 == b2, "box bg must not depend on backdrop"
    assert len(ROI_CACHE) == n_before, "box+text stay cached on second draw"


def _assert_glass_hold_reuses_cache():
    """Same t + same QImage.cacheKey → glass raster once."""
    from PySide6.QtGui import QColor, QImage, QPainter
    from cutora.render.textfx.pipeline import draw_counters, reset_draw_counters
    from cutora.render.textfx.pipeline import draw as pdraw
    from cutora.render.textfx import clear_roi_cache
    w, h = 640, 360
    event = _event()
    st = _style_for(w, h)
    bg = QImage(w, h, QImage.Format.Format_RGB888)
    bg.fill(QColor(40, 80, 120))
    clear_roi_cache()
    reset_draw_counters()

    def _draw():
        img = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(0)
        p = QPainter(img)
        pdraw(p, event, st, 1.2, w, h, bg)
        p.end()

    _draw()
    first = draw_counters()["bg_raster_calls"]
    _draw()
    second = draw_counters()["bg_raster_calls"]
    assert first == 1, first
    assert second == 1, (first, second)
    bg2 = QImage(w, h, QImage.Format.Format_RGB888)
    bg2.fill(QColor(200, 40, 40))
    img = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(0)
    p = QPainter(img)
    pdraw(p, event, st, 1.2, w, h, bg2)
    p.end()
    assert draw_counters()["bg_raster_calls"] == 2


def _assert_visible_hit_uses_bg_panel():
    """T04f-1d-1: glass/box hit = panel; bg.none hit = text bbox."""
    from cutora.render.textfx.layout import layout_event
    from cutora.render.textfx.spec import from_style
    w, h = 1080, 1920
    event = _event()
    st = _style_for(w, h)
    spec = from_style(st)
    lay = layout_event(event, st, spec, w, h)
    assert spec.bg.type == "glass"
    assert lay.hit_w > lay.text_w + 1, (lay.hit_w, lay.text_w)
    assert lay.hit_h > lay.text_h + 1, (lay.hit_h, lay.text_h)
    st2 = dict(st)
    fx = dict(st2.get("fx") or {})
    bg = dict(fx.get("bg") or {})
    bg["type"] = "none"
    fx["bg"] = bg
    st2["fx"] = fx
    spec2 = from_style(st2)
    lay2 = layout_event(event, st2, spec2, w, h)
    assert spec2.bg.type == "none"
    assert abs(lay2.hit_w - lay2.text_w) < 0.5
    assert abs(lay2.hit_h - lay2.text_h) < 0.5


def _assert_v2_records_word_hits():
    from PySide6.QtGui import QColor, QImage, QPainter
    from cutora.render.frame_renderer import FrameRenderer
    w, h = 640, 360
    img = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(QColor("#1E3A5F"))
    event = _event()
    painter = QPainter(img)
    try:
        FrameRenderer.draw_subtitle(
            painter, event, _style_for(w, h), 1.0, w, h, seg_idx=3)
    finally:
        painter.end()
    from cutora.render.textfx.pipeline import word_hits_for
    hits = word_hits_for(3) or event.get("_adv_word_hit") or []
    assert len(hits) >= 2, hits
    for hit in hits:
        assert hit["rect"].width() > 0
        assert hit["char_end"] >= hit["char_start"]


def _assert_glass_skips_v1_box():
    """Glass (fx present) does not paint the v1 red feathered box."""
    from PySide6.QtGui import QColor, QImage, QPainter
    from cutora.render.frame_renderer import FrameRenderer
    from cutora.render.textfx.raster import as_bgra
    w, h = 400, 220
    event = _event("Hello")
    v1 = {
        "font_family": "Arimo", "fontsize": 36, "pos_x": 200, "pos_y": 110,
        "color_text": "#FFFFFF", "use_box": True, "box_blur": 28,
        "color_bg": "#FF0000", "opacity_bg": 255, "box_radius": 8,
        "box_padding_x": 22, "box_padding_y": 14, "use_stroke": False,
    }
    v2 = dict(v1)
    v2["fx"] = {
        "fill": {"type": "solid", "colors": ["#FFFFFF"]},
        "bg": {
            "type": "glass", "color": "#0B1A33", "alpha": 0.35,
            "radius_em": 0.4, "pad_em": [0.5, 0.25], "glass_sigma_em": 0.7,
            "glass_border": True,
        },
    }
    c1 = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    c1.fill(QColor("#102030"))
    p = QPainter(c1)
    FrameRenderer.draw_subtitle(p, event, v1, 1.0, w, h)
    p.end()
    c2 = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    c2.fill(QColor("#102030"))
    bd = QImage(w, h, QImage.Format.Format_RGB888)
    bd.fill(QColor("#204060"))
    p = QPainter(c2)
    FrameRenderer.draw_subtitle(p, event, v2, 1.0, w, h, backdrop=bd)
    p.end()
    _, a = as_bgra(c1)
    _, b = as_bgra(c2)
    red1 = int(((a[..., 2] > 180) & (a[..., 1] < 80) & (a[..., 0] < 80)).sum())
    red2 = int(((b[..., 2] > 180) & (b[..., 1] < 80) & (b[..., 0] < 80)).sum())
    assert red1 > 200, red1
    assert red2 < max(20, red1 * 0.05), (red1, red2)


def _assert_extrude_sides_not_black():
    from cutora.render.textfx.layout import layout_event
    from cutora.render.textfx.raster import as_bgra, raster_roi
    from cutora.render.textfx.spec import from_style
    w, h = 640, 360
    st = _style_for(w, h)
    fx = dict(st["fx"])
    fx["bg"] = {"type": "none"}
    fx["shadow"] = {"sigma_em": 0}
    fx["extrude"] = {"depth_em": 0.25, "angle": 135, "color": ""}
    fx["fill"] = {"type": "solid", "colors": ["#F4EBD8"]}
    st["fx"] = fx
    spec = from_style(st)
    lay = layout_event(_event("WOW"), st, spec, w, h)
    img = raster_roi(lay, spec, st, None, w, h)
    _o, bgra = as_bgra(img)
    vis = bgra[..., 3] > 200
    dark = vis & (bgra[..., 0] <= 0x10) & (bgra[..., 1] <= 0x10) & (
        bgra[..., 2] <= 0x10)
    assert vis.any()
    assert int(dark.sum()) == 0, int(dark.sum())


def _assert_dual_font_run():
    from cutora.render.textfx.layout import layout_event
    from cutora.render.textfx.spec import from_style
    w, h = 1080, 1920
    st = _style_for(w, h)
    fx = dict(st["fx"])
    fx["fonts"] = {"body": "Arimo", "emphasis": "Caveat"}
    fx["emphasis"] = {
        "words": ["place"], "color": "#22D3EE", "scale": 1.3, "font": "emphasis",
    }
    st["fx"] = fx
    spec = from_style(st)
    lay = layout_event(_event("Find its place"), st, spec, w, h)
    fams = {}
    for line in lay.lines:
        for run in line.runs:
            fams[run.text.strip()] = run.font_family
    assert "Caveat" in fams.get("place", ""), fams


def _assert_bubble_alternate():
    from cutora.render.textfx.layout import layout_event
    from cutora.render.textfx.spec import from_style
    w, h = 1080, 1920
    st = _style_for(w, h)
    fx = dict(st["fx"])
    fx["bg"] = {
        "type": "bubble", "align": "alternate", "color": "#0B1A33",
        "alpha": 0.7, "pad_em": [0.4, 0.2], "line_gap_em": 0.3,
    }
    st["fx"] = fx
    event = _event("Hi there\nAbsolutely")
    event["lines"] = ["Hi", "Absolutely"]
    spec = from_style(st)
    lay = layout_event(event, st, spec, w, h)
    assert len(lay.lines) >= 2, len(lay.lines)
    l0, l1 = lay.lines[0], lay.lines[1]
    left = min(l0.x, l1.x)
    right = max(l0.x + l0.w, l1.x + l1.w)
    assert abs(l0.x - left) < 1.0, (l0.x, l1.x, l0.w, l1.w)
    assert abs((l1.x + l1.w) - right) < 1.0, (l0.x, l1.x, l0.w, l1.w)


def _assert_neon_uses_gaussian():
    import inspect
    from cutora.render.textfx import raster as raster_mod
    src = inspect.getsource(raster_mod._paint_neon)
    assert "gaussian_channel" in src
    assert "GaussianBlur" in inspect.getsource(raster_mod.gaussian_channel)


def _assert_glass_top_highlight():
    """Top edge of a glass ROI is brighter than the interior (1 px white rim)."""
    import numpy as np
    from PySide6.QtGui import QColor, QImage
    from cutora.render.textfx.layout import layout_event
    from cutora.render.textfx.raster import as_bgra, raster_bg
    from cutora.render.textfx.spec import from_style
    w, h = 640, 360
    bd = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    bd.fill(QColor("#3A3A3A"))
    st = _style_for(w, h)
    fx = dict(st["fx"])
    fx["bg"] = {
        "type": "glass", "color": "#0B1A33", "alpha": 0.35,
        "radius_em": 0.15, "pad_em": [0.45, 0.32],
        "glass_sigma_em": 0.3, "glass_border": True,
        "glass_thickness_em": 0,
    }
    fx["glow"] = {"sigma_em": 0}
    fx["shadow"] = {"sigma_em": 0}
    st["fx"] = fx
    spec = from_style(st)
    lay = layout_event(_event("Place"), st, spec, w, h)
    img = raster_bg(lay, spec, bd, w, h, skip_blur=False)
    assert img is not None and not img.isNull()
    _o, bgra = as_bgra(img)
    a = bgra[..., 3]
    luma = bgra[..., :3].astype(np.float64).mean(axis=2)
    row_a = a.mean(axis=1)
    rows = np.where(row_a > 16)[0]
    assert len(rows) > 10, len(rows)
    y0, y1 = int(rows[0]), int(rows[-1])
    band = max(1, min(3, (y1 - y0) // 16))
    top_mask = a[y0:y0 + band] > 40
    top_mean = float(luma[y0:y0 + band][top_mask].mean()) if top_mask.any() else 0.0
    mid0 = y0 + int(0.35 * (y1 - y0))
    mid1 = y0 + int(0.65 * (y1 - y0))
    int_mask = a[mid0:mid1] > 200
    int_mean = float(luma[mid0:mid1][int_mask].mean()) if int_mask.any() else 0.0
    assert top_mean > int_mean + 12.0, (top_mean, int_mean)


def _assert_glass_thickness_zero_matches_flat():
    """thickness_em=0 is byte-identical to the pre-thick flat path."""
    from PySide6.QtGui import QColor, QImage, QPainter
    from cutora.render.textfx.layout import layout_event
    from cutora.render.textfx.raster import (
        _empty_roi, _paint_glass_flat, as_bgra, raster_bg,
    )
    from cutora.render.textfx.spec import from_style
    w, h = 640, 360
    bd = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    bd.fill(QColor("#C45C26"))
    st = _style_for(w, h)
    fx = dict(st["fx"])
    fx["bg"] = {
        "type": "glass", "color": "#0B1A33", "alpha": 0.35,
        "radius_em": 0.4, "pad_em": [0.5, 0.3],
        "glass_sigma_em": 0.5, "glass_border": True,
        "glass_thickness_em": 0,
    }
    fx["glow"] = {"sigma_em": 0}
    fx["shadow"] = {"sigma_em": 0}
    st["fx"] = fx
    spec = from_style(st)
    assert spec.bg.glass_thickness_em == 0
    lay = layout_event(_event("Place"), st, spec, w, h)
    via_api = raster_bg(lay, spec, bd, w, h, skip_blur=False)
    flat = _empty_roi(lay)
    p = QPainter(flat)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    _paint_glass_flat(p, lay, spec, bd, w, h, False)
    p.end()
    _a, aa = as_bgra(via_api)
    _b, bb = as_bgra(flat)
    assert aa.tobytes() == bb.tobytes()


def _assert_glass_thick_bevel_lit_corner():
    """Default thick glass: top-left bevel brighter than bottom-right."""
    import numpy as np
    from PySide6.QtGui import QColor, QImage
    from cutora.render.textfx.layout import layout_event
    from cutora.render.textfx.raster import as_bgra, raster_bg
    from cutora.render.textfx.spec import from_style
    w, h = 640, 360
    bd = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    bd.fill(QColor("#808080"))
    st = _style_for(w, h)
    st["fontsize"] = 72
    st["fontsize_pct"] = 72.0 / float(min(w, h))
    fx = dict(st["fx"])
    fx["bg"] = {
        "type": "glass", "color": "#0B1A33", "alpha": 0.35,
        "radius_em": 0.2, "pad_em": [0.5, 0.35],
        "glass_sigma_em": 0.4, "glass_border": True,
        "glass_thickness_em": 0.14, "glass_refraction": 0.55,
        "glass_bevel": 0.7, "glass_sheen": 0.45, "glass_light_deg": 315,
    }
    fx["glow"] = {"sigma_em": 0}
    fx["shadow"] = {"sigma_em": 0}
    st["fx"] = fx
    spec = from_style(st)
    lay = layout_event(_event("Place"), st, spec, w, h)
    img = raster_bg(lay, spec, bd, w, h, skip_blur=False)
    _o, bgra = as_bgra(img)
    a = bgra[..., 3]
    luma = bgra[..., :3].astype(np.float64).mean(axis=2)
    ys, xs = np.where(a > 200)
    assert len(xs) > 40
    x0, x1 = int(xs.min()), int(xs.max())
    y0, y1 = int(ys.min()), int(ys.max())
    band = max(2, min(4, (y1 - y0) // 25))
    xmid0 = x0 + (x1 - x0) // 4
    xmid1 = x0 + 3 * (x1 - x0) // 4
    top = float(luma[y0:y0 + band, xmid0:xmid1].mean())
    bot = float(luma[y1 - band:y1, xmid0:xmid1].mean())
    assert top > bot + 6.0, (top, bot)


def _thick_lock_fx(pad_em=None):
    bg = {
        "type": "glass", "color": "#0B1A33", "alpha": 0.35,
        "radius_em": 0.2, "pad_em": [0.5, 0.35],
        "glass_sigma_em": 0.4, "glass_border": True,
        "glass_thickness_em": 0.14, "glass_refraction": 0.55,
        "glass_bevel": 0.7, "glass_sheen": 0.45, "glass_light_deg": 315,
    }
    if pad_em is not None:
        bg["pad_em"] = list(pad_em)
    return bg


def _thick_lock_style(w, h, fontsize, pos_y=0.55, pad_em=None):
    st = _style_for(w, h)
    st["fontsize"] = fontsize
    st["fontsize_pct"] = fontsize / float(min(w, h))
    st["pos_x"] = w / 2.0
    st["pos_y"] = h * float(pos_y)
    fx = dict(st["fx"])
    fx["bg"] = _thick_lock_fx(pad_em)
    fx["glow"] = {"sigma_em": 0}
    fx["shadow"] = {"sigma_em": 0}
    st["fx"] = fx
    return st


def _assert_glass_thick_pixels_locked():
    """Three paths: 1:1 small, sample-res preview, large-panel half-res backdrop."""
    _assert_glass_thick_lock_1to1()
    _assert_glass_thick_lock_sample_space()
    _assert_glass_thick_lock_large_panel()


def _assert_glass_thick_lock_1to1():
    """(a) 640×360 1:1 — must stay on the fix3/fix4 baseline bytes."""
    import hashlib
    from PySide6.QtGui import QColor, QImage
    from cutora.render.textfx.layout import layout_event
    from cutora.render.textfx.raster import as_bgra, raster_bg
    from cutora.render.textfx.spec import from_style
    w, h = 640, 360
    bd = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    bd.fill(QColor("#808080"))
    st = _thick_lock_style(w, h, 72)
    spec = from_style(st)
    lay = layout_event(_event("Place"), st, spec, w, h)
    img = raster_bg(lay, spec, bd, w, h, skip_blur=False)
    _o, bgra = as_bgra(img)
    digest = hashlib.sha256(bgra.tobytes()).hexdigest()
    expected = (
        "1ecaddd881425d0ee76ce14eac60511e3f96640529ea933687a6e783a0a82ca0")
    assert digest == expected, digest
    img2 = raster_bg(lay, spec, bd, w, h, skip_blur=False)
    _o2, bgra2 = as_bgra(img2)
    assert bgra2.tobytes() == bgra.tobytes()


def _assert_glass_thick_lock_sample_space():
    """(b) 800×450 backdrop on 1920×1080 canvas — sample-res refract path."""
    import hashlib
    import math
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QImage
    from cutora.render.textfx.glass_thick import rim_sigma_px
    from cutora.render.textfx.layout import layout_event
    from cutora.render.textfx.raster import (
        _glass_rect, as_bgra, glass_blur_pair, raster_bg,
    )
    from cutora.render.textfx.spec import from_style
    w, h = 1920, 1080
    full = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    full.fill(QColor("#808080"))
    bd = full.scaled(
        800, 450,
        Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.SmoothTransformation)
    st = _thick_lock_style(w, h, 72)
    spec = from_style(st)
    lay = layout_event(_event("Place"), st, spec, w, h)
    rect = _glass_rect(lay, spec)
    tw = max(1, int(round(rect.width())))
    th = max(1, int(round(rect.height())))
    sigma = spec.bg.glass_sigma_em * lay.em
    pair = glass_blur_pair(
        bd, rect, w, h, sigma, False,
        sigma_rim=rim_sigma_px(lay.em),
        band_px=int(math.ceil(
            spec.bg.glass_thickness_em * lay.em * 1.5 + 2.0)))
    assert pair is not None
    sh, sw = pair[0].shape[:2]
    assert sw * 2 < tw or sh * 2 < th, (sw, sh, tw, th)
    img = raster_bg(lay, spec, bd, w, h, skip_blur=False)
    _o, bgra = as_bgra(img)
    digest = hashlib.sha256(bgra.tobytes()).hexdigest()
    expected = (
        "54f1103655e8d41aed90afb0ea7ca2fd1ccf99a269cebbefb4e09f3aeed78b34")
    assert digest == expected, digest


def _assert_glass_thick_lock_large_panel():
    """(c) panel > 400k px — backdrop may half-res, shade stays at target."""
    import hashlib
    from PySide6.QtGui import QColor, QImage
    from cutora.render.textfx.glass_thick import GLASS_HALFRES_PIXELS
    from cutora.render.textfx.layout import layout_event
    from cutora.render.textfx.raster import _glass_rect, as_bgra, raster_bg
    from cutora.render.textfx.spec import from_style
    w, h = 1920, 1080
    bd = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    bd.fill(QColor("#808080"))
    st = _thick_lock_style(w, h, 140, pos_y=0.50, pad_em=[0.9, 1.15])
    st["wrap_left"] = 40
    st["wrap_right"] = w - 40
    spec = from_style(st)
    text = "Find its place in the world today now"
    lay = layout_event(_event(text), st, spec, w, h)
    rect = _glass_rect(lay, spec)
    tw = max(1, int(round(rect.width())))
    th = max(1, int(round(rect.height())))
    assert tw * th > GLASS_HALFRES_PIXELS, (tw, th, tw * th)
    img = raster_bg(lay, spec, bd, w, h, skip_blur=False)
    _o, bgra = as_bgra(img)
    digest = hashlib.sha256(bgra.tobytes()).hexdigest()
    expected = (
        "e12b05cabf8d49f44cab27b08574adad73c483e89c611b8f96e36cf2f86f4ea9")
    assert digest == expected, digest


def _assert_torn_edge_is_smooth():
    """Consecutive torn-edge offsets stay correlated (not a sawtooth)."""
    from cutora.render.textfx.paint_bg import torn_offset_series
    em, spacing, n = 80.0, 3.2, 240
    mag = torn_offset_series(n, spacing, em, seed=4)
    deltas = [abs(mag[i] - mag[i - 1]) for i in range(1, n)]
    mean_abs = sum(abs(v) for v in mag) / float(n)
    mean_d = sum(deltas) / float(len(deltas))
    assert mean_abs > 0.5, mean_abs
    assert mean_d < 0.35 * mean_abs, (mean_d, mean_abs)


if __name__ == "__main__":
    if "--raster" in sys.argv:
        try:
            raise SystemExit(run_raster_suite())
        except SystemExit:
            raise
        except BaseException:
            import traceback
            traceback.print_exc()
            raise SystemExit(1)
