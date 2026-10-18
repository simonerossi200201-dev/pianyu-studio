#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Generate golden baselines using the UNMODIFIED AutoSub_v6 program.

Run with the old venv:
    D:\\0_Dev\\AutoSub_v6\\core\\venv_autosub\\Scripts\\python.exe tools\\golden_gen.py
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
import wave

OLD_CORE = r"D:\0_Dev\AutoSub_v6\core"
OLD_PY = os.path.join(OLD_CORE, "venv_autosub", "Scripts", "python.exe")
OLD_FFMPEG = os.path.join(OLD_CORE, "ffmpeg.exe")
REPO = r"D:\0_Dev\CUTORA"
GOLDEN = os.path.join(REPO, "tests", "golden")
MEDIA = os.path.join(GOLDEN, "_media")
CUTORA_SFX = os.path.join(REPO, "resources", "audio", "sfx")
FIXTURE_USER_DATA = os.path.join(GOLDEN, "_fixture_user_data")
CUTORA_USER_DATA_ENV = "CUTORA_USER_DATA"

EN_TEXT = (
    "Hello friends. Welcome to the studio. Today we will learn how to edit "
    "by meaning. Peace be with you. Let us begin this journey together."
)
IT_TEXT = (
    "Buongiorno amici. Benvenuti nello studio. Oggi impariamo a montare "
    "per significato. La pace sia con voi. Iniziamo insieme questo viaggio."
)
STITCH_GROUPS = [
    ("01", ["Peace be with you.", "And also with you."],
     "Peace be with you and also with you"),
    ("02", ["Let us pray together.", "For those in need."],
     "Let us pray together for those in need"),
    ("03", ["The word of the Lord.", "Thanks be to God."],
     "The word of the Lord thanks be to God"),
]


def log(msg: str) -> None:
    print(msg, flush=True)


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def write_sha256(case_dir: str, files: list) -> None:
    lines = []
    for path in files:
        lines.append("%s  %s" % (sha256_file(path), os.path.basename(path)))
    with open(os.path.join(case_dir, "expected.sha256"), "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")


def write_json(path: str, obj) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")


def pin_golden_user_data(env=None):
    """Point CUTORA_USER_DATA at the versioned fixture; merge into ``env``."""
    target = os.path.abspath(FIXTURE_USER_DATA)
    os.environ[CUTORA_USER_DATA_ENV] = target
    if env is not None:
        env[CUTORA_USER_DATA_ENV] = target
    return target


