# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Transcript edits: LCS word remap, split/merge, find/replace, case."""
from __future__ import annotations

import csv
import re
from typing import Any

from cutora.subtitle.track_source import set_segments

_END_PUNCT = re.compile(r"[\s.,;:!?…—\-]+$", re.UNICODE)


def _word_text(item: Any) -> str:
    if isinstance(item, dict):
        return str(item.get("word") or item.get("text") or "")
    return str(getattr(item, "text", "") or "")


def tokenize_words(text: str) -> list[str]:
    return [t for t in (text or "").split() if t]


def lcs_old_index(old: list[str], new: list[str]) -> list[int | None]:
    """For each new token, index into old or None. Equality is exact."""
    n, m = len(old), len(new)
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        row, nxt = dp[i], dp[i + 1]
        for j in range(m - 1, -1, -1):
            if old[i] == new[j]:
                row[j] = nxt[j + 1] + 1
            else:
                row[j] = nxt[j] if nxt[j] >= row[j + 1] else row[j + 1]
    mapping: list[int | None] = [None] * m
    i = j = 0
    while i < n and j < m:
        if old[i] == new[j]:
            mapping[j] = i
            i += 1
            j += 1
        elif i + 1 <= n and dp[i + 1][j] >= dp[i][j + 1]:
            i += 1
        else:
            j += 1
    return mapping


def _token_char_span(tokens: list[str], index: int) -> tuple[int, int]:
    pos = 0
    for i, tok in enumerate(tokens):
        if i == index:
            return pos, pos + len(tok)
        pos += len(tok) + 1
    return 0, 0


def remap_words(old_words: list, new_text: str, start: float, end: float) -> list[dict]:
    """Keep timings for LCS hits; interpolate the rest by character span."""
    tokens = tokenize_words(new_text)
    if not tokens:
        return []
    old_list = list(old_words or [])
    old_toks = [_word_text(w) for w in old_list]
    mapped = lcs_old_index(old_toks, tokens)
    total = max(1, len(" ".join(tokens)))
    dur = max(0.0, float(end) - float(start))
    out: list[dict] = []
    for i, tok in enumerate(tokens):
        src_i = mapped[i]
        if src_i is not None and 0 <= src_i < len(old_list):
            src = old_list[src_i]
            if isinstance(src, dict):
                row = dict(src)
            else:
                row = {
                    "word": _word_text(src),
                    "start": float(getattr(src, "start", start)),
                    "end": float(getattr(src, "end", end)),
                }
            row["word"] = tok
            out.append(row)
            continue
        a, b = _token_char_span(tokens, i)
        t0 = float(start) + dur * (a / total)
        t1 = float(start) + dur * (b / total)
        if t1 <= t0:
            t1 = t0 + 1e-3
        out.append({"word": tok, "start": t0, "end": t1})
    return out


def apply_set_text(seg: dict, text: str) -> dict:
    """Write text, remap words, lock. Returns the mutated segment."""
    new_text = str(text or "")
    start = float(seg.get("start") or 0)
    end = float(seg.get("end") or 0)
    seg["text"] = new_text
    seg["words"] = remap_words(seg.get("words") or [], new_text, start, end)
    seg["locked"] = True
    seg.pop("lines", None)
    return seg


def transform_case(text: str, mode: str) -> str:
    raw = str(text or "")
    kind = (mode or "").strip().lower()
    if kind == "upper":
        return raw.upper()
    if kind == "lower":
        return raw.lower()
    if kind in ("title", "capital"):
        return raw.title()
    return raw


def strip_end_punct(text: str) -> str:
    return _END_PUNCT.sub("", str(text or "")).rstrip()


def _compile_query(query: str, regex: bool, case: bool):
    flags = 0 if case else re.IGNORECASE
    if regex:
        return re.compile(query, flags)
    return re.compile(re.escape(query), flags)


def find_in_text(text: str, query: str, *, regex: bool = False, case: bool = False) -> list[tuple[int, int]]:
    if not query:
        return []
    try:
        pat = _compile_query(query, regex, case)
    except re.error:
        return []
    return [(m.start(), m.end()) for m in pat.finditer(text or "")]


def replace_in_text(
    text: str, query: str, replacement: str, *, regex: bool = False, case: bool = False,
) -> str:
    if not query:
        return str(text or "")
    try:
        pat = _compile_query(query, regex, case)
    except re.error:
        return str(text or "")
    return pat.sub(replacement, text or "")


def char_index_to_time(seg: dict, at_char: int) -> float:
    text = str(seg.get("text") or "")
    start = float(seg.get("start") or 0)
    end = float(seg.get("end") or 0)
    n = max(1, len(text))
    cut = max(0, min(int(at_char), n))
    return start + (end - start) * (cut / n)


