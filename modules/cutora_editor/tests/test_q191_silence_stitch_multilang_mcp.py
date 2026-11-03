# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Comprehensive verification for Problems 1–7 and Full Studio Anthropic MCP."""
from __future__ import annotations

import json
import os

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_p1_p2_fullscreen_blend_overlay_and_needs_fill():
    from cutora.batch.face_safe import fit_secondary_clip
    from cutora.render.fill_bg import needs_fill
    from cutora.render.split_screen import is_fullscreen_overlay_track

    overlay_tr = {
        "type": "video",
        "path": "D:/1_PROJECT/Footage/圣经 + 光线.mp4",
        "blend_mode": "screen",
        "split_cell": None,
        "enabled": True,
    }
    cell_tr = {
        "type": "video",
        "path": "a.mp4",
        "blend_mode": "normal",
        "split_cell": "B",
        "enabled": True,
    }
    assert is_fullscreen_overlay_track(overlay_tr) is True
    assert is_fullscreen_overlay_track(cell_tr) is False

    # Secondary clip fitting must never produce scale < 1.0 or shift past cell coverage
    import types
    clip_obj = types.SimpleNamespace(scale=1.0, x=0.0, y=0.0, bg_fill={})
    tf = fit_secondary_clip(clip_obj, 1080, 1920, {"bbox": (0.2, 0.1, 0.4, 0.3)}, (0, 960, 1080, 960), (0, 960, 1080, 1920))
    assert float(clip_obj.scale) >= 1.0
    assert tf["scale"] >= 1.0
    # When target_rect covers the full split cell (0, 0, 1080, 960), needs_fill is False
    from PySide6.QtCore import QRectF
    assert needs_fill({"mode": "blur", "blur": 20}, QRectF(0, 0, 1080, 960), 1080, 1920, split_rect=(0, 0, 1080, 960)) is False


def test_p3_legacy_gpu_nvenc_guard():
    from cutora.render.export_guard import is_legacy_nvenc_gpu
    assert is_legacy_nvenc_gpu("NVIDIA GeForce GTX 1070") is True
    assert is_legacy_nvenc_gpu("NVIDIA GeForce GTX 1080 Ti") is True
    assert is_legacy_nvenc_gpu("NVIDIA GeForce GTX 1660 Super") is True
    assert is_legacy_nvenc_gpu("NVIDIA GeForce RTX 4090") is False
    assert is_legacy_nvenc_gpu("NVIDIA GeForce RTX 5060 Ti") is False


def test_p5_silence_cut_units_breath_protection_and_ripple():
    from cutora.audio.silence_cut import (
        SR,
        SilenceParams,
        analyze,
        apply_plan,
        plan_task,
    )

    # Unit conversion: frames, ms, s, min
    p_frames = SilenceParams(threshold=25, unit="frames", keep=6, keep_unit="frames")
    assert abs(p_frames.threshold_s(25.0) - 1.0) < 1e-6
    assert abs(p_frames.keep_s(25.0) - 0.24) < 1e-6

    p_min = SilenceParams(threshold=0.05, unit="min", keep=250, keep_unit="ms")
    assert abs(p_min.threshold_s(30.0) - 3.0) < 1e-6
    assert abs(p_min.keep_s(30.0) - 0.25) < 1e-6

    # Synthetic 8.2s 16kHz speech signal:
    # Word 1 ("Hello"): 0.20s - 1.50s (speech tone)
    # Pause 1: 1.50s - 3.50s (2.0s dead silence -> cut down to 0.25s!)
    # Word 2 ("world"): 3.50s - 5.00s (speech tone)
    # Word 3 ("Amen"): 5.20s - 7.00s (speech tone)
    n_samples = int(8.2 * SR)
    t_arr = np.arange(n_samples, dtype=np.float32) / SR
    sig = np.random.RandomState(42).normal(0.0, 1e-4, n_samples).astype(np.float32)
    for a, b in ((0.20, 1.50), (3.50, 5.00), (5.20, 7.00)):
        i0, i1 = int(a * SR), int(b * SR)
        sig[i0:i1] = 0.25 * np.sin(2.0 * np.pi * 220.0 * t_arr[i0:i1])
    cov = np.ones(n_samples, dtype=bool)

    task = {
        "fps": 30.0,
        "duration": 8.20,
        "segments": [
            {
                "id": "s1",
                "start": 0.20,
                "end": 5.00,
                "text": "Hello world",
                "words": [
                    {"word": "Hello", "start": 0.20, "end": 1.50},
                    {"word": "world", "start": 3.50, "end": 5.00},
                ],
            },
            {
                "id": "s2",
                "start": 5.20,
                "end": 7.00,
                "text": "Amen",
                "words": [
                    {"word": "Amen", "start": 5.20, "end": 7.00},
                ],
            },
        ],
        "text_overlays": [
            {"text": "TITLE", "start": 0.20, "end": 5.00},
        ],
    }

    params = SilenceParams(
        threshold=1.0, unit="s", keep=0.25, keep_unit="s",
        head_frames=2, tail_frames=2, keep_breaths=True, use_vad=False,
        trim_edges=False,
    )
    an = analyze(task, params, sig_cov=(sig, cov))
    plan = plan_task(task, params, analysis=an)
    assert plan["summary"]["n_cuts"] == 1
    assert float(plan["summary"]["remove_s"]) > 1.5

    applied = apply_plan(task, plan, force=True)
    assert applied["n_cuts"] == 1
    assert float(applied["removed_s"]) > 1.5
    w_world = task["segments"][0]["words"][1]
    assert float(w_world["start"]) < 2.0
    assert float(task["segments"][1]["start"]) < 4.0


