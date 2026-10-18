# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""T27-b: user assets under UserData/assets/user/<category>/."""
from __future__ import annotations

import json
import os
import re
import shutil
import uuid

from cutora.core.log import get_logger
from cutora.core.paths import resolve_user_data

_LOG = get_logger("cutora.assets.user")

USER_CATEGORIES = (
    ("element", "元素"),
    ("frame", "边框"),
    ("lower_third", "字幕条"),
    ("sticker", "贴纸"),
    ("animated", "动图"),
    ("video", "视频"),
    ("audio", "音频"),
)
USER_CATEGORY_KEYS = tuple(k for k, _ in USER_CATEGORIES)
DEFAULT_LICENSE = "自有"
REFERENCE_BYTES = 200 * 1024 * 1024
IMAGE_KINDS = ("element", "frame", "lower_third", "sticker")

_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".svg", ".gif"}
_VIDEO_EXT = {".mov", ".mp4", ".mkv", ".webm"}
_AUDIO_EXT = {".mp3", ".wav", ".flac", ".aac", ".ogg", ".m4a", ".wma"}
_UNSAFE_DIR = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WIN_RESERVED = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {"COM%d" % i for i in range(1, 10)}
    | {"LPT%d" % i for i in range(1, 10)}
)


def asset_folder_name(display_name: str, asset_id: str) -> str:
    """New imports only: ``<readable>_<id[:8]>``. Old id-named dirs stay as-is."""
    raw = str(display_name or "").strip() or "asset"
    slug = _UNSAFE_DIR.sub("", raw)
    slug = re.sub(r"[\s.]+", "_", slug).strip("_")
    if not slug:
        slug = "asset"
    if slug.upper() in _WIN_RESERVED:
        slug = "asset_" + slug
    slug = slug[:48]
    short = str(asset_id or "0")[:8] or "00000000"
    return "%s_%s" % (slug, short)


def _unique_asset_dir(cat_root: str, folder: str) -> str:
    dest = os.path.join(cat_root, folder)
    if not os.path.exists(dest):
        return dest
    n = 2
    while True:
        candidate = os.path.join(cat_root, "%s_%d" % (folder, n))
        if not os.path.exists(candidate):
            return candidate
        n += 1


def user_assets_root() -> str:
    d = os.path.join(resolve_user_data(), "assets", "user")
    os.makedirs(d, exist_ok=True)
    return d


def classify_import(path: str) -> str:
    from cutora.media.animated import classify_animated
    kind = classify_animated(path)
    if kind:
        return "animated"
    ext = os.path.splitext(path)[1].lower()
    if ext in _VIDEO_EXT:
        return "video"
    if ext in _AUDIO_EXT:
        return "audio"
    if ext == ".svg":
        return "element"
    if ext in _IMAGE_EXT:
        return "sticker"
    raise ValueError("不支持的资产类型：%s" % ext)


def _probe_alpha(path: str) -> bool:
    try:
        from cutora.render.readers import _probe_video_has_alpha
        info = _probe_video_has_alpha(path)
        return bool(info and info.get("has_alpha"))
    except Exception:
        _LOG.exception("alpha probe failed: %s", path)
        return False


def _copy_with_progress(src: str, dst: str, progress=None, chunk: int = 8 * 1024 * 1024) -> None:
    total = os.path.getsize(src)
    copied = 0
    with open(src, "rb") as fsrc, open(dst, "wb") as fdst:
        while True:
            buf = fsrc.read(chunk)
            if not buf:
                break
            fdst.write(buf)
            copied += len(buf)
            if progress is not None:
                progress(copied, total)
    shutil.copystat(src, dst)


