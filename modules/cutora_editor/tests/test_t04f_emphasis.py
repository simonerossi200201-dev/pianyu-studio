# -*- coding: utf-8 -*-
"""T04f-3 emphasis detect (pure) + line-count hard constraint (Qt child)."""
from __future__ import annotations

import os
import subprocess

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = os.path.join(REPO, "runtime", "venv", "Scripts", "python.exe")


def _spec(**kwargs):
    from cutora.render.textfx.spec import from_style
    fx = {"emphasis": dict(kwargs)}
    return from_style({"fx": fx})


def test_numbers_skips_stopwords():
    from cutora.render.textfx.emphasis import detect_emphasis
    text = "We cut costs at the office by 30% last year"
    hits = detect_emphasis(text, _spec(auto=["numbers"]))
    toks = [h.token for h in hits]
    assert toks == ["30%"], toks
    assert "at" not in toks and "the" not in toks and "by" not in toks


def test_longest_is_office():
    from cutora.render.textfx.emphasis import detect_emphasis
    text = "We cut costs at the office by 30% last year"
    hits = detect_emphasis(text, _spec(auto=["longest"]))
    assert [h.token for h in hits] == ["office"]


def test_caps_before_letter_case():
    from cutora.render.textfx.emphasis import detect_emphasis
    from cutora.render.textfx.spec import from_style
    spec = from_style({"fx": {
        "letter_case": "upper",
        "emphasis": {"auto": ["caps"]},
    }})
    assert spec.letter_case == "upper"
    hits = detect_emphasis("we cut costs at the office", spec)
    assert hits == []


def test_explicit_word_overrides_stopword():
    from cutora.render.textfx.emphasis import detect_emphasis
    hits = detect_emphasis(
        "We cut costs at the office",
        _spec(words=["at"]),
    )
    assert [h.token for h in hits] == ["at"]


def test_max_per_cue_keeps_highest_priority():
    from cutora.render.textfx.emphasis import detect_emphasis
    text = "NASA cut costs at the office by 30% last year"
    hits = detect_emphasis(
        text,
        _spec(auto=["numbers", "caps", "longest"], max_per_cue=2),
    )
    rules = [h.rule for h in hits]
    toks = [h.token for h in hits]
    assert len(hits) == 2, (toks, rules)
    assert "numbers" in rules
    assert "caps" in rules
    assert "longest" not in rules


def test_detect_stable_across_hashseed():
    py = PY
    script = r"""
import os, json
from cutora.render.textfx.spec import from_style
from cutora.render.textfx.emphasis import detect_emphasis
spec = from_style({"fx": {"emphasis": {"auto": ["numbers", "longest"]}}})
hits = detect_emphasis("We cut costs at the office by 30% last year", spec)
print(json.dumps([(h.token, h.rule) for h in hits]))
"""
    env0 = dict(os.environ)
    env0["PYTHONHASHSEED"] = "0"
    env0["PYTHONPATH"] = REPO + os.pathsep + env0.get("PYTHONPATH", "")
    env1 = dict(env0)
    env1["PYTHONHASHSEED"] = "1"
    r0 = subprocess.run(
        [py, "-c", script], cwd=REPO, env=env0, capture_output=True, text=True)
    r1 = subprocess.run(
        [py, "-c", script], cwd=REPO, env=env1, capture_output=True, text=True)
    assert r0.returncode == 0, r0.stderr
    assert r1.returncode == 0, r1.stderr
    assert r0.stdout.strip() == r1.stdout.strip(), (r0.stdout, r1.stdout)


def test_emphasis_command_in_registry():
    from cutora.core.commands import get_registry
    spec = get_registry().get("subtitle.emphasis.auto")
    assert spec.undoable and spec.headless


def test_max_transform_bounds_zero_without_anim():
    from cutora.render.textfx.spec import from_style
    spec = from_style({})
    assert spec.max_transform_bounds() == (0.0, 0.0, 0.0, 0.0)
    spec_pop = from_style({"fx": {"emphasis": {"anim": "pop", "words": ["office"]}}})
    l, t, r, b = spec_pop.max_transform_bounds()
    assert l > 0 and r > 0 and t > 0 and b > 0


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
        "cutora_t04f3_ud")
    os.makedirs(env["CUTORA_USER_DATA"], exist_ok=True)
    return env


