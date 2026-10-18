# -*- coding: utf-8 -*-
"""文案拆段：先规范格式（不调 AI），再按 Flow / Grok / 本地模型规则切开。

Flow 走 legacy.flow_split.split_copy；Grok 按 max_chars_15s 在标点/词边界切。
本地模型（studio）用 Flow 同一套切法和语速（20 词 ≈ 8 秒），但每段上限放到 15 秒；
「自动」时长学 Grok 只用 6 / 10 / 15 三档（用户 2026-10-06 定：段更完整）；手选还能选 4/6/8/10/15。
时长规则见 specs/00-architecture.md 第 7 节。段的 image 留空，编排面板再填。
"""
from __future__ import annotations

import os
import re
from dataclasses import asdict

from .. import paths
from ..errors import OranteError
from ..models import Segment

paths.add_legacy_to_sys_path()
import flow_split  # type: ignore  # noqa: E402
import wenan_norm  # type: ignore  # noqa: E402

_WS = re.compile(r"\s+")
# Grok 碎段并回上一段的门槛：表格自动下载器.MIN_SEG_WORDS
_GROK_MIN_WORDS = 4


def _cfg(key, default=None):
    try:
        from .. import config
        return config.get(key, default)
    except Exception:
        from ..config import DEFAULTS
        return DEFAULTS.get(key, default)


def _nospace(s: str) -> str:
    return _WS.sub("", s or "")


def _log(job, msg: str) -> None:
    if job is not None and hasattr(job, "log"):
        job.log(msg)


def _check(job) -> None:
    if job is not None and hasattr(job, "check"):
        job.check()


def _engine_of(task: dict | None, manifest) -> str:
    e = (task or {}).get("engine")
    if not e and manifest is not None:
        if isinstance(manifest, dict):
            e = manifest.get("engine")
        else:
            e = getattr(manifest, "engine", None)
    e = (e or "").strip().lower()
    if e not in ("flow", "grok", "studio"):
        raise OranteError(
            f"不知道用哪种拆段规则：{e or '空'}",
            "任务要先选 Flow、Grok 账号或本地模型。")
    return e


def _copy_of(task: dict | None, manifest) -> str:
    t = (task or {}).get("copy")
    if t:
        return t
    if manifest is None:
        return ""
    if isinstance(manifest, dict):
        return ((manifest.get("copy") or {}).get("text") or "")
    cp = getattr(manifest, "copy", None)
    if isinstance(cp, dict):
        return cp.get("text") or ""
    return ""


def _caps():
    from ..engines import caps
    return caps


def _studio_rule() -> dict:
    """本地模型：4/6/8 秒档和 Flow 一样（跟着 Flow 的设置走），10 / 15 秒按同一语速往上推。"""
    f = _rule_for("flow")
    cap8 = int(f["dur_8s_max_words"])
    eff8 = int(f["max_chars"])
    cap15 = min(40, int(cap8 * 15 / 8))                   # 20 词/8 秒 → 37 词/15 秒；flow_split 只认 8~40
    return {
        "engine": "studio",
        "dur_4s_max_words": int(f["dur_4s_max_words"]),
        "dur_6s_max_words": int(f["dur_6s_max_words"]),
        "dur_8s_max_words": cap8,
        "dur_10s_max_words": min(cap15, int(cap8 * 10 / 8)),
        "dur_15s_max_words": cap15,
        "auto_tiers": list(_caps().STUDIO_AUTO_TIERS),    # 自动定时长只用 6/10/15（跟 Grok 一样）
        "max_words": cap15,                               # 拆段主约束：一段最多念 15 秒
        "max_chars_8s": eff8,
        "max_chars": min(300, int(round(eff8 * 15 / 8))),  # 次约束（防超长单词），flow_split 只认 80~300
    }


