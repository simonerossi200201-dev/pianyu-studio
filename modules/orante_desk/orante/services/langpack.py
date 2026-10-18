# -*- coding: utf-8 -*-
"""规格 92：按语种改提示词。

第一次运行时问一句「你的片子是给哪种语言的观众看的」，然后把所有会进到成品提示词里的
文字整理成一份可复制的清单，让用户丢给 ChatGPT 翻成那个语种、格式不变地粘回来。

为什么不在软件里直接调翻译接口：提示词里全是"念出来 / 不要念出来"「镜头」这类拍摄指令，
机器直译很容易把指令翻坏；交给用户自己那边的 ChatGPT，他能一眼看出哪句不对、当场让它改。
Orante 只负责两件事：**把要翻的东西一条不漏地摆出来**，以及**粘回来时守住格式**。

清单长这样（每条一个编号 + 定位键，正文夹在 <<< >>> 中间）：

    [[3]] tail.情绪
    <<<
    人物的情绪是温暖而恳切的……
    >>>

粘回来时只认编号行和 <<< >>>，**少贴几条也没关系**——只应用贴回来的那些，
其余保持原样。ChatGPT 把长文截断了就分两次贴。
"""
from __future__ import annotations

import copy
import json
import re

from .. import config
from ..errors import OranteError
from . import prompts

# 目标语种。code 只用来记在设置里；真正给 ChatGPT 看的是 name。
LANGS = [
    {"code": "zh", "name": "中文（简体）"},
    {"code": "zh-Hant", "name": "中文（繁體）"},
    {"code": "en", "name": "English"},
    {"code": "es", "name": "Español"},
    {"code": "pt", "name": "Português"},
    {"code": "fr", "name": "Français"},
    {"code": "de", "name": "Deutsch"},
    {"code": "it", "name": "Italiano"},
    {"code": "ru", "name": "Русский"},
    {"code": "ar", "name": "العربية"},
    {"code": "hi", "name": "हिन्दी"},
    {"code": "id", "name": "Bahasa Indonesia"},
    {"code": "vi", "name": "Tiếng Việt"},
    {"code": "th", "name": "ไทย"},
    {"code": "ja", "name": "日本語"},
    {"code": "ko", "name": "한국어"},
]

# 收哪些块、按什么顺序。problem.patterns 是识别问题句用的正则/短语，
# 翻了反而认不出来，所以整块不收（要改到多模态面板里手改）。
ROOTS = ("desc", "silent", "tail", "camera", "expand",
         "bridge", "bridge_single", "chain", "upscale", "refill_clause", "problem")
# 关键盘（默认）：决定"片子里的人说什么语言、怎么说"的那些。
# 扩图机位库（expand）有 80 多条，讲的是摄影机怎么摆，翻不翻都不影响成片语种，
# 单独列成"全部"那一档，愿意的人再翻。
CORE_ROOTS = ("desc", "silent", "tail", "camera",
              "bridge", "bridge_single", "chain", "upscale", "refill_clause", "problem")
SKIP_PATHS = ("problem.patterns", "expand.version", "tail.情绪预设")
MIN_LEN = 2                      # 太短的（"—" 之类）不值得翻
PLACEHOLDER = re.compile(r"\{[^{}\n]{1,40}\}")
HEAD_RE = re.compile(r"^\[\[(\d+)\]\]\s*(\S+)\s*$")


def lang_name(code: str) -> str:
    for x in LANGS:
        if x["code"] == code:
            return x["name"]
    return (code or "").strip()


# ---------------------------------------------------------------- 收集
def _walk(node, path: str, out: list) -> None:
    if any(path == s or path.startswith(s + ".") or path.startswith(s + "[")
           for s in SKIP_PATHS):
        return
    if isinstance(node, str):
        if len(node.strip()) >= MIN_LEN:
            out.append({"key": path, "text": node})
        return
    if isinstance(node, dict):
        for k, v in node.items():
            k = str(k)
            if "." in k or "[" in k:      # 键里带分隔符就没法定位，跳过
                continue
            _walk(v, f"{path}.{k}" if path else k, out)
        return
    if isinstance(node, list):
        for i, v in enumerate(node):
            _walk(v, f"{path}[{i}]", out)


