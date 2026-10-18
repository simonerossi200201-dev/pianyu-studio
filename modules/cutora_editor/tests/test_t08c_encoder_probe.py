# -*- coding: utf-8 -*-
"""T08c: hardware encoder probe. -t 1, disk cache, fallback before export."""
from __future__ import annotations

import json
import os

import pytest

from cutora.render.encoder_probe import (
    PROBE_SECONDS,
    SKIP_ENV,
    SLOTS,
    pick_h264,
    pick_hevc,
    probe_argv,
    reset_for_tests,
)
from cutora.render.export_params import build_rawvideo_ffmpeg_cmd, vcodec_quality_args
from cutora.render.export_profile import encode_args, get_profile


@pytest.fixture
def probe_env(tmp_path, monkeypatch):
    monkeypatch.setenv("CUTORA_USER_DATA", str(tmp_path))
    monkeypatch.delenv(SKIP_ENV, raising=False)
    from cutora.core.paths import ensure_user_layout, refresh_cached_paths
    refresh_cached_paths()
    ensure_user_layout()
    reset_for_tests()
    yield tmp_path
    reset_for_tests()


def test_probe_argv_is_one_second_not_tenth():
    cmd = probe_argv("ffmpeg", "h264_nvenc")
    assert "-t" in cmd
    assert cmd[cmd.index("-t") + 1] in ("1", "1.0")
    assert "0.1" not in cmd
    assert PROBE_SECONDS >= 1.0
    assert "-c:v" in cmd and cmd[cmd.index("-c:v") + 1] == "h264_nvenc"
    vendors = {v for v, _n in SLOTS}
    assert vendors == {"nvenc", "amf", "qsv"}


def test_probe_failure_picks_x264_before_encode(probe_env, monkeypatch):
    monkeypatch.setattr(
        "cutora.render.encoder_probe.hw_ok", lambda _n: False)
    monkeypatch.setattr(
        "cutora.render.encoder_probe.ensure_probed",
        lambda: {"encoders": {"h264_nvenc": {"ok": False, "error": "boom"}}},
    )
    enc, reason = pick_h264(True)
    assert enc == "libx264"
    assert "回退" in reason
    vcodec, _p, extra = vcodec_quality_args(True, "compressed")
    assert vcodec == "libx264"
    cmd = build_rawvideo_ffmpeg_cmd(
        320, 240, 30, "t.mp4", use_gpu=True, ffmpeg_path="ffmpeg")
    assert "h264_nvenc" not in cmd
    assert "libx264" in cmd


def test_probe_ok_selects_nvenc(probe_env, monkeypatch):
    monkeypatch.setattr(
        "cutora.render.encoder_probe.hw_ok", lambda n: n == "h264_nvenc")
    monkeypatch.setattr(
        "cutora.render.encoder_probe.ensure_probed",
        lambda: {"encoders": {"h264_nvenc": {"ok": True}}},
    )
    enc, _r = pick_h264(True)
    assert enc == "h264_nvenc"
    vcodec, _p, extra = vcodec_quality_args(True, "compressed")
    assert vcodec == "h264_nvenc"
    assert "-cq" in extra and "18" in extra


def test_skip_env_never_selects_nvenc(monkeypatch):
    monkeypatch.setenv(SKIP_ENV, "1")
    reset_for_tests()
    enc, reason = pick_h264(True)
    assert enc == "libx264"
    assert "跳过" in reason
    reset_for_tests()


def test_hevc_fallback_and_nvenc(probe_env, monkeypatch):
    monkeypatch.setattr(
        "cutora.render.encoder_probe.hw_ok", lambda n: n == "hevc_nvenc")
    monkeypatch.setattr("cutora.render.encoder_probe.ensure_probed", lambda: {})
    assert pick_hevc(True)[0] == "hevc_nvenc"
    monkeypatch.setattr("cutora.render.encoder_probe.hw_ok", lambda n: False)
    assert pick_hevc(True)[0] == "libx265"


def test_source_size_is_crf15_not_zero(monkeypatch):
    monkeypatch.setenv(SKIP_ENV, "1")
    reset_for_tests()
    vcodec, preset, extra, _pix = encode_args(get_profile("source-size"), False)
    assert vcodec == "libx264"
    assert "-crf" in extra and "15" in extra
    assert "0" not in extra
    reset_for_tests()


def test_cache_written_and_reused(probe_env, monkeypatch):
    calls = {"n": 0}

    def fake_run(cmd, **kwargs):
        calls["n"] += 1
        class R:
            returncode = 1
            stderr = b"nope"
            stdout = b""
        return R()

    monkeypatch.setattr("cutora.render.encoder_probe._no_window_run", fake_run)
    monkeypatch.setattr(
        "cutora.render.encoder_probe._ffmpeg_version", lambda _f: "ffmpeg-test")
    monkeypatch.setattr(
        "cutora.render.encoder_probe._ffmpeg", lambda: "ffmpeg")
    from cutora.render.encoder_probe import load_cache, probe
    first = probe(force=True)
    n1 = calls["n"]
    assert n1 == len(SLOTS)
    assert first["encoders"]["h264_nvenc"]["ok"] is False
    assert "h264_amf" in first["encoders"]
    assert "h264_qsv" in first["encoders"]
    reset_for_tests()
    second = probe(force=False)
    assert calls["n"] == n1
    assert second["ffmpeg_version"] == "ffmpeg-test"
    disk = load_cache()
    assert disk["encoders"]["h264_nvenc"]["ok"] is False