def time_to_char_index(seg: dict, time: float) -> int:
    text = str(seg.get("text") or "")
    start = float(seg.get("start") or 0)
    end = float(seg.get("end") or 0)
    dur = end - start
    if dur <= 0:
        return 0
    frac = (float(time) - start) / dur
    frac = max(0.0, min(1.0, frac))
    return int(round(frac * len(text)))


def _split_words(words: list, mid: float) -> tuple[list, list]:
    left, right = [], []
    for w in words or []:
        if not isinstance(w, dict):
            continue
        ws = float(w.get("start") or 0)
        we = float(w.get("end") or 0)
        center = (ws + we) / 2.0
        if center <= mid:
            left.append(dict(w))
        else:
            right.append(dict(w))
    return left, right


def split_segment(seg: dict, *, at_char: int | None = None, time: float | None = None) -> tuple[dict, dict]:
    text = str(seg.get("text") or "")
    start = float(seg.get("start") or 0)
    end = float(seg.get("end") or 0)
    if at_char is None:
        if time is None:
            time = (start + end) / 2.0
        at_char = time_to_char_index(seg, float(time))
    at_char = max(1, min(int(at_char), max(1, len(text) - 1))) if text else 0
    mid = char_index_to_time(seg, at_char)
    if mid <= start or mid >= end:
        raise ValueError("拆分点必须落在分段内")
    left_text = text[:at_char].rstrip()
    right_text = text[at_char:].lstrip()
    if not left_text or not right_text:
        raise ValueError("拆分后两侧都不能为空")
    left_words, right_words = _split_words(list(seg.get("words") or []), mid)
    left = dict(seg)
    right = dict(seg)
    left["end"] = mid
    left["text"] = left_text
    left["words"] = left_words
    left["locked"] = True
    left.pop("lines", None)
    right["start"] = mid
    right["text"] = right_text
    right["words"] = right_words
    right["locked"] = True
    right.pop("lines", None)
    from cutora.subtitle.seg_ids import new_seg_id
    right["id"] = new_seg_id()
    return left, right


def merge_pair(left: dict, right: dict) -> dict:
    out = dict(left)
    a = str(left.get("text") or "").strip()
    b = str(right.get("text") or "").strip()
    out["text"] = (a + " " + b).strip()
    out["start"] = min(float(left.get("start") or 0), float(right.get("start") or 0))
    out["end"] = max(float(left.get("end") or 0), float(right.get("end") or 0))
    words = list(left.get("words") or []) + list(right.get("words") or [])
    if words:
        out["words"] = [dict(w) if isinstance(w, dict) else w for w in words]
    out["locked"] = True
    out.pop("lines", None)
    return out


def load_glossary_csv(path: str) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    with open(path, encoding="utf-8-sig", newline="") as fh:
        reader = csv.reader(fh)
        for row in reader:
            if not row or all(not str(c).strip() for c in row):
                continue
            src = str(row[0]).strip()
            dst = str(row[1]).strip() if len(row) > 1 else ""
            if src:
                pairs.append((src, dst))
    pairs.sort(key=lambda p: len(p[0]), reverse=True)
    return pairs


def apply_pairs(text: str, pairs: list[tuple[str, str]]) -> str:
    body = str(text or "")
    for src, dst in pairs:
        if src:
            body = body.replace(src, dst)
    return body


def iter_scope(segs: list, selected: set[int] | None):
    for i, raw in enumerate(segs):
        if selected is not None and i not in selected:
            continue
        if isinstance(raw, dict):
            yield i, raw


def set_text_task(task: dict, seg_id: int, text: str) -> dict:
    segs = list(task.get("segments") or [])
    if seg_id < 0 or seg_id >= len(segs) or not isinstance(segs[seg_id], dict):
        raise ValueError("seg_id 越界")
    apply_set_text(segs[seg_id], text)
    set_segments(task, segs)
    return {"seg_id": seg_id, "text": segs[seg_id].get("text") or "", "locked": True}


def merge_task(task: dict, seg_id: int, direction: str = "next") -> dict:
    segs = list(task.get("segments") or [])
    kind = (direction or "next").strip().lower()
    if kind == "prev":
        left_i = seg_id - 1
        right_i = seg_id
    else:
        left_i = seg_id
        right_i = seg_id + 1
    if left_i < 0 or right_i >= len(segs):
        raise ValueError("无法与相邻段合并")
    if not isinstance(segs[left_i], dict) or not isinstance(segs[right_i], dict):
        raise ValueError("无法与相邻段合并")
    merged = merge_pair(segs[left_i], segs[right_i])
    segs[left_i:right_i + 1] = [merged]
    set_segments(task, segs)
    return {"seg_id": left_i, "count": len(segs), "text": merged.get("text") or ""}


