# -*- coding: utf-8 -*-
"""T20c: standalone transition library actually composes; export uses start_t."""
from __future__ import annotations

from PySide6.QtGui import QColor, QImage, QPainter

from cutora.render import anim_engine
from cutora.model.timeline_model import Clip, TransitionItem


def _solid(w, h, rgb):
    img = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(QColor(*rgb))
    return img


def test_t20c_library_all_compose():
    names = [p for _lab, p in anim_engine.STANDALONE_TRANSITION_LIBRARY]
    assert "cross_dissolve" in names
    assert "fade_dark" in names
    assert "wipe_left" in names
    out_img = _solid(64, 64, (255, 0, 0))
    in_img = _solid(64, 64, (0, 0, 255))
    for preset in names:
        comp = anim_engine.compose_standalone_transition(
            0.5, preset, out_img, in_img, 64, 64)
        assert comp is not None and not comp.isNull(), preset
        assert comp.width() == 64 and comp.height() == 64


def test_t20c_fade_dark_is_not_pure_black():
    assert anim_engine.FADE_DARK_RGB == (0x0B, 0x0B, 0x0E)


def test_t20c_export_uses_start_t_not_start_time():
    src = open(
        __file__.replace("tests\\test_t20c_transitions.py", "cutora/render/render_worker.py")
        .replace("tests/test_t20c_transitions.py", "cutora/render/render_worker.py"),
        encoding="utf-8",
    ).read()
    assert 'getattr(clip, "start_t"' in src
    assert "_paint_export_transition" in src
    assert "compose_standalone_transition" in src or "paint_standalone_transition" in src


def test_t20c_transition_item_window():
    t = TransitionItem(start_t=2.0, duration=0.35, preset="cross_dissolve")
    assert abs(t.end_t - 2.35) < 1e-9
    c = Clip("s", 0, 4, 0)
    assert abs(c.start_t) < 1e-9


def test_social_transitions_40_catalog_and_render():
    from cutora.audio import sfx_social
    from cutora.render import transition_catalog, transitions, transitions_social

    assert len(transitions_social.SOCIAL_TRANSITION_LIBRARY) == 40
    valid_sfx_ids = {item["id"] for item in sfx_social.CATALOG}

    cat_counts = {"camera": 0, "rhythm": 0, "graphic": 0, "light": 0}
    dur_ranges = {
        "camera": (0.60, 1.00),
        "rhythm": (0.30, 0.50),
        "graphic": (0.50, 0.80),
        "light": (0.50, 0.90),
    }

    a = _solid(128, 72, (230, 80, 45))
    b = _solid(128, 72, (25, 165, 220))

    for label, preset in transitions_social.SOCIAL_TRANSITION_LIBRARY:
        assert label and preset
        assert preset in transition_catalog.TRANSITION_META, f"missing meta for {preset}"
        meta = transition_catalog.meta_of(preset)
        cat = meta["category"]
        assert cat in cat_counts, f"unexpected category {cat} for {preset}"
        cat_counts[cat] += 1
        lo, hi = dur_ranges[cat]
        dur = transition_catalog.recommended_duration(preset)
        assert lo - 1e-6 <= dur <= hi + 1e-6, f"{preset} dur {dur} not in [{lo}, {hi}]"
        sfx_id = transition_catalog.recommended_sfx(preset)
        if sfx_id:
            assert sfx_id in valid_sfx_ids, f"invalid sfx {sfx_id} for {preset}"
        assert meta.get("keywords"), f"missing keywords for {preset}"

        # Direct function call with (t, a, b, w, h, direction, intensity)
        fn = transitions_social.SOCIAL_TRANSITION_FUNCS[preset]
        f0 = fn(0.0, a, b, 128, 72)
        f5 = fn(0.5, a, b, 128, 72, direction="left", intensity=1.0)
        f1 = fn(1.0, a, b, 128, 72)
        assert f0 is not None and not f0.isNull()
        assert f5 is not None and not f5.isNull()
        assert f1 is not None and not f1.isNull()
        assert f5.width() == 128 and f5.height() == 72

        # Via transitions.apply_transition(preset, a, b, t, w, h)
        via_apply = transitions.apply_transition(preset, a, b, 0.5, 128, 72)
        assert via_apply is not None and not via_apply.isNull()

    assert cat_counts == {"camera": 12, "rhythm": 10, "graphic": 10, "light": 8}

    # Check all STANDALONE_TRANSITION_LIBRARY and AUDIO_TRANSITION_LIBRARY are in TRANSITION_META
    for _lab, preset in anim_engine.STANDALONE_TRANSITION_LIBRARY:
        assert preset in transition_catalog.TRANSITION_META, f"missing visual preset {preset} in TRANSITION_META"
        sfx_id = transition_catalog.recommended_sfx(preset)
        if sfx_id:
            assert sfx_id in valid_sfx_ids
    for _lab, preset in anim_engine.AUDIO_TRANSITION_LIBRARY:
        assert preset in transition_catalog.TRANSITION_META, f"missing audio preset {preset} in TRANSITION_META"
        assert transition_catalog.category_of(preset) == "audio"

    hot_presets = [k for k, v in transition_catalog.TRANSITION_META.items() if v.get("hot")]
    assert 16 <= len(hot_presets) <= 20, f"expected 16~20 hot presets, got {len(hot_presets)}"


