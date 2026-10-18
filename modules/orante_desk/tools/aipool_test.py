# -*- coding: utf-8 -*-
"""多模态接口池自检（规格 20）。

临时库、假接口，不联网、不碰真实 data\orante.db。双击 tools\6-AIPool-Test.bat 就跑。
"""
import os
import sys
import tempfile
import threading
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp(prefix="orante-aipool-"))
os.environ["ORANTE_DATA"] = str(TMP)
sys.path.insert(0, str(ROOT))

from orante import db                              # noqa: E402
from orante.errors import OranteError              # noqa: E402
from orante.services import ai, aikeys, aipool     # noqa: E402

FAILED = []


def check(name, got, want):
    ok = got == want
    print(f"  {'✓' if ok else '✗'} {name}\n      实际 {got}\n      期望 {want}")
    if not ok:
        FAILED.append(name)


def check_true(name, cond, note=""):
    print(f"  {'✓' if cond else '✗'} {name}" + (f"   {note}" if note else ""))
    if not cond:
        FAILED.append(name)


def reset(providers):
    db.init()
    for tb in ("ai_providers", "ai_state", "ai_leases"):
        db.run(f"DELETE FROM {tb}")
    for p in providers:
        aikeys.add(p)


def P(name, **kw):
    d = {"name": name, "kind": "openai", "model": "m", "api_key": "k",
         "base_url": "http://x", "priority": 1, "max_inflight": 2}
    d.update(kw)
    return d


# ---- 用假 adapter 顶掉真的网络请求 ----
def fake(behaviour):
    """behaviour: name -> 'ok' | '429min' | '429day' | 'boom'，可带 sleep。"""
    def _f(p, prompt, images, system, temperature, json_mode):
        b = behaviour.get(p["name"], "ok")
        time.sleep(b.get("sleep", 0) if isinstance(b, dict) else 0)
        kind = b["kind"] if isinstance(b, dict) else b
        if kind == "ok":
            return "OK:" + p["name"]
        if kind == "429min":
            e = OranteError("被限流"); e.quota = {"scope": "每分钟", "retry": 3}; raise e
        if kind == "429day":
            e = OranteError("今天没了"); e.quota = {"scope": "每天", "retry": 0}; raise e
        raise OranteError("炸了")
    return _f


def with_fake(behaviour, fn):
    old = dict(ai.ADAPTERS)
    ai.ADAPTERS["openai"] = fake(behaviour)
    try:
        return fn()
    finally:
        ai.ADAPTERS.clear()
        ai.ADAPTERS.update(old)


def test_balance():
    print("\n[1] 同优先级：活按「谁手上最少」摊开，不会全压一条")
    reset([P("A"), P("B"), P("C"), P("D")])
    hits = Counter()
    mu = threading.Lock()

    def one():
        out = ai.client().ask_image("q", __file__)
        with mu:
            hits[out.split(":")[1]] += 1

    def run():
        ths = [threading.Thread(target=one) for _ in range(40)]
        for t in ths:
            t.start()
        for t in ths:
            t.join()
    with_fake({n: {"kind": "ok", "sleep": 0.02} for n in "ABCD"}, run)
    check("四条都干了活", sorted(hits), ["A", "B", "C", "D"])
    check_true("摊得还算匀（每条 ≥5 次）", min(hits.values()) >= 5, dict(hits))
    check("总数对得上", sum(hits.values()), 40)


def test_priority():
    print("\n[2] 优先级：1 档没满就轮不到 2 档")
    reset([P("本地", priority=1, max_inflight=2), P("云端", priority=2, max_inflight=8)])
    hits = Counter()
    mu = threading.Lock()

    def one():
        out = ai.client().ask_image("q", __file__)
        with mu:
            hits[out.split(":")[1]] += 1

    def run():   # 串行发 6 次：1 档一直有空位，应该全被本地吃掉
        for _ in range(6):
            one()
    with_fake({"本地": "ok", "云端": "ok"}, run)
    check("串行时全走优先级 1", dict(hits), {"本地": 6})