def _rule_for(engine: str, override: dict | None = None) -> dict:
    """当时真正用的规则（含 engine），原样记进 manifest.split.rule。"""
    if engine == "studio":
        rule = _studio_rule()
    elif engine == "flow":
        cap8 = int(getattr(flow_split, "DEFAULT_MAX_WORDS", 20) or 20)
        rule = {
            "engine": "flow",
            "max_chars": int(_cfg("split.flow.max_chars", 145)),
            "dur_4s_max_words": int(_cfg("split.flow.dur_4s_max_words", 8)),
            "dur_6s_max_words": int(_cfg("split.flow.dur_6s_max_words", 13)),
            "allow_10s": False,  # 规格 84：已删 allow_10s
            "dur_8s_max_words": cap8,  # 8s 档上限 = flow_split 分段主约束
        }
    else:
        rule = {
            "engine": "grok",
            "max_chars_15s": int(_cfg("split.grok.max_chars_15s", 250)),
            "max_chars_10s": int(_cfg("split.grok.max_chars_10s", 187)),
            "dur_6s_max_chars": int(_cfg("split.grok.dur_6s_max_chars", 112)),
        }
    if override:
        for k, v in override.items():
            if k == "engine" or k not in rule or v is None:
                continue
            if isinstance(rule[k], bool):
                if isinstance(v, bool):
                    rule[k] = v
                else:
                    rule[k] = str(v).strip().lower() in ("1", "true", "yes", "on")
            elif isinstance(rule[k], int):
                rule[k] = int(v)
            else:
                rule[k] = v
    return rule


def _duration_flow(text: str, rule: dict) -> int:
    words = flow_split.word_count(text)
    if words <= int(rule["dur_4s_max_words"]):
        return 4
    if words <= int(rule["dur_6s_max_words"]):
        return 6
    cap8 = int(rule.get("dur_8s_max_words") or 20)
    if rule.get("allow_10s") and words > cap8:
        return 10
    return 8


def _duration_studio(text: str, rule: dict) -> int:
    """自动时长：auto_tiers（默认 6 / 10 / 15）里够念完的最短一档。"""
    words = flow_split.word_count(text)
    tiers = sorted(int(x) for x in (rule.get("auto_tiers") or _caps().STUDIO_AUTO_TIERS))
    for d in tiers[:-1]:
        if words <= int(rule.get(f"dur_{d}s_max_words") or 0):
            return d
    return tiers[-1]


def _duration_grok(text: str, rule: dict) -> int:
    n = len(text or "")
    if n <= int(rule["dur_6s_max_chars"]):
        return 6
    if n <= int(rule["max_chars_10s"]):
        return 10
    return 15


def _split_flow(text: str, rule: dict) -> list[str]:
    max_words = int(rule.get("dur_8s_max_words")
                    or getattr(flow_split, "DEFAULT_MAX_WORDS", 20))
    return _split_words(text, max_words, int(rule["max_chars"]))


def _split_studio(text: str, rule: dict) -> list[str]:
    """本地模型：段数尽量少（每段最长 15 秒），同样段数下再把段长摊匀。

    先用 Flow 的贪心切法（只在标点 / 连词处切）得到最少段数；再在同样的切点里找
    「段数最少 → 少用连词切 → 总秒数最短 → 少在句中切 → 段长最匀」的切法，避免 15 + 15 + 6
    这种最后剩一个碎尾巴的情况。找不到就用贪心结果。"""
    mw, me = int(rule["max_words"]), int(rule["max_chars"])
    greedy = _split_words(text, mw, me)
    try:
        even = _even_split(text.strip(), mw, me, rule)
    except Exception:  # noqa: BLE001
        even = None
    if even and len(even) <= len(greedy):
        return even
    return greedy


