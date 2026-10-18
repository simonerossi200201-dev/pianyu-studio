# -*- coding: utf-8 -*-
"""规格 76：模板套装。先设 ORANTE_DATA。序列化必须走 preset，不重写。"""
from __future__ import annotations

import hashlib
import os
import time
import re
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TEST_DATA = Path(tempfile.mkdtemp(prefix="orante-test-spec76-"))
os.environ["ORANTE_DATA"] = str(TEST_DATA)
sys.path.insert(0, str(ROOT))

from orante import accounts, config, db, paths  # noqa: E402
from orante.errors import OranteError  # noqa: E402
from orante.services import preset, prompts, suites  # noqa: E402

db.init()


def _payload(sid: str, grp: str):
    r = db.one("SELECT payload FROM suite_data WHERE suite_id=? AND grp=?", (sid, grp))
    return db.unj(r["payload"]) if r else None


def _dir_sha(p: Path) -> str:
    h = hashlib.sha256()
    if not p.exists():
        return h.hexdigest()
    files = sorted(f for f in p.rglob("*") if f.is_file())
    for f in files:
        h.update(f.relative_to(p).as_posix().encode("utf-8"))
        h.update(b"\0")
        h.update(f.read_bytes())
    return h.hexdigest()


def test_empty_db_seeds_builtin():
    data = suites.list_all()
    assert data["current"]
    items = data["items"]
    assert items, "空库应该有默认套装"
    builtin = [x for x in items if x.get("builtin")]
    assert len(builtin) == 1
    assert builtin[0]["name"] == "默认套装"
    assert data["current"] == builtin[0]["id"]
    cur = config.get("suite.current")
    assert cur == builtin[0]["id"]
    groups = _payload(builtin[0]["id"], "prompts")
    assert isinstance(groups, dict)


def test_save_current_roundtrip_prompt():
    sid = suites.current_id()
    config.set("prompts.desc", "DESC-SUITE-AAA")
    suites.save_current(sid)
    stored = _payload(sid, "prompts")
    assert stored["prompts.desc"] == "DESC-SUITE-AAA"
    config.set("prompts.desc", "DESC-OTHER")
    suites.switch(sid, save=False)
    assert prompts.get_block("desc") == "DESC-SUITE-AAA"


def test_create_from_current_independent():
    sid_a = suites.current_id()
    config.set("prompts.desc", "DESC-A-KEEP")
    suites.save_current(sid_a)
    b = suites.create("温暖布道", "柔和眼神", "#C08497", from_="current")
    assert b["id"] != sid_a
    # 新建不自动切，前端再调 switch
    assert suites.current_id() == sid_a
    pa = _payload(sid_a, "prompts")
    pb = _payload(b["id"], "prompts")
    assert pa["prompts.desc"] == "DESC-A-KEEP"
    assert pb["prompts.desc"] == "DESC-A-KEEP"
    suites.switch(b["id"], save=True)
    assert suites.current_id() == b["id"]
    config.set("prompts.desc", "DESC-B-ONLY")
    suites.save_current(b["id"])
    pa2 = _payload(sid_a, "prompts")
    pb2 = _payload(b["id"], "prompts")
    assert pa2["prompts.desc"] == "DESC-A-KEEP"
    assert pb2["prompts.desc"] == "DESC-B-ONLY"


def test_create_from_factory_differs_from_dirty():
    sid = suites.current_id()
    config.set("prompts.desc", "FACTORY-SHOULD-NOT-SEE")
    suites.save_current(sid)
    item = suites.create("出厂拷贝-76", from_="factory")
    assert suites.current_id() == sid
    pa = _payload(sid, "prompts")
    pb = _payload(item["id"], "prompts")
    assert pa["prompts.desc"] == "FACTORY-SHOULD-NOT-SEE"
    assert pb["prompts.desc"] != "FACTORY-SHOULD-NOT-SEE"


def test_create_from_other_suite_id():
    src = suites.create("源套装-76", from_="current")
    config.set("prompts.desc", "FROM-OTHER-SRC")
    suites.save_current(src["id"])
    config.set("prompts.desc", "LIVE-NOW")
    item = suites.create("从别人拷-76", from_=src["id"])
    assert _payload(item["id"], "prompts")["prompts.desc"] == "FROM-OTHER-SRC"
    assert prompts.get_block("desc") == "LIVE-NOW"
    assert suites.current_id() != item["id"]
    config.set("prompts.desc", "NEW-ONLY")
    suites.save_current(item["id"])
    assert _payload(src["id"], "prompts")["prompts.desc"] == "FROM-OTHER-SRC"


def test_create_duplicate_name_rejected():
    name = f"套装重名{db.now()}"
    suites.create(name, from_="current")
    with pytest.raises(OranteError) as ei:
        suites.create(name, from_="current")
    assert "已经有叫" in ei.value.message
    assert name in ei.value.message