def split_task(task: dict, seg_id: int, at_char=None, time=None) -> dict:
    segs = list(task.get("segments") or [])
    if seg_id < 0 or seg_id >= len(segs) or not isinstance(segs[seg_id], dict):
        raise ValueError("seg_id 越界")
    left, right = split_segment(segs[seg_id], at_char=at_char, time=time)
    segs[seg_id:seg_id + 1] = [left, right]
    set_segments(task, segs)
    return {"seg_id": seg_id, "count": len(segs), "time": left.get("end")}


def delete_task(task: dict, seg_id: int) -> dict:
    segs = list(task.get("segments") or [])
    if seg_id < 0 or seg_id >= len(segs):
        raise ValueError("seg_id 越界")
    del segs[seg_id]
    set_segments(task, segs)
    return {"removed": seg_id, "count": len(segs)}


MIN_SEG_DUR = 0.08


def _map_word_times(seg: dict, mapper) -> None:
    words = seg.get("words")
    if not isinstance(words, list):
        return
    for w in words:
        if not isinstance(w, dict):
            continue
        if "start" in w:
            w["start"] = float(mapper(w["start"]))
        if "end" in w:
            w["end"] = float(mapper(w["end"]))


def apply_trim(seg: dict, start: float, end: float) -> dict:
    """Set start/end and linearly map words from the old window to the new one."""
    if not isinstance(seg, dict):
        raise ValueError("seg 不是对象")
    old_s = float(seg.get("start") or 0.0)
    old_e = float(seg.get("end") or 0.0)
    new_s = float(start)
    new_e = float(end)
    if new_e < new_s + MIN_SEG_DUR:
        new_e = new_s + MIN_SEG_DUR
    if new_s < 0.0:
        new_e -= new_s
        new_s = 0.0
    span = old_e - old_s
    if span > 1e-9:
        def mapper(t, _os=old_s, _span=span, _ns=new_s, _ne=new_e):
            u = (float(t) - _os) / _span
            return _ns + u * (_ne - _ns)
        _map_word_times(seg, mapper)
    seg["start"] = new_s
    seg["end"] = new_e
    return seg


def apply_move(seg: dict, start: float) -> dict:
    """Translate a segment; words keep their offset from start."""
    if not isinstance(seg, dict):
        raise ValueError("seg 不是对象")
    old_s = float(seg.get("start") or 0.0)
    old_e = float(seg.get("end") or 0.0)
    dur = max(MIN_SEG_DUR, old_e - old_s)
    new_s = max(0.0, float(start))
    delta = new_s - old_s
    seg["start"] = new_s
    seg["end"] = new_s + dur
    _map_word_times(seg, lambda t, d=delta: float(t) + d)
    return seg


def apply_retime(seg: dict, pivot: float, scale: float) -> dict:
    """Scale times around pivot: t' = pivot + (t - pivot) * scale. Words follow."""
    if not isinstance(seg, dict):
        raise ValueError("seg 不是对象")
    pv = float(pivot)
    sc = float(scale)
    if sc <= 1e-6:
        raise ValueError("scale 必须为正")

    def mapper(t, _pv=pv, _sc=sc):
        return _pv + (float(t) - _pv) * _sc

    old_s = float(seg.get("start") or 0.0)
    old_e = float(seg.get("end") or 0.0)
    new_s, new_e = mapper(old_s), mapper(old_e)
    if new_e < new_s:
        new_s, new_e = new_e, new_s
    if new_e - new_s < MIN_SEG_DUR:
        new_e = new_s + MIN_SEG_DUR
    if new_s < 0.0:
        new_e -= new_s
        new_s = 0.0
    _map_word_times(seg, mapper)
    seg["start"] = new_s
    seg["end"] = new_e
    return seg


def trim_segment_task(task: dict, seg_id: int, start: float, end: float) -> dict:
    segs = list(task.get("segments") or [])
    if seg_id < 0 or seg_id >= len(segs) or not isinstance(segs[seg_id], dict):
        raise ValueError("seg_id 越界")
    apply_trim(segs[seg_id], start, end)
    set_segments(task, segs)
    seg = segs[seg_id]
    return {
        "seg_id": seg_id,
        "start": float(seg["start"]),
        "end": float(seg["end"]),
    }


def move_segments_task(task: dict, items: list) -> dict:
    segs = list(task.get("segments") or [])
    if not isinstance(items, list) or not items:
        raise ValueError("缺少 segments")
    moved = []
    for row in items:
        if not isinstance(row, dict):
            raise ValueError("segments 项必须是对象")
        sid = int(row["seg_id"])
        if sid < 0 or sid >= len(segs) or not isinstance(segs[sid], dict):
            raise ValueError("seg_id 越界")
        apply_move(segs[sid], float(row["start"]))
        moved.append({
            "seg_id": sid,
            "start": float(segs[sid]["start"]),
            "end": float(segs[sid]["end"]),
        })
    set_segments(task, segs)
    return {"moved": moved}