def roots_for(scope: str = "core") -> tuple:
    return ROOTS if (scope or "core").lower() == "all" else CORE_ROOTS


def collect(blocks: dict | None = None, *, scope: str = "core") -> list[dict]:
    """把会进到成品提示词里的文字摊平成一条条。scope="all" 连扩图机位库一起。"""
    out: list[dict] = []
    for root in roots_for(scope):
        if blocks is not None:
            val = blocks.get(root)
        else:
            try:
                val = prompts.get_block(root)
            except OranteError:
                continue
        if val is None:
            continue
        _walk(copy.deepcopy(val), root, out)
    return out


# ---------------------------------------------------------------- 导出
HEADER = """# ===== Orante 提示词语种包 v1 =====
# 目标语种：{lang}
# 共 {n} 条

请把下面每一条 <<< 和 >>> 之间的正文改写成【{lang}】，然后把**整份**原样发回来。

规则（很重要，请严格照做）：
1. 不要改 [[编号]] 这一行，也不要改它后面的定位键；不要增删条目、不要调换顺序。
2. 保留 <<< 和 >>> 这两行。正文里的换行、空行、编号、项目符号都保持原样。
3. 保留所有 {{花括号占位符}}（例如 {{原话}}、{{start}}）——它们是程序要替换的变量，原样照抄。
4. 【方括号】里的标签是拍摄指令的标记，照搬结构，把里面的字翻成目标语种。
5. 这些是"拍视频用的提示词"，不是要念出来的台词：
   诸如「不要念出来」「把这句话念完整」这类指令，请按目标语种自然地表达，不要漏掉。
6. 只输出这份清单本身，前后不要加解释。

# ===== 以下是正文 =====
"""


def render(entries: list[dict], lang: str) -> str:
    name = lang_name(lang) or lang or "目标语种"
    parts = [HEADER.format(lang=name, n=len(entries))]
    for i, e in enumerate(entries, 1):
        parts.append(f"\n[[{i}]] {e['key']}\n<<<\n{e['text']}\n>>>\n")
    return "".join(parts)


def export_text(lang: str, *, scope: str = "core") -> dict:
    entries = collect(scope=scope)
    return {"lang": lang, "lang_name": lang_name(lang) or lang, "scope": scope,
            "n": len(entries), "text": render(entries, lang),
            "chars": sum(len(e["text"]) for e in entries)}


# ---------------------------------------------------------------- 回收
def parse(text: str) -> dict:
    """从粘回来的文本里认出 [[n]] key + <<< 正文 >>>。少贴几条也照收。"""
    out: dict[str, str] = {}
    key = None
    buf: list[str] = []
    inside = False
    for raw in (text or "").splitlines():
        line = raw.rstrip("\r")
        if not inside:
            m = HEAD_RE.match(line.strip())
            if m:
                key = m.group(2)
                continue
            if line.strip() == "<<<" and key:
                inside = True
                buf = []
            continue
        if line.strip() == ">>>":
            inside = False
            if key:
                out[key] = "\n".join(buf).strip("\n")
            key = None
            continue
        buf.append(line)
    return out


def _holders(s: str) -> set:
    return set(PLACEHOLDER.findall(s or ""))


def check(parsed: dict, entries: list[dict] | None = None) -> dict:
    """粘回来的东西能不能用。返回 {ok: {...}, problems: [...]}。

    守两条线：占位符不许丢（丢了程序替换不上，成品提示词就废了），正文不许空。
    定位键不认识的直接忽略——多半是 ChatGPT 自己加的解释。
    """
    # 回收时一律按"全部"比对：用户可能只贴了关键盘，也可能贴了全部
    cur = {e["key"]: e["text"] for e in (entries if entries is not None
                                         else collect(scope="all"))}
    ok: dict[str, str] = {}
    problems: list[dict] = []
    for key, new in (parsed or {}).items():
        old = cur.get(key)
        if old is None:
            problems.append({"key": key, "why": "unknown", "text": "这一条在提示词里找不到，已跳过"})
            continue
        if not (new or "").strip():
            problems.append({"key": key, "why": "empty", "text": "正文是空的，已跳过"})
            continue
        lost = _holders(old) - _holders(new)
        if lost:
            problems.append({"key": key, "why": "placeholder",
                             "text": "少了占位符 " + "、".join(sorted(lost)) + "，已跳过"})
            continue
        if new.strip() == (old or "").strip():
            continue                      # 没改动的不算数，也不用写
        ok[key] = new
    return {"ok": ok, "problems": problems,
            "missing": [k for k in cur if k not in (parsed or {})]}


