# -*- coding: utf-8 -*-
"""Q35: export platform presets (TikTok / Reels / Shorts). Suggest only, never lock."""
from __future__ import annotations

import os

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

REQUIRED_FIELDS = (
    "id",
    "name",
    "canvases",
    "max_duration_sec",
    "filename_suffix",
)

EXPECTED = {
    "tiktok": {
        "name": "TikTok",
        "canvases": ((1080, 1920), (720, 1280)),
        "max_duration_sec": 600.0,
    },
    "reels": {
        "name": "Instagram Reels",
        "canvases": ((1080, 1920),),
        "max_duration_sec": 90.0,
    },
    "shorts": {
        "name": "YouTube Shorts",
        "canvases": ((1080, 1920),),
        "max_duration_sec": 180.0,
    },
    "youtube": {
        "name": "YouTube 横屏",
        "canvases": ((1920, 1080),),
        "max_duration_sec": None,
    },
    "custom": {
        "name": "自定义",
        "canvases": (),
        "max_duration_sec": None,
    },
}


def _ui_export_src() -> str:
    return open(
        os.path.join(REPO, "cutora", "ui", "main_window.py"), encoding="utf-8"
    ).read()


def test_q35_presets_table():
    from cutora.render.export_platform import (
        DEFAULT_ID,
        PLATFORMS,
        get_platform,
        list_platforms,
        select_platform,
    )

    assert isinstance(PLATFORMS, dict), "presets must be one dict, not UI literals"
    rows = list_platforms()
    ids = [r["id"] for r in rows]
    assert ids == ["tiktok", "reels", "shorts", "youtube", "custom"], ids
    assert DEFAULT_ID == "tiktok"

    for pid, spec in EXPECTED.items():
        row = get_platform(pid)
        missing = [k for k in REQUIRED_FIELDS if k not in row]
        assert missing == [], "%s missing fields %s" % (pid, missing)
        assert row["id"] == pid
        assert row["name"] == spec["name"], (pid, row["name"])
        assert tuple(row["canvases"]) == spec["canvases"], (pid, row["canvases"])
        got_lim = row["max_duration_sec"]
        want_lim = spec["max_duration_sec"]
        if want_lim is None:
            assert got_lim is None, (pid, got_lim)
        else:
            assert float(got_lim) == float(want_lim), (pid, got_lim)
        if pid == "custom":
            assert row["filename_suffix"] in (None, ""), row["filename_suffix"]
        else:
            suf = str(row["filename_suffix"] or "")
            assert suf.startswith("_"), (pid, suf)

    current_wh = (999, 111)
    current_suffix = "_KEEPME"
    sug = select_platform(
        "custom",
        current_wh=current_wh,
        current_suffix=current_suffix,
        duration_sec=9999.0,
    )
    assert sug["canvas_wh"] == current_wh
    assert sug["filename_suffix"] == current_suffix
    assert sug["duration_warning"] == ""
    assert sug["canvas_mismatch"] is False
    assert sug.get("changed") is False

    ui = _ui_export_src()
    assert "export_platform" in ui
    assert "list_platforms" in ui
    assert "平台" in ui
    for name in ("Instagram Reels", "YouTube Shorts", "YouTube 横屏"):
        assert name not in ui, "platform names must live in export_platform.py, not UI: %s" % name


def test_q35_no_crf_change(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "cutora.core.history._get_app_settings_path",
        lambda: str(tmp_path / "app_settings.json"),
    )
    from cutora.render.export_platform import list_platforms, set_active_platform_id
    from cutora.render.export_profile import encode_args, resolve_profile

    before = resolve_profile()
    before_enc = encode_args(before, False)
    before_fields = {
        "quality": before.get("quality"),
        "vcodec": before.get("vcodec"),
        "container": before.get("container"),
    }
    crf_idx = list(before_enc[2] or []).index("-crf") if "-crf" in (before_enc[2] or []) else None
    before_crf = (before_enc[2] or [])[crf_idx + 1] if crf_idx is not None else None
    before_preset = before_enc[1]

    for row in list_platforms():
        set_active_platform_id(row["id"])
        after = resolve_profile()
        after_enc = encode_args(after, False)
        assert after.get("quality") == before_fields["quality"], row["id"]
        assert after.get("vcodec") == before_fields["vcodec"], row["id"]
        assert after.get("container") == before_fields["container"], row["id"]
        assert after_enc[1] == before_preset, (row["id"], after_enc[1], before_preset)
        assert after_enc[2] == before_enc[2], (row["id"], after_enc[2], before_enc[2])
        if crf_idx is not None:
            assert (after_enc[2] or [])[crf_idx + 1] == before_crf, row["id"]


def test_q35_duration_warning():
    from cutora.render.export_platform import duration_warning, select_platform

    over = duration_warning("reels", 100.0)
    under = duration_warning("reels", 89.0)
    assert over, "Reels + 100s must warn"
    assert "90" in over
    assert under == "", "Reels + 89s must not warn: %r" % under
    assert duration_warning("reels", 90.0) == ""

    over_sug = select_platform("reels", current_wh=(1080, 1920), duration_sec=100.0)
    under_sug = select_platform("reels", current_wh=(1080, 1920), duration_sec=89.0)
    assert over_sug["duration_warning"]
    assert under_sug["duration_warning"] == ""
    assert over_sug.get("block_export") is not True

    ui = _ui_export_src()
    assert "duration_warning" in ui
    assert "statusBar" in ui


def test_q35_canvas_prompt_not_auto():
    from cutora.model.canvas import apply_canvas, canvas_wh
    from cutora.render.export_platform import (
        canvas_mismatch_message,
        select_platform,
    )

    task = {"dims": (720, 1280)}
    apply_canvas(task, 720, 1280)
    assert canvas_wh(task) == (720, 1280)

    sug = select_platform("youtube", current_wh=canvas_wh(task), duration_sec=10.0)
    assert canvas_wh(task) == (720, 1280), "selecting a platform must not mutate canvas"
    assert sug["canvas_mismatch"] is True
    assert sug["canvas_wh"] == (1920, 1080)
    msg = canvas_mismatch_message("youtube", (720, 1280))
    assert msg
    assert "720" in msg and "1280" in msg
    assert "1920" in msg and "1080" in msg
    assert "要改成" in msg

    match = select_platform("tiktok", current_wh=(720, 1280), duration_sec=10.0)
    assert match["canvas_mismatch"] is False
    assert match["canvas_wh"] == (720, 1280)

    ui = _ui_export_src()
    assert "_prompt_canvas_if_platform_mismatch" in ui
    assert "要改成平台尺寸吗" in ui or "canvas_mismatch_message" in ui
    # Combo change must not call _set_canvas_size; only the Yes branch of the prompt may.
    start = ui.find("def open_export_settings")
    end = ui.find("\n    def open_font_manager")
    settings_chunk = ui[start:end] if start >= 0 else ""
    assert "list_platforms" in settings_chunk
    assert "_set_canvas_size" not in settings_chunk
