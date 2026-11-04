# -*- coding: utf-8 -*-
"""Comprehensive verification for 90 social transitions (150 total transitions) & companion assets."""
from __future__ import annotations

import hashlib
import json
import os
import time

import numpy as np


def _pattern_image(w: int, h: int, c1: tuple[int, int, int], c2: tuple[int, int, int], horizontal: bool = True):
    from PySide6.QtGui import QColor, QImage, QPainter
    img = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(QColor(*c1))
    p = QPainter(img)
    p.fillRect(
        0 if horizontal else w // 3,
        h // 3 if horizontal else 0,
        w if horizontal else w // 3,
        h // 3 if horizontal else h,
        QColor(*c2),
    )
    p.end()
    return img


def _img_to_rgba(img) -> np.ndarray:
    from PySide6.QtGui import QImage
    conv = img.convertToFormat(QImage.Format.Format_RGBA8888)
    w, h = conv.width(), conv.height()
    bpl = conv.bytesPerLine()
    return np.frombuffer(conv.constBits(), dtype=np.uint8).reshape((h, bpl))[:, : w * 4].reshape((h, w, 4)).copy()


def test_companion_transition_assets_and_provenance():
    from cutora.render import transitions_social
    assets_dir = os.path.normpath(
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "resources", "transition_assets")
    )
    assert os.path.isdir(assets_dir), f"missing transition_assets dir: {assets_dir}"
    prov_path = os.path.join(assets_dir, "PROVENANCE.json")
    assert os.path.isfile(prov_path), "missing PROVENANCE.json in resources/transition_assets"
    data = json.load(open(prov_path, encoding="utf-8"))
    files = data.get("files") or []
    expected_names = {
        "anamorphic_flare.png",
        "warm_light_leak.png",
        "film_burn_overlay.png",
        "bokeh_crystal_overlay.png",
        "ink_wash_mask.png",
        "paper_tear_edge.png",
        "glass_shards_map.png",
        "speed_lines_mask.png",
        "cyber_hud_grid.png",
    }
    found_names = {entry["file"] for entry in files}
    assert expected_names.issubset(found_names), f"missing assets in PROVENANCE.json: {expected_names - found_names}"

    for entry in files:
        fn = entry["file"]
        fp = os.path.join(assets_dir, fn)
        assert os.path.isfile(fp), f"missing asset file {fp}"
        raw = open(fp, "rb").read()
        assert len(raw) == entry["bytes"]
        assert hashlib.sha256(raw).hexdigest() == entry["sha256"]
        qimg = transitions_social._load_asset_image(fn)
        assert qimg is not None and not qimg.isNull()
        assert [qimg.width(), qimg.height()] == entry["size"]


def test_default_durations_and_150_catalog_entries():
    from cutora.audio import sfx_social
    from cutora.render import anim_engine, transition_catalog, transitions_social

    assert abs(anim_engine.DEFAULT_TRANSITION_DURATION - 1.2) < 1e-6
    assert abs(anim_engine.STANDALONE_TRANSITION_DEFAULT_DUR - 1.2) < 1e-6
    assert abs(anim_engine.AUDIO_TRANSITION_DEFAULT_DUR - 0.80) < 1e-6
    assert abs(anim_engine.AUDIO_SEAM_FADE_DUR - 0.030) < 1e-6
    assert abs(transition_catalog.DEFAULT_META["dur"] - 1.2) < 1e-6

    assert len(transitions_social.SOCIAL_TRANSITION_LIBRARY) == 90
    assert len(transitions_social.SOCIAL_TRANSITION_FUNCS) == 90
    assert len(anim_engine.STANDALONE_TRANSITION_LIBRARY) == 145
    assert len(anim_engine.AUDIO_TRANSITION_LIBRARY) == 5
    assert len(anim_engine.STANDALONE_TRANSITION_LIBRARY) + len(anim_engine.AUDIO_TRANSITION_LIBRARY) == 150
    assert len(transition_catalog.TRANSITION_META) >= 150

    valid_sfx_ids = {item["id"] for item in sfx_social.CATALOG}
    cat_counts = {"camera": 0, "rhythm": 0, "graphic": 0, "light": 0}

    for label, preset in transitions_social.SOCIAL_TRANSITION_LIBRARY:
        assert label and preset
        assert preset in transition_catalog.TRANSITION_META, f"missing {preset} in TRANSITION_META"
        assert preset in transitions_social.SOCIAL_TRANSITION_FUNCS, f"missing {preset} in SOCIAL_TRANSITION_FUNCS"
        meta = transition_catalog.meta_of(preset)
        cat = meta["category"]
        assert cat in cat_counts, f"unexpected category {cat} for {preset}"
        cat_counts[cat] += 1
        dur = transition_catalog.recommended_duration(preset)
        assert 0.90 <= dur <= 2.00, f"{preset} duration {dur} out of [0.90, 2.00]"
        sfx_id = transition_catalog.recommended_sfx(preset)
        assert sfx_id in valid_sfx_ids, f"{preset} has invalid sfx {sfx_id!r}"
        assert meta.get("keywords"), f"{preset} missing keywords"

    assert cat_counts == {"camera": 28, "rhythm": 22, "graphic": 21, "light": 19}

    for _lab, preset in anim_engine.STANDALONE_TRANSITION_LIBRARY:
        assert preset in transition_catalog.TRANSITION_META
        dur = transition_catalog.recommended_duration(preset)
        assert 0.90 <= dur <= 2.00, f"visual preset {preset} dur {dur} too short/long"


