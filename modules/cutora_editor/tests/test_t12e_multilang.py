# -*- coding: utf-8 -*-
"""T12e 提交 4：6 语种（en/it/es/fr/de/ru）各 3 句：朗读 → 识别 → 词级时间戳 → 渲染。"""
from __future__ import annotations

import json
import os
import subprocess
import wave

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(REPO, "tests", "data", "multilang")
ESPEAK_DIR = os.path.join(REPO, "build", "espeak-ng", "eSpeak NG")
FFMPEG = os.path.join(REPO, "runtime", "ffmpeg", "ffmpeg.exe")
SHOT_DIR = os.path.join(REPO, "docs", "plan", "reports", "img")
LANGS = ("en", "it", "es", "fr", "de", "ru")


def _sentences() -> dict:
    with open(os.path.join(DATA, "sentences.json"), encoding="utf-8") as fh:
        return json.load(fh)


def _text(lang: str) -> str:
    return " ".join(_sentences()[lang])


def _mp4(lang: str) -> str:
    return os.path.join(DATA, "%s.mp4" % lang)


def _speak_wav(text: str, wav_path: str, lang: str) -> None:
    exe = os.path.join(ESPEAK_DIR, "espeak-ng.exe")
    if not os.path.isfile(exe):
        raise FileNotFoundError("espeak-ng.exe not in build/espeak-ng (test-only)")
    env = dict(os.environ)
    env["ESPEAK_DATA_PATH"] = os.path.join(ESPEAK_DIR, "espeak-ng-data")
    os.makedirs(os.path.dirname(wav_path) or ".", exist_ok=True)
    proc = subprocess.run(
        [exe, "-v", lang, "-w", wav_path, text],
        env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=60, cwd=ESPEAK_DIR,
    )
    if proc.returncode != 0 or not os.path.isfile(wav_path):
        raise RuntimeError("espeak-ng %s: %s" % (lang, proc.stderr or proc.returncode))


def _mux(wav_path: str, mp4_path: str) -> None:
    with wave.open(wav_path, "rb") as w:
        dur = w.getnframes() / float(w.getframerate() or 1)
    dur = max(2.0, dur + 0.4)
    proc = subprocess.run(
        [FFMPEG, "-y",
         "-f", "lavfi", "-i", "color=c=0x1C1815:s=960x540:d=%.2f" % dur,
         "-i", wav_path,
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast",
         "-c:a", "aac", "-shortest", mp4_path],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=60,
    )
    if proc.returncode != 0 or not os.path.isfile(mp4_path):
        raise RuntimeError("ffmpeg: %s" % (proc.stderr or "")[-400:])


def _ensure_media() -> None:
    os.makedirs(DATA, exist_ok=True)
    for lang in LANGS:
        mp4 = _mp4(lang)
        if os.path.isfile(mp4):
            continue
        wav = os.path.join(DATA, "%s.wav" % lang)
        _speak_wav(_text(lang), wav, lang)
        _mux(wav, mp4)


def test_low_confidence_prompt_appears():
    from cutora.asr.detect import build_detection_result
    det = build_detection_result("it", 0.21)
    assert det["low_confidence"] is True
    assert "低置信" in (det.get("prompt_zh") or "")


def test_six_language_recognize_align_render():
    os.environ["PATH"] = os.path.join(REPO, "runtime", "ffmpeg") + os.pathsep + os.environ.get("PATH", "")
    from cutora.core.runtime import init_torch_runtime
    init_torch_runtime(render_child=False)
    _ensure_media()
    from cutora.app import apply_ui_locale
    apply_ui_locale()
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QFont, QFontMetrics, QImage, QPainter
    from PySide6.QtWidgets import QApplication
    from cutora.ui.panels.fonts import load_embedded_fonts
    from cutora.asr.detect import detect_from_array
    from cutora.asr.engine import get_whisperx_model, whisperx
    from cutora.asr.languages import get_language

    app = QApplication.instance() or QApplication(["t12e-ml"])
    load_embedded_fonts()
    model = get_whisperx_model()
    detected_ok = 0
    os.makedirs(SHOT_DIR, exist_ok=True)
    report = {}
    for lang in LANGS:
        path = _mp4(lang)
        audio = whisperx.load_audio(path)
        det = detect_from_array(audio, model=model)
        if det.get("language") == lang:
            detected_ok += 1
        fw = model.model
        segs_iter, _info = fw.transcribe(
            audio, language=lang, word_timestamps=True, vad_filter=True)
        segs = list(segs_iter)
        assert segs, lang
        words = []
        texts = []
        for seg in segs:
            texts.append((getattr(seg, "text", None) or "").strip())
            for w in list(getattr(seg, "words", None) or []):
                token = getattr(w, "word", None)
                if not token:
                    continue
                words.append({
                    "word": token,
                    "start": getattr(w, "start", None),
                    "end": getattr(w, "end", None),
                })
        assert words, lang
        timed = [w for w in words if isinstance(w["start"], (int, float))
                 and isinstance(w["end"], (int, float))]
        assert timed, lang
        rec = " ".join(t for t in texts if t)
        src = _text(lang)
        entry = get_language(lang) or {}
        if entry.get("line_break") == "space":
            metrics = QFontMetrics(QFont("Arimo", 28))
            missing = [ch for ch in src if not ch.isspace() and not metrics.inFontUcs4(ord(ch))]
            assert missing == [], (lang, missing)
        img = QImage(960, 540, QImage.Format.Format_RGB32)
        img.fill(QColor("#1C1815"))
        p = QPainter(img)
        p.setPen(QColor("#EFE7DA"))
        p.setFont(QFont("Arimo", 22))
        p.drawText(
            img.rect().adjusted(36, 36, -36, -36),
            int(Qt.AlignmentFlag.AlignCenter) | int(Qt.TextFlag.TextWordWrap),
            "[%s]\n%s" % (lang, src),
        )
        p.end()
        shot = os.path.join(SHOT_DIR, "T12e_lang_%s.png" % lang)
        assert img.save(shot, "PNG"), shot
        report[lang] = {
            "detected": det.get("language"),
            "confidence": det.get("confidence"),
            "low_confidence": det.get("low_confidence"),
            "prompt_zh": det.get("prompt_zh"),
            "words": len(words),
            "timed_words": len(timed),
            "recognized": rec,
            "shot": shot,
        }
    assert detected_ok >= 5, report
    dest = os.path.join(REPO, "docs", "plan", "reports", "T12e_multilang.json")
    with open(dest, "w", encoding="utf-8", newline="\n") as fh:
        json.dump({"detected_ok": detected_ok, "langs": report}, fh,
                  ensure_ascii=False, indent=2)
        fh.write("\n")