def test_p6_stitch_grouping_auto_recognition(tmp_path):
    from cutora.batch.stitch_grouping import GROUP_AUTO, precheck, scan_folders

    f1 = tmp_path / "V01-简单的小天使 (1)"
    f2 = tmp_path / "V02-简单的小天使 (2)"
    f1.mkdir()
    f2.mkdir()
    for i in (1, 2):
        (f1 / f"简单的小天使 (1)-{i}.mp4").write_bytes(b"00")
    for i in (1, 2, 3):
        (f2 / f"简单的小天使 (2)-{i}.mp4").write_bytes(b"00")

    res = scan_folders([str(f1), str(f2)], mode=GROUP_AUTO)
    assert len(res.groups) == 2
    assert res.mean_confidence >= 0.95
    assert len(res.groups[0].clips) == 2
    assert len(res.groups[1].clips) == 3

    chk = precheck(res, scripts={res.groups[0].key: "Script one", res.groups[1].key: "Script two"})
    assert chk["ok"] is True


def test_p7_multilingual_zh_mg_sw_ar_support():
    _qapp()
    from cutora.asr.languages import is_experimental, is_rtl_language
    from cutora.asr.romanize import romanize_tokens, speech_tokens
    from cutora.batch.script_stitcher import tokenize
    from cutora.batch.title_align import align_title
    from cutora.render.textfx.layout import _is_rtl_text, layout_event
    from cutora.render.textfx.spec import from_style
    from cutora.subtitle.layout import LayoutRules, break_lines, load_stopwords

    for code in ("zh", "mg", "sw", "ar"):
        assert is_experimental(code) is False, code
    assert is_rtl_language("ar") is True
    assert is_rtl_language("mg") is False

    # 1. Malagasy (mg) exact user prayer text
    mg_text = (
        "Tompo ô, mba jereo amim-pitiavana ity rahavavy mijery ity horonantsary ity izao. "
        "Hitanao ny fahoriana zakainy ary fantatrao ny tsindry ao am-pony. "
        "Sitrano ny ratrany, ampitonio ny ao anatiny ary esory ny ahiahiny. "
        "Arovy koa ny fianakaviany sy izay rehetra sarobidy aminy. "
        "Omeo hery vaovao izy ary ampio izy hijoro mafy indray. Amena. "
        "Raha te handray izao vavaka izao ianao, soraty hoe “Amena”, ary zarao "
        "amin’olona iray mila fampiononana, mba hiditra ao an-tranony koa izao fiadanana izao."
    )
    mg_toks = tokenize(mg_text)
    assert mg_toks[:4] == ["tompo", "o", "mba", "jereo"]
    assert "amim" in mg_toks and "pitiavana" in mg_toks
    mg_rom = romanize_tokens(speech_tokens("Tompo ô, mba jereo", "mg"), "mg")
    assert len(mg_rom) == 4
    mg_stops = load_stopwords("mg")
    assert "ny" in mg_stops and "mba" in mg_stops
    mg_lines = break_lines(
        {"text": "Tompo ô, mba jereo amim-pitiavana ity rahavavy mijery ity", "lang": "mg"},
        LayoutRules(lang="mg", max_chars_per_line=36, max_lines=2),
    )
    assert 1 <= len(mg_lines) <= 2

    # 2. Swahili (sw)
    sw_text = "Bwana Yesu, tunakuomba uwalinde watoto wetu katika safari hii ya maisha."
    sw_toks = tokenize(sw_text)
    assert sw_toks[:2] == ["bwana", "yesu"]
    sw_stops = load_stopwords("sw")
    assert "katika" in sw_stops and "ya" in sw_stops
    sw_lines = break_lines(
        {"text": sw_text, "lang": "sw"},
        LayoutRules(lang="sw", max_chars_per_line=40, max_lines=2),
    )
    assert len(sw_lines) == 2
    assert sw_lines[0].split()[-1].lower() not in sw_stops

    # 3. Arabic (ar): normalization + stopwords + RTL layout (first spoken word on RIGHT)
    ar_vocalized = "بِسْمِ اللَّهِ الرَّحْمَٰنِ الرَّحِيمِ"
    ar_plain = "بسم الله الرحمن الرحيم"
    assert tokenize(ar_vocalized) == tokenize(ar_plain)
    assert len(speech_tokens(ar_plain, "ar")) == 4
    ar_stops = load_stopwords("ar")
    assert "في" in ar_stops and "من" in ar_stops
    assert _is_rtl_text(ar_plain) is True

    ar_style = {"font_family": "Noto Sans Arabic", "script": "arabic", "fontsize": 40}
    ar_event = {
        "id": "ar1",
        "start": 0.0,
        "end": 2.0,
        "text": ar_plain,
        "lang": "ar",
        "words": [
            {"word": "بسم", "start": 0.0, "end": 0.5},
            {"word": "الله", "start": 0.5, "end": 1.0},
            {"word": "الرحمن", "start": 1.0, "end": 1.5},
            {"word": "الرحيم", "start": 1.5, "end": 2.0},
        ],
    }
    ar_lay = layout_event(ar_event, ar_style, from_style(ar_style), 1080, 1920)
    runs = ar_lay.lines[0].runs
    assert len(runs) == 4
    # First spoken word ("بسم") is placed to the RIGHT of the last spoken word ("الرحيم")
    assert runs[0].x > runs[-1].x, [r.x for r in runs]

    # 4. Chinese (zh): per-character norm_tokens + kinsoku-safe CJK line breaking + title_align
    zh_text = "主啊，请慈爱地看顾现在正在看这段视频的这位姐妹。"
    zh_toks = tokenize(zh_text)
    assert zh_toks[:4] == ["主", "啊", "请", "慈"]
    zh_lines = break_lines(
        {"text": zh_text, "lang": "zh"},
        LayoutRules(lang="zh", max_chars_per_line=14, max_lines=2),
    )
    assert len(zh_lines) == 2
    assert not zh_lines[1].startswith(("，", "。"))

    zh_segs = [{
        "start": 0.0,
        "end": 3.0,
        "text": "主啊，请慈爱地看顾这位姐妹",
        "words": [
            {"word": ch, "start": i * 0.2, "end": (i + 1) * 0.2}
            for i, ch in enumerate("主啊请慈爱地看顾这位姐妹")
        ],
    }]
    zh_align = align_title(zh_segs, "主啊，请慈爱地看顾")
    assert zh_align["how"] == "matched"
    assert abs(float(zh_align["start"]) - 0.0) < 1e-6
    assert abs(float(zh_align["end"]) - 1.6) < 1e-6


