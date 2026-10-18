# -*- coding: utf-8 -*-
"""
拼接结果体检 — 只读，不改任何东西
====================================
读 _stitched/cutlist.json，把"缺失文案"报告里靠不住的地方挑出来。

为什么需要它：命中率(ratio)的分母是【该片段转录出了多少词】，不是【它实际
说了多少词】。所以一个 8 秒的片段如果只被转录出 3 个词、这 3 个词都对，
ratio 就是 1.00 —— 显示"命中100%"，实际上 7 秒的内容根本没进入匹配，
那部分文案随后被判成"缺失"。转录失败得越彻底，分数越漂亮。

本工具做四件事：
  A. 词速离群检测   —— 找出转录很可能没转全的片段（误报的源头）
  B. 覆盖空洞归因   —— 每个"缺失"是挨着可疑片段(疑似误报)还是夹在正常片段之间(疑似真缺)
  C. 漏报风险检测   —— 认领了文案却被内部剪刀切掉的片段（报告说完整，成品其实缺）
  D. 汇总建议

用法：
    python stitch_checkup.py  <视频输出文件夹 或 cutlist.json 路径>
    不给参数则提示输入。
"""

import json
import os
import statistics
import sys

# 词速下限：低于这个值基本可以断定转录没转全。
# 依据：正常朗读 2~3 词/秒；实测一批 211 个片段中位 2.62、次低 1.33，
# 而转录失败那个是 0.38 —— 断崖式离群。
ABS_SLOW = 1.00
# 相对下限：低于全批中位数的这个比例也算可疑（防止整批都慢时绝对值失效）
REL_SLOW = 0.40
# 认领区间之间多少个词的洞才算"缺失"（与主程序 k-j>=3 保持一致）
HOLE_MIN = 3


def find_cutlist(arg):
    """接受 cutlist.json 本身、_stitched 目录、或 视频输出 目录。"""
    p = os.path.abspath(arg.strip().strip('"').strip("'"))
    if os.path.isfile(p):
        return p
    for cand in (os.path.join(p, "cutlist.json"),
                 os.path.join(p, "_stitched", "cutlist.json")):
        if os.path.isfile(cand):
            return cand
    return None


def est_words(it):
    """估算该片段被转录出多少个词。

    cutlist 里没有直接存转录词数，但存了 ratio = 命中词数/转录词数，
    以及 s_runs = 该片段认领到的文案区间。认领词数 ≈ 命中词数
    （没被别人抢走的部分），所以 转录词数 ≈ 认领词数 / ratio。
    对"转录严重不全"这个判断来说，这个估算足够准。
    """
    claimed = sum(int(e) - int(s) for s, e in (it.get("s_runs") or []))
    r = float(it.get("ratio") or 0.0)
    if r <= 0:
        return claimed, 0.0
    return claimed, claimed / r


def analyze(data):
    """返回 (片段体检表, 每组空洞归因)。"""
    rows = []
    for g, items in data.items():
        for it in items:
            claimed, est = est_words(it)
            dur = float(it.get("dur") or 0.0) or 0.01
            rows.append({
                "g": g, "name": it.get("name", ""),
                "dur": dur, "claimed": claimed, "est": est,
                "wps": est / dur,
                "ratio": float(it.get("ratio") or 0.0),
                "keep": bool(it.get("keep", True)),
                "recycle": bool(it.get("recycle")),
                "level": it.get("level", ""),
                "segs": it.get("segs") or [],
                "s_runs": it.get("s_runs") or [],
                "s_start": it.get("s_start"), "s_end": it.get("s_end"),
                "note": it.get("note", ""), "cuts": it.get("cuts") or [],
            })

    live = [r for r in rows if r["keep"] and r["est"] > 0]
    med = statistics.median([r["wps"] for r in live]) if live else 0.0
    thr = max(ABS_SLOW, med * REL_SLOW)
    for r in rows:
        r["suspect"] = (r["keep"] and r["est"] > 0 and r["wps"] < thr)
        # 认领了却被内部剪掉 → 报告说讲过、成品其实没有 = 漏报风险
        kept_sec = sum(b - a for a, b in r["segs"]) if r["segs"] else r["dur"]
        r["kept_frac"] = kept_sec / r["dur"]
        r["underrun"] = (r["keep"] and len(r["segs"]) > 1
                         and r["kept_frac"] < 0.85)

    # ---- 每组空洞归因 ----
    # 注意：cutlist 里没存文案总长度，所以只能推到"最后一个被认领的词"为止。
    # 文案尾部整段没人讲的情况（组404 那种）在这里看不见 —— 交给下面的
    # merge_reported() 从 缺失文案清单.txt 里补进来。
    holes = []
    for g, items in data.items():
        covered = set()
        for it in items:
            if not it.get("keep", True):
                continue
            for s, e in (it.get("s_runs") or []):
                covered.update(range(int(s), int(e)))
        if not covered:
            continue
        M = max(covered) + 1
        grow = [r for r in rows if r["g"] == g]
        j = 0
        while j < M:
            if j in covered:
                j += 1
                continue
            k = j
            while k < M and k not in covered:
                k += 1
            if k - j >= HOLE_MIN:
                # 归因：洞的两端紧挨着谁？那个片段可疑吗？
                near = []
                for r in grow:
                    if r["s_end"] is not None and abs(int(r["s_end"]) - j) <= 1:
                        near.append(r)
                    if r["s_start"] is not None and abs(int(r["s_start"]) - k) <= 1:
                        near.append(r)
                sus = [r for r in near if r["suspect"]]
                holes.append({
                    "g": g, "s": j, "e": k, "n": k - j,
                    "verdict": "疑似误报(相邻片段转录不全)" if sus else "疑似真缺失",
                    "near": sorted({r["name"] for r in near}),
                    "suspect_near": sorted({r["name"] for r in sus}),
                })
            j = k
    return rows, holes, med, thr


