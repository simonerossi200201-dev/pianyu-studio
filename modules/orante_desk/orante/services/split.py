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
    """本地视频模型（studio / LTX）：分段逻辑与 Grok 完全一致（按 max_chars_15s 字符切段，6 / 10 / 15 秒三档）。"""
    f = _rule_for("flow")
    g = _rule_for("grok")
    cap8 = int(f["dur_8s_max_words"])
    eff8 = int(f["max_chars"])
    cap15 = min(40, int(cap8 * 15 / 8))
    return {
        "engine": "studio",
        "max_chars_15s": int(g["max_chars_15s"]),
        "max_chars_10s": int(g["max_chars_10s"]),
        "dur_6s_max_chars": int(g["dur_6s_max_chars"]),
        "dur_4s_max_words": int(f["dur_4s_max_words"]),
        "dur_6s_max_words": int(f["dur_6s_max_words"]),
        "dur_8s_max_words": cap8,
        "dur_10s_max_words": min(cap15, int(cap8 * 10 / 8)),
        "dur_15s_max_words": cap15,
        "auto_tiers": list(_caps().STUDIO_AUTO_TIERS),
        "max_words": cap15,
        "max_chars_8s": eff8,
        "max_chars": int(g["max_chars_15s"]),
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
    """本地视频模型自动时长：同 Grok 分段时长规则（6 / 10 / 15 秒）。"""
    if "dur_6s_max_chars" in rule and "max_chars_10s" in rule:
        return _duration_grok(text, rule)
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
    else:
        # Grok 与本地视频模型（studio / LTX）统一按 Grok 规则切段
        max_c = int(rule.get("max_chars_15s") or _cfg("split.grok.max_chars_15s", 250))
        texts = _split_by_chars(src, max_c)
    texts = [t.strip() for t in texts if t and str(t).strip()]
    if not texts:
        raise OranteError("拆完一段都没有", "文案可能太短或全是空白。")
    _assert_conserved(raw, texts)
    return texts


def _to_segments(texts: list[str], engine: str, rule: dict) -> list[dict]:
    dur_fn = _duration_flow if engine == "flow" else _duration_grok
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
    "studio": {4: "dur_4s_max_words", 6: "dur_6s_max_chars", 8: "dur_8s_max_words",
               10: "max_chars_10s", 15: "max_chars_15s"},
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
    elif engine == "studio" and dur in (4, 8):
        rule["max_words"] = cap
        base = int(rule.get("max_chars_8s") or 145)
        rule["max_chars"] = base if dur <= 8 else min(300, int(round(base * dur / 8)))
        rule["max_chars_15s"] = rule["max_chars"]
    else:
        rule["max_chars_15s"] = cap        # grok / studio 按字数切
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
        unit = "字" if engine in ("grok", "studio") else "词"
        _log(job, f"手选时长 {pref} 秒：每段最多 {cap} {unit}，全部段都按 {pref} 秒")
    _log(job, f"拆段规则 {engine}：{rule}")
    texts = _normalize_and_split(raw, engine, rule, log=lambda m: _log(job, m))
    _check(job)
    segs = _to_segments(texts, engine, rule)
    if pref:
        over = 0
        for sg in segs:
            sg["duration"] = int(pref)
            n = (len(sg["text"] or "") if engine in ("grok", "studio")
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


class _LogJob:
    def __init__(self, log_fn=None):
        self._log_fn = log_fn

    def log(self, msg: str) -> None:
        if callable(self._log_fn):
            try:
                self._log_fn(msg)
            except Exception:
                pass

    def check(self) -> None:
        pass


def resplit_task_for_engine(
    task_id: str,
    new_engine: str,
    new_account_id: str | None = None,
    *,
    log=None,
    force: bool = False,
    mark_studio_pending: bool = False,
    studio_reason: str = "",
) -> dict:
    """当任务切换模型/引擎（如 Flow 拒图转本地模型 LTX、或在任务表/生成页换了引擎）时，
    自动按照新模型的分段规则重新分段，并自动完成配图编排。
    """
    from pathlib import Path
    from .. import db, events, tasks as taskmod
    from ..models import Manifest
    from . import arrange, pipeline, track as trackmod

    new_eng = (new_engine or "").strip().lower()
    if new_eng not in ("flow", "grok", "studio"):
        raise OranteError(f"未知引擎：{new_engine!r}")

    t = taskmod.get(task_id)
    acc_id = (new_account_id or t.get("account_id") or "").strip()
    if new_eng == "studio" and not new_account_id:
        acc_id = "STUDIO"

    db.run(
        "UPDATE tasks SET engine=?, account_id=?, updated=? WHERE id=?",
        (new_eng, acc_id, db.now(), task_id),
    )
    t = taskmod.get(task_id)
    d_str = t.get("dir") or ""
    if not d_str or not Path(d_str).is_dir():
        events.emit("tasks.changed")
        return {"resplit": False, "engine": new_eng, "reason": "not_prepared"}

    d = Path(d_str)
    m = Manifest.load(d)
    old_split_eng = str(((m.split or {}).get("rule") or {}).get("engine") or m.engine or "").strip().lower()
    m.engine = new_eng
    if acc_id:
        m.account = acc_id

    # 切到本地模型或 Grok 时，解除 Flow 的图片 blocked 状态，保留原图直接生成
    if new_eng in ("studio", "grok"):
        for im in m.images or []:
            if isinstance(im, dict) and im.get("status") == "blocked":
                im["status"] = "ok"
                im.pop("block_reason", None)
                if new_eng == "grok":
                    im.pop("flow_upload_refused", None)

    gm_col = pipeline.parse_gen_mode(t.get("gen_mode"))
    want_mode = gm_col if gm_col in pipeline.MODES else pipeline.default_mode_for_engine(new_eng)
    if new_eng in ("studio", "grok") and not gm_col:
        want_mode = pipeline.MODE_FIRST

    job_proxy = _LogJob(log)
    old_segs = list(m.segments or [])
    collected_indices = [
        idx for idx, sg in enumerate(old_segs)
        if sg.get("status") == "collected" and (sg.get("takes") or [])
    ]

    need_resplit = force or (old_split_eng != new_eng) or not old_segs
    if need_resplit:
        if not collected_indices:
            # 没有任何已收片段落：整篇文案按新模型规则重新拆段
            for sg in old_segs:
                sg.pop("duration_manual", None)
            m.segments = []
            new_split, new_segs = split_task(job_proxy, {**t, "engine": new_eng}, m)
            m.split = new_split
            m.segments = new_segs
            pipeline.set_mode(m, want_mode, remember=False)
            arrange.auto_arrange_manifest(m, d)
            _log(job_proxy, f"  · 已按 {_eng_name(new_eng)} 规则自动重新分段：{len( old_segs )} 段 → {len(m.segments)} 段")
        else:
            # 保留所有已收片的段（不论在开头、中间还是结尾），仅对未收片的连续段落按新模型规则智能重分段
            uncol_s = [int(sg.get("s") or 0) for sg in old_segs if sg.get("status") != "collected" and int(sg.get("s") or 0) > 0]
            m.save(d)
            if uncol_s:
                resplit_segments_for_engine(task_id, uncol_s, new_eng, log=log)
                m = Manifest.load(d)
            pipeline.set_mode(m, want_mode, remember=False)
    else:
        # 即使规则相同，也确保模式与配图完整
        pipeline.set_mode(m, want_mode, remember=False)
        if any(not (sg.get("image") or "").strip() for sg in (m.segments or [])):
            arrange.auto_arrange_manifest(m, d)

    # 清理未完成段的旧报错/阻塞状态
    for sg in m.segments or []:
        if sg.get("status") != "collected":
            if mark_studio_pending and new_eng == "studio":
                sg["status"] = "blocked"
                sg["studio_pending"] = True
                sg["studio_reason"] = studio_reason or "Flow 拒图自动转本地模型 LTX"
                sg["error"] = "已按本地模型规则重新分段，排给本地模型 LTX 出片"
            else:
                sg["status"] = "planned"
                sg["error"] = None
                sg["gen_id"] = None
                sg.pop("studio_pending", None)
                sg.pop("studio_reason", None)
            if not sg.get("prompt_locked"):
                sg["prompt_final"] = ""
                sg.pop("prompt_snap", None)
                sg.pop("prompt_rev", None)

    m.save(d)
    has_all_imgs = bool(m.segments) and all(bool((sg.get("image") or "").strip()) for sg in m.segments)
    cur_st = t.get("status") or "draft"
    if has_all_imgs and cur_st in ("prepared", "described", "generating"):
        if not collected_indices:
            taskmod.set_status(task_id, "arranged", force=(cur_st == "generating"))
    c = taskmod.remember_counts(d, m)
    taskmod.emit_seg_changed(task_id, c, force=True)
    events.emit("tasks.changed")
    return {
        "resplit": need_resplit,
        "engine": new_eng,
        "account_id": acc_id,
        "old_segments": len(old_segs),
        "segments": len(m.segments or []),
    }


def _archive_old_takes(d, sg: dict) -> list[str]:
    """将待重分段的旧成片移入 clips/_弃用，并返回更新后的 user_discarded sha 列表。"""
    import time
    from pathlib import Path
    from . import verify as verifymod

    d = Path(d)
    trash = d / "clips" / "_弃用"
    trash.mkdir(parents=True, exist_ok=True)
    disc = [str(x).lower() for x in (sg.get("user_discarded") or []) if x]
    for tk in sg.get("takes") or []:
        rel = str(tk.get("file") or "").replace("\\", "/")
        if not rel:
            continue
        pth = d / rel
        sha = str(tk.get("sha256") or "").lower()
        if not sha and pth.is_file():
            try:
                sha = verifymod.sha256_file(pth).lower()
            except OSError:
                sha = ""
        if sha and sha not in disc:
            disc.append(sha)
        if pth.exists():
            try:
                pth.rename(trash / f"{int(time.time())}-{pth.name}")
            except OSError:
                pass
    return disc[-20:]


def _shift_kept_segment_files(d, m, shift_records: list[tuple[dict, int, int]]) -> None:
    """当局部重分段导致已收片段落的段号 s 发生位移（old_s -> new_s）时，
    使用两阶段临时文件名安全重命名磁盘上的 clips/Txxxx-sNNN-pXX.mp4 与 tails/sNNN.png，
    绝不发生文件名覆盖冲突，并同步更新 takes[*]['file']。
    """
    from pathlib import Path
    d = Path(d)
    staged_clips: list[tuple[dict, Path, str]] = []
    staged_tails: list[tuple[Path, Path]] = []

    # Phase 1: 全部先重命名为带位移后缀的临时文件
    for sg, old_s, new_s in shift_records:
        if int(old_s) == int(new_s):
            continue
        old_tag = f"-s{int(old_s):03d}-"
        new_tag = f"-s{int(new_s):03d}-"
        for tk in sg.get("takes") or []:
            rel = str(tk.get("file") or "").replace("\\", "/")
            if not rel:
                continue
            if old_tag in rel:
                new_rel = rel.replace(old_tag, new_tag, 1)
            else:
                new_rel = f"clips/{m.clip_name({**sg, 's': int(new_s)})}"
            src_p = d / rel
            if src_p.is_file():
                tmp_p = d / f"{rel}.__shift_{old_s}_to_{new_s}__"
                try:
                    if tmp_p.exists():
                        tmp_p.unlink()
                    src_p.rename(tmp_p)
                    staged_clips.append((tk, tmp_p, new_rel))
                except OSError:
                    tk["file"] = new_rel
            else:
                tk["file"] = new_rel
        tail_src = d / "tails" / f"s{int(old_s):03d}.png"
        if tail_src.is_file():
            tail_tmp = d / "tails" / f"s{int(old_s):03d}.png.__shift_{old_s}_to_{new_s}__"
            tail_dst = d / "tails" / f"s{int(new_s):03d}.png"
            try:
                if tail_tmp.exists():
                    tail_tmp.unlink()
                tail_src.rename(tail_tmp)
                staged_tails.append((tail_tmp, tail_dst))
            except OSError:
                pass

    # Phase 2: 从临时文件落位到最终新段号文件名
    for tk, tmp_p, new_rel in staged_clips:
        dst_p = d / new_rel
        dst_p.parent.mkdir(parents=True, exist_ok=True)
        try:
            if dst_p.exists():
                dst_p.unlink()
            tmp_p.rename(dst_p)
        except OSError:
            pass
        tk["file"] = new_rel

    for tail_tmp, tail_dst in staged_tails:
        try:
            if tail_dst.exists():
                tail_dst.unlink()
            tail_tmp.rename(tail_dst)
        except OSError:
            pass


def pipeline_is_special(m) -> bool:
    try:
        from . import pipeline
        return pipeline.is_anchor_mode(m) or pipeline.is_chain_mode(m)
    except Exception:
        return False


def resplit_segments_for_engine(
    task_id: str,
    seg_nums: list[int] | set[int],
    target_engine: str,
    *,
    log=None,
) -> dict:
    """跨模型重生智能再分段：
    1) 当选用 Flow（如 Flow1 Omni，单段上限 8 秒 / ~20 词）重生原本 10s/15s 的长段落时：
       自动将该段的文案按 Flow 语速规则拆分为 2~N 段 ≤8 秒的子段落（如 15s -> 两段 6s/8s），
       保留任务内其他已收片段落并无损平移重命名成片文件，返回拆分后的新段号集合 want。
    2) 当选用本地模型 STUDIO (LTX) 或 Grok（支持 10s/15s）重生同一配图的连续多段未完成/重做段时：
       自动将连续短段合并重分为 ≤15 秒的大段，减少生成段数并保持连贯。
    """
    from pathlib import Path
    from .. import events, tasks as taskmod
    from ..models import Manifest
    from . import tails

    target_eng = (target_engine or "").strip().lower()
    want_in = sorted({int(x) for x in (seg_nums or []) if x})
    if target_eng not in ("flow", "grok", "studio") or not want_in:
        return {"resplit": False, "want": want_in}

    t = taskmod.get(task_id)
    d_str = t.get("dir") or ""
    if not d_str or not Path(d_str).is_dir():
        return {"resplit": False, "want": want_in}

    d = Path(d_str)
    m = Manifest.load(d)
    old_segs = list(m.segments or [])
    if not old_segs:
        return {"resplit": False, "want": want_in}

    want_set = set(want_in)
    job_proxy = _LogJob(log)
    rule = _rule_for(target_eng)
    dur_fn = _duration_flow if target_eng == "flow" else _duration_grok

    new_entries: list[tuple[dict, int | None, bool]] = []  # (seg_dict, orig_kept_s_or_None, is_wanted)
    changed_details: list[str] = []
    did_change_count = False
    did_change_dur = False

    if target_eng == "flow":
        max_w = int(rule.get("dur_8s_max_words") or 20)
        max_c = int(rule.get("max_chars") or 145)
        for sg in old_segs:
            s0 = int(sg.get("s") or 0)
            if s0 not in want_set:
                new_entries.append((sg, s0, False))
                continue
            txt = (sg.get("text") or "").strip()
            d0 = int(sg.get("duration") or 8)
            needs_split = bool(txt) and (d0 > 8 or not flow_split.fits(txt, max_w, max_c))
            sub_texts = []
            if needs_split:
                try:
                    sub_texts = _normalize_and_split(txt, "flow", rule)
                except Exception:
                    sub_texts = _split_flow(txt, rule)
                sub_texts = [st.strip() for st in sub_texts if st and st.strip()]
            if len(sub_texts) >= 2:
                did_change_count = True
                disc = _archive_old_takes(d, sg)
                sub_durs = []
                for idx_sub, stxt in enumerate(sub_texts):
                    sdur = int(_duration_flow(stxt, rule))
                    sub_durs.append(f"{sdur}s")
                    sub_sg = {
                        "s": s0,
                        "text": stxt,
                        "duration": sdur,
                        "image": sg.get("image") or "p01",
                        "role": sg.get("role") or "主体",
                        "shot": sg.get("shot") or "",
                        "prompt_final": "",
                        "status": "planned",
                        "gen_id": None,
                        "takes": [],
                        "error": None,
                        "bridge": {},
                        "mark": False,
                    }
                    if idx_sub == 0 and disc:
                        sub_sg["user_discarded"] = disc
                    if sg.get("image_locked"):
                        sub_sg["image_locked"] = True
                    new_entries.append((sub_sg, None, True))
                changed_details.append(f"s{s0:03d}({d0}s) → {len(sub_texts)} 段({'+'.join(sub_durs)})")
            else:
                if txt:
                    d1 = int(_duration_flow(txt, rule))
                else:
                    from ..engines import caps as _caps
                    d1 = int(_caps.snap_duration("flow", d0))
                if d1 != d0:
                    sg["duration"] = d1
                    sg.pop("duration_manual", None)
                    did_change_dur = True
                    changed_details.append(f"s{s0:03d}({d0}s → {d1}s)")
                new_entries.append((sg, s0, True))
    else:
        # target_eng in ("studio", "grok"): 支持将同一配图的连续待重生短段合并重分为 10s/15s 段，或单段按语速重算 6/10/15s
        i = 0
        n_old = len(old_segs)
        while i < n_old:
            sg = old_segs[i]
            s0 = int(sg.get("s") or 0)
            if s0 not in want_set:
                new_entries.append((sg, s0, False))
                i += 1
                continue
            p0 = sg.get("image") or ""
            grp = [sg]
            j = i + 1
            while j < n_old:
                nxt = old_segs[j]
                if int(nxt.get("s") or 0) in want_set and (nxt.get("image") or "") == p0 and (nxt.get("text") or "").strip():
                    grp.append(nxt)
                    j += 1
                else:
                    break
            grp_text = " ".join((x.get("text") or "").strip() for x in grp if (x.get("text") or "").strip()).strip()
            if len(grp) >= 2 and grp_text:
                try:
                    sub_texts = _normalize_and_split(grp_text, target_eng, rule)
                except Exception:
                    max_c = int(rule.get("max_chars_15s") or 250)
                    sub_texts = _split_by_chars(grp_text, max_c)
                sub_texts = [st.strip() for st in sub_texts if st and st.strip()]
                if sub_texts and len(sub_texts) != len(grp):
                    did_change_count = True
                    all_disc: list[str] = []
                    for g_sg in grp:
                        for sha in _archive_old_takes(d, g_sg):
                            if sha not in all_disc:
                                all_disc.append(sha)
                    sub_durs = []
                    for idx_sub, stxt in enumerate(sub_texts):
                        sdur = int(dur_fn(stxt, rule))
                        sub_durs.append(f"{sdur}s")
                        sub_sg = {
                            "s": s0,
                            "text": stxt,
                            "duration": sdur,
                            "image": p0 or "p01",
                            "role": grp[0].get("role") or "主体",
                            "shot": grp[0].get("shot") or "",
                            "prompt_final": "",
                            "status": "planned",
                            "gen_id": None,
                            "takes": [],
                            "error": None,
                            "bridge": {},
                            "mark": False,
                        }
                        if idx_sub == 0 and all_disc:
                            sub_sg["user_discarded"] = all_disc[-20:]
                        new_entries.append((sub_sg, None, True))
                    s_lbl = "+".join(f"s{int(x['s']):03d}" for x in grp)
                    changed_details.append(f"{s_lbl} → {len(sub_texts)} 段({'+'.join(sub_durs)})")
                    i = j
                    continue
            for g_sg in grp:
                gs0 = int(g_sg.get("s") or 0)
                gtxt = (g_sg.get("text") or "").strip()
                gd0 = int(g_sg.get("duration") or 8)
                gd1 = int(dur_fn(gtxt, rule)) if gtxt else gd0
                if gd1 != gd0:
                    g_sg["duration"] = gd1
                    g_sg.pop("duration_manual", None)
                    did_change_dur = True
                    changed_details.append(f"s{gs0:03d}({gd0}s → {gd1}s)")
                new_entries.append((g_sg, gs0, True))
            i = j

    if not did_change_count and not did_change_dur:
        return {"resplit": False, "want": want_in}

    final_segs: list[dict] = []
    shift_records: list[tuple[dict, int, int]] = []
    want_out: list[int] = []

    for new_idx, (seg_dict, orig_kept_s, is_wanted) in enumerate(new_entries, 1):
        if orig_kept_s is not None and int(orig_kept_s) != new_idx and not is_wanted:
            shift_records.append((seg_dict, int(orig_kept_s), new_idx))
        seg_dict["s"] = new_idx
        final_segs.append(seg_dict)
        if is_wanted:
            want_out.append(new_idx)

    if shift_records:
        _shift_kept_segment_files(d, m, shift_records)

    m.segments = final_segs
    if not pipeline_is_special(m):
        tails.assign_roles(m.segments)
    m.save(d)
    c = taskmod.remember_counts(d, m)
    taskmod.emit_seg_changed(task_id, c, force=True)
    events.emit("tasks.changed")

    if changed_details:
        _log(
            job_proxy,
            f"  · 智能跨模型分段（{_eng_name(target_eng)}）：{'，'.join(changed_details)}（总段数 {len(old_segs)} → {len(final_segs)} 段）",
        )
    return {
        "resplit": did_change_count,
        "dur_adjusted": did_change_dur,
        "want": want_out,
        "old_total": len(old_segs),
        "new_total": len(final_segs),
    }
