# -*- coding: utf-8 -*-
"""T06-2: width-based reflow, safe-zone cap, pixel ink lock."""
from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from cutora.subtitle.layout import (
    FIT_SCALE_FLOOR, MAX_FIT_ITERS, LayoutRules, break_lines, reflow_and_fit,
)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = os.path.join(REPO, "runtime", "venv", "Scripts", "python.exe")

OVERFLOW = (
    "Please remember to bring the documents, the maps, the keys, "
    "and the letters for the meeting tomorrow morning at the hall "
    "of the old station."
)


def _seg(text, lang="en"):
    return {"text": text, "lang": lang}


class FM:
    def __init__(self, scale, ppc=12.0):
        self.scale = float(scale)
        self.ppc = float(ppc)

    def horizontalAdvance(self, s):
        return len(str(s)) * self.ppc * self.scale


def test_fitting_two_line_path_unchanged_g09_seg0():
    text = "They waited in the shade of the old church for a friend from town."
    lines = break_lines(
        _seg(text),
        LayoutRules(max_chars_per_line=42, max_lines=2, balance_lines=True, lang="en"),
    )
    assert lines == [
        "They waited in the shade of the old",
        "church for a friend from town.",
    ], lines


def test_reflow_fits_width_and_converges():
    rules = LayoutRules(max_chars_per_line=42, max_lines=2, balance_lines=True, lang="en")
    lines = break_lines(_seg(OVERFLOW), rules)
    cap = 400.0
    out, scale, warn, iters = reflow_and_fit(
        OVERFLOW, lines, "en", rules, lambda sc: FM(sc), cap)
    assert len(out) <= 2
    assert iters <= MAX_FIT_ITERS
    assert scale >= FIT_SCALE_FLOOR - 1e-9
    fm = FM(scale)
    widths = [fm.horizontalAdvance(ln) for ln in out]
    assert all(w <= cap + 0.5 for w in widths), (widths, cap, out)
    mx = max(widths)
    if mx > 0 and len(widths) == 2:
        assert abs(widths[0] - widths[1]) / mx <= 0.25, widths
    vis = " ".join(out)
    orig = " ".join(OVERFLOW.split())
    vis_words = vis.replace("…", " ").split()
    if vis_words != orig.split():
        assert vis.rstrip().endswith("…"), vis
    assert scale <= FIT_SCALE_FLOOR + 1e-6 or vis_words == orig.split()


def _ink_env():
    env = dict(os.environ)
    env["PYTHONPATH"] = REPO + os.pathsep + env.get("PYTHONPATH", "")
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["PYTHONUNBUFFERED"] = "1"
    return env


def test_fit_v1_noop_when_text_fits_wrap():
    r = subprocess.run(
        [PY, "-u", os.path.abspath(__file__), "--noop"],
        cwd=REPO, env=_ink_env(), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=180)
    out = (r.stdout or "") + "\n" + (r.stderr or "")
    assert r.returncode == 0 and "T06_NOOP_OK" in (r.stdout or ""), out[-8000:]


@pytest.mark.skip(reason="g09 retired with v1 golden cases")
def test_g09_seg3_ink_inside_safe_zone():
    r = subprocess.run(
        [PY, "-u", os.path.abspath(__file__), "--ink"],
        cwd=REPO,
        env=_ink_env(),
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=180,
    )
    out = (r.stdout or "") + "\n" + (r.stderr or "")
    assert r.returncode == 0 and "T06_INK_OK" in (r.stdout or ""), out[-8000:]


