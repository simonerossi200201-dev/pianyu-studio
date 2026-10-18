# -*- coding: utf-8 -*-
"""T12f: injectable hardware probe → S/A/B/C/D/E; OOM drop; watchdog; word timing."""
from __future__ import annotations

import threading
import time

import pytest

from cutora.asr.tier import (
    ASR_TIERS,
    HEADROOM,
    HardwareProbe,
    WatchdogTimeout,
    WORD_TIMING_LAYERS,
    build_profile,
    choose_word_timing,
    drop_one_tier,
    format_tier_label,
    handle_oom,
    kill_process_tree,
    next_lower_tier,
    notify_fallback,
    resolve_runtime_tier,
    run_in_subprocess,
    run_with_watchdog,
    select_asr_tier,
    set_active_profile,
    tier_fits,
    watchdog_timeout_sec,
)


def _probe(**kwargs) -> HardwareProbe:
    disk = kwargs.pop("models_on_disk", frozenset({
        "asr-small", "asr-large-v3-turbo",
        "align-en", "align-it", "align_wav2vec2",
    }))
    return HardwareProbe(models_on_disk=frozenset(disk), **kwargs)


def test_tables_are_constants_not_logic():
    ids = [t["id"] for t in ASR_TIERS]
    assert ids == ["T", "S", "E"]
    assert ASR_TIERS[0]["asr_id"] == "asr-large-v3-turbo"
    assert ASR_TIERS[0].get("experimental") is True
    assert ASR_TIERS[1]["asr_id"] == "asr-small"
    assert ASR_TIERS[-1]["asr_id"] == "asr-small"
    assert WORD_TIMING_LAYERS == ("align", "word_timestamps", "sentence")
    assert HEADROOM == 0.20


@pytest.mark.parametrize("vram,ram,cuda,expect", [
    (8.0, 16.0, 1, "S"),     # auto skips experimental turbo
    (3.0, 16.0, 1, "S"),     # 2.0*1.2=2.4
    (2.5, 16.0, 1, "S"),
    (2.0, 16.0, 1, "E"),     # below small GPU headroom
    (0.0, 16.0, 0, "E"),
    (0.0, 0.5, 0, "E"),
])
def test_auto_picks_highest_capable(vram, ram, cuda, expect):
    probe = _probe(cuda_count=cuda, vram_free_gb=vram, ram_free_gb=ram)
    row, warn = select_asr_tier(probe, "auto")
    assert warn is None
    assert row["id"] == expect
    assert row["asr_id"] == "asr-small"


def test_auto_skips_experimental_turbo():
    probe = _probe(cuda_count=1, vram_free_gb=16.0, ram_free_gb=32.0)
    row, _ = select_asr_tier(probe, "auto")
    assert row["id"] == "S"
    assert row["asr_id"] == "asr-small"


def test_manual_above_machine_warns_does_not_silent_drop():
    probe = _probe(cuda_count=0, vram_free_gb=0.0, ram_free_gb=4.0)
    row, warn = select_asr_tier(probe, "T")
    assert row["id"] == "T"
    assert warn and "超出本机能力" in warn
    assert "E" in warn or "S" in warn


def test_headroom_is_applied():
    probe = _probe(cuda_count=1, vram_free_gb=2.0, ram_free_gb=16.0)
    small_gpu = next(t for t in ASR_TIERS if t["id"] == "S")
    assert not tier_fits(small_gpu, probe)
    row, _ = select_asr_tier(probe, "auto")
    assert row["id"] == "E"


def test_oom_drops_one_tier_and_logs():
    logs = []
    nxt = handle_oom("T", log=logs.append)
    assert nxt == "S"
    assert logs == ["已从 T 降到 S"]
    assert drop_one_tier("E", log=logs.append) is None
    assert any("无法再降" in x for x in logs)
    assert next_lower_tier("E") is None


def test_watchdog_kills_hung_asr():
    gate = threading.Event()

    def never():
        gate.wait(timeout=30)
        return "done"

    t0 = time.time()
    with pytest.raises(WatchdogTimeout):
        run_with_watchdog(never, 0.2)
    assert time.time() - t0 < 5.0
    gate.set()


def test_watchdog_timeout_is_10x_duration():
    assert watchdog_timeout_sec(8.0) == 80.0
    assert watchdog_timeout_sec(0) == 1.0


