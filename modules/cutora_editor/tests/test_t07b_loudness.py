# -*- coding: utf-8 -*-
"""T07b: dual-pass loudnorm. Product default -14 LUFS. Golden pins off."""
from __future__ import annotations

import json
import os
import subprocess

import pytest

from cutora.audio.loudness import (
    DEFAULT_MODE,
    MODE_14,
    MODE_OFF,
    SETTING_MODE,
    TARGET_14,
    TRUE_PEAK,
    apply_filter,
    get_loudness_settings,
    measure_filter,
    parse_ebur128_i,
    parse_measured,
    pin_settings_loudness_off,
    set_loudness_settings,
    two_pass_wav,
)
from cutora.core.commands import CommandError, get_bus, reset_bus_for_tests
from cutora.core.paths import FFMPEG

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture
def loudness_store(tmp_path, monkeypatch):
    path = tmp_path / "app_settings.json"
    monkeypatch.setattr(
        "cutora.core.history._get_app_settings_path", lambda: str(path))
    return path


def test_default_is_lufs14(loudness_store):
    ln = get_loudness_settings()
    assert ln.mode == DEFAULT_MODE == MODE_14
    assert ln.enabled is True
    assert abs(ln.target_i - TARGET_14) < 1e-6
    assert abs(ln.target_tp - TRUE_PEAK) < 1e-6


def test_command_set_and_restore(loudness_store):
    reset_bus_for_tests()
    bus = get_bus()
    out = bus.execute("audio.loudness.set", {"mode": "off"}, source="cli")
    assert out["ok"] is True
    assert out["mode"] == MODE_OFF
    assert out["previous"]["mode"] == MODE_14
    again = bus.execute("audio.loudness.set", {"mode": "lufs14"}, source="cli")
    assert again["mode"] == MODE_14
    assert get_loudness_settings().enabled is True
    with pytest.raises(CommandError):
        bus.execute("audio.loudness.set", {"mode": "banana"}, source="cli")
    reset_bus_for_tests()


def test_command_is_undoable_portable_headless():
    reset_bus_for_tests()
    spec = get_bus().registry.get("audio.loudness.set")
    assert spec.undoable is True
    assert spec.portable is True
    assert spec.headless is True
    reset_bus_for_tests()


def test_measure_filter_is_not_single_pass_dynamic():
    text = measure_filter(-14.0)
    assert "print_format=json" in text
    assert "linear=true" not in text
    applied = apply_filter(
        {"input_i": "-5.0", "input_lra": "4.0", "input_tp": "-0.2",
         "input_thresh": "-16.0", "target_offset": "0.5"},
        -14.0,
    )
    assert "linear=true" in applied
    assert "measured_I=" in applied


def test_parse_measured_extracts_json():
    blob = (
        "[Parsed_loudnorm_0 @ 000] \n"
        "{\n"
        "\t\"input_i\" : \"-5.31\",\n"
        "\t\"input_tp\" : \"-0.12\",\n"
        "\t\"input_lra\" : \"3.10\",\n"
        "\t\"input_thresh\" : \"-16.40\",\n"
        "\t\"target_offset\" : \"0.01\"\n"
        "}\n"
    )
    data = parse_measured(blob)
    assert data["input_i"] == "-5.31"


def _ffmpeg():
    return FFMPEG if os.path.isfile(FFMPEG) else "ffmpeg"


def test_two_pass_lands_within_half_lufs(tmp_path, loudness_store):
    ln = get_loudness_settings()
    assert ln.enabled is True
    assert abs(ln.target_i - TARGET_14) < 1e-6
    ffmpeg = _ffmpeg()
    src = str(tmp_path / "hot.wav")
    dst = str(tmp_path / "norm.wav")
    gen = [
        ffmpeg, "-y", "-f", "lavfi",
        "-i", "sine=frequency=1000:sample_rate=48000:duration=3",
        "-filter:a", "volume=8dB",
        src,
    ]
    r = subprocess.run(gen, capture_output=True, timeout=30)
    assert r.returncode == 0, (r.stderr or b"").decode("utf-8", errors="replace")[-500:]
    two_pass_wav(ffmpeg, src, dst, ln.target_i, ln.target_tp)
    probe = [
        ffmpeg, "-hide_banner", "-nostats", "-i", dst,
        "-af", "ebur128=peak=true", "-f", "null", "-",
    ]
    p = subprocess.run(probe, capture_output=True, timeout=30)
    err = (p.stderr or b"").decode("utf-8", errors="replace")
    integ = parse_ebur128_i(err)
    assert integ is not None, err[-800:]
    assert abs(integ - TARGET_14) <= 0.5, integ
    # True-peak ceiling: loudnorm TP=-1. Allow a little meter slack.
    peak = None
    for line in err.splitlines():
        s = line.strip()
        if "Peak:" in s or s.startswith("Peak:"):
            try:
                peak = float(s.split(":")[-1].replace("dBFS", "").replace("dBTP", "").strip())
            except ValueError:
                continue
    if peak is not None:
        assert peak <= 0.1, peak


def test_off_does_not_rewrite_mix_argv():
    """Loudness off must mux the mix as-is.

    Q92 (`7a571ab` / `09e95c8`) stopped building that argv with
    `cmd += maps + encode_tail + [output_path]` and passed the same three
    pieces to `run_filter_script` instead. The off path is the code after
    `if ln.enabled:` returns. It must still append maps + encode_tail +
    output, and it must not mention loudnorm.
    """
    src = open(
        os.path.join(REPO, "cutora", "render", "render_worker.py"),
        encoding="utf-8",
    ).read()
    assert "get_loudness_settings" in src
    assert "if ln.enabled:" in src
    assert "No audio: skip loudnorm" in src
    after = src.split("if ln.enabled:", 1)[1]
    off = after.split("return True", 1)[1].split("\n    def ", 1)[0]
    assert "maps + encode_tail + [output_path]" in off
    assert "loudnorm" not in off
    assert "run_filter_script" in off


def test_golden_harness_pins_loudness_off(tmp_path):
    """Product default is on. Golden must pin off in the copied fixture."""
    fixture = os.path.join(
        REPO, "tests", "golden", "_fixture_user_data", "app_settings.json")
    tracked = json.loads(open(fixture, encoding="utf-8").read())
    assert tracked.get("loudness_mode") == "off"
    leaked = tmp_path / "app_settings.json"
    leaked.write_text(
        json.dumps({SETTING_MODE: "lufs14"}), encoding="utf-8")
    pin_settings_loudness_off(str(leaked))
    data = json.loads(leaked.read_text(encoding="utf-8"))
    assert data[SETTING_MODE] == "off"
    assert get_loudness_settings(data).enabled is False
    assert get_loudness_settings({}).enabled is True


def test_no_audio_skip_does_not_error():
    r = subprocess.run(
        [_ffmpeg(), "-version"], capture_output=True, timeout=15)
    assert r.returncode == 0
    src = open(
        os.path.join(REPO, "cutora", "render", "render_worker.py"),
        encoding="utf-8",
    ).read()
    # The filter-graph helper also has `if not tracks:` and returns "".
    # The mux path is the one that skips loudnorm.
    at = src.find("No audio: skip loudnorm")
    assert at != -1
    chunk = src[at:at + 500]
    assert "没有音轨可导出" not in chunk
    assert "return True" in chunk