def _noop_suite() -> int:
    """Lines that already fit the wrap box must not change (T06-2 ruling §3)."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from cutora.core.runtime import init_torch_runtime
    init_torch_runtime(render_child=False)
    from PySide6.QtGui import QColor, QImage, QPainter, QFontMetrics
    from PySide6.QtWidgets import QApplication
    from cutora.render.frame_renderer import FrameRenderer, _fit_v1_overwide_lines
    from cutora.ui.panels.fonts import get_font_manager, load_embedded_fonts, style_weight_args

    app = QApplication.instance() or QApplication([])
    _ = app
    assert load_embedded_fonts()
    s = {
        "font_family": "SN Pro", "fontsize": 35, "bold": True,
        "pos_x": 540, "use_stroke": True, "border_width": 2,
        "wrap_lines": 2, "color_text": "#ffffff",
    }
    lines = ["Hello friends.", "They waited in the shade."]
    event = {"text": "Hello friends. They waited in the shade.", "lang": "en",
             "start": 0.0, "end": 2.0, "lines": list(lines)}
    img = QImage(1080, 1920, QImage.Format.Format_ARGB32)
    img.fill(QColor(0, 0, 0, 255))
    p = QPainter(img)
    b, i, st = style_weight_args(s)
    font = get_font_manager().get_font("SN Pro", 35, bold=b, italic=i, style=st)
    p.setFont(font)
    fm = QFontMetrics(font)
    wrap = 880.0
    src_lines = list(lines)
    out_lines, _fm, _font, out_sz = _fit_v1_overwide_lines(
        dict(event), s, src_lines, fm, font, 35.0, "SN Pro", wrap, p,
        video_w=1080, video_h=1920)
    p.end()
    assert list(out_lines) == list(lines), (out_lines, lines)
    assert float(out_sz) == 35.0, out_sz
    img_a = QImage(1080, 1920, QImage.Format.Format_ARGB32)
    img_a.fill(QColor(0, 0, 0, 255))
    pa = QPainter(img_a)
    FrameRenderer.draw_subtitle(pa, dict(event), dict(s), 1.0, 1080, 1920)
    pa.end()
    img_b = QImage(1080, 1920, QImage.Format.Format_ARGB32)
    img_b.fill(QColor(0, 0, 0, 255))
    pb = QPainter(img_b)
    FrameRenderer.draw_subtitle(pb, dict(event), dict(s), 1.0, 1080, 1920)
    pb.end()
    ba = bytes(img_a.constBits())
    bb = bytes(img_b.constBits())
    assert ba == bb, "fitting text must paint identically on two draws"
    print("T06_NOOP_OK", flush=True)
    return 0


def _ink_suite() -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from cutora.core.runtime import init_torch_runtime
    init_torch_runtime(render_child=False)
    from PySide6.QtGui import QColor, QImage, QPainter
    from PySide6.QtWidgets import QApplication
    from cutora.render.frame_renderer import FrameRenderer
    from cutora.render.safe_zones import union_unsafe, usable_rect
    from cutora.ui.panels.fonts import load_embedded_fonts

    app = QApplication.instance() or QApplication([])
    _ = app
    assert load_embedded_fonts()
    g09 = json.loads(open(
        os.path.join(REPO, "tests", "golden", "g09_linebreak_latin",
                     "project.asproj.json"), encoding="utf-8").read())
    task = g09["tasks"][0]
    style = dict(task["style"])
    event = dict(task["segments"][3])
    w, h = 1080, 1920
    img = QImage(w, h, QImage.Format.Format_ARGB32)
    img.fill(QColor(0, 0, 0, 255))
    p = QPainter(img)
    FrameRenderer.draw_subtitle(p, event, style, (event["start"] + event["end"]) / 2.0, w, h)
    p.end()
    u = union_unsafe(w, h)
    n_left = max(1, int(round(w * u["left"])))
    n_right = max(1, int(round(w * u["right"])))
    # Locked ink cutoffs (T06-2 4c). Do not "tune": a looser/tighter
    # threshold changes whether anti-aliased edges count. Claude's review
    # used min(RGB)>=150 for AA; this lock uses 200 for glyph interiors
    # and rgb-sum>40 for the outer-column zero-ink band.
    INK_WHITE_RGB = 200
    INK_EDGE_SUM = 40
    sx, _sy, sw, _sh = usable_rect(w, h)
    safe_right = sx + sw

    def col_ink(x0, x1):
        n = 0
        for x in range(x0, x1):
            for y in range(h):
                c = img.pixelColor(x, y)
                if c.red() + c.green() + c.blue() > INK_EDGE_SUM:
                    n += 1
        return n

    left_ink = col_ink(0, n_left)
    right_ink = col_ink(w - n_right, w)
    # White glyph rows → line bboxes
    hits = []
    for y in range(h):
        xs = [x for x in range(w)
              if img.pixelColor(x, y).red() > INK_WHITE_RGB
              and img.pixelColor(x, y).green() > INK_WHITE_RGB
              and img.pixelColor(x, y).blue() > INK_WHITE_RGB]
        if len(xs) >= 8:
            hits.append((y, xs[0], xs[-1]))
    lines = []
    if hits:
        cur = [hits[0]]
        for r in hits[1:]:
            if r[0] - cur[-1][0] > 6:
                lines.append(cur)
                cur = [r]
            else:
                cur.append(r)
        lines.append(cur)
    boxes = []
    for ln in lines:
        y0, y1 = ln[0][0], ln[-1][0]
        if y1 - y0 < 6:
            continue
        x0 = min(r[1] for r in ln)
        x1 = max(r[2] for r in ln)
        boxes.append((x0, x1, x1 - x0 + 1))
    assert boxes, "no subtitle ink"
    for x0, x1, _ww in boxes:
        assert x0 >= sx - 1, (x0, sx, boxes)
        assert x1 <= safe_right + 1, (x1, safe_right, boxes)
    widths = [b[2] for b in boxes]
    mx = max(widths)
    if len(widths) >= 2:
        assert abs(widths[0] - widths[-1]) / mx <= 0.25, widths
    assert left_ink == 0, left_ink
    assert right_ink == 0, right_ink
    visible = str(event.get("_fit_visible") or "")
    orig = " ".join(str(event.get("text") or "").split())
    vis_plain = " ".join(visible.replace("…", " ").split())
    if vis_plain != orig:
        assert visible.rstrip().endswith("…"), (
            "silent drop: visible=%r orig=%r" % (visible, orig))
    print("T06_INK_OK boxes=%s leftN=%s rightN=%s visible=%r scale_iters=%s"
          % (boxes, n_left, n_right, visible, event.get("_fit_iters")), flush=True)
    return 0


if __name__ == "__main__":
    if "--ink" in sys.argv:
        raise SystemExit(_ink_suite())
    if "--noop" in sys.argv:
        raise SystemExit(_noop_suite())
    raise SystemExit("usage: --ink | --noop")
