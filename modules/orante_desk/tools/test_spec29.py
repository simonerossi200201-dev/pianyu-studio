# -*- coding: utf-8 -*-
"""规格 29：提示词自动保存相关的迁移备份、扩图/补位合并、跨任务队列扫描。"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEST_DATA = Path(tempfile.mkdtemp(prefix="orante-test-spec29-"))
os.environ["ORANTE_DATA"] = str(TEST_DATA)
sys.path.insert(0, str(ROOT))

from orante import config, db  # noqa: E402
from orante import tasks as taskmod  # noqa: E402
from orante.jobs import load_all_services  # noqa: E402
from orante.models import Manifest  # noqa: E402
from orante.services import descqueue, expand, prompts  # noqa: E402

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
        "prompts.format_version", "prompts.refill.bak",
        "prompts.expand.bak.v4", "prompts.refill.bak.v4",
        "prompts.expand.bak.v0",
    ):
        try:
            config.reset(k)
        except Exception:
            pass


def test_insert_clause():
    print("== refill_clause 插入位置 ==")
    from orante.services import coverage
    lib = prompts.get_coverage()
    head = lib.get("head") or ""
    clause = prompts.REFILL_CLAUSE_DEFAULT
    picks = coverage.pick(lib, 1, clause=clause)
    if not picks:
        bad("覆盖库拼不出提示词")
        return
    first = picks[0]["prompt"]
    if not first.startswith(head):
        bad(f"不以 head 开头：{first[:40]!r}")
        return
    rest = first[len(head):]
    if not rest.startswith(clause):
        bad(f"clause 不在 head 之后：{rest[:40]!r}")
        return
    used = expand._prompt_block(True)
    if not used.startswith(head) or not used[len(head):].startswith(clause):
        bad("_prompt_block(True) clause 位置不对")
        return
    custom = "保持人物形象不变，只更换拍摄距离为中景。"
    got = prompts.insert_refill_clause(custom, clause)
    want = "保持人物形象不变，" + clause + "只更换拍摄距离为中景。"
    if got != want:
        bad(f"自定义行插入位置不对：{got!r}")
        return
    empty = prompts.insert_refill_clause(custom, "")
    if empty != custom:
        bad("空 clause 应该原样返回")
        return
    ok(f"clause 在 head 之后")


def test_format_migrate_backup():
    print("== 格式升级先备份再重置 ==")
    _wipe_prompts()
    config.set("prompts.expand", "USER EXPAND 手写")
    config.set("prompts.format_version", 4)
    prompts._migrate_format()
    bak = config.get("prompts.expand.bak.v4")
    if bak != "USER EXPAND 手写":
        bad(f"备份不对：{bak!r}")
        return
    if prompts._cfg_has("prompts.expand"):
        bad("升级后用户 expand 还在，应该已 reset")
        return
    notices = prompts.migrate_notices()
    if not any(n.get("kind") == "format_reset" and n.get("name") == "expand" for n in notices):
        bad(f"没有 format_reset 横幅：{notices!r}")
        return
    prompts.restore_backup("expand")
    if prompts.get_block("expand") != "USER EXPAND 手写":
        bad("恢复没把备份写回 expand")
        return
    prompts.ack_notice("format_reset", "expand")
    if prompts.migrate_notices():
        bad("知道了之后横幅还在")
        return
    ok("备份 → 重置 → 横幅 → 恢复 → 知道了")


def test_merge_refill_only():
    print("== 只改过 refill：搬到 expand 并抽出 clause ==")
    _wipe_prompts()
    config.set("prompts.format_version", 6)
    from orante.services import coverage as covlib
    lib = covlib.default_lib()
    sample = (lib.get("head") or "") + (lib.get("cameras") or [{}])[0].get("text", "中景") + (lib.get("tail") or "")
    raw = sample[:len(lib.get("head") or "")] + prompts.REFILL_CLAUSE_DEFAULT + sample[len(lib.get("head") or ""):]
    config.set("prompts.refill", raw)
    prompts._merge_refill()
    if not prompts._cfg_has("prompts.expand"):
        bad("没把 refill 搬到 expand")
        return
    got = prompts.get_block("expand")
    if prompts.REFILL_CLAUSE_DEFAULT in got:
        bad("搬过去之后还留着补位那一句")
        return
    if prompts.get_block("refill_clause") != prompts.REFILL_CLAUSE_DEFAULT:
        bad("没把那一句填进 refill_clause")
        return
    if prompts._cfg_has("prompts.refill"):
        bad("prompts.refill 还在")
        return
    ok("refill → expand，clause 抽出")


def test_merge_both_custom():
    print("== 两个都改过：保留 expand，refill 进 bak ==")
    _wipe_prompts()
    config.set("prompts.format_version", 6)
    config.set("prompts.expand", "EXPAND 手写")
    config.set("prompts.refill", "REFILL 手写")
    prompts._merge_refill()
    if prompts.get_block("expand") != "EXPAND 手写":
        bad("expand 被覆盖了")
        return
    if config.get("prompts.refill.bak") != "REFILL 手写":
        bad("refill 没进 bak")
        return
    notices = prompts.migrate_notices()
    if not any(n.get("kind") == "refill_merged" for n in notices):
        bad(f"没有 refill_merged 横幅：{notices!r}")
        return
    ok("双改过：expand 保留，refill.bak + 横幅")


def test_merge_neither():
    print("== 两个都没改过：什么都不做 ==")
    _wipe_prompts()
    config.set("prompts.format_version", 6)
    prompts._merge_refill()
    if prompts._cfg_has("prompts.expand") or prompts._cfg_has("prompts.refill"):
        bad("没改过却写进了 settings")
        return
    if any(n.get("kind") == "refill_merged" for n in prompts.migrate_notices()):
        bad("没改过却出了合并横幅")
        return
    got = prompts.get_block("expand")
    if not isinstance(got, dict) or not got.get("cameras"):
        bad(f"默认 expand 不是覆盖库：{type(got)}")
        return
    ok("都没改过，expand 仍是默认覆盖库")


def test_all_blocks_no_refill():
    print("== all_blocks 去掉 refill，加上 refill_clause ==")
    blocks = prompts.all_blocks()
    if "refill" in blocks:
        bad("all_blocks 还在返回 refill")
        return
    if "refill_clause" not in blocks:
        bad("all_blocks 没有 refill_clause")
        return
    if blocks["refill_clause"] != prompts.REFILL_CLAUSE_DEFAULT:
        bad(f"默认 clause 不对：{blocks['refill_clause']!r}")
        return
    try:
        prompts.set_block("refill", "x")
        bad("set_block('refill') 应该拒绝")
    except Exception:
        ok("EDITABLE 已去掉 refill")


def _mk_task(tid, name, row, images, descs):
    db.run("DELETE FROM tasks WHERE id=?", (tid,))
    d = TEST_DATA / "ws" / tid
    d.mkdir(parents=True, exist_ok=True)
    (d / "images").mkdir(exist_ok=True)
    db.run(
        "INSERT INTO tasks(id,name,engine,account_id,type,copy,image_source,status,dir,row_order,created,updated)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (tid, name, "flow", "A001", "short", "hello", "x", "prepared",
         str(d), row, db.now(), db.now()),
    )
    m = Manifest.new(tid, name, "flow", "A001", "short", "hello")
    m.images = []
    for i, has in enumerate(images, 1):
        p = "p%02d" % i
        desc = descs[i - 1] if i - 1 < len(descs) else ""
        m.images.append({
            "p": p, "file": f"images/{p}.jpg", "role": "主体",
            "status": "ok", "desc": desc, "sha1": "",
        })
        if has:
            (d / "images" / f"{p}.jpg").write_bytes(b"x")
    m.segments = []
    m.save(d)
    return d


def test_queue_scan_order():
    print("== 队列按 row_order 扫缺描述语的任务 ==")
    db.run("DELETE FROM tasks")
    _mk_task("T0003", "丙", 30, [True], [""])
    _mk_task("T0001", "甲", 10, [True], [""])
    _mk_task("T0002", "乙", 20, [True], ["已有描述语"])
    _mk_task("T0004", "丁空", 40, [], [])
    items = descqueue.scan()
    ids = [t["id"] for t in items]
    if ids != ["T0001", "T0003", "T0004"]:
        bad(f"扫描顺序/筛选不对：{ids}")
        return
    if "T0002" in ids:
        bad("已写好描述语的任务不该进队")
        return
    ok(f"行序 {ids}（跳过已写好的 T0002，空图 T0004 进队待失败）")


def test_queue_continue_on_fail():
    print("== 队列：一个失败继续跑完，汇总可点 ==")
    db.run("DELETE FROM tasks")
    _mk_task("T0016", "李星辰", 1, [True], [""])
    _mk_task("T0019", "没图", 2, [], [])
    _mk_task("T0017", "另一个", 3, [True], [""])
    snap = descqueue.start()
    if not snap.get("on") or snap.get("total") != 3:
        bad(f"start 不对：{snap}")
        return
    deadline = time.time() + 20
    while time.time() < deadline:
        s = descqueue.snapshot()
        if not s.get("on"):
            break
        time.sleep(0.2)
    s = descqueue.snapshot()
    if s.get("on"):
        descqueue.stop()
        bad("队列 20s 还没停")
        return
    results = s.get("results") or []
    by = {r["id"]: r for r in results}
    if "T0019" not in by or by["T0019"].get("ok") or by["T0019"].get("error") != "没有主体图":
        bad(f"T0019 应该失败「没有主体图」：{by.get('T0019')}")
        return
    if not (s.get("summary") or ""):
        bad("没有汇总")
        return
    if "T0019" not in (s.get("summary") or ""):
        bad(f"汇总没点得出 T0019：{s.get('summary')!r}")
        return
    if len(results) < 3:
        bad(f"没有跑完三个：{results}")
        return
    ok(f"汇总：{s.get('summary')}")


def main() -> int:
    print(f"ORANTE_DATA = {TEST_DATA}")
    test_insert_clause()
    test_format_migrate_backup()
    test_merge_refill_only()
    test_merge_both_custom()
    test_merge_neither()
    test_all_blocks_no_refill()
    test_queue_scan_order()
    test_queue_continue_on_fail()
    print()
    if fail:
        print(f"失败 {fail} 项")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
