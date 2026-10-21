# -*- coding: utf-8 -*-
"""Headless high-throughput parallel batch export CLI for CUTORA projects.

Usage:
    python -m cutora.batch.export_cli --project <path.cutora> --out <out_dir> [--workers 10] [--gpu-workers 10] [--skip-existing]
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any, Dict, List, Optional, Tuple


def load_jobs_from_project(
    proj_path: str,
    out_dir: str,
    *,
    skip_existing: bool = True,
    limit: int = 0,
    max_duration_sec: float = 0.0,
) -> Tuple[List[Tuple[dict, str, str, float, float]], List[str]]:
    from cutora.model.project_io import _normalize_project_data, deserialize_render_task, serialize_render_task
    from cutora.model.project_media import collect_missing, rebuild_animated_missing, resolve_relative_paths
    from cutora.render.parallel import job_weight, prepare_task_audio_for_export

    proj_path = os.path.abspath(proj_path)
    with open(proj_path, "r", encoding="utf-8") as f:
        proj_data = json.load(f)

    _normalize_project_data(proj_data)
    raw_tasks = list(proj_data.get("tasks") or [])
    # CRITICAL: GUI save_project writes project-relative paths (e.g. "1021视频/v10.mp4").
    # Always resolve relative paths against proj_path before deserializing or spawning workers!
    resolve_relative_paths(raw_tasks, proj_path)
    rebuild_animated_missing(raw_tasks)
    missing_media = collect_missing(raw_tasks)
    if missing_media:
        raise FileNotFoundError(
            f"[ExportCLI] Refusing to export {proj_path}: {len(missing_media)} media file(s) missing on disk "
            f"(first 3: {missing_media[:3]})"
        )

    os.makedirs(out_dir, exist_ok=True)
    jobs: List[Tuple[dict, str, str, float, float]] = []
    skipped: List[str] = []

    if limit and limit > 0:
        raw_tasks = raw_tasks[: int(limit)]

    for raw_t in raw_tasks:
        t = deserialize_render_task(raw_t)
        vpath = str(t.get("video") or "")
        name = str(t.get("name") or "").strip()
        stem = name or os.path.splitext(os.path.basename(vpath))[0]
        if stem.endswith(".mp4"):
            stem = stem[:-4]
        out_path = os.path.join(out_dir, f"{stem}.mp4")
        if skip_existing and os.path.isfile(out_path) and os.path.getsize(out_path) > 500_000:
            skipped.append(stem)
            continue
        dur = float(t.get("duration") or 0.0)
        if max_duration_sec and max_duration_sec > 0 and dur > max_duration_sec:
            t["trim_end_sec"] = float(max_duration_sec)
            dur = float(max_duration_sec)
        t = prepare_task_audio_for_export(t)
        ser_t = serialize_render_task(t)
        w = max(1.0, float(job_weight(t) or dur))
        jobs.append((ser_t, out_path, stem, w, dur))

    return jobs, skipped


def partition_jobs(jobs: List[Tuple[dict, str, str, float, float]], n_workers: int) -> List[List[int]]:
    if not jobs:
        return []
    n = max(1, min(int(n_workers), len(jobs)))
    order = sorted(range(len(jobs)), key=lambda i: (-jobs[i][3], i))
    chunks: List[List[int]] = [[] for _ in range(n)]
    loads = [0.0] * n
    for i in order:
        k = min(range(n), key=lambda j: (loads[j], j))
        chunks[k].append(i)
        loads[k] += jobs[i][3]
    return [c for c in chunks if c]


def run_batch_export(
    proj_path: str,
    out_dir: str,
    *,
    n_workers: int = 10,
    gpu_workers: int = 10,
    compose_workers: int = 1,
    decode_threads: int = 2,
    skip_existing: bool = True,
    limit: int = 0,
    max_duration_sec: float = 0.0,
    quiet: bool = False,
) -> Dict[str, Any]:
    from cutora.core.flags import apply_render_child
    from cutora.core.paths import REPO_ROOT
    from cutora.render.compose_parallel import COMPOSE_WORKERS_ENV
    from cutora.render.render_worker import EXPORT_DECODE_THREADS_ENV

    t_start = time.perf_counter()
    jobs, skipped = load_jobs_from_project(
        proj_path,
        out_dir,
        skip_existing=skip_existing,
        limit=limit,
        max_duration_sec=max_duration_sec,
    )
    total_vids = len(jobs)
    total_dur = sum(j[4] for j in jobs)
    total_frames = int(round(total_dur * 30.0))

    if total_vids == 0:
        res = {
            "ok": True,
            "project": os.path.abspath(proj_path),
            "out_dir": os.path.abspath(out_dir),
            "exported": 0,
            "skipped": len(skipped),
            "failed": [],
            "wall_sec": 0.0,
            "aggregate_fps": 0.0,
            "peak_worker_fps_sum": 0.0,
        }
        return res

    chunks = partition_jobs(jobs, n_workers)
    actual_workers = len(chunks)
    tmpdir = tempfile.mkdtemp(prefix="cutora_export_cli_")

    if not quiet:
        print(
            f"[ExportCLI] Project={os.path.basename(proj_path)} | Videos={total_vids} (skipped={len(skipped)}) "
            f"| TotalDur={total_dur:.1f}s ({total_frames} frames) | Workers={actual_workers} (GPU={min(actual_workers, gpu_workers)})",
            flush=True,
        )

    workers = []
    try:
        for wid, indices in enumerate(chunks):
            job_path = os.path.join(tmpdir, f"w{wid}.json")
            progress_path = os.path.join(tmpdir, f"w{wid}.progress")
            result_path = os.path.join(tmpdir, f"w{wid}.result")
            log_path = os.path.join(tmpdir, f"w{wid}.log")
            use_gpu = bool(wid < gpu_workers)

            payload = {
                "items": [
                    {
                        "index": idx,
                        "task": jobs[idx][0],
                        "output_path": jobs[idx][1],
                    }
                    for idx in indices
                ],
                "mode": "quality",
                "use_gpu": use_gpu,
                "export_mode": "compressed",
                "enc_threads": 2 if use_gpu else 4,
                "progress_path": progress_path,
                "result_path": result_path,
            }
            with open(job_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False)

            env = dict(os.environ)
            apply_render_child(env)
            env["OMP_NUM_THREADS"] = "1"
            env["MKL_NUM_THREADS"] = "1"
            env["OPENBLAS_NUM_THREADS"] = "1"
            env["NUMEXPR_NUM_THREADS"] = "1"
            env[COMPOSE_WORKERS_ENV] = str(max(1, int(compose_workers)))
            env[EXPORT_DECODE_THREADS_ENV] = str(max(1, int(decode_threads)))
            if not use_gpu:
                env["CUDA_VISIBLE_DEVICES"] = ""

            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            log_f = open(log_path, "w", encoding="utf-8", errors="ignore")
            cmd = [sys.executable, "-u", "-m", "cutora", "--render-job", job_path]
            proc = subprocess.Popen(
                cmd,
                cwd=REPO_ROOT,
                env=env,
                stdout=log_f,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                creationflags=creationflags,
            )
            workers.append(
                {
                    "wid": wid,
                    "proc": proc,
                    "log_f": log_f,
                    "log_path": log_path,
                    "progress_path": progress_path,
                    "result_path": result_path,
                    "indices": indices,
                    "stems": [jobs[idx][2] for idx in indices],
                }
            )
            if wid < actual_workers - 1:
                time.sleep(0.18)

        last_log = time.perf_counter()
        peak_fps_sum = 0.0
        fps_samples: List[float] = []

        while True:
            alive = 0
            done_total = 0
            acc_total = 0.0
            fps_sum = 0.0
            for w in workers:
                rc = w["proc"].poll()
                if rc is None:
                    alive += 1
                if os.path.isfile(w["progress_path"]):
                    try:
                        with open(w["progress_path"], "r", encoding="utf-8") as pf:
                            pd = json.load(pf)
                        d = int(pd.get("done", 0))
                        c = float(pd.get("cur", 0))
                        done_total += d
                        acc_total += d + (c / 100.0 if rc is None else 0.0)
                        if rc is None:
                            fps_sum += float(pd.get("fps", 0.0))
                    except Exception:
                        pass
            if fps_sum > peak_fps_sum:
                peak_fps_sum = fps_sum
            if alive == actual_workers and fps_sum > 50.0:
                fps_samples.append(fps_sum)

            now = time.perf_counter()
            if not quiet and (now - last_log >= 5.0 or alive == 0):
                pct = min(100.0, acc_total / max(1, total_vids) * 100.0)
                elapsed = now - t_start
                print(
                    f"[ExportCLI] {done_total}/{total_vids} ({pct:.1f}%) | "
                    f"ActiveWorkers={alive}/{actual_workers} | WorkerFPS_Sum={fps_sum:.1f} fps (Peak={peak_fps_sum:.1f}) | "
                    f"Elapsed={elapsed:.1f}s",
                    flush=True,
                )
                last_log = now
            if alive == 0:
                break
            time.sleep(0.5)

        failed_stems: List[str] = []
        for w in workers:
            try:
                w["log_f"].close()
            except Exception:
                pass
            if os.path.isfile(w["result_path"]):
                try:
                    with open(w["result_path"], "r", encoding="utf-8") as rf:
                        res_d = json.load(rf)
                    for fidx in res_d.get("failed") or []:
                        failed_stems.append(jobs[int(fidx)][2])
                except Exception:
                    pass
            else:
                for idx in w["indices"]:
                    if not (os.path.isfile(jobs[idx][1]) and os.path.getsize(jobs[idx][1]) > 100_000):
                        failed_stems.append(jobs[idx][2])

        # Post-export black-screen guard: sample middle frame of each exported video
        # to guarantee video tracks actually decoded and rendered (not black background + overlays).
        import cv2
        import numpy as np

        for _ser_t, out_path, stem, _w, dur in jobs:
            if stem in failed_stems or not os.path.isfile(out_path):
                continue
            cap = cv2.VideoCapture(out_path)
            try:
                if not cap.isOpened():
                    failed_stems.append(stem)
                    continue
                n_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
                if n_frames > 4:
                    cap.set(cv2.CAP_PROP_POS_FRAMES, n_frames // 2)
                ok_f, frame = cap.read()
                if not ok_f or frame is None:
                    failed_stems.append(stem)
                    continue
                non_black_ratio = float(np.mean(np.max(frame, axis=2) > 16))
                if non_black_ratio < 0.25:
                    print(
                        f"[ExportCLI] ERROR: {stem}.mp4 failed black-screen guard "
                        f"(non_black_ratio={non_black_ratio:.3f} < 0.25)!",
                        flush=True,
                    )
                    failed_stems.append(stem)
            finally:
                cap.release()

        wall_sec = max(1e-3, time.perf_counter() - t_start)
        agg_fps = total_frames / wall_sec
        steady_fps = (sum(fps_samples) / len(fps_samples)) if fps_samples else peak_fps_sum
        summary = {
            "ok": len(failed_stems) == 0,
            "project": os.path.abspath(proj_path),
            "out_dir": os.path.abspath(out_dir),
            "exported": total_vids - len(failed_stems),
            "skipped": len(skipped),
            "failed": failed_stems,
            "total_frames": total_frames,
            "wall_sec": round(wall_sec, 2),
            "aggregate_fps": round(agg_fps, 1),
            "steady_worker_fps_sum": round(steady_fps, 1),
            "peak_worker_fps_sum": round(peak_fps_sum, 1),
        }
        return summary
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="CUTORA Headless Parallel Batch Export CLI")
    parser.add_argument("--project", required=True, help="Path to .cutora project file")
    parser.add_argument("--out", required=True, help="Output directory for exported MP4s")
    parser.add_argument("--workers", type=int, default=10, help="Number of parallel render processes (default: 10)")
    parser.add_argument("--gpu-workers", type=int, default=10, help="Number of GPU NVENC workers (default: 10)")
    parser.add_argument("--compose-workers", type=int, default=1, help="Compose threads per worker (default: 1)")
    parser.add_argument("--decode-threads", type=int, default=2, help="Decode threads per capture (default: 2)")
    parser.add_argument("--limit", type=int, default=0, help="Limit number of tasks to export (0 = all)")
    parser.add_argument("--max-duration", type=float, default=0.0, help="Optional cap on task duration in seconds")
    parser.add_argument("--skip-existing", action="store_true", default=True, help="Skip existing non-empty MP4s")
    parser.add_argument("--no-skip-existing", dest="skip_existing", action="store_false", help="Overwrite existing MP4s")
    args = parser.parse_args(argv)

    summary = run_batch_export(
        args.project,
        args.out,
        n_workers=args.workers,
        gpu_workers=args.gpu_workers,
        compose_workers=args.compose_workers,
        decode_threads=args.decode_threads,
        skip_existing=args.skip_existing,
        limit=args.limit,
        max_duration_sec=args.max_duration,
    )
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    return 0 if summary.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
