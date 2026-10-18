# -*- coding: utf-8 -*-
"""T08b: export profiles. Default equals current compressed encode."""
from __future__ import annotations

import json
import os
import subprocess

import pytest

from cutora.core.commands import CommandError, get_bus, reset_bus_for_tests
from cutora.render.export_params import build_rawvideo_ffmpeg_cmd, resolve_export_run_output
from cutora.render.export_profile import (
    DEFAULT_ID,
    delete_profile,
    encode_args,
    get_profile,
    list_profiles,
    save_profile,
)
from cutora.core.paths import FFMPEG, FFPROBE
from cutora.ui.command_bodies import CommandBodies


@pytest.fixture
def profile_store(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "cutora.render.export_profile.user_presets_dir", lambda: str(tmp_path))
    monkeypatch.setattr(
        "cutora.core.history._get_app_settings_path",
        lambda: str(tmp_path / "app_settings.json"))
    return tmp_path


def test_builtin_four_plus_source_size():
    ids = [p["id"] for p in list_profiles()]
    assert ids[:4] == [
        "social-vertical", "hevc-high", "archive-prores", "audio-m4a",
    ]
    assert "source-size" in ids
    prof = get_profile("source-size")
    assert "帧率" in prof["name"] and "采样率" in prof["name"]
    assert "不是画质" in prof["note"]
    assert "CRF 15" in prof["note"]


def test_default_profile_matches_legacy_compressed():
    legacy = build_rawvideo_ffmpeg_cmd(
        1080, 1920, 30, "a.mp4", use_gpu=False, ffmpeg_path="ffmpeg")
    social = build_rawvideo_ffmpeg_cmd(
        1080, 1920, 30, "a.mp4", use_gpu=False, ffmpeg_path="ffmpeg",
        profile=get_profile(DEFAULT_ID),
    )
    assert social == legacy
    assert "-c:v" in social
    assert social[social.index("-c:v") + 1] == "libx264"
    assert "-crf" in social and "18" in social


def test_hevc_and_prores_change_codec():
    h264 = build_rawvideo_ffmpeg_cmd(
        1080, 1920, 30, "a.mp4", use_gpu=False, ffmpeg_path="ffmpeg",
        profile=get_profile("social-vertical"),
    )
    hevc = build_rawvideo_ffmpeg_cmd(
        1080, 1920, 30, "a.mp4", use_gpu=False, ffmpeg_path="ffmpeg",
        profile=get_profile("hevc-high"),
    )
    prores = build_rawvideo_ffmpeg_cmd(
        1080, 1920, 30, "a.mov", use_gpu=False, ffmpeg_path="ffmpeg",
        profile=get_profile("archive-prores"),
    )
    assert h264[h264.index("-c:v") + 1] == "libx264"
    assert hevc[hevc.index("-c:v") + 1] == "libx265"
    assert prores[prores.index("-c:v") + 1] == "prores_ks"
    assert hevc != h264 != prores
    vcodec, _p, _e, pix = encode_args(get_profile("audio-m4a"), False)
    assert vcodec is None
    assert pix


def test_profile_changes_generated_path(tmp_path):
    task = {"video": os.path.join(str(tmp_path), "clip.mp4")}
    mp4 = resolve_export_run_output(
        task, out_dir=str(tmp_path / "a"), profile=get_profile("social-vertical"))
    m4a = resolve_export_run_output(
        task, out_dir=str(tmp_path / "b"), profile=get_profile("audio-m4a"))
    mov = resolve_export_run_output(
        task, out_dir=str(tmp_path / "c"), profile=get_profile("archive-prores"))
    assert mp4.endswith("_FINAL.mp4")
    assert m4a.endswith("_FINAL.m4a")
    assert mov.endswith("_FINAL.mov")


def test_save_and_delete_user_profile(profile_store):
    row = save_profile({
        "id": "mine.v1", "name": "我的", "container": "mkv",
        "vcodec": "hevc", "quality": "high",
    })
    assert row["id"] == "mine.v1"
    assert get_profile("mine.v1")["container"] == "mkv"
    with pytest.raises(ValueError):
        save_profile({"id": "social-vertical", "name": "x"})
    with pytest.raises(ValueError):
        delete_profile("social-vertical")
    delete_profile("mine.v1")
    with pytest.raises(KeyError):
        get_profile("mine.v1")


def test_commands_list_save_delete(profile_store):
    reset_bus_for_tests()
    bus = get_bus()
    listed = bus.execute("export.profile.list", {}, source="cli")
    assert listed["count"] >= 5
    saved = bus.execute(
        "export.profile.save",
        {"id": "user.one", "name": "U", "vcodec": "hevc"},
        source="cli",
    )
    assert saved["ok"] is True
    bus.execute("export.profile.delete", {"id": "user.one"}, source="cli")
    with pytest.raises(CommandError):
        bus.execute("export.profile.delete", {"id": "social-vertical"}, source="cli")
    reset_bus_for_tests()


