# -*- coding: utf-8 -*-
"""规格 94：分发用户反馈「Orante 一直说限流不给刷，插件却能刷」——把还会卡死的地方补上。

两件事：
  ① 全网暂停原来一次都不试，死等 30 分钟。现在也按节奏探路，成功就整条解除。
  ② 提交节奏：插件是人一条条点，Orante 一秒连打好几发，正是被判「异常活动」的样子。
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TEST_DATA = Path(tempfile.mkdtemp(prefix="orante-test-94-"))
os.environ["ORANTE_DATA"] = str(TEST_DATA)
sys.path.insert(0, str(ROOT))

from orante import accounts, config, db  # noqa: E402
from orante.engines.flow_api import classify_flow_error  # noqa: E402
from orante.services import generate as genmod  # noqa: E402

db.init()


@pytest.fixture(autouse=True)
def _clean():
    config.set("flow.network_throttle", {}, touch_rev=False)
    config.set("flow.last_throttle", {}, touch_rev=False)
    yield


# ---------- 用户日志里那一条，必须认成限流且不许重试 ----------

def test_the_exact_error_from_user_logs_is_a_throttle():
    err = [7, None, [["type.googleapis.com/google.rpc.ErrorInfo",
                      ["PUBLIC_ERROR_UNUSUAL_ACTIVITY"]]]]
    info = classify_flow_error("eb1hJf", {"status": 200, "err": err, "raw": "", "payload": None})
    assert info["action"] == "throttle"          # 认出来 = 不重试、不算段失败
    assert info["code"] == "PUBLIC_ERROR_UNUSUAL_ACTIVITY"


def test_busy_is_not_a_throttle():
    """429 / 服务忙是另一回事：可以重试，不该把账号按进 20 分钟冷却。"""
    info = classify_flow_error("eb1hJf", {"status": 429, "err": None, "raw": "", "payload": None})
    assert info["action"] == "busy"


def test_flow_api_never_retries_a_throttle():
    src = (ROOT / "orante" / "engines" / "flow_api.py").read_text(encoding="utf-8")
    i = src.index('if info.get("action") == "throttle"')
    assert "raise FlowThrottled" in src[i:i + 300]      # 当场抛，不进重试循环


# ---------- 全网暂停也要探路 ----------

def test_net_probe_due_follows_the_clock():
    accounts.mark_network_throttle(["a", "b", "c"], reason={"code": "X"})
    assert accounts.network_throttled() is True
    assert accounts.net_probe_due(every=600) is False        # 刚暂停，还不到点
    later = time.time() + 700
    assert accounts.net_probe_due(every=600, now=later) is True
    accounts.note_net_probe(now=later)
    assert accounts.net_probe_due(every=600, now=later + 30) is False
    assert accounts.net_probe_due(every=600, now=later + 700) is True
    assert accounts.net_probe_due(every=0, now=later + 700) is False   # 0 = 不探路


def test_net_probe_needs_an_active_pause():
    assert accounts.network_throttled() is False
    assert accounts.net_probe_due(every=1) is False


def test_scheduler_lets_a_probe_through_the_network_pause():
    sch = (ROOT / "orante" / "scheduler.py").read_text(encoding="utf-8")
    i = sch.index("if accounts.network_throttled():")
    block = sch[i:i + 700]
    assert "net_probe_due" in block                 # 全网暂停也放探路
    gen = (ROOT / "orante" / "services" / "generate.py").read_text(encoding="utf-8")
    assert "accounts.note_net_probe()" in gen
    assert "全网暂停还剩" in gen


def test_probe_success_clears_the_whole_pause():
    """探路成功走的是 clear_throttle，它会把全网暂停一起清掉。"""
    a = "A94"
    if not db.one("SELECT 1 FROM accounts WHERE id=?", (a,)):
        db.run("INSERT INTO accounts(id,name,engine,port,profile,project_url,model,"
               "aspect,max_concurrent,enabled,extra,created)"
               " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
               (a, "t94", "flow", 9911, a, "", "", "9:16", 5, 1, "{}", db.now()))
    accounts.mark_throttled(a, reason={"code": "X"})
    accounts.mark_network_throttle([a, "b", "c"], reason={"code": "X"})
    accounts.clear_throttle(a, who="probe")
    assert accounts.throttle_remaining(accounts.get(a)) == 0
    assert accounts.network_throttled() is False


# ---------- 提交节奏 ----------

def test_submit_gap_default_and_override():
    assert config.DEFAULTS["engine.flow.submit_gap_sec"] == 2
    assert genmod.submit_gap() == 2.0
    config.set("engine.flow.submit_gap_sec", 0, touch_rev=False)
    assert genmod.submit_gap() == 0.0
    config.set("engine.flow.submit_gap_sec", 5, touch_rev=False)
    assert genmod.submit_gap() == 5.0
    config.set("engine.flow.submit_gap_sec", "坏值", touch_rev=False)
    assert genmod.submit_gap() == 2.0                 # 坏值回默认，不能崩
    config.reset("engine.flow.submit_gap_sec")


def test_gap_is_only_applied_to_flow_and_is_jittered():
    gen = (ROOT / "orante" / "services" / "generate.py").read_text(encoding="utf-8")
    assert 'gap = submit_gap() if acc.get("engine") == "flow" else 0.0' in gen
    assert "_rnd.uniform" in gen                      # 带抖动，不是整齐的节拍
    assert "note_submit(acc.get(\"id\") or \"\")" in gen
    assert "job.sleep(" in gen                        # 用 job.sleep：停任务时能立刻醒


def test_gap_is_per_account_not_per_task():
    """同账号几个任务一起跑时，节奏要按账号算，否则等于没节奏。"""
    genmod._LAST_SUBMIT.clear()
    assert genmod.submit_wait("AX", 3.0) == 0.0        # 还没提交过，不等
    genmod.note_submit("AX")
    w = genmod.submit_wait("AX", 3.0)
    assert 3.0 <= w <= 4.5                             # 3 秒 + 最多 1.5 秒抖动
    assert genmod.submit_wait("AY", 3.0) == 0.0        # 别的账号不受影响
    assert genmod.submit_wait("AX", 0.0) == 0.0        # 关掉就不等
    # 时间过去之后不再等
    assert genmod.submit_wait("AX", 3.0, now=genmod.last_submit_at("AX") + 30) == 0.0
    genmod._LAST_SUBMIT.clear()


def test_settings_exposes_both_knobs():
    js = (ROOT / "ui" / "js" / "parts" / "settings.js").read_text(encoding="utf-8")
    assert "'engine.flow.submit_gap_sec'" in js
    assert "'engine.flow.submit_gap_sec': [0, 60]" in js
    import json
    zh = json.loads((ROOT / "orante" / "i18n" / "zh.json").read_text(encoding="utf-8"))
    assert zh.get("set.key.engine.flow.submit_gap_sec")
    assert "异常活动" in zh.get("set.hint.engine.flow.submit_gap_sec", "")
