# -*- coding: utf-8 -*-
"""规格 92：第一次运行问语种，把提示词整理成一份清单让用户拿去 ChatGPT 翻好粘回来。"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TEST_DATA = Path(tempfile.mkdtemp(prefix="orante-test-92-"))
os.environ["ORANTE_DATA"] = str(TEST_DATA)
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "orante" / "legacy"))

from orante import config, db  # noqa: E402
from orante.errors import OranteError  # noqa: E402
from orante.services import langpack as L, prompts  # noqa: E402

db.init()


# ---------- 清单 ----------

def test_collect_covers_the_prompts_that_matter():
    core = {e["key"] for e in L.collect(scope="core")}
    assert "desc" in core                       # 写描述语的那段指令
    assert any(k.startswith("tail.") for k in core)      # 念白要求
    assert any(k.startswith("camera.") for k in core)    # 运镜指令
    assert "chain" in core and "bridge" in core
    assert not any(k.startswith("expand.") for k in core)   # 扩图机位库不在关键盘
    all_keys = {e["key"] for e in L.collect(scope="all")}
    assert any(k.startswith("expand.") for k in all_keys)
    assert core <= all_keys


def test_never_exports_regex_patterns():
    """问题句用的正则/短语是拿来匹配的，翻了就认不出来了。"""
    for scope in ("core", "all"):
        keys = {e["key"] for e in L.collect(scope=scope)}
        assert not any(k.startswith("problem.patterns") for k in keys)
    assert any(e["key"] == "problem.template" for e in L.collect())


def test_render_has_rules_and_numbered_entries():
    e = L.collect(scope="core")
    txt = L.render(e, "es")
    assert "Español" in txt
    assert "[[1]] " in txt and "<<<" in txt and ">>>" in txt
    assert "{花括号占位符}" in txt or "花括号" in txt      # 占位符规则写在里头
    assert str(len(e)) in txt


def test_round_trip_is_lossless():
    e = L.collect(scope="all")
    back = L.parse(L.render(e, "en"))
    assert len(back) == len(e)
    assert all(back[x["key"]] == x["text"] for x in e)


def test_parse_tolerates_chatgpt_noise():
    txt = """当然可以！这是翻译好的清单：

[[1]] chain
<<<
Texto traducido.
>>>

