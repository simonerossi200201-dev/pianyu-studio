# -*- coding: utf-8 -*-
"""T04f-2 static fx: textures, metal stops, contact-sheet coverage, fonts."""
from __future__ import annotations

import hashlib
import os

from cutora.render.textfx.fade import edge_fade_mask
from cutora.render.textfx.ramps import METAL_STOPS, sample_ramp
from cutora.render.textfx.spec import is_pure_black
from cutora.render.textfx.static_fx import (
    DARK_SHEET_OVERRIDES,
    SHEET_CAP_RATIO,
    SHEET_CELL_H,
    SHEET_CELL_W,
    SHEET_LABEL_H,
    T04F2_ENABLED_COVERS,
    covered_controls,
    effect_cells,
)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FONT_DIR = os.path.join(REPO, "resources", "fonts")
T04F2_FONTS = {
    "Anton": ("ofl", "Anton-Regular.ttf"),
    "Bebas_Neue": ("ofl", "BebasNeue-Regular.ttf"),
    "Bangers": ("ofl", "Bangers-Regular.ttf"),
    "Caveat": ("ofl", "Caveat-VariableFont_wght.ttf"),
    "Permanent_Marker": ("apache", "PermanentMarker-Regular.ttf"),
    "Playfair_Display": ("ofl", "PlayfairDisplay-VariableFont_wght.ttf"),
}


def test_t04f2_controls_enabled():
    path = os.path.join(REPO, "cutora", "ui", "panels", "fx_schema.py")
    src = open(path, encoding="utf-8").read()
    assert "T04F2_DISABLED_PATHS = frozenset()" in src
    assert "T04F2_DISABLED_ENUM: dict[str, frozenset] = {}" in src


def test_contact_sheet_covers_enabled_controls():
    got = covered_controls()
    missing = set(T04F2_ENABLED_COVERS) - got
    assert not missing, missing


def test_metal_stops_not_pure_black():
    for name, stops in METAL_STOPS.items():
        assert len(stops) >= 4, name
        for pos, color in stops:
            assert 0.0 <= pos <= 1.0, (name, pos)
            assert not is_pure_black(color), (name, color)


def test_gradient_ramp_is_float32_1024():
    import numpy as np
    ramp = sample_ramp([(0.0, "#7A5A12"), (1.0, "#F0CC6A")])
    assert ramp.shape == (1024, 4)
    assert ramp.dtype == np.float32
    assert float(ramp[0, 0]) > 0.4  # R of #7A5A12


def test_ramp_premultiplied_alpha_stays_red():
    import numpy as np
    ramp = sample_ramp([(0.0, "#FF0000FF"), (1.0, "#FF000000")])
    mid = ramp[512]
    assert float(mid[0]) > 0.9, mid
    assert float(mid[1]) < 0.08 and float(mid[2]) < 0.08
    assert 0.4 < float(mid[3]) < 0.6


def test_sheet_geometry_is_large_enough():
    assert SHEET_CELL_W >= 960
    assert SHEET_CELL_H >= 540
    assert SHEET_LABEL_H >= 24
    assert SHEET_CAP_RATIO >= 0.25


def test_edge_fade_mask_left_ramps():
    import numpy as np
    mask = edge_fade_mask(40, 100, 0, 0, 0.3, 0)
    assert mask.shape == (40, 100)
    assert float(mask[20, 0]) < 0.05
    assert float(mask[20, 50]) > 0.99
    assert float(mask[20, 15]) < float(mask[20, 29])
    # smoothstep(0.5) = 0.5 at the midpoint of the 30 px ramp
    assert abs(float(mask[20, 15]) - 0.5) < 0.02


def test_fade_tuple_missing_is_zero():
    from cutora.render.textfx.fade import fade_tuple, write_fade
    assert fade_tuple({}) == (0.0, 0.0, 0.0, 0.0)
    assert fade_tuple(None) == (0.0, 0.0, 0.0, 0.0)
    obj = {}
    write_fade(obj, 0.25, 0.0, 0.1, 0.0)
    assert abs(obj["fade_top"] - 0.25) < 1e-9
    assert abs(obj["fade_left"] - 0.1) < 1e-9
    write_fade(obj, 0.9, 0, 0, 0)
    assert abs(obj["fade_top"] - 0.9) < 1e-9
    write_fade(obj, 1.5, 0, 0, 0)
    assert abs(obj["fade_top"] - 1.0) < 1e-9


def test_textures_seamless_and_checked_in():
    import cv2
    import numpy as np
    dest = os.path.join(REPO, "resources", "textures")
    for name in ("paper", "linen", "grid"):
        path = os.path.join(dest, "%s.png" % name)
        assert os.path.isfile(path), path
        img = cv2.imread(path, cv2.IMREAD_UNCHANGED)
        assert img is not None and img.shape[0] == 1024
        gray = img[:, :, 0].astype(np.float32) / 255.0
        ix = float(np.abs(gray[:, 1:] - gray[:, :-1]).max())
        iy = float(np.abs(gray[1:, :] - gray[:-1, :]).max())
        wx = float(np.abs(gray[:, 0] - gray[:, -1]).max())
        wy = float(np.abs(gray[0, :] - gray[-1, :]).max())
        assert wx <= ix + 2.0 / 255.0, (name, wx, ix)
        assert wy <= iy + 2.0 / 255.0, (name, wy, iy)