def retime_segments_task(task: dict, seg_ids: list, pivot: float, scale: float) -> dict:
    segs = list(task.get("segments") or [])
    ids = [int(i) for i in (seg_ids or [])]
    if not ids:
        raise ValueError("缺少 seg_ids")
    out = []
    for sid in ids:
        if sid < 0 or sid >= len(segs) or not isinstance(segs[sid], dict):
            raise ValueError("seg_id 越界")
        apply_retime(segs[sid], pivot, scale)
        out.append({
            "seg_id": sid,
            "start": float(segs[sid]["start"]),
            "end": float(segs[sid]["end"]),
        })
    set_segments(task, segs)
    return {"pivot": float(pivot), "scale": float(scale), "segments": out}


def duplicate_segments_task(task: dict, seg_ids=None, items=None, time=0.0) -> dict:
    import copy
    segs = list(task.get("segments") or [])
    payload = []
    if items:
        for row in items:
            if isinstance(row, dict):
                payload.append(copy.deepcopy(row))
    else:
        ids = [int(i) for i in (seg_ids or [])]
        if not ids:
            raise ValueError("缺少 seg_ids")
        for sid in ids:
            if sid < 0 or sid >= len(segs) or not isinstance(segs[sid], dict):
                raise ValueError("seg_id 越界")
            payload.append(copy.deepcopy(segs[sid]))
    if not payload:
        raise ValueError("没有可复制的字幕段")
    t0 = min(float(s.get("start") or 0.0) for s in payload)
    delta = float(time) - t0
    added = []
    from cutora.subtitle.seg_ids import new_seg_id
    for row in payload:
        apply_move(row, max(0.0, float(row.get("start") or 0.0) + delta))
        row["id"] = new_seg_id()
        segs.append(row)
        added.append(len(segs) - 1)
    set_segments(task, segs)
    return {"added": added, "count": len(segs)}


def delete_segments_task(task: dict, seg_ids: list) -> dict:
    segs = list(task.get("segments") or [])
    ids = sorted({int(i) for i in (seg_ids or [])}, reverse=True)
    if not ids:
        raise ValueError("缺少 seg_ids")
    removed = []
    for sid in ids:
        if sid < 0 or sid >= len(segs):
            raise ValueError("seg_id 越界")
        del segs[sid]
        removed.append(sid)
    set_segments(task, segs)
    return {"removed": list(reversed(removed)), "count": len(segs)}


def lock_task(task: dict, seg_ids: list[int], locked: bool) -> dict:
    segs = list(task.get("segments") or [])
    changed = 0
    for i in seg_ids:
        if 0 <= int(i) < len(segs) and isinstance(segs[int(i)], dict):
            segs[int(i)]["locked"] = bool(locked)
            changed += 1
    set_segments(task, segs)
    return {"changed": changed, "locked": bool(locked)}


def find_task(task: dict, query: str, *, regex: bool = False, case: bool = False) -> dict:
    hits = []
    for i, raw in iter_scope(list(task.get("segments") or []), None):
        text = str(raw.get("text") or "")
        spans = find_in_text(text, query, regex=regex, case=case)
        for a, b in spans:
            hits.append({"seg_id": i, "start": a, "end": b, "text": text[a:b]})
    return {"hits": hits, "count": len(hits), "query": query}


def _rewrite_scope(task: dict, selected, fn) -> dict:
    segs = list(task.get("segments") or [])
    changed = 0
    for i, raw in iter_scope(segs, selected):
        old = str(raw.get("text") or "")
        new = fn(old)
        if new != old:
            apply_set_text(raw, new)
            changed += 1
    set_segments(task, segs)
    return {"changed": changed, "count": len(segs)}


def replace_task(
    task: dict, query: str, replacement: str, *, regex=False, case=False, selected=None,
) -> dict:
    def _fn(text):
        return replace_in_text(text, query, replacement, regex=regex, case=case)
    return _rewrite_scope(task, selected, _fn)


def transform_case_task(task: dict, mode: str, selected=None) -> dict:
    return _rewrite_scope(task, selected, lambda t: transform_case(t, mode))


def strip_end_punct_task(task: dict, selected=None) -> dict:
    return _rewrite_scope(task, selected, strip_end_punct)


def apply_glossary_task(task: dict, pairs: list[tuple[str, str]], selected=None) -> dict:
    return _rewrite_scope(task, selected, lambda t: apply_pairs(t, pairs))
