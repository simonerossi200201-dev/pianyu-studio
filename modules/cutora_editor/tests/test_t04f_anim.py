# -*- coding: utf-8 -*-
"""T04f-4 animation registry, bounds, and ROI edge-clear scan."""
from __future__ import annotations

import hashlib
import os
import subprocess

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = os.path.join(REPO, "runtime", "venv", "Scripts", "python.exe")


def test_anims_table_has_4a_and_4b():
    from cutora.render.textfx.anim import ANIMS, OUT_MIRROR, phase_anim_items
    need = {
        "none", "fade", "float_in_up", "float_in_down", "scale_in",
        "drop_in", "pop_in", "impact_pop", "stomp", "slide",
        "zoom_slow", "float_loop",
        "reveal_wipe", "typewriter", "line_draw", "scroll", "reveal_zoom_blur",
        "flip_clock", "rotate_flip", "stack", "tracking_in", "outline_to_fill",
        "karaoke", "highlight", "box_highlight", "colour_highlight",
        "rotate_highlight", "bullet_reveal",
    }
    assert need <= set(ANIMS), set(ANIMS) ^ need
    assert "slide" in OUT_MIRROR and "drop_in" in OUT_MIRROR
    assert "reveal_wipe" in OUT_MIRROR
    in_vals = {v for _, v in phase_anim_items("in")}
    loop_vals = {v for _, v in phase_anim_items("loop")}
    out_vals = {v for _, v in phase_anim_items("out")}
    assert "karaoke" not in in_vals
    # 4a line 26 was "unimplemented stay out of the dropdown", not a
    # decision that scroll is an entrance. Appendix H: scroll is loop.
    assert "scroll" in loop_vals
    assert "reveal_wipe" in out_vals
    assert "typewriter" in in_vals
    assert "line_draw" in in_vals
    assert "reveal_zoom_blur" in in_vals
    assert "reveal_zoom_blur" not in out_vals
    assert "typewriter" not in out_vals
    assert "karaoke" in loop_vals
    assert "flip_clock" in in_vals
    assert "tracking_in" in in_vals
    assert "stack" in in_vals
    assert "karaoke" not in in_vals
    assert "slide+left" in in_vals
    assert "zoom_slow" in loop_vals
    assert "stomp" not in out_vals
    assert "impact_pop" not in out_vals


def test_max_transform_bounds_peaks():
    from cutora.render.textfx.spec import from_style
    zero = from_style({}).max_transform_bounds()
    assert zero == (0.0, 0.0, 0.0, 0.0)
    pop = from_style({"fx": {"anim": {"in": "pop_in"}}}).max_transform_bounds()
    impact = from_style({"fx": {"anim": {"in": "impact_pop"}}}).max_transform_bounds()
    stomp = from_style({"fx": {"anim": {"in": "stomp"}}}).max_transform_bounds()
    assert impact[0] > pop[0]
    assert stomp[0] > impact[0]
    slide = from_style({"fx": {"anim": {"in": "slide+left"}}}).max_transform_bounds()
    assert slide[0] >= 1.5
    zb = from_style({"fx": {"anim": {"in": "reveal_zoom_blur"}}}).max_transform_bounds()
    # scale 1.35 extra + 3σ of 0.5 em blur on each side.
    assert zb[0] > 2.5
    assert zb[1] > 1.5
    wipe = from_style({"fx": {"anim": {"in": "reveal_wipe"}}}).max_transform_bounds()
    assert wipe == (0.08, 0.08, 0.08, 0.08)
    scr = from_style({"fx": {"anim": {"loop": "scroll"}}}).max_transform_bounds()
    assert scr == (0.08, 0.08, 0.08, 0.08)
    flip = from_style({"fx": {"anim": {"in": "flip_clock"}}}).max_transform_bounds()
    assert flip[0] >= 0.12
    stack = from_style({"fx": {"anim": {"in": "stack"}}}).max_transform_bounds()
    assert stack[1] >= 2.8
    ch = from_style({"fx": {"anim": {"loop": "colour_highlight"}}}).max_transform_bounds()
    assert ch[0] > pop[0] * 0.4


