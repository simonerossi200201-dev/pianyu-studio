# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q137 · 字幕断句：把词流切成一条条字幕，每条尽量填满，切口尽量落在意群边界。

以前的两条路都只会「装到装不下就切」：
- 重新断句（relayout_task）只在一条字幕内部切，ASR 给的短句永远合不回来；
- 老的 resegment_words_by_style 会跨句重排，但停顿 0.45 s 就断、切口不看
  标点 / 连词，最后一条常常只剩一两个词。

这里是一套：

1. 词流按「硬边界」分成若干段：句末标点（. ! ?）、停顿 ≥ pause_break_sec、
   角色变化、锁定 / 手改过的字幕（原样保留）。
2. 每一段用动态规划切成若干条，条数越少越好（= 每条越满），每个切口按
   优先级计分：标点后 < 数字前 < 大写词前 < 连词 / 介词前 < 任意位置；
   切在冠词 / 介词后面（"the |"）重罚，末条只剩一个词也罚。
3. 一条装不装得下，用和渲染完全一样的像素折行来量（同字体、同可用宽度：
   min(字幕宽度范围, 1 − 2×安全边距) − 描边等外扩），行数 ≤ 字幕行数，
   词数 ≤ 行数 × 每行最多词数（0 = 不限）。

单字蹦（word_mode=single）时每个词一条，不走 DP。
"""
from __future__ import annotations

import re
from typing import Callable

from cutora.subtitle.stopwords import fold_word, strip_punct

PAUSE_BREAK_DEFAULT = 0.6
SAFE_MARGIN_DEFAULT_PCT = 8.0
_SENT_END = re.compile(r"[.!?。！？…]+[\"'”’)\]]*$")
_SOFT_END = re.compile(r"[,;:，；：、]+[\"'”’)\]]*$")
_NUM = re.compile(r"^[\"'“‘(\[]*[$€£¥]?\d")
_CONJ = frozenset({
    "and", "or", "but", "so", "yet", "nor", "because", "although", "though",
    "while", "when", "whenever", "where", "wherever", "if", "unless", "until",
    "since", "as", "than", "that", "which", "who", "whom", "whose", "whether",
    "after", "before", "once", "then", "therefore", "however",
    "in", "on", "at", "to", "of", "for", "from", "with", "by", "into", "onto",
    "upon", "over", "under", "about", "through", "without", "within", "toward",
    "towards", "against", "between", "among", "during", "like",
    # it / es（同一套优先级，词表够用就行）
    "e", "ed", "ma", "però", "perché", "quando", "mentre", "se", "che", "chi",
    "di", "a", "da", "in", "con", "su", "per", "tra", "fra",
    "y", "o", "pero", "porque", "cuando", "mientras", "si", "que", "quien",
    "de", "en", "con", "por", "para", "sin", "sobre", "entre", "hasta",
})
_NO_END = frozenset({
    "a", "an", "the", "of", "to", "in", "on", "at", "for", "with", "by", "from",
    "and", "or", "but", "so", "nor", "yet", "as", "into", "onto", "upon",
    "is", "are", "was", "were", "be", "been", "am", "do", "does", "did",
    "my", "your", "his", "her", "its", "our", "their", "this", "that", "these",
    "those", "il", "lo", "la", "i", "gli", "le", "un", "una", "uno", "di", "da",
    "del", "della", "dei", "delle", "el", "los", "las", "una", "unos", "unas",
    "de", "del", "al",
})

# 切口分值：越小越好
COST_AFTER_PUNCT = 0.0
COST_BEFORE_NUMBER = 1.5
COST_BEFORE_CAPITAL = 2.0
COST_BEFORE_CONJ = 3.0
COST_ANYWHERE = 6.0
COST_AFTER_NO_END = 6.0      # "the |", "of |" —— 挂着一个虚词收尾
COST_NEW_SEGMENT = 3.0       # 每多一条字幕的代价：条数越少 = 每条越满
COST_ORPHAN_TAIL = 3.0       # 一条字幕只有一个词（且不是整段就一个词）
COST_UNDERFULL = 2.5         # 末条比前面明显空（按字符数比例）
COST_EMPTY_LINE = 1.5        # 一条字幕少用一行（两行的样式只填了一行）


def _core(tok: str) -> str:
    return fold_word(strip_punct(tok))


def is_sentence_end(tok: str) -> bool:
    return bool(_SENT_END.search(str(tok or "").strip()))


def is_soft_end(tok: str) -> bool:
    return bool(_SOFT_END.search(str(tok or "").strip()))


def _is_number(tok: str) -> bool:
    return bool(_NUM.match(str(tok or "").strip()))


def _is_capital(tok: str) -> bool:
    core = strip_punct(str(tok or ""))
    return bool(core) and core[0].isupper() and core != "I"


def cut_cost(prev_tok: str, next_tok: str) -> float:
    """在 prev_tok 和 next_tok 之间切一刀的代价。"""
    prev = str(prev_tok or "")
    nxt = str(next_tok or "")
    if is_sentence_end(prev) or is_soft_end(prev):
        base = COST_AFTER_PUNCT
    elif _is_number(nxt):
        base = COST_BEFORE_NUMBER
    elif _is_capital(nxt) and not _is_capital(prev):
        base = COST_BEFORE_CAPITAL
    elif _core(nxt) in _CONJ:
        base = COST_BEFORE_CONJ
    else:
        base = COST_ANYWHERE
    if _core(prev) in _NO_END and not is_soft_end(prev):
        base += COST_AFTER_NO_END
    return base


# ───────────────────────────── 度量 ─────────────────────────────

def safe_margin_frac(style: dict | None) -> float:
    """左右安全边距（画布宽度比例，单边）。样式没写就是老的 8%。"""
    s = style if isinstance(style, dict) else {}
    raw = s.get("safe_margin_pct")
    if raw is None:
        return SAFE_MARGIN_DEFAULT_PCT / 100.0
    try:
        return max(0.0, min(0.45, float(raw) / 100.0))
    except (TypeError, ValueError):
        return SAFE_MARGIN_DEFAULT_PCT / 100.0


def max_words_per_line(style: dict | None) -> int:
    s = style if isinstance(style, dict) else {}
    try:
        return max(0, int(s.get("max_words_per_line") or 0))
    except (TypeError, ValueError):
        return 0


def pause_break_sec(style: dict | None) -> float:
    s = style if isinstance(style, dict) else {}
    raw = s.get("pause_break_sec")
    if raw is None:
        raw = s.get("segment_gap_sec")
    try:
        v = float(raw) if raw is not None else PAUSE_BREAK_DEFAULT
    except (TypeError, ValueError):
        v = PAUSE_BREAK_DEFAULT
    return max(0.1, min(5.0, v))


def wrap_style_for_measure(style: dict, canvas_w: int) -> dict:
    """渲染的可用宽度 = min(字幕宽度范围, 1−2×安全边距)。量的时候也用这个。"""
    s = dict(style or {})
    inset_pct = max(1.0, (1.0 - 2.0 * safe_margin_frac(s)) * 100.0)
    from cutora.subtitle.stylepack import wrap_width_percent_value
    pct = wrap_width_percent_value(s)
    if pct is None or pct > inset_pct:
        s["wrap_width_percent"] = inset_pct
    return s


def make_line_counter(style: dict, task: dict, lang: str | None = None
                      ) -> Callable[[list[str]], int] | None:
    """返回 f(tokens) → 行数。没有 QApplication 时返回 None。"""
    try:
        from PySide6.QtWidgets import QApplication
        if QApplication.instance() is None:
            return None
        from cutora.render.textfx.layout import _pixel_wrap_lines
        from cutora.render.textfx.spec import expansion_px, from_style
        from cutora.subtitle.io import canvas_size
        cw, _ch = canvas_size(task)
        em = float(style.get("fontsize") or 34)
        spec = from_style(style)
        extra = expansion_px(spec, em, for_wrap=True) + float(style.get("box_padding_x") or 0)
        family = spec.fonts.body or str(style.get("font_family") or "Arimo")
        bold = bool(style.get("bold", True))
        italic = bool(style.get("italic", False))
        mstyle = wrap_style_for_measure(style, cw)
        cache: dict = {}

        def count(tokens):
            text = " ".join(tokens)
            hit = cache.get(text)
            if hit is not None:
                return hit
            parts = _pixel_wrap_lines(
                [text], family, em, spec, cw, bold, italic,
                extra_pad=extra, lang=lang, max_lines=None, style=mstyle)
            n = max(1, len(parts))
            cache[text] = n
            return n
        return count
    except Exception:
        return None


# ───────────────────────────── 切分 ─────────────────────────────

def split_run(tokens: list[str], fits: Callable[[list[str]], bool],
              max_words: int = 0, lines_of: Callable[[list[str]], int] | None = None,
              max_lines: int = 1) -> list[list[str]]:
    """把一段词（无硬边界）切成若干条。返回每条的 token 列表。

    fits(seg) 装不装得下；lines_of(seg) 用了几行（给「少用一行」计罚，可不传）。
    """
    n = len(tokens)
    if n == 0:
        return []
    INF = float("inf")
    best = [INF] * (n + 1)
    prev = [-1] * (n + 1)
    best[0] = 0.0
    total_chars = max(1, len(" ".join(tokens)))
    for j in range(1, n + 1):
        # i 从 j-1 往前扫，一旦装不下就停（再往前更装不下）
        i = j - 1
        while i >= 0:
            seg = tokens[i:j]
            if max_words and len(seg) > max_words:
                break
            if len(seg) > 1 and not fits(seg):
                break
            if best[i] < INF:
                cost = best[i] + COST_NEW_SEGMENT
                if i > 0:
                    cost += cut_cost(tokens[i - 1], tokens[i])
                if len(seg) == 1 and n > 1:
                    cost += COST_ORPHAN_TAIL
                if lines_of is not None and max_lines > 1 and len(seg) > 1:
                    used = max(1, min(max_lines, int(lines_of(seg))))
                    cost += COST_EMPTY_LINE * (max_lines - used)
                if j == n and i > 0:
                    # 末条比平均明显空 → 罚，逼它从前一条借词
                    frac = len(" ".join(seg)) / float(total_chars)
                    if frac < 0.25:
                        cost += COST_UNDERFULL * (0.25 - frac) / 0.25
                if cost < best[j]:
                    best[j] = cost
                    prev[j] = i
            i -= 1
        if best[j] == INF:
            # 单个词都装不下（超长词）：只能自己一条
            i = j - 1
            best[j] = best[i] + COST_NEW_SEGMENT
            prev[j] = i
    out: list[list[str]] = []
    j = n
    while j > 0:
        i = prev[j]
        out.append(tokens[i:j])
        j = i
    out.reverse()
    return out


def _hard_break_between(a: dict, b: dict, pause: float) -> bool:
    if is_sentence_end(a.get("word", "")):
        return True
    try:
        gap = float(b.get("start")) - float(a.get("end"))
    except (TypeError, ValueError):
        return False
    return gap >= pause


def group_words(items: list[dict], style: dict, task: dict, *,
                lang: str | None = None, max_lines: int | None = None,
                line_counter: Callable[[list[str]], int] | None = None,
                merge_runs: bool = True) -> list[list[dict]]:
    """items: [{"word","start","end",...}] 已按时间排好。返回分组。

    merge_runs=False 时不看停顿 / 句末（调用方保证 items 本来就是一条字幕）。
    """
    if not items:
        return []
    from cutora.subtitle.stylepack import effective_max_lines, is_single_word_mode
    if is_single_word_mode(style):
        return [[it] for it in items]
    max_l = max(1, int(max_lines or effective_max_lines(style, task)))
    counter = line_counter or make_line_counter(style, task, lang)
    if counter is None:
        return [items]
    cap = max_words_per_line(style)
    max_words = cap * max_l if cap else 0

    def fits(tokens):
        return counter(tokens) <= max_l

    runs: list[list[dict]] = []
    if merge_runs:
        pause = pause_break_sec(style)
        cur: list[dict] = []
        for it in items:
            if cur and _hard_break_between(cur[-1], it, pause):
                runs.append(cur)
                cur = []
            cur.append(it)
        if cur:
            runs.append(cur)
    else:
        runs = [items]
    groups: list[list[dict]] = []
    for run in runs:
        toks = [str(it.get("word") or "") for it in run]
        for part in split_run(toks, fits, max_words, lines_of=counter, max_lines=max_l):
            k = len(part)
            groups.append(run[:k])
            run = run[k:]
        if run:
            groups.append(run)
    return groups


def _seg_text_is_edited(raw: dict) -> bool:
    ed = raw.get("edited_text")
    if not ed:
        return False
    joined = " ".join(str(w.get("word") or "").strip()
                      for w in (raw.get("words") or []) if isinstance(w, dict)).split()
    return " ".join(str(ed).split()) != " ".join(joined)


def reflow_task(task: dict, *, selected=None, max_lines: int | None = None,
                lang: str | None = None) -> dict:
    """跨字幕重排：合并碎句、按意群切。锁定 / 手改过 / 未选中的原样留下。

    返回 {"before": n, "after": m, "changed": k}。
    """
    from cutora.subtitle.layout import _seg_from_word_group, _word_items_for_split
    from cutora.subtitle.track_source import set_segments
    segs = list(task.get("segments") or []) if isinstance(task, dict) else []
    if not segs:
        return {"before": 0, "after": 0, "changed": 0}
    style = task.get("style") if isinstance(task.get("style"), dict) else {}
    counter = make_line_counter(style, task, lang)
    if counter is None:
        return {"before": len(segs), "after": len(segs), "changed": 0}
    out: list = []
    changed = 0
    # 连续可重排的字幕攒成一块，块内跨字幕重排；块与块之间是硬边界
    block: list[dict] = []
    block_base: dict | None = None

    def _flush():
        nonlocal block, block_base, changed
        if not block:
            return
        base = dict(block_base or block[0])
        items = []
        for raw in block:
            items.extend(_word_items_for_split(raw))
        groups = group_words(items, style, task, lang=lang, max_lines=max_lines,
                             line_counter=counter, merge_runs=True)
        parts = [_seg_from_word_group(base, g, first=(k == 0))
                 for k, g in enumerate(groups) if g]
        if parts:
            parts[0]["start"] = float(block[0].get("start") or parts[0]["start"])
            parts[-1]["end"] = float(block[-1].get("end") or parts[-1]["end"])
            for a, b in zip(parts, parts[1:]):
                a["end"] = float(b["start"])
            # 角色：按时间交集继承
            for p in parts:
                p["role"] = _role_for(p, block)
        old_texts = [str(r.get("text") or "") for r in block]
        new_texts = [str(p.get("text") or "") for p in parts]
        if old_texts != new_texts:
            changed += len(parts)
        out.extend(parts or block)
        block = []
        block_base = None

    for i, raw in enumerate(segs):
        keep = (not isinstance(raw, dict) or raw.get("locked")
                or (selected is not None and i not in selected)
                or _seg_text_is_edited(raw))
        if keep:
            _flush()
            out.append(raw)
            continue
        if block and str(raw.get("role") or "default") != str(block[-1].get("role") or "default"):
            _flush()
        if not block:
            block_base = raw
        block.append(raw)
    _flush()
    for raw in out:
        if isinstance(raw, dict):
            raw.pop("lines", None)
            raw.pop("_fit_warn", None)
            raw.pop("layout_warn", None)
    set_segments(task, out)
    return {"before": len(segs), "after": len(out), "changed": changed}


def _role_for(part: dict, block: list[dict]) -> str:
    try:
        ps, pe = float(part.get("start")), float(part.get("end"))
    except (TypeError, ValueError):
        return str(block[0].get("role") or "default")
    best, best_ov = str(block[0].get("role") or "default"), 0.0
    for raw in block:
        try:
            s0, e0 = float(raw.get("start")), float(raw.get("end"))
        except (TypeError, ValueError):
            continue
        ov = min(pe, e0) - max(ps, s0)
        if ov > best_ov:
            best_ov = ov
            best = str(raw.get("role") or "default")
    return best
