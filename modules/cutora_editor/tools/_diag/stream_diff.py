# -*- coding: utf-8 -*-
"""Render one golden case and diff its video / audio streams separately.

2026-09-19: golden drifted after Q18/Q29/Q30 and two rounds of reasoning about
which field moved were both wrong. This stops the guessing: it says outright
whether the picture changed, the sound changed, or both.

usage: runtime\\venv\\Scripts\\python.exe tools\\_diag\\stream_diff.py [case]
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "tools"))
os.chdir(REPO)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

FFMPEG = os.path.join(REPO, "runtime", "ffmpeg", "ffmpeg.exe")
if not os.path.isfile(FFMPEG):
    FFMPEG = "ffmpeg"


def stream_md5(path: str, spec: str):
    """md5 of one elementary stream, container framing excluded."""
    p = subprocess.run(
        [FFMPEG, "-v", "error", "-i", path, "-map", spec, "-c", "copy",
         "-f", "md5", "-"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if p.returncode != 0:
        return "(none/%s)" % (p.stderr.strip().splitlines()[-1][:40] if p.stderr.strip() else "err")
    return p.stdout.strip().replace("MD5=", "")[:16]


def main() -> int:
    case = sys.argv[1] if len(sys.argv) > 1 else "g08_fx_static"
    import golden_check as gc

    case_dir = os.path.join(gc.GOLDEN_ROOT, case)
    manifest = gc.load_manifest(case_dir)
    exp_names = manifest.get("expected") or ["expected.mp4"]
    host, _app = gc.boot_host()
    _data, tasks = gc.load_tasks(host, os.path.join(
        case_dir, manifest.get("project", "project.asproj.json")))

    # 迁移之后 clip 上的空间参数长什么样 —— 直接打出来，不靠推断
    print("=== clip spatial after load/migration ===")
    for ti, t in enumerate(tasks):
        tl = t.get("timeline")
        for tr in getattr(tl, "tracks", []) or []:
            if getattr(tr, "type", None) != "audio":
                continue
            for ci, c in enumerate(getattr(tr, "clips", []) or []):
                print("  task%d clip%d role=%-5s is_main=%-5s enabled=%-5s preset=%-9s amount=%s user_set=%s" % (
                    ti, ci, getattr(c, "audio_role", "") or "-",
                    getattr(c, "is_main", False),
                    getattr(c, "spatial_enabled", None),
                    repr(getattr(c, "spatial_preset", None)),
                    getattr(c, "spatial_amount", None),
                    getattr(c, "spatial_user_set", "(gone)")))
    try:
        from cutora.audio.spatial import resolve_spatial_for_track
        from cutora.audio.sfx_library import load_fx_settings
        fx = load_fx_settings()
        print("  global fx: enabled=%s preset=%s wet=%s width=%s" % (
            fx.get("spatial_enabled"), fx.get("spatial_preset"),
            fx.get("spatial_wet"), fx.get("spatial_width")))
        for ti, t in enumerate(tasks):
            tl = t.get("timeline")
            for tr in getattr(tl, "tracks", []) or []:
                if getattr(tr, "type", None) != "audio":
                    continue
                for ci, c in enumerate(getattr(tr, "clips", []) or []):
                    row = {"is_main": getattr(c, "is_main", False),
                           "audio_role": getattr(c, "audio_role", ""),
                           "spatial_enabled": getattr(c, "spatial_enabled", False),
                           "spatial_amount": getattr(c, "spatial_amount", 0.0),
                           "spatial_preset": getattr(c, "spatial_preset", "")}
                    print("  resolve task%d clip%d -> %s" % (
                        ti, ci, resolve_spatial_for_track(row, fx)))
    except Exception as exc:
        print("  resolve probe failed:", exc)

    gc.reset_case_caches()
    tmp = tempfile.mkdtemp(prefix="%s_diff_" % case)
    outs = gc.render_serial(host, tasks, tmp, manifest.get("mode") or "quality",
                            bool(manifest.get("gpu", False)))
    print()
    print("=== stream md5: fresh render vs expected.mp4 ===")
    print("%-16s %-18s %-18s %s" % ("file", "video", "audio", "verdict"))
    for got, name in zip(outs, exp_names):
        exp = os.path.join(case_dir, name)
        if not os.path.isfile(exp):
            print("%-16s expected.mp4 不在盘上（.gitignore），无法比" % name)
            continue
        gv, ga = stream_md5(got, "0:v:0"), stream_md5(got, "0:a:0")
        ev, ea = stream_md5(exp, "0:v:0"), stream_md5(exp, "0:a:0")
        same_v, same_a = gv == ev, ga == ea
        verdict = ("画面变了 " if not same_v else "") + ("声音变了" if not same_a else "")
        print("%-16s %s %s  %s" % (
            name,
            ("same " + gv) if same_v else ("%s vs %s" % (gv, ev)),
            ("same " + ga) if same_a else ("%s vs %s" % (ga, ea)),
            verdict or "两条流都一样（差异在容器层）"))
        if not same_a:
            audio_report(got, exp)
    return 0


def _wav(path, dest):
    subprocess.run([FFMPEG, "-v", "error", "-y", "-i", path, "-vn",
                    "-ac", "2", "-ar", "48000", "-f", "wav", dest],
                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return dest if os.path.isfile(dest) else None


def audio_report(got, exp):
    """声音到底怎么不一样：长度 / 电平 / 左右去相关（混响的指纹） / 逐秒差。"""
    try:
        import wave

        import numpy as np
    except Exception as exc:
        print("  (numpy/wave 不可用，跳过声学分析: %s)" % exc)
        return

    def load(p, tag):
        w = _wav(p, os.path.join(tempfile.gettempdir(), "sd_%s.wav" % tag))
        if not w:
            return None
        with wave.open(w, "rb") as fh:
            n = fh.getnframes()
            raw = fh.readframes(n)
        a = np.frombuffer(raw, np.int16).astype(np.float32) / 32768.0
        return a.reshape(-1, 2) if a.size % 2 == 0 else None

    a, b = load(got, "new"), load(exp, "old")
    if a is None or b is None:
        print("  (解不出 wav，跳过)")
        return
    print()
    print("  === 声学对比（new = 这次渲染, old = expected.mp4）===")
    print("  %-10s %-12s %-12s %-12s %-12s" % ("", "样本数", "RMS", "峰值", "L/R 相关"))
    for tag, x in (("new", a), ("old", b)):
        rms = float(np.sqrt((x ** 2).mean()))
        pk = float(np.abs(x).max())
        L, R = x[:, 0], x[:, 1]
        den = float(np.sqrt((L ** 2).sum() * (R ** 2).sum())) or 1.0
        corr = float((L * R).sum() / den)
        print("  %-10s %-12d %-12.6f %-12.6f %-12.4f" % (tag, len(x), rms, pk, corr))
    n = min(len(a), len(b))
    if n:
        d = np.abs(a[:n] - b[:n]).mean(axis=1)
        print("  逐样本平均绝对差 %.6f，最大 %.6f" % (float(d.mean()), float(d.max())))
        sec = 48000
        rows = []
        for i in range(0, min(n, sec * 8), sec):
            seg = d[i:i + sec]
            if seg.size:
                rows.append("t=%ds diff=%.5f" % (i // sec, float(seg.mean())))
        print("  " + " | ".join(rows))
    print("  提示：L/R 相关越低 = 混响越强（左右去相关是卷积混响的指纹）。")
    print("        两边相关差不多但样本数或 RMS 不同 → 不是混响，是别的层。")


if __name__ == "__main__":
    raise SystemExit(main())
