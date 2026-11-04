# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Unit tests for 200 Studio-Quality SFX, 20 Melodic Viral BGM beds, and Pre-Transition Audio Controls."""
from __future__ import annotations

import os
import numpy as np

from cutora.audio import bgm_beds, sfx_social
from cutora.model.timeline import Timeline
from cutora.model.timeline_model import Track, TransitionItem


def test_sfx_social_catalog_and_provenance():
    assert len(sfx_social.CATALOG) == 200
    assert sfx_social.GROUPS == [
        "转场音效",
        "自然天气",
        "江河流水",
        "鸟鸣生灵",
        "掌声欢笑",
        "城市火焰",
        "拟音场景",
    ]
    trans_items = [m for m in sfx_social.CATALOG if m["group"] == "转场音效"]
    scene_items = [m for m in sfx_social.CATALOG if m["group"] != "转场音效"]
    assert len(trans_items) == 60
    assert len(scene_items) == 140
    for m in trans_items:
        assert 2.0 <= float(m["duration"]) <= 3.0, (m["id"], m["duration"])
    for m in scene_items:
        assert float(m["duration"]) >= 8.0, (m["id"], m["duration"])

    assert sfx_social.missing_files() == []
    ok, errs = sfx_social.verify_provenance()
    assert ok, errs
    prov = sfx_social.load_provenance()
    assert prov.get("model", {}).get("id") == "stabilityai/stable-audio-open-1.0"
    assert "Stability AI Community License" in prov.get("license", "")
    assert os.path.isfile(os.path.join(sfx_social.SOCIAL_DIR, "LICENSE.txt"))


def test_sfx_social_audio_specs_and_peaks():
    for c in sfx_social.CATALOG:
        sid = c["id"]
        p = sfx_social.available_path(sid)
        assert p and os.path.isfile(p), sid
        st, sr, nch, sw = sfx_social.read_wav16(p)
        assert sr == 48000, (sid, sr)
        assert nch == 2, (sid, nch)
        assert sw == 2, (sid, sw)
        assert abs(st.shape[0] / sr - c["duration"]) <= 0.02, (sid, st.shape[0] / sr, c["duration"])
        assert np.all(np.isfinite(st)), sid
        pk = sfx_social.peak_dbfs(st)
        assert -24.0 <= pk <= -0.9, (sid, pk)
        sf = sfx_social.spectral_flatness(st)
        assert sf <= 0.22, (sid, sf)
        # Clean S-curve fade-in / fade-out (no click at boundaries)
        assert float(np.max(np.abs(st[0]))) <= 0.02, (sid, st[0])
        assert float(np.max(np.abs(st[-1]))) <= 0.02, (sid, st[-1])


def test_attach_and_detach_transition_sfx_idempotent():
    sfx_social.set_pre_transition_sfx_id("auto")
    tl = Timeline(1920, 1080, 30.0)
    vtr = Track("video")
    tl.add_track(vtr)
    trans = TransitionItem(start_t=2.0, duration=0.8, preset="cam_crash_push")
    vtr.transitions.append(trans)

    clip1 = sfx_social.attach_transition_sfx(tl, vtr, trans, "whoosh_heavy")
    assert clip1 is not None
    by_id = sfx_social.catalog_by_id()
    expected_start = 2.4 - float(by_id["whoosh_heavy"]["peak_offset"])
    assert abs(clip1.start_t - expected_start) < 1e-3
    assert 2.0 <= clip1.duration <= 3.0
    assert abs(clip1.fade_in - 0.35) < 1e-6
    assert abs(clip1.fade_out - 0.55) < 1e-6
    assert clip1.audio_role == "sfx"

    # Calling again with same sfx_id updates in-place without duplicating
    clip2 = sfx_social.attach_transition_sfx(tl, vtr, trans, "whoosh_heavy")
    assert clip2 is clip1
    sfx_tracks = [t for t in tl.tracks if getattr(t, "audio_role", None) == "sfx"]
    assert len(sfx_tracks) == 1
    assert len(sfx_tracks[0].clips) == 1

    # Switching sfx_id replaces the clip
    clip3 = sfx_social.attach_transition_sfx(tl, vtr, trans, "impact_punch")
    assert clip3 is not None
    assert len(sfx_tracks[0].clips) == 1
    assert clip3.transition_sfx_id == "impact_punch"
    assert sfx_social.active_transition_sfx_id(tl, vtr, trans) == "impact_punch"

    # Detach removes the clip and clears stale sfx_id unless 'none'
    removed = sfx_social.detach_transition_sfx(tl, vtr, trans)
    assert removed == 1
    assert len(sfx_tracks[0].clips) == 0
    assert sfx_social.active_transition_sfx_id(tl, vtr, trans) == ""

    # Explicit 'none' is preserved across detach
    trans.sfx_id = "none"
    assert sfx_social.active_transition_sfx_id(tl, vtr, trans) == "none"
    sfx_social.detach_transition_sfx(tl, vtr, trans)
    assert sfx_social.active_transition_sfx_id(tl, vtr, trans) == "none"


