# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Line breaking and reading-speed (CPS) for subtitle segments.

Space-mode two-line split (``_best_two_line_split``) scores each candidate
as ``(bad_end, not_punct, spread)`` and keeps the minimum. Lower is better,
so the live priority is:

1. Avoid hanging articles / prepositions at end of line
   (``resources/data/linebreak_<lang>.json`` ``no_break_after``).
2. Prefer a break after punctuation (``, . ; : — …``).
3. Balance the two line lengths (smallest character-count spread).

Punctuation therefore outranks balance (g09 seg1 is 16+35 after a comma).
Hanging-word avoidance outranks punctuation. This is frozen in
``tests/golden/g09_linebreak_latin``; do not reorder the tuple without a
new stills review.

When no two-line split has both sides ≤ ``max_chars_per_line``, packing
rebalances at word boundaries across ``max_lines`` (T06-2). Leftovers are
**not** dumped on the last line. The renderer then shrinks at most 15%
and ellipsizes if still over-wide. Loop is bounded (``MAX_FIT_ITERS``).

v1 ``draw_subtitle`` with ``max_lines == 2`` leftover-on-last-line is
the g09 golden freeze: many words piled on the last line are **not**
shrunk (byte-identical stills). A single token wider than the line —
v1 and v2 — goes through ``fit_lines_to_width`` (shrink to 50%, then
ellipsis) so the painted bbox stays inside SAFE_MARGIN.

When ``max_lines == 1``, ``break_lines`` returns a single line (it does
not insert a second). Overflow is handled by ``relayout_task``, which
splits the segment on word boundaries.

When ``max_lines > 2``, packing is scored (hanging function words,
overrun, punctuation, overflow) but **never exceeds** ``max_lines``.
A sentence that still does not fit is split by ``relayout_task``, not
by adding lines here.

Orphan protection: if the last line is a single word, borrow one (or two,
to avoid a hanging article/preposition) from the previous line. The last
line may then exceed ``max_chars_per_line``; that is preferred to a
widow. Frozen g09 ``lines`` are untouched. Not applied to the n-line
packer (a short last line after a sentence break is acceptable).
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass

from cutora.core.paths import RESOURCES
from cutora.subtitle.model import Segment, segment_from_dict
from cutora.subtitle.track_source import set_segments

DEFAULT_MAX_CHARS = 42
DEFAULT_MAX_LINES = 2
DEFAULT_CPS = 17.0
DEFAULT_MIN_GAP = 0.08
PUNCT_END = frozenset(",.;:…—،؛؟，。；：！？、")
_TOKEN_RE = re.compile(r"\S+|\s+")
_WORD_STRIP = re.compile(r"^[\"'(\[«“‘]+|[\"')\]»”’.,;:!?…—،؛؟，。；：！？、]+$")
_CJK_CHAR_RE = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uac00-\ud7af]")
_CJK_NO_START = frozenset("，。！？；：、）》」』】”’.,;:!?…—")
_CJK_NO_END = frozenset("（《「『【“‘")
_STOP_CACHE: dict[str, frozenset[str]] = {}


@dataclass
class LayoutRules:
    max_chars_per_line: int = DEFAULT_MAX_CHARS
    max_lines: int = DEFAULT_MAX_LINES
    balance_lines: bool = True
    lang: str | None = None
    line_break: str = "space"
    cps_threshold: float = DEFAULT_CPS
    min_gap: float = DEFAULT_MIN_GAP


def load_stopwords(lang: str | None) -> frozenset[str]:
    code = (lang or "").strip().lower()
    if code in _STOP_CACHE:
        return _STOP_CACHE[code]
    path = os.path.join(RESOURCES, "data", "linebreak_%s.json" % code)
    words: frozenset[str] = frozenset()
    if code and os.path.isfile(path):
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        raw = data.get("no_break_after") or []
        words = frozenset(str(w).strip().lower() for w in raw if str(w).strip())
    _STOP_CACHE[code] = words
    return words


def line_break_mode(lang: str | None) -> str:
    code = (lang or "").strip().lower()
    if not code:
        return "space"
    try:
        from cutora.asr.languages import catalog_by_code
        row = catalog_by_code().get(code) or {}
        script = str(row.get("script") or "").strip().lower()
        if script in ("arabic", "hebrew"):
            return "space"
        mode = str(row.get("line_break") or "space").strip().lower()
        return mode if mode in ("space", "fallback") else "space"
    except Exception:
        return "space"


def cps(segment) -> float:
    """Characters (no newlines) divided by duration in seconds."""
    if isinstance(segment, Segment):
        text = segment.text or ""
        start, end = segment.start, segment.end
    else:
        text = str((segment or {}).get("text") or "")
        start = float(segment.get("start") or 0)
        end = float(segment.get("end") or 0)
    dur = end - start
    if dur <= 0:
        return float("inf") if text else 0.0
    n = len(text.replace("\n", ""))
    return n / dur


def is_over_cps(segment, threshold: float = DEFAULT_CPS) -> bool:
    return cps(segment) > float(threshold)


