# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for '添加字幕轨道' + Subtitle Track Inspector (source track selection, auto ASR, manual paste, proofreading)."""
from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from cutora.model.timeline import Timeline
from cutora.model.timeline_model import Clip, MediaSource, Track
from cutora.subtitle.track_source import (
    get_track_manual_script,
    get_track_segments,
    get_track_source_id,
)
from cutora.subtitle.track_transcribe import (
    MAIN_SOURCE_ID,
    build_segments_from_manual_text,
    list_transcribe_source_tracks,
    transcribe_track_to_segments,
)


def _app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(sys.argv)


def _make_sample_task():
    tl = Timeline(1080, 1920, 30.0)
    src_main = MediaSource(path="main_video.mp4", duration=12.0)
    src_sec = MediaSource(path="broll_voice.mp4", duration=8.0)
    src_aud = MediaSource(path="voiceover.mp3", duration=10.0)
    tl.sources[src_main.id] = src_main
    tl.sources[src_sec.id] = src_sec
    tl.sources[src_aud.id] = src_aud

    tr_v1 = Track("video")
    tr_v1.is_main_video = True
    tr_v1.name = "主视频"
    tr_v1.clips.append(Clip(source_id=src_main.id, in_t=0.0, out_t=12.0, start_t=0.0))
    tl.add_track(tr_v1)

    tr_v2 = Track("video")
    tr_v2.name = "画中画解说"
    tr_v2.clips.append(Clip(source_id=src_sec.id, in_t=1.0, out_t=7.0, start_t=2.0))
    tl.add_track(tr_v2)

    tr_a1 = Track("audio")
    tr_a1.name = "旁白配音"
    tr_a1.clips.append(Clip(source_id=src_aud.id, in_t=0.0, out_t=9.0, start_t=0.5))
    tl.add_track(tr_a1)

    tr_sub = Track("subs")
    tl.add_track(tr_sub)

    task = {
        "video": "main_video.mp4",
        "duration": 12.0,
        "dims": (1080, 1920),
        "timeline": tl,
        "segments": [],
        "text_overlays": [],
        "image_overlays": [],
    }
    return task, tr_v1, tr_v2, tr_a1, tr_sub


def test_list_source_tracks_and_transcribe_specific_track():
    task, tr_v1, tr_v2, tr_a1, tr_sub = _make_sample_task()
    items = list_transcribe_source_tracks(task)
    ids = [sid for sid, _lbl in items]
    assert ids[0] == MAIN_SOURCE_ID
    assert tr_v1.id in ids
    assert tr_v2.id in ids
    assert tr_a1.id in ids

    # Transcribe secondary video track (tr_v2 starts at timeline t=2.0, in_t=1.0..7.0)
    def fake_runner(path, in_t, out_t, lang):
        assert "broll_voice.mp4" in path
        assert abs(in_t - 1.0) < 1e-6
        assert abs(out_t - 7.0) < 1e-6
        words = [
            {"start": 1.2, "end": 1.6, "text": "check"},
            {"start": 1.65, "end": 2.0, "text": "this"},
            {"start": 2.05, "end": 2.5, "text": "feature"},
            {"start": 3.5, "end": 4.0, "text": "it"},
            {"start": 4.05, "end": 4.6, "text": "works"},
        ]
        return words, []

    segs = transcribe_track_to_segments(
        task, tr_sub, source_track_id=tr_v2.id, transcribe_fn=fake_runner
    )
    assert get_track_source_id(tr_sub) == tr_v2.id
    assert len(segs) == 2
    # Punctuation automatically added
    assert segs[0]["text"].endswith(".")
    assert segs[1]["text"].endswith(".")
    # Mapped to timeline time (tl_start=2.0 + (1.2 - 1.0) = 2.2)
    assert abs(segs[0]["start"] - 2.2) < 0.05
    assert len(task["segments"]) == 2


def test_manual_paste_plain_and_srt_and_inspector_proofreading():
    _app()
    task, tr_v1, tr_v2, tr_a1, tr_sub = _make_sample_task()

    # 1. Plain multi-line manual paste
    manual_text = "First line without period\nSecond line already has period."
    segs = build_segments_from_manual_text(
        manual_text, task=task, sub_track=tr_sub, source_track_id=tr_a1.id
    )
    assert len(segs) == 2
    assert segs[0]["text"] == "First line without period."
    assert segs[1]["text"] == "Second line already has period."
    assert get_track_manual_script(tr_sub) == manual_text

    # 2. SRT format manual paste
    srt_text = (
        "1\n"
        "00:00:01,000 --> 00:00:03,200\n"
        "Welcome to the new subtitle track\n\n"
        "2\n"
        "00:00:03,500 --> 00:00:06,000\n"
        "You can inspect and edit every line.\n"
    )
    segs_srt = build_segments_from_manual_text(
        srt_text, task=task, sub_track=tr_sub, source_track_id=MAIN_SOURCE_ID
    )
    assert len(segs_srt) == 2
    assert abs(segs_srt[0]["start"] - 1.0) < 1e-3
    assert abs(segs_srt[0]["end"] - 3.2) < 1e-3
    assert segs_srt[0]["text"] == "Welcome to the new subtitle track."

    # 3. PropertyPanel inspector loads and edits subtitle track cues
    from cutora.ui.panels.property import PropertyPanel
    panel = PropertyPanel()
    panel._win_and_task = lambda: (None, task)
    panel.load_data("track", tr_sub)
    assert not panel.sec_sub_track.isHidden()
    assert panel.trk_sub_source_combo.count() >= 4
    assert panel.trk_sub_cue_table.rowCount() == 2

    # Edit row 0 text directly in the proofreading table
    panel.trk_sub_cue_table.item(0, 2).setText("Corrected first subtitle sentence.")
    updated = get_track_segments(task, tr_sub)
    assert updated[0]["text"] == "Corrected first subtitle sentence."
    assert task["segments"][0]["text"] == "Corrected first subtitle sentence."
