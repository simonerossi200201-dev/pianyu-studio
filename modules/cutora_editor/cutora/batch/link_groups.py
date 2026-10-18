# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q154 · 连线导入里的「预合成分组」：右边的外部视频怎么分成一组一组（纯数据，没有 Qt）。

* ``rows``：``[[源下标, …], …]`` —— 一行就是一个预合成组，行内顺序 = 拼接顺序。
  每个源下标在且只在一行里出现（一段只能属于一个组）。
* ``links``：``[(左边媒体下标, 行下标), …]``。
* 默认 ``cols=1``：一段一行（和老版本一模一样）。``cols=3`` 就是 1 2 3 一组、4 5 6 一组……
* 按文件名前缀分组：``video01_a / video01_b / video01_c`` → 一组；没有分隔符的各自一组。
* 行里的段可以随便挪：同行换位、跨行追加 / 插入、拖到空白处另起一组。空行自动消掉。
"""
from __future__ import annotations

import os
import re

# 尾巴 = 分隔符后面的「序号」：a / b / 01 / 12 / a1 / 1a / part12 / clip3 这类
_PREFIX_RE = re.compile(r"^(?P<prefix>.+?)[\s_\-\.]+(?P<tail>[A-Za-z]{1,2}|\d{1,4}|[A-Za-z]{1,8}\d{1,4}|\d{1,3}[A-Za-z])$")


def stem(path: str) -> str:
    return os.path.splitext(os.path.basename(str(path or "")))[0]


def prefix_of(path: str) -> str:
    """``video01_a.mp4`` → ``video01``；``clip 02.mp4`` → ``clip``；``alone.mp4`` → ``alone``。"""
    s = stem(path).strip()
    m = _PREFIX_RE.match(s)
    return (m.group("prefix") if m else s).rstrip(" _-.")


def rows_by_columns(n: int, cols: int) -> list:
    cols = max(1, int(cols or 1))
    n = max(0, int(n))
    return [list(range(i, min(n, i + cols))) for i in range(0, n, cols)]


def rows_by_prefix(paths) -> list:
    """同前缀的连成一行，行的先后按第一次出现的顺序（源本来就是自然排序好的）。"""
    order = []
    buckets = {}
    for i, p in enumerate(paths or []):
        key = prefix_of(p).lower()
        if key not in buckets:
            buckets[key] = []
            order.append(key)
        buckets[key].append(i)
    return [buckets[k] for k in order]


# ─────────── 用户给一个例子，软件学规律（Q154 第二版） ───────────

def pattern_from_example(example: str):
    """用户输入 ``video01`` → 规律 = 字面 ``video`` + 2 位数字。返回 (正则, 中文说明)；空 → (None, "")。

    规则一眼能懂：字母 / 中文 / 符号照抄，连续的数字换成「同样位数的数字」。
    ``video01`` 认 video00–video99；``v1`` 认 v0–v9；``intro`` 就是以 intro 开头；``2024-01`` 认 4 位数字-2 位数字。"""
    ex = str(example or "").strip()
    if not ex:
        return None, ""
    parts = []
    words = []
    for m in re.finditer(r"\d+|[^\d]+", ex):
        tok = m.group(0)
        if tok.isdigit():
            parts.append(r"\d{%d}(?!\d)" % len(tok))      # 位数要正好：video1 不认 video01
            words.append("%d 位数字" % len(tok))
        else:
            parts.append(re.escape(tok))
            words.append("「%s」" % tok)
    pattern = re.compile("^" + "".join(parts), re.IGNORECASE)
    return pattern, " + ".join(words)


def prefix_key(path: str, pattern) -> str:
    """文件名开头能对上规律 → 对上的那一截（小写）当组号；对不上 → ""。"""
    m = pattern.match(stem(path)) if pattern is not None else None
    return m.group(0).lower() if m else ""


def rows_by_pattern(paths, example: str) -> list:
    """按用户给的例子分组：开头同一截的连成一行；对不上规律的各自一行（排在后面）。例子为空 → 自动规则。"""
    pattern, _desc = pattern_from_example(example)
    if pattern is None:
        return rows_by_prefix(paths)
    order = []
    buckets = {}
    loners = []
    for i, p in enumerate(paths or []):
        key = prefix_key(p, pattern)
        if not key:
            loners.append([i])
            continue
        if key not in buckets:
            buckets[key] = []
            order.append(key)
        buckets[key].append(i)
    return [buckets[k] for k in order] + loners


def describe_grouping(paths, example: str) -> str:
    """给界面的一句话：这个例子怎么理解、分成几组、几段没对上。"""
    pattern, desc = pattern_from_example(example)
    n = len(paths or [])
    if pattern is None:
        rows = rows_by_prefix(paths)
        return "自动：按最后一个 _ - 空格 . 前面的部分分组 → %d 组" % len(rows) if n else "自动：按最后一个分隔符前面的部分分组"
    keys = [prefix_key(p, pattern) for p in paths or []]
    groups = {k for k in keys if k}
    miss = sum(1 for k in keys if not k)
    txt = "规律：%s（%s）" % (desc, "开头") 
    if n:
        txt += " → %d 组" % (len(groups) + miss)
        if miss:
            txt += "，%d 段开头对不上（各自一组）" % miss
        if not groups:
            txt += "；没有一段对得上，检查一下例子"
    return txt


def suggest_example(paths) -> str:
    """勾上的时候给个默认例子：每个文件名开头的「字母 + 数字」那一截都试一遍，挑能对上最多文件的那个
    （video01_a / video01_b / video02_a / ending → video01，不会因为 ending 排在最前面就选它）。"""
    paths = list(paths or [])
    if not paths:
        return ""
    best, best_n = "", -1
    seen = set()
    for p in paths:
        st = stem(p)
        m = re.match(r"^([^\d\s_\-\.]*\d+)", st)
        cand = m.group(1) if m else prefix_of(p)
        if not cand or cand.lower() in seen:
            continue
        seen.add(cand.lower())
        pat, _d = pattern_from_example(cand)
        n = sum(1 for q in paths if prefix_key(q, pat)) if pat is not None else 0
        if n > best_n:
            best, best_n = cand, n
    return best


def normalize(rows, n: int) -> list:
    """去掉越界 / 重复 / 空行；没分到行的源各自补一行（保证每个源恰好在一行里）。"""
    seen = set()
    out = []
    for row in rows or []:
        clean = []
        for i in row or []:
            try:
                i = int(i)
            except (TypeError, ValueError):
                continue
            if 0 <= i < n and i not in seen:
                seen.add(i)
                clean.append(i)
        if clean:
            out.append(clean)
    for i in range(n):
        if i not in seen:
            out.append([i])
    return out


def row_of(rows, src: int):
    for r, row in enumerate(rows):
        if src in row:
            return r, row.index(src)
    return None


def move(rows, srcs, dest_row, dest_pos) -> list:
    """把这些源（按它们现在的先后）挪到 dest_row 的 dest_pos 之前；dest_row == len(rows) = 另起一行。

    返回新的 rows（不改传入的）。空掉的行自动消失，下标会跟着变 —— 用返回值。"""
    rows = [list(r) for r in rows]
    want = []
    for row in rows:
        for i in row:
            if i in srcs and i not in want:
                want.append(i)
    if not want:
        return rows
    dest_row = max(0, min(int(dest_row), len(rows)))
    if dest_row == len(rows):
        rows.append([])
        dest_pos = 0
    target = rows[dest_row]
    # 目标行里排在插入点之前的、要搬走的段，先数出来：拿掉以后插入点要往前挪
    dest_pos = max(0, min(int(dest_pos), len(target)))
    shift = sum(1 for i in target[:dest_pos] if i in want)
    for row in rows:
        row[:] = [i for i in row if i not in want]
    pos = max(0, dest_pos - shift)
    rows[dest_row][pos:pos] = want
    return [r for r in rows if r]


def split_out(rows, srcs) -> list:
    """选中的每一段各自单独成一行（放在原来那一行后面）。"""
    rows = [list(r) for r in rows]
    out = []
    for row in rows:
        keep = [i for i in row if i not in srcs]
        gone = [i for i in row if i in srcs]
        if keep:
            out.append(keep)
        out.extend([[i] for i in gone])
    return out


def merge(rows, srcs) -> list:
    """选中的几段并成一行（按现在的先后），放在它们里最靠前那一行的位置。"""
    rows = [list(r) for r in rows]
    want = []
    first = None
    for r, row in enumerate(rows):
        for i in row:
            if i in srcs:
                want.append(i)
                first = r if first is None else first
    if len(want) < 2:
        return rows
    out = []
    for r, row in enumerate(rows):
        keep = [i for i in row if i not in want]
        if r == first:
            out.append(want)
        if keep:
            out.append(keep)
    return out


def remap_links(links, old_rows, new_rows) -> list:
    """行重排以后，连线跟着「行里第一段」走：那一段现在在哪一行，线就连到哪一行。"""
    out = []
    seen = set()
    for li, ri in links or []:
        if not (0 <= ri < len(old_rows)) or not old_rows[ri]:
            continue
        head = old_rows[ri][0]
        hit = row_of(new_rows, head)
        if hit is None:
            continue
        key = (int(li), int(hit[0]))
        if key not in seen:
            seen.add(key)
            out.append(key)
    return out


def auto_links(n_left: int, n_rows: int) -> list:
    return [(i, i) for i in range(max(0, min(int(n_left), int(n_rows))))]


def group_label(paths, row, index: int, example: str = "") -> str:
    """组的显示名：按例子分的组用对上的那一截（video02）；同前缀用前缀；否则「组 N」。"""
    pattern, _d = pattern_from_example(example)
    if pattern is not None:
        keys = [prefix_key(paths[i], pattern) for i in row if 0 <= i < len(paths)]
        if keys and all(keys) and len(set(keys)) == 1:
            m = pattern.match(stem(paths[row[0]]))
            return m.group(0) if m else keys[0]
        return "组 %d" % (int(index) + 1)
    names = [prefix_of(paths[i]) for i in row if 0 <= i < len(paths)]
    if names and len(set(n.lower() for n in names)) == 1 and names[0]:
        return names[0]
    return "组 %d" % (int(index) + 1)


def plan_from_rows(links, tasks, sources, rows) -> list:
    """→ [{"task_index": li, "groups": [[path, …], …], "names": [组名, …]}]，按左下标排。

    同一条媒体连了几行，就是几个组（几条新轨道）；行的先后按行下标。"""
    by_task = {}
    for li, ri in sorted(set((int(a), int(b)) for a, b in (links or []))):
        if not (0 <= li < len(tasks or [])) or not (0 <= ri < len(rows or [])):
            continue
        paths = [sources[i] for i in rows[ri] if 0 <= i < len(sources or [])]
        if not paths:
            continue
        by_task.setdefault(li, []).append((paths, group_label(sources, rows[ri], ri)))
    return [{"task_index": li, "groups": [p for p, _n in rows_], "names": [n for _p, n in rows_]}
            for li, rows_ in sorted(by_task.items())]
