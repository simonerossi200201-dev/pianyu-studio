# -*- coding: utf-8 -*-
"""Flow 文案分段 · 唯一实现
============================
这个模块是 Flow 分段规则的【唯一真源】。flow_table_auto.py（生成 vXX.txt）、
flow_check.py（自检比对）、flow_batch.py（挑时长）全部 import 这里，
谁都不许再抄一份。

—— 为什么要单独开一个文件 ——
早先 flow_table_auto.py 和 flow_check.py 各存了一份逐字相同的分段代码，
两边文件顶上都写着"改这里必须同步改那边"。这种靠注释维持的一致性迟早失守：
一旦漂移，自检算出来的分段和实际生成用的分段就对不上，自检直接变成噪音。

—— 核心：长度按【有效长度】算，不是字符数 ——
旧版只看 len(text)，标点完全不占时间。于是
    "Your agonizing tears will cease flowing, and your kin will enjoy
     lasting peace and joy."
这种 139 字符、3 个标点的排比句被判定"刚好塞得进 8 秒"，实际念到
"lasting peace and joy." 就没时间了 —— 0805 那批 v17 / v28 / v140
掉的全是这句尾巴，一模一样。

实测（0805 全量 865 段）：
    修复前  词数中位 21，超 20 词的段占 47%，8s 档平均 3.02 词/秒
    修复后  词数中位 13，超 20 词的段占  3%，语速回到 2.2 词/秒附近
片段数增长约 60% —— 这是文案总量（平均每条 67 词 ÷ 2.2 词/秒 ≈ 30 秒）
决定的物理下限，省不掉，只能让它增长得尽量少。
"""

import os
import re

# ── 分段上限：注意这是「有效长度」，不是纯字符数 ────────────────────
# 145 是拿 0805 全量跑参数扫描定的：段数增长最少（接近物理下限），
# 同时把 ">20 词的超长段" 从 47% 压到 3%。
# 可用环境变量 FLOW_SPLIT_MAX 覆盖（bat 菜单的松紧档位会设置它）：
#   松 155 / 标准 145 / 紧 130
DEFAULT_SPLIT_MAX = 145


def get_split_max():
    """每次调用都重读环境变量 —— bat 菜单是在进程启动后才设置它的。"""
    try:
        v = int(os.environ.get("FLOW_SPLIT_MAX") or DEFAULT_SPLIT_MAX)
    except ValueError:
        return DEFAULT_SPLIT_MAX
    return v if 80 <= v <= 300 else DEFAULT_SPLIT_MAX


# ── 主约束：加权词数（＝说话时长）───────────────────────────────────
# 字符数只是说话时长的【代理指标】，而且是个会失灵的代理：
#     "I pray for the one holding the phone and saying amen to Jesus Christ.
#      May the Lord open the gates of heaven for you."
#     → 24 个词，却只有 116 字符（平均词长 4.8），有效长度算出来 144，
#       卡在 145 预算之内被放过 —— 实际是 3.0 词/秒，照样赶话掉尾巴。
# 英文短词多的句子每字符塞的词更多，char 预算就管不住它。
# 所以真正的主约束改成【加权词数】，字符预算退居次要防线（防超长单词）。
DEFAULT_MAX_WORDS = 20          # 20 词 @8s = 2.5 词/秒，留出余量


def get_max_words():
    try:
        v = int(os.environ.get("FLOW_MAX_WORDS") or DEFAULT_MAX_WORDS)
    except ValueError:
        return DEFAULT_MAX_WORDS
    return v if 8 <= v <= 40 else DEFAULT_MAX_WORDS


# 标点折算成「相当于几个词的时间」。句末停顿比句中长。
PAUSE_COMMA_W = 0.7
PAUSE_SENT_W = 1.2


# 标点的停顿等效字符数。念到标点要停，停顿也占时长，所以折成字符一起算，
# 标点越密的段真正塞进去的单词就越少，语速才不会像机器人。
# 句号 > 逗号：句末停顿明显比句中长。
# （注：Grok 版 表格自动下载器.py 里是 逗号10 / 句号5，大小反了，那边的
#   注释也自相矛盾 —— 见该文件 120 行与 167 行。此处按正确的来，不动 Grok。）
PAUSE_COMMA = 10
PAUSE_SENT = 14