def test_save_as_restore_original_via_save_false():
    a = suites.current_id()
    config.set("prompts.desc", "ORIGINAL-KEEP")
    suites.save_current(a)
    config.set("prompts.desc", "TAKE-AWAY")
    b = suites.create("带走改动-76", from_="current")
    assert _payload(a, "prompts")["prompts.desc"] == "ORIGINAL-KEEP"
    assert _payload(b["id"], "prompts")["prompts.desc"] == "TAKE-AWAY"
    config.set("suite.autosave_on_switch", True)
    suites.switch(b["id"], save=False)
    assert _payload(a, "prompts")["prompts.desc"] == "ORIGINAL-KEEP"
    assert prompts.get_block("desc") == "TAKE-AWAY"


def test_switch_autosave_keeps_unsaved_edit():
    data = suites.list_all()
    a = next(x for x in data["items"] if x.get("builtin"))
    others = [x for x in data["items"] if x["id"] != a["id"]]
    if not others:
        others = [suites.create("套装B-自动存")]
    b = others[0]
    config.set("suite.autosave_on_switch", True)
    suites.switch(a["id"], save=True)
    config.set("prompts.desc", "DESC-UNSAVED-A")
    # 不调 save-current，直接切走
    suites.switch(b["id"])
    assert prompts.get_block("desc") != "DESC-UNSAVED-A" or a["id"] == b["id"]
    stored_a = _payload(a["id"], "prompts")
    assert stored_a["prompts.desc"] == "DESC-UNSAVED-A"
    suites.switch(a["id"])
    assert prompts.get_block("desc") == "DESC-UNSAVED-A"


def test_autosave_off_returns_dirty_flag():
    data = suites.list_all()
    a = next(x for x in data["items"] if x.get("builtin"))
    others = [x for x in data["items"] if x["id"] != a["id"]]
    if not others:
        others = [suites.create("套装B-脏检查")]
    b = others[0]
    config.set("suite.autosave_on_switch", True)
    suites.switch(a["id"], save=True)
    config.set("suite.autosave_on_switch", False)
    config.set("prompts.desc", "DESC-DIRTY-FLAG")
    with pytest.raises(OranteError) as ei:
        suites.switch(b["id"])
    err = ei.value
    assert err.code == "dirty"
    assert "改动" in err.message
    d = err.to_dict()
    assert d.get("needs_save") is True
    # 确认保存后再切
    r = suites.switch(b["id"], save=True)
    assert r["ok"]
    stored_a = _payload(a["id"], "prompts")
    assert stored_a["prompts.desc"] == "DESC-DIRTY-FLAG"
    config.set("suite.autosave_on_switch", True)


def test_switch_allowed_when_job_running():
    """生成进行中也可以切套装。同一账号的互斥不在切换里做。"""
    data = suites.list_all()
    a = data["current"]
    others = [x for x in data["items"] if x["id"] != a]
    if not others:
        others = [suites.create("套装B-有任务")]
    b = others[0]
    db.run(
        "INSERT INTO jobs(id,task_id,kind,status,started,log,owner_pid,owner_port,beat) VALUES(?,?,?,?,?,?,?,?,?)",
        ("J76run", "T76", "generate", "running", db.now(), "", os.getpid(), 0, time.time()))
    try:
        r = suites.switch(b["id"], save=True)
        assert r["ok"]
        assert r["current"] == b["id"]
    finally:
        db.run("DELETE FROM jobs WHERE id=?", ("J76run",))


def test_deleted_account_skipped_not_raised():
    a1 = accounts.create("suite-acc-keep", "flow")
    a2 = accounts.create("suite-acc-gone", "grok")
    sid = suites.current_id()
    suites.save_current(sid)
    stored = _payload(sid, "accounts")
    assert a1["id"] in stored["enabled"]
    assert a2["id"] in stored["enabled"]
    other = suites.create("套装-账号跳过")
    accounts.delete(a2["id"])
    r = suites.switch(sid)
    assert r["ok"]
    assert a2["id"] in r["skipped_accounts"]
    assert a1["id"] not in r["skipped_accounts"]
    # 切回来也要能过
    r2 = suites.switch(other["id"])
    assert r2["ok"]


def test_login_state_dir_unchanged_on_switch():
    acc = accounts.create("suite-login-keep", "flow")
    prof = accounts.profile_dir(acc)
    (prof / "Default").mkdir(parents=True, exist_ok=True)
    marker = prof / "Default" / "Cookies"
    marker.write_bytes(b"ORANTE-LOGIN-STATE-SPEC76" + os.urandom(32))
    extra = prof / "Local State"
    extra.write_text('{"os_crypt":{"encrypted_key":"keep-me"}}', encoding="utf-8")
    before = _dir_sha(prof)
    sid = suites.current_id()
    suites.save_current(sid)
    other = suites.create("套装-登录态")
    suites.switch(sid)
    suites.switch(other["id"])
    after = _dir_sha(prof)
    assert before == after
    assert marker.read_bytes().startswith(b"ORANTE-LOGIN-STATE-SPEC76")


