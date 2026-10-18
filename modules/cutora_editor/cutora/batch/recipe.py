# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Recipe files (*.cutorarecipe), recording, and folder apply."""
from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Callable

from cutora.core.log import get_logger
from cutora.core.paths import REPO_ROOT, FFPROBE
from cutora.core.proc import popen as _no_window_popen, run as _no_window_run

logger = get_logger("cutora.batch.recipe")

SCHEMA_PATH = os.path.join(REPO_ROOT, "resources", "data", "recipe.schema.json")
BUILTIN_DIR = os.path.join(REPO_ROOT, "resources", "data", "recipes")
PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")
VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".mkv", ".avi", ".webm"}
GPU_SERIAL = {"subtitle.auto"}
EXPORT_CMDS = {"export.run", "export.batch"}


class RecipeError(Exception):
    """Invalid recipe file or apply failure. message names step/field."""


def default_jobs() -> int:
    cpu = os.cpu_count() or 4
    return max(1, min(2, cpu // 4))


def load_schema() -> dict:
    with open(SCHEMA_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def validate_recipe(data: Any) -> dict:
    if not isinstance(data, dict):
        raise RecipeError("配方根节点必须是 JSON 对象")
    try:
        from jsonschema import Draft202012Validator
        from jsonschema.exceptions import ValidationError
    except Exception as exc:
        raise RecipeError("jsonschema 不可用: %s" % exc) from exc
    try:
        Draft202012Validator(load_schema()).validate(data)
    except ValidationError as exc:
        parts = []
        for p in exc.absolute_path:
            if isinstance(p, int):
                parts.append("步骤 %d" % (p + 1))
            else:
                parts.append(str(p))
        where = " / ".join(parts) if parts else "根"
        raise RecipeError("配方不合法（%s）: %s" % (where, exc.message)) from exc
    check_recipe_portable(data)
    return data


def check_recipe_portable(recipe: dict) -> None:
    """Reject recipes that contain media-bound (portable=False) steps.

    Does not skip those steps: the whole recipe is refused so a folder
    apply cannot silently drop work. Caller should re-record.
    """
    from cutora.core.commands import CommandError, get_registry
    reg = get_registry()
    bad = []
    steps = recipe.get("steps") if isinstance(recipe, dict) else None
    if not isinstance(steps, list):
        return
    for i, step in enumerate(steps):
        if not isinstance(step, dict):
            continue
        name = str(step.get("command") or "")
        why = _portable_reason(reg, name)
        if why:
            bad.append((i + 1, name, why))
        skip = step.get("skip_if") if isinstance(step.get("skip_if"), dict) else None
        if skip:
            probe = str(skip.get("command") or "")
            if probe:
                why = _portable_reason(reg, probe)
                if why:
                    bad.append((i + 1, probe, "skip_if: " + why))
    if not bad:
        return
    lines = [
        "这份配方含有不能跨视频重放的步骤，已拒绝执行。请重新录制这份配方。",
    ]
    for n, name, why in bad:
        lines.append("  步骤 %d：%s（%s）" % (n, name, why))
    raise RecipeError("\n".join(lines))


def _portable_reason(reg, name: str) -> str | None:
    from cutora.core.commands import CommandError
    if not name:
        return "缺少命令名"
    try:
        spec = reg.get(name)
    except CommandError:
        return "未知命令"
    if not bool(getattr(spec, "portable", False)):
        return "参数与具体媒体绑定"
    return None


def load_recipe(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except json.JSONDecodeError as exc:
        raise RecipeError("不是合法 JSON（第 %s 行）: %s" % (exc.lineno, exc.msg)) from exc
    except OSError as exc:
        raise RecipeError("读配方失败: %s" % exc) from exc
    return validate_recipe(data)


def save_recipe(path: str, data: dict) -> str:
    validate_recipe(data)
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    return path


def builtin_recipes() -> list[str]:
    if not os.path.isdir(BUILTIN_DIR):
        return []
    names = sorted(n for n in os.listdir(BUILTIN_DIR) if n.endswith(".cutorarecipe"))
    return [os.path.join(BUILTIN_DIR, n) for n in names]


def substitute(value: Any, mapping: dict[str, str]) -> Any:
    if isinstance(value, str):
        def repl(m):
            key = m.group(1)
            if key in mapping:
                return str(mapping[key])
            return m.group(0)
        return PLACEHOLDER_RE.sub(repl, value)
    if isinstance(value, list):
        return [substitute(v, mapping) for v in value]
    if isinstance(value, dict):
        return {k: substitute(v, mapping) for k, v in value.items()}
    return value


def rewrite_recorded_params(command: str, params: dict) -> dict:
    """Replace media paths with {media} and output dirs with {out_dir}."""
    out = json.loads(json.dumps(params or {}))
    if command == "media.import":
        paths = out.get("paths")
        if isinstance(paths, list) and paths:
            out["paths"] = ["{media}"]
    for key in ("out", "out_dir"):
        if key in out and isinstance(out[key], str) and out[key]:
            out[key] = "{out_dir}"
    return out


def should_record(name: str, source: str, undoable: bool, portable: bool = False) -> bool:
    """Record UI steps that are portable. undoable is unused (export.run is not)."""
    _ = undoable
    if source != "ui":
        return False
    if not portable:
        return False
    if name.startswith("app."):
        return False
    return True


def list_media_files(folder: str, recursive: bool = False) -> list[str]:
    folder = os.path.abspath(folder)
    out = []
    if recursive:
        for root, _dirs, files in os.walk(folder):
            for name in files:
                if os.path.splitext(name)[1].lower() in VIDEO_EXTS:
                    out.append(os.path.join(root, name))
    else:
        for name in os.listdir(folder):
            path = os.path.join(folder, name)
            if os.path.isfile(path) and os.path.splitext(name)[1].lower() in VIDEO_EXTS:
                out.append(path)
    out.sort()
    return out


def probe_media(path: str) -> dict:
    """Return duration info or raise RecipeError if not a usable video."""
    if not os.path.isfile(path):
        raise RecipeError("文件不存在: %s" % path)
    import subprocess
    cmd = [
        FFPROBE, "-v", "error", "-show_entries", "format=duration",
        "-of", "json", path,
    ]
    try:
        proc = _no_window_run(
            cmd, capture_output=True, timeout=20, check=False)
    except Exception as exc:
        raise RecipeError("ffprobe 失败: %s" % exc) from exc
    if proc.returncode != 0:
        raise RecipeError("不是可用视频: %s" % os.path.basename(path))
    try:
        info = json.loads(proc.stdout.decode("utf-8", "replace") or "{}")
        dur = float((info.get("format") or {}).get("duration") or 0)
    except Exception:
        dur = 0.0
    if dur < 0.05:
        raise RecipeError("时长无效: %s" % os.path.basename(path))
    return {"path": path, "duration": dur}


def _ts(sec: float) -> str:
    ms = int(round(max(0.0, float(sec)) * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return "%02d:%02d:%02d,%03d" % (h, m, s, ms)


def format_srt(segments: list) -> str:
    lines = []
    for i, seg in enumerate(segments or [], 1):
        start = float(seg.get("start") or 0)
        end = float(seg.get("end") or start)
        text = str(seg.get("text") or "").strip()
        lines.append(str(i))
        lines.append("%s --> %s" % (_ts(start), _ts(end)))
        lines.append(text)
        lines.append("")
    return "\n".join(lines)


def segments_for_media(media: str, project: str | None = None) -> list:
    """Load subtitle segments from an explicit project or a sibling file."""
    segs: list = []
    if project and os.path.isfile(project):
        segs = _segments_from_project(project, media)
    if segs:
        return segs
    sibling = os.path.splitext(media)[0] + ".asproj.json"
    folder_proj = os.path.join(os.path.dirname(media), "project.asproj.json")
    for cand in (sibling, folder_proj):
        if os.path.isfile(cand):
            segs = _segments_from_project(cand, media)
            if segs:
                return segs
    return []


def export_srt_for_media(media: str, out_dir: str, project: str | None = None) -> str:
    os.makedirs(out_dir, exist_ok=True)
    segs = segments_for_media(media, project=project)
    if not segs:
        raise RecipeError("没有字幕可导出: %s" % os.path.basename(media))
    dest = os.path.join(out_dir, os.path.splitext(os.path.basename(media))[0] + ".srt")
    with open(dest, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(format_srt(segs))
    return dest


def _segments_from_project(project: str, media: str) -> list:
    try:
        with open(project, encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return []
    want = os.path.normcase(os.path.abspath(media))
    base = os.path.basename(media).lower()
    for task in data.get("tasks") or []:
        path = task.get("video") or task.get("path") or ""
        if os.path.normcase(os.path.abspath(path)) == want:
            return list(task.get("segments") or [])
        if os.path.basename(path).lower() == base:
            return list(task.get("segments") or [])
    tasks = data.get("tasks") or []
    if len(tasks) == 1:
        return list(tasks[0].get("segments") or [])
    return []


class RecipeRecorder:
    def __init__(self) -> None:
        self.active = False
        self.steps: list[dict] = []
        self.skipped: list[str] = []
        self._hook = None

    def start(self) -> None:
        from cutora.core.commands import get_bus
        self.active = True
        self.steps = []
        self.skipped = []
        if self._hook is None:
            self._hook = self._on_executed
            get_bus().on_executed(self._hook)

    def stop(self) -> list[dict]:
        self.active = False
        from cutora.core.commands import get_bus
        if self._hook is not None:
            get_bus().off_executed(self._hook)
            self._hook = None
        return list(self.steps)

    def _on_executed(self, event: dict) -> None:
        if not self.active:
            return
        name = str(event.get("name") or "")
        source = str(event.get("source") or "")
        spec = None
        try:
            from cutora.core.commands import get_registry
            spec = get_registry().get(name)
        except Exception:
            return
        portable = bool(getattr(spec, "portable", False))
        if not should_record(name, source, bool(spec.undoable), portable):
            if (source == "ui" and spec.undoable and not portable
                    and not name.startswith("app.") and name != "media.select"):
                self.skipped.append(name)
                self._notify_not_portable(name)
            return
        params = rewrite_recorded_params(name, dict(event.get("params") or {}))
        self.steps.append({
            "command": name, "params": params, "on_error": "stop",
        })

    def _notify_not_portable(self, name: str) -> None:
        try:
            from cutora.core.commands import get_bus
            win = getattr(get_bus(), "_window", None)
            bar = getattr(win, "statusBar", None) if win is not None else None
            if callable(bar):
                bar().showMessage("该操作与当前媒体绑定，未录入配方", 3500)
        except Exception:
            logger.debug("portable skip notice failed for %s", name)


_RECORDER = RecipeRecorder()


def get_recorder() -> RecipeRecorder:
    return _RECORDER


def publish_progress(job_id: str, percent: float, stage: str, **extra) -> None:
    from cutora.api.events import HUB
    payload = {"job_id": job_id, "percent": float(percent), "stage": stage}
    payload.update(extra)
    HUB.publish("progress", payload)


def read_export_progress() -> dict:
    from cutora.core.paths import resolve_user_data
    path = os.path.join(resolve_user_data(), "export_state.json")
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _skip_if_matched(step: dict, mapping: dict, execute) -> bool:
    spec = step.get("skip_if")
    if not isinstance(spec, dict):
        return False
    probe = str(spec.get("command") or "")
    field = str(spec.get("field") or "")
    if not probe or not field:
        return False
    params = substitute(dict(spec.get("params") or {}), mapping)
    if execute is not None:
        result = execute(probe, params)
    else:
        from cutora.core.commands import get_bus
        result = get_bus().execute(probe, params, source="recipe")
    if not isinstance(result, dict):
        return False
    expected = spec["equals"] if "equals" in spec else True
    return result.get(field) == expected


def apply_to_folder(
    recipe: dict,
    folder: str,
    out_dir: str,
    *,
    jobs: int | None = None,
    recursive: bool = False,
    execute: Callable[[str, dict], dict] | None = None,
    on_file: Callable[[dict], None] | None = None,
) -> dict:
    """Run recipe against each video in folder. Returns the batch report."""
    recipe = validate_recipe(recipe)
    files = list_media_files(folder, recursive=recursive)
    if not files:
        raise RecipeError("目录里没有视频: %s" % folder)
    os.makedirs(out_dir, exist_ok=True)
    n_jobs = int(jobs if jobs is not None else default_jobs())
    serial = any(st.get("command") in GPU_SERIAL for st in recipe["steps"])
    if serial:
        n_jobs = 1
    rows = []
    t_all = time.perf_counter()
    for media in files:
        row = _apply_one(
            recipe, media, out_dir, execute=execute, on_file=on_file)
        rows.append(row)
        _ = n_jobs  # parallel export reserved; GPU recipes stay serial
    report = {
        "ok": True,
        "recipe": recipe.get("name"),
        "folder": os.path.abspath(folder),
        "out_dir": os.path.abspath(out_dir),
        "jobs": n_jobs,
        "elapsed_sec": round(time.perf_counter() - t_all, 3),
        "counts": {
            "total": len(rows),
            "ok": sum(1 for r in rows if r["status"] == "ok"),
            "skip": sum(1 for r in rows if r["status"] == "skip"),
            "fail": sum(1 for r in rows if r["status"] == "fail"),
        },
        "files": rows,
    }
    dest = os.path.join(out_dir, "_cutora_batch_report.json")
    with open(dest, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    report["report_path"] = dest
    return report


def _apply_one(recipe, media, out_dir, execute=None, on_file=None) -> dict:
    from cutora.api.jobs import create_job, update_job
    from cutora.core.commands import CommandError, get_bus
    job = create_job("recipe.apply", {"media": media, "recipe": recipe.get("name")})
    job_id = job["job_id"]
    mapping = {"media": media, "out_dir": out_dir}
    t0 = time.perf_counter()
    status, error = "ok", None
    artifacts = []
    skipped_steps = []
    publish_progress(job_id, 0, "start", file=os.path.basename(media))
    try:
        for i, step in enumerate(recipe["steps"]):
            cmd = step["command"]
            params = substitute(dict(step.get("params") or {}), mapping)
            on_error = step.get("on_error") or "stop"
            publish_progress(
                job_id, (i / max(1, len(recipe["steps"]))) * 100,
                cmd, file=os.path.basename(media), step=i + 1)
            try:
                if _skip_if_matched(step, mapping, execute):
                    skipped_steps.append(cmd)
                    continue
                if execute is not None:
                    result = execute(cmd, params)
                else:
                    result = get_bus().execute(cmd, params, source="recipe")
                if cmd in EXPORT_CMDS:
                    st = read_export_progress()
                    if st:
                        done = float(st.get("done") or 0)
                        total = float(st.get("total") or 0) or 1.0
                        publish_progress(
                            job_id, 100.0 * done / total, "export",
                            file=os.path.basename(media),
                            export_status=st.get("status"))
                if isinstance(result, dict):
                    art = result.get("artifact") or result.get("path")
                    if art:
                        artifacts.append(art)
            except Exception as exc:
                if on_error == "skip":
                    status, error = "skip", str(exc)
                    break
                raise
        else:
            status = "ok"
    except Exception as exc:
        status, error = "fail", str(exc)
        logger.exception("配方套用失败 %s", media)
    elapsed = round(time.perf_counter() - t0, 3)
    update_job(
        job_id, status="skipped" if status == "skip" else (
            "ok" if status == "ok" else "failed"),
        error=error, result={"artifacts": artifacts})
    publish_progress(
        job_id, 100, status, file=os.path.basename(media), error=error)
    row = {
        "file": os.path.basename(media),
        "path": media,
        "job_id": job_id,
        "status": status,
        "elapsed_sec": elapsed,
        "error": error,
        "artifacts": artifacts,
        "skipped_steps": skipped_steps,
    }
    if on_file:
        on_file(row)
    return row
