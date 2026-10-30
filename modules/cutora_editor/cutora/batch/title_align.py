# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q190 · 标题 ↔ 口播 的按词对齐（批量加文本「识别开头时间戳」的核心）。

以前 ``text_batch.locate_text`` 是把标题和字幕都去掉标点拼成一长串，再原样查找；找不到就
用句子开头的几个字再找。Luca 实机上的结果：
- 「THREE TYPES OF PEOPLE…」口播被模型写成「3 types of people…」→ 一个字对不上 → 未定位；
- 「…OCTOBER 1ST?」被写成「october first」→ 只对上前面「may i pray for you」→ 文本条在 1.5 s 就截掉。

现在：
1. 两边都先走拼接流水线那套归一化（``script_stitcher.norm_tokens`` + 数字折叠）：
   three / 3、1st / first、10% / 10 percent、don't / dont、大小写、标点——全部归一；
   中日韩文按字切，不受空格影响。
2. 标题按词和口播做「贴合对齐」（标题整体要对上，口播两头自由）：允许个别词听错、漏词、多词，
   每个词带自己的时间戳 —— 起点就是标题第一个词开口的时刻，终点就是最后一个词说完的时刻。
3. 结果分类，界面照着写原因：
   matched       吻合（≥ 85% 的词对上）
   partial       基本吻合，有几个词不同（50～85%），``diff`` 列出是哪几个
   mismatch      视频内容与标题文字不符
   window_short  标题说到一半识别范围就到头了（界面会自动往后扩再识别一次）
   no_speech     识别范围里没听到说话
   empty         标题是空的