def import_user_asset(path: str, category: str | None = None,
                      license_text: str | None = None, host=None,
                      copy: bool | None = None, progress=None,
                      storage_mode: str = "library",
                      project_dir: str | None = None) -> dict:
    if not path or not os.path.isfile(path):
        raise FileNotFoundError(path)
    cat = category or classify_import(path)
    if cat not in USER_CATEGORY_KEYS:
        raise ValueError("未知分类：%s" % cat)
    mode = (storage_mode or "library").strip().lower()
    if mode not in ("library", "project"):
        mode = "library"
    if mode == "project":
        if not project_dir:
            raise ValueError("随工程保存需要先打开工程")
        cat_root = os.path.join(os.path.abspath(project_dir), "assets", cat)
    else:
        cat_root = os.path.join(user_assets_root(), cat)
    asset_id = uuid.uuid4().hex[:12]
    filename = os.path.basename(path)
    display = os.path.splitext(filename)[0]
    os.makedirs(cat_root, exist_ok=True)
    dest_dir = _unique_asset_dir(cat_root, asset_folder_name(display, asset_id))
    os.makedirs(dest_dir, exist_ok=True)
    abs_src = os.path.abspath(path)
    size = os.path.getsize(abs_src)
    if copy is None:
        copy = size <= REFERENCE_BYTES
    referenced = not bool(copy)
    if referenced:
        dest_file = abs_src
    else:
        dest_file = os.path.join(dest_dir, filename)
        _copy_with_progress(abs_src, dest_file, progress=progress)
    alpha = False
    if cat in ("video", "animated"):
        alpha = _probe_alpha(dest_file) or cat == "animated"
    rec = {
        "id": asset_id,
        "type": cat,
        "name": os.path.splitext(filename)[0],
        "license": license_text or DEFAULT_LICENSE,
        "source_path": abs_src,
        "file": filename,
        "alpha": bool(alpha),
        "referenced": bool(referenced),
        "storage_mode": mode,
    }
    if cat == "video" and alpha:
        rec["alpha_mode"] = "straight"
    meta = os.path.join(dest_dir, "item.json")
    with open(meta, "w", encoding="utf-8") as fh:
        json.dump(rec, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    rec["_dir"] = dest_dir
    rec["stored_path"] = dest_file
    rec["missing"] = not os.path.isfile(dest_file)
    if cat == "video" and alpha:
        try:
            from cutora.media.alpha_proxy import ensure_alpha_proxy, preview_path_for
            proxy = ensure_alpha_proxy(
                dest_file, host=host, sync=False, item_json=meta)
            if proxy:
                rec["preview_path"] = proxy
            else:
                cand = preview_path_for(dest_file)
                if cand != dest_file:
                    rec["preview_path"] = cand
            if rec.get("preview_path"):
                # The first item.json write happened before the proxy path
                # existed. Persist it now so a later open sees the same path
                # the in-memory record already has.
                payload = {
                    k: v for k, v in rec.items()
                    if k not in ("_dir", "stored_path", "missing")
                }
                with open(meta, "w", encoding="utf-8") as fh:
                    json.dump(payload, fh, ensure_ascii=False, indent=2)
                    fh.write("\n")
        except Exception:
            _LOG.exception("proxy kickoff failed: %s", dest_file)
    _LOG.info("imported user asset %s/%s from %s referenced=%s",
              cat, asset_id, path, referenced)
    return rec


def load_user_item(item_json: str) -> dict:
    with open(item_json, encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict) or not data.get("id"):
        raise ValueError("user item.json 无效：%s" % item_json)
    data.setdefault("license", DEFAULT_LICENSE)
    data.setdefault("type", "sticker")
    data.setdefault("name", data["id"])
    data["_dir"] = os.path.dirname(item_json)
    referenced = bool(data.get("referenced"))
    source = data.get("source_path") or ""
    copied = os.path.join(data["_dir"], data.get("file") or "")
    if referenced:
        data["stored_path"] = source or copied
    else:
        data["stored_path"] = copied
    data["missing"] = not bool(data["stored_path"] and os.path.isfile(data["stored_path"]))
    return data


def _scan_asset_root(root: str, cats: list[str], storage_mode: str) -> list[dict]:
    out = []
    if not root or not os.path.isdir(root):
        return out
    for cat in cats:
        cdir = os.path.join(root, cat)
        if not os.path.isdir(cdir):
            continue
        for name in sorted(os.listdir(cdir)):
            meta = os.path.join(cdir, name, "item.json")
            if not os.path.isfile(meta):
                continue
            try:
                rec = load_user_item(meta)
            except Exception:
                _LOG.exception("skip bad user item %s", meta)
                continue
            rec.setdefault("storage_mode", storage_mode)
            rec["storage_mode"] = rec.get("storage_mode") or storage_mode
            out.append(rec)
    return out


def iter_user_assets(category: str | None = None,
                     project_dir: str | None = None) -> list[dict]:
    cats = [category] if category else list(USER_CATEGORY_KEYS)
    out = _scan_asset_root(user_assets_root(), cats, "library")
    if project_dir:
        out.extend(_scan_asset_root(
            os.path.join(os.path.abspath(project_dir), "assets"),
            cats, "project"))
    return out


def _image_wh(path: str) -> tuple[int, int]:
    ext = os.path.splitext(path or "")[1].lower()
    if ext == ".svg":
        try:
            from PySide6.QtSvg import QSvgRenderer
            rend = QSvgRenderer(path)
            sz = rend.defaultSize()
            if sz.width() > 0 and sz.height() > 0:
                return int(sz.width()), int(sz.height())
        except Exception:
            _LOG.exception("svg size failed: %s", path)
        return 200, 200
    try:
        from PySide6.QtGui import QImage
        img = QImage(path)
        if not img.isNull() and img.width() > 0 and img.height() > 0:
            return int(img.width()), int(img.height())
    except Exception:
        pass
    try:
        import struct
        with open(path, "rb") as fh:
            sig = fh.read(8)
            if sig == b"\x89PNG\r\n\x1a\n":
                _len, tag = struct.unpack(">I4s", fh.read(8))
                if tag == b"IHDR":
                    w, h = struct.unpack(">II", fh.read(8))
                    return int(w), int(h)
    except Exception:
        pass
    return 200, 200


def build_image_overlay(path: str, start_t: float, tw: int, th: int,
                        track_id=None, duration: float = 5.0, ov_id: str | None = None) -> dict:
    """Same dict ``MainWindow._drop_image_file`` puts on ``image_overlays``.

    The picture's position and size are the element's, from
    ``image_fit_rect`` (fit inside the canvas, centered). The overlay box
    stays the full canvas so element x/y are canvas pixels.
    """
    from cutora.model.factory_defaults import overlay_defaults

    iw, ih = _image_wh(path)
    tw = max(1, int(tw))
    th = max(1, int(th))
    dur = max(0.05, float(duration))
    return overlay_defaults(
        "image",
        canvas_w=tw,
        canvas_h=th,
        image_wh=(iw, ih),
        path=path,
        start=float(start_t),
        duration=dur,
        track_id=track_id,
        ov_id=ov_id,
    )


def _ensure_image_track(tl):
    from cutora.model.timeline_model import Track
    for tr in tl.tracks:
        if getattr(tr, "type", None) == "image":
            return tr
    track = Track("image")
    tl.add_track(track)
    return track


def overlay_video_track(tl):
    """Non-main video track stacked above the main video track."""
    from cutora.model.timeline_model import Track
    main_idx = None
    for i, tr in enumerate(tl.tracks):
        if getattr(tr, "type", None) == "video" and getattr(tr, "is_main_video", False):
            main_idx = i
            break
    if main_idx is not None:
        for tr in tl.tracks[main_idx + 1:]:
            if getattr(tr, "type", None) == "video" and not getattr(tr, "is_main_video", False):
                return tr
    overlay = Track("video")
    overlay.name = "叠加"
    overlay.is_main_video = False
    tl.add_track(overlay)
    return overlay


def _place_image_overlay(tl, item: dict, start_t: float, task):
    path = item.get("stored_path") or ""
    if not path or not os.path.isfile(path):
        raise FileNotFoundError(path)
    track = _ensure_image_track(tl)
    tw = int(getattr(tl, "width", 0) or 0) or 1080
    th = int(getattr(tl, "height", 0) or 0) or 1920
    duration = 5.0
    if isinstance(task, dict):
        main_dur = 0.0
        try:
            main_dur = float(task.get("duration") or 0.0)
        except Exception:
            main_dur = 0.0
        if main_dur <= 0:
            for tr in getattr(tl, "tracks", []) or []:
                if not getattr(tr, "is_main_video", False):
                    continue
                for c in getattr(tr, "clips", []) or []:
                    try:
                        main_dur = max(0.0, float(getattr(c, "out_t", 0.0)) - float(getattr(c, "in_t", 0.0)))
                    except Exception:
                        continue
                    if main_dur > 0:
                        break
        if main_dur > 0:
            remaining = max(0.0, float(main_dur) - float(start_t))
            if remaining > 0:
                duration = min(duration, remaining)
        task.setdefault("image_overlays", []).append(
            build_image_overlay(
                path, start_t, tw, th,
                track_id=getattr(track, "id", None), duration=duration))
        return task["image_overlays"][-1]
    ov = build_image_overlay(
        path, start_t, tw, th,
        track_id=getattr(track, "id", None), duration=duration)
    stash = getattr(tl, "image_overlays", None)
    if stash is None:
        tl.image_overlays = []
        stash = tl.image_overlays
    stash.append(ov)
    return ov


def place_on_timeline(tl, item: dict, start_t: float = 0.0, task=None):
    """Put a user/builtin asset onto ``tl``. Images → image_overlays; alpha video → overlay track."""
    from cutora.model.timeline_model import Clip, MediaSource, Track

    kind = item.get("type")
    path = item.get("stored_path") or ""
    if kind == "animated":
        from cutora.media.animated import (
            SEQUENCE_RE, import_animated_as_clip, persistent_anim_dir,
        )
        src_path = item.get("source_path") or ""
        if not os.path.isfile(src_path):
            if SEQUENCE_RE.search(str(src_path).replace("\\", "/")):
                pass
            else:
                src_path = path
        dest_dir = persistent_anim_dir(
            item.get("_project_dir"),
            item.get("storage_mode") or "library")
        info = import_animated_as_clip(
            src_path, fps=getattr(tl, "fps", None), dest_dir=dest_dir)
        path = info["path"]
        item = dict(item)
        item["alpha"] = True
        item["_anim_info"] = info
    if kind in IMAGE_KINDS or (
            kind not in ("video", "animated", "audio")
            and os.path.splitext(path)[1].lower() in _IMAGE_EXT):
        return _place_image_overlay(tl, dict(item, stored_path=path), start_t, task)
    if not path or not os.path.isfile(path):
        raise FileNotFoundError(path)
    src = MediaSource(path)
    src.alpha = bool(item.get("alpha"))
    anim_info = item.get("_anim_info") or {}
    if anim_info.get("source_path"):
        src.source_path = anim_info["source_path"]
    if anim_info.get("kind"):
        src.kind = anim_info["kind"]
    mode = item.get("alpha_mode")
    if mode:
        src.alpha_mode = mode
        try:
            from cutora.render.readers import set_path_alpha_mode
            set_path_alpha_mode(path, mode)
        except Exception:
            _LOG.exception("set_path_alpha_mode failed: %s", path)
    proxy = item.get("preview_path")
    if not (proxy and (os.path.isfile(proxy) or ("%d" in str(proxy)))):
        try:
            from cutora.media.alpha_proxy import preview_path_for
            cand = preview_path_for(path)
            if cand and cand != path:
                proxy = cand
        except Exception:
            _LOG.exception("preview_path_for failed: %s", path)
            proxy = None
    if proxy and (os.path.isfile(proxy) or ("%d" in str(proxy))):
        src.preview_path = proxy
        # The proxy file can land after import_user_asset already wrote
        # item.json. Persist the path the timeline is about to use.
        item_dir = item.get("_dir")
        if item_dir:
            meta = os.path.join(item_dir, "item.json")
            if os.path.isfile(meta):
                try:
                    with open(meta, encoding="utf-8") as fh:
                        stored = json.load(fh)
                    if isinstance(stored, dict) and stored.get("preview_path") != proxy:
                        stored["preview_path"] = proxy
                        with open(meta, "w", encoding="utf-8") as fh:
                            json.dump(stored, fh, ensure_ascii=False, indent=2)
                            fh.write("\n")
                except Exception:
                    _LOG.exception("persist preview_path failed: %s", meta)
    tl.sources[src.id] = src
    if kind == "audio":
        track = None
        for tr in tl.tracks:
            if getattr(tr, "type", None) == "audio":
                track = tr
                break
        if track is None:
            track = Track("audio")
            tl.add_track(track)
    else:
        track = overlay_video_track(tl)
    dur = float(src.duration or 0.0) or 2.0
    clip = Clip(src.id, 0.0, dur, float(start_t))
    clip.alpha = bool(src.alpha)
    if kind == "animated":
        clip.loop = True
    track.clips.append(clip)
    return clip
