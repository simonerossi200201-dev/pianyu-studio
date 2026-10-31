# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Handlers for the agent/MCP gap-fill commands (specs_mcp.py).

Rules: no modal dialogs, no Qt import at module level, no torch at import.
GUI handlers only touch the bound MainWindow; headless handlers reuse the
existing CLI / job helpers in ``cutora.batch.cli`` and ``cutora.api.jobs``.
"""
from __future__ import annotations

import os

from cutora.core.commands import CommandError, Context, NeedsGui


def _win(ctx: Context):
    if ctx.window is None:
        raise NeedsGui()
    return ctx.window


# ---------------------------------------------------------------- export ----

def cmd_export_cancel(ctx: Context, params: dict) -> dict:
    """Stop the running single export or batch export (same as the stop button)."""
    window = _win(ctx)
    was_export = bool(getattr(window, "is_exporting", False))
    was_batch = bool(getattr(window, "is_batch_exporting", False))
    if not was_export and not was_batch:
        return {"stopped": False, "reason": "当前没有正在进行的导出"}
    if was_batch and callable(getattr(window, "toggle_batch_export", None)):
        # toggle_batch_export() while running == the "停止批量导出" button.
        window.toggle_batch_export()
    stop = getattr(window, "stop_export", None)
    if callable(stop) and bool(getattr(window, "is_exporting", False)):
        stop()
    return {
        "stopped": True,
        "was_export": was_export,
        "was_batch": was_batch,
        "exporting": bool(getattr(window, "is_exporting", False)),
        "batch": bool(getattr(window, "is_batch_exporting", False)),
    }


# ------------------------------------------------------------------ app -----

def cmd_app_release_vram(ctx: Context, params: dict) -> dict:
    """Unload cached ASR / AI models (菜单 性能 → 释放显存) without message boxes."""
    window = ctx.window
    if window is not None and (
        getattr(window, "is_ai_running", False) or getattr(window, "is_batch_running", False)
    ):
        raise CommandError("AI 字幕任务正在运行，请等待结束后再释放显存。", code="busy")
    import sys
    if "torch" not in sys.modules:
        # Nothing was ever loaded in this process; never import torch just to free it.
        return {"freed_gb": 0.0, "loaded": False}
    from cutora.asr.engine import release_gpu_models
    freed = float(release_gpu_models() or 0.0)
    return {"freed_gb": round(freed, 3), "loaded": True}


# -------------------------------------------------------------- project -----

def cmd_project_recent_list(ctx: Context, params: dict) -> dict:
    from cutora.core.history import _get_recent_projects
    limit = int(params.get("limit") or 20)
    items = []
    for it in (_get_recent_projects() or [])[: max(1, min(limit, 50))]:
        if isinstance(it, dict):
            path = str(it.get("path") or "")
            items.append({
                "path": path,
                "timestamp": it.get("timestamp"),
                "exists": bool(path) and os.path.isfile(path),
            })
    return {"items": items, "count": len(items)}


def cmd_project_canvas_set(ctx: Context, params: dict) -> dict:
    window = _win(ctx)
    w = int(params.get("width") or 0)
    h = int(params.get("height") or 0)
    if not (16 <= w <= 8192 and 16 <= h <= 8192):
        raise CommandError("画布尺寸必须在 16~8192 之间")
    fn = getattr(window, "_set_canvas_size", None)
    if not callable(fn):
        raise CommandError("主窗口没有画布尺寸接口")
    fn(w, h)
    return {"width": w, "height": h}


def cmd_view_fit_mode_set(ctx: Context, params: dict) -> dict:
    window = _win(ctx)
    mode = str(params.get("mode") or "")
    if mode not in ("fit", "fill", "stretch"):
        raise CommandError("mode 只能是 fit / fill / stretch")
    fn = getattr(window, "_set_fit_mode", None)
    if not callable(fn):
        raise CommandError("主窗口没有适配模式接口")
    fn(mode)
    group = getattr(window, "_fit_mode_actions", None)
    try:
        for act in (group.actions() if group is not None else []):
            if act.data() == mode:
                act.setChecked(True)
    except Exception:
        pass
    return {"mode": mode}


# ----------------------------------------------------------------- edit -----

def _history_info(window) -> dict:
    mgr = getattr(window, "history_mgr", None)
    if mgr is None:
        return {"index": -1, "count": 0}
    hist = list(getattr(mgr, "history", []) or [])
    return {"index": int(getattr(mgr, "current_index", -1)), "count": len(hist)}


def cmd_edit_undo(ctx: Context, params: dict) -> dict:
    window = _win(ctx)
    steps = max(1, min(int(params.get("steps") or 1), 50))
    fn = getattr(window, "undo", None)
    if not callable(fn):
        raise CommandError("主窗口没有撤销接口")
    for _ in range(steps):
        fn()
    out = _history_info(window)
    out["steps"] = steps
    return out


def cmd_edit_redo(ctx: Context, params: dict) -> dict:
    window = _win(ctx)
    steps = max(1, min(int(params.get("steps") or 1), 50))
    fn = getattr(window, "redo", None)
    if not callable(fn):
        raise CommandError("主窗口没有重做接口")
    for _ in range(steps):
        fn()
    out = _history_info(window)
    out["steps"] = steps
    return out


def cmd_edit_history(ctx: Context, params: dict) -> dict:
    window = _win(ctx)
    mgr = getattr(window, "history_mgr", None)
    entries = []
    for i, e in enumerate(list(getattr(mgr, "history", []) or [])):
        entries.append({
            "index": i,
            "desc": e.get("desc"),
            "at": e.get("at"),
            "command": e.get("command"),
            "source": e.get("source"),
        })
    info = _history_info(window)
    info["entries"] = entries
    return info


# ----------------------------------------------------------------- jobs -----

def _jobs_dir() -> str:
    from cutora.api.jobs import jobs_dir
    return jobs_dir()


def cmd_job_list(ctx: Context, params: dict) -> dict:
    import json
    limit = max(1, min(int(params.get("limit") or 20), 200))
    root = _jobs_dir()
    files = []
    for name in os.listdir(root):
        if name.endswith(".json"):
            p = os.path.join(root, name)
            try:
                files.append((os.path.getmtime(p), p))
            except OSError:
                pass
    files.sort(reverse=True)
    items = []
    for _mt, p in files[:limit]:
        try:
            with open(p, "r", encoding="utf-8") as fh:
                info = json.load(fh)
        except Exception:
            continue
        if not isinstance(info, dict):
            continue
        items.append({
            "job_id": info.get("job_id") or os.path.splitext(os.path.basename(p))[0],
            "op": info.get("op") or info.get("command"),
            "status": info.get("status"),
            "pid": info.get("pid"),
            "started_at": info.get("started_at"),
            "log": info.get("log"),
        })
    return {"jobs": items, "count": len(items), "dir": root}


def cmd_job_status(ctx: Context, params: dict) -> dict:
    job_id = str(params.get("job_id") or "").strip()
    from cutora.api.jobs import read_job
    from cutora.batch.cli import cmd_status
    info = read_job(job_id) if job_id else None
    status = cmd_status(job_id or None)
    if info is not None:
        status["job"] = info
    tail_n = max(0, min(int(params.get("log_tail") or 20), 400))
    log_path = (info or {}).get("log") or status.get("log")
    if tail_n and log_path and os.path.isfile(str(log_path)):
        try:
            with open(str(log_path), "r", encoding="utf-8", errors="replace") as fh:
                status["log_tail"] = fh.read().splitlines()[-tail_n:]
        except OSError:
            pass
    return status


def cmd_job_cancel(ctx: Context, params: dict) -> dict:
    job_id = str(params.get("job_id") or "").strip()
    if not job_id:
        raise CommandError("job_id 不能为空")
    from cutora.api.jobs import read_job, update_job
    info = read_job(job_id)
    if info is None:
        raise CommandError("找不到作业 %s" % job_id, code="unknown")
    from cutora.render.export_guard import pid_alive, terminate_pids, write_stop_flag
    state_path = info.get("state_path")
    flagged = False
    if state_path:
        try:
            write_stop_flag(state_path)
            flagged = True
        except Exception:
            flagged = False
    pid = info.get("pid")
    killed = False
    if pid:
        try:
            pid = int(pid)
            if pid != os.getpid() and pid_alive(pid):
                terminate_pids([pid])
                killed = True
        except Exception:
            killed = False
    update_job(job_id, status="cancelled")
    return {"job_id": job_id, "stop_flag": flagged, "killed": killed, "pid": pid}


# ---------------------------------------------------------------- queue -----

def cmd_batch_queue_status(ctx: Context, params: dict) -> dict:
    from cutora.batch.cli import queue_summary
    return {"queue": queue_summary()}


def cmd_batch_queue_pause(ctx: Context, params: dict) -> dict:
    from cutora.batch.cli import pause_queue, queue_summary
    pause_queue(str(params.get("reason") or "agent"))
    return {"queue": queue_summary()}


def cmd_batch_queue_resume(ctx: Context, params: dict) -> dict:
    from cutora.batch.cli import queue_summary, unpause_queue
    unpause_queue()
    return {"queue": queue_summary()}


def cmd_batch_queue_deactivate(ctx: Context, params: dict) -> dict:
    from cutora.batch.cli import deactivate_queue, queue_summary
    deactivate_queue()
    return {"queue": queue_summary()}


# ------------------------------------------------------------ face-safe -----

def _abs_file(path: str, what: str) -> str:
    p = os.path.abspath(str(path or "").strip().strip('"'))
    if not p or not os.path.isfile(p):
        raise CommandError("%s 不存在: %s" % (what, path))
    return p


def cmd_batch_face_safe_verify(ctx: Context, params: dict) -> dict:
    """Re-run face-safe framing calibration on a saved project, write a NEW file."""
    from cutora.batch.cli import ensure_qapp, load_project_file, write_project_file
    from cutora.batch.face_safe import verify_and_calibrate_tasks
    src = _abs_file(params.get("project"), "工程文件")
    out = str(params.get("out") or "").strip()
    if not out:
        stem, ext = os.path.splitext(src)
        if stem.endswith(".asproj"):
            stem, ext = stem[: -len(".asproj")], ".asproj" + ext
        out = stem + "_facesafe" + (ext or ".cutora")
    out = os.path.abspath(out)
    if os.path.normcase(out) == os.path.normcase(src):
        raise CommandError("out 不能覆盖源工程；请给一个新路径")
    ensure_qapp()
    from cutora.ui import main_window as host
    data, tasks = load_project_file(host, src)
    sheets = str(params.get("contact_sheet_dir") or "").strip() or None
    report = verify_and_calibrate_tasks(
        tasks,
        force_top_main_if_needed=bool(params.get("force_top_main")),
        render_contact_sheet_dir=sheets,
    )
    write_project_file(host, out, tasks, curr_idx=data.get("curr_idx", -1))
    slim = dict(report or {})
    if isinstance(slim.get("reports"), list) and len(slim["reports"]) > 50:
        slim["reports"] = slim["reports"][:50]
        slim["reports_truncated"] = True
    return {"ok": True, "project": out, "source": src, "report": slim}


def cmd_batch_auto_project(ctx: Context, params: dict) -> dict:
    from cutora.batch.auto_project import build_batch_project
    batch_dir = str(params.get("batch_dir") or "").strip()
    videos = str(params.get("videos_dir") or "").strip()
    clips = str(params.get("clips_manifest") or "").strip()
    for label, p in (("batch_dir", batch_dir), ("videos_dir", videos)):
        if not p or not os.path.isdir(p):
            raise CommandError("%s 不存在: %s" % (label, p))
    if not clips or not os.path.exists(clips):
        raise CommandError("clips_manifest 不存在: %s" % clips)
    kwargs = dict(
        batch_dir=batch_dir,
        videos_dir=videos,
        clips_manifest=clips,
        table_path=str(params.get("table") or ""),
        phone_img=str(params.get("phone_img") or ""),
        project_name=str(params.get("project_name") or ""),
        export_dest=str(params.get("export_dest") or ""),
        parts=int(params.get("parts") or 2),
    )
    ref = str(params.get("ref_project") or "").strip()
    if ref:
        kwargs["ref_project"] = ref
    return build_batch_project(**kwargs)


# ----------------------------------------------------------- silence cut -----

def _silence_params_from_dict(params: dict):
    from cutora.audio.silence_cut import SilenceParams
    return SilenceParams.from_dict({
        "threshold": params.get("threshold", 1.0),
        "unit": params.get("unit", "s"),
        "keep": params.get("keep", 0.25),
        "keep_unit": params.get("keep_unit", params.get("unit", "s")),
        "head_frames": params.get("head_frames", 2),
        "tail_frames": params.get("tail_frames", 2),
        "keep_breaths": params.get("keep_breaths", True),
        "use_vad": params.get("use_vad", True),
        "trim_edges": params.get("trim_edges", True),
    })


def cmd_silence_detect(ctx: Context, params: dict) -> dict:
    from cutora.audio.silence_cut import AudioCache, plan_task
    sp = _silence_params_from_dict(params)
    proj = str(params.get("project") or "").strip()
    if proj:
        from cutora.batch.cli import ensure_qapp, load_project_file
        src = _abs_file(proj, "工程文件")
        ensure_qapp()
        from cutora.ui import main_window as host
        _data, tasks = load_project_file(host, src)
    else:
        win = _win(ctx)
        tasks = list(getattr(win, "tasks", None) or [])
    if not tasks:
        raise CommandError("没有可检测的视频任务")
    t_idx = params.get("task_index", -1)
    indices = list(range(len(tasks))) if (t_idx is None or int(t_idx) < 0) else [int(t_idx)]
    cache = AudioCache()
    plans = []
    total_cuts = 0
    total_rem = 0.0
    for idx in indices:
        if not (0 <= idx < len(tasks)):
            raise CommandError(f"task_index 越界: {idx}")
        pl = plan_task(tasks[idx], sp, cache=cache, task_index=idx)
        plans.append(pl)
        summ = pl.get("summary") or {}
        total_cuts += int(summ.get("n_cuts") or 0)
        total_rem += float(summ.get("remove_s") or 0.0)
    return {
        "count": len(plans),
        "total_cuts": total_cuts,
        "total_removed_s": round(total_rem, 3),
        "plans": plans,
    }


def cmd_silence_apply(ctx: Context, params: dict) -> dict:
    from cutora.audio.silence_cut import AudioCache, cut_task_silences
    win = _win(ctx)
    tasks = list(getattr(win, "tasks", None) or [])
    if not tasks:
        raise CommandError("当前没有打开的视频任务")
    sp = _silence_params_from_dict(params)
    t_idx = params.get("task_index", -1)
    indices = list(range(len(tasks))) if (t_idx is None or int(t_idx) < 0) else [int(t_idx)]
    undo = getattr(win, "push_undo", None)
    if callable(undo):
        try:
            undo("剪气口")
        except TypeError:
            try:
                undo()
            except Exception:
                pass
    cache = AudioCache()
    results = []
    total_cuts = 0
    total_rem = 0.0
    verify_ok = 0
    for idx in indices:
        if not (0 <= idx < len(tasks)):
            raise CommandError(f"task_index 越界: {idx}")
        res = cut_task_silences(tasks[idx], sp, cache=cache, verify=True, force=True)
        res["task_index"] = idx
        results.append(res)
        total_cuts += int(res["applied"]["n_cuts"])
        total_rem += float(res["applied"]["removed_s"])
        if res.get("verify") and res["verify"].get("ok"):
            verify_ok += 1
    for fn_name in ("update_timeline_ui", "update_preview"):
        fn = getattr(win, fn_name, None)
        if callable(fn):
            try:
                fn()
            except Exception:
                pass
    return {
        "ok": verify_ok == len(results),
        "count": len(results),
        "total_cuts": total_cuts,
        "total_removed_s": round(total_rem, 3),
        "verify_ok": verify_ok,
        "results": results,
    }


def cmd_silence_batch(ctx: Context, params: dict) -> dict:
    from cutora.audio.silence_cut import batch_silence_cut
    from cutora.batch.cli import ensure_qapp, load_project_file, write_project_file
    src = _abs_file(params.get("project"), "工程文件")
    out = str(params.get("out") or "").strip()
    if not out:
        stem, ext = os.path.splitext(src)
        if stem.endswith(".asproj"):
            stem, ext = stem[: -len(".asproj")], ".asproj" + ext
        out = stem + "_debreath" + (ext or ".cutora")
    out = os.path.abspath(out)
    if os.path.normcase(out) == os.path.normcase(src):
        raise CommandError("out 不能覆盖源工程；请提供新路径")
    sp = _silence_params_from_dict(params)
    ensure_qapp()
    from cutora.ui import main_window as host
    data, tasks = load_project_file(host, src)
    report = batch_silence_cut(tasks, sp, verify=True, force=True)
    write_project_file(host, out, tasks, curr_idx=data.get("curr_idx", -1))
    return {
        "ok": bool(report.get("ok")),
        "source": src,
        "project": out,
        "count": report.get("count"),
        "total_cuts": report.get("total_cuts"),
        "total_removed_s": report.get("total_removed_s"),
        "verify_ok_count": report.get("verify_ok_count"),
    }


# ------------------------------------------------------- stitch grouping -----

def cmd_stitch_scan_groups(ctx: Context, params: dict) -> dict:
    from cutora.batch.stitch_grouping import GROUP_AUTO, scan_folders
    folders = list(params.get("folders") or [])
    if not folders:
        raise CommandError("folders 不能为空")
    mode = str(params.get("mode") or GROUP_AUTO)
    gm = str(params.get("group_mark") or "")
    om = str(params.get("order_mark") or "")
    res = scan_folders(folders, mode=mode, group_mark=gm, order_mark=om)
    return res.as_dict()


def cmd_stitch_precheck(ctx: Context, params: dict) -> dict:
    from cutora.batch.stitch_grouping import GROUP_AUTO, precheck, scan_folders
    folders = list(params.get("folders") or [])
    if not folders:
        raise CommandError("folders 不能为空")
    mode = str(params.get("mode") or GROUP_AUTO)
    scripts = dict(params.get("scripts") or {})
    res = scan_folders(folders, mode=mode)
    return precheck(res, scripts=scripts)


# ------------------------------------------------ studio & batch helpers -----

def cmd_batch_title_align(ctx: Context, params: dict) -> dict:
    from cutora.batch.title_align import align_title
    proj = str(params.get("project") or "").strip()
    apply_changes = bool(params.get("apply", False))
    if proj:
        from cutora.batch.cli import ensure_qapp, load_project_file, write_project_file
        src = _abs_file(proj, "工程文件")
        ensure_qapp()
        from cutora.ui import main_window as host
        data, tasks = load_project_file(host, src)
    else:
        win = _win(ctx)
        tasks = list(getattr(win, "tasks", None) or [])
        src = ""
        data = {}
    if not tasks:
        raise CommandError("没有可识别标题的任务")
    t_idx = params.get("task_index", -1)
    indices = list(range(len(tasks))) if (t_idx is None or int(t_idx) < 0) else [int(t_idx)]
    rows = []
    matched = 0
    for idx in indices:
        if not (0 <= idx < len(tasks)):
            raise CommandError(f"task_index 越界: {idx}")
        t = tasks[idx]
        ovs = [ov for ov in (t.get("text_overlays") or []) if isinstance(ov, dict)]
        title_txt = str((ovs[0].get("text") if ovs else "") or t.get("title") or "").strip()
        res = align_title(t.get("segments") or [], title_txt)
        kind = res.get("how") or res.get("kind") or res.get("status")
        if kind in ("matched", "partial"):
            matched += 1
            if apply_changes and ovs and res.get("start") is not None and res.get("end") is not None:
                ovs[0]["start"] = float(res["start"])
                ovs[0]["end"] = float(res["end"])
        rows.append({
            "task_index": idx,
            "title": title_txt,
            "status": kind,
            "ratio": res.get("score") if res.get("score") is not None else res.get("ratio"),
            "start": res.get("start"),
            "end": res.get("end"),
            "reason": res.get("why") or res.get("reason"),
        })
    out_path = ""
    if proj and apply_changes:
        out_path = str(params.get("out") or src).strip()
        from cutora.batch.cli import write_project_file
        from cutora.ui import main_window as host
        write_project_file(host, out_path, tasks, curr_idx=data.get("curr_idx", -1))
    return {
        "count": len(rows),
        "matched": matched,
        "applied": apply_changes,
        "project": out_path or src,
        "items": rows,
    }


def cmd_studio_overview(ctx: Context, params: dict) -> dict:
    from cutora import __version__
    from cutora.asr.languages import FIRST_CLASS_LANGS
    from cutora.core import paths
    from cutora.render.export_guard import gpu_identity, is_legacy_nvenc_gpu
    studio_root = os.path.abspath(os.path.join(paths.REPO_ROOT, "..", ".."))
    modules_dir = os.path.join(studio_root, "modules")
    gpu_info = gpu_identity()
    gpu = gpu_info[0] if isinstance(gpu_info, tuple) else str(gpu_info or "")
    return {
        "studio_version": __version__,
        "studio_root": studio_root,
        "cutora_root": paths.REPO_ROOT,
        "user_data": getattr(paths, "USER_DATA", ""),
        "gpu_name": gpu,
        "legacy_nvenc_guard": is_legacy_nvenc_gpu(gpu),
        "first_class_languages": sorted(FIRST_CLASS_LANGS),
        "modules": {
            "cutora_editor": os.path.isdir(paths.REPO_ROOT),
            "orante_studio": os.path.isdir(os.path.join(modules_dir, "orante_studio")),
            "table_tool": os.path.isdir(os.path.join(modules_dir, "table_tool")),
            "robota": os.path.isdir(os.path.join(studio_root, "robota")),
        },
    }


def cmd_orante_list_tasks(ctx: Context, params: dict) -> dict:
    from cutora.batch.orante_bridge import load_orante_tasks
    task_range = str(params.get("task_range") or "").strip()
    limit = max(1, min(500, int(params.get("limit") or 50)))
    try:
        items = load_orante_tasks(task_range=task_range or None)
    except Exception as exc:
        return {"count": 0, "items": [], "note": str(exc)}
    sliced = list(items or [])[:limit]
    return {"count": len(sliced), "total": len(items or []), "items": sliced}


def cmd_table_inspect(ctx: Context, params: dict) -> dict:
    import csv
    import json
    src = _abs_file(params.get("path"), "表格文件")
    limit = max(1, min(200, int(params.get("limit") or 20)))
    ext = os.path.splitext(src)[1].lower()
    if ext == ".json":
        with open(src, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        rows = data if isinstance(data, list) else data.get("rows") or data.get("tasks") or [data]
        return {"path": src, "total": len(rows), "preview": rows[:limit]}
    with open(src, "r", encoding="utf-8-sig", errors="replace") as fh:
        sample = fh.read(4096)
        fh.seek(0)
        delim = "\t" if "\t" in sample else ","
        reader = list(csv.reader(fh, delimiter=delim))
    headers = reader[0] if reader else []
    body = reader[1:] if len(reader) > 1 else []
    return {
        "path": src,
        "delimiter": "tab" if delim == "\t" else "comma",
        "headers": headers,
        "total": len(body),
        "preview": body[:limit],
    }


def cmd_blackbox_status(ctx: Context, params: dict) -> dict:
    from cutora.core.blackbox import analyze_hardware_and_advise, analyze_post_mortem
    inc_win = bool(params.get("include_win_events", True))
    hw = analyze_hardware_and_advise()
    pm = analyze_post_mortem(include_win_events=inc_win)
    return {
        "hardware_advisor": hw,
        "post_mortem": pm,
    }


def cmd_blackbox_export(ctx: Context, params: dict) -> dict:
    from cutora.core.blackbox import export_blackbox_zip
    dest = str(params.get("dest_dir") or "").strip() or None
    inc_win = bool(params.get("include_win_events", True))
    return export_blackbox_zip(dest_dir=dest, include_win_events=inc_win)