def test_builtin_cannot_delete_restore_only_self():
    data = suites.list_all()
    builtin = next(x for x in data["items"] if x.get("builtin"))
    extra = suites.create("不要被恢复碰到")
    suites.switch(builtin["id"], save=True)
    config.set("prompts.desc", "BUILTIN-DIRTY")
    suites.save_current(builtin["id"])
    assert _payload(builtin["id"], "prompts")["prompts.desc"] == "BUILTIN-DIRTY"
    with pytest.raises(OranteError) as ei:
        suites.delete(builtin["id"])
    assert "不能删" in ei.value.message
    suites.restore_builtin(builtin["id"])
    restored = _payload(builtin["id"], "prompts")
    assert restored["prompts.desc"] != "BUILTIN-DIRTY"
    extra_p = _payload(extra["id"], "prompts")
    # 别的套装原样
    assert extra["id"] in {x["id"] for x in suites.list_all()["items"]}
    assert extra_p is not None


def test_export_readable_by_spec26_import():
    sid = suites.current_id()
    config.set("prompts.desc", "DESC-EXPORT-76")
    suites.save_current(sid)
    dest = TEST_DATA / "suite76.orpreset"
    r = suites.export_suite(sid, "pw-76-ok", str(dest))
    assert dest.is_file()
    assert r["bytes"] > 50
    raw = dest.read_bytes()
    assert raw.startswith(b"ORANTE-PRESET\n")
    config.set("prompts.desc", "DESC-SHOULD-BE-OVERWRITTEN")
    imported = preset.do_import(str(dest), "pw-76-ok", "merge_preset", ["prompts"])
    assert imported["applied"]["prompts"]["overwrite"] >= 1
    assert prompts.get_block("desc") == "DESC-EXPORT-76"
    # 导入成另一个套装
    item = suites.import_suite(str(dest), "pw-76-ok", "从预设来的套装")
    stored = _payload(item["id"], "prompts")
    assert stored["prompts.desc"] == "DESC-EXPORT-76"


def test_suites_py_uses_preset_only():
    src = (ROOT / "orante" / "services" / "suites.py").read_text(encoding="utf-8")
    assert "collect_suite_group" in src
    assert "apply_suite_group" in src
    assert "export_payload" in src
    assert "preset_payload_from_suite_groups" in src
    assert "suite_groups_from_preset_payload" in src
    assert "_prompt_rows" not in src
    assert "dump_rows" not in src
    assert "prompts.desc" not in src
    assert "json.dumps" not in src
    assert "json.loads" not in src
    # 不自己拼账号实体
    assert "INSERT INTO accounts" not in src


def test_api_routes_exist():
    text = (ROOT / "orante" / "server.py").read_text(encoding="utf-8")
    for path in (
        '/api/suites',
        '/api/suites/{sid}/switch',
        '/api/suites/{sid}/save-current',
        '/api/suites/{sid}/restore-builtin',
        '/api/suites/{sid}/export',
        '/api/suites/import',
        '/api/suites/{sid}/duplicate',
    ):
        assert path in text, path
    assert 'from_=d.get("from")' in text or "from_=d.get('from')" in text or 'd.get("from")' in text
    assert "@app.patch" in text
    assert "@app.delete" in text


def test_ui_has_panel_and_transition():
    html = (ROOT / "ui" / "index.html").read_text(encoding="utf-8")
    assert "suite-bar" in html
    assert "suites.js" in html
    css = (ROOT / "ui" / "css" / "app.css").read_text(encoding="utf-8")
    assert "clip-path" in css
    assert "prefers-reduced-motion" in css
    js = (ROOT / "ui" / "js" / "parts" / "suites.js").read_text(encoding="utf-8")
    assert "suite.confirm_autosave" in js
    assert "jobs_active" in js
    assert "prefers-reduced-motion" in js
    assert "suite.create_go" in js
    assert "suite.from.current" in js
    assert "suite.from.factory" in js
    assert "openCreate" in js
    assert "openSaveAs" in js
    assert "suite.save_restore" in js
    assert "suiteTick" in (ROOT / "ui" / "js" / "app.js").read_text(encoding="utf-8")


def test_switch_reloaded_lists_groups():
    data = suites.list_all()
    cur = data["current"]
    others = [x for x in data["items"] if x["id"] != cur]
    if not others:
        others = [suites.create("套装-重载清单")]
    r = suites.switch(others[0]["id"], save=True)
    assert r["ok"]
    for g in ("prompts", "coverage", "desc_cards", "gen", "accounts"):
        assert g in r["reloaded"], r["reloaded"]
