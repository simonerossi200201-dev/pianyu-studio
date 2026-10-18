# -*- coding: utf-8 -*-
"""Lock the portable=True whitelist. New commands default False; adding
a True flag without updating this set (or dropping a listed one) fails.
"""
from __future__ import annotations

from cutora.core.commands import get_registry, reset_bus_for_tests

# Commands whose params still make sense on a different video.
# Keep in sync with docs/plan/reports/T05b_portable名单.md
PORTABLE_WHITELIST = frozenset({
    "asr.detect_language",
    "audio.auto_fades",
    "audio.denoise",
    "audio.detect_silence",
    "audio.duck_set",
    "audio.loudness.set",
    "audio.remove_silences",
    "audio.sfx.apply_all",
    "audio.spatial.set",
    "audio.volume_master",
    "export.batch",
    "export.profile.list",
    "export.profile.save",
    "export.run",
    "media.import",
    "media.probe",
    "style.apply_keyword_preset",
    "style.apply_subtitle_preset",
    "style.apply_text_preset",
    "stylepack.apply",
    "subtitle.apply_glossary",
    "subtitle.apply_preset",
    "subtitle.auto",
    "subtitle.emphasis.auto",
    "subtitle.export_file",
    "subtitle.export_srt",
    "subtitle.extend_durations",
    "subtitle.relayout",
    "subtitle.replace",
    "subtitle.status",
    "subtitle.strip_end_punct",
    "subtitle.transform_case",
    "subtitle.word_appear.set",
    "text_preset.apply",
    "stitch.to_timeline",
})


def test_portable_true_equals_whitelist():
    reset_bus_for_tests()
    got = {s.name for s in get_registry().list() if s.portable}
    assert got == PORTABLE_WHITELIST, (
        "portable=True extra=%s missing=%s"
        % (sorted(got - PORTABLE_WHITELIST), sorted(PORTABLE_WHITELIST - got))
    )
    reset_bus_for_tests()


def test_record_allows_portable_even_if_not_undoable():
    from cutora.batch import recipe as recipe_mod
    rec = recipe_mod.get_recorder()
    rec.start()
    rec._on_executed({
        "name": "export.run", "source": "ui",
        "params": {"out_dir": r"D:\out"},
    })
    rec._on_executed({
        "name": "subtitle.auto", "source": "ui",
        "params": {"language": "auto"},
    })
    rec._on_executed({
        "name": "subtitle.delete_segments", "source": "ui",
        "params": {"seg_ids": [5, 12]},
    })
    steps = rec.stop()
    assert [s["command"] for s in steps] == ["export.run", "subtitle.auto"]
    assert steps[0]["params"]["out_dir"] == "{out_dir}"
    assert "subtitle.delete_segments" in rec.skipped


def test_marker_commands_stay_off_whitelist():
    reset_bus_for_tests()
    for name in (
        "marker.add", "marker.remove", "marker.rename",
        "marker.move", "marker.set_color",
        "timeline.ripple_delete", "timeline.ripple_insert",
        "timeline.close_gaps",
        "timeline.set_in_point", "timeline.set_out_point",
        "timeline.clear_io_points",
        "timeline.set_track_mute", "timeline.set_track_locked",
        "timeline.track.solo",
        "timeline.set_track_height",
        "track.set_props",
        "split.set_layout", "split.set_divider", "track.set_cell",
    ):
        assert name not in PORTABLE_WHITELIST
        assert get_registry().get(name).portable is False, name
    reset_bus_for_tests()
