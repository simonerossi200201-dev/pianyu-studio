# -*- coding: utf-8 -*-
"""T13a registry: count, doc_zh length, schemas, D1/D7 names."""
from __future__ import annotations

REQUIRED = [
    "app.version", "app.gpu_info", "app.free_vram",
    "app.settings.get", "app.settings.set",
    "app.theme.list", "app.theme.set",
    "project.new", "project.open", "project.save", "project.save_as",
    "project.close", "project.info",
    "project.autosave.status", "project.autosave.restore",
    "project.pack",
    "media.import", "media.remove", "media.list", "media.info", "media.select",
    "media.relink", "media.relink_folder",
    "subtitle.auto", "subtitle.status", "subtitle.list", "subtitle.set_text", "subtitle.merge",
    "subtitle.split", "subtitle.delete",
    "subtitle.trim_segment", "subtitle.trim_word", "subtitle.move_segments",
    "subtitle.retime_segments", "subtitle.delete_segments",
    "subtitle.duplicate_segments",
    "subtitle.apply_preset",
    "subtitle.import_file", "subtitle.export_file",
    "subtitle.relayout", "subtitle.check_cps", "subtitle.extend_durations",
    "subtitle.lock", "subtitle.find", "subtitle.replace",
    "subtitle.transform_case", "subtitle.strip_end_punct",
    "subtitle.apply_glossary", "subtitle.check_overlong",
    "subtitle.word_appear.set", "subtitle.emphasis.auto",
    "style.list_presets", "style.apply_subtitle_preset",
    "style.apply_text_preset", "style.apply_keyword_preset",
    "style.save_current_as",
    "stylepack.list", "stylepack.apply", "stylepack.import_file",
    "stylepack.export_file", "stylepack.save_current",
    "stylepack.delete", "stylepack.rename", "stylepack.update",
    "stylepack.convert_preset",
    "text_preset.list", "text_preset.save", "text_preset.delete", "text_preset.apply",
    "timeline.info", "timeline.add_track", "timeline.remove_track",
    "timeline.split_at", "timeline.join_clips", "timeline.add_transition",
    "timeline.set_transition", "timeline.delete_transition",
    "timeline.delete_clip", "timeline.move_clip",
    "timeline.trim_clip", "timeline.trim_clips",
    "timeline.unlink_clips", "timeline.link_clips",
    "timeline.set_clip_fades",
    "timeline.move_clips", "timeline.move_clip_to_track",
    "timeline.duplicate_clips",
    "timeline.ripple_intro",
    "timeline.ripple_delete", "timeline.ripple_insert", "timeline.close_gaps",
    "timeline.set_track_mute", "timeline.set_track_locked",
    "timeline.set_track_height",
    "track.set_props",
    "split.set_layout", "split.set_divider", "track.set_cell",
    "timeline.set_playhead",
    "timeline.set_in_point", "timeline.set_out_point", "timeline.clear_io_points",
    "marker.add", "marker.remove", "marker.rename", "marker.move", "marker.set_color",
    "overlay.set_fade", "overlay.insert_sticker", "overlay.split",
    "timeline.razor",
    "audio.spatial.set", "audio.sfx.apply_current", "audio.sfx.apply_all",
    "audio.volume_master", "audio.loudness.set",
    "export.settings", "export.run", "export.batch", "export.status",
    "export.profile.list", "export.profile.save", "export.profile.delete",
    "batch.stitch", "batch.import_media", "batch.auto_subtitle",
    "batch.save_project", "batch.split_project", "batch.queue_run",
    "batch.batch_export", "batch.add_text_overlay", "batch.add_bgm",
    "batch.merge_projects", "batch.scatter_broll", "batch.insert_image",
    "batch.batch_subtitles", "batch.split_and_queue_export",
    "batch.longtik_finish", "batch.queue.add",
    "batch.bgm_assign", "batch.copy_subtitle_style", "batch.share_tracks",
    "asr.detect_language",
    "asr.models.list", "asr.models.download", "asr.models.delete",
    "glossary.get", "glossary.set",
]


def test_registry_count_and_docs():
    from cutora.core.command_schema import check_schema
    from cutora.core.commands import get_registry
    specs = get_registry().list()
    names = {s.name for s in specs}
    assert len(specs) >= 60, len(specs)
    missing = [n for n in REQUIRED if n not in names]
    assert missing == [], missing
    for spec in specs:
        assert len(spec.doc_zh) >= 40, spec.name
        check_schema(spec.params_schema)
        check_schema(spec.returns_schema)


def test_batch_ops_are_headless():
    from cutora.core.commands import get_registry
    reg = get_registry()
    for name in (
        "batch.stitch", "batch.queue_run", "batch.auto_subtitle",
        "app.version", "app.settings.get",
    ):
        assert reg.get(name).headless is True, name
    assert reg.get("project.new").headless is False
    assert reg.get("subtitle.auto").headless is False
    assert reg.get("subtitle.auto").async_job is True
    assert reg.get("subtitle.status").headless is True
    assert reg.get("subtitle.import_file").headless is True
    assert reg.get("subtitle.export_file").headless is True