def test_mcp_full_studio_tools_and_headless_execution(tmp_path):
    from cutora.api.mcp_server import handle_line

    listed = handle_line(json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "tools/list",
    }))["result"]["tools"]
    names = {t["name"] for t in listed}
    for required_tool in (
        "silence_detect",
        "silence_apply",
        "silence_batch",
        "stitch_scan_groups",
        "stitch_precheck",
        "batch_title_align",
        "studio_overview",
        "orante_list_tasks",
        "table_inspect",
    ):
        assert required_tool in names, required_tool

    # Call studio_overview in headless MCP mode without needing GUI open
    resp = handle_line(json.dumps({
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {
            "name": "studio_overview",
            "arguments": {"_headless": True},
        },
    }))
    assert resp["result"]["isError"] is False
    payload = json.loads(resp["result"]["content"][0]["text"])
    assert payload["ok"] is True
    assert set(payload["result"]["first_class_languages"]) >= {"zh", "mg", "sw", "ar"}


def test_silence_cut_studio_ui_and_stitch_hover_splitter(tmp_path):
    _qapp()
    import types
    from cutora.audio.silence_cut import SR
    from cutora.batch.script_stitcher import StitcherDialog
    from cutora.ui.silence_cut_dialog import HoverThinSplitter, SilenceCutDialog

    # 1. Build a host with 1 synthetic video task
    wav_path = tmp_path / "voice.wav"
    n_samples = int(5.0 * SR)
    t_arr = np.arange(n_samples, dtype=np.float32) / SR
    sig = np.random.RandomState(7).normal(0.0, 1e-4, n_samples).astype(np.float32)
    for a, b in ((0.2, 1.2), (3.0, 4.5)):
        i0, i1 = int(a * SR), int(b * SR)
        sig[i0:i1] = 0.25 * np.sin(2.0 * np.pi * 220.0 * t_arr[i0:i1])
    from cutora.audio.silence_cut import write_wav
    write_wav(str(wav_path), sig)

    from cutora.model.timeline import Timeline
    from cutora.model.timeline_model import Clip, MediaSource, Track
    tl = Timeline(1080, 1920, 30.0)
    src = MediaSource(str(wav_path), duration=5.0, probe=False)
    tl.sources[src.id] = src
    tr = Track("audio")
    tr.audio_role = "main"
    ac = Clip(src.id, 0.0, 5.0, 0.0)
    ac.audio_role = "main"
    ac.is_main = True
    tr.clips.append(ac)
    tl.add_track(tr)

    task = {
        "video": str(wav_path),
        "fps": 30.0,
        "duration": 5.0,
        "timeline": tl,
        "segments": [
            {
                "id": "seg1",
                "start": 0.2,
                "end": 4.5,
                "text": "Hello world",
                "words": [
                    {"word": "Hello", "start": 0.2, "end": 1.2},
                    {"word": "world", "start": 3.0, "end": 4.5},
                ],
            }
        ],
    }
    host = types.SimpleNamespace(tasks=[task], curr_idx=0)

    # 2. Verify SilenceCutDialog opens immediately without blocking analysis in __init__
    dlg = SilenceCutDialog(host)
    assert dlg.property("cutora_sheet") == "fill"
    assert len(dlg._plans) == 0  # Not analyzed until user clicks button!
    assert dlg.task_table.rowCount() == 2  # "All" row + 1 task row
    assert isinstance(dlg.v_splitter, HoverThinSplitter)
    assert isinstance(dlg.h_splitter, HoverThinSplitter)

    # Apply preset and run detection
    dlg._apply_preset(0.8, "s", 0.22, "s", 2, 2)
    res = dlg.run_detect()
    assert res["total_cuts"] == 1
    assert dlg.progress_bar.value() == 100
    assert dlg.table.rowCount() >= 1
    assert "Hello" in dlg.table.item(0, 3).text() and "world" in dlg.table.item(0, 3).text()

    # Test batch selection helpers and frame nudging
    dlg.batch_set_checked("none")
    assert dlg._row_meta[0][2]["enabled"] is False
    dlg.batch_set_checked("all")
    assert dlg._row_meta[0][2]["enabled"] is True
    orig_f0 = dlg._row_meta[0][2]["frame_start"]
    dlg.nudge_selected_cut(1, 0)
    assert dlg._row_meta[0][2]["frame_start"] == orig_f0 + 1

    # 3. Verify StitcherDialog uses HoverThinSplitter with free dragging and no rigid size cap
    sdlg = StitcherDialog(None, lambda: None, lambda _l: None, lambda _t: None)
    assert sdlg.property("cutora_sheet") == "fill"
    assert sdlg.minimumWidth() <= 320 and sdlg.minimumHeight() <= 240
    assert isinstance(sdlg.main_splitter, HoverThinSplitter)
    assert isinstance(sdlg.outer_splitter, HoverThinSplitter)
    assert sdlg.main_splitter.childrenCollapsible() is True
    assert sdlg.outer_splitter.childrenCollapsible() is True
    handle = sdlg.main_splitter.handle(1)
    assert handle._hover is False
    sdlg._cycle_layout_mode()
    assert sdlg._layout_lock == "h"
    sdlg.close()
    dlg.close()