# 可作为切分点的标点（切分点落在标点"之后"）；中英文都覆盖
BREAK_PUNCT = "，。！？、；：,.!?;:…—"
# 标点后可能紧跟的收尾符（引号、括号等），一并归到上一段末尾
TRAIL = "\"'”’)）]】》"

_COMMA_SET = set(",;:，、；：")
_SENT_SET = set(".!?。！？…")
# 数字内部的 3.5 / 1,000 不是停顿，先剔掉再数
_DIGIT_PUNCT_RE = re.compile(r"(?<=\d)[.,:](?=\d)")

# 一段至少要有这么多单词。低于此值的段在 Flow 里生成时容易音频出错，
# 例如 "please type Amen."（3 词）。
# —— 7 → 5：真正的雷区是 3 词以下。分段变短之后，7 词的门槛会把本来
#    合格的短句硬粘回上一段，等于把刚修好的"段太长"问题又制造一遍。
MIN_WORDS = 5


def eff_len(s):
    """有效长度 = 字符数 + 标点停顿等效字符数。整个修复的核心。"""
    t = _DIGIT_PUNCT_RE.sub("", s)
    w = 0
    for ch in t:
        if ch in _SENT_SET:
            w += PAUSE_SENT
        elif ch in _COMMA_SET:
            w += PAUSE_COMMA
    return len(s) + w


_WORD_RE = re.compile(r"[A-Za-z0-9]+(?:['’-][A-Za-z0-9]+)*")
_CJK_RE = re.compile(r"[一-鿿]")


def word_count(s):
    """口播词数：英文按词（撇号/连字符算一个词），中文按字。
    与 flow_batch.count_words 同一套口径 —— 分段用的词数和挑时长用的词数
    必须一致，否则分段以为够短、挑时长又判成 8s，白折腾。"""
    t = str(s or "")
    return len(_WORD_RE.findall(t)) + len(_CJK_RE.findall(t))


def speech_cost(s):
    """说话时长的等效词数 = 词数 + 标点停顿折算。这是分段的主约束。"""
    t = _DIGIT_PUNCT_RE.sub("", str(s or ""))
    c = sum(1 for ch in t if ch in _COMMA_SET)
    p = sum(1 for ch in t if ch in _SENT_SET)
    return word_count(s) + c * PAUSE_COMMA_W + p * PAUSE_SENT_W


def fits(s, max_words=None, max_eff=None):
    """两道预算都得过：加权词数（主）+ 有效字符长度（次，防超长单词）。"""
    if max_words is None:
        max_words = get_max_words()
    if max_eff is None:
        max_eff = get_split_max()
    return speech_cost(s) <= max_words and eff_len(s) <= max_eff


def break_points(text):
    """所有可切分位置：标点之后（含其后的引号/括号等收尾符）"""
    breaks, seen = [], set()
    for i, ch in enumerate(text):
        if ch in BREAK_PUNCT:
            j = i + 1
            while j < len(text) and text[j] in TRAIL:
                j += 1
            if 0 < j < len(text) and j not in seen:
                seen.add(j)
                breaks.append(j)
    return breaks


# ── 次选切分点：连词处 ────────────────────────────────────────────
# 只在【标点切不出合格结果】时才用。切点落在连词【之前】，让连词去开启下一段。
#
# 为什么要分档：并列连词 and / or 经常只是名词并列（"bread and butter"），
# 从那儿切会把一个词组劈两半。从属连词（so that / because / although…）
# 几乎必定引导一个完整从句，切开听起来自然得多。所以按可靠度排序，
# 强档优先用，弱档垫底。
#
# 实例（0807 那 6 段唯一切不动的句子）：
#   "Write Amen and share this with at least one person so that this blessing
#    will never end. Will you say amen?"
#   内部只有 end. 一个标点 → 切成 17 词 + 4 词，尾巴太短被并回去 → 21 词卡死。
#   改从 "so that" 切 → 10 词 + 11 词，两段都自然。
CONJ_TIERS = [
    # 第 1 档 · 从属连词：后面必定跟一整个从句，最可靠
    ["so that", "in order that", "because", "although", "though",
     "unless", "until", "whereas", "而且", "并且", "因为", "所以",
     "如果", "虽然", "但是", "然后"],
    # 第 2 档 · 单字从属/转折连词
    ["but", "yet", "so", "if", "when", "while", "since", "after", "before"],
    # 第 3 档 · 并列连词：可能只是名词并列，最后才考虑
    ["and", "or", "nor"],
]
_CONJ_RES = [
    [re.compile(r"(?<![A-Za-z])" + re.escape(w) + r"(?![A-Za-z])", re.I)
     for w in tier] for tier in CONJ_TIERS
]