希望对你有帮助！
"""
    got = L.parse(txt)
    assert got == {"chain": "Texto traducido."}


def _plain_key() -> str:
    """挑一条不带占位符的（带占位符的另有守门测试）。"""
    for e in L.collect(scope="core"):
        if "{" not in e["text"] and "[" not in e["key"]:
            return e["key"]
    raise AssertionError("找不到不带占位符的条目")


# ---------- 守门 ----------

def test_placeholder_loss_is_rejected():
    cur = {e["key"]: e["text"] for e in L.collect(scope="all")}
    key = next(k for k, v in cur.items() if "{" in v)
    res = L.check({key: "翻译过头，占位符没了"})
    assert res["ok"] == {}
    assert res["problems"] and res["problems"][0]["why"] == "placeholder"


def test_unknown_and_empty_are_skipped():
    res = L.check({"不存在的键": "x", "chain": "  "})
    whys = {p["why"] for p in res["problems"]}
    assert whys == {"unknown", "empty"}
    assert res["ok"] == {}


def test_partial_paste_applies_only_what_came_back():
    key = _plain_key()
    before = {e["key"]: e["text"] for e in L.collect(scope="all")}
    res = L.apply_parsed({key: "TRADUCIDO"}, lang="es")
    assert res["applied"] == 1 and res["keys"] == [key], res
    after = {e["key"]: e["text"] for e in L.collect(scope="all")}
    assert after[key] == "TRADUCIDO"
    assert res["missing_n"] > 0                  # 没贴的那些原样留着
    untouched = [k for k in before if k != key and before[k] != after.get(k)]
    assert untouched == []                       # 只动贴回来的那一条
    L.restore()
    assert {e["key"]: e["text"] for e in L.collect(scope="all")}[key] == before[key]


# ---------- 写回嵌套结构 ----------

def test_applies_into_nested_dicts_and_lists():
    entries = L.collect(scope="all")
    nested = next(e for e in entries if e["key"].startswith("camera.") and "[" in e["key"])
    plain = next(e for e in entries if e["key"].startswith("tail.") and "[" not in e["key"])
    res = L.apply_parsed({nested["key"]: "CAM-TRANSLATED", plain["key"]: "TAIL-TRANSLATED"})
    assert res["applied"] == 2, res
    again = {e["key"]: e["text"] for e in L.collect(scope="all")}
    assert again[nested["key"]] == "CAM-TRANSLATED"
    assert again[plain["key"]] == "TAIL-TRANSLATED"
    L.restore()
    again2 = {e["key"]: e["text"] for e in L.collect(scope="all")}
    assert again2[nested["key"]] == nested["text"]
    assert again2[plain["key"]] == plain["text"]


def test_backup_keeps_the_first_one():
    """第二次翻译前不许用译文覆盖备份——备份要的是出厂那份。"""
    config.set("prompts.lang_backup", {}, touch_rev=False)
    key = _plain_key()
    root = key.split(".")[0]
    orig_block = prompts.get_block(root)
    L.apply_parsed({key: "primera"})
    L.apply_parsed({key: "segunda"})
    bak = config.get("prompts.lang_backup")
    assert bak["blocks"][root] == orig_block
    L.restore()
    assert prompts.get_block(root) == orig_block


def test_restore_without_backup_explains():
    config.set("prompts.lang_backup", {}, touch_rev=False)
    with pytest.raises(OranteError):
        L.restore()


# ---------- 状态 / 接线 ----------

def test_state_reports_language_and_counts():
    L.apply_parsed({_plain_key(): "estado"}, lang="es")
    st = L.state()
    assert st["lang"] == "es" and st["lang_name"] == "Español"
    assert st["n"] > 0 and st["n_all"] >= st["n"]
    assert st["has_backup"] is True
    assert any(x["code"] == "pt" for x in st["langs"])
    L.restore()


def test_config_keys_registered():
    assert config.DEFAULTS["content.lang"] == ""
    for k in ("content.lang", "content.lang_applied", "prompts.lang_backup"):
        assert k in config.HIDDEN_KEYS          # 内部状态，不进设置页


def test_server_and_ui_wired():
    srv = (ROOT / "orante" / "server.py").read_text(encoding="utf-8")
    for path in ("/api/lang/state", "/api/lang/export", "/api/lang/import", "/api/lang/restore"):
        assert path in srv, path
    setup = (ROOT / "ui" / "js" / "parts" / "setup.js").read_text(encoding="utf-8")
    assert "step.value === 4" in setup and "step===5" in setup      # 多出来的那一步
    assert "LangPackLogic" in setup
    logic = (ROOT / "ui" / "js" / "parts" / "langpack.js").read_text(encoding="utf-8")
    assert "exportPack" in logic and "restore" in logic
    idx = (ROOT / "ui" / "index.html").read_text(encoding="utf-8")
    assert "langpack.js" in idx
    gem = (ROOT / "ui" / "js" / "panels" / "gemini.js").read_text(encoding="utf-8")
    assert "lpCopy" in gem and "lpRestore" in gem                   # 以后能重来
    zh = json.loads((ROOT / "orante" / "i18n" / "zh.json").read_text(encoding="utf-8"))
    for k in ("setup.lang.intro", "setup.lang.pick", "setup.lang.copy",
              "setup.lang.apply", "setup.lang.later", "prompts.lang.title",
              "prompts.lang.restore"):
        assert zh.get(k), k
    en = json.loads((ROOT / "orante" / "i18n" / "en.json").read_text(encoding="utf-8"))
    assert en.get("setup.lang.pick") and en.get("prompts.lang.restore")
