# -*- coding: utf-8 -*-
"""规格 38 A/B：同一文案两条成品的连贯性 / 锐度对照。

用法：
  python tools/ab38.py A任务目录 B任务目录
  A = 逐段首帧，B = 锚点+接片。

没有两条实跑成品时只打印方法，不把新模式改成默认。
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from orante.models import Manifest  # noqa: E402
from orante.services import tails, verify, pipeline  # noqa: E402


def _clip(d: Path, m: Manifest, sg: dict) -> Path | None:
    return tails.clip_of(d, m, sg)


def _ffmpeg_frame(video: Path, dest: Path, sseof=None) -> Path | None:
    """不经过 PIL，直接让 ffmpeg 出 JPEG。本机可能没装 Pillow。"""
    try:
        exe = tails._ffmpeg()
        from orante.services.procutil import run_quiet
        cmd = [exe, "-hide_banner", "-loglevel", "error"]
        if sseof is not None:
            cmd += ["-sseof", str(float(sseof))]
        cmd += ["-i", str(video), "-frames:v", "1", "-q:v", "2", "-an", "-y", str(dest)]
        r = run_quiet(cmd, timeout=60)
        if r.returncode != 0 or not dest.is_file() or dest.stat().st_size < 100:
            return None
        return dest
    except Exception as e:
        print("  抽帧失败", video.name, e)
        return None


def _mid_frame(video: Path, dest: Path) -> Path | None:
    # -ss 在 -i 前：大约中段；失败再试文件尾附近
    try:
        info = tails.video_info(video)
        dur = float(info.get("duration") or 0)
    except Exception:
        dur = 0.0
    if dur > 1:
        try:
            exe = tails._ffmpeg()
            from orante.services.procutil import run_quiet
            cmd = [exe, "-hide_banner", "-loglevel", "error",
                   "-ss", f"{dur/2:.3f}", "-i", str(video),
                   "-frames:v", "1", "-q:v", "2", "-an", "-y", str(dest)]
            r = run_quiet(cmd, timeout=60)
            if r.returncode == 0 and dest.is_file() and dest.stat().st_size > 100:
                return dest
        except Exception:
            pass
    return _ffmpeg_frame(video, dest, sseof=-0.4)


def _cut_pair(d: Path, m: Manifest, a: dict, b: dict, work: Path):
    """上一段靠近结尾的一帧 vs 下一段首帧（倒数很大 = 靠近开头，这里用 ffmpeg 第 0 帧走 verify）。"""
    ca, cb = _clip(d, m, a), _clip(d, m, b)
    if not ca or not cb:
        return None
    tail = work / ("cut_%03d_tail.jpg" % int(a["s"]))
    head = work / ("cut_%03d_head.jpg" % int(b["s"]))
    if not _ffmpeg_frame(ca, tail, sseof=-0.2):
        return None
    if not _ffmpeg_frame(cb, head, sseof=None):
        return None
    ha = verify.ahash_image(tail)
    hb = verify.ahash_image(head)
    dist = verify.hamming(ha, hb) if ha is not None and hb is not None else None
    return {"a": int(a["s"]), "b": int(b["s"]), "dist": dist,
            "tail": str(tail), "head": str(head)}


def analyze(task_dir: Path, label: str) -> dict:
    d = Path(task_dir)
    m = Manifest.load(d)
    work = d / "clips" / "_ab38"
    work.mkdir(parents=True, exist_ok=True)
    segs = [sg for sg in m.segments if tails.clip_of(d, m, sg)]
    cuts = []
    for i in range(len(m.segments) - 1):
        pair = _cut_pair(d, m, m.segments[i], m.segments[i + 1], work)
        if pair:
            cuts.append(pair)
    # 锐度：随机 3 个接片（没有接片就随机 3 段）和相邻主片
    bridges = [sg for sg in segs if sg.get("role") == "bridge"]
    anchors = [sg for sg in segs if sg.get("role") != "bridge"]
    pick_b = bridges[:]
    random.shuffle(pick_b)
    pick_b = pick_b[:3] or segs[:3]
    sharp = []
    for sg in pick_b:
        clip = _clip(d, m, sg)
        if not clip:
            continue
        dest = work / ("mid_s%03d.jpg" % int(sg["s"]))
        if _mid_frame(clip, dest):
            try:
                sh = tails.laplacian_var(dest)
            except Exception:
                sh = None  # 没装 Pillow 就算不了
            sharp.append({"s": int(sg["s"]), "role": sg.get("role") or "",
                          "sharp": sh, "kind": "bridge" if sg.get("role") == "bridge" else "other"})
        # 相邻主片
        nxt = pipeline.next_of(m, sg) or pipeline.prev_of(m, sg)
        if nxt and _clip(d, m, nxt):
            dest2 = work / ("mid_s%03d.jpg" % int(nxt["s"]))
            if _mid_frame(_clip(d, m, nxt), dest2):
                try:
                    sh2 = tails.laplacian_var(dest2)
                except Exception:
                    sh2 = None
                sharp.append({"s": int(nxt["s"]), "role": nxt.get("role") or "",
                              "sharp": sh2, "kind": "anchor"})
    gaps = []
    if (d / "clips" / "_stitched" / "缺失文案清单.txt").is_file():
        gaps.append(str(d / "clips" / "_stitched" / "缺失文案清单.txt"))
    return {
        "label": label,
        "dir": str(d),
        "mode": pipeline.mode_of(m),
        "n_clips": len(segs),
        "n_bridge": len(bridges),
        "n_anchor": len(anchors),
        "cuts": cuts,
        "sharp": sharp,
        "gap_lists": gaps,
        "bench": bench_summary(m),
    }


def bench_summary(m: Manifest) -> dict:
    from orante.services import bench
    return bench.counts_of(m)


def fmt(rep: dict) -> str:
    lines = [
        f"## {rep['label']}  `{rep['mode']}`  {rep['dir']}",
        f"- 成片 {rep['n_clips']} 段 · 接片 {rep['n_bridge']} · 主片 {rep['n_anchor']}",
        f"- 检查台 红 {rep['bench']['bench_red']} / 黄 {rep['bench']['bench_yellow']} / 绿 {rep['bench']['bench_green']}",
    ]
    if rep["cuts"]:
        lines.append("- 切点 aHash（上一段近尾帧 vs 下一段首帧，越小越像）：")
        for c in rep["cuts"][:12]:
            lines.append(f"  - s{c['a']:03d}→s{c['b']:03d}  dist={c['dist']}")
        xs = [c["dist"] for c in rep["cuts"] if c["dist"] is not None]
        if xs:
            lines.append(f"  - 平均 {sum(xs)/len(xs):.1f}，最大 {max(xs)}")
    if rep["sharp"]:
        lines.append("- 拉普拉斯锐度（中段一帧）：")
        for s in rep["sharp"]:
            lines.append(f"  - s{s['s']:03d} {s['kind']}  {s['sharp']}")
    if rep["gap_lists"]:
        lines.append("- 缺失文案清单：" + "；".join(rep["gap_lists"]))
    else:
        lines.append("- 缺失文案清单：没有 clips/_stitched/缺失文案清单.txt")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("a", nargs="?", help="逐段首帧任务目录")
    ap.add_argument("b", nargs="?", help="锚点+接片任务目录")
    args = ap.parse_args()
    if not args.a or not args.b:
        print("用法：python tools/ab38.py <逐段首帧目录> <锚点+接片目录>")
        print("没有两条实跑成品之前，新模式不设为默认（generate.mode=first_frame）。")
        return 2
    ra = analyze(Path(args.a), "A 逐段首帧")
    rb = analyze(Path(args.b), "B 锚点+接片")
    print(fmt(ra))
    print()
    print(fmt(rb))
    out = Path(args.b) / "clips" / "_ab38" / "ab38.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"a": ra, "b": rb}, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n写了", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
