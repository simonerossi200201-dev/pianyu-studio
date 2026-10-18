# -*- coding: utf-8 -*-
"""T04L L3: bundled fallback, zero tofu, no system font."""
from __future__ import annotations

import json
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = os.path.join(REPO, "runtime", "venv", "Scripts", "python.exe")
FFMPEG_DIR = os.path.join(REPO, "runtime", "ffmpeg")


def test_script_fallback_covers_languages_scripts():
    from cutora.asr.languages import SCRIPT_ENUM, load_catalog
    from cutora.subtitle.font_coverage import fallback_family_for_script, load_script_fallback
    table = load_script_fallback()
    for sid in SCRIPT_ENUM:
        assert sid in table, sid
        fam = fallback_family_for_script(sid)
        assert fam
    used = {row["script"] for row in load_catalog()}
    for sid in used:
        assert fallback_family_for_script(sid)


def test_anton_cyrillic_substitutes_noto_sans():
    from cutora.subtitle.font_coverage import resolve_run_font
    rec = resolve_run_font("Anton", "Привет мир", "cyrillic")
    assert rec["substituted"] is True
    assert rec["effective"] != "Anton"
    assert rec["effective"].startswith("Noto Sans")
    assert "Noto Sans" in (rec.get("note") or rec["effective"])


def test_zero_tofu_native_names_on_script_default():
    from cutora.asr.languages import load_catalog
    from cutora.subtitle.font_coverage import fallback_family_for_script, missing_chars
    holes = []
    for row in load_catalog():
        sample = row.get("name_native") or row.get("name_en") or row["code"]
        fam = fallback_family_for_script(row["script"])
        miss = missing_chars(fam, sample)
        if miss:
            holes.append((row["code"], fam, miss[:8]))
    assert holes == [], holes[:12]


def test_layout_uses_split_and_no_font_merging():
    src = open(os.path.join(REPO, "cutora", "render", "textfx", "layout.py"), encoding="utf-8").read()
    assert "split_font_runs" in src
    assert "QFont(base)" in src
    assert "apply_no_font_merging" in src
    fonts_src = open(os.path.join(REPO, "cutora", "ui", "panels", "fonts.py"), encoding="utf-8").read()
    assert "apply_no_font_merging" in fonts_src


def test_match_bundled_family_is_exact_never_prefix():
    from cutora.subtitle.font_coverage import match_bundled_family, split_font_runs
    sn = match_bundled_family("SN Pro")
    assert sn == "SN Pro"
    assert not sn.startswith("SN Pro ")
    noto = match_bundled_family("Noto Sans")
    assert noto == "Noto Sans"
    assert not noto.startswith("Noto Sans ")
    runs = split_font_runs("SN Pro", "Hello friends.", "latin")
    fams = {fam for _piece, fam in runs}
    assert "SN Pro ExtraLight" not in fams


PUNCT_SYMBOL_SAMPLE = "“”‘’…—–‹›€£¥©®™°0123456789,.!?:;"
EMOJI_SAMPLE = "🔥❤️👉"


def test_fallback_is_per_character_not_whole_string():
    from cutora.subtitle.font_coverage import split_font_runs
    # Anton has no Cyrillic. Whole-token fallback would paint "Hello" in Noto Sans.
    runs = split_font_runs("Anton", "Hello Привет", "cyrillic")
    assert "".join(piece for piece, _fam in runs) == "Hello Привет"
    latin = "".join(piece for piece, fam in runs if fam == "Anton")
    cyr = "".join(piece for piece, fam in runs if fam != "Anton")
    assert "Hello" in latin
    assert "Привет" in cyr
    assert "Hello" not in cyr
    assert "Привет" not in latin


def test_ellipsis_does_not_swap_whole_latin_token():
    from cutora.subtitle.font_coverage import split_font_runs
    # Acme cmap misses … ; letters must stay Acme.
    runs = split_font_runs("Acme", "Hello…world", "latin")
    assert "".join(piece for piece, _fam in runs) == "Hello…world"
    acme = "".join(piece for piece, fam in runs if fam == "Acme")
    other = "".join(piece for piece, fam in runs if fam != "Acme")
    assert "Hello" in acme and "world" in acme
    assert "…" in other
    assert "Hello" not in other and "world" not in other


def test_punct_symbol_emoji_zero_notdef_all_families():
    from cutora.subtitle.font_coverage import (
        EMOJI_FAMILY, load_coverage, missing_chars_after_fallback, split_font_runs,
    )
    sample = PUNCT_SYMBOL_SAMPLE + EMOJI_SAMPLE
    coverage = load_coverage().get("coverage") or {}
    assert coverage
    holes = []
    for family in coverage:
        miss = missing_chars_after_fallback(family, sample, "latin")
        if miss:
            holes.append((family, miss[:8]))
    assert holes == [], holes[:12]
    emoji_runs = split_font_runs("Anton", EMOJI_SAMPLE, "latin")
    emoji_fams = {fam for _piece, fam in emoji_runs if any(
        ch in _piece for ch in "🔥❤👉")}
    assert emoji_fams == {EMOJI_FAMILY}, emoji_runs