def test_linen_period_and_low_contrast():
    import cv2
    import numpy as np
    path = os.path.join(REPO, "resources", "textures", "linen.png")
    img = cv2.imread(path, cv2.IMREAD_UNCHANGED)
    gray = img[:, :, 0].astype(np.float32) / 255.0
    row = gray[gray.shape[0] // 2]
    row = row - cv2.GaussianBlur(row.reshape(1, -1), (0, 0), 5.0).ravel()
    spec = np.abs(np.fft.rfft(row))
    freqs = np.fft.rfftfreq(row.size, d=1.0)
    band = (freqs > 1.0 / 4.5) & (freqs < 1.0 / 1.6)
    peak = float(freqs[band][int(np.argmax(spec[band]))])
    period = 1.0 / peak
    assert 1.8 <= period <= 3.5, period
    p5, p95 = np.percentile(gray, [5, 95])
    assert float(p95 - p5) <= 0.18, (p5, p95)


def test_paper_grain_is_visible():
    import cv2
    import numpy as np
    path = os.path.join(REPO, "resources", "textures", "paper.png")
    img = cv2.imread(path, cv2.IMREAD_UNCHANGED)
    gray = img[:, :, 0].astype(np.float32) / 255.0
    blur = cv2.GaussianBlur(gray, (0, 0), sigmaX=3.0)
    hp = gray - blur
    assert float(hp.std()) >= 0.012, hp.std()


def test_glass_thickness_defaults_and_zero():
    from cutora.render.textfx.spec import from_style
    spec = from_style({"fx": {"bg": {"type": "glass"}}})
    assert abs(spec.bg.glass_thickness_em - 0.14) < 1e-9
    assert abs(spec.bg.glass_refraction - 0.55) < 1e-9
    assert abs(spec.bg.glass_bevel - 0.7) < 1e-9
    assert abs(spec.bg.glass_sheen - 0.45) < 1e-9
    assert abs(spec.bg.glass_light_deg - 315) < 1e-9
    spec0 = from_style({"fx": {"bg": {"type": "glass", "glass_thickness_em": 0}}})
    assert spec0.bg.glass_thickness_em == 0.0


def test_glass_edge_weight_peaks_at_border():
    import numpy as np
    from cutora.render.textfx.glass_thick import edge_weight
    d = np.array([[0.0, 5.0, 10.0, 20.0]], dtype=np.float32)
    k = edge_weight(d, 10.0)
    assert float(k[0, 0]) == 1.0
    assert float(k[0, 3]) == 0.0
    assert float(k[0, 1]) > float(k[0, 2])


def test_sdf_sees_straight_edges_and_k_band_width():
    """Full-bleed mask must still have d≈1 on the straight top, not only corners."""
    import numpy as np
    from cutora.render.textfx.glass_thick import (
        GLASS_RIM_SIGMA_EM, edge_weight, k_band_width_px, rim_sigma_px,
        sdf_from_mask,
    )
    assert abs(GLASS_RIM_SIGMA_EM - 0.04) < 1e-9
    assert rim_sigma_px(80.0) <= 1.2 + 1e-9
    assert rim_sigma_px(40.0) >= 0.6 - 1e-9
    mask = np.full((80, 160), 255, dtype=np.uint8)
    d, nx, ny = sdf_from_mask(mask)
    assert float(d[0, 80]) < 2.0, d[0, 80]
    assert float(d[40, 0]) < 2.0, d[40, 0]
    assert float(d[40, 80]) > 10.0
    width = 20.0
    k = edge_weight(d, width)
    band = k_band_width_px(k, 0.1)
    assert band >= 0.6 * width, band


def test_dark_sheet_overrides_visible_colors():
    frame = {c["id"]: c for c in effect_cells("frame")}
    dark = {c["id"]: c for c in effect_cells("dark")}
    assert frame["stroke"]["fx"]["stroke"]["color"] == "#141A26"
    assert dark["stroke"]["fx"]["stroke"]["color"] == "#22D3EE"
    assert dark["bg-box"]["fx"]["bg"]["color"] == "#3A0A14"
    assert dark["bubble-alt"]["fx"]["bg"]["color"] == "#062E2E"
    assert set(DARK_SHEET_OVERRIDES) == {"stroke", "bg-box", "bubble-alt"}


def test_t04f2_fonts_have_license_and_sha256():
    lic_md = os.path.join(FONT_DIR, "FONTS_LICENSES.md")
    assert os.path.isfile(lic_md)
    text = open(lic_md, encoding="utf-8").read()
    for folder, (_kind, ttf) in T04F2_FONTS.items():
        fam_dir = os.path.join(FONT_DIR, folder)
        assert os.path.isdir(fam_dir), fam_dir
        files = os.listdir(fam_dir)
        low = {fn.lower() for fn in files}
        assert "ofl.txt" in low or "license.txt" in low, folder
        ttf_path = os.path.join(fam_dir, ttf)
        if not os.path.isfile(ttf_path):
            ttfs = [fn for fn in files if fn.lower().endswith(".ttf")]
            assert ttfs, folder
            ttf_path = os.path.join(fam_dir, ttfs[0])
        digest = hashlib.sha256(open(ttf_path, "rb").read()).hexdigest()
        assert digest in text, (folder, digest)
        assert folder.replace("_", " ") in text or folder in text