def test_place_transition_sfx_on_timeline():
    tl = Timeline(1920, 1080, 30.0)
    clip = sfx_social.place_transition_sfx_on_timeline(
        tl,
        transition_start=3.0,
        transition_dur=0.6,
        sfx_id="trans_harp_gliss_up",
    )
    assert clip is not None
    assert clip.audio_role == "sfx"
    assert 2.0 <= clip.duration <= 3.0
    assert abs(clip.fade_in - 0.35) < 1e-6
    assert abs(clip.fade_out - 0.55) < 1e-6


def test_iter_all_audio_library_items():
    items = sfx_social.iter_all_audio_library_items()
    assert len(items) == 220
    bgm_items = [x for x in items if x["audio_kind"] == "bgm"]
    sfx_items = [x for x in items if x["audio_kind"] == "sfx"]
    assert len(bgm_items) == 20
    assert len(sfx_items) == 200
    for item in items:
        assert os.path.isfile(item["path"]), item["id"]
        assert item["duration"] > 0


def test_preview_and_stop_preview_sfx():
    assert sfx_social.preview_sfx("nonexistent_sfx_id") is False
    assert sfx_social.preview_sfx("none") is False
    assert sfx_social.preview_sfx("whoosh_light") is True
    sfx_social.stop_preview_sfx()


def test_bgm_beds_catalog_and_provenance():
    assert len(bgm_beds.CATALOG) == 20
    assert bgm_beds.missing_files() == []
    ok, errs = bgm_beds.verify_provenance()
    assert ok, errs
    prov = bgm_beds.load_provenance()
    assert prov.get("model", {}).get("id") == "stabilityai/stable-audio-open-1.0"
    assert os.path.isfile(os.path.join(bgm_beds.BGM_DIR, "LICENSE.txt"))

    for c in bgm_beds.CATALOG:
        bid = c["id"]
        p = bgm_beds.available_path(bid)
        assert p and os.path.isfile(p), bid
        st, sr, nch, sw = bgm_beds.read_wav16(p)
        assert sr == 48000 and nch == 2 and sw == 2
        assert 12.0 <= float(c["duration"]) <= 16.0, (bid, c["duration"])
        pk = sfx_social.peak_dbfs(st)
        lufs = sfx_social.integrated_lufs(st)
        sf = sfx_social.spectral_flatness(st)
        assert -20.0 <= pk <= -0.9, (bid, pk)
        assert -20.0 <= lufs <= -12.0, (bid, lufs)
        assert sf <= 0.22, (bid, sf)


def test_place_bgm_bed_on_timeline():
    tl = Timeline(1920, 1080, 30.0)
    clip = bgm_beds.place_bgm_bed(tl, "bgm_upbeat_vlog_120", start_t=0.0, duration=30.0)
    assert clip is not None
    assert clip.audio_role == "bgm"
    assert clip.loop is True
    assert abs(clip.out_t - 30.0) < 1e-3

    clip2 = bgm_beds.place_bgm_bed(tl, "bgm_acoustic_sunny_vlog", start_t=5.0, duration=20.0)
    assert clip2 is not None
    assert clip2.audio_role == "bgm"


def test_transition_panel_pre_sfx_controls():
    from PySide6.QtWidgets import QApplication
    from cutora.ui.panels.transition_panel import TransitionPanel, set_sfx_enabled

    _app = QApplication.instance() or QApplication([])
    set_sfx_enabled(True)
    panel = TransitionPanel()
    assert hasattr(panel, "chk_auto_sfx")
    assert hasattr(panel, "pre_sfx_combo")
    assert hasattr(panel, "btn_pre_sfx_audition")
    assert panel.chk_auto_sfx.isChecked() is True
    assert panel.selected_pre_sfx_id() == "auto"
    # 1 ("auto") + 200 SFX + 1 ("none") = 202 entries
    assert panel.pre_sfx_combo.count() == 202

    panel.set_pre_sfx_id("trans_guzheng_gliss")
    assert panel.selected_pre_sfx_id() == "trans_guzheng_gliss"
    assert sfx_social.get_pre_transition_sfx_id() == "trans_guzheng_gliss"
    assert panel.effective_audition_sfx_id() == "trans_guzheng_gliss"
    sfx_social.set_pre_transition_sfx_id("auto")
