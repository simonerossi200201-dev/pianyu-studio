# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q209: Demucs two-stem worker. The GUI does not import this module's model.

Weights live in runtime/models/demucs/. The filename comes from the pack,
so this file does not require one particular checkpoint name.
"""
from __future__ import annotations

import os

_WEIGHT_EXT = (".th", ".pt", ".pth", ".ckpt")


def weights_dir(root=None) -> str:
    if root:
        return root
    from cutora.core.config import MODELS_DIR
    return os.path.join(MODELS_DIR, "demucs")


def find_weights(root=None) -> str:
    folder = weights_dir(root)
    if not folder or not os.path.isdir(folder):
        return ""
    hits = []
    for dirpath, _dirs, files in os.walk(folder):
        for name in files:
            if name.lower().endswith(_WEIGHT_EXT):
                hits.append(os.path.join(dirpath, name))
    hits.sort()
    return hits[0] if hits else ""


def worker_argv(python, src, out_dir, weights, mode="vocals", dest="", device="") -> list:
    """Subprocess entry. The GUI process never imports the model."""
    argv = [
        str(python), "-m", "cutora.audio.stem_worker",
        "--src", str(src),
        "--out", str(out_dir),
        "--weights", str(weights),
        "--two-stems", "vocals",
        "--mode", "novocals" if str(mode) == "novocals" else "vocals",
    ]
    if dest:
        argv += ["--dest", str(dest)]
    if device:
        argv += ["--device", str(device)]
    return argv


def stem_filename(mode) -> str:
    return "no_vocals.wav" if str(mode) == "novocals" else "vocals.wav"


def find_stem_file(out_dir, mode) -> str:
    """First wav demucs wrote for this mode. Empty if the job produced nothing."""
    name = stem_filename(mode)
    if not out_dir or not os.path.isdir(out_dir):
        return ""
    hits = []
    for dirpath, _dirs, files in os.walk(out_dir):
        for fn in files:
            if fn.lower() == name:
                hits.append(os.path.join(dirpath, fn))
    hits.sort()
    return hits[0] if hits else ""


def _is_voice(clip) -> bool:
    role = str(getattr(clip, "audio_role", "") or "")
    return role not in ("bgm", "sfx")


def _source_path(task, clip) -> str:
    tl = task.get("timeline") if isinstance(task, dict) else None
    if tl is None:
        return ""
    sid = str(getattr(clip, "source_id", "") or "")
    src = (getattr(tl, "sources", None) or {}).get(sid)
    return str(getattr(src, "path", "") or "")


def build_isolate_jobs(task, mode, weights, cache_root, python, *,
                        track_id=None, clip_index=None, device="") -> list:
    """Argv list for the worker. Does not start a process or write audio_proc."""
    tl = task.get("timeline") if isinstance(task, dict) else None
    if tl is None or not weights or not python:
        return []
    tid = str(track_id) if track_id else ""
    want = None if clip_index is None else int(clip_index)
    mode_name = "novocals" if str(mode) == "novocals" else "vocals"
    jobs = []
    for tr in getattr(tl, "tracks", []) or []:
        if getattr(tr, "type", None) != "audio":
            continue
        if tid and str(getattr(tr, "id", "")) != tid:
            continue
        for i, clip in enumerate(getattr(tr, "clips", []) or []):
            if want is not None and i != want:
                continue
            if want is None and not _is_voice(clip):
                continue
            src = _source_path(task, clip)
            if not src or not os.path.isfile(src):
                continue
            cid = str(getattr(clip, "id", "") or i)
            folder = os.path.join(str(cache_root), cid)
            dest = os.path.join(folder, stem_filename(mode_name))
            jobs.append({
                "argv": worker_argv(
                    python, src, os.path.join(folder, "work"), weights,
                    mode_name, dest, device,
                ),
                "dest": dest,
                "clip": clip,
                "mode": mode_name,
                "src": src,
            })
    return jobs


def plan_isolate(clip, *, exporting=False, weights_root=None, mode="vocals") -> dict:
    """Decide whether a job may start. Does not write audio_proc.path."""
    if exporting:
        return {"ok": False, "reason": "正在导出，人声分离先等等"}
    weights = find_weights(weights_root)
    if not weights:
        return {
            "ok": False,
            "reason": "没有找到人声分离模型。把权重放到 runtime/models/demucs/ 后再试。",
        }
    if clip is not None and not os.path.isfile(str(getattr(
            getattr(clip, "source", None), "path", "") or "")):
        pass
    return {"ok": True, "weights": weights, "mode": mode, "queued": True}


def apply_sidecar(clip, mode, path) -> bool:
    """Store a finished sidecar. A missing file leaves the clip unchanged."""
    if clip is None or not path or not os.path.isfile(path):
        return False
    op = "stem_novocals" if str(mode) == "novocals" else "stem_vocals"
    clip.audio_proc = {
        "op": op,
        "path": os.path.abspath(path),
        "preset": "",
        "params": {},
    }
    return True


def main(argv=None) -> int:
    """Model import happens only in this process."""
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--src", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--weights", required=True)
    parser.add_argument("--two-stems", default="vocals")
    parser.add_argument("--mode", default="vocals")
    parser.add_argument("--dest", default="")
    parser.add_argument("--device", default="")
    args = parser.parse_args(argv)
    if not os.path.isfile(args.weights):
        from cutora.core.log import get_logger
        get_logger("cutora.audio.stem_worker").info("没有找到人声分离模型")
        return 2
    import demucs.separate  # noqa: F401
    cmd = [
        "--two-stems", args.two_stems,
        "-n", "htdemucs",
        "-o", args.out,
        "--repo", os.path.dirname(args.weights),
    ]
    if args.device:
        cmd += ["-d", args.device]
    cmd.append(args.src)
    demucs.separate.main(cmd)
    if args.dest:
        found = find_stem_file(args.out, args.mode)
        if not found:
            from cutora.core.log import get_logger
            get_logger("cutora.audio.stem_worker").info("没有分离结果")
            return 3
        os.makedirs(os.path.dirname(os.path.abspath(args.dest)) or ".", exist_ok=True)
        import shutil
        shutil.copyfile(found, args.dest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