def is_overlong(
    segment,
    max_chars: int = DEFAULT_MAX_CHARS,
    max_lines: int = DEFAULT_MAX_LINES,
) -> bool:
    """True when the sentence cannot fit max_chars × max_lines (g09 seg3)."""
    if isinstance(segment, Segment):
        text = segment.text or ""
    else:
        text = str((segment or {}).get("text") or "")
    n = len(text.replace("\n", ""))
    cap = max(1, int(max_chars)) * max(1, int(max_lines))
    return n > cap


def layout_caps(task: dict | None) -> tuple[int, int]:
    """Read caps from style.wrap_lines / layout_rules, else Latin defaults."""
    raw = (task or {}).get("layout_rules") if isinstance(task, dict) else None
    rules = raw if isinstance(raw, dict) else {}
    max_c = int(rules.get("max_chars_per_line") or DEFAULT_MAX_CHARS)
    from cutora.subtitle.stylepack import effective_max_lines
    style = (task or {}).get("style") if isinstance(task, dict) else None
    max_l = effective_max_lines(style if isinstance(style, dict) else None, task)
    return max(1, max_c), max(1, max_l)


def check_overlong_task(task: dict) -> list[dict]:
    max_c, max_l = layout_caps(task)
    hits = []
    for i, raw in enumerate(task.get("segments") or []):
        if not isinstance(raw, dict):
            continue
        if not is_overlong(raw, max_c, max_l):
            continue
        text = str(raw.get("text") or "")
        hits.append({
            "seg_id": i,
            "chars": len(text.replace("\n", "")),
            "cap": max_c * max_l,
            "max_chars_per_line": max_c,
            "max_lines": max_l,
            "start": raw.get("start"),
            "end": raw.get("end"),
            "text": text,
            "locked": bool(raw.get("locked")),
        })
    return hits


def _bare(token: str) -> str:
    return _WORD_STRIP.sub("", token).lower()


def _prefer_after(token: str) -> bool:
    stripped = token.rstrip()
    if not stripped:
        return False
    if stripped.endswith("..."):
        return True
    return stripped[-1] in PUNCT_END


def _tokens(text: str) -> list[str]:
    return [t for t in _TOKEN_RE.findall(text or "") if not t.isspace()]


def _fits(parts: list[str], max_chars: int) -> bool:
    return len(" ".join(parts)) <= max_chars


def _bad_end(token: str, stops: frozenset[str]) -> bool:
    return _bare(token) in stops


