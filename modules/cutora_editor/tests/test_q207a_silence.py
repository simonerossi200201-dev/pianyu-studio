# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q207a: word-gap silences, ripple delete with the linked picture, word seek."""
from __future__ import annotations

import inspect
import os
import sys

if "PySide6" not in sys.modules:
    from cutora.core.runtime import init_torch_runtime
    init_torch_runtime(render_child=False)


def _span_sum(track):
    from cutora.ui.timeline.snap import clip_span
    total = 0.0
    for clip in getattr(track, "clips", []) or []:
        start, end = clip_span(clip)
        total += end - start
    return total


def _linked_task():
    from cutora.model.timeline import Timeline
    from cutora.model.timeline_model import Clip, MediaSource, Track
    tl = Timeline(1080, 1920, 30.0)
    src = MediaSource("voice.wav", duration=4.0, probe=False)
    tl.sources[src.id] = src
    video = Track("video")
    voice = Track("audio")
    voice.audio_role = "main"
    bed = Track("audio")
    bed.audio_role = "bgm"
    vc = Clip(src.id, 0.0, 4.0, 0.0)
    ac = Clip(src.id, 0.0, 4.0, 0.0)
    ac.audio_role = "main"
    ac.is_main = True
    bc = Clip(src.id, 0.0, 4.0, 0.0)
    bc.audio_role = "bgm"
    vc.link_group_id = ac.link_group_id = "g1"
    video.clips.append(vc)
    voice.clips.append(ac)
    bed.clips.append(bc)
    tl.add_track(video)
    tl.add_track(voice)
    tl.add_track(bed)
    task = {
        "timeline": tl,
        "segments": [{
            "start": 0.0,
            "end": 4.0,
            "text": "one two three",
            "words": [
                {"word": "one", "start": 0.0, "end": 0.4},
                {"word": "two", "start": 1.6, "end": 2.0},
                {"word": "three", "start": 2.0, "end": 3.5},
            ],
        }],
    }
    return task, video, voice, bed


def test_words_find_pause_and_short_gap_stays():
    from cutora.audio.silence import detect_silences, pauses_from_words
    task, _v, _a, _b = _linked_task()
    assert "analysis" not in task
    rows = detect_silences(task)
    assert rows == [{"start": 0.4, "end": 1.6}]
    short = pauses_from_words(
        [{"start": 0.0, "end": 0.2}, {"start": 0.8, "end": 1.0}], 0.55)
    assert short == [{"start": 0.2, "end": 0.8}]
    task["analysis"] = {"silences": short}
    from cutora.audio.silence import remove_silences
    assert remove_silences(task, min_dur=0.8) == 0
    assert abs(_span_sum(task["timeline"].tracks[0]) - 4.0) < 1e-6


def test_remove_shrinks_linked_video_and_voice_not_bgm():
    from cutora.audio.silence import detect_silences, remove_silences
    from cutora.ui.command_bodies import CommandBodies
    task, video, voice, bed = _linked_task()

    class Win(CommandBodies):
        def __init__(self):
            self.tasks = [task]
            self.curr_idx = 0

    win = Win()
    out = win.command_detect_silence()
    assert out["found"] == 1
    gap = task["analysis"]["silences"][0]
    assert abs((gap["end"] - gap["start"]) - 1.2) < 1e-6
    removed = win.command_remove_silences(min_dur=0.8)
    assert removed["removed"] == 1
    assert "silences" not in (task.get("analysis") or {})
    assert abs(_span_sum(video) - 2.8) < 1e-6
    assert abs(_span_sum(voice) - 2.8) < 1e-6
    assert abs(_span_sum(bed) - 4.0) < 1e-6
    assert detect_silences({"segments": []}) == []


def test_parse_log_and_word_seek():
    from cutora.audio.silence import parse_silence_log, word_time_at_char
    text = "silence_start: 1.25\nsilence_end: 2.50 | silence_duration: 1.25\n"
    assert parse_silence_log(text) == [{"start": 1.25, "end": 2.5}]
    seg = {
        "text": "hello world",
        "words": [
            {"word": "hello", "start": 1.0, "end": 1.2},
            {"word": "world", "start": 1.5, "end": 1.8},
        ],
    }
    assert word_time_at_char(seg, 0) == 1.0
    assert word_time_at_char(seg, 8) == 1.5
    assert word_time_at_char({"text": "hi"}, 0) is None


def test_ui_hooks_and_no_torch_import():
    import cutora.audio.silence as silence
    from cutora.ui.panels.transcript import _Card
    from cutora.ui.timeline.widget import TimelineWidget
    body = inspect.getsource(silence)
    assert "import torch" not in body
    paint = inspect.getsource(TimelineWidget._paint_audio_track)
    assert "_paint_silence_shades" in paint
    menu = inspect.getsource(TimelineWidget._popup_audio_clip_menu)
    assert "删除此段静音" in menu
    assert "audio.remove_silences" in menu
    card = inspect.getsource(_Card)
    assert "_press_word" in card
    assert "setFocus" in inspect.getsource(_Card.mouseDoubleClickEvent)
    tab = inspect.getsource(
        __import__("cutora.ui.batch_audio_tab", fromlist=["BatchAudioTab"]).BatchAudioTab._build
    )
    assert "audio.detect_silence" in inspect.getsource(
        __import__("cutora.ui.batch_audio_tab", fromlist=["BatchAudioTab"]).BatchAudioTab.detect_silence
    )
    assert "删除" in tab or "btnRemoveSilence" in tab