def _even_split(text: str, max_words: int, max_eff: int, rule: dict) -> list[str] | None:
    """在标点 / 连词切点上做最短路，逐级比较：段数 → 连词切点惩罚 → 总秒数 → 句中（逗号）切点数 → 段长平方和。"""
    n = len(text)
    if not n:
        return None
    conj = {p: tier for p, tier in flow_split.conj_break_points(text)}
    cuts = sorted(set([0, n] + list(flow_split.break_points(text)) + list(conj)))
    if len(cuts) > 600:
        return None
    def mid_sentence(p: int) -> int:
        t = text[:p].rstrip().rstrip(flow_split.TRAIL)
        return 0 if (t and t[-1] in flow_split._SENT_SET) else 1

    best: dict[int, tuple] = {0: (0, 0, 0, 0, 0.0, -1)}
    for j in range(1, len(cuts)):
        cj = cuts[j]
        cand = None
        for i in range(j - 1, -1, -1):
            ci = cuts[i]
            seg = text[ci:cj].strip()
            if not seg:
                continue
            if not flow_split.fits(seg, max_words, max_eff):
                break                                  # 再往前只会更长
            if i not in best:
                continue
            whole = ci == 0 and cj == n
            if not whole and flow_split.word_count(seg) < flow_split.MIN_WORDS:
                continue
            b = best[i]
            pen = (conj[cj] + 1) if (cj in conj and cj != n) else 0
            comma = mid_sentence(cj) if (cj != n and not pen) else 0
            cost = flow_split.speech_cost(seg)
            val = (b[0] + 1, b[1] + pen, b[2] + _duration_studio(seg, rule), b[3] + comma,
                   b[4] + cost * cost, i)
            if cand is None or val[:5] < cand[:5]:
                cand = val
        if cand is not None:
            best[j] = cand
    last = len(cuts) - 1
    if last not in best:
        return None
    out, j = [], last
    while j > 0:
        i = best[j][5]
        out.append(text[cuts[i]:cuts[j]].strip())
        j = i
    return list(reversed(out))


def _split_words(text: str, max_words: int, max_chars: int) -> list[str]:
    """flow_split.split_copy 忽略 max_chars 参数，靠环境变量才吃得到配置。"""
    keys = ("FLOW_SPLIT_MAX", "FLOW_MAX_WORDS")
    prev = {k: os.environ.get(k) for k in keys}
    os.environ["FLOW_SPLIT_MAX"] = str(max_chars)
    os.environ["FLOW_MAX_WORDS"] = str(max_words)
    try:
        return list(flow_split.split_copy(text, max_chars=max_chars) or [])
    finally:
        for k, v in prev.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _merge_tiny_grok(segs: list[str]) -> list[str]:
    """不足 4 词的段并入邻居，和表格自动下载器.merge_tiny_segs 同一门槛。"""
    segs = [s for s in segs if (s or "").strip()]
    i = 0
    while len(segs) > 1 and i < len(segs):
        if flow_split.word_count(segs[i]) < _GROK_MIN_WORDS:
            if i > 0:
                segs[i - 1] = (segs[i - 1].rstrip() + " " + segs[i].lstrip()).strip()
                del segs[i]
            else:
                segs[0] = (segs[0].rstrip() + " " + segs[1].lstrip()).strip()
                del segs[1]
        else:
            i += 1
    return segs


def _split_by_chars(text: str, max_chars: int) -> list[str]:
    """按字符上限切：优先标点后，其次空白，实在没有才硬切。"""
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]
    breaks = flow_split.break_points(text)
    segs, start, n = [], 0, len(text)
    while start < n:
        remain = text[start:]
        if len(remain.strip()) <= max_chars:
            tail = remain.strip()
            if tail:
                segs.append(tail)
            break
        limit = min(start + max_chars, n)
        ok = [b for b in breaks if start < b <= limit]
        if ok:
            best = max(ok)
        else:
            sp = text.rfind(" ", start + 1, limit + 1)
            best = sp if sp > start else limit
        if best <= start:
            best = min(start + max_chars, n)
        chunk = text[start:best].strip()
        if chunk:
            segs.append(chunk)
        else:
            # 窗口里全是空白，往前挪，避免死循环
            start = best if best > start else start + 1
            continue
        start = best
        while start < n and text[start].isspace():
            start += 1
    return _merge_tiny_grok(segs)


