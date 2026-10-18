#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""T35: time ASR stages on Luca's stitch timeline (mix wav + transcribe + align)."""
from __future__ import annotations

import json
import os
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
os.chdir(REPO)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("CUTORA_SKIP_ENCODER_PROBE", "1")


def _ensure_ffmpeg_path():
    ffdir = os.path.join(REPO, "runtime", "ffmpeg")
    path = os.environ.get("PATH") or ""
    parts = [os.path.normcase(p) for p in path.split(os.pathsep)]
    if os.path.normcase(ffdir) not in parts:
        os.environ["PATH"] = ffdir + os.pathsep + path


def _default_project():
    cutora = os.path.join("D:\\", "1_PROJECT", "2026-09-15", "A认真.cutora")
    if os.path.isfile(cutora):
        return cutora
    root = os.path.join("D:\\", "1_PROJECT", "2026-09-15", "A")
    if not os.path.isdir(root):
        return None
    for dirpath, _d, names in os.walk(root):
        if "stitched.asproj.json" not in names:
            continue
        if os.sep + "T023" + os.sep not in (dirpath + os.sep):
            continue
        return os.path.join(dirpath, "stitched.asproj.json")
    return None


def _flatten_words(segments):
    words = []
    for seg in segments or []:
        for w in seg.get("words") or []:
            token = w.get("word") or w.get("text") or ""
            if not token:
                continue
            words.append((str(token), w.get("start"), w.get("end")))
    return words


