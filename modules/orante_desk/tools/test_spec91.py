# -*- coding: utf-8 -*-
"""规格 91：同账号多任务开关 + 受限原因写清楚 + 冷却自动探路。"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TEST_DATA = Path(tempfile.mkdtemp(prefix="orante-test-91-"))
os.environ["ORANTE_DATA"] = str(TEST_DATA)
sys.path.insert(0, str(ROOT))

from orante import accounts, config, db  # noqa: E402

db.init()


def _acc(aid="A91", engine="flow", cap=5):
    if not db.one("SELECT 1 FROM accounts WHERE id=?", (aid,)):
        db.run(
            "INSERT INTO accounts(id,name,engine,port,profile,project_url,model,"
            "aspect,max_concurrent,enabled,extra,created)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (aid, "t91-" + aid, engine, 9800 + (sum(ord(c) for c in aid) % 100),
             aid, "", "", "9:16", cap, 1, "{}", db.now()))
    else:
        db.run("UPDATE accounts SET extra=? WHERE id=?", ("{}", aid))
    return accounts.get(aid)


@pytest.fixture(autouse=True)
def _clean():
    config.set("flow.network_throttle", {}, touch_rev=False)
    config.set("flow.last_throttle", {}, touch_rev=False)
    yield


# ---------- 受限原因 ----------

def test_mark_throttled_records_why():
    a = _acc("A91a")
    accounts.mark_throttled(a["id"], reason={
        "code": "PUBLIC_ERROR_UNUSUAL_ACTIVITY", "rpc": "eb1hJf",
        "err_n": 8, "raw": "…unusual activity…", "seg": "s003"})
    info = accounts.throttle_info(a["id"])
    assert info["on"] is True
    assert info["code"] == "PUBLIC_ERROR_UNUSUAL_ACTIVITY"
    assert info["rpc"] == "eb1hJf" and info["err_n"] == 8 and info["seg"] == "s003"
    assert info["minutes"] == accounts.THROTTLE_FIRST // 60
    assert info["times"] == 1
    assert info["at"]


def test_throttle_lines_explain_who_set_the_clock():
    a = _acc("A91b")
    accounts.mark_throttled(a["id"], reason={"code": "PUBLIC_ERROR_UNUSUAL_ACTIVITY",
                                             "rpc": "eb1hJf", "seg": "s001"})
    lines = "\n".join(accounts.throttle_lines(accounts.get(a["id"]), probe_every=240))
    assert "PUBLIC_ERROR_UNUSUAL_ACTIVITY" in lines
    assert "eb1hJf" in lines
    assert "Orante 自己定的" in lines        # 不是 Google 要求等这么久
    assert "4 分钟" in lines                 # 探路间隔
    assert "现在就试" in lines               # 告诉用户还有这条路


def test_history_keeps_last_five():
    a = _acc("A91c")
    for i in range(7):
        db.run("UPDATE accounts SET extra=json_remove(extra,'$.throttled_until',"
               "'$.throttled_until_ts') WHERE id=?", (a["id"],))
        accounts.mark_throttled(a["id"], reason={"code": f"E{i}", "rpc": "eb1hJf"})
    extra = accounts.get(a["id"])["extra"]
    assert len(extra["throttle_log"]) == 5
    assert extra["throttle_log"][-1]["code"] == "E6"


# ---------- 探路 ----------

def test_probe_due_after_interval():
    a = _acc("A91d")
    accounts.mark_throttled(a["id"], reason={"code": "X", "rpc": "eb1hJf"})
    acc = accounts.get(a["id"])
    assert accounts.probe_due(acc, every=600) is False        # 刚被限，还不到点
    assert accounts.probe_due(acc, every=0) is False          # 0 = 关掉探路
    later = time.time() + 700
    assert accounts.probe_due(acc, every=600, now=later) is True
    accounts.note_probe(a["id"], now=later)
    acc = accounts.get(a["id"])
    assert accounts.probe_due(acc, every=600, now=later + 10) is False   # 刚探过
    assert accounts.probe_due(acc, every=600, now=later + 700) is True


def test_probe_not_due_when_not_throttled():
    a = _acc("A91e")
    assert accounts.probe_due(accounts.get(a["id"]), every=60) is False


def test_clear_throttle_resets_everything():
    a = _acc("A91f")
    accounts.mark_throttled(a["id"], reason={"code": "X", "rpc": "eb1hJf"})
    accounts.mark_network_throttle([a["id"], "B", "C"], reason={"code": "X"})
    assert accounts.network_throttled() is True
    accounts.clear_throttle(a["id"], who="user")
    acc = accounts.get(a["id"])
    assert accounts.throttle_remaining(acc) == 0
    assert acc["extra"]["throttle_minutes"] == accounts.THROTTLE_FIRST // 60   # 档位也清回去
    assert acc["extra"]["throttle_cleared_by"] == "user"
    assert accounts.network_throttled() is False               # 全网暂停一并解除
    info = accounts.throttle_info(acc)
    assert info["on"] is False and info["code"]                # 原因还留着，方便回看


# ---------- 全网暂停放宽 ----------

def test_two_accounts_no_longer_pause_everything():
    """以前两个账号先后受限就全网暂停——太容易误判。现在要三个。"""
    a1, a2, a3 = _acc("A91g"), _acc("A91h"), _acc("A91i")
    accounts.mark_throttled(a1["id"], reason={"code": "X"})
    accounts.mark_throttled(a2["id"], reason={"code": "X"})
    assert accounts.network_throttled() is False
    accounts.mark_throttled(a3["id"], reason={"code": "X"})
    assert accounts.network_throttled() is True
    st = accounts.network_throttle_state()
    assert len(st["accounts"]) >= 3 and st["at"]


def test_network_clear_is_one_call():
    accounts.mark_network_throttle(["a", "b", "c"], reason={"code": "X"})
    assert accounts.network_throttled() is True
    accounts.clear_network_throttle(who="user")
    assert accounts.network_throttled() is False


# ---------- 同账号多任务开关 ----------

def test_defaults_are_off_and_three():
    assert config.DEFAULTS["engine.pack.multi_task"] is False
    assert config.DEFAULTS["engine.pack.max_tasks"] == 3
    assert config.DEFAULTS["engine.flow.throttle_probe_min"] == 4


def test_scheduler_reads_the_switch():
    src = (ROOT / "orante" / "scheduler.py").read_text(encoding="utf-8")
    assert 'config.get("engine.pack.multi_task", False)' in src
    assert 'config.get("engine.pack.max_tasks", 3)' in src
    assert "max(2, min(max_tasks, 5))" in src
    # 关着的时候还是老规则
    assert "return used + need <= cap" in src


def test_fair_share_in_generate():
    src = (ROOT / "orante" / "services" / "generate.py").read_text(encoding="utf-8")
    assert "def my_share" in src
    assert "len(inflight) < share" in src          # 一个任务不许把位子占光
    assert "cap // n" in src


def test_probe_path_wired():
    gen = (ROOT / "orante" / "services" / "generate.py").read_text(encoding="utf-8")
    assert "accounts.probe_due" in gen and "accounts.note_probe" in gen
    assert 'clear_throttle(acc["id"], who="probe")' in gen
    assert "throttle_lines" in gen                 # 不再只写一句"账号限流中"
    sch = (ROOT / "orante" / "scheduler.py").read_text(encoding="utf-8")
    assert "probe_due" in sch                      # 冷却中也要放它去探一次
    srv = (ROOT / "orante" / "server.py").read_text(encoding="utf-8")
    assert "/api/flow/throttle/clear" in srv
    assert "/api/flow/throttle" in srv


def test_ui_shows_reason_and_button():
    js = (ROOT / "ui" / "js" / "panels" / "generate.js").read_text(encoding="utf-8")
    assert "throttleWhy" in js and "tryNowThrottle" in js
    assert "/api/flow/throttle/clear" in js
    zh = json.loads((ROOT / "orante" / "i18n" / "zh.json").read_text(encoding="utf-8"))
    for k in ("gen.th.try_now", "gen.th.why_code", "gen.th.why_cool",
              "gen.th.why_probe", "gen.th.why_browser",
              "set.key.engine.pack.multi_task", "set.key.engine.flow.throttle_probe_min"):
        assert zh.get(k), k
    en = json.loads((ROOT / "orante" / "i18n" / "en.json").read_text(encoding="utf-8"))
    for k in ("gen.th.try_now", "set.key.engine.pack.multi_task"):
        assert en.get(k), k
    st = (ROOT / "ui" / "js" / "parts" / "settings.js").read_text(encoding="utf-8")
    assert "'engine.pack.multi_task'" in st and "'engine.pack.max_tasks'" in st