def test_dropdown_greys_by_language_not_script_union():
    from cutora.subtitle.font_coverage import (
        charset_for_language, family_covers_language, family_covers_script,
        load_charsets,
    )
    basic = charset_for_language("en")
    union = (load_charsets().get("scripts") or {}).get("latin", {}).get("chars") or ""
    assert "A" in basic and "z" in basic
    assert "Ɓ" not in basic
    assert "Ɓ" in union
    assert family_covers_language("Abril Fatface", "en") is True
    assert family_covers_script("Abril Fatface", "latin") is False
    src = open(
        os.path.join(REPO, "cutora", "ui", "panels", "fonts.py"), encoding="utf-8",
    ).read()
    assert "family_covers_language" in src
    assert "family_covers_script(name, script)" not in src


def _weight_child_env() -> dict:
    env = dict(os.environ)
    for key in list(env):
        if key.startswith("PYTEST") or key == "CUTORA_PYTEST_ISOLATED":
            env.pop(key, None)
    env["PYTHONPATH"] = REPO + os.pathsep + env.get("PYTHONPATH", "")
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["PYTHONUNBUFFERED"] = "1"
    path = env.get("PATH", "")
    if os.path.normcase(FFMPEG_DIR) not in os.path.normcase(path):
        env["PATH"] = FFMPEG_DIR + os.pathsep + path
    return env


def test_v2_main_run_keeps_base_weight_and_g08_c0_stem():
    r = subprocess.run(
        [PY, "-u", os.path.join(REPO, "tests", "test_t04l_l3_fallback.py"),
         "--weight"],
        cwd=REPO,
        env=_weight_child_env(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
    )
    out = (r.stdout or "") + "\n" + (r.stderr or "")
    assert r.returncode == 0 and "T04L_L3_WEIGHT_OK" in (r.stdout or ""), out[-8000:]


def _stem_widths(row) -> list[int]:
    runs = []
    n = 0
    for v in row:
        if v:
            n += 1
        elif n:
            runs.append(n)
            n = 0
    if n:
        runs.append(n)
    return runs


def _weight_suite() -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from cutora.core.runtime import init_torch_runtime
    init_torch_runtime(render_child=False)
    from PySide6.QtGui import QFontInfo
    from PySide6.QtWidgets import QApplication
    from cutora.render.textfx.layout import _qfont, layout_event
    from cutora.render.textfx.raster import as_bgra, raster_text
    from cutora.render.textfx.spec import from_style
    from cutora.ui.panels.fonts import load_embedded_fonts

    app = QApplication.instance() or QApplication([])
    _ = app
    assert load_embedded_fonts(), "font load failed"

    g08 = json.loads(open(
        os.path.join(REPO, "tests", "golden", "g08_fx_static",
                     "project.asproj.json"), encoding="utf-8").read())
    style = dict(g08["tasks"][0]["subtitle_roles"]["fx_grad"]["style"])
    text = "Hello friends."
    event = {
        "text": text,
        "start": 0.0,
        "end": 2.0,
        "role": "fx_grad",
        "words": [{"word": w, "start": 0.0, "end": 1.0} for w in text.split()],
    }
    spec = from_style(style)
    layout = layout_event(event, style, spec, 1080, 1920)
    base = _qfont(
        spec.fonts.body or style.get("font_family") or "SN Pro",
        layout.em, bool(style.get("bold", True)),
        bool(style.get("italic", False)), 0.0,
        style_name=str(style.get("font_style") or "").strip(),
    )
    base_info = QFontInfo(base)
    for line in layout.lines:
        for run in line.runs:
            assert run.qfont is not None, run
            info = QFontInfo(run.qfont)
            assert int(info.weight()) == int(base_info.weight()), (
                info.family(), info.styleName(), info.weight(),
                base_info.family(), base_info.styleName(), base_info.weight())
            assert info.styleName() == base_info.styleName(), (
                info.styleName(), base_info.styleName())
            assert "ExtraLight" not in info.styleName()
            assert "Thin" not in info.styleName()
            assert int(info.weight()) >= 700
    print("T04L_L3_WEIGHT_QFONTINFO_OK family=%s style=%s weight=%s"
          % (base_info.family(), base_info.styleName(), base_info.weight()),
          flush=True)

    img = raster_text(layout, spec, style, skip_blur=True, t=1.0)
    _owned, bgra = as_bgra(img)
    alpha = bgra[:, :, 3] > 8
    rows = [i for i in range(alpha.shape[0]) if alpha[i].any()]
    assert rows, "no ink"
    mid = rows[len(rows) // 2]
    raw = _stem_widths(alpha[mid])
    thick = [w for w in raw if w >= 12]
    hair = [w for w in raw if 2 <= w <= 5]
    assert thick and len(thick) >= 3, ("g08 c0 midline stems %s row=%s" % (raw, mid))
    assert not hair, ("g08 c0 hairline stems %s raw=%s" % (hair, raw))
    print("T04L_L3_WEIGHT_STEM_OK thick=%s raw=%s" % (thick, raw), flush=True)
    print("T04L_L3_WEIGHT_OK", flush=True)
    return 0


if __name__ == "__main__":
    if "--weight" in sys.argv:
        raise SystemExit(_weight_suite())
    raise SystemExit("usage: test_t04l_l3_fallback.py --weight")
