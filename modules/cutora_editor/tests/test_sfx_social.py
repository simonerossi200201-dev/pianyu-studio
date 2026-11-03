# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Unit tests for Stable Audio Open 1.0 generated short-video SFX (30) and BGM beds (6)."""
from __future__ import annotations

import os
import numpy as np

from cutora.audio import bgm_beds, sfx_social
from cutora.model.timeline import Timeline
from cutora.model.timeline_model import Track, TransitionItem


def test_sfx_social_catalog_and_provenance():
    assert len(sfx_social.CATALOG) == 30
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
        # Clean fade-in / fade-out (no click at boundaries)
        assert float(np.max(np.abs(st[0]))) <= 0.02, (sid, st[0])
        assert float(np.max(np.abs(st[-1]))) <= 0.02, (sid, st[-1])


def test_attach_and_detach_transition_sfx_idempotent():
    tl = Timeline(1920, 1080, 30.0)
    vtr = Track("video")
    tl.add_track(vtr)
    trans = TransitionItem(start_t=2.0, duration=0.8, preset="cam_crash_push")
    vtr.transitions.append(trans)

    clip1 = sfx_social.attach_transition_sfx(tl, vtr, trans, "whoosh_heavy")
    assert clip1 is not None
    # Midpoint is 2.0 + 0.4 = 2.4s; whoosh_heavy peak_offset = 0.50s -> start_t = 1.90s
    assert abs(clip1.start_t - 1.90) < 1e-3
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

    # Detach removes the clip
    removed = sfx_social.detach_transition_sfx(tl, vtr, trans)
    assert removed == 1
    assert len(sfx_tracks[0].clips) == 0


def test_bgm_beds_catalog_and_provenance():
    assert len(bgm_beds.CATALOG) == 6
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
        pk = sfx_social.peak_dbfs(st)
        lufs = sfx_social.integrated_lufs(st)
        assert -20.0 <= pk <= -0.9, (bid, pk)
        assert -20.0 <= lufs <= -12.0, (bid, lufs)


def test_place_bgm_bed_on_timeline():
    tl = Timeline(1920, 1080, 30.0)
    clip = bgm_beds.place_bgm_bed(tl, "bgm_upbeat_vlog_120", start_t=0.0, duration=30.0)
    assert clip is not None
    assert clip.audio_role == "bgm"
    assert clip.loop is True
    assert abs(clip.out_t - 30.0) < 1e-3
