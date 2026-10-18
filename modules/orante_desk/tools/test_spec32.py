# -*- coding: utf-8 -*-
"""规格 32：机位 × 动作覆盖库、配对、均匀轮转、refill_clause、间隔 class。"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEST_DATA = Path(tempfile.mkdtemp(prefix="orante-test-spec32-"))
os.environ["ORANTE_DATA"] = str(TEST_DATA)
sys.path.insert(0, str(ROOT))

from orante import config, db  # noqa: E402
from orante.jobs import load_all_services  # noqa: E402
from orante.services import arrange, coverage, expand, prompts  # noqa: E402

db.init()
load_all_services()
fail = 0


def ok(msg):
    print("  OK  ", msg)


def bad(msg):
    global fail
    fail += 1
    print("  FAIL", msg)


def _wipe_prompts():
    for k in (
        "prompts.expand", "prompts.refill", "prompts.refill_clause",
        "prompts.refill_merged", "prompts.migrate_notice",
        "prompts.format_version",
    ):
        try:
            config.reset(k)
        except Exception:
            pass


def test_library_intact():
    print("== 打包库：12×12、舞台坐标系、74 合法组合 ==")
    lib = coverage.default_lib()
    cams = lib.get("cameras") or []
    acts = lib.get("actions") or []
    if len(cams) != 12 or len(acts) != 12:
        bad(f"机位 {len(cams)} / 动作 {len(acts)}，应为 12/12")
        return
    if "动作都不变" in (lib.get("head") or ""):
        bad("head 还在锁动作")
        return
    if "从不看镜头" not in (lib.get("head") or ""):
        bad("head 没锁面向听众 / 不看镜头")
        return
    if "获奖级别" in (lib.get("tail") or ""):
        bad("tail 还是那串空形容词")
        return
    n = coverage.legal_count(lib)
    if n != 74:
        bad(f"合法组合 {n}，应为 74")
        return
    n2 = coverage.legal_count(lib, no_frontal=True)
    if n2 != 62:
        bad(f"关掉正脸后 {n2}，应为 62")
        return
    # 近景 rank1 不该配到手势 rank3（v2 近景是 02）
    near = next(c for c in cams if c["id"] == "02")
    hands = next(a for a in acts if a["id"] == "05")
    if coverage.pair_ok(near["rank"], hands["rank"]):
        bad("近景配了双手配合，配对规则坏了")
        return
    ok(f"12 机位 × 12 动作 → {n} 合法 / 关掉正脸 {n2}；head 锁面向听众")


def test_compose_and_clause():
    print("== 成品提示词 = head + 机位 + 动作 + tail；clause 在 head 后 ==")
    lib = coverage.default_lib()
    cam = next(c for c in lib["cameras"] if c["id"] == "04")
    act = next(a for a in lib["actions"] if a["id"] == "03")
    p = coverage.compose(lib, cam, act)
    if not p.startswith(lib["head"]):
        bad("不以 head 开头")
        return
    if cam["text"] not in p or act["text"] not in p:
        bad("机位或动作正文没拼进去")
        return
    if not p.endswith(lib["tail"].lstrip("。") and lib["tail"] or p):
        pass
    if lib["tail"] not in p:
        bad("tail 没拼进去")
        return
    clause = prompts.REFILL_CLAUSE_DEFAULT
    p2 = coverage.compose(lib, cam, act, clause)
    if not p2.startswith(lib["head"] + clause):
        bad(f"clause 不在 head 之后：{p2[len(lib['head']):len(lib['head'])+20]!r}")
        return
    used = expand._prompt_block(True)
    if not used.startswith(lib["head"]) or not used[len(lib["head"]):].startswith(clause):
        bad("_prompt_block(True) clause 位置不对")
        return
    ok("拼装顺序对，补位那一句还在 head 后面")


def test_pick_round_robin():
    print("== 挑 12 条：机位不重复，二元组不重复 ==")
    lib = coverage.default_lib()
    picks = coverage.pick(lib, 12)
    if len(picks) != 12:
        bad(f"只挑到 {len(picks)}")
        return
    cams = [p["cov"]["camera"] for p in picks]
    keys = [p["key"] for p in picks]
    if len(set(cams)) != 12:
        bad(f"机位有重复：{cams}")
        return
    if len(set(keys)) != 12:
        bad(f"二元组有重复：{keys}")
        return
    acts = [p["cov"]["action"] for p in picks]
    if len(set(acts)) < 8:
        bad(f"动作覆盖太窄：{acts}")
        return
    more = coverage.pick(lib, 12, used_across=set(keys), avoid=set(keys))
    overlap = set(p["key"] for p in more) & set(keys)
    if overlap:
        bad(f"跨轮次还拿了用过的：{overlap}")
        return
    ok(f"12 机位全露：{cams}；动作 {acts}")


def test_get_block_dict():
    print("== get_block('expand') 是覆盖库对象 ==")
    _wipe_prompts()
    config.set("prompts.format_version", 6)
    got = prompts.get_block("expand")
    if not isinstance(got, dict) or not got.get("cameras"):
        bad(f"不是库：{type(got)}")
        return
    prompts.set_block("expand", got)
    got2 = prompts.get_block("expand")
    if len(got2["cameras"]) != 12:
        bad("set/get 丢了行")
        return
    ok(f"覆盖库 {len(got2['cameras'])} 机位 / {len(got2['actions'])} 动作")


def test_format_v6():
    print("== 格式升到 v6：旧字符串先备份再重置 ==")
    _wipe_prompts()
    config.set("prompts.expand", "USER EXPAND 手写 24 行")
    config.set("prompts.format_version", 5)
    prompts._migrate_format()
    bak = config.get("prompts.expand.bak.v5")
    if bak != "USER EXPAND 手写 24 行":
        bad(f"备份不对：{bak!r}")
        return
    got = prompts.get_block("expand")
    if not isinstance(got, dict):
        bad("升级后不是覆盖库")
        return
    ok("v5 字符串 → bak.v5，expand 换成覆盖库")


def test_class_spacing():
    print("== 间隔排列：相邻尽量不要同一个 class ==")
    images = [
        {"p": "p01", "role": "主体", "status": "ok",
         "file": "images/p01.jpg", "cov": {"class": "A", "camera": "01"}},
        {"p": "p02", "role": "主体", "status": "ok",
         "file": "images/p02.jpg", "cov": {"class": "A", "camera": "02"}},
        {"p": "p03", "role": "主体", "status": "ok",
         "file": "images/p03.jpg", "cov": {"class": "B", "camera": "04"}},
    ]
    rows = []
    for i, p in enumerate(["p01", "p02", "p01", "p02"], 1):
        rows.append({
            "s": i, "label": "s%03d" % i, "p_new": p, "p_old": p,
            "why": "自动", "locked": False, "status": "planned",
            "state": "planned", "role": "主体", "dur": 6, "changed": False,
        })
    res = {"rows": rows, "warnings": [], "summary": {}}
    tr = {"hold": 1, "jitter": 0, "unit": "seg", "pins": {},
          "pool": ["p01", "p02", "p03"], "no_adjacent_repeat": True}
    arrange.enforce_spacing(res, tr, images)
    ps = [r["p_new"] for r in res["rows"]]
    same_img = [i for i in range(len(ps) - 1) if ps[i] == ps[i + 1]]
    if same_img:
        bad(f"相邻还是同一张：{ps}")
        return
    by = {im["p"]: im["cov"]["class"] for im in images}
    same_cls = [i for i in range(len(ps) - 1)
                if by.get(ps[i]) and by.get(ps[i]) == by.get(ps[i + 1])]
    if same_cls:
        bad(f"有得换却没换 class：{ps}  class {[by.get(x) for x in ps]}")
        return
    ok(f"轨 {ps}，class {[by.get(x) for x in ps]}")


def test_dup_dist_default():
    print("== expand.dup_dist 是正整数 ==")
    v = config.DEFAULTS.get("expand.dup_dist")
    if not isinstance(v, int) or v < 1:
        bad(f"默认 {v}")
        return
    ok(f"dup_dist = {v}")


def main() -> int:
    print(f"ORANTE_DATA = {TEST_DATA}")
    test_library_intact()
    test_compose_and_clause()
    test_pick_round_robin()
    test_get_block_dict()
    test_format_v6()
    test_class_spacing()
    test_dup_dist_default()
    print()
    if fail:
        print(f"失败 {fail} 项")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
