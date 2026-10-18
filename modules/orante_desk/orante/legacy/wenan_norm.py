# -*- coding: utf-8 -*-
"""文案体检 + 格式规范 —— 只补空格, 绝不改字。

要解决的是同一件事的两副面孔:

  A 类 · 标点后面没空格
        "bless you,two"  "understand this.When"  "labour.”Ecclesiastes"
        分词器只按空白切, 标点又被直接删掉 → 粘成 "youtwo" / "thiswhen"。
        口播 ASR 出来永远是分开的两个词, 对不上 → 句子交界处开洞 →
        拼接器报"缺失文案, 请重新生成"。实测 Tik017 一篇 46 处。
        正则百分百可解, 不需要 AI。

  B 类 · 无标点粘连
        "overthrowwhat"  "preparingto"  "peopleto"  "personis"
        源文案里本来就粘着, 没有标点可抓。用英文词频表切:
        整体不是词、但能干净拆成两个常用词的才拆。拆不准的交给 AI 兜底。

铁律 (skeleton 不变式):
    规范前后, 把两边的空白和所有标点全删光、转小写之后, 必须逐字节相同。
    只要不同, 整篇原样退回, 一个字都不改。
    这条对 AI 那条路一样生效 —— AI 最爱顺手把 labour 改成 labor、
    把长句拆短、把引号换成直角引号, 有这条它就翻不了车。

依赖: 只用标准库。AI 是可选的回调, 本模块自己不联网。
"""

from __future__ import annotations

import os
import re

# ---------------------------------------------------------------------------
# 参数 (环境变量可覆盖, 不用改代码)
# ---------------------------------------------------------------------------
KNOWN_ZIPF = float(os.environ.get("WENAN_KNOWN_ZIPF", "2.40"))
# 整体词频低于这个值 → 当作"不是个词", 才轮得到拆
HALF_ZIPF = float(os.environ.get("WENAN_HALF_ZIPF", "3.00"))
# 拆出来的两半各自至少要有这个词频, 才算拆得干净
MIN_TOKEN_LEN = int(os.environ.get("WENAN_MIN_TOKEN_LEN", "6"))
MIN_HALF_LEN = int(os.environ.get("WENAN_MIN_HALF_LEN", "2"))

WORDS_FILE = "wenan_words.txt"

# A 类: 字母 + 标点 + 字母。两边都必须是字母, 所以 "4:9" "22:10" "3:30"
# 这类数字一律不碰 (经文出处 Ecclesiastes 4:9 就是靠这条保住的)。
# 前瞻: 字母 / 中文开引号 “‘ / 括号, 或者直引号后面紧跟非空白 (["'](?=\S))。
# only." So 里 " 后面是空格, 那是闭引号, 不能在 . 和 " 之间插空格。
_GLUED_PUNCT = re.compile(
    r'(?<=[A-Za-z])([.,;:!?…]+[”"’\')\]]*)'
    r'(?=[A-Za-z“‘(\[]|["\'](?=\S))')

# 小写紧接大写: "SaulJoseph" → "Saul Joseph"
# 缩写后面接单词: "KJVMany" → "KJV Many"
# 两条都只在整体不是已知词时才用。
_CAMEL = re.compile(r'(?<=[a-z])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])')

_WORD_RE = re.compile(r"[A-Za-z’']+")
_STRIP_RE = re.compile(r"[^0-9a-z一-鿿]+")

# 【防误拆】这些是构词前缀, 不是独立的词。
# 反面教材: "unequally" (Be ye not unequally yoked) 词频表里没有,
# 于是被拆成 "un equally" —— 两半都"是词", 分数还很高, 但那是个真词。
# 凡是左半落在这张表里的一律不拆。
# 注意这里【故意不收】in/out/over/under/up/on/off/down/back —— 那些本身就是
# 常用词, "inyour" 这类粘连还得靠它们拆开。
_PREFIX_NOT_WORD = {
    "un", "re", "dis", "non", "pre", "mis", "im", "il", "ir", "sub",
    "inter", "intra", "semi", "anti", "en", "em", "fore", "mid", "co",
    "bi", "tri", "multi", "super", "trans", "ultra", "extra", "post",
}
# 同理, 这些是后缀不是词; 右半落进来也不拆。
# eth/th/st: 古英语第三人称/第二人称词尾, 否则 knoweth → know eth。
_SUFFIX_NOT_WORD = {
    "ly", "ness", "ment", "ing", "ed", "er", "est", "ful", "less",
    "tion", "sion", "able", "ible", "ity", "ous", "ive", "ize", "ise",
    "ship", "hood", "ward", "wise",
    "eth", "th", "st",
}

# KJV 经文里反复出现的古英语词, 词频表没有。scan_glued 命中就跳过, 视同已知词。
_ARCHAIC_WORDS = {
    "knoweth", "cometh", "doeth", "goeth", "saith", "hath", "doth",
    "shalt", "wilt", "thou", "thee", "thy", "thine", "ye", "art",
    "knowest", "sayest", "hast", "dost", "canst",
}


