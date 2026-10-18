# -*- coding: utf-8 -*-
"""编排面板图片编辑：复制 / 水平翻转 / 裁切。改 manifest 并落盘。

覆盖原文件前把旧图挪到 images/_replaced/（复用 expand._move_file）。
翻转和原地裁切会打回引用它的段（expand._reset_refs）。
"""
from __future__ import annotations

import copy
import shutil
import time
from pathlib import Path

from PIL import Image, ImageOps

from .. import events
from ..errors import OranteError
from ..models import Manifest, sha1_file
from . import expand

MIN_SIDE = 256


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _load(tid: str, p: str):
    d = expand._task_dir(tid)
    m = Manifest.load(d)
    im = m.image(p)
    if not im:
        raise OranteError(f"没有图片 {p}")
    rel = (im.get("file") or "").replace("\\", "/")
    src = d / rel
    if not src.is_file():
        raise OranteError(f"{p} 的文件不在", rel)
    return d, m, im, src


def _open(path: Path) -> Image.Image:
    pic = Image.open(path)
    pic.load()
    out = ImageOps.exif_transpose(pic)
    if out is not None and out is not pic:
        pic.close()
        return out
    return pic


def _save(pic: Image.Image, dest: Path) -> None:
    """按原后缀落盘，不缩放。JPEG 转成可存的模式。"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    ext = dest.suffix.lower()
    work = pic
    if ext in (".jpg", ".jpeg"):
        if work.mode not in ("RGB", "L"):
            work = work.convert("RGB")
        work.save(dest, format="JPEG", quality=95)
    elif ext == ".png":
        work.save(dest, format="PNG")
    elif ext == ".webp":
        work.save(dest, format="WEBP", quality=95)
    else:
        if work.mode not in ("RGB", "L"):
            work = work.convert("RGB")
        work.save(dest, format="JPEG", quality=95)


def _backup(d: Path, p: str, src: Path) -> Path:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    bak = d / expand.REPL_DIR / f"{p}-{stamp}{src.suffix or '.jpg'}"
    expand._move_file(src, bak)
    return bak


def _dest_rel(im: dict, p: str, ext: str) -> str:
    if not ext.startswith("."):
        ext = "." + ext
    if im.get("role") == "空镜":
        return f"{expand.BROLL_DIR}/{p}{ext}"
    return f"{expand.IMG_DIR}/{p}{ext}"


def _clone_rec(src: dict, p: str, rel: str, origin: str) -> dict:
    rec = {
        "p": p,
        "file": rel.replace("\\", "/"),
        "sha1": "",
        "role": src.get("role") or "主体",
        "status": "ok",
        "desc": src.get("desc") or "",
        "voice": src.get("voice") or "",
        "eye": src.get("eye") or "",
        "scene": src.get("scene") or "常规",
        "measure": copy.deepcopy(src.get("measure") or {}),
        "origin": origin,
        "origin_from": src.get("p"),
        "src": src.get("src") or "",
        "expand_ref": False,
    }
    if "flipped" in src:
        rec["flipped"] = src.get("flipped")
    return rec


def _swap_facing(facing) -> str:
    s = str(facing or "")
    if not s:
        return s
    if "左" in s and "右" in s:
        return s
    if "左" in s:
        return s.replace("左", "右")
    if "右" in s:
        return s.replace("右", "左")
    return s


def _append_edit(im: dict, op: str, box) -> None:
    edits = list(im.get("edits") or [])
    edits.append({"op": op, "at": _now(), "box": list(box) if box is not None else []})
    im["edits"] = edits


def _result(im, reset) -> dict:
    return {"image": im, "reset_segments": list(reset or [])}


def image_info(task_id: str, p: str) -> dict:
    p = (p or "").strip()
    _d, _m, im, src = _load(task_id, p)
    pic = _open(src)
    try:
        w, h = pic.size
    finally:
        pic.close()
    return {"w": int(w), "h": int(h), "file": (im.get("file") or "").replace("\\", "/")}


def duplicate(task_id: str, p: str) -> dict:
    p = (p or "").strip()
    d, m, im, src = _load(task_id, p)
    new_p = expand._next_p(m.images)
    ext = src.suffix or ".jpg"
    rel = _dest_rel(im, new_p, ext)
    dest = d / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(str(src), str(dest))
    rec = _clone_rec(im, new_p, rel, "dup")
    rec["sha1"] = sha1_file(dest) if dest.is_file() else ""
    m.images.append(rec)
    m.save(d)
    events.emit("tasks.changed")
    return _result(rec, [])


def flip(task_id: str, p: str) -> dict:
    p = (p or "").strip()
    d, m, im, src = _load(task_id, p)
    orig_rel = (im.get("file") or "").replace("\\", "/")
    dest = d / orig_rel
    pic = _open(src)
    try:
        box = [0, 0, int(pic.size[0]), int(pic.size[1])]
        mirrored = ImageOps.mirror(pic)
    finally:
        pic.close()
    _backup(d, p, src)
    try:
        _save(mirrored, dest)
    finally:
        mirrored.close()
    im["sha1"] = sha1_file(dest) if dest.is_file() else ""
    im["flipped"] = not bool(im.get("flipped"))
    meas = dict(im.get("measure") or {})
    if meas.get("facing"):
        meas["facing"] = _swap_facing(meas.get("facing"))
        im["measure"] = meas
    _append_edit(im, "flip", box)
    expand._reset_refs(m, p)
    refs = [int(sg.get("s") or 0) for sg in m.segments
            if sg.get("image") == p and int(sg.get("s") or 0)]
    m.save(d)
    events.emit("tasks.changed")
    return _result(im, refs)


def crop(task_id: str, p: str, body: dict | None = None) -> dict:
    p = (p or "").strip()
    body = body or {}
    d, m, im, src = _load(task_id, p)
    try:
        x = int(round(float(body.get("x"))))
        y = int(round(float(body.get("y"))))
        w = int(round(float(body.get("w"))))
        h = int(round(float(body.get("h"))))
    except (TypeError, ValueError):
        raise OranteError("裁切矩形不合法", "需要 x, y, w, h 四个数字。")
    as_new = bool(body.get("as_new"))
    pic = _open(src)
    try:
        iw, ih = pic.size
        if w <= 0 or h <= 0:
            raise OranteError("裁切矩形不合法", f"宽和高必须大于 0，当前 {w}×{h}。")
        if x < 0 or y < 0 or x + w > iw or y + h > ih:
            raise OranteError("裁切矩形越界", f"图是 {iw}×{ih}，框是 ({x},{y},{w},{h})。")
        if min(w, h) < MIN_SIDE:
            raise OranteError("裁切区域短边小于 256 像素", f"当前 {w}×{h}。")
        pixels = pic.crop((x, y, x + w, y + h)).copy()
    finally:
        pic.close()

    box = [x, y, w, h]
    if as_new:
        new_p = expand._next_p(m.images)
        ext = src.suffix or ".jpg"
        rel = _dest_rel(im, new_p, ext)
        dest = d / rel
        try:
            _save(pixels, dest)
        finally:
            pixels.close()
        rec = _clone_rec(im, new_p, rel, "crop")
        rec["sha1"] = sha1_file(dest) if dest.is_file() else ""
        m.images.append(rec)
        m.save(d)
        events.emit("tasks.changed")
        return _result(rec, [])

    orig_rel = (im.get("file") or "").replace("\\", "/")
    dest = d / orig_rel
    _backup(d, p, src)
    try:
        _save(pixels, dest)
    finally:
        pixels.close()
    im["sha1"] = sha1_file(dest) if dest.is_file() else ""
    _append_edit(im, "crop", box)
    expand._reset_refs(m, p)
    refs = [int(sg.get("s") or 0) for sg in m.segments
            if sg.get("image") == p and int(sg.get("s") or 0)]
    m.save(d)
    events.emit("tasks.changed")
    return _result(im, refs)
