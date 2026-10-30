# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""片段拼接的「分组依据」推断。纯函数，不碰 Qt / numpy，单测直接喂文件名。

用户把素材文件夹拖进来的那一刻，我们只看文件名（和文件夹名）就要猜出：
哪些片段属于同一条成品（组），组内按什么排序。以前要用户自己填「分组前缀」
「序号前缀」，现在只剩一个选择：

* ``auto``   自动识别（推荐）—— 本模块的 :func:`infer_groups`
* ``folder`` 按文件夹分组 —— 一个文件夹一组，组内按文件名自然序

自动识别的思路（不写死任何一种命名）：

1. 把每个文件名拆成「形状」+ 数字槽：``T0007-s001-p01`` → 形状 ``t#-s#-p#``，
   槽 [0007, 001, 01]。``简单的小天使 (1)-2`` → ``简单的小天使 (#)-#``。
   10 位以上的数字（时间戳）、20xx 开头的 8 位日期只算形状，不当候选槽。
2. 同形状的文件放一起，对每个槽 k 试一次「k 是组内序号」：组键 = 序号槽
   前面的那段文字（去掉贴着的 s / v / p 之类字母和分隔符），再加上所在文件夹。
3. 给每个假设打分：组内序号是否不重复、是否连续、是否从 1 开始、
   有没有一大堆只有一个片段的组。分最高的胜出，和第二名拉开多少就是置信度。
4. 小形状（只有一两个文件，比如重拍带时间戳的 ``-1789284630``）优先认到
   同文件夹里已经存在的组；实在认不出来的挂到该文件夹的主组上并标黄，
   让用户一键改。

结果里每个组、每个片段都带置信度；低于 :data:`LOW_CONFIDENCE` 的在界面上
标黄。用户手动拖动 / 右键改组时用 :func:`apply_overrides` 叠加，不重跑推断。
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

VIDEO_EXTS = (".mp4", ".mov", ".m4v", ".mkv")

GROUP_AUTO = "auto"
GROUP_FOLDER = "folder"
GROUP_MODES = (GROUP_AUTO, GROUP_FOLDER)

# 低于这个值的组 / 片段在界面里标黄，请用户看一眼
LOW_CONFIDENCE = 0.6

# 和 script_stitcher._folder_label 同一份通用名：这种文件夹名往上取一层
GENERIC_DIR_NAMES = frozenset({"片段", "clips", "素材", "video", "videos"})

# 序号槽前面贴着的这些字母会从组键里去掉（Tik001_v01 → 组 Tik001）
_MAX_LEAD = 6
_SEP_STRIP = " \t-_.,，、·#(（[【{~+"

_NUM_RE = re.compile(r"\d+")


# ----------------------------------------------------------------------
# 小工具
# ----------------------------------------------------------------------

def natkey(s):
    """自然排序键。用 (类型, 值) 元组，int 和 str 永远不会互相比较。"""
    out = []
    for t in re.split(r"(\d+)", str(s)):
        if not t:
            continue
        out.append((0, int(t), "") if t.isdigit() else (1, 0, t.lower()))
    return tuple(out)


def folder_label(folder):
    """人看得懂的文件夹短名。…\\T0138-v02\\clips → T0138-v02。"""
    folder = os.path.normpath(folder or "")
    base = os.path.basename(folder) or folder
    if base.strip().lower() in GENERIC_DIR_NAMES:
        up = os.path.basename(os.path.dirname(folder))
        if up:
            return up
    return base


def mark_to_prefix(mark):
    """旧设置里的 'Tik00x' / 'v0x' → 字面前缀 'Tik' / 'v'（与 script_stitcher 同规则）。"""
    s = str(mark or "").strip()
    while s and s[-1] in "xX#?*0123456789nN":
        s = s[:-1]
    return s


def is_video(name):
    return os.path.splitext(str(name))[1].lower() in VIDEO_EXTS


# ----------------------------------------------------------------------
# 文件名拆解
# ----------------------------------------------------------------------

@dataclass
class Slot:
    start: int          # 槽在 stem 里的起点（含贴着的前导字母之前的位置见 cut）
    end: int
    value: int
    raw: str
    kind: str = "num"   # num | paren | letter
    lead: str = ""      # 紧贴在数字前面的 ASCII 字母（s / v / Tik …）
    noise: bool = False  # 时间戳 / 日期：只算形状，不做候选

    @property
    def cut(self) -> int:
        """组键截到这里为止（去掉前导字母）。"""
        return self.start - len(self.lead)


def _is_noise(raw):
    if len(raw) >= 9:
        return True                       # 1789284630 这类时间戳
    if len(raw) == 8 and raw[:2] in ("19", "20"):
        return True                       # 20260916 日期
    return False


def tokenize(stem):
    """stem → (shape, [Slot])。shape 是数字换成 # 之后的小写文本。"""
    stem = str(stem)
    slots = []
    shape_parts = []
    pos = 0
    for m in _NUM_RE.finditer(stem):
        a, b = m.span()
        raw = m.group(0)
        lead = ""
        i = a
        while i > 0 and len(lead) < _MAX_LEAD and stem[i - 1].isascii() and stem[i - 1].isalpha():
            lead = stem[i - 1] + lead
            i -= 1
        before = stem[:a].rstrip()
        after = stem[b:].lstrip()
        kind = "paren" if (before.endswith(("(", "（")) and after.startswith((")", "）"))) else "num"
        slots.append(Slot(a, b, int(raw), raw, kind, lead, _is_noise(raw)))
        shape_parts.append(stem[pos:a])
        shape_parts.append("#")
        pos = b
        # 01a / 01b：数字后面紧跟单个字母，且字母后面不是字母 → 字母序号槽
        if b < len(stem) and stem[b].isascii() and stem[b].isalpha() and (
                b + 1 == len(stem) or not (stem[b + 1].isascii() and stem[b + 1].isalpha())):
            ch = stem[b].lower()
            slots.append(Slot(b, b + 1, ord(ch) - 96, ch, "letter", "", False))
            shape_parts.append("@")
            pos = b + 1
    shape_parts.append(stem[pos:])
    return "".join(shape_parts).lower(), slots


def clean_prefix(text):
    t = str(text or "")
    prev = None
    while prev != t:
        prev = t
        t = t.rstrip(_SEP_STRIP)
    return t


# ----------------------------------------------------------------------
# 结果结构
# ----------------------------------------------------------------------

@dataclass
class ClipInfo:
    path: str
    folder: str
    name: str                      # 文件名（含扩展名）
    order: tuple = ()              # 组内排序键
    seq: int | None = None         # 识别出来的序号（没有就是 None）
    confidence: float = 1.0
    reason: str = ""               # 为什么低置信 / 手动
    manual: bool = False

    def as_dict(self):
        return {
            "path": self.path, "name": self.name, "seq": self.seq,
            "confidence": round(float(self.confidence), 3),
            "reason": self.reason, "manual": self.manual,
        }


@dataclass
class GroupInfo:
    key: str                        # 表里显示 / 字典键（唯一）
    base: str                       # 成品名（重名改键时仍用原名）
    folder: str
    clips: list = field(default_factory=list)   # [ClipInfo]
    confidence: float = 1.0
    basis: str = ""                 # 人话：按什么分的组、按什么排的序
    warnings: list = field(default_factory=list)
    whole_folder: bool = False

    @property
    def paths(self):
        return [c.path for c in self.clips]

    @property
    def low(self):
        return self.confidence < LOW_CONFIDENCE or any(
            c.confidence < LOW_CONFIDENCE for c in self.clips)

    def as_dict(self):
        return {
            "key": self.key, "base": self.base, "folder": self.folder,
            "confidence": round(float(self.confidence), 3),
            "basis": self.basis, "warnings": list(self.warnings),
            "whole_folder": self.whole_folder,
            "clips": [c.as_dict() for c in self.clips],
        }


@dataclass
class GroupingResult:
    mode: str
    groups: list = field(default_factory=list)  # [GroupInfo] 已排序
    notes: list = field(default_factory=list)
    rules: dict = field(default_factory=dict)   # {shape: 序号槽下标 / -1}，可存成模板

    # 与 scan_groups_multi 同样的四件套，老代码直接吃
    def legacy(self):
        groups, folder_of, base_of = {}, {}, {}
        for g in self.groups:
            groups[g.key] = g.paths
            folder_of[g.key] = g.folder
            base_of[g.key] = g.base
        return groups, folder_of, base_of, list(self.notes)

    def group(self, key):
        for g in self.groups:
            if g.key == key:
                return g
        return None

    @property
    def n_clips(self):
        return sum(len(g.clips) for g in self.groups)

    @property
    def low_items(self):
        return [(g.key, c) for g in self.groups for c in g.clips
                if c.confidence < LOW_CONFIDENCE]

    @property
    def mean_confidence(self):
        n = self.n_clips
        if not n:
            return 0.0
        return sum(c.confidence for g in self.groups for c in g.clips) / n

    def as_dict(self):
        return {
            "mode": self.mode,
            "n_groups": len(self.groups),
            "n_clips": self.n_clips,
            "mean_confidence": round(self.mean_confidence, 3),
            "n_low": len(self.low_items),
            "groups": [g.as_dict() for g in self.groups],
            "notes": list(self.notes),
            "rules": dict(self.rules),
        }


# ----------------------------------------------------------------------
# 推断
# ----------------------------------------------------------------------

@dataclass
class _File:
    path: str
    folder: str
    name: str
    stem: str
    shape: str
    slots: list


def _parse(paths):
    files = []
    seen = set()
    for p in paths or []:
        if not p:
            continue
        p = os.path.normpath(str(p))
        k = os.path.normcase(p)
        if k in seen or not is_video(p):
            continue
        seen.add(k)
        name = os.path.basename(p)
        stem = os.path.splitext(name)[0].strip()
        shape, slots = tokenize(stem)
        files.append(_File(p, os.path.dirname(p), name, stem, shape, slots))
    return files


def _prefix(f, k):
    if k is None or k < 0:
        return ""
    return clean_prefix(f.stem[:f.slots[k].cut])


def _gid(f, k):
    return (os.path.normcase(f.folder), _prefix(f, k).lower())


def _group_score(vals):
    n = len(vals)
    u = len(set(vals))
    lo, hi = min(vals), max(vals)
    uniq = u / n
    contig = u / float(hi - lo + 1)
    start = 1.0 if lo <= 1 else (0.7 if lo <= 2 else 0.4)
    return 0.5 * uniq + 0.3 * contig + 0.2 * start


def _score(files, k, known):
    """k 当序号槽时这一簇的分数。k=-1 表示「整文件夹一组、按名字排」。"""
    buckets = {}
    for f in files:
        buckets.setdefault(_gid(f, k), []).append(f)
    n = len(files)
    total = 0.0
    singles = 0
    for members in buckets.values():
        if k < 0:
            g = 0.55
        else:
            g = _group_score([m.slots[k].value for m in members])
        total += g * len(members)
        if len(members) == 1:
            singles += 1
    s = total / n - 0.35 * (singles / float(len(buckets)))
    if k >= 0:
        slot = files[0].slots[k]
        if slot.kind == "paren":
            s += 0.06
        tail = files[0].stem[slot.end:]
        if not _NUM_RE.search(tail) or all(x.noise for x in files[0].slots[k + 1:]
                                           if x.kind != "letter"):
            s += 0.03                      # 序号多半在文件名末尾
    if known:
        hit = sum(1 for f in files if _gid(f, k) in known)
        s += 0.4 * hit / n
    return s, buckets


def _candidates(files):
    slots = files[0].slots
    out = []
    for k, sl in enumerate(slots):
        if sl.noise:
            continue
        vals = {f.slots[k].value for f in files}
        if len(files) > 1 and len(vals) == 1:
            continue                       # 全一样的槽不可能是序号
        out.append(k)
    return out


def _custom_rule(group_mark, order_mark):
    g_txt = str(group_mark or "").strip()
    o_txt = str(order_mark or "").strip()
    if not g_txt:
        return None
    gp = mark_to_prefix(g_txt)
    op = mark_to_prefix(o_txt)
    g_re = re.compile(re.escape(gp) + r"\s*(\d+)", re.I)
    o_re = re.compile(re.escape(op) + r"\s*(\d+)", re.I) if o_txt else None
    return g_re, o_re, gp


def infer_groups(paths, mode=GROUP_AUTO, *, rules=None, group_mark="",
                 order_mark="", overrides=None):
    """视频路径列表 → :class:`GroupingResult`。

    paths       完整路径。组永远不跨文件夹（成品写回各自文件夹的 _stitched）。
    mode        ``auto`` / ``folder``
    rules       模板：{shape: 序号槽下标}，命中的形状不再猜
    group_mark / order_mark  旧版「分组前缀 / 序号前缀」，非空时先按它分
    overrides   {path: 组键}，用户手动改过的
    """
    files = _parse(paths)
    res = GroupingResult(mode=mode if mode in GROUP_MODES else GROUP_AUTO)
    if not files:
        return res
    if res.mode == GROUP_FOLDER:
        _by_folder(files, res)
    else:
        _auto(files, res, rules or {}, group_mark, order_mark)
    if overrides:
        apply_overrides(res, overrides)
    return res


def _by_folder(files, res):
    buckets = {}
    order = []
    for f in files:
        fk = os.path.normcase(f.folder)
        if fk not in buckets:
            buckets[fk] = []
            order.append(fk)
        buckets[fk].append(f)
    raw = []
    for fk in order:
        members = sorted(buckets[fk], key=lambda f: natkey(f.name))
        folder = members[0].folder
        clips = [ClipInfo(f.path, f.folder, f.name, natkey(f.name), None, 1.0)
                 for f in members]
        raw.append(GroupInfo(
            key=os.path.basename(folder) or folder, base=os.path.basename(folder) or folder,
            folder=folder, clips=clips, confidence=1.0,
            basis="按文件夹分组，组内按文件名自然排序", whole_folder=True))
    _assign_keys(res, raw, keep_names=True)


def _auto(files, res, rules, group_mark, order_mark):
    rule = _custom_rule(group_mark, order_mark)
    placed = {}            # gid -> dict(folder, prefix, items=[(file, order, seq, conf, reason)], basis)
    rest = []
    if rule is not None:
        g_re, o_re, gp = rule
        for f in files:
            mg = g_re.search(f.stem)
            mo = None
            if mg and o_re is not None:
                mo = o_re.search(f.stem, mg.end()) or o_re.search(f.stem)
            if not mg or (o_re is not None and not mo):
                rest.append(f)
                continue
            key = (gp + "%03d" % int(mg.group(1))) if gp else str(int(mg.group(1)))
            seq = int(mo.group(1)) if mo else None
            gid = (os.path.normcase(f.folder), "rule:" + key.lower())
            ent = placed.setdefault(gid, {
                "folder": f.folder, "prefix": key, "items": [],
                "basis": "沿用旧前缀规则 %s / %s" % (group_mark, order_mark or "—"),
                "conf": 1.0, "rule": True})
            order = ((0, seq if seq is not None else 0), natkey(f.name))
            ent["items"].append([f, order, seq, 1.0, ""])
        res.notes.append("沿用旧版前缀规则：%s / %s（认不出的文件仍自动识别）"
                         % (group_mark, order_mark or "—"))
    else:
        rest = list(files)

    clusters = {}
    for f in rest:
        clusters.setdefault((f.shape, len(f.slots)), []).append(f)
    # 大簇先定，小簇往大簇已有的组上靠
    ordered = sorted(clusters.values(), key=lambda c: (-len(c), c[0].shape))
    known = set(placed)
    orphans = []
    for members in ordered:
        shape = members[0].shape
        forced = rules.get(shape)
        cands = _candidates(members)
        scored = []
        if forced is not None and (forced == -1 or forced in range(len(members[0].slots))):
            s, b = _score(members, int(forced), known)
            scored.append((s + 1.0, int(forced), b))
        else:
            for k in cands:
                s, b = _score(members, k, known)
                scored.append((s, k, b))
            s, b = _score(members, -1, known)
            scored.append((s, -1, b))
        scored.sort(key=lambda t: (-t[0], t[1] < 0))
        best_s, best_k, buckets = scored[0]
        second = scored[1][0] if len(scored) > 1 else best_s - 0.3
        margin = max(0.0, best_s - second)
        conf = max(0.0, min(1.0, 0.3 + 0.45 * min(best_s, 1.0) + margin))
        if forced is not None:
            conf = max(conf, 0.95)
        hit_known = any(gid in known for gid in buckets)
        singleton_cluster = len(members) == 1
        if singleton_cluster and not hit_known and forced is None:
            orphans.extend(members)
            continue
        res.rules[shape] = best_k
        for gid, mem in buckets.items():
            fold = mem[0].folder
            pre = _prefix(mem[0], best_k)
            ent = placed.get(gid)
            if ent is None:
                ent = placed[gid] = {
                    "folder": fold, "prefix": pre, "items": [],
                    "basis": _basis(mem[0], best_k), "conf": conf, "rule": False}
            else:
                ent["conf"] = min(ent["conf"], max(conf, 0.7)) if not ent.get("rule") else ent["conf"]
            for f in mem:
                if best_k >= 0:
                    sl = f.slots[best_k]
                    rest_vals = tuple((0, x.value) for x in f.slots[best_k + 1:] if not x.noise)
                    order = ((0, sl.value), _tail_letter(f.stem[sl.end:]), rest_vals, natkey(f.name))
                    seq = sl.value
                else:
                    order = ((1, 0), "", (), natkey(f.name))
                    seq = None
                item_conf = conf if not singleton_cluster else min(conf, 0.75)
                ent["items"].append([f, order, seq, item_conf, ""])
        known.update(buckets.keys())

    # 认不出来的单个文件：同文件夹有组就挂到最大的那组上（标黄），否则自成一组
    for f in orphans:
        fk = os.path.normcase(f.folder)
        same = [(gid, e) for gid, e in placed.items() if gid[0] == fk]
        if same:
            gid, ent = max(same, key=lambda t: len(t[1]["items"]))
            order = ((2, 0), "", (), natkey(f.name))
            ent["items"].append([f, order, None, 0.4,
                                 "文件名和同文件夹其他片段对不上，暂时放进这一组"])
        else:
            gid = (fk, "")
            ent = placed.setdefault(gid, {
                "folder": f.folder, "prefix": "", "items": [],
                "basis": "文件名里没有可用的序号，按文件夹分组、按文件名排序",
                "conf": 0.55, "rule": False})
            ent["items"].append([f, ((1, 0), "", (), natkey(f.name)), None, 0.55,
                                 "文件名里没有序号，按文件名排序"])

    raw = []
    for gid, ent in placed.items():
        items = sorted(ent["items"], key=lambda it: it[1])
        clips = [ClipInfo(f.path, f.folder, f.name, order, seq, c, reason)
                 for f, order, seq, c, reason in items]
        g = GroupInfo(key=ent["prefix"], base=ent["prefix"], folder=ent["folder"],
                      clips=clips, confidence=ent["conf"], basis=ent["basis"])
        _check_group(g)
        raw.append(g)

    # 某个文件夹只出了一个组、而且包含了这个文件夹的全部片段 → 用文件夹名当组名
    per_folder = {}
    for g in raw:
        per_folder.setdefault(os.path.normcase(g.folder), []).append(g)
    counts = {}
    for f in files:
        k = os.path.normcase(f.folder)
        counts[k] = counts.get(k, 0) + 1
    for fk, gs in per_folder.items():
        if len(gs) == 1 and len(gs[0].clips) == counts.get(fk, 0):
            gs[0].whole_folder = True
            gs[0].key = gs[0].base = folder_label(gs[0].folder)
        else:
            for g in gs:
                if not g.key:
                    g.key = g.base = folder_label(g.folder)
    _assign_keys(res, raw, keep_names=False)


def _tail_letter(s):
    m = re.match(r"^[-_ ]?([a-zA-Z])(?![a-zA-Z0-9])", s or "")
    return m.group(1).lower() if m else ""


def _basis(f, k):
    if k < 0:
        return "文件名里没有可用的序号，按文件夹分组、按文件名排序"
    sl = f.slots[k]
    pre = _prefix(f, k)
    what = {"paren": "括号里的数字 (n)", "letter": "末尾字母 a/b/c"}.get(sl.kind)
    if what is None:
        what = ("「%s」后面的数字" % sl.lead) if sl.lead else "第 %d 个数字" % (k + 1)
    if pre:
        return "组名取「%s」，组内按%s排序" % (pre, what)
    return "按文件夹分组，组内按%s排序" % what


def _check_group(g):
    seqs = [c.seq for c in g.clips if c.seq is not None]
    if seqs:
        dup = sorted({v for v in seqs if seqs.count(v) > 1})
        if dup:
            g.warnings.append("序号重复：%s（可能是重拍，拼接时按文案自动取舍）"
                              % ", ".join(str(v) for v in dup[:8]))
            g.confidence = max(0.0, g.confidence - 0.05)
        lo, hi = min(seqs), max(seqs)
        if hi - lo + 1 <= 400:
            gaps = [v for v in range(max(1, lo) if lo <= 2 else lo, hi + 1) if v not in seqs]
            if lo > 1 and lo <= 2:
                gaps = [v for v in range(1, lo)] + gaps
            if gaps:
                g.warnings.append("缺少序号：%s" % ", ".join(str(v) for v in gaps[:12])
                                  + (" …" if len(gaps) > 12 else ""))
                g.confidence = max(0.0, g.confidence - 0.05)
    if len(g.clips) == 1:
        g.warnings.append("这一组只有 1 个片段")
    for c in g.clips:
        c.confidence = min(c.confidence, 1.0)
        if c.confidence > g.confidence:
            c.confidence = g.confidence


def _assign_keys(res, raw, keep_names):
    """组键唯一化（与 scan_groups_multi 同一套改名规则），然后按自然序排。"""
    raw.sort(key=lambda g: (natkey(g.key), natkey(g.folder)))
    taken = set()
    for g in raw:
        key = g.key or "组"
        if key in taken:
            label = os.path.basename(os.path.dirname(os.path.normpath(g.folder))) or g.folder
            if not keep_names:
                label = folder_label(g.folder) if folder_label(g.folder) != key else label
            new = "%s(%s)" % (key, label)
            i = 2
            while new in taken:
                new = "%s(%s-%d)" % (key, label, i)
                i += 1
            res.notes.append("组名 %s 在多个文件夹里都有, 表里改叫 %s "
                             "(成品仍叫 %s, 写回它自己的文件夹)" % (key, new, g.base))
            key = new
        taken.add(key)
        g.key = key
        res.groups.append(g)


# ----------------------------------------------------------------------
# 手动修正
# ----------------------------------------------------------------------

def apply_overrides(res, overrides):
    """{path: 组键} 叠加到结果上。目标组不存在就新建（同文件夹）。"""
    if not overrides:
        return res
    norm = {os.path.normcase(os.path.normpath(p)): k for p, k in overrides.items()}
    moving = []
    for g in res.groups:
        keep = []
        for c in g.clips:
            target = norm.get(os.path.normcase(os.path.normpath(c.path)))
            if target is None:
                keep.append(c)
                continue
            c.manual = True
            c.confidence = 1.0
            c.reason = "手动指定"
            if target == g.key:
                keep.append(c)
            else:
                moving.append((target, c))
        g.clips = keep
    for target, c in moving:
        g = res.group(target)
        if g is None:
            g = GroupInfo(key=target, base=target, folder=c.folder, clips=[],
                          confidence=1.0, basis="手动新建的组")
            res.groups.append(g)
        g.clips.append(c)
        g.clips.sort(key=lambda x: x.order)
    res.groups = [g for g in res.groups if g.clips]
    for g in res.groups:
        if g.clips and all(c.manual for c in g.clips):
            g.confidence = 1.0
    return res


# ----------------------------------------------------------------------
# I/O：只列文件，不读内容
# ----------------------------------------------------------------------

def collect_videos(folders):
    """每个文件夹下一层的视频（不进子目录，与老扫描一致）。"""
    out = []
    seen = set()
    for folder in folders or []:
        folder = os.path.normpath(str(folder or ""))
        fk = os.path.normcase(folder)
        if not folder or fk in seen or not os.path.isdir(folder):
            continue
        seen.add(fk)
        try:
            names = os.listdir(folder)
        except OSError:
            continue
        for name in sorted(names, key=natkey):
            p = os.path.join(folder, name)
            if is_video(name) and os.path.isfile(p):
                out.append(p)
    return out


def scan_folders(folders, mode=GROUP_AUTO, **kw):
    """collect_videos + infer_groups；空文件夹在 notes 里记一行。"""
    paths = collect_videos(folders)
    res = infer_groups(paths, mode, **kw)
    have = {os.path.normcase(os.path.dirname(p)) for p in paths}
    for folder in folders or []:
        f = os.path.normpath(str(folder or ""))
        if f and os.path.isdir(f) and os.path.normcase(f) not in have:
            res.notes.append("%s 里没有视频，跳过" % f)
    return res


# ----------------------------------------------------------------------
# 拼接前检查：文案对齐、重复、缺号
# ----------------------------------------------------------------------

def precheck(res, scripts=None, extra_scripts=0, durations=None):
    """返回 {'groups': [...], 'problems': [...], 'ok': bool}。不碰磁盘。

    scripts        {组键: 文案}
    extra_scripts  粘贴时多出来、没有对应素材的文案条数
    durations      {path: 秒}，有就算每组总时长
    """
    scripts = scripts or {}
    durations = durations or {}
    rows, problems = [], []
    seen_names = {}
    for g in res.groups:
        txt = str(scripts.get(g.key) or "").strip()
        dur = None
        if durations:
            vals = [durations.get(c.path) for c in g.clips]
            if all(isinstance(v, (int, float)) for v in vals):
                dur = float(sum(vals))
        words = len(re.findall(r"\w+", txt)) if txt else 0
        row = {
            "key": g.key, "n": len(g.clips), "confidence": round(g.confidence, 3),
            "order": [c.name for c in g.clips], "duration": dur,
            "has_script": bool(txt), "script_words": words,
            "warnings": list(g.warnings),
        }
        if not txt:
            row["warnings"].append("没有文案，这一组会被跳过")
            problems.append("组 %s 没有文案" % g.key)
        if g.low:
            problems.append("组 %s 分组置信度低，请确认" % g.key)
        for c in g.clips:
            stem = os.path.splitext(c.name)[0].lower()
            seen_names.setdefault((os.path.normcase(g.folder), stem), []).append(g.key)
        rows.append(row)
    if extra_scripts:
        problems.append("有 %d 条文案找不到对应素材（粘贴时超出了组数）" % extra_scripts)
    return {"groups": rows, "problems": problems, "ok": not problems}