def test_failover():
    print("\n[3] 被 429 就换下一条，不是整批失败")
    reset([P("坏-分钟", priority=1), P("坏-每天", priority=1), P("好", priority=2)])

    def run():
        return ai.client().ask_image("q", __file__)
    out = with_fake({"坏-分钟": "429min", "坏-每天": "429day", "好": "ok"}, run)
    check("最终拿到结果", out, "OK:好")
    st = {s["name"]: s for s in aipool.status()}
    check_true("「坏-分钟」进了短冷却", 0 < st["坏-分钟"]["cool_left"] <= 10,
               st["坏-分钟"]["cool_left"])
    check_true("「坏-每天」冷却到明天（>1 小时）", st["坏-每天"]["cool_left"] > 3600,
               st["坏-每天"]["cool_left"])
    check("好的那条记了一次成功", (st["好"]["calls"], st["好"]["fails"]), (1, 0))
    # 冷却期内再来一次：应该直接走「好」，不再碰坏的两条
    out2 = with_fake({"好": "ok"}, run)
    check("冷却期内直接绕开坏的", out2, "OK:好")


def test_all_down():
    print("\n[4] 全挂了要说清楚每条为什么不行")
    reset([P("A"), P("B")])
    try:
        with_fake({"A": "boom", "B": "boom"}, lambda: ai.client().ask_image("q", __file__))
        check_true("应该抛错", False)
    except OranteError as e:
        check_true("报错点名了「所有接口」", "所有多模态接口" in e.message, e.message)
        check_true("hint 里带上了最后的错", "炸" in (e.hint or ""), e.hint)


def test_capacity():
    print("\n[5] 并发线程数跟着 key 数量走，但卡总上限")
    reset([P("A", max_inflight=2), P("B", max_inflight=2)])
    check("2 条 ×2 = 4", (aipool.capacity(), aipool.workers(16)), (4, 4))
    aikeys.add(P("C", max_inflight=30))
    check("加到 34，但上限 16", (aipool.capacity(), aipool.workers(16)), (34, 16))
    ps = aikeys.list_all()
    aikeys.update(ps[0]["id"], {"enabled": 0})
    check("停用的不算容量", aipool.capacity(), 32)


def test_lease_cleanup():
    print("\n[6] 借出去没还的（进程被杀）会被回收，不会永久占位")
    reset([P("A", max_inflight=1)])
    p = aikeys.list_all()[0]
    db.run("INSERT INTO ai_leases(key,provider_id,owner,job_id,ts) VALUES(?,?,?,?,?)",
           ("zombie", p["id"], "dead#1", "", time.time() - aipool.LEASE_STALE - 5))
    out = with_fake({"A": "ok"}, lambda: ai.client().ask_image("q", __file__))
    check("僵尸位被回收后照常干活", out, "OK:A")
    check("没留下在飞记录", db.rows("SELECT * FROM ai_leases"), [])


def test_migrate():
    print("\n[7] 老的单条 gemini.api_key 自动搬进名册")
    db.init()
    for tb in ("ai_providers", "ai_state", "ai_leases", "settings"):
        db.run(f"DELETE FROM {tb}")
    db.run("INSERT INTO settings(key,value) VALUES('gemini.api_key','\"AIzaTEST\"')")
    db.run("INSERT INTO settings(key,value) VALUES('gemini.model','\"gemini-3.5-flash-lite\"')")
    db.run("INSERT INTO settings(key,value) VALUES('gemini.concurrency','4')")
    got = aikeys.migrate_from_settings()
    check("搬过来一条", (got or {}).get("kind"), "gemini")
    check("模型和并发都带过来了",
          ((got or {}).get("model"), (got or {}).get("max_inflight")),
          ("gemini-3.5-flash-lite", 4))
    check("再跑一次不会重复搬", aikeys.migrate_from_settings(), None)
    check("界面上 key 是打码的", aikeys.masked(got)["api_key"], "…TEST")


if __name__ == "__main__":
    print("接口池自检 · 临时库", TMP)
    test_balance()
    test_priority()
    test_failover()
    test_all_down()
    test_capacity()
    test_lease_cleanup()
    test_migrate()
    print("\n" + ("全部通过 ✓" if not FAILED else "有失败：" + "、".join(FAILED)))
    sys.exit(0 if not FAILED else 1)