def merge_reported(holes, rows, cl_dir):
    """把主程序导出的 缺失文案清单.txt 合并进来，逐条给出可信度判定。

    为什么要合并：cutlist 里没有文案总长度，本工具算不出"文案尾部整段没人讲"
    这类空洞；而清单里有。反过来清单只说"缺了"，不说"可不可信"。两边一拼，
    才能对每一条报告给出结论。
    """
    path = os.path.join(cl_dir, "缺失文案清单.txt")
    if not os.path.isfile(path):
        return holes
    try:
        txt = open(path, encoding="utf-8").read()
    except Exception:
        return holes
    import re
    cur_g = None
    known = {(h["g"], h["s"], h["e"]) for h in holes}
    for line in txt.splitlines():
        m = re.search(r"【组\s*(\S+?)】", line)
        if m:
            cur_g = m.group(1)
            continue
        m = re.search(r"文案第\s*(\d+)\s*~\s*(\d+)\s*词", line)
        if not m or cur_g is None:
            continue
        s, e = int(m.group(1)) - 1, int(m.group(2))
        if (cur_g, s, e) in known:
            continue
        grow = [r for r in rows if r["g"] == cur_g]
        near = [r for r in grow
                if (r["s_end"] is not None and abs(int(r["s_end"]) - s) <= 1)
                or (r["s_start"] is not None and abs(int(r["s_start"]) - e) <= 1)]
        sus = [r for r in near if r["suspect"]]
        holes.append({
            "g": cur_g, "s": s, "e": e, "n": e - s,
            "verdict": ("疑似误报(相邻片段转录不全)" if sus else "疑似真缺失"),
            "near": sorted({r["name"] for r in near}),
            "suspect_near": sorted({r["name"] for r in sus}),
            "from_report": True,
        })
    holes.sort(key=lambda h: (str(h["g"]), h["s"]))
    return holes