def _n_line_score(lines: list[str], max_c: int, stops: frozenset[str]) -> tuple:
    """Lower is better. Slack lets 'at once.' stay together (21 vs cap 19)."""
    slack = max(6, max_c // 4)
    hang = big = not_punct = small = bad_start = 0
    lengths: list[int] = []
    n = len(lines)
    for i, line in enumerate(lines):
        toks = str(line).split()
        length = len(line)
        lengths.append(length)
        last = i == n - 1
        if not last and toks and _bad_end(toks[-1], stops):
            hang += 1
        if not last and toks and not _prefer_after(toks[-1]):
            not_punct += 1
        if i and toks and _bare(toks[0]) in stops:
            bad_start += 1
        if length > max_c + slack:
            big += 1
        small += max(0, length - max_c)
    spread = (max(lengths) - min(lengths)) if lengths else 0
    return (hang, big, not_punct, small, bad_start, spread, n)


def _best_n_line_pack(
    tokens: list[str], max_c: int, max_l: int, stops: frozenset[str],
) -> list[str]:
    """Search 1..max_l breaks. ``max_lines == 2`` does not call this (g09)."""
    ntok = len(tokens)
    if not tokens:
        return []
    max_l = max(1, min(int(max_l), ntok))
    total = len(" ".join(tokens))
    min_n = min(max_l, max(1, (total + max_c - 1) // max_c))
    hi = min(max_l, min_n + 1)
    if ntok > 18:
        return _greedy_pack(tokens, max_c, max_l, stops)
    from itertools import combinations
    best: list[str] | None = None
    best_score = None
    for n in range(min_n, hi + 1):
        if n == 1:
            cand = [" ".join(tokens)]
            score = _n_line_score(cand, max_c, stops)
            if best_score is None or score < best_score:
                best_score, best = score, cand
            continue
        for br in combinations(range(1, ntok), n - 1):
            pts = (0,) + br + (ntok,)
            cand = [" ".join(tokens[pts[i]:pts[i + 1]]) for i in range(n)]
            score = _n_line_score(cand, max_c, stops)
            if best_score is None or score < best_score:
                best_score, best = score, cand
    return best or [" ".join(tokens)]


def _split_space(tokens: list[str], rules: LayoutRules, stops: frozenset[str]) -> list[str]:
    max_c = max(1, int(rules.max_chars_per_line))
    max_l = max(1, int(rules.max_lines))
    if not tokens:
        return []
    if max_l == 1 or len(" ".join(tokens)) <= max_c:
        return [" ".join(tokens)]
    if max_l == 2 and rules.balance_lines:
        balanced = _best_two_line_split(tokens, max_c, stops)
        if balanced:
            return _fix_orphan_lines(balanced, max_c, stops)
        return _fix_orphan_lines(
            _overflow_two_line_pack(tokens, max_c, stops), max_c, stops)
    packed = _best_n_line_pack(tokens, max_c, max_l, stops)
    if packed:
        return packed
    return _fix_orphan_lines(_greedy_pack(tokens, max_c, max_l, stops), max_c, stops)


def _fix_orphan_lines(lines: list[str], max_c: int, stops: frozenset[str]) -> list[str]:
    """If the last line is a single word, borrow from the line above."""
    if len(lines) < 2:
        return lines
    last = str(lines[-1] or "").split()
    prev = str(lines[-2] or "").split()
    if len(last) != 1 or len(prev) < 2:
        return lines
    n_borrow = 1
    if len(prev) > 2 and _bare(prev[-2]) in stops:
        n_borrow = 2
    if len(prev) <= n_borrow:
        n_borrow = 1
    keep = prev[:-n_borrow]
    borrowed = prev[-n_borrow:]
    if not keep:
        return lines
    if _bare(keep[-1]) in stops and len(keep) > 1:
        borrowed = [keep[-1]] + borrowed
        keep = keep[:-1]
    if not keep:
        return lines
    new_last = " ".join(borrowed + last)
    new_prev = " ".join(keep)
    return list(lines[:-2]) + [new_prev, new_last]


def _best_two_line_split(tokens: list[str], max_c: int, stops: frozenset[str]) -> list[str] | None:
    best = None
    best_score = None
    for i in range(1, len(tokens)):
        left, right = tokens[:i], tokens[i:]
        if not _fits(left, max_c) or not _fits(right, max_c):
            continue
        # Tuple order is the priority: hanging words, then punct, then balance.
        bad = 1 if _bad_end(left[-1], stops) else 0
        punct = 0 if _prefer_after(left[-1]) else 1
        spread = abs(len(" ".join(left)) - len(" ".join(right)))
        score = (bad, punct, spread)
        if best_score is None or score < best_score:
            best_score = score
            best = [" ".join(left), " ".join(right)]
    return best


def _overflow_two_line_pack(
    tokens: list[str], max_c: int, stops: frozenset[str],
) -> list[str]:
    """Word-boundary split when nothing fits in two *max_c* lines.

    Does not dump leftovers onto the last line. Minimizes (max line
    length, hanging function word, not-punct, spread).
    """
    ntok = len(tokens)
    if ntok <= 1:
        return [" ".join(tokens)] if tokens else []
    best = None
    best_score = None
    for i in range(1, ntok):
        left, right = tokens[:i], tokens[i:]
        ll = len(" ".join(left))
        lr = len(" ".join(right))
        hang = 1 if _bad_end(left[-1], stops) else 0
        punct = 0 if _prefer_after(left[-1]) else 1
        spread = abs(ll - lr)
        over_l = 0 if ll <= max_c else 1
        over_r = 0 if lr <= max_c else 1
        score = (over_l, over_r, max(ll, lr), hang, punct, spread)
        if best_score is None or score < best_score:
            best_score = score
            best = [" ".join(left), " ".join(right)]
    return best or [" ".join(tokens)]


MAX_FIT_ITERS = 8
FIT_SCALE_FLOOR = 0.85


def _greedy_pack(tokens: list[str], max_c: int, max_l: int, stops: frozenset[str]) -> list[str]:
    lines: list[list[str]] = [[]]
    for tok in tokens:
        cur = lines[-1]
        trial = cur + [tok]
        if cur and not _fits(trial, max_c):
            if len(lines) < max_l:
                if cur and _bad_end(cur[-1], stops) and len(cur) > 1:
                    moved = cur.pop()
                    lines.append([moved, tok])
                else:
                    lines.append([tok])
            else:
                cur.append(tok)
        else:
            cur.append(tok)
    return [" ".join(part) for part in lines if part]


def _cjk_atoms(text: str) -> list[str]:
    """Split CJK text into kinsoku-safe atoms (keeping closing punct attached)."""
    body = (text or "").replace("\n", "").strip()
    if not body:
        return []
    atoms: list[str] = []
    i = 0
    n = len(body)
    while i < n:
        ch = body[i]
        if ch.isspace():
            i += 1
            continue
        atom = ""
        while i < n and body[i] in _CJK_NO_END:
            atom += body[i]
            i += 1
        if i < n:
            if body[i].isascii() and (body[i].isalnum() or body[i] in "-'%$"):
                while i < n and body[i].isascii() and not body[i].isspace() and body[i] not in _CJK_NO_START:
                    atom += body[i]
                    i += 1
            else:
                atom += body[i]
                i += 1
        while i < n and body[i] in _CJK_NO_START:
            atom += body[i]
            i += 1
        if atom:
            atoms.append(atom)
    return atoms


def _split_cjk(text: str, rules: LayoutRules) -> list[str]:
    """Clause-aware and kinsoku-safe CJK line breaking."""
    max_c = max(1, int(rules.max_chars_per_line))
    max_l = max(1, int(rules.max_lines))
    body = (text or "").replace("\n", "").strip()
    if not body:
        return []
    if max_l == 1 or len(body) <= max_c:
        return [body]
    atoms = _cjk_atoms(body)
    if len(atoms) <= 1:
        return [body]
    if max_l == 2 and rules.balance_lines:
        best = None
        best_score = None
        for i in range(1, len(atoms)):
            left = "".join(atoms[:i])
            right = "".join(atoms[i:])
            over_l = 0 if len(left) <= max_c else 1
            over_r = 0 if len(right) <= max_c else 1
            punct = 0 if left[-1] in PUNCT_END else 1
            spread = abs(len(left) - len(right))
            score = (over_l + over_r, max(len(left), len(right)) if (over_l or over_r) else 0, punct, spread)
            if best_score is None or score < best_score:
                best_score = score
                best = [left, right]
        if best:
            return best
    lines: list[str] = [""]
    for atom in atoms:
        if lines[-1] and len(lines[-1]) + len(atom) > max_c and len(lines) < max_l:
            lines.append(atom)
        else:
            lines[-1] += atom
    return [ln for ln in lines if ln]


def _split_fallback(text: str, rules: LayoutRules) -> list[str]:
    max_c = max(1, int(rules.max_chars_per_line))
    max_l = max(1, int(rules.max_lines))
    body = (text or "").replace("\n", "")
    if not body:
        return []
    if _CJK_CHAR_RE.search(body):
        return _split_cjk(body, rules)
    chunks = [body[i:i + max_c] for i in range(0, len(body), max_c)]
    if len(chunks) <= max_l:
        return chunks
    head, tail = chunks[: max_l - 1], "".join(chunks[max_l - 1:])
    return head + [tail]


def word_indices_for_lines(lines: list[str], words: list) -> list[list[int]]:
    """Map pre-broken lines onto a flat word list (karaoke indices)."""
    cursor = 0
    n_words = len(words or [])
    out: list[list[int]] = []
    for line in lines:
        n = len(str(line).replace("…", " ").split())
        end = min(n_words, cursor + n)
        out.append(list(range(cursor, end)))
        cursor = end
    return out


def break_lines(segment, rules: LayoutRules | None = None) -> list[str]:
    """Return display lines. ``fallback`` languages are hard-cut (approximate)."""
    rules = rules or LayoutRules()
    if isinstance(segment, Segment):
        text = segment.text or ""
        lang = rules.lang or segment.lang
    else:
        text = str((segment or {}).get("text") or "")
        lang = rules.lang or (segment or {}).get("lang")
    text = " ".join(text.split())
    code = (lang or "").strip().lower()
    mode = (rules.line_break or line_break_mode(lang)).strip().lower()
    if code in ("zh", "zh-cn", "zh-tw", "yue") or (_CJK_CHAR_RE.search(text) and " " not in text.strip()):
        return _split_cjk(text, rules)
    if mode == "fallback":
        return _split_fallback(text, rules)
    tokens = _tokens(text)
    stops = load_stopwords(lang)
    return _split_space(tokens, rules, stops)


def _word_items_for_split(raw: dict) -> list[dict]:
    words = raw.get("words") or []
    items = []
    t0 = float(raw.get("start") or 0)
    t1 = float(raw.get("end") or t0)
    for w in words:
        if not isinstance(w, dict):
            continue
        tok = str(w.get("word") or w.get("text") or "").strip()
        if not tok:
            continue
        ws = w.get("start")
        we = w.get("end")
        items.append({
            "word": tok,
            "start": float(ws if ws is not None else t0),
            "end": float(we if we is not None else t1),
            "raw": dict(w),
        })
    if items:
        return items
    toks = str(raw.get("text") or "").split()
    n = max(1, sum(len(t) for t in toks))
    dur = max(0.0, t1 - t0)
    t = t0
    out = []
    for tok in toks:
        te = t + dur * (len(tok) / n)
        out.append({"word": tok, "start": t, "end": te, "raw": {
            "word": tok, "start": t, "end": te,
        }})
        t = te
    if out:
        out[-1]["end"] = t1
        out[-1]["raw"]["end"] = t1
    return out


def _seg_from_word_group(base: dict, group: list[dict], *, first: bool = False) -> dict:
    new = dict(base)
    if not first:
        # Q139：切出来的每一条都要有自己的 id。以前全部继承母句的 id，而字幕
        # 光栅缓存按 id 命中 —— 「Bible」那一格画出了「Does」（同 id、框一样大）。
        from cutora.subtitle.seg_ids import new_seg_id
        new["id"] = new_seg_id()
    new["text"] = " ".join(w["word"] for w in group)
    new["start"] = float(group[0]["start"])
    new["end"] = float(group[-1]["end"])
    new["words"] = [dict(w["raw"]) for w in group]
    new.pop("layout_warn", None)
    new.pop("_fit_warn", None)
    new.pop("lines", None)
    return new


LAYOUT_ENGINE_SMART = 2


def smart_split_enabled(style: dict | None) -> bool:
    """Q137：样式写了 layout_engine=2 才走智能切分。老工程 / golden 没这个键，
    仍是「装到装不下就切」。按「重新断句」、拖字幕宽度、动新排版控件时写入。"""
    try:
        return int((style or {}).get("layout_engine") or 0) >= LAYOUT_ENGINE_SMART
    except (TypeError, ValueError):
        return False


def _pixel_groups_for_seg(raw: dict, task: dict, rules: LayoutRules) -> list[list[dict]] | None:
    """None → caller keeps the segment (no QApp / measure failed)."""
    from cutora.subtitle.stylepack import effective_max_lines, is_single_word_mode
    try:
        from PySide6.QtWidgets import QApplication
        if QApplication.instance() is None:
            return None
    except Exception:
        return None
    style = task.get("style") if isinstance(task.get("style"), dict) else {}
    items = _word_items_for_split(raw)
    if not items:
        return []
    if is_single_word_mode(style):
        return [[it] for it in items]
    if smart_split_enabled(style):
        # Q137：切口看标点 / 数字 / 大写 / 连词，量宽用渲染同款可用宽度
        from cutora.subtitle.reflow import group_words
        try:
            max_l = max(1, int(rules.max_lines or effective_max_lines(style, task)))
            return group_words(items, style, task, lang=rules.lang, max_lines=max_l,
                               merge_runs=False) or [items]
        except Exception:
            return None
    try:
        max_l = max(1, int(rules.max_lines or effective_max_lines(style, task)))
        from cutora.subtitle.io import canvas_size
        from cutora.render.textfx.spec import expansion_px, from_style
        from cutora.render.textfx.layout import _pixel_wrap_lines
        cw, _ch = canvas_size(task)
        em = float(style.get("fontsize") or 34)
        spec = from_style(style)
        extra = expansion_px(spec, em) + float(style.get("box_padding_x") or 0)
        family = spec.fonts.body or str(style.get("font_family") or "Arimo")
        bold = bool(style.get("bold", True))
        italic = bool(style.get("italic", False))
        groups: list[list[dict]] = []
        cur: list[dict] = []
        for it in items:
            trial = cur + [it]
            text = " ".join(x["word"] for x in trial)
            nline = 1
            if cur:
                parts = _pixel_wrap_lines(
                    [text], family, em, spec, cw, bold, italic,
                    extra_pad=extra, lang=rules.lang, max_lines=None,
                    style=style)
                nline = max(1, len(parts))
            if cur and nline > max_l:
                groups.append(cur)
                cur = [it]
            else:
                cur = trial
        if cur:
            groups.append(cur)
        return groups or [items]
    except Exception:
        return None


def _maybe_split_segments(task: dict, segs: list, rules: LayoutRules, selected):
    out: list = []
    touched: list = []
    for i, raw in enumerate(segs):
        if not isinstance(raw, dict) or raw.get("locked"):
            out.append(raw)
            continue
        if selected is not None and i not in selected:
            out.append(raw)
            continue
        groups = _pixel_groups_for_seg(raw, task, rules)
        if not groups or len(groups) == 1:
            out.append(raw)
            touched.append(raw)
            continue
        t0 = float(raw.get("start") or 0)
        t1 = float(raw.get("end") or t0)
        parts = [_seg_from_word_group(raw, g, first=(k == 0))
                 for k, g in enumerate(groups) if g]
        if parts:
            parts[0]["start"] = t0
            parts[-1]["end"] = t1
            for a, b in zip(parts, parts[1:]):
                mid = float(b["start"])
                a["end"] = mid
        chunk = parts or [raw]
        out.extend(chunk)
        touched.extend(chunk)
    return out, touched


def _pixel_cap_lines(raw: dict, lines: list[str], task: dict, local: LayoutRules) -> list[str]:
    cap = max(1, int(local.max_lines))
    out = list(lines or [])[:cap]
    try:
        from PySide6.QtWidgets import QApplication
        if QApplication.instance() is None:
            return out or [str(raw.get("text") or "")]
        style = task.get("style") if isinstance(task.get("style"), dict) else {}
        from cutora.render.textfx.layout import _pixel_wrap_lines
        from cutora.render.textfx.spec import expansion_px, from_style
        from cutora.subtitle.io import canvas_size
        spec = from_style(style)
        em = float(style.get("fontsize") or 34)
        extra = expansion_px(spec, em) + float(style.get("box_padding_x") or 0)
        cw, _ch = canvas_size(task)
        family = spec.fonts.body or str(style.get("font_family") or "Arimo")
        text = str(raw.get("text") or " ".join(out) or "")
        wrapped = _pixel_wrap_lines(
            [text] if text else out,
            family, em, spec, cw,
            bool(style.get("bold", True)), bool(style.get("italic", False)),
            extra_pad=extra, lang=local.lang, max_lines=cap, style=style)
        return (wrapped or out)[:cap]
    except Exception:
        return out or [str(raw.get("text") or "")]


def relayout_task(task: dict, rules: LayoutRules | None = None, *, selected=None) -> dict:
    """Re-break and re-split so every segment fits ``max_lines``. Skip locked."""
    rules = _rules_from_task(task, rules)
    segs, touched = _maybe_split_segments(
        task, list(task.get("segments") or []), rules, selected)
    changed = 0
    approx = 0
    write = set(id(x) for x in touched) if selected is not None else None
    for raw in segs:
        if not isinstance(raw, dict) or raw.get("locked"):
            continue
        if write is not None and id(raw) not in write:
            continue
        lang = rules.lang or raw.get("lang")
        mode = (rules.line_break or line_break_mode(lang)).strip().lower()
        local = LayoutRules(
            max_chars_per_line=rules.max_chars_per_line,
            max_lines=rules.max_lines,
            balance_lines=rules.balance_lines,
            lang=lang,
            line_break=mode,
            cps_threshold=rules.cps_threshold,
            min_gap=rules.min_gap,
        )
        lines = break_lines(raw, local)
        raw["lines"] = _pixel_cap_lines(raw, lines, task, local)
        raw.pop("layout_warn", None)
        raw.pop("_fit_warn", None)
        if mode == "fallback":
            raw["lines_approx"] = True
            approx += 1
        else:
            raw.pop("lines_approx", None)
        changed += 1
    set_segments(task, segs)
    return {"changed": changed, "approx": approx, "count": len(segs)}


AUTO_RELAYOUT_KEY = "subtitle.auto_relayout"
RELAYOUT_PARAM_KEYS = frozenset({
    "fontsize", "wrap_lines", "wrap_width_percent", "wrap_bounds",
    "font_family", "bold", "italic",
    "box_padding_x", "line_spacing", "tracking_em", "letter_case",
    "border_width", "word_mode",
    # Q137
    "safe_margin_pct", "max_words_per_line", "pause_break_sec",
})
_AUTO_RELAYOUT_CALLS = 0


def auto_relayout_call_count() -> int:
    return int(_AUTO_RELAYOUT_CALLS)


def reset_auto_relayout_calls() -> None:
    global _AUTO_RELAYOUT_CALLS
    _AUTO_RELAYOUT_CALLS = 0


def maybe_auto_relayout(task: dict | None, key=None, settings=None) -> dict | None:
    """Run ``relayout_task`` when auto-relayout is on and *key* is a layout param."""
    global _AUTO_RELAYOUT_CALLS
    if not isinstance(task, dict):
        return None
    if key not in RELAYOUT_PARAM_KEYS:
        return None
    if not auto_relayout_enabled(settings):
        return None
    _AUTO_RELAYOUT_CALLS += 1
    return relayout_task(task)


def auto_relayout_enabled(settings: dict | None = None) -> bool:
    """Default ON when the key is missing (new installs / golden fixture)."""
    data = settings
    if data is None:
        from cutora.core.history import _load_app_settings
        data = _load_app_settings() or {}
    if AUTO_RELAYOUT_KEY not in data:
        return True
    return bool(data.get(AUTO_RELAYOUT_KEY))


def _seg_fingerprint(raw: dict) -> tuple:
    return (
        round(float(raw.get("start") or 0), 3),
        round(float(raw.get("end") or 0), 3),
        str(raw.get("text") or ""),
    )


def fingerprints(segments) -> set:
    out = set()
    for raw in segments or []:
        if isinstance(raw, dict):
            out.add(_seg_fingerprint(raw))
    return out


def _rules_from_task(task: dict, rules: LayoutRules | None = None) -> LayoutRules:
    raw = task.get("layout_rules") if isinstance(task.get("layout_rules"), dict) else {}
    style = task.get("style") if isinstance(task.get("style"), dict) else {}
    from cutora.subtitle.stylepack import effective_max_lines
    wrap = style.get("wrap_lines") if isinstance(style, dict) else None
    if rules is not None:
        if wrap is None:
            return rules
        max_l = effective_max_lines(style, task)
        if int(rules.max_lines) == int(max_l):
            return rules
        return LayoutRules(
            max_chars_per_line=rules.max_chars_per_line,
            max_lines=max_l,
            balance_lines=rules.balance_lines,
            lang=rules.lang,
            line_break=rules.line_break,
            cps_threshold=rules.cps_threshold,
            min_gap=rules.min_gap,
        )
    return LayoutRules(
        max_chars_per_line=int(raw.get("max_chars_per_line") or DEFAULT_MAX_CHARS),
        max_lines=effective_max_lines(style, task),
        balance_lines=bool(raw.get("balance_lines", True)),
        lang=raw.get("lang"),
        line_break=str(raw.get("line_break") or "space"),
    )


def relayout_new_segments(task: dict, previous: set, rules: LayoutRules | None = None) -> dict:
    """Write ``lines`` only onto unlocked segments that were not in ``previous``."""
    rules = _rules_from_task(task, rules)
    segs = list(task.get("segments") or [])
    selected = set()
    for i, raw in enumerate(segs):
        if not isinstance(raw, dict):
            continue
        if _seg_fingerprint(raw) in previous:
            continue
        selected.add(i)
    if not selected:
        return {"changed": 0, "approx": 0, "count": len(segs), "new": 0}
    out = relayout_task(task, rules, selected=selected)
    out["new"] = len(selected)
    return out


def line_preview(seg: dict, rules: LayoutRules | None = None) -> str:
    """Human text of how this sentence will wrap (for hover)."""
    lines = seg.get("lines") if isinstance(seg.get("lines"), list) else None
    if not lines:
        lines = break_lines(seg, rules)
    parts = ["%s（%d）" % (ln, len(ln)) for ln in lines]
    return "将断成 %d 行：\n%s" % (len(lines), "\n".join(parts))


def check_cps_task(task: dict, threshold: float = DEFAULT_CPS) -> list[dict]:
    hits = []
    for i, raw in enumerate(task.get("segments") or []):
        if not isinstance(raw, dict):
            continue
        value = cps(raw)
        if value > float(threshold):
            hits.append({
                "seg_id": i,
                "cps": round(value, 3),
                "threshold": float(threshold),
                "start": raw.get("start"),
                "end": raw.get("end"),
                "text": raw.get("text") or "",
                "locked": bool(raw.get("locked")),
            })
    return hits


def extend_durations_task(
    task: dict,
    threshold: float = DEFAULT_CPS,
    min_gap: float = DEFAULT_MIN_GAP,
) -> dict:
    """Lengthen over-CPS segments without overlapping the next (keep min_gap)."""
    segs = list(task.get("segments") or [])
    changed = 0
    skipped_locked = 0
    for i, raw in enumerate(segs):
        if not isinstance(raw, dict):
            continue
        if raw.get("locked"):
            if is_over_cps(raw, threshold):
                skipped_locked += 1
            continue
        if not is_over_cps(raw, threshold):
            continue
        text = str(raw.get("text") or "").replace("\n", "")
        start = float(raw.get("start") or 0)
        end = float(raw.get("end") or 0)
        need = (len(text) / float(threshold)) if threshold > 0 else end - start
        new_end = start + need
        if i + 1 < len(segs) and isinstance(segs[i + 1], dict):
            nxt = float(segs[i + 1].get("start") or 0)
            new_end = min(new_end, nxt - float(min_gap))
        if new_end > end + 1e-9:
            raw["end"] = new_end
            changed += 1
    set_segments(task, segs)
    return {"changed": changed, "skipped_locked": skipped_locked}


_ELLIPSIS = "…"


def _truncate_token_to_width(token: str, fm, max_w: float) -> str:
    if max_w <= 0:
        return ""
    if float(fm.horizontalAdvance(token)) <= max_w:
        return token
    lo, hi = 0, len(token)
    best = _ELLIPSIS
    while lo <= hi:
        mid = (lo + hi) // 2
        cand = token[:mid] + _ELLIPSIS
        if float(fm.horizontalAdvance(cand)) <= max_w:
            best = cand
            lo = mid + 1
        else:
            hi = mid - 1
    if float(fm.horizontalAdvance(best)) <= max_w:
        return best
    if float(fm.horizontalAdvance(_ELLIPSIS)) <= max_w:
        return _ELLIPSIS
    return ""


def _truncate_overwide_line(line: str, fm, max_w: float) -> str:
    if float(fm.horizontalAdvance(line)) <= max_w:
        return line
    words = str(line).split()
    if len(words) <= 1:
        return _truncate_token_to_width(words[0] if words else line, fm, max_w)
    while words:
        cand = " ".join(words) + _ELLIPSIS
        if float(fm.horizontalAdvance(cand)) <= max_w:
            return cand
        words = words[:-1]
    return _truncate_token_to_width(str(line).split()[0], fm, max_w)


def pack_tokens_to_width(
    tokens: list[str], max_l: int, fm, cap: float, stops: frozenset[str] | None = None,
) -> list[str]:
    """Word-boundary pack minimizing max(rendered line width)."""
    if not tokens:
        return []
    max_l = max(1, int(max_l))
    if max_l == 1 or len(tokens) == 1:
        return [" ".join(tokens)]
    stops = stops or frozenset()
    ntok = len(tokens)
    if max_l == 2:
        best = None
        best_score = None
        for i in range(1, ntok):
            left, right = " ".join(tokens[:i]), " ".join(tokens[i:])
            w0 = float(fm.horizontalAdvance(left))
            w1 = float(fm.horizontalAdvance(right))
            hang = 1 if _bad_end(tokens[i - 1], stops) else 0
            punct = 0 if _prefer_after(tokens[i - 1]) else 1
            score = (max(w0, w1), abs(w0 - w1), hang, punct)
            if best_score is None or score < best_score:
                best_score = score
                best = [left, right]
        return best or [" ".join(tokens)]
    return _greedy_pack(tokens, max(1, int(cap / 8)), max_l, stops)


def _all_fit(lines: list[str], fm, cap: float) -> bool:
    return all(float(fm.horizontalAdvance(ln)) <= cap + 0.5 for ln in (lines or [""]))


def prefix_fill_ellipsis(
    tokens: list[str], max_l: int, fm, cap: float,
) -> list[str]:
    """Keep reading order. Fill up to max_l lines at ``cap``; leftover → last line + …"""
    if not tokens:
        return []
    max_l = max(1, int(max_l))
    cap = float(cap)
    i = 0
    n = len(tokens)
    out: list[str] = []
    for li in range(max_l):
        last = li == max_l - 1
        cur: list[str] = []
        while i < n:
            trial = cur + [tokens[i]]
            body = " ".join(trial)
            more = (i + 1 < n)
            probe = (body + _ELLIPSIS) if (last and more) else body
            adv = float(fm.horizontalAdvance(probe))
            if cur and adv > cap:
                break
            if not cur and adv > cap:
                if last:
                    keep = _truncate_token_to_width(
                        tokens[i] + (_ELLIPSIS if more else ""), fm, cap)
                    out.append(keep)
                    i = n
                    return [ln for ln in out if ln]
                break
            cur.append(tokens[i])
            i += 1
        if last and i < n:
            cand = (" ".join(cur) + _ELLIPSIS) if cur else _ELLIPSIS
            while cur and float(fm.horizontalAdvance(cand)) > cap:
                cur.pop()
                cand = (" ".join(cur) + _ELLIPSIS) if cur else _ELLIPSIS
            out.append(cand)
            i = n
        else:
            if cur:
                out.append(" ".join(cur))
        if i >= n:
            break
    if i < n and out:
        last = out[-1]
        if not last.endswith(_ELLIPSIS):
            cand = last + _ELLIPSIS
            if float(fm.horizontalAdvance(cand)) <= cap:
                out[-1] = cand
            else:
                words = last.split()
                while words:
                    cand = " ".join(words) + _ELLIPSIS
                    if float(fm.horizontalAdvance(cand)) <= cap:
                        out[-1] = cand
                        break
                    words.pop()
    return [ln for ln in out if ln]


def reflow_and_fit(
    text: str, lines: list[str], lang: str | None, rules: LayoutRules,
    fm_factory, max_w: float, *, floor: float = FIT_SCALE_FLOOR,
    max_iters: int = MAX_FIT_ITERS,
) -> tuple[list[str], float, str | None, int]:
    """Width-pack all tokens, shrink to floor, then prefix-fill + visible …"""
    src_text = str(text or "").strip()
    tokens = [t for t in src_text.split() if t] or [
        t for t in " ".join(lines or []).split() if t]
    cur = [str(x) for x in (lines or [])] or ([src_text] if src_text else [""])
    scale = 1.0
    warn = None
    cap = float(max_w)
    max_l = max(1, int(rules.max_lines or 2))
    stops = load_stopwords(lang or rules.lang)
    prev: tuple | None = None
    for iters in range(1, max(1, int(max_iters)) + 1):
        fm = fm_factory(scale)
        packed = pack_tokens_to_width(tokens, max_l, fm, cap, stops) if tokens else cur
        if packed:
            cur = packed
        widths = [float(fm.horizontalAdvance(ln)) for ln in cur] or [0.0]
        widest = max(widths)
        state = (len(cur), round(scale, 4), round(widest, 1))
        if prev == state:
            cur = prefix_fill_ellipsis(tokens, max_l, fm, cap)
            warn = "truncate"
            return cur, float(scale), warn, iters
        prev = state
        if len(cur) <= max_l and _all_fit(cur, fm, cap):
            return cur, float(scale), warn, iters
        if scale > float(floor) + 1e-6:
            nxt = max(float(floor), scale * cap / max(widest, 1.0))
            if nxt >= scale - 1e-6:
                nxt = max(float(floor), scale - 0.02)
            scale = float(floor) if nxt <= float(floor) + 1e-6 else nxt
            warn = "shrink"
            continue
        cur = prefix_fill_ellipsis(tokens, max_l, fm_factory(scale), cap)
        warn = "truncate"
        return cur, float(scale), warn, iters
    fm = fm_factory(scale)
    cur = prefix_fill_ellipsis(tokens, max_l, fm, cap)
    return cur, float(scale), warn or "truncate", max(1, int(max_iters))


def fit_lines_to_width(lines, fm_factory, max_w, *, floor=0.5):
    """Shrink then truncate so each line's advance is ≤ max_w.

    ``fm_factory(em_scale)`` returns an object with ``horizontalAdvance``.
    Scale 1.0 is the caller's current size. Returns
    ``(lines, em_scale, warn)`` with warn in {None, "shrink", "truncate"}.
    If every line already fits, lines and scale are unchanged (warn None).
    """
    src = [str(x) for x in (lines or [""])] or [""]
    cap = float(max_w)
    lo = float(floor)
    fm = fm_factory(1.0)
    widest = max((float(fm.horizontalAdvance(ln)) for ln in src), default=0.0)
    if widest <= cap:
        return src, 1.0, None
    scale = max(lo, cap / max(widest, 1.0))
    while True:
        fm = fm_factory(scale)
        widest = max((float(fm.horizontalAdvance(ln)) for ln in src), default=0.0)
        if widest <= cap:
            return list(src), float(scale), "shrink"
        if scale <= lo + 1e-6:
            break
        scale = max(lo, scale - 0.02)
    out = [_truncate_overwide_line(ln, fm, cap) for ln in src]
    return out, lo, "truncate"
