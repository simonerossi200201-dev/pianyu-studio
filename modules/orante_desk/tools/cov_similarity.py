# -*- coding: utf-8 -*-
"""扩图雷同率：给一个任务的 images 目录（或任务 id），两两算 aHash 汉明距离。

用法：
  python tools/cov_similarity.py T0014
  python tools/cov_similarity.py D:\\1_PROJECT\\...\\T0014-...\\images
  python tools/cov_similarity.py T0014 --t 16
  python tools/cov_similarity.py T0014 --json

打印：最小 / 中位 / 平均、直方图、距离 < T 的图对占比。
默认只算 images/ 根下的主体图，不进 _候选 / _弃用 / _replaced。
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from orante.services.verify import ahash_image, hamming  # noqa: E402

IMG_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
SKIP_DIR = {"_候选", "_弃用", "_replaced", "_broll", "_不合格"}


def _is_img(p: Path) -> bool:
    return p.is_file() and p.suffix.lower() in IMG_EXT


def _list_images(folder: Path) -> list[Path]:
    out = []
    if not folder.is_dir():
        return out
    for p in sorted(folder.iterdir(), key=lambda x: x.name.lower()):
        if p.is_dir():
            continue
        if _is_img(p):
            out.append(p)
    return out


def _resolve_dir(arg: str) -> Path:
    p = Path(arg)
    if p.is_dir():
        return p
    if p.is_file():
        return p.parent
    # 任务 id：从库里找
    tid = arg.strip()
    try:
        from orante import db
        db.init()
        row = db.one("SELECT dir FROM tasks WHERE id=?", (tid,))
        if row and row.get("dir"):
            d = Path(row["dir"]) / "images"
            if d.is_dir():
                return d
    except Exception:
        pass
    raise SystemExit(f"找不到目录或任务：{arg}")


def pairwise(files: list[Path]) -> list[tuple[str, str, int]]:
    hashes: list[tuple[str, int]] = []
    for fp in files:
        h = ahash_image(fp)
        if h is None:
            print(f"  skip 算不出哈希：{fp.name}", file=sys.stderr)
            continue
        hashes.append((fp.name, h))
    pairs = []
    for i in range(len(hashes)):
        for j in range(i + 1, len(hashes)):
            d = hamming(hashes[i][1], hashes[j][1])
            pairs.append((hashes[i][0], hashes[j][0], d))
    pairs.sort(key=lambda x: x[2])
    return pairs


def histogram(dists: list[int], width: int = 16, cap: int = 160) -> list[tuple[str, int]]:
    out = []
    lo = 0
    while lo < cap:
        hi = lo + width
        if hi >= cap:
            n = sum(1 for d in dists if d >= lo)
            out.append((f"{lo:02d}+", n))
            break
        n = sum(1 for d in dists if lo <= d < hi)
        out.append((f"{lo:02d}–{hi - 1:02d}", n))
        lo = hi
    return out


def summarize(pairs: list[tuple[str, str, int]], t: int) -> dict:
    dists = [d for _, _, d in pairs]
    n = len(dists)
    close = [(a, b, d) for a, b, d in pairs if d < t]
    med = statistics.median(dists) if dists else None
    mean = statistics.mean(dists) if dists else None
    return {
        "n_images_pairs": n,
        "min": min(dists) if dists else None,
        "median": med,
        "mean": round(mean, 2) if mean is not None else None,
        "max": max(dists) if dists else None,
        "t": t,
        "close_n": len(close),
        "close_ratio": (len(close) / n) if n else 0.0,
        "close_pairs": close[:30],
        "hist": histogram(dists),
        "all": pairs,
    }


def render(files: list[Path], s: dict) -> str:
    lines = []
    lines.append(f"图 {len(files)} 张，图对 {s['n_images_pairs']} 对")
    lines.append(f"距离  最小 {s['min']}  中位 {s['median']}  平均 {s['mean']}  最大 {s['max']}")
    t = s["t"]
    pct = s["close_ratio"] * 100
    lines.append(f"距离 < {t} 的图对：{s['close_n']} / {s['n_images_pairs']}  =  {pct:.1f}%")
    lines.append("直方图：")
    n = s["n_images_pairs"] or 1
    for label, c in s["hist"]:
        bar = "█" * (c * 40 // n) if c else ""
        lines.append(f"  {label}  {c:4d}  {bar}")
    if s["close_pairs"]:
        lines.append(f"最近的雷同对（<{t}）：")
        for a, b, d in s["close_pairs"][:20]:
            lines.append(f"  {d:3d}  {a}  ↔  {b}")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="扩图 aHash 雷同率")
    ap.add_argument("target", help="任务 id 或 images 目录")
    ap.add_argument("--t", type=int, default=90, help="雷同阈值（距离 < T 算一对）")
    ap.add_argument("--json", action="store_true", help="只打 JSON")
    ap.add_argument("--all", action="store_true", help="连子目录一起算（含 _replaced）")
    args = ap.parse_args(argv)

    folder = _resolve_dir(args.target)
    if args.all:
        files = sorted((p for p in folder.rglob("*") if _is_img(p)
                        and not any(part in SKIP_DIR for part in p.parts)),
                       key=lambda x: x.as_posix().lower())
    else:
        files = _list_images(folder)
    if len(files) < 2:
        print(f"图不够（{len(files)}），要至少 2 张：{folder}", file=sys.stderr)
        return 2
    pairs = pairwise(files)
    s = summarize(pairs, args.t)
    s["dir"] = str(folder)
    s["files"] = [p.name for p in files]
    if args.json:
        dump = dict(s)
        dump["all"] = [(a, b, d) for a, b, d in s["all"]]
        dump["close_pairs"] = [(a, b, d) for a, b, d in s["close_pairs"]]
        print(json.dumps(dump, ensure_ascii=False, indent=2))
    else:
        print(f"目录 {folder}")
        print(render(files, s))
    return 0


if __name__ == "__main__":
    # 工具自己跑时不要污染 ORANTE_DATA
    os.environ.setdefault("ORANTE_DATA", str(ROOT / "data"))
    raise SystemExit(main())
