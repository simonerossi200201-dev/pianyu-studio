# -*- coding: utf-8 -*-
"""Dump g08 export audio filter_complex: ac520ed path vs aa3613a path.

Required by Q30 retry: character-level diff of _build_audio_tracks rows
and the final filter_complex string, before any product-code change.
"""
from __future__ import annotations

import copy
import difflib
import json
import os
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "tools"))
os.chdir(REPO)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

OUT = os.path.join(REPO, "docs", "plan", "reports", "Q30_filter_diff.txt")


def _aa3613a_resolve(track, fx_settings=None):
    """resolve_spatial_for_track as committed in aa3613a (verbatim)."""
    from cutora.audio.spatial import DEFAULT_PRESET, PRESETS

    fx = fx_settings if isinstance(fx_settings, dict) else {}
    is_main = bool(track.get("is_main"))
    role = str(track.get("audio_role") or ("main" if is_main else ""))
    if role == "sfx":
        return False, DEFAULT_PRESET, 0.0, 0.0
    enabled = bool(track.get("spatial_enabled", False))
    preset = str(track.get("spatial_preset") or fx.get("spatial_preset") or DEFAULT_PRESET)
    if preset not in PRESETS:
        preset = DEFAULT_PRESET
    wet = track.get("spatial_amount")
    if wet is None or (float(wet) <= 0 and enabled):
        wet = fx.get("spatial_wet", PRESETS[preset]["wet"])
    try:
        wet = float(wet)
    except (TypeError, ValueError):
        wet = PRESETS[preset]["wet"]
    width = fx.get("spatial_width", PRESETS[preset]["width"])
    try:
        width = float(width)
    except (TypeError, ValueError):
        width = PRESETS[preset]["width"]
    return bool(enabled), preset, wet, width


def _build_filter(worker, project, resolve_fn):
    """Copy of RenderWorker._mix_audio_tracks filter construction, no ffmpeg."""
    from cutora.audio import spatial as S
    from cutora.render.export_params import audio_mix_trim_tail
    from cutora.render.render_worker import _SFX

    tracks = worker._build_audio_tracks(project)
    target_sr = 48000
    _audio_trim_sec = float(project.get("trim_start_sec", 0.0))
    _audio_trim_end_sec = float(project.get("trim_end_sec", 0.0))
    src_volume = float(project.get("src_volume", 1.0))
    if tracks and src_volume != 1.0:
        for t in tracks:
            if t.get("is_main") and abs(t.get("gain_db", 0.0)) < 0.01:
                import math
                t["gain_db"] = 20.0 * math.log10(max(src_volume, 1e-6))

    fx_settings = project.get("audio_fx") if isinstance(project.get("audio_fx"), dict) else None
    if fx_settings is None and _SFX is not None:
        fx_settings = _SFX.load_fx_settings()

    has_video = True
    audio_index0 = 1 if has_video else 0
    filter_parts = []
    rows_out = []
    for idx, t in enumerate(tracks):
        gain_db = float(t.get("gain_db", 0.0))
        trim_in = float(t.get("trim_in", 0.0))
        duration = t.get("duration")
        if duration is not None:
            duration = max(0.01, float(duration))
        start_sec = float(t.get("start", 0.0))
        pitch = float(t.get("pitch_semitones", 0.0))
        fade_in_d = float(t.get("fade_in_dur", 0.0))
        fade_out_d = float(t.get("fade_out_dur", 0.0))
        label_in = f"{idx + audio_index0}:a"
        label_out = f"a{idx}"
        src_dur = t.get("source_duration")
        try:
            src_dur = float(src_dur) if src_dur is not None else 0.0
        except (TypeError, ValueError):
            src_dur = 0.0
        loop = bool(t.get("loop", False))
        need_loop = bool(loop and duration is not None and src_dur > 0.05
                         and duration > (src_dur - trim_in) + 0.08)

        chain = f"[{label_in}]"
        if need_loop:
            if trim_in > 0:
                chain += f"atrim=start={trim_in},"
            chain += f"asetpts=N/SR/TB,aresample={target_sr}"
            one_loop = max(0.05, src_dur - max(0.0, trim_in))
            samples = max(1, int(one_loop * target_sr))
            chain += f",aloop=loop=-1:size={samples}:start=0,atrim=duration={duration},asetpts=N/SR/TB"
        else:
            if trim_in > 0 or duration is not None:
                if duration is not None:
                    chain += f"atrim=start={trim_in}:duration={duration},"
                else:
                    chain += f"atrim=start={trim_in},"
            chain += f"asetpts=N/SR/TB,aresample={target_sr}"
        if pitch != 0:
            rate = pow(2.0, pitch / 12.0)
            chain += f",asetrate={target_sr}*{rate},atempo={1.0/rate},aresample={target_sr}"
        if gain_db != 0:
            chain += f",volume={gain_db}dB"
        if fade_in_d > 0:
            chain += f",afade=t=in:st=0:d={fade_in_d}"
        if fade_out_d > 0 and duration is not None:
            fade_out_start = max(0.0, duration - fade_out_d)
            chain += f",afade=t=out:st={fade_out_start}:d={fade_out_d}"

        spat_on, spat_preset, spat_wet, spat_width = False, "cathedral", 0.0, 0.0
        spat_on, spat_preset, spat_wet, spat_width = resolve_fn(t, fx_settings)
        hall = ""
        mic = S.mic_amount(t, fx_settings)
        if spat_on and spat_wet > 0:
            hall = S.build_hall_reverb_filter(
                spat_preset, spat_wet, spat_width,
                uid=idx, src_path=t.get("path"),
                mic=mic)
            if hall:
                chain += f",{hall}"
            else:
                chain += f",extrastereo=m={min(1.0, max(0.0, spat_wet))}"
        elif bool(t.get("spatial_enabled", False)):
            spatial_amount = min(1.0, max(0.0, float(t.get("spatial_amount", 0.0))))
            if spatial_amount > 0:
                chain += f",extrastereo=m={spatial_amount}"
        if start_sec > 0:
            delay_ms = int(start_sec * 1000)
            chain += f",adelay={delay_ms}|{delay_ms}"
        chain += f"[{label_out}]"
        filter_parts.append(chain)
        rows_out.append({
            "idx": idx,
            "path": t.get("path"),
            "is_main": t.get("is_main"),
            "audio_role": t.get("audio_role"),
            "spatial_enabled": t.get("spatial_enabled"),
            "spatial_amount": t.get("spatial_amount"),
            "spatial_preset": t.get("spatial_preset"),
            "spatial_user_set": t.get("spatial_user_set", "(absent)"),
            "resolve": (spat_on, spat_preset, spat_wet, spat_width),
            "mic": mic,
            "hall_head": (hall[:180] + "…") if hall and len(hall) > 180 else hall,
        })

    _trim_tail = audio_mix_trim_tail(_audio_trim_sec, _audio_trim_end_sec)
    if len(tracks) == 1:
        filter_complex = f"{filter_parts[0]};[a0]anull{_trim_tail}[aout]"
    else:
        mix_inputs = "".join(f"[a{i}]" for i in range(len(tracks)))
        filter_complex = (
            ";".join(filter_parts)
            + f";{mix_inputs}amix=inputs={len(tracks)}:duration=longest:dropout_transition=0:normalize=0"
            + f",alimiter=limit=0.97:level=false{_trim_tail}[aout]"
        )
    return rows_out, filter_complex, fx_settings