def _assert_conserved(src: str, segs: list[str]) -> None:
    if any(not (s or "").strip() for s in segs):
        raise OranteError("拆段结果有空段", "原文可能切出了只有空白的一段。")
    a, b = _nospace(src), _nospace("".join(segs))
    if a != b:
        raise OranteError(
            "拆段后字数对不上",
            f"原文去空白 {len(a)} 字，拆完 {len(b)} 字。请检查文案。")


def _normalize_and_split(raw: str, engine: str, rule: dict, log=None) -> list[str]:
    log = log or (lambda *a: None)
    new, rep = wenan_norm.normalize(raw or "", ai=None, log=log)
    for line in wenan_norm.report_lines(rep):
        log(line)
    src = _WS.sub(" ", (new or "").strip())
    if not src:
        raise OranteError("文案去掉空白后是空的", "请检查原文。")
    if engine == "flow":
        texts = _split_flow(src, rule)
    elif engine == "studio":
        texts = _split_studio(src, rule)
    else:
        texts = _split_by_chars(src, int(rule["max_chars_15s"]))
    texts = [t.strip() for t in texts if t and str(t).strip()]
    if not texts:
        raise OranteError("拆完一段都没有", "文案可能太短或全是空白。")
    _assert_conserved(raw, texts)
    return texts


def _to_segments(texts: list[str], engine: str, rule: dict) -> list[dict]:
    dur_fn = {"flow": _duration_flow, "studio": _duration_studio}.get(engine, _duration_grok)
    out = []
    for i, t in enumerate(texts, 1):
        if not t.strip():
            raise OranteError(f"第 {i} 段是空的", "拆段不能产生空段。")
        out.append(asdict(Segment(s=i, text=t, duration=dur_fn(t, rule), image="")))
    from . import tails
    tails.assign_roles(out)
    return out



class DurationError(OranteError):
    """没文案任务的时长不在引擎能力表里。给人看的错误，前端按普通报错显示。"""
    def __init__(self, message: str, hint: str = ""):
        super().__init__(message, hint)


def _eng_name(eng: str) -> str:
    return {"flow": "Flow", "grok": "Grok", "studio": "本地模型"}.get(eng, eng or "?")


def no_copy_duration(task, engine: str) -> int:
    """没文案（不说话）任务的每段时长。
    duration_pref 合法则用之；空/自动 → split.no_copy_duration.<engine>
    （86 号加设置；此处 flow=8 / grok=10 兜底）；不在 caps.DURATIONS → DurationError。
    """
    from ..engines import caps
    eng = (engine or "").strip().lower()
    if eng not in caps.DURATIONS:
        raise DurationError(f"未知引擎 {engine!r}", "引擎只能是 flow、grok 或 studio（本地模型）。")
    allowed = caps.DURATIONS[eng]
    raw = ""
    if isinstance(task, dict):
        raw = task.get("duration_pref") or ""
    raw = str(raw or "").strip()
    if not raw or raw in ("自动", "auto", "Auto"):
        key = f"split.no_copy_duration.{eng}"
        val = None
        try:
            val = _cfg(key, None)
        except Exception:
            val = None
        if val is None:
            val = caps.NO_COPY_DEFAULT[eng]
        d = int(val)
    else:
        try:
            d = int(float(raw))
        except ValueError as e:
            raise DurationError(
                f"{_eng_name(eng)} 最长 {allowed[-1]} 秒",
                f"时长必须是 {', '.join(map(str, allowed))} 之一，收到 {raw!r}。",
            ) from e
    if d not in allowed:
        raise DurationError(
            f"{_eng_name(eng)} 最长 {allowed[-1]} 秒",
            f"只能是 {', '.join(map(str, allowed))} 秒，收到 {d}。",
        )
    return d