class _Fake(CommandBodies):
    def __init__(self, tasks):
        self.tasks = list(tasks)
        self.curr_idx = 0
        self.exports = []

    def run_export_worker(self, task, target, is_batch=False):
        self.exports.append({"task": task, "out": target, "profile": task.get("_export_profile_id")})


def test_export_run_profile_lands_on_task_and_ext(tmp_path):
    task = {"video": str(tmp_path / "clip.mp4")}
    win = _Fake([task])
    out = win.command_export_run(out_dir=str(tmp_path / "dest"), profile="audio-m4a", allow_offline=True)
    assert out["profile"] == "audio-m4a"
    assert win.exports[0]["out"].endswith(".m4a")
    assert win.exports[0]["profile"] == "audio-m4a"
    assert win.exports[0]["task"]["_export_profile_id"] == "audio-m4a"


def test_export_profile_uses_t11d_preset_io():
    from cutora.core import preset_io
    from cutora.render import export_profile as ep
    from cutora.subtitle import text_preset as tp
    assert ep.read_preset_file is preset_io.read_preset_file
    assert ep.write_preset_file is preset_io.write_preset_file
    assert tp._read_json is preset_io.read_preset_file
    assert tp._write_json is preset_io.write_preset_file
    assert ep._STORE == "export_profiles.json"
    src = open(ep.__file__, encoding="utf-8").read()
    assert "from cutora.core.preset_io import" in src
    assert "os.replace" not in src


def _ffprobe(path):
    cmd = [
        FFPROBE, "-v", "error",
        "-show_entries", "stream=codec_name,codec_type,pix_fmt",
        "-show_entries", "format=format_name",
        "-show_entries", "format_tags=major_brand",
        "-of", "json", path,
    ]
    r = subprocess.run(cmd, capture_output=True, timeout=30)
    assert r.returncode == 0, (r.stderr or b"").decode("utf-8", errors="replace")[-500:]
    return json.loads((r.stdout or b"{}").decode("utf-8"))


def test_builtin_real_encode_ffprobe(tmp_path):
    """Each builtin must produce a file ffmpeg accepts; ffprobe reads codec/container."""
    expect = {
        "social-vertical": {"v": "h264", "fmt": "mp4", "ext": "mp4"},
        "hevc-high": {"v": "hevc", "fmt": "mp4", "ext": "mp4"},
        "archive-prores": {"v": "prores", "fmt": "mov", "ext": "mov"},
        "audio-m4a": {"v": None, "a": "aac", "fmt": "m4a", "ext": "m4a"},
        "source-size": {"v": "h264", "fmt": "mp4", "ext": "mp4"},
    }
    rows = []
    for pid, exp in expect.items():
        prof = get_profile(pid)
        out = str(tmp_path / ("%s.%s" % (pid, exp["ext"])))
        vcodec, preset, extra, pix = encode_args(prof, False)
        if vcodec is None:
            cmd = [
                FFMPEG, "-y", "-f", "lavfi",
                "-i", "sine=frequency=1000:sample_rate=48000:duration=1",
                "-t", "1", "-c:a", "aac", "-b:a", "128k", out,
            ]
        else:
            cmd = [
                FFMPEG, "-y",
                "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=30:duration=1",
                "-f", "lavfi", "-i", "sine=frequency=1000:sample_rate=48000:duration=1",
                "-t", "1", "-c:v", vcodec,
            ]
            if preset:
                cmd += ["-preset", preset]
            cmd += list(extra or [])
            cmd += ["-pix_fmt", pix or "yuv420p", "-c:a"]
            ac = str(prof.get("audio_codec") or "aac")
            cmd += [ac]
            if exp["ext"] == "mov":
                cmd += ["-f", "mov"]
            cmd.append(out)
        r = subprocess.run(cmd, capture_output=True, timeout=60)
        err = (r.stderr or b"").decode("utf-8", errors="replace")
        assert r.returncode == 0, "%s failed:\n%s" % (pid, err[-1200:])
        info = _ffprobe(out)
        streams = info.get("streams") or []
        fmt = str((info.get("format") or {}).get("format_name") or "")
        vname = next((s.get("codec_name") for s in streams if s.get("codec_type") == "video"), None)
        aname = next((s.get("codec_name") for s in streams if s.get("codec_type") == "audio"), None)
        if exp["v"] is None:
            assert vname is None, (pid, vname)
            assert aname == exp["a"], (pid, aname)
            assert "mp4" in fmt or "m4a" in fmt, (pid, fmt)
        else:
            assert vname == exp["v"], (pid, vname, fmt)
            if exp["fmt"] == "mov":
                assert out.endswith(".mov")
                brand = str(((info.get("format") or {}).get("tags") or {}).get("major_brand") or "")
                assert "qt" in brand.lower(), (pid, brand, fmt)
            else:
                assert out.endswith(".mp4")
                assert "mp4" in fmt, (pid, fmt)
        rows.append((pid, exp["v"] or exp.get("a"), vname or aname, fmt))
    assert len(rows) == 5