def test_zh_ar_align_models_layout_and_stitching():
    _qapp()
    from cutora.asr.engine import align_transcript, get_align_model, has_local_align_model
    from cutora.asr.languages import get_language
    from cutora.asr.models import align_row_for_language, reset_catalog
    from cutora.asr.tier import choose_word_timing
    from cutora.batch.script_stitcher import (
        find_missing,
        flatten_words,
        match_clip_global,
        tokenize,
        tokenize_raw_pairs,
    )
    from cutora.subtitle.layout import (
        LayoutRules,
        break_lines,
        relayout_task,
    )
    from cutora.asr.text_norm import ZH_CLOSING, ZH_OPENING
    from cutora.subtitle.text_utils import _doc_layout_lines, is_rtl_text, tokenize_for_wrap
    from PySide6.QtGui import QFont

    reset_catalog()
    for code in ("zh", "ar"):
        lang_row = get_language(code)
        assert lang_row is not None
        assert lang_row["experimental"] is False
        assert lang_row["subtitle_tier"] == "complete"
        assert lang_row["align_tier"] == "exact"
        align_row = align_row_for_language(code)
        assert align_row is not None and align_row["enabled"] is True
        assert has_local_align_model(code) is True
        timing = choose_word_timing(code, has_align=True)
        assert timing.layer == "align"

    # 1. Chinese & Arabic wav2vec2 CTC alignment restoring display tokens
    sr = 16000
    t = np.linspace(0, 2.4, int(sr * 2.4), dtype=np.float32)
    audio = (0.12 * np.sin(2 * np.pi * 220 * t) + 0.08 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)

    m_zh, meta_zh = get_align_model("zh")
    res_zh = align_transcript(
        [{"start": 0.0, "end": 2.4, "text": "“今天用 OpenAI 测试 25% 字幕！”"}],
        m_zh, meta_zh, audio, "cpu", return_char_alignments=False,
    )
    zh_words = res_zh["segments"][0]["words"]
    assert [w["word"] for w in zh_words] == ["“今", "天", "用", "OpenAI", "测", "试", "25%", "字", "幕！”"]
    assert all(isinstance(w.get("start"), (int, float)) and isinstance(w.get("end"), (int, float)) for w in zh_words)

    m_ar, meta_ar = get_align_model("ar")
    res_ar = align_transcript(
        [{"start": 0.0, "end": 2.4, "text": "مَرْحَبًا بِكُمْ فِي هَذَا الْفِيدْيُو، شُكْرًا!"}],
        m_ar, meta_ar, audio, "cpu", return_char_alignments=False,
    )
    ar_words = res_ar["segments"][0]["words"]
    assert [w["word"] for w in ar_words] == ["مَرْحَبًا", "بِكُمْ", "فِي", "هَذَا", "الْفِيدْيُو،", "شُكْرًا!"]
    assert all(isinstance(w.get("start"), (int, float)) and isinstance(w.get("end"), (int, float)) for w in ar_words)

    # 2. Chinese kinsoku + zh-en mixed line breaking (default 16-22 char cap, no broken English words/digits)
    mixed_zh = "今天我们用 OpenAI GPT-4o 模型在 2026 年做 99.5% 精准度的“短视频字幕”测试，效果非常好！"
    lines_zh = break_lines({"text": mixed_zh, "lang": "zh"}, LayoutRules(lang="zh", max_lines=2))
    assert len(lines_zh) == 2
    for ln in lines_zh:
        assert not any(ln.startswith(ch) for ch in ZH_CLOSING), ln
        assert not any(ln.endswith(ch) for ch in ZH_OPENING), ln
    assert any("OpenAI" in ln for ln in lines_zh)
    assert any("GPT-4o" in ln for ln in lines_zh)
    assert any("2026" in ln for ln in lines_zh)
    assert any("99.5%" in ln for ln in lines_zh)
    full_joined = "".join(lines_zh)
    assert "OpenAI GPT-4o" in full_joined
    assert "2026" in full_joined and "99.5%" in full_joined

    wrap_toks, joiner = tokenize_for_wrap(mixed_zh)
    assert joiner == ""
    assert any("OpenAI" in tok for tok in wrap_toks)
    assert any("2026" in tok for tok in wrap_toks)

    # Relayout task on zh and ar does not mark lines_approx
    relayout_data = {
        "segments": [
            {"start": 0.0, "end": 2.5, "text": mixed_zh, "lang": "zh"},
            {"start": 2.5, "end": 5.0, "text": "مرحبا بكم في هذا الفيديو الجديد عن صناعة المحتوى الاحترافي", "lang": "ar"},
        ]
    }
    rel_out = relayout_task(relayout_data)
    assert rel_out["approx"] == 0
    for seg in relayout_data["segments"]:
        assert "lines_approx" not in seg
        assert seg.get("lines")

    # 3. Arabic RTL document layout
    ar_line = "مرحبا بكم في هذا الفيديو الجديد"
    assert is_rtl_text(ar_line) is True
    doc_lines, max_w, total_h = _doc_layout_lines(ar_line, QFont("Noto Sans Arabic", 28), 600)
    assert len(doc_lines) >= 1 and max_w > 0 and total_h > 0

    # 4. Chinese & Arabic stitching (Chinese numerals <-> digits, Trad <-> Simp, Arabic vocalized <-> plain)
    zh_script = "歡迎來到這個視頻，今天有 3 個任務，完成了 25%，耗時 100 秒。"
    zh_asr_segs = [{
        "start": 0.2,
        "end": 3.8,
        "text": "欢迎来到这个视频今天有三个任务完成了百分之二十五耗时一百秒",
        "words": [],
    }]
    zh_clip_words = flatten_words(zh_asr_segs)
    zh_script_toks, zh_script_raws = tokenize_raw_pairs(zh_script)
    assert zh_script_toks == [w[0] for w in zh_clip_words]
    m_zh_clip = match_clip_global(zh_clip_words, zh_script_toks)
    assert m_zh_clip["matched"] is True and m_zh_clip["ratio"] == 1.0
    missing_zh = find_missing(
        [{"name": "01a.mp4", "keep": True, "s_runs": [(m_zh_clip["s_start"], m_zh_clip["s_end"])]}],
        zh_script_toks, zh_script_raws,
    )
    assert missing_zh == []

    ar_script = "مَرْحَبًا بِكُمْ فِي هَذِهِ الْقَنَاةِ الْجَدِيدَةِ لِتَعْلِيمِ الْمُونْتَاجِ"
    ar_asr_segs = [{
        "start": 0.1,
        "end": 3.2,
        "text": "مرحبا بكم في هذه القناه الجديده لتعليم المونتاج",
        "words": [],
    }]
    ar_clip_words = flatten_words(ar_asr_segs)
    ar_script_toks, ar_script_raws = tokenize_raw_pairs(ar_script)
    assert ar_script_toks == [w[0] for w in ar_clip_words]
    m_ar_clip = match_clip_global(ar_clip_words, ar_script_toks)
    assert m_ar_clip["matched"] is True and m_ar_clip["ratio"] == 1.0
