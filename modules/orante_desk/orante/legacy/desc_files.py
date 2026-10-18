# -*- coding: utf-8 -*-
"""Normal 描述语 sidecar。

图片 v01.jpeg 旁边放 v01_desc.txt（不能叫 v01.txt，那是拆分文案）。
提交 / 自检 / 接力 / 突击都读这个文件，不再去表格 I 列。

旧批次只有 _desc_cache.json、还没 sidecar 的，仍然认缓存，避免老文件夹跑不动。
sidecar 优先于缓存。
"""
from __future__ import annotations

import json
import os
import re

_V_RE = re.compile(r"^v(\d+)$", re.I)
_SIDECAR_RE = re.compile(r"^(v\d+)_desc\.txt$", re.I)
CACHE_NAME = "_desc_cache.json"
IMG_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp")
SIDECAR_MIN = 20


def norm_label(stem):
    m = _V_RE.match((stem or "").strip())
    if not m:
        return None
    return "v%02d" % int(m.group(1))


def desc_filename(label):
    lab = norm_label(label) or (label or "").strip()
    return lab + "_desc.txt"


def desc_path(folder, label):
    return os.path.join(folder, desc_filename(label))


def desc_body(text):
    """提交用的 A 层正文：第一行非空、且不是【音色】行。"""
    if not text:
        return ""
    for line in text.splitlines():
        s = line.strip()
        if s and not s.startswith("【音色】"):
            return s
    return text.strip()


def _read_text(path):
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return f.read()
    except OSError:
        return ""


def _write_text(path, text):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
        if text and not text.endswith("\n"):
            f.write("\n")
    os.replace(tmp, path)


def _load_json(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def write_desc(folder, label, text):
    """写 sidecar，并同步进 _desc_cache.json（旧工具还在读缓存）。"""
    lab = norm_label(label)
    if not lab:
        return None
    body = (text or "").strip()
    _write_text(desc_path(folder, lab), body)
    cache_path = os.path.join(folder, CACHE_NAME)
    cache = _load_json(cache_path)
    cache[lab] = desc_body(body)
    _save_json(cache_path, cache)
    return desc_path(folder, lab)


def load_desc_map(folder, log=None):
    """{v01: 描述语正文}。sidecar 优先，缺的用 _desc_cache.json 补。"""
    folder = folder or ""
    out = {}
    n_file = 0
    cache = _load_json(os.path.join(folder, CACHE_NAME)) if folder else {}
    for k, v in cache.items():
        lab = norm_label(k) or str(k).strip()
        if isinstance(v, str) and v.strip():
            out[lab] = desc_body(v)
    if folder and os.path.isdir(folder):
        for name in os.listdir(folder):
            m = _SIDECAR_RE.match(name)
            if not m:
                continue
            lab = norm_label(m.group(1))
            raw = _read_text(os.path.join(folder, name))
            body = desc_body(raw)
            if not body:
                continue
            out[lab] = body
            n_file += 1
    n_cache = len(out) - n_file
    if log:
        if n_file or out:
            extra = ("，缓存补 %d 条（无 sidecar 的旧批次）" % n_cache
                     if n_cache > 0 else "")
            log("描述语：图片旁 sidecar %d 条%s" % (n_file, extra))
        else:
            log("描述语：文件夹里没有 vNN_desc.txt，也没有 _desc_cache.json")
    if n_file and folder:
        try:
            _save_json(os.path.join(folder, CACHE_NAME), out)
        except Exception:
            pass
    return out


def sidecar_filled(folder, label):
    p = desc_path(folder, label)
    return os.path.isfile(p) and os.path.getsize(p) > SIDECAR_MIN


def list_v_images(folder):
    """[(label, img_path, filled), ...] 按 v 号排序。只认 vNN 图，躲开 vNN.txt。"""
    out = []
    if not folder or not os.path.isdir(folder):
        return out
    seen = set()
    for name in os.listdir(folder):
        stem, ext = os.path.splitext(name)
        if ext.lower() not in IMG_EXTS:
            continue
        lab = norm_label(stem)
        if not lab or lab in seen:
            continue
        seen.add(lab)
        out.append((lab, os.path.join(folder, name), sidecar_filled(folder, lab)))
    out.sort(key=lambda x: int(x[0][1:]))
    return out


def copy_descs(src, dest, labels=None):
    """把 sidecar（及缓存里有、文件还没有的）拷到 dest。返回拷过去的 map。"""
    os.makedirs(dest, exist_ok=True)
    src_map = load_desc_map(src)
    if labels is not None:
        want = {norm_label(x) or x for x in labels}
        src_map = {k: v for k, v in src_map.items() if k in want}
    for lab, text in src_map.items():
        src_p = desc_path(src, lab)
        dst_p = desc_path(dest, lab)
        if os.path.isfile(src_p):
            raw = _read_text(src_p)
            _write_text(dst_p, raw)
        else:
            _write_text(dst_p, text)
    if src_map:
        cache = _load_json(os.path.join(dest, CACHE_NAME))
        cache.update(src_map)
        _save_json(os.path.join(dest, CACHE_NAME), cache)
    return src_map
