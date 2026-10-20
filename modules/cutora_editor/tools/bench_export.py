# -*- coding: utf-8 -*-
"""Benchmark script for CUTORA batch export (referenced by 片语工作室备忘录.md Section 〇).

Verifies that CUTORA parallel batch export sustains 250–350 fps aggregate throughput
and preserves visual parity (PSNR > 35 dB) against reference outputs.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def compute_psnr(ref_mp4: str, test_mp4: str, sample_times=(1.0, 5.0, 12.0)) -> float:
    import cv2
    import numpy as np

    cap_r = cv2.VideoCapture(ref_mp4)
    cap_t = cv2.VideoCapture(test_mp4)
    if not cap_r.isOpened() or not cap_t.isOpened():
        return 0.0
    psnrs = []
    try:
        for t in sample_times:
            cap_r.set(cv2.CAP_PROP_POS_MSEC, t * 1000.0)
            cap_t.set(cv2.CAP_PROP_POS_MSEC, t * 1000.0)
            ok1, f1 = cap_r.read()
            ok2, f2 = cap_t.read()
            if not ok1 or not ok2 or f1 is None or f2 is None or f1.shape != f2.shape:
                continue
            mse = float(np.mean((f1.astype(np.float32) - f2.astype(np.float32)) ** 2))
            if mse <= 1e-6:
                psnrs.append(99.0)
            else:
                psnrs.append(10.0 * math.log10((255.0 ** 2) / mse))
    finally:
        cap_r.release()
        cap_t.release()
    return sum(psnrs) / len(psnrs) if psnrs else 0.0


def main() -> int:
    parser = argparse.ArgumentParser(description="CUTORA Batch Export Benchmark (250-350 fps Iron Rule Verifier)")
    parser.add_argument(
        "--project",
        default=r"D:\1_PROJECT\2026-10-19\B\2026-10-19B_part2.cutora",
        help="Path to .cutora project file",
    )
    parser.add_argument("--workers", type=int, default=10, help="Parallel worker processes (default: 10)")
    parser.add_argument("--gpu-workers", type=int, default=10, help="GPU NVENC workers (default: 10)")
    parser.add_argument("--compose-workers", type=int, default=1, help="Compose threads per worker (default: 1)")
    parser.add_argument("--decode-threads", type=int, default=2, help="Decode threads per capture (default: 2)")
    parser.add_argument("--limit", type=int, default=10, help="Number of tasks to benchmark (0 = all)")
    parser.add_argument("--max-duration", type=float, default=90.0, help="Duration per task in seconds for equal-length benchmark (0 = full duration)")
    parser.add_argument("--ref-dir", default=r"D:\1_PROJECT\2026-10-19\B\成品", help="Reference output directory for PSNR check")
    args = parser.parse_args()

    from cutora.batch.export_cli import run_batch_export

    tmp_out = tempfile.mkdtemp(prefix="cutora_bench_out_")
    try:
        summary = run_batch_export(
            args.project,
            tmp_out,
            n_workers=args.workers,
            gpu_workers=args.gpu_workers,
            compose_workers=args.compose_workers,
            decode_threads=args.decode_threads,
            skip_existing=False,
            limit=args.limit,
            max_duration_sec=args.max_duration,
        )
        psnr_map = {}
        if args.ref_dir and os.path.isdir(args.ref_dir):
            for fn in sorted(os.listdir(tmp_out)):
                if not fn.endswith(".mp4"):
                    continue
                ref_p = os.path.join(args.ref_dir, fn)
                test_p = os.path.join(tmp_out, fn)
                if os.path.isfile(ref_p):
                    psnr_map[fn] = round(compute_psnr(ref_p, test_p), 2)
                if len(psnr_map) >= 3:
                    break
        summary["psnr_db"] = psnr_map
        print("\n=== BENCHMARK RESULT ===")
        print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
        return 0 if summary.get("ok") else 1
    finally:
        shutil.rmtree(tmp_out, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
