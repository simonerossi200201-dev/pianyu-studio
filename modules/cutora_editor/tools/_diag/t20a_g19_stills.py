# -*- coding: utf-8 -*-
"""Side-by-side stills: old ffmpeg concat vs new timeline export. Same cutlist."""
from __future__ import annotations

import json
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

from cutora.core.config import FFMPEG_PATH  # noqa: E402

CASE = os.path.join(REPO, "tests", "golden", "g19_stitch_timeline")
MEDIA = os.path.join(REPO, "tests", "golden", "_media", "g19_stitch_timeline")
OUT = os.path.join(REPO, "docs", "plan", "reports", "img")
WORK = os.path.join(REPO, "docs", "plan", "reports", "g19_stills")
NEW_MP4 = os.path.join(CASE, "expected.mp4")
OLD_MP4 = os.path.join(WORK, "old_concat.mp4")


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    print(" $", " ".join(cmd), flush=True)
    return subprocess.run(cmd, capture_output=True)


def encode_old_concat() -> str:
    """Match EncodeWorker._encode_group: source-res scale+pad, concat, x264."""
    os.makedirs(WORK, exist_ok=True)
    cuts = json.loads(open(os.path.join(CASE, "cutlist.json"), encoding="utf-8").read())
    items = cuts["T035"]
    inputs = []
    for it in items:
        if not it.get("keep", True):
            continue
        segs = it.get("segs") or [[it["in_t"], it["out_t"]]]
        for a, b in segs:
            inputs.append((it["path"], float(a), float(b)))
    if not inputs:
        raise SystemExit("empty cutlist")
    probe = [
        FFMPEG_PATH.replace("ffmpeg.exe", "ffprobe.exe"),
        "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height,r_frame_rate",
        "-of", "json", inputs[0][0],
    ]
    pr = subprocess.run(probe, capture_output=True, text=True)
    meta = json.loads(pr.stdout or "{}")
    st = (meta.get("streams") or [{}])[0]
    W = int(st.get("width") or 720)
    H = int(st.get("height") or 1280)
    FPS = 30.0
    cmd = [FFMPEG_PATH, "-y", "-v", "error"]
    for path, a, b in inputs:
        if a > 0.001:
            cmd += ["-ss", "%.3f" % a]
        cmd += ["-to", "%.3f" % b, "-i", path]
    n = len(inputs)
    fc, v_labels, a_labels = [], [], []
    for i in range(n):
        fc.append(
            f"[{i}:v]scale={W}:{H}:force_original_aspect_ratio=decrease,"
            f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={FPS:.5f}[v{i}]")
        v_labels.append(f"[v{i}]")
        seg_len = max(0.0, inputs[i][2] - inputs[i][1])
        fd = min(0.030, max(0.0, (seg_len - 0.10) / 2.0))
        fi = min(0.010, fd)
        af = f"[{i}:a]aresample=48000:async=1"
        if fd > 0.004:
            af += (f",asetpts=PTS-STARTPTS"
                   f",afade=t=in:st=0:d={fi:.3f}"
                   f",afade=t=out:st={max(0.0, seg_len - fd):.3f}:d={fd:.3f}")
        fc.append(af + f"[a{i}]")
        a_labels.append(f"[a{i}]")
    inter = "".join(v + a for v, a in zip(v_labels, a_labels))
    fc.append(f"{inter}concat=n={n}:v=1:a=1[outv][outa]")
    cmd += ["-filter_complex", ";".join(fc),
            "-map", "[outv]", "-map", "[outa]",
            "-c:a", "aac", "-b:a", "192k",
            "-c:v", "libx264", "-crf", "16", "-preset", "veryfast",
            "-movflags", "+faststart", OLD_MP4]
    res = _run(cmd)
    if res.returncode != 0 or not os.path.isfile(OLD_MP4):
        err = (res.stderr or b"").decode("utf-8", "replace")[-800:]
        raise SystemExit("old concat failed: %s" % err)
    print("OLD", OLD_MP4, os.path.getsize(OLD_MP4))
    return OLD_MP4


def _duration(path: str) -> float:
    probe = FFMPEG_PATH.replace("ffmpeg.exe", "ffprobe.exe")
    r = subprocess.run(
        [probe, "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", path],
        capture_output=True, text=True)
    return float((r.stdout or "0").strip() or 0)


def stills() -> list[str]:
    if not os.path.isfile(NEW_MP4):
        raise SystemExit("missing new expected.mp4 — record g19 first")
    if not os.path.isfile(OLD_MP4):
        encode_old_concat()
    os.makedirs(OUT, exist_ok=True)
    dur = min(_duration(OLD_MP4), _duration(NEW_MP4))
    stamps = {
        "start": 0.08,
        "mid": round(dur * 0.5, 3),
        "end": max(0.0, round(dur - 0.15, 3)),
    }
    written = []
    for tag, t in stamps.items():
        old_png = os.path.join(WORK, "old_%s.png" % tag)
        new_png = os.path.join(WORK, "new_%s.png" % tag)
        pair = os.path.join(OUT, "T20a_g19_pair_%s.png" % tag)
        for src, dest in ((OLD_MP4, old_png), (NEW_MP4, new_png)):
            r = _run([
                FFMPEG_PATH, "-y", "-ss", "%.3f" % t, "-i", src,
                "-frames:v", "1", dest,
            ])
            if r.returncode != 0 or not os.path.isfile(dest):
                raise SystemExit("frame fail %s t=%s" % (src, t))
        r = _run([
            FFMPEG_PATH, "-y", "-i", old_png, "-i", new_png,
            "-filter_complex",
            "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,"
            "crop=1080:1920[a];"
            "[1:v]scale=1080:1920:force_original_aspect_ratio=increase,"
            "crop=1080:1920[b];"
            "[a][b]hstack=inputs=2,"
            "drawtext=text='old concat':x=24:y=24:fontsize=36:fontcolor=white:"
            "box=1:boxcolor=black@0.6,"
            "drawtext=text='new timeline':x=1104:y=24:fontsize=36:"
            "fontcolor=white:box=1:boxcolor=black@0.6",
            pair,
        ])
        if r.returncode != 0:
            # drawtext needs a font; fall back to unlabeled hstack
            r = _run([
                FFMPEG_PATH, "-y", "-i", old_png, "-i", new_png,
                "-filter_complex",
                "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,"
                "crop=1080:1920[a];"
                "[1:v]scale=1080:1920:force_original_aspect_ratio=increase,"
                "crop=1080:1920[b];[a][b]hstack=inputs=2",
                pair,
            ])
        if r.returncode != 0 or not os.path.isfile(pair):
            err = (r.stderr or b"").decode("utf-8", "replace")[-600:]
            raise SystemExit("hstack fail %s: %s" % (tag, err))
        written.append(pair)
        print("STILL", tag, "t=%.3f" % t, pair)
    note = os.path.join(OUT, "T20a_g19_pair_notes.md")
    with open(note, "w", encoding="utf-8") as fh:
        fh.write("# T20a g19 并排静帧（开头 / 中段 / 结尾）\n\n")
        fh.write("左 = 旧 EncodeWorker ffmpeg concat（源分辨率 720×1280）。\n")
        fh.write("右 = 新时间线渲出（画布 1080×1920 fill）。\n\n")
        fh.write("同一份 `cutlist.json`。不要求像素相等；要求切点位置和画面内容一致。\n\n")
        for tag, t in stamps.items():
            fh.write("- %s t=%.3fs → `T20a_g19_pair_%s.png`\n" % (tag, t, tag))
    written.append(note)
    return written


def main() -> int:
    if "--old-only" in sys.argv:
        encode_old_concat()
        return 0
    stills()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