def test_zoom_slow_and_float_loop_amplitude():
    from cutora.render.textfx.anim import (
        FLOAT_LOOP_EM, ZOOM_SLOW_AMP, _float_loop_fn, _zoom_slow_fn,
    )
    scales = [_zoom_slow_fn(i / 40.0, {}) for i in range(41)]
    lo = min(x.scale_x for x in scales)
    hi = max(x.scale_x for x in scales)
    assert hi - lo <= 0.0300001, (lo, hi)
    assert abs(hi - 1.0) <= ZOOM_SLOW_AMP + 1e-9
    dys = [_float_loop_fn(i / 40.0, {}) for i in range(41)]
    amp = max(abs(x.dy_em) for x in dys)
    assert amp <= FLOAT_LOOP_EM + 1e-9, amp
    peak_to_peak = max(x.dy_em for x in dys) - min(x.dy_em for x in dys)
    assert peak_to_peak <= 2.0 * FLOAT_LOOP_EM + 1e-9, peak_to_peak


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
        "cutora_t04f4b_ud")
    os.makedirs(env["CUTORA_USER_DATA"], exist_ok=True)
    return env


def test_anim_edge_clear_child():
    r = subprocess.run(
        [PY, os.path.join(REPO, "tests", "test_t04f_anim.py"), "--raster"],
        cwd=REPO, env=_child_env(), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=300,
    )
    out = (r.stdout or "") + "\n" + (r.stderr or "")
    assert r.returncode == 0 and "T04F4C_RASTER_OK" in out, out[-4000:]


