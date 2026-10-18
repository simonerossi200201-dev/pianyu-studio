# -*- coding: utf-8 -*-
"""T04f-6b3: get_font must not leak letterSpacing across calls or cues.

Do not call reset_case_caches() / FontManager.clear_cache() here. These
tests exist because golden now clears that cache between cases and would
miss an export-A → export-B leak.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile

import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)
PY = os.path.join(REPO, "runtime", "venv", "Scripts", "python.exe")
FFMPEG_DIR = os.path.join(REPO, "runtime", "ffmpeg")


def _child_env() -> dict:
    env = dict(os.environ)
    for key in list(env):
        if key.startswith("PYTEST") or key == "CUTORA_PYTEST_ISOLATED":
            env.pop(key, None)
    env["PYTHONPATH"] = REPO + os.pathsep + env.get("PYTHONPATH", "")
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["PYTHONUNBUFFERED"] = "1"
    env.setdefault("CUTORA_USER_DATA", tempfile.mkdtemp(prefix="cutora_fontleak_"))
    path = env.get("PATH", "")
    if os.path.normcase(FFMPEG_DIR) not in os.path.normcase(path):
        env["PATH"] = FFMPEG_DIR + os.pathsep + path
    return env


def test_font_leak_child():
    r = subprocess.run(
        [PY, "-u", os.path.join(REPO, "tests", "test_t04f6b3_font_leak.py"), "--run"],
        cwd=REPO,
        env=_child_env(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
    )
    out = (r.stdout or "") + "\n" + (r.stderr or "")
    assert r.returncode == 0 and "T04F6B3_FONT_LEAK_OK" in (r.stdout or ""), out[-8000:]


def _style(tracking_em: float) -> dict:
    return {
        "font_family": "SN Pro",
        "fontsize": 70,
        "bold": True,
        "fx": {
            "tracking_em": float(tracking_em),
            "fill": {"type": "solid", "colors": ["#F4EBD8"]},
            "anim": {"in": "none", "loop": "none", "out": "none"},
        },
    }


def _raster(style: dict, text: str = "Hello friends."):
    from cutora.render.textfx.spec import from_style
    from cutora.render.textfx.layout import layout_event
    from cutora.render.textfx.raster import raster_text, as_bgra
    event = {
        "text": text,
        "start": 0.0,
        "end": 2.0,
        "role": "default",
        "words": [{"word": w, "start": 0.0, "end": 1.0} for w in text.split()],
    }
    spec = from_style(style)
    layout = layout_event(event, style, spec, 1080, 1920)
    img = raster_text(layout, spec, style, skip_blur=False, t=1.0)
    _owned, bgra = as_bgra(img)
    return bgra.copy()


def run_suite() -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from cutora.core.runtime import init_torch_runtime
    init_torch_runtime(render_child=False)
    from PySide6.QtWidgets import QApplication
    from PySide6.QtGui import QFont
    from cutora.ui.panels.fonts import load_embedded_fonts, get_font_manager

    app = QApplication.instance() or QApplication([])
    _ = app
    assert load_embedded_fonts(), "font load failed"

    # 1) get_font returns a copy; mutating it cannot poison the cache.
    fm = get_font_manager()
    a = fm.get_font("SN Pro", 70, True, False)
    b = fm.get_font("SN Pro", 70, True, False)
    assert a is not b, "get_font handed out the same QFont twice"
    a.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 99.0)
    c = fm.get_font("SN Pro", 70, True, False)
    assert c.letterSpacingType() != QFont.SpacingType.AbsoluteSpacing or abs(
        float(c.letterSpacing()) - 99.0) > 0.5, (
        "cached QFont leaked letterSpacing: type=%s value=%s"
        % (c.letterSpacingType(), c.letterSpacing()))
    print("T04F6B3_FONT_LEAK_1_OK", flush=True)

    # 2) tracking cue must not change a later no-tracking raster.
    #    Fresh reference is taken first; no cache clear between the three.
    plain = _style(0.0)
    tracked = _style(0.08)
    fresh = _raster(plain)
    _ = _raster(tracked)
    after = _raster(plain)
    assert fresh.shape == after.shape, (fresh.shape, after.shape)
    d = np.abs(fresh.astype("int16") - after.astype("int16"))
    assert int(d.max()) == 0, "tracking cue leaked into later plain raster max=%s" % int(d.max())
    print("T04F6B3_FONT_LEAK_2_OK", flush=True)

    # 3) plain raster twice in one process is byte-identical (r1 vs r2).
    first = _raster(plain)
    second = _raster(plain)
    assert first.shape == second.shape
    d2 = np.abs(first.astype("int16") - second.astype("int16"))
    assert int(d2.max()) == 0, "plain raster not idempotent max=%s" % int(d2.max())
    print("T04F6B3_FONT_LEAK_3_OK", flush=True)
    print("T04F6B3_FONT_LEAK_OK", flush=True)
    return 0


if __name__ == "__main__":
    if "--run" in sys.argv:
        raise SystemExit(run_suite())
    raise SystemExit("usage: test_t04f6b3_font_leak.py --run")