def report(rows, holes, med, thr, out_path):
    L = []
    A = L.append
    live = [r for r in rows if r["keep"]]
    suspects = [r for r in rows if r["suspect"]]
    underruns = [r for r in rows if r["underrun"]]

    A("拼接结果体检报告（只读，未修改任何文件）")
    A("=" * 68)
    A(f"片段总数 {len(rows)}，保留 {len(live)}，回收 {len(rows) - len(live)}")
    A(f"词速中位数 {med:.2f} 词/秒；可疑阈值 {thr:.2f} 词/秒")
    A("")
    A("【A】转录可疑片段 —— 误报的源头")
    A("-" * 68)
    A("判据：估算转录词数 / 时长 明显低于全批中位。这类片段会显示很高的")
    A("      命中率（分母小），但它没转出来的那部分文案会被算成“缺失”。")
    A("")
    if not suspects:
        A("  ✓ 没有发现转录明显不全的片段。")
    else:
        A(f"{'词速':>6} {'估转录':>6} {'认领':>5} {'时长':>6} {'命中率':>6}  组     文件")
        for r in sorted(suspects, key=lambda x: x["wps"]):
            A(f"{r['wps']:6.2f} {r['est']:6.0f} {r['claimed']:5d} "
              f"{r['dur']:6.1f} {r['ratio']:6.2f}  {r['g']:>5}  {r['name']}")
        A("")
        A(f"  共 {len(suspects)} 个。建议：这些片段【不应参与缺失判定】，")
        A("  应整段保留并标黄人工复核，或换参数重转一次。")
    A("")

    A("【B】覆盖空洞归因 —— 每处“缺失”到底可不可信")
    A("-" * 68)
    if not holes:
        A("  ✓ 没有 >= %d 词的覆盖空洞。" % HOLE_MIN)
    else:
        fake = [h for h in holes if h["verdict"].startswith("疑似误报")]
        real = [h for h in holes if not h["verdict"].startswith("疑似误报")]
        A(f"  共 {len(holes)} 处：疑似误报 {len(fake)}，疑似真缺失 {len(real)}")
        A("")
        for h in holes:
            A(f"  组 {h['g']}  文案第 {h['s'] + 1}~{h['e']} 词（{h['n']} 词）"
              f"  → {h['verdict']}")
            if h["near"]:
                A(f"      相邻片段: {', '.join(h['near'])}")
            if h["suspect_near"]:
                A(f"      其中转录可疑: {', '.join(h['suspect_near'])}"
                  f"   ← 这处大概率是误报，别去重做")
    A("")

    A("【C】漏报风险 —— 认领了文案，却被内部剪刀切掉")
    A("-" * 68)
    A("机制：缺失判定用的是“认领区间”(s_runs)，成品用的是“保留区间”(segs)。")
    A("      两者不等价：认领了但被剪掉 → 报告说完整，成品其实缺了那句。")
    A("")
    if not underruns:
        A("  ✓ 没有明显的认领/保留不一致。")
    else:
        A(f"{'保留占比':>8} {'刀数':>4} {'时长':>6}  组     文件")
        for r in sorted(underruns, key=lambda x: x["kept_frac"]):
            A(f"{r['kept_frac'] * 100:7.0f}% {len(r['segs']) - 1:4d} "
              f"{r['dur']:6.1f}  {r['g']:>5}  {r['name']}")
            for c in r["cuts"][:2]:
                A(f"          {c[:100]}")
        A("")
        A(f"  共 {len(underruns)} 个，需确认剪掉的部分是不是真重复。")
    A("")

    A("【D】结论")
    A("-" * 68)
    fake_n = len([h for h in holes if h["verdict"].startswith("疑似误报")])
    A(f"  · 缺失报告 {len(holes)} 处，其中约 {fake_n} 处不可信（相邻片段转录不全）")
    A(f"  · 转录可疑片段 {len(suspects)} 个 / 共 {len(rows)} 个"
      f"（{len(suspects) / max(1, len(rows)) * 100:.1f}%）")
    A(f"  · 认领/保留不一致 {len(underruns)} 个（漏报风险）")
    A("")
    A("  注：cutlist.json 里没有保存【文案原文】和【每个片段的转录文本】，")
    A("  所以本体检只能靠词速和区间反推，无法逐词复盘。建议在主程序里加一个")
    A("  debug 落盘（转录文本 + 文案 token），之后任何一处误判都能一眼定位。")

    txt = "\n".join(L)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(txt)
    return txt


def main():
    arg = sys.argv[1] if len(sys.argv) > 1 else input(
        "把 视频输出 文件夹 或 cutlist.json 拖进来后回车：\n> ")
    cl = find_cutlist(arg)
    if not cl:
        print("没找到 cutlist.json。请指向 视频输出 文件夹，或它下面的 _stitched。")
        return 1
    print(f"读取：{cl}")
    with open(cl, encoding="utf-8") as f:
        data = json.load(f)
    rows, holes, med, thr = analyze(data)
    holes = merge_reported(holes, rows, os.path.dirname(cl))
    out = os.path.join(os.path.dirname(cl), "体检报告.txt")
    txt = report(rows, holes, med, thr, out)
    print()
    print(txt)
    print()
    print(f"报告已保存：{out}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        import traceback
        traceback.print_exc()
        input(f"\n出错：{e}\n按回车关闭…")