def _profile_one(task, model_holder):
    from cutora.asr.align_worker import AlignmentWorker
    from cutora.asr.engine import get_align_model, get_whisperx_model
    from cutora.asr.timeline_wav import is_multi_clip_timeline, render_timeline_wav
    from cutora.batch.script_stitcher import (
        compute_envelope,
        last_word_end,
        transcript_incomplete,
    )
    import whisperx

    times = {
        "transcribe_s": 0.0,
        "align_s": 0.0,
        "force_align_s": 0.0,
        "load_audio_s": 0.0,
        "load_align_s": 0.0,
        "smart_seg_s": 0.0,
    }
    text = str(task.get("text") or "")
    times["text_len"] = len(text)
    times["nseg_existing"] = len(task.get("segments") or [])
    times["dur"] = float(task.get("duration") or 0)
    times["name"] = str(task.get("name") or "")
    times["dims"] = task.get("dims")
    times["path"] = "align_worker"

    t0 = time.perf_counter()
    if is_multi_clip_timeline(task):
        wav = render_timeline_wav(task)
    else:
        wav = task.get("video")
    times["render_wav_s"] = round(time.perf_counter() - t0, 3)
    times["wav"] = wav
    print("WAV", wav, "s", times["render_wav_s"], flush=True)

    t1 = time.perf_counter()
    model = get_whisperx_model()
    times["load_asr_s"] = round(time.perf_counter() - t1, 3)
    times["model_same_as_prev"] = bool(
        model_holder.get("model") is not None and model is model_holder.get("model"))
    model_holder["model"] = model

    worker = AlignmentWorker(
        wav, text, style=task.get("style") or {}, language="en")

    orig_load = worker._load_audio_bundle
    orig_force = worker.force_align_user_text
    orig_align = worker._align_or_transcribe
    orig_smart = worker.smart_segmentation
    orig_plain = worker._transcribe_without_align

    def load_bundle():
        t = time.perf_counter()
        out = orig_load()
        times["load_audio_s"] = round(time.perf_counter() - t, 3)
        return out

    def force(*a, **k):
        t = time.perf_counter()
        out = orig_force(*a, **k)
        times["force_align_s"] = round(time.perf_counter() - t, 3)
        times["force_missing_ratio"] = (out[1] or {}).get("missing_ratio")
        times["force_used_nseg"] = len(out[0] or [])
        return out

    def align_or(*a, **k):
        t = time.perf_counter()
        out = orig_align(*a, **k)
        times["align_or_transcribe_s"] = round(time.perf_counter() - t, 3)
        return out

    def smart(*a, **k):
        t = time.perf_counter()
        out = orig_smart(*a, **k)
        times["smart_seg_s"] = round(time.perf_counter() - t, 3)
        return out

    def plain(*a, **k):
        t = time.perf_counter()
        out = orig_plain(*a, **k)
        times["transcribe_s"] = round(time.perf_counter() - t, 3)
        times["used_transcribe_fallback"] = True
        return out

    worker._load_audio_bundle = load_bundle
    worker.force_align_user_text = force
    worker._align_or_transcribe = align_or
    worker.smart_segmentation = smart
    worker._transcribe_without_align = plain

    t_pipe = time.perf_counter()
    segs = worker._run_pipeline(model)
    times["pipeline_s"] = round(time.perf_counter() - t_pipe, 3)
    times["nseg_out"] = len(segs or [])
    times["align_note"] = getattr(worker, "align_note", None)
    times["approximate_words"] = bool(getattr(worker, "approximate_words", False))

    t4 = time.perf_counter()
    align_model, _meta = get_align_model("en")
    times["load_align_s"] = round(time.perf_counter() - t4, 3)
    times["align_same_as_prev"] = bool(
        model_holder.get("align") is not None and align_model is model_holder.get("align"))
    model_holder["align"] = align_model

    words = _flatten_words(segs)
    times["n_words"] = len(words)

    t6 = time.perf_counter()
    audio = whisperx.load_audio(wav)
    env = compute_envelope(audio)
    incomplete = transcript_incomplete(words, times["dur"], env)
    times["bug06_envelope_s"] = round(time.perf_counter() - t6, 3)
    times["bug06_incomplete"] = bool(incomplete)
    times["last_word_end"] = round(float(last_word_end(words) or 0), 3)

    t7 = time.perf_counter()
    missing = 0
    for _tok, s, e in words:
        if not isinstance(s, (int, float)) or not isinstance(e, (int, float)):
            missing += 1
    times["missing_check_s"] = round(time.perf_counter() - t7, 3)
    times["missing_words"] = missing
    times["missing_ratio"] = round(missing / max(1, len(words)), 4)

    t8 = time.perf_counter()
    task["segments"] = list(segs or [])
    times["writeback_s"] = round(time.perf_counter() - t8, 3)

    times["wall_s"] = round(
        float(times.get("render_wav_s") or 0)
        + float(times.get("load_asr_s") or 0)
        + float(times.get("pipeline_s") or 0)
        + float(times.get("bug06_envelope_s") or 0)
        + float(times.get("missing_check_s") or 0)
        + float(times.get("writeback_s") or 0),
        3)
    return times


def main():
    _ensure_ffmpeg_path()
    from cutora.core.runtime import init_torch_runtime
    init_torch_runtime(render_child=False)
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication(["t35-asr"])
    from cutora.model.project_io import deserialize_render_task

    path = (sys.argv[1] if len(sys.argv) > 1 else None) or _default_project()
    if not path or not os.path.isfile(path):
        print("NO_PROJECT", path)
        return 2
    raw = json.loads(open(path, encoding="utf-8").read())
    tasks = [deserialize_render_task(t) for t in (raw.get("tasks") or [])]
    if not tasks:
        print("NO_TASKS")
        return 2
    print("PROJECT", path, "n_tasks", len(tasks), flush=True)
    holder = {}
    rows = []
    grand = time.perf_counter()
    for i, task in enumerate(tasks):
        print("TASK_BEGIN", i, task.get("name"), "dur", task.get("duration"), flush=True)
        row = _profile_one(task, holder)
        row["i"] = i
        rows.append(row)
        print("TASK", i, json.dumps(row, ensure_ascii=False), flush=True)
    total = time.perf_counter() - grand
    out = {"wall_s_all": round(total, 3), "tasks": rows, "project": path}
    print("WALL_S_ALL", round(total, 3), flush=True)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