def test_word_timing_layers_never_silent():
    align = choose_word_timing("en", has_align=True)
    assert align.layer == "align"
    assert align.notify is None
    wts = choose_word_timing("it", has_align=False, word_ts_available=True)
    assert wts.layer == "word_timestamps"
    assert wts.notify == "近似词级"
    sent = choose_word_timing("it", has_align=False, word_ts_available=False)
    assert sent.layer == "sentence"
    assert "句级" in (sent.notify or "")
    other = choose_word_timing("zh", has_align=False, word_ts_available=False)
    assert other.layer == "sentence"
    assert other.notify and other.notify != wts.notify


def test_shared_profile_label_and_notify():
    set_active_profile(None)
    probe = _probe(cuda_count=1, vram_free_gb=16.0, ram_free_gb=32.0)
    prof = build_profile(probe, preference="auto", lang="en", has_align=True)
    assert prof.asr_tier == "S"
    assert prof.word_timing == "align"
    assert prof.auto is True
    assert "自动（当前：small · GPU fp16 · 词级对齐）" == prof.label
    assert notify_fallback("align", "word_timestamps") == "已从 align 降到 word_timestamps"
    manual = build_profile(probe, preference="T", lang="it", has_align=False)
    assert "手动 T" in manual.label
    assert "近似词级" in manual.label
    assert format_tier_label(ASR_TIERS[-1], "sentence", auto=False).startswith("手动 E")


def test_wanted_removed_model_falls_back_to_small():
    logs = []
    probe = _probe(cuda_count=1, vram_free_gb=16.0, ram_free_gb=32.0)
    row, msg = resolve_runtime_tier(
        r"D:\models\asr_large-v3", probe, log=logs.append)
    assert row["asr_id"] == "asr-small"
    assert any("不在产品目录，回落到 small" in x for x in logs)


def test_wanted_turbo_loads_when_gpu_and_disk():
    probe = _probe(cuda_count=1, vram_free_gb=16.0, ram_free_gb=32.0)
    row, msg = resolve_runtime_tier(r"D:\models\asr_large-v3-turbo", probe)
    assert row["asr_id"] == "asr-large-v3-turbo"
    assert row["id"] == "T"
    assert msg is None


def test_vram_2gb_does_not_load_turbo_gpu():
    probe = _probe(cuda_count=1, vram_free_gb=2.0, ram_free_gb=16.0)
    row, msg = resolve_runtime_tier(r"D:\models\asr_large-v3-turbo", probe)
    assert row["asr_id"] == "asr-small"


def test_small_ceiling_not_upgraded():
    probe = _probe(cuda_count=1, vram_free_gb=16.0, ram_free_gb=32.0)
    row, msg = resolve_runtime_tier(r"D:\models\asr_small", probe)
    assert row["asr_id"] == "asr-small"
    assert msg is None


def test_get_whisperx_model_caps_load(monkeypatch, tmp_path):
    from cutora.asr import engine as E
    from cutora.asr import tier as T
    logs = []
    probe = _probe(cuda_count=1, vram_free_gb=2.0, ram_free_gb=16.0)
    monkeypatch.setattr(T, "probe_hardware", lambda impl=None: probe)
    root = tmp_path / "models"
    (root / "asr_small").mkdir(parents=True)
    (root / "asr_large-v3-turbo").mkdir()

    class _M:
        pass

    loaded = {}

    def fake_load(path, device, compute_type="default", **kwargs):
        loaded.update(path=path, device=device, compute=compute_type)
        return _M()

    monkeypatch.setattr(E, "whisperx", type("W", (), {
        "load_model": staticmethod(fake_load),
    })())
    monkeypatch.setattr(E, "whisperx_vad_model", lambda: None)
    monkeypatch.setattr(E, "get_model_paths",
                        lambda: {"asr_path": str(root / "asr_large-v3-turbo")})
    monkeypatch.setattr(E, "models_root", lambda: str(root))
    from cutora.core import config as _cfg
    monkeypatch.setattr(_cfg, "DEVICE", "cuda")
    E.DEVICE = "cuda"
    E._WHISPERX_MODEL = None
    E._WHISPERX_DEVICE = None
    E._WHISPERX_DIR = None
    E.get_whisperx_model(log=logs.append)
    assert "asr_small" in str(loaded.get("path") or "")
    assert loaded.get("device") == "cuda"
    assert loaded.get("compute") in ("int8", "float16")
    assert any("已从 T 降到" in x for x in logs)


def test_process_watchdog_kills_child():
    import sys
    t0 = time.time()
    with pytest.raises(WatchdogTimeout):
        run_in_subprocess(
            [sys.executable, "-c", "import time; time.sleep(60)"], 0.4)
    assert time.time() - t0 < 8.0
    assert kill_process_tree  # imported, used by run_in_subprocess