def test_line_count_hard_constraint_child():
    """Layout child: line-count cap, pop edge-clear, neighbor tracking lock."""
    r = subprocess.run(
        [PY, os.path.join(REPO, "tests", "test_t04f_emphasis.py"), "--layout"],
        cwd=REPO, env=_child_env(), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=180,
    )
    out = (r.stdout or "") + "\n" + (r.stderr or "")
    assert r.returncode == 0 and "T04F3_LAYOUT_OK" in out, out[-4000:]


def _layout_suite() -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from cutora.core.runtime import init_torch_runtime
    init_torch_runtime(render_child=False)
    from cutora.app import apply_ui_locale
    apply_ui_locale()
    from PySide6.QtWidgets import QApplication, QStyleFactory
    from cutora.ui.panels.fonts import load_embedded_fonts
    from cutora.ui.theme.apply import apply_app_qss
    from cutora.ui.theme.fonts_ui import register_ui_fonts, ui_qfont
    from cutora.render.textfx.layout import layout_event
    from cutora.render.textfx.spec import from_style
    from cutora.subtitle.stylepack import SAFE_MARGIN

    app = QApplication.instance() or QApplication(["t04f3-layout"])
    load_embedded_fonts()
    register_ui_fonts()
    app.setFont(ui_qfont())
    app.setStyle(QStyleFactory.create("Fusion"))
    apply_app_qss(app)

    sentence = "Office planning costs money now"
    words, t = [], 0.0
    for w in sentence.split():
        words.append({"word": w, "start": t, "end": t + 0.15})
        t += 0.15
    event = {
        "text": sentence, "start": 0.0, "end": 2.0, "words": words, "id": "e1",
    }

    canvases = ((1080, 1920), (1920, 1080), (1080, 1080))
    for scatter in (False, True):
        for w, h in canvases:
            st = {
                "fontsize": 52,
                "fontsize_pct": 52.0 / float(min(w, h)),
                "wrap_lines": 1,
                "font_family": "Arimo",
                "bold": True,
                "italic": False,
                "pos_x": w / 2.0,
                "pos_y": h * 0.72,
                "fx": {
                    "bg": {"type": "none"},
                    "glow": {"sigma_em": 0},
                    "shadow": {"sigma_em": 0},
                    "fonts": {"body": "Arimo", "emphasis": "Anton"},
                    "emphasis": {
                        "words": sentence.split(),
                        "scale": 2.0,
                        "font": "emphasis",
                        "color": "#22D3EE",
                        "max_per_cue": 0,
                    },
                },
            }
            if scatter:
                st["fx"]["scatter"] = {
                    "enabled": True, "size_jitter": 0.2,
                    "pos_jitter_em": [0.6, 0.3], "rot_jitter_deg": 4,
                }
            spec = from_style(st)
            lay = layout_event(event, st, spec, w, h)
            assert len(lay.lines) == 1, (scatter, w, h, len(lay.lines),
                                         [ln.text for ln in lay.lines])
            mx = w * SAFE_MARGIN
            my = h * SAFE_MARGIN
            assert lay.text_x + 1 >= mx, (scatter, w, h, lay.text_x, mx)
            assert lay.text_x + lay.text_w <= w - mx + 1, (
                scatter, w, h, lay.text_x, lay.text_w, w)
            assert lay.text_y + 1 >= my, (scatter, w, h, lay.text_y, my)
            assert lay.text_y + lay.text_h <= h - my + 1, (
                scatter, w, h, lay.text_y, lay.text_h, h)

    from cutora.render.textfx.raster import _pop_scale, as_bgra, raster_roi
    from cutora.render.textfx.spec import POP_DUR_S, POP_PEAK
    cw, ch = 960, 540
    st_pop = {
        "fontsize": 72,
        "fontsize_pct": 72.0 / float(min(cw, ch)),
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
            "emphasis": {
                "words": ["office"], "color": "#22D3EE",
                "scale": 1.0, "anim": "pop", "max_per_cue": 0,
            },
        },
    }
    ev_pop = {
        "text": "the office", "start": 0.0, "end": 2.0, "id": "pop1",
        "words": [
            {"word": "the", "start": 0.0, "end": 0.2},
            {"word": "office", "start": 0.2, "end": 0.4},
        ],
    }
    spec_pop = from_style(st_pop)
    lay_pop = layout_event(ev_pop, st_pop, spec_pop, cw, ch)
    assert len(lay_pop.lines) == 1
    runs = lay_pop.lines[0].runs
    assert len(runs) >= 2
    the, office = runs[0], runs[1]
    assert office.emphasized
    t_peak = float(office.t_start) + 0.4 * POP_DUR_S
    s = _pop_scale(office, spec_pop, t_peak)
    assert abs(s - POP_PEAK) < 0.01, s
    from cutora.render.textfx.raster import _run_path
    b_the = _run_path(the, st_pop, spec_pop, t=0.0).boundingRect()
    b_off = _run_path(office, st_pop, spec_pop, t=t_peak).boundingRect()
    assert b_off.left() >= b_the.right() - 0.5, (
        float(b_off.left()), float(b_the.right()), s)

    def _edge_clear(img, margin=1):
        _o, bgra = as_bgra(img)
        a = bgra[..., 3]
        h, w = a.shape[:2]
        m = max(0, int(margin))
        left = a[:, :m] if m else a[:, :1]
        right = a[:, w - m:] if m else a[:, w - 1:]
        top = a[:m, :] if m else a[:1, :]
        bot = a[h - m:, :] if m else a[h - 1:, :]
        return (
            int(left.max()) == 0 and int(right.max()) == 0
            and int(top.max()) == 0 and int(bot.max()) == 0
        )

    def _ink_in_safe(img, layout, vw, vh):
        _o, bgra = as_bgra(img)
        a = bgra[..., 3]
        ys, xs = __import__("numpy").nonzero(a > 8)
        if xs.size == 0:
            return True, None
        ox, oy = layout.origin
        box = (int(xs.min()) + ox, int(ys.min()) + oy,
               int(xs.max()) + ox, int(ys.max()) + oy)
        mx, my = vw * SAFE_MARGIN, vh * SAFE_MARGIN
        ok = (box[0] >= mx and box[2] <= vw - mx
              and box[1] >= my and box[3] <= vh - my)
        return ok, box

    for k in range(11):
        t = k / 10.0
        img = raster_roi(
            lay_pop, spec_pop, st_pop, None, cw, ch, skip_blur=True, t=t)
        assert _edge_clear(img, 1), ("pop edge", t)
        ok, box = _ink_in_safe(img, lay_pop, cw, ch)
        assert ok, ("pop SAFE_MARGIN bbox", t, box, cw * SAFE_MARGIN)

    def _scan_overshoot(anim_name, canvas_w, canvas_h):
        long_text = "LONG LINE OVERSHOOTS THE SAFE MARGIN AT PEAK SCALE NOW"
        st = {
            "fontsize": 96,
            "fontsize_pct": 96.0 / float(min(canvas_w, canvas_h)),
            "wrap_lines": 1,
            "font_family": "Arimo",
            "bold": True,
            "pos_x": canvas_w / 2.0,
            "pos_y": canvas_h * 0.5,
            "fx": {
                "bg": {"type": "none"},
                "glow": {"sigma_em": 0},
                "shadow": {"sigma_em": 0},
                "anim": {
                    "in": anim_name, "loop": "none", "out": "none",
                    "in_s": 0.45, "unit": "segment", "easing": "linear",
                },
            },
        }
        words = long_text.split()
        ev = {
            "text": long_text, "start": 0.0, "end": 2.0, "id": anim_name,
            "words": [{"word": w, "start": 0.0, "end": 0.4} for w in words],
        }
        spec = from_style(st)
        lay = layout_event(ev, st, spec, canvas_w, canvas_h)
        for k in range(11):
            t = k / 10.0 * 0.45
            img = raster_roi(
                lay, spec, st, None, canvas_w, canvas_h,
                skip_blur=True, t=t, event=ev)
            assert _edge_clear(img, 1), (anim_name, "roi edge", t)
            ok, box = _ink_in_safe(img, lay, canvas_w, canvas_h)
            assert ok, (anim_name, "SAFE_MARGIN bbox", t, box,
                        canvas_w * SAFE_MARGIN)

    _scan_overshoot("stomp", 1080, 1920)
    _scan_overshoot("impact_pop", 1080, 1920)

    # Short in-margin line: clamp is a byte-identity no-op (g10-shaped).
    import hashlib as _hl
    from cutora.render.textfx import raster as _raster_mod

    def _sha_img(img):
        _o, bgra = as_bgra(img)
        return _hl.md5(bgra.tobytes()).hexdigest()

    st_short = {
        "fontsize": 56,
        "fontsize_pct": 56.0 / 1080.0,
        "wrap_lines": 1,
        "font_family": "Arimo",
        "bold": True,
        "pos_x": 540.0,
        "pos_y": 960.0,
        "fx": {
            "bg": {"type": "none"}, "glow": {"sigma_em": 0},
            "shadow": {"sigma_em": 0},
            "anim": {"in": "stomp", "loop": "none", "out": "none",
                     "in_s": 0.45, "unit": "segment"},
        },
    }
    ev_short = {
        "text": "SDK READY.", "start": 0.0, "end": 2.0, "id": "g10c1",
        "words": [{"word": "SDK", "start": 0.0, "end": 0.2},
                  {"word": "READY.", "start": 0.2, "end": 0.4}],
    }
    spec_short = from_style(st_short)
    lay_short = layout_event(ev_short, st_short, spec_short, 1080, 1920)
    t_peak = 0.08 * 0.45
    img_on = raster_roi(
        lay_short, spec_short, st_short, None, 1080, 1920,
        skip_blur=True, t=t_peak, event=ev_short)
    _orig_clamp = _raster_mod._clamp_xf_to_safe
    _raster_mod._clamp_xf_to_safe = lambda xf, *a, **k: xf
    try:
        img_off = raster_roi(
            lay_short, spec_short, st_short, None, 1080, 1920,
            skip_blur=True, t=t_peak, event=ev_short)
    finally:
        _raster_mod._clamp_xf_to_safe = _orig_clamp
    assert _sha_img(img_on) == _sha_img(img_off), "short stomp must be a no-op"

    st_sc = dict(st_pop)
    st_sc["fx"] = dict(st_pop["fx"])
    st_sc["fx"]["scatter"] = {
        "enabled": True, "size_jitter": 0.2,
        "pos_jitter_em": [0.5, 0.3], "rot_jitter_deg": 8,
    }
    st_sc["fx"]["emphasis"] = {
        "words": ["office"], "color": "#22D3EE",
        "scale": 1.0, "anim": "none", "max_per_cue": 0,
    }
    spec_sc = from_style(st_sc)
    lay_sc = layout_event(ev_pop, st_sc, spec_sc, cw, ch)
    for k in range(11):
        t = k / 10.0
        img = raster_roi(
            lay_sc, spec_sc, st_sc, None, cw, ch, skip_blur=True, t=t)
        assert _edge_clear(img, 1), ("scatter edge", t)

    _assert_emphasis_does_not_retrack_neighbors()

    print("T04F3_LAYOUT_OK", flush=True)
    return 0