def conj_break_points(text):
    """返回 [(位置, 档位)]，位置是连词【起始处】。档位越小越可靠。"""
    out, seen = [], set()
    for tier, regs in enumerate(_CONJ_RES):
        for r in regs:
            for m in r.finditer(text):
                p = m.start()
                # 段首的连词切了等于没切；紧挨着标点的也没必要（标点档已覆盖）
                if p <= 0 or p >= len(text) or p in seen:
                    continue
                if text[max(0, p - 2):p].strip() == "":
                    prev = text[:p].rstrip()
                    if prev and prev[-1] in BREAK_PUNCT:
                        continue          # 前面就是标点，交给标点档
                seen.add(p)
                out.append((p, tier))
    return sorted(out)


def _pick_conj(text, start, end, max_words, max_eff, need_both=True):
    """在 text[start:end] 里挑一个连词切点。
    要求：切完两边都不短于 MIN_WORDS，前半段在预算内。
    优先级：档位小 → 位置靠后（尽量填满预算）。"""
    seg = text[start:end]
    cands = []
    for p, tier in conj_break_points(seg):
        a, c = seg[:p].strip(), seg[p:].strip()
        if not a or not c:
            continue
        if word_count(a) < MIN_WORDS:
            continue
        if need_both and word_count(c) < MIN_WORDS:
            continue
        if not fits(a, max_words, max_eff):
            continue
        cands.append((tier, -(start + p)))
    if not cands:
        return None
    tier, negpos = min(cands)
    return -negpos


def _merge_tiny(segs, max_words, max_eff):
    """消灭中间的碎片段。

    贪心取"预算内最靠后的标点"之后，中间段偶尔仍会很短 —— 当下一个标点
    一步就跨过预算时（如 "Hi," 后面跟一整个长从句）。把这种碎片并给
    合并后超出预算更少的那一侧邻居。

    【不碰末段】末段交给 _rebalance_tail —— 它会把最后两段合起来重新
    对半切，比这里的粗暴回并聪明得多。早先连末段一起并，导致
    "满段 + 4 词碎尾" 被并成 160+ 的超标段，正好抵消掉分段修复的效果
    （0805 实测：这一个顺序问题就贡献了 55 个超标段中的 43 个）。
    """
    i = 0
    while len(segs) > 1 and i < len(segs) - 1:
        if word_count(segs[i]) >= MIN_WORDS:
            i += 1
            continue
        prev_over = (speech_cost(segs[i - 1] + " " + segs[i]) - max_words
                     if i > 0 else None)
        next_over = speech_cost(segs[i] + " " + segs[i + 1]) - max_words
        if prev_over is None or next_over < prev_over:
            segs[i] = (segs[i].rstrip() + " " + segs[i + 1].lstrip()).strip()
            del segs[i + 1]
        else:
            segs[i - 1] = (segs[i - 1].rstrip() + " "
                           + segs[i].lstrip()).strip()
            del segs[i]
            i = max(i - 1, 0)
    return segs


