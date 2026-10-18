# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q162 · 批量「语音转文本」的纯逻辑：识别结果 → 文本 / 字幕文件（没有 Qt）。

* ``transcript_text(segs)``：一行一句的纯文本；``timestamps=True`` 每行前面带 ``[mm:ss.xx]``。
* ``write_outputs(src, segs, dest_dir, formats)``：按勾选写 ``<源文件名>.txt`` / ``.时间轴.txt`` / ``.srt``。
* ``merged_text(items)``：多条合成一份（每条一个标题）。
* ``transcribe_file(path, language, asr_dir, …)``：用自动字幕同一个 AlignmentWorker 跑纯识别（不给脚本），
  同步返回分段；界面那边在自己的线程里逐个文件调它。
"""
from __future__ import annotations

import os
import time

FORMATS = ("txt", "txt_time", "srt")
FORMAT_LABELS = {"txt": "纯文本 .txt", "txt_time": "带时间戳 .txt", "srt": "字幕 .srt"}
MEDIA_EXTS = (".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi", ".mp3", ".wav", ".m4a", ".aac", ".flac",
              ".ogg", ".opus", ".wma", ".aiff", ".aif")


def _tc(sec: float) -> str:
    sec = max(0.0, float(sec or 0.0))
    m, s = divmod(sec, 60.0)
    h, m = divmod(int(m), 60)
    return ("%d:%02d:%05.2f" % (h, m, s)) if h else ("%02d:%05.2f" % (m, s))


def _clean(text) -> str:
    return " ".join(str(text or "").replace("\r", " ").replace("\n", " ").split())


def transcript_text(segs, *, timestamps: bool = False) -> str:
    lines = []
    for seg in segs or []:
        if not isinstance(seg, dict) or seg.get("is_break"):
            continue
        text = _clean(seg.get("text"))
        if not text:
            continue
        if timestamps:
            lines.append("[%s → %s] %s" % (_tc(seg.get("start", 0.0)), _tc(seg.get("end", 0.0)), text))
        else:
            lines.append(text)
    return "\n".join(lines) + ("\n" if lines else "")


def stem_of(path: str) -> str:
    return os.path.splitext(os.path.basename(str(path or "")))[0]


def output_paths(src: str, dest_dir: str | None, formats) -> dict:
    d = dest_dir or os.path.dirname(os.path.abspath(src))
    st = stem_of(src)
    out = {}
    for f in formats or ():
        if f == "txt":
            out[f] = os.path.join(d, st + ".txt")
        elif f == "txt_time":
            out[f] = os.path.join(d, st + ".时间轴.txt")
        elif f == "srt":
            out[f] = os.path.join(d, st + ".srt")
    return out


def write_outputs(src: str, segs, dest_dir: str | None, formats, *, bom: bool = False) -> dict:
    """写文件，返回 {格式: 路径}。目录不存在就建。"""
    paths = output_paths(src, dest_dir, formats)
    written = {}
    for fmt, path in paths.items():
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        if fmt == "srt":
            from cutora.subtitle.formats import save_subtitle
            from cutora.subtitle.model import segments_from_dicts
            rows = [dict(s) for s in (segs or []) if isinstance(s, dict) and not s.get("is_break")
                    and _clean(s.get("text"))]
            save_subtitle(path, segments_from_dicts(rows), "srt", bom=bom)
        else:
            text = transcript_text(segs, timestamps=(fmt == "txt_time"))
            with open(path, "w", encoding="utf-8-sig" if bom else "utf-8", newline="\n") as fh:
                fh.write(text)
        written[fmt] = path
    return written


def merged_text(items, *, timestamps: bool = False) -> str:
    """items = [(源路径, 分段), …] → 一份带标题的合集。"""
    parts = []
    for src, segs in items or []:
        body = transcript_text(segs, timestamps=timestamps).rstrip("\n")
        parts.append("## %s\n%s\n" % (os.path.basename(str(src)), body or "（没有识别到内容）"))
    return "\n".join(parts)


def merged_path(dest_dir: str | None, first_src: str | None) -> str:
    d = dest_dir or (os.path.dirname(os.path.abspath(first_src)) if first_src else os.getcwd())
    return os.path.join(d, "转写合集_%s.txt" % time.strftime("%Y%m%d_%H%M"))


def list_media(entries) -> list:
    """文件 / 文件夹混着给 → 媒体文件列表（文件夹只看一层），按第一次出现去重。"""
    out, seen = [], set()

    def _add(p):
        k = os.path.normcase(os.path.abspath(p))
        if k not in seen:
            seen.add(k)
            out.append(p)

    for raw in entries or []:
        item = str(raw or "").strip().strip('"')
        if not item:
            continue
        if os.path.isdir(item):
            for name in sorted(os.listdir(item)):
                p = os.path.join(item, name)
                if os.path.isfile(p) and os.path.splitext(name)[1].lower() in MEDIA_EXTS:
                    _add(p)
        elif os.path.isfile(item) and os.path.splitext(item)[1].lower() in MEDIA_EXTS:
            _add(item)
    return out


def transcribe_file(path: str, *, language=None, asr_dir=None, on_progress=None, should_stop=None) -> list:
    """同步跑一条媒体的纯识别（自动字幕那条 AlignmentWorker，不给脚本），返回 [{"start","end","text","words"}, …]。

    ``language`` None / "auto" = 自动检测。失败抛 RuntimeError。"""
    from cutora.asr.align_worker import AlignmentWorker, fix_segment_durations
    from cutora.asr.languages import whisper_language_arg
    lang = whisper_language_arg(language) if language else None
    worker = AlignmentWorker(path, "", style={}, video_w=1920, language=lang, asr_dir=asr_dir)
    got = {"segs": None, "err": None}
    worker.finished.connect(lambda s: got.update(segs=s))
    worker.error.connect(lambda m: got.update(err=m))
    if callable(on_progress):
        worker.progress.connect(lambda p: on_progress(int(p)))
    if callable(should_stop):
        # AlignmentWorker 只在阶段之间看 _is_running：开个小线程盯着外面的取消标志，到了就 stop()
        import threading

        def _watch():
            while got["segs"] is None and got["err"] is None:
                if should_stop():
                    worker.stop()
                    break
                time.sleep(0.2)
        threading.Thread(target=_watch, daemon=True).start()
    worker.run()
    if got["err"]:
        raise RuntimeError(str(got["err"]))
    segs = got["segs"]
    if segs is None:
        if callable(should_stop) and should_stop():
            raise RuntimeError("已取消")
        raise RuntimeError("没有识别结果")
    segs = [dict(s) for s in segs if isinstance(s, dict)]
    try:
        fix_segment_durations(segs, min_dur=0.6, next_gap_pad=0.05, rebuild_words_first_only=True)
    except Exception:
        pass
    return segs
