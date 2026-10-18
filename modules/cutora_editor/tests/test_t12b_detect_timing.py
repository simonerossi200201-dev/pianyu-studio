# -*- coding: utf-8 -*-
"""T12b 提交 2：先检测再识别。交互测试先写、先红。"""
from __future__ import annotations


def _low(lang="it", conf=0.41):
    return {
        "language": lang,
        "confidence": conf,
        "low_confidence": True,
        "name_zh": "意大利语",
        "prompt_zh": "检测为 意大利语（低置信），请确认",
    }


def _ok(lang="en", conf=0.91):
    return {
        "language": lang,
        "confidence": conf,
        "low_confidence": False,
        "name_zh": "英语",
        "prompt_zh": "",
    }


def test_explicit_language_skips_detect_and_dialog():
    from cutora.asr.detect_plan import run_single_auto
    started, confirms, detects = [], [], []

    def detect(_path):
        detects.append(1)
        return _ok()

    out = run_single_auto(
        media="a.mp4",
        requested="it",
        interactive=True,
        detect_fn=detect,
        start_fn=started.append,
        ask_confirm_fn=lambda det: confirms.append(det) or det["language"],
    )
    assert detects == []
    assert confirms == []
    assert started == ["it"]
    assert out["started"] is True


def test_single_low_confidence_confirms_then_starts():
    from cutora.asr.detect_plan import run_single_auto
    started, confirms = [], []

    def confirm(det):
        confirms.append(det)
        assert not started, "识别必须在确认之后才启动"
        return det["language"]

    out = run_single_auto(
        media="a.mp4",
        requested="auto",
        interactive=True,
        detect_fn=lambda _p: _low(),
        start_fn=started.append,
        ask_confirm_fn=confirm,
    )
    assert len(confirms) == 1
    assert started == ["it"]
    assert out["started"] is True
    assert out["confirmed"] is True


def test_single_low_confidence_cancel_does_not_start():
    from cutora.asr.detect_plan import run_single_auto
    started = []
    out = run_single_auto(
        media="a.mp4",
        requested="auto",
        interactive=True,
        detect_fn=lambda _p: _low(),
        start_fn=started.append,
        ask_confirm_fn=lambda det: None,
    )
    assert started == []
    assert out["started"] is False


def test_single_high_confidence_starts_without_dialog():
    from cutora.asr.detect_plan import run_single_auto
    started, confirms = [], []
    run_single_auto(
        media="a.mp4",
        requested="auto",
        interactive=True,
        detect_fn=lambda _p: _ok("es", 0.88),
        start_fn=started.append,
        ask_confirm_fn=lambda det: confirms.append(det) or det["language"],
    )
    assert confirms == []
    assert started == ["es"]


def test_batch_never_opens_modal_and_has_language_confidence():
    from cutora.asr.detect_plan import plan_batch
    confirms = []

    def detect(path):
        return _low() if "low" in path else _ok("en", 0.93)

    tasks = [
        {"video": r"C:\low.mp4", "text": "a", "segments": []},
        {"video": r"C:\ok.mp4", "text": "b", "segments": []},
    ]
    rows = plan_batch(
        tasks, detect_fn=detect, requested="auto",
        ask_confirm_fn=lambda det: confirms.append(det) or det["language"],
    )
    assert confirms == [], "批量路径不得弹窗"
    assert [r["language"] for r in rows] == ["it", "en"]
    assert "confidence" in rows[0] and "language" in rows[0]
    assert rows[0]["warn"] is True
    assert rows[1]["warn"] is False
    yellow = [r["media_id"] for r in rows if r["warn"]]
    assert yellow == [0]


def test_batch_table_column_keys():
    from cutora.asr.detect_plan import BATCH_RESULT_COLUMNS
    assert BATCH_RESULT_COLUMNS == ("language", "confidence")


def test_threshold_setting_overrides_default(monkeypatch, tmp_path):
    from cutora.core.history import _save_app_settings
    from cutora.asr.detect import build_detection_result, get_detect_threshold

    monkeypatch.setenv("CUTORA_USER_DATA", str(tmp_path))
    from cutora.core.paths import refresh_cached_paths
    refresh_cached_paths()
    _save_app_settings({"asr.detect_confidence_threshold": 0.9})
    assert get_detect_threshold() == 0.9
    mid = build_detection_result("en", 0.8)
    assert mid["low_confidence"] is True
    hi = build_detection_result("en", 0.9)
    assert hi["low_confidence"] is False