def _rebalance_tail(segs, max_words, max_eff):
    """消灭过短的末段。

    朴素做法是把尾巴直接粘回上一段，但那会把上一段撑爆、超出时长上限。
    这里改为【把最后两段合起来重新对半切】：
        [满段, 碎尾]  →  [半段, 半段]
    两段都不超上限，也都够长。实在切不出合格的两段，才合成一段。
    """
    while len(segs) >= 2 and word_count(segs[-1]) < MIN_WORDS:
        merged = (segs[-2].rstrip() + " " + segs[-1].lstrip()).strip()
        best = None
        if not fits(merged, max_words, max_eff):
            mid = len(merged) / 2
            cands = sorted(break_points(merged), key=lambda x: abs(x - mid))
            # 标点排不出合格的两半时，把连词切点也加进候选。
            # 0807 那 6 段卡死的就是这一步：唯一的标点切点会留下 4 词的尾巴，
            # 短于 MIN_WORDS 被否决，于是整段 21 词原样留下、2.62 词/秒。
            # 从 "so that" 切就变成 10 + 11 词，两边都自然。
            cj = _pick_conj(merged, 0, len(merged), max_words, max_eff)
            if cj is not None and cj not in cands:
                cands = cands + [cj]
            # 第一轮：两半都够长、都在预算内 —— 最理想
            # 第二轮：两半都够长即可，允许其中一半略超预算 ——
            #         总好过让整段停留在 160+ 的严重超标状态
            for strict in (True, False):
                for b in cands:
                    a, c = merged[:b].strip(), merged[b:].strip()
                    if not (a and c):
                        continue
                    if (word_count(a) < MIN_WORDS
                            or word_count(c) < MIN_WORDS):
                        continue
                    if strict and not (fits(a, max_words, max_eff)
                                       and fits(c, max_words, max_eff)):
                        continue
                    best = [a, c]
                    break
                if best:
                    break
        if best:
            segs[-2:] = best
            break                     # 已均衡，末段够长了
        segs[-2:] = [merged]          # 合成一段，宁可略超上限也不留碎片
    return segs


def split_copy(text, max_chars=None, tolerance=None):
    """Flow 专用文案拆分（说话时长版）。

    两道预算，都得过：
      主约束 speech_cost ≤ get_max_words()（默认 20）—— 词数 + 标点停顿折算，
             直接对应说话时间，20 词 @8s ≈ 2.5 词/秒；
      次约束 eff_len ≤ get_split_max()（默认 145）—— 防超长单词把时间撑爆。

    切分点必须落在标点之后，取【预算内最靠后】的那个；整段无标点则原样返回，
    绝不从句中硬切。不改动任何文字。

    max_chars / tolerance 只为兼容旧调用签名，一律忽略。
    """
    text = text.strip()
    max_words, max_eff = get_max_words(), get_split_max()
    if not text:
        return []
    if fits(text, max_words, max_eff):
        return [text]

    # 注意：这里【不能】因为"没有标点"就提前返回。整段一个标点都没有，
    # 恰恰是最需要连词兜底的情况（原文漏标点，如 "…the phone Please take…"）。
    # 早先这里有个 `if not breaks: return [text]`，把这类句子直接放行了。
    breaks = break_points(text)

    segs, start = [], 0
    while not fits(text[start:], max_words, max_eff):
        ok = [b for b in breaks
              if b > start and fits(text[start:b], max_words, max_eff)]
        if ok:
            best = max(ok)                     # ① 首选：预算内最靠后的标点
        else:
            # ② 次选：连词处切（标点排不出合格结果时才用）
            cj = _pick_conj(text, start, len(text), max_words, max_eff)
            after = [b for b in breaks if b > start]
            if cj:
                best = cj
            elif after:
                best = after[0]                # ③ 兜底：超一点也要落在标点上
            else:
                break                          # ④ 整段无处下刀，原样保留
        if best <= start:
            break
        segs.append(text[start:best].strip())
        start = best
    tail = text[start:].strip()
    if tail:
        segs.append(tail)
    segs = [s for s in segs if s]
    segs = _merge_tiny(segs, max_words, max_eff)
    return _rebalance_tail(segs, max_words, max_eff)


def overlong(segs, max_words=None, max_eff=None):
    """挑出仍然超预算的段。这些几乎都是【原文标点缺失】导致切不动的，
    属于文案本身的问题，代码修不了，只能报出来让人去补标点。

    实例：0807 的 v141/v207
        "I pray for the person in front of the phone Please take away…"
        —— "phone" 和 "Please" 之间漏了句号，27 个词连成一串无处下刀。
    """
    if max_words is None:
        max_words = get_max_words()
    if max_eff is None:
        max_eff = get_split_max()
    out = []
    for i, s in enumerate(segs, 1):
        if not fits(s, max_words, max_eff):
            out.append((i, s, eff_len(s), len(break_points(s))))
    return out