def _dilate1(mask):
    import numpy as np
    m = np.asarray(mask, dtype=bool)
    out = m.copy()
    out[1:, :] |= m[:-1, :]
    out[:-1, :] |= m[1:, :]
    out[:, 1:] |= m[:, :-1]
    out[:, :-1] |= m[:, 1:]
    out[1:, 1:] |= m[:-1, :-1]
    out[1:, :-1] |= m[:-1, 1:]
    out[:-1, 1:] |= m[1:, :-1]
    out[:-1, :-1] |= m[1:, 1:]
    return out


def _ink_within_1px(a, b) -> bool:
    """Every ink pixel in A has a counterpart in B within 1 px, and vice versa."""
    da, db = _dilate1(a), _dilate1(b)
    return (not (a & ~db).any()) and (not (b & ~da).any())


def _assert_emphasis_does_not_retrack_neighbors() -> None:
    """Emphasizing one word must not change gaps between the others.

    T04f-4: a dirty tree leaked ~0.1 em tracking onto every word (g01 v1
    width +7.8%). If that leak is in layout/raster, this fails at pytest
    instead of waiting for golden.
    """
    import numpy as np
    from cutora.render.textfx.layout import layout_event
    from cutora.render.textfx.raster import as_bgra, raster_roi
    from cutora.render.textfx.spec import from_style

    sentence = "We cut costs at the office now"
    words = sentence.split()
    cw, ch = 1920, 1080
    ev = {
        "text": sentence, "start": 0.0, "end": 2.0, "id": "gap1",
        "words": [
            {"word": w, "start": i * 0.15, "end": i * 0.15 + 0.14}
            for i, w in enumerate(words)
        ],
    }

    def _st(emph_words, anim):
        return {
            "fontsize": 48,
            "fontsize_pct": 48.0 / float(min(cw, ch)),
            "wrap_lines": 1,
            "wrap_left": 80,
            "wrap_right": 1840,
            "font_family": "Arimo",
            "bold": True,
            "italic": False,
            "pos_x": cw / 2.0,
            "pos_y": ch * 0.55,
            "fx": {
                "bg": {"type": "none"},
                "glow": {"sigma_em": 0},
                "shadow": {"sigma_em": 0},
                "emphasis": {
                    "words": list(emph_words),
                    "color": "#22D3EE",
                    "scale": 1.0,
                    "anim": anim,
                    "max_per_cue": 0,
                },
            },
        }

    st_off = _st([], "none")
    st_on = _st(["office"], "pop")
    spec_off = from_style(st_off)
    spec_on = from_style(st_on)
    lay_off = layout_event(ev, st_off, spec_off, cw, ch)
    lay_on = layout_event(ev, st_on, spec_on, cw, ch)
    assert len(lay_off.lines) == 1 and len(lay_on.lines) == 1
    assert abs(float(lay_off.em) - float(lay_on.em)) < 0.5, (
        "em shrunk %.3f -> %.3f; widen the wrap box"
        % (lay_off.em, lay_on.em)
    )
    runs_off = lay_off.lines[0].runs
    runs_on = lay_on.lines[0].runs
    assert [r.text.strip() for r in runs_off] == words
    assert [r.text.strip() for r in runs_on] == words
    office_i = words.index("office")
    assert runs_on[office_i].emphasized

    # Space lives inside WordRun.w, so runs pack (gap ~ 0). A leaked
    # side-pad/tracking shows up as extra width on non-emphasized runs
    # and as extra advance of later words relative to the line start.
    x0_off = float(runs_off[0].x)
    x0_on = float(runs_on[0].x)
    for i, (ra, rb) in enumerate(zip(runs_off, runs_on)):
        if i == office_i:
            continue
        assert abs(float(ra.w) - float(rb.w)) <= 1.0, (
            "non-emphasized %r width %.3f -> %.3f"
            % (words[i], ra.w, rb.w)
        )
        if i > office_i:
            continue
        d0 = float(ra.x) - x0_off
        d1 = float(rb.x) - x0_on
        assert abs(d0 - d1) <= 1.0, (
            "non-emphasized %r offset %.3f -> %.3f"
            % (words[i], d0, d1)
        )

    img_off = raster_roi(
        lay_off, spec_off, st_off, None, cw, ch, skip_blur=True, t=1.0)
    img_on = raster_roi(
        lay_on, spec_on, st_on, None, cw, ch, skip_blur=True, t=1.0)
    _, bgra_off = as_bgra(img_off)
    _, bgra_on = as_bgra(img_on)
    ink_off = bgra_off[..., 3] > 8
    ink_on = bgra_on[..., 3] > 8

    def _run_img_box(lay, run):
        ox, oy = lay.origin
        x0 = int(np.floor(run.x - ox)) - 1
        y0 = int(np.floor(run.y - oy)) - 1
        x1 = int(np.ceil(run.x + run.w - ox)) + 1
        y1 = int(np.ceil(run.y + run.h - oy)) + 1
        return x0, y0, x1, y1

    def _pair_crop(ink, lay, left, right):
        x0a, y0a, x1a, y1a = _run_img_box(lay, left)
        x0b, y0b, x1b, y1b = _run_img_box(lay, right)
        x0, x1 = min(x0a, x0b), max(x1a, x1b)
        y0, y1 = min(y0a, y0b), max(y1a, y1b)
        h, w = ink.shape
        x0, y0 = max(0, x0), max(0, y0)
        x1, y1 = min(w, x1), min(h, y1)
        return ink[y0:y1, x0:x1]

    for i in range(len(words) - 1):
        if i == office_i or i + 1 == office_i:
            continue
        crop_off = _pair_crop(ink_off, lay_off, runs_off[i], runs_off[i + 1])
        crop_on = _pair_crop(ink_on, lay_on, runs_on[i], runs_on[i + 1])
        assert crop_off.size and crop_on.size, (words[i], words[i + 1])
        assert abs(crop_off.shape[1] - crop_on.shape[1]) <= 2, (
            "pair %s-%s width %s vs %s"
            % (words[i], words[i + 1], crop_off.shape, crop_on.shape)
        )
        assert abs(crop_off.shape[0] - crop_on.shape[0]) <= 2, (
            "pair %s-%s height %s vs %s"
            % (words[i], words[i + 1], crop_off.shape, crop_on.shape)
        )
        h = min(crop_off.shape[0], crop_on.shape[0])
        w = min(crop_off.shape[1], crop_on.shape[1])
        assert _ink_within_1px(crop_off[:h, :w], crop_on[:h, :w]), (
            "pixel gap %s-%s changed beyond 1px AA"
            % (words[i], words[i + 1])
        )


if __name__ == "__main__":
    import sys
    if "--layout" in sys.argv:
        raise SystemExit(_layout_suite())
    raise SystemExit(0)
