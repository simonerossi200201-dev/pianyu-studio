# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""T21: project canvas is the export size. No exceptions."""
from __future__ import annotations

# 顺序即优先级：欢迎页拿第一项当主按钮，画布菜单按同一顺序排。
# 720×1280 是 Luca 最常用的短视频尺寸，放第一。
CANVAS_PRESETS = (
    (720, 1280, "竖屏 720"),
    (1080, 1920, "竖屏"),
    (1280, 720, "横屏 720"),
    (1920, 1080, "横屏"),
)

DEFAULT_NEW_CANVAS = (720, 1280)
SETTING_NEW_CANVAS = "new_project_canvas"


def parse_canvas_spec(spec) -> tuple[int, int] | None:
    raw = str(spec or "").strip().lower().replace(" ", "")
    raw = raw.replace("×", "x").replace("*", "x")
    if "x" not in raw:
        return None
    a, b = raw.split("x", 1)
    try:
        w, h = int(a), int(b)
    except (TypeError, ValueError):
        return None
    if w <= 0 or h <= 0:
        return None
    return w, h


def default_new_canvas_wh(settings=None) -> tuple[int, int]:
    if settings is None:
        try:
            from cutora.core.history import _load_app_settings
            settings = _load_app_settings() or {}
        except Exception:
            from cutora.core.log import get_logger
            get_logger("cutora.model.canvas").exception(
                "load app settings for default canvas failed")
            settings = {}
    if not isinstance(settings, dict):
        settings = {}
    spec = settings.get(SETTING_NEW_CANVAS)
    parsed = parse_canvas_spec(spec) if spec not in (None, "") else None
    if parsed:
        return parsed
    return DEFAULT_NEW_CANVAS


def template_from_settings(settings=None, fps: float | None = None) -> dict:
    from cutora.model.timeline import DEFAULT_TIMELINE_FPS
    w, h = default_new_canvas_wh(settings)
    rate = float(fps if fps not in (None, "") else DEFAULT_TIMELINE_FPS)
    if rate <= 0:
        rate = float(DEFAULT_TIMELINE_FPS)
    return {"width": int(w), "height": int(h), "fps": rate}


def apply_canvas(task: dict, w: int, h: int) -> tuple[int, int]:
    w, h = int(w), int(h)
    if isinstance(task, dict):
        task["dims"] = (w, h)
        tl = task.get("timeline")
        if tl is not None and hasattr(tl, "width"):
            tl.width = w
            tl.height = h
        elif isinstance(tl, dict):
            tl["width"] = w
            tl["height"] = h
    return w, h


def canvas_wh(task: dict | None) -> tuple[int, int]:
    task = task or {}
    tl = task.get("timeline")
    if tl is not None and hasattr(tl, "width"):
        w, h = int(getattr(tl, "width", 0) or 0), int(getattr(tl, "height", 0) or 0)
        if w > 0 and h > 0:
            return w, h
    if isinstance(tl, dict):
        w, h = int(tl.get("width") or 0), int(tl.get("height") or 0)
        if w > 0 and h > 0:
            return w, h
    dims = task.get("dims")
    if isinstance(dims, (list, tuple)) and len(dims) == 2:
        try:
            w, h = int(dims[0]), int(dims[1])
            if w > 0 and h > 0:
                return w, h
        except (TypeError, ValueError):
            pass
    return 1080, 1920