"""
from __future__ import annotations

import difflib
import re

_CJK = re.compile(r"[぀-ヿ㐀-䶿一-鿿豈-﫿가-힯]")
_LATIN_RUN = re.compile(r"[^぀-ヿ㐀-䶿一-鿿豈-﫿가-힯\s]+")

MATCH_GOOD = 0.85        # ≥ 这个比例的标题词对上 = 吻合
MATCH_PARTIAL = 0.50     # ≥ 这个比例 = 基本吻合（可用，但列出不同的词）
NEAR_RATIO = 0.80        # 两个词相似度 ≥ 这个值算「听差了一点」（tithe / tithes）
_S_EQUAL = 1.0
_S_NEAR = 0.7
_S_SUB = -0.9
_GAP_TITLE = -0.6        # 标题里的词口播没说（或模型漏了）
_GAP_SPEECH = -0.5       # 口播里多说了一个词
SENTENCE_PAUSE_S = 0.55  # 词与词之间停这么久 = 一句话的边界
SENTENCE_MAX_WORDS = 28


# ------------------------------------------------------------------ 切词

def _norm_piece(piece: str) -> list[str]:
    from cutora.batch.script_stitcher import norm_tokens
    return norm_tokens(piece)


def tokens_of(text: str) -> list[str]:
    """一段文字 → 归一化 token。西文按词（数字 / 序数 / 百分号 / 缩写统一），中日韩按字。"""
    return [t for t, _ in tokens_with_owner([(str(text or ""), 0)])]


def tokens_with_owner_bounds(pieces) -> list[tuple[str, int, int]]:
    """pieces = [(text, owner_id)] → [(token, start_owner_id, end_owner_id)]，并做数字/经文/缩写折叠。

    折叠多词数字（如 "twenty" "five" → "25"、"two" "thousand" → "2000"）或缩写（"K" "J" "V" → "kjv"）时，
    保留首词 owner 与末词 owner，确保标题在多词数字或缩写处收尾时不会截断末词时长。
    """
    from cutora.batch.script_stitcher import _fold_for_match, unglue_punct
    toks: list[str] = []
    owners: list[int] = []
    for text, owner in pieces:
        text = unglue_punct(str(text or ""))
        for raw in re.split(r"\s+", text):
            if not raw:
                continue
            pos = 0
            for m in _LATIN_RUN.finditer(raw):
                for ch in raw[pos:m.start()]:
                    if _CJK.match(ch):
                        toks.append(ch)
                        owners.append(owner)
                for t in _norm_piece(m.group(0)):
                    toks.append(t)
                    owners.append(owner)
                pos = m.end()
            for ch in raw[pos:]:
                if _CJK.match(ch):
                    toks.append(ch)
                    owners.append(owner)
    if not toks:
        return []
    folded, spans = _fold_for_match(toks, spans=[(i, i) for i in range(len(toks))])
    out = []
    for t, (a, b) in zip(folded, spans):
        out.append((t, owners[a], owners[b]))
    return out


def tokens_with_owner(pieces) -> list[tuple[str, int]]:
    """pieces = [(text, owner_id)] → [(token, owner_id)]，并做数字折叠（folded 的 owner 取首词）。"""
    return [(t, oa) for t, oa, _ob in tokens_with_owner_bounds(pieces)]


def _words_of(segments) -> list[dict]:
    raw_out: list[dict] = []
    for seg in segments or []:
        if isinstance(seg, dict):
            sub_words = seg.get("words")
            if isinstance(sub_words, list) and sub_words:
                for sw in sub_words:
                    if not isinstance(sw, dict):
                        continue
                    w_txt = str(sw.get("text") or sw.get("word") or "").strip()
                    if not w_txt:
                        continue
                    try:
                        ws = float(sw.get("start") if sw.get("start") is not None else seg.get("start") or 0.0)
                        we = float(sw.get("end") if sw.get("end") is not None else ws)
                    except (TypeError, ValueError):
                        continue
                    raw_out.append({"start": ws, "end": max(we, ws), "text": w_txt})
                continue
            start, end, text = seg.get("start"), seg.get("end"), seg.get("text") or seg.get("word")
        else:
            start = getattr(seg, "start", None)
            end = getattr(seg, "end", None)
            text = getattr(seg, "text", "") or getattr(seg, "word", "")
        try:
            start = float(start)
            end = float(end)
        except (TypeError, ValueError):
            continue
        text = str(text or "").strip()
        if not text:
            continue
        end = max(end, start)
        parts = [p for p in re.split(r"\s+", text) if p]
        if len(parts) > 1 and (end - start) > 0.6:
            weights = [max(1, len(p)) for p in parts]
            tot_w = float(sum(weights))
            acc = 0.0
            for p, w_len in zip(parts, weights):
                ws = start + (end - start) * (acc / tot_w)
                acc += w_len
                we = start + (end - start) * (acc / tot_w)
                raw_out.append({"start": ws, "end": max(we, ws), "text": p})
        else:
            raw_out.append({"start": start, "end": end, "text": text})

    # 合并被逗号拆散的千位数字（如 Whisper 将 "2,000" 拆成 ("2", 3.99..4.01) + (",000"/"0", 4.03..4.67)）
    out: list[dict] = []
    for w in raw_out:
        if out:
            prev = out[-1]
            p_txt = prev["text"].strip()
            c_txt = w["text"].strip()
            gap = w["start"] - prev["end"]
            c_dur = w["end"] - w["start"]
            if (
                gap <= 0.15
                and re.fullmatch(r"\d{1,3}", p_txt)
                and (
                    re.fullmatch(r",?\d{3}(?:,\d{3})*", c_txt)
                    or (c_txt == "0" and c_dur >= 0.20)
                )
            ):
                suffix = "000" if c_txt in ("0", "000", ",000") else c_txt.replace(",", "")
                prev["text"] = p_txt + suffix
                prev["end"] = max(prev["end"], w["end"])
                continue
        out.append(w)
    return out


# ------------------------------------------------------------------ 对齐

def _sim(a: str, b: str, cache: dict) -> float:
    if a == b:
        return _S_EQUAL
    key = (a, b) if a < b else (b, a)
    v = cache.get(key)
    if v is None:
        if len(a) >= 4 and len(b) >= 4 and a[0] == b[0] \
                and difflib.SequenceMatcher(None, a, b).ratio() >= NEAR_RATIO:
            v = _S_NEAR
        else:
            v = _S_SUB
        cache[key] = v
    return v


def _fit(title: list[str], speech: list[str]):
    """贴合对齐：标题全体参与（漏词扣分），口播两头随意。返回 (score, pairs)。

    pairs = [(i, j, kind)]，kind ∈ equal / near / sub / gap_t（标题词没对上）/ gap_s（口播多的词）。
    """
    n, m = len(title), len(speech)
    NEG = float("-inf")
    cache: dict = {}
    # H[i][j]：标题前 i 个词 对 口播前 j 个词（口播开头自由）
    H = [[0.0] * (m + 1) for _ in range(n + 1)]
    B = [[0] * (m + 1) for _ in range(n + 1)]     # 0 停, 1 对角, 2 上(标题gap), 3 左(口播gap)
    for i in range(1, n + 1):
        H[i][0] = H[i - 1][0] + _GAP_TITLE
        B[i][0] = 2
    for i in range(1, n + 1):
        ti = title[i - 1]
        Hi, Hp, Bi = H[i], H[i - 1], B[i]
        for j in range(1, m + 1):
            best = Hp[j - 1] + _sim(ti, speech[j - 1], cache)
            back = 1
            up = Hp[j] + _GAP_TITLE
            if up > best:
                best, back = up, 2
            left = Hi[j - 1] + _GAP_SPEECH
            if left > best:
                best, back = left, 3
            Hi[j] = best
            Bi[j] = back
    j_end = max(range(m + 1), key=lambda j: H[n][j]) if m else 0
    score = H[n][j_end] if m else NEG
    pairs = []
    i, j = n, j_end
    while i > 0:
        back = B[i][j]
        if back == 1:
            s = _sim(title[i - 1], speech[j - 1], cache)
            kind = "equal" if s == _S_EQUAL else ("near" if s == _S_NEAR else "sub")
            pairs.append((i - 1, j - 1, kind))
            i -= 1
            j -= 1
        elif back == 2:
            pairs.append((i - 1, None, "gap_t"))
            i -= 1
        else:
            pairs.append((None, j - 1, "gap_s"))
            j -= 1
    pairs.reverse()
    return score, pairs


_MEMO: dict = {}
_MEMO_MAX = 512


def align_title(segments, title: str) -> dict:
    """带缓存的 ``_align_title``：表格每改一格都会重算全部行，同一条视频同一个标题不重复对齐。"""
    words = segments or []
    try:
        key = (len(words), _w_sig(words[0]) if words else None, _w_sig(words[-1]) if words else None,
               id(words[len(words) // 2]) if words else None, str(title or ""))
    except Exception:
        key = None
    if key is not None:
        hit = _MEMO.get(key)
        if hit is not None:
            return dict(hit)
    got = _align_title(segments, title)
    if key is not None:
        if len(_MEMO) >= _MEMO_MAX:
            _MEMO.clear()
        _MEMO[key] = dict(got)
    return got


def _w_sig(w):
    if isinstance(w, dict):
        return (w.get("start"), w.get("end"), w.get("text"))
    return (getattr(w, "start", None), getattr(w, "end", None), getattr(w, "text", None))


def _align_title(segments, title: str) -> dict:
    """标题在这条口播里的位置。

    返回 {ok, how, start, end, score, why, diff, said, n_title, n_speech, tail_missing}
      score  = 对上的标题词 / 标题词数（near 算 0.7）
      diff   = [(标题里的词, 口播里的词)]，口播没说的写 ""，口播多说的标题侧写 ""
      said   = 对齐区间里口播的原话（原词，没归一化）
    """
    out = {"ok": False, "how": "mismatch", "start": None, "end": None, "score": 0.0,
           "why": "", "diff": [], "said": "", "n_title": 0, "n_speech": 0, "tail_missing": 0}
    words = _words_of(segments)
    out["n_speech"] = len(words)
    title_toks = tokens_of(title)
    out["n_title"] = len(title_toks)
    if not title_toks:
        out.update(how="empty", why="这一格是空的")
        return out
    if not words:
        out.update(how="no_speech", why="识别范围里没听到说话")
        return out
    sp = tokens_with_owner_bounds([(w["text"], k) for k, w in enumerate(words)])
    speech_toks = [t for t, _, _ in sp]
    owner_start = [oa for _, oa, _ in sp]
    owner_end = [ob for _, _, ob in sp]
    if not speech_toks:
        out.update(how="no_speech", why="识别范围里没听到说话")
        return out

    n = len(title_toks)
    # 找的是「开头标题」：只在识别范围开头这一段里找（整条视频几百个词时也不全扫，界面不卡）
    head = 3 * n + 80
    _score, pairs = _fit(title_toks, speech_toks[:head])
    hit = 0.0
    n_hit = 0
    js = []
    hit_pairs = []
    diff = []
    for i, j, kind in pairs:
        if kind == "equal":
            hit += 1.0
            n_hit += 1
            js.append(j)
            hit_pairs.append((i, j))
        elif kind == "near":
            hit += _S_NEAR
            n_hit += 1
            js.append(j)
            hit_pairs.append((i, j))
            diff.append((title_toks[i], speech_toks[j]))
        elif kind == "sub":
            diff.append((title_toks[i], speech_toks[j]))
        elif kind == "gap_t":
            diff.append((title_toks[i], ""))
        else:
            diff.append(("", speech_toks[j]))
    score = hit / max(1, n)
    out["score"] = round(score, 3)
    out["diff"] = diff
    if not js:
        out.update(how="mismatch", why="视频内容与标题文字不符")
        return out
    # 防止首尾因个别错词跨越长停顿（>=0.65s 且多跳过口播词）误吸附到下一句里的单个常见词
    while len(hit_pairs) >= 4:
        i_prev, j_prev = hit_pairs[-2]
        i_last, j_last = hit_pairs[-1]
        extra_s = (j_last - j_prev) - (i_last - i_prev)
        t_gap = float(words[owner_start[j_last]]["start"]) - float(words[owner_end[j_prev]]["end"])
        if extra_s >= 2 and (t_gap >= 0.65 or extra_s >= 4):
            hit_pairs.pop()
            js.pop()
        else:
            break
    while len(hit_pairs) >= 4:
        i_first, j_first = hit_pairs[0]
        i_next, j_next = hit_pairs[1]
        extra_s = (j_next - j_first) - (i_next - i_first)
        t_gap = float(words[owner_start[j_next]]["start"]) - float(words[owner_end[j_first]]["end"])
        if extra_s >= 2 and (t_gap >= 0.65 or extra_s >= 4):
            hit_pairs.pop(0)
            js.pop(0)
        else:
            break
    j0, j1 = js[0], js[-1]
    # 对上的词在口播里太稀（一堆 the / you 零散命中）也不算
    density = n_hit / max(1, j1 - j0 + 1)
    # 标题末尾有几个词没对上、而对上的部分已经顶到口播最后 → 识别范围太短
    last_hit_i = hit_pairs[-1][0]
    tail_missing = n - 1 - last_hit_i
    out["tail_missing"] = tail_missing
    i0_idx, i1_idx = owner_start[j0], owner_end[j1]
    w0, w1 = words[i0_idx], words[i1_idx]
    out["start"] = float(w0["start"])
    out["end"] = float(w1["end"])
    out["said"] = ensure_sentence_punctuation(
        _join_words([w["text"] for w in words[i0_idx: i1_idx + 1]]),
        words[i0_idx: i1_idx + 1],
    )
    at_end = (len(speech_toks) - 1 - j1) <= 1
    if tail_missing >= 1 and at_end and n_hit >= min(3, n):
        out.update(how="window_short",
                   why="标题说到一半识别范围就到头了（对上了前 %d 个词）" % n_hit)
        return out
    if n <= 3:
        good = n_hit == n and all(k == "equal" for _i, _j, k in pairs if k in ("equal", "near"))
    else:
        good = score >= MATCH_GOOD and density >= 0.5
    if good:
        out.update(ok=True, how="matched",
                   why=("" if not diff else "有 %d 处词不同" % len(diff)))
        return out
    if score >= MATCH_PARTIAL and density >= 0.5:
        out.update(ok=True, how="partial",
                   why="基本吻合，有 %d 处词不同" % len(diff))
        return out
    out.update(how="mismatch", why="视频内容与标题文字不符")
    return out


# ------------------------------------------------------------------ 原话

_QUESTION_STARTERS = frozenset({
    "is", "are", "was", "were", "do", "does", "did",
    "can", "could", "will", "would", "shall", "should",
    "may", "might", "have", "has", "had",
    "who", "what", "where", "when", "why", "how", "whom", "whose", "which",
})
_CLAUSE_CONJUNCTIONS = frozenset({
    "but", "because", "although", "though", "while", "whereas",
    "unless", "since", "so", "yet", "however", "therefore",
})
_COMMA_PAUSE_S = 0.24


def _join_words(texts) -> str:
    out = ""
    for t in texts:
        t = str(t or "").strip()
        if not t:
            continue
        if out and not (_CJK.match(out[-1]) and _CJK.match(t[0])):
            out += " "
        out += t
    return out


def _infer_end_punct(text: str) -> str:
    s = str(text or "").strip()
    if not s:
        return ""
    body = s.rstrip("\"'”’)] ")
    if not body:
        return ""
    if body[-1] in ".!?…。！？;；:：":
        return ""
    if _CJK.search(body):
        if body.endswith(("吗", "呢", "吧")) or any(q in body for q in ("为什么", "怎么", "哪里", "谁", "多少", "几")):
            return "？"
        return "。"
    toks = re.findall(r"[A-Za-z0-9']+", body.lower())
    if toks and toks[0] in _QUESTION_STARTERS:
        # "when god begins to lift you up" -> subordinate clause if not followed by auxiliary, so only question if short or aux
        if toks[0] in ("is", "are", "was", "were", "do", "does", "did", "can", "could", "will", "would", "shall", "should", "may", "might"):
            return "?"
        if len(toks) >= 2 and toks[1] in ("is", "are", "was", "were", "do", "does", "did", "can", "could", "will", "would", "shall", "should", "may", "might", "have", "has", "had"):
            return "?"
    return "."


def ensure_sentence_punctuation(text: str, words_slice: list[dict] | None = None) -> str:
    """确保识别出的口播原句带有准确标点符号（尤其是句尾句号/问号、句中停顿逗号及句首大写）。"""
    if words_slice and not any(ch in str(text or "") for ch in ".!?…。！？,，"):
        parts: list[str] = []
        n_w = len(words_slice)
        for idx, w in enumerate(words_slice):
            tok = str(w.get("text") or "").strip()
            if not tok:
                continue
            if tok == "i":
                tok = "I"
            elif tok.startswith("i'") and len(tok) > 2:
                tok = "I'" + tok[2:]
            if idx == 0 and tok and tok[0].isalpha() and tok[0].islower():
                tok = tok[0].upper() + tok[1:]
            # 句中明显停顿或转折连词前补逗号（不在句首句尾加）
            if 2 <= idx <= n_w - 2 and parts and not parts[-1].endswith((",", "，", ".", "!", "?", "。", "！", "？")):
                try:
                    gap = float(w.get("start") or 0.0) - float(words_slice[idx - 1].get("end") or 0.0)
                except (TypeError, ValueError):
                    gap = 0.0
                low = tok.lower().strip(".,!?")
                if gap >= _COMMA_PAUSE_S or (gap >= 0.10 and low in _CLAUSE_CONJUNCTIONS):
                    parts[-1] = parts[-1] + ("，" if _CJK.search(parts[-1]) else ",")
            parts.append(tok)
        s = _join_words(parts)
    else:
        s = str(text or "").strip()
        if s and s[0].isalpha() and s[0].islower():
            s = s[0].upper() + s[1:]
        s = re.sub(r"\bi\b", "I", s)
    if not s:
        return ""
    punct = _infer_end_punct(s)
    return s + punct


def spoken_sentences(segments, pause: float = SENTENCE_PAUSE_S,
                     max_words: int = SENTENCE_MAX_WORDS) -> list[dict]:
    """按停顿把口播切成句子：[{start, end, text}]，并自动补齐准确的句末句号/问号与句中逗号。"""
    words = _words_of(segments)
    out = []
    cur: list[dict] = []
    for w in words:
        if cur and (w["start"] - cur[-1]["end"] >= pause or len(cur) >= max_words):
            raw = _join_words(x["text"] for x in cur)
            out.append({"start": cur[0]["start"], "end": cur[-1]["end"],
                        "text": ensure_sentence_punctuation(raw, cur)})
            cur = []
        cur.append(w)
    if cur:
        raw = _join_words(x["text"] for x in cur)
        out.append({"start": cur[0]["start"], "end": cur[-1]["end"],
                    "text": ensure_sentence_punctuation(raw, cur)})
    return out


_SENT_SPLIT = re.compile(r"(?<=[.!?…。！？])[\"'”’)\]]*\s+(?=\S)|(?<=[。！？])(?=\S)")


def split_punctuated(text: str) -> list[str]:
    """模型给的带标点原文 → 一句一条（句末标点留在句子上）。"""
    out = []
    for piece in _SENT_SPLIT.split(str(text or "").strip()):
        piece = piece.strip()
        if piece:
            out.append(ensure_sentence_punctuation(piece))
    return out


def sentences_with_times(segments, sents) -> list[dict]:
    """带标点的原句（``sents`` = [{start,end,text}]，一条可能含几句）配上词级时间。

    每句的词和 ``segments``（词列表）对齐取起止；对不上的按原段时长等分估一个。
    ``sents`` 为空就退回按停顿切（``spoken_sentences``）。
    """
    if not sents:
        return spoken_sentences(segments)
    words = _words_of(segments)
    sp = tokens_with_owner_bounds([(w["text"], k) for k, w in enumerate(words)])
    speech_toks = [t for t, _, _ in sp]
    owner_start = [oa for _, oa, _ in sp]
    owner_end = [ob for _, _, ob in sp]
    out = []
    cursor = 0
    for seg in sents:
        if not isinstance(seg, dict):
            continue
        pieces = split_punctuated(seg.get("text") or "")
        if not pieces:
            continue
        try:
            s_start, s_end = float(seg.get("start") or 0.0), float(seg.get("end") or 0.0)
        except (TypeError, ValueError):
            s_start, s_end = 0.0, 0.0
        total = sum(max(1, len(tokens_of(p))) for p in pieces)
        acc = 0
        for piece in pieces:
            ptoks = tokens_of(piece)
            n = max(1, len(ptoks))
            est_a = s_start + (s_end - s_start) * acc / max(1, total)
            est_b = s_start + (s_end - s_start) * (acc + n) / max(1, total)
            acc += n
            start, end = est_a, est_b
            if ptoks and speech_toks and cursor < len(speech_toks):
                hi = min(len(speech_toks), cursor + len(ptoks) * 2 + 12)
                _score, pairs = _fit(ptoks, speech_toks[cursor:hi])
                js = [j for _i, j, k in pairs if k in ("equal", "near")]
                if len(js) >= max(1, len(ptoks) // 2):
                    j0, j1 = cursor + js[0], cursor + js[-1]
                    start, end = words[owner_start[j0]]["start"], words[owner_end[j1]]["end"]
                    cursor = j1 + 1
            out.append({"start": float(start), "end": float(max(end, start)),
                        "text": ensure_sentence_punctuation(piece)})
    return out


def punctuate_words_for_display(words: list[dict], sentences: list[dict] | None = None) -> list[dict]:
    """将词列表与带标点句子对齐，使每个词在点词条（TranscriptStrip）中也带准确的句号/问号/逗号与句首大写。"""
    w_list = [dict(w) for w in (words or []) if isinstance(w, dict) and str(w.get("text") or "").strip()]
    if not w_list:
        return []
    sents = list(sentences or []) or spoken_sentences(w_list)
    n = len(w_list)
    # 1. 先按句子起止时间把句首大写和句末标点(. / ? / !)标到对应词上
    used_ends: set[int] = set()
    for s in sents:
        st_txt = ensure_sentence_punctuation(str(s.get("text") or ""))
        if not st_txt:
            continue
        end_p = _end_punct(st_txt) or _infer_end_punct(st_txt) or "."
        try:
            sa, sb = float(s.get("start") or 0.0), float(s.get("end") or 0.0)
        except (TypeError, ValueError):
            continue
        in_span = [
            i for i, w in enumerate(w_list)
            if float(w.get("end") or 0.0) >= sa - 0.04 and float(w.get("start") or 0.0) <= sb + 0.04
        ]
        if not in_span:
            continue
        i_first, i_last = in_span[0], in_span[-1]
        t0 = str(w_list[i_first].get("text") or "")
        if t0 and t0[0].isalpha() and t0[0].islower():
            w_list[i_first]["text"] = t0[0].upper() + t0[1:]
        tl = str(w_list[i_last].get("text") or "")
        if tl and tl[-1] not in ".!?…。！？,，":
            w_list[i_last]["text"] = tl + end_p
        used_ends.add(i_last)
        # 如果句子内部有带逗号的词（来自 Whisper 原句），同步逗号到对应位置的词
        raw_tokens_with_punct = st_txt.split()
        if len(raw_tokens_with_punct) == len(in_span):
            for idx_k, raw_tok in zip(in_span, raw_tokens_with_punct):
                w_list[idx_k]["text"] = raw_tok

    # 2. 对剩余词修正代词 I，并在明显停顿处补句号或逗号
    for i, w in enumerate(w_list):
        txt = str(w.get("text") or "")
        if txt == "i":
            w["text"] = "I"
        elif txt.startswith("i'") and len(txt) > 2:
            w["text"] = "I'" + txt[2:]
        if i < n - 1 and i not in used_ends:
            try:
                gap = float(w_list[i + 1].get("start") or 0.0) - float(w.get("end") or 0.0)
            except (TypeError, ValueError):
                gap = 0.0
            cur_t = str(w.get("text") or "")
            if gap >= SENTENCE_PAUSE_S and cur_t and cur_t[-1] not in ".!?…。！？":
                w["text"] = cur_t.rstrip(",，") + "."
                nxt_t = str(w_list[i + 1].get("text") or "")
                if nxt_t and nxt_t[0].isalpha() and nxt_t[0].islower():
                    w_list[i + 1]["text"] = nxt_t[0].upper() + nxt_t[1:]
            elif gap >= _COMMA_PAUSE_S and cur_t and cur_t[-1] not in ".!?…。！？,，":
                w["text"] = cur_t + ","
    # 确保最后一个词有句末标点
    last_t = str(w_list[-1].get("text") or "")
    if last_t and last_t[-1] not in ".!?…。！？":
        w_list[-1]["text"] = last_t.rstrip(",，") + _infer_end_punct(last_t)
    return w_list


def sentences_in_span(sentences, start, end, pad: float = 0.05) -> list[dict]:
    """和 [start, end] 有实质交集的句子（给「视频原话」那一格用）。

    避免下一句仅因停顿极短（如 0.02s）落入旧版 +0.15s pad 而被误并入当前标题。
    """
    try:
        start_f, end_f = float(start), float(end)
    except (TypeError, ValueError):
        return []
    out = []
    for s in sentences or []:
        try:
            sa, sb = float(s["start"]), float(s["end"])
        except (TypeError, ValueError, KeyError):
            continue
        overlap = min(sb, end_f) - max(sa, start_f)
        s_dur = max(0.05, sb - sa)
        if overlap >= min(0.18, 0.25 * s_dur) or (sa >= start_f - pad and sb <= end_f + pad):
            out.append(s)
    return out


def diff_note(diff, limit: int = 8) -> str:
    """给悬停提示用：「标题 → 口播」列表。"""
    rows = []
    for a, b in (diff or [])[:limit]:
        if a and b:
            rows.append("%s → %s" % (a, b))
        elif a:
            rows.append("%s → （没说）" % a)
        else:
            rows.append("（多说）%s" % b)
    more = len(diff or []) - limit
    if more > 0:
        rows.append("… 还有 %d 处" % more)
    return "\n".join(rows)


# ------------------------------------------------------------------ 用原话替换标题

_SENT_END = "。！？!?…"
_LATIN_END = ".!?…"


def _title_sentences(title: str) -> list[str]:
    from cutora.batch.text_batch import split_sentences
    return split_sentences(title)


def _case_like(src: str, text: str) -> str:
    letters = [c for c in src if c.isalpha() and c.isascii()]
    if letters and all(c.isupper() for c in letters):
        return text.upper()
    if letters and all(c.islower() for c in letters):
        return text.lower()
    # 混合大小写：照模型给的（它会把 God / Jesus 这类专有名词写对），只保证句首大写
    return text[:1].upper() + text[1:] if text else text


def _end_punct(sent: str) -> str:
    s = sent.rstrip()
    while s and s[-1] in "\"'”’)]":
        s = s[:-1]
    return s[-1] if s and s[-1] in _SENT_END + _LATIN_END + ";；" else ""


def replacement_title(title: str, sentences: list[dict], count: int | None = None) -> str:
    """把口播的前几句拼成新标题，大小写 / 句数 / 换行 / 句末标点都跟着原标题走。

    sentences 是 ``spoken_sentences`` 的输出；count 不给就取原标题的句数（至少 1）。
    """
    title = str(title or "")
    t_sents = _title_sentences(title) or ([title.strip()] if title.strip() else [])
    k = int(count) if count else max(1, len(t_sents))
    picked = [s["text"] for s in (sentences or [])[:k] if str(s.get("text") or "").strip()]
    if not picked:
        return ""
    parts = []
    for idx, text in enumerate(picked):
        src = t_sents[idx] if idx < len(t_sents) else (t_sents[-1] if t_sents else "")
        body = _case_like(src or title, text.strip())
        if _end_punct(body):
            punct = ""                                   # 模型自己带了句末标点（问号 / 句号）就用它的
        else:
            punct = _end_punct(src) if src else ""
            if not punct and idx < len(picked) - 1 and not _CJK.search(body):
                punct = "."
        parts.append(body + punct)
    # 换行跟原标题：原标题句与句之间有换行就换行，否则空格
    seps = []
    if len(t_sents) > 1:
        pos = 0
        for s in t_sents[:-1]:
            i = title.find(s, pos)
            j = i + len(s) if i >= 0 else pos
            pos = j
            seps.append("\n" if "\n" in title[j:j + 3] else " ")
    out = parts[0]
    for idx, p in enumerate(parts[1:]):
        sep = seps[idx] if idx < len(seps) else (seps[-1] if seps else " ")
        if _CJK.search(p) and sep == " ":
            sep = ""
        out += sep + p
    return out
