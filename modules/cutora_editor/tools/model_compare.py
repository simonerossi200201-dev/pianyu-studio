# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Harness for the three quality-only model tables. No license filter.

Same method for every row:
  signed bias, ≥300 words, 6 languages, clean voice + music bed,
  30 words hand-labeled.

Tables:
  a. Italian align: VoxPopuli base 10k vs jonatasgrosman xlsr-53-italian
  b. ASR: medium vs large-v3 (WER + wall time). Golden does not run ASR;
     pin golden to the old profile if the product default changes.
  c. Align all langs: Meta MMS vs the six per-language models.

This script does not swap the product default. It writes JSON under
docs/plan/reports/. Run a table only when both weights are on disk.
"""
from __future__ import annotations

import argparse
import json
import os
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(REPO, "docs", "plan", "reports")

METHOD = {
    "signed_bias": True,
    "min_words": 300,
    "languages": ["en", "it", "es", "fr", "de", "pt"],
    "conditions": ["clean_voice", "with_music"],
    "hand_label_words": 30,
}


def _write(name: str, payload: dict) -> str:
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, name)
    payload = dict(payload)
    payload["method"] = METHOD
    payload["product_default_unchanged"] = True
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    return path


def table_a_italian_align():
    return _write("model_compare_it_align.json", {
        "table": "a",
        "candidates": [
            {"id": "align-it", "name": "VoxPopuli base 10k",
             "license": "CC-BY-NC-4.0", "status": "pending_measurement"},
            {"id": "align-it-xlsr",
             "name": "jonatasgrosman/wav2vec2-large-xlsr-53-italian",
             "license": "Apache-2.0", "status": "pending_measurement"},
        ],
        "note": "Pick by signed bias on ≥300 Italian words. Do not swap until measured.",
    })


def table_b_asr_size():
    return _write("model_compare_asr_size.json", {
        "table": "b",
        "candidates": [
            {"id": "asr-medium", "role": "fast", "status": "pending_measurement"},
            {"id": "asr-large-v3", "role": "quality_default_proposed",
             "status": "pending_measurement"},
            {"id": "asr-small", "role": "cpu_fallback", "status": "current_ceiling"},
        ],
        "golden_runs_asr": False,
        "note": "WER + wall time. Changing product default does not move golden hashes.",
    })


def table_c_mms():
    return _write("model_compare_mms.json", {
        "table": "c",
        "candidates": [
            {"id": "mms-align", "name": "Meta MMS CTC",
             "license": "CC-BY-NC-4.0", "status": "pending_measurement"},
            {"id": "per-lang-six", "name": "en/it/fr/es/de/pt wav2vec2",
             "status": "current"},
        ],
        "note": "Same 4d method, 6 languages. Do not drop the six until MMS wins on data.",
    })


def _models_root() -> str:
    return os.path.join(REPO, "runtime", "models")


def _model_dir(name: str) -> str | None:
    path = os.path.join(_models_root(), name)
    return path if os.path.isdir(path) else None


def _tokens(text: str) -> list[str]:
    import re
    s = (text or "").lower()
    s = re.sub(r"[^a-z0-9':]+", " ", s)
    return [w for w in s.split() if w]


def _wer(ref: list[str], hyp: list[str]) -> float:
    if not ref:
        return 0.0 if not hyp else 1.0
    n, m = len(ref), len(hyp)
    dp = list(range(m + 1))
    for i, rw in enumerate(ref, 1):
        prev = dp[0]
        dp[0] = i
        for j, hw in enumerate(hyp, 1):
            cur = dp[j]
            if rw == hw:
                dp[j] = prev
            else:
                dp[j] = 1 + min(prev, dp[j], dp[j - 1])
            prev = cur
    return dp[m] / float(n)


def _ffmpeg() -> str:
    exe = os.path.join(REPO, "runtime", "ffmpeg", "ffmpeg.exe")
    return exe if os.path.isfile(exe) else "ffmpeg"


def _extract_wav(src: str, dest: str) -> str:
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    import subprocess
    cmd = [_ffmpeg(), "-y", "-i", src, "-ac", "1", "-ar", "16000", dest]
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0 or not os.path.isfile(dest):
        raise RuntimeError("ffmpeg wav failed: %s" % (r.stderr[-400:] if r.stderr else ""))
    return dest


def _free_cuda(model=None) -> None:
    """Drop a WhisperModel and the CUDA cache. Stacking models caused
    STATUS_STACK_BUFFER_OVERRUN (0xC0000409) on T0037 large-v3."""
    import gc
    try:
        del model
    except Exception:
        pass
    gc.collect()
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            torch.cuda.ipc_collect()
    except Exception:
        pass


def _transcribe_fw(model_dir: str, wav: str, model=None) -> dict:
    from faster_whisper import WhisperModel
    own = model is None
    t_load = time.time()
    device, compute = "cpu", "int8"
    try:
        import torch
        if torch.cuda.is_available():
            device, compute = "cuda", "float16"
    except Exception:
        pass
    if own:
        model = WhisperModel(model_dir, device=device, compute_type=compute)
    load_s = time.time() - t_load
    try:
        t_inf = time.time()
        segs, info = model.transcribe(
            wav, language="en", word_timestamps=False, vad_filter=True)
        parts = []
        n_words = 0
        for s in segs:
            parts.append(s.text or "")
            n_words += len(_tokens(s.text or ""))
        inf_s = time.time() - t_inf
        text = " ".join(parts).strip()
        return {
            "device": device,
            "compute": compute,
            "load_s": round(load_s, 3),
            "infer_s": round(inf_s, 3),
            "wall_s": round(load_s + inf_s, 3),
            "n_words": n_words,
            "text": text,
            "language": getattr(info, "language", None),
        }
    finally:
        if own:
            _free_cuda(model)


TALKS = [
    ("T0035", r"D:\1_PROJECT\2026-09-15\C\T0035-实滢-0915-001", "T035.mp4"),
    ("T0036", r"D:\1_PROJECT\2026-09-15\C\T0036-实滢-0915-002", "T036.mp4"),
    ("T0037", r"D:\1_PROJECT\2026-09-15\C\T0037-实滢-0915-003", "T037.mp4"),
    ("T0038", r"D:\1_PROJECT\2026-09-15\C\T0038-实滢-0915-004", "T038.mp4"),
    ("T0039", r"D:\1_PROJECT\2026-09-15\C\T0039-实滢-0915-005", "T039.mp4"),
]


def _read_table_b() -> dict:
    path = os.path.join(OUT, "model_compare_asr_size.json")
    if not os.path.isfile(path):
        return {}
    try:
        return json.loads(open(path, encoding="utf-8").read())
    except (OSError, json.JSONDecodeError):
        return {}


def _upsert_model(talks: list, talk_id: str, ref_words: int, item: dict) -> None:
    for row in talks:
        if row.get("talk") != talk_id:
            continue
        models = row.setdefault("models", [])
        for i, m in enumerate(models):
            if m.get("id") == item["id"]:
                models[i] = item
                return
        models.append(item)
        return
    talks.append({"talk": talk_id, "ref_words": ref_words, "models": [item]})


def _cid_done(talks: list, cid: str, n_talks: int) -> bool:
    n = 0
    for row in talks:
        if any(m.get("id") == cid and m.get("status") == "measured"
               for m in row.get("models") or []):
            n += 1
    return n >= n_talks


def _write_table_b(talks: list, roles: dict) -> str:
    means = {}
    for cid in roles:
        wers = [m["wer"] for t in talks for m in t.get("models") or []
                if m.get("id") == cid and m.get("status") == "measured"]
        if wers:
            means[cid] = round(sum(wers) / len(wers), 4)
    return _write("model_compare_asr_size.json", {
        "table": "b",
        "talks": talks,
        "mean_wer": means,
        "golden_runs_asr": False,
        "condition": "clean_voice",
        "languages_run": ["en"],
        "note": (
            "WER vs copy.txt on five English sermons. "
            "Do not swap the product default from this file until turbo "
            "wins or ties every talk."
        ),
    })


def measure_table_b(only_id: str | None = None):
    """WER + wall time on T0035–T0039. One CUDA model per process.

    Parent spawns a child per remaining model so a CTranslate2 abort
    (0xC0000409) cannot wipe earlier rows. Re-runs skip measured pairs.
    """
    import subprocess
    import sys

    models = {
        "asr-small": _model_dir("asr_small"),
        "asr-medium": _model_dir("asr_medium"),
        "asr-large-v3": _model_dir("asr_large-v3") or _model_dir("asr_large_v3"),
        "asr-large-v3-turbo": _model_dir("asr_large-v3-turbo"),
    }
    roles = {
        "asr-small": "cpu_fallback",
        "asr-medium": "fast",
        "asr-large-v3": "quality_default_proposed",
        "asr-large-v3-turbo": "quality_fast",
    }
    order = ("asr-small", "asr-medium", "asr-large-v3", "asr-large-v3-turbo")
    work = os.path.join(OUT, "model_compare_work")
    prepared = []
    for talk_id, folder, mp4_name in TALKS:
        copy = os.path.join(folder, "copy.txt")
        audio = os.path.join(folder, "clips", "_stitched", mp4_name)
        ref = _tokens(open(copy, encoding="utf-8").read()) if os.path.isfile(copy) else []
        wav = os.path.join(work, talk_id.lower() + ".wav")
        if not os.path.isfile(audio) or len(ref) < METHOD["min_words"]:
            prepared.append((talk_id, None, ref))
            continue
        if not os.path.isfile(wav):
            print("extract", audio, "->", wav, flush=True)
            _extract_wav(audio, wav)
        prepared.append((talk_id, wav, ref))

    existing = _read_table_b()
    talks = list(existing.get("talks") or [])
    if not talks:
        talks = [{"talk": tid, "ref_words": len(ref), "models": []}
                 for tid, _wav, ref in prepared]

    if only_id is None:
        script = os.path.join(REPO, "tools", "model_compare.py")
        py = sys.executable
        env = os.environ.copy()
        env["PYTHONPATH"] = REPO + os.pathsep + env.get("PYTHONPATH", "")
        env["PYTHONUNBUFFERED"] = "1"
        for cid in order:
            if _cid_done(talks, cid, len(prepared)):
                print("skip", cid, "already measured", flush=True)
                continue
            print("spawn", cid, flush=True)
            r = subprocess.run(
                [py, "-u", script, "b", "--measure", "--measure-id", cid],
                cwd=REPO, env=env)
            print("child", cid, "rc", r.returncode, flush=True)
            talks = list((_read_table_b().get("talks") or talks))
        return _write_table_b(talks, roles)

    cid = only_id
    if cid not in roles:
        raise SystemExit("unknown --measure-id %s" % cid)
    from faster_whisper import WhisperModel
    path = models.get(cid)
    device, compute = "cpu", "int8"
    try:
        import torch
        if torch.cuda.is_available():
            device, compute = "cuda", "float16"
    except Exception:
        pass
    model = None
    load_s = 0.0
    if path:
        print("load", cid, path, flush=True)
        t_load = time.time()
        model = WhisperModel(path, device=device, compute_type=compute)
        load_s = time.time() - t_load
    first = True
    try:
        for talk_id, wav, ref in prepared:
            already = any(
                m.get("id") == cid and m.get("status") == "measured"
                for row in talks if row.get("talk") == talk_id
                for m in row.get("models") or [])
            if already:
                print("skip", talk_id, cid, flush=True)
                continue
            item = {"id": cid, "role": roles[cid], "path": path}
            if wav is None:
                item["status"] = "pending_measurement"
            elif not path:
                item["status"] = "pending_measurement"
                item["reason"] = "weights not on disk"
            else:
                print("transcribe", talk_id, cid, flush=True)
                hit = _transcribe_fw(path, wav, model=model)
                hyp = _tokens(hit["text"])
                this_load = round(load_s, 3) if first else 0.0
                first = False
                item.update({
                    "status": "measured",
                    "device": hit["device"],
                    "compute": hit["compute"],
                    "wall_s": round(this_load + hit["infer_s"], 3),
                    "infer_s": hit["infer_s"],
                    "load_s": this_load,
                    "hyp_words": hit["n_words"],
                    "wer": round(_wer(ref, hyp), 4),
                })
            _upsert_model(talks, talk_id, len(ref), item)
            _write_table_b(talks, roles)
            print("checkpoint", talk_id, cid, flush=True)
    finally:
        _free_cuda(model)
    return _write_table_b(talks, roles)


def _ctc_decode_wer(model_dir: str, wav: str, ref_text: str, lang: str) -> dict:
    """Greedy CTC decode WER. Reference timestamps = wav2vec2 if loadable."""
    import numpy as np
    import torch
    import torchaudio
    from transformers import Wav2Vec2ForCTC, Wav2Vec2Processor
    t0 = time.time()
    wav_t, sr = torchaudio.load(wav)
    audio = wav_t.mean(dim=0).numpy()
    if int(sr) != 16000:
        n = int(round(len(audio) * 16000 / float(sr)))
        audio = np.interp(np.linspace(0, 1, n), np.linspace(0, 1, len(audio)), audio)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    proc = Wav2Vec2Processor.from_pretrained(model_dir)
    model = Wav2Vec2ForCTC.from_pretrained(model_dir).to(device)
    load_s = time.time() - t0
    t1 = time.time()
    inputs = proc(audio, sampling_rate=16000, return_tensors="pt", padding=True)
    inputs = {k: v.to(device) for k, v in inputs.items()}
    with torch.no_grad():
        logits = model(**inputs).logits
    pred = torch.argmax(logits, dim=-1)
    text = proc.batch_decode(pred)[0]
    inf_s = time.time() - t1
    ref = _tokens(ref_text)
    hyp = _tokens(text)
    return {
        "device": device,
        "load_s": round(load_s, 3),
        "infer_s": round(inf_s, 3),
        "wall_s": round(load_s + inf_s, 3),
        "wer": round(_wer(ref, hyp), 4),
        "hyp_words": len(hyp),
        "ref_words": len(ref),
        "text": text[:400],
        "lang": lang,
    }


def measure_table_a():
    """Italian align: VoxPopuli (current) vs XLSR. g03 audio. wav2vec2 = ref."""
    g03 = os.path.join(REPO, "tests", "golden", "_media", "g03", "video.mp4")
    proj = os.path.join(REPO, "tests", "golden", "g03_horizontal_it", "project.asproj.json")
    xlsr = _model_dir("align_it")
    work = os.path.join(OUT, "model_compare_work")
    wav = os.path.join(work, "g03.wav")
    ref_text = ""
    if os.path.isfile(proj):
        data = json.loads(open(proj, encoding="utf-8").read())
        tasks = data.get("tasks") or []
        if tasks:
            ref_text = str(tasks[0].get("text") or "")
    rows = [
        {"id": "align-it", "name": "VoxPopuli base 10k",
         "license": "CC-BY-NC-4.0", "status": "current",
         "note": "product default; used as wav2vec2 reference"},
    ]
    if not os.path.isfile(g03) or not xlsr:
        rows.append({
            "id": "align-it-xlsr",
            "name": "jonatasgrosman/wav2vec2-large-xlsr-53-italian",
            "status": "pending_measurement",
            "reason": "need g03 media and XLSR weights",
        })
    else:
        if not os.path.isfile(wav):
            _extract_wav(g03, wav)
        print("ctc xlsr g03", flush=True)
        try:
            hit = _ctc_decode_wer(xlsr, wav, ref_text, "it")
            rows.append({
                "id": "align-it-xlsr",
                "name": "jonatasgrosman/wav2vec2-large-xlsr-53-italian",
                "license": "Apache-2.0",
                "status": "measured",
                "audio": "g03_horizontal_it",
                "signed_bias_ref": "align-it (VoxPopuli)",
                **{k: hit[k] for k in (
                    "wer", "wall_s", "load_s", "infer_s", "hyp_words",
                    "ref_words", "device")},
            })
        except Exception as exc:
            rows.append({
                "id": "align-it-xlsr",
                "status": "pending_measurement",
                "reason": str(exc)[:300],
            })
    return _write("model_compare_it_align.json", {
        "table": "a",
        "candidates": rows,
        "note": "Italian g03. Do not swap align-it until XLSR wins on data.",
    })


def measure_table_c():
    mms = _model_dir("align_mms")
    g03 = os.path.join(REPO, "tests", "golden", "_media", "g03", "video.mp4")
    proj = os.path.join(REPO, "tests", "golden", "g03_horizontal_it", "project.asproj.json")
    ref_text = ""
    if os.path.isfile(proj):
        data = json.loads(open(proj, encoding="utf-8").read())
        tasks = data.get("tasks") or []
        if tasks:
            ref_text = str(tasks[0].get("text") or "")
    rows = [
        {"id": "per-lang-six", "name": "en/it/fr/es/de/pt wav2vec2",
         "status": "current"},
    ]
    if not mms or not os.path.isfile(g03):
        rows.append({
            "id": "mms-align",
            "name": "MahmoudAshraf/mms-300m-1130-forced-aligner",
            "status": "pending_measurement",
            "reason": "need MMS weights and g03",
        })
    else:
        work = os.path.join(OUT, "model_compare_work")
        wav = os.path.join(work, "g03.wav")
        if not os.path.isfile(wav):
            _extract_wav(g03, wav)
        print("ctc mms g03", flush=True)
        try:
            hit = _ctc_decode_wer(mms, wav, ref_text, "it")
            rows.append({
                "id": "mms-align",
                "name": "MahmoudAshraf/mms-300m-1130-forced-aligner",
                "license": "CC-BY-NC-4.0",
                "status": "measured",
                "audio": "g03_horizontal_it",
                "signed_bias_ref": "per-lang wav2vec2",
                **{k: hit[k] for k in (
                    "wer", "wall_s", "load_s", "infer_s", "hyp_words",
                    "ref_words", "device")},
            })
        except Exception as exc:
            rows.append({
                "id": "mms-align",
                "status": "pending_measurement",
                "reason": str(exc)[:300],
            })
    return _write("model_compare_mms.json", {
        "table": "c",
        "candidates": rows,
        "note": "MMS is the 300M forced-aligner (CTC), not mms-1b-all adapters. Do not drop the six until MMS wins.",
    })


def main():
    p = argparse.ArgumentParser()
    p.add_argument("table", choices=["a", "b", "c", "all"])
    p.add_argument("--measure", action="store_true",
                   help="run rows whose weights are on disk; others stay pending")
    p.add_argument("--measure-id", default=None,
                   help="one catalog id in this process (parent spawns one each)")
    args = p.parse_args()
    t0 = time.time()
    written = []
    if args.table in ("a", "all"):
        written.append(measure_table_a() if args.measure else table_a_italian_align())
    if args.table in ("b", "all"):
        written.append(
            measure_table_b(args.measure_id) if args.measure else table_b_asr_size())
    if args.table in ("c", "all"):
        written.append(measure_table_c() if args.measure else table_c_mms())
    print("wrote", written, "in %.2fs" % (time.time() - t0))


if __name__ == "__main__":
    main()