def run_cmd(cmd, cwd=None, env=None, timeout=600):
    log("  $ " + " ".join(cmd))
    r = subprocess.run(cmd, cwd=cwd, env=env, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError("command failed (%s): %s" % (r.returncode, cmd))
    return r


def wav_duration(path: str) -> float:
    with wave.open(path, "rb") as w:
        return w.getnframes() / float(w.getframerate() or 1)


def sapi_voices() -> list:
    ps = (
        "Add-Type -AssemblyName System.Speech; "
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        "$s.GetInstalledVoices() | ForEach-Object { "
        "$_.VoiceInfo.Name + '|' + $_.VoiceInfo.Culture.Name }"
    )
    r = subprocess.run(
        ["powershell", "-NoProfile", "-Command", ps],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    voices = []
    for line in (r.stdout or "").splitlines():
        line = line.strip()
        if "|" in line:
            name, cult = line.split("|", 1)
            voices.append((name.strip(), cult.strip()))
    return voices


def sapi_speak(text: str, wav_path: str, culture: str = "en") -> None:
    os.makedirs(os.path.dirname(wav_path) or ".", exist_ok=True)
    voices = sapi_voices()
    chosen = ""
    culture = culture.lower()
    for name, cult in voices:
        if cult.lower().startswith(culture):
            chosen = name
            break
    if not chosen:
        for name, cult in voices:
            if cult.lower().startswith("en"):
                chosen = name
                break
    if not chosen and voices:
        chosen = voices[0][0]
    log("  [sapi] requested=%s voice=%s" % (culture, chosen or "(default)"))
    tmp = wav_path + ".ps1"
    # Escape for PowerShell single-quoted string
    t = text.replace("'", "''")
    v = chosen.replace("'", "''")
    script = (
        "Add-Type -AssemblyName System.Speech\n"
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer\n"
        "$s.Rate = -1\n"
    )
    if chosen:
        script += "$s.SelectVoice('%s')\n" % v
    script += (
        "$s.SetOutputToWaveFile('%s')\n"
        "$s.Speak('%s')\n"
        "$s.Dispose()\n"
    ) % (wav_path.replace("'", "''"), t)
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(script)
    try:
        run_cmd(["powershell", "-NoProfile", "-File", tmp])
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
    if not os.path.isfile(wav_path):
        raise RuntimeError("SAPI did not write %s" % wav_path)


def make_video(wav_path: str, out_mp4: str, w: int, h: int, seconds: int) -> None:
    os.makedirs(os.path.dirname(out_mp4) or ".", exist_ok=True)
    dur = max(seconds, 1)
    src = (
        "testsrc2=size=%dx%d:rate=30:duration=%s" % (w, h, dur)
    )
    sine = "sine=frequency=220:sample_rate=44100:duration=%s" % dur
    vf = "format=yuv420p"
    cmd = [
        OLD_FFMPEG, "-y",
        "-f", "lavfi", "-i", src,
        "-i", wav_path,
        "-f", "lavfi", "-i", sine,
        "-filter_complex",
        "[1:a]apad=whole_dur=%s[a1];[2:a]volume=0.04[a2];"
        "[a1][a2]amix=inputs=2:duration=first:dropout_transition=0[a]" % dur,
        "-map", "0:v", "-map", "[a]",
        "-t", str(dur),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        out_mp4,
    ]
    run_cmd(cmd, timeout=120)
    if not os.path.isfile(out_mp4):
        raise RuntimeError("ffmpeg did not write " + out_mp4)


def make_png(path: str, w: int = 1080, h: int = 1920, color: str = "0x1A1A2E") -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    run_cmd([
        OLD_FFMPEG, "-y", "-f", "lavfi", "-i",
        "color=c=%s:s=%dx%d:d=0.04" % (color, w, h),
        "-frames:v", "1", path,
    ], timeout=30)


def prepare_old_runtime():
    if OLD_CORE not in sys.path:
        sys.path.insert(0, OLD_CORE)
    os.chdir(OLD_CORE)
    path = os.environ.get("PATH") or ""
    if os.path.normcase(OLD_CORE) not in [os.path.normcase(p) for p in path.split(os.pathsep)]:
        os.environ["PATH"] = OLD_CORE + os.pathsep + path
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"


def boot_old():
    prepare_old_runtime()
    from cutora.ui import main_window as host
    # Load CUDA/ASR before QApplication. Concurrent Qt+CUDA init has
    # ACCESS_VIOLATIONed on this machine (0xC0000005).
    log("[boot] loading ASR model before Qt")
    host.get_whisperx_model()
    from PySide6.QtWidgets import QApplication
    from cutora.batch import cli as batch_cli
    app = QApplication.instance() or QApplication([])
    batch_cli.ensure_qapp()
    return host, autosub_cli, app


def set_lang(host, lang: str) -> None:
    from cutora.core import config as cfg
    cfg.LANGUAGE = lang
    host.LANGUAGE = lang
    host._WHISPERX_MODEL = None
    host._WHISPERX_DEVICE = None


def align_task(host, task):
    from cutora.batch import cli as batch_cli
    segs = batch_cli._align_one(host, task)
    if segs is None:
        raise RuntimeError("align returned None")
    task["segments"] = segs
    return segs


def save_project(host, path, tasks):
    from cutora.batch import cli as batch_cli
    batch_cli.write_project_file(host, path, tasks, curr_idx=0)


def render_to(host, tasks, dest_files, mode="quality", use_gpu=False, log_path=None):
    logs = []
    for task, dest in zip(tasks, dest_files):
        os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
        err = []
        worker = host.RenderWorker(task, dest, mode, bool(use_gpu), export_logs=False)
        worker.error.connect(lambda m: err.append(m))
        t0 = time.time()
        worker.run()
        dt = time.time() - t0
        line = "render %s -> %s (%.1fs) err=%s" % (
            os.path.basename(task.get("video") or ""), dest, dt, err)
        log("  " + line)
        logs.append(line)
        if err or not os.path.isfile(dest):
            raise RuntimeError("render failed: %s" % (err or dest))
    if log_path:
        with open(log_path, "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(logs) + "\n")


def case_dir(name: str) -> str:
    d = os.path.join(GOLDEN, name)
    os.makedirs(d, exist_ok=True)
    return d


def apply_g02_fx(task) -> None:
    style = dict(task.get("style") or {})
    style["color_text"] = "#BBBBBB"
    style["color_high"] = "#F5B700"
    style["dynamic_box"] = True
    style["color_high_bg"] = "#FF3B30"
    style["dyn_box_glow_radius"] = 14
    style["dyn_box_anim"] = "elastic"
    style["holy_glow_radius"] = 10
    style["holy_glow_color"] = "#F5B700"
    style["anim_in_type"] = "fade"
    style["anim_in_duration"] = 0.25
    style["char_bounce_height"] = 18
    style["keyword_rules"] = [{
        "word": "peace",
        "color1": "#FF4D4D",
        "color2": "#FF4D4D",
        "use_grad": False,
        "bold": True,
        "use_stroke": True,
        "stroke_c": "#000000",
        "stroke_w": 2,
        "use_font": False,
        "font_f": "Arimo",
        "font_s": 80,
    }]
    task["style"] = style
    w, h = task.get("dims") or (1080, 1920)
    if isinstance(w, list):
        w, h = w[0], w[1]
    task.setdefault("text_overlays", [])
    task["text_overlays"].append({
        "type": "text",
        "content": "CUTORA GOLDEN G02",
        "start": 0.5,
        "end": min(8.0, float(task.get("duration") or 20)),
        "x": 80,
        "y": 120,
        "w": 920,
        "h": 140,
        "font_family": "Arimo",
        "fontsize": 42,
        "bold": True,
        "italic": False,
        "color": "#F5B700",
        "use_stroke": True,
        "stroke_color": "#000000",
        "stroke_width": 3,
        "bg_enabled": True,
        "bg_color": "#0E1116",
        "bg_opacity": 180,
        "bg_padding": 12,
        "bg_radius": 12,
        "rotation": 0.0,
        "opacity": 255,
        "blend_mode": "normal",
    })
    task.setdefault("image_overlays", [])
    task["image_overlays"].append({
        "type": "image",
        "content": "",
        "start": 1.0,
        "end": min(6.0, float(task.get("duration") or 20)),
        "x": 80,
        "y": 1500,
        "w": 920,
        "h": 220,
        "scale": 1.0,
        "opacity": 220,
        "keep_aspect": False,
        "elements": [{
            "id": uuid.uuid4().hex,
            "kind": "shape",
            "shape": "round_rect",
            "x": 80,
            "y": 1500,
            "w": 920,
            "h": 220,
            "fill_color": "#5CE1E6",
            "opacity": 140,
            "border_enabled": False,
            "rotation": 0,
            "z": 1,
        }],
    })
    tl = task.get("timeline")
    if tl is None:
        return
    for tr in getattr(tl, "tracks", []) or []:
        if getattr(tr, "is_main_video", False) or getattr(tr, "type", None) == "video":
            from cutora.model.timeline_model import TransitionItem
            tr.transitions = list(getattr(tr, "transitions", []) or [])
            tr.transitions.append(TransitionItem(0.0, 0.6, "fade_in"))
            break


def apply_g03_audio(task) -> None:
    from cutora.model.timeline_model import MediaSource, Clip
    from cutora.audio import sfx_library as sfx_library

    sfx_library.apply_spatial_defaults_to_task(task, {
        "spatial_enabled": True,
        "spatial_preset": "stage",
        "spatial_wet": 0.26,
        "spatial_width": 0.62,
    })
    tl = task.get("timeline")
    if tl is None:
        return
    track = sfx_library.get_or_create_sfx_track(tl)
    wav = os.path.join(CUTORA_SFX, "venue_ambience.wav")
    if not os.path.isfile(wav):
        wav = os.path.join(OLD_CORE, "assets", "sfx", "venue_ambience.wav")
    src = MediaSource(wav)
    src.has_video = False
    src.has_audio = True
    tl.sources[src.id] = src
    dur = min(5.0, float(task.get("duration") or 5.0))
    clip = Clip(src.id, 0.0, dur, 2.0)
    clip.is_main = False
    clip.audio_role = "sfx"
    clip.gain_db = -8.0
    clip.fade_in_dur = 0.2
    clip.fade_out_dur = 0.4
    clip.spatial_enabled = False
    track.clips.append(clip)
    for tr in getattr(tl, "tracks", []) or []:
        if getattr(tr, "type", None) != "audio":
            continue
        for c in getattr(tr, "clips", []) or []:
            if sfx_library.is_main_clip(c):
                c.spatial_enabled = True
                c.spatial_preset = "stage"
                c.spatial_amount = 0.26


def build_media():
    log("[media] synthesizing fixtures")
    os.makedirs(MEDIA, exist_ok=True)
    en_wav = os.path.join(MEDIA, "_en.wav")
    it_wav = os.path.join(MEDIA, "_it.wav")
    sapi_speak(EN_TEXT, en_wav, "en")
    sapi_speak(IT_TEXT, it_wav, "it")

    g01 = os.path.join(MEDIA, "g01", "video.mp4")
    g02 = os.path.join(MEDIA, "g02", "video.mp4")
    g03 = os.path.join(MEDIA, "g03", "video.mp4")
    g07 = os.path.join(MEDIA, "g07", "video.mp4")
    os.makedirs(os.path.dirname(g01), exist_ok=True)
    os.makedirs(os.path.dirname(g02), exist_ok=True)
    os.makedirs(os.path.dirname(g03), exist_ok=True)
    os.makedirs(os.path.dirname(g07), exist_ok=True)
    make_video(en_wav, g01, 1080, 1920, 20)
    shutil.copy2(g01, g02)
    make_video(it_wav, g03, 1920, 1080, 30)
    shutil.copy2(g01, g07)

    stitch_root = os.path.join(MEDIA, "g04_stitch")
    captions = []
    for gid, phrases, full in STITCH_GROUPS:
        captions.append("%s|%s" % (gid, full))
        for i, phrase in enumerate(phrases):
            wav = os.path.join(stitch_root, "_%s_%d.wav" % (gid, i))
            sapi_speak(phrase, wav, "en")
            name = "%s.mp4" % gid if i == 0 else "%s (%d).mp4" % (gid, i + 1)
            make_video(wav, os.path.join(stitch_root, name), 1080, 1920, 4)
    cap_path = os.path.join(stitch_root, "captions.txt")
    with open(cap_path, "w", encoding="utf-8", newline="\n") as f:
        # parse_scripts splits on blank lines; each "NN|text" must be its own para
        f.write("\n\n".join(captions) + "\n")

    g05_host = os.path.join(MEDIA, "g05", "host.mp4")
    g05_broll = os.path.join(MEDIA, "g05", "broll", "clip.mp4")
    g05_img = os.path.join(MEDIA, "g05", "card.png")
    os.makedirs(os.path.dirname(g05_broll), exist_ok=True)
    shutil.copy2(g01, g05_host)
    # short silent-ish broll from testsrc2
    broll_wav = os.path.join(MEDIA, "g05", "_broll.wav")
    sapi_speak("B roll overlay clip.", broll_wav, "en")
    make_video(broll_wav, g05_broll, 1080, 1920, 3)
    make_png(g05_img)

    for i, src in enumerate((g01, g02, g03), start=1):
        dst_dir = os.path.join(MEDIA, "g06", "v%d" % i)
        os.makedirs(dst_dir, exist_ok=True)
        shutil.copy2(src, os.path.join(dst_dir, "video.mp4"))
    log("[media] done")


def make_subtitled_project(host, autosub_cli, video, text, out_proj, lang="en"):
    set_lang(host, lang)
    task = batch_cli.make_task_from_video(host, video, text=text)
    align_task(host, task)
    save_project(host, out_proj, [task])
    return task


def do_g01(host, autosub_cli):
    name = "g01_vertical_basic"
    d = case_dir(name)
    video = os.path.join(MEDIA, "g01", "video.mp4")
    proj = os.path.join(d, "project.asproj.json")
    task = make_subtitled_project(host, autosub_cli, video, EN_TEXT, proj, "en")
    dest = os.path.join(d, "expected.mp4")
    render_to(host, [task], [dest], log_path=os.path.join(d, "export_log.txt"))
    write_sha256(d, [dest])
    write_json(os.path.join(d, "manifest.json"), {
        "case": name,
        "project": "project.asproj.json",
        "gpu": False,
        "workers": 1,
        "mode": "quality",
        "expected": ["expected.mp4"],
        "asr_frozen": True,
        "notes": "20s vertical EN, default style, ASR frozen in project",
    })
    return proj, task


def do_g02(host, autosub_cli):
    name = "g02_vertical_fx"
    d = case_dir(name)
    video = os.path.join(MEDIA, "g02", "video.mp4")
    proj = os.path.join(d, "project.asproj.json")
    task = make_subtitled_project(host, autosub_cli, video, EN_TEXT, proj, "en")
    apply_g02_fx(task)
    save_project(host, proj, [task])
    dest = os.path.join(d, "expected.mp4")
    render_to(host, [task], [dest], log_path=os.path.join(d, "export_log.txt"))
    write_sha256(d, [dest])
    write_json(os.path.join(d, "manifest.json"), {
        "case": name,
        "project": "project.asproj.json",
        "gpu": False,
        "workers": 1,
        "mode": "quality",
        "expected": ["expected.mp4"],
        "asr_frozen": True,
        "notes": "g01 plus word highlight, bounce box, glow, keyword, text overlay, shape, fade_in",
    })
    return proj, task


def do_g03(host, autosub_cli):
    name = "g03_horizontal_it"
    d = case_dir(name)
    video = os.path.join(MEDIA, "g03", "video.mp4")
    proj = os.path.join(d, "project.asproj.json")
    task = make_subtitled_project(host, autosub_cli, video, IT_TEXT, proj, "it")
    apply_g03_audio(task)
    save_project(host, proj, [task])
    dest = os.path.join(d, "expected.mp4")
    render_to(host, [task], [dest], log_path=os.path.join(d, "export_log.txt"))
    write_sha256(d, [dest])
    write_json(os.path.join(d, "manifest.json"), {
        "case": name,
        "project": "project.asproj.json",
        "gpu": False,
        "workers": 1,
        "mode": "quality",
        "expected": ["expected.mp4"],
        "asr_frozen": True,
        "notes": "30s horizontal IT, spatial stage + venue_ambience sfx clip frozen",
    })
    return proj, task


def old_cli(op, params, timeout=1800):
    params_path = os.path.join(GOLDEN, "_cli_params.json")
    write_json(params_path, params)
    env = os.environ.copy()
    env["PATH"] = OLD_CORE + os.pathsep + (env.get("PATH") or "")
    env["HF_HUB_OFFLINE"] = "1"
    env["TRANSFORMERS_OFFLINE"] = "1"
    pin_golden_user_data(env)
    cmd = [OLD_PY, "AutoSub_debug.py", "--do", op, "--params", "@" + params_path, "--json"]
    log("  [cli] %s" % op)
    r = subprocess.run(cmd, cwd=OLD_CORE, env=env, timeout=timeout,
                       capture_output=True)
    stdout = (r.stdout or b"").decode("utf-8", "replace")
    stderr = (r.stderr or b"").decode("utf-8", "replace")
    log(stderr[-4000:] if stderr else "(no stderr)")
    if r.returncode != 0:
        log(stdout[-2000:])
        raise RuntimeError("CLI %s failed code=%s" % (op, r.returncode))
    try:
        return json.loads(stdout)
    except Exception:
        log(stdout[-2000:])
        return {"ok": True, "raw": stdout}


def do_g04(host, autosub_cli):
    name = "g04_stitch"
    d = case_dir(name)
    folder = os.path.join(MEDIA, "g04_stitch")
    cap = os.path.join(folder, "captions.txt")
    payload = old_cli("stitch", {
        "dir": folder,
        "captions": "@" + cap,
        "language": "en",
        "out_dir": os.path.join(folder, "_stitched"),
        "write_project": True,
        "allow_missing": False,
        "allow_blocking": True,
    })
    write_json(os.path.join(d, "stitch_result.json"), payload)
    src_proj = payload.get("project") or os.path.join(folder, "_stitched", "stitched.asproj.json")
    if not src_proj or not os.path.isfile(src_proj):
        # stitch may write stitched.asproj.json next to out_dir
        cand = os.path.join(folder, "_stitched", "stitched.asproj.json")
        src_proj = cand if os.path.isfile(cand) else src_proj
    if not src_proj or not os.path.isfile(str(src_proj)):
        raise RuntimeError("stitch did not write a project: %s" % payload)
    dest_proj = os.path.join(d, "project.asproj.json")
    shutil.copy2(src_proj, dest_proj)
    set_lang(host, "en")
    data, tasks = batch_cli.load_project_file(host, dest_proj)
    for t in tasks:
        if str(t.get("text") or "").strip() and not t.get("segments"):
            align_task(host, t)
    save_project(host, dest_proj, tasks)
    if len(tasks) == 1:
        outs = [os.path.join(d, "expected.mp4")]
    else:
        outs = [os.path.join(d, "expected_%02d.mp4" % i) for i in range(len(tasks))]
    render_to(host, tasks, outs, log_path=os.path.join(d, "export_log.txt"))
    write_sha256(d, outs)
    write_json(os.path.join(d, "manifest.json"), {
        "case": name,
        "project": "project.asproj.json",
        "gpu": False,
        "workers": 1,
        "mode": "quality",
        "expected": [os.path.basename(p) for p in outs],
        "asr_frozen": True,
        "notes": "3-group intelligent stitch; captions in 01|text format; ASR frozen after stitch",
    })


def do_g05(host, autosub_cli):
    name = "g05_scatter_insert"
    d = case_dir(name)
    video = os.path.join(MEDIA, "g05", "host.mp4")
    proj = os.path.join(d, "project.asproj.json")
    task = make_subtitled_project(host, autosub_cli, video, EN_TEXT, proj, "en")
    save_project(host, proj, [task])
    old_cli("scatter_broll", {
        "project": proj,
        "clips_dir": os.path.join(MEDIA, "g05", "broll"),
        "clip_sec": 3,
        "interval_sec": 8,
        "start_sec": 4,
        "mute": True,
        "out": proj,
    })
    old_cli("insert_image", {
        "project": proj,
        "image": os.path.join(MEDIA, "g05", "card.png"),
        "start_sec": 3,
        "end_pad_sec": 1,
        "out": proj,
    })
    data, tasks = batch_cli.load_project_file(host, proj)
    dest = os.path.join(d, "expected.mp4")
    render_to(host, tasks, [dest], log_path=os.path.join(d, "export_log.txt"))
    write_sha256(d, [dest])
    write_json(os.path.join(d, "manifest.json"), {
        "case": name,
        "project": "project.asproj.json",
        "gpu": False,
        "workers": 1,
        "mode": "quality",
        "expected": ["expected.mp4"],
        "asr_frozen": True,
        "notes": "scatter_broll + insert_image on 20s host",
    })


def do_g06(host, autosub_cli):
    name = "g06_parallel"
    d = case_dir(name)
    src_projs = [
        os.path.join(GOLDEN, "g01_vertical_basic", "project.asproj.json"),
        os.path.join(GOLDEN, "g02_vertical_fx", "project.asproj.json"),
        os.path.join(GOLDEN, "g03_horizontal_it", "project.asproj.json"),
    ]
    tasks = []
    for sp in src_projs:
        _data, ts = batch_cli.load_project_file(host, sp)
        tasks.extend(ts)
    proj = os.path.join(d, "project.asproj.json")
    save_project(host, proj, tasks)
    dests = [os.path.join(d, "expected_%02d.mp4" % i) for i in range(len(tasks))]
    # serial first
    ser_dir = os.path.join(d, "_serial")
    os.makedirs(ser_dir, exist_ok=True)
    ser_outs = [os.path.join(ser_dir, "out_%02d.mp4" % i) for i in range(len(tasks))]
    render_to(host, tasks, ser_outs, log_path=os.path.join(d, "serial_log.txt"))
    # parallel via ParallelExportManager
    from PySide6.QtCore import QEventLoop
    par_dir = os.path.join(d, "_parallel")
    os.makedirs(par_dir, exist_ok=True)
    jobs = [(t, os.path.join(par_dir, "out_%02d.mp4" % i)) for i, t in enumerate(tasks)]
    mgr = host.ParallelExportManager(
        jobs, mode="quality", use_gpu=False,
        export_mode=getattr(host, "EXPORT_VIDEO_MODE", "compressed"),
        workers=3, parent=None, gpu_workers=0, enc_threads=0)
    done = []
    loop = QEventLoop()
    mgr.finished.connect(lambda idxs: (done.append(list(idxs or [])), loop.quit()))
    mgr.start()
    if not done:
        loop.exec()
    failed = done[-1] if done else list(range(len(jobs)))
    if failed:
        raise RuntimeError("g06 parallel failed: %s" % failed)
    par_ok = True
    notes = []
    for i, dest in enumerate(dests):
        shutil.copy2(jobs[i][1], dest)
        sh = sha256_file(jobs[i][1])
        ss = sha256_file(ser_outs[i])
        if sh != ss:
            par_ok = False
            notes.append("mismatch item %d par=%s ser=%s" % (i, sh, ss))
        else:
            notes.append("item %d parallel==serial %s" % (i, sh))
    write_sha256(d, dests)
    write_json(os.path.join(d, "parallel_vs_serial.txt") if False else os.path.join(d, "parallel_cmp.json"),
               {"ok": par_ok, "notes": notes})
    write_json(os.path.join(d, "manifest.json"), {
        "case": name,
        "project": "project.asproj.json",
        "gpu": False,
        "workers": 3,
        "mode": "quality",
        "expected": [os.path.basename(p) for p in dests],
        "verify_parallel_eq_serial": True,
        "asr_frozen": True,
        "notes": "g01+g02+g03 copies; parallel workers=3 vs serial",
        "parallel_eq_serial": par_ok,
    })
    with open(os.path.join(d, "export_log.txt"), "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(notes) + "\n")
    if not par_ok:
        log("[g06] WARNING parallel != serial")


def do_g07(host, autosub_cli):
    name = "g07_cli"
    d = case_dir(name)
    video = os.path.join(MEDIA, "g07", "video.mp4")
    proj = os.path.join(d, "project.asproj.json")
    task = make_subtitled_project(host, autosub_cli, video, EN_TEXT, proj, "en")
    save_project(host, proj, [task])
    payload = old_cli("batch_export", {
        "project": proj,
        "gpu": False,
        "workers": 1,
        "mode": "quality",
    })
    write_json(os.path.join(d, "cli_result.json"), payload)
    artifacts = [a for a in (payload.get("artifacts") or []) if os.path.isfile(a)]
    if not artifacts:
        raise RuntimeError("g07 CLI produced no artifacts: %s" % payload)
    dest = os.path.join(d, "expected.mp4")
    shutil.copy2(artifacts[0], dest)
    write_sha256(d, [dest])
    write_json(os.path.join(d, "manifest.json"), {
        "case": name,
        "project": "project.asproj.json",
        "gpu": False,
        "workers": 1,
        "mode": "quality",
        "expected": ["expected.mp4"],
        "asr_frozen": True,
        "notes": "autosub_cli batch_export of g01 copy",
        "cli_artifact": artifacts[0],
    })
    with open(os.path.join(d, "export_log.txt"), "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(payload, ensure_ascii=False, indent=2))


def write_readme():
    text = """# Golden baselines (T00)

These cases were generated with the **unmodified** AutoSub_v6 program
(`D:\\0_Dev\\AutoSub_v6\\core`) and frozen into this tree.

## Non-determinism / ASR

Whisper / whisperX on GPU is not bit-stable across runs. Every case therefore
**freezes ASR output inside the `.asproj.json`**. `tools/golden_check.py`
re-renders only; it must not re-run recognition.

## Layout

| case | what |
|---|---|
| g01_vertical_basic | 20s 1080x1920 EN + default style |
| g02_vertical_fx | same + highlight / bounce / glow / keyword / overlays / transition |
| g03_horizontal_it | 30s 1920x1080 IT + spatial(stage) + sfx clip |
| g04_stitch | 3 groups, `01\\|text` captions |
| g05_scatter_insert | scatter_broll + insert_image |
| g06_parallel | three tasks, parallel workers=3, compared to serial |
| g07_cli | `AutoSub_debug.py --do batch_export` on a g01 copy |

Media lives in `_media/` (gitignored). `expected.mp4` is also gitignored;
`expected.sha256` and the project JSON are tracked.

Export parameters for every case: `gpu=false`, `mode=quality` (libx264 CRF 18).

## USER_DATA fixture

Golden may only depend on files inside this repository. `tools/golden_check.py`
and `tools/golden_gen.py` pin `CUTORA_USER_DATA` at
`_fixture_user_data/` (copied to a temp dir for the check run) so renders never
read the machine `user_data/app_settings.json`. Any code path that loads
`user_data/` during a golden run must honour that override (including
`--render-job` children, g06 parallel, and g07 CLI).
"""
    with open(os.path.join(GOLDEN, "README.md"), "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def case_complete(name: str) -> bool:
    d = os.path.join(GOLDEN, name)
    return os.path.isfile(os.path.join(d, "expected.mp4")) or os.path.isfile(
        os.path.join(d, "expected_00.mp4"))


def main():
    t0 = time.time()
    pin_golden_user_data()
    os.makedirs(GOLDEN, exist_ok=True)
    write_readme()
    if not os.path.isfile(os.path.join(MEDIA, "g01", "video.mp4")):
        build_media()
    else:
        cap = os.path.join(MEDIA, "g04_stitch", "captions.txt")
        captions = ["%s|%s" % (gid, full) for gid, _ph, full in STITCH_GROUPS]
        with open(cap, "w", encoding="utf-8", newline="\n") as f:
            f.write("\n\n".join(captions) + "\n")
        log("[media] reused existing fixtures; rewrote stitch captions")
    host, autosub_cli, _app = boot_old()
    steps = [
        ("g01_vertical_basic", do_g01),
        ("g02_vertical_fx", do_g02),
        ("g03_horizontal_it", do_g03),
        ("g04_stitch", do_g04),
        ("g05_scatter_insert", do_g05),
        ("g06_parallel", do_g06),
        ("g07_cli", do_g07),
    ]
    for name, fn in steps:
        if case_complete(name):
            log("[skip] %s already has expected output" % name)
            continue
        log("[run] %s" % name)
        fn(host, autosub_cli)
    log("[golden_gen] finished in %.1fs" % (time.time() - t0))


if __name__ == "__main__":
    main()
