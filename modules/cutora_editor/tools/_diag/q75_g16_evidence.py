# -*- coding: utf-8 -*-
"""Q75 evidence: g16 parallel outputs vs g11/g12/g13 serial baselines.

Does not rewrite any golden file. Prints EVIDENCE_PASS or EVIDENCE_FAIL
and writes docs/plan/reports/Q75_g16/evidence.json.

usage: runtime\\venv\\Scripts\\python.exe tools\\_diag\\q75_g16_evidence.py
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "tools"))
os.chdir(REPO)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("CUTORA_SKIP_ENCODER_PROBE", "1")
os.environ.setdefault("PYTHONUNBUFFERED", "1")

FFMPEG = os.path.join(REPO, "runtime", "ffmpeg", "ffmpeg.exe")
FFPROBE = os.path.join(REPO, "runtime", "ffmpeg", "ffprobe.exe")
OUT_DIR = os.path.join(REPO, "docs", "plan", "reports", "Q75_g16")
GOLDEN = os.path.join(REPO, "tests", "golden")

# 2026-09-19 twelve-case re-record, confirmed again by Q57.
FP_EXPECT = {
    "fonts_count": 240,
    "fonts_digest": "80429c0971f116bfb759c58db8db60baa539f3762be8dcd4da46f6b00e510e2f",
    "ffmpeg_needle": "2026-01-07-git-af6a1dd0b2",
    "cv2": "4.9.0",
    "numpy": "1.26.4",
    "pyside6": "6.10.2",
}

SERIAL = (
    ("g11_vertical_basic_fx", "expected.mp4"),
    ("g12_vertical_fx_path", "expected.mp4"),
    ("g13_horizontal_it_fx", "expected.mp4"),
)


def say(msg):
    print(msg, flush=True)


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def md5_file(path):
    h = hashlib.md5()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def byte_compare(a_path, b_path):
    """Chunked identity. Returns sizes, sha256, md5, and the first mismatch."""
    sa = os.path.getsize(a_path)
    sb = os.path.getsize(b_path)
    ha = sha256_file(a_path)
    hb = sha256_file(b_path)
    ma = md5_file(a_path)
    mb = md5_file(b_path)
    first = None
    differ = 0
    with open(a_path, "rb") as fa, open(b_path, "rb") as fb:
        off = 0
        while True:
            ca = fa.read(1 << 20)
            cb = fb.read(1 << 20)
            if not ca and not cb:
                break
            n = min(len(ca), len(cb))
            if ca[:n] != cb[:n]:
                for i in range(n):
                    if ca[i] != cb[i]:
                        differ += 1
                        if first is None:
                            first = off + i
            if len(ca) != len(cb):
                extra = abs(len(ca) - len(cb))
                differ += extra
                if first is None:
                    first = off + n
            off += n
    return {
        "a_bytes": sa,
        "b_bytes": sb,
        "a_sha256": ha,
        "b_sha256": hb,
        "a_md5": ma,
        "b_md5": mb,
        "bytes_differ": differ,
        "first_mismatch": first,
        "identical": sa == sb and differ == 0 and ha == hb,
    }


def probe(path):
    cmd = [
        FFPROBE, "-v", "error", "-show_entries",
        "stream=index,codec_type,codec_name,width,height,nb_frames,duration,sample_rate,channels",
        "-of", "json", path,
    ]
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if p.returncode != 0:
        raise RuntimeError("ffprobe %s: %s" % (path, (p.stderr or "")[-300:]))
    data = json.loads(p.stdout or "{}")
    video = audio = None
    for st in data.get("streams") or []:
        if st.get("codec_type") == "video" and video is None:
            video = st
        elif st.get("codec_type") == "audio" and audio is None:
            audio = st
    return {"video": video, "audio": audio}


def _read_exact(pipe, n):
    buf = bytearray()
    while len(buf) < n:
        chunk = pipe.read(n - len(buf))
        if not chunk:
            break
        buf += chunk
    return bytes(buf)


def frame_compare(a_path, b_path):
    import numpy as np

    pa = probe(a_path)["video"]
    pb = probe(b_path)["video"]
    if not pa or not pb:
        return {"error": "missing video stream", "identical": False}
    wa, ha = int(pa["width"]), int(pa["height"])
    wb, hb = int(pb["width"]), int(pb["height"])
    if (wa, ha) != (wb, hb):
        return {
            "identical": False,
            "a_wh": [wa, ha],
            "b_wh": [wb, hb],
            "error": "dimensions differ",
        }
    frame_n = wa * ha * 3
    cmd = [FFMPEG, "-v", "error", "-i", a_path, "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    cmd_b = [FFMPEG, "-v", "error", "-i", b_path, "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    proc_a = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    proc_b = subprocess.Popen(cmd_b, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    n = 0
    diff_frames = 0
    max_abs = 0
    pixels = 0
    first = None
    try:
        while True:
            ba = _read_exact(proc_a.stdout, frame_n)
            bb = _read_exact(proc_b.stdout, frame_n)
            if not ba and not bb:
                break
            if len(ba) != frame_n or len(bb) != frame_n:
                return {
                    "identical": False,
                    "frames": n,
                    "error": "truncated frame a=%d b=%d" % (len(ba), len(bb)),
                    "width": wa,
                    "height": ha,
                }
            aa = np.frombuffer(ba, dtype=np.uint8)
            bb_a = np.frombuffer(bb, dtype=np.uint8)
            if aa.shape != bb_a.shape or not np.array_equal(aa, bb_a):
                delta = np.abs(aa.astype(np.int16) - bb_a.astype(np.int16))
                frame_max = int(delta.max())
                frame_pix = int(np.count_nonzero(delta))
                diff_frames += 1
                pixels += frame_pix
                if frame_max > max_abs:
                    max_abs = frame_max
                if first is None:
                    first = n
            n += 1
            if n % 150 == 0:
                say("  frames %d  diff_frames %d  max_abs %d" % (n, diff_frames, max_abs))
    finally:
        proc_a.stdout.close()
        proc_b.stdout.close()
        proc_a.wait(timeout=60)
        proc_b.wait(timeout=60)
    return {
        "identical": diff_frames == 0 and n > 0,
        "frames": n,
        "width": wa,
        "height": ha,
        "diff_frames": diff_frames,
        "pixels_differ": pixels,
        "max_abs": max_abs,
        "first_diff_frame": first,
        "ffmpeg_rc": [proc_a.returncode, proc_b.returncode],
    }


def pcm_compare(a_path, b_path):
    import numpy as np

    pa = probe(a_path)["audio"]
    pb = probe(b_path)["audio"]
    if not pa or not pb:
        return {"error": "missing audio stream", "identical": False}
    meta = {
        "a_rate": pa.get("sample_rate"),
        "b_rate": pb.get("sample_rate"),
        "a_channels": pa.get("channels"),
        "b_channels": pb.get("channels"),
        "a_codec": pa.get("codec_name"),
        "b_codec": pb.get("codec_name"),
    }
    if str(pa.get("sample_rate")) != str(pb.get("sample_rate")) or int(pa.get("channels") or 0) != int(pb.get("channels") or 0):
        meta["identical"] = False
        meta["error"] = "audio format differs"
        return meta

    def decode(path):
        cmd = [
            FFMPEG, "-v", "error", "-i", path, "-map", "0:a:0",
            "-f", "s16le", "-acodec", "pcm_s16le", "-",
        ]
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if p.returncode != 0:
            raise RuntimeError("pcm %s: %s" % (path, (p.stderr or b"")[-200:]))
        return np.frombuffer(p.stdout, dtype="<i2")

    aa = decode(a_path)
    bb = decode(b_path)
    n = min(aa.size, bb.size)
    if n == 0:
        meta.update({"identical": False, "error": "empty pcm", "a_samples": int(aa.size), "b_samples": int(bb.size)})
        return meta
    if aa.size == bb.size and np.array_equal(aa, bb):
        differ = 0
        max_abs = 0
    else:
        m = min(aa.size, bb.size)
        delta = np.abs(aa[:m].astype(np.int32) - bb[:m].astype(np.int32))
        differ = int(np.count_nonzero(delta)) + abs(aa.size - bb.size)
        max_abs = int(delta.max()) if m else 0
    meta.update({
        "identical": differ == 0 and aa.size == bb.size,
        "a_samples": int(aa.size),
        "b_samples": int(bb.size),
        "samples_differ": differ,
        "max_abs": max_abs,
        "sample_rate": int(pa.get("sample_rate") or 0),
        "channels": int(pa.get("channels") or 0),
    })
    return meta


def fingerprint_report(fp):
    ffmpeg = str(fp.get("ffmpeg") or "")
    rows = {
        "fonts_count": {"got": fp.get("fonts_count"), "expect": FP_EXPECT["fonts_count"]},
        "fonts_digest": {"got": fp.get("fonts_digest"), "expect": FP_EXPECT["fonts_digest"]},
        "ffmpeg": {"got": ffmpeg, "expect_contains": FP_EXPECT["ffmpeg_needle"]},
        "cv2": {"got": fp.get("cv2"), "expect": FP_EXPECT["cv2"]},
        "numpy": {"got": fp.get("numpy"), "expect": FP_EXPECT["numpy"]},
        "pyside6": {"got": fp.get("pyside6"), "expect": FP_EXPECT["pyside6"]},
    }
    ok = (
        rows["fonts_count"]["got"] == FP_EXPECT["fonts_count"]
        and str(rows["fonts_digest"]["got"]) == FP_EXPECT["fonts_digest"]
        and FP_EXPECT["ffmpeg_needle"] in ffmpeg
        and str(rows["cv2"]["got"]) == FP_EXPECT["cv2"]
        and str(rows["numpy"]["got"]) == FP_EXPECT["numpy"]
        and str(rows["pyside6"]["got"]) == FP_EXPECT["pyside6"]
    )
    return {"ok": ok, "fields": rows, "head": fp.get("head"), "source_dirty": fp.get("source_dirty")}


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    import golden_check as gc

    say("=== fingerprint ===")
    host, _app = gc.boot_host()
    fp = gc.collect_fingerprint()
    say(gc.format_fingerprint(fp))
    fp_rep = fingerprint_report(fp)
    say("FINGERPRINT %s" % ("MATCH" if fp_rep["ok"] else "MISMATCH"))

    case_dir = os.path.join(GOLDEN, "g16_parallel_fx")
    manifest = gc.load_manifest(case_dir)
    project = os.path.join(case_dir, manifest.get("project", "project.asproj.json"))
    _data, tasks = gc.load_tasks(host, project)
    if len(tasks) != 3:
        say("EVIDENCE_FAIL task count %d" % len(tasks))
        return 2
    gc.reset_case_caches()
    say("=== render g16 parallel workers=%s ===" % manifest.get("workers"))
    outs = gc.render_parallel(
        host, tasks, os.path.join(OUT_DIR, "got"),
        manifest.get("mode") or "quality",
        bool(manifest.get("gpu", False)),
        int(manifest.get("workers") or 1),
    )
    say("rendered %d files" % len(outs))

    report = {"fingerprint": fp_rep, "outputs": [], "conclusions": {}}
    labels = ("g11_vertical_basic_fx", "g12_vertical_fx_path", "g13_horizontal_it_fx")
    for i, (got, label) in enumerate(zip(outs, labels)):
        base = os.path.join(GOLDEN, label, "expected.mp4")
        stored = os.path.join(case_dir, "expected_%02d.mp4" % i)
        say("=== byte %02d vs %s ===" % (i, label))
        fresh = byte_compare(got, base)
        on_disk = byte_compare(stored, base)
        fresh["got"] = got
        fresh["baseline"] = base
        fresh["stored_expected_vs_baseline"] = {
            "identical": on_disk["identical"],
            "stored_sha256": on_disk["a_sha256"],
            "baseline_sha256": on_disk["b_sha256"],
            "stored_bytes": on_disk["a_bytes"],
            "baseline_bytes": on_disk["b_bytes"],
            "bytes_differ": on_disk["bytes_differ"],
        }
        say("  fresh sha %s identical=%s bytes_differ=%s" % (
            fresh["a_sha256"][:16], fresh["identical"], fresh["bytes_differ"]))
        say("  stored vs baseline identical=%s" % on_disk["identical"])
        report["outputs"].append(fresh)

    say("=== frames got_02 vs g13 ===")
    frames = frame_compare(outs[2], os.path.join(GOLDEN, "g13_horizontal_it_fx", "expected.mp4"))
    say("  frames=%s diff_frames=%s max_abs=%s pixels=%s" % (
        frames.get("frames"), frames.get("diff_frames"), frames.get("max_abs"), frames.get("pixels_differ")))
    say("=== pcm got_02 vs g13 ===")
    pcm = pcm_compare(outs[2], os.path.join(GOLDEN, "g13_horizontal_it_fx", "expected.mp4"))
    say("  samples=%s/%s differ=%s max_abs=%s identical=%s" % (
        pcm.get("a_samples"), pcm.get("b_samples"), pcm.get("samples_differ"),
        pcm.get("max_abs"), pcm.get("identical")))

    c0 = bool(report["outputs"][0]["identical"])
    c1 = bool(report["outputs"][1]["identical"])
    c2_pic = bool(frames.get("identical"))
    c2_pcm = bool(pcm.get("identical"))
    report["frames"] = frames
    report["pcm"] = pcm
    report["conclusions"] = {
        "picture_got02_vs_g13_every_frame_diff_0": c2_pic,
        "pcm_got02_vs_g13_sample_identical": c2_pcm,
        "got00_byte_identical_g11": c0,
        "got01_byte_identical_g12": c1,
    }
    ok = c0 and c1 and c2_pic and c2_pcm
    path = os.path.join(OUT_DIR, "evidence.json")
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    say("WROTE %s" % path)
    say("EVIDENCE_PASS" if ok else "EVIDENCE_FAIL")
    return 0 if ok else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        import traceback
        traceback.print_exc()
        say("EVIDENCE_FAIL exception")
        raise