def _set_path(root_val, path: str, value: str):
    """按 a.b[0].c 这样的路径写回去。root 那一段已经在调用方剥掉了。"""
    node = root_val
    steps: list = []
    for part in path.split("."):
        if not part:
            continue
        m = re.match(r"^([^\[]*)((?:\[\d+\])*)$", part)
        name, idx = (m.group(1), m.group(2)) if m else (part, "")
        if name:
            steps.append(name)
        for n in re.findall(r"\[(\d+)\]", idx or ""):
            steps.append(int(n))
    if not steps:
        return value                      # 整块就是一段文字
    for s in steps[:-1]:
        node = node[s]
    node[steps[-1]] = value
    return root_val


def apply_parsed(parsed: dict, *, lang: str = "") -> dict:
    """把认过的条目写回提示词。写之前先备份，随时能一键还原。"""
    res = check(parsed)
    ok = res["ok"]
    if not ok:
        return {"applied": 0, "keys": [], "problems": res["problems"],
                "missing_n": len(res["missing"])}
    backup()
    by_root: dict[str, list] = {}
    for key in ok:
        root = key.split(".")[0].split("[")[0]
        by_root.setdefault(root, []).append(key)
    applied: list[str] = []
    for root, keys in by_root.items():
        val = copy.deepcopy(prompts.get_block(root))
        for key in keys:
            rest = key[len(root):].lstrip(".")
            try:
                val = _set_path(val, rest, ok[key])
            except (KeyError, IndexError, TypeError):
                res["problems"].append({"key": key, "why": "path",
                                        "text": "定位键对不上现在的提示词，已跳过"})
                continue
            applied.append(key)
        prompts.set_block(root, val)
    if lang:
        config.set("content.lang", lang)
    config.set("content.lang_applied", {
        "lang": lang or config.get("content.lang") or "",
        "n": len(applied),
        "at": _now(),
    }, touch_rev=False)
    return {"applied": len(applied), "keys": applied,
            "problems": res["problems"], "missing_n": len(res["missing"])}


def _now() -> str:
    from datetime import datetime
    return datetime.now().isoformat(timespec="seconds")


# ---------------------------------------------------------------- 备份 / 还原
def backup() -> dict:
    """存一份改之前的提示词。只存第一次——第二次翻译前的那份已经是译文了，
    留着原文才有意义（用户要的是"翻坏了能回到出厂那套"）。"""
    old = config.get("prompts.lang_backup")
    if isinstance(old, dict) and old.get("blocks"):
        return old
    rec = {"at": _now(), "blocks": {r: copy.deepcopy(prompts.get_block(r))
                                    for r in ROOTS}}
    config.set("prompts.lang_backup", rec, touch_rev=False)
    return rec


def restore() -> dict:
    rec = config.get("prompts.lang_backup")
    if not isinstance(rec, dict) or not rec.get("blocks"):
        raise OranteError("没有可还原的备份", "还没做过语种改写，或者备份被清掉了。")
    n = 0
    for root, val in (rec.get("blocks") or {}).items():
        if root in prompts.EDITABLE or root in ROOTS:
            try:
                prompts.set_block(root, copy.deepcopy(val))
                n += 1
            except OranteError:
                continue
    config.set("content.lang_applied", {}, touch_rev=False)
    return {"restored": n, "at": rec.get("at") or ""}


def state() -> dict:
    applied = config.get("content.lang_applied")
    applied = applied if isinstance(applied, dict) else {}
    bak = config.get("prompts.lang_backup")
    return {
        "lang": config.get("content.lang") or "",
        "lang_name": lang_name(config.get("content.lang") or ""),
        "applied": applied,
        "has_backup": bool(isinstance(bak, dict) and bak.get("blocks")),
        "langs": LANGS,
        "n": len(collect(scope="core")),
        "n_all": len(collect(scope="all")),
    }