def test_all_90_social_transitions_render_continuity_opacity_and_speed():
    from cutora.render import transitions, transitions_social

    w, h = 128, 72
    a = _pattern_image(w, h, (225, 70, 45), (250, 200, 70), horizontal=True)
    b = _pattern_image(w, h, (25, 145, 220), (80, 240, 175), horizontal=False)
    arr_a = _img_to_rgba(a)
    arr_b = _img_to_rgba(b)

    t0 = time.perf_counter()
    for _label, preset in transitions_social.SOCIAL_TRANSITION_LIBRARY:
        fn = transitions_social.SOCIAL_TRANSITION_FUNCS[preset]
        f_start = fn(0.0, a, b, w, h)
        f_near0 = fn(0.02, a, b, w, h)
        f_mid = fn(0.5, a, b, w, h, direction="left", intensity=1.0)
        f_near1 = fn(0.98, a, b, w, h)
        f_end = fn(1.0, a, b, w, h)

        for frame in (f_start, f_near0, f_mid, f_near1, f_end):
            assert frame is not None and not frame.isNull(), f"{preset} returned null frame"
            assert frame.width() == w and frame.height() == h
            rgba = _img_to_rgba(frame)
            assert np.all(rgba[..., 3] == 255), f"{preset} produced non-opaque alpha pixels"

        assert np.array_equal(_img_to_rgba(f_start), arr_a), f"{preset} t=0.0 != a"
        assert np.array_equal(_img_to_rgba(f_end), arr_b), f"{preset} t=1.0 != b"

        mid_rgba = _img_to_rgba(f_mid)
        diff_a = np.abs(mid_rgba[..., :3].astype(np.float32) - arr_a[..., :3].astype(np.float32)).mean()
        diff_b = np.abs(mid_rgba[..., :3].astype(np.float32) - arr_b[..., :3].astype(np.float32)).mean()
        assert diff_a > 1.0 or diff_b > 1.0, f"{preset} midpoint is static"

        via_apply = transitions.apply_transition(preset, a, b, 0.5, w, h)
        assert via_apply is not None and not via_apply.isNull()

    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    avg_ms_per_preset = elapsed_ms / len(transitions_social.SOCIAL_TRANSITION_LIBRARY)
    assert avg_ms_per_preset < 25.0, f"rendering 6 frames per preset too slow: {avg_ms_per_preset:.2f} ms"


def test_multistage_camera_choreography_4stage_curve():
    """Verify the 4-stage push-drift-pull-settle curve has smooth endpoints and distinct stages."""
    from cutora.render import transitions_social
    s1_0, s2_0, s3_0, s4_0, bm_0 = transitions_social._choreo_4stage(0.0)
    assert (s1_0, s2_0, s3_0, s4_0, bm_0) == (0.0, 0.0, 0.0, 0.0, 0.0)

    s1_1, s2_1, s3_1, s4_1, bm_1 = transitions_social._choreo_4stage(1.0)
    assert (s1_1, s2_1, s3_1, s4_1, bm_1) == (1.0, 1.0, 1.0, 1.0, 1.0)

    # At p=0.42 (middle of Stage 2 '缓缓的动'), Stage 1 is finished (1.0), Stage 2 is ~0.5, Stage 3 hasn't started (0.0)
    s1_m, s2_m, s3_m, s4_m, bm_m = transitions_social._choreo_4stage(0.42)
    assert s1_m == 1.0
    assert 0.3 < s2_m < 0.7
    assert s3_m == 0.0
    assert s4_m == 0.0
    assert 0.2 < bm_m < 0.8


def test_overlay_standalone_transition_preserves_transparent_background():
    """Verify apply_overlay_standalone_transition preserves alpha=0 on transparent corners for light/overlay presets."""
    from PySide6.QtGui import QColor, QImage, QPainter
    from cutora.render import anim_engine, transitions_social

    w, h = 96, 54
    layer = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    layer.fill(0)
    p = QPainter(layer)
    p.fillRect(w // 4, h // 4, w // 2, h // 2, QColor(240, 120, 60, 255))
    p.end()

    for preset in transitions_social.SOCIAL_OVERLAY_LIGHT_PRESETS:
        res = anim_engine.apply_overlay_standalone_transition(
            layer, current_time=0.5, seg_start=0.0, seg_end=2.0, preset=f"{preset}_in", duration=1.0
        )
        rgba = _img_to_rgba(res)
        # Corner (0, 0) was transparent in original overlay and must remain transparent
        assert rgba[0, 0, 3] == 0, f"{preset} leaked opaque background onto transparent overlay corner"