def _raster_suite() -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from cutora.core.runtime import init_torch_runtime
    init_torch_runtime(render_child=False)
    from cutora.app import apply_ui_locale
    apply_ui_locale()
    from PySide6.QtWidgets import QApplication, QStyleFactory
    from cutora.ui.panels.fonts import load_embedded_fonts
    from cutora.ui.theme.apply import apply_app_qss
    from cutora.ui.theme.fonts_ui import register_ui_fonts, ui_qfont
    from cutora.render.textfx.anim import ANIMS, LOOP_PERIOD_S, Transform
    from cutora.render.textfx.layout import layout_event
    from cutora.render.textfx.raster import (
        as_bgra, raster_roi, raster_text, warp_layer_xf,
    )
    from cutora.render.textfx.spec import from_style

    app = QApplication.instance() or QApplication(["t04f4a"])
    load_embedded_fonts()
    register_ui_fonts()
    app.setFont(ui_qfont())
    app.setStyle(QStyleFactory.create("Fusion"))
    apply_app_qss(app)

    cw, ch = 960, 540
    event = {
        "text": "the office", "start": 0.0, "end": 3.0, "id": "a1",
        "words": [
            {"word": "the", "start": 0.0, "end": 0.4},
            {"word": "office", "start": 0.4, "end": 0.8},
        ],
    }

    def style_for(anim_fx):
        return {
            "fontsize": 48,
            "fontsize_pct": 48.0 / float(min(cw, ch)),
            "wrap_lines": 1,
            "font_family": "Arimo",
            "bold": True,
            "italic": False,
            "pos_x": cw / 2.0,
            "pos_y": ch * 0.55,
            "fx": {
                "bg": {"type": "none"},
                "glow": {"sigma_em": 0},
                "shadow": {"sigma_em": 0},
                "anim": anim_fx,
            },
        }

    def edge_clear(img, margin=1):
        _o, bgra = as_bgra(img)
        a = bgra[..., 3]
        h, w = a.shape[:2]
        m = max(0, int(margin))
        left = a[:, :m]
        right = a[:, w - m:]
        top = a[:m, :]
        bot = a[h - m:, :]
        return (
            int(left.max()) == 0 and int(right.max()) == 0
            and int(top.max()) == 0 and int(bot.max()) == 0
        )

    def sha(img):
        _o, bgra = as_bgra(img)
        return hashlib.sha256(bgra.tobytes()).hexdigest()

    scanned = set()
    for aid, defn in ANIMS.items():
        scanned.add(aid)
        if aid == "slide":
            in_name = "slide+left"
        elif "in" in defn.phases:
            in_name = aid
        else:
            in_name = "none"
        if "loop" in defn.phases and "in" not in defn.phases and aid != "none":
            in_name = "none"
            loop_name = aid
        else:
            loop_name = "none"
        st = style_for({
            "in": in_name, "loop": loop_name, "out": "none",
            "in_s": 1.0, "out_s": 0.25, "easing": "ease_out", "seed": 1,
        })
        if aid in ("line_draw", "outline_to_fill"):
            st["fx"] = dict(st["fx"])
            st["fx"]["stroke"] = {"width_em": 0.08, "color": "#22D3EE"}
        spec = from_style(st)
        lay = layout_event(event, st, spec, cw, ch)
        for k in range(11):
            t = k / 10.0
            img = raster_roi(
                lay, spec, st, None, cw, ch, skip_blur=True, t=t, event=event)
            assert edge_clear(img, 1), (aid, t, lay.roi_w, lay.roi_h)
        if "loop" in defn.phases and aid != "none":
            st_loop = style_for({
                "in": "none", "loop": aid, "out": "none",
                "in_s": 1.0, "out_s": 0.25, "easing": "ease_out", "seed": 1,
            })
            word_color = aid in (
                "karaoke", "highlight", "box_highlight",
                "colour_highlight", "rotate_highlight",
            )
            ev_loop = event
            sample_t = (0.0, 0.25 * LOOP_PERIOD_S, 0.75 * LOOP_PERIOD_S)
            if word_color:
                ev_loop = {
                    "text": "one two three four", "start": 0.0, "end": 2.0,
                    "id": "loop4",
                    "words": [
                        {"word": "one", "start": 0.0, "end": 0.2},
                        {"word": "two", "start": 0.2, "end": 0.4},
                        {"word": "three", "start": 0.4, "end": 0.6},
                        {"word": "four", "start": 0.6, "end": 0.8},
                    ],
                }
                sample_t = (0.10, 0.30, 0.50)
            spec_loop = from_style(st_loop)
            lay_loop = layout_event(ev_loop, st_loop, spec_loop, cw, ch)
            frames = {}
            for t in sample_t:
                img = raster_roi(
                    lay_loop, spec_loop, st_loop, None, cw, ch,
                    skip_blur=True, t=t, event=ev_loop)
                frames[t] = sha(img)
            vals = list(frames.values())
            assert vals[1] != vals[0], aid
            assert vals[2] != vals[0], aid
            assert vals[1] != vals[2], aid
            if aid == "zoom_slow":
                def ink_width(u):
                    img = raster_roi(
                        lay_loop, spec_loop, st_loop, None, cw, ch,
                        skip_blur=True, t=u * LOOP_PERIOD_S, event=event)
                    _o, bgra = as_bgra(img)
                    a = bgra[..., 3]
                    cols = (a > 8).any(axis=0)
                    xs = cols.nonzero()[0]
                    return int(xs[-1] - xs[0] + 1) if len(xs) else 0
                w_peak = ink_width(0.25)
                w_zero = ink_width(0.5)
                w_valley = ink_width(0.75)
                swing = (w_peak - w_valley) / float(w_zero) if w_zero else 0.0
                print("ZOOM_SLOW_W", w_peak, w_zero, w_valley, "swing", round(swing, 4), flush=True)
                assert w_peak > w_valley, (w_peak, w_valley)
                assert abs(swing - 0.030) < 0.012, (w_peak, w_zero, w_valley, swing)
    assert scanned == set(ANIMS), set(ANIMS) - scanned

    st0 = style_for({"in": "none", "loop": "none", "out": "none"})
    spec0 = from_style(st0)
    lay0 = layout_event(event, st0, spec0, cw, ch)
    base = raster_roi(lay0, spec0, st0, None, cw, ch, skip_blur=True, t=0.0, event=event)
    ident = warp_layer_xf(base, lay0, Transform())
    assert sha(ident) == sha(base)
    scaled = warp_layer_xf(base, lay0, Transform(scale_x=1.2, scale_y=1.2))
    moved = warp_layer_xf(base, lay0, Transform(dx_em=0.4))
    spun = warp_layer_xf(base, lay0, Transform(rot_deg=9.0))
    faded = warp_layer_xf(base, lay0, Transform(alpha=0.4))
    hb = sha(base)
    assert sha(scaled) != hb
    assert sha(moved) != hb
    assert sha(spun) != hb
    assert sha(faded) != hb

    # §0.2 clip + clip_dir actually change pixels.
    c_lo = warp_layer_xf(base, lay0, Transform(clip=0.30, clip_dir="ltr"))
    c_hi = warp_layer_xf(base, lay0, Transform(clip=0.70, clip_dir="ltr"))
    c_rtl = warp_layer_xf(base, lay0, Transform(clip=0.50, clip_dir="rtl"))
    c_ltr = warp_layer_xf(base, lay0, Transform(clip=0.50, clip_dir="ltr"))
    assert sha(c_lo) != hb
    assert sha(c_hi) != hb
    assert sha(c_lo) != sha(c_hi)
    assert sha(c_ltr) != sha(c_rtl)

    # §0.2 blur_sigma_em actually changes pixels (ROI Gaussian, not glyph).
    b_hi = warp_layer_xf(base, lay0, Transform(blur_sigma_em=0.40))
    b_lo = warp_layer_xf(base, lay0, Transform(blur_sigma_em=0.10))
    assert sha(b_hi) != hb
    assert sha(b_lo) != hb
    assert sha(b_hi) != sha(b_lo)

    # line_draw without stroke == none.
    st_ld0 = style_for({
        "in": "line_draw", "loop": "none", "out": "none",
        "in_s": 1.0, "out_s": 0.25, "easing": "ease_out", "seed": 1,
    })
    spec_ld0 = from_style(st_ld0)
    lay_ld0 = layout_event(event, st_ld0, spec_ld0, cw, ch)
    img_ld0 = raster_roi(
        lay_ld0, spec_ld0, st_ld0, None, cw, ch,
        skip_blur=True, t=0.4, event=event)
    img_none = raster_roi(
        lay0, spec0, st0, None, cw, ch, skip_blur=True, t=0.4, event=event)
    assert sha(img_ld0) == sha(img_none)

    # typewriter: ink is monotonic; each new word adds pixels.
    tw_event = {
        "text": "one two three four", "start": 0.0, "end": 3.0, "id": "tw",
        "words": [
            {"word": "one", "start": 0.0, "end": 0.2},
            {"word": "two", "start": 0.2, "end": 0.4},
            {"word": "three", "start": 0.4, "end": 0.6},
            {"word": "four", "start": 0.6, "end": 0.8},
        ],
    }
    st_tw = style_for({
        "in": "typewriter", "loop": "none", "out": "none",
        "in_s": 1.0, "out_s": 0.25, "easing": "linear", "seed": 1,
    })
    spec_tw = from_style(st_tw)
    lay_tw = layout_event(tw_event, st_tw, spec_tw, cw, ch)

    def ink_count(img):
        _o, bgra = as_bgra(img)
        return int((bgra[..., 3] > 8).sum())

    inks = []
    for i in range(9):
        u = i / 8.0
        img = raster_roi(
            lay_tw, spec_tw, st_tw, None, cw, ch,
            skip_blur=True, t=u, event=tw_event)
        inks.append(ink_count(img))
        assert edge_clear(img, 1), ("typewriter", u)
    for a, b in zip(inks, inks[1:]):
        assert b >= a, (inks)
    assert inks[0] == 0
    assert inks[-1] > inks[0]
    assert len(set(inks)) >= 4, inks
    tw_hashes = []
    tw_inks = []
    for u in (0.25, 0.50, 0.75):
        img = raster_roi(
            lay_tw, spec_tw, st_tw, None, cw, ch,
            skip_blur=True, t=u, event=tw_event)
        tw_hashes.append(sha(img))
        tw_inks.append(ink_count(img))
    assert tw_hashes[0] != tw_hashes[1]
    assert tw_hashes[1] != tw_hashes[2]
    assert tw_hashes[0] != tw_hashes[2]
    assert tw_inks[0] < tw_inks[1] < tw_inks[2], tw_inks

    import inspect
    from cutora.render.textfx import raster as raster_mod
    premul_src = inspect.getsource(raster_mod._blur_premul)
    assert "INTER_AREA" in premul_src
    assert "_gauss_ds" in premul_src
    assert "dview[..., :3]" not in premul_src
    assert raster_mod._gauss_ds(48.0, 1000, 1000) == 8
    assert raster_mod._gauss_ds(3.0, 1000, 1000) == 1

    # §0.2 fill_progress / extra_color / tracking_em
    from cutora.render.textfx.word_fx import recolor_premul, tint_run_ltr
    st_fp = style_for({"in": "none", "loop": "none", "out": "none"})
    st_fp["fx"] = dict(st_fp["fx"])
    st_fp["fx"]["stroke"] = {"width_em": 0.08, "color": "#22D3EE"}
    spec_fp = from_style(st_fp)
    lay_fp = layout_event(event, st_fp, spec_fp, cw, ch)
    fp0 = raster_text(
        lay_fp, spec_fp, st_fp, True, t=0.0, xf=Transform(fill_progress=0.0))
    fp1 = raster_text(
        lay_fp, spec_fp, st_fp, True, t=0.0, xf=Transform(fill_progress=0.5))
    fp2 = raster_text(
        lay_fp, spec_fp, st_fp, True, t=0.0, xf=Transform(fill_progress=1.0))
    assert sha(fp0) != sha(fp1)
    assert sha(fp1) != sha(fp2)
    assert sha(fp0) != sha(fp2)
    e1 = warp_layer_xf(base, lay0, Transform(extra_color="#22D3EE"))
    e2 = warp_layer_xf(base, lay0, Transform(extra_color="#7C3AED"))
    assert sha(e1) != hb
    assert sha(e2) != hb
    assert sha(e1) != sha(e2)
    c_a = recolor_premul(base.copy(), "#22D3EE", 1.0)
    c_b = recolor_premul(base.copy(), "#7C3AED", 1.0)
    assert sha(c_a) != sha(c_b)
    assert sha(c_a) != hb
    assert sha(c_b) != hb
    run0 = lay0.lines[0].runs[0]
    p_lo = tint_run_ltr(base.copy(), lay0, run0, "#22D3EE", 0.30, 1.0)
    p_hi = tint_run_ltr(base.copy(), lay0, run0, "#22D3EE", 0.70, 1.0)
    assert sha(p_lo) != sha(p_hi)
    st_tr0 = style_for({"in": "none", "loop": "none", "out": "none"})
    st_tr1 = style_for({"in": "none", "loop": "none", "out": "none"})
    st_tr1["fx"] = dict(st_tr1["fx"])
    st_tr1["fx"]["tracking_em"] = 0.2
    spec_tr0 = from_style(st_tr0)
    spec_tr1 = from_style(st_tr1)
    lay_tr0 = layout_event(event, st_tr0, spec_tr0, cw, ch)
    lay_tr1 = layout_event(event, st_tr1, spec_tr1, cw, ch)
    img_tr0 = raster_roi(lay_tr0, spec_tr0, st_tr0, None, cw, ch, skip_blur=True, t=0.0, event=event)
    img_tr1 = raster_roi(lay_tr1, spec_tr1, st_tr1, None, cw, ch, skip_blur=True, t=0.0, event=event)
    assert sha(img_tr0) != sha(img_tr1)

    # emphasis wins over karaoke colour — pixel lock (T04f-5 §0.1)
    # t=0.2: current karaoke word is "the" (0.0–0.4); "office" is emphasized.
    from cutora.render.textfx.spec import DEFAULT_FILL, rgb_tuple
    from cutora.render.textfx.word_fx import _run_rect
    emph_hex = "#7C3AED"
    st_em = style_for({
        "in": "none", "loop": "karaoke", "out": "none",
        "in_s": 1.0, "out_s": 0.25, "easing": "linear", "seed": 1,
    })
    st_em["fx"] = dict(st_em["fx"])
    st_em["fx"]["emphasis"] = {
        "words": ["office"], "color": emph_hex, "max_per_cue": 0,
    }
    spec_em = from_style(st_em)
    lay_em = layout_event(event, st_em, spec_em, cw, ch)
    t_lock = 0.2
    img_em0 = raster_roi(
        lay_em, spec_em, st_em, None, cw, ch,
        skip_blur=True, t=t_lock, event=event)
    emph_runs = [r for ln in lay_em.lines for r in ln.runs if r.emphasized]
    other_runs = [
        r for ln in lay_em.lines for r in ln.runs
        if str(r.text or "").strip().lower() == "the"]
    assert emph_runs and emph_runs[0].fill_override, emph_runs
    assert other_runs, [r.text for ln in lay_em.lines for r in ln.runs]
    _owned_em, bgra_em = as_bgra(img_em0)

    def _unpremul_mean(run, f0, f1, amin=220):
        x0, y0, x1, y1 = _run_rect(lay_em, run, 1.0)
        xa = x0 + int(round((x1 - x0) * f0))
        xb = x0 + int(round((x1 - x0) * f1))
        xa, xb = max(0, min(xa, xb)), min(bgra_em.shape[1], max(xa, xb))
        y0, y1 = max(0, y0), min(bgra_em.shape[0], y1)
        sl = bgra_em[y0:y1, xa:xb]
        if sl.size == 0:
            return None
        a = sl[..., 3].astype("float32")
        mask = a >= float(amin)
        if int(mask.sum()) < 8:
            return None
        aa = a[mask].clip(1.0)
        b = sl[..., 0].astype("float32")[mask] * 255.0 / aa
        g = sl[..., 1].astype("float32")[mask] * 255.0 / aa
        r = sl[..., 2].astype("float32")[mask] * 255.0 / aa
        return (float(r.mean()), float(g.mean()), float(b.mean()), int(mask.sum()))

    er, eg, eb = rgb_tuple(emph_hex)
    fr, fg, fb = rgb_tuple(DEFAULT_FILL)
    office_px = _unpremul_mean(emph_runs[0], 0.15, 0.85)
    the_left = _unpremul_mean(other_runs[0], 0.05, 0.40)
    the_right = _unpremul_mean(other_runs[0], 0.60, 0.95)
    print("EMPH_PIXEL office", office_px, "want", (er, eg, eb), flush=True)
    print("EMPH_PIXEL the_left", the_left, "karaoke", (er, eg, eb), flush=True)
    print("EMPH_PIXEL the_right", the_right, "fill", (fr, fg, fb), flush=True)
    assert office_px is not None, "no opaque pixels in emphasized run"
    assert abs(office_px[0] - er) <= 20 and abs(office_px[1] - eg) <= 20 and abs(office_px[2] - eb) <= 20, office_px
    img_em_t0 = raster_roi(
        lay_em, spec_em, st_em, None, cw, ch,
        skip_blur=True, t=0.0, event=event)
    assert sha(img_em0) != sha(img_em_t0), "karaoke must still change pixels on the un-emphasized word"
    st_em2 = style_for({
        "in": "none", "loop": "karaoke", "out": "none",
        "in_s": 1.0, "out_s": 0.25, "easing": "linear", "seed": 1,
    })
    spec_em2 = from_style(st_em2)
    lay_em2 = layout_event(event, st_em2, spec_em2, cw, ch)
    img_em1 = raster_roi(
        lay_em2, spec_em2, st_em2, None, cw, ch,
        skip_blur=True, t=t_lock, event=event)
    assert sha(img_em0) != sha(img_em1)
    if the_right is not None:
        assert abs(the_right[0] - fr) <= 25 and abs(the_right[1] - fg) <= 25 and abs(the_right[2] - fb) <= 25, the_right
    if the_left is not None and the_right is not None:
        dist_l = abs(the_left[0] - fr) + abs(the_left[1] - fg) + abs(the_left[2] - fb)
        dist_r = abs(the_right[0] - fr) + abs(the_right[1] - fg) + abs(the_right[2] - fb)
        assert dist_l > dist_r + 20, (the_left, the_right, dist_l, dist_r)
    _ = img_em0
    _ = img_em1

    # tracking_in three canvases: line count holds
    from cutora.subtitle.stylepack import SAFE_MARGIN
    sentence = "Office planning costs money today"
    words2, tt = [], 0.0
    for w in sentence.split():
        words2.append({"word": w, "start": tt, "end": tt + 0.15})
        tt += 0.15
    ev_tr = {"text": sentence, "start": 0.0, "end": 2.0, "words": words2, "id": "tr"}
    for vw, vh in ((1080, 1920), (1920, 1080), (1080, 1080)):
        st_ti = {
            "fontsize": 52,
            "fontsize_pct": 52.0 / float(min(vw, vh)),
            "wrap_lines": 1,
            "font_family": "Arimo",
            "bold": True,
            "pos_x": vw / 2.0,
            "pos_y": vh * 0.72,
            "fx": {
                "bg": {"type": "none"},
                "glow": {"sigma_em": 0},
                "shadow": {"sigma_em": 0},
                "anim": {"in": "tracking_in", "loop": "none", "out": "none",
                         "in_s": 1.0, "easing": "linear"},
            },
        }
        spec_ti = from_style(st_ti)
        lay_ti = layout_event(ev_tr, st_ti, spec_ti, vw, vh)
        assert len(lay_ti.lines) == 1, (vw, vh, [ln.text for ln in lay_ti.lines])
        mx = vw * SAFE_MARGIN
        assert lay_ti.text_x >= mx - 1.0, (vw, lay_ti.text_x, mx)

    # karaoke three word-mids must differ (not last-word saturation)
    kar_event = {
        "text": "one two three four", "start": 0.0, "end": 2.0, "id": "kar",
        "words": [
            {"word": "one", "start": 0.0, "end": 0.2},
            {"word": "two", "start": 0.2, "end": 0.4},
            {"word": "three", "start": 0.4, "end": 0.6},
            {"word": "four", "start": 0.6, "end": 0.8},
        ],
    }
    st_k = style_for({
        "in": "none", "loop": "karaoke", "out": "none",
        "in_s": 1.0, "out_s": 0.25, "easing": "linear", "seed": 1,
    })
    spec_k = from_style(st_k)
    lay_k = layout_event(kar_event, st_k, spec_k, cw, ch)
    kar_h = []
    for t in (0.10, 0.30, 0.50):
        img = raster_roi(
            lay_k, spec_k, st_k, None, cw, ch,
            skip_blur=True, t=t, event=kar_event)
        kar_h.append(sha(img))
        assert edge_clear(img, 1), ("karaoke", t)
    assert kar_h[0] != kar_h[1] != kar_h[2]
    assert kar_h[0] != kar_h[2]

    # outline_to_fill: 0.3 / 0.8 / 1.0 → fill 0 / 0.5 / 1.0
    st_of = style_for({
        "in": "outline_to_fill", "loop": "none", "out": "none",
        "in_s": 1.0, "out_s": 0.25, "easing": "linear", "seed": 1,
    })
    st_of["fx"] = dict(st_of["fx"])
    st_of["fx"]["stroke"] = {"width_em": 0.08, "color": "#22D3EE"}
    spec_of = from_style(st_of)
    lay_of = layout_event(event, st_of, spec_of, cw, ch)
    of_h = []
    of_fp = []
    from cutora.render.textfx.anim import _outline_to_fill_fn
    for u in (0.3, 0.8, 1.0):
        of_fp.append(_outline_to_fill_fn(u, {}).fill_progress)
        img = raster_roi(
            lay_of, spec_of, st_of, None, cw, ch,
            skip_blur=True, t=u, event=event)
        of_h.append(sha(img))
    print("OUTLINE_FP", of_fp, flush=True)
    assert [round(x, 6) for x in of_fp] == [0.0, 0.5, 1.0], of_fp
    assert of_h[0] != of_h[1] != of_h[2]

    # stack: three times while line 2 is still entering must differ
    st_st = style_for({
        "in": "stack", "loop": "none", "out": "none",
        "in_s": 0.8, "out_s": 0.25, "easing": "linear", "seed": 1,
    })
    st_st["wrap_lines"] = 2
    ev_st = {
        "text": "the office next line", "start": 0.0, "end": 4.0, "id": "st",
        "lines": ["the office", "next line"],
        "words": [
            {"word": "the", "start": 0.0, "end": 0.2},
            {"word": "office", "start": 0.2, "end": 0.4},
            {"word": "next", "start": 0.4, "end": 0.6},
            {"word": "line", "start": 0.6, "end": 0.8},
        ],
    }
    spec_st = from_style(st_st)
    lay_st = layout_event(ev_st, st_st, spec_st, cw, ch)
    assert len(lay_st.lines) == 2, [ln.text for ln in lay_st.lines]
    st_h = []
    for t in (0.3, 0.9, 1.4):
        img = raster_roi(
            lay_st, spec_st, st_st, None, cw, ch,
            skip_blur=True, t=t, event=ev_st)
        st_h.append(sha(img))
        assert edge_clear(img, 1), ("stack", t)
    print("STACK_SHA", [h[:8] for h in st_h], flush=True)
    assert st_h[0] != st_h[1] != st_h[2]
    assert st_h[0] != st_h[2]

    st_br = style_for({
        "in": "bullet_reveal", "loop": "none", "out": "none",
        "in_s": 0.3, "out_s": 0.25, "stagger_s": 0.15,
        "easing": "ease_out", "seed": 1, "unit": "line",
    })
    st_br["wrap_lines"] = 2
    spec_br = from_style(st_br)
    lay_br = layout_event(ev_st, st_br, spec_br, cw, ch)
    assert len(lay_br.lines) == 2, [ln.text for ln in lay_br.lines]
    from cutora.render.textfx.raster import as_bgra
    def _ink(img):
        _o, bgra = as_bgra(img)
        return int((bgra[..., 3] > 8).sum())
    img_early = raster_roi(
        lay_br, spec_br, st_br, None, cw, ch,
        skip_blur=True, t=0.05, event=ev_st)
    img_mid = raster_roi(
        lay_br, spec_br, st_br, None, cw, ch,
        skip_blur=True, t=0.20, event=ev_st)
    img_late = raster_roi(
        lay_br, spec_br, st_br, None, cw, ch,
        skip_blur=True, t=0.50, event=ev_st)
    assert edge_clear(img_early, 1) and edge_clear(img_mid, 1) and edge_clear(img_late, 1)
    ink_e, ink_m, ink_l = _ink(img_early), _ink(img_mid), _ink(img_late)
    print("BULLET_INK", ink_e, ink_m, ink_l, flush=True)
    assert ink_e < ink_m < ink_l, (ink_e, ink_m, ink_l)
    assert sha(img_early) != sha(img_late)

    # highlight must recolor the current word to emphasis.color, not clip to white
    from cutora.render.textfx.word_fx import tint_run_solid
    from PySide6.QtGui import QImage, QColor
    probe = QImage(8, 8, QImage.Format.Format_ARGB32_Premultiplied)
    probe.fill(QColor(255, 255, 255, 255))
    from cutora.render.textfx.layout import WordRun
    run = WordRun(
        text="Hi", font_family="Arimo", fontsize_px=20, tracking_em=0.0,
        fill_override="", emphasized=False, word_index=0,
        t_start=0.0, t_end=1.0, x=0.0, y=0.0, w=8.0, h=8.0, baseline=6.0,
    )
    class _Lay:
        origin = (0, 0)
    gold = tint_run_solid(probe, _Lay(), run, "#ffff7f", 1.0)
    _o, gpx = as_bgra(gold)
    # un-premul BGRA of opaque pixel
    b, g, r, a = [int(gpx[4, 4, i]) for i in range(4)]
    print("HIGHLIGHT_PX", r, g, b, a, flush=True)
    assert a == 255 and r == 255 and g == 255 and abs(b - 127) <= 1, (r, g, b, a)

    # box_highlight still draws when the current word is emphasized
    st_bx = style_for({
        "in": "none", "loop": "box_highlight", "out": "none",
        "in_s": 1.0, "out_s": 0.25, "easing": "linear", "seed": 1,
    })
    st_bx["fx"] = dict(st_bx["fx"])
    st_bx["fx"]["emphasis"] = {
        "words": ["office"], "color": "#7C3AED", "max_per_cue": 0,
    }
    spec_bx = from_style(st_bx)
    lay_bx = layout_event(event, st_bx, spec_bx, cw, ch)
    img_bx = raster_roi(
        lay_bx, spec_bx, st_bx, None, cw, ch,
        skip_blur=True, t=0.5, event=event)
    st_bx0 = style_for({
        "in": "none", "loop": "none", "out": "none",
        "in_s": 1.0, "out_s": 0.25, "easing": "linear", "seed": 1,
    })
    st_bx0["fx"] = dict(st_bx0["fx"])
    st_bx0["fx"]["emphasis"] = {
        "words": ["office"], "color": "#7C3AED", "max_per_cue": 0,
    }
    spec_bx0 = from_style(st_bx0)
    lay_bx0 = layout_event(event, st_bx0, spec_bx0, cw, ch)
    img_bx0 = raster_roi(
        lay_bx0, spec_bx0, st_bx0, None, cw, ch,
        skip_blur=True, t=0.5, event=event)
    assert sha(img_bx) != sha(img_bx0)
    emph_bx = [r for ln in lay_bx.lines for r in ln.runs if r.emphasized]
    assert emph_bx and emph_bx[0].fill_override

    # flip_clock sample values must differ (avoid 4a/4b zero-cross)
    from cutora.render.textfx.anim import _flip_clock_fn
    sy = [_flip_clock_fn(u, {}).scale_y for u in (0.0, 0.35, 0.85)]
    print("FLIP_CLOCK_SY", sy, flush=True)
    assert sy[0] != sy[1] != sy[2]
    assert sy[0] < 0 and sy[2] > 0

    # extrude side RGB (T04f-2 debt)
    from cutora.render.textfx.ramps import darken_for_extrude, mix_hex
    from cutora.render.textfx.spec import rgb_tuple
    face = "#F4EBD8"
    side = darken_for_extrude(face)
    far = mix_hex(side, "#141A26", 0.15)
    print("EXTRUDE_RGB face", rgb_tuple(face), "side", rgb_tuple(side),
          "far", rgb_tuple(far), "far_mix #141A26 0.15", flush=True)
    st_ex = style_for({"in": "none", "loop": "none", "out": "none"})
    st_ex["fx"] = dict(st_ex["fx"])
    st_ex["fx"]["extrude"] = {"depth_em": 0.20, "angle": 135, "color": ""}
    spec_ex = from_style(st_ex)
    lay_ex = layout_event(event, st_ex, spec_ex, cw, ch)
    img_ex = raster_roi(lay_ex, spec_ex, st_ex, None, cw, ch, skip_blur=True, t=0.0, event=event)
    _o, bgra = as_bgra(img_ex)
    a = bgra[..., 3]
    ys, xs = (a > 200).nonzero()
    assert len(xs) > 0
    # BGRA: face is bright; side is darker but not near-black
    face_px = bgra[ys, xs][:, :3].astype("float32")
    mean_face = face_px.mean(axis=0)
    print("EXTRUDE_SAMPLE_BGR_mean", [round(float(x), 1) for x in mean_face], flush=True)
    # far mix must not be pure black
    fr, fg, fb = rgb_tuple(far)
    assert max(fr, fg, fb) > 0x10, (fr, fg, fb)

    print("T04F4C_RASTER_OK", flush=True)
    return 0


if __name__ == "__main__":
    import sys
    if "--raster" in sys.argv:
        raise SystemExit(_raster_suite())
    raise SystemExit(0)
