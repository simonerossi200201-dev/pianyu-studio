# -*- coding: utf-8 -*-
"""bullet_reveal UNIT_WARP must not change export N after export N-1.

Do not call reset_case_caches() here. That helper is golden-only and would
hide a cross-export leak.
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)
PY = os.path.join(REPO, "runtime", "venv", "Scripts", "python.exe")


def _child_env() -> dict:
    env = dict(os.environ)
    for key in list(env):
        if key.startswith("PYTEST") or key == "CUTORA_PYTEST_ISOLATED":
            env.pop(key, None)
    env["PYTHONPATH"] = REPO + os.pathsep + env.get("PYTHONPATH", "")
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["PYTHONUNBUFFERED"] = "1"
    env["CUTORA_USER_DATA"] = os.path.join(
        os.environ.get("TEMP") or os.environ.get("TMP") or ".",
        "cutora_t11g_leak_ud")
    os.makedirs(env["CUTORA_USER_DATA"], exist_ok=True)
    return env


def test_second_style_matches_solo_child():
    r = subprocess.run(
        [PY, "-u", os.path.abspath(__file__), "--run"],
        cwd=REPO,
        env=_child_env(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
    )
    out = (r.stdout or "") + "\n" + (r.stderr or "")
    assert r.returncode == 0 and "T11G_LEAK_OK" in (r.stdout or ""), out[-8000:]


def _sha(img) -> str:
    return hashlib.sha256(bytes(img.constBits())).hexdigest()


def _style(anim: dict) -> dict:
    return {
        "fontsize": 70,
        "font_family": "SN Pro",
        "bold": True,
        "pos_x": 540.0,
        "pos_y": 1440.0,
        "fx": {
            "fill": {"type": "solid", "colors": ["#FFFFFF"]},
            "stroke": {"width_em": 0.057, "color": "#550000"},
            "glow": {"sigma_em": 0},
            "shadow": {"sigma_em": 0},
            "bg": {"type": "none", "color": "#550000", "alpha": 0.26,
                   "radius_em": 0.37},
            "emphasis": {"color": "#ffff7f"},
            "anim": anim,
        },
    }


def _event():
    return {
        "id": "cue0",
        "text": "Hello friends.",
        "start": 0.0,
        "end": 2.054,
        "role": "default",
        "words": [
            {"word": "Hello", "start": 0.0, "end": 1.027},
            {"word": "friends.", "start": 1.15, "end": 2.054},
        ],
    }


def _pass(style: dict, times: list[float], event: dict, cw: int, ch: int):
    from PySide6.QtGui import QColor, QImage, QPainter
    from cutora.render.textfx import draw
    hs = []
    canvas = QImage(cw, ch, QImage.Format.Format_ARGB32_Premultiplied)
    for t in times:
        canvas.fill(QColor("#101018"))
        p = QPainter(canvas)
        try:
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            draw(p, event, style, float(t), cw, ch)
        finally:
            p.end()
        hs.append(_sha(canvas))
    return tuple(hs)


def _run() -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from cutora.core.runtime import init_torch_runtime
    init_torch_runtime(render_child=False)
    from PySide6.QtWidgets import QApplication
    from cutora.ui.panels.fonts import load_embedded_fonts
    app = QApplication.instance() or QApplication(["t11g-leak"])
    assert load_embedded_fonts()
    _ = app

    cw, ch = 1080, 1920
    event = _event()
    # g11-like: per-line fade via UNIT_WARP + stacked loop.
    style_b = _style({
        "in": "bullet_reveal", "loop": "highlight+box_highlight", "out": "fade",
        "unit": "line", "stagger_s": 0.15, "in_s": 0.3, "out_s": 0.15,
        "easing": "ease_out", "seed": 0,
    })
    style_a = _style({
        "in": "fade", "loop": "karaoke", "out": "fade",
        "unit": "word", "stagger_s": 0.0, "in_s": 0.25, "out_s": 0.15,
        "easing": "ease_out", "seed": 0,
    })
    times = [i / 30.0 for i in range(0, 63)]  # 0..2.066 covers in/out

    solo = _pass(style_b, times, event, cw, ch)
    again = _pass(style_b, times, event, cw, ch)
    ndiff_bb = sum(1 for x, y in zip(solo, again) if x != y)
    print("B_then_B ndiff", ndiff_bb, flush=True)
    assert ndiff_bb == 0, "same style twice in one process drifted"

    _pass(style_a, times, event, cw, ch)
    after_a = _pass(style_b, times, event, cw, ch)
    ndiff_ab = sum(1 for x, y in zip(solo, after_a) if x != y)
    print("B_after_A vs solo ndiff", ndiff_ab, flush=True)
    assert ndiff_ab == 0, "second style after another style drifted from solo"

    print("T11G_LEAK_OK", flush=True)
    return 0


if __name__ == "__main__":
    if "--run" in sys.argv:
        raise SystemExit(_run())
    raise SystemExit(0)
