# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""ASR command handlers. Import torch / whisper only inside the functions."""
from __future__ import annotations

from cutora.core.commands import CommandError, Context


def cmd_asr_detect_language(ctx: Context, params: dict) -> dict:
    path = str(params.get("media") or "").strip()
    if not path:
        path = _media_path_from_context(ctx, params.get("media_id"))
    if not path:
        raise CommandError("asr.detect_language 需要 media 路径或 media_id")
    from cutora.asr.detect import detect_for_media
    return detect_for_media(path)


def cmd_asr_models_list(ctx: Context, params: dict) -> dict:
    from cutora.asr.models import list_status
    from cutora.asr.tier import build_profile, probe_hardware
    rows = []
    for row in list_status():
        rows.append({
            "id": row.get("id"),
            "kind": row.get("kind"),
            "title_zh": row.get("title_zh"),
            "repo": row.get("repo"),
            "license": row.get("license"),
            "size_mb": row.get("size_mb"),
            "status": row.get("status"),
            "enabled": bool(row.get("enabled")),
            "path": row.get("path"),
            "note": row.get("note") or "",
            "local_only": bool(row.get("local_only")),
            "bundled": bool(row.get("bundled")),
        })
    try:
        prof = build_profile(probe_hardware())
        recommend = prof.asr_id
        tier = {
            "id": prof.asr_tier,
            "label": prof.label,
            "word_timing": prof.word_timing,
            "warning": prof.warning,
        }
    except Exception:
        recommend = "asr-small"
        tier = None
    return {"models": rows, "recommend": recommend, "tier": tier}


def cmd_asr_models_download(ctx: Context, params: dict) -> dict:
    from cutora.asr.download import download_model
    model_id = str(params.get("id") or "").strip()
    if not model_id:
        raise CommandError("asr.models.download 需要 id")
    return download_model(model_id)


def cmd_glossary_get(ctx: Context, params: dict) -> dict:
    from cutora.asr.glossary import load_global_terms, parse_terms
    scope = str(params.get("scope") or "global").strip().lower()
    if scope != "project":
        return {"scope": "global", "terms": load_global_terms()}
    window = ctx.window
    if window is None:
        raise CommandError(
            "glossary.get scope=project 需要已打开的工程", code="needs_project")
    terms = parse_terms(getattr(window, "project_glossary", None))
    return {"scope": "project", "terms": terms}


def cmd_glossary_set(ctx: Context, params: dict) -> dict:
    from cutora.asr.glossary import parse_terms, save_global_terms
    scope = str(params.get("scope") or "global").strip().lower()
    terms = parse_terms(params.get("terms") if params.get("terms") is not None
                        else params.get("text"))
    if scope != "project":
        save_global_terms(terms)
        return {"scope": "global", "count": len(terms), "terms": terms}
    window = ctx.window
    if window is None:
        raise CommandError(
            "glossary.set scope=project 需要已打开的工程", code="needs_project")
    window.project_glossary = terms
    return {"scope": "project", "count": len(terms), "terms": terms}


def cmd_asr_models_delete(ctx: Context, params: dict) -> dict:
    from cutora.asr.download import delete_model
    model_id = str(params.get("id") or "").strip()
    if not model_id:
        raise CommandError("asr.models.delete 需要 id")
    return delete_model(model_id)


def _media_path_from_context(ctx: Context, media_id) -> str:
    window = ctx.window
    if window is None:
        return ""
    tasks = list(getattr(window, "tasks", None) or [])
    task = None
    if media_id is not None:
        try:
            idx = int(media_id)
        except (TypeError, ValueError):
            idx = -1
        if 0 <= idx < len(tasks):
            task = tasks[idx]
    elif 0 <= int(getattr(window, "curr_idx", -1) or -1) < len(tasks):
        task = tasks[int(window.curr_idx)]
    elif len(tasks) == 1:
        task = tasks[0]
    if task is None:
        return ""
    return str(task.get("video") or "")