def test_social_transition_recommended_duration_and_auto_sfx(tmp_path, monkeypatch):
    from cutora.audio import sfx_library, sfx_social
    from cutora.model.timeline_model import Track
    from cutora.render.transition_catalog import recommended_duration, recommended_sfx
    from cutora.ui.command_bodies import CommandBodies

    settings_file = tmp_path / "settings.json"
    monkeypatch.setattr(sfx_library, "_settings_path", lambda: str(settings_file))

    class _DummyTimeline:
        def __init__(self):
            self.fps = 30.0
            self.width = 1920
            self.height = 1080
            self.tracks = []
            self.sources = {}

    class _DummyTimelineWidget:
        def update(self):
            pass

    class _DummyHost(CommandBodies):
        def __init__(self, tl):
            self.curr_idx = 0
            self.tasks = [{"video": "dummy.mp4", "timeline": tl}]
            self.timeline = _DummyTimelineWidget()

        def _require_unlocked(self, tr, track_id):
            pass

    tl = _DummyTimeline()
    vtrack = Track("video")
    vtrack.clips = [
        Clip("src1", 0.0, 3.0, 0.0),
        Clip("src2", 0.0, 3.0, 3.0),
    ]
    tl.tracks = [vtrack]
    host = _DummyHost(tl)

    sfx_social.set_enabled(True)
    assert sfx_social.is_enabled() is True

    res = host.command_add_transition(
        track_id=vtrack.id,
        start_t=2.55,
        duration=0.0,  # should auto-resolve to recommended_duration("cam_dolly_vertigo") == 0.90
        preset="cam_dolly_vertigo",
        domain="visual",
    )
    assert abs(res["duration"] - recommended_duration("cam_dolly_vertigo")) < 1e-6
    assert len(vtrack.transitions) == 1
    tr_item = vtrack.transitions[0]
    assert recommended_sfx("cam_dolly_vertigo") == "riser_short"

    key = sfx_social.transition_key(vtrack, tr_item)
    mid = tr_item.start_t + tr_item.duration * 0.5
    hits = sfx_social._find_existing(tl, key, mid)
    assert len(hits) == 1, "expected auto-attached SFX clip when sfx_social is enabled"
    assert getattr(hits[0][1], "transition_sfx_id", "") == "riser_short"

    # Switching preset updates SFX
    host.command_set_transition(
        track_id=vtrack.id,
        transition_id=tr_item.id,
        preset="beat_bass_pump",
    )
    hits2 = sfx_social._find_existing(tl, key, mid)
    assert len(hits2) == 1
    assert getattr(hits2[0][1], "transition_sfx_id", "") == "bass_drop"

    # Deleting transition detaches SFX clip
    host.command_delete_transition(track_id=vtrack.id, transition_id=tr_item.id)
    hits3 = sfx_social._find_existing(tl, key, mid)
    assert len(hits3) == 0