def _clone_task(task):
    return copy.deepcopy(task)


def main():
    import golden_check as gc
    from cutora.audio.spatial import resolve_spatial_for_track
    from cutora.audio.sfx_library import prepare_task_audio_for_export, load_fx_settings
    from cutora.render.render_worker import RenderWorker

    case = "g08_fx_static"
    case_dir = os.path.join(gc.GOLDEN_ROOT, case)
    host, _app = gc.boot_host()
    _data, tasks = gc.load_tasks(host, os.path.join(
        case_dir, "project.asproj.json"))
    task0 = tasks[0]
    worker = RenderWorker(task0, os.path.join(tempfile.gettempdir(), "q30_dummy.mp4"),
                          "quality", False, export_logs=False)
    worker.export_audio_rate = 48000

    lines = []
    def w(s=""):
        lines.append(s)
        print(s)

    w("=== global fx (golden fixture) ===")
    fx = load_fx_settings()
    w("  %s" % {k: fx.get(k) for k in (
        "spatial_enabled", "spatial_preset", "spatial_wet", "spatial_width",
        "mic_texture", "mic_amount")})

    w()
    w("=== clip spatial as loaded (before prepare_task_audio_for_export) ===")
    tl = task0.get("timeline")
    for tr in getattr(tl, "tracks", []) or []:
        if getattr(tr, "type", None) != "audio":
            continue
        for ci, c in enumerate(getattr(tr, "clips", []) or []):
            w("  clip%d role=%s is_main=%s enabled=%s preset=%r amount=%s user_set=%s" % (
                ci, getattr(c, "audio_role", ""), getattr(c, "is_main", False),
                getattr(c, "spatial_enabled", None),
                getattr(c, "spatial_preset", None),
                getattr(c, "spatial_amount", None),
                getattr(c, "spatial_user_set", None)))

    # --- A: ac520ed / current HEAD (prepare defaults + old resolve) ---
    t_old = _clone_task(task0)
    prepare_task_audio_for_export(t_old)
    w()
    w("=== A  ac520ed/HEAD after prepare_task_audio_for_export ===")
    tl = t_old.get("timeline")
    for tr in getattr(tl, "tracks", []) or []:
        if getattr(tr, "type", None) != "audio":
            continue
        for ci, c in enumerate(getattr(tr, "clips", []) or []):
            w("  clip%d enabled=%s preset=%r amount=%s user_set=%s" % (
                ci, getattr(c, "spatial_enabled", None),
                getattr(c, "spatial_preset", None),
                getattr(c, "spatial_amount", None),
                getattr(c, "spatial_user_set", None)))
    rows_a, fc_a, _ = _build_filter(worker, t_old, resolve_spatial_for_track)
    w("  rows:")
    for r in rows_a:
        w("    %s" % json.dumps(r, ensure_ascii=False, default=str))

    # --- B: aa3613a as committed (no prepare-defaults, clip.spatial_enabled as stored) ---
    t_b = _clone_task(task0)
    # aa3613a removed apply_spatial_defaults_to_task from prepare
    rows_b, fc_b, _ = _build_filter(worker, t_b, _aa3613a_resolve)
    w()
    w("=== B  aa3613a as committed (no export rewrite; enabled=stored False) ===")
    for r in rows_b:
        w("    %s" % json.dumps(r, ensure_ascii=False, default=str))

    # --- C: aa3613a + Claude migration (enabled=True, preset still cathedral on the row) ---
    t_c = _clone_task(task0)
    tl = t_c.get("timeline")
    for tr in getattr(tl, "tracks", []) or []:
        if getattr(tr, "type", None) != "audio":
            continue
        for c in getattr(tr, "clips", []) or []:
            if getattr(c, "is_main", False) or getattr(c, "audio_role", "") == "main":
                c.spatial_enabled = True
    rows_c, fc_c, _ = _build_filter(worker, t_c, _aa3613a_resolve)
    w()
    w("=== C  aa3613a + enabled=True migration (preset still row's cathedral) ===")
    for r in rows_c:
        w("    %s" % json.dumps(r, ensure_ascii=False, default=str))

    def dump_fc(tag, fc):
        w()
        w("=== filter_complex %s (%d chars) ===" % (tag, len(fc)))
        w(fc)

    dump_fc("A ac520ed/HEAD", fc_a)
    dump_fc("B aa3613a raw", fc_b)
    dump_fc("C aa3613a+migrate enabled", fc_c)

    def char_diff(name, a, b):
        w()
        w("=== char diff %s ===" % name)
        if a == b:
            w("  IDENTICAL")
            return
        sm = difflib.SequenceMatcher(a=a, b=b)
        n = 0
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag == "equal":
                continue
            n += 1
            w("  %s A[%d:%d]=%r" % (tag, i1, i2, a[i1:i2][:240]))
            w("       B[%d:%d]=%r" % (j1, j2, b[j1:j2][:240]))
        w("  hunks=%d" % n)

    char_diff("A (ac520ed) vs B (aa3613a raw)", fc_a, fc_b)
    char_diff("A (ac520ed) vs C (aa3613a+enabled=True)", fc_a, fc_c)

    w()
    w("=== explanation ===")
    w("Golden clip stores spatial_enabled=false spatial_preset='cathedral'")
    w("spatial_amount=0.0 spatial_user_set=false — these are setdefault placeholders,")
    w("not a user choice. ac520ed export does two things before the filter is built:")
    w("  1. prepare_task_audio_for_export → apply_default_spatial_to_clip writes")
    w("     enabled=True preset=stage amount=0.1 (from fixture) onto the clip")
    w("  2. resolve_spatial_for_track with user_set=False IGNORES clip preset/amount")
    w("     and uses global fx: stage / wet=0.1 / width=0.6")
    w("aa3613a deleted (1) and made resolve read clip.spatial_preset, which")
    w("_build_audio_tracks pins to 'cathedral' via `or 'cathedral'`.")
    w("stage PRESETS have makeup_db=1.5 + presence EQ; cathedral has makeup_db=0")
    w("and pre=(). That is the uniform −0.58 dB RMS / −1.36 dB peak: the stage")
    w("makeup and 3.2k/9k boosts went missing, not the reverb on/off bit.")
    w("Probing the CLIP (preset='') made resolve look like (True,'stage',0.1,0.6);")
    w("the ROW that actually builds the graph has preset='cathedral'.")

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    print("\nwrote", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