# ---------------------------------------------------------------------------
# 词表
# ---------------------------------------------------------------------------

def _default_words_path():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), WORDS_FILE)


_CACHE = {}


def load_words(path=None):
    """读词频表 → {word: zipf}。读不到就返回空表 (只做 A 类, 不做 B 类)。"""
    path = path or _default_words_path()
    key = os.path.abspath(path)
    if key in _CACHE:
        return _CACHE[key]
    d = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split()
                if len(parts) >= 2:
                    try:
                        d[parts[0]] = float(parts[1])
                    except ValueError:
                        pass
                elif parts:
                    d[parts[0]] = 5.0
    except OSError:
        d = {}
    _CACHE[key] = d
    return d


def _z(words, w):
    return words.get((w or "").lower(), 0.0)


# ---------------------------------------------------------------------------
# skeleton 不变式
# ---------------------------------------------------------------------------

def skeleton(text):
    """把空白和所有标点全删光、转小写。规范前后这个必须一模一样。"""
    return _STRIP_RE.sub("", (text or "").lower())


# ---------------------------------------------------------------------------
# A 类
# ---------------------------------------------------------------------------

def fix_punct_spacing(text):
    """标点后面漏掉的空格补回来。返回 (新文本, 补了几处)。"""
    text = text or ""
    n = len(_GLUED_PUNCT.findall(text))
    return (_GLUED_PUNCT.sub(r'\1 ', text), n) if n else (text, 0)


# ---------------------------------------------------------------------------
# B 类
# ---------------------------------------------------------------------------

def _best_split(words, tok):
    """能不能干净地拆成两半。拆得动返回 (左, 右), 拆不动返回 None。"""
    low = tok.lower()
    best = None
    best_score = 0.0
    for i in range(MIN_HALF_LEN, len(low) - MIN_HALF_LEN + 1):
        l, r = low[:i], low[i:]
        if l in _PREFIX_NOT_WORD or r in _SUFFIX_NOT_WORD:
            continue                      # 构词前后缀, 不是拼在一起的两个词
        zl, zr = _z(words, l), _z(words, r)
        if zl < HALF_ZIPF or zr < HALF_ZIPF:
            continue
        # 两半都得是常用词; 谁更"确定"看较弱的那一半
        score = min(zl, zr)
        if score > best_score:
            best_score, best = score, (tok[:i], tok[i:])
    return best


def scan_glued(text, words=None):
    """找出疑似无标点粘连的 token。

    返回 (可以放心拆的 [(原词, 拆开)], 拿不准的 [原词])。
    拿不准 = 整体不像个词, 但也找不到干净的拆法 → 该问 AI 的就是这些。
    """
    words = load_words() if words is None else words
    sure, unsure = [], []
    if not words:
        return sure, unsure
    seen = set()
    for m in _WORD_RE.finditer(text or ""):
        tok = m.group(0)
        if len(tok) < MIN_TOKEN_LEN or tok in seen:
            continue
        seen.add(tok)
        if "'" in tok or "’" in tok:
            continue                      # anyone's / God's 这种所有格, 不碰
        if tok.lower() in _ARCHAIC_WORDS:
            continue                      # 古英语白名单, 视同已知词
        if _z(words, tok) >= KNOWN_ZIPF:
            continue                      # 本来就是个词, 别动
        if _CAMEL.search(tok):            # SaulJoseph 这种, 大小写就够定案
            sure.append((tok, _CAMEL.sub(" ", tok)))
            continue
        sp = _best_split(words, tok)
        if sp:
            sure.append((tok, "%s %s" % sp))
        else:
            unsure.append(tok)
    return sure, unsure


def _apply_pairs(text, pairs):
    """整词替换, 按长度倒序, 避免短词先替把长词切碎。"""
    for old, new in sorted(pairs, key=lambda p: -len(p[0])):
        text = re.sub(r"(?<![A-Za-z])%s(?![A-Za-z])" % re.escape(old), new, text)
    return text


# ---------------------------------------------------------------------------
# 对外
# ---------------------------------------------------------------------------

def check(text, words=None):
    """只体检不改。返回报告 dict。"""
    _, n_punct = fix_punct_spacing(text)
    sure, unsure = scan_glued(text, words)
    return {"punct": n_punct, "glued": sure, "unsure": unsure}