# 有文案任务手选时长档时，每段的字/词上限（选 6 秒就按 6 秒档的量拆）
_TIER_CAP = {
    "flow": {4: "dur_4s_max_words", 6: "dur_6s_max_words", 8: "dur_8s_max_words"},
    "grok": {6: "dur_6s_max_chars", 10: "max_chars_10s", 15: "max_chars_15s"},
    "studio": {4: "dur_4s_max_words", 6: "dur_6s_max_words", 8: "dur_8s_max_words",
               10: "dur_10s_max_words", 15: "dur_15s_max_words"},
}


def copy_duration_pref(task, engine: str) -> int | None:
    """有文案任务在 ① 任务表手选的时长档。空 / 自动 → None（按语速自动）。

    不合法（不在引擎能力表里）→ DurationError，和没文案任务一个说法。
    """
    from ..engines import caps
    eng = (engine or "").strip().lower()
    raw = ""
    if isinstance(task, dict):
        raw = task.get("duration_pref") or ""
    raw = str(raw or "").strip()
    if not raw or raw in ("自动", "auto", "Auto"):
        return None
    allowed = caps.COPY_TIERS.get(eng) or ()         # 本地模型有文案：4/6/8/10/15
    try:
        d = int(float(raw))
    except ValueError:
        d = -1
    if d not in allowed:
        raise DurationError(
            f"{_eng_name(eng)} 只能出 {', '.join(map(str, allowed))} 秒",
            f"时长这一列收到 {raw!r}。清空 = 按语速自动。")
    return d


def _apply_duration_pref(rule: dict, engine: str, dur: int) -> tuple[dict, int]:
    """把规则压到手选的那一档：每段字/词不超过这一档的量。返回 (新规则, 每段上限)。"""
    key = _TIER_CAP[engine][dur]
    cap = int(rule[key])
    rule = dict(rule)
    if engine == "flow":
        rule["dur_8s_max_words"] = cap     # flow 拆段的主约束就是这个
    elif engine == "studio":
        rule["max_words"] = cap
        base = int(rule.get("max_chars_8s") or 145)
        rule["max_chars"] = base if dur <= 8 else min(300, int(round(base * dur / 8)))
    else:
        rule["max_chars_15s"] = cap        # grok 按字数切
    rule["duration_pref"] = int(dur)
    return rule, cap


def _image_sort_key(im: dict):
    p = str(im.get("p") or "")
    m = re.search(r"(\d+)", p)
    return (int(m.group(1)) if m else 0, p)


def clip_images(manifest) -> list[dict]:
    """没文案（不说话 / 空镜头）按图拆段用的全部图。

    主体和空镜都算。用户没填文案时要的就是空镜头，不能因为判成空镜就拆不了段。
    文件已经不在、或被拒的图除外。
    """
    imgs = list(getattr(manifest, "images", None) or [])
    out = []
    for im in imgs:
        if not isinstance(im, dict):
            continue
        if not str(im.get("p") or "").strip():
            continue
        st = (im.get("status") or "ok").strip()
        if st in ("missing", "blocked"):
            continue
        out.append(im)
    out.sort(key=_image_sort_key)
    return out


def _split_no_copy(job, task: dict, manifest, engine: str):
    """没文案 → 每张图一段（含空镜）；时长走 no_copy_duration。"""
    dur = no_copy_duration(task, engine)
    pics = clip_images(manifest)
    if not pics:
        raise OranteError("没有图片，没法按图拆段", "先给任务配上至少一张图。空镜也可以。")
    segs = []
    for i, im in enumerate(pics, 1):
        segs.append({
            "s": i,
            "text": "",
            "duration": int(dur),
            "image": im.get("p") or "",
            "role": "主体",
            "shot": "",
            "prompt_final": "",
            "status": "planned",
            "gen_id": None,
            "takes": [],
            "error": None,
            "bridge": {},
            "image_locked": True,
        })
    rule = {"no_copy": True, "per_image": True, "duration": int(dur)}
    _log(job, f"没有文案：按图拆成 {len(segs)} 段（每段 {dur} 秒）")
    return {"rule": rule}, segs


