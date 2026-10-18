# -*- coding: utf-8 -*-
"""Compare ASR with / without glossary on a real sermon clip (read-only)."""
from __future__ import annotations

import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

FFMPEG_DIR = os.path.join(REPO, "runtime", "ffmpeg")
os.environ["PATH"] = FFMPEG_DIR + os.pathsep + os.environ.get("PATH", "")

from cutora.core.runtime import init_torch_runtime

init_torch_runtime(render_child=False)

CLIP = os.path.join(
    "D:\\1_PROJECT", "过时", "260829", "A", "Grok", "0829", "视频输出", "02c.mp4")
FALLBACK = os.path.join(
    REPO, "tests", "golden", "_media", "g01", "video.mp4")
ASR_DIR = os.path.join(REPO, "runtime", "models", "asr_small")
OUT = os.path.join(REPO, "docs", "plan", "reports", "T12d_glossary_compare.json")

GLOSSARY = (
    "Deuteronomy, Hear, O Israel, The LORD our God, "
    "The LORD is one, Bible"
)


def _join(segments) -> str:
    parts = []
    for seg in segments:
        text = (getattr(seg, "text", None) or "").strip()
        if text:
            parts.append(text)
    return " ".join(parts)


def main() -> int:
    media = CLIP if os.path.isfile(CLIP) else FALLBACK
    if not os.path.isfile(media):
        raise SystemExit("no media: %s" % media)
    if not os.path.isdir(ASR_DIR):
        raise SystemExit("no asr_small at %s" % ASR_DIR)
    from faster_whisper import WhisperModel
    model = WhisperModel(ASR_DIR, device="cuda", compute_type="float16")
    segs_off, _ = model.transcribe(
        media, language="en", vad_filter=True, word_timestamps=False)
    text_off = _join(segs_off)
    segs_on, _ = model.transcribe(
        media, language="en", vad_filter=True, word_timestamps=False,
        initial_prompt=GLOSSARY, hotwords=GLOSSARY)
    text_on = _join(segs_on)
    data = {
        "media": media,
        "model": ASR_DIR,
        "glossary": GLOSSARY,
        "without": text_off,
        "with": text_on,
        "expected_phrase": "Hear, O Israel",
        "without_has_hear_o_israel": "hear, o israel" in text_off.lower()
        or "hear o israel" in text_off.lower(),
        "with_has_hear_o_israel": "hear, o israel" in text_on.lower()
        or "hear o israel" in text_on.lower(),
        "without_has_hero": "hero" in text_off.lower(),
        "with_has_hero": "hero" in text_on.lower(),
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    print("media", media)
    print("WITHOUT:", text_off)
    print("WITH:   ", text_on)
    print("wrote", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