def normalize(text, words=None, ai=None, log=None):
    """规范格式。返回 (新文本, 报告)。

    ai: 可选回调, ai(["overthrowwhat", ...]) -> {"overthrowwhat": "overthrow what"}
        只把词表拿不准的那几个交给它。返回的每一条也要过 skeleton 检查,
        AI 改了字的那条直接丢掉, 其余照收。
    """
    log = log or (lambda *a: None)
    words = load_words() if words is None else words
    src = text or ""
    before = skeleton(src)

    out, n_punct = fix_punct_spacing(src)
    sure, unsure = scan_glued(out, words)
    if sure:
        out = _apply_pairs(out, sure)

    ai_fixed, ai_bad = [], []
    if unsure and ai:
        try:
            got = ai(list(unsure)) or {}
        except Exception as e:
            log("  ! 文案规范: AI 兜底失败, 跳过 (%s)" % str(e)[:80])
            got = {}
        for tok, rep in got.items():
            rep = str(rep or "")
            if not rep or tok not in unsure:
                continue
            if skeleton(rep) != skeleton(tok):
                ai_bad.append(tok)        # AI 改了字, 不要
                continue
            ai_fixed.append((tok, rep))
        if ai_fixed:
            out = _apply_pairs(out, ai_fixed)
        unsure = [t for t in unsure if t not in dict(ai_fixed)]

    rep = {"punct": n_punct, "glued": sure, "ai_glued": ai_fixed,
           "ai_rejected": ai_bad, "unsure": unsure, "ok": True, "changed": out != src}

    # 铁律: 骨架必须一模一样, 否则整篇退回
    if skeleton(out) != before:
        log("  ⚠ 文案规范: 骨架对不上 (改动了字, 不只是空格) —— 整篇原样退回, 一个字没改")
        rep["ok"] = False
        rep["changed"] = False
        return src, rep
    return out, rep


def gemini_ai(api_key, model="gemini-2.0-flash", timeout=25):
    """做一个能塞给 normalize(ai=...) 的回调。只用标准库。

    只把词表拿不准的那几个词发过去, 不发整篇文案 —— 便宜、快, 也不给 AI
    机会去动别的地方。回来的每一条还要过 skeleton 检查, 改了字的直接丢。
    """
    if not api_key:
        return None
    import json
    import urllib.request

    def _ask(tokens):
        if not tokens:
            return {}
        prompt = (
            "下面每个字符串, 可能是两个英文单词漏了空格粘在一起, "
            "也可能它本身就是一个正常的英文单词。\n"
            "规则:\n"
            "1. 确定是粘连的, 在正确位置加一个空格拆开。\n"
            "2. 本身就是正常英文单词的 (比如 unequally, nevertheless), 原样返回。\n"
            "3. 拿不准的, 原样返回。\n"
            "4. 只允许加空格。绝对不许改动、增加或删除任何一个字母。\n"
            "只返回一个 JSON 对象 {原字符串: 结果}, 不要任何解释。\n\n"
            + "\n".join(tokens))
        url = ("https://generativelanguage.googleapis.com/v1beta/models/"
               "%s:generateContent?key=%s" % (model, api_key))
        body = json.dumps({
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0,
                                 "responseMimeType": "application/json"},
        }).encode("utf-8")
        req = urllib.request.Request(
            url, data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
        txt = (data["candidates"][0]["content"]["parts"][0]["text"] or "").strip()
        txt = re.sub(r"^```(?:json)?|```$", "", txt, flags=re.M).strip()
        got = json.loads(txt)
        return got if isinstance(got, dict) else {}

    return _ask


def report_lines(rep):
    """把报告变成能直接打进日志的几行中文。"""
    out = []
    if rep.get("punct"):
        out.append("文案体检: 标点后缺空格 %d 处, 已补" % rep["punct"])
    if rep.get("glued"):
        ex = ", ".join("%s→%s" % (a, b) for a, b in rep["glued"][:6])
        out.append("文案体检: 粘连词 %d 个, 已按词表拆开 (%s%s)"
                   % (len(rep["glued"]), ex, " …" if len(rep["glued"]) > 6 else ""))
    if rep.get("ai_glued"):
        ex = ", ".join("%s→%s" % (a, b) for a, b in rep["ai_glued"][:6])
        out.append("文案体检: AI 兜底拆开 %d 个 (%s)" % (len(rep["ai_glued"]), ex))
    if rep.get("ai_rejected"):
        out.append("文案体检: AI 想改字的 %d 个已丢弃 (%s)"
                   % (len(rep["ai_rejected"]), ", ".join(rep["ai_rejected"][:6])))
    if rep.get("unsure"):
        out.append("文案体检: 这几个词拿不准, 没动, 建议到源文案里手工看一眼 → %s"
                   % ", ".join(rep["unsure"][:10]))
    if not rep.get("ok"):
        out.append("文案体检: 骨架校验没过, 整篇原样退回")
    if not out:
        out.append("文案体检: 没发现格式问题")
    return out


if __name__ == "__main__":
    import sys
    ws = load_words()
    print("词表 %d 条 (%s)" % (len(ws), _default_words_path()))
    for p in sys.argv[1:]:
        with open(p, "r", encoding="utf-8") as f:
            t = f.read()
        new, rep = normalize(t, ws)
        print("\n=== %s ===" % p)
        for line in report_lines(rep):
            print("  " + line)