def split_task(job, task: dict, manifest) -> tuple[dict, list[dict]]:
    """返回 (split_rule, segments)。

    split_rule 形状是 {rule: {...}}，直接赋给 manifest.split。
    segments 是 models.Segment 的字段（s 从 1 起；image 先留空）。
    """
    _check(job)
    engine = _engine_of(task, manifest)
    raw = _copy_of(task, manifest)
    if not (raw or "").strip():
        # 没文案 → 一图一段（主体和空镜都算），时长走 no_copy_duration（本地模型 4~15 秒都行）
        return _split_no_copy(job, task, manifest, engine)
    # 本地模型的有文案任务：Flow 的切法和语速，每段最长 15 秒，时长 4/6/8/10/15（段数尽量少）
    rule = _rule_for(engine)
    try:
        pref = copy_duration_pref(task, engine)
    except DurationError as e:
        # 时长列里是这个引擎不支持的档（多半是换了账号留下的旧值）：
        # 有文案的任务不因此卡住，按语速自动拆，表上那一格本来就标黄了。
        pref = None
        _log(job, f"⚠ 时长 {e.message}；这次按语速自动定时长")
    if pref:
        rule, cap = _apply_duration_pref(rule, engine, pref)
        unit = "字" if engine == "grok" else "词"
        _log(job, f"手选时长 {pref} 秒：每段最多 {cap} {unit}，全部段都按 {pref} 秒")
    _log(job, f"拆段规则 {engine}：{rule}")
    texts = _normalize_and_split(raw, engine, rule, log=lambda m: _log(job, m))
    _check(job)
    segs = _to_segments(texts, engine, rule)
    if pref:
        over = 0
        for sg in segs:
            sg["duration"] = int(pref)
            n = (len(sg["text"] or "") if engine == "grok"
                 else flow_split.word_count(sg["text"]))
            if n > cap:
                over += 1
        if over:
            # Flow 只在标点处断句，长从句断不开，这时按用户选的秒数走但要说清楚
            _log(job, f"⚠ 有 {over} 段超过 {pref} 秒能念完的量（{cap} {unit}），可能念不完；"
                      f"把这几段自己再断句，或把时长选长一点")
    from . import duration_edit as _duredit
    segs = _duredit.preserve_manual_durations(getattr(manifest, "segments", None) or [], segs)
    durs = {}
    for sg in segs:
        durs[sg["duration"]] = durs.get(sg["duration"], 0) + 1
    hist = "，".join(f"{k}s×{v}" for k, v in sorted(durs.items()))
    _log(job, f"拆成 {len(segs)} 段（{hist}）")
    return {"rule": rule}, segs


def preview(text: str, engine: str, rule: dict | None = None) -> list[dict]:
    """不落盘，给前端预演用：[{s, text, duration, words, chars}]"""
    engine = (engine or "").strip().lower()
    if engine not in ("flow", "grok", "studio"):
        raise OranteError(
            f"拆段引擎只能是 flow、grok 或 studio，收到 {engine!r}",
            "预演时把引擎传对。")
    if not (text or "").strip():
        raise OranteError("没有文案，没法预演拆段", "先把文案贴进来。")
    r = _rule_for(engine, rule)
    texts = _normalize_and_split(text, engine, r)
    segs = _to_segments(texts, engine, r)
    return [
        {"s": sg["s"], "text": sg["text"], "duration": sg["duration"],
         "words": flow_split.word_count(sg["text"]), "chars": len(sg["text"])}
        for sg in segs
    ]
