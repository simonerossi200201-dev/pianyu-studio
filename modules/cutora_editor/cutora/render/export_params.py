# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Pure mappings from export command params to ffmpeg argv / output paths.

T08a: every ``export.*`` parameter must land here. Silent no-ops are forbidden.
No Qt / torch imports.
"""
from __future__ import annotations

import os


def _sar_filter(sar) -> str:
    if isinstance(sar, (list, tuple)) and len(sar) == 2 and sar[0] and sar[1]:
        return "setsar=%s/%s" % (sar[0], sar[1])
    return "setsar=1"


def vcodec_quality_args(use_gpu, export_mode="compressed"):
    """Return ``(vcodec, preset, extra_args)`` for the H.264 rawvideo encode pass."""
    from cutora.render.encoder_probe import pick_h264
    vcodec, _reason = pick_h264(bool(use_gpu))
    gpu = vcodec == "h264_nvenc"
    if gpu:
        if export_mode == "original":
            return "h264_nvenc", "p4", ["-rc", "constqp", "-qp", "0"]
        return "h264_nvenc", "p4", ["-rc", "vbr", "-cq", "18", "-b:v", "0"]
    if export_mode == "original":
        return "libx264", "ultrafast", ["-crf", "0"]
    return "libx264", "veryfast", ["-crf", "18"]


def build_rawvideo_ffmpeg_cmd(
    width,
    height,
    fps,
    output_path,
    *,
    use_gpu=False,
    export_mode="compressed",
    sar=None,
    enc_threads=0,
    ffmpeg_path=None,
    profile=None,
):
    """FFmpeg argv for the raw BGRA → encoded temp-video pass."""
    if not ffmpeg_path:
        from cutora.core.config import FFMPEG_PATH
        ffmpeg_path = FFMPEG_PATH
    if profile is not None:
        from cutora.render.export_profile import encode_args
        vcodec, preset, quality_args, pix = encode_args(profile, use_gpu)
    else:
        vcodec, preset, quality_args = vcodec_quality_args(use_gpu, export_mode)
        pix = "yuv420p"
    if not vcodec:
        raise ValueError("audio-only profile has no video encoder")
    cmd = [
        ffmpeg_path, "-y",
        "-f", "rawvideo",
        "-pix_fmt", "bgra",
        "-s", "%sx%s" % (width, height),
        "-r", str(fps),
        "-thread_queue_size", "64",
        "-i", "-",
        "-an",
        "-c:v", vcodec,
        "-vf", _sar_filter(sar),
        "-pix_fmt", pix or "yuv420p",
    ]
    if preset:
        cmd += ["-preset", preset]
    cmd += list(quality_args or [])
    cmd += ["-threads", str(int(enc_threads or 0)), output_path]
    return cmd


def resolve_export_run_output(task, out=None, out_dir=None, profile=None):
    """``export.run`` output path: ``out`` wins; else ``out_dir/{stem}_FINAL.<ext>``."""
    ext = "mp4"
    if isinstance(profile, dict) and profile.get("container"):
        ext = str(profile["container"]).lstrip(".")
    if out:
        return out
    if not out_dir:
        return None
    from cutora.render.export_dest import unique_mp4_path
    src = (task or {}).get("video") or "out.mp4"
    name = str((task or {}).get("name") or "").strip() or None
    suffix = "_FINAL"
    if ext != "mp4":
        os.makedirs(out_dir, exist_ok=True)
        stem = name or os.path.splitext(os.path.basename(src))[0]
        return os.path.join(out_dir, stem + suffix + "." + ext)
    return unique_mp4_path(out_dir, src, suffix, name=name)


def batch_output_path(task, out_dir=None):
    """Batch export path. ``out_dir`` overrides the source-side 字幕成品 folder."""
    video = (task or {}).get("video") or (task or {}).get("path") or "out.mp4"
    if out_dir:
        from cutora.render.export_dest import unique_mp4_path
        return unique_mp4_path(
            out_dir, video, "_sub",
            name=str((task or {}).get("name") or "").strip() or None)
    dest_dir = os.path.join(os.path.dirname(video), "字幕成品")
    os.makedirs(dest_dir, exist_ok=True)
    stem, ext = os.path.splitext(os.path.basename(video))
    out_name = ("%s%s" % (stem, ext)) if stem.endswith("_sub") else ("%s_sub%s" % (stem, ext))
    path = os.path.join(dest_dir, out_name)
    try:
        from cutora.batch import script_insert_image as _sii
        path = _sii.resolve_export_output_path(task, path)
    except Exception:
        pass
    return path


def apply_honor_io(task, honor_io):
    """Copy I/O points onto trim_start_sec / trim_end_sec when honor_io is set."""
    if not honor_io or not isinstance(task, dict):
        return task
    task = dict(task)
    inp = task.get("in_point")
    outp = task.get("out_point")
    if inp is not None:
        task["trim_start_sec"] = float(inp)
    if outp is not None:
        task["trim_end_sec"] = float(outp)
    return task


def should_honor_io(task) -> bool:
    """True when the current task has in/out points set (Ctrl+E uses this)."""
    if not isinstance(task, dict):
        return False
    return task.get("in_point") is not None or task.get("out_point") is not None


def format_tc(sec) -> str:
    """``MM:SS.mmm`` (or ``HH:MM:SS.mmm`` past one hour)."""
    t = max(0.0, float(sec or 0.0))
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = t - h * 3600 - m * 60
    if h:
        return "%02d:%02d:%06.3f" % (h, m, s)
    return "%02d:%06.3f" % (m, s)


def honor_io_dialog_line(task) -> str:
    """Caption when honor_io will shorten the file. Empty if it will not."""
    if not should_honor_io(task):
        return ""
    start, end = export_trim_window(task, True)
    full = float((task or {}).get("duration") or (task or {}).get("trim_end_sec") or 0.0)
    if full <= 0:
        full = end if end > 0 else start
    end_s = end if end > 0 else full
    return "将只导出入出点区间  %s – %s（全长 %s）" % (
        format_tc(start), format_tc(end_s), format_tc(full))


def export_trim_window(task, honor_io=False):
    """``(start_sec, end_sec)`` used for duration cap and mix-bus atrim.

    ``end_sec`` is 0 when unset (full remaining duration).
    """
    t = apply_honor_io(task, honor_io)
    if not isinstance(t, dict):
        return 0.0, 0.0
    return float(t.get("trim_start_sec") or 0.0), float(t.get("trim_end_sec") or 0.0)


def cap_duration(raw_dur, trim_end_sec) -> float:
    """Match RenderWorker._calc_project_duration trim-end clamp."""
    trim_end = float(trim_end_sec or 0.0)
    if trim_end > 0:
        return min(float(raw_dur or 0.0), trim_end)
    return float(raw_dur or 0.0)


def export_verify_expect(duration, fps, trim_start_sec=0.0):
    """(seconds, frames) of the file the renderer actually writes.

    ``duration`` is the timeline end, already clamped by trim_end.
    The loop starts at ``int(trim_start * fps)`` and does not write the
    frames before that. Checking the file against the full timeline
    treats a finished cut as a failure, and the batch then exports it
    again under a new name.
    """
    fps = float(fps or 0.0)
    duration = float(duration or 0.0)
    if fps <= 0 or duration <= 0:
        return 0.0, 0
    total = max(1, int(duration * fps))
    start = int(float(trim_start_sec or 0.0) * fps)
    if start < 0:
        start = 0
    if start > total:
        start = total
    n = total - start
    if n <= 0:
        return 0.0, 0
    return n / fps, n


def verify_window_for_task(task):
    """Expected (seconds, frames, fps) for a finished export of ``task``.

    ``(0, 0, fps)`` when the task has no duration — the caller then skips
    the length check, same as before.
    """
    if not isinstance(task, dict):
        task = {}
    try:
        raw = float(task.get("duration") or 0.0)
    except (TypeError, ValueError):
        raw = 0.0
    fps = 30.0
    tl = task.get("timeline")
    raw_fps = tl.get("fps") if isinstance(tl, dict) else getattr(tl, "fps", None)
    try:
        if raw_fps:
            fps = float(raw_fps)
    except (TypeError, ValueError):
        fps = 30.0
    if fps <= 0:
        fps = 30.0
    capped = cap_duration(raw, task.get("trim_end_sec"))
    seconds, frames = export_verify_expect(capped, fps, task.get("trim_start_sec"))
    return seconds, frames, fps


def audio_mix_trim_tail(trim_start_sec, trim_end_sec) -> str:
    """FFmpeg filter-complex tail for mix-bus I/O trim. Empty if no trim."""
    start = float(trim_start_sec or 0.0)
    end = float(trim_end_sec or 0.0)
    if start <= 0 and end <= 0:
        return ""
    args = []
    if start > 0:
        args.append("start=%s" % start)
    if end > 0:
        args.append("end=%s" % end)
    tail = ",atrim=" + ":".join(args)
    if end > 0:
        fade_dur = 0.5
        fade_start = max(0.0, end - fade_dur)
        tail += ",afade=t=out:st=%s:d=%s" % (fade_start, fade_dur)
    tail += ",asetpts=N/SR/TB"
    return tail
